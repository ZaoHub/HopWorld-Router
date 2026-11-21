import unittest
from unittest.mock import MagicMock, patch

import importlib.util
from pathlib import Path

_BACKENDS = Path(__file__).resolve().parents[1] / 'deploy' / 'dns-webhook' / 'backends.py'
_spec = importlib.util.spec_from_file_location('dns_webhook_backends', _BACKENDS)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


class DnsWebhookBackendTests(unittest.TestCase):
    def test_split_hostname(self):
        self.assertEqual(_mod.split_hostname('node.example.com'), ('node', 'example.com'))

    def test_handle_ddns(self):
        mock_ddns = MagicMock()
        mock_acme = MagicMock()
        with patch.dict('os.environ', {'HOPWORLD_DNS_BACKEND': 'cloudflare'}, clear=False):
            with patch.dict(_mod._BACKENDS, {'cloudflare': (mock_ddns, mock_acme)}):
                _mod.handle_payload({'hostname': 'node.example.com', 'ipv4': '1.2.3.4'})
        mock_ddns.assert_called_once_with('node.example.com', '1.2.3.4', None)

    def test_handle_acme(self):
        mock_ddns = MagicMock()
        mock_acme = MagicMock()
        with patch.dict('os.environ', {'HOPWORLD_DNS_BACKEND': 'cloudflare'}, clear=False):
            with patch.dict(_mod._BACKENDS, {'cloudflare': (mock_ddns, mock_acme)}):
                _mod.handle_payload({
                    'action': 'acme_txt',
                    'phase': 'publish',
                    'zone': 'example.com',
                    'sub_domain': '_acme-challenge.node',
                    'txt': 'x',
                })
        mock_acme.assert_called_once_with('publish', 'example.com', '_acme-challenge.node', 'x')


if __name__ == '__main__':
    unittest.main()
