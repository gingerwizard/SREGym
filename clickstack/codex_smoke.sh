#!/usr/bin/env bash
# Container-level check of the Codex arm without touching the cluster:
#   1. install the pinned Codex CLI inside the agent image (same installer and 7-day npm rule as a real run)
#   2. reach the ClickStack MCP auth proxy from inside a container
#   3. run one Codex turn that calls clickstack_list_sources through MCP with the configured model
#
#   bash clickstack/codex_smoke.sh [model]        (default gpt-6.1-astra; reads ~/.config/sregym/sregym.env)
set -uo pipefail
MODEL=${1:-gpt-6.1-astra}
IMAGE=${SREGYM_AGENT_IMAGE:-sregym-agent-base:latest}
VERSION=${CODEX_VERSION:-0.166.0}
MCP_URL=${CLICKSTACK_MCP_URL:-http://host.docker.internal:9300/mcp}
set -a; . "$HOME/.config/sregym/sregym.env"; set +a
if [ -z "${OPENAI_API_KEY:-}" ]; then echo "OPENAI_API_KEY is not set in ~/.config/sregym/sregym.env"; exit 2; fi
docker run --rm --add-host host.docker.internal:host-gateway \
    -e OPENAI_API_KEY -e OPENAI_BASE_URL="${OPENAI_BASE_URL:-}" -e AGENT_VERSION="$VERSION" -e MODEL="$MODEL" -e MCP_URL="$MCP_URL" \
    "$IMAGE" '
set -u
echo "== 1. install Codex CLI $AGENT_VERSION"
if /opt/sregym/install-scripts/install-codex.sh >/tmp/install.log 2>&1; then tail -1 /tmp/install.log; else echo "INSTALL FAILED"; tail -20 /tmp/install.log; exit 1; fi
echo "== 2. MCP proxy handshake from inside the container"
python3 /opt/sregym/clients/claudecode_clickstack/mcp_smoke.py --url "$MCP_URL" 2>&1 | tail -3 || echo "MCP HANDSHAKE FAILED"
echo "== 3. one Codex turn through MCP with model $MODEL"
export CODEX_HOME=/tmp/codex-home; mkdir -p "$CODEX_HOME" /tmp/work
python3 -c "import json,os; json.dump({\"OPENAI_API_KEY\": os.environ[\"OPENAI_API_KEY\"]}, open(os.environ[\"CODEX_HOME\"] + \"/auth.json\", \"w\"))"
[ -n "${OPENAI_BASE_URL:-}" ] || unset OPENAI_BASE_URL
cd /tmp/work
timeout 240 codex exec --skip-git-repo-check -m "$MODEL" \
    -c "mcp_servers.clickstack.url=\"$MCP_URL\"" -c mcp_servers.clickstack.startup_timeout_sec=60 \
    "Call the clickstack_list_sources tool once and reply with only the number of sources it returned." 2>&1 | grep -v "^\s*$" | tail -12
echo "codex exit status: ${PIPESTATUS[0]}"
' 2>&1 | sed -E "s/(provided: )[^ ]+/\1<redacted>/"
