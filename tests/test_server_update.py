import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router import system_update


class ServerUpdateTests(unittest.TestCase):
    def test_package_reports_apt_instead_of_triggering_the_git_updater(self):
        with patch.dict(os.environ, {'HOPWORLD_PACKAGE_VERSION': '1.0.0~dev1'}), \
             patch.object(system_update, '_unit_running') as unit_running:
            status = system_update.status()
            self.assertEqual((status['state'], status['current']), ('unavailable', '1.0.0~dev1'))
            self.assertEqual((status['managed'], status['channel']), (False, 'APT'))
            self.assertIn('APT', status['error'])
            with self.assertRaisesRegex(ValueError, 'APT'):
                system_update.start()
            unit_running.assert_not_called()

    def test_local_source_does_not_run_the_deployed_updater(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, clear=True):
            root = Path(temporary)
            (root / '.git').mkdir()
            with patch.object(system_update, 'APP_ROOT', root), patch.object(system_update, '_unit_running') as unit_running:
                status = system_update.status()
                self.assertFalse(status['managed'])
                self.assertEqual(status['channel'], '本地源码')
                self.assertIn('Git 工作区', status['error'])
                with self.assertRaisesRegex(ValueError, 'Git 工作区'):
                    system_update.start()
                unit_running.assert_not_called()

    def test_fast_forward_and_refuse_local_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote, repo, commands = root / 'remote.git', root / 'repo', root / 'bin'
            commands.mkdir()
            systemctl = commands / 'systemctl'
            systemctl.write_text('#!/bin/sh\nexit 0\n')
            systemctl.chmod(0o755)
            flock = commands / 'flock'
            flock.write_text('#!/bin/sh\nexit 0\n')
            flock.chmod(0o755)
            subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
            subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True)
            def git(*args):
                return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True)
            git('config', 'user.name', 'test')
            git('config', 'user.email', 'test@example.com')
            git('checkout', '-b', 'main')
            (repo / '.gitignore').write_text('.venv\n')
            (repo / 'requirements.txt').write_text('')
            (repo / 'version').write_text('old')
            git('add', '.')
            git('commit', '-m', 'initial')
            git('remote', 'add', 'origin', str(remote))
            git('push', 'origin', 'main')
            old = git('rev-parse', 'HEAD').stdout.strip()
            (repo / 'version').write_text('new')
            git('commit', '-am', 'update')
            git('push', 'origin', 'main')
            git('reset', '--hard', old)
            (repo / '.venv/bin').mkdir(parents=True)
            (repo / '.venv/bin/python').symlink_to(sys.executable)
            script = (Path(__file__).resolve().parents[1] / 'deploy/hopworld-update.sh').read_text()
            script = script.replace('/opt/hopworld-router', str(repo)).replace('/var/lock/hopworld-router-update.lock', str(root / 'lock'))
            script = script.replace('sleep 5', ':')
            env = {**os.environ, 'PATH': str(commands) + os.pathsep + os.environ['PATH']}
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((repo / 'version').read_text(), 'new')
            (repo / 'version').write_text('custom')
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((repo / 'version').read_text(), 'custom')


if __name__ == '__main__':
    unittest.main()
