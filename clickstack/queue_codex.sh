#!/usr/bin/env bash
# Wait for the running campaign to finish, then run the Codex half of the same campaign.
# Before launching it checks the OpenAI credential and keeps only the model ids the API knows.
#
#   nohup bash clickstack/queue_codex.sh lite-sweep > ~/campaign-lite-sweep-codex.log 2>&1 &
#
# Environment overrides: MODELS="gpt-6.1-sol gpt-6.1-terra"  ARMS="codex codex_clickstack ..."  NO_WAIT=1  PROBE_ONLY=1 (check the key and model ids, launch nothing)
set -uo pipefail
NAME=${1:-lite-sweep}
MODELS=${MODELS:-"gpt-6.1-sol gpt-6.1-terra"}
ARMS=${ARMS:-"codex codex_clickstack codex_clickstack_only_t0 codex_clickstack_only_t1 codex_clickstack_only_t2 codex_clickstack_only_t3"}
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
log() { echo "[$(date -u +%FT%TZ)] $*"; }

if [ -z "${NO_WAIT:-}" ]; then
    log "waiting for the running campaign.py to exit"
    while pgrep -f "clickstack/campaign[.]py --name" >/dev/null; do sleep 300; done
    log "no campaign running"
fi

set -a; . "$HOME/.config/sregym/sregym.env"; set +a
base="${OPENAI_BASE_URL:-${OPENAI_API_BASE:-https://api.openai.com/v1}}"
base="${base%/}"
ok_models=()
for m in $MODELS; do
    code=$(curl -s -m 30 -o /tmp/queue_codex_probe.json -w "%{http_code}" "$base/models/$m" -H "Authorization: Bearer ${OPENAI_API_KEY:-}")
    if [ "$code" = "200" ]; then
        ok_models+=("$m"); log "model $m: available at $base"
    else
        log "model $m: HTTP $code $(head -c 200 /tmp/queue_codex_probe.json | tr -d '\n')"
    fi
done
rm -f /tmp/queue_codex_probe.json
if [ -n "${PROBE_ONLY:-}" ]; then
    log "probe only; ${#ok_models[@]} usable model(s): ${ok_models[*]:-none}"
    [ ${#ok_models[@]} -gt 0 ]; exit $?
fi
if [ ${#ok_models[@]} -eq 0 ]; then
    log "no usable model. Put a valid OPENAI_API_KEY (and OPENAI_BASE_URL if the key is for a gateway) in ~/.config/sregym/sregym.env and rerun: NO_WAIT=1 bash clickstack/queue_codex.sh $NAME"
    exit 2
fi
log "launching the Codex half of $NAME: models ${ok_models[*]}; arms $ARMS"
# shellcheck disable=SC2086
exec uv run python clickstack/campaign.py --name "$NAME" --suite sregym-lite --n-attempts 3 --models "${ok_models[@]}" --arms $ARMS
