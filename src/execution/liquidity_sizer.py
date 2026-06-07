# src/execution/liquidity_sizer.py

"""
P11.3 — Liquidity-Aware Trading: automatic position size reduction in low-liquidity markets.

Adapts trading behavior to current market depth conditions by analyzing
orderbook depth, spread, and volume to compute a liquidity_multiplier that
reduces position size when market conditions are thin.

Architecture:
    tick_data, order_size, asset
        │
        ▼
    LiquidityAwareSizer.assess()
        ├─ Depth analysis    → depth_coverage = total_L1_depth / order_size
        ├─ Spread penalty    → spread_factor = f(spread)
        ├─ Volume scarcity   → volume_factor = f(volume_24h)
        └─ Combined          → liquidity_multiplier = depth_factor × spread_factor × volume_factor
        │
        ▼
    LiquidityAssessment (multiplier, recommended_size, reasons)

Integration point:
    TradingService._evaluate_risk_and_execute()
        → before RiskEngine.evaluate(), adjust requested_amount via sizer

Design rationale:
    - P9.1-P9.4 model what happens DURING execution (slippage, fills, routing)
    - P11.3 adjusts what we ATTEMPT to execute (pre-trade sizing)
    - Smaller orders in thin markets → less slippage → better execution quality

Usage:
    sizer = LiquidityAwareSizer()
    assessment = sizer.assess(tick_data=tick_dict, order_size=10.0, asset="ETH")
    adjusted_size = assessment.recommended_size
"""

from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class LiquiditySizerConfig:
    """Configuration for the liquidity-aware position sizer."""

    min_depth_coverage: float = 3.0
    """Ratio of total L1 depth to order_size below which sizing is reduced.
    E.g., 3.0 means we need at least 3x the order size in L1 depth."""

    max_spread: float = 0.05
    """Spread (in price units) above which sizing begins to be penalized."""

    min_volume_24h: float = 500.0
    """24h volume in USDC below which sizing is reduced."""

    size_floor_pct: float = 0.25
    """Minimum fraction of the original order size (never reduce below this)."""

    severe_depth_coverage: float = 1.0
    """Depth coverage below which the maximum reduction is applied."""

    spread_max_penalty: float = 0.70
    """Maximum penalty from spread (multiplier floor for spread component)."""

    volume_max_penalty: float = 0.60
    """Maximum penalty from low volume (multiplier floor for volume component)."""

    def validate(self) -> None:
        if self.min_depth_coverage <= 0:
            raise ValueError(
                f"min_depth_coverage must be > 0, got {self.min_depth_coverage}"
            )
        if not 0.0 < self.size_floor_pct <= 1.0:
            raise ValueError(
                f"size_floor_pct must be in (0, 1], got {self.size_floor_pct}"
            )
        if self.severe_depth_coverage <= 0:
            raise ValueError(
                f"severe_depth_coverage must be > 0, got {self.severe_depth_coverage}"
            )
        if not 0.0 < self.spread_max_penalty <= 1.0:
            raise ValueError(
                f"spread_max_penalty must be in (0, 1], got {self.spread_max_penalty}"
            )
        if not 0.0 < self.volume_max_penalty <= 1.0:
            raise ValueError(
                f"volume_max_penalty must be in (0, 1], got {self.volume_max_penalty}"
            )


# ══════════════════════════════════════════════════════════════════════════
# LIQUIDITY ASSESSMENT
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class LiquidityAssessment:
    """Result of a liquidity analysis for a potential order."""

    liquidity_multiplier: float
    """Combined multiplier (0.0-1.0) to apply to the position size."""

    recommended_size: float
    """Original order_size × liquidity_multiplier, clamped to floor."""

    original_size: float
    """The original requested order size."""

    depth_coverage: float
    """Total L1 depth / order_size. Higher = more liquidity available."""

    depth_factor: float = 1.0
    """Multiplier from depth analysis alone."""

    spread_factor: float = 1.0
    """Multiplier from spread analysis alone."""

    volume_factor: float = 1.0
    """Multiplier from volume analysis alone."""

    spread: float = 0.0
    """Current bid-ask spread."""

    volume_24h: float = 0.0
    """24h volume used for analysis."""

    total_depth: float = 0.0
    """Total L1 depth (bids_vol_1 + asks_vol_1 or appropriate side)."""

    reasons: list[str] = field(default_factory=list)
    """Human-readable reasons for size adjustment."""

    confidence: float = 1.0
    """Confidence in the assessment (degraded when using fallback data)."""

    @property
    def is_reduced(self) -> bool:
        """Whether the size was reduced at all."""
        return self.liquidity_multiplier < 0.99

    @property
    def reduction_pct(self) -> float:
        """Percentage by which the size was reduced (0-100)."""
        return round((1.0 - self.liquidity_multiplier) * 100, 1)

    @property
    def is_severe(self) -> bool:
        """Whether liquidity conditions are severely degraded (multiplier < 50%)."""
        return self.liquidity_multiplier < 0.50


# ══════════════════════════════════════════════════════════════════════════
# LIQUIDITY-AWARE SIZER
# ══════════════════════════════════════════════════════════════════════════


class LiquidityAwareSizer:
    """Analyzes market liquidity and recommends position size adjustments.

    Reduces position size when:
        - Orderbook depth is thin relative to order size (depth_coverage low)
        - Bid-ask spread is wide (expensive to cross)
        - 24h volume is low (market is illiquid)

    The combined liquidity_multiplier is the product of three independent
    factors, each in [0, 1], clamped to the configured floor:

        multiplier = depth_factor × spread_factor × volume_factor
        multiplier = max(floor, min(1.0, multiplier))

    This multiplicative approach ensures that multiple weak signals compound
    (e.g., thin depth + wide spread → very small size), while a single weak
    signal alone only has a moderate effect.
    """

    def __init__(self, config: LiquiditySizerConfig | None = None):
        self._config = config or LiquiditySizerConfig()
        self._config.validate()

    # ── Public API ────────────────────────────────────────────────────────

    def assess(
        self,
        tick_data: dict,
        order_size: float,
        asset: str = "DEFAULT",
        side: str = "entry",
    ) -> LiquidityAssessment:
        """Analyze liquidity and recommend adjusted position size.

        Args:
            tick_data: Dict with depth fields (bids_vol_1..3, asks_vol_1..3),
                       spread, volume_24h, best_bid, best_ask.
            order_size: Intended position size in USDC.
            asset: Asset label (BTC/ETH/DEFAULT) — for context, currently
                   not asset-specific but reserved for future calibration.
            side: "entry" (uses asks_vol for depth) or "exit" (uses bids_vol).

        Returns:
            LiquidityAssessment with liquidity_multiplier, recommended_size,
            and detailed factor breakdown.
        """
        cfg = self._config

        # ── Extract liquidity metrics ──────────────────────────────────
        spread = tick_data.get("spread", 0.0) or 0.0
        volume_24h = tick_data.get("volume_24h", 0.0) or 0.0

        # Total L1 depth depends on side
        if side == "exit":
            total_depth = (tick_data.get("bids_vol_1", 0.0) or 0.0)
        else:
            total_depth = (tick_data.get("asks_vol_1", 0.0) or 0.0)

        # ── Confidence: degrade when using fallback data ───────────────
        confidence = 1.0
        if total_depth <= 0:
            confidence = min(confidence, 0.5)
        if volume_24h <= 0:
            confidence = min(confidence, 0.6)
        if spread <= 0:
            confidence = min(confidence, 0.7)

        # ── Guard: zero or negative order size ─────────────────────────
        if order_size <= 0:
            return LiquidityAssessment(
                liquidity_multiplier=1.0,
                recommended_size=0.0,
                original_size=0.0,
                depth_coverage=float("inf"),
                depth_factor=1.0,
                spread_factor=1.0,
                volume_factor=1.0,
                spread=spread,
                volume_24h=volume_24h,
                total_depth=total_depth,
                reasons=["zero_order_size"],
                confidence=confidence,
            )

        # ── 1. Depth factor ────────────────────────────────────────────
        depth_factor = self._compute_depth_factor(order_size, total_depth)

        # ── 2. Spread factor ───────────────────────────────────────────
        spread_factor = self._compute_spread_factor(spread)

        # ── 3. Volume factor ───────────────────────────────────────────
        volume_factor = self._compute_volume_factor(volume_24h)

        # ── 4. Combined multiplier ─────────────────────────────────────
        raw_multiplier = depth_factor * spread_factor * volume_factor
        liquidity_multiplier = round(
            max(cfg.size_floor_pct, min(1.0, raw_multiplier)), 4
        )

        recommended_size = round(order_size * liquidity_multiplier, 2)

        # ── 5. Reasons ──────────────────────────────────────────────────
        reasons: list[str] = []
        depth_coverage = total_depth / order_size if order_size > 0 else float("inf")

        if depth_factor < 0.99:
            reasons.append(
                f"thin_depth: coverage={depth_coverage:.1f}x "
                f"(need ≥{cfg.min_depth_coverage:.0f}x)"
            )
        if spread_factor < 0.99:
            reasons.append(
                f"wide_spread: spread={spread:.4f} "
                f"(penalty starts at {cfg.max_spread:.4f})"
            )
        if volume_factor < 0.99:
            reasons.append(
                f"low_volume: vol_24h={volume_24h:.0f} USDC "
                f"(min={cfg.min_volume_24h:.0f})"
            )
        if not reasons:
            reasons.append("adequate_liquidity")

        return LiquidityAssessment(
            liquidity_multiplier=liquidity_multiplier,
            recommended_size=recommended_size,
            original_size=order_size,
            depth_coverage=round(depth_coverage, 2),
            depth_factor=round(depth_factor, 4),
            spread_factor=round(spread_factor, 4),
            volume_factor=round(volume_factor, 4),
            spread=spread,
            volume_24h=volume_24h,
            total_depth=total_depth,
            reasons=reasons,
            confidence=round(confidence, 2),
        )

    def compute_multiplier(
        self,
        tick_data: dict,
        order_size: float,
        side: str = "entry",
    ) -> float:
        """Convenience: return only the liquidity_multiplier.

        Args:
            tick_data: Orderbook tick dict.
            order_size: Intended position size in USDC.
            side: "entry" or "exit".

        Returns:
            liquidity_multiplier in [size_floor_pct, 1.0].
        """
        return self.assess(tick_data, order_size, side=side).liquidity_multiplier

    # ── Internal ──────────────────────────────────────────────────────────

    def _compute_depth_factor(
        self, order_size: float, total_depth: float
    ) -> float:
        """Compute depth factor based on L1 depth vs order size.

        Linear interpolation:
        - coverage ≥ min_depth_coverage → 1.0 (no reduction)
        - coverage = severe_depth_coverage → floor (max reduction)
        - coverage between → linear interpolation
        """
        cfg = self._config

        if total_depth <= 0 or order_size <= 0:
            return cfg.size_floor_pct

        coverage = total_depth / order_size

        if coverage >= cfg.min_depth_coverage:
            return 1.0

        if coverage <= cfg.severe_depth_coverage:
            return cfg.size_floor_pct

        # Linear interpolation between severe and min thresholds
        ratio = (coverage - cfg.severe_depth_coverage) / (
            cfg.min_depth_coverage - cfg.severe_depth_coverage
        )
        return round(cfg.size_floor_pct + ratio * (1.0 - cfg.size_floor_pct), 4)

    def _compute_spread_factor(self, spread: float) -> float:
        """Compute spread factor.

        Spread above max_spread is penalized linearly down to spread_max_penalty.
        """
        cfg = self._config

        if spread <= cfg.max_spread:
            return 1.0

        # Excess spread scaled: at 2× max_spread → max penalty
        excess = (spread - cfg.max_spread) / cfg.max_spread
        penalty = min(1.0, excess)  # cap at 1.0 (full penalty)
        return round(1.0 - penalty * (1.0 - cfg.spread_max_penalty), 4)

    def _compute_volume_factor(self, volume_24h: float) -> float:
        """Compute volume factor.

        Volume below min_volume_24h is penalized linearly down to volume_max_penalty.
        """
        cfg = self._config

        if volume_24h >= cfg.min_volume_24h:
            return 1.0

        if volume_24h <= 0:
            return cfg.volume_max_penalty

        # Linear interpolation from 0 to min_volume_24h
        ratio = volume_24h / cfg.min_volume_24h
        return round(cfg.volume_max_penalty + ratio * (1.0 - cfg.volume_max_penalty), 4)
