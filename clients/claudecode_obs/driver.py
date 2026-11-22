"""Driver for Claude Code with SREGym's observability MCP mounts and no kubectl.

Reuses clients.claudecode.driver and swaps in ObservabilityClaudeCodeAgent. The task instruction's kubectl
line is replaced by a line naming the three MCP servers; a diagnosis-only run is told to stop after the
diagnosis. The agent waits for the SSE mounts at run start (they exist only while a problem runs).
"""

import sys

import clients.claudecode.driver as base
from clients.claudecode_obs.claudecode_agent import ObservabilityClaudeCodeAgent, mount_urls

base.ClaudeCodeAgent = ObservabilityClaudeCodeAgent

KUBECTL_TOOL_LINE = "- You have access to kubectl commands to inspect and modify resources in namespace(s): "
NO_KUBECTL_LINE = (
    "- This environment has no kubectl and no cluster credentials. Prometheus, Loki and Jaeger, through the MCP "
    "servers prometheus, loki and jaeger, are your only view of the cluster, covering namespace(s): "
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
    instruction = instruction.replace(KUBECTL_TOOL_LINE, NO_KUBECTL_LINE)
    if app_info.get("stages") == ["diagnosis"]:
        instruction += "\n" + DIAGNOSIS_ONLY_NOTE
    return instruction


base.build_instruction = build_instruction


def _check_mounts() -> None:
    """The mounts are served by the conductor's port-forward, which exists only while a problem runs, so they
    cannot be probed at preflight; the agent waits for them at run start. Print the URLs for the record."""
    print(f"Observability MCP preflight: mounts {', '.join(mount_urls().values())} are checked at run start; kubectl=no")


def run_preflight() -> None:
    _check_mounts()
    base.run_preflight()


if __name__ == "__main__":
    base.main()
