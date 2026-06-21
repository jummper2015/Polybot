#!/usr/bin/env python3
"""
Walk-forward validation para MeanReversion (R1.2-ter, Eslabón 1 del protocolo).

Reusa el motor inline de scripts/optimize_mr.py (run_mr_sweep + run_mr_backtest)
porque WalkForwardValidator en src/quantitative/walk_forward.py está hard-coded
para BuyAboveThreshold (sweep_thresholds / sweep_targets / sweep_stop_losses).

Para cada uno de los N folds (default 5):
  1. TRAIN: sweep MR sobre el tramo de entrenamiento (train_ratio del fold).
  2. SELECT: mejor combo por Sharpe condicionado a min_trades.
  3. OOS:   un único backtest sobre el tramo de test (1 - train_ratio del fold)
            con los params seleccionados.

Output: reporte JSON con per-fold + agregados (mediana OOS Sharpe, estabilidad
de parámetros vía coeficient of variation, % folds rentables).

Uso:
  python scripts/walk_forward_mr.py --asset BTC --folds 5 --quick
  python scripts/walk_forward_mr.py --asset ETH --folds 5 --full --n-ticks 500000
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.optimize_mr import (  # noqa: E402
    FULL_ENTRY_ZSCORES,
    FULL_EXIT_ZSCORES,
    FULL_MA_WINDOWS,
    FULL_POS_SIZES,
    FULL_STOP_LOSSES,
    FULL_TIMEOUT_MINUTES,
    QUICK_ENTRY_ZSCORES,
    QUICK_EXIT_ZSCORES,
    QUICK_MA_WINDOWS,
    QUICK_POS_SIZES,
    QUICK_STOP_LOSSES,
    QUICK_TIMEOUT_MINUTES,
    MRResult,
    load_parquet_ticks,
    run_mr_backtest,
    run_mr_sweep,
)

REPORTS_DIR = Path("data/reports")


@dataclass
class FoldOutcome:
    """Resultado de un fold de walk-forward para MR."""

    fold_index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    # Parámetros seleccionados en train (mejor Sharpe con min_trades)
    best_ma: int = 0
    best_entry_z: float = 0.0
    best_exit_z: float = 0.0
    best_stop_loss: float = 0.0
    best_timeout_min: float = 0.0
    best_pos_size: float = 0.0

    # In-sample (train)
    train_trades: int = 0
    train_sharpe: float = 0.0
    train_pnl: float = 0.0
    train_pf: float = 0.0
    train_wr: float = 0.0
    train_max_dd: float = 0.0

    # Out-of-sample (test)
    oos_trades: int = 0
    oos_sharpe: float = 0.0
    oos_pnl: float = 0.0
    oos_pf: float = 0.0
    oos_wr: float = 0.0
    oos_max_dd: float = 0.0

    # Diagnóstico
    skipped: bool = False
    skip_reason: str = ""

    @property
    def has_oos_trades(self) -> bool:
        return self.oos_trades > 0


@dataclass
class WalkForwardMRReport:
    """Reporte agregado del walk-forward MR."""

    asset: str
    folds_requested: int
    total_ticks: int
    train_ratio: float
    min_trades: int
    mode: str  # quick|full
    n_ticks_used: int
    parquet_dir: str
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    folds: list[FoldOutcome] = field(default_factory=list)

    @property
    def completed_folds(self) -> list[FoldOutcome]:
        return [f for f in self.folds if not f.skipped and f.oos_trades > 0]

    # ── Métricas agregadas ──
    def oos_sharpe_values(self) -> list[float]:
        return [f.oos_sharpe for f in self.completed_folds]

    def oos_sharpe_median(self) -> float:
        vals = self.oos_sharpe_values()
        return _median(vals)

    def oos_sharpe_mean(self) -> float:
        vals = self.oos_sharpe_values()
        return sum(vals) / len(vals) if vals else 0.0

    def profitable_fold_pct(self) -> float:
        completed = self.completed_folds
        if not completed:
            return 0.0
        profitable = sum(1 for f in completed if f.oos_pnl > 0)
        return profitable / len(completed)

    def parameter_stability(self) -> dict:
        """Coefficient of variation por parámetro entre folds completados.
        CV bajo (< 0.30) = parámetros estables; CV alto = overfitting fold-a-fold.
        """
        completed = self.completed_folds
        if len(completed) < 2:
            return {
                "ma_cv": 0.0,
                "entry_z_cv": 0.0,
                "exit_z_cv": 0.0,
                "stop_loss_cv": 0.0,
                "timeout_cv": 0.0,
                "n_folds_for_cv": len(completed),
            }
        return {
            "ma_cv":         _cv([f.best_ma for f in completed]),
            "entry_z_cv":    _cv([f.best_entry_z for f in completed]),
            "exit_z_cv":     _cv([f.best_exit_z for f in completed]),
            "stop_loss_cv":  _cv([f.best_stop_loss for f in completed]),
            "timeout_cv":    _cv([f.best_timeout_min for f in completed]),
            "n_folds_for_cv": len(completed),
        }

    def passes_protocol(self) -> dict:
        """Aplica los criterios del strategy-validation-protocol skill:
        - ≥ 5 folds completados con trades
        - Sharpe OOS mediana > 0.8
        - Parámetros estables (CV < 0.30 en la mediana)
        """
        completed = self.completed_folds
        med_sharpe = self.oos_sharpe_median()
        cvs = self.parameter_stability()
        cv_values = [v for k, v in cvs.items() if k.endswith("_cv")]
        median_cv = _median(cv_values) if cv_values else 0.0

        criteria = {
            "n_folds_completed": len(completed),
            "n_folds_required": 5,
            "passes_fold_count": len(completed) >= 5,
            "oos_sharpe_median": round(med_sharpe, 4),
            "passes_sharpe_threshold": med_sharpe > 0.8,
            "param_median_cv": round(median_cv, 4),
            "passes_stability": median_cv < 0.30,
        }
        criteria["all_pass"] = (
            criteria["passes_fold_count"]
            and criteria["passes_sharpe_threshold"]
            and criteria["passes_stability"]
        )
        return criteria

    def to_dict(self) -> dict:
        return {
            "asset": self.asset,
            "folds_requested": self.folds_requested,
            "total_ticks": self.total_ticks,
            "train_ratio": self.train_ratio,
            "min_trades": self.min_trades,
            "mode": self.mode,
            "n_ticks_used": self.n_ticks_used,
            "parquet_dir": self.parquet_dir,
            "generated_at": self.generated_at,
            "aggregate": {
                "n_folds_completed": len(self.completed_folds),
                "oos_sharpe_median": round(self.oos_sharpe_median(), 4),
                "oos_sharpe_mean": round(self.oos_sharpe_mean(), 4),
                "profitable_fold_pct": round(self.profitable_fold_pct(), 4),
                "parameter_stability": self.parameter_stability(),
                "protocol_check": self.passes_protocol(),
            },
            "folds": [asdict(f) for f in self.folds],
        }


# ── Helpers ──────────────────────────────────────────────────────────


def _cv(values: list[float]) -> float:
    """Coefficient of variation = std / |mean|. 0 si mean es 0."""
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance) / abs(mean)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 == 1 else (s[mid - 1] + s[mid]) / 2.0


def _select_best_train_combo(
    results: list[MRResult], min_trades: int
) -> MRResult | None:
    """Selecciona el mejor combo del train sweep por Sharpe, condicionado a
    un mínimo de trades para evitar combos lucky con ~3 trades."""
    eligible = [r for r in results if r.total_trades >= min_trades]
    if not eligible:
        # Fallback: el mejor Sharpe sin restricción de trades.
        eligible = [r for r in results if r.total_trades > 0]
    if not eligible:
        return None
    return max(eligible, key=lambda r: r.sharpe_ratio)


# ── Núcleo: ejecución de un fold ─────────────────────────────────────


def _run_fold(
    fold_idx: int,
    ticks: list,
    train_start: int,
    train_end: int,
    test_start: int,
    test_end: int,
    ma_windows: list[int],
    entry_zscores: list[float],
    exit_zscores: list[float],
    stop_losses: list[float],
    timeouts: list[float],
    pos_sizes: list[float],
    balance: float,
    min_trades: int,
    verbose: bool,
) -> FoldOutcome:
    fold = FoldOutcome(
        fold_index=fold_idx,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
    )

    train_ticks = ticks[train_start:train_end]
    test_ticks = ticks[test_start:test_end]

    if len(train_ticks) < 100 or len(test_ticks) < 100:
        fold.skipped = True
        fold.skip_reason = (
            f"too few ticks (train={len(train_ticks)}, test={len(test_ticks)})"
        )
        return fold

    # 1. TRAIN: sweep MR sobre el tramo de entrenamiento.
    if verbose:
        print(
            f"  Fold {fold_idx + 1}: train=[{train_start}:{train_end}] "
            f"({len(train_ticks)} ticks)  test=[{test_start}:{test_end}] "
            f"({len(test_ticks)} ticks)"
        )

    train_results = run_mr_sweep(
        ticks=train_ticks,
        ma_windows=ma_windows,
        entry_zscores=entry_zscores,
        exit_zscores=exit_zscores,
        stop_losses=stop_losses,
        timeout_minutes_list=timeouts,
        pos_sizes=pos_sizes,
        balance=balance,
        label=f"fold{fold_idx + 1}_train",
    )

    best = _select_best_train_combo(train_results, min_trades=min_trades)
    if best is None:
        fold.skipped = True
        fold.skip_reason = "no train combo with trades"
        return fold

    fold.best_ma = best.ma_window
    fold.best_entry_z = best.entry_zscore
    fold.best_exit_z = best.exit_zscore
    fold.best_stop_loss = best.stop_loss_pct
    fold.best_timeout_min = best.timeout_minutes
    fold.best_pos_size = best.position_size_pusd

    fold.train_trades = best.total_trades
    fold.train_sharpe = best.sharpe_ratio
    fold.train_pnl = best.total_pnl
    fold.train_pf = best.profit_factor if math.isfinite(best.profit_factor) else 0.0
    fold.train_wr = best.win_rate
    fold.train_max_dd = best.max_drawdown

    # 2. OOS: backtest con params fijos sobre el tramo de test.
    oos = run_mr_backtest(
        ticks=test_ticks,
        entry_zscore=fold.best_entry_z,
        exit_zscore=fold.best_exit_z,
        ma_window=fold.best_ma,
        stop_loss_pct=fold.best_stop_loss,
        timeout_minutes=fold.best_timeout_min,
        position_size_pusd=fold.best_pos_size,
        initial_balance=balance,
    )

    fold.oos_trades = oos.total_trades
    fold.oos_sharpe = oos.sharpe_ratio
    fold.oos_pnl = oos.total_pnl
    fold.oos_pf = oos.profit_factor if math.isfinite(oos.profit_factor) else 0.0
    fold.oos_wr = oos.win_rate
    fold.oos_max_dd = oos.max_drawdown

    if verbose:
        print(
            f"    best: ma={fold.best_ma} ez={fold.best_entry_z:.1f} "
            f"xz={fold.best_exit_z:.1f} sl={fold.best_stop_loss:.0%} "
            f"tm={fold.best_timeout_min:.0f}m  "
            f"train_sharpe={fold.train_sharpe:.3f}  "
            f"oos_sharpe={fold.oos_sharpe:.3f}  "
            f"oos_trades={fold.oos_trades}  oos_pnl={fold.oos_pnl:+.2f}"
        )

    return fold


# ── CLI ──────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Walk-forward validation para MeanReversion sobre Parquet real."
    )
    parser.add_argument("--asset", required=True, choices=["BTC", "ETH"])
    parser.add_argument(
        "--folds", type=int, default=5,
        help="Número de folds (default 5; protocolo exige ≥5).",
    )
    parser.add_argument(
        "--train-ratio", type=float, default=0.7,
        help="Fracción del fold para train; resto = OOS test (default 0.7).",
    )
    parser.add_argument(
        "--n-ticks", type=int, default=500000,
        help="Cap de ticks a usar (toma los últimos N) (default 500000).",
    )
    parser.add_argument(
        "--min-trades", type=int, default=10,
        help="Mín trades en train para considerar un combo elegible (default 10).",
    )
    parser.add_argument(
        "--balance", type=float, default=1000.0,
        help="Balance inicial USDC (default 1000).",
    )
    parser.add_argument(
        "--parquet-dir", default="data/parquet",
        help="Directorio raíz de parquets (default data/parquet).",
    )
    parser.add_argument("--quick", action="store_true", help="Param grid QUICK")
    parser.add_argument("--full", action="store_true", help="Param grid FULL")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--output-dir", default="data/reports",
        help="Donde escribir el reporte JSON (default data/reports).",
    )
    args = parser.parse_args()
    if not args.quick and not args.full:
        args.quick = True  # Default
    return args


def main() -> int:
    args = parse_args()

    print("=" * 70)
    print(f"  WALK-FORWARD MR — {args.asset} — {args.folds} folds — "
          f"{'QUICK' if args.quick else 'FULL'}")
    print("=" * 70)

    # ── Cargar ticks ──
    print(f"  Loading {args.asset} ticks from {args.parquet_dir} …")
    t0 = time.monotonic()
    all_ticks = load_parquet_ticks(args.asset, window="raw", parquet_dir=args.parquet_dir)
    if not all_ticks:
        print(f"  ❌ No ticks for {args.asset}.")
        return 2
    load_time = time.monotonic() - t0
    print(f"  Loaded {len(all_ticks):,} ticks in {load_time:.1f}s.")

    # Recorta a los últimos N para hacerlo tractable (mismo enfoque que optimize_mr)
    if len(all_ticks) > args.n_ticks:
        all_ticks = all_ticks[-args.n_ticks:]
        print(f"  Trimmed to last {len(all_ticks):,} ticks.")

    total = len(all_ticks)
    ticks_per_fold = total // args.folds
    print(f"  Window per fold: {ticks_per_fold:,} ticks. "
          f"Train ratio: {args.train_ratio:.0%}.")

    # ── Param grids ──
    if args.quick:
        ma_windows, ez, xz, sl, tm, ps = (
            QUICK_MA_WINDOWS, QUICK_ENTRY_ZSCORES, QUICK_EXIT_ZSCORES,
            QUICK_STOP_LOSSES, QUICK_TIMEOUT_MINUTES, QUICK_POS_SIZES,
        )
    else:
        ma_windows, ez, xz, sl, tm, ps = (
            FULL_MA_WINDOWS, FULL_ENTRY_ZSCORES, FULL_EXIT_ZSCORES,
            FULL_STOP_LOSSES, FULL_TIMEOUT_MINUTES, FULL_POS_SIZES,
        )

    # ── Ejecutar folds ──
    report = WalkForwardMRReport(
        asset=args.asset,
        folds_requested=args.folds,
        total_ticks=total,
        train_ratio=args.train_ratio,
        min_trades=args.min_trades,
        mode="quick" if args.quick else "full",
        n_ticks_used=total,
        parquet_dir=args.parquet_dir,
    )

    t_total = time.monotonic()
    for fold_idx in range(args.folds):
        fold_start = fold_idx * ticks_per_fold
        fold_end = min(fold_start + ticks_per_fold, total)
        train_size = int((fold_end - fold_start) * args.train_ratio)
        train_start = fold_start
        train_end = fold_start + train_size
        test_start = train_end
        test_end = fold_end

        fold = _run_fold(
            fold_idx=fold_idx,
            ticks=all_ticks,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            ma_windows=ma_windows,
            entry_zscores=ez,
            exit_zscores=xz,
            stop_losses=sl,
            timeouts=tm,
            pos_sizes=ps,
            balance=args.balance,
            min_trades=args.min_trades,
            verbose=args.verbose,
        )
        report.folds.append(fold)

    elapsed = time.monotonic() - t_total

    # ── Resumen humano ──
    print()
    print("=" * 70)
    print(
        f"  RESULT — {args.asset} — "
        f"{len(report.completed_folds)}/{args.folds} folds with OOS trades"
    )
    print("=" * 70)
    for f in report.folds:
        if f.skipped:
            print(f"  Fold {f.fold_index + 1}: SKIPPED ({f.skip_reason})")
            continue
        print(
            f"  Fold {f.fold_index + 1}: "
            f"ma={f.best_ma} ez={f.best_entry_z:+.1f} xz={f.best_exit_z:+.1f} "
            f"sl={f.best_stop_loss:.0%}  "
            f"train_sharpe={f.train_sharpe:+.3f}  oos_sharpe={f.oos_sharpe:+.3f}  "
            f"oos_trades={f.oos_trades}  oos_pnl={f.oos_pnl:+.2f}  "
            f"oos_dd={f.oos_max_dd:.1%}"
        )

    agg = report.to_dict()["aggregate"]
    print()
    print(f"  OOS Sharpe median: {agg['oos_sharpe_median']:+.3f}")
    print(f"  OOS Sharpe mean:   {agg['oos_sharpe_mean']:+.3f}")
    print(f"  Profitable folds:  {agg['profitable_fold_pct']:.0%}")
    print(f"  Param CV (median): {agg['protocol_check']['param_median_cv']:.3f}")

    checks = agg["protocol_check"]
    print()
    print("  Protocol checks (strategy-validation-protocol):")
    print(f"    {'✅' if checks['passes_fold_count'] else '❌'} "
          f"folds completed ≥ 5: {checks['n_folds_completed']}")
    print(f"    {'✅' if checks['passes_sharpe_threshold'] else '❌'} "
          f"OOS Sharpe median > 0.8: {checks['oos_sharpe_median']:+.3f}")
    print(f"    {'✅' if checks['passes_stability'] else '❌'} "
          f"param median CV < 0.30: {checks['param_median_cv']:.3f}")
    print(f"  → {'ALL PASS' if checks['all_pass'] else 'INCOMPLETE'}")
    print(f"  Elapsed: {elapsed:.1f}s")

    # ── Persistir ──
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"walk_forward_mr_{args.asset}_{ts}.json"
    with open(output_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2, default=str)
    print(f"\n  💾 Report: {output_path}")

    # Latest symlink
    latest_path = output_dir / f"walk_forward_mr_{args.asset}_latest.json"
    try:
        if latest_path.exists() or latest_path.is_symlink():
            latest_path.unlink()
        latest_path.symlink_to(output_path.name)
    except OSError:
        pass

    return 0 if checks["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
