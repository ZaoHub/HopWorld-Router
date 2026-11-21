import base64
from contextlib import ExitStack, nullcontext
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from subprocess import CalledProcessError
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from router.egress_guard import change_rules, render_rules
from router import gateway_preflight
from router.linux_gateway import Gateway
from router.security_dns import build_secure_dns_config, validate_resolver
from router.server import Store, make_handler


UPSTREAM = 'a' * 64
RESOLVER = {'name': 'Lab resolver', 'protocol': 'doh', 'address': '192.0.2.10',
            'server_name': 'resolver.example.test', 'port': 443, 'path': '/dns-query'}


class NetworkSecurityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'router.db')
        self.security = self.store.security
        self.store._save_independent_profiles({
            'sing-box': {'egress': 'fixed', 'enabled': True, 'port': 8444, 'upstream_id': UPSTREAM},
        })

    def save(self, *, mode='compatibility', **changes):
        resolver = self.security.add_resolver({**RESOLVER, **changes})
        return self.security.save_policy('sing-box', {
            'mode': mode, 'profile_id': 'sing-box', 'upstream_id': UPSTREAM, 'resolver_id': resolver['id'],
        })

    def run_job(self, action):
        identifier = self.security.start_job('sing-box', action)['id']
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.security.job(identifier)
            if job['finished_at']:
                return job
            time.sleep(.01)
        self.fail('Timed out waiting for completed job')

    def test_draft_apply_and_rollback_use_immutable_revisions(self):
        first = self.save()
        self.assertEqual(first['desired_revision'], 1)
        self.assertIsNone(first['active_revision'])
        with patch.object(self.store, 'apply_security_profile') as apply:
            self.assertEqual(self.run_job('apply')['status'], 'PASS')
            self.assertEqual(apply.call_args.args[-1]['revision'], 1)
            self.save(port=8443)
            self.assertEqual(self.security.binding('sing-box')['resolver']['port'], 443)
            self.assertEqual(self.run_job('apply')['status'], 'PASS')
            self.assertEqual(self.security.binding('sing-box')['resolver']['port'], 8443)
            self.assertEqual(self.run_job('rollback')['status'], 'PASS')
            self.assertEqual(self.security.binding('sing-box')['resolver']['port'], 443)
            self.assertEqual(self.run_job('rollback')['status'], 'PASS')
            self.assertIsNone(apply.call_args.args[-1])
        reopened = Store(self.store.path).security
        policy = reopened.status()['policies'][0]
        self.assertEqual(policy['desired_revision'], 2)
        self.assertIsNone(policy['active_revision'])
        self.assertIsNone(policy['verified_revision'])
        self.assertEqual(reopened.get('events')['events'][0]['action'], 'rollback')

    def test_unsupported_and_failed_apply_cannot_replace_running_revision(self):
        self.save()
        with patch.object(self.store, 'apply_security_profile') as apply:
            self.assertEqual(self.run_job('apply')['status'], 'PASS')
            saved = self.security.binding('sing-box')
            self.save(mode='strict')
            self.assertEqual(self.security.validate('sing-box')['status'], 'UNSUPPORTED')
            self.assertEqual(self.run_job('apply')['status'], 'UNSUPPORTED')
            self.assertEqual(self.security.binding('sing-box'), saved)
            apply.assert_called_once()
            self.save(port=9443)
            apply.side_effect = ValueError('core rejected candidate')
            self.assertEqual(self.run_job('apply')['status'], 'FAIL')
            self.assertEqual(self.security.binding('sing-box'), saved)
        with self.assertRaisesRegex(ValueError, '严格'):
            self.store.start_independent_core('sing-box', {
                'public_host': '', 'port': 8444, 'public_listener': False, 'egress': 'strict', 'upstream_id': UPSTREAM,
            })

    def test_api_enforces_session_and_csrf_including_put(self):
        with ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, 'secure-api-password')) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            root = f'http://127.0.0.1:{server.server_port}'
            api = root + '/api/v1/security/'
            auth = 'Basic ' + base64.b64encode(b'admin:secure-api-password').decode()

            def request(path, data=None, *, origin=True, authorized=True, method=None):
                headers = {'Content-Type': 'application/json'}
                if authorized:
                    headers['Authorization'] = auth
                if origin:
                    headers['Origin'] = root
                encoded = json.dumps(data).encode() if data is not None else None
                with urlopen(Request(api + path, encoded, headers, method=method), timeout=2) as reply:
                    return reply.status, json.load(reply)

            try:
                with self.assertRaises(HTTPError) as no_auth:
                    request('capabilities', authorized=False)
                self.assertEqual(no_auth.exception.code, 401)
                code, resolver = request('dns/resolvers', RESOLVER)
                self.assertEqual(code, 201)
                policy = {'mode': 'compatibility', 'profile_id': 'sing-box', 'upstream_id': UPSTREAM,
                          'resolver_id': resolver['id']}
                with self.assertRaises(HTTPError) as wrong_origin:
                    request('policies/sing-box', policy, origin=False, method='PUT')
                self.assertEqual(wrong_origin.exception.code, 403)
                self.assertEqual(request('policies/sing-box', policy, method='PUT')[0], 200)
                self.assertEqual(request('status')[1]['policies'][0]['status'], 'CONFIGURED_NOT_APPLIED')
            finally:
                server.shutdown()
                thread.join()


class SecureDNSConfigTests(unittest.TestCase):
    def test_pinned_doh_and_dot_only_have_selected_socks_detour(self):
        config = {
            'outbounds': [{'type': 'socks', 'tag': 'egress', 'server': '2001:db8::1', 'server_port': 1080,
                           'domain_resolver': 'system'}],
            'dns': {'servers': [{'type': 'local', 'tag': 'system'}]},
        }
        for protocol, path in (('doh', '/dns-query'), ('dot', '')):
            resolver = validate_resolver({**RESOLVER, 'protocol': protocol, 'path': path})
            result = build_secure_dns_config(config, resolver, udp_over_tcp=True)
            self.assertNotIn('system', json.dumps(result))
            self.assertEqual(result['dns']['servers'][0]['detour'], 'egress')
            self.assertFalse(result['dns']['servers'][0]['tls']['insecure'])
            self.assertEqual(result['outbounds'][0]['udp_over_tcp']['version'], 2)
            self.assertIn({'port': 53, 'action': 'hijack-dns'}, result['route']['rules'])
        with self.assertRaises(ValueError):
            build_secure_dns_config({'outbounds': [{**config['outbounds'][0], 'server': 'socks.example.test'}]},
                                    validate_resolver(RESOLVER))
        for attrs in ({'address': '::ffff:127.0.0.1'}, {'server_name': 'a/../host'}, {'tls_verify': False}):
            with self.assertRaises(ValueError):
                validate_resolver({**RESOLVER, **attrs})

    def test_guard_plan_defaults_to_drop_and_only_permits_pinned_underlay(self):
        rules = change_rules('plan', 'hwr-sec-lab', 'wan0', '2001:db8::1', 1080, (8444, 8445))
        self.assertIn('ip6 daddr 2001:db8::1 tcp dport 1080', rules)
        self.assertIn('ct direction reply ct state established udp sport { 8444, 8445 }', rules)
        self.assertEqual(rules.count('policy drop'), 2)
        self.assertNotIn('accept', render_rules())
        with self.assertRaises(ValueError):
            render_rules('wan0" accept', '2001:db8::1', 1080, (8444,))

    def test_gateway_keeps_lan_traffic_on_tun_and_pins_the_external_tuple(self):
        request = {
            'worker': 'hwr-sec-gw', 'edge': 'hwr-sec-edge', 'lan': 'lan0',
            'worker_transit': 'uplink0', 'edge_transit': 'transit0', 'uplink': 'wan0',
            'lan_networks': ['fd62:2::/64', '10.80.1.0/24'], 'transit_ip': '2001:db8::2',
            'worker_uid': 61077, 'worker_gid': 61077, 'upstream': {'address': '2001:db8:1::10', 'port': 1080},
            'resolver': RESOLVER,
        }
        gateway = Gateway.from_json(request)
        config = gateway.config()
        self.assertFalse(config['inbounds'][0]['auto_route'])
        self.assertEqual(config['inbounds'][0]['type'], 'tun')
        self.assertEqual(config['outbounds'][0]['inet6_bind_address'], request['transit_ip'])
        self.assertTrue(config['outbounds'][0]['udp_over_tcp']['enabled'])
        worker, edge = gateway.rules().values()
        self.assertIn('iifname "lan0" oifname "hwr-tun0" ip6 saddr fd62:2::/64', worker)
        self.assertNotIn('iifname "lan0" oifname "uplink0"', worker)
        self.assertIn('ip6 saddr 2001:db8::2 ip6 daddr 2001:db8:1::10 tcp dport 1080', edge)
        self.assertIn('type filter hook input priority filter; policy drop', edge)
        for changes in ({'worker_uid': 0}, {'edge': request['worker']}, {'lan_networks': ['0.0.0.0/0', 'fd62:2::/64']}):
            with self.assertRaises(ValueError):
                Gateway.from_json({**request, **changes})


class GatewayHostPreflightTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(gateway_preflight.sys, 'platform', 'linux'))
        self.uid = stack.enter_context(patch.object(gateway_preflight.os, 'geteuid', create=True, return_value=0))
        self.tools = {Path(path).name: True for path in gateway_preflight.TOOLS}
        stack.enter_context(patch.object(gateway_preflight, 'gateway_requirements', side_effect=lambda: (self.tools, True)))
        self.namespace = stack.enter_context(patch.object(gateway_preflight, 'mapped_namespace', return_value=nullcontext(1234)))
        stack.enter_context(patch.object(gateway_preflight, '_check_namespace'))
        self.run_command = stack.enter_context(patch.object(gateway_preflight, '_run', side_effect=self.command_output))
        self.nft_error = False
        self.ip_error = False

    def command_output(self, command, *, rules=None):
        if self.nft_error and rules:
            raise CalledProcessError(1, command)
        if self.ip_error and '-j' in command:
            raise CalledProcessError(255, command)
        if '--help' in command:
            return '--ambient-caps --bounding-set --pdeathsig'
        return '[]'

    def test_preflight_distinguishes_missing_tools_and_kernel_permissions(self):
        self.tools['nft'] = False
        self.ip_error = True
        self.namespace.side_effect = ValueError('namespace denied')
        report = gateway_preflight.host_preflight()
        self.assertEqual(report['status'], 'BLOCKED')
        for key in ('nft', 'iproute2_json', 'user_network_namespace'):
            self.assertEqual(report['checks'][key]['status'], 'BLOCKED')
        self.assertEqual(report['checks']['nftables_kernel']['status'], 'SKIPPED')

    def test_preflight_requires_root_for_runtime_checks(self):
        self.uid.return_value = 1000
        report = gateway_preflight.host_preflight()
        self.assertEqual(report['status'], 'NEEDS_ROOT')
        self.namespace.assert_not_called()

    def test_preflight_checks_nft_in_a_disposable_namespace(self):
        self.nft_error = True
        report = gateway_preflight.host_preflight()
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['checks']['nftables_kernel']['status'], 'BLOCKED')
        self.nft_error = False
        report = gateway_preflight.host_preflight()
        self.assertEqual(report['status'], 'READY_FOR_TOPOLOGY')
        self.assertTrue(all(item['status'] == 'PASS' for item in report['checks'].values()))
        command = self.run_command.call_args.args[0]
        body = self.run_command.call_args.kwargs['rules']
        self.assertEqual(command, [gateway_preflight.NSENTER, '--target', '1234', '--net',
                                   gateway_preflight.NFT, '--check', '-f', '-'])
        self.assertIn('masquerade', body)
