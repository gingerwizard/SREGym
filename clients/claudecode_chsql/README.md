# Claude Code + ClickHouse SQL (raw SQL over the same telemetry)

The SQL-only counterpart of [`clients/claudecode_clickstack`](../claudecode_clickstack). Same container,
same tiers of collected Kubernetes state, same skill content rewritten for SQL, but the only interface to
the telemetry is the open-source ClickHouse MCP server ([`mcp-clickhouse`](https://pypi.org/project/mcp-clickhouse/)
0.7.0, pinned, run as a service on the runner against the read-only database user) with `run_query`,
`list_databases` and `list_tables`. No ClickStack builder tools. Comparing these arms with the ClickStack
arms measures what ClickStack's purpose-built tools add over SQL on the same data.

| Agent | Cluster access | Skill tier | Purpose |
|---|---|---|---|
| `claudecode_chsql` | kubectl as in the stock client | 2 | ClickHouse SQL added to the stock toolset |
| `claudecode_chsql_only_t0` | none (`cluster_access: false`) | 0 | logs, events, metrics, traces only; `--stages diagnosis` |
| `claudecode_chsql_only_t1` | none | 1 | plus snapshots and changes of the core Kubernetes kinds |
| `claudecode_chsql_only_t2` | none | 2 | plus every kind, control-plane and machine logs |
| `claudecode_chsql_only_t3` | none | 3 | plus node settings |

Differences from the ClickStack client: MCP server `clickhouse` at `CHSQL_MCP_URL` (default
`http://host.docker.internal:9400/mcp`), allowlist `mcp__clickhouse__run_query`, `list_databases`,
`list_tables`; skill `clickhouse-sql` (built by `skills/build_skills.py` from `skills/sections/`, which
are the ClickStack sections with the macros and tool guidance replaced by SQL); routing texts in SQL
terms; run record `chsql-run.json`. `CLICKSTACK_TIER` keeps its name so the campaign runner sets the
collector tier the same way. Every query is capped at 500 result rows, 10 MB and 60 seconds (query settings added by
`clickstack/chsql_mcp_main.py`, the unit's entry point; a SETTINGS clause cannot lift them in readonly mode), and the
server refuses writes.

Server on the runner: systemd unit `sregym-chsql-mcp` (ExecStart `clickstack/chsql_mcp_main.py` in the pinned
mcp-clickhouse 0.7.0 tool venv), environment in `~/.config/sregym/chsql-mcp.env`,
bound to the Docker bridge address `172.17.0.1:9400`, authentication disabled on that local-only address.
Check with `bash clickstack/chsql_smoke.sh`.
