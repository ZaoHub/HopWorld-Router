"""Convert a conservative subset of Clash YAML to standard share links."""
import base64
import json
from urllib.parse import quote, urlencode

import yaml
from yaml.events import AliasEvent


class UnsupportedNode(ValueError):
    pass


def _mapping(value, allowed):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - allowed:
        raise UnsupportedNode('不支持的嵌套选项')
    return value


def _text(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value):
        raise UnsupportedNode('缺少必填字段')
    return str(value)


def _convert(node):
    if not isinstance(node, dict): raise UnsupportedNode('节点格式错误')
    common = {'name', 'type', 'server', 'port', 'udp', 'tls', 'skip-cert-verify', 'servername', 'sni', 'client-fingerprint', 'alpn', 'network', 'ws-opts', 'grpc-opts', 'reality-opts', 'xhttp-opts'}
    kind = node.get('type')
    fields = {'vless': {'uuid', 'flow'}, 'trojan': {'password'}, 'ss': {'cipher', 'password'}, 'vmess': {'uuid', 'alterId', 'cipher'}, 'anytls': {'password'}}
    if kind not in fields or set(node) - common - fields[kind]: raise UnsupportedNode('不支持的协议或选项')
    if node.get('udp', True) is not True or node.get('skip-cert-verify', False) is not False: raise UnsupportedNode('不支持的安全或 UDP 选项')
    server = _text(node.get('server'))
    if any(c in server for c in '/@?#[]') or any(c.isspace() for c in server): raise UnsupportedNode('服务器地址无效')
    host = f'[{server}]' if ':' in server else server
    port = node.get('port')
    if isinstance(port, bool): raise UnsupportedNode('端口无效')
    try: port = int(port)
    except (ValueError, TypeError): raise UnsupportedNode('端口无效') from None
    if not 1 <= port <= 65535: raise UnsupportedNode('端口无效')
    name = quote(_text(node.get('name')), safe='')
    if kind == 'ss':
        if node.get('tls', False) is not False or any(k in node for k in ('network','ws-opts','grpc-opts','reality-opts','xhttp-opts','sni','servername','alpn','client-fingerprint')): raise UnsupportedNode('不支持的 SS 选项')
        credential = base64.urlsafe_b64encode(f"{_text(node.get('cipher'))}:{_text(node.get('password'))}".encode()).decode().rstrip('=')
        return f'ss://{credential}@{host}:{port}#{name}'
    network = node.get('network', 'tcp')
    if network not in ('tcp', 'ws', 'grpc', 'xhttp'): raise UnsupportedNode('不支持的传输方式')
    if kind == 'anytls' and (network != 'tcp' or any(k in node for k in ('ws-opts','grpc-opts','reality-opts','xhttp-opts','client-fingerprint','alpn'))): raise UnsupportedNode('不支持的 AnyTLS 选项')
    for option, expected in [('ws-opts','ws'), ('grpc-opts','grpc'), ('xhttp-opts','xhttp')]:
        if option in node and network != expected: raise UnsupportedNode('传输参数不匹配')
    ws = _mapping(node.get('ws-opts'), {'path','headers'})
    headers = _mapping(ws.get('headers'), {'Host','host'})
    if 'Host' in headers and 'host' in headers and headers['Host'] != headers['host']: raise UnsupportedNode('Host 冲突')
    grpc = _mapping(node.get('grpc-opts'), {'grpc-service-name'})
    xhttp = _mapping(node.get('xhttp-opts'), {'path','host','mode'})
    if xhttp.get('mode', 'auto') != 'auto': raise UnsupportedNode('XHTTP 非 auto 模式暂不支持')
    reality = _mapping(node.get('reality-opts'), {'public-key','short-id'})
    if reality and (not reality.get('public-key') or not node.get('tls', False)): raise UnsupportedNode('Reality 参数不完整')
    if node.get('servername') and node.get('sni') and node['servername'] != node['sni']: raise UnsupportedNode('SNI 冲突')
    tls = node.get('tls', kind in ('trojan', 'anytls'))
    if not isinstance(tls, bool) or (kind in ('trojan','anytls') and not tls): raise UnsupportedNode('TLS 设置不支持')
    sni = node.get('sni') or node.get('servername')
    alpn = node.get('alpn')
    if alpn is not None and (not isinstance(alpn, list) or any(not isinstance(x, str) or ',' in x for x in alpn)): raise UnsupportedNode('ALPN 格式错误')
    if kind == 'vmess':
        if reality or grpc or xhttp or node.get('client-fingerprint') or node.get('flow'): raise UnsupportedNode('VMess 选项不支持')
        values = {'v':'2', 'ps':_text(node['name']), 'add':server, 'port':str(port), 'id':_text(node.get('uuid')), 'aid':str(node.get('alterId',0)), 'scy':_text(node.get('cipher','auto')), 'net':network, 'type':'none', 'host':headers.get('Host',headers.get('host','')), 'path':ws.get('path',''), 'tls':'tls' if tls else '', 'sni':sni or '', 'alpn':','.join(alpn or [])}
        payload = base64.b64encode(json.dumps(values, ensure_ascii=False, separators=(',',':')).encode()).decode()
        return 'vmess://' + payload
    query = {}
    if kind == 'vless': query['encryption'] = 'none'
    if kind != 'anytls': query['type'] = network
    if kind == 'vless': query['security'] = 'reality' if reality else ('tls' if tls else 'none')
    if kind == 'trojan': query['security'] = 'tls'
    for key,value in [('sni',sni),('fp',node.get('client-fingerprint')),('alpn',','.join(alpn or [])),('flow',node.get('flow')),('path',ws.get('path') or xhttp.get('path')),('host',headers.get('Host') or headers.get('host') or xhttp.get('host')),('serviceName',grpc.get('grpc-service-name')),('pbk',reality.get('public-key')),('sid',reality.get('short-id'))]:
        if value is not None and value != '': query[key] = _text(value)
    if kind != 'vless' and (reality or node.get('flow')): raise UnsupportedNode('协议选项不支持')
    credential = _text(node.get('uuid') if kind == 'vless' else node.get('password'))
    query_string = '?' + urlencode(query) if query else ''
    return f"{kind}://{quote(credential, safe='')}@{host}:{port}{query_string}#{name}"


def parse_clash(content):
    """Return converted links and skipped-node count, without logging credentials."""
    try:
        if any(isinstance(event, AliasEvent) for event in yaml.parse(content, Loader=yaml.SafeLoader)):
            raise ValueError('不支持 YAML 引用')
        document = yaml.safe_load(content)
    except (yaml.YAMLError, RecursionError, UnicodeError) as exc:
        raise ValueError('Clash YAML 格式错误') from exc
    if not isinstance(document, dict) or not isinstance(document.get('proxies'), list):
        raise ValueError('Clash YAML 缺少节点列表')
    if len(document['proxies']) > 10000: raise ValueError('节点数量超过限制')
    links, skipped = [], 0
    for node in document['proxies']:
        try: links.append(_convert(node))
        except (UnsupportedNode, ValueError, TypeError, KeyError): skipped += 1
    if not links: raise ValueError('没有可转换的节点')
    return links, skipped
