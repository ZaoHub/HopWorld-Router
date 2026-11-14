import base64
import json
import unittest
from urllib.parse import parse_qs, urlsplit
from router.subscription import parse_subscription


class ClashTests(unittest.TestCase):
    def test_convert_and_report_unsupported(self):
        content = b'''proxies:
  - {name: First, type: vless, server: example.com, port: 443, uuid: 2f3e2bf8-cf53-4715-a436-5afedc29bffd, tls: true, sni: example.com, network: ws, ws-opts: {path: /ws, headers: {Host: example.com}}}
  - {name: Unsupported, type: vless, server: example.com, port: 443, uuid: 2f3e2bf8-cf53-4715-a436-5afedc29bffd, network: xhttp, xhttp-opts: {mode: stream-one}}
  - {name: SS, type: ss, server: example.org, port: 1234, cipher: aes-256-gcm, password: secret}
  - {name: VM, type: vmess, server: example.net, port: 443, uuid: 2f3e2bf8-cf53-4715-a436-5afedc29bffd, alterId: 0, cipher: auto, tls: true, network: ws, ws-opts: {path: /socket}}
'''
        links, skipped = parse_subscription(content)
        self.assertEqual(skipped, 1)
        self.assertEqual(len(links), 3)
        parsed = urlsplit(links[0]); query = parse_qs(parsed.query)
        self.assertEqual((parsed.scheme, parsed.hostname, parsed.port), ('vless','example.com',443))
        self.assertEqual(query['type'], ['ws'])
        self.assertEqual(query['path'], ['/ws'])
        self.assertEqual(query['security'], ['tls'])
        self.assertTrue(links[1].startswith('ss://'))
        vmess = json.loads(base64.b64decode(links[2][8:]))
        self.assertEqual((vmess['net'], vmess['path'], vmess['tls']), ('ws','/socket','tls'))

    def test_reject_yaml_aliases(self):
        with self.assertRaises(ValueError):
            parse_subscription(b'proxies:\n  - &a {name: x}\n  - *a\n')

    def test_unknown_node_options_are_not_silently_dropped(self):
        content = b'''proxies:
  - {name: bad, type: trojan, server: example.com, port: 443, password: secret, plugin: ignored}
  - {name: good, type: trojan, server: example.com, port: 443, password: secret, tls: true}
'''
        links, skipped = parse_subscription(content)
        self.assertEqual((len(links), skipped), (1, 1))


if __name__ == '__main__': unittest.main()
