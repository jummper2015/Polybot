#!/usr/bin/env bash
# PreToolUse hook — bloquea commits que incluyan archivos basura tipo =*.
# Detecta archivos en git diff/staged/unstaged que empiecen con '='.
# Exit 2 + mensaje a stderr = bloquea la herramienta y muestra el motivo a Claude.

set -euo pipefail

if ! command -v git >/dev/null 2>&1; then
  exit 0  # sin git, no bloqueamos.
fi

cd /workspaces/Polybot 2>/dev/null || exit 0

# Detecta archivos basura =* en el working tree.
TRASH_FILES="$(git ls-files --others --exclude-standard 2>/dev/null | grep '^=' || true)"
STAGED_TRASH="$(git diff --name-only --cached 2>/dev/null | grep '^=' || true)"

if [[ -n "$TRASH_FILES$STAGED_TRASH" ]]; then
  echo "🗑️  ARCHIVOS BASURA DETECTADOS (patrón =*):" >&2
  [[ -n "$TRASH_FILES" ]]  && echo "$TRASH_FILES" | sed 's/^/   unstaged: /' >&2
  [[ -n "$STAGED_TRASH" ]] && echo "$STAGED_TRASH" | sed 's/^/   staged:   /' >&2
  echo "" >&2
  echo "   Estos archivos son basura de 'pip install X >= Y' mal redirigido." >&2
  echo "   Elimínalos con: rm -f $TRASH_FILES $STAGED_TRASH" >&2
  echo "   El patrón =* ya está en .gitignore — git rm si están staged." >&2
  exit 2
fi

exit 0
