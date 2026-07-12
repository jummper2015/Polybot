# src/strategies/regime_aware.py

"""
Regime-Aware Strategy Switching (P11.1).

Provides a RegimeDetector for real-time regime classification and a
RegimeAwareOrchestrator that wraps StrategyEngine with regime-based
strategy filtering.

Each strategy declares which regimes it is active in and optionally
confidence multipliers per regime. The orchestrator classifies the
current market regime before evaluating strategies, skipping those
incompatible with the detected regime.

Architecture:
    RegimeDetector (streaming regime classification)
        ├── FeaturePipeline (streaming feature computation)
        ├── RegimeClassifier (P8.4 heuristic classifier)
        └── Rolling tick buffer (per market)

    StrategyRegimeBinding (per-strategy config)
        ├── allowed_regimes: set[Regime]
        ├── confidence_multipliers: dict[Regime, float] (optional)
        └── enabled: bool

    RegimeAwareOrchestrator
        ├── Wraps StrategyEngine
        ├── Uses RegimeDetector per market
        ├── Filters strategies by regime
        └── Applies confidence multipliers

Usage:
    # Instantiate with existing components
    orchestrator = RegimeAwareOrchestrator(
        strategy_engine=engine,
        regime_classifier=RegimeClassifier(),
        bindings={
            "MeanReversion": StrategyRegimeBinding(
                allowed_regimes={Regime.TREND, Regime.CHOP, Regime.EVENT_DRIVEN},
            ),
            "BuyAboveThreshold": StrategyRegimeBinding(
                allowed_regimes={Regime.TREND},
                confidence_multipliers={Regime.TREND: 1.0},
            ),
        },
    )

    # Use as drop-in replacement for StrategyEngine in TradingService
    await orchestrator.on_cycle_start(market)
    await orchestrator.on_tick(market, tick)
    signal = await orchestrator.should_enter(market, tick)
    signal = await orchestrator.should_exit(market, tick)
    await orchestrator.on_exit(market)
"""

from dataclasses import dataclass, field
from datetime import datetime

import structlog

from src.domain.entities.market import Market
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import Signal
from src.infrastructure.data.features import FeaturePipeline, StreamingState
from src.infrastructure.data.regime import Regime, RegimeClassifier
from src.infrastructure.observability.metrics import (
    EVENT_ACTIVE,
    EVENT_DETECTED,
    EVENT_HALT_ENTRIES,
    EVENT_RESPONSE,
    REGIME_CLASSIFICATIONS,
    REGIME_CONFIDENCE,
    REGIME_CURRENT,
    REGIME_ORCHESTRATOR_ENABLED,
    STRATEGY_ACTIVE_IN_REGIME,
    STRATEGY_ERRORS,
    STRATEGY_SKIPPED_BY_REGIME,
)
from src.strategies.base import IStrategy
from src.strategies.engine import StrategyEngine
from src.strategies.ensemble import EnsembleAggregator, EnsembleConfig, EnsembleResult
from src.strategies.event_detector import (
    EventDetector,
    MarketEvent,
)

# ══════════════════════════════════════════════════════════════════════════
# TICK PROXY (for regime classification from streaming state)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _TickProxy:
    """
    Lightweight MarketTick-like object reconstructable from streaming state.

    Used by RegimeDetector.detect() to provide RegimeClassifier.classify_tick()
    with the tick attributes it needs (yes_price, volume_24h, spread, best_bid,
    best_ask) without requiring the original MarketTick object.
    """
    yes_price: float
    no_price: float
    best_bid: float
    best_ask: float
    spread: float
    volume_24h: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

logger = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY REGIME BINDING
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class StrategyRegimeBinding:
    """
    Per-strategy regime configuration.

    Attributes:
        allowed_regimes: Set of regimes where this strategy is active.
        confidence_multipliers: Optional per-regime confidence boost/reduction.
            Values > 1.0 boost confidence; < 1.0 reduce it.
            Regimes not in this dict default to 1.0 (no adjustment).
        enabled: If False, the strategy is completely disabled regardless
            of regime. Useful for emergency shutdown of individual strategies.
    """

    allowed_regimes: set[Regime] = field(default_factory=lambda: {
        Regime.TREND, Regime.CHOP, Regime.EVENT_DRIVEN,
    })
    confidence_multipliers: dict[Regime, float] = field(default_factory=dict)
    enabled: bool = True


# ══════════════════════════════════════════════════════════════════════════
# REGIME DETECTOR (streaming mode)
# ══════════════════════════════════════════════════════════════════════════


class RegimeDetector:
    """
    Streaming regime classifier for real-time trading.

    Maintains a rolling tick buffer per market, computes features
    incrementally via FeaturePipeline, and classifies the regime
    on demand using RegimeClassifier.

    This is the real-time counterpart to the batch-mode
    RegimeClassifier.classify_batch().
    """

    def __init__(
        self,
        regime_classifier: RegimeClassifier | None = None,
        feature_pipeline: FeaturePipeline | None = None,
        buffer_window: int = 50,
    ):
        """
        Args:
            regime_classifier: RegimeClassifier instance (P8.4).
            feature_pipeline: FeaturePipeline for streaming features.
            buffer_window: Number of ticks to keep per market.
        """
        self._classifier = regime_classifier or RegimeClassifier()
        self._pipeline = feature_pipeline or FeaturePipeline()
        self._buffer_window = buffer_window

        # Per-market state: market_id → StreamingState
        self._streaming_states: dict[str, StreamingState] = {}

        # Cache the last detected regime per market
        self._last_regime: dict[str, Regime] = {}
        self._last_confidence: dict[str, float] = {}

    # ── Public API ──────────────────────────────────────────────────────

    def feed_tick(self, market_id: str, tick: MarketTick) -> None:
        """
        Feed a new tick into the detector.

        Updates the rolling buffer for the market. Regime is NOT
        classified here — call detect() when needed.
        """
        state = self._get_streaming_state(market_id)
        state.push(tick)

    def detect(
        self,
        market_id: str,
    ) -> tuple[Regime, float]:
        """
        Classify the current regime for a market.

        Uses the accumulated tick buffer (populated via feed_tick) to
        compute features and passes them to RegimeClassifier.classify_tick().

        IMPORTANT: This does NOT push the tick — feed_tick() must have
        been called first to populate the buffer. Otherwise features
        would be computed on stale data.

        Returns:
            Tuple of (Regime, confidence).
        """
        state = self._get_streaming_state(market_id)

        if not state.is_ready:
            return Regime.CHOP, 0.3

        # Compute features from accumulated state (no re-push — avoided
        # double-counting bug that would corrupt rolling computations)
        features: dict[str, float | None] = {}
        try:
            features = self._compute_features_from_state(state)
        except Exception as e:
            logger.warning(
                "regime_features_failed",
                market_id=market_id,
                error=str(e),
            )

        # Classify regime from features
        # Use a dummy tick since classify_tick accesses tick attributes
        # only for volume_24h (ILLIQUID check) and yes_price
        last_tick = self._get_last_tick(state)
        try:
            regime, confidence = self._classifier.classify_tick(
                tick=last_tick, features=features,  # type: ignore[arg-type]  # _TickProxy has needed attrs
            )
        except Exception as e:
            logger.warning(
                "regime_classification_failed",
                market_id=market_id,
                error=str(e),
            )
            regime, confidence = Regime.CHOP, 0.3

        self._last_regime[market_id] = regime
        self._last_confidence[market_id] = confidence

        return regime, confidence

    def get_last_regime(self, market_id: str) -> Regime:
        """Get the last detected regime for a market (defaults to CHOP)."""
        return self._last_regime.get(market_id, Regime.CHOP)

    def reset(self, market_id: str) -> None:
        """Reset streaming state for a market (e.g., on market expiry)."""
        self._streaming_states.pop(market_id, None)
        self._last_regime.pop(market_id, None)
        self._last_confidence.pop(market_id, None)

    # ── Internal ────────────────────────────────────────────────────────

    def _get_streaming_state(self, market_id: str) -> StreamingState:
        if market_id not in self._streaming_states:
            self._streaming_states[market_id] = (
                self._pipeline.create_streaming_state(
                    window_size=self._buffer_window,
                )
            )
        return self._streaming_states[market_id]

    def _compute_features_from_state(
        self, state: StreamingState
    ) -> dict[str, float | None]:
        """
        Compute features from accumulated StreamingState WITHOUT re-pushing.

        Uses the same feature computation as FeaturePipeline._streaming_feature
        but operates on the already-accumulated state. This avoids the
        double-push bug where feed_tick() pushes once and detect() would
        push again via compute_streaming().
        """
        import math

        features: dict[str, float | None] = {}

        # spread_percentile
        if len(state.spreads) >= 2:
            current = state.spreads[-1]
            count_le = sum(1 for s in state.spreads if s <= current)
            features["spread_percentile"] = round(count_le / len(state.spreads), 4)
        else:
            features["spread_percentile"] = None

        # realized_volatility
        if len(state.prices) >= 3:
            returns = []
            for j in range(1, len(state.prices)):
                if state.prices[j - 1] > 0 and state.prices[j] > 0:
                    returns.append(math.log(
                        state.prices[j] / state.prices[j - 1]
                    ))
            if len(returns) >= 2:
                mean = sum(returns) / len(returns)
                variance = sum((r - mean) ** 2 for r in returns) / len(returns)
                std = math.sqrt(variance)
                features["realized_volatility"] = round(std * math.sqrt(1051200), 6)
            else:
                features["realized_volatility"] = None
        else:
            features["realized_volatility"] = None

        # momentum_decay — simplified: use latest prices for EWMA
        if len(state.prices) >= 30:
            features["momentum_decay"] = self._compute_momentum_decay(state.prices)
        else:
            features["momentum_decay"] = None

        # liquidity_depth from depth data
        features["liquidity_depth"] = None
        for bv, av in zip(reversed(state.bid_vols), reversed(state.ask_vols)):
            total_bid = sum(bv)
            total_ask = sum(av)
            if total_bid > 0 and total_ask > 0:
                features["liquidity_depth"] = round(total_bid / total_ask, 4)
                break

        # orderbook_imbalance
        features["orderbook_imbalance"] = None
        for bv, av in zip(reversed(state.bid_vols), reversed(state.ask_vols)):
            total_bid = sum(bv)
            total_ask = sum(av)
            denominator = total_bid + total_ask
            if denominator > 0:
                features["orderbook_imbalance"] = round(
                    (total_bid - total_ask) / denominator, 4
                )
                break

        # event_proximity — not available in streaming without market metadata
        features["event_proximity"] = None

        return features

    @staticmethod
    def _compute_momentum_decay(prices: list[float]) -> float | None:
        """Compute momentum decay from price list (simplified streaming version)."""
        import math

        if len(prices) < 30:
            return None

        short_hl = 5
        long_hl = 30

        def ewma_diff(vals, half_life):
            alpha = 1 - math.exp(-math.log(2) / half_life)
            ewma = 0.0
            weight_sum = 0.0
            for j in range(max(1, len(vals) - half_life * 3), len(vals)):
                diff = vals[j] - vals[j - 1]
                dist = len(vals) - 1 - j
                weight = (1 - alpha) ** dist
                ewma += diff * weight
                weight_sum += weight
            return ewma / weight_sum if weight_sum > 0 else None

        short = ewma_diff(prices, short_hl)
        long = ewma_diff(prices, long_hl)

        if short is None or long is None:
            return None

        return round(short - long, 6)

    @staticmethod
    def _get_last_tick(state: StreamingState) -> object:
        """
        Build a minimal MarketTick-like object from streaming state
        for RegimeClassifier.classify_tick() which needs yes_price,
        volume_24h, spread, best_bid, best_ask.
        """
        last_price = state.prices[-1] if state.prices else 0.5
        last_spread = state.spreads[-1] if state.spreads else 0.02
        last_vol = state.volumes[-1] if state.volumes else 5000.0

        # Reconstruct best_bid/best_ask from mid ± half spread
        half_spread = last_spread / 2

        return _TickProxy(
            yes_price=last_price,
            no_price=1.0 - last_price,
            best_bid=last_price - half_spread,
            best_ask=last_price + half_spread,
            spread=last_spread,
            volume_24h=last_vol,
        )


# ══════════════════════════════════════════════════════════════════════════
# REGIME-AWARE ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════


class RegimeAwareOrchestrator:
    """
    Drop-in replacement for StrategyEngine with regime-aware filtering.

    Wraps StrategyEngine and adds:
    1. Real-time regime detection via RegimeDetector
    2. Strategy filtering: only active-in-regime strategies are evaluated
    3. Confidence adjustment: regime multipliers applied to entry signals
    4. Fallback: if no strategy is active in current regime → HOLD

    Maintains the same interface as StrategyEngine so it can be
    swapped in TradingService without changes.

    Each strategy requires a StrategyRegimeBinding. Strategies without
    a binding default to being active in ALL regimes (backward compatible).
    """

    def __init__(
        self,
        strategy_engine: StrategyEngine,
        regime_classifier: RegimeClassifier | None = None,
        feature_pipeline: FeaturePipeline | None = None,
        bindings: dict[str, StrategyRegimeBinding] | None = None,
        buffer_window: int = 50,
        regime_detection_enabled: bool = True,
        ensemble_mode: bool = False,
        ensemble_config: EnsembleConfig | None = None,
        event_detector: EventDetector | None = None,
        event_detection_enabled: bool = True,
    ):
        """
        Args:
            strategy_engine: The underlying StrategyEngine.
            regime_classifier: RegimeClassifier (P8.4). Created if None.
            feature_pipeline: FeaturePipeline for streaming features.
            bindings: Dict mapping strategy_name → StrategyRegimeBinding.
            buffer_window: Ticks to keep for streaming feature computation.
            regime_detection_enabled: If False, acts as passthrough
                (all strategies always active). Useful for A/B testing.
            ensemble_mode: If True (P11.2), evaluates ALL active strategies
                and aggregates their signals instead of first-wins.
            ensemble_config: Configuration for EnsembleAggregator.
            event_detector: Optional EventDetector (P11.4). Created with
                defaults if None and event detection is enabled.
            event_detection_enabled: If False (P11.4), disables event-driven
                response. Useful for A/B testing event detection impact.
        """
        self._engine = strategy_engine
        self._bindings = bindings or {}
        self._enabled = regime_detection_enabled
        self._ensemble_mode = ensemble_mode
        self._event_enabled = event_detection_enabled

        # Event detector (P11.4) — detects price shocks, volume surges, etc.
        self._event_detector = event_detector or (
            EventDetector() if event_detection_enabled else None
        )

        # Ensemble aggregator (P11.2)
        self._ensemble = (
            EnsembleAggregator(config=ensemble_config)
            if ensemble_mode else None
        )

        # Regime detector (only used if regime detection is enabled)
        self._detector = RegimeDetector(
            regime_classifier=regime_classifier or RegimeClassifier(),
            feature_pipeline=feature_pipeline or FeaturePipeline(),
            buffer_window=buffer_window,
        )

        # HOLD signal factory (reuse from engine)
        self._HOLD = self._engine._HOLD

        # Expose orchestrator status as metric
        REGIME_ORCHESTRATOR_ENABLED.set(1 if regime_detection_enabled else 0)

        logger.info(
            "regime_aware_orchestrator_initialized",
            enabled=regime_detection_enabled,
            ensemble_mode=ensemble_mode,
            event_detection_enabled=event_detection_enabled,
            strategies=self.registered_strategies(),
            bindings={name: list(b.allowed_regimes) for name, b in self._bindings.items()},
        )

    # ── IStrategy-compatible interface (same as StrategyEngine) ──────────

    @property
    def name(self) -> str:
        return "RegimeAwareOrchestrator"

    async def on_cycle_start(self, market: Market) -> None:
        """Delegate to underlying StrategyEngine."""
        await self._engine.on_cycle_start(market)

    async def on_tick(self, market: Market, tick: MarketTick) -> None:
        """
        Feed tick to both the regime detector and the strategy engine.
        """
        if self._enabled:
            self._detector.feed_tick(market.id, tick)
        if self._event_enabled and self._event_detector is not None:
            self._event_detector.feed_tick(tick, market)  # type: ignore[union-attr]
        await self._engine.on_tick(market, tick)

    async def should_enter(
        self, market: Market, tick: MarketTick
    ) -> Signal:
        """
        Evaluate entry with regime filtering.

        Two modes:
          - First-wins (default, P11.1): evaluates strategies in
            priority order, returns first actionable signal.
          - Ensemble (P11.2): evaluates ALL active strategies,
            aggregates their signals via EnsembleAggregator.
        """
        # Detect regime and market events
        regime, regime_conf = self._detect_regime(market)
        self._emit_regime_metrics(market, regime, regime_conf)

        # P11.4: Event-driven response — check for blocking events first
        if self._event_enabled and self._event_detector is not None:
            events = self._event_detector.detect(tick, market)  # type: ignore[union-attr]
            self._emit_event_metrics(market, events)
            if events:
                response = self._event_detector.respond(  # type: ignore[union-attr]
                    events, order_size=0.0, confidence=0.0
                )
                EVENT_RESPONSE.labels(
                    asset=market.asset.value, action=response.action.value
                ).inc()
                if response.should_halt:
                    EVENT_HALT_ENTRIES.labels(asset=market.asset.value).inc()
                    EVENT_ACTIVE.labels(
                        asset=market.asset.value, market_id=market.id
                    ).set(1)
                    logger.info(
                        "event_halt_entry",
                        market_id=market.id,
                        reasons=response.reasons,
                    )
                    return self._HOLD(market.id, "EventDetector:HALT")  # type: ignore[misc]  # _HOLD is a callable factory
            EVENT_ACTIVE.labels(
                asset=market.asset.value, market_id=market.id
            ).set(0)
        else:
            EVENT_ACTIVE.labels(
                asset=market.asset.value, market_id=market.id
            ).set(0)

        if self._ensemble_mode:
            return await self._ensemble_enter(market, tick, regime)
        else:
            return await self._single_enter(market, tick, regime)

    async def _single_enter(
        self, market: Market, tick: MarketTick, regime: Regime
    ) -> Signal:
        """First-wins: evaluate strategies in priority order (P11.1)."""
        for strategy in self._engine._strategies:
            if not self._is_strategy_active(strategy.name, regime):
                logger.debug(
                    "strategy_skipped_by_regime",
                    strategy=strategy.name,
                    regime=regime.value,
                    market_id=market.id,
                )
                STRATEGY_SKIPPED_BY_REGIME.labels(
                    strategy=strategy.name,
                    regime=regime.value,
                ).inc()
                continue

            STRATEGY_ACTIVE_IN_REGIME.labels(
                strategy=strategy.name,
                regime=regime.value,
            ).inc()

            state = self._engine._get_state(strategy, market)
            if state.in_position:
                continue

            try:
                signal = await strategy.should_enter(market, tick)
            except Exception as e:
                logger.error(
                    "strategy_should_enter_error",
                    strategy=strategy.name,
                    market_id=market.id,
                    error=str(e),
                )
                STRATEGY_ERRORS.labels(strategy=strategy.name).inc()
                continue

            if signal.is_actionable():
                if self._enabled:
                    signal = self._apply_multiplier(signal, strategy.name, regime)

                logger.info(
                    "entry_signal_regime_aware",
                    strategy=strategy.name,
                    market_id=market.id,
                    regime=regime.value,
                    signal_type=signal.type.value,
                    confidence=signal.confidence,
                    reason=signal.reason,
                )
                return signal

        return self._HOLD(market.id, "RegimeAwareOrchestrator")

    async def _ensemble_enter(
        self, market: Market, tick: MarketTick, regime: Regime
    ) -> Signal:
        """
        Ensemble mode (P11.2): evaluate ALL active strategies,
        collect their signals, and aggregate via EnsembleAggregator.
        """
        signals: list[tuple[str, Signal]] = []

        for strategy in self._engine._strategies:
            if not self._is_strategy_active(strategy.name, regime):
                STRATEGY_SKIPPED_BY_REGIME.labels(
                    strategy=strategy.name,
                    regime=regime.value,
                ).inc()
                continue

            STRATEGY_ACTIVE_IN_REGIME.labels(
                strategy=strategy.name,
                regime=regime.value,
            ).inc()

            state = self._engine._get_state(strategy, market)
            if state.in_position:
                continue

            try:
                raw_signal = await strategy.should_enter(market, tick)
            except Exception as e:
                logger.error(
                    "strategy_should_enter_error",
                    strategy=strategy.name,
                    market_id=market.id,
                    error=str(e),
                )
                STRATEGY_ERRORS.labels(strategy=strategy.name).inc()
                continue

            # Apply regime confidence multiplier (even for HOLD signals,
            # since the ensemble needs all inputs)
            if self._enabled:
                raw_signal = self._apply_multiplier(
                    raw_signal, strategy.name, regime
                )

            signals.append((strategy.name, raw_signal))

        if not signals:
            return self._HOLD(market.id, "RegimeAwareOrchestrator")

        # Aggregate via EnsembleAggregator
        result: EnsembleResult = self._ensemble.aggregate(  # type: ignore[union-attr]  # _ensemble is set when ensemble_mode=True
            signals, market, tick
        )

        if result.is_actionable:
            logger.info(
                "ensemble_entry_signal",
                strategies=result.contributing_strategies,
                agreement_level=result.agreement_level,
                confidence=result.ensemble_confidence,
                had_conflict=result.had_conflict,
                market_id=market.id,
            )

        return result.signal

    def _detect_regime(self, market: Market) -> tuple[Regime, float]:
        """Detect current regime, falling back to CHOP on error."""
        try:
            regime, regime_conf = self._detector.detect(market.id)
        except Exception as e:
            logger.warning(
                "regime_detection_failed",
                market_id=market.id,
                error=str(e),
            )
            regime, regime_conf = Regime.CHOP, 0.3

        logger.debug(
            "regime_detected",
            market_id=market.id,
            regime=regime.value,
            confidence=round(regime_conf, 3),
        )
        return regime, regime_conf

    def _emit_regime_metrics(
        self, market: Market, regime: Regime, confidence: float
    ) -> None:
        """Emit Prometheus regime metrics for dashboard."""
        for r in Regime:
            REGIME_CURRENT.labels(
                asset=market.asset.value,
                window=market.window.value,
                regime=r.value,
            ).set(1 if r == regime else 0)

        REGIME_CONFIDENCE.labels(
            asset=market.asset.value,
            window=market.window.value,
            regime=regime.value,
        ).set(confidence)

        REGIME_CLASSIFICATIONS.labels(
            asset=market.asset.value,
            window=market.window.value,
            regime=regime.value,
        ).inc()

    def _emit_event_metrics(
        self, market: Market, events: list[MarketEvent]
    ) -> None:
        """Emit Prometheus metrics for detected market events (P11.4)."""
        if not events:
            return
        for e in events:
            EVENT_DETECTED.labels(
                asset=market.asset.value,
                event_type=e.event_type.value,
                severity=e.severity.value,
            ).inc()

    async def should_exit(
        self, market: Market, tick: MarketTick
    ) -> Signal:
        """
        Evaluate exit with regime filtering.

        Exit signals are NOT filtered by regime — if we have a position,
        we always check exit conditions regardless of current regime.
        This is a safety measure: you don't want to hold a position
        through a PANIC just because the strategy isn't "active" in PANIC.
        """
        for strategy in self._engine._strategies:
            state = self._engine._get_state(strategy, market)

            if not state.in_position:
                continue

            try:
                signal = await strategy.should_exit(market, tick)
            except Exception as e:
                logger.error(
                    "strategy_should_exit_error",
                    strategy=strategy.name,
                    market_id=market.id,
                    error=str(e),
                )
                STRATEGY_ERRORS.labels(strategy=strategy.name).inc()
                continue

            if signal.is_actionable():
                logger.info(
                    "exit_signal_regime_aware",
                    strategy=strategy.name,
                    market_id=market.id,
                    signal_type=signal.type.value,
                    reason=signal.reason,
                )
                return signal

        return self._HOLD(market.id, "RegimeAwareOrchestrator")

    async def on_exit(self, market: Market) -> None:
        """Delegate to underlying StrategyEngine."""
        await self._engine.on_exit(market)

    # ── State management (passthrough) ──────────────────────────────────

    def mark_entry(
        self, strategy_name: str, market_id: str, price: float
    ) -> None:
        """Delegate to underlying StrategyEngine."""
        self._engine.mark_entry(strategy_name, market_id, price)

    def mark_exit(self, strategy_name: str, market_id: str) -> None:
        """Delegate to underlying StrategyEngine."""
        self._engine.mark_exit(strategy_name, market_id)

    def get_state(self, strategy_name: str, market_id: str):
        """Delegate to underlying StrategyEngine."""
        return self._engine.get_state(strategy_name, market_id)

    def registered_strategies(self) -> list[str]:
        """List of registered strategy names."""
        return self._engine.registered_strategies()

    def clear_state(self, market_id: str) -> None:
        """Clear state for a market (engine + detector)."""
        self._engine._clear_state(market_id)
        if self._enabled:
            self._detector.reset(market_id)

    # ── Regime utilities ────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """Whether regime detection is enabled."""
        return self._enabled

    def get_current_regime(self, market_id: str) -> Regime:
        """Get the last detected regime for a market."""
        return self._detector.get_last_regime(market_id)

    def get_regime_confidence(self, market_id: str) -> float:
        """Get the confidence of the last regime detection for a market."""
        return self._detector._last_confidence.get(market_id, 0.0)

    def is_strategy_active_in_regime(
        self, strategy_name: str, regime: Regime
    ) -> bool:
        """Public wrapper: check if a strategy is active in a given regime."""
        return self._is_strategy_active(strategy_name, regime)

    def get_regime_status(self, market_id: str) -> dict | None:
        """
        Return full regime status for a market.

        Returns a dict with keys:
            regime, confidence, strategies_active, strategies_inactive
        or None if orchestrator is disabled.
        """
        if not self._enabled:
            return None

        regime = self.get_current_regime(market_id)
        confidence = self.get_regime_confidence(market_id)

        strategies_active = []
        strategies_inactive = []
        for name in self.registered_strategies():
            if self._is_strategy_active(name, regime):
                strategies_active.append(name)
            else:
                strategies_inactive.append(name)

        return {
            "regime": regime,
            "confidence": confidence,
            "strategies_active": strategies_active,
            "strategies_inactive": strategies_inactive,
        }

    def set_strategy_regime(
        self, strategy_name: str, binding: StrategyRegimeBinding
    ) -> None:
        """Update or add a regime binding for a strategy."""
        self._bindings[strategy_name] = binding
        logger.info(
            "regime_binding_updated",
            strategy=strategy_name,
            allowed_regimes=[r.value for r in binding.allowed_regimes],
        )

    def disable_strategy(self, strategy_name: str) -> None:
        """Disable a strategy regardless of regime."""
        if strategy_name in self._bindings:
            self._bindings[strategy_name].enabled = False
        else:
            self._bindings[strategy_name] = StrategyRegimeBinding(
                allowed_regimes=set(Regime),
                enabled=False,
            )
        logger.info("strategy_disabled", strategy=strategy_name)

    def enable_strategy(self, strategy_name: str) -> None:
        """Re-enable a previously disabled strategy."""
        if strategy_name in self._bindings:
            binding = self._bindings[strategy_name]
            binding.enabled = True
            # Restore to all regimes if allowed_regimes was emptied
            if not binding.allowed_regimes:
                binding.allowed_regimes = set(Regime)
            logger.info("strategy_enabled", strategy=strategy_name)

    # ── Internal ────────────────────────────────────────────────────────

    def _is_strategy_active(self, strategy_name: str, regime: Regime) -> bool:
        """Check if a strategy should be active in the given regime."""
        # If regime detection is globally disabled, all strategies are active
        if not self._enabled:
            return True
        binding = self._bindings.get(strategy_name)
        if binding is None:
            # No binding → active in all regimes (backward compatible)
            return True
        return binding.enabled and regime in binding.allowed_regimes

    def _apply_multiplier(
        self,
        signal: Signal,
        strategy_name: str,
        regime: Regime,
    ) -> Signal:
        """Apply regime-based confidence multiplier to a signal."""
        binding = self._bindings.get(strategy_name)
        if binding is None:
            return signal

        multiplier = binding.confidence_multipliers.get(regime, 1.0)
        if multiplier == 1.0:
            return signal

        adjusted = min(1.0, signal.confidence * multiplier)
        adjusted = max(0.0, adjusted)

        logger.debug(
            "confidence_adjusted_by_regime",
            strategy=strategy_name,
            regime=regime.value,
            original=round(signal.confidence, 4),
            multiplier=round(multiplier, 3),
            adjusted=round(adjusted, 4),
        )

        return Signal(
            type=signal.type,
            market_id=signal.market_id,
            confidence=round(adjusted, 4),
            source_strategy=signal.source_strategy,
            reason=signal.reason,
            timestamp=signal.timestamp,
        )


# ══════════════════════════════════════════════════════════════════════════
# FACTORY HELPERS
# ══════════════════════════════════════════════════════════════════════════


def create_binding_from_config(
    strategy_name: str,
    allowed_regimes: list[str] | None = None,
    confidence_multipliers: dict[str, float] | None = None,
) -> StrategyRegimeBinding:
    """
    Create a StrategyRegimeBinding from string-based regime names.

    Utility for wiring from config files or environment variables.

    Args:
        strategy_name: Strategy identifier (for logging).
        allowed_regimes: List of regime strings (e.g. ["trend", "chop"]).
            If None, defaults to all regimes (backward compatible).
        confidence_multipliers: Dict mapping regime_name → multiplier.

    Returns:
        StrategyRegimeBinding ready for RegimeAwareOrchestrator.
    """
    if allowed_regimes is None:
        allowed = set(Regime)
    else:
        allowed = set()
        for r_name in allowed_regimes:
            try:
                allowed.add(Regime(r_name))
            except ValueError:
                logger.warning(
                    "invalid_regime_name",
                    strategy=strategy_name,
                    regime=r_name,
                )

    multipliers: dict[Regime, float] = {}
    if confidence_multipliers:
        for r_name, mult in confidence_multipliers.items():
            try:
                multipliers[Regime(r_name)] = mult
            except ValueError:
                logger.warning(
                    "invalid_multiplier_regime",
                    strategy=strategy_name,
                    regime=r_name,
                )

    return StrategyRegimeBinding(
        allowed_regimes=allowed,
        confidence_multipliers=multipliers,
        enabled=True,
    )


def build_orchestrator(
    strategies: list[IStrategy],
    regime_classifier: RegimeClassifier | None = None,
    feature_pipeline: FeaturePipeline | None = None,
    regime_configs: dict[str, dict] | None = None,
    ensemble_mode: bool = False,
    ensemble_config: EnsembleConfig | None = None,
) -> RegimeAwareOrchestrator:
    """
    Build a RegimeAwareOrchestrator with auto-configured bindings.

    Convenience factory that:
    1. Creates a StrategyEngine from the strategy list
    2. Reads `allowed_regimes` from each strategy's config (if available)
    3. Creates StrategyRegimeBinding for each strategy
    4. Wraps everything in a RegimeAwareOrchestrator

    Args:
        strategies: List of IStrategy instances.
        regime_classifier: Optional RegimeClassifier.
        feature_pipeline: Optional FeaturePipeline.
        regime_configs: Optional dict mapping strategy_name → config dict.
            Each config dict can have 'allowed_regimes' (list[str]) and
            'confidence_multipliers' (dict[str, float]).
        ensemble_mode: If True (P11.2), enables ensemble signal aggregation.
        ensemble_config: Configuration for EnsembleAggregator.

    Returns:
        Configured RegimeAwareOrchestrator ready for use.
    """
    engine = StrategyEngine(strategies)

    bindings: dict[str, StrategyRegimeBinding] = {}

    for strategy in strategies:
        name = strategy.name

        # Try to get regime config from passed configs or from strategy config attr
        allowed = None
        multipliers = None

        if regime_configs and name in regime_configs:
            cfg = regime_configs[name]
            allowed = cfg.get("allowed_regimes")
            multipliers = cfg.get("confidence_multipliers")
        elif hasattr(strategy, "_config") and hasattr(strategy._config, "allowed_regimes"):
            allowed = strategy._config.allowed_regimes

        binding = create_binding_from_config(
            strategy_name=name,
            allowed_regimes=allowed,
            confidence_multipliers=multipliers,
        )
        bindings[name] = binding

    return RegimeAwareOrchestrator(
        strategy_engine=engine,
        regime_classifier=regime_classifier,
        feature_pipeline=feature_pipeline,
        bindings=bindings,
        ensemble_mode=ensemble_mode,
        ensemble_config=ensemble_config,
    )
