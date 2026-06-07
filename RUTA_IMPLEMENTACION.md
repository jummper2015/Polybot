# RUTA DE IMPLEMENTACIÓN — PolyBot v4.0

> **Fecha:** 2026-06-07  
> **Ciclo:** PLANEAR → CONSTRUIR → TESTEAR → DESPLEGAR

---

## 🔴 BLOQUE R1 — CIMENTACIÓN (URGENTE — Ahora)

Esto es lo que hay que hacer AHORA para tener un sistema sin fisuras. Cada tarea sigue el ciclo PLANEAR → CONSTRUIR → TESTEAR → DESPLEGAR.

---

### R1.1 — Paper Trading Extendido (100+ ciclos)

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

### R1.2 — Validación MR con Datos Reales

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

### R1.3 — Dashboard Event-Driven (P11.4)

**Problema:** El EventDetector está implementado y cableado, pero no tiene dashboard en Grafana para monitorizar eventos en tiempo real.

**PLANEAR:**
- Objetivo: Visualizar eventos de mercado (price_shock, volume_surge, expiry_proximity, spread_explosion) en Grafana
- Criterio: Panel muestra eventos por tipo, severidad, y HALT status
- Dependencias: Métricas Prometheus P11.4 (ya existen en metrics.py)

**CONSTRUIR:**
- [ ] `monitoring/grafana-event-dashboard.json` — paneles:
  - Eventos detectados por tipo (contador)
  - HALTs activos (gauge)
  - Eventos por severidad (histograma)
  - Timeline de eventos recientes

**TESTEAR:**
- [ ] Dashboard carga en Grafana sin errores
- [ ] Métricas aparecen durante paper trading

**DESPLEGAR:**
- [ ] Añadir a docker-compose Grafana provisioning
- [ ] Documentar en RECORRIDO_ACTUAL.md

---

### R1.4 — Auditoría de Seguridad

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

### R1.5 — Cobertura de Tests Críticos

**Problema:** Routers API, handlers Telegram y adaptadores de infraestructura tienen cobertura < 50%.

**PLANEAR:**
- Objetivo: Subir cobertura de módulos críticos al 80%+
- Criterio: APIs y handlers de ejecución con tests completos
- Riesgo: Bajo — añadir tests no rompe nada

**CONSTRUIR:**
- [ ] Tests adicionales para `api/routers/` (markets, orders, positions, dashboard)
- [ ] Tests adicionales para `execution/real_handler.py` (paths con Redis/CLOB)
- [ ] Tests adicionales para `interfaces/telegram/handlers/`

**TESTEAR:**
- [ ] pytest con --cov-report muestra >80% en módulos objetivo
- [ ] Sin regresiones en tests existentes

---

### R1.6 — Documentación Sincronizada

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

## 🟡 BLOQUE R2 — VERIFICACIÓN (ALTA — Julio 2026)

---

### R2.1 — Checklist Pre-Real-Trading (Pasos 3-6)

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
