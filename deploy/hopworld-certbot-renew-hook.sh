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
dest="$DATA/certs/$domain"
mkdir -p "$dest"
install -m 600 -o hopworld-router -g hopworld-router \
  "$LINEAGE/fullchain.pem" "$dest/fullchain.pem"
install -m 600 -o hopworld-router -g hopworld-router \
  "$LINEAGE/privkey.pem" "$dest/privkey.pem"
chmod 700 "$dest" 2>/dev/null || true

if command -v nginx >/dev/null && systemctl is-active --quiet nginx; then
  nginx -t && systemctl reload nginx
fi
