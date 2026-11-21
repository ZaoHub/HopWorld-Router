"""Canonical admin and subscription URLs from local public host (domain or IP)."""

import os
from pathlib import Path

from router.public_endpoint import is_ip_host


def _format_host_for_url(host):
    host = host.strip()
    if is_ip_host(host) and ':' in host and '.' not in host:
        return f'[{host}]'
    return host


def resolve_public_host(store):
    settings = store.local_node_settings()
    host = (settings.get('public_host') or '').strip()
    if not host:
        host = os.environ.get('HOPWORLD_PUBLIC_HOST', '').strip()
    return host


def build_public_urls(store):
    host = resolve_public_host(store)
    token = store.token()
    port = int(os.environ.get('HOPWORLD_PORT', '8080'))
    empty = {
        'host': '',
        'admin_url': '',
        'subscription_url': '',
        'ikev2_profile_url': '',
        'subscription_https': False,
        'cert_days_left': None,
        'management_note': '',
    }
    if not host or not token:
        return {**empty, 'host': host}

    acme = store.acme_status()
    cert_path = (acme.get('cert_path') or '').strip()
    https_sub = (
        bool(acme.get('enabled'))
        and not (acme.get('error') or '').strip()
        and cert_path
        and Path(cert_path).is_file()
        and not is_ip_host(host)
    )
    override = os.environ.get('HOPWORLD_SUBSCRIPTION_BASE', '').strip().rstrip('/')

    if override:
        subscription = f'{override}/s/{token}'
        admin = override
    elif https_sub:
        subscription = f'https://{host}/s/{token}'
        admin = f'http://{_format_host_for_url(host)}:{port}'
    else:
        formatted = _format_host_for_url(host)
        admin = f'http://{formatted}:{port}'
        subscription = f'{admin}/s/{token}'

    ikev2_on = bool(store.ikev2_settings().get('enabled'))
    base = subscription.rsplit('/s/', 1)[0]
    ikev2_profile = f'{base}/s/{token}/ikev2.mobileconfig' if ikev2_on else ''
    cert_days = acme.get('days_left') if https_sub else None

    return {
        'host': host,
        'admin_url': admin,
        'subscription_url': subscription,
        'ikev2_profile_url': ikev2_profile,
        'subscription_https': https_sub,
        'cert_days_left': cert_days,
        'management_note': '',
    }
