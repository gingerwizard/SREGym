#!/usr/bin/env bash
# Run the ClickHouse-SQL arms campaign once the cluster is idle: first a shakedown (one problem, one attempt,
# one SQL-only arm per harness), then the full sql-sweep for the chosen models, then arm the gap fill.
#
#   nohup bash clickstack/queue_sql.sh > ~/campaign-sql-sweep.log 2>&1 &
#
# Environment overrides: CLAUDE_MODEL (claude-fable-5-2), OPENAI_MODEL (gpt-6.1-sol), NAME (sql-sweep), NO_WAIT=1, SKIP_SHAKEDOWN=1
set -uo pipefail
NAME=${NAME:-sql-sweep}
CLAUDE_MODEL=${CLAUDE_MODEL:-claude-fable-5-2}
OPENAI_MODEL=${OPENAI_MODEL:-gpt-6.1-sol}
PROBLEM=${PROBLEM:-service_wrong_pod_selection_hotel_reservation}
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
log() { echo "[$(date -u +%FT%TZ)] $*"; }

if [ -z "${NO_WAIT:-}" ]; then
    log "waiting for the cluster to be idle (no campaign, Codex queue or gap fill running)"
    while pgrep -f "clickstack/campaign[.]py --name|clickstack/queue_code[x][.]sh|clickstack/fill_gap[s][.]py" >/dev/null; do sleep 300; done
    log "idle"
fi
set -a; . "$HOME/.config/sregym/sregym.env"; set +a
if ! systemctl is-active --quiet sregym-chsql-mcp; then log "ClickHouse MCP service is not active; aborting"; exit 2; fi

if [ -z "${SKIP_SHAKEDOWN:-}" ]; then
    log "shakedown: $PROBLEM, one attempt, SQL-only tier 2 under each harness"
    uv run python clickstack/campaign.py --name "$NAME-shakedown" --problem "$PROBLEM" --n-attempts 1 --models "$CLAUDE_MODEL" --arms claudecode_chsql_only_t2 --rerun
    uv run python clickstack/campaign.py --name "$NAME-shakedown" --problem "$PROBLEM" --n-attempts 1 --models "$OPENAI_MODEL" --arms codex_chsql_only_t2 --rerun
    bad=$(python3 - "$NAME-shakedown" <<'PY'
import json, sys
from pathlib import Path
recs = [json.loads(l) for l in Path(f"results/campaigns/{sys.argv[1]}.jsonl").read_text().splitlines() if l.strip()]
latest = {}
for r in recs: latest[(r["agent"], r["model"])] = r
print(" ".join(f"{a}x{m}={r['exit_code']}" for (a, m), r in latest.items() if r["exit_code"] not in (0, 2)))
PY
)
    if [ -n "$bad" ]; then log "shakedown failed ($bad); not launching $NAME"; exit 3; fi
    log "shakedown passed"
fi

log "launching $NAME: Claude Code arms with $CLAUDE_MODEL"
uv run python clickstack/campaign.py --name "$NAME" --suite sregym-lite --n-attempts 3 --models "$CLAUDE_MODEL" \
    --arms claudecode_chsql claudecode_chsql_only_t0 claudecode_chsql_only_t1 claudecode_chsql_only_t2 claudecode_chsql_only_t3
log "launching $NAME: Codex arms with $OPENAI_MODEL"
uv run python clickstack/campaign.py --name "$NAME" --suite sregym-lite --n-attempts 3 --models "$OPENAI_MODEL" \
    --arms codex_chsql codex_chsql_only_t0 codex_chsql_only_t1 codex_chsql_only_t2 codex_chsql_only_t3
log "arming the gap fill for $NAME"
exec uv run python clickstack/fill_gaps.py "$NAME" --wait
