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
            'domain': 'node.example.com',
            'error': '',
            'cert_path': '/var/lib/hopworld-router/certs/node.example.com/fullchain.pem',
            'days_left': 88.5,
        }
        store.ikev2_settings.return_value = {'enabled': False}
        with patch.object(Path, 'is_file', return_value=True):
            urls = build_public_urls(store)
        self.assertTrue(urls['subscription_https'])
        self.assertEqual(urls['subscription_url'], 'https://node.example.com/s/tok')
        self.assertEqual(urls['public_subscription_url'], '')
        self.assertEqual(urls['admin_url'], 'http://node.example.com:8080')
        self.assertEqual(urls['cert_days_left'], 88.5)
        store.acme_status.return_value['error'] = '续签失败'
        with patch.object(Path, 'is_file', return_value=True):
            self.assertEqual(build_public_urls(store)['subscription_url'], urls['subscription_url'])

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

    @patch.dict(os.environ, {'HOPWORLD_SUBSCRIPTION_BASE': 'https://edge.example.com'}, clear=True)
    def test_publication_works_independently_of_node_host(self):
        store = MagicMock()
        store.token.return_value = 't'
        store.local_node_settings.return_value = {'public_host': ''}
        store.acme_status.return_value = {}
        store.ikev2_settings.return_value = {'enabled': True}
        listener = 'https://router.example.com:9443'
        self.assertEqual(build_public_urls(store)['public_subscription_url'], 'https://edge.example.com/s/t')
        proxied = build_public_urls(store, subscription_base=listener)
        self.assertEqual(proxied['public_subscription_url'], 'https://edge.example.com/s/t')
        self.assertEqual(proxied['subscription_url'], proxied['public_subscription_url'])
        self.assertEqual(proxied['ikev2_profile_url'], 'https://edge.example.com/s/t/ikev2.mobileconfig')
        for override in ('', 'http://edge.example.com'):
            with self.subTest(override=override), patch.dict(os.environ, {'HOPWORLD_SUBSCRIPTION_BASE': override}):
                direct = build_public_urls(store, subscription_base=listener)
                self.assertEqual(direct['public_subscription_url'], f'{listener}/s/t')
