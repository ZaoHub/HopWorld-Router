from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import base64
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import unittest

from router import autostart
from router.acme import cert_paths
from router.admin_auth import matches_record, password_record
from router.management_access import ManagementAccess
from router.server import Store, make_handler


PASSWORD = 'a-local-test-password'


@contextmanager
def router(password=PASSWORD):
    with TemporaryDirectory() as temporary:
        store = Store(Path(temporary) / 'router.db')
        with ThreadingHTTPServer(('127.0.0.1', 0), make_handler(store, password)) as server:
            access = ManagementAccess(store, server)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
            thread.start()
            try:
                yield store, access, f'http://127.0.0.1:{server.server_port}'
            finally:
                access.close()
                server.shutdown()
                thread.join()


def available_port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


def request(root, path, data=None, *, auth=PASSWORD, headers=None, context=None):
    headers = dict(headers or {})
    if auth:
        headers['Authorization'] = 'Basic ' + base64.b64encode(f'admin:{auth}'.encode()).decode()
    if data is not None:
        headers.setdefault('Origin', root)
        headers['Content-Type'] = 'application/json'
    req = Request(root + path, json.dumps(data).encode() if data is not None else None, headers)
    return urlopen(req, timeout=4, context=context)


class ManagementAccessTests(unittest.TestCase):
    path = '/api/v1/management/access'

    def test_six_character_passwords_do_not_require_mixed_character_types(self):
        for password in ('123456', 'abcdef', '::::::', '汉字密码示例'):
            with self.subTest(password=password):
                self.assertTrue(matches_record(password_record(password), password))

    def test_manage_account_checks_current_password_and_invalidates_existing_sessions(self):
        with router() as (store, _access, root):
            store.initialize_admin_password('stale-initial-password')
            data = {'username': 'new-admin', 'current_password': PASSWORD, 'new_password': '123456'}
            path = '/api/v1/management/account'
            with request(root, '/login', {'username': 'admin', 'password': PASSWORD}, auth=None) as first:
                first_cookie = first.headers['Set-Cookie'].split(';', 1)[0]
            with request(root, '/login', {'username': 'admin', 'password': PASSWORD}, auth=None) as second:
                second_cookie = second.headers['Set-Cookie'].split(';', 1)[0]
            with self.assertRaises(HTTPError) as anonymous:
                request(root, path, data, auth=None)
            self.assertEqual(anonymous.exception.code, 401)
            with self.assertRaises(HTTPError) as wrong_password:
                request(root, path, {**data, 'current_password': 'incorrect'}, auth=None, headers={'Cookie': first_cookie})
            self.assertEqual(wrong_password.exception.code, 400)
            with request(root, path, data, auth=None, headers={'Cookie': first_cookie}) as changed:
                self.assertEqual(changed.status, 200)
                self.assertIn('Max-Age=0', changed.headers['Set-Cookie'])
            for cookie in (first_cookie, second_cookie):
                with self.assertRaises(HTTPError) as stale:
                    request(root, '/api/v1/settings', auth=None, headers={'Cookie': cookie})
                self.assertEqual(stale.exception.code, 401)
            with self.assertRaises(HTTPError) as old_basic:
                request(root, '/api/v1/settings')
            self.assertEqual(old_basic.exception.code, 401)
            login = {'username': 'new-admin', 'password': '123456'}
            with request(root, '/login', login, auth=None) as response:
                cookie = response.headers['Set-Cookie'].split(';', 1)[0]
            with request(root, '/api/v1/settings', auth=None, headers={'Cookie': cookie}) as settings:
                self.assertEqual(json.load(settings)['admin_username'], 'new-admin')
            with request(root, path, {'username': 'renamed', 'current_password': login['password']},
                         auth=None, headers={'Cookie': cookie}) as changed:
                self.assertEqual(changed.status, 200)
            with request(root, '/login', {**login, 'username': 'renamed'}, auth=None) as response:
                self.assertEqual(response.status, 200)

    def test_port_switch_preserves_subscription_and_persists_after_confirmation(self):
        with router() as (store, access, old):
            port = available_port()
            new = f'http://127.0.0.1:{port}'
            started = json.load(request(old, self.path, {'action': 'stage', 'port': port, 'https': False}))
            self.assertEqual(started['pending']['port'], port)
            self.assertIsNone(store.management_access_settings())
            with self.assertRaises(HTTPError) as wrong_port:
                request(old, self.path, {'action': 'confirm'})
            self.assertEqual(wrong_port.exception.code, 400)
            self.assertTrue(json.load(request(new, self.path))['can_confirm'])
            with request(new, self.path, {'action': 'confirm'}) as response:
                self.assertEqual(json.load(response)['active']['port'], port)
            self.assertEqual(store.management_access_settings(), {'port': port, 'https': False})
            with self.assertRaises(HTTPError) as inactive:
                request(old, '/')
            self.assertEqual(inactive.exception.code, 404)
            with request(old, '/s/' + store.token(), auth=None) as subscription:
                self.assertEqual(subscription.status, 200)
            access.close()
            access.restore()
            self.assertEqual(json.load(request(new, self.path))['active']['port'], port)

    def test_invalid_busy_and_unconfirmed_ports_leave_original_available(self):
        with router() as (store, _access, old), socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            for port in (-1, occupied.getsockname()[1]):
                with self.assertRaises(HTTPError) as invalid:
                    request(old, self.path, {'action': 'stage', 'port': port, 'https': False})
                self.assertEqual(invalid.exception.code, 400)
            with patch('router.management_access.CONFIRM_SECONDS', .1):
                request(old, self.path, {'action': 'stage', 'port': available_port(), 'https': False}).close()
                deadline = time.monotonic() + 2
                while json.load(request(old, self.path))['pending'] and time.monotonic() < deadline:
                    time.sleep(.02)
            self.assertIsNone(json.load(request(old, self.path))['pending'])
            self.assertIsNone(store.management_access_settings())
            self.assertEqual(request(old, '/').status, 200)

    @unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for local TLS fixture')
    def test_https_uses_existing_certificate_and_requires_https_confirmation(self):
        with router() as (store, _access, old):
            port = available_port()
            with self.assertRaises(HTTPError) as no_cert:
                request(old, self.path, {'action': 'stage', 'port': port, 'https': True})
            self.assertEqual(no_cert.exception.code, 400)
            domain = 'localhost'
            cert, key = cert_paths(store.data_dir, domain)
            cert.parent.mkdir(parents=True)
            config = cert.parent / 'openssl.cnf'
            config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n'
                              '[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost\n')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                            '-keyout', str(key), '-out', str(cert), '-config', str(config)],
                           check=True, capture_output=True, timeout=15)
            store.save_acme_settings({**store.acme_settings(), 'domain': domain})
            store.save_local_node_settings({**store.local_node_settings(), 'public_host': domain})
            request(old, self.path, {'action': 'stage', 'port': port, 'https': True}).close()
            new = f'https://localhost:{port}'
            context = ssl.create_default_context(cafile=str(cert))
            with request(new, self.path, {'action': 'confirm'}, context=context) as response:
                self.assertTrue(json.load(response)['active']['https'])
            self.assertTrue(store.management_access_settings()['https'])

    def test_default_login_initializes_password_and_supports_logout(self):
        with router('admin') as (store, _access, root):
            with request(root, '/', auth=None, headers={'Accept': 'text/html'}) as response:
                self.assertIn(b'data-initial-setup="true"', response.read())
            public_headers = {'Host': 'router.example.com', 'Origin': 'https://router.example.com'}
            with request(root, '/login', auth=None, headers=public_headers) as response:
                self.assertIn(b'data-initial-setup="false"', response.read())
            with self.assertRaises(HTTPError) as unauthorized:
                request(root, '/api/v1/settings', auth='admin')
            self.assertEqual(unauthorized.exception.code, 401)
            default = {'username': 'admin', 'password': 'admin'}
            with request(root, '/login', default, auth=None) as response:
                self.assertTrue(json.load(response)['setup_required'])
                self.assertIsNone(response.headers.get('Set-Cookie'))
            with self.assertRaises(HTTPError) as cross_origin:
                request(root, '/login', {**default, 'new_password': PASSWORD}, auth=None,
                        headers={'Origin': 'http://invalid.local'})
            self.assertEqual(cross_origin.exception.code, 403)
            new_password = '密:😊 码!'
            with self.assertRaises(HTTPError) as public_setup:
                request(root, '/login', {**default, 'new_password': new_password}, auth=None, headers=public_headers)
            self.assertEqual(public_setup.exception.code, 403)
            with self.assertRaises(HTTPError) as too_short:
                request(root, '/login', {**default, 'new_password': new_password[:-1]}, auth=None)
            self.assertEqual(too_short.exception.code, 400)
            self.assertFalse(store.admin_password_record())
            with request(root, '/login', {**default, 'new_password': new_password}, auth=None) as response:
                cookie = SimpleCookie(response.headers['Set-Cookie'])
            self.assertTrue(store.admin_password_record())
            self.assertNotIn(new_password, store.admin_password_record())
            with request(root, '/login', auth=None) as response:
                self.assertIn(b'data-initial-setup="false"', response.read())
            with request(root, '/api/v1/settings', auth=new_password) as response:
                self.assertEqual(response.status, 200)
            self.assertTrue(cookie['hopworld_session']['httponly'])
            self.assertEqual(cookie['hopworld_session']['samesite'], 'Strict')
            headers = {'Cookie': 'hopworld_session=' + cookie['hopworld_session'].value}
            with request(root, '/api/v1/settings', auth=None, headers=headers) as response:
                self.assertEqual(response.status, 200)
            request(root, '/logout', {}, auth=None, headers=headers).close()
            with self.assertRaises(HTTPError) as logged_out:
                request(root, '/api/v1/settings', auth=None, headers=headers)
            self.assertEqual(logged_out.exception.code, 401)
            with self.assertRaises(HTTPError) as old_password:
                request(root, '/login', default, auth=None)
            self.assertEqual(old_password.exception.code, 401)

    @unittest.skipUnless(os.name == 'posix', 'Router restarts using POSIX exec')
    def test_restart_requires_admin_and_restores_confirmed_management_port(self):
        with TemporaryDirectory() as temporary:
            port = available_port()
            root = f'http://127.0.0.1:{port}'
            process = subprocess.Popen(
                [sys.executable, '-m', 'router.server'],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'HOPWORLD_DATA': temporary, 'HOPWORLD_PORT': str(port),
                     'HOPWORLD_HOST': '127.0.0.1', 'HOPWORLD_ADMIN_PASSWORD': PASSWORD,
                     'HOPWORLD_MANAGEMENT_RECOVERY': '0'},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

            def ready(address, previous=None):
                deadline = time.monotonic() + 10
                while process.poll() is None and time.monotonic() < deadline:
                    try:
                        with request(address, '/health/ready', auth=None) as response:
                            instance = response.headers['X-HopWorld-Instance']
                            if instance and instance != previous:
                                return instance
                    except OSError:
                        pass
                    time.sleep(.05)
                self.fail(f'Router did not become ready (exit code {process.poll()})')

            try:
                before = ready(root)
                restart = '/api/v1/management/restart'
                for options, expected in (({'auth': None}, 401), ({'headers': {'Origin': 'http://invalid.local'}}, 403)):
                    with self.subTest(expected=expected), self.assertRaises(HTTPError) as error:
                        request(root, restart, {}, **options)
                    self.assertEqual(error.exception.code, expected)
                request(root, '/api/v1/settings', {'name': 'Restart check', 'timezone': 'UTC'}).close()

                managed_port = available_port()
                managed = f'http://127.0.0.1:{managed_port}'
                request(root, self.path, {'action': 'stage', 'port': managed_port, 'https': False}).close()
                with self.assertRaises(HTTPError) as pending:
                    request(managed, restart, {})
                self.assertEqual(pending.exception.code, 409)
                request(managed, self.path, {'action': 'confirm'}).close()
                self.assertTrue(json.load(request(managed, '/api/v1/settings'))['restart_available'])
                with request(managed, '/login', {'username': 'admin', 'password': PASSWORD}, auth=None) as response:
                    session = SimpleCookie(response.headers['Set-Cookie'])['hopworld_session'].value
                headers = {'Cookie': 'hopworld_session=' + session}
                with request(managed, restart, {}, auth=None, headers=headers) as response:
                    self.assertEqual(response.status, 202)
                    self.assertEqual(json.load(response)['instance'], before)

                after = ready(managed, before)
                self.assertEqual(ready(root), after)
                self.assertIsNone(process.poll())
                self.assertEqual(json.load(request(managed, '/api/v1/settings'))['name'], 'Restart check')
                with self.assertRaises(HTTPError) as expired:
                    request(managed, '/api/v1/settings', auth=None, headers=headers)
                self.assertEqual(expired.exception.code, 401)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


class AutostartTests(unittest.TestCase):
    def test_system_service_toggles_only_its_exact_unit(self):
        prefix = ['/usr/bin/systemctl']
        target = autostart.UNIT
        result = lambda status=0, text='': subprocess.CompletedProcess([], status, text)
        responses = [result(1, 'disabled'), result(), result(), result(),
                     result(0, 'enabled'), result(), result()]
        with patch('router.autostart._service', return_value=('system', prefix, target)), \
             patch('router.autostart.os.geteuid', return_value=1000), \
             patch('router.autostart._run', side_effect=responses) as run:
            self.assertTrue(autostart.set_enabled(True)['enabled'])
        self.assertEqual(run.call_args_list[3].args[0],
                         ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'enable', target])
