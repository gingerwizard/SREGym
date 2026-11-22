"""Driver for Codex with the ClickHouse MCP server (raw SQL).

Reuses clients.codex.driver (conductor handshake, submission, results), swaps in ClickHouseSqlCodexAgent,
and adjusts the task instruction the same way clients.claudecode_chsql does. The preflight checks the
ClickHouse MCP server offers `run_query`, then runs the stock Codex preflight.
"""

import sys

import clients.codex.driver as base
from clients.claudecode_chsql.claudecode_agent import (
    CORE_TOOLS,
    READ_ONLY_TOOLS,
    WRITE_TOOL_MARKERS,
    chsql_mcp_url,
    chsql_tier,
    has_cluster_access,
)
from clients.claudecode_clickstack.mcp_smoke import handshake
from clients.codex_chsql.codex_agent import ClickHouseSqlCodexAgent

base.CodexAgent = ClickHouseSqlCodexAgent

KUBECTL_TOOL_LINE = "- You have access to kubectl commands to inspect and modify resources in namespace '"
NO_KUBECTL_LINE = (
    "- This environment has no kubectl and no cluster credentials. The ClickHouse SQL tool (run_query on the "
    "clickhouse MCP server) over this cluster's telemetry database is your only view of the cluster, covering namespace '"
)
CLICKSTACK_LINE = (
    "- The ClickHouse MCP tool (run_query) queries this cluster's logs, metrics, traces and Kubernetes state, stored in "
    "ClickHouse. AGENTS.md explains which tables and columns exist and the SQL patterns that work; follow it."
)
DIAGNOSIS_ONLY_NOTE = (
    "- This run has a single task: DIAGNOSIS. After you submit the diagnosis you are done; do not attempt a mitigation."
)


def _pristine_build_instruction():
    builder = base.build_instruction
    while getattr(builder, "__module__", base.__name__) != base.__name__:
        builder = sys.modules[builder.__module__]._upstream_build_instruction
    return builder


_upstream_build_instruction = _pristine_build_instruction()


def build_instruction(app_info: dict) -> str:
    instruction = _upstream_build_instruction(app_info)
    if instruction.count(KUBECTL_TOOL_LINE) != 1:
        raise RuntimeError("upstream's kubectl tool line not found once in the task instruction")
    tool_line = KUBECTL_TOOL_LINE if has_cluster_access() else NO_KUBECTL_LINE
    instruction = instruction.replace(KUBECTL_TOOL_LINE, tool_line)
    lines = instruction.split("\n")
    index = next(i for i, line in enumerate(lines) if line.startswith(tool_line))
    lines.insert(index + 1, CLICKSTACK_LINE)
    if app_info.get("stages") == ["diagnosis"]:
        lines.append(DIAGNOSIS_ONLY_NOTE)
    return "\n".join(lines)


base.build_instruction = build_instruction


def _check_clickhouse_mcp() -> None:
    url = chsql_mcp_url()
    tier = chsql_tier()
    try:
        result = handshake(url, timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"ClickHouse MCP preflight: cannot complete an MCP handshake with {url}: {exc}")
        sys.exit(1)
    missing = sorted(set(READ_ONLY_TOOLS) - set(result["tools"]))
    missing_core = sorted(set(CORE_TOOLS) - set(result["tools"]))
    if missing_core:
        print(f"ClickHouse MCP preflight: server at {url} lacks core tools: {missing_core}")
        sys.exit(1)
    if missing:
        print(f"ClickHouse MCP preflight: server at {url} lacks non-core tools the client expects, continuing without them: {missing}")
    unknown = sorted(t for t in result["tools"] if t not in READ_ONLY_TOOLS and not any(m in t for m in WRITE_TOOL_MARKERS))
    if unknown:
        print(f"ClickHouse MCP preflight: server exposes tools this client does not know (not allowed to the agent): {unknown}")
    info = result["server"]
    print(f"ClickHouse MCP preflight OK: {info.get('name')} {info.get('version')} at {url}, "
          f"{len(result['tools'])} tools, skill tier {tier}, kubectl={'yes' if has_cluster_access() else 'no'}")


def run_preflight() -> None:
    _check_clickhouse_mcp()
    base.run_preflight()


if __name__ == "__main__":
    base.main()
