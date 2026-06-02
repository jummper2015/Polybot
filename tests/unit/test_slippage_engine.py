"""
Unit tests for P9.2 — Slippage Engine.

Tests:
  - SlippageEstimate: properties, combined multipliers, adjusted_fill_price
  - VolatilityAdjuster: low/normal/high vol, edge cases
  - RegimeScaling: all 5 regimes, unknown, None, empty string
  - SlippageTracker: recording, calibration, stats
  - SlippageEngine: entry/exit estimation, volatility, regime, record_actual
"""

import pytest

from src.execution.fill_simulator import FillEstimate, FillSimulator
from src.execution.slippage_engine import (
    RegimeScaling,
    RegimeScalingConfig,
    SlippageEngine,
    SlippageEstimate,
    SlippageTracker,
    SlippageTrackerConfig,
    VolatilityAdjuster,
    VolatilityConfig,
)

# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _make_tick(
    best_bid: float = 0.495,
    best_ask: float = 0.505,
    spread: float = 0.010,
    asks_vol_1: float = 20000.0,
    asks_vol_2: float = 5000.0,
    asks_vol_3: float = 500.0,
    bids_vol_1: float = 50000.0,
    bids_vol_2: float = 5000.0,
    bids_vol_3: float = 500.0,
    volume_24h: float = 300000.0,
) -> dict:
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "bids_vol_1": bids_vol_1,
        "bids_vol_2": bids_vol_2,
        "bids_vol_3": bids_vol_3,
        "asks_vol_1": asks_vol_1,
        "asks_vol_2": asks_vol_2,
        "asks_vol_3": asks_vol_3,
        "volume_24h": volume_24h,
    }


def _make_base_estimate() -> FillEstimate:
    return FillEstimate(
        fill_price=0.51,
        slippage=0.01,
        slippage_pct=0.02,
        fill_ratio=1.0,
        p50_slippage=0.01,
        p95_slippage=0.015,
        p99_slippage=0.02,
        mid_price=0.50,
    )


# ══════════════════════════════════════════════════════════════════════════
# SLIPPAGE ESTIMATE TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestSlippageEstimate:
    """Test SlippageEstimate properties."""

    def test_combined_multiplier(self):
        base = _make_base_estimate()
        est = SlippageEstimate(
            base_estimate=base,
            fill_price=0.51, slippage=0.01, slippage_pct=0.02, fill_ratio=1.0,
            vol_multiplier=0.7, regime_multiplier=1.5, calibration_multiplier=1.1,
            adjusted_slippage=0.01, adjusted_p50_slippage=0.01,
            adjusted_p95_slippage=0.015, adjusted_p99_slippage=0.02,
        )
        # 0.7 * 1.5 * 1.1 = 1.155
        assert est.total_multiplier == pytest.approx(1.155, abs=0.001)

    def test_default_multipliers_are_one(self):
        base = _make_base_estimate()
        est = SlippageEstimate(
            base_estimate=base,
            fill_price=0.51, slippage=0.01, slippage_pct=0.02, fill_ratio=1.0,
            adjusted_slippage=0.01, adjusted_p50_slippage=0.01,
            adjusted_p95_slippage=0.015, adjusted_p99_slippage=0.02,
        )
        assert est.vol_multiplier == 1.0
        assert est.regime_multiplier == 1.0
        assert est.calibration_multiplier == 1.0
        assert est.total_multiplier == 1.0

    def test_adjusted_fill_price_entry(self):
        """Entry: adjusted_fill_price = mid + adjusted_slippage (positive)."""
        base = FillEstimate(
            fill_price=0.51, slippage=0.01, slippage_pct=0.02,
            fill_ratio=1.0, mid_price=0.50,
        )
        est = SlippageEstimate(
            base_estimate=base,
            fill_price=0.51, slippage=0.01, slippage_pct=0.02, fill_ratio=1.0,
            vol_multiplier=2.0,
            adjusted_slippage=0.02, adjusted_p50_slippage=0.02,
            adjusted_p95_slippage=0.03, adjusted_p99_slippage=0.04,
        )
        # mid(0.50) + adjusted_slippage(0.02) = 0.52
        assert est.adjusted_fill_price == pytest.approx(0.52, abs=0.001)

    def test_adjusted_fill_price_exit(self):
        """Exit: adjusted_fill_price = mid + adjusted_slippage (negative)."""
        base = FillEstimate(
            fill_price=0.49, slippage=-0.01, slippage_pct=0.02,
            fill_ratio=1.0, mid_price=0.50,
        )
        est = SlippageEstimate(
            base_estimate=base,
            fill_price=0.49, slippage=-0.01, slippage_pct=0.02, fill_ratio=1.0,
            vol_multiplier=2.0,
            # v2 sign-preserving: exit has negative adjusted_slippage
            adjusted_slippage=-0.02, adjusted_p50_slippage=0.02,
            adjusted_p95_slippage=0.03, adjusted_p99_slippage=0.04,
        )
        # mid(0.50) + adjusted_slippage(-0.02) = 0.48
        assert est.adjusted_fill_price == pytest.approx(0.48, abs=0.001)

    def test_is_full_fill_delegates(self):
        base = FillEstimate(
            fill_price=0.51, slippage=0.01, slippage_pct=0.02,
            fill_ratio=0.5, mid_price=0.50,
        )
        est = SlippageEstimate(
            base_estimate=base,
            fill_price=0.51, slippage=0.01, slippage_pct=0.02, fill_ratio=0.5,
            adjusted_slippage=0.01, adjusted_p50_slippage=0.01,
            adjusted_p95_slippage=0.015, adjusted_p99_slippage=0.02,
        )
        assert not est.is_full_fill
        assert est.effective_price == pytest.approx(0.505, abs=0.001)

    def test_regime_and_vol_stored(self):
        base = _make_base_estimate()
        est = SlippageEstimate(
            base_estimate=base,
            fill_price=0.51, slippage=0.01, slippage_pct=0.02, fill_ratio=1.0,
            adjusted_slippage=0.01, adjusted_p50_slippage=0.01,
            adjusted_p95_slippage=0.015, adjusted_p99_slippage=0.02,
            regime="PANIC", volatility=0.35,
        )
        assert est.regime == "PANIC"
        assert est.volatility == 0.35


# ══════════════════════════════════════════════════════════════════════════
# VOLATILITY ADJUSTER TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestVolatilityAdjuster:
    """Test volatility-based slippage adjustment."""

    @pytest.fixture
    def adjuster(self):
        return VolatilityAdjuster()

    def test_no_volatility_returns_one(self, adjuster):
        assert adjuster.apply(None) == 1.0
        assert adjuster.apply(0.0) == 1.0

    def test_low_vol_reduces_slippage(self, adjuster):
        # Low vol (< 0.05) → 0.7x multiplier
        result = adjuster.apply(0.02)
        assert result < 1.0
        assert result == pytest.approx(0.7, abs=0.01)

    def test_high_vol_increases_slippage(self, adjuster):
        # High vol (> 0.30) → 2.0x+
        result = adjuster.apply(0.40)
        assert result > 1.0
        assert result >= 2.0

    def test_normal_vol_between(self, adjuster):
        """vol = 0.15 → interpolated between 0.7 and 2.0."""
        result = adjuster.apply(0.15)
        # ratio = (0.15 - 0.05) / (0.30 - 0.05) = 0.10 / 0.25 = 0.4
        # 0.7 + 0.4 * (2.0 - 0.7) = 0.7 + 0.52 = 1.22
        assert 0.7 < result < 2.0
        assert result == pytest.approx(1.22, abs=0.05)

    def test_extreme_vol_capped(self, adjuster):
        """Extreme vol → capped at max_multiplier=5.0."""
        result = adjuster.apply(2.0)
        assert result <= 5.0

    def test_custom_config(self):
        cfg = VolatilityConfig(
            low_vol_threshold=0.10,
            high_vol_threshold=0.50,
            low_vol_multiplier=0.5,
            high_vol_multiplier=3.0,
            max_multiplier=10.0,
        )
        adj = VolatilityAdjuster(cfg)
        assert adj.apply(0.05) == 0.5  # below low threshold
        assert adj.apply(0.60) > 3.0  # above high threshold

    def test_config_validation(self):
        with pytest.raises(ValueError):
            VolatilityConfig(
                low_vol_threshold=0.50,
                high_vol_threshold=0.30,  # low > high
            ).validate()


# ══════════════════════════════════════════════════════════════════════════
# REGIME SCALING TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestRegimeScaling:
    """Test regime-based slippage scaling."""

    @pytest.fixture
    def scaling(self):
        return RegimeScaling()

    def test_none_returns_one(self, scaling):
        assert scaling.apply(None) == 1.0

    def test_chop_normal(self, scaling):
        """CHOP regime → no adjustment (1.0)."""
        assert scaling.apply("CHOP") == 1.0
        assert scaling.apply("chop") == 1.0

    def test_panic_highest(self, scaling):
        """PANIC should have the highest multiplier."""
        assert scaling.apply("PANIC") == 2.0

    def test_all_regimes_positive(self, scaling):
        for regime in ("TREND", "CHOP", "PANIC", "ILLIQUID", "EVENT_DRIVEN"):
            result = scaling.apply(regime)
            assert result >= 1.0, f"{regime} multiplier should be >= 1.0"
            assert result <= 3.0, f"{regime} multiplier should be <= 3.0"

    def test_case_insensitive(self, scaling):
        assert scaling.apply("trend") == scaling.apply("TREND")
        assert scaling.apply("Panic") == scaling.apply("PANIC")
        assert scaling.apply("ChOp") == scaling.apply("CHOP")

    def test_unknown_regime_defaults(self, scaling):
        assert scaling.apply("BULL_MARKET") == 1.0

    def test_empty_string_returns_one(self, scaling):
        """Empty regime string → no adjustment."""
        assert scaling.apply("") == 1.0

    def test_custom_config(self):
        cfg = RegimeScalingConfig(
            panic_multiplier=3.0,
            illiquid_multiplier=2.5,
        )
        sc = RegimeScaling(cfg)
        assert sc.apply("PANIC") == 3.0
        assert sc.apply("ILLIQUID") == 2.5

    def test_config_validation(self):
        with pytest.raises(ValueError):
            RegimeScalingConfig(panic_multiplier=-1.0).validate()


# ══════════════════════════════════════════════════════════════════════════
# SLIPPAGE TRACKER TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestSlippageTracker:
    """Test expected-vs-actual slippage tracking."""

    @pytest.fixture
    def tracker(self):
        return SlippageTracker()

    def test_initial_calibration_is_one(self, tracker):
        assert tracker.calibration_multiplier == 1.0
        assert tracker.window_size == 0

    def test_empty_stats(self, tracker):
        stats = tracker.get_stats()
        assert stats["samples"] == 0
        assert stats["mean_ratio"] is None
        assert stats["calibration_multiplier"] == 1.0

    def test_record_below_threshold_does_not_adjust(self, tracker):
        """< 3 samples → no calibration adjustment."""
        tracker.record(expected_slippage=0.01, actual_slippage=0.01)
        tracker.record(expected_slippage=0.01, actual_slippage=0.01)
        assert tracker.calibration_multiplier == 1.0

    def test_record_at_parity_no_change(self, tracker):
        """Expected = Actual → no adjustment needed."""
        for _ in range(20):
            tracker.record(expected_slippage=0.01, actual_slippage=0.01)
        assert tracker.calibration_multiplier == 1.0

    def test_record_underestimation_increases(self, tracker):
        """Actual > Expected consistently → increase calibration."""
        for _ in range(20):
            tracker.record(expected_slippage=0.01, actual_slippage=0.02)
        assert tracker.calibration_multiplier > 1.0

    def test_record_overestimation_decreases(self, tracker):
        """Expected > Actual consistently → decrease calibration."""
        for _ in range(20):
            tracker.record(expected_slippage=0.02, actual_slippage=0.01)
        assert tracker.calibration_multiplier < 1.0

    def test_calibration_capped(self, tracker):
        """Calibration can't exceed max_multiplier."""
        config = SlippageTrackerConfig(max_multiplier=2.0, window_size=5,
                                         adjustment_step=0.5)
        tracker = SlippageTracker(config)
        for _ in range(20):
            tracker.record(expected_slippage=0.01, actual_slippage=0.10)
        assert tracker.calibration_multiplier <= 2.0

    def test_calibration_floored(self, tracker):
        """Calibration can't go below min_multiplier."""
        config = SlippageTrackerConfig(min_multiplier=0.5, window_size=5,
                                         adjustment_step=0.5)
        tracker = SlippageTracker(config)
        for _ in range(20):
            tracker.record(expected_slippage=0.10, actual_slippage=0.01)
        assert tracker.calibration_multiplier >= 0.5

    def test_zero_expected_ignored(self, tracker):
        """Zero expected slippage should not affect tracker."""
        tracker.record(expected_slippage=0.0, actual_slippage=0.01)
        assert tracker.window_size == 0  # Ignored

    def test_stats_populated(self, tracker):
        for i in range(5):
            tracker.record(expected_slippage=0.01, actual_slippage=0.01 + i * 0.001)
        stats = tracker.get_stats()
        assert stats["samples"] == 5
        assert stats["mean_ratio"] is not None
        assert "calibration_multiplier" in stats


# ══════════════════════════════════════════════════════════════════════════
# SLIPPAGE ENGINE TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestSlippageEngine:
    """Test the unified SlippageEngine."""

    @pytest.fixture
    def engine(self):
        return SlippageEngine()

    def test_estimate_basic(self, engine):
        """Basic estimate without volatility or regime."""
        tick = _make_tick()
        est = engine.estimate(tick, order_size=10.0, asset="BTC")

        assert isinstance(est, SlippageEstimate)
        assert est.fill_price > 0
        assert est.slippage > 0  # entry positive
        assert est.adjusted_slippage > 0  # sign preserved for entry
        assert est.vol_multiplier == 1.0  # no vol adjustment
        assert est.regime_multiplier == 1.0  # no regime
        assert est.adjusted_slippage == pytest.approx(est.slippage, abs=0.001)

    def test_estimate_exit_side(self, engine):
        """Exit estimation → negative slippage, uses estimate_exit."""
        tick = _make_tick()
        est = engine.estimate(tick, order_size=10.0, asset="BTC", side="exit")

        assert isinstance(est, SlippageEstimate)
        assert est.slippage < 0  # exit negative
        assert est.adjusted_slippage < 0  # sign preserved for exit
        assert est.fill_price < est.base_estimate.mid_price  # sell below mid

    def test_estimate_with_volatility(self, engine):
        """High volatility → increased adjusted slippage."""
        tick = _make_tick()
        est_low = engine.estimate(tick, 10.0, "BTC", volatility=0.02)
        est_high = engine.estimate(tick, 10.0, "BTC", volatility=0.40)

        # High vol should have higher adjusted slippage
        assert est_high.adjusted_slippage > est_low.adjusted_slippage
        assert est_high.vol_multiplier > 1.0
        assert est_low.vol_multiplier < 1.0

    def test_estimate_with_regime(self, engine):
        """PANIC regime → higher adjusted slippage than CHOP."""
        tick = _make_tick()
        est_chop = engine.estimate(tick, 10.0, "BTC", regime="CHOP")
        est_panic = engine.estimate(tick, 10.0, "BTC", regime="PANIC")

        assert est_panic.adjusted_slippage > est_chop.adjusted_slippage
        assert est_panic.regime_multiplier == 2.0
        assert est_chop.regime_multiplier == 1.0

    def test_estimate_with_both_factors(self, engine):
        """Volatility × Regime both apply."""
        tick = _make_tick()
        est = engine.estimate(tick, 10.0, "BTC", volatility=0.40, regime="PANIC")

        # vol_mult ~2.3 × regime_mult 2.0 → combined ~4.6
        assert est.vol_multiplier > 1.0
        assert est.regime_multiplier > 1.0
        assert est.total_multiplier > 3.0
        # Adjusted slippage should be significantly higher
        assert est.adjusted_slippage > est.slippage * 2.0

    def test_estimate_with_different_assets(self, engine):
        """Different assets use different base profiles."""
        tick = _make_tick()
        est_btc = engine.estimate(tick, 50.0, "BTC", volatility=0.10, regime="CHOP")
        est_eth = engine.estimate(tick, 50.0, "ETH", volatility=0.10, regime="CHOP")

        # ETH has higher base_impact_bps → adjusted_slippage should be higher
        assert est_eth.adjusted_slippage >= est_btc.adjusted_slippage, (
            f"ETH ({est_eth.adjusted_slippage}) should be >= "
            f"BTC ({est_btc.adjusted_slippage})"
        )

    def test_estimate_adjusted_percentiles(self, engine):
        """P50/P95/P99 are adjusted by multipliers."""
        tick = _make_tick()
        est = engine.estimate(tick, 10.0, "BTC", volatility=0.40, regime="PANIC")

        assert est.adjusted_p50_slippage > 0
        assert est.adjusted_p50_slippage <= est.adjusted_p95_slippage <= est.adjusted_p99_slippage

    def test_record_actual_updates_tracker(self, engine):
        """record_actual feeds the SlippageTracker."""
        tick = _make_tick()
        est = engine.estimate(tick, 10.0, "BTC")

        assert engine.get_tracker_stats()["samples"] == 0

        engine.record_actual(est, actual_fill_price=0.52)

        assert engine.get_tracker_stats()["samples"] == 1

    def test_record_actual_learning_loop(self, engine):
        """After many underestimated fills, calibration increases significantly."""
        tick = _make_tick(best_bid=0.49, best_ask=0.51)

        initial_cal = engine.calibration_multiplier
        assert initial_cal == 1.0

        # Simulate 20 fills where actual is consistently 3x worse than estimate
        # The tracker compares ADJUSTED expected vs actual, so for the
        # calibration to move, actual must exceed adjusted (not just base).
        for _ in range(20):
            est = engine.estimate(tick, 10.0, "BTC")
            # Actual fill much worse → ratio > 1.1 after a few samples
            engine.record_actual(est, actual_fill_price=0.55)

        stats = engine.get_tracker_stats()
        assert stats["samples"] >= 3
        # Calibration should have increased well above 1.0
        assert engine.calibration_multiplier > 1.05, (
            f"Expected calibration > 1.05 after 20 poor fills, "
            f"got {engine.calibration_multiplier}"
        )

    def test_record_actual_with_zero_mid_ignored(self, engine):
        """Zero mid price → record_actual is a no-op."""
        base = FillEstimate(fill_price=0.51, slippage=0.01, slippage_pct=0.02,
                            fill_ratio=1.0, mid_price=0.0)
        est = SlippageEstimate(
            base_estimate=base,
            fill_price=0.51, slippage=0.01, slippage_pct=0.02, fill_ratio=1.0,
            adjusted_slippage=0.01, adjusted_p50_slippage=0.01,
            adjusted_p95_slippage=0.015, adjusted_p99_slippage=0.02,
        )
        engine.record_actual(est, actual_fill_price=0.52)
        assert engine.get_tracker_stats()["samples"] == 0

    def test_estimate_exit_sign_preserved(self, engine):
        """Exit estimate preserves negative adjusted_slippage."""
        tick = _make_tick()
        est = engine.estimate(tick, order_size=10.0, asset="BTC", side="exit",
                              volatility=0.40, regime="PANIC")

        assert est.slippage < 0  # base exit is negative
        assert est.adjusted_slippage < 0  # adjusted preserves sign
        # Adjusted magnitude should be larger (vol × regime multipliers)
        assert abs(est.adjusted_slippage) > abs(est.slippage)

    def test_custom_components_injected(self, engine):
        """Custom FillSimulator, VolatilityAdjuster, etc. can be injected."""
        from unittest.mock import MagicMock
        mock_fs = MagicMock(spec=FillSimulator)
        mock_fs.estimate_entry.return_value = FillEstimate(
            fill_price=0.55, slippage=0.02, slippage_pct=0.036,
            fill_ratio=1.0, p50_slippage=0.02, p95_slippage=0.03,
            p99_slippage=0.04, mid_price=0.53,
        )
        mock_vol = MagicMock(spec=VolatilityAdjuster)
        mock_vol.apply.return_value = 0.5
        mock_reg = MagicMock(spec=RegimeScaling)
        mock_reg.apply.return_value = 2.0

        engine = SlippageEngine(
            fill_simulator=mock_fs,
            vol_adjuster=mock_vol,
            regime_scaling=mock_reg,
        )

        tick = _make_tick()
        est = engine.estimate(tick, 10.0, "BTC", volatility=0.02, regime="CHOP")

        # mocks were called
        mock_fs.estimate_entry.assert_called_once()
        mock_vol.apply.assert_called_once_with(0.02)
        mock_reg.apply.assert_called_once_with("CHOP")

        # combined = 0.5 * 2.0 = 1.0
        assert est.total_multiplier == pytest.approx(1.0, abs=0.001)


# ══════════════════════════════════════════════════════════════════════════
# INTEGRATION — Engine with real FillSimulator
# ══════════════════════════════════════════════════════════════════════════


class TestSlippageEngineIntegration:
    """Integration tests with real FillSimulator and all components."""

    @pytest.fixture
    def engine(self):
        return SlippageEngine()

    def test_full_pipeline_entry(self, engine):
        """Full pipeline: tick → estimate → adjusted → record → check calibration."""
        tick = _make_tick(spread=0.03, asks_vol_1=5000, asks_vol_2=2000, asks_vol_3=500)

        # High vol + PANIC → both multipliers active
        est = engine.estimate(
            tick, order_size=500.0, asset="ETH",
            volatility=0.35, regime="PANIC",
        )

        assert est.base_estimate.levels_consumed >= 1  # ate some depth
        assert est.adjusted_slippage > est.slippage  # adjustments applied
        assert est.vol_multiplier > 1.0  # high vol
        assert est.regime_multiplier > 1.0  # PANIC

        # Record actual (worse than expected)
        engine.record_actual(est, actual_fill_price=0.55)
        stats = engine.get_tracker_stats()
        assert stats["samples"] == 1

    def test_multiple_assets_different_profiles(self, engine):
        """BTC and ETH with same inputs → different adjusted slippage."""
        tick = _make_tick()
        est_btc = engine.estimate(tick, 100.0, "BTC",
                                  volatility=0.20, regime="TREND")
        est_eth = engine.estimate(tick, 100.0, "ETH",
                                  volatility=0.20, regime="TREND")

        # Same volatility and regime, but ETH has higher base impact
        assert est_btc.vol_multiplier == pytest.approx(est_eth.vol_multiplier, abs=0.01)
        assert est_btc.regime_multiplier == est_eth.regime_multiplier
        assert est_eth.adjusted_slippage >= est_btc.adjusted_slippage

    def test_no_dynamic_factors_equals_base(self, engine):
        """Without volatility/regime, adjusted equals base."""
        tick = _make_tick()
        est = engine.estimate(tick, 10.0, "BTC")

        assert est.adjusted_slippage == pytest.approx(
            abs(est.slippage), abs=0.001
        )
        assert est.adjusted_p50_slippage == pytest.approx(
            est.base_estimate.p50_slippage, abs=0.001
        )
