#!/usr/bin/env python3
"""
Headless live market data recorder for Polybot.

Designed for 24/7 deployment via K8s Deployment or systemd service.
Uses structlog for structured logging and exposes Prometheus metrics on a
separate HTTP port for scraping.

Differences from record_live_data.py:
    - No interactive prints (all logging via structlog)
    - Prometheus metrics server on configurable port
    - Only Parquet format (CSV deprecated for headless)
    - Indefinite recording by default (--duration-hours 0)
    - Graceful shutdown on SIGTERM/SIGINT with final manifest write

Usage:
    python scripts/record_live_headless.py --all
    python scripts/record_live_headless.py --asset BTC --metrics-port 9091
    python scripts/record_live_headless.py --all --duration-hours 168
"""

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import structlog
import websockets
from prometheus_client import start_http_server

from scripts.record_live_data import (
    WS_BASE_URL,
    WS_PING_INTERVAL,
    find_markets_for_asset,
    init_book_state,
    parse_market,
    parse_ws_message,
)
from src.infrastructure.data.storage import MultiAssetRecorder
from src.infrastructure.observability.metrics import (
    RECORDING_MARKETS_ACTIVE,
    RECORDING_STORAGE_SIZE_BYTES,
    RECORDING_TICKS_TOTAL,
    RECORDING_UPTIME_SECONDS,
    RECORDING_WS_RECONNECTS,
)

logger = structlog.get_logger(__name__)

DEFAULT_METRICS_PORT = 9091
DEFAULT_PARQUET_DIR = Path("data/parquet")
DEFAULT_BATCH_SIZE = 1000

_shutdown_requested = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headless live market data recorder (K8s/systemd)",
    )
    parser.add_argument("--asset", choices=["BTC", "ETH"],
                        help="Asset to record markets for")
    parser.add_argument("--all", action="store_true",
                        help="Record both BTC and ETH")
    parser.add_argument("--duration-hours", type=float, default=0.0,
                        help="Hours to record (0=indefinite, default: 0)")
    parser.add_argument("--output-dir", default=str(DEFAULT_PARQUET_DIR),
                        help=f"Parquet output directory (default: {DEFAULT_PARQUET_DIR})")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Buffer batch size (default: {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--metrics-port", type=int, default=DEFAULT_METRICS_PORT,
                        help=f"Prometheus metrics HTTP port (default: {DEFAULT_METRICS_PORT})")
    return parser.parse_args()


# ── Headless WS Listener (with Prometheus instrumentation) ────────────────────

async def listen_market_headless(
    token_id: str,
    market_id: str,
    asset: str,
    recorder: MultiAssetRecorder,
    duration_hours: float,
) -> int:
    """
    Connect to WebSocket and record ticks via MultiAssetRecorder.

    Updates Prometheus metrics: ticks_total, ticks_rate, ws_reconnects.
    Returns number of ticks recorded.
    """
    url = WS_BASE_URL
    start_time = asyncio.get_event_loop().time()
    tick_count = 0

    init_book_state(market_id, token_id)

    while not _shutdown_requested:
        try:
            async with websockets.connect(
                url,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=30,
                close_timeout=10,
            ) as ws:
                sub_msg = json.dumps({
                    "assets_ids": [token_id],
                    "type": "market",
                })
                await ws.send(sub_msg)
                await logger.ainfo("ws_connected",
                                   market_id=market_id[:16],
                                   asset=asset)

                while not _shutdown_requested:
                    elapsed = (asyncio.get_event_loop().time() - start_time) / 3600
                    if duration_hours > 0 and elapsed >= duration_hours:
                        await logger.ainfo("duration_reached",
                                           hours=duration_hours,
                                           ticks=tick_count)
                        return tick_count

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue

                    tick = parse_ws_message(market_id, raw, asset)
                    if tick:
                        recorder.record_tick(asset, tick)
                        tick_count += 1
                        RECORDING_TICKS_TOTAL.labels(asset=asset).inc()

        except asyncio.CancelledError:
            break
        except websockets.ConnectionClosed as e:
            RECORDING_WS_RECONNECTS.labels(asset=asset).inc()
            await logger.awarning("ws_disconnected",
                                  market_id=market_id[:16],
                                  reason=str(e)[:100])
            await asyncio.sleep(5)
        except Exception as e:
            RECORDING_WS_RECONNECTS.labels(asset=asset).inc()
            await logger.aerror("ws_error",
                                market_id=market_id[:16],
                                error=type(e).__name__,
                                detail=str(e)[:200])
            await asyncio.sleep(10)

    return tick_count


# ── Storage size updater (background task) ────────────────────────────────────

async def update_storage_metrics(output_dir: Path, interval: float = 60.0):
    """Periodically update RECORDING_STORAGE_SIZE_BYTES gauge."""
    while not _shutdown_requested:
        try:
            if output_dir.exists():
                total_size = sum(
                    f.stat().st_size
                    for f in output_dir.rglob("*.parquet")
                )
                RECORDING_STORAGE_SIZE_BYTES.set(total_size)
        except Exception:
            pass
        await asyncio.sleep(interval)


# ── Uptime updater (background task) ──────────────────────────────────────────

async def update_uptime_metrics(start_ts: float, interval: float = 15.0):
    """Periodically update RECORDING_UPTIME_SECONDS gauge."""
    while not _shutdown_requested:
        RECORDING_UPTIME_SECONDS.set(time.time() - start_ts)
        await asyncio.sleep(interval)


# ── Signal Handling ──────────────────────────────────────────────────────────

def setup_signal_handlers():
    def handler(sig, frame):
        global _shutdown_requested
        if not _shutdown_requested:
            logger.warning("shutdown_requested", signal=sig)
            _shutdown_requested = True
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    args = parse_args()
    setup_signal_handlers()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    start_ts = time.time()

    # ── Start Prometheus metrics server ─────────────────────────────────
    try:
        start_http_server(args.metrics_port)
        await logger.ainfo("metrics_server_started",
                           port=args.metrics_port)
    except OSError as e:
        await logger.aerror("metrics_server_failed",
                            port=args.metrics_port,
                            error=str(e))
        # Continue without metrics — don't fail the recording

    await logger.ainfo("recorder_starting",
                       output_dir=str(output_dir.absolute()),
                       duration_hours=args.duration_hours,
                       batch_size=args.batch_size)

    # ── Initialize recorder ──────────────────────────────────────────────
    recorder = MultiAssetRecorder(
        base_dir=output_dir,
        batch_size=args.batch_size,
        verbose=False,
    )

    # ── Asset discovery ──────────────────────────────────────────────────
    assets = []
    if args.all:
        assets = ["BTC", "ETH"]
    elif args.asset:
        assets = [args.asset]
    else:
        await logger.aerror("no_asset_specified")
        sys.exit(1)

    all_tasks = []
    total_markets = 0

    for asset in assets:
        await logger.ainfo("discovering_markets", asset=asset)

        markets = await find_markets_for_asset(asset)
        if not markets:
            await logger.awarning("no_markets_found", asset=asset)
            continue

        for m in markets:
            info = parse_market(m)
            if not info:
                continue

            ws_token_id = info.get("yes_token_id") or info.get("no_token_id")
            if not ws_token_id:
                await logger.awarning("no_token_id",
                                      market_id=info["condition_id"][:16])
                continue

            recorder.start_session(
                asset=asset,
                market_id=info["condition_id"],
                question=info["question"],
            )

            task = asyncio.create_task(
                listen_market_headless(
                    token_id=ws_token_id,
                    market_id=info["condition_id"],
                    asset=asset,
                    recorder=recorder,
                    duration_hours=args.duration_hours,
                ),
                name=f"ws_{info['condition_id'][:20]}",
            )
            all_tasks.append((task, info["condition_id"], info["question"], asset))
            total_markets += 1

    if not all_tasks:
        await logger.aerror("no_markets_to_record")
        sys.exit(1)

    RECORDING_MARKETS_ACTIVE.set(total_markets)

    # ── Background tasks ─────────────────────────────────────────────────
    bg_tasks = [
        asyncio.create_task(update_storage_metrics(output_dir), name="storage_metrics"),
        asyncio.create_task(update_uptime_metrics(start_ts), name="uptime_metrics"),
    ]

    await logger.ainfo("recording_started",
                       markets=total_markets,
                       assets=assets)

    # ── Wait for all listener tasks ──────────────────────────────────────
    try:
        results = await asyncio.gather(*[t for t, _, _, _ in all_tasks])
    except asyncio.CancelledError:
        results = []

    # ── Cancel background tasks ──────────────────────────────────────────
    for bt in bg_tasks:
        bt.cancel()
    await asyncio.gather(*bg_tasks, return_exceptions=True)

    # ── Finalize ─────────────────────────────────────────────────────────
    manifest = recorder.finalize_all()
    total_ticks = manifest.get("total_ticks", 0)

    # Build per-market session summaries
    sessions = []
    for idx, (_, cid, question, asset) in enumerate(all_tasks):
        ticks = results[idx] if idx < len(results) else 0
        sessions.append({
            "asset": asset,
            "market_id": cid,
            "question": question,
            "ticks": ticks,
        })

    # Write manifest
    manifest_path = output_dir / "manifest.json"
    manifest["sessions"] = sessions
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    # Storage size
    total_size = sum(
        f.stat().st_size for f in output_dir.rglob("*.parquet")
    ) if output_dir.exists() else 0
    RECORDING_STORAGE_SIZE_BYTES.set(total_size)

    elapsed = time.time() - start_ts

    await logger.ainfo("recorder_finished",
                       total_ticks=total_ticks,
                       markets=len(sessions),
                       elapsed_seconds=round(elapsed, 1),
                       storage_mb=round(total_size / 1024 / 1024, 2))


if __name__ == "__main__":
    asyncio.run(main())
