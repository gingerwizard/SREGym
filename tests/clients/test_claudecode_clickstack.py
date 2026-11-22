"""Tests for the Claude Code + ClickStack client: tool allowlist, MCP config, skills, instruction wiring."""

import json
import re
from pathlib import Path

import pytest

from clients.claudecode.claudecode_agent import ClaudeCodeAgent
from clients.claudecode_clickstack import claudecode_agent as agent
from clients.claudecode_clickstack.claudecode_agent import (
    READ_ONLY_TOOLS,
    ROUTING_KUBECTL,
    ROUTING_NO_KUBECTL,
    SKILL_NAME,
    SKILLS_DIR,
    WRITE_TOOL_MARKERS,
    ClickStackClaudeCodeAgent,
)
from clients.claudecode_clickstack.skills import build_skills

# Nothing an agent reads may name the benchmark, its applications, its problems or its grading.
FORBIDDEN_WORDS = (
    "SREGym", "sregym", "hotel", "astronomy", "social-network", "social_network", "train-ticket", "trainticket",
    "tidb", "frappe", "saleor", "slack", "gitlab", "gitea", "mattermost", "stripe", "fleet", "flight",
    "oracle", "submit", "inject", "fault", "chaos", "khaos", "problem_id", "rubric",
)


def _skill_text(tier: int) -> str:
    return (SKILLS_DIR / f"tier{tier}" / SKILL_NAME / "SKILL.md").read_text()


def test_allowlist_adds_only_read_tools(monkeypatch):
    monkeypatch.setenv("AGENT_INTERNET_ACCESS", "filtered")
    stock = ClaudeCodeAgent.allowed_tools()
    ours = ClickStackClaudeCodeAgent.allowed_tools()

    assert ours[: len(stock)] == stock
    assert "WebFetch" not in ours
    mcp_tools = [t for t in ours if t.startswith("mcp__clickstack__")]
    assert len(mcp_tools) == len(READ_ONLY_TOOLS) >= 20
    assert "mcp__clickstack" not in ours  # no blanket prefix grant
    for tool in mcp_tools:
        assert not any(marker in tool for marker in WRITE_TOOL_MARKERS), tool
    for must in ("clickstack_search", "clickstack_sql", "clickstack_service_health", "clickstack_list_sources"):
        assert f"mcp__clickstack__{must}" in ours


def test_mcp_config_points_at_the_configured_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("CLICKSTACK_MCP_URL", "http://proxy.test:9300/mcp")
    a = ClickStackClaudeCodeAgent(logs_dir=tmp_path, model_name="anthropic/claude-opus-5-5")
    config = json.loads(a._write_mcp_config().read_text())
    assert config == {"mcpServers": {"clickstack": {"type": "http", "url": "http://proxy.test:9300/mcp"}}}


@pytest.mark.parametrize("tier", [0, 1, 2, 3, 4])
def test_skill_is_installed_for_the_tier(monkeypatch, tmp_path, tier):
    monkeypatch.setenv("CLICKSTACK_TIER", str(tier))
    monkeypatch.chdir(tmp_path)
    a = ClickStackClaudeCodeAgent(logs_dir=tmp_path / "logs", model_name="claude-opus-5-5")
    installed = a._install_skill(agent.clickstack_tier())
    assert len(installed) == 2
    for dest in installed:
        assert (dest / "SKILL.md").read_text() == _skill_text(tier)


def test_tier_out_of_range_is_rejected(monkeypatch):
    monkeypatch.setenv("CLICKSTACK_TIER", "9")
    with pytest.raises(RuntimeError):
        agent.clickstack_tier()


def test_skills_are_cumulative_and_generated_from_sections(tmp_path):
    texts = [_skill_text(t) for t in range(5)]
    for lower, higher in zip(texts, texts[1:]):
        body_lower = lower.split("\n# ", 1)[1]
        assert body_lower in higher
    build_skills.build(tmp_path)
    for tier in range(5):
        assert (tmp_path / f"tier{tier}" / SKILL_NAME / "SKILL.md").read_text() == texts[tier], "run build_skills.py"


def test_skill_frontmatter_and_content():
    for tier in range(5):
        text = _skill_text(tier)
        assert text.startswith("---\nname: clickstack-telemetry\ndescription: ")
        assert "clickstack_list_sources" in text
    assert "k8s-objects" not in _skill_text(0) and "k8s-objects" in _skill_text(1)
    assert "ConfigMap" not in _skill_text(1) and "ConfigMap" in _skill_text(2)
    assert "node-state" not in _skill_text(2) and "node-state" in _skill_text(3)
    assert "node-syslog" not in _skill_text(1) and "node-syslog" in _skill_text(2)


@pytest.mark.parametrize("path", [*(SKILLS_DIR / f"tier{t}" / SKILL_NAME / "SKILL.md" for t in range(5)), ROUTING_KUBECTL, ROUTING_NO_KUBECTL])
def test_agent_facing_text_names_nothing_benchmark_specific(path: Path):
    text = path.read_text()
    for word in FORBIDDEN_WORDS:
        assert not re.search(rf"\b{re.escape(word)}", text, flags=re.IGNORECASE), f"{path.name} mentions {word!r}"


def test_instruction_wiring(monkeypatch):
    import clients.claudecode.driver as upstream
    monkeypatch.setattr(upstream, "build_instruction", upstream.build_instruction)
    from clients.claudecode_clickstack import driver

    # Importing the driver patches the stock module (as in the radar client); put it back afterwards so
    # other clients' tests in this process see the stock builder.
    monkeypatch.setattr(upstream, "build_instruction", driver.build_instruction)
    assert upstream.build_instruction is driver.build_instruction
    app = {"app_name": "demo", "namespaces": ["ns1"], "descriptions": "d"}
    stock = driver._upstream_build_instruction(app)

    monkeypatch.setattr(agent, "AGENT_KUBECONFIG", Path("/nonexistent/kubeconfig"))
    monkeypatch.delenv("KUBECONFIG", raising=False)
    without = driver.build_instruction(app)
    assert "kubectl" not in without.replace(driver.NO_KUBECTL_LINE, "")
    assert driver.NO_KUBECTL_LINE + "ns1" in without
    assert driver.CLICKSTACK_LINE in without
    assert driver.DIAGNOSIS_ONLY_NOTE not in without

    monkeypatch.setenv("KUBECONFIG", "/somewhere")
    with_kubectl = driver.build_instruction({**app, "stages": ["diagnosis"]})
    assert driver.KUBECTL_TOOL_LINE + "ns1" in with_kubectl
    assert with_kubectl.endswith(driver.DIAGNOSIS_ONLY_NOTE)
    assert with_kubectl.replace("\n" + driver.CLICKSTACK_LINE, "").replace("\n" + driver.DIAGNOSIS_ONLY_NOTE, "") == stock


def test_cluster_access_expectation(monkeypatch):
    monkeypatch.setattr(agent, "AGENT_KUBECONFIG", Path("/nonexistent/kubeconfig"))
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.setenv("CLICKSTACK_EXPECT_KUBECTL", "false")
    assert agent.check_cluster_access_expectation() is False
    monkeypatch.setenv("CLICKSTACK_EXPECT_KUBECTL", "true")
    with pytest.raises(RuntimeError):
        agent.check_cluster_access_expectation()


def test_tier4_is_tier3_data_plus_investigation_rules():
    t3, t4 = _skill_text(3), _skill_text(4)
    assert "Investigation discipline" not in t3 and "Investigation discipline" in t4
    assert t3.split("\n# ", 1)[1] in t4
