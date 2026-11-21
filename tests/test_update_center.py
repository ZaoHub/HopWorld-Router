from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from router import system_update
from router.release_channel import deb_package, stable_release
from router.server import Store


class ReleaseChannelTests(unittest.TestCase):
    def test_stable_release_selects_highest_version_in_the_current_major(self):
        releases = [{'tag_name': 'v2.0.0'}, {'tag_name': 'v1.0.2-beta.1', 'prerelease': True},
                    {'tag_name': 'v1.0.1'}, {'tag_name': 'v1.0.12'}, {'tag_name': 'v1.2.0', 'draft': True},
                    {'tag_name': 'v1.0.2'}]
        picked = stable_release(lambda _: releases)
        self.assertEqual(picked['tag_name'], 'v1.0.12')

    def test_debian_asset_requires_the_release_url_and_digest(self):
        name = 'hopworld-router_1.0.2_all.deb'
        asset = {'name': name, 'digest': 'sha256:' + 'e' * 64,
                 'browser_download_url': f'https://github.com/ZaoHub/HopWorld-Router/releases/download/v1.0.2/{name}'}
        release = {'tag_name': 'v1.0.2', 'assets': [asset]}
        self.assertEqual(deb_package(release)[0:2], ('1.0.2', name))
        asset['digest'] = ''
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            deb_package(release)

    def test_status_without_refresh_does_not_check_github(self):
        with patch.object(system_update, '_unmanaged_status', return_value=None), \
             patch.object(system_update, '_unit_running', return_value=False), \
             patch.object(system_update, '_starting_until', 0), \
             patch.object(system_update, '_checking', False), \
             patch.object(system_update.threading, 'Thread') as worker:
            system_update.status()
            worker.assert_not_called()


class UpdateCenterTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'router.db'
        self.store = Store(self.path)

    def test_v1_preferences_and_read_cursor_survive_reopen(self):
        self.store.save_update_prefs(False, False)
        self.store.updates.save({'target': 'mihomo', 'automatic': True})
        self.store.updates.record('mihomo', 'available', version='v1.2.3', key='known')
        self.store.updates.record('mihomo', 'available', version='v1.2.3', key='known')
        history = self.store.updates.history()
        self.assertEqual(history['unread'], 1)
        self.store.updates.record('package', 'completed', version='1.0.2')
        self.store.updates.mark_read(history['events'][0]['id'])
        reopened = Store(self.path)
        self.assertTrue(reopened.updates.preferences()['mihomo'])
        self.assertEqual(reopened.update_prefs(), {'auto_check': False, 'auto_apply': False})
        self.assertEqual(reopened.updates.history()['unread'], 1)

    def test_auto_updates_existing_router_installed_core_without_restarting(self):
        path = self.path.parent / 'bin' / 'mihomo'
        path.parent.mkdir()
        path.write_bytes(b'old')
        row = {'id': 'mihomo', 'installed': True, 'path': str(path), 'status': 'running', 'version': 'v1.2.2'}
        self.store.updates.save({'target': 'mihomo', 'automatic': True})
        with patch('router.update_center.core_inventory', return_value=[row]), \
             patch('router.update_center.check_update', return_value={'current': 'v1.2.2', 'latest': 'v1.2.3', 'update': True}), \
             patch('router.update_center.install_core', return_value={'id': 'mihomo', 'version': 'v1.2.3'}) as install:
            self.store.updates.run_core_updates()
            self.store.updates.run_core_updates()
            install.assert_called_once()
        events = self.store.updates.history()['events']
        self.assertEqual([event['state'] for event in events], ['completed', 'available'])
        self.assertIn('下次重启', events[0]['detail'])

    def test_core_binary_is_not_replaced_while_dns_policy_pins_its_version(self):
        self.store.security.get = lambda _: {'policies': [{'active': {'revision': 1}}]}
        with patch('router.update_center.install_core') as install:
            with self.assertRaisesRegex(ValueError, '锁定'):
                self.store.updates.install_core('sing-box', {'installed': True, 'version': '1.14.4'})
            install.assert_not_called()
        self.assertEqual(self.store.updates.history()['events'][0]['state'], 'failed')

    def test_worker_result_is_imported_once_after_process_restarts(self):
        result = {'state': 'complete', 'id': 'a' * 32, 'current': '1.0.0', 'target': '1.0.1', 'timestamp': 12345}
        with patch.object(system_update, 'worker_result', return_value=result), \
             patch.object(system_update, 'current_target', return_value='package'):
            self.store.updates.import_worker_result()
            history = Store(self.path).updates.history()
        self.assertEqual(len(history['events']), 1)
        self.assertEqual(history['events'][0]['timestamp'], 12345)
        self.assertEqual(history['events'][0]['target'], 'package')
