# ClickStack ingestion for SREGym

Ships the SREGym cluster's telemetry, plus Kubernetes state captured as OpenTelemetry data, to a
ClickHouse Cloud service with the ClickStack UI (the "self-configured" ClickStack option). Nothing
here is part of a benchmark problem; the whole directory is observability plumbing.

```
app namespaces ─traces/span metrics─▶ observe/otel-collector (SREGym's, re-applied per problem)
                                              │ otlp
otel/ (persistent namespace)                  ▼
  otel-node-agent (DaemonSet) ───▶ otel-gateway ───▶ clickstack-collector ──https:8443──▶ ClickHouse Cloud
  otel-cluster (Deployment)   ───▶ (filters, k8s   (ClickStack distro, owns the otel_* schema)
  node-state (DaemonSet)      ───▶  normalisation)
```

| Component | File | Collects |
|---|---|---|
| `clickstack-collector` | `k8s/clickstack-collector.yaml` | writer; credentials from Secret `clickstack-credentials` |
| `otel-gateway` | `k8s/gateway.yaml` | SREGym visibility policy, k8s object normalisation, Secret stripping, service-name fallback |
| `otel-node-agent` | `helm/daemonset-values.yaml` | container logs (not the `otel` namespace), node syslog, hostmetrics, kubeletstats, cAdvisor throttling counters |
| `otel-cluster` | `helm/deployment-values.yaml` | Kubernetes Events, `k8s_cluster` metrics, `k8sobjects` snapshots (60 s) and watches for tiers 1 and 2, SREGym's application/blackbox/Locust scrape jobs |
| `node-state` | `k8s/node-state.yaml` | tier 3: sysctls, conntrack, file handles, chrony offset, kubelet config, /etc/hosts, disk, one JSON line per node per minute |

## Data model in ClickStack

Everything lands in the standard `otel_logs`, `otel_traces`, `otel_metrics_*` tables (database `default`).
Kubernetes state is in `otel_logs`, addressed by `ServiceName`:

| ServiceName | Record | Key attributes (`LogAttributes`) |
|---|---|---|
| `k8s-objects` | one snapshot per object per minute; `Body` is the manifest (managedFields removed, Secret data removed) | `k8s.object.kind`, `k8s.object.name`, `k8s.namespace.name`, `k8s.object.uid`, `k8s.object.resource_version`; the ClickStack collector also flattens top-level fields: `kind`, `metadata`, `spec`, `status`, `data` as JSON strings |
| `k8s-changes` | watch events, `Body` = `{type, object}` | same, plus `k8s.object.event` = ADDED / MODIFIED / DELETED |
| `k8s-events` | Kubernetes Events (events.k8s.io) | `k8s.event.reason`, `k8s.event.type`, `k8s.event.note`, `k8s.event.count`, `k8s.object.kind/name` of the regarding object |
| `node-state` | node JSON record | `node`, `sysctl.*`, `file_nr`, `chrony_tracking_csv`, `kubelet_config`, `hosts_file`, `disk_df`, `services.*` |
| `node-syslog` | `/var/log/syslog`, `/var/log/kern.log` | `k8s.node.name` in resource attributes |

Latest manifest of an object: `argMax(Body, Timestamp)` grouped by `LogAttributes['k8s.object.uid']`.
Prometheus-scraped metrics use the scrape job as `ServiceName` (e.g. `blackbox-hotel-reservation`) and the
target as `ResourceAttributes['service.instance.id']`. Span metrics from SREGym's collector are
`traces_span_metrics.calls` and `traces_span_metrics.duration`.

## Visibility policy (mirrors SREGym's agent view)

Dropped at the gateway: namespaces `khaos` and `chaos-mesh`; log lines naming Chaos Mesh or `khaos/...`
objects; objects labelled `app=load-generator`, `app=locust-fetcher`, `job=workload`,
`opentelemetry.io/name=load-generator`, `network-access=restricted`; Helm release secrets; Chaos Mesh
cluster objects; events about load-generator pods; span metrics of `unknown_service:wlgen_proc`.
Secret `data` is deleted at the cluster collector and any Secret still carrying data is dropped.
Container logs of load generators stay visible, as they are in Loki.

## Tier gate

The gateway Deployment carries `CLICKSTACK_TIER` (default `3`). `filter/tier` drops the Kubernetes-state
records above that tier: tier 0 keeps container logs, Events, metrics and traces only; tier 1 adds
`k8s-objects` and `k8s-changes` for Pod, Deployment, ReplicaSet, StatefulSet, DaemonSet, Job, CronJob,
Service, Endpoints, EndpointSlice, Ingress, PersistentVolumeClaim, PersistentVolume, Node and Namespace;
tier 2 adds every other kind and `node-syslog`; tier 3 adds `node-state`. `campaign.py` sets the tier to
the arm's skill tier before each run (and back to 3 afterwards), so an arm's data and its skill match.
Set it by hand with `kubectl -n otel set env deployment/otel-gateway CLICKSTACK_TIER=<n>`.

## Operating

- Credentials: `~/.config/sregym/clickstack.env` (never committed). `deploy.sh` turns it into the Secret.
- Deploy or update: `bash clickstack/deploy.sh` (idempotent; restarts the gateway).
- Verify: `bash clickstack/verify.sh [minutes]`.
- Per-problem reset: `clickstack/truncate.sh`, run by the conductor when `SREGYM_PRE_DEPLOY_HOOK` points at it
  (see `~/.config/sregym/sregym.env` on the runner). It truncates every `otel_*` table waiting for all replicas
  (`alter_sync=2`) and verifies that no data part created before the truncation survives; rows the collectors
  write afterwards are allowed. A non-zero exit counts as a failed deploy in SREGym, so the check is deliberately
  about old data only.
- Nodes: `clickstack/node-tune.sh` sets the Docker Hub mirror and inotify limits after the Ansible playbook.
- SREGym changes that make this work: `sregym/observer/otel_collector/otel-collector.yaml` gains an
  `otlp/clickstack` exporter to the gateway; `Conductor.start_problem` gains `SREGYM_PRE_DEPLOY_HOOK`;
  `scripts/ansible/setup_cluster.yml` writes a GRUB drop-in for cloud images and has a longer kernel-install timeout.

## Campaigns and the report

- `clickstack/campaign.py --name <n> --models ... --arms ... --problem <id> | --suite sregym-lite` runs every
  (arm, model) pair sequentially with one fixed `--judge-model`, and appends a record per run to
  `results/campaigns/<n>.jsonl` (agent, model, tier, kubectl, stages, results directory, exit code).
  `--dry-run` prints the plan and a time estimate; re-running skips pairs that already completed.
- `clickstack/campaign_status.py <name>` prints finished pairs, the pair in progress (current problem and
  attempt) and a finishing estimate; `clickstack/incomplete_attempts.py <name>` lists attempts that did not
  complete and why. SREGym's results CSVs are cumulative snapshots, so both deduplicate by (problem, attempt);
  SREGym exit code 2 (an attempt could not be deployed) counts as a finished pair.
- `clickstack/fill_gaps.py <name> [--wait] [--dry-run]` re-runs the attempts a campaign is missing under
  `<name>-fill`; `--wait` starts once no `campaign.py` or `queue_codex.sh` is running.
- Codex half: `clickstack/queue_codex.sh <name>` waits for the running campaign, checks `OPENAI_API_KEY` and
  the model ids against the API (`PROBE_ONLY=1` to only check), then launches the six `codex*` arms.
  `clickstack/codex_smoke.sh [model]` checks the Codex path without the cluster: pinned CLI install inside the
  agent image, MCP proxy handshake from a container, one Codex turn that calls a ClickStack tool.
- Between pairs: `clickstack/run_between_pairs.sh <campaign.py pid> <command>` pauses a running campaign with
  SIGSTOP, runs the command when its current pair has finished, then resumes it (nothing is lost);
  `clickstack/shakedown_codex.sh` is the command used for the Codex shakedown (one problem, one attempt,
  kubectl + ClickStack arm, one model per provider).
- `clickstack/report.py --out results/report.html` reads every manifest (finished and in-progress pairs), the
  SREGym results CSVs and each attempt's transcript and usage file, and writes one self-contained HTML page.
  All aggregates are computed in the browser from the filters (campaign, model, arm, harness, problem, text):
  headline tables by arm and by model (pass@1, pass@k, mitigation, TTD, TTM, tokens, ClickStack and kubectl
  calls, SQL share, tool errors, cost), a model × arm matrix, and tabs by model, by arm, by problem (with the
  fault text), all attempts (each opens to fault, diagnosis, judge verdict, tokens and the full tool trace with
  the agent's words before each call) and every ClickStack tool call and SQL query. CSV export; the URL hash
  selects a tab. A cron job on the runner regenerates it hourly.
