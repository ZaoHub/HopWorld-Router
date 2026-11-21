"""Canonical admin and subscription URLs from local public host (domain or IP)."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from router.public_endpoint import is_ip_host


def _format_host_for_url(host):
    host = host.strip()
    if is_ip_host(host) and ':' in host:
        return f'[{host}]'
    return host


def resolve_public_host(store):
    settings = store.local_node_settings()
    host = (settings.get('public_host') or '').strip()
    if not host:
        host = os.environ.get('HOPWORLD_PUBLIC_HOST', '').strip()
    return host


def build_public_urls(store, *, subscription_base=''):
    host = resolve_public_host(store)
    token = store.token()
    port = int(os.environ.get('HOPWORLD_PORT', '8080'))
    override = os.environ.get('HOPWORLD_SUBSCRIPTION_BASE', '').strip().rstrip('/')
    selected_base = override if urlsplit(override).scheme == 'https' else (subscription_base or override)
    empty = {
        'host': '',
        'admin_url': '',
        'subscription_url': '',
        'public_subscription_url': '',
        'ikev2_profile_url': '',
        'subscription_https': False,
        'cert_days_left': None,
        'management_note': '',
    }
    if not (host or selected_base) or not token:
        return {**empty, 'host': host}

    acme = store.acme_status()
    cert_path = (acme.get('cert_path') or '').strip()
    https_sub = (
        bool(acme.get('enabled'))
        and (acme.get('domain') or '').strip().lower() == host.lower()
        and cert_path
        and Path(cert_path).is_file()
        and not is_ip_host(host)
    )
    admin = f'http://{_format_host_for_url(host)}:{port}' if host else ''
    public_subscription = ''
    if selected_base:
        subscription = f'{selected_base}/s/{token}'
        if urlsplit(selected_base).scheme == 'https':
            public_subscription = subscription
        if not subscription_base:
            admin = override
    elif https_sub:
        subscription = f'https://{host}/s/{token}'
    else:
        subscription = f'{admin}/s/{token}'

    ikev2_on = bool(store.ikev2_settings().get('enabled'))
    base = subscription.rsplit('/s/', 1)[0]
    ikev2_profile = f'{base}/s/{token}/ikev2.mobileconfig' if ikev2_on else ''
    subscription_host = urlsplit(subscription).hostname
    cert_days = acme.get('days_left') if (acme.get('domain') or '').lower() == subscription_host else None

    return {
        'host': host,
        'admin_url': admin,
        'subscription_url': subscription,
        'public_subscription_url': public_subscription,
        'ikev2_profile_url': ikev2_profile,
        'subscription_https': bool(public_subscription or not selected_base and https_sub),
        'cert_days_left': cert_days,
        'management_note': '',
    }
