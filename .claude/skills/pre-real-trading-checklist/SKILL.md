---
name: pre-real-trading-checklist
description: >
  Checklist obligatorio R2.1 antes de activar real trading con capital propio
  en Polymarket. Activa cuando el usuario pide habilitar real trading, cuando
  se prepara canary deploy, cuando se ejecuta validate_criteria.py /
  check_env.py / run_paper_marathon.py / optimize_mr.py, o cuando se discute
  el paso de paper a canary o de canary a real. NO activa para cambios de
  código rutinarios — sólo para el flujo de habilitación operativa.
---

# Skill: Pre-Real-Trading Checklist (R2.1)

## Propósito

Garantizar que **ningún capital real** se ponga a riesgo sin completar los 6 pasos del checklist. Cada paso tiene criterio de éxito objetivo y comando reproducible.

---

## Cuándo activa este skill

- Usuario pide "activar real trading", "habilitar producción", "/mode real".
- Preparación de canary deploy (R2.2).
- Ejecución de `scripts/check_env.py`, `scripts/validate_criteria.py`, `scripts/run_paper_marathon.py`, `scripts/optimize_mr.py`.
- Discusión sobre escalado de capital (25% → 50% → 100% de R3.1).
- Tras incidente que pause real trading, antes de reactivarlo.

NO activa para edición rutinaria de strategies, risk o infra.

---

## Estado actual del checklist (snapshot 2026-06-07)

| Paso | Tarea | Criterio | Estado |
|---|---|---|---|
| 1 | `check_env.py` — paper + real | Sin errores en ambos modos | ✅ |
| 2 | Recording 168h activo | Parquet en `data/parquet/` con manifest fresco | 🔄 verificar |
| 3 | `optimize_mr.py --csv data/parquet/` | `optimal_params_mr_real.json` generado | ✅ R1.2 |
| 4 | `validate_criteria.py` | Sharpe > 0.8, PF > 1.2, MaxDD < 20% | 🔄 verificar |
| 5 | Paper marathon 100 ciclos | 0 excepciones no manejadas, shutdown limpio | ✅ R1.1 |
| 6 | `/mode real <PIN>` | Sólo tras 1-5 verdes | ⛔ NO antes de R1.3+R1.5+R1.7 |

Antes del Paso 6, además **deben** estar verdes:
- R1.3 Dashboard event-driven (visibilidad de HALTs en producción).
- R1.5 Cobertura de tests críticos ≥ 80% en routers + execution + telegram.
- R1.7 Auditoría CLOB V2 cerrada.
- R2.2 Canary 72h continuo sin drawdown > 5% diario ni errores > 5/min.

---

## Procedimiento (en orden, no saltar)

### Paso 1 — Validación de entorno

```bash
python scripts/check_env.py            # modo paper
python scripts/check_env.py --phase real  # modo real
```

Criterio: cero errores. Verificar que `POLYMARKET_BUILDER_CODE` y `POLYMARKET_SIGNATURE_TYPE` están presentes (no requeridos en paper, sí en real).

### Paso 2 — Recording activo y reciente

```bash
ls -lt data/parquet/ | head
python scripts/watchdog_recording.py --status
```

Criterio: último manifest < 1h, Parquet por asset (BTC, ETH) por ventana (M5, M15), zstd.

### Paso 3 — Optimización con datos reales

```bash
python scripts/optimize_mr.py --csv data/parquet/
```

Criterio: `data/optimization/optimal_params_mr_real.json` actualizado, parámetros estables entre folds (no overfitting).

### Paso 4 — Validar criterios cuantitativos

```bash
python scripts/validate_criteria.py
```

Criterio:
- Sharpe out-of-sample > **0.8** (mínimo aceptable con datos reales).
- Profit factor > **1.2**.
- Max drawdown < **20%**.
- Walk-forward: ≥ 5 folds consistentes.

Si cualquier criterio falla → **STOP**. No avanzar al Paso 5.

### Paso 5 — Paper marathon

```bash
python scripts/run_paper_marathon.py --cycles 100
```

Criterio:
- 100+ ciclos sin excepciones no manejadas.
- Shutdown graceful en cada ciclo.
- Sin memory leaks (RSS estable ±10%).
- Métricas: latencia p95 < 100ms en paths críticos.

### Paso 6 — Activar real trading

Sólo si pasos 1-5 ✅ **y** R1.3, R1.5, R1.7, R2.2 ✅.

```
/mode real <PIN-de-6-dígitos>
```

Reglas de escalado (R3.1):
1. **Semana 1:** 25% de capital target.
2. **Semana 2:** 50% si drawdown semanal < 5%.
3. **Semana 3:** 100% si drawdown acumulado < 8%.

En cualquier momento: drawdown > 10% diario → kill switch automático (`risk/drawdown.py`).

---

## Lo que este skill **rechaza** explícitamente

- Saltar pasos para "ir más rápido".
- Activar real sin canary 72h previo.
- Subir capital sin esperar la ventana semanal de cada escalón.
- Modificar PIN durante una sesión activa de real trading.
- Lanzar real trading con tests fallando.
- Lanzar real trading con vulnerabilidades HIGH/CRITICAL en `pip-audit` no resueltas.

---

## Bandera de emergencia: cómo abortar

1. Telegram: `/mode paper` (vuelve a paper inmediato, cierra órdenes pendientes).
2. Si Telegram no responde: `kubectl scale deployment polybot --replicas=0` o `docker compose stop`.
3. Verificar en `audit_events` que no quedaron órdenes en `SUBMITTED` huérfanas.
4. Reconciliar contra CLOB con `python scripts/_api_v2.py reconcile`.

---

## Checklist de exit (antes de declarar R2.1 completo)

- [ ] Los 6 pasos del procedimiento documentados con timestamps en `AUDIT_REPORT.md`.
- [ ] Capturas de validate_criteria con métricas reales adjuntas.
- [ ] Resumen de paper marathon (PnL, latencia, errores) en `data/reports/`.
- [ ] Confirmación humana en `RECORRIDO_ACTUAL.md` con firma y fecha.
- [ ] PIN actualizado y rotado respecto al de pruebas.
- [ ] Plan de canary (R2.2) firmado: capital, duración, criterios de rollback.
