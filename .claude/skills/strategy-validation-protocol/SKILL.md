---
name: strategy-validation-protocol
description: >
  Protocolo obligatorio para añadir, modificar o reactivar una estrategia
  algorítmica en el bot. Activa para cualquier cambio en src/strategies/
  (mean_reversion, buy_above_threshold, regime_aware, ensemble, event_detector,
  engine, base, filters), para cambios en src/backtesting/ o src/quantitative/
  que toquen evaluación de estrategias, y cuando el usuario pide "añadir
  estrategia X", "optimizar parámetros", "validar edge", "calibrar MR/BAT".
  NO activa para risk (usa risk-engine-guard) ni ejecución (usa
  paper-vs-real-execution).
---

# Skill: Strategy Validation Protocol

## Cadena obligatoria de validación

Ninguna estrategia llega a real sin pasar por esta cadena en orden:

```
walk-forward  →  Monte Carlo  →  out-of-sample  →  paper marathon  →  canary  →  real
```

Saltarse cualquier eslabón = rechazo automático del PR. Cada eslabón tiene un criterio numérico, no opiniones.

---

## Cuándo activa este skill

- Cualquier edición en `src/strategies/*.py`.
- Cambios en `src/backtesting/regime_aware.py` o `src/quantitative/walk_forward.py` / `monte_carlo.py` / `calibration.py` / `post_trade.py`.
- Cambios en `scripts/optimize_mr.py`, `scripts/optimize_bat.py`, `backtest_mean_reversion.py`.
- Edición de `data/optimization/optimal_params_*.json`.
- Petición del usuario: "añadir estrategia", "optimizar", "validar edge".

NO activa para:
- Risk (usa `risk-engine-guard`).
- Ejecución (usa `paper-vs-real-execution`).
- Infra Polymarket (usa `polymarket-clob-audit`).

---

## Filosofía cuantitativa (CLAUDE.md, inamovible)

Cada estrategia es una **hipótesis** y debe responder con texto en el PR:

1. **¿De dónde viene el edge?** (microestructura, sentiment, mean reversion, drift, etc.)
2. **¿Bajo qué regímenes funciona?** (TREND, CHOP, PANIC, ILLIQUID, EVENT_DRIVEN)
3. **¿Qué la invalida?** (qué condición de mercado mata el edge)
4. **¿Qué supuestos de ejecución requiere?** (slippage máximo, latencia, liquidez)

Sin estas 4 respuestas → la estrategia no entra al repo.

---

## Contrato técnico (algorithmic strategy protocol)

Toda estrategia hereda de `BaseStrategy` (`src/strategies/base.py`) e implementa exactamente estos 5 métodos:

```
name (property)
on_cycle_start(cycle)
on_tick(tick)
should_enter(cycle, tick)  →  StrategySignal | None
should_exit(cycle, tick, position)  →  StrategySignal | None
on_exit(cycle, result)
```

Reglas:
- `should_enter` y `should_exit` son **funciones puras**: mismo input → mismo output. No I/O, no logs con side-effects, no acceso a DB/Redis.
- Las estrategias **nunca** deciden sizing ni límites de capital — eso vive en `risk/`.
- `confidence ∈ [0, 1]` calibrado (usar `quantitative/calibration.py`).

---

## Cadena de validación — criterios cuantitativos

### Eslabón 1 — Walk-forward (`src/quantitative/walk_forward.py`)

- ≥ 5 folds.
- Datos: `data/parquet/` reales (**nunca** sintéticos).
- Sharpe out-of-sample > **0.8**.
- Profit factor > **1.2**.
- Max drawdown < **20%**.
- Parámetros estables entre folds (varianza < 30% del valor medio).

### Eslabón 2 — Monte Carlo (`src/quantitative/monte_carlo.py`)

- ≥ 1000 trayectorias.
- P5 del PnL final > 0 (peor caso del 5% inferior es no-pérdida).
- Probabilidad de ruina (drawdown > 50%) < 1%.
- Resultado guardado en `data/reports/monte_carlo_*.json`.

### Eslabón 3 — Out-of-sample (hold-out)

- Hold-out separado del set de optimización (mínimo 30% del histórico).
- Sharpe out-of-sample > 0.5 (mínimo aceptable distinto del walk-forward).
- Si difiere > 40% del walk-forward → señal de overfitting → reoptimizar con regularización.

### Eslabón 4 — Paper marathon

- `python scripts/run_paper_marathon.py --cycles 100`.
- 0 excepciones no manejadas.
- Slippage observado vs simulado ±10%.
- PnL no diverge > 20% del backtest equivalente sobre el mismo periodo.

### Eslabón 5 — Canary (R2.2)

- 72h continuas con capital $5–50 USDC.
- Drawdown diario < 5%.
- Errores < 5/min.
- Rollback automático si cualquier umbral se cruza.

### Eslabón 6 — Real (R3.1)

- Escalado gradual 25% → 50% → 100% (1 semana por escalón).
- Monitoreo continuo via Grafana + alertas Prometheus.

---

## Reglas duras (no negociables, de CLAUDE.md)

- **Cambios en `src/strategies/` requieren walk-forward + paper antes de mergear.**
- **No optimizar en sintético** — sólo `data/parquet/`.
- **Cada cambio de strategy trae su test en el mismo PR.**
- Diffs mínimos en módulos estables.

---

## Estrategias actuales (estado 2026-06-07)

| Estrategia | Rol | Estado validación | Archivo |
|---|---|---|---|
| MeanReversion | **primaria** | Eslabón 1–4 ✅ (R1.1, R1.2) | `src/strategies/mean_reversion/` |
| BuyAboveThreshold | secundaria | Histórica, no priorizada | `src/strategies/buy_above_threshold/` |
| RegimeAware | orquestador | ✅ | `src/strategies/regime_aware.py` |
| Ensemble | meta | ✅ | `src/strategies/ensemble.py` |
| EventDetector | filtro/HALT | ✅ código, ⏳ dashboard R1.3 | `src/strategies/event_detector.py` |

**No añadir nuevas estrategias** hasta que MR + BAT estén validadas en real (anti-meta vigente).

---

## Racionalizaciones a rechazar

- *"Para iterar más rápido, optimizo con datos sintéticos."* → No. Sintético = overfitting confirmado.
- *"El Sharpe en in-sample es 1.5, eso basta."* → No. In-sample sin out-of-sample no prueba nada.
- *"Esta estrategia es obvia, no necesita walk-forward."* → No. Obviedad ≠ edge. Todas pasan la cadena.
- *"Añado un parámetro más para mejorar el fit."* → Riesgo de overfitting. Justificar con regularización + caída de Sharpe out-of-sample tras añadir.
- *"Pongo lógica de sizing dentro de la estrategia."* → No. Sizing vive en `risk/kelly_sizing.py`.
- *"Me salto Monte Carlo, ya hice walk-forward."* → No. Monte Carlo cuantifica cola; walk-forward cuantifica robustez. Son ortogonales.

---

## Red flags

- Estrategia con efectos secundarios en `should_enter`/`should_exit`.
- Estrategia que lee DB/Redis directamente.
- Parámetros optimizados sobre `data/historical/` sin justificar (esos son retros, no validación).
- Cambio de parámetros sin re-correr walk-forward + Monte Carlo.
- PR de estrategia sin test asociado.
- Estrategia nueva añadida cuando aún no se cumple "anti-meta: validar existentes primero".

---

## Checklist de exit (antes de merge)

- [ ] PR contiene las 4 respuestas de la "filosofía cuantitativa".
- [ ] `pytest tests/unit/test_<strategy>.py -v` verde.
- [ ] Walk-forward report en `data/reports/walk_forward_<strategy>.json` adjunto.
- [ ] Monte Carlo report en `data/reports/monte_carlo_<strategy>.json` adjunto.
- [ ] Parámetros guardados en `data/optimization/optimal_params_<strategy>_real.json`.
- [ ] `RECORRIDO_ACTUAL.md` y, si aplica, `RUTA_IMPLEMENTACION.md` actualizados.
- [ ] Si toca regímenes → actualizar `regime_aware.py` mapping y dashboard correspondiente.
