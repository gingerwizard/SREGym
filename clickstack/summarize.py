#!/usr/bin/env python3
"""Print campaign results as text tables, from the same loaders as the report.

  uv run python clickstack/summarize.py --campaign lite-sweep --harness claudecode
  uv run python clickstack/summarize.py --campaign lite-sweep --problems      # per-problem view
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import FAMILY_ORDER, in_progress_runs, load_attempts, load_manifests  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", default=str(REPO / "results"))
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--harness", default=None, help="claudecode or codex")
    ap.add_argument("--problems", action="store_true", help="per-problem pass rates, kubectl arms against ClickStack-only arms")
    args = ap.parse_args()
    root = Path(args.results_root)
    runs = load_manifests(root, args.campaign)
    runs = [r for r in runs if r.get("exit_code") in (0, 2, None) and r.get("results_dir")]
    runs += in_progress_runs(root, runs, args.campaign)
    attempts = load_attempts(root, runs)
    if args.harness:
        attempts = [a for a in attempts if a["harness"] == args.harness]
    scored = [a for a in attempts if a["diagnosis"] is not None]
    print(f"{len(attempts)} attempts, {len(scored)} scored, {len(attempts) - len(scored)} unfinished or not deployed")

    if args.problems:
        prob = defaultdict(lambda: [0, 0, 0, 0])
        for a in scored:
            i = 0 if a["family"] in ("stock", "kubectl_clickstack") else 2
            prob[a["problem"]][i] += bool(a["diagnosis"])
            prob[a["problem"]][i + 1] += 1
        print(f"\n{'problem':55s} {'kubectl arms':>14s}   {'ClickStack-only':>16s}")
        for p in sorted(prob, key=lambda p: prob[p][2] / max(prob[p][3], 1)):
            k, kn, o, on = prob[p]
            print(f"{p:55s} {k:3d}/{kn:<3d} {100 * k / max(kn, 1):3.0f}%   {o:3d}/{on:<3d} {100 * o / max(on, 1):3.0f}%")
        return 0

    by = defaultdict(lambda: dict(p=0, n=0, t=0.0, c=0, mp=0, mn=0, tok=0.0, tn=0, mcp=0, kub=0, cost=0.0, cn=0, err=0, calls=0))
    for a in scored:
        b = by[(a["model"], a["family"])]
        b["p"] += bool(a["diagnosis"]); b["n"] += 1
        if a["diagnosis"] and a["ttd_s"]:
            b["t"] += a["ttd_s"]; b["c"] += 1
        if a["mitigation"] is not None:
            b["mn"] += 1; b["mp"] += bool(a["mitigation"])
        tt = a["tokens"] and a["tokens"].get("total_tokens")
        if tt:
            b["tok"] += tt; b["tn"] += 1
        b["mcp"] += a["n_mcp"]; b["kub"] += a["n_kubectl"]; b["err"] += a["n_errors"]; b["calls"] += a["n_mcp"] + a["n_kubectl"] + a["n_shell"]
        if a["cost_usd"] is not None:
            b["cost"] += a["cost_usd"]; b["cn"] += 1
    print(f"\n{'model':18s} {'arm':20s} {'diagnosis':>12s} {'mitigation':>10s} {'TTD':>7s} {'tokens':>9s} {'mcp':>5s} {'kubectl':>7s} {'errors':>6s} {'cost':>6s}")
    for k in sorted(by, key=lambda k: (k[0], FAMILY_ORDER.index(k[1]) if k[1] in FAMILY_ORDER else 99)):
        b = by[k]
        mit = f"{b['mp']}/{b['mn']}" if b["mn"] else "-"
        ttd = f"{b['t'] / b['c'] / 60:.1f}m" if b["c"] else "-"
        tok = f"{b['tok'] / b['tn']:,.0f}" if b["tn"] else "-"
        cost = f"${b['cost'] / b['cn']:.2f}" if b["cn"] else "-"
        err = f"{100 * b['err'] / b['calls']:.0f}%" if b["calls"] else "-"
        print(f"{k[0]:18s} {k[1]:20s} {b['p']:3d}/{b['n']:<3d} {100 * b['p'] / b['n']:3.0f}% {mit:>10s} {ttd:>7s} {tok:>9s} {b['mcp'] / b['n']:5.1f} {b['kub'] / b['n']:7.1f} {err:>6s} {cost:>6s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
