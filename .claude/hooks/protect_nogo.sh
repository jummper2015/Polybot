#!/usr/bin/env bash
# PreToolUse hook — protege no-go zones de edición/escritura/borrado.
# Lee el evento JSON por stdin (tool_input.file_path o tool_input.command).
# Exit 2 + mensaje a stderr = bloquea la herramienta y muestra el motivo a Claude.

set -euo pipefail

# Necesitamos jq para parsear el JSON del hook.
if ! command -v jq >/dev/null 2>&1; then
  exit 0  # sin jq, no bloqueamos (degradación amable).
fi

PAYLOAD="$(cat)"

TOOL_NAME="$(echo "$PAYLOAD" | jq -r '.tool_name // empty')"
FILE_PATH="$(echo "$PAYLOAD" | jq -r '.tool_input.file_path // empty')"
COMMAND="$(echo "$PAYLOAD" | jq -r '.tool_input.command // empty')"

# Patrones de no-go (deben coincidir con CLAUDE.md).
NOGO_PATTERNS=(
  "alembic/versions/"
  "monitoring/alerts.yml"
  "k8s/production/"
)

is_nogo_path() {
  local p="$1"
  [[ -z "$p" ]] && return 1
  for pat in "${NOGO_PATTERNS[@]}"; do
    if [[ "$p" == *"$pat"* ]]; then return 0; fi
  done
  # src/domain/: solo bloqueamos Write y Edit, no Read.
  if [[ "$p" == *"src/domain/"* && ( "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "NotebookEdit" ) ]]; then
    return 0
  fi
  # .env real (no .env.example): solo bloquear Write/Edit.
  if [[ "$p" == *"/.env" && "$p" != *".env.example" && ( "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ) ]]; then
    return 0
  fi
  return 1
}

if [[ -n "$FILE_PATH" ]] && is_nogo_path "$FILE_PATH"; then
  echo "🛑 NO-GO ZONE: '$FILE_PATH' está protegido por CLAUDE.md." >&2
  echo "   Editar este path requiere RFC explícito. Pregunta al usuario antes de continuar." >&2
  exit 2
fi

# Comandos peligrosos en Bash.
if [[ "$TOOL_NAME" == "Bash" && -n "$COMMAND" ]]; then
  if echo "$COMMAND" | grep -Eq '(^|[[:space:]])rm[[:space:]]+(-[a-zA-Z]*[rf][a-zA-Z]*[[:space:]]+)?(/|~/?$|\.)'; then
    echo "🛑 Comando 'rm' contra ruta amplia ('/', '~', '.') bloqueado. Especifica un path concreto." >&2
    exit 2
  fi
  if echo "$COMMAND" | grep -Eq 'git[[:space:]]+push[[:space:]]+.*--force([^-]|$)|git[[:space:]]+push[[:space:]]+.*-f([^-]|$)'; then
    echo "🛑 'git push --force' bloqueado. Usa --force-with-lease si es estrictamente necesario y confirma con el usuario." >&2
    exit 2
  fi
  if echo "$COMMAND" | grep -Eq 'git[[:space:]]+reset[[:space:]]+--hard'; then
    echo "⚠️  'git reset --hard' es destructivo. Confirma con el usuario antes de ejecutar." >&2
    exit 2
  fi
  if echo "$COMMAND" | grep -Eq '(--no-verify|--no-gpg-sign)'; then
    echo "🛑 Skipping hooks/signature (--no-verify, --no-gpg-sign) está prohibido por CLAUDE.md." >&2
    exit 2
  fi
fi

exit 0
