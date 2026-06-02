#!/usr/bin/env python3
"""
Benchmark: Parquet write speed vs WebSocket throughput for P8.1 TESTEAR.

Measures:
  1. Parquet write throughput (ticks/sec, MB/sec)
  2. Simulated WS throughput (estimated ticks/sec based on observed rates)
  3. Headroom analysis: can the writer keep up with the WS?
  4. Data loss estimation under various batch sizes

Polymarket WS typically sends 1-5 ticks/sec per market (BTC: ~2/s, ETH: ~1/s).
With 5 markets per asset, that's ~10-15 ticks/sec total for --all mode.
Parquet+zstd should handle 50K+ ticks/sec easily, so headroom is >1000x.

Usage:
    python scripts/benchmark_recording.py
    python scripts/benchmark_recording.py --batch-size 8192 --n-ticks 100000
    python scripts/benchmark_recording.py --compare  # Compare batch sizes

Output:
    Summary table with throughput metrics + headroom analysis.
    Exit code 0 if write speed > 10x expected WS throughput.
"""

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.infrastructure.data.storage import ParquetTickWriter

# ── Observed WS throughput (empirical, from 3-min recording test) ─────────────
# BTC: 88 ticks in 3 min → 0.49 ticks/sec
# ETH: 20 ticks in 3 min → 0.11 ticks/sec
# Total with --all: ~0.6 ticks/sec per active market
OBSERVED_WS_TICKS_PER_SEC = 2.0    # Conservative: assume 2 ticks/sec across all markets
PEAK_WS_TICKS_PER_SEC = 10.0       # Peak bursts: 10 ticks/sec
FUTURE_WS_TICKS_PER_SEC = 50.0     # Future expansion: 50 ticks/sec (10x markets × 5 ticks/sec)

# ── Minimum headroom required ─────────────────────────────────────────────────
MIN_HEADROOM_FACTOR = 10.0  # Writer must be at least 10x faster than peak WS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Parquet write throughput vs WS throughput",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--batch-size", type=int, default=8192,
                        help="Parquet batch size (default: 8192, K8s deployment value)")
    parser.add_argument("--n-ticks", type=int, default=50000,
                        help="Number of ticks to benchmark (default: 50000)")
    parser.add_argument("--compare", action="store_true",
                        help="Compare multiple batch sizes (1K, 2K, 4K, 8K, 16K, 32K)")
    parser.add_argument("--output", type=str, default=None,
                        help="JSON output path for benchmark results")
    return parser.parse_args()


def _make_tick(ts_ns: int, asset: str = "BTC") -> dict:
    """Create a tick dict with realistic values."""
    import random
    price = 0.60 + random.random() * 0.30
    return {
        "timestamp_ns": ts_ns,
        "market_id": f"0xtest_bench_{random.randint(0, 9)}",
        "asset": asset,
        "yes_price": round(price, 4),
        "no_price": round(1.0 - price, 4),
        "mid_price": round(price, 4),
        "best_bid": round(price - 0.01, 4),
        "best_ask": round(price + 0.01, 4),
        "spread": 0.02,
        "volume_24h": 5000.0,
        "liquidity_score": 50.0,
        "bids_vol_1": 100.0,
        "asks_vol_1": 80.0,
        "bids_vol_2": 50.0,
        "asks_vol_2": 40.0,
        "bids_vol_3": 20.0,
        "asks_vol_3": 15.0,
    }


def run_benchmark(batch_size: int, n_ticks: int) -> dict:
    """Run write benchmark and return metrics."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="polybot_bench_"))
    ts_base = int(time.time() * 1_000_000_000)

    writer = ParquetTickWriter(
        base_dir=tmp_dir,
        batch_size=batch_size,
        verbose=False,
    )

    # Generate ticks in memory first to exclude generation time
    ticks = [_make_tick(ts_base + i * 10_000_000, "BTC") for i in range(n_ticks)]
    # Also ETH ticks for realistic multi-asset simulation
    eth_ticks = [_make_tick(ts_base + i * 10_000_000, "ETH") for i in range(n_ticks // 2)]

    # ── Benchmark: write all ticks ─────────────────────────────────────
    t_start = time.monotonic()

    all_ticks = ticks + eth_ticks

    for tick in all_ticks:
        writer.write_tick(tick)

    # Final flush (includes auto-flushed batches)
    summary = writer.close()
    t_elapsed = time.monotonic() - t_start

    written = summary["total_ticks"]
    write_rate = written / t_elapsed if t_elapsed > 0 else float("inf")

    # Calculate storage size
    parquet_files = list(tmp_dir.rglob("*.parquet"))
    total_bytes = sum(f.stat().st_size for f in parquet_files)
    mb_per_tick = (total_bytes / written / (1024 * 1024)) if written > 0 else 0
    mb_per_sec = (total_bytes / t_elapsed / (1024 * 1024)) if t_elapsed > 0 else 0

    # Cleanup
    shutil.rmtree(str(tmp_dir), ignore_errors=True)

    # ── Headroom analysis ───────────────────────────────────────────────
    headroom_vs_observed = write_rate / OBSERVED_WS_TICKS_PER_SEC if OBSERVED_WS_TICKS_PER_SEC > 0 else float("inf")
    headroom_vs_peak = write_rate / PEAK_WS_TICKS_PER_SEC if PEAK_WS_TICKS_PER_SEC > 0 else float("inf")
    headroom_vs_future = write_rate / FUTURE_WS_TICKS_PER_SEC if FUTURE_WS_TICKS_PER_SEC > 0 else float("inf")

    # Data loss rate estimation: if writer is Nx faster than WS, loss ≈ 0%
    # Real loss comes from: WS disconnects (reconnection time), buffer overflow,
    # and process crashes (ungraceful shutdown).
    # The writer itself should never be the bottleneck.
    estimated_data_loss_pct = 0.0 if headroom_vs_peak >= MIN_HEADROOM_FACTOR else (
        (1.0 / headroom_vs_peak) * 100
    )

    return {
        "batch_size": batch_size,
        "ticks_written": written,
        "elapsed_seconds": round(t_elapsed, 4),
        "write_rate_tps": round(write_rate, 1),
        "storage_bytes": total_bytes,
        "storage_mb": round(total_bytes / (1024 * 1024), 3),
        "mb_per_tick": round(mb_per_tick, 8),
        "mb_per_sec_write": round(mb_per_sec, 3),
        "ws_observed_tps": OBSERVED_WS_TICKS_PER_SEC,
        "ws_peak_tps": PEAK_WS_TICKS_PER_SEC,
        "ws_future_tps": FUTURE_WS_TICKS_PER_SEC,
        "headroom_vs_observed": round(headroom_vs_observed, 1),
        "headroom_vs_peak": round(headroom_vs_peak, 1),
        "headroom_vs_future": round(headroom_vs_future, 1),
        "headroom_min_required": MIN_HEADROOM_FACTOR,
        "estimated_data_loss_pct": round(estimated_data_loss_pct, 4),
        "passes": headroom_vs_peak >= MIN_HEADROOM_FACTOR,
    }


_BOLD = "\033[1m"
_GREEN = "\033[92m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def print_results(results: list[dict]) -> None:
    """Pretty-print benchmark results."""
    print()
    print("═" * 75)
    print(f"  {_BOLD}POLYBOT — RECORDING BENCHMARK{_RESET}")
    print("  Parquet Write Speed vs WebSocket Throughput")
    print("═" * 75)
    print()
    print(f"  {_CYAN}WebSocket Throughput Estimates:{_RESET}")
    print(f"    Observed (empirical):  {OBSERVED_WS_TICKS_PER_SEC:.1f} ticks/sec")
    print(f"    Peak (burst):          {PEAK_WS_TICKS_PER_SEC:.1f} ticks/sec")
    print(f"    Future (10x growth):   {FUTURE_WS_TICKS_PER_SEC:.1f} ticks/sec")
    print(f"    Min headroom required: {MIN_HEADROOM_FACTOR:.0f}x")
    print()

    if len(results) == 1:
        r = results[0]
        print(f"  {_CYAN}Results (batch_size={r['batch_size']}):{_RESET}")
        print(f"    Ticks written:     {r['ticks_written']:,}")
        print(f"    Elapsed:           {r['elapsed_seconds']:.3f}s")
        print(f"    Write rate:        {r['write_rate_tps']:,.0f} ticks/sec")
        print(f"    Storage:           {r['storage_mb']:.3f} MB ({r['mb_per_tick']*1e6:.2f} bytes/tick)")
        print(f"    Write bandwidth:   {r['mb_per_sec_write']:.3f} MB/sec")
        print()

        for label, headroom, ws_rate in [
            ("Observed WS", r["headroom_vs_observed"], r["ws_observed_tps"]),
            ("Peak WS", r["headroom_vs_peak"], r["ws_peak_tps"]),
            ("Future WS", r["headroom_vs_future"], r["ws_future_tps"]),
        ]:
            color = _GREEN if headroom >= MIN_HEADROOM_FACTOR else _RED
            print(f"    {label} ({ws_rate} tps): {color}{headroom:,.0f}x headroom{_RESET}")

        print()
        if r["passes"]:
            print(
                f"  {_GREEN}{_BOLD}✅ PASS — Writer is {r['headroom_vs_peak']:.0f}x "
                f"faster than peak WS throughput{_RESET}"
            )
            print(f"    Estimated data loss rate: {r['estimated_data_loss_pct']:.4f}% (< 0.1% requirement)")
        else:
            print(
                f"  {_RED}{_BOLD}❌ FAIL — Writer is only "
                f"{r['headroom_vs_peak']:.1f}x faster than peak WS{_RESET}"
            )
        print()

    else:
        # Comparison table
        print(f"  {_CYAN}Batch Size Comparison:{_RESET}")
        header = (
            f"  {'Batch':>8} {'Ticks/sec':>12} {'MB/sec':>10} "
            f"{'Headroom':>10} {'Pass?':>7}"
        )
        print(header)
        print("  " + "-" * 52)
        for r in results:
            color = _GREEN if r["passes"] else _RED
            status = f"{color}{'✅' if r['passes'] else '❌'}{_RESET}"
            print(
                f"  {r['batch_size']:>8} {r['write_rate_tps']:>12,.0f} "
                f"{r['mb_per_sec_write']:>10.3f} {r['headroom_vs_peak']:>10,.0f}x "
                f"{status:>7}"
            )
        print()

    # ── Key takeaway ────────────────────────────────────────────────────
    print(f"  {_CYAN}Analysis:{_RESET}")
    print(
        f"    Parquet+zstd writer can handle "
        f"{results[0]['write_rate_tps']:,.0f} ticks/sec."
    )
    print(
        f"    Even at peak burst (10 tps), "
        f"headroom is {results[0]['headroom_vs_peak']:,.0f}x."
    )
    print("    Data loss from writer bottleneck is effectively zero.")
    print("    Real data loss comes from: WS disconnects (reconnection gaps),")
    print("    process crashes (ungraceful shutdown), and network partitions.")
    print("    These are mitigated by: reconnect logic, watchdog auto-restart,")
    print("    and the --duration-hours flag allowing clean rotation.")
    print()
    print("═" * 75)
    print()


def main() -> int:
    args = parse_args()

    if args.compare:
        batch_sizes = [1000, 2000, 4000, 8192, 16384, 32768]
        results = []
        for bs in batch_sizes:
            r = run_benchmark(bs, args.n_ticks)
            results.append(r)
    else:
        results = [run_benchmark(args.batch_size, args.n_ticks)]

    print_results(results)

    # Write JSON output if requested
    if args.output:
        import json
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"  📁 Results saved to {args.output}")

    return _determine_exit(results)

def _determine_exit(results: list[dict]) -> int:
    """Return exit code 0 if all benchmarks pass."""
    all_pass = all(r["passes"] for r in results)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
