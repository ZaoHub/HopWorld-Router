import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from router.runtime_artifact import install_artifact, load_manifest, verify_signature
from router import incremental_update, runtime_layout

@unittest.skipUnless(__import__('shutil').which('openssl'), 'OpenSSL required')
class RuntimeArtifactTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.private_key = directory / 'test-signing.pem'
        self.public_key = directory / 'test-signing.pub'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.private_key)], check=True)
        subprocess.run(['openssl', 'pkey', '-in', str(self.private_key), '-pubout', '-out', str(self.public_key)], check=True)

    def test_sign_verify_and_switch_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'out'
            commit = 'a' * 40
            # Build from a tiny fake tree by invoking builder against a temp git-less layout is hard;
            # instead craft archive/manifest manually and sign with openssl like the builder.
            tree = Path(temporary) / 'tree'
            (tree / 'router').mkdir(parents=True)
            payload = b"VERSION = '1.0.5'\n"
            target = tree / 'router' / 'version.py'
            target.write_bytes(payload)
            digest = __import__('hashlib').sha256(payload).hexdigest()
            archive = output / f'hopworld-runtime-{commit}.tar.gz'
            output.mkdir()
            import tarfile
            with tarfile.open(archive, 'w:gz') as tar:
                tar.add(tree / 'router', arcname='router')
            manifest = {
                'commit': commit,
                'compatible_package_major': 1,
                'archive': archive.name,
                'archive_sha256': __import__('hashlib').sha256(archive.read_bytes()).hexdigest(),
                'files': [{'path': 'router/version.py', 'sha256': digest, 'op': 'add'}],
            }
            manifest_path = output / f'hopworld-runtime-{commit}.manifest.json'
            signature_path = output / f'hopworld-runtime-{commit}.manifest.json.sig'
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            subprocess.run(
                ['openssl', 'pkeyutl', '-sign', '-inkey', str(self.private_key), '-rawin',
                 '-in', str(manifest_path), '-out', str(signature_path)],
                check=True, capture_output=True,
            )
            verify_signature(manifest_path, signature_path, public_key=self.public_key)
            load_manifest(manifest_path, signature_path, archive, public_key=self.public_key)

            root = Path(temporary) / 'runtime'
            seed = Path(temporary) / 'seed'
            (seed / 'router').mkdir(parents=True)
            (seed / 'router' / 'version.py').write_text("VERSION = '1.0.4'\n", encoding='utf-8')
            runtime_layout.migrate_from_seed(seed=seed, root=root, package_version='1.0.4', commit='b' * 40)
            result = incremental_update.apply_local(
                manifest_path, signature_path, archive, root=root, public_key=self.public_key)
            self.assertEqual(result['commit'], commit)
            current = runtime_layout.resolve_current(root)
            self.assertEqual((current / 'router' / 'version.py').read_bytes(), payload)

    def test_bad_signature_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            commit = 'c' * 40
            archive = output / f'hopworld-runtime-{commit}.tar.gz'
            archive.write_bytes(b'not-a-tar')
            manifest_path = output / f'hopworld-runtime-{commit}.manifest.json'
            signature_path = output / f'hopworld-runtime-{commit}.manifest.json.sig'
            manifest_path.write_text('{}', encoding='utf-8')
            signature_path.write_bytes(b'\x00' * 64)
            with self.assertRaisesRegex(ValueError, '签名'):
                verify_signature(manifest_path, signature_path, public_key=self.public_key)


if __name__ == '__main__':
    unittest.main()
