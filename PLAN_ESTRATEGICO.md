# PLAN_ESTRATÉGICO — PolyBot v4.0

> **Fecha:** 2026-06-07  
> **Estado:** Activo  
> **Principio rector:** Sistema Rolls-Royce primero, estrategias ganadoras después.

---

## 🎯 VISIÓN

PolyBot debe ser un sistema de trading algorítmico **sin fisuras, sin fallos, sin detalles descuidados**. Un sistema donde cada componente está probado, monitorizado, y validado con datos reales. La excelencia operativa precede a la rentabilidad.

Las mejores estrategias llegarán después. Primero, el chasis debe ser impecable.

---

## 🏛️ FILOSOFÍA

```
ROBUSTEZ > CORRECCIÓN > OBSERVABILIDAD > RENTABILIDAD > OPTIMIZACIÓN
```

**Mandamientos:**

1. **Cero fallos silenciosos.** Todo error debe ser visible, trazable y accionable.
2. **Cada subsistema tiene su dashboard.** Si no se puede ver, no existe.
3. **Validación con datos reales.** Nada se despliega sin respaldo empírico.
4. **Paper first, canary second, production last.** Escalamiento gradual obligatorio.
5. **Documentación viva.** Lo que no está documentado, no está terminado.

---

## 📊 DIAGNÓSTICO ACTUAL (Junio 2026)

### ✅ Completado (100%)

| Área | Componentes | Tests | Estado |
|------|------------|-------|--------|
| **Fundación** | Excepciones, DB, Circuit Breaker, Idempotencia, SDK | 139+ | ✅ |
| **Estrategias** | BAT, MeanReversion, Kelly, MTF, Graceful Degradation | 180+ | ✅ |
| **Testing** | Unit, Property, Integration, Performance, Security | 343+ | ✅ |
| **Infraestructura** | CI/CD (10 jobs), K8s (17 YAMLs), Vault, Grafana (51 paneles), OTel | 403+ | ✅ |
| **Pulido** | Telegram, Optimización, CVEs resueltos, API tests | 343 | ✅ |
| **Data Foundation (F8)** | Recording 24/7, Replay Engine, Feature Store, Regime Labeling | 89 | ✅ |
| **Execution Realism (F9)** | FillSim, Slippage, QueuePosition, SmartRouter | 196 | ✅ |
| **Quant Validation (F10)** | Walk-Forward, Monte Carlo, Calibration, Post-Trade | 164 | ✅ |
| **Advanced Strategies (F11)** | Regime-Aware, Ensemble, Liquidity-Aware, Event-Driven | 118 | ✅ |

**Total: 1,125 tests, 973 pasando, 1 fallo (e2e corregido).**

### 🔄 En Progreso / Necesita Verificación

| Área | Qué falta | Prioridad |
|------|----------|-----------|
| **Paper Trading** | Validación 100+ ciclos continuos | 🔴 CRÍTICA |
| **Validación con datos reales** | Optimizar MR con datos Parquet reales | 🔴 CRÍTICA |
| **P11.4 Event-Driven** | Dashboard Grafana de eventos | 🟡 ALTA |
| **Real Trading Readiness** | Builder code validado, checklist P7.3 paso 3-6 | 🔴 CRÍTICA |
| **Cobertura de tests** | Routers API, handlers Telegram, clients <50% | 🟡 MEDIA |

### 🔮 Futuro (NO urgente ahora)

| Área | Descripción |
|------|------------|
| **Fase 12 — Portfolio & Scaling** | Multi-estrategia, capital allocation, multi-market |
| **Fase 13 — AI/ML Research** | Gradient boosting, meta-labeling, online learning |

---

## 🚗 LA RUTA DEL ROLLS-ROYCE

Nuestro foco AHORA no es añadir más features. Es **pulir cada detalle** de lo que ya existe.

### Fase R1 — CIMENTACIÓN (URGENTE — Junio 2026)

**Objetivo:** Garantizar que cada subsistema funciona a la perfección.

- [x] **R1.1 — Paper Trading Extendido:** 100+ ciclos continuos sin errores, PnL estable *(✅ 2026-06-07, commit `2eb5c9c`)*
- [x] **R1.2 — Validación con Datos Reales:** Optimizar MR con Parquet real (168h+), validar Sharpe > 0.8 *(✅ 2026-06-07, commit `c80690f`)*
- [x] **R1.3 — Dashboard Event-Driven:** Panel Grafana para P11.4 (eventos detectados, HALTs, respuestas) + provisioning auto-carga de 6 dashboards *(✅ 2026-06-14)*
- [x] **R1.4 — Auditoría de Seguridad:** Revisar todos los guards, circuit breakers, rate limiters *(✅ 2026-06-07, commit `671192a`)*
- [x] **R1.5 — Cobertura de Tests Críticos:** Subir routers API + execution handlers + Telegram handlers al 80%+ *(✅ 2026-06-14, 95.73% en módulos objetivo)*
- [x] **R1.6 — Documentación Sincronizada:** Eliminar discrepancias entre docs y código *(✅ 2026-06-07)*
- [x] **R1.7 — Auditoría CLOB V2 SDK:** Validar `py-clob-client-v2`, documentar `BUILDER_CODE` y `SIGNATURE_TYPE`, cachear fees dinámicos por mercado *(✅ 2026-06-14)*

### Fase R2 — VERIFICACIÓN (ALTA — Julio 2026)

**Objetivo:** Validar la integridad operativa antes de real trading.

- [ ] **R2.1 — Checklist Pre-Real-Trading:** Completar pasos 3-6 con credenciales
- [ ] **R2.2 — Canary Deploy:** Capital limitado ($5-50 USDC), monitoreo 72h
- [ ] **R2.3 — Stress Test:** Simular fallos de red, API, DB en entorno controlado
- [ ] **R2.4 — Latency Audit:** Verificar que todos los paths críticos son < 100ms

### Fase R3 — PRODUCCIÓN (MEDIA — Agosto 2026)

**Objetivo:** Operación real con capital controlado.

- [ ] **R3.1 — Real Trading Gradual:** 25% → 50% → 100% capital en 1 semana
- [ ] **R3.2 — Alertas Críticas:** PagerDuty/Slack para drawdown, errores, circuit breaker
- [ ] **R3.3 — Post-Mortem Automatizado:** Análisis post-trade tras cada ciclo

### Fase R4 — EXCELENCIA (BAJA — Septiembre+ 2026)

**Objetivo:** Escalar con seguridad.

- [ ] **R4.1 — Portfolio Risk Engine (F12.1)**
- [ ] **R4.2 — Dynamic Capital Allocation (F12.2)**
- [ ] **R4.3 — Nueva estrategia basada en datos reales**

---

## 🎯 CRITERIOS DE ÉXITO

PolyBot es un Rolls-Royce cuando:

1. ✅ **1,125 tests pasan sin fallos** — cobertura completa
2. ✅ **Cada subsistema tiene su dashboard** — visibilidad total
3. ✅ **Paper trading 100+ ciclos** — estabilidad operativa comprobada
4. ✅ **Validación con datos reales** — Sharpe > 0.8, PF > 1.2
5. ✅ **Real trading sin sorpresas** — slippage esperado vs real ±10%
6. ✅ **Alertas accionables** — cada error tiene una respuesta definida
7. ✅ **Rollback instantáneo** — < 60s para volver a estado seguro

---

## ⛔ ANTI-METAS (lo que NO hacemos ahora)

- ❌ Añadir nuevas estrategias sin validar las existentes
- ❌ Implementar ML sin base de datos sólida
- ❌ Escalar capital sin paper trading extensivo
- ❌ Optimizar Sharpe en backtests sintéticos
- ❌ Añadir features sin sus tests + dashboard + docs

---

## 📐 ARQUITECTURA DE REFERENCIA

```
   ┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐
   │  CLOB V2 (auth L2)   │    │   GAMMA + WS market  │    │  DATA API (público)  │
   │  clob.polymarket.com │    │ ws-subscriptions-... │    │ data-api.polymarket  │
   │  pUSD · builderCode  │    │  /events/keyset      │    │  /positions          │
   └──────────┬───────────┘    └──────────┬───────────┘    └──────────┬───────────┘
              │                           │                           │
              │                ┌──────────▼───────────┐                │
              │                │    REAL MARKET DATA   │                │
              │                │   (Parquet 24/7)      │                │
              │                └──────────┬───────────┘                │
              │                           │                           │
              │           ┌────────────────────┼────────────────────┐  │
              │           │                    │                    │  │
              │  ┌────────▼────────┐  ┌───────▼────────┐  ┌───────▼────────┐
              │  │  REGIME DETECT  │  │ EVENT DETECT   │  │  FEATURE STORE │
              │  │  (5 regimes)    │  │ (4 event types)│  │  (6 features)  │
              │  └────────┬────────┘  └───────┬────────┘  └───────┬────────┘
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   │
                        ┌──────────▼───────────┐
                        │  REGIME-AWARE        │
                        │  ORCHESTRATOR        │
                        │  (Ensemble + Events) │
                        └──────────┬───────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
     ┌────────▼────────┐  ┌───────▼────────┐  ┌───────▼────────┐
     │  LIQUIDITY SIZER│  │  RISK ENGINE   │  │  SMART ROUTER  │
     │  (auto-reduce)  │  │  (6 rules)     │  │  (maker/taker) │
     └────────┬────────┘  └───────┬────────┘  └───────┬────────┘
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   │
                        ┌──────────▼───────────┐
                        │  EXECUTION HANDLER   │
                        │  (paper | canary |   │
                        │   production)         │
                        └──────────┬───────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
     ┌────────▼────────┐  ┌───────▼────────┐  ┌───────▼────────┐
     │  POST-TRADE     │  │  METRICS       │  │  GRAFANA       │
     │  ANALYTICS      │  │  (Prometheus)  │  │  (Dashboards)  │
     │  ← Data API     │  │                │  │                │
     │  cross-check    │  │                │  │                │
     └─────────────────┘  └───────────────┘  └───────────────┘
```

**Integración Polymarket CLOB V2 (abril 2026+):**
- SDK: `py-clob-client-v2` 1.0.1 (low-level, oficial Polymarket Engineering)
- Auth: L1 (EIP-712 wallet signature) + L2 (HMAC api_key/secret/passphrase)
- Colateral: pUSD (Polymarket USD, reemplazó USDC.e)
- Order struct V2: timestamp (ms) para unicidad, sin nonces, sin feeRateBps, con builderCode
- Fees: dinámicos por mercado, vía `get_clob_market_info(condition_id)`

---

## 📚 DOCUMENTACIÓN RELACIONADA

- `RUTA_IMPLEMENTACION.md` — Lo urgente y lo diferido, paso a paso
- `RECORRIDO_ACTUAL.md` — Auditoría completa de lo implementado
- `CLAUDE.md` — Decisiones de arquitectura inmutables
- `AUDIT_REPORT.md` — Última auditoría de seguridad (Junio 2026)
- `docs_historicos/` — Documentación anterior (ROADMAP, WORKFLOW, RECORRIDO, SPEC)

---

*PolyBot — Ingeniería cuantitativa, sin atajos.*
