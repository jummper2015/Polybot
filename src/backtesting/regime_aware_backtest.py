# src/backtesting/regime_aware_backtest.py

"""
Regime-aware multi-strategy backtesting (P11.1 DESPLEGAR).

Extends the backtesting pipeline to support multiple strategies with
regime-based filtering. Pre-computes market regimes from the dataset
in batch mode, then evaluates strategies conditionally based on
their allowed_regimes configuration.

Architecture:
    HistoricalDataset → RegimeClassifier.classify_batch()
    → regimes: list[Regime] (one per tick)
    → MultiStrategyBacktestRunner
        ├── BAT (allowed: TREND)
        ├── MR (allowed: CHOP, TREND, EVENT_DRIVEN)
        └── Regime filtering: skip strategy if regime mismatch

Usage:
    runner = MultiStrategyBacktestRunner(
        strategies=[bat_strategy, mr_strategy],
        initial_balance=1000.0,
    )
    result = runner.run(dataset)

    # Or use the classmethod for full pipeline
    result = MultiStrategyBacktestRunner.run_from_dataset(
        dataset,
        bat_config=BuyAboveThresholdConfig(),
        mr_config=MeanReversionConfig(),
        initial_balance=1000.0,
    )
"""

from dataclasses import dataclass, field
from datetime import datetime

import structlog

from src.backtesting.data_loader import HistoricalDataset
from src.backtesting.engine import BacktestPosition
from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import SignalType
from src.execution.fill_simulator import FillSimulator
from src.infrastructure.data.regime import Regime, RegimeClassifier
from src.risk.engine import RiskEngineConfig
from src.strategies.base import IStrategy
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig
from src.strategies.buy_above_threshold.strategy import BuyAboveThresholdStrategy
from src.strategies.mean_reversion.config import MeanReversionConfig
from src.strategies.mean_reversion.strategy import MeanReversionStrategy
from src.strategies.regime_aware import StrategyRegimeBinding, create_binding_from_config

logger = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# MULTI-STRATEGY BACKTEST RESULT
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class MultiStrategyBacktestResult:
    """
    Extended backtest result with per-strategy breakdown.

    Contains the standard BacktestResult plus:
    - Per-strategy position lists
    - Per-strategy PnL and metrics
    - Regime distribution during the backtest
    """

    asset: str
    window: str
    dataset_ticks: int
    dataset_start: datetime
    dataset_end: datetime
    initial_balance: float
    final_balance: float

    # All positions (combined from all strategies)
    positions: list[BacktestPosition] = field(default_factory=list)

    # Per-strategy breakdown
    strategy_positions: dict[str, list[BacktestPosition]] = field(default_factory=dict)
    strategy_pnl: dict[str, float] = field(default_factory=dict)
    strategy_trades: dict[str, int] = field(default_factory=dict)

    # Regime distribution during backtest
    regime_distribution: dict[str, float] = field(default_factory=dict)
    regime_ticks: list[str] = field(default_factory=list)

    # Regime-aware filtering stats
    strategies_skipped_by_regime: dict[str, int] = field(default_factory=dict)

    @property
    def closed_positions(self) -> list[BacktestPosition]:
        return [p for p in self.positions if not p.is_open]

    @property
    def total_pnl(self) -> float:
        return self.final_balance - self.initial_balance

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "asset": self.asset,
            "window": self.window,
            "dataset_ticks": self.dataset_ticks,
            "initial_balance": self.initial_balance,
            "final_balance": self.final_balance,
            "total_pnl": round(self.total_pnl, 4),
            "total_trades": len(self.positions),
            "closed_trades": len(self.closed_positions),
            "strategy_pnl": {
                name: round(pnl, 4)
                for name, pnl in self.strategy_pnl.items()
            },
            "strategy_trades": self.strategy_trades,
            "regime_distribution": self.regime_distribution,
            "strategies_skipped_by_regime": self.strategies_skipped_by_regime,
        }


# ══════════════════════════════════════════════════════════════════════════
# MULTI-STRATEGY BACKTEST RUNNER
# ══════════════════════════════════════════════════════════════════════════


class MultiStrategyBacktestRunner:
    """
    Runs backtests with multiple strategies and regime-aware filtering.

    Pre-computes market regimes from the dataset in batch mode,
    then evaluates each strategy conditionally based on its
    allowed_regimes configuration.

    Differences from BacktestEngine:
    - Supports multiple strategies (BAT + MR)
    - Regime-aware: skips strategies not active in current regime
    - Tracks per-strategy performance
    - Exit signals NOT filtered by regime (safety first)
    """

    def __init__(
        self,
        strategies: list[IStrategy],
        strategy_bindings: dict[str, StrategyRegimeBinding] | None = None,
        risk_config: RiskEngineConfig | None = None,
        initial_balance: float = 1000.0,
        verbose: bool = False,
        fill_simulator: FillSimulator | None = None,
        regime_classifier: RegimeClassifier | None = None,
    ):
        """
        Args:
            strategies: List of IStrategy instances to run.
            strategy_bindings: Dict mapping strategy_name → StrategyRegimeBinding.
                If None, bindings are auto-created from strategy configs.
            risk_config: Risk engine config for balance checks.
            initial_balance: Starting balance in USDC.
            verbose: Print each trade during backtest.
            fill_simulator: Optional FillSimulator for realistic fills.
            regime_classifier: RegimeClassifier for batch regime detection.
        """
        self._strategies = strategies
        self._risk_config = risk_config or RiskEngineConfig()
        self._initial_balance = initial_balance
        self._verbose = verbose
        self._fill_sim = fill_simulator or FillSimulator()
        self._classifier = regime_classifier or RegimeClassifier()

        # Build bindings for each strategy
        if strategy_bindings:
            self._bindings = strategy_bindings
        else:
            self._bindings = self._build_bindings()

        # Validate all strategy configs
        for strategy in strategies:
            if hasattr(strategy, "_config") and hasattr(strategy._config, "validate"):
                strategy._config.validate()

    # ── Public API ──────────────────────────────────────────────────────

    def run(self, dataset: HistoricalDataset) -> MultiStrategyBacktestResult:
        """
        Run multi-strategy backtest on a dataset.

        Flow:
        1. Pre-compute regimes for all ticks in batch mode
        2. Initialize all strategies with synthetic market
        3. For each tick: evaluate exit (any position) → entry (regime-filtered)
        4. Collect per-strategy results

        Returns:
            MultiStrategyBacktestResult with full breakdown.
        """
        logger.info(
            "multi_strategy_backtest_starting",
            asset=dataset.asset,
            window=dataset.window,
            ticks=dataset.tick_count,
            strategies=[s.name for s in self._strategies],
        )

        # ── Pre-compute regimes ───────────────────────────────────────
        regimes, regime_confidences = self._precompute_regimes(dataset)
        logger.info(
            "regimes_precomputed",
            ticks=len(regimes),
            distribution=self._regime_distribution(regimes),
        )

        # ── Initialize strategies ─────────────────────────────────────
        market = self._make_synthetic_market(dataset)
        for strategy in self._strategies:
            strategy._get_or_create_state(dataset.market_id)  # type: ignore[attr-defined]  # sync backtest helper on IStrategy

        # ── State ─────────────────────────────────────────────────────
        balance: float = self._initial_balance
        all_positions: list[BacktestPosition] = []
        strategy_positions: dict[str, list[BacktestPosition]] = {
            s.name: [] for s in self._strategies
        }
        open_position: BacktestPosition | None = None
        open_strategy_name: str | None = None
        skipped_by_regime: dict[str, int] = {s.name: 0 for s in self._strategies}

        # ── Main loop ─────────────────────────────────────────────────
        for tick_idx, tick in enumerate(dataset.ticks):
            current_regime = regimes[tick_idx] if tick_idx < len(regimes) else Regime.CHOP

            # Update strategy states
            for strategy in self._strategies:
                state = strategy._get_or_create_state(dataset.market_id)  # type: ignore[attr-defined]
                self._sync_strategy_on_tick(strategy, state, tick)

            # ── Evaluate exit (any position) ──────────────────────────
            if open_position:
                exit_signal = self._sync_should_exit_for_strategy(
                    self._get_strategy(open_strategy_name),
                    market, tick,
                )

                if exit_signal is not None and exit_signal.type in (
                    SignalType.EXIT, SignalType.BUY_NO,
                ):
                    position_value = open_position.shares * tick.yes_price
                    tick_data = self._tick_to_data(tick)
                    estimate = self._fill_sim.estimate_exit(
                        tick_data=tick_data,
                        position_value=position_value,
                        asset=dataset.asset,
                    )
                    exit_price = estimate.fill_price

                    open_position.close(
                        exit_price=exit_price,
                        exit_tick=tick_idx,
                        exit_at=tick.timestamp,
                        reason=exit_signal.reason,
                    )
                    return_value = open_position.shares * exit_price
                    balance += return_value

                    # Update strategy state
                    strat = self._get_strategy(open_strategy_name)
                    if strat:
                        strat._get_or_create_state(dataset.market_id).record_exit()  # type: ignore[attr-defined]
                    open_position = None
                    open_strategy_name = None

                    if self._verbose and all_positions:
                        last = all_positions[-1]
                        print(
                            f"  EXIT [{tick_idx:5d}] "
                            f"price={exit_price:.4f} "
                            f"pnl={last.pnl:+.4f} USDC "
                            f"({last.pnl_pct:+.2%}) "
                            f"reason={exit_signal.reason}"
                        )

            # ── Evaluate entry (regime-filtered) ──────────────────────
            elif not open_position:
                for strategy in self._strategies:
                    strategy_name = strategy.name

                    # Check regime compatibility
                    if not self._is_strategy_active(strategy_name, current_regime):
                        skipped_by_regime[strategy_name] += 1
                        continue

                    state = strategy._get_or_create_state(dataset.market_id)  # type: ignore[attr-defined]
                    if state.in_position:
                        continue

                    entry_signal = self._sync_should_enter(strategy, market, tick)

                    if entry_signal is not None and entry_signal.type == SignalType.BUY_YES:
                        # Risk check
                        risk_ok, _ = self._check_risk_sync(
                            balance=balance,
                            open_count=1 if open_position else 0,
                        )
                        if not risk_ok:
                            continue

                        amount = self._get_position_size(strategy)
                        tick_data = self._tick_to_data(tick)
                        estimate = self._fill_sim.estimate_entry(
                            tick_data=tick_data,
                            order_size=amount,
                            asset=dataset.asset,
                        )
                        fill_price = estimate.fill_price
                        shares = amount / fill_price

                        position = BacktestPosition(
                            market_id=dataset.market_id,
                            side="YES",
                            amount=amount,
                            shares=shares,
                            entry_price=fill_price,
                            entry_tick=tick_idx,
                            entry_at=tick.timestamp,
                        )
                        open_position = position
                        open_strategy_name = strategy_name
                        all_positions.append(position)
                        strategy_positions[strategy_name].append(position)
                        balance -= amount
                        state.record_entry(fill_price)

                        if self._verbose:
                            print(
                                f"  ENTRY [{tick_idx:5d}] "
                                f"strategy={strategy_name} "
                                f"price={fill_price:.4f} "
                                f"amount={amount:.2f} USDC "
                                f"regime={current_regime.value}"
                            )
                        break  # First strategy wins

        # ── Close open position at dataset end ─────────────────────────
        if open_position and dataset.ticks:
            last_tick = dataset.ticks[-1]
            position_value = open_position.shares * last_tick.yes_price
            tick_data = self._tick_to_data(last_tick)
            estimate = self._fill_sim.estimate_exit(
                tick_data=tick_data,
                position_value=position_value,
                asset=dataset.asset,
            )
            exit_price = estimate.fill_price
            open_position.close(
                exit_price=exit_price,
                exit_tick=len(dataset.ticks) - 1,
                exit_at=last_tick.timestamp,
                reason="dataset_end",
            )
            balance += open_position.shares * exit_price

        # ── Compute per-strategy PnL ──────────────────────────────────
        strategy_pnl: dict[str, float] = {}
        strategy_trades: dict[str, int] = {}
        for strategy_name, positions in strategy_positions.items():
            closed = [p for p in positions if not p.is_open]
            pnl = sum((p.pnl or 0.0 for p in closed), 0.0)
            strategy_pnl[strategy_name] = pnl
            strategy_trades[strategy_name] = len(closed)

        # ── Build result ──────────────────────────────────────────────
        result = MultiStrategyBacktestResult(
            asset=dataset.asset,
            window=dataset.window,
            dataset_ticks=dataset.tick_count,
            dataset_start=dataset.start_at,
            dataset_end=dataset.end_at,
            initial_balance=self._initial_balance,
            final_balance=balance,
            positions=all_positions,
            strategy_positions=strategy_positions,
            strategy_pnl=strategy_pnl,
            strategy_trades=strategy_trades,
            regime_distribution=self._regime_distribution(regimes),
            regime_ticks=[r.value for r in regimes],
            strategies_skipped_by_regime=skipped_by_regime,
        )

        logger.info(
            "multi_strategy_backtest_complete",
            total_positions=len(all_positions),
            closed=len(result.closed_positions),
            final_balance=round(balance, 2),
            total_pnl=round(result.total_pnl, 4),
            strategy_pnl={
                name: round(pnl, 4)
                for name, pnl in strategy_pnl.items()
            },
        )

        return result

    @classmethod
    def run_from_dataset(
        cls,
        dataset: HistoricalDataset,
        bat_config: BuyAboveThresholdConfig | None = None,
        mr_config: MeanReversionConfig | None = None,
        initial_balance: float = 1000.0,
        verbose: bool = False,
    ) -> MultiStrategyBacktestResult:
        """
        Convenience classmethod: create strategies from configs and run.

        Args:
            dataset: HistoricalDataset to backtest on.
            bat_config: BAT strategy config (default if None).
            mr_config: MR strategy config (default if None).
            initial_balance: Starting balance.
            verbose: Print each trade.

        Returns:
            MultiStrategyBacktestResult.
        """
        bat = BuyAboveThresholdStrategy(config=bat_config)
        mr = MeanReversionStrategy(config=mr_config)

        runner = cls(
            strategies=[bat, mr],
            initial_balance=initial_balance,
            verbose=verbose,
        )
        return runner.run(dataset)

    # ── Internal: Regime Pre-computation ────────────────────────────────

    def _precompute_regimes(
        self, dataset: HistoricalDataset
    ) -> tuple[list[Regime], list[float]]:
        """Pre-compute regime labels for all ticks in batch mode."""
        result = self._classifier.classify_batch(
            ticks=dataset.ticks,
            asset=dataset.asset,
            market_id=dataset.market_id,
        )
        return result.labels, result.confidence

    # ── Internal: Strategy State Sync ───────────────────────────────────

    @staticmethod
    def _sync_strategy_on_tick(
        strategy: IStrategy,
        state,
        tick: MarketTick,
    ) -> None:
        """Update strategy state for a tick (sync version)."""
        state.add_tick(tick)

        # BAT-specific: consecutive tick tracking
        if strategy.name == "BuyAboveThreshold" and hasattr(strategy, "_config"):
            threshold = strategy._config.threshold
            if tick.yes_price >= threshold:
                state.consecutive_ticks += 1
            else:
                state.consecutive_ticks = 0

    def _sync_should_enter(
        self,
        strategy: IStrategy,
        market: Market,
        tick: MarketTick,
    ):
        """Sync version of should_enter."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(strategy.should_enter(market, tick))
        finally:
            loop.close()

    @staticmethod
    def _sync_should_exit_for_strategy(
        strategy,
        market: Market,
        tick: MarketTick,
    ):
        """Sync version of should_exit."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(strategy.should_exit(market, tick))
        finally:
            loop.close()

    # ── Internal: Regime Filtering ─────────────────────────────────────

    def _is_strategy_active(self, strategy_name: str, regime: Regime) -> bool:
        """Check if a strategy should be active in the given regime."""
        binding = self._bindings.get(strategy_name)
        if binding is None:
            return True  # No binding → always active
        return binding.enabled and regime in binding.allowed_regimes

    def _build_bindings(self) -> dict[str, StrategyRegimeBinding]:
        """Auto-create bindings from strategy configs."""
        bindings: dict[str, StrategyRegimeBinding] = {}
        for strategy in self._strategies:
            name = strategy.name
            allowed = None
            if hasattr(strategy, "_config") and hasattr(strategy._config, "allowed_regimes"):
                allowed = strategy._config.allowed_regimes
            binding = create_binding_from_config(
                strategy_name=name,
                allowed_regimes=allowed,
            )
            bindings[name] = binding
        return bindings

    # ── Internal: Risk ──────────────────────────────────────────────────

    def _check_risk_sync(
        self,
        balance: float,
        open_count: int,
    ) -> tuple[bool, str]:
        """Sync risk check."""
        if balance - 10.0 < self._risk_config.min_balance_usdc:
            return False, "min_balance"
        if open_count >= self._risk_config.max_open_positions:
            return False, "max_positions"
        return True, "ok"

    # ── Internal: Helpers ───────────────────────────────────────────────

    def _get_strategy(self, name: str | None) -> IStrategy | None:
        if name is None:
            return None
        for s in self._strategies:
            if s.name == name:
                return s
        return None

    @staticmethod
    def _get_position_size(strategy: IStrategy) -> float:
        """Extract position_size_pusd from strategy config."""
        if hasattr(strategy, "_config") and hasattr(strategy._config, "position_size_pusd"):
            return strategy._config.position_size_pusd
        return 10.0

    @staticmethod
    def _tick_to_data(tick: MarketTick) -> dict:
        """Build FillSimulator-compatible tick dict."""
        return {
            "best_bid": tick.best_bid,
            "best_ask": tick.best_ask,
            "spread": tick.spread,
            "bids_vol_1": 0.0,
            "bids_vol_2": 0.0,
            "bids_vol_3": 0.0,
            "asks_vol_1": 0.0,
            "asks_vol_2": 0.0,
            "asks_vol_3": 0.0,
            "volume_24h": tick.volume_24h,
        }

    @staticmethod
    def _make_synthetic_market(dataset: HistoricalDataset) -> Market:
        """Create a synthetic Market for backtest."""
        far_future = datetime(2099, 12, 31, 23, 59, 59)
        if dataset.ticks:
            yes_price = dataset.ticks[0].yes_price
            no_price = dataset.ticks[0].no_price
            volume_24h = dataset.ticks[0].volume_24h
        else:
            yes_price = 0.50
            no_price = 0.50
            volume_24h = 1000.0

        return Market(
            id=dataset.market_id,
            asset=Asset(dataset.asset),
            window=Window(dataset.window),
            question=f"Backtest {dataset.asset} {dataset.window}",
            status=MarketStatus.ACTIVE,
            yes_token_id="backtest_yes",
            no_token_id="backtest_no",
            yes_price=yes_price,
            no_price=no_price,
            volume_24h=volume_24h,
            expiry=far_future,
        )

    @staticmethod
    def _regime_distribution(regimes: list[Regime]) -> dict[str, float]:
        """Compute regime distribution as fractions."""
        total = len(regimes)
        if total == 0:
            return {}
        counts: dict[str, int] = {}
        for r in regimes:
            counts[r.value] = counts.get(r.value, 0) + 1
        return {
            regime: round(count / total, 4)
            for regime, count in sorted(counts.items())
        }
