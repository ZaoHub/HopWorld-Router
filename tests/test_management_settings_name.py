import base64
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from router.server import Store, UI_SETTINGS_KEY, make_handler

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'router' / 'static'
PASSWORD = 'settings-name-test-password'
AUTH = 'Basic ' + base64.b64encode(f'admin:{PASSWORD}'.encode()).decode()


def seed_ui(store, name, timezone='browser'):
    with store.connect() as db:
        db.execute(
            'INSERT OR REPLACE INTO settings VALUES (?, ?)',
            (UI_SETTINGS_KEY, json.dumps({'name': name, 'timezone': timezone}, ensure_ascii=False)),
        )


class ManagementSettingsNameStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'router.db')

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_name_change_and_keeps_existing_value(self):
        seed_ui(self.store, '现有名称', 'browser')
        with self.assertRaises(ValueError) as error:
            self.store.save_management_settings({'name': '新名称', 'timezone': 'UTC'})
        self.assertEqual(str(error.exception), '平台名称不可修改')
        self.assertEqual(self.store.management_ui_settings(), {'name': '现有名称', 'timezone': 'browser'})

    def test_omitted_or_same_name_saves_other_fields_without_dropping_prefs(self):
        seed_ui(self.store, '现有名称', 'browser')
        first = self.store.save_management_settings({
            'timezone': 'Asia/Shanghai', 'auto_check': False, 'auto_apply': False, 'probe_target': 'cloudflare',
        })
        self.assertEqual(first['name'], '现有名称')
        self.assertEqual(first['timezone'], 'Asia/Shanghai')
        self.assertEqual(first['auto_check'], False)
        self.assertEqual(first['auto_apply'], False)
        self.assertEqual(first['probe_target'], 'cloudflare')
        same = self.store.save_management_settings({'name': '现有名称', 'timezone': 'UTC'})
        self.assertEqual(same, {'name': '现有名称', 'timezone': 'UTC'})
        self.assertEqual(self.store.update_prefs(), {'auto_check': False, 'auto_apply': False})
        self.assertEqual(self.store.probe_target(), 'cloudflare')
        self.assertEqual(self.store.management_ui_settings()['name'], '现有名称')

    def test_partial_pref_update_does_not_drop_the_other_pref(self):
        self.store.save_management_settings({'auto_check': False, 'auto_apply': False})
        saved = self.store.save_management_settings({'auto_check': True})
        self.assertEqual(saved['auto_check'], True)
        self.assertEqual(self.store.update_prefs(), {'auto_check': True, 'auto_apply': False})
        self.assertEqual(self.store.management_ui_settings()['name'], 'HopWorld-Router')


class ManagementSettingsNameHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'router.db')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, PASSWORD))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()
        self.root = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, auth=True, origin=True):
        headers = {}
        if auth:
            headers['Authorization'] = AUTH
        if data is not None:
            headers['Content-Type'] = 'application/json'
            if origin:
                headers['Origin'] = self.root
        request = Request(
            self.root + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
        )
        return urlopen(request, timeout=4)

    def error_body(self, error):
        return json.loads(error.read().decode())

    def test_permissions_and_name_write_protection(self):
        path = '/api/v1/settings'
        seed_ui(self.store, '现有名称', 'browser')
        original = json.load(self.request(path))
        self.assertEqual(original['name'], '现有名称')
        payload = {'timezone': 'Asia/Shanghai', 'auto_check': False, 'auto_apply': False, 'probe_target': 'cloudflare'}
        with self.assertRaises(HTTPError) as anonymous:
            self.request(path, payload, auth=False)
        self.assertEqual(anonymous.exception.code, 401)
        with self.assertRaises(HTTPError) as no_origin:
            self.request(path, payload, origin=False)
        self.assertEqual(no_origin.exception.code, 403)
        with self.assertRaises(HTTPError) as renamed:
            self.request(path, {**payload, 'name': 'Home Router'})
        self.assertEqual(renamed.exception.code, 400)
        self.assertEqual(self.error_body(renamed.exception)['error'], '平台名称不可修改')
        self.assertEqual(json.load(self.request(path))['name'], '现有名称')
        self.assertEqual(json.load(self.request(path))['timezone'], 'browser')
        saved = json.load(self.request(path, payload))
        self.assertEqual(saved['name'], '现有名称')
        self.assertEqual(saved['timezone'], 'Asia/Shanghai')
        self.assertEqual(saved['auto_check'], False)
        self.assertEqual(saved['auto_apply'], False)
        self.assertEqual(saved['probe_target'], 'cloudflare')
        again = json.load(self.request(path, {'timezone': 'UTC'}))
        self.assertEqual(again['name'], '现有名称')
        self.assertEqual(again['timezone'], 'UTC')
        current = json.load(self.request(path))
        self.assertEqual(current['name'], '现有名称')
        self.assertEqual(current['auto_check'], False)
        self.assertEqual(current['auto_apply'], False)
        self.assertEqual(current['probe_target'], 'cloudflare')


class ManagementSettingsNameStaticTests(unittest.TestCase):
    def test_basic_card_is_read_only_name_and_uses_page_save(self):
        html = (STATIC / 'index.html').read_text(encoding='utf-8')
        js = (STATIC / 'v1.js').read_text(encoding='utf-8')
        self.assertIn('<span>平台名称</span><output id="v1-settings-name">', html)
        self.assertNotIn('id="v1-settings-basic-save"', html)
        self.assertNotIn('保存基本设置', html)
        self.assertNotRegex(html, r'<input[^>]*id="v1-settings-name"')
        self.assertIn("id=\"v1-settings-save\"", html)
        self.assertIn('保存设置', html)
        self.assertIn("return {timezone: fields.timezone.value, auto_check: fields.auto_check.checked,", js)
        self.assertNotIn("namedItem('name')", js)
        self.assertNotIn('basicSettingsSave', js)
        self.assertNotIn('请填写平台名称', js)
        self.assertIn('const saved = await persistManagementSettings(draft);', js)
        self.assertIn("byId('v1-settings-name').textContent = value.name || '—';", js)
        self.assertIn("id=\"v1-management-apply\"", html)
        self.assertIn('验证新管理入口', html)


if __name__ == '__main__':
    unittest.main()
