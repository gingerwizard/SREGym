#!/usr/bin/env python3
"""Entry point for the pinned mcp-clickhouse 0.7.0 server behind the ClickHouse-SQL arms.

Puts the per-query caps (500 result rows, 10 MB, 60 s, overflow = throw) on every tool query as query
settings, next to the readonly=1 the server already sends. They used to live on the database user's
settings profile, which broke the server: clickhouse-connect 1.13 opens every connection with
`SELECT name, value, readonly FROM system.settings` (1,694 rows on ClickHouse 26.6), the profile's
500-row cap threw on that query, and every SQL tool call failed with TOO_MANY_ROWS_OR_BYTES (seen on
all fleet runners 20-21 November 2026). Query-level settings leave the connection setup alone, and the
agent cannot lift them: a SETTINGS clause in the query text is refused in readonly mode (READONLY, 164).

Run as the systemd unit `sregym-chsql-mcp`:
  ExecStart=<uv tool venv>/bin/python /home/sregym/SREGym/clickstack/chsql_mcp_main.py
with the same environment file as the stock `mcp-clickhouse` entry point. Override the caps with
SREGYM_SQL_MAX_RESULT_ROWS, SREGYM_SQL_MAX_RESULT_BYTES and SREGYM_SQL_MAX_EXECUTION_TIME.
"""
import logging
import os

import mcp_clickhouse.clients as clients
from mcp_clickhouse.main import main

QUERY_SETTINGS = {
    "max_result_rows": os.environ.get("SREGYM_SQL_MAX_RESULT_ROWS", "500"),
    "max_result_bytes": os.environ.get("SREGYM_SQL_MAX_RESULT_BYTES", "10000000"),
    "result_overflow_mode": "throw",
    "max_execution_time": os.environ.get("SREGYM_SQL_MAX_EXECUTION_TIME", "60"),
}

_upstream_build_query_settings = clients.build_query_settings


def build_query_settings(client) -> dict:
    settings = _upstream_build_query_settings(client)
    settings.update(QUERY_SETTINGS)
    return settings


clients.build_query_settings = build_query_settings

if __name__ == "__main__":
    logging.getLogger("sregym.chsql_mcp").warning("per-query caps: %s", QUERY_SETTINGS)
    main()
