#!/usr/bin/env python3
"""Compose the per-tier skills from the shared sections.

Tier N contains sections 00..N0. Run after editing anything under sections/; the generated
tier*/clickstack-telemetry/SKILL.md files are committed so the client needs no build step.
"""
from pathlib import Path

HERE = Path(__file__).parent
SECTIONS = sorted((HERE / "sections").glob("*.md"))
TIERS = {
    0: "container logs, Kubernetes Events, node logs, metrics and traces",
    1: "that plus Kubernetes object snapshots and the change history of workloads, Services, Nodes and volumes",
    2: "that plus ConfigMaps, policies, RBAC, webhooks, operator objects and control-plane logs",
    3: "that plus per-node kernel, network and kubelet configuration records",
    4: "the same data as tier 3, plus investigation rules: read whole manifests, look for a second cause, name the origin not the victim",
}
FRONTMATTER = """---
name: clickstack-telemetry
description: How this cluster's telemetry and Kubernetes state are stored in ClickStack and how to query them with the clickstack MCP tools. Read before the first query. Covers {scope}.
---

# Investigating this cluster through ClickStack

This skill describes what the `mcp__clickstack__*` tools can see in this environment and the query
patterns that work. It describes the data, not any particular incident.

"""


def build(dest: Path = HERE, database: str = "default") -> None:
    """database: the ClickHouse database this cluster writes to; the sections are written for `default`."""
    for tier, scope in TIERS.items():
        parts = [p.read_text().rstrip().replace("default.otel_", f"{database}.otel_") + "\n" for p in SECTIONS if int(p.name[:2]) <= tier * 10]
        out = dest / f"tier{tier}" / "clickstack-telemetry" / "SKILL.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(FRONTMATTER.format(scope=scope) + "\n".join(parts))
        print(f"wrote {out} ({len(out.read_text().split())} words)")


if __name__ == "__main__":
    import sys

    build(database=sys.argv[1] if len(sys.argv) > 1 else "default")
