#!/usr/bin/env bash
set -euo pipefail

confirm_firewall_change() {
    local reply
    while true; do
        printf '%s [y/N] ' "$1" >&3
        read -r -u 3 reply || return 1
        case "$reply" in
            [Yy]) return 0 ;;
            [Nn]|'') return 1 ;;
            *) printf '请输入 Y 或 N。\n' >&3 ;;
        esac
    done
}

open_public_admin_ports() (
    local status='' ssh_peer ssh_port zone active_zones port failed=0
    local -a ports=()
    if ! { exec 3<>/dev/tty; } 2>/dev/null; then
        echo '未检测到交互终端，已跳过防火墙修改。' >&2
        return 1
    fi

    if command -v ufw >/dev/null; then
        status=$(LC_ALL=C ufw status) || return 1
        printf '%s\n' "$status"
    fi
    if confirm_firewall_change '是否放行 TCP 80（HTTP 证书验证和续期）？'; then
        ports+=(80/tcp)
    fi
    if confirm_firewall_change '是否放行 TCP 443（公网 HTTPS 后台）？'; then
        ports+=(443/tcp)
    fi
    if (( ${#ports[@]} == 0 )); then
        echo '已跳过防火墙修改。'
        return 0
    fi

    if [[ "$status" == 'Status: active'* ]]; then
        for port in "${ports[@]}"; do
            ufw allow "$port" || failed=1
        done
    elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
        active_zones=$(firewall-cmd --get-active-zones) || return 1
        zone=$(awk '/^[^[:space:]]/{print $1}' <<< "$active_zones")
        if [[ -z "$zone" ]]; then
            zone=$(firewall-cmd --get-default-zone) || return 1
        fi
        if [[ ! "$zone" =~ ^[[:alnum:]_-]+$ ]]; then
            echo 'firewalld 有多个活动区域或无法识别区域，请在公网入口所属区域放行端口。' >&2
            return 1
        fi
        for port in "${ports[@]}"; do
            firewall-cmd --zone="$zone" --permanent --add-port="$port" &&
                firewall-cmd --zone="$zone" --add-port="$port" || failed=1
        done
    elif [[ "$status" == 'Status: inactive' ]]; then
        read -r ssh_peer _ _ ssh_port <<< "${SSH_CONNECTION:-}"
        if [[ ! "$ssh_peer" =~ ^[[:xdigit:].:]+$ ||
              ! "$ssh_port" =~ ^[1-9][0-9]{0,4}$ ]] || (( ssh_port > 65535 )); then
            echo 'UFW 未启用，且无法识别当前 SSH 连接。请先确认 SSH 放行规则，再启用 UFW。' >&2
            return 1
        fi
        if ! confirm_firewall_change "UFW 未启用。是否放行来自 $ssh_peer 的 SSH TCP $ssh_port 并启用 UFW？"; then
            echo '已跳过启用 UFW 和修改防火墙规则。'
            return 0
        fi
        # Put the active SSH peer ahead of any existing user deny rules before enabling UFW.
        ufw prepend allow from "$ssh_peer" to any port "$ssh_port" proto tcp || return 1
        for port in "${ports[@]}"; do
            ufw allow "$port" || return 1
        done
        ufw --force enable || return 1
    else
        return 1
    fi
    return "$failed"
)

if [[ ${EUID} != 0 || $# != 1 ]] || ! command -v apt-get >/dev/null; then
    echo '用法：在 Ubuntu 或 Debian 上以 root 执行 bash hopworld-enable-public-admin.sh 公网IPv4或域名 / --firewall-only' >&2
    exit 2
fi

if [[ "$1" == --firewall-only ]]; then
    echo '检测到已配置的公网 HTTPS 后台，等待确认是否放行 TCP 80、443…'
    if ! open_public_admin_ports; then
        echo '防火墙配置未完成，请检查主机防火墙和云安全组的 TCP 80、443 入站规则。' >&2
    fi
    echo 'HopWorld Router 管理服务已就绪。'
    hopworld-router-check --instructions
    exit
fi

host=$(python3 - "$1" <<'PYTHON'
import ipaddress
import re
import sys

host = sys.argv[1].lower().rstrip(".")
if re.fullmatch(r"[0-9.]+", host):
    try:
        address = ipaddress.IPv4Address(host)
        if address.is_global and not address.is_multicast and not address.is_reserved:
            print(address)
            sys.exit(0)
    except ValueError:
        pass
elif re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", host):
    print(host)
    sys.exit(0)
sys.exit("请填写一条有效的公网 IPv4 或域名。")
PYTHON
)

local_url=http://127.0.0.1:8080
curl --noproxy '*' -fsS --max-time 5 "$local_url/health/ready" >/dev/null
site=/etc/nginx/conf.d/hopworld-public-admin.conf
settings=/etc/hopworld-router.env
marker='# Managed by HopWorld Router public admin setup'
if [[ -e "$site" ]] && ! grep -Fxq "$marker" "$site"; then
    echo "已有 $site，请先检查现有的 Nginx 配置。" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get install -y nginx
if [[ ! -x /snap/bin/certbot ]]; then
    apt-get install -y snapd
    snap install --classic certbot
fi

webroot=/var/www/hopworld-public-admin
cert_name=hopworld-public-admin-$host
cert_dir=/etc/letsencrypt/live/$cert_name
backup=$(mktemp -d)
if [[ -e "$settings" ]]; then cp -p "$settings" "$backup/router.env"; fi
if [[ -e "$site" ]]; then cp -p "$site" "$backup/nginx.conf"; fi

changed_settings=0
finished=0
cleanup() {
    if (( !finished )); then
        if [[ -f "$backup/nginx.conf" ]]; then
            cp -p "$backup/nginx.conf" "$site"
        else
            unlink "$site" 2>/dev/null || true
        fi
        nginx -t && systemctl reload nginx || true
        if (( changed_settings )); then
            if [[ -f "$backup/router.env" ]]; then
                cp -p "$backup/router.env" "$settings"
            else
                unlink "$settings" 2>/dev/null || true
            fi
            systemctl restart hopworld-router || true
        fi
    fi
    if [[ -f "$backup/router.env" ]]; then unlink "$backup/router.env"; fi
    if [[ -f "$backup/nginx.conf" ]]; then unlink "$backup/nginx.conf"; fi
    rmdir "$backup"
}
trap cleanup EXIT

install -d -m 0755 "$webroot/.well-known/acme-challenge"
write_http() {
    printf '%s\n' "$marker"
    cat <<NGINX
server {
    listen 80;
    server_name $host;
    location ^~ /.well-known/acme-challenge/ {
        root $webroot;
        try_files \$uri =404;
    }
    location / { $1 }
}
NGINX
}

if [[ ! -r "$cert_dir/fullchain.pem" ]]; then
    write_http 'return 404;' > "$site"
    nginx -t
    systemctl enable --now nginx
    systemctl reload nginx
fi

echo '正在检查主机防火墙，等待确认是否放行 TCP 80、443…'
if ! open_public_admin_ports; then
    echo '防火墙配置未完成，将继续申请证书。请检查主机防火墙的 TCP 80、443 入站规则。' >&2
fi
echo '如果通过云安全组或 NAT 接入，请确认公网 TCP 80、443 可以到达本机。'

certificate_args=(-d "$host")
if [[ "$host" =~ ^[0-9.]+$ ]]; then
    certificate_args=(--preferred-profile shortlived --ip-address "$host")
fi
here=$(cd "$(dirname "$0")" && pwd)
/snap/bin/certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
    --keep-until-expiring --webroot --webroot-path "$webroot" --cert-name "$cert_name" \
    --deploy-hook 'systemctl reload nginx' "${certificate_args[@]}"

# Seed Router's cert cache so「启用公网订阅」can reuse this HTTPS certificate on TCP 9443.
data_dir=/var/lib/hopworld-router
if [[ -f "$settings" ]]; then
    data_dir=$(awk -F= '/^[[:space:]]*HOPWORLD_DATA=/{value=$2} END{print value}' "$settings")
    data_dir=${data_dir:-/var/lib/hopworld-router}
fi
RENEWED_LINEAGE="$cert_dir" HOPWORLD_DATA="$data_dir" bash "$here/hopworld-certbot-renew-hook.sh"
bash "$here/hopworld-install-certbot-renew-hook.sh"

write_http "return 308 https://$host\$request_uri;" > "$site"
cat >> "$site" <<NGINX
server {
    listen 443 ssl;
    server_name $host;
    ssl_certificate $cert_dir/fullchain.pem;
    ssl_certificate_key $cert_dir/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    location / {
        proxy_pass $local_url;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$remote_addr;
    }
}
NGINX
nginx -t

changed_settings=1
if [[ ! -e "$settings" ]]; then install -m 0600 /dev/null "$settings"; fi
sed -i -E '/^[[:space:]]*HOPWORLD_MANAGEMENT_(HTTPS_HOST|LOCAL_ONLY)=/d' "$settings"
printf '\nHOPWORLD_MANAGEMENT_HTTPS_HOST=%s\nHOPWORLD_MANAGEMENT_LOCAL_ONLY=1\n' "$host" >> "$settings"
systemctl restart hopworld-router
curl --noproxy '*' --retry 5 --retry-connrefused -fsS --max-time 5 "$local_url/health/ready" >/dev/null
systemctl enable --now nginx
systemctl reload nginx
curl --noproxy '*' --resolve "$host:443:127.0.0.1" --retry 5 --retry-connrefused -fsS --max-time 5 \
    "https://$host/health/ready" >/dev/null
finished=1

printf '\n%s\n' '公网 HTTPS 后台配置完成。'
if ! command -v hopworld-router-check >/dev/null || ! hopworld-router-check --instructions; then
    printf '本地管理地址：%s/\n外网管理地址：https://%s/\n' "$local_url" "$host"
    echo '新装用户请先通过本地地址完成首次管理员改密，再用新密码登录外网管理地址。'
fi
