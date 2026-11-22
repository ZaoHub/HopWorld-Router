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
ICON_PATH = '/v1-icons.js'
JS_MARKER = b'hopworldIcons'
LOGIN_MARKERS = (b'login-form', b'data-initial-setup', b'<html')
CDN_MARKERS = (b'cdn.jsdelivr', b'unpkg.com', b'cdnjs.', b'cdn.lucide', b'https://lucide')


class UiIconsStaticTests(unittest.TestCase):
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

    def test_authenticated_icons_script_matches_file_and_javascript_type(self):
        expected = (STATIC / 'v1-icons.js').read_bytes()
        with self.request(ICON_PATH) as response:
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.headers.get_content_type() == 'application/javascript'
                            or (response.headers['Content-Type'] or '').startswith('application/javascript'))
            self.assertEqual(body, expected)
            self.assertIn(JS_MARKER, body)
            self.assertIn(b'viewBox="0 0 24 24"', body)
            self.assertNotIn(b'data-initial-setup', body)
            self.assertFalse(body.lstrip().startswith(b'<'))
            for marker in CDN_MARKERS:
                self.assertNotIn(marker, body)

    def test_anonymous_auth_matches_v1_js(self):
        def anonymous(path):
            try:
                with self.request(path, auth=False) as response:
                    return response.status, response.headers.get_content_type(), response.read()
            except HTTPError as error:
                return error.code, error.headers.get_content_type(), error.read()

        icon_status, icon_type, icon_body = anonymous(ICON_PATH)
        js_status, js_type, js_body = anonymous('/v1.js')
        self.assertEqual(icon_status, js_status)
        self.assertEqual(js_status, 401)
        self.assertEqual(icon_type, js_type)
        self.assertFalse(icon_body.lstrip().startswith(b'<html'))

    def test_unknown_icon_paths_are_not_served(self):
        for path in ('/v1-icons.js.bak', '/static/v1-icons.js', '/v1-icons.js/../v1.js'):
            with self.subTest(path=path):
                try:
                    with self.request(path) as response:
                        self.fail(f'{path} should not be served as an allowlisted script, got {response.status}')
                except HTTPError as error:
                    self.assertEqual(error.code, 404)

    def test_index_script_order_and_fail_closed_helpers(self):
        html = (STATIC / 'index.html').read_text(encoding='utf-8')
        icons_src = html.find('src="v1-icons.js"')
        app_src = html.find('src="app.js"')
        secret_src = html.find('src="v1-overview-secret.js"')
        v1_src = html.find('src="v1.js"')
        self.assertNotEqual(icons_src, -1)
        self.assertLess(html.find('src="qrcode.js"'), icons_src)
        self.assertLess(icons_src, app_src)
        self.assertLess(icons_src, secret_src)
        self.assertLess(secret_src, v1_src)
        js = (STATIC / 'v1.js').read_text(encoding='utf-8')
        self.assertIn('function iconSvg(name, size)', js)
        self.assertIn('function setIcon(node, name, size)', js)
        self.assertIn('else node.replaceChildren();', js)
        self.assertIn("iconSvg(paused ? 'play' : 'pause', 16)", js)
        self.assertNotIn("pause.textContent = paused ? '▶' : '❚❚'", js)

    def test_static_assets_have_no_runtime_cdn(self):
        for name in ('index.html', 'v1.js', 'v1.css', 'v1-icons.js', 'v1-overview-secret.js', 'app.js'):
            data = (STATIC / name).read_bytes()
            for marker in CDN_MARKERS:
                self.assertNotIn(marker, data, name)


if __name__ == '__main__':
    unittest.main()
