#!/usr/bin/env bash
# certbot renew deploy-hook: copy certs for HopWorld Router and reload nginx.
# Installed to: /etc/letsencrypt/renewal-hooks/deploy/hopworld-router.sh
set -euo pipefail

DATA="${HOPWORLD_DATA:-/var/lib/hopworld-router}"
LINEAGE="${RENEWED_LINEAGE:-}"

if [[ -z "$LINEAGE" || ! -d "$LINEAGE" ]]; then
  exit 0
fi

domain="$(basename "$LINEAGE")"
# Public-admin install uses lineage hopworld-public-admin-<host>; cache under <host>
# so Router public subscription can reuse the same certificate.
if [[ "$domain" == hopworld-public-admin-* ]]; then
  domain="${domain#hopworld-public-admin-}"
fi
dest="$DATA/certs/$domain"
if [[ -L "$DATA/certs" || -L "$dest" || -L "$dest/fullchain.pem" || -L "$dest/privkey.pem" ]]; then
  echo "证书目标位置不能是符号链接" >&2
  exit 1
fi
install -d -m 700 -o hopworld-router -g hopworld-router "$dest"
stage="$(mktemp -d "$dest/.certbot.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
install -m 600 -o hopworld-router -g hopworld-router \
  "$LINEAGE/fullchain.pem" "$stage/fullchain.pem"
install -m 600 -o hopworld-router -g hopworld-router \
  "$LINEAGE/privkey.pem" "$stage/privkey.pem"
cert_pub="$(openssl x509 -in "$stage/fullchain.pem" -noout -pubkey)"
key_pub="$(openssl pkey -in "$stage/privkey.pem" -passin pass: -pubout)"
if [[ -z "$cert_pub" || "$cert_pub" != "$key_pub" ]]; then
  echo "证书与私钥不匹配" >&2
  exit 1
fi
for part in fullchain.pem privkey.pem; do
  if [[ -f "$dest/$part" ]]; then
    cp -p "$dest/$part" "$stage/previous-$part"
  fi
done
restore_pair() {
  local part
  for part in fullchain.pem privkey.pem; do
    if [[ -f "$stage/previous-$part" ]]; then
      mv -f -- "$stage/previous-$part" "$dest/$part"
    else
      rm -f -- "$dest/$part"
    fi
  done
}
mv -f -- "$stage/privkey.pem" "$dest/privkey.pem"
if ! mv -f -- "$stage/fullchain.pem" "$dest/fullchain.pem"; then
  restore_pair
  exit 1
fi

reload_website() {
  if command -v nginx >/dev/null && systemctl is-active --quiet nginx; then
    nginx -t && systemctl reload nginx || return 1
  fi
  if command -v caddy >/dev/null && systemctl is-active --quiet caddy; then
    caddy validate --config "${HOPWORLD_CADDY_CONFIG:-/etc/caddy/Caddyfile}" && systemctl reload caddy || return 1
  fi
}
if ! reload_website; then
  restore_pair
  reload_website || true
  echo "网站配置校验或重载失败，已恢复 Router 缓存的证书" >&2
  exit 1
fi
