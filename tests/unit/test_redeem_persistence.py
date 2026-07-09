"""
Tests de persistencia para redeem_operations (R2.0-redeem-impl F1 Paso 2).

Cubre:
  - TestReceiptModelMappers (roundtrip): _receipt_to_model + _model_to_receipt
  - TestSaveRedeemOperation: INSERT vs UPDATE upsert
  - TestGetRedeemOperation: fetch por UUID
  - TestGetPendingRedeems: query status IN (pending, submitted, mined)
  - TestCheckDuplicateRedeem: guard true/false
  - TestCTFRedeemerPersistenceHooks: pre-tx INSERT + post-tx UPDATE
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.enums.finality_status import FinalityStatus
from src.domain.exceptions.ctf_exceptions import DuplicateRedeemError
from src.domain.value_objects.redeem_receipt import RedeemReceipt
from src.infrastructure.db.models import RedeemOperationModel
from src.infrastructure.db.repository import SQLAlchemyRepository


# ── Fixtures ─────────────────────────────────────────────────────────────

VALID_OP_ID    = "test-op-uuid-1234"
VALID_COND_ID  = "0x" + "ab" * 32
VALID_TX_HASH  = "0x" + "cd" * 32
VALID_PROXY    = "0x" + "11" * 20
VALID_ADAPTER  = "0x" + "22" * 20


def make_receipt(**kwargs) -> RedeemReceipt:
    """Factory con defaults para RedeemReceipt en tests."""
    defaults = dict(
        redeem_op_id=VALID_OP_ID,
        condition_id=VALID_COND_ID,
        tx_hash=VALID_TX_HASH,
        index_sets=(1,),
        shares_redeemed=100,
        pusd_received=100.0,
        gas_used=250_000,
        gas_fee_matic=0.05,
        submitted_at=datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc),
        mined_at=datetime(2026, 7, 9, 12, 0, 30, tzinfo=timezone.utc),
        confirmed_at=datetime(2026, 7, 9, 12, 2, 0, tzinfo=timezone.utc),
        status=FinalityStatus.CONFIRMED.value,
        proxy_address=VALID_PROXY,
        adapter_address=VALID_ADAPTER,
    )
    defaults.update(kwargs)
    return RedeemReceipt(**defaults)


def make_model(**kwargs) -> RedeemOperationModel:
    """Factory para RedeemOperationModel (mock del ORM)."""
    m = MagicMock(spec=RedeemOperationModel)
    m.redeem_op_id    = VALID_OP_ID
    m.condition_id    = VALID_COND_ID
    m.tx_hash         = VALID_TX_HASH
    m.index_sets      = [1]
    m.shares_redeemed = 100
    m.pusd_received   = 100.0
    m.gas_used        = 250_000
    m.gas_fee_matic   = 0.05
    m.submitted_at    = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)
    m.mined_at        = datetime(2026, 7, 9, 12, 0, 30, tzinfo=timezone.utc)
    m.confirmed_at    = datetime(2026, 7, 9, 12, 2, 0, tzinfo=timezone.utc)
    m.status          = FinalityStatus.CONFIRMED.value
    m.proxy_address   = VALID_PROXY
    m.adapter_address = VALID_ADAPTER
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def make_repo_with_mock_session():
    """Repo con session_factory que retorna un mock async context manager."""
    session = AsyncMock()
    # session.begin() debe ser async context manager
    session.begin = MagicMock(return_value=AsyncMock().__aenter__.return_value)
    begin_ctx = MagicMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=session)
    begin_ctx.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_ctx)

    # session_factory() → async context manager de session
    factory_ctx = MagicMock()
    factory_ctx.__aenter__ = AsyncMock(return_value=session)
    factory_ctx.__aexit__ = AsyncMock(return_value=None)

    session_factory = MagicMock(return_value=factory_ctx)
    repo = SQLAlchemyRepository(session_factory=session_factory)
    return repo, session


# ══════════════════════════════════════════════════════════════════════
# TestReceiptModelMappers — roundtrip domain ↔ ORM
# ══════════════════════════════════════════════════════════════════════

class TestReceiptModelMappers:

    def test_receipt_to_model_maps_all_fields(self):
        """_receipt_to_model preserva todos los campos incluyendo tuple → list."""
        repo, _ = make_repo_with_mock_session()
        receipt = make_receipt()
        model = repo._receipt_to_model(receipt)

        assert model.redeem_op_id == VALID_OP_ID
        assert model.condition_id == VALID_COND_ID
        assert model.tx_hash == VALID_TX_HASH
        assert model.index_sets == [1]  # tuple → list JSON
        assert model.shares_redeemed == 100
        assert model.pusd_received == 100.0
        assert model.gas_used == 250_000
        assert model.status == FinalityStatus.CONFIRMED.value
        assert model.proxy_address == VALID_PROXY

    def test_model_to_receipt_maps_all_fields(self):
        """_model_to_receipt preserva todos los campos incluyendo list → tuple."""
        repo, _ = make_repo_with_mock_session()
        model = make_model()
        receipt = repo._model_to_receipt(model)

        assert receipt.redeem_op_id == VALID_OP_ID
        assert receipt.condition_id == VALID_COND_ID
        assert receipt.tx_hash == VALID_TX_HASH
        assert receipt.index_sets == (1,)  # list JSON → tuple
        assert receipt.shares_redeemed == 100
        assert receipt.pusd_received == 100.0
        assert receipt.gas_used == 250_000
        assert receipt.status == FinalityStatus.CONFIRMED.value

    def test_roundtrip_receipt_to_model_to_receipt(self):
        """Roundtrip preserva la identidad del receipt."""
        repo, _ = make_repo_with_mock_session()
        original = make_receipt(index_sets=(1, 2), shares_redeemed=250)
        model = repo._receipt_to_model(original)
        # Model → Receipt (mockeamos porque model creado no persiste)
        m = make_model(index_sets=[1, 2], shares_redeemed=250)
        m.condition_id = model.condition_id
        m.index_sets = list(original.index_sets)
        m.shares_redeemed = original.shares_redeemed
        restored = repo._model_to_receipt(m)

        assert restored.index_sets == original.index_sets
        assert restored.shares_redeemed == original.shares_redeemed
        assert restored.status == original.status

    def test_pending_receipt_maps_none_values(self):
        """Receipt PENDING con tx_hash=None mapea correctamente."""
        repo, _ = make_repo_with_mock_session()
        pending = make_receipt(
            tx_hash=None,
            gas_used=None,
            gas_fee_matic=None,
            submitted_at=None,
            mined_at=None,
            confirmed_at=None,
            pusd_received=0.0,
            status=FinalityStatus.PENDING.value,
        )
        model = repo._receipt_to_model(pending)
        assert model.tx_hash is None
        assert model.gas_used is None
        assert model.status == FinalityStatus.PENDING.value


# ══════════════════════════════════════════════════════════════════════
# TestSaveRedeemOperation — INSERT vs UPDATE
# ══════════════════════════════════════════════════════════════════════

class TestSaveRedeemOperation:

    @pytest.mark.asyncio
    async def test_insert_new_operation_when_not_exists(self):
        """Si session.get retorna None, se hace session.add (INSERT)."""
        repo, session = make_repo_with_mock_session()
        session.get = AsyncMock(return_value=None)  # No existe
        session.add = MagicMock()

        receipt = make_receipt(status=FinalityStatus.PENDING.value)
        await repo.save_redeem_operation(receipt)

        assert session.get.await_count == 1
        session.add.assert_called_once()
        added_model = session.add.call_args[0][0]
        assert added_model.redeem_op_id == VALID_OP_ID
        assert added_model.status == FinalityStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_update_existing_operation(self):
        """Si session.get retorna el modelo, se actualiza (UPDATE)."""
        repo, session = make_repo_with_mock_session()
        existing = make_model(status=FinalityStatus.PENDING.value, tx_hash=None)
        session.get = AsyncMock(return_value=existing)
        session.add = MagicMock()

        # Nuevo receipt con tx_hash y status CONFIRMED
        updated = make_receipt(status=FinalityStatus.CONFIRMED.value)
        await repo.save_redeem_operation(updated)

        # session.add NUNCA se llama en update
        session.add.assert_not_called()
        # El modelo existente se muta con los nuevos valores
        assert existing.tx_hash == VALID_TX_HASH
        assert existing.status == FinalityStatus.CONFIRMED.value


# ══════════════════════════════════════════════════════════════════════
# TestGetRedeemOperation
# ══════════════════════════════════════════════════════════════════════

class TestGetRedeemOperation:

    @pytest.mark.asyncio
    async def test_returns_receipt_when_found(self):
        repo, session = make_repo_with_mock_session()
        session.get = AsyncMock(return_value=make_model())

        result = await repo.get_redeem_operation(VALID_OP_ID)
        assert result is not None
        assert result.redeem_op_id == VALID_OP_ID
        assert result.status == FinalityStatus.CONFIRMED.value

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        repo, session = make_repo_with_mock_session()
        session.get = AsyncMock(return_value=None)

        result = await repo.get_redeem_operation("nonexistent-uuid")
        assert result is None


# ══════════════════════════════════════════════════════════════════════
# TestGetPendingRedeems
# ══════════════════════════════════════════════════════════════════════

class TestGetPendingRedeems:

    @pytest.mark.asyncio
    async def test_returns_list_of_pending_operations(self):
        repo, session = make_repo_with_mock_session()

        # Mock query result: 2 operaciones pending
        m1 = make_model()
        m1.redeem_op_id = "op-1"
        m1.status = FinalityStatus.PENDING.value
        m2 = make_model()
        m2.redeem_op_id = "op-2"
        m2.status = FinalityStatus.SUBMITTED.value

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [m1, m2]
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.get_pending_redeems(limit=100)
        assert len(result) == 2
        assert result[0].redeem_op_id == "op-1"
        assert result[1].redeem_op_id == "op-2"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_pending(self):
        repo, session = make_repo_with_mock_session()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.get_pending_redeems()
        assert result == []


# ══════════════════════════════════════════════════════════════════════
# TestCheckDuplicateRedeem
# ══════════════════════════════════════════════════════════════════════

class TestCheckDuplicateRedeem:

    @pytest.mark.asyncio
    async def test_returns_true_when_duplicate_exists(self):
        repo, session = make_repo_with_mock_session()
        result_mock = MagicMock()
        result_mock.scalar.return_value = 1  # 1 op activa encontrada
        session.execute = AsyncMock(return_value=result_mock)

        assert await repo.check_duplicate_redeem(VALID_COND_ID) is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_duplicate(self):
        repo, session = make_repo_with_mock_session()
        result_mock = MagicMock()
        result_mock.scalar.return_value = 0  # cero ops activas
        session.execute = AsyncMock(return_value=result_mock)

        assert await repo.check_duplicate_redeem(VALID_COND_ID) is False


# ══════════════════════════════════════════════════════════════════════
# TestCTFRedeemerPersistenceHooks — verifica INSERT/UPDATE en flujo
# ══════════════════════════════════════════════════════════════════════

class TestCTFRedeemerPersistenceHooks:
    """Verifica que CTFRedeemer.redeem() llama save_redeem_operation en momentos correctos."""

    @pytest.mark.asyncio
    async def test_dry_run_persists_receipt_when_repo_available(self):
        """Dry run debe persistir el receipt (status=mined o failed) si repo disponible."""
        from src.infrastructure.polymarket import ctf_redeemer as cr_module
        from tests.unit.test_ctf_redeemer import make_mock_web3, make_redeemer, VALID_CONDITION_ID

        w3 = make_mock_web3()
        mock_repo = AsyncMock()
        mock_repo.check_duplicate_redeem = AsyncMock(return_value=False)
        mock_repo.save_redeem_operation = AsyncMock()

        redeemer = make_redeemer(w3, dry_run=True)
        # Inyectar repo post-init (constructor lo acepta, pero make_redeemer no lo pasa)
        redeemer._repo = mock_repo

        await redeemer.redeem(
            condition_id=VALID_CONDITION_ID,
            shares_yes=100, shares_no=0,
            redeem_op_id="op-dry-persist",
        )

        # Dry_run: al menos 2 calls (PENDING pre-preflight + status final tras eth_call)
        assert mock_repo.save_redeem_operation.await_count >= 2

    @pytest.mark.asyncio
    async def test_duplicate_redeem_raises_when_active_exists(self):
        """Si check_duplicate_redeem retorna True, redeem levanta DuplicateRedeemError."""
        from tests.unit.test_ctf_redeemer import make_mock_web3, make_redeemer, VALID_CONDITION_ID

        w3 = make_mock_web3()
        mock_repo = AsyncMock()
        mock_repo.check_duplicate_redeem = AsyncMock(return_value=True)  # ya existe

        redeemer = make_redeemer(w3, dry_run=True)
        redeemer._repo = mock_repo

        with pytest.raises(DuplicateRedeemError, match="ya activa"):
            await redeemer.redeem(
                condition_id=VALID_CONDITION_ID,
                shares_yes=100, shares_no=0,
                redeem_op_id="op-duplicate",
            )
        # No debería llamar save si el guard bloquea
        mock_repo.save_redeem_operation.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_persistence_when_repo_none(self):
        """Sin repo (path legacy tests), no debe intentar guardar."""
        from tests.unit.test_ctf_redeemer import make_mock_web3, make_redeemer, VALID_CONDITION_ID

        w3 = make_mock_web3()
        redeemer = make_redeemer(w3, dry_run=True)
        # _repo default es None; no inyectamos

        receipt = await redeemer.redeem(
            condition_id=VALID_CONDITION_ID,
            shares_yes=50, shares_no=0,
            redeem_op_id="op-no-repo",
        )
        # No hay repo → receipt vuelve, sin errores
        assert receipt.redeem_op_id == "op-no-repo"
