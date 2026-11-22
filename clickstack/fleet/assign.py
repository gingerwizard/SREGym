#!/usr/bin/env python3
"""Assign (arm, model) pairs to clusters and write one queue file per cluster.

Arms that need ClickStack go only to clusters with ClickStack access; the rest spread over all clusters by
estimated attempt count. Each queue line is: <campaign> <suite> <n_attempts> <model> <arm>.

  uv run python clickstack/fleet/assign.py --campaign phase1 --suite sregym-lite --n-attempts 3 \
      --models claude-opus-5-5 claude-fable-5-2 gpt-6.1-sol --arms-file clickstack/fleet/arms-phase1.txt --out ~/fleet/queues
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--suite", required=True)
    ap.add_argument("--n-attempts", type=int, default=3)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--arms", nargs="*", default=[])
    ap.add_argument("--arms-file", default=None)
    ap.add_argument("--clusters", default=str(REPO / "clickstack" / "fleet" / "clusters.json"))
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these cluster ids")
    ap.add_argument("--out", required=True, help="directory for <cluster>.queue files")
    args = ap.parse_args()
    arms = list(args.arms)
    if args.arms_file:
        arms += [l.strip() for l in Path(args.arms_file).read_text().splitlines() if l.strip() and not l.startswith("#")]
    clusters = json.loads(Path(args.clusters).read_text())
    if args.only:
        clusters = {k: v for k, v in clusters.items() if k in args.only}
    pairs = [(m, a) for a in arms for m in args.models]
    harness_of = lambda a: "codex" if a.startswith("codex") else "claudecode"  # noqa: E731
    needs_cs = lambda a: "_clickstack" in a  # noqa: E731
    # Codex pairs first and ClickStack pairs first within that: the constrained ones get placed before the flexible ones
    pairs.sort(key=lambda p: (not needs_cs(p[1]), harness_of(p[1]) != "codex"))
    load = {c: 0 for c in clusters}
    queues = {c: [] for c in clusters}
    for model, arm in pairs:
        eligible = [c for c in clusters if clusters[c].get("clickstack") or not needs_cs(arm)]
        target = min(eligible, key=lambda c: load[c])
        queues[target].append(f"{args.campaign} {args.suite} {args.n_attempts} {model} {arm}")
        load[target] += 1
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    for c, lines in queues.items():
        (out / f"{c}.queue").write_text("\n".join(lines) + ("\n" if lines else ""))
        print(f"{c}: {len(lines)} pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
