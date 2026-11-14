"""Loopback-only subscription manager; run behind an authenticated secure tunnel."""
import base64
from contextlib import contextmanager
import hmac
import hashlib
import ipaddress
import json
import os
import secrets
import signal
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from router.subscription import MAX_SUBSCRIPTION_BYTES, combine_subscriptions, decode_subscription, encode_subscription, parse_subscription
from router.probe import TARGETS, TEST_URL, UnsupportedNode, outbound, probe
from router.local_proxy_discovery import discover
from router.local_node import DEFAULT_LOCAL_PORT, DEFAULT_NODE_NAME, validate_node_name, validate_public_host
from router.public_endpoint import detect_public_host, detect_public_ip, is_ip_host
from router.ddns import publish_ddns
from router.acme import days_until_expiry, short_acme_error, sync_certificate, validate_acme_challenge, validate_acme_email
from router.dns_hints import build_dns_hints
from router.dns_verify import verify_dns_resolution
from router.public_urls import build_public_urls
from router.xray_service import XrayService
from router.ikev2_service import Ikev2Service, build_mobileconfig, validate_psk, validate_remote_id

STATIC = Path(__file__).parent / "static"
MAX_BODY = 16384
LOCAL_NODE_KEY = 'local_node'
IKEV2_KEY = 'ikev2'
ACME_KEY = 'acme'


def _default_acme_settings():
    email = os.environ.get('HOPWORLD_ACME_EMAIL', '').strip()
    auto = os.environ.get('HOPWORLD_ACME', '').strip().lower() in ('1', 'true', 'yes', 'on')
    challenge = os.environ.get('HOPWORLD_ACME_CHALLENGE', 'auto').strip().lower() or 'auto'
    return {
        'enabled': auto,
        'email': email,
        'domain': '',
        'days_left': None,
        'cert_path': '',
        'key_path': '',
        'error': '',
        'challenge': challenge if challenge in ('auto', 'http', 'dns', 'tls-alpn') else 'auto',
        'challenge_effective': '',
    }


def node_id(link):
    return hashlib.sha256(link.encode('utf-8')).hexdigest()


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Store:
    def __init__(self, path, xray=None, ikev2=None):
        self._local_lock = threading.RLock()
        self.path = str(path)
        self.data_dir = Path(self.path).parent
        self.xray = xray or XrayService(self.data_dir)
        self.ikev2 = ikev2 or Ikev2Service(self.data_dir)
        self._dns_verify_cache = None
        self._dns_verify_key = ''
        self._dns_verify_at = 0.0
        Path(self.path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not Path(self.path).exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, links TEXT NOT NULL DEFAULT '[]', updated INTEGER, error TEXT); CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);''')
            db.executescript('''CREATE TABLE IF NOT EXISTS manual_nodes (id TEXT PRIMARY KEY, link TEXT NOT NULL); CREATE TABLE IF NOT EXISTS disabled_nodes (id TEXT PRIMARY KEY);''')
            db.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', ('token', secrets.token_urlsafe(32)))
            if db.execute('SELECT 1 FROM settings WHERE key=?', (LOCAL_NODE_KEY,)).fetchone() is None:
                initial = self.xray.default_settings(os.environ.get('HOPWORLD_PUBLIC_HOST', ''))
                db.execute('INSERT INTO settings VALUES (?, ?)', (LOCAL_NODE_KEY, json.dumps(initial, ensure_ascii=False)))
            if db.execute('SELECT 1 FROM settings WHERE key=?', (IKEV2_KEY,)).fetchone() is None:
                initial = self.ikev2.default_settings(os.environ.get('HOPWORLD_PUBLIC_HOST', ''))
                db.execute('INSERT INTO settings VALUES (?, ?)', (IKEV2_KEY, json.dumps(initial, ensure_ascii=False)))
            if db.execute('SELECT 1 FROM settings WHERE key=?', (ACME_KEY,)).fetchone() is None:
                db.execute('INSERT INTO settings VALUES (?, ?)', (ACME_KEY, json.dumps(_default_acme_settings(), ensure_ascii=False)))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def sources(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id, name, updated, error, links FROM sources ORDER BY id')]

    def add(self, name, url):
        if not isinstance(name, str) or not isinstance(url, str):
            raise ValueError('名称或地址无效')
        name, url = name.strip(), url.strip()
        if not name or len(name) > 100 or len(url) > 4096:
            raise ValueError('名称或地址无效')
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment:
            raise ValueError('订阅地址必须为 HTTPS 地址，且不能包含用户名、密码或片段')
        with self.connect() as db:
            return db.execute('INSERT INTO sources(name,url) VALUES (?,?)', (name, url)).lastrowid

    def delete(self, source_id):
        with self.connect() as db:
            return db.execute('DELETE FROM sources WHERE id=?', (source_id,)).rowcount > 0

    def refresh(self, source_id):
        with self.connect() as db:
            row = db.execute('SELECT url FROM sources WHERE id=?', (source_id,)).fetchone()
        if row is None:
            raise ValueError('订阅不存在')
        try:
            request = Request(row['url'], headers={'User-Agent': 'HopWorld-Router/0.1', 'Accept': 'text/plain, */*'})
            with build_opener(NoRedirects()).open(request, timeout=15) as response:
                body = response.read(MAX_SUBSCRIPTION_BYTES + 1)
            links, skipped = parse_subscription(body)
            warning = f'已跳过 {skipped} 个暂不支持的节点或参数' if skipped else None
            with self.connect() as db:
                db.execute('UPDATE sources SET links=?, updated=?, error=? WHERE id=?', (json.dumps(links, ensure_ascii=False), int(time.time()), warning, source_id))
            return len(links)
        except Exception:
            # Never include the URL or server response in logs or browser errors.
            with self.connect() as db:
                db.execute('UPDATE sources SET error=? WHERE id=?', ('更新失败，请检查订阅地址、格式和网络', source_id))
            raise ValueError('更新失败，请检查订阅地址、格式和网络') from None

    def token(self):
        with self.connect() as db:
            return db.execute('SELECT value FROM settings WHERE key=?', ('token',)).fetchone()[0]

    def rotate(self):
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('UPDATE settings SET value=? WHERE key=?', (token, 'token'))
        return token

    def local_node_settings(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (LOCAL_NODE_KEY,)).fetchone()
        settings = json.loads(row['value']) if row else self.xray.default_settings(os.environ.get('HOPWORLD_PUBLIC_HOST', ''))
        if not settings.get('public_host') and os.environ.get('HOPWORLD_PUBLIC_HOST'):
            try:
                settings['public_host'] = validate_public_host(os.environ['HOPWORLD_PUBLIC_HOST'])
            except ValueError:
                pass
        return settings

    def save_local_node_settings(self, settings):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (LOCAL_NODE_KEY, json.dumps(settings, ensure_ascii=False)))

    def local_link(self, settings=None):
        settings = settings or self.local_node_settings()
        if not settings.get('enabled'):
            return ''
        try:
            _, link = self.xray.build(settings)
            return link
        except ValueError:
            return ''

    def subscription(self):
        try:
            self.sync_public_endpoint(force=True)
        except ValueError:
            pass
        with self.connect() as db:
            rows = db.execute('SELECT links FROM sources ORDER BY id').fetchall()
            manual = [row['link'] for row in db.execute('SELECT link FROM manual_nodes ORDER BY rowid')]
            disabled = {row['id'] for row in db.execute('SELECT id FROM disabled_nodes')}
        local = [link] if (link := self.local_link()) else []
        links = combine_subscriptions([json.loads(row['links']) for row in rows] + [manual, local])
        links = [link for link in links if node_id(link) not in disabled]
        return encode_subscription(links) if links else ''

    def add_manual(self, content):
        if not isinstance(content, str): raise ValueError('节点内容无效')
        links = decode_subscription(content.encode('utf-8'))
        with self.connect() as db:
            db.executemany('INSERT OR IGNORE INTO manual_nodes VALUES (?, ?)', ((node_id(link), link) for link in links))

    def remove_manual(self, identifier):
        with self.connect() as db:
            return db.execute('DELETE FROM manual_nodes WHERE id=?', (identifier,)).rowcount > 0

    def nodes(self):
        with self.connect() as db:
            rows = db.execute('SELECT id, links FROM sources ORDER BY id').fetchall()
            manual = {row['id']: row['link'] for row in db.execute('SELECT id,link FROM manual_nodes ORDER BY rowid')}
            disabled = {row['id'] for row in db.execute('SELECT id FROM disabled_nodes')}
        source_links = [(f"source:{row['id']}", json.loads(row['links'])) for row in rows]
        membership = {}
        for group, group_links in source_links:
            for link in group_links: membership.setdefault(node_id(link), set()).add(group)
        for identifier in manual: membership.setdefault(identifier, set()).add('manual')
        local_link = self.local_link()
        local_links = [local_link] if local_link else []
        if local_link:
            membership.setdefault(node_id(local_link), set()).add('local')
        links = combine_subscriptions([group_links for _, group_links in source_links] + [list(manual.values()), local_links])
        result = []
        for index, link in enumerate(links, 1):
            parts = urlsplit(link)
            identifier = node_id(link)
            title = unquote(parts.fragment).strip()[:120] or f'节点 {index}'
            result.append({'id': identifier, 'title': title, 'protocol': parts.scheme.lower(),
                           'enabled': identifier not in disabled, 'manual': identifier in manual,
                           'local': bool(local_link and identifier == node_id(local_link)),
                           'groups': sorted(membership[identifier])})
        return result

    def node_link(self, identifier):
        with self.connect() as db:
            rows = db.execute('SELECT links FROM sources ORDER BY id').fetchall()
            manual = [row['link'] for row in db.execute('SELECT link FROM manual_nodes ORDER BY rowid')]
        local = [link] if (link := self.local_link()) else []
        for link in combine_subscriptions([json.loads(row['links']) for row in rows] + [manual, local]):
            if node_id(link) == identifier: return link
        raise ValueError('节点不存在')

    def set_node_enabled(self, identifier, enabled):
        if not isinstance(identifier, str) or not isinstance(enabled, bool): raise ValueError('节点设置无效')
        if not any(node['id'] == identifier for node in self.nodes()): raise ValueError('节点不存在')
        with self.connect() as db:
            if enabled: db.execute('DELETE FROM disabled_nodes WHERE id=?', (identifier,))
            else: db.execute('INSERT OR IGNORE INTO disabled_nodes VALUES (?)', (identifier,))

    def refresh_auto_public_host(self, settings):
        settings = dict(settings)
        if not settings.get('auto_public_host'):
            return settings, False
        prefer = settings.get('public_host_prefer') or 'ipv4'
        if prefer not in ('ipv4', 'ipv6', 'ipv4_first'):
            prefer = 'ipv4'
        host = validate_public_host(detect_public_host(prefer))
        old = settings.get('public_host') or ''
        settings['public_host'] = host
        settings['public_host_detected_at'] = int(time.time())
        return settings, host != old

    def refresh_ddns(self, settings):
        settings = dict(settings)
        if not settings.get('ddns_enabled'):
            return settings, False
        host = (settings.get('public_host') or '').strip()
        if not host or is_ip_host(host):
            raise ValueError('DDNS 需要固定域名作为公网地址')
        prefer = settings.get('public_host_prefer') or 'ipv4'
        ipv4 = ipv6 = None
        if prefer in ('ipv4', 'ipv4_first'):
            try:
                ipv4 = detect_public_ip(4)
            except ValueError:
                pass
        if prefer == 'ipv6':
            try:
                ipv6 = detect_public_ip(6)
            except ValueError:
                pass
        elif prefer == 'ipv4_first' and not ipv4:
            try:
                ipv6 = detect_public_ip(6)
            except ValueError:
                pass
        if not ipv4 and not ipv6:
            raise ValueError('无法探测公网地址')
        old4 = settings.get('ddns_ipv4') or ''
        old6 = settings.get('ddns_ipv6') or ''
        changed = (ipv4 and ipv4 != old4) or (ipv6 and ipv6 != old6)
        if changed or not (old4 or old6):
            publish_ddns(host, ipv4=ipv4, ipv6=ipv6)
            if ipv4:
                settings['ddns_ipv4'] = ipv4
            if ipv6:
                settings['ddns_ipv6'] = ipv6
            settings['ddns_updated_at'] = int(time.time())
        return settings, changed

    def sync_ddns(self, force=False):
        settings = self.local_node_settings()
        if not settings.get('enabled') or not settings.get('ddns_enabled'):
            return False
        now = int(time.time())
        min_gap = max(1, int(os.environ.get('HOPWORLD_PUBLIC_HOST_MIN_INTERVAL', '5')))
        last_probe = int(settings.get('public_host_last_probe_at') or 0)
        if not force and now - last_probe < min_gap:
            return False
        settings['public_host_last_probe_at'] = now
        try:
            settings, changed = self.refresh_ddns(settings)
        except ValueError:
            self.save_local_node_settings(settings)
            raise
        self.save_local_node_settings(settings)
        return changed

    def sync_public_endpoint(self, force=False):
        with self._local_lock:
            settings = self.local_node_settings()
            if not settings.get('enabled'):
                return False
            if settings.get('ddns_enabled'):
                return self.sync_ddns(force=force)
            if settings.get('auto_public_host'):
                return self.sync_auto_public_host(force=force)
            return False

    def sync_auto_public_host(self, force=False):
        settings = self.local_node_settings()
        if not settings.get('enabled') or not settings.get('auto_public_host'):
            return False
        now = int(time.time())
        min_gap = max(1, int(os.environ.get('HOPWORLD_PUBLIC_HOST_MIN_INTERVAL', '5')))
        last_probe = int(settings.get('public_host_last_probe_at') or 0)
        if not force and now - last_probe < min_gap:
            return False
        settings['public_host_last_probe_at'] = now
        try:
            settings, changed = self.refresh_auto_public_host(settings)
        except ValueError:
            self.save_local_node_settings(settings)
            raise
        self.save_local_node_settings(settings)
        return changed

    def _auto_pool_entries(self, settings):
        pool = []
        identifiers = []
        with self.connect() as db:
            sources = [json.loads(row['links']) for row in db.execute('SELECT links FROM sources ORDER BY id')]
            manual = [row['link'] for row in db.execute('SELECT link FROM manual_nodes ORDER BY rowid')]
            disabled = {row['id'] for row in db.execute('SELECT id FROM disabled_nodes')}
        local_link = self.local_link()
        for link in combine_subscriptions(sources + [manual]):
            if node_id(link) in disabled or link == local_link:
                continue
            parts = urlsplit(link)
            try:
                host = (parts.hostname or '').lower()
                if host == 'localhost':
                    continue
                try:
                    address = ipaddress.ip_address(host)
                    if address.is_loopback or address.is_unspecified:
                        continue
                except ValueError:
                    pass
                if host == (settings.get('public_host') or '').lower() and parts.port == settings.get('port'):
                    continue
                pool.append(outbound(link))
                identifiers.append(node_id(link))
            except (UnsupportedNode, ValueError):
                continue
        return pool, identifiers

    def _auto_pool(self, settings):
        return self._auto_pool_entries(settings)[0]

    def reconcile_auto_pool(self):
        with self._local_lock:
            settings = self.local_node_settings()
            if not settings.get('enabled') or settings.get('egress_mode') != 'auto':
                return
            pool, identifiers = self._auto_pool_entries(settings)
            if pool == settings.get('proxy_pool') and identifiers == settings.get('auto_pool_ids'):
                return
            changed = pool != settings.get('proxy_pool')
            settings['proxy_pool'] = pool
            settings['auto_pool_ids'] = identifiers
            if changed:
                try:
                    self.xray.apply(settings)
                except ValueError:
                    self.xray.stop()
                    raise
            self.save_local_node_settings(settings)

    def configure_local_node(self, *args, **kwargs):
        with self._local_lock:
            return self._configure_local_node(*args, **kwargs)

    def _configure_local_node(self, enabled, public_host=None, port=None, name=None,
                             auto_public_host=None, public_host_prefer=None, ddns_enabled=None,
                             acme_enabled=None, acme_email=None, acme_challenge=None,
                             egress_mode=None, local_proxy=None, auto_target=None):
        if not isinstance(enabled, bool):
            raise ValueError('本地节点设置无效')
        settings = self.local_node_settings()
        if name is not None:
            settings['name'] = validate_node_name(name)
        elif not settings.get('name'):
            settings['name'] = DEFAULT_NODE_NAME
        if auto_public_host is not None:
            settings['auto_public_host'] = bool(auto_public_host)
        if ddns_enabled is not None:
            settings['ddns_enabled'] = bool(ddns_enabled)
        if settings.get('ddns_enabled') and settings.get('auto_public_host'):
            raise ValueError('固定域名 DDNS 与出口写入订阅 IP 不能同时开启')
        if public_host_prefer is not None:
            prefer = str(public_host_prefer).strip().lower()
            if prefer not in ('ipv4', 'ipv6', 'ipv4_first'):
                raise ValueError('公网地址偏好无效')
            settings['public_host_prefer'] = prefer
        if public_host is not None:
            if not isinstance(public_host, str):
                raise ValueError('公网地址无效')
            if public_host.strip() and not settings.get('auto_public_host'):
                settings['public_host'] = validate_public_host(public_host)
            elif not public_host.strip() and not settings.get('auto_public_host') and not settings.get('ddns_enabled'):
                settings['public_host'] = ''
        if port is not None:
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError('端口无效')
            settings['port'] = port
        if egress_mode is not None:
            if egress_mode not in ('default', 'auto', 'local_proxy'):
                raise ValueError('出口模式无效')
            settings['egress_mode'] = egress_mode
        if auto_target is not None:
            if auto_target not in ('google', 'youtube', 'cloudflare'):
                raise ValueError('自动换线测试目标无效')
            settings['auto_target'] = auto_target
        if local_proxy is not None:
            if not isinstance(local_proxy, str) or len(local_proxy) > 20:
                raise ValueError('本机代理无效')
            settings['local_proxy'] = local_proxy
        mode = settings.get('egress_mode', 'default')
        if mode == 'auto':
            settings['proxy_pool'], settings['auto_pool_ids'] = self._auto_pool_entries(settings)
            target = settings.get('auto_target', 'google')
            settings['auto_probe_url'] = TARGETS[target][1] or TEST_URL
        elif mode == 'local_proxy':
            settings.pop('auto_pool_ids', None)
            try:
                protocol, raw_port = (settings.get('local_proxy') or '').split(':')
                proxy_port = int(raw_port)
                if protocol not in ('http', 'socks') or not 1 <= proxy_port <= 65535 or proxy_port == settings.get('port'):
                    raise ValueError()
            except ValueError:
                raise ValueError('请选择有效的本机代理，且不能使用本地节点入口端口') from None
            if enabled and {'protocol': protocol, 'port': proxy_port} not in discover([proxy_port]):
                raise ValueError('本机代理不可用或无法连接本地地址，请重新扫描')
            settings['proxy_pool'] = [{'protocol': protocol, 'settings': {'servers': [{'address': '127.0.0.1', 'port': proxy_port}]}}]
            settings['auto_probe_url'] = TEST_URL
        else:
            settings.pop('proxy_pool', None)
            settings.pop('auto_pool_ids', None)
        if enabled:
            if settings.get('ddns_enabled'):
                if not settings.get('public_host') or is_ip_host(settings.get('public_host', '')):
                    raise ValueError('DDNS 模式下请填写域名作为公网地址')
                settings, _ = self.refresh_ddns(settings)
            elif settings.get('auto_public_host'):
                settings, _ = self.refresh_auto_public_host(settings)
            if not settings.get('public_host'):
                raise ValueError('无法探测公网地址，请检查网络或改为手动填写')
            settings = self.xray.ensure_credentials(settings)
            settings['enabled'] = True
            self.xray.apply(settings)
            self.save_local_node_settings(settings)
        else:
            settings['enabled'] = False
            self.xray.apply(settings)
            self.save_local_node_settings(settings)
        view = self.xray.public_view(settings)
        if acme_enabled is not None or acme_email is not None or acme_challenge is not None:
            acme_on = bool(acme_enabled) if acme_enabled is not None else self.acme_settings().get('enabled')
            email = acme_email if acme_email is not None else self.acme_settings().get('email')
            challenge = acme_challenge if acme_challenge is not None else self.acme_settings().get('challenge')
            view['acme'] = self.configure_acme(acme_on, email, challenge)
        else:
            view['acme'] = self.acme_status()
        return self.attach_dns_hints(view, settings)

    def dns_verify_for_settings(self, settings, force=False, probe_expected=False):
        host = (settings.get('public_host') or '').strip()
        key = '|'.join([
            host,
            str(settings.get('ddns_enabled')),
            str(settings.get('auto_public_host')),
            settings.get('ddns_ipv4') or '',
            settings.get('ddns_ipv6') or '',
            settings.get('public_host_prefer') or '',
            os.environ.get('HOPWORLD_PUBLIC_HOST', '').strip(),
        ])
        now = time.time()
        if (
            not force
            and self._dns_verify_cache
            and self._dns_verify_key == key
            and now - self._dns_verify_at < 45
        ):
            return dict(self._dns_verify_cache)
        result = verify_dns_resolution(settings, probe_expected=probe_expected or force)
        self._dns_verify_cache = result
        self._dns_verify_key = key
        self._dns_verify_at = now
        return dict(result)

    def attach_dns_hints(self, view, settings):
        hints = build_dns_hints(settings, probe=False)
        if hints['mode'] in ('fixed_domain', 'ddns', 'auto_ip') and not hints.get('egress_ipv4'):
            hints = build_dns_hints(settings, probe=True)
        view['dns_hints'] = hints
        if hints.get('mode') in ('fixed_domain', 'ddns'):
            view['dns_verify'] = self.dns_verify_for_settings(settings, probe_expected=False)
        else:
            view['dns_verify'] = verify_dns_resolution(settings, probe_expected=False)
        return view

    def preview_local_node_settings(self, public_host=None, auto_public_host=None, ddns_enabled=None, public_host_prefer=None):
        settings = dict(self.local_node_settings())
        if auto_public_host is not None:
            settings['auto_public_host'] = str(auto_public_host) in ('1', 'true', 'True', 'yes', 'on')
        if ddns_enabled is not None:
            settings['ddns_enabled'] = str(ddns_enabled) in ('1', 'true', 'True', 'yes', 'on')
        if public_host_prefer is not None:
            settings['public_host_prefer'] = str(public_host_prefer).strip().lower() or 'ipv4'
        if public_host is not None:
            ph = str(public_host).strip()
            if ph and not settings.get('auto_public_host'):
                try:
                    settings['public_host'] = validate_public_host(ph)
                except ValueError:
                    settings['public_host'] = ph
            elif not ph and not settings.get('auto_public_host'):
                settings['public_host'] = ''
            else:
                settings['public_host'] = ph
        return settings

    def preview_dns_hints(self, public_host=None, auto_public_host=None, ddns_enabled=None, public_host_prefer=None):
        settings = self.preview_local_node_settings(
            public_host, auto_public_host, ddns_enabled, public_host_prefer,
        )
        return build_dns_hints(settings, probe=True)

    def local_node_status(self):
        settings = self.local_node_settings()
        view = self.xray.public_view(settings)
        return self.attach_dns_hints(view, settings)

    def auto_observation_status(self):
        with self._local_lock:
            settings = self.local_node_settings()
            if not settings.get('enabled') or settings.get('egress_mode') != 'auto':
                return {'state': 'inactive', 'nodes': []}
            if not self.xray.running():
                return {'state': 'stopped', 'nodes': []}
            if not settings.get('proxy_pool'):
                return {'state': 'empty', 'nodes': []}
            data = self.xray.auto_observations()
            if data is None:
                return {'state': 'unavailable', 'nodes': []}
            titles = {node['id']: node['title'] for node in self.nodes()}
            result = []
            for index, identifier in enumerate(settings.get('auto_pool_ids') or []):
                info = data.get(f'candidate-{index}')
                state = 'pending'
                delay = None
                if isinstance(info, dict) and type(info.get('last_try_time')) is int and info['last_try_time'] > 0 and type(info.get('alive')) is bool:
                    state = 'stale' if time.time() - info['last_try_time'] > 180 else ('ok' if info['alive'] else 'failed')
                    value = info.get('delay')
                    if state == 'ok' and type(value) is int and 0 <= value <= 86400000:
                        delay = value
                checked = info.get('last_try_time') if isinstance(info, dict) and type(info.get('last_try_time')) is int and info['last_try_time'] > 0 else None
                result.append({'id': identifier, 'title': titles.get(identifier, '节点已变更'), 'state': state, 'delay_ms': delay, 'last_checked': checked})
            return {'state': 'ok', 'nodes': result}

    def acme_settings(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (ACME_KEY,)).fetchone()
        return json.loads(row['value']) if row else _default_acme_settings()

    def save_acme_settings(self, settings):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (ACME_KEY, json.dumps(settings, ensure_ascii=False)))

    def acme_status(self):
        settings = self.acme_settings()
        error = settings.get('error') or ''
        cert_path = (settings.get('cert_path') or '').strip()
        enabled = bool(settings.get('enabled'))
        days_left = settings.get('days_left')
        if cert_path and Path(cert_path).is_file():
            live = days_until_expiry(cert_path)
            if live is not None:
                days_left = round(live, 1)
        if error:
            state = 'error'
        elif enabled and cert_path and Path(cert_path).is_file():
            state = 'ok'
        elif enabled:
            state = 'pending'
        else:
            state = ''
        return {
            'enabled': enabled,
            'email': settings.get('email') or '',
            'domain': settings.get('domain') or '',
            'days_left': days_left,
            'cert_path': cert_path,
            'key_path': settings.get('key_path') or '',
            'error': error,
            'state': state,
            'challenge': settings.get('challenge') or 'auto',
            'challenge_effective': settings.get('challenge_effective') or '',
            'renew_days': int(os.environ.get('HOPWORLD_ACME_RENEW_DAYS', '3')),
        }

    def configure_acme(self, enabled, email=None, challenge=None):
        settings = self.acme_settings()
        settings['enabled'] = bool(enabled)
        if email is not None:
            settings['email'] = validate_acme_email(email) if str(email).strip() else ''
        if challenge is not None:
            settings['challenge'] = validate_acme_challenge(challenge)
        if settings['enabled']:
            if not settings.get('email'):
                raise ValueError('请填写 ACME 邮箱')
            domain = (self.local_node_settings().get('public_host') or '').strip()
            if not domain or is_ip_host(domain):
                raise ValueError('ACME 需要域名公网地址')
            try:
                view = sync_certificate(
                    self.data_dir,
                    domain,
                    settings['email'],
                    True,
                    settings.get('challenge', 'auto'),
                )
                settings.update(view)
                settings['error'] = ''
            except ValueError as exc:
                settings['error'] = short_acme_error(str(exc))
                settings['days_left'] = None
                settings['cert_path'] = ''
                settings['key_path'] = ''
                settings['domain'] = domain
        self.save_acme_settings(settings)
        return self.acme_status()

    def sync_acme_certificate(self):
        settings = self.acme_settings()
        if not settings.get('enabled'):
            return False
        domain = (self.local_node_settings().get('public_host') or '').strip()
        if not domain or is_ip_host(domain):
            settings['error'] = '需要域名'
            self.save_acme_settings(settings)
            return False
        try:
            view = sync_certificate(
                self.data_dir,
                domain,
                settings['email'],
                True,
                settings.get('challenge', 'auto'),
            )
            settings.update(view)
            settings['error'] = ''
        except ValueError as exc:
            settings['error'] = short_acme_error(str(exc))
            self.save_acme_settings(settings)
            return False
        self.save_acme_settings(settings)
        return True

    def restore_local_node(self):
        settings = self.local_node_settings()
        if settings.get('enabled'):
            if settings.get('egress_mode') == 'auto':
                settings['proxy_pool'], settings['auto_pool_ids'] = self._auto_pool_entries(settings)
            settings = self.xray.ensure_credentials(settings)
            if settings.get('ddns_enabled'):
                settings, _ = self.refresh_ddns(settings)
            elif settings.get('auto_public_host'):
                settings, _ = self.refresh_auto_public_host(settings)
            self.save_local_node_settings(settings)
            self.xray.apply(settings)

    def ikev2_settings(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (IKEV2_KEY,)).fetchone()
        settings = json.loads(row['value']) if row else self.ikev2.default_settings(os.environ.get('HOPWORLD_PUBLIC_HOST', ''))
        if not settings.get('public_host') and os.environ.get('HOPWORLD_PUBLIC_HOST'):
            try:
                settings['public_host'] = validate_public_host(os.environ['HOPWORLD_PUBLIC_HOST'])
            except ValueError:
                pass
        return settings

    def save_ikev2_settings(self, settings):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (IKEV2_KEY, json.dumps(settings, ensure_ascii=False)))

    def configure_ikev2(self, enabled, public_host=None, remote_id=None, rotate_psk=False, psk=None):
        if not isinstance(enabled, bool):
            raise ValueError('IKEv2 设置无效')
        settings = self.ikev2_settings()
        if public_host is not None:
            if not isinstance(public_host, str):
                raise ValueError('公网地址无效')
            settings['public_host'] = validate_public_host(public_host) if public_host.strip() else ''
        if remote_id is not None:
            settings['remote_id'] = validate_remote_id(remote_id)
        custom_psk = None
        if psk is not None and isinstance(psk, str) and psk.strip() and not rotate_psk:
            custom_psk = validate_psk(psk)
        if enabled:
            if not settings.get('public_host'):
                raise ValueError('请先填写公网地址')
            settings = self.ikev2.ensure_credentials(
                settings, rotate_psk=bool(rotate_psk), new_psk=custom_psk,
            )
            settings['enabled'] = True
            self.save_ikev2_settings(settings)
            self.ikev2.apply(settings)
        else:
            settings['enabled'] = False
            if custom_psk is not None:
                settings['psk'] = custom_psk
            elif rotate_psk:
                settings = self.ikev2.ensure_credentials(settings, rotate_psk=True)
            self.save_ikev2_settings(settings)
            self.ikev2.apply(settings)
        return self.ikev2.public_view(settings)

    def ikev2_status(self):
        return self.ikev2.public_view(self.ikev2_settings())

    def restore_ikev2(self):
        settings = self.ikev2_settings()
        if settings.get('enabled'):
            settings = self.ikev2.ensure_credentials(settings)
            self.save_ikev2_settings(settings)
            self.ikev2.apply(settings)


def make_handler(store, password):
    probe_results = {}
    probe_lock = threading.Lock()
    probe_slots = threading.BoundedSemaphore(4)
    jobs = {'update': {'running': False, 'done': 0, 'total': 0}, 'probe': {'running': False, 'done': 0, 'total': 0}}
    jobs_lock = threading.Lock()
    cancellations = {'update': threading.Event(), 'probe': threading.Event()}

    def start_job(kind, target='google', groups=None):
        if kind == 'probe' and (not isinstance(target, str) or target not in TARGETS): raise ValueError('测试目标无效')
        with jobs_lock:
            if jobs[kind]['running']: raise ValueError('任务已经在运行')
            if kind == 'update':
                items = [source['id'] for source in store.sources()]
            else:
                nodes = store.nodes()
                allowed = {f"source:{source['id']}" for source in store.sources()} | {'manual', 'local'}
                if groups is not None:
                    if not isinstance(groups, list) or not all(isinstance(group, str) for group in groups) or not set(groups).issubset(allowed):
                        raise ValueError('订阅组无效')
                    selected = set(groups)
                else:
                    selected = allowed
                items = [node['id'] for node in nodes if selected.intersection(node['groups'])]
            if not items: raise ValueError('没有可处理的订阅或已选节点')
            cancellations[kind].clear()
            jobs[kind] = {'running': True, 'done': 0, 'total': len(items), 'failed': 0}
            if kind == 'probe':
                jobs[kind]['target'] = TARGETS[target][0]
                with probe_lock:
                    for identifier in items:
                        if probe_results.get(identifier, {}).get('status') != 'testing':
                            probe_results.pop(identifier, None)

        def run_item(item):
            if cancellations[kind].is_set(): return
            failed = False
            if kind == 'update':
                try:
                    store.refresh(item)
                    store.reconcile_auto_pool()
                except ValueError: failed = True
            else:
                owns_probe = False
                try:
                    link = store.node_link(item)
                    with probe_lock:
                        if probe_results.get(item, {}).get('status') == 'testing':
                            raise ValueError('节点正在测试')
                        probe_results[item] = {'status': 'testing', 'detail': '正在测试'}
                        owns_probe = True
                    with probe_slots: result = probe(link, target=target)
                except Exception:
                    result = {'status': 'failed', 'detail': '测试失败'}
                result['target'] = TARGETS[target][0]
                if owns_probe:
                    with probe_lock: probe_results[item] = result
                failed = result['status'] != 'ok'
            with jobs_lock:
                jobs[kind]['done'] += 1
                if failed: jobs[kind]['failed'] += 1

        def worker():
            try:
                if kind == 'probe':
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        list(pool.map(run_item, items))
                else:
                    for item in items:
                        if cancellations[kind].is_set(): break
                        run_item(item)
            finally:
                with jobs_lock:
                    jobs[kind]['running'] = False
                    jobs[kind]['cancelled'] = cancellations[kind].is_set()
        threading.Thread(target=worker, daemon=True).start()


    def start_probe(identifier, target='google'):
        if not isinstance(identifier, str): raise ValueError('节点不存在')
        if not isinstance(target, str) or target not in TARGETS: raise ValueError('测试目标无效')
        link = store.node_link(identifier)
        with probe_lock:
            if probe_results.get(identifier, {}).get('status') == 'testing': raise ValueError('节点正在测试')
            if not probe_slots.acquire(blocking=False): raise ValueError('测试任务已满')
            probe_results[identifier] = {'status': 'testing', 'detail': '正在测试'}
        def worker():
            try:
                result = probe(link, target=target)
            except Exception:
                result = {'status': 'failed', 'detail': '测试失败'}
            finally:
                probe_slots.release()
            result['target'] = TARGETS[target][0]
            with probe_lock: probe_results[identifier] = result
        threading.Thread(target=worker, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Subscription URLs and credentials must not enter access logs.

        def reply(self, status, body, content_type='application/json; charset=utf-8', headers=None):
            data = body if isinstance(body, bytes) else body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            for key, value in (headers or {}).items(): self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def json(self, status, obj):
            self.reply(status, json.dumps(obj, ensure_ascii=False))

        def authorized(self):
            auth = self.headers.get('Authorization', '')
            try:
                kind, encoded = auth.split(' ', 1)
                decoded = base64.b64decode(encoded, validate=True).decode('utf-8')
                user, supplied = decoded.split(':', 1)
                ok = kind.lower() == 'basic' and hmac.compare_digest(user.encode(), b'admin') and hmac.compare_digest(supplied.encode(), password.encode())
            except (ValueError, UnicodeError):
                ok = False
            if not ok:
                self.reply(401, 'Authentication required', 'text/plain; charset=utf-8', {'WWW-Authenticate': 'Basic realm="HopWorld Router", charset="UTF-8"'})
            return ok

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith('/s/'):
                rest = path.removeprefix('/s/')
                if '/' in rest:
                    candidate, sub = rest.split('/', 1)
                else:
                    candidate, sub = rest, ''
                if not hmac.compare_digest(candidate.encode(), store.token().encode()):
                    return self.reply(404, 'Not found', 'text/plain; charset=utf-8')
                if sub == 'ikev2.mobileconfig':
                    try:
                        blob = build_mobileconfig(store.ikev2_settings(), store.ikev2.data_dir)
                    except ValueError as exc:
                        return self.reply(400, str(exc), 'text/plain; charset=utf-8')
                    return self.reply(
                        200,
                        blob,
                        'application/x-apple-aspen-config',
                        {
                            'Content-Disposition': 'attachment; filename="hopworld-ikev2.mobileconfig"',
                        },
                    )
                if sub:
                    return self.reply(404, 'Not found', 'text/plain; charset=utf-8')
                return self.reply(200, store.subscription(), 'text/plain; charset=utf-8')
            if not self.authorized(): return
            if path == '/': return self.reply(200, (STATIC / 'index.html').read_bytes(), 'text/html; charset=utf-8', {'Content-Security-Policy': "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
            if path == '/app.js': return self.reply(200, (STATIC / 'app.js').read_bytes(), 'application/javascript; charset=utf-8')
            if path == '/qrcode.js': return self.reply(200, (STATIC / 'qrcode.js').read_bytes(), 'application/javascript; charset=utf-8')
            if path == '/style.css': return self.reply(200, (STATIC / 'style.css').read_bytes(), 'text/css; charset=utf-8')
            if path == '/logo.png':
                logo = STATIC / 'logo.png'
                if logo.is_file():
                    return self.reply(200, logo.read_bytes(), 'image/png', {'Cache-Control': 'public, max-age=604800'})
            if path == '/api/status':
                try:
                    store.sync_public_endpoint()
                except ValueError:
                    pass
                sources = store.sources()
                for source in sources:
                    source['count'] = len(json.loads(source.pop('links')))
                nodes = store.nodes()
                with probe_lock:
                    for node in nodes: node['probe'] = probe_results.get(node['id'])
                with jobs_lock: job_status = {key: dict(value) for key, value in jobs.items()}
                last = job_status['probe']
                if not last['running'] and last.get('target'):
                    def rank(node):
                        result = node.get('probe') or {}
                        matches = result.get('target') == last['target']
                        passed = matches and result.get('status') == 'ok'
                        latency = result.get('latency_ms')
                        return (0 if passed else 1, latency if passed and isinstance(latency, (int, float)) else float('inf'), node['title'].casefold(), node['id'])
                    nodes.sort(key=rank)
                return self.json(200, {
                    'sources': sources,
                    'nodes': nodes,
                    'jobs': job_status,
                    'token': store.token(),
                    'public_urls': build_public_urls(store),
                    'local_node': store.local_node_status(),
                    'acme': store.acme_status(),
                    'ikev2': store.ikev2_status(),
                })
            if path == '/api/local/auto-status':
                return self.json(200, store.auto_observation_status())
            if path == '/api/local/dns-hints':
                if not self.authorized():
                    return
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)

                def first(key):
                    values = query.get(key)
                    return values[0] if values else None

                hints = store.preview_dns_hints(
                    first('public_host'),
                    first('auto_public_host'),
                    first('ddns_enabled'),
                    first('public_host_prefer'),
                )
                return self.json(200, hints)
            if path == '/api/local/dns-verify':
                if not self.authorized():
                    return
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)

                def first(key):
                    values = query.get(key)
                    return values[0] if values else None

                force = first('force') == '1'
                settings = store.preview_local_node_settings(
                    first('public_host'),
                    first('auto_public_host'),
                    first('ddns_enabled'),
                    first('public_host_prefer'),
                )
                verify = store.dns_verify_for_settings(settings, force=force, probe_expected=True)
                return self.json(200, verify)
            if path == '/api/ikev2/live':
                return self.json(200, {'ikev2': store.ikev2_status()})
            if path == '/api/ikev2/mobileconfig':
                try:
                    blob = build_mobileconfig(store.ikev2_settings(), store.ikev2.data_dir)
                except ValueError as exc:
                    return self.json(400, {'error': str(exc)})
                headers = {
                    'Content-Disposition': 'attachment; filename="hopworld-ikev2.mobileconfig"',
                    'Content-Type': 'application/x-apple-aspen-config',
                }
                return self.reply(200, blob, 'application/x-apple-aspen-config', headers)
            self.reply(404, 'Not found', 'text/plain; charset=utf-8')

        def do_POST(self):
            if not self.authorized(): return
            # Same-origin CSRF check: browser Origin must match this request's Host.
            origin = self.headers.get('Origin', '')
            host = self.headers.get('Host', '')
            if not host or origin not in (f'http://{host}', f'https://{host}'):
                return self.json(403, {'error': '访问来源不受信任'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > MAX_BODY: raise ValueError('请求过大')
                data = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(data, dict): raise ValueError('请求格式错误')
                path = urlsplit(self.path).path
                if path == '/api/sources':
                    source_id = store.add(data.get('name', ''), data.get('url', ''))
                    try: store.refresh(source_id)
                    except ValueError: pass
                elif path == '/api/refresh': store.refresh(int(data['id']))
                elif path == '/api/delete':
                    if not store.delete(int(data['id'])): raise ValueError('订阅不存在')
                elif path == '/api/rotate': store.rotate()
                elif path == '/api/manual': store.add_manual(data.get('content', ''))
                elif path == '/api/manual/delete':
                    if not store.remove_manual(data.get('id', '')): raise ValueError('节点不存在')
                elif path == '/api/node': store.set_node_enabled(data.get('id'), data.get('enabled'))
                elif path == '/api/probe': start_probe(data.get('id'), data.get('target', 'google'))
                elif path == '/api/local/discover':
                    ports = data.get('ports')
                    return self.json(200, {'proxies': discover() if ports is None else discover(ports)})
                elif path == '/api/local-node':
                    return self.json(200, {'ok': True, 'local_node': store.configure_local_node(
                        bool(data.get('enabled')), data.get('public_host'), data.get('port'), data.get('name'),
                        data.get('auto_public_host'), data.get('public_host_prefer'), data.get('ddns_enabled'),
                        data.get('acme_enabled'), data.get('acme_email'), data.get('acme_challenge'),
                        data.get('egress_mode'), data.get('local_proxy'), data.get('auto_target'))})
                elif path == '/api/ikev2':
                    return self.json(200, {'ok': True, 'ikev2': store.configure_ikev2(
                        bool(data.get('enabled')), data.get('public_host'), data.get('remote_id'),
                        bool(data.get('rotate_psk')), data.get('psk'))})
                elif path == '/api/ikev2/sync':
                    store.ikev2._run_helper('sync-clients')
                    with store.ikev2._runtime_lock:
                        store.ikev2._runtime_cache = None
                    return self.json(200, {'ok': True, 'ikev2': store.ikev2_status()})
                elif path in ('/api/batch/update', '/api/batch/probe'): start_job(path.rsplit('/', 1)[1], data.get('target', 'google'), data.get('groups'))
                elif path in ('/api/batch/update/stop', '/api/batch/probe/stop'):
                    cancellations[path.split('/')[-2]].set()
                else: return self.json(404, {'error': '请求不存在'})
                if path in ('/api/sources', '/api/refresh', '/api/delete', '/api/manual', '/api/manual/delete', '/api/node'):
                    store.reconcile_auto_pool()
                self.json(200, {'ok': True})
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                message = str(exc) if isinstance(exc, ValueError) and str(exc) else '操作失败，请检查输入或订阅状态'
                self.json(400, {'error': message})
    return Handler


def main():
    password = os.environ.get('HOPWORLD_ADMIN_PASSWORD', '')
    if len(password) < 16 or ':' in password:
        raise SystemExit('HOPWORLD_ADMIN_PASSWORD 必须至少 16 字符，且不能包含冒号')
    host = os.environ.get('HOPWORLD_HOST', '127.0.0.1')
    port = int(os.environ.get('HOPWORLD_PORT', '8080'))
    data_dir = Path(os.environ.get('HOPWORLD_DATA', './data'))
    store = Store(data_dir / 'router.db')
    server = ThreadingHTTPServer((host, port), make_handler(store, password))
    stopped = threading.Event()

    def restore_background():
        try:
            store.restore_local_node()
        except ValueError as exc:
            print(f'本地网络节点未启动: {exc}', flush=True)
        try:
            store.sync_public_endpoint()
        except ValueError:
            pass
        try:
            store.sync_acme_certificate()
        except ValueError:
            pass
        try:
            store.restore_ikev2()
        except ValueError as exc:
            print(f'IKEv2 未启动: {exc}', flush=True)

    threading.Thread(target=restore_background, daemon=True).start()

    def update_loop():
        while not stopped.wait(3600):
            for source in store.sources():
                if stopped.is_set(): return
                try:
                    store.refresh(source['id'])
                    store.reconcile_auto_pool()
                except ValueError: pass

    threading.Thread(target=update_loop, daemon=True).start()

    def public_host_loop():
        interval = max(10, int(os.environ.get('HOPWORLD_PUBLIC_HOST_POLL', '30')))
        while not stopped.wait(interval):
            try:
                store.sync_public_endpoint()
            except ValueError:
                pass

    threading.Thread(target=public_host_loop, daemon=True).start()

    def acme_loop():
        interval = max(3600, int(os.environ.get('HOPWORLD_ACME_CHECK_SECONDS', '43200')))
        while not stopped.wait(interval):
            try:
                store.sync_acme_certificate()
            except ValueError:
                pass

    threading.Thread(target=acme_loop, daemon=True).start()
    display_host = '127.0.0.1' if host in ('0.0.0.0', '::') else host
    print(f'HopWorld Router: http://{display_host}:{server.server_port}', flush=True)

    def shutdown(*_args):
        stopped.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try: server.serve_forever()
    finally:
        stopped.set()
        store.xray.stop()
        server.server_close()


if __name__ == '__main__':
    main()
