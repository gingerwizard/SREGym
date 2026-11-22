#!/usr/bin/env python3
"""Which problems are saturated (solved by essentially every model and arm) and which still discriminate.

A problem is saturated when, over the scored attempts in scope, its overall pass rate is at least
--threshold (default 0.95) and no (model, arm) cell falls below --min-cell (default 0.67, i.e. at most one
failed attempt out of three in any cell). The report applies the same definition behind its
"hide saturated problems" switch.

  uv run python clickstack/saturation.py --campaign lite-sweep --campaign lite-sweep-fill
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import in_progress_runs, load_attempts, load_manifests  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
from report import FAMILY_ORDER as FAMS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", default=str(REPO / "results"))
    ap.add_argument("--campaign", action="append", default=None, help="repeatable; default lite-sweep and lite-sweep-fill")
    ap.add_argument("--harness", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--arms", default=None, help="comma-separated arm families to judge over, e.g. stock,kubectl_clickstack")
    ap.add_argument("--threshold", type=float, default=0.95, help="overall pass rate at or above which a problem counts as saturated")
    ap.add_argument("--min-cell", type=float, default=0.67, help="every (model, arm) cell must pass at least this share of its attempts")
    args = ap.parse_args()
    root = Path(args.results_root)
    campaigns = args.campaign or ["lite-sweep", "lite-sweep-fill"]
    runs = []
    for c in campaigns:
        rs = [r for r in load_manifests(root, c) if r.get("exit_code") in (0, 2, None) and r.get("results_dir")]
        runs += rs + in_progress_runs(root, rs, c)
    arms = set(args.arms.split(",")) if args.arms else None
    attempts = [a for a in load_attempts(root, runs) if a["diagnosis"] is not None and (not args.harness or a["harness"] == args.harness) and (not args.model or a["model"] == args.model) and (not arms or a["family"] in arms)]

    prob: dict[str, dict] = defaultdict(lambda: {"p": 0, "n": 0, "cells": defaultdict(lambda: [0, 0]), "fam": defaultdict(lambda: [0, 0]), "ttd": []})
    for a in attempts:
        d = prob[a["problem"]]
        d["p"] += bool(a["diagnosis"]); d["n"] += 1
        d["cells"][(a["model"], a["family"])][0] += bool(a["diagnosis"]); d["cells"][(a["model"], a["family"])][1] += 1
        d["fam"][a["family"]][0] += bool(a["diagnosis"]); d["fam"][a["family"]][1] += 1
        if a["diagnosis"] and a["ttd_s"]:
            d["ttd"].append(a["ttd_s"])
    fams = [f for f in FAMS if any(f in d["fam"] for d in prob.values())]
    head = f"{'problem':52s} {'overall':>12s} {'weakest cell':>12s} {'cells<100%':>10s} " + " ".join(f"{f[:10]:>10s}" for f in fams) + f" {'TTD':>6s}  verdict"
    print(f"{len(attempts)} scored attempts in {', '.join(campaigns)}; saturated = overall >= {args.threshold:.0%} and every cell >= {args.min_cell:.0%}\n")
    print(head)
    saturated, open_ = [], []
    for p in sorted(prob, key=lambda p: -prob[p]["p"] / prob[p]["n"]):
        d = prob[p]
        rate = d["p"] / d["n"]
        weakest = min(c[0] / c[1] for c in d["cells"].values())
        imperfect = sum(1 for c in d["cells"].values() if c[0] < c[1])
        sat = rate >= args.threshold and weakest >= args.min_cell
        (saturated if sat else open_).append(p)
        cols = " ".join((f"{100 * d['fam'][f][0] / d['fam'][f][1]:9.0f}%" if d["fam"].get(f) and d["fam"][f][1] else f"{'-':>10s}") for f in fams)
        ttd = f"{sum(d['ttd']) / len(d['ttd']) / 60:5.1f}m" if d["ttd"] else "     -"
        print(f"{p:52s} {100 * rate:5.0f}% {d['p']:3d}/{d['n']:<3d} {100 * weakest:11.0f}% {imperfect:5d}/{len(d['cells']):<4d} {cols} {ttd}  {'SATURATED' if sat else 'open'}")
    print(f"\nsaturated ({len(saturated)}): " + ", ".join(saturated))
    print(f"open ({len(open_)}): " + ", ".join(open_))
    return 0


if __name__ == "__main__":
    sys.exit(main())
