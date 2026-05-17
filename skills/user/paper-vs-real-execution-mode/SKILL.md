---
name: paper-vs-real-execution-mode
description: >
  Formaliza el switch entre Paper Trading y Real Trading: cuándo y cómo
  activar cada modo, los guardrails de confirmación de Telegram para operaciones
  reales, la regla de idempotencia de órdenes, y el manejo de slippage,
  retries y redeem. Activa cuando se trabaja en `execution/`, cuando se
  implementa o revisa el flujo de confirmación de Telegram para real trading,
  cuando se diseña o revisa la persistencia de órdenes, o cuando se discute
  cómo una StrategySignal se convierte en una orden ejecutada (paper o real).
---

# Skill: Paper vs Real Execution Mode

## Overview

Este skill define el contrato de ejecución de órdenes para ambos modos del bot.
Garantiza que el cambio de Paper Trading a Real Trading sea explícito, seguro,
auditado y reversible, y que las órdenes reales nunca se ejecuten sin
confirmación humana mediante Telegram.

**Principio central:** Paper Trading es el modo por defecto. Real Trading
requiere tres capas de confirmación antes de ejecutar cualquier orden.

---

## Cuándo usar este skill

Activa cuando:

- Se implementa o modifica cualquier archivo en `execution/`.
- Se implementa el flujo de confirmación en `interfaces/telegram/`.
- Se diseña el modelo de base de datos para `Order` o `Position`.
- Se revisa la lógica de idempotencia, retries o redeem de órdenes.
- Se discute cómo activar/desactivar el modo real desde Telegram.
- Se revisa el cálculo de PnL en paper trading.

NO activas para:
- Lógica de estrategias (usar `algorithmic-strategy-protocol`).
- Descubrimiento de mercados (usar `polymarket-market-discovery`).
- Configuración general de seguridad (usar `security-and-hardening`).

---

## Contratos de dominio (inamovibles)

### ExecutionMode

```python
# domain/models/execution.py
from enum import Enum

class ExecutionMode(str, Enum):
    PAPER = "paper"
    REAL  = "real"
```

### OrderStatus

```python
class OrderStatus(str, Enum):
    PENDING   = "pending"     # creada, esperando confirmación (real) o ejecución
    CONFIRMED = "confirmed"   # confirmada por el humano vía Telegram (solo real)
    SUBMITTED = "submitted"   # enviada al CLOB de Polymarket
    FILLED    = "filled"      # ejecutada completamente
    PARTIAL   = "partial"     # ejecutada parcialmente
    FAILED    = "failed"      # fallo irrecuperable tras retries
    CANCELLED = "cancelled"   # cancelada por el humano o por timeout de confirmación
```

### Order

```python
# domain/models/execution.py (continuación)
from dataclasses import dataclass, field
from datetime import datetime
from domain.models.signal import StrategySignal, Outcome

@dataclass
class Order:
    order_id: str              # UUID generado localmente (idempotencia)
    market_id: str
    mode: ExecutionMode
    outcome: Outcome           # YES o NO
    side: str                  # "buy" o "sell"
    amount_usdc: float         # importe en USDC
    price_limit: float         # precio límite (del tick que generó la señal)
    strategy_name: str
    signal: StrategySignal
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float | None = None
    slippage: float | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    confirmed_at: datetime | None = None
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
    retry_count: int = 0
    polymarket_order_id: str | None = None  # ID del CLOB tras submit
```

### TradeResult

```python
@dataclass(frozen=True)
class TradeResult:
    order: Order
    pnl_usdc: float            # positivo = ganancia, negativo = pérdida
    pnl_pct: float             # porcentaje sobre el capital invertido
    exit_reason: str           # razón textual (stop-loss, timeout, etc.)
    closed_at: datetime = field(default_factory=datetime.utcnow)
```

---

## Proceso Paper Trading

Paper Trading simula ejecución realista sin enviar órdenes al CLOB.

### Paso 1 — Recibir StrategySignal

```python
# execution/paper_handler.py
class PaperExecutionHandler:
    def __init__(self, db: "OrderRepository", event_bus: "EventBus"):
        self._db = db
        self._bus = event_bus

    async def execute(self, signal: StrategySignal, decision: "RiskDecision") -> Order:
        order = Order(
            order_id      = str(uuid4()),
            market_id     = signal.market_id,
            mode          = ExecutionMode.PAPER,
            outcome       = signal.outcome,
            side          = "buy" if signal.direction == SignalDirection.ENTER else "sell",
            amount_usdc   = decision.position_size_usdc,
            price_limit   = signal.price_at_signal,
            strategy_name = signal.strategy_name,
            signal        = signal,
        )
        await self._db.save(order)
        return order
```

### Paso 2 — Simular fill con slippage realista

```python
    async def simulate_fill(self, order: Order, latest_tick: MarketTick) -> Order:
        """Simula el precio de ejecución con slippage de mercado."""
        # Slippage: 0.1% a 0.5% según liquidez (inverso)
        liquidity = latest_tick.yes_liquidity if order.outcome == Outcome.YES \
                    else latest_tick.no_liquidity
        slippage_pct = max(0.001, min(0.005, 50.0 / max(liquidity, 1.0)))
        direction = 1 if order.side == "buy" else -1
        filled_price = order.price_limit * (1 + direction * slippage_pct)

        order.filled_price = round(filled_price, 4)
        order.slippage     = round(slippage_pct, 5)
        order.status       = OrderStatus.FILLED
        order.filled_at    = datetime.utcnow()

        await self._db.update(order)
        await self._bus.publish("order.filled", order)
        return order
```

### Paso 3 — Calcular y guardar PnL

```python
    def calculate_pnl(self, entry: Order, exit_order: Order) -> TradeResult:
        invested = entry.amount_usdc
        returned = exit_order.filled_price * invested / entry.filled_price
        pnl_usdc = returned - invested
        pnl_pct  = pnl_usdc / invested

        return TradeResult(
            order       = exit_order,
            pnl_usdc    = round(pnl_usdc, 4),
            pnl_pct     = round(pnl_pct, 6),
            exit_reason = exit_order.signal.reason,
        )
```

---

## Proceso Real Trading (3 capas de confirmación)

Real Trading requiere confirmación explícita antes de cualquier orden.
Este flujo es obligatorio e inamovible. No se puede saltear ninguna capa.

```
Capa 1: RiskEngine.allow()    → validación automática de reglas de riesgo
Capa 2: Telegram confirmation → confirmación manual del humano (timeout: 60s)
Capa 3: Idempotency check     → verificar que la orden no existe ya en DB
                                  antes de enviar al CLOB
```

### Capa 1 — Risk Engine (automático)

El Risk Engine ya validó la señal antes de llegar aquí. Si llega a
`RealExecutionHandler`, el riesgo ya está aprobado.

### Capa 2 — Confirmación Telegram (manual, obligatoria)

```python
# interfaces/telegram/confirmation.py
CONFIRMATION_TIMEOUT_SECONDS = 60

async def request_confirmation(bot, chat_id: int, order: Order) -> bool:
    """Envía mensaje de confirmación y espera respuesta del humano.
    Retorna True solo si el humano confirma dentro del timeout."""

    message = (
        f"⚠️ REAL TRADING — Confirmación requerida\n\n"
        f"Mercado: {order.market_id}\n"
        f"Estrategia: {order.strategy_name}\n"
        f"Dirección: {order.side.upper()} {order.outcome.value}\n"
        f"Importe: ${order.amount_usdc:.2f} USDC\n"
        f"Precio límite: {order.price_limit:.4f}\n"
        f"Razón: {order.signal.reason}\n\n"
        f"Confirmar en {CONFIRMATION_TIMEOUT_SECONDS}s o se cancela automáticamente."
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Confirmar", callback_data=f"confirm:{order.order_id}"),
        InlineKeyboardButton(text="Cancelar",  callback_data=f"cancel:{order.order_id}"),
    ]])

    await bot.send_message(chat_id, message, reply_markup=keyboard)

    # Esperar respuesta con timeout
    try:
        result = await asyncio.wait_for(
            _wait_for_response(order.order_id),
            timeout=CONFIRMATION_TIMEOUT_SECONDS,
        )
        return result == "confirm"
    except asyncio.TimeoutError:
        await bot.send_message(chat_id, f"Timeout: orden {order.order_id} cancelada.")
        return False
```

### Capa 3 — Idempotencia (obligatoria)

```python
# execution/real_handler.py
class RealExecutionHandler:

    async def execute(self, signal: StrategySignal, decision: "RiskDecision") -> Order:
        order = Order(
            order_id = str(uuid4()),  # UUID local, creado ANTES del submit
            ...
        )
        # Guardar en DB con status=PENDING ANTES de enviar al CLOB
        await self._db.save(order)

        confirmed = await self._telegram.request_confirmation(order)
        if not confirmed:
            order.status = OrderStatus.CANCELLED
            await self._db.update(order)
            return order

        # Verificar idempotencia: ¿ya existe una orden fill para este market_id?
        existing = await self._db.find_active_order(order.market_id)
        if existing:
            order.status = OrderStatus.CANCELLED
            await self._db.update(order)
            return order

        return await self._submit_with_retry(order)

    async def _submit_with_retry(self, order: Order) -> Order:
        """Envía al CLOB con hasta 3 reintentos y backoff exponencial."""
        order.status = OrderStatus.SUBMITTED
        order.submitted_at = datetime.utcnow()
        await self._db.update(order)

        for attempt in range(3):
            try:
                clob_response = await self._clob.submit_order(order)
                order.polymarket_order_id = clob_response["id"]
                order.status = OrderStatus.FILLED
                order.filled_price = clob_response["price"]
                order.filled_at = datetime.utcnow()
                await self._db.update(order)
                await self._audit_log.record(order)  # siempre auditar
                return order
            except CLOBError as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s
                else:
                    order.status = OrderStatus.FAILED
                    await self._db.update(order)
                    await self._audit_log.record(order, error=str(e))
                    raise

        return order
```

---

## Switch de modo (activación desde Telegram)

El modo se guarda en Redis y en la tabla `bot_settings` de PostgreSQL.
El switch NUNCA es automático — siempre requiere comando explícito del humano.

```python
# Comandos Telegram disponibles
/mode paper    → activa Paper Trading (modo seguro, default)
/mode real     → inicia flujo de activación de Real Trading (requiere PIN)
/mode status   → muestra el modo actual y posiciones abiertas
```

**Flujo de activación de Real Trading:**

1. Usuario envía `/mode real`.
2. Bot pide PIN de 6 dígitos configurado en `.env` como `REAL_MODE_PIN`.
3. Si el PIN es correcto → activa modo real, registra en audit log.
4. Si el PIN es incorrecto 3 veces → bloquea el comando por 10 minutos.
5. Al activar modo real → bot envía mensaje de confirmación con timestamp.

```python
# infrastructure/settings.py
class BotSettings(BaseModel):
    mode: ExecutionMode = ExecutionMode.PAPER  # siempre paper por defecto
    real_mode_activated_at: datetime | None = None
    real_mode_activated_by: int | None = None  # chat_id del activador
```

---

## Reglas de seguridad obligatorias

- La private key de Polymarket NUNCA aparece en logs, mensajes de Telegram,
  ni en strings de excepción.
- El `order_id` local se genera ANTES de cualquier llamada al CLOB.
  Si el CLOB falla, el `order_id` local permite verificar idempotencia.
- Todo submit al CLOB se registra en el audit log (tabla `audit_events`)
  independientemente del resultado.
- Si la conexión al CLOB cae durante un submit, el bot verifica el estado
  de la orden por `polymarket_order_id` antes de reintentar.

---

## Señales de alerta (Red Flags)

- Se llama al CLOB sin antes guardar la `Order` en la DB.
  → La DB siempre es la fuente de verdad, no el CLOB.
- El modo Real Trading se activa sin solicitar confirmación de Telegram.
  → Tres capas de confirmación son obligatorias, siempre.
- El PnL en paper trading usa el precio del tick actual sin slippage.
  → El slippage simulado es obligatorio para que paper sea realista.
- La orden se reintenta sin verificar si el CLOB ya la procesó.
  → Siempre verificar `polymarket_order_id` antes de reintentar.
- El timeout de confirmación de Telegram es mayor a 120 segundos.
  → Máximo 60 segundos. Las oportunidades de mercado son breves.

---

## Racionalizaciones comunes que Claude Code debe rechazar

**"En paper trading no necesito guardar en DB, es solo simulación."**
→ No. El paper trading alimenta el backtesting y el dashboard. Todo se guarda.

**"Puedo saltar la confirmación de Telegram si la señal tiene alta confianza."**
→ No. La confirmación es obligatoria para todas las órdenes reales,
independientemente de la confianza de la señal.

**"El modo Real se puede activar desde el código sin pasar por Telegram."**
→ No. El switch es siempre explícito y siempre va por el comando de Telegram.

---

## Verificación de completitud (checklist de exit)

Antes de marcar C11/C12 del roadmap como completados:

- [ ] `execution/paper_handler.py` implementa fill con slippage simulado.
- [ ] `execution/real_handler.py` implementa las 3 capas de confirmación.
- [ ] `domain/models/execution.py` contiene `Order`, `OrderStatus`,
      `ExecutionMode`, `TradeResult`.
- [ ] `interfaces/telegram/confirmation.py` tiene timeout de 60s.
- [ ] El switch `/mode real` requiere PIN.
- [ ] Todo submit al CLOB se registra en `audit_events`.
- [ ] Tests en `tests/unit/test_paper_handler.py` cubren: fill correcto,
      slippage calculado, PnL guardado en DB.
- [ ] Tests en `tests/unit/test_real_handler.py` cubren: confirmación
      aprobada, confirmación rechazada, timeout de confirmación, retry
      exitoso, retry fallido tras 3 intentos.
- [ ] `python -m pytest tests/unit/test_paper_handler.py tests/unit/test_real_handler.py -v`
      pasa sin errores.