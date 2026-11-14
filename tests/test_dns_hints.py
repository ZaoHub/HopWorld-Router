import os
import unittest
from unittest.mock import patch

from router.dns_hints import build_dns_hints


class DnsHintsTests(unittest.TestCase):
    def test_fixed_ip_no_records(self):
        hints = build_dns_hints({'public_host': '8.8.8.8'}, probe=False)
        self.assertEqual(hints['mode'], 'fixed_ip')
        self.assertEqual(hints['records'], [])

    @patch.dict(os.environ, {'HOPWORLD_PUBLIC_HOST': '8.8.8.8'}, clear=False)
    def test_fixed_domain_shows_a_record(self):
        hints = build_dns_hints({'public_host': 'node.example.com'}, probe=False)
        self.assertEqual(hints['mode'], 'fixed_domain')
        self.assertEqual(len(hints['records']), 1)
        self.assertEqual(hints['records'][0]['type'], 'A')
        self.assertEqual(hints['records'][0]['host'], 'node')
        self.assertEqual(hints['records'][0]['value'], '8.8.8.8')
        self.assertFalse(hints['records'][0]['managed'])

    def test_ddns_managed_records(self):
        settings = {
            'ddns_enabled': True,
            'public_host': 'node.example.com',
            'ddns_ipv4': '1.2.3.4',
            'ddns_ipv6': '2001:db8::1',
            'public_host_prefer': 'ipv4_first',
        }
        hints = build_dns_hints(settings, probe=False)
        self.assertEqual(hints['mode'], 'ddns')
        self.assertTrue(any(r['type'] == 'A' and r['managed'] for r in hints['records']))
        self.assertTrue(any(r['type'] == 'AAAA' for r in hints['records']))

    def test_auto_ip_mode(self):
        settings = {
            'auto_public_host': True,
            'public_host': '8.8.4.4',
            'public_host_prefer': 'ipv4',
        }
        hints = build_dns_hints(settings, probe=False)
        self.assertEqual(hints['mode'], 'auto_ip')
        self.assertEqual(hints['egress_ipv4'], '8.8.4.4')
