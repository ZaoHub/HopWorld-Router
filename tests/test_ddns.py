import unittest
from unittest.mock import patch

from router.ddns import publish_ddns, split_hostname


class DdnsTests(unittest.TestCase):
    def test_split_hostname(self):
        self.assertEqual(split_hostname('node.example.com'), ('node', 'example.com'))
        self.assertEqual(split_hostname('example.com'), ('@', 'example.com'))

    @patch('router.ddns._post_json')
    def test_webhook(self, mock_post):
        with patch.dict('os.environ', {
            'HOPWORLD_DDNS_PROVIDER': 'webhook',
            'HOPWORLD_DDNS_WEBHOOK_URL': 'https://ddns.test/hook',
        }, clear=False):
            publish_ddns('node.example.com', ipv4='1.2.3.4')
            mock_post.assert_called_once_with('https://ddns.test/hook', {
                'hostname': 'node.example.com', 'ipv4': '1.2.3.4', 'ipv6': None,
            })


if __name__ == '__main__':
    unittest.main()
