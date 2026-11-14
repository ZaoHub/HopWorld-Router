import base64
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from router.server import Store, make_handler


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
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, 'a-long-safe-password'))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root = f'http://127.0.0.1:{self.server.server_port}'
        self.auth = 'Basic ' + base64.b64encode(b'admin:a-long-safe-password').decode()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, path, data=None, auth=True, origin=True):
        headers = {}
        if auth: headers['Authorization'] = self.auth
        if data is not None:
            headers['Content-Type'] = 'application/json'
            if origin: headers['Origin'] = self.root
        req = Request(self.root + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        return urlopen(req, timeout=2)

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


if __name__ == '__main__': unittest.main()
