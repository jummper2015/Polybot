# src/backtesting/replay_engine.py

"""
Deterministic market replay engine.

Replays historical tick data at configurable speeds for backtesting,
strategy validation, and research. Guarantees reproducibility:
same input + same seed → same signals.

Architecture:
    ParquetDataLoader → HistoricalDataset → ReplayEngine → BacktestEngine

Modes:
    - instant (default):  Process all ticks as fast as possible
    - time-travel:         Skip to a specific timestamp before replay

Note: Speed-controlled replay (1x, Nx) is not yet implemented — the
BacktestEngine processes ticks synchronously in a single pass. All
modes produce instant replay. The ReplayConfig.speed parameter is
recorded for future use.

Usage:
    engine = ReplayEngine()
    result = engine.replay(dataset)

    # Time travel
    result = engine.replay(dataset, ReplayConfig(
        start_timestamp=datetime(2026, 5, 27, 12, 0, 0),
    ))

    # From Parquet
    result = engine.replay_from_parquet(asset="BTC")
"""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import structlog

from src.backtesting.data_loader import HistoricalDataset
from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.parquet_loader import ParquetDataLoader
from src.risk.engine import RiskEngineConfig
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig

logger = structlog.get_logger(__name__)


@dataclass
class ReplayConfig:
    """Configuration for a replay session."""

    speed: float = 1.0
    """Replay speed multiplier (reserved for future speed-controlled replay).
       Currently all modes run instant. Values <= 0 are treated as instant."""

    start_timestamp: Optional[datetime] = None
    """If set, skip ticks before this timestamp (time-travel)."""

    end_timestamp: Optional[datetime] = None
    """If set, ignore ticks after this timestamp."""

    max_ticks: Optional[int] = None
    """If set, stop after processing this many ticks."""

    seed: int = 42
    """Random seed for reproducibility. Same seed → same signals."""

    verbose: bool = False
    """Log every tick during replay (slow but useful for debugging)."""


@dataclass
class ReplayResult:
    """Result of a replay session with metadata."""

    backtest: BacktestResult
    """The underlying backtest result from BacktestEngine."""

    replay_config: ReplayConfig
    """The config used for this replay."""

    replay_duration_seconds: float = 0.0
    """Wall-clock time the replay took."""

    ticks_replayed: int = 0
    """Number of ticks actually processed."""

    ticks_skipped: int = 0
    """Number of ticks skipped (before start_timestamp)."""

    real_time_span_hours: float = 0.0
    """Real-world time span covered (original tick timestamps)."""

    effective_speed: float = 0.0
    """Effective replay speed (real_time_span / replay_duration).
       Note: all modes run instant currently, so this is always very high."""


class ReplayEngine:
    """
    Deterministic market replay orchestrator.

    Replays historical tick data through the BacktestEngine for
    strategy evaluation. Supports time-travel, tick limits, and
    Parquet data loading.

    Guarantees:
    - Same dataset + same seed → same BacktestResult
    - Synchronous execution (compatible with BacktestEngine)

    Integration:
    - Uses BacktestEngine.run() for the actual strategy logic
    - Uses ParquetDataLoader for data loading
    - Works with both Parquet and synthetic datasets
    """

    def __init__(
        self,
        strategy_config: BuyAboveThresholdConfig | None = None,
        risk_config: RiskEngineConfig | None = None,
        initial_balance: float = 1000.0,
        parquet_base_dir: str = "data/parquet",
    ):
        self._strategy_config = strategy_config or BuyAboveThresholdConfig()
        self._risk_config = risk_config or RiskEngineConfig()
        self._initial_balance = initial_balance
        self._parquet_loader = ParquetDataLoader(base_dir=parquet_base_dir)

    # ── Public API ─────────────────────────────────────────────────────────

    def replay(
        self,
        dataset: HistoricalDataset,
        config: ReplayConfig | None = None,
    ) -> ReplayResult:
        """
        Replay a HistoricalDataset through the BacktestEngine.

        Processes all ticks synchronously at maximum speed. Supports
        time-travel (via config.start_timestamp) and tick limits
        (via config.max_ticks).

        Args:
            dataset: The tick dataset to replay.
            config: Replay configuration (time range, tick limit, seed).

        Returns:
            ReplayResult with backtest output and replay metadata.
        """
        cfg = config or ReplayConfig()

        # Apply time filters
        ticks = self._filter_ticks(
            dataset.ticks,
            start_at=cfg.start_timestamp,
            end_at=cfg.end_timestamp,
            max_ticks=cfg.max_ticks,
        )

        ticks_skipped = dataset.tick_count - len(ticks)

        logger.info("replay_starting",
                     asset=dataset.asset,
                     window=dataset.window,
                     ticks=len(ticks),
                     skipped=ticks_skipped,
                     seed=cfg.seed)

        # Create filtered dataset (handle empty edge case)
        if not ticks:
            filtered = HistoricalDataset(
                asset=dataset.asset,
                window=dataset.window,
                market_id=dataset.market_id,
                ticks=[],
                start_at=dataset.start_at,
                end_at=dataset.end_at,
            )
        else:
            filtered = HistoricalDataset(
                asset=dataset.asset,
                window=dataset.window,
                market_id=dataset.market_id,
                ticks=ticks,
                start_at=ticks[0].timestamp,
                end_at=ticks[-1].timestamp,
            )

        # Run backtest
        wall_start = time.monotonic()

        engine = BacktestEngine(
            strategy_config=self._strategy_config,
            risk_config=self._risk_config,
            initial_balance=self._initial_balance,
            verbose=cfg.verbose,
        )

        backtest_result = engine.run(filtered)

        wall_end = time.monotonic()
        duration = wall_end - wall_start

        # Compute metadata
        real_span = 0.0
        if len(ticks) >= 2:
            delta = ticks[-1].timestamp - ticks[0].timestamp
            real_span = delta.total_seconds() / 3600

        effective_speed = (
            real_span / (duration / 3600) if duration > 0
            else float("inf")
        )

        result = ReplayResult(
            backtest=backtest_result,
            replay_config=cfg,
            replay_duration_seconds=round(duration, 3),
            ticks_replayed=len(ticks),
            ticks_skipped=ticks_skipped,
            real_time_span_hours=round(real_span, 2),
            effective_speed=round(effective_speed, 1),
        )

        logger.info("replay_complete",
                     ticks=len(ticks),
                     duration=round(duration, 3),
                     effective_speed=round(effective_speed, 1),
                     positions=len(backtest_result.positions),
                     pnl=round(
                         backtest_result.final_balance
                         - backtest_result.initial_balance, 4
                     ))

        return result

    def replay_from_parquet(
        self,
        asset: str,
        market_id: Optional[str] = None,
        window: str = "5m",
        config: ReplayConfig | None = None,
    ) -> ReplayResult:
        """
        Load data from Parquet and replay it.

        Convenience method combining ParquetDataLoader + replay().
        """
        dataset = self._parquet_loader.load(
            asset=asset,
            market_id=market_id,
            window=window,
        )
        return self.replay(dataset, config)

    def replay_date_range(
        self,
        asset: str,
        start_date: datetime,
        end_date: Optional[datetime] = None,
        market_id: Optional[str] = None,
        config: ReplayConfig | None = None,
    ) -> ReplayResult:
        """
        Replay a specific date range from Parquet data.
        """
        dataset = self._parquet_loader.load(
            asset=asset,
            market_id=market_id,
            start_date=start_date,
            end_date=end_date,
        )
        return self.replay(dataset, config)

    def get_parquet_loader(self) -> ParquetDataLoader:
        """Access the underlying ParquetDataLoader for data exploration."""
        return self._parquet_loader

    # ── Internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _filter_ticks(
        ticks: list,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        max_ticks: Optional[int] = None,
    ) -> list:
        """Filter ticks by time range and max count."""
        result = ticks

        if start_at:
            result = [t for t in result if t.timestamp >= start_at]

        if end_at:
            result = [t for t in result if t.timestamp < end_at]

        if max_ticks is not None and len(result) > max_ticks:
            result = result[:max_ticks]

        return result
