#!/usr/bin/env bash
# Certbot DNS-01 hook: hopworld-acme-dns-hook.sh auth|cleanup
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python -m router.acme_dns_hook "$@"
fi
exec /usr/bin/python3 -m router.acme_dns_hook "$@"
