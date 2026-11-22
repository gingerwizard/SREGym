#!/usr/bin/env bash
# One problem, one attempt, the kubectl + ClickStack arm, one model per provider.
#   bash clickstack/shakedown_codex.sh        (env overrides: NAME PROBLEM OPENAI_MODEL CLAUDE_MODEL)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . "$HOME/.config/sregym/sregym.env"; set +a
NAME=${NAME:-codex-shakedown}
PROBLEM=${PROBLEM:-service_wrong_pod_selection_hotel_reservation}
OPENAI_MODEL=${OPENAI_MODEL:-gpt-6.1-sol}
CLAUDE_MODEL=${CLAUDE_MODEL:-claude-opus-5-5}
uv run python clickstack/campaign.py --name "$NAME" --problem "$PROBLEM" --n-attempts 1 --models "$OPENAI_MODEL" --arms codex_clickstack
uv run python clickstack/campaign.py --name "$NAME" --problem "$PROBLEM" --n-attempts 1 --models "$CLAUDE_MODEL" --arms claudecode_clickstack
