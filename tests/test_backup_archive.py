from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from router.acme import cert_paths
from router.backup_archive import FORMAT, _extract_bundle, create_archive, restore_archive
from router.database_backup import data_lock
from router.server import Store


class BackupArchiveTests(unittest.TestCase):
    def test_archive_rejects_paths_outside_managed_files(self):
        for filename in ('../escape', '/absolute', 'certs/../privkey.pem', 'bin/xray'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = BytesIO()
                with ZipFile(archive, 'w') as bundle:
                    bundle.comment = FORMAT
                    bundle.writestr(filename, b'content')
                with self.assertRaises(ValueError):
                    _extract_bundle(archive, root)
                self.assertEqual(list(root.iterdir()), [])

    @unittest.skipUnless(os.name == 'posix' and all(shutil.which(tool) for tool in ('age', 'age-keygen', 'openssl')),
                         'requires POSIX, age, age-keygen and openssl')
    def test_encrypted_backup_retains_certificate_and_rejects_wrong_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            domain = 'router.example.test'
            key, cert = root / 'fixture.key', root / 'fixture.crt'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                            '-subj', f'/CN={domain}', '-addext', f'subjectAltName=DNS:{domain}',
                            '-out', str(cert), '-keyout', str(key)], check=True, capture_output=True)
            original = Store(root / 'original' / 'router.db')
            with patch.object(original, 'require_domain_binding'):
                original.register_domain(domain, 'ipv4')
            original.import_manual_certificate(domain, cert.read_text(), key.read_text())
            cached_cert, cached_key = cert_paths(original.data_dir, domain)
            shutil.copyfile(cert, cached_cert)
            shutil.copyfile(key, cached_key)
            original.add_manual('trojan://backup-secret@upstream.example.test:443#Backup')
            (original.data_dir / 'xray-local.pid').write_text('12345')
            archive, restored = root / 'router.age', root / 'restored'
            identity, other = root / 'recovery.key', root / 'other.key'
            for path in (identity, other):
                subprocess.run(['age-keygen', '-o', str(path)], check=True, capture_output=True)
            recipient = subprocess.check_output(['age-keygen', '-y', str(identity)], text=True).strip()
            with data_lock(original.data_dir), self.assertRaisesRegex(ValueError, '正在使用'):
                create_archive(archive, original.data_dir, recipient)
            self.assertFalse(archive.exists())
            create_archive(archive, original.data_dir, recipient)
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(b'backup-secret', archive.read_bytes())
            with self.assertRaisesRegex(ValueError, '备份文件已存在'):
                create_archive(archive, original.data_dir, recipient)
            with self.assertRaisesRegex(ValueError, 'age 解密失败'):
                restore_archive(archive, restored, other)
            self.assertFalse(restored.exists())
            damaged = root / 'damaged.age'
            damaged.write_bytes(archive.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, 'age 解密失败'):
                restore_archive(damaged, restored, identity)
            self.assertFalse(restored.exists())

            restore_archive(archive, restored, identity)
            migrated = Store(restored / 'router.db')
            self.assertEqual(migrated.subscription(), original.subscription())
            self.assertEqual(migrated.token(), original.token())
            self.assertFalse((restored / 'xray-local.pid').exists())
            cert_path, key_path = map(Path, migrated._quic_certificate({'public_host': domain}))
            self.assertTrue(cert_path.is_relative_to(restored.resolve()))
            self.assertEqual(cert_path.read_bytes(), cert.read_bytes())
            self.assertEqual(key_path.read_bytes(), key.read_bytes())
            self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
            cached_cert, cached_key = cert_paths(restored, domain)
            self.assertEqual(cached_cert.read_bytes(), cert.read_bytes())
            self.assertEqual(cached_key.read_bytes(), key.read_bytes())
            with self.assertRaisesRegex(ValueError, '恢复目录已存在'):
                restore_archive(archive, restored, identity)
