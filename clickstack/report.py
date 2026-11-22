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

CORE_DIR = REPO / "clickstack" / "webapp" / "lib"

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
    database = os.environ.get("SREGYM_RESULTS_DB", "sregym_results")
    return {"endpoint": endpoint, "database": database, "schema": schema, "tables": {"scored": f"{database}.attempts_scored", "calls": f"{database}.tool_calls"}}


def build_html(runs: list[dict], attempts: list[dict], title: str, failed: list[dict], notes_html: str = "") -> str:
    judge = sorted({r.get("judge_model") for r in runs if r.get("judge_model")})
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
    cfg = {"data": attempts, "famOrder": FAMILY_ORDER, "famLabel": FAMILY_LABEL, "series": SERIES_LIGHT, "sets": sets, "areaLabel": AREA_LABEL,
           "areaMinTier": AREA_MIN_TIER, "failureModes": FAILURE_MODES, "notesHtml": notes_html, "ch": clickhouse_config()}
    core_js = (CORE_DIR / "report-core.js").read_text()
    css = (CORE_DIR / "report.css").read_text()
    boot = "initReport(document.getElementById('report'), " + json.dumps(cfg).replace("</", "<\\/") + ");"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>{css}</style></head><body>
<header><h1>{html.escape(title)}</h1><div class="muted">Generated {generated}. Campaigns: {html.escape(", ".join(campaigns) or "–")}. Judge: {html.escape(", ".join(judge) or "–")}.
Diagnosis is graded by the LLM judge against the problem's fault specification (pass at composite 0.70); mitigation by SREGym's live oracle (kubectl arms only).
TTD and TTM are SREGym's times from agent start to the diagnosis and mitigation submissions. pass@k counts a (model, arm, problem) cell as passed if any attempt passed. All numbers recompute from the filters. Hover or focus an <span class="info" tabindex="0" data-tip="Every column, tile and filter has one of these; it explains the number and how it is computed.">i</span> for what a number means.
ClickStack MCP server versions seen: {html.escape(", ".join(versions) or "–")} (each attempt's version is in its detail row).</div>{failed_note}</header>
<main id="report"></main>
<script>{core_js}</script>
<script>{boot}</script></body></html>"""


def write_constants(path: Path) -> None:
    """The report's static vocabulary for the web app (clickstack/webapp reads it at build time)."""
    path.write_text(json.dumps({"famOrder": FAMILY_ORDER, "famLabel": FAMILY_LABEL, "series": SERIES_LIGHT, "areaLabel": AREA_LABEL,
                                "areaMinTier": AREA_MIN_TIER, "failureModes": FAILURE_MODES}, indent=1) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", default=str(REPO / "results"))
    ap.add_argument("--campaign", nargs="*", default=None, help="only these campaigns (manifest stems)")
    ap.add_argument("--notes", action="append", default=[], help="Markdown file(s) rendered in the Method & notes tab; clickstack/notes/<campaign>.md is added for every campaign present")
    ap.add_argument("--out", default=str(REPO / "results" / "report.html"))
    ap.add_argument("--write-constants", action="store_true", help="also refresh clickstack/webapp/lib/report-constants.json")
    ap.add_argument("--title", default="SREGym × ClickStack: diagnosis success by model, arm and skill tier")
    args = ap.parse_args()
    if args.write_constants:
        write_constants(CORE_DIR / "report-constants.json")
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
