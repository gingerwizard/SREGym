## In this environment

There is no `kubectl`, no kubeconfig and no network path to the Kubernetes API. Your view of the cluster is
the observability stack, through three MCP servers:

- `prometheus`: `get_metrics` runs a PromQL query (instant); `get_alerts` lists firing alerts. Kubernetes
  state metrics (`kube_pod_status_phase`, `kube_deployment_status_replicas_available`,
  `kube_pod_container_status_restarts_total`, `kube_pod_container_status_waiting_reason`), container usage
  (`container_memory_working_set_bytes`, `container_cpu_usage_seconds_total`) and application metrics are there.
- `loki`: `get_logs` runs a LogQL query over the last N minutes (for example
  `{namespace="<namespace>"} |= "error"`); `get_labels` and `get_label_values` show the label set
  (`namespace`, `pod`, `container`, `app`) to build queries from.
- `jaeger`: `get_services`, `get_operations`, `get_traces` (recent traces for a service), and
  `get_dependency_graph` (which service calls which).

Bash is available for everything else, such as HTTP calls to the conductor API. Work from symptoms to cause:
alerts and error rates first, then the logs of the failing component, then the state metrics that explain
them. Say what you verified and what you inferred.
