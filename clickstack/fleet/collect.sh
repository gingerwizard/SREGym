#!/usr/bin/env bash
# Run ON THE PRIMARY RUNNER: pull every other cluster's results and manifests, rewrite their result paths under
# results/<cluster>/, merge the manifests into the primary's, and regenerate the report.
#   bash clickstack/fleet/collect.sh
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
for entry in $(python3 -c "import json;[print(k+'='+v['runner']) for k,v in json.load(open('clickstack/fleet/clusters.json')).items() if k!='c1']"); do
    c=${entry%%=*}; runner=${entry#*=}
    mkdir -p "results/$c/campaigns"
    rsync -az --exclude '.runtime' --exclude 'invalid' -e "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20" "$runner:~/SREGym/results/" "results/$c/" 2>/dev/null || { echo "$c: rsync failed"; continue; }
    for mf in results/$c/campaigns/*.jsonl; do
        [ -f "$mf" ] || continue
        name=$(basename "$mf")
        # rewrite results_dir and log paths to the cluster's subtree, dedupe by started_at
        python3 - "$mf" "results/campaigns/$name" "$c" "$name" <<'PY'
import json, sys
src, dst, c, name = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
have = set()
try:
    for l in open(dst):
        if l.strip(): have.add(json.loads(l).get("started_at"))
except FileNotFoundError:
    pass
added = 0
with open(dst, "a") as out:
    for l in open(src):
        if not l.strip(): continue
        r = json.loads(l)
        if r.get("started_at") in have: continue
        for k in ("results_dir", "log"):
            if r.get(k): r[k] = f"results/{c}/" + r[k].split("results/", 1)[1]
        r["cluster"] = c
        out.write(json.dumps(r) + "\n"); added += 1
print(f"{c}: {name}: {added} new record(s)")
PY
    done
done
uv run python clickstack/report.py --out results/report.html | tail -1
