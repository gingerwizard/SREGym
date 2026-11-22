#!/usr/bin/env python3
"""Run the same SREGym problems across several models and agent arms, one run after another.

Each (arm, model) pair is one `main.py` invocation. SREGym names its results directory by the
minute and never records the model, so this runner writes a manifest (JSON lines) that joins
agent, model, skill tier, kubectl availability, stages and the results directory; `report.py`
reads that manifest to build the HTML report.

Example:
  uv run python clickstack/campaign.py --name anthropic-sweep \\
      --models claude-opus-5-5 claude-sonnet-5-5 claude-haiku-5-5 \\
      --arms claudecode_clickstack claudecode_clickstack_only_t2 \\
      --problem service_wrong_pod_selection_hotel_reservation --n-attempts 1
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"
CAMPAIGNS = RESULTS / "campaigns"
DEFAULT_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
DEFAULT_ARMS = ["claudecode", "claudecode_clickstack", "claudecode_clickstack_only_t0", "claudecode_clickstack_only_t1",
                "claudecode_clickstack_only_t2", "claudecode_clickstack_only_t3"]
OBS_ENDPOINT = "http://host.docker.internal:9954"  # SREGym's MCP server port-forward (MCP_SERVER_PORT), for the *_obs arms
CHSQL_ENDPOINT = "http://host.docker.internal:9400"  # the ClickHouse MCP server (mcp-clickhouse) on the runner, SQL-only arms
MCP_ENDPOINT = "http://host.docker.internal:9300"


def model_arg(model: str) -> str:
    """LiteLLM-style model string for main.py: Anthropic ids get the provider prefix, OpenAI ids are bare."""
    return model if model.startswith(("gpt", "o1", "o3", "o4", "codex")) else f"anthropic/{model}"


def registry() -> dict[str, dict]:
    data = yaml.safe_load((REPO / "agents.yaml").read_text())
    return {a["name"]: a for a in data["agents"]}


def arm_profile(reg: dict[str, dict], arm: str) -> dict:
    entry = reg[arm]
    env = entry.get("kickoff_env") or {}
    cluster = entry.get("cluster_access", True)
    return {
        "agent": arm,
        "tier": int(env.get("CLICKSTACK_TIER", -1)) if "CLICKSTACK_TIER" in env else None,
        "kubectl": bool(cluster),
        "stages": ["diagnosis"] if not cluster else ["diagnosis", "mitigation"],
    }


def set_gateway_tier(tier: int) -> None:
    """Make the collectors ship exactly the Kubernetes state the arm's skill describes."""
    subprocess.run(["kubectl", "-n", "otel", "set", "env", "deployment/otel-gateway", f"CLICKSTACK_TIER={tier}"], check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run(["kubectl", "-n", "otel", "rollout", "status", "deployment/otel-gateway", "--timeout=180s"], check=True,
                   stdout=subprocess.DEVNULL)


def mcp_probe_url(url: str) -> dict:
    try:
        import hashlib  # noqa: PLC0415
        sys.path.insert(0, str(REPO))
        from clients.claudecode_clickstack.mcp_smoke import handshake  # noqa: PLC0415
        r = handshake(url, timeout=30)
        tools = sorted(r["tools"])
        return {"name": r["server"].get("name"), "version": r["server"].get("version"), "tool_count": len(tools),
                "tools_sha": hashlib.sha256("\n".join(tools).encode()).hexdigest()[:12], "tools": tools}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


def mcp_probe() -> dict | None:
    """Name, version and tool list hash of the ClickStack MCP server behind the proxy, recorded per pair so that a
    server-side change (the managed server renamed a tool on 2026-11-18) is visible in the results."""
    try:
        import hashlib  # noqa: PLC0415
        sys.path.insert(0, str(REPO))
        from clients.claudecode_clickstack.mcp_smoke import handshake  # noqa: PLC0415
        # the agents reach the proxy as host.docker.internal; from the runner itself it is the Docker bridge address
        probe_url = os.environ.get("CLICKSTACK_MCP_PROBE_URL", MCP_ENDPOINT.replace("host.docker.internal", "172.17.0.1").rstrip("/") + "/mcp")
        r = handshake(probe_url, timeout=30)
        tools = sorted(r["tools"])
        return {"name": r["server"].get("name"), "version": r["server"].get("version"), "tool_count": len(tools),
                "tools_sha": hashlib.sha256("\n".join(tools).encode()).hexdigest()[:12], "tools": tools}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


def existing_result_dirs() -> set[Path]:
    return {p for p in RESULTS.glob("*") if p.is_dir() and p.name != "campaigns"}


def load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="campaign name; manifest at results/campaigns/<name>.jsonl")
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS, help="Anthropic model ids for the agent")
    ap.add_argument("--arms", nargs="+", default=DEFAULT_ARMS, help="agent names from agents.yaml")
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--problem", action="append", help="problem id (repeatable)")
    sel.add_argument("--suite", help="named suite, e.g. sregym-lite")
    ap.add_argument("--n-attempts", type=int, default=1)
    ap.add_argument("--judge-model", default="anthropic/claude-sonnet-5-5", help="one judge for every run")
    ap.add_argument("--reasoning-effort", default=None)
    ap.add_argument("--agent-timeout", type=int, default=1800)
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[], help="extra main.py args after --")
    ap.add_argument("--rerun", action="store_true", help="run pairs already completed in the manifest")
    ap.add_argument("--no-tier-gate", action="store_true", help="leave the gateway at tier 3 for every run")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    reg = registry()
    for arm in args.arms:
        if arm not in reg:
            ap.error(f"unknown agent {arm!r}; known: {sorted(reg)}")
    if not args.dry_run:
        if any(a.startswith("claudecode") for a in args.arms) and not any(os.environ.get(v) for v in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")):
            ap.error("set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (the Claude Code agent's credential)")
        if any(a.startswith("codex") for a in args.arms) and not os.environ.get("OPENAI_API_KEY"):
            ap.error("set OPENAI_API_KEY (the Codex agent's credential)")

    CAMPAIGNS.mkdir(parents=True, exist_ok=True)
    manifest_path = CAMPAIGNS / f"{args.name}.jsonl"
    # exit 2 is SREGym's EXIT_DEPLOY_EXHAUSTED: the suite ran, one or more attempts could not be deployed; count the pair as done
    done = {(r["agent"], r["model"], tuple(r.get("problems") or []), r.get("suite")) for r in load_manifest(manifest_path) if r.get("exit_code") in (0, 2)}
    pairs = [(arm, model) for model in args.models for arm in args.arms]
    if not args.rerun:
        pairs = [p for p in pairs if (p[0], p[1], tuple(args.problem or []), args.suite) not in done]

    problems = args.problem or []
    n_problems = len(problems) if problems else {"sregym-lite": 17, "sregym-lite-ia": 17}.get(args.suite, 0)
    print(f"campaign {args.name}: {len(pairs)} runs = {len(args.arms)} arms x {len(args.models)} models "
          f"(skipping {len(args.arms) * len(args.models) - len(pairs)} already done)")
    est = len(pairs) * max(n_problems, 1) * args.n_attempts * 25
    print(f"problems per run: {n_problems or args.suite}; attempts: {args.n_attempts}; rough estimate {est // 60} h {est % 60} min")
    first = True
    for arm, model in pairs:
        profile = arm_profile(reg, arm)
        cmd = [sys.executable, "main.py", "--agent", arm, "--model", model_arg(model), "--judge-model", args.judge_model,
               "--n-attempts", str(args.n_attempts), "--agent-timeout", str(args.agent_timeout),
               "--allow-agent-endpoint", MCP_ENDPOINT, "--allow-agent-endpoint", CHSQL_ENDPOINT]
        if problems:
            if len(problems) == 1:
                cmd += ["--problem", problems[0]]
            else:
                cmd += ["--problem", problems[0]]  # SREGym takes one --problem; several run as separate invocations below
        else:
            cmd += ["--suite", args.suite]
        if profile["stages"] == ["diagnosis"]:
            cmd += ["--stages", "diagnosis"]
        if arm.endswith("_obs"):  # SREGym's own Prometheus/Loki/Jaeger MCP mounts, port-forwarded on the runner; /kubectl stays unreachable
            for mount in ("prometheus", "loki", "jaeger"):
                cmd += ["--allow-agent-endpoint", f"{OBS_ENDPOINT}/{mount}"]
        if args.reasoning_effort:
            cmd += ["--reasoning-effort", args.reasoning_effort]
        if first:
            cmd.append("--force-build")
        cmd += [a for a in args.extra if a != "--"]

        problem_list = problems if problems else [None]
        for problem in problem_list:
            run_cmd = list(cmd)
            if problem is not None:
                i = run_cmd.index("--problem")
                run_cmd[i + 1] = problem
            label = f"{arm}__{model}" + (f"__{problem}" if problem and len(problem_list) > 1 else "")
            print(f"\n=== {label}\n    {' '.join(run_cmd)}")
            if args.dry_run:
                continue
            gateway_tier = 3 if (args.no_tier_gate or profile["tier"] is None) else profile["tier"]
            set_gateway_tier(gateway_tier)
            before = existing_result_dirs()
            mcp = mcp_probe()
            mcp_sql = mcp_probe_url(CHSQL_ENDPOINT.replace("host.docker.internal", "172.17.0.1") + "/mcp")
            started = dt.datetime.now(dt.timezone.utc)
            log_path = CAMPAIGNS / args.name / f"{label}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "w") as log:
                proc = subprocess.run(run_cmd, cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
            finished = dt.datetime.now(dt.timezone.utc)
            new_dirs = sorted(existing_result_dirs() - before, key=lambda p: p.stat().st_mtime)
            results_dir = next((d for d in reversed(new_dirs) if (d / arm).is_dir()), new_dirs[-1] if new_dirs else None)
            record = {
                "campaign": args.name, "agent": arm, "model": model, "tier": profile["tier"], "kubectl": profile["kubectl"],
                "stages": profile["stages"], "gateway_tier": gateway_tier, "problems": [problem] if problem else problems, "suite": args.suite,
                "n_attempts": args.n_attempts, "judge_model": args.judge_model, "reasoning_effort": args.reasoning_effort,
                "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                "duration_s": int((finished - started).total_seconds()), "exit_code": proc.returncode,
                "results_dir": str(results_dir.relative_to(REPO)) if results_dir else None, "log": str(log_path.relative_to(REPO)),
                "mcp": mcp, "mcp_sql": mcp_sql,
            }
            with open(manifest_path, "a") as mf:
                mf.write(json.dumps(record) + "\n")
            status = "ok" if proc.returncode == 0 else ("partial, deploy exhausted for some attempt" if proc.returncode == 2 else f"exit {proc.returncode}")
            print(f"    -> {status} in {record['duration_s'] // 60} min; results: {record['results_dir']}")
            first = False
            time.sleep(5)
    if not args.dry_run:
        set_gateway_tier(3)
    print(f"\nmanifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
