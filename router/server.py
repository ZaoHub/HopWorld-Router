"""Loopback-only subscription manager; run behind an authenticated secure tunnel."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hmac
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPException, HTTPSConnection
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from router.subscription import MAX_SUBSCRIPTION_BYTES, decode_subscription, encode_subscription, parse_subscription, upstream_node_id, vmess_fields
from router.probe import HYSTERIA_SCHEMES, LIGHT_CLIENT_SCHEMES, TARGETS, TEST_URL, UnsupportedNode, outbound, probe, probe_light
from router.local_proxy_discovery import discover
from router import autostart, incremental_update, runtime_layout, system_update
from router.database_backup import data_lock, snapshot
from router.node_export import EXPORT_FORMATS, export_client_config
from router.network_security import API as SECURITY_API, NetworkSecurity
from router.security_dns import build_secure_dns_config, require_sing_box_version
from router.admin_auth import (ADMIN_ACCOUNT_KEY, ADMIN_PASSWORD_KEY, DEFAULT_PASSWORD, MIN_PASSWORD_LENGTH,
                               matches_password, password_record)
from router.system_info import CORES, core_inventory, system_info
from router.core_install import (_Cancelled, _Paused, bundled_binary, cancel_download, core_client_binary,
                                 missing_core_client, pause_download, uninstall_core)
from router.update_center import UpdateCenter
from router.local_node import (
    DEFAULT_LOCAL_PORT, DEFAULT_NODE_NAME, LOCAL_EGRESS_SOCKS_PORT, native_hysteria_port,
    _protocol_label, quic_ports, validate_node_name, validate_public_host,
)
from router.public_endpoint import detect_public_host, detect_public_ip, direct_address_status, is_ip_host, local_route_address
from router.ddns import publish_ddns
from router.acme import (ACME_KEY, CERTIFICATE_PEM, HYSTERIA_CERTIFICATE_NAME, IP_CERTIFICATES_KEY, SHORTLIVED_RENEW_SECONDS,
                         cert_paths, days_until_expiry,
                         ensure_ip_certificate, ensure_management_https_certificate, hysteria_local_certificate,
                         import_certificate, ip_certificate_paths, ip_certificate_readiness,
                         management_https_certificate_status, management_https_host, short_acme_error,
                         stage_certificate, sync_certificate,
                         validate_acme_challenge, validate_acme_email, validate_ip_challenge)
from router.dns_hints import build_dns_hints
from router.dns_verify import _lookup_addresses, verify_dns_resolution
from router.public_urls import build_public_urls
from router.singbox_service import SingboxService
from router.hysteria_service import HysteriaService
from router.independent_core import (
    COMPATIBLE_PUBLISHERS, IndependentCore, PUBLISHERS, profile_credentials, publisher_bind, publisher_core, publisher_ports,
    render_publisher,
)
from router.xray_service import XrayService
from router.traffic_history import SAMPLE_SECONDS, TrafficHistory, collect as collect_traffic
from router.publisher_traffic import PublisherTraffic, collect as collect_publisher_traffic
from router.ikev2_service import Ikev2Service, build_mobileconfig, validate_psk, validate_remote_id
from router.management_access import ManagementAccess
from router.public_subscription import DEFAULT_PORT as PUBLIC_SUBSCRIPTION_PORT, PublicSubscription
from router.relay import RelayClient, validate_relay

STATIC = Path(__file__).parent / "static"
MAX_BODY = 16384
LOCAL_NODE_KEY = 'local_node'
IKEV2_KEY = 'ikev2'
UI_SETTINGS_KEY = 'management_ui'
PROBE_TARGET_KEY = 'node_probe_target'
ACCESS_SETTINGS_KEY = 'management_access'
PUBLIC_SUBSCRIPTION_KEY = 'public_subscription'
CORE_PROFILES_KEY = 'independent_core_profiles'
LEGACY_CORE_FLAGS = {'xray-core': 'enabled', 'sing-box': 'legacy_singbox_enabled',
                     'hysteria2': 'native_hysteria_enabled'}
RELAY_KEY = 'frp_relay'
UI_DEFAULTS = {'name': 'HopWorld-Router', 'timezone': 'browser'}
UI_TIMEZONES = {'browser', 'Asia/Shanghai', 'UTC', 'Asia/Tokyo', 'Europe/London', 'America/Los_Angeles'}
SOURCE_UPDATE_INTERVAL = 3600
SESSION_COOKIE = 'hopworld_session'
SESSION_SECONDS = 8 * 3600
PAGE_HEADERS = {'Content-Security-Policy': "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}


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


def validate_subscription_domain(host):
    if not isinstance(host, str) or not host.strip():
        raise ValueError('请先填写订阅域名，再检查地址或保存')
    try:
        domain = validate_public_host(host)
        if is_ip_host(domain):
            raise ValueError
    except ValueError:
        raise ValueError('请填写域名，例如 proxy.example.com，端口在下方单独设置') from None
    return domain


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _public_subscription_socket(destination, timeout, source_address=None):
    addresses = socket.getaddrinfo(*destination, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    for family, _, _, _, endpoint in addresses:
        address = ipaddress.ip_address(endpoint[0])
        if family == socket.AF_INET6:
            if endpoint[3]:
                raise ValueError('订阅地址必须解析到公网 IP')
            if address.ipv4_mapped is not None:
                address = address.ipv4_mapped
            elif (address not in ipaddress.IPv6Network('2000::/3') or
                  address.sixtofour is not None or address.teredo is not None):
                raise ValueError('订阅地址必须解析到公网 IP')
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise ValueError('订阅地址必须解析到公网 IP')
    deadline = time.monotonic() + timeout
    last_error = OSError('订阅服务器没有可用地址')
    for family, kind, protocol, _, endpoint in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        peer = None
        try:
            peer = socket.socket(family, kind, protocol)
            peer.settimeout(remaining)
            if source_address is not None:
                peer.bind(source_address)
            # Connect to the verified address while HTTPS retains the original hostname for TLS.
            peer.connect(endpoint)
            return peer
        except OSError as exc:
            if peer is not None:
                peer.close()
            last_error = exc
    raise last_error


class PublicSubscriptionHTTPS(HTTPSHandler):
    def https_open(self, req):
        def connection(*args, **kwargs):
            client = HTTPSConnection(*args, **kwargs)
            client._create_connection = _public_subscription_socket
            return client
        return self.do_open(connection, req, context=self._context)


class Store:
    def __init__(self, path, xray=None, ikev2=None, singbox=None):
        self._local_lock = threading.RLock()
        self._hysteria_ip_lock = threading.Lock()
        self.path = str(path)
        self.data_dir = Path(self.path).parent
        self.xray = xray or XrayService(self.data_dir)
        self.singbox = singbox or SingboxService(self.data_dir)
        self.hysteria = HysteriaService(self.data_dir)
        self.independent = {core_id: IndependentCore(self.data_dir, core_id) for core_id in PUBLISHERS}
        self.relay = RelayClient(self.data_dir)
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
            db.execute('CREATE TABLE IF NOT EXISTS upstream_names (id TEXT PRIMARY KEY, name TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS certificate_history (
                id INTEGER PRIMARY KEY, domain TEXT NOT NULL, action TEXT NOT NULL,
                detail TEXT NOT NULL, timestamp INTEGER NOT NULL)''')
            db.execute('BEGIN IMMEDIATE')
            source_columns = {row['name'] for row in db.execute('PRAGMA table_info(sources)')}
            for column, definition in (
                ('enabled', 'INTEGER NOT NULL DEFAULT 1'),
                ('auto_update', 'INTEGER NOT NULL DEFAULT 1'),
                ('update_interval', f'INTEGER NOT NULL DEFAULT {SOURCE_UPDATE_INTERVAL}'),
                ('checked', 'INTEGER'),
                ('refresh_id', 'TEXT'),
                ('etag', 'TEXT'),
                ('last_modified', 'TEXT'),
                ('retained_links', "TEXT NOT NULL DEFAULT '[]'"),
            ):
                if column not in source_columns:
                    db.execute(f'ALTER TABLE sources ADD COLUMN {column} {definition}')
            db.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', ('token', secrets.token_urlsafe(32)))
            if db.execute('SELECT 1 FROM settings WHERE key=?', (LOCAL_NODE_KEY,)).fetchone() is None:
                initial = self.xray.default_settings(os.environ.get('HOPWORLD_PUBLIC_HOST', ''))
                db.execute('INSERT INTO settings VALUES (?, ?)', (LOCAL_NODE_KEY, json.dumps(initial, ensure_ascii=False)))
            if db.execute('SELECT 1 FROM settings WHERE key=?', (IKEV2_KEY,)).fetchone() is None:
                initial = self.ikev2.default_settings(os.environ.get('HOPWORLD_PUBLIC_HOST', ''))
                db.execute('INSERT INTO settings VALUES (?, ?)', (IKEV2_KEY, json.dumps(initial, ensure_ascii=False)))
            if db.execute('SELECT 1 FROM settings WHERE key=?', (ACME_KEY,)).fetchone() is None:
                db.execute('INSERT INTO settings VALUES (?, ?)', (ACME_KEY, json.dumps(_default_acme_settings(), ensure_ascii=False)))
            if db.execute("INSERT OR IGNORE INTO settings VALUES ('upstream_identity_v1', 'true')").rowcount:
                self._migrate_upstream_ids(db)
        self.traffic_history = TrafficHistory(self.connect)
        self.publisher_traffic = PublisherTraffic(self.connect)
        self.security = NetworkSecurity(self)
        self.updates = UpdateCenter(self)

    def _migrate_upstream_ids(self, db):
        manual = [row['link'] for row in db.execute('SELECT link FROM manual_nodes ORDER BY rowid')]
        sources = [json.loads(row['links']) for row in db.execute('SELECT links FROM sources')]
        ids = {node_id(link): upstream_node_id(link) for links in [*sources, manual] for link in links}
        if not ids:
            return
        db.execute('DELETE FROM manual_nodes')
        db.executemany('INSERT OR IGNORE INTO manual_nodes VALUES (?, ?)',
                       ((ids[node_id(link)], link) for link in manual))
        disabled = [row['id'] for row in db.execute('SELECT id FROM disabled_nodes') if row['id'] in ids]
        # Keep legacy disables for any identical URI also used by a local publisher.
        db.executemany('INSERT OR IGNORE INTO disabled_nodes VALUES (?)', ((ids[old],) for old in disabled))
        for row in db.execute('SELECT key,value FROM settings WHERE key IN (?,?)', (LOCAL_NODE_KEY, CORE_PROFILES_KEY)).fetchall():
            settings = json.loads(row['value'])
            profiles = [settings] if row['key'] == LOCAL_NODE_KEY else settings.values()
            for profile in profiles:
                if profile.get('upstream_id') in ids:
                    profile['upstream_id'] = ids[profile['upstream_id']]
                if 'auto_pool_ids' in profile:
                    profile['auto_pool_ids'] = [ids.get(old, old) for old in profile['auto_pool_ids']]
            db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(settings, ensure_ascii=False), row['key']))

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
            rows = [dict(row) for row in db.execute(
                'SELECT id,name,url,updated,error,links,enabled,auto_update,update_interval,checked FROM sources ORDER BY id'
            )]
        now = int(time.time())
        for row in rows:
            row['enabled'] = bool(row['enabled'])
            row['auto_update'] = bool(row['auto_update'])
            checked = row.pop('checked')
            last = checked if checked is not None else row['updated']
            row['next_update'] = None
            if row['enabled'] and row['auto_update']:
                row['next_update'] = last + row['update_interval'] if last is not None else now
        return rows

    @staticmethod
    def _source_fields(name, url, enabled, auto_update, update_interval):
        if not isinstance(name, str) or not isinstance(url, str):
            raise ValueError('名称或地址无效')
        name, url = name.strip(), url.strip()
        if not name or len(name) > 100 or len(url) > 4096 or any(char.isspace() or ord(char) < 32 for char in url):
            raise ValueError('名称或地址无效')
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise ValueError('订阅地址无效') from None
        if parts.scheme != 'https' or not parts.hostname or parts.username is not None or parts.password is not None or '#' in url:
            raise ValueError('订阅地址必须为 HTTPS 地址，且不能包含用户名、密码或片段')
        if port is not None and not 1 <= port <= 65535:
            raise ValueError('订阅地址端口无效')
        if type(enabled) is not bool or type(auto_update) is not bool:
            raise ValueError('订阅开关无效')
        if type(update_interval) is not int or not 300 <= update_interval <= 604800:
            raise ValueError('更新周期必须在 5 分钟到 7 天之间')
        return name, url, enabled, auto_update, update_interval

    def add(self, name, url, enabled=True, auto_update=True, update_interval=SOURCE_UPDATE_INTERVAL):
        fields = self._source_fields(name, url, enabled, auto_update, update_interval)
        with self.connect() as db:
            return db.execute('INSERT INTO sources(name,url,enabled,auto_update,update_interval) VALUES (?,?,?,?,?)', fields).lastrowid

    def update_source(self, source_id, settings):
        with self._local_lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
            if row is None:
                raise ValueError('订阅不存在')
            defaults = {
                'name': row['name'], 'url': row['url'], 'enabled': bool(row['enabled']),
                'auto_update': bool(row['auto_update']), 'update_interval': row['update_interval'],
            }
            if not settings or settings.keys() - defaults.keys():
                raise ValueError('订阅设置无效')
            fields = self._source_fields(**(defaults | settings))
            url_changed = fields[1] != row['url']
            enabled_changed = fields[2] != defaults['enabled']
            db.execute('UPDATE sources SET name=?,url=?,enabled=?,auto_update=?,update_interval=? WHERE id=?', (*fields, source_id))
            if url_changed:
                db.execute("""UPDATE sources SET retained_links=links,updated=NULL,error=NULL,checked=NULL,
                            refresh_id=NULL,etag=NULL,last_modified=NULL WHERE id=?""", (source_id,))
            elif enabled_changed:
                db.execute('UPDATE sources SET refresh_id=NULL WHERE id=?', (source_id,))

    def due_sources(self):
        with self.connect() as db:
            return [row['id'] for row in db.execute(
                'SELECT id FROM sources WHERE enabled=1 AND auto_update=1 AND COALESCE(checked,updated,-update_interval)+update_interval<=? ORDER BY id',
                (int(time.time()),),
            )]

    def delete(self, source_id):
        with self.connect() as db:
            return db.execute('DELETE FROM sources WHERE id=?', (source_id,)).rowcount > 0

    def refresh(self, source_id, automatic=False):
        refresh_id = secrets.token_hex(16)
        with self.connect() as db:
            now = int(time.time())
            query = 'UPDATE sources SET checked=?,refresh_id=? WHERE id=? AND enabled=1'
            params = (now, refresh_id, source_id)
            if automatic:
                query += ' AND auto_update=1 AND COALESCE(checked,updated,-update_interval)+update_interval<=?'
                params += (now,)
            if not db.execute(query, params).rowcount:
                raise ValueError('订阅不存在、已停用或无需更新')
            source = db.execute(
                'SELECT url,links,retained_links,error,etag,last_modified FROM sources WHERE id=?', (source_id,)
            ).fetchone()
        headers = {'User-Agent': 'HopWorld-Router/1.0', 'Accept': 'text/plain, */*'}
        conditions = {'If-None-Match': source['etag'], 'If-Modified-Since': source['last_modified']}
        headers.update({name: value for name, value in conditions.items() if value})
        not_modified = False
        try:
            request = Request(source['url'], headers=headers)
            try:
                opener = build_opener(ProxyHandler({}), NoRedirects(), PublicSubscriptionHTTPS())
                with opener.open(request, timeout=15) as response:
                    body = response.read(MAX_SUBSCRIPTION_BYTES + 1)
                    metadata = getattr(response, 'headers', {})
                    validators = [metadata.get(name) for name in ('ETag', 'Last-Modified')]
            except HTTPError as exc:
                with exc:
                    if exc.code != 304 or not any(conditions.values()):
                        raise
                not_modified = True
            if not not_modified:
                links, skipped = parse_subscription(body)
                links.extend(json.loads(source['retained_links']))
                unique = {}
                for link in links:
                    unique.setdefault(upstream_node_id(link), link)
                links = list(unique.values())
        except (OSError, HTTPException, ValueError):
            # Never include the URL or server response in logs or browser errors.
            with self.connect() as db:
                db.execute('UPDATE sources SET error=? WHERE id=? AND refresh_id=?', ('更新失败，请检查订阅地址、格式和网络', source_id, refresh_id))
            raise ValueError('更新失败，请检查订阅地址、格式和网络') from None
        if not_modified:
            warning = source['error'] if (source['error'] or '').startswith('已跳过 ') else None
            with self.connect() as db:
                if not db.execute('UPDATE sources SET error=? WHERE id=? AND refresh_id=?',
                                  (warning, source_id, refresh_id)).rowcount:
                    raise ValueError('订阅设置已变更，请重新更新')
            return len(json.loads(source['links']))
        warning = f'已跳过 {skipped} 个暂不支持的节点或参数' if skipped else None
        etag, modified = [
            value.strip() if isinstance(value, str) and re.fullmatch(r'[ -~]{1,1024}', value) else None
            for value in validators
        ]
        with self.connect() as db:
            if not db.execute(
                'UPDATE sources SET links=?,updated=?,error=?,etag=?,last_modified=? WHERE id=? AND refresh_id=?',
                (json.dumps(links, ensure_ascii=False), int(time.time()), warning, etag, modified, source_id, refresh_id),
            ).rowcount:
                raise ValueError('订阅设置已变更，请重新更新')
        return len(links)

    def update_prefs(self):
        prefs = {'auto_check': True, 'auto_apply': True}
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', ('project_update',)).fetchone()
        if not row:
            return prefs
        try:
            data = json.loads(row['value'])
        except (ValueError, TypeError):
            return prefs
        if isinstance(data, dict):
            if type(data.get('auto_check')) is bool:
                prefs['auto_check'] = data['auto_check']
            if type(data.get('auto_apply')) is bool:
                prefs['auto_apply'] = data['auto_apply']
        return prefs

    def save_update_prefs(self, auto_check, auto_apply):
        if type(auto_check) is not bool or type(auto_apply) is not bool:
            raise ValueError('更新设置无效')
        prefs = {'auto_check': auto_check, 'auto_apply': auto_apply}
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', ('project_update', json.dumps(prefs)))
        return prefs

    def management_ui_settings(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (UI_SETTINGS_KEY,)).fetchone()
        return json.loads(row['value']) if row else dict(UI_DEFAULTS)

    def probe_target(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (PROBE_TARGET_KEY,)).fetchone()
        return row['value'] if row and row['value'] in TARGETS else 'google'

    def admin_password_record(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (ADMIN_PASSWORD_KEY,)).fetchone()
        return row['value'] if row else ''

    def initialize_admin_password(self, value):
        record = password_record(value)
        with self.connect() as db:
            if not db.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', (ADMIN_PASSWORD_KEY, record)).rowcount:
                raise ValueError('管理员已完成初始化，请重新登录')

    def admin_account(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (ADMIN_ACCOUNT_KEY,)).fetchone()
        return tuple(json.loads(row['value'])) if row else None

    def save_admin_account(self, username, verifier):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (ADMIN_ACCOUNT_KEY, json.dumps([username, verifier])))

    def management_access_settings(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (ACCESS_SETTINGS_KEY,)).fetchone()
        if not row:
            return None
        try:
            value = json.loads(row['value'])
            if type(value['port']) is int and 1024 <= value['port'] <= 65535 and type(value['https']) is bool:
                return value
        except (KeyError, ValueError, TypeError):
            pass
        return None

    def save_management_access_settings(self, port, https, use_base):
        with self.connect() as db:
            if use_base:
                db.execute('DELETE FROM settings WHERE key=?', (ACCESS_SETTINGS_KEY,))
            else:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)',
                           (ACCESS_SETTINGS_KEY, json.dumps({'port': port, 'https': https})))

    def save_management_settings(self, data):
        if not isinstance(data, dict):
            raise ValueError('设置字段无效')
        allowed = {'name', 'timezone', 'auto_check', 'auto_apply', 'probe_target'}
        keys = set(data)
        if not keys or keys - allowed:
            raise ValueError('设置字段无效')
        current = self.management_ui_settings()
        if 'name' in data:
            name = data['name']
            if not isinstance(name, str) or name.strip() != current['name']:
                raise ValueError('平台名称不可修改')
        timezone = current['timezone']
        if 'timezone' in data:
            timezone = data['timezone']
            if not isinstance(timezone, str) or timezone not in UI_TIMEZONES:
                raise ValueError('界面时区无效')
        prefs = {key: data[key] for key in ('auto_check', 'auto_apply') if key in data}
        if any(type(value) is not bool for value in prefs.values()):
            raise ValueError('更新设置无效')
        rows = []
        if 'timezone' in data:
            rows.append((UI_SETTINGS_KEY, json.dumps({'name': current['name'], 'timezone': timezone}, ensure_ascii=False)))
        if prefs:
            rows.append(('project_update', json.dumps({**self.update_prefs(), **prefs})))
        probe_settings = {}
        if 'probe_target' in data:
            target = data['probe_target']
            if not isinstance(target, str) or target not in TARGETS:
                raise ValueError('测试目标无效')
            rows.append((PROBE_TARGET_KEY, target))
            probe_settings['probe_target'] = target
        if rows:
            with self.connect() as db:
                db.executemany('INSERT OR REPLACE INTO settings VALUES (?, ?)', rows)
        return {'name': current['name'], 'timezone': timezone, **prefs, **probe_settings}

    def token(self):
        with self.connect() as db:
            return db.execute('SELECT value FROM settings WHERE key=?', ('token',)).fetchone()[0]

    def public_subscription_settings(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (PUBLIC_SUBSCRIPTION_KEY,)).fetchone()
        return json.loads(row[0]) if row else {'enabled': False, 'domain': '', 'port': PUBLIC_SUBSCRIPTION_PORT}

    def save_public_subscription_settings(self, settings):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (PUBLIC_SUBSCRIPTION_KEY, json.dumps(settings)))

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

    def local_links(self, settings=None):
        with self._local_lock:
            settings = settings or self.local_node_settings()
            link = self.local_link(settings)
            links = [link, *self.singbox.links(settings)] if link else []
            if settings.get('native_hysteria_enabled') and self.xray.running() and (native := self.hysteria.link(settings)):
                links.append(native)
            profiles = self.independent_profiles()
            for profile_id, profile in profiles.items():
                if profile.get('enabled') and self._independent_links_available(profile):
                    links.extend(self.publisher_runtime(profile_id).links())
            return links

    @staticmethod
    def _independent_links_available(profile):
        return profile.get('tls_mode') not in ('ip_acme', 'management_ip') or (
            not profile.get('ip_certificate_pending') and profile.get('ip_certificate_expires', 0) > time.time()
        )

    def independent_profiles(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (CORE_PROFILES_KEY,)).fetchone()
        return json.loads(row['value']) if row else {}

    def _save_independent_profiles(self, profiles, *, legacy_settings=None, relay_settings=None):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (CORE_PROFILES_KEY, json.dumps(profiles)))
            if legacy_settings is not None:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)',
                           (LOCAL_NODE_KEY, json.dumps(legacy_settings, ensure_ascii=False)))
            if relay_settings is not None:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (RELAY_KEY, json.dumps(relay_settings)))

    def _legacy_profile(self, core_id, settings):
        flag = LEGACY_CORE_FLAGS.get(core_id)
        host = settings.get('public_host')
        if not flag or not host:
            return {}
        legacy = self.legacy_core(core_id)
        enabled = settings.get(flag, core_id == 'sing-box' and settings.get('enabled'))
        if not enabled and not legacy.running():
            return {}
        port = int(settings.get('port') or DEFAULT_LOCAL_PORT)
        profile = {
            'public_host': host, 'port': port, 'public_listener': True,
            'address_mode': 'auto_ip' if settings.get('auto_public_host') else 'fixed_ip' if is_ip_host(host) else 'domain',
            'address_family': {'ipv4_first': 'dual'}.get(settings.get('public_host_prefer'),
                                                      settings.get('public_host_prefer', 'ipv4')),
            'ddns': bool(settings.get('ddns_enabled')),
            'name': settings.get('name') or DEFAULT_NODE_NAME,
        }
        if is_ip_host(host):
            profile['address_family'] = 'ipv6' if ':' in host else 'ipv4'
        if core_id == 'xray-core':
            profile.update({key: settings[key] for key in ('uuid', 'private_key', 'public_key', 'short_id', 'dest', 'server_name')
                            if settings.get(key)})
        elif core_id == 'sing-box':
            profile['port'], profile['tuic_port'] = quic_ports(port)
            if 'legacy_quic_protocols' in settings:
                profile['quic_protocols'] = settings['legacy_quic_protocols']
                if profile['quic_protocols'] == ['tuic']:
                    profile['port'] = profile['tuic_port']
            if settings.get('uuid'):
                profile['uuid'] = settings['uuid']
            if settings.get('hy2_password'):
                profile['password'] = settings['hy2_password']
            if settings.get('tuic_password'):
                profile['tuic_password'] = settings['tuic_password']
        else:
            profile['port'] = native_hysteria_port(port)
            if settings.get('native_hysteria_password'):
                profile['password'] = settings['native_hysteria_password']
        if settings.get('egress_mode') == 'fixed' and settings.get('upstream_id'):
            profile.update(egress='fixed', upstream_id=settings['upstream_id'])
        elif (settings.get('egress_mode') or 'default') == 'default' and self.xray.running():
            try:
                outbounds = json.loads(self.xray.config_path.read_text())['outbounds']
                if [item['protocol'] for item in outbounds] == ['freedom']:
                    profile['egress'] = 'direct'
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return profile

    def legacy_core(self, core_id):
        return {'xray-core': self.xray, 'sing-box': self.singbox, 'hysteria2': self.hysteria}.get(core_id)

    def publisher_runtime(self, profile_id):
        with self._local_lock:
            if profile_id not in self.independent:
                core, _, instance = profile_id.partition('@')
                self.independent[profile_id] = IndependentCore(self.data_dir, core, instance)
            return self.independent[profile_id]

    @staticmethod
    def _publisher_profile_id(core_id, instance, profiles):
        if instance == '':
            return core_id
        if not isinstance(instance, str) or instance == core_id or publisher_core(instance) != core_id or instance not in profiles:
            raise ValueError('此发布配置不存在，请刷新列表')
        return instance

    def core_runtime(self, core_id, profiles=None):
        legacy = self.legacy_core(core_id)
        if legacy and legacy.running():
            return legacy
        if profiles is None:
            profiles = self.independent_profiles()
        primary = self.independent.get(core_id) if not legacy or core_id in profiles else legacy
        if primary and primary.running():
            return primary
        return next((service for key, profile in profiles.items()
                     if key != core_id and publisher_core(key) == core_id and profile.get('enabled')
                     and (service := self.publisher_runtime(key)).running()), primary)

    def core_logs(self, core_id):
        if not isinstance(core_id, str) or core_id not in PUBLISHERS:
            raise ValueError('内核无效')
        with self._local_lock:
            runtimes = []
            legacy = self.legacy_core(core_id)
            if legacy:
                runtimes.append(('旧版发布服务', legacy))
            runtimes.append(('主发布实例', self.publisher_runtime(core_id)))
            runtimes.extend(
                (f'发布实例 · {profile["port"]} · {key}', self.publisher_runtime(key))
                for key, profile in self.independent_profiles().items()
                if key != core_id and publisher_core(key) == core_id
            )
            logs = [(title, output) for title, runtime in runtimes if (output := runtime.log_text())]
        if len(logs) == 1:
            return logs[0][1]
        return '\n\n'.join(f'[{title}]\n{output.rstrip()}' for title, output in logs)

    def core_configuration(self, core_id):
        if not isinstance(core_id, str) or core_id not in PUBLISHERS:
            raise ValueError('内核无效')
        with self._local_lock:
            service = self.core_runtime(core_id)
            if not service:
                raise ValueError('此内核没有已生成的配置')
            try:
                fd = os.open(service.config_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(fd, 'rb') as source:
                    payload = source.read(1024 * 1024 + 1)
                if len(payload) > 1024 * 1024 or not isinstance(json.loads(payload), dict):
                    raise ValueError('内核配置无效或过大')
            except (UnicodeError, json.JSONDecodeError, OSError) as exc:
                raise ValueError('无法读取已生成的内核配置') from exc
            return payload

    def backup_core_configuration(self, core_id):
        payload = self.core_configuration(core_id)
        directory = self.data_dir / 'core-config-backups'
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise ValueError('内核配置备份目录须为 Router 的私有目录')
        with tempfile.NamedTemporaryFile(dir=directory, prefix=f'{core_id}-', suffix='.json', delete=False) as output:
            output.write(payload)
            return Path(output.name).name

    def independent_endpoints(self, profiles=None):
        with self._local_lock:
            if profiles is None:
                profiles = self.independent_profiles()
            relay = self.relay_settings()
            public_ports = {
                (item['core_id'], item['port']): item.get('public_port', item['port'])
                for item in relay.get('mappings', [])
            }
            endpoints = {}
            for profile_id, profile in profiles.items():
                if not profile.get('enabled') or not self._independent_links_available(profile):
                    continue
                core_id = publisher_core(profile_id)
                for link in self.publisher_runtime(profile_id).links():
                    parts = urlsplit(link)
                    endpoint = {
                        'core_id': core_id, 'public_host': profile.get(
                            'detected_ipv6' if profile.get('public_host_ipv6') and ':' in (parts.hostname or '') else 'detected_host'
                        ) or parts.hostname, 'port': parts.port,
                        'protocol': parts.scheme, 'egress': profile['egress'],
                        'bind': publisher_bind(profile),
                    }
                    if profile_id != core_id:
                        endpoint['profile_id'] = profile_id
                    if endpoint['public_host'] == relay.get('domain') and not profile['public_listener']:
                        public_port = public_ports.get((core_id, parts.port))
                        if public_port is not None:
                            endpoint['public_port'] = public_port
                    endpoints[node_id(link)] = endpoint
            return endpoints

    def independent_profile_view(self, core_id, instance=''):
        if not isinstance(core_id, str) or core_id not in PUBLISHERS:
            raise ValueError('内核无效')
        _, _, _, port, tls, fixed = PUBLISHERS[core_id]
        profiles = self.independent_profiles()
        profile_id = self._publisher_profile_id(core_id, instance, profiles)
        profile = profiles.get(profile_id, {})
        settings = self.local_node_settings()
        legacy = self._legacy_profile(core_id, settings) if not profile else {}
        profile = profile or legacy
        host = profile.get('public_host', settings.get('public_host', ''))
        acme = self.acme_settings() if tls else {}
        certificate_domain = acme.get('domain', '')
        certificate_ready = bool(certificate_domain and not is_ip_host(certificate_domain)
                                 and self._quic_certificate({'public_host': certificate_domain}))
        management = management_https_certificate_status(self.data_dir) if tls else {}
        certificate_ip = management.get('host', '') if (management.get('days_left') or 0) > 0 else ''
        if not is_ip_host(certificate_ip):
            certificate_ip = ''
        default_mode = 'domain'
        if not certificate_ready and (core_id == 'hysteria2' or certificate_ip):
            default_mode = 'auto_ip'
        if not tls:
            if not host:
                default_mode = 'auto_ip'
            elif is_ip_host(host):
                default_mode = 'fixed_ip'
        return {
            'public_host': host, 'detected_host': profile.get('detected_host', ''),
            'detected_ipv6': profile.get('detected_ipv6', ''),
            'address_mode': profile.get('address_mode', default_mode),
            'address_family': profile.get('address_family', 'ipv6' if ':' in (host or certificate_ip) else 'ipv4'),
            'ddns': profile.get('ddns', False), 'address_error': profile.get('address_error', ''),
            'ddns_ipv4': profile.get('ddns_ipv4', ''), 'ddns_ipv6': profile.get('ddns_ipv6', ''),
            'port': profile.get('port', port), 'tuic_port': profile.get('tuic_port'),
            'quic_protocols': list(publisher_ports(core_id, profile)) if profile and core_id == 'sing-box' else [],
            'public_listener': profile.get('public_listener', False),
            'legacy': bool(legacy),
            'egress': profile.get('egress', ''), 'upstream_id': profile.get('upstream_id', ''),
            'security': self.security.publisher_status(profile_id),
            'certificate_required': tls, 'fixed_upstream': fixed,
            'certificate_domain': certificate_domain,
            'certificate_ready': certificate_ready,
            'certificate_ip': certificate_ip,
            'tls_mode': profile.get('tls_mode', 'automatic'),
            'ip_certificate_email': profile.get('ip_certificate_email') or acme.get('email', ''),
            'ip_certificate_expires': profile.get('ip_certificate_expires'),
            'bound_domains': self.bound_domains(),
        }

    def relay_settings(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (RELAY_KEY,)).fetchone()
        return json.loads(row['value']) if row else {}

    def _relay_candidates(self, profiles=None):
        return [item for item in self.independent_endpoints(profiles).values() if item['bind'] == '127.0.0.1']

    @staticmethod
    def _relay_mappings(domain, cores, candidates):
        mappings = []
        for candidate in candidates:
            core, port, protocol = candidate['core_id'], candidate['port'], candidate['protocol']
            if core not in cores or candidate['public_host'].lower() != domain:
                continue
            transports = ('tcp',) if protocol == 'vless' else ('tcp', 'udp') if protocol == 'ss' else ('udp',)
            for transport in transports:
                mappings.append({'core_id': core, 'port': port, 'transport': transport,
                                 'name': f'hopworld-{core}-{transport}-{port}'})
        return mappings

    def _live_relay_mappings(self, settings, candidates):
        if not settings.get('enabled'):
            return []
        live = {mapping['name'] for mapping in self._relay_mappings(settings['domain'], settings['cores'], candidates)}
        return [mapping for mapping in settings['mappings'] if mapping['name'] in live]

    def configure_relay(self, data):
        with self._local_lock:
            previous = self.relay_settings()
            candidates = self._relay_candidates()
            if data == {'action': 'stop'}:
                if not previous:
                    return self.relay_status()
                settings = {**previous, 'enabled': False}
            else:
                settings = validate_relay(data, previous)
                cores = settings['cores']
                if not isinstance(cores, list) or len(cores) > len(PUBLISHERS) or any(
                    not isinstance(core, str) or core not in PUBLISHERS for core in cores
                ) or len(set(cores)) != len(cores):
                    raise ValueError('请选择要映射的独立发布节点')
                if not cores:
                    if previous.get('mappings'):
                        raise ValueError('请选择至少一个映射。暂停全部映射请使用「停止隧道」')
                    settings['enabled'] = False
                if settings['server'] == settings['domain']:
                    raise ValueError('请填写 VPS 公网 IP 或独立的控制域名，供 Router 校验节点域名指向')
                local = self.local_node_settings()
                if local.get('ddns_enabled') and (local.get('public_host') or '').lower() == settings['domain']:
                    raise ValueError('请先关闭此域名的家宽 DDNS，再启用 VPS 中转')
                if any(profile.get('enabled') and profile.get('ddns') and profile['public_host'] == settings['domain']
                       for profile in self.independent_profiles().values()):
                    raise ValueError('请先关闭独立节点对此域名的 DDNS，再启用 VPS 中转')
                mappings = self._relay_mappings(settings['domain'], cores, candidates)
                if {mapping['core_id'] for mapping in mappings} != set(cores):
                    raise ValueError('所选内核需正在运行、仅监听本机，并使用相同的订阅域名')
                profiles = self.independent_profiles()
                families = {profiles[node.get('profile_id', node['core_id'])].get('address_family', 'ipv4')
                            for node in candidates if node['core_id'] in cores and node['public_host'].lower() == settings['domain']}
                for family in families:
                    self.require_domain_binding(settings['domain'], family, relay=settings)
                public_ports = settings.pop('public_ports', {})
                keys = {f"{item['core_id']}:{item['port']}" for item in mappings}
                if not isinstance(public_ports, dict) or public_ports.keys() - keys or any(
                    type(port) is not int or not 1 <= port <= 65535 for port in public_ports.values()
                ):
                    raise ValueError('VPS 公网端口无效，请确认所选节点及端口范围 1–65535')
                previous_ports = {
                    item['name']: item.get('public_port', item['port']) for item in previous.get('mappings', [])
                } if previous.get('domain') == settings['domain'] else {}
                ports = {('tcp', settings['port'])}
                for mapping in mappings:
                    key = f"{mapping['core_id']}:{mapping['port']}"
                    public_port = public_ports.get(key, previous_ports.get(mapping['name'], mapping['port']))
                    entry = (mapping['transport'], public_port)
                    if entry in ports:
                        raise ValueError(f'{entry[0].upper()} 公网端口 {public_port} 与其他映射或 VPS 控制端口冲突')
                    ports.add(entry)
                    mapping['public_port'] = public_port
                settings['mappings'] = mappings
            try:
                live = self._live_relay_mappings(settings, candidates)
                previous_ready = live and previous.get('enabled') and any(
                    status['registered'] for status in self.relay.proxy_status(previous['token']).values()
                )
                self.relay.apply(settings, live)
                if live:
                    deadline = time.monotonic() + 2
                    expected = {mapping['name'] for mapping in live}
                    while time.monotonic() < deadline:
                        registrations = self.relay.proxy_status(settings['token'])
                        for name in expected & registrations.keys():
                            if reason := registrations[name]['error']:
                                raise ValueError(f'VPS 拒绝映射 {name}：{reason}')
                        if all(registrations.get(name, {}).get('registered') for name in expected):
                            break
                        time.sleep(0.15)
                    else:
                        if previous_ready:
                            raise ValueError('新隧道未完成端口注册，已恢复上一次配置')
                with self.connect() as db:
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (RELAY_KEY, json.dumps(settings)))
            except (ValueError, OSError, sqlite3.Error) as exc:
                self._reconcile_relay(previous, candidates)
                if isinstance(exc, ValueError):
                    raise
                raise ValueError('无法保存或启动 VPS 中转，请检查数据目录权限') from exc
            return self.relay_status()

    def sync_relay(self):
        with self._local_lock:
            settings = self.relay_settings()
            self._reconcile_relay(settings, self._relay_candidates() if settings.get('enabled') else [])

    def _reconcile_relay(self, settings, candidates):
        try:
            self.relay.apply(settings, self._live_relay_mappings(settings, candidates))
        except (ValueError, OSError) as exc:
            self.relay.stop()
            self.relay._error = str(exc) if isinstance(exc, ValueError) else '无法读写 frpc 运行文件'

    def relay_status(self):
        with self._local_lock:
            settings = self.relay_settings()
            candidates = self._relay_candidates()
            live = self._live_relay_mappings(settings, candidates)
            running = bool(live) and self.relay.running()
            registrations = self.relay.proxy_status(settings['token']) if running else {}
            mappings = [
                {**mapping, 'public_port': mapping.get('public_port', mapping['port']), 'local_ready': mapping in live,
                 **(registrations.get(mapping['name'], {}) if mapping in live else {})}
                for mapping in settings.get('mappings', [])
            ]
            return {
                'enabled': settings.get('enabled', False), 'domain': settings.get('domain', ''),
                'server': settings.get('server', ''), 'port': settings.get('port', 7000),
                'cores': settings.get('cores', []), 'mappings': mappings, 'candidates': candidates,
                'installed': os.name != 'nt' and bool(self.relay.locate()), 'running': running,
                'configured': bool(settings), 'error': self.relay._error,
            }

    def relay_log(self):
        with self._local_lock:
            return self.relay.redact(self.relay.log_text(), self.relay_settings().get('token', ''))

    def _detach_relay_core(self, core_id):
        settings = self.relay_settings()
        if settings.get('enabled') and core_id in settings['cores']:
            self.relay.stop()

    def _independent_config(self, profile_id, profile):
        core_id = publisher_core(profile_id)
        service = self.independent[core_id]
        if not service.locate():
            raise ValueError(f'请先安装 {service.program}')
        proxy = None
        if profile['egress'] == 'fixed':
            pool, _ = self._auto_pool_entries(profile, profile['upstream_id'])
            if not pool:
                raise ValueError('绑定的上游节点已停用、不可用或不兼容')
            proxy = pool[0]
        pin = ''
        if profile.get('tls_mode') == 'ip_acme':
            if profile.get('ip_certificate_expires', 0) <= time.time():
                raise ValueError('公网 IP 证书已过期，等待自动续期')
            certificate = ip_certificate_paths(self.data_dir, profile['public_host'])
        elif (core_id == 'hysteria2' and profile.get('tls_mode') != 'management_ip'
              and profile.get('address_mode') == 'auto_ip' and is_ip_host(profile['public_host'])):
            certificate, pin = hysteria_local_certificate(self.data_dir)
        else:
            certificate = self._quic_certificate(profile) if PUBLISHERS[core_id][4] else None
        config, links = render_publisher(core_id, profile, certificate, proxy, certificate_pin=pin)
        return config, links, certificate

    def _apply_independent_profile(self, profile_id, profile):
        self.apply_security_profile(profile_id, profile, self.security.binding(profile_id))

    def _security_candidate(self, profile_id, profile, binding):
        service = self.publisher_runtime(profile_id)
        config, links, certificate = self._independent_config(profile_id, profile)
        if binding:
            if service.core_id != 'sing-box' or profile['egress'] != 'fixed' or profile['upstream_id'] != binding['upstream_id']:
                raise ValueError('当前 DNS 策略绑定了指定上游，请先在「网络安全」回滚策略')
            require_sing_box_version(service.locate())
            config = build_secure_dns_config(config, binding['resolver'])
        return service, config, links, certificate

    def validate_security_profile(self, profile_id, profile, binding):
        service, config, _, _ = self._security_candidate(profile_id, profile, binding)
        service._validate(json.dumps(config).encode())

    def apply_security_profile(self, profile_id, profile, binding):
        service, config, links, certificate = self._security_candidate(profile_id, profile, binding)
        revision = binding['revision'] if binding else None
        service.apply_profile(config, links, certificate, bind=publisher_bind(profile), security_revision=revision)
        service._error = ''

    def _publisher_switch_source(self, core_id, protocol, port):
        peers = COMPATIBLE_PUBLISHERS.get(protocol)
        if not peers or core_id not in peers or type(port) is not int:
            raise ValueError('此发布协议没有可切换的兼容内核')
        target = peers[1] if core_id == peers[0] else peers[0]
        profiles = self.independent_profiles()
        settings = self.local_node_settings()
        for key, source in profiles.items():
            if publisher_core(key) == core_id and source.get('enabled') and publisher_ports(core_id, source).get(protocol) == port:
                return target, profiles, settings, source, key
        if core_id not in profiles and settings.get('enabled'):
            source = self._legacy_profile(core_id, settings)
            if source and publisher_ports(core_id, source).get(protocol) == port:
                return target, profiles, settings, source, None
        raise ValueError('原节点已变更，请刷新端口列表')

    def _legacy_target_profile(self, core_id, profiles, settings):
        if not self.legacy_core(core_id) or core_id in profiles or not settings.get('enabled'):
            return {}
        return self._legacy_profile(core_id, settings)

    def publisher_switch_view(self, core_id, protocol, port):
        with self._local_lock:
            target, profiles, settings, source, source_id = self._publisher_switch_source(core_id, protocol, port)
            existing = profiles.get(target, {})
            legacy_target = self._legacy_target_profile(target, profiles, settings)
            occupied = any(publisher_core(key) == target and profile.get('enabled')
                           and publisher_ports(target, profile).get(protocol) == port for key, profile in profiles.items())
            occupied = occupied or bool(legacy_target and publisher_ports(target, legacy_target).get(protocol) == port)
            manage_target = False
            reason = ''
            if (source_id and self.security.binding(source_id)) or self.security.binding(target):
                reason = '已有 DNS 策略绑定，请先在「网络安全」回滚策略'
            elif occupied:
                reason = '目标内核已配置相同的协议和端口'
            elif protocol == 'hysteria2' and (source.get('tls_mode') == 'ip_acme' or
                    source.get('tls_mode') != 'management_ip'
                    and source.get('address_mode') == 'auto_ip' and is_ip_host(source['public_host'])):
                reason = '此节点的公网 IP 证书模式由原生 Hysteria 2 提供，请先改用域名证书'
            elif not self.independent[target].locate():
                reason = '请先在内核管理安装目标内核'
                manage_target = True
            elif PUBLISHERS[target][4] and not self._quic_certificate(source):
                reason = '请先为此节点配置匹配的有效 TLS 证书'
            fixed = source.get('egress') == 'fixed' and bool(PUBLISHERS[target][5])
            if fixed:
                pool, _ = self._auto_pool_entries(source, source['upstream_id'])
                fixed = bool(pool and pool[0]['protocol'] == 'socks')
            keep_egress = fixed or source.get('egress') == 'direct'
            return {'target': target, 'reason': reason, 'keep_egress': keep_egress,
                    'manage_target': manage_target, 'replace_saved': bool(existing and not existing.get('enabled') and not legacy_target)}

    def switch_publisher(self, data):
        if (not isinstance(data, dict) or set(data) != {'source', 'target', 'protocol', 'port', 'egress'}
                or not all(isinstance(data[key], str) for key in ('source', 'target', 'protocol', 'egress'))
                or data['egress'] not in ('keep', 'direct')):
            raise ValueError('内核切换参数无效')
        core_id, protocol, port = data['source'], data['protocol'], data['port']
        with self._local_lock:
            target, profiles, settings, source, source_id = self._publisher_switch_source(core_id, protocol, port)
            legacy = source_id is None
            view = self.publisher_switch_view(core_id, protocol, port)
            if data['target'] != target or view['reason']:
                raise ValueError(view['reason'] or '目标内核不兼容')
            if data['egress'] == 'keep' and not view['keep_egress']:
                raise ValueError('目标内核无法沿用原出口，请明确选择本机直连')
            egress = source['egress'] if data['egress'] == 'keep' else 'direct'
            previous_target = profiles.get(target, {})
            target_id = target
            new_target = {**source, 'enabled': True, 'port': port, 'egress': egress,
                          'upstream_id': source.get('upstream_id', '') if egress == 'fixed' else ''}
            new_target.pop('quic_protocols', None)
            new_target.pop('tuic_port', None)
            password = source.get('tuic_password' if core_id == 'sing-box' and protocol == 'tuic' else 'password')
            if not password or protocol == 'tuic' and not source.get('uuid'):
                raise ValueError('原节点的认证凭据缺失，请先保存原配置')
            source_service = self.legacy_core(core_id) if legacy else self.publisher_runtime(source_id)
            source_running = source_service.running()
            if not legacy:
                original_links = source_service.links()
            elif core_id == 'sing-box':
                original_links = source_service.links(settings)
            else:
                original_links = [link] if (link := source_service.link(settings)) else []
            names = {parts.scheme: parts.fragment for parts in map(urlsplit, original_links)}
            if source.get('public_host_ipv6'):
                names = {scheme: quote(unquote(name).removesuffix(' · IPv4').removesuffix(' · IPv6'))
                         for scheme, name in names.items()}
            if legacy and protocol not in names:
                suffix = {'hysteria2': 'Hysteria2' if core_id == 'sing-box' else '原生 Hysteria2',
                          'tuic': 'TUIC'}[protocol]
                names[protocol] = quote(_protocol_label(source['name'], suffix))
            shared = ('public_host', 'address_family', 'public_listener', 'address_mode', 'ddns',
                      'public_host_ipv6', 'detected_host', 'detected_ipv6', 'egress', 'upstream_id')
            merge = (previous_target.get('enabled') and protocol not in publisher_ports(target, previous_target)
                     and (new_target.get('tls_mode') == 'management_ip') == (previous_target.get('tls_mode') == 'management_ip')
                     and all((new_target.get(key) or '') == (previous_target.get(key) or '') for key in shared))
            if merge:
                new_target = {**previous_target}
            elif previous_target.get('enabled') or self._legacy_target_profile(target, profiles, settings):
                target_id = f'{target}@{secrets.token_hex(6)}'
            new_target['link_names'] = {**new_target.get('link_names', {}), **(
                {protocol: names[protocol]} if protocol in names else {})}
            if target == 'sing-box':
                ports = publisher_ports(target, previous_target) if merge else {}
                ports[protocol] = port
                new_target.update(quic_protocols=list(ports), port=ports.get('hysteria2', ports.get('tuic')))
                if 'tuic' in ports:
                    new_target['tuic_port'] = ports['tuic']
            field = 'tuic_password' if target == 'sing-box' and protocol == 'tuic' else 'password'
            new_target[field] = password
            if protocol == 'tuic':
                new_target['uuid'] = source['uuid']
            profile_credentials(new_target)
            prepared = self._independent_config(target_id, new_target)
            originals = {link for link in original_links if urlsplit(link).scheme == protocol}
            if not originals.issubset(prepared[1]):
                raise ValueError('目标内核无法保留原节点的连接参数')

            remaining = publisher_ports(core_id, source)
            del remaining[protocol]
            new_source = {**source, 'enabled': bool(remaining)}
            new_legacy = None
            if legacy:
                new_legacy = {**settings, LEGACY_CORE_FLAGS[core_id]: bool(remaining)}
                if core_id == 'sing-box':
                    new_legacy['legacy_quic_protocols'] = list(remaining)
            elif remaining:
                new_source.update(quic_protocols=list(remaining), port=next(iter(remaining.values())))
                if 'tuic' in remaining:
                    new_source['tuic_port'] = remaining['tuic']
            updated = {**profiles, target_id: new_target}
            if not legacy:
                if remaining or source_id == core_id:
                    updated[source_id] = new_source
                else:
                    del updated[source_id]

            previous_relay = self.relay_settings()
            mappings = []
            for item in previous_relay.get('mappings', []):
                if item['core_id'] == core_id and item['port'] == port:
                    item = {**item, 'core_id': target, 'name': f"hopworld-{target}-{item['transport']}-{port}"}
                mappings.append(item)
            relay_settings = None
            if mappings != previous_relay.get('mappings', []):
                relay_settings = {**previous_relay, 'mappings': mappings,
                                  'cores': list(dict.fromkeys(item['core_id'] for item in mappings))}
            target_service = self.publisher_runtime(target_id)
            target_running = target_service.running()
            target_changed = False
            try:
                if source_running and remaining:
                    if legacy:
                        source_service.apply(new_legacy, self._quic_certificate(settings))
                        if not source_service.running():
                            raise ValueError('原 sing-box 无法保留另一个协议')
                    else:
                        self._apply_independent_profile(source_id, new_source)
                elif source_running:
                    source_service.stop()
                target_service.apply_profile(*prepared, bind=publisher_bind(new_target))
                target_changed = True
                if relay_settings:
                    candidates = self._relay_candidates(updated)
                    self.relay.apply(relay_settings, self._live_relay_mappings(relay_settings, candidates))
                self._save_independent_profiles(updated, legacy_settings=new_legacy, relay_settings=relay_settings)
                target_service._error = ''
                if source_id is not None and source_id not in updated:
                    self.independent.pop(source_id, None)
            except (OSError, ValueError, sqlite3.Error) as error:
                try:
                    if target_changed:
                        if target_running:
                            self._apply_independent_profile(target_id, previous_target)
                        else:
                            target_service.stop()
                    if source_running:
                        if legacy:
                            source_service.apply(settings, self._quic_certificate(settings))
                            if not source_service.running():
                                raise ValueError('原内核未恢复监听')
                        else:
                            self._apply_independent_profile(source_id, source)
                    if relay_settings:
                        self.sync_relay()
                except (OSError, ValueError) as rollback:
                    raise ValueError(f'切换失败，原发布恢复失败：{rollback}') from rollback
                finally:
                    if target_id != target and not target_service.running():
                        self.independent.pop(target_id, None)
                if isinstance(error, sqlite3.Error):
                    raise ValueError('无法保存切换配置，原发布已恢复') from error
                raise

    def start_independent_core(self, core_id, data, instance=''):
        if not isinstance(core_id, str) or core_id not in PUBLISHERS or not isinstance(data, dict):
            raise ValueError('内核配置无效')
        profile_id = self._publisher_profile_id(core_id, instance, self.independent_profiles())
        if set(data) - {'address_mode', 'address_family', 'ddns', 'tls_mode', 'ip_certificate_email'} != {
            'public_host', 'port', 'public_listener', 'egress', 'upstream_id'
        }:
            raise ValueError('内核配置字段无效')
        port = data['port']
        if type(port) is not int or not 1 <= port <= 65535 or type(data['public_listener']) is not bool:
            raise ValueError('监听地址或端口无效')
        mode, upstream_id = data['egress'], data['upstream_id']
        if mode == 'strict':
            raise ValueError('严格出口尚未通过独立网络验收，当前禁止以严格模式发布')
        if mode not in ('direct', 'fixed') or not isinstance(upstream_id, str):
            raise ValueError('请选择出口方式')
        if mode == 'fixed' and (not PUBLISHERS[core_id][5] or not re.fullmatch(r'[0-9a-f]{64}', upstream_id)):
            raise ValueError('请选择这个内核支持的上游节点')
        automatic = data.get('address_mode') == 'auto_ip'
        if automatic:
            host = ''
        elif data.get('address_mode') == 'domain':
            host = validate_subscription_domain(data['public_host'])
        else:
            host = validate_public_host(data['public_host'])
        address_mode = data.get('address_mode', 'fixed_ip' if is_ip_host(host) else 'domain')
        family = data.get('address_family', 'ipv6' if ':' in host else 'ipv4')
        ddns = data.get('ddns', False)
        if address_mode not in ('domain', 'fixed_ip', 'auto_ip') or family not in ('ipv4', 'ipv6', 'dual') or type(ddns) is not bool:
            raise ValueError('订阅地址选项无效')
        if address_mode == 'fixed_ip' and (
            not is_ip_host(host) or (':' in host) != (family == 'ipv6') or family == 'dual'
        ):
            raise ValueError('订阅地址与所选类型不匹配')
        dual_ip = automatic and family == 'dual'
        if ddns and (address_mode != 'domain' or '.' not in host):
            raise ValueError('DDNS 需要完整域名')
        tls_mode = data.get('tls_mode', 'automatic')
        if tls_mode not in ('automatic', 'ip_acme') or tls_mode == 'ip_acme' and (core_id != 'hysteria2' or not automatic):
            raise ValueError('公网 IP 证书仅用于 Hysteria 2 自动公网 IP 模式')
        if dual_ip and tls_mode == 'ip_acme':
            raise ValueError('公网 IP 证书模式只支持单地址族，双栈请选择自动 TLS 证书')
        ip_certificate = {'tls_mode': tls_mode, 'ip_certificate_email': '', 'ip_certificate_expires': 0,
                          'ip_certificate_pending': False}
        if dual_ip:
            detected, detected_ipv6 = (self._detect_dual_public_ip(version) for version in (4, 6))
        else:
            detected = self._detect_direct_public_ip(6 if family == 'ipv6' else 4) if automatic else ''
            detected_ipv6 = ''
        certificate_host = ''
        if automatic and PUBLISHERS[core_id][4] and tls_mode != 'ip_acme':
            domain = self.acme_settings().get('domain') or ''
            if domain and not is_ip_host(domain) and self._quic_certificate({'public_host': domain}):
                certificate_host = domain
            elif not dual_ip and detected == management_https_host():
                paths = self._quic_certificate({'public_host': detected, 'tls_mode': 'management_ip'})
                if paths and (days := days_until_expiry(paths[0]) or 0) > 0:
                    certificate_host = detected
                    ip_certificate.update(tls_mode='management_ip', ip_certificate_expires=int(time.time() + days * 86400))
            if not certificate_host and core_id != 'hysteria2':
                raise ValueError('双栈自动公网 IP 需要有效的域名证书。也可选择安装证书覆盖的单一地址族。' if dual_ip
                                 else '检测到的公网 IP 没有匹配的管理 IP 证书，请配置有效的域名证书。')
        if tls_mode == 'ip_acme':
            email = validate_acme_email(data.get('ip_certificate_email'))
            service = self.independent[core_id]
            if not service.locate():
                raise ValueError(f'请先安装 {service.program}')
            with self._hysteria_ip_lock:
                managed = self.ip_certificates().get(family, {})
                expires = ensure_ip_certificate(self.data_dir, detected, email, challenge=managed.get('challenge', 'http'))
            ip_certificate.update(ip_certificate_email=email, ip_certificate_expires=expires)
        with self._local_lock:
            profiles = self.independent_profiles()
            profile_id = self._publisher_profile_id(core_id, instance, profiles)
            legacy = self.legacy_core(core_id) if profile_id == core_id else None
            legacy_running = bool(legacy and legacy.running())
            if core_id == 'xray-core' and (self.singbox.running() or self.hysteria.running()):
                raise ValueError('仍有旧版 QUIC 服务使用 Xray 出口，请先切换或停止这些服务')
            settings = self.local_node_settings()
            previous = profiles.get(profile_id) or self._legacy_profile(core_id, settings)
            binding = self.security.binding(profile_id)
            if binding and (mode != 'fixed' or upstream_id != binding['upstream_id']):
                raise ValueError('当前 DNS 策略绑定了指定上游，请先在「网络安全」回滚策略')
            if tls_mode == 'ip_acme':
                host = detected
            elif certificate_host:
                host = certificate_host
            elif automatic:
                host = (previous['public_host'] if previous.get('address_mode') == address_mode
                        and previous.get('address_family') == family and is_ip_host(previous['public_host']) else detected)
            profile = {**previous, **data, **ip_certificate, 'public_host': host, 'enabled': True, 'address_mode': address_mode,
                       'address_family': family, 'ddns': ddns, 'detected_host': detected, 'address_error': '',
                       'upstream_id': upstream_id if mode == 'fixed' else ''}
            if dual_ip:
                profile['public_host_ipv6'] = previous.get('public_host_ipv6') or detected_ipv6
                profile['detected_ipv6'] = detected_ipv6
            else:
                profile.pop('public_host_ipv6', None)
                profile.pop('detected_ipv6', None)
            if port != previous.get('port'):
                if profile.get('quic_protocols') == ['tuic']:
                    profile['tuic_port'] = port
                else:
                    profile.pop('tuic_port', None)
            if ddns:
                self._validate_independent_ddns(profile)
            if address_mode == 'domain':
                if {'host': host, 'family': family} not in self.bound_domains():
                    raise ValueError('请先在「域名与证书」绑定此域名及地址族，再选择它')
                self.require_domain_binding(host, family)
            if not ddns or not previous.get('ddns') or any(
                previous.get(key) != profile[key] for key in ('public_host', 'address_family')
            ):
                profile.update(ddns_ipv4='', ddns_ipv6='')
            profile_credentials(profile)
            if core_id == 'xray-core' and not profile.get('private_key'):
                self.xray.locate()
                profile['private_key'], profile['public_key'] = self.xray.generate_keys()
            self._detach_relay_core(core_id)
            legacy_settings = {**settings, LEGACY_CORE_FLAGS[core_id]: False} if legacy else None
            started = False
            try:
                if legacy_running:
                    legacy.stop()
                self._apply_independent_profile(profile_id, profile)
                started = True
                self._save_independent_profiles({**profiles, profile_id: profile}, legacy_settings=legacy_settings)
            except (OSError, ValueError, sqlite3.Error) as error:
                if started:
                    self.publisher_runtime(profile_id).stop()
                if legacy_running:
                    try:
                        if core_id == 'xray-core':
                            legacy.apply(settings)
                        else:
                            legacy.apply(settings, self._quic_certificate(settings))
                        if not legacy.running():
                            raise ValueError('旧版服务未恢复运行')
                    except (OSError, ValueError) as restore_error:
                        raise ValueError(f'{error}。旧版服务恢复失败：{restore_error}') from restore_error
                if isinstance(error, sqlite3.Error):
                    raise ValueError('无法保存独立内核设置，请检查数据库') from error
                raise

    @staticmethod
    def _detect_direct_public_ip(version):
        host = ipaddress.ip_address(detect_public_ip(version, timeout=3, direct=True))
        if host.version != version or not host.is_global or host.is_multicast:
            raise ValueError(f'未检测到公网 IPv{version} 地址')
        return str(host)

    @staticmethod
    def _dual_local_address(version):
        address = local_route_address(version)
        if address:
            host = ipaddress.ip_address(address)
            if host.version == version and host.is_global and not host.is_multicast and not host.is_reserved:
                return str(host)
        return None

    def _detect_dual_public_ip(self, version):
        try:
            return self._detect_direct_public_ip(version)
        except ValueError:
            if address := self._dual_local_address(version):
                return address
            raise

    def _domain_binding_target(self, host, family, relay=None):
        if family not in ('ipv4', 'ipv6', 'dual', 'ipv4_first'):
            raise ValueError('请选择地址族')
        if relay is None:
            relay = self.relay_settings()
        vps = relay.get('domain') == host
        expected = {'ipv4': set(), 'ipv6': set()}
        target = 'VPS' if vps else '本机公网入口'
        if vps:
            server = relay['server']
            if server == host:
                raise ValueError('请先在 VPS 管理填写独立的控制地址，以校验节点域名')
            try:
                expected = _lookup_addresses(server, timeout=4)
            except OSError as exc:
                raise ValueError('无法解析 VPS 控制地址，请检查 VPS 设置') from exc
        else:
            for version in (4, 6):
                key = f'ipv{version}'
                if family in (key, 'dual', 'ipv4_first'):
                    try:
                        expected[key].add(self._detect_direct_public_ip(version))
                    except (ValueError, OSError):
                        pass
                if family == 'ipv4_first' and expected['ipv4']:
                    break
        if family == 'ipv4_first':
            family = 'ipv4' if expected['ipv4'] else 'ipv6'
        return expected, target, ('ipv4', 'ipv6') if family == 'dual' else (family,)

    def _domain_binding_relay(self, host, ingress):
        relay = self.relay_settings()
        if ingress == 'vps':
            if not relay.get('domain'):
                raise ValueError('请先在「公网接入 → VPS 管理」登记 VPS 入口')
            if host and relay['domain'] != host:
                raise ValueError(f'当前 VPS 入口登记的节点域名为 {relay["domain"]}，请使用该域名或更新 VPS 配置')
        elif ingress == 'direct':
            if host and relay.get('domain') == host:
                raise ValueError('此域名已登记为 VPS 入口，请选择 VPS 中转')
        elif ingress is not None:
            raise ValueError('请选择公网入口类型')
        return relay

    def domain_binding_records(self, host, family, ingress=None):
        host = validate_subscription_domain(host) if host != '' else ''
        relay = self._domain_binding_relay(host, ingress)
        if not host and ingress == 'vps':
            host = relay['domain']
        expected, target, required = self._domain_binding_target(host, family, relay)
        resolved = self._domain_dns_answers(host) if host else None
        return {'target': target, 'records': [
            {'type': 'A' if key == 'ipv4' else 'AAAA', 'name': host, 'values': sorted(expected[key]),
             'current_values': sorted(resolved[key]) if resolved is not None else None}
            for key in required
        ]}

    @staticmethod
    def _domain_dns_answers(host):
        try:
            return _lookup_addresses(host, timeout=4)
        except OSError:
            return None

    def domain_binding(self, host, family, *, relay=None):
        host = validate_subscription_domain(host)
        expected, target, required = self._domain_binding_target(host, family, relay)
        resolved = self._domain_dns_answers(host) or {'ipv4': set(), 'ipv6': set()}
        issues = []
        for key, record in (('ipv4', 'A'), ('ipv6', 'AAAA')):
            if key not in required:
                if resolved[key]:
                    issues.append(f'请移除额外的 {record} 记录，或选用双栈')
            elif not expected[key]:
                issues.append(f'无法确认{target}的 {key.upper()} 地址')
            elif not resolved[key] or resolved[key] - expected[key]:
                issues.append(f'{record} 应指向 {"、".join(sorted(expected[key]))}')
        return {
            **{key: sorted(values) for key, values in resolved.items()},
            'bound': not issues,
            'detail': '；'.join(issues) if issues else f'域名已绑定到{target}，入站端口待验证',
        }

    def require_domain_binding(self, host, family, *, relay=None):
        binding = self.domain_binding(host, family, relay=relay)
        if not binding['bound']:
            raise ValueError(f'请先绑定域名。{binding["detail"]}')

    def bound_domains(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='bound_domains'").fetchone()
        domains = json.loads(row['value']) if row else {}
        return [{'host': host, 'family': family} for host, family in sorted(domains.items())]

    def direct_network_status(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            addresses = dict(zip(('ipv4', 'ipv6'), pool.map(direct_address_status, (4, 6))))
        now = int(time.time())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM settings WHERE key='direct_address_history'").fetchone()
            history = json.loads(row[0]) if row else {}
            for family, result in addresses.items():
                latest = history.get(family, [])
                public = result['public']
                if public and (not latest or public != latest[0]['address']):
                    latest = [{'address': public, 'at': now}, *latest[:7]]
                    history[family] = latest
                result['last_change'] = latest[0]['at'] if latest else None
            db.execute("INSERT OR REPLACE INTO settings VALUES ('direct_address_history', ?)", (json.dumps(history),))
        return addresses

    def register_domain(self, host, family, ingress=None):
        host = validate_subscription_domain(host)
        if family not in ('ipv4', 'ipv6', 'dual'):
            raise ValueError('请选择域名的地址族')
        with self._local_lock:
            relay = self._domain_binding_relay(host, ingress)
            self.require_domain_binding(host, family, relay=relay)
            domains = {item['host']: item['family'] for item in self.bound_domains()}
            domains[host] = family
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('bound_domains', ?)", (json.dumps(domains),))
            return self.bound_domains()

    def _validate_independent_ddns(self, profile):
        domain = profile['public_host']
        relay = self.relay_settings()
        if relay.get('domain') == domain:
            raise ValueError('VPS 中转域名须解析到 VPS，不能开启家宽 DDNS')
        local = self.local_node_settings()
        if local.get('ddns_enabled') and (local.get('public_host') or '').lower() == domain:
            raise ValueError('此域名由旧版入口更新，请关闭旧版 DDNS 后再启用')
        if profile['address_family'] != 'ipv4' and os.environ.get('HOPWORLD_DDNS_PROVIDER', 'webhook').strip().lower() == 'dnspod':
            raise ValueError('当前 DNSPod 更新方式仅支持 A 记录，AAAA 请使用 DNS Webhook')

    def sync_independent_addresses(self):
        profiles = self.independent_profiles()
        active = {key: profile for key, profile in profiles.items()
                  if profile.get('enabled') and profile.get('tls_mode') != 'ip_acme'
                  and (profile.get('address_mode') == 'auto_ip' or profile.get('ddns'))}
        families = {'ipv4': (4,), 'ipv6': (6,), 'dual': (4, 6)}
        observed = {}
        for version in {version for profile in active.values() for version in families[profile['address_family']]}:
            try:
                observed[version] = self._detect_direct_public_ip(version)
            except ValueError:
                observed[version] = None
        if not active:
            return
        local = {}
        if any(profile.get('public_host_ipv6') for profile in active.values()):
            local = {version: self._dual_local_address(version) for version, address in observed.items() if not address}
        with self._local_lock:
            current = self.independent_profiles()
            updates = {}
            for key, profile in active.items():
                if current.get(key) != profile:
                    continue
                versions = families[profile['address_family']]
                detected = observed
                if profile.get('public_host_ipv6'):
                    detected = {version: observed[version] or local.get(version) for version in versions}
                missing = [f'IPv{version}' for version in versions if not detected[version]]
                update = {'address_error': f'暂未检测到 {"、".join(missing)}，保留上次地址' if missing else ''}
                if profile.get('ddns'):
                    changed = {f'ipv{version}': observed[version] for version in versions
                               if observed[version] and profile.get(f'ddns_ipv{version}') != observed[version]}
                    if changed:
                        try:
                            self._validate_independent_ddns(profile)
                            publish_ddns(profile['public_host'], **changed)
                        except (ValueError, OSError):
                            update['address_error'] = 'DDNS 更新失败，请检查 DNS 提供方配置'
                        else:
                            update.update({f'ddns_{family}': host for family, host in changed.items()})
                elif profile.get('tls_mode') == 'management_ip':
                    if not missing and detected[versions[0]] != profile['public_host']:
                        update['address_error'] = '公网 IP 已变化，保留证书覆盖的原地址'
                elif profile.get('public_host_ipv6'):
                    if detected[4]:
                        update['detected_host'] = detected[4]
                    if detected[6]:
                        update['detected_ipv6'] = detected[6]
                elif not missing:
                    update['detected_host'] = observed[versions[0]]
                if any(profile.get(field) != value for field, value in update.items()):
                    updates[key] = {**profile, **update}
            if updates:
                self._save_independent_profiles({**current, **updates})

    def sync_hysteria_ip_certificate(self):
        with self._hysteria_ip_lock:
            for profile_id, profile in self.independent_profiles().items():
                if publisher_core(profile_id) == 'hysteria2' and profile.get('enabled') and profile.get('tls_mode') == 'ip_acme':
                    self._sync_hysteria_ip_profile(profile_id, profile)

    def sync_management_ip_core_certificates(self):
        with self._local_lock:
            profiles = self.independent_profiles()
            managed = {key: profile for key, profile in profiles.items()
                       if profile.get('enabled') and profile.get('tls_mode') == 'management_ip'}
            if not managed:
                return
            certificate = management_https_certificate_status(self.data_dir)
            days = certificate.get('days_left') or 0
            if days <= 0:
                return
            expires = int(time.time() + days * 86400)
            changed = False
            for key, profile in managed.items():
                if profile['public_host'] != certificate['host']:
                    continue
                if abs(expires - profile['ip_certificate_expires']) <= 1 and self.publisher_runtime(key).running():
                    continue
                updated = {**profile, 'ip_certificate_expires': expires}
                try:
                    self._apply_independent_profile(key, updated)
                except (ValueError, OSError):
                    continue
                profiles[key] = updated
                changed = True
            if changed:
                self._save_independent_profiles(profiles)

    def _sync_hysteria_ip_profile(self, profile_id, previous):
        pending = previous
        service = self.publisher_runtime(profile_id)
        try:
            address = self._detect_direct_public_ip(6 if previous['address_family'] == 'ipv6' else 4)
            changed = address != previous['public_host']
            if changed:
                with self._local_lock:
                    profiles = self.independent_profiles()
                    if profiles.get(profile_id) != previous:
                        return
                    pending = {**previous, 'ip_certificate_pending': True,
                               'address_error': '公网 IP 已变化，正在为新地址申请证书'}
                    self._save_independent_profiles({**profiles, profile_id: pending})
            expires = previous['ip_certificate_expires']
            if changed or expires - time.time() <= SHORTLIVED_RENEW_SECONDS or not service.running():
                managed = self.ip_certificates().get(previous['address_family'], {})
                expires = ensure_ip_certificate(self.data_dir, address, previous['ip_certificate_email'], renew=True,
                                                challenge=managed.get('challenge', 'http'))
            updated = {**previous, 'public_host': address, 'detected_host': address,
                       'ip_certificate_expires': expires, 'ip_certificate_pending': False, 'address_error': ''}
            with self._local_lock:
                profiles = self.independent_profiles()
                if profiles.get(profile_id) != pending:
                    return
                if changed or expires != previous['ip_certificate_expires'] or not service.running():
                    self._apply_independent_profile(profile_id, updated)
                if updated != pending:
                    self._save_independent_profiles({**profiles, profile_id: updated})
        except (OSError, ValueError) as exc:
            with self._local_lock:
                profiles = self.independent_profiles()
                if profiles.get(profile_id) == pending:
                    error = str(exc) if isinstance(exc, ValueError) else '无法读取公网 IP 证书'
                    self._save_independent_profiles({**profiles, profile_id: {**pending, 'address_error': error}})

    def ip_certificates(self):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (IP_CERTIFICATES_KEY,)).fetchone()
        return json.loads(row['value']) if row else {}

    def _save_ip_certificate(self, family, record):
        certificates = self.ip_certificates()
        certificates[family] = record
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (IP_CERTIFICATES_KEY, json.dumps(certificates)))

    @staticmethod
    def _ip_certificate_version(family):
        if family not in ('ipv4', 'ipv6'):
            raise ValueError('请选择公网 IPv4 或 IPv6')
        return 6 if family == 'ipv6' else 4

    def check_ip_certificate(self, family):
        version = self._ip_certificate_version(family)
        result = ip_certificate_readiness(version)
        try:
            result['address'] = self._detect_direct_public_ip(version)
        except ValueError as error:
            result['address_error'] = str(error)
        certificate = self.ip_certificates().get(family)
        if certificate:
            result['certificate'] = certificate
        return result

    def _ip_certificate_target(self, version, address=None):
        if not address:
            return self._detect_direct_public_ip(version)
        ip = ipaddress.ip_address(address)
        if ip.version != version or not ip.is_global or ip.is_multicast or '%' in str(ip):
            raise ValueError('请输入所选地址族的公网 IP')
        try:
            with socket.socket(socket.AF_INET6 if version == 6 else socket.AF_INET) as listener:
                listener.bind((str(ip), 0))
        except OSError:
            if str(ip) != self._detect_direct_public_ip(version):
                raise ValueError('所选 IP 需要属于本机，或与检测到的公网出口一致') from None
        return str(ip)

    def stage_acme(self, family, target, email, challenge):
        if family == 'domain':
            target = validate_subscription_domain(target)
            binding = next((item for item in self.bound_domains() if item['host'] == target), None)
            if not binding:
                raise ValueError('请先绑定域名')
            self.require_domain_binding(target, binding['family'])
        else:
            target = self._ip_certificate_target(self._ip_certificate_version(family), target)
        effective = stage_certificate(self.data_dir, target, email, challenge)
        return {'target': target, 'challenge': effective}

    def configure_ip_certificate(self, family, enabled, email, *, address=None, challenge='http', verified_target=None):
        version = self._ip_certificate_version(family)
        with self._hysteria_ip_lock:
            previous = self.ip_certificates().get(family, {})
            if enabled:
                email = validate_acme_email(email)
                challenge = validate_ip_challenge(challenge)
                manual = bool(address)
                address = self._ip_certificate_target(version, address)
                if verified_target and address != verified_target:
                    raise ValueError('公网 IP 已变化，请重新验证公网可达性')
                expires = ensure_ip_certificate(self.data_dir, address, email, challenge=challenge)
                updated = {'enabled': True, 'email': email, 'address': address, 'expires': expires,
                           'manual': manual, 'challenge': challenge, 'last_check': int(time.time()), 'error': ''}
            elif previous:
                updated = {**previous, 'enabled': False, 'error': ''}
            else:
                return {}
            self._save_ip_certificate(family, updated)
            return updated

    def sync_managed_ip_certificates(self):
        with self._hysteria_ip_lock:
            for family, previous in self.ip_certificates().items():
                if not previous['enabled']:
                    continue
                now = int(time.time())
                if previous.get('retry_at', 0) > now:
                    continue
                try:
                    address = self._ip_certificate_target(self._ip_certificate_version(family),
                                                          previous['address'] if previous.get('manual') else None)
                    expires = previous['expires']
                    if address != previous['address'] or expires - now <= SHORTLIVED_RENEW_SECONDS:
                        expires = ensure_ip_certificate(self.data_dir, address, previous['email'], renew=True,
                                                        challenge=previous.get('challenge', 'http'))
                    updated = {**previous, 'address': address, 'expires': expires, 'error': '',
                               'last_check': now, 'retry_at': 0, 'failures': 0}
                except (OSError, ValueError) as exc:
                    error = str(exc) if isinstance(exc, ValueError) else '无法读取公网 IP 证书'
                    failures = previous.get('failures', 0) + 1
                    updated = {**previous, 'error': error, 'last_check': now, 'failures': failures,
                               'retry_at': now + min(6 * 3600, 900 * 2 ** min(failures - 1, 5))}
                if updated != previous:
                    self._save_ip_certificate(family, updated)

    def stop_core(self, core_id, instance=''):
        if not isinstance(core_id, str) or core_id not in PUBLISHERS:
            raise ValueError('内核无效')
        with self._local_lock:
            profiles = self.independent_profiles()
            profile_id = self._publisher_profile_id(core_id, instance, profiles)
            selected = [key for key in profiles if key == profile_id] if instance else [
                key for key in profiles if publisher_core(key) == core_id]
            legacy = self.legacy_core(core_id) if not instance else None
            if selected:
                self._detach_relay_core(core_id)
                for key in selected:
                    profiles[key]['enabled'] = False
                self._save_independent_profiles(profiles)
                for key in selected:
                    self.publisher_runtime(key).stop()
            if legacy:
                settings = self.local_node_settings()
                settings[LEGACY_CORE_FLAGS[core_id]] = False
                self.save_local_node_settings(settings)
                legacy.stop()

    def disconnect_publishers(self):
        with self._local_lock:
            profiles = self.independent_profiles()
            active = [key for key, profile in profiles.items() if profile.get('enabled')]
            settings = self.local_node_settings()
            for key in LEGACY_CORE_FLAGS.values():
                settings[key] = False
            for key in active:
                profiles[key]['enabled'] = False
            self._save_independent_profiles(profiles, legacy_settings=settings)
            for key in active:
                self.publisher_runtime(key).stop()
            for service in (self.xray, self.singbox, self.hysteria):
                service.stop()
            self.relay.stop()

    def restore_independent_cores(self, *, fixed_only=False, tls_only=False):
        with self._local_lock:
            for profile_id, profile in self.independent_profiles().items():
                if not profile.get('enabled') or (fixed_only and profile.get('egress') != 'fixed') or (tls_only and not PUBLISHERS[publisher_core(profile_id)][4]):
                    continue
                service = self.publisher_runtime(profile_id)
                try:
                    self._apply_independent_profile(profile_id, profile)
                except (OSError, ValueError) as error:
                    service.stop()
                    service._error = str(error)

    def _quic_certificate(self, settings):
        if settings.get('tls_mode') == 'management_ip':
            try:
                _, cert, key = ensure_management_https_certificate(self.data_dir, settings['public_host'])
                return str(cert), str(key)
            except (OSError, ValueError):
                return None
        acme = self.acme_settings()
        cert = acme.get('cert_path') or ''
        key = acme.get('key_path') or ''
        host = (settings.get('public_host') or '').lower()
        if cert and key and Path(key).is_file() and (acme.get('domain') or '').lower() == host and (days_until_expiry(cert) or 0) > 0:
            return cert, key
        return None

    def native_hysteria_readiness(self):
        with self._local_lock:
            settings = self.local_node_settings()
            host = settings.get('public_host') or ''
            domain_configured = bool(host and not settings.get('auto_public_host') and not is_ip_host(host))
            xray = self.xray.locate()
            hysteria = self.hysteria.locate()
            local_enabled = bool(settings.get('enabled'))
            return {
                'supported': os.name != 'nt',
                'xray_installed': bool(xray and shutil.which(xray)),
                'hysteria_installed': bool(hysteria and shutil.which(hysteria)),
                'local_enabled': local_enabled,
                'local_running': local_enabled and self.xray.running(),
                'domain_configured': domain_configured,
                'certificate_ready': domain_configured and self._quic_certificate(settings) is not None,
            }

    def _start_quic(self, settings):
        certificate = self._quic_certificate(settings)
        profiles = self.independent_profiles()
        if 'sing-box' not in profiles:
            try:
                legacy = settings if settings.get('legacy_singbox_enabled', True) else {**settings, 'enabled': False}
                self.singbox.apply(legacy, certificate)
            except (OSError, ValueError):
                pass
        try:
            if 'hysteria2' not in profiles:
                self.hysteria.apply(settings, certificate)
        except (OSError, ValueError):
            pass

    def _legacy_singbox_view(self, settings):
        enabled = bool(settings.get('enabled') and settings.get('legacy_singbox_enabled', True)
                       and 'sing-box' not in self.independent_profiles())
        if not enabled:
            settings = {**settings, 'enabled': False}
        return {**self.singbox.public_view(settings), 'enabled': enabled}

    def set_native_hysteria(self, enabled):
        with self._local_lock:
            settings = self.local_node_settings()
            if enabled and 'hysteria2' in self.independent_profiles():
                raise ValueError('请在「内核管理」使用原生 Hysteria 2 的独立配置')
            if enabled and (not settings.get('enabled') or not self.xray.running()):
                raise ValueError('请先启动本地网络节点，原生 Hysteria 2 通过 Xray 使用其出口设置')
            settings['native_hysteria_enabled'] = enabled
            if enabled and not settings.get('native_hysteria_password'):
                settings['native_hysteria_password'] = secrets.token_urlsafe(18)
            self.hysteria.apply(settings, self._quic_certificate(settings) if enabled else None)
            self.save_local_node_settings(settings)

    def _reload_quic(self):
        settings = self.local_node_settings()
        if settings.get('enabled'):
            self._start_quic(settings)
        self.restore_independent_cores(tls_only=True)

    @staticmethod
    def _published_link(link, endpoint):
        if not endpoint:
            return link
        parts = urlsplit(link)
        host = endpoint['public_host']
        port = endpoint.get('public_port', endpoint['port'])
        if host == parts.hostname and port == parts.port:
            return link
        address = f'[{host}]' if ':' in host else host
        auth, separator, _ = parts.netloc.rpartition('@')
        return parts._replace(netloc=f'{auth}{separator}{address}:{port}').geturl()

    def subscription(self):
        try:
            self.sync_public_endpoint(force=True)
        except ValueError:
            pass
        with self.connect() as db:
            upstreams = self._upstream_nodes(db)
            disabled = {row['id'] for row in db.execute('SELECT id FROM disabled_nodes')}
        with self._local_lock:
            local = self.local_links()
            independent = self.independent_endpoints()
        links = [link for identifier, (link, _) in upstreams.items() if identifier not in disabled and link not in local]
        links.extend(self._published_link(link, independent.get(node_id(link), {}))
                     for link in local if node_id(link) not in disabled)
        links = list(dict.fromkeys(links))
        return encode_subscription(links) if links else ''

    def add_manual(self, content):
        if not isinstance(content, str): raise ValueError('节点内容无效')
        links = decode_subscription(content.encode('utf-8'))
        with self.connect() as db:
            db.executemany('INSERT OR IGNORE INTO manual_nodes VALUES (?, ?)',
                           ((upstream_node_id(link), link) for link in links))

    def remove_manual(self, identifier):
        with self.connect() as db:
            return db.execute('DELETE FROM manual_nodes WHERE id=?', (identifier,)).rowcount > 0

    def _upstream_nodes(self, db):
        nodes = {}
        for row in db.execute('SELECT id,links FROM sources WHERE enabled=1 ORDER BY id'):
            for link in json.loads(row['links']):
                nodes.setdefault(upstream_node_id(link), (link, set()))[1].add(f"source:{row['id']}")
        for row in db.execute('SELECT id,link FROM manual_nodes ORDER BY rowid'):
            nodes.setdefault(row['id'], (row['link'], set()))[1].add('manual')
        return nodes

    def _node_entries(self, upstreams):
        with self._local_lock:
            local_links = self.local_links()
            independent = self.independent_endpoints()
        local = {node_id(link): (link, {'local'}) for link in local_links}
        for link, groups in local.values():
            identifier = upstream_node_id(link)
            if identifier in upstreams and upstreams[identifier][0] == link:
                groups.update(upstreams.pop(identifier)[1])
        return upstreams | local, independent

    def nodes(self):
        with self.connect() as db:
            upstreams = self._upstream_nodes(db)
            disabled = {row['id'] for row in db.execute('SELECT id FROM disabled_nodes')}
            names = dict(db.execute('SELECT id,name FROM upstream_names'))
        entries, independent = self._node_entries(upstreams)
        result = []
        for index, (identifier, (link, groups)) in enumerate(entries.items(), 1):
            parts = urlsplit(link)
            title = unquote(parts.fragment)
            if parts.scheme == 'vmess':
                title = (vmess_fields(link) or {}).get('ps', title)
            title = title.strip()[:120] if isinstance(title, str) else ''
            if any(0xd800 <= ord(char) <= 0xdfff for char in title):
                title = ''
            title = title or f'节点 {index}'
            local = 'local' in groups
            node = {'id': identifier, 'title': title, 'protocol': parts.scheme.lower(),
                    'enabled': identifier not in disabled, 'manual': 'manual' in groups and not local,
                    'local': local, 'groups': sorted(groups)}
            if node['local']:
                node['port'] = parts.port
                node['export_formats'] = EXPORT_FORMATS.get(node['protocol'], ())
                node.update(independent.get(identifier, {}))
                if node.get('core_id'):
                    node['security'] = self.security.publisher_status(node.get('profile_id', node['core_id']))
            elif identifier in names:
                node.update(title=names[identifier], custom_name=names[identifier])
            result.append(node)
        return result

    def batch_probe_specs(self, groups, node_ids):
        with self.connect() as db:
            upstreams = self._upstream_nodes(db)
            allowed = {f"source:{row['id']}" for row in db.execute('SELECT id FROM sources')} | {'manual', 'local'}
        entries, independent = self._node_entries(upstreams)
        if node_ids is not None:
            if groups is not None or not isinstance(node_ids, list) or not all(isinstance(item, str) for item in node_ids):
                raise ValueError('测试节点范围无效')
            selected = set(node_ids)
            if not selected.issubset(entries):
                raise ValueError('所选节点已变更，请刷新后重试')
        else:
            if groups is not None and (not isinstance(groups, list) or not all(isinstance(group, str) for group in groups)
                                       or not set(groups).issubset(allowed)):
                raise ValueError('订阅组无效')
            selected_groups = allowed if groups is None else set(groups)
            selected = {identifier for identifier, (_, memberships) in entries.items() if selected_groups.intersection(memberships)}
        return {identifier: (self._published_link(link, independent.get(identifier, {})),
                             independent.get(identifier, {}).get('core_id'))
                for identifier, (link, _) in entries.items() if identifier in selected}

    def node_link(self, identifier, *, local_only=False):
        return self.node_probe_spec(identifier, local_only=local_only)[0]

    def node_probe_spec(self, identifier, *, local_only=False):
        if not local_only:
            with self.connect() as db:
                if upstream := self._upstream_nodes(db).get(identifier):
                    return upstream[0], None
        with self._local_lock:
            for link in self.local_links():
                if node_id(link) == identifier:
                    endpoint = self.independent_endpoints().get(identifier, {})
                    return self._published_link(link, endpoint), endpoint.get('core_id')
        raise ValueError('节点不存在')

    def set_node_enabled(self, identifier, enabled):
        if not isinstance(identifier, str) or not isinstance(enabled, bool): raise ValueError('节点设置无效')
        if not any(node['id'] == identifier for node in self.nodes()): raise ValueError('节点不存在')
        with self.connect() as db:
            if enabled: db.execute('DELETE FROM disabled_nodes WHERE id=?', (identifier,))
            else: db.execute('INSERT OR IGNORE INTO disabled_nodes VALUES (?)', (identifier,))

    def set_upstream_name(self, identifier, name):
        if not isinstance(identifier, str) or not isinstance(name, str):
            raise ValueError('节点设置无效')
        name = name.strip()
        if name:
            name = validate_node_name(name)
            if not name.isprintable():
                raise ValueError('节点名称无效')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if identifier not in self._upstream_nodes(db):
                raise ValueError('上游节点不存在')
            if name:
                db.execute('INSERT OR REPLACE INTO upstream_names VALUES (?,?)', (identifier, name))
            else:
                db.execute('DELETE FROM upstream_names WHERE id=?', (identifier,))

    def refresh_auto_public_host(self, settings):
        settings = dict(settings)
        if not settings.get('auto_public_host'):
            return settings, False
        prefer = settings.get('public_host_prefer') or 'ipv4'
        if prefer not in ('ipv4', 'ipv6', 'ipv4_first'):
            prefer = 'ipv4'
        host = validate_public_host(detect_public_host(prefer, timeout=3, direct=True))
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
        relay = self.relay_settings()
        if relay.get('enabled') and relay['domain'] == host.lower():
            raise ValueError('VPS 中转域名由 VPS 的公网地址提供解析，请关闭家宽 DDNS')
        if any(profile.get('enabled') and profile.get('ddns') and profile['public_host'] == host.lower()
               for profile in self.independent_profiles().values()):
            raise ValueError('此域名已由独立节点更新，请关闭独立节点 DDNS 后再启用')
        prefer = settings.get('public_host_prefer') or 'ipv4'
        ipv4 = ipv6 = None
        if prefer in ('ipv4', 'ipv4_first', 'dual'):
            try:
                ipv4 = self._detect_direct_public_ip(4)
            except ValueError:
                pass
        if prefer in ('ipv6', 'dual'):
            if os.environ.get('HOPWORLD_DDNS_PROVIDER', 'webhook').strip().lower() == 'dnspod':
                raise ValueError('当前 DNSPod 更新方式仅支持 A 记录，AAAA 请使用 DNS Webhook')
            try:
                ipv6 = self._detect_direct_public_ip(6)
            except ValueError:
                pass
        elif prefer == 'ipv4_first' and not ipv4:
            try:
                ipv6 = self._detect_direct_public_ip(6)
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

    def _auto_pool_entries(self, settings, selected_id=None):
        pool = []
        identifiers = []
        with self.connect() as db:
            upstreams = self._upstream_nodes(db)
            disabled = {row['id'] for row in db.execute('SELECT id FROM disabled_nodes')}
        local_ids = {upstream_node_id(link) for link in self.local_links()}
        for identifier, (link, _) in upstreams.items():
            if identifier in disabled or identifier in local_ids or (selected_id is not None and selected_id != identifier):
                continue
            try:
                proxy = outbound(link)
                endpoint = (proxy['settings'].get('vnext') or proxy['settings']['servers'])[0]
                host = endpoint['address'].lower().rstrip('.')
                if host == 'localhost' or host.endswith('.localhost'):
                    continue
                try:
                    address = ipaddress.ip_address(host)
                    if address.is_loopback or address.is_unspecified:
                        continue
                except ValueError:
                    pass
                if host == (settings.get('public_host') or '').lower().rstrip('.') and endpoint['port'] == settings.get('port'):
                    continue
                pool.append(proxy)
                identifiers.append(identifier)
            except (UnsupportedNode, ValueError):
                continue
        return pool, identifiers

    def _auto_pool(self, settings):
        return self._auto_pool_entries(settings)[0]

    def _refresh_egress_pool(self, settings):
        if settings.get('egress_mode') == 'fixed':
            selected = settings.get('upstream_id')
            settings['proxy_pool'] = self._auto_pool_entries(settings, selected)[0] if selected else []
            settings.pop('auto_pool_ids', None)
        else:
            settings['proxy_pool'], settings['auto_pool_ids'] = self._auto_pool_entries(settings)

    def reconcile_egress_pool(self):
        with self._local_lock:
            self.restore_independent_cores(fixed_only=True)
            settings = self.local_node_settings()
            if not settings.get('enabled') or settings.get('egress_mode') not in ('auto', 'fixed'):
                return
            previous_pool, previous_ids = settings.get('proxy_pool'), settings.get('auto_pool_ids')
            self._refresh_egress_pool(settings)
            changed = settings['proxy_pool'] != previous_pool
            if not changed and settings.get('auto_pool_ids') == previous_ids:
                return
            if changed:
                try:
                    self.xray.apply(settings, rollback=False)
                except (OSError, ValueError):
                    self.xray.stop()
                    self.singbox.stop()
                    self.hysteria.stop()
                    raise
                self._start_quic(settings)
            self.save_local_node_settings(settings)

    def configure_local_node(self, *args, **kwargs):
        with self._local_lock:
            return self._configure_local_node(*args, **kwargs)

    def _configure_local_node(self, enabled, public_host=None, port=None, name=None,
                             auto_public_host=None, public_host_prefer=None, ddns_enabled=None,
                             acme_enabled=None, acme_email=None, acme_challenge=None,
                             egress_mode=None, local_proxy=None, auto_target=None, auto_interval=None,
                             upstream_id=None):
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
            if prefer not in ('ipv4', 'ipv6', 'ipv4_first', 'dual'):
                raise ValueError('公网地址偏好无效')
            settings['public_host_prefer'] = prefer
        if settings.get('auto_public_host') and settings.get('public_host_prefer') == 'dual':
            raise ValueError('自动获取公网 IP 时请选择 IPv4 或 IPv6')
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
            if egress_mode not in ('default', 'auto', 'local_proxy', 'fixed'):
                raise ValueError('出口模式无效')
            settings['egress_mode'] = egress_mode
        if upstream_id is not None:
            if not isinstance(upstream_id, str) or (upstream_id and not re.fullmatch(r'[0-9a-f]{64}', upstream_id)):
                raise ValueError('上游节点标识无效')
            settings['upstream_id'] = upstream_id
        if auto_target is not None:
            if auto_target not in ('google', 'youtube', 'cloudflare'):
                raise ValueError('自动换线测试目标无效')
            settings['auto_target'] = auto_target
        if auto_interval is not None:
            if type(auto_interval) is not int or auto_interval not in (30, 60, 120, 300):
                raise ValueError('自动换线探测间隔无效')
            settings['auto_interval'] = auto_interval
        if local_proxy is not None:
            if not isinstance(local_proxy, str) or len(local_proxy) > 20:
                raise ValueError('本机代理无效')
            settings['local_proxy'] = local_proxy
        mode = settings.get('egress_mode', 'default')
        if mode == 'auto':
            self._refresh_egress_pool(settings)
            target = settings.get('auto_target', 'google')
            settings['auto_probe_url'] = TARGETS[target][1] or TEST_URL
        elif mode == 'fixed':
            self._refresh_egress_pool(settings)
        elif mode == 'local_proxy':
            settings.pop('auto_pool_ids', None)
            try:
                protocol, raw_port = (settings.get('local_proxy') or '').split(':')
                proxy_port = int(raw_port)
                reserved = {settings.get('port'), LOCAL_EGRESS_SOCKS_PORT, *quic_ports(settings.get('port'))}
                if protocol not in ('http', 'socks') or not 1 <= proxy_port <= 65535 or proxy_port in reserved:
                    raise ValueError()
            except ValueError:
                raise ValueError('请选择有效的本机代理，且不能使用本地节点入口端口') from None
            if enabled and not any(item.get('protocol') == protocol and item.get('port') == proxy_port for item in discover([proxy_port])):
                raise ValueError('本机代理不可用或无法连接本地地址，请重新扫描')
            settings['proxy_pool'] = [{'protocol': protocol, 'settings': {'servers': [{'address': '127.0.0.1', 'port': proxy_port}]}}]
            settings['auto_probe_url'] = TEST_URL
        else:
            settings.pop('proxy_pool', None)
            settings.pop('auto_pool_ids', None)
        if enabled:
            if self.independent_profiles().get('xray-core', {}).get('enabled'):
                raise ValueError('请在「内核管理」配置已启用的独立 Xray')
            entry_host = settings.get('public_host') or ''
            if entry_host and not settings.get('auto_public_host') and not is_ip_host(entry_host):
                if self.relay_settings().get('domain') == entry_host:
                    raise ValueError('VPS 域名请使用独立发布节点，或改用指向本机的域名')
                self.require_domain_binding(entry_host, settings.get('public_host_prefer') or 'ipv4')
            if settings.get('ddns_enabled'):
                if not settings.get('public_host') or is_ip_host(settings.get('public_host', '')):
                    raise ValueError('DDNS 模式下请填写域名作为公网地址')
                settings, _ = self.refresh_ddns(settings)
            elif settings.get('auto_public_host'):
                settings, _ = self.refresh_auto_public_host(settings)
                if mode == 'fixed':
                    self._refresh_egress_pool(settings)
            if not settings.get('public_host'):
                raise ValueError('请填写域名或 IP，或选择「自动获取公网 IP」')
            self.xray.locate()
            self.singbox.locate()
            settings = self.xray.ensure_credentials(settings)
            settings['enabled'] = True
            self.xray.apply(settings)
            self._start_quic(settings)
            self.save_local_node_settings(settings)
        else:
            settings['enabled'] = False
            self.xray.apply(settings)
            self._start_quic(settings)
            self.save_local_node_settings(settings)
        view = self.xray.public_view(settings)
        view['quic'] = self._legacy_singbox_view(settings)
        view['native_hysteria'] = self.hysteria.public_view(settings)
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

    def refresh_singbox(self):
        with self._local_lock:
            if not self.singbox.locate():
                raise ValueError('未找到 sing-box，请在服务器安装已验证版本后重新检测')
            settings = self.local_node_settings()
            self._start_quic(settings)
            view = self.xray.public_view(settings)
            view['quic'] = self._legacy_singbox_view(settings)
            return view

    def local_node_status(self):
        settings = self.local_node_settings()
        view = self.xray.public_view(settings)
        view['quic'] = self._legacy_singbox_view(settings)
        view['native_hysteria'] = self.hysteria.public_view(settings)
        view['independent_endpoints'] = list(self.independent_endpoints().values())
        view['legacy_singbox_enabled'] = bool(settings.get('legacy_singbox_enabled', settings.get('enabled')))
        return self.attach_dns_hints(view, settings)

    def local_traffic_status(self):
        with self._local_lock:
            settings = self.local_node_settings()
            if not settings.get('enabled'):
                return {'state': 'inactive'}
            if not self.xray.running():
                return {'state': 'stopped'}
            traffic = self.xray.node_traffic()
            return {'state': 'ok', **traffic} if traffic is not None else {'state': 'unavailable'}

    def publisher_traffic_status(self):
        publishers = []
        for identifier, profile in self.independent_profiles().items():
            if not profile.get('enabled'):
                continue
            core = publisher_core(identifier)
            ports = publisher_ports(core, profile)
            protocol, port = next(iter(ports.items())) if len(ports) == 1 else ('', 0)
            service = self.publisher_runtime(identifier)
            if service.running():
                publishers.append((identifier, core, protocol, port, service.traffic_counters()))
        return publishers

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
                    interval = settings.get('auto_interval', 60)
                    state = 'stale' if time.time() - info['last_try_time'] > max(180, interval * 3) else ('ok' if info['alive'] else 'failed')
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

    def record_certificate_event(self, domain, action, detail):
        with self.connect() as db:
            db.execute('INSERT INTO certificate_history (domain, action, detail, timestamp) VALUES (?, ?, ?, ?)',
                       (domain, action, detail, int(time.time())))
            db.execute('DELETE FROM certificate_history WHERE id < (SELECT id FROM certificate_history ORDER BY id DESC LIMIT 1 OFFSET 29)')

    def certificate_domain(self, expected, settings):
        domain = validate_public_host(expected)
        acme = self.acme_settings()
        current = acme.get('bound_domain') or acme.get('domain') or (settings.get('public_host') or '').strip().rstrip('.').lower()
        if is_ip_host(domain) or domain != current:
            raise ValueError('公网域名已变更，请刷新后重试')
        return domain

    def certificate_download(self, expected):
        domain = self.certificate_domain(expected, self.local_node_settings())
        settings = self.acme_settings()
        if settings.get('domain') != domain or not settings.get('cert_path'):
            raise ValueError('该域名暂无可下载的证书')
        path, _ = cert_paths(self.data_dir.resolve(), domain, manual=settings.get('source') == 'manual')
        try:
            if path.resolve() != path or not path.is_file():
                raise ValueError('该域名暂无可下载的证书')
            with path.open('rb') as file:
                data = file.read(256 * 1024 + 1)
        except (OSError, RuntimeError) as exc:
            raise ValueError('证书文件无法读取') from exc
        if len(data) > 256 * 1024 or not CERTIFICATE_PEM.fullmatch(data):
            raise ValueError('证书文件格式无效')
        return domain, data

    def acme_status(self):
        settings = self.acme_settings()
        error = settings.get('error') or ''
        cert_path = (settings.get('cert_path') or '').strip()
        enabled = bool(settings.get('enabled'))
        days_left = settings.get('days_left')
        available = bool(cert_path and Path(cert_path).is_file())
        live = None
        if available:
            live = days_until_expiry(cert_path)
            days_left = round(live, 1) if live is not None else None
        if enabled and live is not None and live <= 0:
            state = 'expired'
        elif error:
            state = 'error'
        elif enabled and live is not None:
            state = 'ok'
        elif enabled:
            state = 'pending'
        else:
            state = ''
        domain = settings.get('bound_domain') or settings.get('domain') or (self.local_node_settings().get('public_host') or '').strip().rstrip('.').lower()
        with self.connect() as db:
            history = [dict(row) for row in db.execute(
                'SELECT action, detail, timestamp FROM certificate_history WHERE domain=? ORDER BY id DESC LIMIT 5',
                (domain,),
            )]
        return {
            'enabled': enabled,
            'source': settings.get('source') or 'acme',
            'automatic': enabled and settings.get('source') != 'manual',
            'email': settings.get('email') or '',
            'domain': settings.get('domain') or '',
            'days_left': days_left,
            'cert_path': cert_path,
            'key_path': settings.get('key_path') or '',
            'error': error,
            'state': state,
            'download_available': available and settings.get('domain') == domain,
            'certificate_valid': live is not None and live > 0,
            'history': history,
            'challenge': settings.get('challenge') or 'auto',
            'challenge_effective': settings.get('challenge_effective') or '',
            'renew_days': int(os.environ.get('HOPWORLD_ACME_RENEW_DAYS', '3')),
        }

    def configure_acme(self, enabled, email=None, challenge=None, selected_domain=None):
        settings = self.acme_settings()
        manual = settings.get('source') == 'manual'
        if manual and (not enabled or selected_domain is None):
            return self.acme_status()
        settings['enabled'] = bool(enabled)
        settings['source'] = 'acme'
        if email is not None:
            settings['email'] = validate_acme_email(email) if str(email).strip() else ''
        if challenge is not None:
            settings['challenge'] = validate_acme_challenge(challenge)
        if settings['enabled']:
            if not settings.get('email'):
                raise ValueError('请填写 ACME 邮箱')
            if selected_domain is not None:
                selected_domain = validate_subscription_domain(selected_domain)
                binding = next((item for item in self.bound_domains() if item['host'] == selected_domain), None)
                if not binding:
                    raise ValueError('请先在「域名与证书」绑定要申请证书的域名')
                self.require_domain_binding(selected_domain, binding['family'])
                settings['bound_domain'] = selected_domain
            domain = settings.get('bound_domain') or (self.local_node_settings().get('public_host') or '').strip()
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
            except (ValueError, OSError) as exc:
                message = short_acme_error(str(exc)) if isinstance(exc, ValueError) else '证书文件无法读取'
                if settings.get('cert_path') and (manual or selected_domain is not None and domain != settings.get('domain')):
                    self.record_certificate_event(domain, '证书签发检查', message)
                    raise ValueError(message) from exc
                settings['error'] = message
                if domain != settings.get('domain'):
                    settings.update(days_left=None, cert_path='', key_path='', certificate_revision='')
                settings['domain'] = domain
        self.save_acme_settings(settings)
        if settings['enabled']:
            self.record_certificate_event(domain, '证书签发检查', settings.get('error') or '证书已就绪')
        if not settings.get('error'):
            self._reload_quic()
        return self.acme_status()

    def import_manual_certificate(self, domain, chain, private_key):
        with self._local_lock:
            domain = validate_subscription_domain(domain)
            if not any(item['host'] == domain for item in self.bound_domains()):
                raise ValueError('请先在「域名与证书」绑定该域名')
            settings = self.acme_settings()
            try:
                settings.update(import_certificate(self.data_dir, domain, chain, private_key))
            except OSError as exc:
                raise ValueError('无法写入证书文件，请检查数据目录和文件权限') from exc
            self.save_acme_settings(settings)
            self.record_certificate_event(domain, '手动导入证书', '证书已生效')
            self._reload_quic()
            return self.acme_status()

    def sync_acme_certificate(self):
        with self._local_lock:
            return self._sync_acme_certificate()

    def renew_acme_certificate(self, expected):
        with self._local_lock:
            domain = self.certificate_domain(expected, self.local_node_settings())
            settings = self.acme_settings()
            if settings.get('source') == 'manual':
                raise ValueError('手动证书请在到期前重新导入')
            if not settings.get('enabled') or settings.get('domain') != domain or not settings.get('cert_path'):
                raise ValueError('请先为该域名申请证书')
            try:
                updated = sync_certificate(self.data_dir, domain, settings.get('email'), True,
                                           settings.get('challenge', 'auto'), force=True)
            except (ValueError, OSError) as exc:
                message = short_acme_error(str(exc)) if isinstance(exc, ValueError) else '证书文件无法读取'
                settings['error'] = message
                self.save_acme_settings(settings)
                self.record_certificate_event(domain, '立即续签', message)
                if (days_until_expiry(settings['cert_path']) or 0) <= 0:
                    self._reload_quic()
                raise ValueError(message) from exc
            settings.update(updated)
            self.save_acme_settings(settings)
            self.record_certificate_event(domain, '立即续签', '续签成功')
            self._reload_quic()

    def _sync_acme_certificate(self):
        settings = self.acme_settings()
        if not settings.get('enabled'):
            return False
        if settings.get('source') == 'manual':
            if (days_until_expiry(settings.get('cert_path') or '') or 0) <= 0:
                self._reload_quic()
            return False
        domain = settings.get('bound_domain') or settings.get('domain') or (self.local_node_settings().get('public_host') or '').strip()
        if not domain or is_ip_host(domain):
            settings['error'] = '需要域名'
            self.save_acme_settings(settings)
            return False
        previous_revision = settings.get('certificate_revision')
        previous_error = settings.get('error')
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
        except (ValueError, OSError) as exc:
            settings['error'] = short_acme_error(str(exc)) if isinstance(exc, ValueError) else '证书文件无法读取'
            self.save_acme_settings(settings)
            if settings['error'] != previous_error:
                self.record_certificate_event(domain, '自动续签检查', settings['error'])
            if (days_until_expiry(settings.get('cert_path') or '') or 0) <= 0:
                self._reload_quic()
            return False
        self.save_acme_settings(settings)
        if previous_error or (previous_revision and previous_revision != view.get('certificate_revision')):
            self.record_certificate_event(domain, '自动续签检查', '有效证书已同步')
        self._reload_quic()
        return True

    def restore_local_node(self):
        settings = self.local_node_settings()
        if settings.get('enabled') and not self.independent_profiles().get('xray-core', {}).get('enabled'):
            self.xray.locate()
            self.singbox.locate()
            settings = self.xray.ensure_credentials(settings)
            if settings.get('ddns_enabled'):
                settings, _ = self.refresh_ddns(settings)
            elif settings.get('auto_public_host'):
                settings, _ = self.refresh_auto_public_host(settings)
            if settings.get('egress_mode') in ('auto', 'fixed'):
                self._refresh_egress_pool(settings)
            self.save_local_node_settings(settings)
            try:
                self.xray.apply(settings, rollback=False)
            except (OSError, ValueError):
                self.xray.stop()
                self.singbox.stop()
                self.hysteria.stop()
                raise
            self._reload_quic()

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


def _managed_core(store, core_id):
    if not isinstance(core_id, str) or not core_id:
        raise ValueError('内核无效')
    return store.core_runtime(core_id)


def make_handler(store, password, public_subscription=None, *, request_restart=None, ready=None,
                 management_local_only=False, management_https_host=''):
    probe_results = {}
    probe_lock = threading.Lock()
    probe_slots = threading.BoundedSemaphore(4)
    backup_lock = threading.Lock()
    autostart_lock = threading.Lock()
    restart_lock = threading.Lock()
    instance = secrets.token_urlsafe(16)
    sessions = {}
    sessions_lock = threading.Lock()
    account_lock = threading.Lock()
    jobs = {'update': {'running': False, 'done': 0, 'total': 0}, 'probe': {'running': False, 'done': 0, 'total': 0}}
    jobs_lock = threading.Lock()
    cancellations = {'update': threading.Event(), 'probe': threading.Event()}

    def probe_node(spec, target, mode):
        link, core_id = spec
        scheme = urlsplit(link).scheme
        if mode == 'light' and scheme in LIGHT_CLIENT_SCHEMES:
            binary = shutil.which(str(store.data_dir / 'bin' / 'hopworld-probe')) or shutil.which('hopworld-probe')
            if binary:
                result = probe_light(link, binary, target)
                if result['status'] != 'unsupported':
                    return result
        if core_id:
            binary = core_client_binary(store.data_dir, core_id, store.independent[core_id].locate())
            return probe(link, target=target, core=core_id, client_binary=binary)
        if scheme in HYSTERIA_SCHEMES:
            binary = store.hysteria.locate()
            if binary and shutil.which(binary):
                return probe(link, target=target, core='hysteria2', client_binary=binary)
            result = probe(link, target=target, singbox_binary=store.singbox.locate())
            if result['status'] == 'unavailable':
                return {**result, 'detail': '完整测试需要 Hysteria 2 或 sing-box。请在「内核管理」下载对应内核。'}
            return result
        return probe(link, target=target, binary=store.xray.binary, singbox_binary=store.singbox.binary)

    def probe_metadata(mode, target):
        if mode not in ('light', 'full'):
            raise ValueError('测试方式无效')
        if not isinstance(target, str) or target not in TARGETS:
            raise ValueError('测试目标无效')
        return {'mode': mode, 'target': TARGETS[target][0]}

    def start_job(kind, target=None, groups=None, node_ids=None, mode='full'):
        metadata = probe_metadata(mode, target) if kind == 'probe' else {}
        with jobs_lock:
            if jobs[kind]['running']: raise ValueError('任务已经在运行')
            if kind == 'update':
                items = [source['id'] for source in store.sources() if source['enabled']]
            else:
                items = store.batch_probe_specs(groups, node_ids)
            if not items: raise ValueError('没有可测试的节点' if kind == 'probe' else '没有可更新的订阅')
            cancellations[kind].clear()
            jobs[kind] = {'running': True, 'done': 0, 'total': len(items), 'failed': 0}
            if kind == 'probe':
                jobs[kind].update(metadata, groups=groups, active={})
                with probe_lock:
                    for identifier in items:
                        if probe_results.get(identifier, {}).get('status') != 'testing':
                            probe_results.pop(identifier, None)

        def run_item(item, position=None):
            if cancellations[kind].is_set(): return
            failed = False
            if kind == 'update':
                try:
                    store.refresh(item)
                    store.reconcile_egress_pool()
                except ValueError: failed = True
            else:
                owns_probe = False
                try:
                    with probe_slots:
                        with jobs_lock, probe_lock:
                            if cancellations[kind].is_set(): return
                            if probe_results.get(item, {}).get('status') == 'testing':
                                raise ValueError('节点正在测试')
                            probe_results[item] = {'status': 'testing', 'detail': '正在测试', **metadata}
                            jobs[kind]['active'][item] = position
                            owns_probe = True
                        result = probe_node(items[item], target, mode)
                except Exception:
                    result = {'status': 'failed', 'detail': '测试失败'}
                failed = result['status'] != 'ok'
            with jobs_lock:
                if kind == 'probe' and owns_probe:
                    with probe_lock: probe_results[item] = {**result, **metadata}
                    del jobs[kind]['active'][item]
                jobs[kind]['done'] += 1
                if failed: jobs[kind]['failed'] += 1

        def worker():
            try:
                if kind == 'probe':
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        list(pool.map(run_item, items, range(1, len(items) + 1)))
                else:
                    for item in items:
                        if cancellations[kind].is_set(): break
                        run_item(item)
            finally:
                with jobs_lock:
                    jobs[kind]['running'] = False
                    jobs[kind].pop('stopping', None)
                    jobs[kind]['cancelled'] = cancellations[kind].is_set()
        threading.Thread(target=worker, daemon=True).start()


    def start_probe(identifier, target='google', mode='full'):
        if not isinstance(identifier, str): raise ValueError('节点不存在')
        metadata = probe_metadata(mode, target)
        spec = store.node_probe_spec(identifier)
        with probe_lock:
            if probe_results.get(identifier, {}).get('status') == 'testing': raise ValueError('节点正在测试')
            if not probe_slots.acquire(blocking=False): raise ValueError('测试任务已满')
            probe_results[identifier] = {'status': 'testing', 'detail': '正在测试', **metadata}
        def worker():
            try:
                result = probe_node(spec, target, mode)
            except Exception:
                result = {'status': 'failed', 'detail': '测试失败'}
            finally:
                probe_slots.release()
            with probe_lock: probe_results[identifier] = {**result, **metadata}
        threading.Thread(target=worker, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Subscription URLs and credentials must not enter access logs.

        def reply(self, status, body, content_type='application/json; charset=utf-8', headers=None):
            data = body if isinstance(body, bytes) else body.encode('utf-8')
            self.reply_headers(status, len(data), content_type, headers)
            self.wfile.write(data)

        def reply_headers(self, status, length, content_type, headers=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(length))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            for key, value in (headers or {}).items(): self.send_header(key, value)
            self.end_headers()

        def database_backup(self):
            if not backup_lock.acquire(blocking=False):
                return self.json(409, {'error': '备份正在生成，请稍后再试'})
            try:
                with tempfile.TemporaryDirectory(prefix='hopworld-backup-') as directory:
                    path = Path(directory) / 'router.db'
                    try:
                        with store.connect() as source:
                            snapshot(source, path)
                    except (OSError, sqlite3.Error):
                        return self.json(500, {'error': '无法生成数据库备份'})
                    with path.open('rb') as archive:
                        filename = time.strftime('hopworld-router-%Y%m%d-%H%M%S.db')
                        self.reply_headers(200, path.stat().st_size, 'application/vnd.sqlite3',
                                           {'Content-Disposition': f'attachment; filename="{filename}"'})
                        try:
                            shutil.copyfileobj(archive, self.wfile)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
            finally:
                backup_lock.release()

        def json(self, status, obj):
            self.reply(status, json.dumps(obj, ensure_ascii=False))

        def admin_account(self):
            saved = store.admin_account()
            if saved is not None:
                return saved
            record = store.admin_password_record() if password == DEFAULT_PASSWORD else ''
            return 'admin', record

        def credentials_match(self, user, supplied, account):
            if not isinstance(user, str) or not isinstance(supplied, str):
                return False
            username, verifier = account
            if not hmac.compare_digest(user.encode(), username.encode()):
                return False
            return matches_password(password, verifier, supplied)

        def needs_initial_password(self, account):
            return password == DEFAULT_PASSWORD and not account[1]

        def local_initialization(self):
            host = urlsplit('//' + self.headers.get('Host', '')).hostname
            if not host:
                return False
            try:
                return ipaddress.ip_address(self.client_address[0]).is_loopback and (
                    host == 'localhost' or ipaddress.ip_address(host).is_loopback
                )
            except ValueError:
                return False

        def session_id(self):
            try:
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
            except CookieError:
                return ''
            cookie = cookies.get(SESSION_COOKIE)
            return cookie.value if cookie else ''

        def session_cookie(self, session, seconds):
            secure = self.secure_management_request() or self.headers.get('Origin', '').startswith('https://')
            return (f'{SESSION_COOKIE}={session}; Max-Age={seconds}; Path=/; HttpOnly; SameSite=Strict'
                    + ('; Secure' if secure else ''))

        def secure_management_request(self):
            return getattr(self.server, 'tls', None) or (
                management_local_only and management_https_host
                and ipaddress.ip_address(self.client_address[0]).is_loopback
                and self.headers.get('Host') == management_https_host
                and self.headers.get('X-Forwarded-Proto') == 'https'
            )

        def login_page(self):
            page = (STATIC / 'login.html').read_bytes()
            if self.local_initialization() and self.needs_initial_password(self.admin_account()):
                page = page.replace(b'data-initial-setup="false"', b'data-initial-setup="true"', 1)
            self.reply(200, page, 'text/html; charset=utf-8', PAGE_HEADERS)

        def authorized(self, *, allow_initial=False):
            self.initial_session = False
            session = self.session_id()
            if session:
                with sessions_lock:
                    expires, initial = sessions.get(session, (0, False))
                if expires > time.monotonic():
                    if initial and not self.local_initialization():
                        self.json(403, {'error': '请通过本机地址或 SSH 隧道完成首次登录'})
                        return False
                    if initial and not allow_initial:
                        self.json(403, {'error': '请先修改默认密码'})
                        return False
                    self.initial_session = initial
                    return True
            auth = self.headers.get('Authorization', '')
            try:
                kind, encoded = auth.split(' ', 1)
                decoded = base64.b64decode(encoded, validate=True).decode('utf-8')
                user, supplied = decoded.split(':', 1)
                account = self.admin_account()
                ok = (kind.lower() == 'basic' and not self.needs_initial_password(account)
                      and self.credentials_match(user, supplied, account))
            except (ValueError, UnicodeError):
                ok = False
            if not ok:
                if getattr(self, 'browser_login', False):
                    self.login_page()
                elif self.headers.get('Accept') == 'application/json':
                    self.json(401, {'error': '登录已失效，请重新登录'})
                else:
                    self.reply(401, 'Authentication required', 'text/plain; charset=utf-8', {'WWW-Authenticate': 'Basic realm="HopWorld Router", charset="UTF-8"'})
            return ok

        def create_session(self, *, initial=False, replace_all=False):
            token = secrets.token_urlsafe(32)
            now = time.monotonic()
            seconds = 15 * 60 if initial else SESSION_SECONDS
            with sessions_lock:
                if replace_all:
                    sessions.clear()
                else:
                    sessions.pop(self.session_id(), None)
                    for key in [key for key, (expires, _) in sessions.items() if expires <= now]:
                        sessions.pop(key)
                    if len(sessions) >= 256:
                        sessions.pop(next(iter(sessions)))
                sessions[token] = (now + seconds, initial)
            return self.reply(200, '{"ok":true}', headers={'Set-Cookie': self.session_cookie(token, seconds)})

        def sign_in(self, data):
            with account_lock:
                account = self.admin_account()
                if not self.credentials_match(data.get('username'), data.get('password'), account):
                    return self.json(401, {'error': '账号或密码不正确'})
                initial = self.needs_initial_password(account)
                if initial and not self.local_initialization():
                    return self.json(403, {'error': '请通过本机地址或 SSH 隧道完成首次登录'})
                return self.create_session(initial=initial)

        def complete_initial_password(self, data):
            with account_lock:
                if not self.authorized(allow_initial=True):
                    return
                if not self.initial_session or not self.needs_initial_password(self.admin_account()):
                    return self.json(403, {'error': '首次密码设置已结束，请重新登录或在系统设置中修改密码'})
                store.initialize_admin_password(data.get('new_password'))
                return self.create_session(replace_all=True)

        def change_admin_account(self, data):
            if not (self.secure_management_request() or self.local_initialization()):
                return self.json(403, {'error': '请通过后台 HTTPS、本机地址或 SSH 隧道修改账号'})
            username = data.get('username')
            current = data.get('current_password')
            new_password = data.get('new_password', '')
            if (not isinstance(username, str) or not 1 <= len(username) <= 64
                    or username.strip() != username or not username.isprintable() or ':' in username):
                raise ValueError('账号须为 1 至 64 个可显示字符，不能包含冒号或首尾空白')
            with account_lock:
                if not self.authorized():
                    return
                account = self.admin_account()
                if not self.credentials_match(account[0], current, account):
                    raise ValueError('当前密码不正确')
                if new_password == '':
                    if username == account[0]:
                        raise ValueError('请修改账号或填写新密码')
                    verifier = account[1] or password_record(current)
                else:
                    verifier = password_record(new_password)
                store.save_admin_account(username, verifier)
                with sessions_lock:
                    sessions.clear()
            return self.reply(200, '{"ok":true}', headers={'Set-Cookie': self.session_cookie('', 0)})

        def management_permitted(self):
            if management_local_only and not ipaddress.ip_address(self.client_address[0]).is_loopback:
                host = self.headers.get('Host', '').lower()
                if (management_https_host and self.command == 'GET' and self.path == '/'
                        and host == f'{management_https_host}:{self.server.server_port}'):
                    self.reply(302, '', headers={'Location': f'https://{management_https_host}/'})
                else:
                    self.reply(404, 'Not found', 'text/plain; charset=utf-8')
                return False
            access = getattr(self.server, 'management_access', None)
            if access and not access.permits(self.server):
                self.reply(404, '管理入口已切换', 'text/plain; charset=utf-8')
                return False
            return True

        def do_GET(self):
            if getattr(self.server, 'subscription_only', False):
                path = self.path.partition('?')[0]
                if not path.startswith('/s/'):
                    return self.reply(404, 'Not found', 'text/plain; charset=utf-8')
            else:
                path = urlsplit(self.path).path
            if path == '/health/live':
                return self.json(200, {'status': 'ok'})
            if path == '/health/ready':
                try:
                    if (ready is not None and not ready.is_set()) or not Path(store.path).is_file():
                        return self.json(503, {'status': 'unavailable'})
                    with store.connect() as db:
                        db.execute('SELECT key FROM settings LIMIT 1').fetchone()
                except sqlite3.Error:
                    return self.json(503, {'status': 'unavailable'})
                return self.reply(200, '{"status":"ok"}', headers={'X-HopWorld-Instance': instance})
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
            if not self.management_permitted(): return
            if path == '/login':
                return self.login_page()
            if path in ('/login.css', '/login.js', '/v1-theme-neon-fusion.css'):
                filename = path[1:]
                content_type = 'text/css' if filename.endswith('.css') else 'application/javascript'
                return self.reply(200, (STATIC / filename).read_bytes(), content_type + '; charset=utf-8')
            if path == '/logo.png':
                logo = STATIC / 'logo.png'
                if logo.is_file():
                    return self.reply(200, logo.read_bytes(), 'image/png')
            self.browser_login = path == '/' and 'text/html' in self.headers.get('Accept', '')
            if not self.authorized(allow_initial=path in ('/', '/style.css', '/v1.css', '/first-login.js')): return
            if path.startswith(SECURITY_API):
                try:
                    view = store.security.get(path.removeprefix(SECURITY_API))
                except ValueError as exc:
                    return self.json(404, {'error': str(exc)})
                return self.json(200, view) if view is not None else self.json(404, {'error': '请求不存在'})
            if path == '/api/v1/system':
                return self.json(200, system_info(store))
            if path == '/api/v1/cores/profile':
                params = parse_qs(urlsplit(self.path).query)
                try:
                    view = store.independent_profile_view(params.get('id', [''])[0], params.get('instance', [''])[0])
                    return self.json(200, view)
                except ValueError as exc:
                    return self.json(400, {'error': str(exc)})
            if path == '/api/v1/cores/switch':
                params = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
                try:
                    view = store.publisher_switch_view(params.get('source'), params.get('protocol'),
                                                       int(params.get('port', '')))
                    return self.json(200, view)
                except ValueError as exc:
                    return self.json(400, {'error': str(exc)})
            if path == '/api/v1/domains':
                return self.json(200, {'domains': store.bound_domains()})
            if path == '/api/v1/public-network':
                return self.json(200, store.direct_network_status())
            if path == '/api/v1/nodes/export':
                options = parse_qs(urlsplit(self.path).query)
                identifier = options.get('id', [''])[0]
                format_name = options.get('format', ['uri'])[0]
                if not re.fullmatch(r'[0-9a-f]{64}', identifier):
                    return self.json(400, {'error': '节点标识无效'})
                if format_name not in ('uri', 'mihomo', 'sing-box', 'xray'):
                    return self.json(400, {'error': '配置导出格式无效'})
                try:
                    uri = store.node_link(identifier, local_only=True)
                except ValueError:
                    return self.json(404, {'error': '这个本地发布节点已停止或不存在'})
                if format_name == 'uri':
                    return self.json(200, {'uri': uri})
                try:
                    certificate, _ = cert_paths(store.data_dir, HYSTERIA_CERTIFICATE_NAME)
                    content, filename = export_client_config(uri, format_name, hysteria_certificate=certificate)
                except ValueError as exc:
                    return self.json(400, {'error': str(exc)})
                return self.json(200, {'content': content, 'filename': filename})
            if path == '/api/v1/cores/hysteria2/readiness':
                return self.json(200, store.native_hysteria_readiness())
            if path == '/api/v1/cores/config':
                if not (getattr(self.server, 'tls', None) or ipaddress.ip_address(self.client_address[0]).is_loopback):
                    return self.json(403, {'error': '请通过本机管理地址、SSH 隧道或后台 HTTPS 导出配置'})
                core_id = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
                try:
                    payload = store.core_configuration(core_id)
                except ValueError as exc:
                    return self.json(400, {'error': str(exc)})
                return self.reply(200, payload, headers={'Content-Disposition': f'attachment; filename="{core_id}.json"'})
            if path == '/api/v1/settings':
                host, port = self.server.server_address[:2]
                access = getattr(self.server, 'management_access', None)
                return self.json(200, {
                    **store.management_ui_settings(), **store.update_prefs(), 'host': host, 'port': port,
                    'probe_target': store.probe_target(),
                    'admin_username': self.admin_account()[0],
                    'access': access.view(self.server) if access else None,
                    'autostart': autostart.status() if access else None,
                    'restart_available': request_restart is not None,
                })
            if path == '/api/v1/management/access':
                access = getattr(self.server, 'management_access', None)
                return self.json(200, access.view(self.server) if access else None)
            if path == '/api/v1/relay':
                return self.json(200, store.relay_status())
            if path == '/api/v1/relay/logs':
                return self.json(200, {'text': store.relay_log()})
            if path == '/api/v1/settings/backup':
                return self.database_backup()
            if path == '/api/acme/ip':
                family = parse_qs(urlsplit(self.path).query).get('family', [''])[0]
                try:
                    return self.json(200, store.check_ip_certificate(family))
                except ValueError as exc:
                    return self.json(400, {'error': str(exc)})
            if path == '/api/acme/download':
                expected = parse_qs(urlsplit(self.path).query).get('domain', [''])[0]
                try:
                    domain, certificate = store.certificate_download(expected)
                except ValueError as exc:
                    return self.json(400, {'error': str(exc)})
                return self.reply(200, certificate, 'application/x-pem-file', {
                    'Content-Disposition': f'attachment; filename="{domain}.pem"',
                })
            if path == '/':
                page = (STATIC / 'index.html').read_bytes()
                if self.initial_session:
                    start = page.index(b'  <!-- DASHBOARD SCRIPTS -->')
                    end = page.index(b'</head>', start)
                    page = page[:start] + b'  <script src="/first-login.js" defer></script>\n' + page[end:]
                return self.reply(200, page, 'text/html; charset=utf-8', PAGE_HEADERS)
            if path == '/first-login.js':
                return self.reply(200, (STATIC / 'first-login.js').read_bytes(), 'application/javascript; charset=utf-8')
            if path == '/v1.js': return self.reply(200, (STATIC / 'v1.js').read_bytes(), 'application/javascript; charset=utf-8')
            if path == '/v1-icons.js': return self.reply(200, (STATIC / 'v1-icons.js').read_bytes(), 'application/javascript; charset=utf-8')
            if path == '/v1-overview-secret.js': return self.reply(200, (STATIC / 'v1-overview-secret.js').read_bytes(), 'application/javascript; charset=utf-8')
            if path == '/v1-about.js': return self.reply(200, (STATIC / 'v1-about.js').read_bytes(), 'application/javascript; charset=utf-8')
            if path == '/v1.css': return self.reply(200, (STATIC / 'v1.css').read_bytes(), 'text/css; charset=utf-8')
            if path == '/v1-theme.js': return self.reply(200, (STATIC / 'v1-theme.js').read_bytes(), 'application/javascript; charset=utf-8')
            if path == '/app.js': return self.reply(200, (STATIC / 'app.js').read_bytes(), 'application/javascript; charset=utf-8')
            if path == '/qrcode.js': return self.reply(200, (STATIC / 'qrcode.js').read_bytes(), 'application/javascript; charset=utf-8')
            if path == '/style.css': return self.reply(200, (STATIC / 'style.css').read_bytes(), 'text/css; charset=utf-8')
            if path == '/api/status':
                try:
                    store.sync_public_endpoint()
                except ValueError:
                    pass
                sources = store.sources()
                for source in sources:
                    source['count'] = len(json.loads(source.pop('links')))
                nodes = store.nodes()
                with jobs_lock:
                    job_status = {key: dict(value) for key, value in jobs.items()}
                    if 'active' in job_status['probe']:
                        job_status['probe']['active'] = job_status['probe']['active'].copy()
                    with probe_lock:
                        for node in nodes: node['probe'] = probe_results.get(node['id'])
                last = job_status['probe']
                if not last['running'] and last.get('target'):
                    def rank(node):
                        result = node.get('probe') or {}
                        matches = result.get('target') == last['target'] and result.get('mode') == last.get('mode')
                        passed = matches and result.get('status') == 'ok'
                        latency = result.get('latency_ms')
                        return (0 if passed else 1, latency if passed and isinstance(latency, (int, float)) else float('inf'), node['title'].casefold(), node['id'])
                    nodes.sort(key=rank)
                publication = public_subscription.view() if public_subscription else None
                public_urls = build_public_urls(store, subscription_base=publication['base'] if publication else '')
                acme = store.acme_status()
                management_certificate = management_https_certificate_status(store.data_dir)
                access = getattr(self.server, 'management_access', None)
                if management_local_only and management_https_host:
                    public_urls.update(admin_url=f'https://{management_https_host}', management_note='')
                elif management_local_only:
                    public_urls.update(admin_url='', management_note='请通过本机或 SSH 隧道访问管理台')
                elif access and access.available:
                    public_urls['admin_url'] = access.address()
                return self.json(200, {
                    'sources': sources,
                    'nodes': nodes,
                    'jobs': job_status,
                    'probe_target': store.probe_target(),
                    'token': store.token(),
                    'public_urls': public_urls,
                    'public_subscription': publication,
                    'public_subscription_tls': public_subscription.certificate_status(
                        acme, management=management_certificate) if public_subscription else None,
                    'management_ip_certificate': management_certificate
                    if is_ip_host(management_certificate.get('host', '')) else None,
                    'local_node': store.local_node_status(),
                    'acme': acme,
                    'bound_domains': store.bound_domains(),
                    'ip_certificates': store.ip_certificates(),
                    'ikev2': store.ikev2_status(),
                })
            if path == '/api/local/auto-status':
                return self.json(200, store.auto_observation_status())
            if path == '/api/local/traffic':
                return self.json(200, store.local_traffic_status())
            if path == '/api/v1/traffic':
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if 'start' in query or 'end' in query:
                    try:
                        start, end = query.get('start', [''])[0], query.get('end', [''])[0]
                        daily = store.publisher_traffic.daily_range(start, end)
                    except ValueError as exc:
                        return self.json(400, {'error': str(exc)})
                    return self.json(200, {'daily': daily, 'legacy_daily': store.traffic_history.daily_range(start, end)})
                detailed = query.get('dashboard') == ['1']
                view = store.publisher_traffic.snapshot(dashboard=detailed)
                view['legacy'] = store.traffic_history.snapshot(dashboard=detailed)
                return self.json(200, view)
            if path == '/api/system/update':
                status = dict(store.updates.project_status())
                status.update(store.update_prefs())
                status['installation'] = system_update.current_target()
                if status['installation'] == 'package':
                    status.update(auto_check=store.updates.preferences()['package'],
                                  auto_apply=store.updates.preferences()['package'])
                status['runtime'] = runtime_layout.status()
                status['incremental'] = {
                    'state': 'idle', 'changed_files': [], 'channel': status['runtime'].get('channel', 'stable'),
                    'note': '',
                }
                return self.json(200, status)
            if path == '/api/v1/updates/runtime':
                return self.json(200, runtime_layout.status())
            if path == '/api/v1/updates':
                return self.json(200, {**store.updates.history(), 'preferences': store.updates.preferences(),
                                       'installation': system_update.current_target(),
                                       'runtime': runtime_layout.status(),
                                       'components': [row[:2] for row in CORES]})
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

        def do_PUT(self):
            if not urlsplit(self.path).path.startswith(SECURITY_API):
                return self.json(404, {'error': '请求不存在'})
            self.do_POST()

        def do_POST(self):
            if getattr(self.server, 'subscription_only', False):
                return self.reply(404, 'Not found', 'text/plain; charset=utf-8')
            if not self.management_permitted(): return
            path = urlsplit(self.path).path
            initial_paths = ('/api/v1/management/initial-password', '/logout')
            if path != '/login' and not self.authorized(allow_initial=path in initial_paths): return
            # Same-origin CSRF check: browser Origin must match this request's Host.
            origin = self.headers.get('Origin', '')
            host = self.headers.get('Host', '')
            if not host or origin not in (f'http://{host}', f'https://{host}'):
                return self.json(403, {'error': '访问来源不受信任'})
            if path == '/api/acme/import' and not (self.secure_management_request() or self.local_initialization()):
                return self.json(403, {'error': '请通过本机管理地址或后台 HTTPS 导入证书私钥'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                limit = {'/api/acme/import': 256 * 1024, '/api/batch/probe': MAX_SUBSCRIPTION_BYTES}.get(path, MAX_BODY)
                if length < 0 or length > limit: raise ValueError('请求过大')
                data = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(data, dict): raise ValueError('请求格式错误')
                if path.startswith(SECURITY_API):
                    result = store.security.write(self.command, path.removeprefix(SECURITY_API), data)
                    return self.json(*result) if result is not None else self.json(404, {'error': '请求不存在'})
                if path == '/login':
                    return self.sign_in(data)
                if path == '/api/v1/management/initial-password':
                    return self.complete_initial_password(data)
                if path == '/api/v1/management/account':
                    return self.change_admin_account(data)
                if path == '/api/v1/public-subscription' and public_subscription:
                    enabled, port = data.get('enabled'), data.get('port')
                    access = getattr(self.server, 'management_access', None)
                    if enabled is True and (port == self.server.server_port or access and port == access.base.server_port):
                        raise ValueError('HTTPS 订阅需要独立于管理后台的端口')
                    try:
                        configuration = public_subscription.configure(Handler, enabled, port, data.get('host'))
                    except sqlite3.Error:
                        return self.json(503, {'error': '无法保存公网订阅配置，请检查数据目录'})
                    return self.json(200, configuration)
                if path == '/api/v1/public-subscription/topbar' and public_subscription:
                    try:
                        return self.json(200, public_subscription.save_topbar_labels(data))
                    except sqlite3.Error:
                        return self.json(503, {'error': '无法保存状态文字，请检查数据目录'})
                if path == '/api/v1/domains':
                    return self.json(200, {'domains': store.register_domain(
                        data.get('host'), data.get('family'), data.get('ingress'))})
                if path == '/api/v1/domains/records':
                    return self.json(200, store.domain_binding_records(
                        data.get('host', ''), data.get('family'), data.get('ingress')))
                if path == '/api/v1/cores/address-check':
                    family = data.get('family')
                    if family not in ('ipv4', 'ipv6', 'dual', 'ipv4_first'):
                        raise ValueError('请选择地址族')
                    if data.get('mode') == 'auto_ip':
                        if family == 'dual':
                            return self.json(200, {'ipv4': store._detect_dual_public_ip(4),
                                                   'ipv6': store._detect_dual_public_ip(6)})
                        if family not in ('ipv4', 'ipv6'):
                            raise ValueError('自动获取公网 IP 时请选择 IPv4 或 IPv6')
                        return self.json(200, {'address': store._detect_direct_public_ip(6 if family == 'ipv6' else 4)})
                    if data.get('mode') != 'domain':
                        raise ValueError('请选择订阅地址类型')
                    return self.json(200, store.domain_binding(data.get('host'), family))
                if path == '/logout':
                    with sessions_lock:
                        sessions.pop(self.session_id(), None)
                    return self.reply(200, '{"ok":true}', headers={'Set-Cookie': self.session_cookie('', 0)})
                if path == '/api/sources':
                    enabled = data.get('enabled', True)
                    source_id = store.add(
                        data.get('name', ''), data.get('url', ''), enabled,
                        data.get('auto_update', True), data.get('update_interval', SOURCE_UPDATE_INTERVAL),
                    )
                    if enabled:
                        try: store.refresh(source_id)
                        except ValueError: pass
                elif path == '/api/sources/settings':
                    source_id = data.pop('id', None)
                    if type(source_id) is not int:
                        raise ValueError('订阅设置无效')
                    store.update_source(source_id, data)
                elif path == '/api/refresh': store.refresh(int(data['id']))
                elif path == '/api/delete':
                    if not store.delete(int(data['id'])): raise ValueError('订阅不存在')
                elif path == '/api/rotate':
                    store.rotate()
                    return self.json(200, {'ok': True})
                elif path == '/api/v1/relay':
                    return self.json(200, store.configure_relay(data))
                elif path == '/api/manual': store.add_manual(data.get('content', ''))
                elif path == '/api/manual/delete':
                    if not store.remove_manual(data.get('id', '')): raise ValueError('节点不存在')
                elif path == '/api/node': store.set_node_enabled(data.get('id'), data.get('enabled'))
                elif path == '/api/v1/nodes/name': store.set_upstream_name(data.get('id'), data.get('name'))
                elif path == '/api/probe':
                    start_probe(data.get('id'), data.get('target', store.probe_target()), data.get('mode', 'full'))
                elif path == '/api/local/discover':
                    ports = data.get('ports')
                    return self.json(200, {'proxies': discover() if ports is None else discover(ports)})
                elif path == '/api/local/singbox':
                    return self.json(200, {'ok': True, 'local_node': store.refresh_singbox()})
                elif path == '/api/local-node':
                    return self.json(200, {'ok': True, 'local_node': store.configure_local_node(
                        bool(data.get('enabled')), data.get('public_host'), data.get('port'), data.get('name'),
                        data.get('auto_public_host'), data.get('public_host_prefer'), data.get('ddns_enabled'),
                        data.get('acme_enabled'), data.get('acme_email'), data.get('acme_challenge'),
                        data.get('egress_mode'), data.get('local_proxy'), data.get('auto_target'), data.get('auto_interval'),
                        data.get('upstream_id'))})
                elif path == '/api/v1/publishers/disconnect':
                    if data:
                        raise ValueError('断开发布服务操作不接受其他参数')
                    store.disconnect_publishers()
                    return self.json(200, {'ok': True})
                elif path == '/api/acme':
                    if type(data.get('enabled')) is not bool or not isinstance(data.get('email'), str) or not isinstance(data.get('challenge'), str):
                        raise ValueError('证书设置无效')
                    with store._local_lock:
                        return self.json(200, {'ok': True, 'acme': store.configure_acme(
                            data['enabled'], data['email'], data['challenge'], data.get('domain'))})
                elif path == '/api/acme/preflight':
                    if set(data) != {'family', 'target', 'email', 'challenge'} or not all(isinstance(v, str) for v in data.values()):
                        raise ValueError('证书验证参数无效')
                    return self.json(200, store.stage_acme(data['family'], data['target'], data['email'], data['challenge']))
                elif path == '/api/acme/ip':
                    if set(data) - {'address', 'challenge', 'verified_target'} != {'family', 'enabled', 'email'} or type(data['enabled']) is not bool:
                        raise ValueError('公网 IP 证书设置无效')
                    return self.json(200, {'certificate': store.configure_ip_certificate(
                        data['family'], data['enabled'], data['email'], address=data.get('address'),
                        challenge=data.get('challenge', 'http'), verified_target=data.get('verified_target'))})
                elif path == '/api/acme/renew':
                    store.renew_acme_certificate(data.get('domain'))
                    return self.json(200, {'ok': True, 'acme': store.acme_status()})
                elif path == '/api/acme/import':
                    return self.json(200, {'acme': store.import_manual_certificate(
                        data.get('domain'), data.get('certificate'), data.get('private_key'))})
                elif path == '/api/acme/dns':
                    settings = store.local_node_settings()
                    domain = validate_subscription_domain(data.get('domain'))
                    binding = next((item for item in store.bound_domains() if item['host'] == domain), None)
                    if binding:
                        result = store.domain_binding(domain, binding['family'])
                        verify = {'status': 'ok' if result['bound'] else 'mismatch', 'detail': result['detail'],
                                  'resolved_ipv4': result['ipv4'], 'resolved_ipv6': result['ipv6']}
                    else:
                        store.certificate_domain(domain, settings)
                        verify = store.dns_verify_for_settings(settings, force=True)
                    store.record_certificate_event(domain, '检查解析', verify['detail'])
                    return self.json(200, {'dns': verify, 'acme': store.acme_status()})
                elif path == '/api/v1/cores/check':
                    core_id = data.get('id')
                    if not isinstance(core_id, str) or not core_id:
                        raise ValueError('内核无效')
                    row = next((item for item in core_inventory(store) if item['id'] == core_id), None)
                    if row is None:
                        raise ValueError('没有这个内核')
                    return self.json(200, {'ok': True, **store.updates.core_check(core_id, row),
                                           'client_missing': missing_core_client(store.data_dir, core_id, row['path'])})
                elif path == '/api/v1/cores/pause':
                    core_id = data.get('id')
                    if not isinstance(core_id, str) or not core_id:
                        raise ValueError('内核无效')
                    pause_download(core_id)
                    return self.json(200, {'ok': True})
                elif path == '/api/v1/cores/cancel':
                    core_id = data.get('id')
                    if not isinstance(core_id, str) or not core_id:
                        raise ValueError('内核无效')
                    cancel_download(store.data_dir, core_id)
                    return self.json(200, {'ok': True})
                elif path == '/api/v1/cores/install':
                    core_id = data.get('id')
                    if not isinstance(core_id, str) or not core_id:
                        raise ValueError('内核无效')
                    self.close_connection = True
                    self.protocol_version = 'HTTP/1.1'
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
                    self.send_header('Transfer-Encoding', 'chunked')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()

                    def emit(event):
                        payload = (json.dumps(event, ensure_ascii=False) + '\n').encode()
                        self.wfile.write(b'%X\r\n' % len(payload) + payload + b'\r\n')
                        self.wfile.flush()

                    try:
                        row = next((item for item in core_inventory(store) if item['id'] == core_id), None)
                        installed = store.updates.install_core(core_id, row, progress=emit)
                        if installed.get('uptodate'):
                            emit({'ok': True, 'uptodate': True, 'version': installed['version'], 'system': system_info(store)})
                        else:
                            store.xray.locate()
                            store.singbox.locate()
                            store.hysteria.locate()
                            emit({'ok': True, 'installed': installed, 'system': system_info(store)})
                    except _Paused as paused:
                        try:
                            emit({'paused': True, 'received': paused.received, 'total': paused.total})
                        except OSError:
                            pass
                    except _Cancelled:
                        try:
                            emit({'cancelled': True})
                        except OSError:
                            pass
                    except ValueError as exc:
                        try:
                            emit({'error': str(exc) or '安装失败'})
                        except OSError:
                            pass
                    try:
                        self.wfile.write(b'0\r\n\r\n')
                        self.wfile.flush()
                    except OSError:
                        pass
                    return
                elif path == '/api/v1/cores/switch':
                    store.switch_publisher(data)
                    return self.json(200, {'ok': True, 'system': system_info(store)})
                elif path == '/api/v1/cores/start':
                    if data.get('id') == 'hysteria2' and 'profile' not in data:
                        store.set_native_hysteria(True)
                    else:
                        store.start_independent_core(data.get('id'), data.get('profile'), data.get('instance', ''))
                    return self.json(200, {'ok': True, 'system': system_info(store)})
                elif path == '/api/v1/cores/stop':
                    if data.get('id') == 'hysteria2' and not any(
                        publisher_core(key) == 'hysteria2' for key in store.independent_profiles()
                    ):
                        store.set_native_hysteria(False)
                    else:
                        store.stop_core(data.get('id'), data.get('instance', ''))
                    return self.json(200, {'ok': True, 'system': system_info(store)})
                elif path == '/api/v1/cores/restart':
                    service = _managed_core(store, data.get('id'))
                    if service is None:
                        raise ValueError('这个内核不由本页启动，不能从这里重启')
                    with store._local_lock:
                        core_id = data['id']
                        runtimes = [store.publisher_runtime(key) for key, profile in store.independent_profiles().items()
                                    if publisher_core(key) == core_id and profile.get('enabled')]
                        running = [runtime for runtime in dict.fromkeys([service, *runtimes]) if runtime.running()]
                        for runtime in running or [service]:
                            runtime.restart()
                    return self.json(200, {'ok': True, 'system': system_info(store)})
                elif path == '/api/v1/cores/logs':
                    return self.json(200, {'ok': True, 'text': store.core_logs(data.get('id'))})
                elif path == '/api/v1/cores/backup':
                    return self.json(200, {'filename': store.backup_core_configuration(data.get('id'))})
                elif path == '/api/v1/cores/uninstall':
                    core_id = data.get('id')
                    if not isinstance(core_id, str) or not core_id:
                        raise ValueError('内核无效')
                    with store._local_lock:
                        target = bundled_binary(store.data_dir, core_id)
                        service = _managed_core(store, core_id)
                        if service and service.running() and service.binary:
                            try:
                                busy = Path(service.binary).resolve() == target.resolve()
                            except OSError:
                                busy = False
                            if busy:
                                raise ValueError('这个内核正在运行，请先停止该内核')
                        removed = uninstall_core(store.data_dir, core_id)
                        if service:
                            service.locate()
                    return self.json(200, {'ok': True, 'removed': removed, 'system': system_info(store)})
                elif path == '/api/v1/settings':
                    return self.json(200, store.save_management_settings(data))
                elif path == '/api/v1/management/access':
                    access = getattr(self.server, 'management_access', None)
                    if access is None:
                        raise ValueError('当前服务没有启用后台入口管理')
                    action = data.get('action')
                    if action == 'stage' and set(data) == {'action', 'port', 'https'}:
                        access.stage(self.server, data['port'], data['https'])
                    elif action == 'confirm' and set(data) == {'action'}:
                        try:
                            access.confirm(self.server)
                        except sqlite3.Error:
                            return self.json(500, {'error': '无法保存后台入口，请稍后重试'})
                    elif action == 'cancel' and set(data) == {'action'}:
                        access.cancel(self.server)
                    else:
                        raise ValueError('后台入口操作无效')
                    return self.json(200, access.view(self.server))
                elif path == '/api/v1/management/autostart':
                    if set(data) != {'enabled'} or not getattr(self.server, 'management_access', None):
                        raise ValueError('自启动设置无效')
                    with autostart_lock:
                        return self.json(200, autostart.set_enabled(data['enabled']))
                elif path == '/api/v1/management/restart':
                    if data:
                        raise ValueError('重启 Router 不需要参数')
                    if request_restart is None:
                        return self.json(409, {'error': '当前启动方式不支持从管理后台重启'})
                    access = getattr(self.server, 'management_access', None)
                    if access and access.view(self.server)['can_confirm']:
                        return self.json(409, {'error': '请先确认新的管理入口'})
                    if not restart_lock.acquire(blocking=False):
                        return self.json(409, {'error': 'Router 正在重启'})
                    try:
                        self.json(202, {'instance': instance})
                    except OSError:
                        restart_lock.release()
                        raise
                    request_restart()
                    return
                elif path == '/api/v1/updates/preferences':
                    return self.json(200, store.updates.save(data))
                elif path == '/api/v1/updates/read':
                    if set(data) != {'seen'}:
                        raise ValueError('记录位置无效')
                    store.updates.mark_read(data['seen'])
                    return self.json(200, {'ok': True})
                elif path == '/api/system/update':
                    if 'auto_check' in data or 'auto_apply' in data:
                        if set(data) != {'auto_check', 'auto_apply'}:
                            raise ValueError('更新设置无效')
                        return self.json(200, {'ok': True, **store.save_update_prefs(data['auto_check'], data['auto_apply'])})
                    if data == {'check': True}:
                        return self.json(200, store.updates.project_status(refresh=True))
                    if data:
                        raise ValueError('更新请求不能包含参数')
                    store.updates.start_project()
                    return self.json(200, {'ok': True})
                elif path == '/api/v1/updates/runtime':
                    action = data.get('action')
                    if action == 'migrate':
                        incremental_update.start_migration()
                        return self.json(202, {'ok': True, 'started': True})
                    if action == 'channel':
                        return self.json(200, {'ok': True, 'runtime': runtime_layout.set_channel(data.get('channel'))})
                    if action == 'rollback':
                        return self.json(200, {'ok': True, 'runtime': runtime_layout.rollback_to(data.get('id', ''))})
                    if action == 'check-files':
                        return self.json(200, {'ok': True, 'incremental': incremental_update.check()})
                    if action == 'apply-files':
                        return self.json(200, incremental_update.apply())
                    raise ValueError('运行时更新操作无效')
                elif path == '/api/ikev2':
                    return self.json(200, {'ok': True, 'ikev2': store.configure_ikev2(
                        bool(data.get('enabled')), data.get('public_host'), data.get('remote_id'),
                        bool(data.get('rotate_psk')), data.get('psk'))})
                elif path == '/api/ikev2/sync':
                    store.ikev2._run_helper('sync-clients')
                    with store.ikev2._runtime_lock:
                        store.ikev2._runtime_cache = None
                    return self.json(200, {'ok': True, 'ikev2': store.ikev2_status()})
                elif path == '/api/batch/update': start_job('update')
                elif path == '/api/batch/probe':
                    start_job('probe', data.get('target', store.probe_target()),
                              data.get('groups'), data.get('node_ids'), data.get('mode', 'full'))
                elif path in ('/api/batch/update/stop', '/api/batch/probe/stop'):
                    kind = path.split('/')[-2]
                    with jobs_lock:
                        if jobs[kind]['running']:
                            jobs[kind]['stopping'] = True
                            cancellations[kind].set()
                else: return self.json(404, {'error': '请求不存在'})
                if path in ('/api/sources', '/api/sources/settings', '/api/refresh', '/api/delete', '/api/manual', '/api/manual/delete', '/api/node'):
                    store.reconcile_egress_pool()
                self.json(200, {'ok': True})
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                message = str(exc) if isinstance(exc, ValueError) and str(exc) else '操作失败，请检查输入或订阅状态'
                self.json(400, {'error': message})
    return Handler


def main():
    data_dir = Path(os.environ.get('HOPWORLD_DATA', './data'))
    restart_command = [sys.executable, *sys.orig_argv[1:]] if (
        os.name == 'posix' and sys.executable and os.access(sys.executable, os.X_OK)
    ) else None
    try:
        with data_lock(data_dir):
            restart_requested = serve(data_dir, allow_restart=restart_command is not None)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    if restart_requested:
        try:
            os.execv(restart_command[0], restart_command)
        except OSError as exc:
            raise SystemExit(f'无法重新启动 Router：{exc.strerror}') from None


def serve(data_dir, *, allow_restart=False):
    password = os.environ.get('HOPWORLD_ADMIN_PASSWORD') or DEFAULT_PASSWORD
    if password != DEFAULT_PASSWORD and len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f'HOPWORLD_ADMIN_PASSWORD 必须至少 {MIN_PASSWORD_LENGTH} 个字符')
    host = os.environ.get('HOPWORLD_HOST', '127.0.0.1')
    port = int(os.environ.get('HOPWORLD_PORT', '8080'))
    store = Store(data_dir / 'router.db')
    public_subscription = PublicSubscription(store)
    ready = threading.Event()
    stopped = threading.Event()
    restart_requested = False

    def shutdown():
        stopped.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    def request_restart():
        nonlocal restart_requested
        if not stopped.is_set():
            restart_requested = True
            shutdown()

    def handle_signal(*_args):
        nonlocal restart_requested
        restart_requested = False
        shutdown()

    handler = make_handler(store, password, public_subscription, ready=ready,
                           request_restart=request_restart if allow_restart else None,
                           management_local_only=os.environ.get('HOPWORLD_MANAGEMENT_LOCAL_ONLY') == '1',
                           management_https_host=os.environ.get('HOPWORLD_MANAGEMENT_HTTPS_HOST', '').strip().lower())
    server = ThreadingHTTPServer((host, port), handler)
    if password == DEFAULT_PASSWORD and not (store.admin_account() or store.admin_password_record()):
        if not ipaddress.ip_address(server.server_address[0]).is_loopback:
            server.server_close()
            raise SystemExit('请先在回环地址上完成默认管理员初始化')
    management = ManagementAccess(store, server)
    management.restore()
    public_subscription.restore(server.RequestHandlerClass)
    threading.Thread(target=collect_traffic, args=(store.traffic_history, store.local_traffic_status, stopped), daemon=True).start()
    threading.Thread(target=collect_publisher_traffic,
                     args=(store.publisher_traffic, store.publisher_traffic_status, stopped, SAMPLE_SECONDS), daemon=True).start()

    def restore_background():
        try:
            store.restore_local_node()
        except ValueError as exc:
            print(f'本地网络节点未启动: {exc}', flush=True)
        store.restore_independent_cores()
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

    def relay_loop():
        while not stopped.wait(5):
            try:
                store.sync_relay()
            except sqlite3.Error as exc:
                print(f'无法读取中转设置: {exc}', flush=True)

    relay_thread = threading.Thread(target=relay_loop, daemon=True)
    relay_thread.start()

    def update_loop():
        while not stopped.wait(30):
            for source_id in store.due_sources():
                if stopped.is_set(): return
                try:
                    store.refresh(source_id, automatic=True)
                    store.reconcile_egress_pool()
                except ValueError: pass

    threading.Thread(target=update_loop, daemon=True).start()

    def project_update_loop():
        next_check = 0
        while not stopped.is_set():
            wait = 30
            try:
                store.updates.import_worker_result()
                prefs = store.update_prefs()
                package = system_update.current_target() == 'package'
                automatic = store.updates.preferences()['package'] if package else prefs['auto_check'] and prefs['auto_apply']
                if (automatic if package else prefs['auto_check']) and time.monotonic() >= next_check:
                    status = store.updates.project_status(refresh=True)
                    if status['state'] == 'checking':
                        wait = 3
                        if stopped.wait(wait):
                            return
                        status = store.updates.project_status()
                    if status['state'] != 'checking':
                        next_check = time.monotonic() + 600
                        if automatic and status['state'] == 'available':
                            store.updates.start_project(status)
            except (ValueError, OSError, sqlite3.Error):
                pass
            if stopped.wait(wait):
                return

    threading.Thread(target=project_update_loop, daemon=True).start()

    def core_update_loop():
        while not stopped.wait(30):
            try:
                store.updates.run_core_updates()
            except (ValueError, OSError, sqlite3.Error):
                pass

    threading.Thread(target=core_update_loop, daemon=True).start()

    def public_host_loop():
        interval = max(10, int(os.environ.get('HOPWORLD_PUBLIC_HOST_POLL', '30')))
        while not stopped.wait(interval):
            try:
                store.sync_independent_addresses()
            except sqlite3.Error as exc:
                print(f'无法更新独立节点地址: {exc}', flush=True)
            try:
                store.sync_public_endpoint()
            except ValueError:
                pass

    threading.Thread(target=public_host_loop, daemon=True).start()

    def acme_loop():
        interval = max(3600, int(os.environ.get('HOPWORLD_ACME_CHECK_SECONDS', '21600')))
        while not stopped.wait(interval):
            try:
                store.sync_acme_certificate()
            except ValueError:
                pass

    threading.Thread(target=acme_loop, daemon=True).start()

    def ip_acme_loop():
        delay = 1
        while not stopped.wait(delay):
            try:
                store.sync_managed_ip_certificates()
                store.sync_hysteria_ip_certificate()
                store.sync_management_ip_core_certificates()
                public_subscription.sync_ip_binding(handler)
            except sqlite3.Error:
                print('无法更新 Hysteria 2 公网 IP 证书状态', flush=True)
            delay = 900

    threading.Thread(target=ip_acme_loop, daemon=True).start()
    print(f'HopWorld Router: {management.address()}', flush=True)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    ready.set()
    try: server.serve_forever()
    finally:
        stopped.set()
        public_subscription.close()
        management.close()
        relay_thread.join()
        with store._local_lock:
            store.relay.stop()
        store.xray.stop()
        store.singbox.stop()
        store.hysteria.stop()
        for service in store.independent.values():
            service.stop()
        server.server_close()
    return restart_requested


if __name__ == '__main__':
    main()
