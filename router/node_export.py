"""Render client-only configurations for locally published nodes."""

import hashlib
import json
import ssl
from urllib.parse import urlsplit

import yaml

from router.probe import QUIC_SCHEMES, outbound, singbox_outbound

EXPORT_FORMATS = {
    'vless': ('xray',),
    'ss': ('mihomo', 'sing-box'),
    'hysteria2': ('mihomo', 'sing-box'),
    'hy2': ('mihomo', 'sing-box'),
    'tuic': ('sing-box',),
}


def _client_outbound(uri, scheme):
    if scheme in QUIC_SCHEMES:
        return singbox_outbound(uri, allow_hysteria_pin=True)
    node = outbound(uri)['settings']['servers'][0]
    return {'type': 'shadowsocks', 'server': node['address'], 'server_port': node['port'],
            'method': node['method'], 'password': node['password']}


def _mihomo_proxy(outbound):
    shadowsocks = outbound['type'] == 'shadowsocks'
    proxy = {'name': 'HopWorld', 'type': 'ss' if shadowsocks else outbound['type'],
             'server': outbound['server'], 'port': outbound['server_port'],
             'password': outbound['password'], 'udp': True}
    if shadowsocks:
        proxy['cipher'] = outbound['method']
    else:
        tls = outbound['tls']
        proxy['sni'] = tls['server_name']
        if 'alpn' in tls:
            proxy['alpn'] = tls['alpn']
        if pin := tls.get('pinSHA256'):
            proxy['fingerprint'] = pin
    if 'obfs' in outbound:
        proxy['obfs'] = outbound['obfs']['type']
        proxy['obfs-password'] = outbound['obfs']['password']
    return proxy


def export_client_config(uri, format_name, *, hysteria_certificate=None):
    scheme = urlsplit(uri).scheme
    if format_name not in EXPORT_FORMATS.get(scheme, ()):
        raise ValueError('该发布节点暂不支持此配置格式')
    if format_name == 'xray':
        config = {'inbounds': [{'protocol': 'socks', 'listen': '127.0.0.1', 'port': 10808,
                                'settings': {'udp': True}}],
                  'outbounds': [outbound(uri)]}
        return json.dumps(config, ensure_ascii=False, indent=2) + '\n', 'hopworld-xray.json'
    proxy = _client_outbound(uri, scheme)
    if format_name == 'mihomo':
        config = {'mixed-port': 7890, 'bind-address': '127.0.0.1', 'allow-lan': False, 'mode': 'rule',
                  'proxies': [_mihomo_proxy(proxy)], 'rules': ['MATCH,HopWorld']}
        return yaml.safe_dump(config, allow_unicode=True, sort_keys=False), 'hopworld-mihomo.yaml'
    if pin := proxy.get('tls', {}).pop('pinSHA256', ''):
        try:
            certificate = hysteria_certificate.read_text(encoding='ascii') if hysteria_certificate else ''
            fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
        except (ValueError, OSError) as error:
            raise ValueError('无法读取 Hysteria 2 本地证书，请刷新后重试') from error
        if pin != fingerprint:
            raise ValueError('Hysteria 2 证书已变更，请刷新后重试')
        proxy['tls'].pop('insecure', None)
        proxy['tls']['certificate'] = certificate
    proxy.update(tag='HopWorld', domain_resolver='system')
    config = {'dns': {'servers': [{'type': 'local', 'tag': 'system'}]},
              'inbounds': [{'type': 'mixed', 'listen': '127.0.0.1', 'listen_port': 2080}],
              'outbounds': [proxy], 'route': {'final': 'HopWorld'}}
    return json.dumps(config, ensure_ascii=False, indent=2) + '\n', 'hopworld-sing-box.json'
