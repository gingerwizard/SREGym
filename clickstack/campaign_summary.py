#!/usr/bin/env python3
"""Print Markdown result tables for a campaign: diagnosis pass per model × arm, mitigation for kubectl arms,
and per-run detail (judge composite, agent minutes, cost, tool mix). Shares its loaders with report.py."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report  # noqa: E402

ARM_ORDER = ["claudecode", "claudecode_clickstack", "claudecode_clickstack_only_t0", "claudecode_clickstack_only_t1",
             "claudecode_clickstack_only_t2", "claudecode_clickstack_only_t3"]
ARM_SHORT = {"claudecode": "stock", "claudecode_clickstack": "kubectl + ClickStack", "claudecode_clickstack_only_t0": "ClickStack only, tier 0",
             "claudecode_clickstack_only_t1": "tier 1", "claudecode_clickstack_only_t2": "tier 2", "claudecode_clickstack_only_t3": "tier 3"}


def mark(a: dict) -> str:
    comp = re.search(r"composite=([0-9.]+)", a.get("diagnosis_reasoning") or "")
    score = f" ({comp.group(1)})" if comp else ""
    if a.get("diagnosis") is True:
        return "pass" + score
    if a.get("diagnosis") is False:
        return "fail" + score
    return "no result"


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "anthropic-sweep"
    root = report.REPO / "results"
    runs = [r for r in report.load_manifests(root, name) if r.get("exit_code") == 0]
    attempts = report.load_attempts(root, runs)
    models = sorted({a["model"] for a in attempts})
    arms = [x for x in ARM_ORDER if any(a["agent"] == x for a in attempts)]
    cell = {(a["model"], a["agent"]): a for a in attempts}

    print("### Diagnosis, model against arm (judge composite in brackets)\n")
    print("| Model | " + " | ".join(ARM_SHORT[x] for x in arms) + " |")
    print("|---|" + "---|" * len(arms))
    for m in models:
        print(f"| {m} | " + " | ".join(mark(cell[(m, x)]) if (m, x) in cell else "–" for x in arms) + " |")

    kub = [x for x in arms if any(a["agent"] == x and a.get("mitigation") is not None for a in attempts)]
    print("\n### Mitigation, kubectl arms only\n")
    print("| Model | " + " | ".join(ARM_SHORT[x] for x in kub) + " |")
    print("|---|" + "---|" * len(kub))
    for m in models:
        row = []
        for x in kub:
            a = cell.get((m, x))
            row.append("–" if not a or a.get("mitigation") is None else ("pass" if a["mitigation"] else "fail"))
        print(f"| {m} | " + " | ".join(row) + " |")

    print("\n### Per run: agent minutes / turns / cost / ClickStack calls / kubectl calls / skill read\n")
    print("| Model | Arm | Diagnosis | Min | Turns | Cost | ClickStack | kubectl | Skill |")
    print("|---|---|---|---|---|---|---|---|---|")
    for m in models:
        for x in arms:
            a = cell.get((m, x))
            if not a:
                continue
            tc = a.get("tool_calls") or {}
            mins = f"{tc['duration_ms'] / 60000:.1f}" if tc.get("duration_ms") else "–"
            cost = f"${tc['cost_usd']:.2f}" if tc.get("cost_usd") is not None else "–"
            skill = "yes" if a.get("skill_read") else ("no" if a.get("skill_read") is False else "–")
            print(f"| {m} | {ARM_SHORT[x]} | {mark(a)} | {mins} | {tc.get('turns', '–')} | {cost} | {tc.get('clickstack', '–')} | {tc.get('kubectl', '–')} | {skill} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
