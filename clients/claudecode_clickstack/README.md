# Claude Code + ClickStack MCP

SREGym client for Claude Code with the ClickStack MCP server as its observability interface. It is a
thin layer over [`clients/claudecode`](../claudecode), in the same way as
[`clients/claudecode_radar`](../claudecode_radar): the stock driver is reused (conductor handshake,
submission, results) and the agent class swaps in a Claude Code CLI started with the ClickStack MCP
server attached and a tiered skill installed.

Two families of registrations in `agents.yaml`:

| Agent | Cluster access | Skill tier | Purpose |
|---|---|---|---|
| `claudecode_clickstack` | kubectl as in the stock client | 2 | ClickStack added to the stock toolset |
| `claudecode_clickstack_only_t0` | none (`cluster_access: false`, kubectl removed) | 0 | logs, events, metrics, traces only; run with `--stages diagnosis` |
| `claudecode_clickstack_only_t1` | none | 1 | plus snapshots and changes of the core Kubernetes kinds |
| `claudecode_clickstack_only_t2` | none | 2 | plus every kind, control-plane and machine logs |
| `claudecode_clickstack_only_t3` | none | 3 | plus node settings |

## Differences from `claudecode`

| | `claudecode` | `claudecode_clickstack*` |
|---|---|---|
| MCP servers | none | ClickStack via `--mcp-config` + `--strict-mcp-config`, URL from `CLICKSTACK_MCP_URL` |
| Allowed tools | stock list | stock list + the read-only `mcp__clickstack__*` tools (no save, patch, delete or backtest) + `ToolSearch`, `Monitor`, `Task*`, MCP resource readers |
| System prompt | CLI default | CLI default + `routing_kubectl.md` or `routing_no_kubectl.md` |
| Skills | none | `skills/tier<N>/clickstack-telemetry` copied into the CLI config dir and the project dir before start |
| Task instruction | SREGym's | SREGym's plus one line pointing at the ClickStack tools and the skill; the kubectl line is replaced when there is no cluster access; a diagnosis-only run is told to stop after the diagnosis |
| Permission mode | CLI default | `--permission-mode dontAsk` |
| Preflight | model call | MCP handshake with the proxy (initialize, tools/list, all allowed tools present), then the model call |

The skill and routing texts name attribute conventions and query patterns only; a test
(`tests/clients/test_claudecode_clickstack.py`) fails if they mention the benchmark, an application,
a problem or grading.

## Architecture

```
agent container                              runner host                         ClickHouse Cloud
  claude ──MCP/HTTP──▶ host.docker.internal:9300 ──▶ mcp_auth_proxy.py ──Bearer──▶ mcp.clickhouse.cloud/clickstack
```

The managed ClickStack MCP accepts OAuth only, which a container running `claude -p` cannot complete.
`mcp_auth_proxy.py` runs on the runner (systemd unit `sregym-mcp-proxy`, installed by
`install_proxy.sh`), bound to the Docker bridge address only. It reuses the token Claude Code stored
when `claude mcp login --no-browser clickstack` was completed once on the runner, refreshes it with the
OAuth refresh grant, writes rotated tokens back so the CLI stays valid, and adds the `x-service-id`
header. Containers never hold cloud credentials. `mcp_smoke.py --url <endpoint>` performs the same
handshake the preflight does.

## Skills

`skills/sections/*.md` are composed by `skills/build_skills.py` into `skills/tier<N>/clickstack-telemetry/SKILL.md`;
tier N contains sections 00 to N0. Edit the sections, run the builder, commit both.

| Tier | Adds |
|---|---|
| 0 | sources and tables, tool selection, container logs, Kubernetes Events, node syslog, metric families, traces |
| 1 | `k8s-objects` snapshots and `k8s-changes` history for workloads, Services, endpoints, volumes, Nodes |
| 2 | ConfigMaps, Secret metadata, policies, RBAC, quotas, webhooks, Calico objects; control-plane and cluster-component logs |
| 3 | `node-state` records: sysctls, conntrack, file handles, clock offset, kubelet configuration |

The collectors that produce this data live in [`clickstack/`](../../clickstack/README.md).

## Running

```bash
# one-time on the runner: `claude mcp login --no-browser clickstack`, then `sudo bash clients/claudecode_clickstack/install_proxy.sh`
uv run main.py --suite sregym-lite --agent claudecode_clickstack \
  --model anthropic/claude-opus-5-5 --reasoning-effort medium \
  --allow-agent-endpoint http://host.docker.internal:9300 --force-build

uv run main.py --suite sregym-lite --agent claudecode_clickstack_only_t2 --stages diagnosis \
  --model anthropic/claude-opus-5-5 --reasoning-effort medium \
  --allow-agent-endpoint http://host.docker.internal:9300
```

`--force-build` is needed on the first run and after any change under `clients/`. Without it SREGym runs the released
upstream agent image, which does not contain this client, so also set `SREGYM_AGENT_IMAGE=sregym-agent-base:latest`
(the tag `--force-build` produces) in the environment of every run; `~/.config/sregym/sregym.env` on the runner does this. Credentials are
the stock client's (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`). Set `SREGYM_PRE_DEPLOY_HOOK`,
`SREGYM_TELEMETRY_NAMESPACE` and `SREGYM_TELEMETRY_EGRESS` as in `~/.config/sregym/sregym.env` on the runner.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CLICKSTACK_MCP_URL` | `http://host.docker.internal:9300/mcp` | the proxy as the container sees it |
| `CLICKSTACK_TIER` | `2` | which `clickstack-telemetry` skill to install (0 to 3) |
| `CLICKSTACK_EXPECT_KUBECTL` | unset | `true` or `false`; a mismatch with the container's actual cluster access fails the run |

## Artifacts

The stock client's artifacts plus `clickstack-mcp-config.json` and `clickstack-run.json` (tier, kubectl
availability, endpoint) in the logs directory. ClickStack tool calls and responses are in the transcript.
