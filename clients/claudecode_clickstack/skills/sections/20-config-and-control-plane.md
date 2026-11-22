## Configuration, policy, access and operator objects

The `k8s-objects` and `k8s-changes` records also cover these kinds:

| Kind | Where the interesting part is |
|---|---|
| ConfigMap | `data` (the CoreDNS Corefile in `kube-system/coredns`, application config files, feature-flag files, scripts). Changes to a ConfigMap appear in `k8s-changes` before any Pod picks them up |
| Secret | metadata only; the `data` values are never stored. Use the name, type, labels and age |
| NetworkPolicy | `spec.podSelector`, `spec.ingress`, `spec.egress`, `spec.policyTypes` |
| Role, RoleBinding, ClusterRole, ClusterRoleBinding, ServiceAccount | which subject can do what; a controller or init container that lost a verb shows up here |
| PriorityClass | `value`, `globalDefault`, `preemptionPolicy` |
| ResourceQuota, LimitRange | `status.hard` against `status.used`; default requests and limits |
| HorizontalPodAutoscaler | `spec.minReplicas`, `spec.maxReplicas`, `status.conditions` (`ScalingActive`) |
| PodDisruptionBudget | `spec.minAvailable`, `status.disruptionsAllowed` |
| MutatingWebhookConfiguration, ValidatingWebhookConfiguration | `webhooks[*].clientConfig.service`, `failurePolicy`, `timeoutSeconds`, `namespaceSelector`, `rules`; the `caBundle` is present but not meaningful |
| StorageClass | `provisioner`, `volumeBindingMode` |
| Calico `IPPool`, `IPAMConfig`, `FelixConfiguration`, `BGPPeer`, `BGPConfiguration`, `GlobalNetworkPolicy` | pod CIDR and `disabled`, `strictAffinity`, route reflector peering, cluster-wide policy |

Example, the CoreDNS configuration now:
`SELECT argMax(JSONExtractString(Body, 'data', 'Corefile'), Timestamp) FROM $__sourceTable WHERE $__timeFilter(TimestampTime) AND ServiceName = 'k8s-objects' AND LogAttributes['k8s.object.kind'] = 'ConfigMap' AND LogAttributes['k8s.object.name'] = 'coredns'`.

## Control-plane and cluster-component logs

These are ordinary container logs in Logs, namespace `kube-system`, under `ServiceName`
`kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `etcd`, `coredns`, `kube-proxy` and
`calico-node`. Worth searching:

- `kube-apiserver`: admission webhook calls that fail or time out (`failed calling webhook`,
  `context deadline exceeded`), authorization denials, finalizer and deletion problems.
- `kube-scheduler`: why a Pod is pending, together with the `FailedScheduling` Event note
  (`Insufficient cpu`, `node(s) had untolerated taint`, `volume node affinity conflict`, `exceeded quota`).
- `kube-controller-manager`: ReplicaSet and Deployment reconciliation errors, quota rejections (`exceeded quota`), PVC binding.
- `coredns`: `NXDOMAIN`, `SERVFAIL`, plugin and template errors; compare with the Corefile ConfigMap.
- `calico-node`: BGP peering, IPAM exhaustion (`no IPs available`), policy sync.

## Node logs

`ServiceName:"node-syslog"` on Logs: `/var/log/syslog` and `/var/log/kern.log` of every node (kubelet,
container runtime, kernel), with `ResourceAttributes['k8s.node.name']`. It carries kubelet messages: eviction (`Evicted`, `DiskPressure`, `MemoryPressure`),
image pull failures, probe failures, container runtime errors, and kernel messages such as OOM kills and
conntrack table full.
