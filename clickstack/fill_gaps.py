#!/usr/bin/env python3
"""Re-run the attempts a campaign is missing: for every (arm, model, problem) with fewer complete attempts
than the campaign's n_attempts (deploy failures, agent exits, timeouts), run the missing number under the
campaign name <name>-fill. The report merges both campaigns when both are selected.

  python3 clickstack/fill_gaps.py lite-sweep --dry-run
  nohup python3 clickstack/fill_gaps.py lite-sweep --wait > ~/campaign-lite-sweep-fill.log 2>&1 &
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def complete_counts(results_dir: Path, agent: str) -> Counter:
    latest = {}
    for f in sorted((results_dir / agent).glob("*/*_results.csv"), key=lambda p: p.stat().st_mtime):
        with open(f, newline="") as fh:
            for row in csv.DictReader(fh):
                latest[(row.get("problem_id") or f.parent.name, row.get("attempt"))] = row
    return Counter(p for (p, _a), r in latest.items() if r.get("run_status") == "complete")


def gaps(name: str) -> list[tuple[str, str, str, int]]:
    from sregym.conductor.problem_sets import PROBLEM_SETS  # noqa: PLC0415
    manifest = REPO / "results" / "campaigns" / f"{name}.jsonl"
    fill = REPO / "results" / "campaigns" / f"{name}-fill.jsonl"
    records = [json.loads(l) for l in manifest.read_text().splitlines() if l.strip()] if manifest.exists() else []
    filled = Counter()
    for l in (fill.read_text().splitlines() if fill.exists() else []):
        r = json.loads(l)
        if r.get("exit_code") in (0, 2) and r.get("results_dir"):
            for problem, n in complete_counts(REPO / r["results_dir"], r["agent"]).items():
                filled[(r["agent"], r["model"], problem)] += n
    out = []
    for r in records:
        if r.get("exit_code") not in (0, 2) or not r.get("results_dir"):
            continue
        problems = r.get("problems") or list(PROBLEM_SETS[r["suite"]])
        have = complete_counts(REPO / r["results_dir"], r["agent"])
        for problem in problems:
            missing = (r.get("n_attempts") or 1) - have.get(problem, 0) - filled.get((r["agent"], r["model"], problem), 0)
            if missing > 0:
                out.append((r["agent"], r["model"], problem, missing))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", default="lite-sweep")
    ap.add_argument("--wait", action="store_true", help="wait until no campaign.py or queue_codex.sh is running before starting")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    if args.wait:
        print(f"[{time.strftime('%FT%TZ', time.gmtime())}] waiting for the campaign and the Codex queue to finish")
        while subprocess.run(["pgrep", "-f", r"clickstack/campaign[.]py --name|clickstack/queue_codex[.]sh"], capture_output=True).returncode == 0:
            time.sleep(300)
        print(f"[{time.strftime('%FT%TZ', time.gmtime())}] nothing running; computing gaps")
    todo = gaps(args.name)
    print(f"{len(todo)} gap(s), {sum(m for *_, m in todo)} attempt(s) to run")
    for agent, model, problem, missing in todo:
        print(f"  {agent} x {model}: {problem} needs {missing}")
    if args.dry_run:
        return 0
    for agent, model, problem, missing in todo:
        cmd = [sys.executable, str(REPO / "clickstack" / "campaign.py"), "--name", f"{args.name}-fill", "--problem", problem,
               "--n-attempts", str(missing), "--models", model, "--arms", agent, "--rerun"]
        print(f"\n>>> {' '.join(cmd)}")
        subprocess.run(cmd, cwd=REPO, env=os.environ)
    return 0


if __name__ == "__main__":
    sys.exit(main())
