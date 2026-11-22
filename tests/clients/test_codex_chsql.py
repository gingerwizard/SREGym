"""Tests for the Codex + ClickHouse SQL client."""

from pathlib import Path

import yaml

import clients.claudecode_clickstack.claudecode_agent as cs
from clients.codex_chsql.codex_agent import ClickHouseSqlCodexAgent, agents_md


def test_command_adds_the_clickhouse_server_before_the_instruction(monkeypatch, tmp_path):
    monkeypatch.setenv("CHSQL_MCP_URL", "http://sql.test:9400/mcp")
    monkeypatch.delenv("AGENT_API_BASE", raising=False)
    a = ClickHouseSqlCodexAgent(logs_dir=tmp_path, model_name="gpt-6.1-sol")
    cmd = a._build_command("do the thing")
    sep = cmd.index("--")
    assert cmd[sep + 1] == "do the thing"
    assert 'mcp_servers.clickhouse.url="http://sql.test:9400/mcp"' in cmd[:sep]
    assert not any("clickstack" in c for c in cmd)


def test_agents_md_contains_sql_routing_and_the_tier_skill_body():
    text = agents_md(1, kubectl=False)
    assert "run_query" in text and "k8s-objects" in text and not text.startswith("---")
    assert "ConfigMap" not in text and "ConfigMap" in agents_md(2, kubectl=True)


def test_instruction_wiring(monkeypatch):
    import clients.codex.driver as upstream
    from clients.codex_chsql import driver

    monkeypatch.setattr(upstream, "build_instruction", upstream.build_instruction)
    monkeypatch.setattr(upstream, "build_instruction", driver.build_instruction)
    app = {"app_name": "demo", "namespace": "ns1", "descriptions": "d"}
    monkeypatch.setattr(cs, "AGENT_KUBECONFIG", Path("/nonexistent/kubeconfig"))
    monkeypatch.delenv("KUBECONFIG", raising=False)
    without = driver.build_instruction(app)
    assert driver.NO_KUBECTL_LINE + "ns1'" in without and "run_query" in without
    monkeypatch.setenv("KUBECONFIG", "/somewhere")
    with_k = driver.build_instruction({**app, "stages": ["diagnosis"]})
    assert driver.KUBECTL_TOOL_LINE + "ns1'" in with_k and with_k.endswith(driver.DIAGNOSIS_ONLY_NOTE)


def test_registrations_mirror_the_clickstack_arms():
    reg = {a["name"]: a for a in yaml.safe_load(Path("agents.yaml").read_text())["agents"]}
    for harness in ("claudecode", "codex"):
        top = reg[f"{harness}_chsql"]
        assert top.get("cluster_access", True) is True and top["kickoff_env"]["CLICKSTACK_TIER"] == "2"
        assert top["kickoff_env"]["CHSQL_MCP_URL"].endswith(":9400/mcp")
        for tier in range(4):
            r = reg[f"{harness}_chsql_only_t{tier}"]
            assert r["cluster_access"] is False
            assert r["kickoff_env"]["CLICKSTACK_TIER"] == str(tier)
            assert r["kickoff_env"]["CLICKSTACK_EXPECT_KUBECTL"] == "false"
            assert r["agent_version"] == reg[f"{harness}_clickstack_only_t{tier}"]["agent_version"]
