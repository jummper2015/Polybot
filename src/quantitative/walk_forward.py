# src/quantitative/walk_forward.py

"""
Walk-forward validation framework for strategy evaluation.

Implements anchored walk-forward analysis to eliminate static overfitting
and validate temporal robustness of trading strategies.

Architecture:
    ParquetDataLoader → HistoricalDataset → WalkForwardValidator
        ├── Fold 0: [train | val | test]
        ├── Fold 1:         [train | val | test]
        └── Fold N:                 [train | val | test]
            │
            ├── Train: optimize strategy params on training window
            ├── Val:   evaluate optimized params on validation window
            └── Test:  final out-of-sample test

Usage:
    from src.quantitative import WalkForwardValidator, WalkForwardConfig

    config = WalkForwardConfig(folds=5, train_ratio=0.5, val_ratio=0.2)
    validator = WalkForwardValidator(config)
    report = validator.run_on_parquet(asset="BTC")

    print(f"OoS/IS Sharpe: {report.oos_is_ratio:.2f}")
    print(f"Param stability: {report.parameter_stability:.4f}")
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

from src.backtesting.data_loader import HistoricalDataset
from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.metrics import BacktestMetrics
from src.backtesting.parquet_loader import ParquetDataLoader
from src.risk.engine import RiskEngineConfig
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig

logger = structlog.get_logger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass
class WalkForwardConfig:
    """Configuration for walk-forward analysis."""

    folds: int = 5
    """Number of sequential folds to evaluate."""

    train_ratio: float = 0.5
    """Fraction of ticks per fold window used for training/optimization.
    Must be in (0, 1)."""

    val_ratio: float = 0.2
    """Fraction of ticks per fold window used for validation.
    train_ratio + val_ratio + test_ratio must be <= 1.0."""

    min_ticks_per_fold: int = 50
    """Minimum ticks required in each fold window. If a fold has fewer ticks,
    it is skipped with a warning."""

    seed: int = 42
    """Random seed for reproducibility."""

    strategy_config_base: Optional[BuyAboveThresholdConfig] = None
    """Base strategy config. Parameter sweeps will vary threshold, stop_loss,
    target_price around these defaults."""

    sweep_thresholds: Optional[list[float]] = None
    """Thresholds to sweep during training optimization.
    Default: [0.65, 0.70, 0.75, 0.80, 0.85]."""

    sweep_stop_losses: Optional[list[float]] = None
    """Stop-loss values (as pct) to sweep. Default: [0.08, 0.10, 0.12, 0.15]."""

    sweep_targets: Optional[list[float]] = None
    """Target prices to sweep. Default: [0.85, 0.88, 0.90, 0.92, 0.95]."""

    verbose: bool = False
    """Log progress for each fold."""

    def __post_init__(self) -> None:
        if self.folds < 2:
            raise ValueError(f"Need at least 2 folds, got {self.folds}")
        if not 0 < self.train_ratio < 1:
            raise ValueError(
                f"train_ratio must be in (0, 1), got {self.train_ratio}"
            )
        if not 0 < self.val_ratio < 1:
            raise ValueError(
                f"val_ratio must be in (0, 1), got {self.val_ratio}"
            )
        if self.train_ratio + self.val_ratio >= 1.0:
            raise ValueError(
                "train_ratio + val_ratio must be < 1.0 "
                "to leave room for test_ratio"
            )
        if self.min_ticks_per_fold <= 0:
            raise ValueError(
                f"min_ticks_per_fold must be > 0, got {self.min_ticks_per_fold}"
            )

    @property
    def test_ratio(self) -> float:
        """Remaining fraction for the test window."""
        return 1.0 - self.train_ratio - self.val_ratio


# ── Results ──────────────────────────────────────────────────────────────────


@dataclass
class FoldResult:
    """Results for a single walk-forward fold."""

    fold_index: int
    """0-based fold index."""

    # ── Window boundaries (tick indices) ──
    train_start: int
    train_end: int
    val_start: int
    val_end: int
    test_start: int
    test_end: int

    # ── In-sample (training) ──
    train_trades: int = 0
    train_sharpe: float = 0.0
    train_pnl: float = 0.0
    train_win_rate: float = 0.0
    train_profit_factor: float = 0.0
    train_max_dd: float = 0.0

    # ── Best params found during training ──
    best_threshold: float = 0.0
    best_stop_loss: float = 0.0
    best_target: float = 0.0

    # ── Validation ──
    val_trades: int = 0
    val_sharpe: float = 0.0
    val_pnl: float = 0.0
    val_win_rate: float = 0.0
    val_profit_factor: float = 0.0
    val_max_dd: float = 0.0

    # ── Out-of-sample (test) ──
    oos_trades: int = 0
    oos_sharpe: float = 0.0
    oos_pnl: float = 0.0
    oos_win_rate: float = 0.0
    oos_profit_factor: float = 0.0
    oos_max_dd: float = 0.0

    # ── Metadata ──
    train_ticks: int = 0
    val_ticks: int = 0
    oos_ticks: int = 0
    fold_start_dt: Optional[str] = None
    fold_end_dt: Optional[str] = None

    @property
    def oos_is_sharpe_ratio(self) -> float:
        """Ratio of out-of-sample Sharpe to in-sample Sharpe.
        > 0.5 indicates robust strategy; < 0 indicates overfitting."""
        if self.train_sharpe == 0:
            return 0.0
        return self.oos_sharpe / self.train_sharpe

    @property
    def has_trades(self) -> bool:
        """Whether this fold produced any trades."""
        return self.train_trades > 0 or self.oos_trades > 0


@dataclass
class WalkForwardReport:
    """Aggregate report from a walk-forward validation run."""

    asset: str
    config: WalkForwardConfig
    total_ticks: int
    folds: list[FoldResult] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # ── Aggregate metrics ─────────────────────────────────────────────────

    @property
    def completed_folds(self) -> list[FoldResult]:
        """Folds that produced trades (non-empty)."""
        return [f for f in self.folds if f.has_trades]

    @property
    def oos_sharpe_mean(self) -> float:
        """Mean OoS Sharpe across folds."""
        values = [f.oos_sharpe for f in self.completed_folds]
        return sum(values) / len(values) if values else 0.0

    @property
    def oos_sharpe_std(self) -> float:
        """Standard deviation of OoS Sharpe across folds."""
        values = [f.oos_sharpe for f in self.completed_folds]
        if len(values) < 2:
            return 0.0
        mean = self.oos_sharpe_mean
        return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))

    @property
    def is_sharpe_mean(self) -> float:
        """Mean IS Sharpe across folds."""
        values = [f.train_sharpe for f in self.completed_folds]
        return sum(values) / len(values) if values else 0.0

    @property
    def oos_is_ratio(self) -> float:
        """Mean OoS/IS Sharpe ratio.
        Values:
            > 0.8: excellent generalization
            0.5–0.8: good, some degradation
            0.0–0.5: significant overfitting
            < 0:   broken (OoS loses money while IS was profitable)
        """
        ratios = [f.oos_is_sharpe_ratio for f in self.completed_folds]
        return sum(ratios) / len(ratios) if ratios else 0.0

    @property
    def oos_win_rate_mean(self) -> float:
        """Mean OoS win rate across folds."""
        values = [f.oos_win_rate for f in self.completed_folds]
        return sum(values) / len(values) if values else 0.0

    @property
    def oos_total_pnl(self) -> float:
        """Cumulative OoS PnL across all folds."""
        return sum(f.oos_pnl for f in self.completed_folds)

    @property
    def oos_total_trades(self) -> int:
        """Total OoS trades across all folds."""
        return sum(f.oos_trades for f in self.completed_folds)

    @property
    def oos_profit_factor_mean(self) -> float:
        """Mean OoS profit factor across folds."""
        values = [f.oos_profit_factor for f in self.completed_folds]
        return sum(values) / len(values) if values else 0.0

    @property
    def parameter_stability(self) -> float:
        """Coefficient of variation of optimal parameters across folds.
        Lower values indicate more stable parameter selection across time periods.
        < 0.2: excellent stability. > 0.5: unstable (likely overfitting)."""
        thresholds = [f.best_threshold for f in self.completed_folds]
        stop_losses = [f.best_stop_loss for f in self.completed_folds]
        targets = [f.best_target for f in self.completed_folds]

        if len(thresholds) < 2:
            return 0.0

        def _cv(values: list[float]) -> float:
            mean = sum(values) / len(values)
            if mean == 0:
                return 0.0
            n = len(values)
            if n < 2:
                return 0.0
            std = math.sqrt(
                sum((v - mean) ** 2 for v in values) / (n - 1)
            )
            return std / abs(mean)

        # Average CV across the three parameters
        return (_cv(thresholds) + _cv(stop_losses) + _cv(targets)) / 3.0

    @property
    def consistent_profitable(self) -> bool:
        """Whether OoS is profitable in the majority of folds."""
        profitable = sum(1 for f in self.completed_folds if f.oos_pnl > 0)
        return profitable > len(self.completed_folds) / 2 if self.completed_folds else False

    @property
    def max_oos_drawdown(self) -> float:
        """Maximum OoS drawdown across all folds."""
        values = [f.oos_max_dd for f in self.completed_folds]
        return max(values) if values else 0.0

    def to_dict(self) -> dict:
        """Serialize report to a dictionary for JSON export."""
        return {
            "asset": self.asset,
            "config": {
                "folds": self.config.folds,
                "train_ratio": self.config.train_ratio,
                "val_ratio": self.config.val_ratio,
                "seed": self.config.seed,
            },
            "total_ticks": self.total_ticks,
            "generated_at": self.generated_at,
            "completed_folds": len(self.completed_folds),
            "summary": {
                "oos_sharpe_mean": round(self.oos_sharpe_mean, 4),
                "oos_sharpe_std": round(self.oos_sharpe_std, 4),
                "is_sharpe_mean": round(self.is_sharpe_mean, 4),
                "oos_is_ratio": round(self.oos_is_ratio, 4),
                "oos_win_rate_mean": round(self.oos_win_rate_mean, 4),
                "oos_total_pnl": round(self.oos_total_pnl, 4),
                "oos_total_trades": self.oos_total_trades,
                "oos_profit_factor_mean": round(self.oos_profit_factor_mean, 4),
                "parameter_stability": round(self.parameter_stability, 4),
                "consistent_profitable": self.consistent_profitable,
                "max_oos_drawdown": round(self.max_oos_drawdown, 4),
            },
            "folds": [
                {
                    "fold": f.fold_index,
                    "train_sharpe": round(f.train_sharpe, 4),
                    "val_sharpe": round(f.val_sharpe, 4),
                    "oos_sharpe": round(f.oos_sharpe, 4),
                    "oos_is_ratio": round(f.oos_is_sharpe_ratio, 4),
                    "train_trades": f.train_trades,
                    "val_trades": f.val_trades,
                    "oos_trades": f.oos_trades,
                    "oos_pnl": round(f.oos_pnl, 4),
                    "oos_win_rate": round(f.oos_win_rate, 4),
                    "best_threshold": f.best_threshold,
                    "best_stop_loss": f.best_stop_loss,
                    "best_target": f.best_target,
                    "train_ticks": f.train_ticks,
                    "val_ticks": f.val_ticks,
                    "oos_ticks": f.oos_ticks,
                }
                for f in self.folds
            ],
        }


# ── Validator ────────────────────────────────────────────────────────────────


class WalkForwardValidator:
    """
    Anchored walk-forward validation engine.

    Splits historical tick data into sequential train/val/test folds,
    optimizes strategy parameters on each training window, validates
    on the validation window, and produces out-of-sample test results.

    Uses the existing ReplayEngine + BacktestEngine for each fold,
    ensuring consistent slippage and fill simulation.

    Stability Metrics (computed across folds):
    - OoS/IS Sharpe ratio: measures generalization
    - Parameter stability (CV): measures robustness of optimal params
    - Consistent profitability: majority of folds profitable?
    """

    def __init__(
        self,
        config: WalkForwardConfig | None = None,
        strategy_config: BuyAboveThresholdConfig | None = None,
        risk_config: RiskEngineConfig | None = None,
        initial_balance: float = 1000.0,
        parquet_base_dir: str = "data/parquet",
    ):
        self._config = config or WalkForwardConfig()
        self._strategy_config = strategy_config or BuyAboveThresholdConfig()
        self._risk_config = risk_config or RiskEngineConfig()
        self._initial_balance = initial_balance
        self._parquet_loader = ParquetDataLoader(base_dir=parquet_base_dir)

    # ── Public API ─────────────────────────────────────────────────────────

    def run_on_parquet(
        self,
        asset: str,
        market_id: Optional[str] = None,
        config: WalkForwardConfig | None = None,
    ) -> WalkForwardReport:
        """
        Run walk-forward validation on Parquet data.

        Args:
            asset: "BTC" or "ETH".
            market_id: Specific condition_id or None for all markets.
            config: Override config (uses self._config if None).

        Returns:
            WalkForwardReport with per-fold results and aggregate metrics.
        """
        cfg = config or self._config
        dataset = self._parquet_loader.load(asset=asset, market_id=market_id)
        return self.run(dataset, cfg)

    def run(
        self,
        dataset: HistoricalDataset,
        config: WalkForwardConfig | None = None,
    ) -> WalkForwardReport:
        """
        Run walk-forward validation on a pre-loaded dataset.

        The dataset is split into `folds` sequential windows. Each window
        is further divided into train/val/test segments. For each fold:

        1. Train: optimize strategy parameters on the training segment
        2. Val: evaluate the best params on the validation segment
        3. Test: final evaluation on the out-of-sample test segment

        Args:
            dataset: Pre-loaded HistoricalDataset with tick data.
            config: Override config.

        Returns:
            WalkForwardReport with full per-fold and aggregate results.
        """
        cfg = config or self._config

        logger.info(
            "walk_forward_starting",
            asset=dataset.asset,
            folds=cfg.folds,
            total_ticks=dataset.tick_count,
            train_ratio=cfg.train_ratio,
            val_ratio=cfg.val_ratio,
        )

        total_ticks = dataset.tick_count
        ticks = dataset.ticks

        # Calculate fold window size
        ticks_per_fold = max(1, total_ticks // cfg.folds)

        folds: list[FoldResult] = []

        for fold_idx in range(cfg.folds):
            # ── Compute window boundaries ──
            fold_start = fold_idx * ticks_per_fold
            fold_end = min(fold_start + ticks_per_fold, total_ticks)

            fold_window = fold_end - fold_start
            train_size = int(fold_window * cfg.train_ratio)
            val_size = int(fold_window * cfg.val_ratio)
            test_size = fold_window - train_size - val_size

            train_start = fold_start
            train_end = fold_start + train_size
            val_start = train_end
            val_end = val_start + val_size
            test_start = val_end
            test_end = fold_end

            # Skip folds with insufficient data
            if test_size < cfg.min_ticks_per_fold:
                logger.warning(
                    "walk_forward_fold_skipped",
                    fold=fold_idx,
                    test_ticks=test_size,
                    min_required=cfg.min_ticks_per_fold,
                )
                continue

            if cfg.verbose:
                print(
                    f"  Fold {fold_idx + 1}/{cfg.folds}: "
                    f"train=[{train_start}:{train_end}] "
                    f"val=[{val_start}:{val_end}] "
                    f"test=[{test_start}:{test_end}] "
                    f"({train_size}/{val_size}/{test_size} ticks)"
                )

            # ── Extract windows ──
            train_ticks = ticks[train_start:train_end]
            val_ticks = ticks[val_start:val_end]
            test_ticks = ticks[test_start:test_end]

            if not test_ticks:
                continue

            # ── Build window datasets ──
            train_ds = self._make_window_dataset(dataset, train_ticks)
            val_ds = self._make_window_dataset(dataset, val_ticks)
            test_ds = self._make_window_dataset(dataset, test_ticks)

            # ── 1. TRAIN: parameter optimization ──
            train_result, best_params = self._optimize_on_window(
                train_ds, cfg
            )

            # ── 2. VALIDATE: evaluate best params ──
            val_result = self._evaluate_with_params(
                val_ds, best_params, cfg
            )

            # ── 3. TEST: out-of-sample evaluation ──
            test_result = self._evaluate_with_params(
                test_ds, best_params, cfg
            )

            # ── Compute metrics ──
            fold = self._build_fold_result(
                fold_idx=fold_idx,
                train_start=train_start,
                train_end=train_end,
                val_start=val_start,
                val_end=val_end,
                test_start=test_start,
                test_end=test_end,
                train_result=train_result,
                val_result=val_result,
                test_result=test_result,
                best_params=best_params,
                train_ticks_count=len(train_ticks),
                val_ticks_count=len(val_ticks),
                test_ticks_count=len(test_ticks),
                test_start_dt=ticks[test_start].timestamp if test_ticks else None,
                test_end_dt=ticks[test_end - 1].timestamp if test_ticks else None,
            )
            folds.append(fold)

            if cfg.verbose:
                print(
                    f"    Train Sharpe={fold.train_sharpe:.3f} | "
                    f"Val Sharpe={fold.val_sharpe:.3f} | "
                    f"OoS Sharpe={fold.oos_sharpe:.3f} "
                    f"({fold.oos_trades} trades) | "
                    f"Best: thr={fold.best_threshold:.2f} "
                    f"sl={fold.best_stop_loss:.2f} "
                    f"tgt={fold.best_target:.2f}"
                )

        report = WalkForwardReport(
            asset=dataset.asset,
            config=cfg,
            total_ticks=total_ticks,
            folds=folds,
        )

        logger.info(
            "walk_forward_complete",
            asset=dataset.asset,
            completed_folds=len(report.completed_folds),
            oos_sharpe_mean=round(report.oos_sharpe_mean, 4),
            oos_is_ratio=round(report.oos_is_ratio, 4),
            param_stability=round(report.parameter_stability, 4),
            consistent=report.consistent_profitable,
        )

        return report

    # ── Internal: Window Management ─────────────────────────────────────────

    @staticmethod
    def _make_window_dataset(
        dataset: HistoricalDataset,
        ticks: list,
    ) -> HistoricalDataset:
        """Build a HistoricalDataset for a specific tick window."""
        if not ticks:
            return HistoricalDataset(
                asset=dataset.asset,
                window=dataset.window,
                market_id=dataset.market_id,
                ticks=[],
                start_at=dataset.start_at,
                end_at=dataset.end_at,
            )
        return HistoricalDataset(
            asset=dataset.asset,
            window=dataset.window,
            market_id=dataset.market_id,
            ticks=ticks,
            start_at=ticks[0].timestamp,
            end_at=ticks[-1].timestamp,
        )

    # ── Internal: Optimization ──────────────────────────────────────────────

    def _optimize_on_window(
        self,
        dataset: HistoricalDataset,
        cfg: WalkForwardConfig,
    ) -> tuple[Optional[BacktestResult], dict]:
        """
        Run parameter sweep on the training window.

        Returns:
            (best_result, best_params_dict) or (None, {}) if no trades.
        """
        thresholds = cfg.sweep_thresholds or [0.65, 0.70, 0.75, 0.80, 0.85]
        stop_losses = cfg.sweep_stop_losses or [0.08, 0.10, 0.12, 0.15]
        targets = cfg.sweep_targets or [0.85, 0.88, 0.90, 0.92, 0.95]

        if dataset.tick_count < cfg.min_ticks_per_fold:
            return None, {}

        engine = BacktestEngine(
            strategy_config=self._strategy_config,
            risk_config=self._risk_config,
            initial_balance=self._initial_balance,
            verbose=False,
        )

        try:
            results = engine.run_parameter_sweep(
                dataset=dataset,
                thresholds=thresholds,
                stop_losses=stop_losses,
                targets=targets,
                ticks_list=[self._strategy_config.required_ticks],
                pos_sizes=[self._strategy_config.position_size_pusd],
            )
        except Exception as e:
            logger.warning("walk_forward_sweep_failed",
                           asset=dataset.asset,
                           ticks=dataset.tick_count,
                           error=str(e))
            return None, {}

        if not results:
            return None, {}

        # Score by Sharpe ratio (prefer positive Sharpe with reasonable trades)
        scored = []
        for r in results:
            metrics = BacktestMetrics(r).compute_all()
            sharpe = metrics["risk"]["sharpe_ratio"]
            trades = metrics["summary"]["closed_positions"]
            # Penalize results with too few trades
            score = sharpe if trades >= 3 else sharpe * 0.5
            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_result = scored[0][1]

        best_params = {
            "threshold": best_result.config.threshold,
            "stop_loss_pct": best_result.config.stop_loss_pct,
            "target_price": best_result.config.target_price,
            "required_ticks": best_result.config.required_ticks,
            "position_size_pusd": best_result.config.position_size_pusd,
        }

        return best_result, best_params

    # ── Internal: Evaluation ────────────────────────────────────────────────

    def _evaluate_with_params(
        self,
        dataset: HistoricalDataset,
        params: dict,
        cfg: WalkForwardConfig,
    ) -> Optional[BacktestResult]:
        """
        Run a backtest with fixed parameters on a validation/test window.

        Returns BacktestResult or None if no trades / empty dataset.
        """
        if not params or dataset.tick_count < cfg.min_ticks_per_fold:
            return None

        eval_config = BuyAboveThresholdConfig(
            threshold=params.get("threshold", self._strategy_config.threshold),
            stop_loss_pct=params.get("stop_loss_pct", self._strategy_config.stop_loss_pct),
            target_price=params.get("target_price", self._strategy_config.target_price),
            required_ticks=params.get("required_ticks", self._strategy_config.required_ticks),
            position_size_pusd=params.get(
                "position_size_pusd", self._strategy_config.position_size_pusd
            ),
            max_spread=self._strategy_config.max_spread,
            min_volume_pusd=self._strategy_config.min_volume_pusd,
        )

        engine = BacktestEngine(
            strategy_config=eval_config,
            risk_config=self._risk_config,
            initial_balance=self._initial_balance,
            verbose=False,
        )

        try:
            return engine.run(dataset)
        except Exception as e:
            logger.warning("walk_forward_eval_failed",
                           asset=dataset.asset,
                           ticks=dataset.tick_count,
                           error=str(e))
            return None

    # ── Internal: Result Building ───────────────────────────────────────────

    def _build_fold_result(
        self,
        fold_idx: int,
        train_start: int,
        train_end: int,
        val_start: int,
        val_end: int,
        test_start: int,
        test_end: int,
        train_result: Optional[BacktestResult],
        val_result: Optional[BacktestResult],
        test_result: Optional[BacktestResult],
        best_params: dict,
        train_ticks_count: int,
        val_ticks_count: int,
        test_ticks_count: int,
        test_start_dt: Optional[datetime],
        test_end_dt: Optional[datetime],
    ) -> FoldResult:
        """Build a FoldResult from backtest results and metadata."""

        def _extract(result: Optional[BacktestResult]) -> dict:
            if result is None:
                return {"trades": 0, "sharpe": 0.0, "pnl": 0.0,
                        "wr": 0.0, "pf": 0.0, "max_dd": 0.0}
            metrics = BacktestMetrics(result).compute_all()
            return {
                "trades": metrics["summary"]["closed_positions"],
                "sharpe": metrics["risk"]["sharpe_ratio"],
                "pnl": metrics["pnl"]["total_pnl_usdc"],
                "wr": metrics["performance"]["win_rate"],
                "pf": metrics["performance"]["profit_factor"],
                "max_dd": metrics["risk"]["max_drawdown_pct"],
            }

        train_m = _extract(train_result)
        val_m = _extract(val_result)
        test_m = _extract(test_result)

        return FoldResult(
            fold_index=fold_idx,
            train_start=train_start,
            train_end=train_end,
            val_start=val_start,
            val_end=val_end,
            test_start=test_start,
            test_end=test_end,
            train_trades=train_m["trades"],
            train_sharpe=train_m["sharpe"],
            train_pnl=train_m["pnl"],
            train_win_rate=train_m["wr"],
            train_profit_factor=train_m["pf"],
            train_max_dd=train_m["max_dd"],
            best_threshold=best_params.get("threshold", 0.0),
            best_stop_loss=best_params.get("stop_loss_pct", 0.0),
            best_target=best_params.get("target_price", 0.0),
            val_trades=val_m["trades"],
            val_sharpe=val_m["sharpe"],
            val_pnl=val_m["pnl"],
            val_win_rate=val_m["wr"],
            val_profit_factor=val_m["pf"],
            val_max_dd=val_m["max_dd"],
            oos_trades=test_m["trades"],
            oos_sharpe=test_m["sharpe"],
            oos_pnl=test_m["pnl"],
            oos_win_rate=test_m["wr"],
            oos_profit_factor=test_m["pf"],
            oos_max_dd=test_m["max_dd"],
            train_ticks=train_ticks_count,
            val_ticks=val_ticks_count,
            oos_ticks=test_ticks_count,
            fold_start_dt=test_start_dt.isoformat() if test_start_dt else None,
            fold_end_dt=test_end_dt.isoformat() if test_end_dt else None,
        )
