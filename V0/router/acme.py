"""Let's Encrypt (90-day) certificates via certbot; renew when within threshold days of expiry."""

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from router.acme_dns import VALID_CHALLENGES, challenge_label, dns01_configured, resolve_challenge_mode
from router.public_endpoint import is_ip_host

RENEW_DAYS_DEFAULT = 3


def short_acme_error(detail: str) -> str:
    text = (detail or '').replace('\n', ' ').strip()
    low = text.lower()
    if 'timeout' in low or 'firewall' in low:
        return '外网无法访问校验端口（HTTP 需 80，TLS-ALPN 需 443）'
    if 'dns' in low and ('failed' in low or 'incorrect' in low):
        return 'DNS-01 未通过，请检查 TXT 记录或 DNS API 配置'
    if 'some challenges have failed' in low or 'challenge failed' in low:
        return '证书校验未通过'
    if low.startswith('usage:') or 'usage: certbot' in low:
        return 'certbot 参数异常（已修复命令拼接，请重试保存）'
    if len(text) > 160:
        return text[:160] + '…'
    return text or 'certbot 执行失败'


def _certbot_path():
    path = os.environ.get('HOPWORLD_CERTBOT') or shutil.which('certbot')
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


def cert_paths(data_dir, domain):
    base = Path(data_dir) / 'certs' / domain
    return base / 'fullchain.pem', base / 'privkey.pem'


def letsencrypt_live(domain):
    return Path(f'/etc/letsencrypt/live/{domain}')


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
    data = src.read_bytes()
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def materialize_certs(data_dir, domain):
    """Prefer managed copies under HOPWORLD_DATA; fall back to Let's Encrypt live tree."""
    cert_out, key_out = cert_paths(data_dir, domain)
    live = letsencrypt_live(domain)
    if cert_out.is_file() and key_out.is_file():
        return cert_out, key_out
    if live.is_dir():
        try:
            _copy_tree_file(live / 'fullchain.pem', cert_out)
            _copy_tree_file(live / 'privkey.pem', key_out)
            return cert_out, key_out
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
        port = os.environ.get('HOPWORLD_ACME_TLS_PORT', '443').strip() or '443'
        try:
            port = str(int(port))
        except ValueError:
            port = '443'
        return ['--standalone', '--preferred-challenges', 'tls-alpn-01', '--https-port', port]
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


def _build_certbot_cmd(email, domain, challenge, data_dir, renew=False):
    mode = _effective_mode(challenge)
    cmd = [_certbot_path()]
    if renew:
        cmd.extend(['renew', '--non-interactive', '--cert-name', domain])
        if mode == 'dns':
            cmd.extend(_renew_dns_hook_args())
        return cmd
    cmd.extend([
        '--certonly',
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
        detail = (exc.stderr or exc.stdout or '').strip()[:500]
        raise ValueError(short_acme_error(detail)) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('certbot 执行失败') from exc


def issue_certificate(data_dir, domain, email, challenge='auto'):
    if not domain or is_ip_host(domain):
        raise ValueError('ACME 需要域名')
    email = validate_acme_email(email)
    validate_acme_challenge(challenge)
    _run_certbot(_build_certbot_cmd(email, domain, challenge, data_dir), data_dir)
    return materialize_certs(data_dir, domain)


def renew_certificate(data_dir, domain, email, challenge='auto'):
    if not domain or is_ip_host(domain):
        raise ValueError('ACME 需要域名')
    email = validate_acme_email(email)
    validate_acme_challenge(challenge)
    _run_certbot(_build_certbot_cmd(email, domain, challenge, data_dir, renew=True), data_dir)
    try:
        return materialize_certs(data_dir, domain)
    except ValueError:
        return issue_certificate(data_dir, domain, email, challenge)


def sync_certificate(data_dir, domain, email, enabled, challenge='auto'):
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
    try:
        cert_path, key_path = materialize_certs(data_dir, domain)
    except ValueError:
        cert_path, key_path = issue_certificate(data_dir, domain, email, stored_challenge)
        effective = resolve_challenge_mode(stored_challenge)

    days_left = days_until_expiry(cert_path)
    if days_left is not None and days_left <= threshold:
        cert_path, key_path = renew_certificate(data_dir, domain, email, stored_challenge)
        effective = resolve_challenge_mode(stored_challenge)
        days_left = days_until_expiry(cert_path)
    return {
        'enabled': True,
        'domain': domain,
        'days_left': round(days_left, 1) if days_left is not None else None,
        'cert_path': str(cert_path),
        'key_path': str(key_path),
        'error': '',
        'challenge': stored_challenge,
        'challenge_effective': challenge_label(effective),
    }
