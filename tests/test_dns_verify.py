import unittest
from unittest.mock import patch

from router.dns_verify import verify_dns_resolution


class DnsVerifyTests(unittest.TestCase):
    def test_skip_when_public_host_is_ip(self):
        result = verify_dns_resolution({'public_host': '8.8.8.8'})
        self.assertEqual(result['status'], 'skip')

    @patch('router.dns_verify._lookup_addresses')
    def test_ok_when_a_record_matches(self, mock_lookup):
        mock_lookup.return_value = {'ipv4': {'8.8.8.8'}, 'ipv6': set()}
        result = verify_dns_resolution({
            'public_host': 'node.example.com',
            'ddns_ipv4': '8.8.8.8',
        })
        self.assertEqual(result['status'], 'ok')
        self.assertIn('8.8.8.8', result['detail'])

    @patch('router.dns_verify._lookup_addresses')
    def test_mismatch(self, mock_lookup):
        mock_lookup.return_value = {'ipv4': {'203.0.113.1'}, 'ipv6': set()}
        result = verify_dns_resolution({
            'public_host': 'node.example.com',
            'ddns_ipv4': '8.8.8.8',
        })
        self.assertEqual(result['status'], 'mismatch')
