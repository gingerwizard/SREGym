#!/usr/bin/env bash
# Run ON THE PRIMARY RUNNER: one block per cluster: pairs finished in the current queue, the running pair, and any follow-on queues.
cd "$(dirname "$0")/../.." || exit 1
read -r -d '' INNER <<'INNER_EOF'
cd ~/SREGym
q=$(ls ~/fleet/queue ~/fleet/*.queue 2>/dev/null | head -1)
done=$(sed "/queue done/q" ~/fleet-queue.log 2>/dev/null | grep -c "^    -> "); done=${done:-0}
total=$(grep -c . "$q" 2>/dev/null || echo "?")
echo "  pairs finished: $done / $total; running: $(pgrep -f "clickstack/campaign[.]py --name" >/dev/null && echo yes || echo no)"
grep -E "^===|-> " ~/fleet-queue.log 2>/dev/null | tail -2 | sed "s/^/  /"
for extra in ~/fleet-queue2.log ~/fleet-queue3.log ~/fleet-queue4.log ~/fleet-queue5.log; do
    [ -f "$extra" ] || continue
    name=$(basename "$extra" .log | sed s/fleet-//)
    n=$(sed "/queue done/q" "$extra" 2>/dev/null | grep -c "^    -> "); t=$(grep -c . ~/fleet/"$name" 2>/dev/null); echo "  -- $name: ${n:-0} pair(s) finished of ${t:-?}$(grep -q "queue done" "$extra" && echo ", filling gaps or done")"
    grep -E "^===|-> " "$extra" 2>/dev/null | tail -1 | sed "s/^/  /"
done
INNER_EOF
for entry in $(python3 -c "import json;[print(k+'='+v['runner']) for k,v in json.load(open('clickstack/fleet/clusters.json')).items()]"); do
    c=${entry%%=*}; runner=${entry#*=}
    echo "== $c ($runner)"
    if [ "$c" = "c1" ]; then bash -c "$INNER"; else ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 "$runner" bash -s <<<"$INNER" 2>/dev/null; fi
done
