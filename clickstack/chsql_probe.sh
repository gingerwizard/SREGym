#!/usr/bin/env bash
# Run ON A RUNNER: probe the local ClickHouse MCP server the way the agent containers use it.
# Handshake, one real query against this cluster's database, one over-cap query (must fail with code 396),
# one attempt to lift the cap from the query text (must fail with code 164), and list_tables.
#   bash clickstack/chsql_probe.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
URL=${CHSQL_PROBE_URL:-http://172.17.0.1:9400/mcp}
DB=$(sed -nE 's/^CLICKHOUSE_DATABASE=(.*)$/\1/p' "$HOME/.config/sregym/chsql-mcp.env"); DB=${DB:-default}
smoke() { uv run python clients/claudecode_clickstack/mcp_smoke.py --url "$URL" "$@" 2>&1; }
echo "handshake: $(smoke | tr -d '\n ' | cut -c1-120)"
echo "count ($DB):  $(smoke --call run_query --args "{\"query\": \"SELECT count() AS rows FROM $DB.otel_logs WHERE TimestampTime >= now() - INTERVAL 1 HOUR\"}" | tr -d '\n' | cut -c1-160)"
echo "1000 rows:    $(smoke --call run_query --args '{"query": "SELECT number FROM numbers(1000)"}' | tr -d '\n' | cut -c1-160)"
echo "lift cap:     $(smoke --call run_query --args '{"query": "SELECT number FROM numbers(1000) SETTINGS max_result_rows = 0"}' | tr -d '\n' | cut -c1-160)"
echo "list_tables:  $(smoke --call list_tables --args "{\"database\": \"$DB\"}" | tr -d '\n' | cut -c1-120)"
