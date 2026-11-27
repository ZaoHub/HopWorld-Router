#!/usr/bin/env bash
set -euo pipefail

site=/etc/nginx/conf.d/hopworld-public-admin.conf
if [[ $EUID != 0 ]] || ! grep -Fxq '# Managed by HopWorld Router public admin setup' "$site"; then
    echo '请在已配置 HopWorld Router 公网后台的服务器上以 root 执行。' >&2
    exit 1
fi

page=$(curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8080/login)
if [[ "$page" != *'data-initial-setup="true"'* ]]; then
    echo '管理员已完成首次设置，请直接使用公网管理地址登录。' >&2
    exit 1
fi

runtime=/run/hopworld-first-login
install -d -m 0700 "$runtime"
# The directory and its grants also disappear on reboot.
lease=$(mktemp "$runtime/lease.XXXXXXXX")
token=$(openssl rand -hex 32)
site_backup=$lease.original
cp -p "$site" "$site_backup"
applied=0
cleanup() {
    if (( !applied )); then
        unlink "$lease.map" 2>/dev/null || true
        unlink "$lease.server" 2>/dev/null || true
        cp -p "$site_backup" "$site"
        nginx -t && systemctl reload nginx || true
    fi
    unlink "$site_backup"
    unlink "$lease"
}
trap cleanup EXIT

public_host=$(python3 - "$site" <<'PYTHON'
from pathlib import Path
import re
import sys

site = Path(sys.argv[1])
text = site.read_text()
hosts = set(re.findall(r"^\s*server_name ([a-zA-Z0-9][a-zA-Z0-9.-]*);\s*$", text, re.M))
if len(hosts) != 1:
    sys.exit("无法确定公网管理地址。")
host = hosts.pop()
if "# HopWorld first-login routing" not in text:
    tls = "    ssl_protocols TLSv1.2 TLSv1.3;\n"
    host_header = f"        proxy_set_header Host {host};\n"
    if text.count(tls) != 1 or text.count(host_header) != 1:
        sys.exit("公网安装配置已自定义，无法自动添加首次设置入口。")
    text = text.replace(tls, tls + "    include /run/hopworld-first-login/*.server;\n")
    text = text.replace(host_header, "        proxy_set_header Host $hopworld_setup_host;\n"
                                  "        proxy_set_header Origin $hopworld_setup_origin;\n")
    maps = r"""# HopWorld first-login routing
map "$cookie_hopworld_first_setup|$request_method|$http_origin|$uri" $hopworld_setup_allowed {
    default 0;
    include /run/hopworld-first-login/*.map;
}
map $hopworld_setup_allowed $hopworld_setup_host {
    default PUBLIC_HOST;
    1 127.0.0.1;
}
map $hopworld_setup_allowed $hopworld_setup_origin {
    default $http_origin;
    1 https://127.0.0.1;
}
"""
    text = maps.replace("PUBLIC_HOST", host) + text

if "map $uri $hopworld_setup_path {" not in text:
    # Setup sessions need the same scope for the page and its password-change assets.
    for source in ("$cookie_hopworld_first_setup", "$remote_addr"):
        key = source + "|$request_method|$http_origin|$uri"
        text = text.replace(key, key.removesuffix("$uri") + "$hopworld_setup_path")
    text = """map $uri $hopworld_setup_path {
    default $uri;
    /style.css /;
    /v1.css /;
    /first-login.js /;
}
""" + text
if site.read_text() != text:
    site.write_text(text)
print(host)
PYTHON
)

umask 077
host_pattern=${public_host//./[.]}
cat > "$lease.map" <<NGINX
~^${token}[|]GET[|][|]/\$ 1;
~^${token}[|]GET[|][|]/login\$ 1;
~^${token}[|]POST[|]https://${host_pattern}[|]/login\$ 1;
~^${token}[|]POST[|]https://${host_pattern}[|]/api/v1/management/initial-password\$ 1;
NGINX
cat > "$lease.server" <<NGINX
location = /initial-setup/$token {
    access_log off;
    add_header Cache-Control "no-store" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Set-Cookie "hopworld_first_setup=$token; Max-Age=1800; Path=/; Secure; HttpOnly; SameSite=Lax" always;
    return 303 /login;
}
NGINX

nginx -t
systemd-run --quiet --on-active=30m --unit "hopworld-first-login-${lease##*.}" \
    /bin/bash -c 'unlink "$1"; unlink "$2"; /usr/sbin/nginx -t && /bin/systemctl reload nginx' \
    bash "$lease.map" "$lease.server"
systemctl reload nginx
applied=1
printf 'https://%s/initial-setup/%s\n' "$public_host" "$token"
