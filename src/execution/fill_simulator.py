# src/execution/fill_simulator.py

"""
Realistic fill simulation for paper and backtesting (P9.1).

Models slippage and fill probability using orderbook depth data
collected from real market recording (Parquet ticks).

Architecture:
    Tick data → FillSimulator → FillEstimate
      (spread,      ├─ SlippageModel (price impact + spread crossing)
       depth,       └─ FillProbability (partial fill risk)
       volume)

Key differences from the legacy spread*0.5 model:
    - Price impact is a function of order SIZE vs available DEPTH
    - Partial fills modeled probabilistically based on liquidity
    - Different markets have different depth profiles (BTC vs ETH)
    - Calibrated to real Parquet data, not hardcoded constants

Usage:
    simulator = FillSimulator()
    estimate = simulator.estimate_entry(
        tick_data=tick_dict,
        order_size=10.0,    # USDC
        asset="BTC",
    )
    print(f"Fill: {estimate.fill_price:.4f}, Slippage: {estimate.slippage:.6f}")

Attributes:
    FillEstimate.fill_ratio — 1.0 = full fill, <1.0 = partial
    FillEstimate.partial_fill_prob — probability of NOT getting a full fill
    FillEstimate.p50_slippage — median expected slippage
    FillEstimate.p95_slippage — worst-case (95th percentile) slippage
"""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class FillEstimate:
    """Result of a fill simulation for a single order."""

    fill_price: float
    """Estimated fill price (best guess)."""

    slippage: float
    """Slippage from mid price (absolute; positive = adverse, negative = favorable)."""

    slippage_pct: float
    """Slippage as percentage of mid price."""

    fill_ratio: float = 1.0
    """Expected fill ratio: 1.0 = full fill, 0.5 = half filled."""

    p50_slippage: float = 0.0
    """Median expected adverse slippage (always non-negative)."""

    p95_slippage: float = 0.0
    """Worst-case adverse slippage at 95th percentile (non-negative)."""

    p99_slippage: float = 0.0
    """Extreme adverse slippage at 99th percentile (non-negative)."""

    levels_consumed: int = 0
    """Number of orderbook depth levels consumed by this order."""

    mid_price: float = 0.0
    """Mid price at time of estimation."""

    @property
    def partial_fill_prob(self) -> float:
        """Probability of NOT getting a full fill (0.0-1.0)."""
        return round(1.0 - self.fill_ratio, 4)

    @property
    def effective_price(self) -> float:
        """Weighted average fill price accounting for partial fills."""
        return self.fill_price * self.fill_ratio + self.mid_price * (1 - self.fill_ratio)

    @property
    def is_full_fill(self) -> bool:
        """Whether this order is expected to fill completely."""
        return self.fill_ratio >= 0.99


@dataclass
class MarketDepthProfile:
    """Calibrated depth parameters for a specific asset."""

    asset: str
    spread_cross_factor: float = 0.5
    """Fraction of spread lost crossing bid-ask (0.0-1.0)."""

    base_impact_bps: float = 1.0
    """Base price impact in basis points (1 bps = 0.0001)."""

    min_fill_ratio: float = 0.1
    """Minimum fill ratio even for very large orders."""


# ── Default profiles calibrated from real Parquet data ────────────────────────
# BTC: tight spread (0.001), deep L1 (36K), L2 drops to ~5.5K
# ETH: wider spread (0.029), moderate L1 (11K), L2 ~7.2K

DEFAULT_PROFILES: dict[str, MarketDepthProfile] = {
    "BTC": MarketDepthProfile(
        asset="BTC",
        spread_cross_factor=0.5,
        base_impact_bps=0.5,     # tighter impact due to deep liquidity
        min_fill_ratio=0.2,
    ),
    "ETH": MarketDepthProfile(
        asset="ETH",
        spread_cross_factor=0.5,
        base_impact_bps=2.0,     # wider impact (thinner books)
        min_fill_ratio=0.15,
    ),
    "DEFAULT": MarketDepthProfile(
        asset="DEFAULT",
        spread_cross_factor=0.5,
        base_impact_bps=1.5,
        min_fill_ratio=0.1,
    ),
}


# ── Fill Simulator ────────────────────────────────────────────────────────────

class FillSimulator:
    """
    Realistic fill simulation using orderbook depth.

    Models two components:
    1. Slippage = spread_cross + price_impact(order_size, depth)
    2. Fill probability = f(order_size, available liquidity)

    The price impact model is a linear function of order size relative
    to available depth at each level. Large orders consume multiple
    levels and incur progressively more slippage.
    """

    def __init__(
        self,
        profiles: dict[str, MarketDepthProfile] | None = None,
    ):
        self._profiles = profiles or DEFAULT_PROFILES

    # ── Public API ────────────────────────────────────────────────────────

    def estimate_entry(
        self,
        tick_data: dict,
        order_size: float,
        asset: str = "DEFAULT",
    ) -> FillEstimate:
        """
        Estimate fill for a BUY order (entry).

        For BUY_YES: we buy at the ask side, crossing the spread upward.
        Consumes asks_vol depth levels.

        Args:
            tick_data: Dict with best_bid, best_ask, spread,
                       bids_vol_1..3, asks_vol_1..3, volume_24h
            order_size: Order size in USDC
            asset: BTC, ETH, or DEFAULT for profile selection
        """
        profile = self._get_profile(asset)
        mid = (tick_data.get("best_bid", 0) + tick_data.get("best_ask", 0)) / 2
        spread = tick_data.get("spread", 0.01)

        depth_levels = [
            tick_data.get("asks_vol_1", 0) or 0,
            tick_data.get("asks_vol_2", 0) or 0,
            tick_data.get("asks_vol_3", 0) or 0,
        ]

        return self._estimate(
            side="entry",
            mid_price=mid,
            spread=spread,
            best_price=tick_data.get("best_ask", mid + spread / 2),
            depth_levels=depth_levels,
            order_size=order_size,
            profile=profile,
        )

    def estimate_exit(
        self,
        tick_data: dict,
        position_value: float,
        asset: str = "DEFAULT",
    ) -> FillEstimate:
        """
        Estimate fill for a SELL order (exit).

        For SELL_YES: we sell at the bid side, incurring negative slippage.
        Consumes bids_vol depth levels.

        Args:
            tick_data: Dict with best_bid, best_ask, spread,
                       bids_vol_1..3, asks_vol_1..3, volume_24h
            position_value: Position value in USDC to exit
            asset: BTC, ETH, or DEFAULT
        """
        profile = self._get_profile(asset)
        mid = (tick_data.get("best_bid", 0) + tick_data.get("best_ask", 0)) / 2
        spread = tick_data.get("spread", 0.01)

        depth_levels = [
            tick_data.get("bids_vol_1", 0) or 0,
            tick_data.get("bids_vol_2", 0) or 0,
            tick_data.get("bids_vol_3", 0) or 0,
        ]

        return self._estimate(
            side="exit",
            mid_price=mid,
            spread=spread,
            best_price=tick_data.get("best_bid", mid - spread / 2),
            depth_levels=depth_levels,
            order_size=position_value,
            profile=profile,
        )

    # ── Internal ──────────────────────────────────────────────────────────

    def _estimate(
        self,
        side: str,
        mid_price: float,
        spread: float,
        best_price: float,
        depth_levels: list[float],
        order_size: float,
        profile: MarketDepthProfile,
    ) -> FillEstimate:
        """
        Core fill estimation logic shared by entry and exit.

        Algorithm:
        1. Calculate spread crossing cost (base slippage)
        2. Calculate price impact from consuming depth levels
        3. Calculate partial fill probability
        4. Return FillEstimate with P50/P95/P99 distributions
        """
        # ── Guard: handle zero/negative prices ──────────────────────────
        if mid_price <= 0:
            mid_price = 0.5
        if spread <= 0:
            spread = 0.001
        if best_price <= 0:
            if side == "entry":
                best_price = mid_price + spread * 0.5
            else:
                best_price = mid_price - spread * 0.5

        # ── 1. Spread crossing cost ────────────────────────────────────
        spread_cross_slippage = spread * profile.spread_cross_factor

        # ── 2. Price impact from depth consumption ──────────────────────
        remaining = order_size
        levels_consumed = 0
        cumulative_impact = 0.0

        for i, depth in enumerate(depth_levels):
            if depth <= 0:
                continue
            if remaining <= 0:
                break

            level_fill = min(remaining, depth)
            remaining -= level_fill

            # Impact scales with level depth:
            # Level 1 (best price) → multiplier 1
            # Level 2 (worse price) → multiplier 2
            # Level 3 (worst price) → multiplier 3
            level_multiplier = i + 1
            impact_bps = (
                profile.base_impact_bps
                * level_multiplier
                * (level_fill / max(depth, 1))
            )
            cumulative_impact += impact_bps
            levels_consumed += 1

        # Convert basis points to absolute price impact
        price_impact = mid_price * cumulative_impact / 10000.0

        # ── 3. Total slippage ───────────────────────────────────────────
        if side == "entry":
            total_slippage = spread_cross_slippage + price_impact
        else:
            total_slippage = -(spread_cross_slippage + price_impact)

        fill_price = mid_price + total_slippage
        fill_price = max(0.001, min(0.999, fill_price))

        # ── 4. Fill ratio ───────────────────────────────────────────────
        total_depth = sum(d for d in depth_levels if d > 0)
        if total_depth > 0 and order_size > 0:
            depth_coverage = total_depth / order_size
            # 2x depth → ~75%, 10x depth → ~100%
            fill_ratio = max(
                profile.min_fill_ratio,
                min(1.0, 0.5 + 0.5 * min(1.0, depth_coverage / 2.0)),
            )
        else:
            fill_ratio = 1.0

        # ── 5. Slippage distributions (P50/P95/P99) ────────────────────
        abs_slippage = abs(total_slippage)
        depth_stress = order_size / max(total_depth, 1)
        tail_multiplier = 1.0 + min(3.0, depth_stress)

        p50_slippage = abs_slippage
        p95_slippage = abs_slippage * tail_multiplier
        p99_slippage = p95_slippage * 1.5

        slippage_pct = abs_slippage / mid_price if mid_price > 0 else 0.0

        return FillEstimate(
            fill_price=round(fill_price, 6),
            slippage=round(total_slippage, 6),
            slippage_pct=round(slippage_pct, 6),
            fill_ratio=round(fill_ratio, 4),
            p50_slippage=round(p50_slippage, 6),
            p95_slippage=round(p95_slippage, 6),
            p99_slippage=round(p99_slippage, 6),
            levels_consumed=levels_consumed,
            mid_price=round(mid_price, 6),
        )

    def _get_profile(self, asset: str) -> MarketDepthProfile:
        """Get the depth profile for an asset, falling back to DEFAULT."""
        return self._profiles.get(
            asset.upper(),
            self._profiles.get("DEFAULT", MarketDepthProfile(asset="DEFAULT")),
        )

    # ── Calibration ──────────────────────────────────────────────────────

    @classmethod
    def calibrate_from_ticks(
        cls,
        ticks: list[dict],
        asset: str = "DEFAULT",
    ) -> "FillSimulator":
        """
        Calibrate depth profiles from real tick data.

        Uses median statistics (robust to outliers) for:
        - Base impact: inversely proportional to L1 depth
        - Profile added to DEFAULT_PROFILES under the given asset key.

        Args:
            ticks: List of tick dicts with spread, bids_vol_1..3,
                   asks_vol_1..3, volume_24h fields.
            asset: Asset label for the calibrated profile.

        Returns:
            FillSimulator with calibrated profile for this asset.
        """
        if not ticks:
            return cls()

        l1_depths = []

        for t in ticks:
            b1 = t.get("bids_vol_1") or t.get("asks_vol_1") or 0
            if b1 > 0:
                l1_depths.append(b1)

        def _median(vals: list[float]) -> float:
            if not vals:
                return 0.0
            s = sorted(vals)
            return s[len(s) // 2]

        median_l1 = _median(l1_depths) if l1_depths else 10000.0

        # Base impact: inversely proportional to L1 depth
        if median_l1 > 0:
            base_impact_bps = max(0.1, 50000.0 / median_l1)
        else:
            base_impact_bps = 2.0

        profile = MarketDepthProfile(
            asset=asset,
            spread_cross_factor=0.5,
            base_impact_bps=round(base_impact_bps, 2),
            min_fill_ratio=0.1,
        )

        profiles = dict(DEFAULT_PROFILES)
        profiles[asset] = profile

        logger.info(
            "fill_simulator_calibrated",
            asset=asset,
            ticks=len(ticks),
            median_l1=round(median_l1, 0),
            base_impact_bps=round(base_impact_bps, 2),
        )

        return cls(profiles=profiles)
