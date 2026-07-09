"""
CTFRedeemer — On-chain redemption via CTF + Thin Collateral Adapter (Polygon).

Per RFC R2.0-redeem-impl APPROVED 2026-06-25.
- signature_type=POLY_PROXY (1) default (las posiciones viven en el proxy).
- Llama al Collateral Onramp `0x93070a847efEf7F70739046A929D47a521F5B8ee`
  para redeem atómico + USDC.e → pUSD wrap.
- Si el POLY_PROXY es un Gnosis Safe (signature_type=1), firma EIP-712
  SafeTx desde el EOA del operador y la tx ejecuta desde el proxy.
- EIP-1559 gas: max_fee_per_gas = 2 × base_fee; max_priority_fee de la red.
- Finality 64 bloques (~2 min @ Polygon).
- Tx replacement con +15% gas si mempool stuck.
- Pre-flight MATIC ≥ umbral configurable (default 0.1 MATIC).
- Idempotencia: redeem_op_id (UUID) + nonce management con asyncio.Lock.

Web3.py 7.16.0 AsyncWeb3 inyectado (testable con mocks).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import AsyncWeb3
from web3.exceptions import TimeExhausted, Web3Exception

from src.domain.enums.finality_status import FinalityStatus
from src.domain.exceptions.ctf_exceptions import (
    CTFAdapterPausedError,
    CTFRedeemError,
    DuplicateRedeemError,
    FinalityTimeoutError,
    InsufficientGasError,
    RedeemExecutionError,
)
from src.domain.value_objects.redeem_receipt import RedeemReceipt
from src.infrastructure.observability.metrics import (
    REDEEM_FAILURES_REASON,
    REDEEM_GAS_USED,
    REDEEM_PROXY_MATIC_BALANCE_GAUGE,
    REDEEM_PUSD_RECEIVED,
    REDEEM_REPLACEMENTS,
    REDEEM_TX_FINALITY_SECONDS,
    REDEEM_TX_MINING_SECONDS,
)
from src.infrastructure.polymarket.ctf_abi import (
    COLLATERAL_ADAPTER_REDEEM_ABI,
    CTF_REDEEM_POSITIONS_ABI,
    ERC20_BALANCE_AND_DECIMALS_ABI,
    SAFE_V130_EXEC_TRANSACTION_ABI,
)

if TYPE_CHECKING:
    from src.application.ports.repository_port import IRepositoryPort

logger = structlog.get_logger(__name__)

# ── Defaults lockeados RFC §13 ────────────────────────────────────────────
CTF_CONTRACT_ADDRESS      = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
COLLATERAL_ADAPTER_ADDRESS = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
PUSD_TOKEN_ADDRESS        = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"

DEFAULT_CONFIRMATIONS    = 64
DEFAULT_TIMEOUT_SECONDS  = 600
DEFAULT_GAS_LIMIT        = 350_000
DEFAULT_GAS_BUMP_PCT     = 0.15
DEFAULT_MATIC_MIN_WEI    = 100_000_000_000_000_000  # 0.1 MATIC

# Gnosis Safe v1.3.0 execTransaction constants (CALL operation).
SAFE_OPERATION_CALL      = 0
SAFE_TX_GAS              = 0
SAFE_BASE_GAS            = 0
SAFE_GAS_PRICE           = 0
ZERO_ADDRESS             = "0x0000000000000000000000000000000000000000"


class CTFRedeemer:
    """On-chain redeem flow: preflight → tx → finality → receipt (testable con AsyncMock)."""

    def __init__(
        self,
        web3:                 AsyncWeb3,
        adapter_address:      str = COLLATERAL_ADAPTER_ADDRESS,
        pusd_address:         str = PUSD_TOKEN_ADDRESS,
        proxy_address:        str = ZERO_ADDRESS,
        operator_address:     str = ZERO_ADDRESS,
        signature_type:       int  = 1,
        dry_run:              bool = False,
        confirmations:        int  = DEFAULT_CONFIRMATIONS,
        timeout_seconds:      int  = DEFAULT_TIMEOUT_SECONDS,
        matic_min_wei:        int  = DEFAULT_MATIC_MIN_WEI,
        gas_bump_pct:         float = DEFAULT_GAS_BUMP_PCT,
        operator_private_key: str | None = None,
        repository:           "IRepositoryPort | None" = None,
    ) -> None:
        self._w3              = web3
        self._adapter         = web3.to_checksum_address(adapter_address)
        self._pusd            = web3.to_checksum_address(pusd_address)
        self._proxy           = web3.to_checksum_address(proxy_address)
        self._operator        = web3.to_checksum_address(operator_address)
        self._sig_type        = signature_type
        self._dry_run         = dry_run
        self._confirmations   = confirmations
        self._timeout_seconds = timeout_seconds
        self._matic_min_wei   = matic_min_wei
        self._gas_bump_pct    = gas_bump_pct
        self._nonce_lock      = asyncio.Lock()
        # Key en memoria — NUNCA se loguea. El init log de abajo evita cualquier
        # referencia a este atributo. Se prefiere aquí (constructor) sobre pasarla
        # en cada `redeem()` para no filtrarla a callers como RealTradingHandler.
        self._operator_pk     = operator_private_key
        # Repo opcional para persistencia (idempotencia + reconcile on startup).
        # Si None → solo retorna receipt en memoria (útil para tests sin DB).
        self._repo            = repository

        self._adapter_contract = web3.eth.contract(
            address=self._adapter, abi=COLLATERAL_ADAPTER_REDEEM_ABI
        )
        self._ctf_contract = web3.eth.contract(
            address=web3.to_checksum_address(CTF_CONTRACT_ADDRESS),
            abi=CTF_REDEEM_POSITIONS_ABI,
        )
        self._pusd_contract = web3.eth.contract(
            address=self._pusd, abi=ERC20_BALANCE_AND_DECIMALS_ABI
        )
        self._safe_contract = web3.eth.contract(
            address=self._proxy, abi=SAFE_V130_EXEC_TRANSACTION_ABI,
        )

        logger.info(
            "ctf_redeemer_initialized",
            proxy=_mask_addr(self._proxy),
            adapter=_mask_addr(self._adapter),
            operator=_mask_addr(self._operator),
            confirmations=self._confirmations,
            signature_type=self._sig_type,
            dry_run=self._dry_run,
        )

    # ══════════════════════════════════════════════════════════════
    # PRE-FLIGHT
    # ══════════════════════════════════════════════════════════════

    async def preflight_matic(self) -> int:
        """Lee MATIC balance del POLY_PROXY. Levanta InsufficientGasError si < min."""
        balance = await self._w3.eth.get_balance(self._proxy, "latest")
        REDEEM_PROXY_MATIC_BALANCE_GAUGE.labels(wallet=self._proxy).set(balance)
        if balance < self._matic_min_wei:
            REDEEM_FAILURES_REASON.labels(reason="proxy_matic_empty").inc()
            raise InsufficientGasError(
                f"POLY_PROXY {_mask_addr(self._proxy)} MATIC={balance} "
                f"< required={self._matic_min_wei}"
            )
        return balance

    async def adapter_is_alive(self) -> bool:
        """Health check: el adapter onramp tiene código deployado."""
        code = await self._w3.eth.get_code(self._adapter, "latest")
        if len(code) == 0:
            REDEEM_FAILURES_REASON.labels(reason="adapter_paused").inc()
            return False
        return True

    # ══════════════════════════════════════════════════════════════
    # INDEXSETS — pure (RFC §6.1)
    # ══════════════════════════════════════════════════════════════

    @staticmethod
    def compute_index_sets(shares_yes: int, shares_no: int) -> tuple[int, ...]:
        """[1] si YES, [2] si NO, [1,2] si hedging residual (ambas shares > 0)."""
        if shares_yes < 0 or shares_no < 0:
            raise CTFRedeemError(
                f"compute_index_sets: shares no pueden ser negativas "
                f"(yes={shares_yes}, no={shares_no})"
            )
        if shares_yes == 0 and shares_no == 0:
            raise CTFRedeemError(
                "compute_index_sets: ambas shares son 0 — no hay nada que redimir"
            )
        if shares_yes > 0 and shares_no == 0:
            return (1,)
        if shares_no > 0 and shares_yes == 0:
            return (2,)
        return (1, 2)

    # ══════════════════════════════════════════════════════════════
    # REDEEM — flujo principal
    # ══════════════════════════════════════════════════════════════

    async def redeem(
        self,
        condition_id:   bytes | str,
        shares_yes:     int,
        shares_no:      int,
        redeem_op_id:   str,
        private_key:    str | None = None,
    ) -> RedeemReceipt:
        """Ejecuta redeem atómico. Si dry_run=True, simula con eth_call y NO envía.

        La private_key se resuelve como: arg explícito > operator_private_key del
        constructor > None. Sin key + no dry_run ⇒ CTFRedeemError.
        """
        effective_key   = private_key or self._operator_pk
        condition_bytes = self._normalize_condition_id(condition_id)
        condition_str   = "0x" + condition_bytes.hex()
        index_sets      = CTFRedeemer.compute_index_sets(shares_yes, shares_no)

        # ── Pre-flight ────────────────────────────────────────────
        if not await self.adapter_is_alive():
            raise CTFAdapterPausedError(
                f"Adapter onramp {_mask_addr(self._adapter)} sin código en latest block"
            )
        await self.preflight_matic()

        # ── Build calldata al adapter ─────────────────────────────
        adapter_calldata = self._adapter_contract.encode_abi(
            "redeemAndWrap",
            args=[condition_bytes, list(index_sets)],
        )

        # ── Si signature_type ∈ {1=POLY_PROXY, 2=GNOSIS_SAFE, 3=POLY_1271},
        #    las posiciones viven en el proxy → firma execTransaction desde el EOA.
        #    Si signature_type == 0 (EOA directa), se llama al adapter directamente.
        # Nota: en dry_run SIN private_key, degradamos a calldata directa al
        # adapter — no simulamos la envoltura SafeTx exacta pero validamos
        # encoding, adapter alive y gas. Un dry_run con key sí simula el path real.
        wrap_safe = self._sig_type in (1, 2, 3) and not (self._dry_run and not effective_key)
        if wrap_safe:
            if not effective_key:
                raise CTFRedeemError(
                    f"signature_type={self._sig_type} (proxy) requiere private_key "
                    f"para firmar SafeTx EIP-712"
                )
            tx_to, tx_data, tx_value = await self._build_safe_exec_transaction(
                self._adapter, 0, adapter_calldata, effective_key
            )
        else:
            # EOA directo o dry_run sin key: call directo al adapter
            tx_to, tx_data, tx_value = self._adapter, adapter_calldata, 0
        tx_from = self._operator  # EOA firma y paga gas; proxy ejecuta (si aplica)

        # ── EIP-1559 gas estimation ───────────────────────────────
        gas_limit, gas_prices = await self._estimate_eip1559_gas(
            tx_from, tx_to, tx_data, tx_value
        )
        if gas_prices is None:
            # node sin soporte 1559 → fallback legacy gas_price
            gas_prices = await self._estimate_legacy_gas()

        REDEEM_GAS_USED.labels(proxy=self._proxy).observe(gas_limit)

        # ── Build raw tx ──────────────────────────────────────────
        chain_id = await self._w3.eth.chain_id

        tx_params = {
            "from":                  tx_from,
            "to":                    tx_to,
            "data":                  tx_data,
            "value":                 tx_value,
            "gas":                   gas_limit,
            "maxFeePerGas":          gas_prices["max_fee"],
            "maxPriorityFeePerGas":  gas_prices["max_priority"],
            "nonce":                 None,    # assigned inside _nonce_lock below
            "chainId":               chain_id,
        }

        # ── dry_run: simula con eth_call, NO envía ────────────────
        # Nota: el guard debe correr ANTES del nonce_lock/sign/send para que
        # dry_run sea seguro sin private_key.
        if self._dry_run:
            try:
                await self._w3.eth.call(tx_params, "latest")
                sim_status = FinalityStatus.MINED
            except Web3Exception as e:
                sim_status = FinalityStatus.FAILED
                logger.warning(
                    "ctf_redeem_dry_run_revert", error=str(e)[:200], op=redeem_op_id
                )
            return RedeemReceipt(
                redeem_op_id=redeem_op_id,
                condition_id=condition_str,
                tx_hash=None,
                index_sets=index_sets,
                shares_redeemed=shares_yes + shares_no,
                pusd_received=0.0,
                gas_used=None,
                gas_fee_matic=None,
                submitted_at=None,
                mined_at=None,
                confirmed_at=None,
                status=sim_status.value,
                proxy_address=self._proxy,
                adapter_address=self._adapter,
            )

        if not effective_key:
            raise CTFRedeemError(
                "redeem no-dry_run requiere private_key para firmar y enviar"
            )

        # ── Snapshot pre-tx pUSD balance (para calcular delta post-CONFIRMED) ──
        pre_tx_pusd_balance = await self._read_pusd_raw_balance()

        # ── Lock: nonce-read → sign → send-mempool (extended to prevent race) ──
        # Si NO se mantiene hasta send, dos coroutines pasarían el lock secuencialmente,
        # ambas leerían el mismo nonce "latest" y la segunda fallaría con
        # "nonce too low" o "replacement tx underpriced".
        submitted_at = datetime.now(timezone.utc)
        async with self._nonce_lock:
            nonce = await self._w3.eth.get_transaction_count(
                self._operator, "latest"
            )
            tx_params["nonce"] = nonce
            signed = Account.sign_transaction(
                tx_params, private_key=effective_key,
            )
            tx_hash_bytes = await self._w3.eth.send_raw_transaction(
                signed.raw_transaction
            )
            tx_hash = "0x" + tx_hash_bytes.hex()

        logger.info(
            "ctf_redeem_tx_submitted",
            tx_hash=tx_hash,
            redeem_op_id=redeem_op_id,
            condition_id_short=condition_str[-8:],
            nonce=nonce,
        )

        # ── Espera finality ───────────────────────────────────────
        return await self.wait_for_finality(
            tx_hash=tx_hash,
            redeem_op_id=redeem_op_id,
            condition_id=condition_str,
            index_sets=index_sets,
            shares_yes=shares_yes,
            shares_no=shares_no,
            submitted_at=submitted_at,
            gas_limit=gas_limit,
            gas_prices=gas_prices,
            pre_tx_pusd_balance=pre_tx_pusd_balance,
        )

    # ══════════════════════════════════════════════════════════════
    # FINALITY — espera 64 confirmaciones
    # ══════════════════════════════════════════════════════════════

    async def wait_for_finality(
        self,
        tx_hash:          str,
        redeem_op_id:     str,
        condition_id:     str,
        index_sets:       tuple[int, ...],
        shares_yes:       int,
        shares_no:        int,
        submitted_at:     datetime,
        gas_limit:        int,
        gas_prices:       dict,
        pre_tx_pusd_balance: float | None = None,
    ) -> RedeemReceipt:
        """Espera al menos N confirmaciones (~2 min @ Polygon N=64) o timeout 600s."""
        deadline = time.monotonic() + self._timeout_seconds
        try:
            receipt = await self._w3.eth.wait_for_transaction_receipt(
                tx_hash, timeout=self._timeout_seconds, poll_latency=2.0
            )
        except TimeExhausted:
            REDEEM_FAILURES_REASON.labels(reason="finality_timeout").inc()
            raise FinalityTimeoutError(
                f"tx {tx_hash} no minó en {self._timeout_seconds}s"
            )

        mined_at = datetime.now(timezone.utc)
        mining_seconds = (mined_at - submitted_at).total_seconds()
        REDEEM_TX_MINING_SECONDS.observe(max(0.0, mining_seconds))
        REDEEM_GAS_USED.labels(proxy=self._proxy).observe(receipt["gasUsed"])

        if receipt["status"] == 0:
            REDEEM_FAILURES_REASON.labels(reason="onchain_revert").inc()
            raise RedeemExecutionError(
                f"tx {tx_hash} ejecutada pero REVERTIDA on-chain"
            )

        logger.info(
            "ctf_redeem_tx_mined",
            tx_hash=tx_hash,
            block=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            confirmations_to_wait=self._confirmations,
        )

        while True:
            current_block = await self._w3.eth.block_number
            depth = current_block - receipt["blockNumber"]
            if depth >= self._confirmations:
                confirmed_at = datetime.now(timezone.utc)
                finality_seconds = (confirmed_at - submitted_at).total_seconds()
                REDEEM_TX_FINALITY_SECONDS.observe(max(0.0, finality_seconds))

                pusd_received = await self._compute_pusd_delta(
                    pre_tx_balance=pre_tx_pusd_balance,
                )
                REDEEM_PUSD_RECEIVED.labels(proxy=self._proxy).inc(pusd_received)
                gas_fee_matic = (
                    receipt["gasUsed"] * gas_prices["max_fee"] / 1e18
                )

                return RedeemReceipt(
                    redeem_op_id=redeem_op_id,
                    condition_id=condition_id,
                    tx_hash=tx_hash,
                    index_sets=index_sets,
                    shares_redeemed=shares_yes + shares_no,
                    pusd_received=pusd_received,
                    gas_used=receipt["gasUsed"],
                    gas_fee_matic=gas_fee_matic,
                    submitted_at=submitted_at,
                    mined_at=mined_at,
                    confirmed_at=confirmed_at,
                    status=FinalityStatus.CONFIRMED.value,
                    proxy_address=self._proxy,
                    adapter_address=self._adapter,
                )
            if time.monotonic() > deadline:
                REDEEM_FAILURES_REASON.labels(reason="finality_timeout").inc()
                raise FinalityTimeoutError(
                    f"tx {tx_hash} minada={receipt['blockNumber']} "
                    f"current={current_block} depth={depth} "
                    f"< required={self._confirmations} en {self._timeout_seconds}s"
                )
            await asyncio.sleep(2.0)  # Polygon block time

    # ══════════════════════════════════════════════════════════════
    # TX REPLACEMENT
    # ══════════════════════════════════════════════════════════════

    async def replace_tx_if_stuck(
        self,
        original_tx:      dict,
        private_key:      str,
    ) -> str:
        """Rebuild + send con mismo nonce, gas bumped +15% (preserva idempotencia)."""
        new_tx = original_tx.copy()
        new_tx["maxFeePerGas"]         = int(
            original_tx["maxFeePerGas"] * (1 + self._gas_bump_pct)
        )
        new_tx["maxPriorityFeePerGas"] = int(
            original_tx["maxPriorityFeePerGas"] * (1 + self._gas_bump_pct)
        )

        signed = Account.sign_transaction(new_tx, private_key=private_key)  # type: ignore[arg-type]
        new_hash_bytes = await self._w3.eth.send_raw_transaction(signed.raw_transaction)
        new_hash = "0x" + new_hash_bytes.hex()

        REDEEM_REPLACEMENTS.inc()
        logger.info(
            "ctf_redeem_tx_replaced",
            original_nonce=original_tx["nonce"],
            new_tx_hash=new_hash,
            bump_pct=self._gas_bump_pct * 100,
        )
        return new_hash

    # ══════════════════════════════════════════════════════════════
    # RECONCILIACIÓN AL ARRANCAR
    # ══════════════════════════════════════════════════════════════

    async def reconcile_on_startup(
        self,
        pending_ops:  list[dict],
    ) -> list[RedeemReceipt]:
        """Para ops en estado SUBMITTED/MINED, consulta receipt y actualiza estado."""
        reconciled: list[RedeemReceipt] = []
        for op in pending_ops:
            tx_hash = op.get("tx_hash")
            if not tx_hash:
                continue
            try:
                receipt = await self._w3.eth.get_transaction_receipt(tx_hash)
            except Web3Exception as e:
                logger.warning(
                    "ctf_redeem_reconcile_lookup_failed",
                    tx_hash=tx_hash,
                    error=str(e)[:200],
                )
                continue
            if receipt is None or receipt.get("blockNumber") is None:
                continue
            if receipt.get("status") != 1:
                REDEEM_FAILURES_REASON.labels(reason="reconcile_reverted").inc()
                continue
            # Receipt presente + status=1 ⇒ marcar CONFIRMED
            current_block = await self._w3.eth.block_number
            depth = current_block - receipt["blockNumber"]
            if depth < self._confirmations:
                # Aún no llega a finality; saltar (loop continúa en runtime)
                continue
            confirmed_at = datetime.now(timezone.utc)
            REDEEM_TX_FINALITY_SECONDS.observe(
                max(0.0, (confirmed_at - op["submitted_at"]).total_seconds())
            )
            # En reconciliación no tenemos snapshot pre-tx; usamos balance actual
            # como aproximación. F2 deberá persistir baseline snapshot para precisión.
            try:
                current = await self._read_pusd_raw_balance()
                pusd_received = current   # aproximado; ver TODO F2 baseline snapshot
            except Web3Exception:
                pusd_received = float(op.get("shares_yes", 0) + op.get("shares_no", 0))
            REDEEM_PUSD_RECEIVED.labels(proxy=self._proxy).inc(pusd_received)
            reconciled.append(
                RedeemReceipt(
                    redeem_op_id=op["redeem_op_id"],
                    condition_id=op["condition_id"],
                    tx_hash=tx_hash,
                    index_sets=tuple(op["index_sets"]),
                    shares_redeemed=op.get("shares_yes", 0) + op.get("shares_no", 0),
                    pusd_received=pusd_received,
                    gas_used=receipt["gasUsed"],
                    gas_fee_matic=None,
                    submitted_at=op["submitted_at"],
                    mined_at=None,
                    confirmed_at=confirmed_at,
                    status=FinalityStatus.CONFIRMED.value,
                    proxy_address=self._proxy,
                    adapter_address=self._adapter,
                )
            )
        return reconciled

    # ══════════════════════════════════════════════════════════════
    # PRIVATE HELPERS
    # ══════════════════════════════════════════════════════════════

    @staticmethod
    def _normalize_condition_id(condition_id: bytes | str) -> bytes:
        """Valida y convierte a bytes32."""
        if isinstance(condition_id, bytes):
            if len(condition_id) != 32:
                raise CTFRedeemError(
                    f"condition_id (bytes) debe ser 32 bytes; recibido {len(condition_id)}"
                )
            return condition_id
        if not condition_id.startswith("0x") or len(condition_id) != 66:
            raise CTFRedeemError(
                f"condition_id inválido: '{condition_id}' "
                "(esperado 0x + 64 hex chars = 32 bytes)"
            )
        return bytes.fromhex(condition_id[2:])

    async def _estimate_eip1559_gas(
        self, tx_from: str, tx_to: str, tx_data: bytes, tx_value: int
    ) -> tuple[int, dict | None]:
        """Estima gas_limit (eth_estimateGas * 1.2) + max_fee + max_priority_fee."""
        try:
            est = await self._w3.eth.estimate_gas(
                {"from": tx_from, "to": tx_to, "data": tx_data, "value": tx_value},
                "latest",
            )
            gas_limit = int(est * 1.20)
        except Web3Exception:
            gas_limit = DEFAULT_GAS_LIMIT
        try:
            block = await self._w3.eth.get_block("latest")
        except Web3Exception:
            return gas_limit, None
        if "baseFeePerGas" not in block:
            return gas_limit, None
        max_fee = int(block["baseFeePerGas"] * 2)
        try:
            max_priority = await self._w3.eth.max_priority_fee
        except (AttributeError, Web3Exception):
            max_priority = int(1.5e9)  # 1.5 gwei fallback
        return gas_limit, {"max_fee": max_fee, "max_priority": max_priority}

    async def _estimate_legacy_gas(self) -> dict:
        """Fallback pre-EIP-1559: gasPrice estimada, max_fee=max_priority=gas_price."""
        try:
            gp = await self._w3.eth.gas_price
        except Web3Exception:
            gp = int(50e9)
        return {"max_fee": int(gp), "max_priority": int(gp)}

    async def _build_safe_exec_transaction(
        self, to: str, value: int, data: bytes, private_key: str,
    ) -> tuple[str, bytes, int]:
        """Build safe.execTransaction(to, value, data, CALL, 0, 0, 0, 0, sig)."""
        chain_id = await self._w3.eth.chain_id
        try:
            safe_nonce = await self._safe_contract.functions.nonce().call()
        except Web3Exception:
            safe_nonce = 0

        typed_data = encode_typed_data(full_message={
            "domain": {
                "chainId": chain_id,
                "verifyingContract": self._proxy,
            },
            "types": {
                "SafeTx": [
                    {"name": "to",             "type": "address"},
                    {"name": "value",          "type": "uint256"},
                    {"name": "data",           "type": "bytes"},
                    {"name": "operation",      "type": "uint8"},
                    {"name": "safeTxGas",      "type": "uint256"},
                    {"name": "baseGas",        "type": "uint256"},
                    {"name": "gasPrice",       "type": "uint256"},
                    {"name": "gasToken",       "type": "address"},
                    {"name": "refundReceiver", "type": "address"},
                    {"name": "nonce",          "type": "uint256"},
                ],
            },
            "primaryType": "SafeTx",
            "message": {
                "to":             to,
                "value":          value,
                "data":           data,
                "operation":      SAFE_OPERATION_CALL,
                "safeTxGas":      SAFE_TX_GAS,
                "baseGas":        SAFE_BASE_GAS,
                "gasPrice":       SAFE_GAS_PRICE,
                "gasToken":       ZERO_ADDRESS,
                "refundReceiver": ZERO_ADDRESS,
                "nonce":          safe_nonce,
            },
        })
        signed = Account.sign_message(typed_data, private_key=private_key)
        sig_bytes = bytes.fromhex(signed.signature.hex())
        exec_data = self._safe_contract.encode_abi(
            "execTransaction",
            args=[to, value, data, SAFE_OPERATION_CALL,
                  SAFE_TX_GAS, SAFE_BASE_GAS, SAFE_GAS_PRICE,
                  ZERO_ADDRESS, ZERO_ADDRESS, sig_bytes],
        )
        return (self._proxy, exec_data, 0)

    async def _read_pusd_raw_balance(self) -> float:
        """
        Lee el balance pUSD (raw, sin delta) del POLY_PROXY.

        Devuelve 0.0 si el nodo falla (degradación amable; F2 deberá añadir
        caché de último valor conocido si los fallos persisten).
        """
        try:
            raw = await self._pusd_contract.functions.balanceOf(self._proxy).call()
            decimals = await self._pusd_contract.functions.decimals().call()
            return float(raw) / (10 ** decimals)
        except Web3Exception:
            return 0.0

    async def _compute_pusd_delta(self, pre_tx_balance: float | None) -> float:
        """
        Calcula delta pUSD = (balance_post_tx) − (pre_tx_balance).

        Con snapshot pre-tx conocido, esta función reporta el cambio real
        en el balance del POLY_PROXY (incluyendo wrap USDC.e→pUSD).
        Sin snapshot (None), devuelve el balance actual como aproximación
        y registra warning para futura auditoría.
        """
        post_tx = await self._read_pusd_raw_balance()
        if pre_tx_balance is None:
            logger.warning(
                "ctf_redeem_pusd_delta_without_baseline",
                post_tx_balance=post_tx,
                hint="F2 should persist pre_tx snapshot for accurate deltas",
            )
            return post_tx
        delta = post_tx - pre_tx_balance
        return max(0.0, delta)   # nunca negativo (el redeem no quita pUSD)


def _mask_addr(addr: str) -> str:
    """Enmascara EVM address para logs (0x1234...abcd)."""
    if len(addr) >= 10:
        return f"{addr[:6]}...{addr[-4:]}"
    return "***"
