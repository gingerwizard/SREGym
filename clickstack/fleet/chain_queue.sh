#!/usr/bin/env bash
# Run ON A RUNNER: when process PID exits, start run_queue.sh on QUEUE, logging to ~/fleet-<LOG>.log.
#   bash clickstack/fleet/chain_queue.sh <pid> <queue file> <log name>
set -u
PID=$1; QUEUE=$2; LOG=$3
nohup bash -c "while kill -0 $PID 2>/dev/null; do sleep 60; done; cd ~/SREGym && exec bash clickstack/fleet/run_queue.sh $QUEUE" > ~/fleet-$LOG.log 2>&1 &
echo "chained $QUEUE after pid $PID (waiter $!)"
