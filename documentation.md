# SREGym on ClickStack

This document explains, from the beginning, what we are testing, how the test
environment was built, which changes were made to SREGym, how each test run
works, and how to read the results.

It assumes no prior knowledge of SREGym, ClickStack or Kubernetes benchmarks.


## 1. The question we are answering

SREGym is a benchmark for AI agents that do site reliability work.

It runs a real application on a Kubernetes cluster. It breaks something on
purpose. Then it asks an AI agent to find the cause and, where possible, fix it.

ClickStack is ClickHouse's observability product. It stores logs, metrics and
traces in ClickHouse. It has an MCP server, which is an interface that lets an
AI agent query that data with tools.

We want to know four things.

1. Can an agent find the cause of a fault using only what ClickStack holds,
   without any direct access to the cluster?

2. How does that compare with the usual approach, where the agent runs
   `kubectl` commands against the cluster?

3. Does the agent do better when ClickStack also holds Kubernetes state
   (the configuration of the cluster's objects), not just logs, metrics and
   traces? And how much state is enough?

4. Which model does best in each setting, and does the answer hold across two
   agent harnesses: Claude Code with Claude models, Codex with OpenAI models?

5. How much of ClickStack's value is in its purpose-built tools, as opposed to
   having the data in ClickHouse at all? A second set of arms gives the agent
   raw SQL over the same tables and nothing else.


## 2. Words used in this document

**Conductor.** The part of SREGym that runs a test. It deploys the application,
injects the fault, talks to the agent and grades the result.

**Problem.** One fault scenario. SREGym has 117. Each has a known root cause.

**Agent.** The AI system under test. Here it is a command-line coding agent,
Claude Code for Claude models or Codex for OpenAI models, running inside a
container with a fixed set of tools.

**Judge.** A language model that compares the agent's written diagnosis with the
known root cause and decides whether it is correct. It works through a
checklist and passes the diagnosis if the weighted score is at least 0.70.

**Oracle.** A program that checks the live cluster after the agent says it has
fixed the problem. It decides whether the mitigation worked.

**MCP tools.** Functions the agent can call to query ClickStack, for example
"search logs" or "show the health of each service".

**Skill.** A short document the agent reads before it starts. It explains where
each kind of data lives in ClickStack and how to query it. The MCP server does
not know this on its own.

**Tier.** How much Kubernetes state is captured into ClickStack. Tier 0 is none.
Tier 3 is everything we capture. Section 5 lists them.

**Arm.** One configuration of the agent that we compare against the others.

**Run.** One agent working on one problem once, from a clean cluster. SREGym
calls this an attempt. The campaign runs three attempts per problem.

**Harness.** The command-line program that drives the model: Claude Code or
Codex. Each harness has its own tool loop, so a model is only compared with
other models in the same harness.

**Campaign.** One batch of runs: a set of arms, models and problems, and a
number of attempts, run one after another on the one cluster.

**pass@1 and pass@k.** pass@1 is the share of attempts whose diagnosis the judge
accepted. pass@k is the share of (model, arm, problem) cells where at least one
attempt passed.

**TTD and TTM.** Time to diagnosis and time to mitigation. SREGym's clock runs
from the moment the agent starts until it submits the diagnosis, and until it
submits the mitigation. Both include every tool call and model response in
between.


## 3. The environment

**Cluster.** Three virtual machines on Google Cloud form a Kubernetes cluster,
built with SREGym's own installation playbook. A fourth machine, the runner,
executes the tests and hosts the agent containers.

**ClickHouse Cloud.** A ClickHouse service with the ClickStack user interface
enabled. All telemetry ends up here.

**Collectors.** Programs inside the cluster that gather telemetry and send it
to ClickHouse. There are four.

- A node agent on every machine collects container logs, node metrics and
  machine logs.

- A cluster collector collects Kubernetes events, cluster metrics, and the
  configuration and status of Kubernetes objects. It also collects the
  application metrics SREGym normally scrapes with Prometheus.

- A small program on every machine records machine settings once a minute,
  such as kernel limits, clock offset and the kubelet configuration.

- SREGym's own collector, which already receives the applications' traces,
  now also forwards them to ClickStack.

**Gateway.** All collectors send to one gateway before ClickHouse. The gateway
removes things the agent must not see (section 6), labels the Kubernetes
state records so they can be found, removes secret values, and drops the
state records that are above the current tier.

**MCP proxy.** The ClickStack MCP server requires a browser login. Agent
containers cannot do that. A small service on the runner holds the login,
refreshes it, and offers the containers a plain local endpoint.

**What is pinned and what is not.** The collector images
(`clickhouse/clickstack-otel-collector:2.19.0`, OpenTelemetry contrib
0.161.0), the agent programs (Claude Code 2.1.318, Codex 0.166.0), the
SREGym commit and the skills are fixed for the whole campaign, and the
campaign record stores, for every pair, the MCP server's reported version and
a hash of its tool list. Two things are not under our control: the ClickHouse
Cloud service, which ClickHouse upgrades, and the managed MCP server at
`mcp.clickhouse.cloud`, which moved to 2.45.0 on 18 November and renamed a
tool (section 6). The report shows which server version each attempt ran
against. To pin the MCP server as well, the ClickStack application has to be
run by us at a fixed image tag against the same database; its built-in MCP
endpoint, `/api/mcp` on the ClickStack frontend with a personal API key,
would also remove the need for the login-holding proxy. That is the setup to
use for the next campaign. The ClickHouse MCP server used by the SQL arms is
already of that kind: `mcp-clickhouse` 0.7.0, installed and pinned on the
runner, with its tool list recorded per pair like the ClickStack server's.


## 4. The tests

### The problems

The campaign uses SREGym-Lite, the 17-problem set that SREGym's public
leaderboard is scored on. The problems, in SREGym's order:

- `cronjob_sidecar_blocks_completion_hotel_reservation`
- `edge_request_filter_cpu_saturation`
- `network_policy_block`
- `env_variable_shadowing_astronomy_shop`
- `mutating_webhook_resource_limits_social_network`
- `finalizer_deadlock_controller_hotel_reservation`
- `kafka_poison_pill_hol_block`
- `internal_traffic_policy_local_astronomy_shop`
- `service_dns_resolution_failure_social_network`
- `service_wrong_pod_selection_hotel_reservation`
- `namespace_memory_limit`
- `valkey_auth_disruption`
- `secret_rotation_stale_env_credentials_astronomy_shop`
- `unschedulable_incorrect_port_assignment`
- `duplicate_pvc_mounts_social_network`
- `rolling_update_misconfigured_social_network`
- `search_rate_retry_collapse_hotel_reservation`

The report shows each problem's fault description, taken from the fault
specification SREGym writes beside every attempt. As an example of what a
problem is: `service_wrong_pod_selection_hotel_reservation` runs a hotel
reservation system with about twenty services and changes the label selector
of the `frontend` Service so that it also matches the search pods, which do
not listen on the frontend port. Requests through the frontend then fail
intermittently. A correct diagnosis names the `frontend` Service, says that
its selector was changed, and explains that traffic is being routed to the
wrong pods.

### The arms

| Arm | Harness | Cluster access | Observability interface | Kubernetes state in ClickStack | Skill | Stages run |
|---|---|---|---|---|---|---|
| `claudecode` (stock SREGym) | Claude Code | kubectl, through SREGym's filtering proxy | Prometheus, Loki and Jaeger, reached through kubectl | none | none | diagnosis, then mitigation |
| `claudecode_clickstack` | Claude Code | kubectl, through SREGym's filtering proxy | stock tools plus ClickStack MCP, 25 read-only tools | tier 2 | tier 2, installed as a Claude Code skill | diagnosis, then mitigation |
| `claudecode_clickstack_only_t0` | Claude Code | none | ClickStack MCP, 25 read-only tools | tier 0: container logs with pod labels, events, metrics, traces | tier 0, installed as a Claude Code skill | diagnosis only |
| `claudecode_clickstack_only_t1` | Claude Code | none | ClickStack MCP, 25 read-only tools | tier 1: tier 0 plus snapshots and changes of the core object kinds | tier 1, installed as a Claude Code skill | diagnosis only |
| `claudecode_clickstack_only_t2` | Claude Code | none | ClickStack MCP, 25 read-only tools | tier 2: tier 1 plus every kind, control-plane and machine logs | tier 2, installed as a Claude Code skill | diagnosis only |
| `claudecode_clickstack_only_t3` | Claude Code | none | ClickStack MCP, 25 read-only tools | tier 3: tier 2 plus node settings | tier 3, installed as a Claude Code skill | diagnosis only |
| `codex` (stock SREGym) | Codex | kubectl, through SREGym's filtering proxy | Prometheus, Loki and Jaeger, reached through kubectl | none | none | diagnosis, then mitigation |
| `codex_clickstack` | Codex | kubectl, through SREGym's filtering proxy | stock tools plus ClickStack MCP, the same 25 read-only tools (the proxy refuses the 10 writing tools) | tier 2 | tier 2, written as `AGENTS.md` | diagnosis, then mitigation |
| `codex_clickstack_only_t0` | Codex | none | ClickStack MCP, the same 25 read-only tools | tier 0: container logs with pod labels, events, metrics, traces | tier 0, written as `AGENTS.md` | diagnosis only |
| `codex_clickstack_only_t1` | Codex | none | ClickStack MCP, the same 25 read-only tools | tier 1: tier 0 plus snapshots and changes of the core object kinds | tier 1, written as `AGENTS.md` | diagnosis only |
| `codex_clickstack_only_t2` | Codex | none | ClickStack MCP, the same 25 read-only tools | tier 2: tier 1 plus every kind, control-plane and machine logs | tier 2, written as `AGENTS.md` | diagnosis only |
| `codex_clickstack_only_t3` | Codex | none | ClickStack MCP, the same 25 read-only tools | tier 3: tier 2 plus node settings | tier 3, written as `AGENTS.md` | diagnosis only |
| `claudecode_chsql` | Claude Code | kubectl, through SREGym's filtering proxy | stock tools plus the ClickHouse MCP server: `run_query`, `list_tables`, `list_databases` (raw SQL, read-only) | tier 2 | tier 2, SQL skill installed as a Claude Code skill | diagnosis, then mitigation |
| `claudecode_chsql_only_t0` | Claude Code | none | ClickHouse MCP server, raw SQL | tier 0 | tier 0, SQL skill | diagnosis only |
| `claudecode_chsql_only_t1` | Claude Code | none | ClickHouse MCP server, raw SQL | tier 1 | tier 1, SQL skill | diagnosis only |
| `claudecode_chsql_only_t2` | Claude Code | none | ClickHouse MCP server, raw SQL | tier 2 | tier 2, SQL skill | diagnosis only |
| `claudecode_chsql_only_t3` | Claude Code | none | ClickHouse MCP server, raw SQL | tier 3 | tier 3, SQL skill | diagnosis only |
| `codex_chsql` | Codex | kubectl, through SREGym's filtering proxy | stock tools plus the ClickHouse MCP server, raw SQL | tier 2 | tier 2, SQL skill written as `AGENTS.md` | diagnosis, then mitigation |
| `codex_chsql_only_t0` | Codex | none | ClickHouse MCP server, raw SQL | tier 0 | tier 0, SQL skill written as `AGENTS.md` | diagnosis only |
| `codex_chsql_only_t1` | Codex | none | ClickHouse MCP server, raw SQL | tier 1 | tier 1, SQL skill written as `AGENTS.md` | diagnosis only |
| `codex_chsql_only_t2` | Codex | none | ClickHouse MCP server, raw SQL | tier 2 | tier 2, SQL skill written as `AGENTS.md` | diagnosis only |
| `codex_chsql_only_t3` | Codex | none | ClickHouse MCP server, raw SQL | tier 3 | tier 3, SQL skill written as `AGENTS.md` | diagnosis only |
| `claudecode_obs`, `codex_obs` | both | none | SREGym's own Prometheus, Loki and Jaeger through its MCP server (`get_metrics`, `get_alerts`, `get_logs`, labels, `get_services`, `get_operations`, `get_traces`, `get_dependency_graph`); no ClickStack | none | none, a routing note only | diagnosis only |
| `claudecode_clickstack_t3`, `codex_clickstack_t3` | both | kubectl | stock tools plus ClickStack MCP | tier 3 | tier 3 | diagnosis, then mitigation |
| `claudecode_clickstack_t4`, `codex_clickstack_t4` | both | kubectl | stock tools plus ClickStack MCP | tier 3 data | tier 4: tier 3 plus investigation rules | diagnosis, then mitigation |
| `claudecode_clickstack_only_t4`, `codex_clickstack_only_t4` | both | none | ClickStack MCP | tier 3 data | tier 4 | diagnosis only |
| `claudecode_chsql_t3`, `codex_chsql_t3` | both | kubectl | stock tools plus ClickHouse SQL | tier 3 | tier 3, SQL skill | diagnosis, then mitigation |
| `claudecode_chsql_t4`, `codex_chsql_t4` | both | kubectl | stock tools plus ClickHouse SQL | tier 3 data | tier 4, SQL skill | diagnosis, then mitigation |
| `claudecode_chsql_only_t4`, `codex_chsql_only_t4` | both | none | ClickHouse MCP server, raw SQL | tier 3 data | tier 4, SQL skill | diagnosis only |

Thirty-six arms, registered under those names in `agents.yaml`: per harness,
the stock arm, the stock-telemetry-without-kubectl arm, three kubectl +
ClickStack tiers, five ClickStack-only tiers, three kubectl + SQL tiers and
five SQL-only tiers. The second campaign runs eighteen of them per model.

**Tier 4** collects nothing new. It is the tier 3 data with an extra skill
section, "Investigation discipline": read the whole manifest of any object
you blame and of its controller before submitting; assume a second cause may
be active and check what the first does not explain; name the origin rather
than the victim (a mutated Pod points at a webhook or controller, a denied
request at the Role whose rules changed); for credential faults say which
side holds the new secret; order changes by time. It targets the failure
modes section 9 found in the tier 3 runs, and because it is a prompt change
rather than a data change the report labels it as its own tier.

**Stock telemetry without kubectl** (`*_obs`) gives the agent SREGym's own
Prometheus, Loki and Jaeger through the MCP server SREGym runs for its
reference agent, over the conductor's port-forward, with the kubectl mount
unreachable. It is the stock-tools counterpart of the ClickStack-only and
SQL-only arms: how far does the standard telemetry stack get an agent that
cannot touch the cluster.

The ClickHouse-SQL arms (`_chsql`) are the ClickStack arms with the ClickStack
MCP server swapped for the open-source ClickHouse MCP server, `mcp-clickhouse`
0.7.0, pinned and run on the runner against a read-only database user. The
agent gets one query tool and two listing tools over the same tables, the same
tier of Kubernetes state, and a skill with the same content rewritten in SQL
terms (table names and time predicates instead of ClickStack's tools and
macros). Results are capped at 500 rows and 60 seconds per query. Comparing
`_chsql` with `_clickstack` at the same tier measures what ClickStack's
purpose-built tools add over raw SQL on the same data.

The stock arm of each harness is the unchanged SREGym agent. It is the
baseline.

The `_clickstack` arm keeps kubectl and adds ClickStack. It is the only
ClickStack arm that can fix the problem, because fixing needs kubectl.

The four `_only` arms of each harness have no kubectl and no cluster
credentials. They are asked for a diagnosis only. They differ in how much
Kubernetes state ClickStack holds and in which skill they read.

The Codex arms mirror the Claude Code arms one for one: the same skills, the
same MCP tools through the same proxy, the same tier gate. The only
difference is how the skill is delivered. Claude Code reads it as a skill
file; Codex reads it as its `AGENTS.md` instructions file.

### The models

The six Claude Code arms run with `claude-opus-5-5`, `claude-fable-5-1` and
`claude-fable-5-2`. The six Codex arms run with `gpt-6.1-sol` and
`gpt-6.1-terra`. That is 30 (arm, model) pairs over 17 problems with 3
attempts each, 1,530 attempts in all.

The judge is `claude-sonnet-5-5` for every run.

The wider run uses three models, `claude-opus-5-5` and `claude-fable-5-2`
under Claude Code and `gpt-6.1-sol` under Codex, on 18 arms each (54 pairs),
in two phases on a fleet of eight clusters. Phase 1 (`phase1`) runs the 36
pairs that have no Lite data yet on the 17 Lite problems, three attempts
each. Phase 2 (`phase2`) runs all 54 pairs on the 54 problems of the
71-problem set that are not in Lite. The 71 are the public 90-fault cohort
restricted to the three applications this cluster runs (62 problems; 20
cohort ids do not exist in the pinned SREGym version and 8 need TiDB or
TrainTicket) plus the 9 Lite problems outside the cohort. The sets are in
`clickstack/problem_sets.json` and loaded through `SREGYM_PROBLEM_SETS_FILE`.

The OpenAI ids were checked against the API with the project's key before
use. `gpt-6.1-astra`, the first choice for the second OpenAI model, does not
exist for this key; the 6.1 generation offers `sol` and `terra`, and `astra`
only exists as `gpt-6-astra`.

### What a run looks like

1. The gateway is set to the arm's tier.
2. The ClickStack tables are emptied.
3. The cluster is reset and the application is deployed fresh.
4. The fault is injected.
5. The agent starts, with a 30-minute limit.
6. The agent submits a written diagnosis. The judge grades it.
7. If the arm has kubectl, the agent then applies a fix and submits again.
   The oracle checks the cluster.
8. Everything is torn down.

Steps 2 to 8 repeat three times per problem. Every attempt starts from a
fresh deployment and empty ClickStack tables, and the
agent has no memory of earlier attempts.

### What is measured

- Diagnosis: whether the judge accepted it, and the composite score from 0 to 1.
- Mitigation: whether the oracle accepted it, for the arms that can fix.
- TTD and TTM from SREGym's own clock, the `TTL` and `TTM` columns of its
  results file.
- Tokens: input, output, cached input and cache-creation tokens as the harness
  reports them at the end of the run, and for Claude Code the harness's own
  cost figure.
- Tool calls: how many went to ClickStack tools and how many to kubectl, how
  many ClickStack calls were raw SQL rather than a builder tool, and how many
  calls of any kind returned an error (invalid arguments, oversized results,
  blocked commands).
- Whether the agent read the skill, and how many network requests the sandbox
  blocked.

### What the agent can see

The agent runs in a container built from `docker/agents/Dockerfile`. The image
holds the agent drivers in `clients/`, the `logger/` and `llm_backend/`
packages, and five small helper files from `sregym/`: `paths.py`,
`service/kubectl.py`, `service/helm.py`, `service/apps/base.py` and
`service/apps/helpers.py`. It holds no problem definitions, no fault
injectors, no judge and no oracle. The agent program itself is installed when
the container starts, at a pinned version.

The container's network reaches three places: the model provider, the MCP
proxy on the runner and, for arms with cluster access, SREGym's filtering
Kubernetes proxy. Everything else is blocked, and blocked requests are
counted. The MCP proxy refuses any ClickStack tool that saves, patches,
deletes or backtests. Diagnoses and mitigations go to the conductor over HTTP.

Nothing the agent reads names the benchmark, the application, the problem or
the grading. A unit test checks the skills and prompts for a list of such
words.

### Fairness rules

- Every run in a campaign uses the same problems, the same 30-minute limit
  and the same judge model, `claude-sonnet-5-5`, so verdicts are comparable.

- Both kubectl arms go through the same SREGym proxy, so a proxy fault
  (section 6 lists one) affects them equally.

- The ClickStack tables are emptied before every run, so no run can see data
  from an earlier one. This matches SREGym, which rebuilds its Prometheus and
  Loki between problems.

- The gateway hides from ClickStack exactly what SREGym hides from kubectl:
  the namespaces where faults are staged, the load generators, Helm release
  records and all secret values.

- The data in ClickStack always matches the skill. A tier 0 run does not
  contain the object records a tier 1 skill describes.

- Nothing the agent reads names the benchmark, an application, a problem or
  the grading. A test enforces this.


## 5. The tiers

| Tier | What ClickStack holds | Roughly equivalent to |
|---|---|---|
| 0 | container logs (each line carries the pod's labels and annotations), Kubernetes events, metrics, traces | `kubectl logs`, `kubectl get events`, `kubectl top` |
| 1 | tier 0 plus a snapshot every minute, and every change, of Pods, Deployments, ReplicaSets, StatefulSets, DaemonSets, Jobs, CronJobs, Services, Endpoints, Ingresses, volumes, Nodes and Namespaces | `kubectl get -o yaml` and `kubectl describe`, with history |
| 2 | tier 1 plus ConfigMaps, Secret metadata, NetworkPolicies, access control objects, quotas, webhooks, Calico objects, control-plane logs and machine logs | reading cluster configuration and kube-system logs |
| 3 | tier 2 plus machine settings: kernel limits, connection tracking, open files, clock offset, kubelet configuration | logging in to a node |

Secret values are never stored. Only their names and metadata are.

How each tier is collected. Tier 0 is standard OpenTelemetry Kubernetes
collection: the node agent tails container logs and attaches the pod's labels
and annotations to every line, the cluster collector receives Kubernetes
events, and metrics come from the kubelet, the node, the applications'
Prometheus endpoints and span metrics derived from the traces. Tier 1 adds the
cluster collector's object watcher: a full snapshot of each object every
minute plus a record of every change, for the core workload and networking
kinds. Tier 2 widens the watcher to every kind, including configuration,
access control, admission webhooks and the network plugin's own objects, and
adds the control-plane and machine logs. Tier 3 adds a small program on every
node that writes a JSON record of kernel and kubelet settings once a minute.
The gateway drops anything above the current tier, so the data in ClickStack
always matches the skill the agent reads.


## 6. Changes made to SREGym

SREGym has a built-in way to add an agent. It has no built-in way to add an
observability backend. So most of the work is new files, and a few existing
files needed small edits.

### New files

`clickstack/` holds the collector definitions, the deploy and verify scripts,
the table-emptying hook, the campaign runner (`campaign.py`), the status tool
(`campaign_status.py`), the Codex queue and smoke test (`queue_codex.sh`,
`codex_smoke.sh`) and the report generator (`report.py`).

`clients/claudecode_clickstack/` holds the Claude Code client, the MCP proxy,
the skills and their build script, and the texts the agent is given.
`clients/codex_clickstack/` holds the Codex client, which reuses the same
skills and the same proxy. `clients/claudecode_chsql/` and
`clients/codex_chsql/` are the ClickHouse-SQL counterparts: the same clients
with the ClickHouse MCP server, a three-tool allowlist and the SQL skills,
built by `clients/claudecode_chsql/skills/build_skills.py`.

`tests/clients/test_claudecode_clickstack.py`,
`tests/clients/test_codex_clickstack.py`, `tests/clients/test_claudecode_chsql.py`
and `tests/clients/test_codex_chsql.py` hold the tests.

### Edited files

| File | Edit | Why |
|---|---|---|
| `sregym/observer/otel_collector/otel-collector.yaml` | one extra exporter | the applications only send traces to this collector, so it must forward them |
| `sregym/conductor/conductor.py` | optional hook before each deploy | empties the ClickStack tables |
| `sregym/service/provider_endpoints.py` | two conditions | the network policy did not recognise the new agent names |
| `main.py`, `sregym/agent_launcher.py` | entries for both agent families | so the new agents get a pre-flight check |
| `sregym/traces/convert.py` | entries for both agent families | so the new agents' transcripts convert to the standard trajectory format |
| `scripts/ansible/setup_cluster.yml` | two small fixes | cloud images need a different boot setting; one package download was slow |
| `agents.yaml` | twenty registrations | the new arms |

In total, eight existing files changed by about 182 lines.

### Problems found and fixed on the way

- Pods could not talk across machines because the cloud firewall blocked the
  tunnel protocol the network plugin uses. A firewall rule fixed it.

- An early version of the gateway stored secret values. The table was
  emptied, the credentials were rotated, and secret values are now removed
  before they leave the cluster.

- The agent container refused to install the newest Claude Code release,
  because SREGym only installs releases older than seven days. An older
  release is pinned.

- Without a build flag, SREGym ran an upstream container image that did not
  contain the new client. The locally built image is now pinned.

- Found during the campaign and left in place: SREGym's filtering
  Kubernetes proxy answers `kubectl get` with an error for some resource kinds
  when the list is empty. Kubernetes returns a Table whose `rows` field is
  `null` rather than an empty list (seen with LimitRanges), the proxy's list
  filter tries to iterate it, and the agent sees "502 Bad Gateway: 'NoneType'
  object is not iterable". It affects both kubectl arms equally and the agent
  carries on, so the harness was not changed mid-campaign. The fix is one line
  in `sregym/service/agent_visibility_policy.py`, treating a null `items` or
  `rows` as empty, and will be proposed upstream.

- SREGym writes a cumulative snapshot of the whole run's results table into
  every problem's results CSV, so the report and status tool deduplicate rows
  by problem and attempt. When an attempt cannot be deployed after three
  tries, SREGym records it with `deploy_failed`, skips the problem's remaining
  attempts, and exits with code 2 after running everything else; the campaign
  tools count such a pair as finished and the report shows the attempt with a
  deploy mark.

- Found on 13 November: the table-emptying hook checked
  that the tables were empty immediately after `TRUNCATE`. Collectors write
  continuously, so whenever the cluster was busy (the teardown of the previous
  problem) a few hundred rows had already arrived and the hook reported
  failure. SREGym treats a hook failure as a failed deploy, and for
  `edge_request_filter_cpu_saturation` all three tries failed in each of the
  first four pairs, losing one to three attempts per pair on that problem. The
  hook now waits for every replica (`alter_sync=2`) and verifies that no data
  part created before the truncation survives, which is the property that
  matters; rows arriving afterwards are allowed. `clickstack/fill_gaps.py`
  re-runs the missing attempts under the campaign name `lite-sweep-fill`
  once the main campaign and the Codex half are done; the report merges the
  two campaigns when both are selected.

- Found on 18 November: the managed ClickStack MCP server can change its
  tool list without notice (a tool was renamed and one added overnight), and
  the clients' preflight treated any missing tool as fatal, which stopped
  every ClickStack arm for ten hours. The preflight now requires only the
  core tools the skills depend on and prints the differences; unknown new
  tools are not offered to the agent, and the proxy's deny list covers the
  new feedback tool.

- Found on 15 November: `edge_request_filter_cpu_saturation` kept losing
  attempts after the hook fix, for a different reason. Its fault injection
  ends with a check that a normal request to the edge proxy now times out
  within 30 seconds; the proxy pod has no CPU limit, so whether the crafted
  traffic saturates it in time depends on the node's spare CPU, and the check
  fails often. SREGym treats a failed check as a failed deploy and gives up
  after three tries. Every third try succeeded in some pairs and none did in
  others, so the problem has attempts for some arms and not for others. Two
  things address it: the number of deploy tries is now read from
  `SREGYM_MAX_DEPLOY_RETRIES` (default three, unchanged for the running Claude
  Code half; six for the gap fill and the Codex half), and the report has a
  "balanced problems only" switch that keeps only problems with a scored
  attempt in every selected model × arm cell, so arms are compared on the
  same problems. The check itself is a SREGym issue to raise upstream.


## 7. How the tests are run

A campaign runs every arm with every model, one after another, on the one
cluster. The Claude Code half:

```
uv run python clickstack/campaign.py --name lite-sweep --suite sregym-lite \
    --n-attempts 3 \
    --models claude-opus-5-5 claude-fable-5-1 claude-fable-5-2 \
    --arms claudecode claudecode_clickstack claudecode_clickstack_only_t0 \
           claudecode_clickstack_only_t1 claudecode_clickstack_only_t2 \
           claudecode_clickstack_only_t3
```

For each pair, the campaign sets the gateway tier, launches SREGym, waits for
it to finish, and records a line in `results/campaigns/lite-sweep.jsonl`
with the arm, the model, the tier, and where the results are.

SREGym writes each run's results to its own directory under `results/`: a CSV
with the verdicts and the judge's reasoning, and a folder per attempt with the
agent's full transcript, token usage and the fault specification.

If the campaign is run again, pairs that already finished are skipped and
pairs that failed are retried.

The Codex half uses the same campaign name, so both halves land in one
manifest and one report. `clickstack/queue_codex.sh` waits for the running
campaign to finish, checks the OpenAI key and the model ids against the API,
and launches the Codex arms with the models that exist:

```
nohup bash clickstack/queue_codex.sh lite-sweep > ~/campaign-lite-sweep-codex.log 2>&1 &
```

`clickstack/codex_smoke.sh` checks the Codex arm without touching the
cluster. It installs the pinned Codex CLI inside the agent image, reaches the
MCP proxy from inside a container, and runs one Codex turn that calls a
ClickStack tool.

To see progress, including the pair in progress, its current problem and
attempt, and a finishing estimate from the attempts completed so far:

```
python3 clickstack/campaign_status.py lite-sweep
python3 clickstack/incomplete_attempts.py lite-sweep   # attempts that did not complete, and why
uv run python clickstack/summarize.py --campaign lite-sweep [--harness codex] [--problems]
uv run python clickstack/saturation.py [--harness claudecode] [--threshold 0.95 --min-cell 0.67]
```

Missing attempts are filled afterwards. `fill_gaps.py` lists every (arm,
model, problem) with fewer complete attempts than the campaign asked for and
runs the missing number under `lite-sweep-fill`; with `--wait` it starts
once nothing else is running on the cluster. It runs with
`SREGYM_MAX_DEPLOY_RETRIES=6`, so a problem whose fault injection check is
timing-dependent gets six tries instead of three:

```
uv run python clickstack/fill_gaps.py lite-sweep --dry-run
nohup uv run python clickstack/fill_gaps.py lite-sweep --wait > ~/campaign-lite-sweep-fill.log 2>&1 &
```

A cron job on the runner regenerates `results/report.html` at 17 minutes
past every hour.

**The fleet.** Eight clusters, each three nodes and a runner, built from the
same playbook and a disk image of the first runner. Every cluster writes to
its own ClickHouse database (`default`, `c2` to `c8`) on the one ClickHouse
Cloud service, with the collectors, the truncation hook and the SQL MCP server
pointed at it and the skills rebuilt with that database name. Since 21
November every cluster also has its own ClickStack sources (Logs, Traces and
Metrics over its database, created through the managed MCP server), and the
auth proxy on each runner filters `list_sources` to the cluster's own sources
and refuses any other source id, so any arm can run on any cluster.
`clickstack/fleet/` holds the inventory (`clusters.json`, with each cluster's
source ids), the per-cluster finishing script, the pair assignment
(`assign.py` for phase 1, `plan_phase2.py` for phase 2), the per-runner queue
(`run_queue.sh`, one campaign invocation per pair, then the gap fill for that
campaign), `chain_queue.sh` (starts a second queue when the process running
the first one exits, which is how phase 2 follows phase 1 on every runner
without a gap), the collector (`collect.sh`, which pulls every cluster's
results under `results/<cluster>/` and merges the manifests), `invalidate.py`
(retires a campaign's records for arms matching a pattern, with their files,
when a run turns out to be invalid) and a status script. On a runner the
queues are `~/fleet/queue` (phase 1), `~/fleet/queue2` (follow-on phase 1
pairs on the two ClickStack clusters) and `~/fleet/queue3` (phase 2), each
with its log `~/fleet-<queue>.log`.

Phase 2 is scheduled in three 18-problem chunks (`sregym-71-rest-a`, `-b`,
`-c`): every pair finishes chunk a before any pair starts chunk b, so the
report holds a complete 35-problem picture (Lite plus chunk a) about a third
of the way through, and within a chunk the arms that discriminate most come
first (the stock reference, then the telemetry-only tiers 3 and 4, SQL-only
tiers 3 and 4, stock telemetry without kubectl, tier 0, tiers 1 and 2, and
the kubectl-plus-telemetry arms last).

The SQL MCP server runs as the systemd unit `sregym-chsql-mcp` with
`clickstack/chsql_mcp_main.py` as its entry point, which adds the result caps
(500 rows, 10 MB, 60 seconds, error rather than truncation) to every tool
query as query settings. They were a settings profile on the database user
until 21 November; see the progress notes for why that broke every SQL call.
`clickstack/chsql_probe.sh` on a runner checks the server the way the agents
use it: a real query, an over-cap query that must fail, an attempt to lift the
cap from the query text that must be refused, and `list_tables`.
`clickstack/chsql_smoke.sh` checks the SQL arms without the cluster: the MCP
handshake from inside the agent image, then one Codex turn and one Claude
Code turn that each run a SQL query through the server. The older
`clickstack/queue_sql.sh` (a single-cluster SQL campaign queued behind the
Lite sweep) is superseded by the fleet queues.

To build the report:

```
uv run python clickstack/report.py --out results/report.html
```


## 8. How to read the report

The report is one HTML file. It needs no server. Every number on it is
computed in the browser from the attempts that pass the filters, so any cut of
the data can be read off directly. Every column heading, tile and filter
carries a small "i" icon; hovering or focusing it shows a short explanation of
what the number means and how it is computed, for example what pass@k is or
why TTD is reported both over all attempts and over correct ones.

A "hide saturated problems" switch removes, from every number on the page,
the problems that the reference arms solve essentially every time. The
reference is chosen beside the switch: the kubectl arms by default (stock and
every kubectl-plus-telemetry arm, the agents with cluster access), the stock
arm alone, the selected arms, or the telemetry-only arms. The definition has
two adjustable thresholds: the problem's overall pass rate over the selected
campaigns and models and the reference arms must be at least the first
(default 95 percent), and no single model × arm cell in the reference may be
below the second (default 67 percent, at most one failed attempt out of
three). A list under the filters names the problems that qualify, with their
rates, whether or not the switch is on, so nothing is hidden silently; the
"problems in view" tile says how many were hidden. Judging saturation on a
reference set rather than on whatever arms are ticked matters: with a
telemetry-only arm in view, almost no problem is solved by every cell, and
the earlier version of the switch hid nothing. `clickstack/saturation.py`
prints the same analysis as text.

**Filters**, at the top and kept in view while scrolling: campaign, model,
arm, harness, a problem set (SREGym-Lite, the 54 further problems, or all), a
problem checklist (tick problems by hand, or press "exclude saturated" to
untick the saturated list), a text search over problem, model and the
submitted diagnosis, and a "balanced problems only" switch that keeps only
the problems with a scored attempt in every selected model × arm cell, so
that arms are compared on the same problems when some attempts are missing.
A button exports the filtered attempts as CSV. A line under the filters
reports how many attempts are in view and how long the recomputation took
(tens of milliseconds); only the visible tab is rendered, and an attempt's
detail is built when its row is opened, so the page stays responsive with
thousands of attempts.

**Tiles**: diagnosis pass@1, pass@k with the number of cells, attempts, problems
in view (and how many the saturation switch hid), mean TTD for correct
diagnoses, mean tokens, MCP calls per attempt.

**Headline tables**, one by arm and one by model. Columns: attempts, pass@1,
pass@k, mitigation rate, mean TTD, TTD when correct, mean TTM, mean tokens,
calls per attempt, and that figure split by kind with each kind's share of all
calls in brackets: ClickStack MCP calls, ClickHouse SQL MCP calls, SREGym's
Prometheus/Loki/Jaeger MCP calls, kubectl calls and other shell commands (a
kind's column appears only when an arm in view has such calls; the shares add
up to 100 percent). "Other shell" is, in every arm, the curl that submits the
diagnosis (one to two per attempt), reading the skill or the run's files
(about two), live probes against a service, writing or running a small
script, and plain text processing; the Tool use tab breaks it down per arm.
Then the share of ClickStack calls that were raw SQL (ClickStack arms only),
the share of tool calls that returned an error, and mean cost. Under the
arm table, a line gives each tier's change against tier 0 in pass@1, tokens,
MCP calls and TTD.

**Defaults.** The page opens with the study's three models ticked
(claude-fable-5-2, claude-opus-5-5, gpt-6.1-sol; claude-fable-5-1 and
gpt-6.1-terra from the Lite sweep start unticked), every arm and campaign
ticked, and "hide saturated problems" on with the kubectl arms as reference.
`?sat=0` opens with the switch off.

**Matrix**: model against arm. Each cell is diagnosis pass@1 with the counts
underneath and, for kubectl arms, the mitigation count. Darker is better. A
small dot in the corner of each cell, and before each row of the headline and
tool-use tables, shows completeness: green when every planned attempt is
scored (the problems of the selected set or phase, minus hidden ones, times
three attempts), orange when some are scored or a pair is still running, red
when nothing has started. Hovering the dot gives the counts, so a 93% over
54 scored attempts of 162 planned reads differently from a finished cell.

The page is organised in nine tabs; the URL hash selects one, so a link can
point at a tab. A problem-set filter beside the problem list restricts every
number to a named set (SREGym-Lite, the 54 further problems of the 71-problem
set, or all), so the Lite results stay comparable with the Lite-only campaign
(`?set=sregym-lite` in the URL pre-selects it).

- **Overview.** The tiles, the headline tables and the model × arm matrix.

- **Tool use.** For each model, one row per arm: attempts, pass@1, mean calls
  per attempt split into MCP tools, kubectl invocations, live probes (kubectl
  exec, port-forward, debug pods, curl or dig from a pod) and fixes, the
  share of calls that errored, the share of attempts that touched each
  telemetry area (Kubernetes events, object snapshots, object changes,
  application logs, metrics, traces, node settings), the tools used most and
  how often the skill was read. The plus sign opens every tool and kubectl
  verb with its calls per passing and per failing attempt and its error rate,
  and a sentence comparing the arm with what the same model's stock arm read
  in its successful runs and naming the areas this arm never queried although
  its tier ships them.

- **Gaps vs stock.** For each model, every arm against the same model's stock
  arm (kubectl plus Prometheus, Loki and Jaeger) over the same filters: gap
  problems (stock passed at least half of its attempts, the arm fewer than
  half), gains (the reverse) and shared failures; the failure modes of the
  arm's rejected attempts, derived from the judge's checklist (no component
  named, wrong component, symptom not origin, wrong mechanism, wrong fault
  type, right cause but imprecise, gave up with or without failing tools);
  and for every gap problem what the stock agent read to solve it, what the
  arm's failing attempts read, and a rule-based reading of the gap: live
  probing (the stock runs leaned on exec, port-forward or debug pods, which
  no telemetry stream replaces), not captured at this tier (the stock agent
  read data that only a higher collector tier ships), captured but not
  queried (the arm never touched the area the stock agent used), or right
  data but wrong conclusion. A table under each arm ranks the telemetry the
  stock runs relied on across the gap problems, whether the arm's tier ships
  it and whether the arm's failing attempts queried it: the "what would have
  helped" list. The stock arm's own card lists the problems a telemetry arm
  of the same model solved that stock did not.

- **By model.** One section per model: pass rate per arm as a bar, then every
  problem with a mark per attempt and arm.

- **By arm / tier.** One section per arm: pass rate per model, the MCP tools
  the arm used and how often, then every problem with a mark per attempt and
  model. This is where the effect of the tiers shows.

- **By problem.** Every problem with its fault description, against every
  model and arm, one mark per attempt.

- **Attempts.** One row per attempt: verdicts, TTD, TTM, tokens, MCP and
  kubectl calls, SQL calls, errors, blocked requests, cost and status. The
  plus sign opens the attempt: the fault, the submitted diagnosis, the judge's
  verdict and checklist, the token breakdown, and every tool call in order
  with its arguments or SQL, how long it took, any error, and the agent's own
  words before the call. "Expand all" opens every row; adding `?expand=1` to
  the URL does the same for printing.

- **Queries.** A count of every MCP tool across the filtered attempts, then
  every SQL query the agents wrote, with the words the agent said before
  writing it and any error it returned.

- **SQL.** The same data in ClickHouse, queried from the page. The filters at
  the top are shown as a WHERE clause over the attempts table; presets (pass@1
  and pass@k by model and arm, failure modes, tool use, gap problems, areas
  queried, erroring SQL, saturation) insert it where they say `{where}`, and
  any query can be edited and run with the read-only user. Results come
  straight from the ClickHouse HTTP interface to the browser (the endpoint is
  embedded, the password is typed once and kept only in the browser if asked).
  The schema is listed under the editor. See "The results in ClickHouse" below.

- **Method & notes.** The definition of every metric and filter, the arms and
  tiers with the tier at which each telemetry area first ships, the failure
  modes, the table that maps kubectl reads to their telemetry equivalents (the
  basis of the gap readings), and analyst notes written per campaign in
  `clickstack/notes/<campaign>.md`.

Marks: a green tick is a correct diagnosis with the judge's score, a red cross
an incorrect one, a yellow mark an attempt that did not finish or was not
scored, and a grey dash no attempt. Hovering a mark shows the judge's reasoning.


## 9. Results

Campaign `lite-sweep`: 12 arms, 5 models, 17 problems, 3 attempts, judge
`claude-sonnet-5-5`, Claude Code 2.1.318 and Codex 0.166.0, agents at the
default reasoning effort. Started 12 November 2026 at 19:06 UTC on the
runner. The Claude Code half (18 pairs, 918 attempts) runs first at about 7
minutes per attempt including the fresh deployment, the Codex half (12 pairs,
612 attempts) is queued behind it, and the gap fill runs last.

All 30 pairs finished on 20 November at 09:13 UTC: 1,512 attempts run, 1,494
scored, 18 not deployed or unfinished. The gap fill for those 18 (almost all
the edge-proxy problem, section 6) was running when these tables were taken;
it can move a cell by one or two attempts. `results/report.html` carries the
live numbers.

### Diagnosis pass@1

Share of attempts whose diagnosis the judge accepted, with the counts.

| Arm | Opus 5.5 | Fable 5.1 | Fable 5.2 | gpt-6.1-sol | gpt-6.1-terra |
|---|---|---|---|---|---|
| stock | 96% (48/50) | 96% (49/51) | 100% (49/49) | 94% (48/51) | 90% (43/48) |
| kubectl + ClickStack | 96% (46/48) | 94% (46/49) | 100% (50/50) | 94% (48/51) | 88% (45/51) |
| ClickStack only, tier 0 | 49% (23/47) | 71% (34/48) | 82% (42/51) | 59% (30/51) | 49% (25/51) |
| ClickStack only, tier 1 | 75% (36/48) | 73% (35/48) | 92% (46/50) | 78% (40/51) | 73% (37/51) |
| ClickStack only, tier 2 | 77% (37/48) | 82% (41/50) | 94% (47/50) | 88% (44/50) | 80% (41/51) |
| ClickStack only, tier 3 | 79% (38/48) | 78% (39/50) | 98% (50/51) | 94% (48/51) | 84% (43/51) |

### Diagnosis pass@k

Problems with at least one accepted diagnosis in three attempts, out of 17
(16 where the edge-proxy problem has no attempt yet).

| Arm | Opus 5.5 | Fable 5.1 | Fable 5.2 | gpt-6.1-sol | gpt-6.1-terra |
|---|---|---|---|---|---|
| stock | 17/17 | 17/17 | 17/17 | 16/17 | 16/17 |
| kubectl + ClickStack | 17/17 | 16/17 | 17/17 | 16/17 | 17/17 |
| ClickStack only, tier 0 | 11/16 | 14/16 | 16/17 | 13/17 | 13/17 |
| ClickStack only, tier 1 | 13/16 | 13/16 | 16/17 | 14/17 | 15/17 |
| ClickStack only, tier 2 | 14/16 | 16/17 | 17/17 | 16/17 | 16/17 |
| ClickStack only, tier 3 | 14/16 | 15/17 | 17/17 | 16/17 | 16/17 |

### Mitigation, kubectl arms

| Arm | Opus 5.5 | Fable 5.1 | Fable 5.2 | gpt-6.1-sol | gpt-6.1-terra |
|---|---|---|---|---|---|
| stock | 48/50 | 48/51 | 45/49 | 47/51 | 45/48 |
| kubectl + ClickStack | 45/48 | 46/49 | 50/50 | 49/51 | 45/51 |

### Time to diagnosis when correct, minutes

| Arm | Opus 5.5 | Fable 5.1 | Fable 5.2 | gpt-6.1-sol | gpt-6.1-terra |
|---|---|---|---|---|---|
| stock | 0.8 | 1.6 | 1.3 | 0.8 | 0.7 |
| kubectl + ClickStack | 0.8 | 1.7 | 1.1 | 1.0 | 0.7 |
| ClickStack only, tier 0 | 1.4 | 4.8 | 2.5 | 1.2 | 1.0 |
| ClickStack only, tier 1 | 0.9 | 3.5 | 2.0 | 1.0 | 1.1 |
| ClickStack only, tier 2 | 1.0 | 3.3 | 1.6 | 1.4 | 0.9 |
| ClickStack only, tier 3 | 0.9 | 2.6 | 1.5 | 1.1 | 0.9 |

### Effort per attempt

Mean total tokens in thousands, and for Claude Code the harness's own cost
figure (Codex reports no cost).

| Arm | Opus 5.5 | Fable 5.1 | Fable 5.2 | gpt-6.1-sol | gpt-6.1-terra |
|---|---|---|---|---|---|
| stock | 330k, $0.20 | 483k, $0.88 | 508k, $0.37 | 367k | 355k |
| kubectl + ClickStack | 389k, $0.24 | 1,043k, $1.73 | 872k, $0.63 | 931k | 794k |
| ClickStack only, tier 0 | 1,092k, $0.71 | 1,436k, $3.35 | 1,136k, $1.11 | 744k | 673k |
| ClickStack only, tier 1 | 702k, $0.51 | 1,173k, $2.74 | 893k, $1.08 | 965k | 645k |
| ClickStack only, tier 2 | 603k, $0.48 | 1,056k, $2.60 | 790k, $1.32 | 631k | 608k |
| ClickStack only, tier 3 | 614k, $0.49 | 1,168k, $2.65 | 753k, $1.32 | 650k | 584k |

ClickStack and kubectl calls per attempt in the kubectl + ClickStack arm:
Opus 0.6 and 7.9, Fable 5.1 6.4 and 9.8, Fable 5.2 3.3 and 9.0, sol 10.9 and
9.7, terra 10.6 and 9.8. In the ClickStack-only arms the Claude models make 7
to 25 ClickStack calls per attempt and the OpenAI models 16 to 22. Between 3
and 7 percent of tool calls return an error in every arm and harness. The
skill was read in 97 to 100 percent of ClickStack-only attempts, in 70
percent of Claude Code's kubectl + ClickStack attempts, and always by Codex,
which loads it as standing instructions.

### By problem

Accepted diagnoses over attempts, kubectl arms (stock and kubectl +
ClickStack) against the four ClickStack-only arms, per harness.

| Problem | Claude Code, kubectl | Claude Code, ClickStack only | Codex, kubectl | Codex, ClickStack only |
|---|---|---|---|---|
| `unschedulable_incorrect_port_assignment` | 18/18 | 12/35 (34%) | 12/12 | 22/24 (92%) |
| `mutating_webhook_resource_limits_social_network` | 18/18 | 15/36 (42%) | 4/12 (33%) | 8/24 (33%) |
| `search_rate_retry_collapse_hotel_reservation` | 12/18 (67%) | 21/36 (58%) | 5/12 (42%) | 19/24 (79%) |
| `secret_rotation_stale_env_credentials_astronomy_shop` | 16/18 (89%) | 21/36 (58%) | 10/12 (83%) | 15/24 (62%) |
| `finalizer_deadlock_controller_hotel_reservation` | 18/18 | 23/36 (64%) | 12/12 | 17/24 (71%) |
| `env_variable_shadowing_astronomy_shop` | 18/18 | 29/36 (81%) | 12/12 | 20/24 (83%) |
| `network_policy_block` | 18/18 | 29/36 (81%) | 12/12 | 13/24 (54%) |
| `internal_traffic_policy_local_astronomy_shop` | 18/18 | 30/36 (83%) | 12/12 | 20/24 (83%) |
| `rolling_update_misconfigured_social_network` | 18/18 | 31/36 (86%) | 12/12 | 19/24 (79%) |
| `valkey_auth_disruption` | 18/18 | 33/36 (92%) | 12/12 | 1/24 (4%) |
| `cronjob_sidecar_blocks_completion_hotel_reservation` | 18/18 | 34/36 (94%) | 12/12 | 24/24 |
| `service_dns_resolution_failure_social_network` | 18/18 | 34/36 (94%) | 12/12 | 18/24 (75%) |
| `duplicate_pvc_mounts_social_network` | 18/18 | 35/36 (97%) | 12/12 | 22/24 (92%) |
| `service_wrong_pod_selection_hotel_reservation` | 17/18 (94%) | 35/36 (97%) | 12/12 | 22/24 (92%) |
| `edge_request_filter_cpu_saturation` | 9/9 | 14/14 | 9/9 | 21/23 (91%) |
| `kafka_poison_pill_hol_block` | 18/18 | 36/36 | 12/12 | 23/24 (96%) |
| `namespace_memory_limit` | 18/18 | 36/36 | 12/12 | 24/24 |

### Saturation

A problem is saturated when essentially every model and arm solves it, so it
cannot separate them. Over all 30 pairs, with the default definition (overall
pass rate of 95 percent or more and no model × arm cell below 67 percent), only
`namespace_memory_limit` is saturated: every other problem is failed by at
least one telemetry-only cell. Judged over the two kubectl arms alone, 13 of
the 17 problems are saturated at 100 percent for every model:
`namespace_memory_limit`, `kafka_poison_pill_hol_block`,
`cronjob_sidecar_blocks_completion_hotel_reservation`,
`edge_request_filter_cpu_saturation`, `duplicate_pvc_mounts_social_network`,
`service_dns_resolution_failure_social_network`,
`internal_traffic_policy_local_astronomy_shop`,
`rolling_update_misconfigured_social_network`,
`env_variable_shadowing_astronomy_shop`, `network_policy_block`,
`finalizer_deadlock_controller_hotel_reservation`,
`unschedulable_incorrect_port_assignment` and `valkey_auth_disruption`. The
four that still separate models with kubectl are
`service_wrong_pod_selection_hotel_reservation` (one stock miss),
`secret_rotation_stale_env_credentials_astronomy_shop`,
`search_rate_retry_collapse_hotel_reservation` and
`mutating_webhook_resource_limits_social_network`. Without kubectl the
picture inverts: 16 of 17 problems separate the tiers and models, and the
six easiest for telemetry (95 percent or more overall) are the ones listed
first in the table above. The report's "hide saturated problems" switch
applies this definition to whatever is selected.

### Why ClickStack-only tier 3 loses to kubectl: the 22 Claude Code failures

For the three Claude models, tier 3 failed 22 attempts where the stock arm
mostly passed (`clickstack/compare_arms.py --a only_t3 --b stock`). Reading
the diagnoses, judge verdicts and tool traces, the failures fall into four
kinds, and only one of them is missing data.

- **Stopped at the first sufficient cause (6, all on
  `unschedulable_incorrect_port_assignment`).** The problem has two faults: a
  nodeSelector that keeps the pods Pending and a wrong backend port in an
  environment variable. Every tier 3 agent found the nodeSelector in under
  two minutes, named the right Deployment, and submitted. The port was in the
  same Deployment manifest in ClickStack; nobody extracted the `env` field
  because the hypothesis was already complete. With kubectl, `describe` shows
  every field of the object at once, and all nine stock attempts passed.

- **Right mechanism, origin attributed to the wrong object (8).** Finalizer
  deadlock (2): the agents found the controller's 403s and blamed a missing
  RoleBinding; the actual change was to a ClusterRole, whose snapshot was in
  ClickStack at tier 2 and above but was never queried. Secret rotation (4):
  the agents found the password rotation but concluded the database still had
  the old password, when it was the running pod that had the stale one; the
  two hypotheses produce the same authentication-failed logs, and only a live
  connection test tells them apart. Mutating webhook (1): the agent saw the
  injected 16Mi memory limit on the pod and blamed the Deployment; the webhook
  configuration was in ClickStack but not consulted. Traffic policy (1): one
  near miss on scope.

- **Evidence that only a live probe reveals (2, Valkey).** The cart service
  logs a generic "wasn't able to connect to redis" and Valkey does not log
  rejected unauthenticated clients, so NOAUTH never appears in any telemetry.
  A stock agent runs `redis-cli PING` and sees it in one step.

- **Judge definition of the origin (5, `search_rate_retry_collapse`).** All
  five agents identified the rate service's queue and throttle and the retry
  storm from search; the checklist wants the search retry policy named as the
  sustaining origin. The stock arm has the same trouble with Fable 5.1 (one
  pass in three).

One "no fault found" (duplicate PVC mounts, Fable 5.1): the agent saw a second
Jaeger replica Pending and dismissed it because Jaeger is not an application
service. Thirteen of the 22 failures scored 0.55 or more, so most are near
misses, not blank failures. Tier 3 agents were also fast on these failures,
often under a minute: the failure mode is early commitment to one cause,
not exhaustion of the data.

### Observations

- With kubectl, every model is at or near the ceiling: 88 to 100 percent
  pass@1 and every problem solved at least once by nearly every model.
  Adding ClickStack to kubectl moves pass@1 by at most two points and leaves
  TTD unchanged. The Claude models mostly ignore ClickStack when kubectl is
  there (0.6 to 6.4 calls per attempt against 8 to 10 kubectl calls); the
  OpenAI models use both about equally.

- Telemetry alone, with no cluster access, diagnoses between half and four
  fifths of the problems at tier 0 depending on the model, and Kubernetes
  state in ClickStack lifts every model: by 26 points for Opus and 24 for
  terra from tier 0 to tier 1 alone, and by 16 to 35 points from tier 0 to
  tier 3 for every model. The step from tier 0 to tier 1, object snapshots
  and changes for the core kinds, is the largest; tiers 2 and 3 add a few
  points more. At tier 3, Fable 5.2 (98 percent) and sol (94 percent) match
  their own kubectl arms.

- Three attempts solve most of what one attempt misses: at tiers 2 and 3 the
  telemetry-only arms solve 14 to 17 of the 17 problems at least once for
  every model. The gap to kubectl is mostly consistency, not impossibility.

- The problems telemetry struggles with are harness-specific. Claude Code's
  telemetry-only arms get 34 percent on the unschedulable-port problem and
  42 percent on the mutating webhook; Codex's get 4 percent on the Valkey
  authentication fault, which Claude Code solves 92 percent of the time, and
  92 percent on the unschedulable-port problem. The webhook problem is also
  the one Codex fails with kubectl (33 percent). Which telemetry each
  harness's agents read first decides these.

- Speed and cost: Opus, sol and terra diagnose in about a minute in every
  arm. Fable 5.1 is the slowest and most expensive model (2.6 to 4.8 minutes
  and $2.6 to $3.4 per telemetry-only attempt); Fable 5.2 is twice as fast,
  2.5 times cheaper and more accurate than Fable 5.1 in every arm.

### Campaign `sql-sweep`

Superseded: the single-cluster SQL campaign never ran (a stray start on the
primary was stopped after a few attempts, recorded under campaign
`sql-sweep`). The SQL arms run in the fleet's `phase1` and `phase2`
campaigns instead.

### Campaigns `phase1` and `phase2`

Phase 1 runs the 36 pairs of the arms that had no Lite data (kubectl +
ClickStack at tiers 3 and 4, ClickStack only at tier 4, kubectl + ClickHouse
SQL at tiers 2 to 4, ClickHouse SQL only at tiers 0 to 4, stock telemetry
without kubectl) for claude-opus-5-5, claude-fable-5-2 and gpt-6.1-sol on the
17 Lite problems, three attempts each. Phase 2 runs all 54 pairs (every arm)
on the 54 further problems of the 71-problem set. Results are in the live
report; tables are added here when a phase completes.

### Progress notes

- 13 November, 00:26 UTC. First pair, Opus 5.5 with stock tools: 50 of 51
  attempts ran. Diagnosis 48 of 50, mitigation 48 of 50.
- 13 November, 13:00 UTC. Three pairs finished, the fourth running. The
  `edge_request_filter_cpu_saturation` attempts lost to the hook problem in
  section 6 (eight attempts across the first three pairs, plus one attempt
  where the agent exited without submitting) are queued for the gap fill.
- 15 November, 09:00 UTC. Ten pairs finished, the eleventh running, 507 of
  918 attempts complete at 7.3 minutes each; the Claude Code half should end
  around 17 November 11:00 UTC. The edge-proxy problem's injection check
  (section 6) has left that problem with attempts in only four of the
  eleven cells so far; the gap fill will retry them with six tries.
- 16 November, 10:45 UTC. Fourteen pairs finished, the fifteenth running,
  702 of 918 attempts complete at 7.5 minutes each; the Claude Code half
  should end around 17 November 14:00 UTC. Every Opus and Fable 5.1 pair is
  done; Fable 5.2 has its two kubectl arms done at 100 percent each.
- 17 November, 07:22 UTC. The Claude Code half finished: 18 pairs, 886 of
  918 attempts complete, 32 attempts for the gap fill (31 of them the
  edge-proxy problem's injection check, one an agent that exited without
  submitting). The Codex half launched at 10:22 UTC; a stray shell whose
  command line happened to contain the campaign script's name had kept the
  queue's process check waiting for three hours, and the checks now match
  only a real campaign invocation.
- 18 November, 13:00 UTC. The managed ClickStack MCP server changed its tool
  list overnight: `clickstack_trace_top_time_consuming_operations` became
  `clickstack_trace_top_time_consuming_ops`, and a `clickstack_submit_feedback`
  tool appeared. The clients' preflight required every tool on their read-only
  list, so from 02:38 UTC every ClickStack arm, Claude Code or Codex, exited at
  launch: eight Codex pairs and all the ClickStack-arm gap fills. The four
  Codex pairs that had already started or use no MCP completed normally
  (`gpt-6.1-sol` stock, kubectl + ClickStack and tier 0; `gpt-6.1-terra`
  stock). The preflight now insists only on the nine core tools the skills
  use, reports other differences, and the proxy refuses the feedback tool
  like the other writing tools. The eight pairs were relaunched at 13:00 UTC
  and the gap fill re-armed behind them. The first relaunched pair was lost
  to a corrupt Docker build cache on the runner (the image rebuild that
  follows a client change failed once); the cache was cleared and a second
  queue pass is armed to pick that pair up after the others.
- 19 November, 12:30 UTC. Nine of the twelve Codex pairs are done; the
  terra tier 1 pair is running, terra tiers 2 and 3 and the sol tier 1
  re-run follow, then the gap fill. Expected to finish during 20 November.
- 20 November, 09:13 UTC. All 30 pairs finished. The gap fill started: 31
  attempts across 16 cells, 29 of them the edge-proxy problem.
- 20 November, 21:55 UTC. Phase 1 launched on the fleet of eight clusters
  (36 pairs, 1,836 attempts); the stock-telemetry-without-kubectl arm had
  passed a one-problem shakedown under both harnesses first.
- 21 November, 15:40 UTC. First results collected from the fleet showed every
  ClickHouse-SQL-only pair at 0 percent with 57 to 66 percent tool errors,
  while the kubectl + SQL pairs passed 96 to 100 percent with 30 to 39 percent
  errors. Every SQL tool call on every cluster had been failing since the
  launch with ClickHouse error 396: the read-only database user's settings
  profile capped results at 500 rows, and the clickhouse-connect driver
  (1.13) reads the 1,694-row `system.settings` table when it opens a
  connection, so no connection could be made (4,666 failed connection
  setups in the server's query log). The smoke tests on 20 November had
  passed only because the primary's server had cached a connection before
  the cap was attached. The kubectl + SQL agents fell back to kubectl, so
  those runs measured the stock arm.
- 21 November, 16:00 UTC. Fix: the caps are now query settings added by the
  server's entry point (`clickstack/chsql_mcp_main.py`) next to `readonly=1`,
  which the agent cannot lift from the query text; the profile keeps only the
  time and byte limits. Verified on all eight runners (a real query succeeds,
  a 1,000-row query fails with 396, `SETTINGS max_result_rows = 0` is refused
  with 164). The 14 finished and 6 running SQL pairs were retired
  (`results/campaigns/phase1.invalid-sql.bak`, files under `results/invalid/`)
  and every SQL pair re-queued, rebalanced over the six SQL clusters with
  three more pairs chained behind the two ClickStack clusters' queues. The
  server's query log shows the relaunched agents' queries succeeding. Phase 1
  should now finish around 22 November 15:00 UTC, about 22 hours later than
  planned.
- 22 November, 11:00 UTC. Reading the no-kubectl telemetry arm's Codex traces:
  Codex only speaks streamable HTTP to a remote MCP server, SREGym's mounts
  speak HTTP+SSE, so the three servers failed to attach (HTTP 405) in every
  Codex attempt and the agent wrote its own SSE client in Python and drove
  `get_metrics`, `get_logs` and `get_traces` through the shell, 21 calls per
  attempt on average. The data it reached is the same, so the 58 percent
  stands as "Codex with the stock telemetry", but the tool accounting had
  counted those calls as shell commands; the report now counts them as MCP
  calls and flags them. The Codex client now attaches each mount as a stdio
  server through `clients/codex_obs/sse_stdio_bridge.py` (standard library
  only; verified against a live mount: initialize, tools/list, tools/call).
  Phase 2 Codex runs of this arm use it; whether to re-run the Lite pair with
  it is an open decision. The same check showed that SREGym's stock clients
  attach no MCP servers at all, so in the stock arm Prometheus, Loki and
  Jaeger are reachable only through kubectl; the report's labels now say so.
  Also found: the "ClickStack calls" column counted every MCP tool (it is now
  "MCP calls"), the saturation switch judged on every selected arm and so hid
  nothing when telemetry-only arms were in view (it now judges on a reference
  arm set, kubectl arms by default), and the page rendered every tab and
  every attempt detail eagerly (now lazy; a filter change recomputes in tens
  of milliseconds).
- 21 November, 16:30 UTC. The report gained the Tool use, Gaps vs stock and
  Method tabs, the problem-set filter and per-campaign notes; the Lite
  archive was regenerated from the same data with the new tabs. ClickStack
  sources were created for the six SQL clusters and their proxies restricted
  to them, so phase 2 can spread every arm over all eight clusters. Phase 2
  (162 chunk-pairs, about 115 hours of work per cluster) is chained behind
  phase 1 on every runner; on eight clusters it ends around 26 to 27
  November, not Monday 24 November as hoped. Options to shorten it: more
  clusters, one attempt instead of three for the kubectl-plus-telemetry arms
  (near-saturated on Lite), or dropping tiers 1 and 2 of the telemetry-only
  arms.

### Shakedown of the Codex arm, 13 November 2026

Run between the first and second pairs of the campaign, with the campaign
process paused at the pair boundary so nothing was lost
(`clickstack/run_between_pairs.sh`). One attempt each on
`service_wrong_pod_selection_hotel_reservation`, kubectl + ClickStack arm,
judge `claude-sonnet-5-5`.

| | Codex, `gpt-6.1-sol` | Claude Code, `claude-opus-5-5` |
|---|---|---|
| Diagnosis | pass (1.00) | pass (1.00) |
| Mitigation | pass | pass |
| TTD | 48 s | 46 s |
| TTM | 56 s | 58 s |
| ClickStack calls | 9 | 0 |
| kubectl calls | 6 | 9 |
| Other shell commands | 4 | 2 |
| Tool errors | 2 | 1 |
| Tokens (total, mostly cached input) | 626,489 | 376,222 |
| Skill read | yes, as `AGENTS.md` | no |
| Requests blocked by the sandbox | 16, all to chatgpt.com | 4, all to datadoghq.com |

Both diagnoses name the frontend Service selector change and the search pod
polluting the endpoints. Codex read the skill, listed the ClickStack sources,
described two of them and then mixed ClickStack and kubectl; Opus solved the
problem with kubectl alone and never called ClickStack. The blocked requests are the two harnesses' own telemetry endpoints,
not anything the agents needed. The Codex transcript, token usage and fault
specification parse into the report in the same form as Claude Code's.

### Report file

`results/report.html` on the primary runner, regenerated hourly from every
cluster's collected results (`clickstack/fleet/collect.sh` then
`clickstack/report.py`). A copy is at `results-report.html` in this
repository checkout. The Lite sweep is archived as
`results/archive/report-lite-sweep-2026-11-20.html` (copy:
`results-report-lite-sweep-2026-11-20.html`), regenerated on 21 November from
the unchanged Lite data with the tool-use and gap tabs
(`report.py --campaign lite-sweep lite-sweep-fill`).

Per-attempt artifacts are under `results/<timestamp>/<arm>/<problem>/run_<n>/`
(for the other clusters, `results/<cluster>/<timestamp>/...` on the primary):
the agent transcript (`claude-code.txt` or `codex.txt`), the fault
specification, token usage, and the results CSV with the judge's full
reasoning. The campaign manifests are `results/campaigns/<campaign>.jsonl`;
retired records are kept beside them as `<campaign>.invalid-<tag>.bak`.


### The web app

`clickstack/webapp/` is a Next.js app that serves the same report from the
ClickHouse results database, deployed on Vercel in the personal (hobby) scope
`daleclickhousecs-projects` as project `sregym-clickstack-report`. The page's
filters, tabs, tables and gap analysis are the shared core
`clickstack/webapp/lib/report-core.js` (with `report.css`), which the static
single-file page embeds too, so both have the same controls and defaults.

Every ClickHouse access is server-side: the API routes read the endpoint,
user, password, database and table names from the project's environment
variables (`CLICKHOUSE_URL`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`,
`CLICKHOUSE_DATABASE`, `RESULTS_ATTEMPTS_TABLE`, `RESULTS_TOOL_CALLS_TABLE`,
`RESULTS_PROBLEM_SETS_TABLE`, `RESULTS_SCORED_VIEW`; see `.env.example`),
stored in Vercel as secrets; the browser only ever talks to the app. Access
is by a shared password only: `middleware.ts` sends browsers to `/login`, a
one-field form that sets a signed cookie for 30 days (the cookie is derived
from `APP_PASSWORD`, a project environment variable, so rotating the password
signs everyone out); tools may send the password as HTTP Basic auth with any
username. The URL can be given to colleagues without a Vercel account; Vercel
Authentication is off. Routes:
`/api/attempts` (every attempt without the long texts, about 8 MB for 3,800
attempts), `/api/attempt?id=` (one attempt's submission, judge reasoning and
tool calls, fetched when a row is opened), `/api/queries` (the agents' SQL for
the attempts in view), `/api/sql` (the SQL tab: one SELECT-shaped statement,
run with the read-only user, `readonly=1`, 2,000 rows, 60 seconds) and
`/api/config` (arm vocabulary, problem sets, table schema).

Local run: `cp .env.example .env.local`, fill it in, `npm install`,
`npm run dev`. Deploy: `vercel --prod --scope daleclickhousecs-projects` from
`clickstack/webapp`. Code changes go through pull requests on the fork.

### The results in ClickHouse

`clickstack/load_results.py` loads the same attempts the report is built from
into the ClickHouse Cloud service, database `sregym_results` (the hourly job
on the primary runs it after collecting every cluster's results). Tables are
ReplacingMergeTree keyed by the attempt's run directory, so a reload replaces
rows; query them with `FINAL` or use the views.

- `attempts`: one row per attempt. Identity (campaign, cluster, model, harness,
  agent, family, tier, kubectl, problem, attempt, run directory, run start),
  verdicts (diagnosis, composite, mitigation, failure mode), times (TTD, TTM,
  agent wall time, turns), tokens and cost, call counts (all, MCP, kubectl,
  other shell, SQL, errors, live probes, fixes, calls made through an
  agent-written client), maps of tools, tool errors, kubectl verb+kind
  tokens and telemetry areas with their counts, the judge's checklist answers
  and dimension scores, run status fields, the fault text, the submitted
  diagnosis and the judge's reasoning.
- `tool_calls`: one row per tool call, in order: kind (mcp, kubectl, shell,
  skill, other), tool, telemetry area, arguments or SQL, the kubectl tokens,
  probe and scripted flags, error and error text, duration, and the agent's
  words before the call.
- `problem_sets`: set name and problem, for joins (`sregym-lite`,
  `sregym-71-rest`, the phase 2 chunks).
- Views: `attempts_scored` (finished and scored attempts, FINAL applied),
  `pass_rates` (per campaign, model, harness and arm), `cells` (per model, arm
  and problem, with whether any attempt passed, for pass@k).

The read-only user the SQL arms use has SELECT on the database, so the
report's SQL tab, the ClickHouse SQL console or any client can query it.
Example, pass@1 by model and arm on the Lite problems:

```
SELECT model, family, count() AS attempts, round(100 * countIf(diagnosis) / count()) AS pass1
FROM sregym_results.attempts_scored
WHERE problem IN (SELECT problem FROM sregym_results.problem_sets WHERE set_name = 'sregym-lite')
GROUP BY model, family ORDER BY model, family
```

## 10. Limits of what this shows

- Seventeen problems with three attempts per cell separate the arms for a
  model, but one cell is still three attempts, so a difference of one attempt
  is noise. pass@k and the per-problem view show where a result rests on a
  single attempt.

- Not every cell has every attempt. Fault injection can fail on the SREGym
  side (section 6), and an agent can exit without submitting. The report's
  "balanced problems only" switch restricts a comparison to problems every
  selected cell has data for; the per-attempt counts are shown everywhere.

- For agents with kubectl, SREGym-Lite is close to saturated: 13 of 17
  problems are solved in every attempt by every model. Differences between
  kubectl arms rest on four problems and on TTD and tokens. The telemetry-only
  arms are not saturated, which is where this suite still discriminates.

- A model is only comparable with models in the same harness. Claude Code and
  Codex have different tool loops, prompts and defaults, so a gap between a
  Claude model and an OpenAI model mixes model and harness.

- The judge is a Claude model for every run. It grades against a fixed
  checklist, but a reader may still want to spot-check its verdicts on the
  Codex runs.

- Some ClickStack tool calls return errors: invalid arguments, mostly to the
  service-health and table tools, and results too large for the harness.
  These are counted and shown per attempt. They are part of what the agent
  has to cope with, not removed from the data.

- The ClickStack-only arms cannot mitigate, so they are not comparable on
  SREGym's leaderboard, which ranks by diagnosis and mitigation together.

- In the stock arm the agent reaches Prometheus and Loki through kubectl, so
  it still has telemetry. The comparison is about which interface and which
  data lead to the right answer, not telemetry against no telemetry.

- The judge is a language model. Its verdict follows a checklist against the
  known root cause, and its reasoning is kept with every result so it can be
  reviewed.
