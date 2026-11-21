#!/usr/bin/env bash
# Run once on the project-managed systemd installation.
set -euo pipefail
test "$(id -u)" = 0 || { echo '请以 root 配置页面自启动权限' >&2; exit 1; }
cd /opt/hopworld-router
id hopworld-router >/dev/null
test -f /etc/systemd/system/hopworld-router.service
test -x /usr/bin/systemctl
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
printf '%s\n' 'hopworld-router ALL=(root) NOPASSWD: /usr/bin/systemctl enable hopworld-router.service, /usr/bin/systemctl disable hopworld-router.service' > "$tmp"
visudo -cf "$tmp"
if test -f /usr/local/sbin/hopworld-boot && grep -Fq 'systemctl enable hopworld-router.service hopworld-boot.service' /usr/local/sbin/hopworld-boot; then
  install -o root -g root -m 755 deploy/hopworld-boot /usr/local/sbin/hopworld-boot
fi
install -o root -g root -m 440 "$tmp" /etc/sudoers.d/hopworld-router-autostart
echo '页面自启动权限已配置'
