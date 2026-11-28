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

exec 9>/var/lock/hopworld-router-update.lock
flock -n 9 || { echo '更新正在进行，请稍后再试' >&2; exit 1; }

runtime_conf=/etc/systemd/system/hopworld-router.service.d/runtime.conf
install -d -m 755 "$runtime_root" "$(dirname "$runtime_conf")"
backup=$(mktemp -d)
committed=0
changed=0
was_active=0
systemctl is-active --quiet hopworld-router.service && was_active=1
if [[ -f "$runtime_conf" ]]; then cp -p "$runtime_conf" "$backup/runtime.conf"; fi
for name in current state.json; do
  if [[ -e "$runtime_root/$name" || -L "$runtime_root/$name" ]]; then
    cp -a "$runtime_root/$name" "$backup/$name"
  fi
done
finish() {
  if (( changed && !committed )); then
    for name in current state.json; do
      if [[ -e "$backup/$name" || -L "$backup/$name" ]]; then
        cp -a --remove-destination "$backup/$name" "$runtime_root/$name"
      else
        unlink "$runtime_root/$name" 2>/dev/null || true
      fi
    done
    if [[ -f "$backup/runtime.conf" ]]; then cp -p "$backup/runtime.conf" "$runtime_conf"
    else unlink "$runtime_conf" 2>/dev/null || true; fi
    systemctl daemon-reload
    if (( was_active )); then systemctl restart hopworld-router.service; fi
  fi
  for name in current state.json runtime.conf new.conf; do
    if [[ -e "$backup/$name" || -L "$backup/$name" ]]; then unlink "$backup/$name"; fi
  done
  rmdir "$backup"
}
trap finish EXIT
changed=1
HOPWORLD_RUNTIME_ROOT=$runtime_root HOPWORLD_DPKG_SEED=$seed PYTHONPATH=$seed /usr/bin/python3 - <<'PYTHON'
import os
import re
from pathlib import Path
from router.runtime_layout import migrate_from_seed

seed = Path(os.environ['HOPWORLD_DPKG_SEED'])
commit = os.environ.get('HOPWORLD_SOURCE_COMMIT', '').strip()
if not commit and (seed / 'SOURCE_COMMIT').is_file():
    commit = (seed / 'SOURCE_COMMIT').read_text(encoding='ascii').strip()
if not re.fullmatch(r'[0-9a-f]{40}', commit):
    commit = ''
migrate_from_seed(seed=seed, package_version=os.environ.get('HOPWORLD_PACKAGE_VERSION', ''), commit=commit)
PYTHON

cat > "$backup/new.conf" <<EOF
# Managed by hopworld-install-runtime.sh — do not edit package unit files.
[Service]
WorkingDirectory=$runtime_root/current
Environment=HOPWORLD_RUNTIME_ROOT=$runtime_root
EOF
install -m 0644 "$backup/new.conf" "$runtime_conf"
unlink "$backup/new.conf"
systemctl daemon-reload
if (( was_active )); then
  systemctl restart hopworld-router.service
  PYTHONPATH=$seed /usr/bin/python3 -c 'from router.system_update import require_service_ready; require_service_ready()'
fi
committed=1
echo "独立运行时已启用：$runtime_root/current"
