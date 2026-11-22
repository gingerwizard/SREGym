# Skill verification before scoring

Run against a healthy deployment (`uv run main.py --use-external-harness --problem <any>` leaves the
application up) with `claude` on the runner using the same MCP endpoint and the tier skill under test.
Each question must be answered correctly from ClickStack alone; the second column is how to get the
ground truth with kubectl on the runner.

| Tier | Question to the agent | Ground truth |
|---|---|---|
| 0 | Which services received requests in the last 15 minutes, and which had the highest p95 latency? | `clickstack_service_health` versus Jaeger; sanity only |
| 0 | Which pods in namespace N restarted in the last 30 minutes, and why? | `kubectl get pods -n N -o wide`; `kubectl describe pod` (Last State) |
| 0 | List the Warning events in namespace N in the last 30 minutes with their reasons | `kubectl get events -n N --field-selector type=Warning` |
| 0 | What is the current CPU limit of container C in pod P? | `kubectl get pod P -n N -o jsonpath='{.spec.containers[?(@.name=="C")].resources.limits.cpu}'` |
| 0 | Is the TCP probe to service S:port up right now? | `probe_success` for that target; `kubectl exec` a curl/nc |
| 1 | What is the selector of Service S, and which pods match it? | `kubectl get svc S -n N -o jsonpath='{.spec.selector}'`; `kubectl get pods -n N -l <selector>` |
| 1 | Which image and environment variables does Deployment D run? | `kubectl get deploy D -n N -o yaml` |
| 1 | What changed in namespace N in the last 20 minutes, other than pod status? | `k8s-changes` versus `kubectl get events`; the operator's own actions |
| 1 | Does Endpoints S have addresses? | `kubectl get endpoints S -n N` |
| 1 | What taints does node X have, and how much memory is allocatable? | `kubectl describe node X` |
| 2 | What does the CoreDNS Corefile contain? | `kubectl -n kube-system get cm coredns -o yaml` |
| 2 | Which NetworkPolicies select pods in namespace N, and what do they allow? | `kubectl get netpol -n N -o yaml` |
| 2 | Which webhooks are configured, with which failure policy and timeout? | `kubectl get mutatingwebhookconfigurations,validatingwebhookconfigurations -o yaml` |
| 2 | Is there a ResourceQuota in namespace N, and how much of it is used? | `kubectl describe quota -n N` |
| 2 | Did the API server log any admission webhook failures in the last 30 minutes? | `kubectl -n kube-system logs kube-apiserver-<control> \| grep -i webhook` |
| 3 | What is the ephemeral port range and conntrack usage on node X? | `ssh X sysctl net.ipv4.ip_local_port_range net.netfilter.nf_conntrack_count` |
| 3 | What are the kubelet eviction thresholds on node X? | `ssh X grep -A5 evictionHard /var/lib/kubelet/config.yaml` |
| 3 | How far is node X's clock from the collector's clock? | `ssh X chronyc tracking` |

Record for each question: answered correctly / incorrectly / could not find, the tools called, and the
time taken. A tier passes when every question at that tier and below is answered correctly.
