#!/usr/bin/env bash
# Enable the independent runtime tree for APT or Git installs without patching dpkg files.
# Copies /usr/lib/hopworld-router (or /opt/hopworld-router) into
# /var/lib/hopworld-router-runtime/releases/… and points systemd at current via drop-in.
set -euo pipefail
test "$(id -u)" = 0 || { echo '请以 root 安装独立运行时' >&2; exit 1; }

runtime_root=${HOPWORLD_RUNTIME_ROOT:-/var/lib/hopworld-router-runtime}
seed=${HOPWORLD_DPKG_SEED:-}
if [[ -z "$seed" ]]; then
  if [[ -d /usr/lib/hopworld-router/router ]]; then
    seed=/usr/lib/hopworld-router
  elif [[ -d /opt/hopworld-router/router ]]; then
    seed=/opt/hopworld-router
  else
    echo '未找到程序种子目录' >&2
    exit 1
  fi
fi

install -d -m 755 "$runtime_root" /etc/systemd/system/hopworld-router.service.d
python3 - <<PY
import os
from pathlib import Path
import sys
sys.path.insert(0, "$seed")
from router.runtime_layout import migrate_from_seed
os.environ['HOPWORLD_RUNTIME_ROOT'] = "$runtime_root"
os.environ['HOPWORLD_DPKG_SEED'] = "$seed"
pkg = os.environ.get('HOPWORLD_PACKAGE_VERSION', '')
if not pkg:
    try:
        from router.version import VERSION
        pkg = VERSION
    except Exception:
        pkg = '0'
commit = os.environ.get('HOPWORLD_SOURCE_COMMIT', '')
print(migrate_from_seed(seed=Path("$seed"), root=Path("$runtime_root"), package_version=pkg, commit=commit))
PY

cat > /etc/systemd/system/hopworld-router.service.d/runtime.conf <<EOF
# Managed by hopworld-install-runtime.sh — do not edit package unit files.
[Service]
WorkingDirectory=$runtime_root/current
Environment=HOPWORLD_RUNTIME_ROOT=$runtime_root
EOF

systemctl daemon-reload
if systemctl is-active --quiet hopworld-router.service; then
  systemctl restart hopworld-router.service
fi
echo "独立运行时已启用：$runtime_root/current"