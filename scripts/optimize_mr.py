#!/usr/bin/env python3
"""
MeanReversion Strategy Parameter Optimizer.

Runs exhaustive parameter sweeps across all 4 market datasets
(BTC/ETH × 5m/15m) to find optimal MeanReversion parameters.

Uses the same realistic synthetic data generator as optimize_bat.py.
The MR strategy is fundamentally better suited for prediction markets
than BAT because prediction markets are mean-reverting by nature.

Usage:
    python scripts/optimize_mr.py
    python scripts/optimize_mr.py --quick          # Fast mode (~2 min)
    python scripts/optimize_mr.py --full            # Full sweep (~15 min)
    python scripts/optimize_mr.py --dataset BTC_5m  # Single dataset

Output:
    data/optimization/optimal_params_mr.json   # Best parameters found
    data/optimization/sweep_mr_*.csv           # Full sweep results per dataset
"""

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Reuse the realistic data generator from optimize_bat.py (canonical source)
from scripts.optimize_bat import generate_realistic_dataset as _gen_bat_dataset
from src.backtesting.parquet_loader import ParquetDataLoader
from src.domain.value_objects.market_tick import MarketTick

OUTPUT_DIR = Path("data/optimization")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── SWEEP PARAMETERS ──────────────────────────────────────────────────
FULL_MA_WINDOWS      = [10, 15, 20, 30, 40]
FULL_ENTRY_ZSCORES   = [-3.0, -2.5, -2.0, -1.5, -1.0]
FULL_EXIT_ZSCORES    = [-1.0, -0.5, 0.0, 0.5, 1.0]
FULL_STOP_LOSSES     = [0.05, 0.08, 0.10, 0.12, 0.15]
FULL_TIMEOUT_MINUTES = [30, 45, 60, 90]
FULL_POS_SIZES       = [5, 10, 15]

QUICK_MA_WINDOWS     = [10, 20, 30]
QUICK_ENTRY_ZSCORES  = [-2.5, -2.0, -1.5]
QUICK_EXIT_ZSCORES   = [-0.5, 0.0, 0.5]
QUICK_STOP_LOSSES    = [0.08, 0.10, 0.15]
QUICK_TIMEOUT_MINUTES = [45, 60]
QUICK_POS_SIZES      = [5, 10]

DATASETS = {
    "BTC_5m":  {"asset": "BTC", "window": "5m"},
    "BTC_15m": {"asset": "BTC", "window": "15m"},
    "ETH_5m":  {"asset": "ETH", "window": "5m"},
    "ETH_15m": {"asset": "ETH", "window": "15m"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MeanReversion Strategy Parameter Optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--quick", action="store_true",
                        help="Quick sweep (~180 configs/dataset, ~2 min total)")
    parser.add_argument("--full", action="store_true",
                        help="Full sweep (~4000 configs/dataset, ~15 min total)")
    parser.add_argument("--dataset", choices=list(DATASETS.keys()),
                        help="Optimize for a single dataset only")
    parser.add_argument("--n-ticks", type=int, default=3000,
                        help="Number of synthetic ticks per dataset (default: 3000)")
    parser.add_argument("--balance", type=float, default=1000.0,
                        help="Initial balance in USDC (default: 1000)")
    parser.add_argument("--parquet-dir", type=str, default=None,
                        help="Load real data from Parquet dir instead of synthetic")
    return parser.parse_args()


# ── SHARED DATA GENERATOR (imported from optimize_bat) ────────────────

def load_parquet_ticks(
    asset: str,
    window: str,
    parquet_dir: str = "data/parquet",
) -> list[MarketTick]:
    """
    Load real MarketTick data from Parquet files.

    Uses ParquetDataLoader to read all tick data for an asset.
    Returns ticks sorted by timestamp.
    """
    loader = ParquetDataLoader(base_dir=parquet_dir)
    try:
        dataset = loader.load(asset=asset, window=window)
        if dataset.tick_count == 0:
            print(f"  ⚠️  No ticks for {asset}/{window} — skipping")
            return []
        print(f"     Loaded {dataset.tick_count} real ticks for {asset}/{window}")
        return dataset.ticks
    except FileNotFoundError:
        print(f"  ⚠️  No Parquet files for {asset} — skipping")
        return []
    except Exception as e:
        print(f"  ⚠️  Error loading {asset}/{window}: {e} — skipping")
        return []


def load_real_dataset(
    asset: str,
    window: str,
    n_ticks: int = 3000,
    parquet_dir: str = "data/parquet",
) -> list[MarketTick]:
    """
    Load real data from Parquet, falling back to synthetic if unavailable.

    If real data exists, samples up to n_ticks (chronologically).
    If insufficient real data, uses all available ticks.
    If no real data, falls back to synthetic generation.
    """
    ticks = load_parquet_ticks(asset, window, parquet_dir)

    if ticks:
        # Sample up to n_ticks (use most recent for relevance)
        if len(ticks) > n_ticks:
            ticks = ticks[-n_ticks:]
        print(f"     Using {len(ticks)} ticks (real Parquet data)")
        return ticks

    # Fallback to synthetic
    print(f"     No real data for {asset}/{window} — generating synthetic fallback")
    return generate_realistic_dataset(
        asset=asset, window=window, n_ticks=n_ticks, save_csv=False
    )


def generate_realistic_dataset(
    asset: str,
    window: str,
    n_ticks: int = 3000,
    save_csv: bool = False,
) -> list[MarketTick]:
    """
    Generate synthetic data via optimize_bat.py's canonical generator
    and return ticks for MR backtesting.
    """
    dataset = _gen_bat_dataset(
        asset=asset, window=window, n_ticks=n_ticks, save_csv=save_csv
    )
    return dataset.ticks


# ── MEAN REVERSION BACKTEST ENGINE ────────────────────────────────────

@dataclass
class MRPosition:
    """Lightweight position tracking for MR backtest."""
    entry_price: float
    entry_tick: int
    entry_at: datetime
    amount: float
    shares: float
    exit_price: float | None = None
    exit_tick: int | None = None
    exit_reason: str | None = None
    pnl: float = 0.0

    def close(self, price: float, tick_idx: int, ts: datetime, reason: str) -> None:
        self.exit_price = price
        self.exit_tick = tick_idx
        self.exit_reason = reason
        self.pnl = (price - self.entry_price) * self.shares


def _compute_sma(prices: list[float], window: int) -> float:
    """Simple Moving Average."""
    if len(prices) < window:
        return 0.0
    return sum(prices[-window:]) / window


def _compute_zscore(price: float, prices: list[float], window: int) -> float:
    """Z-score: (price - SMA) / std."""
    if len(prices) < window:
        return 0.0
    recent = prices[-window:]
    sma = sum(recent) / window
    variance = sum((p - sma) ** 2 for p in recent) / window
    std = variance ** 0.5
    if std < 1e-10:
        return 0.0
    return (price - sma) / std


@dataclass
class MRResult:
    """Result of a single MR backtest run."""
    entry_zscore: float
    exit_zscore: float
    ma_window: int
    stop_loss_pct: float
    timeout_minutes: float
    position_size_pusd: float
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    avg_trade_pnl: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    exit_reasons: dict = field(default_factory=dict)
    # Individual PnL per closed trade — exposed for downstream consumers
    # (Monte Carlo resampling, post-trade analytics) so they never need to
    # re-run the backtest loop or duplicate filter/share-calculation logic.
    trade_pnls: list[float] = field(default_factory=list)


def run_mr_backtest(
    ticks: list[MarketTick],
    entry_zscore: float,
    exit_zscore: float,
    ma_window: int,
    stop_loss_pct: float,
    timeout_minutes: float,
    position_size_pusd: float,
    initial_balance: float = 1000.0,
    max_spread: float = 0.03,
    min_volume: float = 500.0,
) -> MRResult:
    """
    Run a MeanReversion backtest over a list of ticks.

    Logic:
    - Enter when z_score < entry_zscore (oversold) + filters pass
    - Exit when:
      1. Stop loss: loss >= stop_loss_pct
      2. Mean reversion: z_score > exit_zscore
      3. Timeout: minutes_in_position >= timeout_minutes
      4. End of dataset (forced close)

    Returns MRResult with all metrics computed.
    """
    result = MRResult(
        entry_zscore=entry_zscore,
        exit_zscore=exit_zscore,
        ma_window=ma_window,
        stop_loss_pct=stop_loss_pct,
        timeout_minutes=timeout_minutes,
        position_size_pusd=position_size_pusd,
    )

    balance = initial_balance
    position: MRPosition | None = None
    trades: list[MRPosition] = []
    prices_history: list[float] = []
    equity_curve: list[float] = [initial_balance]

    for tick_idx, tick in enumerate(ticks):
        prices_history.append(tick.yes_price)

        # ── Skip tick if spread/volume filters fail ──────────────────
        if tick.spread > max_spread:
            continue
        if tick.volume_24h < min_volume:
            continue

        # ── Check exit if in position ────────────────────────────────
        if position is not None:
            entry = position.entry_price
            current = tick.yes_price
            minutes_in = (tick.timestamp - position.entry_at).total_seconds() / 60.0
            z_score = _compute_zscore(current, prices_history, ma_window)

            should_exit = False
            exit_reason = ""

            # 1. Stop Loss
            loss_pct = (current - entry) / entry if entry > 0 else 0
            if loss_pct <= -stop_loss_pct:
                should_exit = True
                exit_reason = "stop_loss"

            # 2. Mean Reversion
            elif z_score > exit_zscore and len(prices_history) >= ma_window:
                should_exit = True
                exit_reason = "mean_reverted"

            # 3. Timeout
            elif minutes_in >= timeout_minutes:
                should_exit = True
                exit_reason = "timeout"

            if should_exit:
                slippage = tick.spread * 0.5
                exit_price = max(current - slippage, 0.001)
                position.close(exit_price, tick_idx, tick.timestamp, exit_reason)
                balance += position.shares * exit_price
                trades.append(position)
                position = None
                equity_curve.append(balance)
                continue

        # ── Check entry if not in position ───────────────────────────
        if position is None:
            z_score = _compute_zscore(tick.yes_price, prices_history, ma_window)

            if z_score < entry_zscore and len(prices_history) >= ma_window:
                # Enter position
                amount = position_size_pusd
                slippage = tick.spread * 0.5
                fill_price = min(tick.yes_price + slippage, 0.999)
                shares = amount / fill_price
                balance -= amount

                position = MRPosition(
                    entry_price=fill_price,
                    entry_tick=tick_idx,
                    entry_at=tick.timestamp,
                    amount=amount,
                    shares=shares,
                )
                equity_curve.append(balance)

    # ── Force close at end of dataset ────────────────────────────────
    if position is not None and ticks:
        last_tick = ticks[-1]
        exit_price = max(last_tick.yes_price - last_tick.spread * 0.5, 0.001)
        position.close(exit_price, len(ticks) - 1, last_tick.timestamp,
                       "dataset_end")
        balance += position.shares * exit_price
        trades.append(position)
        equity_curve.append(balance)

    # ── Compute metrics ──────────────────────────────────────────────
    result.total_trades = len(trades)
    if result.total_trades == 0:
        return result

    pnls = [t.pnl for t in trades]
    result.trade_pnls = pnls
    result.total_pnl = sum(pnls)
    result.winners = sum(1 for p in pnls if p > 0)
    result.losers = sum(1 for p in pnls if p <= 0)
    result.win_rate = result.winners / result.total_trades
    result.avg_trade_pnl = result.total_pnl / result.total_trades
    result.best_trade = max(pnls)
    result.worst_trade = min(pnls)

    # Profit Factor
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else 0.0
    )

    # Sharpe Ratio
    if result.total_trades >= 2:
        mean_pnl = result.total_pnl / result.total_trades
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (result.total_trades - 1)
        std_pnl = variance ** 0.5
        result.sharpe_ratio = (mean_pnl / std_pnl) if std_pnl > 0 else 0.0
    else:
        result.sharpe_ratio = 0.0

    # Max Drawdown from equity curve
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    result.max_drawdown = max_dd

    # Exit reasons
    reasons: dict[str, int] = {}
    for t in trades:
        r = t.exit_reason or "unknown"
        reasons[r] = reasons.get(r, 0) + 1
    result.exit_reasons = reasons

    return result


def run_mr_sweep(
    ticks: list[MarketTick],
    ma_windows: list[int],
    entry_zscores: list[float],
    exit_zscores: list[float],
    stop_losses: list[float],
    timeout_minutes_list: list[float],
    pos_sizes: list[float],
    balance: float,
    label: str,
) -> list[MRResult]:
    """Run parameter sweep for MeanReversion on a single dataset."""
    print(f"\n  🔍 Running MR sweep for {label}...")
    print(f"     {len(ticks)} ticks")

    combos = (len(ma_windows) * len(entry_zscores) * len(exit_zscores) *
              len(stop_losses) * len(timeout_minutes_list) * len(pos_sizes))
    valid_combos = sum(1
        for mw in ma_windows
        for ez in entry_zscores
        for xz in exit_zscores
        for sl in stop_losses
        for tm in timeout_minutes_list
        for ps in pos_sizes
        if ez < xz  # entry_zscore must be less than exit_zscore
    )
    print(f"     Running {valid_combos} valid combinations (of {combos} total)...")

    t0 = time.monotonic()
    results: list[MRResult] = []
    count = 0

    for mw in ma_windows:
        for ez in entry_zscores:
            for xz in exit_zscores:
                if ez >= xz:
                    continue
                for sl in stop_losses:
                    for tm in timeout_minutes_list:
                        for ps in pos_sizes:
                            count += 1
                            r = run_mr_backtest(
                                ticks=ticks,
                                entry_zscore=ez,
                                exit_zscore=xz,
                                ma_window=mw,
                                stop_loss_pct=sl,
                                timeout_minutes=tm,
                                position_size_pusd=ps,
                                initial_balance=balance,
                            )
                            results.append(r)

    elapsed = time.monotonic() - t0
    valid = sum(1 for r in results if r.total_trades > 0)
    print(f"     {len(results)} results in {elapsed:.1f}s")
    print(f"     {valid} configs with trades")

    # Sort by Sharpe
    results.sort(key=lambda r: r.sharpe_ratio, reverse=True)

    if results:
        top = results[0]
        print(f"     Best: ma={top.ma_window} entry_z={top.entry_zscore:.1f} "
              f"exit_z={top.exit_zscore:.1f} sl={top.stop_loss_pct:.0%} "
              f"timeout={top.timeout_minutes:.0f}m "
              f"sharpe={top.sharpe_ratio:.3f} "
              f"WR={top.win_rate:.1%} "
              f"PF={top.profit_factor:.2f} "
              f"trades={top.total_trades}")

    return results


def find_robust_mr_params(all_results: dict[str, list[MRResult]]) -> list[dict]:
    """Find MR parameters that perform well across multiple datasets."""
    print("\n\n═══════════════════════════════════════════════════════════")
    print("  FINDING ROBUST MR PARAMETERS")
    print("═══════════════════════════════════════════════════════════")

    config_stats: dict[str, dict] = {}

    for ds_name, results in all_results.items():
        if not results:
            continue
        for rank, r in enumerate(results):
            key = (f"ma={r.ma_window}_"
                   f"ez={r.entry_zscore:.1f}_"
                   f"xz={r.exit_zscore:.1f}_"
                   f"sl={r.stop_loss_pct:.2f}_"
                   f"tm={r.timeout_minutes:.0f}_"
                   f"ps={r.position_size_pusd:.0f}")

            if key not in config_stats:
                config_stats[key] = {
                    "ma_window": r.ma_window,
                    "entry_zscore": r.entry_zscore,
                    "exit_zscore": r.exit_zscore,
                    "stop_loss_pct": r.stop_loss_pct,
                    "timeout_minutes": r.timeout_minutes,
                    "position_size_pusd": r.position_size_pusd,
                    "datasets": {},
                }

            rank_pct = 1.0 - (rank / max(len(results), 1))
            config_stats[key]["datasets"][ds_name] = {
                "sharpe_ratio": r.sharpe_ratio,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "max_drawdown": r.max_drawdown,
                "total_trades": r.total_trades,
                "total_pnl": r.total_pnl,
                "rank": rank + 1,
                "rank_pct": round(rank_pct, 3),
            }

    robust_list = []
    for key, cfg in config_stats.items():
        ds = cfg["datasets"]
        n = len(ds)
        if n == 0:
            continue

        sharpes = [d["sharpe_ratio"] for d in ds.values()]
        rank_pcts = [d["rank_pct"] for d in ds.values()]
        win_rates = [d["win_rate"] for d in ds.values()]
        profit_factors = [d["profit_factor"] for d in ds.values()]
        trades = [d["total_trades"] for d in ds.values()]

        avg_sharpe = sum(sharpes) / n
        avg_rank = sum(rank_pcts) / n
        avg_wr = sum(win_rates) / n
        good_sharpe = sum(1 for s in sharpes if s > 0.3)
        good_pf = sum(1 for pf in profit_factors if pf > 1.1 and pf != float("inf"))
        good_trades = sum(1 for t in trades if t >= 5)

        real_pfs = [pf for pf in profit_factors if pf != float("inf")]
        avg_pf = sum(real_pfs) / max(len(real_pfs), 1)

        robustness = (
            avg_rank * 0.3
            + (min(avg_sharpe / 2.0, 1.0) * 0.25 if avg_sharpe > 0 else -0.1)
            + (good_sharpe / max(n, 1)) * 0.25
            + (good_trades / max(n, 1)) * 0.20
        )

        robust_list.append({
            "ma_window": cfg["ma_window"],
            "entry_zscore": cfg["entry_zscore"],
            "exit_zscore": cfg["exit_zscore"],
            "stop_loss_pct": cfg["stop_loss_pct"],
            "timeout_minutes": cfg["timeout_minutes"],
            "position_size_pusd": cfg["position_size_pusd"],
            "n_datasets": n,
            "avg_sharpe": round(avg_sharpe, 4),
            "avg_win_rate": round(avg_wr, 4),
            "avg_profit_factor": round(avg_pf, 4),
            "datasets_good_sharpe": good_sharpe,
            "datasets_good_pf": good_pf,
            "robustness_score": round(robustness, 4),
            "per_dataset": ds,
        })

    robust_list.sort(key=lambda x: x["robustness_score"], reverse=True)
    return robust_list


def print_robust_mr_results(robust: list[dict], top_n: int = 10) -> None:
    """Pretty-print top MR configurations."""
    print(f"\n  TOP {top_n} ROBUST MR CONFIGURATIONS:\n")
    print(f"  {'Rank':>4} {'MA':>4} {'EZ':>6} {'XZ':>6} {'SL':>6} "
          f"{'Tm':>5} {'PS':>5} {'AvgSharpe':>10} {'AvgWR':>7} "
          f"{'AvgPF':>7} {'GoodS':>6} {'Robust':>8}")
    print("  " + "-" * 90)

    for i, cfg in enumerate(robust[:top_n]):
        print(
            f"  {i+1:>4} "
            f"{cfg['ma_window']:>4} "
            f"{cfg['entry_zscore']:>6.1f} "
            f"{cfg['exit_zscore']:>6.1f} "
            f"{cfg['stop_loss_pct']:>6.0%} "
            f"{cfg['timeout_minutes']:>5.0f} "
            f"{cfg['position_size_pusd']:>5.0f} "
            f"{cfg['avg_sharpe']:>10.3f} "
            f"{cfg['avg_win_rate']:>7.1%} "
            f"{cfg['avg_profit_factor']:>7.2f} "
            f"{cfg['datasets_good_sharpe']:>6} "
            f"{cfg['robustness_score']:>8.3f}"
        )

    print("\n  PER-DATASET DETAIL FOR TOP 3:\n")
    for i, cfg in enumerate(robust[:3]):
        print(f"  #{i+1}: ma={cfg['ma_window']} entry_z={cfg['entry_zscore']:.1f} "
              f"exit_z={cfg['exit_zscore']:.1f} sl={cfg['stop_loss_pct']:.0%} "
              f"timeout={cfg['timeout_minutes']:.0f}m "
              f"size={cfg['position_size_pusd']:.0f} USDC")
        for ds, stats in sorted(cfg["per_dataset"].items()):
            pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float('inf') else "∞"
            print(f"     {ds:<8}  sharpe={stats['sharpe_ratio']:>7.3f}  "
                  f"WR={stats['win_rate']:>6.1%}  "
                  f"PF={pf_str:>7}  "
                  f"PnL={stats['total_pnl']:>+8.4f}  "
                  f"DD={stats['max_drawdown']:>6.1%}  "
                  f"trades={stats['total_trades']:>4}")
        print()


def save_mr_results(
    all_results: dict[str, list[MRResult]],
    robust: list[dict],
    args: argparse.Namespace,
) -> None:
    """Save sweep results and optimal parameters to disk."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Save per-dataset sweep CSVs
    for ds_name, results in all_results.items():
        if not results:
            continue
        path = OUTPUT_DIR / f"sweep_mr_{ds_name}_{timestamp}.csv"
        fieldnames = [
            "ma_window", "entry_zscore", "exit_zscore", "stop_loss_pct",
            "timeout_minutes", "position_size_pusd", "total_trades",
            "winners", "losers", "total_pnl", "win_rate", "profit_factor",
            "sharpe_ratio", "max_drawdown", "avg_trade_pnl",
            "best_trade", "worst_trade",
        ]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in results:
                writer.writerow({
                    "ma_window": r.ma_window,
                    "entry_zscore": r.entry_zscore,
                    "exit_zscore": r.exit_zscore,
                    "stop_loss_pct": r.stop_loss_pct,
                    "timeout_minutes": r.timeout_minutes,
                    "position_size_pusd": r.position_size_pusd,
                    "total_trades": r.total_trades,
                    "winners": r.winners,
                    "losers": r.losers,
                    "total_pnl": r.total_pnl,
                    "win_rate": r.win_rate,
                    "profit_factor": r.profit_factor,
                    "sharpe_ratio": r.sharpe_ratio,
                    "max_drawdown": r.max_drawdown,
                    "avg_trade_pnl": r.avg_trade_pnl,
                    "best_trade": r.best_trade,
                    "worst_trade": r.worst_trade,
                })
        print(f"  💾 Sweep results: {path}")

    # Save optimal parameters
    if robust:
        output_filename = "optimal_params_mr_real.json" if args.parquet_dir else "optimal_params_mr.json"
        optimal_path = OUTPUT_DIR / output_filename
        best = robust[0]
        optimal = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "quick" if args.quick else "full",
            "data_source": "parquet" if args.parquet_dir else "synthetic",
            "parquet_dir": args.parquet_dir,
            "n_ticks": args.n_ticks,
            "balance": args.balance,
            "top_config": {
                "ma_window": best["ma_window"],
                "entry_zscore": best["entry_zscore"],
                "exit_zscore": best["exit_zscore"],
                "stop_loss_pct": best["stop_loss_pct"],
                "timeout_minutes": best["timeout_minutes"],
                "position_size_pusd": best["position_size_pusd"],
            },
            "top_3": [
                {
                    "ma_window": cfg["ma_window"],
                    "entry_zscore": cfg["entry_zscore"],
                    "exit_zscore": cfg["exit_zscore"],
                    "stop_loss_pct": cfg["stop_loss_pct"],
                    "timeout_minutes": cfg["timeout_minutes"],
                    "position_size_pusd": cfg["position_size_pusd"],
                    "avg_sharpe": cfg["avg_sharpe"],
                    "avg_win_rate": cfg["avg_win_rate"],
                    "avg_profit_factor": cfg["avg_profit_factor"],
                    "robustness_score": cfg["robustness_score"],
                }
                for cfg in robust[:3]
            ],
            "full_ranking": [
                {
                    "ma_window": cfg["ma_window"],
                    "entry_zscore": cfg["entry_zscore"],
                    "exit_zscore": cfg["exit_zscore"],
                    "stop_loss_pct": cfg["stop_loss_pct"],
                    "timeout_minutes": cfg["timeout_minutes"],
                    "position_size_pusd": cfg["position_size_pusd"],
                    "robustness_score": cfg["robustness_score"],
                    "avg_sharpe": cfg["avg_sharpe"],
                }
                for cfg in robust[:20]
            ],
        }
        with open(optimal_path, "w") as f:
            json.dump(optimal, f, indent=2)
        print(f"\n  🎯 Optimal MR params saved to: {optimal_path}")


def main() -> None:
    args = parse_args()

    print("═" * 65)
    print("  POLYBOT — MeanReversion Strategy Parameter Optimizer")
    print("═" * 65)
    print(f"  Mode:    {'QUICK' if args.quick else 'FULL'} sweep")
    print(f"  Ticks:   {args.n_ticks} per dataset")
    print(f"  Balance: ${args.balance:.0f} USDC")
    print(f"  Output:  {OUTPUT_DIR.absolute()}")
    if args.parquet_dir:
        print(f"  Data:    REAL (parquet: {args.parquet_dir})")
    else:
        print("  Data:    SYNTHETIC")

    if args.quick:
        ma_windows = QUICK_MA_WINDOWS
        entry_zscores = QUICK_ENTRY_ZSCORES
        exit_zscores = QUICK_EXIT_ZSCORES
        stop_losses = QUICK_STOP_LOSSES
        timeout_mins = QUICK_TIMEOUT_MINUTES
        pos_sizes = QUICK_POS_SIZES
    else:
        ma_windows = FULL_MA_WINDOWS
        entry_zscores = FULL_ENTRY_ZSCORES
        exit_zscores = FULL_EXIT_ZSCORES
        stop_losses = FULL_STOP_LOSSES
        timeout_mins = FULL_TIMEOUT_MINUTES
        pos_sizes = FULL_POS_SIZES

    combos = (len(ma_windows) * len(entry_zscores) * len(exit_zscores) *
              len(stop_losses) * len(timeout_mins) * len(pos_sizes))
    valid = sum(1
        for mw in ma_windows
        for ez in entry_zscores
        for xz in exit_zscores
        for sl in stop_losses
        for tm in timeout_mins
        for ps in pos_sizes
        if ez < xz
    )
    print(f"  Combos:  {combos} possible ({valid} valid)")
    print()

    # Determine datasets
    if args.dataset:
        ds_info = DATASETS[args.dataset]
        if args.parquet_dir:
            print(f"📊 Loading real data for {args.dataset}...")
            ticks = load_real_dataset(
                ds_info["asset"], ds_info["window"], args.n_ticks, args.parquet_dir
            )
        else:
            print(f"📊 Generating data for {args.dataset}...")
            ticks = generate_realistic_dataset(
                ds_info["asset"], ds_info["window"], args.n_ticks, save_csv=True
            )
        target_datasets = {args.dataset: ticks}
    else:
        if args.parquet_dir:
            print(f"📊 Loading real data from {args.parquet_dir}...")
        else:
            print("📊 Generating realistic data for all datasets...")
        target_datasets = {}
        for ds_name, ds_info in DATASETS.items():
            if args.parquet_dir:
                print(f"   {ds_name}...")
                ticks = load_real_dataset(
                    ds_info["asset"], ds_info["window"],
                    args.n_ticks, args.parquet_dir
                )
            else:
                print(f"   {ds_name}...")
                ticks = generate_realistic_dataset(
                    ds_info["asset"], ds_info["window"], args.n_ticks, save_csv=True
                )
            if ticks:
                target_datasets[ds_name] = ticks

    # ── Run sweeps ──────────────────────────────────────────────────
    t_total = time.monotonic()
    all_results: dict[str, list[MRResult]] = {}

    for ds_name, ticks in target_datasets.items():
        results = run_mr_sweep(
            ticks=ticks,
            ma_windows=ma_windows,
            entry_zscores=entry_zscores,
            exit_zscores=exit_zscores,
            stop_losses=stop_losses,
            timeout_minutes_list=timeout_mins,
            pos_sizes=pos_sizes,
            balance=args.balance,
            label=ds_name,
        )
        all_results[ds_name] = results

    total_elapsed = time.monotonic() - t_total

    # ── Find robust parameters ──────────────────────────────────────
    robust = find_robust_mr_params(all_results)
    if robust:
        print_robust_mr_results(robust)

    # ── Save results ────────────────────────────────────────────────
    save_mr_results(all_results, robust, args)

    # ── Summary ─────────────────────────────────────────────────────
    print(f"\n{'═' * 65}")
    print(f"  MR OPTIMIZATION COMPLETE — {total_elapsed:.1f}s total")
    print(f"{'═' * 65}")

    if robust:
        best = robust[0]
        print("\n  ✅ Best robust MR parameters:")
        print(f"     ma_window         = {best['ma_window']}")
        print(f"     entry_zscore      = {best['entry_zscore']:.1f}")
        print(f"     exit_zscore       = {best['exit_zscore']:.1f}")
        print(f"     stop_loss_pct     = {best['stop_loss_pct']:.0%}")
        print(f"     timeout_minutes   = {best['timeout_minutes']:.0f}")
        print(f"     position_size     = {best['position_size_pusd']:.0f} USDC")
        print(f"     avg_sharpe        = {best['avg_sharpe']:.4f}")
        print(f"     avg_win_rate      = {best['avg_win_rate']:.1%}")
        print(f"     avg_profit_factor = {best['avg_profit_factor']:.2f}")
        print(f"     robustness_score  = {best['robustness_score']:.4f}")

        criteria_met = []
        if best["avg_sharpe"] > 0.5:
            criteria_met.append("✅ Sharpe > 0.5")
        else:
            criteria_met.append(f"⚠️  Sharpe {best['avg_sharpe']:.3f} < 0.5 target")
        if best["avg_win_rate"] > 0.40:
            criteria_met.append("✅ Win Rate > 40%")
        else:
            criteria_met.append(f"⚠️  WR {best['avg_win_rate']:.1%} < 40% target")
        if best["avg_profit_factor"] > 1.1:
            criteria_met.append("✅ Profit Factor > 1.1")
        else:
            criteria_met.append(f"⚠️  PF {best['avg_profit_factor']:.2f} < 1.1 target")

        print("\n  Criteria check:")
        for c in criteria_met:
            print(f"     {c}")

    print(f"\n  Output files in: {OUTPUT_DIR.absolute()}/\n")


if __name__ == "__main__":
    main()
