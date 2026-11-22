## Kubernetes object snapshots (`k8s-objects`)

`ServiceName:"k8s-objects"` on Logs. Every minute, one record per object with the full manifest (spec and
status, `managedFields` removed) in `Body`, so anything `kubectl get -o yaml` or `kubectl describe` would show
is here. Kinds covered: Pod, Deployment, ReplicaSet, StatefulSet, DaemonSet, Job, CronJob, Service,
Endpoints, EndpointSlice, Ingress, PersistentVolumeClaim, PersistentVolume, Node, Namespace.

| Attribute (`LogAttributes`) | Meaning |
|---|---|
| `k8s.object.kind`, `k8s.object.name`, `k8s.namespace.name` | identity (namespace empty for cluster-scoped kinds) |
| `k8s.object.uid`, `k8s.object.resource_version` | identity across snapshots; `resource_version` changes when the object changes |
| `kind`, `metadata`, `spec`, `status` | the same manifest split into top-level JSON strings |

Patterns:

- Latest manifest of each object of a kind (SQL on the Logs source):
  `SELECT LogAttributes['k8s.object.name'] AS name, argMax(Body, Timestamp) AS manifest FROM $__sourceTable WHERE $__timeFilter(TimestampTime) AND ServiceName = 'k8s-objects' AND LogAttributes['k8s.object.kind'] = 'Service' AND LogAttributes['k8s.namespace.name'] = '<namespace>' GROUP BY name`
- One field across objects: `argMax(JSONExtractRaw(Body, 'spec', 'selector'), Timestamp)`,
  `JSONExtractString(Body, 'status', 'phase')`, `JSONExtractRaw(Body, 'spec', 'template', 'spec', 'containers')`.
- A Service and the Pods it should reach: compare the Service `spec.selector` with Pod `metadata.labels`;
  check the Endpoints or EndpointSlice object of the same name for `addresses`. An Endpoints object with no
  `subsets` means no Pod matches the selector or none is Ready.
- A Deployment's intent: `spec.template.spec.containers[*].image`, `env`, `ports`, `resources`,
  `livenessProbe`, `readinessProbe`, `args`; `spec.replicas` against `status.availableReplicas`;
  `spec.strategy.rollingUpdate` for stuck rollouts.
- A Pod's state: `status.phase`, `status.conditions`, `status.containerStatuses[*].state`,
  `lastState.terminated.reason` (`OOMKilled`, `Error`), `restartCount`, `spec.nodeName`,
  `spec.dnsPolicy`, `spec.hostAliases`, `spec.securityContext.sysctls`, `spec.tolerations`, `spec.affinity`.
- A Node: `spec.taints`, `status.allocatable`, `status.conditions`, `status.nodeInfo`.
- Volumes: PersistentVolumeClaim `status.phase`, `spec.accessModes`, `spec.storageClassName`;
  PersistentVolume `spec.nodeAffinity`.

## Kubernetes object changes (`k8s-changes`)

`ServiceName:"k8s-changes"` on Logs. One record per create, update or delete of the kinds above,
with the object after the change in `Body` (`object` field) and `LogAttributes['k8s.object.event']`
set to `ADDED`, `MODIFIED` or `DELETED`. This is the change history kubectl does not have.

- What changed recently in a namespace: `clickstack_search` with
  `ServiceName:"k8s-changes" AND LogAttributes.k8s.namespace.name:"<namespace>" AND LogAttributes.k8s.object.event:"MODIFIED"`,
  excluding noisy kinds (`NOT LogAttributes.k8s.object.kind:"Pod"` hides status-only updates) and sorted by time.
- What a change did: compare the `MODIFIED` record with the previous record of the same `k8s.object.uid`
  (`JSONExtractRaw(Body, 'object', 'spec')` on both), or with the first `k8s-objects` snapshot of that uid.
- Pods created, killed or evicted: `k8s.object.kind:"Pod"` with `ADDED` / `DELETED`, together with
  `k8s-events` for the reason.
