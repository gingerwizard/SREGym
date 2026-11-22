## Node state records (`node-state`)

`ServiceName = 'node-state'` in `default.otel_logs`. One record per node per minute with the node configuration that is
neither a Kubernetes object nor a metric. The JSON is in `Body` and flattened into `LogAttributes` (one key per field):

| Attribute | Meaning and what normal looks like |
|---|---|
| `node` | node name |
| `sysctl.net.ipv4.ip_local_port_range` | ephemeral port range; normally `32768 60999`. A narrow range exhausts outbound connections |
| `sysctl.net.netfilter.nf_conntrack_count`, `sysctl.net.netfilter.nf_conntrack_max` | connection-tracking table usage; count near max means dropped connections |
| `file_nr` (`allocated 0 max`), `sysctl.fs.file-max`, `sysctl.fs.nr_open` | open file handles against the limit |
| `sysctl.fs.inotify.max_user_instances`, `sysctl.fs.inotify.max_user_watches` | inotify limits |
| `sysctl.vm.max_map_count`, `sysctl.vm.swappiness`, `sysctl.kernel.pid_max`, `sysctl.kernel.threads-max` | memory and process limits |
| `sysctl.net.core.somaxconn`, `sysctl.net.ipv4.tcp_max_syn_backlog`, `sysctl.net.ipv4.tcp_tw_reuse` | listen backlog and TIME_WAIT reuse |
| `chrony_tracking_csv` | chrony `tracking` output; the fifth field is the system clock offset in seconds, normally under 0.01 |
| `time_epoch` | the node's clock, to compare with `Timestamp` (collector time) for drift |
| `kubelet_config`, `kubelet_flags` | the kubelet configuration file and flags: eviction thresholds (`evictionHard`), `maxPods`, `imageGCHighThresholdPercent`, cgroup driver |
| `hosts_file` | `/etc/hosts` on the node |
| `disk_df` | `df` for the root, kubelet, container runtime and local-volume paths |
| `services.kubelet`, `services.docker`, `services.cri-docker`, `services.chrony` | `active` or not |
| `loadavg`, `mem_available_kb`, `uptime_s`, `kernel`, `listening_ports` | general node health |

Latest record per node: `SELECT LogAttributes['node'] AS node, argMax(Body, Timestamp) AS state FROM default.otel_logs WHERE TimestampTime >= now() - INTERVAL 1 HOUR AND ServiceName = 'node-state' GROUP BY node LIMIT 20`;
one setting across nodes: `argMax(LogAttributes['sysctl.net.ipv4.ip_local_port_range'], Timestamp)` grouped by `node`.
