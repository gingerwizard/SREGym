"""Claude Code agent with the ClickStack MCP server attached.

Identical to clients.claudecode.claudecode_agent.ClaudeCodeAgent except that the CLI is started with:
  --mcp-config            the ClickStack MCP endpoint, as served by the host-side auth proxy
                          (clients/claudecode_clickstack/mcp_auth_proxy.py); the container holds no
                          cloud credentials
  --strict-mcp-config
  --append-system-prompt  routing_kubectl.md or routing_no_kubectl.md, depending on whether the
                          registration gave the container cluster access
  --permission-mode dontAsk
and the read-only `mcp__clickstack__*` tools plus EXTRA_TOOLS are added to the allowed tools.

Before the CLI starts, the tier's `clickstack-telemetry` skill is installed where Claude Code loads
skills from, so the agent can read how this environment's telemetry and Kubernetes state are stored.
CLICKSTACK_TIER selects the skill (0-3; higher tiers describe more of the cluster state that the
collectors ship), CLICKSTACK_MCP_URL the endpoint, CLICKSTACK_EXPECT_KUBECTL whether the registration
is meant to provide kubectl (a mismatch fails loudly instead of silently changing the experiment).
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from clients.claudecode.claudecode_agent import ClaudeCodeAgent

logger = logging.getLogger("all.claudecode_clickstack.agent")

HERE = Path(__file__).parent
SKILLS_DIR = HERE / "skills"
SKILL_NAME = "clickstack-telemetry"
ROUTING_KUBECTL = HERE / "routing_kubectl.md"
ROUTING_NO_KUBECTL = HERE / "routing_no_kubectl.md"
AGENT_KUBECONFIG = Path("/root/.kube/config")

# Read-only subset of the ClickStack MCP tool list (36 tools as of 18 November 2026). Everything that
# saves, patches or deletes dashboards, alerts, sources, saved searches or webhooks, and the feedback
# submission tool, is excluded. The managed server renames tools now and then (the trace hot-spans tool
# went from ..._operations to ..._ops on 18 November), so the preflight only insists on CORE_TOOLS.
READ_ONLY_TOOLS = (
    "clickstack_describe_metric",
    "clickstack_describe_source",
    "clickstack_describe_table",
    "clickstack_emerging_signals",
    "clickstack_event_deltas",
    "clickstack_event_patterns",
    "clickstack_get_alert",
    "clickstack_get_dashboard",
    "clickstack_get_dashboard_tile",
    "clickstack_get_saved_search",
    "clickstack_get_source_filter_values",
    "clickstack_get_webhook",
    "clickstack_list_metrics",
    "clickstack_list_sources",
    "clickstack_list_teams",
    "clickstack_query_tile",
    "clickstack_query_tiles",
    "clickstack_search",
    "clickstack_search_dashboards",
    "clickstack_service_health",
    "clickstack_sql",
    "clickstack_table",
    "clickstack_timeseries",
    "clickstack_trace_top_time_consuming_ops",
    "clickstack_trace_waterfall",
)
# The tools the skills and routing texts rely on; a server without one of these cannot run the arm.
CORE_TOOLS = (
    "clickstack_list_sources",
    "clickstack_describe_source",
    "clickstack_search",
    "clickstack_table",
    "clickstack_timeseries",
    "clickstack_sql",
    "clickstack_service_health",
    "clickstack_event_patterns",
    "clickstack_trace_waterfall",
)
WRITE_TOOL_MARKERS = ("save_", "delete_", "patch_", "backtest_", "submit_")

# Same additions as clients/claudecode_radar: ToolSearch loads deferred MCP tool schemas, the rest are
# background-task and MCP-resource helpers.
EXTRA_TOOLS = [
    "ToolSearch",
    "Monitor",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskStop",
    "TaskUpdate",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    "ReadMcpResourceDirTool",
]


def clickstack_mcp_url() -> str:
    return os.environ.get("CLICKSTACK_MCP_URL", "http://host.docker.internal:9300/mcp")


def clickstack_tier() -> int:
    raw = os.environ.get("CLICKSTACK_TIER", "2")
    try:
        tier = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"CLICKSTACK_TIER must be an integer, got {raw!r}") from exc
    if not (SKILLS_DIR / f"tier{tier}" / SKILL_NAME / "SKILL.md").is_file():
        raise RuntimeError(f"no skill for CLICKSTACK_TIER={tier} under {SKILLS_DIR}")
    return tier


def has_cluster_access() -> bool:
    return bool(os.environ.get("KUBECONFIG")) or AGENT_KUBECONFIG.exists()


def check_cluster_access_expectation() -> bool:
    """Return whether kubectl is available, failing if it contradicts CLICKSTACK_EXPECT_KUBECTL."""
    actual = has_cluster_access()
    expected = os.environ.get("CLICKSTACK_EXPECT_KUBECTL")
    if expected is not None and (expected.lower() == "true") != actual:
        raise RuntimeError(
            f"CLICKSTACK_EXPECT_KUBECTL={expected} but the container {'has' if actual else 'has no'} cluster "
            "credentials; fix cluster_access on this agent's registration in agents.yaml"
        )
    return actual


def remove_kubectl_from_container() -> None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / "kubectl"
        if candidate.is_file():
            candidate.unlink()
            logger.info(f"removed {candidate}")


class ClickStackClaudeCodeAgent(ClaudeCodeAgent):
    @classmethod
    def allowed_tools(cls) -> list[str]:
        return [*super().allowed_tools(), *EXTRA_TOOLS, *(f"mcp__clickstack__{t}" for t in READ_ONLY_TOOLS)]

    def _write_mcp_config(self) -> Path:
        path = self.logs_dir / "clickstack-mcp-config.json"
        path.write_text(json.dumps({"mcpServers": {"clickstack": {"type": "http", "url": clickstack_mcp_url()}}}, indent=2))
        return path

    def _install_skill(self, tier: int) -> list[Path]:
        """Copy the tier's skill where Claude Code discovers skills: the config dir and the project dir."""
        source = SKILLS_DIR / f"tier{tier}" / SKILL_NAME
        installed = []
        for root in (self.sessions_dir / "skills", Path.cwd() / ".claude" / "skills"):
            dest = root / SKILL_NAME
            try:
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(source, dest)
                installed.append(dest)
            except OSError as exc:
                logger.warning(f"could not install skill at {dest}: {exc}")
        if not installed:
            raise RuntimeError(f"could not install the {SKILL_NAME} skill anywhere")
        logger.info(f"installed {SKILL_NAME} tier {tier} at {[str(p) for p in installed]}")
        return installed

    def run(self, instruction: str) -> int:
        # Mirrors ClaudeCodeAgent.run; the additions are marked CLICKSTACK.
        model = self.model_name.split("/")[-1]
        invalid_patterns = ["bedrock", "litellm", "azure", "openai", "watsonx", "gemini"]
        if any(pattern in model.lower() for pattern in invalid_patterns):
            logger.warning(
                f"Model '{model}' appears to be for a non-Anthropic provider. Defaulting to 'sonnet' for Claude Code."
            )
            model = "sonnet"

        logger.info(f"Running Claude Code (ClickStack MCP) with instruction: {instruction}")
        logger.info(f"Using model: {model}")

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
        elif api_key:
            env["ANTHROPIC_API_KEY"] = api_key

        env["ANTHROPIC_MODEL"] = model
        reasoning_effort = os.environ.get("AGENT_REASONING_EFFORT")
        if "MAX_THINKING_TOKENS" in os.environ:
            env["MAX_THINKING_TOKENS"] = os.environ["MAX_THINKING_TOKENS"]

        # CLICKSTACK: MCP config, the tier skill, and the routing note for this variant.
        mcp_config = self._write_mcp_config()
        tier = clickstack_tier()
        self._install_skill(tier)
        kubectl = check_cluster_access_expectation()
        if not kubectl:
            remove_kubectl_from_container()
        routing = (ROUTING_KUBECTL if kubectl else ROUTING_NO_KUBECTL).read_text()
        (self.logs_dir / "clickstack-run.json").write_text(
            json.dumps({"tier": tier, "kubectl": kubectl, "mcp_url": clickstack_mcp_url(), "skill": SKILL_NAME}, indent=2)
        )

        command = [
            "claude",
            "--verbose",
            "--output-format",
            "stream-json",
            "--thinking-display",
            "summarized",
            "-p",
            instruction,
        ]
        if reasoning_effort:
            command.extend(["--effort", reasoning_effort])
        # CLICKSTACK
        command.extend(["--mcp-config", str(mcp_config), "--strict-mcp-config"])
        command.extend(["--append-system-prompt", routing])
        command.extend(["--permission-mode", "dontAsk"])
        command.extend(["--allowedTools", *self.allowed_tools()])

        logger.info(f"Executing command: {' '.join(c if c != routing else '<routing.md>' for c in command)}")

        try:
            with open(self.output_path, "w") as out_file:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    env=env,
                    text=True,
                    bufsize=1,
                )
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
