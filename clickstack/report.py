#!/usr/bin/env python3
"""Build a single-file, filterable HTML report over campaign manifests.

Python collects one record per attempt (verdicts, judge score, submitted diagnosis, time to diagnosis
and mitigation, tokens, cost, blocked network requests, and the ordered tool trace with every SQL
query and the agent's words before it). The page computes all aggregates in the browser from the
attempts that pass the filters (model, harness, arm/tier, problem), so every number can be sliced.

  uv run python clickstack/report.py --out results/report.html
  uv run python clickstack/report.py --campaign lite-sweep --out /tmp/lite.html
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
FAMILY_ORDER = ["stock", "obs_only",
                "kubectl_clickstack", "kubectl_clickstack_t3", "kubectl_clickstack_t4", "only_t0", "only_t1", "only_t2", "only_t3", "only_t4",
                "kubectl_chsql", "kubectl_chsql_t3", "kubectl_chsql_t4", "chsql_only_t0", "chsql_only_t1", "chsql_only_t2", "chsql_only_t3", "chsql_only_t4"]
FAMILY_LABEL = {
    "stock": "stock tools (kubectl; Prometheus, Loki, Jaeger in the cluster)",
    "obs_only": "stock telemetry MCP only (Prometheus, Loki, Jaeger tools; no kubectl)",
    "kubectl_clickstack": "kubectl + ClickStack (tier 2)",
    "kubectl_clickstack_t3": "kubectl + ClickStack (tier 3)",
    "kubectl_clickstack_t4": "kubectl + ClickStack (tier 4)",
    "only_t0": "ClickStack only, tier 0",
    "only_t1": "ClickStack only, tier 1",
    "only_t2": "ClickStack only, tier 2",
    "only_t3": "ClickStack only, tier 3",
    "only_t4": "ClickStack only, tier 4",
    "kubectl_chsql": "kubectl + ClickHouse SQL (tier 2)",
    "kubectl_chsql_t3": "kubectl + ClickHouse SQL (tier 3)",
    "kubectl_chsql_t4": "kubectl + ClickHouse SQL (tier 4)",
    "chsql_only_t0": "ClickHouse SQL only, tier 0",
    "chsql_only_t1": "ClickHouse SQL only, tier 1",
    "chsql_only_t2": "ClickHouse SQL only, tier 2",
    "chsql_only_t3": "ClickHouse SQL only, tier 3",
    "chsql_only_t4": "ClickHouse SQL only, tier 4",
}
MCP_PREFIXES = ("mcp__clickstack__", "mcp__clickhouse__", "mcp__prometheus__", "mcp__loki__", "mcp__jaeger__")


# ----------------------------------------------------------------------------- tool-use analysis
# kubectl resource aliases -> canonical kind
KUBECTL_ALIASES = {
    "po": "pod", "pods": "pod", "deploy": "deployment", "deployments": "deployment", "svc": "service", "services": "service",
    "ns": "namespace", "namespaces": "namespace", "no": "node", "nodes": "node", "ev": "event", "events": "event",
    "netpol": "networkpolicy", "networkpolicies": "networkpolicy", "cm": "configmap", "configmaps": "configmap", "secrets": "secret",
    "pvc": "persistentvolumeclaim", "persistentvolumeclaims": "persistentvolumeclaim", "pv": "persistentvolume", "persistentvolumes": "persistentvolume",
    "sts": "statefulset", "statefulsets": "statefulset", "ds": "daemonset", "daemonsets": "daemonset", "cj": "cronjob", "cronjobs": "cronjob", "jobs": "job",
    "rs": "replicaset", "replicasets": "replicaset", "ep": "endpoints", "endpointslices": "endpointslice", "ing": "ingress", "ingresses": "ingress",
    "sa": "serviceaccount", "serviceaccounts": "serviceaccount", "hpa": "horizontalpodautoscaler", "horizontalpodautoscalers": "horizontalpodautoscaler",
    "quota": "resourcequota", "resourcequotas": "resourcequota", "limits": "limitrange", "limitranges": "limitrange",
    "crd": "customresourcedefinition", "crds": "customresourcedefinition", "customresourcedefinitions": "customresourcedefinition",
    "mutatingwebhookconfigurations": "mutatingwebhookconfiguration", "validatingwebhookconfigurations": "validatingwebhookconfiguration",
    "roles": "role", "rolebindings": "rolebinding", "clusterroles": "clusterrole", "clusterrolebindings": "clusterrolebinding",
    "sc": "storageclass", "storageclasses": "storageclass", "pdb": "poddisruptionbudget", "poddisruptionbudgets": "poddisruptionbudget",
    "pc": "priorityclass", "priorityclasses": "priorityclass", "rc": "replicationcontroller", "all": "all",
}
KUBECTL_FLAG_WITH_VALUE = {"-n", "--namespace", "-o", "--output", "-l", "--selector", "-c", "--container", "-f", "--filename", "-p", "--patch",
                           "--type", "--as", "--as-group", "--image", "--sort-by", "--field-selector", "--context", "--kubeconfig", "--timeout",
                           "--since", "--tail", "--since-time", "--template", "--restart", "--for", "--to", "--revision", "--replicas",
                           "--overwrite", "--resource", "--subresource", "--grace-period", "--port", "--target-port", "--name", "--address",
                           "--env", "--limits", "--requests", "--rm", "--max", "--min", "--cpu-percent", "--cascade", "--server", "--token",
                           "--dry-run", "--output-watch-events", "--chunk-size", "--raw", "--show-labels", "--label-columns", "-L"}
KUBECTL_NO_VALUE = {"--rm", "--show-labels", "--dry-run", "--output-watch-events", "--raw"}  # listed above for safety, take no value
KUBECTL_FIX_VERBS = {"apply", "patch", "edit", "delete", "scale", "set", "label", "annotate", "create", "replace", "cordon", "uncordon", "drain",
                     "taint", "expose", "autoscale", "certificate"}
KUBECTL_PROBE_VERBS = {"exec", "port-forward", "run", "debug", "cp", "attach", "proxy"}
KUBECTL_RESOURCE_VERBS = {"get", "describe", "delete", "edit", "patch", "scale", "label", "annotate", "explain", "wait", "create", "expose", "autoscale", "replace"}
SHELL_PROBE_RE = re.compile(r"\b(curl|wget|nc|ncat|netcat|dig|nslookup|host|getent|ping|traceroute|grpcurl|redis-cli|valkey-cli|mongosh|mongo|psql|mysql|openssl s_client|telnet)\b")
SUBMIT_RE = re.compile(r"host\.docker\.internal:8000/submit")
# SREGym's Prometheus/Loki/Jaeger MCP tools driven from a shell command (an agent that wrote its own MCP client,
# e.g. `python /tmp/obs.py prometheus get_metrics '{...}'` or a raw tools/call) count as MCP calls of that tool.
SCRIPTED_TOOL_RE = re.compile(r"\b(get_metrics|get_alerts|get_logs|get_labels|get_label_values|get_services|get_operations|get_traces|get_dependency_graph)\b")
SCRIPTED_CLIENT_RE = re.compile(r"\b\w+\.py\b|tools/call|:9954/|/sse\b|text/event-stream")


SHELL_READ_RE = re.compile(r"SKILL\.md|AGENTS\.md|/logs\b|/opt/sregym|clickstack-telemetry|clickhouse-sql|routing", re.I)
SHELL_TEXT_RE = re.compile(r"^\s*(?:/bin/bash -lc\s+['\"]?)?(?:cd [^;&|]+[;&|]+\s*)?(cat|ls|find|rg|grep|head|tail|sed|awk|jq|echo|printf|wc|sort|uniq|cut|tr|date|env|python3? -c|python3? -I -c)\b")
SHELL_SCRIPT_RE = re.compile(r"cat > /tmp/|python3? /tmp/|\.py\b|node -e|<<\s*['\"]?(PY|EOF)")
SHELL_CLASSES = ("submit", "read skill or files", "live probe", "script", "text processing", "other")


def shell_class(command: str) -> str:
    """What a non-kubectl shell command is for: the submission to the conductor, reading the skill or the run's files,
    a live probe, running or writing a script, plain text processing, or anything else."""
    if SUBMIT_RE.search(command):
        return "submit"
    if SHELL_READ_RE.search(command):
        return "read skill or files"
    if is_probe_shell(command):
        return "live probe"
    if SHELL_SCRIPT_RE.search(command):
        return "script"
    if SHELL_TEXT_RE.search(command):
        return "text processing"
    return "other"


CLICKSTACK_TOOL_RE = re.compile(r"^clickstack_")
SQL_MCP_TOOLS = {"run_query", "list_databases", "list_tables"}
OBS_MCP_TOOLS = {"get_metrics", "get_alerts", "get_logs", "get_labels", "get_label_values", "get_services", "get_operations", "get_traces", "get_dependency_graph"}


def mcp_server_of(tool: str) -> str:
    if CLICKSTACK_TOOL_RE.match(tool):
        return "clickstack"
    if tool in SQL_MCP_TOOLS:
        return "sql"
    if tool in OBS_MCP_TOOLS:
        return "obs"
    return "other"


def scripted_mcp(command: str) -> str | None:
    """The telemetry tool a shell command drives through a self-written MCP client, or None."""
    if SUBMIT_RE.search(command) and not SCRIPTED_TOOL_RE.search(command):
        return None
    m = SCRIPTED_TOOL_RE.search(command)
    if m and SCRIPTED_CLIENT_RE.search(command):
        return m.group(1)
    return None


def parse_kubectl(command: str) -> list[str]:
    """Normalised 'verb kind' tokens for every kubectl invocation in a shell command line.

    'kubectl get pods,svc -n x; kubectl describe deploy/web' -> ['get pod', 'get service', 'describe deployment'].
    Verbs without a kind keep the implied one (logs/exec/top pod, rollout status deployment, ...)."""
    out = []
    for seg in re.split(r"\s*(?:;|&&|\|\||\|)\s*", command):
        m = re.search(r"\bkubectl\b\s+(.*)$", seg)
        if not m:
            continue
        toks = m.group(1).replace("'", " ").replace('"', " ").split()
        words = []
        skip = False
        for t in toks:
            if skip:
                skip = False
                continue
            if t.startswith("-"):
                if "=" not in t and t in KUBECTL_FLAG_WITH_VALUE and t not in KUBECTL_NO_VALUE:
                    skip = True
                continue
            words.append(t)
        if not words:
            continue
        verb = words[0].lower()
        rest = words[1:]
        kinds: list[str] = []
        if verb in ("rollout", "top", "auth", "config", "cluster-info", "api-resources", "api-versions", "version", "set", "certificate"):
            sub = rest[0].lower() if rest else ""
            if verb == "top":
                kinds = [KUBECTL_ALIASES.get(sub, sub) or "pod"]
            elif verb == "rollout":
                verb = f"rollout {sub}" if sub else "rollout"
                target = rest[1] if len(rest) > 1 else ""
                kinds = [KUBECTL_ALIASES.get(target.split("/")[0].lower(), target.split("/")[0].lower()) or "deployment"]
            elif verb == "auth":
                verb = "auth can-i"
                kinds = [""]
            elif verb == "set":
                verb = f"set {sub}" if sub else "set"
                target = rest[1] if len(rest) > 1 else ""
                kinds = [KUBECTL_ALIASES.get(target.split("/")[0].lower(), target.split("/")[0].lower())]
            else:
                kinds = [""]
        elif verb in ("logs", "exec", "port-forward", "attach", "cp", "debug"):
            target = rest[0] if rest else ""
            kind = target.split("/")[0].lower() if "/" in target else "pod"
            kinds = [KUBECTL_ALIASES.get(kind, kind)]
        elif verb in ("run",):
            kinds = ["pod"]
        elif verb in KUBECTL_RESOURCE_VERBS:
            target = rest[0] if rest else ""
            if target.startswith("-f") or verb == "apply":
                kinds = ["manifest"]
            else:
                for part in target.split(","):
                    kind = part.split("/")[0].lower()
                    kind = re.sub(r"\.(apps|batch|v1|networking\.k8s\.io|rbac\.authorization\.k8s\.io|admissionregistration\.k8s\.io|policy|autoscaling|storage\.k8s\.io|apiextensions\.k8s\.io|scheduling\.k8s\.io).*$", "", kind)
                    kinds.append(KUBECTL_ALIASES.get(kind, kind))
        else:
            kinds = [""]
        for kind in kinds:
            out.append((verb + " " + kind).strip())
    return out


def split_token(token: str) -> tuple[str, str]:
    """('rollout status', 'deployment') from 'rollout status deployment'; two-word verbs are rollout X, set X and auth can-i."""
    words = token.split(" ")
    if words[0] in ("rollout", "set", "auth") and len(words) > 1:
        return " ".join(words[:2]), " ".join(words[2:])
    return words[0], " ".join(words[1:])


def kubectl_class(token: str) -> str:
    verb, _ = split_token(token)
    if verb in KUBECTL_FIX_VERBS or (verb.startswith("rollout ") and verb not in ("rollout status", "rollout history")) or verb.startswith("set "):
        return "fix"
    if verb in KUBECTL_PROBE_VERBS:
        return "probe"
    return "diag"


def is_probe_shell(command: str) -> bool:
    """A shell command that probes the running system (curl to a service, dig, nc, ...) rather than reading files or submitting."""
    if SUBMIT_RE.search(command):
        return False
    return bool(SHELL_PROBE_RE.search(command))


# Which telemetry area a ClickStack / SQL / stock-telemetry call reads. Order matters: the Kubernetes-state services
# live in the logs table, so they are matched before the generic logs rule.
AREA_RULES = [
    ("events", re.compile(r"k8s-events|k8s\.event\.", re.I)),
    ("changes", re.compile(r"k8s-changes", re.I)),
    ("objects", re.compile(r"k8s-objects|k8s\.object\.(kind|name|uid)|k8s\.resource\.name", re.I)),
    ("node-state", re.compile(r"node-state|sysctl|kubelet-config|file-max|inotify", re.I)),
    ("machine-logs", re.compile(r"journald|kubelet|containerd|control-plane|kube-apiserver|kube-controller|kube-scheduler|etcd", re.I)),
    ("traces", re.compile(r"otel_traces|TraceId|SpanName|SpanKind|ParentSpanId|StatusCode", re.I)),
    ("metrics", re.compile(r"otel_metrics|MetricName|metricName|TimeUnix|k8s\.(pod|container|deployment|node|resource_quota|hpa|statefulset|daemonset|job)\.", re.I)),
    ("logs", re.compile(r"otel_logs|SeverityText|\bBody\b|LogAttributes|ServiceName", re.I)),
]
AREA_BY_TOOL = {
    "clickstack_service_health": "traces", "clickstack_trace_waterfall": "traces", "clickstack_trace_top_time_consuming_ops": "traces",
    "clickstack_trace_top_time_consuming_operations": "traces", "clickstack_timeseries": "metrics", "clickstack_describe_metric": "metrics",
    "clickstack_list_metrics": "metrics", "clickstack_event_patterns": "logs", "clickstack_event_deltas": "logs", "clickstack_emerging_signals": "logs",
    "clickstack_search": "logs", "clickstack_list_sources": "meta", "clickstack_describe_source": "meta", "clickstack_describe_table": "meta",
    "clickstack_get_source_filter_values": "meta", "clickstack_list_teams": "meta", "clickstack_search_dashboards": "meta", "clickstack_get_dashboard": "meta",
    "clickstack_get_dashboard_tile": "meta", "clickstack_query_tile": "meta", "clickstack_query_tiles": "meta", "clickstack_get_saved_search": "meta",
    "clickstack_get_alert": "meta", "clickstack_get_webhook": "meta",
    "list_databases": "meta", "list_tables": "meta",
    "get_metrics": "metrics", "get_alerts": "metrics", "get_logs": "logs", "get_labels": "meta", "get_label_values": "meta",
    "get_services": "traces", "get_operations": "traces", "get_traces": "traces", "get_dependency_graph": "traces",
}
AREA_LABEL = {"events": "Kubernetes events", "changes": "object changes (k8s-changes)", "objects": "object snapshots (k8s-objects)",
              "node-state": "node settings (node-state)", "machine-logs": "control-plane and machine logs", "traces": "traces", "metrics": "metrics",
              "logs": "application logs", "meta": "schema and source discovery", "other": "other"}
# Lowest collector tier at which an area is shipped (tier 4 ships the same data as tier 3)
AREA_MIN_TIER = {"events": 0, "logs": 0, "metrics": 0, "traces": 0, "objects": 1, "changes": 1, "machine-logs": 2, "node-state": 3, "meta": 0, "other": 0}
# Kinds whose snapshots and changes need tier 2 ("every kind"); the core kinds arrive at tier 1
CORE_KINDS = {"pod", "deployment", "replicaset", "statefulset", "daemonset", "service", "endpoints", "endpointslice", "node", "namespace", "job",
              "cronjob", "configmap", "event", "persistentvolumeclaim", "horizontalpodautoscaler"}


def area_of(tool: str, args: str, sql: str | None) -> str:
    text = (sql or "") + " " + (args or "")
    for area, rx in AREA_RULES[:5]:
        if rx.search(text):
            return area
    if tool in AREA_BY_TOOL and AREA_BY_TOOL[tool] != "logs":
        if AREA_BY_TOOL[tool] == "meta":
            return "meta"
        for area, rx in AREA_RULES[5:]:
            if rx.search(text):
                return area
        return AREA_BY_TOOL[tool]
    for area, rx in AREA_RULES[5:]:
        if rx.search(text):
            return area
    return AREA_BY_TOOL.get(tool, "other")


def kubectl_equivalent(token: str) -> tuple[str, str, int | None]:
    """(area, description, minimum tier) of the telemetry that stands in for a kubectl 'verb kind'; tier None = no equivalent."""
    verb, kind = split_token(token)
    cls = kubectl_class(token)
    if cls == "fix":
        return ("fix", "a change to the cluster (mitigation, not diagnosis)", None)
    if cls == "probe" or verb in ("run", "debug"):
        return ("probe", "a live probe from inside the cluster (exec, port-forward, debug pod): nothing in the telemetry answers it", None)
    if verb == "logs":
        return ("logs", "application logs (tier 0); a crashed container's previous logs only if the collector read them before the restart", 0)
    if verb == "top":
        return ("metrics", f"{kind or 'pod'} CPU and memory metrics (tier 0)", 0)
    if kind == "event" or verb == "events":
        return ("events", "Kubernetes events (tier 0)", 0)
    if verb == "auth can-i":
        return ("objects", "RBAC objects: roles and bindings snapshots (tier 2)", 2)
    if verb in ("rollout status", "rollout history"):
        return ("changes", f"{kind or 'deployment'} changes and ReplicaSet snapshots (tier 1)", 1)
    if kind in ("secret",):
        return ("objects", "Secret metadata only: data is stripped from the telemetry by design", 1)
    if kind in ("node",):
        return ("objects", "node snapshots (tier 1), node metrics (tier 0), node settings such as sysctls (tier 3)", 1)
    if kind in ("", "all", "manifest", "api-resources", "version", "cluster-info", "config"):
        return ("meta", "discovery, no data needed", 0)
    if kind in CORE_KINDS:
        return ("objects", f"{kind} snapshots and changes (tier 1)", 1)
    return ("objects", f"{kind} snapshots and changes (tier 2, every kind)", 2)


def parse_checklist(text: str) -> dict[str, str]:
    """{'D1-Q1': 'Yes', ...} from the judge's checklist column (a Python literal)."""
    if not text:
        return {}
    try:
        import ast  # noqa: PLC0415
        items = ast.literal_eval(text)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and it.get("id"):
                out[str(it["id"])] = str(it.get("answer", ""))
    return out


def parse_dims(text: str) -> dict[str, float]:
    if not text:
        return {}
    try:
        import ast  # noqa: PLC0415
        d = ast.literal_eval(text)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict) and v.get("score") is not None:
                try:
                    out[str(k)] = float(v["score"])
                except (TypeError, ValueError):
                    pass
    return out


GAVE_UP_RE = re.compile(r"\b(unable to|could not|cannot|can't|insufficient|no (clear |definitive |confirmed )?(root cause|evidence|data)|inconclusive|"
                        r"not (been )?able to|does not establish|did not find|tools? (are|were|is) (failing|unavailable|not working))\b", re.I)
FAILURE_MODES = {
    "gave up: tools failing": "No cause named and the submission says so, after at least half of the tool calls returned errors.",
    "gave up: no cause named": "No faulty component named; the submission says the evidence was insufficient or the cause could not be established.",
    "wrong component": "A healthy component was blamed as the origin (judge D1-Q1 No and D1-Q3 No).",
    "no component named": "The diagnosis names no faulty component (D1-Q1 No) without declaring itself inconclusive.",
    "symptom, not origin": "The right area, but a secondary failure was presented as the cause (D1-Q2 No).",
    "wrong mechanism": "Right component, wrong explanation of how it fails (D2-Q1 No).",
    "wrong fault type": "Right component and roughly right mechanism, but attributed to the wrong kind of fault (D2-Q3 No).",
    "right cause, imprecise": "Localisation and mechanism accepted; details, scope or symptoms were missing or over-attributed (D2-Q2 or D3 No), and the composite fell below 0.70.",
    "below threshold": "Every checklist item was answered Yes or left blank, yet the composite score is below 0.70.",
}


def failure_mode(checks: dict[str, str], submission: str, n_errors: int, n_calls: int) -> str:
    if not checks:
        return "unscored"
    gave_up = bool(GAVE_UP_RE.search(submission or ""))
    if checks.get("D1-Q1") == "No":
        if gave_up and n_calls and n_errors / n_calls >= 0.5:
            return "gave up: tools failing"
        if gave_up and checks.get("D1-Q3") != "No":
            return "gave up: no cause named"
        if checks.get("D1-Q3") == "No":
            return "wrong component"
        return "no component named"
    if checks.get("D1-Q2") == "No":
        return "symptom, not origin"
    if checks.get("D2-Q1") == "No":
        return "wrong mechanism"
    if checks.get("D2-Q3") == "No":
        return "wrong fault type"
    if any(checks.get(k) == "No" for k in ("D2-Q2", "D3-Q1", "D3-Q2", "D3-Q3")):
        return "right cause, imprecise"
    return "below threshold"


def problem_sets() -> dict[str, list[str]]:
    """Named problem sets (SREGym's registry plus clickstack/problem_sets.json), for the report's problem-set filter."""
    import os  # noqa: PLC0415
    os.environ.setdefault("SREGYM_PROBLEM_SETS_FILE", str(REPO / "clickstack" / "problem_sets.json"))
    try:
        sys.path.insert(0, str(REPO))
        from sregym.conductor.problem_sets import PROBLEM_SETS  # noqa: PLC0415
        return {k: sorted(v) for k, v in PROBLEM_SETS.items()}
    except Exception:  # noqa: BLE001
        try:
            return {k: sorted(v) for k, v in json.loads((REPO / "clickstack" / "problem_sets.json").read_text()).items()}
        except Exception:  # noqa: BLE001
            return {}


def render_notes(path: Path) -> str:
    """A small Markdown subset (#/## headings, - bullets, **bold**, `code`, paragraphs) to HTML."""
    out, in_list = [], False
    for raw in path.read_text().splitlines() + [""]:
        line = raw.rstrip()
        def inline(t: str) -> str:
            t = html.escape(t)
            t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
            return re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("## "):
            out.append(f"<h4>{inline(line[3:])}</h4>")
        elif line.startswith("# "):
            out.append(f"<h3>{inline(line[2:])}</h3>")
        elif line:
            out.append(f"<p>{inline(line)}</p>")
    return "\n".join(out)


# ----------------------------------------------------------------------------- loading

def as_bool(value) -> bool | None:
    s = str(value if value is not None else "").strip().lower()
    return True if s in {"true", "1", "yes"} else False if s in {"false", "0", "no"} else None


def as_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def harness_of(agent: str) -> str:
    return "claudecode" if agent.startswith("claudecode") else "codex" if agent.startswith("codex") else agent


def family_of(agent: str, tier) -> str:
    if "_clickstack_only_t" in agent:
        return f"only_t{agent.rsplit('_t', 1)[1]}"
    if "_chsql_only_t" in agent:
        return f"chsql_only_t{agent.rsplit('_t', 1)[1]}"
    if agent.endswith("_clickstack"):
        return "kubectl_clickstack"
    if agent.endswith("_chsql"):
        return "kubectl_chsql"
    m = re.search(r"_(clickstack|chsql)_t(\d)$", agent)
    if m:
        return f"kubectl_{m.group(1)}_t{m.group(2)}"
    if agent.endswith("_obs"):
        return "obs_only"
    return "stock"


def load_manifests(root: Path, campaign: list[str] | str | None) -> list[dict]:
    wanted = [campaign] if isinstance(campaign, str) else list(campaign or [])
    records = []
    for path in sorted((root / "campaigns").glob("*.jsonl")):
        if wanted and path.stem not in wanted:
            continue
        for line in path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                rec.setdefault("campaign", path.stem)
                records.append(rec)
    return records


def _log_start(log: Path) -> dt.datetime | None:
    """First [MM/DD/YY HH:MM:SS] timestamp in a per-pair log (the runner clock is UTC)."""
    with open(log, errors="replace") as fh:
        for i, line in enumerate(fh):
            m = re.match(r"\[(\d\d/\d\d/\d\d \d\d:\d\d:\d\d)\]", line)
            if m:
                return dt.datetime.strptime(m.group(1), "%m/%d/%y %H:%M:%S").replace(tzinfo=dt.timezone.utc)
            if i > 200:
                break
    return None


def in_progress_runs(root: Path, records: list[dict], campaign: list[str] | str | None = None, max_age_h: float = 6) -> list[dict]:
    """Pairs whose per-pair log exists but which have no manifest record yet (the pair still running): synthesise
    a run record so their finished attempts show up. Only logs written in the last max_age_h hours count, and the
    results directory is the one SREGym created at the pair's start (its name is the start time, MMDD_HHMM)."""
    out = []
    finished = {}
    for r in records:
        if r.get("finished_at"):
            finished[r.get("log")] = max(finished.get(r.get("log"), 0.0), dt.datetime.fromisoformat(r["finished_at"]).timestamp())
    recorded_dirs = {r.get("results_dir") for r in records}
    judge = {r.get("campaign"): r.get("judge_model") for r in records if r.get("judge_model")}
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    wanted = [campaign] if isinstance(campaign, str) else list(campaign or [])
    for camp_dir in sorted(p for p in (root / "campaigns").glob("*") if p.is_dir()):
        if wanted and camp_dir.name not in wanted:
            continue
        for log in sorted(camp_dir.glob("*.log")):
            rel = str(log.relative_to(root.parent))
            if log.stat().st_mtime <= finished.get(rel, 0.0) + 60 or now - log.stat().st_mtime > max_age_h * 3600:
                continue
            arm, _, model = log.stem.partition("__")
            started = _log_start(log)
            if not model or started is None:
                continue
            stamp = started.strftime("%m%d_%H%M")
            cands = sorted(d for d in root.iterdir() if d.is_dir() and re.fullmatch(r"\d{4}_\d{4}", d.name) and d.name >= stamp
                           and (d / arm).is_dir() and str(d.relative_to(root.parent)) not in recorded_dirs)
            if not cands:
                continue
            d = cands[0]
            m = re.search(r"_t(\d)$", arm)
            tier = int(m.group(1)) if m else (2 if arm.endswith("_clickstack") else None)
            out.append({"campaign": camp_dir.name, "agent": arm, "model": model, "tier": tier, "kubectl": "_only_" not in arm,
                        "exit_code": None, "results_dir": str(d.relative_to(root.parent)), "log": rel, "started_at": started.isoformat(),
                        "judge_model": judge.get(camp_dir.name), "in_progress": True})
    return out


def _iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _mcp_args(inp: dict) -> tuple[str, str | None]:
    """Short description of a ClickStack tool call, and the full SQL when there is one."""
    sql = inp.get("sql") or inp.get("query")
    if not sql and isinstance(inp.get("queries"), list):
        sql = "\n\n".join(str(q.get("sql", "")) for q in inp["queries"] if isinstance(q, dict))
    parts = []
    for key in ("where", "groupBy", "select", "metricName", "startTime", "endTime", "sourceId", "namePattern", "traceId"):
        if key in inp and inp[key] not in ("", None, []):
            val = inp[key]
            parts.append(f"{key}={json.dumps(val)[:160]}")
    short = "; ".join(parts) if parts else (("sql: " + sql[:160]) if sql else json.dumps(inp)[:160])
    return short, (sql if sql else None)


def _result_is_error(block: dict, text: str, step: dict) -> bool:
    """True when the harness flagged the tool result, or an MCP result says isError, or a shell command reported a non-zero exit."""
    if block.get("is_error"):
        return True
    head = (text or "")[:400]
    if step["kind"] == "mcp":
        return bool(re.search(r'"isError"\s*:\s*true', head)) or head.startswith("Error") or bool(re.search(r'"status"\s*:\s*"(error|failed)"', head))
    return bool(re.match(r"Exit code [1-9]", head)) or head.startswith("Error:")


def parse_claude(path: Path) -> dict:
    steps, pending, texts = [], {}, ""
    out = {"steps": steps, "agent_wall_s": 0.0, "turns": 0, "cost_usd": None}
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _iso(e.get("timestamp"))
        if e.get("type") == "result":
            out["agent_wall_s"] += (e.get("duration_ms") or 0) / 1000
            out["turns"] += e.get("num_turns") or 0
            if e.get("total_cost_usd") is not None:
                out["cost_usd"] = max(out["cost_usd"] or 0, e["total_cost_usd"])
            continue
        msg = e.get("message")
        content = (msg.get("content") if isinstance(msg, dict) else None) or []
        if isinstance(content, str):
            content = []
        if e.get("type") == "assistant":
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text"):
                    texts = b["text"]
                elif b.get("type") == "tool_use":
                    name = b.get("name", "")
                    inp = b.get("input") or {}
                    if name.startswith(MCP_PREFIXES):
                        short, sql = _mcp_args(inp)
                        tool = name
                        for prefix in MCP_PREFIXES:
                            tool = tool.replace(prefix, "")
                        step = {"kind": "mcp", "tool": tool, "args": short, "sql": sql, "area": area_of(tool, short, sql)}
                    elif name == "Bash":
                        cmd = str(inp.get("command", ""))
                        scripted = scripted_mcp(cmd) if "kubectl" not in cmd else None
                        if scripted:
                            step = {"kind": "mcp", "tool": scripted, "args": "scripted: " + cmd[:280], "sql": None, "area": area_of(scripted, cmd, None), "scripted": True}
                        else:
                            step = {"kind": "kubectl" if "kubectl" in cmd else "shell", "tool": "Bash", "args": cmd[:300], "sql": None,
                                    "kv": parse_kubectl(cmd), "probe": is_probe_shell(cmd)}
                    elif name == "Skill":
                        step = {"kind": "skill", "tool": "Skill", "args": str(inp.get("skill", "")), "sql": None}
                    else:
                        step = {"kind": "other", "tool": name, "args": json.dumps(inp)[:200], "sql": None}
                    step.update({"i": len(steps) + 1, "ts": ts, "pre_text": texts[-400:], "error": False, "error_text": "", "dur_s": None})
                    steps.append(step)
                    pending[b.get("id")] = step
                    texts = ""
        elif e.get("type") == "user":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    step = pending.pop(b.get("tool_use_id"), None)
                    if step is None:
                        continue
                    if ts and step["ts"]:
                        step["dur_s"] = round(ts - step["ts"], 1)
                    body = b.get("content")
                    text = body if isinstance(body, str) else " ".join(x.get("text", "") for x in body if isinstance(x, dict)) if isinstance(body, list) else ""
                    if _result_is_error(b, text, step):
                        step["error"] = True
                        step["error_text"] = text[:300]
    return out


def parse_codex(run_dir: Path) -> dict:
    """Codex CLI --json output (codex.txt): item.completed events for mcp_tool_call, command_execution, agent_message."""
    steps = []
    out = {"steps": steps, "agent_wall_s": None, "turns": 0, "cost_usd": None}
    texts = ""
    path = run_dir / "codex.txt"
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "turn.completed":
            out["turns"] += 1
            continue
        item = e.get("item") if e.get("type") == "item.completed" else None
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "agent_message":
            texts = str(item.get("text", ""))
        elif t == "mcp_tool_call":
            inp = item.get("arguments") or {}
            short, sql = _mcp_args(inp if isinstance(inp, dict) else {})
            failed = str(item.get("status", "")).lower() in ("failed", "error") or bool(item.get("error"))
            tool = str(item.get("tool", ""))
            steps.append({"i": len(steps) + 1, "ts": None, "kind": "mcp", "tool": tool, "args": short, "sql": sql, "area": area_of(tool, short, sql),
                          "pre_text": texts[-400:], "error": failed, "error_text": str(item.get("error") or "")[:300], "dur_s": None})
            texts = ""
        elif t == "command_execution":
            cmd = str(item.get("command", ""))
            scripted = scripted_mcp(cmd) if "kubectl" not in cmd else None
            if scripted:
                steps.append({"i": len(steps) + 1, "ts": None, "kind": "mcp", "tool": scripted, "args": "scripted: " + cmd[:280], "sql": None,
                              "area": area_of(scripted, cmd, None), "scripted": True, "pre_text": texts[-400:],
                              "error": (item.get("exit_code") not in (0, None)), "error_text": "", "dur_s": None})
            else:
                steps.append({"i": len(steps) + 1, "ts": None, "kind": "kubectl" if "kubectl" in cmd else "shell", "tool": "shell", "args": cmd[:300], "sql": None,
                              "kv": parse_kubectl(cmd), "probe": is_probe_shell(cmd),
                              "pre_text": texts[-400:], "error": (item.get("exit_code") not in (0, None)), "error_text": "", "dur_s": None})
            texts = ""
    return out


def attempt_artifacts(run_dir: Path) -> dict:
    out = {"trace": {"steps": [], "agent_wall_s": None, "turns": 0, "cost_usd": None}, "tokens": None, "skill_read": None}
    if not run_dir.is_dir():
        return out
    claude = next(iter(run_dir.rglob("claude-code.txt")), None)
    if claude:
        out["trace"] = parse_claude(claude)
        out["skill_read"] = any(s["kind"] == "skill" for s in out["trace"]["steps"])
    elif (run_dir / "codex.txt").exists():
        out["trace"] = parse_codex(run_dir)
        out["skill_read"] = (run_dir / "AGENTS.md").exists() or (run_dir / "clickstack-run.json").exists() or (run_dir / "chsql-run.json").exists()
    for path in sorted(run_dir.glob("*results*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and isinstance(data.get("usage_metrics"), dict):
            u = data["usage_metrics"]
            out["tokens"] = {k: u.get(k) for k in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_input_tokens", "reasoning_output_tokens", "total_tokens")}
            break
    return out


def fault_text(run_dir: Path) -> str:
    p = run_dir / "fault-spec.json"
    if not p.exists():
        return ""
    try:
        rc = json.loads(p.read_text()).get("root_cause", "")
    except Exception:  # noqa: BLE001
        return ""
    return rc.split("||", 1)[-1].strip() if "||" in rc else rc


TOOL_RENAME_AT = "2026-11-18T02:38:00+00:00"  # the managed ClickStack MCP server renamed a tool and added one


def mcp_version_of(run: dict) -> str:
    mcp = run.get("mcp") or {}
    if mcp.get("version"):
        return f"{mcp['version']} ({mcp.get('tool_count')} tools)"
    started = run.get("started_at") or ""
    if started and started < TOOL_RENAME_AT:
        return "before 2026-11-18 (35 tools)"
    return "2.45.0 (36 tools, inferred)" if started else "unknown"


def load_attempts(root: Path, runs: list[dict]) -> list[dict]:
    attempts = []
    for run in runs:
        rd = run.get("results_dir")
        if not rd:
            continue
        results_dir = Path(rd) if Path(rd).is_absolute() else root.parent / rd
        agent = run["agent"]
        # SREGym writes a cumulative snapshot of the whole run's results table into each problem's CSV, and a
        # _running_ copy while a problem is open, so keep the latest row per (problem, attempt) across all files.
        latest: dict[tuple[str, int], dict] = {}
        for csv_path in sorted((results_dir / agent).glob("*/*_results.csv"), key=lambda q: q.stat().st_mtime):
            with open(csv_path, newline="") as fh:
                for row in csv.DictReader(fh):
                    try:
                        n = int(row.get("attempt") or 1)
                    except ValueError:
                        n = 1
                    latest[(row.get("problem_id") or csv_path.parent.name, n)] = row
        for (problem, n), row in sorted(latest.items()):
            run_dir = results_dir / agent / problem / f"run_{n}"
            art = attempt_artifacts(run_dir)
            tr = art["trace"]
            steps = tr["steps"]
            mcp = [s for s in steps if s["kind"] == "mcp"]
            tools = Counter(s["tool"] for s in mcp)
            tool_err = Counter(s["tool"] for s in mcp if s["error"])
            kverbs = Counter(t for s in steps for t in s.get("kv", []))
            areas = Counter(s.get("area", "other") for s in mcp)
            shell_kinds = Counter(shell_class(s["args"]) for s in steps if s["kind"] == "shell")
            servers = Counter(mcp_server_of(s["tool"]) for s in mcp)
            n_probe = sum(1 for s in steps if s.get("probe")) + sum(1 for s in steps for t in s.get("kv", []) if kubectl_class(t) == "probe")
            n_fix = sum(1 for s in steps for t in s.get("kv", []) if kubectl_class(t) == "fix")
            checks = parse_checklist(row.get("Diagnosis.checklist") or "")
            dims = parse_dims(row.get("Diagnosis.dimensions") or "")
            n_calls = len(steps)
            n_errors = sum(1 for s in steps if s["error"])
            verdict = as_bool(row.get("Diagnosis.success"))
            mode = "pass" if verdict is True else failure_mode(checks, row.get("Diagnosis.submission") or "", n_errors, n_calls) if verdict is False else "unscored"
            attempts.append({
                "campaign": run.get("campaign"), "cluster": run.get("cluster") or "c1", "run_started_at": run.get("started_at"),
                "model": run["model"], "agent": agent, "harness": harness_of(agent), "mcp_version": mcp_version_of(run),
                "family": family_of(agent, run.get("tier")), "tier": run.get("tier"), "kubectl": bool(run.get("kubectl")),
                "judge_model": run.get("judge_model"), "problem": problem, "attempt": n,
                "diagnosis": as_bool(row.get("Diagnosis.success")), "composite": as_float(row.get("Diagnosis.composite_score")),
                "mitigation": as_bool(row.get("Mitigation.success")), "submission": (row.get("Diagnosis.submission") or "")[:3000],
                "judge_reasoning": (row.get("Diagnosis.reasoning") or "")[:6000], "run_status": row.get("run_status") or "",
                "timed_out": as_bool(row.get("timed_out")), "deploy_failed": as_bool(row.get("deploy_failed")),
                "incomplete_reason": row.get("incomplete_reason") or "", "unscored": row.get("Diagnosis.unscored_reason") or "",
                "ttd_s": as_float(row.get("TTL")) or as_float(row.get("phase.stage:diagnosis.duration_s")),
                "ttm_s": as_float(row.get("TTM")) if as_bool(row.get("Mitigation.success")) is not None else None,
                "blocked_requests": as_float(row.get("blocked_requests")),
                "tokens": art["tokens"], "skill_read": art["skill_read"], "agent_wall_s": tr["agent_wall_s"], "turns": tr["turns"],
                "cost_usd": tr["cost_usd"], "n_mcp": len(mcp), "n_kubectl": sum(1 for s in steps if s["kind"] == "kubectl"),
                "n_shell": sum(1 for s in steps if s["kind"] == "shell"), "n_sql": sum(1 for s in mcp if s["sql"]),
                "n_errors": n_errors, "tools": dict(tools), "tool_err": dict(tool_err), "kverbs": dict(kverbs), "areas": dict(areas),
                "n_probe": n_probe, "n_fix": n_fix, "checks": checks, "dims": dims, "mode": mode, "steps": steps,
                "n_scripted": sum(1 for s in mcp if s.get("scripted")),
                "n_cs": servers.get("clickstack", 0), "n_sqlmcp": servers.get("sql", 0), "n_obsmcp": servers.get("obs", 0), "shell_kinds": dict(shell_kinds),
                "fault": fault_text(run_dir), "run_dir": str(run_dir.relative_to(root.parent)) if run_dir.is_dir() else "",
            })
    return attempts


# ----------------------------------------------------------------------------- page

CSS = """
:root{color-scheme:light dark;--surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--good:#0ca30c;--bad:#d03b3b;--warn:#fab219;--border:rgba(11,11,11,.10)}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--plane:#0d0d0d;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10)}}
body{margin:0;background:var(--plane);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header{padding:20px 28px 8px}h1{margin:0 0 4px;font-size:22px}h2{font-size:17px;margin:22px 0 8px}h3{margin:0 0 4px;font-size:15px}.muted{color:var(--muted)}.small{font-size:11px;color:var(--ink2)}
main{padding:0 28px 40px}.tiles{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}.tile{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 16px;min-width:130px}
.tile .num{font-size:24px;font-weight:600}.tile .lbl{color:var(--ink2);font-size:12px}
.filters{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px 12px;margin:8px 0 16px;position:sticky;top:0;z-index:2}
.filters .grp{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center}.filters .sep{width:1px;height:18px;background:var(--grid)}.filters select,.filters input[type=search],.filters button{font:inherit;padding:4px 8px;border:1px solid var(--axis);border-radius:6px;background:var(--surface);color:var(--ink)}
nav.tabs{display:flex;gap:4px;border-bottom:1px solid var(--grid);margin:16px 0 12px;flex-wrap:wrap}nav.tabs button{background:none;border:0;border-bottom:2px solid transparent;color:var(--ink2);padding:8px 14px;font:inherit;cursor:pointer}
nav.tabs button.active{color:var(--ink);border-bottom-color:var(--ink)}.panel{display:none}.panel.active{display:block}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin:0 0 14px}
table{border-collapse:collapse;width:100%;margin-top:8px;background:var(--surface)}th,td{border-bottom:1px solid var(--grid);padding:6px 8px;text-align:left;vertical-align:top}thead th{color:var(--ink2);font-weight:600;font-size:12px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.rate{text-align:center;min-width:84px}.rate b,.rate .small{color:inherit}
.swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:baseline}
.st{display:inline-block;padding:1px 6px;border-radius:4px;font-size:12px;font-weight:600;margin:1px 2px 1px 0;white-space:nowrap}.st.good{color:#fff;background:var(--good)}.st.bad{color:#fff;background:var(--bad)}.st.warn{color:#0b0b0b;background:var(--warn)}.st.muted{color:var(--muted);border:1px solid var(--grid)}
.bars{display:grid;grid-template-columns:minmax(200px,max-content) 1fr 210px;gap:6px 10px;align-items:center;margin:8px 0}.bar-track{height:10px;background:var(--grid);border-radius:0 4px 4px 0}.bar-fill{height:10px;border-radius:0 4px 4px 0}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin:4px 0 8px;font-size:12px;color:var(--ink2)}
.trace{font-size:12px}.trace td{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;white-space:pre-wrap;word-break:break-word}.trace td.res{max-height:120px;overflow:auto;display:block;max-width:360px}.trace td.sqlcol{min-width:420px}.fault{min-width:360px;max-width:520px}.pre{white-space:pre-wrap;font-size:12px;color:var(--ink2);max-width:900px}
details summary{cursor:pointer;color:var(--ink2)}.hidden{display:none!important}.row-exp td{background:var(--plane)}
.info{display:inline-block;width:14px;height:14px;line-height:13px;border-radius:50%;border:1px solid var(--axis);color:var(--ink2);font:600 10px/13px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;text-align:center;margin-left:4px;cursor:help;position:relative;vertical-align:1px;white-space:normal}
.info::after{content:attr(data-tip);position:absolute;left:50%;top:20px;transform:translateX(-50%);width:max-content;max-width:320px;background:var(--ink);color:var(--plane);padding:8px 10px;border-radius:6px;font-size:12px;font-weight:400;line-height:1.4;text-align:left;white-space:normal;opacity:0;pointer-events:none;transition:opacity .12s;z-index:9;box-shadow:0 4px 14px rgba(0,0,0,.25)}
.info:hover::after,.info:focus::after,.info.show::after{opacity:1}
th.num .info::after,th:last-child .info::after{left:auto;right:0;transform:none}th:first-child .info::after{left:0;transform:none}.filters .info::after{top:22px}
#f-problems-box summary{cursor:pointer;list-style:none}#f-problems-box summary::-webkit-details-marker{display:none}#f-problems-box[open] summary{margin-bottom:4px}
body.hide-mit .mit{display:none}
.phases{display:flex;gap:6px;align-items:center;flex-basis:100%;margin-bottom:4px}.phases button.phase{font:inherit;font-weight:600;padding:6px 14px;border:1px solid var(--axis);border-radius:999px;background:var(--surface);color:var(--ink2);cursor:pointer}.phases button.phase.active{background:var(--ink);color:var(--plane);border-color:var(--ink)}
details.camps summary{cursor:pointer;list-style:none}details.camps summary::-webkit-details-marker{display:none}details.camps[open] summary{margin-bottom:4px}details.camps label{display:inline-block;margin-right:8px}
table.rows-sep tbody tr>th,table.rows-sep tbody tr>td{border-top:2px solid var(--axis);padding-top:9px;padding-bottom:9px}table.rows-sep tbody tr:nth-child(even)>th,table.rows-sep tbody tr:nth-child(even)>td{background:var(--plane)}table.rows-sep tbody tr>th{font-weight:600}
.sqlconn label{margin-right:12px}.sqlconn input{font:inherit;padding:3px 6px;border:1px solid var(--axis);border-radius:6px;background:var(--surface);color:var(--ink)}
.sqlgrid{display:grid;grid-template-columns:minmax(260px,1fr) 2fr;gap:12px;margin:10px 0}.sqlgrid textarea{width:100%;box-sizing:border-box;font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;border:1px solid var(--axis);border-radius:6px;background:var(--surface);color:var(--ink);padding:6px}
.sqlgrid select,#ch-run{font:inherit;padding:4px 8px;border:1px solid var(--axis);border-radius:6px;background:var(--surface);color:var(--ink)}#ch-result{overflow:auto;max-height:70vh}
.probs{columns:3;column-gap:18px;max-height:240px;overflow:auto;font-size:12px;padding:4px 0}.probs label{display:block;break-inside:avoid;white-space:nowrap}.probs-actions{column-span:all;margin-bottom:4px}.probs-actions button{font:inherit;font-size:12px;padding:2px 6px}
"""

JS = r"""
const DATA = __DATA__;
const FAM_ORDER = __FAM_ORDER__; const FAM_LABEL = __FAM_LABEL__; const SERIES = __SERIES__;
const CH = __CH__;   // {endpoint, database, schema: {table: [[column, type], ...]}}
const SETS = __SETS__; const KV_EQUIV = __KV_EQUIV__; const AREA_LABEL = __AREA_LABEL__; const AREA_MIN_TIER = __AREA_MIN_TIER__; const FAILURE_MODES = __FAILURE_MODES__; const NOTES_HTML = __NOTES__;
const AREAS = ['events','objects','changes','logs','metrics','traces','node-state','machine-logs'];
const dataTier = f => (f==='stock'||f==='obs_only')? null : (/_t(\d)$/.test(f)? Math.min(3, +f.match(/_t(\d)$/)[1]) : 2);   // tier 4 ships tier-3 data
const OBS_MISSING = new Set(['events','objects','changes','node-state','machine-logs']);   // not in SREGym's Prometheus/Loki/Jaeger
let REF_ITEMS = [];
const models = [...new Set(DATA.map(a=>a.model))].sort(); const fams = FAM_ORDER.filter(f=>DATA.some(a=>a.family===f));
const harnesses = [...new Set(DATA.map(a=>a.harness))].sort(); const campaigns = [...new Set(DATA.map(a=>a.campaign||''))].sort(); const problems = [...new Set(DATA.map(a=>a.problem))].sort();
const colour = m => SERIES[models.indexOf(m) % SERIES.length];
const esc = s => String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const HELP = {
 attempts: "Attempts in view. One attempt is one agent run on one problem from a fresh deployment and empty ClickStack tables; each problem is run three times per model and arm. Unfinished or undeployed attempts carry a yellow mark and are left out of the rates.",
 pass1: "pass@1: the share of scored attempts whose written diagnosis the judge accepted. The judge (claude-sonnet-5-5) works through a checklist against the fault specification; a composite score of 0.70 or more passes. The strictest view: every attempt counts on its own.",
 passk: "pass@k: the share of (model, arm, problem) cells where at least one of the k attempts (k = 3 here) was accepted. The bracketed number is how many cells are in view. It answers whether the setup can solve the problem at all given a few tries, and shows how much of a pass@1 gap is inconsistency rather than inability.",
 mitigation: "Share of attempts where SREGym's live oracle confirmed the fix after the agent applied it and submitted. Only arms with kubectl can fix; ClickStack-only arms stop after the diagnosis and show –.",
 ttd: "Mean time to diagnosis over all scored attempts, right or wrong: SREGym's clock from agent start to the diagnosis submission, including every tool call and model response.",
 ttdc: "Mean time to diagnosis over accepted diagnoses only. Wrong answers can be fast (a quick guess) or slow (a 30-minute timeout), so this isolates how quickly the setup reaches the right answer.",
 ttm: "Mean time to mitigation over attempts whose fix was confirmed: agent start to the mitigation submission.",
 tokens: "Mean total tokens per attempt as the harness reports them at the end of the run: input, output, cached input and cache-creation tokens. In long runs most of it is cached input.",
 mcp: "Mean number of MCP tool calls per attempt, all servers together: ClickStack's tools (search, table, timeseries, SQL, service health, event patterns, trace tools) in the ClickStack arms, the ClickHouse server's run_query and list tools in the SQL arms, or SREGym's Prometheus, Loki and Jaeger tools in the telemetry-without-kubectl arm. Calls an agent made through an MCP client it wrote itself in the shell (Codex did this when the SSE servers would not attach) are counted here too and flagged in the attempt detail. In brackets: their share of all tool calls in the cell (MCP, kubectl and other shell commands).",
 kubectl: "Mean number of kubectl invocations per attempt (several in one shell command count separately), through SREGym's filtering proxy. Zero for arms without cluster access. In brackets: their share of all tool calls in the cell.",
 calls_total: "Mean tool calls per attempt, everything the agent ran: MCP tools, kubectl, and other shell commands. The columns to the right split it; their shares add up to 100%.",
 cs: "Mean calls per attempt to ClickStack's MCP tools (search, table, timeseries, sql, service_health, event_patterns, trace tools, list and describe sources). In brackets: their share of all tool calls in the cell. Only the ClickStack arms have these.",
 sqlmcp: "Mean calls per attempt to the ClickHouse SQL MCP server (run_query, list_tables, list_databases). In brackets: share of all tool calls. Only the ClickHouse SQL arms have these.",
 obsmcp: "Mean calls per attempt to SREGym's own Prometheus, Loki and Jaeger MCP tools (get_metrics, get_logs, get_traces ...). In brackets: share of all tool calls. Only the stock-telemetry-without-kubectl arm has these; calls Codex made through a client it wrote itself are included.",
 othershell: "Mean shell commands per attempt that are not kubectl and not a telemetry tool call, with their share of all tool calls. What they are, in every arm: the curl that submits the diagnosis (1 to 2 per attempt), reading the skill or the run's files (about 2), live probes such as curl or dig against a service, writing or running a small script, and plain text processing (grep, jq, sort). The Tool use tab breaks them down per arm.",
 sql: "Share of the ClickStack MCP calls that were clickstack_sql, raw SQL instead of the search, table and timeseries tools. Shown only when ClickStack arms are in view.",
 errors: "Share of all tool calls (ClickStack, kubectl and other shell) that returned an error: invalid arguments, results too large for the harness, blocked commands, non-zero exits.",
 cost: "Mean API cost per attempt as the harness reports it. Claude Code reports a dollar figure; Codex reports none, so Codex rows show –.",
 problems: "Distinct problems among the attempts in view. SREGym-Lite has 17.",
 matrix: "Each cell is diagnosis pass@1 for that model and arm, with accepted/scored underneath and, for kubectl arms, the mitigation count. Darker is higher.",
 allarms: "All arms in view pooled for the model.",
 arm: "stock: SREGym's unchanged agent: kubectl through the filtering proxy, with Prometheus, Loki and Jaeger deployed in the cluster and reachable through it (no MCP tools; the stock clients attach none). kubectl + ClickStack: the same plus the ClickStack MCP tools, with tier 2 state and skill. ClickStack only, tier N: no cluster access, telemetry alone, diagnosis only. Tier 0: logs with pod labels, events, metrics, traces. Tier 1: plus snapshots and changes of the core Kubernetes objects. Tier 2: plus every kind, control-plane and machine logs. Tier 3: plus node settings. Tier 4: the same data as tier 3 plus investigation rules in the skill (read whole manifests, look for a second fault, name the origin not the victim). Stock telemetry MCP only: SREGym's own MCP server's Prometheus, Loki and Jaeger tools (get_metrics, get_logs, get_traces and so on), no kubectl; the only arm that uses those tools. kubectl + ClickHouse SQL and ClickHouse SQL only, tier N: the same arms with the open-source ClickHouse MCP server (run_query, list_tables) instead of ClickStack's tools, over the same data; they measure what ClickStack's purpose-built tools add over raw SQL.",
 harness: "The agent program driving the model: Claude Code for Claude models, Codex for OpenAI models. Each has its own tool loop and prompts, so compare models within a harness.",
 campaign: "lite-sweep: the Lite sweep, 17 SREGym-Lite problems × 3 attempts for the first six arms. phase1: the other arms on the same problems. phase2: every arm on the 54 further problems. *-fill: re-runs of attempts a campaign could not deploy or finish. anthropic-sweep, codex-shakedown, sql-sweep*, phase1-shakedown: pilots and checks, a problem or two each. Use the phase buttons above for the usual selections.",
 balanced: "Keep only problems with a scored attempt in every selected model × arm cell, so arms are compared on the same problems when some attempts are missing.",
 saturated: "A saturated problem is one that the reference arms solve essentially every time, so it cannot separate setups. Definition: over the selected campaigns, models and harnesses, and the reference arms chosen in 'judged on' (kubectl arms by default), the problem's overall pass rate is at least the first threshold and no single model × arm cell is below the second (67% means at most one failed attempt out of three). A problem needs scored attempts in at least three reference cells before it can count as saturated, so a campaign that has only started does not hide problems on one cell's say-so. The list below the filters shows which problems qualify; with the switch on they are removed from every number on the page, whatever arms are in view.",
 sat_ref: "Which arms decide whether a problem is saturated. kubectl arms: stock and every kubectl + telemetry arm, the agents with cluster access (the default, and the reading that a problem is 'always solved'). stock arm: SREGym's unchanged agent alone. selected arms: whatever arms are ticked, which with telemetry-only arms in view usually saturates nothing. telemetry-only arms: the arms without kubectl. The arm filter itself does not change the saturation judgement.",
 phases: "Phase 1 is every arm on the 17 SREGym-Lite problems: the Lite sweep (stock, kubectl + ClickStack tier 2, ClickStack only tiers 0 to 3) together with the phase1 campaign that added the other arms. Phase 2 is every arm on the 54 further problems of the 71-problem set. Everything adds the pilots and shakedowns. The buttons set the campaign checkboxes under 'campaigns'; ?phase=p1, p2 or all in the URL does the same.",
 mit_hide: "Mitigation is attempted only by the kubectl arms and graded by SREGym's live oracle after the agent's fix; the telemetry-only arms stop after the diagnosis. Hiding the mitigation columns (mitigation rate, mean TTM, the fix counts in the matrix, the Fix and TTM columns of the attempts table) keeps the diagnosis comparison compact. Untick to show them; ?mit=1 in the URL does the same.",
 models_default: "claude-fable-5-2, claude-opus-5-5 and gpt-6.1-sol are the study's models and start ticked; claude-fable-5-1 and gpt-6.1-terra ran only in the Lite sweep and start unticked. Tick them to add them to every number.",
 problems_pick: "Tick the problems to include; every number follows. 'exclude saturated' unticks the problems in the saturated list under the current definition, 'all' restores them, 'none' clears them so a few can be ticked by hand.",
 delta: "Each tier's difference from tier 0 among the ClickStack-only arms in view: pass@1 in percentage points; tokens, ClickStack calls and TTD-when-correct as percentage changes.",
 problem: "SREGym problem id: the fault type in the prefix, the application in the suffix (hotel_reservation, astronomy_shop, social_network).",
 fault: "The ground-truth root cause, from the fault specification SREGym writes beside each attempt.",
 diag: "Judge verdict with the composite score in brackets. Green: accepted (0.70 or more). Red: rejected. Yellow: not finished, not deployed or not scored. Hover a mark for the judge's reasoning.",
 fix: "SREGym oracle verdict on the mitigation: ✓ confirmed, ✗ not confirmed, – not attempted (ClickStack-only arms).",
 attempt_no: "Attempt number within the (model, arm, problem) cell, 1 to 3.",
 ttd_a: "Time from agent start to the diagnosis submission.",
 ttm_a: "Time from agent start to the mitigation submission, kubectl arms only.",
 tokens_a: "Total tokens the harness reported for this attempt.",
 mcp_a: "MCP tool calls in this attempt: ClickStack, ClickHouse SQL or SREGym's Prometheus, Loki and Jaeger tools, whichever the arm has.",
 kubectl_a: "Shell commands invoking kubectl in this attempt.",
 sql_a: "MCP calls in this attempt that carried raw SQL.",
 errors_a: "Tool calls in this attempt that returned an error.",
 blocked: "Network requests from the agent container that the sandbox refused. Almost always the harness's own telemetry endpoints (datadoghq.com for Claude Code, chatgpt.com for Codex), nothing the agent needed.",
 cost_a: "API cost the harness reported for this attempt, Claude Code only.",
 status: "SREGym's run status and, for incomplete attempts, the reason: the agent exited early, a timeout, or a deploy that failed after every try.",
 tool: "MCP tool name (ClickStack, ClickHouse SQL, or SREGym's Prometheus, Loki and Jaeger tools). Tools that write (save, patch, delete, backtest, submit feedback) are refused by the proxy and never appear here.",
 pre: "The agent's last message before it issued the call: its own stated reason for the query.",
 set: "Restrict every number to a named problem set: SREGym-Lite (17 problems), the 54 further problems of the 71-problem set, or the whole set. Lite results stay comparable with the earlier Lite-only campaign.",
 calls: "Mean tool calls per attempt, split into MCP tools (ClickStack, ClickHouse SQL or SREGym's Prometheus/Loki/Jaeger), kubectl invocations (several per shell command count separately), live probes (kubectl exec, port-forward, run/debug pods, and shell curl/dig/nc against the cluster) and fixes (apply, patch, delete, scale, rollout restart ...).",
 areas: "Share of attempts in which the agent queried each telemetry area at least once: Kubernetes events, object snapshots (k8s-objects), object changes (k8s-changes), application logs, metrics, traces, node settings. Classified from the query text and the tool used. kubectl reads are classified the same way for the stock arm (get/describe = objects, get events = events, logs = logs, top = metrics).",
 toptools: "The tools this model and arm used most, as mean calls per attempt. MCP tool names for the telemetry arms; 'verb kind' for kubectl (get pod, describe deployment, logs, exec ...).",
 toolerr: "Share of this cell's tool calls that returned an error.",
 passfail: "Mean calls per attempt among accepted attempts versus rejected ones; a tool used far more in failures than in passes often marks where the agent got stuck.",
 gaps: "Gap problems: the stock arm (kubectl plus Prometheus, Loki and Jaeger) with the same model passed at least half of its scored attempts and this arm passed fewer than half. Gains: the reverse. Shared failures: both below half. Rates are computed on the problems in view.",
 verdict: "A rule-based reading of each gap: 'live probing' when the stock agent's successful runs leaned on exec, port-forward or debug pods (at least a quarter of their cluster reads), which the telemetry cannot replace; 'not captured at this tier' when the data the stock agent read arrives only at a higher collector tier; 'captured but not queried' when this arm never touched the area the stock agent used; otherwise 'read the right data, wrong conclusion', with the judge-derived failure modes.",
 modes: "Failure modes derived from the judge's checklist and the submission text; the Method tab defines each. 'pass' and 'unscored' are not failures.",
 helpful: "Telemetry the stock agent's successful runs relied on in the gap problems, ranked by how many gap problems needed it, with whether this arm's tier ships it and whether this arm's failing attempts queried it at all.",
 sqltab: "Runs SQL against the ClickHouse database that clickstack/load_results.py fills from the same files this page is built from (one row per attempt, one per tool call). Queries go straight from this browser to the ClickHouse HTTP endpoint with the user and password entered here; they are kept only in this browser if you tick remember. Results are capped at 2,000 rows.",
 sqlwhere: "The filters at the top of the page, expressed as a WHERE clause over the attempts table: campaigns, models, arms, harness, problem set, the problem checklist and the hidden saturated problems. Presets insert it where they say {where}; paste it into your own queries to keep the page and the SQL on the same slice.",
 stockref: "The reference for this model is its stock arm (kubectl + Prometheus/Loki/Jaeger) over the same filters; the arm filter is ignored for the reference so it stays available while you look at other arms.",
};
const PHASES = { p1: {label:'Phase 1 · Lite problems, every arm', campaigns:['lite-sweep','lite-sweep-fill','phase1','phase1-fill']},
                 p2: {label:'Phase 2 · 54 further problems', campaigns:['phase2','phase2-fill']},
                 all: {label:'Everything', campaigns:null} };
function applyPhase(key){ const ph=PHASES[key]; if(!ph) return; document.querySelectorAll('.f-camp').forEach(x=>{ x.checked = ph.campaigns? ph.campaigns.includes(x.value) : true; }); }
function currentPhase(){ const on=new Set([...document.querySelectorAll('.f-camp:checked')].map(x=>x.value)); for(const [k,ph] of Object.entries(PHASES)){ const want=new Set((ph.campaigns||campaigns).filter(c=>campaigns.includes(c))); if(want.size===on.size && [...want].every(c=>on.has(c))) return k; } return null; }
const DEFAULT_MODELS = ['claude-fable-5-2','claude-opus-5-5','gpt-6.1-sol'];   // the study's models; others (earlier pilots) start unticked
const defaultModel = m => models.some(x=>DEFAULT_MODELS.includes(x)) ? DEFAULT_MODELS.includes(m) : true;
const SHOW_HELP = new URLSearchParams(location.search).get('help');  // ?help=passk keeps that explanation open (for links and screenshots)
const H = (label,key) => `${label}${label?' ':''}<span class="info${SHOW_HELP===key?' show':''}" tabindex="0" data-tip="${esc(HELP[key]||'')}">i</span>`;
const pct = (n,d) => d? Math.round(100*n/d)+'%' : '–'; const mean = xs => { const v=xs.filter(x=>typeof x==='number'&&isFinite(x)); return v.length? v.reduce((a,b)=>a+b,0)/v.length : null; };
const fmt = (x,d=1) => x==null? '–' : (Math.abs(x)>=1000? Math.round(x).toLocaleString() : x.toFixed(d));
const fmtS = x => x==null? '–' : (x>=90? (x/60).toFixed(1)+' min' : Math.round(x)+' s');
const tokTotal = a => a.tokens? (a.tokens.total_tokens ?? ((a.tokens.input_tokens||0)+(a.tokens.output_tokens||0))) : null;
const SEQ=["#cde2fb","#b7d3f6","#9ec5f4","#86b6ef","#6da7ec","#5598e7","#3987e5","#2a78d6","#256abf","#1c5cab","#184f95","#104281","#0d366b"];
const seq = (n,d) => d? SEQ[Math.min(SEQ.length-1, Math.round((n/d)*(SEQ.length-1)))] : 'transparent';
const fg = (n,d) => (d && n/d>0.55)? '#ffffff' : '#0b0b0b'; const cellStyle = (n,d) => `background:${seq(n,d)};color:${fg(n,d)}`;

function mark(a){ const sc = a.composite!=null? ` (${a.composite.toFixed(2)})` : '';
  if(a.deploy_failed) return '<span class="st warn">! deploy</span>'; if(a.unscored) return '<span class="st warn">! unscored</span>';
  if(a.diagnosis===true) return `<span class="st good" title="${esc(a.judge_reasoning.slice(0,600))}">✓ pass${sc}</span>`;
  if(a.diagnosis===false) return `<span class="st bad" title="${esc(a.judge_reasoning.slice(0,600))}">✗ fail${sc}</span>`;
  if(a.timed_out) return '<span class="st warn">! timeout</span>'; if(a.run_status&&a.run_status!=='complete') return `<span class="st warn">! ${esc(a.run_status)}</span>`; if(!a.run_status) return '<span class="st warn">! unfinished</span>'; return '<span class="st muted">– none</span>'; }

function agg(items){ const scored=items.filter(a=>a.diagnosis!==null); const passes=scored.filter(a=>a.diagnosis).length;
  const cells={}; for(const a of items){ const k=a.model+'|'+a.family+'|'+a.problem; cells[k]=cells[k]||[]; cells[k].push(a); }
  const cellList=Object.values(cells); const anyPass=cellList.filter(c=>c.some(a=>a.diagnosis)).length;
  const mit=items.filter(a=>a.mitigation!==null); const nmcp=items.reduce((s,a)=>s+a.n_mcp,0);
  return { n:items.length, passes, scored:scored.length, pass1: scored.length? passes/scored.length : null, passk: cellList.length? anyPass/cellList.length : null, cells: cellList.length,
    mitPass: mit.filter(a=>a.mitigation).length, mitN: mit.length, ttd: mean(items.filter(a=>a.diagnosis!==null).map(a=>a.ttd_s)), ttdPass: mean(items.filter(a=>a.diagnosis).map(a=>a.ttd_s)),
    ttm: mean(items.filter(a=>a.mitigation).map(a=>a.ttm_s)), tokens: mean(items.map(tokTotal)), cost: mean(items.map(a=>a.cost_usd)),
    mcp: mean(items.map(a=>a.n_mcp)), kubectl: mean(items.map(a=>a.n_kubectl)), calls: mean(items.map(a=>a.n_mcp+a.n_kubectl+a.n_shell)),
    mcpShare: (()=>{const c=items.reduce((s,a)=>s+a.n_mcp+a.n_kubectl+a.n_shell,0); return c? nmcp/c : null;})(), kubectlShare: (()=>{const c=items.reduce((s,a)=>s+a.n_mcp+a.n_kubectl+a.n_shell,0); return c? items.reduce((s,a)=>s+a.n_kubectl,0)/c : null;})(),
    cs: mean(items.map(a=>a.n_cs||0)), sqlmcp: mean(items.map(a=>a.n_sqlmcp||0)), obsmcp: mean(items.map(a=>a.n_obsmcp||0)), shell: mean(items.map(a=>a.n_shell||0)),
    shares: (()=>{const c=items.reduce((s,a)=>s+a.n_mcp+a.n_kubectl+a.n_shell,0); const f=k=>c? items.reduce((s,a)=>s+(a[k]||0),0)/c : null; return {cs:f('n_cs'), sqlmcp:f('n_sqlmcp'), obsmcp:f('n_obsmcp'), kubectl:f('n_kubectl'), shell:f('n_shell')};})(),
    shellKinds: (()=>{const o={}; for(const a of items) for(const [k,v] of Object.entries(a.shell_kinds||{})) o[k]=(o[k]||0)+v; return o;})(),
    sqlShare: nmcp? items.reduce((s,a)=>s+a.n_sql,0)/nmcp : null, errRate: (()=>{const c=items.reduce((s,a)=>s+a.n_mcp+a.n_kubectl+a.n_shell,0); return c? items.reduce((s,a)=>s+a.n_errors,0)/c : null;})(),
    skill: items.filter(a=>a.skill_read).length, unfinished: items.filter(a=>a.diagnosis===null).length }; }

let SATURATED = new Map(); const MIN_REF_CELLS = 3;   // model × arm cells with scored attempts a problem needs before it can count as saturated
function saturatedProblems(items, thr, minCell){
  // a problem is saturated when its overall pass rate is >= thr and no (model, arm) cell is below minCell
  const stat = {};
  for(const a of items){ if(a.diagnosis===null) continue; const s = stat[a.problem] = stat[a.problem] || {p:0,n:0,cells:{}}; s.p += a.diagnosis?1:0; s.n++;
    const k = a.model+'|'+a.family; const c = s.cells[k] = s.cells[k] || [0,0]; c[0] += a.diagnosis?1:0; c[1]++; }
  const out = new Map();
  for(const [prob, s] of Object.entries(stat)){ const rate = s.p/s.n; const cells = Object.values(s.cells); const weakest = Math.min(...cells.map(c=>c[0]/c[1]));
    if(cells.length < MIN_REF_CELLS) continue;   // too few reference cells to call a problem saturated (early in a campaign)
    if(rate >= thr && weakest >= minCell) out.set(prob, {rate, weakest, passes:s.p, n:s.n, cells:cells.length, imperfect: cells.filter(c=>c[0]<c[1]).length}); }
  return out; }
function selected(){ const on = cls => [...document.querySelectorAll(cls+':checked')].map(x=>x.value);
  const M=new Set(on('.f-model')), F=new Set(on('.f-fam')), H=new Set(on('.f-har')), C=new Set(on('.f-camp')); const Q=document.getElementById('f-text').value.toLowerCase();
  const S=document.getElementById('f-set').value; const setP = S? new Set(SETS[S]||[]) : null;
  const PSEL = new Set(on('.f-prob'));
  const base0 = DATA.filter(a=>C.has(a.campaign||'')&&M.has(a.model)&&H.has(a.harness)&&(!setP||setP.has(a.problem)));
  const base = base0.filter(a=>F.has(a.family));
  const ref = document.getElementById('f-sat-ref').value;
  const isK = f => f==='stock' || f.startsWith('kubectl_');
  const refFam = f => ref==='all'? F.has(f) : ref==='stock'? f==='stock' : ref==='kubectl'? isK(f) : !isK(f);
  SATURATED = saturatedProblems(base0.filter(a=>refFam(a.family)), parseFloat(document.getElementById('f-sat-thr').value), parseFloat(document.getElementById('f-sat-cell').value));
  const hideSat = document.getElementById('f-sat').checked;
  const keep = a => PSEL.has(a.problem) && !(hideSat&&SATURATED.has(a.problem));
  REF_ITEMS = base0.filter(keep);
  let items = base.filter(a=>keep(a)&&(Q===''||(a.problem+' '+a.model+' '+a.agent+' '+a.submission).toLowerCase().includes(Q)));
  const cnt=document.getElementById('f-prob-count'); if(cnt) cnt.textContent = `${new Set(items.map(a=>a.problem)).size} of ${problems.length} in view`;
  if(document.getElementById('f-balanced').checked){ const cells=new Set(items.map(a=>a.model+'|'+a.family)); const have={}; for(const a of items) if(a.diagnosis!==null){ (have[a.problem]=have[a.problem]||new Set()).add(a.model+'|'+a.family); }
    const ok=new Set(Object.keys(have).filter(p=>have[p].size===cells.size)); items=items.filter(a=>ok.has(a.problem)); }
  return items; }

const withShare = (n, sh) => `${fmt(n)} <span class="small">(${sh==null?'–':Math.round(100*sh)+'%'})</span>`;
const AGG_COLS = [
  {k:'n', h:['attempts','attempts'], cell:g=>g.n},
  {k:'pass1', h:['pass@1','pass1'], cell:g=>pct(g.passes,g.scored)},
  {k:'passk', h:['pass@k (cells)','passk'], cell:g=>`${g.passk==null?'–':Math.round(100*g.passk)+'%'} <span class="small">(${g.cells})</span>`},
  {k:'mit', h:['mitigation','mitigation'], cell:g=>g.mitN? pct(g.mitPass,g.mitN):'–'},
  {k:'ttd', h:['mean TTD','ttd'], cell:g=>fmtS(g.ttd)},
  {k:'ttdc', h:['TTD when correct','ttdc'], cell:g=>fmtS(g.ttdPass)},
  {k:'ttm', h:['mean TTM','ttm'], cell:g=>fmtS(g.ttm)},
  {k:'tokens', h:['mean tokens','tokens'], cell:g=>fmt(g.tokens,0)},
  {k:'calls', h:['calls / attempt','calls_total'], cell:g=>fmt(g.calls)},
  {k:'cs', h:['ClickStack calls','cs'], cell:g=>withShare(g.cs, g.shares.cs), present:items=>items.some(a=>a.n_cs>0)},
  {k:'sqlmcp', h:['ClickHouse SQL calls','sqlmcp'], cell:g=>withShare(g.sqlmcp, g.shares.sqlmcp), present:items=>items.some(a=>a.n_sqlmcp>0)},
  {k:'obsmcp', h:['Prometheus/Loki/Jaeger calls','obsmcp'], cell:g=>withShare(g.obsmcp, g.shares.obsmcp), present:items=>items.some(a=>a.n_obsmcp>0)},
  {k:'kubectl', h:['kubectl calls','kubectl'], cell:g=>withShare(g.kubectl, g.shares.kubectl), present:items=>items.some(a=>a.n_kubectl>0)},
  {k:'shell', h:['other shell','othershell'], cell:g=>withShare(g.shell, g.shares.shell)},
  {k:'sql', h:['SQL share of ClickStack calls','sql'], cell:g=>g.sqlShare==null?'–':Math.round(100*g.sqlShare)+'%', present:items=>items.some(a=>a.n_cs>0)},
  {k:'err', h:['tool errors','errors'], cell:g=>g.errRate==null?'–':Math.round(100*g.errRate)+'%'},
  {k:'cost', h:['mean cost','cost'], cell:g=>g.cost==null?'–':'$'+g.cost.toFixed(2)},
];
const showMit = () => !document.getElementById('f-mit').checked;
const aggCols = items => AGG_COLS.filter(c=>(!c.present || c.present(items)) && !(['mit','ttm'].includes(c.k) && !showMit()));
const aggHead = cols => `<thead><tr><th></th>${cols.map(c=>`<th class="num">${H(c.h[0],c.h[1])}</th>`).join('')}</tr></thead>`;
function aggRow(label, g, cols){ return `<tr><th>${label}</th>${cols.map(c=>`<td class="num">${c.cell(g)}</td>`).join('')}</tr>`; }

let ITEMS=[]; const DIRTY=new Set(); let ATT_SORTED=[];
function renderCore(items){ const all=agg(items);
  const hideSat = document.getElementById('f-sat').checked; const satList=[...SATURATED.entries()].sort((x,y)=>y[1].rate-x[1].rate);
  document.getElementById('sat-note').innerHTML = satList.length ? `<details ${hideSat?'open':''}><summary>${satList.length} saturated problem(s) under the current definition${hideSat?', hidden from every number on this page':''} (details)</summary><table><thead><tr><th>Problem</th><th class="num">pass rate</th><th class="num">attempts</th><th class="num">weakest model × arm cell</th><th class="num">cells with a failure</th></tr></thead><tbody>${satList.map(([p,s])=>`<tr><td>${esc(p)}</td><td class="num">${Math.round(100*s.rate)}%</td><td class="num">${s.passes}/${s.n}</td><td class="num">${Math.round(100*s.weakest)}%</td><td class="num">${s.imperfect}/${s.cells}</td></tr>`).join('')}</tbody></table></details>` : '<span class="muted">No problem is saturated under the current definition.</span>';
  document.getElementById('tiles').innerHTML = [[all.pass1==null?'–':Math.round(100*all.pass1)+'%',H('diagnosis pass@1','pass1')],[all.passk==null?'–':Math.round(100*all.passk)+'%',H('pass@k over '+all.cells+' cells','passk')],[items.length,H('attempts'+(all.unfinished?` (${all.unfinished} unfinished)`:''),'attempts')],[new Set(items.map(a=>a.problem)).size,H('problems in view'+((hideSat&&SATURATED.size)? ` (${[...SATURATED.keys()].length} saturated hidden)` : ''),'problems')],[fmtS(all.ttdPass),H('mean TTD when correct','ttdc')],[fmt(all.tokens,0),H('mean tokens','tokens')],[fmt(all.mcp),H('MCP calls / attempt','mcp')]].map(([n,l])=>`<div class="tile"><div class="num">${n}</div><div class="lbl">${l}</div></div>`).join('');
  // headlines
  const cols=aggCols(items);
  let h='<table>'+aggHead(cols)+'<tbody>'; for(const f of fams){ const g=agg(items.filter(a=>a.family===f)); if(g.n) h+=aggRow(esc(FAM_LABEL[f]),g,cols);} h+='</tbody></table>';
  let d='';
  for(const [base,tiers,label] of [['only_t0',['only_t1','only_t2','only_t3','only_t4'],'ClickStack-only arms'],['chsql_only_t0',['chsql_only_t1','chsql_only_t2','chsql_only_t3','chsql_only_t4'],'ClickHouse-SQL-only arms']]){ const t0=agg(items.filter(a=>a.family===base)); if(!t0.n) continue;
    d+='<p class="muted">Change against tier 0 ('+label+')'+H('','delta')+': '+tiers.filter(f=>fams.includes(f)).map(f=>{const g=agg(items.filter(a=>a.family===f)); if(!g.n) return ''; const dp=(g.pass1!=null&&t0.pass1!=null)? (Math.round(100*(g.pass1-t0.pass1))+' pts pass@1') : ''; const dt=(g.tokens!=null&&t0.tokens!=null)? Math.round(100*(g.tokens-t0.tokens)/t0.tokens)+'% tokens':''; const dc=(g.mcp!=null&&t0.mcp!=null)? Math.round(100*(g.mcp-t0.mcp)/t0.mcp)+'% MCP calls':''; const dd=(g.ttdPass!=null&&t0.ttdPass!=null)? Math.round(100*(g.ttdPass-t0.ttdPass)/t0.ttdPass)+'% TTD':''; return `<b>${esc(FAM_LABEL[f])}</b>: ${[dp,dt,dc,dd].filter(Boolean).join(', ')}`;}).filter(Boolean).join(' · ')+'</p>'; }
  let hm='<table>'+aggHead(cols)+'<tbody>'; for(const m of models){ const g=agg(items.filter(a=>a.model===m)); if(g.n) hm+=aggRow(`<span class="swatch" style="background:${colour(m)}"></span>${esc(m)}`,g,cols);} hm+='</tbody></table>';
  document.getElementById('headlines').innerHTML = `<h3>By arm</h3>${h}${d}<h3>By model</h3>${hm}`;
  // matrix
  let mx='<table class="matrix"><thead><tr><th>'+H('Model','matrix')+'</th>'+fams.map(f=>`<th>${esc(FAM_LABEL[f])}</th>`).join('')+'<th>'+H('All arms','allarms')+'</th></tr></thead><tbody>';
  for(const m of models){ mx+=`<tr><th><span class="swatch" style="background:${colour(m)}"></span>${esc(m)}</th>`; for(const f of [...fams,null]){ const g=agg(items.filter(a=>a.model===m&&(f===null||a.family===f))); mx+=`<td class="rate" style="${cellStyle(g.passes,g.scored)}"><b>${pct(g.passes,g.scored)}</b><br><span class="small">${g.passes}/${g.scored}${(g.mitN&&showMit())?` · fix ${g.mitPass}/${g.mitN}`:''}</span></td>`; } mx+='</tr>'; }
  mx+='<tr><th>All models</th>'; for(const f of [...fams,null]){ const g=agg(items.filter(a=>f===null||a.family===f)); mx+=`<td class="rate" style="${cellStyle(g.passes,g.scored)}"><b>${pct(g.passes,g.scored)}</b><br><span class="small">${g.passes}/${g.scored}</span></td>`; } mx+='</tr></tbody></table>';
  document.getElementById('matrix').innerHTML=mx;
}
function renderByModel(){ const items=ITEMS;
  document.getElementById('p-model').innerHTML = models.map(m=>{ const mi=items.filter(a=>a.model===m); if(!mi.length) return ''; const g=agg(mi);
    const bars='<div class="bars">'+fams.map(f=>{const gg=agg(mi.filter(a=>a.family===f)); if(!gg.n) return ''; const w=gg.scored? 100*gg.passes/gg.scored:0; return `<div>${esc(FAM_LABEL[f])}</div><div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${colour(m)}"></div></div><div>${pct(gg.passes,gg.scored)} <span class="small">(${gg.passes}/${gg.scored}, TTD ${fmtS(gg.ttdPass)})</span></div>`;}).join('')+'</div>';
    return `<section class="card"><h3><span class="swatch" style="background:${colour(m)}"></span>${esc(m)} <span class="small">${esc(mi[0].harness)}</span></h3><p class="muted">${g.n} attempts · pass@1 ${pct(g.passes,g.scored)} · mean TTD when correct ${fmtS(g.ttdPass)} · mean tokens ${fmt(g.tokens,0)} · mean cost ${g.cost==null?'–':'$'+g.cost.toFixed(2)}</p>${bars}${problemTable(mi, fams, 'family')}</section>`; }).join('');
}
function renderByArm(){ const items=ITEMS;
  document.getElementById('p-arm').innerHTML = fams.map(f=>{ const fi=items.filter(a=>a.family===f); if(!fi.length) return ''; const g=agg(fi);
    const bars='<div class="bars">'+models.map(m=>{const gg=agg(fi.filter(a=>a.model===m)); if(!gg.n) return ''; const w=gg.scored? 100*gg.passes/gg.scored:0; return `<div><span class="swatch" style="background:${colour(m)}"></span>${esc(m)}</div><div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${colour(m)}"></div></div><div>${pct(gg.passes,gg.scored)} <span class="small">(${gg.passes}/${gg.scored}, TTD ${fmtS(gg.ttdPass)})</span></div>`;}).join('')+'</div>';
    const tools={}; for(const a of fi) for(const [t,c] of Object.entries(a.tools)) tools[t]=(tools[t]||0)+c; const tl=Object.entries(tools).sort((x,y)=>y[1]-x[1]).map(([t,c])=>`${esc(t)} ${c}`).join(' · ');
    return `<section class="card"><h3>${esc(FAM_LABEL[f])}</h3><p class="muted">${g.n} attempts · pass@1 ${pct(g.passes,g.scored)} · mean TTD when correct ${fmtS(g.ttdPass)} · mean tokens ${fmt(g.tokens,0)} · ${fmt(g.mcp)} MCP and ${fmt(g.kubectl)} kubectl calls per attempt · SQL share ${g.sqlShare==null?'–':Math.round(100*g.sqlShare)+'%'} · tool errors ${g.errRate==null?'–':Math.round(100*g.errRate)+'%'}</p>${bars}${tl?`<p class="small">MCP tools used: ${tl}</p>`:''}${problemTable(fi, models, 'model')}</section>`; }).join('');
}
function renderByProblem(){ const items=ITEMS;
  const cols=[]; for(const m of models) for(const f of fams) if(items.some(a=>a.model===m&&a.family===f)) cols.push([m,f]);
  let pt='<table class="rows-sep"><thead><tr><th>'+H('Problem','problem')+'</th><th>'+H('Fault','fault')+'</th>'+cols.map(([m,f])=>`<th><span class="swatch" style="background:${colour(m)}"></span>${esc(m)}<br><span class="small">${esc(FAM_LABEL[f])}</span></th>`).join('')+'</tr></thead><tbody>';
  for(const p of problems){ const pi=items.filter(a=>a.problem===p); if(!pi.length) continue; pt+=`<tr><th>${esc(p)}</th><td class="small fault">${esc((pi.find(a=>a.fault)||{}).fault||'')}</td>`+cols.map(([m,f])=>{const c=pi.filter(a=>a.model===m&&a.family===f).sort((x,y)=>x.attempt-y.attempt); const g=agg(c); return `<td>${c.map(mark).join(' ')||'<span class="st muted">–</span>'}<br><span class="small">${c.length? 'TTD '+fmtS(g.ttdPass):''}</span></td>`;}).join('')+'</tr>'; }
  document.getElementById('p-problem').innerHTML=`<section class="card">${pt}</tbody></table></section>`;
}
function renderAttempts(){ const items=[...ITEMS];
  let at=`<table class="runs"><thead><tr><th></th><th>Model</th><th>${H('Arm','arm')}</th><th>${H('Problem','problem')}</th><th>${H('#','attempt_no')}</th><th>${H('Diagnosis','diag')}</th><th class="mit">${H('Fix','fix')}</th><th class="num">${H('TTD','ttd_a')}</th><th class="num mit">${H('TTM','ttm_a')}</th><th class="num">${H('Tokens','tokens_a')}</th><th class="num">${H('MCP','mcp_a')}</th><th class="num">${H('kubectl','kubectl_a')}</th><th class="num">${H('SQL','sql_a')}</th><th class="num">${H('Errors','errors_a')}</th><th class="num">${H('Blocked','blocked')}</th><th class="num">${H('Cost','cost_a')}</th><th>${H('Status','status')}</th></tr></thead><tbody>`;
  items.sort((x,y)=>(x.model+x.family+x.problem).localeCompare(y.model+y.family+y.problem)||x.attempt-y.attempt).forEach((a,i)=>{ const id='r'+i;
    at+=`<tr><td><button class="exp" data-t="${id}">+</button></td><td><span class="swatch" style="background:${colour(a.model)}"></span>${esc(a.model)}</td><td>${esc(FAM_LABEL[a.family])}<br><span class="small">${esc(a.agent)}</span></td><td>${esc(a.problem)}</td><td>${a.attempt}</td><td>${mark(a)}</td><td class="mit">${a.mitigation===true?'✓':a.mitigation===false?'✗':'–'}</td><td class="num">${fmtS(a.ttd_s)}</td><td class="num mit">${fmtS(a.ttm_s)}</td><td class="num">${fmt(tokTotal(a),0)}</td><td class="num">${a.n_mcp}</td><td class="num">${a.n_kubectl}</td><td class="num">${a.n_sql}</td><td class="num">${a.n_errors}</td><td class="num">${a.blocked_requests==null?'–':a.blocked_requests}</td><td class="num">${a.cost_usd==null?'–':'$'+a.cost_usd.toFixed(2)}</td><td class="small">${esc(a.run_status)} ${esc(a.incomplete_reason)}</td></tr>`;
    at+=`<tr id="${id}" class="row-exp hidden"><td></td><td colspan="16" data-i="${i}"></td></tr>`; });
  ATT_SORTED=items;
  document.getElementById('p-attempts').innerHTML=`<section class="card"><p class="muted">${items.length} attempts. <button id="exp-all">Expand all</button> Click + to open an attempt: fault, submitted diagnosis, judge verdict, every tool call in order with arguments, errors and the agent's words before each SQL query.</p>${at}</tbody></table></section>`;
  const fill = r => { const td=r.querySelector('td[data-i]'); if(td && !td.innerHTML) td.innerHTML = detail(ATT_SORTED[+td.dataset.i]); };
  document.querySelectorAll('#p-attempts button.exp').forEach(b=>b.addEventListener('click',()=>{const r=document.getElementById(b.dataset.t); fill(r); r.classList.toggle('hidden'); b.textContent=r.classList.contains('hidden')?'+':'−';}));
  const expAll=document.getElementById('exp-all'); let expanded=new URLSearchParams(location.search).get('expand')==='1'; const setAll=()=>{document.querySelectorAll('#p-attempts .row-exp').forEach(r=>{ if(expanded) fill(r); r.classList.toggle('hidden',!expanded);}); document.querySelectorAll('#p-attempts button.exp').forEach(b=>b.textContent=expanded?'−':'+'); expAll.textContent=expanded?'Collapse all':'Expand all';}; expAll.addEventListener('click',()=>{expanded=!expanded; setAll();}); if(expanded) setAll();
}
function renderQueries(){ const items=ITEMS;
  const sqls=items.flatMap(a=>a.steps.filter(s=>s.sql).map(s=>({a,s}))); const toolsAll={}; for(const a of items) for(const [t,c] of Object.entries(a.tools)) toolsAll[t]=(toolsAll[t]||0)+c;
  let tt=`<section class="card"><h3>MCP tool calls (${Object.values(toolsAll).reduce((x,y)=>x+y,0)} across ${items.length} attempts)</h3><table><thead><tr><th>${H('Tool','tool')}</th><th class="num">Calls</th><th class="num">Per attempt</th></tr></thead><tbody>`+Object.entries(toolsAll).sort((x,y)=>y[1]-x[1]).map(([t,c])=>`<tr><td>${esc(t)}</td><td class="num">${c}</td><td class="num">${(c/items.length).toFixed(1)}</td></tr>`).join('')+'</tbody></table></section>';
  tt+=`<section class="card"><h3>SQL queries (${sqls.length}) with the agent's words immediately before each</h3><table class="trace"><thead><tr><th>Model · arm · problem</th><th>${H("Agent's words before the query",'pre')}</th><th>SQL</th><th>Error</th></tr></thead><tbody>`+sqls.map(({a,s})=>`<tr><td>${esc(a.model)} · ${esc(FAM_LABEL[a.family])} · ${esc(a.problem)} #${a.attempt}</td><td>${esc(s.pre_text||'(no text before the call)')}</td><td class="sqlcol">${esc(s.sql)}</td><td class="res">${s.error?esc(s.error_text||'error'):'–'}</td></tr>`).join('')+'</tbody></table></section>';
  document.getElementById('p-tools').innerHTML=tt;
}
const TAB_RENDER = { 'p-model': renderByModel, 'p-arm': renderByArm, 'p-problem': renderByProblem, 'p-attempts': renderAttempts, 'p-tools': renderQueries,
  'p-tooluse': ()=>renderToolUse(ITEMS, REF_ITEMS), 'p-gaps': ()=>renderGaps(ITEMS, REF_ITEMS),
  'p-sql': ()=>{ const w=document.getElementById('ch-where'); if(w) w.textContent=filtersAsWhere(); else renderSqlTab(); } };
function renderActive(){ const active=document.querySelector('.panel.active'); if(active && DIRTY.has(active.id) && TAB_RENDER[active.id]){ const t0=performance.now(); TAB_RENDER[active.id](); DIRTY.delete(active.id); noteRender(`${active.querySelector('button')? '' : ''}tab rendered in ${Math.round(performance.now()-t0)} ms`); } }
function noteRender(txt){ const n=document.getElementById('render-note'); if(n) n.textContent = `${ITEMS.length} attempts in view · ${txt}`; }
function render(){ const t0=performance.now(); document.body.classList.toggle('hide-mit', !showMit()); const cur=currentPhase(); document.querySelectorAll('button.phase').forEach(b=>b.classList.toggle('active', b.dataset.phase===cur)); ITEMS=selected(); renderCore(ITEMS); for(const id of Object.keys(TAB_RENDER)) DIRTY.add(id); noteRender(`recomputed in ${Math.round(performance.now()-t0)} ms`); renderActive(); }
// ---------------------------------------------------------------- tool use
const sum = xs => xs.reduce((a,b)=>a+b,0);
function toolCounts(items, field){ const c={}; for(const a of items) for(const [t,n] of Object.entries(a[field]||{})) c[t]=(c[t]||0)+n; return c; }
function areaShare(items){ const out={}; for(const ar of AREAS) out[ar]=items.length? items.filter(a=>(a.areas||{})[ar]>0 || Object.keys(a.kverbs||{}).some(t=>(KV_EQUIV[t]||[])[0]===ar)).length/items.length : 0; return out; }
function topList(counts, n, denom){ return Object.entries(counts).sort((x,y)=>y[1]-x[1]).slice(0,n).map(([t,c])=>`${esc(t)} <span class="small">${(c/denom).toFixed(1)}</span>`).join(', '); }
function calls(a){ return a.n_mcp + a.n_kubectl + a.n_shell; }
function renderToolUse(items, ref){
  const el=document.getElementById('p-tooluse-body'); let out='';
  for(const m of models){ const mi=items.filter(a=>a.model===m); if(!mi.length) continue;
    const stockRef = ref.filter(a=>a.model===m&&a.family==='stock');
    let t=`<table><thead><tr><th>${H('Arm','arm')}</th><th class="num">${H('attempts','attempts')}</th><th class="num">${H('pass@1','pass1')}</th><th>${H('calls per attempt by kind (share of all calls)','calls_total')}</th><th class="num">${H('tool errors','toolerr')}</th><th>${H('areas queried (share of attempts)','areas')}</th><th>${H('most used tools (per attempt)','toptools')}</th><th class="num">skill read</th></tr></thead><tbody>`;
    const rows=[]; for(const f of fams){ const fi=mi.filter(a=>a.family===f); if(!fi.length) continue; const g=agg(fi);
      const n=fi.length; const sh=areaShare(fi); const tools=toolCounts(fi,'tools'); const kv=toolCounts(fi,'kverbs');
      const areaTxt = AREAS.filter(ar=>sh[ar]>0).map(ar=>`${esc(AREA_LABEL[ar]||ar)} <span class="small">${Math.round(100*sh[ar])}%</span>`).join(', ') || '<span class="muted">none</span>';
      const toolTxt = [topList(tools,5,n), topList(kv,5,n)].filter(Boolean).join('<br>') || '<span class="muted">no tool calls recorded</span>';
      const errs = sum(fi.map(a=>a.n_errors)), tot = sum(fi.map(calls));
      rows.push(`<tr class="tu-row" data-k="${esc(m)}|${f}"><td>${esc(FAM_LABEL[f])} <button class="exp tu-exp" data-t="tu-${models.indexOf(m)}-${f}">+</button></td><td class="num">${n}</td><td class="num">${pct(g.passes,g.scored)}</td><td class="small">${[['ClickStack',g.cs,g.shares.cs],['ClickHouse SQL',g.sqlmcp,g.shares.sqlmcp],['Prometheus/Loki/Jaeger',g.obsmcp,g.shares.obsmcp],['kubectl',g.kubectl,g.shares.kubectl],['other shell',g.shell,g.shares.shell]].filter(([,n])=>n>0).map(([l,n,sh])=>`${l} ${withShare(n,sh)}`).join(' · ')}<br>of which live probes ${fmt(mean(fi.map(a=>a.n_probe||0)))}, fixes ${fmt(mean(fi.map(a=>a.n_fix||0)))} per attempt; other shell: ${Object.entries(g.shellKinds).sort((x,y)=>y[1]-x[1]).map(([k,v])=>`${esc(k)} ${(v/fi.length).toFixed(1)}`).join(', ')||'–'}</td><td class="num">${pct(errs,tot)}</td><td class="small">${areaTxt}</td><td class="small">${toolTxt}</td><td class="num">${pct(fi.filter(a=>a.skill_read).length,n)}</td></tr>`);
      rows.push(`<tr id="tu-${models.indexOf(m)}-${f}" class="row-exp hidden"><td colspan="8">${toolDetail(fi, stockRef, f)}</td></tr>`); }
    t+=rows.join('')+'</tbody></table>';
    out+=`<section class="card"><h3><span class="swatch" style="background:${colour(m)}"></span>${esc(m)} <span class="small">${esc(mi[0].harness)}</span></h3><p class="muted">What each arm actually called, per attempt. Click + for every tool with its use in accepted versus rejected attempts.</p>${t}</section>`; }
  el.innerHTML = out || '<p class="muted">No attempts in view.</p>';
  el.querySelectorAll('button.tu-exp').forEach(b=>b.addEventListener('click',()=>{const r=document.getElementById(b.dataset.t); r.classList.toggle('hidden'); b.textContent=r.classList.contains('hidden')?'+':'−';}));
}
function toolDetail(fi, stockRef, f){
  const pass=fi.filter(a=>a.diagnosis===true), fail=fi.filter(a=>a.diagnosis===false);
  const per = (items, counts, t) => items.length? ((counts[t]||0)/items.length).toFixed(1) : '–';
  const row = (t, c, err, kindLabel) => `<tr><td>${esc(t)} <span class="small">${kindLabel}</span></td><td class="num">${c}</td><td class="num">${(c/fi.length).toFixed(1)}</td><td class="num">${per(pass, cp, t)}</td><td class="num">${per(fail, cf, t)}</td><td class="num">${err==null?'–':pct(err,c)}</td></tr>`;
  const tools=toolCounts(fi,'tools'), terr=toolCounts(fi,'tool_err'), kv=toolCounts(fi,'kverbs');
  let cp=Object.assign({}, toolCounts(pass,'tools'), toolCounts(pass,'kverbs')); let cf=Object.assign({}, toolCounts(fail,'tools'), toolCounts(fail,'kverbs'));
  let t=`<table><thead><tr><th>${H('tool','tool')}</th><th class="num">calls</th><th class="num">per attempt</th><th class="num">${H('per passing attempt','passfail')}</th><th class="num">per failing attempt</th><th class="num">${H('errors','toolerr')}</th></tr></thead><tbody>`;
  t+=Object.entries(tools).sort((x,y)=>y[1]-x[1]).map(([k,c])=>row(k,c,terr[k]||0,'MCP')).join('');
  t+=Object.entries(kv).sort((x,y)=>y[1]-x[1]).map(([k,c])=>row(k,c,null,(KV_EQUIV[k]||[])[0]==='fix'?'kubectl, fix':(KV_EQUIV[k]||[])[0]==='probe'?'kubectl, probe':'kubectl')).join('');
  t+='</tbody></table>';
  // comparison with the stock arm's passing attempts
  let cmp='';
  if(f!=='stock' && stockRef.length){ const sp=stockRef.filter(a=>a.diagnosis===true); const skv=toolCounts(sp,'kverbs'); const top=Object.entries(skv).filter(([k])=>!['fix'].includes((KV_EQUIV[k]||[])[0])).sort((x,y)=>y[1]-x[1]).slice(0,6);
    const shArm=areaShare(fi); const tier=dataTier(f);
    const never=AREAS.filter(ar=>shArm[ar]===0 && (f==='obs_only'? !OBS_MISSING.has(ar) : (tier==null || (AREA_MIN_TIER[ar]??0)<=tier)));
    const missing=AREAS.filter(ar=> f==='obs_only'? OBS_MISSING.has(ar) : (tier!=null && (AREA_MIN_TIER[ar]??0)>tier));
    cmp=`<p class="small">The same model's stock arm passed ${sp.length} attempt(s) here reading mostly ${top.map(([k,c])=>`${esc(k)} (${(c/Math.max(1,sp.length)).toFixed(1)}/attempt)`).join(', ')||'–'}.`+
        (never.length? ` This arm never queried ${never.map(ar=>esc(AREA_LABEL[ar])).join(', ')} although its tier ships ${never.length>1?'them':'it'}.`:'')+
        (missing.length? ` Not shipped at this tier: ${missing.map(ar=>esc(AREA_LABEL[ar])).join(', ')}.`:'')+`</p>`; }
  return cmp+t;
}
// ---------------------------------------------------------------- gaps against the stock arm
function rateBy(items){ const r={}; for(const a of items){ if(a.diagnosis===null) continue; const s=r[a.problem]=r[a.problem]||{p:0,n:0}; s.p+=a.diagnosis?1:0; s.n++; } return r; }
function modeCounts(items){ const c={}; for(const a of items) if(a.diagnosis===false) c[a.mode||'unknown']=(c[a.mode||'unknown']||0)+1; return c; }
function modeText(c){ const e=Object.entries(c).sort((x,y)=>y[1]-x[1]); return e.length? e.map(([k,v])=>`${esc(k)} ${v}`).join(', ') : '–'; }
function gapVerdict(f, tier, stockPass, armFail){
  // what the stock agent's successful runs read, and whether this arm could have / did read the same
  const skv=toolCounts(stockPass,'kverbs'); const stockTools=toolCounts(stockPass,'tools');
  const reads=Object.entries(skv).filter(([k])=>!['fix','meta','probe'].includes((KV_EQUIV[k]||[])[0]));
  const totalReads=sum(reads.map(([,c])=>c)); const probeReads=sum(stockPass.map(a=>a.n_probe||0));   // n_probe already counts kubectl exec/port-forward/run and shell probes
  const probeShare=probeReads/Math.max(1,totalReads+probeReads);
  const needed={}; for(const [k,c] of reads){ const eq=KV_EQUIV[k]; if(!eq) continue; const [ar,desc,mt]=eq; if(ar==='probe') continue; const n=needed[ar]=needed[ar]||{c:0,descs:new Set(),tier:mt}; n.c+=c; n.descs.add(desc); }
  for(const [t,c] of Object.entries(stockTools)){ const ar = /metric|alert/.test(t)?'metrics':/log/.test(t)?'logs':/trace|service|operation|dependency/.test(t)?'traces':null; if(ar){ const n=needed[ar]=needed[ar]||{c:0,descs:new Set(),tier:0}; n.c+=c; n.descs.add('stock telemetry ('+t+')'); } }
  const armAreas={}; for(const a of armFail) for(const [ar,c] of Object.entries(a.areas||{})) armAreas[ar]=(armAreas[ar]||0)+c;
  const avail = ar => f==='obs_only'? !OBS_MISSING.has(ar) : (tier==null || (AREA_MIN_TIER[ar]??0)<=tier);
  const notCaptured=Object.keys(needed).filter(ar=>!avail(ar)); const notQueried=Object.keys(needed).filter(ar=>avail(ar)&&!(armAreas[ar]>0)&&ar!=='meta');
  const modes=modeCounts(armFail);
  let kind, text;
  const probeNote = probeReads? ` The stock runs also used ${probeReads} live probe${probeReads===1?'':'s'} (${Math.round(100*probeShare)}% of their cluster reads).` : '';
  if(notCaptured.length && probeShare<0.5){ kind='not captured at this tier'; text=`the stock agent read ${notCaptured.map(ar=>AREA_LABEL[ar]).join(' and ')}, which ${f==='obs_only'?'SREGym\'s Prometheus, Loki and Jaeger do not hold':'arrives only at tier '+Math.max(...notCaptured.map(ar=>AREA_MIN_TIER[ar]))}.`+probeNote; }
  else if(probeShare>=0.25){ kind='live probing'; text=`the stock agent's accepted runs leaned on live probes (${probeReads} of ${totalReads+probeReads} cluster reads: kubectl exec, port-forward, debug pods, curl or dig from a pod); the telemetry has no stand-in for those.`; }
  else if(notQueried.length){ kind='captured but not queried'; text=`this arm's failing attempts never read ${notQueried.map(ar=>AREA_LABEL[ar]).join(' or ')}, which the stock agent used (${notQueried.map(ar=>[...needed[ar].descs][0]).join('; ')}).`+probeNote; }
  else { kind='right data, wrong conclusion'; text=`this arm read the same areas the stock agent used (${Object.keys(needed).map(ar=>AREA_LABEL[ar]).join(', ')||'–'}) and still failed: ${modeText(modes)}.`+probeNote; }
  return {kind, text, needed, notCaptured, notQueried, modes, stockReads: reads.sort((x,y)=>y[1]-x[1]).slice(0,6)};
}
function renderGaps(items, ref){
  const el=document.getElementById('p-gaps-body'); let out='';
  for(const m of models){ const mi=items.filter(a=>a.model===m); if(!mi.length) continue;
    const stock=ref.filter(a=>a.model===m&&a.family==='stock'); const sr=rateBy(stock); const sg=agg(stock);
    out+=`<section class="card"><h3><span class="swatch" style="background:${colour(m)}"></span>${esc(m)} <span class="small">${esc(mi[0].harness)}</span></h3>`;
    if(!stock.length){ out+=`<p class="muted">No stock-arm attempts for this model in the selected campaigns, so there is no reference to measure gaps against.${H('','stockref')}</p>`; }
    else out+=`<p class="muted">Reference${H('','stockref')}: stock arm pass@1 ${pct(sg.passes,sg.scored)} over ${sg.scored} scored attempts, failure modes ${modeText(modeCounts(stock))}.</p>`;
    for(const f of fams){ const fi=mi.filter(a=>a.family===f); if(!fi.length) continue; const g=agg(fi); const ar=rateBy(fi); const tier=dataTier(f);
      if(f==='stock'){ // where other arms of the same model beat stock
        const others=mi.filter(a=>a.family!=='stock'); const orr={}; for(const a of others){ if(a.diagnosis===null) continue; const k=a.family+'|'+a.problem; const s=orr[k]=orr[k]||{p:0,n:0}; s.p+=a.diagnosis?1:0; s.n++; }
        const beat=Object.entries(orr).filter(([k,s])=>s.p/s.n>=0.5 && ar[k.split('|')[1]] && ar[k.split('|')[1]].p/ar[k.split('|')[1]].n<0.5);
        out+=`<details class="card"><summary><b>${esc(FAM_LABEL[f])}</b> · pass@1 ${pct(g.passes,g.scored)} · failure modes: ${modeText(modeCounts(fi))}</summary><p class="small">Problems where stock failed (under half of its attempts) but a telemetry arm of the same model passed: ${beat.length? beat.map(([k])=>`${esc(k.split('|')[1])} <span class="muted">(${esc(FAM_LABEL[k.split('|')[0]])})</span>`).join('; ') : 'none in view'}.</p>${stockFailTable(fi)}</details>`; continue; }
      if(!stock.length){ out+=`<details class="card"><summary><b>${esc(FAM_LABEL[f])}</b> · pass@1 ${pct(g.passes,g.scored)} · failure modes: ${modeText(modeCounts(fi))}</summary>${stockFailTable(fi)}</details>`; continue; }
      const probs=Object.keys(ar).filter(p=>sr[p]); const gaps=probs.filter(p=>sr[p].p/sr[p].n>=0.5 && ar[p].p/ar[p].n<0.5); const gains=probs.filter(p=>sr[p].p/sr[p].n<0.5 && ar[p].p/ar[p].n>=0.5); const shared=probs.filter(p=>sr[p].p/sr[p].n<0.5 && ar[p].p/ar[p].n<0.5);
      const d = (g.pass1!=null&&sg.pass1!=null)? Math.round(100*(g.pass1-sg.pass1)) : null;
      // per gap problem
      const verdicts=gaps.map(p=>{ const sp=stock.filter(a=>a.problem===p&&a.diagnosis===true); const af=fi.filter(a=>a.problem===p&&a.diagnosis===false); return [p, gapVerdict(f, tier, sp, af), sp, af]; });
      const kinds={}; for(const [,v] of verdicts) kinds[v.kind]=(kinds[v.kind]||0)+1;
      // what would have helped: areas the stock runs needed across gap problems
      const help={}; for(const [p,v] of verdicts) for(const [ar,n] of Object.entries(v.needed)){ const h=help[ar]=help[ar]||{probs:0,queried:0,tier:n.tier,descs:new Set()}; h.probs++; [...n.descs].forEach(x=>h.descs.add(x)); if(!v.notQueried.includes(ar)&&!v.notCaptured.includes(ar)) h.queried++; }
      const avail = ar => f==='obs_only'? !OBS_MISSING.has(ar) : (tier==null || (AREA_MIN_TIER[ar]??0)<=tier);
      const helpRows=Object.entries(help).sort((x,y)=>y[1].probs-x[1].probs).map(([ar,h])=>`<tr><td>${esc(AREA_LABEL[ar]||ar)}<br><span class="small">${[...h.descs].slice(0,3).map(esc).join('; ')}</span></td><td class="num">${h.probs}</td><td>${avail(ar)? 'yes' : (f==='obs_only'? 'no (not in stock telemetry)' : `no (tier ${AREA_MIN_TIER[ar]} needed, this arm has tier ${tier})`)}</td><td class="num">${h.queried} of ${h.probs}</td></tr>`).join('');
      const summary=`<p>pass@1 <b>${pct(g.passes,g.scored)}</b> against the stock arm's ${pct(sg.passes,sg.scored)}${d==null?'':` (${d>0?'+':''}${d} pts)`}. On the ${probs.length} problems both arms were scored on: <b>${gaps.length} gap${gaps.length===1?'':'s'}</b>, ${gains.length} gain${gains.length===1?'':'s'}, ${shared.length} shared failure${shared.length===1?'':'s'}.`+
        (gaps.length? ` Gap reasons: ${Object.entries(kinds).sort((x,y)=>y[1]-x[1]).map(([k,v])=>`${esc(k)} ×${v}`).join(', ')}.`:'')+
        ` Failure modes across all ${fi.filter(a=>a.diagnosis===false).length} rejected attempts: ${modeText(modeCounts(fi))}.</p>`;
      let gt='';
      if(gaps.length){ gt=`<table class="rows-sep"><thead><tr><th>${H('gap problem','gaps')}</th><th>${H('fault','fault')}</th><th>this arm</th><th>stock</th><th>${H('what the stock agent read','toptools')}</th><th>this arm read</th><th>${H('reading','verdict')}</th></tr></thead><tbody>`+
        verdicts.map(([p,v,sp,af])=>{ const ci=fi.filter(a=>a.problem===p).sort((x,y)=>x.attempt-y.attempt); const si=stock.filter(a=>a.problem===p).sort((x,y)=>x.attempt-y.attempt); const armAreas={}; for(const a of af) for(const [ar,c] of Object.entries(a.areas||{})) armAreas[ar]=(armAreas[ar]||0)+c;
          return `<tr><td>${esc(p)}</td><td class="small fault">${esc((ci.find(a=>a.fault)||{}).fault||'')}</td><td>${ci.map(mark).join(' ')}<br><span class="small">${modeText(modeCounts(ci))}</span></td><td>${si.map(mark).join(' ')}</td><td class="small">${v.stockReads.map(([k,c])=>`${esc(k)} ${c}`).join(', ')||'–'}${sum(sp.map(a=>a.n_probe||0))?`; probes ${sum(sp.map(a=>a.n_probe||0))}`:''}</td><td class="small">${Object.entries(armAreas).sort((x,y)=>y[1]-x[1]).map(([ar,c])=>`${esc(AREA_LABEL[ar]||ar)} ${c}`).join(', ')||'no telemetry calls'}</td><td class="small"><b>${esc(v.kind)}</b>: ${esc(v.text)}</td></tr>`; }).join('')+'</tbody></table>'; }
      const gainTxt = gains.length? `<p class="small"><b>Gains over stock</b>${H('','gaps')}: ${gains.map(p=>`${esc(p)} (${ar[p].p}/${ar[p].n} vs stock ${sr[p].p}/${sr[p].n})`).join('; ')}.</p>` : '';
      const helpTxt = helpRows? `<h4>${H('What would have helped','helpful')}</h4><table><thead><tr><th>telemetry the stock agent relied on</th><th class="num">gap problems</th><th>shipped at this tier</th><th class="num">queried by this arm's failing attempts</th></tr></thead><tbody>${helpRows}</tbody></table>` : '';
      out+=`<details class="card" ${gaps.length?'open':''}><summary><b>${esc(FAM_LABEL[f])}</b> · pass@1 ${pct(g.passes,g.scored)} vs stock ${pct(sg.passes,sg.scored)} · ${gaps.length} gap(s), ${gains.length} gain(s)</summary>${summary}${gt}${gainTxt}${helpTxt}</details>`; }
    out+='</section>'; }
  el.innerHTML = out || '<p class="muted">No attempts in view.</p>';
}
function stockFailTable(fi){ const fails=fi.filter(a=>a.diagnosis===false); if(!fails.length) return '<p class="small">No rejected attempts in view.</p>';
  const byP={}; for(const a of fails) (byP[a.problem]=byP[a.problem]||[]).push(a);
  return `<table class="rows-sep"><thead><tr><th>problem</th><th>fault</th><th>rejected attempts</th><th>${H('failure modes','modes')}</th><th>${H('tools used in them','toptools')}</th></tr></thead><tbody>`+Object.entries(byP).map(([p,as])=>`<tr><td>${esc(p)}</td><td class="small fault">${esc((as.find(a=>a.fault)||{}).fault||'')}</td><td>${as.sort((x,y)=>x.attempt-y.attempt).map(mark).join(' ')}</td><td class="small">${modeText(modeCounts(as))}</td><td class="small">${[topList(toolCounts(as,'tools'),4,as.length), topList(toolCounts(as,'kverbs'),4,as.length)].filter(Boolean).join('<br>')||'–'}</td></tr>`).join('')+'</tbody></table>'; }
// ---------------------------------------------------------------- SQL tab
const sqlStr = v => "'" + String(v).replace(/\\/g, "\\\\").replace(/'/g, "\\'") + "'";
function filtersAsWhere(){ const on = cls => [...document.querySelectorAll(cls+':checked')].map(x=>x.value);
  const parts=[];
  const all = (cls, col, universe) => { const v=on(cls); if(v.length && v.length<universe.length) parts.push(`${col} IN (${v.map(sqlStr).join(', ')})`); };
  all('.f-camp','campaign',campaigns); all('.f-model','model',models); all('.f-fam','family',fams); all('.f-har','harness',harnesses);
  const inView=[...new Set(ITEMS.map(a=>a.problem))]; if(inView.length && inView.length<problems.length) parts.push(`problem IN (${inView.map(sqlStr).join(', ')})`);
  return parts.length? parts.join('\n  AND ') : '1';
}
const PRESETS = [
 ['pass@1 by model and arm', `SELECT model, family, count() AS attempts, countIf(diagnosis) AS passes, round(100 * countIf(diagnosis) / count()) AS pass1_pct,
       round(avgIf(ttd_s, diagnosis)) AS ttd_when_correct_s, round(avg(n_mcp), 1) AS mcp_per_attempt, round(avg(n_kubectl), 1) AS kubectl_per_attempt
FROM {db}.attempts_scored
WHERE {where}
GROUP BY model, family ORDER BY model, family`],
 ['pass@k (any of the attempts) by model and arm', `SELECT model, family, count() AS cells, countIf(any_pass) AS cells_passed, round(100 * countIf(any_pass) / count()) AS passk_pct
FROM (SELECT model, family, problem, max(diagnosis) AS any_pass FROM {db}.attempts_scored WHERE {where} GROUP BY model, family, problem)
GROUP BY model, family ORDER BY model, family`],
 ['failure modes by arm', `SELECT family, mode, count() AS rejected
FROM {db}.attempts_scored
WHERE {where} AND diagnosis = false
GROUP BY family, mode ORDER BY family, rejected DESC`],
 ['tool use by arm: calls per attempt and share', `SELECT family, tool, kind, count() AS calls, round(count() / uniq(run_dir), 1) AS per_attempt, round(100 * countIf(error) / count()) AS error_pct
FROM {db}.tool_calls FINAL
WHERE run_dir IN (SELECT run_dir FROM {db}.attempts_scored WHERE {where})
GROUP BY family, tool, kind ORDER BY family, calls DESC`],
 ['gap problems: stock passes, arm fails (same model)', `WITH stock AS (SELECT model, problem, countIf(diagnosis) / count() AS stock_rate FROM {db}.attempts_scored WHERE family = 'stock' GROUP BY model, problem)
SELECT a.model, a.family, a.problem, round(countIf(a.diagnosis) / count(), 2) AS arm_rate, round(any(s.stock_rate), 2) AS stock_rate, groupArray(a.mode) AS modes
FROM {db}.attempts_scored AS a INNER JOIN stock AS s ON a.model = s.model AND a.problem = s.problem
WHERE {where} AND a.family != 'stock'
GROUP BY a.model, a.family, a.problem
HAVING arm_rate < 0.5 AND stock_rate >= 0.5
ORDER BY a.model, a.family, a.problem`],
 ['telemetry areas queried per attempt, by arm', `SELECT family, area, round(count() / uniq(run_dir), 1) AS calls_per_attempt, round(100 * uniq(run_dir) / (SELECT uniq(run_dir) FROM {db}.attempts_scored WHERE {where} AND family = t.family)) AS attempts_touching_pct
FROM {db}.tool_calls FINAL AS t
WHERE kind = 'mcp' AND run_dir IN (SELECT run_dir FROM {db}.attempts_scored WHERE {where})
GROUP BY family, area ORDER BY family, calls_per_attempt DESC`],
 ['every SQL query the agents wrote that errored', `SELECT model, family, problem, attempt, left(sql, 200) AS sql, left(error_text, 120) AS error
FROM {db}.tool_calls FINAL
WHERE sql != '' AND error AND run_dir IN (SELECT run_dir FROM {db}.attempts_scored WHERE {where})
ORDER BY model, family, problem LIMIT 500`],
 ['saturation: problems every kubectl cell solves', `SELECT problem, round(100 * countIf(diagnosis) / count()) AS pass_pct, min(cell_rate) AS weakest_cell
FROM (SELECT problem, model, family, diagnosis, countIf(diagnosis) OVER (PARTITION BY problem, model, family) / count() OVER (PARTITION BY problem, model, family) AS cell_rate
      FROM {db}.attempts_scored WHERE {where} AND (family = 'stock' OR family LIKE 'kubectl_%'))
GROUP BY problem HAVING pass_pct >= 95 AND weakest_cell >= 0.67 ORDER BY problem`],
];
function renderSqlTab(){
  const el=document.getElementById('p-sql'); if(!el || !CH || !CH.endpoint) { if(el) el.innerHTML='<p class="muted">No ClickHouse endpoint was configured when this page was built.</p>'; return; }
  const saved = (()=>{ try { return JSON.parse(localStorage.getItem('sregym-ch')||'{}'); } catch(e){ return {}; } })();
  const schema = Object.entries(CH.schema||{}).map(([t,cols])=>`<details><summary><code>${esc(CH.database)}.${esc(t)}</code> <span class="small">${cols.length} columns</span></summary><table><tbody>${cols.map(([c,ty])=>`<tr><td><code>${esc(c)}</code></td><td class="small">${esc(ty)}</td></tr>`).join('')}</tbody></table></details>`).join('');
  el.innerHTML = `<section class="card"><h3>Query the results in ClickHouse${H('','sqltab')}</h3>
  <div class="sqlconn"><label>endpoint <input id="ch-endpoint" size="48" value="${esc(saved.endpoint||CH.endpoint)}"></label> <label>user <input id="ch-user" size="18" value="${esc(saved.user||'clickstack_readonly')}"></label> <label>password <input id="ch-pass" type="password" size="18" value="${esc(saved.pass||'')}"></label> <label><input type="checkbox" id="ch-remember" ${saved.pass?'checked':''}> remember in this browser</label></div>
  <p class="small">Loaded by <code>clickstack/load_results.py</code> into <code>${esc(CH.database)}</code>: <code>attempts</code> (one row per attempt), <code>tool_calls</code> (one row per tool call), <code>problem_sets</code>, and the views <code>attempts_scored</code>, <code>pass_rates</code> and <code>cells</code>. Query the tables with <code>FINAL</code> or use the views.</p>
  <div class="sqlgrid"><div><label class="small">${H('current filters as SQL','sqlwhere')}</label><textarea id="ch-where" rows="4" readonly></textarea></div>
  <div><label class="small">preset</label> <select id="ch-preset"><option value="">choose a query</option>${PRESETS.map((p,i)=>`<option value="${i}">${esc(p[0])}</option>`).join('')}</select><br>
  <textarea id="ch-sql" rows="12" spellcheck="false" placeholder="SELECT ... FROM ${esc(CH.database)}.attempts_scored WHERE ..."></textarea><br>
  <button id="ch-run">Run</button> <span id="ch-status" class="small muted"></span></div></div>
  <div id="ch-result"></div></section><section class="card"><h3>Schema</h3>${schema}</section>`;
  const whereBox=document.getElementById('ch-where'); whereBox.textContent=filtersAsWhere();
  document.getElementById('ch-preset').addEventListener('change', e=>{ const p=PRESETS[+e.target.value]; if(p) document.getElementById('ch-sql').value = p[1].replace(/\{db\}/g, CH.database).replace(/\{where\}/g, filtersAsWhere()); });
  document.getElementById('ch-run').addEventListener('click', runSql);
  document.getElementById('ch-sql').addEventListener('keydown', e=>{ if((e.metaKey||e.ctrlKey) && e.key==='Enter') runSql(); });
}
async function runSql(){
  const endpoint=document.getElementById('ch-endpoint').value.trim().replace(/\/$/,''); const user=document.getElementById('ch-user').value.trim(); const pass=document.getElementById('ch-pass').value;
  const sql=document.getElementById('ch-sql').value; const status=document.getElementById('ch-status'); const out=document.getElementById('ch-result');
  try { localStorage.setItem('sregym-ch', JSON.stringify(document.getElementById('ch-remember').checked? {endpoint,user,pass} : {endpoint,user})); } catch(e){}
  if(!sql.trim()){ status.textContent='nothing to run'; return; }
  status.textContent='running…'; out.innerHTML='';
  const params=new URLSearchParams({add_http_cors_header:'1', default_format:'JSONCompact', max_result_rows:'2000', result_overflow_mode:'break', max_execution_time:'60', user, password:pass});
  const t0=performance.now();
  try {
    const r = await fetch(`${endpoint}/?${params}`, {method:'POST', headers:{'Content-Type':'text/plain'}, body: sql});
    const text = await r.text();
    if(!r.ok){ status.textContent=`error (HTTP ${r.status})`; out.innerHTML=`<pre class="pre" style="color:var(--bad)">${esc(text.slice(0,2000))}</pre>`; return; }
    const d = JSON.parse(text); const cols=d.meta.map(m=>m.name);
    out.innerHTML = `<table><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${d.data.map(row=>`<tr>${row.map(v=>`<td>${esc(v==null?'∅':(typeof v==='object'? JSON.stringify(v) : v))}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    status.textContent = `${d.rows} row(s)${d.rows_before_limit_at_least && d.rows_before_limit_at_least>d.rows? ` of at least ${d.rows_before_limit_at_least}`:''} in ${Math.round(performance.now()-t0)} ms${d.statistics? ` · ${d.statistics.rows_read.toLocaleString()} rows read` : ''}`;
  } catch(e){ status.textContent='request failed'; out.innerHTML=`<pre class="pre" style="color:var(--bad)">${esc(String(e))}\nIf the endpoint is right, the browser may have refused a cross-origin request; the ClickHouse HTTP interface answers browser requests when add_http_cors_header=1 is set, which this page sends.</pre>`; }
}
// ---------------------------------------------------------------- method
function renderMethod(){
  const metric=[['attempts','attempts'],['pass@1','pass1'],['pass@k','passk'],['mitigation','mitigation'],['mean TTD','ttd'],['TTD when correct','ttdc'],['mean TTM','ttm'],['tokens','tokens'],['MCP calls','mcp'],['kubectl calls','kubectl'],['SQL share','sql'],['tool errors','errors'],['cost','cost'],['saturated problems','saturated'],['balanced problems','balanced'],['problem set','set'],['calls per attempt','calls'],['areas queried','areas'],['gaps and gains','gaps'],['gap reading','verdict'],['what would have helped','helpful']];
  let h=`<section class="card"><h3>How to read the numbers</h3><table><tbody>${metric.map(([l,k])=>`<tr><th style="width:220px">${esc(l)}</th><td>${esc(HELP[k])}</td></tr>`).join('')}</tbody></table></section>`;
  h+=`<section class="card"><h3>Arms and tiers</h3><p>${esc(HELP.arm)}</p><p>${esc(HELP.harness)}</p><table><thead><tr><th>telemetry area</th><th>first shipped at tier</th></tr></thead><tbody>${AREAS.map(ar=>`<tr><td>${esc(AREA_LABEL[ar])}</td><td>${AREA_MIN_TIER[ar]}</td></tr>`).join('')}</tbody></table><p class="small">Tier 4 ships the same data as tier 3; it adds investigation rules to the skill. The stock-telemetry arm (Prometheus, Loki, Jaeger) holds application logs, metrics and traces only.</p></section>`;
  h+=`<section class="card"><h3>Failure modes</h3><p>Derived for every rejected attempt from the judge's checklist (D1 fault localisation, D2 fault characterisation, D3 scope precision; three questions each) and the submission text.</p><table><tbody>${Object.entries(FAILURE_MODES).map(([k,v])=>`<tr><th style="width:220px">${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</tbody></table></section>`;
  h+=`<section class="card"><h3>kubectl reads and their telemetry equivalents</h3><p class="small">How the gap analysis maps what the stock agent did to what a telemetry arm could have queried.</p><table><thead><tr><th>kubectl</th><th>area</th><th>equivalent</th><th>tier</th></tr></thead><tbody>${Object.entries(KV_EQUIV).sort().map(([k,[ar,d,t]])=>`<tr><td>${esc(k)}</td><td>${esc(ar)}</td><td>${esc(d)}</td><td>${t==null?'–':t}</td></tr>`).join('')}</tbody></table></section>`;
  if(NOTES_HTML) h+=`<section class="card"><h3>Analyst notes</h3>${NOTES_HTML}</section>`;
  document.getElementById('p-method').innerHTML=h;
}
function problemTable(items, cols, key){ const label=k=> key==='family'? FAM_LABEL[k] : k; let t='<table class="rows-sep"><thead><tr><th>Problem</th>'+cols.filter(c=>items.some(a=>a[key]===c)).map(c=>`<th>${esc(label(c))}</th>`).join('')+'</tr></thead><tbody>';
  for(const p of problems){ const pi=items.filter(a=>a.problem===p); if(!pi.length) continue; t+=`<tr><th>${esc(p)}</th>`+cols.filter(c=>items.some(a=>a[key]===c)).map(c=>{const cc=pi.filter(a=>a[key]===c).sort((x,y)=>x.attempt-y.attempt); return `<td>${cc.map(mark).join(' ')||'<span class="st muted">–</span>'}</td>`;}).join('')+'</tr>'; } return t+'</tbody></table>'; }
function detail(a){ const steps=a.steps.map(s=>`<tr><td>${s.i}</td><td>${esc(s.kind)}</td><td>${esc(s.tool)}</td><td>${esc(s.sql||s.args)}</td><td>${s.dur_s==null?'':s.dur_s+' s'}</td><td class="res">${s.error?'<span class="st bad">error</span> '+esc(s.error_text):''}</td><td class="small">${esc(s.pre_text||'')}</td></tr>`).join('');
  const tok=a.tokens? Object.entries(a.tokens).filter(([k,v])=>v!=null).map(([k,v])=>`${k.replace('_tokens','')} ${Number(v).toLocaleString()}`).join(', ') : '–';
  return `<div class="small">ClickStack MCP server: ${esc(a.mcp_version||'–')}${a.n_scripted? ` · ${a.n_scripted} MCP call(s) made through a client the agent wrote itself (shell), counted as MCP calls`:''}</div><div class="pre"><b>Fault (ground truth):</b> ${esc(a.fault||'–')}</div><div class="pre"><b>Submitted diagnosis:</b> ${esc(a.submission||'–')}</div><details><summary>Judge verdict and checklist</summary><div class="pre">${esc(a.judge_reasoning||'–')}</div></details><p class="small">tokens: ${tok} · agent wall ${fmtS(a.agent_wall_s)} · turns ${a.turns||'–'} · skill read: ${a.skill_read===true?'yes':a.skill_read===false?'no':'–'} · artifacts: ${esc(a.run_dir)}</p><table class="trace"><thead><tr><th>#</th><th>kind</th><th>tool</th><th>arguments / command / SQL</th><th>took</th><th>result</th><th>agent's words before</th></tr></thead><tbody>${steps}</tbody></table>`; }
function exportCsv(){ const items=selected(); const cols=['campaign','model','harness','agent','family','tier','problem','attempt','diagnosis','composite','mitigation','ttd_s','ttm_s','tokens_total','n_mcp','n_kubectl','n_sql','n_errors','blocked_requests','cost_usd','run_status','submission'];
  const rows=[cols.join(',')].concat(items.map(a=>cols.map(c=>{let v=c==='tokens_total'? tokTotal(a) : a[c]; v= v==null? '' : String(v).replace(/"/g,'""'); return /[",\n]/.test(v)? `"${v}"`:v;}).join(','))); const blob=new Blob([rows.join('\n')],{type:'text/csv'}); const u=URL.createObjectURL(blob); const l=document.createElement('a'); l.href=u; l.download='sregym-clickstack-attempts.csv'; l.click(); URL.revokeObjectURL(u); }
// filters UI
const fl=document.getElementById('filters');
fl.innerHTML = `<div class="phases">${Object.entries(PHASES).map(([k,ph])=>`<button type="button" class="phase" data-phase="${k}">${esc(ph.label)}</button>`).join('')}${H('','phases')}</div><div class="grp"><details class="camps"><summary><b>campaigns</b>${H('','campaign')}</summary>${campaigns.map(c=>`<label><input type="checkbox" class="f-camp" value="${esc(c)}" checked> ${esc(c)}</label>`).join('')}</details></div><span class="sep"></span><div class="grp"><b>Model</b>${H('','models_default')} ${models.map(m=>`<label><input type="checkbox" class="f-model" value="${esc(m)}" ${defaultModel(m)?'checked':''}> <span class="swatch" style="background:${colour(m)}"></span>${esc(m)}</label>`).join('')}</div><span class="sep"></span>`+
 `<div class="grp"><b>Arm</b>${H('','arm')} ${fams.map(f=>`<label><input type="checkbox" class="f-fam" value="${f}" checked> ${esc(FAM_LABEL[f])}</label>`).join('')}</div><span class="sep"></span>`+
 `<div class="grp"><b>Harness</b>${H('','harness')} ${harnesses.map(h=>`<label><input type="checkbox" class="f-har" value="${h}" checked> ${esc(h)}</label>`).join('')}</div><span class="sep"></span>`+
 `<div class="grp"><b>Problem set</b>${H('','set')} <select id="f-set"><option value="">all</option>${Object.keys(SETS).filter(k=>SETS[k].some(p=>problems.includes(p))).map(k=>`<option value="${esc(k)}">${esc(k)} (${SETS[k].filter(p=>problems.includes(p)).length} in data)</option>`).join('')}</select></div>`+
 `<div class="grp"><details id="f-problems-box"><summary><b>Problems</b>${H('','problems_pick')} <span class="small" id="f-prob-count"></span></summary><div class="probs"><div class="probs-actions"><button type="button" id="prob-all">all</button> <button type="button" id="prob-none">none</button> <button type="button" id="prob-nosat">exclude saturated</button></div>${problems.map(p=>`<label><input type="checkbox" class="f-prob" value="${esc(p)}" checked> ${esc(p)}</label>`).join('')}</div></details></div>`+
 `<div class="grp"><input type="search" id="f-text" placeholder="search problem, model, diagnosis text"></div><div class="grp"><label><input type="checkbox" id="f-balanced"> balanced problems only</label>${H('','balanced')}</div><div class="grp"><label><input type="checkbox" id="f-mit" checked> hide mitigation</label>${H('','mit_hide')}</div><div class="grp"><label><input type="checkbox" id="f-sat" checked> hide saturated problems</label>${H('','saturated')} <span class="small">judged on</span> <select id="f-sat-ref"><option value="kubectl" selected>kubectl arms</option><option value="stock">stock arm</option><option value="all">selected arms</option><option value="telemetry">telemetry-only arms</option></select>${H('','sat_ref')} <span class="small">pass rate ≥</span> <select id="f-sat-thr"><option value="0.9">90%</option><option value="0.95" selected>95%</option><option value="1">100%</option></select> <span class="small">and weakest cell ≥</span> <select id="f-sat-cell"><option value="0">any</option><option value="0.67" selected>67%</option><option value="1">100%</option></select></div><div class="grp"><button id="f-csv">Export CSV</button></div>`;
document.querySelectorAll('.f-model,.f-fam,.f-har,.f-camp,.f-prob,#f-sat,#f-sat-thr,#f-sat-cell,#f-sat-ref,#f-set,#f-mit').forEach(x=>x.addEventListener('change',safeRender));
const setProbs = pred => { document.querySelectorAll('.f-prob').forEach(x=>{ x.checked = pred(x.value); }); safeRender(); };
document.querySelectorAll('button.phase').forEach(b=>b.addEventListener('click',()=>{ applyPhase(b.dataset.phase); safeRender(); }));
document.getElementById('prob-all').addEventListener('click',()=>setProbs(()=>true)); document.getElementById('prob-none').addEventListener('click',()=>setProbs(()=>false)); document.getElementById('prob-nosat').addEventListener('click',()=>setProbs(p=>!SATURATED.has(p))); document.getElementById('f-text').addEventListener('input',safeRender); document.getElementById('f-balanced').addEventListener('change',safeRender); document.getElementById('f-csv').addEventListener('click',exportCsv);
{ const q=new URLSearchParams(location.search); if(q.get('sat')==='1') document.getElementById('f-sat').checked=true; if(q.get('sat')==='0') document.getElementById('f-sat').checked=false; if(q.get('mit')==='1') document.getElementById('f-mit').checked=false; if(q.get('balanced')==='1') document.getElementById('f-balanced').checked=true;
  if(q.get('set') && SETS[q.get('set')]) document.getElementById('f-set').value=q.get('set');
  if(q.get('satref')) document.getElementById('f-sat-ref').value=q.get('satref');
  applyPhase(PHASES[q.get('phase')]? q.get('phase') : 'p1'); }  // ?sat=1, ?satref=stock, ?balanced=1 and ?set=sregym-lite pre-set the filters, for links
const tabs=[...document.querySelectorAll('nav.tabs button')]; const panels=[...document.querySelectorAll('.panel')];
const showTab=id=>{tabs.forEach(x=>x.classList.toggle('active',x.dataset.panel===id));panels.forEach(p=>p.classList.toggle('active',p.id===id));};
tabs.forEach(b=>b.addEventListener('click',()=>{showTab(b.dataset.panel); history.replaceState(null,'','#'+b.dataset.panel); try{ renderActive(); }catch(e){ console.error(e); }}));
if(panels.some(p=>p.id===location.hash.slice(1))) showTab(location.hash.slice(1));
function safeRender(){ const box=document.getElementById('js-error'); try{ render(); box.textContent=''; box.classList.add('hidden'); } catch(e){ box.textContent='The page could not render part of the report: '+(e&&e.stack||e); box.classList.remove('hidden'); console.error(e); } }
renderMethod();
safeRender();
"""


def clickhouse_config() -> dict:
    """Endpoint and database for the SQL tab, from ~/.config/sregym/clickstack.env (no credentials), plus the table schema."""
    env_file = Path(os.environ.get("CLICKSTACK_ENV_FILE", Path.home() / ".config" / "sregym" / "clickstack.env"))
    endpoint = ""
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("CLICKHOUSE_ENDPOINT="):
                endpoint = line.split("=", 1)[1].strip().strip("'\"")
    schema = {}
    try:
        from clickstack.load_results import DDL  # noqa: PLC0415
        for stmt in DDL.split(";"):
            m = re.search(r"CREATE TABLE IF NOT EXISTS \{db\}\.(\w+) \((.*)\) ENGINE", stmt, re.S)
            if m:
                cols = []
                for part in re.split(r",\s*\n", m.group(2).strip()):
                    for col in re.split(r",\s+(?=[a-z_]+ [A-Z])", part.strip()):
                        name, _, ty = col.strip().partition(" ")
                        if name:
                            cols.append([name, ty.split(" DEFAULT")[0]])
                schema[m.group(1)] = cols
    except Exception:  # noqa: BLE001
        pass
    return {"endpoint": endpoint, "database": os.environ.get("SREGYM_RESULTS_DB", "sregym_results"), "schema": schema}


def build_html(runs: list[dict], attempts: list[dict], title: str, failed: list[dict], notes_html: str = "") -> str:
    judge = sorted({r.get("judge_model") for r in runs if r.get("judge_model")})
    kv_equiv = {t: list(kubectl_equivalent(t)) for a in attempts for t in a.get("kverbs", {})}
    sets = problem_sets()
    campaigns = sorted({r.get("campaign") for r in runs if r.get("campaign")})
    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    versions = sorted({a["mcp_version"] for a in attempts})
    failed_note = ("<p class='muted'>" + f"{len(failed)} launch(es) failed before any attempt: " + ", ".join(html.escape(f"{r['agent']} × {r['model']}") for r in failed) + "</p>") if failed else ""
    partial = [r for r in runs if r.get("exit_code") == 2]
    live = [r for r in runs if r.get("in_progress")]
    if partial:
        failed_note += "<p class='muted'>" + f"{len(partial)} pair(s) where SREGym could not deploy every attempt (shown as deploy marks): " + ", ".join(html.escape(f"{r['agent']} × {r['model']}") for r in partial) + "</p>"
    if live:
        failed_note += "<p class='muted'>" + f"{len(live)} pair(s) still running, partial: " + ", ".join(html.escape(f"{r['agent']} × {r['model']}") for r in live) + "</p>"
    if failed_note:
        bits = [f"{len(failed)} launch failure(s)" if failed else "", f"{len(partial)} pair(s) with an undeployed attempt" if partial else "", f"{len(live)} pair(s) running" if live else ""]
        failed_note = "<details class='muted'><summary>" + ", ".join(b for b in bits if b) + " (details)</summary>" + failed_note + "</details>"
    data = json.dumps(attempts).replace("</", "<\\/")
    js = (JS.replace("__DATA__", data).replace("__FAM_ORDER__", json.dumps(FAMILY_ORDER)).replace("__FAM_LABEL__", json.dumps(FAMILY_LABEL)).replace("__SERIES__", json.dumps(SERIES_LIGHT))
          .replace("__SETS__", json.dumps(sets)).replace("__KV_EQUIV__", json.dumps(kv_equiv)).replace("__AREA_LABEL__", json.dumps(AREA_LABEL))
          .replace("__AREA_MIN_TIER__", json.dumps(AREA_MIN_TIER)).replace("__FAILURE_MODES__", json.dumps(FAILURE_MODES)).replace("__NOTES__", json.dumps(notes_html).replace("</", "<\\/"))
          .replace("__CH__", json.dumps(clickhouse_config())))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>{CSS}</style></head><body>
<header><h1>{html.escape(title)}</h1><div class="muted">Generated {generated}. Campaigns: {html.escape(", ".join(campaigns) or "–")}. Judge: {html.escape(", ".join(judge) or "–")}.
Diagnosis is graded by the LLM judge against the problem's fault specification (pass at composite 0.70); mitigation by SREGym's live oracle (kubectl arms only).
TTD and TTM are SREGym's times from agent start to the diagnosis and mitigation submissions. pass@k counts a (model, arm, problem) cell as passed if any attempt passed. All numbers recompute from the filters. Hover or focus an <span class="info" tabindex="0" data-tip="Every column, tile and filter has one of these; it explains the number and how it is computed.">i</span> for what a number means.
ClickStack MCP server versions seen: {html.escape(", ".join(versions) or "–")} (each attempt's version is in its detail row).</div>{failed_note}</header>
<main>
<div id="filters" class="filters"></div>
<div id="render-note" class="small muted" style="margin:-10px 0 4px"></div>
<div id="sat-note" class="small" style="margin:0 0 12px"></div>
<div id="js-error" class="card hidden" style="color:var(--bad)"></div>
<nav class="tabs"><button class="active" data-panel="p-overview">Overview</button><button data-panel="p-tooluse">Tool use</button><button data-panel="p-gaps">Gaps vs stock</button><button data-panel="p-model">By model</button><button data-panel="p-arm">By arm / tier</button><button data-panel="p-problem">By problem</button><button data-panel="p-attempts">Attempts</button><button data-panel="p-tools">Queries</button><button data-panel="p-sql">SQL</button><button data-panel="p-method">Method &amp; notes</button></nav>
<div id="p-overview" class="panel active"><div id="tiles" class="tiles"></div><h2>Headline numbers</h2><div id="headlines" class="card"></div><h2>Diagnosis pass@1, model × arm</h2><div id="matrix" class="card"></div></div>
<div id="p-tooluse" class="panel"><p class="muted">Which tools each model and arm called, how often, in passing against failing attempts, and which telemetry areas they touched. Everything follows the filters above.</p><div id="p-tooluse-body"></div></div>
<div id="p-gaps" class="panel"><p class="muted">For every model, each arm against the same model's stock arm: problems the stock agent solves that the arm does not, a rule-based reading of why (live probing, data not captured at the tier, captured but never queried, or read and misjudged), the judge-derived failure modes, and the telemetry that would have helped.</p><div id="p-gaps-body"></div></div>
<div id="p-model" class="panel"></div><div id="p-arm" class="panel"></div><div id="p-problem" class="panel"></div><div id="p-attempts" class="panel"></div><div id="p-tools" class="panel"></div><div id="p-sql" class="panel"></div><div id="p-method" class="panel"></div>
</main><script>{js}</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", default=str(REPO / "results"))
    ap.add_argument("--campaign", nargs="*", default=None, help="only these campaigns (manifest stems)")
    ap.add_argument("--notes", action="append", default=[], help="Markdown file(s) rendered in the Method & notes tab; clickstack/notes/<campaign>.md is added for every campaign present")
    ap.add_argument("--out", default=str(REPO / "results" / "report.html"))
    ap.add_argument("--title", default="SREGym × ClickStack: diagnosis success by model, arm and skill tier")
    args = ap.parse_args()
    root = Path(args.results_root)
    runs = load_manifests(root, args.campaign)
    if not runs:
        print(f"no campaign manifests under {root / 'campaigns'}", file=sys.stderr)
        return 1
    latest = {}
    for r in runs:
        latest[(r.get("campaign"), r["agent"], r["model"], tuple(r.get("problems") or []), r.get("suite"))] = r
    # exit 2 is SREGym's EXIT_DEPLOY_EXHAUSTED: the suite ran but at least one attempt could not be deployed; keep its results
    failed = [r for r in latest.values() if r.get("exit_code") not in (0, 2, None) or not r.get("results_dir")]
    runs = [r for r in runs if r.get("exit_code") in (0, 2, None) and r.get("results_dir")]
    runs += in_progress_runs(root, runs, args.campaign)
    attempts = load_attempts(root, runs)
    note_files = [Path(n) for n in args.notes]
    for camp in sorted({r.get("campaign") for r in runs if r.get("campaign")}):
        auto = REPO / "clickstack" / "notes" / f"{camp}.md"
        if auto.is_file() and auto not in note_files:
            note_files.append(auto)
    notes_html = "\n".join(render_notes(p) for p in note_files if p.is_file())
    Path(args.out).write_text(build_html(runs, attempts, args.title, failed, notes_html))
    print(f"{len(runs)} runs, {len(attempts)} attempts, {len(failed)} failed launches -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
