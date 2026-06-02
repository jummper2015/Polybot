# SPEC.md — Bot Algorítmico Polymarket

> **Documento vivo.** Actualizar este archivo ANTES de modificar cualquier
> contrato, arquitectura o decisión de diseño. Si hay conflicto entre este
> spec y el código, el spec tiene precedencia — discutir con el humano antes
> de cambiar cualquiera de los dos.

---

## Objective

Bot algorítmico de trading para Polymarket que opera mercados de predicción
de precio de BTC y ETH en ventanas de 5 y 15 minutos.

**Usuario objetivo:** Operador individual con control total vía Telegram.

**Definición de éxito (métricas verificables):**
- Respuesta al usuario en Telegram < 2 segundos (p95)
- Tasa de conversión paper → orden enviada > 60% de señales generadas
- PnL acumulado en paper trading positivo tras 100 ciclos antes de activar real
- Drawdown máximo en real trading < 20% del capital configurado
- Uptime del bot > 99% (medido por Prometheus `bot_uptime_seconds`)
- Zero secrets en el repositorio (verificado por `git-secrets` en cada commit)

---

## Scope — Inamovible sin aprobación explícita

| Parámetro       | Valor fijado        | Razón para cambiar |
|---|---|---|
| Activos         | BTC y ETH únicamente | Requiere actualizar filtros de discovery |
| Ventanas        | 5m (300s) y 15m (900s) únicamente | Requiere actualizar MarketTimer |
| Plataforma      | Polymarket CLOB API v2 únicamente | — |
| Modo inicio     | Paper Trading | Real requiere 100 ciclos paper primero |
| Estrategia v1   | Buy Above Threshold | Nuevas estrategias siguen el ABC base |

---

## Tech Stack

| Capa | Tecnología | Versión |
|---|---|---|
| Lenguaje | Python | 3.12 |
| Framework API | FastAPI | 0.111.0 |
| ASGI server | Uvicorn + uvloop | 0.29.0 |
| Telegram UI | aiogram | 3.7.0 |
| ORM | SQLAlchemy async | 2.0.30 |
| Driver BD | asyncpg | 0.29.0 |
| Migraciones | Alembic | 1.13.1 |
| Caché/estado | Redis con hiredis | 5.0.4 |
| HTTP client | httpx | 0.27.0 |
| WebSocket | websockets | 12.0 |
| Logging | structlog | 24.1.0 |
| Métricas | prometheus-client | 0.20.0 |
| Retries | tenacity | 8.3.0 |
| Env | python-dotenv | 1.0.1 |

---

## Commands

```bash
# Desarrollo
python main.py                          # arranca el sistema completo
uvicorn src.interfaces.api.app:app --reload  # solo API en modo dev

# Base de datos
alembic upgrade head                   # aplica todas las migraciones
alembic revision --autogenerate -m "descripcion"  # genera nueva migración
alembic downgrade -1                   # revierte última migración

# Tests
pytest tests/unit/         -v          # tests unitarios
pytest tests/integration/  -v          # tests de integración
pytest tests/e2e/          -v          # tests end-to-end
pytest --cov=src --cov-report=term-missing  # con cobertura

# Backtesting
python -m src.backtesting.cli --asset BTC --window 5m --start 2024-01-01
python -m src.backtesting.cli --asset ETH --window 15m --report

# Utilidades
python scripts/check_env.py            # verifica variables de entorno
python scripts/seed_db.py              # carga datos iniciales

# Docker
docker-compose up -d                   # levanta PostgreSQL + Redis + Grafana
docker-compose down                    # para todos los servicios

# Linting y tipos (a instalar en dev)
ruff check src/                        # linting rápido
mypy src/ --ignore-missing-imports     # verificación de tipos
```

---

## Project Structure

```
polymarket-bot/
├── SPEC.md                    ← este archivo (fuente de verdad)
├── CLAUDE.md                  ← instrucciones para Claude Code
├── main.py                    ← entry point: asyncio.run(bootstrap())
├── pyproject.toml
├── requirements.txt
├── alembic.ini
├── Dockerfile
├── docker-compose.yml
│
├── tasks/                     ← gestión de trabajo por sprint
│   ├── plan.md                ← roadmap completo con fases A-D
│   └── todo.md                ← tareas atómicas del sprint actual
│
├── skills/user/               ← skills propios de Claude Code
│   ├── polymarket-market-discovery/SKILL.md
│   ├── algorithmic-strategy-protocol/SKILL.md
│   └── paper-vs-real-execution-mode/SKILL.md
│
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 001_initial_schema.py
│       ├── 002_audit_events_table.py   ← pendiente crear
│       ├── 003_bot_settings_mode.py    ← pendiente crear
│       └── 004_order_retry_fields.py   ← pendiente crear
│
├── monitoring/
│   ├── prometheus.yml
│   ├── alerts.yml
│   └── grafana/dashboard/
│       ├── prometheus.yml
│       └── trading_dashboard.json
│
├── scripts/
│   ├── check_env.py
│   └── seed_db.py
│
├── tests/
│   ├── unit/
│   │   ├── test_domain.py
│   │   ├── test_risk.py
│   │   ├── test_strategy.py
│   │   └── test_backtesting_metrics.py  ← pendiente crear
│   ├── integration/
│   │   ├── test_market_service.py
│   │   ├── test_paper_trading.py
│   │   └── test_backtesting_engine.py   ← pendiente crear
│   └── e2e/
│       └── test_full_cycle.py
│
└── src/
    ├── domain/                ← reglas puras, sin dependencias externas
    │   ├── entities/
    │   │   ├── market.py      ← MarketInfo, MarketCycle (frozen dataclasses)
    │   │   ├── order.py       ← Order con OrderStatus
    │   │   └── position.py    ← Position abierta o cerrada
    │   ├── value_objects/
    │   │   ├── market_tick.py ← MarketTick (frozen, con is_liquid_enough)
    │   │   ├── signal.py      ← StrategySignal, SignalDirection, Outcome
    │   │   ├── risk_decision.py ← RiskDecision (allow/deny + razón)
    │   │   ├── trade_result.py  ← TradeResult con PnL
    │   │   └── ws_state.py    ← WSState enum: CONNECTING/CONNECTED/RECONNECTING/FAILED
    │   ├── enums/
    │   │   ├── asset.py       ← Asset.BTC / Asset.ETH
    │   │   ├── window.py      ← Window.M5 / Window.M15
    │   │   ├── trading_mode.py ← TradingMode.PAPER / TradingMode.REAL
    │   │   ├── order_status.py ← PENDING/CONFIRMED/SUBMITTED/FILLED/FAILED/CANCELLED
    │   │   ├── order_side.py   ← BUY / SELL
    │   │   └── signal_type.py  ← ENTER / EXIT
    │   └── exceptions.py      ← NoActiveMarketsError, MarketFilterError (PENDIENTE)
    │
    ├── application/           ← casos de uso, orquestación
    │   ├── services/
    │   │   ├── market_service.py    ← discovery + ciclo de mercado
    │   │   ├── trading_service.py   ← orquesta strategy engine + risk + execution
    │   │   └── portfolio_service.py ← posiciones abiertas + PnL agregado
    │   ├── ports/
    │   │   ├── execution_port.py    ← interfaz de ejecución (paper o real)
    │   │   ├── market_data_port.py  ← interfaz de datos de mercado
    │   │   ├── notification_port.py ← interfaz de notificaciones (Telegram)
    │   │   └── repository_port.py   ← interfaz de persistencia
    │   └── dto/
    │       ├── market_dto.py
    │       ├── order_dto.py
    │       └── portfolio_dto.py
    │
    ├── strategies/            ← estrategias algorítmicas
    │   ├── base.py            ← BaseStrategy ABC (5 métodos obligatorios)
    │   ├── engine.py          ← StrategyEngine: registro + orquestación
    │   ├── buy_above_threshold/
    │   │   ├── config.py      ← BuyAboveThresholdConfig (7 parámetros)
    │   │   └── strategy.py    ← implementación completa
    │   └── filters/
    │       ├── base.py
    │       ├── liquidity_filter.py     ← min USDC en cada lado del book
    │       ├── spread_filter.py        ← max spread tolerado
    │       ├── tick_confirmation.py    ← N ticks consecutivos
    │       └── time_filter.py          ← filtros de hora/día
    │
    ├── risk/                  ← Risk Engine: reglas allow/deny
    │   ├── base.py            ← BaseRule ABC
    │   ├── engine.py          ← RiskEngine.evaluate(signal) → RiskDecision
    │   ├── context.py         ← RiskContext (estado actual del portfolio)
    │   └── rules/
    │       ├── drawdown.py        ← límite de caída desde peak
    │       ├── hedge.py           ← regla de posición opuesta
    │       ├── max_exposure.py    ← capital máximo expuesto
    │       ├── max_positions.py   ← límite de posiciones simultáneas
    │       └── min_balance.py     ← balance mínimo para operar
    │
    ├── execution/             ← handlers de ejecución de órdenes
    │   ├── base.py            ← BaseExecutionHandler ABC
    │   ├── paper_handler.py   ← simulación con slippage realista + PnL
    │   └── real_handler.py    ← 3 capas de confirmación + retry + audit
    │
    ├── infrastructure/
    │   ├── polymarket/
    │   │   ├── http_client.py  ← fetch de mercados activos (CLOB API v2)
    │   │   ├── ws_client.py    ← suscripción order book + reconexión exponencial
    │   │   ├── clob_client.py  ← submit/cancel órdenes en el CLOB
    │   │   └── adapters.py     ← transformación raw API → domain objects
    │   ├── db/
    │   │   ├── models.py       ← modelos SQLAlchemy (ORM)
    │   │   ├── repository.py   ← implementación de repository_port
    │   │   └── session.py      ← async session factory
    │   ├── cache/
    │   │   └── redis_client.py ← get/set/delete con TTL, pub/sub
    │   ├── security/
    │   │   ├── audit_log.py    ← registro inmutable de toda acción real
    │   │   ├── key_manager.py  ← gestión de private key (env/KMS)
    │   │   ├── log_sanitizer.py ← elimina secrets de logs
    │   │   ├── rate_limiter.py  ← límite de rate para CLOB y Telegram
    │   │   ├── secure_config.py ← carga de configuración segura
    │   │   └── security_guard.py ← validaciones pre-ejecución
    │   └── observability/
    │       ├── logging.py      ← configuración structlog
    │       └── metrics.py      ← métricas Prometheus
    │
    ├── interfaces/
    │   ├── api/               ← FastAPI
    │   │   ├── app.py         ← creación de la app + middleware
    │   │   ├── middleware.py
    │   │   ├── routers/
    │   │   │   ├── health.py
    │   │   │   ├── markets.py
    │   │   │   ├── orders.py
    │   │   │   ├── positions.py
    │   │   │   ├── metrics.py
    │   │   │   └── dashboard.py
    │   │   ├── schemas/
    │   │   │   ├── health_schema.py
    │   │   │   ├── market_schema.py
    │   │   │   ├── order_schema.py
    │   │   │   └── position_schema.py
    │   │   └── static/        ← dashboard HTML/CSS/JS (migrar a React en D19)
    │   └── telegram/
    │       ├── bot.py         ← entry point aiogram + dispatcher
    │       ├── middleware.py
    │       └── handlers/
    │           ├── start.py
    │           ├── status.py
    │           ├── positions.py
    │           ├── settings.py   ← switch paper/real + PIN
    │           └── alerts.py
    │
    ├── backtesting/           ← D18 — replay de datos históricos
    │   ├── cli.py
    │   ├── data_loader.py
    │   ├── engine.py
    │   ├── metrics.py         ← Sharpe, drawdown, winrate, profit factor
    │   └── reporter.py
    │
    └── core/                  ← bootstrap, DI, lifecycle
        ├── bootstrap.py       ← inicialización ordenada del sistema
        ├── config.py          ← Settings (pydantic-settings)
        ├── container.py       ← inyección de dependencias
        └── lifecycle.py       ← startup/shutdown hooks
```

---

## Code Style

Toda función pública lleva type hints. Los dataclasses de dominio son
`frozen=True`. Los servicios de aplicación son clases async. Los handlers
de Telegram son funciones async independientes.

### Ejemplo canónico — handler de estrategia

```python
# src/strategies/buy_above_threshold/strategy.py
async def should_enter(
    self,
    cycle: MarketCycle,
    tick: MarketTick,
) -> StrategySignal | None:
    """Pura: mismo input → mismo output. Sin efectos secundarios."""
    if self._entry_price is not None:
        return None
    if not self._passes_filters(tick):
        return None
    if self._ticks_above < self.cfg.confirm_ticks:
        return None
    return StrategySignal(
        strategy_name   = self.name,
        market_id       = tick.market_id,
        direction       = SignalDirection.ENTER,
        outcome         = Outcome.YES,
        confidence      = min(tick.yes_price, 0.99),
        price_at_signal = tick.yes_price,
        reason          = f"YES {tick.yes_price:.3f} ≥ {self.cfg.threshold} "
                          f"por {self._ticks_above} ticks",
    )
```

### Ejemplo canónico — port de ejecución

```python
# src/application/ports/execution_port.py
from abc import ABC, abstractmethod
from src.domain.entities.order import Order
from src.domain.value_objects.signal import StrategySignal
from src.domain.value_objects.risk_decision import RiskDecision

class ExecutionPort(ABC):
    @abstractmethod
    async def execute(
        self,
        signal: StrategySignal,
        decision: RiskDecision,
    ) -> Order: ...

    @abstractmethod
    async def close_position(self, order: Order) -> Order: ...
```

### Ejemplo canónico — logging estructurado

```python
import structlog
log = structlog.get_logger(__name__)

await log.ainfo(
    "order.submitted",
    order_id    = order.order_id,
    market_id   = order.market_id,
    mode        = order.mode.value,
    amount_usdc = order.amount_usdc,
    # NUNCA: private_key, api_secret, pin
)
```

---

## Environment Variables

```bash
# Polymarket CLOB API
POLYMARKET_API_KEY=
POLYMARKET_API_SECRET=
POLYMARKET_API_PASSPHRASE=
POLYMARKET_PRIVATE_KEY=        # NUNCA en logs ni código

# Base de datos
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/polybot

# Redis
REDIS_URL=redis://localhost:6379/0

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_ADMIN_CHAT_ID=        # único chat_id autorizado

# Seguridad
REAL_MODE_PIN=                 # PIN de 6 dígitos para activar modo real
LOG_LEVEL=INFO

# Observabilidad
PROMETHEUS_PORT=9090
GRAFANA_ADMIN_PASSWORD=
```

---

## Testing Strategy

| Nivel | Directorio | Cobertura mínima | Herramienta |
|---|---|---|---|
| Unit | `tests/unit/` | 80% en `src/domain/` y `src/strategies/` | pytest + pytest-asyncio |
| Integration | `tests/integration/` | Flujos completos con mocks de Polymarket | pytest + httpx test client |
| E2E | `tests/e2e/` | Ciclo completo paper: discovery → señal → orden → PnL | pytest con Redis/PG reales |
| Backtesting | `tests/unit/test_backtesting_metrics.py` | Métricas: Sharpe, drawdown, winrate | pytest |

**Casos de test obligatorios por módulo:**

`test_strategy.py`: entrada correcta, umbral no alcanzado, liquidez
insuficiente, spread excesivo, ticks insuficientes, stop-loss, stop-drop,
timeout.

`test_risk.py`: allow correcto, deny por drawdown, deny por max_exposure,
deny por max_positions, deny por min_balance, precedencia de reglas.

`test_domain.py`: MarketInfo inmutable, MarketTick.is_liquid_enough,
StrategySignal.confidence validation, Order transitions de estado.

`test_paper_trading.py`: fill con slippage calculado, PnL positivo y
negativo guardado en DB, ciclo completo de apertura y cierre.

---

## Boundaries

### Siempre
- Validar token en cada request de Telegram (solo `TELEGRAM_ADMIN_CHAT_ID`)
- Sanitizar logs antes de escribir (log_sanitizer elimina keys/secrets)
- Guardar `Order` en DB con `status=PENDING` ANTES de cualquier submit al CLOB
- Registrar en `audit_log` todo submit al CLOB, independientemente del resultado
- Respetar el Decisions Log de este spec sin excepciones

### Preguntar antes
- Cambios al schema de PostgreSQL (requiere nueva migración de Alembic)
- Añadir un nuevo activo o ventana temporal (fuera del scope actual)
- Modificar la firma del ABC `BaseStrategy` (rompe todas las estrategias)
- Cambiar el flujo de confirmación de real trading
- Añadir nuevas dependencias al `requirements.txt`

### Nunca
- Guardar `POLYMARKET_PRIVATE_KEY` en logs, strings de error o respuestas Telegram
- Ejecutar órdenes reales sin las 3 capas de confirmación
- Reintenter un submit al CLOB sin verificar `polymarket_order_id` primero
- Eliminar tests que fallan sin aprobación humana
- Commitear archivos `.env` o cualquier secret

---

## Success Criteria

Antes de marcar cualquier fase como completa, verificar:

- [ ] `pytest tests/ -v` pasa sin errores ni warnings
- [ ] `mypy src/ --ignore-missing-imports` sin errores de tipo
- [ ] `python scripts/check_env.py` todas las variables presentes
- [ ] `curl http://localhost:8080/health` retorna `{"status": "ok"}`
- [ ] `docker-compose up -d` levanta todos los servicios sin errores
- [ ] Prometheus en `localhost:9090` recibe métricas del bot
- [ ] `git-secrets --scan` no encuentra secrets en el repositorio

---

## Open Questions

Estas preguntas están pendientes de respuesta. No implementar las áreas
afectadas hasta resolverlas:

1. ¿El catálogo de mercados se refresca cada N minutos o solo al arrancar?
   Afecta: `market_service.py` — frecuencia de llamada al `http_client`.

2. ¿Se permite más de una posición simultánea en el mismo mercado?
   Afecta: `max_positions` rule en Risk Engine.

3. ¿El dashboard web (D19) debe ser React o quedarse como HTML estático?
   Afecta: sprint de D19 y dependencias de frontend.

4. ¿El backtesting usa datos reales de la API de Polymarket o un dataset
   descargado previamente?
   Afecta: `data_loader.py` y las credenciales necesarias para correr D18.

---

## Changelog

| Versión | Fecha | Cambio |
|---|---|---|
| 1.0 | 2026-05-17 | Creación inicial basada en estructura real del repositorio |