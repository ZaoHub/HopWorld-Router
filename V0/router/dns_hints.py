"""DNS record guidance for the local node public-endpoint modes."""

import ipaddress
import os

from router.ddns import split_hostname
from router.public_endpoint import detect_public_ip, is_ip_host


def _ip_version(host):
    try:
        return ipaddress.ip_address(host.strip().strip('[]')).version
    except ValueError:
        return None


def _resolve_egress_ipv4(settings, probe=True):
    cached = (settings.get('ddns_ipv4') or '').strip()
    if cached:
        return cached
    host = (settings.get('public_host') or '').strip()
    if host and is_ip_host(host) and _ip_version(host) == 4:
        return host
    env = os.environ.get('HOPWORLD_PUBLIC_HOST', '').strip()
    if env and is_ip_host(env) and _ip_version(env) == 4:
        return env
    if not probe:
        return ''
    try:
        return detect_public_ip(4, timeout=8)
    except ValueError:
        return ''


def _resolve_egress_ipv6(settings, probe=True):
    cached = (settings.get('ddns_ipv6') or '').strip()
    if cached:
        return cached
    host = (settings.get('public_host') or '').strip()
    if host and is_ip_host(host) and _ip_version(host) == 6:
        return host
    if not probe:
        return ''
    try:
        return detect_public_ip(6, timeout=8)
    except ValueError:
        return ''


def build_dns_hints(settings, probe=True):
    """Return human-facing DNS rows for the current local-node public mode."""
    host = (settings.get('public_host') or '').strip()
    ddns = bool(settings.get('ddns_enabled'))
    auto = bool(settings.get('auto_public_host'))
    prefer = (settings.get('public_host_prefer') or 'ipv4').strip().lower()

    egress_v4 = _resolve_egress_ipv4(settings, probe=probe)
    egress_v6 = _resolve_egress_ipv6(settings, probe=probe and prefer != 'ipv4')

    empty = {
        'mode': 'unknown',
        'summary': '',
        'records': [],
        'egress_ipv4': egress_v4,
        'egress_ipv6': egress_v6,
    }

    if auto:
        return {
            **empty,
            'mode': 'auto_ip',
            'summary': '',
            'records': [],
        }

    if ddns:
        if not host or is_ip_host(host):
            return {
                **empty,
                'mode': 'ddns',
                'summary': '',
                'records': [],
            }
        try:
            sub, _apex = split_hostname(host)
        except ValueError:
            return {**empty, 'mode': 'ddns', 'summary': '域名无效', 'records': []}
        records = []
        if egress_v4:
            records.append({
                'type': 'A',
                'host': sub,
                'value': egress_v4,
                'managed': True,
            })
        if egress_v6 and prefer in ('ipv6', 'ipv4_first'):
            records.append({
                'type': 'AAAA',
                'host': sub,
                'value': egress_v6,
                'managed': True,
            })
        summary = 'Router 自动更新' if records else '无法探测出口 IP'
        return {**empty, 'mode': 'ddns', 'summary': summary, 'records': records}

    if host and is_ip_host(host):
        return {
            **empty,
            'mode': 'fixed_ip',
            'summary': '',
            'records': [],
        }

    if host:
        try:
            sub, _apex = split_hostname(host)
        except ValueError:
            return {**empty, 'mode': 'fixed_domain', 'summary': '域名无效', 'records': []}
        value = egress_v4 or '—'
        records = [{'type': 'A', 'host': sub, 'value': value, 'managed': False}]
        return {**empty, 'mode': 'fixed_domain', 'summary': '', 'records': records}

    return {
        **empty,
        'mode': 'unset',
        'summary': '',
        'records': [],
    }
