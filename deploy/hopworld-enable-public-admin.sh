#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} != 0 || $# != 1 ]] || ! command -v apt-get >/dev/null; then
    echo '用法：在 Ubuntu 或 Debian 上以 root 执行 bash hopworld-enable-public-admin.sh 公网IPv4或域名' >&2
    exit 2
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

certificate_args=(-d "$host")
if [[ "$host" =~ ^[0-9.]+$ ]]; then
    certificate_args=(--preferred-profile shortlived --ip-address "$host")
fi
/snap/bin/certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
    --keep-until-expiring --webroot --webroot-path "$webroot" --cert-name "$cert_name" \
    --deploy-hook 'systemctl reload nginx' "${certificate_args[@]}"

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

printf '\n本地管理地址：%s/\n外网管理地址：https://%s/\n' "$local_url" "$host"
echo '新装用户请先通过本地地址完成首次管理员改密，再用新密码登录外网管理地址。'
