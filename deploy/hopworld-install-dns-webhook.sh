#!/usr/bin/env bash
# Install HopWorld DNS webhook sidecar (systemd). Run as root.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE=/etc/hopworld-dns-webhook.env
UNIT=/etc/systemd/system/hopworld-dns-webhook.service

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 运行" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cat >"$ENV_FILE" <<'EOF'
# HOPWORLD_DNS_BACKEND=cloudflare
# CF_API_TOKEN=
# HOPWORLD_DNS_WEBHOOK_HOST=127.0.0.1
# HOPWORLD_DNS_WEBHOOK_PORT=9876
HOPWORLD_DNS_BACKEND=cloudflare
EOF
  chmod 600 "$ENV_FILE"
  echo "已创建 $ENV_FILE — 请填入厂商密钥后 systemctl restart hopworld-dns-webhook"
fi

cat >"$UNIT" <<EOF
[Unit]
Description=HopWorld DNS webhook (DDNS / ACME TXT)
After=network.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python $ROOT/deploy/dns-webhook/server.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hopworld-dns-webhook
systemctl restart hopworld-dns-webhook
sleep 1
systemctl is-active hopworld-dns-webhook
curl -sS -o /dev/null -w 'health=%{http_code}\n' http://127.0.0.1:9876/health || true
echo "Router 请设置 HOPWORLD_DDNS_WEBHOOK_URL=http://127.0.0.1:9876/"
