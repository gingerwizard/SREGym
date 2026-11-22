---
name: clickstack-telemetry
description: How this cluster's telemetry and Kubernetes state are stored in ClickStack and how to query them with the clickstack MCP tools. Read before the first query. Covers that plus per-node kernel, network and kubelet configuration records.
---

# Investigating this cluster through ClickStack

This skill describes what the `mcp__clickstack__*` tools can see in this environment and the query
patterns that work. It describes the data, not any particular incident.

## What is stored and where

All telemetry for this cluster is in ClickStack and is queried with the `mcp__clickstack__*` tools.
Start with `clickstack_list_sources`, then `clickstack_describe_source` for each source you use.
There are three sources:

| Source | Table | Holds |
|---|---|---|
| Logs | `default.otel_logs` | container logs, Kubernetes Events, and the Kubernetes state records described below |
| Traces | `default.otel_traces` | spans from instrumented services |
| Metrics | `default.otel_metrics_gauge`, `_sum`, `_histogram` | cluster, node, container, probe and application metrics |

Columns are PascalCase (`Timestamp`, `ServiceName`, `Body`, `SeverityText`, `LogAttributes`,
`ResourceAttributes`; traces add `SpanName`, `SpanKind`, `StatusCode`, `StatusMessage`, `Duration` in
nanoseconds, `SpanAttributes`, `TraceId`, `ParentSpanId`). Map attributes use dot syntax in Lucene
(`LogAttributes.k8s.object.kind:Service`) and bracket syntax in SQL (`LogAttributes['k8s.object.kind']`).
Lucene `field:value` is a substring match; quote the value for an exact match.

The environment is small and recent: start every query with the last 30 to 60 minutes, then widen.
Everything in the store belongs to the current deployment of the application you are investigating;
there is no older unrelated history.

## Tool selection

- `clickstack_service_health` on the Traces source first: throughput, error rate and latency per service.
- `clickstack_search` on Logs for rows; `clickstack_table` for counts and top-N; `clickstack_timeseries` for trends.
- `clickstack_event_patterns` and `clickstack_emerging_signals` on Logs to find new or unusual log lines quickly.
- `clickstack_trace_waterfall` for one slow or failed trace; `clickstack_trace_top_time_consuming_ops` for its hot spans.
- `clickstack_sql` only for joins, `argMax`, JSON extraction from object manifests, or anything the builder tools cannot express.
  Use `$__sourceTable` and `$__timeFilter(TimestampTime)` (logs) / `$__timeFilter(Timestamp)` (traces) / `$__timeFilter(TimeUnix)` (metrics).

## Container logs

Every container's stdout and stderr, including earlier instances of a restarted container, is in Logs.
`ServiceName` is the workload: the `app.kubernetes.io/name` label when present, otherwise the Deployment,
DaemonSet, StatefulSet, CronJob or Job name, otherwise the container name (control-plane static pods such as
`kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `etcd` appear under those names).
`ResourceAttributes` carry `k8s.namespace.name`, `k8s.pod.name`, `k8s.container.name`, `k8s.node.name`,
`k8s.container.restart_count`, the workload names, and every pod label and annotation under its own key.
`SeverityText` is set when the log line had a recognisable level.

Example: `ResourceAttributes.k8s.namespace.name:"<namespace>" AND (error OR timeout OR refused)` on Logs,
sorted newest first.

## Kubernetes Events

`ServiceName:"k8s-events"` on Logs. One record per Event occurrence, with:

| Attribute (`LogAttributes`) | Meaning |
|---|---|
| `k8s.event.reason` | `FailedScheduling`, `BackOff`, `Unhealthy`, `Killing`, `FailedMount`, `OOMKilling`, `Pulled`, ... |
| `k8s.event.type` | `Normal` or `Warning` |
| `k8s.event.note` | the message |
| `k8s.event.count` | repeat count |
| `k8s.object.kind`, `k8s.object.name`, `k8s.namespace.name` | the object the event is about |

Example: `clickstack_table` on Logs, `where`: `ServiceName:"k8s-events" AND LogAttributes.k8s.event.type:"Warning"`,
`groupBy`: `LogAttributes['k8s.event.reason'], LogAttributes['k8s.object.kind'], LogAttributes['k8s.object.name']`.

## Metrics

Discover names with `clickstack_describe_source` and `clickstack_list_metrics` (use `namePattern`, e.g. `k8s.pod.%`).
Every select item on the Metrics source needs `metricType` and `metricName`.

| Family | Examples | Keyed by |
|---|---|---|
| Cluster state (gauge) | `k8s.deployment.available`, `k8s.deployment.desired`, `k8s.pod.phase`, `k8s.container.restarts`, `k8s.container.ready`, `k8s.container.cpu_limit`, `k8s.container.memory_limit`, `k8s.container.cpu_request`, `k8s.container.memory_request`, `k8s.resource_quota.hard_limit`, `k8s.resource_quota.used`, `k8s.node.condition_ready`, `k8s.daemonset.ready_nodes`, `k8s.statefulset.ready_pods`, `k8s.job.active_pods`, `k8s.hpa.current_replicas` | `ResourceAttributes['k8s.namespace.name']`, `k8s.deployment.name`, `k8s.pod.name`, `k8s.container.name`, `k8s.node.name` |
| Pod and node usage | `k8s.pod.cpu.time` (sum), `k8s.pod.memory.working_set`, `k8s.pod.network.io`, `container.cpu.time`, `container.memory.working_set`, `k8s.node.cpu.usage`, `k8s.node.memory.working_set`, `k8s.volume.available` | same |
| Host | `system.cpu.time`, `system.memory.usage`, `system.filesystem.usage`, `system.network.io`, `system.network.errors`, `system.disk.io`, `system.cpu.load_average.1m` | `ResourceAttributes['k8s.node.name']` or `host.name` |
| Throttling and container errors (from cAdvisor, Prometheus names) | `container_cpu_cfs_throttled_periods_total`, `container_cpu_cfs_periods_total`, `container_oom_events_total`, `container_network_receive_errors_total`, `container_file_descriptors`, `container_sockets`, `container_processes` | `Attributes['pod']`, `Attributes['namespace']`, `Attributes['container']` |
| Request rates and latency per service | `traces_span_metrics.calls` (sum), `traces_span_metrics.duration` (histogram) | `Attributes['service.name']`, `span.name`, `span.kind`, `status.code` |
| Reachability probes | `probe_success` (1 up, 0 down), `probe_duration_seconds` | `ServiceName` = probe job, `ResourceAttributes['service.instance.id']` = `host:port` target |
| Application and exporter metrics | whatever the application exposes, plus `up` per scrape target | `ServiceName` = scrape job, `ResourceAttributes['service.instance.id']` = target |

Counters (`sum` kind) are plotted with `aggFn: increase`; gauges with `last_value`, `avg`, `max`.

## Traces

Spans carry `ServiceName`, `SpanName`, `SpanKind` (`Server`, `Client`, `Internal`), `StatusCode`
(`STATUS_CODE_ERROR` on failure), `StatusMessage`, `Duration` (ns) and `SpanAttributes` such as
`http.status_code`, `rpc.method`, `net.peer.name`. `ResourceAttributes['k8s.namespace.name']` is set when
the exporter knew the pod. Use `clickstack_search` with `StatusCode:"STATUS_CODE_ERROR"` to find failing
spans, then `clickstack_trace_waterfall` with the `TraceId`.

## Kubernetes object snapshots (`k8s-objects`)

`ServiceName:"k8s-objects"` on Logs. Every minute, one record per object with the full manifest (spec and
status, `managedFields` removed) in `Body`, so anything `kubectl get -o yaml` or `kubectl describe` would show
is here. Kinds covered: Pod, Deployment, ReplicaSet, StatefulSet, DaemonSet, Job, CronJob, Service,
Endpoints, EndpointSlice, Ingress, PersistentVolumeClaim, PersistentVolume, Node, Namespace.

| Attribute (`LogAttributes`) | Meaning |
|---|---|
| `k8s.object.kind`, `k8s.object.name`, `k8s.namespace.name` | identity (namespace empty for cluster-scoped kinds) |
| `k8s.object.uid`, `k8s.object.resource_version` | identity across snapshots; `resource_version` changes when the object changes |
| `kind`, `metadata`, `spec`, `status` | the same manifest split into top-level JSON strings |

Patterns:

- Latest manifest of each object of a kind (SQL on the Logs source):
  `SELECT LogAttributes['k8s.object.name'] AS name, argMax(Body, Timestamp) AS manifest FROM $__sourceTable WHERE $__timeFilter(TimestampTime) AND ServiceName = 'k8s-objects' AND LogAttributes['k8s.object.kind'] = 'Service' AND LogAttributes['k8s.namespace.name'] = '<namespace>' GROUP BY name`
- One field across objects: `argMax(JSONExtractRaw(Body, 'spec', 'selector'), Timestamp)`,
  `JSONExtractString(Body, 'status', 'phase')`, `JSONExtractRaw(Body, 'spec', 'template', 'spec', 'containers')`.
- A Service and the Pods it should reach: compare the Service `spec.selector` with Pod `metadata.labels`;
  check the Endpoints or EndpointSlice object of the same name for `addresses`. An Endpoints object with no
  `subsets` means no Pod matches the selector or none is Ready.
- A Deployment's intent: `spec.template.spec.containers[*].image`, `env`, `ports`, `resources`,
  `livenessProbe`, `readinessProbe`, `args`; `spec.replicas` against `status.availableReplicas`;
  `spec.strategy.rollingUpdate` for stuck rollouts.
- A Pod's state: `status.phase`, `status.conditions`, `status.containerStatuses[*].state`,
  `lastState.terminated.reason` (`OOMKilled`, `Error`), `restartCount`, `spec.nodeName`,
  `spec.dnsPolicy`, `spec.hostAliases`, `spec.securityContext.sysctls`, `spec.tolerations`, `spec.affinity`.
- A Node: `spec.taints`, `status.allocatable`, `status.conditions`, `status.nodeInfo`.
- Volumes: PersistentVolumeClaim `status.phase`, `spec.accessModes`, `spec.storageClassName`;
  PersistentVolume `spec.nodeAffinity`.

## Kubernetes object changes (`k8s-changes`)

`ServiceName:"k8s-changes"` on Logs. One record per create, update or delete of the kinds above,
with the object after the change in `Body` (`object` field) and `LogAttributes['k8s.object.event']`
set to `ADDED`, `MODIFIED` or `DELETED`. This is the change history kubectl does not have.

- What changed recently in a namespace: `clickstack_search` with
  `ServiceName:"k8s-changes" AND LogAttributes.k8s.namespace.name:"<namespace>" AND LogAttributes.k8s.object.event:"MODIFIED"`,
  excluding noisy kinds (`NOT LogAttributes.k8s.object.kind:"Pod"` hides status-only updates) and sorted by time.
- What a change did: compare the `MODIFIED` record with the previous record of the same `k8s.object.uid`
  (`JSONExtractRaw(Body, 'object', 'spec')` on both), or with the first `k8s-objects` snapshot of that uid.
- Pods created, killed or evicted: `k8s.object.kind:"Pod"` with `ADDED` / `DELETED`, together with
  `k8s-events` for the reason.

## Configuration, policy, access and operator objects

The `k8s-objects` and `k8s-changes` records also cover these kinds:

| Kind | Where the interesting part is |
|---|---|
| ConfigMap | `data` (the CoreDNS Corefile in `kube-system/coredns`, application config files, feature-flag files, scripts). Changes to a ConfigMap appear in `k8s-changes` before any Pod picks them up |
| Secret | metadata only; the `data` values are never stored. Use the name, type, labels and age |
| NetworkPolicy | `spec.podSelector`, `spec.ingress`, `spec.egress`, `spec.policyTypes` |
| Role, RoleBinding, ClusterRole, ClusterRoleBinding, ServiceAccount | which subject can do what; a controller or init container that lost a verb shows up here |
| PriorityClass | `value`, `globalDefault`, `preemptionPolicy` |
| ResourceQuota, LimitRange | `status.hard` against `status.used`; default requests and limits |
| HorizontalPodAutoscaler | `spec.minReplicas`, `spec.maxReplicas`, `status.conditions` (`ScalingActive`) |
| PodDisruptionBudget | `spec.minAvailable`, `status.disruptionsAllowed` |
| MutatingWebhookConfiguration, ValidatingWebhookConfiguration | `webhooks[*].clientConfig.service`, `failurePolicy`, `timeoutSeconds`, `namespaceSelector`, `rules`; the `caBundle` is present but not meaningful |
| StorageClass | `provisioner`, `volumeBindingMode` |
| Calico `IPPool`, `IPAMConfig`, `FelixConfiguration`, `BGPPeer`, `BGPConfiguration`, `GlobalNetworkPolicy` | pod CIDR and `disabled`, `strictAffinity`, route reflector peering, cluster-wide policy |

Example, the CoreDNS configuration now:
`SELECT argMax(JSONExtractString(Body, 'data', 'Corefile'), Timestamp) FROM $__sourceTable WHERE $__timeFilter(TimestampTime) AND ServiceName = 'k8s-objects' AND LogAttributes['k8s.object.kind'] = 'ConfigMap' AND LogAttributes['k8s.object.name'] = 'coredns'`.

## Control-plane and cluster-component logs

These are ordinary container logs in Logs, namespace `kube-system`, under `ServiceName`
`kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `etcd`, `coredns`, `kube-proxy` and
`calico-node`. Worth searching:

- `kube-apiserver`: admission webhook calls that fail or time out (`failed calling webhook`,
  `context deadline exceeded`), authorization denials, finalizer and deletion problems.
- `kube-scheduler`: why a Pod is pending, together with the `FailedScheduling` Event note
  (`Insufficient cpu`, `node(s) had untolerated taint`, `volume node affinity conflict`, `exceeded quota`).
- `kube-controller-manager`: ReplicaSet and Deployment reconciliation errors, quota rejections (`exceeded quota`), PVC binding.
- `coredns`: `NXDOMAIN`, `SERVFAIL`, plugin and template errors; compare with the Corefile ConfigMap.
- `calico-node`: BGP peering, IPAM exhaustion (`no IPs available`), policy sync.

## Node logs

`ServiceName:"node-syslog"` on Logs: `/var/log/syslog` and `/var/log/kern.log` of every node (kubelet,
container runtime, kernel), with `ResourceAttributes['k8s.node.name']`. It carries kubelet messages: eviction (`Evicted`, `DiskPressure`, `MemoryPressure`),
image pull failures, probe failures, container runtime errors, and kernel messages such as OOM kills and
conntrack table full.

## Node state records (`node-state`)

`ServiceName:"node-state"` on Logs. One record per node per minute with the node configuration that is
neither a Kubernetes object nor a metric. The JSON is in `Body` and flattened into `LogAttributes`:

| Attribute | Meaning and what normal looks like |
|---|---|
| `node` | node name |
| `sysctl.net.ipv4.ip_local_port_range` | ephemeral port range; normally `32768 60999`. A narrow range exhausts outbound connections |
| `sysctl.net.netfilter.nf_conntrack_count`, `sysctl.net.netfilter.nf_conntrack_max` | connection-tracking table usage; count near max means dropped connections |
| `file_nr` (`allocated 0 max`), `sysctl.fs.file-max`, `sysctl.fs.nr_open` | open file handles against the limit |
| `sysctl.fs.inotify.max_user_instances`, `sysctl.fs.inotify.max_user_watches` | inotify limits |
| `sysctl.vm.max_map_count`, `sysctl.vm.swappiness`, `sysctl.kernel.pid_max`, `sysctl.kernel.threads-max` | memory and process limits |
| `sysctl.net.core.somaxconn`, `sysctl.net.ipv4.tcp_max_syn_backlog`, `sysctl.net.ipv4.tcp_tw_reuse` | listen backlog and TIME_WAIT reuse |
| `chrony_tracking_csv` | chrony `tracking` output; the fifth field is the system clock offset in seconds, normally under 0.01 |
| `time_epoch` | the node's clock, to compare with `Timestamp` (collector time) for drift |
| `kubelet_config`, `kubelet_flags` | the kubelet configuration file and flags: eviction thresholds (`evictionHard`), `maxPods`, `imageGCHighThresholdPercent`, cgroup driver |
| `hosts_file` | `/etc/hosts` on the node |
| `disk_df` | `df` for the root, kubelet, container runtime and local-volume paths |
| `services.kubelet`, `services.docker`, `services.cri-docker`, `services.chrony` | `active` or not |
| `loadavg`, `mem_available_kb`, `uptime_s`, `kernel`, `listening_ports` | general node health |

Latest record per node: `clickstack_search` with `ServiceName:"node-state"`, one page, newest first; or
`argMax(Body, Timestamp)` grouped by `LogAttributes['node']` in SQL.
