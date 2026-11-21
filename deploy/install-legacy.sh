#!/usr/bin/env bash
set -euo pipefail

ready() {
  local attempt
  for ((attempt=0; attempt<20; attempt++)); do
    if curl --noproxy '*' --fail --silent --max-time 2 http://127.0.0.1:8080/health/ready >/dev/null &&
       systemctl is-active --quiet hopworld-router.service; then
      return 0
    fi
    sleep 1
  done
  return 1
}

main() (
  local version=${1:-}
  [[ "$version" =~ ^1\.[0-9]+\.[0-9]+$ && "$EUID" == 0 && -d /run/systemd/system ]] || exit 1
  . /etc/os-release
  case "$ID:$VERSION_ID" in
    ubuntu:18.04|centos:7) ;;
    *) echo '此安装流程适用于 Ubuntu 18.04 和 CentOS 7。' >&2; exit 1 ;;
  esac

  local arch digest
  arch=$(uname -m)
  case "$arch" in
    x86_64) digest=3288c54cea3db350052019b3f8b558dd8a3da8afa6fda573f5fd2bcb37635b4a ;;
    aarch64) digest=7e71f26a003949b57757e287f4f4b6689da07e7a229f5bbee7919c19ed77155d ;;
    *) echo '旧系统安装支持 x86_64 和 aarch64。' >&2; exit 1 ;;
  esac
  local tool
  for tool in curl tar gzip sha256sum flock useradd systemctl; do
    command -v "$tool" >/dev/null || { echo "缺少系统工具：$tool" >&2; exit 1; }
  done

  local root=/opt/hopworld-router-legacy
  local data=/var/lib/hopworld-router
  local unit=/etc/systemd/system/hopworld-router.service
  local release=$root/releases/$version
  local python_url=https://github.com/astral-sh/python-build-standalone/releases/download/20261110
  local python_archive=cpython-3.11.18%2B20261110-${arch}-unknown-linux-gnu-install_only_stripped.tar.gz
  local workdir previous='' was_active=0 switched=0 database_saved=0

  umask 022
  exec 9>/run/hopworld-router-install.lock
  flock -n 9 || { echo '另一次安装正在运行。' >&2; exit 1; }
  if [[ -L "$root/current" && -f "$unit" ]] && grep -Fxq "WorkingDirectory=$root/current" "$unit"; then
    previous=$(readlink "$root/current")
    if [[ "$previous" == "$release" ]]; then
      systemctl enable hopworld-router.service
      systemctl start hopworld-router.service
      ready
      echo "V$version 管理服务已就绪。"
      exit
    fi
  elif [[ -e "$root/current" || -L "$root/current" || -e /opt/hopworld-router || -e "$unit" ]] ||
       systemctl cat hopworld-router.service >/dev/null 2>&1; then
    echo '检测到其他方式安装的 HopWorld Router，请先备份并迁移。' >&2
    exit 1
  fi

  install -d -m 755 "$root/releases"
  workdir=$(mktemp -d "$root/.install.XXXXXXXX")
  cleanup() {
    local result=$?
    if (( result != 0 && switched )); then
      journalctl -u hopworld-router.service -n 20 --no-pager >&2 || true
      systemctl stop hopworld-router.service || true
      if (( database_saved )); then
        rm -f -- "$data/router.db-wal" "$data/router.db-shm"
        install -o hopworld-router -g hopworld-router -m 600 "$workdir/router.db" "$data/router.db"
      fi
      if [[ -n "$previous" ]]; then
        rm -f -- "$workdir/current"
        ln -s "$previous" "$workdir/current"
        mv -Tf "$workdir/current" "$root/current"
        install -m 644 "$workdir/previous.service" "$unit"
        systemctl daemon-reload
        if (( was_active )); then systemctl start hopworld-router.service; fi
      else
        systemctl disable hopworld-router.service >/dev/null 2>&1 || true
        rm -f -- "$unit" "$root/current"
        systemctl daemon-reload
      fi
      rm -rf -- "$release"
    fi
    rm -rf -- "$workdir"
  }
  trap cleanup EXIT

  local stage=$workdir/release
  mkdir -p "$stage/python"
  curl -fLsS --retry 3 --max-time 180 -o "$workdir/router.tar.gz" \
    "https://codeload.github.com/ZaoHub/HopWorld-Router/tar.gz/refs/tags/v$version"
  curl -fLsS --retry 3 --max-time 600 -o "$workdir/python.tar.gz" "$python_url/$python_archive"
  printf '%s  %s\n' "$digest" "$workdir/python.tar.gz" | sha256sum -c -
  tar --no-same-owner --strip-components=1 -xzf "$workdir/router.tar.gz" -C "$stage"
  tar --no-same-owner --strip-components=1 -xzf "$workdir/python.tar.gz" -C "$stage/python"
  local py=$stage/python/bin/python3
  (cd "$stage"; "$py" -c 'import ssl, sqlite3, sys; from router.version import VERSION; assert VERSION == sys.argv[1]' "$version")
  if [[ -n "$previous" ]]; then
    "$py" - "$version" "${previous##*/}" <<'PYTHON'
import sys

requested, installed = [tuple(map(int, value.split('.'))) for value in sys.argv[1:]]
if requested <= installed:
    raise SystemExit('请使用更新版本。')
PYTHON
    cp -- "$unit" "$workdir/previous.service"
  fi
  "$py" -m pip --disable-pip-version-check install -r "$stage/requirements.txt"
  if ! id hopworld-router >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$data" --shell /sbin/nologin hopworld-router
  fi
  install -d -m 750 -o hopworld-router -g hopworld-router "$data"
  [[ ! -e "$release" && ! -L "$release" ]] || { echo "已存在版本目录：$release" >&2; exit 1; }

  if systemctl is-active --quiet hopworld-router.service; then was_active=1; fi
  if [[ -n "$previous" ]]; then systemctl stop hopworld-router.service; fi
  if [[ -f "$data/router.db" ]]; then
    if ! "$py" - "$data/router.db" "$workdir/router.db" <<'PY'
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

with closing(sqlite3.connect(Path(sys.argv[1]).as_uri() + '?mode=ro', uri=True)) as source:
    with closing(sqlite3.connect(sys.argv[2])) as target:
        source.backup(target)
PY
    then
      if (( was_active )); then systemctl start hopworld-router.service; fi
      exit 1
    fi
    database_saved=1
  fi
  switched=1
  mv -- "$stage" "$release"
  ln -s "$release" "$workdir/current"
  mv -Tf "$workdir/current" "$root/current"
  install -m 644 "$release/deploy/hopworld-router-legacy.service" "$unit"
  systemctl daemon-reload
  systemctl enable hopworld-router.service
  systemctl start hopworld-router.service
  ready || { echo '后台未通过就绪检查，正在恢复。' >&2; exit 1; }
  echo "V$version 管理服务已就绪，监听 127.0.0.1:8080。"
  echo '新安装首次以 admin / admin 登录并设置密码。'
)

main "$@"
