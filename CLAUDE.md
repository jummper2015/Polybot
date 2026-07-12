# CLAUDE.md — PolyBot v4.0

## Identidad del proyecto

PolyBot es un sistema de trading algorítmico para **Polymarket**, focado en mercados de predicción cripto (BTC/ETH), ventanas M5 / M15, modos `paper` / `canary` / `production`.

- SDK CLOB oficial: `py-clob-client-v2` 1.0.1 (pin exacto, Polymarket Engineering)
- Idempotency key: `SHA256(strategy + market + side + operation + minute_bucket)[:16]` (64-bit hex)
- **Fuente única de dependencias:** `pyproject.toml`. `requirements.txt` es generado. Cualquier discrepancia = bug.
- Endpoint REST CLOB: `https://clob.polymarket.com`
- Endpoint Gamma: `https://gamma-api.polymarket.com`
- Endpoint Data API: `https://data-api.polymarket.com`
- WebSocket: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- Chain: Polygon Mainnet (ID 137)
- Colateral: pUSD (Polymarket USD, V2 abril 2026)

## Prioridades inamovibles

```
robustez > corrección > observabilidad > rentabilidad > optimización
```

## Comandos del proyecto

| Acción | Comando |
|---|---|
| Tests completos | `pytest -x -q` (~1,125 tests, ~3 min) |
| Test único | `pytest tests/unit/test_X.py::TestY::test_z -xvs` |
| Lint | `ruff check src/ tests/` |
| Type check | `mypy src/` |
| Security | `bandit -r src/ -c .bandit && pip-audit` |
| Backtest MR | `python backtest_mean_reversion.py` |
| Validar criterios | `python verify_criteria.py` |
| Paper marathon | `python scripts/run_paper_marathon.py --cycles 100` |
| Optimizar MR | `python scripts/optimize_mr.py --csv data/parquet/` |
| Migración DB | `alembic upgrade head` |
| Dev compose | `docker compose up -d` |

## Arquitectura — paths críticos

```
src/
├── domain/                    entidades, value objects, enums, 31 excepciones
├── application/               servicios + puertos ABC
├── strategies/                MeanReversion (primaria), BAT (secundaria), ensemble, event_detector
├── risk/                      6 reglas (Kelly, drawdown, exposure, positions, balance, hedge)
├── execution/                 paper_handler, real_handler, fill_simulator, slippage_engine, queue_position, smart_router, liquidity_sizer
├── backtesting/               engine, replay, parquet_loader, regime-aware backtest
├── quantitative/              walk_forward, monte_carlo, calibration, post_trade
├── infrastructure/
│   ├── polymarket/            clob_client.py, http_client.py, ws_client.py, adapters.py, data_api_client.py
│   ├── security/              8 módulos (key_manager, audit_log, circuit_breaker, rate_limiter, ...)
│   ├── db/                    SQLAlchemy async + asyncpg, Alembic
│   ├── cache/                 Redis client
│   └── observability/         structlog JSON, Prometheus 30+ metrics, OpenTelemetry
├── interfaces/                FastAPI (7 routers), Telegram (aiogram + PIN), React SPA
└── core/                      bootstrap (uvloop), DI container, config, lifecycle
```

## Reglas duras (no negociables)

1. **Nunca bypassear `RiskEngine.evaluate()`** en el flujo de entrada de órdenes.
2. **Nunca loguear** `private_key`, `api_secret`, `api_passphrase`, ni `builderCode` completo. Usar `_mask_*` helpers.
3. **Real trading requiere 3 capas** de confirmación: `RiskEngine` → Telegram PIN de 6 dígitos → idempotency key.
4. **Cambios en `src/strategies/`** requieren walk-forward + paper antes de mergear.
5. **Cambios en `src/risk/`** requieren property tests con Hypothesis.
6. **Cambios en `src/infrastructure/polymarket/`** invocan skill `polymarket-clob-audit`.
7. **No optimizar Sharpe en datos sintéticos** — solo `data/parquet/` reales.
8. **No introducir dependencias** sin justificación + verificación async + check de mantenimiento.
9. **Nunca usar truthiness (`or`) sobre valores numéricos donde 0.0 sea válido** — usar `is not None` (evita R2.2.1 `suggested_amount or requested_amount` fallacy).
10. **Mappers Entity ↔ Model deben ser exhaustivos y simétricos** en `src/infrastructure/db/repository.py`. Cualquier campo nuevo en `domain.Order` / `domain.Position` / `domain.Market` DEBE aparecer en `_X_to_model` y `_model_to_X` en el mismo PR (evita R2.2.2 `idempotency_key` no persistido).

## No-go zones (no tocar sin RFC)

- `alembic/versions/001_initial_schema.py` — migración inicial aplicada en staging/prod
- `alembic/versions/003_bot_settings_mode.py` — aplicada en staging/prod
- `alembic/versions/004_order_retry_fields.py` — aplicada en staging/prod
- `src/domain/` — solo extensiones aditivas, nunca breaking changes
- `monitoring/alerts.yml` — 15 alertas críticas, romper = PagerDuty
- `k8s/production/*` — capital real, requiere aprobación humana
- `.env` y `.env.example` para secrets reales

**Nuevas migraciones (005+) y cambios en `src/infrastructure/db/`** están permitidos
pero requieren obligatoriamente el skill `db-integrity-guard` (constraints,
Índices parciales, migraciones reversibles, IntegrityError handlers).

## Documentación viva (leer antes de cambios mayores)

- `PLAN_ESTRATEGICO.md` — visión, filosofía, fases R1→R4
- `RUTA_IMPLEMENTACION.md` — checklist de tareas activas con ciclo PLANEAR→CONSTRUIR→TESTEAR→DESPLEGAR
- `RECORRIDO_ACTUAL.md` — estado real del sistema, snapshot 2026-06-07
- `AUDIT_REPORT.md` — última auditoría de seguridad

## Datos

- `data/parquet/` — 168h+ datos reales BTC/ETH (zstd, manifests por asset)
- `data/optimization/optimal_params_mr_real.json` — params MR calibrados con reales (Sharpe ~0.8+)
- `data/historical/` — datasets históricos
- `data/reports/` — informes (incluye `monte_carlo_btc_report.json`)

## Cobertura de tests (objetivos)

| Capa | Actual | Objetivo |
|---|---|---|
| domain | >90% | mantener |
| strategies | >85% | mantener |
| risk | >85% | mantener |
| execution | >80% | mantener |
| infrastructure | ~40% | 80%+ (R1.5) |

## Filosofía cuantitativa

Las estrategias son **hipótesis**, no verdades. Cada una debe responder:
- ¿De dónde viene el edge?
- ¿Bajo qué regímenes funciona?
- ¿Qué la invalida?
- ¿Qué supuestos de ejecución requiere?

Toda mejora debe pasar: walk-forward → Monte Carlo → out-of-sample → paper → canary → real.

## Estilo de trabajo del asistente

- **Diffs mínimos.** No reescribir módulos estables.
- **Antes de implementar**, leer `RUTA_IMPLEMENTACION.md` y verificar que el cambio cabe en una tarea activa.
- **Si ambiguo**: pedir aclaración OR implementar la interpretación más conservadora (HOLD > ENTRY ante duda).
- **Respuestas concisas** al usuario; sin narración interna ni resúmenes innecesarios.
- **Cada cambio de strategy/risk** trae su test en el mismo PR.
- **Errores deben ser visibles, trazables y accionables.** Cero fallos silenciosos.

## Anti-metas (lo que NO hacemos ahora)

- Añadir estrategias nuevas antes de validar las existentes con datos reales
- ML antes de tener edge probado
- Escalar capital sin paper marathon
- Refactors grandes sin RFC
- Optimización de parámetros en sintético

## Skills disponibles para este proyecto

Las ocho skills viven en `.claude/skills/<nombre>/SKILL.md`. Cada una se activa por la `description` de su frontmatter cuando la conversación toca el área correspondiente:

- `polymarket-clob-audit` — auditoría CLOB V2 (pUSD, builderCode, signature_type, fees dinámicos, WS). Obligatorio en cualquier cambio en `src/infrastructure/polymarket/`.
- `paper-vs-real-execution` — dicotomía paper/canary/real, 3 capas de confirmación, idempotencia, switch `/mode`. Obligatorio en `src/execution/` y `interfaces/telegram/handlers/`.
- `pre-real-trading-checklist` — los 6 pasos de R2.1 antes de habilitar real trading.
- `strategy-validation-protocol` — cadena walk-forward → Monte Carlo → OOS → paper → canary → real. Obligatorio en `src/strategies/`.
- `risk-engine-guard` — auditoría de las 6 reglas (Kelly, drawdown, exposure, positions, balance, hedge) + property tests Hypothesis. Obligatorio en `src/risk/`.
- `db-integrity-guard` — integridad de BD (constraints anti-duplicado, índices parciales, migraciones reversibles, IntegrityError handlers). Obligatorio en `alembic/versions/` y `src/infrastructure/db/`.
- `dependency-hygiene` — higiene de dependencias (pyproject.toml como fuente única de verdad, pins exactos, pip-audit sin CVEs HIGH, sin basura `=*`). Obligatorio al tocar `requirements.txt` o `pyproject.toml`.
- `ctf-onchain-redeem` — redeem on-chain de CTF tokens (web3.py, redeemPositions, indexSets, gas estimation, tx receipt). Obligatorio en `src/execution/real_handler.py` redeem + scripts de redeem.

Para invocarlas manualmente, usa la herramienta Skill con el nombre del skill (sin barra). El harness también las recordará automáticamente cuando el prompt toque el área (ver `Harness` abajo).

## Harness (.claude/settings.json + hooks)

El proyecto define un harness reproducible para mantener orden:

- **Permissions** — `allow` para comandos rutinarios (pytest, ruff, mypy, scripts de validación, `git status/diff/log`); `deny` para destructivos (`rm -rf` masivos, `git push --force`, `git reset --hard`, `--no-verify`, lectura/escritura de `.env`, edición de migraciones aplicadas `001/003/004`, `k8s/production/`, `monitoring/alerts.yml`); `ask` para acciones con blast radius (commits, push, PR, migraciones downgrade, kubectl).
- **Hooks**:
  - `SessionStart` → `hooks/session_start.sh`: imprime prioridades y estado de la fase R1 al abrir sesión.
  - `UserPromptSubmit` → `hooks/remind_workflow.sh`: si el prompt menciona estrategia/risk/Polymarket/ejecución/real-trading/DB/dependencias/redeem, recuerda el skill aplicable.
  - `PreToolUse` (Edit|Write|NotebookEdit|Bash) → `hooks/protect_nogo.sh`: bloquea ediciones en no-go zones y comandos destructivos.
  - `PreToolUse` (Edit|Write|Bash) → `hooks/protect_trash.sh`: bloquea commits que incluyan archivos basura `=*`.
  - `PreToolUse` (Edit|Write) → `hooks/check_dep_drift.sh`: detecta drift entre `requirements.txt` y `pyproject.toml`.
  - `Stop` → `hooks/stop_summary.sh`: al cerrar el turno, lista los checks pendientes según los paths modificados.

Los hooks viven en `.claude/hooks/` y son `bash` ejecutables. No requieren dependencias salvo `jq` (degradación amable si falta).
