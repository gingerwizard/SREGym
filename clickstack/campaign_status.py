#!/usr/bin/env python3
"""One-screen status of a campaign: finished pairs from the manifest, the pair in progress
(attempts done, current problem) and a time estimate from the attempts completed so far.

  python3 clickstack/campaign_status.py lite-sweep
  python3 clickstack/campaign_status.py lite-sweep --pairs 18   # when ~/campaign-<name>.log is not there
"""
import argparse
import csv
from collections import Counter
import datetime as dt
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"


def load_manifest(name: str) -> list[dict]:
    path = RESULTS / "campaigns" / f"{name}.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def count_rows(path: Path) -> int:
    with open(path, newline="") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def pair_progress(results_dir: Path, agent: str, n_attempts: int) -> tuple[int, int, int, tuple[str, int] | None]:
    """(complete attempts, incomplete attempts, problems with every attempt complete, current (problem, attempt)).

    SREGym writes a cumulative snapshot of the run's results table into each problem's CSV (a _running_ copy while
    the problem is open), so rows are deduplicated by (problem, attempt), latest file winning."""
    base = results_dir / agent
    if not base.is_dir():
        return 0, 0, 0, None
    latest: dict[tuple[str, int], dict] = {}
    for c in sorted(base.glob("*/*_results.csv"), key=lambda q: q.stat().st_mtime):
        with open(c, newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    n = int(row.get("attempt") or 1)
                except ValueError:
                    n = 1
                latest[(row.get("problem_id") or c.parent.name, n)] = row
    complete = sum(1 for r in latest.values() if r.get("run_status") == "complete")
    per_problem = Counter(p for (p, _n), r in latest.items() if r.get("run_status") == "complete")
    finished = sum(1 for c in per_problem.values() if c >= n_attempts)
    run_dirs = sorted(base.glob("*/run_*"), key=lambda q: q.stat().st_mtime)
    current = None
    if run_dirs:
        d = run_dirs[-1]
        tail = d.name.rsplit("_", 1)[-1]
        current = (d.parent.name, int(tail) if tail.isdigit() else 0)
    return complete, len(latest) - complete, finished, current


def log_start(log: Path) -> dt.datetime:
    """Start time of a per-pair run from the first timestamp in its log ([MM/DD/YY HH:MM:SS], runner clock is UTC)."""
    with open(log, errors="replace") as fh:
        for line in fh:
            m = re.match(r"\[(\d\d/\d\d/\d\d \d\d:\d\d:\d\d)\]", line)
            if m:
                return dt.datetime.strptime(m.group(1), "%m/%d/%y %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    return dt.datetime.fromtimestamp(log.stat().st_mtime, dt.timezone.utc)


def fmt_h(seconds: float) -> str:
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", default="lite-sweep")
    ap.add_argument("--pairs", type=int, default=None, help="total (arm, model) pairs in the campaign; read from ~/campaign-<name>.log when absent")
    ap.add_argument("--problems", type=int, default=None, help="problems per pair (default: from the manifest or 17 for sregym-lite)")
    args = ap.parse_args()

    records = load_manifest(args.name)
    latest = {}
    for r in records:
        latest[(r["agent"], r["model"])] = r
    ok = [r for r in latest.values() if r.get("exit_code") in (0, 2)]  # 2: SREGym ran the suite but could not deploy every attempt
    bad = [r for r in latest.values() if r.get("exit_code") not in (0, 2)]
    running = subprocess.run(["pgrep", "-f", r"clickstack/campaign[.]py --name"], capture_output=True, text=True).stdout.strip() != ""
    print(f"campaign {args.name}: {len(ok)} pairs finished, {len(bad)} failed, campaign {'running' if running else 'not running'}"
          + ("  (part = some attempt could not be deployed; results kept)" if any(r.get("exit_code") == 2 for r in ok) else ""))

    completed_attempts = 0
    for r in sorted(latest.values(), key=lambda r: (r["model"], r["agent"])):
        status = "ok  " if r.get("exit_code") == 0 else ("part" if r.get("exit_code") == 2 else f"exit {r.get('exit_code')}")
        n = pair_progress(REPO / r["results_dir"], r["agent"], r.get("n_attempts") or 1)[0] if r.get("results_dir") else 0
        completed_attempts += n
        print(f"  {status} {r['model']:18s} {r['agent']:34s} {r.get('duration_s', 0) // 60:4d} min  {n:3d} attempts  {r.get('results_dir') or ''}")

    # the pair in progress: newest per-pair log that has no manifest record yet
    logs = sorted((RESULTS / "campaigns" / args.name).glob("*.log"), key=lambda p: p.stat().st_mtime) if (RESULTS / "campaigns" / args.name).is_dir() else []
    finished = {}
    for r in records:
        if r.get("finished_at"):
            finished[r.get("log")] = max(finished.get(r.get("log"), 0.0), dt.datetime.fromisoformat(r["finished_at"]).timestamp())
    current = next((l for l in reversed(logs) if l.stat().st_mtime > finished.get(str(l.relative_to(REPO)), 0.0) + 60), None)
    n_problems = args.problems or next((len(r["problems"]) for r in records if r.get("problems")), None) or 17
    n_attempts = next((r.get("n_attempts") for r in records if r.get("n_attempts")), 3)
    if running and current:
        arm, _, model = current.stem.partition("__")
        known = {Path(r["results_dir"]).name for r in records if r.get("results_dir")}
        dirs = sorted((d for d in RESULTS.iterdir() if d.is_dir() and d.name != "campaigns" and d.name not in known and (d / arm).is_dir()), key=lambda d: d.stat().st_mtime)
        if dirs:
            rd = dirs[-1]
            done, incomplete, finished, cur = pair_progress(rd, arm, n_attempts)
            completed_attempts += done
            started = log_start(current)
            elapsed = (dt.datetime.now(dt.timezone.utc) - started).total_seconds()
            print(f"\nin progress: {model} x {arm} since {started:%H:%M} UTC ({fmt_h(elapsed)} ago), results {rd.relative_to(REPO)}")
            print(f"  {done}/{n_problems * n_attempts} attempts complete" + (f" ({incomplete} incomplete, retried at the end)" if incomplete else "") + f", {finished}/{n_problems} problems finished")
            if cur:
                print(f"  current problem: {cur[0]}  (attempt {cur[1]} of {n_attempts})")
            if done:
                per = elapsed / done
                print(f"  mean {per / 60:.1f} min per attempt in this pair; pair should finish in about {fmt_h(per * (n_problems * n_attempts - done))}")

    # whole-campaign estimate
    pairs = args.pairs
    log_home = Path.home() / f"campaign-{args.name}.log"
    if pairs is None and log_home.exists():
        # the launch line reads "campaign X: N runs = ... (skipping K already done)"; total pairs = N + K
        m = re.search(r"campaign \S+: (\d+) runs = .*?\(skipping (\d+) already done\)", log_home.read_text(errors="replace"))
        pairs = int(m.group(1)) + int(m.group(2)) if m else None
    starts = [dt.datetime.fromisoformat(r["started_at"]) for r in records if r.get("started_at")]
    if current and running:
        starts.append(log_start(current))
    if pairs and starts and completed_attempts:
        total = pairs * n_problems * n_attempts
        elapsed = (dt.datetime.now(dt.timezone.utc) - min(starts)).total_seconds()
        per = elapsed / completed_attempts
        print(f"\ncampaign: {completed_attempts}/{total} attempts in {fmt_h(elapsed)} ({per / 60:.1f} min per attempt); "
              f"remaining about {fmt_h(per * (total - completed_attempts))}, finishing around {(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=per * (total - completed_attempts))):%a %d %b %H:%M} UTC")


if __name__ == "__main__":
    main()
