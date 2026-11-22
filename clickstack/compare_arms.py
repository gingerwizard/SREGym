#!/usr/bin/env python3
"""Where does arm A fail while arm B passes? Per-problem pass counts for both arms, then every failed attempt of
arm A (same model, same problem) with the fault, the submitted diagnosis, the judge's verdict and the tool trace,
so the failure mode can be read off.

  uv run python clickstack/compare_arms.py --a only_t3 --b stock --harness claudecode
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import in_progress_runs, load_attempts, load_manifests  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", default=str(REPO / "results"))
    ap.add_argument("--campaign", action="append", default=None)
    ap.add_argument("--a", default="only_t3", help="arm family under scrutiny")
    ap.add_argument("--b", default="stock", help="reference arm family")
    ap.add_argument("--harness", default="claudecode")
    ap.add_argument("--model", default=None)
    ap.add_argument("--chars", type=int, default=700, help="characters of diagnosis and judge text to print")
    ap.add_argument("--steps", type=int, default=14, help="tool-call steps to print per attempt")
    args = ap.parse_args()
    root = Path(args.results_root)
    runs = []
    for c in args.campaign or ["lite-sweep", "lite-sweep-fill"]:
        rs = [r for r in load_manifests(root, c) if r.get("exit_code") in (0, 2, None) and r.get("results_dir")]
        runs += rs + in_progress_runs(root, rs, c)
    att = [a for a in load_attempts(root, runs) if a["diagnosis"] is not None and a["harness"] == args.harness and (not args.model or a["model"] == args.model)]
    A = [a for a in att if a["family"] == args.a]
    B = [a for a in att if a["family"] == args.b]
    cell = defaultdict(lambda: [0, 0])
    for a in A + B:
        c = cell[(a["problem"], a["model"], a["family"])]; c[0] += bool(a["diagnosis"]); c[1] += 1
    problems = sorted({a["problem"] for a in A}, key=lambda p: sum(cell[(p, m, args.a)][0] for m in {a["model"] for a in A}))
    models = sorted({a["model"] for a in A})
    print(f"{args.harness}: {args.a} against {args.b}, pass counts per model (A / B)\n")
    print(f"{'problem':52s} " + " ".join(f"{m[:16]:>16s}" for m in models))
    for p in problems:
        print(f"{p:52s} " + " ".join(f"{cell[(p, m, args.a)][0]}/{cell[(p, m, args.a)][1]} vs {cell[(p, m, args.b)][0]}/{cell[(p, m, args.b)][1]}".rjust(16) for m in models))
    print()
    fails = [a for a in A if not a["diagnosis"]]
    fails.sort(key=lambda a: (a["problem"], a["model"], a["attempt"]))
    print(f"{len(fails)} failed {args.a} attempts\n")
    for a in fails:
        b_pass = cell[(a["problem"], a["model"], args.b)]
        print("=" * 110)
        print(f"{a['problem']}  |  {a['model']}  attempt {a['attempt']}  |  score {a['composite']}  |  TTD {a['ttd_s'] and round(a['ttd_s'] / 60, 1)} min  |  {args.b} passed {b_pass[0]}/{b_pass[1]}  |  calls: {a['n_mcp']} mcp, {a['n_errors']} errors")
        print(f"FAULT: {a['fault'][: args.chars]}")
        print(f"DIAGNOSIS: {a['submission'][: args.chars].replace(chr(10), ' ')}")
        print(f"JUDGE: {a['judge_reasoning'][: args.chars].replace(chr(10), ' ')}")
        print("TRACE:")
        for s in a["steps"][: args.steps]:
            txt = (s["sql"] or s["args"] or "").replace("\n", " ")[:150]
            print(f"  {s['i']:>3} {s['kind']:7s} {s['tool'][:28]:28s} {txt}{'  [ERROR]' if s['error'] else ''}")
        if len(a["steps"]) > args.steps:
            print(f"  ... {len(a['steps']) - args.steps} more calls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
