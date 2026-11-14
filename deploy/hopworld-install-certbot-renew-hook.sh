#!/usr/bin/env bash
# Install certbot deploy hook (renew → copy certs + reload nginx). Run as root.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_DST=/etc/letsencrypt/renewal-hooks/deploy/hopworld-router.sh

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 运行" >&2
  exit 1
fi

mkdir -p "$(dirname "$HOOK_DST")"
install -m 755 "$ROOT/deploy/hopworld-certbot-renew-hook.sh" "$HOOK_DST"

ENV_FILE=/etc/hopworld-router.env
touch "$ENV_FILE"
grep -q '^HOPWORLD_DATA=' "$ENV_FILE" \
  || echo 'HOPWORLD_DATA=/var/lib/hopworld-router' >>"$ENV_FILE"

echo "OK: deploy-hook=$HOOK_DST"
echo "测试: certbot renew --dry-run"
