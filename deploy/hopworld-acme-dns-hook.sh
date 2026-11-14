#!/usr/bin/env bash
# Certbot DNS-01 hook: hopworld-acme-dns-hook.sh auth|cleanup
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/.venv/bin/python" -m router.acme_dns_hook "$@"
