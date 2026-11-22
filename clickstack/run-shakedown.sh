#!/usr/bin/env bash
# Run one SREGym problem with a ClickStack agent on the runner.
#   usage: clickstack/run-shakedown.sh <agent> <problem> [extra main.py args...]
#   e.g.   clickstack/run-shakedown.sh claudecode_clickstack_only_t2 service_wrong_pod_selection_hotel_reservation --stages diagnosis
# Needs ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN for the agent, and credentials for --judge-model (defaults below).
set -euo pipefail
AGENT="${1:?agent name from agents.yaml}"; PROBLEM="${2:?problem id}"; shift 2
cd "$(dirname "$0")/.."
set -a; . "$HOME/.config/sregym/sregym.env"; set +a
: "${AGENT_MODEL:=anthropic/claude-opus-5-5}"
: "${JUDGE_MODEL:=anthropic/claude-sonnet-5-5}"
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN for the agent (and the judge's provider key)" >&2; exit 2
fi
systemctl is-active --quiet sregym-mcp-proxy || { echo "sregym-mcp-proxy is not running" >&2; exit 2; }
exec uv run main.py --agent "$AGENT" --problem "$PROBLEM" --model "$AGENT_MODEL" --judge-model "$JUDGE_MODEL" \
  --allow-agent-endpoint http://host.docker.internal:9300 "$@"
