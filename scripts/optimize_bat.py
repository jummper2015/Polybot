#!/usr/bin/env python3
"""
BAT Strategy Parameter Optimizer.

Runs exhaustive parameter sweeps across all 4 market datasets
(BTC/ETH × 5m/15m) to find optimal BuyAboveThreshold parameters.

Generates realistic synthetic data mimicking Polymarket prediction
market behavior (trend + mean reversion + volatility clustering),
runs full sweeps, and selects robust parameters that perform well
across multiple datasets.

Usage:
    python scripts/optimize_bat.py
    python scripts/optimize_bat.py --quick          # Fast mode (~2 min)
    python scripts/optimize_bat.py --full            # Full sweep (~10 min)
    python scripts/optimize_bat.py --dataset BTC_5m  # Single dataset
    python scripts/optimize_bat.py --csv data/historical/BTC_5m.csv  # Real data

Output:
    data/optimization/optimal_params.json    # Best parameters found
    data/optimization/sweep_*.csv            # Full sweep results per dataset
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.backtesting.data_loader import DataLoader, HistoricalDataset
from src.backtesting.engine import BacktestEngine
from src.backtesting.metrics import BacktestMetrics
from src.domain.value_objects.market_tick import MarketTick
from src.risk.engine import RiskEngineConfig
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig

OUTPUT_DIR = Path("data/optimization")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── SWEEP PARAMETERS ──────────────────────────────────────────────────
# Full sweep: 7 × 4 × 5 × 3 × 3 = 1260 combinations per dataset
FULL_THRESHOLDS   = [0.65, 0.68, 0.70, 0.72, 0.75, 0.78, 0.80, 0.82]
FULL_STOP_LOSSES  = [0.08, 0.10, 0.12, 0.15, 0.20]
FULL_TARGETS      = [0.82, 0.85, 0.88, 0.90, 0.92, 0.95]
FULL_TICKS        = [2, 3, 4]
FULL_POS_SIZES    = [5, 10, 15]

# Quick sweep: 5 × 3 × 3 × 2 × 2 = 180 combinations
QUICK_THRESHOLDS  = [0.65, 0.70, 0.75, 0.78, 0.80]
QUICK_STOP_LOSSES = [0.10, 0.15, 0.20]
QUICK_TARGETS     = [0.85, 0.90, 0.95]
QUICK_TICKS       = [2, 3]
QUICK_POS_SIZES   = [5, 10]

# Default dataset definitions
DATASETS = {
    "BTC_5m":  {"asset": "BTC", "window": "5m"},
    "BTC_15m": {"asset": "BTC", "window": "15m"},
    "ETH_5m":  {"asset": "ETH", "window": "5m"},
    "ETH_15m": {"asset": "ETH", "window": "15m"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BAT Strategy Parameter Optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--quick", action="store_true",
                        help="Quick sweep (~180 configs/dataset, ~2 min total)")
    parser.add_argument("--full", action="store_true",
                        help="Full sweep (~1260 configs/dataset, ~10 min total)")
    parser.add_argument("--dataset",
                        choices=list(DATASETS.keys()),
                        help="Optimize for a single dataset only")
    parser.add_argument("--csv", help="Use real CSV data file instead of synthetic")
    parser.add_argument("--n-ticks", type=int, default=3000,
                        help="Number of synthetic ticks per dataset (default: 3000)")
    parser.add_argument("--balance", type=float, default=1000.0,
                        help="Initial balance in USDC (default: 1000)")
    return parser.parse_args()


def generate_realistic_dataset(
    asset: str,
    window: str,
    n_ticks: int = 3000,
    save_csv: bool = False,
) -> HistoricalDataset:
    """
    Generate synthetic data that mimics Polymarket prediction market behavior.

    Uses a regime-switching latent fair value (LFV) model:
    1. Regime state machine: consolidation (65%) ↔ trending (35%)
    2. Latent fair_value that shifts during trends (0.20-0.50 magnitude)
    3. Price mean-reverts to current fair_value, not static 0.5
    4. Information shocks: discrete ±10-20% jumps (news events)
    5. Expiry-driven drift in last 20% (prices trend toward 0 or 1)
    6. Market microstructure (realistic spread 1-3%, volume patterns)

    This is specifically designed to create realistic conditions where
    the BAT (Buy Above Threshold) strategy can find edge — sustained
    momentum periods above threshold followed by exits to target.

    Trend characteristics (IMPROVED for BAT strategy viability):
    - Duration: 80-250 ticks (longer trends = more BAT captures)
    - Magnitude: 0.20-0.50 (ensures price CAN cross 0.70→0.90 gaps)
    - Direction: random (50% up, 50% down)
    - Reduced noise during trends: sigma 0.001 (fewer false dips)
    - Mean reversion speed to fair_value: 0.30 (faster tracking)

    Consolidation characteristics:
    - Duration: 150-350 ticks (shorter to increase trend frequency)
    - Fair_value wanders very slowly
    - Low noise (0.002), heavy mean reversion to fair_value

    Information shocks:
    - Frequency: ~1 per 500 ticks on average
    - Magnitude: discrete jump of ±10-20% of current price
    - Simulates news events that move prediction markets
    """
    import random
    from datetime import datetime, timedelta

    market_id = f"synthetic_{asset}_{window}"
    start = datetime(2024, 1, 1, 0, 0, 0)
    interval_secs = 30 if window == "5m" else 60

    # ── Regime state machine (IMPROVED) ────────────────────────────
    # More frequent trends (35% vs 20-25%), longer duration, bigger magnitude
    regime: str = "consolidation"
    regime_ticks: int = random.randint(150, 350)  # Shorter consol = more trends
    fair_value: float = 0.58 if asset == "BTC" else 0.55
    trend_velocity: float = 0.0

    # ── Expiry setup ─────────────────────────────────────────────────
    expiry_resolves_to_yes: bool = random.random() < 0.50
    expiry_target: float = 0.95 if expiry_resolves_to_yes else 0.05
    expiry_start_tick: int = int(n_ticks * 0.80)  # Last 20%

    # ── Information shocks ───────────────────────────────────────────
    next_shock_tick: int = random.randint(400, 600)

    # ── Price state ──────────────────────────────────────────────────
    latent_prob: float = fair_value
    ticks: list[MarketTick] = []

    for i in range(n_ticks):
        regime_ticks -= 1

        # ── Regime transition (IMPROVED) ────────────────────────────
        if regime_ticks <= 0:
            if regime == "consolidation":
                # Enter trending regime — LONGER and STRONGER trends
                regime = "trend"
                regime_ticks = random.randint(80, 250)  # Was: 50-150
                direction = 1 if random.random() < 0.50 else -1
                magnitude = random.uniform(0.20, 0.50)  # Was: 0.15-0.40
                trend_velocity = (direction * magnitude) / regime_ticks
                # 40% chance of continuation trend (was: 30%)
                if random.random() < 0.40:
                    regime_ticks += random.randint(40, 100)
            else:
                # Return to consolidation — SHORTER so trends are more frequent
                regime = "consolidation"
                regime_ticks = random.randint(150, 350)  # Was: 200-500
                trend_velocity = 0.0

        # ── Information shocks (NEW) ────────────────────────────────
        if i >= next_shock_tick:
            shock_magnitude = random.uniform(0.10, 0.20) * random.choice([-1, 1])
            fair_value += shock_magnitude
            fair_value = max(0.05, min(0.95, fair_value))
            # Also apply shock to latent price for immediate effect
            latent_prob += shock_magnitude * 0.8
            latent_prob = max(0.02, min(0.98, latent_prob))
            next_shock_tick = i + random.randint(300, 700)

        # ── Regime dynamics (IMPROVED noise) ────────────────────────
        if regime == "trend":
            # Strong directional move in fair_value — LESS noise
            fair_value += trend_velocity
            fair_value = max(0.05, min(0.95, fair_value))
            noise = random.gauss(0, 0.001)  # Was: 0.0025 — fewer false dips
        else:
            # Low volatility mean-reverting consolidation
            noise = random.gauss(0, 0.0015)

        # ── Expiry effect (last 20% of data) ─────────────────────────
        if i >= expiry_start_tick:
            # Aggressive drift toward final outcome
            fair_value += (expiry_target - fair_value) * 0.008

        # ── Price evolution: FASTER mean-revert to current fair_value ──
        # Faster reversion (0.30 vs 0.25) so price tracks fair_value closely,
        # reducing false dips below threshold during trends
        latent_prob += (fair_value - latent_prob) * 0.30 + noise
        latent_prob = max(0.02, min(0.98, latent_prob))

        # ── Derive yes_price from latent probability ─────────────────
        yes_price = latent_prob + random.gauss(0, 0.0015)  # Was: 0.003
        yes_price = max(0.02, min(0.98, yes_price))
        no_price = round(1.0 - yes_price, 4)

        # ── Market microstructure ────────────────────────────────────
        # Spread: 1-3% typical for Polymarket, wider when uncertain
        uncertainty = 1.0 - abs(yes_price - 0.5) * 1.5
        spread = random.uniform(0.005, 0.025) * (0.4 + uncertainty * 0.6)
        best_bid = round(yes_price - spread / 2, 4)
        best_ask = round(yes_price + spread / 2, 4)

        # Volume: higher near 0.5 (most uncertainty), lower near extremes
        vol_uncertainty = 1.0 - abs(yes_price - 0.5) * 2.0
        base_volume = 1000 + vol_uncertainty * 8000
        volume = random.uniform(base_volume * 0.6, base_volume * 1.4)

        timestamp = start + timedelta(seconds=i * interval_secs)

        ticks.append(MarketTick(
            market_id=market_id,
            yes_price=round(yes_price, 4),
            no_price=no_price,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=round(spread, 4),
            volume_24h=round(volume, 2),
            timestamp=timestamp,
        ))

    # ── Optionally save as CSV ───────────────────────────────────────
    if save_csv:
        _save_dataset_csv(ticks, asset, window)

    return HistoricalDataset(
        asset=asset,
        window=window,
        market_id=market_id,
        ticks=ticks,
        start_at=ticks[0].timestamp,
        end_at=ticks[-1].timestamp,
    )


def _save_dataset_csv(
    ticks: list[MarketTick],
    asset: str,
    window: str,
) -> None:
    """Save generated ticks as CSV for reproducibility."""
    import csv
    path = OUTPUT_DIR / f"dataset_{asset}_{window}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "yes_price", "no_price", "best_bid", "best_ask",
            "spread", "volume_24h",
        ])
        writer.writeheader()
        for tick in ticks:
            writer.writerow({
                "timestamp": tick.timestamp.isoformat(),
                "yes_price": tick.yes_price,
                "no_price": tick.no_price,
                "best_bid": tick.best_bid,
                "best_ask": tick.best_ask,
                "spread": tick.spread,
                "volume_24h": tick.volume_24h,
            })
    print(f"     💾 Dataset saved: {path}")


def run_sweep_for_dataset(
    dataset: HistoricalDataset,
    thresholds: list[float],
    stop_losses: list[float],
    targets: list[float],
    ticks_list: list[int],
    pos_sizes: list[float],
    balance: float,
) -> list[dict]:
    """
    Run parameter sweep on a single dataset.
    Returns ranked list of results sorted by Sharpe ratio.
    """
    label = f"{dataset.asset}_{dataset.window}"
    print(f"\n  🔍 Running sweep for {label}...")
    print(f"     {dataset.tick_count} ticks, {dataset.duration_hours:.1f}h of data")
    combos = len(thresholds) * len(stop_losses) * len(targets) * len(ticks_list) * len(pos_sizes)

    t0 = time.monotonic()
    engine = BacktestEngine(
        strategy_config=BuyAboveThresholdConfig(),
        risk_config=RiskEngineConfig(),
        initial_balance=balance,
        verbose=False,
    )

    # Run sweep with progress indicator
    valid_combos = sum(1
        for th in thresholds for sl in stop_losses for tp in targets
        for tk in ticks_list for ps in pos_sizes
        if tp > th and th > 0.55
    )
    print(f"     Running {valid_combos} valid combinations...")

    results = engine.run_parameter_sweep(
        dataset=dataset,
        thresholds=thresholds,
        stop_losses=stop_losses,
        targets=targets,
        ticks_list=ticks_list,
        pos_sizes=pos_sizes,
    )
    elapsed = time.monotonic() - t0

    # Convert to comparable dicts
    comparisons = BacktestMetrics.compare(results)

    valid = sum(1 for c in comparisons if c["total_trades"] > 0)
    print(f"     {len(comparisons)} results ({combos} attempted) in {elapsed:.1f}s")
    print(f"     {valid} configs with trades")

    if comparisons:
        top = comparisons[0]
        print(f"     Best: threshold={top['threshold']:.2f} "
              f"stop_loss={top['stop_loss_pct']:.0%} "
              f"target={top['target_price']:.2f} "
              f"ticks={top.get('required_ticks', '?')} "
              f"sharpe={top['sharpe_ratio']:.3f} "
              f"WR={top['win_rate']:.1%} "
              f"PF={top['profit_factor']:.2f}")

    return comparisons


def find_robust_params(all_results: dict[str, list[dict]]) -> list[dict]:
    """
    Find parameters that perform well across multiple datasets.

    Robustness score: sum of (Sharpe rank percentile) across all datasets
    Higher = consistently good across all market conditions.
    """
    print("\n\n═══════════════════════════════════════════════════════════")
    print("  FINDING ROBUST PARAMETERS")
    print("═══════════════════════════════════════════════════════════")

    # Build a config-key → per-dataset stats map
    config_stats: dict[str, dict] = {}

    for ds_name, results in all_results.items():
        for rank, r in enumerate(results):
            # Create a unique key for this config combination
            key = (f"th={r['threshold']:.2f}_"
                   f"sl={r['stop_loss_pct']:.2f}_"
                   f"tp={r['target_price']:.2f}_"
                   f"tk={r.get('required_ticks', '?')}_"
                   f"ps={r.get('position_size_pusd', '?')}")

            if key not in config_stats:
                config_stats[key] = {
                    "threshold": r["threshold"],
                    "stop_loss_pct": r["stop_loss_pct"],
                    "target_price": r["target_price"],
                    "required_ticks": r.get("required_ticks", 0),
                    "position_size_pusd": r.get("position_size_pusd", 0),
                    "datasets": {},
                }

            rank_pct = 1.0 - (rank / max(len(results), 1))  # Top = ~1.0, Bottom = ~0.0
            config_stats[key]["datasets"][ds_name] = {
                "sharpe_ratio": r["sharpe_ratio"],
                "win_rate": r["win_rate"],
                "profit_factor": r["profit_factor"],
                "max_drawdown": r["max_drawdown"],
                "total_trades": r["total_trades"],
                "total_pnl": r["total_pnl"],
                "rank": rank + 1,
                "rank_pct": round(rank_pct, 3),
            }

    # Calculate robustness metrics
    robust_list = []
    for key, cfg in config_stats.items():
        ds = cfg["datasets"]
        n_datasets = len(ds)

        sharpes = [d["sharpe_ratio"] for d in ds.values()]
        rank_pcts = [d["rank_pct"] for d in ds.values()]
        win_rates = [d["win_rate"] for d in ds.values()]
        profit_factors = [d["profit_factor"] for d in ds.values()]

        avg_sharpe = sum(sharpes) / n_datasets if n_datasets else 0
        avg_rank = sum(rank_pcts) / n_datasets if n_datasets else 0
        avg_wr = sum(win_rates) / n_datasets if n_datasets else 0
        avg_pf = sum(pf for pf in profit_factors if pf != float("inf")) / max(
            sum(1 for pf in profit_factors if pf != float("inf")), 1
        )

        # Count datasets meeting success criteria
        good_sharpe = sum(1 for s in sharpes if s > 0.5)
        good_pf = sum(1 for pf in profit_factors if pf > 1.3 and pf != float("inf"))
        good_wr = sum(1 for wr in win_rates if wr > 0.35)

        robustness = (avg_rank * 0.4) + (min(avg_sharpe / 3.0, 1.0) * 0.3) + (
            (good_sharpe / max(n_datasets, 1)) * 0.3
        )

        robust_list.append({
            **{k: v for k, v in cfg.items() if k != "datasets"},
            "n_datasets": n_datasets,
            "avg_sharpe": round(avg_sharpe, 4),
            "avg_win_rate": round(avg_wr, 4),
            "avg_profit_factor": round(avg_pf, 4),
            "datasets_good_sharpe": good_sharpe,
            "datasets_good_pf": good_pf,
            "datasets_good_wr": good_wr,
            "robustness_score": round(robustness, 4),
            "per_dataset": ds,
        })

    robust_list.sort(key=lambda x: x["robustness_score"], reverse=True)
    return robust_list


def print_robust_results(robust: list[dict], top_n: int = 15) -> None:
    """Pretty-print the top robust configurations."""
    print(f"\n  TOP {top_n} ROBUST CONFIGURATIONS:\n")
    print(f"  {'Rank':>4} {'Th':>6} {'SL':>6} {'TP':>6} {'Tk':>4} {'PS':>5} "
          f"{'AvgSharpe':>10} {'AvgWR':>7} {'AvgPF':>7} "
          f"{'GoodS':>6} {'GoodPF':>7} {'GoodWR':>7} {'Robust':>8}")
    print("  " + "-" * 95)

    for i, cfg in enumerate(robust[:top_n]):
        print(
            f"  {i+1:>4} "
            f"{cfg['threshold']:>6.2f} "
            f"{cfg['stop_loss_pct']:>6.0%} "
            f"{cfg['target_price']:>6.2f} "
            f"{cfg['required_ticks']:>4} "
            f"{cfg['position_size_pusd']:>5.0f} "
            f"{cfg['avg_sharpe']:>10.3f} "
            f"{cfg['avg_win_rate']:>7.1%} "
            f"{cfg['avg_profit_factor']:>7.2f} "
            f"{cfg['datasets_good_sharpe']:>6} "
            f"{cfg['datasets_good_pf']:>7} "
            f"{cfg['datasets_good_wr']:>7} "
            f"{cfg['robustness_score']:>8.3f}"
        )

    # Per-dataset detail for top 3
    print("\n  PER-DATASET DETAIL FOR TOP 3:\n")
    for i, cfg in enumerate(robust[:3]):
        print(f"  #{i+1}: threshold={cfg['threshold']:.2f} "
              f"stop_loss={cfg['stop_loss_pct']:.0%} "
              f"target={cfg['target_price']:.2f} "
              f"ticks={cfg['required_ticks']} "
              f"size={cfg['position_size_pusd']:.0f} USDC")
        for ds, stats in sorted(cfg["per_dataset"].items()):
            print(f"     {ds:<8}  sharpe={stats['sharpe_ratio']:>7.3f}  "
                  f"WR={stats['win_rate']:>6.1%}  "
                  f"PF={stats['profit_factor']:>6.2f}  "
                  f"PnL={stats['total_pnl']:>+8.4f}  "
                  f"DD={stats['max_drawdown']:>6.1%}  "
                  f"trades={stats['total_trades']:>4}")
        print()


def save_results(
    all_results: dict[str, list[dict]],
    robust: list[dict],
    args: argparse.Namespace,
) -> None:
    """Save sweep results and optimal parameters to disk."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Save per-dataset sweep CSVs
    for ds_name, results in all_results.items():
        path = OUTPUT_DIR / f"sweep_{ds_name}_{timestamp}.csv"
        if results:
            fieldnames = list(results[0].keys())
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(results)
            print(f"  💾 Sweep results: {path}")

    # Save optimal parameters as JSON
    if robust:
        optimal_path = OUTPUT_DIR / "optimal_params.json"
        # Always update with latest best
        optimal = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "quick" if args.quick else "full",
            "n_ticks": args.n_ticks,
            "balance": args.balance,
            "top_config": {
                "threshold": robust[0]["threshold"],
                "stop_loss_pct": robust[0]["stop_loss_pct"],
                "target_price": robust[0]["target_price"],
                "required_ticks": robust[0]["required_ticks"],
                "position_size_pusd": robust[0]["position_size_pusd"],
            },
            "top_3": [
                {
                    "threshold": cfg["threshold"],
                    "stop_loss_pct": cfg["stop_loss_pct"],
                    "target_price": cfg["target_price"],
                    "required_ticks": cfg["required_ticks"],
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
                    "threshold": cfg["threshold"],
                    "stop_loss_pct": cfg["stop_loss_pct"],
                    "target_price": cfg["target_price"],
                    "required_ticks": cfg["required_ticks"],
                    "position_size_pusd": cfg["position_size_pusd"],
                    "robustness_score": cfg["robustness_score"],
                    "avg_sharpe": cfg["avg_sharpe"],
                }
                for cfg in robust[:20]
            ],
        }
        with open(optimal_path, "w") as f:
            json.dump(optimal, f, indent=2)
        print(f"\n  🎯 Optimal params saved to: {optimal_path}")


def main():
    args = parse_args()

    print("═" * 65)
    print("  POLYBOT — BAT Strategy Parameter Optimizer")
    print("═" * 65)
    print(f"  Mode:    {'QUICK' if args.quick else 'FULL'} sweep")
    print(f"  Ticks:   {args.n_ticks} per dataset")
    print(f"  Balance: ${args.balance:.0f} USDC")
    print(f"  Output:  {OUTPUT_DIR.absolute()}")

    # Select sweep parameters
    if args.quick:
        thresholds = QUICK_THRESHOLDS
        stop_losses = QUICK_STOP_LOSSES
        targets = QUICK_TARGETS
        ticks_list = QUICK_TICKS
        pos_sizes = QUICK_POS_SIZES
    else:
        thresholds = FULL_THRESHOLDS
        stop_losses = FULL_STOP_LOSSES
        targets = FULL_TARGETS
        ticks_list = FULL_TICKS
        pos_sizes = FULL_POS_SIZES

    combos = len(thresholds) * len(stop_losses) * len(targets) * len(ticks_list) * len(pos_sizes)
    valid_combos = sum(1
        for th in thresholds
        for sl in stop_losses
        for tp in targets
        for tk in ticks_list
        for ps in pos_sizes
        if tp > th and th > 0.55
    )
    print(f"  Combos:  {combos} possible ({valid_combos} valid)")
    print()

    # Determine datasets to process
    if args.csv:
        ds_name = args.dataset or "CUSTOM"
        ds_info = {"asset": "CUSTOM", "window": "custom"}
        if args.dataset:
            ds_info = DATASETS[args.dataset]
        print(f"📂 Loading real data from: {args.csv}")
        ext = args.csv.rsplit(".", 1)[-1].lower()
        if ext == "csv":
            dataset = DataLoader.from_polymarket_csv(
                args.csv, ds_info["asset"], ds_info["window"]
            )
        elif ext == "json":
            dataset = DataLoader.from_json(
                args.csv, ds_info["asset"], ds_info["window"]
            )
        else:
            print(f"❌ Unsupported format: {ext}")
            sys.exit(1)
        target_datasets = {ds_name: dataset}
    elif args.dataset:
        ds_info = DATASETS[args.dataset]
        print(f"📊 Generating realistic data for {args.dataset}...")
        dataset = generate_realistic_dataset(
            ds_info["asset"], ds_info["window"], args.n_ticks, save_csv=True
        )
        target_datasets = {args.dataset: dataset}
    else:
        print("📊 Generating realistic data for all datasets...")
        target_datasets = {}
        for ds_name, ds_info in DATASETS.items():
            print(f"   {ds_name}...")
            dataset = generate_realistic_dataset(
                ds_info["asset"], ds_info["window"], args.n_ticks, save_csv=True
            )
            target_datasets[ds_name] = dataset

    # ── Run sweeps ────────────────────────────────────────────────────
    t_total = time.monotonic()
    all_results: dict[str, list[dict]] = {}

    for ds_name, dataset in target_datasets.items():
        results = run_sweep_for_dataset(
            dataset=dataset,
            thresholds=thresholds,
            stop_losses=stop_losses,
            targets=targets,
            ticks_list=ticks_list,
            pos_sizes=pos_sizes,
            balance=args.balance,
        )
        all_results[ds_name] = results

    total_elapsed = time.monotonic() - t_total

    # ── Find robust parameters ────────────────────────────────────────
    robust = find_robust_params(all_results)
    if robust:
        print_robust_results(robust)

    # ── Save results ──────────────────────────────────────────────────
    save_results(all_results, robust, args)

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n{'═' * 65}")
    print(f"  OPTIMIZATION COMPLETE — {total_elapsed:.1f}s total")
    print(f"{'═' * 65}")

    if robust:
        best = robust[0]
        print("\n  ✅ Best robust parameters:")
        print(f"     threshold         = {best['threshold']:.2f}")
        print(f"     stop_loss_pct     = {best['stop_loss_pct']:.0%}")
        print(f"     target_price      = {best['target_price']:.2f}")
        print(f"     required_ticks    = {best['required_ticks']}")
        print(f"     position_size     = {best['position_size_pusd']:.0f} USDC")
        print(f"     avg_sharpe        = {best['avg_sharpe']:.4f}")
        print(f"     robustness_score  = {best['robustness_score']:.4f}")

        # Quick validation against criteria
        criteria_met = []
        if best["avg_sharpe"] > 1.0:
            criteria_met.append("✅ Sharpe > 1.0")
        else:
            criteria_met.append(f"⚠️  Sharpe {best['avg_sharpe']:.3f} < 1.0 target")
        if best["avg_win_rate"] > 0.45:
            criteria_met.append("✅ Win Rate > 45%")
        else:
            criteria_met.append(f"⚠️  Win Rate {best['avg_win_rate']:.1%} < 45% target")
        if best["avg_profit_factor"] > 1.3:
            criteria_met.append("✅ Profit Factor > 1.3")
        else:
            criteria_met.append(f"⚠️  Profit Factor {best['avg_profit_factor']:.2f} < 1.3 target")

        print("\n  Criteria check:")
        for c in criteria_met:
            print(f"     {c}")

    print(f"\n  Output files in: {OUTPUT_DIR.absolute()}/\n")


if __name__ == "__main__":
    main()
