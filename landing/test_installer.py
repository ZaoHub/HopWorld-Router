from contextlib import contextmanager
import json
import socket
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from installer import INSTALL, WEB_ORIGIN, Installer, parse_private_key, public_ip


class TestSSHServer(paramiko.ServerInterface):
    def __init__(self, authorized_key=None):
        self.authorized_key = authorized_key
        self.command = None
        self.received = threading.Event()
        self.authenticated = False

    def check_auth_password(self, username, password):
        self.authenticated = username == "root" and password == "temporary-test"
        return paramiko.AUTH_SUCCESSFUL if self.authenticated else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        self.authenticated = username == "root" and key.fingerprint == self.authorized_key
        return paramiko.AUTH_SUCCESSFUL if self.authenticated else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, _channel_id):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, _channel, command):
        self.command = command.decode()
        self.received.set()
        return True


@contextmanager
def test_ssh(exit_code=0, authorized_key=None):
    key = paramiko.RSAKey.generate(2048)
    server = TestSSHServer(authorized_key)
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

    def install(self, port, fingerprint, origin=None, **credentials):
        target = {"host": "127.0.0.1", "port": port, "username": "root", "password": "temporary-test",
                  "method": "password", "fingerprint": fingerprint}
        target.update(credentials)
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

    def test_ssh_private_key_authenticates_and_streams(self):
        client_key = ed25519.Ed25519PrivateKey.generate()
        raw = client_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                       serialization.BestAvailableEncryption(b"test-passphrase")).decode()
        fingerprint = parse_private_key(raw, "test-passphrase").fingerprint
        with test_ssh(authorized_key=fingerprint) as (port, host_fingerprint, remote):
            events = self.install(port, host_fingerprint, method="key", privateKey=raw,
                                  passphrase="test-passphrase")
            self.assertEqual(events[-1]["type"], "success")
            self.assertTrue(remote.authenticated)
            self.assertIn(INSTALL, remote.command)
            self.assertNotIn("test-passphrase", json.dumps(events))

        with self.assertRaisesRegex(ValueError, "无法读取 SSH 私钥"):
            parse_private_key(raw, "wrong-passphrase")

    def test_accepts_rsa_and_ecdsa_pem_keys(self):
        keys = ((rsa.generate_private_key(public_exponent=65537, key_size=2048), paramiko.RSAKey),
                (ec.generate_private_key(ec.SECP256R1()), paramiko.ECDSAKey))
        for key, expected_type in keys:
            with self.subTest(key=expected_type):
                raw = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
                self.assertIsInstance(parse_private_key(raw, ""), expected_type)

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


    def test_web_gateway_requires_operator_token_and_public_target(self):
        token = "f" * 64
        self.server.operator_token = token
        headers = {"Origin": WEB_ORIGIN, "Content-Type": "application/json"}
        endpoint = self.base + "/api/install"
        ready = Request(self.base + "/api/ready", headers={"Origin": WEB_ORIGIN})
        with urlopen(ready, timeout=2) as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], WEB_ORIGIN)
            self.assertEqual(json.load(response), {"installer": True, "sshKey": True})
        preflight = Request(endpoint, headers={"Origin": WEB_ORIGIN}, method="OPTIONS")
        with urlopen(preflight, timeout=2) as response:
            self.assertEqual(response.status, 204)
            self.assertIn("Authorization", response.headers["Access-Control-Allow-Headers"])
        target = {"host": "127.0.0.1", "port": 22, "username": "root", "password": "temporary-test",
                  "method": "password", "fingerprint": "SHA256:" + "A" * 43}
        with patch("installer.run_install") as run:
            for extra, status in (({}, 401), ({"Authorization": "Bearer " + token}, 400),
                                  ({"Origin": "https://example.com", "Authorization": "Bearer " + token}, 403)):
                with self.subTest(status=status), self.assertRaises(HTTPError) as rejected:
                    urlopen(Request(endpoint, json.dumps(target).encode(), headers | extra), timeout=2)
                self.assertEqual(rejected.exception.code, status)
            run.assert_not_called()

            target["host"] = "1.1.1.1"
            headers["Authorization"] = "Bearer " + token
            run.side_effect = lambda _target, send, *, web: send("success", str(web))
            with urlopen(Request(endpoint, json.dumps(target).encode(), headers), timeout=2) as response:
                self.assertEqual(json.load(response), {"type": "success", "text": "True"})
            run.assert_called_once()

    def test_public_target_validation(self):
        for address in ("10.0.0.1", "127.0.0.1", "169.254.169.254", "100.64.0.1",
                        "100.100.100.200", "224.0.0.1", "::1", "::ffff:127.0.0.1",
                        "64:ff9b::7f00:1", "2002:7f00:1::", "install.example.com"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                public_ip(address)
        self.assertEqual(public_ip("2606:4700:4700:0000::1111"), "2606:4700:4700::1111")


if __name__ == "__main__":
    unittest.main()
