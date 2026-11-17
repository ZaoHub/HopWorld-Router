import os
import json
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from router.local_node import (
    LOCAL_EGRESS_SOCKS_PORT,
    build_local_node,
    build_local_reality_node,
    build_quic_links,
    build_singbox_config,
    quic_ports,
    validate_public_host,
)
from router.singbox_service import SingboxService
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

    def test_reality_direct_egress_supports_udp(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            config, _ = build_local_reality_node(
                'example.com', 8443, 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'priv', 'pub', 'ab12',
            )
        self.assertEqual(config['inbounds'][0]['protocol'], 'vless')
        self.assertEqual(config['outbounds'], [{'protocol': 'freedom', 'tag': 'direct'}])

    def test_auto_pool_never_falls_back_to_direct(self):
        args = ('example.com', 8443, 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'priv', 'pub', 'ab12')
        config, _ = build_local_reality_node(*args, proxy_pool=[])
        self.assertEqual([item['protocol'] for item in config['outbounds']], ['blackhole'])
        upstream = {'protocol': 'socks', 'settings': {'servers': [{'address': 'example.com', 'port': 1080}]}}
        config, _ = build_local_reality_node(*args, proxy_pool=[upstream], probe_url='https://example.com/check')
        self.assertEqual(config['routing']['balancers'][0]['fallbackTag'], 'blocked')
        self.assertEqual(config['routing']['balancers'][0]['strategy']['type'], 'leastPing')
        self.assertEqual(config['observatory']['probeUrl'], 'https://example.com/check')
        self.assertEqual(config['observatory']['probeInterval'], '1m')
        config, _ = build_local_reality_node(*args, proxy_pool=[upstream], probe_interval=300)
        self.assertEqual(config['observatory']['probeInterval'], '300s')
        for invalid in (True, 1, 301, '60'):
            with self.subTest(interval=invalid), self.assertRaises(ValueError):
                build_local_reality_node(*args, proxy_pool=[upstream], probe_interval=invalid)
        self.assertNotIn('tag', upstream)

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


class QuicLinkTests(unittest.TestCase):
    def test_hysteria2_and_tuic_use_the_same_egress_ports_and_secrets(self):
        hy2, tuic = build_quic_links(
            '8.8.8.8', 8443, 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89',
            'hy2-password-value', 'tuic-password-value', name='家里宽带', insecure=True,
        )
        hy2_parts, tuic_parts = urlsplit(hy2), urlsplit(tuic)
        self.assertEqual((hy2_parts.scheme, hy2_parts.hostname, hy2_parts.port, hy2_parts.username), ('hysteria2', '8.8.8.8', 8444, 'hy2-password-value'))
        self.assertEqual((tuic_parts.scheme, tuic_parts.port, tuic_parts.username, tuic_parts.password), ('tuic', 8445, 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'tuic-password-value'))
        self.assertEqual(parse_qs(hy2_parts.query)['insecure'], ['1'])
        self.assertEqual(parse_qs(tuic_parts.query)['allow_insecure'], ['1'])
        self.assertEqual(unquote(hy2_parts.fragment), '家里宽带 Hysteria2')
        self.assertEqual(unquote(tuic_parts.fragment), '家里宽带 TUIC')
        config = build_singbox_config('8.8.8.8', 8443, 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'hy2-password-value', 'tuic-password-value', '/cert.pem', '/key.pem')
        self.assertEqual([item['type'] for item in config['inbounds']], ['hysteria2', 'tuic'])
        self.assertEqual(config['outbounds'][0]['server_port'], LOCAL_EGRESS_SOCKS_PORT)
        self.assertEqual(quic_ports(65535), (65534, 65533))
        self.assertNotIn(LOCAL_EGRESS_SOCKS_PORT, quic_ports(LOCAL_EGRESS_SOCKS_PORT - 1))
        with self.assertRaises(ValueError):
            build_quic_links('8.8.8.8', 8443, 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'short', 'tuic-password-value')

    def test_singbox_publishes_links_only_after_it_stays_up(self):
        with tempfile.TemporaryDirectory() as temp:
            service = SingboxService(Path(temp), binary='/usr/bin/sing-box')
            settings = {
                'enabled': True, 'public_host': '8.8.8.8', 'port': 8443, 'name': '本地网络',
                'uuid': 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89',
                'hy2_password': 'hy2-password-value', 'tuic_password': 'tuic-password-value',
            }
            with unittest.mock.patch.object(service, '_self_signed', return_value=('/cert.pem', '/key.pem')), \
                 unittest.mock.patch('router.singbox_service.subprocess.Popen') as popen:
                popen.return_value.wait.side_effect = subprocess.TimeoutExpired('sing-box', 0.5)
                popen.return_value.poll.return_value = None
                links = service.apply(settings)
            self.assertEqual(popen.call_args.args[0][:3], ['/usr/bin/sing-box', 'run', '-c'])
            self.assertEqual([urlsplit(link).scheme for link in links], ['hysteria2', 'tuic'])
            self.assertTrue(json.loads((Path(temp) / 'singbox-local.meta.json').read_text())['insecure'])
            missing = SingboxService(Path(temp) / 'absent', binary='')
            with unittest.mock.patch('router.singbox_service.subprocess.Popen') as idle:
                self.assertEqual(missing.apply(settings), [])
            idle.assert_not_called()


class XrayRenameTests(unittest.TestCase):
    def test_node_traffic_uses_private_metrics_for_every_egress_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            service = XrayService(Path(temp), binary='/unused')
            base = {'public_host': 'example.com', 'port': 8443,
                    'uuid': 'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89',
                    'private_key': 'priv', 'public_key': 'pub', 'short_id': 'abcd'}
            for mode in ('default', 'local_proxy', 'auto'):
                with self.subTest(mode=mode):
                    config, _ = service.build({**base, 'egress_mode': mode, 'proxy_pool': []})
                    self.assertEqual(config['inbounds'][0]['tag'], 'local-node-in')
                    self.assertEqual(config['stats'], {})
                    self.assertTrue(config['policy']['system']['statsInboundUplink'])
                    self.assertTrue(config['policy']['system']['statsInboundDownlink'])
                    self.assertEqual(config['routing']['rules'][0]['inboundTag'], ['internal-metrics-in'])
                    self.assertEqual(Path(config['inbounds'][1]['listen']).parent.stat().st_mode & 0o777, 0o700)
                    socks = config['inbounds'][-1]
                    self.assertEqual(socks['tag'], 'local-socks-in')
                    self.assertEqual(socks['listen'], '127.0.0.1')
                    self.assertEqual(socks['port'], LOCAL_EGRESS_SOCKS_PORT)
                    self.assertTrue(socks['settings']['udp'])

    def test_node_traffic_validates_metrics_and_preserves_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            service = XrayService(Path(temp), binary='/unused')
            service.pid_path.write_text('1234')
            for up, down in ((0, 0), (123, 456)):
                payload = {'stats': {'inbound': {'local-node-in': {'uplink': up, 'downlink': down}}}}
                with unittest.mock.patch.object(service, '_metrics', return_value=payload):
                    traffic = service.node_traffic()
                    self.assertEqual((traffic['up_bytes'], traffic['down_bytes'], traffic['total_bytes']), (up, down, up + down))
                    self.assertIsInstance(traffic['instance'], str)
            both = {'stats': {'inbound': {'local-node-in': {'uplink': 1, 'downlink': 2}, 'local-socks-in': {'uplink': 10, 'downlink': 20}}}}
            with unittest.mock.patch.object(service, '_metrics', return_value=both):
                traffic = service.node_traffic()
                self.assertEqual((traffic['up_bytes'], traffic['down_bytes'], traffic['total_bytes']), (11, 22, 33))
            bad_socks = {'stats': {'inbound': {'local-node-in': {'uplink': 1, 'downlink': 2}, 'local-socks-in': {'uplink': -1, 'downlink': 1}}}}
            with unittest.mock.patch.object(service, '_metrics', return_value=bad_socks):
                self.assertEqual(service.node_traffic()['up_bytes'], 1)
            for payload in (None, {}, {'stats': {'inbound': {}}},
                            {'stats': {'inbound': {'local-node-in': {'uplink': True, 'downlink': 1}}}},
                            {'stats': {'inbound': {'local-node-in': {'uplink': -1, 'downlink': 1}}}}):
                with unittest.mock.patch.object(service, '_metrics', return_value=payload):
                    self.assertIsNone(service.node_traffic())

    def test_protocol_status_uses_running_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            service = XrayService(Path(temp), binary='/unused')
            settings = {'enabled': True}
            cases = [
                ([{'tag': 'direct', 'protocol': 'freedom'}], '已配置'),
                ([{'tag': 'egress', 'protocol': 'http'}], '不支持'),
                ([{'tag': 'local-proxy', 'protocol': 'http'}], '不支持'),
                ([{'tag': 'local-proxy', 'protocol': 'socks'}], '待验证'),
                ([{'tag': 'candidate-0', 'protocol': 'vless'}], '取决于上游'),
                ([{'tag': 'blocked', 'protocol': 'blackhole'}], '已阻断'),
            ]
            for outbounds, expected in cases:
                service.config_path.write_text(json.dumps({'outbounds': outbounds}))
                self.assertEqual(service.public_view(settings, running=True)['egress_protocol_info']['udp'], expected)
            self.assertEqual(service.public_view(settings, running=False)['egress_protocol_info']['udp'], '未运行')
            service.config_path.unlink()
            self.assertEqual(service.public_view(settings, running=True)['egress_protocol_info']['udp'], '无法确认')

    def test_auto_observations_use_private_socket_not_public_listener(self):
        with tempfile.TemporaryDirectory() as temp:
            service = XrayService(Path(temp), binary='/usr/bin/xray')
            settings = {'public_host':'example.com', 'port':8443, 'uuid':'a9fc544e-a0a5-4a0c-a2a9-7c08e8057f89', 'private_key':'priv', 'public_key':'pub', 'short_id':'abcd', 'egress_mode':'auto', 'proxy_pool':[{'protocol':'http', 'settings':{'servers':[{'address':'example.net','port':8080}]}}]}
            try:
                config, _ = service.build(settings)
                path = Path(config['inbounds'][1]['listen'])
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(config['metrics'], {'tag': 'internal-metrics'})
                self.assertEqual(config['routing']['rules'][0]['inboundTag'], ['internal-metrics-in'])
                self.assertFalse(path.exists())
            finally:
                if service._metrics_dir: shutil.rmtree(service._metrics_dir, ignore_errors=True)

    def test_xray_immediate_exit_is_reported_without_pid(self):
        with tempfile.TemporaryDirectory() as temp:
            service = XrayService(Path(temp), binary='/usr/bin/xray')
            with unittest.mock.patch('router.xray_service.subprocess.Popen') as popen:
                popen.return_value.wait.return_value = 1
                with self.assertRaisesRegex(ValueError, '启动后立即退出'):
                    service._restart_locked()
            self.assertIsNone(service._process)
            self.assertFalse(service.pid_path.exists())

    def test_xray_process_still_running_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            service = XrayService(Path(temp), binary='/usr/bin/xray')
            with unittest.mock.patch('router.xray_service.subprocess.Popen') as popen:
                popen.return_value.pid = 1234
                popen.return_value.wait.side_effect = subprocess.TimeoutExpired('xray', 0.5)
                service._restart_locked()
            self.assertEqual(service.pid_path.read_text(encoding='utf-8'), '1234')
            self.assertIs(service._process, popen.return_value)

    def test_xray_launch_error_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            service = XrayService(Path(temp), binary='/missing/xray')
            with unittest.mock.patch('router.xray_service.subprocess.Popen', side_effect=OSError('missing')):
                with self.assertRaisesRegex(ValueError, '无法启动 Xray'):
                    service._restart_locked()

    def test_windows_never_uses_unix_pid_signals(self):
        with tempfile.TemporaryDirectory() as temp:
            service = XrayService(Path(temp), binary='/unused')
            service.pid_path.write_text('1234', encoding='utf-8')
            with unittest.mock.patch('router.xray_service.os.name', 'nt'), \
                 unittest.mock.patch('router.xray_service.os.kill') as kill:
                self.assertFalse(service.running())
                self.assertEqual(service.apply({'enabled': False}), '')
                with self.assertRaisesRegex(ValueError, 'Windows 原生环境暂不支持'):
                    service.ensure_credentials({})
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
