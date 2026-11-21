"""Test the Linux M6 gateway with an independent LAN client and WAN observer."""

from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import ssl
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time

from e2e_independent_cores import echo, stop
from e2e_network_security import DoH, QUERY_TYPES, command, run_core
from router.egress_guard import IP, NFT, TABLE, change_rules
from router.gateway_namespace import worker_authority, worker_command
from router.linux_gateway import Gateway
from router.security_dns import require_sing_box_version


LAN4, LAN6 = '198.18.42.', 'fd62:93f2:42::'
TRANSIT4, TRANSIT6 = '198.18.43.', 'fd62:93f2:43::'
WAN4, WAN6 = '198.18.44.', 'fd62:93f2:44::'
RESOLVER_NAME = 'resolver.lab.test'
STUN_COOKIE = bytes.fromhex('2112a442')


def xor_address(address, mask):
    return bytes(left ^ right for left, right in zip(address, mask))


def stun_reply(listener):
    while True:
        try:
            request, remote = listener.recvfrom(512)
            if len(request) != 20 or request[:8] != b'\x00\x01\x00\x00' + STUN_COOKIE:
                continue
            family = 1 if listener.family == socket.AF_INET else 2
            address = socket.inet_pton(listener.family, remote[0])
            attr = struct.pack('!BBH', 0, family, remote[1] ^ 0x2112) + xor_address(address, request[4:])
            result = struct.pack('!HH', 0x0101, 4 + len(attr)) + request[4:] + struct.pack('!HH', 0x20, len(attr)) + attr
            listener.sendto(result, remote)
        except OSError:
            return


def ip_ns(namespace, *arguments):
    command([IP, 'netns', 'exec', namespace, IP, *arguments])


def pair(stack, left, left_if, left_ips, right, right_if, right_ips):
    left_temp, right_temp = ('mg' + secrets.token_hex(4) if name else interface
                             for name, interface in ((left, left_if), (right, right_if)))
    command([IP, 'link', 'add', left_temp, 'type', 'veth', 'peer', 'name', right_temp])
    directions = ((left, left_if, left_ips, left_temp), (right, right_if, right_ips, right_temp))
    for namespace, interface, addresses, temporary in directions:
        prefix = [IP, 'netns', 'exec', namespace] if namespace else []
        if namespace:
            command([IP, 'link', 'set', temporary, 'netns', namespace])
            command([*prefix, IP, 'link', 'set', temporary, 'name', interface])
        stack.callback(command, [*prefix, IP, 'link', 'set', interface, 'down'])
        command([*prefix, IP, 'addr', 'add', addresses[0], 'dev', interface])
        command([*prefix, IP, '-6', 'addr', 'add', addresses[1], 'dev', interface, 'nodad'])
        command([*prefix, IP, 'link', 'set', interface, 'up'])


def drive(operation, host, port):
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    if operation == 'stun':
        with socket.socket(family, socket.SOCK_DGRAM) as client:
            client.settimeout(3)
            mask = STUN_COOKIE + secrets.token_bytes(12)
            client.sendto(b'\x00\x01\x00\x00' + mask, (host, port))
            response = client.recv(512)
            assert response[:2] == b'\x01\x01' and response[4:20] == mask and response[20:22] == b'\x00\x20'
            seen_address = socket.inet_ntop(family, xor_address(response[28:], mask))
            assert seen_address == host, seen_address
        return
    query_id = 0x1212 if family == socket.AF_INET else 0x1228
    qtype = 1 if family == socket.AF_INET else 28
    label = 'gateway' if operation == 'dns' else 'outage-' + secrets.token_hex(4)
    question = bytes([len(label)]) + label.encode() + b'\x03lab\x04test\0' + struct.pack('!HH', qtype, 1)
    query = struct.pack('!6H', query_id, 0x100, 1, 0, 0, 0) + question
    for transport in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        with socket.socket(family, transport) as client:
            failing = operation in ('blocked', 'dns-fail')
            client.settimeout(1 if failing else 3)
            try:
                client.connect((host, port))
                if operation in ('dns', 'dns-fail'):
                    if transport == socket.SOCK_STREAM:
                        client.sendall(struct.pack('!H', len(query)) + query)
                        with client.makefile('rb') as stream:
                            size = stream.read(2)
                            reply = stream.read(struct.unpack('!H', size)[0]) if len(size) == 2 else b''
                    else:
                        client.send(query)
                        reply = client.recv(4096)
                    success = reply[:2] == query[:2] and reply[3] & 0x0F == 0 and reply[6:8] == b'\x00\x01'
                    assert success != failing, reply
                else:
                    payload = b'gateway-' + secrets.token_bytes(10)
                    client.sendall(payload)
                    reply = client.recv(32)
                    assert reply == (b'' if operation == 'blocked' else payload), reply
            except OSError:
                if not failing:
                    raise


def exercise(namespace, operation, host, port):
    command([IP, 'netns', 'exec', namespace, sys.executable, __file__, operation, host, str(port)])


def start_gateway(stack, command, env, log):
    with log.open('wb') as logs:
        runner = subprocess.Popen(command, env=env, stdout=logs, stderr=logs)
    stack.callback(stop, runner)
    deadline = time.monotonic() + 10
    while runner.poll() is None and time.monotonic() < deadline:
        if 'ACTIVE_UNVERIFIED' in log.read_text():
            return runner
        time.sleep(.1)
    raise AssertionError(log.read_text())


def gateway_pid(namespace):
    processes = command([IP, 'netns', 'pids', namespace], capture_output=True, text=True).stdout.split()
    assert len(processes) == 1, processes
    return int(processes[0])


def main():
    assert os.geteuid() == 0
    binary = str(Path(os.environ['HOPWORLD_SINGBOX']).resolve())
    require_sing_box_version(binary)
    suffix = secrets.token_hex(3)
    worker, edge, client = (f'hwr-sec-{label}{suffix}' for label in ('gw', 'ed', 'lan'))
    wan_if = 'm6w' + suffix
    socks_port = 20000 + secrets.randbelow(12000)
    target_port = socks_port + 1
    stun_port = socks_port + 2
    underlay = os.environ.get('HOPWORLD_TEST_GATEWAY_UNDERLAY', '4')
    upstream_ip, edge_ip, transit_ip = (WAN6+'1', WAN6+'2', TRANSIT6+'2') if underlay == '6' else (
        WAN4+'1', WAN4+'2', TRANSIT4+'2')
    design = dict(worker=worker, edge=edge, lan='lan0', worker_transit='uplink0', edge_transit='transit0', uplink='wan0',
                  lan_networks=[LAN4+'0/24', LAN6+'/64'], transit_ip=transit_ip, worker_uid=61077, worker_gid=61077,
                  upstream=dict(address=upstream_ip, port=socks_port),
                  resolver=dict(name='VM resolver', protocol='doh', address=WAN4+'1', port=443,
                                server_name=RESOLVER_NAME, path='/dns-query'))
    with TemporaryDirectory(prefix='hwr-m6-') as directory, ExitStack() as stack:
        root = Path(directory)
        config = root / 'policy.json'
        config.write_text(json.dumps(design))
        launcher = [sys.executable, '-m', 'router.linux_gateway']
        args = ['--config', str(config), '--sing-box', binary]
        for namespace in (worker, edge, client):
            command([*launcher, 'create-worker', *args] if namespace == worker else [IP, 'netns', 'add', namespace])
            stack.callback(command, [IP, 'netns', 'del', namespace])
            ip_ns(namespace, 'link', 'set', 'lo', 'up')
        stack.callback(command, [IP, 'link', 'del', wan_if])
        pair(stack, '', wan_if, (WAN4+'1/30', WAN6+'1/64'), edge, 'wan0', (WAN4+'2/30', WAN6+'2/64'))
        pair(stack, worker, 'uplink0', (TRANSIT4+'2/30', TRANSIT6+'2/64'),
             edge, 'transit0', (TRANSIT4+'1/30', TRANSIT6+'1/64'))
        pair(stack, worker, 'lan0', (LAN4+'1/24', LAN6+'1/64'), client, 'eth0', (LAN4+'2/24', LAN6+'2/64'))
        for namespace in (worker, edge):
            command([IP, 'netns', 'exec', namespace, '/usr/sbin/sysctl', '-qw',
                     'net.ipv4.ip_forward=1', 'net.ipv6.conf.all.forwarding=1',
                     'net.ipv4.conf.all.rp_filter=0', 'net.ipv4.conf.default.rp_filter=0'])
        for family, lan, transit in (('-4', LAN4, TRANSIT4), ('-6', LAN6, TRANSIT6)):
            ip_ns(client, family, 'route', 'add', 'default', 'via', lan+'1', 'dev', 'eth0')
            ip_ns(worker, family, 'route', 'add', 'default', 'via', transit+'1', 'dev', 'uplink0')

        cert, key = root / 'cert.pem', root / 'key.pem'
        command(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                 '-keyout', str(key), '-out', str(cert), '-subj', f'/CN={RESOLVER_NAME}',
                 '-addext', f'subjectAltName=DNS:{RESOLVER_NAME}'], capture_output=True)
        root.chmod(0o755)
        env = {**os.environ, 'SSL_CERT_FILE': str(cert)}
        resolver = stack.enter_context(ThreadingHTTPServer((WAN4+'1', 0), DoH))
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        resolver.socket = tls.wrap_socket(resolver.socket, server_side=True)
        threading.Thread(target=resolver.serve_forever, daemon=True).start()
        stack.callback(resolver.shutdown)
        for family, host in ((socket.AF_INET, WAN4+'1'), (socket.AF_INET6, WAN6+'1')):
            for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                server = stack.enter_context(socket.socket(family, kind))
                server.bind((host, target_port))
                if kind == socket.SOCK_STREAM:
                    server.listen()
                threading.Thread(target=echo, args=(server,), daemon=True).start()
            stun = stack.enter_context(socket.socket(family, socket.SOCK_DGRAM))
            stun.bind((host, stun_port))
            threading.Thread(target=stun_reply, args=(stun,), daemon=True).start()
        upstream = run_core(stack, root, [], binary, 'upstream', {
            'inbounds': [{'type': 'socks', 'listen': upstream_ip, 'listen_port': socks_port}],
            'outbounds': [{'type': 'direct'}],
        })
        design['resolver']['port'] = resolver.server_port
        config.write_text(json.dumps(design))
        gateway = Gateway.from_json(design)
        command([*launcher, 'check', *args], env=env)
        with worker_authority(gateway) as authority:
            for attempt in (
                ['/usr/bin/nsenter', f'--net=/run/netns/{edge}', NFT, 'list', 'ruleset'],
                [IP, 'link', 'set', 'dev', 'uplink0', 'netns', edge],
            ):
                result = subprocess.run(worker_command(gateway, authority, attempt, net_admin=True),
                                        pass_fds=(authority,), capture_output=True, timeout=6)
                assert result.returncode != 0, 'Worker capability escaped its user namespace'
        print('PASS: TUN worker cannot enter the independent firewall or move links into it', flush=True)
        capture_path = root / 'gateway.pcap'
        capture = subprocess.Popen(['tcpdump', '-Q', 'in', '-U', '-n', '-i', wan_if, '-w', str(capture_path)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stack.callback(stop, capture)
        log = root / 'gateway.log'
        runner = start_gateway(stack, [*launcher, 'run', *args], env, log)
        for host in (WAN4+'1', WAN6+'1'):
            exercise(client, 'dns', host, 53)
            exercise(client, 'echo', host, target_port)
            exercise(client, 'stun', host, stun_port)
        assert set(QUERY_TYPES) >= {1, 28}
        print(f'PASS: IPv{underlay} SOCKS underlay, LAN TCP/UDP 53, dual-stack TCP/UDP and STUN', flush=True)
        stop(upstream)
        for host in (WAN4+'1', WAN6+'1'):
            exercise(client, 'blocked', host, target_port)
            exercise(client, 'dns-fail', host, 53)
        command([IP, 'netns', 'exec', worker, NFT, 'delete', 'table', 'inet', TABLE])
        for host in (WAN4+'1', WAN6+'1'):
            exercise(worker, 'blocked', host, target_port)
        os.kill(gateway_pid(worker), signal.SIGKILL)
        assert runner.wait(timeout=8) != 0, log.read_text()
        assert 'accept' not in change_rules('status', edge)
        command([*launcher, 'check', *args], env=env)
        print('PASS: upstream outage, inner rule removal, and worker crash all fail closed', flush=True)

        restarted = start_gateway(stack, [*launcher, 'run', *args], env, root / 'restarted.log')
        child = gateway_pid(worker)
        restarted.kill()
        restarted.wait(timeout=5)
        for _ in range(40):
            pending = command([IP, 'netns', 'pids', worker], capture_output=True, text=True).stdout.split()
            if str(child) not in pending:
                break
            time.sleep(.05)
        else:
            os.kill(child, signal.SIGKILL)
            raise AssertionError('TUN worker survived the supervisor crash')
        for namespace in (edge, worker):
            change_rules('block', namespace)
        assert 'accept' not in change_rules('status', edge)
        print('PASS: supervisor crash terminated TUN without reopening any direct path', flush=True)
        stop(capture)
        traffic = command(['tcpdump', '-nn', '-r', str(capture_path),
                           f'(tcp or udp) and not (src host {edge_ip} and dst host {upstream_ip} and tcp dst port {socks_port})'],
                          capture_output=True, text=True)
        assert not traffic.stdout, traffic.stdout
        if target := os.environ.get('HOPWORLD_SECURITY_CAPTURE'):
            Path(target).write_bytes(capture_path.read_bytes())


if __name__ == '__main__':
    if len(sys.argv) == 4:
        drive(sys.argv[1], sys.argv[2], int(sys.argv[3]))
    else:
        main()
