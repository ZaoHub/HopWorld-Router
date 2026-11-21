from contextlib import contextmanager
import json
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import paramiko

from installer import INSTALL, Installer


class TestSSHServer(paramiko.ServerInterface):
    def __init__(self):
        self.command = None
        self.received = threading.Event()
        self.authenticated = False

    def check_auth_password(self, username, password):
        self.authenticated = username == "root" and password == "temporary-test"
        return paramiko.AUTH_SUCCESSFUL if self.authenticated else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, _channel_id):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, _channel, command):
        self.command = command.decode()
        self.received.set()
        return True


@contextmanager
def test_ssh(exit_code=0):
    key = paramiko.RSAKey.generate(2048)
    server = TestSSHServer()
    with socket.create_server(("127.0.0.1", 0)) as listener:
        listener.settimeout(5)

        def serve():
            connection, _ = listener.accept()
            with paramiko.Transport(connection) as transport:
                transport.add_server_key(key)
                try:
                    transport.start_server(server=server)
                    channel = transport.accept(3)
                    if channel and server.received.wait(3):
                        channel.sendall(b"Linux installer output\n")
                        channel.send_exit_status(exit_code)
                        channel.close()
                except (EOFError, OSError, paramiko.SSHException):
                    pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], key.fingerprint, server
        finally:
            thread.join(5)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Installer)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def install(self, port, fingerprint, origin=None):
        target = {"host": "127.0.0.1", "port": port, "username": "root", "password": "temporary-test",
                  "method": "password", "fingerprint": fingerprint}
        request = Request(self.base + "/api/install", json.dumps(target).encode(),
                          {"Origin": origin or self.base, "Content-Type": "application/json"})
        with urlopen(request, timeout=12) as response:
            return [json.loads(event) for event in response]

    def test_streams_output_and_reports_real_exit_status(self):
        for exit_code, expected in ((0, "success"), (37, "error")):
            with self.subTest(exit_code=exit_code), test_ssh(exit_code) as (port, fingerprint, remote):
                events = self.install(port, fingerprint)
                self.assertEqual(events[-1]["type"], expected)
                self.assertTrue(any(event["type"] == "output" for event in events))
                self.assertIn(INSTALL, remote.command)
                self.assertNotIn("temporary-test", json.dumps(events))

    def test_rejects_wrong_fingerprint_before_password_authentication(self):
        with test_ssh() as (port, fingerprint, remote):
            mismatch = "SHA256:" + ("A" if fingerprint[7] != "A" else "B") + fingerprint[8:]
            events = self.install(port, mismatch)
            self.assertEqual(events[-1]["type"], "error")
            self.assertIn("主机指纹不匹配", events[-1]["text"])
            self.assertFalse(remote.authenticated)
            self.assertIsNone(remote.command)

    def test_rejects_cross_origin_install(self):
        with self.assertRaises(HTTPError) as rejected:
            self.install(22, "SHA256:" + "A" * 43, origin="https://example.com")
        self.assertEqual(rejected.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
