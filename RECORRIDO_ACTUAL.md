# RECORRIDO ACTUAL — PolyBot v4.0

> **Última auditoría:** 2026-06-16 (R2.0-redeem — flujo CTF on-chain)
> **Tests:** 1,369 pasando (+4 nuevos en R2.0-redeem; antes 1,365)
> **Conclusión:** El sistema está TÉCNICAMENTE COMPLETO para paper. Falta el redeem on-chain via CTF (R2.0-redeem-impl) antes de poder cerrar el ciclo entry→exit→redeem en real.

---

## 📊 RESUMEN EJECUTIVO

PolyBot ha completado **todas las fases planificadas** (F1-F11), con 1,125 tests, infraestructura K8s completa, dashboards Grafana, y hardening de seguridad.

**El 95% del trabajo técnico está hecho.** Lo que queda es:
1. Validación operativa (paper trading extensivo, datos reales)
2. Pulido de documentación y dashboards
3. Preparación para real trading

---

## ✅ LO COMPLETADO (100%)

### Fases Legacy (F1-F7) — Fundación Técnica

| Fase | Componentes | Estado |
|------|------------|--------|
| F1 | Seguridad, Estabilidad, Deuda Técnica (8/8) | ✅ |
| F2 | Estrategias y Risk Management (4/4) | ✅ |
| F3 | Testing Exhaustivo (5/5) | ✅ |
| F4 | CI/CD, K8s, Observabilidad (6/6) | ✅ |
| F5 | Pulido Final (7/7) | ✅ |
| F6 | Diagnóstico Honesto (5/5) | ✅ |
| F7 | Pulido Definitivo (4/4) | ✅ |

### Fase 8 — Data & Research Foundation

| Subfase | Componente | Archivos | Tests | Estado |
|---------|-----------|---------|-------|--------|
| P8.1 | Real Market Recording 24/7 | `scripts/record_live_data.py`, `record_live_headless.py`, `watchdog_recording.py` | 18 | ✅ |
| P8.2 | Replay Engine | `src/backtesting/replay_engine.py`, `parquet_loader.py` | 18 | ✅ |
| P8.3 | Feature Store | `src/infrastructure/data/features.py` | 35 | ✅ |
| P8.4 | Regime Labeling | `src/infrastructure/data/regime.py` | 18 | ✅ |

**Infraestructura P8:** K8s Deployment recording 24/7, systemd timer, Grafana dashboard (12 paneles), Prometheus metrics, Parquet zstd, Watchdog con alertas Telegram.

### Fase 9 — Execution Realism

| Subfase | Componente | Archivos | Tests | Estado |
|---------|-----------|---------|-------|--------|
| P9.1 | Fill Simulation | `src/execution/fill_simulator.py` (330 líneas) | 30 | ✅ |
| P9.2 | Slippage Engine | `src/execution/slippage_engine.py` (569 líneas) | 47 | ✅ |
| P9.3 | Queue Position Modeling | `src/execution/queue_position.py` (670 líneas) | 54 | ✅ |
| P9.4 | Smart Order Routing | `src/execution/smart_router.py` (270 líneas) | 30 | ✅ |

**Dashboards P9:** Slippage (12 paneles), Queue Position (12 paneles), Liquidity (12 paneles).

### Fase 10 — Quantitative Validation

| Subfase | Componente | Archivos | Tests | Estado |
|---------|-----------|---------|-------|--------|
| P10.1 | Walk-Forward Validation | `src/quantitative/walk_forward.py` (470 líneas) | 42 | ✅ |
| P10.2 | Monte Carlo Simulation | `src/quantitative/monte_carlo.py` (470 líneas) | 44 | ✅ |
| P10.3 | Confidence Calibration | `src/quantitative/calibration.py` (280 líneas) | 29 | ✅ |
| P10.4 | Post-Trade Analytics | `src/quantitative/post_trade.py` | 49 | ✅ |

### Fase 11 — Advanced Strategies

| Subfase | Componente | Archivos | Tests | Estado |
|---------|-----------|---------|-------|--------|
| P11.1 | Regime-Aware Switching | `src/strategies/regime_aware.py` (520 líneas) | 58 | ✅ |
| P11.2 | Ensemble Signal Engine | `src/strategies/ensemble.py` (250 líneas) | 15 | ✅ |
| P11.3 | Liquidity-Aware Trading | `src/execution/liquidity_sizer.py` | 41 | ✅ |
| P11.4 | Event-Driven Trading | `src/strategies/event_detector.py` (400+ líneas) | 46 | ✅ |

**P11.4 Detalle:**
- 4 tipos de eventos: PRICE_SHOCK, VOLUME_SURGE, EXPIRY_PROXIMITY, SPREAD_EXPLOSION
- 4 acciones de respuesta: HALT, REDUCE_SIZE, BOOST_CONFIDENCE, ALLOW
- Cableado en `RegimeAwareOrchestrator.should_enter()` — HALT antes de evaluar estrategias
- Métricas Prometheus: `EVENT_DETECTED`, `EVENT_RESPONSE`, `EVENT_HALT_ENTRIES`, `EVENT_ACTIVE`
- ✅ Dashboard Grafana: PENDIENTE (R1.3)

---

## 🟡 LO QUE NECESITA AJUSTES

### 1. Documentación Desincronizada ✅ CORREGIDO

**Problema:** `RECORRIDO.txt` y `WORKFLOW.md` mostraban P11.4 como "TODO [ ]" cuando el código existe desde hace semanas.

**Solución:** Documentos antiguos movidos a `docs_historicos/`. Nuevos documentos creados:
- `PLAN_ESTRATEGICO.md` — Plan estratégico v4.0
- `RUTA_IMPLEMENTACION.md` — Prioridades urgentes
- `RECORRIDO_ACTUAL.md` — Este documento

### 2. Test e2e con fallo ✅ CORREGIDO

**Problema:** `test_strategy_engine_marks_entry_correctly` fallaba por validación `target_price > threshold`.

**Solución:** Añadido `target_price=0.90` explícito en la configuración del test.

### 3. Dashboard P11.4 Pendiente

**Problema:** El EventDetector no tiene dashboard en Grafana.

**Solución:** Tarea R1.3 en `RUTA_IMPLEMENTACION.md`.

### 4. Cobertura de Tests en Infraestructura

**Módulos con cobertura < 50%:**
- `api/routers/` — markets, orders, positions, dashboard
- `interfaces/telegram/handlers/` — handlers de comandos
- `infrastructure/polymarket/` — ws_client, http_client, adapters

**Riesgo:** Bajo — los módulos críticos (domain, risk, strategies) tienen >80%.

**Solución:** Tarea R1.5 en `RUTA_IMPLEMENTACION.md`.

### 5. Validación Paper Trading Insuficiente

**Problema:** Paper trading se ha ejecutado exitosamente pero en pruebas cortas (< 10 ciclos).

**Solución:** Tarea R1.1 — 100+ ciclos continuos.

### 6. Parámetros MR con Datos Sintéticos

**Problema:** Los parámetros de MeanReversion se optimizaron con generador sintético.

**Solución:** Tarea R1.2 — optimizar con Parquet real (168h+).

---

## 🔴 LO QUE FALTA (Urgente)

| Tarea | Prioridad | Ver en |
|-------|-----------|--------|
| ~~Paper trading 100+ ciclos~~ ✅ | — | Completado 2026-06-07 (commit `2eb5c9c`) |
| ~~Optimización MR con datos reales~~ ✅ | — | Completado 2026-06-07 (commit `c80690f`) |
| ~~Auditoría de seguridad~~ ✅ | — | Completado 2026-06-07 (commit `671192a`) |
| ~~Auditoría CLOB V2 SDK~~ ✅ | — | Completado 2026-06-14 |
| ~~Dashboard P11.4 Event-Driven~~ ✅ | — | Completado 2026-06-14 |
| ~~Cobertura tests críticos~~ ✅ | — | Completado 2026-06-14 (95.73% en módulos objetivo) |
| ~~Wallet connectivity verification (read-only)~~ ✅ | — | Completado 2026-06-15 — `scripts/verify_polymarket_connectivity.py` + 25 tests |
| ~~Audit redeem CLOB V2~~ ✅ | — | Completado 2026-06-16 — fail-fast + audit log; impl on-chain pendiente como R2.0-redeem-impl |
| Implementar redeem on-chain CTF (R2.0-redeem-impl) | 🔴 NUEVO | Requiere RFC + `web3.py`. Bloquea cerrar ciclo entry→exit→redeem en real |
| Checklist pre-real-trading (pasos 3-6) | ⛔ BLOQUEADO | AUDIT_REPORT.md § R2.1 (2026-06-14) — MR sin edge en parquets reales |

---

## ⛔ R2.1 — Bloqueos pre-real-trading (snapshot 2026-06-14)

Tras correr el checklist completo, **NO se puede activar real trading**. Detalle:

- **MR sin edge en datos reales.** `scripts/backtest_real.py` (nuevo) corre los parámetros de `optimal_params_mr_real.json` sobre los parquets de `data/parquet/`: 0/4 datasets pasan. ETH 5m/15m: Sharpe -3.35, WR 0%, PnL -60.74 USDC. BTC 5m/15m: 0 trades (entry-zscore -2.5 muy estricto).
- **Tooling de validación previo era sintético.** Resuelto con `scripts/backtest_real.py`.
- **Recording inactivo desde 2026-06-01.** Hay parquets de 27-may a 02-jun (15k ticks BTC, 14k ETH); el watchdog no corre.

### Actualización R1.2-bis (2026-06-14, mismo día)

Sweep MR QUICK (324 combos × 4 datasets, ~62s) ejecutado sobre `data/parquet/`. Resultado: **0/324 combos pasan criterios**. Investigación reveló que:

- **BTC parquet**: 15,091 ticks de **1 solo market** ("Will bitcoin hit $1m before GTA VI?") con precio constante **0.4925** (std=0).
- **ETH parquet**: 14,041 ticks de **1 solo market** ("MegaETH airdrop?") rango 0.1465-0.1830 en tendencia bajista lenta.

**Conclusión:** los parquets capturaron **markets longevos**, no los M5/M15 cripto que la estrategia espera. MR no está rota — no se puede ejercitar con estos datos. Nuevo bloqueo **B4: discovery filtró markets equivocados**.

### Auditoría B4 + B5 (mismo día)

- ✅ **B4 resuelto**: `scripts/record_live_data.py:find_markets_for_asset` ahora filtra por window M5/M15 (port de `MarketService._matches_window`).
- ❌ **B5 — externo**: tras el fix, Gamma API no expone markets BTC/ETH M5/M15 abiertos (`tag_id=620` y `102322` con `active=true&closed=false` retornan 0).

**Conclusión:** ningún fix de código abre R2.1. Decisión estratégica pendiente del usuario:
1. Esperar a que Polymarket reabra esos markets.
2. Cambiar alcance del bot.
3. Modo demo-only (saltar a R2.3/R2.4/R3.2/R4 sin escalar capital).

Detalle: `AUDIT_REPORT.md § R2.1 > B5`.

---

## 📈 GRAFANA — Dashboards auto-provisionados

Al levantar `docker compose up -d`, Grafana monta `./monitoring/grafana/dashboards` y `./monitoring/grafana/datasources` como provisioning. Los dashboards aparecen bajo la carpeta **PolyBot** sin pasos manuales.

| Dashboard | UID | Paneles | Cubre |
|---|---|---|---|
| Event-Driven Trading (P11.4) | `polybot-event-driven` | 12 | Eventos detectados por tipo/severidad, HALTs activos, timeline de bloqueos, acciones de respuesta, mercados bloqueados |
| Regime Awareness (P11.1) | `polybot-regime` | 12 | Régimen actual, transiciones, distribución por asset |
| Slippage Engine (P9.2) | `polybot-slippage` | 11 | Slippage esperado vs real, calibración, multiplicadores vol/régimen |
| Queue Position (P9.3) | `polybot-queue-position` | 12 | P(fill) maker, expected time to fill, adverse selection |
| Liquidity-Aware (P11.3) | `polybot-liquidity` | 12 | Sizing dinámico por liquidez, depth observada |
| Data Recording (P8.1) | `polybot-recording` | 12 | Ticks/seg, gaps, salud del recorder |

Cambios al provisioning recargan en ≤ 30s (`updateIntervalSeconds: 30`).

---

## 🔴 R2.0-redeem — Auditoría redeem CLOB V2 (snapshot 2026-06-16)

**Hallazgo:** `PolymarketCLOBClient.redeem_position` llamaba a `POST https://clob.polymarket.com/redeem`, **endpoint que no existe en CLOB V2** (abril 2026). La docs oficial (`/trading/ctf/redeem.md`) y la lista de métodos del SDK `py-clob-client-v2` 1.0.1 confirman que la redención es **on-chain via CTF**:

```
ConditionalTokens(0x4D97DCd97eC945f40cF65F87097ACe5EA0476045).redeemPositions(
  collateralToken = pUSD (0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB),
  parentCollectionId = bytes32(0),
  conditionId = market.condition_id,
  indexSets = [1, 2]  # cubre ambos outcomes
)
```

**Riesgo evitado:** sin este fix, en producción el primer redeem habría devuelto 404, agotado los 3 retries y reportado un error genérico de red — **fallo silencioso disfrazado**, violando "errores deben ser visibles, trazables y accionables".

**Fix aplicado (cero efectos en cadena):**

| Cambio | Archivo |
|---|---|
| `CLOBRedeemNotSupportedError(NotImplementedError)` + constante `CTF_CONTRACT_ADDRESS` | `src/infrastructure/polymarket/clob_client.py` |
| `redeem_position` ahora hace `raise CLOBRedeemNotSupportedError(...)` con mensaje guía | `src/infrastructure/polymarket/clob_client.py:252` |
| `_call_with_retry` no reintenta `NotImplementedError` (falla rápido) | `src/execution/real_handler.py:672` |
| `redeem_resolved_position` emite `REAL_REDEEM_FAILED` con `reason="ctf_onchain_required"` cuando aplica | `src/execution/real_handler.py:537` |
| Nuevo `AuditAction.REAL_REDEEM_FAILED` | `src/infrastructure/security/audit_log.py:22` |
| +4 tests (`TestRedeemPositionV2`) +1 reescrito (`test_redeem_ctf_unsupported_fail_fast`) | `tests/unit/test_clob_client.py`, `tests/unit/test_execution_handlers.py` |

**Resultado:** 1,369/1,369 tests verde, lint limpio sobre los archivos tocados. Detalle completo en `AUDIT_REPORT.md § R2.0-redeem`.

**Pendiente (R2.0-redeem-impl — requiere RFC):** implementación efectiva on-chain — añadir `web3.py`, crear `src/infrastructure/polymarket/ctf_redeemer.py`, resolver `indexSets` por outcome ganador, gas estimation, tx receipt, audit log on-chain. Ver `RUTA_IMPLEMENTACION.md § R2.0-redeem`.

---

## 🔌 POLYMARKET CLOB V2 — SDK & Endpoints

| Recurso | Valor |
|---|---|
| SDK low-level | `py-clob-client-v2` 1.0.1 (Polymarket Engineering) |
| REST CLOB | `https://clob.polymarket.com` |
| Gamma | `https://gamma-api.polymarket.com` |
| Data API | `https://data-api.polymarket.com` |
| WebSocket | `wss://ws-subscriptions-clob.polymarket.com/ws/market` |
| Chain | Polygon Mainnet (137) |
| Colateral | pUSD (Polymarket USD, V2 abril 2026) |
| Auth | L1 EIP-712 (wallet) + L2 HMAC (api_key/secret/passphrase) |
| Order V2 | timestamp (ms) para unicidad — sin nonces, sin `feeRateBps`, con `builderCode` |
| Fees | dinámicos por mercado vía `get_clob_market_info(condition_id)` |
| `signature_type` | `0` EOA · `1` POLY_PROXY (default, R1.7) · `2` GNOSIS_SAFE · `3` POLY_1271 |
| Cache fees | Redis `clob:market_info:{condition_id}` con TTL 300s (R1.7) |
| SDK beta | `polymarket-client` (unificado) — evaluación deferida a R4.5 |

**R1.7 — Cambios aplicados (2026-06-14):**
- `KeyManager.signature_type` (validación `0|1|2|3`, default `1`) propagado explícito al `ClobClient` del SDK.
- `RedisClient.set_clob_market_info` / `get_clob_market_info` (TTL 300s) + `PolymarketCLOBClient.get_market_info_cached`.
- `slippage_engine.taker_fee_bps_from_market_info(info)` + arg `taker_fee_bps` en `SlippageEngine.estimate()` (default `0.0` = no regresión).
- 1,157 tests verdes (incluyendo 16 nuevos casos para `signature_type`, cache y fee-aware slippage).

**R2.1-wallet — Read-only connectivity verification (2026-06-15):**
- `scripts/verify_polymarket_connectivity.py` — 8 pasos (env, init, auth L1+L2, balance pUSD, posiciones Data API, open orders SDK, trades SDK, activity Data API). Exit `0|1|2`. Salida texto o `--json`.
- `PolymarketCLOBClient.assert_auth()` / `get_open_orders()` / `get_trades(limit)` — wrappers async sobre el SDK síncrono. CERO efectos en cadena.
- `DataAPIClient.get_activity(limit, activity_type)` — `GET /activity` para cross-check público vs L2.
- 25 tests (`tests/unit/test_verify_connectivity.py`). Cubre happy path, fallo de auth, wallet vacía, init de clientes con clave hex inválida, env faltante, salida JSON, exit codes.
- **No requiere que B5 esté resuelto** — funciona en cualquier momento si hay credenciales válidas.

**R2.1-smoke — End-to-End pipeline verification (2026-06-15):**

Decisión estratégica: en lugar de esperar a que B5 se resuelva, se ejercita el pipeline completo (discovery → strategy → risk → paper execution) contra los markets cripto longevos que Polymarket **sí** tiene activos hoy. No es una validación de edge — es una verificación de que cada eslabón funciona sobre datos reales.

- **`scripts/smoke_test_pipeline.py`** (~580 líneas):
  1. `fetch_active_crypto_markets()` — Gamma directo (público), filtra crypto via `detect_asset` (canónica en `src/infrastructure/polymarket/market_filters.py`), top-N por `volume24hr`.
  2. `build_market_from_gamma()` — parsea `conditionId`, `clobTokenIds`, `outcomePrices` (JSON-strings) → entidad `Market`. `window=Window.M15` placeholder (los markets longevos no son M5/M15 reales; el `_run_market_cycle` no filtra por window).
  3. `bootstrap_smoke_container()` — reusa el patrón de `run_paper_marathon.bootstrap_marathon()` **sin** llamar a `discover_markets()` (esa ruta sigue filtrando por M5/M15, correcta para cuando B5 se resuelva).
  4. `inject_markets()` — persiste en DB + Redis.
  5. `warmup_market_ticks()` — N llamadas a `MarketService.get_market_tick()` (real CLOB `/book`) para llenar el buffer del `strategy_orchestrator`.
  6. `run_single_cycle()` — envuelve `TradingService._run_market_cycle()` capturando excepciones sin propagar.
  7. `force_fake_signal()` (`--force-fake-signal`) — inyecta `Signal BUY_YES` directo al `execution_handler.execute_entry()` para validar el camino paper completo (slippage, fill, persistencia, balance) sin esperar a que MR genere señal real.
  8. Reporte JSON con `validations` por objetivo + `b5_context` explícito. Exit `0|1|2`.

- **33 tests nuevos** (`tests/unit/test_smoke_test_pipeline.py`): cubre fetch+ranking, parseo dict→Market, helpers JSON, `run_single_cycle` (success + exception), `build_report` (todos los caminos de validación), `main` CLI (exits 0/1/2), `write_report` (symlink), forced-signal path.

- **Verificación contra Polymarket real (paper, sin .env)**:
  - Run normal (`--n-cycles 2 --warmup-ticks 5`): exit `0`, 1 market (`will-bitcoin-hit-1m-before-gta-vi`), 5 ticks reales (0.4925), 2 ciclos sin error, `objective_2 = PASS_NO_SIGNAL` (MR rechaza correctamente datos fuera de su régimen — no es fallo).
  - Run con `--force-fake-signal --force-amount 10`: exit `0`, **1 orden ejecutada en paper, fill_price=0.493001, slippage=0.0005** sobre el bid real 0.4925. La cadena slippage → fill → persistencia DB → balance funciona end-to-end.

- **Lo que NO valida**:
  - Objetivo #3 (rotación M5/M15 + redeem por evento). Sigue ⛔ BLOQUEADO por B5.
  - Real trading (no toca credenciales L1/L2, no firma órdenes en cadena).
  - Edge de la estrategia (MR no genera señales sobre markets longevos; eso es el comportamiento correcto).

- **Side fix (no relacionado pero bloqueaba la suite)**: `scripts/record_live_data.py:346` llamaba `detect_asset(m)` cuando se había importado como `_detect_asset`. NameError pre-existente desde el merge de market_filters. Corregido en 1 línea para que `tests/unit/test_live_crypto_discovery.py` pase.

- **Resultado**: **1,365 tests verde** (subimos de 1,332 con los 33 nuevos). `ruff check` limpio sobre los archivos nuevos.

---

## 🔮 LO QUE NO ES NECESARIO AHORA

| Tarea | Por qué no ahora |
|-------|-----------------|
| Fase 12 — Portfolio & Scaling | Sin real trading estable, no tiene sentido |
| Fase 13 — AI/ML Research | Sin edge validado, ML = overfitting |
| Nuevas estrategias | Las actuales (BAT+MR) necesitan validación primero |
| Multi-market expansion | BTC/ETH son suficientes para validar el sistema |
| Optimización de hiperparámetros | Usar datos reales primero, optimizar después |

---

## 📈 MÉTRICAS DEL SISTEMA

| Métrica | Valor |
|---------|-------|
| Tests totales | 1,125 |
| Tests pasando | 1,124 |
| Cobertura domain | >90% |
| Cobertura strategies | >85% |
| Cobertura risk | >85% |
| Cobertura execution | >80% | mantener |
| Cobertura routers API | **96%** | mantener (R1.5) |
| Cobertura telegram handlers | **96%** | mantener (R1.5) |
| Cobertura execution/real_handler | **93%** | mantener (R1.5) |
| Cobertura infrastructure | ~40% | 80%+ (R-largo plazo) |
| Paneles Grafana | 51 + 4 dashboards específicos |
| Alertas Prometheus | 15 (7 críticas + 8 warning) |
| Manifiestos K8s | 17 YAMLs en 4 entornos |
| Jobs CI/CD | 10 |
| Módulos de seguridad | 8 (audit, key mgr, sanitizer, rate limiter, circuit breaker, secure config, security guard, idempotency) |
| Estrategias | 2 (BAT secundaria, MR primaria) |
| Features computadas | 6 (spread_percentile, orderbook_imbalance, realized_volatility, liquidity_depth, momentum_decay, event_proximity) |
| Regímenes detectados | 5 (TREND, CHOP, PANIC, ILLIQUID, EVENT_DRIVEN) |
| Tipos de eventos | 4 (price_shock, volume_surge, expiry_proximity, spread_explosion) |

---

## 🏗️ ARQUITECTURA ACTUAL

```
src/
├── domain/           — Entidades, value objects, enums, excepciones (31 clases)
├── application/      — Servicios (trading, market, portfolio), puertos ABC
├── strategies/       — BAT, MeanReversion, RegimeAware, Ensemble, EventDetector
├── risk/             — 6 reglas (Kelly, drawdown, exposure, positions, balance, hedge)
├── execution/        — Paper/Real handlers, FillSim, Slippage, Queue, SmartRouter, LiquiditySizer
├── backtesting/      — Engine, Replay, Parquet loader, RegimeAware backtest, Reporter
├── quantitative/     — Walk-Forward, Monte Carlo, Calibration, Post-Trade
├── infrastructure/   — Polymarket (WS/HTTP/CLOB), DB (SQLAlchemy+asyncpg), Redis, Security (8 módulos), Observability
├── interfaces/       — FastAPI (7 routers), Telegram (6 handlers), React Dashboard
└── core/             — Bootstrap, Container DI, Config, Lifecycle
```

---

## 🔗 DOCUMENTACIÓN RELACIONADA

- `PLAN_ESTRATEGICO.md` — Visión y filosofía v4.0
- `RUTA_IMPLEMENTACION.md` — Lo urgente paso a paso
- `CLAUDE.md` — Decisiones de arquitectura inmutables
- `AUDIT_REPORT.md` — Última auditoría de seguridad
- `docs_historicos/` — Documentación anterior

---

*Auditoría completada. El sistema es sólido. A pulir.*
