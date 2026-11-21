#!/usr/bin/env bash
# Run once as root after reviewing the checked-out helper and service file.
set -euo pipefail
test "$(id -u)" = 0 || { echo '请以 root 安装页面更新服务' >&2; exit 1; }
cd /opt/hopworld-router
command -v sqlite3 >/dev/null || { echo '请先安装 sqlite3' >&2; exit 1; }
install -d -m 755 /usr/local/libexec
install -o root -g root -m 755 deploy/hopworld-router-page-update /usr/local/libexec/hopworld-router-page-update
install -o root -g root -m 644 deploy/hopworld-router-update.service /etc/systemd/system/hopworld-router-update.service
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
printf '%s\n' 'hopworld-router ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block hopworld-router-update.service' > "$tmp"
visudo -cf "$tmp"
install -o root -g root -m 440 "$tmp" /etc/sudoers.d/hopworld-router-update
systemctl daemon-reload
install -d -o root -g root -m 755 /etc/hopworld-router
install -o root -g root -m 644 /dev/null /etc/hopworld-router/stable-release-updater
echo '页面更新服务已安装'
