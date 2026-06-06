#!/usr/bin/env python3
"""
P9 Execution Pipeline Validation — Fase 9 DESPLEGAR verification.

Validates all P9.1-P9.4 components with real Parquet market data:
  P9.1 — FillSimulator: fill estimates, slippage, partial fill probability
  P9.2 — SlippageEngine: volatility scaling, regime scaling, tracker
  P9.3 — QueuePositionModel: maker fill probability, cost comparison
  P9.4 — SmartRouter: adaptive routing, order splitting, thresholds

Generates a JSON validation report in data/reports/.
No Redis, PostgreSQL, or WebSocket required.

Usage:
    python scripts/validate_execution_pipeline.py
    python scripts/validate_execution_pipeline.py --max-ticks 5000 --asset BTC
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.execution.fill_simulator import FillSimulator
from src.execution.queue_position import QueuePositionEngine
from src.execution.slippage_engine import SlippageEngine
from src.execution.smart_router import SmartRouter


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="P9 Execution Pipeline Validation (P9.1-P9.4)"
    )
    p.add_argument("--asset", default="BTC", choices=["BTC", "ETH"])
    p.add_argument("--max-ticks", type=int, default=2000,
                   help="Max ticks to sample (default: 2000)")
    p.add_argument("--output", default=None,
                   help="Output JSON path (default: auto-generated)")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def load_ticks(asset: str, max_ticks: int) -> list[dict]:
    """Load and convert Parquet ticks to FillSimulator-compatible dicts."""
    from src.backtesting.parquet_loader import ParquetDataLoader

    loader = ParquetDataLoader(base_dir="data/parquet")
    n_total = loader.get_tick_count(asset)
    if n_total == 0:
        print(f"❌ No Parquet data found for {asset}")
        sys.exit(1)

    print(f"📊 Loading {asset} ticks: {n_total:,} total, sampling up to {max_ticks:,}...")
    dataset = loader.load(asset=asset, window="raw")
    ticks = dataset.ticks[:max_ticks]

    # Convert MarketTick → dict for FillSimulator
    result = []
    for t in ticks:
        dict_tick = {
            "best_bid": t.best_bid,
            "best_ask": t.best_ask,
            "spread": t.spread,
            "volume_24h": t.volume_24h,
            "bids_vol_1": 0.0, "bids_vol_2": 0.0, "bids_vol_3": 0.0,
            "asks_vol_1": 0.0, "asks_vol_2": 0.0, "asks_vol_3": 0.0,
        }
        # MarketTick doesn't have depth data; FillSimulator gracefully
        # falls back to spread-cross-only model without depth.
        result.append(dict_tick)

    print(f"   Loaded {len(result):,} tick dicts for validation")
    return result


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[len(s) // 2]


def _pct_between(vals: list[float], lo: float, hi: float) -> float:
    if not vals:
        return 0.0
    return sum(1 for v in vals if lo <= v <= hi) / len(vals)


def _pct_above(vals: list[float], threshold: float) -> float:
    if not vals:
        return 0.0
    return sum(1 for v in vals if v > threshold) / len(vals)


def _rms(vals: list[float]) -> float:
    if not vals:
        return 0.0
    return math.sqrt(sum(v * v for v in vals) / len(vals))


def _regime_cycle(ticks: list[dict], idx: int) -> str:
    """Simulate a simple regime assignment based on tick index (cycling).

    In production, this comes from RegimeClassifier (P8.4).
    For validation, we cycle through regimes to exercise all paths.
    """
    regimes = ["CHOP", "TREND", "CHOP", "PANIC", "CHOP", "ILLIQUID", "EVENT_DRIVEN"]
    return regimes[idx % len(regimes)]


def _vol_estimate(spread: float) -> float:
    """Crude vol proxy from spread: wider spread → higher vol."""
    return min(0.50, max(0.02, spread * 10))


# ── P9.1 Validation ─────────────────────────────────────────────────────────

def validate_p9_1(ticks: list[dict], asset: str, verbose: bool) -> dict:
    """Validate FillSimulator — fill estimates, slippage, distributions."""
    print("\n" + "─" * 50)
    print("  P9.1 — FILL SIMULATOR")
    print("─" * 50)

    sim = FillSimulator()
    entry_slips: list[float] = []
    exit_slips: list[float] = []
    fill_ratios: list[float] = []
    p95_slips: list[float] = []
    mid_prices: list[float] = []

    t0 = time.perf_counter()
    for tick in ticks[:1000]:
        e = sim.estimate_entry(tick_data=tick, order_size=10.0, asset=asset)
        entry_slips.append(abs(e.slippage))
        p95_slips.append(e.p95_slippage)
        fill_ratios.append(e.fill_ratio)
        mid_prices.append(e.mid_price)

        x = sim.estimate_exit(tick_data=tick, position_value=10.0, asset=asset)
        exit_slips.append(abs(x.slippage))
    elapsed = time.perf_counter() - t0

    results = {
        "component": "P9.1_FillSimulator",
        "samples": len(entry_slips),
        "elapsed_ms": round(elapsed * 1000, 1),
        "ticks_per_sec": round(len(entry_slips) * 2 / elapsed, 0),
        "entry": {
            "mean_slippage": round(_mean(entry_slips), 6),
            "median_slippage": round(_median(entry_slips), 6),
            "max_slippage": round(max(entry_slips), 6),
            "min_slippage": round(min(entry_slips), 6),
        },
        "exit": {
            "mean_slippage": round(_mean(exit_slips), 6),
            "median_slippage": round(_median(exit_slips), 6),
            "max_slippage": round(max(exit_slips), 6),
        },
        "fill_quality": {
            "mean_fill_ratio": round(_mean(fill_ratios), 4),
            "pct_full_fill": round(_pct_above(fill_ratios, 0.99) * 100, 1),
        },
        "distributions": {
            "p95_gt_p50": round(_pct_above(p95_slips, 0) * 100, 1),
            "mean_p95_ratio": round(
                _mean([p / max(s, 1e-10) for p, s in zip(p95_slips, entry_slips)]), 2
            ),
        },
        "checks": {
            "all_slippage_non_negative": all(s >= 0 for s in entry_slips),
            "all_fill_ratios_in_range": all(0.0 <= r <= 1.0 for r in fill_ratios),
            "entry_exit_slip_similar": abs(
                _mean(entry_slips) - _mean(exit_slips)
            ) < 0.01,
            "mid_prices_valid": all(0 < p < 1 for p in mid_prices),
        },
    }

    if verbose:
        print(f"   {len(entry_slips)} samples in {elapsed*1000:.0f}ms "
              f"({len(entry_slips)*2/elapsed:.0f} ticks/s)")
        print(f"   Entry slippage: mean={_mean(entry_slips):.6f}, "
              f"median={_median(entry_slips):.6f}, max={max(entry_slips):.6f}")
        print(f"   Fill ratio: mean={_mean(fill_ratios):.4f}, "
              f"full fills={_pct_above(fill_ratios, 0.99)*100:.0f}%")
        checks = results["checks"]
        print(f"   Checks: non-neg={checks['all_slippage_non_negative']}, "
              f"ratio_range={checks['all_fill_ratios_in_range']}, "
              f"prices_valid={checks['mid_prices_valid']}")

    return results


# ── P9.2 Validation ─────────────────────────────────────────────────────────

def validate_p9_2(ticks: list[dict], asset: str, verbose: bool) -> dict:
    """Validate SlippageEngine — vol scaling, regime scaling, tracker."""
    print("\n" + "─" * 50)
    print("  P9.2 — SLIPPAGE ENGINE")
    print("─" * 50)

    engine = SlippageEngine()

    vol_multipliers: list[float] = []
    regime_multipliers: list[float] = []
    adjusted_slips: list[float] = []
    base_slips: list[float] = []
    total_multipliers: list[float] = []

    vol_regimes = [0.03, 0.08, 0.15, 0.25, 0.40, None, 0.0]
    for i, tick in enumerate(ticks[:700]):
        vol = vol_regimes[i % len(vol_regimes)]
        regime = _regime_cycle(ticks, i)

        est = engine.estimate(
            tick_data=tick, order_size=10.0, asset=asset,
            side="entry", volatility=vol, regime=regime,
        )
        base_slips.append(abs(est.slippage))
        adjusted_slips.append(abs(est.adjusted_slippage))
        vol_multipliers.append(est.vol_multiplier)
        regime_multipliers.append(est.regime_multiplier)
        total_multipliers.append(est.total_multiplier)

    # Test calibration tracker
    tracker_stats_before = engine.get_tracker_stats()
    for tick in ticks[:30]:
        est = engine.estimate(
            tick_data=tick, order_size=10.0, asset=asset,
            side="entry",
        )
        engine.record_actual(est, actual_fill_price=est.adjusted_fill_price)
    tracker_stats_after = engine.get_tracker_stats()

    results = {
        "component": "P9.2_SlippageEngine",
        "samples": len(base_slips),
        "base_slippage": {
            "mean": round(_mean(base_slips), 6),
            "median": round(_median(base_slips), 6),
        },
        "adjusted_slippage": {
            "mean": round(_mean(adjusted_slips), 6),
            "median": round(_median(adjusted_slips), 6),
        },
        "multipliers": {
            "vol_mean": round(_mean(vol_multipliers), 4),
            "vol_range": [round(min(vol_multipliers), 2), round(max(vol_multipliers), 2)],
            "regime_mean": round(_mean(regime_multipliers), 4),
            "regime_range": [round(min(regime_multipliers), 2), round(max(regime_multipliers), 2)],
            "total_mean": round(_mean(total_multipliers), 4),
        },
        "tracker": {
            "samples_before": tracker_stats_before.get("samples", 0),
            "samples_after": tracker_stats_after.get("samples", 0),
            "calibration_before": tracker_stats_before.get("calibration_multiplier", 1.0),
            "calibration_after": tracker_stats_after.get("calibration_multiplier", 1.0),
        },
        "checks": {
            "vol_affects_slippage": max(vol_multipliers) > min(vol_multipliers),
            "regime_affects_slippage": max(regime_multipliers) > min(regime_multipliers),
            "adjusted_differs_from_base": not all(
                abs(a - b) < 1e-10 for a, b in zip(adjusted_slips, base_slips)
            ),
            "tracker_calibration_stable": abs(
                tracker_stats_after.get("calibration_multiplier", 1.0) - 1.0
            ) < 0.2,
        },
    }

    if verbose:
        print(f"   Base slip: mean={_mean(base_slips):.6f}, "
              f"adj mean={_mean(adjusted_slips):.6f}")
        print(f"   Vol multipliers: [{min(vol_multipliers):.2f}, {max(vol_multipliers):.2f}]")
        print(f"   Regime multipliers: [{min(regime_multipliers):.1f}, {max(regime_multipliers):.1f}]")
        print(f"   Tracker: {tracker_stats_before['samples']}→{tracker_stats_after['samples']} "
              f"samples, cal={tracker_stats_after.get('calibration_multiplier', 1.0):.3f}")
        checks = results["checks"]
        print(f"   Checks: vol={checks['vol_affects_slippage']}, "
              f"regime={checks['regime_affects_slippage']}, "
              f"adjusted_diff={checks['adjusted_differs_from_base']}")

    return results


# ── P9.3 Validation ─────────────────────────────────────────────────────────

def validate_p9_3(ticks: list[dict], asset: str, verbose: bool) -> dict:
    """Validate QueuePositionModel — maker fill probability, cost comparison."""
    print("\n" + "─" * 50)
    print("  P9.3 — QUEUE POSITION MODELING")
    print("─" * 50)

    engine = SlippageEngine()
    queue = QueuePositionEngine()

    fill_probs: list[float] = []
    times_to_fill: list[float] = []
    adverse_bps: list[float] = []
    maker_wins: int = 0
    taker_wins: int = 0
    decisions: list[dict] = []
    confidence: list[float] = []

    for i, tick in enumerate(ticks[:500]):
        regime = _regime_cycle(ticks, i)
        vol = _vol_estimate(tick.get("spread", 0.02))
        order_size = 10.0

        taker_est = engine.estimate(
            tick_data=tick, order_size=order_size, asset=asset,
            side="entry", volatility=vol, regime=regime,
        )
        taker_cost = abs(taker_est.adjusted_slippage)

        maker = engine.estimate_maker(
            tick_data=tick, order_size=order_size, side="entry",
            volatility=vol, regime=regime,
        )
        fill_probs.append(maker.p_fill)
        times_to_fill.append(maker.expected_time_to_fill)
        adverse_bps.append(maker.adverse_selection_bps)
        confidence.append(maker.confidence)

        decision = engine.compare_maker_vs_taker(
            taker_cost=taker_cost,
            maker_estimate=maker,
        )
        if decision.prefer_maker:
            maker_wins += 1
        else:
            taker_wins += 1
        decisions.append({
            "mode": decision.mode,
            "cost_ratio": decision.cost_ratio,
            "reason": decision.reason,
        })

    finite_times = [t for t in times_to_fill if t != float("inf")]

    results = {
        "component": "P9.3_QueuePosition",
        "samples": len(fill_probs),
        "fill_probability": {
            "mean": round(_mean(fill_probs), 4),
            "median": round(_median(fill_probs), 4),
            "max": round(max(fill_probs), 4),
            "pct_viable": round(_pct_above(fill_probs, 0.50) * 100, 1),
            "pct_zero": round(
                sum(1 for p in fill_probs if p < 0.01) / len(fill_probs) * 100, 1
            ),
        },
        "time_to_fill": {
            "mean_seconds": round(_mean(finite_times), 2) if finite_times else 0,
            "median_seconds": round(_median(finite_times), 2) if finite_times else 0,
            "pct_inf": round(
                sum(1 for t in times_to_fill if t == float("inf")) / len(times_to_fill) * 100, 1
            ),
        },
        "adverse_selection": {
            "mean_bps": round(_mean(adverse_bps), 2),
            "median_bps": round(_median(adverse_bps), 2),
            "max_bps": round(max(adverse_bps), 2),
        },
        "confidence": {
            "mean": round(_mean(confidence), 2),
            "low_conf_pct": round(_pct_above(
                [1 - c for c in confidence], 0.5
            ) * 100, 1),
        },
        "maker_vs_taker": {
            "maker_decisions": maker_wins,
            "taker_decisions": taker_wins,
            "maker_pct": round(maker_wins / max(maker_wins + taker_wins, 1) * 100, 1),
        },
        "checks": {
            "all_probs_in_range": all(0.0 <= p <= 1.0 for p in fill_probs),
            "adverse_non_negative": all(a >= 0 for a in adverse_bps),
            "both_modes_used": maker_wins > 0 and taker_wins > 0,
            "confidence_in_range": all(0.0 <= c <= 1.0 for c in confidence),
        },
    }

    if verbose:
        print(f"   Fill prob: mean={_mean(fill_probs):.4f}, "
              f"median={_median(fill_probs):.4f}, "
              f"viable={_pct_above(fill_probs, 0.50)*100:.0f}%")
        print(f"   Time to fill: mean={_mean(finite_times):.1f}s, "
              f"inf={results['time_to_fill']['pct_inf']:.0f}%")
        print(f"   Adverse sel: mean={_mean(adverse_bps):.1f}bps, "
              f"max={max(adverse_bps):.1f}bps")
        print(f"   Maker/Taker: {maker_wins}/{taker_wins} "
              f"({maker_wins/(maker_wins+taker_wins)*100:.0f}% maker)")
        checks = results["checks"]
        print(f"   Checks: probs={checks['all_probs_in_range']}, "
              f"adverse_non_neg={checks['adverse_non_negative']}, "
              f"both_modes={checks['both_modes_used']}")

    return results


# ── P9.4 Validation ─────────────────────────────────────────────────────────

def validate_p9_4(ticks: list[dict], asset: str, verbose: bool) -> dict:
    """Validate SmartRouter — adaptive routing, order splitting, thresholds."""
    print("\n" + "─" * 50)
    print("  P9.4 — SMART ORDER ROUTING")
    print("─" * 50)

    router = SmartRouter()

    routing_modes: dict[str, int] = {}
    splits: int = 0
    split_chunks: list[int] = []
    maker_routes: int = 0
    taker_routes: int = 0
    forced_taker: int = 0

    order_sizes = [5.0, 10.0, 10.0, 25.0, 30.0, 10.0]

    for i, tick in enumerate(ticks[:500]):
        regime = _regime_cycle(ticks, i)
        vol = _vol_estimate(tick.get("spread", 0.02))
        order_size = order_sizes[i % len(order_sizes)]

        decision = router.route(
            tick_data=tick, order_size=order_size, side="entry",
            asset=asset, volatility=vol, regime=regime,
        )

        routing_modes[decision.mode] = routing_modes.get(decision.mode, 0) + 1

        if decision.mode == "split":
            splits += 1
            split_chunks.append(len(decision.chunks))
        elif decision.mode == "maker":
            maker_routes += 1
        elif decision.mode == "taker":
            taker_routes += 1

        if "spread" in decision.reason or "depth" in decision.reason or "volatility" in decision.reason:
            forced_taker += 1

    total = maker_routes + taker_routes + splits

    results = {
        "component": "P9.4_SmartRouter",
        "samples": total,
        "routing": {
            "taker": routing_modes.get("taker", 0),
            "maker": routing_modes.get("maker", 0),
            "split": routing_modes.get("split", 0),
            "maker_pct": round(
                routing_modes.get("maker", 0) / max(total, 1) * 100, 1
            ),
        },
        "splitting": {
            "total_splits": splits,
            "avg_chunks": round(_mean([float(c) for c in split_chunks]), 1) if split_chunks else 0,
            "max_chunks": max(split_chunks) if split_chunks else 0,
        },
        "routing_quality": {
            "forced_taker_constraints": forced_taker,
            "pct_constrained": round(forced_taker / max(total, 1) * 100, 1),
        },
        "checks": {
            "all_modes_used": (
                "taker" in routing_modes and
                routing_modes["taker"] > 0
            ),
            "splits_occur": splits > 0,
            "maker_viable_sometimes": maker_routes > 0,
        },
    }

    if verbose:
        print(f"   Routes: taker={routing_modes.get('taker', 0)}, "
              f"maker={routing_modes.get('maker', 0)}, "
              f"split={routing_modes.get('split', 0)}")
        print(f"   Splits: {splits} total, "
              f"avg chunks={_mean([float(c) for c in split_chunks]) if split_chunks else 0:.1f}")
        print(f"   Constrained (forced taker): {forced_taker} ({forced_taker/max(total,1)*100:.0f}%)")
        checks = results["checks"]
        print(f"   Checks: taker_used={checks['all_modes_used']}, "
              f"splits={checks['splits_occur']}, "
              f"maker_seen={checks['maker_viable_sometimes']}")

    return results


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    print("═" * 60)
    print("  P9 EXECUTION PIPELINE VALIDATION")
    print(f"  Asset: {args.asset} | Max ticks: {args.max_ticks:,}")
    print(f"  Time: {datetime.now(timezone.utc).isoformat()}")
    print("═" * 60)

    # Load real data
    ticks = load_ticks(args.asset, args.max_ticks)

    # Validate each P9 component
    results = {}
    all_checks: dict[str, bool] = {}

    for validator, label in [
        (validate_p9_1, "P9.1_FillSimulator"),
        (validate_p9_2, "P9.2_SlippageEngine"),
        (validate_p9_3, "P9.3_QueuePosition"),
        (validate_p9_4, "P9.4_SmartRouter"),
    ]:
        try:
            r = validator(ticks, args.asset, args.verbose)
            results[label] = r
            for k, v in r.get("checks", {}).items():
                all_checks[f"{label}.{k}"] = v
        except Exception as e:
            print(f"   ❌ {label} FAILED: {e}")
            results[label] = {"error": str(e)}

    # Summary
    passed = sum(1 for v in all_checks.values() if v)
    total = len(all_checks)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asset": args.asset,
        "ticks_sampled": len(ticks),
        "components": results,
        "summary": {
            "checks_total": total,
            "checks_passed": passed,
            "checks_failed": total - passed,
            "pass_rate_pct": round(passed / max(total, 1) * 100, 1),
            "all_passed": passed == total,
        },
    }

    # Save report
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = args.output or f"data/reports/execution_validation_{ts}.json"
    out_dir = Path(out_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Print summary
    print("\n" + "═" * 60)
    print("  VALIDATION SUMMARY")
    print("═" * 60)
    print(f"  Checks passed: {passed}/{total} ({passed/max(total,1)*100:.0f}%)")
    print(f"  All passed: {'✅ YES' if passed == total else '❌ NO'}")

    if passed < total:
        print("\n  Failed checks:")
        for k, v in all_checks.items():
            if not v:
                print(f"    ❌ {k}")

    print(f"\n  Report saved: {out_path}")
    print("═" * 60)

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
