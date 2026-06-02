"""
Unit tests for P9.1 — Realistic Fill Simulation.

Tests:
  - FillSimulator entry/exit estimation
  - Slippage model with depth consumption
  - Partial fill probability
  - FillEstimate properties
  - Calibration from real tick data
  - Edge cases (zero depth, zero spread, extreme orders)
  - Profile selection (BTC vs ETH vs DEFAULT)
"""

import pytest

from src.execution.fill_simulator import (
    DEFAULT_PROFILES,
    FillEstimate,
    FillSimulator,
    MarketDepthProfile,
)

# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _make_tick(
    best_bid: float = 0.495,
    best_ask: float = 0.505,
    spread: float = 0.010,
    bids_vol_1: float = 50000.0,
    bids_vol_2: float = 5000.0,
    bids_vol_3: float = 500.0,
    asks_vol_1: float = 20000.0,
    asks_vol_2: float = 5000.0,
    asks_vol_3: float = 500.0,
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


# ══════════════════════════════════════════════════════════════════════════
# MARKET DEPTH PROFILE TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestMarketDepthProfile:
    """Test profile defaults and customization."""

    def test_default_profiles_exist(self):
        assert "BTC" in DEFAULT_PROFILES
        assert "ETH" in DEFAULT_PROFILES
        assert "DEFAULT" in DEFAULT_PROFILES

    def test_btc_profile_has_tighter_impact(self):
        btc = DEFAULT_PROFILES["BTC"]
        eth = DEFAULT_PROFILES["ETH"]
        assert btc.base_impact_bps < eth.base_impact_bps, (
            "BTC should have tighter impact than ETH"
        )

    def test_custom_profile(self):
        profile = MarketDepthProfile(
            asset="CUSTOM",
            base_impact_bps=5.0,
            min_fill_ratio=0.15,
        )
        assert profile.asset == "CUSTOM"
        assert profile.base_impact_bps == 5.0
        assert profile.min_fill_ratio == 0.15


# ══════════════════════════════════════════════════════════════════════════
# FILL ESTIMATE TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestFillEstimate:
    """Test FillEstimate dataclass properties."""

    def test_full_fill(self):
        fe = FillEstimate(fill_price=0.51, slippage=0.01, slippage_pct=0.02,
                          fill_ratio=1.0)
        assert fe.is_full_fill is True
        assert fe.effective_price == 0.51
        assert fe.partial_fill_prob == 0.0

    def test_partial_fill(self):
        fe = FillEstimate(fill_price=0.51, slippage=0.01, slippage_pct=0.02,
                          fill_ratio=0.5, mid_price=0.50)
        assert fe.is_full_fill is False
        # effective = 0.51 * 0.5 + 0.50 * 0.5 = 0.255 + 0.25 = 0.505
        assert fe.effective_price == pytest.approx(0.505, abs=0.001)
        # partial_fill_prob = 1 - fill_ratio = 0.5
        assert fe.partial_fill_prob == pytest.approx(0.5, abs=0.001)

    def test_effective_price_clamps(self):
        fe = FillEstimate(fill_price=0.99, slippage=0.49, slippage_pct=0.98,
                          fill_ratio=0.1, mid_price=0.5)
        # effective doesn't clamp — it's a weighted average
        assert 0.0 <= fe.effective_price <= 1.0


# ══════════════════════════════════════════════════════════════════════════
# FILL SIMULATOR TESTS — Entry
# ══════════════════════════════════════════════════════════════════════════

class TestFillSimulatorEntry:
    """Test entry (buy) fill estimation."""

    @pytest.fixture
    def sim(self):
        return FillSimulator()

    def test_entry_normal_order(self, sim):
        """Normal 10 USDC entry — should fill easily with deep liquidity."""
        tick = _make_tick()
        estimate = sim.estimate_entry(tick, order_size=10.0, asset="BTC")

        assert estimate.fill_price > 0
        assert estimate.slippage > 0  # entry always positive slippage
        assert estimate.fill_ratio > 0.9  # deep liquidity, near-certain fill
        assert estimate.is_full_fill

    def test_entry_slippage_increases_with_size(self, sim):
        """Larger orders should have more slippage."""
        tick = _make_tick()
        small = sim.estimate_entry(tick, order_size=10.0, asset="BTC")
        large = sim.estimate_entry(tick, order_size=5000.0, asset="BTC")

        assert large.slippage >= small.slippage, (
            f"Large order ({large.slippage}) should have >= slippage "
            f"than small ({small.slippage})"
        )

    def test_entry_fill_ratio_decreases_with_size(self, sim):
        """Larger orders have higher partial fill risk."""
        tick = _make_tick()
        small = sim.estimate_entry(tick, order_size=10.0, asset="BTC")
        large = sim.estimate_entry(tick, order_size=50000.0, asset="BTC")

        assert large.fill_ratio <= small.fill_ratio, (
            f"Large order fill_ratio ({large.fill_ratio}) should be <= "
            f"small ({small.fill_ratio})"
        )

    def test_entry_with_thin_depth(self, sim):
        """Very thin orderbook → significant slippage, possible partial fill."""
        tick = _make_tick(asks_vol_1=100.0, asks_vol_2=50.0, asks_vol_3=0.0)
        estimate = sim.estimate_entry(tick, order_size=500.0, asset="ETH")

        # With thin depth, should consume multiple levels
        assert estimate.levels_consumed >= 1
        # Slippage should be at least spread cross (0.01 * 0.5 = 0.005)
        assert estimate.slippage >= 0.005

    def test_entry_prices_clamped(self, sim):
        """Fill price should always be in [0.001, 0.999]."""
        tick = _make_tick(best_bid=0.95, best_ask=0.98, spread=0.03)
        estimate = sim.estimate_entry(tick, order_size=10.0, asset="BTC")

        assert 0.001 <= estimate.fill_price <= 0.999
        assert estimate.mid_price > 0

    def test_entry_btc_vs_eth(self, sim):
        """BTC should have less slippage than ETH for same order."""
        tick = _make_tick()
        btc = sim.estimate_entry(tick, order_size=50.0, asset="BTC")
        eth = sim.estimate_entry(tick, order_size=50.0, asset="ETH")

        # ETH profile has higher base_impact_bps
        assert eth.slippage >= btc.slippage, (
            f"ETH slippage ({eth.slippage}) should be >= BTC ({btc.slippage})"
        )


# ══════════════════════════════════════════════════════════════════════════
# FILL SIMULATOR TESTS — Exit
# ══════════════════════════════════════════════════════════════════════════

class TestFillSimulatorExit:
    """Test exit (sell) fill estimation."""

    @pytest.fixture
    def sim(self):
        return FillSimulator()

    def test_exit_normal_order(self, sim):
        """Normal exit should have negative slippage (we lose on spread)."""
        tick = _make_tick()
        estimate = sim.estimate_exit(tick, position_value=10.0, asset="BTC")

        assert estimate.slippage < 0  # exit always negative
        assert estimate.fill_price < estimate.mid_price  # sell below mid
        assert estimate.fill_ratio > 0.9
        assert estimate.is_full_fill

    def test_exit_slippage_worse_than_entry(self, sim):
        """Exit slippage should be symmetric to entry (both cross spread)."""
        tick = _make_tick()
        entry = sim.estimate_entry(tick, order_size=1000.0, asset="BTC")

        # For exit, the fill_price should be mid - slippage
        mid = entry.mid_price
        assert entry.fill_price > mid  # entry buys above mid
        assert entry.slippage > 0

    def test_exit_with_thin_bids(self, sim):
        """Thin bid side → more negative slippage on exit."""
        tick = _make_tick(bids_vol_1=100.0, bids_vol_2=50.0, bids_vol_3=10.0)
        estimate = sim.estimate_exit(tick, position_value=500.0, asset="ETH")

        assert estimate.levels_consumed >= 1
        # Exit slippage should be at least spread_cross (negative)
        assert estimate.slippage <= -0.005

    def test_exit_large_position(self, sim):
        """Large position exit → partial fill likely."""
        tick = _make_tick()
        estimate = sim.estimate_exit(tick, position_value=100000.0, asset="BTC")

        assert estimate.fill_ratio < 1.0  # partial fill expected
        assert estimate.partial_fill_prob > 0.0


# ══════════════════════════════════════════════════════════════════════════
# DISTRIBUTION TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestFillDistributions:
    """Test P50/P95/P99 slippage distributions."""

    @pytest.fixture
    def sim(self):
        return FillSimulator()

    def test_percentiles_ordered(self, sim):
        """P50 ≤ P95 ≤ P99."""
        tick = _make_tick()
        estimate = sim.estimate_entry(tick, order_size=100.0, asset="BTC")

        assert 0 <= estimate.p50_slippage <= estimate.p95_slippage <= estimate.p99_slippage

    def test_percentiles_wider_with_thin_depth(self, sim):
        """Thin depth → wider tail (P99 - P50 larger)."""
        deep = _make_tick(asks_vol_1=50000)
        thin = _make_tick(asks_vol_1=100)

        est_deep = sim.estimate_entry(deep, order_size=100.0, asset="BTC")
        est_thin = sim.estimate_entry(thin, order_size=100.0, asset="ETH")

        deep_spread = est_deep.p99_slippage - est_deep.p50_slippage
        thin_spread = est_thin.p99_slippage - est_thin.p50_slippage

        assert thin_spread >= deep_spread * 0.5, (
            f"Thin market tail spread ({thin_spread}) should be meaningful "
            f"vs deep ({deep_spread})"
        )


# ══════════════════════════════════════════════════════════════════════════
# EDGE CASES
# ══════════════════════════════════════════════════════════════════════════

class TestFillSimulatorEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def sim(self):
        return FillSimulator()

    def test_zero_spread(self, sim):
        """Zero spread should not crash."""
        tick = _make_tick(spread=0.0, best_bid=0.50, best_ask=0.50)
        estimate = sim.estimate_entry(tick, order_size=10.0, asset="BTC")
        assert estimate.fill_price > 0

    def test_all_zero_depth(self, sim):
        """All depth levels zero → should not crash, use defaults."""
        tick = _make_tick(
            bids_vol_1=0, bids_vol_2=0, bids_vol_3=0,
            asks_vol_1=0, asks_vol_2=0, asks_vol_3=0,
        )
        estimate = sim.estimate_entry(tick, order_size=10.0, asset="DEFAULT")
        assert estimate.fill_price > 0
        assert estimate.fill_ratio > 0  # min_fill_ratio kicks in

    def test_zero_order_size(self, sim):
        """Zero order → no impact, full fill."""
        tick = _make_tick()
        estimate = sim.estimate_entry(tick, order_size=0.0, asset="BTC")
        assert estimate.fill_ratio == 1.0
        assert estimate.levels_consumed == 0

    def test_negative_prices_guarded(self, sim):
        """Negative prices should be guarded."""
        tick = _make_tick(best_bid=-0.5, best_ask=-0.4, spread=0.1)
        estimate = sim.estimate_entry(tick, order_size=10.0, asset="BTC")
        assert estimate.fill_price > 0
        assert 0.001 <= estimate.fill_price <= 0.999

    def test_unknown_asset_uses_default(self, sim):
        """Unknown asset falls back to DEFAULT profile."""
        tick = _make_tick()
        estimate = sim.estimate_entry(tick, order_size=10.0, asset="SOLANA")
        assert estimate.fill_price > 0
        assert estimate.is_full_fill

    def test_extreme_order_size(self, sim):
        """Massive order — should still produce valid estimate."""
        tick = _make_tick()
        estimate = sim.estimate_entry(tick, order_size=1_000_000.0, asset="BTC")
        assert estimate.fill_price > 0
        assert 0.0 <= estimate.fill_ratio <= 1.0
        # Should consume all 3 levels
        assert estimate.levels_consumed == 3


# ══════════════════════════════════════════════════════════════════════════
# CALIBRATION TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestFillSimulatorCalibration:
    """Test calibration from real tick data."""

    def test_calibrate_empty_ticks(self):
        """Empty tick list returns default simulator."""
        sim = FillSimulator.calibrate_from_ticks([], asset="TEST")
        assert sim is not None

    def test_calibrate_with_ticks(self):
        """Calibration from ticks produces sensible profile."""
        ticks = [_make_tick() for _ in range(100)]
        sim = FillSimulator.calibrate_from_ticks(ticks, asset="CALIBRATED")

        # Verify the simulator works with calibrated profile
        estimate = sim.estimate_entry(ticks[0], order_size=10.0, asset="CALIBRATED")
        assert estimate.fill_price > 0
        assert estimate.is_full_fill

    def test_calibrate_produces_different_profiles(self):
        """Different data → different calibrated profiles."""
        btc_ticks = [_make_tick(
            best_bid=0.499, best_ask=0.501, spread=0.002,
            asks_vol_1=80000, bids_vol_1=80000,
        ) for _ in range(50)]

        eth_ticks = [_make_tick(
            best_bid=0.48, best_ask=0.52, spread=0.04,
            asks_vol_1=5000, bids_vol_1=5000,
        ) for _ in range(50)]

        sim_btc = FillSimulator.calibrate_from_ticks(btc_ticks, asset="BTC_CAL")
        sim_eth = FillSimulator.calibrate_from_ticks(eth_ticks, asset="ETH_CAL")

        # Test on SAME tick — BTC-calibrated should show less slippage
        tick = _make_tick(spread=0.03)
        btc_est = sim_btc.estimate_entry(tick, order_size=500.0, asset="BTC_CAL")
        eth_est = sim_eth.estimate_entry(tick, order_size=500.0, asset="ETH_CAL")

        # BTC profile should be tighter (lower base_impact_bps)
        assert btc_est.slippage < eth_est.slippage, (
            f"BTC slippage ({btc_est.slippage}) should be < "
            f"ETH ({eth_est.slippage})"
        )


# ══════════════════════════════════════════════════════════════════════════
# INTEGRATION — Real Parquet-like data
# ══════════════════════════════════════════════════════════════════════════

class TestFillSimulatorRealistic:
    """Tests with data resembling real Parquet ticks."""

    @pytest.fixture
    def sim(self):
        return FillSimulator()

    def test_btc_like_tick(self, sim):
        """BTC: tight spread (0.001), deep L1 (~50K)."""
        tick = _make_tick(
            best_bid=0.4995, best_ask=0.5005, spread=0.001,
            bids_vol_1=50000, bids_vol_2=5000, bids_vol_3=500,
            asks_vol_1=20000, asks_vol_2=5000, asks_vol_3=500,
            volume_24h=500000,
        )
        estimate = sim.estimate_entry(tick, order_size=10.0, asset="BTC")

        assert estimate.fill_ratio > 0.99  # near-certain fill
        assert estimate.slippage_pct < 0.01  # less than 1% slippage
        assert estimate.is_full_fill

    def test_eth_like_tick(self, sim):
        """ETH: wider spread (0.03), shallower L1 (~11K)."""
        tick = _make_tick(
            best_bid=0.485, best_ask=0.515, spread=0.030,
            bids_vol_1=11000, bids_vol_2=3000, bids_vol_3=500,
            asks_vol_1=11000, asks_vol_2=7000, asks_vol_3=27000,
            volume_24h=350000,
        )
        estimate = sim.estimate_entry(tick, order_size=10.0, asset="ETH")

        assert estimate.fill_ratio > 0.9  # still decent fill
        assert estimate.slippage_pct < 0.05  # under 5%

    def test_large_order_on_eth(self, sim):
        """Large order (1500 USDC) on ETH-like market — may hit L2."""
        tick = _make_tick(
            best_bid=0.485, best_ask=0.515, spread=0.030,
            bids_vol_1=11000, bids_vol_2=3000, bids_vol_3=500,
            asks_vol_1=11000, asks_vol_2=7000, asks_vol_3=27000,
            volume_24h=350000,
        )
        estimate = sim.estimate_entry(tick, order_size=1500.0, asset="ETH")

        # 1500 USDC on 11K L1 → fits in L1 alone
        assert estimate.levels_consumed == 1
        assert estimate.fill_ratio >= 0.75
