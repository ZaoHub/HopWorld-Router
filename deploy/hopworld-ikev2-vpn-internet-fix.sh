#!/bin/bash
# Run on the VPS as root when IKEv2 shows “connected but no traffic” or phone has no internet.
# Usage: bash /opt/hopworld-router/deploy/hopworld-ikev2-vpn-internet-fix.sh
set -euo pipefail

ROOT="${HOPWORLD_ROOT:-/opt/hopworld-router}"
DATA="${HOPWORLD_IKEV2_DATA:-/var/lib/hopworld-router/ikev2}"
SBIN=/usr/local/sbin

install -d -m 755 "$SBIN"
for name in hopworld-ikev2-apply hopworld-ikev2-updown hopworld-boot; do
  src="$ROOT/deploy/$name"
  [[ -f "$src" ]] || { echo "missing $src"; exit 1; }
  install -m 755 "$src" "$SBIN/$name"
done

chown -R hopworld-router:hopworld-router "$ROOT/router" 2>/dev/null || true
systemctl restart hopworld-router
sleep 2

"$SBIN/hopworld-ikev2-apply" apply "$DATA"
"$SBIN/hopworld-ikev2-apply" sync-clients "$DATA"

if systemctl is-active strongswan &>/dev/null; then
  systemctl restart strongswan
elif systemctl is-active strongswan-starter &>/dev/null; then
  systemctl restart strongswan-starter
fi

echo "--- NAT (pool + per-client) ---"
iptables -t nat -S POSTROUTING | grep -E '10\.66|MASQUERADE' || true
echo "--- routes (virtual IP on lo) ---"
ip route | grep '10.66' || true
echo "--- strongSwan ---"
strongswan status 2>/dev/null | head -8 || true
echo "OK — 请在手机断开 VPN 后重新连接，Safari 打开 http://1.1.1.1 测试。"
