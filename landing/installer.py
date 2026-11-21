"""Loopback-only Linux installer for HopWorld-Router."""

import argparse
import codecs
import hmac
import ipaddress
import json
import re
import shlex
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import paramiko

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "dist"
DOWNLOAD = "/hopworld-router-installer.zip"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/commands.html": ("commands.html", "text/html; charset=utf-8"),
    "/commands.js": ("commands.js", "application/javascript; charset=utf-8"),
    "/site.css": ("site.css", "text/css; charset=utf-8"),
    "/site.js": ("site.js", "application/javascript; charset=utf-8"),
    "/logo.png": ("logo.png", "image/png"),
}
INSTALL = "set -eo pipefail; curl -fsSL https://raw.githubusercontent.com/ZaoHub/HopWorld-Router/v1.0.2/deploy/install.sh | bash"
INSTALL_LOCK = threading.Lock()
BODY_LIMIT = 8192
JOB_SECONDS = 25 * 60
HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
USER = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")


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


def validation(data):
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
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("SSH 端口须为 1 至 65535。")
    if not isinstance(username, str) or not USER.fullmatch(username):
        raise ValueError("请输入有效的 SSH 账号。")
    if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("请输入完整的 SHA256 主机指纹。")
    if method not in ("password", "agent"):
        raise ValueError("请选择 SSH 验证方式。")
    password = data.pop("password", None) if method == "password" else None
    if method == "password" and (not isinstance(password, str) or not 1 <= len(password) <= 512):
        raise ValueError("请输入 SSH 密码。")
    return host, port, username, fingerprint, method, password


def run_install(target, send):
    host, port, username, fingerprint, method, password = target
    send("status", "正在连接服务器并核对 SSH 主机指纹…")
    with paramiko.SSHClient() as client:
        client.set_missing_host_key_policy(VerifyHostKey(fingerprint))
        client.connect(host, port=port, username=username, password=password,
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
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while not channel.exit_status_ready() or channel.recv_ready():
                if time.monotonic() >= deadline:
                    raise TimeoutError("安装等待超时，请检查服务器上的 HopWorld-Router 服务状态。")
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
    def local_host(self):
        return f"127.0.0.1:{self.server.server_port}"

    def headers_for(self, status, mime, size=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        if size is not None:
            self.send_header("Content-Length", str(size))
        self.end_headers()

    def reply(self, status, mime, data):
        self.headers_for(status, mime, len(data))
        self.wfile.write(data)

    def json(self, status, **data):
        self.reply(status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode())

    def do_GET(self):
        if self.headers.get("Host") != self.local_host:
            return self.json(403, error="请通过 127.0.0.1 访问安装器。")
        if self.path == "/api/ready":
            return self.json(200, installer=True)
        if self.path == DOWNLOAD:
            return self.reply(200, "application/zip", installer_archive())
        file = FILES.get(self.path)
        if file is None:
            return self.json(404, error="页面不存在。")
        name, mime = file
        return self.reply(200, mime, (STATIC / name).read_bytes())

    def do_POST(self):
        if (self.headers.get("Host") != self.local_host or
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
            target = validation(json.loads(self.rfile.read(size)))
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
                run_install(target, send)
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
    with ThreadingHTTPServer(("127.0.0.1", options.port), Installer) as server:
        print(f"HopWorld-Router 安装器：http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
