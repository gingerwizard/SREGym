## In this environment

Besides `kubectl`, the ClickHouse MCP tool `mcp__clickhouse__run_query` queries the database that holds
this cluster's logs, metrics, traces and Kubernetes state with history. `mcp__clickhouse__list_tables`
lists the tables.

- Read the `clickhouse-sql` skill before your first query. It explains which tables and columns exist,
  how Kubernetes state is stored, and the SQL patterns that work.
- Prefer the database for anything historical or cross-cutting: what changed and when, when errors
  started, which pods restarted, how latency moved. Use `kubectl` to read live state when needed and to
  apply changes.
- The database user is read-only, results are capped at 500 rows and 60 seconds per query, and there
  are no saved searches, dashboards or alert tools: write plain SELECT statements with a LIMIT.
