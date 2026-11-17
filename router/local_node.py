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
LOCAL_EGRESS_SOCKS_PORT = 37891


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


def quic_ports(port):
    """UDP ports for Hysteria 2 and TUIC, distinct from the VLESS port and the loopback SOCKS port."""
    port = _validate_port(port)
    used = {port, LOCAL_EGRESS_SOCKS_PORT}
    found = []
    for candidate in (port + 1, port + 2, port - 1, port - 2, 8444, 8445):
        if 1 <= candidate <= 65535 and candidate not in used:
            found.append(candidate)
            used.add(candidate)
        if len(found) == 2:
            return found[0], found[1]
    raise ValueError('没有可用的 Hysteria 2 / TUIC 端口')


def _secret(value, label):
    if not isinstance(value, str) or not 16 <= len(value) <= 128 or any(char in value for char in ':/@?# \x00'):
        raise ValueError(f'{label}无效')
    return value


def _protocol_label(name, suffix):
    label = f'{name} {suffix}'
    if len(label) <= 40:
        return label
    room = 40 - len(suffix) - 1
    return suffix[:40] if room < 1 else f'{name[:room]} {suffix}'


def build_quic_links(public_host, port, client_id, hy2_password, tuic_password, name=DEFAULT_NODE_NAME, insecure=True):
    """Share links for the same local egress, carried over Hysteria 2 and TUIC."""
    public_host = validate_public_host(public_host)
    port = _validate_port(port)
    client_id = _parse_client_id(client_id)
    hy2_password = _secret(hy2_password, 'Hysteria 2 密码')
    tuic_password = _secret(tuic_password, 'TUIC 密码')
    name = validate_node_name(name)
    hy2_port, tuic_port = quic_ports(port)
    host = _link_host(public_host)
    hy2_query = {'sni': public_host}
    tuic_query = {'congestion_control': 'bbr', 'udp_relay_mode': 'native', 'alpn': 'h3', 'sni': public_host}
    if insecure:
        hy2_query['insecure'] = '1'
        tuic_query['allow_insecure'] = '1'
    hy2 = f"hysteria2://{quote(hy2_password, safe='')}@{host}:{hy2_port}?{urlencode(hy2_query)}#{quote(_protocol_label(name, 'Hysteria2'))}"
    tuic = f"tuic://{client_id}:{quote(tuic_password, safe='')}@{host}:{tuic_port}?{urlencode(tuic_query)}#{quote(_protocol_label(name, 'TUIC'))}"
    return hy2, tuic


def build_singbox_config(public_host, port, client_id, hy2_password, tuic_password, certificate, key):
    """Sing-box inbounds that forward into the local Xray SOCKS egress."""
    public_host = validate_public_host(public_host)
    client_id = _parse_client_id(client_id)
    hy2_password = _secret(hy2_password, 'Hysteria 2 密码')
    tuic_password = _secret(tuic_password, 'TUIC 密码')
    if not all(isinstance(path, str) and path.startswith('/') and '\x00' not in path for path in (certificate, key)):
        raise ValueError('证书和私钥必须使用绝对路径')
    hy2_port, tuic_port = quic_ports(port)
    tls = {'enabled': True, 'server_name': public_host, 'certificate_path': certificate, 'key_path': key, 'alpn': ['h3']}
    return {
        'log': {'level': 'warn'},
        'inbounds': [
            {'type': 'hysteria2', 'tag': 'hy2-in', 'listen': '0.0.0.0', 'listen_port': hy2_port, 'users': [{'name': 'hopworld', 'password': hy2_password}], 'tls': tls},
            {'type': 'tuic', 'tag': 'tuic-in', 'listen': '0.0.0.0', 'listen_port': tuic_port, 'users': [{'uuid': client_id, 'password': tuic_password}], 'congestion_control': 'bbr', 'tls': dict(tls)},
        ],
        # shortcut: IPv4 only; listen on :: as well when clients connect over IPv6 without NAT64.
        'outbounds': [{'type': 'socks', 'tag': 'egress', 'server': '127.0.0.1', 'server_port': LOCAL_EGRESS_SOCKS_PORT, 'version': '5'}],
    }
