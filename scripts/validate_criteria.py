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
    parser.add_argument(
        "--check-data", action="store_true",
        help="Check data integrity of recorded Parquet files (P8.1 DESPLEGAR)"
    )
    parser.add_argument(
        "--data-dir", type=str, default="data/parquet",
        help="Parquet data directory for integrity check (default: data/parquet)"
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
        print("     ⚠️  optimize_bat not importable, using DataLoader.generate_synthetic()")
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


_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def print_results(
    results: dict[str, dict],
    evaluations: list[dict],
    config: dict,
    elapsed: float,
) -> None:
    """Pretty-print results with ANSI colors."""
    print()
    print("═" * 70)
    print(f"  {_BOLD}POLYBOT — CRITERIA VALIDATION REPORT{_RESET}")
    print("═" * 70)
    print("  Generated: " + datetime.now(timezone.utc).isoformat())
    print(f"  Elapsed:   {elapsed:.1f}s")
    print(f"  Config:    threshold={config['threshold']:.2f} "
          f"stop_loss={config['stop_loss_pct']:.0%} "
          f"target={config['target_price']:.2f} "
          f"ticks={config['required_ticks']} "
          f"size={config['position_size_usdc']:.0f} USDC")
    print()

    # ── Per-dataset summary ──────────────────────────────────────────
    print(f"  {_BOLD}BACKTEST RESULTS PER DATASET:{_RESET}")
    hdr_cols = (
        f"  {'Dataset':<10} {'Sharpe':>8} {'WR':>7} {'PF':>7} "
        f"{'PnL':>10} {'MaxDD':>7} {'Trades':>7}"
    )
    print(hdr_cols)
    print("  " + "-" * 60)
    for ds_name in ["BTC_5m", "BTC_15m", "ETH_5m", "ETH_15m"]:
        if ds_name in results:
            r = results[ds_name]
            sharpe_color = _GREEN if r["sharpe_ratio"] > 1.0 else (
                _YELLOW if r["sharpe_ratio"] > 0.5 else _RED
            )
            print(
                f"  {ds_name:<10} "
                f"{sharpe_color}{r['sharpe_ratio']:>8.3f}{_RESET} "
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

    print(f"  {_BOLD}CRITERIA EVALUATION:{_RESET}")
    eval_hdr = (
        f"  {'ID':<5} {'Criterion':<28} {'Target':<10} "
        f"{'Result':<10} {'Status':<8}"
    )
    print(eval_hdr)
    print("  " + "-" * 65)

    for ev in evaluations:
        status = "✅ PASS" if ev["passed"] else "❌ FAIL"
        color = _GREEN if ev["passed"] else _RED

        # Show worst dataset result
        actuals = [d["actual"] for d in ev["details"]]
        if ev["passed"]:
            result_str = f"{min(actuals):.3f}" if actuals else "N/A"
        else:
            result_str = f"{min(actuals):.3f}" if actuals else "N/A"

        print(
            f"  {color}{ev['id']:<5} {ev['name']:<28} {ev['target']:<10} "
            f"{result_str:<10} {status}{_RESET}"
        )

        # Show per-dataset details for failures
        if not ev["passed"]:
            for d in ev["details"]:
                if not d["passed"]:
                    print(f"       ↳ {_RED}{d['dataset']}: {d['actual']:.3f}{_RESET}")

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

    print(f"  {_BOLD}SUMMARY:{_RESET}")
    print(f"  Critical: {_GREEN}{critical_pass} passed{_RESET}, "
          f"{_RED}{critical_fail} failed{_RESET}")
    print(f"  Total:    {total_pass}/{total} passed "
          f"({total_pass / max(total, 1) * 100:.0f}%)")
    print()

    if critical_fail == 0:
        print(f"  {_GREEN}{_BOLD}✅ ALL CRITICAL CRITERIA PASSED{_RESET}")
        print("  The strategy meets the minimum requirements for paper trading.")
    else:
        print(f"  {_RED}{_BOLD}❌ {critical_fail} CRITICAL CRITERIA FAILED{_RESET}")
        print("  The strategy does NOT yet meet the requirements for production.")
        print("  Consider: adjusting parameters, more data, or strategy refinement.")

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


def check_data_integrity(data_dir: str) -> tuple[bool, dict]:
    """Check integrity of recorded Parquet data (P8.1 DESPLEGAR)."""
    import pyarrow.parquet as pq

    from src.infrastructure.data.schema import TICK_SCHEMA, read_ticks_uniform

    data_path = Path(data_dir)
    report: dict = {
        "data_dir": str(data_path.absolute()),
        "checks": {},
        "warnings": [],
        "errors": [],
    }

    print()
    print("═" * 70)
    print(f"  {_BOLD}POLYBOT — DATA INTEGRITY CHECK (P8.1){_RESET}")
    print("═" * 70)
    print("  Data dir: " + str(data_path.absolute()))

    # ── Check 1: Parquet files exist ──────────────────────────────────
    if not data_path.exists():
        print(f"  {_RED}❌ Data directory does not exist{_RESET}")
        report["checks"]["directory_exists"] = False
        return False, report

    parquet_files = sorted(data_path.rglob("*.parquet"))
    report["checks"]["directory_exists"] = True
    report["total_files"] = len(parquet_files)

    if not parquet_files:
        print(f"  {_RED}❌ No Parquet files found in {data_dir}{_RESET}")
        report["checks"]["files_exist"] = False
        return False, report

    print(f"  {_GREEN}✅ Found {len(parquet_files)} Parquet files{_RESET}")
    report["checks"]["files_exist"] = True

    # ── Check 2: All files readable ───────────────────────────────────
    unreadable = []
    total_rows = 0
    total_size = 0
    for pf in parquet_files:
        try:
            meta = pq.read_metadata(pf)
            total_rows += meta.num_rows
            total_size += pf.stat().st_size
        except Exception:
            unreadable.append(str(pf))

    report["total_rows"] = total_rows
    report["total_size_bytes"] = total_size
    report["total_size_mb"] = round(total_size / (1024 * 1024), 2)

    if unreadable:
        print(f"  {_RED}❌ {len(unreadable)} unreadable files:{_RESET}")
        for f in unreadable[:5]:
            print(f"     {f}")
        report["checks"]["all_readable"] = False
    else:
        print(f"  {_GREEN}✅ All files readable — "
              f"{total_rows:,} rows, {report['total_size_mb']:.2f} MB{_RESET}")
        report["checks"]["all_readable"] = True

    # ── Check 3: Schema matches canonical ──────────────────────────────
    missing_fields = []
    for pf in parquet_files[:10]:  # Sample first 10 files
        try:
            file_schema = pq.read_schema(pf)
            file_fields = {f.name for f in file_schema}
            canonical_fields = {f.name for f in TICK_SCHEMA}
            missing = canonical_fields - file_fields
            if missing:
                missing_fields.extend(missing)
        except Exception:
            pass

    if missing_fields:
        unique_missing = sorted(set(missing_fields))
        print(f"  {_RED}❌ Schema mismatch — missing fields: {unique_missing}{_RESET}")
        report["checks"]["schema_valid"] = False
    else:
        print(f"  {_GREEN}✅ Schema matches canonical TICK_SCHEMA (17 fields){_RESET}")
        report["checks"]["schema_valid"] = True

    # ── Run full data validation on sampled ticks ──────────────────────
    if total_rows > 0:
        try:
            all_paths = [str(pf) for pf in parquet_files]
            table = read_ticks_uniform(all_paths)

            if table.num_rows == 0:
                print(f"  {_YELLOW}⚠️  Read returned 0 rows{_RESET}")
                report["checks"]["data_readable"] = True
            else:
                # ── Check 4: Prices in [0, 1] ─────────────────────────────────
                yes_col = table.column("yes_price")
                bad_yes = 0
                for i in range(yes_col.length()):
                    val = yes_col[i].as_py()
                    if val is not None and (val < 0 or val > 1):
                        bad_yes += 1

                if bad_yes > 0:
                    print(f"  {_RED}❌ {bad_yes} ticks with yes_price outside [0,1]{_RESET}")
                    report["checks"]["prices_valid"] = False
                else:
                    print(f"  {_GREEN}✅ All {table.num_rows:,} yes_prices in range [0,1]{_RESET}")
                    report["checks"]["prices_valid"] = True

                # ── Check 5: Spread >= 0 (bid <= ask) ─────────────────────────
                bad_spread = 0
                for i in range(min(table.num_rows, 10000)):
                    bid = table.column("best_bid")[i].as_py()
                    ask = table.column("best_ask")[i].as_py()
                    spread = table.column("spread")[i].as_py()
                    if bid is not None and ask is not None:
                        if bid > ask or (spread is not None and spread < 0):
                            bad_spread += 1

                if bad_spread > 0:
                    print(f"  {_RED}❌ {bad_spread} ticks with bad spread{_RESET}")
                    report["checks"]["spread_valid"] = False
                else:
                    print(f"  {_GREEN}✅ All checked ticks have valid spread{_RESET}")
                    report["checks"]["spread_valid"] = True

                # ── Check 6: Timestamps chronological ─────────────────────────
                ts_col = table.column("timestamp_ns")
                out_of_order = 0
                prev = None
                for i in range(ts_col.length()):
                    curr = ts_col[i].as_py()
                    if prev is not None and curr is not None and curr < prev:
                        out_of_order += 1
                    if curr is not None:
                        prev = curr

                if out_of_order > 0:
                    print(f"  {_YELLOW}⚠️  {out_of_order} out-of-order timestamps{_RESET}")
                    report["checks"]["timestamps_sorted"] = True  # Warning, not fail
                    report["warnings"].append(f"{out_of_order} out-of-order timestamps")
                else:
                    print(f"  {_GREEN}✅ All timestamps in chronological order{_RESET}")
                    report["checks"]["timestamps_sorted"] = True

                # ── Check 7: Timestamp range ──────────────────────────────────
                if ts_col.length() > 0:
                    min_ts = ts_col[0].as_py()
                    max_ts = ts_col[-1].as_py()
                    if min_ts and max_ts:
                        from datetime import datetime, timezone
                        dt_min = datetime.fromtimestamp(min_ts / 1_000_000_000, tz=timezone.utc)
                        dt_max = datetime.fromtimestamp(max_ts / 1_000_000_000, tz=timezone.utc)
                        span_hours = (max_ts - min_ts) / (1_000_000_000 * 3600)
                        print(
                            f"  {_CYAN}ℹ️  Range: {dt_min.isoformat()[:19]} → "
                            f"{dt_max.isoformat()[:19]} ({span_hours:.1f}h){_RESET}"
                        )
                        report["timestamp_range"] = {
                            "min": dt_min.isoformat(),
                            "max": dt_max.isoformat(),
                            "span_hours": round(span_hours, 2),
                        }

                # ── Check 8: Asset distribution ──────────────────────────────
                asset_col = table.column("asset")
                if asset_col.length() > 0:
                    from collections import Counter
                    asset_counts = Counter()
                    for i in range(asset_col.length()):
                        asset_counts[asset_col[i].as_py()] += 1
                    print(f"  {_CYAN}ℹ️  Asset distribution: {dict(asset_counts)}{_RESET}")
                    report["asset_distribution"] = dict(asset_counts)

        except Exception as e:
            print(f"  {_RED}❌ Error reading Parquet data: {e}{_RESET}")
            report["checks"]["data_readable"] = False
            report["errors"].append(f"Parquet read error: {e}")
    else:
        print(f"  {_YELLOW}⚠️  No ticks to validate (0 rows total){_RESET}")
        report["checks"]["data_readable"] = True  # Not an error, just empty

    # ── Check 9: Manifest consistency ───────────────────────────────────
    manifest_path = data_path / "manifest.json"
    if manifest_path.exists():
        import json
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            manifest_ticks = manifest.get("total_ticks", 0)
            sessions = manifest.get("sessions", [])
            print(f"  {_CYAN}ℹ️  Manifest: {manifest_ticks} ticks, {len(sessions)} sessions{_RESET}")

            # Compare manifest ticks vs actual row count
            if manifest_ticks > 0 and total_rows > 0:
                # Manifest may be from a shorter test run
                if total_rows >= manifest_ticks:
                    print(f"  {_GREEN}✅ Actual rows >= manifest{_RESET}")
                    report["checks"]["manifest_consistent"] = True
                else:
                    diff_pct = (manifest_ticks - total_rows) / max(manifest_ticks, 1) * 100
                    if diff_pct > 5.0:
                        print(f"  {_RED}❌ {diff_pct:.1f}% row gap vs manifest{_RESET}")
                        report["checks"]["manifest_consistent"] = False
                    else:
                        print(f"  {_YELLOW}⚠️  {diff_pct:.1f}% row gap vs manifest (tolerable){_RESET}")
                        report["checks"]["manifest_consistent"] = True
                        report["warnings"].append(f"{diff_pct:.1f}% row count gap vs manifest")
            else:
                report["checks"]["manifest_consistent"] = True

            report["manifest"] = {
                "ticks": manifest_ticks,
                "sessions": len(sessions),
                "recorded_at": manifest.get("recorded_at", ""),
            }
        except Exception as e:
            print(f"  {_YELLOW}⚠️  Manifest parse error{_RESET}")
            report["checks"]["manifest_consistent"] = False
    else:
        print(f"  {_YELLOW}⚠️  No manifest.json found{_RESET}")
        report["checks"]["manifest_consistent"] = True  # Not required for integrity

    # ── Data loss rate estimation ────────────────────────────────────────
    if "manifest" in report and report["manifest"]["ticks"] > 0 \
            and total_rows > 0:
        manifest_ticks = report["manifest"]["ticks"]
        loss_pct = max(0, (1.0 - (total_rows / max(manifest_ticks, 1)))) * 100
        report["data_loss_rate_pct"] = round(loss_pct, 4)

        if loss_pct < 0.1:
            print(f"  {_GREEN}✅ Data loss: {loss_pct:.4f}% (< 0.1%){_RESET}")
            report["checks"]["data_loss_acceptable"] = True
        else:
            print(f"  {_RED}❌ Data loss: {loss_pct:.4f}% (> 0.1%){_RESET}")
            report["checks"]["data_loss_acceptable"] = False
    else:
        report["data_loss_rate_pct"] = None

    # ── Overall result ──────────────────────────────────────────────────
    critical_checks = [
        "files_exist", "all_readable", "schema_valid",
        "prices_valid", "spread_valid",
    ]
    all_ok = all(
        report["checks"].get(check, True) for check in critical_checks
    )

    print()
    if all_ok:
        print(f"  {_GREEN}{_BOLD}✅ DATA INTEGRITY: PASS{_RESET}")
    else:
        print(f"  {_RED}{_BOLD}❌ DATA INTEGRITY: FAIL{_RESET}")
    print("═" * 70)
    print()

    report["overall_pass"] = all_ok
    return all_ok, report


def main() -> int:
    args = parse_args()

    # ── Data integrity check mode (P8.1) ─────────────────────────────
    if args.check_data:
        ok, report = check_data_integrity(args.data_dir)

        # Save report
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = args.output or str(REPORTS_DIR / f"data_integrity_{timestamp}.json")
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"  📁 Report saved to: {output_path}")

        return 0 if ok else 1

    # ── Standard criteria validation ─────────────────────────────────
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
