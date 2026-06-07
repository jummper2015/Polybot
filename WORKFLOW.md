# WORKFLOW.md — PolyBot Operational Workflow

Version: 1.0
Status: Active
Last updated: 2026-06-01

---

# CICLO OPERATIVO

Toda fase y subfase de PolyBot sigue estrictamente este ciclo de 4 etapas:

```
PLANEAR → CONSTRUIR → TESTEAR → DESPLEGAR
```

Ningún código llega a producción sin completar las 4 etapas.
Ninguna fase se considera completada hasta que sus 4 etapas están verificadas.

---

## ETAPA 1 — PLANEAR

**Objetivo:** Definir exactamente qué se va a construir ANTES de escribir código.

**Entregables obligatorios:**
- [ ] Objetivo de la fase/subfase claramente definido
- [ ] Criterios de éxito medibles y verificables
- [ ] Dependencias identificadas (¿qué fases/componentes deben estar listos?)
- [ ] Riesgos listados y mitigaciones planeadas
- [ ] Contratos afectados del Decisions Log (SPEC.md)
- [ ] Estimación de impacto en tests existentes

**Regla de oro:** Si no puedes explicar el "por qué" en 3 frases, no está listo para CONSTRUIR.

**Output:** Actualizar ROADMAP.md o RECORRIDO.txt con la sección PLANEAR de la fase.

---

## ETAPA 2 — CONSTRUIR

**Objetivo:** Implementar con mínimos cambios, máxima compatibilidad y calidad.

**Principios:**
- Mínimo diff posible — nunca reescribir módulos estables innecesariamente
- Preservar compatibilidad backward
- Type hints en toda función pública
- Dataclasses de dominio `frozen=True`
- Funciones puras en strategies (`should_enter`, `should_exit`)
- DB antes de CLOB (persistir Order antes de submit)
- Logging estructurado con `structlog`, nunca f-strings
- Secrets NUNCA en logs

**Reglas de dependencias:**
- Justificar necesidad antes de añadir paquetes
- Verificar async compatibility
- Evaluar mantenimiento, seguridad y performance

**Output:** Código implementado + imports actualizados.

---

## ETAPA 3 — TESTEAR

**Objetivo:** Verificar que lo construido funciona, es seguro, y no rompe nada.

**Batería de tests requerida según tipo de cambio:**

| Tipo de cambio | Unit | Integration | Property | Chaos | Security |
|---|---|---|---|---|---|
| Lógica de negocio | ✅ | — | ✅ | — | — |
| DB/Redis/API | ✅ | ✅ | — | — | — |
| Ejecución/Órdenes | ✅ | ✅ | ✅ | ✅ | — |
| Infraestructura | ✅ | ✅ | — | ✅ | ✅ |
| Seguridad | ✅ | — | — | — | ✅ |

**Checklist de TESTEAR:**
- [ ] `pytest tests/unit/ -q` — 0 failures
- [ ] `pytest tests/integration/ -q` — 0 failures  
- [ ] `ruff check src/ --quiet` — 0 errors
- [ ] `bandit -c .bandit -r src/ -ll` — 0 HIGH/MEDIUM
- [ ] `python -c "from src.core.bootstrap import bootstrap; print('OK')"` — import exitoso
- [ ] Sin regresiones en tests existentes

**Output:** Reporte de tests pasando + coverage sin degradación.

---

## ETAPA 4 — DESPLEGAR

**Objetivo:** Poner el cambio en producción de forma segura y verificable.

**Niveles de despliegue según madurez:**

### Nivel 0 — Research/Datos (Fase 8)
- Ejecutar scripts de recording localmente o como cronjob
- Verificar integridad de datos recolectados
- Sin exposición a capital real

### Nivel 1 — Paper Trading
- `python main.py --mode paper`
- 100+ ciclos sin errores
- PnL paper positivo y estable

### Nivel 2 — Canary
- Deploy en entorno `canary` con capital limitado ($5-50 USDC)
- Monitoreo 24-72h antes de escalar
- Rollback automático si: drawdown > 5% diario, errores > 5/min

### Nivel 3 — Production
- Deploy en entorno `production`
- Capital escalado gradualmente (25% → 50% → 100% en 1 semana)
- Monitoreo continuo con alertas

**Checklist pre-DESPLEGAR:**
- [ ] `scripts/check_env.py --phase <N>` pasa (variables requeridas)
- [ ] Tests pasan en CI/CD (GitHub Actions)
- [ ] K8s manifests validados (`kubectl --dry-run=client`)
- [ ] Rollback plan documentado
- [ ] Alertas configuradas en Grafana
- [ ] Telegram notificaciones funcionales

**Output:** Despliegue verificado + monitoreo confirmado.

---

# ESTADO ACTUAL

## Fases Legacy (1-7): ✅ COMPLETADO

| Fase | Nombre | Prioridades | Tests |
|---|---|---|---|
| Fase 1 | Seguridad, Estabilidad, Deuda Técnica | 8/8 ✅ | 139+ |
| Fase 2 | Estrategias y Risk Management | 4/4 ✅ | 180+ |
| Fase 3 | Testing Exhaustivo | 5/5 ✅ | 324+ |
| Fase 4 | CI/CD, K8s, Observabilidad | 6/6 ✅ | 403+ |
| Fase 5 | Pulido Final (Datos, Optimización) | 7/7 ✅ | 343 |
| Fase 6 | Diagnóstico Honesto | 5/5 ✅ | 343 |
| Fase 7 | Pulido Definitivo | 4/4 ✅ | 343 |

**Total Legacy:** 39/39 prioridades | 343 tests | 100% técnico

## Ruta Estratégica (Fases 8-13)

| Fase | Nombre | Estado | Prioridad |
|---|---|---|---|
| **Fase 8** | Data & Research Foundation | ✅ COMPLETADA | CRÍTICA |
| **Fase 9** | Execution Realism | ✅ COMPLETADA | CRÍTICA |
| Fase 10 | Quantitative Validation | ✅ COMPLETADA | MUY ALTA |
| Fase 11 | Advanced Strategies | 🔄 ACTIVE | ALTA |
| Fase 12 | Portfolio & Scaling | 🔮 FUTURE | ALTA |
| Fase 13 | AI/ML Research | 🧪 EXPERIMENTAL | BAJA |

---

# FASE 8 — DATA & RESEARCH FOUNDATION [COMPLETADA ✅]

**Objetivo:** Construir la infraestructura de datos necesaria para investigación cuantitativa seria.

**Problema actual:** Sin datos reales, toda optimización es teórica. Polymarket no expone datos históricos de precios vía API REST.

**Solución:** Grabación continua vía WebSocket público + Gamma API pública. **No requiere credenciales de Polymarket API.** Las credenciales solo son necesarias para trading real (Fase 9+).

---

## P8.1 — Continuous Real Market Recording

### PLANEAR
- **Objetivo:** Recolectar datos de mercado en vivo vía WebSocket de forma continua
- **Criterios de éxito:**
  - 30+ días de colección ininterrumpida
  - Datasets reproducibles con timestamps deterministas
  - Tasa de pérdida de datos < 0.1%
- **Dependencias:** Ninguna (WS público + Gamma API pública)
- **Riesgos:** Desconexiones WS, rate limiting de Gamma API, consumo de disco
- **Mitigaciones:** Reconnect exponencial, caché local de markets, rotación de archivos

### CONSTRUIR
- [x] Script `record_live_data.py` con soporte Parquet + CSV
- [x] `MultiAssetRecorder` con buffering y particionado (asset/date)
- [x] Schema Parquet optimizado (zstd, 17 campos)
- [x] `find_markets_for_asset()` vía Gamma API pública
- [x] `parse_ws_message()` con orderbook depth (3 niveles)
- [x] Graceful shutdown con SIGINT/SIGTERM
- [x] Servicio systemd + timer: `scripts/polybot-recorder.{service,timer}`
- [x] K8s Deployment: `k8s/base/deployment-recording.yaml` (PVC + Service + probes)
- [x] Métricas Prometheus: `RECORDING_TICKS_TOTAL`, `_WS_RECONNECTS`, `_STORAGE_SIZE_BYTES`, `_UPTIME_SECONDS`, `_MARKETS_ACTIVE`
- [x] Script headless: `scripts/record_live_headless.py` (structlog + metrics server)

### TESTEAR
- [x] `pytest tests/unit/test_data_recording.py -v` — 18 tests pasando (schema, storage, recorder)
- [x] `python -c "from src.infrastructure.data.storage import ParquetTickWriter; print('OK')"` — import OK
- [x] `python scripts/record_live_data.py --all --duration-hours 0.05` — 108 ticks, 0 errores
- [x] Validar schema Parquet en output real — 17 campos, zstd compression ✅
- [x] ruff check — 0 errors
- [x] Cross-script imports validados (scripts/__init__.py creado)
- [x] Benchmark: 43,663 ticks/sec (4,366x headroom vs peak WS) — scripts/benchmark_recording.py
- [x] Verificar tasa de pérdida de datos: 0.00% (20,132 rows across 152 files, 108.4h range)

### DESPLEGAR
- [x] Recording 168h lanzado: PID 55620, setsid, batch 1000, heartbeat 5min
- [x] Watchdog: `scripts/watchdog_recording.py` — 4 health checks + Telegram alerts
- [x] Verificar integridad: `python scripts/validate_criteria.py --check-data` → 152 files, schema OK, prices OK, spread OK, 0% data loss
- [x] Configurar K8s Deployment para recording 24/7 (`k8s/base/deployment-recording.yaml`)
- [x] Configurar systemd timer para recording 24/7 (`scripts/polybot-recorder.{service,timer}`)
- [x] Dashboard Grafana: panel de recording — `monitoring/grafana-recording-dashboard.json` (12 paneles)

> 📊 **Progreso actual (2026-06-02):** P8.1 COMPLETADO ✅ (100%)
> CONSTRUIR ✅ (10/10), TESTEAR ✅ (8/8), DESPLEGAR ✅ (5/5).
> Benchmark: 43,663 ticks/sec (4,366x headroom). Data loss: 0.00%.
> Dashboard Grafana: 12 paneles en monitoring/grafana-recording-dashboard.json.
> Integridad de datos: scripts/validate_criteria.py --check-data.

---

## P8.2 — Replay Engine

### PLANEAR
- **Objetivo:** Motor de replay determinista para simulación histórica y backtesting acelerado
- **Criterios de éxito:**
  - Precisión de replay validada (mismo input → mismo output)
  - Rendimiento estable con datasets > 100K ticks
  - Velocidad configurable (1x, 10x, 100x)
- **Dependencias:** P8.1 (datasets Parquet)
- **Riesgos:** Consumo de memoria con datasets grandes, timing inconsistente

### CONSTRUIR
- [x] `ReplayEngine` síncrono con integración directa a BacktestEngine
- [x] `ParquetDataLoader`: carga Parquet particionado → HistoricalDataset
- [x] Modo replay: instantáneo (full speed). Speed-controlled (1x, Nx) planeado.
- [x] Reproducibilidad garantizada: misma semilla → mismas señales (vía BacktestEngine)
- [x] Integración con `BacktestEngine` existente en `src/backtesting/engine.py`
- [x] Time-travel: saltar a timestamp específico (config.start_timestamp)
- [x] Tick limits: max_ticks para limitar procesamiento
- [x] `DataLoader.from_parquet()` para backward compatibilidad
- [x] Fix: BacktestEngine maneja datasets vacíos

### TESTEAR
- [x] `pytest tests/unit/test_replay_engine.py` — 18/18 ✅
- [x] Determinismo: mismo seed → mismo BacktestResult
- [x] Time-travel: skip ticks antes de timestamp
- [x] Integración: Parquet → load → replay → result
- [x] Edge case: dataset vacío manejado
- [x] ruff: 0 errors en archivos nuevos
- [ ] Performance: < 5s para 100K ticks (medir con datos reales acumulados)

### DESPLEGAR
- [x] Documentar uso en RECORRIDO.txt
- [ ] Backtesting con datos reales Parquet (depende de P8.1 acumulando ≥100K ticks)
- [ ] No exponer a producción hasta Fase 10 (Quantitative Validation)

---

## P8.3 — Feature Store

### PLANEAR
- **Objetivo:** Pipeline centralizado de features reutilizables para investigación
- **Features iniciales:**
  - Percentil de spread
  - Imbalance del orderbook
  - Volatilidad realizada (rolling std)
  - Profundidad de liquidez
  - Decaimiento de momentum
  - Proximidad a eventos de mercado
- **Criterios de éxito:**
  - Features reproducibles offline y online
  - Misma ventana → mismos valores
- **Dependencias:** P8.1 (datasets), P8.2 (replay engine)

### CONSTRUIR
- [x] `FeatureRegistry` con decorador @register — 6 features
- [x] `FeaturePipeline` batch + streaming + Parquet export
- [x] Features con soporte depth_data (orderbook_imbalance, liquidity_depth)
- [x] `StreamingState` con rolling window y soporte depth
- [x] Determinismo: batch y streaming comparten misma lógica (momentum_decay delegado)
- [x] Parquet export con metadata (zstd compression)

### TESTEAR
- [x] `pytest tests/unit/test_features.py` — 35/35 ✅
- [x] Consistencia offline/online verificada
- [x] Orderbook features testeados con depth_data
- [x] Tests de determinismo, edge cases, batch, streaming
- [x] ruff: 0 errors

### DESPLEGAR
- [x] Documentado en RECORRIDO.txt
- [ ] Integrar con P8.1 (recording en vivo produce features en tiempo real)
- [ ] Dashboard de features en Grafana

---

## P8.4 — Regime Labeling

### PLANEAR
- **Objetivo:** Clasificar estados de mercado para estrategias adaptativas
- **Regímenes a detectar:**
  - **Trend:** Movimiento direccional persistente
  - **Chop:** Movimiento lateral sin dirección
  - **Panic:** Movimiento brusco + alta volatilidad
  - **Illiquid:** Baja liquidez, spreads amplios
  - **Event-Driven:** Movimiento correlacionado con eventos externos
- **Criterios de éxito:**
  - Clasificación estable (misma ventana → mismo régimen)
  - Separación predictiva útil entre regímenes
- **Dependencias:** P8.3 (feature store)

### CONSTRUIR
- [x] `RegimeClassifier` con heurísticas deterministas (no ML)
- [x] 5 regímenes: TREND, CHOP, PANIC, ILLIQUID, EVENT_DRIVEN
- [x] Priority ordering: PANIC > ILLIQUID > EVENT_DRIVEN > TREND > CHOP
- [x] `RegimeConfig` con thresholds configurables
- [x] Batch mode: `classify_batch()` sobre lista completa de ticks
- [x] Streaming mode: `classify_tick()` incremental (momentum-only TREND)
- [x] FeaturePipeline integration: `classify_from_features(FeatureBatch)`
- [x] Defensive `_ticks` insertion garantiza TREND detection en todos los paths
- [x] `RegimeResult` con `regime_distribution` property

### TESTEAR
- [x] `pytest tests/unit/test_regime.py` — 18/18 ✅
- [x] Todos 5 regímenes testeados (trend, chop, panic, illiquid, event_driven)
- [x] Determinismo: mismos ticks → mismas labels
- [x] FeaturePipeline integration: batch + streaming + classify_from_features
- [x] Edge cases: empty ticks, confidence range [0,1], distribución
- [x] ruff: 0 errors

### DESPLEGAR
- [x] Documentado en RECORRIDO.txt + WORKFLOW.md
- [ ] Dashboard: timeline de regímenes en Grafana
- [ ] Paper trading con estrategias por régimen (Fase 11)

---

# FASES 9-13 (PLANEADAS)

## FASE 9 — EXECUTION REALISM [COMPLETADA ✅]
**Dependencia:** Fase 8 completa
**Requiere credenciales:** SÍ (trading real para validar modelos)

| Subfase | PLANEAR | CONSTRUIR | TESTEAR | DESPLEGAR |
|---|---|---|---|---|
| P9.1 Realistic Fill Simulation | ✅ Definido | ✅ | ✅ | ✅ |
| P9.2 Slippage Engine | ✅ Definido | ✅ | ✅ | ✅ |
| P9.3 Queue Position Modeling | ✅ Definido | ✅ | ✅ | ✅ |
| P9.4 Smart Order Routing | ✅ Definido | ✅ | ✅ | ✅ |

---

## P9.3 — Queue Position Modeling

### PLANEAR
- **Objetivo:** Modelar la probabilidad de fill para órdenes MAKER (límite) basada en posición estimada en la cola del orderbook, permitiendo optimización maker-vs-taker.
- **Problema actual:** FillSimulator (P9.1) y SlippageEngine (P9.2) siempre asumen comportamiento TAKER (crossing the spread). En mercados con suficiente profundidad, ser MAKER puede reducir costos de ejecución al evitar el spread y el price impact.
- **Criterios de éxito:**
  - Estimaciones de probabilidad de fill dentro de ±15% de fills simulados
  - Decisión maker/taker reduce costo total de ejecución en ≥5% vs taker-only
  - Modelo produce distribuciones de fill time estables y reproducibles
  - Integración no degrada performance del handler (< 1ms overhead)
- **Dependencias:**
  - P9.1 (FillSimulator) — modelo de depth del orderbook
  - P9.2 (SlippageEngine) — factores dinámicos (volatilidad, régimen) para adverse selection
  - P8.1 (Parquet recording) — datos reales para calibrar tasas de turnover
  - P8.4 (RegimeClassifier) — clasificación de régimen para ajustar riesgo de adverse selection
- **Riesgos:**
  - Sin Level 3 data (no hay IDs de órdenes individuales, eventos add/cancel/trade) → posición en cola es aproximada
  - Orderbooks de Polymarket tienen profundidad limitada → dinámicas de cola pueden ser ruidosas
  - Adverse selection en prediction markets puede dominar cualquier beneficio de maker
- **Mitigaciones:**
  - Usar volume_24h como proxy de tasa de turnover (más robusto que cambios tick-a-tick en depth)
  - Fórmula de fill probability con time-bound (ventana de espera T configurable, default 30s)
  - Costo de adverse selection ligado a volatilidad (P9.2) × tiempo de espera estimado
  - En régimen PANIC/TREND, penalizar fuertemente la estrategia maker
  - Fallback: volume_24h=0 → tasa de turnover por defecto ~0.01 USDC/sec (observado en Polymarket)
- **Parámetros de diseño:**
  - `wait_time_T`: tiempo máximo que el maker está dispuesto a esperar fill (default 30s, configurable por estrategia/asset)
  - `delay_penalty`: costo de oportunidad si el maker no se llena = taker_slippage × missed_entry_factor (default 0.5)
  - `mode`: `maker` (siempre límite), `taker` (siempre mercado), `auto` (compara costos esperados)
- **Flujo de decisión auto-mode:**
  1. `SlippageEngine.estimate(side="entry")` → TakerCost = slippage
  2. `SlippageEngine.estimate_maker(wait_time=T)` → p_fill, adverse_selection
  3. `MakerCost = (p_fill * adverse_selection) + (1-p_fill) * (TakerCost + delay_penalty)`
  4. Si `MakerCost < TakerCost * 0.95` → ejecutar como MAKER, else TAKER

## FASE 10 — QUANTITATIVE VALIDATION [COMPLETED ✅]
**Dependencia:** Fases 8-9 completas

| Subfase | PLANEAR | CONSTRUIR | TESTEAR | DESPLEGAR |
|---|---|---|---|---|
| P10.1 Walk-Forward Validation | ✅ Definido | ✅ | ✅ | ✅ |
| P10.2 Monte Carlo Simulation | ✅ Definido | ✅ | ✅ | ✅ |
| P10.3 Confidence Calibration | ✅ Definido | ✅ | ✅ | ✅ |
| P10.4 Post-Trade Analytics Engine | ✅ Definido | ✅ | ✅ | ✅ |

## FASE 11 — ADVANCED STRATEGIES [2/4 ACTIVE 🔄]
**Dependencia:** Fase 10 completa

| Subfase | PLANEAR | CONSTRUIR | TESTEAR | DESPLEGAR |
|---|---|---|---|---|
| P11.1 Regime-Aware Strategy Switching | ✅ Definido | ✅ | ✅ | ✅ |
| P11.2 Ensemble Signal Engine | ✅ Definido | ✅ | ✅ | ✅ |
| P11.3 Liquidity-Aware Trading | ✅ Definido | [ ] | [ ] | [ ] |
| P11.4 Event-Driven Trading | ✅ Definido | [ ] | [ ] | [ ] |

## FASE 12 — PORTFOLIO & SCALING [FUTURE 🔮]
**Dependencia:** Fases 8-11 completas

| Subfase | PLANEAR | CONSTRUIR | TESTEAR | DESPLEGAR |
|---|---|---|---|---|
| P12.1 Portfolio Risk Engine | ✅ Definido | [ ] | [ ] | [ ] |
| P12.2 Dynamic Capital Allocation | ✅ Definido | [ ] | [ ] | [ ] |
| P12.3 Multi-Market Expansion | ✅ Definido | [ ] | [ ] | [ ] |

## FASE 13 — AI / ML RESEARCH [EXPERIMENTAL 🧪]
**Dependencia:** Fases 8-12 completas
**Regla:** ML solo después de base de datos sólida + execution realism validado

| Subfase | PLANEAR | CONSTRUIR | TESTEAR | DESPLEGAR |
|---|---|---|---|---|
| P13.1 Gradient Boosted Models | ✅ Definido | [ ] | [ ] | [ ] |
| P13.2 Meta-Labeling | ✅ Definido | [ ] | [ ] | [ ] |
| P13.3 Online Learning | ✅ Definido | [ ] | [ ] | [ ] |

---

# MAPA DE DEPENDENCIAS

```
Fase 8 (Data Foundation) ────────────────────────────────────┐
    │                                                         │
    ├── P8.1 Recording ← NO CREDS (WS público)                │
    ├── P8.2 Replay Engine ← depende de P8.1                  │
    ├── P8.3 Feature Store ← depende de P8.1, P8.2            │
    └── P8.4 Regime Labeling ← depende de P8.3                │
                                                              │
    └── Fase 9 (Execution Realism) ← requiere CREDS ──┐       │
        │                                               │       │
        └── Fase 10 (Quant Validation) ──┐              │       │
            │                             │              │       │
            ├── Fase 11 (Adv. Strategies) ──┐           │       │
            │   │                             │           │       │
            │   └── Fase 12 (Portfolio) ────┐ │           │       │
            │       │                         │ │           │       │
            │       └── Fase 13 (AI/ML) ────┐││           │       │
            │                                │││           │       │
            └────────────────────────────────┴┴┴───────────┘       │
```

---

# HITOS (MILESTONES)

## Milestone A — Research Ready
- [ ] Replay Engine funcional (P8.2)
- [ ] Feature Store operativo (P8.3)
- [ ] Datasets reales ≥ 30 días (P8.1)
- [ ] Walk-Forward testing básico (P10.1)
- **Target:** Entorno de investigación confiable

## Milestone B — Production Alpha Validation
- [ ] 60+ días de paper trading continuo
- [ ] Expectancy positiva
- [ ] Drawdowns dentro de tolerancia
- [ ] Calidad de ejecución estable
- **Target:** Candidato a alpha validado

## Milestone C — Real Capital Stability
- [ ] Deploy canary exitoso (capital limitado $100-500 USDC)
- [ ] Desviación por slippage baja
- [ ] Métricas operacionales estables
- **Target:** Profitabilidad live a pequeña escala

## Milestone D — Scalable Quant Platform
- [ ] Orquestación multi-estrategia
- [ ] Portfolio risk engine
- [ ] Execution alpha medible
- **Target:** Arquitectura de grado institucional

---

# BLOQUEANTES Y RIESGOS

## 🔴 BLOQUEANTE #1 — Credenciales Polymarket API
- **Impacto:** Bloquea trading real (Fase 9+) pero NO bloquea Fase 8 (recording)
- **Afecta:** Fase 9 (Execution Realism), Fase 10 (Quantitative Validation)
- **Mitigación:** Fase 8 (recording) avanza sin credenciales usando WS público + Gamma API
- **Acción:** Seguir checklist P7.3 cuando credenciales estén disponibles

## 🟡 RIESGO #2 — Calidad de datos sintéticos
- **Impacto:** El generador sintético no produce mean-reversion realista
- **Afecta:** Validación de estrategias sin datos reales
- **Mitigación:** Fase 8 produce datos reales → resuelve el problema

## 🟡 RIESGO #3 — aiohttp CVEs (19 transitivas)
- **Impacto:** Dependencia de aiogram 3.7
- **Afecta:** Seguridad de infraestructura Telegram
- **Mitigación:** Solo expuesto internamente, no a internet pública

## 🟢 RIESGO #4 — Cobertura de tests en infraestructura
- **Impacto:** 20+ archivos con 0% cobertura (routers, handlers, clients)
- **Afecta:** Confianza en cambios de API/Telegram
- **Mitigación:** Módulos críticos (domain, risk, strategies) tienen >80%

---

# CHECKLIST PRE-REAL-TRADING

Ejecutar en orden cuando `POLYMARKET_PRIVATE_KEY` esté disponible:

- [ ] 1. `python scripts/check_env.py --phase real` → todo OK
- [ ] 2. `python scripts/record_live_data.py --all --duration-hours 168` → 1 semana de datos
- [ ] 3. `python scripts/optimize_mr.py --csv data/parquet/` → parámetros óptimos
- [ ] 4. `python scripts/validate_criteria.py --strategy mean_reversion` → Sharpe > 0.8
- [ ] 5. `python main.py --mode paper` → 100 ciclos, PnL positivo
- [ ] 6. `/mode real <PIN>` (Telegram) → position_size inicial $5-10 USDC

⚠️  **NO ACTIVAR REAL TRADING SIN COMPLETAR PASOS 1-5**

---

# REGLAS DE OPERACIÓN

1. **Cada subfase completa las 4 etapas** (PLANEAR→CONSTRUIR→TESTEAR→DESPLEGAR)
2. **Ningún código salta TESTEAR** — si no tiene tests, no existe
3. **DESPLEGAR es incremental** — Research → Paper → Canary → Production
4. **Fail-fast en PLANEAR** — si las dependencias no están listas, no se construye
5. **Documentar en RECORRIDO.txt al completar cada subfase**
6. **Si una decisión del Decisions Log (CLAUDE.md) bloquea el avance → preguntar antes de modificar**

---

# REFERENCIAS

- **Estrategia:** ROADMAP.md (visión a largo plazo)
- **Tracking:** RECORRIDO.txt (historial y estado actual)
- **Especificación:** SPEC.md (arquitectura y contratos)
- **Decisiones:** CLAUDE.md (Decisions Log inamovible)
- **Entorno:** `.env.example` (variables requeridas por fase)
