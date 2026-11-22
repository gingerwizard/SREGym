#!/usr/bin/env bash
# Pre-deploy hook: empty the ClickStack tables so every problem attempt starts with no
# telemetry, matching SREGym's per-problem wipe of the observe namespace.
#
# Verification: after TRUNCATE (waiting for every replica, alter_sync=2) no active data part
# created before the truncation may remain. Rows the collectors write after the truncation are
# expected and allowed. The earlier check, count() == 0 right after TRUNCATE, failed whenever the
# cluster was busy (teardown of the previous problem) and cost whole deploy attempts.
set -euo pipefail
ENV_FILE="${CLICKSTACK_ENV_FILE:-$HOME/.config/sregym/clickstack.env}"
set -a; . "$ENV_FILE"; set +a
db="${CLICKSTACK_DATABASE:-default}"
tables="otel_logs otel_traces otel_metrics_gauge otel_metrics_sum otel_metrics_histogram otel_metrics_exponential_histogram otel_metrics_summary hyperdx_sessions"
ch() { curl -sS --fail-with-body --max-time 90 --user "$CLICKHOUSE_ADMIN_USER:$CLICKHOUSE_ADMIN_PASSWORD" --data-binary "$1" "${CLICKHOUSE_ENDPOINT}/?${2:-}"; }
in_list=$(printf "'%s'," $tables); in_list=${in_list%,}
for round in 1 2 3; do
  t0=$(ch "SELECT toString(now())")
  for t in $tables; do
    out=$(ch "TRUNCATE TABLE IF EXISTS ${db}.${t}" "alter_sync=2" 2>&1) || { echo "truncate ${db}.${t} failed: $out" >&2; exit 1; }
  done
  old=$(ch "SELECT count() FROM system.parts WHERE database = '${db}' AND table IN (${in_list}) AND active AND modification_time < toDateTime('${t0}')")
  if [ "${old:-1}" = "0" ]; then
    rows=$(ch "SELECT sum(rows) FROM system.parts WHERE database = '${db}' AND table IN (${in_list}) AND active")
    echo "truncated clickstack tables in ${db} at ${t0} (no parts older than the truncation remain; ${rows:-0} new rows arrived since)"
    exit 0
  fi
  echo "round ${round}: ${old} part(s) created before ${t0} still active, truncating again" >&2
  sleep 5
done
echo "tables not empty after truncation: parts created before the truncation remain after 3 rounds" >&2
exit 1
