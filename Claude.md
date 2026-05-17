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
**Tareas del sprint actual:** Ver `tasks/todo.md`.
**Roadmap completo:** Ver `tasks/plan.md`.

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

## Skills activos

Carga los skills relevantes al sprint actual. No cargar todos a la vez —
consume contexto innecesariamente.

### Skills del repositorio addyosmani/agent-skills
Instalar con: `/plugin install agent-skills@addy-agent-skills`

```
# Siempre activos (toda sesión)
@skills/spec-driven-development/SKILL.md
@skills/security-and-hardening/SKILL.md

# Fase A (definición y spec)
@skills/planning-and-task-breakdown/SKILL.md

# Fase B-C (diseño e implementación)
@skills/api-and-interface-design/SKILL.md
@skills/incremental-implementation/SKILL.md
@skills/test-driven-development/SKILL.md

# Debugging y corrección
@skills/debugging-and-error-recovery/SKILL.md

# Fase D (validación y deploy)
@skills/performance-optimization/SKILL.md
@skills/shipping-and-launch/SKILL.md
```

### Skills propios del proyecto (en skills/user/)
```
# Market discovery: filtrado BTC/ETH, contratos MarketTick/MarketCycle
@skills/user/polymarket-market-discovery/SKILL.md

# Protocolo de estrategias: ABC 5 métodos, Buy Above Threshold, separación riesgo
@skills/user/algorithmic-strategy-protocol/SKILL.md

# Switch paper/real: 3 capas confirmación, slippage, idempotencia, audit
@skills/user/paper-vs-real-execution-mode/SKILL.md
```

**Activación por contexto:**
- Trabajando en `infrastructure/polymarket/` o `application/services/market_service.py`
  → activar `polymarket-market-discovery`
- Trabajando en `strategies/` o `application/services/trading_service.py`
  → activar `algorithmic-strategy-protocol`
- Trabajando en `execution/` o `interfaces/telegram/handlers/settings.py`
  → activar `paper-vs-real-execution-mode`

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

## Modo Resume

Cuando el humano indica un checkpoint (ej: "resume desde C13"):

1. Leer este `CLAUDE.md` completo.
2. Leer `SPEC.md` completo.
3. Leer `tasks/todo.md` para conocer el estado actual de las tareas.
4. Preguntar al humano si hay decisiones nuevas tomadas desde la última
   sesión que no están reflejadas en el Decisions Log.
5. Solo entonces continuar desde el checkpoint indicado.
6. Reusar todos los contratos ya definidos — no regenerar lo que existe.

**Nunca reescribir código que ya existe** a menos que el humano lo pida
explícitamente o que sea necesario para corregir un conflicto con el
Decisions Log.

---

## Estado actual del proyecto

> Actualizar esta sección al final de cada sesión de trabajo.

**Última sesión:** 2026-05-17
**Completitud estimada:** 73% del prompt maestro
**Fase actual:** Correcciones de gaps + inicio de D20 (auditoría)

### Completado
- [x] B4 — Estructura de carpetas completa
- [x] B5 — FastAPI backend (routers, schemas, middleware)
- [x] B8 — Strategy Engine modular con ABC
- [x] B9 — Buy Above Threshold con 4 filtros independientes
- [x] B10 — Risk Engine con 5 reglas independientes
- [x] C11 — Paper Trading handler
- [x] C12 — Real Trading handler con confirmación Telegram
- [x] C14 — Telegram Bot (aiogram 3.7) con handlers
- [x] C15 — Seguridad: 6 módulos (audit, key_manager, sanitizer, rate_limiter, secure_config, guard)
- [x] C16 — Logging (structlog) + Métricas (Prometheus) + Dashboard (Grafana)
- [x] C17 — MVP ejecutable (main.py + bootstrap + health endpoint)
- [x] D18 — Backtesting (engine, metrics, reporter, CLI)

### En progreso
- [ ] B6/B7 — Market Discovery + WebSocket (falta `domain/exceptions.py`)
- [ ] C13 — Alembic (falta migraciones 002, 003, 004)

### Pendiente
- [ ] A1/A2/A3 — SPEC.md + CLAUDE.md + tasks/ (este sprint)
- [ ] D19 — Dashboard web React
- [ ] D20 — Auditoría técnica final

### Gaps críticos (resolver primero)
1. **Crear `src/domain/exceptions.py`** con `NoActiveMarketsError` y `MarketFilterError`
2. **Crear migraciones Alembic 002, 003, 004** para `audit_events`, `bot_settings`, campos de retry
3. **Crear `tasks/plan.md` y `tasks/todo.md`** con el roadmap y tareas atómicas del sprint

---

## Verificación rápida de sesión

Al iniciar cualquier sesión, ejecutar mentalmente este checklist:

- [ ] ¿Leí el Decisions Log completo?
- [ ] ¿El código que voy a generar respeta D-01 a D-33?
- [ ] ¿Hay suposiciones que debo aflorar antes de comenzar?
- [ ] ¿Voy a generar tests junto con el código?
- [ ] ¿El módulo que voy a tocar tiene algún contrato en los skills propios?

Si cualquier respuesta es "no" o "no sé", resolver antes de escribir código.