import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ServerUpdateTests(unittest.TestCase):
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
