#!/usr/bin/env bash
set -euo pipefail

main() (
  local version=1.0.5 public_admin_host='' public_admin_script=''
  if (( $# > 0 )); then
    if [[ $# != 2 || $1 != --public-admin ]]; then
      echo '用法：bash install.sh [--public-admin 公网IPv4或域名]' >&2
      exit 2
    fi
    public_admin_host=$2
    if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
      local deploy_dir
      deploy_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
      if [[ -f "$deploy_dir/hopworld-enable-public-admin.sh" ]]; then
        public_admin_script="$deploy_dir/hopworld-enable-public-admin.sh"
      fi
    fi
  fi
  if [[ "$EUID" != 0 || "$(uname -s)" != Linux || ! -d /run/systemd/system ]]; then
    echo '请在使用 systemd 的 Linux 服务器上以 root 运行。' >&2
    exit 1
  fi
  . /etc/os-release
  case "$ID:$VERSION_ID" in
    ubuntu:18.04|centos:7)
      if [[ -n "$public_admin_host" ]]; then
        echo '--public-admin 需要 Ubuntu 22.04+ 或 Debian 12+。旧系统可以安装本地后台。' >&2
        exit 2
      fi
      curl -fsSL "https://raw.githubusercontent.com/ZaoHub/HopWorld-Router/v$version/deploy/install-legacy.sh" |
        bash -s -- "$version"
      exit
      ;;
  esac
  if ! command -v apt-get >/dev/null; then
    echo '请使用受支持的 Ubuntu、Debian 或 CentOS 7 系统。' >&2
    exit 1
  fi

  local package=hopworld-router_${version}_all.deb
  local release=https://github.com/ZaoHub/HopWorld-Router/releases/download/v${version}
  local workdir
  workdir=$(mktemp -d)
  trap 'rm -rf -- "$workdir"' EXIT
  chmod 755 "$workdir"
  cd "$workdir"

  apt-get update
  apt-get install -y ca-certificates curl
  curl --fail --silent --show-error --location --retry 3 --max-time 120 --output "$package" "$release/$package"
  curl --fail --silent --show-error --location --retry 3 --max-time 120 --output "$package.sha256" "$release/$package.sha256"
  sha256sum --check --strict "$package.sha256"
  if [[ -n "$public_admin_host" ]]; then
    # The released package prints its access instructions from postinst.
    # Defer the summary until the firewall and public HTTPS setup are complete.
    echo '正在安装 HopWorld Router 软件包…'
    apt-get -o Dpkg::Use-Pty=0 install -y "./$package" >/dev/null
  else
    apt-get install -y "./$package"
  fi
  systemctl is-active --quiet hopworld-router.service
  curl --noproxy '*' --fail --silent --show-error --output /dev/null \
    --retry 5 --retry-connrefused --retry-all-errors --max-time 5 http://127.0.0.1:8080/health/ready
  if [[ -n "$public_admin_host" ]]; then
    if [[ -n "$public_admin_script" ]]; then
      bash "$public_admin_script" "$public_admin_host"
    else
      curl -fsSL "https://raw.githubusercontent.com/ZaoHub/HopWorld-Router/v$version/deploy/hopworld-enable-public-admin.sh" |
        bash -s -- "$public_admin_host"
    fi
  else
    echo 'V1 管理服务已就绪。'
    echo '本地管理地址：http://127.0.0.1:8080/'
    echo '外网管理入口可通过 --public-admin 公网IPv4或域名配置。'
  fi
)

main "$@"
