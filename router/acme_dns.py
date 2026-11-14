"""DNS-01 helpers: publish _acme-challenge TXT via DNSPod or webhook."""

import json
import os
from pathlib import Path

from router.ddns import _dnspod_token, _post_form, _post_json, split_hostname

STATE_DIRNAME = 'acme-dns-state'
VALID_CHALLENGES = ('auto', 'http', 'dns', 'tls-alpn')


def acme_txt_record_name(cert_domain):
    """Return (sub_domain, zone) for the ACME TXT record."""
    cert_domain = cert_domain.strip().rstrip('.').lower()
    sub, zone = split_hostname(cert_domain)
    if sub == '@':
        return '_acme-challenge', zone
    return f'_acme-challenge.{sub}', zone


def dns01_configured():
    provider = os.environ.get('HOPWORLD_DDNS_PROVIDER', 'webhook').strip().lower()
    if provider == 'dnspod':
        try:
            _dnspod_token()
            return True
        except ValueError:
            return False
    url = (
        os.environ.get('HOPWORLD_ACME_DNS_WEBHOOK_URL', '').strip()
        or os.environ.get('HOPWORLD_DDNS_WEBHOOK_URL', '').strip()
    )
    return bool(url)


def resolve_challenge_mode(requested):
    mode = (requested or os.environ.get('HOPWORLD_ACME_CHALLENGE', 'auto')).strip().lower()
    if mode not in VALID_CHALLENGES:
        raise ValueError('ACME 校验方式无效')
    if mode == 'dns' and not dns01_configured():
        raise ValueError('DNS-01 需要配置 DNSPod 或 Webhook')
    if mode != 'auto':
        return mode
    if dns01_configured():
        return 'dns'
    if os.environ.get('HOPWORLD_ACME_WEBROOT', '').strip():
        return 'http'
    if os.environ.get('HOPWORLD_ACME_TLS_ALPN', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        return 'tls-alpn'
    return 'http'


def challenge_label(mode):
    labels = {
        'http': 'HTTP-01（80）',
        'dns': 'DNS-01',
        'tls-alpn': 'TLS-ALPN-01（443）',
        'auto': '自动',
    }
    return labels.get(mode, mode)


def _state_path(data_dir, cert_domain):
    base = Path(data_dir) / STATE_DIRNAME
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    safe = cert_domain.replace('/', '_')
    return base / f'{safe}.json'


def _webhook_url():
    return (
        os.environ.get('HOPWORLD_ACME_DNS_WEBHOOK_URL', '').strip()
        or os.environ.get('HOPWORLD_DDNS_WEBHOOK_URL', '').strip()
    )


def publish_acme_txt(data_dir, cert_domain, txt_value):
    if not txt_value:
        raise ValueError('TXT 值为空')
    sub, zone = acme_txt_record_name(cert_domain)
    provider = os.environ.get('HOPWORLD_DDNS_PROVIDER', 'webhook').strip().lower()
    state = {'provider': provider, 'sub': sub, 'zone': zone, 'record_id': ''}

    if provider == 'dnspod':
        login_token = _dnspod_token()
        line = os.environ.get('HOPWORLD_DDNS_RECORD_LINE', '默认').strip() or '默认'
        created = _post_form(
            'https://dnsapi.cn/Record.Create',
            {
                'login_token': login_token,
                'format': 'json',
                'domain': zone,
                'sub_domain': sub,
                'record_type': 'TXT',
                'record_line': line,
                'value': txt_value,
            },
        )
        records = created.get('record', {})
        if isinstance(records, dict):
            state['record_id'] = str(records.get('id', ''))
        if not state['record_id']:
            listed = _post_form(
                'https://dnsapi.cn/Record.List',
                {
                    'login_token': login_token,
                    'format': 'json',
                    'domain': zone,
                    'sub_domain': sub,
                    'record_type': 'TXT',
                },
            )
            for item in listed.get('records') or []:
                if item.get('name') == sub or item.get('sub_domain') == sub:
                    state['record_id'] = str(item.get('id', ''))
                    break
    else:
        url = _webhook_url()
        if not url:
            raise ValueError('请配置 HOPWORLD_ACME_DNS_WEBHOOK_URL 或 DDNS Webhook')
        _post_json(
            url,
            {
                'action': 'acme_txt',
                'phase': 'publish',
                'hostname': cert_domain,
                'sub_domain': sub,
                'zone': zone,
                'txt': txt_value,
            },
        )

    _state_path(data_dir, cert_domain).write_text(
        json.dumps(state, ensure_ascii=False),
        encoding='utf-8',
    )


def clear_acme_txt(data_dir, cert_domain):
    path = _state_path(data_dir, cert_domain)
    if not path.is_file():
        return
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        path.unlink(missing_ok=True)
        return
    provider = state.get('provider') or 'webhook'
    if provider == 'dnspod':
        record_id = (state.get('record_id') or '').strip()
        if record_id:
            _post_form(
                'https://dnsapi.cn/Record.Remove',
                {
                    'login_token': _dnspod_token(),
                    'format': 'json',
                    'domain_id': os.environ.get('HOPWORLD_DDNS_ID', '').strip(),
                    'record_id': record_id,
                },
            )
    else:
        url = _webhook_url()
        if url:
            _post_json(
                url,
                {
                    'action': 'acme_txt',
                    'phase': 'clear',
                    'hostname': cert_domain,
                    'sub_domain': state.get('sub') or '',
                    'zone': state.get('zone') or '',
                },
            )
    path.unlink(missing_ok=True)
