#!/usr/bin/env python3
"""Take a campaign's records for arms matching a pattern out of the manifest, together with their results and logs.

First used on 21 November 2026 to drop every ClickHouse-SQL arm run on the fleet while the SQL MCP server was
refusing every query (see clickstack/chsql_mcp_main.py).

  uv run python clickstack/fleet/invalidate.py --campaign phase1 --agent-pattern chsql --reason "..." [--clusters c3 c4] [--dry-run]

Matching records move to results/campaigns/<campaign>.invalid-<tag>.bak (a file the report and collect.sh ignore),
their results directories and per-pair logs to results/invalid/. Results directories and logs that match the
pattern but have no record (a pair killed mid-run) move too. On the primary, --clusters restricts the records to
those collected from the named clusters; without it every matching record is taken.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "results"


def move(path: Path, invalid_dir: Path) -> None:
    dest = invalid_dir / path.relative_to(RESULTS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
    shutil.move(str(path), str(dest))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--agent-pattern", required=True, help="regular expression matched against the agent name")
    ap.add_argument("--clusters", nargs="*", default=None, help="only records collected from these clusters")
    ap.add_argument("--tag", default="sql")
    ap.add_argument("--reason", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    pattern = re.compile(args.agent_pattern)
    manifest = RESULTS / "campaigns" / f"{args.campaign}.jsonl"
    keep, drop = [], []
    for line in manifest.read_text().splitlines() if manifest.exists() else []:
        if not line.strip():
            continue
        r = json.loads(line)
        match = pattern.search(r.get("agent", "")) and (args.clusters is None or r.get("cluster") in args.clusters)
        (drop if match else keep).append(r)
    # Anything any manifest still refers to (this campaign's kept records, every other campaign, the collected
    # copies of other clusters' manifests) stays where it is.
    referenced = {r.get("results_dir") for r in keep} | {r.get("log") for r in keep}
    for mf in list((RESULTS / "campaigns").glob("*.jsonl")) + list(RESULTS.glob("*/campaigns/*.jsonl")):
        if mf == manifest:
            continue
        for line in mf.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                referenced |= {r.get("results_dir"), r.get("log")}
                # a cluster's own manifest names its paths as results/<MMDD_HHMM>; the collected copy lives under results/<cluster>/
                cluster = mf.parent.parent.name if mf.parent.parent != RESULTS else None
                if cluster:
                    referenced |= {f"results/{cluster}/" + v.split("results/", 1)[1] for v in (r.get("results_dir"), r.get("log")) if v}
    # Orphans: results directories (results/<MMDD_HHMM>/<agent>, or results/<cluster>/<MMDD_HHMM>/<agent>) and per-pair
    # logs that match the pattern and no kept record refers to.
    roots = [RESULTS] + ([RESULTS / c for c in args.clusters] if args.clusters else [])
    orphans = []
    for root in roots:
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if d.is_dir() and re.fullmatch(r"\d{4}_\d{4}", d.name) and str(d.relative_to(REPO)) not in referenced \
                    and any(sub.is_dir() and pattern.search(sub.name) for sub in d.iterdir()):
                orphans.append(d)
        camp_dir = root / "campaigns" / args.campaign
        if camp_dir.is_dir():
            for log in camp_dir.glob("*.log"):
                if pattern.search(log.stem.partition("__")[0]) and str(log.relative_to(REPO)) not in referenced:
                    orphans.append(log)
    print(f"{len(drop)} record(s) to invalidate, {len(keep)} kept, {len(orphans)} unrecorded path(s)")
    for r in drop:
        print(f"  record  {r.get('cluster', 'local')} {r['agent']} x {r['model']} {r.get('results_dir')} exit={r.get('exit_code')}")
    for p in orphans:
        print(f"  orphan  {p.relative_to(REPO)}")
    if args.dry_run:
        return 0
    invalid_dir = RESULTS / "invalid"
    invalid_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if drop:
        with open(RESULTS / "campaigns" / f"{args.campaign}.invalid-{args.tag}.bak", "a") as out:
            for r in drop:
                out.write(json.dumps({**r, "invalidated_at": stamp, "invalid_reason": args.reason}) + "\n")
        manifest.write_text("".join(json.dumps(r) + "\n" for r in keep))
    for r in drop:
        for key in ("results_dir", "log"):
            if r.get(key) and (REPO / r[key]).exists():
                move(REPO / r[key], invalid_dir)
    for p in orphans:
        if p.exists():
            move(p, invalid_dir)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
