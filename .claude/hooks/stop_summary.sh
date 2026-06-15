#!/usr/bin/env bash
# Stop hook — al terminar el turno, recuerda checklist mínimo si se modificó código.

set -euo pipefail

if ! command -v git >/dev/null 2>&1; then
  exit 0
fi

cd /workspaces/Polybot 2>/dev/null || exit 0

# Cambios no commiteados que tocan áreas sensibles.
CHANGED="$(git diff --name-only HEAD 2>/dev/null; git diff --name-only --cached 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null)"

if [[ -z "$CHANGED" ]]; then
  exit 0
fi

NEEDS=()
echo "$CHANGED" | grep -q '^src/strategies/'             && NEEDS+=("walk-forward + paper marathon (skill: strategy-validation-protocol)")
echo "$CHANGED" | grep -q '^src/risk/'                   && NEEDS+=("property tests Hypothesis (skill: risk-engine-guard)")
echo "$CHANGED" | grep -q '^src/infrastructure/polymarket/' && NEEDS+=("auditoría CLOB V2 (skill: polymarket-clob-audit)")
echo "$CHANGED" | grep -q '^src/execution/'              && NEEDS+=("verificar 3 capas para real (skill: paper-vs-real-execution)")
echo "$CHANGED" | grep -q '^\.env\.example'              && NEEDS+=("validar con scripts/check_env.py")

if (( ${#NEEDS[@]} )); then
  echo "" >&2
  echo "📋 Antes de commitear:" >&2
  for item in "${NEEDS[@]}"; do
    echo "   • $item" >&2
  done
fi

exit 0
