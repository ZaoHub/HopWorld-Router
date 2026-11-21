import unittest
from unittest.mock import patch
from router.probe import outbound, probe, UnsupportedNode

class ProbeTests(unittest.TestCase):
    def test_vless_reality_parameters_are_preserved(self):
        link = 'vless://a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89@example.com:443?type=ws&security=reality&pbk=key&sid=abcd&sni=example.com&path=%2Fws'
        result = outbound(link)
        self.assertEqual(result['streamSettings']['realitySettings']['publicKey'], 'key')
        self.assertEqual(result['streamSettings']['wsSettings']['path'], '/ws')
        self.assertEqual(result['settings']['vnext'][0]['port'], 443)

    def test_unknown_options_are_not_silently_ignored(self):
        with self.assertRaises(UnsupportedNode):
            outbound('vless://a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89@example.com:443?type=ws&unknown=x')
        self.assertEqual(probe('ss://abc@example.com:443')['status'], 'unsupported')
        self.assertEqual(probe('trojan://password@example.com:443', binary='/nonexistent')['status'], 'unavailable')

if __name__ == '__main__': unittest.main()
