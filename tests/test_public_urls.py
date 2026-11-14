import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from router.public_urls import build_public_urls


class PublicUrlsTests(unittest.TestCase):
    def test_domain_urls_use_public_host(self):
        store = MagicMock()
        store.token.return_value = 'abc123'
        store.local_node_settings.return_value = {'public_host': 'node.example.com'}
        store.acme_status.return_value = {'enabled': False, 'error': '', 'cert_path': ''}
        store.ikev2_settings.return_value = {'enabled': False}
        urls = build_public_urls(store)
        self.assertEqual(urls['subscription_url'], 'http://node.example.com:8080/s/abc123')
        self.assertEqual(urls['admin_url'], 'http://node.example.com:8080')

    def test_https_subscription_when_cert_ready(self):
        store = MagicMock()
        store.token.return_value = 'tok'
        store.local_node_settings.return_value = {'public_host': 'node.example.com'}
        store.acme_status.return_value = {
            'enabled': True,
            'error': '',
            'cert_path': '/var/lib/hopworld-router/certs/node.example.com/fullchain.pem',
            'days_left': 88.5,
        }
        store.ikev2_settings.return_value = {'enabled': False}
        with patch.object(Path, 'is_file', return_value=True):
            urls = build_public_urls(store)
        self.assertTrue(urls['subscription_https'])
        self.assertEqual(urls['subscription_url'], 'https://node.example.com/s/tok')
        self.assertEqual(urls['admin_url'], 'http://node.example.com:8080')
        self.assertEqual(urls['cert_days_left'], 88.5)

    @patch.dict(os.environ, {'HOPWORLD_PORT': '9090'}, clear=False)
    def test_custom_port(self):
        store = MagicMock()
        store.token.return_value = 't'
        store.local_node_settings.return_value = {'public_host': 'node.example.com'}
        store.acme_status.return_value = {'enabled': False, 'error': '', 'cert_path': ''}
        store.ikev2_settings.return_value = {'enabled': True}
        urls = build_public_urls(store)
        self.assertIn(':9090/s/t', urls['subscription_url'])
        self.assertIn('ikev2.mobileconfig', urls['ikev2_profile_url'])
