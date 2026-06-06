# AUDIT_REPORT.md — Polymarket API Integration Audit

**Fecha:** 2026-06-05  
**Alcance:** Revisión completa de la integración con Polymarket API vs documentación oficial  
**Fase:** P11.1 — PLANEAR → CONSTRUIR → TESTEAR → DESPLEGAR

---

## Resumen Ejecutivo

Se auditó la integración del bot con la API de Polymarket (Gamma API, CLOB API, Data API, WebSocket)
contra la [documentación oficial](https://docs.polymarket.com/). Se identificaron **6 problemas**
(4 críticos, 2 medios). Todos fueron corregidos, validados con 127 tests, y ruff reporta 0 errores.

---

## Hallazgos y Correcciones

### 🔴 CRÍTICO #1 — `neg_risk` no se manejaba

**Problema:** El endpoint `/book` y los mensajes WS incluyen el campo `neg_risk`. Si un mercado
tiene `neg_risk: true`, las órdenes DEBEN incluir `negRisk: true` o serán rechazadas.
Había 0 referencias a `neg_risk`/`negRisk` en todo el código.

**Corrección:**
- `adapters.py`: Nuevo método `parse_neg_risk(raw)` para extraer el flag
- `ws_client.py`: Cachea `neg_risk` en Redis cuando se detecta en mensajes WS
- `http_client.py`: Cachea `neg_risk` desde `/book` REST en `set_market_metadata()`
- `clob_client.py`: Nuevo parámetro `neg_risk` en `create_order()`, lo pasa al SDK
- `real_handler.py`: Resuelve `neg_risk` desde Redis metadata y lo pasa a `create_order()`
- `market.py`: Nuevo campo `neg_risk: bool` en la entidad `Market`

---

### 🔴 CRÍTICO #2 — `tick_size_change` del WS no se monitoreaba

**Problema:** Polymarket cambia tick sizes dinámicamente (ej: cerca de 0.04 o 0.96).
Si usamos el tick_size viejo, las órdenes subsiguientes son rechazadas por "invalid price".
Había 0 referencias a `tick_size_change` en el código.

**Corrección:**
- `ws_client.py`: Nuevo método `_handle_tick_size_change()` que actualiza el tick_size en Redis
- `adapters.py`: Nuevo método `parse_tick_size(raw)` para extraer el valor del evento
- `ws_client.py:_process_message()`: Detecta `tick_size_change` y `new_market`/`market_resolved`

---

### 🔴 CRÍTICO #3 — `builderCode` no se enviaba en órdenes

**Problema:** CLOB V2 requiere `builderCode` en el Order struct para identificar la entidad
que construye la orden. El comentario en `clob_client.py:17` lo mencionaba pero nunca se pasaba.

**Corrección:**
- `key_manager.py`: Nueva variable de entorno `POLYMARKET_BUILDER_CODE` + propiedad `builder_code`
- `clob_client.py`: `create_order()` pasa `builderCode` al SDK (con fallback si no soportado)
- Se genera en https://polymarket.com/settings

---

### 🔴 CRÍTICO #4 — `market_info` (tick_size, MOS) no se cacheaba ni usaba

**Problema:** `get_market_info()` existía pero nunca se llamaba durante el discovery.
`create_order()` usaba `tick_size="0.01"` hardcodeado, ignorando el tick_size real por mercado.

**Corrección:**
- `market_service.py`: Nuevo método `_cache_market_info()` llamado tras cada market discovery.
  Fetcha `/book` (que ya cachea metadata en Redis) y mergea los metadatos en la entidad Market.
- `redis_client.py`: Nuevos métodos `set_market_metadata()` y `get_market_metadata()` con merge parcial
- `real_handler.py`: Resuelve `tick_size` desde Redis metadata en entry y exit orders
- `market.py`: Nuevos campos `tick_size: str` y `min_order_size: float`

---

### 🟡 MEDIO #5 — `price_change` WS deltas no se procesaban correctamente

**Problema:** El adapter aceptaba `event_type="price_change"` pero intentaba parsearlos como `book`
(buscando `bids`/`asks` arrays). Los `price_change` son deltas incrementales con
`price_changes` arrays o `best_bid`/`best_ask` a nivel top-level. Retornaban `None` silenciosamente.

**Corrección:**
- `adapters.py`: `parse_orderbook_message()` ahora maneja 3 formatos de `price_change`:
  1. `best_bid`/`best_ask` a nivel top-level → sintetiza bids/asks
  2. `price_changes` array con deltas → extrae best bid/ask de los deltas
  3. Fallback: retorna None si no hay datos utilizables
- Protegido contra `max()`/`min()` en generadores vacíos (ValueError)

---

### 🟡 MEDIO #6 — No se validaba `minimum_order_size` antes de órdenes

**Problema:** `MIN_ORDER_AMOUNT_PUSD = 1.0` hardcodeado, pero el MOS real varía por mercado.
Órdenes bajo el MOS real serían rechazadas.

**Corrección:**
- `real_handler.py`: Nuevo método `_apply_mos_guardrail(amount, mos, market_id)`
- El MOS se obtiene desde Redis metadata (cacheado de `/book`)
- Se usa `max(MIN_ORDER_AMOUNT_PUSD, market_mos)` como mínimo efectivo
- El guardrail bloquea órdenes bajo el MOS antes de llamar a la API

---

## Archivos Modificados (9 archivos)

| Archivo | Cambios |
|---------|---------|
| `src/domain/entities/market.py` | +3 campos: `neg_risk`, `tick_size`, `min_order_size` |
| `src/infrastructure/polymarket/adapters.py` | +2 métodos (`parse_neg_risk`, `parse_tick_size`), fix `price_change` handling |
| `src/infrastructure/polymarket/ws_client.py` | +`_handle_tick_size_change()`, neg_risk caching, `WS_NON_TICK_EVENTS` |
| `src/infrastructure/polymarket/clob_client.py` | +`neg_risk` y `builderCode` en `create_order()` |
| `src/infrastructure/polymarket/http_client.py` | Cachea neg_risk/tick_size/MOS desde `/book` REST |
| `src/infrastructure/cache/redis_client.py` | +`set_market_metadata()`, `get_market_metadata()`, nuevos campos en serialize |
| `src/infrastructure/security/key_manager.py` | +`POLYMARKET_BUILDER_CODE` env var, propiedad `builder_code` |
| `src/application/services/market_service.py` | +`_cache_market_info()` tras discovery |
| `src/execution/real_handler.py` | Usa tick_size/MOS/neg_risk desde Redis, +`_apply_mos_guardrail()` |
| `tests/unit/test_execution_handlers.py` | Mock de `get_market_metadata` |
| `tests/unit/test_graceful_degradation.py` | Fix timing boundary en cache TTL test |

---

## Validación

- ✅ **ruff:** 0 errores en todos los archivos modificados
- ✅ **pytest:** 127/127 tests pasando (execution_handlers, clob_client, config_validation, strategy, graceful_degradation, domain)
- ✅ **Python syntax:** Todos los archivos parsean correctamente
- ✅ **Code review:** Pasado por code-reviewer-deepseek, issues críticos resueltos

---

## Variables de Entorno Nuevas

```bash
# Builder Code de Polymarket (requerido por CLOB V2)
# Se genera en https://polymarket.com/settings
POLYMARKET_BUILDER_CODE=your_builder_code_here
```

---

## Siguientes Pasos Recomendados

1. **Paper trading test:** Ejecutar `python main.py --mode paper` para verificar que
   los nuevos flujos (metadata caching, tick_size dinámico) funcionan en producción.
2. **Configurar POLYMARKET_BUILDER_CODE:** Generar el builder code en Polymarket Settings
   y agregarlo al `.env` antes de activar real trading.
3. **Monitorear logs de `tick_size_changed`:** Tras el deploy, verificar en los logs
   que los eventos `tick_size_change` se están procesando correctamente.
