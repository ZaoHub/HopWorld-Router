import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from datetime import datetime, timezone

from router.ikev2_service import (
    Ikev2Service,
    apply_handshake,
    build_connectivity_diagnosis,
    client_net_status,
    generate_psk,
    merge_client_traffic,
    parse_handshake_log,
    parse_strongswan_status,
    parse_swanctl_leases,
    parse_traffic_section,
    parse_xfrm_section,
    validate_psk,
    validate_remote_id,
)
from router.server import Store


class Ikev2Tests(unittest.TestCase):
    def test_remote_id_validation(self):
        self.assertEqual(validate_remote_id('hopworld.ikev2'), 'hopworld.ikev2')
        with self.assertRaises(ValueError):
            validate_remote_id('bad id')

    def test_writes_config_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Ikev2Service(tmp, apply_helper='/bin/true')
            settings = service.ensure_credentials({
                'enabled': True,
                'public_host': '8.8.8.8',
                'remote_id': 'hopworld.ikev2',
                'psk': '',
            })
            self.assertEqual(len(settings['psk']), 10)
            self.assertTrue(settings['psk'].isalnum())
            with patch.object(service, '_run_helper', return_value='ok') as helper:
                service.apply(settings)
                helper.assert_called_once_with('apply')
            conf = (Path(tmp) / 'ikev2' / 'ipsec.conf').read_text(encoding='utf-8')
            swanctl = (Path(tmp) / 'ikev2' / 'swanctl.conf').read_text(encoding='utf-8')
            self.assertIn('conn hopworld-bypass-host', conf)
            self.assertNotIn('conn hopworld-ikev2', conf)
            self.assertNotIn('hopworld-v6', swanctl)
            self.assertNotIn('netmask', swanctl)
            self.assertNotIn('dhcp', swanctl)
            self.assertIn('local_ts = 0.0.0.0/0\n', swanctl)
            self.assertIn('hopworld.ikev2', swanctl)
            self.assertNotIn('remote_ts', swanctl)
            self.assertIn(settings['psk'], swanctl)
            view = service.public_view(settings, running=True)
            self.assertTrue(view['enabled'])
            self.assertEqual(view['psk'], settings['psk'])

    def test_diagnosis_no_traffic(self):
        code, _ = client_net_status({'total_bytes': 0})
        self.assertEqual(code, 'no_traffic')
        items = build_connectivity_diagnosis(
            [{'virtual_ip': '10.66.0.1', 'total_bytes': 0}],
            {'status_ok': True, 'ip_forward': True, 'nat_masquerade': True},
            True,
            True,
        )
        self.assertTrue(any('无上网流量' in i['text'] for i in items))
        waiting = build_connectivity_diagnosis(
            [{'public_ip': '192.0.2.13', 'virtual_ip': '10.66.0.2', 'handshake': 'waiting', 'total_bytes': 0}],
            {'status_ok': True, 'ip_forward': True, 'nat_masquerade': True},
            True,
            True,
        )
        hint = next(i for i in waiting if i.get('title') == '系统 VPN 连不上')
        self.assertIn('没有收下确认', hint['text'])
        self.assertEqual(waiting[0]['title'], '系统 VPN 连不上')

    def test_parse_traffic_and_merge(self):
        traffic = parse_traffic_section('vip=10.66.0.1 up=1000 down=2000\n')
        xfrm = parse_xfrm_section('peer=1.2.3.4 up=500 down=800\n')
        clients = [{'virtual_ip': '10.66.0.1', 'public_ip': '1.2.3.4'}]
        merge_client_traffic(clients, traffic, xfrm)
        self.assertEqual(clients[0]['up_bytes'], 1000)
        self.assertEqual(clients[0]['down_bytes'], 2000)
        clients2 = [{'virtual_ip': '10.66.0.2', 'public_ip': '5.6.7.8'}]
        xfrm2 = {'5.6.7.8': {'xfrm_up_bytes': 500, 'xfrm_down_bytes': 800}}
        merge_client_traffic(clients2, {}, xfrm2)
        self.assertEqual(clients2[0]['up_bytes'], 500)

    def test_handshake_shows_udp500_before_tunnel(self):
        now = datetime(2026, 11, 11, 3, 45, 10, tzinfo=timezone.utc)
        text = """
2026-11-11T03:45:02+00:00 host charon[1]: 05[IKE] 192.0.2.10 is initiating an IKE_SA
2026-11-11T03:45:02+00:00 host charon[1]: 07[IKE] IKE_SA hopworld-ikev2[15] established between 8.8.8.8[8.8.8.8]...192.0.2.10[]
2026-11-11T03:45:03+00:00 host charon[1]: 13[NET] received packet: from 192.0.2.10[30953] to 8.8.8.8[4500]
2026-11-11T03:45:03+00:00 host charon[1]: 13[IKE] received retransmit of request with ID 1, retransmitting response
2026-11-11T03:45:08+00:00 host charon[1]: 05[IKE] 1.2.3.4 is initiating an IKE_SA
"""
        events = parse_handshake_log(text, now=now)
        self.assertEqual(events['192.0.2.10']['phase'], 'waiting')
        self.assertEqual(events['192.0.2.10']['label'], '正在连接 · 手机未确认')
        self.assertEqual(events['1.2.3.4']['phase'], 'udp500')
        clients = apply_handshake(
            [{'public_ip': '192.0.2.10', 'virtual_ip': '10.66.0.2', 'net_status': 'no_traffic', 'net_label': '已连接但无流量'}],
            events,
        )
        self.assertEqual(clients[0]['net_label'], '正在连接 · 手机未确认')
        self.assertEqual(clients[1]['public_ip'], '1.2.3.4')
        self.assertEqual(clients[1]['net_status'], 'connecting')
        later = datetime(2026, 11, 11, 3, 45, 30, tzinfo=timezone.utc)
        stale = parse_handshake_log(text, now=later)
        cleared = apply_handshake(
            [{'public_ip': '192.0.2.10', 'virtual_ip': '10.66.0.2', 'total_bytes': 0, 'net_status': 'no_traffic', 'net_label': '已连接但无流量'}],
            stale,
        )
        self.assertEqual(cleared, [])

    def test_parse_swanctl_leases(self):
        sample = """hopworld-ikev2: #6, ESTABLISHED, IKEv2
  remote '10.4.33.55' @ 192.0.2.12[27666] [10.66.0.1]
"""
        leases = parse_swanctl_leases(sample)
        self.assertEqual(leases.get('10.4.33.55'), '10.66.0.1')

    def test_parse_strongswan_ignores_fake_vip(self):
        text = """Security Associations (1 up, 0 connecting):
hopworld-ikev2[6]: ESTABLISHED 1 minute ago, 8.8.8.8[hopworld.ikev2]...1.2.3.4[iphone]
hopworld-ikev2{3}:   0.0.0.0/0 === 0.0.0.0/0
"""
        self.assertEqual(parse_strongswan_status(text), [])

    def test_parse_strongswan_status(self):
        sample = '''
Security Associations (1 up, 0 connecting):
hopworld-ikev2[4]: ESTABLISHED 2 minutes ago, 8.8.8.8[hopworld.ikev2]...192.0.2.11[192.168.0.133]
hopworld-ikev2{2}:  INSTALLED, TUNNEL, reqid 2, ESP in UDP SPIs: c39e7c14_i 02890614_o
hopworld-ikev2{2}:   0.0.0.0/0 === 10.66.0.1/32
'''
        clients = parse_strongswan_status(sample)
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0]['virtual_ip'], '10.66.0.1')
        self.assertEqual(clients[0]['public_ip'], '192.0.2.11')
        self.assertEqual(clients[0]['remote_id'], '192.168.0.133')

    def test_generate_psk_is_short_alphanumeric(self):
        psk = generate_psk()
        self.assertEqual(len(psk), 10)
        self.assertTrue(psk.isalnum())

    def test_validate_psk_custom(self):
        self.assertEqual(validate_psk('HopWorld12'), 'HopWorld12')
        with self.assertRaises(ValueError):
            validate_psk('short')
        with self.assertRaises(ValueError):
            validate_psk('bad-key!')

    def test_ensure_credentials_custom_psk(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Ikev2Service(tmp, apply_helper='/bin/true')
            settings = service.ensure_credentials(
                {'enabled': True, 'public_host': '1.2.3.4', 'psk': 'old12345'},
                new_psk='MyVpnKey9',
            )
            self.assertEqual(settings['psk'], 'MyVpnKey9')

    def test_store_configure_requires_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db')
            with self.assertRaises(ValueError):
                store.configure_ikev2(True, public_host='', remote_id='hopworld.ikev2')

    def test_store_configure_custom_psk(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db')
            with patch.object(store.ikev2, 'apply') as apply_mock:
                view = store.configure_ikev2(
                    True, public_host='8.8.8.8', remote_id='hopworld.ikev2', psk='CustomKey8',
                )
                apply_mock.assert_called_once()
                self.assertEqual(view['psk'], 'CustomKey8')


if __name__ == '__main__':
    unittest.main()
