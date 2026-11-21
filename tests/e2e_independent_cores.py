"""Run authenticated TCP/UDP traffic through all six independent publishers on loopback.

Supply the binaries using HOPWORLD_XRAY, HOPWORLD_SINGBOX, HOPWORLD_MIHOMO,
HOPWORLD_HYSTERIA, HOPWORLD_SSSERVER and HOPWORLD_TUIC_SERVER.
Set HOPWORLD_TEST_DUAL_STACK=1 to verify both IPv4 and IPv6 listeners on an isolated test host.
Set HOPWORLD_TEST_NATIVE_PROBES=1 and supply HOPWORLD_SSLOCAL and HOPWORLD_TUIC_CLIENT
to verify the node manager's HTTP probe using each publisher's own client.
Set HOPWORLD_TEST_LIGHT_PROBE to a compiled hopworld-probe to also test lightweight REALITY forwarding.
"""

from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

from router import probe as node_probe
from router.core_install import core_client_binary
from router.independent_core import PUBLISHERS, build_local_reality_node, render_publisher
from router.probe import outbound, singbox_outbound
from router.server import Store


HOST = '127.0.0.1'
DOMAIN = 'entry.example.test'


def echo(sock, tls=None):
    def stream_reply(conn):
        try:
            with conn:
                conn.settimeout(3)
                if tls:
                    with tls.wrap_socket(conn, server_side=True) as stream:
                        stream.recv(4096)
                elif data := conn.recv(4096):
                    conn.sendall(data)
        except OSError:
            pass

    while True:
        try:
            if sock.type == socket.SOCK_DGRAM:
                data, sender = sock.recvfrom(4096)
                sock.sendto(data, sender)
            else:
                conn, _ = sock.accept()
                threading.Thread(target=stream_reply, args=(conn,), daemon=True).start()
        except OSError:
            return


def listener(stack, kind=socket.SOCK_STREAM, port=0):
    sock = stack.enter_context(socket.socket(socket.AF_INET, kind))
    sock.bind((HOST, port))
    if kind == socket.SOCK_STREAM:
        sock.listen()
    return sock


def free_port(taken, adjacent=False):
    for _ in range(50):
        with ExitStack() as held:
            primary = listener(held)
            port = primary.getsockname()[1]
            probes = {port, port + 1} if adjacent else {port}
            if probes & taken or max(probes) > 65535:
                continue
            try:
                for candidate in probes:
                    listener(held, socket.SOCK_DGRAM, candidate)
            except OSError:
                continue
            taken.update(probes)
            return port
    raise AssertionError('Unable to reserve an isolated port')


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)


def start_client(stack, root, core, uri, target, port, cert, binaries, *, server=HOST):
    if uri.startswith(('hysteria2://', 'tuic://')):
        proxy = singbox_outbound(uri)
        proxy['server'] = server
        proxy['tls']['certificate_path'] = str(cert)
        config = {'inbounds': [{'type': 'direct', 'listen': HOST, 'listen_port': port,
                                'override_address': HOST, 'override_port': target}], 'outbounds': [proxy]}
        binary = binaries['sing-box']
    else:
        proxy = outbound(uri)
        endpoint = (proxy['settings'].get('vnext') or proxy['settings']['servers'])[0]
        endpoint['address'] = server
        config = {'inbounds': [{'protocol': 'dokodemo-door', 'listen': HOST, 'port': port,
                                'settings': {'address': HOST, 'port': target, 'network': 'tcp,udp'}}], 'outbounds': [proxy]}
        binary = binaries['xray-core']
        if core == 'xray-core':
            config['log'] = {'loglevel': 'debug'}
    path = root / f'{core}-client-{port}.json'
    path.write_text(json.dumps(config))
    log = stack.enter_context(path.with_suffix('.log').open('wb'))
    process = subprocess.Popen([binary, 'run', '-c', str(path)], stdout=log, stderr=log)
    stack.callback(stop, process)
    for _ in range(80):
        if process.poll() is not None:
            raise AssertionError(path.with_suffix('.log').read_text()[-1400:])
        try:
            with socket.create_connection((HOST, port), timeout=.1):
                return
        except OSError:
            time.sleep(.025)
    raise AssertionError(f'{core} client did not bind')


def round_trip(port):
    message = secrets.token_bytes(24)
    with socket.create_connection((HOST, port), timeout=5) as conn:
        conn.sendall(message)
        with conn.makefile('rb') as reply:
            assert reply.read(len(message)) == message, 'TCP payload did not return'
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as conn:
        conn.settimeout(5)
        conn.sendto(message, (HOST, port))
        assert conn.recv(1024) == message, 'UDP payload did not return'


def main():
    binaries = {core_id: os.environ.get(spec[1]) for core_id, spec in PUBLISHERS.items()}
    dual_stack = os.environ.get('HOPWORLD_TEST_DUAL_STACK') == '1'
    native_probes = os.environ.get('HOPWORLD_TEST_NATIVE_PROBES') == '1'
    light_probe = os.environ.get('HOPWORLD_TEST_LIGHT_PROBE')
    for core_id, binary in binaries.items():
        if not binary:
            raise SystemExit(f'Set {PUBLISHERS[core_id][1]} to the binary path')

    with TemporaryDirectory() as temporary, ExitStack() as stack:
        root = Path(temporary)
        cert, key = root / 'cert.pem', root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                        '-subj', f'/CN={DOMAIN}', '-addext', f'subjectAltName=DNS:{DOMAIN},IP:{HOST}',
                        '-addext', 'basicConstraints=critical,CA:FALSE',
                        '-keyout', str(key), '-out', str(cert)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.set_alpn_protocols(['h2', 'http/1.1'])
        context.load_cert_chain(cert, key)
        target = listener(stack)
        target_port = target.getsockname()[1]
        if native_probes:
            class ProbeTarget(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(204)
                    self.end_headers()

                def log_message(self, *_):
                    pass

            http = stack.enter_context(ThreadingHTTPServer((HOST, 0), ProbeTarget))
            threading.Thread(target=http.serve_forever, daemon=True).start()
            stack.callback(http.shutdown)
            https = stack.enter_context(ThreadingHTTPServer((HOST, 0), ProbeTarget))
            https.socket = context.wrap_socket(https.socket, server_side=True)
            threading.Thread(target=https.serve_forever, daemon=True).start()
            stack.callback(https.shutdown)
            verify_target = ssl.create_default_context(cafile=str(cert))
            stack.enter_context(patch.object(node_probe.ssl, 'create_default_context', return_value=verify_target))
            stack.enter_context(patch.dict(node_probe.TARGETS, {
                'cloudflare': ('Loopback HTTP', f'http://{HOST}:{http.server_port}/'),
                'google': ('Loopback HTTPS', f'https://{HOST}:{https.server_port}/'),
            }))
            original_probe_config = node_probe._probe_config

            def trust_test_certificate(link, core, port, directory):
                args, config = original_probe_config(link, core, port, directory)
                if core == 'sing-box':
                    config['outbounds'][0]['tls']['certificate_path'] = str(cert)
                elif core == 'hysteria2':
                    config['tls']['ca'] = str(cert)
                elif core == 'tuic-server':
                    config['relay']['certificates'] = [str(cert)]
                return args, config

            stack.enter_context(patch.object(node_probe, '_probe_config', side_effect=trust_test_certificate))
        datagrams = listener(stack, socket.SOCK_DGRAM, target_port)
        handshake = listener(stack)
        handshake_port = handshake.getsockname()[1]
        for sock, tls in ((target, None), (datagrams, None), (handshake, context)):
            threading.Thread(target=echo, args=(sock, tls), daemon=True).start()

        def local_reality(*args, **kwargs):
            config, uri = build_local_reality_node(*args, dest=f'{HOST}:{handshake_port}', server_name=DOMAIN, **kwargs)
            config['log'] = {'loglevel': 'debug'}
            return config, uri

        def isolated_publisher(core_id, *args, **kwargs):
            config, uris = render_publisher(core_id, *args, **kwargs)
            if core_id == 'xray-core':
                ports = (target_port, http.server_port, https.server_port) if native_probes else (target_port,)
                config['outbounds'][0]['settings'] = {'finalRules': [
                    {'action': 'allow', 'network': 'tcp,udp', 'ip': [f'{HOST}/32'], 'port': str(port)}
                    for port in ports
                ]}
            return config, uris

        stack.enter_context(patch('router.independent_core.build_local_reality_node', side_effect=local_reality))
        stack.enter_context(patch('router.server.render_publisher', side_effect=isolated_publisher))
        store = Store(root / 'router.db')
        stack.enter_context(patch.object(store, 'require_domain_binding'))
        store.register_domain(DOMAIN, 'dual' if dual_stack else 'ipv4')
        store.save_acme_settings({**store.acme_settings(), 'domain': DOMAIN, 'cert_path': str(cert), 'key_path': str(key)})
        for service in store.independent.values():
            stack.callback(service.stop)
        taken = {target_port, handshake_port}
        profiles = {}
        for core in ('hysteria2', 'sing-box', 'mihomo', 'shadowsocks-rust', 'tuic-server', 'xray-core'):
            profiles[core] = {'public_host': DOMAIN, 'port': free_port(taken, adjacent=core == 'sing-box'),
                              'public_listener': dual_stack, 'egress': 'direct', 'upstream_id': ''}
            if dual_stack:
                profiles[core]['address_family'] = 'dual'
            store.start_independent_core(core, profiles[core])
            assert not store.xray.running() and not store.singbox.running() and not store.hysteria.running()
            assert [name for name, instance in store.independent.items() if instance.running()] == [core]
            service = store.independent[core]
            for uri in service.links():
                if native_probes:
                    parts = urlsplit(uri)
                    auth = parts.netloc.rpartition('@')[0]
                    local_uri = parts._replace(netloc=f'{auth}@{HOST}:{parts.port}').geturl()
                    binary = core_client_binary(store.data_dir, core, binaries[core])
                    for site in ('cloudflare', 'google'):
                        result = node_probe.probe(local_uri, core=core, client_binary=binary, target=site)
                        assert result['status'] == 'ok', (core, parts.scheme, result)
                    print(f'{core}/{parts.scheme}: native client returned 204 over HTTP and HTTPS', flush=True)
                    if light_probe and core == 'xray-core':
                        result = node_probe.probe_light(local_uri, light_probe, target='cloudflare')
                        assert result['status'] == 'ok', result
                        print('xray-core/vless: lightweight REALITY client forwarded HTTP 204', flush=True)
                for address in (HOST, '::1') if dual_stack else (HOST,):
                    port = free_port(taken)
                    with ExitStack() as client:
                        start_client(client, root, core, uri, target_port, port, cert, binaries, server=address)
                        try:
                            round_trip(port)
                        except (AssertionError, OSError):
                            print(f'{core} failed at {address}', flush=True)
                            print((root / f'{core}-client-{port}.log').read_text()[-1500:], flush=True)
                            print(service.log_text()[-1500:], flush=True)
                            raise
            assert any(node.get('core_id') == core for node in store.nodes())
            print(f'{core}: authenticated TCP and UDP relayed', flush=True)
            store.stop_core(core)

        for core, profile in profiles.items():
            store.start_independent_core(core, profile)
        pids = {core: service._read_pid() for core, service in store.independent.items()}
        store.stop_core('xray-core')
        assert not store.independent['xray-core'].running()
        for core, service in store.independent.items():
            if core != 'xray-core':
                assert service.running() and service._read_pid() == pids[core]
        assert all(node.get('core_id') != 'xray-core' for node in store.nodes())
        print('Stopping Xray preserved all five other processes and their subscriptions', flush=True)

        for service in store.independent.values():
            service.stop()
        store.restore_independent_cores()
        assert not store.independent['xray-core'].running()
        assert all(service.running() for core, service in store.independent.items() if core != 'xray-core')
        print('Saved profiles restored only the five enabled cores', flush=True)


if __name__ == '__main__':
    main()
