"""Verify authenticated Reality and Hysteria 2 via real frps/frpc on loopback.

Set HOPWORLD_FRPC, HOPWORLD_TEST_FRPS, HOPWORLD_XRAY, HOPWORLD_HYSTERIA
and HOPWORLD_SINGBOX to the corresponding binaries.
"""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
from tempfile import TemporaryDirectory
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit

from router.independent_core import build_local_reality_node, render_publisher
from router.server import Store
from router.subscription import decode_subscription
from tests.e2e_independent_cores import (
    DOMAIN, HOST, echo, free_port, listener, round_trip, start_client, stop,
)


def wait_for(check, description, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.15)
    raise AssertionError(description)


def main():
    for name in ('HOPWORLD_FRPC', 'HOPWORLD_TEST_FRPS', 'HOPWORLD_XRAY', 'HOPWORLD_HYSTERIA', 'HOPWORLD_SINGBOX'):
        if not os.environ.get(name):
            raise SystemExit(f'Set {name} to its binary path')
    binaries = {'xray-core': os.environ['HOPWORLD_XRAY'], 'sing-box': os.environ['HOPWORLD_SINGBOX']}
    vps = '::1'

    with TemporaryDirectory() as directory, ExitStack() as stack:
        root = Path(directory)
        cert, key = root / 'cert.pem', root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                        '-subj', f'/CN={DOMAIN}', '-addext', f'subjectAltName=DNS:{DOMAIN},IP:{HOST}',
                        '-keyout', str(key), '-out', str(cert)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.set_alpn_protocols(['h2', 'http/1.1'])
        context.load_cert_chain(cert, key)
        target = listener(stack)
        target_port = target.getsockname()[1]
        datagrams = listener(stack, socket.SOCK_DGRAM, target_port)
        handshake = listener(stack)
        handshake_port = handshake.getsockname()[1]
        for sock, tls in ((target, None), (datagrams, None), (handshake, context)):
            threading.Thread(target=echo, args=(sock, tls), daemon=True).start()

        def local_reality(*args, **kwargs):
            return build_local_reality_node(*args, dest=f'{HOST}:{handshake_port}', server_name=DOMAIN, **kwargs)

        def isolated_publisher(core_id, *args, **kwargs):
            config, uris = render_publisher(core_id, *args, **kwargs)
            if core_id == 'xray-core':
                config['outbounds'][0]['settings'] = {'finalRules': [
                    {'action': 'allow', 'network': 'tcp,udp', 'ip': [f'{HOST}/32'], 'port': str(target_port)},
                ]}
            return config, uris

        stack.enter_context(patch('router.independent_core.build_local_reality_node', side_effect=local_reality))
        stack.enter_context(patch('router.server.render_publisher', side_effect=isolated_publisher))
        store = Store(root / 'router.db')
        stack.enter_context(patch.object(store, 'require_domain_binding'))
        store.register_domain(DOMAIN, 'ipv4')
        store.save_acme_settings({**store.acme_settings(), 'domain': DOMAIN, 'cert_path': str(cert), 'key_path': str(key)})
        taken = {target_port, handshake_port}
        control = free_port(taken)
        ports = {'xray-core': free_port(taken), 'hysteria2': free_port(taken)}
        public_ports = {core: free_port(taken) for core in ports}
        client_ports = {}
        for core, port in ports.items():
            store.start_independent_core(core, {'public_host': DOMAIN, 'port': port, 'public_listener': False,
                                               'egress': 'direct', 'upstream_id': ''})
            stack.callback(store.independent[core].stop)
        stack.callback(store.relay.stop)

        token = secrets.token_urlsafe(32)
        config = root / 'frps.json'
        config.write_text(json.dumps({
            'bindAddr': HOST, 'bindPort': control, 'proxyBindAddr': vps,
            'auth': {'method': 'token', 'token': token, 'additionalScopes': ['HeartBeats', 'NewWorkConns']},
            'transport': {'tls': {'force': True, 'certFile': str(cert), 'keyFile': str(key)}},
            'allowPorts': [{'single': port} for port in (*ports.values(), *public_ports.values())],
        }))
        log = stack.enter_context((root / 'frps.log').open('wb'))

        def start_vps():
            process = subprocess.Popen([os.environ['HOPWORLD_TEST_FRPS'], '-c', str(config)], stdout=log, stderr=log)
            stack.callback(stop, process)
            wait_for(lambda: tcp_open(HOST, control), 'frps did not start', timeout=5)
            return process

        def ready(count):
            status = store.relay_status()
            return sum(bool(mapping.get('registered')) for mapping in status['mappings']) == count

        vps_process = start_vps()
        settings = {'server': HOST, 'port': control, 'domain': DOMAIN, 'token': token,
                    'ca': cert.read_text(), 'cores': list(ports)}
        staged = store.configure_relay({**settings, 'cores': []})
        assert staged['configured'] and not staged['enabled'] and not staged['mappings']
        store.configure_relay({**settings, 'cores': ['xray-core']})
        wait_for(lambda: ready(1), 'Reality registration failed')
        original_id = next(node['id'] for node in store.nodes() if node.get('core_id') == 'xray-core')
        try:
            store.configure_relay({**settings, 'cores': ['xray-core'],
                                   'public_ports': {f"xray-core:{ports['xray-core']}": control}})
        except ValueError as error:
            assert 'VPS 控制端口冲突' in str(error), error
        else:
            raise AssertionError('Accepted the TCP control port as a public port')
        try:
            store.configure_relay({**settings, 'cores': ['xray-core'], 'token': secrets.token_urlsafe(32)})
        except ValueError as error:
            assert '新隧道未完成端口注册' in str(error), error
        else:
            raise AssertionError('Replaced a working relay with an invalid token')
        wait_for(lambda: ready(1), 'Previous token did not recover after failed authentication')
        settings['public_ports'] = {f'{core}:{ports[core]}': port for core, port in public_ports.items()}
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as occupied:
            occupied.bind((vps, public_ports['hysteria2']))
            try:
                store.configure_relay(settings)
            except ValueError as error:
                assert 'VPS 拒绝映射' in str(error), error
            else:
                raise AssertionError('Accepted an occupied VPS UDP port')
            assert store.relay_settings()['cores'] == ['xray-core']
            assert urlsplit(store.node_link(original_id)).port == ports['xray-core']
        wait_for(lambda: ready(1), 'Previous mapping did not recover after a rejected change')
        store.configure_relay(settings)
        wait_for(lambda: ready(2), 'TCP/UDP registration failed')
        assert token not in json.dumps(store.relay_status())
        assert store.relay.config_path.stat().st_mode & 0o777 == 0o600
        assert {urlsplit(uri).port for uri in decode_subscription(store.subscription().encode())} == set(public_ports.values())
        for core in ports:
            node = next(node for node in store.nodes() if node.get('core_id') == core)
            uri = store.node_link(node['id'])
            assert node['port'] == ports[core] and node['public_port'] == public_ports[core]
            assert urlsplit(uri).port == public_ports[core]
            if core == 'xray-core':
                assert node['id'] == original_id
                store.set_node_enabled(original_id, False)
                assert urlsplit(decode_subscription(store.subscription().encode())[0]).port == public_ports['hysteria2']
                store.set_node_enabled(original_id, True)
            client_port = free_port(taken)
            client_ports[core] = client_port
            start_client(stack, root, core, uri, target_port, client_port, cert, binaries, server=vps)
            round_trip(client_port)
        print('Reality and Hysteria 2 authenticated via frp and carried TCP and UDP payloads', flush=True)

        stop(vps_process)
        wait_for(lambda: ready(0) and not tcp_open(vps, public_ports['xray-core']), 'VPS mappings remained open')
        assert all(store.independent[core].running() for core in ports)
        start_vps()
        wait_for(lambda: ready(2), 'frpc did not reconnect after VPS restart')
        for port in client_ports.values():
            round_trip(port)
        print('VPS loss closed the remote listener and restart restored both mappings', flush=True)

        store.relay.stop()
        resumed = Store(root / 'router.db')
        stack.callback(resumed.relay.stop)
        resumed.sync_relay()
        wait_for(lambda: all(mapping.get('registered') for mapping in resumed.relay_status()['mappings']),
                 'Persisted mappings did not recover after manager restart')
        assert not resumed.configure_relay({'action': 'stop'})['enabled']
        resumed.sync_relay()
        assert not resumed.relay.running()
        assert not tcp_open(vps, public_ports['xray-core'])
        print('Saved mappings restored after Router restart and stayed stopped after an explicit stop', flush=True)


def tcp_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=.25):
            return True
    except OSError:
        return False


if __name__ == '__main__':
    main()
