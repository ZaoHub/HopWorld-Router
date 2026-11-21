"""Isolated Linux acceptance for the nftables guard and sing-box DNS/UDP.

Run as root with HOPWORLD_SINGBOX set to the pinned binary. Creates a temporary
veth pair and namespace. No host route, DNS or firewall rules are changed.
"""

from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
from socketserver import StreamRequestHandler, ThreadingTCPServer
import ssl
import struct
import subprocess
from tempfile import TemporaryDirectory
import threading
import time
from unittest.mock import patch

from e2e_independent_cores import echo, free_port, round_trip, stop
from router.egress_guard import change_rules
from router.independent_core import profile_credentials, render_publisher
from router.probe import singbox_outbound
from router.server import Store
from router.security_dns import build_secure_dns_config, require_sing_box_version
from router.security_probe import dns_answer
from router.subscription import upstream_node_id


WAN4, PROXY4 = '198.18.30.1', '198.18.30.2'
WAN6, PROXY6 = 'fd62:93f2:30::1', 'fd62:93f2:30::2'
NAME = 'resolver.lab.test'
QUERY_TYPES = []


def dns_response(query):
    end = query.index(b'\0', 12) + 5
    qtype = struct.unpack('!H', query[end - 4:end - 2])[0]
    QUERY_TYPES.append(qtype)
    address = socket.inet_pton(socket.AF_INET if qtype == 1 else socket.AF_INET6, WAN4 if qtype == 1 else WAN6)
    return (query[:2] + struct.pack('!5H', 0x8180, 1, 1, 0, 0) + query[12:end] +
            struct.pack('!3HIH', 0xc00c, qtype, 1, 30, len(address)) + address)


class DoH(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        query = self.rfile.read(int(self.headers['Content-Length']))
        response = dns_response(query)
        self.send_response(200)
        self.send_header('Content-Type', 'application/dns-message')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def do_GET(self):
        body = {'/ip4': b'93.184.215.14', '/ip6': b'2001:4860::14'}.get(self.path)
        if not body:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DoT(StreamRequestHandler):
    def handle(self):
        while prefix := self.rfile.read(2):
            query = self.rfile.read(struct.unpack('!H', prefix)[0])
            response = dns_response(query)
            self.wfile.write(struct.pack('!H', len(response)) + response)


def command(args, **kwargs):
    return subprocess.run(args, check=True, timeout=12, **kwargs)


def run_core(stack, root, command_prefix, binary, name, config, *, log=None, env=None):
    path = root / (name + '.json')
    path.write_text(json.dumps(config))
    with stack.enter_context((root / (name + '.log')).open('wb')) as default_log:
        process = subprocess.Popen([*command_prefix, binary, 'run', '-c', str(path)],
                                   stdout=log or default_log, stderr=subprocess.STDOUT, env=env)
        stack.callback(stop, process)
        time.sleep(.22)
        assert process.poll() is None, (root / (name + '.log')).read_text()
    return process


def start_local_client(stack, root, binary, link, cert, name, port, *, destination=None, host=PROXY4):
    proxy = singbox_outbound(link)
    proxy['server'] = host
    proxy['tls']['certificate_path'] = str(cert)
    inbound = {'type': 'http', 'listen': '127.0.0.1', 'listen_port': port}
    if destination:
        inbound.update(type='direct', override_address=destination[0], override_port=destination[1])
    return run_core(stack, root, [], binary, name, {'inbounds': [inbound], 'outbounds': [proxy]})


def blocked(call):
    try:
        call()
    except (OSError, ValueError):
        return
    raise AssertionError('Expected the protected operation to fail')


def no_echo(port):
    with socket.create_connection(('127.0.0.1', port), timeout=3) as tcp:
        tcp.sendall(b'tcp-outage')
        try:
            reply = tcp.recv(16)
        except OSError:
            reply = b''
        assert not reply
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(1)
        udp.sendto(b'udp-outage', ('127.0.0.1', port))
        blocked(lambda: udp.recv(16))


def router_round_trip(stack, root, port, host, socks_port, dot_port, observer, cert, key):
    store = Store(root / 'router-test.db')
    runtime = store.publisher_runtime('sing-box')
    stack.callback(runtime.stop)
    uri = f'socks5://{host}:{socks_port}'
    store.add_manual(uri)
    upstream_id = upstream_node_id(uri)
    profile = {'enabled': True, 'egress': 'fixed', 'upstream_id': upstream_id, 'port': port,
               'public_host': NAME, 'public_listener': False, 'address_family': 'ipv4'}
    profile_credentials(profile)
    store._save_independent_profiles({'sing-box': profile})
    resolver = store.security.add_resolver({'name': 'Test DoT', 'protocol': 'dot', 'address': host,
                                           'port': dot_port, 'server_name': NAME, 'path': ''})
    store.security.save_policy('sing-box', {'mode': 'compatibility', 'upstream_id': upstream_id,
                                           'profile_id': 'sing-box', 'resolver_id': resolver['id']})
    stack.enter_context(patch.object(store, '_quic_certificate', return_value=(str(cert), str(key))))
    stack.enter_context(patch.dict(os.environ, {'SSL_CERT_FILE': str(cert)}))
    for family in (4, 6):
        stack.enter_context(patch(f'router.security_probe.IPV{family}_ENDPOINTS',
                                  (f'https://{NAME}:{observer}/ip{family}',)))
    assert store.security.validate('sing-box')['status'] == 'PASS'

    def job(action):
        identifier = store.security.start_job('sing-box', action)['id']
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = store.security.job(identifier)
            if result['finished_at'] is not None:
                return result
            time.sleep(.05)
        raise AssertionError(f'Timed out waiting for {action}')

    assert (applied := job('apply'))['status'] == 'PASS', applied
    report = job('test')
    assert report['status'] == 'INCONCLUSIVE', report
    findings = {item['code']: item for item in report['findings']}
    assert findings['DNS_A']['status'] == findings['DNS_AAAA']['status'] == 'PASS', report
    assert '93.184.215.14' in findings['IPV4']['detail'], report
    assert '2001:4860::14' in findings['IPV6']['detail'], report
    runtime.stop()
    store.restore_independent_cores()
    assert runtime.security_revision() == 1, runtime._error
    assert (rolled_back := job('rollback'))['status'] == 'PASS', rolled_back
    assert runtime.running() and runtime.security_revision() is None
    runtime.stop()
    print('PASS: Router apply, DoT A/AAAA and exit observations, restart restore, and rollback', flush=True)


def main():
    assert os.geteuid() == 0, 'use a disposable Linux test host'
    binary = str(Path(os.environ['HOPWORLD_SINGBOX']).resolve())
    require_sing_box_version(binary)
    token = secrets.token_hex(3)
    namespace, wan, worker = 'hwr-sec-' + token, 'hw-w' + token, 'hw-p' + token
    ip = '/usr/sbin/ip'
    inside = [ip, 'netns', 'exec', namespace]
    with ExitStack() as stack, TemporaryDirectory(prefix='hwr-security-e2e-') as directory:
        root = Path(directory)
        root.chmod(0o755)
        command([ip, 'netns', 'add', namespace])
        stack.callback(command, [ip, 'netns', 'del', namespace])
        command([ip, 'link', 'add', wan, 'type', 'veth', 'peer', 'name', worker])
        stack.callback(command, [ip, 'link', 'del', wan])
        command([ip, 'link', 'set', worker, 'netns', namespace])
        command([ip, 'addr', 'add', WAN4 + '/24', 'dev', wan])
        command([ip, '-6', 'addr', 'add', WAN6 + '/64', 'dev', wan, 'nodad'])
        command([ip, 'link', 'set', wan, 'up'])
        command([*inside, ip, 'addr', 'add', PROXY4 + '/24', 'dev', worker])
        command([*inside, ip, '-6', 'addr', 'add', PROXY6 + '/64', 'dev', worker, 'nodad'])
        command([*inside, ip, 'link', 'set', worker, 'up'])
        command([*inside, ip, 'link', 'set', 'lo', 'up'])
        port = free_port(set(), adjacent=True)
        socks_port = free_port({port, port + 1})
        host_port = free_port({port, port + 1, socks_port})
        guard = ('apply', namespace, worker, WAN4, socks_port, (port, port + 1))
        print(change_rules('check', *guard[1:]), flush=True)
        change_rules(*guard)

        captured = root / 'wan.pcap'
        tcpdump = subprocess.Popen(['tcpdump', '-U', '-n', '-i', wan, '-w', str(captured)],
                                   stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        stack.callback(stop, tcpdump)
        cert, key = root / 'cert.pem', root / 'key.pem'
        command(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                 '-keyout', str(key), '-out', str(cert), '-subj', f'/CN={NAME}', '-addext', f'subjectAltName=DNS:{NAME}'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chown(key, 65534, 65534)
        env = {**os.environ, 'SSL_CERT_FILE': str(cert)}
        dns = stack.enter_context(ThreadingHTTPServer((WAN4, 0), DoH))
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        dns.socket = tls.wrap_socket(dns.socket, server_side=True)
        threading.Thread(target=dns.serve_forever, daemon=True).start()
        stack.callback(dns.shutdown)
        dot = stack.enter_context(ThreadingTCPServer((WAN4, 0), DoT))
        dot.daemon_threads = True
        dot.socket = tls.wrap_socket(dot.socket, server_side=True)
        threading.Thread(target=dot.serve_forever, daemon=True).start()
        stack.callback(dot.shutdown)

        for family, address in ((socket.AF_INET, WAN4), (socket.AF_INET6, WAN6)):
            for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                server = stack.enter_context(socket.socket(family, kind))
                server.bind((address, host_port))
                if kind == socket.SOCK_STREAM:
                    server.listen()
                threading.Thread(target=echo, args=(server,), daemon=True).start()

        upstream = run_core(stack, root, [], binary, 'upstream', {
            'inbounds': [{'type': 'socks', 'listen': WAN4, 'listen_port': socks_port}],
            'outbounds': [{'type': 'direct'}],
        })
        profile = {'egress': 'fixed', 'public_host': NAME, 'port': port, 'public_listener': True, 'address_family': 'dual'}
        profile_credentials(profile)
        config, links = render_publisher('sing-box', profile, (str(cert), str(key)), {
            'protocol': 'socks', 'settings': {'servers': [{'address': WAN4, 'port': socks_port}]},
        })
        resolver = {'address': WAN4, 'port': dns.server_port, 'protocol': 'doh', 'server_name': NAME, 'path': '/dns-query'}
        sandbox = [*inside, '/usr/bin/setpriv', '--reuid=65534', '--regid=65534', '--clear-groups', '--no-new-privs']
        publisher = run_core(stack, root, sandbox, binary, 'publisher',
                             build_secure_dns_config(config, resolver, udp_over_tcp=True), env=env)
        taken = {port, port + 1, socks_port, host_port, dns.server_port, dot.server_address[1]}
        for index, address in enumerate((WAN4, WAN6)):
            client_port = free_port(taken)
            start_local_client(stack, root, binary, links[index], cert, f'echo-{index}', client_port,
                               destination=(address, host_port), host=PROXY4 if index == 0 else PROXY6)
            round_trip(client_port)
        dns_port = free_port(taken)
        start_local_client(stack, root, binary, links[0], cert, 'dns-client', dns_port)
        assert dns_answer(dns_port, 1) and dns_answer(dns_port, 28)
        udp_dns_port = free_port(taken)
        start_local_client(stack, root, binary, links[0], cert, 'udp-dns-client', udp_dns_port,
                           destination=('192.0.2.53', 53))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(4)
            query = bytes.fromhex('102001000001000000000000') + b'\x03udp\x03lab\x04test\0\0\x01\0\x01'
            client.sendto(query, ('127.0.0.1', udp_dns_port))
            reply = client.recv(2048)
            assert reply[:2] == query[:2] and reply[6:8] == b'\0\x01'
        assert set(QUERY_TYPES) == {1, 28}
        print('PASS: guard -> Hysteria 2/TUIC -> pinned SOCKS5 UoT; IPv4/IPv6 TCP+UDP echo and DoH A/AAAA', flush=True)

        for address in (WAN4, WAN6):
            for target_port in (host_port, 53, 853):
                script = ('import socket,sys; h,p,t=sys.argv[1:]; '
                          's=socket.socket(socket.AF_INET6 if ":" in h else socket.AF_INET, int(t)); '
                          's.settimeout(.25); s.connect((h,int(p))); '
                          's.send(b"x"); s.recv(4)')
                for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                    check = subprocess.run([*sandbox, '/usr/bin/python3', '-c', script,
                                            address, str(target_port), str(int(kind))],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                    assert check.returncode != 0, 'Non-authorized direct path worked'

        stop(publisher)
        bad_resolver = {**resolver, 'server_name': 'wrong.lab.test'}
        before_bad_tls = len(QUERY_TYPES)
        publisher = run_core(stack, root, sandbox, binary, 'bad-tls',
                             build_secure_dns_config(config, bad_resolver, udp_over_tcp=True), env=env)
        blocked(lambda: dns_answer(dns_port, 1, hostname='invalid-tls.lab.test'))
        assert len(QUERY_TYPES) == before_bad_tls, 'Wrong TLS identity reached the resolver application'
        stop(publisher)
        router_round_trip(stack, root, free_port(taken, adjacent=True), WAN4, socks_port,
                          dot.server_address[1], dns.server_port, cert, key)
        publisher = run_core(stack, root, sandbox, binary, 'outage',
                             build_secure_dns_config(config, resolver, udp_over_tcp=True), env=env)
        stop(upstream)
        blocked(lambda: dns_answer(dns_port, 1, hostname='outage.lab.test'))
        no_echo(client_port)
        stop(publisher)
        stop(tcpdump)
        packets = command(['tcpdump', '-nn', '-r', str(captured),
                           f'(src host {PROXY4} or src host {PROXY6}) and '
                           f'(tcp or udp) and not (dst host {WAN4} and tcp dst port {socks_port}) '
                           f'and not (udp src port {port} or udp src port {port + 1})'],
                          capture_output=True, text=True).stdout
        assert not packets, packets
        if destination := os.environ.get('HOPWORLD_SECURITY_CAPTURE'):
            shutil.copyfile(captured, destination)
        print('PASS: wrong TLS identity and upstream outage fail closed; WAN capture saw no unauthorized TCP/UDP', flush=True)

        try:
            change_rules('apply', namespace, 'wrong0', WAN4, socks_port, (port,))
        except ValueError:
            assert 'drop' in change_rules('status', namespace)
            assert 'accept' not in change_rules('status', namespace)
        else:
            raise AssertionError('Invalid route was accepted')
        print('PASS: failed apply left only the namespace default drop rules', flush=True)


if __name__ == '__main__':
    main()
