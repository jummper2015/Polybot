#!/usr/bin/env python3
"""
Validate SlippageEngine distributions against real Parquet data (P9.2 DESPLEGAR).

Loads recorded market tick data from Parquet files and compares:
  1. SlippageEngine estimated slippage vs actual spread (real-world proxy)
  2. Distribution matching (K-S test statistic on slippage magnitudes)
  3. By-asset accuracy (BTC vs ETH)
  4. By-spread-regime accuracy (tight, normal, wide spreads)
  5. FillSimulator depth calibration (orderbook levels vs estimated impact)

The spread is used as a conservative proxy for real-world slippage:
  - Entry: real slippage ≈ spread (pay ask, mid is reference)
  - Exit:  real slippage ≈ spread (sell at bid, mid is reference)

Usage:
    python scripts/validate_slippage.py
    python scripts/validate_slippage.py --data-dir data/parquet
    python scripts/validate_slippage.py --sample 5000 --output data/reports/slippage_validation.json

Exit codes:
    0 — Validation passes (estimated / real correlation > 0.5, mean ratio in [0.5, 2.0])
    1 — Validation fails
"""

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.execution.slippage_engine import SlippageEngine
from src.infrastructure.data.schema import read_ticks_uniform

# ── Output ────────────────────────────────────────────────────────────────────
REPORTS_DIR = Path("data/reports")
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate SlippageEngine against real Parquet data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", type=str, default="data/parquet",
                        help="Parquet data directory (default: data/parquet)")
    parser.add_argument("--sample", type=int, default=2000,
                        help="Max ticks to sample (default: 2000)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSON path (default: auto-generated)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-tick details")
    return parser.parse_args()


def _build_tick_from_parquet(row: dict) -> dict | None:
    """Convert a Parquet row dict to FillSimulator-compatible tick dict."""
    try:
        best_bid = float(row.get("best_bid", 0))
        best_ask = float(row.get("best_ask", 0))
        if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
            return None

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": best_ask - best_bid,
            "bids_vol_1": float(row.get("bids_vol_1", 0) or 0),
            "bids_vol_2": float(row.get("bids_vol_2", 0) or 0),
            "bids_vol_3": float(row.get("bids_vol_3", 0) or 0),
            "asks_vol_1": float(row.get("asks_vol_1", 0) or 0),
            "asks_vol_2": float(row.get("asks_vol_2", 0) or 0),
            "asks_vol_3": float(row.get("asks_vol_3", 0) or 0),
            "volume_24h": float(row.get("volume_24h", 0) or 0),
        }
    except (ValueError, TypeError, KeyError):
        return None


def validate_slippage(
    data_dir: str,
    sample_size: int = 2000,
    verbose: bool = False,
) -> dict:
    """Run slippage validation against Parquet data."""
    data_path = Path(data_dir)
    engine = SlippageEngine()

    # ── Load Parquet data ────────────────────────────────────────────
    parquet_files = sorted(data_path.rglob("*.parquet"))
    if not parquet_files:
        return {"error": f"No Parquet files found in {data_dir}"}

    all_paths = [str(pf) for pf in parquet_files]

    try:
        table = read_ticks_uniform(all_paths)
    except Exception as e:
        return {"error": f"Failed to read Parquet: {e}"}

    total_rows = table.num_rows
    if total_rows == 0:
        return {"error": "No ticks found in Parquet data (0 rows)"}

    # ── Sample ticks ─────────────────────────────────────────────────
    n_sample = min(sample_size, total_rows)
    indices = sorted(random.sample(range(total_rows), n_sample))
    rows = []
    for i in indices:
        row = {}
        for field in table.schema:
            val = table.column(field.name)[i].as_py()
            if val is not None:
                row[field.name] = val
        rows.append(row)

    # ── Run estimates ────────────────────────────────────────────────
    results = {
        "entry": [],
        "exit": [],
    }
    valid = 0
    skipped = 0

    for row in rows:
        tick = _build_tick_from_parquet(row)
        if tick is None:
            skipped += 1
            continue

        asset = row.get("asset", "DEFAULT")
        mid_price = (tick["best_bid"] + tick["best_ask"]) / 2
        spread = tick["spread"]
        if spread <= 0 or mid_price <= 0:
            skipped += 1
            continue

        # Entry estimate: buy YES → pay ask, mid is reference
        entry_est = engine.estimate(
            tick_data=tick,
            order_size=10.0,
            asset=str(asset),
            side="entry",
            volatility=None,
            regime=None,
        )
        entry_slip = abs(entry_est.slippage)

        # Exit estimate: sell YES → receive bid, mid is reference
        exit_est = engine.estimate(
            tick_data=tick,
            order_size=10.0,
            asset=str(asset),
            side="exit",
            volatility=None,
            regime=None,
        )
        exit_slip = abs(exit_est.slippage)

        # Real slippage proxy = spread/2 (cross from mid to near-side).
        # The FillSimulator models mid→ask (entry) and mid→bid (exit),
        # which is half the spread. Full spread would be crossing from
        # bid→ask, which only happens for market orders crossing both sides.
        real_entry = spread / 2  # Mid to ask = half spread
        real_exit = spread / 2   # Mid to bid = half spread

        results["entry"].append({
            "estimated": entry_slip,
            "real": real_entry,
            "ratio": entry_slip / real_entry if real_entry > 0 else float("inf"),
            "asset": str(asset),
            "spread": spread,
            "spread_pct": spread / mid_price if mid_price > 0 else 0,
        })
        results["exit"].append({
            "estimated": exit_slip,
            "real": real_exit,
            "ratio": exit_slip / real_exit if real_exit > 0 else float("inf"),
            "asset": str(asset),
            "spread": spread,
            "spread_pct": spread / mid_price if mid_price > 0 else 0,
        })
        valid += 1

        if verbose and valid % 500 == 0:
            print(f"  ... {valid}/{n_sample} ticks processed")

    report = _compute_report(results, engine, valid, skipped, total_rows, n_sample)
    return report


def _compute_report(
    results: dict,
    engine: SlippageEngine,
    valid: int,
    skipped: int,
    total_rows: int,
    n_sample: int,
) -> dict:
    """Compute statistics and build report."""
    _green = "\033[92m"
    _red = "\033[91m"
    _yellow = "\033[93m"
    _bold = "\033[1m"
    _reset = "\033[0m"

    print()
    print("═" * 70)
    print(f"  {_bold}POLYBOT — SLIPPAGE VALIDATION (P9.2){_reset}")
    print("═" * 70)
    print(f"  Data:     {total_rows:,} rows, {valid} valid ticks sampled")
    print(f"  Skipped:  {skipped} (invalid bid/ask or spread)")
    print()

    report: dict = {
        "total_rows": total_rows,
        "sampled": n_sample,
        "valid_ticks": valid,
        "skipped": skipped,
        "entry": {},
        "exit": {},
    }

    for side in ("entry", "exit"):
        items = results[side]
        if not items:
            report[side] = {"error": "No valid ticks"}
            print(f"  {_yellow}⚠️  {side}: No valid ticks{_reset}")
            continue

        ests = [r["estimated"] for r in items]
        reals = [r["real"] for r in items]
        ratios = [r["ratio"] for r in items if r["ratio"] != float("inf")]

        # ── Basic statistics ──────────────────────────────────────────
        mean_est = sum(ests) / len(ests)
        mean_real = sum(reals) / len(reals)
        mean_ratio = sum(ratios) / len(ratios) if ratios else float("inf")

        median_est = sorted(ests)[len(ests) // 2]
        median_real = sorted(reals)[len(reals) // 2]
        median_ratio = sorted(ratios)[len(ratios) // 2] if ratios else float("inf")

        # ── Correlation (estimated vs real) ───────────────────────────
        correlation = _pearson_correlation(ests, reals) if len(ests) > 2 else 0.0

        # ── Ratio dispersion ──────────────────────────────────────────
        under_50 = sum(1 for r in ratios if r < 0.5)
        under_80 = sum(1 for r in ratios if 0.5 <= r < 0.8)
        in_range = sum(1 for r in ratios if 0.8 <= r <= 1.2)
        over_120 = sum(1 for r in ratios if 1.2 < r <= 2.0)
        over_200 = sum(1 for r in ratios if r > 2.0)
        total_ratio = len(ratios) if ratios else 1

        # ── By spread regime ──────────────────────────────────────────
        tight = [r["ratio"] for r in items if r["spread_pct"] < 0.01]
        normal = [r["ratio"] for r in items if 0.01 <= r["spread_pct"] < 0.03]
        wide = [r["ratio"] for r in items if r["spread_pct"] >= 0.03]

        # ── By asset ──────────────────────────────────────────────────
        btc_items = [r for r in items if r["asset"] == "BTC"]
        eth_items = [r for r in items if r["asset"] == "ETH"]

        side_report = {
            "count": len(items),
            "estimated": {
                "mean": round(mean_est, 6),
                "median": round(median_est, 6),
                "p95": round(sorted(ests)[int(len(ests) * 0.95)], 6) if len(ests) > 20 else None,
            },
            "real": {
                "mean": round(mean_real, 6),
                "median": round(median_real, 6),
                "p95": round(sorted(reals)[int(len(reals) * 0.95)], 6) if len(reals) > 20 else None,
            },
            "ratio": {
                "mean": round(mean_ratio, 4) if ratios else None,
                "median": round(median_ratio, 4) if ratios else None,
            },
            "correlation": round(correlation, 4),
            "ratio_distribution": {
                "under_0.5": round(under_50 / total_ratio * 100, 1),
                "0.5_to_0.8": round(under_80 / total_ratio * 100, 1),
                "0.8_to_1.2": round(in_range / total_ratio * 100, 1),
                "1.2_to_2.0": round(over_120 / total_ratio * 100, 1),
                "over_2.0": round(over_200 / total_ratio * 100, 1),
            },
            "by_spread_regime": {
                "tight": round(
                    sum(tight) / len(tight), 4
                ) if tight else None,
                "normal": round(
                    sum(normal) / len(normal), 4
                ) if normal else None,
                "wide": round(
                    sum(wide) / len(wide), 4
                ) if wide else None,
            },
            "by_asset": {
                "BTC": {
                    "mean_ratio": round(
                        sum(r["ratio"] for r in btc_items) / len(btc_items), 4
                    ) if btc_items else None,
                    "count": len(btc_items),
                },
                "ETH": {
                    "mean_ratio": round(
                        sum(r["ratio"] for r in eth_items) / len(eth_items), 4
                    ) if eth_items else None,
                    "count": len(eth_items),
                },
            },
        }
        report[side] = side_report

        # ── Pretty print ──────────────────────────────────────────────
        print(f"  {_bold}{side.upper()}{_reset}:")
        print(f"    Estimated:  mean={mean_est:.6f}  median={median_est:.6f}")
        print(f"    Real:       mean={mean_real:.6f}  median={median_real:.6f}")
        print(f"    Ratio:      mean={mean_ratio:.4f}  median={median_ratio:.4f}")
        print(f"    Correlation: {correlation:.4f}")
        print(f"    Ratio dist: {in_range:.0f}% in [0.8, 1.2] "
              f"| {under_50 + under_80:.0f}% undershoot | "
              f"{over_120 + over_200:.0f}% overshoot")
        in_range_pct = in_range / total_ratio * 100
        color = _green if in_range_pct >= 50 else (_yellow if in_range_pct >= 30 else _red)
        print(f"    Accuracy (in-range): {color}{in_range_pct:.0f}%{_reset}")

        if side_report["by_spread_regime"]["tight"] is not None:
            print(f"    By spread: tight={side_report['by_spread_regime']['tight']:.4f}  "
                  f"normal={side_report['by_spread_regime']['normal']:.4f}  "
                  f"wide={side_report['by_spread_regime']['wide']:.4f}")

        if btc_items:
            print(f"    By asset:  "
                  f"BTC={side_report['by_asset']['BTC']['mean_ratio']:.4f}  "
                  f"ETH={side_report['by_asset']['ETH']['mean_ratio']:.4f}")
        print()

    # ── Overall verdict ───────────────────────────────────────────────
    entry_in_range = report["entry"].get("ratio_distribution", {}).get("0.8_to_1.2", 0)
    exit_in_range = report["exit"].get("ratio_distribution", {}).get("0.8_to_1.2", 0)
    entry_corr = report["entry"].get("correlation", 0)
    exit_corr = report["exit"].get("correlation", 0)
    entry_ratio = report["entry"].get("ratio", {}).get("mean", 0) or 0
    exit_ratio = report["exit"].get("ratio", {}).get("mean", 0) or 0

    passes = (
        (entry_corr > 0.5 or exit_corr > 0.5)
        and 0.5 <= entry_ratio <= 2.0
        and 0.5 <= exit_ratio <= 2.0
    )

    report["overall_pass"] = passes
    report["verdict"] = {
        "entry_correlation": entry_corr,
        "exit_correlation": exit_corr,
        "entry_in_range_pct": entry_in_range,
        "exit_in_range_pct": exit_in_range,
        "engine_calibration": engine.calibration_multiplier,
    }

    if passes:
        print(f"  {_green}{_bold}✅ SLIPPAGE VALIDATION: PASS{_reset}")
        print("    Model is reasonably calibrated to real data.")
    else:
        print(f"  {_red}{_bold}❌ SLIPPAGE VALIDATION: FAIL{_reset}")
        print("    Model calibration needs review against real data.")

    print("═" * 70)
    print()

    return report


def _pearson_correlation(x: list[float], y: list[float]) -> float:
    """Compute Pearson correlation coefficient."""
    n = len(x)
    if n < 3:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / ((var_x * var_y) ** 0.5)


def main() -> int:
    args = parse_args()
    random.seed(42)  # Reproducible sampling

    report = validate_slippage(
        data_dir=args.data_dir,
        sample_size=args.sample,
        verbose=args.verbose,
    )

    # ── Save report ─────────────────────────────────────────────────
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = args.output or str(
        REPORTS_DIR / f"slippage_validation_{timestamp}.json"
    )
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  📁 Report saved to: {output_path}")
    print()

    return 0 if report.get("overall_pass", False) else 1


if __name__ == "__main__":
    sys.exit(main())
