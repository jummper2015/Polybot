"""
Unit tests para CTFRedeemer (R2.0-redeem-impl F1).

Block_number se mockea con AsyncMock(default).return_value para que `await` funcione.
AsyncMock(side_effect=[items]) consume un valor por cada `await`.

Cubre:
  - TestComputeIndexSets (7)         pure, sin web3
  - TestPreflightMatic (3)            mocks w3.eth.get_balance + InsufficientGasError
  - TestAdapterIsAlive (2)            mocks w3.eth.get_code
  - TestEstimateEIP1559Gas (3)        mocks w3.eth.estimate_gas + get_block + max_priority_fee
  - TestEstimateLegacyFallback (1)    get_block sin baseFeePerGas → legacy path
  - TestRedeemDryRun (3)              eth_call ok, revert, no real tx
  - TestRedeemRealHappyPath (3)       only_yes, only_no, both
  - TestRedeemErrors (4)              invalid condition_id, adapter paused, matic empty, no private key
  - TestWaitForFinality (4)           confirmed,early,timeout,reverted
  - TestReplaceTxIfStuck (3)          +15% gas, mismo nonce, nuevo tx_hash
  - TestReconcileOnStartup (3)        present receipt, below threshold, missing
  - TestNormalizeConditionId (4)      str hex, bytes32, invalid length, sin 0x
  - TestMaskAddr (3)                  long, short, empty

Total: 41 tests a través de 14 clases.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _AwaitableValue:
    """Reusable awaitable that emulates web3.py 7.x async properties.

    Real `AsyncEth.chain_id`, `.max_priority_fee`, `.gas_price`, `.block_number`
    are `@property async def`, so `await w3.eth.foo` awaits a coroutine yielded
    by the property getter. `AsyncMock` instances are NOT directly awaitable
    (you'd have to call them with parens: `await asyncmock()`), so this helper
    supports the `await attr` pattern used by real web3.

    Supports:
        _AwaitableValue(42)                       — reusable, returns 42 each await
        _AwaitableValue(side_effect=[1, 2, 3])    — pop from list per await
        obj.return_value = 99                     — override on the fly
    """
    def __init__(self, value=None, side_effect=None):
        self._value = value
        self.side_effect = side_effect
        self._sfx_iter = None

    @property
    def return_value(self):
        return self._value

    @return_value.setter
    def return_value(self, v):
        self._value = v

    def __await__(self):
        async def _get():
            if self.side_effect is not None:
                if self._sfx_iter is None:
                    self._sfx_iter = iter(self.side_effect)
                try:
                    return next(self._sfx_iter)
                except StopIteration:
                    return self._value
            return self._value
        return _get().__await__()

from src.domain.enums.finality_status import FinalityStatus
from src.domain.exceptions.ctf_exceptions import (
    CTFAdapterPausedError,
    CTFRedeemError,
    FinalityTimeoutError,
    InsufficientGasError,
    RedeemExecutionError,
)
from src.domain.value_objects.redeem_receipt import RedeemReceipt
from src.infrastructure.polymarket import ctf_redeemer as cr_module
from src.infrastructure.polymarket.ctf_redeemer import (
    DEFAULT_CONFIRMATIONS,
    DEFAULT_GAS_LIMIT,
    DEFAULT_MATIC_MIN_WEI,
    CTFRedeemer,
    _mask_addr,
)

# ── Test fixtures ───────────────────────────────────────────────────────

DUMMY_PROXY    = "0x" + "11" * 20   # 20 bytes
DUMMY_OPERATOR = "0x" + "22" * 20
DUMMY_ADAPTER  = "0x" + "33" * 20
DUMMY_PUSD     = "0x" + "44" * 20
DUMMY_PRIVATE_KEY = "0x" + "ab" * 32  # 32 bytes hex (mocked; Account.sign_transaction patched)

# A 32-byte condition_id válido (64 hex chars)
VALID_CONDITION_ID = "0x" + "ef" * 32
VALID_CONDITION_BYTES = bytes.fromhex("ef" * 32)

# Block heights used across tests (default mock block_number=12345)
RECEIPT_BLOCK = 12345
CONFIRMED_BLOCK = RECEIPT_BLOCK + DEFAULT_CONFIRMATIONS + 1
SHALLOW_BLOCK = RECEIPT_BLOCK + 1  # sólo 1 confirmación


def make_mock_web3(
    *,
    matic_wei: int = 1 * 10**18,                # 1 MATIC > 0.1 min
    adapter_code: bytes = b"\x00" * 100,        # non-empty
    estimate_gas_result: int = 200_000,
    chain_id: int = 137,
    block_base_fee: int = 30_000_000_000,       # 30 gwei
    max_priority_fee: int = 1_500_000_000,      # 1.5 gwei
    nonce: int = 7,
    block_number: int = RECEIPT_BLOCK,
) -> MagicMock:
    """Build a MagicMock que satisface CTFRedeemer.__init__ + method calls.

    Atributos `block_number`, `max_priority_fee`, `chain_id`, `gas_price`,
    `send_raw_transaction`, etc., SON AsyncMock para que `await` funcione.
    Tests individuales pueden sobreescribir `.return_value` (e.g.
    `w3.eth.block_number.return_value = 12350`).
    """

    w3 = MagicMock(name="AsyncWeb3")

    w3.eth.get_balance            = AsyncMock(return_value=matic_wei)
    w3.eth.get_code               = AsyncMock(return_value=adapter_code)
    w3.eth.estimate_gas           = AsyncMock(return_value=estimate_gas_result)
    w3.eth.get_block              = AsyncMock(return_value={
        "baseFeePerGas": block_base_fee,
        "number": RECEIPT_BLOCK,
    })
    # Property-style access: real web3.py returns a coroutine from @property async.
    # AsyncMock is not directly awaitable → use _AwaitableValue helper.
    w3.eth.max_priority_fee       = _AwaitableValue(max_priority_fee)
    w3.eth.get_transaction_count  = AsyncMock(return_value=nonce)
    w3.eth.chain_id               = _AwaitableValue(chain_id)
    w3.eth.block_number           = _AwaitableValue(block_number)
    w3.eth.gas_price              = _AwaitableValue(50_000_000_000)
    w3.eth.send_raw_transaction   = AsyncMock(
        return_value=bytes.fromhex("aabb" * 32),
    )
    w3.eth.wait_for_transaction_receipt = AsyncMock(return_value={
        "status": 1, "blockNumber": RECEIPT_BLOCK, "gasUsed": 250_000,
    })
    w3.eth.get_transaction_receipt = AsyncMock(return_value={
        "status": 1, "blockNumber": RECEIPT_BLOCK, "gasUsed": 250_000,
    })
    w3.eth.call                   = AsyncMock(return_value=b"")

    # Contract factory: cada llamada a `w3.eth.contract(...)` en CTFRedeemer.__init__
    # devuelve el mismo MagicMock. Preconfigurar los .call() en las cadenas
    # `contract.functions.<method>(...).call` como AsyncMock para simular
    # AsyncContractFunction.call() de web3.py.
    contract_factory              = MagicMock(name="contract_factory")
    shared_contract               = contract_factory.return_value
    shared_contract.functions.nonce.return_value.call     = AsyncMock(return_value=0)
    shared_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=1_000_000)
    shared_contract.functions.decimals.return_value.call  = AsyncMock(return_value=6)
    # encode_abi() en real web3 devuelve bytes (o hex str). Ser explícitos con bytes
    # para que downstream (encode_typed_data, send_raw_transaction) lo acepte.
    shared_contract.encode_abi                            = MagicMock(return_value=b"\xde\xad\xbe\xef")
    w3.eth.contract               = contract_factory

    w3.to_checksum_address = MagicMock(side_effect=lambda x: str(x))

    return w3


def make_redeemer(w3: MagicMock | None = None, **kwargs) -> CTFRedeemer:
    if w3 is None:
        w3 = make_mock_web3()
    defaults = dict(
        web3=w3,
        adapter_address=DUMMY_ADAPTER,
        pusd_address=DUMMY_PUSD,
        proxy_address=DUMMY_PROXY,
        operator_address=DUMMY_OPERATOR,
        signature_type=1,
        dry_run=True,
        confirmations=DEFAULT_CONFIRMATIONS,
        matic_min_wei=DEFAULT_MATIC_MIN_WEI,
    )
    defaults.update(kwargs)
    return CTFRedeemer(**defaults)


# ══════════════════════════════════════════════════════════════════════
# TestComputeIndexSets — pure (sin AsyncWeb3)
# ══════════════════════════════════════════════════════════════════════

class TestComputeIndexSets:

    def test_only_yes_returns_index_1(self):
        assert CTFRedeemer.compute_index_sets(shares_yes=100, shares_no=0) == (1,)

    def test_only_no_returns_index_2(self):
        assert CTFRedeemer.compute_index_sets(shares_yes=0, shares_no=100) == (2,)

    def test_both_returns_indices_1_and_2(self):
        assert CTFRedeemer.compute_index_sets(shares_yes=50, shares_no=50) == (1, 2)

    def test_both_zero_raises(self):
        with pytest.raises(CTFRedeemError, match="ambas shares son 0"):
            CTFRedeemer.compute_index_sets(0, 0)

    def test_negative_yes_raises(self):
        with pytest.raises(CTFRedeemError, match="negativas"):
            CTFRedeemer.compute_index_sets(-1, 0)

    def test_negative_no_raises(self):
        with pytest.raises(CTFRedeemError, match="negativas"):
            CTFRedeemer.compute_index_sets(0, -1)

    def test_returns_tuple_type(self):
        result = CTFRedeemer.compute_index_sets(10, 0)
        assert isinstance(result, tuple)
        assert all(isinstance(i, int) for i in result)


# ══════════════════════════════════════════════════════════════════════
# TestPreflightMatic — w3.eth.get_balance
# ══════════════════════════════════════════════════════════════════════

class TestPreflightMatic:

    @pytest.mark.asyncio
    async def test_returns_balance_above_min(self):
        w3 = make_mock_web3(matic_wei=10**18)  # 1 MATIC > 0.1 min
        redeemer = make_redeemer(w3)
        balance = await redeemer.preflight_matic()
        assert balance == 10**18

    @pytest.mark.asyncio
    async def test_raises_insufficient_when_below_min(self):
        w3 = make_mock_web3(matic_wei=50_000_000_000_000_000)  # 0.05 MATIC
        redeemer = make_redeemer(w3, matic_min_wei=100_000_000_000_000_000)
        with pytest.raises(InsufficientGasError, match="MATIC="):
            await redeemer.preflight_matic()

    @pytest.mark.asyncio
    async def test_sets_gauge_with_proxy_wallet_label(self):
        from src.infrastructure.observability.metrics import (
            REDEEM_PROXY_MATIC_BALANCE_GAUGE,
        )
        w3 = make_mock_web3(matic_wei=10**18)
        redeemer = make_redeemer(w3)
        with patch.object(REDEEM_PROXY_MATIC_BALANCE_GAUGE, "labels") as mock_labels:
            mock_gauge = MagicMock()
            mock_labels.return_value = mock_gauge
            await redeemer.preflight_matic()
            assert mock_labels.called
            assert mock_gauge.set.called


# ══════════════════════════════════════════════════════════════════════
# TestAdapterIsAlive — w3.eth.get_code
# ══════════════════════════════════════════════════════════════════════

class TestAdapterIsAlive:

    @pytest.mark.asyncio
    async def test_alive_with_code(self):
        w3 = make_mock_web3(adapter_code=b"\x60\x60\x60\x00")
        redeemer = make_redeemer(w3)
        assert await redeemer.adapter_is_alive() is True

    @pytest.mark.asyncio
    async def test_paused_with_empty_code(self):
        w3 = make_mock_web3(adapter_code=b"")
        redeemer = make_redeemer(w3)
        assert await redeemer.adapter_is_alive() is False


# ══════════════════════════════════════════════════════════════════════
# TestEstimateEIP1559Gas + TestEstimateLegacyFallback
# ══════════════════════════════════════════════════════════════════════

class TestEstimateEIP1559Gas:

    @pytest.mark.asyncio
    async def test_returns_gas_limit_and_prices_with_block_base_fee(self):
        w3 = make_mock_web3(
            estimate_gas_result=200_000,
            block_base_fee=30_000_000_000,
            max_priority_fee=1_500_000_000,
        )
        redeemer = make_redeemer(w3)
        gas_limit, prices = await redeemer._estimate_eip1559_gas(
            DUMMY_OPERATOR, DUMMY_ADAPTER, b"\xab\xcd", 0
        )
        assert gas_limit == int(200_000 * 1.2) == 240_000
        assert prices["max_fee"] == 60_000_000_000  # 2 × base_fee
        assert prices["max_priority"] == 1_500_000_000

    @pytest.mark.asyncio
    async def test_uses_default_gas_limit_when_estimate_fails(self):
        from web3.exceptions import Web3Exception

        w3 = make_mock_web3()
        w3.eth.estimate_gas = AsyncMock(side_effect=Web3Exception("rpc down"))
        redeemer = make_redeemer(w3)
        gas_limit, prices = await redeemer._estimate_eip1559_gas(
            DUMMY_OPERATOR, DUMMY_ADAPTER, b"\xab\xcd", 0
        )
        assert gas_limit == DEFAULT_GAS_LIMIT
        assert prices is not None and "max_fee" in prices

    @pytest.mark.asyncio
    async def test_handles_missing_base_fee_per_gas(self):
        w3 = make_mock_web3()
        w3.eth.get_block = AsyncMock(return_value={"number": RECEIPT_BLOCK})  # sin baseFeePerGas
        redeemer = make_redeemer(w3)
        gas_limit, prices = await redeemer._estimate_eip1559_gas(
            DUMMY_OPERATOR, DUMMY_ADAPTER, b"\xab\xcd", 0
        )
        assert prices is None  # caller will use legacy fallback


class TestEstimateLegacyFallback:

    @pytest.mark.asyncio
    async def test_uses_gas_price_when_no_1559(self):
        w3 = make_mock_web3()
        w3.eth.gas_price = _AwaitableValue(80_000_000_000)
        redeemer = make_redeemer(w3)
        prices = await redeemer._estimate_legacy_gas()
        assert prices["max_fee"] == 80_000_000_000
        assert prices["max_priority"] == 80_000_000_000


# ══════════════════════════════════════════════════════════════════════
# TestRedeemDryRun — eth_call sin tx real
# ══════════════════════════════════════════════════════════════════════

class TestRedeemDryRun:

    @pytest.mark.asyncio
    async def test_dry_run_success_returns_mined_status(self):
        w3 = make_mock_web3()
        redeemer = make_redeemer(w3, dry_run=True)
        receipt = await redeemer.redeem(
            condition_id=VALID_CONDITION_ID,
            shares_yes=100, shares_no=0,
            redeem_op_id="op-dry-success",
        )
        assert receipt.status == FinalityStatus.MINED.value
        assert receipt.tx_hash is None
        assert receipt.index_sets == (1,)
        assert receipt.shares_redeemed == 100
        assert receipt.pusd_received == 0.0

    @pytest.mark.asyncio
    async def test_dry_run_revert_returns_failed_status(self):
        from web3.exceptions import Web3Exception

        w3 = make_mock_web3()
        w3.eth.call = AsyncMock(side_effect=Web3Exception("execution reverted"))
        redeemer = make_redeemer(w3, dry_run=True)
        receipt = await redeemer.redeem(
            condition_id=VALID_CONDITION_ID,
            shares_yes=100, shares_no=0,
            redeem_op_id="op-dry-revert",
        )
        assert receipt.status == FinalityStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_dry_run_no_send_raw_transaction(self):
        w3 = make_mock_web3()
        redeemer = make_redeemer(w3, dry_run=True)
        await redeemer.redeem(
            condition_id=VALID_CONDITION_ID,
            shares_yes=50, shares_no=0,
            redeem_op_id="op-dry-stub",
        )
        # dry_run NO debe enviar tx
        w3.eth.send_raw_transaction.assert_not_called()


# ══════════════════════════════════════════════════════════════════════
# TestRedeemRealHappyPath — Account.sign_transaction patched + block_number deep
# ══════════════════════════════════════════════════════════════════════

class TestRedeemRealHappyPath:

    @pytest.mark.asyncio
    async def test_redeem_only_yes_executes_end_to_end(self):
        w3 = make_mock_web3()
        # Por defecto block_number=12345; depth = current - receipt.blockNumber.
        # 12345 - 12345 = 0, no llegamos a 64 → loop infinito. Sobreescribimos.
        w3.eth.block_number.return_value = CONFIRMED_BLOCK
        redeemer = make_redeemer(w3, dry_run=False)

        with patch.object(
            cr_module.Account, "sign_transaction",
            return_value=MagicMock(raw_transaction=b"\xab" * 100),
        ):
            receipt = await redeemer.redeem(
                condition_id=VALID_CONDITION_ID,
                shares_yes=100, shares_no=0,
                redeem_op_id="op-real-yes",
                private_key=DUMMY_PRIVATE_KEY,
            )
        assert receipt.status == FinalityStatus.CONFIRMED.value
        assert receipt.tx_hash == "0x" + "aabb" * 32
        assert receipt.index_sets == (1,)
        assert receipt.shares_redeemed == 100
        assert receipt.gas_used == 250_000
        assert receipt.confirmed_at is not None
        assert receipt.submitted_at is not None

    @pytest.mark.asyncio
    async def test_redeem_only_no_uses_index_set_2(self):
        w3 = make_mock_web3()
        w3.eth.block_number.return_value = CONFIRMED_BLOCK
        redeemer = make_redeemer(w3, dry_run=False)

        with patch.object(
            cr_module.Account, "sign_transaction",
            return_value=MagicMock(raw_transaction=b"\xab" * 100),
        ):
            receipt = await redeemer.redeem(
                condition_id=VALID_CONDITION_ID,
                shares_yes=0, shares_no=200,
                redeem_op_id="op-real-no",
                private_key=DUMMY_PRIVATE_KEY,
            )
        assert receipt.index_sets == (2,)
        assert receipt.shares_redeemed == 200

    @pytest.mark.asyncio
    async def test_redeem_both_uses_pair_dry_run(self):
        w3 = make_mock_web3()
        redeemer = make_redeemer(w3, dry_run=True)
        receipt = await redeemer.redeem(
            condition_id=VALID_CONDITION_ID,
            shares_yes=10, shares_no=10,
            redeem_op_id="op-real-pair",
        )
        assert receipt.index_sets == (1, 2)
        assert receipt.shares_redeemed == 20


class TestRedeemErrors:

    @pytest.mark.asyncio
    async def test_invalid_condition_id_raises(self):
        w3 = make_mock_web3()
        redeemer = make_redeemer(w3, dry_run=True)
        with pytest.raises(CTFRedeemError, match="condition_id inv"):
            await redeemer.redeem(
                condition_id="0xdeadbeef",  # muy corto
                shares_yes=10, shares_no=0,
                redeem_op_id="op-bad-cid",
            )

    @pytest.mark.asyncio
    async def test_adapter_paused_raises(self):
        w3 = make_mock_web3(adapter_code=b"")  # adaptador pausado
        redeemer = make_redeemer(w3, dry_run=True)
        with pytest.raises(CTFAdapterPausedError, match="sin c"):
            await redeemer.redeem(
                condition_id=VALID_CONDITION_ID,
                shares_yes=10, shares_no=0,
                redeem_op_id="op-paused",
            )

    @pytest.mark.asyncio
    async def test_matic_empty_preflight_raises(self):
        w3 = make_mock_web3(matic_wei=10_000_000_000_000_000)  # 0.01 MATIC
        redeemer = make_redeemer(w3, dry_run=True)
        with pytest.raises(InsufficientGasError):
            await redeemer.redeem(
                condition_id=VALID_CONDITION_ID,
                shares_yes=10, shares_no=0,
                redeem_op_id="op-no-matic",
            )

    @pytest.mark.asyncio
    async def test_real_redeem_without_private_key_raises(self):
        w3 = make_mock_web3()
        redeemer = make_redeemer(w3, dry_run=False)
        with pytest.raises(CTFRedeemError):
            await redeemer.redeem(
                condition_id=VALID_CONDITION_ID,
                shares_yes=10, shares_no=0,
                redeem_op_id="op-no-key",
            )


# ══════════════════════════════════════════════════════════════════════
# TestWaitForFinality — receipt + confirmation loop
# ══════════════════════════════════════════════════════════════════════

class TestWaitForFinality:

    @pytest.mark.asyncio
    async def test_confirmed_after_64_blocks(self):
        w3 = make_mock_web3()
        redeemer = make_redeemer(w3, confirmations=DEFAULT_CONFIRMATIONS)
        # block_height_of_receipt = 12345; side_effect consume un valor por cada `await`
        w3.eth.block_number = _AwaitableValue(side_effect=[
            SHALLOW_BLOCK,                          # 1 conf, <64 → wait
            RECEIPT_BLOCK + 5,                      # 5 conf, <64 → wait
            CONFIRMED_BLOCK,                        # 65 conf → CONFIRMED
        ])
        receipt = await redeemer.wait_for_finality(
            tx_hash="0x" + "cc" * 32,
            redeem_op_id="op-finality-ok",
            condition_id=VALID_CONDITION_ID,
            index_sets=(1,),
            shares_yes=10, shares_no=0,
            submitted_at=datetime.now(timezone.utc),
            gas_limit=240_000,
            gas_prices={"max_fee": 60_000_000_000, "max_priority": 1_500_000_000},
        )
        assert receipt.status == FinalityStatus.CONFIRMED.value
        assert receipt.gas_used == 250_000

    @pytest.mark.asyncio
    async def test_mempool_timeout_raises(self):
        from web3.exceptions import TimeExhausted

        w3 = make_mock_web3()
        w3.eth.wait_for_transaction_receipt = AsyncMock(
            side_effect=TimeExhausted("mempool > 600s"),
        )
        redeemer = make_redeemer(
            w3, confirmations=DEFAULT_CONFIRMATIONS, timeout_seconds=1,
        )
        with pytest.raises(FinalityTimeoutError, match="no min"):
            await redeemer.wait_for_finality(
                tx_hash="0x" + "cc" * 32,
                redeem_op_id="op-timeout",
                condition_id=VALID_CONDITION_ID,
                index_sets=(1,),
                shares_yes=10, shares_no=0,
                submitted_at=datetime.now(timezone.utc),
                gas_limit=240_000,
                gas_prices={"max_fee": 60_000_000_000, "max_priority": 1_500_000_000},
            )

    @pytest.mark.asyncio
    async def test_reverted_onchain_raises(self):
        w3 = make_mock_web3()
        w3.eth.wait_for_transaction_receipt = AsyncMock(return_value={
            "status": 0,  # reverted
            "blockNumber": RECEIPT_BLOCK,
            "gasUsed": 100_000,
        })
        redeemer = make_redeemer(w3)
        with pytest.raises(RedeemExecutionError, match="REVERTIDA"):
            await redeemer.wait_for_finality(
                tx_hash="0x" + "cc" * 32,
                redeem_op_id="op-revert",
                condition_id=VALID_CONDITION_ID,
                index_sets=(1,),
                shares_yes=10, shares_no=0,
                submitted_at=datetime.now(timezone.utc),
                gas_limit=240_000,
                gas_prices={"max_fee": 60_000_000_000, "max_priority": 1_500_000_000},
            )

    @pytest.mark.asyncio
    async def test_confirmation_loop_times_out(self):
        w3 = make_mock_web3()
        # block_number estático en sólo 1 conf; nunca llega a 64
        w3.eth.block_number.return_value = SHALLOW_BLOCK
        redeemer = make_redeemer(
            w3, confirmations=DEFAULT_CONFIRMATIONS, timeout_seconds=0,
        )
        with pytest.raises(FinalityTimeoutError, match="depth="):
            await redeemer.wait_for_finality(
                tx_hash="0x" + "cc" * 32,
                redeem_op_id="op-finality-loop",
                condition_id=VALID_CONDITION_ID,
                index_sets=(1,),
                shares_yes=10, shares_no=0,
                submitted_at=datetime.now(timezone.utc),
                gas_limit=240_000,
                gas_prices={"max_fee": 60_000_000_000, "max_priority": 1_500_000_000},
            )


# ══════════════════════════════════════════════════════════════════════
# TestReplaceTxIfStuck — tx replacement
# ══════════════════════════════════════════════════════════════════════

class TestReplaceTxIfStuck:

    @pytest.mark.asyncio
    async def test_bumps_gas_15pct(self):
        w3 = make_mock_web3()
        redeemer = make_redeemer(w3, gas_bump_pct=0.15)
        original = {
            "maxFeePerGas":          60_000_000_000,
            "maxPriorityFeePerGas":  1_500_000_000,
            "nonce":                 7,
        }
        with patch.object(
            cr_module.Account, "sign_transaction",
            return_value=MagicMock(raw_transaction=b"\xab" * 120),
        ):
            new_hash = await redeemer.replace_tx_if_stuck(
                original_tx=original,
                private_key=DUMMY_PRIVATE_KEY,
            )
        assert w3.eth.send_raw_transaction.await_count == 1
        assert new_hash.startswith("0x")

    @pytest.mark.asyncio
    async def test_preserves_nonce(self):
        w3 = make_mock_web3()
        redeemer = make_redeemer(w3)
        original = {
            "maxFeePerGas":          100_000_000_000,
            "maxPriorityFeePerGas":  2_000_000_000,
            "nonce":                 42,
        }
        signed_nonces = []

        def capture_sign(tx_params, private_key):
            signed_nonces.append(tx_params["nonce"])
            return MagicMock(raw_transaction=b"\xab" * 120)

        with patch.object(
            cr_module.Account, "sign_transaction", side_effect=capture_sign,
        ):
            await redeemer.replace_tx_if_stuck(
                original_tx=original,
                private_key=DUMMY_PRIVATE_KEY,
            )
        # Same nonce → idempotencia preservada
        assert signed_nonces == [42]

    @pytest.mark.asyncio
    async def test_returns_distinct_tx_hash(self):
        w3 = make_mock_web3()
        w3.eth.send_raw_transaction = AsyncMock(
            return_value=bytes.fromhex("d1d2d3d4" * 8),
        )
        redeemer = make_redeemer(w3)
        with patch.object(
            cr_module.Account, "sign_transaction",
            return_value=MagicMock(raw_transaction=b"\xab" * 120),
        ):
            new_hash = await redeemer.replace_tx_if_stuck(
                original_tx={"maxFeePerGas": 60_000_000_000,
                             "maxPriorityFeePerGas": 1_500_000_000,
                             "nonce": 5},
                private_key=DUMMY_PRIVATE_KEY,
            )
        assert new_hash == "0x" + "d1d2d3d4" * 8


# ══════════════════════════════════════════════════════════════════════
# TestReconcileOnStartup
# ══════════════════════════════════════════════════════════════════════

class TestReconcileOnStartup:

    @pytest.mark.asyncio
    async def test_confirms_receipt_with_enough_confirmations(self):
        w3 = make_mock_web3()
        w3.eth.block_number.return_value = CONFIRMED_BLOCK
        redeemer = make_redeemer(w3)
        ops = [{
            "redeem_op_id":  "op-recon-ok",
            "condition_id":  VALID_CONDITION_ID,
            "tx_hash":       "0x" + "ee" * 32,
            "index_sets":    [1],
            "submitted_at":  datetime.now(timezone.utc),
            "shares_yes":    10, "shares_no": 0,
            "pusd_received": None,
        }]
        results = await redeemer.reconcile_on_startup(pending_ops=ops)
        assert len(results) == 1
        assert results[0].status == FinalityStatus.CONFIRMED.value

    @pytest.mark.asyncio
    async def test_skips_pendings_below_threshold(self):
        w3 = make_mock_web3()
        w3.eth.block_number.return_value = SHALLOW_BLOCK  # 1 conf < 64
        redeemer = make_redeemer(w3)
        ops = [{
            "redeem_op_id": "op-recon-too-shallow",
            "condition_id": VALID_CONDITION_ID,
            "tx_hash":      "0x" + "ee" * 32,
            "index_sets":   [1],
            "submitted_at": datetime.now(timezone.utc),
            "shares_yes":   10, "shares_no": 0,
        }]
        results = await redeemer.reconcile_on_startup(pending_ops=ops)
        assert results == []  # No confirmado aún

    @pytest.mark.asyncio
    async def test_skips_missing_tx_hash(self):
        w3 = make_mock_web3()
        redeemer = make_redeemer(w3)
        ops = [{
            "redeem_op_id": "op-recon-no-hash",
            "condition_id": VALID_CONDITION_ID,
            "tx_hash":      None,
            "index_sets":   [1],
        }]
        results = await redeemer.reconcile_on_startup(pending_ops=ops)
        assert results == []


# ══════════════════════════════════════════════════════════════════════
# TestNormalizeConditionId
# ══════════════════════════════════════════════════════════════════════

class TestNormalizeConditionId:

    def test_str_hex_returns_32_bytes(self):
        result = CTFRedeemer._normalize_condition_id(VALID_CONDITION_ID)
        assert result == VALID_CONDITION_BYTES
        assert isinstance(result, bytes)
        assert len(result) == 32

    def test_bytes_input_returns_same(self):
        result = CTFRedeemer._normalize_condition_id(VALID_CONDITION_BYTES)
        assert result == VALID_CONDITION_BYTES

    def test_invalid_length_str_raises(self):
        with pytest.raises(CTFRedeemError, match="inv"):
            CTFRedeemer._normalize_condition_id("0xabcd")

    def test_no_0x_prefix_raises(self):
        bad = "ab" * 32  # sin 0x
        with pytest.raises(CTFRedeemError, match="inv"):
            CTFRedeemer._normalize_condition_id(bad)


# ══════════════════════════════════════════════════════════════════════
# TestMaskAddr
# ══════════════════════════════════════════════════════════════════════

class TestMaskAddr:

    def test_long_address_masked(self):
        addr = "0x" + "12" * 20
        # _mask_addr formato: addr[:6] + '...' + addr[-4:] (usado en logs del init).
        assert _mask_addr(addr) == "0x1212...1212"

    def test_short_address_returns_stars(self):
        assert _mask_addr("0xshort") == "***"

    def test_empty_address_returns_stars(self):
        assert _mask_addr("") == "***"
