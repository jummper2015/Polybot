---
name: ctf-onchain-redeem
description: >
  Auditoría obligatoria del flujo de redeem on-chain de CTF tokens (R2.0).
  Activa cuando se toca el redeem de posiciones resueltas en Polymarket:
  web3.py, redeemPositions, indexSets, gas estimation, tx receipt, audit
  on-chain. Cubre el camino CTF (Conditional Tokens Framework) cuando el
  CLOB V2 no soporta redeem nativo (redeemPositions no implementado).
  NO activa para el redeem vía CLOB (eso es polymarket-clob-audit).
---

# Skill: CTF On-Chain Redeem

## Contexto (R2.0 — feature/r2.0-redeem-impl)

Cuando un mercado de Polymarket se resuelve, las posiciones ganadoras deben redimirse
para convertir los tokens en pUSD. El CLOB V2 **no** implementa `redeemPositions` de forma
nativa (abril 2026). El camino alternativo es interactuar directamente con el contrato
**Conditional Tokens Framework (CTF)** en Polygon Mainnet vía `web3.py`.

---

## Hechos inamovibles

- **CTF Address (Polygon):** `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`
- **Chain:** Polygon Mainnet (ID 137).
- **Colateral:** pUSD (Polymarket USD, V2 abril 2026).
- **Método clave:** `redeemPositions(collateralToken, parentCollectionId, conditionId, indexSets)`.
- **indexSets:** Bitmask de outcomes ganadores. `1` = YES, `2` = NO, `3` = ambos.
- **Gas:** Estimar con `estimateGas()` + buffer 20%. Gas price vía `eth_gasPrice` o EIP-1559.
- **Cuenta:** La wallet usada para firmar órdenes CLOB (misma private key).
- **SDK:** `web3.py` para interacción on-chain. NO usar py-clob-client para redeem.

---

## Cuándo activa este skill

- Edición de `src/execution/real_handler.py` en el método `redeem_resolved_position`.
- Creación de `src/infrastructure/polymarket/ctf_redeemer.py`.
- Discusión sobre `redeemPositions`, `indexSets`, o el ABI de CTF.
- Tocar `scripts/redeem_dry_run.py` o `scripts/fund_proxy_matic.py`.
- Cualquier cambio que interactúe con `web3.py` o la wallet on-chain.
- Issues relacionados con `CLOBRedeemNotSupportedError`.

NO activa para:
- Redeem vía CLOB (usa `polymarket-clob-audit`).
- Ejecución de órdenes normales (usa `paper-vs-real-execution`).
- Risk (usa `risk-engine-guard`).

---

## Checklist de auditoría (obligatorio en cada cambio)

### A. Conexión web3

- [ ] `Web3(Web3.HTTPProvider(RPC_URL))` con RPC de Polygon (público o privado).
- [ ] Chain ID verificado: `web3.eth.chain_id == 137`.
- [ ] La private key **nunca** se loguea ni aparece en strings de excepción.
- [ ] `from we3.eth.account import Account; account = Account.from_key(private_key)`.

### B. Gas estimation

- [ ] `estimateGas()` llamado ANTES de `build_transaction()`.
- [ ] Buffer del 20% sobre el gas estimado (mínimo 300,000).
- [ ] Gas price vía EIP-1559 (`maxFeePerGas`, `maxPriorityFeePerGas`) o fallback a `gasPrice`.
- [ ] Si `estimateGas()` falla → no se envía la transacción → error logged + audit.

### C. Construcción de redeemPositions

- [ ] `collateralToken`: dirección del token pUSD.
- [ ] `parentCollectionId`: `0x000...` (bytes32 zero) para mercados estándar.
- [ ] `conditionId`: el `market_id` de la posición (ya es el conditionId de CTF).
- [ ] `indexSets`: `[1]` para YES, `[2]` para NO, `[3]` para ambos (si aplica).
- [ ] Amount de tokens a redimir = `position.shares` (todas las shares de la posición).

### D. Transacción y receipt

- [ ] `signed_tx = account.sign_transaction(tx)`.
- [ ] `tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)`.
- [ ] `receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)`.
- [ ] Verificar `receipt.status == 1` (éxito).
- [ ] Si `status == 0` → tx revertida → error logged + audit.
- [ ] `tx_hash` y `receipt` persistidos en audit log.

### E. Observabilidad y seguridad

- [ ] Métrica Prometheus: `redeem_attempts_total{status=success|failed}`.
- [ ] Métrica Prometheus: `redeem_gas_used` (histogram).
- [ ] Audit log registra: tx_hash, gas_used, block_number, redeemed_amount.
- [ ] Circuit breaker NO aplica a redeem (es operación one-shot, no reintentable con backoff).
- [ ] Timeout de 120s para `wait_for_transaction_receipt`.

### F. Edge cases

- [ ] Posición con `shares == 0` → no hacer redeem, log warning.
- [ ] Posición ya cerrada (`closed_at IS NOT NULL`) → no hacer redeem, log warning.
- [ ] Redeem de NO tokens → mismo flujo, `indexSets=[2]`.
- [ ] La tx se envía una sola vez; si falla, se requiere intervención manual (NO retry automático).
- [ ] Balance de MATIC en la wallet ≥ 0.1 MATIC (gas mínimo). Si no, alertar.

---

## Racionalizaciones a rechazar

- *"Uso el CLOB para redeem porque es más fácil."* → El CLOB V2 no implementa `redeemPositions`. Cualquier intento lanza `NotImplementedError`.
- *"No necesito estimateGas, pongo 500k fijo."* → No. El gas varía por complejidad del mercado. Estimación real > hardcode.
- *"Si falla, hago retry automático."* → No. Redeem on-chain no es idempotente. Un retry automático podría gastar gas en una tx que ya se ejecutó. Requiere intervención manual.
- *"Redimo solo YES, las NO se ignoran."* → Incorrecto. Si la posición es NO y ganó, hay que redimir NO (indexSets=[2]).
- *"No necesito audit log para redeem, ya está en el CLOB handler."* → No. El redeem on-chain es una operación financiera real; cada paso debe auditarse.

---

## Referencias

- CTF contract (Polygon): `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`
- CTF docs: https://docs.gnosis.io/conditionaltokens/
- Polymarket CTF integration: https://docs.polymarket.com/developers/ctf-exchange
- Script helpers: `scripts/redeem_dry_run.py`, `scripts/fund_proxy_matic.py`
- RFC de implementación: `RFC_R2_0_redeem_impl.md`
