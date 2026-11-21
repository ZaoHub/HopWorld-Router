import base64
from io import BytesIO
import json
import socket
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from router.server import CORE_PROFILES_KEY, Store, _public_subscription_socket, node_id
from router.subscription import upstream_node_id


LINK = 'trojan://test@node.example:443?security=tls#Original'
REPLACEMENT = 'trojan://test@replacement.example:443?security=tls#Replacement'


class SourceSettingsTests(unittest.TestCase):
    def test_secure_subscription_connects_to_checked_address_and_blocks_nonpublic_answers(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('8.8.8.8', 443))
        destination = ('sub.example', 443)
        with patch('router.server.socket.getaddrinfo', return_value=[public]) as resolve, \
             patch('router.server.socket.socket') as socket_factory:
            peer = _public_subscription_socket(destination, 15)
            resolve.assert_called_once_with(*destination, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
            peer.connect.assert_called_once_with(public[-1])

        private_v4 = ('127.0.0.1', '100.64.0.1', '169.254.169.254', '192.168.0.1', '224.0.0.1')
        private_v6 = ('::1', 'fd00::1', '::ffff:127.0.0.1', '64:ff9b::a9fe:a9fe', '2002:7f00:1::')
        answers = [(socket.AF_INET, (host, 443)) for host in private_v4]
        answers.extend((socket.AF_INET6, (host, 443, 0, 0)) for host in private_v6)
        for family, endpoint in answers:
            rejected = (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', endpoint)
            with self.subTest(endpoint=endpoint), \
                 patch('router.server.socket.getaddrinfo', return_value=[public, rejected]), \
                 patch('router.server.socket.socket') as socket_factory:
                with self.assertRaisesRegex(ValueError, '公网 IP'):
                    _public_subscription_socket(destination, 15)
                socket_factory.assert_not_called()

    def test_conditional_refresh_keeps_cached_nodes_and_clears_validators_on_url_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'router.db')
            url = 'https://sub.example/path'
            source_id = store.add('Conditional', url, update_interval=300)
            opener = MagicMock()
            response = opener.open.return_value.__enter__.return_value
            response.read.return_value = LINK.encode()
            response.headers = {'ETag': 'W/"fixture"', 'Last-Modified': 'Thu, 01 Jan 1970 00:00:00 GMT'}
            with patch('router.server.build_opener', return_value=opener):
                with patch('router.server.time.time', return_value=1000):
                    self.assertEqual(store.refresh(source_id, automatic=True), 1)
                store.set_node_enabled(upstream_node_id(LINK), False)
                opener.open.side_effect = HTTPError(url, 304, 'Not Modified', {}, BytesIO())
                with patch('router.server.time.time', return_value=1300):
                    self.assertEqual(store.refresh(source_id, automatic=True), 1)
                    self.assertEqual(store.due_sources(), [])
                request = opener.open.call_args.args[0]
                self.assertEqual(request.get_header('If-none-match'), response.headers['ETag'])
                self.assertEqual(request.get_header('If-modified-since'), response.headers['Last-Modified'])
                self.assertEqual(store.subscription(), '')
                source = store.sources()[0]
                self.assertEqual((source['updated'], source['next_update'], json.loads(source['links'])), (1000, 1600, [LINK]))
                self.assertIsNone(source['error'])

                store.update_source(source_id, {'url': 'https://sub.example/replaced'})
                opener.open.side_effect = None
                response.headers = {}
                response.read.return_value = REPLACEMENT.encode()
                self.assertEqual(store.refresh(source_id), 2)
                request = opener.open.call_args.args[0]
                self.assertIsNone(request.get_header('If-none-match'))
                self.assertIsNone(request.get_header('If-modified-since'))
                self.assertEqual(base64.b64decode(store.subscription()).decode(), REPLACEMENT)

    def test_address_change_preserves_unique_nodes_through_failed_and_successful_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'router.db'
            store = Store(path)
            source_id = store.add('Original', 'https://sub.example/before')
            old_only = LINK.replace('node.example', 'old-only.example')
            renamed = LINK.replace('#Original', '#Renamed')
            opener = MagicMock()
            response = opener.open.return_value.__enter__.return_value
            response.read.return_value = f'{LINK}\n{old_only}'.encode()
            with patch('router.server.build_opener', return_value=opener):
                store.refresh(source_id)
                store.update_source(source_id, {'url': 'https://sub.example/after'})
                self.assertEqual(base64.b64decode(store.subscription()).decode().splitlines(), [LINK, old_only])

                opener.open.side_effect = TimeoutError
                with self.assertRaisesRegex(ValueError, '更新失败'):
                    store.refresh(source_id)
                self.assertEqual(json.loads(store.sources()[0]['links']), [LINK, old_only])

                opener.open.side_effect = None
                response.read.return_value = f'{renamed}\n{REPLACEMENT}\n{LINK}'.encode()
                self.assertEqual(store.refresh(source_id), 3)
                self.assertEqual(json.loads(store.sources()[0]['links']), [renamed, REPLACEMENT, old_only])

                store = Store(path)
                response.read.return_value = renamed.encode()
                self.assertEqual(store.refresh(source_id), 2)
                self.assertEqual(json.loads(store.sources()[0]['links']), [renamed, old_only])

    def test_migrates_existing_sources_and_disabled_source_stops_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'router.db'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("""CREATE TABLE sources (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
                    links TEXT NOT NULL DEFAULT '[]', updated INTEGER, error TEXT
                )""")
                db.execute('INSERT INTO sources VALUES (1, ?, ?, ?, 1000, NULL)',
                           ('Legacy', 'https://sub.example/test', json.dumps([LINK])))
            store = Store(path)
            with patch('router.server.time.time', return_value=4599):
                source = store.sources()[0]
                self.assertEqual((source['enabled'], source['auto_update'], source['update_interval']), (True, True, 3600))
                self.assertEqual(source['next_update'], 4600)
                self.assertEqual(store.due_sources(), [])
            self.assertEqual(base64.b64decode(store.subscription()).decode(), LINK)
            store.update_source(1, {'enabled': False})
            source = store.sources()[0]
            self.assertEqual(json.loads(source['links']), [LINK])
            self.assertIsNone(source['next_update'])
            self.assertEqual(store.subscription(), '')
            self.assertEqual(store.nodes(), [])
            self.assertEqual(store._auto_pool_entries({}, selected_id=upstream_node_id(LINK)), ([], []))
            store.update_source(1, {'enabled': True, 'auto_update': False})
            self.assertEqual(base64.b64decode(store.subscription()).decode(), LINK)
            self.assertEqual(store.due_sources(), [])

    def test_refresh_does_not_apply_results_from_an_edited_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'router.db')
            source_id = store.add('Original', 'https://sub.example/before')

            def edit_while_reading(limit):
                store.update_source(source_id, {'url': 'https://sub.example/after', 'name': 'Edited'})
                return LINK.encode()

            opener = MagicMock()
            response = opener.open.return_value.__enter__.return_value
            response.read.side_effect = edit_while_reading
            with patch('router.server.build_opener', return_value=opener), self.assertRaisesRegex(ValueError, '订阅设置已变更'):
                store.refresh(source_id)
            source = store.sources()[0]
            self.assertEqual(source['name'], 'Edited')
            self.assertEqual(source['url'], 'https://sub.example/after')
            self.assertEqual(source['links'], '[]')
            self.assertIsNone(source['error'])

            response.read.side_effect = None
            response.read.return_value = REPLACEMENT.encode()
            with patch('router.server.build_opener', return_value=opener):
                store.refresh(source_id)
            self.assertEqual(base64.b64decode(store.subscription()).decode(), REPLACEMENT)

    def test_legacy_node_overrides_and_fixed_egress_survive_a_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'router.db'
            store = Store(path)
            source = store.add('Legacy', 'https://sub.example/legacy')
            old_id = node_id(LINK)
            store.save_local_node_settings({**store.local_node_settings(), 'egress_mode': 'fixed', 'upstream_id': old_id})
            with store.connect() as db:
                db.execute("DELETE FROM settings WHERE key='upstream_identity_v1'")
                db.execute('UPDATE sources SET links=? WHERE id=?', (json.dumps([LINK]), source))
                db.execute('INSERT INTO disabled_nodes VALUES (?)', (old_id,))
                db.execute('INSERT INTO settings VALUES (?,?)',
                           (CORE_PROFILES_KEY, json.dumps({'xray-core': {'upstream_id': old_id}})))
                db.execute('INSERT INTO manual_nodes VALUES (?,?)', (node_id(REPLACEMENT), REPLACEMENT))

            store = Store(path)
            identifier = upstream_node_id(LINK)
            self.assertTrue(store.remove_manual(upstream_node_id(REPLACEMENT)))
            self.assertEqual(store.local_node_settings()['upstream_id'], identifier)
            self.assertEqual(store.independent_profiles()['xray-core']['upstream_id'], identifier)
            self.assertFalse(store.nodes()[0]['enabled'])
            store.set_upstream_name(identifier, '手选出口')
            renamed = LINK.replace('#Original', '#Renamed')
            opener = MagicMock()
            response = opener.open.return_value.__enter__.return_value
            response.read.return_value = f'{renamed}\n{LINK}'.encode()
            with patch('router.server.build_opener', return_value=opener):
                self.assertEqual(store.refresh(source), 1)
                store = Store(path)
                self.assertEqual([(node['id'], node['title'], node['enabled']) for node in store.nodes()],
                                 [(identifier, '手选出口', False)])
                self.assertEqual(store.subscription(), '')
                self.assertEqual(store._auto_pool_entries({}, selected_id=identifier), ([], []))
                store.set_node_enabled(identifier, True)
                store.set_upstream_name(identifier, '')
                self.assertEqual(store.nodes()[0]['title'], 'Renamed')
                self.assertEqual(base64.b64decode(store.subscription()).decode(), renamed)
                self.assertEqual(store._auto_pool_entries({}, selected_id=identifier)[1], [identifier])
                response.read.return_value = renamed.replace('test@', 'different-credential@').encode()
                store.refresh(source)
            self.assertNotEqual(store.nodes()[0]['id'], identifier)
            self.assertEqual(store._auto_pool_entries({}, selected_id=identifier), ([], []))

    def test_vmess_remarks_do_not_change_node_identity(self):
        fields = {'v': '2', 'ps': '出口甲', 'add': 'example.test', 'port': '443', 'id': 'fixture'}
        first = 'vmess://' + base64.b64encode(json.dumps(fields).encode()).decode()
        second = 'vmess://' + base64.urlsafe_b64encode(json.dumps({**fields, 'ps': '出口乙'}, sort_keys=True).encode()).decode().rstrip('=')
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'router.db')
            store.add_manual(first + '\n' + second)
            self.assertEqual(len(store.nodes()), 1)
            self.assertEqual(store.nodes()[0]['title'], '出口甲')
            self.assertEqual(upstream_node_id(first), upstream_node_id(second))

    def test_failed_automatic_attempt_keeps_previous_links_and_respects_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'router.db')
            source_id = store.add('Scheduled', 'https://sub.example/path', update_interval=300)
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = LINK.encode()
            with patch('router.server.time.time', return_value=1000), patch('router.server.build_opener', return_value=opener):
                store.refresh(source_id, automatic=True)
                self.assertEqual(store.due_sources(), [])
            with patch('router.server.time.time', return_value=1300), patch('router.server.build_opener', side_effect=OSError):
                self.assertEqual(store.due_sources(), [source_id])
                with self.assertRaises(ValueError):
                    store.refresh(source_id, automatic=True)
                self.assertEqual(store.due_sources(), [])
                source = store.sources()[0]
                self.assertEqual(source['updated'], 1000)
                self.assertEqual(source['next_update'], 1600)
                self.assertEqual(json.loads(source['links']), [LINK])
