#!/usr/bin/env python3
"""
Live market data recorder for Polymarket.

Connects to the WebSocket feed and records real-time ticks to CSV files.
Useful for building historical datasets when the /prices-history API
does not have sufficient data for backtesting.

Usage:
    # Record ALL active BTC/ETH markets (auto-discovery)
    python scripts/record_live_data.py --asset BTC --duration-hours 24

    # Record specific market by condition_id
    python scripts/record_live_data.py --market-id 0xabc... --duration-hours 8

    # Record both assets indefinitely (Ctrl+C to stop)
    python scripts/record_live_data.py --all

Output:
    data/historical/live_{asset}_{market_id}_{date}.csv
    Each row: timestamp, yes_price, no_price, best_bid, best_ask, spread, volume_24h

Architecture:
    Connects to Polymarket WS (wss://ws-subscriptions-clob.polymarket.com)
    Subscribes to orderbook channel for each market
    Saves every significant tick to CSV (flushed every 100 ticks)
"""

import argparse
import asyncio
import csv
import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import websockets

# Polymarket endpoints
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
WS_BASE_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Defaults
DEFAULT_OUTPUT_DIR = Path("data/historical")
FLUSH_EVERY_N_TICKS = 100
WS_PING_INTERVAL = 20

# Global flag for graceful shutdown
_shutdown_requested = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record live Polymarket market data via WebSocket",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/record_live_data.py --asset BTC --duration-hours 24
  python scripts/record_live_data.py --asset ETH --duration-hours 12
  python scripts/record_live_data.py --all --duration-hours 48
  python scripts/record_live_data.py --market-id 0xabc... --duration-hours 8
        """,
    )
    parser.add_argument("--asset", choices=["BTC", "ETH"],
                        help="Asset to record markets for")
    parser.add_argument("--all", action="store_true",
                        help="Record both BTC and ETH")
    parser.add_argument("--market-id",
                        help="Record a specific condition_id")
    parser.add_argument("--duration-hours", type=float, default=24.0,
                        help="Hours to record (default: 24, 0=indefinite)")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every tick received")
    return parser.parse_args()


async def find_markets_for_asset(asset: str) -> list[dict]:
    """Find active markets for a given asset via Gamma API."""
    async with httpx.AsyncClient(timeout=30) as client:
        # Search by question text for relevant keywords
        keywords = ["bitcoin", "btc"] if asset == "BTC" else ["ethereum", "eth"]

        all_markets = []
        # Try closed=false for active only
        for closed_flag in [False, True]:
            response = await client.get(
                f"{GAMMA_BASE_URL}/markets",
                params={
                    "active": str(not closed_flag).lower(),
                    "closed": str(closed_flag).lower(),
                    "_limit": "50",
                },
            )
            response.raise_for_status()
            markets = response.json()
            all_markets.extend(markets)

        # Filter by question keywords
        matching = []
        seen = set()
        for m in all_markets:
            cid = m.get("conditionId", "")
            q = m.get("question", "").lower()
            if cid and cid not in seen and any(k in q for k in keywords):
                seen.add(cid)
                matching.append(m)

        return matching[:5]  # Top 5 by volume


def parse_market(m: dict) -> dict | None:
    """Extract market info from Gamma API response."""
    cid = m.get("conditionId", "")
    if not cid:
        return None
    clob_tokens = m.get("clobTokenIds", [])
    return {
        "condition_id": cid,
        "question": m.get("question", "")[:120],
        "yes_token_id": clob_tokens[0] if clob_tokens else None,
        "active": m.get("active", False),
    }


class TickRecorder:
    """Records ticks from WebSocket to CSV files, one per market."""

    def __init__(self, output_dir: Path, verbose: bool = False):
        self._dir = output_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._verbose = verbose

        # market_id → {"file": handle, "writer": csv.DictWriter, "count": int}
        self._writers: dict[str, dict] = {}

    def _open_writer(self, market_id: str, asset: str) -> None:
        """Create a CSV writer for a new market."""
        short_id = market_id[:10] + ".." + market_id[-6:]
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"live_{asset}_{short_id}_{date_str}.csv"
        path = self._dir / filename

        f = open(path, "w", newline="")
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "yes_price", "no_price", "best_bid", "best_ask",
            "spread", "volume_24h",
        ])
        writer.writeheader()

        self._writers[market_id] = {
            "file": f,
            "writer": writer,
            "count": 0,
            "path": path,
        }
        print(f"  📝 Recording to {path}")

    def record(self, market_id: str, tick_data: dict) -> None:
        """Write a tick to the appropriate CSV file."""
        w = self._writers.get(market_id)
        if not w:
            return

        w["writer"].writerow(tick_data)
        w["count"] += 1

        # Flush periodically
        if w["count"] % FLUSH_EVERY_N_TICKS == 0:
            w["file"].flush()

        if self._verbose:
            print(f"  [{market_id[:12]}..] "
                  f"yes={tick_data['yes_price']:.4f} "
                  f"spread={tick_data['spread']:.4f} "
                  f"(#{w['count']})")

    def close_all(self) -> list[dict]:
        """Close all writers and return summary."""
        summaries = []
        for market_id, w in self._writers.items():
            w["file"].flush()
            w["file"].close()
            summaries.append({
                "market_id": market_id,
                "ticks": w["count"],
                "path": str(w["path"]),
            })
        self._writers.clear()
        return summaries


async def listen_market(
    market_id: str,
    asset: str,
    recorder: TickRecorder,
    duration_hours: float,
) -> None:
    """Connect to WebSocket and listen for orderbook updates for one market."""
    url = WS_BASE_URL
    start_time = asyncio.get_event_loop().time()

    while not _shutdown_requested:
        try:
            async with websockets.connect(
                url,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=30,
                close_timeout=10,
            ) as ws:
                # Subscribe to orderbook
                sub_msg = json.dumps({
                    "type": "subscribe",
                    "channel": "orderbook",
                    "markets": [market_id],
                })
                await ws.send(sub_msg)
                print(f"  🔗 Connected to {market_id[:20]}...")

                recorder._open_writer(market_id, asset)

                while not _shutdown_requested:
                    # Check duration
                    elapsed = (asyncio.get_event_loop().time() - start_time) / 3600
                    if duration_hours > 0 and elapsed >= duration_hours:
                        print(f"  ⏰ Duration reached ({duration_hours}h)")
                        return

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue

                    tick = parse_ws_message(market_id, raw)
                    if tick:
                        recorder.record(market_id, tick)

        except asyncio.CancelledError:
            break
        except websockets.ConnectionClosed:
            print(f"  ⚠️  WS disconnected for {market_id[:20]}..., reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"  ❌ Error on {market_id[:20]}...: {e}")
            await asyncio.sleep(10)


def parse_ws_message(market_id: str, raw_message: str) -> dict | None:
    """Parse a WebSocket message into a tick dict for CSV recording."""
    try:
        data = json.loads(raw_message)

        # Handle list messages
        if isinstance(data, list):
            for item in data:
                result = parse_ws_message(market_id, json.dumps(item))
                if result:
                    return result
            return None

        event_type = data.get("event_type", data.get("type", ""))

        # Only process orderbook/book events
        if event_type not in ("book", "price_change", "last_trade_price"):
            return None

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        if not bids or not asks:
            return None

        best_bid = max(float(b["price"]) for b in bids)
        best_ask = min(float(a["price"]) for a in asks)
        yes_price = (best_bid + best_ask) / 2
        no_price = 1.0 - yes_price
        spread = best_ask - best_bid

        # Volume from bid sizes
        volume = sum(float(b.get("size", 0)) for b in bids)

        # Timestamp
        ts_raw = data.get("timestamp")
        if ts_raw:
            timestamp = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        return {
            "timestamp": timestamp.isoformat(),
            "yes_price": round(yes_price, 4),
            "no_price": round(no_price, 4),
            "best_bid": round(best_bid, 4),
            "best_ask": round(best_ask, 4),
            "spread": round(spread, 4),
            "volume_24h": round(volume, 2),
        }

    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def setup_signal_handlers():
    """Handle Ctrl+C gracefully."""
    def handler(sig, frame):
        global _shutdown_requested
        print("\n  🛑 Shutdown requested...")
        _shutdown_requested = True

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


async def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    setup_signal_handlers()
    recorder = TickRecorder(output_dir, verbose=args.verbose)

    print("═" * 65)
    print("  POLYBOT — Live Market Data Recorder")
    print("  WebSocket → CSV recorder for backtesting datasets")
    print("═" * 65)
    print(f"  Output:      {output_dir.absolute()}")
    print(f"  Duration:    {args.duration_hours}h "
          f"({'indefinite' if args.duration_hours == 0 else ''})")
    print()

    # ── Single market mode ──────────────────────────────────────────
    if args.market_id:
        print(f"  Recording market: {args.market_id}")
        await listen_market(args.market_id, "CUSTOM", recorder,
                           args.duration_hours)
        summaries = recorder.close_all()
        for s in summaries:
            print(f"  ✅ {s['ticks']} ticks → {s['path']}")
        return

    # ── Asset mode ──────────────────────────────────────────────────
    assets = []
    if args.all:
        assets = ["BTC", "ETH"]
    elif args.asset:
        assets = [args.asset]
    else:
        print("❌ Specify --asset, --all, or --market-id")
        sys.exit(1)

    tasks = []
    for asset in assets:
        print(f"\n{'─' * 65}")
        print(f"  ASSET: {asset}")
        print(f"{'─' * 65}")

        markets = await find_markets_for_asset(asset)
        if not markets:
            print(f"  ⚠️  No markets found for {asset}. "
                  f"Make sure there are active BTC/ETH markets on Polymarket.")
            continue

        for m in markets:
            info = parse_market(m)
            if not info:
                continue
            print(f"  📊 {info['question']}")
            print(f"     ID: {info['condition_id'][:30]}...")

            task = asyncio.create_task(
                listen_market(
                    market_id=info["condition_id"],
                    asset=asset,
                    recorder=recorder,
                    duration_hours=args.duration_hours,
                ),
                name=f"ws_{info['condition_id'][:20]}",
            )
            tasks.append(task)

    if not tasks:
        print("\n  ❌ No markets found to record.")
        print("  Try specifying a market directly with --market-id")
        sys.exit(1)

    print(f"\n  📡 Recording {len(tasks)} markets...")
    print(f"  Press Ctrl+C to stop early\n")

    # Wait for all recording tasks
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass

    # Close and summarize
    summaries = recorder.close_all()
    print(f"\n{'═' * 65}")
    print(f"  RECORDING SUMMARY")
    print(f"{'═' * 65}")
    total_ticks = 0
    for s in summaries:
        print(f"  {s['ticks']:>6} ticks → {s['path']}")
        total_ticks += s["ticks"]
    print(f"\n  ✅ {total_ticks} total ticks across {len(summaries)} markets")

    # Also save manifest
    manifest_path = output_dir / "live_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump({
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "duration_hours": args.duration_hours,
            "total_ticks": total_ticks,
            "markets": summaries,
        }, f, indent=2)
    print(f"  📋 Manifest: {manifest_path}")


if __name__ == "__main__":
    asyncio.run(main())
