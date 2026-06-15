---
name: paper-vs-real-execution
description: >
  Formaliza la dicotomía paper / canary / production en el sistema de ejecución
  del bot. Activa cuando se trabaja en src/execution/ (paper_handler,
  real_handler, fill_simulator, slippage_engine, smart_router, queue_position,
  liquidity_sizer), cuando se diseña el switch /mode desde Telegram, cuando se
  toca la confirmación PIN, cuando se discute idempotencia de órdenes, o cuando
  una StrategySignal se convierte en una orden ejecutable. También activa para
  cambios en interfaces/telegram/handlers/ relacionados con confirmación.
---

# Skill: Paper vs Real Execution Mode

## Principio central

```
Paper = default. Canary = pasarela controlada. Real = sólo tras 3 capas de confirmación.
```

El switch entre modos es **siempre explícito y humano**. No hay activación automática.

---

## Cuándo activa este skill

- Edición de cualquier archivo en `src/execution/`.
- Edición de `interfaces/telegram/handlers/` que toque el flujo `/mode`.
- Cambios en el cálculo de PnL paper.
- Cambios en simulación de slippage, queue position, fill simulator.
- Diseño de la persistencia de `Order` / `Position` / `audit_events`.
- Discusión sobre cómo activar real trading.

NO activa para:
- Lógica de estrategias (usa `strategy-validation-protocol`).
- Auditoría CLOB V2 (usa `polymarket-clob-audit`).
- Reglas de risk (usa `risk-engine-guard`).

---

## Las 3 capas de confirmación para REAL

Esta secuencia es **inamovible**. Saltarse cualquier capa = bug crítico.

```
Capa 1: RiskEngine.evaluate(signal)
        → validación automática de las 6 reglas de risk
        → si decision.allowed == False → cancelar orden, no continuar

Capa 2: Telegram PIN de 6 dígitos + confirmación inline (timeout 60s)
        → bot pide PIN al chat_id autorizado
        → 3 fallos consecutivos → bloqueo 10 min, audit log
        → timeout sin respuesta → cancelar orden

Capa 3: Idempotency key (UUID local generado ANTES del submit)
        → persistir Order(status=PENDING) en DB antes de llamar al CLOB
        → buscar orden activa para mismo market_id → si existe, cancelar la nueva
        → tras submit, guardar polymarket_order_id para reintentos seguros
```

Para **paper**: sólo Capa 1 (Risk). Para **canary**: las 3 capas + capital tope $5–50 USDC.

---

## Contratos de dominio (inamovibles)

```python
class ExecutionMode(str, Enum):
    PAPER   = "paper"
    CANARY  = "canary"
    REAL    = "real"
```

`Order` debe contener al mínimo: `order_id (UUID local)`, `market_id`, `mode`, `outcome`, `side`, `amount_usdc`, `price_limit`, `strategy_name`, `status (OrderStatus)`, `filled_price`, `slippage`, `created_at`, `confirmed_at`, `submitted_at`, `filled_at`, `polymarket_order_id`, `retry_count`.

`OrderStatus`: `PENDING → CONFIRMED → SUBMITTED → FILLED | PARTIAL | FAILED | CANCELLED`.

---

## Paper Trading — invariantes

- Toda orden paper se persiste en DB igual que una real. La DB es la única fuente de verdad.
- `fill_simulator.py` aplica slippage realista basado en liquidez observada (no constante).
- `queue_position.py` simula la posición en el book — no asumir fill instantáneo.
- El PnL se calcula con `filled_price`, **no** con el precio del tick en señal.
- Cada fill emite evento `order.filled` para que el dashboard se actualice.
- Paper trading alimenta el backtesting walk-forward — los datos deben ser **idénticos en forma** a los de real.

---

## Real Trading — invariantes

- `Order.order_id` se genera **antes** de cualquier llamada al CLOB.
- DB guarda `status=PENDING` antes del submit. Si el CLOB falla, la orden queda registrada y se puede reconciliar.
- Telegram pide confirmación con teclado inline: `Confirmar` / `Cancelar`. Timeout 60s.
- **3 capas** de confirmación obligatorias (RiskEngine → PIN/inline → idempotencia).
- Cada submit (éxito o fallo) → `audit_events` en DB.
- Reintentos: máx 3, backoff exponencial 1s/2s/4s, **siempre** verificar `polymarket_order_id` antes de re-submit.
- Si la conexión cae mid-submit: consultar estado por `polymarket_order_id` antes de cualquier acción.
- Logs sanitizados: `_mask_*` helpers para `private_key`, `api_secret`, `api_passphrase`, `builderCode`.

---

## Switch de modo desde Telegram

Comandos (sólo desde `TELEGRAM_CHAT_ID` autorizado):

```
/mode status                 → muestra modo actual + posiciones abiertas
/mode paper                  → vuelve a paper (siempre permitido)
/mode canary <PIN>           → entra a canary con cap $50 USDC
/mode real <PIN>             → entra a real (requiere REAL_MODE_PIN del .env)
```

Reglas del switch:
- Default arranque: `paper`.
- PIN: 6 dígitos numéricos. 3 fallos → bloqueo 10 min + log.
- Cambio de modo registra `bot_settings` (PostgreSQL) y `audit_events` con timestamp + chat_id.
- Cualquier cambio a `real` emite mensaje de confirmación con timestamp visible.

---

## Racionalizaciones a rechazar

- *"En paper no hace falta guardar en DB, es simulación."* → No. Paper alimenta backtesting y dashboard.
- *"Si la confianza de la señal es muy alta, puedo saltar la confirmación de Telegram."* → No. Confirmación obligatoria sin excepciones.
- *"Activo el modo real desde el código para tests."* → No. Switch siempre por Telegram. Para tests, mockear el handler completo.
- *"El timeout de 60s es muy corto, lo subo a 5 min."* → No. Las oportunidades son breves; >60s = stale signal.
- *"Reintento sin chequear `polymarket_order_id`, es más rápido."* → No. Riesgo de doble submit y doble posición.
- *"PnL paper con el precio del tick de señal."* → No. Sin slippage = paper irreal = decisiones erróneas en real.

---

## Red flags

- Llamada al CLOB **sin** orden persistida.
- Switch a `real` que no pasa por Telegram.
- Logs con `private_key`, `api_secret`, `api_passphrase`, o `builderCode` completo.
- Confirmación con timeout >120s.
- Cálculo de PnL paper sin slippage.
- Reintento sin verificación previa por `polymarket_order_id`.
- Capital canary > $50 USDC sin autorización humana explícita.

---

## Checklist de exit (antes de merge)

- [ ] Tests unit: paper fill correcto, slippage aplicado, PnL persistido.
- [ ] Tests unit: confirmación aprobada, rechazada, timeout, retry x3 con éxito y con fallo final.
- [ ] Tests integración: switch `/mode` → estado persistido en DB y Redis.
- [ ] Audit log captura cada submit (éxito y fallo).
- [ ] Helpers `_mask_*` cubren todos los puntos de log que tocan secretos.
- [ ] `pytest tests/unit/test_paper_handler.py tests/unit/test_real_handler.py -v` verde.
- [ ] Si se tocó `slippage_engine.py` → invocar también `polymarket-clob-audit` (fees cacheados).
