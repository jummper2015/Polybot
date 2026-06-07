# src/execution/queue_position.py

"""
P9.3 — Queue Position Modeling: maker fill probability and cost optimization.

Models the probability that a MAKER (limit) order will fill within a
configurable wait window, based on estimated queue position in the orderbook.
Enables maker-vs-taker cost comparison for optimal execution mode selection.

Architecture:
    tick_data, order_size, wait_time_T, volatility, regime
        │
        ▼
    QueuePositionModel.estimate()
        ├─ QueueTurnoverModel      → volume_sec = volume_24h / 86400
        ├─ Fill probability        → P(fill) = 1 - exp(-expected_vol / volume_needed)
        └─ AdverseSelectionAdjuster → cost ≈ volatility × time_to_fill × regime_factor
        │
        ▼
    QueuePositionEstimate (p_fill, expected_time_to_fill, adverse_selection_bps)
        │
        ▼
    CostComparator.compare(taker_cost, maker_estimate) → Decision (MAKER / TAKER)

Key constraints:
    - No Level 3 data (no individual order IDs) → queue position is approximated
      from L1 depth as a proxy for total resting orders at the best price.
    - Polymarket orderbooks have limited depth; fallback strategies handle
      zero-volume / zero-depth gracefully.

Usage:
    config = QueuePositionConfig(wait_time_T=30.0)
    engine = QueuePositionModel(config)

    estimate = engine.estimate(
        tick_data={"volume_24h": 5000.0, "asks_vol_1": 20000.0, ...},
        order_size=10.0,
        side="entry",
        volatility=0.15,
        regime="CHOP",
    )
    print(f"P(fill) in {config.wait_time_T}s: {estimate.p_fill:.2%}")

Refs: WORKFLOW.md P9.3 PLAN, ROADMAP.md Phase 7 (Execution Realism)
"""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class QueuePositionConfig:
    """Configuration for queue position modeling and maker/taker decisions."""

    wait_time_T: float = 30.0  # noqa: N815
    """Maximum time (seconds) the maker is willing to wait for a fill."""

    missed_entry_factor: float = 0.5
    """Cost multiplier for a missed entry relative to taker slippage.
    delay_penalty = taker_slippage * missed_entry_factor."""

    maker_discount_threshold: float = 0.95
    """Minimum cost ratio (maker/taker) to prefer maker over taker.
    E.g., 0.95 means maker must be at least 5% cheaper."""

    fallback_volume_sec: float = 0.01
    """Default volume-per-second (USDC) when volume_24h is zero or missing."""

    min_l1_depth: float = 1.0
    """Minimum assumed L1 depth to avoid division-by-zero in fill probability."""


# ══════════════════════════════════════════════════════════════════════════
# QUEUE POSITION ESTIMATE
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class QueuePositionEstimate:
    """Result of a maker fill probability estimation."""

    p_fill: float
    """Probability of fill within wait_time_T (0.0-1.0)."""

    expected_time_to_fill: float
    """Expected time to full fill (seconds). Infinity if p_fill < 0.01."""

    adverse_selection_bps: float
    """Expected adverse selection cost in basis points (non-negative)."""

    confidence: float = 1.0
    """Confidence in the estimate (0.0-1.0). Degraded when using fallbacks."""

    wait_time_T: float = 30.0  # noqa: N815
    """Wait window used for this estimate."""

    volume_sec: float = 0.0
    """Estimated taker volume per second (USDC/sec)."""

    l1_depth: float = 0.0
    """L1 depth at the relevant side (USDC)."""

    regime: str = "UNKNOWN"
    """Regime at time of estimate."""

    volatility: float = 0.0
    """Annualized volatility used for adverse selection."""

    @property
    def is_viable(self) -> bool:
        """Whether maker execution is worth considering (P(fill) > 50%)."""
        return self.p_fill > 0.50

    @property
    def fill_time_seconds(self) -> float:
        """Alias for expected_time_to_fill."""
        return self.expected_time_to_fill

    @property
    def adverse_selection_pct(self) -> float:
        """Adverse selection as percentage (bps / 100)."""
        return self.adverse_selection_bps / 100.0


# ══════════════════════════════════════════════════════════════════════════
# QUEUE TURNOVER MODEL
# ══════════════════════════════════════════════════════════════════════════


class QueueTurnoverModel:
    """Estimates taker order arrival rate from 24h volume.

    In a CLOB, taker orders consume resting liquidity at the best bid/ask.
    The arrival rate of these taker orders determines how quickly the queue
    clears and whether a maker order gets filled.

    Uses volume_24h as a robust proxy (avoids noisy tick-to-tick depth
    changes which are dominated by adds/cancels, not trades).
    """

    def __init__(self, config: QueuePositionConfig | None = None):
        self._config = config or QueuePositionConfig()

    def estimate_volume_per_sec(
        self,
        volume_24h: float,
    ) -> tuple[float, float]:
        """Estimate taker volume per second and confidence.

        Args:
            volume_24h: 24-hour trading volume in USDC.

        Returns:
            (volume_sec, confidence): Estimated volume per second and
            confidence (1.0 = real data, 0.5 = fallback used).
        """
        if volume_24h is None or volume_24h <= 0:
            fallback = self._config.fallback_volume_sec
            logger.debug(
                "queue_turnover_fallback",
                volume_24h=volume_24h,
                fallback_volume_sec=fallback,
            )
            return fallback, 0.5

        volume_sec = volume_24h / 86400.0  # seconds in 24h

        # Sanity check: cap at reasonable maximum (~$10M/day)
        max_volume_sec = 1_000_000.0 / 86400.0  # ~115.7 USDC/sec
        if volume_sec > max_volume_sec:
            volume_sec = max_volume_sec

        logger.debug(
            "queue_turnover_estimated",
            volume_24h=volume_24h,
            volume_sec=round(volume_sec, 4),
        )

        return volume_sec, 1.0


# ══════════════════════════════════════════════════════════════════════════
# QUEUE POSITION MODEL
# ══════════════════════════════════════════════════════════════════════════


class QueuePositionModel:
    """Models fill probability for a maker (limit) order.

    Formula (time-bounded exponential):
        volume_needed = max(L1_depth, min_l1_depth) + order_size
        expected_taker_vol = volume_sec * wait_time_T
        P(fill) = 1 - exp(-expected_taker_vol / volume_needed)

    Intuition:
        - Deep L1 (large depth) → more volume needed to clear → lower P(fill)
        - High volume_sec → faster queue turnover → higher P(fill)
        - Longer wait_time_T → more taker volume arrives → higher P(fill)
        - Small order_size → less volume needed → higher P(fill)
    """

    def __init__(self, config: QueuePositionConfig | None = None):
        self._config = config or QueuePositionConfig()

    def estimate_fill_probability(
        self,
        order_size: float,
        l1_depth: float,
        volume_sec: float,
        wait_time_T: float | None = None,  # noqa: N803
    ) -> tuple[float, float]:
        """Estimate P(fill) and expected time to fill.

        Args:
            order_size: Order size in USDC.
            l1_depth: Total depth at the best price level (L1) in USDC.
            volume_sec: Estimated taker volume per second (USDC/sec).
            wait_time_T: Wait window in seconds (defaults to config).

        Returns:
            (p_fill, expected_time_to_fill_seconds):
                p_fill in [0.0, 1.0], time_to_fill in seconds.
        """
        wait_time_t = wait_time_T if wait_time_T is not None else self._config.wait_time_T

        # ── Guards ────────────────────────────────────────────────────
        if order_size <= 0:
            return 1.0, 0.0  # zero-size order always fills

        if volume_sec <= 0:
            return 0.0, float("inf")  # no taker volume → never fills

        l1 = max(l1_depth, self._config.min_l1_depth)
        wait_time = max(wait_time_t, 1.0)  # minimum 1s to avoid degenerate

        # ── Core formula ──────────────────────────────────────────────
        volume_needed = l1 + order_size
        expected_taker_vol = volume_sec * wait_time

        if volume_needed <= 0:
            return 1.0, 0.0

        ratio = expected_taker_vol / volume_needed
        p_fill = 1.0 - self._safe_exp(-ratio)

        # Clamp to [0, 1]
        p_fill = max(0.0, min(1.0, p_fill))

        # ── Expected time to fill ────────────────────────────────────
        if p_fill < 0.01:
            expected_time = float("inf")
        elif volume_sec > 0:
            # Linear approximation: time = volume_needed / volume_sec
            expected_time = volume_needed / volume_sec
            expected_time = min(expected_time, wait_time * 10)  # cap at 10x wait window
        else:
            expected_time = float("inf")

        return p_fill, expected_time

    @staticmethod
    def _safe_exp(x: float) -> float:
        """Compute exp(x), clamped to avoid overflow."""
        # exp(50) ≈ 5e21 — beyond this, floating precision degrades
        if x > 50:
            return float("inf")
        if x < -50:
            return 0.0

        import math

        return math.exp(x)


# ══════════════════════════════════════════════════════════════════════════
# ADVERSE SELECTION ADJUSTER
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class AdverseSelectionConfig:
    """Configuration for adverse selection cost estimation."""

    base_bps_per_second: float = 0.05
    """Base adverse selection cost in bps per second of wait time."""

    panic_regime_multiplier: float = 4.0
    """Multiplier for PANIC regime (extreme adverse selection risk)."""

    trend_regime_multiplier: float = 2.0
    """Multiplier for TREND regime (directional risk)."""

    illiquid_regime_multiplier: float = 1.5
    """Multiplier for ILLIQUID regime."""

    event_driven_multiplier: float = 2.5
    """Multiplier for EVENT_DRIVEN regime."""

    chop_multiplier: float = 1.0
    """Multiplier for CHOP regime (normal risk)."""

    max_bps: float = 200.0
    """Maximum adverse selection cost in bps."""

    def validate(self) -> None:
        for name, val in [
            ("base_bps_per_second", self.base_bps_per_second),
            ("panic_regime_multiplier", self.panic_regime_multiplier),
            ("max_bps", self.max_bps),
        ]:
            if val <= 0:
                raise ValueError(f"{name} must be positive, got {val}")


class AdverseSelectionAdjuster:
    """Estimates adverse selection cost for maker orders.

    Adverse selection is the risk that the price moves against you while
    your limit order is resting. The longer you wait, the higher the risk.

    Cost model:
        cost_bps = base_bps_per_second * volatility_annualized
                 * time_to_fill * regime_multiplier

    The volatility factor captures: higher vol → faster price moves →
    higher risk of adverse fill.
    """

    # Canonical regime names (case-insensitive, matches P9.2 RegimeScaling)
    _REGIME_MULTIPLIERS: dict[str, float] = {
        "trend": 2.0,
        "chop": 1.0,
        "panic": 4.0,
        "illiquid": 1.5,
        "event_driven": 2.5,
        "unknown": 1.0,
    }

    def __init__(
        self,
        config: AdverseSelectionConfig | None = None,
    ):
        cfg = config or AdverseSelectionConfig()
        cfg.validate()
        self._config = cfg
        self._regime_multipliers = {
            "trend": cfg.trend_regime_multiplier,
            "chop": cfg.chop_multiplier,
            "panic": cfg.panic_regime_multiplier,
            "illiquid": cfg.illiquid_regime_multiplier,
            "event_driven": cfg.event_driven_multiplier,
            "unknown": 1.0,
        }

    def estimate_cost(
        self,
        volatility: float | None,
        time_to_fill: float,
        regime: str | None = None,
    ) -> float:
        """Estimate adverse selection cost in basis points.

        Args:
            volatility: Annualized realized volatility (0.0-1.0+). None → 0.
            time_to_fill: Expected time to fill in seconds.
                float('inf') → returns max_bps.
            regime: Market regime label. None → "unknown" (1.0x).

        Returns:
            Adverse selection cost in basis points (non-negative).
        """
        if time_to_fill <= 0:
            return 0.0

        # ── Infinite wait → max cost ──────────────────────────────────
        if time_to_fill == float("inf") or time_to_fill > 3600:
            return self._config.max_bps

        # ── Volatility factor ─────────────────────────────────────────
        vol = volatility if volatility is not None and volatility >= 0 else 0.0
        vol = min(vol, 5.0)  # cap at 500% annualized

        # Annualized vol → per-second vol factor
        # sqrt(1/31536000) ≈ 0.000178 → per-second scaling
        vol_per_second = vol * 0.000178

        # ── Regime factor ────────────────────────────────────────────
        regime_key = self._normalize_regime(regime)
        regime_mult = self._regime_multipliers.get(
            regime_key, self._regime_multipliers["unknown"]
        )

        # ── Compute cost ─────────────────────────────────────────────
        cost_bps = (
            self._config.base_bps_per_second
            * (1.0 + vol_per_second * 100)  # scale vol effect
            * time_to_fill
            * regime_mult
        )

        return min(cost_bps, self._config.max_bps)

    def _normalize_regime(self, regime: str | None) -> str:
        """Normalize regime label to canonical lowercase key."""
        if not regime:
            return "unknown"
        return regime.strip().lower()


# ══════════════════════════════════════════════════════════════════════════
# QUEUE POSITION ENGINE
# ══════════════════════════════════════════════════════════════════════════


class QueuePositionEngine:
    """Unified maker fill estimation composing turnover, position, and
    adverse selection models.

    Composes:
      - QueueTurnoverModel        — taker volume arrival rate
      - QueuePositionModel        — fill probability in wait window
      - AdverseSelectionAdjuster  — cost of waiting

    Usage:
        engine = QueuePositionEngine()
        est = engine.estimate(
            tick_data={"volume_24h": 5000.0, "asks_vol_1": 20000.0},
            order_size=10.0,
            side="entry",
            volatility=0.15,
            regime="CHOP",
        )
        print(f"P(fill)={est.p_fill:.2%}, adverse={est.adverse_selection_bps:.1f}bps")
    """

    def __init__(
        self,
        config: QueuePositionConfig | None = None,
        turnover_model: QueueTurnoverModel | None = None,
        position_model: QueuePositionModel | None = None,
        adverse_adjuster: AdverseSelectionAdjuster | None = None,
    ):
        self._config = config or QueuePositionConfig()
        self._turnover = turnover_model or QueueTurnoverModel(self._config)
        self._position = position_model or QueuePositionModel(self._config)
        self._adverse = adverse_adjuster or AdverseSelectionAdjuster()

    def estimate(
        self,
        tick_data: dict,
        order_size: float,
        side: str = "entry",
        volatility: float | None = None,
        regime: str | None = None,
        wait_time_T: float | None = None,  # noqa: N803
    ) -> QueuePositionEstimate:
        """Estimate fill probability and adverse selection for a maker order.

        Args:
            tick_data: Dict with volume_24h, bids_vol_1..3 or asks_vol_1..3,
                       and spread fields (same as FillSimulator tick_data).
            order_size: Order size in USDC.
            side: "entry" (uses asks_vol_1 depth) or "exit" (uses bids_vol_1).
            volatility: Annualized realized volatility for adverse selection.
            regime: Market regime label (TREND/CHOP/PANIC/ILLIQUID/EVENT_DRIVEN).
            wait_time_T: Override for wait window (defaults to config).

        Returns:
            QueuePositionEstimate with p_fill, expected_time_to_fill,
            and adverse_selection_bps.
        """
        # ── Extract data from tick ────────────────────────────────────
        volume_24h = tick_data.get("volume_24h", 0.0) or 0.0

        if side == "exit":
            l1_depth = tick_data.get("bids_vol_1", 0.0) or 0.0
        else:
            l1_depth = tick_data.get("asks_vol_1", 0.0) or 0.0

        wait_time_t = wait_time_T if wait_time_T is not None else self._config.wait_time_T

        # ── 1. Turnover rate ──────────────────────────────────────────
        volume_sec, turnover_confidence = self._turnover.estimate_volume_per_sec(
            volume_24h
        )

        # ── 2. Fill probability ──────────────────────────────────────
        p_fill, expected_time = self._position.estimate_fill_probability(
            order_size=order_size,
            l1_depth=l1_depth,
            volume_sec=volume_sec,
            wait_time_T=wait_time_t,
        )

        # ── 3. Adverse selection cost ─────────────────────────────────
        adverse_bps = self._adverse.estimate_cost(
            volatility=volatility,
            time_to_fill=expected_time,
            regime=regime,
        )

        # ── 4. Confidence ─────────────────────────────────────────────
        # Confidence degrades when:
        #   - Using fallback volume (turnover_confidence < 1.0)
        #   - Zero L1 depth (orderbook data unavailable)
        #   - Zero volume_24h
        confidence = turnover_confidence
        if l1_depth <= 0 or volume_24h <= 0:
            confidence = min(confidence, 0.5)
        if p_fill < 0.01:
            confidence = min(confidence, 0.3)

        return QueuePositionEstimate(
            p_fill=round(p_fill, 4),
            expected_time_to_fill=round(expected_time, 2),
            adverse_selection_bps=round(adverse_bps, 2),
            confidence=round(confidence, 2),
            wait_time_T=wait_time_T,
            volume_sec=round(volume_sec, 4),
            l1_depth=l1_depth,
            regime=regime or "UNKNOWN",
            volatility=volatility or 0.0,
        )


# ══════════════════════════════════════════════════════════════════════════
# COST COMPARATOR
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class MakerVsTakerDecision:
    """Result of maker-vs-taker cost comparison."""

    mode: str  # "MAKER" or "TAKER"
    """Recommended execution mode."""

    taker_cost: float
    """Estimated taker cost (slippage in price units)."""

    maker_cost: float
    """Estimated maker cost (expected adverse selection, price units)."""

    cost_ratio: float
    """maker_cost / taker_cost. < 1.0 means maker is cheaper."""

    maker_estimate: QueuePositionEstimate
    """The underlying maker fill estimate."""

    reason: str = ""
    """Human-readable explanation of the decision."""

    @property
    def prefer_maker(self) -> bool:
        return self.mode == "MAKER"

    @property
    def savings_pct(self) -> float:
        """Percentage saved by using the recommended mode vs the alternative."""
        return round((1.0 - self.cost_ratio) * 100, 2) if self.prefer_maker else 0.0


class CostComparator:
    """Compares maker vs taker expected costs to recommend execution mode.

    Decision rule:
        delay_penalty = taker_slippage * missed_entry_factor
        maker_cost = (p_fill * adverse_cost)
                    + (1 - p_fill) * (taker_cost + delay_penalty)
        if maker_cost < taker_cost * maker_discount_threshold → MAKER
        else → TAKER
    """

    def __init__(self, config: QueuePositionConfig | None = None):
        self._config = config or QueuePositionConfig()

    def compare(
        self,
        taker_cost: float,
        maker_estimate: QueuePositionEstimate,
    ) -> MakerVsTakerDecision:
        """Compare expected costs and recommend execution mode.

        Args:
            taker_cost: Expected taker slippage cost (absolute price units).
            maker_estimate: Maker fill estimate from QueuePositionEngine.

        Returns:
            MakerVsTakerDecision with recommended mode and cost breakdown.
        """
        cfg = self._config

        # ── Guard: if maker is basically impossible, skip to taker ────
        if maker_estimate.p_fill < 0.01:
            return MakerVsTakerDecision(
                mode="TAKER",
                taker_cost=taker_cost,
                maker_cost=float("inf"),
                cost_ratio=float("inf"),
                maker_estimate=maker_estimate,
                reason=f"P(fill)={maker_estimate.p_fill:.1%} too low",
            )

        # ── Convert adverse selection bps → price units ───────────────
        adverse_cost = maker_estimate.adverse_selection_pct

        # ── Delay penalty: cost of missing the entry ──────────────────
        delay_penalty = taker_cost * cfg.missed_entry_factor

        # ── Expected maker cost ───────────────────────────────────────
        p = maker_estimate.p_fill
        maker_cost = (p * adverse_cost) + ((1.0 - p) * (taker_cost + delay_penalty))

        cost_ratio = maker_cost / max(taker_cost, 1e-10)

        # ── Decision ─────────────────────────────────────────────────
        if cost_ratio < cfg.maker_discount_threshold:
            mode = "MAKER"
            savings = (1.0 - cost_ratio) * 100
            reason = (
                f"Maker cheaper by {savings:.1f}%: "
                f"P(fill)={p:.1%}, "
                f"adverse={maker_estimate.adverse_selection_bps:.1f}bps"
            )
        else:
            mode = "TAKER"
            reason = (
                f"Taker preferred: "
                f"maker_cost/taker_cost={cost_ratio:.3f} >= "
                f"threshold={cfg.maker_discount_threshold}"
            )

        return MakerVsTakerDecision(
            mode=mode,
            taker_cost=round(taker_cost, 6),
            maker_cost=round(maker_cost, 6),
            cost_ratio=round(cost_ratio, 4),
            maker_estimate=maker_estimate,
            reason=reason,
        )
