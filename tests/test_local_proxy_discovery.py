import socket
import socketserver
import threading
import unittest

from router.local_proxy_discovery import discover


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        conn = self.request
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = conn.recv(1)
            if not chunk: return
            data += chunk
        target = int(data.split(b' ', 2)[1].rsplit(b':', 1)[1])
        with socket.create_connection(('127.0.0.1', target)) as downstream:
            conn.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            downstream.sendall(conn.recv(24))


class SocksHandler(socketserver.BaseRequestHandler):
    def handle(self):
        conn = self.request
        if conn.recv(3) != b'\x05\x01\x00': return
        conn.sendall(b'\x05\x00')
        data = b''
        while len(data) < 10:
            chunk = conn.recv(10 - len(data))
            if not chunk: return
            data += chunk
        if data[1] != 1:
            conn.sendall(b'\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00')
            return
        with socket.create_connection(('127.0.0.1', int.from_bytes(data[-2:], 'big'))) as downstream:
            conn.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
            downstream.sendall(conn.recv(24))


class SocksUDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        conn = self.request
        if conn.recv(3) != b'\x05\x01\x00': return
        conn.sendall(b'\x05\x00')
        data = b''
        while len(data) < 10:
            chunk = conn.recv(10 - len(data))
            if not chunk: return
            data += chunk
        if data[1] == 1:
            with socket.create_connection(('127.0.0.1', int.from_bytes(data[-2:], 'big'))) as downstream:
                conn.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
                downstream.sendall(conn.recv(24))
        elif data[1] == 3:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as relay:
                relay.bind(('127.0.0.1', 0))
                relay.settimeout(2)
                conn.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01' + relay.getsockname()[1].to_bytes(2, 'big'))
                packet, client = relay.recvfrom(1024)
                relay.sendto(packet[10:], ('127.0.0.1', int.from_bytes(packet[8:10], 'big')))
                response, _ = relay.recvfrom(1024)
                relay.sendto(packet[:10] + response, client)


class DiscoveryTests(unittest.TestCase):
    def test_detects_actual_local_socks_proxy(self):
        with socketserver.ThreadingTCPServer(('127.0.0.1', 0), SocksHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assertEqual(discover([server.server_address[1]], timeout=1), [{'protocol': 'socks', 'port': server.server_address[1], 'tcp': 'supported', 'udp': 'unknown'}])
            finally:
                server.shutdown()
                thread.join()
    def test_detects_actual_local_http_proxy_without_external_request(self):
        with socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assertEqual(discover([server.server_address[1]], timeout=1), [{'protocol': 'http', 'port': server.server_address[1], 'tcp': 'supported', 'udp': 'unsupported'}])
            finally:
                server.shutdown()
                thread.join()

    def test_socks_udp_round_trip(self):
        with socketserver.ThreadingTCPServer(('127.0.0.1', 0), SocksUDPHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assertEqual(discover([server.server_address[1]], timeout=1), [{'protocol': 'socks', 'port': server.server_address[1], 'tcp': 'supported', 'udp': 'supported'}])
            finally:
                server.shutdown()
                thread.join()

    def test_rejects_unbounded_or_non_integer_ports(self):
        for ports in ([0], [True], [65536], list(range(1, 22))):
            with self.subTest(ports=ports), self.assertRaises(ValueError):
                discover(ports)
