#!/usr/bin/env bash
set -euo pipefail

main() (
  if [[ "$EUID" != 0 || "$(uname -s)" != Linux || ! -d /run/systemd/system ]]; then
    echo '请在使用 systemd 的 Linux 服务器上以 root 运行。' >&2
    exit 1
  fi
  if ! command -v apt-get >/dev/null; then
    echo '一键安装适用于 Ubuntu 22.04+ 或 Debian 12+。' >&2
    exit 1
  fi

  local app=/opt/hopworld-router
  local config=/etc/hopworld-router.env
  local unit=hopworld-router.service
  local unit_file=/etc/systemd/system/$unit
  local account=hopworld-router
  local data=/var/lib/hopworld-router
  local url=http://127.0.0.1:8080/
  local password response
  local app_created=0 config_created=0 unit_created=0

  exec 9>/run/hopworld-router-install.lock
  if ! flock -n 9; then
    echo '安装正在运行。' >&2
    exit 1
  fi
  if [[ -e "$app" || -L "$app" || -e "$config" || -L "$config" ||
        -e "$unit_file" || -L "$unit_file" ]] || systemctl cat "$unit" >/dev/null 2>&1; then
    echo '检测到已有安装。请使用 README 中的升级流程。' >&2
    exit 1
  fi

  cleanup() {
    local status=$?
    if (( status == 0 )); then
      return
    fi
    if (( unit_created )); then
      journalctl -u "$unit" -n 20 --no-pager >&2 || true
      systemctl disable --now "$unit" >/dev/null 2>&1 || true
      rm -f -- "$unit_file"
      systemctl daemon-reload || true
    fi
    if (( config_created )); then
      rm -f -- "$config"
    fi
    if (( app_created )); then
      rm -rf -- "$app"
    fi
    echo '安装失败，请根据上方错误排查后重试。' >&2
  }
  trap cleanup EXIT
  umask 022

  apt-get update
  apt-get install -y ca-certificates curl git python3-venv
  python3 -c 'import sys; sys.exit("需要 Python 3.10 或更新版本。") if sys.version_info < (3, 10) else None'

  mkdir "$app"
  app_created=1
  git clone --branch main https://github.com/ZaoHub/HopWorld-Router.git "$app"
  python3 -m venv "$app/.venv"
  "$app/.venv/bin/python" -m pip install -r "$app/requirements.txt"

  if ! id "$account" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$data" --shell /usr/sbin/nologin "$account"
  fi
  install -d -o "$account" -g "$account" -m 750 "$data"
  password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
  config_created=1
  (umask 077; printf 'HOPWORLD_ADMIN_PASSWORD=%s\nHOPWORLD_HOST=127.0.0.1\nHOPWORLD_PORT=8080\n' "$password" > "$config")

  unit_created=1
  install -m 644 "$app/deploy/$unit" "$unit_file"
  systemctl daemon-reload
  systemctl enable --now "$unit"
  response=$(curl --noproxy '*' --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --retry 5 --retry-connrefused --max-time 3 "$url")
  if [[ "$response" != 401 ]]; then
    echo "管理页面未要求登录（HTTP $response）。" >&2
    exit 1
  fi
  response=$(printf 'user = "admin:%s"\n' "$password" | curl --config - --noproxy '*' \
    --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 5 "$url")
  if [[ "$response" != 200 ]] || ! systemctl is-active --quiet "$unit"; then
    echo "管理服务未通过本机登录检查（HTTP $response）。" >&2
    exit 1
  fi

  printf '\n安装完成。管理账号 admin，密码 %s\n' "$password"
  printf '在自己的电脑上执行 ssh -N -L 18080:127.0.0.1:8080 root@服务器地址\n'
  printf '保持连接，然后打开 http://127.0.0.1:18080/\n'
)

main "$@"
