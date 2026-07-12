#!/usr/bin/env bash
# PreToolUse hook — detecta drift entre requirements.txt y pyproject.toml.
# Compara que todas las dependencias de pyproject.toml aparezcan en requirements.txt.
# Exit 2 = drift detectado → bloquea la herramienta.
# Exit 0 = sin drift (o archivos no tocados).

set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  exit 0  # sin python, no podemos verificar.
fi

cd /workspaces/Polybot 2>/dev/null || exit 0

# Solo verifica si requirements.txt o pyproject.toml fueron modificados.
PAYLOAD="$(cat 2>/dev/null || true)"
FILE_PATH="$(echo "${PAYLOAD:-}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null || true)"

# Si no se está tocando ninguno de los dos archivos, skip.
if [[ "$FILE_PATH" != *"requirements.txt" && "$FILE_PATH" != *"pyproject.toml" ]]; then
  exit 0
fi

# Compara dependencias principales: extrae nombres de paquetes de pyproject.toml
# y verifica que existan en requirements.txt.
PYPROJECT_DEPS=$(python3 -c "
import sys, re
sys.path.insert(0, '.')
deps = set()
try:
    with open('pyproject.toml') as f:
        content = f.read()
    # Extrae tanto [project.dependencies] como [project.optional-dependencies].
    # Busca todas las lineas que empiezan con comillas y contienen == o >=.
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('\"') and ('==' in line or '>=' in line):
            # Extrae nombre del paquete: '\"paquete[extra]==x.y' -> 'paquete[extra]'
            pkg = line.split('\"')[1].split('==')[0].split('>=')[0].split('<=')[0].strip()
            deps.add(pkg)
except Exception:
    pass

req_deps = set()
try:
    with open('requirements.txt') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and ('==' in line or '>=' in line):
                pkg = line.split('==')[0].split('>=')[0].split('<=')[0].strip()
                req_deps.add(pkg)
except Exception:
    pass

missing_in_req = deps - req_deps
missing_in_py = req_deps - deps
if missing_in_req:
    print('MISSING_IN_REQ:' + ','.join(sorted(missing_in_req)))
if missing_in_py:
    print('MISSING_IN_PY:' + ','.join(sorted(missing_in_py)))
" 2>/dev/null || true)

if [[ -n "$PYPROJECT_DEPS" ]]; then
  echo "📦 DRIFT DETECTADO entre pyproject.toml y requirements.txt:" >&2
  echo "$PYPROJECT_DEPS" | tr ',' '\n' | while read -r line; do
    case "$line" in
      MISSING_IN_REQ:*)  echo "   En pyproject.toml pero NO en requirements.txt: ${line#MISSING_IN_REQ:}" >&2 ;;
      MISSING_IN_PY:*)   echo "   En requirements.txt pero NO en pyproject.toml: ${line#MISSING_IN_PY:}" >&2 ;;
    esac
  done
  echo "" >&2
  echo "   pyproject.toml es la fuente de verdad. Sincroniza requirements.txt." >&2
  exit 2
fi

exit 0
