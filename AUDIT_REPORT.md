# AUDIT_REPORT.md — PolyBot Security Audit

**Última auditoría:** 2026-07-12 (R2.2 — Auditoría de correctitud full-stack + cierre Olas 1-2)
**Auditoría anterior:** 2026-06-16 (R2.0-redeem — Auditoría redeem CLOB V2)

---

## 🟡 R2.2 — Auditoría de correctitud full-stack (Julio 2026)

**Fecha:** 2026-07-12
**Alcance:** revisión exhaustiva de mappers Entity↔Model, propagación de datos entre capas, guards contra fallos silenciosos, integridad DB, y flujos de real trading. Detonante: la lectura crítica del código realizada tras completar R1.3 (dashboard) reveló bugs latentes que el runtime paper no exponía. R2.2.1–R2.2.10 son las 10 sub-auditorías; su plan de cierre (Olas 1-6) está en RUTA_IMPLEMENTACION.md § R2.2.10.

### Resumen de hallazgos y cierre (Olas 1 y 2)

| # | Hallazgo | Severidad | Estado | Commit(s) |
|---|---|---|---|---|
| **R2.2.1** | `amount = risk_decision.suggested_amount or requested_amount` — `or` colapsa `0.0` legítimo en `requested_amount`, saltándose el sizing del RiskEngine. | 🟠 Alta (sizing) | ✅ Cerrado Ola 1.3 | `720bd85` |
| **R2.2.2** | `_order_to_model` / `_model_to_order` NO persistían `idempotency_key`. Un reintento post-timeout creaba orden duplicada porque la columna existía pero nunca se guardaba. | 🔴 Crítica (dup real) | ✅ Cerrado Ola 1.1+1.2 | `a8a4f46` |
| **R2.2.3** | `RealTradingHandler._call_with_retry` puede retornar `(None, None)` sin marcar error; `float(api_response.get(...))` crashea silente. 3 sitios (entry:287, exit:435, redeem:588). | 🟠 Alta (crash silencioso) | ✅ Cerrado Ola 1.4 | `49318a6` |
| **R2.2.4** | `_get_token_and_price` y `_get_current_price` caían a 0.5 (mid) cuando WS aún no había emitido tick — fill a "50% presunto" con slippage no estimable. | 🔴 Crítica (real trading fantasma) | ✅ Cerrado Ola 1.5 | `49318a6` |
| **R2.2.5** | Archivos basura `=0.28`, `=6.100.0` en root — artefactos de `pip install ...>=X.Y` con redirección bash. Riesgo: se pushean, contaminan builds. | 🟡 Media (housekeeping) | ✅ Cerrado Ola 1.6 + `.gitignore =*` | `0ef61ce` |
| **R2.2.6** | WS emitía `event_type=market_resolved` a nivel DEBUG y se descartaba. Posiciones abiertas en mercados resueltos quedaban colgadas hasta exit manual. | 🟠 Alta (redeem workflow bloqueado) | ✅ Cerrado Ola 2.1 | `f34c8f0` |
| **R2.2.7** | Activar real trading solo requería 2 clicks; NO cumplía la regla dura #3 de CLAUDE.md (3 capas de confirmación incluyendo PIN). | 🔴 Crítica (real trading unsafe) | ✅ Cerrado Ola 2.2 | `6ba4ac0` |
| **R2.2.8** | `_market_cycle_loop` polleaba `get_active_markets` a 30s constantes; durante rollovers largos (M5/M15 gap) hammereaba la API. | 🟡 Media (rate limit) | ✅ Cerrado Ola 2.3 (backoff 30s→5min) | `cceac67` |
| **R2.2.9** | `RETRY_BACKOFF` era determinista `[1s, 2s, 4s]` — N réplicas atacaban en sincronía, thundering herd al recuperarse un 429/5xx. | 🟡 Media (thundering herd) | ✅ Cerrado Ola 2.4 (jitter ≤50%) | `36ffbb1` |
| **R2.2.10** | Paper handler pasaba `volatility=None, regime=None` hardcoded al SlippageEngine (TODO(P9.2) sin cerrar). Slippage paper irrealmente plano. | 🟠 Alta (edge falso en paper) | ✅ Cerrado Ola 2.5 | `7bbe0df` |

**Bonus:** durante Ola 1 también se cerraron **R2.5.1** (idempotency key ahora incluye `side + operation` — antes entry YES + hedge NO en el mismo minuto colisionaban), **R2.5.3** (unique partial index `positions (market_id, mode) WHERE closed_at IS NULL`), y **R2.5.4** (unique index `markets (asset, window, expiry)`), materializados en la migración 005 y sus IntegrityError handlers.

### Métricas post-cierre

| Métrica | Pre-R2.2 | Post-Ola 1+2 |
|---|---|---|
| Tests pasando | 1125 | **1446** (+321) |
| Migraciones aplicables | 004 | **006** (+005 integrity, +006 resolved_at) |
| Reglas duras CLAUDE.md | 8 | **10** (+or fallacy, +mappers simétricos) |
| Skills disponibles | 5 | **8** (+db-integrity-guard, +dependency-hygiene, +ctf-onchain-redeem) |
| Hooks harness | 4 | **6** (+check_dep_drift, +protect_trash) |

### Infraestructura persistente introducida

- `Position.resolved_at: datetime | None` + `is_resolved` property (extensión aditiva en no-go zone, RFC autorizado 2026-07-12).
- `PositionModel.resolved_at` + `ix_positions_resolved_at` (migración 006).
- `IRepositoryPort.mark_positions_resolved(market_id, ts) → int` con idempotencia (`WHERE resolved_at IS NULL`).
- `IMarketDataPort.set_resolution_callback` + `ResolutionCallback` en `PolymarketWSClient` (patrón callback global idempotente).
- `AuditAction.MARKET_RESOLVED` en el enum del audit log.
- `src/interfaces/telegram/pin_gate.py` — `PinGate` reutilizable con SHA256, `hmac.compare_digest` constant-time, rate limit 3 intentos, lockout 10 min por chat_id.
- `RealModeStates.waiting_pin` (FSM aiogram) + `on_pin_message` handler con estados WRONG/LOCKED_OUT/INVALID_FORMAT/OK/NOT_CONFIGURED.
- `RedisClient.push_recent_tick` / `get_recent_ticks` / `clear_recent_ticks` (LPUSH+LTRIM+EXPIRE atómico) — rolling buffer reutilizable para futuras features de streaming analytics.
- `_get_market_context(market_id)` en `PaperTradingHandler` que computa realized volatility annualized + label {panic/trend/chop}.
- Helper puro `compute_empty_backoff_wait(consecutive_empty) → int` en `trading_service` (facilita property tests).
- Helper `_jittered_wait(base) → float` en `real_handler` con `JITTER_FACTOR=0.5`.

### Reglas duras añadidas a CLAUDE.md

- **#9** — Nunca usar truthiness (`or`) sobre valores numéricos donde 0.0 sea válido; usar `is not None`. Justificación: R2.2.1.
- **#10** — Mappers Entity ↔ Model deben ser exhaustivos y simétricos; cualquier campo nuevo en `domain.Order` / `domain.Position` / `domain.Market` DEBE aparecer en `_X_to_model` y `_model_to_X` en el mismo PR. Justificación: R2.2.2 (idempotency_key fantasma).

### Verificación pip-audit post-Olas 1+2 (2026-07-12)

91 findings totales de pip-audit. Distribución por paquete:

| Paquete runtime | Versión actual | Findings | Fix mínimo |
|---|---|---|---|
| aiohttp | 3.9.5 | 31 | ≥3.14.1 |
| starlette | 0.37.2 | 8 | (RUTA 3.1 dice ≥1.1.0) |
| bleach | 6.3.0 | 3 | ≥6.4.0 |
| python-multipart | 0.0.28 | 3 | ≥0.0.31 |
| protobuf | 4.25.9 | 1 | ≥5.29.6 |
| ujson | 5.12.1 | 1 | ≥5.13.0 |

Los 6 paquetes que la RUTA § R2.2.10 Ola 3 identifica dominan la lista. Findings adicionales fuera del RFC de Ola 3: gitpython (4), urllib3 (3), idna (2), requests (1), pytest (1), pip (2), msgpack (1), pygments (1), soupsieve (2). Además, dev tooling (Jupyter stack) suma 26 findings — evaluar si mantener Jupyter en dev deps o mover a un extras separado durante Ola 3.

pip-audit no devuelve severity numérica (`?` en el output); habría que cruzar CVE-por-CVE con NVD para clasificar HIGH/MEDIUM/LOW. Ola 3 debería empezar con esa clasificación antes de tocar pyproject.

### Lo que Ola 1+2 NO cubre (pendiente para Olas 3-6)

- **CVEs deps** — todos los 91 findings de pip-audit siguen sin resolver (Ola 3).
- **Timeouts CLOB/HTTP/WS via env** — sin implementar (Ola 3.2).
- **Dashboard auth JWT + CORS** — endpoints siguen abiertos por trust del reverse proxy (Ola 4).
- **10+ endpoints de métricas MUST/SHOULD** — Ola 4.
- **R2.0-redeem-impl** — el skill `ctf-onchain-redeem` existe pero `web3.py`+CTF contract call sigue no implementado; posiciones resueltas se **detectan** (Ola 2.1) pero NO se **redimen** (Ola 5.1).
- **ParquetDataLoader window filter** — sigue siendo label, no filtro real (Ola 5.2). Métricas M5 y M15 son el mismo dataset.
- **Walk-forward / Monte Carlo / OOS** — sin ejecutar sobre parquets extendidos (Ola 5.3-5.5).
- **Paper marathon end-to-end sobre el stack completo** — pendiente Ola 6.

### Racional del orden de cierre

Ola 1 y 2 se priorizaron por riesgo asc (RUTA § R2.2.10 Ola 6 vs Ola 1) para (a) construir momentum con cambios low-risk mecánicos, (b) reducir la superficie de fallos silenciosos ANTES de tocar deps/dashboard/redeem, (c) dejar la infraestructura (migración 006, PinGate, ResolutionCallback, Redis buffer) lista para que las olas más pesadas puedan asumirla sin miedo a regresiones.

---

## 🔴 R2.0-redeem — Auditoría redeem CLOB V2 (Junio 2026)

**Fecha:** 2026-06-16
**Alcance:** verificar el flujo de redención de tokens ganadores en CLOB V2 (`PolymarketCLOBClient.redeem_position`, `RealTradingHandler.redeem_resolved_position`). Activado por la prioridad del usuario "objetivo #3: el bot debe ser capaz de reclamar las ganancias acumuladas por cada evento".

### Hallazgo CRÍTICO — endpoint REST `/redeem` no existe en V2

El código en `src/infrastructure/polymarket/clob_client.py:252` hacía:

```python
await self._http.post("/redeem", json={"token_id": ..., "market_id": ...},
                      headers={"POLY_ADDRESS": wallet})
```

**Esto está roto en CLOB V2 (abril 2026):**

| Verificación | Resultado |
|---|---|
| Docs oficiales (`https://docs.polymarket.com/llms.txt`) | ❌ Ningún endpoint REST `/redeem` listado; sólo el endpoint público `/api-reference/core/get-user-combo-activity` que **reporta** redeem events |
| Guía `/trading/ctf/redeem.md` | ✅ Confirma: "Exchange winning tokens for pUSD after market resolution" se hace **on-chain via CTF** |
| Contrato (`/resources/contracts.md`) | ✅ CTF en Polygon: `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` (Gnosis CTF estándar) |
| SDK `py-clob-client-v2` 1.0.1 — métodos disponibles | ❌ NO existe `redeem_position`, `redeem`, `redeemPositions` ni equivalente |

**Llamada correcta documentada:**

```
ConditionalTokens(0x4D97...0476045).redeemPositions(
  collateralToken = pUSD (0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB),
  parentCollectionId = bytes32(0),
  conditionId = market.condition_id,
  indexSets = [1, 2]  # cubre ambos outcomes; redime la posición ganadora
)
```

La capa correcta es un **thin collateral adapter** (citado por docs) que burns ERC1155 outcome tokens, recibe USDC.e como colateral, wrappea a pUSD y devuelve a la wallet automáticamente.

### Impacto si no se corrige

- En producción, la primera llamada a `redeem_resolved_position` haría `POST https://clob.polymarket.com/redeem` → 404 / 405.
- Tras los 3 reintentos del `_call_with_retry`, el handler reportaba fallo con un error críptico (`HTTP 404`).
- **No habría reclamación efectiva** de ganancias por mercado resuelto — viola directamente el objetivo #3 del usuario.
- En el peor caso, podría parecer "fallo intermitente" (porque el endpoint sí responde, sólo con 404) y disfrazarse como un problema de red.

### Fix aplicado (conservador, cero efectos en cadena)

| Cambio | Archivo | Línea | Detalle |
|---|---|---|---|
| Nueva excepción `CLOBRedeemNotSupportedError(NotImplementedError)` | `clob_client.py` | 52 | Marca explícitamente que el camino REST no existe |
| Constante `CTF_CONTRACT_ADDRESS` documentada | `clob_client.py` | 50 | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| `redeem_position` ahora hace `raise CLOBRedeemNotSupportedError(...)` | `clob_client.py` | 252 | Mensaje guía al desarrollador hacia `redeemPositions` on-chain |
| `_call_with_retry` no reintenta `NotImplementedError` | `real_handler.py` | 672 | Falla rápido; sin retries innecesarios; log claro |
| `redeem_resolved_position` emite `REAL_REDEEM_FAILED` con `reason="ctf_onchain_required"` | `real_handler.py` | 537 | Audit log distinto de errores de red |
| `AuditAction.REAL_REDEEM_FAILED` añadido | `audit_log.py` | 22 | Permite distinguir fallos de redeem en analytics |

### Tests añadidos (4 nuevos)

`tests/unit/test_clob_client.py::TestRedeemPositionV2`:
- `test_redeem_not_supported_error_is_not_implemented` — verifica jerarquía de excepciones.
- `test_ctf_contract_address_documented` — verifica el address exacto del CTF.
- `test_redeem_position_raises_clob_redeem_not_supported` — mensaje guía contiene "CTF" y "redeemPositions".
- `test_redeem_position_does_not_touch_network` — confirma cero llamadas SDK/HTTP.

`tests/unit/test_execution_handlers.py::TestRealHandlerRedeem`:
- `test_redeem_ctf_unsupported_fail_fast` — handler NO reintenta, emite `REAL_REDEEM_FAILED` con `reason="ctf_onchain_required"`.
- `test_redeem_network_failure` — comportamiento de red mantenido (reintenta hasta agotar y devuelve failure).

### Verificación

- ✅ `pytest tests/unit/test_clob_client.py::TestRedeemPositionV2 -xvs` → 4/4.
- ✅ `pytest tests/unit/test_execution_handlers.py -x -q` → 64/64.
- ✅ `pytest -x -q` (suite completa) → **1,369/1,369** (sin regresiones, +4 nuevos).
- ✅ `ruff check` sobre los archivos modificados → limpio (sin nuevos hallazgos).
- ✅ Cero efectos en cadena durante la auditoría (todos los tests usan `AsyncMock`).

### Fuera de alcance (defer — requiere RFC)

**R2.0-redeem-impl**: implementación efectiva del redeem on-chain via CTF.

Requisitos para esa iteración:
1. Añadir `web3.py` a `requirements.txt` con justificación (chain de mantenimiento, async support).
2. Crear `src/infrastructure/polymarket/ctf_redeemer.py` con wrapper sobre `ConditionalTokens.redeemPositions`.
3. Resolver el `indexSets` correcto por mercado (depende de `outcome` ganador — observable post-resolution en Data API).
4. Gas estimation en MATIC + dry-run + tx receipt + retry on chain reorg.
5. Decidir entre: (a) llamar directo al CTF, o (b) usar el "thin collateral adapter" para auto-wrap a pUSD (preferible para UX, requiere encontrar el address en docs).
6. Property tests Hypothesis sobre cálculo de `indexSets` por outcome.
7. Audit log de tx hash, gas used, pUSD recibido.

Hasta que esta iteración se cierre, R3.x (real trading) **no puede completar el ciclo entry→exit→redeem** y por tanto no es seguro escalar capital más allá de un canary minúsculo.

---

## 🟢 R2.1-smoke — End-to-End Pipeline Verification (Junio 2026)

**Fecha:** 2026-06-15
**Alcance:** validar la cadena completa `discovery → strategy → risk → paper execution` contra Polymarket real, sin depender de B5 (que sigue ⛔ activo) ni de credenciales reales. Cubre los objetivos #1 (conectividad lectura) y #2 (compra/venta paper) del usuario.

### Tooling añadido

- **`scripts/smoke_test_pipeline.py`** (~580 líneas). Discovery alternativo vía Gamma directo (público, helper canónica `detect_asset`), inyección manual de markets en repo+Redis, warmup de ticks reales contra CLOB `/book`, N ciclos completos invocando `TradingService._run_market_cycle()`, y `--force-fake-signal` que inyecta `Signal BUY_YES` directo al `execution_handler.execute_entry()` para validar el camino paper sin esperar a que MR genere señal sobre markets fuera de su régimen. Salida JSON con `validations` por objetivo, exit codes `0|1|2`.
- **33 tests** en `tests/unit/test_smoke_test_pipeline.py` cubriendo fetch+ranking, parseo `Gamma dict → Market`, helpers JSON defensivos, captura de excepciones, todos los caminos de `build_report`, CLI exits, y el path forced-signal.

### Decisiones de diseño

| Item | Decisión |
|---|---|
| Bypass del discovery M5/M15 | Sí — `MarketService.discover_markets()` sigue siendo correcto para cuando B5 se resuelva; el smoke usa Gamma directo. |
| Modificación de `src/` | Cero. Sólo `scripts/` y `tests/`. |
| Reuso de helpers existentes | `detect_asset`, `MarketService.get_market_tick`, `TradingService._run_market_cycle`, `execution_handler.execute_entry`. |
| Window placeholder | `Window.M15` documentado en `build_market_from_gamma()`. El `_run_market_cycle` no filtra por window. |
| Tests sintéticos vs reales | El run real contra Polymarket no es parte de la suite (requiere red + docker-compose). La suite cubre toda la lógica con mocks. |
| Side fix `record_live_data.py` | 1 línea — `detect_asset` → `_detect_asset`. Bug pre-existente que rompía `tests/unit/test_live_crypto_discovery.py`. |

### Verificación contra Polymarket real (paper, sin `.env`)

**Setup**: `docker compose up -d postgres redis`. Sin variables `POLYMARKET_*`.

**Run 1 — Pipeline normal** (`--n-cycles 2 --warmup-ticks 5 --cycle-interval 2`):

```
Estado:               success
Markets usados:       1 (will-bitcoin-hit-1m-before-gta-vi)
Ciclos ejecutados:    2
Errores en pipeline:  0
Órdenes (inferidas):  0
objective_1_connectivity_read:  PASS
objective_2_paper_execution:    PASS_NO_SIGNAL
objective_3_m5_m15_rotation:   BLOCKED_BY_B5
```

5 ticks reales de CLOB `/book` (precio constante 0.4925), 2 ciclos sin excepción. MR no entra (correcto — el market longevo no exhibe oscilación rápida). El pipeline corre limpio.

**Run 2 — Forced signal** (`--force-fake-signal --force-amount 10`):

```
Estado:               success
Markets usados:       1
Ciclos ejecutados:    1
Órdenes (inferidas):  1
forced_signals_executed: 1
objective_2_paper_execution:    PASS_WITH_ORDER

forced_signals[0]:
  success=True
  fill_price=0.493001
  slippage=0.000501
```

La cadena `Signal → SlippageEngine → SmartRouter → fill → Position → Redis balance → DB persist` funciona end-to-end. Slippage realista (0.0005) sobre el bid real (0.4925).

### Lo que NO cubre

- Edge de MeanReversion (los markets longevos no son su régimen — no se puede medir Sharpe/PF aquí; ese sigue siendo el camino de R1.2-ter cuando B5 se resuelva).
- Objetivo #3 (rotación M5/M15 + redeem): bloqueado por B5.
- Real trading (cero efectos en cadena, sin credenciales L1/L2).
- Stress (R2.3) y latencia (R2.4) — fuera de alcance.

### Cobertura tests

- **Suite total**: 1,365 tests, 100% verde (1,332 previos + 33 nuevos).
- **Lint**: `ruff check` limpio sobre los archivos nuevos.
- **No regresiones** en módulos críticos (domain, risk, strategies, execution).

### Implicación para R2.1 (checklist principal)

R2.1 sigue ⛔ **BLOQUEADO por B5**. Lo que cambia:
- Step 1 (conectividad): cubierto por `verify_polymarket_connectivity.py` (necesita `.env` real) + `smoke_test_pipeline.py` (no necesita `.env`).
- Step 2 (recording 168h): sigue bloqueado por B5.
- Step 3-4 (MR edge): sigue bloqueado por B5 (los parquets actuales no son representativos).
- Step 5 (paper marathon 100 ciclos): el script existe; pendiente correr y versionar reporte.
- Step 6 (real trading): NO autorizado.

El smoke E2E **no desbloquea R2.1** — sí valida que el bot funciona técnicamente, no que tenga edge. Real trading sigue dependiendo de B5 + paper marathon con datos representativos.

---

## 🟢 R2.1 — Wallet Connectivity Verification (Junio 2026)

**Fecha:** 2026-06-15
**Alcance:** Cubrir el objetivo #1 del usuario (conectividad wallet read-only) sin depender del bloqueo externo B5. Permite verificar credenciales + saldo + posiciones + trades en cualquier momento, sin colocar órdenes ni mover capital.

### Tooling añadido

- **`scripts/verify_polymarket_connectivity.py`** (≈ 350 líneas, sólo lectura).
  Pasos:
  1. `env`              — valida las 5 `POLYMARKET_*` requeridas.
  2. `init_clients`     — construye `KeyManager` + `PolymarketCLOBClient` + `DataAPIClient`. Captura errores de clave inválida sin romper la salida.
  3. `auth_l1_l2`       — `assert_level_1_auth` (EIP-712) + `assert_level_2_auth` (HMAC) + `get_address` vía SDK.
  4. `balance_pusd`     — `get_balance_allowance` (SDK) con fallback REST `/balance`.
  5. `data_api_positions` — `GET data-api.polymarket.com/positions?user=<wallet>` y agrega `cashPnl`, `currentValue`, `redeemable`.
  6. `clob_open_orders` — `get_open_orders` (SDK L2).
  7. `clob_trades`      — `get_trades` (SDK L2), truncado a `--trades-limit`.
  8. `data_api_activity` — `GET data-api.polymarket.com/activity?user=<wallet>` como cross-check público.

  Salida humana o `--json`. Exit codes:
  - `0` todo OK.
  - `1` faltan variables `POLYMARKET_*` en `.env`.
  - `2` al menos un paso falló (auth, balance, posiciones, trades, activity, o init).

- **Wrappers read-only añadidos** (todos `asyncio.to_thread` sobre el SDK síncrono, sin nuevos endpoints):
  - `PolymarketCLOBClient.assert_auth()` — L1 + L2 + address.
  - `PolymarketCLOBClient.get_open_orders()` — órdenes vivas, normalizadas a `list[dict]`.
  - `PolymarketCLOBClient.get_trades(limit)` — trades históricos (truncado cliente-side).
  - `DataAPIClient.get_activity(limit, activity_type)` — `/activity` público con filtro opcional.

### Decisiones de diseño (CLOB V2 audit checklist)

| Checklist item | Decisión |
|---|---|
| `signature_type` explícito | ✅ Heredado del refactor R1.7, propagado al SDK. |
| Builder code enmascarado en logs | ✅ Heredado de `key_manager`. |
| L1 + L2 verificados antes de cualquier read | ✅ `assert_auth()` falla rápido si la wallet o las credenciales L2 están mal. |
| No coloca ni cancela órdenes | ✅ Sin paths `create_order`/`cancel`/`redeem`. |
| No loguea claves | ✅ Sólo wallet enmascarada. |
| Cross-check público (Data API) vs L2 (SDK) | ✅ Posiciones y trades comparables; el operador detecta divergencias. |
| Errores agrupados como pasos, no traceback crudo | ✅ Cada fallo registra un `StepResult`. |

### Cobertura de tests

- **`tests/unit/test_verify_connectivity.py`** — 25 tests, 100% verde:
  - `TestCheckEnv` (3) — variables faltantes / parciales / completas.
  - `TestSummarize` (3) — agregaciones de positions/trades, manejo de `None`.
  - `TestVerify` (5) — happy path completo (8 pasos en orden), fallo de auth aislado, wallet vacía, env faltante, init de clientes fallido.
  - `TestMain` (4) — exit codes 0/1/2 + salida JSON.
  - `TestClobReadOnlyWrappers` (6) — `assert_auth` L1+L2, propagación de fallo L1, `get_open_orders` lista/vacía, `get_trades` con/sin límite.
  - `TestDataAPIActivity` (4) — payload normal, filtro `type`, respuesta no-lista, propagación de `HTTPStatusError`.

### Lo que NO cubre este trabajo

- **No prueba contra Polymarket real.** Requiere `.env` con credenciales válidas — la verificación real es responsabilidad del operador.
- **No habilita real trading.** R2.1 sigue bloqueado por B5 (no hay markets M5/M15 cripto activos en Gamma).
- **No mueve ni reclama capital.** El script `redeem_position()` existente en `clob_client.py` queda fuera de alcance (R2.2).

### Próxima acción operativa

Cuando el operador cargue credenciales reales en `.env`:

```bash
python scripts/verify_polymarket_connectivity.py
# o salida programática:
python scripts/verify_polymarket_connectivity.py --json | jq
```

Si todos los pasos pasan ✅, la wallet está conectada y se puede iniciar paper trading con datos reales. La capacidad de comprar/vender end-to-end (objetivo #2) se valida por separado contra paper handler (no requiere B5).

---

## 🔴 R2.1 — Pre-Real-Trading Checklist (Junio 2026)

**Fecha:** 2026-06-14
**Veredicto:** ⛔ **REAL TRADING BLOQUEADO** por falta de edge validado.
**Alcance:** Ejecución de los 6 pasos del skill `pre-real-trading-checklist` contra los datos reales del repo.

### Resultados paso a paso

| # | Paso | Estado | Hallazgo |
|---|---|---|---|
| 1 | `check_env.py` paper | ✅ | Variables paper OK. `POLYMARKET_*` no cargadas (esperado mientras estemos pre-real). |
| 2 | Recording 168h+ activo y reciente | 🟡 | `data/parquet/manifest.json` con fecha `2026-06-01` (13 días). El watchdog no corre. Hay parquets utilizables (BTC 15,091 ticks, ETH 14,041) entre 27-may y 02-jun. |
| 3 | `optimize_mr.py` → `optimal_params_mr_real.json` | 🔴 | Archivo existe (2026-06-07), top_config `entry_z=-2.5 ma=20 SL=0.08`. **`avg_sharpe=-1.86`, `win_rate=0.0`, `PF=0.0`** — la optimización corrió pero **no encontró edge**. R1.2 fue marcada ✅ por ejecución, no por cumplimiento de umbrales. |
| 4 | `validate_criteria.py` con datos reales | 🔴 | Tooling existente (`scripts/validate_criteria.py`, `verify_criteria.py`) **es sintético** — usa `HistoricalDataset.generate_synthetic`. **Creado `scripts/backtest_real.py`** que carga `data/parquet/` vía `ParquetDataLoader` y reutiliza la lógica MR de `optimize_mr.run_mr_backtest`. Resultado: **0/4 datasets** pasan los criterios (Sharpe ≥ 0.8, PF ≥ 1.2, WR ≥ 45%, MaxDD ≤ 20%). BTC 5m/15m generan **0 trades** (entry-zscore -2.5 demasiado estricto para el rango observado). ETH 5m/15m: Sharpe **-3.35**, WR 0%, PnL **-60.74 USDC**. |
| 5 | `run_paper_marathon.py --cycles 100` | ❓ | `data/` no contiene reportes `paper_marathon_*.json`. El commit `2eb5c9c` añadió el script pero los resultados no están versionados. R1.1 marcado ✅ sin evidencia auditable en el repo. |
| 6 | `/mode real <PIN>` | ⛔ | **NO autorizado.** Bloqueado por 2, 3, 4 y 5. Además R2.2 (canary 72h) tampoco se ha hecho. |

### Bloqueos identificados

**B1. La estrategia primaria (MeanReversion) no tiene edge en parquets reales.**
- Top config optimizado da Sharpe -1.86 en `optimize_mr.py` y Sharpe -3.35 en `backtest_real.py`.
- BTC genera 0 trades con `entry_zscore=-2.5` (predictor demasiado restrictivo o ventana ma=20 insuficiente).
- ETH genera 108 trades pero todos pierden (WR 0%).
- Conclusión: los parámetros actuales no son utilizables. Hace falta re-optimización con grid más amplio, o revisar la hipótesis de edge de MR en mercados de Polymarket cripto.

**B2. Tooling de validación previo era sintético.**
- `verify_criteria.py` (raíz) y `scripts/validate_criteria.py` generan ticks via `HistoricalDataset.generate_synthetic` o `optimize_bat.generate_realistic_dataset`. Esto **viola la regla dura #7 de CLAUDE.md** ("No optimizar Sharpe en datos sintéticos — solo `data/parquet/` reales") en el flujo de verificación.
- **Resuelto:** `scripts/backtest_real.py` reutiliza `ParquetDataLoader` + `run_mr_backtest`, persiste reporte JSON, exit codes 0/1/2 según criterios.

**B3. Recording inactivo desde hace 13 días.**
- `data/parquet/manifest.json` no se ha actualizado desde el 2026-06-01.
- El watchdog (`scripts/watchdog_recording.py`) no está corriendo.
- Para R2.2 (canary 72h) hace falta data fresca de los últimos días.

### Tooling añadido en R2.1

- `scripts/backtest_real.py` (293 líneas) — backtest MR sobre parquets reales con evaluación contra los 4 criterios oficiales (`Sharpe ≥ 0.8`, `PF ≥ 1.2`, `WR ≥ 45%`, `MaxDD ≤ 20%`). Salida JSON en `data/reports/backtest_real_*.json`.

### R1.2-bis — Re-optimización MR con QUICK grid sobre parquets reales (mismo día)

**Ejecutado:** 2026-06-14, `python scripts/optimize_mr.py --quick --parquet-dir data/parquet --n-ticks 15091`.

**Grid QUICK** (324 combos válidos × 4 datasets = 1296 backtests, ~62s):
- `ma_window ∈ {10, 20, 30}`
- `entry_zscore ∈ {-2.5, -2.0, -1.5}`
- `exit_zscore ∈ {-0.5, 0.0, 0.5}`
- `stop_loss_pct ∈ {0.08, 0.10, 0.15}`
- `timeout_minutes ∈ {45, 60}`
- `position_size_pusd ∈ {5, 10}`

**Resultado:**

| Dataset | Mejor Sharpe | Mejor WR | Mejor PF | Trades |
|---|---|---|---|---|
| BTC_5m  | 0.000 | 0% | 0.00 | **0** trades en los 324 combos |
| BTC_15m | 0.000 | 0% | 0.00 | **0** trades en los 324 combos |
| ETH_5m  | -3.214 | 0% | 0.00 | 62 |
| ETH_15m | -3.214 | 0% | 0.00 | 62 |

**Top config "robusto"**: `ma=20 entry_z=-2.5 exit_z=-0.5 SL=8% timeout=45m size=5` — `avg_sharpe=-1.673, WR=0%, PF=0`.

### Causa raíz (descubierta en R1.2-bis)

**Los parquets del repo contienen markets equivocados.** Inspección directa de `data/parquet/`:

| Asset | Ticks | Market_id único | Question | Rango precio |
|---|---|---|---|---|
| BTC | 15,091 | `0xbb57ccf5...aee89d2` | "Will bitcoin hit $1m before GTA VI?" | min=max=**0.4925**, std=0.0000 |
| ETH | 14,041 | `0xe459d1b5...903d3c8e` | "Will MegaETH perform an airdrop by June 30?" | 0.1465 – 0.1830, std=0.006 |

- **BTC**: el único market grabado es un binario longevo (años vista) con precio **plano**. Z-score = `(price - mean) / std` = `0/0` → la condición `z_score < -2.5` nunca se cumple → 0 trades. **No es problema de la estrategia**: es matemáticamente imposible operar.
- **ETH**: market longevo en tendencia bajista lenta. MR entra "comprando dips" pero el precio sigue cayendo → 0% WR. Tampoco es señal: no hay reversión en estos datos.

El `record_live_data.py` capturó **markets longevos** en vez de los markets **M5/M15 de precio cripto** que la estrategia espera. La hipótesis MR ("oscilación rápida en torno a la media en mercados cripto cortos") no se puede testear con estos parquets.

### Conclusión definitiva de R1.2-bis

- **MR no está "rota"**: el código no se ha podido ejercitar con datos representativos.
- **Los parquets actuales son insuficientes** para validar cualquier estrategia M5/M15 cripto.
- **`optimal_params_mr_real.json` actual no es útil** (los parámetros se basan en datos no representativos).
- Esto explica que R1.2 se haya marcado ✅ formalmente sin cumplir umbrales: el sweep corrió, pero sobre datos que no exhiben el patrón que MR busca.

### Bloqueos para R2.1 (actualizado)

| ID | Bloqueo | Estado |
|---|---|---|
| B1 | MR sin edge en parquets reales | 🟡 reformulado: **datos inadecuados**, no estrategia rota |
| B2 | Tooling de validación sintético | ✅ resuelto con `scripts/backtest_real.py` |
| B3 | Recording inactivo 13 días | ❌ pendiente |
| B4 | Discovery captura markets longevos en vez de M5/M15 cripto | ✅ **fix aplicado en `record_live_data.py`** (`_matches_window`) |
| **B5** | ~~Polymarket no tiene markets BTC/ETH M5/M15 abiertos~~ → **falso positivo de auditoría 2026-06-14**: el discovery del script usaba el endpoint equivocado. Endpoint correcto (`/events/keyset?tag=crypto`) expone 54 markets `*-updown-*` activos. | ✅ **resuelto 2026-06-21** (fix + 4 tests no-regresión) |

### B4 — Fix de discovery filter (2026-06-14)

`scripts/record_live_data.py:find_markets_for_asset` antes filtraba **solo por keyword del asset** (`bitcoin/btc`, `ethereum/eth`). Eso aceptaba "Will bitcoin hit $1m before GTA VI?" como candidato BTC y lo grababa durante días.

Fix aplicado: portada la lógica canónica de `MarketService._matches_window` como helper local `_matches_window(raw, window=...)`. Ahora un market es aceptado solo si:
1. Su `slug` contiene `-5m-` o `-15m-`, o
2. Su `question` incluye un rango horario cuya duración cae en `[2,7]` min (M5) o `[12,18]` min (M15).

Los binarios longevos (`question` sin rango temporal, sin slug `-5m-`/`-15m-`) ya no pasan. El script ahora también ordena por `volume24hr` y retorna hasta 5 markets por ventana.

### B5 — No hay markets M5/M15 cripto abiertos en Gamma (2026-06-14)

Tras corregir B4, se hizo audit en vivo contra `gamma-api.polymarket.com`:

- `GET /markets?active=true&closed=false&_limit=500` → **20 markets totales**, solo 1 BTC (longevo).
- `GET /events?tag_id=620` (tag *btc*) → 10 eventos, **todos cerrados** (`closed=true`). Hay events tipo `Bitcoin Up or Down on june-26` y `Ethereum Price - June 26 5PM ET` que sí son M5/M15, pero ya resueltos.
- `GET /events?tag_id=620&active=true&closed=false` → **0 eventos**.
- `GET /events?tag_id=102322&active=true&closed=false` (Ethereum Prices) → **0 eventos**.

**Conclusión (2026-06-14, ahora obsoleta):** la auditoría usó endpoints que no coinciden con el discovery canónico del bot; ver re-check abajo.

### B5 — Re-check 2026-06-21: falso positivo

Los endpoints usados en la auditoría B5 inicial no son el discovery real del bot. `PolymarketHTTPClient.get_active_markets` consume **`GET /events/keyset?tag=crypto&active=true&closed=false&limit=100&order=volume24hr&sort=desc`** desde R1.x (commit del cliente HTTP). El bloqueo se debía a que `scripts/record_live_data.py` se quedó en el endpoint anterior (`GET /markets?_limit=500`) tras el merge de market_filters.

Verificación contra Polymarket producción el 2026-06-21:

| Endpoint | Markets devueltos | Updown |
|---|---|---|
| `GET /markets?active=true&closed=false&_limit=500` (el que usaba el script) | 20 | **0** |
| `GET /events/keyset?tag=crypto&active=true&closed=false&limit=100&order=volume24hr&sort=desc` (canónico) | 100 events / **641 markets** | **54** |

Ejemplos (slugs reales):
- `btc-updown-5m-1782077100`, `btc-updown-5m-1782084600`, `btc-updown-15m-1782100800`
- `eth-updown-5m-1782076800`, `eth-updown-15m-1782100800`
- `bnb-updown-5m-...`, `doge-updown-5m-...`, `xrp-updown-5m-...`

**Fix aplicado:**
- `scripts/record_live_data.py` — nuevo helper `_fetch_crypto_events_paginated()` que replica el patrón del HTTP client (keyset pagination, `PolymarketAdapter.parse_rest_market`). `find_markets_for_asset` y `find_live_crypto_markets` ambos lo usan.
- Conserva `endDate`/`conditionId` camelCase (varios helpers downstream los leen así).
- Tests refactorizados al shape `{"events":[...], "next_cursor": ...}`.
- Nuevo `TestFindMarketsForAsset` con 4 casos: updown vs longevidad (rechaza GTA-VI), paginación `next_cursor`, respuesta vacía, top-volume per window.

**Smoke live (paper, sin .env):**
- `find_markets_for_asset("BTC")` → 10 markets, todos cripto M5/M15/H4 reales.
- `find_live_crypto_markets("BTC")` → 56 markets en cola.
- `find_live_crypto_markets("ETH")` → 45 markets en cola.

**Lecciones de auditoría:**
- Antes de declarar un bloqueo "externo", auditar **el endpoint real que usa el bot en producción**, no un endpoint adyacente.
- El test de discovery debe asegurar paridad con `PolymarketHTTPClient.get_active_markets`. El nuevo `TestFindMarketsForAsset::test_finds_updown_5m_rejects_longevity_market` cubre exactamente esa regresión.

**Implicaciones:**
- B3 (recording) y R1.2-ter (re-optimizar MR full) ya no están bloqueados.
- R2.1 → objetivo #3 desbloqueado para validación operativa.

### Próximas acciones (re-priorizadas)

1. **B4 (URGENTE) — Auditar `MarketDiscoveryService`/`record_live_data.py`.** Verificar que el filtro de mercados sigue los criterios del skill `polymarket-market-discovery` (BTC/ETH × M5/M15 con `question` matching "5 minute" / "15 minute" e `is_active=True`). Los markets capturados no encajan.
2. **B3 — Reactivar recording con los filtros corregidos.** Acumular 168h+ de markets M5/M15 reales antes de cualquier optimización.
3. **R1.2-ter — Re-correr `optimize_mr.py` FULL con los nuevos parquets.** No tiene sentido hasta tener B3+B4 resueltos.
4. **Documentar las 4 preguntas del skill `strategy-validation-protocol`** para MR antes de reactivar.

---

## 🟡 R1.7 — Auditoría CLOB V2 SDK (Junio 2026)

**Fecha:** 2026-06-14
**Alcance:** SDK `py-clob-client-v2` 1.0.1, propagación de `signature_type`, cache Redis de fees dinámicos, integración fee-aware en `SlippageEngine`.

### Hallazgos cerrados

| # | Hallazgo previo | Severidad | Resolución |
|---|---|---|---|
| 1 | `signature_type` no se pasaba al SDK → dependencia del default interno (cambiable entre versiones) | 🟡 ALTA | `KeyManager.signature_type` validado (`0\|1\|2\|3`, default `1`) y propagado explícito en `clob_client.py:101`. |
| 2 | `get_clob_market_info` se llamaba on-demand sin cache (riesgo de saturar el endpoint y de tomar decisiones con info stale) | 🟡 ALTA | `RedisClient.set/get_clob_market_info` con TTL 300s + `PolymarketCLOBClient.get_market_info_cached`. Cache opt-in: cliente sin Redis degrada al SDK directo. |
| 3 | `SlippageEngine` ignoraba los fees dinámicos V2 → simulación irreal | 🟡 ALTA | `taker_fee_bps_from_market_info(info)` + arg `taker_fee_bps` en `estimate()` (default `0.0`, no regresión). Diagnóstico `taker_fee_bps`/`taker_fee_price` en `SlippageEstimate`. |
| 4 | Documentación CLOB V2 incompleta (`signature_type`, cache TTL, beta SDK) | 🟢 BAJA | `RECORRIDO_ACTUAL.md` sección "Polymarket CLOB V2 — SDK & Endpoints" y `RUTA_IMPLEMENTACION.md § R1.7` cerrado. |

### Verificaciones

- ✅ `pytest tests/unit/test_key_manager.py tests/unit/test_clob_client.py tests/unit/test_slippage_engine.py -x` → 91/91.
- ✅ `pytest -x -q` → 1,157/1,157 (sin regresiones).
- ✅ `ruff check` sobre archivos modificados → 0 hallazgos.
- ✅ `bandit -r src/...` → 0 HIGH/MEDIUM.

### Fuera de alcance (defer)

- Integración full de `taker_fee_bps` en `paper_handler.py` / `real_handler.py` / `smart_router.py` (la plumbería queda lista; cableado por call-site en una iteración posterior).
- Evaluación del SDK beta `polymarket-client` unificado (R4.5).
- Métricas Prometheus para edad del cache de fees (R1.5).

---

## 🔴 R1.4 — Auditoría de Seguridad (Junio 2026)

**Fecha:** 2026-06-07  
**Alcance:** SAST (bandit), SCA (pip-audit), secrets scan, .env hygiene  

### 1. Bandit — Static Analysis Security Testing

```
✅ HIGH:   0 findings
✅ MEDIUM: 0 findings
🟡 LOW:   23 findings (reviewed, no action needed)
```

**Detalle LOW:** 23 issues de baja severidad (assert usages, subprocess sin shell=True,
random no criptográfico en tests). Ninguno es explotable en producción.
- `assert` en tests → sin riesgo
- `subprocess` sin `shell=True` → uso correcto
- `random` en `optimize_bat.py` y `optimize_mr.py` → solo genera datos sintéticos
- `try-except-pass` → intencional en graceful degradation paths

**Conclusión:** ✅ Código limpio — 0 HIGH, 0 MEDIUM.

### 2. pip-audit — Software Composition Analysis

```
⚠️  53 vulnerabilidades en 13 paquetes
```

| Paquete | Vulns | Tipo | Riesgo Producción |
|---------|-------|------|-------------------|
| aiohttp 3.9.5 | 22 | HTTP server | 🟡 MEDIO — usado en prod, actualizar |
| jupyter-server 2.17.0 | 7 | Dev tool | 🟢 BAJO — solo desarrollo |
| gitpython 3.1.46 | 4 | Dev tool | 🟢 BAJO — solo desarrollo |
| mistune 3.2.0 | 4 | Markdown parser | 🟢 BAJO — dependencia de nbconvert |
| starlette 0.37.2 | 4 | ASGI framework | 🟡 MEDIO — usado en prod (FastAPI), actualizar |
| jupyterlab 4.5.5 | 3 | Dev tool | 🟢 BAJO — solo desarrollo |
| nbconvert 7.17.0 | 2 | Dev tool | 🟢 BAJO — solo desarrollo |
| urllib3 2.6.3 | 2 | HTTP library | 🟡 MEDIO — usado en prod, actualizar |
| idna 3.11 | 1 | URL parser | 🟢 BAJO — dependencia indirecta |
| pip 26.1.1 | 1 | Package manager | 🟢 BAJO — entorno de build |
| pygments 2.19.2 | 1 | Syntax highlighter | 🟢 BAJO — dependencia indirecta |
| pytest 8.4.2 | 1 | Test framework | 🟢 BAJO — solo desarrollo |
| requests 2.32.5 | 1 | HTTP client | 🟡 MEDIO — usado en prod, actualizar |

**Conclusión:** ⚠️ 53 vulnerabilidades, pero solo 4 paquetes de producción necesitan atención:
- `aiohttp` (22 vulns) → considerar actualizar a 3.10+
- `starlette` (4 vulns) → actualizar a 0.38+
- `urllib3` (2 vulns) → actualizar
- `requests` (1 vuln) → actualizar

El resto (jupyter, pytest, nbconvert, gitpython) son dependencias de desarrollo sin exposición en producción.

### 3. Secrets Scan — Git History

```
✅ No secrets found in git history
```

Verificados los siguientes patrones:
- `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_API_SECRET` → 0 matches
- `TELEGRAM_BOT_TOKEN` con valores reales → 0 matches
- `DATABASE_URL` con credenciales reales → 0 matches
- Claves SSH/PEM/GitHub tokens → 0 matches
- `.env` en git history → 0 commits

**Conclusión:** ✅ Sin secrets expuestos en el historial.

### 4. .env Hygiene

```
✅ .env existe localmente con valores reales
✅ .env.example contiene solo placeholders (CAMBIA_ESTE_VALOR)
✅ .gitignore cubre .env (no se trackea)
✅ 0 archivos .env/.pem/.key trackeados en git
```

### 5. Circuit Breakers & Guards

Verificados en `src/infrastructure/security/`:
- `circuit_breaker.py` — CLOB circuit breaker (5 fallos/60s → bloquea 60s) ✅
- `security_guard.py` — pre-flight checks para órdenes reales ✅
- `rate_limiter.py` — rate limiting en API calls ✅
- `key_manager.py` — manejo seguro de claves privadas ✅
- `audit_log.py` — registro inmutable de operaciones sensibles ✅

### R1.4 — Resumen

| Check | Resultado |
|-------|-----------|
| Bandit HIGH/MEDIUM | ✅ 0 findings |
| pip-audit producción | ⚠️ 4 paquetes a actualizar |
| Secrets en git | ✅ 0 leaks |
| .env hygiene | ✅ Seguro |
| Circuit breakers | ✅ Funcionales |

**Acciones recomendadas:**
1. `pip install --upgrade aiohttp starlette urllib3 requests`
2. Re-ejecutar tests tras actualizar dependencias
3. Nada bloquea el despliegue — riesgo aceptable

---

## P11.1 — Polymarket API Integration Audit (Junio 2026)

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
