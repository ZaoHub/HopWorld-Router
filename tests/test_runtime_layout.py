import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router import incremental_update, runtime_layout


class RuntimeLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'runtime'
        self.seed = Path(self.temp.name) / 'seed'
        (self.seed / 'router').mkdir(parents=True)
        (self.seed / 'router' / 'version.py').write_text("VERSION = '1.0.4'\n", encoding='utf-8')
        (self.seed / 'router' / 'server.py').write_text('print("ok")\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_migrate_switches_current_without_touching_seed(self):
        before = {path: path.read_bytes() for path in self.seed.rglob('*') if path.is_file()}
        status = runtime_layout.migrate_from_seed(
            seed=self.seed, root=self.root, package_version='1.0.4', commit='a' * 40)
        self.assertTrue(status['migrated'])
        self.assertEqual(status['apt_version'], '1.0.4')
        self.assertTrue(status['running_id'].startswith('apt-1.0.4-'))
        current = runtime_layout.resolve_current(self.root)
        self.assertIsNotNone(current)
        self.assertEqual((current / 'router' / 'version.py').read_text(encoding='utf-8'), "VERSION = '1.0.4'\n")
        self.assertEqual({path: path.read_bytes() for path in self.seed.rglob('*') if path.is_file()}, before)
        again = runtime_layout.migrate_from_seed(seed=self.seed, root=self.root, package_version='1.0.4')
        self.assertEqual(again['running_id'], status['running_id'])

    def test_rollback_restores_previous_release(self):
        first = runtime_layout.migrate_from_seed(
            seed=self.seed, root=self.root, package_version='1.0.4', commit='a' * 40)
        second_seed = Path(self.temp.name) / 'seed2'
        (second_seed / 'router').mkdir(parents=True)
        (second_seed / 'router' / 'version.py').write_text("VERSION = '1.0.5'\n", encoding='utf-8')
        second = runtime_layout.migrate_from_seed(
            seed=second_seed, root=self.root, package_version='1.0.5', commit='b' * 40, force=True)
        self.assertNotEqual(first['running_id'], second['running_id'])
        rolled = runtime_layout.rollback_to(first['running_id'], root=self.root)
        self.assertEqual(rolled['running_id'], first['running_id'])
        self.assertEqual((runtime_layout.resolve_current(self.root) / 'router' / 'version.py').read_text(encoding='utf-8'),
                         "VERSION = '1.0.4'\n")

    def test_update_lock_rejects_concurrent_holders(self):
        lock = Path(self.temp.name) / 'update.lock'
        with runtime_layout.update_lock(lock, blocking=False):
            with self.assertRaisesRegex(ValueError, '更新正在进行'):
                with runtime_layout.update_lock(lock, blocking=False):
                    pass

    def test_channel_persists(self):
        runtime_layout.migrate_from_seed(seed=self.seed, root=self.root, package_version='1.0.4')
        status = runtime_layout.set_channel('development', root=self.root)
        self.assertEqual(status['channel'], 'development')
        self.assertEqual(runtime_layout.read_state(self.root)['channel'], 'development')


    def test_service_channel_is_saved_separately_from_runtime(self):
        data = Path(self.temp.name) / 'data'
        data.mkdir()
        with patch.dict('os.environ', {'HOPWORLD_DATA': str(data), 'HOPWORLD_RUNTIME_ROOT': str(self.root)}):
            self.assertEqual(runtime_layout.set_channel('development')['channel'], 'development')
            self.assertEqual(runtime_layout.status()['channel'], 'development')
            self.assertEqual(runtime_layout.read_state(self.root)['channel'], 'stable')
            self.assertFalse(self.root.exists())

    def test_start_migration_invokes_only_the_fixed_unit(self):
        with patch.object(incremental_update.subprocess, 'run') as launch:
            launch.return_value.returncode = 0
            incremental_update.start_migration()
        launch.assert_called_once_with(
            ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'start', '--no-block',
             'hopworld-router-runtime-migrate.service'], capture_output=True, timeout=5)


class IncrementalPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'runtime'
        self.seed = Path(self.temp.name) / 'seed'
        (self.seed / 'router').mkdir(parents=True)
        (self.seed / 'router' / 'app.py').write_text('x=1\n', encoding='utf-8')
        runtime_layout.migrate_from_seed(
            seed=self.seed, root=self.root, package_version='1.0.4', commit='a' * 40)

    def tearDown(self):
        self.temp.cleanup()

    def test_stable_channel_blocks_main_tracking(self):
        def fetch(url):
            if url.endswith('/commits/main'):
                return {'sha': 'b' * 40}
            raise AssertionError(url)

        result = incremental_update.check(root=self.root, fetch=fetch)
        self.assertEqual(result['state'], 'blocked')

    def test_development_channel_lists_allowed_files(self):
        runtime_layout.set_channel('development', root=self.root)

        def fetch(url):
            if url.endswith('/commits/main'):
                return {'sha': 'b' * 40}
            if '/compare/' in url:
                return {'files': [
                    {'filename': 'router/runtime_layout.py', 'status': 'added'},
                    {'filename': 'README.md', 'status': 'modified'},
                    {'filename': 'router/server.py', 'status': 'renamed', 'previous_filename': 'router/old_server.py'},
                ]}
            if url.endswith('/releases/tags/runtime-v1'):
                raise ValueError('not published')
            raise AssertionError(url)

        # _artifact_urls catches missing release via fetch returning bad shape
        def fetch2(url):
            if url.endswith('/commits/main'):
                return {'sha': 'b' * 40}
            if '/compare/' in url:
                return {'files': [
                    {'filename': 'router/runtime_layout.py', 'status': 'added'},
                    {'filename': 'README.md', 'status': 'modified'},
                    {'filename': 'router/server.py', 'status': 'renamed', 'previous_filename': 'router/old_server.py'},
                ]}
            if url.endswith('/releases/tags/runtime-v1'):
                return {'assets': []}
            raise AssertionError(url)

        result = incremental_update.check(root=self.root, fetch=fetch2)
        self.assertEqual(result['state'], 'available')
        self.assertEqual(result['changed_files'], ['router/runtime_layout.py', 'router/server.py'])
        self.assertEqual(result['changes'][1]['op'], 'rename')
        self.assertFalse(result['apply_ready'])

    def test_apply_ready_when_signed_artifact_exists(self):
        runtime_layout.set_channel('development', root=self.root)
        tip = 'b' * 40

        def fetch(url):
            if url.endswith('/commits/main'):
                return {'sha': tip}
            if '/compare/' in url:
                return {'files': [{'filename': 'router/runtime_layout.py', 'status': 'modified'}]}
            if url.endswith('/releases/tags/runtime-v1'):
                base = f'https://github.com/ZaoHub/HopWorld-Router/releases/download/runtime-v1/hopworld-runtime-{tip}'
                return {'assets': [
                    {'name': f'hopworld-runtime-{tip}.tar.gz', 'browser_download_url': base + '.tar.gz'},
                    {'name': f'hopworld-runtime-{tip}.manifest.json', 'browser_download_url': base + '.manifest.json'},
                    {'name': f'hopworld-runtime-{tip}.manifest.json.sig', 'browser_download_url': base + '.manifest.json.sig'},
                ]}
            raise AssertionError(url)

        result = incremental_update.check(root=self.root, fetch=fetch)
        self.assertTrue(result['apply_ready'])
        queued = incremental_update.apply(root=self.root, fetch=fetch, start_unit=False)
        self.assertEqual(queued['queued'], tip)


if __name__ == '__main__':
    unittest.main()
