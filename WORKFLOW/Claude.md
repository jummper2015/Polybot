# CLAUDE.md — Bot Algorítmico Polymarket

Este archivo es leído automáticamente por Claude Code al iniciar cualquier
sesión en este repositorio. Contiene el contexto completo del proyecto,
el Decisions Log inamovible, y las instrucciones de comportamiento para
el agente.

**Leer este archivo completo al inicio de cada sesión.** Si la sesión es un
Resume desde una fase específica, leer igualmente desde el inicio y
luego saltar al punto de resume.

---

## Resumen del proyecto

Bot algorítmico que opera mercados de predicción de precio BTC/ETH en
Polymarket usando ventanas de 5 y 15 minutos. Stack Python 3.12 con
FastAPI, aiogram 3.x, SQLAlchemy async, Redis y PostgreSQL. UI de control
exclusivamente vía Telegram.

**Spec completo:** Ver `SPEC.md` en la raíz del proyecto.
**Plan de mejoras:** Ver `PLAN_MEJORAS.txt` (v5.0 — 39 prioridades, todas completadas).
**Recorrido de implementación:** Ver `RECORRIDO.txt` (tracking completo de 7 fases).

---

## Decisions Log

**Estas decisiones están fijadas. Claude Code NO puede cambiarlas sin
aprobación explícita del humano y actualización del SPEC.md.**

Si detectas un conflicto entre el código existente y alguna decisión
de esta lista, detente y pregunta antes de modificar cualquier cosa.

### Decisiones de alcance
| ID | Decisión | Valor | Inamovible hasta |
|---|---|---|---|
| D-01 | Activos operados | BTC y ETH únicamente | Aprobación explícita |
| D-02 | Ventanas temporales | 5m (300s) y 15m (900s) únicamente | Aprobación explícita |
| D-03 | Plataforma | Polymarket CLOB API v2 únicamente | Aprobación explícita |
| D-04 | Modo de inicio | Paper Trading siempre primero | 100 ciclos paper validados |
| D-05 | Estrategia inicial | Buy Above Threshold | Nuevas estrategias se añaden, no reemplazan |

### Decisiones de arquitectura
| ID | Decisión | Valor |
|---|---|---|
| D-10 | Estructura de dominio | `entities/` para entidades, `value_objects/` para VOs, `enums/` para enumerados |
| D-11 | Nombre del Strategy Engine | `StrategyEngine` (no StrategyManager ni StrategyRunner) |
| D-12 | Protocolo de estrategia | ABC con exactamente 5 métodos: `on_cycle_start`, `on_tick`, `should_enter`, `should_exit`, `on_exit` |
| D-13 | Separación estrategia/riesgo | Sizing y límites de capital SIEMPRE en `risk/`, nunca en `strategies/` |
| D-14 | Fuente de verdad de órdenes | PostgreSQL es la fuente de verdad. El CLOB es el destino, no la fuente |
| D-15 | Capas de confirmación real trading | Siempre 3: RiskEngine → Telegram (60s timeout) → Idempotency check |
| D-16 | Switch paper/real | Solo desde comando Telegram `/mode real` con PIN. Nunca automático |
| D-17 | Audit log | Todo submit al CLOB se registra en `audit_events`, éxito o fallo |

### Decisiones de contratos de datos
| ID | Decisión | Valor |
|---|---|---|
| D-20 | MarketInfo | `frozen=True` dataclass en `domain/entities/market.py` |
| D-21 | MarketTick | `frozen=True` dataclass en `domain/value_objects/market_tick.py`, incluye campo `spread` y propiedad `is_liquid_enough` |
| D-22 | StrategySignal | `frozen=True` dataclass en `domain/value_objects/signal.py`, campo `confidence` validado 0.0–1.0 en `__post_init__` |
| D-23 | Filtro de activo | Siempre sobre el campo `question` del mercado. Nunca inferir desde `market_id` (es hash opaco) |
| D-24 | Intervalos de ciclo | Window.M5 → 300 segundos exactos. Window.M15 → 900 segundos exactos |
| D-25 | Slippage en paper | Calculado como inversamente proporcional a la liquidez: `max(0.001, min(0.005, 50.0 / liquidity))` |

### Decisiones de seguridad
| ID | Decisión | Valor |
|---|---|---|
| D-30 | Secrets en logs | `POLYMARKET_PRIVATE_KEY`, `API_SECRET`, `PIN` nunca en logs ni strings de excepción |
| D-31 | Autenticación Telegram | Solo el `TELEGRAM_ADMIN_CHAT_ID` configurado puede operar el bot |
| D-32 | Reconexión WebSocket | Backoff exponencial: espera `2^n` segundos entre intentos, máximo 5 reintentos |
| D-33 | Orden antes de CLOB | `Order` se persiste en DB con `status=PENDING` antes de cualquier llamada al CLOB |

---

## Reglas de comportamiento para Claude Code

### Reglas generales
1. **Una fase a la vez.** Nunca generar código de múltiples sub-fases en
   una sola respuesta.
2. **Spec antes de código.** Cualquier funcionalidad nueva requiere
   actualizar `SPEC.md` primero.
3. **Aflorar suposiciones.** Antes de implementar, listar explícitamente
   las suposiciones que se están tomando.
4. **No redefinir contratos del Decisions Log.** Si detectas un conflicto,
   detente y pregunta.
5. **Tests con el código.** Cada módulo implementado viene con sus tests
   unitarios en la misma entrega.

### Reglas de código
6. **Type hints en toda función pública.** Sin excepciones.
7. **Dataclasses de dominio son `frozen=True`.** `MarketInfo`, `MarketTick`,
   `StrategySignal`, `RiskDecision`, `TradeResult`.
8. **Funciones puras en strategies.** `should_enter()` y `should_exit()`
   no tienen efectos secundarios. No escriben logs, no modifican estado externo.
9. **DB antes de CLOB.** Si una función hace submit al CLOB sin haber
   persistido la `Order` primero, es un bug.
10. **Logging estructurado.** Usar `structlog.get_logger(__name__)` y
    `await log.ainfo(event_name, **campos)`. Nunca f-strings en logging.

### Reglas de seguridad
11. **Nunca mostrar secrets.** Si el código que estás generando incluye
    una variable de entorno que podría ser un secret (`KEY`, `SECRET`,
    `PASSWORD`, `PIN`, `TOKEN`), verificar que `log_sanitizer.py` la filtra.
12. **Audit log es obligatorio en real trading.** Toda llamada a
    `clob_client.submit_order()` debe tener un `await audit_log.record(order)`
    en el bloque `try` Y en el bloque `except`.

---

## Estado actual del proyecto

> Actualizar esta sección al final de cada sesión de trabajo.

**Última sesión:** 2026-05-25
**Completitud estimada:** 100% del prompt maestro (PLAN_MEJORAS v5.0 — 39/39 prioridades)
**Fase actual:** Cierre — Fase 7 completada (P7.1–P7.4)

### Completado
- [x] B4 — Estructura de carpetas completa
- [x] B5 — FastAPI backend (routers, schemas, middleware)
- [x] B6/B7 — Market Discovery + WebSocket
- [x] B8 — Strategy Engine modular con ABC
- [x] B9 — Buy Above Threshold con 4 filtros independientes
- [x] B10 — Risk Engine con 5 reglas independientes
- [x] C11 — Paper Trading handler
- [x] C12 — Real Trading handler con confirmación Telegram
- [x] C13 — Alembic (migraciones 001, 003, 004 completadas)
- [x] C14 — Telegram Bot (aiogram 3.7) con handlers
- [x] C15 — Seguridad: 6 módulos (audit, key_manager, sanitizer, rate_limiter, secure_config, guard)
- [x] C16 — Logging (structlog) + Métricas (Prometheus) + Dashboard (Grafana)
- [x] C17 — MVP ejecutable (main.py + bootstrap + health endpoint)
- [x] D18 — Backtesting (engine, metrics, reporter, CLI)
- [x] D19 — Dashboard web React
- [x] D20 — Auditoría técnica final
- [x] PLAN_MEJORAS Fase 1 (P1.1–P1.8): Seguridad, Estabilidad, Deuda Técnica
- [x] PLAN_MEJORAS Fase 2 (P2.1–P2.4): Estrategias y Risk Management
- [x] PLAN_MEJORAS Fase 3 (P3.1–P3.5): Testing Exhaustivo
- [x] PLAN_MEJORAS Fase 4 (P4.1–P4.6): CI/CD, Despliegue y Monitoreo
- [x] PLAN_MEJORAS Fase 5 (P5.1–P5.7): Datos Reales, Optimización, Telegram, CVEs, API Tests, Documentación
- [x] PLAN_MEJORAS Fase 6 (P6.1–P6.5): Cierre Definitivo con diagnóstico honesto
- [x] PLAN_MEJORAS Fase 7 (P7.1–P7.4): Pulido Final Definitivo

### Métricas del proyecto
- **343 tests totales pasando** (298 unit + 45 API integration)
- 39 prioridades completadas (22 originales + 7 Fase 5 + 5 Fase 6 + 4 Fase 7 + 1 D19/D20)
- 17 archivos Kubernetes YAML (base/staging/canary/production)
- 51 paneles Grafana en 6 secciones
- CI/CD pipeline con 10 jobs en GitHub Actions
- 5 experimentos de Chaos Engineering
- 4 scripts: download_historical_data.py, optimize_bat.py, optimize_mr.py, validate_criteria.py, record_live_data.py
- Estrategias: MeanReversion (PRIMARIA) + BuyAboveThreshold (SECUNDARIA)

### Pendiente
- ⚠️  **Validación con datos reales:** Bloqueado — sin credenciales Polymarket API
  - Checklist P7.3 en PLAN_MEJORAS.txt detalla los 6 pasos exactos a seguir
  - Cuando POLYMARKET_PRIVATE_KEY esté disponible → ejecutar checklist en orden

### Gaps resueltos ✅
1. ✅ Crear `src/domain/exceptions.py` — 31 clases de excepción en jerarquía tipada
2. ✅ Crear migraciones Alembic 003, 004 — bot_settings + order retry/idempotency fields
3. ✅ Telegram Modo REAL + Settings — ContainerMiddleware inyecta container, handlers wired (P5.2)
4. ✅ Optimización BAT — BacktestEngine 5-param sweep, optimizador con modelo two-factor (P5.3)
5. ✅ Validación de criterios — scripts/validate_criteria.py automatizado (P5.4)
6. ✅ CVEs en dependencias — python-dotenv 1.2.2, httpx 0.28.1, orjson 3.11.6 (P5.5)
7. ✅ Tests API routers — 45 tests integration (health, markets, positions, orders, dashboard, metrics) (P5.6)
8. ✅ Optimización MeanReversion — scripts/optimize_mr.py con sweep de 6 parámetros (P7.1)
9. ✅ Documentación consolidada — PLAN_MEJORAS v5.0 + RECORRIDO.txt Fase 7 (P7.2)
10. ✅ Plan pre-producción — checklist de 6 pasos documentado (P7.3)

---

## Verificación rápida de sesión

Al iniciar cualquier sesión, ejecutar mentalmente este checklist:

- [ ] ¿Leí el Decisions Log completo?
- [ ] ¿El código que voy a generar respeta D-01 a D-33?
- [ ] ¿Hay suposiciones que debo aflorar antes de comenzar?
- [ ] ¿Voy a generar tests junto con el código?
- [ ] ¿El módulo que voy a tocar tiene algún contrato en los skills propios?

Si cualquier respuesta es "no" o "no sé", resolver antes de escribir código.
