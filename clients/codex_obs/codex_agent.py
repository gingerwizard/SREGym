"""Codex agent with SREGym's observability MCP mounts and no kubectl (see clients.claudecode_obs)."""

import json
import logging
import sys
from pathlib import Path

from clients.claudecode_obs.claudecode_agent import ROUTING, check_cluster_access_expectation, mount_urls, remove_kubectl_from_container, wait_for_mounts
from clients.codex.codex_agent import CodexAgent

logger = logging.getLogger("all.codex_obs.agent")


class ObservabilityCodexAgent(CodexAgent):
    def _build_command(self, instruction: str) -> list[str]:
        command = super()._build_command(instruction)
        separator = command.index("--")
        extra = []
        # Codex speaks streamable HTTP to a `url` server and SREGym's mounts speak HTTP+SSE (a POST to them is a 405),
        # so each mount is attached as a stdio server through the bridge beside this file.
        bridge = str(Path(__file__).resolve().parent / "sse_stdio_bridge.py")
        for name, url in mount_urls().items():
            extra += ["-c", f"mcp_servers.{name}.command={json.dumps(sys.executable or 'python3')}",
                      "-c", f"mcp_servers.{name}.args={json.dumps([bridge, url])}",
                      "-c", f"mcp_servers.{name}.startup_timeout_sec=60"]
        return command[:separator] + extra + command[separator:]

    def run(self, instruction: str) -> int:
        wait_for_mounts()
        if check_cluster_access_expectation():
            raise RuntimeError("the observability-only arm must run without cluster access")
        remove_kubectl_from_container()
        text = ROUTING.read_text()
        written = []
        for root in (self.codex_home, Path.cwd()):
            try:
                (root / "AGENTS.md").write_text(text)
                written.append(str(root / "AGENTS.md"))
            except OSError as exc:
                logger.warning(f"could not write AGENTS.md under {root}: {exc}")
        if not written:
            raise RuntimeError("could not write AGENTS.md anywhere")
        (self.logs_dir / "obs-run.json").write_text(json.dumps({"kubectl": False, "mounts": mount_urls(), "backend": "sregym-observability-mcp", "harness": "codex", "transport": "stdio bridge to SSE"}, indent=2))
        return super().run(instruction)
