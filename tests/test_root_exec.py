import subprocess
import unittest
from pathlib import Path


class RetiredRootInstallerTests(unittest.TestCase):
    def test_retired_installation_scripts_fail_closed(self):
        deploy = Path(__file__).resolve().parents[1] / 'deploy'
        for command in ('hopworld-install-root.sh', 'hopworld-root'):
            with self.subTest(command=command):
                result = subprocess.run(['bash', str(deploy / command), 'acme'], capture_output=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'V1', result.stderr)
