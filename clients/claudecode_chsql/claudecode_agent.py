"""Claude Code agent with the ClickHouse MCP server (raw SQL) attached.

The ClickHouse-SQL counterpart of clients.claudecode_clickstack: the same container, the same tier skills
rewritten for SQL, but the only interface to the telemetry is the open-source ClickHouse MCP server
(mcp-clickhouse, pinned) with `run_query`, `list_databases` and `list_tables`, connected as a read-only
database user. No ClickStack builder tools. Comparing these arms with the ClickStack arms measures what
ClickStack's purpose-built tools add over SQL on the same data.

CLICKSTACK_TIER selects the skill (0-3) and is also what the campaign runner uses to set the collector
tier, so the data in the database matches the skill. CHSQL_MCP_URL is the endpoint,
CLICKSTACK_EXPECT_KUBECTL whether the registration is meant to provide kubectl.
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from clients.claudecode.claudecode_agent import ClaudeCodeAgent
from clients.claudecode_clickstack.claudecode_agent import (
    EXTRA_TOOLS,
    check_cluster_access_expectation,
    has_cluster_access,
    remove_kubectl_from_container,
)

logger = logging.getLogger("all.claudecode_chsql.agent")

HERE = Path(__file__).parent
SKILLS_DIR = HERE / "skills"
SKILL_NAME = "clickhouse-sql"
ROUTING_KUBECTL = HERE / "routing_kubectl.md"
ROUTING_NO_KUBECTL = HERE / "routing_no_kubectl.md"
AGENT_KUBECONFIG = Path("/root/.kube/config")

# The ClickHouse MCP server (mcp-clickhouse 0.7.0) exposes three tools; writes are disabled server-side
# (CLICKHOUSE_ALLOW_WRITE_ACCESS=false) and the database user has SELECT only.
READ_ONLY_TOOLS = (
    "run_query",
    "list_databases",
    "list_tables",
)
CORE_TOOLS = ("run_query",)
WRITE_TOOL_MARKERS = ()
MCP_SERVER_KEY = "clickhouse"


def chsql_mcp_url() -> str:
    return os.environ.get("CHSQL_MCP_URL", "http://host.docker.internal:9400/mcp")


def chsql_tier() -> int:
    raw = os.environ.get("CLICKSTACK_TIER", "2")
    try:
        tier = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"CLICKSTACK_TIER must be an integer, got {raw!r}") from exc
    if not (SKILLS_DIR / f"tier{tier}" / SKILL_NAME / "SKILL.md").is_file():
        raise RuntimeError(f"no skill for CLICKSTACK_TIER={tier} under {SKILLS_DIR}")
    return tier


class ClickHouseSqlClaudeCodeAgent(ClaudeCodeAgent):
    @classmethod
    def allowed_tools(cls) -> list[str]:
        return [*super().allowed_tools(), *EXTRA_TOOLS, *(f"mcp__{MCP_SERVER_KEY}__{t}" for t in READ_ONLY_TOOLS)]

    def _write_mcp_config(self) -> Path:
        path = self.logs_dir / "chsql-mcp-config.json"
        path.write_text(json.dumps({"mcpServers": {MCP_SERVER_KEY: {"type": "http", "url": chsql_mcp_url()}}}, indent=2))
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

        logger.info(f"Running Claude Code (ClickHouse SQL MCP) with instruction: {instruction}")
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

        # CHSQL: MCP config, the tier skill, and the routing note for this variant.
        mcp_config = self._write_mcp_config()
        tier = chsql_tier()
        self._install_skill(tier)
        kubectl = check_cluster_access_expectation()
        if not kubectl:
            remove_kubectl_from_container()
        routing = (ROUTING_KUBECTL if kubectl else ROUTING_NO_KUBECTL).read_text()
        (self.logs_dir / "chsql-run.json").write_text(
            json.dumps({"tier": tier, "kubectl": kubectl, "mcp_url": chsql_mcp_url(), "skill": SKILL_NAME, "backend": "clickhouse-sql"}, indent=2)
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
        # CHSQL
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
