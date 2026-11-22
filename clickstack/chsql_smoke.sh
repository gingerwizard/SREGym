#!/usr/bin/env bash
# Container-level check of the ClickHouse-SQL arms without touching the cluster:
#   1. reach the ClickHouse MCP server (mcp-clickhouse on the runner) from inside the agent image
#   2. one Codex turn that runs a SQL query through it
#   3. one Claude Code turn that runs a SQL query through it, with the client's allowlist
#
#   bash clickstack/chsql_smoke.sh [openai-model] [claude-model]
set -uo pipefail
OPENAI_MODEL=${1:-gpt-6.1-sol}
CLAUDE_MODEL=${2:-claude-fable-5-2}
IMAGE=${SREGYM_AGENT_IMAGE:-sregym-agent-base:latest}
MCP_URL=${CHSQL_MCP_URL:-http://host.docker.internal:9400/mcp}
set -a; . "$HOME/.config/sregym/sregym.env"; set +a
docker run --rm --add-host host.docker.internal:host-gateway \
    -e OPENAI_API_KEY -e ANTHROPIC_API_KEY -e OPENAI_MODEL="$OPENAI_MODEL" -e CLAUDE_MODEL="$CLAUDE_MODEL" -e MCP_URL="$MCP_URL" \
    "$IMAGE" '
set -u
Q="SELECT count() AS rows FROM default.otel_logs WHERE TimestampTime >= now() - INTERVAL 1 HOUR"
echo "== 1. ClickHouse MCP handshake from inside the container"
python3 /opt/sregym/clients/claudecode_clickstack/mcp_smoke.py --url "$MCP_URL" 2>&1 | tail -4
echo "== 2. one Codex turn through run_query ($OPENAI_MODEL)"
AGENT_VERSION=0.166.0 /opt/sregym/install-scripts/install-codex.sh >/tmp/install-codex.log 2>&1 || { echo "codex install failed"; tail -5 /tmp/install-codex.log; }
export CODEX_HOME=/tmp/codex-home; mkdir -p "$CODEX_HOME" /tmp/work; python3 -c "import json,os; json.dump({\"OPENAI_API_KEY\": os.environ[\"OPENAI_API_KEY\"]}, open(os.environ[\"CODEX_HOME\"] + \"/auth.json\", \"w\"))"
# the stock SREGym Codex client runs exec with --dangerously-bypass-approvals-and-sandbox; without it Codex refuses MCP tools that carry no readOnlyHint
cd /tmp/work && timeout 240 codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -m "$OPENAI_MODEL" -c "mcp_servers.clickhouse.url=\"$MCP_URL\"" -c mcp_servers.clickhouse.startup_timeout_sec=60 \
    "Use the clickhouse MCP run_query tool to run exactly this SQL and reply with only the number it returns: $Q" 2>&1 | grep -v "bubblewrap\|failed to refresh available models" | tail -6
echo "== 3. one Claude Code turn through mcp__clickhouse__run_query ($CLAUDE_MODEL)"
AGENT_VERSION=2.1.318 /opt/sregym/install-scripts/install-claudecode.sh >/tmp/install-claude.log 2>&1 || { echo "claude install failed"; tail -5 /tmp/install-claude.log; }
printf "{\"mcpServers\": {\"clickhouse\": {\"type\": \"http\", \"url\": \"%s\"}}}" "$MCP_URL" > /tmp/mcp.json
export CLAUDE_CONFIG_DIR=/tmp/claude-home; mkdir -p "$CLAUDE_CONFIG_DIR"
timeout 240 claude -p "Use the mcp__clickhouse__run_query tool to run exactly this SQL and reply with only the number it returns: $Q" \
    --model "$CLAUDE_MODEL" --mcp-config /tmp/mcp.json --strict-mcp-config --permission-mode dontAsk \
    --allowedTools mcp__clickhouse__run_query mcp__clickhouse__list_tables mcp__clickhouse__list_databases --output-format text 2>&1 | tail -3
' 2>&1
