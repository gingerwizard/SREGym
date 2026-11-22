#!/usr/bin/env bash
# Pause a running campaign.py at its next pair boundary, run a command while the cluster is free, then resume it.
#
#   nohup bash clickstack/run_between_pairs.sh <campaign.py pid> <command...> > ~/between-pairs.log 2>&1 &
#
# Note: pgrep/pkill patterns elsewhere match "clickstack/campaign[.]py --name"; keep that literal out of ad-hoc shells.
# Mechanism: SIGSTOP the campaign process. Its current main.py child keeps running to the end of the pair
# (its log file descriptor is its own, nothing flows through the parent). When that child has exited, the
# cluster is free: run the command. Then SIGCONT the campaign, which reaps the child, records the pair in the
# manifest with the real exit code, and continues with the next pair. No attempt is lost.
set -uo pipefail
PID=$1; shift
log() { echo "[$(date -u +%FT%TZ)] $*"; }
if ! kill -0 "$PID" 2>/dev/null; then log "no process $PID"; exit 1; fi
kill -STOP "$PID" && log "paused campaign.py pid $PID; its current pair keeps running"
trap 'kill -CONT "$PID" 2>/dev/null; log "resumed campaign.py pid $PID (exit trap)"' EXIT
while true; do
    live=$(ps -o pid=,stat= --ppid "$PID" | awk '$2 !~ /^Z/ {print $1}')
    [ -z "$live" ] && break
    sleep 30
done
log "pair finished; waiting for leftover agent containers"
for _ in $(seq 1 40); do docker ps --format '{{.Names}}' | grep -Eq '^(sregym-|evaluation-egress-proxy)' || break; sleep 15; done
log "cluster is free; running: $*"
"$@"
rc=$?
log "command exit $rc"
kill -CONT "$PID" && log "resumed campaign.py pid $PID"
trap - EXIT
exit $rc
