#!/usr/bin/env bash
# CentOS/RHEL: nginx + certbot webroot for HopWorld ACME (run as root).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEBROOT=/var/www/acme
NGINX_CONF=/etc/nginx/conf.d/hopworld-acme-webroot.conf
ENV_FILE=/etc/hopworld-router.env
SUDOERS=/etc/sudoers.d/hopworld-router-acme

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 运行" >&2
  exit 1
fi

if command -v yum >/dev/null; then
  yum install -y epel-release 2>/dev/null || true
  yum install -y nginx certbot 2>/dev/null || yum install -y nginx python2-certbot-nginx
elif command -v dnf >/dev/null; then
  dnf install -y nginx certbot
else
  echo "请手动安装 nginx 与 certbot" >&2
  exit 1
fi

mkdir -p "$WEBROOT"
chown hopworld-router:hopworld-router "$WEBROOT"
chmod 755 /var/www
chmod 755 "$WEBROOT"

install -m 644 "$ROOT/deploy/hopworld-acme-webroot.conf" "$NGINX_CONF"

printf '%s\n' \
  'hopworld-router ALL=(root) NOPASSWD: /usr/bin/certbot' \
  > "$SUDOERS"
chmod 440 "$SUDOERS"
visudo -cf "$SUDOERS"

touch "$ENV_FILE"
grep -q '^HOPWORLD_ACME_WEBROOT=' "$ENV_FILE" \
  && sed -i "s|^HOPWORLD_ACME_WEBROOT=.*|HOPWORLD_ACME_WEBROOT=$WEBROOT|" "$ENV_FILE" \
  || echo "HOPWORLD_ACME_WEBROOT=$WEBROOT" >> "$ENV_FILE"
grep -q '^HOPWORLD_ACME_WRAPPER=' "$ENV_FILE" \
  && sed -i 's|^HOPWORLD_ACME_WRAPPER=.*|HOPWORLD_ACME_WRAPPER=sudo /usr/bin/certbot|' "$ENV_FILE" \
  || echo 'HOPWORLD_ACME_WRAPPER=sudo /usr/bin/certbot' >> "$ENV_FILE"
chmod 600 "$ENV_FILE"

systemctl enable nginx
systemctl restart nginx
systemctl restart hopworld-router 2>/dev/null || true

echo "OK: webroot=$WEBROOT nginx=$(systemctl is-active nginx)"
echo "下一步: 域名 A 记录指向本机后，在管理页填写域名并启用 Let's Encrypt"
