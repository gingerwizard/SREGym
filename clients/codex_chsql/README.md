# Codex + ClickHouse SQL

The Codex counterpart of [`clients/claudecode_chsql`](../claudecode_chsql): SREGym's stock Codex client with
the ClickHouse MCP server (`run_query`, `list_databases`, `list_tables`) attached through
`-c mcp_servers.clickhouse.url=...`, and `AGENTS.md` carrying the SQL routing note plus the tier's
`clickhouse-sql` skill. Registrations `codex_chsql` (kubectl plus SQL, tier 2) and `codex_chsql_only_t0`
to `_t3` (no cluster access, diagnosis only), mirroring `codex_clickstack*`.
