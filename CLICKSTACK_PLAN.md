# SREGym on ClickStack (self-managed collector, ClickHouse Cloud): project plan, v3

Goal: run SREGym on a GCE cluster whose telemetry is written to a ClickHouse Cloud service with the ClickStack UI enabled, give the agent the ClickStack MCP server plus a tiered skill describing the data, and measure diagnosis accuracy against stock SREGym tools under identical conditions. The second goal is to show how much Kubernetes state, captured as OpenTelemetry data and explained by a skill, lets an agent with **no kubectl** diagnose.

Assumed given before we start: a ClickHouse Cloud service with ClickStack enabled, SQL credentials for it (so tables can be truncated), and MCP access with credentials.

Changes from v2: we run the ClickStack collector distribution in the cluster as the writer; per-problem isolation is a real `TRUNCATE`; a new phase designs one skill file per collection tier; `mcp-clickhouse` returns as an optional arm because we have the SQL endpoint.

## Status, 2026-11-11

**Built and verified**
- GCE: `sregym-control`, `sregym-worker-1`, `sregym-worker-2` (n2-standard-8, Ubuntu 22.04, generic 6.8 kernel, Kubernetes 1.31.14, Calico, Docker via cri-dockerd) and `sregym-runner` (n2-standard-4, Ubuntu 24.04). SSH to all of them goes through an IAP tunnel: `ssh -F ~/.ssh/sregym_config <host>`.
- Runner: SREGym at commit b501c3fc plus the patches below, uv environment, Docker, kubectl, helm 4, Node 22, Claude Code CLI, clickhouse-client. Kubeconfig in `~/.kube/config`. Env files in `~/.config/sregym/`.
- ClickHouse Cloud: users `clickstack_ingest` (documented grants) and `clickstack_readonly`; tables `otel_*` in `default`; Logs, Traces, Metrics and Sessions sources registered and cross-linked through the MCP.
- Collectors (see `clickstack/README.md`): ClickStack collector as writer, gateway with the visibility policy and Kubernetes-state normalisation, node agent, cluster collector with tiers 1 and 2 object capture and SREGym's scrape jobs, tier 3 node-state DaemonSet. All configs validated against the contrib binary and deployed.
- First SREGym run (`--use-external-harness --problem service_wrong_pod_selection_hotel_reservation`) deployed cleanly. Traces for all eight hotel-reservation services, span metrics, container logs, blackbox probes, Events, object snapshots and the Service change that constitutes the fault are all queryable through the MCP.

**Fixes found along the way** (all in the repo): GCE firewall rule `sregym-allow-ipip` for Calico IPIP; GRUB drop-in in the playbook for cloud images; longer kernel-install timeout; Docker Hub mirror and inotify limits on nodes (`clickstack/node-tune.sh`); Secret data stripped at the source collector after an early version leaked it (table purged, credentials rotated).

- MCP for agents: the runner's Claude Code is logged in (OAuth, `claude mcp login --no-browser`), and `clients/claudecode_clickstack/mcp_auth_proxy.py` runs as systemd service `sregym-mcp-proxy` on the Docker bridge (172.17.0.1:9300), refreshing the token itself. Agent containers use `http://host.docker.internal:9300/mcp`; the server exposes 35 tools.

- Phase 3 and 4 built: `clients/claudecode_clickstack/` (agent class, driver, routing texts, MCP auth proxy, smoke test), skills tier 0 to 3 generated from `skills/sections/`, registrations `claudecode_clickstack` and `claudecode_clickstack_only_t0..t3` in `agents.yaml`, 26 tests in `tests/clients/test_claudecode_clickstack.py` (allowlist is read-only, skills cumulative and free of benchmark names, instruction wiring). `clickstack/run-shakedown.sh <agent> <problem>` runs one problem on the runner.

- Tests: the repository suite on the runner (importlib import mode, integration tests deselected, two upstream modules with broken collection ignored) gives 4200 passed; the 6 failures in `tests/service/test_telemetry_storage.py` are order-dependent and fail identically with my changes stashed, so they are pre-existing. Agent image `sregym-agent-base:latest` is built on the runner.

- Multi-model tooling: `clickstack/campaign.py` runs every (arm, model) pair sequentially with one judge and writes `results/campaigns/<name>.jsonl`; `clickstack/report.py` renders one self-contained HTML report (model × arm matrix, tabs by model / skill tier / problem / attempts, tool-call mix, judge reasoning). Both tested on a synthetic fixture.

- Tier gate: the gateway drops Kubernetes-state records above `CLICKSTACK_TIER`; the campaign sets it per arm, so tier N means both the data and the skill. Verified live at tiers 0, 1 and 3. Node syslog moved to tier 2. Stock `claudecode` added as the baseline arm; `claude-fable-5-1` added to the model list (6 arms × 4 models × 1 problem ≈ 10 h).

- **First sweep done (2026-11-12):** 24 runs, 6 arms × 4 models on `service_wrong_pod_selection_hotel_reservation`. Diagnosis passes: Opus 6/6, Fable 5.1 6/6, Haiku 3/6 (only with Kubernetes state), Sonnet 2/6. By arm: stock 3/4, kubectl+ClickStack 2/4, tiers 0/1/2/3: 2/3/3/4 of 4. Tier 0 passes came from pod labels on log lines. Object state cut ClickStack-only time and cost by about two thirds. Full tables and observations in `documentation.md`; HTML in `results/report.html`.

**Next**
1. First sweep: 3 Anthropic models × 2 arms × the deployed problem, then widen to SREGym-Lite. Needs the agent credential on the runner.
2. Shakedown with a real agent: needs an Anthropic credential (and the judge model's key) exported on the runner.
2. SREGym-Lite across arms A, B, C0 to C3.

## Status, 2026-11-22 (evening): repo on the fork, web app on Vercel

- **Repo.** `origin` = github.com/gingerwizard/SREGym (branch `clickstack`, default), `upstream` = SREGym/SREGym; four logical commits on top of b501c3fc; upstream main ~20 commits ahead (rebase before PRs). Further changes by PR: #1 = the web app.
- **Web app.** `clickstack/webapp` (Next.js 15.5, React 19): server-side ClickHouse via env vars (`CLICKHOUSE_URL/USER/PASSWORD/DATABASE`, `RESULTS_*`), routes `/api/attempts|attempt|queries|sql|config`; the report logic moved to `lib/report-core.js` + `report.css` (shared with report.py, which embeds them; `--write-constants` refreshes `lib/report-constants.json`). Vercel project `sregym-clickstack-report` in scope `daleclickhousecs-projects` (hobby), env vars set as secrets for production and preview, production deploy Ready; Vercel Authentication was on briefly; replaced by app-level HTTP Basic auth (`middleware.ts`, env `APP_USER`/`APP_PASSWORD`, password kept in `~/.config/sregym/webapp.env` on the laptop) so colleagues without Vercel accounts can open it; `ssoProtection` set back to null. Local test: all routes OK, SQL route refuses DDL, page = static report figures.
- **Report controls added today.** Phase selectors (p1/p2/all, default p1), model defaults (fable-5-2, opus-5-5, sol), hide mitigation (default on), call breakdown by server with shares, saturation reference (kubectl arms, min 3 cells), problem checklist, row markers. Loader columns n_cs/n_sqlmcp/n_obsmcp/shell_kinds.

## Status, 2026-11-22: results in ClickHouse, SQL tab, Codex SSE bridge

- **ClickHouse store.** `clickstack/load_results.py` → database `sregym_results` on the Cloud service: `attempts`, `tool_calls`, `problem_sets` (ReplacingMergeTree by run_dir; reload = replace), views `attempts_scored`, `pass_rates`, `cells`; SELECT granted to `clickstack_readonly`. Hourly cron on the primary: `collect.sh && load_results.py`. First load: 3,202 attempts.
- **Report.** New SQL tab (filters → WHERE clause, presets, free SQL run from the browser against the ClickHouse HTTP endpoint with the read-only user; `add_http_cors_header=1`, creds as URL params, no preflight; Cloud answers `Access-Control-Allow-Origin: *`). Saturation judged on a reference arm set (`#f-sat-ref`, default kubectl arms; `?satref=`), problem checklist (`.f-prob`, all/none/exclude saturated), lazy per-tab rendering with on-demand attempt details (recompute ~15 ms, DOM 108 → ~2 MB), MCP/kubectl shares in brackets, labels "MCP calls", stock = "kubectl; Prometheus/Loki/Jaeger in the cluster" (stock clients attach no MCP), obs = "stock telemetry MCP only".
- **Codex no-kubectl arm.** Codex's `mcp_servers.<name>.url` is streamable HTTP; SREGym's mounts are SSE → 405, servers never attached; Codex scripted its own SSE client in all 48 Lite attempts (21 calls/attempt; counted as MCP calls with `scripted` flag now). Fix: `clients/codex_obs/sse_stdio_bridge.py` as `mcp_servers.<name>.command` (stdio↔SSE, stdlib only), verified live. Deployed to all runners (phase 2 lines use it). Open: re-run the Lite `codex_obs × gpt-6.1-sol` pair (the retire+requeue step was prepared: `invalidate.py --campaign phase1 --agent-pattern '^codex_obs$' --tag codex-sse` on c3 and primary + `phase1-fill`, then append `phase1 sregym-lite 3 gpt-6.1-sol codex_obs` to c3's `~/fleet/queue`).
- **Phase 1.** 31/36 pairs done; running: Claude SQL-only t0/t1 (c1, c3, c4, c5); re-queued `claudecode_clickstack_t3 × fable` (SREGym "cleanup failed" at launch 21 Nov) on c1's queue2. Phase 2 running on c6/c7/c8 since ~10:00 UTC (ClickStack-only t4 on cloned clusters works: 21/21 early).

## Status, 2026-11-21: SQL-arm incident, report tabs, phase 2 chained

- **Incident.** All ClickHouse-SQL tool calls on every cluster failed from the phase 1 launch (20 Nov 21:56 UTC) until 21 Nov 16:00 UTC: the `sregym_agent` profile's `max_result_rows = 500` with `result_overflow_mode = throw` tripped on clickhouse-connect 1.13's connection-setup query (`SELECT name, value, readonly FROM system.settings LIMIT 10000`, 1,694 rows), so no connection could be opened (4,666 failures in `system.query_log`). The 20 Nov smoke tests passed only because the primary's server had cached a connection before the profile was attached. Fix: `clickstack/chsql_mcp_main.py` is the unit's ExecStart (uv tool venv python) and adds the caps as per-query settings beside `readonly=1` (agent cannot lift them: `SETTINGS` in query text → 164 READONLY); profile now `max_execution_time = 60, max_result_bytes = 10000000` only. Verified on all 8 runners with `clickstack/chsql_probe.sh`. 14 finished + 6 running SQL pairs retired with `clickstack/fleet/invalidate.py --campaign phase1 --agent-pattern chsql` (records → `phase1.invalid-sql.bak`, files → `results/invalid/`; on the primary with `--clusters c3..c8`); all 24 SQL pairs re-queued: c3–c8 fresh `~/fleet/queue` (3–4 pairs each, Codex first), c1 `~/fleet/queue2` (opus only_t4, only_t0), c2 `~/fleet/queue2` (fable only_t4; fable only_t3 was already its 5th line), chained with `chain_queue.sh` behind the running queue process. Phase 1 ETA ≈ 22 Nov 15:00 UTC.
- **Report.** `clickstack/report.py` now has tabs Overview / Tool use / Gaps vs stock / By model / By arm / By problem / Attempts / Queries / Method & notes, a problem-set filter (`?set=sregym-lite`), `--campaign` taking several stems, `--notes` + auto `clickstack/notes/<campaign>.md`, per-attempt `kverbs` (kubectl verb+kind), `areas` (telemetry area per MCP call), `n_probe`, `n_fix`, judge `checks`/`dims`, and a failure `mode`; browser-side gap analysis against the same model's stock arm with rule-based readings (live probing / not captured at this tier / captured but not queried / right data, wrong conclusion). Lite archive regenerated with `--campaign lite-sweep lite-sweep-fill`. Rendering checked headless (no JS errors). Notes: `clickstack/notes/lite-sweep.md`, `clickstack/notes/phase1.md`.
- **Fleet.** ClickStack sources created for c3–c8 (Logs/Traces/Metrics cN; ids in `clusters.json`), proxies on c3–c8 given `--allowed-sources` (their units had none: clones predated the filter; agents saw every cluster's sources), `clickstack.env` source ids updated; every cluster can now run every arm. `status.sh` reads `~/fleet/queue` and reports queue2/queue3 logs.
- **Phase 2.** `clickstack/fleet/plan_phase2.py` wrote `~/fleet/queue3` on every runner (162 lines: 54 pairs × chunks `sregym-71-rest-a/b/c` of 18 problems, chunk-major, discriminating arms first, balanced by hours with head starts c1=4 h, c2=6 h), chained behind each runner's current queue (`chain_queue.sh`). ≈115 h of work per cluster after phase 1 → ends ≈ 26–27 Nov on 8 clusters. User to decide whether to scale out or cut (n=1 for kubectl+telemetry arms; drop tiers 1–2).

## Status, 2026-11-20 (evening): fleet of 8 clusters, phase 1 of the wider run

User decisions: models reduced to claude-opus-5-5, claude-fable-5-2, gpt-6.1-sol; problem set = the 71 runnable problems (public 90-cohort ∩ our three apps ∪ Lite; `clickstack/problem_sets.json`: `sregym-71`, `sregym-71-rest` (54 non-Lite), `lite-12`); new arms: kubectl + ClickStack/SQL at tiers 3 and 4, telemetry-only tier 4, and a stock-telemetry-without-kubectl arm (`*_obs`, SREGym's Prometheus/Loki/Jaeger MCP mounts over SSE at MCP_SERVER_PORT 9954, path-prefixed egress rules, no /kubectl); tier 4 = tier 3 data + `40-investigation.md` skill rules; 8 clusters; pinned ClickStack wanted (not yet: the managed MCP accepted per-cluster sources via save_source, so c2 runs ClickStack arms through the managed server with proxy-side source filtering `--allowed-sources`; the self-hosted pinned instance needs the user's one-time UI registration and is deferred).

Fleet: VMs `sregym-c{2..8}-{control,worker-1,worker-2}` (n2-standard-8) and `sregym-c{2..8}-runner` (cloned from image `sregym-runner-20261120`); laptop ssh config has a `sregym-c*` wildcard. Each cluster writes to its own ClickHouse database `cN` (ingest user granted CREATE DATABASE/TABLE/… on `cN.*`); `clickstack/fleet/`: `clusters.json`, `finish_cluster.sh cN` (kubeconfig from `~/fleet/cN/.kube/config` on the primary → runner, node-tune, `deploy.sh` with CLICKSTACK_DATABASE, skills rebuilt per database), `assign.py`, `run_queue.sh ~/fleet/queue` (per runner, nohup, log `~/fleet-queue.log`), `collect.sh` (rsync results into `results/cN/`, merge manifests), `status.sh`. Ansible runs per cluster with `HOME=~/fleet/cN` so kubeconfigs don't collide; the playbook needs `/users/sregym/.kube` pre-created on nodes (else fatal on workers).

Phase 1 (campaign `phase1`, Lite 17 × 3 attempts, 36 new pairs = 1,836 attempts): queues installed on all runners (c1/c2 carry the ClickStack arms); started 21:55 UTC on c3, c6, c7, c8, c2; c4 after its `codex_chsql_only_t4` shakedown, c1 after the `*_obs` shakedown, c5 after its rebuild. Lite report archived to `results/archive/report-lite-sweep-2026-11-20.html` (+ manifests) and `results-report-lite-sweep-2026-11-20.html` locally (1,512 attempts; 4 edge attempts never deployed). Phase 2 = `sregym-71-rest` × all 18 arms × 3 models × 3 attempts (~8,700 attempts, ~6 days on 8 clusters). Stray: the old `sql-sweep` queue had started on c1 and was killed (a few attempts of fable-5-2 × claudecode_chsql recorded under campaign `sql-sweep`).

## Status, 2026-11-12

- Campaign `anthropic-sweep` finished: 24 runs, one problem, four Claude models, six arms. Results in `documentation.md` section 9.
- `clickstack/report.py` rewritten: one HTML file, all aggregates computed in the browser from filters (campaign, model, arm, harness, problem, text). Headline tables by arm and model (pass@1, pass@k, mitigation, TTD, TTM, tokens, ClickStack and kubectl calls, SQL share, tool errors, cost), model × arm matrix, tabs by model / arm / problem / attempt / tool calls and SQL. Each attempt opens to its fault, diagnosis, judge verdict, tokens and full tool trace with the agent's words before each call. CSV export.
- Campaign `lite-sweep` started 19:06 UTC: Claude Code arms × {opus-5-5, fable-5-1, fable-5-2} × SREGym-Lite (17 problems) × 3 attempts = 918 attempts, about 7 min each, about five days. `clickstack/campaign_status.py lite-sweep` shows progress and a finishing estimate.
- Codex half (`codex*` arms × {gpt-6.1-sol, gpt-6.1-terra}) to be queued behind it by `clickstack/queue_codex.sh`, which checks the OpenAI key and model ids before launching. Key verified 20:48 UTC: `clickstack/codex_smoke.sh` ran a Codex turn through the MCP proxy inside the agent image with gpt-6.1-sol. `gpt-6.1-astra` does not exist for this key (6.1 has sol and terra; astra exists only as gpt-6-astra). The between-pairs shakedown (`run_between_pairs.sh` paused campaign.py with SIGSTOP at the pair boundary, ran `shakedown_codex.sh`, then SIGCONT) ran 00:27 to 00:39 UTC on 13 Nov: codex_clickstack × gpt-6.1-sol and claudecode_clickstack × claude-opus-5-5 both passed diagnosis (1.00) and mitigation on the hotel selector problem; Codex made 9 ClickStack calls, Opus none. Codex queue re-armed afterwards. First Lite pair (Opus stock) finished with exit 2: 50/51 attempts, one deploy exhausted; campaign tools now count exit 2 as finished and the report shows in-progress pairs. 13 Nov 13:00 UTC: the deploy failures were the truncation hook's own count()==0 check failing while collectors kept writing (edge_request_filter_cpu_saturation lost 1-3 attempts in each of the first four pairs). Hook rewritten to verify by data-part age with alter_sync=2; `fill_gaps.py --wait` armed to re-run missing attempts as `lite-sweep-fill` after the Codex half; hourly report cron installed; gpt-6.1-terra smoke test passed; Codex half dry run resolves all 12 pairs. 15 Nov 09:00 UTC: 10/18 Claude pairs done (507/918 attempts, 7.3 min each, Claude half ends ~17 Nov 11:00 UTC). Early read (Lite, pass@1): Opus stock 96%, kubectl+ClickStack 96%, ClickStack-only t0 49% / t1 75% / t2 77% / t3 79%; Fable 5.1 stock 96%, kubectl+ClickStack 94%, t0 71%, t1 73%. Second data-loss cause found: edge_request_filter_cpu_saturation's injection check (normal request must time out within 30 s; proxy has no CPU limit) is flaky; `SREGYM_MAX_DEPLOY_RETRIES` added to main.py (default 3; 6 in sregym.env for the fill and Codex half); report gained a "balanced problems only" switch. Report cron had crashed on a Claude Code event with a string `message` (fixed). 16 Nov 10:45 UTC: 14/18 pairs done, 702/918 attempts; Claude half ends ~17 Nov 14:00 UTC. Fable 5.1 final: stock 96%, kubectl+ClickStack 94%, t0 71%, t1 73%, t2 82%, t3 78%. Fable 5.2: stock 100%, kubectl+ClickStack 100%, t0 running. 17 Nov: Claude half finished 07:22 UTC (886/918; 32 for the fill). Codex half launched 10:22 UTC (3 h late: a stale watcher shell with the literal "campaign.py" in its command line matched the queue's `pgrep -f campaign[.]py`; patterns in queue_codex.sh, fill_gaps.py and campaign_status.py now match `clickstack/campaign[.]py --name`). Fable 5.2 final: stock 100%, kubectl+ClickStack 100%, t0 82%, t1 92%, t2 94%, t3 98%. `clickstack/summarize.py` prints text tables from the report loaders. 18 Nov 13:00 UTC: managed MCP renamed `clickstack_trace_top_time_consuming_operations` → `..._ops` and added `clickstack_submit_feedback` overnight; strict preflight killed 8 Codex pairs and all ClickStack-arm fills at launch (02:38–13:00). Client fixed: CORE_TOOLS-only preflight, `submit_` in the proxy deny list, skills rebuilt; Codex pairs relaunched (ends ~20 Nov), fill re-armed. Codex so far: sol stock 94%, sol kubectl+ClickStack 94%, sol t0 59%, terra stock 90%. 20 Nov 09:13 UTC: all 30 pairs done (1,512 attempts, 1,494 scored); gap fill running (31 attempts). Final tables in documentation.md section 9. Headline: kubectl arms 88-100% for every model; ClickStack-only t0 49-82%, t3 78-98% (Fable 5.2 98%, sol 94% = their kubectl arms); tier 0→1 is the biggest step. 20 Nov afternoon: built the ClickHouse-SQL arms (`*_chsql`, `*_chsql_only_t0..t3` for both harnesses): mcp-clickhouse 0.7.0 pinned as systemd `sregym-chsql-mcp` on 172.17.0.1:9400 (read-only user, 500-row/60-s caps via settings profile `sregym_agent`), clients `clients/claudecode_chsql` + `clients/codex_chsql` (copies of the ClickStack clients with SQL skills), 10 registrations, tooling families; `queue_sql.sh` armed to run a shakedown then `sql-sweep` (Fable 5.2 + gpt-6.1-sol, 510 attempts, ~2.5 days) after the fill.
- Found, not yet fixed (kept constant during the campaign): SREGym's kubectl proxy returns 502 for `kubectl get` on kinds whose empty Table has `rows: null` (LimitRanges). One-line fix in `agent_visibility_policy.py` to propose upstream after the campaign.

## How SREGym runs (what the plan has to fit)

- **No long collection period.** Each attempt: reset cluster, deploy app, baseline traffic (0 to 120 s), inject fault, propagation wait (0 to 180 s), agent (30 min cap), judge, teardown. Telemetry is collected inside that cycle.
- **The `observe` namespace is wiped after every problem.** Our collectors live in a namespace that exists when SREGym captures its cluster baseline.
- **Filtered mode blocks pod egress** with a Calico policy. One escape hatch: `SREGYM_TELEMETRY_NAMESPACE` names a single namespace allowed out and `SREGYM_TELEMETRY_EGRESS` lists `cidr:port` destinations.
- **Agents run in Docker on the runner host.** External endpoints need `--allow-agent-endpoint`.
- **Diagnosis is graded by an LLM judge** (structured fault spec, 0.70 checklist threshold). **Mitigation is graded by a live oracle** and always needs kubectl writes.
- **Precedents:** `clients/claudecode_radar` (removes kubectl, swaps in an MCP server, host-side preflight) and the Chiron hook in `clients/claudecode` (HTTP MCP with headers). The ClickStack client merges them.

## Architecture

```
GCE (kubeadm + Calico)                                                 ClickHouse Cloud
┌───────────────────────────────────────────────────────────────┐
│ app namespaces ─traces─▶ observe/otel-collector (SREGym's)     │
│                                   │ otlp, in-cluster           │
│ otel/ (persistent namespace)      ▼                            │
│   daemonset collector ──▶ gateway collector ──▶ clickstack-otel-collector ──https:8443──▶ ClickHouse
│   deployment collector ─▶ (filters, transforms)  (schema owner, writes otel_*)      │     + ClickStack UI
│   node-state daemonset ─▶                                      │                      │
└───────────────────────────────────────────────────────────────┘                      ▼
runner VM: main.py, judge, agent containers ───────────────────────────▶ mcp.clickhouse.cloud/clickstack
          TRUNCATE hook ────────────────────────────────────────────────▶ ClickHouse :8443
```

## Phase 0: GCE infrastructure

GKE will not work: the Ansible playbook needs SSH and root on every node. Use plain VMs.

1. **Cluster VMs**, Ubuntu 22.04/24.04 x86_64. Lite: 1 control + 2 workers, `n2-standard-8`, 200 GB each. Full suite: 1 control `n2-standard-8` + 3 workers `n2-standard-16`. Nodes need outbound internet to reach ClickHouse Cloud on 8443.
2. **Runner VM**, `n2-standard-4`, Linux: Docker, Python 3.12, uv, kubectl, helm 4, Claude Code CLI, `clickhouse-client`, the repo with submodules.
3. Fill `scripts/ansible/inventory.yml`, run the playbook, confirm `kubectl get nodes` from the runner.
4. Firewall: runner to control node on 6443, nodes open to each other, SSH from your IP only.

## Phase 1: ClickHouse side (one-time, with the provided credentials)

1. Create database `otel` and an ingestion user with `SELECT, INSERT, CREATE DATABASE, CREATE TABLE, CREATE VIEW` on it, as the getting-started guide specifies. Keep the admin credentials on the runner for the truncation hook.
2. Create a **read-only** user for the `mcp-clickhouse` arm and for the agent's SQL console access (the "Service Read Only" level).
3. Enable MCP on the service and note the service id. Authorise the MCP once on the runner with the agent's identity: `claude mcp add --transport http clickstack https://mcp.clickhouse.cloud/clickstack --header "x-service-id: <id>"`, then `/mcp`. The docs describe this endpoint as OAuth-only; if the credentials you provide are a bearer key instead, the client just sends a header.
4. Set TTL on the `otel_*` tables to a few days. Truncation handles isolation; TTL only bounds cost.

## Phase 2: Collectors in the cluster

All in a persistent namespace `otel`, deployed **before the first SREGym run** so the baseline snapshot includes it. On the runner: `SREGYM_TELEMETRY_NAMESPACE=otel` and `SREGYM_TELEMETRY_EGRESS=<clickhouse-cloud-cidrs>:8443` (or `0.0.0.0/0:8443`).

1. **ClickStack collector** (`docker.clickhouse.com/clickhouse/clickstack-otel-collector`, via the upstream OTel Helm chart in deployment mode). Env: `CLICKHOUSE_ENDPOINT=https://<host>:8443`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `HYPERDX_OTEL_EXPORTER_CLICKHOUSE_DATABASE=otel`, `OTLP_AUTH_TOKEN`. It owns the schema and is the only pod that talks to the cloud. Its config cannot be modified beyond extra receivers, which is why the next component exists.
2. **Gateway collector** (contrib, deployment). Receives OTLP from everything below, applies the visibility filters and the Kubernetes-state transforms once, exports `otlphttp` to the ClickStack collector with the `authorization` header set to `OTLP_AUTH_TOKEN`.
3. **SREGym's collector** (`sregym/observer/otel_collector/otel-collector.yaml`, re-applied every problem). Add an `otlp` exporter to `otel-gateway.otel.svc:4317` on both traces pipelines and both spanmetrics pipelines. Keep the Jaeger and Prometheus exporters so arm A is untouched.
4. **DaemonSet collector** (contrib, Kubernetes guide values): `logsCollection`, `hostMetrics`, `kubeletMetrics`, `kubernetesAttributes` with all pod labels and annotations; plus a `prometheus` receiver on the kubelet cAdvisor endpoint and a `journald` receiver for kubelet and container runtime units.
5. **Deployment collector** (contrib, Kubernetes guide values): `kubernetesAttributes`, `kubernetesEvents`, `clusterMetrics`; plus the `k8sobjects` snapshots from the next section and a `prometheus` receiver with the app scrape jobs copied from `sregym/observer/prometheus/prometheus/values.yaml` (hotel-reservation, Slack Spine, Frappe, Saleor, TiDB, blackbox, Locust).
6. **Visibility filters** on the gateway, mirroring SREGym's API proxy and Promtail rules: drop namespaces `khaos`, `chaos-mesh`, `sregym`; workloads labelled `app=load-generator`, `app=locust-fetcher`, `job=workload`, `opentelemetry.io/name=load-generator`; Helm release secrets; Chaos Mesh CRDs and events; every Secret's `data`. Without these a cluster-wide object watch ingests the injector scripts and hands the agent the answer.
7. **Truncation hook.** A ten-line patch in `Conductor.start_problem` runs `SREGYM_PRE_DEPLOY_HOOK` before `deploy_app`; the hook truncates `otel.otel_logs`, `otel_traces` and the `otel_metrics_*` tables over HTTPS. This gives ClickStack the same empty-at-deploy start that Prometheus and Loki get from the `observe` wipe.
8. **Validate** with `uv run main.py --use-external-harness --problem <id>`: deploys the app, injects the fault, exits with the cluster up. Confirm in the UI that the app namespace's traces, logs, metrics, events and objects arrive and that `khaos` does not.

## Capturing Kubernetes state as OpenTelemetry data

Everything kubectl reads is an object (a log record whose body is the manifest), a count (a metric), or a change (an event or watch record). Standard contrib components cover all three. Collection is tiered so the experiment reports which tier unlocked which problems, and each tier has a matching skill (next section).

| Tier | Component | Captures | kubectl equivalent | Expected gain |
|---|---|---|---|---|
| 0 | `k8s_cluster`, `kubeletstats`, `hostmetrics`, `filelog`, `kubernetesEvents` presets | replica counts, pod phase, restarts, requests and limits, quotas, node conditions, per-pod CPU and memory, container logs including previous instances, Events | `get` status columns, `top`, `logs`, `get events` | the 47 problems telemetry already solves |
| 1 | `k8sobjects` pull every 60 s **and** watch: Pod, Deployment, ReplicaSet, StatefulSet, DaemonSet, Job, CronJob, Service, Endpoints, EndpointSlice, Ingress, PVC, PV, Node, Namespace | full specs and status, plus a timestamped change history kubectl lacks | `get -o yaml`, `describe` | +29, to 76 |
| 2 | `k8sobjects`: ConfigMap, Secret (metadata only), NetworkPolicy, Role, RoleBinding, ClusterRole, ClusterRoleBinding, ServiceAccount, PriorityClass, ResourceQuota, LimitRange, HPA, PDB, webhook configurations, CRD, operator CRs (Calico `ippools`, `bgppeers`, `felixconfigurations`; TiDB `tidbclusters`); kube-system component logs; `journald` kubelet | flag ConfigMaps, Corefile, webhooks, RBAC, admission and scheduling logs, eviction messages | `get configmap -o yaml`, `logs -n kube-system`, node `journalctl` | +29, to 105 |
| 3 | one small `node-state` DaemonSet printing a JSON line per minute | sysctls (`ip_local_port_range`, conntrack count and max, `fs.file-max`), kubelet `/configz`, NTP offset, `/etc/hosts`, disk pressure | `kubectl debug node`, `sysctl`, `chronyc` | clock drift, conntrack and eviction thresholds become direct reads. Non-standard, labelled |
| 4 (optional) | `sqlquery` receiver on the apps' Postgres and MySQL | `pg_locks`, `pg_stat_activity`, `pg_db_role_setting`, `SHOW GRANTS`, `read_only` | `kubectl exec psql` | 4 or 5 of the 12 in-container problems |
| optional | kube-apiserver audit log (`filelog` on the control node, RequestResponse level for app namespaces) | who changed what, when, with the diff | nothing; kubectl has no history | large, but more than kubectl offers; its own arm if used |

**Making it addressable.** Object records land in `otel_logs`. A `transform` processor on the gateway sets `service.name` to `k8s-objects` (pull), `k8s-changes` (watch), `k8s-events`, `node-state` or `db-state`; promotes `k8s.object.kind`, `k8s.object.name`, `k8s.namespace.name`, `k8s.object.uid`, `k8s.object.resource_version` and the watch type to `LogAttributes`; and deletes `metadata.managedFields`, the `last-applied-configuration` annotation and Secret `data` from the body. The latest manifest of an object is then one query: `argMax(Body, Timestamp)` grouped by uid, or a search on `service.name:k8s-objects k8s.object.kind:Service k8s.object.name:frontend` sorted by time.

**Volume.** A few hundred objects per minute at a 60 s interval is under 1 MB per minute.

**Out of reach by design.** Secret values stay hidden. Of the 12 in-container problems, tier 4 recovers the database ones; the rest are reported as out of scope, not failures.

## Phase 3: Skills, one per tier

The MCP server knows the tables, not what we put in them. A skill file supplies that knowledge. There is one skill per tier, cumulative, so the ClickStack-only arms differ only in collection plus the skill that explains it.

**Layout.** `clients/claudecode_clickstack/skills/tier<N>/clickstack-telemetry/SKILL.md`. Same skill name at every tier, so the task instruction can always say "read the `clickstack-telemetry` skill first". The client mounts the selected tier's directory at `.claude/skills/` in the agent's workspace; the tier comes from `CLICKSTACK_TIER` in the agent's `kickoff_env`.

**Contents by tier** (each tier includes everything above it):

| Tier | The skill adds |
|---|---|
| 0 | The three sources and their tables (`otel_logs`, `otel_traces`, `otel_metrics_gauge/sum/histogram`) with the columns that matter: `Timestamp`, `ServiceName`, `Body`, `SeverityText`, `LogAttributes`, `ResourceAttributes`, `TraceId`. Which MCP tool for which question: `service_health` first, `search` for logs and events, `timeseries` and `promql` for metrics, `trace_waterfall` for a slow request, `sql` when a Map lookup or join is needed. Where container logs are (`k8s.namespace.name`, `k8s.pod.name`, `k8s.container.name`), where Events are (`service.name:k8s-events`, `k8s.event.reason`, body is the message), the metric names from `k8s_cluster`, `kubeletstats`, `hostmetrics`, cAdvisor and spanmetrics, and `probe_success` from blackbox. A statement that there is no kubectl and that all cluster state must come from these sources |
| 1 | `k8s-objects` and `k8s-changes`: the kinds present, the attribute names, the "latest snapshot" query, how to read a change record, and worked lookups: a Service's selector against its Pods' labels, a Deployment's env and probes, an Endpoints object with no addresses, a Pod's conditions and container statuses, a Node's taints and allocatable |
| 2 | The added kinds and what to look for in each (ConfigMap `data` including the CoreDNS Corefile and feature-flag files, NetworkPolicy rules, RoleBindings, webhook `clientConfig` and `failurePolicy`, PriorityClass values, ResourceQuota status, Calico and TiDB CRs). The kube-system component logs by container name and the log lines that matter: admission webhook timeouts, `FailedScheduling`, controller errors, CoreDNS NXDOMAIN. The `journald` kubelet stream and eviction messages |
| 3 | The `node-state` record schema field by field, with the normal ranges, and which symptoms each field explains |
| 4 | The `db-state` record schema: lock rows, role settings, grants, `read_only` |

**Guardrails.** A skill names attribute conventions and query patterns only. It never names an SREGym application, problem, fault, oracle or rubric. A unit test enforces this, as `clients/claudecode_radar` does for `routing.md`. Arm B (kubectl plus ClickStack) gets the same tier skill as the collection it runs with. Arm A gets nothing extra: SREGym's MCP tool docstrings are its equivalent.

**Verification before scoring.** Deploy a healthy app with `--use-external-harness`, then put a dozen factual questions to an agent holding each tier's skill: the selector of a named Service, the env of a named Deployment, the pods restarted in the last ten minutes, a named ConfigMap's keys, a node's conntrack max. Each must be answered correctly from telemetry alone. This checks the skill against the real schema before any benchmark time is spent.

## Phase 4: The ClickStack agent client

`clients/claudecode_clickstack`, modelled on `clients/claudecode_radar`:

- **MCP config**: `type: http`, URL `https://mcp.clickhouse.cloud/clickstack`, header `x-service-id`, via `--mcp-config` and `--strict-mcp-config`. Credentials: copy the runner's pre-authorised Claude Code credentials into the container's private temp storage, as the Codex client does with its auth file; fallback is `mcp-remote` behind an HTTP proxy on the runner, the Radar pattern. Token refresh across a 20-hour campaign is the first shakedown item.
- **Egress**: `--allow-agent-endpoint https://mcp.clickhouse.cloud` plus the auth host used for refresh.
- **Tool allowlist**: read tools only (`list_sources`, `describe_*`, `list_metrics`, `service_health`, `timeseries`, `table`, `search`, `event_patterns`, `event_deltas`, `emerging_signals`, `sql`, `promql`, `trace_waterfall`, `trace_top_time_consuming_operations`, `query_tile(s)`, `get_*`). No `save_*` or `delete_*`. Server side, the agent identity has the read-only console level.
- **Preflight**: MCP `initialize` and `tools/list` from inside the container; store the tool list hash, service id and skill tier with the result.
- **Agents in `agents.yaml`**, one per arm so results files stay apart: `claudecode_clickstack` (kubectl plus ClickStack, SREGym's Prometheus, Loki and Jaeger tools withheld, `CLICKSTACK_TIER=2`), and `claudecode_clickstack_only_t0` through `_t3` (`cluster_access: false`, diagnosis only, tier skill mounted).
- **Optional `claudecode_mcp_clickhouse`**: the official `mcp-clickhouse` server pointed at the same database with the read-only user, same tier skill, no ClickStack MCP. Tests whether the ClickStack tools add anything over raw SQL.

## Phase 5: Experiment design

Same cluster, agent model, `--judge-model` (different from the agent), `--agent-timeout`, `--reasoning-effort`, `--baseline`, `--propagation`, filtered network mode, truncation hook on for every arm.

| Arm | Cluster access | Observability | Collection and skill | Stages |
|---|---|---|---|---|
| A. Stock | kubectl | SREGym MCP: Prometheus, Loki, Jaeger | SREGym default | diagnosis, mitigation |
| B. ClickStack | kubectl | ClickStack MCP | tiers 0 to 2, skill 2 | diagnosis, mitigation |
| C0 to C3. ClickStack only | none | ClickStack MCP | tier N, skill N | diagnosis |
| D. Optional | none | `mcp-clickhouse` | tier 2, skill 2 | diagnosis |

C0 to C3 is the direct test of the 47 → 76 → 105 prediction, with the skill held consistent with the data. B against A is the headline backend result. D against C2 isolates the value of the ClickStack tools over raw SQL.

Problem sets, in order:
1. **Shakedown**: 3 problems, 1 attempt, arms A and B. Fix plumbing, MCP credentials, filters, truncation.
2. **Skill verification** (Phase 3) for every tier.
3. **SREGym-Lite** (17 problems), `--n-attempts 3`, all arms. Arm A is leaderboard-comparable.
4. **Telemetry-relevant subset** via `sregym/conductor/tasklist.yml`: the 25 telemetry-primary, 13 both-needed and 26 either problems, then the tier 1 and tier 2 k8s-primary problems. Arms C0 to C3.
5. Full 117, 1 attempt, if budget allows.

Time per attempt is 15 to 40 min; Lite at 3 attempts is about 20 hours per arm. With six arms, plan two weeks sequentially, or run arms in parallel on separate clusters each writing to its own database.

## Phase 6: Evaluation and reporting

- Results: `results/<agent>/<problem>/*_results.csv` (`Diagnosis.success`, `Mitigation.success`, judge reasoning, incomplete reasons); `sregym/results/report.py` for pass rates; an ATIF `trajectory.json` per attempt with every tool call.
- **Diagnosis and mitigation reported separately.** Only diagnosis can move with the backend.
- Three cuts: Lite, the telemetry-relevant subset, and by class. For C0 to C3, list the problems that flipped and the tier that flipped them.
- pass@1 and pass@3 from three attempts; rerun `unscored` and `incomplete` rows.
- From trajectories, per arm: tool calls, share of observability against kubectl calls, time to the first correct component, tokens, and whether the skill was read.
- Freeze with the results: SREGym commit, collector images and configs, filter rules, skill files by hash, MCP tool list hash, judge model.

## Open items and risks

1. **MCP credentials in a headless container.** OAuth token forwarding or a host-side proxy; refresh behaviour over long campaigns. Validate in the shakedown. A bearer key, if that is what is provided, removes this item.
2. **Read-only enforcement for the ClickStack MCP.** Client allowlist plus the read-only console level; confirm by attempting a save.
3. **Egress rule granularity.** The telemetry exception takes CIDRs; ClickHouse Cloud is a hostname. Resolve and pin, or use `0.0.0.0/0:8443`.
4. **`k8sobjects` attribute names vary by collector version.** Pin the contrib image and verify the transform output in the UI before writing the tier 1 skill.
5. **Skill variance.** Claude Code decides when to invoke a skill. The instruction line that names it should make reading it near-certain; the trajectories will show if it did not.
6. **The managed MCP tool list is undocumented.** Capture it at preflight; adjust the allowlist and the tier 0 skill if names differ.

## Code footprint: what changes where

SREGym has a formal plug-in point for agents and none for observability backends, so the work splits into additive pieces and a few small core changes.

**Additive, no core change**
- `clients/claudecode_clickstack/` with its driver, skills and README, plus entries in `agents.yaml` (schema in `sregym/agent_registry.py`: `kickoff_command`, `kickoff_env`, `cluster_access`, `install_script`). `SREGYM_AGENT_REGISTRY` can point at a private registry file, so nothing in the repo needs editing to run.
- Everything in the `otel` namespace: the ClickStack collector, gateway, DaemonSet and Deployment collectors, node-state DaemonSet. Plain Kubernetes manifests outside SREGym.
- Pod egress for the collectors via the existing `SREGYM_TELEMETRY_NAMESPACE` and `SREGYM_TELEMETRY_EGRESS` variables; agent egress via `--allow-agent-endpoint`.

**Small core changes, written to be upstreamable**
- `sregym/observer/otel_collector/otel-collector.yaml`: an extra OTLP exporter on the traces and span-metrics pipelines. Apps are hard-wired to send traces to this collector, so there is no way around it. Make it an optional `SREGYM_OTLP_FORWARD_ENDPOINT` that `OtelCollector.deploy` injects, in the spirit of the existing telemetry-egress variables.
- `Conductor.start_problem`: an optional `SREGYM_PRE_DEPLOY_HOOK` command run before `deploy_app`, used for truncation.
- Arm B only: hide the `observe` namespace from the agent's cluster view so kubectl cannot reach Prometheus, Loki and Jaeger directly. `HIDDEN_NAMESPACES` in `sregym/service/agent_visibility_policy.py` is a constant today; make it extendable by environment variable. Without this, arm B is "stock plus ClickStack", which is still a valid and arguably more realistic comparison, so decide which B you want before the shakedown.

**SREGym's own MCP server (`mcp_server/`)**
- It runs in the `sregym` namespace with SSE mounts for kubectl, Prometheus, Loki, Jaeger and submit, and it is used only by the Stratus reference agent. The CLI agents (Claude Code, Codex, Gemini, OpenCode, Cursor, Copilot) never connect to it: they get a kubeconfig that points at the conductor's filtering proxy and submit through the conductor's HTTP API.
- The Claude Code arms therefore do not use it. It keeps being deployed each problem, which is harmless, and `cluster_access: false` withholds its port anyway.
- It becomes relevant only if you also want to benchmark Stratus with ClickStack. Then the path is a `clickstack_server.py` mount alongside the Prometheus one, which is the upstream-friendly way to give every MCP-consuming agent the same tools. Not needed for the planned arms.
