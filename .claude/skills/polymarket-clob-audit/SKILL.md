---
name: polymarket-clob-audit
description: >
  Auditoría obligatoria de la integración Polymarket CLOB V2 (abril 2026+).
  Activa cuando se modifica cualquier archivo en src/infrastructure/polymarket/
  (clob_client, http_client, ws_client, adapters, data_api_client), cuando se
  toca firma EIP-712, builderCode, signature_type, fees dinámicos, pUSD, o
  cuando se sube/baja la versión del SDK py-clob-client-v2. También activa
  ante preguntas sobre endpoints REST/WS, autenticación L1/L2, o el ciclo de
  vida de una orden V2 (sin nonce, con timestamp ms).
---

# Skill: Polymarket CLOB V2 Audit

## Hechos inamovibles (no inventar, no extrapolar)

- **SDK oficial:** `py-clob-client-v2` 1.0.1 (low-level, Polymarket Engineering).
- **REST CLOB:** `https://clob.polymarket.com`
- **Gamma:** `https://gamma-api.polymarket.com`
- **Data API:** `https://data-api.polymarket.com`
- **WebSocket:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- **Chain:** Polygon Mainnet (ID 137).
- **Colateral:** pUSD (Polymarket USD, V2 abril 2026 — sustituyó USDC.e).
- **Auth:** L1 (EIP-712 wallet signature) + L2 (HMAC `api_key`/`api_secret`/`api_passphrase`).
- **Order V2:** timestamp en milisegundos para unicidad. **NO** nonces, **NO** `feeRateBps`, **SÍ** `builderCode`.
- **Fees:** dinámicos por mercado vía `get_clob_market_info(condition_id)`.
- **`signature_type`:** 0=EOA, 1=POLY_PROXY (default), 2=GNOSIS_SAFE, 3=POLY_1271 (deposit wallet).

Cualquier discrepancia entre el código y estos hechos → **detener**, abrir hallazgo en `AUDIT_REPORT.md`, no auto-corregir sin RFC.

---

## Cuándo activa este skill

- Cualquier edición en `src/infrastructure/polymarket/*.py`.
- Tocar `slippage_engine.py` para cálculo de fees (depende del cache de fees por mercado).
- Tocar `.env` o `.env.example` en variables `POLYMARKET_*`.
- Cambiar versión de `py-clob-client-v2` en `requirements*.txt` / `pyproject.toml`.
- Diseñar caché de `get_clob_market_info` en Redis.
- Tarea R1.7 en `RUTA_IMPLEMENTACION.md` (auditoría CLOB V2).

NO activa para:
- Lógica de estrategias (usa `strategy-validation-protocol`).
- Lógica de risk (usa `risk-engine-guard`).
- Switch paper/real (usa `paper-vs-real-execution`).

---

## Checklist de auditoría (obligatorio en cada cambio)

### A. Configuración

- [ ] `POLYMARKET_BUILDER_CODE` presente en `.env.example`, documentado, **enmascarado** en logs.
- [ ] `POLYMARKET_SIGNATURE_TYPE` presente con default `1` (POLY_PROXY).
- [ ] `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE` **nunca** se loguean ni aparecen en strings de excepción.
- [ ] Helpers `_mask_private_key`, `_mask_api_secret`, `_mask_builder_code` usados en cualquier log que toque estos valores.

### B. Inicialización del cliente

- [ ] `ClobClient(...)` recibe `signature_type` **explícito** (no confiar en el default del SDK).
- [ ] Chain ID = 137 (Polygon Mainnet), explícito.
- [ ] Endpoints REST/WS apuntan a los hosts oficiales (lista de "Hechos inamovibles").
- [ ] Cliente Gamma y Data API se inicializan **sin** credenciales L2 (son públicos).

### C. Orden V2 (lifecycle)

- [ ] La orden incluye `timestamp` en **milisegundos** (no segundos).
- [ ] **No** se calcula ni envía `nonce`.
- [ ] **No** se calcula ni envía `feeRateBps`.
- [ ] `builderCode` se incluye en el campo de la orden, no como header.
- [ ] El `order_id` local (UUID) se genera **antes** del submit y se persiste antes de llamar al CLOB (idempotencia).
- [ ] Tras submit, se almacena `polymarket_order_id` retornado para verificación en reintentos.
- [ ] Cualquier reintento verifica primero el estado por `polymarket_order_id` antes de re-submit.

### D. Fees dinámicos

- [ ] `get_clob_market_info(condition_id)` se cachea en Redis con TTL ≤ 5 min y key `clob:market_info:{condition_id}`.
- [ ] `slippage_engine.py` consume el fee desde cache; si cache miss, refresca y reintenta una vez antes de fallar.
- [ ] Cache invalidable manualmente (comando admin) para responder a cambios de mercado.

### E. WebSocket

- [ ] Reconexión con backoff exponencial (máx 5 reintentos, espera `2^n` segundos).
- [ ] Heartbeat / ping configurado según protocolo Polymarket.
- [ ] Mensajes parseados con guardas: `bids`, `asks`, `price_change`, `book` — campos faltantes → tick descartado con log WARNING.
- [ ] Suscripción usa `assets_ids` para tokens YES y NO (token_id) además de `markets` (condition_id).

### F. Observabilidad

- [ ] Métricas Prometheus: latencia de submit, % éxito, count de retries, edad del cache de fees.
- [ ] Audit log (`audit_events` en DB) registra **todo** submit, éxito o fallo.
- [ ] Errores del CLOB se mapean a excepciones de dominio (`CLOBError`, `CLOBRateLimitError`, `CLOBInsufficientLiquidity`).

---

## Racionalizaciones a rechazar

- *"El SDK ya pone el `signature_type` por defecto, no hace falta pasarlo."* → No. **Siempre explícito** — el default puede cambiar entre versiones del SDK.
- *"Como Gamma es público, podemos usarlo para datos críticos de trading."* → No. Gamma es para descubrimiento de mercados; precios y order book vienen del WebSocket CLOB.
- *"Cacheamos fees solo en memoria, sin TTL."* → No. Sin TTL, un fee desactualizado puede causar pérdidas en producción. Redis + TTL ≤ 5 min.
- *"Si el WebSocket se cae, reconecto inmediato sin backoff."* → No. Backoff exponencial obligatorio para no saturar el endpoint.
- *"Para acelerar, no esperamos respuesta del submit y asumimos éxito."* → No. Sin `polymarket_order_id` no hay forma de verificar — riesgo de doble submit.

---

## Hallazgos comunes durante auditoría

| Hallazgo | Severidad | Acción |
|---|---|---|
| `signature_type` no pasado explícito | 🟡 ALTA | Pasar explícito desde `settings`, añadir test |
| Fees no cacheados | 🟡 ALTA | Implementar cache Redis con TTL |
| Reconexión WS sin backoff | 🔴 CRÍTICA | Añadir backoff exponencial |
| `builderCode` logueado completo | 🔴 CRÍTICA | Aplicar `_mask_builder_code` |
| Orden enviada sin guardar en DB | 🔴 CRÍTICA | Persistir antes de submit |
| `feeRateBps` en payload V2 | 🔴 CRÍTICA | Eliminar — V2 no lo usa |
| `nonce` calculado | 🔴 CRÍTICA | Eliminar — V2 usa timestamp ms |

---

## Salidas esperadas tras una auditoría

1. Pull request con cambios mínimos y reversibles.
2. Tests nuevos cubriendo el cambio (unit + un integration si toca submit).
3. Entrada en `AUDIT_REPORT.md` con fecha, alcance y hallazgos.
4. Actualización de `RECORRIDO_ACTUAL.md` si el cambio cierra una tarea de `RUTA_IMPLEMENTACION.md` (típicamente R1.7).
5. Métricas Prometheus actualizadas en `monitoring/` si aplica.

---

## Referencias rápidas

- Docs Polymarket: https://docs.polymarket.com
- Migración CLOB V2 (abril 2026): consultar `docs_historicos/` y `RECORRIDO_ACTUAL.md`.
- SDK source: https://github.com/Polymarket/py-clob-client (V2 fork)
