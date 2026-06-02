# src/infrastructure/data/regime.py

"""
Market regime classifier using heuristic rules (P8.4).

Classifies market states into 5 regimes based on features from the
Feature Store (P8.3). Uses configurable thresholds — no ML, fully
deterministic, reproducible.

Regimes:
    TREND        — Sustained directional movement with momentum
    CHOP         — Range-bound, no clear direction (default)
    PANIC        — Extreme volatility, rapid price moves
    ILLIQUID     — Wide spreads, low depth, low volume
    EVENT_DRIVEN — Proximity to expiry or known events

Priority: PANIC > ILLIQUID > EVENT_DRIVEN > TREND > CHOP

Architecture:
    MarketTick list → FeaturePipeline → Features → RegimeClassifier → Labels

Usage:
    classifier = RegimeClassifier()
    labels = classifier.classify_batch(ticks)         # batch mode
    label = classifier.classify_tick(tick, features)   # streaming mode
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

import structlog

from src.domain.value_objects.market_tick import MarketTick

logger = structlog.get_logger(__name__)


class Regime(str, Enum):
    """Market regime classification."""

    TREND = "trend"
    """Sustained directional movement with momentum."""

    CHOP = "chop"
    """Range-bound, no clear direction. Default regime."""

    PANIC = "panic"
    """Extreme volatility, rapid price changes, wide spreads."""

    ILLIQUID = "illiquid"
    """Wide spreads, low depth, insufficient liquidity."""

    EVENT_DRIVEN = "event_driven"
    """Near a known event (expiry, news) with elevated activity."""


@dataclass
class RegimeConfig:
    """Configurable thresholds for regime classification."""

    # ── PANIC thresholds ────────────────────────────────────────────────
    panic_volatility_threshold: float = 50.0
    """Annualized volatility above which PANIC is triggered."""

    panic_spread_percentile: float = 0.85
    """Spread percentile above which PANIC is considered."""

    # ── ILLIQUID thresholds ─────────────────────────────────────────────
    illiquid_spread_percentile: float = 0.80
    """Spread percentile above which market is illiquid."""

    illiquid_min_volume: float = 100.0
    """Minimum 24h volume (USDC) below which market is illiquid."""

    # ── TREND thresholds ────────────────────────────────────────────────
    trend_momentum_min: float = 0.0001
    """Minimum |momentum_decay| for trend detection."""

    trend_price_move_min_pct: float = 0.01
    """Minimum price movement (as fraction) over trend window."""

    trend_direction_ratio: float = 0.60
    """Fraction of recent ticks moving in same direction for trend."""

    trend_window_ticks: int = 20
    """Number of ticks to analyze for trend direction."""

    # ── EVENT_DRIVEN thresholds ─────────────────────────────────────────
    event_proximity_minutes: float = 60.0
    """Minutes before event to flag as EVENT_DRIVEN."""


@dataclass
class RegimeResult:
    """Result of regime classification for a batch of ticks."""

    asset: str
    market_id: str
    ticks_processed: int
    labels: list[Regime]
    confidence: list[float]

    @property
    def regime_distribution(self) -> dict[str, float]:
        """Fraction of ticks in each regime."""
        total = len(self.labels)
        if total == 0:
            return {}
        counts: dict[str, int] = {}
        for label in self.labels:
            counts[label.value] = counts.get(label.value, 0) + 1
        return {
            regime: round(count / total, 4)
            for regime, count in sorted(counts.items())
        }


class RegimeClassifier:
    """
    Classifies market ticks into regimes using heuristic rules.

    Uses features from FeaturePipeline (P8.3):
    - realized_volatility → PANIC detection
    - spread_percentile   → ILLIQUID detection
    - momentum_decay      → TREND/CHOP detection
    - event_proximity     → EVENT_DRIVEN detection

    Priority (first match wins):
        PANIC > ILLIQUID > EVENT_DRIVEN > TREND > CHOP
    """

    def __init__(self, config: RegimeConfig | None = None):
        self._config = config or RegimeConfig()

    # ── Batch Classification ────────────────────────────────────────────────

    def classify_batch(
        self,
        ticks: list[MarketTick],
        asset: str = "",
        market_id: str = "",
        features: dict[str, list[float | None]] | None = None,
        expiry: Optional[datetime] = None,
    ) -> RegimeResult:
        """
        Classify a batch of ticks into regimes.

        If features are not provided, computes them internally.
        """
        if features is None:
            features = self._compute_features(ticks, expiry)
        elif "_ticks" not in features:
            # Defensive: ensure raw ticks available for TREND detection
            features = {**features, "_ticks": ticks}

        labels: list[Regime] = []
        confidences: list[float] = []

        for i, tick in enumerate(ticks):
            label, conf = self._classify_tick(i, tick, features)
            labels.append(label)
            confidences.append(conf)

        result = RegimeResult(
            asset=asset,
            market_id=market_id,
            ticks_processed=len(ticks),
            labels=labels,
            confidence=confidences,
        )

        logger.info("regime_classification_complete",
                     asset=asset,
                     ticks=len(ticks),
                     distribution=result.regime_distribution)

        return result

    def classify_from_features(
        self,
        ticks: list[MarketTick],
        feature_batch,
    ) -> RegimeResult:
        """Classify ticks using pre-computed FeatureBatch from P8.3."""
        asset = feature_batch.asset
        market_id = feature_batch.market_id

        features: dict[str, list[float | None]] = {}
        for fname in feature_batch.feature_names:
            features[fname] = feature_batch.features_computed.get(fname, [])

        # Pass raw ticks for TREND direction detection
        features["_ticks"] = ticks

        return self.classify_batch(
            ticks=ticks, asset=asset, market_id=market_id, features=features,
        )

    # ── Streaming ───────────────────────────────────────────────────────────

    def classify_tick(
        self,
        tick: MarketTick,
        features: dict[str, float | None],
    ) -> tuple[Regime, float]:
        """
        Classify a single tick (streaming mode).

        Note: TREND detection in streaming mode uses only momentum_decay;
        raw price direction analysis is not available (requires tick window).
        For full regime fidelity, prefer batch mode.
        """
        return self._classify_tick(0, tick, self._single_to_batch(features))

    # ── Internal ────────────────────────────────────────────────────────────

    def _classify_tick(
        self,
        idx: int,
        tick: MarketTick,
        features: dict[str, list[float | None]],
    ) -> tuple[Regime, float]:
        """
        Classify a tick using features at index.

        Priority: PANIC > ILLIQUID > EVENT_DRIVEN > TREND > CHOP.
        """
        cfg = self._config

        vol = self._get_feature(features, "realized_volatility", idx)
        spread_pct = self._get_feature(features, "spread_percentile", idx)
        momentum = self._get_feature(features, "momentum_decay", idx)
        event_prox = self._get_feature(features, "event_proximity", idx)
        liq_depth = self._get_feature(features, "liquidity_depth", idx)

        # ── PANIC ──────────────────────────────────────────────────────
        if vol is not None and vol > cfg.panic_volatility_threshold:
            if spread_pct is not None and spread_pct > cfg.panic_spread_percentile:
                return Regime.PANIC, round(min(1.0, vol / (cfg.panic_volatility_threshold * 2)), 4)
            return Regime.PANIC, round(min(1.0, vol / (cfg.panic_volatility_threshold * 2)), 4)

        # ── ILLIQUID ───────────────────────────────────────────────────
        is_wide_spread = spread_pct is not None and spread_pct > cfg.illiquid_spread_percentile
        is_low_vol = tick.volume_24h < cfg.illiquid_min_volume
        is_skewed_depth = liq_depth is not None and (liq_depth < 0.3 or liq_depth > 3.0)

        if is_wide_spread and (is_low_vol or is_skewed_depth):
            return Regime.ILLIQUID, round(min(1.0, spread_pct if spread_pct else 0.8), 4)

        # ── EVENT_DRIVEN ───────────────────────────────────────────────
        if event_prox is not None and 0 < event_prox < cfg.event_proximity_minutes:
            conf = 1.0 - (event_prox / cfg.event_proximity_minutes)
            return Regime.EVENT_DRIVEN, round(conf, 4)

        # ── TREND ──────────────────────────────────────────────────────
        # Uses two signals: (a) raw price direction over window, (b) momentum_decay
        has_momentum = momentum is not None and abs(momentum) > cfg.trend_momentum_min
        is_directional = self._check_raw_direction(
            ticks=features.get("_ticks", []),  # not stored — use fallback
            idx=idx,
            window=cfg.trend_window_ticks,
            min_move_pct=cfg.trend_price_move_min_pct,
            min_ratio=cfg.trend_direction_ratio,
        )

        if has_momentum or is_directional:
            conf = 0.6
            if has_momentum and momentum:
                conf = min(1.0, abs(momentum) / (cfg.trend_momentum_min * 10))
            return Regime.TREND, round(conf, 4)

        # ── CHOP (default) ─────────────────────────────────────────────
        return Regime.CHOP, 0.5

    def _check_raw_direction(
        self,
        ticks: list,
        idx: int,
        window: int,
        min_move_pct: float,
        min_ratio: float,
    ) -> bool:
        """Check if recent ticks show sustained directional movement using raw prices."""
        if idx < 3:
            return False

        start = max(0, idx - window)
        if idx - start < 3:
            return False

        # Use MarketTick objects if passed, otherwise features
        prices = []
        for j in range(start, idx + 1):
            if j < len(ticks):
                if isinstance(ticks[j], MarketTick):
                    prices.append(ticks[j].yes_price)
                elif isinstance(ticks[j], (int, float)):
                    prices.append(float(ticks[j]))

        if len(prices) < 3:
            return False

        # Direction: count consecutive moves in same direction
        ups = sum(1 for j in range(1, len(prices)) if prices[j] > prices[j - 1])
        downs = len(prices) - 1 - ups

        max_dir = max(ups, downs)
        ratio = max_dir / (len(prices) - 1) if len(prices) > 1 else 0

        # Total price movement over window
        price_range = abs(prices[-1] - prices[0])
        start_price = prices[0] if prices[0] > 0 else 0.5
        move_pct = price_range / start_price

        return ratio >= min_ratio and move_pct >= min_move_pct

    def _compute_features(
        self,
        ticks: list[MarketTick],
        expiry: Optional[datetime] = None,
    ) -> dict[str, list[float | None]]:
        """Compute features internally via FeaturePipeline, with raw prices included."""
        from src.infrastructure.data.features import FeaturePipeline

        pipeline = FeaturePipeline()
        batch = pipeline.compute_batch(ticks, expiry=expiry)
        features = batch.features_computed

        # Also pass raw prices for trend detection
        features["_ticks"] = ticks

        return features

    @staticmethod
    def _get_feature(
        features: dict[str, list],
        name: str,
        idx: int,
    ) -> float | None:
        """Safely get a feature value at an index."""
        values = features.get(name, [])
        if idx < len(values):
            return values[idx]
        return None

    @staticmethod
    def _single_to_batch(
        features: dict[str, float | None],
    ) -> dict[str, list[float | None]]:
        """Convert streaming feature dict to batch-compatible format."""
        return {k: [v] for k, v in features.items()}
