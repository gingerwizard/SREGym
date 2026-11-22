# Phase 1 (from 20 November 2026): all arms on SREGym-Lite with three models

Phase 1 runs 36 model × arm pairs (claude-opus-5-5 and claude-fable-5-2 under Claude Code, gpt-6.1-sol under Codex) on the 17 Lite problems, three attempts each, across a fleet of eight clusters. Arms: kubectl + ClickStack at tiers 3 and 4, ClickStack only at tier 4, kubectl + ClickHouse SQL at tiers 2, 3 and 4, ClickHouse SQL only at tiers 0 to 4, and SREGym's own telemetry (Prometheus, Loki, Jaeger) without kubectl. Earlier Lite results for the other arms are in the lite-sweep campaign and compare directly (same problems, same judge).

## Incident: the SQL arms ran with a broken server for 18 hours
- From the phase 1 launch (20 November 21:56 UTC) until 21 November 16:00 UTC every ClickHouse SQL tool call on every cluster failed with ClickHouse error 396 (result limit exceeded). The read-only database user's settings profile capped results at 500 rows; the clickhouse-connect driver reads the 1,694-row system.settings table when it opens a connection, so no connection could be established. The earlier smoke tests passed only because the primary's server had cached a connection before the cap was added.
- The 14 finished and 6 running SQL-arm pairs from that window were retired (their records are kept in results/campaigns/phase1.invalid-sql.bak, their files under results/invalid/) and every SQL pair was re-queued. In the retired kubectl + SQL runs the agents fell back to kubectl, so those runs measured the stock arm, not the SQL arm.
- Fix: the caps are now query settings added by the server entry point (clickstack/chsql_mcp_main.py) next to readonly=1; the agent cannot lift them from the query text. Behaviour for the agent is unchanged: 500 rows, 10 MB, 60 seconds, an error rather than silent truncation.

## Validation before launch
- The telemetry-without-kubectl arm was checked on the primary with both harnesses (one problem each, both diagnoses accepted) after a first launch failed because SREGym's MCP mounts are port-forwarded only while a problem runs; the client now waits for them at run start.
