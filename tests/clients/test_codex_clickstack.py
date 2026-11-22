"""Tests for the Codex + ClickStack client."""

from pathlib import Path

import yaml

from clients.claudecode_clickstack import claudecode_agent as cc
from clients.codex_clickstack import codex_agent as agent
from clients.codex_clickstack.codex_agent import ClickStackCodexAgent, agents_md


def test_command_adds_the_mcp_server_before_the_instruction(monkeypatch, tmp_path):
    monkeypatch.setenv("CLICKSTACK_MCP_URL", "http://proxy.test:9300/mcp")
    monkeypatch.delenv("AGENT_API_BASE", raising=False)
    a = ClickStackCodexAgent(logs_dir=tmp_path, model_name="gpt-6.1-sol")
    cmd = a._build_command("do the thing")
    sep = cmd.index("--")
    assert cmd[sep + 1] == "do the thing"
    assert 'mcp_servers.clickstack.url="http://proxy.test:9300/mcp"' in cmd[:sep]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "gpt-6.1-sol"


def test_agents_md_contains_routing_and_the_tier_skill_body():
    text = agents_md(1, kubectl=False)
    assert "no `kubectl`" in text or "no\n`kubectl`" in text
    assert "k8s-objects" in text
    assert not text.startswith("---")  # frontmatter stripped
    assert "ConfigMap" not in text  # tier 1 only
    assert "ConfigMap" in agents_md(2, kubectl=True)


def test_instruction_wiring(monkeypatch):
    import clients.codex.driver as upstream
    from clients.codex_clickstack import driver

    monkeypatch.setattr(upstream, "build_instruction", upstream.build_instruction)
    monkeypatch.setattr(upstream, "build_instruction", driver.build_instruction)
    app = {"app_name": "demo", "namespace": "ns1", "descriptions": "d"}
    monkeypatch.setattr(cc, "AGENT_KUBECONFIG", Path("/nonexistent/kubeconfig"))
    monkeypatch.delenv("KUBECONFIG", raising=False)
    without = driver.build_instruction(app)
    assert driver.NO_KUBECTL_LINE + "ns1'" in without
    assert "kubectl" not in without.replace(driver.NO_KUBECTL_LINE, "")
    assert driver.CLICKSTACK_LINE in without
    monkeypatch.setenv("KUBECONFIG", "/somewhere")
    with_k = driver.build_instruction({**app, "stages": ["diagnosis"]})
    assert driver.KUBECTL_TOOL_LINE + "ns1'" in with_k and with_k.endswith(driver.DIAGNOSIS_ONLY_NOTE)


def test_registrations_cover_both_harnesses():
    reg = {a["name"]: a for a in yaml.safe_load(Path("agents.yaml").read_text())["agents"]}
    for harness in ("claudecode", "codex"):
        assert reg[f"{harness}_clickstack"].get("cluster_access", True) is True
        for tier in range(4):
            r = reg[f"{harness}_clickstack_only_t{tier}"]
            assert r["cluster_access"] is False
            assert r["kickoff_env"]["CLICKSTACK_TIER"] == str(tier)
            assert r["kickoff_env"]["CLICKSTACK_EXPECT_KUBECTL"] == "false"


def test_tier_variants_are_registered_for_both_harnesses():
    reg = {a["name"]: a for a in yaml.safe_load(Path("agents.yaml").read_text())["agents"]}
    for harness in ("claudecode", "codex"):
        for backend in ("clickstack", "chsql"):
            for tier in (3, 4):
                r = reg[f"{harness}_{backend}_t{tier}"]
                assert r.get("cluster_access", True) is True and r["kickoff_env"]["CLICKSTACK_TIER"] == str(tier)
            r = reg[f"{harness}_{backend}_only_t4"]
            assert r["cluster_access"] is False and r["kickoff_env"]["CLICKSTACK_TIER"] == "4"
        obs = reg[f"{harness}_obs"]
        assert obs["cluster_access"] is False and obs["kickoff_env"]["OBS_MCP_BASE"].endswith(":9954")
