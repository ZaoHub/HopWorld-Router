"""Test a supported node with an isolated proxy core and a real HTTP request."""
import base64
import binascii
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import uuid
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from router.local_node import _link_host, validate_public_host

TEST_URL = 'https://www.google.com/generate_204'
TARGETS = {
    'google': ('Google', None),
    'youtube': ('YouTube', 'https://www.youtube.com/generate_204'),
    'cloudflare': ('Cloudflare', 'http://cp.cloudflare.com/generate_204'),
    'apple': ('Apple', 'http://captive.apple.com/hotspot-detect.html'),
    'microsoft': ('Microsoft', 'http://www.msftconnecttest.com/connecttest.txt'),
    'github': ('GitHub', 'https://github.com/'),
    'wikipedia': ('Wikipedia', 'https://www.wikipedia.org/'),
    'bing': ('Bing', 'https://www.bing.com/'),
    'baidu': ('百度', 'https://www.baidu.com/'),
    'tiktok': ('TikTok', 'https://www.tiktok.com/'),
    'twitter': ('X', 'https://x.com/'),
    'facebook': ('Facebook', 'https://www.facebook.com/'),
    'instagram': ('Instagram', 'https://www.instagram.com/'),
    'telegram': ('Telegram', 'https://web.telegram.org/'),
    'whatsapp': ('WhatsApp', 'https://www.whatsapp.com/'),
    'reddit': ('Reddit', 'https://www.reddit.com/'),
    'discord': ('Discord', 'https://discord.com/'),
    'linkedin': ('LinkedIn', 'https://www.linkedin.com/'),
    'pinterest': ('Pinterest', 'https://www.pinterest.com/'),
    'openai': ('ChatGPT', 'https://chatgpt.com/'),
    'claude': ('Claude', 'https://claude.ai/'),
    'gemini': ('Gemini', 'https://gemini.google.com/'),
    'copilot': ('Copilot', 'https://copilot.microsoft.com/'),
    'perplexity': ('Perplexity', 'https://www.perplexity.ai/'),
    'huggingface': ('Hugging Face', 'https://huggingface.co/'),
    'grok': ('Grok', 'https://grok.com/'),
}
STRICT_204_TARGETS = frozenset({'google', 'youtube', 'cloudflare'})
HYSTERIA_SCHEMES = frozenset({'hysteria2', 'hy2'})
QUIC_SCHEMES = HYSTERIA_SCHEMES | {'tuic'}
LIGHT_XRAY_SCHEMES = frozenset({'vless', 'trojan'})
LIGHT_CLIENT_SCHEMES = HYSTERIA_SCHEMES | LIGHT_XRAY_SCHEMES | {'http', 'https', 'socks', 'socks5'}


class UnsupportedNode(ValueError):
    pass


def outbound(link):
    parts = urlsplit(link)
    if parts.scheme in ('ss', 'vmess'):
        return encoded_outbound(parts)
    if parts.scheme in ('http', 'https', 'socks', 'socks5'):
        if not parts.hostname or not parts.port or parts.path not in ('', '/') or parts.query:
            raise UnsupportedNode('节点参数暂不支持')
        item = {'address':parts.hostname, 'port':parts.port}
        if parts.username is not None:
            item['users'] = [{'user':unquote(parts.username), 'pass':unquote(parts.password or '')}]
        stream = {'network':'tcp', 'security':'tls' if parts.scheme == 'https' else 'none'}
        if parts.scheme == 'https': stream['tlsSettings'] = {'serverName':parts.hostname, 'alpn':['http/1.1']}
        return {'protocol':'socks' if parts.scheme in ('socks','socks5') else 'http', 'settings':{'servers':[item]}, 'streamSettings':stream}
    if parts.scheme not in ('vless', 'trojan') or not parts.hostname or not parts.port or not parts.username or parts.password:
        raise UnsupportedNode('暂不支持此节点类型')
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if len(dict(pairs)) != len(pairs): raise UnsupportedNode('存在重复参数')
    query = dict(pairs)
    # These share-link flags do not change the server. insecure=0 is the default.
    flags = {key: query.pop(key) for key in ('insecure', 'allowInsecure', 'pcs', 'quicSecurity', 'fragment', 'peer') if key in query}
    if flags.get('insecure', '0') not in ('', '0') or flags.get('allowInsecure', '0') not in ('', '0'):
        raise UnsupportedNode('跳过证书校验的节点暂不测试')
    if flags.get('pcs', '') != '' or ('quicSecurity' in flags and flags['quicSecurity'] not in ('', 'none')):
        raise UnsupportedNode('存在暂不支持的参数')
    if 'peer' in flags and flags['peer'] not in ('', query.get('sni', ''), query.get('host', '')):
        raise UnsupportedNode('存在暂不支持的参数')
    allowed = {'type','security','encryption','sni','fp','alpn','flow','path','host','serviceName','pbk','sid','spx','headerType','mode'}
    if set(query) - allowed: raise UnsupportedNode('存在暂不支持的参数')
    network = query.get('type', 'tcp')
    security = query.get('security', 'tls' if parts.scheme == 'trojan' else 'none')
    if network not in ('tcp','ws','grpc','xhttp') or security not in ('none','tls','reality'):
        raise UnsupportedNode('传输方式暂不支持')
    if parts.scheme == 'trojan' and security != 'tls': raise UnsupportedNode('Trojan 安全方式暂不支持')
    if parts.scheme == 'vless' and query.get('encryption','none') != 'none': raise UnsupportedNode('加密方式暂不支持')
    if query.get('headerType', 'none') not in ('', 'none'): raise UnsupportedNode('TCP 请求头类型暂不支持')
    mode = query.get('mode', '')
    if mode and not ((network == 'grpc' and mode == 'gun') or (network == 'xhttp' and mode in ('auto', 'packet-up', 'stream-up', 'stream-one'))):
        raise UnsupportedNode('传输模式暂不支持')
    stream = {'network': network, 'security': security}
    if network == 'ws': stream['wsSettings'] = {'path': query.get('path','/'), 'host': query.get('host') or query.get('sni') or parts.hostname}
    if network == 'grpc': stream['grpcSettings'] = {'serviceName': query.get('serviceName','')}
    if network == 'xhttp': stream['xhttpSettings'] = {'path': query.get('path','/'), 'host': query.get('host') or query.get('sni') or parts.hostname}
    if network == 'xhttp' and mode: stream['xhttpSettings']['mode'] = mode
    if security in ('tls','reality'):
        tls = {}
        for source, target in [('sni','serverName'),('fp','fingerprint')]:
            if query.get(source): tls[target] = query[source]
        if query.get('alpn'): tls['alpn'] = query['alpn'].split(',')
        if security == 'reality':
            if not query.get('pbk'): raise UnsupportedNode('Reality 缺少公钥')
            for source, target in [('pbk','publicKey'),('sid','shortId'),('spx','spiderX')]:
                if query.get(source): tls[target] = query[source]
        stream['tlsSettings' if security == 'tls' else 'realitySettings'] = tls
    credential = unquote(parts.username)
    if parts.scheme == 'vless':
        try: uuid.UUID(credential)
        except ValueError: raise UnsupportedNode('节点标识无效') from None
        user = {'id':credential, 'encryption':'none'}
        if query.get('flow'): user['flow'] = query['flow']
        settings = {'vnext':[{'address':parts.hostname,'port':parts.port,'users':[user]}]}
    else:
        if query.get('flow'): raise UnsupportedNode('暂不支持该参数')
        settings = {'servers':[{'address':parts.hostname,'port':parts.port,'password':credential}]}
    return {'protocol':parts.scheme, 'settings':settings, 'streamSettings':stream}


def _decode(value):
    try:
        value = value.encode('ascii')
        return base64.b64decode(value + b'=' * (-len(value) % 4), altchars=b'-_', validate=True).decode('utf-8')
    except (ValueError, UnicodeError, binascii.Error):
        raise UnsupportedNode('编码无效') from None


def encoded_outbound(parts):
    if parts.scheme == 'ss':
        if parts.query or not parts.hostname or not parts.port or not parts.username or parts.password:
            raise UnsupportedNode('SS 选项暂不支持')
        credential = _decode(unquote(parts.username))
        if ':' not in credential: raise UnsupportedNode('SS 凭据无效')
        method, password = credential.split(':', 1)
        supported = {'aes-128-gcm','aes-256-gcm','chacha20-ietf-poly1305','chacha20-poly1305','xchacha20-ietf-poly1305','xchacha20-poly1305','2022-blake3-aes-128-gcm','2022-blake3-aes-256-gcm','2022-blake3-chacha20-poly1305'}
        if method not in supported or not password: raise UnsupportedNode('SS 加密暂不支持')
        return {'protocol':'shadowsocks','settings':{'servers':[{'address':parts.hostname,'port':parts.port,'method':method,'password':password}]}}
    if parts.query or parts.fragment: raise UnsupportedNode('VMess 格式无效')
    try:
        value = json.loads(_decode(parts.netloc + parts.path))
        allowed = {'v','ps','add','port','id','aid','scy','net','type','host','path','tls','sni','alpn'}
        if not isinstance(value, dict) or set(value) - allowed: raise UnsupportedNode('VMess 参数暂不支持')
        network = value.get('net','tcp') or 'tcp'
        if network not in ('tcp','ws','grpc','xhttp') or value.get('type','none') not in ('none','') or value.get('tls','') not in ('','tls'):
            raise UnsupportedNode('VMess 传输暂不支持')
        identifier = str(uuid.UUID(str(value['id'])))
        server = str(value['add']); port = int(value['port']); aid = int(value.get('aid',0))
        if not server or not 1 <= port <= 65535: raise UnsupportedNode('VMess 服务器无效')
        stream = {'network':network,'security':'tls' if value.get('tls') == 'tls' else 'none'}
        host = value.get('host') or value.get('sni') or server
        if network == 'ws': stream['wsSettings'] = {'path':value.get('path') or '/', 'host':host}
        if network == 'grpc': stream['grpcSettings'] = {'serviceName':value.get('path') or ''}
        if network == 'xhttp': stream['xhttpSettings'] = {'path':value.get('path') or '/', 'host':host}
        if stream['security'] == 'tls':
            tls = {}
            if value.get('sni'): tls['serverName'] = value['sni']
            if value.get('alpn'): tls['alpn'] = value['alpn'].split(',')
            stream['tlsSettings'] = tls
        return {'protocol':'vmess','settings':{'vnext':[{'address':server,'port':port,'users':[{'id':identifier,'alterId':aid,'security':value.get('scy') or 'auto'}]}]},'streamSettings':stream}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise UnsupportedNode('VMess 节点无效或暂不支持') from None


def singbox_outbound(link, *, allow_hysteria_pin=False):
    parts = urlsplit(link)
    if parts.scheme not in QUIC_SCHEMES or not parts.hostname or not parts.username or parts.path not in ('', '/'):
        raise UnsupportedNode('QUIC 节点地址或凭据无效')
    host = validate_public_host(parts.hostname)
    port = 443 if parts.port is None else parts.port
    if not port:
        raise UnsupportedNode('QUIC 端口无效')
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    params = dict(pairs)
    if len(params) != len(pairs):
        raise UnsupportedNode('存在重复参数')
    insecure = [params.pop(key, '0') for key in ('insecure', 'allow_insecure', 'allowInsecure')]
    pin = params.pop('pinSHA256', '')
    if pin and (not allow_hysteria_pin or parts.scheme not in HYSTERIA_SCHEMES
                or not re.fullmatch(r'[0-9a-fA-F]{64}', pin)):
        raise UnsupportedNode('当前客户端不支持此证书指纹')
    if any(value not in ('', '0', 'false') for value in insecure) and (
        not pin or insecure[0] != '1' or any(value not in ('', '0', 'false') for value in insecure[1:])
    ):
        raise UnsupportedNode('跳过证书校验的节点暂不测试')
    tls = {'enabled': True, 'server_name': validate_public_host(params.pop('sni', '') or host)}
    if pin:
        tls.update(pinSHA256=pin.lower(), insecure=insecure[0] == '1')
    if alpn := params.pop('alpn', ''):
        tls['alpn'] = alpn.split(',')
    proxy = {'type': 'hysteria2', 'server': host, 'server_port': port, 'tls': tls}
    if parts.scheme == 'tuic':
        if not parts.password:
            raise UnsupportedNode('TUIC 密码无效')
        congestion = params.pop('congestion_control', 'cubic')
        relay = params.pop('udp_relay_mode', 'native')
        if congestion not in ('cubic', 'new_reno', 'bbr') or relay not in ('native', 'quic'):
            raise UnsupportedNode('TUIC 传输参数暂不支持')
        proxy.update(type='tuic', uuid=str(uuid.UUID(unquote(parts.username))), password=unquote(parts.password),
                     congestion_control=congestion, udp_relay_mode=relay)
    else:
        auth = parts.netloc.rsplit('@', 1)[0]
        proxy['password'] = unquote(auth)
        obfs = params.pop('obfs', '')
        obfs_password = params.pop('obfs-password', '')
        if obfs == 'salamander' and obfs_password:
            proxy['obfs'] = {'type': obfs, 'password': obfs_password}
        elif obfs or obfs_password:
            raise UnsupportedNode('Hysteria 2 混淆参数暂不支持')
    if params:
        raise UnsupportedNode('存在暂不支持的参数')
    return proxy


def _probe_config(link, core, port, directory):
    local = '127.0.0.1'
    if core == 'xray-core':
        return ('run', '-c'), {
            'log': {'loglevel': 'none'}, 'inbounds': [{'listen': local, 'port': port, 'protocol': 'http'}],
            'outbounds': [outbound(link)],
        }
    if core in ('mihomo', 'shadowsocks-rust'):
        proxy = outbound(link)
        if proxy['protocol'] != 'shadowsocks':
            raise UnsupportedNode('此发布内核的测试仅支持 Shadowsocks')
        node = proxy['settings']['servers'][0]
        if core == 'mihomo':
            return ('-d', directory, '-f'), {
                'port': port, 'bind-address': local, 'allow-lan': False, 'mode': 'rule', 'log-level': 'silent',
                'proxies': [{'name': 'tested-node', 'type': 'ss', 'server': node['address'],
                             'port': node['port'], 'cipher': node['method'], 'password': node['password']}],
                'rules': ['MATCH,tested-node'],
            }
        return ('-c',), {
            'server': node['address'], 'server_port': node['port'],
            'method': node['method'], 'password': node['password'],
            'local_address': local, 'local_port': port, 'protocol': 'http',
        }

    proxy = singbox_outbound(link, allow_hysteria_pin=core == 'hysteria2')
    if core == 'sing-box':
        return ('run', '-c'), {
            'log': {'disabled': True},
            'dns': {'servers': [{'type': 'local', 'tag': 'system'}]},
            'inbounds': [{'listen': local, 'listen_port': port, 'type': 'http'}],
            'outbounds': [{**proxy, 'domain_resolver': 'system', 'tag': 'tested-node'}],
            'route': {'final': 'tested-node'},
        }
    host, server_port, sni = proxy['server'], proxy['server_port'], proxy['tls']['server_name']
    if core == 'hysteria2' and proxy['type'] == 'hysteria2':
        config = {
            'server': f'{_link_host(host)}:{server_port}', 'auth': proxy['password'],
            'tls': {'sni': sni}, 'http': {'listen': f'{local}:{port}'},
        }
        if pin := proxy['tls'].get('pinSHA256'):
            config['tls'].update(insecure=proxy['tls']['insecure'], pinSHA256=pin)
        if obfs := proxy.get('obfs'):
            config['obfs'] = {'type': obfs['type'], obfs['type']: {'password': obfs['password']}}
        return ('client', '--disable-update-check', '-c'), config
    if core == 'tuic-server' and proxy['type'] == 'tuic':
        relay = {
            'server': f'{_link_host(sni)}:{server_port}', 'uuid': proxy['uuid'], 'password': proxy['password'],
            'alpn': proxy['tls'].get('alpn', []), 'congestion_control': proxy['congestion_control'],
            'udp_relay_mode': proxy['udp_relay_mode'],
        }
        if sni != host:
            # The native client accepts an IP override while preserving the certificate name.
            try:
                relay['ip'] = str(ipaddress.ip_address(host))
            except ValueError:
                raise UnsupportedNode('TUIC 发布域名与 TLS 名称不一致') from None
        return ('-c',), {'relay': relay, 'local': {'server': f'{local}:{port}', 'enable_http': True}}
    raise UnsupportedNode('发布内核与节点协议不匹配')


def _http_status(port, url, timeout):
    target = urlsplit(url)
    if target.scheme == 'https':
        connection = HTTPSConnection('127.0.0.1', port, timeout=timeout, context=ssl.create_default_context())
        connection.set_tunnel(target.hostname, target.port or 443)
        destination = urlunsplit(('', '', target.path or '/', target.query, ''))
    else:
        connection = HTTPConnection('127.0.0.1', port, timeout=timeout)
        destination = url
    try:
        connection.request('GET', destination, headers={'Connection': 'close', 'User-Agent': 'HopWorld-Router'})
        with connection.getresponse() as response:
            return response.status
    finally:
        connection.close()


def light_xray_outbound(link):
    config = outbound(link)
    stream = config['streamSettings']
    network, security = stream['network'], stream['security']
    if network not in ('tcp', 'ws') or (security == 'reality' and network != 'tcp'):
        raise UnsupportedNode('此传输方式需使用对应内核')
    protocol = config['protocol']
    if protocol == 'vless':
        endpoint = config['settings']['vnext'][0]
        credentials = {key: endpoint['users'][0][key] for key in ('id', 'flow') if key in endpoint['users'][0]}
    else:
        endpoint = config['settings']['servers'][0]
        credentials = {'password': endpoint['password']}
    result = {'protocol': protocol, 'address': endpoint['address'], 'port': endpoint['port'],
              'network': network, 'security': security, **credentials}
    if security in ('tls', 'reality'):
        tls = stream[f'{security}Settings']
        result.update(server_name=tls.get('serverName') or endpoint['address'], alpn=tls.get('alpn', []),
                      fingerprint=tls.get('fingerprint') or ('chrome' if security == 'reality' else ''))
        if security == 'reality':
            result.update(public_key=tls['publicKey'], short_id=tls.get('shortId', ''), spider_x=tls.get('spiderX') or '/')
    if network == 'ws':
        ws = stream['wsSettings']
        path = urlsplit(ws['path'])
        pairs = parse_qsl(path.query, keep_blank_values=True)
        early_data = next((value for name, value in pairs if name == 'ed'), '0')
        if not early_data.isascii() or not early_data.isdecimal() or len(early_data) > 7:
            raise UnsupportedNode('WebSocket Early Data 参数无效')
        if any(name == 'ed' for name, _ in pairs):
            path = path._replace(query=urlencode([(name, value) for name, value in pairs if name != 'ed']))
        result.update(path=path.geturl(), host=ws['host'], early_data=int(early_data))
    return result


def probe_light(link, binary, target, timeout=8):
    target_name, target_url = TARGETS[target]
    request = {'target': target_url or TEST_URL, 'timeout_ms': int(timeout * 1000)}
    try:
        parts = urlsplit(link)
        if parts.scheme in HYSTERIA_SCHEMES:
            proxy = singbox_outbound(link)
            request.update(
                server=f"{_link_host(proxy['server'])}:{proxy['server_port']}",
                server_name=proxy['tls']['server_name'],
                password=proxy['password'],
                obfs_password=proxy.get('obfs', {}).get('password', ''),
            )
        elif parts.scheme in LIGHT_XRAY_SCHEMES:
            request['xray'] = light_xray_outbound(link)
        else:
            outbound(link)
            request['proxy_url'] = parts._replace(
                scheme='socks5' if parts.scheme == 'socks' else parts.scheme, fragment=''
            ).geturl()
    except UnsupportedNode as exc:
        return {'status': 'unsupported', 'detail': str(exc)}
    except ValueError:
        return {'status': 'unsupported', 'detail': '节点地址或参数无效'}

    started = time.monotonic()
    try:
        result = subprocess.run([binary], input=json.dumps(request).encode(), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {'status': 'failed', 'detail': f'经节点访问 {target_name} 超时'}
    except OSError:
        return {'status': 'unavailable', 'detail': '轻量测试客户端无法运行，可使用已安装的对应内核做完整测试'}
    elapsed = round((time.monotonic() - started) * 1000)
    if result.returncode == 3:
        return {'status': 'failed', 'detail': '节点连接、TLS 校验或认证失败'}
    code = result.stdout.strip()
    if result.returncode or len(code) != 3 or not code.isdigit():
        return {'status': 'failed', 'detail': f'无法经节点连接 {target_name}'}
    return _http_probe_result(int(code), target, elapsed)


def _http_probe_result(status, target, latency_ms):
    target_name = TARGETS[target][0]
    passed = status == 204 if target in STRICT_204_TARGETS else 200 <= status < 400
    if passed:
        return {'status': 'ok', 'latency_ms': latency_ms, 'detail': f'{target_name} 响应正常', 'target': target_name}
    return {'status': 'failed', 'detail': f'{target_name} 返回 HTTP {status}', 'target': target_name}


def probe(link, binary=None, timeout=8, target='google', *, singbox_binary=None, core=None, client_binary=None):
    if target not in TARGETS:
        return {'status': 'unsupported', 'detail': '测试目标暂不支持'}
    target_name, target_url = TARGETS[target]
    process = None
    try:
        if core is None:
            if urlsplit(link).scheme in QUIC_SCHEMES:
                core = 'sing-box'
                client_binary = singbox_binary or os.environ.get('HOPWORLD_SINGBOX') or shutil.which('sing-box')
            else:
                core = 'xray-core'
                client_binary = binary or os.environ.get('HOPWORLD_XRAY') or shutil.which('xray')
        program = {'xray-core': 'Xray', 'sing-box': 'sing-box', 'mihomo': 'mihomo', 'hysteria2': 'hysteria',
                   'shadowsocks-rust': 'sslocal', 'tuic-server': 'tuic-client'}[core]
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix='hopworld-probe-') as directory:
            args, config = _probe_config(link, core, port, directory)
            client = shutil.which(client_binary) if client_binary else None
            if not client:
                return {'status': 'unavailable', 'detail': f'完整测试需要 {program}。请在「内核管理」下载对应内核或客户端。'}
            path = os.path.join(directory, 'config.json')
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as handle:
                json.dump(config, handle)
            process = subprocess.Popen([client, *args, path], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + (timeout if core == 'hysteria2' else 3)
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    return {'status': 'failed', 'detail': f'{program} 测试客户端启动失败'}
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1):
                        break
                except OSError:
                    time.sleep(.05)
            else:
                return {'status': 'failed', 'detail': f'{program} 测试客户端启动超时'}
            start = time.monotonic()
            status = _http_status(port, target_url or TEST_URL, timeout)
            elapsed = round((time.monotonic() - start) * 1000)
        return _http_probe_result(status, target, elapsed)
    except UnsupportedNode as exc:
        return {'status': 'unsupported', 'detail': str(exc)}
    except ValueError:
        return {'status': 'unsupported', 'detail': '节点地址或参数无效'}
    except TimeoutError:
        detail = f'经节点访问 {target_name} 超时'
    except ssl.SSLError:
        detail = f'访问 {target_name} 时 TLS 握手或证书校验失败'
    except (OSError, HTTPException):
        detail = f'无法经节点连接 {target_name}'
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return {'status': 'failed', 'detail': detail, 'target': target_name}
