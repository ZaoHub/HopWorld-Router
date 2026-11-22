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
PASSWORD = 'about-static-route-test-password'
AUTH = 'Basic ' + base64.b64encode(f'admin:{PASSWORD}'.encode()).decode()
ABOUT_PATH = '/v1-about.js'
JS_MARKER = b'hopworldAbout'


class AboutStaticTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'router.db')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, PASSWORD))
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

    def test_authenticated_about_script_matches_file_and_javascript_type(self):
        expected = (STATIC / 'v1-about.js').read_bytes()
        with self.request(ABOUT_PATH) as response:
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertTrue((response.headers['Content-Type'] or '').startswith('application/javascript'))
            self.assertEqual(body, expected)
            self.assertIn(JS_MARKER, body)
            self.assertFalse(body.lstrip().startswith(b'<'))

    def test_existing_v1_js_and_qrcode_still_served(self):
        for name in ('v1.js', 'qrcode.js'):
            expected = (STATIC / name).read_bytes()
            with self.subTest(name=name), self.request('/' + name) as response:
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertTrue((response.headers['Content-Type'] or '').startswith('application/javascript'))
                self.assertEqual(body, expected)

    def test_unauthenticated_auth_matches_v1_js(self):
        def anonymous(path):
            try:
                self.request(path, auth=False)
            except HTTPError as error:
                return error.code, error.headers.get('Content-Type', ''), error.read()
            self.fail(f'{path} should require authentication')

        about_status, about_type, about_body = anonymous(ABOUT_PATH)
        js_status, js_type, js_body = anonymous('/v1.js')
        self.assertEqual(about_status, js_status)
        self.assertEqual(js_status, 401)
        self.assertEqual(about_type, js_type)
        self.assertFalse(about_body.lstrip().startswith(b'<html'))
        self.assertNotIn(JS_MARKER, about_body)

    def test_page_registers_about_navigation_and_script(self):
        html = (STATIC / 'index.html').read_text(encoding='utf-8')
        self.assertIn('href="#about"', html)
        self.assertIn('data-v1-link="about"', html)
        self.assertIn('data-v1-view="about"', html)
        self.assertIn('src="v1-about.js"', html)
        self.assertIn('id="v1-about-check-update"', html)
        self.assertIn('id="v1-about-maintenance"', html)
        self.assertIn('HopWorld-Router', html)
        self.assertNotIn('跃界', html)
        self.assertNotIn('GeoNames', html)


class AboutPageWiringTests(unittest.TestCase):
    def test_router_registers_about_page_and_reuses_updates(self):
        js = (STATIC / 'v1.js').read_text(encoding='utf-8')
        app = (STATIC / 'app.js').read_text(encoding='utf-8')
        self.assertIn("about: ['关于', '产品信息、版本与支持开发']", js)
        self.assertIn("byId('v1-about-version')", js)
        self.assertIn("byId('v1-about-maintenance')", js)
        self.assertIn("showMaintenanceOnRoute = true", js)
        self.assertIn("$('#v1-about-check-update')", app)
        self.assertIn('requestSystemUpdateCheck', app)
        self.assertIn("$('#v1-about-update-status')", app)


if __name__ == '__main__':
    unittest.main()
