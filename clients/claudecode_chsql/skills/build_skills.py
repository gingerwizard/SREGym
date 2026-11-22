#!/usr/bin/env python3
"""Compose the per-tier clickhouse-sql skills from the shared sections (SQL flavour of the ClickStack skills).

Tier N contains sections 00..N0. The generated tier*/clickhouse-sql/SKILL.md files are committed.
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
name: clickhouse-sql
description: How this cluster's telemetry and Kubernetes state are stored in ClickHouse and how to query them with SQL through the clickhouse MCP tool. Read before the first query. Covers {scope}.
---

# Investigating this cluster with SQL on ClickHouse

This skill describes what `mcp__clickhouse__run_query` can see in this environment and the SQL
patterns that work. It describes the data, not any particular incident.

"""


def build(dest: Path = HERE, database: str = "default") -> None:
    """database: the ClickHouse database this cluster writes to; the sections are written for `default`."""
    for tier, scope in TIERS.items():
        parts = [p.read_text().rstrip().replace("default.otel_", f"{database}.otel_") + "\n" for p in SECTIONS if int(p.name[:2]) <= tier * 10]
        out = dest / f"tier{tier}" / "clickhouse-sql" / "SKILL.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(FRONTMATTER.format(scope=scope) + "\n".join(parts))
        print(f"wrote {out} ({len(out.read_text().split())} words)")


if __name__ == "__main__":
    import sys

    build(database=sys.argv[1] if len(sys.argv) > 1 else "default")
