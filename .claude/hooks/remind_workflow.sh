#!/usr/bin/env bash
# UserPromptSubmit hook — añade un recordatorio compacto del workflow al contexto.
# Sólo se imprime si el prompt parece tocar áreas críticas.

set -euo pipefail

PAYLOAD="$(cat)"

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

PROMPT="$(echo "$PAYLOAD" | jq -r '.prompt // empty' | tr '[:upper:]' '[:lower:]')"

# Si el prompt menciona áreas sensibles, recordamos el skill aplicable.
remind() {
  echo "📌 Recordatorio: $1" >&2
}

case "$PROMPT" in
  *strateg*|*mean*revers*|*regime*|*ensemble*|*event*detect*|*backtest*|*walk*forward*|*monte*carlo*)
    remind "Cambios de estrategia → skill strategy-validation-protocol. Walk-forward + paper antes de mergear."
    ;;
esac

case "$PROMPT" in
  *risk*|*kelly*|*drawdown*|*exposure*|*hedge*|*kill*switch*)
    remind "Cambios de risk → skill risk-engine-guard. Property tests con Hypothesis obligatorios."
    ;;
esac

case "$PROMPT" in
  *polymarket*|*clob*|*pusd*|*gamma*|*websocket*|*ws*client*|*builder*code*|*signature*type*)
    remind "Tocar Polymarket → skill polymarket-clob-audit. Verificar SDK v2, builder code y signature_type."
    ;;
esac

case "$PROMPT" in
  *execution*|*paper*|*real*trading*|*canary*|*pin*|*confirm*|*idempot*)
    remind "Cambios de ejecución → skill paper-vs-real-execution. 3 capas para REAL."
    ;;
esac

case "$PROMPT" in
  *activ*real*|*mode*real*|*production*|*go*live*)
    remind "Pre-real → skill pre-real-trading-checklist. Los 6 pasos + R1.3/R1.5/R1.7/R2.2 verdes."
    ;;
esac

exit 0
