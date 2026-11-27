"""Let's Encrypt (90-day) certificates via certbot; renew when within threshold days of expiry."""

import hashlib
import ipaddress
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from router.acme_dns import VALID_CHALLENGES, challenge_label, dns01_configured, resolve_challenge_mode
from router.public_endpoint import is_ip_host

RENEW_DAYS_DEFAULT = 3
ACME_KEY = 'acme'
IP_CERTIFICATES_KEY = 'ip_certificates'
SHORTLIVED_RENEW_SECONDS = 80 * 3600
_ACME_CLIENT_LOCK = threading.Lock()
HYSTERIA_CERTIFICATE_NAME = 'hysteria2-router.invalid'
MAX_CERTIFICATE_PEM = 128 * 1024
MAX_PRIVATE_KEY_PEM = 32 * 1024
CERTIFICATE_PEM = re.compile(
    rb'(?:-----BEGIN CERTIFICATE-----\r?\n(?:[A-Za-z0-9+/=]+\r?\n)+-----END CERTIFICATE-----\s*)+'
)


def short_acme_error(detail: str, *, ip_http=False) -> str:
    text = (detail or '').replace('\n', ' ').strip()
    low = text.lower()
    if ip_http:
        if 'could not bind' in low and 'port 80' in low:
            return 'Certbot 无法监听本机 TCP 80，请检查端口占用或使用 Webroot 校验'
        if any(reason in low for reason in ('timeout during connect', 'connection refused', 'error getting validation data')):
            return 'Let’s Encrypt 无法连接公网 IP 的 TCP 80。请检查出口地址、端口转发和防火墙'
        if 'invalid response' in low and '/.well-known/acme-challenge/' in low:
            return '公网 TCP 80 已响应，但 HTTP-01 校验文件内容不匹配，请检查 Webroot 或反向代理'
    timed_out = 'timeout' in low or 'timed out' in low
    if timed_out and 'api.letsencrypt.org' in low:
        return 'Certbot 访问 Let’s Encrypt 的 HTTPS 接口超时（出站 TCP 443）。请检查 Router 的出网连接、代理或 TUN'
    if 'timeout during connect' in low or 'likely firewall problem' in low or 'error getting validation data' in low:
        return '外网无法访问校验端口（HTTP 需 80，TLS-ALPN 需 443）'
    if 'dns' in low and ('failed' in low or 'incorrect' in low):
        return 'DNS-01 未通过，请检查 TXT 记录或 DNS API 配置'
    if 'some challenges have failed' in low or 'challenge failed' in low:
        return '证书校验未通过'
    if timed_out:
        return 'Certbot 请求超时，请检查出站网络和证书日志'
    if low.startswith('usage:') or 'usage: certbot' in low:
        return 'Certbot 不接受当前参数，请检查 Certbot 版本与证书日志'
    if len(text) > 160:
        return text[:160] + '…'
    return text or 'certbot 执行失败'


def _certbot_path():
    configured = os.environ.get('HOPWORLD_CERTBOT')
    path = shutil.which(configured) if configured else shutil.which('certbot', path=os.pathsep.join(
        (os.environ.get('PATH', os.defpath), '/opt/homebrew/bin', '/usr/local/bin', '/snap/bin')
    ))
    if not path:
        raise ValueError('未找到 certbot，请安装后设置 HOPWORLD_CERTBOT')
    return path


def _renew_days():
    try:
        days = int(os.environ.get('HOPWORLD_ACME_RENEW_DAYS', str(RENEW_DAYS_DEFAULT)))
    except ValueError:
        days = RENEW_DAYS_DEFAULT
    return max(1, min(days, 30))


def validate_acme_email(email):
    if not isinstance(email, str) or not email.strip():
        raise ValueError('ACME 邮箱无效')
    email = email.strip()
    if len(email) > 254 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        raise ValueError('ACME 邮箱无效')
    return email


def validate_acme_challenge(value):
    mode = (value or 'auto').strip().lower()
    if mode not in VALID_CHALLENGES:
        raise ValueError('ACME 校验方式无效')
    if mode == 'dns' and not dns01_configured():
        raise ValueError('DNS-01 需要配置 DNSPod 或 Webhook')
    return mode


def cert_paths(data_dir, domain, *, manual=False):
    base = Path(data_dir) / 'certs' / domain
    if manual:
        base /= 'manual'
    return base / 'fullchain.pem', base / 'privkey.pem'


def letsencrypt_live(domain):
    return Path(f'/etc/letsencrypt/live/{domain}')


PUBLIC_ADMIN_CERT_PREFIX = 'hopworld-public-admin-'


def management_https_host():
    return os.environ.get('HOPWORLD_MANAGEMENT_HTTPS_HOST', '').strip().lower().rstrip('.')


def ensure_management_https_certificate(data_dir, host=None, *, validate=True):
    """Reuse the public-admin Nginx certificate for Router HTTPS listeners.

    Install writes lineage hopworld-public-admin-<host>; hopworld-router usually
    cannot read /etc/letsencrypt, so prefer the data-dir cache seeded by root.
    """
    host = (host or management_https_host()).strip().lower().rstrip('.')
    if not host:
        raise ValueError('未配置公网 HTTPS 管理地址')
    data_dir = Path(data_dir).resolve()
    cert_out, key_out = cert_paths(data_dir, host)
    alt = cert_paths(data_dir, PUBLIC_ADMIN_CERT_PREFIX + host)
    for live in (letsencrypt_live(PUBLIC_ADMIN_CERT_PREFIX + host), letsencrypt_live(host)):
        try:
            if live.is_dir():
                _sync_live_certificate(live, cert_out, key_out)
                break
        except (OSError, ValueError, PermissionError):
            continue
    if (not cert_out.is_file() or not key_out.is_file()) and all(path.is_file() for path in alt):
        try:
            _sync_live_certificate(alt[0].parent, cert_out, key_out)
        except (OSError, ValueError, PermissionError):
            pass
    if not cert_out.is_file() or not key_out.is_file():
        raise ValueError('公网管理 HTTPS 证书尚未同步到 Router')
    expires = days_until_expiry(cert_out)
    if expires is None or expires <= 0:
        raise ValueError('公网管理 HTTPS 证书已过期')
    if validate:
        # leaf=None: require a publicly trusted chain covering the management host.
        _validate_imported_certificate(cert_out, key_out, None, host)
    return host, cert_out, key_out


def days_until_expiry(cert_path):
    if not Path(cert_path).is_file():
        return None
    try:
        completed = subprocess.run(
            ['openssl', 'x509', '-in', str(cert_path), '-noout', '-enddate'],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = completed.stdout.strip()
    if not line.startswith('notAfter='):
        return None
    raw = line.split('=', 1)[1].strip()
    try:
        expires = datetime.strptime(raw, '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    delta = expires - datetime.now(timezone.utc)
    return delta.total_seconds() / 86400


def _copy_tree_file(src: Path, dest: Path):
    if not src.is_file():
        raise ValueError('证书文件不存在')
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(src.read_bytes())


def validate_key_pair(cert, key):
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key, password=lambda: '')
    except ssl.SSLError as exc:
        raise ValueError('证书与私钥不匹配或格式无效') from exc


def _replace_certificate_pair(cert, key, cert_out, key_out):
    previous_key = key.parent / 'previous-key.pem'
    if key_out.is_file():
        _copy_tree_file(key_out, previous_key)
    os.replace(key, key_out)
    try:
        os.replace(cert, cert_out)
    except OSError:
        if previous_key.is_file():
            os.replace(previous_key, key_out)
        else:
            key_out.unlink(missing_ok=True)
        raise


def hysteria_local_certificate(data_dir):
    """Keep a stable TLS identity for Hysteria 2 links that pin the leaf certificate."""
    cert, key = cert_paths(Path(data_dir).resolve(), HYSTERIA_CERTIFICATE_NAME)
    try:
        if not cert.exists():
            cert.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(dir=cert.parent) as directory:
                temporary = Path(directory)
                config = temporary / 'openssl.cnf'
                config.write_text(
                    '[req]\ndistinguished_name=dn\nx509_extensions=server\n'
                    f'[dn]\nCN={HYSTERIA_CERTIFICATE_NAME}\n'
                    '[server]\nbasicConstraints=critical,CA:FALSE\n'
                    'extendedKeyUsage=serverAuth\n'
                    f'subjectAltName=DNS:{HYSTERIA_CERTIFICATE_NAME}\n',
                    encoding='ascii',
                )
                new_cert, new_key = temporary / cert.name, temporary / key.name
                subprocess.run(
                    ['openssl', 'req', '-new', '-x509', '-newkey', 'ec', '-pkeyopt',
                     'ec_paramgen_curve:prime256v1', '-nodes', '-days', '3650', '-batch',
                     '-subj', f'/CN={HYSTERIA_CERTIFICATE_NAME}', '-config', str(config),
                     '-out', str(new_cert), '-keyout', str(new_key)],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                )
                new_cert.chmod(0o600)
                new_key.chmod(0o600)
                validate_key_pair(new_cert, new_key)
                _replace_certificate_pair(new_cert, new_key, cert, key)
        validate_key_pair(cert, key)
        leaf = ssl.PEM_cert_to_DER_cert(cert.read_text(encoding='ascii'))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ValueError('无法生成或读取 Hysteria 2 本地证书，请检查 OpenSSL 和证书目录权限') from exc
    return (str(cert), str(key)), hashlib.sha256(leaf).hexdigest()


def _validate_imported_certificate(cert, key, leaf, domain):
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        server.load_cert_chain(cert, key, password=lambda: '')
    except ssl.SSLError as exc:
        raise ValueError('证书与私钥不匹配，或私钥格式无效、已加密') from exc
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.hostname_checks_common_name = False
    if leaf:
        # Manual imports may use a private CA. Still check identity, dates and key usage.
        client.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        client.load_verify_locations(cadata=leaf)
    else:
        client.load_default_certs()
    client_in, client_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    peers = (
        (client.wrap_bio(client_in, client_out, server_hostname=domain), client_out, server_in),
        (server.wrap_bio(server_in, server_out, server_side=True), server_out, client_in),
    )
    pending = {peer[0] for peer in peers}
    try:
        for _ in range(12):
            for connection, outgoing, incoming in peers:
                if connection not in pending:
                    continue
                try:
                    connection.do_handshake()
                    pending.remove(connection)
                except ssl.SSLWantReadError:
                    pass
                if outgoing.pending:
                    incoming.write(outgoing.read())
            if not pending:
                return
    except ssl.SSLCertVerificationError as exc:
        if leaf is None:
            raise ValueError('证书必须覆盖所选公网地址，且由受信任的 CA 签发') from exc
        raise ValueError('证书必须覆盖所选域名，处于有效期内，并允许用于服务端 TLS') from exc
    except ssl.SSLError as exc:
        raise ValueError('证书无法用于 TLS') from exc
    raise ValueError('证书 TLS 校验未完成')


def import_certificate(data_dir, domain, chain, private_key):
    if not isinstance(chain, str) or not isinstance(private_key, str):
        raise ValueError('请选择 PEM 格式的证书链和私钥')
    chain, private_key = chain.strip(), private_key.strip()
    if (not chain.isascii() or not private_key.isascii() or
            len(chain) > MAX_CERTIFICATE_PEM or len(private_key) > MAX_PRIVATE_KEY_PEM):
        raise ValueError('证书或私钥超出大小限制，或文件格式无效')
    chain_bytes = chain.encode() + b'\n'
    if not CERTIFICATE_PEM.fullmatch(chain_bytes):
        raise ValueError('证书链应为 PEM 格式，并以域名证书开头')
    end = '-----END CERTIFICATE-----'
    leaf = chain.split(end, 1)[0] + end + '\n'
    cert_out, key_out = cert_paths(data_dir, domain, manual=True)
    cert_out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=cert_out.parent, prefix='.cert-') as directory:
        cert, key = Path(directory) / cert_out.name, Path(directory) / key_out.name
        for target, content in ((cert, chain_bytes), (key, private_key.encode() + b'\n')):
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(content)
        _validate_imported_certificate(cert, key, leaf, domain)
        expires = days_until_expiry(cert)
        if expires is None or expires <= 0:
            raise ValueError('无法读取证书有效期，或证书已过期')
        _replace_certificate_pair(cert, key, cert_out, key_out)
    return {
        'enabled': True, 'domain': domain, 'bound_domain': domain, 'source': 'manual',
        'cert_path': str(cert_out), 'key_path': str(key_out), 'days_left': round(expires, 1),
        'certificate_revision': hashlib.sha256(chain_bytes).hexdigest(),
        'error': '', 'challenge_effective': '',
    }


def _sync_live_certificate(live, cert_out, key_out, *, validate=None, source_names=None):
    cert_out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=cert_out.parent, prefix='.cert-') as directory:
        staged = Path(directory)
        cert, key = staged / cert_out.name, staged / key_out.name
        source_cert, source_key = source_names or (cert.name, key.name)
        _copy_tree_file(live / source_cert, cert)
        _copy_tree_file(live / source_key, key)
        same_cert = cert_out.is_file() and cert.read_bytes() == cert_out.read_bytes()
        if same_cert and key_out.is_file() and key.read_bytes() == key_out.read_bytes():
            return
        expires = days_until_expiry(cert)
        if expires is None or expires <= 0:
            raise ValueError('Certbot 证书无法读取或已过期')
        if not same_cert and (days_until_expiry(cert_out) or 0) >= expires:
            return
        if validate:
            validate(cert, key)
        else:
            validate_key_pair(cert, key)
        _replace_certificate_pair(cert, key, cert_out, key_out)


def materialize_certs(data_dir, domain):
    """Synchronize a newer readable Certbot lineage into Router's protected cache."""
    cert_out, key_out = cert_paths(data_dir, domain)
    live = letsencrypt_live(domain)
    try:
        if live.is_dir():
            _sync_live_certificate(live, cert_out, key_out)
    except (OSError, ValueError):
        pass
    if cert_out.is_file() and key_out.is_file():
        return cert_out, key_out
    raise ValueError('证书尚未签发或无法读取')


def _dns_hook_script():
    custom = os.environ.get('HOPWORLD_ACME_DNS_HOOK', '').strip()
    if custom:
        return custom
    root = Path(__file__).resolve().parent.parent
    script = root / 'deploy' / 'hopworld-acme-dns-hook.sh'
    return str(script)


def _effective_mode(challenge):
    mode = validate_acme_challenge(challenge)
    if mode == 'auto':
        return resolve_challenge_mode('auto')
    return mode


def _certbot_challenge_args(mode, data_dir):
    if mode == 'dns':
        hook = _dns_hook_script()
        if not Path(hook).is_file():
            raise ValueError('未找到 DNS-01 钩子脚本 hopworld-acme-dns-hook.sh')
        return [
            '--manual',
            '--preferred-challenges',
            'dns',
            '--manual-auth-hook',
            f'{hook} auth',
            '--manual-cleanup-hook',
            f'{hook} cleanup',
        ]
    if mode == 'tls-alpn':
        raise ValueError('TLS-ALPN-01 由 lego 处理')
    webroot = os.environ.get('HOPWORLD_ACME_WEBROOT', '').strip()
    if webroot:
        return ['--webroot', '-w', webroot]
    raise ValueError('HTTP-01 需配置 HOPWORLD_ACME_WEBROOT（Nginx 已占用 80 时勿用 standalone）')


def _renew_dns_hook_args():
    hook = _dns_hook_script()
    if not Path(hook).is_file():
        raise ValueError('未找到 DNS-01 钩子脚本')
    return [
        '--manual',
        '--preferred-challenges',
        'dns',
        '--manual-auth-hook',
        f'{hook} auth',
        '--manual-cleanup-hook',
        f'{hook} cleanup',
    ]


def _build_certbot_cmd(email, domain, challenge, data_dir, renew=False, force=False):
    mode = _effective_mode(challenge)
    cmd = [_certbot_path()]
    if renew:
        cmd.extend(['renew', '--non-interactive', '--cert-name', domain])
        if force:
            cmd.append('--force-renewal')
        if mode == 'dns':
            cmd.extend(_renew_dns_hook_args())
        return cmd
    cmd.extend([
        'certonly',
        '--non-interactive',
        '--agree-tos',
        '--keep-until-expiring',
        '-m',
        email,
        '-d',
        domain,
    ])
    cmd.extend(_certbot_challenge_args(mode, data_dir))
    return cmd


def _run_certbot(cmd, data_dir):
    wrapper = os.environ.get('HOPWORLD_ACME_WRAPPER', '').strip()
    if wrapper and cmd:
        cmd = wrapper.split() + cmd[1:]
    env = {**os.environ, 'HOPWORLD_DATA': str(data_dir)}
    try:
        with _ACME_CLIENT_LOCK:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=600,
                env=env,
            )
    except subprocess.CalledProcessError as exc:
        detail = '\n'.join((exc.stderr or '', exc.stdout or ''))
        raise ValueError(short_acme_error(detail, ip_http='--ip-address' in cmd)) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Certbot 执行超时，请检查出站网络和证书日志') from exc
    except OSError as exc:
        raise ValueError('certbot 执行失败') from exc


def _ip_lineage(address):
    ip = ipaddress.ip_address(address)
    if not ip.is_global or ip.is_multicast:
        raise ValueError('证书申请需要公网 IP')
    return 'hopworld-ip-' + ip.packed.hex()


def ip_certificate_paths(data_dir, address):
    return tuple(str(path) for path in cert_paths(Path(data_dir).resolve(), _ip_lineage(address)))


def validate_ip_certificate(cert, key, address):
    _validate_imported_certificate(cert, key, None, address)


def _ip_certbot_runtime(webroot):
    binary = _certbot_path()
    try:
        output = subprocess.check_output([binary, '--version'], stderr=subprocess.STDOUT, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('无法读取 Certbot 版本') from exc
    match = re.search(r'certbot\s+(\d+)\.(\d+)', output)
    minimum = 4 if webroot else 3
    if not match or tuple(map(int, match.groups())) < (5, minimum):
        raise ValueError(f'申请公网 IP 证书需要 Certbot 5.{minimum} 或更新版本')
    return binary, '.'.join(match.groups())


def ip_certificate_readiness(version):
    webroot = os.environ.get('HOPWORLD_ACME_WEBROOT', '').strip()
    wrapper = os.environ.get('HOPWORLD_ACME_WRAPPER', '').strip()
    try:
        _, certbot = _ip_certbot_runtime(webroot)
        certbot_status = f'Certbot {certbot} 已就绪'
    except ValueError as exc:
        certbot_status = str(exc)
    if webroot:
        port_status = ('已配置 Webroot，请确保该 IP 的 TCP 80 提供 ACME 校验路径'
                       if wrapper or os.path.isdir(webroot) and os.access(webroot, os.W_OK)
                       else '配置的 Webroot 不存在或不可写')
    elif wrapper:
        port_status = 'Certbot 将通过已配置的授权命令监听 TCP 80'
    else:
        try:
            with socket.socket(socket.AF_INET6 if version == 6 else socket.AF_INET, socket.SOCK_STREAM) as test:
                test.bind(('::' if version == 6 else '0.0.0.0', 80))
            port_status = '本机可绑定 TCP 80'
        except PermissionError:
            port_status = 'Router 无权监听 TCP 80，请配置 Webroot 或 Certbot 授权'
        except OSError:
            port_status = '本机 TCP 80 无法绑定，请检查端口占用或配置 Webroot'
    return {'certbot_status': certbot_status, 'port_status': port_status}


def _ip_certbot_command(email, address, data_dir, *, renew):
    name = _ip_lineage(address)
    webroot = os.environ.get('HOPWORLD_ACME_WEBROOT', '').strip()
    binary, _ = _ip_certbot_runtime(webroot)
    command = [binary]
    command.extend(['certonly', '--non-interactive', '--agree-tos', '--email', email,
                    '--server', 'https://acme-v02.api.letsencrypt.org/directory',
                    '--required-profile', 'shortlived', '--cert-name', name, '--ip-address', address,
                    '--preferred-challenges', 'http',
                    '--force-renewal' if renew else '--keep-until-expiring'])
    if webroot:
        command.extend(['--webroot', '--webroot-path', webroot])
    else:
        bind = '::' if ipaddress.ip_address(address).version == 6 else '0.0.0.0'
        command.extend(['--standalone', '--http-01-address', bind])
    if not os.environ.get('HOPWORLD_ACME_WRAPPER', '').strip():
        root = Path(data_dir).resolve() / 'ip-acme'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for argument, child in (('--config-dir', 'config'), ('--work-dir', 'work'), ('--logs-dir', 'logs')):
            command.extend([argument, str(root / child)])
    return command


def _lego_runtime():
    binary = shutil.which(os.environ.get('HOPWORLD_ACME_LEGO') or 'lego')
    if not binary:
        raise ValueError('TLS-ALPN-01 需要 lego 5，请安装并设置 HOPWORLD_ACME_LEGO')
    try:
        version = subprocess.check_output([binary, '--version'], text=True, timeout=5, stderr=subprocess.STDOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('无法读取 lego 版本') from exc
    if not re.search(r'\bv?5\.\d+\.\d+\b', version):
        raise ValueError('TLS-ALPN-01 需要 lego 5')
    return binary


def _lego_alpn(data_dir, address, email, *, staging=False, force=False):
    binary = _lego_runtime()
    try:
        port = int(os.environ.get('HOPWORLD_ACME_TLS_PORT', '443'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError('TLS-ALPN-01 本机监听端口无效') from None
    root = Path(data_dir).resolve() / ('lego-staging' if staging else 'lego')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    ip = is_ip_host(address)
    name = _ip_lineage(address) if ip else address
    bind = ('0.0.0.0' if ipaddress.ip_address(address).version == 4 else '[::]') if ip else ''
    command = [binary, 'run', '--accept-tos', '--email', email, '--domains', address, '--tls',
               '--tls.address', f'{bind}:{port}', '--path', str(root), '--cert.name', name,
               '--server', 'letsencrypt-staging' if staging else 'letsencrypt']
    if ip:
        command.extend(['--profile', 'shortlived'])
    if staging:
        command.append('--always-deactivate-authorizations')
    if force:
        command.append('--renew-force')
    try:
        with _ACME_CLIENT_LOCK:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        detail = '\n'.join((exc.stderr or '', exc.stdout or '')).lower()
        if 'address already in use' in detail or 'permission denied' in detail:
            raise ValueError(f'lego 无法监听本机 TCP {port}，请检查占用和权限。公网 TCP 443 需转发到验证入口') from exc
        if 'timeout during connect' in detail or 'connection refused' in detail:
            raise ValueError('Let’s Encrypt 无法连到公网 TCP 443，请检查入站防火墙与 TLS-ALPN 转发') from exc
        raise ValueError('TLS-ALPN-01 校验失败，请确认公网 TCP 443 能将 acme-tls/1 握手传给 lego') from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('lego 执行失败或超时') from exc
    if staging:
        return None
    cert, key = map(Path, ip_certificate_paths(data_dir, address)) if ip else cert_paths(data_dir, address)
    _sync_live_certificate(root / 'certificates', cert, key, source_names=(name + '.crt', name + '.key'),
                           validate=lambda chain, private_key: _validate_imported_certificate(chain, private_key, None, address))
    return cert, key


def validate_ip_challenge(challenge):
    if challenge not in ('http', 'tls-alpn'):
        raise ValueError('公网 IP 证书支持 HTTP-01 或 TLS-ALPN-01')
    return challenge


def stage_certificate(data_dir, address, email, challenge):
    email = validate_acme_email(email)
    ip = is_ip_host(address)
    mode = validate_ip_challenge(challenge) if ip else _effective_mode(challenge)
    if mode == 'tls-alpn':
        _lego_alpn(data_dir, address, email, staging=True, force=True)
    else:
        command = (_ip_certbot_command(email, address, data_dir, renew=False) if ip
                   else _build_certbot_cmd(email, address, challenge, data_dir))
        if ip:
            server = command.index('--server')
            del command[server:server + 2]
        command.remove('--keep-until-expiring')
        _run_certbot([*command, '--dry-run', '--force-renewal'], data_dir)
    return mode


def ensure_ip_certificate(data_dir, address, email, *, renew=False, challenge='http'):
    """Obtain or renew a publicly trusted IP certificate while retaining the last good pair."""
    email = validate_acme_email(email)
    challenge = validate_ip_challenge(challenge)
    cert, key = map(Path, ip_certificate_paths(data_dir, address))
    name = cert.parent.name
    wrapped = os.environ.get('HOPWORLD_ACME_WRAPPER', '').strip()
    live = (letsencrypt_live(name) if wrapped else
            Path(data_dir).resolve() / 'ip-acme' / 'config' / 'live' / name)

    def validate(chain, private_key):
        validate_ip_certificate(chain, private_key, address)

    def cached_expiry():
        try:
            if live.is_dir():
                _sync_live_certificate(live, cert, key, validate=validate)
        except (OSError, ValueError):
            pass
        days = days_until_expiry(cert)
        if days and days > 0:
            validate(cert, key)
            return int(time.time() + days * 86400)
        return 0

    try:
        expiry = cached_expiry()
        if expiry and (not renew or expiry - time.time() > SHORTLIVED_RENEW_SECONDS):
            return expiry
        if challenge == 'tls-alpn':
            _lego_alpn(data_dir, address, email, force=bool(expiry))
        else:
            command = _ip_certbot_command(email, address, data_dir, renew=bool(expiry))
            _run_certbot(command, data_dir)
        updated = cached_expiry()
    except OSError as exc:
        raise ValueError('无法读取 IP 证书，请检查 Certbot 证书目录和权限') from exc
    if updated <= expiry:
        raise ValueError('Certbot 未同步有效的 IP 证书，请检查证书路径和续期钩子')
    return updated


def issue_certificate(data_dir, domain, email, challenge='auto'):
    if not domain or is_ip_host(domain):
        raise ValueError('ACME 需要域名')
    email = validate_acme_email(email)
    validate_acme_challenge(challenge)
    if _effective_mode(challenge) == 'tls-alpn':
        return _lego_alpn(data_dir, domain, email)
    _run_certbot(_build_certbot_cmd(email, domain, challenge, data_dir), data_dir)
    return materialize_certs(data_dir, domain)


def renew_certificate(data_dir, domain, email, challenge='auto', force=False):
    if not domain or is_ip_host(domain):
        raise ValueError('ACME 需要域名')
    email = validate_acme_email(email)
    validate_acme_challenge(challenge)
    if _effective_mode(challenge) == 'tls-alpn':
        return _lego_alpn(data_dir, domain, email, force=force)
    if force:
        cert_path, _ = materialize_certs(data_dir, domain)
        previous = cert_path.read_bytes()
    _run_certbot(_build_certbot_cmd(email, domain, challenge, data_dir, renew=True, force=force), data_dir)
    if force:
        cert_path, key_path = materialize_certs(data_dir, domain)
        if cert_path.read_bytes() == previous:
            raise ValueError('续签后尚未同步到新证书，请检查 Certbot 部署钩子')
        validate_key_pair(cert_path, key_path)
        return cert_path, key_path
    try:
        return materialize_certs(data_dir, domain)
    except ValueError:
        return issue_certificate(data_dir, domain, email, challenge)


def sync_certificate(data_dir, domain, email, enabled, challenge='auto', force=False):
    if not enabled:
        return {
            'enabled': False,
            'domain': domain or '',
            'days_left': None,
            'cert_path': '',
            'key_path': '',
            'error': '',
            'challenge': 'auto',
            'challenge_effective': '',
        }
    if not domain or is_ip_host(domain):
        raise ValueError('ACME 需要填写域名')
    email = validate_acme_email(email)
    stored_challenge = validate_acme_challenge(challenge)
    threshold = _renew_days()
    effective = resolve_challenge_mode(stored_challenge)
    if force:
        cert_path, key_path = renew_certificate(data_dir, domain, email, stored_challenge, force=True)
    else:
        try:
            cert_path, key_path = materialize_certs(data_dir, domain)
        except ValueError:
            cert_path, key_path = issue_certificate(data_dir, domain, email, stored_challenge)
            effective = resolve_challenge_mode(stored_challenge)

    days_left = days_until_expiry(cert_path)
    if not force and days_left is not None and days_left <= threshold:
        cert_path, key_path = renew_certificate(data_dir, domain, email, stored_challenge, force=True)
        effective = resolve_challenge_mode(stored_challenge)
        days_left = days_until_expiry(cert_path)
    return {
        'enabled': True,
        'domain': domain,
        'days_left': round(days_left, 1) if days_left is not None else None,
        'cert_path': str(cert_path),
        'key_path': str(key_path),
        'certificate_revision': hashlib.sha256(cert_path.read_bytes()).hexdigest(),
        'error': '',
        'challenge': stored_challenge,
        'challenge_effective': challenge_label(effective),
    }
