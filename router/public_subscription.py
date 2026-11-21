"""Run a dedicated HTTPS listener for the public subscription."""

import socket
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path

from router.acme import HYSTERIA_CERTIFICATE_NAME, cert_paths, ip_certificate_paths, validate_ip_certificate
from router.local_node import validate_public_host
from router.management_access import ManagementHTTPServer, ManagementTLS
from router.public_endpoint import is_ip_host


DEFAULT_PORT = 9443
DEFAULT_TOPBAR_LABELS = {'running': '订阅开', 'stopped': '订阅关', 'error': '订阅异常'}


class SubscriptionServer(ManagementHTTPServer):
    subscription_only = True
    request_timeout = 10


class IPv6SubscriptionServer(SubscriptionServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


class PublicSubscription:
    def __init__(self, store):
        self.store = store
        self.settings = store.public_subscription_settings()
        self.listeners = ()
        self.error = ''
        self.lock = threading.RLock()

    def _view(self):
        domain, port = self.settings['domain'], self.settings['port']
        host = f'[{domain}]' if ':' in domain else domain
        base = f'https://{host}' + (f':{port}' if port != 443 else '') if self.listeners else ''
        return {**self.settings, 'base': base, 'error': self.error,
                'topbar_labels': self.settings.get('topbar_labels', DEFAULT_TOPBAR_LABELS)}

    def view(self):
        with self.lock:
            return self._view()

    def save_topbar_labels(self, labels):
        if labels.keys() != DEFAULT_TOPBAR_LABELS.keys() or any(
            not isinstance(value, str) or len(value) > 4 or any(
                char != '〇' and not unicodedata.name(char, '').startswith(
                    ('CJK UNIFIED IDEOGRAPH-', 'CJK COMPATIBILITY IDEOGRAPH-')
                ) for char in value
            ) for value in labels.values()
        ):
            raise ValueError('状态文字可留空，每项最多四个汉字')
        with self.lock:
            settings = {**self.settings, 'topbar_labels': labels.copy()}
            self.store.save_public_subscription_settings(settings)
            self.settings = settings
            return self._view()

    def certificate_status(self, acme):
        choices = {}
        domain = acme.get('domain')
        if domain and domain != HYSTERIA_CERTIFICATE_NAME and acme.get('certificate_valid'):
            choices[domain] = 'domain'

        records = [(record.get('address'), record.get('expires', 0)) for record in self.store.ip_certificates().values()]
        hysteria = self.store.independent_profiles().get('hysteria2', {})
        if hysteria.get('tls_mode') == 'ip_acme' and not hysteria.get('ip_certificate_pending'):
            records.append((hysteria.get('public_host'), hysteria.get('ip_certificate_expires', 0)))
        now = time.time()
        for address, expiry in records:
            if not address or (expiry or 0) <= now or address in choices:
                continue
            try:
                paths = ip_certificate_paths(self.store.data_dir, address)
            except ValueError:
                continue
            if all(Path(path).is_file() for path in paths):
                choices[address] = 'ip'
        local_cert = cert_paths(self.store.data_dir, HYSTERIA_CERTIFICATE_NAME)
        return {'certificates': [{'host': host, 'type': kind} for host, kind in choices.items()],
                'hysteria_local': all(path.is_file() for path in local_cert)}

    def _certificate(self, domain):
        if domain and is_ip_host(domain):
            paths = tuple(map(Path, ip_certificate_paths(self.store.data_dir, domain)))
            try:
                validate_ip_certificate(*paths, domain)
            except OSError as exc:
                raise ValueError('请先为此地址准备受信任的公网 IP 证书') from exc
            return ManagementTLS(self.store, domain=domain, paths=paths)
        settings = self.store.acme_settings()
        if not domain or domain == HYSTERIA_CERTIFICATE_NAME or settings.get('domain') != domain:
            raise ValueError('公网订阅需要与访问地址匹配的受信任域名或公网 IP 证书')
        return ManagementTLS(self.store, domain=domain)

    def _tracked_ip_source(self, host):
        family = 'ipv6' if ':' in host else 'ipv4'
        record = self.store.ip_certificates().get(family, {})
        if record.get('enabled') and not record.get('manual') and record.get('address') == host:
            return family
        profile = self.store.independent_profiles().get('hysteria2', {})
        if profile.get('enabled') and profile.get('tls_mode') == 'ip_acme' and profile.get('public_host') == host:
            return 'hysteria2'
        return ''

    def sync_ip_binding(self, handler):
        with self.lock:
            source = self.settings.get('ip_source')
            if not self.settings['enabled'] or not source:
                return
            if source == 'hysteria2':
                profile = self.store.independent_profiles().get(source, {})
                if not profile.get('enabled') or profile.get('tls_mode') != 'ip_acme' or profile.get('ip_certificate_pending'):
                    return
                address, expiry = profile.get('public_host'), profile.get('ip_certificate_expires', 0)
            else:
                record = self.store.ip_certificates().get(source, {})
                if not record.get('enabled') or record.get('manual'):
                    return
                address, expiry = record.get('address'), record.get('expires', 0)
            if address and expiry > time.time() and (address != self.settings['domain'] or not self.listeners):
                try:
                    self.configure(handler, True, self.settings['port'], address)
                except ValueError as exc:
                    self.error = f'订阅证书切换失败：{exc}'

    def _bind(self, port, handler, tls):
        listeners = []
        try:
            listeners.append(SubscriptionServer(('0.0.0.0', port), handler, tls))
            if socket.has_dualstack_ipv6():
                listeners.append(IPv6SubscriptionServer(('::', port), handler, tls))
        except OSError as exc:
            for listener in listeners:
                listener.server_close()
            raise ValueError('无法监听 HTTPS 订阅端口，请检查端口是否已被占用及服务权限') from exc
        return tuple(listeners)

    @staticmethod
    def _start(listeners):
        for listener in listeners:
            threading.Thread(target=listener.serve_forever, kwargs={'poll_interval': .1}, daemon=True).start()

    @staticmethod
    def _stop(listeners):
        for listener in listeners:
            listener.shutdown()
            listener.server_close()

    def restore(self, handler):
        with self.lock:
            if self.listeners or not self.settings['enabled']:
                return
            try:
                tls = self._certificate(self.settings['domain'])
                self.listeners = self._bind(self.settings['port'], handler, tls)
                self._start(self.listeners)
                self.error = ''
            except ValueError as exc:
                self.error = str(exc)

    def configure(self, handler, enabled, port, host=None):
        if type(enabled) is not bool:
            raise ValueError('请选择启用或停用公网订阅')
        with self.lock:
            previous = self.listeners
            settings = {**self.settings, 'enabled': enabled}
            listeners = ()
            if enabled:
                if type(port) is not int or not 1 <= port <= 65535:
                    raise ValueError('HTTPS 订阅端口须为 1 至 65535 之间的整数')
                if host is None:
                    options = self.certificate_status(self.store.acme_status())['certificates']
                    host = next((item['host'] for item in options if item['host'] == settings['domain']),
                                options[0]['host'] if options else '')
                if host:
                    host = validate_public_host(host)
                domain = host or ''
                tls = self._certificate(domain)
                listeners = previous if previous and self.settings['port'] == port else self._bind(port, handler, tls)
                settings.update(domain=domain, port=port)
                settings.pop('ip_source', None)
                if is_ip_host(domain) and (source := self._tracked_ip_source(domain)):
                    settings['ip_source'] = source
            try:
                self.store.save_public_subscription_settings(settings)
            except sqlite3.Error:
                if listeners is not previous:
                    for listener in listeners:
                        listener.server_close()
                raise
            self.settings, self.listeners, self.error = settings, listeners, ''
            for listener in listeners:
                listener.tls = tls
            if listeners is not previous:
                self._start(listeners)
                self._stop(previous)
            return self._view()

    def close(self):
        with self.lock:
            listeners, self.listeners = self.listeners, ()
            self._stop(listeners)
