import json
from subprocess import CompletedProcess
import unittest
from unittest.mock import patch
from router.local_node import build_quic_links
from router.probe import _probe_config, light_xray_outbound, outbound, probe, probe_light, singbox_outbound, UnsupportedNode

class ProbeTests(unittest.TestCase):
    def test_lightweight_hysteria_sends_credentials_on_stdin_and_checks_target_response(self):
        link = 'hy2://secret@example.test:443?obfs=salamander&obfs-password=other&sni=example.test'
        with patch('router.probe.subprocess.run', return_value=CompletedProcess([], 0, stdout=b'204')) as command:
            self.assertEqual(probe_light(link, '/fixture/hopworld-probe', 'cloudflare')['status'], 'ok')
            args = command.call_args.args[0]
            request = json.loads(command.call_args.kwargs['input'])
            self.assertEqual(args, ['/fixture/hopworld-probe'])
            self.assertEqual(request['server'], 'example.test:443')
            self.assertEqual(request['server_name'], 'example.test')
            self.assertEqual(request['password'], 'secret')
            self.assertEqual(request['obfs_password'], 'other')
            self.assertTrue(request['target'].endswith('/generate_204'))
            command.return_value = CompletedProcess([], 0, stdout=b'403')
            self.assertEqual(probe_light(link, '/fixture/hopworld-probe', 'cloudflare')['status'], 'failed')
            self.assertEqual(probe_light(link + '&insecure=1', '/fixture/hopworld-probe', 'cloudflare')['status'], 'unsupported')

    def test_vless_reality_parameters_are_preserved(self):
        link = 'vless://a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89@example.com:443?type=ws&security=reality&pbk=key&sid=abcd&sni=example.com&path=%2Fws'
        result = outbound(link)
        self.assertEqual(result['streamSettings']['realitySettings']['publicKey'], 'key')
        self.assertEqual(result['streamSettings']['wsSettings']['path'], '/ws')
        self.assertEqual(result['settings']['vnext'][0]['port'], 443)

    def test_lightweight_reality_and_trojan_remain_authenticated(self):
        node_id = 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89'
        vless = light_xray_outbound(
            f'vless://{node_id}@example.test:443?security=reality&flow=xtls-rprx-vision&pbk=key&sid=12&sni=cert.example.test'
        )
        self.assertEqual((vless['id'], vless['flow'], vless['fingerprint']), (node_id, 'xtls-rprx-vision', 'chrome'))
        self.assertEqual((vless['server_name'], vless['public_key'], vless['short_id']), ('cert.example.test', 'key', '12'))
        trojan = light_xray_outbound(
            'trojan://secret@example.test:443?type=ws&sni=cert.example.test&host=ws.example.test&path=%2Fws%3Fed%3D2048'
        )
        self.assertEqual((trojan['password'], trojan['security'], trojan['server_name']), ('secret', 'tls', 'cert.example.test'))
        self.assertEqual((trojan['host'], trojan['path'], trojan['early_data']), ('ws.example.test', '/ws', 2048))
        with self.assertRaises(UnsupportedNode):
            light_xray_outbound(f'vless://{node_id}@example.test:443?type=grpc')

    def test_client_flags_that_do_not_change_the_server_are_accepted(self):
        link = 'vless://a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89@example.com:443?type=tcp&security=reality&pbk=key&sni=example.com&flow=xtls-rprx-vision&insecure=0&pcs=&quicSecurity=none'
        result = outbound(link)
        self.assertEqual(result['streamSettings']['realitySettings']['publicKey'], 'key')
        self.assertEqual(result['settings']['vnext'][0]['users'][0]['flow'], 'xtls-rprx-vision')
        trojan = outbound('trojan://secret@example.com:443?type=ws&sni=example.com&peer=example.com&allowInsecure=0&pcs=&fragment=1,40-60,30-50')
        self.assertEqual(trojan['streamSettings']['tlsSettings']['serverName'], 'example.com')

    def test_skip_verify_and_unknown_options_are_still_rejected(self):
        with self.assertRaises(UnsupportedNode):
            outbound('vless://a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89@example.com:443?security=tls&insecure=1')
        with self.assertRaises(UnsupportedNode):
            outbound('vless://a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89@example.com:443?type=ws&unknown=x')
        self.assertEqual(probe('ss://abc@example.com:443')['status'], 'unsupported')
        self.assertEqual(probe('trojan://password@example.com:443', binary='/nonexistent')['status'], 'unavailable')

    def test_published_quic_links_are_parsed_for_testing_with_tls(self):
        identifier = 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89'
        hy2, tuic = build_quic_links('example.test', 8443, identifier, 'hysteria-password', 'tuic-password-value')
        self.assertEqual(singbox_outbound(hy2)['password'], 'hysteria-password')
        self.assertEqual(singbox_outbound(tuic)['uuid'], identifier)
        self.assertEqual(singbox_outbound(tuic)['tls']['alpn'], ['h3'])
        self.assertEqual(singbox_outbound(hy2)['tls'], {'enabled': True, 'server_name': 'example.test'})
        response = probe(hy2, singbox_binary='/nonexistent')
        self.assertIn('sing-box', response['detail'])
        for link in ('hy2://password@example.test:443?insecure=1', 'tuic://bad-id:password@example.test:443'):
            self.assertEqual(probe(link)['status'], 'unsupported')

    def test_native_hysteria_uses_imported_salamander_options(self):
        link = 'hy2://secret@example.test:443?obfs=salamander&obfs-password=other%2Bsecret&sni=example.test'
        _, config = _probe_config(link, 'hysteria2', 8080, '/unused')
        self.assertEqual(config['obfs'], {'type': 'salamander', 'salamander': {'password': 'other+secret'}})
        self.assertEqual(config['tls'], {'sni': 'example.test'})

if __name__ == '__main__': unittest.main()
