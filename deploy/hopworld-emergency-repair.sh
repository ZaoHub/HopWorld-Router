#!/bin/bash
# Run as root from the server console when the management service is unreachable.
set -euo pipefail

ENV=/etc/hopworld-router.env
UNIT=hopworld-router

echo "[1] ensure env listens on all interfaces"
touch "$ENV"
chmod 600 "$ENV"
grep -q '^HOPWORLD_HOST=' "$ENV" && sed -i 's/^HOPWORLD_HOST=.*/HOPWORLD_HOST=0.0.0.0/' "$ENV" \
  || echo 'HOPWORLD_HOST=0.0.0.0' >>"$ENV"

echo "[2] firewall local (CentOS / generic)"
if command -v firewall-cmd &>/dev/null && systemctl is-active firewalld &>/dev/null; then
  firewall-cmd --permanent --add-port=80/tcp || true
  firewall-cmd --permanent --add-port=8080/tcp || true
  firewall-cmd --reload || true
fi
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 80 -j ACCEPT
iptables -C INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 8080 -j ACCEPT

echo "[3] install boot self-enable"
if [[ -x /opt/hopworld-router/deploy/hopworld-install-boot.sh ]]; then
  bash /opt/hopworld-router/deploy/hopworld-install-boot.sh || true
else
  systemctl enable hopworld-router 2>/dev/null || true
fi
echo "[4] restart hopworld-router"
systemctl daemon-reload
systemctl restart "$UNIT"
sleep 2
systemctl is-active "$UNIT" || { journalctl -u "$UNIT" -n 30 --no-pager; exit 1; }

echo "[5] local health"
ss -lntp | grep ':8080' || (echo "8080 not listening"; exit 1)
curl -s -o /dev/null -w "local_http=%{http_code}\n" http://127.0.0.1:8080/ || true

echo "[6] IKEv2 helper"
if [[ -x /usr/local/sbin/hopworld-ikev2-apply ]]; then
  /usr/local/sbin/hopworld-ikev2-apply apply /var/lib/hopworld-router/ikev2 2>/dev/null || true
  /usr/local/sbin/hopworld-ikev2-apply sync-clients /var/lib/hopworld-router/ikev2 2>/dev/null || true
fi

echo "OK — 若外网仍打不开，请在 检查服务器安全组和防火墙配置。"
