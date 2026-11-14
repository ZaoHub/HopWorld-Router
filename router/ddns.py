"""Push detected public IPs to a DNS provider (stable hostname without Cloudflare Tunnel)."""

import json
import os
import re
import urllib.parse
import urllib.request
from router.public_endpoint import is_ip_host

_HOST = re.compile(
    r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$',
    re.IGNORECASE,
)


def split_hostname(hostname):
    """Return (sub_domain, apex_domain) for common DNS APIs."""
    host = hostname.strip().rstrip('.').lower()
    if not host or is_ip_host(host) or not _HOST.match(host):
        raise ValueError('DDNS 需要有效的域名')
    labels = host.split('.')
    if len(labels) < 2:
        raise ValueError('DDNS 需要有效的域名')
    if len(labels) == 2:
        return '@', host
    return labels[0], '.'.join(labels[1:])


def _dnspod_token():
    token = os.environ.get('HOPWORLD_DDNS_TOKEN', '').strip()
    domain_id = os.environ.get('HOPWORLD_DDNS_ID', '').strip()
    if not token or not domain_id:
        raise ValueError('请配置 HOPWORLD_DDNS_ID 与 HOPWORLD_DDNS_TOKEN')
    return f'{domain_id},{token}'


def _post_form(url, fields, timeout=20):
    body = urllib.parse.urlencode(fields).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=body,
        headers={'User-Agent': 'HopWorld-Router/1.0', 'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode('utf-8', 'replace')
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError('DNS 接口返回无效') from exc
    if data.get('status', {}).get('code') not in ('1', 1):
        message = data.get('status', {}).get('message') or 'DNS 更新失败'
        raise ValueError(str(message))
    return data


def _post_json(url, payload, timeout=20):
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=body,
        headers={'User-Agent': 'HopWorld-Router/1.0', 'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status >= 400:
            raise ValueError(f'DNS Webhook 失败 ({response.status})')


def publish_ddns(hostname, ipv4=None, ipv6=None):
    """Update DNS records for hostname using the configured provider."""
    if not hostname or is_ip_host(hostname):
        raise ValueError('DDNS 需要固定域名')
    provider = os.environ.get('HOPWORLD_DDNS_PROVIDER', 'webhook').strip().lower()
    if provider == 'webhook':
        url = os.environ.get('HOPWORLD_DDNS_WEBHOOK_URL', '').strip()
        if not url:
            raise ValueError('请配置 HOPWORLD_DDNS_WEBHOOK_URL')
        _post_json(url, {'hostname': hostname, 'ipv4': ipv4, 'ipv6': ipv6})
        return
    if provider == 'dnspod':
        sub, domain = split_hostname(hostname)
        login_token = _dnspod_token()
        line = os.environ.get('HOPWORLD_DDNS_RECORD_LINE', '默认').strip() or '默认'
        if ipv4:
            _post_form(
                'https://dnsapi.cn/Record.Ddns',
                {
                    'login_token': login_token,
                    'format': 'json',
                    'domain': domain,
                    'sub_domain': sub,
                    'record_line': line,
                    'ip': ipv4,
                },
            )
        if not ipv4:
            raise ValueError('未探测到可写入的公网地址')
        return
    raise ValueError('不支持的 DDNS 提供方')
