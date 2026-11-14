#!/bin/bash
# 回退到尽量接近「能连上、监视器能看到在线」的稳定基线（在 VNC/SSH 上以 root 执行）。
# 用法: bash /opt/hopworld-router/deploy/hopworld-ikev2-rollback-stable.sh
set -euo pipefail

DATA="${HOPWORLD_IKEV2_DATA:-/var/lib/hopworld-router/ikev2}"
POOL="10.66.0.0/24"

echo "[1] 去掉 ipsec 里可能不兼容的 rightupdown"
if [[ -f "$DATA/ipsec.conf" ]]; then
  sed -i '/^[[:space:]]*rightupdown=/d' "$DATA/ipsec.conf"
fi

echo "[2] 清理 lo 上错误的 VPN 主机路由"
while read -r vip; do
  [[ -n "$vip" ]] && ip route del "${vip}/32" dev lo metric 50 2>/dev/null || true
done < <(ip route show table main 2>/dev/null | awk '/^10\.66\.0\./ {print $1}' | grep '/32')

echo "[3] 恢复最小 updown（只计流量，不做 per-client SNAT，避免拖垮 strongSwan）"
UPDOWN=/usr/local/sbin/hopworld-ikev2-updown
cat >"$UPDOWN" <<'UPDOWN_EOF'
#!/bin/bash
set -euo pipefail
POOL="${HOPWORLD_VPN_POOL:-10.66.0.0/24}"
CHAIN='HOPWORLD_VPN'
ensure_traffic_chain() {
  iptables -N "$CHAIN" 2>/dev/null || true
  iptables -C "$CHAIN" -j RETURN 2>/dev/null || iptables -A "$CHAIN" -j RETURN
  iptables -C FORWARD -s "$POOL" -j "$CHAIN" 2>/dev/null || iptables -I FORWARD 1 -s "$POOL" -j "$CHAIN"
  iptables -C FORWARD -d "$POOL" -j "$CHAIN" 2>/dev/null || iptables -I FORWARD 1 -d "$POOL" -j "$CHAIN"
}
traffic_up() {
  local vip="$1"
  [[ -n "$vip" ]] || return 0
  set +e
  ensure_traffic_chain
  iptables -C "$CHAIN" -s "$vip" -m comment --comment "hopworld-up:$vip" -j RETURN 2>/dev/null \
    || iptables -I "$CHAIN" 1 -s "$vip" -m comment --comment "hopworld-up:$vip" -j RETURN
  iptables -C "$CHAIN" -d "$vip" -m comment --comment "hopworld-down:$vip" -j RETURN 2>/dev/null \
    || iptables -I "$CHAIN" 1 -d "$vip" -m comment --comment "hopworld-down:$vip" -j RETURN
  set -e
}
traffic_down() {
  local vip="$1"
  [[ -n "$vip" ]] || return 0
  iptables -D "$CHAIN" -s "$vip" -m comment --comment "hopworld-up:$vip" -j RETURN 2>/dev/null || true
  iptables -D "$CHAIN" -d "$vip" -m comment --comment "hopworld-down:$vip" -j RETURN 2>/dev/null || true
}
case "${PLUTO_VERB:-}" in
  up|up-client|client-up)
    traffic_up "${PLUTO_PEER_CLIENT:-}"
    ;;
  down|down-client|client-down)
    traffic_down "${PLUTO_PEER_CLIENT:-}"
    ;;
esac
exit 0
UPDOWN_EOF
chmod 755 "$UPDOWN"

echo "[4] NAT + 转发（地址池 MASQUERADE，不再叠加复杂 hairpin 规则）"
if [[ -x /usr/local/sbin/hopworld-ikev2-apply ]]; then
  # 若 apply 带 hairpin，仍执行；稳定后以能连为准
  /usr/local/sbin/hopworld-ikev2-apply apply "$DATA"
else
  echo "missing hopworld-ikev2-apply" >&2
  exit 1
fi

echo "[5] 重启 strongSwan"
for u in strongswan strongswan-starter ipsec; do
  systemctl restart "$u" 2>/dev/null && break || true
done
sleep 1
systemctl is-active strongswan strongswan-starter hopworld-router 2>/dev/null || true
strongswan status 2>/dev/null | head -6 || true

echo "OK — 请手机删除 VPN 配置后重建，再连一次。管理后台建议用电脑或关 VPN 后打开手机浏览器。"
