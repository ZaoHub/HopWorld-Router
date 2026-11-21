"""Stage a second, loopback-only management listener and confirm it after login."""

from dataclasses import dataclass
from http.server import ThreadingHTTPServer
import ipaddress
import os
import ssl
import threading
import time

from router.acme import cert_paths, days_until_expiry
from router.local_node import validate_public_host
from router.public_endpoint import is_ip_host


CONFIRM_SECONDS = 180


def _certificate_domain(store):
    settings = store.acme_settings()
    try:
        domain = validate_public_host(settings.get('domain'))
        current = validate_public_host(store.local_node_settings().get('public_host'))
    except ValueError:
        return ''
    if domain != current or is_ip_host(domain):
        return ''
    cert, key = cert_paths(store.data_dir, domain, manual=settings.get('source') == 'manual')
    return domain if cert.is_file() and key.is_file() else ''


class ManagementTLS:
    def __init__(self, store, *, domain=None, paths=None):
        self.domain = _certificate_domain(store) if domain is None else validate_public_host(domain)
        if not self.domain:
            raise ValueError('请先在「域名与证书」签发可用的证书')
        self.store = store
        self.paths = paths or cert_paths(store.data_dir, self.domain, manual=store.acme_settings().get('source') == 'manual')
        self.fixed_paths = paths is not None
        if (days_until_expiry(self.paths[0]) or 0) <= 0:
            raise ValueError('HTTPS 需要仍在有效期内的证书')
        self._lock = threading.Lock()
        self._version = None
        self._context = None
        self.context()

    def context(self):
        with self._lock:
            try:
                paths = self.paths
                if not self.fixed_paths:
                    settings = self.store.acme_settings()
                    if settings.get('domain') == self.domain:
                        paths = cert_paths(self.store.data_dir, self.domain, manual=settings.get('source') == 'manual')
                cert, key = paths
                version = (paths, cert.stat().st_mtime_ns, key.stat().st_mtime_ns)
                if version != self._version:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.minimum_version = ssl.TLSVersion.TLSv1_2
                    context.load_cert_chain(cert, key, password=lambda: '')
                    self._context, self._version = context, version
                    self.paths = paths
            except OSError as exc:
                if self._context is None:
                    raise ValueError('HTTPS 证书与私钥无法加载') from exc
            return self._context


class ManagementHTTPServer(ThreadingHTTPServer):
    request_timeout = None

    def __init__(self, address, handler, tls):
        self.tls = tls
        super().__init__(address, handler)

    def process_request_thread(self, request, client_address):
        if self.tls:
            try:
                request.settimeout(5)
                request = self.tls.context().wrap_socket(request, server_side=True)
                request.settimeout(self.request_timeout)
            except (OSError, ValueError):
                request.close()
                return
        super().process_request_thread(request, client_address)


@dataclass
class PendingAccess:
    listener: ThreadingHTTPServer
    deadline: float
    timer: threading.Timer | None = None


class ManagementAccess:
    def __init__(self, store, base):
        self.store = store
        self.base = base
        self.active = base
        self.pending = None
        self.error = ''
        self.available = ipaddress.ip_address(base.server_address[0]).is_loopback
        self.lock = threading.Lock()
        base.management_access = self

    @staticmethod
    def describe(server):
        host, port = server.server_address[:2]
        tls = getattr(server, 'tls', None)
        return {'host': host, 'port': port, 'https': bool(tls), 'domain': tls.domain if tls else ''}

    def view(self, listener):
        with self.lock:
            pending = self.pending
            return {
                'available': self.available, 'active': self.describe(self.active),
                'base_port': self.base.server_port, 'error': self.error,
                'certificate_domain': _certificate_domain(self.store),
                'pending': {**self.describe(pending.listener), 'expires_at': int(pending.deadline)} if pending else None,
                'can_confirm': bool(pending and listener is pending.listener),
            }

    def address(self):
        with self.lock:
            entry = self.describe(self.active)
        host = entry['domain'] or entry['host']
        if host in ('0.0.0.0', '::'):
            host = '127.0.0.1'
        return f"{'https' if entry['https'] else 'http'}://{host}:{entry['port']}"

    def permits(self, listener):
        with self.lock:
            return listener is self.active or bool(self.pending and listener is self.pending.listener)

    def _bind(self, port, https):
        if port == self.base.server_port:
            if https:
                raise ValueError('原端口继续提供 HTTP 订阅转发，后台 HTTPS 需使用另一个端口')
            return self.base
        tls = ManagementTLS(self.store) if https else None
        try:
            listener = ManagementHTTPServer((self.base.server_address[0], port), self.base.RequestHandlerClass, tls)
        except OSError as exc:
            raise ValueError('无法监听该管理端口，可能已被占用或权限不足') from exc
        listener.management_access = self
        threading.Thread(target=listener.serve_forever, kwargs={'poll_interval': .2}, daemon=True).start()
        return listener

    def restore(self):
        prefs = self.store.management_access_settings()
        if not self.available or not prefs:
            return
        if os.environ.get('HOPWORLD_MANAGEMENT_RECOVERY') == '1':
            self.error = '后台访问已进入恢复模式'
            return
        try:
            self.active = self._bind(prefs['port'], prefs['https'])
        except ValueError as exc:
            self.error = f'无法恢复已设置的后台入口，现使用原端口。{exc}'

    def stage(self, listener, port, https):
        if not self.available:
            raise ValueError('启用页面修改前，管理服务需监听回环地址')
        if type(port) is not int or not 1024 <= port <= 65535 or type(https) is not bool:
            raise ValueError('管理端口需为 1024 至 65535 之间的整数')
        with self.lock:
            if listener is not self.active:
                raise ValueError('请先确认当前待切换的入口')
            if self.pending:
                raise ValueError('已有待确认的入口，请先确认或取消')
            if port == self.active.server_port:
                raise ValueError('请指定一个空闲的新端口进行切换')
            candidate = self._bind(port, https)
            pending = PendingAccess(candidate, time.time() + CONFIRM_SECONDS)
            pending.timer = threading.Timer(CONFIRM_SECONDS, self._expire, args=(pending,))
            pending.timer.daemon = True
            self.pending = pending
            pending.timer.start()

    def _expire(self, pending):
        with self.lock:
            if self.pending is not pending:
                return
            self.pending = None
        self._stop(pending.listener)

    def cancel(self, listener=None):
        with self.lock:
            if listener is not None and listener is not self.active:
                raise ValueError('请回到原入口取消切换')
            pending, self.pending = self.pending, None
            if pending:
                pending.timer.cancel()
        if pending:
            self._stop(pending.listener)

    def confirm(self, listener):
        with self.lock:
            pending = self.pending
            if not pending or listener is not pending.listener or time.time() >= pending.deadline:
                raise ValueError('请在新的后台入口登录后确认，或重新发起切换')
            target = self.describe(listener)
            self.store.save_management_access_settings(target['port'], target['https'], listener is self.base)
            previous, self.active = self.active, listener
            self.pending = None
            self.error = ''
            pending.timer.cancel()
        self._stop(previous)

    def _stop(self, server):
        if server is not self.base:
            server.shutdown()
            server.server_close()

    def close(self):
        self.cancel()
        self._stop(self.active)
