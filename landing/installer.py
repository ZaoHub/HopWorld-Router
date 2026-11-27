"""SSH installer for HopWorld-Router, with an optional HTTPS gateway."""

import argparse
import codecs
import hmac
import ipaddress
import json
import os
import re
import shlex
import socket
import threading
import time
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import paramiko
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "dist"
DOWNLOAD = "/hopworld-router-installer.zip"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/commands.html": ("commands.html", "text/html; charset=utf-8"),
    "/commands.js": ("commands.js", "application/javascript; charset=utf-8"),
    "/site.css": ("site.css", "text/css; charset=utf-8"),
    "/ssh-install-access.js": ("ssh-install-access.js", "application/javascript; charset=utf-8"),
    "/logo.png": ("logo.png", "image/png"),
}
INSTALL = "set -eo pipefail; curl -fsSL https://raw.githubusercontent.com/ZaoHub/HopWorld-Router/v1.0.2/deploy/install.sh | bash"
INSTALL_LOCK = threading.Lock()
BODY_LIMIT = 24 * 1024
KEY_LIMIT = 12 * 1024
JOB_SECONDS = 25 * 60
HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
USER = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
WEB_ORIGIN = "https://zaohub.github.io"
OPERATOR_TOKEN = re.compile(r"[0-9a-f]{64}")
PUBLIC_IPV6 = ipaddress.ip_network("2000::/3")
SIX_TO_FOUR = ipaddress.ip_network("2002::/16")
NON_ROUTABLE_IPV4 = (ipaddress.ip_network("192.0.0.0/24"), ipaddress.ip_network("192.88.99.0/24"))


def public_ip(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("网页安装需要填写服务器的公网 IP。") from None
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise ValueError("网页安装只连接公网单播 IP。")
    if address.version == 6 and (address not in PUBLIC_IPV6 or address in SIX_TO_FOUR):
        raise ValueError("网页安装只连接公网单播 IP。")
    if address.version == 4 and any(address in subnet for subnet in NON_ROUTABLE_IPV4):
        raise ValueError("网页安装只连接公网单播 IP。")
    return str(address)


def installer_archive():
    files = [ROOT / name for name in ("installer.py", "manage.py", "start.sh", "requirements.txt", "README.md")]
    files.extend(STATIC / name for name, _ in FILES.values())
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        for path in files:
            output.write(path, f"hopworld-router-installer/{path.relative_to(ROOT)}")
    return buffer.getvalue()


class HostKeyMismatch(paramiko.SSHException):
    """The server presented a different key from the trusted fingerprint."""


class VerifyHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected):
        self.expected = expected

    def missing_host_key(self, _client, _hostname, key):
        if not hmac.compare_digest(self.expected, key.fingerprint):
            raise HostKeyMismatch(f"主机指纹不匹配。服务器提供 {key.get_name()} {key.fingerprint}。请从可信终端核对。")


def parse_private_key(text, passphrase):
    if not isinstance(text, str) or not 1 <= len(text) <= KEY_LIMIT or not text.isascii():
        raise ValueError("请粘贴完整的 SSH 私钥。")
    if not isinstance(passphrase, str) or len(passphrase) > 512:
        raise ValueError("SSH 私钥口令过长。")
    raw = text.strip().encode("ascii")
    unlock = passphrase.encode() if passphrase else None
    load = (serialization.load_ssh_private_key if raw.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")
            else serialization.load_pem_private_key)
    try:
        key = load(raw, password=unlock)
        classes = ((rsa.RSAPrivateKey, paramiko.RSAKey), (ec.EllipticCurvePrivateKey, paramiko.ECDSAKey),
                   (ed25519.Ed25519PrivateKey, paramiko.Ed25519Key))
        for kind, ssh_key in classes:
            if isinstance(key, kind):
                normalized = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                               serialization.NoEncryption())
                return ssh_key.from_private_key(StringIO(normalized.decode("ascii")))
    except (ValueError, TypeError, UnsupportedAlgorithm, paramiko.SSHException):
        raise ValueError("无法读取 SSH 私钥，请检查私钥格式和口令。") from None
    raise ValueError("请使用 RSA、ECDSA 或 Ed25519 私钥。")


def validation(data, web=False):
    if not isinstance(data, dict):
        raise ValueError("安装资料必须是 JSON 对象。")
    host, port, username = (data.get(field) for field in ("host", "port", "username"))
    fingerprint, method = (data.get(field) for field in ("fingerprint", "method"))
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", host):
        raise ValueError("请输入服务器 IP 或域名。")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not all(HOST_LABEL.fullmatch(label) for label in host.removesuffix(".").split(".")):
            raise ValueError("服务器地址格式有误，请填写 IP 或域名。") from None
    if web:
        host = public_ip(host)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("SSH 端口须为 1 至 65535。")
    if not isinstance(username, str) or not USER.fullmatch(username):
        raise ValueError("请输入有效的 SSH 账号。")
    if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("请输入完整的 SHA256 主机指纹。")
    if method not in ("password", "key", "agent") or (web and method == "agent"):
        raise ValueError("请选择 SSH 验证方式。")
    password = data.pop("password", None) if method == "password" else None
    if method == "password" and (not isinstance(password, str) or not 1 <= len(password) <= 512):
        raise ValueError("请输入 SSH 密码。")
    private_key = None
    if method == "key":
        private_key = parse_private_key(data.pop("privateKey", None), data.pop("passphrase", ""))
    return host, port, username, fingerprint, method, password, private_key


def run_install(target, send, web=False):
    host, port, username, fingerprint, method, password, private_key = target
    send("status", "正在连接服务器并核对 SSH 主机指纹…")
    connection = socket.create_connection((host, port), timeout=12) if web else nullcontext()
    with connection as sock, paramiko.SSHClient() as client:
        client.set_missing_host_key_policy(VerifyHostKey(fingerprint))
        client.connect(host, port=port, username=username, password=password, pkey=private_key, sock=sock,
                       allow_agent=method == "agent", look_for_keys=False, timeout=12,
                       auth_timeout=15, banner_timeout=12, channel_timeout=15)
        send("status", "SSH 已连接，正在执行 HopWorld-Router V1.0.2 安装…")
        transport = client.get_transport()
        transport.set_keepalive(20)
        with transport.open_session(timeout=15) as channel:
            channel.set_combine_stderr(True)
            shell = "bash" if username == "root" else "sudo -n bash"
            channel.exec_command(f"{shell} -c {shlex.quote(INSTALL)}")
            channel.shutdown_write()
            deadline = time.monotonic() + JOB_SECONDS
            heartbeat = time.monotonic() + 15
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while not channel.exit_status_ready() or channel.recv_ready():
                now = time.monotonic()
                if now >= deadline:
                    raise TimeoutError("安装等待超时，请检查服务器上的 HopWorld-Router 服务状态。")
                if web and now >= heartbeat:
                    send("heartbeat", "")
                    heartbeat = now + 15
                if channel.recv_ready():
                    fragment = decoder.decode(channel.recv(8192))
                    if fragment:
                        send("output", fragment)
                else:
                    time.sleep(0.15)
            if trailing := decoder.decode(b"", final=True):
                send("output", trailing)
            code = channel.recv_exit_status()
            if code:
                send("error", f"安装退出，代码 {code}。请查看上方输出。")
            else:
                send("success", "安装完成。")


class Installer(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    @property
    def operator_token(self):
        return getattr(self.server, "operator_token", None)

    @property
    def local_host(self):
        return f"127.0.0.1:{self.server.server_port}"

    def headers_for(self, status, mime, size=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.operator_token and self.headers.get("Origin") == WEB_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", WEB_ORIGIN)
            self.send_header("Vary", "Origin")
            if self.command == "OPTIONS":
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        if size is not None:
            self.send_header("Content-Length", str(size))
        self.end_headers()

    def reply(self, status, mime, data):
        self.headers_for(status, mime, len(data))
        self.wfile.write(data)

    def json(self, status, **data):
        self.reply(status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode())

    def do_OPTIONS(self):
        if not self.operator_token or self.headers.get("Origin") != WEB_ORIGIN:
            return self.json(403, error="请从 GitHub 安装页面发起请求。")
        if self.path != "/api/install":
            return self.json(404, error="页面不存在。")
        self.headers_for(204, "text/plain", 0)

    def do_GET(self):
        if self.operator_token:
            if self.headers.get("Origin") != WEB_ORIGIN:
                return self.json(403, error="请从 GitHub 安装页面发起请求。")
            if self.path == "/api/ready":
                return self.json(200, installer=True, sshKey=True)
            return self.json(404, error="页面不存在。")
        if self.headers.get("Host") != self.local_host:
            return self.json(403, error="请通过 127.0.0.1 访问安装器。")
        if self.path == "/api/ready":
            return self.json(200, installer=True, sshKey=True)
        if self.path == DOWNLOAD:
            return self.reply(200, "application/zip", installer_archive())
        file = FILES.get(self.path)
        if file is None:
            return self.json(404, error="页面不存在。")
        name, mime = file
        return self.reply(200, mime, (STATIC / name).read_bytes())

    def do_POST(self):
        web = bool(self.operator_token)
        if web:
            if self.headers.get("Origin") != WEB_ORIGIN:
                return self.json(403, error="请从 GitHub 安装页面发起请求。")
            authorization = self.headers.get("Authorization", "").encode("latin-1")
            expected = f"Bearer {self.operator_token}".encode("ascii")
            if not hmac.compare_digest(authorization, expected):
                return self.json(401, error="安装服务访问令牌无效。")
        elif (self.headers.get("Host") != self.local_host or
              self.headers.get("Origin") != f"http://{self.local_host}"):
            return self.json(403, error="请通过本机安装页面发起请求。")
        if self.path != "/api/install":
            return self.json(404, error="页面不存在。")
        if self.headers.get_content_type() != "application/json":
            return self.json(415, error="请求格式不受支持。")
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 1 <= size <= BODY_LIMIT:
                return self.json(413, error="安装资料大小不受支持。")
            self.connection.settimeout(10)
            target = validation(json.loads(self.rfile.read(size)), web=web)
        except (ValueError, UnicodeError, TimeoutError) as error:
            message = str(error) if type(error) is ValueError else "无法读取安装资料。"
            return self.json(400, error=message)
        if not INSTALL_LOCK.acquire(blocking=False):
            return self.json(409, error="另一项安装正在进行。")
        try:
            self.headers_for(200, "application/x-ndjson; charset=utf-8")

            def send(kind, message):
                data = json.dumps({"type": kind, "text": message}, ensure_ascii=False) + "\n"
                self.wfile.write(data.encode())
                self.wfile.flush()

            try:
                run_install(target, send, web=web)
            except HostKeyMismatch as error:
                send("error", str(error))
            except paramiko.AuthenticationException:
                send("error", "SSH 身份验证失败，请核对账号与所选验证方式。")
            except TimeoutError as error:
                message = str(error) if str(error).startswith("安装等待") else "SSH 连接超时，请检查服务器地址与端口。"
                send("error", message)
            except (socket.gaierror, OSError, paramiko.SSHException):
                send("error", "无法完成 SSH 连接或执行安装，请检查 SSH 服务和账号权限。")
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            INSTALL_LOCK.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18731)
    options = parser.parse_args()
    token = os.environ.get("INSTALL_OPERATOR_TOKEN")
    if token is not None and not OPERATOR_TOKEN.fullmatch(token):
        parser.error("INSTALL_OPERATOR_TOKEN must contain 64 lowercase hexadecimal characters")
    with ThreadingHTTPServer(("127.0.0.1", options.port), Installer) as server:
        server.operator_token = token
        print(f"HopWorld-Router 安装器监听 127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
