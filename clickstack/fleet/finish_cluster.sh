#!/usr/bin/env bash
# Run ON THE PRIMARY RUNNER after the Ansible playbook for cluster cN has finished:
#   bash clickstack/fleet/finish_cluster.sh c3
# 1. copies the fetched kubeconfig to that cluster's runner, 2. tunes the nodes, 3. deploys the collectors into the
# cluster writing to its own database, 4. rebuilds the SQL skills for that database on the runner, 5. verifies.
set -euo pipefail
C=$1
cd "$(dirname "$0")/../.."
RUNNER=$(python3 -c "import json;print(json.load(open('clickstack/fleet/clusters.json'))['$C']['runner'])")
DB=$(python3 -c "import json;print(json.load(open('clickstack/fleet/clusters.json'))['$C']['database'])")
NODES=$(python3 -c "import json;print(' '.join(json.load(open('clickstack/fleet/clusters.json'))['$C']['nodes']))")
KUBECONFIG_SRC="$HOME/fleet/$C/.kube/config"
log() { echo "[$(date -u +%FT%TZ)] $C: $*"; }
[ -f "$KUBECONFIG_SRC" ] || { log "no kubeconfig at $KUBECONFIG_SRC (playbook not finished?)"; exit 1; }
log "nodes: $(KUBECONFIG=$KUBECONFIG_SRC kubectl get nodes --no-headers 2>&1 | awk '{print $1":"$2}' | tr '\n' ' ')"
bash clickstack/node-tune.sh $NODES 2>&1 | grep -E "==|mirror|active|daemon" | tr "\n" " " ; echo; log "nodes tuned"
log "deploying collectors into database $DB"
KUBECONFIG=$KUBECONFIG_SRC CLICKSTACK_DATABASE=$DB bash clickstack/deploy.sh 2>&1 | tail -8
log "installing kubeconfig and database settings on $RUNNER"
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$RUNNER" "mkdir -p ~/.kube" && scp -o StrictHostKeyChecking=no -q "$KUBECONFIG_SRC" "$RUNNER:~/.kube/config"
ssh -o StrictHostKeyChecking=no "$RUNNER" "cd ~/SREGym && export PATH=\$HOME/.local/bin:\$PATH && grep -q '^export CLICKSTACK_DATABASE=' ~/.config/sregym/sregym.env || echo 'export CLICKSTACK_DATABASE=$DB' >> ~/.config/sregym/sregym.env; sed -i 's/^export CLICKSTACK_DATABASE=.*/export CLICKSTACK_DATABASE=$DB/' ~/.config/sregym/sregym.env; sed -i 's/^CLICKHOUSE_DATABASE=.*/CLICKHOUSE_DATABASE=$DB/' ~/.config/sregym/chsql-mcp.env; sudo -n systemctl restart sregym-chsql-mcp; uv run python clients/claudecode_chsql/skills/build_skills.py $DB >/dev/null; uv run python clients/claudecode_clickstack/skills/build_skills.py $DB >/dev/null; kubectl get nodes --no-headers | wc -l"
log "done"
