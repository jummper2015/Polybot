# RUTA DE IMPLEMENTACIÓN — PolyBot v4.0

> **Fecha:** 2026-06-07  
> **Ciclo:** PLANEAR → CONSTRUIR → TESTEAR → DESPLEGAR

---

## 🔴 BLOQUE R1 — CIMENTACIÓN (URGENTE — Ahora)

Esto es lo que hay que hacer AHORA para tener un sistema sin fisuras. Cada tarea sigue el ciclo PLANEAR → CONSTRUIR → TESTEAR → DESPLEGAR.

---

### R1.1 — Paper Trading Extendido (100+ ciclos) ✅ COMPLETADO (2026-06-07, commit `2eb5c9c`)

**Problema:** El paper trading se ha ejecutado exitosamente pero solo en pruebas cortas. Necesitamos validación continua para garantizar estabilidad.

**PLANEAR:**
- Objetivo: 100+ ciclos de paper trading sin errores ni crashes
- Criterio: 0 excepciones no manejadas, PnL razonable, shutdown limpio
- Dependencias: P8.1 recording activo (datos reales frescos)

**CONSTRUIR:**
- [ ] Script `scripts/run_paper_marathon.py` — ejecuta N ciclos, guarda métricas
- [ ] Auto-reinicio con backoff si hay crash no fatal
- [ ] Log de métricas por ciclo (latencia, señales, posiciones, PnL)

**TESTEAR:**
- [ ] 100 ciclos completados sin errores
- [ ] Shutdown graceful verificado en cada ciclo
- [ ] Sin memory leaks (monitorizar RSS)

**DESPLEGAR:**
- [ ] Ejecutar en staging K8s 24h+
- [ ] Dashboard de paper trading health

---

### R1.2 — Validación MR con Datos Reales ✅ COMPLETADO (2026-06-07, commit `c80690f`)

**Problema:** Los parámetros de MeanReversion se optimizaron con datos sintéticos. Tenemos 168h+ de datos Parquet reales — hay que usarlos.

**PLANEAR:**
- Objetivo: Sharpe > 0.8, PF > 1.2 con datos reales Parquet
- Criterio: Parámetros estables entre folds (no overfitting)
- Dependencias: P8.1 recording (168h acumulados)

**CONSTRUIR:**
- [ ] `scripts/optimize_mr.py --csv data/parquet/` → optimizar con datos reales
- [ ] Walk-forward validation con datos reales (P10.1)
- [ ] Guardar `optimal_params_mr_real.json`

**TESTEAR:**
- [ ] Sharpe out-of-sample > 0.5 (mínimo aceptable con datos reales)
- [ ] Profit factor > 1.1
- [ ] Max drawdown < 20%

**DESPLEGAR:**
- [ ] Actualizar config de MR con parámetros calibrados
- [ ] Documentar en RECORRIDO_ACTUAL.md

---

### R1.3 — Dashboard Event-Driven (P11.4) ✅ COMPLETADO (2026-06-14)

**Problema:** El EventDetector está implementado y cableado, pero no tiene dashboard en Grafana para monitorizar eventos en tiempo real.

**Hallazgo durante la auditoría:** El JSON del dashboard de eventos ya existía (`monitoring/grafana-event-dashboard.json`, 12 paneles) pero el provisioning de Grafana estaba roto: `docker-compose.yml` montaba `./monitoring/grafana/dashboards` y `./monitoring/grafana/datasources` (rutas que no existían). Los 6 dashboards del repo nunca llegaban a Grafana al levantar el stack.

**PLANEAR:**
- Objetivo: Visualizar eventos de mercado (price_shock, volume_surge, expiry_proximity, spread_explosion) en Grafana + arreglar provisioning roto.
- Criterio: Dashboard carga al levantar `docker compose up -d` sin pasos manuales.
- Dependencias: Métricas Prometheus P11.4 (ya existen en `metrics.py`).

**CONSTRUIR:**
- [x] `monitoring/grafana-event-dashboard.json` ya contiene 12 paneles cubriendo: eventos por tipo, severidad, HALTs activos, timeline, distribución, acciones de respuesta, BTC vs ETH, tabla de mercados bloqueados.
- [x] `monitoring/grafana/dashboards/` creado con provider file-based (`dashboards.yml`).
- [x] `monitoring/grafana/datasources/prometheus.yml` creado para auto-conexión Prometheus.
- [x] Los 6 dashboards (event-driven, regime-aware, slippage, queue-position, liquidity, recording) desempaquetados (top-level JSON, no wrapper `{"dashboard": {...}}`) y copiados al directorio de provisioning.

**TESTEAR:**
- [x] `docker compose config` válido.
- [x] `jq` sobre los 6 JSON: títulos, UIDs y paneles presentes (12+12+11+12+12+12 paneles).
- [x] `yaml.safe_load` sobre los 2 provisioning YAMLs.

**DESPLEGAR:**
- [x] Provisioning auto-cargado al levantar `docker compose up -d` (carpeta `PolyBot` en Grafana).
- [x] Documentado en `RECORRIDO_ACTUAL.md`.

---

### R1.4 — Auditoría de Seguridad ✅ COMPLETADO (2026-06-07, commit `671192a`)

**Problema:** Última auditoría (AUDIT_REPORT.md) fue en Junio 2026. Necesitamos verificar que todos los guards siguen funcionando.

**PLANEAR:**
- Objetivo: Verificar circuit breakers, rate limiters, idempotencia
- Criterio: Todos los guards funcionales, 0 CVEs HIGH/CRITICAL

**CONSTRUIR:**
- [ ] Re-ejecutar `scripts/security_scan.sh`
- [ ] Verificar `.env` no contiene secrets expuestos
- [ ] Verificar `POLYMARKET_BUILDER_CODE` configurado

**TESTEAR:**
- [ ] bandit: 0 HIGH/MEDIUM
- [ ] pip-audit: sin nuevos CVEs críticos
- [ ] Circuit breaker tests pasan

**DESPLEGAR:**
- [ ] Actualizar AUDIT_REPORT.md si hay cambios

---

### R1.5 — Cobertura de Tests Críticos ✅ COMPLETADO (2026-06-14)

**Problema:** Routers API, handlers Telegram y adaptadores de infraestructura tenían cobertura < 50% (telegram al 0%, real_handler 69%, dashboard 73%).

**PLANEAR:**
- Objetivo: Subir cobertura de módulos críticos al 80%+
- Criterio: APIs y handlers de ejecución con tests completos
- Riesgo: Bajo — añadir tests no rompe nada

**CONSTRUIR:**
- [x] Tests adicionales para `api/routers/` (TestDashboardEdgeCases + TestHealthErrorPaths en `tests/integration/test_api_routers.py`)
- [x] Tests adicionales para `execution/real_handler.py` (5 nuevas clases: post-only, token+price, create_position, exit, hedge, redeem)
- [x] Tests nuevos para `interfaces/telegram/handlers/` (`tests/unit/test_telegram_handlers.py`, 63 tests, 5 clases)

**TESTEAR:**
- [x] pytest con --cov-fail-under=80 muestra **95.73%** en módulos objetivo
- [x] Sin regresiones en tests existentes (1,223 pasan)

**Cobertura final:**

| Módulo | Antes | Después |
|---|---|---|
| `routers/dashboard.py` | 73% | **100%** |
| `routers/health.py` | 78% | **92%** |
| `telegram/handlers/alerts.py` | 0% | **100%** |
| `telegram/handlers/positions.py` | 0% | **95%** |
| `telegram/handlers/settings.py` | 0% | **95%** |
| `telegram/handlers/start.py` | 0% | **100%** |
| `telegram/handlers/status.py` | 0% | **90%** |
| `execution/real_handler.py` | 69% | **93%** |
| **TOTAL módulos R1.5** | ~35% | **95.73%** |

---

### R1.6 — Documentación Sincronizada ✅ COMPLETADO (2026-06-07)

**Problema:** RECORRIDO.txt y WORKFLOW.md mostraban P11.4 como "TODO" cuando el código existe y está cableado. Eliminar discrepancias.

**PLANEAR:**
- Objetivo: Una sola fuente de verdad sobre el estado del proyecto
- Criterio: Cada módulo implementado está documentado como completado

**CONSTRUIR:**
- [x] Mover docs antiguos a `docs_historicos/` ✅
- [x] Crear `PLAN_ESTRATEGICO.md` (este documento) ✅
- [x] Crear `RUTA_IMPLEMENTACION.md` (este documento) ✅
- [x] Crear `RECORRIDO_ACTUAL.md` ✅

---

### R1.7 — Auditoría CLOB V2 SDK ✅ COMPLETADO (2026-06-14)

**Problema:** Tras la migración CLOB V2 (abril 2026), Polymarket archivó el antiguo `py-clob-client` (25-may-2026) y publicó `polymarket-client` (beta) como SDK unificado. Nuestro código usa `py-clob-client-v2 1.0.1` (low-level oficial), pero faltaban detalles operativos.

**PLANEAR:**
- Objetivo: cerrar la integración CLOB V2 con todas las recomendaciones oficiales aplicadas
- Criterio: SDK validado, `BUILDER_CODE` y `SIGNATURE_TYPE` documentados, fees dinámicos cacheados
- Riesgo: bajo — sólo documentación + cache, sin cambios de lógica

**CONSTRUIR:**
- [x] Añadir `POLYMARKET_BUILDER_CODE` a `.env.example` con instrucciones
- [x] Añadir `POLYMARKET_SIGNATURE_TYPE` (default 1) a `.env.example`
- [x] Pasar `signature_type` explícito al `ClobClient` en `clob_client.py:93`
- [x] Cachear `get_clob_market_info()` por `condition_id` en Redis con TTL 5 min
- [x] Usar fees cacheados en `slippage_engine.py` para cálculo realista
- [x] Documentar en `RECORRIDO_ACTUAL.md` el SDK exacto + versión + endpoints

**TESTEAR:**
- [x] Test unitario: `signature_type` propagado correctamente al SDK
- [x] Test integración: caché de fees por mercado funciona con TTL

**DESPLEGAR:**
- [ ] Evaluar `polymarket-client` (beta) — tracking R4.5 (no urgente)

---

## 🟡 BLOQUE R2 — VERIFICACIÓN (ALTA — Julio 2026)

---

### R2.0-redeem — Redeem on-chain via CTF 🔴 NUEVO BLOQUEANTE (2026-06-16)

**Problema:** durante la auditoría del flujo de redeem (priorizada por el objetivo #3 del usuario "reclamar ganancias acumuladas por cada evento") se descubrió que `PolymarketCLOBClient.redeem_position` llamaba a un endpoint REST `POST /redeem` que **no existe en CLOB V2**. La redención en V2 se hace on-chain via Conditional Tokens Framework (CTF), llamando al método `redeemPositions(...)` del contrato `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` en Polygon Mainnet.

**Fix aplicado en R2.0-redeem (audit):**
- `clob_client.py`: nueva excepción `CLOBRedeemNotSupportedError(NotImplementedError)`; `redeem_position` falla rápido con mensaje guía hacia CTF.
- `real_handler.py`: `_call_with_retry` no reintenta `NotImplementedError`; `redeem_resolved_position` emite `AuditAction.REAL_REDEEM_FAILED` con `reason="ctf_onchain_required"`.
- `audit_log.py`: nuevo `REAL_REDEEM_FAILED`.
- Tests: +4 (`TestRedeemPositionV2`) +1 reescrito (`test_redeem_ctf_unsupported_fail_fast`).
- Suite total: **1,369/1,369**.

Detalle completo en `AUDIT_REPORT.md § R2.0-redeem`.

**PENDIENTE (R2.0-redeem-impl — requiere RFC, NO incluido en este audit):**

- [ ] RFC: añadir `web3.py` a `requirements.txt` (justificar maintenance + async).
- [ ] `src/infrastructure/polymarket/ctf_redeemer.py` con wrapper sobre `ConditionalTokens.redeemPositions`.
- [ ] Lógica de cálculo de `indexSets` por outcome ganador (observable post-resolution en Data API).
- [ ] Gas estimation + dry-run + tx receipt + retry on chain reorg.
- [ ] Decidir: llamar CTF directo vs. usar "thin collateral adapter" para auto-wrap pUSD.
- [ ] Property tests Hypothesis sobre cálculo de `indexSets`.
- [ ] Audit log de tx hash + gas + pUSD recibido.

**Impacto:** R3.x (real trading) NO puede completar ciclo entry→exit→redeem hasta resolver R2.0-redeem-impl. No es seguro escalar capital más allá de un canary minúsculo sin esto.

---

### R2.1 — Checklist Pre-Real-Trading (Pasos 3-6) ⛔ BLOQUEADO (2026-06-14)

**Estado actual del checklist P7.3:**
- [x] Paso 1: `check_env.py` — Paper OK ✅, Real ⏳ pendiente credenciales
- [ ] Paso 2: Recording 168h → manifest del 2026-06-01 (13 días), watchdog inactivo. Hay parquets utilizables.
- [x] Paso 3: `optimize_mr.py --csv data/parquet/` → optimizado, **pero top config da `avg_sharpe=-1.86`** 🔴
- [x] Paso 4: `validate_criteria.py` → reemplazado por `scripts/backtest_real.py` (nuevo tooling honesto). **0/4 datasets pasan criterios** 🔴
- [ ] Paso 5: Paper marathon 100 ciclos — sin reporte versionado
- [ ] Paso 6: Activar real trading `/mode real <PIN>` — ⛔ **BLOQUEADO**

**Bloqueos (ver AUDIT_REPORT.md § R2.1 para detalle):**

| ID | Bloqueo | Impacto |
|---|---|---|
| B1 | MeanReversion no tiene edge en parquets reales (`Sharpe -3.35`, BTC genera 0 trades) | Crítico — no se puede activar real sin edge |
| B2 | `validate_criteria.py` / `verify_criteria.py` previos eran sintéticos | Resuelto: `scripts/backtest_real.py` ya existe |
| B3 | Recording inactivo desde 2026-06-01 | Datos congelados; bloquea R2.2 canary fresco |

**Tooling añadido en R2.1:**
- `scripts/backtest_real.py` — backtest MR sobre `data/parquet/` con evaluación contra los 4 criterios (Sharpe ≥ 0.8, PF ≥ 1.2, WR ≥ 45%, MaxDD ≤ 20%). JSON en `data/reports/backtest_real_*.json`.

**Acciones de desbloqueo (prerequisitos para reabrir R2.1):**

R1.2-bis y auditoría de discovery ejecutados el 2026-06-14 (ver `AUDIT_REPORT.md § R2.1`). Hallazgos:

- ✅ **B4 — fix aplicado**: `scripts/record_live_data.py` ahora usa el filtro `_matches_window` portado de `MarketService`. Acepta solo markets con slug `-5m-`/`-15m-` o rango horario coherente; rechaza binarios longevos.
- ✅ **B5 — falso positivo confirmado el 2026-06-21**: el bloqueo era el endpoint usado por el discovery, no Polymarket. `scripts/record_live_data.py` consultaba `GET /markets?_limit=500` (20 markets generales, 0 updown). El endpoint correcto (`GET /events/keyset?tag=crypto`, paridad con `PolymarketHTTPClient.get_active_markets`) expone **54 markets `*-updown-*` activos**. Fix + 4 tests no-regresión (`TestFindMarketsForAsset`) en commit subsiguiente.
- ✅ **B3**: con discovery corregido, recording vuelve a tener markets que grabar.
- ✅ **R1.2-ter**: re-optimizar full ya no está bloqueado por B5 — basta relanzar `scripts/record_live_data.py --all` con el script corregido para regenerar parquets.

**Estado:** B5 cerrado. La decisión "esperar / cambiar alcance / demo-only" queda obsoleta — el alcance M5/M15 cripto es alcanzable hoy.

### R1.2-ter — Sweep MR con datos cripto reales (2026-06-21)

**Construido:**
- Recording 30 min cripto M5/M15 real: 1.33M ticks, 27 markets únicos, auto-rotate funcional (commit `bb9cb1c`).
- Sweep MR QUICK 324 combos × 4 datasets × 200K ticks (17 min): TOP-1 robusto `ma=10, ez=-1.5, xz=0.5, sl=15%, tm=45m, ps=5 USDC`.

**Resultado:**
- BTC Sharpe **0.785** (límite del 0.8 protocolo), 2,789 trades, MaxDD 0.7%, PF 57.04, WR 89.4%.
- ETH Sharpe **0.389** (por debajo del umbral), 4,545 trades, MaxDD 0.7%, PF 24.69, WR 87.2%.
- Promedio Sharpe 0.587, robustness 0.818.

**Caveats reconocidos:**
- Single-fold (no walk-forward).
- `ParquetDataLoader` no filtra por window — `BTC_5m == BTC_15m` (dataset etiquetado dos veces). Refactor futuro: añadir filtro real de window.
- Datos cubren 30 min de un día. No representativo de regímenes.

**Próximos pasos en orden:**
1. **Recording extendido** — relanzar `record_live_data.py --all --duration-hours 8+` para acumular varios regímenes / liquidez intradía.
2. **Walk-forward** — `src/quantitative/walk_forward.py` sobre los nuevos parquets, ≥ 5 folds, criterio: parámetros estables (varianza < 30%), Sharpe OOS > 0.8 en mediana.
3. **Monte Carlo** — `src/quantitative/monte_carlo.py` ≥ 1000 trayectorias sobre los trades del walk-forward; P5 PnL > 0, P(ruina) < 1%.
4. **Out-of-sample hold-out** — 30% del histórico fuera del set de optimización; Sharpe > 0.5; diferencia con walk-forward < 40%.
5. Solo entonces → paper marathon + canary.

Nada de esto pasa a real hasta que los 4 puntos anteriores estén verdes **y** R1.5 (cobertura) y R1.7 (auditoría CLOB V2) cerrados.

---

### R2.1-smoke — Sub-task: End-to-End pipeline verification ✅ COMPLETADO (2026-06-15)

**Por qué**: el usuario decide avanzar sin esperar a B5. Necesitamos demostrar que el pipeline completo (discovery → strategy → risk → paper execution) funciona end-to-end contra datos reales de Polymarket, usando los markets cripto longevos que SÍ existen hoy. Esto cubre los objetivos #1 (conectividad lectura) y #2 (compra/venta paper) sin tocar real trading ni esperar a que B5 se resuelva.

**Construido**:
- [x] `scripts/smoke_test_pipeline.py` — bypass del filtro M5/M15 de discovery, inyecta markets manualmente, corre el `_run_market_cycle` del `TradingService` con observabilidad paso a paso. `--force-fake-signal` para forzar ejecución paper sin esperar señal real.
- [x] 33 tests unitarios (`tests/unit/test_smoke_test_pipeline.py`).
- [x] Side fix en `scripts/record_live_data.py:346` (`detect_asset` → `_detect_asset`) que destrababa la suite (`tests/unit/test_live_crypto_discovery.py`).

**Verificado contra Polymarket real (paper, sin .env)**:
- [x] Run normal: exit `0`, `objective_1=PASS`, `objective_2=PASS_NO_SIGNAL`.
- [x] Run con `--force-fake-signal`: exit `0`, **1 orden paper ejecutada, fill_price=0.493001, slippage=0.0005**. Cadena slippage→fill→persistencia funciona.

**Lo que SIGUE bloqueado**:
- ✅ ~~Objetivo #3 (M5/M15 rotación + redeem): bloqueado por B5 externo~~ → **B5 resuelto 2026-06-21**. Objetivo desbloqueado para validación operativa (pendiente: relanzar recording + ciclo entry/exit/redeem real en paper sobre markets `*-updown-*`).
- ⛔ Pasos 5 y 6 del checklist P7.3 (paper marathon con reporte versionado, real trading).

**Próximos pasos posibles** (decisión del usuario, no autorizados por este smoke):
- Relanzar `scripts/record_live_data.py --all` con el discovery corregido para capturar parquets cripto M5/M15 reales.
- Re-correr `run_paper_marathon.py` 100 ciclos sobre los parquets nuevos para tener reporte versionado en `data/reports/`.
- Cargar `.env` real + correr `verify_polymarket_connectivity.py` para cerrar el step 1 del checklist con credenciales reales.

---

### R2.1 — Checklist Pre-Real-Trading (definición original)

**Estado actual del checklist P7.3:**
- [x] Paso 1: `check_env.py` — Paper OK ✅, Real OK ✅
- [ ] Paso 2: Recording 168h → necesita re-lanzar y verificar
- [ ] Paso 3: `optimize_mr.py --csv data/parquet/` → optimizar con reales
- [ ] Paso 4: `validate_criteria.py` → Sharpe > 0.8, PF > 1.2
- [ ] Paso 5: Paper trading 100 ciclos (R1.1)
- [ ] Paso 6: Activar real trading `/mode real <PIN>`

---

### R2.2 — Canary Deploy

- [ ] Deploy en K8s canary con capital $5-50 USDC
- [ ] Monitoreo 72h continuo
- [ ] Rollback automático si drawdown > 5% diario o errores > 5/min

---

### R2.3 — Stress Test

- [ ] Simular WS disconnection durante trading
- [ ] Simular Redis failure → fallback
- [ ] Simular DB pool exhaustion
- [ ] Verificar graceful degradation en todos los escenarios

---

## 🟢 BLOQUE R3 — PRODUCCIÓN (MEDIA — Agosto+ 2026)

- [ ] **R3.1** — Real trading gradual (25% → 50% → 100% capital)
- [ ] **R3.2** — Alertas críticas (PagerDuty/Slack)
- [ ] **R3.3** — Post-mortem automatizado

---

## 🔵 BLOQUE R4 — EXCELENCIA (BAJA — Septiembre+ 2026)

- [ ] **R4.1** — Portfolio Risk Engine (F12.1)
- [ ] **R4.2** — Dynamic Capital Allocation (F12.2)
- [ ] **R4.3** — Nueva estrategia basada en datos reales
- [ ] **R4.4** — Multi-market (nuevos assets en Polymarket)

---

## 📋 RESUMEN DE PRIORIDADES

```
AHORA (Junio):       R1.1 → R1.6  — Cimentación
JULIO:               R2.1 → R2.3  — Verificación pre-real
AGOSTO+:             R3.1 → R3.3  — Producción gradual
SEPTIEMBRE+:         R4.1 → R4.4  — Excelencia y escalado
```

---

---

## 🔬 R2.2 — AUDITORÍA INTEGRAL 2026-07-11 (arranque paper + dashboard + gaps operativos)

> **Contexto:** el usuario pide (a) revisar estado real del bot, (b) validar arranque paper end-to-end, (c) profesionalizar el dashboard, (d) confirmar que puede leer mercados M5/M15 y operar. Se ejecutó auditoría paralela sobre 8 vectores: params/fallos silenciosos, integridad DB, dependencias, wallet Polymarket, compra/venta, ciclo M5/M15 + rollover + redeem, dashboard, arranque paper sin API keys.

### 📊 Snapshot ejecutivo

| Vector | Estado | Bloqueantes | Ver R2.2.x |
|---|---|---|---|
| Arranque paper sin claves reales | ✅ Operativo | 0 | R2.2.7 |
| Conexión Polymarket (lectura pública) | ✅ 200 OK desde codespace | 0 | R2.2.4 |
| Compra/venta paper (fill sim + slippage + persistencia) | ✅ Operativo | 0 | R2.2.5 |
| Compra/venta real (SDK, retry, idempotency, audit) | ⚠️ 95% | PIN 6 dígitos ausente | R2.2.5 |
| Discovery M5/M15 cripto | ✅ Operativo (B5-recheck) | 0 | R2.2.6 |
| Rollover al siguiente evento | ⚠️ Semi-auto | Lag ≤ 60 min si `get_active_markets` falla | R2.2.6 |
| **Detección de resolución (`market_resolved`)** | 🔴 **NO IMPLEMENTADO** | Bloquea ciclo entry→exit→redeem | R2.2.6 |
| Redeem CTF on-chain | ⛔ Bloqueado (R2.0-redeem-impl) | Sin `web3.py` + `ctf_redeemer.py` | R2.2.4 |
| Integridad DB (idempotency) | 🔴 **BUG CRÍTICO** | Mapper no persiste `idempotency_key` | R2.2.2 |
| Dependencias | ⚠️ CVEs HIGH en 21 paquetes | aiohttp, starlette, python-multipart | R2.2.3 |
| Params / fallos silenciosos | ⚠️ 1 HIGH real + smells | `or` fallacy en risk suggestion | R2.2.1 |
| Dashboard | ⚠️ 90% | Sin auth, sin rolling metrics, CORS abierto | R2.2.8 |

**Veredicto:** el bot puede arrancar HOY en paper contra Polymarket real (lectura pública) y ejecutar el ciclo entry→exit sobre markets M5/M15. **NO** cierra ciclo con redeem (bloqueado R2.0-redeem-impl) y **NO** detecta cuándo los eventos se resuelven (gap crítico nuevo). El dashboard es funcional pero requiere hardening antes de exponerse en real.

---

### R2.2.1 — Params y fallos silenciosos ⚠️

**HIGH — confirmado (bug real):**

- `src/application/services/trading_service.py:395-398` — `or` fallacy sobre `suggested_amount`:
  ```python
  amount = risk_decision.suggested_amount or requested_amount
  ```
  Si `RiskEngine` devuelve `suggested_amount=0.0` (rechazo por Kelly / exposure), Python evalúa `0.0 or requested_amount → requested_amount`. La reducción sugerida se **ignora silenciosamente**. Fix: usar `is not None`.

**MEDIUM — smells verificados:**

- `src/execution/real_handler.py:287,435,588` — `float(api_response.get(...))` con `# type: ignore[union-attr]`. El branch anterior (`if error: return`) protege, pero es un contrato implícito. Añadir `assert api_response is not None` explícito.
- `src/execution/real_handler.py:917,921,929` — `ws_state.get("last_yes_price", 0.5)` cae silenciosamente a 50% si el WS nunca emitió tick. Fix: `WARNING` + rechazar orden si buffer vacío.
- `src/execution/real_handler.py:48-49` — `RETRY_BACKOFF=[1.0,2.0,4.0]` sin jitter. Riesgo de thundering herd en recovery post-outage. Fix: añadir jitter `random.uniform(0, 0.5*wait)`.
- Timeouts hardcoded en `clob_client.py:135` (15s), `http_client.py:54` (10s), `ws_client.py:202` (30s) sin override env. Recomendado exponer como env vars con defaults.

**FALSOS POSITIVOS descartados (verificados en código):**

- ~~`await self._risk.evaluate()` sobre función sync~~ → `RiskEngine.evaluate()` **sí es `async`** en `src/risk/engine.py:103`. La firma en `base.py:34` es la ABC.

---

### R2.2.2 — Integridad de base de datos 🔴 BUG CRÍTICO

**Estado migraciones:**
- `001_initial_schema`, `003_bot_settings_mode`, `004_order_retry_fields` (idempotency_key UNIQUE), `005_integrity_constraints` ✅ presente (partial unique en `positions(market_id, mode) WHERE closed_at IS NULL` + `markets(asset, window, expiry)`).
- Simetría upgrade/downgrade correcta.

**🔴 CRITICAL — `idempotency_key` no se persiste a BD:**

- `src/infrastructure/db/repository.py:344-353` — `_order_to_model()` NO incluye `idempotency_key` en la conversión Entity → SQLAlchemy Model.
- `src/infrastructure/db/repository.py:355-364` — `_model_to_order()` tampoco lo recupera al leer.

**Impacto:**
1. `real_handler.py:185` calcula SHA256 correctamente y lo asigna a `order.idempotency_key`.
2. `save_order()` invoca el mapper defectuoso → la columna `orders.idempotency_key` queda NULL.
3. La UNIQUE constraint `ix_orders_idempotency` nunca se activa.
4. **Reintentos post-timeout crean órdenes duplicadas en Polymarket.**

**Fix inmediato (1 línea añadida en cada mapper):**
```python
def _order_to_model(self, o: Order) -> OrderModel:
    return OrderModel(
        ...,
        idempotency_key=o.idempotency_key,   # ← AÑADIR
    )
```

**HIGH — `save_order()` sin catch de `IntegrityError`:**

- `src/infrastructure/db/repository.py:124-144` — a diferencia de `save_market()` (l.74-84) y `save_position()` (l.199-208) que sí catchean, `save_order` propaga la excepción. Tras el fix del mapper, cualquier colisión de `idempotency_key` levantará crash en vez del comportamiento esperado (re-fetch + return).

**Fix:** patrón unificado — catchear `IntegrityError` con `"ix_orders_idempotency"` en el mensaje → `SELECT WHERE idempotency_key=... LIMIT 1` → return existente.

---

### R2.2.3 — Dependencias ⚠️

**Drift pyproject.toml ↔ requirements.txt:** ✅ alineados. 20 runtime + 11 dev.

**Basura filesystem:** `=0.28` y `=6.100.0` marcados como `D` en git status — pip installation artifacts. Ya están en git status para borrar; confirmar con `git clean -fd` tras el `git rm`.

**Pins vs ranges (LOW):** 3 paquetes con `>=` en `[project].dependencies` (`httpx>=0.28.0`, `python-dotenv>=1.2.2`, `uvloop>=0.21.0`). Aceptable para patches de seguridad.

**Verificaciones OK:** `py-clob-client-v2==1.0.1` pinado (SDK oficial V2). No hay `py-clob-client` (v1 archivado).

**🔴 CVEs HIGH — 21 paquetes afectados, 91 CVEs totales:**

| Paquete | Versión actual | Fix mínimo | Nota |
|---|---|---|---|
| `aiohttp` | 3.9.5 | ≥ 3.14.1 | 25 CVEs (varios HIGH) en superficie HTTP crítica |
| `starlette` | 0.37.2 | ≥ 1.1.0 | 8 CVEs (routing/middleware) — impacta FastAPI |
| `python-multipart` | 0.0.28 | ≥ 0.0.31 | CVE-2026-53540/53539/53538 |
| `bleach` | 6.3.0 | ≥ 6.4.0 | GHSA-g75f-g53v-794x |
| `protobuf` | 4.25.9 | ≥ 5.29.6 | PYSEC-2026-1805 (injection) |
| `ujson` | 5.12.1 | ≥ 5.13.0 | CVE-2026-54911 |

**🔴 GAP — `web3.py` ausente:** R2.0-redeem-impl (CTF on-chain) requiere `web3.py>=6.13.0`. Actualmente el import está comentado en `clob_client.py:16`. Sin esto, no se puede cerrar el ciclo redeem.

**Acción:** RFC de dependencias — subir aiohttp/starlette/python-multipart en un PR aislado + regenerar `requirements.txt` desde `pyproject.toml`. Añadir `web3.py==6.13.0` en el RFC de R2.0-redeem-impl.

---

### R2.2.4 — Wallet Polymarket + conexión ✅

**Cubierto (verificado en código):**

| Requisito | Path | Estado |
|---|---|---|
| Saldo pUSD | `clob_client.py:295` (`get_balance()`) | ✅ |
| Posiciones activas | `data_api_client.py:68` (`get_positions()`) | ✅ |
| Historial trades (L2) | `clob_client.py:432` (`get_trades()`) | ✅ |
| Historial actividad (público) | `data_api_client.py:143` (`get_activity()`) | ✅ |
| Órdenes vivas | `clob_client.py:421` (`get_open_orders()`) | ✅ |
| Auth L1+L2 assert | `clob_client.py:399` (`assert_auth()`) | ✅ |
| Handshake script | `scripts/verify_polymarket_connectivity.py` (8 pasos) | ✅ |

**Conectividad verificada 2026-07-11 desde codespace:** `HEAD https://clob.polymarket.com/` → HTTP 200 (Cloudflare); `GET https://gamma-api.polymarket.com/events/keyset?tag=crypto&limit=1` → 200 con payload válido.

**Ubicación / geoblock:**
- Endpoints públicos (Gamma, Data API) **no aplican geoblock** — funcionan desde codespace GitHub (Azure US).
- Real trading (crear órdenes vía CLOB): Polymarket puede aplicar geoblock por IP + T&C exige que la wallet no pertenezca a jurisdicción restringida (US, ciertos países OFAC). El codespace corre en Azure US → **paper OK desde aquí, real trading debe ejecutarse en VPS UE/LATAM/Asia**. Ver `GUIA_DESPLIEGUE_VPS.md`.
- Recomendación: paper y todo el desarrollo continúan en codespace; real trading solo desde el VPS ya documentado.

**⛔ Redeem CTF on-chain** — `clob_client.redeem_position()` (l.266) lanza `CLOBRedeemNotSupportedError` intencionalmente (fail-fast R2.0). No hay `ctf_redeemer.py`. Bloqueante para completar ciclo.

---

### R2.2.5 — Compra/venta paper y real ✅ (con gap R2.1)

**Paper (`paper_handler.py`):** 100% operativo. Fill simulation con slippage depth-based (P9.1+P9.2), balance virtual persistente, orden y posición atómicas en DB, métricas Prometheus. **Gap menor:** `volatility` y `regime` pasados como `None` al `SlippageEngine` (l.118-127) — hay TODO comment; el modelo cae a estimación base.

**Real (`real_handler.py`):** 100% operativo end-to-end (create, cancel, retry, backoff, circuit breaker, audit log inmutable). SDK CLOB V2 usado correctamente (EIP-712 domain V2, timestamp ms, sin nonce, con `builderCode` y `signature_type` propagados). `_call_with_retry` con MAX_RETRIES=3, backoff [1,2,4]s; no reintenta `NotImplementedError` (redeem CTF) ni 4xx lógicos.

**Guard de riesgo:** `TradingService._run_market_cycle` (l.373) invoca `await RiskEngine.evaluate(...)` ANTES de `execute_entry` — no hay ruta alternativa (verificado). Único camino a ejecución pasa por `if risk_decision.allowed`.

**⚠️ Gap R2.1 — PIN 6 dígitos ausente:** `interfaces/telegram/handlers/start.py:93,134,161` implementa doble confirmación con botones inline pero **no exige un PIN numérico** ni rate-limit tras N intentos fallidos, como CLAUDE.md § "Reglas duras #3" exige. Fix: insertar paso de PIN entre `cb_real_confirm_step1` y `cb_real_confirm_final`.

---

### R2.2.6 — Ciclo M5/M15 + rollover + resolución + redeem

**Discovery** ✅ — `market_service.py:91-164` (`discover_markets`) filtro triple: slug `-5m-`/`-15m-` → rango horario → `LIVE_UP_DOWN_CRYPTO_PATTERN`. Redis cache TTL 3900s. Verificado 2026-06-21: 54 markets `*-updown-*` desde `/events/keyset?tag=crypto`.

**Rollover** ⚠️ semi-automático — `_market_cycle_loop` (30s) + `_rediscovery_loop` (3600s). Si `get_active_markets` falla, hay hasta 60 min de no-trading. **Fix sugerido:** reintento agresivo (backoff exp 30s → 5min) cuando la lista viene vacía, en vez de esperar la ventana completa.

**🔴 Detección de resolución NO IMPLEMENTADA (nuevo bloqueante):**

- `ws_client.py:61` define `WS_NON_TICK_EVENTS = {"tick_size_change", "new_market", "market_resolved"}`.
- El `market_resolved` **se ignora** con `continue` en el loop de eventos.
- CERO handlers en `TradingService` procesan resoluciones.
- **Efecto:** el bot no sabe cuándo un market cierra. No dispara redeem. La posición queda "abierta" indefinidamente en el modelo, aunque on-chain ya se resolvió.
- **Fix:** añadir handler WS `on_market_resolved(condition_id)` → marca `Position.resolved_at` en DB → agenda `redeem_resolved_position` (bloqueado por R2.0-impl mientras tanto).

**Redeem CTF** ⛔ — R2.0-redeem-impl pendiente. Sin `web3.py` + `ctf_redeemer.py` + lógica de `indexSets` por outcome ganador, no hay forma de liquidar posiciones ganadoras a pUSD.

**ParquetDataLoader window bug** ⚠️ conocido — `parquet_loader.py:72-143` NO filtra por `window`; solo etiqueta. `BTC_5m == BTC_15m`. Impacto: métricas de backtest sobreestimadas (Sharpe/PF inflados). Documentado en R1.2-ter caveats. Fix futuro no bloqueante.

---

### R2.2.7 — Arranque paper sin claves reales ✅ verificado

**Comando probado (2026-07-11):**
```bash
DATABASE_URL='sqlite+aiosqlite:///:memory:' \
REDIS_URL='redis://localhost:6379/0' \
TELEGRAM_BOT_TOKEN='fake:token' TELEGRAM_CHAT_ID='0' \
TRADING_MODE='paper' \
python -c "from src.core.config import load_config; print(load_config().trading_mode)"
# → paper (OK, sin exigir POLYMARKET_*)
```

**Env vars requeridas para paper:**
- `DATABASE_URL` (SQLite en memoria vale para dev; Postgres en staging/prod).
- `REDIS_URL`.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (validación tolerante — si el token es inválido, el bot avisa y sigue).
- `TRADING_MODE=paper`.

**Env vars NO necesarias en paper:** `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE`, `POLYMARKET_WALLET_ADDRESS`. La validación de `REQUIRED_REAL` en `secure_config.py:89-95` solo se activa si `trading_mode == "real"`.

**DI Container** (`src/core/container.py:243-271`): cuando `trading_mode == "paper"` inyecta `PaperTradingHandler`, no instancia `KeyManager`, no crea `DataAPIClient`. Aislamiento correcto.

**Comando de arranque sugerido para test end-to-end (contra Polymarket real, paper mode):**
```bash
# 1. levantar infra local
docker compose up -d db redis

# 2. env mínima
cat > .env.paper <<'EOF'
DATABASE_URL=postgresql+asyncpg://polybot:changeme@localhost:5432/polybot
REDIS_URL=redis://localhost:6379/0
TELEGRAM_BOT_TOKEN=fake:token   # opcional; sin real, no envía alerts
TELEGRAM_CHAT_ID=0
TRADING_MODE=paper
PAPER_INITIAL_BALANCE=1000.0
EOF

# 3. migraciones
alembic upgrade head

# 4. arranque
env $(cat .env.paper) python main.py

# 5. en otra terminal, smoke test contra live crypto markets:
python scripts/smoke_test_pipeline.py --n-cycles 5 --warmup-ticks 10
```

---

### R2.2.8 — Dashboard: gaps y plan de profesionalización

**Stack real:** React 18.3 + Vite 5 + TypeScript 5; CSS custom (dark theme); Recharts para gráficos. Sin tests. Build servido por FastAPI desde `src/interfaces/api/static/`.

**API existente (7 routers):** `dashboard` (con `/quant-metrics`, `/risk-activity`, `/events` de R1.3), `health` (6 checks paralelos), `markets`, `positions`, `orders`, `metrics` (Prometheus).

**Gaps de seguridad (CRÍTICOS antes de real):**
- **CORS abierto** (`allow_origins=["*"]`) → restringir por dominio.
- **Sin auth** → añadir JWT bearer o token de sesión firmado; el dashboard expone PnL, wallet balance y estado de órdenes.

**Métricas faltantes (priorizadas):**

| Prioridad | Métrica | Fuente | Endpoint sugerido |
|---|---|---|---|
| MUST | PnL rolling (1h/24h/7d/MTD) | `PostTradeAnalyzer` + rolling windows | `/dashboard/pnl-rolling?window=24h` |
| MUST | Sharpe/Sortino intraday | Derivar de ticks 5min | `/dashboard/sharpe-intraday` |
| MUST | Latencia por hop (signal→order→ack→fill) | Traces OpenTelemetry existentes → agregación | `/dashboard/latency-histogram` |
| MUST | Fill quality vs midpoint/VWAP | `SlippageTracker` (P9.2) ya lo calcula | `/dashboard/fill-quality` |
| MUST | Capital deployed vs available | Redis + Repository | `/dashboard/capital-utilization` |
| MUST | Mercado activo actual (asset, window, tiempo restante, order book L2-L5) | `MarketService` + WS state | `/dashboard/active-market` |
| SHOULD | Distribution de position sizes | Repository query | `/dashboard/size-distribution` |
| SHOULD | Historial eventos resueltos + redeem status | Post R2.2.6 fix | `/dashboard/events/resolved` |
| SHOULD | Alertas activas + severidad + ack | Prometheus Alertmanager API | `/dashboard/alerts` |
| SHOULD | Rate limit budget Polymarket | HTTP headers `X-RateLimit-*` | `/dashboard/rate-limit` |
| COULD | Attribution por estrategia | `PostTradeAnalyzer` | `/dashboard/strategy-attribution` |
| COULD | Modo dark/light + mobile responsive | CSS refactor | — |

**UX quick wins:**
- Skeleton loaders para transiciones.
- Reintento exponencial visible en fetches.
- Toast notifications para eventos importantes (nueva señal, HALT activado, redeem completado).
- Panel "modo actual" prominente (paper/canary/real) con color distintivo.

**Tests dashboard:** 0 hoy. Objetivo mínimo: pytest sobre los 3 endpoints R1.3 nuevos (`/quant-metrics`, `/risk-activity`, `/events`) + smoke Vitest sobre 2-3 componentes (Health, Summary).

---

### R2.2.9 — Skills, Harness, CLAUDE.md — evaluación

**Skills (8 actuales)** — cobertura vs. gaps identificados:

| Skill | Cubre gap actual | Necesita cambio |
|---|---|---|
| `polymarket-clob-audit` | Bug WS `market_resolved` sin handler | Extender scope: incluir explícitamente handler de `market_resolved` en el checklist de auditoría |
| `db-integrity-guard` | Bug `_order_to_model` sin `idempotency_key` | ✅ ya cubre. Ejecutar sobre repository.py resolverá esto |
| `dependency-hygiene` | 91 CVEs, `web3.py` faltante | ✅ ya cubre. Ejecutar sobre pyproject.toml resolverá esto |
| `risk-engine-guard` | Bug `or` fallacy suggested_amount | Añadir explicitud sobre uso de `is not None` vs. truthiness |
| `paper-vs-real-execution` | Gap PIN 6 dígitos | ✅ ya cubre. Reforzar checklist paso PIN |
| `ctf-onchain-redeem` | Gap `ctf_redeemer.py` + `web3.py` + indexSets | ✅ ya cubre. Ejecutar cuando se abra R2.0-redeem-impl |
| `strategy-validation-protocol` | Bug ParquetDataLoader window filter | ✅ ya cubre. Registrar como caveat conocido en el skill |
| `pre-real-trading-checklist` | 6 pasos operativos | ✅ ya cubre. Ejecutar antes de R3.1 |

**Skill NUEVA sugerida:** `dashboard-hardening` — auth (JWT), CORS restrictivo, tests API+UI, métricas rolling. Activa al tocar `dashboard/` o `src/interfaces/api/routers/`. Prioridad MEDIA (no bloquea paper, sí bloquea exponer dashboard en real).

**Harness (.claude/settings.json + 6 hooks)** — evaluación:

- `session_start.sh`, `remind_workflow.sh`, `protect_nogo.sh`, `protect_trash.sh`, `check_dep_drift.sh`, `stop_summary.sh` → todos vigentes y útiles.
- **Gap sugerido:** hook `PreToolUse` sobre `Edit|Write` en `src/execution/real_handler.py` y `src/risk/engine.py` que fuerce lectura previa de `AUDIT_REPORT.md § R2.2` — evita regresiones en los mismos vectores auditados hoy.
- **Gap sugerido:** hook `Stop` que recuerde correr `pytest -x -q tests/unit/test_execution_handlers.py tests/unit/test_repository.py` cuando se toca `src/execution/` o `src/infrastructure/db/` — mismo espíritu que `stop_summary.sh` pero accionable.

**CLAUDE.md** — necesita micro-update (no rewrite):

- Añadir a "Reglas duras": `#9 — Nunca usar truthiness (`or`) para valores numéricos donde 0.0 sea válido; usar `is not None`.` (evita R2.2.1 or fallacy).
- Añadir a "No-go zones": `src/infrastructure/db/repository.py mappers` requieren update sincrónico Entity↔Model (evita R2.2.2 bug).
- Añadir en sección "Datos": nota sobre `ParquetDataLoader.load(window=...)` — actualmente es label, no filtro. Métricas M5 y M15 son el mismo dataset hasta que se implemente filtro real.

---

### R2.2.10 — Plan y ruta a implementar (priorizado)

**Ola 1 — Correcciones sin código nuevo (1-2 días, PRs pequeños):**

| # | Tarea | Bloquea | Skill a invocar | Tests requeridos |
|---|---|---|---|---|
| 1.1 | Fix `_order_to_model` + `_model_to_order` en `repository.py` (añadir `idempotency_key`) | Duplicados en real | `db-integrity-guard` | +2 unit (round-trip) |
| 1.2 | Añadir catch `IntegrityError` en `save_order` con re-fetch por key | Crash post-fix 1.1 | `db-integrity-guard` | +1 unit (race simulada) |
| 1.3 | Fix `or` fallacy en `trading_service.py:395-398` — usar `is not None` | Kelly sizing correcto | `risk-engine-guard` | +1 unit (suggested=0.0) |
| 1.4 | Guard explícito `if api_response is None: return failed` en `real_handler.py:287,435,588` | Crash silencioso | `paper-vs-real-execution` | +1 unit por punto |
| 1.5 | Guard `if not ws_state or "last_yes_price" not in ws_state: reject` en `real_handler.py:917+` | Fill a 50% silencioso | `paper-vs-real-execution` | +1 unit |
| 1.6 | `git rm =0.28 =6.100.0` + `git clean -fd` | Basura filesystem | `dependency-hygiene` | — |

**Ola 2 — Gaps funcionales (2-4 días):**

| # | Tarea | Bloquea | Skill | Notas |
|---|---|---|---|---|
| 2.1 | WS handler `on_market_resolved` en `TradingService` + persiste `Position.resolved_at` | Redeem workflow | `polymarket-clob-audit` | Sin CTF impl aún, solo marca detección |
| 2.2 | PIN 6 dígitos en `telegram/handlers/start.py` + rate limit 3 intentos | Real trading según CLAUDE.md | `paper-vs-real-execution` | Añadir tests handler |
| 2.3 | Reintento agresivo en `_market_cycle_loop` si `get_active_markets` vacío (backoff 30s→5min) | Rollover robusto | `polymarket-clob-audit` | Property test tiempo entre reintentos |
| 2.4 | Jitter en `RETRY_BACKOFF` (`random.uniform(0, 0.5*wait)`) | Thundering herd | `paper-vs-real-execution` | Test estadístico |
| 2.5 | Volatility + regime dinámicos en paper fills (`paper_handler.py:118-127`) | Realismo paper | `strategy-validation-protocol` | Comparar slippage estimado vs real en run |

**Ola 3 — Seguridad y dependencias (2-3 días, RFC required):**

| # | Tarea | Skill | Bloquea |
|---|---|---|---|
| 3.1 | RFC dependencias: subir aiohttp≥3.14.1, starlette≥1.1.0, python-multipart≥0.0.31, protobuf≥5.29.6, ujson≥5.13.0, bleach≥6.4.0 | `dependency-hygiene` | Real trading (CVEs) |
| 3.2 | Añadir env vars para timeouts (CLOB, HTTP, WS) con defaults actuales | `polymarket-clob-audit` | Debug / canary tuning |

**Ola 4 — Dashboard hardening (3-5 días):**

| # | Tarea | Prioridad | Skill sugerida |
|---|---|---|---|
| 4.1 | Auth JWT en middleware para `/dashboard/*` y `/positions/*` y `/orders/*` | MUST antes de real | `dashboard-hardening` (nueva) |
| 4.2 | CORS restringido por env `DASHBOARD_ORIGINS` | MUST | `dashboard-hardening` |
| 4.3 | Endpoint `/dashboard/pnl-rolling?window=1h|24h|7d|mtd` | MUST | — |
| 4.4 | Endpoint `/dashboard/sharpe-intraday` derivado de PostTradeAnalyzer con rolling | MUST | — |
| 4.5 | Endpoint `/dashboard/latency-histogram` desde OpenTelemetry spans | MUST | — |
| 4.6 | Endpoint `/dashboard/active-market` (asset, window, tiempo restante, book L2-L5) | MUST | — |
| 4.7 | Endpoint `/dashboard/fill-quality` (SlippageTracker) | MUST | — |
| 4.8 | Endpoint `/dashboard/capital-utilization` | MUST | — |
| 4.9 | Tests pytest sobre los 3 endpoints R1.3 + Vitest sobre 3 componentes | MUST | — |
| 4.10 | UI: panel "modo actual" con color distintivo + skeleton loaders + toast notifications | SHOULD | — |
| 4.11 | Endpoint `/dashboard/events/resolved` (post Ola 2.1) | SHOULD | — |
| 4.12 | Endpoint `/dashboard/alerts` (Alertmanager API proxy) | SHOULD | — |
| 4.13 | Endpoint `/dashboard/rate-limit` (headers Polymarket) | SHOULD | — |
| 4.14 | Refactor CSS a Tailwind + mobile responsive | COULD | — |

**Ola 5 — Bloqueantes de real trading (RFC + implementación pesada):**

| # | Tarea | Skill | Bloquea |
|---|---|---|---|
| 5.1 | R2.0-redeem-impl completo: `web3.py`, `ctf_redeemer.py`, indexSets, gas estimation, tx receipt, retry chain reorg | `ctf-onchain-redeem` | Ciclo entry→exit→redeem en real |
| 5.2 | Fix `ParquetDataLoader.load(window=...)` — filtrar por window real (columna en parquet o pattern en market_id) | `strategy-validation-protocol` | Sharpe/PF backtest inflados |
| 5.3 | Walk-forward 5+ folds sobre parquets cripto reales (recording extendido ≥ 8h) | `strategy-validation-protocol` | Real trading (edge no validado) |
| 5.4 | Monte Carlo 1000+ trayectorias sobre trades del walk-forward | `strategy-validation-protocol` | Real trading |
| 5.5 | Out-of-sample hold-out 30% | `strategy-validation-protocol` | Real trading |

**Ola 6 — Arranque operativo end-to-end paper (validación integrada):**

```bash
# Post-Ola 1: arrancar paper y correr smoke test extendido
env $(cat .env.paper) python main.py &
sleep 30  # bootstrap + discovery
python scripts/smoke_test_pipeline.py --n-cycles 20 --warmup-ticks 20 --force-fake-signal
python scripts/run_paper_marathon.py --cycles 100 --report data/reports/marathon_$(date +%Y%m%d).json
```

Criterios de éxito:
- 100/100 ciclos sin crash.
- ≥ 10 órdenes paper ejecutadas (variable según señales).
- WS reconnect ≤ 3s si se corta (test manual).
- Dashboard `/summary` refleja balance y órdenes en tiempo real.
- Logs sin `WARNING`/`ERROR` no explicados.

---

### R2.2.11 — Documentación colateral

- **RECORRIDO_ACTUAL.md** — se actualiza con snapshot 2026-07-11 (esta auditoría).
- **CLAUDE.md** — micro-update (ver R2.2.9).
- **AUDIT_REPORT.md** — pendiente: consolidar los hallazgos de R2.2.1–R2.2.10 en formato auditoría formal (se hará junto con Ola 1 al abrir PRs).
- **GUIA_DESPLIEGUE_VPS.md** — sigue vigente para real trading; añadir nota sobre geoblock desde codespace (informativa, no bloqueante para paper).

---

## 🚫 LO QUE NO TOCAMOS AHORA

- ❌ Fase 12 (Portfolio & Scaling) — no hasta tener real trading estable
- ❌ Fase 13 (AI/ML) — no hasta tener edge validado con datos reales
- ❌ Nuevas estrategias — no hasta validar las existentes
- ❌ Optimización de parámetros en sintético — solo datos reales
- ❌ Refactors grandes — solo cambios incrementales seguros

---

*Cada tarea completada → actualizar este documento.*
