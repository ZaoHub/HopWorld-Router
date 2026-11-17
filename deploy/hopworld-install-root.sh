#!/bin/bash
# One-time: let the admin service run deploy/hopworld-root without a password.
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 运行" >&2
  exit 1
fi
id hopworld-router >/dev/null 2>&1 || { echo "请先安装 HopWorld Router 服务" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")" && pwd)"
install -m 755 "$ROOT/hopworld-root" /usr/local/sbin/hopworld-root
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
printf '%s\n' 'hopworld-router ALL=(root) NOPASSWD: /usr/local/sbin/hopworld-root' > "$tmp"
visudo -cf "$tmp"
install -m 440 "$tmp" /etc/sudoers.d/hopworld-router-root
# 命令装不上不中断本脚本，管理页仍可在线安装。
if ! /usr/local/sbin/hopworld-root sing-box; then
  echo "sing-box 未装上，可稍后在管理页在线安装" >&2
fi
echo "已授权管理页执行系统组件安装和本机防火墙放行"
