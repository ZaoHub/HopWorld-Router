"""Render a pinned, proxy-routed resolver for independent sing-box publishers."""

import ipaddress
import re
import subprocess


SING_BOX_VERSION = '1.14.4'
SERVER_NAME = re.compile(r'(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')


def endpoint_ip(value):
    if not isinstance(value, str) or '%' in value:
        raise ValueError('请输入明确的 IPv4 或 IPv6 地址')
    address = ipaddress.ip_address(value)
    if (address.is_unspecified or address.is_multicast or address.is_link_local or address.is_loopback
            or getattr(address, 'ipv4_mapped', None)):
        raise ValueError('出口地址不能使用回环、链路本地、组播、未指定或映射地址')
    return str(address)


def valid_port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError('端口必须介于 1 和 65535')
    return value


def validate_resolver(data):
    if not isinstance(data, dict) or set(data) != {'name', 'protocol', 'address', 'port', 'server_name', 'path'}:
        raise ValueError('解析器字段无效')
    name = data['name']
    protocol = data['protocol']
    sni = data['server_name']
    path = data['path']
    if not isinstance(name, str) or not name.strip() or len(name) > 60 or not name.isprintable():
        raise ValueError('解析器名称无效')
    if protocol not in ('doh', 'dot'):
        raise ValueError('请选择 DoH 或 DoT')
    if not isinstance(sni, str) or not SERVER_NAME.fullmatch(sni.lower()):
        raise ValueError('请输入用于验证证书的 DNS 主机名')
    if not isinstance(path, str) or (protocol == 'dot' and path) or (
        protocol == 'doh' and not re.fullmatch(r'/[a-zA-Z0-9._~/-]{0,255}', path)
    ):
        raise ValueError('DoH 需要不带参数的 HTTPS 路径，DoT 的路径应留空')
    return {
        'name': name.strip(), 'protocol': protocol, 'address': endpoint_ip(data['address']),
        'port': valid_port(data['port']), 'server_name': sni.lower(), 'path': path,
    }


def require_sing_box_version(binary):
    try:
        result = subprocess.run([binary, 'version'], check=True, timeout=4, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('无法获取 sing-box 的版本') from exc
    if result.stdout.splitlines()[:1] != [f'sing-box version {SING_BOX_VERSION}']:
        raise ValueError(f'网络安全配置需要已核对语法的 sing-box {SING_BOX_VERSION}')


def build_secure_dns_config(config, resolver, *, udp_over_tcp=False):
    if len(config['outbounds']) != 1 or config['outbounds'][0]['type'] != 'socks':
        raise ValueError('网络安全配置需要固定 SOCKS5 上游')
    outbound = config['outbounds'][0].copy()
    outbound['server'] = endpoint_ip(outbound['server'])
    outbound.pop('domain_resolver', None)
    if udp_over_tcp:
        outbound['udp_over_tcp'] = {'enabled': True, 'version': 2}

    tag = 'business-dns'
    doh = resolver['protocol'] == 'doh'
    server = {
        'type': 'https' if doh else 'tls', 'tag': tag, 'server': resolver['address'],
        'server_port': resolver['port'], 'detour': outbound['tag'],
        'tls': {'enabled': True, 'server_name': resolver['server_name'], 'insecure': False},
    }
    if doh:
        server['path'] = resolver['path']
    return {
        **config,
        'log': {'disabled': True},
        'outbounds': [outbound],
        'dns': {'servers': [server], 'final': tag, 'optimistic': False, 'timeout': '4s'},
        'route': {
            'rules': [
                {'port': 53, 'action': 'hijack-dns'},
                {'port': 853, 'action': 'reject'},
                {'action': 'resolve', 'server': tag},
            ],
            'final': outbound['tag'],
        },
    }
