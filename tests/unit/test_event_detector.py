# tests/unit/test_event_detector.py

"""
Unit tests for P11.4 Event-Driven Trading — EventDetector.

Covers:
  - Configuration validation
  - Event detection: price_shock, volume_surge, expiry_proximity, spread_explosion
  - Severity levels: LOW, MEDIUM, HIGH, CRITICAL
  - Cooldown mechanism
  - EventResponse actions: HALT, REDUCE_SIZE, BOOST_CONFIDENCE, ALLOW
  - Edge cases: empty buffers, zero values, multiple events
  - Determinism and reset
"""

from datetime import datetime, timedelta

import pytest

from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.strategies.event_detector import (
    EventDetector,
    EventDetectorConfig,
    EventResponse,
    EventSeverity,
    EventType,
    MarketEvent,
    ResponseAction,
)

# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _make_market(
    market_id: str = "test-market-1",
    expiry_offset: float = 3600.0,
) -> Market:
    """Create a test Market with configurable expiry offset."""
    return Market(
        id=market_id,
        asset=Asset.BTC,
        window=Window.M15,
        question="Test market?",
        status=MarketStatus.ACTIVE,
        yes_token_id=f"yes-{market_id}",
        no_token_id=f"no-{market_id}",
        yes_price=0.50,
        no_price=0.50,
        volume_24h=5000.0,
        expiry=datetime.utcnow() + timedelta(seconds=expiry_offset),
    )


def _make_tick(
    market_id: str = "test-market-1",
    yes_price: float = 0.50,
    spread: float = 0.01,
    volume_24h: float = 5000.0,
    timestamp: datetime | None = None,
) -> MarketTick:
    """Create a test MarketTick."""
    return MarketTick(
        market_id=market_id,
        timestamp=timestamp or datetime.utcnow(),
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        best_bid=yes_price - spread / 2,
        best_ask=yes_price + spread / 2,
        spread=spread,
        volume_24h=volume_24h,
    )


def _make_config(**overrides) -> EventDetectorConfig:
    """Create EventDetectorConfig with overrides."""
    defaults = {
        "price_shock_low": 0.05,
        "price_shock_high": 0.15,
        "price_window_ticks": 3,
        "volume_surge_multiplier": 3.0,
        "volume_window_ticks": 20,
        "expiry_low_minutes": 30.0,
        "expiry_medium_minutes": 10.0,
        "expiry_high_minutes": 5.0,
        "spread_explosion_multiplier": 3.0,
        "spread_window_ticks": 20,
        "event_cooldown_seconds": 0.0,  # disable cooldown for most tests
        "halt_size_multiplier": 0.0,
        "reduce_size_medium": 0.50,
        "reduce_size_high": 0.25,
        "boost_confidence_multiplier": 1.15,
    }
    defaults.update(overrides)
    return EventDetectorConfig(**defaults)


# ══════════════════════════════════════════════════════════════════════════
# CONFIG VALIDATION TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestEventDetectorConfig:
    """Tests for EventDetectorConfig validation."""

    def test_valid_config(self):
        """Default config should validate without error."""
        cfg = EventDetectorConfig()
        cfg.validate()  # Should not raise

    def test_price_shock_low_not_less_than_high(self):
        """price_shock_low must be < price_shock_high."""
        cfg = _make_config(price_shock_low=0.20, price_shock_high=0.10)
        with pytest.raises(ValueError, match="price_shock"):
            cfg.validate()

    def test_price_window_ticks_minimum(self):
        """price_window_ticks must be >= 2."""
        cfg = _make_config(price_window_ticks=1)
        with pytest.raises(ValueError, match="price_window_ticks"):
            cfg.validate()

    def test_volume_surge_multiplier_gt_one(self):
        """volume_surge_multiplier must be > 1.0."""
        cfg = _make_config(volume_surge_multiplier=1.0)
        with pytest.raises(ValueError, match="volume_surge"):
            cfg.validate()

    def test_spread_explosion_multiplier_gt_one(self):
        """spread_explosion_multiplier must be > 1.0."""
        cfg = _make_config(spread_explosion_multiplier=1.0)
        with pytest.raises(ValueError, match="spread_explosion"):
            cfg.validate()

    def test_reduce_size_ordering(self):
        """reduce_size_high must be < reduce_size_medium."""
        cfg = _make_config(reduce_size_high=0.60, reduce_size_medium=0.50)
        with pytest.raises(ValueError, match="reduce_size"):
            cfg.validate()

    def test_negative_cooldown(self):
        """event_cooldown_seconds must be >= 0."""
        cfg = _make_config(event_cooldown_seconds=-1.0)
        with pytest.raises(ValueError, match="cooldown"):
            cfg.validate()


# ══════════════════════════════════════════════════════════════════════════
# PRICE SHOCK DETECTION TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestPriceShockDetection:
    """Tests for price shock event detection."""

    def test_no_shock_normal_price_movement(self):
        """Small price changes should NOT trigger a price shock."""
        detector = EventDetector(_make_config(price_window_ticks=3))
        market = _make_market()
        base_price = 0.50

        # Feed ticks with small price movements
        for i in range(5):
            price = base_price + (i * 0.001)  # 0.1% changes
            tick = _make_tick(yes_price=price)
            detector.feed_tick(tick, market)

        # Detect on last tick (0.6% change over 3 ticks)
        tick = _make_tick(yes_price=base_price + 0.003)
        events = detector.detect(tick, market)

        assert len(events) == 0

    def test_price_shock_low_up(self):
        """Price jump > 5% should trigger LOW severity price shock."""
        config = _make_config(price_shock_low=0.05, price_window_ticks=3)
        detector = EventDetector(config)
        market = _make_market()

        # Feed baseline ticks at ~0.50
        for _ in range(4):
            tick = _make_tick(yes_price=0.50)
            detector.feed_tick(tick, market)

        # Current tick: 6% higher (0.50 → 0.53)
        tick = _make_tick(yes_price=0.53)
        events = detector.detect(tick, market)

        assert len(events) == 1
        e = events[0]
        assert e.event_type == EventType.PRICE_SHOCK
        assert e.severity == EventSeverity.LOW
        assert e.confidence > 0.0

    def test_price_shock_high(self):
        """Price jump > 15% should trigger HIGH severity."""
        config = _make_config(
            price_shock_low=0.05, price_shock_high=0.15, price_window_ticks=3
        )
        detector = EventDetector(config)
        market = _make_market()

        for _ in range(4):
            detector.feed_tick(_make_tick(yes_price=0.50), market)

        # 20% jump
        tick = _make_tick(yes_price=0.60)
        events = detector.detect(tick, market)

        assert len(events) == 1
        e = events[0]
        assert e.event_type == EventType.PRICE_SHOCK
        assert e.severity == EventSeverity.HIGH
        assert e.is_blocking is True

    def test_price_shock_down(self):
        """Price drop should also be detected."""
        config = _make_config(price_shock_low=0.05, price_window_ticks=3)
        detector = EventDetector(config)
        market = _make_market()

        for _ in range(4):
            detector.feed_tick(_make_tick(yes_price=0.50), market)

        # 10% drop
        tick = _make_tick(yes_price=0.45)
        events = detector.detect(tick, market)

        assert len(events) == 1
        e = events[0]
        assert e.event_type == EventType.PRICE_SHOCK
        assert "down" in e.reason.lower()

    def test_no_shock_insufficient_history(self):
        """Not enough ticks in buffer → no price shock detection."""
        detector = EventDetector(_make_config(price_window_ticks=5))
        market = _make_market()

        # Only 2 ticks fed
        detector.feed_tick(_make_tick(yes_price=0.50), market)
        detector.feed_tick(_make_tick(yes_price=0.60), market)

        events = detector.detect(_make_tick(yes_price=0.60), market)
        assert len(events) == 0

    def test_no_shock_zero_old_price(self):
        """Old price of 0 should not trigger price shock (division guard)."""
        config = _make_config(price_shock_low=0.05, price_window_ticks=3)
        detector = EventDetector(config)
        market = _make_market()

        # Feed zero price ticks
        for _ in range(4):
            tick = _make_tick(yes_price=0.0)
            detector.feed_tick(tick, market)

        events = detector.detect(_make_tick(yes_price=0.50), market)

        # No price shock events when old_price is 0
        price_events = [e for e in events if e.event_type == EventType.PRICE_SHOCK]
        assert len(price_events) == 0

    def test_price_shock_confidence_scaling(self):
        """Confidence should scale with price movement magnitude."""
        config = _make_config(price_shock_low=0.05, price_window_ticks=3)
        detector = EventDetector(config)
        market = _make_market()

        for _ in range(4):
            detector.feed_tick(_make_tick(yes_price=0.50), market)

        # 6% change → confidence = 0.06 / 0.20 = 0.30
        tick = _make_tick(yes_price=0.53)
        events = detector.detect(tick, market)
        assert events[0].confidence == pytest.approx(0.30, abs=0.01)

        # Reset for second test
        detector.reset(market.id)

        for _ in range(4):
            detector.feed_tick(_make_tick(yes_price=0.50, market_id=market.id), market)

        # 18% change → confidence = min(1.0, 0.18/0.20) = 0.90
        tick2 = _make_tick(yes_price=0.59, market_id=market.id)
        events2 = detector.detect(tick2, market)
        assert events2[0].confidence == pytest.approx(0.90, abs=0.01)


# ══════════════════════════════════════════════════════════════════════════
# VOLUME SURGE DETECTION TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestVolumeSurgeDetection:
    """Tests for volume surge event detection."""

    def test_no_surge_normal_volume(self):
        """Normal volume should not trigger surge."""
        config = _make_config(volume_surge_multiplier=3.0, volume_window_ticks=10)
        detector = EventDetector(config)
        market = _make_market()

        # Feed ticks with stable volume ~5000
        for _ in range(12):
            detector.feed_tick(_make_tick(volume_24h=5000.0), market)

        events = detector.detect(_make_tick(volume_24h=5200.0), market)
        assert len(events) == 0

    def test_volume_surge_low(self):
        """Volume 3x+ baseline should trigger LOW severity surge."""
        config = _make_config(volume_surge_multiplier=3.0, volume_window_ticks=10)
        detector = EventDetector(config)
        market = _make_market()

        # Feed baseline: stable ~1000
        for _ in range(12):
            detector.feed_tick(_make_tick(volume_24h=1000.0), market)

        # Surge: 4000 (4x baseline)
        tick = _make_tick(volume_24h=4000.0)
        events = detector.detect(tick, market)

        assert len(events) == 1
        e = events[0]
        assert e.event_type == EventType.VOLUME_SURGE
        assert e.severity == EventSeverity.LOW

    def test_volume_surge_high(self):
        """Volume 6x+ baseline should trigger HIGH severity."""
        config = _make_config(volume_surge_multiplier=3.0, volume_window_ticks=10)
        detector = EventDetector(config)
        market = _make_market()

        for _ in range(12):
            detector.feed_tick(_make_tick(volume_24h=1000.0), market)

        # Surge: 7000 (7x baseline → > 3.0*2 = 6.0 → HIGH)
        tick = _make_tick(volume_24h=7000.0)
        events = detector.detect(tick, market)

        assert len(events) == 1
        e = events[0]
        assert e.severity == EventSeverity.HIGH

    def test_no_surge_insufficient_history(self):
        """Not enough volume history → no surge detection."""
        config = _make_config(volume_window_ticks=20)
        detector = EventDetector(config)
        market = _make_market()

        # Only 2 ticks
        detector.feed_tick(_make_tick(volume_24h=1000.0), market)
        detector.feed_tick(_make_tick(volume_24h=5000.0), market)

        events = detector.detect(_make_tick(volume_24h=5000.0), market)
        assert len(events) == 0


# ══════════════════════════════════════════════════════════════════════════
# EXPIRY PROXIMITY DETECTION TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestExpiryProximityDetection:
    """Tests for expiry proximity event detection."""

    def test_expiry_high(self):
        """Market expiring in < 5 min → HIGH severity."""
        detector = EventDetector(_make_config(expiry_high_minutes=5.0))
        market = _make_market(expiry_offset=180.0)  # 3 minutes

        tick = _make_tick()
        events = detector.detect(tick, market)

        assert len(events) == 1
        e = events[0]
        assert e.event_type == EventType.EXPIRY_PROXIMITY
        assert e.severity == EventSeverity.HIGH
        assert e.is_blocking is True

    def test_expiry_medium(self):
        """Market expiring in 5-10 min → MEDIUM severity."""
        detector = EventDetector(_make_config(
            expiry_medium_minutes=10.0, expiry_high_minutes=5.0
        ))
        market = _make_market(expiry_offset=420.0)  # 7 minutes

        tick = _make_tick()
        events = detector.detect(tick, market)

        assert len(events) == 1
        e = events[0]
        assert e.severity == EventSeverity.MEDIUM
        assert e.is_blocking is False
        assert e.is_actionable is True

    def test_expiry_low(self):
        """Market expiring in 10-30 min → LOW severity."""
        detector = EventDetector(_make_config(
            expiry_low_minutes=30.0, expiry_medium_minutes=10.0
        ))
        market = _make_market(expiry_offset=1200.0)  # 20 minutes

        tick = _make_tick()
        events = detector.detect(tick, market)

        assert len(events) == 1
        e = events[0]
        assert e.severity == EventSeverity.LOW

    def test_no_expiry_far_future(self):
        """Market expiring far in the future → no event."""
        detector = EventDetector(_make_config(expiry_low_minutes=30.0))
        market = _make_market(expiry_offset=7200.0)  # 2 hours

        tick = _make_tick()
        events = detector.detect(tick, market)
        assert len(events) == 0

    def test_expiry_event_metadata(self):
        """Expiry event should contain correct metadata."""
        detector = EventDetector(_make_config(expiry_high_minutes=5.0))
        market = _make_market(expiry_offset=120.0)  # 2 minutes

        tick = _make_tick(yes_price=0.75, spread=0.03, volume_24h=3000.0)
        events = detector.detect(tick, market)

        e = events[0]
        assert e.market_id == "test-market-1"
        assert e.tick_price == 0.75
        assert e.tick_spread == 0.03
        assert e.tick_volume == 3000.0
        assert "expir" in e.reason.lower()


# ══════════════════════════════════════════════════════════════════════════
# SPREAD EXPLOSION DETECTION TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestSpreadExplosionDetection:
    """Tests for spread explosion event detection."""

    def test_no_explosion_normal_spread(self):
        """Normal stable spread should not trigger explosion."""
        config = _make_config(
            spread_explosion_multiplier=3.0, spread_window_ticks=10
        )
        detector = EventDetector(config)
        market = _make_market()

        # Feed ticks with stable spread ~0.01
        for _ in range(12):
            detector.feed_tick(_make_tick(spread=0.01), market)

        events = detector.detect(_make_tick(spread=0.012), market)
        assert len(events) == 0

    def test_spread_explosion_medium(self):
        """Spread 3x+ baseline → MEDIUM severity."""
        config = _make_config(
            spread_explosion_multiplier=3.0, spread_window_ticks=10
        )
        detector = EventDetector(config)
        market = _make_market()

        # Feed baseline: stable low spread
        for _ in range(12):
            detector.feed_tick(_make_tick(spread=0.01), market)

        # Explosion: spread 0.04 (4x baseline)
        tick = _make_tick(spread=0.04)
        events = detector.detect(tick, market)

        assert len(events) == 1
        e = events[0]
        assert e.event_type == EventType.SPREAD_EXPLOSION
        assert e.severity == EventSeverity.MEDIUM

    def test_spread_explosion_high(self):
        """Spread 7.5x+ baseline → HIGH severity."""
        config = _make_config(
            spread_explosion_multiplier=3.0, spread_window_ticks=10
        )
        detector = EventDetector(config)
        market = _make_market()

        for _ in range(12):
            detector.feed_tick(_make_tick(spread=0.01), market)

        # Explosion: spread 0.08 (8x baseline → > 3.0*2.5 = 7.5 → HIGH)
        tick = _make_tick(spread=0.08)
        events = detector.detect(tick, market)

        assert len(events) == 1
        e = events[0]
        assert e.severity == EventSeverity.HIGH
        assert e.is_blocking is True

    def test_no_explosion_insufficient_history(self):
        """Not enough spread history → no spread explosion."""
        config = _make_config(spread_window_ticks=20)
        detector = EventDetector(config)
        market = _make_market()

        detector.feed_tick(_make_tick(spread=0.01), market)
        detector.feed_tick(_make_tick(spread=0.10), market)

        events = detector.detect(_make_tick(spread=0.10), market)
        assert len(events) == 0


# ══════════════════════════════════════════════════════════════════════════
# COOLDOWN MECHANISM TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestCooldowns:
    """Tests for event cooldown mechanism."""

    def test_cooldown_prevents_duplicate_events(self):
        """Same event type should not fire within cooldown window."""
        config = _make_config(
            price_shock_low=0.05,
            price_window_ticks=3,
            event_cooldown_seconds=30.0,
        )
        detector = EventDetector(config)
        market = _make_market()
        now = 1000.0

        # Feed baseline
        for _ in range(4):
            detector.feed_tick(_make_tick(yes_price=0.50), market)

        # First detection at now=1000
        tick = _make_tick(yes_price=0.55)  # 10% jump
        events1 = detector.detect(tick, market, current_time=now)
        assert len(events1) == 1  # Should fire

        # Second detection at now=1015 (15s later, < 30s cooldown)
        tick2 = _make_tick(yes_price=0.60)  # Another jump
        events2 = detector.detect(tick2, market, current_time=now + 15)
        assert len(events2) == 0  # Should NOT fire

    def test_cooldown_expires_and_re_fires(self):
        """After cooldown expires, same event type can fire again."""
        config = _make_config(
            price_shock_low=0.05,
            price_window_ticks=3,
            event_cooldown_seconds=30.0,
        )
        detector = EventDetector(config)
        market = _make_market()

        # First detection at now=1000
        for _ in range(4):
            detector.feed_tick(_make_tick(yes_price=0.50), market)
        tick1 = _make_tick(yes_price=0.55)
        events1 = detector.detect(tick1, market, current_time=1000.0)
        assert len(events1) == 1

        # Next detection at now=1050 (50s later, > 30s cooldown)
        detector.feed_tick(_make_tick(yes_price=0.55), market)
        detector.feed_tick(_make_tick(yes_price=0.55), market)
        detector.feed_tick(_make_tick(yes_price=0.55), market)
        tick2 = _make_tick(yes_price=0.65)  # Big jump
        events2 = detector.detect(tick2, market, current_time=1050.0)
        assert len(events2) >= 1  # Should fire again

    def test_cooldown_per_market(self):
        """Cooldown is per-market — events on different markets don't interfere."""
        config = _make_config(
            price_shock_low=0.05,
            price_window_ticks=3,
            event_cooldown_seconds=30.0,
        )
        detector = EventDetector(config)
        market1 = _make_market(market_id="m1")
        market2 = _make_market(market_id="m2")
        now = 1000.0

        # Feed baseline to both
        for _ in range(4):
            detector.feed_tick(_make_tick(yes_price=0.50, market_id="m1"), market1)
            detector.feed_tick(_make_tick(yes_price=0.50, market_id="m2"), market2)

        # Shock on m1 at now=1000
        events_m1 = detector.detect(
            _make_tick(yes_price=0.55, market_id="m1"), market1, current_time=now
        )
        assert len(events_m1) == 1

        # Shock on m2 at now=1000 (different market — should NOT be blocked)
        events_m2 = detector.detect(
            _make_tick(yes_price=0.55, market_id="m2"), market2, current_time=now
        )
        assert len(events_m2) == 1  # Different market — allowed


# ══════════════════════════════════════════════════════════════════════════
# EVENT RESPONSE TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestEventResponseActions:
    """Tests for EventDetector.respond() action selection."""

    def test_no_events_allow(self):
        """No events → ALLOW with default multipliers."""
        detector = EventDetector()
        response = detector.respond([], order_size=10.0, confidence=0.8)
        assert response.action == ResponseAction.ALLOW
        assert response.size_multiplier == 1.0
        assert response.confidence_multiplier == 1.0

    def test_high_severity_halt(self):
        """HIGH severity event → HALT response."""
        detector = EventDetector()

        event = MarketEvent(
            event_type=EventType.PRICE_SHOCK,
            severity=EventSeverity.HIGH,
            confidence=0.9,
            reason="price moved up 20%",
            market_id="m1",
        )
        response = detector.respond([event], order_size=10.0, confidence=0.8)

        assert response.action == ResponseAction.HALT
        assert response.should_halt is True
        assert response.size_multiplier == 0.0  # halt = no trade

    def test_critical_severity_halt(self):
        """CRITICAL severity event → HALT response."""
        detector = EventDetector()

        event = MarketEvent(
            event_type=EventType.EXPIRY_PROXIMITY,
            severity=EventSeverity.CRITICAL,
            confidence=1.0,
            reason="market expired",
            market_id="m1",
        )
        response = detector.respond([event], order_size=10.0, confidence=0.8)
        assert response.action == ResponseAction.HALT

    def test_medium_severity_reduce_size(self):
        """MEDIUM severity → REDUCE_SIZE with medium multiplier."""
        detector = EventDetector(_make_config(
            reduce_size_medium=0.50, reduce_size_high=0.25
        ))

        event = MarketEvent(
            event_type=EventType.SPREAD_EXPLOSION,
            severity=EventSeverity.MEDIUM,
            confidence=0.7,
            reason="spread widened 4x",
            market_id="m1",
        )
        response = detector.respond([event], order_size=10.0, confidence=0.8)

        assert response.action == ResponseAction.REDUCE_SIZE
        assert response.should_reduce is True
        assert response.size_multiplier == 0.50
        assert response.triggering_events == 1

    def test_low_severity_boost_confidence(self):
        """LOW severity price shock/surge → BOOST_CONFIDENCE."""
        detector = EventDetector()

        event = MarketEvent(
            event_type=EventType.PRICE_SHOCK,
            severity=EventSeverity.LOW,
            confidence=0.4,
            reason="price moved up 6%",
            market_id="m1",
        )
        response = detector.respond([event], order_size=10.0, confidence=0.8)

        assert response.action == ResponseAction.BOOST_CONFIDENCE
        assert response.should_boost is True
        assert response.confidence_multiplier == 1.15

    def test_low_severity_expiry_not_boosted(self):
        """LOW severity expiry event should NOT trigger boost."""
        detector = EventDetector()

        event = MarketEvent(
            event_type=EventType.EXPIRY_PROXIMITY,
            severity=EventSeverity.LOW,
            confidence=0.5,
            reason="expiring in 20 min",
            market_id="m1",
        )
        response = detector.respond([event], order_size=10.0, confidence=0.8)

        # LOW expiry → not boostable → ALLOW
        assert response.action == ResponseAction.ALLOW

    def test_low_severity_spread_not_boosted(self):
        """LOW severity spread event should NOT trigger boost."""
        detector = EventDetector()

        event = MarketEvent(
            event_type=EventType.SPREAD_EXPLOSION,
            severity=EventSeverity.LOW,
            confidence=0.3,
            reason="spread slightly wide",
            market_id="m1",
        )
        response = detector.respond([event], order_size=5.0, confidence=0.7)
        assert response.action == ResponseAction.ALLOW

    def test_halt_takes_priority_over_reduce(self):
        """HALT events take priority over REDUCE_SIZE events."""
        detector = EventDetector()

        events = [
            MarketEvent(
                event_type=EventType.SPREAD_EXPLOSION,
                severity=EventSeverity.MEDIUM,
                confidence=0.7,
                reason="spread widened",
                market_id="m1",
            ),
            MarketEvent(
                event_type=EventType.PRICE_SHOCK,
                severity=EventSeverity.HIGH,
                confidence=0.9,
                reason="price crashed 25%",
                market_id="m1",
            ),
        ]
        response = detector.respond(events, order_size=10.0, confidence=0.8)
        assert response.action == ResponseAction.HALT

    def test_multiple_events_reasons_aggregated(self):
        """Multiple events should aggregate reasons."""
        detector = EventDetector()

        events = [
            MarketEvent(
                event_type=EventType.VOLUME_SURGE,
                severity=EventSeverity.MEDIUM,
                confidence=0.6,
                reason="volume 4x baseline",
                market_id="m1",
            ),
            MarketEvent(
                event_type=EventType.SPREAD_EXPLOSION,
                severity=EventSeverity.MEDIUM,
                confidence=0.7,
                reason="spread 5x baseline",
                market_id="m1",
            ),
        ]
        response = detector.respond(events, order_size=10.0, confidence=0.8)

        assert response.action == ResponseAction.REDUCE_SIZE
        assert response.triggering_events == 2
        assert len(response.reasons) == 2

    def test_medium_expiry_uses_high_reduction(self):
        """MEDIUM expiry proximity should use reduce_size_high."""
        detector = EventDetector(_make_config(
            reduce_size_medium=0.50, reduce_size_high=0.25
        ))

        event = MarketEvent(
            event_type=EventType.EXPIRY_PROXIMITY,
            severity=EventSeverity.MEDIUM,
            confidence=0.8,
            reason="expiring soon",
            market_id="m1",
        )
        response = detector.respond([event], order_size=10.0, confidence=0.8)
        assert response.action == ResponseAction.REDUCE_SIZE
        assert response.size_multiplier == 0.25  # High reduction for expiry


# ══════════════════════════════════════════════════════════════════════════
# MULTIPLE EVENT TYPES TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestMultipleEventTypes:
    """Tests for detection when multiple event types fire simultaneously."""

    def test_price_shock_and_spread_explosion(self):
        """Both price shock and spread explosion can fire together."""
        config = _make_config(
            price_shock_low=0.05,
            price_window_ticks=3,
            spread_explosion_multiplier=3.0,
            spread_window_ticks=10,
        )
        detector = EventDetector(config)
        market = _make_market()

        # Feed baseline: stable price and spread
        for _ in range(12):
            detector.feed_tick(_make_tick(yes_price=0.50, spread=0.01), market)

        # Current: price shock + spread explosion
        tick = _make_tick(yes_price=0.56, spread=0.05)
        events = detector.detect(tick, market)

        event_types = [e.event_type for e in events]
        assert EventType.PRICE_SHOCK in event_types
        assert EventType.SPREAD_EXPLOSION in event_types
        assert len(events) == 2

    def test_all_four_event_types(self):
        """All 4 event types can fire simultaneously."""
        config = _make_config(
            price_shock_low=0.05,
            price_window_ticks=3,
            volume_surge_multiplier=3.0,
            volume_window_ticks=10,
            expiry_low_minutes=30.0,
            expiry_high_minutes=5.0,
            spread_explosion_multiplier=3.0,
            spread_window_ticks=10,
        )
        detector = EventDetector(config)
        market = _make_market(expiry_offset=180.0)  # 3 min → HIGH

        # Feed baseline
        for _ in range(12):
            detector.feed_tick(
                _make_tick(yes_price=0.50, spread=0.01, volume_24h=1000.0),
                market,
            )

        # Current: all 4 events
        tick = _make_tick(yes_price=0.60, spread=0.06, volume_24h=8000.0)
        events = detector.detect(tick, market)

        event_types = {e.event_type for e in events}
        assert event_types == {
            EventType.PRICE_SHOCK,
            EventType.VOLUME_SURGE,
            EventType.EXPIRY_PROXIMITY,
            EventType.SPREAD_EXPLOSION,
        }


# ══════════════════════════════════════════════════════════════════════════
# EDGE CASES
# ══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_reset_clears_all_state(self):
        """Reset should clear buffers and cooldowns for a market."""
        config = _make_config(
            price_shock_low=0.05,
            price_window_ticks=3,
            event_cooldown_seconds=30.0,
        )
        detector = EventDetector(config)
        market = _make_market()

        # Feed and detect
        for _ in range(4):
            detector.feed_tick(_make_tick(yes_price=0.50), market)
        events = detector.detect(
            _make_tick(yes_price=0.55), market, current_time=1000.0
        )
        assert len(events) == 1  # Cooldown starts

        # Reset
        detector.reset(market.id)

        # Feed fresh baseline and detect again — should fire
        for _ in range(4):
            detector.feed_tick(_make_tick(yes_price=0.60), market)
        events2 = detector.detect(
            _make_tick(yes_price=0.70), market, current_time=1000.0
        )
        assert len(events2) >= 1  # Reset cleared cooldown

    def test_detector_with_default_config(self):
        """Default config should create a valid detector."""
        detector = EventDetector()
        assert detector._config is not None

        market = _make_market()
        tick = _make_tick()
        events = detector.detect(tick, market)
        # With default config and no history, should return empty
        assert isinstance(events, list)

    def test_deterministic_with_same_inputs(self):
        """Same inputs should produce same results (deterministic)."""
        config = _make_config(
            price_shock_low=0.05, price_window_ticks=3, event_cooldown_seconds=0.0
        )
        market = _make_market()

        # Run 1
        d1 = EventDetector(config)
        for _ in range(4):
            d1.feed_tick(_make_tick(yes_price=0.50), market)
        events1 = d1.detect(_make_tick(yes_price=0.58), market, current_time=100.0)

        # Run 2
        d2 = EventDetector(config)
        for _ in range(4):
            d2.feed_tick(_make_tick(yes_price=0.50), market)
        events2 = d2.detect(_make_tick(yes_price=0.58), market, current_time=100.0)

        assert len(events1) == len(events2)
        assert events1[0].event_type == events2[0].event_type
        assert events1[0].severity == events2[0].severity

    def test_empty_history_only_expiry(self):
        """No ticks fed → only expiry proximity can fire (uses market metadata)."""
        detector = EventDetector()
        market = _make_market(expiry_offset=120.0)  # 2 min → HIGH
        tick = _make_tick()

        events = detector.detect(tick, market)
        # Only expiry proximity fires (uses market metadata, not tick history)
        assert len(events) == 1
        assert events[0].event_type == EventType.EXPIRY_PROXIMITY

    def test_feed_tick_then_detect_default_now(self):
        """detect() without current_time should use tick timestamp."""
        config = _make_config(
            price_shock_low=0.05, price_window_ticks=3, event_cooldown_seconds=0.0
        )
        detector = EventDetector(config)
        market = _make_market()

        for _ in range(4):
            detector.feed_tick(_make_tick(yes_price=0.50), market)

        # detect without current_time
        tick = _make_tick(yes_price=0.55)
        events = detector.detect(tick, market)
        assert len(events) == 1  # Should work with timestamp from tick

    def test_no_crash_on_zero_baseline_volume(self):
        """Zero baseline volume should not cause division error."""
        config = _make_config(volume_surge_multiplier=3.0, volume_window_ticks=10)
        detector = EventDetector(config)
        market = _make_market()

        # Feed ticks with zero volume
        for _ in range(12):
            detector.feed_tick(_make_tick(volume_24h=0.0), market)

        tick = _make_tick(volume_24h=1000.0)
        events = detector.detect(tick, market)
        # Median of zeros = 0 → baseline <= 0 → no volume surge event
        assert not any(e.event_type == EventType.VOLUME_SURGE for e in events)


# ══════════════════════════════════════════════════════════════════════════
# DATA CLASSES TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestMarketEventProperties:
    """Tests for MarketEvent property methods."""

    def test_is_actionable(self):
        """MEDIUM, HIGH, CRITICAL are actionable; LOW is not."""
        low = MarketEvent(
            event_type=EventType.PRICE_SHOCK, severity=EventSeverity.LOW,
            confidence=0.4, reason="test",
        )
        assert low.is_actionable is False

        medium = MarketEvent(
            event_type=EventType.SPREAD_EXPLOSION, severity=EventSeverity.MEDIUM,
            confidence=0.6, reason="test",
        )
        assert medium.is_actionable is True

    def test_is_blocking(self):
        """HIGH and CRITICAL are blocking; LOW and MEDIUM are not."""
        high = MarketEvent(
            event_type=EventType.PRICE_SHOCK, severity=EventSeverity.HIGH,
            confidence=0.8, reason="test",
        )
        assert high.is_blocking is True

        medium = MarketEvent(
            event_type=EventType.SPREAD_EXPLOSION, severity=EventSeverity.MEDIUM,
            confidence=0.6, reason="test",
        )
        assert medium.is_blocking is False


class TestEventResponseProperties:
    """Tests for EventResponse property methods."""

    def test_should_halt(self):
        resp = EventResponse(action=ResponseAction.HALT, size_multiplier=0.0)
        assert resp.should_halt is True
        assert resp.should_reduce is False
        assert resp.should_boost is False

    def test_should_reduce(self):
        resp = EventResponse(action=ResponseAction.REDUCE_SIZE, size_multiplier=0.5)
        assert resp.should_halt is False
        assert resp.should_reduce is True
        assert resp.should_boost is False

    def test_should_boost(self):
        resp = EventResponse(
            action=ResponseAction.BOOST_CONFIDENCE, confidence_multiplier=1.15
        )
        assert resp.should_halt is False
        assert resp.should_reduce is False
        assert resp.should_boost is True

    def test_default_response(self):
        resp = EventResponse(action=ResponseAction.ALLOW)
        assert resp.should_halt is False
        assert resp.should_reduce is False
        assert resp.should_boost is False
        assert resp.size_multiplier == 1.0
        assert resp.confidence_multiplier == 1.0
        assert resp.triggering_events == 0
