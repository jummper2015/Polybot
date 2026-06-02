"""
Unit tests for P8.3 — Feature Store.

Tests:
  - FeatureRegistry: registration, discovery, metadata
  - 6 feature computations: correctness, edge cases, determinism
  - FeaturePipeline: batch mode, streaming mode, Parquet export
  - StreamingState: push, eviction, readiness
"""

import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.infrastructure.data.features import (
    FeatureRegistry,
    FeaturePipeline,
    FeatureBatch,
    FeatureDict,
    StreamingState,
    _registry,
    compute_spread_percentile,
    compute_realized_volatility,
    compute_momentum_decay,
    compute_event_proximity,
    compute_orderbook_imbalance,
    compute_liquidity_depth,
)
from src.domain.value_objects.market_tick import MarketTick


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _make_ticks(
    n: int = 100,
    start_price: float = 0.70,
    spread: float = 0.02,
    volatility: float = 0.005,
    seed: int = 42,
) -> list[MarketTick]:
    """Generate synthetic ticks with realistic price movement."""
    import random
    rng = random.Random(seed)
    ts = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    ticks = []
    price = start_price

    for i in range(n):
        price += (rng.random() - 0.5) * volatility
        price = max(0.05, min(0.95, price))

        ticks.append(MarketTick(
            market_id="test_market",
            yes_price=round(price, 4),
            no_price=round(1.0 - price, 4),
            best_bid=round(price - spread / 2, 4),
            best_ask=round(price + spread / 2, 4),
            spread=round(spread, 4),
            volume_24h=5000.0,
            timestamp=ts + timedelta(seconds=i * 30),
        ))

    return ticks


# ══════════════════════════════════════════════════════════════════════════
# FEATURE REGISTRY TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestFeatureRegistry:
    """Test feature registration, discovery, and metadata."""

    def test_global_registry_has_all_features(self):
        """All 6 features are registered in the global registry."""
        names = _registry.list_names()
        expected = {
            "event_proximity", "liquidity_depth",
            "momentum_decay", "orderbook_imbalance",
            "realized_volatility", "spread_percentile",
        }
        assert set(names) == expected

    def test_registry_get_by_name(self):
        """Get feature metadata by name."""
        meta = _registry.get("spread_percentile")
        assert meta is not None
        assert meta["name"] == "spread_percentile"
        assert meta["category"] == "liquidity"
        assert meta["window_size"] == 50
        assert callable(meta["func"])

    def test_registry_list_by_category(self):
        """Features grouped by category."""
        groups = _registry.list_by_category()
        assert "liquidity" in groups
        assert "volatility" in groups
        assert "momentum" in groups
        assert "event" in groups
        assert len(groups["liquidity"]) == 3  # spread_percentile, orderbook_imbalance, liquidity_depth

    def test_registry_get_nonexistent(self):
        """Get returns None for unregistered features."""
        assert _registry.get("nonexistent") is None

    def test_registry_get_window_size(self):
        """Default window sizes are correct."""
        assert _registry.get_window_size("spread_percentile") == 50
        assert _registry.get_window_size("realized_volatility") == 20
        assert _registry.get_window_size("momentum_decay") == 30

    def test_custom_registry_register_decorator(self):
        """New features can be registered via decorator."""
        reg = FeatureRegistry()

        @reg.register(name="test_feature", category="test", window_size=10)
        def my_feature(ticks):
            return [1.0] * len(ticks)

        assert "test_feature" in reg.list_names()
        meta = reg.get("test_feature")
        assert meta["category"] == "test"
        assert meta["window_size"] == 10


# ══════════════════════════════════════════════════════════════════════════
# FEATURE COMPUTATION TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestSpreadPercentile:
    """Test spread_percentile feature."""

    def test_returns_correct_length(self):
        ticks = _make_ticks(n=50)
        result = compute_spread_percentile(ticks)
        assert len(result) == 50

    def test_warmup_period_is_none(self):
        ticks = _make_ticks(n=5)
        result = compute_spread_percentile(ticks)
        assert result[0] is None
        assert result[1] is None

    def test_values_in_range(self):
        ticks = _make_ticks(n=100)
        result = compute_spread_percentile(ticks)
        valid = [v for v in result if v is not None]
        assert len(valid) > 0
        for v in valid:
            assert 0.0 <= v <= 1.0, f"percentile {v} out of range"

    def test_deterministic(self):
        ticks1 = _make_ticks(n=50)
        ticks2 = _make_ticks(n=50)
        r1 = compute_spread_percentile(ticks1)
        r2 = compute_spread_percentile(ticks2)
        assert r1 == r2


class TestRealizedVolatility:
    """Test realized_volatility feature."""

    def test_returns_correct_length(self):
        ticks = _make_ticks(n=30)
        result = compute_realized_volatility(ticks)
        assert len(result) == 30

    def test_warmup_period_is_none(self):
        ticks = _make_ticks(n=3)
        result = compute_realized_volatility(ticks)
        assert result[0] is None
        assert result[1] is None
        assert result[2] is None

    def test_values_are_positive(self):
        ticks = _make_ticks(n=100, volatility=0.01)
        result = compute_realized_volatility(ticks)
        valid = [v for v in result if v is not None]
        assert len(valid) > 0
        for v in valid:
            assert v >= 0, f"volatility {v} should be >= 0"

    def test_constant_price_gives_zero_vol(self):
        """Constant prices produce zero volatility."""
        ts = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
        ticks = []
        for i in range(30):
            ticks.append(MarketTick(
                market_id="test", yes_price=0.70, no_price=0.30,
                best_bid=0.69, best_ask=0.71, spread=0.02,
                volume_24h=5000.0,
                timestamp=ts + timedelta(seconds=i * 30),
            ))

        result = compute_realized_volatility(ticks)
        valid = [v for v in result if v is not None]
        assert len(valid) > 0
        for v in valid:
            assert v == pytest.approx(0.0, abs=1e-4)


class TestMomentumDecay:
    """Test momentum_decay feature."""

    def test_returns_correct_length(self):
        ticks = _make_ticks(n=50)
        result = compute_momentum_decay(ticks)
        assert len(result) == 50

    def test_warmup_is_none(self):
        ticks = _make_ticks(n=20)
        result = compute_momentum_decay(ticks)
        for v in result:
            assert v is None

    def test_values_after_warmup(self):
        ticks = _make_ticks(n=60, volatility=0.01)
        result = compute_momentum_decay(ticks)
        valid = [v for v in result if v is not None]
        assert len(valid) > 0


class TestEventProximity:
    """Test event_proximity feature."""

    def test_no_expiry_returns_none(self):
        ticks = _make_ticks(n=10)
        result = compute_event_proximity(ticks, expiry=None)
        assert all(v is None for v in result)

    def test_with_expiry(self):
        ts = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
        expiry = ts + timedelta(hours=2)

        ticks = []
        for i in range(5):
            ticks.append(MarketTick(
                market_id="test", yes_price=0.70, no_price=0.30,
                best_bid=0.69, best_ask=0.71, spread=0.02,
                volume_24h=5000.0,
                timestamp=ts + timedelta(minutes=i * 15),
            ))

        result = compute_event_proximity(ticks, expiry=expiry)
        assert len(result) == 5
        # First tick: 120 min to expiry, last tick: 60 min to expiry
        assert result[0] == pytest.approx(120.0, abs=1)
        assert result[-1] == pytest.approx(60.0, abs=1)

    def test_expired_market(self):
        ts = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
        expiry = ts - timedelta(hours=1)  # Expired 1h ago

        ticks = [MarketTick(
            market_id="test", yes_price=0.70, no_price=0.30,
            best_bid=0.69, best_ask=0.71, spread=0.02,
            volume_24h=5000.0, timestamp=ts,
        )]

        result = compute_event_proximity(ticks, expiry=expiry)
        assert result[0] < 0  # Negative = expired


class TestOrderbookFeatures:
    """Test orderbook_imbalance and liquidity_depth features."""

    def test_orderbook_imbalance_no_depth_data(self):
        """Returns None when tick has no depth data."""
        ticks = _make_ticks(n=20)
        result = compute_orderbook_imbalance(ticks)
        assert all(v is None for v in result)

    def test_liquidity_depth_no_depth_data(self):
        """Returns None when tick has no depth data."""
        ticks = _make_ticks(n=20)
        result = compute_liquidity_depth(ticks)
        assert all(v is None for v in result)

    def test_orderbook_imbalance_with_depth_data(self):
        """Computes imbalance correctly from depth data."""
        ticks = _make_ticks(n=10)
        # Bids stronger than asks → positive imbalance
        depth_data = [
            {"bids_vol_1": 200.0, "bids_vol_2": 100.0, "bids_vol_3": 50.0,
             "asks_vol_1": 100.0, "asks_vol_2": 50.0, "asks_vol_3": 25.0}
        ] * 10

        result = compute_orderbook_imbalance(ticks, depth_data=depth_data)
        assert len(result) == 10
        for v in result:
            assert v is not None
            # (350 - 175) / 525 = 0.3333
            assert v == pytest.approx(0.3333, abs=0.01)

    def test_liquidity_depth_with_depth_data(self):
        """Computes depth ratio correctly from depth data."""
        ticks = _make_ticks(n=10)
        # Bids deeper than asks → ratio > 1
        depth_data = [
            {"bids_vol_1": 300.0, "bids_vol_2": 200.0, "bids_vol_3": 100.0,
             "asks_vol_1": 100.0, "asks_vol_2": 50.0, "asks_vol_3": 25.0}
        ] * 10

        result = compute_liquidity_depth(ticks, depth_data=depth_data)
        assert len(result) == 10
        for v in result:
            assert v is not None
            # 600 / 175 = 3.4286
            assert v == pytest.approx(3.4286, abs=0.01)

    def test_orderbook_imbalance_equal_bid_ask(self):
        """Equal bids and asks → imbalance = 0."""
        ticks = _make_ticks(n=5)
        depth_data = [
            {"bids_vol_1": 100.0, "bids_vol_2": 50.0, "bids_vol_3": 25.0,
             "asks_vol_1": 100.0, "asks_vol_2": 50.0, "asks_vol_3": 25.0}
        ] * 5

        result = compute_orderbook_imbalance(ticks, depth_data=depth_data)
        for v in result:
            assert v == pytest.approx(0.0, abs=0.01)


# ══════════════════════════════════════════════════════════════════════════
# FEATURE PIPELINE TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestFeaturePipeline:
    """Test batch and streaming pipeline modes."""

    def test_compute_batch_all_features(self):
        """Batch mode computes all registered features."""
        pipeline = FeaturePipeline()
        ticks = _make_ticks(n=100)

        batch = pipeline.compute_batch(ticks, asset="BTC", market_id="0xtest")

        assert isinstance(batch, FeatureBatch)
        assert batch.ticks_processed == 100
        assert batch.asset == "BTC"
        assert len(batch.features_computed) == 6
        # Each feature should have exactly 100 values
        for fname, values in batch.features_computed.items():
            assert len(values) == 100, f"{fname} has {len(values)}"

    def test_compute_batch_subset_of_features(self):
        """Only compute specified features."""
        pipeline = FeaturePipeline(feature_names=["spread_percentile", "realized_volatility"])
        ticks = _make_ticks(n=50)

        batch = pipeline.compute_batch(ticks)
        assert len(batch.features_computed) == 2
        assert "spread_percentile" in batch.features_computed
        assert "realized_volatility" in batch.features_computed
        assert "momentum_decay" not in batch.features_computed

    def test_compute_batch_empty_ticks(self):
        """Handle empty tick list gracefully."""
        pipeline = FeaturePipeline()
        batch = pipeline.compute_batch([])
        assert batch.ticks_processed == 0

    def test_compute_batch_with_expiry(self):
        """Batch mode passes expiry to event_proximity."""
        pipeline = FeaturePipeline(feature_names=["event_proximity"])
        ticks = _make_ticks(n=10)
        expiry = ticks[0].timestamp + timedelta(hours=1)

        batch = pipeline.compute_batch(ticks, expiry=expiry)
        values = batch.features_computed["event_proximity"]
        assert values[0] is not None
        assert values[0] > 0

    def test_streaming_state_push(self):
        """StreamingState accumulates and evicts ticks."""
        state = StreamingState(window_size=5)
        ticks = _make_ticks(n=10)

        for t in ticks:
            state.push(t)

        # Should only have last 5 ticks
        assert len(state.prices) == 5
        assert len(state.spreads) == 5

    def test_streaming_state_readiness(self):
        """State reports ready after enough ticks."""
        state = StreamingState(window_size=50)
        ticks = _make_ticks(n=3)
        for t in ticks:
            state.push(t)
        assert state.is_ready  # 3 >= 2

        empty_state = StreamingState(window_size=50)
        assert not empty_state.is_ready  # 0 < 2

    def test_streaming_compute(self):
        """Streaming mode produces features per tick."""
        pipeline = FeaturePipeline(
            feature_names=["spread_percentile", "realized_volatility"]
        )
        state = pipeline.create_streaming_state(window_size=50)
        ticks = _make_ticks(n=60)

        results = []
        for tick in ticks:
            fd = pipeline.compute_streaming(tick, state)
            results.append(fd)

        assert len(results) == 60
        for fd in results:
            assert isinstance(fd, FeatureDict)
            assert "spread_percentile" in fd.features
            assert "realized_volatility" in fd.features

        # Early ticks should have None features (warmup)
        assert results[0].features["spread_percentile"] is None

    def test_to_parquet_export(self):
        """FeatureBatch exports to Parquet correctly."""
        pipeline = FeaturePipeline(
            feature_names=["spread_percentile", "realized_volatility"]
        )
        ticks = _make_ticks(n=100)
        batch = pipeline.compute_batch(ticks, asset="BTC", market_id="0xtest")

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            path = f.name

        try:
            pipeline.to_parquet(batch, path)

            import pyarrow.parquet as pq
            table = pq.read_table(path)
            assert table.num_rows == 100
            assert "spread_percentile" in table.column_names
            assert "realized_volatility" in table.column_names
        finally:
            Path(path).unlink(missing_ok=True)

    def test_feature_batch_as_dicts(self):
        """FeatureBatch.as_dicts() produces correct output."""
        pipeline = FeaturePipeline(feature_names=["spread_percentile"])
        ticks = _make_ticks(n=10)
        batch = pipeline.compute_batch(ticks, asset="BTC")

        dicts = batch.as_dicts()
        assert len(dicts) == 10
        assert "spread_percentile" in dicts[0]

    def test_determinism(self):
        """Same ticks → same features (deterministic)."""
        pipeline = FeaturePipeline(feature_names=["spread_percentile", "realized_volatility"])

        ticks1 = _make_ticks(n=50)
        ticks2 = _make_ticks(n=50)

        b1 = pipeline.compute_batch(ticks1)
        b2 = pipeline.compute_batch(ticks2)

        for fname in b1.feature_names:
            assert b1.features_computed[fname] == b2.features_computed[fname]
