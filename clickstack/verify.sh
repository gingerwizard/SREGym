#!/usr/bin/env bash
# Summarise what has arrived in ClickStack over the last N minutes (default 15), per signal.
set -euo pipefail
ENV_FILE="${CLICKSTACK_ENV_FILE:-$HOME/.config/sregym/clickstack.env}"
set -a; . "$ENV_FILE"; set +a
MIN="${1:-15}"
q() { curl -sS --user "$CLICKHOUSE_READONLY_USER:$CLICKHOUSE_READONLY_PASSWORD" --data-binary "$1" "$CLICKHOUSE_ENDPOINT"; }
echo "== logs by service (last ${MIN}m)"
q "SELECT ServiceName, count() c, max(Timestamp) last FROM default.otel_logs WHERE TimestampTime > now() - INTERVAL ${MIN} MINUTE GROUP BY ServiceName ORDER BY c DESC LIMIT 25 FORMAT PrettyCompactNoEscapes"
echo "== logs by namespace (last ${MIN}m)"
q "SELECT ResourceAttributes['k8s.namespace.name'] ns, count() c FROM default.otel_logs WHERE TimestampTime > now() - INTERVAL ${MIN} MINUTE GROUP BY ns ORDER BY c DESC LIMIT 20 FORMAT PrettyCompactNoEscapes"
echo "== k8s object records by kind (last ${MIN}m)"
q "SELECT ServiceName, LogAttributes['k8s.object.kind'] kind, count() c, uniq(LogAttributes['k8s.object.uid']) objects FROM default.otel_logs WHERE TimestampTime > now() - INTERVAL ${MIN} MINUTE AND ServiceName IN ('k8s-objects','k8s-changes','k8s-events') GROUP BY ServiceName, kind ORDER BY ServiceName, c DESC LIMIT 60 FORMAT PrettyCompactNoEscapes"
echo "== hidden-namespace leak check (must be empty)"
q "SELECT ServiceName, ResourceAttributes['k8s.namespace.name'] ns, LogAttributes['k8s.namespace.name'] ans, count() FROM default.otel_logs WHERE TimestampTime > now() - INTERVAL ${MIN} MINUTE AND (ns IN ('khaos','chaos-mesh') OR ans IN ('khaos','chaos-mesh') OR Body ILIKE '%chaos-mesh%') GROUP BY ALL LIMIT 10 FORMAT PrettyCompactNoEscapes"
echo "== metrics by name prefix (last ${MIN}m)"
q "SELECT substring(MetricName, 1, position(MetricName, '.') ) prefix, count() c, uniq(MetricName) names FROM (SELECT MetricName FROM default.otel_metrics_gauge WHERE TimeUnix > now() - INTERVAL ${MIN} MINUTE UNION ALL SELECT MetricName FROM default.otel_metrics_sum WHERE TimeUnix > now() - INTERVAL ${MIN} MINUTE UNION ALL SELECT MetricName FROM default.otel_metrics_histogram WHERE TimeUnix > now() - INTERVAL ${MIN} MINUTE) GROUP BY prefix ORDER BY c DESC LIMIT 25 FORMAT PrettyCompactNoEscapes"
echo "== traces by service (last ${MIN}m)"
q "SELECT ServiceName, count() spans, uniq(TraceId) traces FROM default.otel_traces WHERE Timestamp > now() - INTERVAL ${MIN} MINUTE GROUP BY ServiceName ORDER BY spans DESC LIMIT 20 FORMAT PrettyCompactNoEscapes"
echo "== table sizes"
q "SELECT name, total_rows, formatReadableSize(total_bytes) size FROM system.tables WHERE database='default' AND name LIKE 'otel_%' ORDER BY name FORMAT PrettyCompactNoEscapes"
