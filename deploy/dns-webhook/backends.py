"""DNS provider backends for HopWorld Router webhook (stdlib only)."""

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

_HOST = re.compile(
    r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$',
    re.IGNORECASE,
)


def split_hostname(hostname):
    host = hostname.strip().rstrip('.').lower()
    if not host or not _HOST.match(host):
        raise ValueError('无效域名')
    labels = host.split('.')
    if len(labels) < 2:
        raise ValueError('无效域名')
    if len(labels) == 2:
        return '@', host
    return labels[0], '.'.join(labels[1:])


def _json_request(method, url, headers=None, body=None, timeout=30):
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers = {**(headers or {}), 'Content-Type': 'application/json'}
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:500]
        raise ValueError(f'HTTP {exc.code}: {detail}') from exc


def _get_text(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'HopWorld-DNS-Webhook/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', 'replace')


def backend_name():
    return os.environ.get('HOPWORLD_DNS_BACKEND', 'cloudflare').strip().lower()


# --- Cloudflare ---


def _cf_token():
    token = os.environ.get('CF_API_TOKEN', '').strip()
    if not token:
        raise ValueError('请配置 CF_API_TOKEN')
    return token


def _cf_zone_id(zone):
    zone_id = os.environ.get('CF_ZONE_ID', '').strip()
    if zone_id:
        return zone_id
    _, body = _json_request(
        'GET',
        f'https://api.cloudflare.com/client/v4/zones?name={urllib.parse.quote(zone)}',
        headers={'Authorization': f'Bearer {_cf_token()}'},
    )
    zones = (body or {}).get('result') or []
    if not zones:
        raise ValueError(f'Cloudflare 未找到区域 {zone}')
    return zones[0]['id']


def _cf_record_name(zone, sub):
    if sub in ('@', ''):
        return zone
    return f'{sub}.{zone}'


def _cf_find_record(zone_id, name, rtype):
    q = urllib.parse.urlencode({'name': name, 'type': rtype})
    _, body = _json_request(
        'GET',
        f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?{q}',
        headers={'Authorization': f'Bearer {_cf_token()}'},
    )
    for item in (body or {}).get('result') or []:
        if item.get('name', '').lower() == name.lower() and item.get('type') == rtype:
            return item.get('id')
    return None


def _cf_upsert(zone, sub, rtype, content, ttl=120):
    zone_id = _cf_zone_id(zone)
    name = _cf_record_name(zone, sub)
    record_id = _cf_find_record(zone_id, name, rtype)
    payload = {'type': rtype, 'name': name, 'content': content, 'ttl': ttl, 'proxied': False}
    headers = {'Authorization': f'Bearer {_cf_token()}'}
    if record_id:
        _json_request('PATCH', f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}', headers, payload)
    else:
        _json_request('POST', f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records', headers, payload)


def _cf_delete(zone, sub, rtype):
    zone_id = _cf_zone_id(zone)
    name = _cf_record_name(zone, sub)
    record_id = _cf_find_record(zone_id, name, rtype)
    if not record_id:
        return
    _json_request(
        'DELETE',
        f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}',
        headers={'Authorization': f'Bearer {_cf_token()}'},
    )


def cloudflare_ddns(hostname, ipv4=None, ipv6=None):
    sub, zone = split_hostname(hostname)
    if ipv4:
        _cf_upsert(zone, sub, 'A', ipv4)
    if ipv6:
        _cf_upsert(zone, sub, 'AAAA', ipv6)
    if not ipv4 and not ipv6:
        raise ValueError('缺少 ipv4/ipv6')


def cloudflare_acme_txt(phase, zone, sub, txt=''):
    if phase == 'publish':
        _cf_upsert(zone, sub, 'TXT', txt, ttl=60)
    else:
        _cf_delete(zone, sub, 'TXT')


# --- Namesilo ---


def _namesilo_key():
    key = os.environ.get('NAMESILO_API_KEY', '').strip()
    if not key:
        raise ValueError('请配置 NAMESILO_API_KEY')
    return key


def _namesilo_call(operation, **params):
    query = {'version': '1', 'type': 'xml', 'key': _namesilo_key(), 'operation': operation, **params}
    url = 'https://www.namesilo.com/api/' + operation + '?' + urllib.parse.urlencode(query)
    root = ET.fromstring(_get_text(url))
    code = (root.findtext('reply/code') or '').strip()
    if code != '300':
        detail = root.findtext('reply/detail') or 'Namesilo API 失败'
        raise ValueError(detail)
    return root


def _namesilo_rr(sub):
    return '@' if sub in ('@', '') else sub


def _namesilo_list(domain, rtype, host):
    root = _namesilo_call('dnsListRecords', domain=domain)
    for rec in root.findall('.//resource_record'):
        if (rec.findtext('type') or '').upper() == rtype.upper() and (rec.findtext('host') or '') == host:
            return rec.findtext('record_id')
    return None


def _namesilo_upsert(domain, sub, rtype, value):
    host = _namesilo_rr(sub)
    rid = _namesilo_list(domain, rtype, host)
    if rid:
        _namesilo_call(
            'dnsUpdateRecord',
            domain=domain,
            rrid=rid,
            rr=host,
            rtype=rtype,
            address=value,
        )
    else:
        _namesilo_call('dnsAddRecord', domain=domain, rr=host, rtype=rtype, address=value)


def _namesilo_delete(domain, sub, rtype):
    host = _namesilo_rr(sub)
    rid = _namesilo_list(domain, rtype, host)
    if rid:
        _namesilo_call('dnsDeleteRecord', domain=domain, rrid=rid)


def namesilo_ddns(hostname, ipv4=None, ipv6=None):
    sub, zone = split_hostname(hostname)
    if ipv4:
        _namesilo_upsert(zone, sub, 'A', ipv4)
    if ipv6:
        _namesilo_upsert(zone, sub, 'AAAA', ipv6)
    if not ipv4 and not ipv6:
        raise ValueError('缺少 ipv4/ipv6')


def namesilo_acme_txt(phase, zone, sub, txt=''):
    if phase == 'publish':
        _namesilo_upsert(zone, sub, 'TXT', txt)
    else:
        _namesilo_delete(zone, sub, 'TXT')


# --- Aliyun (Alidns) ---


def _aliyun_keys():
    ak = os.environ.get('ALIYUN_ACCESS_KEY_ID', '').strip()
    sk = os.environ.get('ALIYUN_ACCESS_KEY_SECRET', '').strip()
    if not ak or not sk:
        raise ValueError('请配置 ALIYUN_ACCESS_KEY_ID / ALIYUN_ACCESS_KEY_SECRET')
    return ak, sk


def _aliyun_rpc(action, params):
    ak, sk = _aliyun_keys()
    endpoint = os.environ.get('ALIYUN_DNS_ENDPOINT', 'https://alidns.aliyuncs.com/').strip()
    public = {
        'Format': 'JSON',
        'Version': '2015-01-09',
        'AccessKeyId': ak,
        'SignatureMethod': 'HMAC-SHA1',
        'Timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'SignatureVersion': '1.0',
        'SignatureNonce': str(uuid.uuid4()),
        'Action': action,
        **params,
    }
    items = sorted(public.items())
    query = urllib.parse.urlencode(items, quote_via=urllib.parse.quote)
    string_to_sign = 'GET&%2F&' + urllib.parse.quote(query, safe='')
    digest = hmac.new((sk + '&').encode(), string_to_sign.encode(), hashlib.sha1).digest()
    public['Signature'] = base64.b64encode(digest).decode()
    url = endpoint + '?' + urllib.parse.urlencode(public)
    with urllib.request.urlopen(urllib.request.Request(url, method='GET'), timeout=30) as resp:
        body = json.loads(resp.read().decode('utf-8', 'replace'))
    if body.get('Code'):
        raise ValueError(body.get('Message') or body.get('Code'))
    return body


def _aliyun_rr(sub):
    return '@' if sub in ('@', '') else sub


def _aliyun_find(domain, rr, rtype):
    body = _aliyun_rpc('DescribeDomainRecords', {'DomainName': domain, 'RRKeyWord': rr, 'TypeKeyWord': rtype})
    for row in body.get('DomainRecords', {}).get('Record') or []:
        if row.get('RR') == rr and row.get('Type') == rtype:
            return row.get('RecordId')
    return None


def _aliyun_upsert(domain, sub, rtype, value):
    rr = _aliyun_rr(sub)
    rid = _aliyun_find(domain, rr, rtype)
    if rid:
        _aliyun_rpc('UpdateDomainRecord', {'RecordId': rid, 'RR': rr, 'Type': rtype, 'Value': value})
    else:
        _aliyun_rpc('AddDomainRecord', {'DomainName': domain, 'RR': rr, 'Type': rtype, 'Value': value, 'TTL': 600})


def _aliyun_delete(domain, sub, rtype):
    rr = _aliyun_rr(sub)
    rid = _aliyun_find(domain, rr, rtype)
    if rid:
        _aliyun_rpc('DeleteDomainRecord', {'RecordId': rid})


def aliyun_ddns(hostname, ipv4=None, ipv6=None):
    sub, zone = split_hostname(hostname)
    if ipv4:
        _aliyun_upsert(zone, sub, 'A', ipv4)
    if ipv6:
        _aliyun_upsert(zone, sub, 'AAAA', ipv6)
    if not ipv4 and not ipv6:
        raise ValueError('缺少 ipv4/ipv6')


def aliyun_acme_txt(phase, zone, sub, txt=''):
    if phase == 'publish':
        _aliyun_upsert(zone, sub, 'TXT', txt)
    else:
        _aliyun_delete(zone, sub, 'TXT')


# --- Huawei Cloud DNS (公网域名解析) ---


def _huawei_token():
    ak = os.environ.get('HUAWEI_ACCESS_KEY_ID', '').strip()
    sk = os.environ.get('HUAWEI_SECRET_ACCESS_KEY', '').strip()
    project = os.environ.get('HUAWEI_PROJECT_ID', '').strip()
    if not ak or not sk or not project:
        raise ValueError('请配置 HUAWEI_ACCESS_KEY_ID / HUAWEI_SECRET_ACCESS_KEY / HUAWEI_PROJECT_ID')
    return ak, sk, project


def huawei_ddns(hostname, ipv4=None, ipv6=None):
    """Huawei API 需按区域开通 DNS；建议先用 Cloudflare / 阿里云，或自写转发到函数工作流。"""
    _huawei_token()
    raise ValueError(
        '华为云 DNS 各区域接口不同，请用 deploy/dns-webhook/README.md 中「自建转发」示例，'
        '或设置 HOPWORLD_DNS_BACKEND=cloudflare|aliyun|namesilo'
    )


def huawei_acme_txt(phase, zone, sub, txt=''):
    huawei_ddns(f'{sub}.{zone}' if sub != '@' else zone)


_BACKENDS = {
    'cloudflare': (cloudflare_ddns, cloudflare_acme_txt),
    'namesilo': (namesilo_ddns, namesilo_acme_txt),
    'aliyun': (aliyun_ddns, aliyun_acme_txt),
    'huawei': (huawei_ddns, huawei_acme_txt),
}


def handle_payload(payload: dict):
    name = backend_name()
    if name not in _BACKENDS:
        raise ValueError(f'未知 HOPWORLD_DNS_BACKEND: {name}')
    ddns_fn, acme_fn = _BACKENDS[name]
    action = (payload.get('action') or 'ddns').strip().lower()
    if action == 'acme_txt':
        phase = (payload.get('phase') or 'publish').strip().lower()
        zone = (payload.get('zone') or '').strip()
        sub = (payload.get('sub_domain') or payload.get('sub') or '').strip()
        if not zone or not sub:
            raise ValueError('acme_txt 缺少 zone / sub_domain')
        acme_fn(phase, zone, sub, (payload.get('txt') or '').strip())
        return 'acme_txt ok'
    hostname = (payload.get('hostname') or '').strip()
    if not hostname:
        raise ValueError('缺少 hostname')
    ddns_fn(hostname, payload.get('ipv4'), payload.get('ipv6'))
    return 'ddns ok'
