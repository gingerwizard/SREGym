#!/usr/bin/env bash
# Run from the runner after the Ansible playbook: give each node Google's Docker Hub mirror
# (avoids anonymous pull-rate limits) and raise inotify limits for log tailing on busy nodes.
set -euo pipefail
# Node names as arguments; default to the first cluster's nodes.
NODES=("$@"); [ ${#NODES[@]} -eq 0 ] && NODES=(sregym-control sregym-worker-1 sregym-worker-2)
for h in "${NODES[@]}"; do
  echo "== $h"
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new sregym@"$h" 'sudo bash -s' <<'REMOTE'
set -e
python3 - <<'PY'
import json, os
p = "/etc/docker/daemon.json"
cfg = json.load(open(p)) if os.path.exists(p) else {}
mirrors = cfg.get("registry-mirrors", [])
if "https://mirror.gcr.io" not in mirrors:
    cfg["registry-mirrors"] = mirrors + ["https://mirror.gcr.io"]
    json.dump(cfg, open(p, "w"), indent=2)
    print("daemon.json updated")
else:
    print("mirror already set")
PY
cat > /etc/sysctl.d/90-sregym.conf <<'SYS'
fs.inotify.max_user_instances = 1024
fs.inotify.max_user_watches = 1048576
SYS
sysctl -q --system >/dev/null
systemctl restart docker
sleep 5
docker info 2>/dev/null | grep -A2 "Registry Mirrors" | tail -1
systemctl is-active docker kubelet cri-docker | paste -sd' '
REMOTE
done
