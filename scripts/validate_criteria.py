#!/usr/bin/env python3
"""
Criteria Validation Script — Backtesting Success Criteria Check.

Runs comprehensive backtesting across all 4 market datasets
(BTC/ETH × 5m/15m) using optimized parameters and validates
against the success criteria defined in PLAN_MEJORAS.txt.

Usage:
    python scripts/validate_criteria.py
    python scripts/validate_criteria.py --quick         # Fast mode
    python scripts/validate_criteria.py --threshold 0.75 --stop-loss 0.15 --target 0.90
    python scripts/validate_criteria.py --output data/reports/validation.json

Exit codes:
    0 — ALL criteria passed
    1 — One or more criteria failed
    2 — Script error (missing files, import errors, etc.)

Output:
    data/reports/validation_{timestamp}.json   # Full report
    data/reports/validation_summary.txt        # Human-readable summary
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.backtesting.data_loader import DataLoader, HistoricalDataset
from src.backtesting.engine import BacktestEngine
from src.backtesting.metrics import BacktestMetrics
from src.risk.engine import RiskEngineConfig
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig

# ── Output directories ───────────────────────────────────────────────
REPORTS_DIR = Path("data/reports")
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

OPTIMAL_PARAMS_PATH = Path("data/optimization/optimal_params.json")

# ── Datasets ──────────────────────────────────────────────────────────
DATASETS = {
    "BTC_5m":  {"asset": "BTC", "window": "5m"},
    "BTC_15m": {"asset": "BTC", "window": "15m"},
    "ETH_5m":  {"asset": "ETH", "window": "5m"},
    "ETH_15m": {"asset": "ETH", "window": "15m"},
}

# ── Success Criteria (PLAN_MEJORAS.txt) ───────────────────────────────
CRITERIA = [
    {
        "id": "C01",
        "name": "Sharpe BTC 5m",
        "check": lambda r: r["sharpe_ratio"] > 1.0,
        "target": "> 1.0",
        "datasets": ["BTC_5m"],
        "weight": "critical",
    },
    {
        "id": "C02",
        "name": "Sharpe BTC 15m",
        "check": lambda r: r["sharpe_ratio"] > 1.0,
        "target": "> 1.0",
        "datasets": ["BTC_15m"],
        "weight": "critical",
    },
    {
        "id": "C03",
        "name": "Sharpe ETH 5m",
        "check": lambda r: r["sharpe_ratio"] > 0.8,
        "target": "> 0.8",
        "datasets": ["ETH_5m"],
        "weight": "critical",
    },
    {
        "id": "C04",
        "name": "Sharpe ETH 15m",
        "check": lambda r: r["sharpe_ratio"] > 0.8,
        "target": "> 0.8",
        "datasets": ["ETH_15m"],
        "weight": "critical",
    },
    {
        "id": "C05",
        "name": "Profit Factor > 1.3",
        "check": lambda r: r["profit_factor"] > 1.3,
        "target": "> 1.3",
        "datasets": ["BTC_5m", "BTC_15m", "ETH_5m", "ETH_15m"],
        "weight": "critical",
    },
    {
        "id": "C06",
        "name": "Win Rate > 45%",
        "check": lambda r: r["win_rate"] > 0.45,
        "target": "> 45%",
        "datasets": ["BTC_5m", "BTC_15m", "ETH_5m", "ETH_15m"],
        "weight": "critical",
    },
    {
        "id": "C07",
        "name": "Max Drawdown < 15%",
        "check": lambda r: r["max_drawdown"] < 0.15,
        "target": "< 15%",
        "datasets": ["BTC_5m", "BTC_15m", "ETH_5m", "ETH_15m"],
        "weight": "critical",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate backtesting success criteria",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Quick mode (1000 ticks, fewer datasets)"
    )
    parser.add_argument(
        "--n-ticks", type=int, default=3000,
        help="Number of synthetic ticks per dataset (default: 3000)"
    )
    parser.add_argument(
        "--balance", type=float, default=1000.0,
        help="Initial balance in USDC (default: 1000)"
    )
    parser.add_argument(
        "--threshold", type=float, default=None,
        help="BAT threshold override (default: from optimal_params.json or 0.70)"
    )
    parser.add_argument(
        "--stop-loss", type=float, default=None, dest="stop_loss",
        help="BAT stop_loss_pct override"
    )
    parser.add_argument(
        "--target", type=float, default=None, dest="target_price",
        help="BAT target_price override"
    )
    parser.add_argument(
        "--required-ticks", type=int, default=None, dest="required_ticks",
        help="BAT required_ticks override"
    )
    parser.add_argument(
        "--position-size", type=float, default=None, dest="position_size",
        help="BAT position_size_usdc override"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON path (default: data/reports/validation_<timestamp>.json)"
    )
    return parser.parse_args()


def load_optimal_params() -> dict:
    """Load optimized parameters from P5.3 output, or return defaults."""
    if OPTIMAL_PARAMS_PATH.exists():
        with open(OPTIMAL_PARAMS_PATH) as f:
            data = json.load(f)
        top = data.get("top_config", {})
        return {
            "threshold": top.get("threshold", 0.70),
            "stop_loss_pct": top.get("stop_loss_pct", 0.15),
            "target_price": top.get("target_price", 0.90),
            "required_ticks": top.get("required_ticks", 3),
            "position_size_usdc": top.get("position_size_usdc", 10),
        }
    return {
        "threshold": 0.70,
        "stop_loss_pct": 0.15,
        "target_price": 0.90,
        "required_ticks": 3,
        "position_size_usdc": 10,
    }


def generate_dataset(
    ds_name: str,
    ds_info: dict,
    n_ticks: int,
) -> HistoricalDataset:
    """Generate realistic synthetic data for a dataset."""
    try:
        from scripts.optimize_bat import generate_realistic_dataset
        return generate_realistic_dataset(
            asset=ds_info["asset"],
            window=ds_info["window"],
            n_ticks=n_ticks,
            save_csv=False,
        )
    except ImportError:
        # Fallback: use DataLoader.generate_synthetic with realistic params
        print(f"     ⚠️  optimize_bat not importable, using DataLoader.generate_synthetic()")
        return DataLoader.generate_synthetic(
            asset=ds_info["asset"],
            window=ds_info["window"],
            n_ticks=n_ticks,
            start_price=0.70,
            volatility=0.02,
            trend=0.0001,
            reversion_strength=0.002,
            reversion_center=0.75,
        )


def run_backtest(
    dataset: HistoricalDataset,
    config: BuyAboveThresholdConfig,
    balance: float,
) -> dict:
    """Run backtest and return key metrics."""
    engine = BacktestEngine(
        strategy_config=config,
        risk_config=RiskEngineConfig(),
        initial_balance=balance,
        verbose=False,
    )
    result = engine.run(dataset)
    metrics = BacktestMetrics(result).compute_all()

    return {
        "dataset": f"{dataset.asset}_{dataset.window}",
        "asset": dataset.asset,
        "window": dataset.window,
        "ticks": dataset.tick_count,
        "duration_hours": round(dataset.duration_hours, 1),
        "sharpe_ratio": metrics["risk"]["sharpe_ratio"],
        "sortino_ratio": metrics["risk"]["sortino_ratio"],
        "max_drawdown": metrics["risk"]["max_drawdown_pct"],
        "win_rate": metrics["performance"]["win_rate"],
        "profit_factor": metrics["performance"]["profit_factor"],
        "total_pnl": metrics["pnl"]["total_pnl_usdc"],
        "total_pnl_pct": metrics["pnl"]["total_pnl_pct"],
        "total_trades": metrics["summary"]["closed_positions"],
        "winners": metrics["performance"]["winners"],
        "losers": metrics["performance"]["losers"],
        "avg_pnl_per_trade": metrics["pnl"]["avg_pnl_per_trade"],
        "best_trade": metrics["pnl"]["best_trade_usdc"],
        "worst_trade": metrics["pnl"]["worst_trade_usdc"],
        "calmar_ratio": metrics["risk"]["calmar_ratio"],
        "exit_reasons": metrics["duration"]["exit_reasons"],
    }


def evaluate_criteria(
    results: dict[str, dict],
) -> list[dict]:
    """Evaluate all criteria against backtest results."""
    evaluations = []

    for criterion in CRITERIA:
        dataset_results = []
        for ds_name in criterion["datasets"]:
            if ds_name in results:
                r = results[ds_name]
                passed = criterion["check"](r)
                dataset_results.append({
                    "dataset": ds_name,
                    "passed": passed,
                    "actual": _extract_value(r, criterion["name"]),
                })

        all_pass = all(d["passed"] for d in dataset_results)
        evaluations.append({
            "id": criterion["id"],
            "name": criterion["name"],
            "target": criterion["target"],
            "weight": criterion["weight"],
            "passed": all_pass,
            "details": dataset_results,
        })

    return evaluations


def _extract_value(result: dict, criterion_name: str) -> float:
    """Extract the relevant metric value from a result dict."""
    name_lower = criterion_name.lower()
    if "sharpe" in name_lower:
        return result["sharpe_ratio"]
    elif "profit factor" in name_lower:
        return result["profit_factor"]
    elif "win rate" in name_lower:
        return result["win_rate"]
    elif "drawdown" in name_lower:
        return result["max_drawdown"]
    return 0.0


def print_results(
    results: dict[str, dict],
    evaluations: list[dict],
    config: dict,
    elapsed: float,
) -> None:
    """Pretty-print results with ANSI colors."""
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    print()
    print("═" * 70)
    print(f"  {BOLD}POLYBOT — CRITERIA VALIDATION REPORT{RESET}")
    print("═" * 70)
    print(f"  Generated: {datetime.now(timezone.utc).isoformat()}")
    print(f"  Elapsed:   {elapsed:.1f}s")
    print(f"  Config:    threshold={config['threshold']:.2f} "
          f"stop_loss={config['stop_loss_pct']:.0%} "
          f"target={config['target_price']:.2f} "
          f"ticks={config['required_ticks']} "
          f"size={config['position_size_usdc']:.0f} USDC")
    print()

    # ── Per-dataset summary ──────────────────────────────────────────
    print(f"  {BOLD}BACKTEST RESULTS PER DATASET:{RESET}")
    print(f"  {'Dataset':<10} {'Sharpe':>8} {'WR':>7} {'PF':>7} "
          f"{'PnL':>10} {'MaxDD':>7} {'Trades':>7}")
    print("  " + "-" * 60)
    for ds_name in ["BTC_5m", "BTC_15m", "ETH_5m", "ETH_15m"]:
        if ds_name in results:
            r = results[ds_name]
            sharpe_color = GREEN if r["sharpe_ratio"] > 1.0 else (
                YELLOW if r["sharpe_ratio"] > 0.5 else RED
            )
            print(
                f"  {ds_name:<10} "
                f"{sharpe_color}{r['sharpe_ratio']:>8.3f}{RESET} "
                f"{r['win_rate']:>7.1%} "
                f"{r['profit_factor']:>7.2f} "
                f"{r['total_pnl']:>+10.4f} "
                f"{r['max_drawdown']:>6.1%} "
                f"{r['total_trades']:>7}"
            )
    print()

    # ── Criteria evaluation ──────────────────────────────────────────
    critical_pass = 0
    critical_fail = 0
    warning_pass = 0
    warning_fail = 0

    print(f"  {BOLD}CRITERIA EVALUATION:{RESET}")
    print(f"  {'ID':<5} {'Criterion':<28} {'Target':<10} {'Result':<10} {'Status':<8}")
    print("  " + "-" * 65)

    for ev in evaluations:
        status = "✅ PASS" if ev["passed"] else "❌ FAIL"
        color = GREEN if ev["passed"] else RED

        # Show worst dataset result
        actuals = [d["actual"] for d in ev["details"]]
        if ev["passed"]:
            result_str = f"{min(actuals):.3f}" if actuals else "N/A"
        else:
            result_str = f"{min(actuals):.3f}" if actuals else "N/A"

        print(
            f"  {color}{ev['id']:<5} {ev['name']:<28} {ev['target']:<10} "
            f"{result_str:<10} {status}{RESET}"
        )

        # Show per-dataset details for failures
        if not ev["passed"]:
            for d in ev["details"]:
                if not d["passed"]:
                    print(f"       ↳ {RED}{d['dataset']}: {d['actual']:.3f}{RESET}")

        if ev["weight"] == "critical":
            if ev["passed"]:
                critical_pass += 1
            else:
                critical_fail += 1
        else:
            if ev["passed"]:
                warning_pass += 1
            else:
                warning_fail += 1

    print()

    # ── Summary ──────────────────────────────────────────────────────
    total_pass = critical_pass + warning_pass
    total_fail = critical_fail + warning_fail
    total = total_pass + total_fail

    print(f"  {BOLD}SUMMARY:{RESET}")
    print(f"  Critical: {GREEN}{critical_pass} passed{RESET}, "
          f"{RED}{critical_fail} failed{RESET}")
    print(f"  Total:    {total_pass}/{total} passed "
          f"({total_pass / max(total, 1) * 100:.0f}%)")
    print()

    if critical_fail == 0:
        print(f"  {GREEN}{BOLD}✅ ALL CRITICAL CRITERIA PASSED{RESET}")
        print(f"  The strategy meets the minimum requirements for paper trading.")
    else:
        print(f"  {RED}{BOLD}❌ {critical_fail} CRITICAL CRITERIA FAILED{RESET}")
        print(f"  The strategy does NOT yet meet the requirements for production.")
        print(f"  Consider: adjusting parameters, more data, or strategy refinement.")

    print()
    print("═" * 70)
    print()


def save_report(
    results: dict[str, dict],
    evaluations: list[dict],
    config: dict,
    args: argparse.Namespace,
    elapsed: float,
) -> Path:
    """Save full validation report as JSON."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = args.output or str(REPORTS_DIR / f"validation_{timestamp}.json")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "args": {
            "n_ticks": args.n_ticks,
            "balance": args.balance,
            "quick": args.quick,
        },
        "elapsed_seconds": round(elapsed, 2),
        "results": results,
        "evaluations": evaluations,
        "summary": {
            "total_criteria": len(evaluations),
            "passed": sum(1 for e in evaluations if e["passed"]),
            "failed": sum(1 for e in evaluations if not e["passed"]),
            "critical_passed": sum(
                1 for e in evaluations
                if e["passed"] and e["weight"] == "critical"
            ),
            "critical_failed": sum(
                1 for e in evaluations
                if not e["passed"] and e["weight"] == "critical"
            ),
            "exit_code": 0 if all(
                e["passed"] for e in evaluations if e["weight"] == "critical"
            ) else 1,
        },
    }

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Also save as latest (for CI)
    latest_path = REPORTS_DIR / "validation_latest.json"
    with open(latest_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    return Path(output_path)


def main() -> int:
    args = parse_args()

    print()
    print("═" * 70)
    print("  POLYBOT — CRITERIA VALIDATION")
    print("═" * 70)
    print(f"  Mode:    {'QUICK' if args.quick else 'FULL'} validation")
    print(f"  Ticks:   {args.n_ticks} per dataset")
    print(f"  Balance: ${args.balance:.0f} USDC")
    print()

    # ── Load parameters ──────────────────────────────────────────────
    params = load_optimal_params()
    if args.threshold is not None:
        params["threshold"] = args.threshold
    if args.stop_loss is not None:
        params["stop_loss_pct"] = args.stop_loss
    if args.target_price is not None:
        params["target_price"] = args.target_price
    if args.required_ticks is not None:
        params["required_ticks"] = args.required_ticks
    if args.position_size is not None:
        params["position_size_usdc"] = args.position_size

    config = BuyAboveThresholdConfig(
        threshold=params["threshold"],
        stop_loss_pct=params["stop_loss_pct"],
        target_price=params["target_price"],
        required_ticks=params["required_ticks"],
        position_size_usdc=params["position_size_usdc"],
        max_spread=0.03,
        min_volume_usdc=500.0,
    )
    config.validate()

    print(f"  Using config: threshold={config.threshold:.2f} "
          f"stop_loss={config.stop_loss_pct:.0%} "
          f"target={config.target_price:.2f} "
          f"ticks={config.required_ticks} "
          f"size={config.position_size_usdc:.0f}")
    print()

    # ── Generate datasets and run backtests ──────────────────────────
    t_total = time.monotonic()
    results: dict[str, dict] = {}

    datasets_to_run = DATASETS
    if args.quick:
        datasets_to_run = {"BTC_5m": DATASETS["BTC_5m"], "ETH_5m": DATASETS["ETH_5m"]}

    for ds_name, ds_info in datasets_to_run.items():
        print(f"  📊 {ds_name}: generating data ({args.n_ticks} ticks)...")
        dataset = generate_dataset(ds_name, ds_info, args.n_ticks)
        print(f"     {dataset.tick_count} ticks, {dataset.duration_hours:.1f}h")

        print(f"  🔍 {ds_name}: running backtest...")
        t0 = time.monotonic()
        result = run_backtest(dataset, config, args.balance)
        dt = time.monotonic() - t0
        results[ds_name] = result

        sharpe_icon = "✅" if result["sharpe_ratio"] > 1.0 else (
            "⚠️" if result["sharpe_ratio"] > 0 else "❌"
        )
        print(f"     {sharpe_icon} Sharpe={result['sharpe_ratio']:.3f} "
              f"WR={result['win_rate']:.1%} "
              f"PF={result['profit_factor']:.2f} "
              f"PnL={result['total_pnl']:+.4f} "
              f"({dt:.1f}s)")
        print()

    elapsed = time.monotonic() - t_total

    # ── Evaluate criteria ────────────────────────────────────────────
    evaluations = evaluate_criteria(results)

    # ── Print and save ───────────────────────────────────────────────
    print_results(results, evaluations, params, elapsed)
    output_path = save_report(results, evaluations, params, args, elapsed)

    print(f"  📁 Full report saved to: {output_path}")
    print(f"  📁 Latest report:        {REPORTS_DIR / 'validation_latest.json'}")
    print()

    # ── Determine exit code ──────────────────────────────────────────
    critical_failed = sum(
        1 for e in evaluations
        if not e["passed"] and e["weight"] == "critical"
    )

    return 0 if critical_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
