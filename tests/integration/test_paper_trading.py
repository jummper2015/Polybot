# tests/integration/test_paper_trading.py

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from src.domain.value_objects.signal import Signal, SignalType
from src.domain.entities.position import Position
from src.execution.paper_handler import PaperTradingHandler


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.save_order    = AsyncMock(return_value=None)
    repo.save_position = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get_ws_state       = AsyncMock(return_value={
        "last_yes_price": "0.80",
        "last_spread":    "0.02",
    })
    redis.set_paper_balance  = AsyncMock()
    redis.get_market         = AsyncMock(return_value=MagicMock(
        asset=MagicMock(value="BTC"),
        window=MagicMock(value="5m"),
    ))
    return redis


@pytest.fixture
def mock_notifier():
    notifier = AsyncMock()
    notifier.send_trade_alert = AsyncMock()
    notifier.send_exit_alert  = AsyncMock()
    return notifier


@pytest.fixture
def paper_handler(mock_repo, mock_redis, mock_notifier):
    return PaperTradingHandler(
        repository=mock_repo,
        redis=mock_redis,
        notifier=mock_notifier,
        initial_balance=1000.0,
    )


@pytest.fixture
def buy_yes_signal():
    return Signal(
        type=SignalType.BUY_YES,
        market_id="test_market",
        confidence=0.7,
        source_strategy="BuyAboveThreshold",
        reason="test_entry",
        timestamp=datetime.utcnow(),
    )


class TestPaperTradingHandler:

    @pytest.mark.asyncio
    async def test_execute_entry_reduces_balance(
        self, paper_handler, buy_yes_signal
    ):
        """La entrada reduce el balance en el monto especificado."""
        initial = paper_handler.get_balance()
        result  = await paper_handler.execute_entry(
            signal=buy_yes_signal,
            market_id="test_market",
            amount=10.0,
        )
        assert result.success
        assert paper_handler.get_balance() < initial
        assert abs(paper_handler.get_balance() - (initial - 10.0)) < 0.01

    @pytest.mark.asyncio
    async def test_execute_entry_creates_order_and_position(
        self, paper_handler, buy_yes_signal, mock_repo
    ):
        """La entrada persiste una orden y una posición en el repo."""
        result = await paper_handler.execute_entry(
            signal=buy_yes_signal,
            market_id="test_market",
            amount=10.0,
        )
        assert result.success
        mock_repo.save_order.assert_called_once()
        mock_repo.save_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_entry_fails_insufficient_balance(
        self, paper_handler, buy_yes_signal
    ):
        """Falla si el monto supera el balance disponible."""
        result = await paper_handler.execute_entry(
            signal=buy_yes_signal,
            market_id="test_market",
            amount=5000.0,   # Más que el balance inicial de 1000
        )
        assert not result.success
        assert "balance insuficiente" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_exit_returns_value_to_balance(
        self, paper_handler, mock_repo, mock_redis
    ):
        """El cierre de posición devuelve el valor al balance."""
        # Primero abre una posición
        buy_signal = Signal(
            type=SignalType.BUY_YES, market_id="test_market",
            confidence=0.7, source_strategy="BuyAboveThreshold",
            reason="test", timestamp=datetime.utcnow(),
        )
        await paper_handler.execute_entry(
            signal=buy_signal, market_id="test_market", amount=10.0
        )
        balance_after_entry = paper_handler.get_balance()

        # Crea posición mock para cerrar
        position = Position(
            id="pos_1", market_id="test_market",
            asset="BTC", window="5m", side="YES",
            amount=10.0, shares=12.0, entry_price=0.833,
            exit_price=None, pnl=None, pnl_pct=None,
            mode="paper", strategy="BuyAboveThreshold",
        )

        result = await paper_handler.execute_exit(
            position=position, reason="target_reached"
        )

        assert result.success
        assert paper_handler.get_balance() > balance_after_entry

    @pytest.mark.asyncio
    async def test_slippage_applied_correctly(
        self, paper_handler, buy_yes_signal
    ):
        """El slippage es exactamente spread * 0.5."""
        result = await paper_handler.execute_entry(
            signal=buy_yes_signal,
            market_id="test_market",
            amount=10.0,
        )
        # spread=0.02 → slippage=0.01
        assert result.slippage == pytest.approx(0.01, abs=0.001)


# tests/integration/test_market_service.py

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from src.application.services.market_service import MarketService
from src.domain.entities.market import Market, Asset, Window, MarketStatus


@pytest.fixture
def mock_market_data_port():
    port = AsyncMock()
    port.get_active_markets = AsyncMock(return_value=[
        {
            "condition_id": "0xabc123",
            "question":     "Will BTC exceed price at close?",
            "active":       True,
            "tokens": [
                {"outcome": "Yes", "token_id": "yes_token", "price": "0.76"},
                {"outcome": "No",  "token_id": "no_token",  "price": "0.24"},
            ],
            "volume24hr":    "5000.0",
            "start_date_iso": datetime.utcnow().isoformat(),
            "end_date_iso":   (
                datetime.utcnow() + timedelta(minutes=5)
            ).isoformat(),
        }
    ])
    return port


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.save_market       = AsyncMock(side_effect=lambda m: m)
    repo.get_active_markets = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.set_market          = AsyncMock()
    redis.get_active_markets  = AsyncMock(return_value=[])
    redis.get_market          = AsyncMock(return_value=None)
    return redis


class TestMarketService:

    @pytest.mark.asyncio
    async def test_discover_markets_saves_to_db_and_redis(
        self, mock_market_data_port, mock_repo, mock_redis
    ):
        """El discovery guarda mercados en DB y Redis."""
        service = MarketService(mock_market_data_port, mock_repo, mock_redis)
        markets = await service.discover_markets()

        # Debe haber guardado en ambos
        mock_repo.save_market.assert_called()
        mock_redis.set_market.assert_called()

    @pytest.mark.asyncio
    async def test_discover_filters_by_window_duration(
        self, mock_market_data_port, mock_repo, mock_redis
    ):
        """Solo acepta mercados cuya duración corresponde a 5m o 15m."""
        service = MarketService(mock_market_data_port, mock_repo, mock_redis)
        markets = await service.discover_markets()

        # El mercado de 5 minutos debe ser aceptado
        assert len(markets) > 0
        assert all(m.window in (Window.M5, Window.M15) for m in markets)

    @pytest.mark.asyncio
    async def test_get_active_markets_uses_redis_cache(
        self, mock_market_data_port, mock_repo, mock_redis
    ):
        """Usa Redis como caché antes de ir a la DB."""
        cached_market = MagicMock(spec=Market)
        mock_redis.get_active_markets = AsyncMock(return_value=[cached_market])

        service = MarketService(mock_market_data_port, mock_repo, mock_redis)
        markets = await service.get_active_markets()

        # Debe usar el caché de Redis
        mock_redis.get_active_markets.assert_called_once()
        mock_repo.get_active_markets.assert_not_called()
        assert markets == [cached_market]