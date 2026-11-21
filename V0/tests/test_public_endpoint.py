import unittest
from unittest.mock import patch

from router.public_endpoint import detect_public_host, detect_public_ip, is_ip_host


class PublicEndpointTests(unittest.TestCase):
    @patch('router.public_endpoint.urlopen')
    def test_detect_ipv4(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'8.8.4.4\n'
        self.assertEqual(detect_public_ip(4), '8.8.4.4')

    @patch('router.public_endpoint.urlopen')
    def test_detect_ipv6(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'2a04:4e41:10:639f::7594:e39f\n'
        self.assertEqual(detect_public_ip(6), '2a04:4e41:10:639f::7594:e39f')

    @patch('router.public_endpoint.detect_public_ip')
    def test_prefer_ipv4_first(self, mock_detect):
        mock_detect.side_effect = [ValueError('v4 fail'), '2001:db8::1']
        self.assertEqual(detect_public_host('ipv4_first'), '2001:db8::1')
        self.assertEqual(mock_detect.call_count, 2)

    def test_is_ip_host(self):
        self.assertTrue(is_ip_host('1.2.3.4'))
        self.assertTrue(is_ip_host('2001:db8::1'))
        self.assertFalse(is_ip_host('node.example.com'))


if __name__ == '__main__':
    unittest.main()
