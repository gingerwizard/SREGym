"""Codex agent with the ClickHouse MCP server (raw SQL) attached.

The ClickHouse-SQL counterpart of clients.codex_clickstack: the CLI is started with the ClickHouse MCP
server (mcp-clickhouse, read-only database user) as a config override, and AGENTS.md carries the routing
note plus the tier's clickhouse-sql skill. CLICKSTACK_TIER, CHSQL_MCP_URL and CLICKSTACK_EXPECT_KUBECTL
mean the same as for clients.claudecode_chsql.
"""

import json
import logging
import os
from pathlib import Path

from clients.claudecode_chsql.claudecode_agent import (
    MCP_SERVER_KEY,
    ROUTING_KUBECTL,
    ROUTING_NO_KUBECTL,
    SKILL_NAME,
    SKILLS_DIR,
    check_cluster_access_expectation,
    chsql_mcp_url,
    chsql_tier,
    remove_kubectl_from_container,
)
from clients.codex.codex_agent import CodexAgent

logger = logging.getLogger("all.codex_chsql.agent")


def skill_text(tier: int) -> str:
    return (SKILLS_DIR / f"tier{tier}" / SKILL_NAME / "SKILL.md").read_text()


def agents_md(tier: int, kubectl: bool) -> str:
    """Standing instructions for Codex: the routing note, then the skill body (frontmatter removed)."""
    routing = (ROUTING_KUBECTL if kubectl else ROUTING_NO_KUBECTL).read_text()
    body = skill_text(tier).split("\n---\n", 1)[-1] if skill_text(tier).startswith("---") else skill_text(tier)
    return f"{routing}\n\n{body}"


class ClickHouseSqlCodexAgent(CodexAgent):
    def _build_command(self, instruction: str) -> list[str]:
        command = super()._build_command(instruction)
        separator = command.index("--")
        extra = ["-c", f"mcp_servers.{MCP_SERVER_KEY}.url={json.dumps(chsql_mcp_url())}",
                 "-c", f"mcp_servers.{MCP_SERVER_KEY}.startup_timeout_sec=60"]
        return command[:separator] + extra + command[separator:]

    def run(self, instruction: str) -> int:
        tier = chsql_tier()
        kubectl = check_cluster_access_expectation()
        if not kubectl:
            remove_kubectl_from_container()
        text = agents_md(tier, kubectl)
        written = []
        for root in (self.codex_home, Path.cwd()):
            try:
                (root / "AGENTS.md").write_text(text)
                written.append(str(root / "AGENTS.md"))
            except OSError as exc:
                logger.warning(f"could not write AGENTS.md under {root}: {exc}")
        if not written:
            raise RuntimeError("could not write AGENTS.md anywhere")
        logger.info(f"wrote routing note and {SKILL_NAME} tier {tier} to {written}")
        (self.logs_dir / "chsql-run.json").write_text(
            json.dumps({"tier": tier, "kubectl": kubectl, "mcp_url": chsql_mcp_url(), "skill": SKILL_NAME, "harness": "codex", "backend": "clickhouse-sql"}, indent=2)
        )
        return super().run(instruction)
