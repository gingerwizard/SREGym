## In this environment

ClickStack's MCP tools (`mcp__clickstack__*`) are your only interface to the cluster. There is no
`kubectl`, no kubeconfig and no network path to the Kubernetes API. Bash is available for everything
else, such as HTTP calls to the conductor API.

- Read the `clickstack-telemetry` skill before your first query. It explains what this environment
  stores in ClickStack and the query patterns that work.
- Use the builder tools first: `clickstack_service_health`, `clickstack_search`, `clickstack_table`,
  `clickstack_timeseries`, `clickstack_event_patterns`, `clickstack_trace_waterfall`. Use
  `clickstack_sql` for joins and for extracting fields from stored object manifests.
- Only read tools are available. Do not try to save or delete dashboards, alerts, sources or webhooks.
