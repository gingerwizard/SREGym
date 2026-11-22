#!/usr/bin/env bash
# Install the MCP auth proxy as a systemd service on the runner (run with sudo).
set -euo pipefail
U="${SUDO_USER:-sregym}"; H=$(getent passwd "$U" | cut -d: -f6)
cat > /etc/systemd/system/sregym-mcp-proxy.service <<UNIT
[Unit]
Description=SREGym ClickStack MCP auth proxy (OAuth holder for agent containers)
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
User=$U
EnvironmentFile=$H/.config/sregym/clickstack.env
ExecStart=$H/.local/bin/uv run --project $H/SREGym python $H/SREGym/clients/claudecode_clickstack/mcp_auth_proxy.py --listen 172.17.0.1:9300 --header "x-service-id: \${CLICKSTACK_SERVICE_ID}"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now sregym-mcp-proxy.service
sleep 3
systemctl --no-pager --lines=5 status sregym-mcp-proxy.service | sed -n 1,12p
