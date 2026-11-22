#!/usr/bin/env python3
"""Load the campaign results into ClickHouse: one row per attempt, one row per tool call, plus the problem sets.

  uv run python clickstack/load_results.py                      # every campaign under results/campaigns
  uv run python clickstack/load_results.py --campaign phase1 phase2
  uv run python clickstack/load_results.py --ddl-only           # create the database, tables and views, grant SELECT

Reads the same manifests and per-attempt files as clickstack/report.py (run clickstack/fleet/collect.sh first on the
primary so every cluster's results are present). Credentials come from ~/.config/sregym/clickstack.env
(CLICKHOUSE_ENDPOINT, CLICKHOUSE_ADMIN_USER, CLICKHOUSE_ADMIN_PASSWORD). Tables are ReplacingMergeTree keyed by the
attempt's run directory, so loading again replaces rows rather than duplicating them; query them with FINAL or
through the views, which already do. The read-only user the SQL arms use (clickstack_readonly) is granted SELECT,
so the report's SQL tab can query the database with it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from clickstack.report import in_progress_runs, load_attempts, load_manifests, problem_sets  # noqa: E402

ENV_FILE = Path(os.environ.get("CLICKSTACK_ENV_FILE", Path.home() / ".config" / "sregym" / "clickstack.env"))
READONLY_USER = "clickstack_readonly"

DDL = """
CREATE DATABASE IF NOT EXISTS {db};
CREATE TABLE IF NOT EXISTS {db}.attempts (
    loaded_at DateTime DEFAULT now(),
    campaign LowCardinality(String), cluster LowCardinality(String), run_started_at Nullable(DateTime64(3)), run_dir String,
    model LowCardinality(String), harness LowCardinality(String), agent LowCardinality(String), family LowCardinality(String),
    tier Nullable(UInt8), kubectl Bool, problem LowCardinality(String), attempt UInt8, in_progress Bool,
    diagnosis Nullable(Bool), composite Nullable(Float32), mitigation Nullable(Bool), mode LowCardinality(String),
    ttd_s Nullable(Float32), ttm_s Nullable(Float32),
    tokens_total Nullable(UInt64), tokens_input Nullable(UInt64), tokens_output Nullable(UInt64), tokens_cached Nullable(UInt64),
    cost_usd Nullable(Float32), agent_wall_s Nullable(Float32), turns Nullable(UInt32),
    n_calls UInt32, n_mcp UInt32, n_kubectl UInt32, n_shell UInt32, n_sql UInt32, n_errors UInt32, n_probe UInt32, n_fix UInt32, n_scripted UInt32,
    tools Map(String, UInt32), tool_err Map(String, UInt32), kverbs Map(String, UInt32), areas Map(String, UInt32),
    checks Map(String, String), dims Map(String, Float32),
    run_status LowCardinality(String), timed_out Nullable(Bool), deploy_failed Nullable(Bool), incomplete_reason String, unscored String,
    blocked_requests Nullable(UInt32), skill_read Nullable(Bool), mcp_version LowCardinality(String), judge_model LowCardinality(String),
    fault String, submission String, judge_reasoning String
) ENGINE = ReplacingMergeTree(loaded_at) ORDER BY (campaign, agent, model, problem, attempt, run_dir);
CREATE TABLE IF NOT EXISTS {db}.tool_calls (
    loaded_at DateTime DEFAULT now(),
    campaign LowCardinality(String), cluster LowCardinality(String), run_dir String,
    model LowCardinality(String), harness LowCardinality(String), agent LowCardinality(String), family LowCardinality(String),
    problem LowCardinality(String), attempt UInt8, diagnosis Nullable(Bool),
    i UInt32, ts Nullable(DateTime64(3)), kind LowCardinality(String), tool LowCardinality(String), area LowCardinality(String),
    args String, sql String, kubectl_tokens Array(String), probe Bool, scripted Bool, error Bool, error_text String,
    dur_s Nullable(Float32), pre_text String
) ENGINE = ReplacingMergeTree(loaded_at) ORDER BY (campaign, agent, model, problem, attempt, run_dir, i);
CREATE TABLE IF NOT EXISTS {db}.problem_sets (
    set_name LowCardinality(String), problem LowCardinality(String)
) ENGINE = ReplacingMergeTree ORDER BY (set_name, problem);
CREATE OR REPLACE VIEW {db}.attempts_scored AS
    SELECT * FROM {db}.attempts FINAL WHERE diagnosis IS NOT NULL AND NOT in_progress;
CREATE OR REPLACE VIEW {db}.pass_rates AS
    SELECT campaign, model, harness, family, agent,
           count() AS attempts, countIf(diagnosis) AS passes, round(countIf(diagnosis) / count(), 3) AS pass1,
           countIf(mitigation = true) AS fixes, countIf(mitigation IS NOT NULL) AS fix_attempts,
           round(avgIf(ttd_s, diagnosis)) AS ttd_when_correct_s, round(avg(tokens_total)) AS mean_tokens,
           round(avg(n_mcp), 1) AS mcp_per_attempt, round(avg(n_kubectl), 1) AS kubectl_per_attempt,
           round(sum(n_errors) / greatest(sum(n_calls), 1), 3) AS error_share
    FROM {db}.attempts_scored GROUP BY campaign, model, harness, family, agent;
CREATE OR REPLACE VIEW {db}.cells AS
    SELECT model, family, problem, count() AS attempts, countIf(diagnosis) AS passes, max(diagnosis) AS any_pass
    FROM {db}.attempts_scored GROUP BY model, family, problem;
GRANT SELECT ON {db}.* TO {ro};
"""


def read_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip().removeprefix("export ").strip()] = v.strip().strip("'\"")
    return out


class ClickHouse:
    def __init__(self, endpoint: str, user: str, password: str):
        import httpx  # noqa: PLC0415  (kept local so report.py can import the DDL without the dependency)

        self.endpoint = endpoint.rstrip("/")
        self.client = httpx.Client(auth=(user, password), timeout=300)

    def query(self, sql: str, data: bytes | None = None, **settings: str) -> str:
        params = {"date_time_input_format": "best_effort", **settings}
        if data is None:
            r = self.client.post(f"{self.endpoint}/", params=params, content=sql.encode())
        else:
            r = self.client.post(f"{self.endpoint}/", params={**params, "query": sql}, content=data)
        if r.status_code != 200:
            raise RuntimeError(f"ClickHouse HTTP {r.status_code}: {r.text[:500]}")
        return r.text

    def insert_rows(self, table: str, rows: list[dict], batch: int = 2000) -> int:
        n = 0
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            body = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in chunk).encode()
            self.query(f"INSERT INTO {table} FORMAT JSONEachRow", data=body)
            n += len(chunk)
        return n


def ts_text(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def as_int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def attempt_row(a: dict) -> dict:
    tok = a.get("tokens") or {}
    cached = (tok.get("cached_input_tokens") or 0) + (tok.get("cache_creation_input_tokens") or 0) if tok else None
    total = tok.get("total_tokens") if tok else None
    if tok and total is None:
        total = (tok.get("input_tokens") or 0) + (tok.get("output_tokens") or 0)
    return {
        "campaign": a.get("campaign") or "", "cluster": a.get("cluster") or "c1", "run_started_at": ts_text(a.get("run_started_at")), "run_dir": a.get("run_dir") or "",
        "model": a["model"], "harness": a["harness"], "agent": a["agent"], "family": a["family"],
        "tier": as_int(a.get("tier")), "kubectl": bool(a.get("kubectl")), "problem": a["problem"], "attempt": int(a["attempt"]), "in_progress": bool(a.get("in_progress")),
        "diagnosis": a.get("diagnosis"), "composite": a.get("composite"), "mitigation": a.get("mitigation"), "mode": a.get("mode") or "",
        "ttd_s": a.get("ttd_s"), "ttm_s": a.get("ttm_s"),
        "tokens_total": as_int(total), "tokens_input": as_int(tok.get("input_tokens")) if tok else None,
        "tokens_output": as_int(tok.get("output_tokens")) if tok else None, "tokens_cached": as_int(cached) if tok else None,
        "cost_usd": a.get("cost_usd"), "agent_wall_s": a.get("agent_wall_s"), "turns": as_int(a.get("turns")),
        "n_calls": a["n_mcp"] + a["n_kubectl"] + a["n_shell"], "n_mcp": a["n_mcp"], "n_kubectl": a["n_kubectl"], "n_shell": a["n_shell"],
        "n_sql": a["n_sql"], "n_errors": a["n_errors"], "n_probe": a.get("n_probe") or 0, "n_fix": a.get("n_fix") or 0, "n_scripted": a.get("n_scripted") or 0,
        "tools": a.get("tools") or {}, "tool_err": a.get("tool_err") or {}, "kverbs": a.get("kverbs") or {}, "areas": a.get("areas") or {},
        "checks": a.get("checks") or {}, "dims": {k: float(v) for k, v in (a.get("dims") or {}).items()},
        "run_status": a.get("run_status") or "", "timed_out": a.get("timed_out"), "deploy_failed": a.get("deploy_failed"),
        "incomplete_reason": a.get("incomplete_reason") or "", "unscored": a.get("unscored") or "",
        "blocked_requests": as_int(a.get("blocked_requests")), "skill_read": a.get("skill_read"), "mcp_version": a.get("mcp_version") or "",
        "judge_model": a.get("judge_model") or "", "fault": a.get("fault") or "", "submission": a.get("submission") or "", "judge_reasoning": a.get("judge_reasoning") or "",
    }


def call_rows(a: dict) -> list[dict]:
    base = {"campaign": a.get("campaign") or "", "cluster": a.get("cluster") or "c1", "run_dir": a.get("run_dir") or "", "model": a["model"], "harness": a["harness"],
            "agent": a["agent"], "family": a["family"], "problem": a["problem"], "attempt": int(a["attempt"]), "diagnosis": a.get("diagnosis")}
    out = []
    for s in a.get("steps") or []:
        ts = s.get("ts")
        ts_text_value = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if isinstance(ts, (int, float)) else None
        out.append({**base, "i": s["i"], "ts": ts_text_value, "kind": s["kind"], "tool": s["tool"], "area": s.get("area") or "",
                    "args": s.get("args") or "", "sql": s.get("sql") or "", "kubectl_tokens": s.get("kv") or [], "probe": bool(s.get("probe")),
                    "scripted": bool(s.get("scripted")), "error": bool(s.get("error")), "error_text": s.get("error_text") or "",
                    "dur_s": s.get("dur_s"), "pre_text": s.get("pre_text") or ""})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", default=str(REPO / "results"))
    ap.add_argument("--campaign", nargs="*", default=None)
    ap.add_argument("--database", default=os.environ.get("SREGYM_RESULTS_DB", "sregym_results"))
    ap.add_argument("--ddl-only", action="store_true")
    ap.add_argument("--truncate", action="store_true", help="empty the tables before loading")
    ap.add_argument("--skip-calls", action="store_true", help="load attempts and problem sets only")
    args = ap.parse_args()
    env = read_env(ENV_FILE)
    ch = ClickHouse(env["CLICKHOUSE_ENDPOINT"], env["CLICKHOUSE_ADMIN_USER"], env["CLICKHOUSE_ADMIN_PASSWORD"])
    db = args.database
    for stmt in DDL.format(db=db, ro=READONLY_USER).split(";"):
        if stmt.strip():
            ch.query(stmt.strip())
    print(f"schema ready in {db}")
    if args.ddl_only:
        return 0
    if args.truncate:
        for t in ("attempts", "tool_calls", "problem_sets"):
            ch.query(f"TRUNCATE TABLE {db}.{t}")
    root = Path(args.results_root)
    runs = load_manifests(root, args.campaign)
    latest = {}
    for r in runs:
        latest[(r.get("campaign"), r["agent"], r["model"], tuple(r.get("problems") or []), r.get("suite"))] = r
    runs = [r for r in runs if r.get("exit_code") in (0, 2, None) and r.get("results_dir")]
    live = in_progress_runs(root, runs, args.campaign)
    attempts = load_attempts(root, runs + live)
    n_att = ch.insert_rows(f"{db}.attempts", [attempt_row(a) for a in attempts])
    n_calls = 0 if args.skip_calls else ch.insert_rows(f"{db}.tool_calls", [row for a in attempts for row in call_rows(a)])
    sets = [{"set_name": k, "problem": p} for k, v in problem_sets().items() for p in v]
    n_sets = ch.insert_rows(f"{db}.problem_sets", sets)
    print(f"loaded {n_att} attempts, {n_calls} tool calls, {n_sets} problem-set rows into {db} ({len(runs)} runs, {len(live)} in progress)")
    print(ch.query(f"SELECT campaign, count() AS attempts, countIf(diagnosis) AS passes FROM {db}.attempts FINAL GROUP BY campaign ORDER BY campaign FORMAT PrettyCompactNoEscapes"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
