#!/usr/bin/env bash
# Run ON A CLUSTER'S RUNNER: execute its queue file line by line, one campaign.py invocation per (model, arm) pair.
#   nohup bash clickstack/fleet/run_queue.sh ~/fleet/queue > ~/fleet-queue.log 2>&1 &
# Lines already recorded as finished (exit 0 or 2) in the campaign manifest are skipped by campaign.py itself.
set -uo pipefail
Q=${1:-$HOME/fleet/queue}
cd "$(dirname "$0")/../.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . "$HOME/.config/sregym/sregym.env"; set +a
export SREGYM_PROBLEM_SETS_FILE="$PWD/clickstack/problem_sets.json"
log() { echo "[$(date -u +%FT%TZ)] $*"; }
log "queue $Q: $(grep -c . "$Q") pair(s) on $(hostname)"
while read -r campaign suite n model arm; do
    [ -z "${campaign:-}" ] && continue
    log "=== $model x $arm ($suite, $n attempts)"
    uv run python clickstack/campaign.py --name "$campaign" --suite "$suite" --n-attempts "$n" --models "$model" --arms "$arm"
done < "$Q"
log "queue done; filling gaps"
uv run python clickstack/fill_gaps.py "$(head -1 "$Q" | awk '{print $1}')"
log "all done"
