---
name: clickhouse-sql
description: How this cluster's telemetry and Kubernetes state are stored in ClickHouse and how to query them with SQL through the clickhouse MCP tool. Read before the first query. Covers container logs, Kubernetes Events, node logs, metrics and traces.
---

# Investigating this cluster with SQL on ClickHouse

This skill describes what `mcp__clickhouse__run_query` can see in this environment and the SQL
patterns that work. It describes the data, not any particular incident.

## What is stored and where

All telemetry for this cluster is in a ClickHouse database called `default` and is read with the
`mcp__clickhouse__run_query` tool (plain SQL, read-only). `mcp__clickhouse__list_tables` with
`database: default` lists the tables; `DESCRIBE default.<table>` shows the columns.

| Table | Holds |
|---|---|
| `default.otel_logs` | container logs, Kubernetes Events, and the Kubernetes state records described below |
| `default.otel_traces` | spans from instrumented services |
| `default.otel_metrics_gauge`, `default.otel_metrics_sum`, `default.otel_metrics_histogram` | cluster, node, container, probe and application metrics, one table per metric kind |

Columns are PascalCase. Logs: `Timestamp` (DateTime64), `TimestampTime` (DateTime, use it in time
filters), `ServiceName`, `Body`, `SeverityText`, `LogAttributes` and `ResourceAttributes` (both
`Map(String, String)`), `TraceId`, `SpanId`. Traces: `Timestamp`, `TraceId`, `SpanId`, `ParentSpanId`,
`ServiceName`, `SpanName`, `SpanKind`, `StatusCode`, `StatusMessage`, `Duration` (nanoseconds),
`SpanAttributes`, `ResourceAttributes`. Metrics: `TimeUnix`, `MetricName`, `Value` (gauge and sum),
`Count`, `Sum`, `BucketCounts`, `ExplicitBounds` (histogram), `Attributes`, `ResourceAttributes`,
`ServiceName`. Map columns are read with bracket syntax, `LogAttributes['k8s.object.kind']`; the keys
present in a set of rows come from `arrayJoin(mapKeys(LogAttributes))`.

Rules that keep queries fast and results readable:

- Always filter on time. The environment is small and recent: start with
  `TimestampTime >= now() - INTERVAL 30 MINUTE` (logs), `Timestamp >= now() - INTERVAL 30 MINUTE`
  (traces) or `TimeUnix >= now() - INTERVAL 30 MINUTE` (metrics), then widen. Everything in the
  database belongs to the current deployment of the application you are investigating; there is no
  older unrelated history.
- Aggregate before you read rows, and always end a row query with `LIMIT` (200 or fewer). The server
  rejects results above 500 rows or 60 seconds with an error instead of truncating silently.
- `argMax(col, Timestamp)` grouped by an identity gives the latest value per object; `countIf`,
  `quantile(0.95)(Duration)` and `groupUniqArray` summarise quickly.
- `Body` of state records is JSON: `JSONExtractString(Body, 'status', 'phase')`,
  `JSONExtractRaw(Body, 'spec', 'selector')`, `JSONExtractArrayRaw(...)`.

## Query patterns

- Service health from traces, per service over the last 30 minutes:
  `SELECT ServiceName, count() AS calls, countIf(StatusCode = 'STATUS_CODE_ERROR') AS errors, round(quantile(0.95)(Duration) / 1e6) AS p95_ms FROM default.otel_traces WHERE Timestamp >= now() - INTERVAL 30 MINUTE GROUP BY ServiceName ORDER BY errors DESC, p95_ms DESC LIMIT 50`
- Error lines in a namespace, newest first:
  `SELECT Timestamp, ServiceName, ResourceAttributes['k8s.pod.name'] AS pod, Body FROM default.otel_logs WHERE TimestampTime >= now() - INTERVAL 30 MINUTE AND ResourceAttributes['k8s.namespace.name'] = '<namespace>' AND (Body ILIKE '%error%' OR Body ILIKE '%timeout%' OR Body ILIKE '%refused%') ORDER BY Timestamp DESC LIMIT 100`
- Which workloads log at all, and how much: `SELECT ServiceName, ResourceAttributes['k8s.namespace.name'] AS ns, count() FROM default.otel_logs WHERE TimestampTime >= now() - INTERVAL 30 MINUTE GROUP BY ServiceName, ns ORDER BY count() DESC LIMIT 100`
- A log line's pod labels: every pod label and annotation is a key of `ResourceAttributes`, so
  `SELECT DISTINCT ResourceAttributes FROM default.otel_logs WHERE ResourceAttributes['k8s.pod.name'] = '<pod>' LIMIT 1` shows them.
- New or rare messages: group by a normalised body, `SELECT replaceRegexpAll(Body, '[0-9a-f]{8,}|\\d+', '#') AS pattern, count() AS n, min(Timestamp) AS first_seen, any(ServiceName) FROM default.otel_logs WHERE TimestampTime >= now() - INTERVAL 30 MINUTE GROUP BY pattern ORDER BY first_seen DESC LIMIT 50`.
- One trace as a tree: `SELECT Timestamp, ServiceName, SpanName, SpanKind, StatusCode, StatusMessage, round(Duration / 1e6, 1) AS ms, SpanId, ParentSpanId FROM default.otel_traces WHERE TraceId = '<trace id>' ORDER BY Timestamp LIMIT 200`;
  the hot spans of a trace: the same rows ordered by `Duration DESC`.
- Failing traces to pick from: `SELECT TraceId, min(Timestamp) AS started, any(ServiceName), any(StatusMessage) FROM default.otel_traces WHERE Timestamp >= now() - INTERVAL 30 MINUTE AND StatusCode = 'STATUS_CODE_ERROR' GROUP BY TraceId ORDER BY started DESC LIMIT 20`

## Container logs

Every container's stdout and stderr, including earlier instances of a restarted container, is in
`default.otel_logs`. `ServiceName` is the workload: the `app.kubernetes.io/name` label when present,
otherwise the Deployment, DaemonSet, StatefulSet, CronJob or Job name, otherwise the container name
(control-plane static pods such as `kube-apiserver`, `kube-scheduler`, `kube-controller-manager`,
`etcd` appear under those names). `ResourceAttributes` carry `k8s.namespace.name`, `k8s.pod.name`,
`k8s.container.name`, `k8s.node.name`, `k8s.container.restart_count`, the workload names, and every
pod label and annotation under its own key. `SeverityText` is set when the log line had a
recognisable level.

## Kubernetes Events

`ServiceName = 'k8s-events'` in `default.otel_logs`. One record per Event occurrence, with:

| Key in `LogAttributes` | Meaning |
|---|---|
| `k8s.event.reason` | `FailedScheduling`, `BackOff`, `Unhealthy`, `Killing`, `FailedMount`, `OOMKilling`, `Pulled`, ... |
| `k8s.event.type` | `Normal` or `Warning` |
| `k8s.event.note` | the message |
| `k8s.event.count` | repeat count |
| `k8s.object.kind`, `k8s.object.name`, `k8s.namespace.name` | the object the event is about |

Example: `SELECT LogAttributes['k8s.event.reason'] AS reason, LogAttributes['k8s.object.kind'] AS kind, LogAttributes['k8s.object.name'] AS name, count() AS n, max(Timestamp) AS last, any(LogAttributes['k8s.event.note']) AS note FROM default.otel_logs WHERE TimestampTime >= now() - INTERVAL 30 MINUTE AND ServiceName = 'k8s-events' AND LogAttributes['k8s.event.type'] = 'Warning' GROUP BY reason, kind, name ORDER BY last DESC LIMIT 100`

## Metrics

Discover names with `SELECT DISTINCT MetricName FROM default.otel_metrics_gauge WHERE TimeUnix >= now() - INTERVAL 30 MINUTE AND MetricName LIKE 'k8s.pod.%' LIMIT 200`
(and the same on `otel_metrics_sum` and `otel_metrics_histogram`). Latest value per series:
`SELECT ResourceAttributes['k8s.pod.name'] AS pod, argMax(Value, TimeUnix) AS v FROM default.otel_metrics_gauge WHERE TimeUnix >= now() - INTERVAL 30 MINUTE AND MetricName = 'k8s.container.restarts' AND ResourceAttributes['k8s.namespace.name'] = '<namespace>' GROUP BY pod ORDER BY v DESC LIMIT 100`.
Counters in `otel_metrics_sum` are cumulative: compare `max(Value) - min(Value)` over a window per series.
Histograms: `Count`, `Sum`, `BucketCounts` with `ExplicitBounds`; `Sum / Count` is the mean.

| Family | Examples | Keyed by |
|---|---|---|
| Cluster state (gauge) | `k8s.deployment.available`, `k8s.deployment.desired`, `k8s.pod.phase`, `k8s.container.restarts`, `k8s.container.ready`, `k8s.container.cpu_limit`, `k8s.container.memory_limit`, `k8s.container.cpu_request`, `k8s.container.memory_request`, `k8s.resource_quota.hard_limit`, `k8s.resource_quota.used`, `k8s.node.condition_ready`, `k8s.daemonset.ready_nodes`, `k8s.statefulset.ready_pods`, `k8s.job.active_pods`, `k8s.hpa.current_replicas` | `ResourceAttributes['k8s.namespace.name']`, `k8s.deployment.name`, `k8s.pod.name`, `k8s.container.name`, `k8s.node.name` |
| Pod and node usage | `k8s.pod.cpu.time` (sum), `k8s.pod.memory.working_set`, `k8s.pod.network.io`, `container.cpu.time`, `container.memory.working_set`, `k8s.node.cpu.usage`, `k8s.node.memory.working_set`, `k8s.volume.available` | same |
| Host | `system.cpu.time`, `system.memory.usage`, `system.filesystem.usage`, `system.network.io`, `system.network.errors`, `system.disk.io`, `system.cpu.load_average.1m` | `ResourceAttributes['k8s.node.name']` or `host.name` |
| Throttling and container errors (from cAdvisor, Prometheus names) | `container_cpu_cfs_throttled_periods_total`, `container_cpu_cfs_periods_total`, `container_oom_events_total`, `container_network_receive_errors_total`, `container_file_descriptors`, `container_sockets`, `container_processes` | `Attributes['pod']`, `Attributes['namespace']`, `Attributes['container']` |
| Request rates and latency per service | `traces_span_metrics.calls` (sum), `traces_span_metrics.duration` (histogram) | `Attributes['service.name']`, `span.name`, `span.kind`, `status.code` |
| Reachability probes | `probe_success` (1 up, 0 down), `probe_duration_seconds` | `ServiceName` = probe job, `ResourceAttributes['service.instance.id']` = `host:port` target |
| Application and exporter metrics | whatever the application exposes, plus `up` per scrape target | `ServiceName` = scrape job, `ResourceAttributes['service.instance.id']` = target |

## Traces

Spans carry `ServiceName`, `SpanName`, `SpanKind` (`Server`, `Client`, `Internal`), `StatusCode`
(`STATUS_CODE_ERROR` on failure), `StatusMessage`, `Duration` (ns) and `SpanAttributes` such as
`http.status_code`, `rpc.method`, `net.peer.name`. `ResourceAttributes['k8s.namespace.name']` is set when
the exporter knew the pod. Find failing spans with `StatusCode = 'STATUS_CODE_ERROR'`, then read the whole
trace by `TraceId` as shown above.
