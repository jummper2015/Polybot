"""
Unit tests for P9.3 — Queue Position Modeling.

Tests:
  - QueueTurnoverModel: volume_24h → volume_sec conversion, fallbacks
  - QueuePositionModel: fill probability formula, edge cases
  - AdverseSelectionAdjuster: cost estimation, regime multipliers
  - QueuePositionEngine: end-to-end estimation pipeline
  - CostComparator: maker-vs-taker decision logic
  - MakerVsTakerDecision: properties and edge cases
  - Integration: SlippageEngine.estimate_maker() and compare_maker_vs_taker()
"""

import math

import pytest

from src.execution.queue_position import (
    AdverseSelectionAdjuster,
    AdverseSelectionConfig,
    CostComparator,
    MakerVsTakerDecision,
    QueuePositionConfig,
    QueuePositionEngine,
    QueuePositionEstimate,
    QueuePositionModel,
    QueueTurnoverModel,
)
from src.execution.slippage_engine import SlippageEngine

# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _make_tick(
    volume_24h: float = 5000.0,
    asks_vol_1: float = 20000.0,
    bids_vol_1: float = 18000.0,
    spread: float = 0.02,
    best_bid: float = 0.49,
    best_ask: float = 0.51,
) -> dict:
    return {
        "volume_24h": volume_24h,
        "asks_vol_1": asks_vol_1,
        "asks_vol_2": 5000.0,
        "asks_vol_3": 1000.0,
        "bids_vol_1": bids_vol_1,
        "bids_vol_2": 4000.0,
        "bids_vol_3": 800.0,
        "spread": spread,
        "best_bid": best_bid,
        "best_ask": best_ask,
    }


# ══════════════════════════════════════════════════════════════════════════
# QUEUE POSITION CONFIG TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestQueuePositionConfig:
    """Test configuration defaults and customization."""

    def test_defaults(self):
        cfg = QueuePositionConfig()
        assert cfg.wait_time_T == 30.0
        assert cfg.missed_entry_factor == 0.5
        assert cfg.maker_discount_threshold == 0.95
        assert cfg.fallback_volume_sec == 0.01
        assert cfg.min_l1_depth == 1.0

    def test_custom_config(self):
        cfg = QueuePositionConfig(
            wait_time_T=60.0,
            missed_entry_factor=0.3,
            maker_discount_threshold=0.90,
        )
        assert cfg.wait_time_T == 60.0
        assert cfg.missed_entry_factor == 0.3
        assert cfg.maker_discount_threshold == 0.90


# ══════════════════════════════════════════════════════════════════════════
# QUEUE POSITION ESTIMATE TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestQueuePositionEstimate:
    """Test QueuePositionEstimate properties and defaults."""

    def test_is_viable(self):
        est = QueuePositionEstimate(
            p_fill=0.60, expected_time_to_fill=15.0,
            adverse_selection_bps=5.0, confidence=1.0,
        )
        assert est.is_viable

    def test_not_viable_below_50_pct(self):
        est = QueuePositionEstimate(
            p_fill=0.49, expected_time_to_fill=30.0,
            adverse_selection_bps=10.0, confidence=0.8,
        )
        assert not est.is_viable

    def test_fill_time_seconds_alias(self):
        est = QueuePositionEstimate(
            p_fill=0.80, expected_time_to_fill=25.5,
            adverse_selection_bps=3.0,
        )
        assert est.fill_time_seconds == 25.5

    def test_adverse_selection_pct_conversion(self):
        est = QueuePositionEstimate(
            p_fill=0.70, expected_time_to_fill=10.0,
            adverse_selection_bps=150.0,
        )
        assert est.adverse_selection_pct == pytest.approx(1.5, abs=0.01)

    def test_defaults(self):
        est = QueuePositionEstimate(
            p_fill=0.50, expected_time_to_fill=20.0,
            adverse_selection_bps=5.0,
        )
        assert est.confidence == 1.0
        assert est.wait_time_T == 30.0
        assert est.volume_sec == 0.0
        assert est.l1_depth == 0.0
        assert est.regime == "UNKNOWN"
        assert est.volatility == 0.0

    def test_fields_stored(self):
        est = QueuePositionEstimate(
            p_fill=0.75, expected_time_to_fill=12.0,
            adverse_selection_bps=8.0, confidence=0.9,
            wait_time_T=45.0, volume_sec=0.05,
            l1_depth=15000.0, regime="TREND", volatility=0.20,
        )
        assert est.p_fill == 0.75
        assert est.expected_time_to_fill == 12.0
        assert est.adverse_selection_bps == 8.0
        assert est.confidence == 0.9
        assert est.wait_time_T == 45.0
        assert est.volume_sec == 0.05
        assert est.l1_depth == 15000.0
        assert est.regime == "TREND"
        assert est.volatility == 0.20


# ══════════════════════════════════════════════════════════════════════════
# QUEUE TURNOVER MODEL TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestQueueTurnoverModel:
    """Test taker volume arrival rate estimation."""

    @pytest.fixture
    def model(self):
        return QueueTurnoverModel()

    def test_normal_volume(self, model):
        """5000 USDC/24h → ~0.0579 USDC/sec."""
        vol_sec, conf = model.estimate_volume_per_sec(5000.0)
        assert vol_sec == pytest.approx(5000.0 / 86400.0, abs=0.0001)
        assert conf == 1.0

    def test_high_volume(self, model):
        """High volume: ~$100K/day."""
        vol_sec, conf = model.estimate_volume_per_sec(100000.0)
        assert vol_sec == pytest.approx(100000.0 / 86400.0, abs=0.001)
        assert conf == 1.0

    def test_zero_volume_fallback(self, model):
        """Zero volume → fallback of 0.01 USDC/sec."""
        vol_sec, conf = model.estimate_volume_per_sec(0.0)
        assert vol_sec == 0.01
        assert conf == 0.5

    def test_none_volume_fallback(self, model):
        """None volume → fallback with degraded confidence."""
        vol_sec, conf = model.estimate_volume_per_sec(None)
        assert vol_sec == 0.01
        assert conf == 0.5

    def test_negative_volume_fallback(self, model):
        """Negative volume → fallback."""
        vol_sec, conf = model.estimate_volume_per_sec(-100.0)
        assert vol_sec == 0.01
        assert conf == 0.5

    def test_very_high_volume_capped(self, model):
        """Volume > $10M/day should be capped."""
        vol_sec, conf = model.estimate_volume_per_sec(50_000_000.0)
        max_expected = 1_000_000.0 / 86400.0
        assert vol_sec <= max_expected * 1.01
        assert conf == 1.0

    def test_small_volume(self, model):
        """Tiny but non-zero volume."""
        vol_sec, conf = model.estimate_volume_per_sec(1.0)
        assert vol_sec > 0
        assert vol_sec < 0.001
        assert conf == 1.0


# ══════════════════════════════════════════════════════════════════════════
# QUEUE POSITION MODEL TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestQueuePositionModel:
    """Test fill probability formula."""

    @pytest.fixture
    def model(self):
        return QueuePositionModel()

    def test_basic_fill_probability(self, model):
        """10 USDC order, 20K L1 depth, 0.05 USDC/sec → moderate p_fill."""
        p_fill, ttf = model.estimate_fill_probability(
            order_size=10.0,
            l1_depth=20000.0,
            volume_sec=0.05,
        )
        # volume_needed = 20000 + 10 = 20010
        # expected_vol = 0.05 * 30 = 1.5
        # ratio = 1.5 / 20010 ≈ 7.5e-5
        # p_fill = 1 - exp(-7.5e-5) ≈ 7.5e-5
        assert 0.0 < p_fill < 0.01
        assert ttf > 0

    def test_high_volume_fast_fill(self, model):
        """100 USDC/s volume, 1000 L1 depth → fills very fast."""
        p_fill, ttf = model.estimate_fill_probability(
            order_size=10.0,
            l1_depth=1000.0,
            volume_sec=100.0,
        )
        # volume_needed = 1000 + 10 = 1010
        # expected_vol = 100 * 30 = 3000
        # p_fill = 1 - exp(-3000/1010) = 1 - exp(-2.97) ≈ 0.95
        assert p_fill > 0.90
        assert ttf < 100

    def test_zero_order_always_fills(self, model):
        """Zero-size order fills instantly."""
        p_fill, ttf = model.estimate_fill_probability(
            order_size=0.0,
            l1_depth=1000.0,
            volume_sec=0.01,
        )
        assert p_fill == 1.0
        assert ttf == 0.0

    def test_zero_volume_never_fills(self, model):
        """No taker volume → never fills."""
        p_fill, ttf = model.estimate_fill_probability(
            order_size=10.0,
            l1_depth=1000.0,
            volume_sec=0.0,
        )
        assert p_fill == 0.0
        assert ttf == float("inf")

    def test_deep_l1_lower_probability(self, model):
        """Deeper L1 → lower fill probability (more orders ahead)."""
        p_shallow, _ = model.estimate_fill_probability(
            order_size=10.0, l1_depth=1000.0, volume_sec=0.05,
        )
        p_deep, _ = model.estimate_fill_probability(
            order_size=10.0, l1_depth=50000.0, volume_sec=0.05,
        )
        assert p_shallow > p_deep

    def test_larger_order_lower_probability(self, model):
        """Larger order → lower fill probability."""
        p_small, _ = model.estimate_fill_probability(
            order_size=5.0, l1_depth=1000.0, volume_sec=0.05,
        )
        p_large, _ = model.estimate_fill_probability(
            order_size=500.0, l1_depth=1000.0, volume_sec=0.05,
        )
        assert p_small > p_large

    def test_longer_wait_higher_probability(self, model):
        """Longer wait time → higher fill probability."""
        p_short, _ = model.estimate_fill_probability(
            order_size=10.0, l1_depth=1000.0,
            volume_sec=0.01, wait_time_T=10.0,
        )
        p_long, _ = model.estimate_fill_probability(
            order_size=10.0, l1_depth=1000.0,
            volume_sec=0.01, wait_time_T=60.0,
        )
        assert p_long > p_short

    def test_p_fill_bounded(self, model):
        """p_fill always in [0, 1]."""
        for order_size in [0.1, 10.0, 1000.0]:
            for l1 in [1.0, 1000.0, 100000.0]:
                for vol in [0.001, 0.1, 10.0]:
                    p_fill, _ = model.estimate_fill_probability(
                        order_size=order_size, l1_depth=l1,
                        volume_sec=vol,
                    )
                    assert 0.0 <= p_fill <= 1.0, (
                        f"p_fill={p_fill} out of bounds: "
                        f"order={order_size}, l1={l1}, vol={vol}"
                    )

    def test_low_fill_probability_infinite_time(self, model):
        """If p_fill < 1%, expected time should be infinity."""
        _, ttf = model.estimate_fill_probability(
            order_size=10.0, l1_depth=100000.0,
            volume_sec=0.001,
        )
        assert ttf == float("inf")

    def test_custom_wait_time(self, model):
        """Custom wait_time_T is respected."""
        p_default, _ = model.estimate_fill_probability(
            order_size=10.0, l1_depth=1000.0,
            volume_sec=0.1, wait_time_T=30.0,
        )
        p_custom, _ = model.estimate_fill_probability(
            order_size=10.0, l1_depth=1000.0,
            volume_sec=0.1, wait_time_T=120.0,
        )
        assert p_custom > p_default

    def test_min_l1_depth_floor(self, model):
        """L1 depth below min_l1_depth is floored."""
        p_floor, _ = model.estimate_fill_probability(
            order_size=10.0, l1_depth=0.1, volume_sec=0.05,
        )
        p_default, _ = model.estimate_fill_probability(
            order_size=10.0, l1_depth=1.0, volume_sec=0.05,
        )
        # Floor of 1.0 USDC applied → same result as l1_depth=1.0
        assert p_floor == pytest.approx(p_default, abs=0.001)

    def test_safe_exp_overflow(self, model):
        """Large positive exponent → infinity handled."""
        result = model._safe_exp(100.0)
        assert math.isinf(result)

    def test_safe_exp_underflow(self, model):
        """Large negative exponent → zero."""
        result = model._safe_exp(-100.0)
        assert result == 0.0


# ══════════════════════════════════════════════════════════════════════════
# ADVERSE SELECTION ADJUSTER TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestAdverseSelectionAdjuster:
    """Test adverse selection cost estimation."""

    @pytest.fixture
    def adjuster(self):
        return AdverseSelectionAdjuster()

    def test_zero_time_zero_cost(self, adjuster):
        assert adjuster.estimate_cost(
            volatility=0.15, time_to_fill=0.0,
        ) == 0.0

    def test_negative_time_zero_cost(self, adjuster):
        assert adjuster.estimate_cost(
            volatility=0.15, time_to_fill=-5.0,
        ) == 0.0

    def test_infinite_time_max_cost(self, adjuster):
        cost = adjuster.estimate_cost(
            volatility=0.15, time_to_fill=float("inf"),
        )
        assert cost == 200.0  # max_bps default

    def test_very_long_time_max_cost(self, adjuster):
        cost = adjuster.estimate_cost(
            volatility=0.15, time_to_fill=7200.0,
        )
        assert cost == 200.0

    def test_normal_estimate(self, adjuster):
        """30s wait, 15% vol, CHOP regime → moderate cost."""
        cost = adjuster.estimate_cost(
            volatility=0.15, time_to_fill=30.0, regime="CHOP",
        )
        # base_bps * (1 + vol_factor) * time * regime_mult
        # 0.05 * (1 + 0.15*0.000178*100) * 30 * 1.0
        # ≈ 0.05 * 1.00267 * 30 ≈ 1.504 bps
        assert 1.0 < cost < 5.0

    def test_panic_regime_higher_cost(self, adjuster):
        """PANIC regime → 4x multiplier."""
        cost_chop = adjuster.estimate_cost(
            volatility=0.15, time_to_fill=30.0, regime="CHOP",
        )
        cost_panic = adjuster.estimate_cost(
            volatility=0.15, time_to_fill=30.0, regime="PANIC",
        )
        # PANIC should be 4x CHOP
        assert cost_panic == pytest.approx(cost_chop * 4.0, abs=1.0)

    def test_high_volatility_higher_cost(self, adjuster):
        """Higher volatility → higher adverse selection."""
        cost_low = adjuster.estimate_cost(
            volatility=0.05, time_to_fill=30.0, regime="CHOP",
        )
        cost_high = adjuster.estimate_cost(
            volatility=0.50, time_to_fill=30.0, regime="CHOP",
        )
        assert cost_high > cost_low

    def test_none_volatility_zero(self, adjuster):
        """None volatility treated as 0."""
        cost = adjuster.estimate_cost(
            volatility=None, time_to_fill=30.0, regime="CHOP",
        )
        assert cost > 0

    def test_none_regime_defaults(self, adjuster):
        """None regime treated as unknown (1.0x)."""
        cost = adjuster.estimate_cost(
            volatility=0.15, time_to_fill=30.0, regime=None,
        )
        assert 1.0 < cost < 5.0

    def test_empty_regime_defaults(self, adjuster):
        """Empty regime treated as unknown."""
        cost = adjuster.estimate_cost(
            volatility=0.15, time_to_fill=30.0, regime="",
        )
        assert 1.0 < cost < 5.0

    def test_volatility_capped(self, adjuster):
        """Volatility capped at 5.0 (500%)."""
        cost_normal = adjuster.estimate_cost(
            volatility=0.50, time_to_fill=30.0,
        )
        cost_extreme = adjuster.estimate_cost(
            volatility=10.0, time_to_fill=30.0,
        )
        # Should not be exponentially higher
        assert cost_extreme <= cost_normal * 3.0

    def test_all_regimes_positive(self, adjuster):
        """All regimes produce non-negative costs."""
        for regime in ("TREND", "CHOP", "PANIC", "ILLIQUID", "EVENT_DRIVEN"):
            cost = adjuster.estimate_cost(
                volatility=0.15, time_to_fill=30.0, regime=regime,
            )
            assert cost >= 0
            assert cost <= 200.0

    def test_custom_config(self):
        cfg = AdverseSelectionConfig(
            base_bps_per_second=0.10,
            panic_regime_multiplier=8.0,
            max_bps=500.0,
        )
        adj = AdverseSelectionAdjuster(cfg)
        cost = adj.estimate_cost(
            volatility=0.15, time_to_fill=30.0, regime="PANIC",
        )
        # base * (1 + vol*0.000178*100) * time * regime
        # 0.10 * 1.00267 * 30 * 8.0 ≈ 24.06
        assert cost > 20.0
        assert cost < 30.0

    def test_config_validation(self):
        with pytest.raises(ValueError):
            AdverseSelectionConfig(base_bps_per_second=-0.1).validate()


# ══════════════════════════════════════════════════════════════════════════
# QUEUE POSITION ENGINE TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestQueuePositionEngine:
    """Test end-to-end maker fill estimation."""

    @pytest.fixture
    def engine(self):
        return QueuePositionEngine()

    def test_estimate_entry(self, engine):
        """Entry side uses asks_vol_1 as L1 depth."""
        tick = _make_tick(asks_vol_1=15000.0)
        est = engine.estimate(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=0.15, regime="CHOP",
        )
        assert isinstance(est, QueuePositionEstimate)
        assert est.l1_depth == 15000.0
        assert 0.0 <= est.p_fill <= 1.0
        assert est.adverse_selection_bps >= 0
        assert est.confidence > 0

    def test_estimate_exit(self, engine):
        """Exit side uses bids_vol_1 as L1 depth."""
        tick = _make_tick(bids_vol_1=12000.0)
        est = engine.estimate(
            tick_data=tick, order_size=10.0, side="exit",
            volatility=0.15, regime="CHOP",
        )
        assert est.l1_depth == 12000.0

    def test_zero_depth_degraded_confidence(self, engine):
        """Zero L1 depth → confidence degraded."""
        tick = _make_tick(asks_vol_1=0.0, volume_24h=0.0)
        est = engine.estimate(
            tick_data=tick, order_size=10.0, side="entry",
        )
        assert est.confidence <= 0.5

    def test_low_fill_degraded_confidence(self, engine):
        """Very low fill probability → confidence degraded."""
        tick = _make_tick(asks_vol_1=100000.0, volume_24h=1.0)
        est = engine.estimate(
            tick_data=tick, order_size=10.0, side="entry",
        )
        if est.p_fill < 0.01:
            assert est.confidence <= 0.5

    def test_high_volume_market_confident(self, engine):
        """High volume, shallow depth → fill probable, high confidence."""
        tick = _make_tick(
            volume_24h=50000.0, asks_vol_1=100.0,
        )
        est = engine.estimate(
            tick_data=tick, order_size=10.0, side="entry",
        )
        # Shallow L1 + high volume → high p_fill → high confidence
        assert est.confidence >= 0.5

    def test_stores_regime_and_volatility(self, engine):
        est = engine.estimate(
            tick_data=_make_tick(), order_size=10.0,
            volatility=0.25, regime="TREND",
        )
        assert est.regime == "TREND"
        assert est.volatility == 0.25

    def test_none_regime_and_vol_defaults(self, engine):
        est = engine.estimate(
            tick_data=_make_tick(), order_size=10.0,
        )
        assert est.regime == "UNKNOWN"
        assert est.volatility == 0.0

    def test_custom_wait_time(self, engine):
        """Custom wait_time_T is passed through."""
        est = engine.estimate(
            tick_data=_make_tick(), order_size=10.0,
            wait_time_T=60.0,
        )
        assert est.wait_time_T == 60.0

    def test_custom_components_injected(self):
        """Custom models can be injected."""
        from unittest.mock import MagicMock

        mock_turnover = MagicMock(spec=QueueTurnoverModel)
        mock_turnover.estimate_volume_per_sec.return_value = (0.1, 1.0)

        mock_position = MagicMock(spec=QueuePositionModel)
        mock_position.estimate_fill_probability.return_value = (0.75, 15.0)

        mock_adverse = MagicMock(spec=AdverseSelectionAdjuster)
        mock_adverse.estimate_cost.return_value = 5.0

        engine = QueuePositionEngine(
            turnover_model=mock_turnover,
            position_model=mock_position,
            adverse_adjuster=mock_adverse,
        )

        est = engine.estimate(
            tick_data=_make_tick(), order_size=10.0, side="entry",
        )
        assert est.p_fill == 0.75
        assert est.expected_time_to_fill == 15.0
        assert est.adverse_selection_bps == 5.0


# ══════════════════════════════════════════════════════════════════════════
# COST COMPARATOR TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestCostComparator:
    """Test maker-vs-taker cost comparison."""

    @pytest.fixture
    def comparator(self):
        return CostComparator()

    def _make_estimate(self, p_fill=0.80, adverse_bps=10.0):
        return QueuePositionEstimate(
            p_fill=p_fill,
            expected_time_to_fill=15.0,
            adverse_selection_bps=adverse_bps,
            confidence=0.9,
        )

    def test_maker_cheaper_preferred(self, comparator):
        """If adverse cost << taker cost, MAKER preferred."""
        est = self._make_estimate(p_fill=0.90, adverse_bps=1.0)
        decision = comparator.compare(taker_cost=0.02, maker_estimate=est)

        assert decision.mode == "MAKER"
        assert decision.prefer_maker
        assert decision.taker_cost == 0.02
        assert decision.cost_ratio < 1.0
        assert decision.savings_pct > 0

    def test_taker_preferred_when_adverse_high(self, comparator):
        """High adverse selection → TAKER preferred."""
        est = self._make_estimate(p_fill=0.80, adverse_bps=500.0)
        decision = comparator.compare(taker_cost=0.01, maker_estimate=est)

        # adverse_cost = 500 bps / 100 = 5% = 0.05 price units
        # maker_cost = 0.8 * 0.05 + 0.2 * (0.01 + 0.005) = 0.04 + 0.003 = 0.043
        # cost_ratio = 0.043 / 0.01 = 4.3 → TAKER
        assert decision.mode == "TAKER"

    def test_very_low_p_fill_taker(self, comparator):
        """p_fill < 1% → TAKER forced."""
        est = self._make_estimate(p_fill=0.005)
        decision = comparator.compare(taker_cost=0.01, maker_estimate=est)

        assert decision.mode == "TAKER"
        assert math.isinf(decision.maker_cost)
        assert math.isinf(decision.cost_ratio)

    def test_maker_savings_property(self, comparator):
        est = self._make_estimate(p_fill=0.95, adverse_bps=1.0)
        decision = comparator.compare(taker_cost=0.02, maker_estimate=est)

        if decision.prefer_maker:
            assert decision.savings_pct > 0
        else:
            assert decision.savings_pct == 0.0

    def test_reason_included(self, comparator):
        est = self._make_estimate(p_fill=0.80, adverse_bps=5.0)
        decision = comparator.compare(taker_cost=0.02, maker_estimate=est)

        assert len(decision.reason) > 0
        assert "P(fill)" in decision.reason or "Taker" in decision.reason

    def test_maker_estimate_stored(self, comparator):
        est = self._make_estimate(p_fill=0.75)
        decision = comparator.compare(taker_cost=0.02, maker_estimate=est)

        assert decision.maker_estimate is est

    def test_cost_ratio_symmetric_taker_cheap(self, comparator):
        """When taker cost is very low, maker unlikely to beat it."""
        est = self._make_estimate(p_fill=0.80, adverse_bps=10.0)
        decision = comparator.compare(taker_cost=0.001, maker_estimate=est)

        # adverse_cost = 10/100 = 0.1% = 0.001 price units
        # maker_cost ≈ 0.8*0.001 + 0.2*(0.001+0.0005) ≈ 0.0011
        # cost_ratio ≈ 1.1 → TAKER (just above 0.95 threshold)
        assert decision.mode == "TAKER"


# ══════════════════════════════════════════════════════════════════════════
# MAKER VS TAKER DECISION TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestMakerVsTakerDecision:
    """Test decision dataclass properties."""

    def _make_est(self, p_fill=0.50):
        return QueuePositionEstimate(
            p_fill=p_fill, expected_time_to_fill=10.0,
            adverse_selection_bps=5.0,
        )

    def test_maker_decision(self):
        est = self._make_est()
        d = MakerVsTakerDecision(
            mode="MAKER", taker_cost=0.02, maker_cost=0.015,
            cost_ratio=0.75, maker_estimate=est,
            reason="Maker cheaper by 25%",
        )
        assert d.prefer_maker
        assert d.savings_pct == 25.0

    def test_taker_decision(self):
        est = self._make_est()
        d = MakerVsTakerDecision(
            mode="TAKER", taker_cost=0.01, maker_cost=0.05,
            cost_ratio=5.0, maker_estimate=est,
            reason="Taker preferred",
        )
        assert not d.prefer_maker
        assert d.savings_pct == 0.0

    def test_fields_match(self):
        est = self._make_est(p_fill=0.60)
        d = MakerVsTakerDecision(
            mode="MAKER", taker_cost=0.015, maker_cost=0.010,
            cost_ratio=0.667, maker_estimate=est,
            reason="test",
        )
        assert d.mode == "MAKER"
        assert d.taker_cost == 0.015
        assert d.maker_cost == 0.010
        assert d.cost_ratio == pytest.approx(0.667, abs=0.001)


# ══════════════════════════════════════════════════════════════════════════
# INTEGRATION — SlippageEngine.estimate_maker() and compare_maker_vs_taker()
# ══════════════════════════════════════════════════════════════════════════


class TestSlippageEngineMakerIntegration:
    """Test that SlippageEngine properly delegates to QueuePositionEngine."""

    @pytest.fixture
    def engine(self):
        return SlippageEngine()

    def test_estimate_maker_entry(self, engine):
        """estimate_maker() delegates to QueuePositionEngine."""
        tick = _make_tick()
        maker = engine.estimate_maker(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=0.15, regime="CHOP",
        )
        assert isinstance(maker, QueuePositionEstimate)
        assert 0.0 <= maker.p_fill <= 1.0
        assert maker.l1_depth == 20000.0  # asks_vol_1

    def test_estimate_maker_exit(self, engine):
        """Exit side uses bids_vol_1."""
        tick = _make_tick(bids_vol_1=12000.0)
        maker = engine.estimate_maker(
            tick_data=tick, order_size=10.0, side="exit",
        )
        assert maker.l1_depth == 12000.0

    def test_compare_maker_vs_taker_maker_wins(self, engine):
        """When maker is cheaper → MAKER decision."""
        tick = _make_tick()
        taker_est = engine.estimate(tick, order_size=10.0, asset="BTC")
        maker_est = engine.estimate_maker(
            tick_data=tick, order_size=10.0, side="entry",
        )

        decision = engine.compare_maker_vs_taker(
            taker_cost=taker_est.adjusted_slippage,
            maker_estimate=maker_est,
        )
        assert isinstance(decision, MakerVsTakerDecision)
        assert decision.mode in ("MAKER", "TAKER")

    def test_full_pipeline_taker_estimate_maker_compare(self, engine):
        """Full pipeline: tick → taker estimate → maker estimate → compare."""
        tick = _make_tick(volume_24h=1000.0, asks_vol_1=500.0)

        # Step 1: Taker estimate from SlippageEngine
        taker = engine.estimate(tick, order_size=10.0, asset="BTC")
        assert taker.adjusted_slippage > 0

        # Step 2: Maker estimate
        maker = engine.estimate_maker(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=0.15, regime="CHOP",
        )
        assert maker.p_fill is not None

        # Step 3: Compare and decide
        decision = engine.compare_maker_vs_taker(
            taker_cost=abs(taker.adjusted_slippage),
            maker_estimate=maker,
        )
        assert decision.mode in ("MAKER", "TAKER")

    def test_estimate_maker_passes_through_all_params(self, engine):
        """All params (vol, regime, wait_time) are passed through."""
        tick = _make_tick()
        maker = engine.estimate_maker(
            tick_data=tick, order_size=10.0, side="entry",
            volatility=0.30, regime="PANIC", wait_time_T=60.0,
        )
        assert maker.regime == "PANIC"
        assert maker.volatility == 0.30
        assert maker.wait_time_T == 60.0
