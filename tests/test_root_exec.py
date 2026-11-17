import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router import root_exec
from router.server import Store


class RootExecTests(unittest.TestCase):
    def test_missing_helper_skips(self):
        with patch.dict(os.environ, {'HOPWORLD_ROOT_HELPER': '/no/such/hopworld-root'}):
            self.assertIsNone(root_exec.ensure_xray())

    def test_run_uses_sudo_and_surfaces_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'hopworld-root'
            script.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
            with patch.dict(os.environ, {'HOPWORLD_ROOT_HELPER': str(script)}):
                with patch('router.root_exec.subprocess.run') as run:
                    run.return_value.returncode = 0
                    run.return_value.stdout = ''
                    run.return_value.stderr = ''
                    root_exec.ensure_xray()
                cmd = run.call_args.args[0]
                self.assertEqual(cmd[-1], 'xray')
                self.assertIn(str(script), cmd)
                run.return_value.returncode = 1
                run.return_value.stderr = 'sudo: a password is required'
                with self.assertRaises(ValueError) as error:
                    root_exec.ensure_xray()
                self.assertIn('hopworld-install-root.sh', str(error.exception))

    def test_enabling_local_node_requests_xray_and_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db')
            calls = []
            with patch('router.root_exec.run', side_effect=lambda *args, **kwargs: calls.append(args) or None), \
                 patch.object(store.xray, 'ensure_credentials', side_effect=lambda settings: settings), \
                 patch.object(store.xray, 'apply', return_value=''):
                store.configure_local_node(True, '8.8.8.8', 8443, '本地网络')
            self.assertIn(('xray',), calls)
            self.assertIn(('open', 'tcp', '8443'), calls)


if __name__ == '__main__':
    unittest.main()
