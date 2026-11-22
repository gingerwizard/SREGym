#!/usr/bin/env python3
"""Write the phase 2 queue files: every arm on the 54 non-Lite problems, in three 18-problem chunks.

Lines are chunk-major (every pair finishes chunk a before any pair starts chunk b) so the report holds a complete
35-problem picture early, and within a chunk the arms that discriminate most come first: the stock reference,
then telemetry-only tiers 3 and 4, SQL-only tiers 3 and 4, stock telemetry without kubectl, tier 0, tiers 1 and 2,
and the kubectl-plus-telemetry arms last. Pairs are spread over the clusters by estimated hours; a cluster that
frees up earlier (--head-start cluster=hours) takes more.

  uv run python clickstack/fleet/plan_phase2.py --out ~/fleet/phase2 [--head-start c1=4 c2=6]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODELS = {"claudecode": ["claude-opus-5-5", "claude-fable-5-2"], "codex": ["gpt-6.1-sol"]}
CHUNKS = ["sregym-71-rest-a", "sregym-71-rest-b", "sregym-71-rest-c"]
# arm suffix (after the harness prefix) in priority order
PRIORITY = ["", "_clickstack_only_t3", "_clickstack_only_t4", "_chsql_only_t3", "_chsql_only_t4", "_obs", "_clickstack_only_t0", "_chsql_only_t0",
            "_clickstack_only_t1", "_clickstack_only_t2", "_chsql_only_t1", "_chsql_only_t2",
            "_clickstack", "_clickstack_t3", "_clickstack_t4", "_chsql", "_chsql_t3", "_chsql_t4"]
# hours per 18-problem chunk (3 attempts), from the Lite pairs: ~5 min an attempt for Codex, ~6.5 for Claude without kubectl
HOURS = {"codex": 4.5, "claudecode": 5.5}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign", default="phase2")
    ap.add_argument("--n-attempts", type=int, default=3)
    ap.add_argument("--clusters", default=str(REPO / "clickstack" / "fleet" / "clusters.json"))
    ap.add_argument("--head-start", nargs="*", default=[], help="cluster=hours it is free before the others")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    clusters = list(json.loads(Path(args.clusters).read_text()))
    head = {c: 0.0 for c in clusters}
    for item in args.head_start:
        c, _, h = item.partition("=")
        head[c] = float(h)
    load = {c: -head[c] for c in clusters}
    queues = {c: [] for c in clusters}
    for chunk in CHUNKS:
        for suffix in PRIORITY:
            for harness, models in MODELS.items():
                arm = harness + suffix
                for model in models:
                    target = min(clusters, key=lambda c: load[c])
                    queues[target].append(f"{args.campaign} {chunk} {args.n_attempts} {model} {arm}")
                    load[target] += HOURS[harness] * (1.2 if "_only_" in arm or arm.endswith("_obs") else 1.0)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    for c in clusters:
        (out / f"{c}.queue").write_text("\n".join(queues[c]) + "\n")
        print(f"{c}: {len(queues[c])} lines, {load[c] + head[c]:.0f} h of work")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
