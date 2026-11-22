## In this environment

The ClickHouse MCP tool `mcp__clickhouse__run_query` is your only interface to the cluster. There is no
`kubectl`, no kubeconfig and no network path to the Kubernetes API. Bash is available for everything
else, such as HTTP calls to the conductor API.

- Read the `clickhouse-sql` skill before your first query. It explains which tables and columns exist,
  how Kubernetes state is stored, and the SQL patterns that work.
- Work in SQL: aggregate first (counts, error rates, latest state per object), then read individual rows.
  `mcp__clickhouse__list_tables` on database `default` lists the tables if you need to check.
- The database user is read-only, results are capped at 500 rows and 60 seconds per query, and there
  are no saved searches, dashboards or alert tools: write plain SELECT statements with a LIMIT.
