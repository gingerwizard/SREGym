#!/usr/bin/env python3
"""List attempts that did not complete in a campaign's finished pairs (deploy failures, timeouts, unscored).

  python3 clickstack/incomplete_attempts.py lite-sweep
"""
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
name = sys.argv[1] if len(sys.argv) > 1 else "lite-sweep"
manifest = REPO / "results" / "campaigns" / f"{name}.jsonl"
records = [json.loads(l) for l in manifest.read_text().splitlines() if l.strip()] if manifest.exists() else []
for r in records:
    if not r.get("results_dir"):
        continue
    base = REPO / r["results_dir"] / r["agent"]
    latest = {}
    for f in sorted(base.glob("*/*_results.csv"), key=lambda p: p.stat().st_mtime):
        with open(f, newline="") as fh:
            for row in csv.DictReader(fh):
                latest[(row.get("problem_id") or f.parent.name, row.get("attempt"))] = row
    bad = [(k, row) for k, row in sorted(latest.items()) if row.get("run_status") != "complete"]
    print(f"{r['agent']} x {r['model']}: exit {r.get('exit_code')}, {len(latest)} rows, {len(bad)} not complete")
    for (problem, attempt), row in bad:
        detail = {c: (row.get(c) or "")[:70] for c in ("run_status", "incomplete_reason", "deploy_failed", "deploy_attempts", "timed_out", "Diagnosis.unscored_reason")}
        print(f"    {problem} attempt {attempt}: " + ", ".join(f"{k}={v}" for k, v in detail.items() if v))
