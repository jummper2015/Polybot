"""
P9.2 -- Slippage Engine: dynamic execution cost estimation.

Composes the static FillSimulator (P9.1) with runtime factors:
  - VolatilityAdjuster  -- scales slippage by realized volatility
  - RegimeScaling        -- adjusts slippage per market regime
  - SlippageTracker      -- rolling window of expected vs actual fills
  - QueuePositionEngine  -- P9.3 maker fill probability (via estimate_maker)
  - CostComparator       -- P9.3 maker-vs-taker cost optimization

Architecture:
    tick_data, volatility, regime, order_size, asset
        |
        v
    SlippageEngine.estimate()              -> SlippageEstimate (TAKER)
        +- FillSimulator.estimate_entry/exit()  -> FillEstimate (base)
        +- VolatilityAdjuster.apply(vol)        -> vol_multiplier
        +- RegimeScaling.apply(regime)          -> regime_multiplier

    SlippageEngine.estimate_maker()        -> QueuePositionEstimate (MAKER)
        +- QueueTurnoverModel                  -> volume_sec
        +- QueuePositionModel                  -> P(fill)
        +- AdverseSelectionAdjuster            -> adverse_selection_bps

    SlippageEngine.compare_maker_vs_taker() -> MakerVsTakerDecision
"""

from collections import deque
from dataclasses import dataclass

import structlog

from src.execution.fill_simulator import FillEstimate, FillSimulator
from src.execution.queue_position import (
    CostComparator,
    MakerVsTakerDecision,
    QueuePositionEngine,
    QueuePositionEstimate,
)

logger = structlog.get_logger(__name__)


# ==========================================================================
# CLOB V2 FEE EXTRACTOR
# ==========================================================================


def taker_fee_bps_from_market_info(market_info: dict | None) -> float:
    """Extrae el taker-fee en bps desde la respuesta de get_clob_market_info.

    CLOB V2 estructura los fees por mercado en `market_info["fd"]`:
      {"r": <numerador>, "e": <exponente>, "to": <takerOnly bool>}
    El fee efectivo del taker en formato decimal es r / 10^e.
    Lo convertimos a bps multiplicando por 10_000.

    Si `to=False`, en V2 maker y taker pagan; tratamos `r` como fee de taker
    igualmente (es el lado que ejecuta inmediato). Si falta `fd` o las claves
    no son numéricas, devolvemos 0.0 con log DEBUG — fail-safe: nunca subimos
    una estimación por un fee que no pudimos parsear.
    """
    if not market_info or not isinstance(market_info, dict):
        return 0.0
    fd = market_info.get("fd")
    if not fd or not isinstance(fd, dict):
        logger.debug("taker_fee_no_fd", reason="missing fd in market_info")
        return 0.0
    try:
        rate = float(fd["r"])
        exponent = int(fd["e"])
    except (KeyError, TypeError, ValueError):
        logger.debug("taker_fee_parse_error", fd_keys=list(fd.keys()))
        return 0.0
    if exponent < 0 or exponent > 18:
        # Fuera de rango razonable — protegemos contra valores corruptos.
        logger.debug("taker_fee_exponent_out_of_range", exponent=exponent)
        return 0.0
    fee_decimal = rate / (10 ** exponent)
    return fee_decimal * 10_000.0


# ==========================================================================
# SLIPPAGE ESTIMATE
# ==========================================================================


@dataclass
class SlippageEstimate:
    """Complete slippage estimate with base model + runtime adjustments."""

    # -- Base (from FillSimulator) --------------------------------------
    base_estimate: FillEstimate
    """Raw FillSimulator estimate before adjustments."""

    fill_price: float
    """Estimated fill price (base)."""

    slippage: float
    """Base slippage from mid price."""

    slippage_pct: float
    """Base slippage as percentage of mid."""

    fill_ratio: float
    """Expected fill ratio (1.0 = full)."""

    # -- Runtime multipliers --------------------------------------------
    vol_multiplier: float = 1.0
    """Multiplier applied for volatility conditions."""

    regime_multiplier: float = 1.0
    """Multiplier applied for market regime."""

    # -- Adjusted outputs -----------------------------------------------
    adjusted_slippage: float = 0.0
    """Slippage after all adjustments applied (base * vol * regime)."""

    adjusted_p50_slippage: float = 0.0
    """Adjusted median (P50) slippage."""

    adjusted_p95_slippage: float = 0.0
    """Adjusted P95 slippage."""

    adjusted_p99_slippage: float = 0.0
    """Adjusted P99 slippage."""

    # -- Diagnostics ----------------------------------------------------
    calibration_multiplier: float = 1.0
    """Multiplier from SlippageTracker (1.0 = no adjustment)."""

    regime: str = "UNKNOWN"
    """Regime at time of estimate."""

    volatility: float = 0.0
    """Volatility at time of estimate."""

    taker_fee_bps: float = 0.0
    """Taker fee from CLOB V2 market info (bps). 0.0 = no fee applied."""

    taker_fee_price: float = 0.0
    """Taker fee converted to price units (taker_fee_bps / 10_000 * mid)."""

    @property
    def effective_price(self) -> float:
        """Weighted fill price accounting for partial fills."""
        return self.base_estimate.effective_price

    @property
    def total_multiplier(self) -> float:
        """Combined multiplier: vol * regime * calibration."""
        return self.vol_multiplier * self.regime_multiplier * self.calibration_multiplier

    @property
    def adjusted_fill_price(self) -> float:
        """Fill price adjusted by all multipliers.

        Sign is already preserved in adjusted_slippage
        (positive for entry, negative for exit).
        Clamped to [0.001, 0.999].
        """
        mid = self.base_estimate.mid_price
        if mid <= 0:
            return self.fill_price
        raw = mid + self.adjusted_slippage
        return round(max(0.001, min(0.999, raw)), 6)

    @property
    def is_full_fill(self) -> bool:
        return self.fill_ratio >= 0.99


# ==========================================================================
# VOLATILITY ADJUSTER
# ==========================================================================


@dataclass
class VolatilityConfig:
    """Configuration for volatility-based slippage adjustment."""

    low_vol_threshold: float = 0.05
    """Annualized volatility below which slippage is reduced."""

    high_vol_threshold: float = 0.30
    """Annualized volatility above which slippage is increased."""

    low_vol_multiplier: float = 0.7
    """Slippage multiplier in low-vol environments."""

    high_vol_multiplier: float = 2.0
    """Slippage multiplier in high-vol environments."""

    max_multiplier: float = 5.0
    """Absolute cap on the volatility multiplier."""

    def validate(self) -> None:
        if self.low_vol_threshold >= self.high_vol_threshold:
            raise ValueError(
                f"low_vol_threshold ({self.low_vol_threshold}) must be "
                f"< high_vol_threshold ({self.high_vol_threshold})"
            )
        if self.low_vol_multiplier <= 0 or self.high_vol_multiplier <= 0:
            raise ValueError("Multipliers must be positive")


class VolatilityAdjuster:
    """Adjusts slippage based on realized volatility.

    Low vol (tight ranges) -> slips less (maker-friendly).
    High vol (panic, trend) -> slips more (taker-heavy, wide spreads).
    """

    def __init__(self, config: VolatilityConfig | None = None):
        self._config = config or VolatilityConfig()
        self._config.validate()

    def apply(self, volatility: float | None) -> float:
        """Compute volatility multiplier for slippage.

        Args:
            volatility: Annualized realized volatility (0.0-1.0+).
                        None, 0.0, or negative -> default multiplier of 1.0.

        Returns:
            Multiplier to apply to base slippage.
        """
        if volatility is None or volatility <= 0 or volatility > 100:
            return 1.0

        cfg = self._config

        if volatility <= cfg.low_vol_threshold:
            return cfg.low_vol_multiplier

        if volatility >= cfg.high_vol_threshold:
            # Linear interpolation beyond threshold, capped at max
            excess = (volatility - cfg.high_vol_threshold) / cfg.high_vol_threshold
            return min(cfg.high_vol_multiplier + excess, cfg.max_multiplier)

        # Linear between low and high thresholds
        ratio = (volatility - cfg.low_vol_threshold) / (
            cfg.high_vol_threshold - cfg.low_vol_threshold
        )
        return cfg.low_vol_multiplier + ratio * (
            cfg.high_vol_multiplier - cfg.low_vol_multiplier
        )


# ==========================================================================
# REGIME SCALING
# ==========================================================================


@dataclass
class RegimeScalingConfig:
    """Multipliers for slippage per market regime."""

    chop_multiplier: float = 1.0
    """Range-bound -> normal slippage."""

    trend_multiplier: float = 1.2
    """Trending -> moderately wider spreads."""

    panic_multiplier: float = 2.0
    """High volatility panic -> much wider spreads, thin books."""

    illiquid_multiplier: float = 1.5
    """Illiquid -> wider spreads, less depth."""

    event_driven_multiplier: float = 1.3
    """Event-driven -> directional pressure, some spread widening."""

    unknown_multiplier: float = 1.0
    """Unknown regime -> no adjustment."""

    def validate(self) -> None:
        for name in (
            "chop_multiplier", "trend_multiplier", "panic_multiplier",
            "illiquid_multiplier", "event_driven_multiplier",
            "unknown_multiplier",
        ):
            val = getattr(self, name)
            if val <= 0:
                raise ValueError(f"{name} must be positive, got {val}")


class RegimeScaling:
    """Maps market regime -> slippage multiplier.

    Defaults calibrated to prediction-market behavior:
    - PANIC has widest spreads, thinnest depth -> 2x
    - ILLIQUID has poor depth -> 1.5x
    - TREND has moderate pressure -> 1.2x
    - CHOP is normal -> 1.0x
    """

    # Canonical regime names from src/backtesting/regime.py
    REGIME_MAP: dict[str, str] = {
        # Lowercase variants
        "trend": "trend",
        "chop": "chop",
        "panic": "panic",
        "illiquid": "illiquid",
        "event_driven": "event_driven",
        # Uppercase variants (from RegimeClassifier)
        "TREND": "trend",
        "CHOP": "chop",
        "PANIC": "panic",
        "ILLIQUID": "illiquid",
        "EVENT_DRIVEN": "event_driven",
    }

    def __init__(self, config: RegimeScalingConfig | None = None):
        self._config = config or RegimeScalingConfig()
        self._config.validate()

        self._multipliers: dict[str, float] = {
            "trend": self._config.trend_multiplier,
            "chop": self._config.chop_multiplier,
            "panic": self._config.panic_multiplier,
            "illiquid": self._config.illiquid_multiplier,
            "event_driven": self._config.event_driven_multiplier,
            "unknown": self._config.unknown_multiplier,
        }

    def apply(self, regime: str | None) -> float:
        """Compute regime multiplier.

        Args:
            regime: Regime label (case-insensitive).
                    None or empty -> 1.0 (no adjustment).

        Returns:
            Slippage multiplier for this regime.
        """
        if not regime:
            return 1.0

        key = self.REGIME_MAP.get(regime, regime.lower() if regime else "unknown")
        return self._multipliers.get(key, self._multipliers["unknown"])


# ==========================================================================
# SLIPPAGE TRACKER
# ==========================================================================


@dataclass
class SlippageTrackerConfig:
    """Configuration for the slippage tracker."""

    window_size: int = 20
    """Number of recent fills to track."""

    overestimate_threshold: float = 0.90
    """Below this ratio -> model overestimates slippage (tighten)."""

    underestimate_threshold: float = 1.10
    """Above this ratio -> model underestimates slippage (widen)."""

    adjustment_step: float = 0.05
    """How much to adjust calibration_multiplier per observation."""

    min_multiplier: float = 0.50
    """Floor for calibration_multiplier."""

    max_multiplier: float = 3.00
    """Ceiling for calibration_multiplier."""


class SlippageTracker:
    """Tracks expected vs actual slippage over a rolling window.

    Maintains a calibration_multiplier that nudges the engine:
    - Observed > expected -> increase multiplier (model too optimistic)
    - Observed < expected -> decrease multiplier (model too pessimistic)
    """

    def __init__(self, config: SlippageTrackerConfig | None = None):
        self._config = config or SlippageTrackerConfig()
        self._window: deque[tuple[float, float]] = deque(
            maxlen=self._config.window_size
        )
        self._calibration_multiplier: float = 1.0

    def record(
        self,
        expected_slippage: float,
        actual_slippage: float,
    ) -> None:
        """Record an expected-vs-actual slippage pair.

        Args:
            expected_slippage: Slip estimate from the engine (absolute value).
            actual_slippage: Realized slippage from the fill (absolute value).
        """
        if expected_slippage <= 0:
            return

        self._window.append((expected_slippage, actual_slippage))
        self._update_calibration()

    def _update_calibration(self) -> None:
        """Nudge calibration_multiplier based on rolling bias."""
        if len(self._window) < 3:
            return  # Not enough data yet

        ratios = []
        for expected, actual in self._window:
            if expected > 0:
                ratios.append(actual / expected)

        if not ratios:
            return

        mean_ratio = sum(ratios) / len(ratios)
        cfg = self._config

        if mean_ratio > cfg.underestimate_threshold:
            # Model underestimates -> widen
            self._calibration_multiplier = min(
                self._calibration_multiplier + cfg.adjustment_step,
                cfg.max_multiplier,
            )
        elif mean_ratio < cfg.overestimate_threshold:
            # Model overestimates -> tighten
            self._calibration_multiplier = max(
                self._calibration_multiplier - cfg.adjustment_step,
                cfg.min_multiplier,
            )
        # else: within [0.9, 1.1] -> no adjustment needed

    @property
    def calibration_multiplier(self) -> float:
        """Current calibration multiplier (1.0 = model is accurate)."""
        return self._calibration_multiplier

    @property
    def window_size(self) -> int:
        """Number of recorded fills."""
        return len(self._window)

    def get_stats(self) -> dict:
        """Get tracking statistics for monitoring."""
        if not self._window:
            return {
                "samples": 0,
                "mean_ratio": None,
                "calibration_multiplier": self._calibration_multiplier,
            }

        ratios = [
            a / e for e, a in self._window if e > 0
        ]

        return {
            "samples": len(self._window),
            "mean_ratio": round(sum(ratios) / len(ratios), 4) if ratios else None,
            "min_ratio": round(min(ratios), 4) if ratios else None,
            "max_ratio": round(max(ratios), 4) if ratios else None,
            "calibration_multiplier": round(self._calibration_multiplier, 4),
        }


# ==========================================================================
# SLIPPAGE ENGINE
# ==========================================================================


class SlippageEngine:
    """Unified slippage estimation composing static + dynamic factors.

    Composes:
      - FillSimulator (P9.1) -- base fill model
      - VolatilityAdjuster     -- volatility scaling
      - RegimeScaling          -- regime-based scaling
      - SlippageTracker        -- learning from actual fills
      - QueuePositionEngine    -- P9.3 maker fill probability
      - CostComparator         -- P9.3 maker-vs-taker optimizer

    Usage:
        engine = SlippageEngine()
        est = engine.estimate(tick_data, 10.0, "BTC", volatility=0.12, regime="CHOP")
        print(f"Adjusted slippage: {est.adjusted_slippage:.6f}")

        # P9.3: maker fill estimation
        maker = engine.estimate_maker(tick_data, 10.0, "BTC", volatility=0.12, regime="CHOP")
        print(f"Maker P(fill)={maker.p_fill:.2%}")

        # P9.3: auto-mode decision
        decision = engine.compare_maker_vs_taker(taker_cost=est.adjusted_slippage, maker=maker)
        print(f"Mode: {decision.mode}, Savings: {decision.savings_pct:.1f}%")

        # After fill:
        engine.record_actual(est, actual_fill_price=0.734)
    """

    def __init__(
        self,
        fill_simulator: FillSimulator | None = None,
        vol_adjuster: VolatilityAdjuster | None = None,
        regime_scaling: RegimeScaling | None = None,
        slippage_tracker: SlippageTracker | None = None,
        queue_engine: QueuePositionEngine | None = None,
        cost_comparator: CostComparator | None = None,
    ):
        self._fill_sim = fill_simulator or FillSimulator()
        self._vol_adj = vol_adjuster or VolatilityAdjuster()
        self._regime = regime_scaling or RegimeScaling()
        self._tracker = slippage_tracker or SlippageTracker()
        self._queue = queue_engine or QueuePositionEngine()
        self._cost_cmp = cost_comparator or CostComparator()

    # -- Public API ----------------------------------------------------

    def estimate(
        self,
        tick_data: dict,
        order_size: float,
        asset: str = "DEFAULT",
        side: str = "entry",
        volatility: float | None = None,
        regime: str | None = None,
        taker_fee_bps: float = 0.0,
    ) -> SlippageEstimate:
        """Estimate slippage for an order with all runtime factors.

        Args:
            tick_data: FillSimulator-compatible tick dict.
            order_size: Order size (entry) or position value (exit) in USDC.
            asset: BTC, ETH, or DEFAULT.
            side: "entry" (buy) or "exit" (sell). Routes to FillSimulator.
            volatility: Annualized realized volatility (0.0-1.0+). None -> no adjustment.
            regime: Market regime label (TREND/CHOP/PANIC/ILLIQUID/EVENT_DRIVEN).
                    None -> no adjustment.
            taker_fee_bps: CLOB V2 taker fee in bps for this market (default 0.0 =
                no fee adjustment, preserves legacy behavior). Use
                taker_fee_bps_from_market_info(info) to extract from
                ClobClient.get_market_info_cached(condition_id).

        Returns:
            SlippageEstimate with base + adjusted values.
        """
        # -- 1. Base estimate from FillSimulator ----------------------
        if side == "exit":
            base = self._fill_sim.estimate_exit(
                tick_data=tick_data,
                position_value=order_size,
                asset=asset,
            )
        else:
            base = self._fill_sim.estimate_entry(
                tick_data=tick_data,
                order_size=order_size,
                asset=asset,
            )

        # -- 2. Runtime multipliers -----------------------------------
        vol_mult = self._vol_adj.apply(volatility)
        regime_mult = self._regime.apply(regime)
        cal_mult = self._tracker.calibration_multiplier

        combined = vol_mult * regime_mult * cal_mult

        # -- 3. CLOB V2 taker fee (additive, not multiplicative) ------
        # El fee se cobra en el momento de la ejecución como una resta directa
        # sobre el precio efectivo del taker. Se suma a la magnitud del slippage
        # ajustado preservando el signo (positivo en entry, negativo en exit).
        mid = base.mid_price if base.mid_price > 0 else 0.0
        fee_price = (taker_fee_bps / 10_000.0) * mid if taker_fee_bps > 0 else 0.0
        sign = 1.0 if base.slippage >= 0 else -1.0
        fee_signed = sign * fee_price

        return SlippageEstimate(
            base_estimate=base,
            fill_price=base.fill_price,
            slippage=base.slippage,
            slippage_pct=base.slippage_pct,
            fill_ratio=base.fill_ratio,
            vol_multiplier=vol_mult,
            regime_multiplier=regime_mult,
            adjusted_slippage=round(base.slippage * combined + fee_signed, 6),
            adjusted_p50_slippage=round(base.p50_slippage * combined + fee_signed, 6),
            adjusted_p95_slippage=round(base.p95_slippage * combined + fee_signed, 6),
            adjusted_p99_slippage=round(base.p99_slippage * combined + fee_signed, 6),
            calibration_multiplier=cal_mult,
            regime=regime or "UNKNOWN",
            volatility=volatility or 0.0,
            taker_fee_bps=round(taker_fee_bps, 4),
            taker_fee_price=round(fee_price, 6),
        )

    def record_actual(
        self,
        estimate: SlippageEstimate,
        actual_fill_price: float,
    ) -> None:
        """Record an actual fill to calibrate the tracker.

        Compares the ADJUSTED expected slippage (with vol/regime) vs actual,
        so the tracker captures residual model error, not effects already
        accounted for by volatility and regime adjustments.

        Args:
            estimate: The estimate returned by self.estimate().
            actual_fill_price: The observed fill price from execution.
        """
        mid = estimate.base_estimate.mid_price
        if mid <= 0:
            return

        expected_abs = abs(estimate.adjusted_slippage)
        actual_abs = abs(actual_fill_price - mid)

        self._tracker.record(
            expected_slippage=expected_abs,
            actual_slippage=actual_abs,
        )

        logger.debug(
            "slippage_tracker_recorded",
            expected=round(expected_abs, 6),
            actual=round(actual_abs, 6),
            calibration=self._tracker.calibration_multiplier,
            samples=self._tracker.window_size,
        )

    # -- P9.3: Maker Fill Estimation -----------------------------------

    def estimate_maker(
        self,
        tick_data: dict,
        order_size: float,
        asset: str = "DEFAULT",
        side: str = "entry",
        volatility: float | None = None,
        regime: str | None = None,
        wait_time_T: float | None = None,  # noqa: N803
    ) -> QueuePositionEstimate:
        """Estimate fill probability for a MAKER (limit) order.

        Models the probability of fill within a wait window, accounting
        for queue position, taker volume arrival rate, and adverse
        selection risk.

        Args:
            tick_data: FillSimulator-compatible tick dict with volume_24h
                       and depth fields (bids_vol_1..3 / asks_vol_1..3).
            order_size: Order size in USDC.
            asset: BTC, ETH, or DEFAULT (currently not used by maker model).
            side: "entry" (buy) or "exit" (sell).
            volatility: Annualized realized volatility for adverse selection.
            regime: Market regime for adverse selection scaling.
            wait_time_T: Override for wait window (default from config, 30s).

        Returns:
            QueuePositionEstimate with p_fill, expected_time_to_fill,
            and adverse_selection_bps.
        """
        return self._queue.estimate(
            tick_data=tick_data,
            order_size=order_size,
            side=side,
            volatility=volatility,
            regime=regime,
            wait_time_T=wait_time_T,
        )

    def compare_maker_vs_taker(
        self,
        taker_cost: float,
        maker_estimate: QueuePositionEstimate,
    ) -> MakerVsTakerDecision:
        """Compare maker vs taker expected costs and recommend execution mode.

        Computes expected maker cost = (p_fill * adverse_cost) +
        (1-p_fill) * (taker_cost + delay_penalty), and recommends
        MAKER if it's at least 5% cheaper than TAKER.

        Args:
            taker_cost: Expected taker cost (slippage in price units).
            maker_estimate: Maker fill estimate from estimate_maker().

        Returns:
            MakerVsTakerDecision with recommended mode and cost breakdown.
        """
        return self._cost_cmp.compare(
            taker_cost=abs(taker_cost),
            maker_estimate=maker_estimate,
        )

    # -- Diagnostics ---------------------------------------------------

    def get_tracker_stats(self) -> dict:
        """Get slippage tracker statistics for monitoring."""
        return self._tracker.get_stats()

    @property
    def fill_simulator(self) -> FillSimulator:
        """Access the underlying FillSimulator."""
        return self._fill_sim

    @property
    def calibration_multiplier(self) -> float:
        """Current tracker calibration multiplier."""
        return self._tracker.calibration_multiplier

    @property
    def queue_engine(self) -> QueuePositionEngine:
        """Access the underlying QueuePositionEngine (P9.3)."""
        return self._queue

    @property
    def cost_comparator(self) -> CostComparator:
        """Access the underlying CostComparator (P9.3)."""
        return self._cost_cmp
