import base64
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from router.server import Store, make_handler

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'router' / 'static'
PASSWORD = 'static-route-test-password'
AUTH = 'Basic ' + base64.b64encode(f'admin:{PASSWORD}'.encode()).decode()
SECRET_PATH = '/v1-overview-secret.js'
JS_MARKER = b'createOverviewSecrets'
LOGIN_MARKERS = (b'login-form', b'data-initial-setup', b'<html', b'text/html')


class OverviewSecretStaticTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'router.db')
        self.server = ThreadingHTTPServer(
            ('127.0.0.1', 0), make_handler(self.store, PASSWORD)
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True
        )
        self.thread.start()
        self.root = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, *, auth=True, headers=None):
        request_headers = dict(headers or {})
        if auth:
            request_headers['Authorization'] = AUTH
        return urlopen(Request(self.root + path, headers=request_headers), timeout=4)

    def test_authenticated_secret_script_matches_file_and_javascript_type(self):
        expected = (STATIC / 'v1-overview-secret.js').read_bytes()
        with self.request(SECRET_PATH) as response:
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.headers.get_content_type() == 'application/javascript'
                            or (response.headers['Content-Type'] or '').startswith('application/javascript'))
            self.assertEqual(body, expected)
            self.assertIn(JS_MARKER, body)
            self.assertNotIn(b'data-initial-setup', body)
            self.assertNotIn(b'name="password"', body)
            self.assertFalse(body.lstrip().startswith(b'<'))

    def test_existing_v1_js_still_served(self):
        expected = (STATIC / 'v1.js').read_bytes()
        with self.request('/v1.js') as response:
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertTrue((response.headers['Content-Type'] or '').startswith('application/javascript'))
            self.assertEqual(body, expected)
            self.assertIn(b"function metrics(", body)

    def test_unauthenticated_auth_matches_v1_js(self):
        def anonymous(path):
            try:
                self.request(path, auth=False)
            except HTTPError as error:
                return error.code, error.headers.get('Content-Type', ''), error.read()
            self.fail(f'{path} should require authentication')

        secret_status, secret_type, secret_body = anonymous(SECRET_PATH)
        js_status, js_type, js_body = anonymous('/v1.js')
        self.assertEqual(secret_status, js_status)
        self.assertEqual(js_status, 401)
        self.assertEqual(secret_type, js_type)
        self.assertNotIn(JS_MARKER, secret_body)
        self.assertNotIn(b'createOverviewSecrets', secret_body)
        self.assertNotIn(b'data-initial-setup', secret_body)

    def test_unknown_or_traversal_paths_are_not_exposed(self):
        for path in (
            '/v1-overview-secret.js.bak',
            '/static/v1-overview-secret.js',
            '/v1-overview-secret.js/../v1.js',
            '/login.html',
        ):
            with self.subTest(path=path):
                try:
                    self.request(path)
                except HTTPError as error:
                    self.assertEqual(error.code, 404)
                    self.assertNotIn(JS_MARKER, error.read())
                else:
                    self.fail(f'{path} should not be served as an allowlisted script')

    def test_index_script_order_and_v1_fail_closed(self):
        html = (STATIC / 'index.html').read_text(encoding='utf-8')
        secret_src = html.find('src="v1-overview-secret.js"')
        v1_src = html.find('src="v1.js"')
        self.assertNotEqual(secret_src, -1)
        self.assertNotEqual(v1_src, -1)
        self.assertLess(secret_src, v1_src)
        js = (STATIC / 'v1.js').read_text(encoding='utf-8')
        self.assertIn('overviewSecretApi?.createOverviewSecrets', js)
        self.assertIn('function fallbackOverviewSecrets()', js)
        self.assertNotIn('window.hopworldOverviewSecret.createOverviewSecrets', js)
        self.assertIn("display: maskable ? mask : (text || '—')", js)
        self.assertIn('toggle(id) { return snapshot(id); }', js)


if __name__ == '__main__':
    unittest.main()
