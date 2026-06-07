# tests/unit/test_liquidity_sizer.py

"""P11.3 — Liquidity-Aware Trading unit tests."""

import pytest

from src.execution.liquidity_sizer import (
    LiquidityAssessment,
    LiquidityAwareSizer,
    LiquiditySizerConfig,
)


def _make_tick_data(
    spread: float = 0.01,
    bids_vol_1: float = 1000.0,
    asks_vol_1: float = 1000.0,
    volume_24h: float = 5000.0,
    best_bid: float = 0.49,
    best_ask: float = 0.51,
) -> dict:
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "bids_vol_1": bids_vol_1,
        "bids_vol_2": 500.0,
        "bids_vol_3": 200.0,
        "asks_vol_1": asks_vol_1,
        "asks_vol_2": 500.0,
        "asks_vol_3": 200.0,
        "volume_24h": volume_24h,
    }


# ══════════════════════════════════════════════════════════════════════════
# CONFIG TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestLiquiditySizerConfig:

    def test_defaults_are_valid(self):
        cfg = LiquiditySizerConfig()
        cfg.validate()  # Should not raise

    def test_invalid_min_depth_coverage(self):
        with pytest.raises(ValueError, match="min_depth_coverage"):
            LiquiditySizerConfig(min_depth_coverage=0).validate()

    def test_invalid_size_floor_pct_zero(self):
        with pytest.raises(ValueError, match="size_floor_pct"):
            LiquiditySizerConfig(size_floor_pct=0).validate()

    def test_invalid_size_floor_pct_over_one(self):
        with pytest.raises(ValueError, match="size_floor_pct"):
            LiquiditySizerConfig(size_floor_pct=1.5).validate()

    def test_invalid_severe_depth_coverage(self):
        with pytest.raises(ValueError, match="severe_depth_coverage"):
            LiquiditySizerConfig(severe_depth_coverage=0).validate()

    def test_invalid_spread_max_penalty(self):
        with pytest.raises(ValueError, match="spread_max_penalty"):
            LiquiditySizerConfig(spread_max_penalty=0).validate()

    def test_invalid_volume_max_penalty(self):
        with pytest.raises(ValueError, match="volume_max_penalty"):
            LiquiditySizerConfig(volume_max_penalty=0).validate()

    def test_custom_config_accepted(self):
        cfg = LiquiditySizerConfig(
            min_depth_coverage=5.0,
            max_spread=0.03,
            min_volume_24h=1000.0,
            size_floor_pct=0.20,
        )
        cfg.validate()


# ══════════════════════════════════════════════════════════════════════════
# LIQUIDITY ASSESSMENT TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestLiquidityAssessment:

    def test_full_liquidity_no_reduction(self):
        assessment = LiquidityAssessment(
            liquidity_multiplier=1.0,
            recommended_size=10.0,
            original_size=10.0,
            depth_coverage=10.0,
        )
        assert not assessment.is_reduced
        assert assessment.reduction_pct == 0.0
        assert not assessment.is_severe

    def test_partial_reduction(self):
        assessment = LiquidityAssessment(
            liquidity_multiplier=0.60,
            recommended_size=6.0,
            original_size=10.0,
            depth_coverage=1.5,
        )
        assert assessment.is_reduced
        assert assessment.reduction_pct == 40.0
        assert not assessment.is_severe

    def test_severe_reduction(self):
        assessment = LiquidityAssessment(
            liquidity_multiplier=0.30,
            recommended_size=3.0,
            original_size=10.0,
            depth_coverage=0.5,
        )
        assert assessment.is_reduced
        assert assessment.is_severe
        assert assessment.reduction_pct == 70.0

    def test_properties_with_defaults(self):
        assessment = LiquidityAssessment(
            liquidity_multiplier=0.80,
            recommended_size=8.0,
            original_size=10.0,
            depth_coverage=2.0,
            depth_factor=0.8,
            spread_factor=1.0,
            volume_factor=1.0,
            spread=0.01,
            volume_24h=5000.0,
            total_depth=20.0,
            reasons=["thin_depth: coverage=2.0x (need ≥3.0x)"],
        )
        assert assessment.is_reduced
        assert assessment.depth_factor == 0.8
        assert assessment.spread_factor == 1.0
        assert assessment.volume_factor == 1.0


# ══════════════════════════════════════════════════════════════════════════
# DEPTH FACTOR TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestDepthFactor:

    def test_ample_depth_no_reduction(self):
        sizer = LiquidityAwareSizer()
        # 10000 depth / 10 size = 1000x coverage >> 3x min → no reduction
        tick = _make_tick_data(asks_vol_1=10000.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.depth_factor == 1.0
        assert result.liquidity_multiplier == 1.0
        assert not result.is_reduced

    def test_moderate_thin_depth(self):
        sizer = LiquidityAwareSizer()
        # 20 depth / 10 size = 2.0x coverage (between severe=1.0 and min=3.0)
        tick = _make_tick_data(asks_vol_1=20.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0)
        # Linear: ratio = (2.0 - 1.0) / (3.0 - 1.0) = 0.5
        # factor = 0.25 + 0.5 * 0.75 = 0.625
        assert result.depth_factor < 1.0
        assert result.depth_factor > 0.25  # Above floor
        assert result.is_reduced

    def test_severe_thin_depth(self):
        sizer = LiquidityAwareSizer()
        # 5 depth / 10 size = 0.5x coverage < severe=1.0 → floor
        tick = _make_tick_data(asks_vol_1=5.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.depth_factor == 0.25  # size_floor_pct
        assert result.is_severe

    def test_zero_depth(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(asks_vol_1=0.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.depth_factor == 0.25  # Falls to floor

    def test_depth_at_min_threshold(self):
        sizer = LiquidityAwareSizer()
        # Exactly at threshold: 30 depth / 10 size = 3.0x → no reduction
        tick = _make_tick_data(asks_vol_1=30.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.depth_factor == 1.0

    def test_exit_side_uses_bids(self):
        sizer = LiquidityAwareSizer()
        # Bids are thin, asks are deep → exit should be reduced
        tick = _make_tick_data(bids_vol_1=5.0, asks_vol_1=10000.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0, side="exit")
        assert result.depth_factor < 1.0
        assert result.is_reduced

    def test_entry_side_uses_asks(self):
        sizer = LiquidityAwareSizer()
        # Asks are thin, bids are deep → entry should be reduced
        tick = _make_tick_data(bids_vol_1=10000.0, asks_vol_1=5.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0, side="entry")
        assert result.depth_factor < 1.0
        assert result.is_reduced


# ══════════════════════════════════════════════════════════════════════════
# SPREAD FACTOR TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestSpreadFactor:

    def test_normal_spread_no_penalty(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(spread=0.02, asks_vol_1=10000.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.spread_factor == 1.0

    def test_wide_spread_penalty(self):
        sizer = LiquidityAwareSizer()
        # spread=0.10, max_spread=0.05 → excess=1.0 → max penalty
        tick = _make_tick_data(spread=0.10, asks_vol_1=10000.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.spread_factor < 1.0
        assert result.spread_factor >= 0.70  # spread_max_penalty

    def test_extreme_spread_max_penalty(self):
        sizer = LiquidityAwareSizer()
        # Very wide spread
        tick = _make_tick_data(spread=0.50, asks_vol_1=10000.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.spread_factor == 0.70  # spread_max_penalty

    def test_spread_at_threshold(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(spread=0.05, asks_vol_1=10000.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.spread_factor == 1.0  # At threshold, no penalty


# ══════════════════════════════════════════════════════════════════════════
# VOLUME FACTOR TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestVolumeFactor:

    def test_healthy_volume_no_penalty(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(asks_vol_1=10000.0, volume_24h=5000.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.volume_factor == 1.0

    def test_low_volume_penalty(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(asks_vol_1=10000.0, volume_24h=250.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.volume_factor < 1.0
        assert result.volume_factor >= 0.60

    def test_zero_volume(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(asks_vol_1=10000.0, volume_24h=0.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.volume_factor == 0.60  # volume_max_penalty

    def test_volume_at_threshold(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(asks_vol_1=10000.0, volume_24h=500.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.volume_factor == 1.0


# ══════════════════════════════════════════════════════════════════════════
# COMBINED FACTOR TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestCombinedFactors:

    def test_all_good_no_reduction(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(
            spread=0.01, asks_vol_1=10000.0, volume_24h=50000.0
        )
        result = sizer.assess(tick, order_size=10.0)
        assert result.liquidity_multiplier == 1.0
        assert result.recommended_size == 10.0
        assert "adequate_liquidity" in result.reasons

    def test_all_bad_compounds_to_floor(self):
        sizer = LiquidityAwareSizer()
        # Thin depth + wide spread + low volume
        tick = _make_tick_data(
            spread=0.50, asks_vol_1=5.0, volume_24h=0.0
        )
        result = sizer.assess(tick, order_size=10.0)
        # depth=0.25 × spread=0.70 × volume=0.60 = 0.105 → floor 0.25
        assert result.liquidity_multiplier == 0.25
        assert result.recommended_size == 2.5
        assert result.is_severe

    def test_thin_depth_only(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(asks_vol_1=5.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=10.0)
        assert result.liquidity_multiplier == 0.25  # depth floor

    def test_wide_spread_only(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(
            spread=0.10, asks_vol_1=10000.0, volume_24h=50000.0
        )
        result = sizer.assess(tick, order_size=10.0)
        # spread_factor=0.70, others=1.0 → 0.70
        assert result.liquidity_multiplier == 0.70

    def test_low_volume_only(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(asks_vol_1=10000.0, volume_24h=0.0)
        result = sizer.assess(tick, order_size=10.0)
        # volume_factor=0.60, others=1.0 → 0.60
        assert result.liquidity_multiplier == 0.60


# ══════════════════════════════════════════════════════════════════════════
# EDGE CASES
# ══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:

    def test_zero_order_size(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data()
        result = sizer.assess(tick, order_size=0.0)
        assert result.liquidity_multiplier == 1.0
        assert result.recommended_size == 0.0

    def test_negative_order_size(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data()
        result = sizer.assess(tick, order_size=-1.0)
        assert result.liquidity_multiplier == 1.0

    def test_very_large_order_size(self):
        sizer = LiquidityAwareSizer()
        # 100 USDC order with only 10 depth → coverage = 0.1x
        tick = _make_tick_data(asks_vol_1=10.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=100.0)
        assert result.liquidity_multiplier == 0.25  # Floor

    def test_small_order_relative_to_depth(self):
        sizer = LiquidityAwareSizer()
        # 1 USDC order with 10000 depth → coverage = 10000x
        tick = _make_tick_data(asks_vol_1=10000.0, volume_24h=50000.0)
        result = sizer.assess(tick, order_size=1.0)
        assert result.liquidity_multiplier == 1.0

    def test_confidence_degraded_with_fallback_data(self):
        sizer = LiquidityAwareSizer()
        # No depth, no volume, no spread → all fallbacks
        tick = {
            "best_bid": 0.49, "best_ask": 0.51,
            "spread": 0.0, "volume_24h": 0.0,
            "bids_vol_1": 0.0, "bids_vol_2": 0.0, "bids_vol_3": 0.0,
            "asks_vol_1": 0.0, "asks_vol_2": 0.0, "asks_vol_3": 0.0,
        }
        result = sizer.assess(tick, order_size=10.0)
        assert result.confidence < 1.0

    def test_reasons_list_includes_all_factors(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(
            spread=0.10, asks_vol_1=15.0, volume_24h=100.0
        )
        result = sizer.assess(tick, order_size=10.0)
        assert len(result.reasons) >= 2  # At least depth + spread/volume
        assert any("thin_depth" in r for r in result.reasons)


# ══════════════════════════════════════════════════════════════════════════
# CONVENIENCE METHOD
# ══════════════════════════════════════════════════════════════════════════


class TestComputeMultiplier:

    def test_returns_multiplier(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(asks_vol_1=10000.0, volume_24h=50000.0)
        mult = sizer.compute_multiplier(tick, order_size=10.0)
        assert mult == 1.0

    def test_reduced_multiplier(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(asks_vol_1=5.0, volume_24h=50000.0)
        mult = sizer.compute_multiplier(tick, order_size=10.0)
        assert mult == 0.25


# ══════════════════════════════════════════════════════════════════════════
# DETERMINISM
# ══════════════════════════════════════════════════════════════════════════


class TestDeterminism:

    def test_same_input_same_output(self):
        sizer = LiquidityAwareSizer()
        tick = _make_tick_data(asks_vol_1=20.0, volume_24h=200.0)
        r1 = sizer.assess(tick, order_size=10.0)
        r2 = sizer.assess(tick, order_size=10.0)
        assert r1.liquidity_multiplier == r2.liquidity_multiplier
        assert r1.recommended_size == r2.recommended_size
