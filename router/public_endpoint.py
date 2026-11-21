"""Detect the machine's public egress address for client-facing node links."""

import ipaddress
import socket
from urllib.request import ProxyHandler, Request, build_opener, urlopen

IPV4_ENDPOINTS = (
    'https://api4.ipify.org',
    'https://ipv4.icanhazip.com',
    'https://v4.ident.me',
)
IPV6_ENDPOINTS = (
    'https://api6.ipify.org',
    'https://ipv6.icanhazip.com',
    'https://v6.ident.me',
)


def _fetch_address(url, timeout=15, *, open_url=urlopen):
    request = Request(url, headers={'User-Agent': 'HopWorld-Router/1.0'})
    with open_url(request, timeout=timeout) as response:
        text = response.read().decode('utf-8', 'replace').strip()
    if not text or len(text) > 64 or any(ch.isspace() for ch in text):
        raise ValueError('探测结果无效')
    ip = ipaddress.ip_address(text.strip().strip('[]'))
    return str(ip)


def detect_public_ip(version=4, timeout=15, *, direct=False):
    """Return the egress address as seen by check services."""
    if version not in (4, 6):
        raise ValueError('协议版本无效')
    endpoints = IPV4_ENDPOINTS if version == 4 else IPV6_ENDPOINTS
    open_url = build_opener(ProxyHandler({})).open if direct else urlopen
    errors = []
    for url in endpoints:
        try:
            return _fetch_address(url, timeout=timeout, open_url=open_url)
        except (OSError, ValueError, TimeoutError) as exc:
            errors.append(str(exc))
    raise ValueError('无法探测公网地址') from (errors and Exception(errors[-1]))


def detect_public_host(prefer='ipv4', timeout=15, *, direct=False):
    """Pick a client dial address when the egress IP may change over time."""
    if prefer not in ('ipv4', 'ipv6', 'ipv4_first'):
        raise ValueError('公网地址偏好无效')
    if prefer == 'ipv4':
        return detect_public_ip(4, timeout=timeout, direct=direct)
    if prefer == 'ipv6':
        return detect_public_ip(6, timeout=timeout, direct=direct)
    try:
        return detect_public_ip(4, timeout=timeout, direct=direct)
    except ValueError:
        return detect_public_ip(6, timeout=timeout, direct=direct)


def is_ip_host(host):
    try:
        ipaddress.ip_address(host.strip().strip('[]'))
        return True
    except ValueError:
        return False


def direct_address_status(version):
    family = socket.AF_INET6 if version == 6 else socket.AF_INET
    local = ''
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as route:
            route.connect(('2606:4700:4700::1111' if version == 6 else '1.1.1.1', 443))
            local = route.getsockname()[0].split('%', 1)[0]
    except OSError:
        pass
    public = ''
    try:
        detected = ipaddress.ip_address(detect_public_ip(version, timeout=2, direct=True))
        if detected.version == version and detected.is_global and not detected.is_multicast:
            public = str(detected)
    except ValueError:
        pass
    if not local:
        kind = '无本机路由'
    elif version == 4 and ipaddress.ip_address(local) in ipaddress.ip_network('100.64.0.0/10'):
        kind = '共享地址（CGNAT 地址段）'
    elif not ipaddress.ip_address(local).is_global:
        kind = '内网或隧道接口'
    else:
        kind = '本机公网地址' if local == public else '本机全球单播地址'
    return {'local': local, 'public': public, 'kind': kind}
