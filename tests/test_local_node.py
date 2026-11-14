import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from router.local_node import build_local_node, build_local_reality_node, validate_public_host
from router.xray_service import XrayService


class LocalNodeTests(unittest.TestCase):
    def test_config_and_link_match(self):
        identifier = 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89'
        config, link = build_local_node('example.com', 443, identifier, '/cert.pem', '/key.pem')
        inbound = config['inbounds'][0]
        self.assertEqual(inbound['settings']['clients'][0]['id'], identifier)
        self.assertEqual(inbound['streamSettings']['security'], 'tls')
        self.assertEqual(urlsplit(link).hostname, 'example.com')
        self.assertEqual(urlsplit(link).port, 443)

    def test_invalid_inputs_are_rejected(self):
        good = ['example.com', 443, 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', '/cert.pem', '/key.pem']
        for index, bad in [(0, '127.0.0.1'), (0, 'bad host'), (1, 0), (2, 'not-uuid'), (3, 'relative')]:
            values = good.copy(); values[index] = bad
            with self.subTest(index=index, value=bad), self.assertRaises(ValueError):
                build_local_node(*values)

    def test_reality_config_and_link_match(self):
        identifier = 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89'
        private_key = 'private-key-value'
        public_key = 'public-key-value'
        short_id = 'abcd1234'
        config, link = build_local_reality_node(
            '8.8.8.8', 8443, identifier, private_key, public_key, short_id,
        )
        inbound = config['inbounds'][0]
        reality = inbound['streamSettings']['realitySettings']
        self.assertEqual(inbound['settings']['clients'][0]['id'], identifier)
        self.assertEqual(inbound['settings']['clients'][0]['flow'], 'xtls-rprx-vision')
        self.assertEqual(inbound['streamSettings']['security'], 'reality')
        self.assertEqual(reality['privateKey'], private_key)
        self.assertEqual(reality['shortIds'], [short_id])
        self.assertEqual(reality['dest'], 'www.microsoft.com:443')
        parts = urlsplit(link)
        query = parse_qs(parts.query)
        self.assertEqual(parts.scheme, 'vless')
        self.assertEqual(parts.hostname, '8.8.8.8')
        self.assertEqual(parts.port, 8443)
        self.assertEqual(parts.username, identifier)
        self.assertEqual(query['security'], ['reality'])
        self.assertEqual(query['pbk'], [public_key])
        self.assertEqual(query['sid'], [short_id])
        self.assertEqual(query['flow'], ['xtls-rprx-vision'])
        self.assertEqual(query['sni'], ['www.microsoft.com'])
        self.assertEqual(unquote(parts.fragment), '本地网络')

    def test_custom_name_is_the_link_label(self):
        _, link = build_local_reality_node(
            '8.8.8.8', 8443, 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'priv', 'pub', 'ab12',
            name='家里宽带',
        )
        self.assertEqual(unquote(urlsplit(link).fragment), '家里宽带')
        with self.assertRaises(ValueError):
            build_local_reality_node(
                '8.8.8.8', 8443, 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'priv', 'pub', 'ab12',
                name='  ',
            )

    def test_reality_uses_egress_proxy_from_environ(self):
        identifier = 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89'
        proxy = 'http://user:pass@hatch-egress-proxy:3128'
        with unittest.mock.patch.dict(os.environ, {'HTTPS_PROXY': proxy}, clear=False):
            config, _ = build_local_reality_node(
                'proxy.example.com', 8443, identifier, 'priv', 'pub', 'ab12',
            )
        self.assertEqual(config['outbounds'][0]['protocol'], 'http')
        self.assertEqual(config['outbounds'][0]['tag'], 'egress')
        self.assertEqual(
            config['outbounds'][0]['settings']['servers'][0]['address'],
            'hatch-egress-proxy',
        )
        self.assertIn('routing', config)

    def test_reality_rejects_invalid_inputs(self):
        good = ['8.8.8.8', 8443, 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'priv', 'pub', 'ab12']
        for index, bad in [(0, ''), (0, 'bad host'), (1, 0), (2, 'not-uuid'), (5, 'zz'), (5, '1')]:
            values = good.copy(); values[index] = bad
            with self.subTest(index=index, value=bad), self.assertRaises(ValueError):
                build_local_reality_node(*values)

    def test_validate_public_host(self):
        self.assertEqual(validate_public_host('8.8.8.8'), '8.8.8.8')
        self.assertEqual(validate_public_host('Example.COM'), 'example.com')
        with self.assertRaises(ValueError):
            validate_public_host('')
        with self.assertRaises(ValueError):
            validate_public_host('not a host')


class XrayRenameTests(unittest.TestCase):
    def test_windows_never_uses_unix_pid_signals(self):
        with tempfile.TemporaryDirectory() as temp:
            service = XrayService(Path(temp), binary='/unused')
            service.pid_path.write_text('1234', encoding='utf-8')
            with unittest.mock.patch('router.xray_service.os.name', 'nt'), \
                 unittest.mock.patch('router.xray_service.os.kill') as kill:
                self.assertFalse(service.running())
                self.assertEqual(service.apply({'enabled': False}), '')
                with self.assertRaisesRegex(ValueError, 'Windows 原生环境暂不支持'):
                    service.apply({'enabled': True})
                service.stop()
                kill.assert_not_called()

    def test_generate_keys_accepts_xray_output_formats(self):
        outputs = (
            'Private key: priv\nPublic key: pub\n',
            'PrivateKey: priv\nPassword: pub\nHash32: ignored\n',
            'PrivateKey: priv\nPassword (PublicKey): pub\nHash32: ignored\n',
        )
        with tempfile.TemporaryDirectory() as temp:
            service = XrayService(Path(temp), binary='/usr/bin/xray')
            for output in outputs:
                with self.subTest(output=output), unittest.mock.patch('router.xray_service.subprocess.run') as run:
                    run.return_value.stdout = output
                    self.assertEqual(service.generate_keys(), ('priv', 'pub'))

    def test_renaming_does_not_restart_when_config_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            service = XrayService(Path(temp), binary=None)
            settings = {
                'enabled': True,
                'public_host': '8.8.8.8',
                'port': 8443,
                'uuid': 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89',
                'private_key': 'priv',
                'public_key': 'pub',
                'short_id': 'abcd1234',
                'name': '本地网络',
            }
            config, _ = service.build(settings)
            service._write_config(config)
            service.pid_path.write_text(str(os.getpid()), encoding='utf-8')
            link = service.apply({**settings, 'name': '家里宽带'})
            self.assertEqual(unquote(urlsplit(link).fragment), '家里宽带')
            self.assertIsNone(service._process)
            with self.assertRaises(ValueError):
                service.apply({**settings, 'port': 9443})


if __name__ == '__main__':
    unittest.main()
