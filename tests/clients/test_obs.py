"""Tests for the observability-only (no kubectl) clients."""

from pathlib import Path

import clients.claudecode_clickstack.claudecode_agent as cs
from clients.claudecode.claudecode_agent import ClaudeCodeAgent
from clients.claudecode_obs.claudecode_agent import OBS_TOOLS, ObservabilityClaudeCodeAgent, mount_urls
from clients.codex_obs.codex_agent import ObservabilityCodexAgent
from tests.clients.test_claudecode_clickstack import FORBIDDEN_WORDS
import re


def test_allowlist_is_the_nine_observability_tools_and_no_kubectl_mount(monkeypatch):
    monkeypatch.setenv("AGENT_INTERNET_ACCESS", "filtered")
    stock = ClaudeCodeAgent.allowed_tools()
    ours = ObservabilityClaudeCodeAgent.allowed_tools()
    assert ours[: len(stock)] == stock
    mcp = [t for t in ours if t.startswith("mcp__")]
    assert sorted(mcp) == sorted(f"mcp__{m}__{t}" for m, ts in OBS_TOOLS.items() for t in ts)
    assert len(mcp) == 9 and not any("kubectl" in t for t in mcp)


def test_mount_urls_follow_the_mcp_port(monkeypatch):
    monkeypatch.delenv("OBS_MCP_BASE", raising=False)
    monkeypatch.setenv("MCP_SERVER_PORT", "9999")
    assert mount_urls()["loki"] == "http://host.docker.internal:9999/loki/sse"
    monkeypatch.setenv("OBS_MCP_BASE", "http://obs.test:1234/")
    assert mount_urls()["jaeger"] == "http://obs.test:1234/jaeger/sse"


def test_codex_command_adds_three_sse_servers(monkeypatch, tmp_path):
    monkeypatch.setenv("OBS_MCP_BASE", "http://obs.test:9954")
    monkeypatch.delenv("AGENT_API_BASE", raising=False)
    cmd = ObservabilityCodexAgent(logs_dir=tmp_path, model_name="gpt-6.1-sol")._build_command("go")
    sep = cmd.index("--")
    joined = " ".join(cmd[:sep])
    for m in ("prometheus", "loki", "jaeger"):
        # Codex speaks streamable HTTP to a url server and SREGym's mounts speak SSE, so each mount is a stdio bridge
        assert f'mcp_servers.{m}.command=' in joined
        assert f'sse_stdio_bridge.py", "http://obs.test:9954/{m}/sse"]' in joined
    assert "kubectl" not in joined


def test_instruction_wiring_claude(monkeypatch):
    import clients.claudecode.driver as upstream
    from clients.claudecode_obs import driver
    monkeypatch.setattr(upstream, "build_instruction", driver.build_instruction)
    monkeypatch.setattr(cs, "AGENT_KUBECONFIG", Path("/nonexistent/kubeconfig"))
    monkeypatch.delenv("KUBECONFIG", raising=False)
    text = driver.build_instruction({"app_name": "demo", "namespaces": ["ns1"], "descriptions": "d", "stages": ["diagnosis"]})
    assert driver.NO_KUBECTL_LINE + "ns1" in text and "kubectl" not in text.replace(driver.NO_KUBECTL_LINE, "")
    assert text.endswith(driver.DIAGNOSIS_ONLY_NOTE)


def test_routing_text_names_nothing_benchmark_specific():
    text = (Path("clients/claudecode_obs/routing.md")).read_text()
    for word in FORBIDDEN_WORDS:
        assert not re.search(rf"\b{re.escape(word)}", text, flags=re.IGNORECASE), word
