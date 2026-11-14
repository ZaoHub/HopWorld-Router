"""Test a supported node with an isolated Xray and a real HTTPS request."""
import base64
import binascii
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from urllib.parse import parse_qsl, unquote, urlsplit

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


def probe(link, binary=None, timeout=8, target='google'):
    if target not in TARGETS: return {'status':'unsupported', 'detail':'测试目标暂不支持'}
    try: proxy = outbound(link)
    except (UnsupportedNode, ValueError): return {'status':'unsupported', 'detail':'节点协议或参数暂不支持测试'}
    binary = binary or os.environ.get('HOPWORLD_XRAY') or shutil.which('xray')
    curl = shutil.which('curl')
    if not binary or not os.path.isfile(binary) or not os.access(binary, os.X_OK) or not curl:
        return {'status':'unavailable', 'detail':'未找到 Xray 或 curl，请检查服务配置'}
    process = None
    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix='hopworld-probe-') as directory:
            path = os.path.join(directory,'config.json')
            config = {'log':{'loglevel':'none'},'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'http','settings':{}}], 'outbounds':[proxy]}
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as f: json.dump(config, f)
            process = subprocess.Popen([binary,'run','-config',path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if process.poll() is not None: return {'status':'failed', 'detail':'节点测试核心启动失败'}
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1): break
                except OSError: time.sleep(.05)
            else: return {'status':'failed', 'detail':'节点测试核心启动超时'}
            start = time.monotonic()
            target_name, target_url = TARGETS[target]
            result = subprocess.run([curl,'--silent','--show-error','--proxy',f'http://127.0.0.1:{port}','--noproxy','','--max-time',str(timeout),'--output',os.devnull,'--write-out','%{http_code}',target_url or TEST_URL], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout+2, check=False)
            elapsed = round((time.monotonic()-start)*1000)
            status = result.stdout.strip()
            passed = status == b'204' if target in STRICT_204_TARGETS else status.isdigit() and 200 <= int(status) < 400
            if result.returncode == 0 and passed: return {'status':'ok','latency_ms':elapsed, 'detail':f'{target_name} 响应正常', 'target':target_name}
            return {'status':'failed','detail':f'{target_name} 未返回预期响应', 'target':target_name}
    except (OSError, subprocess.TimeoutExpired): return {'status':'failed','detail':'连接失败或超时'}
    finally:
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
