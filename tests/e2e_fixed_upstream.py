"""Local TCP/UDP check through a real Xray Reality server and fixed SOCKS5 upstream.

Run with HOPWORLD_TEST_XRAY=/absolute/path/to/xray python -m tests.e2e_fixed_upstream.
Set HOPWORLD_TEST_SINGBOX to also check Hysteria 2 and TUIC using sing-box clients.
Set HOPWORLD_TEST_HYSTERIA to additionally check the native Hysteria 2 server.
All listeners are on loopback and data is temporary.
"""
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import secrets
import socket
import ssl
import subprocess as sp
import threading
import time
from unittest.mock import patch
from router import probe as node_probe
from router.local_node import build_native_hysteria, build_quic_links, build_singbox_config, native_hysteria_port, quic_ports
from router.hysteria_service import HysteriaService
from router.probe import outbound, singbox_outbound
from router.xray_service import XrayService

binary = os.environ.get('HOPWORLD_TEST_XRAY')
if not binary:
    raise SystemExit('Set HOPWORLD_TEST_XRAY to the Xray binary path')
singbox = os.environ.get('HOPWORLD_TEST_SINGBOX')
hysteria = os.environ.get('HOPWORLD_TEST_HYSTERIA')
if hysteria and not singbox:
    raise SystemExit('Set HOPWORLD_TEST_SINGBOX for the Hysteria 2 client')
host = '127.0.0.1'
def stop(p):
    if p.poll() is None:
        p.terminate()
        p.wait(4)

def wait_tcp(port):
    for _ in range(40):
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f'Port {port} did not start')

def echo_tcp(sock):
    while True:
        try:
            c, _ = sock.accept()
            with c:
                d = c.recv(1024)
                if d:
                    c.sendall(d)
        except OSError:
            return

def echo_udp(sock):
    while True:
        try:
            d, peer = sock.recvfrom(1024)
            sock.sendto(d, peer)
        except OSError:
            return

with TemporaryDirectory() as temp, ExitStack() as stack:
    root = Path(temp)
    sockets = [socket.socket() for _ in range(4)]
    try:
        for reserved in sockets:
            reserved.bind((host, 0))
        client_port, upstream_port, tls_port, relay_port = [s.getsockname()[1] for s in sockets]
    finally:
        for reserved in sockets:
            reserved.close()
    target = stack.enter_context(socket.socket())
    target.bind((host, 0))
    target.listen()
    target_port = target.getsockname()[1]
    udp = stack.enter_context(socket.socket(socket.AF_INET,socket.SOCK_DGRAM))
    udp.bind((host, target_port))
    forbidden = {client_port, upstream_port, tls_port, relay_port, target_port}
    for _ in range(40):
        with ExitStack() as reservation:
            tcp = reservation.enter_context(socket.socket())
            tcp.bind((host, 0))
            server_port = tcp.getsockname()[1]
            ports = {*quic_ports(server_port), native_hysteria_port(server_port)}
            if forbidden.intersection(ports | {server_port}):
                continue
            try:
                for port in ports:
                    reservation.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)).bind((host, port))
            except OSError:
                continue
            break
    else:
        raise AssertionError('Could not allocate isolated listener ports')
    threading.Thread(target=echo_tcp, args=(target,), daemon=True).start()
    threading.Thread(target=echo_udp, args=(udp,), daemon=True).start()
    sp.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=example.test',
            '-addext', 'subjectAltName=DNS:example.test,IP:127.0.0.1',
            '-keyout', str(root/'key.pem'), '-out', str(root/'cert.pem')], check=True, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    tls_listener = stack.enter_context(socket.socket())
    tls_listener.bind((host, tls_port))
    tls_listener.listen()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(['h2', 'http/1.1'])
    context.load_cert_chain(root/'cert.pem', root/'key.pem')
    def accept_tls():
        while True:
            try:
                c, _ = tls_listener.accept()
            except OSError:
                return
            def tls_handshake(c):
                try:
                    c.settimeout(3)
                    with context.wrap_socket(c, server_side=True) as tls:
                        tls.recv(1024)
                except OSError:
                    c.close()
            threading.Thread(target=tls_handshake, args=(c,), daemon=True).start()
    threading.Thread(target=accept_tls, daemon=True).start()

    logs = {}
    def start(name, config, listen=None, *, core=binary, command=('run',)):
        path = root / (name+'.json')
        if core == binary:
            config['log'] = {'loglevel': 'warning'}
        path.write_text(json.dumps(config))
        log = root / (name+'.log')
        f = stack.enter_context(log.open('w+'))
        logs[name] = f
        p = sp.Popen([core, *command, '-c', str(path)], stdout=f, stderr=f)
        stack.callback(stop, p)
        if listen is not None:
            wait_tcp(listen)
        else:
            try:
                p.wait(timeout=.3)
            except sp.TimeoutExpired:
                return p
            f.seek(0)
            raise AssertionError(f'{name} exited during startup: {f.read()[-1200:]}')
        return p

    upstream = start('upstream', {'inbounds':[{'listen':host,'port':upstream_port,'protocol':'socks','settings':{'auth':'noauth','udp':True}}],
                                  'outbounds':[{'protocol':'freedom'}]}, upstream_port)
    service = XrayService(root, binary=binary)
    settings = service.ensure_credentials(service.default_settings(host))
    settings.update(enabled=True, port=server_port, dest=f'{host}:{tls_port}', server_name='example.test',
                    proxy_pool=[{'protocol':'socks','settings':{'servers':[{'address':host,'port':upstream_port}]}}])
    original_build = service.build

    def loopback_build(values):
        config, link = original_build(values)
        config['inbounds'][0]['listen'] = host
        next(item for item in config['inbounds'] if item.get('tag') == 'local-socks-in')['port'] = relay_port
        return config, link

    stack.enter_context(patch.object(service, 'build', side_effect=loopback_build))
    stack.callback(service.stop)
    link = service.apply(settings)
    start('client', {'inbounds':[{'listen':host,'port':client_port,'protocol':'dokodemo-door',
                                 'settings':{'address':host,'port':target_port,'network':'tcp,udp'}}],
                     'outbounds':[outbound(link)]}, client_port)
    def tcp_round_trip(port, marker, timeout=4):
        with socket.create_connection((host, port), timeout=timeout) as client:
            client.sendall(marker)
            with client.makefile('rb') as stream:
                return stream.read(len(marker))

    def udp_round_trip(port, marker):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(3)
            client.sendto(marker, (host, port))
            return client.recv(1024)

    def quic_client(label, uri, cert):
        proxy = singbox_outbound(uri)
        proxy['tls']['certificate_path'] = cert
        with socket.socket() as reserve:
            reserve.bind((host, 0))
            port = reserve.getsockname()[1]
        start(label, {
            'inbounds': [{'type': 'direct', 'listen': host, 'listen_port': port,
                          'override_address': host, 'override_port': target_port}],
            'outbounds': [proxy],
        }, port, core=singbox)
        return port

    try:
        previous = service.config_path.read_bytes()
        with socket.socket() as occupied:
            occupied.bind((host, 0))
            occupied.listen()
            try:
                service.apply({**settings, 'port': occupied.getsockname()[1]})
            except ValueError as exc:
                assert '已恢复原有 Xray 服务' in str(exc), str(exc)
            else:
                raise AssertionError('Xray accepted an occupied TCP port')
        assert service.config_path.read_bytes() == previous
        print('Occupied TCP port rejected; previous Xray process restored', flush=True)

        clients = {'Reality': client_port}
        if singbox:
            cert, key = str(root / 'cert.pem'), str(root / 'key.pem')
            quic_args = (host, server_port, settings['uuid'], settings['hy2_password'], settings['tuic_password'])
            config = build_singbox_config(*quic_args, cert, key)
            for inbound in config['inbounds']:
                inbound['listen'] = host
            config['outbounds'][0]['server_port'] = relay_port
            start('singbox-router', config, core=singbox)
            hy2_link, tuic_link = build_quic_links(*quic_args)
            clients['sing-box Hysteria 2'] = quic_client('hy2-client', hy2_link, cert)
            clients['sing-box TUIC'] = quic_client('tuic-client', tuic_link, cert)

            class ProbeTarget(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(204)
                    self.end_headers()

                def log_message(self, *_args):
                    pass

            http = stack.enter_context(HTTPServer((host, 0), ProbeTarget))
            threading.Thread(target=http.serve_forever, daemon=True).start()
            stack.callback(http.shutdown)

            def trust_fixture(uri):
                config = singbox_outbound(uri)
                config['tls']['certificate_path'] = cert
                return config

            with patch.dict(node_probe.TARGETS, {'cloudflare': ('Local HTTP', f'http://{host}:{http.server_port}/')}), \
                 patch.object(node_probe, 'singbox_outbound', side_effect=trust_fixture):
                for uri in (hy2_link, tuic_link):
                    result = node_probe.probe(uri, singbox_binary=singbox, target='cloudflare')
                    assert result['status'] == 'ok', result
            print('Node test: real HTTP 204 through Hysteria 2 and TUIC', flush=True)
            if hysteria:
                native = HysteriaService(root, binary=hysteria)
                native_settings = {**settings, 'native_hysteria_enabled': True, 'native_hysteria_password': secrets.token_urlsafe(18)}

                def build_loopback_native(values, certificate, private_key):
                    config, uri = build_native_hysteria(values, certificate, private_key)
                    config['listen'] = config['listen'].replace('0.0.0.0', host, 1)
                    config['outbounds'][0]['socks5']['addr'] = f'{host}:{relay_port}'
                    return config, uri

                stack.enter_context(patch('router.hysteria_service.build_native_hysteria', side_effect=build_loopback_native))
                stack.callback(native.stop)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
                    occupied.bind((host, native_hysteria_port(server_port)))
                    try:
                        native.apply(native_settings, (cert, key))
                    except ValueError as exc:
                        assert 'UDP 端口占用' in str(exc), str(exc)
                    else:
                        raise AssertionError('Hysteria accepted an occupied UDP port')
                assert not native.link(native_settings)
                native.apply(native_settings, (cert, key))
                clients['native Hysteria 2'] = quic_client('native-hy2-client', native.link(native_settings), cert)

        for label, port in clients.items():
            marker = secrets.token_bytes(24)
            assert tcp_round_trip(port, marker) == marker, f'{label}: TCP round-trip failed'
            assert udp_round_trip(port, marker) == marker, f'{label}: UDP round-trip failed'
            print(f'{label}: TCP and UDP payloads passed', flush=True)
        stats=service.node_traffic()
        assert stats['up_bytes']>0 and stats['down_bytes']>0, stats
        print('Real Xray counters:', stats['up_bytes'], stats['down_bytes'], flush=True)
        if hysteria:
            native.stop()
            assert not native.link(native_settings)
            assert udp_round_trip(clients['sing-box Hysteria 2'], marker) == marker
            assert udp_round_trip(clients['sing-box TUIC'], marker) == marker
            native.apply(native_settings, (cert, key))
            clients['native Hysteria 2'] = quic_client('native-hy2-new-client', native.link(native_settings), cert)
            assert tcp_round_trip(clients['native Hysteria 2'], marker) == marker
            print('Native Hysteria 2 stopped and started independently of sing-box', flush=True)
        stop(upstream)
        for label, port in clients.items():
            marker = secrets.token_bytes(24)
            try:
                assert not tcp_round_trip(port, marker, timeout=2), f'{label}: TCP fell back to direct'
            except (TimeoutError, ConnectionResetError):
                pass
            try:
                udp_round_trip(port, marker)
            except TimeoutError:
                pass
            else:
                raise AssertionError(f'{label}: UDP fell back to direct')
            print(f'{label}: TCP and UDP fail closed after upstream stop', flush=True)
    except Exception:
        print('router', service.log_text()[-1200:])
        for name, log in logs.items():
            log.seek(0)
            print(name, log.read()[-1200:])
        raise
