import contextlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from router import uninstall
from router.admin_auth import ADMIN_ACCOUNT_KEY, ADMIN_PASSWORD_KEY, password_record


class UninstallTests(unittest.TestCase):
    def test_verifier_prefers_edited_account_and_matches_legacy_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'router.db'
            initial = password_record('initial-pass')
            edited = password_record('234567')
            with contextlib.closing(sqlite3.connect(database)) as db, db:
                db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
                db.execute('INSERT INTO settings VALUES (?, ?)', (ADMIN_PASSWORD_KEY, initial))
            self.assertEqual(uninstall.auth_verifier(database, 'admin'), initial)
            self.assertEqual(uninstall.auth_verifier(database, 'preset-pass'), '')
            with contextlib.closing(sqlite3.connect(database)) as db, db:
                db.execute('INSERT INTO settings VALUES (?, ?)', (ADMIN_ACCOUNT_KEY, f'["changed", "{edited}"]'))
            self.assertEqual(uninstall.auth_verifier(database, 'preset-pass'), edited)

    def test_password_and_confirmation_precede_uninstall(self):
        verifier = password_record('correct-pass')
        unit = ('WorkingDirectory=/usr/lib/hopworld-router\n'
                'FragmentPath=/usr/lib/systemd/system/hopworld-router.service\nMainPID=0\nDropInPaths=\n')
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'data'
            data.mkdir()
            env = Path(directory) / 'router.env'
            env.touch()
            with patch.multiple(uninstall, DATA=data, ENV_FILE=env), \
                    patch.object(uninstall.os, 'geteuid', return_value=0), \
                    patch.object(uninstall.sys.stdin, 'isatty', return_value=True), \
                    patch.object(uninstall.subprocess, 'check_output', return_value=unit), \
                    patch.object(uninstall.subprocess, 'run') as execute, \
                    patch.object(uninstall, 'auth_verifier', return_value=verifier), \
                    patch.object(Path, 'read_text', return_value=''), \
                    patch.object(uninstall.getpass, 'getpass', return_value='incorrect') as password, \
                    patch('builtins.input', return_value='cancel') as confirm, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, '管理员密码不正确'):
                    uninstall.uninstall()
                confirm.assert_not_called()
                execute.assert_not_called()
                self.assertTrue(data.is_dir())
                password.return_value = 'correct-pass'
                uninstall.uninstall()
                execute.assert_not_called()
                self.assertTrue(data.is_dir())
                confirm.return_value = 'UNINSTALL'
                uninstall.uninstall()
                self.assertEqual([call.args[0] for call in execute.call_args_list], [
                    ['systemctl', 'stop', uninstall.UNIT], ['dpkg', '--purge', 'hopworld-router'],
                ])
                self.assertFalse(data.exists())
                self.assertFalse(env.exists())
