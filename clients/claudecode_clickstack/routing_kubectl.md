## In this environment

Besides `kubectl`, ClickStack's MCP tools (`mcp__clickstack__*`) hold this cluster's logs, metrics,
traces and Kubernetes state with history.

- Read the `clickstack-telemetry` skill before your first query. It explains what this environment
  stores in ClickStack and the query patterns that work.
- Prefer ClickStack for anything historical or cross-cutting: what changed and when, when errors
  started, which pods restarted, how latency moved. Use `kubectl` to read live state when needed and to
  apply changes.
- Only ClickStack read tools are available. Do not try to save or delete dashboards, alerts, sources or
  webhooks.
