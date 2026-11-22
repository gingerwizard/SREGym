"""Tests for the Claude Code + ClickHouse SQL client: allowlist, MCP config, skills, instruction wiring."""

import json
import re
from pathlib import Path

import pytest

from clients.claudecode.claudecode_agent import ClaudeCodeAgent
from clients.claudecode_chsql import claudecode_agent as agent
from clients.claudecode_chsql.claudecode_agent import (
    CORE_TOOLS,
    READ_ONLY_TOOLS,
    ROUTING_KUBECTL,
    ROUTING_NO_KUBECTL,
    SKILL_NAME,
    SKILLS_DIR,
    ClickHouseSqlClaudeCodeAgent,
)
from clients.claudecode_chsql.skills import build_skills
from tests.clients.test_claudecode_clickstack import FORBIDDEN_WORDS


def _skill_text(tier: int) -> str:
    return (SKILLS_DIR / f"tier{tier}" / SKILL_NAME / "SKILL.md").read_text()


def test_allowlist_adds_only_the_three_sql_tools(monkeypatch):
    monkeypatch.setenv("AGENT_INTERNET_ACCESS", "filtered")
    stock = ClaudeCodeAgent.allowed_tools()
    ours = ClickHouseSqlClaudeCodeAgent.allowed_tools()
    assert ours[: len(stock)] == stock
    mcp_tools = [t for t in ours if t.startswith("mcp__")]
    assert sorted(mcp_tools) == sorted(f"mcp__clickhouse__{t}" for t in READ_ONLY_TOOLS)
    assert "mcp__clickhouse__run_query" in ours
    assert not any(t.startswith("mcp__clickstack__") for t in ours)
    assert set(CORE_TOOLS) <= set(READ_ONLY_TOOLS)


def test_mcp_config_points_at_the_clickhouse_server(monkeypatch, tmp_path):
    monkeypatch.setenv("CHSQL_MCP_URL", "http://sql.test:9400/mcp")
    a = ClickHouseSqlClaudeCodeAgent(logs_dir=tmp_path, model_name="anthropic/claude-fable-5-2")
    config = json.loads(a._write_mcp_config().read_text())
    assert config == {"mcpServers": {"clickhouse": {"type": "http", "url": "http://sql.test:9400/mcp"}}}


@pytest.mark.parametrize("tier", [0, 1, 2, 3, 4])
def test_skill_is_installed_for_the_tier(monkeypatch, tmp_path, tier):
    monkeypatch.setenv("CLICKSTACK_TIER", str(tier))
    monkeypatch.chdir(tmp_path)
    a = ClickHouseSqlClaudeCodeAgent(logs_dir=tmp_path / "logs", model_name="claude-fable-5-2")
    installed = a._install_skill(agent.chsql_tier())
    assert len(installed) == 2
    for dest in installed:
        assert (dest / "SKILL.md").read_text() == _skill_text(tier)


def test_skills_are_cumulative_and_generated_from_sections(tmp_path):
    texts = [_skill_text(t) for t in range(5)]
    for lower, higher in zip(texts, texts[1:]):
        assert lower.split("\n# ", 1)[1] in higher
    build_skills.build(tmp_path)
    for tier in range(5):
        assert (tmp_path / f"tier{tier}" / SKILL_NAME / "SKILL.md").read_text() == texts[tier], "run build_skills.py"


def test_skill_is_sql_only_and_mirrors_the_clickstack_tiers():
    for tier in range(5):
        text = _skill_text(tier)
        assert text.startswith("---\nname: clickhouse-sql\ndescription: ")
        assert "mcp__clickhouse__run_query" in text and "default.otel_logs" in text
        assert "clickstack_" not in text and "$__" not in text and "Lucene" not in text
    assert "k8s-objects" not in _skill_text(0) and "k8s-objects" in _skill_text(1)
    assert "ConfigMap" not in _skill_text(1) and "ConfigMap" in _skill_text(2)
    assert "node-state" not in _skill_text(2) and "node-state" in _skill_text(3)


@pytest.mark.parametrize("path", [*(SKILLS_DIR / f"tier{t}" / SKILL_NAME / "SKILL.md" for t in range(5)), ROUTING_KUBECTL, ROUTING_NO_KUBECTL])
def test_agent_facing_text_names_nothing_benchmark_specific(path: Path):
    text = path.read_text()
    for word in FORBIDDEN_WORDS:
        assert not re.search(rf"\b{re.escape(word)}", text, flags=re.IGNORECASE), f"{path.name} mentions {word!r}"


def test_instruction_wiring(monkeypatch):
    import clients.claudecode.driver as upstream
    monkeypatch.setattr(upstream, "build_instruction", upstream.build_instruction)
    from clients.claudecode_chsql import driver

    monkeypatch.setattr(upstream, "build_instruction", driver.build_instruction)
    app = {"app_name": "demo", "namespaces": ["ns1"], "descriptions": "d"}
    stock = driver._upstream_build_instruction(app)
    import clients.claudecode_clickstack.claudecode_agent as cs
    monkeypatch.setattr(cs, "AGENT_KUBECONFIG", Path("/nonexistent/kubeconfig"))
    monkeypatch.delenv("KUBECONFIG", raising=False)
    without = driver.build_instruction(app)
    assert "kubectl" not in without.replace(driver.NO_KUBECTL_LINE, "")
    assert driver.NO_KUBECTL_LINE + "ns1" in without and "run_query" in without
    monkeypatch.setenv("KUBECONFIG", "/somewhere")
    with_kubectl = driver.build_instruction({**app, "stages": ["diagnosis"]})
    assert driver.KUBECTL_TOOL_LINE + "ns1" in with_kubectl
    assert with_kubectl.endswith(driver.DIAGNOSIS_ONLY_NOTE)
    assert with_kubectl.replace("\n" + driver.CLICKSTACK_LINE, "").replace("\n" + driver.DIAGNOSIS_ONLY_NOTE, "") == stock


def test_tier4_is_tier3_data_plus_investigation_rules():
    t3, t4 = _skill_text(3), _skill_text(4)
    assert "Investigation discipline" not in t3 and "Investigation discipline" in t4
    assert t3.split("\n# ", 1)[1] in t4
