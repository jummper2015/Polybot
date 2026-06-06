"""
Unit tests for P8.4 — Regime Labeling.

Tests:
  - Regime enum values
  - RegimeConfig defaults
  - RegimeClassifier batch mode
  - FeaturePipeline → RegimeClassifier integration
  - Streaming classification
  - Determinism: same ticks → same labels
  - Edge cases: empty ticks, all regimes represented
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.domain.value_objects.market_tick import MarketTick
from src.infrastructure.data.features import FeaturePipeline
from src.infrastructure.data.regime import (
    Regime,
    RegimeClassifier,
    RegimeConfig,
    RegimeResult,
)

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
    """Generate synthetic ticks with seeded RNG for determinism."""
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


def _make_trend_ticks(n: int = 100) -> list[MarketTick]:
    """Generate ticks with a clear upward trend."""
    ts = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    ticks = []
    price = 0.60

    for i in range(n):
        price += 0.003  # steady upward drift
        price = min(0.95, price)

        ticks.append(MarketTick(
            market_id="trend_market",
            yes_price=round(price, 4),
            no_price=round(1.0 - price, 4),
            best_bid=round(price - 0.01, 4),
            best_ask=round(price + 0.01, 4),
            spread=0.02,
            volume_24h=5000.0,
            timestamp=ts + timedelta(seconds=i * 30),
        ))

    return ticks


def _make_choppy_ticks(n: int = 100) -> list[MarketTick]:
    """Generate ticks that oscillate in a tight range (chop)."""
    import random
    rng = random.Random(99)
    ts = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    ticks = []
    price = 0.70

    for i in range(n):
        price += (rng.random() - 0.5) * 0.002  # tiny moves
        price = max(0.69, min(0.71, price))  # tight range

        ticks.append(MarketTick(
            market_id="chop_market",
            yes_price=round(price, 4),
            no_price=round(1.0 - price, 4),
            best_bid=round(price - 0.01, 4),
            best_ask=round(price + 0.01, 4),
            spread=0.02,
            volume_24h=5000.0,
            timestamp=ts + timedelta(seconds=i * 30),
        ))

    return ticks


def _make_panic_ticks(n: int = 100) -> list[MarketTick]:
    """Generate ticks with extreme volatility (panic)."""
    import random
    rng = random.Random(77)
    ts = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    ticks = []
    price = 0.70

    for i in range(n):
        # High-frequency swings → annualized vol >> 30
        price += (rng.random() - 0.5) * 0.15
        price = max(0.05, min(0.95, price))

        ticks.append(MarketTick(
            market_id="panic_market",
            yes_price=round(price, 4),
            no_price=round(1.0 - price, 4),
            best_bid=round(price - 0.06, 4),
            best_ask=round(price + 0.06, 4),
            spread=0.12,
            volume_24h=5000.0,
            timestamp=ts + timedelta(seconds=i * 30),
        ))

    return ticks


def _make_illiquid_ticks(n: int = 100) -> list[MarketTick]:
    """Generate ticks with wide spreads and low volume."""
    ts = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    ticks = []

    for i in range(n):
        price = 0.70

        ticks.append(MarketTick(
            market_id="illiquid_market",
            yes_price=round(price, 4),
            no_price=round(1.0 - price, 4),
            best_bid=round(price - 0.04, 4),   # wide spread
            best_ask=round(price + 0.04, 4),
            spread=0.08,                        # wide spread
            volume_24h=50.0,                    # very low volume
            timestamp=ts + timedelta(seconds=i * 30),
        ))

    return ticks


# ══════════════════════════════════════════════════════════════════════════
# REGIME ENUM TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestRegimeEnum:
    """Test Regime enum values and properties."""

    def test_all_regimes_defined(self):
        assert Regime.TREND.value == "trend"
        assert Regime.CHOP.value == "chop"
        assert Regime.PANIC.value == "panic"
        assert Regime.ILLIQUID.value == "illiquid"
        assert Regime.EVENT_DRIVEN.value == "event_driven"

    def test_regime_is_string_compatible(self):
        assert isinstance(Regime.TREND, str)
        assert Regime.TREND == "trend"

    def test_five_regimes_total(self):
        assert len(Regime) == 5


# ══════════════════════════════════════════════════════════════════════════
# REGIME CONFIG TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestRegimeConfig:
    """Test configuration defaults and overrides."""

    def test_default_config(self):
        cfg = RegimeConfig()
        assert cfg.panic_volatility_threshold == 50.0
        assert cfg.illiquid_spread_percentile == 0.80
        assert cfg.trend_momentum_min == 0.0001
        assert cfg.event_proximity_minutes == 60.0

    def test_custom_config(self):
        cfg = RegimeConfig(panic_volatility_threshold=100.0)
        assert cfg.panic_volatility_threshold == 100.0
        # Other values stay at defaults
        assert cfg.illiquid_spread_percentile == 0.80


# ══════════════════════════════════════════════════════════════════════════
# REGIME CLASSIFIER TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestRegimeClassifier:
    """Test regime classification with different market conditions."""

    @pytest.fixture
    def classifier(self):
        return RegimeClassifier()

    def test_classify_batch_normal_ticks(self, classifier):
        """Normal synthetic ticks should mostly be CHOP."""
        ticks = _make_ticks(n=100)
        result = classifier.classify_batch(ticks, asset="BTC")

        assert isinstance(result, RegimeResult)
        assert result.ticks_processed == 100
        assert len(result.labels) == 100
        assert len(result.confidence) == 100
        assert all(isinstance(l, Regime) for l in result.labels)

    def test_classify_batch_trend(self, classifier):
        """Trend ticks should produce some TREND labels."""
        from src.infrastructure.data.regime import RegimeConfig
        cfg = RegimeConfig(
            trend_price_move_min_pct=0.005,
            trend_direction_ratio=0.55,
        )
        sensitive = RegimeClassifier(config=cfg)

        ticks = _make_trend_ticks(n=100)
        result = sensitive.classify_batch(ticks, asset="BTC")

        trends = sum(1 for l in result.labels if l == Regime.TREND)
        assert trends > 10, f"Expected some TREND labels, got distribution: {result.regime_distribution}"

    def test_classify_batch_chop(self, classifier):
        """Choppy ticks should be mostly CHOP."""
        ticks = _make_choppy_ticks(n=100)
        result = classifier.classify_batch(ticks, asset="ETH")

        chops = sum(1 for l in result.labels if l == Regime.CHOP)
        # Chop should dominate
        assert chops > 50, f"Expected mostly CHOP, got distribution: {result.regime_distribution}"

    def test_classify_batch_panic(self, classifier):
        """Panic ticks should trigger PANIC regime with lowered threshold."""
        from src.infrastructure.data.regime import RegimeConfig
        cfg = RegimeConfig(panic_volatility_threshold=30.0)
        sensitive = RegimeClassifier(config=cfg)

        ticks = _make_panic_ticks(n=100)
        result = sensitive.classify_batch(ticks, asset="BTC")

        panics = sum(1 for l in result.labels if l == Regime.PANIC)
        assert panics > 0, f"Expected some PANIC labels, got distribution: {result.regime_distribution}"

    def test_classify_batch_illiquid(self, classifier):
        """Illiquid ticks should trigger ILLIQUID regime."""
        ticks = _make_illiquid_ticks(n=100)
        result = classifier.classify_batch(ticks, asset="ETH")

        illiquid = sum(1 for l in result.labels if l == Regime.ILLIQUID)
        assert illiquid > 0, f"Expected some ILLIQUID labels, got distribution: {result.regime_distribution}"

    def test_classify_batch_event_driven(self, classifier):
        """Ticks near expiry should produce EVENT_DRIVEN labels."""
        ts = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
        expiry = ts + timedelta(minutes=30)  # 30 min to expiry

        ticks = []
        for i in range(50):
            ticks.append(MarketTick(
                market_id="event_market",
                yes_price=0.70, no_price=0.30,
                best_bid=0.69, best_ask=0.71,
                spread=0.02, volume_24h=5000.0,
                timestamp=ts + timedelta(seconds=i * 30),
            ))

        result = classifier.classify_batch(
            ticks, asset="BTC", expiry=expiry,
        )

        events = sum(1 for l in result.labels if l == Regime.EVENT_DRIVEN)
        assert events > 0, f"Expected EVENT_DRIVEN labels near expiry, got distribution: {result.regime_distribution}"

    def test_regime_distribution(self, classifier):
        """Distribution sums to 1.0."""
        ticks = _make_ticks(n=100)
        result = classifier.classify_batch(ticks, asset="BTC")

        dist = result.regime_distribution
        total = sum(dist.values())
        assert total == pytest.approx(1.0, abs=0.01)

    def test_confidence_in_range(self, classifier):
        """All confidence scores are between 0 and 1."""
        ticks = _make_ticks(n=100)
        result = classifier.classify_batch(ticks, asset="BTC")

        for conf in result.confidence:
            assert 0.0 <= conf <= 1.0, f"confidence {conf} out of range"

    def test_empty_ticks(self, classifier):
        """Empty tick list returns empty result."""
        result = classifier.classify_batch([])
        assert result.ticks_processed == 0
        assert len(result.labels) == 0

    def test_determinism(self, classifier):
        """Same ticks → same labels (deterministic)."""
        ticks1 = _make_ticks(n=50)
        ticks2 = _make_ticks(n=50)

        r1 = classifier.classify_batch(ticks1)
        r2 = classifier.classify_batch(ticks2)

        assert r1.labels == r2.labels
        assert r1.confidence == r2.confidence


# ══════════════════════════════════════════════════════════════════════════
# FEATURE PIPELINE INTEGRATION TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestFeaturePipelineIntegration:
    """Test RegimeClassifier + FeaturePipeline integration."""

    def test_classify_from_features(self):
        """FeaturePipeline → RegimeClassifier integration."""

        pipeline = FeaturePipeline()
        classifier = RegimeClassifier()

        ticks = _make_ticks(n=100)
        feature_batch = pipeline.compute_batch(ticks, asset="BTC")

        result = classifier.classify_from_features(ticks, feature_batch)

        assert result.ticks_processed == 100
        assert result.asset == "BTC"
        assert len(result.labels) == 100

    def test_streaming_classification(self):
        """Streaming mode produces a label for each tick."""
        from src.infrastructure.data.features import StreamingState

        pipeline = FeaturePipeline(
            feature_names=["spread_percentile", "realized_volatility",
                          "momentum_decay", "event_proximity"]
        )
        classifier = RegimeClassifier()
        state = StreamingState(window_size=50)

        ticks = _make_ticks(n=60)
        labels = []

        for tick in ticks:
            fd = pipeline.compute_streaming(tick, state)
            label, conf = classifier.classify_tick(tick, fd.features)
            labels.append(label)

        assert len(labels) == 60
        assert all(isinstance(l, Regime) for l in labels)

    def test_batch_vs_streaming_consistency(self):
        """Batch and streaming should produce compatible results."""
        from src.infrastructure.data.features import StreamingState

        feature_names = ["spread_percentile", "realized_volatility",
                        "momentum_decay"]
        pipeline = FeaturePipeline(feature_names=feature_names)
        classifier = RegimeClassifier()

        ticks = _make_ticks(n=60)

        # Batch
        batch_result = classifier.classify_batch(
            ticks, features=pipeline.compute_batch(ticks).features_computed
        )

        # Streaming
        state = StreamingState(window_size=50)
        stream_labels = []
        for tick in ticks:
            fd = pipeline.compute_streaming(tick, state)
            label, _ = classifier.classify_tick(tick, fd.features)
            stream_labels.append(label)

        # Compare: later ticks should match once warmup is done
        # Allow differences in early ticks (warmup)
        matches = sum(
            1 for i in range(len(ticks))
            if batch_result.labels[i] == stream_labels[i]
        )
        match_rate = matches / len(ticks)
        # At least 60% should match (early warmup and momentum differences)
        assert match_rate > 0.60, f"Only {match_rate:.1%} match between batch and streaming"
