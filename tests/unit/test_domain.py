# tests/unit/test_domain.py
"""Tests unitarios para las entidades y value objects del dominio."""

from datetime import datetime, timedelta

import pytest

from src.domain.entities.market import Market
from src.domain.entities.order import Order
from src.domain.entities.position import Position
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.order_side import OrderSide
from src.domain.enums.order_status import OrderStatus
from src.domain.enums.signal_type import SignalType
from src.domain.enums.trading_mode import TradingMode
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.risk_decision import RiskDecision
from src.domain.value_objects.signal import Signal
from src.domain.value_objects.trade_result import TradeResult

# ── Helpers ────────────────────────────────────────────────────────────

def make_market(**kwargs) -> Market:
    defaults = dict(
        id="market_001",
        asset=Asset.BTC,
        window=Window.M5,
        question="Will BTC be above $100k at end of day?",
        status=MarketStatus.ACTIVE,
        yes_token_id="yes_token_001",
        no_token_id="no_token_001",
        yes_price=0.65,
        no_price=0.35,
        volume_24h=5000.0,
        expiry=datetime.utcnow() + timedelta(hours=2),
    )
    defaults.update(kwargs)
    return Market(**defaults)


def make_tick(**kwargs) -> MarketTick:
    defaults = dict(
        market_id="market_001",
        yes_price=0.65,
        no_price=0.35,
        best_bid=0.64,
        best_ask=0.66,
        spread=0.02,
        volume_24h=5000.0,
        timestamp=datetime.utcnow(),
    )
    defaults.update(kwargs)
    return MarketTick(**defaults)


# ── Market ─────────────────────────────────────────────────────────────

class TestMarket:
    def test_market_creation(self):
        """Market se crea correctamente con todos los campos."""
        m = make_market()
        assert m.id == "market_001"
        assert m.asset == Asset.BTC
        assert m.window == Window.M5
        assert m.yes_price == 0.65
        assert m.no_price == 0.35
        assert m.volume_24h == 5000.0
        assert m.status == MarketStatus.ACTIVE

    def test_market_is_active_when_active_and_not_expired(self):
        """is_active=True solo si status=ACTIVE y no ha expirado."""
        m = make_market(expiry=datetime.utcnow() + timedelta(hours=1))
        assert m.is_active() is True

    def test_market_is_not_active_when_expired(self):
        """is_active=False si el mercado ya expiró."""
        m = make_market(expiry=datetime.utcnow() - timedelta(minutes=5))
        assert m.is_active() is False

    def test_market_is_not_active_when_not_active_status(self):
        """is_active=False si status != ACTIVE aunque no haya expirado."""
        m = make_market(status=MarketStatus.RESOLVED)
        assert m.is_active() is False

    def test_market_minutes_to_expiry_positive(self):
        """minutes_to_expiry devuelve minutos positivos si no ha expirado."""
        m = make_market(expiry=datetime.utcnow() + timedelta(minutes=30))
        assert 29.0 <= m.minutes_to_expiry() <= 31.0

    def test_market_minutes_to_expiry_zero_when_expired(self):
        """minutes_to_expiry devuelve 0 si ya expiró."""
        m = make_market(expiry=datetime.utcnow() - timedelta(hours=1))
        assert m.minutes_to_expiry() == 0.0

    def test_market_update_prices(self):
        """update_prices actualiza yes_price, no_price, volume y updated_at."""
        m = make_market()
        old_updated = m.updated_at
        m.update_prices(yes_price=0.70, no_price=0.30, volume=6000.0)
        assert m.yes_price == 0.70
        assert m.no_price == 0.30
        assert m.volume_24h == 6000.0
        assert m.updated_at >= old_updated


# ── MarketTick ─────────────────────────────────────────────────────────

class TestMarketTick:
    def test_market_tick_is_immutable(self):
        """MarketTick es frozen — no se puede modificar tras crearlo."""
        tick = make_tick()
        with pytest.raises(Exception):
            tick.yes_price = 0.99  # type: ignore

    def test_is_liquid_enough_true(self):
        """is_liquid_enough=True si volume > 0 y spread < 1.0."""
        tick = make_tick(spread=0.02, volume_24h=5000.0)
        assert tick.is_liquid_enough is True

    def test_is_liquid_enough_false_zero_volume(self):
        """is_liquid_enough=False si volume_24h == 0."""
        tick = make_tick(spread=0.02, volume_24h=0.0)
        assert tick.is_liquid_enough is False

    def test_is_liquid_enough_false_max_spread(self):
        """is_liquid_enough=False si spread >= 1.0."""
        tick = make_tick(spread=1.0, volume_24h=5000.0)
        assert tick.is_liquid_enough is False

    def test_mid_price(self):
        """mid_price es el promedio entre best_bid y best_ask."""
        tick = make_tick(best_bid=0.60, best_ask=0.64)
        assert tick.mid_price == pytest.approx(0.62)


# ── Signal ─────────────────────────────────────────────────────────────

class TestSignal:
    def test_signal_creation(self):
        """Signal se crea correctamente con todos los campos."""
        now = datetime.utcnow()
        s = Signal(
            type=SignalType.BUY_YES,
            market_id="market_001",
            confidence=0.8,
            source_strategy="TestStrategy",
            reason="test signal",
            timestamp=now,
        )
        assert s.type == SignalType.BUY_YES
        assert s.confidence == 0.8
        assert s.source_strategy == "TestStrategy"

    def test_signal_is_actionable_for_buy_yes(self):
        s = Signal(
            type=SignalType.BUY_YES,
            market_id="market_001",
            confidence=0.8,
            source_strategy="Test",
            reason="test",
            timestamp=datetime.utcnow(),
        )
        assert s.is_actionable() is True

    def test_signal_not_actionable_for_hold(self):
        s = Signal(
            type=SignalType.HOLD,
            market_id="market_001",
            confidence=0.0,
            source_strategy="Test",
            reason="no signal",
            timestamp=datetime.utcnow(),
        )
        assert s.is_actionable() is False

    def test_signal_is_actionable_for_exit(self):
        s = Signal(
            type=SignalType.EXIT,
            market_id="market_001",
            confidence=1.0,
            source_strategy="Test",
            reason="stop loss",
            timestamp=datetime.utcnow(),
        )
        assert s.is_actionable() is True

    def test_signal_immutable(self):
        """Signal es frozen — no se puede modificar tras crearlo."""
        s = Signal(
            type=SignalType.BUY_YES,
            market_id="market_001",
            confidence=0.5,
            source_strategy="Test",
            reason="test",
            timestamp=datetime.utcnow(),
        )
        with pytest.raises(Exception):
            s.confidence = 0.9  # type: ignore


# ── RiskDecision ───────────────────────────────────────────────────────

class TestRiskDecision:
    def test_risk_decision_allowed(self):
        """RiskDecision.allowed=True con suggested_amount."""
        rd = RiskDecision(
            allowed=True,
            reason="all good",
            rule_triggered="TestRule",
            suggested_amount=50.0,
        )
        assert rd.allowed is True
        assert rd.suggested_amount == 50.0

    def test_risk_decision_denied(self):
        """RiskDecision.allowed=False sin suggested_amount."""
        rd = RiskDecision(
            allowed=False,
            reason="drawdown exceeded",
            rule_triggered="DrawdownRule",
        )
        assert rd.allowed is False
        assert rd.suggested_amount is None

    def test_risk_decision_immutable(self):
        """RiskDecision es frozen."""
        rd = RiskDecision(allowed=True, reason="ok", rule_triggered="R")
        with pytest.raises(Exception):
            rd.allowed = False  # type: ignore


# ── TradeResult ────────────────────────────────────────────────────────

class TestTradeResult:
    def test_trade_result_creation_success(self):
        """TradeResult para una orden exitosa."""
        now = datetime.utcnow()
        tr = TradeResult(
            order_id="order_001",
            market_id="market_001",
            side="YES",
            amount=10.0,
            target_price=0.65,
            fill_price=0.66,
            slippage=0.01,
            pnl=None,
            success=True,
            mode="paper",
            timestamp=now,
        )
        assert tr.success is True
        assert tr.side == "YES"
        assert tr.fill_price == 0.66
        assert tr.slippage == 0.01
        assert tr.error is None

    def test_trade_result_creation_failure(self):
        """TradeResult para una orden fallida."""
        now = datetime.utcnow()
        tr = TradeResult(
            order_id="order_002",
            market_id="market_001",
            side="YES",
            amount=10.0,
            target_price=0.65,
            fill_price=0.65,
            slippage=0.0,
            pnl=None,
            success=False,
            mode="real",
            timestamp=now,
            error="Circuit breaker open",
        )
        assert tr.success is False
        assert tr.error == "Circuit breaker open"

    def test_trade_result_immutable(self):
        """TradeResult es frozen."""
        now = datetime.utcnow()
        tr = TradeResult(
            order_id="o", market_id="m", side="YES",
            amount=10.0, target_price=0.5, fill_price=0.5,
            slippage=0.0, pnl=None, success=True,
            mode="paper", timestamp=now,
        )
        with pytest.raises(Exception):
            tr.success = False  # type: ignore


# ── Order ──────────────────────────────────────────────────────────────

class TestOrder:
    def test_order_creation_pending(self):
        """Order se crea en estado PENDING."""
        o = Order(
            id="order_001",
            market_id="market_001",
            side=OrderSide.YES,
            amount=10.0,
            target_price=0.65,
            fill_price=None,
            slippage=None,
            status=OrderStatus.PENDING,
            mode=TradingMode.PAPER,
            strategy="BuyAboveThreshold",
            reason="price above threshold",
        )
        assert o.status == OrderStatus.PENDING
        assert o.fill_price is None
        assert o.filled_at is None

    def test_order_mark_filled(self):
        """mark_filled actualiza estado, precio, slippage y timestamp."""
        o = Order(
            id="order_001",
            market_id="market_001",
            side=OrderSide.YES,
            amount=10.0,
            target_price=0.65,
            fill_price=None,
            slippage=None,
            status=OrderStatus.PENDING,
            mode=TradingMode.PAPER,
            strategy="Test",
            reason="test",
        )
        o.mark_filled(fill_price=0.66, slippage=0.01)
        assert o.status == OrderStatus.FILLED
        assert o.fill_price == 0.66
        assert o.slippage == 0.01
        assert o.filled_at is not None

    def test_order_mark_failed(self):
        """mark_failed actualiza estado y error."""
        o = Order(
            id="order_001",
            market_id="market_001",
            side=OrderSide.YES,
            amount=10.0,
            target_price=0.65,
            fill_price=None,
            slippage=None,
            status=OrderStatus.PENDING,
            mode=TradingMode.PAPER,
            strategy="Test",
            reason="test",
        )
        o.mark_failed("Network timeout")
        assert o.status == OrderStatus.FAILED
        assert o.error == "Network timeout"

    def test_order_shares_calculation(self):
        """shares = amount / fill_price."""
        o = Order(
            id="order_001",
            market_id="market_001",
            side=OrderSide.YES,
            amount=10.0,
            target_price=0.65,
            fill_price=0.50,
            slippage=0.01,
            status=OrderStatus.FILLED,
            mode=TradingMode.PAPER,
            strategy="Test",
            reason="test",
        )
        assert o.shares == 20.0  # 10 / 0.50 = 20

    def test_order_shares_none_when_no_fill_price(self):
        """shares=None si fill_price es None o 0."""
        o = Order(
            id="order_001",
            market_id="market_001",
            side=OrderSide.YES,
            amount=10.0,
            target_price=0.65,
            fill_price=None,
            slippage=None,
            status=OrderStatus.PENDING,
            mode=TradingMode.PAPER,
            strategy="Test",
            reason="test",
        )
        assert o.shares is None


# ── Position ───────────────────────────────────────────────────────────

class TestPosition:
    def test_position_open(self):
        """Position.is_open=True cuando closed_at es None."""
        p = Position(
            id="pos_001",
            market_id="market_001",
            asset="BTC",
            window="5m",
            side="YES",
            amount=10.0,
            shares=20.0,
            entry_price=0.50,
            exit_price=None,
            pnl=None,
            pnl_pct=None,
            mode="paper",
            strategy="Test",
            exit_reason=None,
        )
        assert p.is_open is True
        assert p.pnl is None

    def test_position_calculate_unrealized_pnl(self):
        """PnL no realizado = (current - entry) * shares."""
        p = Position(
            id="pos_001",
            market_id="market_001",
            asset="BTC",
            window="5m",
            side="YES",
            amount=10.0,
            shares=20.0,
            entry_price=0.50,
            exit_price=None,
            pnl=None,
            pnl_pct=None,
            mode="paper",
            strategy="Test",
            exit_reason=None,
        )
        unrealized = p.calculate_unrealized_pnl(current_price=0.60)
        assert unrealized == pytest.approx(2.0)  # (0.60 - 0.50) * 20 = 2.0

    def test_position_calculate_unrealized_pnl_pct(self):
        """PnL% no realizado sobre amount."""
        p = Position(
            id="pos_001",
            market_id="market_001",
            asset="BTC",
            window="5m",
            side="YES",
            amount=10.0,
            shares=20.0,
            entry_price=0.50,
            exit_price=None,
            pnl=None,
            pnl_pct=None,
            mode="paper",
            strategy="Test",
            exit_reason=None,
        )
        pnl_pct = p.calculate_unrealized_pnl_pct(current_price=0.60)
        assert pnl_pct == pytest.approx(0.20)  # 20%

    def test_position_close_calculates_pnl(self):
        """close() calcula PnL realizado y marca como cerrada."""
        p = Position(
            id="pos_001",
            market_id="market_001",
            asset="BTC",
            window="5m",
            side="YES",
            amount=10.0,
            shares=20.0,
            entry_price=0.50,
            exit_price=None,
            pnl=None,
            pnl_pct=None,
            mode="paper",
            strategy="Test",
            exit_reason=None,
        )
        p.close(exit_price=0.70, reason="target_reached")
        assert p.is_open is False
        assert p.exit_price == 0.70
        assert p.pnl == pytest.approx(4.0)  # (0.70 - 0.50) * 20 = 4.0
        assert p.pnl_pct == pytest.approx(0.40)  # 40%
        assert p.exit_reason == "target_reached"
        assert p.closed_at is not None

    def test_position_close_negative_pnl(self):
        """close() calcula PnL negativo cuando exit < entry."""
        p = Position(
            id="pos_001",
            market_id="market_001",
            asset="BTC",
            window="5m",
            side="YES",
            amount=10.0,
            shares=20.0,
            entry_price=0.50,
            exit_price=None,
            pnl=None,
            pnl_pct=None,
            mode="paper",
            strategy="Test",
            exit_reason=None,
        )
        p.close(exit_price=0.40, reason="stop_loss")
        assert p.pnl == pytest.approx(-2.0)  # (0.40 - 0.50) * 20 = -2.0
        assert p.pnl_pct == pytest.approx(-0.20)  # -20%
