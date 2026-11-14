#!/bin/bash
# Install boot units — run once on the VPS as root.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
install -m 755 "$ROOT/hopworld-boot" /usr/local/sbin/hopworld-boot
install -m 644 "$ROOT/hopworld-boot.service" /etc/systemd/system/hopworld-boot.service
[[ -f /etc/systemd/system/hopworld-router.service ]] || install -m 644 "$ROOT/hopworld-router.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable hopworld-boot.service hopworld-router.service
if command -v /usr/local/sbin/hopworld-ikev2-apply &>/dev/null; then
  for unit in strongswan strongswan-starter ipsec; do
    if systemctl cat "${unit}.service" &>/dev/null; then
      systemctl enable "$unit"
      break
    fi
  done
fi
systemctl start hopworld-boot.service || true
systemctl start hopworld-router.service || true
echo "enabled: hopworld-boot hopworld-router (and strongSwan if present)"
