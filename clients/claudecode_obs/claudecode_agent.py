"""Claude Code agent with SREGym's own observability tools and no kubectl.

The stock SREGym agent reaches Prometheus, Loki and Jaeger through kubectl. This variant gives the agent
the same three systems through SREGym's own MCP server (its /prometheus, /loki and /jaeger mounts, served
over SSE on the runner's port-forward) and nothing else: no kubeconfig, no API proxy, no /kubectl mount.
It answers the question "how far does stock telemetry get an agent that cannot touch the cluster?", the
stock-tools counterpart of the ClickStack-only and SQL-only arms.

OBS_MCP_BASE is the base URL of SREGym's MCP server as seen from the container
(default http://host.docker.internal:9954); CLICKSTACK_EXPECT_KUBECTL must be "false".
"""

import json
import logging
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from clients.claudecode.claudecode_agent import ClaudeCodeAgent
from clients.claudecode_clickstack.claudecode_agent import (
    EXTRA_TOOLS,
    check_cluster_access_expectation,
    remove_kubectl_from_container,
)

logger = logging.getLogger("all.claudecode_obs.agent")

HERE = Path(__file__).parent
ROUTING = HERE / "routing.md"
MOUNTS = ("prometheus", "loki", "jaeger")
# SREGym's MCP tools on those mounts (mcp_server/{prometheus,loki,jaeger}_server.py).
OBS_TOOLS = {
    "prometheus": ("get_metrics", "get_alerts"),
    "loki": ("get_logs", "get_labels", "get_label_values"),
    "jaeger": ("get_services", "get_operations", "get_traces", "get_dependency_graph"),
}


def obs_mcp_base() -> str:
    port = os.environ.get("MCP_SERVER_PORT", "9954")
    return os.environ.get("OBS_MCP_BASE", f"http://host.docker.internal:{port}").rstrip("/")


def mount_urls() -> dict[str, str]:
    base = obs_mcp_base()
    return {m: f"{base}/{m}/sse" for m in MOUNTS}


def wait_for_mounts(timeout_s: float = 90) -> None:
    """Block until every SSE mount answers 200 (the conductor's port-forward comes up with the problem)."""
    deadline = time.monotonic() + timeout_s
    pending = dict(mount_urls())
    last_error = ""
    while pending and time.monotonic() < deadline:
        for name, url in list(pending.items()):
            try:
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                    if resp.status == 200:
                        resp.read(1)
                        pending.pop(name)
                        continue
                    last_error = f"{name}: HTTP {resp.status}"
            except Exception as exc:  # noqa: BLE001
                last_error = f"{name}: {exc}"
        if pending:
            time.sleep(3)
    if pending:
        raise RuntimeError(f"observability MCP mounts not answering: {sorted(pending)} ({last_error})")
    logger.info(f"observability MCP mounts answer: {', '.join(mount_urls())}")


class ObservabilityClaudeCodeAgent(ClaudeCodeAgent):
    @classmethod
    def allowed_tools(cls) -> list[str]:
        return [*super().allowed_tools(), *EXTRA_TOOLS, *(f"mcp__{m}__{t}" for m, tools in OBS_TOOLS.items() for t in tools)]

    def _write_mcp_config(self) -> Path:
        path = self.logs_dir / "obs-mcp-config.json"
        path.write_text(json.dumps({"mcpServers": {m: {"type": "sse", "url": url} for m, url in mount_urls().items()}}, indent=2))
        return path

    def run(self, instruction: str) -> int:
        model = self.model_name.split("/")[-1]
        invalid_patterns = ["bedrock", "litellm", "azure", "openai", "watsonx", "gemini"]
        if any(pattern in model.lower() for pattern in invalid_patterns):
            logger.warning(f"Model '{model}' appears to be for a non-Anthropic provider. Defaulting to 'sonnet' for Claude Code.")
            model = "sonnet"
        logger.info(f"Running Claude Code (observability MCP, no kubectl) with instruction: {instruction}")
        self._setup_sessions_structure()
        env = os.environ.copy()
        env["CLAUDE_CONFIG_DIR"] = str(self.sessions_dir)
        env["FORCE_AUTO_BACKGROUND_TASKS"] = "1"
        env["ENABLE_BACKGROUND_TASKS"] = "1"
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        if not api_key and not oauth_token:
            logger.error("No Anthropic API authentication found (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN)")
            return 1
        if oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
            env.pop("ANTHROPIC_API_KEY", None)
        else:
            env["ANTHROPIC_API_KEY"] = api_key
        env["ANTHROPIC_MODEL"] = model
        reasoning_effort = os.environ.get("AGENT_REASONING_EFFORT")
        if "MAX_THINKING_TOKENS" in os.environ:
            env["MAX_THINKING_TOKENS"] = os.environ["MAX_THINKING_TOKENS"]

        wait_for_mounts()
        mcp_config = self._write_mcp_config()
        kubectl = check_cluster_access_expectation()
        if kubectl:
            raise RuntimeError("the observability-only arm must run without cluster access")
        remove_kubectl_from_container()
        routing = ROUTING.read_text()
        (self.logs_dir / "obs-run.json").write_text(json.dumps({"kubectl": False, "mounts": mount_urls(), "backend": "sregym-observability-mcp"}, indent=2))

        command = ["claude", "--verbose", "--output-format", "stream-json", "--thinking-display", "summarized", "-p", instruction]
        if reasoning_effort:
            command.extend(["--effort", reasoning_effort])
        command.extend(["--mcp-config", str(mcp_config), "--strict-mcp-config"])
        command.extend(["--append-system-prompt", routing])
        command.extend(["--permission-mode", "dontAsk"])
        command.extend(["--allowedTools", *self.allowed_tools()])
        logger.info(f"Executing command: {' '.join(c if c != routing else '<routing.md>' for c in command)}")
        try:
            with open(self.output_path, "w") as out_file:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, text=True, bufsize=1)
                for line in process.stdout:
                    out_file.write(line)
                    out_file.flush()
                    print(line, end="", flush=True)
                process.wait()
            logger.info(f"Claude Code finished with return code: {process.returncode}")
            return process.returncode
        except Exception as e:
            logger.error(f"Error running Claude Code: {e}")
            raise
