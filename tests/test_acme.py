import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router.acme import days_until_expiry, validate_acme_email


class AcmeTests(unittest.TestCase):
    def test_validate_email(self):
        self.assertEqual(validate_acme_email('  a@b.co  '), 'a@b.co')
        with self.assertRaises(ValueError):
            validate_acme_email('not-an-email')

    @patch('router.acme.subprocess.run')
    def test_days_until_expiry(self, mock_run):
        mock_run.return_value.stdout = 'notAfter=Dec 31 23:59:59 2026 GMT\n'
        mock_run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as tmp:
            cert = Path(tmp) / 'cert.pem'
            cert.write_text('dummy', encoding='utf-8')
            days = days_until_expiry(cert)
            self.assertIsNotNone(days)


if __name__ == '__main__':
    unittest.main()
