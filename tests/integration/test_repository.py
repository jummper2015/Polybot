# tests/integration/test_repository.py

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.market import Market
from src.domain.entities.order import Order
from src.domain.entities.position import Position
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.order_side import OrderSide
from src.domain.enums.order_status import OrderStatus
from src.domain.enums.trading_mode import TradingMode
from src.domain.enums.window import Window
from src.infrastructure.db.models import AuditLogModel, MarketModel, OrderModel, PositionModel
from src.infrastructure.db.repository import SQLAlchemyRepository

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_market(
    market_id: str = "repo_test_market",
    asset: Asset = Asset.BTC,
    window: Window = Window.M5,
) -> Market:
    return Market(
        id=market_id,
        asset=asset,
        window=window,
        question=f"Will {asset.value} price exceed?",
        status=MarketStatus.ACTIVE,
        yes_token_id=f"yes_{market_id}",
        no_token_id=f"no_{market_id}",
        yes_price=0.76,
        no_price=0.24,
        volume_24h=5000.0,
        expiry=datetime.utcnow() + timedelta(hours=2),
    )


def _make_order(
    order_id: str = "order_1",
    market_id: str = "repo_test_market",
) -> Order:
    return Order(
        id=order_id,
        market_id=market_id,
        side=OrderSide.YES,
        amount=10.0,
        target_price=0.82,
        fill_price=0.83,
        slippage=0.01,
        status=OrderStatus.FILLED,
        mode=TradingMode.PAPER,
        strategy="BuyAboveThreshold",
        reason="test_entry",
        created_at=datetime.utcnow(),
        filled_at=datetime.utcnow(),
    )


def _make_position(
    position_id: str = "pos_1",
    market_id: str = "repo_test_market",
    is_open: bool = True,
) -> Position:
    return Position(
        id=position_id,
        market_id=market_id,
        asset="BTC",
        window="5m",
        side="YES",
        amount=10.0,
        shares=12.0,
        entry_price=0.83,
        exit_price=None if is_open else 0.88,
        pnl=None if is_open else 0.60,
        pnl_pct=None if is_open else 0.06,
        mode="paper",
        strategy="BuyAboveThreshold",
        exit_reason=None if is_open else "target_reached",
        closed_at=None if is_open else datetime.utcnow(),
    )


# ── Repository fixture with mocked DB session ───────────────────────────


@pytest.fixture
def mock_session():
    """Crea una AsyncSession mockeada para testing del repository."""
    s = AsyncMock()
    s.get = AsyncMock()
    s.execute = AsyncMock()
    s.add = MagicMock()
    s.begin = MagicMock()

    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=None)
    s.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    s.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    return s


@pytest.fixture
def mock_session_factory(mock_session):
    """Crea un async_sessionmaker mockeado."""
    factory = MagicMock()
    factory.return_value = mock_session
    return factory


@pytest.fixture
def repo(mock_session_factory):
    """Repository con session factory mockeada."""
    return SQLAlchemyRepository(session_factory=mock_session_factory)


# ── Tests ────────────────────────────────────────────────────────────────


class TestRepositoryMarkets:

    @pytest.mark.asyncio
    async def test_save_market_inserts_new(self, repo, mock_session):
        """save_market inserta un mercado nuevo cuando no existe en DB."""
        market = _make_market()

        # Mock: get devuelve None → mercado no existe → INSERT
        mock_session.get = AsyncMock(return_value=None)

        result = await repo.save_market(market)

        mock_session.add.assert_called_once()
        assert result.id == market.id
        assert result.asset == market.asset
        assert result.window == market.window

    @pytest.mark.asyncio
    async def test_save_market_updates_existing(self, repo, mock_session):
        """save_market actualiza un mercado existente (upsert)."""
        market = _make_market()

        # Mock: get devuelve un modelo existente → UPDATE
        existing_model = MagicMock(spec=MarketModel)
        existing_model.yes_price = 0.70
        existing_model.status = "active"
        mock_session.get = AsyncMock(return_value=existing_model)

        await repo.save_market(market)

        mock_session.add.assert_not_called()
        assert existing_model.yes_price == market.yes_price
        assert existing_model.volume_24h == market.volume_24h


class TestRepositoryOrders:

    @pytest.mark.asyncio
    async def test_save_order_inserts_new(self, repo, mock_session):
        """save_order inserta una orden nueva cuando no existe."""
        order = _make_order()
        mock_session.get = AsyncMock(return_value=None)

        result = await repo.save_order(order)
        mock_session.add.assert_called_once()
        assert result.id == order.id
        assert result.side == OrderSide.YES

    @pytest.mark.asyncio
    async def test_save_order_updates_status(self, repo, mock_session):
        """save_order actualiza estado de orden existente."""
        order = _make_order()
        order.status = OrderStatus.CANCELLED
        order.error = "user_cancelled"

        existing_model = MagicMock(spec=OrderModel)
        existing_model.status = "filled"
        mock_session.get = AsyncMock(return_value=existing_model)

        await repo.save_order(order)

        mock_session.add.assert_not_called()
        assert existing_model.status == "cancelled"
        assert existing_model.error == "user_cancelled"

    # ── R2.2.2 (Ola 1.1) — idempotency_key mapping ─────────────────────

    def test_order_to_model_persists_idempotency_key(self, repo):
        """_order_to_model DEBE copiar idempotency_key al ORM model
        (sin esto, el UNIQUE ix_orders_idempotency no bloquea duplicados).
        """
        order = _make_order()
        order.idempotency_key = "abc123def456ffff"

        model = repo._order_to_model(order)
        assert model.idempotency_key == "abc123def456ffff"

    def test_model_to_order_round_trips_idempotency_key(self, repo):
        """_model_to_order DEBE recuperar idempotency_key (simetría con
        _order_to_model). Sin esto no se puede leer la key desde BD."""
        model = MagicMock(spec=OrderModel)
        model.id            = "order_x"
        model.market_id     = "m_x"
        model.side          = "YES"
        model.amount        = 5.0
        model.target_price  = 0.5
        model.fill_price    = 0.5
        model.slippage      = 0.0
        model.status        = "filled"
        model.mode          = "paper"
        model.strategy      = "MR"
        model.reason        = "test"
        model.error         = None
        model.idempotency_key = "0011223344556677"
        model.created_at    = datetime.utcnow()
        model.filled_at     = datetime.utcnow()

        order = repo._model_to_order(model)
        assert order.idempotency_key == "0011223344556677"

    # ── R2.2.2 (Ola 1.2) — IntegrityError handler on collision ─────────

    @pytest.mark.asyncio
    async def test_save_order_handles_idempotency_key_collision(
        self, repo, mock_session, mock_session_factory
    ):
        """Cuando dos flujos concurrentes intentan guardar órdenes con la
        misma idempotency_key (race post-timeout), el segundo INSERT debe
        levantar IntegrityError sobre ix_orders_idempotency; save_order
        DEBE catchearlo, no relanzar, y devolver la orden ya persistida.
        """
        from sqlalchemy.exc import IntegrityError

        order = _make_order()
        order.idempotency_key = "collision_key_01"

        # Simula: session.get devuelve None (por ID no existe), pero al
        # hacer session.add + commit dispara IntegrityError sobre el UNIQUE.
        mock_session.get = AsyncMock(return_value=None)

        # El commit dentro de session.begin() levanta el error
        mock_session.begin.return_value.__aexit__ = AsyncMock(
            side_effect=IntegrityError(
                "INSERT INTO orders",
                params=None,
                orig=Exception(
                    "duplicate key value violates unique constraint "
                    "\"ix_orders_idempotency\""
                ),
            )
        )

        # Preparamos una 2ª sesión para el re-fetch (segunda entrada al
        # session_factory) — devuelve la orden ganadora.
        existing_orm = MagicMock(spec=OrderModel)
        existing_orm.id            = "order_winner"
        existing_orm.market_id     = order.market_id
        existing_orm.side          = "YES"
        existing_orm.amount        = order.amount
        existing_orm.target_price  = order.target_price
        existing_orm.fill_price    = 0.5
        existing_orm.slippage      = 0.0
        existing_orm.status        = "filled"
        existing_orm.mode          = "paper"
        existing_orm.strategy      = "BuyAboveThreshold"
        existing_orm.reason        = "test_entry"
        existing_orm.error         = None
        existing_orm.idempotency_key = "collision_key_01"
        existing_orm.created_at    = datetime.utcnow()
        existing_orm.filled_at     = datetime.utcnow()

        refetch_session = AsyncMock()
        refetch_session.__aenter__ = AsyncMock(return_value=refetch_session)
        refetch_session.__aexit__  = AsyncMock(return_value=None)
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = existing_orm
        refetch_session.execute = AsyncMock(return_value=scalar_result)

        # 1ª llamada al factory: mock_session (falla). 2ª: refetch_session.
        mock_session_factory.side_effect = [mock_session, refetch_session]

        result = await repo.save_order(order)

        # No debe crashear; debe devolver la orden ganadora del UNIQUE
        assert result is not None
        assert result.idempotency_key == "collision_key_01"
        assert result.id == "order_winner"


class TestRepositoryPositions:

    @pytest.mark.asyncio
    async def test_save_position_inserts_new(self, repo, mock_session):
        """save_position inserta una posición nueva."""
        position = _make_position()
        mock_session.get = AsyncMock(return_value=None)

        result = await repo.save_position(position)
        mock_session.add.assert_called_once()
        assert result.id == position.id
        assert result.is_open

    @pytest.mark.asyncio
    async def test_save_position_closes_existing(self, repo, mock_session):
        """save_position actualiza posición existente al cerrarla."""
        position = _make_position(is_open=False)

        existing_model = MagicMock(spec=PositionModel)
        existing_model.pnl = None
        existing_model.closed_at = None
        mock_session.get = AsyncMock(return_value=existing_model)

        await repo.save_position(position)

        mock_session.add.assert_not_called()
        assert existing_model.pnl == position.pnl
        assert existing_model.exit_reason == "target_reached"
        assert existing_model.closed_at is not None


class TestRepositoryQueries:

    @pytest.mark.asyncio
    async def test_get_open_positions_count(self, repo, mock_session):
        """get_open_positions_count devuelve el conteo correcto."""
        mock_result = MagicMock()
        mock_result.scalar.return_value = 3
        mock_session.execute = AsyncMock(return_value=mock_result)

        count = await repo.get_open_positions_count()
        assert count == 3

    @pytest.mark.asyncio
    async def test_get_open_positions_count_zero(self, repo, mock_session):
        """get_open_positions_count devuelve 0 cuando no hay posiciones abiertas."""
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        count = await repo.get_open_positions_count()
        assert count == 0

    @pytest.mark.asyncio
    async def test_get_total_pnl_aggregates_correctly(self, repo, mock_session):
        """get_total_pnl suma correctamente el PnL de posiciones cerradas."""
        mock_result = MagicMock()
        mock_result.scalar.return_value = 15.75
        mock_session.execute = AsyncMock(return_value=mock_result)

        total_pnl = await repo.get_total_pnl()
        assert total_pnl == 15.75

    @pytest.mark.asyncio
    async def test_get_total_pnl_with_mode_filter(self, repo, mock_session):
        """get_total_pnl filtra correctamente por modo."""
        mock_result = MagicMock()
        mock_result.scalar.return_value = 8.50
        mock_session.execute = AsyncMock(return_value=mock_result)

        total_pnl = await repo.get_total_pnl(mode="paper")
        assert total_pnl == 8.50
        assert mock_session.execute.called


class TestRepositoryAuditLog:

    @pytest.mark.asyncio
    async def test_save_audit_log_persists_correctly(self, repo, mock_session):
        """save_audit_log persiste una entrada de auditoría correctamente."""
        audit_entry = {
            "audit_action": "real_order_submitted",
            "order_id": "order_42",
            "market_id": "market_abc",
            "amount": 10.0,
            "timestamp": datetime.utcnow().isoformat(),
            "strategy": "BuyAboveThreshold",
            "signal_confidence": 0.85,
        }

        await repo.save_audit_log(audit_entry)

        mock_session.add.assert_called_once()

        # Verificar que el modelo creado tiene los campos correctos
        added_model = mock_session.add.call_args[0][0]
        assert isinstance(added_model, AuditLogModel)
        assert added_model.action == "real_order_submitted"
        assert added_model.order_id == "order_42"
        assert added_model.market_id == "market_abc"
        assert added_model.amount == 10.0

        # Los campos extras deben ir a details (JSONB)
        assert "strategy" in added_model.details
        assert added_model.details["strategy"] == "BuyAboveThreshold"
        assert added_model.details["signal_confidence"] == 0.85

        # audit_action, order_id, market_id, amount, timestamp NO en details
        assert "audit_action" not in added_model.details
        assert "order_id" not in added_model.details
        assert "market_id" not in added_model.details
        assert "amount" not in added_model.details
        assert "timestamp" not in added_model.details

    @pytest.mark.asyncio
    async def test_save_audit_log_minimal_entry(self, repo, mock_session):
        """save_audit_log maneja entradas mínimas (solo audit_action)."""
        audit_entry = {"audit_action": "security_check_passed"}

        await repo.save_audit_log(audit_entry)

        added_model = mock_session.add.call_args[0][0]
        assert added_model.action == "security_check_passed"
        assert added_model.order_id is None
        assert added_model.amount is None
        assert added_model.details == {}
