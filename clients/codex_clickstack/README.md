# Codex + ClickStack MCP

The OpenAI-model counterpart of [`clients/claudecode_clickstack`](../claudecode_clickstack): SREGym's
stock Codex client with the ClickStack MCP server attached through the host-side auth proxy, and the
same tier skills, one registration per Claude Code arm:

| Agent | Cluster access | Skill tier | Purpose |
|---|---|---|---|
| `codex_clickstack` | kubectl as in the stock client | 2 | ClickStack added to the stock toolset |
| `codex_clickstack_only_t0` | none (`cluster_access: false`) | 0 | logs, events, metrics, traces only; `--stages diagnosis` |
| `codex_clickstack_only_t1` | none | 1 | plus snapshots and changes of the core Kubernetes kinds |
| `codex_clickstack_only_t2` | none | 2 | plus every kind, control-plane and machine logs |
| `codex_clickstack_only_t3` | none | 3 | plus node settings |

The stock `codex` registration is the baseline the first row is compared with.

Differences from the Claude Code variant, all forced by the harness:

| | Claude Code | Codex |
|---|---|---|
| MCP server | `--mcp-config` file | `-c mcp_servers.clickstack.url=...` override |
| Skill delivery | installed as a skill the agent invokes | written into `AGENTS.md` in the Codex home, which Codex reads as standing instructions |
| Read-only guarantee | CLI allowlist plus the proxy | the proxy only (Codex has no per-tool allowlist) |
| Credentials | `ANTHROPIC_API_KEY` | `OPENAI_API_KEY`, as the stock Codex client |

Everything else (environment variables, routing texts, preflight, artifacts) matches the Claude variant.
Results for OpenAI and Anthropic models therefore compare model plus harness, not model alone.

## Running

```
# cluster-free check of the whole path (CLI install in the agent image, MCP proxy, one Codex turn)
bash clickstack/codex_smoke.sh gpt-6.1-sol

# one problem, one attempt
uv run main.py --problem service_wrong_pod_selection_hotel_reservation --agent codex_clickstack \
    --model gpt-6.1-sol --judge-model anthropic/claude-sonnet-5-5 --allow-agent-endpoint http://host.docker.internal:9300

# all six Codex arms on the Lite suite, queued behind a running campaign
nohup bash clickstack/queue_codex.sh lite-sweep > ~/campaign-lite-sweep-codex.log 2>&1 &
```

Model ids are passed bare (`gpt-6.1-sol`), not with a provider prefix. The campaign runner's `model_arg()`
does this automatically. Artifacts per attempt: `codex.txt` (the CLI's JSON event stream), `AGENTS.md`,
`codex_results_*.json` with `usage_metrics`, `trajectory.json`, and SREGym's `fault-spec.json`.
