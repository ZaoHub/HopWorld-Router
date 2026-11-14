"""Detect the machine's public egress address for client-facing node links."""

import ipaddress
from urllib.request import Request, urlopen

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


def _fetch_address(url, timeout=15):
    request = Request(url, headers={'User-Agent': 'HopWorld-Router/1.0'})
    with urlopen(request, timeout=timeout) as response:
        text = response.read().decode('utf-8', 'replace').strip()
    if not text or len(text) > 64 or any(ch.isspace() for ch in text):
        raise ValueError('探测结果无效')
    ip = ipaddress.ip_address(text.strip().strip('[]'))
    return str(ip)


def detect_public_ip(version=4, timeout=15):
    """Return egress IPv4 or IPv6 as seen by check services (respects HTTP_PROXY)."""
    if version not in (4, 6):
        raise ValueError('协议版本无效')
    endpoints = IPV4_ENDPOINTS if version == 4 else IPV6_ENDPOINTS
    errors = []
    for url in endpoints:
        try:
            return _fetch_address(url, timeout=timeout)
        except (OSError, ValueError, TimeoutError) as exc:
            errors.append(str(exc))
    raise ValueError('无法探测公网地址') from (errors and Exception(errors[-1]))


def detect_public_host(prefer='ipv4', timeout=15):
    """Pick a client dial address when the egress IP may change over time."""
    if prefer not in ('ipv4', 'ipv6', 'ipv4_first'):
        raise ValueError('公网地址偏好无效')
    if prefer == 'ipv4':
        return detect_public_ip(4, timeout=timeout)
    if prefer == 'ipv6':
        return detect_public_ip(6, timeout=timeout)
    try:
        return detect_public_ip(4, timeout=timeout)
    except ValueError:
        return detect_public_ip(6, timeout=timeout)


def is_ip_host(host):
    try:
        ipaddress.ip_address(host.strip().strip('[]'))
        return True
    except ValueError:
        return False
