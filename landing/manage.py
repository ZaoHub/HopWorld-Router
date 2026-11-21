"""Manage a HopWorld-Router instance through its local HTTP interface."""

import argparse
import base64
import getpass
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ACCOUNT_PATH = "/api/v1/management/account"
ACCESS_PATH = "/api/v1/management/access"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def local_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.path not in ("", "/") or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("请输入 HTTP 回环地址，例如 http://127.0.0.1:8080。HTTPS 管理入口请通过后台设置修改。")
    return f"http://127.0.0.1:{parsed.port}"


def make_call(user, password):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    authorization = base64.b64encode(f"{user}:{password}".encode()).decode()

    def call(origin, path, data=None):
        headers = {"Authorization": f"Basic {authorization}", "Accept": "application/json"}
        body = None
        if data is not None:
            headers.update({"Origin": origin, "Content-Type": "application/json"})
            body = json.dumps(data).encode()
        try:
            with opener.open(Request(origin + path, data=body, headers=headers), timeout=8) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                detail = json.load(error).get("error")
            except (ValueError, AttributeError):
                detail = None
            raise ValueError(detail or f"后台返回 HTTP {error.code}。") from None

    return call


def main():
    parser = argparse.ArgumentParser(description="通过服务器回环地址或 SSH 隧道管理 HopWorld-Router。")
    parser.add_argument("action", choices=("password", "username", "port"))
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--port", type=int, help="新的服务器管理端口")
    options = parser.parse_args()

    try:
        origin = local_origin(options.url)
        username = input("当前管理员账号 [admin]：").strip() or "admin"
        password = getpass.getpass("当前管理员密码：")
        call = make_call(username, password)
        if options.action == "password":
            new_password = getpass.getpass("新密码（至少 6 个字符）：")
            if not 6 <= len(new_password) <= 256 or new_password != getpass.getpass("再次输入新密码："):
                raise ValueError("密码长度须为 6 至 256 个字符，且两次输入一致。")
            call(origin, ACCOUNT_PATH, {"username": username, "current_password": password,
                                       "new_password": new_password})
        elif options.action == "username":
            new_username = input("新管理员账号：").strip()
            call(origin, ACCOUNT_PATH, {"username": new_username, "current_password": password})
        else:
            settings = call(origin, ACCESS_PATH)
            if not settings or settings.get("active", {}).get("https"):
                raise ValueError("当前入口不可通过此命令切换，请在管理后台调整 HTTPS 或端口设置。")
            port = options.port if options.port is not None else int(input("新的管理端口（1024–65535）："))
            if not 1024 <= port <= 65535:
                raise ValueError("新端口须为 1024 至 65535。")
            call(origin, ACCESS_PATH, {"action": "stage", "port": port, "https": False})
            try:
                call(f"http://127.0.0.1:{port}", ACCESS_PATH, {"action": "confirm"})
            except (OSError, ValueError) as error:
                raise ValueError(f"新端口尚未确认，原入口继续有效。{error}") from None
            print(f"管理端口已切换至 {port}。请调整 SSH 隧道的目标端口。")
            return
        print("已保存。请使用更新后的账号信息登录。")
    except (ValueError, URLError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
