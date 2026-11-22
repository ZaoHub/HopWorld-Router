import base64
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from router.public_subscription import PublicSubscription
from router.server import Store, make_handler
from router.subscription import decode_subscription


class SubscriptionTokenRotateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'db'
        self.store = Store(self.db)
        self.publication = PublicSubscription(self.store)
        self.server = ThreadingHTTPServer(
            ('127.0.0.1', 0),
            make_handler(self.store, 'a-long-safe-password', self.publication),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root = f'http://127.0.0.1:{self.server.server_port}'
        self.auth = 'Basic ' + base64.b64encode(b'admin:a-long-safe-password').decode()

    def tearDown(self):
        self.publication.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, auth=True, origin=True):
        headers = {}
        if auth:
            headers['Authorization'] = self.auth
        if data is not None:
            headers['Content-Type'] = 'application/json'
            if origin:
                headers['Origin'] = self.root
        req = Request(
            self.root + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
        )
        return urlopen(req, timeout=4)

    def settings_map(self, store=None):
        store = store or self.store
        with store.connect() as db:
            return dict(db.execute('SELECT key, value FROM settings'))

    def download(self, token, sub=''):
        path = f'/s/{token}' + (f'/{sub}' if sub else '')
        return self.request(path, auth=False)

    def test_rotate_invalidates_old_subscription_paths_and_persists(self):
        link = 'vless://example-secret@node.example:443#Example'
        self.store.add_manual(link)

        status = json.load(self.request('/api/status'))
        old = status['token']
        self.assertTrue(old)
        with self.download(old) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(decode_subscription(response.read()), [link])

        with self.assertRaises(HTTPError) as ike_old_ok:
            # same token also gates /s/<token>/ikev2.mobileconfig; VPN may be off (not 404)
            self.download(old, 'ikev2.mobileconfig')
        self.assertNotEqual(ike_old_ok.exception.code, 404)

        before = self.settings_map()
        body = json.load(self.request('/api/rotate', {}))
        self.assertEqual(body, {'ok': True})
        self.assertNotIn('token', body)
        self.assertNotIn(old, json.dumps(body))

        after = self.settings_map()
        self.assertEqual(set(after), set(before))
        changed = {key for key in before if before[key] != after[key]}
        self.assertEqual(changed, {'token'})

        with self.assertRaises(HTTPError) as expired:
            self.download(old)
        self.assertEqual(expired.exception.code, 404)
        with self.assertRaises(HTTPError) as expired_profile:
            self.download(old, 'ikev2.mobileconfig')
        self.assertEqual(expired_profile.exception.code, 404)

        refreshed = json.load(self.request('/api/status'))
        new = refreshed['token']
        self.assertNotEqual(new, old)
        self.assertGreaterEqual(len(new), 32)
        with self.download(new) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(decode_subscription(response.read()), [link])
        with self.assertRaises(HTTPError) as ike_new:
            self.download(new, 'ikev2.mobileconfig')
        self.assertNotEqual(ike_new.exception.code, 404)

        # reopen the database like a process restart
        restarted = Store(self.db)
        self.assertEqual(restarted.token(), new)
        self.assertNotEqual(restarted.token(), old)

    def test_rotate_keeps_proxy_base_and_only_changes_token_path(self):
        link = 'trojan://example-secret@node.example:443#Example'
        self.store.add_manual(link)
        old = self.store.token()
        with patch.dict(os.environ, {'HOPWORLD_SUBSCRIPTION_BASE': 'https://edge.example.com'}):
            status = json.load(self.request('/api/status'))
            advertised = status['public_urls']['public_subscription_url']
            self.assertEqual(advertised, f'https://edge.example.com/s/{old}')
            json.load(self.request('/api/rotate', {}))
            rotated = json.load(self.request('/api/status'))
        new = rotated['token']
        self.assertNotEqual(new, old)
        self.assertEqual(
            rotated['public_urls']['public_subscription_url'],
            f'https://edge.example.com/s/{new}',
        )
        self.assertNotIn(old, rotated['public_urls']['public_subscription_url'])
        with self.assertRaises(HTTPError) as expired:
            self.download(old)
        self.assertEqual(expired.exception.code, 404)
        with self.download(new) as response:
            self.assertEqual(decode_subscription(response.read()), [link])

    def test_rotate_rejects_anonymous_and_cross_origin(self):
        old = self.store.token()
        with self.assertRaises(HTTPError) as anonymous:
            self.request('/api/rotate', {}, auth=False)
        self.assertEqual(anonymous.exception.code, 401)
        with self.assertRaises(HTTPError) as csrf:
            self.request('/api/rotate', {}, origin=False)
        self.assertEqual(csrf.exception.code, 403)
        self.assertEqual(self.store.token(), old)
        with self.download(old) as response:
            self.assertEqual(response.status, 200)


if __name__ == '__main__':
    unittest.main()
