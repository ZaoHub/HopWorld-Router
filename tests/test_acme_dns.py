import os
import unittest
from unittest.mock import patch

from router.acme_dns import acme_txt_record_name, dns01_configured, resolve_challenge_mode


class AcmeDnsTests(unittest.TestCase):
    def test_acme_txt_name(self):
        sub, zone = acme_txt_record_name('node.example.com')
        self.assertEqual(sub, '_acme-challenge.node')
        self.assertEqual(zone, 'example.com')

    @patch.dict(os.environ, {'HOPWORLD_DDNS_PROVIDER': 'dnspod', 'HOPWORLD_DDNS_ID': '1', 'HOPWORLD_DDNS_TOKEN': 'x'}, clear=False)
    def test_auto_prefers_dns_when_configured(self):
        self.assertEqual(resolve_challenge_mode('auto'), 'dns')

    @patch.dict(os.environ, {'HOPWORLD_ACME_WEBROOT': '/var/www/acme'}, clear=True)
    def test_auto_http_without_dns(self):
        self.assertEqual(resolve_challenge_mode('auto'), 'http')

    def test_dns01_configured_webhook(self):
        with patch.dict(os.environ, {'HOPWORLD_DDNS_WEBHOOK_URL': 'https://x'}, clear=True):
            self.assertTrue(dns01_configured())
