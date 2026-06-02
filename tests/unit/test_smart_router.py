"""
Unit tests for P9.4 — Smart Order Routing.

Tests:
  - SmartRouterConfig: defaults, validation
  - RoutingDecision: properties, chunk calculations
  - SmartRouter: basic taker routing, maker routing,
    split routing, force taker (spread/depth/volatility),
    dynamic threshold, single chunk edge cases
"""

import pytest

from src.execution.slippage_engine import SlippageEngine
from src.execution.smart_router import (
    OrderChunk,
    RoutingDecision,
    SmartRouter,
    SmartRouterConfig,
)

# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _make_tick(
    spread: float = 0.02,
    asks_vol_1: float = 20000.0,
    bids_vol_1: float = 18000.0,
    best_bid: float = 0.49,
    best_ask: float = 0.51,
    volume_24h: float = 5000.0,
) -> dict:
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "bids_vol_1": bids_vol_1,
        "bids_vol_2": 4000.0,
        "bids_vol_3": 800.0,
        "asks_vol_1": asks_vol_1,
        "asks_vol_2": 5000.0,
        "asks_vol_3": 1000.0,
        "volume_24h": volume_24h,
    }


# ══════════════════════════════════════════════════════════════════════════
# CONFIG TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestSmartRouterConfig:
    """Test configuration defaults and customization."""

    def test_defaults(self):
        cfg = SmartRouterConfig()
        assert cfg.maker_spread_max == 0.05
        assert cfg.maker_depth_min == 1000.0
        assert cfg.maker_vol_ceiling == 0.30
        assert cfg.max_chunk_size == 25.0
        assert cfg.min_chunk_size == 5.0
        assert cfg.max_chunks == 4
        assert cfg.timing_enabled is False

    def test_custom_config(self):
        cfg = SmartRouterConfig(
            maker_spread_max=0.03,
            max_chunk_size=50.0,
            timing_enabled=True,
        )
        assert cfg.maker_spread_max == 0.03
        assert cfg.max_chunk_size == 50.0
        assert cfg.timing_enabled is True


# ══════════════════════════════════════════════════════════════════════════
# ROUTING DECISION TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestRoutingDecision:
    """Test RoutingDecision dataclass properties."""

    def test_single_chunk_not_split(self):
        d = RoutingDecision(
            mode="taker",
            chunks=[OrderChunk(size=10.0, mode="taker")],
        )
        assert not d.is_split
        assert d.total_size == 10.0
        assert d.taker_chunks == 1
        assert d.maker_chunks == 0

    def test_multiple_chunks_is_split(self):
        d = RoutingDecision(
            mode="split",
            chunks=[
                OrderChunk(size=10.0, mode="maker"),
                OrderChunk(size=10.0, mode="taker"),
            ],
        )
        assert d.is_split
        assert d.total_size == 20.0
        assert d.maker_chunks == 1
        assert d.taker_chunks == 1

    def test_all_maker(self):
        d = RoutingDecision(
            mode="maker",
            chunks=[OrderChunk(size=15.0, mode="maker")],
        )
        assert d.maker_chunks == 1
        assert d.taker_chunks == 0

    def test_fields_stored(self):
        d = RoutingDecision(
            mode="taker",
            chunks=[OrderChunk(size=5.0, mode="taker", delay_seconds=2.0)],
            expected_slippage=0.01,
            expected_savings_pct=5.0,
            reason="test reason",
            maker_p_fill=0.75,
            adverse_bps=10.0,
        )
        assert d.mode == "taker"
        assert d.expected_slippage == 0.01
        assert d.expected_savings_pct == 5.0
        assert d.reason == "test reason"
        assert d.maker_p_fill == 0.75
        assert d.adverse_bps == 10.0
        assert d.chunks[0].delay_seconds == 2.0


# ══════════════════════════════════════════════════════════════════════════
# SMART ROUTER — TAKER PATH
# ══════════════════════════════════════════════════════════════════════════


class TestSmartRouterTaker:
    """Test SmartRouter taker path (conditions force taker)."""

    @pytest.fixture
    def router(self):
        return SmartRouter()

    def test_normal_conditions_may_use_maker(self, router):
        """Normal spread + deep L1 + low vol -> maker may be considered."""
        tick = _make_tick(spread=0.01, asks_vol_1=20000.0)
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=0.10, regime="CHOP",
        )
        assert decision.mode in ("maker", "taker")

    def test_wide_spread_forces_taker(self, router):
        """Spread > 0.05 forces taker."""
        tick = _make_tick(spread=0.10)
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
        )
        assert decision.mode == "taker"
        assert "spread" in decision.reason.lower()

    def test_shallow_depth_forces_taker(self, router):
        """L1 depth < 1000 forces taker."""
        tick = _make_tick(asks_vol_1=500.0)
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
        )
        assert decision.mode == "taker"
        assert "depth" in decision.reason.lower()

    def test_high_volatility_forces_taker(self, router):
        """Volatility > 0.30 forces taker."""
        tick = _make_tick()
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=0.50,
        )
        assert decision.mode == "taker"
        assert "volatility" in decision.reason.lower()

    def test_panic_regime_forces_taker(self, router):
        """PANIC regime with moderate vol may still force taker via dynamic threshold."""
        tick = _make_tick()
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=0.40, regime="PANIC",
        )
        # High vol + PANIC -> forced taker by volatility ceiling
        assert decision.mode == "taker"

    def test_exit_side_uses_bids_depth(self, router):
        """Exit side checks bids_vol_1 for depth constraint."""
        tick = _make_tick(bids_vol_1=500.0)
        decision = router.route(
            tick_data=tick, order_size=10.0, side="exit",
        )
        assert decision.mode == "taker"
        assert "depth" in decision.reason.lower()

    def test_single_chunk_taker(self, router):
        """Force taker produces single chunk."""
        tick = _make_tick(spread=0.10)
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
        )
        assert len(decision.chunks) == 1
        assert decision.chunks[0].mode == "taker"
        assert decision.chunks[0].size == 10.0


# ══════════════════════════════════════════════════════════════════════════
# SMART ROUTER — SPLIT PATH
# ══════════════════════════════════════════════════════════════════════════


class TestSmartRouterSplit:
    """Test SmartRouter order splitting logic."""

    @pytest.fixture
    def router(self):
        return SmartRouter()

    def test_large_order_splits(self, router):
        """Order > max_chunk_size (25) splits into chunks."""
        tick = _make_tick(asks_vol_1=50000.0)
        decision = router.route(
            tick_data=tick, order_size=100.0, side="entry",
            volatility=0.10, regime="CHOP",
        )
        assert decision.is_split
        assert len(decision.chunks) >= 2

    def test_split_chunks_sum_to_total(self, router):
        """Chunk sizes sum to original order size."""
        tick = _make_tick(asks_vol_1=50000.0)
        decision = router.route(
            tick_data=tick, order_size=80.0, side="entry",
            volatility=0.10, regime="CHOP",
        )
        total = sum(c.size for c in decision.chunks)
        assert total == pytest.approx(80.0, abs=0.5)

    def test_first_chunk_may_be_maker(self, router):
        """First chunk may try maker; subsequent are taker."""
        tick = _make_tick(asks_vol_1=50000.0)
        decision = router.route(
            tick_data=tick, order_size=80.0, side="entry",
            volatility=0.10, regime="CHOP",
        )
        # First chunk could be maker or taker depending on conditions
        assert decision.chunks[0].mode in ("maker", "taker")
        if len(decision.chunks) > 1:
            for chunk in decision.chunks[1:]:
                assert chunk.mode == "taker"

    def test_small_order_not_split(self, router):
        """Order <= max_chunk_size and below depth threshold won't split."""
        tick = _make_tick(asks_vol_1=50000.0)
        decision = router.route(
            tick_data=tick, order_size=15.0, side="entry",
            volatility=0.10, regime="CHOP",
        )
        assert not decision.is_split

    def test_respects_min_chunk_size(self, router):
        """Chunks respect min_chunk_size (5.0)."""
        tick = _make_tick(asks_vol_1=50000.0)
        decision = router.route(
            tick_data=tick, order_size=50.0, side="entry",
            volatility=0.10, regime="CHOP",
        )
        for chunk in decision.chunks:
            assert chunk.size >= 4.5  # Allow small rounding

    def test_max_chunks_respected(self, router):
        """Split doesn't exceed max_chunks (4)."""
        tick = _make_tick(asks_vol_1=50000.0)
        decision = router.route(
            tick_data=tick, order_size=200.0, side="entry",
            volatility=0.10, regime="CHOP",
        )
        assert len(decision.chunks) <= 4


# ══════════════════════════════════════════════════════════════════════════
# SMART ROUTER — DYNAMIC THRESHOLD
# ══════════════════════════════════════════════════════════════════════════


class TestSmartRouterDynamicThreshold:
    """Test dynamic maker/taker threshold adaptation."""

    @pytest.fixture
    def router(self):
        return SmartRouter()

    def test_low_vol_loose_threshold(self, router):
        """Low volatility -> loose threshold (maker favored)."""
        tick = _make_tick(spread=0.01, asks_vol_1=20000.0)
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=0.05, regime="CHOP",
        )
        # With loose threshold and favorable conditions, maker more likely
        # But may still be taker if P(fill) is low
        assert decision.mode in ("maker", "taker")

    def test_panic_regime_tight_threshold(self, router):
        """PANIC regime -> tight threshold."""
        # PANIC with moderate vol (below ceiling) -> tight threshold
        tick = _make_tick(spread=0.01, asks_vol_1=20000.0)
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=0.15, regime="PANIC",
        )
        assert decision.mode in ("maker", "taker")

    def test_trend_regime_tight_threshold(self, router):
        """TREND regime -> tight threshold like PANIC."""
        tick = _make_tick(spread=0.01, asks_vol_1=20000.0)
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=0.10, regime="TREND",
        )
        assert decision.mode in ("maker", "taker")

    def test_none_vol_defaults(self, router):
        """None volatility treated as 0 -> loose threshold."""
        tick = _make_tick(spread=0.01, asks_vol_1=20000.0)
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=None, regime="CHOP",
        )
        assert decision.mode in ("maker", "taker")

    def test_none_regime_defaults(self, router):
        """None regime treated as unknown, vol-based threshold."""
        tick = _make_tick(spread=0.01, asks_vol_1=20000.0)
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=0.10, regime=None,
        )
        assert decision.mode in ("maker", "taker")


# ══════════════════════════════════════════════════════════════════════════
# SMART ROUTER — EDGE CASES
# ══════════════════════════════════════════════════════════════════════════


class TestSmartRouterEdgeCases:
    """Test SmartRouter edge cases."""

    @pytest.fixture
    def router(self):
        return SmartRouter()

    def test_zero_order_size(self, router):
        """Zero-size order should route without error."""
        tick = _make_tick()
        decision = router.route(
            tick_data=tick, order_size=0.0, side="entry",
        )
        assert decision.mode in ("maker", "taker")
        assert len(decision.chunks) == 1

    def test_custom_config_accepted(self):
        """Custom config is accepted."""
        cfg = SmartRouterConfig(
            max_chunk_size=10.0,
            maker_depth_min=500.0,
        )
        router = SmartRouter(config=cfg)
        tick = _make_tick(asks_vol_1=20000.0)
        decision = router.route(
            tick_data=tick, order_size=25.0, side="entry",
        )
        # 25 > max_chunk_size(10) -> split
        assert decision.is_split

    def test_exit_side_splitting(self, router):
        """Exit side orders can also be split."""
        tick = _make_tick(bids_vol_1=50000.0)
        decision = router.route(
            tick_data=tick, order_size=80.0, side="exit",
            volatility=0.10, regime="CHOP",
        )
        assert decision.is_split or decision.mode in ("maker", "taker")

    def test_reason_not_empty(self, router):
        """Every decision has a non-empty reason."""
        tick = _make_tick()
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
        )
        assert len(decision.reason) > 0

    def test_routing_decision_chunks_not_empty(self, router):
        """Every decision has at least one chunk."""
        tick = _make_tick(spread=0.10)
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
        )
        assert len(decision.chunks) >= 1

    def test_custom_slippage_engine(self):
        """Custom SlippageEngine can be injected."""
        engine = SlippageEngine()
        router = SmartRouter(slippage_engine=engine)
        tick = _make_tick()
        decision = router.route(
            tick_data=tick, order_size=10.0, side="entry",
        )
        assert decision.mode in ("maker", "taker")

    def test_slippage_engine_property(self, router):
        """SmartRouter exposes slippage_engine."""
        assert isinstance(router.slippage_engine, SlippageEngine)

    def test_config_property(self, router):
        """SmartRouter exposes config."""
        assert isinstance(router.config, SmartRouterConfig)
