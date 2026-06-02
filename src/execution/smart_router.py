"""
P9.4 -- Smart Order Routing: adaptive execution orchestration.

Composes P9.1-P9.3 modules into intelligent routing decisions:
  1. Adaptive maker/taker: dynamic thresholds based on volatility, spread, depth
  2. Timing optimization: wait-for-favorable windows (optional)
  3. Order splitting: chunk large orders to reduce price impact

Architecture:
    tick_data, order_size, side, volatility, regime
        |
        v
    SmartRouter.route()
        +- SlippageEngine.estimate()        -> taker cost
        +- SlippageEngine.estimate_maker()  -> maker viability
        +- SlippageEngine.compare_maker_vs_taker() -> decision
        |
        v
    RoutingDecision
        +- mode: "taker" | "maker" | "split"
        +- chunks: list of (size, mode, delay) for split orders
        +- expected_slippage: aggregate expected cost
        +- reason: human-readable explanation

Key differences from P9.3's static execute_mode:
    - Dynamic thresholds: maker threshold tightens in high vol, widens in low vol
    - Order splitting: orders > depth_threshold are chunked into N parts
    - Timing: can delay execution to wait for better conditions (configurable)

Usage:
    router = SmartRouter()
    decision = router.route(
        tick_data=tick_dict, order_size=50.0, side="entry",
        volatility=0.15, regime="CHOP",
    )
    for chunk in decision.chunks:
        execute(chunk.size, chunk.mode)

Refs: WORKFLOW.md P9.4, ROADMAP.md P7.4 (Smart Order Routing)
"""

from dataclasses import dataclass, field

import structlog

from src.execution.slippage_engine import SlippageEngine

logger = structlog.get_logger(__name__)


# ==========================================================================
# CONFIGURATION
# ==========================================================================


@dataclass
class SmartRouterConfig:
    """Configuration for adaptive order routing."""

    # -- Maker/Taker thresholds -----------------------------------------
    maker_spread_max: float = 0.05
    """Maximum spread for maker execution (wider -> taker only)."""

    maker_depth_min: float = 1000.0
    """Minimum L1 depth (USDC) for maker execution."""

    maker_vol_ceiling: float = 0.30
    """Volatility above which maker is disabled (force taker)."""

    maker_threshold_base: float = 0.95
    """Base maker cost ratio threshold (cost_ratio < this -> MAKER)."""

    maker_threshold_tight: float = 0.90
    """Tight threshold in high-vol regimes (requires 10%+ savings)."""

    maker_threshold_loose: float = 0.98
    """Loose threshold in low-vol regimes (2% savings enough)."""

    # -- Order splitting ------------------------------------------------
    max_chunk_size: float = 25.0
    """Maximum size per chunk in USDC. Orders above this are split."""

    min_chunk_size: float = 5.0
    """Minimum chunk size. Orders below this are not split."""

    max_chunks: int = 4
    """Maximum number of chunks to split into."""

    split_spread_factor: float = 1.5
    """Multiplier: split if order > spread_factor * L1_depth."""

    # -- Timing ---------------------------------------------------------
    timing_enabled: bool = False
    """Enable delay-before-execute optimization (experimental)."""

    max_wait_seconds: float = 30.0
    """Maximum seconds to wait for favorable conditions."""


# ==========================================================================
# ROUTING DECISION
# ==========================================================================


@dataclass
class OrderChunk:
    """A single chunk of a potentially split order."""

    size: float
    """Chunk size in USDC."""

    mode: str  # "maker" or "taker"
    """Execution mode for this chunk."""

    delay_seconds: float = 0.0
    """Optional delay before executing this chunk."""


@dataclass
class RoutingDecision:
    """Result of a smart routing decision."""

    mode: str  # "taker", "maker", or "split"
    """Overall execution mode."""

    chunks: list[OrderChunk] = field(default_factory=list)
    """Order chunks to execute (single element if not split)."""

    expected_slippage: float = 0.0
    """Aggregate expected slippage across all chunks."""

    expected_savings_pct: float = 0.0
    """Estimated savings vs pure taker execution."""

    reason: str = ""
    """Human-readable explanation."""

    maker_p_fill: float = 0.0
    """Fill probability for maker path (0.0 if taker)."""

    adverse_bps: float = 0.0
    """Adverse selection estimate in bps."""

    @property
    def is_split(self) -> bool:
        return len(self.chunks) > 1

    @property
    def total_size(self) -> float:
        return sum(c.size for c in self.chunks)

    @property
    def maker_chunks(self) -> int:
        return sum(1 for c in self.chunks if c.mode == "maker")

    @property
    def taker_chunks(self) -> int:
        return sum(1 for c in self.chunks if c.mode == "taker")


# ==========================================================================
# SMART ROUTER
# ==========================================================================


class SmartRouter:
    """Orchestrates P9.1-P9.3 modules for intelligent order execution.

    Composes:
      - SlippageEngine (P9.2): taker cost + maker estimate + comparison
      - QueuePositionEngine (P9.3, via SlippageEngine): maker fill prob

    Decision flow:
      1. Check environmental constraints (spread, depth, volatility)
         -> if extreme, force TAKER
      2. If conditions permit maker, do cost comparison
      3. If order_size > max_chunk_size, split into chunks
         -> each chunk independently routed (maker/taker)
      4. Return RoutingDecision with execution plan
    """

    def __init__(
        self,
        config: SmartRouterConfig | None = None,
        slippage_engine: SlippageEngine | None = None,
    ):
        self._config = config or SmartRouterConfig()
        self._slippage = slippage_engine or SlippageEngine()

    # -- Public API ----------------------------------------------------

    def route(
        self,
        tick_data: dict,
        order_size: float,
        side: str = "entry",
        asset: str = "DEFAULT",
        volatility: float | None = None,
        regime: str | None = None,
    ) -> RoutingDecision:
        """Determine optimal execution strategy for an order.

        Args:
            tick_data: Orderbook tick dict (best_bid/ask, spread, depth).
            order_size: Total order size in USDC.
            side: "entry" (buy) or "exit" (sell).
            asset: BTC, ETH, or DEFAULT for FillSimulator profile.
            volatility: Annualized realized volatility (0.0-1.0+).
            regime: Market regime label (TREND/CHOP/PANIC/ILLIQUID/EVENT_DRIVEN).

        Returns:
            RoutingDecision with execution plan.
        """
        cfg = self._config
        spread = tick_data.get("spread", 0.02)
        l1_depth = (
            tick_data.get("asks_vol_1", 0) or 0
            if side == "entry"
            else tick_data.get("bids_vol_1", 0) or 0
        )
        vol = volatility or 0.0

        # ── 1. Environmental constraints ──────────────────────────
        if spread > cfg.maker_spread_max:
            return self._force_taker(
                order_size, side, tick_data, asset, volatility, regime,
                f"spread {spread:.4f} > max {cfg.maker_spread_max}",
            )
        if l1_depth < cfg.maker_depth_min:
            return self._force_taker(
                order_size, side, tick_data, asset, volatility, regime,
                f"L1 depth {l1_depth:.0f} < min {cfg.maker_depth_min}",
            )
        if vol > cfg.maker_vol_ceiling:
            return self._force_taker(
                order_size, side, tick_data, asset, volatility, regime,
                f"volatility {vol:.2f} > ceiling {cfg.maker_vol_ceiling}",
            )

        # ── 2. Dynamic maker threshold ────────────────────────────
        threshold = self._dynamic_threshold(volatility, regime)

        # ── 3. Check if order should be split ─────────────────────
        if order_size > cfg.max_chunk_size or self._should_split(
            order_size, l1_depth
        ):
            return self._split_route(
                order_size, side, tick_data, asset,
                volatility, regime, threshold,
            )

        # ── 4. Single order: maker-vs-taker comparison ────────────
        return self._single_route(
            order_size, side, tick_data, asset,
            volatility, regime, threshold,
        )

    # -- Internal -------------------------------------------------------

    def _dynamic_threshold(
        self,
        volatility: float | None,
        regime: str | None,
    ) -> float:
        """Compute dynamic maker threshold based on conditions.

        Low vol / CHOP -> loose threshold (maker favored).
        High vol / PANIC -> tight threshold (taker favored).
        """
        cfg = self._config
        vol = volatility or 0.0

        if regime and regime.upper() in ("PANIC", "TREND"):
            return cfg.maker_threshold_tight
        if vol <= 0.10:
            return cfg.maker_threshold_loose
        if vol >= cfg.maker_vol_ceiling:
            return cfg.maker_threshold_tight

        # Linear interpolation between loose and tight
        ratio = vol / cfg.maker_vol_ceiling
        return cfg.maker_threshold_loose - ratio * (
            cfg.maker_threshold_loose - cfg.maker_threshold_tight
        )

    def _should_split(self, order_size: float, l1_depth: float) -> bool:
        """Determine if order should be split into chunks.

        Splits if order is large relative to available L1 depth,
        even if it's below max_chunk_size (thin book risk).
        """
        cfg = self._config
        if l1_depth <= 0:
            return order_size > cfg.min_chunk_size
        return order_size > l1_depth * cfg.split_spread_factor

    def _force_taker(
        self,
        order_size: float,
        side: str,
        tick_data: dict,
        asset: str,
        volatility: float | None,
        regime: str | None,
        reason: str,
    ) -> RoutingDecision:
        """Force taker execution when conditions don't permit maker."""
        est = self._slippage.estimate(
            tick_data=tick_data,
            order_size=order_size,
            asset=asset,
            side=side,
            volatility=volatility,
            regime=regime,
        )
        return RoutingDecision(
            mode="taker",
            chunks=[OrderChunk(size=order_size, mode="taker")],
            expected_slippage=abs(est.adjusted_slippage),
            reason=reason,
        )

    def _single_route(
        self,
        order_size: float,
        side: str,
        tick_data: dict,
        asset: str,
        volatility: float | None,
        regime: str | None,
        threshold: float,
    ) -> RoutingDecision:
        """Route a single (non-split) order."""
        taker_est = self._slippage.estimate(
            tick_data=tick_data,
            order_size=order_size,
            asset=asset,
            side=side,
            volatility=volatility,
            regime=regime,
        )
        taker_cost = abs(taker_est.adjusted_slippage)

        maker = self._slippage.estimate_maker(
            tick_data=tick_data,
            order_size=order_size,
            side=side,
            volatility=volatility,
            regime=regime,
        )
        decision = self._slippage.compare_maker_vs_taker(
            taker_cost=taker_cost,
            maker_estimate=maker,
        )

        if decision.prefer_maker and decision.cost_ratio < threshold:
            # Expected maker cost: fills at adverse price, or doesn't fill
            # and pays taker cost + delay penalty.
            adverse_price_units = maker.adverse_selection_bps / 10000.0
            maker_expected_slip = (
                maker.p_fill * adverse_price_units
                + (1 - maker.p_fill) * taker_cost
            )
            return RoutingDecision(
                mode="maker",
                chunks=[OrderChunk(size=order_size, mode="maker")],
                expected_slippage=round(maker_expected_slip, 6),
                expected_savings_pct=decision.savings_pct,
                reason=f"maker preferred: P(fill)={maker.p_fill:.1%}, "
                       f"ratio={decision.cost_ratio:.3f} < {threshold}",
                maker_p_fill=maker.p_fill,
                adverse_bps=maker.adverse_selection_bps,
            )

        return RoutingDecision(
            mode="taker",
            chunks=[OrderChunk(size=order_size, mode="taker")],
            expected_slippage=taker_cost,
            reason=f"taker preferred: ratio={decision.cost_ratio:.3f} >= {threshold}",
            maker_p_fill=maker.p_fill,
            adverse_bps=maker.adverse_selection_bps,
        )

    def _split_route(
        self,
        order_size: float,
        side: str,
        tick_data: dict,
        asset: str,
        volatility: float | None,
        regime: str | None,
        threshold: float,
    ) -> RoutingDecision:
        """Route a split order into multiple chunks.

        Each chunk is independently evaluated for maker vs taker.
        First chunk may try maker; subsequent chunks use taker
        (since maker fill probability degrades after first fill).
        """
        cfg = self._config
        num_chunks = min(
            cfg.max_chunks,
            max(2, int(order_size / cfg.max_chunk_size) + 1),
        )
        base_chunk = order_size / num_chunks
        base_chunk = max(cfg.min_chunk_size, base_chunk)

        chunks: list[OrderChunk] = []
        total_slippage = 0.0
        maker_fills = 0
        total_p_fill = 0.0

        for i in range(num_chunks):
            chunk_size = base_chunk
            # Last chunk gets the remainder (rounded to avoid float drift)
            if i == num_chunks - 1:
                allocated = round(base_chunk * (num_chunks - 1), 2)
                chunk_size = round(order_size - allocated, 2)
                chunk_size = max(cfg.min_chunk_size, chunk_size)

            if i == 0:
                # First chunk: try maker
                sub = self._single_route(
                    chunk_size, side, tick_data, asset,
                    volatility, regime, threshold,
                )
                chunks.append(OrderChunk(
                    size=chunk_size,
                    mode=sub.mode,
                ))
                total_slippage += abs(sub.expected_slippage)
                if sub.mode == "maker":
                    maker_fills += 1
                total_p_fill += sub.maker_p_fill
            else:
                # Subsequent chunks: taker only (maker queue already consumed)
                est = self._slippage.estimate(
                    tick_data=tick_data,
                    order_size=chunk_size,
                    asset=asset,
                    side=side,
                    volatility=volatility,
                    regime=regime,
                )
                chunks.append(OrderChunk(
                    size=chunk_size,
                    mode="taker",
                ))
                total_slippage += abs(est.adjusted_slippage)

        avg_p_fill = total_p_fill / num_chunks if num_chunks > 0 else 0.0

        return RoutingDecision(
            mode="split",
            chunks=chunks,
            expected_slippage=total_slippage,
            expected_savings_pct=round(
                maker_fills / max(num_chunks, 1) * 100, 1
            ),
            reason=f"split into {num_chunks} chunks "
                   f"(~{base_chunk:.0f} USDC each), "
                   f"maker/taker per chunk",
            maker_p_fill=avg_p_fill,
        )

    # -- Diagnostics ---------------------------------------------------

    @property
    def slippage_engine(self) -> SlippageEngine:
        """Access the underlying SlippageEngine (P9.2)."""
        return self._slippage

    @property
    def config(self) -> SmartRouterConfig:
        """Access the current routing configuration."""
        return self._config
