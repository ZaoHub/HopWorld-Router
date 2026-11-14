#!/bin/bash
# Run on the VPS (as root) when http://PUBLIC:8080 does not load.
set -euo pipefail
echo "=== hopworld-router ==="
systemctl status hopworld-router --no-pager -l || true
echo "=== listen 8080 ==="
ss -lntp | grep ':8080' || echo "port 8080 not listening"
echo "=== local curl ==="
curl -s -o /dev/null -w "http_127=%{http_code}\n" --connect-timeout 3 http://127.0.0.1:8080/ || echo http_failed
echo "=== env ==="
grep -E '^HOPWORLD_' /etc/hopworld-router.env 2>/dev/null | sed 's/ADMIN_PASSWORD=.*/ADMIN_PASSWORD=***/' || true
echo "=== recent logs ==="
journalctl -u hopworld-router -n 40 --no-pager 2>/dev/null || true
echo "=== iptables 8080 ==="
iptables -S INPUT 2>/dev/null | grep 8080 || true
