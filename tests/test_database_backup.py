from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from router.database_backup import data_lock, restore, snapshot
from router.server import Store


@unittest.skipUnless(os.name == 'posix', 'Offline restore uses a POSIX data lock')
class DatabaseBackupTests(unittest.TestCase):
    def test_restore_preserves_previous_data_and_requires_stopped_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / 'live' / 'router.db')
            store.add_manual('trojan://original@node.example:443#Original')
            expected = store.nodes()
            backup = root / 'snapshot.db'
            with store.connect() as source:
                snapshot(source, backup)
            store.add_manual('trojan://additional@node.example:443#Additional')
            self.assertIsNone(restore(backup, store.data_dir))
            with data_lock(store.data_dir), self.assertRaisesRegex(ValueError, '正在使用'):
                restore(backup, store.data_dir, apply=True)
            self.assertEqual(len(store.nodes()), 2)
            previous = restore(backup, store.data_dir, apply=True)
            self.assertEqual(previous.stat().st_mode & 0o777, 0o600)
            self.assertEqual(Store(store.path).nodes(), expected)
            self.assertEqual(len(Store(previous).nodes()), 2)

    def test_unrelated_database_cannot_replace_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup = root / 'foreign.db'
            with closing(sqlite3.connect(backup)) as db:
                db.execute('CREATE TABLE settings (k TEXT)')
            with self.assertRaisesRegex(ValueError, '缺少 HopWorld Router 数据表'):
                restore(backup, root / 'live', apply=True)
            self.assertFalse((root / 'live').exists())
