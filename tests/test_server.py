import base64
from contextlib import closing
import json
import os
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPResponse
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener, urlopen

import yaml

from router.server import Store, make_handler, node_id
from router.acme import cert_paths, hysteria_local_certificate, ip_certificate_paths
from router.public_subscription import PublicSubscription
from router.subscription import decode_subscription


class Response:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.data[:limit]


class Opener:
    def __init__(self, data): self.data = data
    def open(self, request, timeout): return Response(self.data)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'db')
        self.store.singbox.binary = ''
        self.publication = PublicSubscription(self.store)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, 'a-long-safe-password', self.publication))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root = f'http://127.0.0.1:{self.server.server_port}'
        self.auth = 'Basic ' + base64.b64encode(b'admin:a-long-safe-password').decode()

    def tearDown(self):
        self.publication.close()
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, path, data=None, auth=True, origin=True):
        headers = {}
        if auth: headers['Authorization'] = self.auth
        if data is not None:
            headers['Content-Type'] = 'application/json'
            if origin: headers['Origin'] = self.root
        req = Request(self.root + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        return urlopen(req, timeout=2)

    def test_core_config_download_and_private_backup(self):
        runtime = self.store.publisher_runtime('mihomo')
        runtime.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = b'{"mode":"direct"}'
        runtime.config_path.write_bytes(payload)
        path = '/api/v1/cores/config?id=mihomo'
        with self.assertRaises(HTTPError) as anonymous:
            self.request(path, auth=False)
        self.assertEqual(anonymous.exception.code, 401)
        with self.request(path) as response:
            self.assertEqual(response.headers['Content-Disposition'], 'attachment; filename="mihomo.json"')
            self.assertEqual(response.read(), payload)
        with self.request('/api/v1/cores/backup', {'id': 'mihomo'}) as response:
            result = json.load(response)
        backup = self.store.data_dir / 'core-config-backups' / result['filename']
        self.assertEqual(backup.read_bytes(), payload)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_certificate_import_accepts_only_configured_https_proxy(self):
        host = 'router.example.com'
        self.server.RequestHandlerClass = make_handler(
            self.store, 'a-long-safe-password', management_local_only=True, management_https_host=host,
        )
        url = self.root + '/api/acme/import'
        for name, proto, expected in ((host, 'https', 400), (host, 'http', 403), ('other.example.com', 'https', 403)):
            with self.subTest(name=name, proto=proto):
                request = Request(url, data=b'{}', headers={
                    'Host': name, 'Origin': f'https://{name}', 'Authorization': self.auth,
                    'X-Forwarded-Proto': proto,
                })
                with self.assertRaises(HTTPError) as response:
                    urlopen(request, timeout=2)
                self.assertEqual(response.exception.code, expected)

    def test_disconnect_disables_saved_publishers_and_stops_the_relay(self):
        self.store._save_independent_profiles({'mihomo': {'enabled': True, 'port': 8388}})
        publisher = self.store.publisher_runtime('mihomo')
        settings = self.store.local_node_settings()
        self.store.save_local_node_settings({**settings, 'enabled': True})
        with patch.object(publisher, 'stop') as stop, patch.object(self.store.relay, 'stop') as stop_relay:
            with self.request('/api/v1/publishers/disconnect', {}) as response:
                self.assertEqual(response.status, 200)
            stop.assert_called_once_with()
            stop_relay.assert_called_once_with()
        self.assertFalse(self.store.independent_profiles()['mihomo']['enabled'])
        self.assertFalse(self.store.local_node_settings()['enabled'])

    def test_management_settings_are_validated_and_share_update_preferences(self):
        path = '/api/v1/settings'
        original = json.load(self.request(path))
        self.assertEqual(original['port'], self.server.server_port)
        self.assertEqual(original['name'], 'HopWorld-Router')
        self.assertEqual(original['probe_target'], 'google')
        data = {'timezone': 'Asia/Shanghai', 'auto_check': False, 'auto_apply': False}
        with self.assertRaises(HTTPError) as no_origin:
            self.request(path, data, origin=False)
        self.assertEqual(no_origin.exception.code, 403)
        with self.assertRaises(HTTPError) as invalid:
            self.request(path, {**data, 'timezone': '../../timezone'})
        self.assertEqual(invalid.exception.code, 400)
        with self.assertRaises(HTTPError) as invalid_target:
            self.request(path, {**data, 'probe_target': 'unknown'})
        self.assertEqual(invalid_target.exception.code, 400)
        with self.assertRaises(HTTPError) as immutable_name:
            self.request(path, {**data, 'name': 'Home Router'})
        self.assertEqual(immutable_name.exception.code, 400)
        self.assertEqual(json.load(self.request(path)), original)
        result = json.load(self.request(path, data))
        self.assertEqual(result['name'], 'HopWorld-Router')
        self.assertEqual(result['timezone'], 'Asia/Shanghai')
        self.assertEqual(result['auto_check'], False)
        self.assertEqual(result['auto_apply'], False)
        self.assertEqual(json.load(self.request(path))['name'], 'HopWorld-Router')
        self.assertEqual(self.store.update_prefs(), {'auto_check': False, 'auto_apply': False})
        basic = {'timezone': 'UTC'}
        saved_basic = json.load(self.request(path, basic))
        self.assertEqual(saved_basic['name'], 'HopWorld-Router')
        self.assertEqual(saved_basic['timezone'], 'UTC')
        self.assertEqual(self.store.update_prefs(), {'auto_check': False, 'auto_apply': False})

    def test_public_subscription_requires_admin_and_a_certificate(self):
        path = '/api/v1/public-subscription'
        settings = {'enabled': True, 'port': 9443}
        for options, status in (({'auth': False}, 401), ({'origin': False}, 403)):
            with self.subTest(options=options), self.assertRaises(HTTPError) as error:
                self.request(path, settings, **options)
            self.assertEqual(error.exception.code, status)
        for invalid in ({'port': 65536}, {'enabled': 'true'}, {}):
            with self.subTest(invalid=invalid), self.assertRaises(HTTPError) as error:
                self.request(path, {**settings, **invalid})
            self.assertEqual(error.exception.code, 400)
        self.assertFalse(json.load(self.request('/api/status'))['public_subscription']['enabled'])

    @unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for management HTTPS fixture')
    def test_public_subscription_lists_management_https_certificate(self):
        host = 'admin.example.test'
        cert, key = cert_paths(self.store.data_dir, host)
        cert.parent.mkdir(parents=True)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                        '-subj', f'/CN={host}', '-addext', f'subjectAltName=DNS:{host}',
                        '-out', str(cert), '-keyout', str(key)],
                       check=True, capture_output=True)
        with patch.dict(os.environ, {'HOPWORLD_MANAGEMENT_HTTPS_HOST': host}):
            with patch('router.acme.letsencrypt_live', return_value=Mock(is_dir=Mock(return_value=False))):
                choices = json.load(self.request('/api/status'))['public_subscription_tls']['certificates']
        self.assertEqual(choices, [{'host': host, 'type': 'domain'}])

    def test_public_subscription_topbar_labels_validate_and_persist(self):
        path = '/api/v1/public-subscription/topbar'
        defaults = json.load(self.request('/api/status'))['public_subscription']['topbar_labels']
        self.assertEqual(defaults, {'running': '订阅开', 'stopped': '订阅关', 'error': '订阅异常'})
        custom = {'running': '', 'stopped': '暂停订阅', 'error': '𠀀𠀁𠀂𠀃'}
        for invalid in ('已经启动了', 'ON', '启用🙂', '订 阅', None):
            with self.subTest(invalid=invalid), self.assertRaises(HTTPError) as response:
                self.request(path, {**custom, 'running': invalid})
            self.assertEqual(response.exception.code, 400)
        result = json.load(self.request(path, custom))
        self.assertEqual(result['topbar_labels'], custom)
        self.assertFalse(result['enabled'])
        persisted = PublicSubscription(Store(self.store.path)).view()
        self.assertEqual(persisted['topbar_labels'], custom)

    @unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for local TLS fixture')
    def test_public_https_download_blocks_management_and_restores_listener(self):
        domain = 'subscription.example.test'
        cert, key = cert_paths(self.store.data_dir, domain, manual=True)
        cert.parent.mkdir(parents=True)
        openssl_config = cert.parent / 'openssl.cnf'
        openssl_config.write_text(
            f'[req]\ndistinguished_name=dn\nprompt=no\nx509_extensions=extensions\n[dn]\nCN={domain}\n'
            f'[extensions]\nsubjectAltName=DNS:{domain},IP:127.0.0.1,IP:::1\n'
        )
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                        '-out', str(cert), '-keyout', str(key), '-config', str(openssl_config)],
                       check=True, capture_output=True)
        self.store.save_acme_settings({'domain': domain, 'source': 'manual', 'enabled': True, 'cert_path': str(cert)})
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            port = available.getsockname()[1]
        link = 'trojan://example-secret@node.example:443#Example'
        self.store.add_manual(link)
        path = '/api/v1/public-subscription'
        settings = {'enabled': True, 'port': port}
        publication = json.load(self.request(path, settings))
        advertised = json.load(self.request('/api/status'))['public_urls']['public_subscription_url']
        self.assertEqual(publication['base'], f'https://{domain}:{port}')
        self.assertEqual(advertised, f"{publication['base']}/s/{self.store.token()}")
        listeners = self.publication.listeners
        labels = {'running': '', 'stopped': '订阅关', 'error': '订阅异常'}
        display = json.load(self.request(path + '/topbar', labels))
        self.assertEqual(display['base'], publication['base'])
        self.assertEqual(display['topbar_labels'], labels)
        self.assertIs(self.publication.listeners, listeners)
        publication = display
        context = ssl.create_default_context(cafile=str(cert))
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
        base = f'https://127.0.0.1:{port}'
        subscription_path = urlsplit(advertised).path
        with opener.open(base + subscription_path, timeout=3) as response:
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(decode_subscription(response.read()), [link])
        if socket.has_dualstack_ipv6():
            with opener.open(f'https://[::1]:{port}' + subscription_path, timeout=3) as response:
                self.assertEqual(decode_subscription(response.read()), [link])

        for route in ('/', '/api/status', '/health/live', '/login', '/s/wrong-key'):
            with self.subTest(route=route), self.assertRaises(HTTPError) as error:
                opener.open(base + route, timeout=3)
            self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError) as post:
            opener.open(Request(base + '/api/rotate', data=b'{}', headers={'Authorization': self.auth}), timeout=3)
        self.assertEqual(post.exception.code, 404)

        with socket.socket() as occupied:
            occupied.bind(('0.0.0.0', 0))
            occupied.listen()
            with self.assertRaises(HTTPError) as failure:
                self.request(path, {**settings, 'port': occupied.getsockname()[1]})
            self.assertEqual(failure.exception.code, 400)
        self.assertEqual(self.publication.view(), publication)

        self.publication.close()
        recovered = PublicSubscription(self.store)
        try:
            recovered.restore(self.server.RequestHandlerClass)
            self.assertEqual(recovered.view(), publication)
            with opener.open(base + subscription_path, timeout=3) as response:
                self.assertEqual(decode_subscription(response.read()), [link])
        finally:
            recovered.close()
        self.publication.restore(self.server.RequestHandlerClass)
        self.request('/api/rotate', {})
        with self.assertRaises(HTTPError) as expired:
            opener.open(base + subscription_path, timeout=3)
        self.assertEqual(expired.exception.code, 404)
        rotated = json.load(self.request('/api/status'))['public_urls']['public_subscription_url']
        with opener.open(base + urlsplit(rotated).path, timeout=3) as response:
            self.assertEqual(decode_subscription(response.read()), [link])
        disabled = json.load(self.request(path, {'enabled': False}))
        self.assertEqual(disabled['base'], '')
        self.assertFalse(self.store.public_subscription_settings()['enabled'])
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection(('127.0.0.1', port), timeout=2)

    @unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for trusted IP fixture')
    def test_public_subscription_reuses_ipv6_certificate_but_not_hysteria_fingerprint_certificate(self):
        root = self.store.data_dir
        hysteria_local_certificate(root)
        options = json.load(self.request('/api/status'))['public_subscription_tls']
        self.assertEqual(options, {'certificates': [], 'hysteria_local': True})
        with self.assertRaises(HTTPError) as local:
            self.request('/api/v1/public-subscription', {'enabled': True, 'port': 9443, 'host': 'hysteria2-router.invalid'})
        self.assertEqual(local.exception.code, 400)

        def openssl(*args):
            subprocess.run(['openssl', *args], check=True, capture_output=True)

        ca, ca_key = root / 'public-ca.pem', root / 'public-ca.key'
        ca_config = root / 'ca.cnf'
        ca_config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ca\n'
                             '[dn]\nCN=Test Router Public CA\n[ca]\nbasicConstraints=critical,CA:TRUE\n'
                             'keyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n')
        openssl('req', '-new', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '7',
                '-config', str(ca_config), '-out', str(ca), '-keyout', str(ca_key))

        def issue(address):
            cert, key = map(Path, ip_certificate_paths(root, address))
            cert.parent.mkdir(parents=True)
            ext, csr = cert.parent / 'ext.cnf', cert.parent / 'ip.csr'
            ext.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n'
                           'extendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid,issuer\n'
                           f'subjectKeyIdentifier=hash\nsubjectAltName=IP:{address}\n')
            openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=Router',
                    '-out', str(csr), '-keyout', str(key))
            openssl('x509', '-req', '-in', str(csr), '-CA', str(ca), '-CAkey', str(ca_key), '-CAcreateserial',
                    '-extfile', str(ext), '-days', '6', '-out', str(cert))
            self.store._save_ip_certificate('ipv6', {'enabled': True, 'address': address,
                                            'expires': int(time.time()) + 3600, 'challenge': 'tls-alpn'})

        host = '2606:4700::1111'
        issue(host)
        choices = json.load(self.request('/api/status'))['public_subscription_tls']['certificates']
        self.assertEqual(choices, [{'host': host, 'type': 'ip'}])
        with socket.socket() as free:
            free.bind(('127.0.0.1', 0))
            port = free.getsockname()[1]
        path = '/api/v1/public-subscription'
        data = {'enabled': True, 'port': port, 'host': host}
        context = ssl.create_default_context(cafile=str(ca))
        with patch.dict(os.environ, {'SSL_CERT_FILE': str(ca)}):
            publication = json.load(self.request(path, data))
            self.assertEqual(publication['base'], f'https://[{host}]:{port}')
            replacement = '2606:4700::1001'
            issue(replacement)
            self.publication.sync_ip_binding(self.server.RequestHandlerClass)
            self.assertEqual(self.publication.view()['base'], f'https://[{replacement}]:{port}')
            with context.wrap_socket(socket.create_connection(('127.0.0.1', port), 3),
                                     server_hostname=replacement) as stream:
                stream.sendall(f'GET /s/{self.store.token()} HTTP/1.0\r\n\r\n'.encode())
                with HTTPResponse(stream) as response:
                    response.begin()
                    self.assertEqual(response.status, 200)

    def test_management_backup_requires_auth_and_is_a_sqlite_snapshot(self):
        path = '/api/v1/settings/backup'
        with self.assertRaises(HTTPError) as no_auth:
            self.request(path, auth=False)
        self.assertEqual(no_auth.exception.code, 401)
        with self.request(path) as response:
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertIn('attachment', response.headers['Content-Disposition'])
            archive = Path(self.temp.name) / 'backup.db'
            archive.write_bytes(response.read())
        with closing(sqlite3.connect(archive)) as backup:
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(backup.execute("SELECT value FROM settings WHERE key = 'token'").fetchone()[0],
                             self.store.token())

    def test_single_node_export_requires_auth_and_uses_published_port(self):
        link = 'hysteria2://sample@node.example:8443?sni=cert.example#Published'
        path = '/api/v1/nodes/export?id=' + node_id(link)
        with self.assertRaises(HTTPError) as no_auth:
            self.request(path, auth=False)
        self.assertEqual(no_auth.exception.code, 401)
        self.store.add_manual(link)
        with self.assertRaises(HTTPError) as not_local:
            self.request(path)
        self.assertEqual(not_local.exception.code, 404)
        endpoint = {'public_host': 'node.example', 'port': 8443, 'public_port': 9443}
        with patch.object(self.store, 'local_links', return_value=[link]), \
             patch.object(self.store, 'independent_endpoints', return_value={node_id(link): endpoint}):
            with self.request(path) as response:
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(json.load(response)['uri'], link.replace(':8443?', ':9443?'))
            with self.request(path + '&format=mihomo') as response:
                exported = json.load(response)
                self.assertEqual(exported['filename'], 'hopworld-mihomo.yaml')
                config = yaml.safe_load(exported['content'])
                self.assertEqual(config['rules'], ['MATCH,HopWorld'])
                proxy = config['proxies'][0]
                self.assertEqual((proxy['port'], proxy['sni']), (9443, 'cert.example'))
                self.assertFalse(config['allow-lan'])
            with self.request(path + '&format=sing-box') as response:
                exported = json.load(response)
                self.assertEqual(exported['filename'], 'hopworld-sing-box.json')
                config = json.loads(exported['content'])
                self.assertEqual(config['route']['final'], 'HopWorld')
                proxy = config['outbounds'][0]
                self.assertEqual((proxy['server_port'], proxy['tls']['server_name']), (9443, 'cert.example'))
                self.assertEqual(config['inbounds'][0]['listen'], '127.0.0.1')
            with self.assertRaises(HTTPError) as unsupported:
                self.request(path + '&format=xray')
            self.assertEqual(unsupported.exception.code, 400)

    @unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for local TLS fixture')
    def test_import_certificate_checks_bound_domain_san_key_and_preserves_active_pair(self):
        root = Path(self.temp.name)
        domain = 'node.example.test'
        cert, key = root / 'fixture.pem', root / 'fixture.key'
        config = root / 'fixture.cnf'
        config.write_text(f'[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n'
                          f'[dn]\nCN=other.example.test\n[ext]\nsubjectAltName=DNS:{domain}\n')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                        '-keyout', str(key), '-out', str(cert), '-config', str(config)],
                       check=True, capture_output=True, timeout=15)
        data = {'domain': domain, 'certificate': cert.read_text(), 'private_key': key.read_text()}
        path = '/api/acme/import'
        for auth, origin, status in ((False, True, 401), (True, False, 403), (True, True, 400)):
            with self.assertRaises(HTTPError) as rejection:
                self.request(path, data, auth=auth, origin=origin)
            self.assertEqual(rejection.exception.code, status)
        with patch.object(self.store, 'require_domain_binding'):
            self.store.register_domain(domain, 'ipv4', 'direct')
            self.store.register_domain('other.example.test', 'ipv4', 'direct')
        with self.assertRaises(HTTPError) as wrong_san:
            self.request(path, {**data, 'domain': 'other.example.test'})
        self.assertEqual(wrong_san.exception.code, 400)
        self.assertIn('证书必须覆盖所选域名', json.load(wrong_san.exception)['error'])
        with self.request(path, data) as response:
            imported = json.load(response)['acme']
        self.assertEqual(imported['source'], 'manual')
        self.assertTrue(imported['certificate_valid'])
        self.assertFalse(imported['automatic'])
        self.assertNotIn(data['private_key'], json.dumps(imported))
        key_path = Path(imported['key_path'])
        self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(HTTPError) as broken_pair:
            self.request(path, {**data, 'private_key': 'invalid key'})
        self.assertEqual(broken_pair.exception.code, 400)
        self.assertEqual(key_path.read_bytes(), key.read_bytes())
        with patch('router.server.sync_certificate') as certbot:
            self.assertFalse(self.store.sync_acme_certificate())
            certbot.assert_not_called()
        with self.request('/api/acme/download?domain=' + domain) as response:
            self.assertEqual(response.read(), cert.read_bytes())

    def test_relay_endpoints_require_management_auth_and_redact_secrets(self):
        path = '/api/v1/relay'
        for endpoint in (path, path + '/logs'):
            with self.assertRaises(HTTPError) as no_auth:
                self.request(endpoint, auth=False)
            self.assertEqual(no_auth.exception.code, 401)
        with self.assertRaises(HTTPError) as no_origin:
            self.request(path, {'action': 'stop'}, origin=False)
        self.assertEqual(no_origin.exception.code, 403)
        key = 'test-secret-must-never-appear-in-json'
        with self.store.connect() as db:
            db.execute("INSERT INTO settings VALUES ('frp_relay', ?)",
                       (json.dumps({'enabled': False, 'token': key, 'ca': 'private-fixture'}),))
        with patch.object(self.store.relay, 'log_text', return_value=f'connection failed: {key}'):
            body = json.load(self.request(path))
            self.assertTrue(body['configured'])
            self.assertFalse(body['enabled'])
            self.assertNotIn('token', body)
            self.assertNotIn('ca', body)
            self.assertEqual(json.load(self.request(path + '/logs'))['text'], 'connection failed: [已隐藏]')

    def test_new_address_form_defaults_to_auto_ip_and_explains_invalid_domain(self):
        with patch.dict('os.environ', {'HOPWORLD_PUBLIC_HOST': ''}):
            self.assertEqual(json.load(self.request('/api/v1/cores/profile?id=xray-core'))['address_mode'], 'auto_ip')
        self.assertEqual(json.load(self.request('/api/v1/cores/profile?id=hysteria2'))['address_mode'], 'auto_ip')
        path = '/api/v1/cores/address-check'
        for host, message in (('', '请先填写订阅域名'), ('https://proxy.example.com:443/', '请填写域名')):
            with self.assertRaises(HTTPError) as response:
                self.request(path, {'mode': 'domain', 'family': 'ipv4', 'host': host})
            self.assertEqual(response.exception.code, 400)
            self.assertIn(message, json.load(response.exception)['error'])

    def test_domain_record_preview_shows_expected_values_before_public_dns_is_bound(self):
        path = '/api/v1/domains/records'
        with patch.object(self.store, '_detect_direct_public_ip', side_effect=['8.8.8.8', '2606:4700::1111']), \
             patch('router.server._lookup_addresses', return_value={'ipv4': {'1.1.1.1'}, 'ipv6': set()}) as lookup:
            result = json.load(self.request(path, {'host': 'node.example.test', 'family': 'dual', 'ingress': 'direct'}))
        lookup.assert_called_once_with('node.example.test', timeout=4)
        self.assertEqual(result['target'], '本机公网入口')
        self.assertEqual(result['records'], [
            {'type': 'A', 'name': 'node.example.test', 'values': ['8.8.8.8'], 'current_values': ['1.1.1.1']},
            {'type': 'AAAA', 'name': 'node.example.test', 'values': ['2606:4700::1111'], 'current_values': []},
        ])
        with patch.object(self.store, 'relay_settings', return_value={
            'domain': 'node.example.test', 'server': 'vps.example.test',
        }), patch('router.server._lookup_addresses', side_effect=[
            {'ipv4': {'1.1.1.1'}, 'ipv6': set()}, OSError('name not yet resolved'),
        ]) as lookup:
            result = json.load(self.request(path, {'host': '', 'family': 'ipv4', 'ingress': 'vps'}))
        self.assertEqual([call.args[0] for call in lookup.call_args_list], ['vps.example.test', 'node.example.test'])
        self.assertEqual(result, {'target': 'VPS', 'records': [
            {'type': 'A', 'name': 'node.example.test', 'values': ['1.1.1.1'], 'current_values': None},
        ]})
        self.assertEqual(self.store.bound_domains(), [])

    def test_vps_domain_binding_requires_registered_ingress_and_does_not_detect_home_ip(self):
        records = '/api/v1/domains/records'
        domain = {'host': 'node.example.test', 'family': 'ipv4', 'ingress': 'vps'}
        with patch.object(self.store, '_detect_direct_public_ip') as detect:
            for path in (records, '/api/v1/domains'):
                with self.assertRaises(HTTPError) as response:
                    self.request(path, domain)
                self.assertIn('登记 VPS 入口', json.load(response.exception)['error'])
            with patch.object(self.store, 'relay_settings', return_value={'domain': domain['host'], 'server': 'vps.example.test'}):
                for payload in ({**domain, 'host': 'other.example.test'}, {**domain, 'ingress': 'direct'}):
                    with self.assertRaises(HTTPError) as response:
                        self.request(records, payload)
                    self.assertEqual(response.exception.code, 400)
            detect.assert_not_called()

    def test_domain_binding_requires_all_published_addresses_to_match_and_blocks_enable(self):
        host = 'entry.example.test'
        answers = {'ipv4': {'8.8.8.8', '1.1.1.1'}, 'ipv6': set()}
        profile = {'public_host': host, 'port': 8443, 'public_listener': True, 'egress': 'direct',
                   'upstream_id': '', 'address_mode': 'domain', 'address_family': 'ipv4'}
        with patch.object(self.store, '_detect_direct_public_ip', return_value='8.8.8.8'), \
             patch('router.server._lookup_addresses', return_value=answers), \
             patch.object(self.store.xray, 'apply') as apply, \
             patch.object(self.store, '_apply_independent_profile') as start:
            check = {'mode': 'domain', 'family': 'ipv4', 'host': host}
            self.assertFalse(json.load(self.request('/api/v1/cores/address-check', check))['bound'])
            for path, payload in (
                ('/api/v1/cores/start', {'id': 'xray-core', 'profile': profile}),
                ('/api/local-node', {'enabled': True, 'public_host': host, 'egress_mode': 'default'}),
            ):
                with self.assertRaises(HTTPError) as response:
                    self.request(path, payload)
                self.assertIn('绑定', json.load(response.exception)['error'])
            start.assert_not_called()
            apply.assert_not_called()
            answers['ipv4'].remove('1.1.1.1')
            self.assertTrue(json.load(self.request('/api/v1/cores/address-check', check))['bound'])
            domains = json.load(self.request('/api/v1/domains', {'host': host, 'family': 'ipv4'}))['domains']
            self.assertEqual(domains, [{'host': host, 'family': 'ipv4'}])
            self.assertEqual(json.load(self.request('/api/v1/cores/profile?id=xray-core'))['bound_domains'], domains)
            answers['ipv4'].add('1.1.1.1')
            with self.assertRaises(HTTPError) as response:
                self.request('/api/v1/cores/start', {'id': 'xray-core', 'profile': profile})
            self.assertIn('请先绑定域名', json.load(response.exception)['error'])

    def test_automatic_ipv6_updates_subscription_without_changing_node_identity(self):
        path = '/api/v1/cores/start'
        first, second = '2606:4700::1111', '2606:4700::1001'
        core = self.store.independent['xray-core']
        profile = {'public_host': '', 'port': 8443, 'public_listener': True, 'egress': 'direct',
                   'upstream_id': '', 'address_mode': 'auto_ip', 'address_family': 'ipv6'}
        generated = []
        with patch('router.server.detect_public_ip', side_effect=[first, second, ValueError('offline')]) as detect, \
             patch.object(self.store.xray, 'generate_keys', return_value=('private', 'public')), \
             patch.object(core, 'locate', return_value='/fixture/xray'), \
             patch.object(core, 'apply_profile', side_effect=lambda config, links, cert, **kwargs: generated.extend(links)) as apply, \
             patch.object(core, 'links', side_effect=lambda: generated):
            self.assertTrue(json.load(self.request(path, {'id': 'xray-core', 'profile': profile}))['ok'])
            self.assertEqual(apply.call_args.kwargs['bind'], '::')
            node = self.store.nodes()[0]
            self.store.sync_independent_addresses()
            self.assertEqual(self.store.nodes()[0]['id'], node['id'])
            self.assertIn(f'@[{second}]:8443', self.store.node_link(node['id']))
            self.assertEqual(decode_subscription(self.store.subscription().encode()), [self.store.node_link(node['id'])])
            self.store.sync_independent_addresses()
            self.assertEqual(self.store.independent_profile_view('xray-core')['detected_host'], second)
            self.assertIn('保留上次地址', self.store.independent_profile_view('xray-core')['address_error'])
            apply.assert_called_once()
            self.assertEqual([call.args[0] for call in detect.call_args_list], [6, 6, 6])

    def test_dual_stack_ddns_updates_each_record_family_and_retains_last_address(self):
        profile = {'enabled': True, 'public_host': 'entry.example.test', 'ddns': True, 'address_family': 'dual'}
        self.store._save_independent_profiles({'xray-core': profile})
        v6 = '2606:4700::1111'
        def observe(version, **kwargs):
            if version == 6 and not ipv6_available:
                raise ValueError('offline')
            return v6 if version == 6 else ipv4
        ipv4, ipv6_available = '8.8.8.8', True
        with patch('router.server.detect_public_ip', side_effect=observe), \
             patch('router.server.publish_ddns') as publish, \
             patch.dict('os.environ', {'HOPWORLD_DDNS_PROVIDER': 'webhook'}):
            self.store.sync_independent_addresses()
            publish.assert_called_once_with('entry.example.test', ipv4=ipv4, ipv6=v6)
            self.store.sync_independent_addresses()
            self.assertEqual(publish.call_count, 1)
            ipv4, ipv6_available = '8.8.4.4', False
            self.store.sync_independent_addresses()
            publish.assert_called_with('entry.example.test', ipv4=ipv4)
            self.assertEqual(self.store.independent_profiles()['xray-core']['ddns_ipv6'], v6)

    @unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for local TLS fixture')
    def test_hysteria_automatic_ip_can_start_without_a_domain_certificate(self):
        core = self.store.independent['hysteria2']
        profile = {'public_host': '', 'port': 8442, 'public_listener': False, 'egress': 'direct',
                   'upstream_id': '', 'address_mode': 'auto_ip', 'address_family': 'ipv4'}
        path = '/api/v1/cores/start'
        view = json.load(self.request('/api/v1/cores/profile?id=hysteria2'))
        self.assertEqual(view['address_mode'], 'auto_ip')
        self.assertFalse(view['certificate_ready'])
        generated = []
        def record_links(config, links, cert, **kwargs):
            generated[:] = links

        with patch('router.server.detect_public_ip', side_effect=['8.8.8.8', '8.8.4.4']), \
             patch.object(core, 'locate', return_value='/fixture/hysteria'), \
             patch.object(core, 'apply_profile', side_effect=record_links) as apply, \
             patch.object(core, 'links', side_effect=lambda: generated):
            self.assertTrue(json.load(self.request(path, {'id': 'hysteria2', 'profile': profile}))['ok'])
            first = parse_qs(urlsplit(generated[0]).query)
            self.assertEqual(first['insecure'], ['1'])
            self.assertRegex(first['pinSHA256'][0], r'^[a-f0-9]{64}$')
            cert, key = apply.call_args.args[2]
            self.assertEqual(Path(key).stat().st_mode & 0o777, 0o600)
            self.assertIn('BEGIN CERTIFICATE', Path(cert).read_text())
            self.store.sync_independent_addresses()
            self.store.restore_independent_cores()
            self.assertEqual(parse_qs(urlsplit(generated[0]).query), first)
            uri, = decode_subscription(self.store.subscription().encode())
            self.assertEqual(urlsplit(uri).hostname, '8.8.4.4')
            self.assertEqual(parse_qs(urlsplit(uri).query), first)
            identifier = node_id(generated[0])
            export = '/api/v1/nodes/export?id=' + identifier
            mihomo = yaml.safe_load(json.load(self.request(export + '&format=mihomo'))['content'])
            self.assertEqual(mihomo['proxies'][0]['fingerprint'], first['pinSHA256'][0])
            singbox = json.loads(json.load(self.request(export + '&format=sing-box'))['content'])
            self.assertEqual(singbox['outbounds'][0]['tls']['certificate'], Path(cert).read_text())
            self.assertNotIn('insecure', singbox['outbounds'][0]['tls'])

    def test_quic_automatic_ip_keeps_certificate_name_in_the_exported_link(self):
        core = self.store.independent['hysteria2']
        profile = {'public_host': '', 'port': 8442, 'public_listener': True, 'egress': 'direct',
                   'upstream_id': '', 'address_mode': 'auto_ip', 'address_family': 'ipv6'}
        path = '/api/v1/cores/start'
        domain = 'cert.example.test'
        self.store.save_acme_settings({**self.store.acme_settings(), 'domain': domain})
        profile_url = '/api/v1/cores/profile?id=hysteria2'
        self.assertFalse(json.load(self.request(profile_url))['certificate_ready'])
        generated = []
        with patch('router.server.detect_public_ip', side_effect=['2606:4700::1111', '2606:4700::1001']), \
             patch.object(self.store, '_quic_certificate', return_value=('/cert', '/key')) as certificate, \
             patch.object(core, 'locate', return_value='/fixture/hysteria'), \
             patch.object(core, 'apply_profile', side_effect=lambda config, links, cert, **kwargs: generated.extend(links)), \
             patch.object(core, 'links', side_effect=lambda: generated):
            self.assertTrue(json.load(self.request(profile_url))['certificate_ready'])
            self.assertTrue(json.load(self.request(path, {'id': 'hysteria2', 'profile': profile}))['ok'])
            self.assertEqual(certificate.call_args.args[0]['public_host'], domain)
            self.store.sync_independent_addresses()
            uri, = decode_subscription(self.store.subscription().encode())
            self.assertIn('@[2606:4700::1001]:8442?', uri)
            self.assertIn(f'sni={domain}', uri)

    def test_hysteria_ip_certificate_renews_and_replaces_both_address_families(self):
        core = self.store.independent['hysteria2']
        generated = []

        def record_links(config, links, certificate, **kwargs):
            generated[:] = links

        for family, address, replacement in (
            ('ipv4', '8.8.8.8', '8.8.4.4'), ('ipv6', '2606:4700::1111', '2606:4700::1001'),
        ):
            with self.subTest(family=family), \
                 patch.object(self.store, '_detect_direct_public_ip', return_value=address) as detect, \
                 patch.object(core, 'locate', return_value='/fixture/hysteria'), \
                 patch.object(core, 'apply_profile', side_effect=record_links) as apply, \
                 patch.object(core, 'running', return_value=True), \
                 patch.object(core, 'links', side_effect=lambda: generated), \
                 patch('router.server.ensure_ip_certificate', return_value=int(time.time()) + 86400) as issue:
                profile = {'public_host': '', 'port': 8442, 'public_listener': True, 'egress': 'direct', 'upstream_id': '',
                           'address_mode': 'auto_ip', 'address_family': family, 'tls_mode': 'ip_acme',
                           'ip_certificate_email': 'owner@example.test'}
                path = '/api/v1/cores/start'
                self.assertTrue(json.load(self.request(path, {'id': 'hysteria2', 'profile': profile}))['ok'])
                detect.assert_called_once_with(4 if family == 'ipv4' else 6)
                issue.assert_called_once_with(self.store.data_dir, address, profile['ip_certificate_email'], challenge='http')
                before, = self.store.nodes()
                uri = self.store.node_link(before['id'], local_only=True)
                self.assertEqual(urlsplit(uri).hostname, address)
                self.assertEqual(urlsplit(uri).query, '')

                issue.return_value += 5 * 86400
                self.store.sync_hysteria_ip_certificate()
                issue.assert_called_with(self.store.data_dir, address, profile['ip_certificate_email'], renew=True, challenge='http')
                self.assertEqual(apply.call_count, 2)

                detect.return_value = replacement
                issue.side_effect = ValueError('外网无法访问验证端口')
                self.store.sync_hysteria_ip_certificate()
                self.assertEqual(apply.call_count, 2)
                self.assertEqual(self.store.nodes(), [])
                self.assertEqual(self.store.independent_profile_view('hysteria2')['address_error'], '外网无法访问验证端口')

                issue.side_effect = None
                self.store.sync_hysteria_ip_certificate()
                issue.assert_called_with(self.store.data_dir, replacement, profile['ip_certificate_email'], renew=True, challenge='http')
                after, = self.store.nodes()
                self.assertEqual(after['id'], before['id'])
                uri, = decode_subscription(self.store.subscription().encode())
                self.assertEqual(urlsplit(uri).hostname, replacement)
                self.assertEqual(urlsplit(uri).query, '')
                self.assertEqual(self.store.independent_profile_view('hysteria2')['address_error'], '')

    def test_certificate_page_manages_ip_families_without_starting_hysteria(self):
        addresses = {4: '8.8.8.8', 6: '2606:4700::1111'}
        path = '/api/acme/ip'
        previous_domain = self.store.acme_settings()
        with patch.object(self.store, '_detect_direct_public_ip', side_effect=addresses.__getitem__), \
             patch('router.server.ip_certificate_readiness', return_value={
                 'certbot_status': 'Certbot 5.10 已就绪', 'port_status': 'Router 无权监听 TCP 80',
             }), patch('router.server.ensure_ip_certificate', return_value=int(time.time()) + 86400) as issue:
            for family in ('ipv4', 'ipv6'):
                readiness = json.load(self.request(f'{path}?family={family}'))
                self.assertEqual(readiness['address'], addresses[int(family[-1])])
                self.assertIn('无权监听 TCP 80', readiness['port_status'])
                configured = json.load(self.request(path, {
                    'family': family, 'enabled': True, 'email': 'owner@example.test',
                }))
                self.assertEqual(configured['certificate']['address'], readiness['address'])
            self.assertEqual(issue.call_count, 2)
            self.assertEqual(self.store.independent_profiles(), {})
            self.assertEqual(self.store.acme_settings(), previous_domain)
            self.request(path, {'family': 'ipv4', 'enabled': False, 'email': ''}).close()
            addresses[6] = '2606:4700::1001'
            issue.return_value += 5 * 86400
            self.store.sync_managed_ip_certificates()
            issue.assert_called_with(self.store.data_dir, addresses[6], 'owner@example.test', renew=True, challenge='http')
            self.assertEqual(issue.call_count, 3)
            status = json.load(self.request('/api/status'))['ip_certificates']
            self.assertFalse(status['ipv4']['enabled'])
            self.assertEqual(status['ipv6']['address'], addresses[6])
            with self.assertRaises(HTTPError) as invalid:
                self.request(path + '?family=dual')
            self.assertEqual(invalid.exception.code, 400)

    def test_staging_checks_ip_family_and_rejects_a_changed_address_before_issuance(self):
        data = {'family': 'ipv6', 'target': '', 'email': 'owner@example.test', 'challenge': 'tls-alpn'}
        path = '/api/acme/preflight'
        host = '2606:4700::1111'
        with patch.object(self.store, '_detect_direct_public_ip', return_value=host), \
             patch('router.server.stage_certificate', return_value='tls-alpn') as stage:
            self.assertEqual(json.load(self.request(path, data)), {'target': host, 'challenge': 'tls-alpn'})
            stage.assert_called_once_with(self.store.data_dir, host, data['email'], 'tls-alpn')
            self.assertEqual(self.store.ip_certificates(), {})
        with patch.object(self.store, '_detect_direct_public_ip', return_value='2606:4700::1001'), \
             patch('router.server.ensure_ip_certificate') as issue:
            with self.assertRaises(HTTPError) as changed:
                self.request('/api/acme/ip', {'family': 'ipv6', 'enabled': True, 'email': data['email'],
                                             'challenge': data['challenge'], 'verified_target': host})
            self.assertIn('公网 IP 已变化', changed.exception.read().decode())
            issue.assert_not_called()

    def test_core_install_endpoint_reports_the_installed_copy(self):
        with patch('router.update_center.install_core', return_value={'id': 'xray-core', 'version': 'v9.9.9', 'path': '/tmp/xray'}) as install:
            raw = self.request('/api/v1/cores/install', {'id': 'xray-core'}).read().decode()
        body = json.loads(raw.strip().splitlines()[-1])
        install.assert_called_once()
        self.assertEqual(body['installed']['version'], 'v9.9.9')
        self.assertEqual(len(body['system']['cores']), 6)
        with patch('router.update_center.install_core', return_value={'id': 'tuic-server', 'version': 'v1.0.0', 'path': '/tmp/tuic-server'}):
            raw = self.request('/api/v1/cores/install', {'id': 'tuic-server'}).read().decode()
        self.assertEqual(json.loads(raw.strip().splitlines()[-1])['installed']['id'], 'tuic-server')
        with patch('router.update_center.check_update', return_value={'current': 'v1.0.0', 'latest': 'v2.0.0', 'update': True}):
            checked = json.load(self.request('/api/v1/cores/check', {'id': 'xray-core'}))
        self.assertTrue(checked['update'])
        self.assertEqual(checked['latest'], 'v2.0.0')
        with self.assertRaises(HTTPError) as stopped:
            self.request('/api/v1/cores/restart', {'id': 'xray-core'})
        self.assertIn('没有在运行', stopped.exception.read().decode())
        self.assertEqual(json.load(self.request('/api/v1/cores/logs', {'id': 'xray-core'}))['text'], '')
        self.assertEqual(json.load(self.request('/api/v1/cores/logs', {'id': 'hysteria2'}))['text'], '')
        self.assertEqual(json.load(self.request('/api/v1/cores/logs', {'id': 'tuic-server'}))['text'], '')

    def test_core_logs_include_independent_and_legacy_publisher_instances(self):
        path = '/api/v1/cores/logs'
        self.store._save_independent_profiles({
            'hysteria2@test': {'port': 9442, 'enabled': False},
            'mihomo@test': {'port': 8388, 'enabled': True},
        })
        self.store.hysteria.log_path.write_text('legacy Hysteria log')
        self.store.publisher_runtime('hysteria2').log_path.write_text('primary Hysteria log')
        self.store.publisher_runtime('hysteria2@test').log_path.write_text('other Hysteria log')
        self.store.publisher_runtime('mihomo@test').log_path.write_text('mihomo log')
        output = json.load(self.request(path, {'id': 'hysteria2'}))['text']
        self.assertIn('[旧版发布服务]\nlegacy Hysteria log', output)
        self.assertIn('[主发布实例]\nprimary Hysteria log', output)
        self.assertIn('[发布实例 · 9442 · hysteria2@test]\nother Hysteria log', output)
        self.assertNotIn('mihomo log', output)
        self.assertEqual(json.load(self.request(path, {'id': 'mihomo'}))['text'], 'mihomo log')

    def test_independent_hysteria_uses_its_own_profile_and_fixed_upstream(self):
        self.store.add_manual('socks5://user:fixture-secret@socks.example.test:1080#upstream')
        upstream_id = self.store.nodes()[0]['id']
        profile = {'public_host': 'entry.example.test', 'port': 8442, 'public_listener': False,
                   'egress': 'fixed', 'upstream_id': upstream_id}
        core = self.store.independent['hysteria2']
        with patch.object(self.store, 'require_domain_binding'), \
             patch.object(core, 'locate', return_value='/dummy/hysteria'), \
             patch.object(core, 'apply_profile') as apply, patch.object(core, 'stop') as stop, \
             patch.object(self.store, '_quic_certificate', return_value=('/cert.pem', '/key.pem')), \
             patch.object(self.store.xray, 'apply') as xray, patch.object(self.store.singbox, 'apply') as singbox:
            self.store.register_domain(profile['public_host'], 'ipv4')
            response = json.load(self.request('/api/v1/cores/start', {'id': 'hysteria2', 'profile': profile}))
            self.assertTrue(response['ok'])
            config = apply.call_args.args[0]
            self.assertEqual(config['listen'], '127.0.0.1:8442')
            self.assertEqual([route['type'] for route in config['outbounds']], ['socks5'])
            self.assertEqual(config['outbounds'][0]['socks5']['addr'], 'socks.example.test:1080')
            self.assertFalse(self.store.local_node_settings()['enabled'])
            saved = json.load(self.request('/api/v1/cores/profile?id=hysteria2'))
            self.assertEqual(saved['egress'], 'fixed')
            self.assertNotIn('password', saved)
            self.request('/api/node', {'id': upstream_id, 'enabled': False}).close()
            apply.assert_called_once()
            stop.assert_called_once()
            self.assertTrue(self.store.independent_profiles()['hysteria2']['enabled'])
            self.request('/api/v1/cores/stop', {'id': 'hysteria2'}).close()
            self.assertFalse(self.store.independent_profiles()['hysteria2']['enabled'])
            xray.assert_not_called()
            singbox.assert_not_called()

    def test_legacy_publishers_switch_to_independent_profiles(self):
        settings = self.store.xray.default_settings('entry.example.test')
        settings.update(
            enabled=True, legacy_singbox_enabled=True,
            uuid='a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', private_key='priv', public_key='pub',
            hy2_password='hy2-password-value', tuic_password='tuic-password-value',
        )
        self.store.save_local_node_settings(settings)
        start = '/api/v1/cores/start'
        legacy_singbox = self.store.singbox
        with patch.object(legacy_singbox, 'running', return_value=True) as quic_running, \
             patch.object(self.store.xray, 'running', return_value=True), \
             patch.object(legacy_singbox, 'stop', side_effect=lambda: setattr(quic_running, 'return_value', False)), \
             patch.object(self.store.xray, 'stop') as xray_stop, \
             patch.object(self.store, 'require_domain_binding'), \
             patch.object(self.store, '_quic_certificate', return_value=('/cert.pem', '/key.pem')):
            self.store.register_domain(settings['public_host'], 'ipv4')
            for core_id, port in (('xray-core', 8443), ('sing-box', 8444)):
                view = json.load(self.request('/api/v1/cores/profile?id=' + core_id))
                self.assertTrue(view['legacy'])
                self.assertEqual(view['port'], port)
                self.assertTrue(view['public_listener'])
                self.assertNotIn('password-value', json.dumps(view))
            profile = {'public_host': settings['public_host'], 'port': 8443, 'public_listener': True,
                       'egress': 'direct', 'upstream_id': ''}
            with self.assertRaises(HTTPError) as dependent:
                self.request(start, {'id': 'xray-core', 'profile': profile})
            self.assertIn('旧版 QUIC 服务', dependent.exception.read().decode())
            xray_stop.assert_not_called()
            with patch.object(self.store.independent['sing-box'], 'locate', return_value='/dummy/sing-box'), \
                 patch.object(self.store.independent['sing-box'], 'apply_profile') as activate:
                request = {'id': 'sing-box', 'profile': {**profile, 'port': 8444}}
                self.request(start, request).close()
                config, links, _ = activate.call_args.args
                self.assertEqual([item['listen_port'] for item in config['inbounds']], [8444, 8445])
                self.assertEqual([urlsplit(link).username for link in links], [settings['hy2_password'], settings['uuid']])
                self.assertEqual(list(config['outbounds']), [{'type': 'direct', 'tag': 'egress'}])
            self.assertFalse(self.store.local_node_status()['quic']['enabled'])
            expected_link = self.store.local_link(settings)
            with patch.object(self.store.independent['xray-core'], 'locate', return_value='/dummy/xray'), \
                 patch.object(self.store.independent['xray-core'], 'apply_profile') as activate:
                self.request(start, {'id': 'xray-core', 'profile': profile}).close()
                self.assertEqual(activate.call_args.args[1], [expected_link])
            self.assertFalse(self.store.local_node_settings()['enabled'])
            self.assertEqual(set(self.store.independent_profiles()), {'xray-core', 'sing-box'})

    def test_legacy_tuic_can_switch_to_native_without_removing_hysteria(self):
        settings = self.store.xray.default_settings('entry.example.test')
        settings.update(enabled=True, legacy_singbox_enabled=True,
                        uuid='a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', tuic_password='tuic-password-value')
        self.store.save_local_node_settings(settings)
        path = '/api/v1/cores/switch'
        target = self.store.independent['tuic-server']
        data = {'source': 'sing-box', 'target': 'tuic-server', 'protocol': 'tuic', 'port': 8445}
        with patch.object(target, 'locate', return_value='/dummy/tuic-server'), \
             patch.object(target, 'apply_profile', side_effect=ValueError('目标端口启动失败')) as start, \
             patch.object(self.store, '_quic_certificate', return_value=('/cert.pem', '/key.pem')):
            view = json.load(self.request(path + '?source=sing-box&protocol=tuic&port=8445'))
            self.assertEqual(view['target'], 'tuic-server')
            self.assertFalse(view['reason'])
            self.assertFalse(view['keep_egress'])
            with self.assertRaises(HTTPError):
                self.request(path, {**data, 'egress': 'keep'})
            start.assert_not_called()
            with self.assertRaises(HTTPError):
                self.request(path, {**data, 'egress': 'direct'})
            self.assertEqual(self.store.local_node_settings(), settings)
            self.assertEqual(self.store.independent_profiles(), {})
            start.side_effect = None
            self.request(path, {**data, 'egress': 'direct'}).close()

        quic = self.store.local_node_status()['quic']
        self.assertEqual((quic['hy2_port'], quic['tuic_port']), (8444, 0))
        self.assertEqual(self.store.local_node_settings()['legacy_quic_protocols'], ['hysteria2'])
        config, links, _ = start.call_args.args
        self.assertEqual(config['users'], {settings['uuid']: settings['tuic_password']})
        self.assertEqual(urlsplit(links[0]).port, 8445)

    def test_hysteria_switch_preserves_other_ports_on_the_target(self):
        host = 'entry.example.test'
        settings = self.store.xray.default_settings(host)
        settings.update(enabled=True, legacy_singbox_enabled=True, hy2_password='legacy-hysteria-password')
        self.store.save_local_node_settings(settings)
        native = {'public_host': host, 'port': 8442, 'password': 'native-hysteria-password',
                  'enabled': True, 'egress': 'direct', 'public_listener': True}
        self.store._save_independent_profiles({'hysteria2': native})

        endpoint = '/api/v1/cores/switch'
        with patch.object(self.store.independent['hysteria2'], 'locate', return_value='/dummy/hysteria'), \
             patch.object(self.store.independent['sing-box'], 'locate', return_value='/dummy/sing-box'), \
             patch.object(self.store, '_quic_certificate', return_value=('/cert.pem', '/key.pem')), \
             patch('router.independent_core.IndependentCore.apply_profile', autospec=True) as apply:
            cases = [('sing-box', 'hysteria2', 8444, 'direct'), ('hysteria2', 'sing-box', 8442, 'keep')]
            for source, target, port, egress in cases:
                view = json.load(self.request(f'{endpoint}?source={source}&protocol=hysteria2&port={port}'))
                self.assertEqual(view['reason'], '')
                data = {'source': source, 'target': target, 'port': port, 'protocol': 'hysteria2', 'egress': egress}
                self.request(endpoint, data).close()
                runtime, _, uris, _ = apply.call_args.args
                self.assertNotEqual(runtime.config_path, self.store.independent[target].config_path)
                self.assertEqual([urlsplit(uri).port for uri in uris], [port])
                profiles = self.store.independent_profiles()
                instance = next(key for key in profiles if key.startswith(target + '@'))
                profile = json.load(self.request(f'/api/v1/cores/profile?id={target}&instance={instance}'))
                self.assertEqual(profile['port'], port)
                if target == 'hysteria2':
                    self.assertEqual(profiles['hysteria2'], native)
        self.assertNotIn('sing-box', self.store.independent_profiles())
        quic = self.store.local_node_status()['quic']
        self.assertEqual((quic['hy2_port'], quic['tuic_port']), (0, 8445))

    def test_failed_switch_restores_legacy_service(self):
        original = {**self.store.local_node_settings(), 'enabled': True, 'public_host': '8.8.8.8'}
        self.store.save_local_node_settings(original)
        profile = {'public_host': '8.8.8.8', 'port': 8443, 'public_listener': True,
                   'egress': 'direct', 'upstream_id': ''}
        with patch.object(self.store.xray, 'running', return_value=True), \
             patch.object(self.store.xray, 'stop') as stop, \
             patch.object(self.store.xray, 'generate_keys', return_value=('priv', 'pub')), \
             patch.object(self.store.xray, 'apply') as restore, \
             patch.object(self.store, '_apply_independent_profile', side_effect=ValueError('端口占用')):
            with self.assertRaises(HTTPError) as failed:
                self.request('/api/v1/cores/start', {'id': 'xray-core', 'profile': profile})
            self.assertIn('端口占用', failed.exception.read().decode())
            stop.assert_called_once()
            restore.assert_called_once_with(original)
        self.assertEqual(self.store.local_node_settings(), original)
        self.assertEqual(self.store.independent_profiles(), {})

    def test_native_hysteria_start_stop_requires_xray_and_keeps_other_cores(self):
        start = '/api/v1/cores/start'
        data = {'id': 'hysteria2'}
        with self.assertRaises(HTTPError) as missing_dependency:
            self.request(start, data)
        self.assertIn('请先启动本地网络节点', missing_dependency.exception.read().decode())
        self.assertNotIn('native_hysteria_enabled', self.store.local_node_settings())

        settings = self.store.local_node_settings()
        self.store.save_local_node_settings({**settings, 'enabled': True, 'public_host': 'example.test'})
        with patch.object(self.store.xray, 'running', return_value=True), \
             patch.object(self.store.hysteria, 'apply', side_effect=ValueError('原生 Hysteria 2 需要有效证书')):
            with self.assertRaises(HTTPError) as failed_start:
                self.request(start, data)
            self.assertIn('需要有效证书', failed_start.exception.read().decode())
        self.assertNotIn('native_hysteria_enabled', self.store.local_node_settings())

        with patch.object(self.store.xray, 'running', return_value=True), \
             patch.object(self.store.xray, 'apply') as xray, patch.object(self.store.singbox, 'apply') as singbox, \
             patch.object(self.store.hysteria, 'apply') as native:
            result = json.load(self.request(start, data))
            saved = self.store.local_node_settings()
            self.assertTrue(saved['native_hysteria_enabled'])
            self.assertNotIn(saved['native_hysteria_password'], json.dumps(result))
            self.assertTrue(self.store.local_node_status()['native_hysteria']['enabled'])
            self.request('/api/v1/cores/stop', data).close()
            stopped = self.store.local_node_settings()
            self.assertFalse(stopped['native_hysteria_enabled'])
            self.assertTrue(stopped['enabled'])
            self.assertEqual(stopped['native_hysteria_password'], saved['native_hysteria_password'])
            self.assertEqual(native.call_count, 2)
            xray.assert_not_called()
            singbox.assert_not_called()

    def test_native_hysteria_readiness_checks_installed_cores_runtime_and_matching_certificate(self):
        path = '/api/v1/cores/hysteria2/readiness'
        with self.assertRaises(HTTPError) as unauthenticated:
            self.request(path, auth=False)
        self.assertEqual(unauthenticated.exception.code, 401)

        binary = Path(self.temp.name) / 'core'
        binary.touch(mode=0o700)
        cert = Path(self.temp.name) / 'cert.pem'
        key = Path(self.temp.name) / 'key.pem'
        cert.touch()
        key.touch()
        settings = self.store.local_node_settings()
        settings.update(public_host='entry.example.test', auto_public_host=False, enabled=False)
        self.store.save_local_node_settings(settings)
        acme = {**self.store.acme_settings(), 'cert_path': str(cert), 'key_path': str(key), 'domain': 'other.example.test'}
        with patch.object(self.store.xray, 'locate', return_value=None), \
             patch.object(self.store.hysteria, 'locate', return_value=str(binary)):
            readiness = json.load(self.request(path))
            self.assertFalse(readiness['xray_installed'])
            self.assertTrue(readiness['hysteria_installed'])
            self.assertTrue(readiness['domain_configured'])
            self.assertFalse(readiness['certificate_ready'])
        self.store.save_acme_settings(acme)
        with patch.object(self.store.xray, 'locate', return_value=str(binary)), \
             patch.object(self.store.xray, 'running', return_value=True), \
             patch('router.server.days_until_expiry', return_value=2):
            readiness = json.load(self.request(path))
            self.assertTrue(readiness['xray_installed'])
            self.assertFalse(readiness['local_running'])
            self.assertFalse(readiness['certificate_ready'])
            self.store.save_acme_settings({**acme, 'domain': settings['public_host']})
            self.store.save_local_node_settings({**settings, 'enabled': True})
            readiness = json.load(self.request(path))
            self.assertTrue(readiness['local_running'])
            self.assertTrue(readiness['certificate_ready'])
            self.store.save_local_node_settings({**settings, 'auto_public_host': True})
            readiness = json.load(self.request(path))
            self.assertFalse(readiness['domain_configured'])
            self.assertFalse(readiness['certificate_ready'])

    def test_core_uninstall_removes_the_bundled_copy_and_keeps_a_running_one(self):
        bundled = Path(self.store.data_dir) / 'bin' / 'hysteria'
        bundled.parent.mkdir()
        bundled.write_bytes(b'installed')
        body = json.load(self.request('/api/v1/cores/uninstall', {'id': 'hysteria2'}))
        self.assertEqual(body['removed']['id'], 'hysteria2')
        self.assertFalse(bundled.exists())
        self.assertEqual(len(body['system']['cores']), 6)
        running = Path(self.store.data_dir) / 'bin' / 'xray'
        running.write_bytes(b'running')
        self.store.xray.binary = str(running)
        with patch.object(self.store.xray, 'running', return_value=True):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/v1/cores/uninstall', {'id': 'xray-core'})
        self.assertIn('正在运行', error.exception.read().decode())
        self.assertTrue(running.exists())

    def test_authentication_subscription_and_failed_update(self):
        with self.assertRaises(HTTPError) as error: self.request('/api/status', auth=False)
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error: self.request('/api/rotate', {}, origin=False)
        self.assertEqual(error.exception.code, 403)
        with patch('router.server.build_opener', return_value=Opener(b'vless://example#node')):
            self.request('/api/sources', {'name': 'source', 'url': 'https://example.test/sub'}).close()
        status = json.load(self.request('/api/status'))
        self.assertEqual(status['sources'][0]['count'], 1)
        old = status['token']
        self.assertEqual(base64.b64decode(self.request('/s/' + old, auth=False).read()), b'vless://example#node')
        with patch('router.server.build_opener', side_effect=OSError('failed')):
            with self.assertRaises(HTTPError): self.request('/api/refresh', {'id': status['sources'][0]['id']})
        self.assertEqual(base64.b64decode(self.request('/s/' + old, auth=False).read()), b'vless://example#node')
        self.request('/api/rotate', {}).close()
        with self.assertRaises(HTTPError) as error: self.request('/s/' + old, auth=False)
        self.assertEqual(error.exception.code, 404)

    def test_rejects_insecure_upstream_url(self):
        with self.assertRaises(HTTPError): self.request('/api/sources', {'name':'x', 'url':'http://example.test'})

    def test_independent_acme_settings_preserve_node(self):
        settings = self.store.local_node_settings()
        settings.update({'enabled': True, 'public_host': 'node.example.com', 'port': 8443})
        self.store.save_local_node_settings(settings)
        certificate = {'domain': 'node.example.com', 'challenge': 'http', 'challenge_effective': 'HTTP-01'}
        with patch('router.server.sync_certificate', return_value=certificate) as sync, \
             patch.object(self.store.xray, 'apply') as apply:
            response = json.load(self.request('/api/acme', {'enabled': True, 'email': 'test@example.com', 'challenge': 'http'}))
            self.assertTrue(response['acme']['enabled'])
            self.assertEqual(response['acme']['domain'], 'node.example.com')
            sync.assert_called_once_with(self.store.data_dir, 'node.example.com', 'test@example.com', True, 'http')
            apply.assert_not_called()
        self.assertEqual(self.store.local_node_settings(), settings)
        response = json.load(self.request('/api/acme', {'enabled': False, 'email': '', 'challenge': 'auto'}))
        self.assertFalse(response['acme']['enabled'])
        self.assertEqual(self.store.local_node_settings(), settings)

    def test_independent_acme_rejects_invalid_inputs(self):
        original_node = self.store.local_node_settings()
        original_acme = self.store.acme_settings()
        valid = {'enabled': False, 'email': '', 'challenge': 'auto'}
        for change in ({'enabled': 'false'}, {'enabled': None}, {'email': 123}, {'email': 'invalid'},
                       {'challenge': None}, {'challenge': 'invalid'}, {'enabled': True, 'email': ''}):
            with self.subTest(change=change), self.assertRaises(HTTPError) as error:
                self.request('/api/acme', {**valid, **change})
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(self.store.acme_settings(), original_acme)
            self.assertEqual(self.store.local_node_settings(), original_node)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/acme', valid, auth=False)
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/acme', valid, origin=False)
        self.assertEqual(error.exception.code, 403)

    def test_acme_can_use_a_registered_domain_independently_of_the_local_node(self):
        domain = 'cert.example.test'
        payload = {'enabled': True, 'email': 'test@example.com', 'challenge': 'http', 'domain': domain}
        with self.assertRaises(HTTPError) as response:
            self.request('/api/acme', payload)
        self.assertIn('绑定', json.load(response.exception)['error'])
        with patch.object(self.store, 'require_domain_binding'), \
             patch('router.server.sync_certificate', return_value={'domain': domain}) as issue:
            self.store.register_domain(domain, 'dual')
            original = self.store.local_node_settings()
            result = json.load(self.request('/api/acme', payload))
            self.assertEqual(result['acme']['domain'], domain)
            self.assertEqual(self.store.local_node_settings(), original)
            self.assertTrue(self.store.sync_acme_certificate())
            self.assertEqual([call.args[1] for call in issue.call_args_list], [domain, domain])

    def test_certificate_actions_use_bound_domain_and_log_results(self):
        domain = 'node.example.com'
        self.store.save_local_node_settings({**self.store.local_node_settings(), 'public_host': domain})
        cert = self.store.data_dir / 'certs' / domain / 'fullchain.pem'
        self.store.save_acme_settings({
            'domain': domain, 'enabled': True, 'email': 'test@example.com', 'cert_path': str(cert),
        })
        with patch('router.server.sync_certificate', return_value={'domain': domain}) as sync, \
             patch.object(self.store, '_reload_quic'):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/acme/renew', {'domain': 'other.example.com'})
            self.assertEqual(error.exception.code, 400)
            result = json.load(self.request('/api/acme/renew', {'domain': domain}))
            sync.assert_called_once_with(self.store.data_dir, domain, 'test@example.com', True, 'auto', force=True)
            self.assertEqual(result['acme']['history'][0]['detail'], '续签成功')
        dns = {'status': 'ok', 'detail': '解析匹配'}
        with patch.object(self.store, 'dns_verify_for_settings', return_value=dns) as verify:
            result = json.load(self.request('/api/acme/dns', {'domain': domain}))
            self.assertEqual(verify.call_args.kwargs, {'force': True})
        self.assertEqual(result['dns'], dns)
        self.assertEqual([event['action'] for event in result['acme']['history']], ['检查解析', '立即续签'])

    def test_certificate_download_returns_only_the_public_chain(self):
        domain = 'node.example.com'
        self.store.save_local_node_settings({**self.store.local_node_settings(), 'public_host': domain})
        cert = self.store.data_dir / 'certs' / domain / 'fullchain.pem'
        cert.parent.mkdir(parents=True)
        chain = b'-----BEGIN CERTIFICATE-----\nQUJD\n-----END CERTIFICATE-----\n' * 2
        cert.write_bytes(chain)
        self.store.save_acme_settings({'domain': domain, 'cert_path': str(cert)})
        path = f'/api/acme/download?domain={domain}'
        with self.assertRaises(HTTPError) as error:
            self.request(path, auth=False)
        self.assertEqual(error.exception.code, 401)
        with self.request(path) as response:
            self.assertEqual(response.read(), chain)
            self.assertEqual(response.headers['Content-Disposition'], f'attachment; filename="{domain}.pem"')
        cert.write_bytes(chain + b'-----BEGIN PRIVATE KEY-----\nQUJD\n-----END PRIVATE KEY-----\n')
        with self.assertRaises(HTTPError) as error:
            self.request(path)
        self.assertEqual(error.exception.code, 400)
        outside = self.store.data_dir / 'outside.pem'
        outside.write_bytes(chain)
        cert.unlink()
        cert.symlink_to(outside)
        with self.assertRaises(HTTPError) as error:
            self.request(path)
        self.assertEqual(error.exception.code, 400)

    def test_local_traffic_requires_auth_and_reports_availability(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/local/traffic', auth=False)
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(json.load(self.request('/api/local/traffic')), {'state': 'inactive'})
        settings = self.store.local_node_settings()
        settings['enabled'] = True
        self.store.save_local_node_settings(settings)
        with patch.object(self.store.xray, 'running', return_value=False):
            self.assertEqual(json.load(self.request('/api/local/traffic')), {'state': 'stopped'})
        with patch.object(self.store.xray, 'running', return_value=True), \
             patch.object(self.store.xray, 'node_traffic', return_value=None):
            self.assertEqual(json.load(self.request('/api/local/traffic')), {'state': 'unavailable'})
        counters = {'up_bytes': 0, 'down_bytes': 0, 'total_bytes': 0, 'instance': '123'}
        with patch.object(self.store.xray, 'running', return_value=True), \
             patch.object(self.store.xray, 'node_traffic', return_value=counters):
            self.assertEqual(json.load(self.request('/api/local/traffic')), {'state': 'ok', **counters})

    def test_v1_traffic_history_is_authenticated(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/v1/traffic', auth=False)
        self.assertEqual(error.exception.code, 401)
        with self.request('/api/v1/traffic') as response:
            body = json.load(response)
        self.assertEqual(body['state'], 'unavailable')
        self.assertIsNone(body['today']['total_bytes'])
        self.assertEqual(len(body['daily']), 7)

    def test_local_node_joins_subscription_when_enabled(self):
        link = 'vless://a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89@8.8.8.8:8443?encryption=none&flow=xtls-rprx-vision&security=reality&sni=www.microsoft.com&fp=chrome&pbk=pub&sid=abcd&type=tcp#%E6%9C%AC%E5%9C%B0%E7%BD%91%E7%BB%9C'
        status = json.load(self.request('/api/status'))
        self.assertIn('local_node', status)
        self.assertFalse(status['local_node']['enabled'])
        with patch.object(self.store.xray, 'ensure_credentials', side_effect=lambda settings: {**settings, 'uuid':'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'private_key':'priv', 'public_key':'pub', 'short_id':'abcd'}), \
             patch.object(self.store.xray, 'apply', return_value=link) as apply_mock, \
             patch.object(self.store.xray, 'build', return_value=({}, link)):
            result = json.load(self.request('/api/local-node', {'enabled': True, 'public_host': '8.8.8.8', 'port': 8443}))
            self.assertTrue(result['local_node']['enabled'])
            apply_mock.assert_called_once()
            status = json.load(self.request('/api/status'))
            self.assertTrue(any(node.get('local') for node in status['nodes']))
            self.assertIn(b'8.8.8.8', base64.b64decode(self.request('/s/' + status['token'], auth=False).read()))
            self.request('/api/local-node', {'enabled': False, 'public_host': '8.8.8.8', 'port': 8443}).close()
        status = json.load(self.request('/api/status'))
        self.assertFalse(status['local_node']['enabled'])
        self.assertFalse(any(node.get('local') for node in status['nodes']))

    def test_local_node_name_appears_in_subscription(self):
        with patch.object(self.store.xray, 'ensure_credentials', side_effect=lambda settings: {**settings, 'uuid':'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'private_key':'priv', 'public_key':'pub', 'short_id':'abcd'}), \
             patch.object(self.store.xray, 'apply', return_value=''):
            result = json.load(self.request('/api/local-node', {
                'enabled': True, 'public_host': '8.8.8.8', 'port': 8443, 'name': '家里宽带',
            }))
            self.assertEqual(result['local_node']['name'], '家里宽带')
            status = json.load(self.request('/api/status'))
            local = next(node for node in status['nodes'] if node.get('local'))
            self.assertEqual(local['title'], '家里宽带')
            body = base64.b64decode(self.request('/s/' + status['token'], auth=False).read()).decode()
            self.assertIn(quote('家里宽带'), body)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/local-node', {'enabled': False, 'public_host': '8.8.8.8', 'port': 8443, 'name': ' '})
        self.assertEqual(error.exception.code, 400)

    def test_local_network_subscription_includes_hysteria2_and_tuic(self):
        settings = self.store.xray.default_settings('8.8.8.8')
        settings.update({
            'enabled': True, 'public_host': '8.8.8.8', 'port': 8443, 'name': '本地网络',
            'legacy_singbox_enabled': True,
            'uuid': 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89',
            'private_key': 'priv', 'public_key': 'pub', 'short_id': 'abcd',
            'hy2_password': 'hy2-password-value', 'tuic_password': 'tuic-password-value',
        })
        self.store.save_local_node_settings(settings)
        self.store.singbox.meta_path.write_text('{"host": "8.8.8.8", "port": 8443}', encoding='utf-8')
        idle = json.load(self.request('/api/status'))
        self.assertIn('sing-box', idle['local_node']['quic']['error'])
        with patch.object(self.store.singbox, '_alive_locked', return_value=True):
            status = json.load(self.request('/api/status'))
            body = base64.b64decode(self.request('/s/' + status['token'], auth=False).read()).decode()
        self.assertEqual({node['protocol'] for node in status['nodes'] if node.get('local')}, {'vless', 'hysteria2', 'tuic'})
        self.assertIn('hysteria2://', body)
        self.assertIn('tuic://', body)
        self.assertNotIn('hy2-password-value', json.dumps(status))
        self.assertNotIn('tuic-password-value', json.dumps(status))

    def test_singbox_redetects_installed_binary_without_applying_xray(self):
        settings = self.store.local_node_settings()
        settings.update({'enabled': True, 'public_host': '8.8.8.8', 'port': 8443, 'legacy_singbox_enabled': True})
        self.store.save_local_node_settings(settings)

        def locate():
            self.store.singbox.binary = '/usr/local/bin/sing-box'
            return self.store.singbox.binary

        with patch.object(self.store.singbox, 'locate', side_effect=locate), \
             patch.object(self.store.singbox, 'apply', return_value=[]) as apply, \
             patch.object(self.store.singbox, 'links', return_value=['hysteria2://test', 'tuic://test']), \
             patch.object(self.store.xray, 'apply') as xray_apply:
            result = json.load(self.request('/api/local/singbox', {}))
        apply.assert_called_once()
        xray_apply.assert_not_called()
        self.assertTrue(result['local_node']['quic']['available'])

    def test_singbox_redetection_reports_missing_binary(self):
        with patch.object(self.store.singbox, 'locate', return_value=None):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/local/singbox', {})
        self.assertEqual(error.exception.code, 400)
        self.assertIn('sing-box', error.exception.read().decode())

    def test_quic_failure_does_not_stop_vless(self):
        credentials = {'uuid': 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'private_key': 'priv', 'public_key': 'pub', 'short_id': 'abcd'}
        with patch.object(self.store, 'require_domain_binding'), \
             patch.object(self.store, 'attach_dns_hints', side_effect=lambda view, settings: view), \
             patch.object(self.store.xray, 'ensure_credentials', side_effect=lambda settings: {**settings, **credentials}), \
             patch.object(self.store.xray, 'apply') as apply, \
             patch.object(self.store.xray, 'stop') as stop, \
             patch.object(self.store.singbox, 'apply', side_effect=ValueError('cannot start')):
            view = json.load(self.request('/api/local-node', {'enabled': True, 'public_host': 'entry.example.com'}))['local_node']
        self.assertTrue(view['enabled'])
        self.assertFalse(view['quic']['available'])
        self.assertTrue(self.store.local_node_settings()['enabled'])
        apply.assert_called_once()
        stop.assert_not_called()

    def test_local_node_requires_public_host(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/local-node', {'enabled': True, 'public_host': '', 'port': 8443})
        self.assertEqual(error.exception.code, 400)

    def test_failed_local_node_start_preserves_saved_settings(self):
        original = self.store.local_node_settings()
        with patch.object(self.store.xray, 'ensure_credentials', side_effect=lambda settings: settings), \
             patch.object(self.store.xray, 'apply', side_effect=ValueError('无法启动 Xray')):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/local-node', {'enabled': True, 'public_host': '8.8.8.8', 'port': 8443})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.store.local_node_settings(), original)
        self.assertFalse(json.load(self.request('/api/status'))['local_node']['enabled'])

    def test_immediate_xray_exit_does_not_enable_node(self):
        creds = {'uuid':'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'private_key':'priv', 'public_key':'pub', 'short_id':'abcd'}
        self.store.xray.binary = '/fake/xray'
        with patch.object(self.store.xray, 'ensure_credentials', side_effect=lambda settings: {**settings, **creds}), \
             patch('router.xray_service.subprocess.run'), \
             patch('router.xray_service.subprocess.Popen') as popen:
            popen.return_value.wait.return_value = 1
            with self.assertRaises(HTTPError) as error:
                self.request('/api/local-node', {'enabled': True, 'public_host': '8.8.8.8', 'port': 8443})
        self.assertEqual(error.exception.code, 400)
        self.assertFalse(self.store.local_node_settings()['enabled'])
        popen.assert_called_once()
        self.assertFalse(self.store.xray.config_path.exists())

    def test_auto_pool_tracks_enabled_upstreams_without_self_or_unsupported(self):
        self.request('/api/manual', {'content': '\n'.join([
            'trojan://secret@example.com:443?security=tls#good',
            'trojan://secret@127.0.0.1:8443?security=tls#loop',
            'vless://not-a-uuid@example.net:443#unsupported',
        ])}).close()
        nodes = json.load(self.request('/api/status'))['nodes']
        good = next(n for n in nodes if n['title'] == 'good')
        creds = {'uuid':'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'private_key':'priv', 'public_key':'pub', 'short_id':'abcd'}
        with patch.object(self.store.xray, 'ensure_credentials', side_effect=lambda settings: {**settings, **creds}), \
             patch.object(self.store.xray, 'apply') as apply:
            result = json.load(self.request('/api/local-node', {'enabled': True, 'public_host': '8.8.8.8', 'port': 8443, 'egress_mode': 'auto', 'auto_target': 'youtube', 'auto_interval': 300}))
            self.assertEqual(result['local_node']['auto_pool_count'], 1)
            self.assertEqual(result['local_node']['auto_interval'], 300)
            self.assertNotIn('secret', json.dumps(result))
            self.assertEqual(self.store.local_node_settings()['auto_probe_url'], 'https://www.youtube.com/generate_204')
            self.request('/api/node', {'id': good['id'], 'enabled': False}).close()
            self.assertEqual(self.store.local_node_settings()['proxy_pool'], [])
            self.assertEqual(apply.call_args.args[0]['proxy_pool'], [])

    def test_fixed_upstream_blocks_when_disabled_removed_or_unsupported(self):
        self.assertEqual(self.store.local_node_settings()['egress_mode'], 'fixed')
        self.store.add_manual('\n'.join([
            'http://admin:proxy-password@example.net:8080#chosen',
            'trojan://other-password@example.com:443?security=tls#other',
            'vless://not-a-uuid@example.org:443#unsupported',
        ]))
        nodes = {n['title']: n['id'] for n in self.store.nodes()}
        credentials = {'uuid': 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'private_key': 'priv', 'public_key': 'pub', 'short_id': 'abcd'}
        payload = {'enabled': True, 'public_host': 'entry.example.net', 'egress_mode': 'fixed', 'upstream_id': nodes['chosen']}
        with patch.object(self.store, 'require_domain_binding'), \
             patch.object(self.store, 'attach_dns_hints', side_effect=lambda view, _: view), \
             patch.object(self.store.xray, 'ensure_credentials', side_effect=lambda settings: {**settings, **credentials}), \
             patch.object(self.store.xray, 'apply') as apply:
            view = json.load(self.request('/api/local-node', payload))['local_node']
            self.assertTrue(view['upstream_ready'])
            self.assertEqual(view['upstream_id'], nodes['chosen'])
            self.assertNotIn('proxy-password', json.dumps(view))
            config, _ = self.store.xray.build(apply.call_args.args[0])
            self.assertEqual([item['protocol'] for item in config['outbounds']], ['blackhole', 'http'])
            self.assertEqual(config['routing']['rules'][-1]['network'], 'tcp')
            self.assertNotIn('balancers', config['routing'])

            self.request('/api/node', {'id': nodes['chosen'], 'enabled': False}).close()
            blocked, _ = self.store.xray.build(apply.call_args.args[0])
            self.assertEqual([item['protocol'] for item in blocked['outbounds']], ['blackhole'])
            self.assertFalse(self.store.local_node_status()['upstream_ready'])

            self.request('/api/node', {'id': nodes['chosen'], 'enabled': True}).close()
            self.assertTrue(self.store.local_node_status()['upstream_ready'])
            self.request('/api/manual/delete', {'id': nodes['chosen']}).close()
            self.assertEqual(self.store.local_node_settings()['proxy_pool'], [])
            self.assertEqual(self.store.local_node_settings()['upstream_id'], nodes['chosen'])
            # Rebuild the pool from current records even if an old snapshot remains in storage.
            settings = self.store.local_node_settings()
            settings['proxy_pool'] = [config['outbounds'][1]]
            self.store.save_local_node_settings(settings)
            self.store.restore_local_node()
            self.assertEqual(apply.call_args.args[0]['proxy_pool'], [])

            view = json.load(self.request('/api/local-node', {**payload, 'upstream_id': nodes['unsupported']}))['local_node']
            self.assertFalse(view['upstream_ready'])
            self.assertEqual(self.store.local_node_settings()['proxy_pool'], [])

    def test_fixed_upstream_uses_any_enabled_source_for_a_shared_node(self):
        link = 'trojan://secret@example.net:443?security=tls#shared'
        first = self.store.add('第一组', 'https://example.net/sub1')
        second = self.store.add('第二组', 'https://example.net/sub2')
        with self.store.connect() as db:
            db.execute('UPDATE sources SET links=?', (json.dumps([link]),))
        selected = self.store.nodes()[0]['id']
        settings = self.store.local_node_settings()
        settings.update({'enabled': True, 'upstream_id': selected})
        self.store._refresh_egress_pool(settings)
        self.store.save_local_node_settings(settings)
        self.assertEqual(len(settings['proxy_pool']), 1)

        with patch.object(self.store.xray, 'apply') as apply:
            self.request('/api/sources/settings', {'id': first, 'enabled': False}).close()
            self.assertEqual(len(self.store.local_node_settings()['proxy_pool']), 1)
            apply.assert_not_called()
            self.request('/api/sources/settings', {'id': second, 'enabled': False}).close()
            self.assertEqual(apply.call_args.args[0]['proxy_pool'], [])
            self.assertEqual(self.store.local_node_settings()['upstream_id'], selected)

    def test_discovered_proxy_is_used_only_as_local_node_outbound(self):
        creds = {'uuid':'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'private_key':'priv', 'public_key':'pub', 'short_id':'abcd'}
        with patch('router.server.discover', return_value=[{'protocol': 'http', 'port': 7890, 'tcp': 'supported', 'udp': 'unsupported'}]), \
             patch.object(self.store.xray, 'ensure_credentials', side_effect=lambda settings: {**settings, **creds}), \
             patch.object(self.store.xray, 'apply') as apply:
            result = json.load(self.request('/api/local-node', {'enabled': True, 'public_host': '8.8.8.8', 'port': 8443, 'egress_mode': 'local_proxy', 'local_proxy': 'http:7890'}))
            self.assertEqual(result['local_node']['local_proxy'], 'http:7890')
            config, _ = self.store.xray.build(apply.call_args.args[0])
            self.assertEqual(config['outbounds'][0]['settings']['servers'][0], {'address': '127.0.0.1', 'port': 7890})
            self.assertNotIn('observatory', config)
            self.assertEqual(config['routing']['rules'], [{'type': 'field', 'inboundTag': ['internal-metrics-in'], 'outboundTag': 'internal-metrics'}])

    def test_auto_pool_does_not_truncate_enabled_nodes(self):
        self.store.add_manual('\n'.join(f'trojan://secret@node{i}.example.com:443?security=tls' for i in range(40)))
        self.assertEqual(len(self.store._auto_pool({'public_host': 'entry.example.com', 'port': 8443})), 40)

    def test_auto_status_maps_observations_to_nodes_and_hides_secrets(self):
        self.store.add_manual('trojan://secret@example.com:443?security=tls#线路A')
        node = self.store.nodes()[0]
        settings = self.store.local_node_settings()
        settings.update({'enabled': True, 'egress_mode': 'auto', 'proxy_pool': [{}], 'auto_pool_ids': [node['id']]})
        self.store.save_local_node_settings(settings)
        data = {'candidate-0': {'alive': True, 'delay': 42, 'last_try_time': 1000, 'other': 'private'}}
        with patch.object(self.store.xray, 'running', return_value=True), \
             patch.object(self.store.xray, 'auto_observations', return_value=data), \
             patch('router.server.time.time', return_value=1010):
            status = json.load(self.request('/api/local/auto-status'))
        self.assertEqual(status['state'], 'ok')
        self.assertEqual(status['nodes'][0]['title'], '线路A')
        self.assertEqual(status['nodes'][0]['delay_ms'], 42)
        self.assertNotIn('secret', json.dumps(status))
        self.assertNotIn('private', json.dumps(status))
        with patch.object(self.store.xray, 'running', return_value=True), \
             patch.object(self.store.xray, 'auto_observations', return_value=data), \
             patch('router.server.time.time', return_value=1200):
            self.assertEqual(self.store.auto_observation_status()['nodes'][0]['state'], 'stale')
        settings['auto_interval'] = 300
        self.store.save_local_node_settings(settings)
        with patch.object(self.store.xray, 'running', return_value=True), \
             patch.object(self.store.xray, 'auto_observations', return_value=data), \
             patch('router.server.time.time', return_value=1200):
            self.assertEqual(self.store.auto_observation_status()['nodes'][0]['state'], 'ok')

    def test_auto_interval_rejects_invalid_values(self):
        for value in (True, '60', 1, 301):
            with self.subTest(value=value), self.assertRaises(HTTPError) as error:
                self.request('/api/local-node', {'enabled': False, 'auto_interval': value})
            self.assertEqual(error.exception.code, 400)

    def test_local_node_auto_public_host_updates_subscription(self):
        reality = 'vless://a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89@8.8.4.4:8443?encryption=none&flow=xtls-rprx-vision&security=reality&sni=www.microsoft.com&fp=chrome&pbk=pub&sid=abcd&type=tcp#%E6%9C%AC%E5%9C%B0%E7%BD%91%E7%BB%9C'
        def build_link(settings):
            host = settings.get('public_host', '8.8.4.4')
            link = reality.replace('@8.8.4.4:', f'@{host}:')
            return ({}, link)
        with patch('router.server.detect_public_host', return_value='8.8.4.4'), \
             patch.object(self.store.xray, 'ensure_credentials', side_effect=lambda settings: {**settings, 'uuid':'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'private_key':'priv', 'public_key':'pub', 'short_id':'abcd'}), \
             patch.object(self.store.xray, 'apply', return_value=reality), \
             patch.object(self.store.xray, 'build', side_effect=build_link):
            json.load(self.request('/api/local-node', {
                'enabled': True, 'public_host': '', 'port': 8443,
                'auto_public_host': True, 'public_host_prefer': 'ipv4',
            }))
            status = json.load(self.request('/api/status'))
            self.assertTrue(status['local_node']['auto_public_host'])
            self.assertEqual(status['local_node']['public_host'], '8.8.4.4')
            body = base64.b64decode(self.request('/s/' + status['token'], auth=False).read()).decode()
            self.assertIn('8.8.4.4', body)
        with patch('router.server.detect_public_host', return_value='203.0.113.10'):
            self.store.sync_auto_public_host(force=True)
            status = json.load(self.request('/api/status'))
            self.assertEqual(status['local_node']['public_host'], '203.0.113.10')
            body = base64.b64decode(self.request('/s/' + status['token'], auth=False).read()).decode()
            self.assertIn('203.0.113.10', body)
            self.assertNotIn('8.8.4.4', body)

    def test_manual_nodes_selection_and_persistence(self):
        self.request('/api/manual', {'content': 'vless://example#first\ntrojan://example#second'}).close()
        status = json.load(self.request('/api/status'))
        self.assertEqual([node['title'] for node in status['nodes']], ['first', 'second'])
        self.assertTrue(all(node['manual'] for node in status['nodes']))
        first = status['nodes'][0]['id']
        self.request('/api/node', {'id': first, 'enabled': False}).close()
        restored = Store(self.store.path)
        self.assertEqual(base64.b64decode(restored.subscription()), b'trojan://example#second')
        self.request('/api/manual/delete', {'id': first}).close()
        self.assertEqual(len(json.load(self.request('/api/status'))['nodes']), 1)

    def test_node_test_runs_and_returns_result(self):
        self.request('/api/manual', {'content': 'trojan://secret@example.test:443?security=tls'}).close()
        node = json.load(self.request('/api/status'))['nodes'][0]
        with patch('router.server.probe', return_value={'status':'ok', 'latency_ms':42, 'detail':'目标站点响应正常'}):
            self.request('/api/probe', {'id': node['id']}).close()
            for _ in range(100):
                state = json.load(self.request('/api/status'))['nodes'][0]['probe']
                if state['status'] != 'testing': break
                time.sleep(.01)
        self.assertEqual(state['status'], 'ok')
        self.assertEqual(state['latency_ms'], 42)

    def test_batch_probe_also_tests_nodes_excluded_from_subscription(self):
        self.request('/api/manual', {'content': 'trojan://a@example.test:443\ntrojan://b@example.test:443'}).close()
        nodes = json.load(self.request('/api/status'))['nodes']
        self.request('/api/node', {'id': nodes[0]['id'], 'enabled': False}).close()
        with patch('router.server.probe', return_value={'status':'ok', 'latency_ms':20, 'detail':'ok'}) as mock:
            self.request('/api/batch/probe', {}).close()
            for _ in range(100):
                status = json.load(self.request('/api/status'))
                if not status['jobs']['probe']['running']: break
                time.sleep(.01)
        self.assertEqual(status['jobs']['probe']['total'], 2)
        self.assertEqual(status['jobs']['probe']['done'], 2)
        self.assertEqual(mock.call_count, 2)
        self.assertTrue(all(node['probe']['status'] == 'ok' for node in status['nodes']))
        self.assertEqual(len(base64.b64decode(self.store.subscription()).splitlines()), 1)

    def test_batch_probe_limits_selection_and_uses_saved_target(self):
        self.store.add_manual('trojan://a@example.test:443#first\ntrojan://b@example.test:443#second')
        selected, other = self.store.nodes()
        selection = [selected['id'], selected['id']]
        settings = {'timezone': 'UTC', 'probe_target': 'cloudflare'}
        saved = json.load(self.request('/api/v1/settings', settings))
        self.assertEqual(saved['timezone'], 'UTC')
        self.assertEqual(saved['probe_target'], 'cloudflare')
        self.assertEqual(saved['name'], 'HopWorld-Router')
        self.assertEqual(Store(self.store.path).probe_target(), 'cloudflare')
        for invalid in ([], 'invalid', [3], [selected['id'], 'unknown']):
            with self.assertRaises(HTTPError):
                self.request('/api/batch/probe', {'node_ids': invalid})
        with self.assertRaises(HTTPError):
            self.request('/api/batch/probe', {'node_ids': selection, 'groups': ['manual']})
        with patch('router.server.probe', return_value={'status': 'ok', 'latency_ms': 20}) as mock:
            self.request('/api/batch/probe', {'node_ids': selection}).close()
            for _ in range(100):
                status = json.load(self.request('/api/status'))
                if not status['jobs']['probe']['running']:
                    break
                time.sleep(.01)
            mock.assert_called_once()
            self.assertIn('#first', mock.call_args.args[0])
            self.assertEqual(mock.call_args.kwargs['target'], 'cloudflare')
        job = status['jobs']['probe']
        self.assertEqual((job['done'], job['total'], job['failed']), (1, 1, 0))
        results = {node['id']: node['probe'] for node in status['nodes']}
        self.assertEqual(results[selected['id']]['target'], 'Cloudflare')
        self.assertIsNone(results[other['id']])

    def test_independent_single_and_batch_probes_use_the_publishing_core(self):
        uri = 'ss://YWVzLTI1Ni1nY206c2VjcmV0@example.test:8388#native'
        identifier = node_id(uri)
        endpoint = {'core_id': 'mihomo', 'port': 8388, 'public_host': 'relay.example.test', 'public_port': 18388}
        with patch.object(self.store, 'local_links', return_value=[uri]), \
             patch.object(self.store, 'independent_endpoints', return_value={identifier: endpoint}), \
             patch.object(self.store.independent['mihomo'], 'locate', return_value='/my/mihomo'), \
             patch('router.server.probe', return_value={'status': 'ok', 'latency_ms': 20}) as mock:
            for path, body in (('/api/probe', {'id': identifier}), ('/api/batch/probe', {'node_ids': [identifier]})):
                self.request(path, {**body, 'target': 'youtube'}).close()
                for _ in range(100):
                    status = json.load(self.request('/api/status'))
                    if not status['jobs']['probe']['running'] and (status['nodes'][0]['probe'] or {}).get('status') == 'ok':
                        break
                    time.sleep(.01)
        self.assertEqual(mock.call_count, 2)
        for args, kwargs in mock.call_args_list:
            self.assertIn('@relay.example.test:18388', args[0])
            self.assertEqual(kwargs, {'core': 'mihomo', 'client_binary': '/my/mihomo', 'target': 'youtube'})

    def test_lightweight_tests_use_client_and_send_the_selected_web_target(self):
        binary = self.store.data_dir / 'bin' / 'hopworld-probe'
        binary.parent.mkdir()
        binary.touch(mode=0o700)
        link = 'hy2://secret@example.test:443#light'
        self.store.add_manual(link)
        identifier = self.store.nodes()[0]['id']
        with patch('router.server.probe', side_effect=AssertionError('full probe called')) as full, \
             patch('router.server.probe_light', return_value={'status': 'ok', 'latency_ms': 20}) as light:
            for path, selection in (('/api/probe', {'id': identifier}), ('/api/batch/probe', {'node_ids': [identifier]})):
                self.request(path, {**selection, 'mode': 'light', 'target': 'cloudflare'}).close()
                for _ in range(100):
                    status = json.load(self.request('/api/status'))
                    result = status['nodes'][0]['probe'] or {}
                    if not status['jobs']['probe']['running'] and result.get('status') == 'ok':
                        break
                    time.sleep(.01)
                self.assertEqual((result['status'], result['mode'], result['target']), ('ok', 'light', 'Cloudflare'))
            self.assertEqual(status['jobs']['probe']['mode'], 'light')
            self.assertEqual((status['jobs']['probe']['done'], status['jobs']['probe']['failed']), (1, 0))
            full.assert_not_called()
            self.assertEqual([call.args for call in light.call_args_list], [(link, str(binary), 'cloudflare')] * 2)
        with self.assertRaises(HTTPError):
            self.request('/api/probe', {'id': identifier, 'mode': 'bad'})

    def test_imported_hysteria_probes_prefer_native_client_and_allow_singbox_fallback(self):
        self.store.add_manual('hy2://secret@example.test:443#test')
        identifier = self.store.nodes()[0]['id']
        with patch.object(self.store.hysteria, 'locate', return_value=sys.executable) as hysteria, \
             patch.object(self.store.singbox, 'locate', return_value=None), \
             patch('router.server.probe', return_value={'status': 'ok', 'latency_ms': 20}) as mock:
            for path, body, target in (
                ('/api/probe', {'id': identifier}, 'baidu'),
                ('/api/batch/probe', {'node_ids': [identifier]}, 'cloudflare'),
                ('/api/probe', {'id': identifier}, 'google'),
            ):
                if target == 'google':
                    hysteria.return_value = None
                    mock.return_value = {'status': 'unavailable', 'detail': 'missing sing-box'}
                self.request(path, {**body, 'target': target}).close()
                for _ in range(100):
                    status = json.load(self.request('/api/status'))
                    result = status['nodes'][0]['probe'] or {}
                    if not status['jobs']['probe']['running'] and result.get('status') == mock.return_value['status']:
                        break
                    time.sleep(.01)
        self.assertEqual([call.kwargs for call in mock.call_args_list], [
            {'target': 'baidu', 'core': 'hysteria2', 'client_binary': sys.executable},
            {'target': 'cloudflare', 'core': 'hysteria2', 'client_binary': sys.executable},
            {'target': 'google', 'singbox_binary': None},
        ])
        self.assertIn('Hysteria 2 或 sing-box', result['detail'])

    def test_batch_results_are_sorted_by_success_then_latency(self):
        self.request('/api/manual', {'content': 'trojan://slow@example.test:443#slow\ntrojan://failed@example.test:443#failed\ntrojan://fast@example.test:443#fast'}).close()
        def fake_probe(link, **kwargs):
            if 'failed@' in link: return {'status':'failed', 'detail':'failed'}
            return {'status':'ok', 'latency_ms':100 if 'slow@' in link else 10, 'detail':'ok'}
        with patch('router.server.probe', side_effect=fake_probe):
            self.request('/api/batch/probe', {'groups':['manual']}).close()
            for _ in range(100):
                status = json.load(self.request('/api/status'))
                if not status['jobs']['probe']['running']: break
                time.sleep(.01)
        self.assertEqual([node['title'] for node in status['nodes']], ['fast', 'slow', 'failed'])

    def test_batch_update_reports_failures(self):
        with patch('router.server.build_opener', return_value=Opener(b'trojan://a@example.test:443')):
            self.request('/api/sources', {'name':'x','url':'https://example.test/a'}).close()
        with patch('router.server.build_opener', side_effect=OSError('unavailable')):
            self.request('/api/batch/update', {}).close()
            for _ in range(100):
                job = json.load(self.request('/api/status'))['jobs']['update']
                if not job['running']: break
                time.sleep(.01)
        self.assertEqual((job['done'], job['total'], job['failed']), (1, 1, 1))
        self.assertEqual(len(base64.b64decode(self.store.subscription()).splitlines()), 1)

    def test_batch_probe_can_limit_subscription_groups_and_deduplicate(self):
        first = self.store.add('first', 'https://example.test/first')
        second = self.store.add('second', 'https://example.test/second')
        shared = 'trojan://shared@example.test:443'
        other = 'trojan://other@example.test:443'
        with self.store.connect() as db:
            db.execute('UPDATE sources SET links=? WHERE id=?', (json.dumps([shared]), first))
            db.execute('UPDATE sources SET links=? WHERE id=?', (json.dumps([shared, other]), second))
        self.store.add_manual('trojan://manual@example.test:443')
        status = json.load(self.request('/api/status'))
        self.assertEqual(set(status['nodes'][0]['groups']), {f'source:{first}', f'source:{second}'})
        with patch('router.server.probe', return_value={'status':'ok', 'latency_ms':10, 'detail':'ok'}) as mock:
            self.request('/api/batch/probe', {'groups':[f'source:{first}', 'manual'], 'target':'youtube'}).close()
            for _ in range(100):
                status = json.load(self.request('/api/status'))
                if not status['jobs']['probe']['running']: break
                time.sleep(.01)
        self.assertEqual((status['jobs']['probe']['done'], status['jobs']['probe']['total']), (2, 2))
        self.assertEqual(status['jobs']['probe']['target'], 'YouTube')
        self.assertEqual(mock.call_count, 2)
        self.assertIsNone(next(node for node in status['nodes'] if node['title'] == '节点 2')['probe'])
        with self.assertRaises(HTTPError):
            self.request('/api/batch/probe', {'groups':['source:999999']})
        with self.assertRaises(HTTPError):
            self.request('/api/batch/probe', {'groups':[]})

    def test_batch_probe_reports_active_positions_independently_of_completed_count(self):
        source = self.store.add('2', 'https://example.test/second')
        links = [f'trojan://fixture@example.test:443?node={n}#{n}' for n in range(1, 6)]
        with self.store.connect() as db:
            db.execute('UPDATE sources SET links=? WHERE id=?', (json.dumps(links), source))
        identifiers = {int(node['title']): node['id'] for node in self.store.nodes()}
        held = {link: threading.Event() for link in links}

        def probe_in_order(link, **_):
            held[link].wait(timeout=5)
            return {'status': 'ok', 'latency_ms': 10}

        def status_when(check):
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                with self.request('/api/status') as response:
                    status = json.load(response)
                if check(status['jobs']['probe']):
                    return status
                time.sleep(.01)
            self.fail('Probe progress did not reach the expected state')

        group = f'source:{source}'
        with patch('router.server.probe', side_effect=probe_in_order):
            self.request('/api/batch/probe', {'groups': [group]}).close()
            try:
                status = status_when(lambda job: len(job['active']) == 4)
                job = status['jobs']['probe']
                self.assertEqual(job['groups'], [group])
                self.assertEqual((job['done'], job['total']), (0, 5))
                self.assertEqual(job['active'], {identifiers[n]: n for n in range(1, 5)})
                held[links[1]].set()
                status = status_when(lambda job: identifiers[5] in job['active'])
                job = status['jobs']['probe']
                self.assertEqual(job['done'], 1)
                self.assertEqual(job['active'], {identifiers[n]: n for n in (1, 3, 4, 5)})
            finally:
                for event in held.values():
                    event.set()
            job = status_when(lambda job: not job['running'])['jobs']['probe']
            self.assertEqual((job['done'], job['active']), (5, {}))

    def test_project_update_prefs_default_on_and_can_be_saved(self):
        with patch('router.server.system_update.status', return_value={'state': 'current', 'changed_files': [], 'error': ''}):
            status = json.load(self.request('/api/system/update'))
        self.assertTrue(status['auto_check'])
        self.assertTrue(status['auto_apply'])
        saved = json.load(self.request('/api/system/update', {'auto_check': False, 'auto_apply': True}))
        self.assertEqual(saved['auto_check'], False)
        self.assertEqual(saved['auto_apply'], True)
        with patch('router.server.system_update.status', return_value={'state': 'current', 'changed_files': [], 'error': ''}):
            status = json.load(self.request('/api/system/update'))
        self.assertFalse(status['auto_check'])
        self.assertTrue(status['auto_apply'])
        with self.assertRaises(HTTPError):
            self.request('/api/system/update', {'auto_check': 'yes', 'auto_apply': True})


if __name__ == '__main__': unittest.main()
