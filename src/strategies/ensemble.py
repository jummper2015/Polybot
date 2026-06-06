# src/strategies/ensemble.py

"""
P11.2 — Ensemble Signal Engine.

Aggregates signals from multiple active strategies into a unified
ensemble signal. Instead of the "first-wins" approach (P11.1),
the ensemble evaluates ALL strategies simultaneously and combines
their outputs with dynamic weighting.

Key features:
  - Weighted aggregation: strategies weighted by their confidence
  - Agreement bonus: when 2+ strategies agree, boost ensemble confidence
  - Conflict detection: opposing signals → HOLD (safety-first)
  - Per-strategy contribution tracking
  - Backward-compatible with RegimeAwareOrchestrator

Architecture:
    RegimeAwareOrchestrator (ensemble_mode=True)
        └── EnsembleAggregator
            ├── Collects signals from ALL active strategies
            ├── Aggregates by type (BUY_YES vs HOLD vs conflict)
            ├── Applies agreement bonuses
            └── Returns consensus Signal or HOLD
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from src.domain.entities.market import Market
from src.domain.enums.signal_type import SignalType
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import Signal
from src.infrastructure.observability.metrics import (
    ENSEMBLE_AGREEMENT_BONUS,
    ENSEMBLE_CONFLICTS,
    ENSEMBLE_CONTRIBUTIONS,
    ENSEMBLE_SIGNALS,
    ENSEMBLE_WEIGHTS,
)

logger = structlog.get_logger(__name__)


@dataclass
class EnsembleConfig:
    """Configuration for the EnsembleAggregator."""

    # When 2+ strategies agree on BUY, boost ensemble confidence by this amount
    agreement_bonus: float = 0.10

    # Strategies with confidence below this threshold are treated as HOLD
    # (too weak to influence the ensemble)
    min_confidence_threshold: float = 0.05

    # If True, return HOLD when strategies disagree (BUY vs SELL).
    # Safety-first: better to miss a trade than enter on conflict.
    conflict_to_hold: bool = True

    # Per-strategy weight multipliers (strategy_name → multiplier).
    # A multiplier of 0.5 means the strategy's confidence counts half.
    # A multiplier of 2.0 means it counts double.
    strategy_weights: dict[str, float] = field(default_factory=dict)


@dataclass
class EnsembleResult:
    """Result of ensemble signal aggregation."""

    signal: Signal
    contributing_strategies: list[str]
    agreement_level: int  # Number of strategies that agreed
    had_conflict: bool
    ensemble_confidence: float

    @property
    def is_actionable(self) -> bool:
        return self.signal.is_actionable()


class EnsembleAggregator:
    """
    Aggregates multiple strategy signals into an ensemble consensus.

    Algorithm:
        1. Filter signals by min_confidence_threshold
        2. Group by signal type (BUY_YES, BUY_NO, EXIT, HOLD)
        3. If any BUY_NO/EXIT signal → conflict (return HOLD by default)
        4. If all actionable signals are BUY_YES:
           a. Weighted average confidence (weight = strategy_weight multiplier)
           b. If 2+ strategies contributed → apply agreement bonus
           c. Return ensemble BUY_YES signal
        5. If no actionable signals → return HOLD
    """

    def __init__(self, config: EnsembleConfig | None = None):
        self._config = config or EnsembleConfig()

    def aggregate(
        self,
        signals: list[tuple[str, Signal]],
        market:  Market,
        tick:    MarketTick,
    ) -> EnsembleResult:
        """
        Aggregate a list of (strategy_name, Signal) tuples into an ensemble result.

        Args:
            signals: List of (strategy_name, Signal) from all active strategies.
            market:  The market being evaluated.
            tick:    The current market tick.

        Returns:
            EnsembleResult with the aggregated signal and metadata.
        """
        log = logger.bind(
            market_id=market.id,
            asset=market.asset.value,
            window=market.window.value,
        )

        # ── Step 1: Filter by confidence threshold ──────────────────
        actionable = [
            (name, sig)
            for name, sig in signals
            if sig.is_actionable()
            and sig.confidence >= self._config.min_confidence_threshold
        ]

        if not actionable:
            ENSEMBLE_SIGNALS.labels(outcome="hold_all_below_threshold").inc()
            return EnsembleResult(
                signal=self._make_hold(market.id, tick.timestamp),
                contributing_strategies=[],
                agreement_level=0,
                had_conflict=False,
                ensemble_confidence=0.0,
            )

        # ── Step 2: Group by type ────────────────────────────────────
        buy_yes: list[tuple[str, Signal]] = []
        buy_no_or_sell: list[tuple[str, Signal]] = []

        for name, sig in actionable:
            if sig.type == SignalType.BUY_YES:
                buy_yes.append((name, sig))
            elif sig.type == SignalType.BUY_NO:
                # BUY_NO is opposing entry signal (short/hedge) → conflict
                buy_no_or_sell.append((name, sig))
            elif sig.type == SignalType.EXIT:
                # EXIT from should_enter is unusual but treat as opposing
                buy_no_or_sell.append((name, sig))
            # HOLD is ignored here (already filtered by is_actionable)

        # ── Step 3: Conflict detection ───────────────────────────────
        if buy_no_or_sell and self._config.conflict_to_hold:
            # Different strategies want opposite things → safety HOLD
            ENSEMBLE_CONFLICTS.inc()
            ENSEMBLE_SIGNALS.labels(outcome="conflict_hold").inc()
            buy_names = [n for n, _ in buy_yes]
            sell_names = [n for n, _ in buy_no_or_sell]
            log.info(
                "ensemble_conflict_detected",
                buy_strategies=buy_names,
                sell_strategies=sell_names,
                outcome="HOLD",
            )
            return EnsembleResult(
                signal=self._make_hold(market.id, tick.timestamp),
                contributing_strategies=buy_names + sell_names,
                agreement_level=0,
                had_conflict=True,
                ensemble_confidence=0.0,
            )

        # ── Step 4: Aggregate BUY_YES signals ────────────────────────
        if not buy_yes:
            # Only SELL/BUY_NO signals and conflict_to_hold=False
            ENSEMBLE_SIGNALS.labels(outcome="no_buy_signals").inc()
            return EnsembleResult(
                signal=self._make_hold(market.id, tick.timestamp),
                contributing_strategies=[n for n, _ in buy_no_or_sell],
                agreement_level=0,
                had_conflict=bool(buy_no_or_sell),
                ensemble_confidence=0.0,
            )

        total_weight = 0.0
        weighted_confidence = 0.0
        contributing: list[str] = []

        for name, sig in buy_yes:
            # Weight = strategy_weight_multiplier.
            # Each strategy's contribution to the ensemble is proportional
            # to its configured weight. Confidence acts as the value being
            # averaged, not as the voting power multiplier.
            strategy_mult = self._config.strategy_weights.get(name, 1.0)
            weighted_confidence += sig.confidence * strategy_mult
            total_weight += strategy_mult
            contributing.append(name)

        if total_weight <= 0:
            ensemble_conf = 0.0
        else:
            ensemble_conf = weighted_confidence / total_weight

        # ── Step 5: Agreement bonus ──────────────────────────────────
        agreement_level = len(buy_yes)
        if agreement_level >= 2:
            bonus = self._config.agreement_bonus
            ensemble_conf = min(1.0, ensemble_conf + bonus)
            ENSEMBLE_AGREEMENT_BONUS.inc()
            log.debug(
                "ensemble_agreement_bonus_applied",
                strategies=contributing,
                count=agreement_level,
                bonus=bonus,
                confidence_before=round(ensemble_conf - bonus, 4),
                confidence_after=round(ensemble_conf, 4),
            )

        # ── Emit per-strategy contribution metrics ───────────────────
        for name in contributing:
            ENSEMBLE_CONTRIBUTIONS.labels(strategy=name).inc()
            ENSEMBLE_WEIGHTS.labels(strategy=name).set(
                self._config.strategy_weights.get(name, 1.0)
            )

        ENSEMBLE_SIGNALS.labels(outcome="buy_yes").inc()

        # ── Build ensemble signal ────────────────────────────────────
        reason = (
            f"Ensemble({','.join(contributing)}) "
            f"agreement={agreement_level} "
            f"conflict={bool(buy_no_or_sell)}"
        )

        ensemble_signal = Signal(
            type=SignalType.BUY_YES,
            market_id=market.id,
            confidence=round(ensemble_conf, 4),
            source_strategy="Ensemble",
            reason=reason,
            timestamp=tick.timestamp,
        )

        log.info(
            "ensemble_signal_generated",
            strategies=contributing,
            confidence=round(ensemble_conf, 4),
            agreement_level=agreement_level,
            had_conflict=bool(buy_no_or_sell),
        )

        return EnsembleResult(
            signal=ensemble_signal,
            contributing_strategies=contributing,
            agreement_level=agreement_level,
            had_conflict=bool(buy_no_or_sell),
            ensemble_confidence=ensemble_conf,
        )

    @staticmethod
    def _make_hold(market_id: str, timestamp: datetime | None = None) -> Signal:
        """Create a HOLD signal with consistent timestamp."""
        ts = timestamp or datetime.now(timezone.utc)
        return Signal(
            type=SignalType.HOLD,
            market_id=market_id,
            confidence=0.0,
            source_strategy="Ensemble",
            reason="Ensemble — no consensus",
            timestamp=ts,
        )

    def get_strategy_weight(self, strategy_name: str) -> float:
        """Get the configured weight for a strategy (default 1.0)."""
        return self._config.strategy_weights.get(strategy_name, 1.0)
