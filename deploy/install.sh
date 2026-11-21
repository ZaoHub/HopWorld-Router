#!/usr/bin/env bash
set -euo pipefail

main() (
  local version=1.0.2
  if [[ "$EUID" != 0 || "$(uname -s)" != Linux || ! -d /run/systemd/system ]]; then
    echo '请在使用 systemd 的 Linux 服务器上以 root 运行。' >&2
    exit 1
  fi
  . /etc/os-release
  case "$ID:$VERSION_ID" in
    ubuntu:18.04|centos:7)
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
  apt-get install -y "./$package"
  systemctl is-active --quiet hopworld-router.service
  curl --noproxy '*' --fail --silent --show-error --output /dev/null \
    --retry 5 --retry-connrefused --retry-all-errors --max-time 5 http://127.0.0.1:8080/health/ready
  echo 'V1 管理服务已就绪。'
)

main "$@"
