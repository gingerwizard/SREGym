"""Codex agent with the ClickStack MCP server attached.

Identical to clients.codex.codex_agent.CodexAgent except that the CLI is started with the ClickStack
MCP endpoint (the host-side auth proxy) as a config override, and AGENTS.md in the Codex home carries
the routing note plus the tier's clickstack-telemetry skill, which is how Codex takes standing
instructions. Read-only access is enforced by the proxy, which refuses every write tool.

CLICKSTACK_TIER, CLICKSTACK_MCP_URL and CLICKSTACK_EXPECT_KUBECTL mean the same as for
clients.claudecode_clickstack.
"""

import json
import logging
import os
from pathlib import Path

from clients.claudecode_clickstack.claudecode_agent import (
    ROUTING_KUBECTL,
    ROUTING_NO_KUBECTL,
    SKILL_NAME,
    SKILLS_DIR,
    check_cluster_access_expectation,
    clickstack_mcp_url,
    clickstack_tier,
    remove_kubectl_from_container,
)
from clients.codex.codex_agent import CodexAgent

logger = logging.getLogger("all.codex_clickstack.agent")


def skill_text(tier: int) -> str:
    return (SKILLS_DIR / f"tier{tier}" / SKILL_NAME / "SKILL.md").read_text()


def agents_md(tier: int, kubectl: bool) -> str:
    """Standing instructions for Codex: the routing note, then the skill body (frontmatter removed)."""
    routing = (ROUTING_KUBECTL if kubectl else ROUTING_NO_KUBECTL).read_text()
    body = skill_text(tier).split("\n---\n", 1)[-1] if skill_text(tier).startswith("---") else skill_text(tier)
    return f"{routing}\n\n{body}"


class ClickStackCodexAgent(CodexAgent):
    def _build_command(self, instruction: str) -> list[str]:
        command = super()._build_command(instruction)
        separator = command.index("--")
        extra = ["-c", f"mcp_servers.clickstack.url={json.dumps(clickstack_mcp_url())}",
                 "-c", "mcp_servers.clickstack.startup_timeout_sec=60"]
        return command[:separator] + extra + command[separator:]

    def run(self, instruction: str) -> int:
        tier = clickstack_tier()
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
        (self.logs_dir / "clickstack-run.json").write_text(
            json.dumps({"tier": tier, "kubectl": kubectl, "mcp_url": clickstack_mcp_url(), "skill": SKILL_NAME, "harness": "codex"}, indent=2)
        )
        return super().run(instruction)
