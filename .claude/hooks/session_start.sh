#!/usr/bin/env bash
# SessionStart hook — recuerda el estado del proyecto y las prioridades activas.
# Salida a stdout = mensaje informativo añadido al contexto del sistema.

set -euo pipefail

cat <<'EOF'
🤖 PolyBot v4.0 — Sesión iniciada

Prioridades inamovibles:
  robustez > corrección > observabilidad > rentabilidad > optimización

Fase activa: R1 — CIMENTACIÓN (Junio 2026)
  ✅ R1.1 Paper marathon 100+ ciclos
  ✅ R1.2 MR optimizada con datos reales
  ⏳ R1.3 Dashboard event-driven (P11.4)
  ✅ R1.4 Auditoría de seguridad
  ⏳ R1.5 Cobertura tests críticos ≥ 80%
  ✅ R1.6 Docs sincronizados
  ⏳ R1.7 Auditoría CLOB V2 SDK

Antes de cualquier cambio:
  1. Leer RUTA_IMPLEMENTACION.md y verificar que cabe en una tarea activa.
  2. Diffs mínimos. Cero fallos silenciosos.
  3. Cambios en strategies/ → skill strategy-validation-protocol.
  4. Cambios en risk/ → skill risk-engine-guard + property tests Hypothesis.
  5. Cambios en infrastructure/polymarket/ → skill polymarket-clob-audit.
  6. Cambios en execution/ → skill paper-vs-real-execution.
  7. Activar real trading → skill pre-real-trading-checklist.
  8. Cambios en alembic/ o db/ → skill db-integrity-guard.
  9. Cambios en requirements.txt/pyproject.toml → skill dependency-hygiene.
 10. Cambios en redeem/CTF → skill ctf-onchain-redeem.

Fuente de verdad para dependencias: pyproject.toml (requirements.txt generado).

No-go zones (RFC obligatorio):
  alembic/versions/  src/domain/  monitoring/alerts.yml  k8s/production/  .env

Skills disponibles (8): invoca con la herramienta Skill.
EOF
