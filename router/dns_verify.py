"""Check whether a hostname's public DNS points at this machine."""

import socket
import threading
import time

from router.dns_hints import _resolve_egress_ipv4, _resolve_egress_ipv6
from router.public_endpoint import is_ip_host


def _lookup_addresses(hostname, timeout=10):
    result = {'ipv4': set(), 'ipv6': set()}
    error = []

    def worker():
        try:
            for family, _, _, _, sockaddr in socket.getaddrinfo(
                hostname,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            ):
                addr = sockaddr[0].split('%')[0].strip().strip('[]')
                if family == socket.AF_INET6:
                    result['ipv6'].add(addr)
                else:
                    result['ipv4'].add(addr)
        except socket.gaierror as exc:
            error.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError('DNS 查询超时')
    if error:
        raise error[0]
    return result


def verify_dns_resolution(settings, probe_expected=False):
    """Return check result comparing DNS answers to expected local public IPs."""
    host = (settings.get('public_host') or '').strip()
    checked_at = int(time.time())
    base = {
        'status': 'skip',
        'detail': '',
        'expected_ipv4': '',
        'expected_ipv6': '',
        'resolved_ipv4': [],
        'resolved_ipv6': [],
        'checked_at': checked_at,
    }

    if not host:
        return {**base, 'detail': '请先填写公网域名'}
    if is_ip_host(host):
        return {**base, 'detail': 'IP 模式无需校验'}
    if settings.get('auto_public_host'):
        return {**base, 'detail': '出口 IP 模式不使用固定域名解析'}

    prefer = (settings.get('public_host_prefer') or 'ipv4').strip().lower()
    check_v6 = bool(settings.get('ddns_enabled')) and prefer in ('ipv6', 'ipv4_first')

    expected_v4 = _resolve_egress_ipv4(settings, probe=probe_expected)
    expected_v6 = _resolve_egress_ipv6(settings, probe=probe_expected and check_v6) if check_v6 else ''

    base['expected_ipv4'] = expected_v4
    base['expected_ipv6'] = expected_v6 or ''

    if not expected_v4 and not (check_v6 and expected_v6):
        return {
            **base,
            'status': 'unknown',
            'detail': '无法探测本机公网 IP',
        }

    try:
        resolved = _lookup_addresses(host)
    except TimeoutError:
        return {**base, 'status': 'error', 'detail': 'DNS 查询超时，请稍后重试'}
    except socket.gaierror as exc:
        return {**base, 'status': 'error', 'detail': f'域名无法解析（{exc}）'}
    except OSError as exc:
        return {**base, 'status': 'error', 'detail': f'DNS 查询失败（{exc}）'}

    base['resolved_ipv4'] = sorted(resolved['ipv4'])
    base['resolved_ipv6'] = sorted(resolved['ipv6'])

    issues = []
    ok_parts = []
    if expected_v4:
        if expected_v4 in resolved['ipv4']:
            ok_parts.append(f'IPv4 正确 · {expected_v4}')
        elif not resolved['ipv4']:
            issues.append(f'缺少 A → {expected_v4}')
        else:
            issues.append(f'期望 {expected_v4}，当前 {", ".join(sorted(resolved["ipv4"]))}')

    if check_v6 and expected_v6:
        if expected_v6 in resolved['ipv6']:
            ok_parts.append(f'IPv6 已指向本机（{expected_v6}）')
        elif not resolved['ipv6']:
            issues.append(f'未解析到 IPv6，期望 AAAA → {expected_v6}')
        else:
            issues.append(f'IPv6 不一致：期望 {expected_v6}，当前 {", ".join(sorted(resolved["ipv6"]))}')

    if issues:
        return {**base, 'status': 'mismatch', 'detail': '；'.join(issues)}
    if ok_parts:
        return {**base, 'status': 'ok', 'detail': '；'.join(ok_parts)}
    return {**base, 'status': 'unknown', 'detail': '解析结果为空'}
