# RFC R2.0-redeem-impl — Redeem on-chain via CTF + pUSD thin collateral adapter

> **Estado:** 🟢 APPROVED 2026-06-25 — Decisiones Q1-Q5 confirmadas por operador; pasar a F1 (Build)
> **Fecha:** 2026-06-25
> **Decisiones operativas:** ver §13
> **Autor:** Buffy (orquestador Claude)
> **Predecesor:** R2.0-redeem-audit ✅ cerrado 2026-06-16 (`AUDIT_REPORT.md § R2.0-redeem`)
> **Skill aplicable:** `polymarket-clob-audit`, `paper-vs-real-execution`, `risk-engine-guard`
> **Bloqueante para:** R3.x real trading (cierra ciclo `entry → exit → redeem`)
> **Reglas duras que aplica:** #1 (RiskEngine.evaluate nunca se bypassea), #2 (nunca loguear claves), #3 (3 capas de confirmación REAL), #5 (property tests Hypothesis), #8 (añadir dep justificada)

---

## 0 · TL;DR

Hoy el `PolymarketCLOBClient.redeem_position` falla rápido con `CLOBRedeemNotSupportedError` (R2.0-redeem-audit). Esto es correcto pero **incompleto**: deja al bot sin camino para reclamar ganancias en real.

Este RFC propone cerrar el ciclo con un redeem on-chain que:

1. Llama al **Thin Collateral Adapter** (`0x93070a847efEf7F70739046A929D47a521F5B8ee`) oficial de Polymarket — un wrapper que atómicamente hace `CTF.redeemPositions` + `USDC.e → pUSD wrap`.
2. Ejecuta desde el **`POLY_PROXY`** (`signature_type=1`) del operador vía `execTransaction` estilo Gnosis Safe (las posiciones viven en el proxy, no en la EOA).
3. Espera **64 bloques** (~2 min) de finality antes de marcar `CONFIRMED`.
4. Sustituye nonce por **tx replacement** (mismo nonce, gas bumped 15%) si mempool > 2 min.
5. Persiste `audit_events` con tx hash, gas usado, pUSD recibido, status vista confirmada.

Net: añadir `web3.py>=7.16.0` + 1 archivo nuevo (`ctf_redeemer.py`) + 2 modificaciones quirúrgicas (clob_client, real_handler). Cero cambio de comportamiento en paper trading.

---

## 1 · Contexto y motivación

### 1.1 Estado actual (auditado 2026-06-16)

```
path: src/infrastructure/polymarket/clob_client.py:252
  await self._http.post("/redeem", json={...}, headers={"POLY_ADDRESS": wallet})
```

Este endpoint **no existe en CLOB V2**. La redención es **on-chain** vía el Conditional Tokens Framework. El fix aplicado en R2.0-redeem-audit:

- Lanzar `CLOBRedeemNotSupportedError(NotImplementedError)` desde `redeem_position` (fail-fast, sin 404 silencioso).
- `real_handler._call_with_retry` no reintenta `NotImplementedError`.
- `real_handler.redeem_resolved_position` emite `AuditAction.REAL_REDEEM_FAILED` con `reason="ctf_onchain_required"`.
- Tests: 1,369/1,369 verde.

**Veredicto:** código defensivo correcto, pero el bot **no puede** reclamar ganancias en real. R3.x (R3.1 real trading gradual) está bloqueado hasta que esto se cierre.

### 1.2 Por qué ahora

- B5 (B-recheck 2026-06-21) resuelto: 54 markets `*-updown-*` activos visibles vía `/events/keyset?tag=crypto`.
- R1.2-ter (2026-06-21) confirma datos real-time en mercados cripto M5/M15 → `TradingService._run_market_cycle` ejecuta entry + exit correctamente (Pipeline E2E validado en `smoke_test_pipeline.py`).
- Falta exactamente una pieza: la repatriación del capital tras la resolución de cada market M5/M15.

### 1.3 Fuera de alcance

- ❌ Canarying de R3.1 (eso es R2.2).
- ❌ Nueva estrategia / ML / multi-estrategia (R4+).
- ❌ Migración a SDK unificado `polymarket-client` beta (R4.5 deferred).
- ❌ Multi-chain (solo Polygon 137).

---

## 2 · Decisiones tomadas (NO se reabren en este RFC)

| Decisión | Justificación |
|---|---|
| `signature_type=1` POLY_PROXY | Default CLOB V2, ya validado en R1.7. Posiciones viven en proxy derivado de la EOA. |
| Colateral: **pUSD via thin collateral adapter** | CLOB V2 nativo (abril 2026). Auto-wrap USDC.e → pUSD. Evita swap manual. |
| Llamar al **adapter** (no CTF directo) | Atómico: redeem + unwrap + wrap en una tx. Logs atómicos. |
| Esperar **64 bloques** de finality | Polygon PoS tiene reorgs < 256 bloques. 64 = ~2 min = seguro sin esperar checkpoint 30 min. |
| Tx replacement con gas bumped 15% | Preserva idempotencia del nonce; tasa de reemplazo es estándar en Polygon. |
| `web3.py>=7.16.0` (no `eth-brownie`, no `ape`) | Web3.py AsyncWeb3 estable, MIT, Polygon-compatible, release mensual mayo 2026. |

---

## 3 · Hechos técnicos verificados (no inventados, no extrapolados)

Documentados como tales para auditoría posterior. Cualquier cambio de address ⇒ se reabre el RFC.

| Recurso | Valor | Fuente |
|---|---|---|
| pUSD token (Polygon) | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` | docs.polymarket.com/post-feb-2026-collateral |
| CTF (Conditional Tokens Framework) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` | docs.polymarket.com/trading/ctf/redeem |
| Thin Collateral Adapter / Onramp | `0x93070a847efEf7F70739046A929D47a521F5B8ee` | docs.polymarket.com/trading/ctf/redeem |
| Chain ID | 137 (Polygon Mainnet, PoS) | — |
| Signatura `redeemPositions` | `(address collateralToken, bytes32 parentCollectionId, bytes32 conditionId, uint256[] indexSets)` | CTF ERC-1155 estándar |
| Signature adapter/redeem-tokens | requiere quemado del ERC1155 outcome + unwrap USDC.e → wrap pUSD | inferido del patrón CTF + docs onramp |
| `web3.py` estable actual | 7.16.0 (mayo 2026) | pypi.org/project/web3 |
| Python compatible | 3.11, 3.12, 3.13 | web3py.readthedocs.io |
| Polygon EIP-1559 | soportado nativo desde 2023 | eip-1559 + Polygon docs |

⚠️ **Verificación obligatoria antes de merge:** ejecutar `web3.eth.get_code(0x93070a..., block=latest)` para confirmar que el adapter existe y tiene código deployado. Si cambia, este RFC se reabre.

---

## 4 · Diseño de alto nivel

### 4.1 Pipeline de redeem (3 capas, respeta regla dura #3)

```
   ┌─────────────────────────────────────────────────────────────┐
   │  Capa 1: Risk Engine (ya existente, NO se toca)             │
   │  position.close() ⇒ sell position_amount, output=pUSD       │
   │  ⇒ estado interno del bot en DB: position.closed=True       │
   └──────────────────────────────────┬──────────────────────────┘
                                      │
   ┌──────────────────────────────────▼──────────────────────────┐
   │  Capa 2 (repatriación): SIN PIN por decisión §13.Q4         │
   │  Rationale confirmado 2026-06-25:                           │
   │    - Regla dura #3 aplica a entry/exit (riesgo asimétrico)  │
   │    - Redeem = settlement de posición ya confirmada          │
   │    - Defense-in-depth pasiva (6 capas) documenta en §4.1    │
   │  Override opt-in via `REDEEM_REQUIRE_PIN=true` + umbral    │
   │    `REDEEM_PIN_THRESHOLD_PUSD=50.0` (off por default).      │
   └──────────────────────────────────┬──────────────────────────┘
                                      │
   ┌──────────────────────────────────▼──────────────────────────┐
   │  Capa 3: Idempotencia ANTES de on-chain                    │
   │  redeem_op_id = UUID()  (key determinista por tx hash)      │
   │  ⇒ INSERT INTO redeem_operations (status='PENDING',        │
   │                                   tx_hash=None)             │
   │  ⇒ class CTFRedeemer.redeem(...) → construye + firma +      │
   │    espera 64 bloques                                          │
   │  ⇒ UPDATE redeem_operations SET tx_hash=… , status='SUBMITTED'│
   │  ⇒ al confirmar 64 bloques: status='CONFIRMED'              │
   │  ⇒ emite audit events CTF_REDEEM_TX_SUBMITTED/MINED/CONFIRMED│
   └─────────────────────────────────────────────────────────────┘
```

### 4.2 Componentes nuevos / modificados

#### Componente nuevo: `src/infrastructure/polymarket/ctf_redeemer.py` (~280 líneas)

```python
"""
CTFRedeemer — Wrapper async sobre el Thin Collateral Adapter de Polymarket.

Firma: ejecuta redeem on-chain desde el POLY_PROXY (signature_type=1)
usando Gnosis Safe execTransaction pattern. Entrega pUSD atómicamente.

Decisiones DC1:
- Llama al Adapter Onramp (0x93070a...) — no al CTF directo.
- indexSets = [1] si outcome ganador conocido vía Data API; [1, 2] si no.
- EIP-1559 con max_fee_per_gas derivado de eth_estimateGas * 1.2.
- Hash de la transacción se usa como idempotency key secundaria.
"""

class CTFRedeemer:
    def __init__(
        self,
        web3: AsyncWeb3,
        adapter_address: ChecksumAddress,
        pusd_address: ChecksumAddress,
        operator_address: ChecksumAddress,
        private_key: str,  # Solo firma; nunca se loguea
    ): ...

    async def redeem(
        self,
        condition_id: bytes32,
        shares_yes: int,       # 0 si solo tenemos NO
        shares_no: int,        # 0 si solo tenemos YES
    ) -> RedeemReceipt: ...

    async def wait_for_finality(
        self,
        tx_hash: HexStr,
        confirmations: int = 64,
        timeout_seconds: int = 600,
    ) -> FinalityStatus: ...

    async def replace_tx_if_stuck(
        self,
        original_tx: dict,
        bump_pct: float = 0.15,
    ) -> HexStr:
        """Si mempool > 2 min, rebuild con mismo nonce + priority fee +15%."""
```

#### Componente modificado: `src/infrastructure/polymarket/clob_client.py` (~40 líneas diff)

- Mantener `redeem_position` como **wrapper de compatibilidad** que delega al `CTFRedeemer` inyectado.
- Quitar `raise CLOBRedeemNotSupportedError` solo si el CTFRedeemer está disponible. Si no → fallback NotImplementedError (modo paper / sin python -m web3 instalado).

```python
async def redeem_position(self, token_id, market_id) -> dict:
    if self._ctf_redeemer is None:
        raise CLOBRedeemNotSupportedError(...)  # Modo paper/legacy
    if not await self._has_resolved_market(market_id):
        raise CTFMarketNotResolvedError(...)
    receipt = await self._ctf_redeemer.redeem(
        condition_id=bytes32.fromhex(market_id),
        ...
    )
    return {"redeemed_amount": receipt.pusd_received, "tx_hash": receipt.tx_hash}
```

#### Componente modificado: `src/execution/real_handler.py` (~120 líneas diff en `redeem_resolved_position`)

- Sustituir el `try/except CLOBRedeemNotSupportedError` por flujo happy-path.
- Añadir pre-flight: MATIC balance ≥ 0.1 MATIC antes de enviar (abort + alerta Telegram si no).
- Llamada a `CTFRedeemer.wait_for_finality(64)` con timeout 10 min.
- Al confirmar: actualizar Position + audit log chain.
- Pre-flight reconciliación al arrancar: ∀(position in DB|status='redeem_pending') ⇒ consultar Data API / clob onchain status; reconciliar.

#### Componente modificado: `src/infrastructure/security/audit_log.py` (enum extension)

```python
class AuditAction(str, Enum):
    # ...existentes...
    CTF_REDEEM_TX_SUBMITTED = "ctf_redeem_tx_submitted"   # tx en mempool
    CTF_REDEEM_TX_MINED     = "ctf_redeem_tx_mined"       # tx en bloque, <64 conf
    CTF_REDEEM_TX_CONFIRMED = "ctf_redeem_tx_confirmed"   # ≥64 conf, pUSD liquidado
    CTF_REDEEM_FAILED       = "ctf_redeem_failed"
    CTF_REDEEM_REPLACED     = "ctf_redeem_replaced"       # tx-bump por mempool stuck
    CTF_REDEEM_RECONCILED   = "ctf_redeem_reconciled"     # arranque reconcilia estado
```

#### Componente modificado: `src/infrastructure/observability/metrics.py` (nuevas métricas)

```python
REDEEM_GAS_USED            = Histogram("redeem_gas_used",  ...)         # per-tx
REDEEM_PUSD_RECEIVED       = Counter("redeem_pusd_received",  ...)      # total pUSD repatriado
REDEEM_TX_MINING_SECONDS   = Histogram("redeem_tx_mining_seconds", ...) # submit→mined
REDEEM_TX_FINALITY_SECONDS = Histogram("redeem_tx_finality_seconds", ...)
REDEEM_FAILURES_REASON     = Counter("redeem_failures_reason", ["reason"])  # ctf_onchain_required, gas_empty, ...
REDEEM_REPLACEMENTS        = Counter("redeem_tx_replacements", ...)
REDEEM_PROXY_MATIC_BALANCE = Gauge("redeem_proxy_matic_balance", ["wallet"])
```

#### Componente modificado: `src/infrastructure/security/key_manager.py` (+1 dep)

```python
ENV_POLYGON_RPC_URL    = "POLYGON_RPC_URL"     # Alchemy / Infura / público
ENV_REDEEM_DRY_RUN     = "REDEEM_DRY_RUN"      # default "false". "true" ⇒ eth_call sin broadcast
```

#### Componente modificado: `requirements.txt` y `pyproject.toml` (+1 dep)

```
web3==7.16.0
```

Justificación detallada en §7.

---

## 5 · POLY_PROXY = 1 — Modelo de interacción con Gnosis Safe

CONCEPTO: `signature_type=1` significa que las posiciones ERC1155 viven en el **POLY_PROXY** del operador (un contrato tipo Gnosis Safe desplegado y controlado por el EOA del operador). El EOA firma `execTransaction` en el proxy para que el proxy sea el `msg.sender` que interactúa con el collateral adapter.

```solidity
// caller: operator EOA (signature_type=1)
// recipient on-chain: POLY_PROXY (donde viven los tokens ERC1155)
// execution: POLY_PROXY.execTransaction(
//              to:    0x93070a847efEf7F70739046A929D47a521F5B8ee,
//              value: 0,
//              data:   abi.encodeWithSelector(
//                ICollateralOnramp.redeemAndWrap.selector,
//                conditionId, indexSets
//              ),
//              operation: 0,  // CALL
//              safeTxGas, baseGas, gasPrice, gasToken, refundReceiver,
//              signature: <EOA signature over EIP-712 SafeTx>
//            );
```

**Implementación práctica en `CTFRedeemer`:**

1. El operador provee la private key del EOA en `POLYMARKET_PRIVATE_KEY`.
2. `CTFRedeemer.__init__` deriva la dirección del POLY_PROXY desde el EOA (lookup on-chain: `PolymarketProxyFactory.getProxy(address(EOA))` o derivado de API `auth/l1` que ya conocemos).
3. Antes de cada redeem: `eth_call` simula la tx desde el proxy. Si revierte → abort con WHY.
4. Si ok, `web3.eth.send_transaction` con `from=EOA, to=POLY_PROXY, data=execTransaction_calldata`.
5. La EOA paga MATIC; el POLY_PROXY es quien "actúa" on-chain.
6. Resultado: pUSD se acredita en el POLY_PROXY (no en la EOA). El bot lee el balance del proxy vía `web3.eth.get_balance(proxy, 'latest')` para el balance del colateral.

**Caso edge:** Si Polymarket migra el POLY_PROXY a un patrón no-Gnosis (por ejemplo una Smart Wallet custom), el módulo GnosisExec desaparece y se sustituye; **RFC documentado en §9.1**.

---

## 6 · indexSets, gas, idempotencia, reorg

### 6.1 Cálculo de `indexSets`

```
spec: https://docs.polymarket.com/trading/ctf/redeem
method signature:
  redeemPositions(
    address  collateralToken,
    bytes32  parentCollectionId,
    bytes32  conditionId,
    uint256[] indexSets
  )
```

**Reglas adoptadas en `CTFRedeemer.redeem()`:**

| Caso | indexSets | Fuente |
|---|---|---|
| Tenemos solo YES | `[1]` | inferido de nuestra posición (`shares_yes > 0, shares_no == 0`) |
| Tenemos solo NO | `[2]` | idem |
| Tenemos ambos lados | `[1, 2]` | hedging residual |
| Bolsa de YES y NO se cancelan antes de redeem | `[1, 2]` y solo se redime el ganador | nota: más gas, pero garantiza atomicidad |

`parentCollectionId = bytes32(0)` (estándar para markets de Polymarket sin nesting).
`collateralToken = 0xC011a7E12a19f7B1f670d46F03B03B03f3342E82DFB` (pUSD, hardcoded).

**Property tests Hypothesis** (regla dura #5): toda combinación (`shares_yes ∈ [0, N]`, `shares_no ∈ [0, N]`) produce un `indexSets` válido que satisface la invariante:

```
sum(outcome_value_received) == shares_yes (if YES wins) | shares_no (if NO wins)
```

### 6.2 Gas estimation (EIP-1559)

```
chain_id: 137 (Polygon, soporta EIP-1559 nativo desde 2023)

estimación = chain.AsyncWeb3.eth.estimate_gas(call_params, 'latest') * 1.20
   ⇒ margen de seguridad 20% por variabilidad de nodos Polygon
   ⇒ si eth_estimateGas falla → fallback gas_limit hardcoded = 350_000 (más que suficiente para CTF.redeemPositions)

max_fee_per_gas = chain.eth.get_block('latest').base_fee_per_gas * 2  // cubre 1 base-fee spike
max_priority_fee_per_gas = chain.eth.max_priority_fee              // sugiere web3.py 7.x
```

NO usamos Polygon Gas Station API externa → dependencia innecesaria. `eth_estimateGas` + `eth_maxPriorityFeePerGas` de `AsyncWeb3` es suficiente.

### 6.3 Idempotencia y tx replacement

**Pre-broadcast:**

```python
redeem_op_id = uuid.uuid4()
# 1) INSERT INTO redeem_operations en DB con (op_id, status='PENDING', tx_hash=None)
# 2) Solo si ya existe otra 'PENDING' para este (conditionId, outcome) → raise DuplicateRedeemError
```

**Mempool stuck (>2 min sin minar):**

```
replacement_tx:
  same nonce
  same to, data (=execTransaction calldata)
  max_fee_per_gas bumped +15%
  max_priority_fee_per_gas bumped +15%
  emit AuditAction.CTF_REDEEM_REPLACED con old_tx_hash + new_tx_hash
```

(Esto preserva idempotencia criptográfica vía el nonce. Si la tx original minara a la vez, el reemplazo es no-op on-chain pero el tracking DB se reconcilia por tx_hash actual.)

### 6.4 Finality y reorgs

```
espera: 64 bloques (~2 min @ Polygon 2s/block)
   → emit AuditAction.CTF_REDEEM_TX_MINED en bloque 1 (websockets newHeads subscription)
   → emit AuditAction.CTF_REDEEM_TX_CONFIRMED en bloque 64
timeout: 600s (10 min) → si no confirma: emit CTF_REDEEM_FAILED reason=finality_timeout
                          y requiere intervención manual del operador
```

Polygon tiene finality práctica en checkpoint (~30 min en el bridge L1), pero nuestro riesgo de doble-Redeem es bajo — solo perderíamos dinero si el redeem no se ejecuta, no si se ejecuta dos veces (idempotencia del proxy ya cubre eso).

### 6.5 Pre-flight y reconciliación de arranque

Pre-flight **antes** de cada redeem (en `redeem_resolved_position`):

```python
matic_balance = await w3.eth.get_balance(POLY_PROXY, 'latest')
if matic_balance < 0.1 * 10**18:
    emit_audit(action=CTF_REDEEM_FAILED, reason="proxy_matic_empty")
    alert_telegram("⚠️ Proxy MATIC < 0.1 — fondear antes de próximo redeem")
    raise InsufficientGasError(...)
```

Reconciliación al arrancar (`bootstrap.lifecycle`):

```python
pending = await db.fetch("SELECT * FROM redeem_operations WHERE status='SUBMITTED'")
for op in pending:
    receipt = await w3.eth.get_transaction_receipt(op.tx_hash)
    if receipt and receipt.blockNumber + 64 <= await w3.eth.block_number:
        await db.update(op.id, status='CONFIRMED', confirmed_at=...)
        emit_audit(action=CTF_REDEEM_RECONCILED, details={...})
```

---

## 7 · Dependencias: justificación de `web3.py>=7.16.0`

Regla dura #8: "No introducir dependencias sin justificación + verificación async + check de mantenimiento". Esta sección cumple.

| Criterio | web3.py 7.16.0 | Veredicto |
|---|---|---|
| Mantenedor | ApeWorX + comunidad EF ecosystem grants | ✅ Estable |
| Última versión | v7.16.0 (mayo 2026) | ✅ Reciente |
| Cadencia de release | Mensual (v7.14.0 oct-2025, v7.16.0 may-2026) | ✅ Activo |
| Async support (`AsyncWeb3`) | Estable, recomendado para trading bots I/O-bound | ✅ Apto |
| Polygon compatibility | EIP-1559 nativo, sin middleware PoA (Polygon es PoS) | ✅ OK |
| Gas estimation | `eth_estimateGas`, `eth_maxPriorityFeePerGas` | ✅ Necesario y suficiente |
| Python | 3.11, 3.12, 3.13 | ✅ Compatible con pyproject actual |
| Licencia | MIT | ✅ OK comercial |
| CVE connues | Ninguna crítica en 2026 | ✅ Limpio (pip-audit) |
| Tamaño / latencia import | ~30 MB; importe inicial < 200ms warm | ✅ Aceptable |
| Alternativas | `ape` (sobre web3.py, +abstracción), `web3-ethereum-defi` (Trading Strategy fork, optimiza DeFi) | ❌ ape añade bloat; ❌ web3-ethereum-defi fork específica para el caso |

**Veredicto:** introducir `web3==7.16.0` en `requirements.txt` y `[project.dependencies]` de pyproject.toml. Sin extras opcionales (no `web3[test]`, no `eth-tester`).

📌 `web3` arrastra transitive `eth-abi`, `eth-account`, `eth-utils`, `eth-typing`, `toolz`, `aiohttp` (ya presente), `hexbytes`. Auditar con `pip-audit` post-install.

---

## 8 · Plan de tests

### 8.1 Unit (`tests/unit/test_ctf_redeemer.py`)

```
~30 tests:

TestComputeIndexSets (Hypothesis):
  - ∀ shares_yes ∈ [0..10^6], shares_no ∈ [0..10^6]:
    indexSets ∈ {[1], [2], [1,2]}
    suma invariante correcta
  - edge cases: ambas zero → InvalidPositionError
                solo NO y solo YES → sets unitarios

TestBuildRedeemCalldata:
  - encoding correcto: redeemPositions(collateralToken, 0x0, conditionId, indexSets)
  - abi.encodeWithSelector de ICollateralOnramp.redeemAndWrap

TestGasEstimation:
  - eth_estimateGas resultado * 1.2 guard → max_fee_per_gas correcto
  - fallback 350_000 si estimation throws
  - EIP-1559 max_fee >= 2 * base_fee (cubre 1 spike)

TestReplaceTxIfStuck (Hypothesis):
  - mismo nonce, gas bumped +15%, mismo data → identity calldata except gas fields
  - retorna tx_hash nuevo

TestWaitForFinality:
  - mock de AsyncWeb3: 64 confirmación ⇒ status='CONFIRMED'
  - <64 ⇒ status='MINED'
  - timeout 600s ⇒ status='TIMEOUT'+ audit CTF_REDEEM_FAILED
  - reorg detectada (reorg_depth>0) ⇒ status='REORG_CONFIRMED' + Counter metric

TestPreflightMaticBalance:
  - MATIC < 0.1 → InsufficientGasError + log alert
  - MATIC >= 0.1 → proceed
```

### 8.2 Property (`tests/property/test_redeem_invariants.py`)

```
TestRedeemValueConservation:
  propiedad: shares_yes * winning_outcome_indicator + shares_no * (1-winning_outcome_indicator)
             == pusd_received (con tolerancia ±gas margin %)
  
  Hypothesis: 50 ejemplos random, market resuelto en Data API mock,
               slippage gas como variable aleatoria.
```

Regla dura #5 (cambios en src/infrastructure/polymarket/ requieren property tests).

### 8.3 Integration (`tests/integration/test_redeem_onchain.py`)

Opciones, en orden de preferencia:

1. **Anvil / Hardhat fork Polygon block N** (Local: `anvil --fork-url $POLYGON_RPC --fork-block-number N`)
   - Pro: red real, contratos reales en N, sin gastar gas.
   - Contra: requiere `anvil` instalado en CI, otros tests ya lo usan → ver `tests/chaos/`.
2. **MockAsyncWeb3** — 90% simulaciones con `unittest.mock.AsyncMock`. Más rápido pero menos fidelidad.
3. **Pytest con cuenta de devnet de Polymarket** — descartado: requiere BUILDER_CODE real + capital de prueba.

**Decisión:** empezar con mocks (rápido, auditable) + integration con Anvil fork como smoke en CI una vez mocks estables.

### 8.4 E2E (`scripts/redeem_dry_run.py` — script nuevo, opcional)

```
usage:  python scripts/redeem_dry_run.py --condition-id 0x... --shares-yes 100 --shares-no 0

efecto:
  1. Verifica POLYGON_RPC_URL + MATIC balance del proxy
  2. eth_call → simula redeem desde POLY_PROXY
  3. Imprime tx estimada: gas, fee, recipient, expected pUSD out
  4. NO envía tx si --dry-run (default)
  5. --live envía + monitoriza 64 bloques + reporta confirmación

salida: JSON con dry_run_result | live_result
exit codes: 0 OK | 1 pre-flight fail | 2 simulation revert | 3 finality timeout
auditoría: cada ejecución registra AUDIT entry con mode=dry_run|live
```

Sirve de smoke test en staging antes de delegar a `redeem_resolved_position` real.

---

## 9 · Riesgos top-5 + mitigación

### R1. Adapter Onramp pausado / actualizado

**Probabilidad:** 🟡 (mediana — Polygon DeFi evoluciona rápido)
**Impacto:** 🔴 redeem falla, capital atrapado en el proxy
**Mitigación:**
- Fallback programado en `CTFRedeemer.redeem()`: si `redeemAndWrap` revierte, intentar `ConditionalTokens.redeemPositions` directo → entrega USDC.e (no pUSD); emit warning Telegram "swap manual requerido".
- Health check diario: `web3.eth.get_code(adapter_address)` ≠ '0x'. Si vuelve '0x' → alerta crítica.

### R2. MATIC insuficiente en `POLY_PROXY`

**Probabilidad:** 🟢 (operador controla esto)
**Impacto:** 🟡 redeem skip + alerta Telegram, no se pierde capital
**Mitigación:**
- Pre-flight check (gas balance ≥ 0.1 MATIC).
- Gauge `REDEEM_PROXY_MATIC_BALANCE` Prometheus. Alerta en `monitoring/alerts.yml` si <0.5 MATIC.
- Operator runbook: `Fondear MATIC al POLY_PROXY antes de canary`.

### R3. Race condition: dos redeems concurrentes al mismo nonce

**Probabilidad:** 🔴 (probable si varios markets resuelven en mismo bloque)
**Impacto:** 🔴 tx revertida, redeem parcial
**Mitigación:**
- `asyncio.Lock()` estricto en `CTFRedeemer._nonce_lock`.
- Nonce manager: `_next_nonce` se lee ANTES de armar la tx; se decrementa solo en tx SUBMITTED confirmada en mempool (no en dry-run).
- En producción, los markets resuelven en tiempos distintos (1 per block); race en práctica es baja, pero el lock es defensivo.

### R4. Chain reorg profundo (> 64 bloques)

**Probabilidad:** 🟢 (raro en Polygon PoS ya estabilizado)
**Impacto:** 🟡 pUSD acreditado pero audit log dice "PENDING" hasta próxima reconciliación
**Mitigación:**
- Reconciliación al arrancar (sección 6.5).
- Audit log entry `CTF_REDEEM_RECONCILED` siempre que el estado se materialice fuera del flujo normal.

### R5. Private key leak en logs / fixtures

**Probabilidad:** 🟡 (siempre presente sin guardrails)
**Impacto:** 🔴 CRÍTICO — pérdida de wallet
**Mitigación:**
- Regla dura #2 ya cubre con helpers `_mask_*` en `key_manager.py`.
- Test explícito: `TestCTFRedeemer.test_never_logs_private_key` con `caplog` + `_mask_private_key`.

---

## 10 · Plan de despliegue por fases (no avanzar hasta verde)

| Fase | Acción | Criterio de pase |
|---|---|---|
| **F0 — RFC** | Este documento aprobado por usuario | ✅ |
| **F1 — Build** | Crear `ctf_redeemer.py`, modificar clob_client, real_handler, audit_log, metrics, key_manager; añadir `web3` a deps | `pytest tests/unit/test_ctf_redeemer.py tests/unit/test_real_handler.py` ≥ 100% verde. `pytest tests/property/test_redeem_invariants.py` ≥ 50 examples verde. `ruff` 0 hallazgos. |
| **F2 — Integration** | Run `tests/integration/test_redeem_onchain.py` con Anvil fork Polygon | Anvil fork deploys, redeem de prueba desde POS local → tx mined → 64 conf → pUSD balance del proxy aumenta. |
| **F3 — Dry-run** | `python scripts/redeem_dry_run.py --dry-run` con un conditionId real pero SIN broadcast | eth_call exitoso, output esperado, audit entry registrado, exit 2 (simulación revertida) o 0 (todas las condiciones pasan). |
| **F4 — Canary redeem** | Redeem real de un solo market con shares pequeñas (10 pUSD), en TRADING_MODE=canary, paper capital | tx mined, 64 conf, pUSD recibido ∈ [expected ±gas], audit log completo. |
| **F5 — Real redeem** | Tras ≥5 redeems exitosos en canary + 0 fallos | `TRADING_MODE=real` permite redeem. |

**Gating rule:** ningún paso avanza al siguiente con fallos pendientes. R2.0-redeem-impl NO se considera cerrado hasta F5 verde (≥5 redeems reales exitosos).

---

## 11 · Checklist de exit pre-merge

```
Pre-merge obligatorio (skill polymarket-clob-audit):

Configuración:
  [ ] POLYMARKET_PRIVATE_KEY, *_API_*, *_BUILDER_CODE nunca en logs
  [ ] POLYGON_RPC_URL + REDEEM_DRY_RUN en .env.example
  [ ] _mask_private_key aplicado en cualquier log path

Initialización:
  [ ] AsyncWeb3 inicializado con chain_id=137 explícito
  [ ] Adapter address (0x93070a...) inyectado desde settings (validado on-boot)
  [ ] POLY_PROXY address derivada correctamente del EOA

Redención:
  [ ] indexSets siempre derivado de shares_yes/shares_no antes de armar tx
  [ ] Gas EIP-1559 con eth_estimateGas * 1.20
  [ ] Pre-flight MATIC ≥ 0.1 abort + alerta si no

Idempotencia:
  [ ] redeem_op_id (UUID) generado ANTES de tx
  [ ] INSERT INTO redeem_operations status=PENDING antes de send_transaction
  [ ] DuplicateRedeemError si ya existe PENDING para misma (conditionId, outcome)
  [ ] Tx replacement solo en mempool stuck >2 min con +15% gas, mismo nonce

Finality:
  [ ] Espera 64 bloques antes de CTF_REDEEM_TX_CONFIRMED
  [ ] Timeout 600s después del cual CTF_REDEEM_FAILED reason=finality_timeout
  [ ] Reconciliación al arrancar (bootsrap.lifecycle) para redeem_operations SUBMITTED

Auditoría + Observabilidad (skills cfc + paper-real):
  [ ] AuditAction.CTF_REDEEM_TX_SUBMITTED, _MINED, _CONFIRMED, _FAILED, _REPLACED, _RECONCILED
  [ ] Metrics: REDEEM_GAS_USED, REDEEM_PUSD_RECEIVED, REDEEM_TX_MINING_SECONDS,
             REDEEM_TX_FINALITY_SECONDS, REDEEM_FAILURES_REASON, REDEEM_REPLACEMENTS,
             REDEEM_PROXY_MATIC_BALANCE

Tests:
  [ ] tests/unit/test_ctf_redeemer.py: ≥30 unit
  [ ] tests/property/test_redeem_invariants.py: ≥1 invariant con Hypothesis
  [ ] tests/integration/test_redeem_onchain.py: Anvil forkPolygon smoke
  [ ] tests/unit/test_real_handler.py:redeem_resolved_position actualizado (no fail-fast)
  [ ] tests/unit/test_clob_client.py:redeem_position ya no lanza NotImplementedError cuando
                                  se inyecta CTFRedeemer
  [ ] tests/unit/test_key_manager.py:POLYGON_RPC_URL añadido

Docs:
  [ ] RUTA_IMPLEMENTACION.md § R2.0-redeem-impl actualizado con F0-F5 statuses
  [ ] RECORRIDO_ACTUAL.md sección "LO QUE FALTA" R2.0-redeem-impl cambia de 🔴 a 🟡
  [ ] AUDIT_REPORT.md § R2.0-redeem-impl con fecha y resultado de F1-F5
  [ ] este RFC movido a docs_historicos/ cuando se implemente (referencia histórica)

Lint / SAST / SCA:
  [ ] ruff check src/ tests/ → 0 hallazgos
  [ ] mypy src/infrastructure/polymarket/ctf_redeemer.py → 0 errores
  [ ] bandit -r src/infrastructure/polymarket/ctf_redeemer.py -c .bandit → 0 HIGH/MEDIUM
  [ ] pip-audit → 0 vulnerabilidades nuevas
```

---

## 12 · Trabajo futuro (deferred)

Lo que NO entra en este RFC:

- Adapter adapter adapter (más wrapping): si Polymarket introduce un nuevo adapter de próxima generación, este RFC se reabre. Hoy el Onramp (0x93070a...) es oficial.
- Multi-collateral (USDC nativo en Solana, etc): requiere RFC separado multi-chain.
- Batched redeem de N markets en 1 tx: posible pero conviene atomicidad per-market por ahora.
- ZK proof of redeem (si Polymarket migra a prueba ZK): requiere re-diseño.
- SDK `polymarket-client` beta (evalúa `redeem` como método nativo): R4.5 deferred.

---

## 13 · Decisiones confirmadas 2026-06-25

Aprobadas por el operador tras revisión de F0 (RFC). Estas decisiones son **inamovibles** salvo RFC nuevo. Cada decisión referencia las secciones del documento que ajusta.

| # | Decisión | Implementación concreta | Ajusta en RFC |
|---|---|---|---|
| Q1 | **MATIC funding — modelo híbrido** | `scripts/fund_proxy_matic.py` arma la tx (build calldata, calcula nonce + gas EIP-1559, dry-run por defecto), imprime para inspección; el **broadcast se hace manual** desde MetaMask. El script registra `CTF_REDEEM_MATIC_FUNDED` con `mode=dry_run` o `mode=manual_reconciled` después de detectar la tx en `eth_getTransactionByHash` polleando 30s. Auditoría robusta sin añadir dependencias de firma adicionales. | §4.1, §10.F1.5 |
| Q2 | **`REDEEM_DRY_RUN` split automático** | Default computado en `bootstrap.lifecycle` desde `DEPLOY_ENV`: `true` ∈ {`staging`, `canary`, `paper`}; `false` ⇔ `production`. Override manual via `REDEEM_DRY_RUN` env si el operador fuerza el otro modo (con audit entry). Sin override explícito → split automático. | §6, §10.F2, §11 |
| Q3 | **Finality 64 bloques** | Confirmado. Constante `CTFRedeemer.DEFAULT_CONFIRMATIONS = 64` (~2 min @ Polygon 2s/block). Configurable via `REDEEM_CONFIRMATIONS` env (no recomendado; documentado como opt-in para casos de incident-response). | §6.4, §11 |
| Q4 | **Sin PIN en redeem (`REDEEM_REQUIRE_PIN=false`)** | `redeem_resolved_position` NO invoca handler Telegram. Documentamos esta excepción a regla dura #3 explícitamente: la regla #3 aplica a **creación/cierre de posición** (riesgo asimétrico), no a **repatriación de resultado** (settlement de operación ya confirmada). Defense-in-depth vía 6 capas pasivas documentadas en §4.1. Activación opcional via `REDEEM_REQUIRE_PIN=true` con umbral `REDEEM_PIN_THRESHOLD_PUSD=50.0` — **off por default**. | §4.1, §11 |
| Q5 | **Tests: mocks obligatorios + Anvil `@pytest.mark.slow`** | CI por defecto corre `pytest -m "not slow"` (~1,373 tests actuales + nuevos). `tests/integration/test_redeem_onchain.py` con Anvil Polygon fork marcado `@pytest.mark.slow`, ejecutado por devs pre-merge (`make test-integration-slow` o `pytest -m slow`). **Foundry NO requerido en CI**; devs lo instalan local para integration tests. | §8.3, §11 |

### Cambios derivados en este documento (consolidación)

- **§4.1 Capa 2** → actualizada para reflejar "Sin PIN por default, opt-in flag".
- **§6.4 Finality** → constante `DEFAULT_CONFIRMATIONS = 64` confirmada.
- **§8.3 Integration** → marker `@pytest.mark.slow` añadido; CI patrón `-m "not slow"`.
- **§10 Plan de despliegue** → insertado **F1.5 = `scripts/fund_proxy_matic.py`** entre F1 (Build) y F2 (Integration).
- **§11 Checklist exit** → nuevas verificaciones: `REDEEM_DRY_RUN` desde `DEPLOY_ENV`, `CTFRedeemer.DEFAULT_CONFIRMATIONS == 64`, `scripts/fund_proxy_matic.py --dry-run` retornando 0 sin broadcast.

### Plan inmediato (acción al aprobar)

1. **Inmediato (hoy):** crear branch de trabajo `feature/r2.0-redeem-impl`. Tag `R2.0-redeem-impl-F0` en este commit (aprobación + decisiones).
2. **F1 Build:** `src/infrastructure/polymarket/ctf_redeemer.py` + diffs en `clob_client.py` / `real_handler.py` / `audit_log.py` / `metrics.py` / `key_manager.py` + `web3==7.16.0` en requirements + `tests/unit/test_ctf_redeemer.py` (≥30) + `tests/property/test_redeem_invariants.py` (≥1).
3. **F1.5 Helper:** `scripts/fund_proxy_matic.py` con dry-run + manual broadcast + reconcile-by-hash.
4. **F2 Integration:** `tests/integration/test_redeem_onchain.py` con `@pytest.mark.slow`.
5. **F3-F5 Dry-run → Canary → Real:** secuencial, gating verde.

### Visibilidad en otros docs del repo (acción post-F5)

- `RUTA_IMPLEMENTACION.md § R2.0-redeem-impl`: actualizar checkboxes PLANEAR→CONSTRUIR→TESTEAR→DESPLEGAR con estados F0-F5.
- `RECORRIDO_ACTUAL.md § "🔴 LO QUE FALTA"`: mover R2.0-redeem-impl de 🔴 a 🟡 durante F1-F2, a ✅ al cerrar F5.
- `AUDIT_REPORT.md`: añadir entrada `R2.0-redeem-impl` con fecha, alcance, F-stados cerrados.
- `docs_historicos/RFC_R2_0_redeem_impl.md`: emitir snapshot del RFC al cerrar F5 (referencia histórica inmutable).

---

*— Fin del RFC (APPROVED 2026-06-25) —*
