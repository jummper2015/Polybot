# src/infrastructure/data/features.py

"""
Centralized feature pipeline for quantitative research (P8.3).

Provides a registry of reusable, deterministic features that can be
computed over tick data in both batch (backtesting) and streaming
(live trading) modes.

Architecture:
    FeatureRegistry
    ├── spread_percentile     (rolling window)
    ├── orderbook_imbalance   (per-tick, from depth)
    ├── realized_volatility   (rolling std of returns)
    ├── liquidity_depth       (per-tick, volume ratio)
    ├── momentum_decay        (exponential decay over window)
    └── event_proximity       (static, from market metadata)

    FeaturePipeline
    ├── compute_batch(ticks)       → FeatureBatch (for backtesting)
    ├── compute_streaming(tick)    → FeatureDict (for live trading)
    └── to_parquet(features, path) → Extended Parquet

Design principles:
    - Pure functions: same input → same output (deterministic)
    - No side effects: features don't modify state
    - Batch/streaming compatible: same feature function, different caller
    - Extensible: register new features via decorator

Usage:
    # Batch mode (backtesting)
    pipeline = FeaturePipeline()
    batch = pipeline.compute_batch(ticks)

    # Streaming mode (live)
    state = pipeline.create_streaming_state(window_size=50)
    for tick in live_ticks:
        features = pipeline.compute_streaming(tick, state)

    # Parquet export
    pipeline.to_parquet(batch, "data/features/BTC_features.parquet")
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import structlog

from src.domain.value_objects.market_tick import MarketTick

logger = structlog.get_logger(__name__)

# ── Feature batch types ─────────────────────────────────────────────────────


@dataclass
class FeatureDict:
    """A single tick's computed features (streaming mode output)."""

    timestamp: datetime
    market_id: str
    features: dict[str, float | None] = field(default_factory=dict)


@dataclass
class FeatureBatch:
    """Computed features for a batch of ticks (backtesting mode output)."""

    asset: str
    market_id: str
    ticks_processed: int
    features_computed: dict[str, list[float | None]]
    """Dict mapping feature_name → list of values (one per tick)."""

    @property
    def feature_names(self) -> list[str]:
        return sorted(self.features_computed.keys())

    @property
    def tick_count(self) -> int:
        return self.ticks_processed

    def as_dicts(self) -> list[dict]:
        """Convert to list of per-tick dicts (for CSV/Parquet export)."""
        result = []
        for i in range(self.ticks_processed):
            row = {}
            for fname, values in self.features_computed.items():
                row[fname] = values[i]
            result.append(row)
        return result


@dataclass
class StreamingState:
    """Mutable state for streaming feature computation."""

    window_size: int
    """Number of ticks to keep in the rolling window."""

    prices: list[float] = field(default_factory=list)
    spreads: list[float] = field(default_factory=list)
    bid_vols: list[list[float]] = field(default_factory=list)
    ask_vols: list[list[float]] = field(default_factory=list)
    volumes: list[float] = field(default_factory=list)

    def push(self, tick: MarketTick, depth: dict | None = None) -> None:
        """Add a tick to the rolling window, evicting oldest if full.

        Args:
            tick: MarketTick to add.
            depth: Optional dict with orderbook depth (bids_vol_1..3, asks_vol_1..3).
        """
        self.prices.append(tick.yes_price)
        self.spreads.append(tick.spread)
        self.volumes.append(tick.volume_24h)
        # Orderbook depth from Parquet data if available
        if depth:
            self.bid_vols.append([
                depth.get(f"bids_vol_{j}", 0) or 0 for j in range(1, 4)
            ])
            self.ask_vols.append([
                depth.get(f"asks_vol_{j}", 0) or 0 for j in range(1, 4)
            ])
        else:
            self.bid_vols.append([0.0, 0.0, 0.0])
            self.ask_vols.append([0.0, 0.0, 0.0])

        # Trim to window_size
        if len(self.prices) > self.window_size:
            self.prices.pop(0)
            self.spreads.pop(0)
            self.volumes.pop(0)
            self.bid_vols.pop(0)
            self.ask_vols.pop(0)

    @property
    def is_ready(self) -> bool:
        """True when enough ticks have been accumulated for feature computation."""
        return len(self.prices) >= 2


# ── Feature Registry ─────────────────────────────────────────────────────────


class FeatureRegistry:
    """
    Registry of named feature functions.

    Each feature is a pure function that takes a list of MarketTick objects
    and returns a list of computed values (one per tick, None for warmup).

    Features are registered via the @register decorator and can be
    discovered by name or category.
    """

    def __init__(self):
        self._features: dict[str, dict] = {}

    def register(
        self,
        name: str,
        category: str = "general",
        window_size: int = 50,
        description: str = "",
    ):
        """
        Decorator to register a feature function.

        Args:
            name: Unique feature name (e.g. "spread_percentile").
            category: Group for organization (e.g. "liquidity", "momentum").
            window_size: Default rolling window size in ticks.
            description: Human-readable description.
        """
        def decorator(func: Callable):
            self._features[name] = {
                "func": func,
                "name": name,
                "category": category,
                "window_size": window_size,
                "description": description,
            }
            return func
        return decorator

    def get(self, name: str) -> dict | None:
        """Get a feature by name."""
        return self._features.get(name)

    def list_names(self) -> list[str]:
        """List all registered feature names."""
        return sorted(self._features.keys())

    def list_by_category(self) -> dict[str, list[str]]:
        """Group feature names by category."""
        groups: dict[str, list[str]] = {}
        for name, meta in self._features.items():
            cat = meta["category"]
            groups.setdefault(cat, []).append(name)
        return groups

    def get_window_size(self, name: str) -> int:
        """Get the default window size for a feature."""
        meta = self.get(name)
        return meta["window_size"] if meta else 50


# ── Global registry instance ─────────────────────────────────────────────────

_registry = FeatureRegistry()


# ══════════════════════════════════════════════════════════════════════════
# FEATURE DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════


@_registry.register(
    name="spread_percentile",
    category="liquidity",
    window_size=50,
    description="Percentile rank of current spread within rolling window. "
                "High values (>80) indicate unusual spread widening.",
)
def compute_spread_percentile(ticks: list[MarketTick]) -> list[float | None]:
    """
    Spread percentile within a rolling window.

    For each tick, computes what percentile the current spread is
    within the last `window_size` spreads. Returns None until
    enough ticks are accumulated.
    """
    window_size = 50
    results: list[float | None] = []

    for i in range(len(ticks)):
        if i < 2:
            results.append(None)
            continue

        start = max(0, i - window_size)
        window_spreads = [t.spread for t in ticks[start:i + 1]]
        current = ticks[i].spread

        if len(window_spreads) < 2:
            results.append(None)
            continue

        # Count how many spreads are <= current spread
        count_le = sum(1 for s in window_spreads if s <= current)
        percentile = count_le / len(window_spreads)
        results.append(round(percentile, 4))

    return results


@_registry.register(
    name="orderbook_imbalance",
    category="liquidity",
    window_size=1,
    description="Order book imbalance: (bid_vol - ask_vol) / (bid_vol + ask_vol). "
                "Positive = buying pressure, negative = selling pressure. "
                "Requires depth_data dict with bids_vol_1..3, asks_vol_1..3 per tick.",
)
def compute_orderbook_imbalance(
    ticks: list[MarketTick],
    depth_data: list[dict] | None = None,
) -> list[float | None]:
    """
    Order book imbalance from depth data.

    Requires depth_data from Parquet (bids_vol_1..3, asks_vol_1..3).
    Returns None for ticks without depth data.
    """
    if depth_data is None:
        return [None] * len(ticks)

    results: list[float | None] = []
    for i in range(len(ticks)):
        if i >= len(depth_data):
            results.append(None)
            continue
        d = depth_data[i]
        bid_vol = sum(d.get(f"bids_vol_{j}", 0) or 0 for j in range(1, 4))
        ask_vol = sum(d.get(f"asks_vol_{j}", 0) or 0 for j in range(1, 4))
        denom = bid_vol + ask_vol
        if denom > 0:
            results.append(round((bid_vol - ask_vol) / denom, 4))
        else:
            results.append(None)
    return results


@_registry.register(
    name="realized_volatility",
    category="volatility",
    window_size=20,
    description="Rolling standard deviation of mid-price returns. "
                "Annualized assuming 30-second tick intervals.",
)
def compute_realized_volatility(ticks: list[MarketTick]) -> list[float | None]:
    """
    Realized volatility from rolling std of returns.

    Uses mid_price = (best_bid + best_ask) / 2 for return calculation.
    Returns None until at least 3 ticks are available.
    """
    window_size = 20
    results: list[float | None] = []

    for i in range(len(ticks)):
        if i < 3:
            results.append(None)
            continue

        start = max(0, i - window_size)
        window = ticks[start:i + 1]

        # Compute log returns on mid_price
        returns = []
        for j in range(1, len(window)):
            prev_mid = (window[j - 1].best_bid + window[j - 1].best_ask) / 2
            curr_mid = (window[j].best_bid + window[j].best_ask) / 2
            if prev_mid > 0 and curr_mid > 0:
                log_ret = math.log(curr_mid / prev_mid)
                returns.append(log_ret)

        if len(returns) < 2:
            results.append(None)
            continue

        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        std = math.sqrt(variance)

        # Annualize: assume 30s ticks → 1051200 ticks/year
        annual_factor = math.sqrt(1051200)
        annualized = std * annual_factor

        results.append(round(annualized, 6))

    return results


@_registry.register(
    name="liquidity_depth",
    category="liquidity",
    window_size=1,
    description="Ratio of bid-side depth to ask-side depth at top-3 levels. "
                ">1.0 = deeper bids, <1.0 = deeper asks. "
                "Requires depth_data dict with bids_vol_1..3, asks_vol_1..3 per tick.",
)
def compute_liquidity_depth(
    ticks: list[MarketTick],
    depth_data: list[dict] | None = None,
) -> list[float | None]:
    """
    Liquidity depth ratio from orderbook depth.

    Requires depth_data from Parquet (bids_vol_1..3, asks_vol_1..3).
    Returns None for ticks without depth data.
    """
    if depth_data is None:
        return [None] * len(ticks)

    results: list[float | None] = []
    for i in range(len(ticks)):
        if i >= len(depth_data):
            results.append(None)
            continue
        d = depth_data[i]
        bid_vol = sum(d.get(f"bids_vol_{j}", 0) or 0 for j in range(1, 4))
        ask_vol = sum(d.get(f"asks_vol_{j}", 0) or 0 for j in range(1, 4))
        if ask_vol > 0:
            results.append(round(bid_vol / ask_vol, 4))
        else:
            results.append(None)
    return results


@_registry.register(
    name="momentum_decay",
    category="momentum",
    window_size=30,
    description="Exponential decay rate of price momentum. "
                "Measures whether recent price acceleration is fading. "
                "Positive = momentum accelerating, Negative = decaying.",
)
def compute_momentum_decay(ticks: list[MarketTick]) -> list[float | None]:
    """
    Momentum decay using exponential weighting.

    Computes the difference between recent (short-term) and older
    (long-term) exponentially weighted price changes.

    Short half-life: 5 ticks. Long half-life: 30 ticks.
    Positive decay = recent momentum stronger than older momentum.
    """
    short_hl = 5   # short half-life in ticks
    long_hl = 30   # long half-life in ticks
    results: list[float | None] = []

    for i in range(len(ticks)):
        if i < long_hl:
            results.append(None)
            continue

        # Compute EWMA of price changes
        short_ewma = _ewma_diff(ticks, i, short_hl)
        long_ewma = _ewma_diff(ticks, i, long_hl)

        if short_ewma is None or long_ewma is None:
            results.append(None)
            continue

        decay = short_ewma - long_ewma
        results.append(round(decay, 6))

    return results


@_registry.register(
    name="event_proximity",
    category="event",
    window_size=1,
    description="Minutes until a known event (market expiry, news, etc.). "
                "0 = event has passed. Negative = event in the past. "
                "Requires market metadata with expiry timestamp.",
)
def compute_event_proximity(
    ticks: list[MarketTick],
    expiry: Optional[datetime] = None,
) -> list[float | None]:
    """
    Minutes until market expiry.

    If no expiry is provided, returns None for all ticks.
    """
    if expiry is None:
        return [None] * len(ticks)

    results: list[float | None] = []
    for tick in ticks:
        delta = expiry - tick.timestamp
        minutes = delta.total_seconds() / 60
        results.append(round(minutes, 1))

    return results


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _ewma_diff(
    ticks: list[MarketTick],
    end_idx: int,
    half_life: int,
) -> float | None:
    """
    Compute exponentially weighted moving average of price differences.

    Uses half_life to determine decay factor:
        alpha = 1 - exp(-ln(2) / half_life)
    """
    if end_idx < 2:
        return None

    alpha = 1 - math.exp(-math.log(2) / half_life)
    ewma = 0.0
    weight_sum = 0.0

    for j in range(max(1, end_idx - half_life * 3), end_idx + 1):
        diff = ticks[j].yes_price - ticks[j - 1].yes_price
        dist = end_idx - j
        weight = (1 - alpha) ** dist
        ewma += diff * weight
        weight_sum += weight

    if weight_sum == 0:
        return None

    return ewma / weight_sum


# ══════════════════════════════════════════════════════════════════════════
# FEATURE PIPELINE
# ══════════════════════════════════════════════════════════════════════════


class FeaturePipeline:
    """
    Orchestrates feature computation over tick data.

    Supports two modes:
    - Batch: compute all features over a list of ticks at once
    - Streaming: compute features incrementally as ticks arrive

    Usage:
        pipeline = FeaturePipeline()

        # Batch
        batch = pipeline.compute_batch(ticks)

        # Streaming
        state = pipeline.create_streaming_state(window_size=50)
        for tick in live_ticks:
            features = pipeline.compute_streaming(tick, state)

        # Parquet
        pipeline.to_parquet(batch, "features.parquet")
    """

    def __init__(
        self,
        registry: FeatureRegistry | None = None,
        feature_names: list[str] | None = None,
    ):
        """
        Args:
            registry: Feature registry (default: global _registry).
            feature_names: Features to compute (default: all registered).
        """
        self._registry = registry or _registry
        self._feature_names = feature_names or self._registry.list_names()

    # ── Batch Mode ──────────────────────────────────────────────────────────

    def compute_batch(
        self,
        ticks: list[MarketTick],
        asset: str = "",
        market_id: str = "",
        expiry: Optional[datetime] = None,
        depth_data: list[dict] | None = None,
    ) -> FeatureBatch:
        """
        Compute all features over a batch of ticks.

        Args:
            ticks: List of MarketTick objects in chronological order.
            asset: Asset label (for metadata).
            market_id: Market ID (for metadata).
            expiry: Market expiry timestamp (for event_proximity).
            depth_data: Optional list of dicts with orderbook depth
                       (bids_vol_1..3, asks_vol_1..3) per tick.

        Returns:
            FeatureBatch with all computed feature values.
        """
        features_computed: dict[str, list[float | None]] = {}

        for fname in self._feature_names:
            meta = self._registry.get(fname)
            if meta is None:
                logger.warning("feature_not_found", name=fname)
                continue

            try:
                # Build kwargs based on what the feature accepts
                kwargs: dict = {}
                if fname == "event_proximity":
                    kwargs["expiry"] = expiry
                if fname in ("orderbook_imbalance", "liquidity_depth") and depth_data is not None:
                    kwargs["depth_data"] = depth_data

                if kwargs:
                    values = meta["func"](ticks, **kwargs)
                else:
                    values = meta["func"](ticks)

                # Pad to match tick count
                if len(values) < len(ticks):
                    values = [None] * (len(ticks) - len(values)) + values

                features_computed[fname] = values

            except Exception as e:
                logger.error("feature_computation_failed",
                             feature=fname, error=str(e))
                features_computed[fname] = [None] * len(ticks)

        logger.info("batch_features_computed",
                     asset=asset,
                     market_id=market_id[:20] if market_id else "",
                     ticks=len(ticks),
                     features=len(features_computed))

        return FeatureBatch(
            asset=asset,
            market_id=market_id,
            ticks_processed=len(ticks),
            features_computed=features_computed,
        )

    # ── Streaming Mode ──────────────────────────────────────────────────────

    def create_streaming_state(self, window_size: int = 50) -> StreamingState:
        """Create a new streaming state for incremental computation."""
        return StreamingState(window_size=window_size)

    def compute_streaming(
        self,
        tick: MarketTick,
        state: StreamingState,
        depth: dict | None = None,
    ) -> FeatureDict:
        """
        Compute features for a single tick using streaming state.

        Appends the tick to the rolling window and computes features
        based on the accumulated state.

        Args:
            tick: The new MarketTick.
            state: Mutable StreamingState (updated in-place).
            depth: Optional dict with orderbook depth (bids_vol_1..3, asks_vol_1..3).

        Returns:
            FeatureDict with computed feature values.
        """
        state.push(tick, depth=depth)
        features: dict[str, float | None] = {}

        for fname in self._feature_names:
            meta = self._registry.get(fname)
            if meta is None:
                continue

            try:
                # For streaming, use a simple per-tick computation
                # or None if not enough data
                value = self._streaming_feature(fname, state, tick)
                features[fname] = value
            except Exception:
                features[fname] = None

        return FeatureDict(
            timestamp=tick.timestamp,
            market_id=tick.market_id,
            features=features,
        )

    def _streaming_feature(
        self,
        fname: str,
        state: StreamingState,
        tick: MarketTick,
    ) -> float | None:
        """Compute a single feature from streaming state."""
        if not state.is_ready:
            return None

        if fname == "spread_percentile":
            if len(state.spreads) < 2:
                return None
            current = state.spreads[-1]
            count_le = sum(1 for s in state.spreads if s <= current)
            return round(count_le / len(state.spreads), 4)

        elif fname == "realized_volatility":
            if len(state.prices) < 3:
                return None
            returns = []
            for j in range(1, len(state.prices)):
                if state.prices[j - 1] > 0 and state.prices[j] > 0:
                    returns.append(math.log(
                        state.prices[j] / state.prices[j - 1]
                    ))
            if len(returns) < 2:
                return None
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / len(returns)
            std = math.sqrt(variance)
            return round(std * math.sqrt(1051200), 6)

        elif fname == "momentum_decay":
            if len(state.prices) < 30:
                return None
            # NOTE: This is O(n) per tick. For long-running pipelines
            # with 100K+ ticks, consider caching or incremental EWMA.
            # Create synthetic ticks from prices and delegate to batch function.
            ts = datetime.now()
            synthetic_ticks = [
                MarketTick(
                    market_id="stream", yes_price=p, no_price=1-p,
                    best_bid=p-0.01, best_ask=p+0.01,
                    spread=0.02, volume_24h=5000.0, timestamp=ts,
                )
                for p in state.prices
            ]
            values = compute_momentum_decay(synthetic_ticks)
            return values[-1] if values else None

        elif fname == "liquidity_depth":
            # Find the most recent valid bid/ask volumes
            for bv, av in zip(
                reversed(state.bid_vols), reversed(state.ask_vols)
            ):
                total_bid = sum(bv)
                total_ask = sum(av)
                if total_bid > 0 and total_ask > 0:
                    return round(total_bid / total_ask, 4)
            return None

        elif fname == "orderbook_imbalance":
            for bv, av in zip(
                reversed(state.bid_vols), reversed(state.ask_vols)
            ):
                total_bid = sum(bv)
                total_ask = sum(av)
                denominator = total_bid + total_ask
                if denominator > 0:
                    return round(
                        (total_bid - total_ask) / denominator, 4
                    )
            return None

        return None

    # ── Parquet Export ──────────────────────────────────────────────────────

    def to_parquet(
        self,
        batch: FeatureBatch,
        path: str,
    ) -> None:
        """
        Export computed features to a Parquet file.

        Writes a single table with all feature columns plus metadata.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq

        # Build table from feature columns
        cols = {}
        for fname in self._feature_names:
            if fname in batch.features_computed:
                values = batch.features_computed[fname]
                cols[fname] = pa.array(values, type=pa.float64())

        if not cols:
            logger.warning("no_features_to_export")
            return

        table = pa.table(cols)

        # Add metadata
        table = table.replace_schema_metadata({
            b"polybot_schema_version": b"2.0",
            b"polybot_schema_type": b"features",
            b"asset": batch.asset.encode() if batch.asset else b"",
            b"market_id": batch.market_id.encode() if batch.market_id else b"",
            b"feature_names": ",".join(self._feature_names).encode(),
        })

        pq.write_table(
            table,
            path,
            compression="zstd",
            compression_level=3,
            write_statistics=True,
        )

        logger.info("features_exported_to_parquet",
                     path=path,
                     features=len(cols),
                     rows=batch.ticks_processed)
