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
- ❌ **B5 — nuevo, externo**: tras el fix, audit en vivo contra Gamma API muestra **0 events BTC/ETH activos con M5/M15** (`tag_id=620` btc y `tag_id=102322` eth-prices vacíos). Polymarket no está publicando estos markets ahora mismo.
- ❌ **B3**: hasta que B5 se resuelva, recording no tiene markets que grabar.
- ❌ **R1.2-ter**: re-optimizar full requiere parquets nuevos → bloqueado por B5.

**Decisión pendiente del usuario** (ver `AUDIT_REPORT.md § R2.1 > B5`):
1. Esperar a que Polymarket reabra markets M5/M15 cripto.
2. Cambiar alcance del bot (markets daily, eventos políticos).
3. Modo "demo only" — saltar a R2.3/R2.4/R3.2/R4 sin escalar capital.

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
- ⛔ Objetivo #3 (M5/M15 rotación + redeem): bloqueado por B5 externo.
- ⛔ Pasos 5 y 6 del checklist P7.3 (paper marathon con reporte versionado, real trading).

**Próximos pasos posibles** (decisión del usuario, no autorizados por este smoke):
- Re-correr `run_paper_marathon.py` 100 ciclos para tener reporte versionado en `data/reports/`.
- Cargar `.env` real + correr `verify_polymarket_connectivity.py` para cerrar el step 1 del checklist con credenciales reales.
- Esperar a B5 o cambiar alcance (ver decisión pendiente arriba).

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

## 🚫 LO QUE NO TOCAMOS AHORA

- ❌ Fase 12 (Portfolio & Scaling) — no hasta tener real trading estable
- ❌ Fase 13 (AI/ML) — no hasta tener edge validado con datos reales
- ❌ Nuevas estrategias — no hasta validar las existentes
- ❌ Optimización de parámetros en sintético — solo datos reales
- ❌ Refactors grandes — solo cambios incrementales seguros

---

*Cada tarea completada → actualizar este documento.*
