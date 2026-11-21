"""Build an Xray VLESS server configuration and matching client link."""

import ipaddress
import os
import re
import uuid
from urllib.parse import quote, unquote, urlencode, urlsplit

DEFAULT_REALITY_DEST = 'www.microsoft.com:443'
DEFAULT_REALITY_SERVER_NAME = 'www.microsoft.com'
DEFAULT_LOCAL_PORT = 8443
DEFAULT_NODE_NAME = '本地网络'


def _parse_client_id(client_id):
    try:
        return str(uuid.UUID(str(client_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('节点凭据无效') from exc


def _validate_port(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('端口无效')
    return port


def validate_public_host(host):
    """Accept a public IPv4/IPv6 address or DNS hostname for the client link."""
    if not isinstance(host, str) or not host or len(host) > 253:
        raise ValueError('公网地址无效')
    host = host.strip().rstrip('.').lower()
    if not host or '/' in host or ' ' in host or '\x00' in host:
        raise ValueError('公网地址无效')
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.split('.')
    if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
        raise ValueError('公网地址无效')
    return host


def validate_node_name(name):
    """Name shown by subscription clients; it is only the link label."""
    if not isinstance(name, str):
        raise ValueError('节点名称无效')
    name = name.strip()
    if not name or len(name) > 40 or any(ord(char) < 32 or char == '\x7f' for char in name):
        raise ValueError('节点名称无效')
    return name


def _link_host(host):
    try:
        if ipaddress.ip_address(host).version == 6:
            return f'[{host}]'
    except ValueError:
        pass
    return host


def egress_http_outbound_from_environ():
    """When HTTP(S)_PROXY is set (e.g. hatch cloud egress), route client traffic through it."""
    raw = ''
    for key in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy'):
        value = os.environ.get(key, '').strip()
        if value:
            raw = value
            break
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ('http', 'https') or not parts.hostname or not parts.port:
        return None
    server = {'address': parts.hostname, 'port': parts.port}
    if parts.username is not None:
        server['users'] = [{'user': unquote(parts.username), 'pass': unquote(parts.password or '')}]
    return {'protocol': 'http', 'tag': 'egress', 'settings': {'servers': [server]}}


def _finish_local_xray_config(config, proxy_pool=None, probe_url=None, single_proxy=False, probe_interval=60):
    if proxy_pool is not None:
        config['outbounds'] = [{'protocol': 'blackhole', 'tag': 'blocked', 'settings': {}}]
        if proxy_pool:
            if single_proxy:
                config['outbounds'] = [{**proxy_pool[0], 'tag': 'local-proxy'}]
                return config
            config['outbounds'] += [{**outbound, 'tag': f'candidate-{index}'} for index, outbound in enumerate(proxy_pool)]
            config['observatory'] = {'subjectSelector': ['candidate-'], 'probeUrl': probe_url or 'https://www.google.com/generate_204', 'probeInterval': '1m' if probe_interval == 60 else f'{probe_interval}s', 'enableConcurrency': True}
            config['routing'] = {'domainStrategy': 'AsIs', 'balancers': [{'tag': 'auto', 'selector': ['candidate-'], 'fallbackTag': 'blocked', 'strategy': {'type': 'leastPing'}}], 'rules': [{'type': 'field', 'network': 'tcp,udp', 'balancerTag': 'auto'}]}
        return config
    egress = egress_http_outbound_from_environ()
    if egress:
        config['outbounds'] = [
            egress,
            {'protocol': 'freedom', 'tag': 'direct', 'settings': {}},
        ]
        config['routing'] = {
            'domainStrategy': 'AsIs',
            'rules': [
                {'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'direct'},
                {'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'egress'},
            ],
        }
    else:
        config['outbounds'] = [{'protocol': 'freedom', 'tag': 'direct'}]
    return config


def build_local_node(hostname, port, client_id, certificate, private_key, name=DEFAULT_NODE_NAME):
    """Generate TLS configuration only; callers must verify TLS, reachability and Xray readiness."""
    if not isinstance(hostname, str) or len(hostname) > 253 or not hostname:
        raise ValueError('需要有效的域名')
    hostname = hostname.rstrip('.').lower()
    labels = hostname.split('.')
    if len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
        raise ValueError('需要与 TLS 证书匹配的域名')
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError('需要域名而不是 IP 地址')
    port = _validate_port(port)
    client_id = _parse_client_id(client_id)
    name = validate_node_name(name)
    if not all(isinstance(p, str) and p.startswith('/') and '\x00' not in p for p in (certificate, private_key)):
        raise ValueError('证书和私钥必须使用绝对路径')
    config = _finish_local_xray_config({
        'log': {'loglevel': 'warning'},
        'inbounds': [{'listen': '0.0.0.0', 'port': port, 'protocol': 'vless',
                      'settings': {'clients': [{'id': client_id}], 'decryption': 'none'},
                      'streamSettings': {'network': 'tcp', 'security': 'tls', 'tlsSettings': {
                          'certificates': [{'certificateFile': certificate, 'keyFile': private_key}]}}}],
    })
    params = urlencode({'encryption': 'none', 'security': 'tls', 'type': 'tcp', 'sni': hostname})
    link = f'vless://{client_id}@{hostname}:{port}?{params}#{quote(name)}'
    return config, link


def build_local_reality_node(public_host, port, client_id, private_key, public_key, short_id,
                             dest=DEFAULT_REALITY_DEST, server_name=DEFAULT_REALITY_SERVER_NAME,
                             name=DEFAULT_NODE_NAME, proxy_pool=None, probe_url=None, single_proxy=False, probe_interval=60):
    """Generate a VLESS Reality inbound config and matching client share link."""
    if type(probe_interval) is not int or probe_interval not in (30, 60, 120, 300):
        raise ValueError('自动换线探测间隔无效')
    public_host = validate_public_host(public_host)
    port = _validate_port(port)
    client_id = _parse_client_id(client_id)
    if not isinstance(private_key, str) or not private_key or '\x00' in private_key or len(private_key) > 128:
        raise ValueError('Reality 私钥无效')
    if not isinstance(public_key, str) or not public_key or '\x00' in public_key or len(public_key) > 128:
        raise ValueError('Reality 公钥无效')
    if not isinstance(short_id, str) or not re.fullmatch(r'[0-9a-fA-F]{2,16}', short_id):
        raise ValueError('Reality shortId 无效')
    if not isinstance(dest, str) or not dest or len(dest) > 253 or '\x00' in dest:
        raise ValueError('Reality dest 无效')
    if not isinstance(server_name, str) or not server_name or len(server_name) > 253 or '\x00' in server_name:
        raise ValueError('Reality serverName 无效')
    short_id = short_id.lower()
    name = validate_node_name(name)
    config = _finish_local_xray_config({
        'log': {'loglevel': 'warning'},
        'inbounds': [{
            'listen': '0.0.0.0',
            'port': port,
            'protocol': 'vless',
            'settings': {
                'clients': [{'id': client_id, 'flow': 'xtls-rprx-vision'}],
                'decryption': 'none',
            },
            'streamSettings': {
                'network': 'tcp',
                'security': 'reality',
                'realitySettings': {
                    'dest': dest,
                    'serverNames': [server_name],
                    'privateKey': private_key,
                    'shortIds': [short_id],
                },
            },
        }],
    }, proxy_pool=proxy_pool, probe_url=probe_url, single_proxy=single_proxy, probe_interval=probe_interval)
    params = urlencode({
        'encryption': 'none',
        'flow': 'xtls-rprx-vision',
        'security': 'reality',
        'sni': server_name,
        'fp': 'chrome',
        'pbk': public_key,
        'sid': short_id,
        'type': 'tcp',
    })
    link = f'vless://{client_id}@{_link_host(public_host)}:{port}?{params}#{quote(name)}'
    return config, link
