#!/usr/bin/env python3
"""
Live market data recorder for Polymarket.

Records real-time tick data to Parquet (default) or CSV format.

Architecture:
    WebSocket → Tick Parser → MultiAssetRecorder (buffered) → Parquet files
                                                                  ↓
                                                            Partitioned by
                                                            asset/date

Usage:
    python scripts/record_live_data.py --asset BTC --duration-hours 24
    python scripts/record_live_data.py --asset ETH --format csv    # backward compat
    python scripts/record_live_data.py --all
    python scripts/record_live_data.py --market-id 0xabc...

Output (Parquet mode, default):
    data/parquet/
        asset=BTC/year=2026/month=05/day=26/ticks_HHMMSS_ffffff.parquet
        asset=ETH/year=2026/month=05/day=26/ticks_HHMMSS_ffffff.parquet
        manifest.json

Output (CSV mode, legacy):
    data/historical/live_{asset}_{market_id}_{date}.csv
    live_manifest.json
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

# Ensure project root is on sys.path for src.* imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import structlog
import websockets

from src.infrastructure.data.schema import datetime_to_ns
from src.infrastructure.data.storage import MultiAssetRecorder

logger = structlog.get_logger(__name__)

# Polymarket endpoints
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
WS_BASE_URL    = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Correct subscription format for Polymarket CLOB WebSocket API
# Docs: https://docs.polymarket.com/market-data/websocket/market-channel
# Uses "type": "market" with "assets_ids" (token IDs, NOT condition IDs)
WS_SUBSCRIBE_TYPE = "market"

# Defaults
DEFAULT_OUTPUT_DIR   = Path("data/historical")
DEFAULT_PARQUET_DIR  = Path("data/parquet")
FLUSH_EVERY_N_TICKS  = 100
WS_PING_INTERVAL     = 20

# Global flag for graceful shutdown
_shutdown_requested = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record live Polymarket market data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/record_live_data.py --asset BTC --duration-hours 24
  python scripts/record_live_data.py --asset ETH --format csv
  python scripts/record_live_data.py --market-id 0xabc...
  python scripts/record_live_data.py --all --format parquet --batch-size 16384
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
    parser.add_argument("--format", choices=["parquet", "csv"], default="parquet",
                        help="Output format (default: parquet)")
    parser.add_argument("--output-dir",
                        help="Output directory (default: parquet=data/parquet, csv=data/historical)")
    parser.add_argument("--batch-size", type=int, default=1000,
                        help="Parquet buffer batch size (default: 1000)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every tick received")
    return parser.parse_args()


# ── Market Discovery ─────────────────────────────────────────────────────────

async def find_markets_for_asset(asset: str) -> list[dict]:
    """Find active markets for a given asset via Gamma API."""
    async with httpx.AsyncClient(timeout=30) as client:
        keywords = ["bitcoin", "btc"] if asset == "BTC" else ["ethereum", "eth"]
        all_markets = []

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
            all_markets.extend(response.json())

        matching = []
        seen = set()
        for m in all_markets:
            cid = m.get("conditionId", "")
            q = m.get("question", "").lower()
            if cid and cid not in seen and any(k in q for k in keywords):
                seen.add(cid)
                matching.append(m)

        return matching[:5]


def parse_market(m: dict) -> dict | None:
    """Extract market info from Gamma API response."""
    cid = m.get("conditionId", "")
    if not cid:
        return None

    # Primary: clobTokenIds (list of strings like ["123", "456"])
    clob_tokens = m.get("clobTokenIds", [])
    if isinstance(clob_tokens, str):
        try:
            clob_tokens = json.loads(clob_tokens)
        except (json.JSONDecodeError, TypeError):
            clob_tokens = []

    # Fallback: tokens field (list of objects with token_id key)
    tokens_objs = m.get("tokens", [])
    if isinstance(tokens_objs, str):
        try:
            tokens_objs = json.loads(tokens_objs)
        except (json.JSONDecodeError, TypeError):
            tokens_objs = []

    yes_tid = None
    no_tid = None

    # Prefer clobTokenIds first (direct string IDs)
    if clob_tokens:
        yes_tid = str(clob_tokens[0]) if clob_tokens else None
        no_tid = str(clob_tokens[1]) if len(clob_tokens) > 1 else None
    # Fallback: extract from tokens objects
    elif tokens_objs:
        for t in tokens_objs:
            outcome = str(t.get("outcome", "")).lower()
            tid = str(t.get("token_id", "")) if t.get("token_id") else None
            if tid:
                if outcome == "yes" and not yes_tid:
                    yes_tid = tid
                elif outcome == "no" and not no_tid:
                    no_tid = tid
        # If outcomes aren't labeled, use position 0=Yes, 1=No
        if not yes_tid and tokens_objs:
            yes_tid = str(tokens_objs[0].get("token_id", "")) or None
        if not no_tid and len(tokens_objs) > 1:
            no_tid = str(tokens_objs[1].get("token_id", "")) or None

    return {
        "condition_id": cid,
        "question": m.get("question", "")[:120],
        "yes_token_id": yes_tid,
        "no_token_id": no_tid,
        "active": m.get("active", False),
    }


# ── Stateful Tick Parsing ─────────────────────────────────────────────────────
#
# The Polymarket CLOB WebSocket sends two types of messages:
#   1. Initial snapshot: list containing full order book (bids + asks)
#   2. Price change events: incremental updates (price_changes array, NO bids/asks)
#
# We maintain a module-level cache of the last known order book state per market
# so that price_change events can be converted into complete tick records.

_book_cache: dict[str, dict] = {}


def init_book_state(market_id: str, yes_token_id: str) -> None:
    """Initialize/reinitialize order book state for a market before WS connect."""
    _book_cache[market_id] = {
        "best_bid": 0.0,
        "best_ask": 0.0,
        "bids_vols": [0.0, 0.0, 0.0],
        "asks_vols": [0.0, 0.0, 0.0],
        "volume": 0.0,
        "yes_token_id": yes_token_id,
        "initialized": False,
    }


def _build_tick(
    market_id: str,
    asset: str,
    best_bid: float,
    best_ask: float,
    state: dict,
    timestamp_raw=None,
) -> dict:
    """Build a normalized tick dict from book state."""
    yes_price = (best_bid + best_ask) / 2
    no_price = 1.0 - yes_price
    spread = best_ask - best_bid
    mid_price = yes_price
    volume = state.get("volume", 0.0)

    liquidity_score = None
    if volume > 0 and spread > 0:
        liquidity_score = round(volume / (1.0 + spread * 100), 2)

    # Timestamp: WS may send seconds (10-digit), ms (13-digit), or finer.
    # Normalise to seconds for datetime.fromtimestamp().
    if timestamp_raw:
        ts_int = int(timestamp_raw)
        while ts_int > 10_000_000_000:       # seconds fit in 10 digits
            ts_int = ts_int // 1000
        ts_dt = datetime.fromtimestamp(ts_int, tz=timezone.utc)
    else:
        ts_dt = datetime.now(timezone.utc)
    ts_ns = datetime_to_ns(ts_dt)

    bv = state.get("bids_vols", [0.0, 0.0, 0.0])
    av = state.get("asks_vols", [0.0, 0.0, 0.0])

    return {
        "timestamp_ns":    ts_ns,
        "market_id":       market_id,
        "asset":           asset,
        "yes_price":       round(yes_price, 4),
        "no_price":        round(no_price, 4),
        "mid_price":       round(mid_price, 4),
        "best_bid":        round(best_bid, 4),
        "best_ask":        round(best_ask, 4),
        "spread":          round(spread, 4),
        "volume_24h":      round(volume, 2),
        "liquidity_score": liquidity_score,
        "bids_vol_1":      round(bv[0], 2),
        "asks_vol_1":      round(av[0], 2),
        "bids_vol_2":      round(bv[1], 2),
        "asks_vol_2":      round(av[1], 2),
        "bids_vol_3":      round(bv[2], 2),
        "asks_vol_3":      round(av[2], 2),
    }


def _apply_book_snapshot(market_id: str, data: dict, asset: str) -> dict | None:
    """Parse a full order book snapshot and update the cache."""
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    if not bids or not asks:
        return None

    best_bid = max(float(b["price"]) for b in bids)
    best_ask = min(float(a["price"]) for a in asks)
    volume = sum(float(b.get("size", 0)) for b in bids)

    # Order book depth (top 3 levels)
    bids_sorted = sorted(bids, key=lambda b: float(b["price"]), reverse=True)
    asks_sorted = sorted(asks, key=lambda a: float(a["price"]))

    def _vol(items, idx):
        return float(items[idx].get("size", 0)) if idx < len(items) else 0.0

    state = _book_cache.get(market_id, {})
    state["best_bid"] = best_bid
    state["best_ask"] = best_ask
    state["volume"] = volume
    state["bids_vols"] = [_vol(bids_sorted, 0), _vol(bids_sorted, 1), _vol(bids_sorted, 2)]
    state["asks_vols"] = [_vol(asks_sorted, 0), _vol(asks_sorted, 1), _vol(asks_sorted, 2)]
    state["initialized"] = True
    _book_cache[market_id] = state

    return _build_tick(market_id, asset, best_bid, best_ask, state, data.get("timestamp"))


def _apply_price_changes(market_id: str, data: dict, asset: str) -> dict | None:
    """Apply price_change events using best_bid/best_ask from the event itself.

    Each price_change object in Polymarket's WS already carries the current
    best_bid and best_ask, so we don't need to track incremental state.
    We update the cache and produce a tick from the first YES-token change.
    """
    state = _book_cache.get(market_id)
    if not state or not state.get("initialized"):
        return None

    yes_token_id = str(state.get("yes_token_id", ""))
    price_changes = data.get("price_changes", [])

    if not price_changes:
        return None

    # Find the first price_change for the YES token
    best_pc = None
    for pc in price_changes:
        pc_asset = str(pc.get("asset_id", ""))
        if pc_asset == yes_token_id:
            best_pc = pc
            break
    # No YES-token change found — skip (NO-token prices would need inversion)
    if not best_pc:
        return None

    # Use best_bid/best_ask from the price_change directly
    raw_bid = best_pc.get("best_bid")
    raw_ask = best_pc.get("best_ask")
    if not raw_bid or not raw_ask:
        return None

    best_bid = float(raw_bid)
    best_ask = float(raw_ask)

    if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
        return None

    # Update cache
    state["best_bid"] = best_bid
    state["best_ask"] = best_ask
    _book_cache[market_id] = state

    return _build_tick(market_id, asset, best_bid, best_ask, state, data.get("timestamp"))


def parse_ws_message(
    market_id: str,
    raw_message: str,
    asset: str = "",
) -> dict | None:
    """
    Parse a WebSocket message into a normalized tick dict.

    Uses a module-level order book cache to handle incremental price_change
    events that lack full bids/asks. The initial snapshot seeds the cache.

    Returns None if the message cannot produce a valid tick.
    """
    try:
        data = json.loads(raw_message)

        # ── Initial snapshot (list) ─────────────────────────────────
        if isinstance(data, list):
            for item in data:
                event_type = item.get("event_type", item.get("type", ""))
                if event_type in ("book", ""):
                    result = _apply_book_snapshot(market_id, item, asset)
                    if result:
                        return result
            # Empty list or no book items — expected for illiquid markets
            return None

        event_type = data.get("event_type", data.get("type", ""))

        # ── Full book snapshot ─────────────────────────────────────
        if event_type == "book":
            return _apply_book_snapshot(market_id, data, asset)

        # ── Price change event (carries best_bid/best_ask natively) ─
        if event_type == "price_change":
            return _apply_price_changes(market_id, data, asset)

        return None

    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        print(f"  ⚠️  [{market_id[:12]}] Parse error: {type(e).__name__}: {e}")
        return None


# ── CSV TickRecorder (legacy) ────────────────────────────────────────────────

class CsvTickRecorder:
    """Legacy CSV-based tick recorder (backward compatibility)."""

    def __init__(self, output_dir: Path, verbose: bool = False):
        self._dir = output_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._verbose = verbose
        self._writers: dict[str, dict] = {}

    def _open_writer(self, market_id: str, asset: str) -> None:
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
            "file": f, "writer": writer, "count": 0, "path": path,
        }
        print(f"  📝 Recording to {path}")

    def record(self, market_id: str, tick_data: dict) -> None:
        w = self._writers.get(market_id)
        if not w:
            return

        from datetime import datetime, timezone
        ts_ns = tick_data.get("timestamp_ns", 0)
        ts_dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)

        row = {
            "timestamp": ts_dt.isoformat(),
            "yes_price": tick_data["yes_price"],
            "no_price":  tick_data["no_price"],
            "best_bid":  tick_data["best_bid"],
            "best_ask":  tick_data["best_ask"],
            "spread":    tick_data["spread"],
            "volume_24h": tick_data["volume_24h"],
        }
        w["writer"].writerow(row)
        w["count"] += 1
        if w["count"] % FLUSH_EVERY_N_TICKS == 0:
            w["file"].flush()
        if self._verbose:
            print(f"  [{market_id[:12]}..] yes={tick_data['yes_price']:.4f} spread={tick_data['spread']:.4f} (#{w['count']})")

    def close_all(self) -> list[dict]:
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


# ── WebSocket Listener ───────────────────────────────────────────────────────

async def listen_market_parquet(
    token_id: str,
    market_id: str,
    asset: str,
    recorder: MultiAssetRecorder,
    duration_hours: float,
    verbose: bool = False,
) -> int:
    """
    Connect to WebSocket and record ticks via MultiAssetRecorder (Parquet).

    token_id:  CLOB asset_id (numeric token ID) used for WS subscription
    market_id: condition_id (0x...) used for tick recording labels

    Returns number of ticks recorded.
    """
    url = WS_BASE_URL
    start_time = asyncio.get_event_loop().time()
    tick_count = 0
    short_id = market_id[:16]
    last_heartbeat = start_time

    # Initialize the stateful book cache before connecting
    init_book_state(market_id, token_id)

    while not _shutdown_requested:
        try:
            async with websockets.connect(
                url,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=30,
                close_timeout=10,
            ) as ws:
                # Polymarket CLOB WS API v2: "type": "market" with "assets_ids"
                sub_msg = json.dumps({
                    "assets_ids": [token_id],
                    "type": "market",
                })
                await ws.send(sub_msg)
                print(f"  🔗 Connected to {short_id}... (asset_id={token_id[:20]}...)")

                while not _shutdown_requested:
                    now = asyncio.get_event_loop().time()
                    elapsed = (now - start_time) / 3600
                    if duration_hours > 0 and elapsed >= duration_hours:
                        print(f"  ⏰ Duration reached ({duration_hours}h)")
                        return tick_count

                    # Heartbeat every 5 minutes
                    if now - last_heartbeat >= 300:
                        rate = tick_count / max(1, (now - start_time) / 3600)
                        print(f"  💓 [{short_id}..{asset}] {tick_count} ticks | "
                              f"{elapsed:.1f}h elapsed | {rate:.0f} ticks/h")
                        last_heartbeat = now

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue

                    tick = parse_ws_message(market_id, raw, asset)
                    if tick:
                        recorder.record_tick(asset, tick)
                        tick_count += 1

                        if verbose and tick_count % 100 == 0:
                            print(f"  [{short_id}..] yes={tick['yes_price']:.4f} spread={tick['spread']:.4f} (#{tick_count})")

        except asyncio.CancelledError:
            break
        except websockets.ConnectionClosed:
            print(f"  ⚠️  WS disconnected for {short_id}..., reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"  ❌ Error on {short_id}...: {e}")
            await asyncio.sleep(10)

    return tick_count


async def listen_market_csv(
    token_id: str,
    market_id: str,
    asset: str,
    recorder: CsvTickRecorder,
    duration_hours: float,
    verbose: bool = False,
) -> int:
    """
    Legacy CSV listener (backward compatible).
    Returns number of ticks recorded.

    token_id:  CLOB asset_id (numeric) for WS subscription
    market_id: condition_id (0x...) for tick labelling
    """
    url = WS_BASE_URL
    start_time = asyncio.get_event_loop().time()
    tick_count = 0

    # Initialize the stateful book cache before connecting
    init_book_state(market_id, token_id)

    while not _shutdown_requested:
        try:
            async with websockets.connect(
                url,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=30,
                close_timeout=10,
            ) as ws:
                # Polymarket CLOB WS API v2: "type": "market" with "assets_ids"
                sub_msg = json.dumps({
                    "assets_ids": [token_id],
                    "type": "market",
                })
                await ws.send(sub_msg)
                print(f"  🔗 Connected to {market_id[:20]}... (token={token_id[:16]}...)")
                recorder._open_writer(market_id, asset)

                while not _shutdown_requested:
                    elapsed = (asyncio.get_event_loop().time() - start_time) / 3600
                    if duration_hours > 0 and elapsed >= duration_hours:
                        print(f"  ⏰ Duration reached ({duration_hours}h)")
                        return tick_count

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue

                    tick = parse_ws_message(market_id, raw, asset)
                    if tick:
                        recorder.record(market_id, tick)
                        tick_count += 1

        except asyncio.CancelledError:
            break
        except websockets.ConnectionClosed:
            print(f"  ⚠️  WS disconnected for {market_id[:20]}..., reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"  ❌ Error on {market_id[:20]}...: {e}")
            await asyncio.sleep(10)

    return tick_count


# ── Signal Handling ──────────────────────────────────────────────────────────

def setup_signal_handlers():
    def handler(sig, frame):
        global _shutdown_requested
        print("\n  🛑 Shutdown requested...")
        _shutdown_requested = True
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    args = parse_args()
    setup_signal_handlers()

    use_parquet = args.format == "parquet"

    # ── Output directory ───────────────────────────────────────────────
    if args.output_dir:
        output_dir = Path(args.output_dir)
    elif use_parquet:
        output_dir = DEFAULT_PARQUET_DIR
    else:
        output_dir = DEFAULT_OUTPUT_DIR

    output_dir.mkdir(parents=True, exist_ok=True)

    print("═" * 65)
    print("  POLYBOT — Live Market Data Recorder")
    print(f"  Format:      {'Parquet (compressed)' if use_parquet else 'CSV (legacy)'}")
    print(f"  Output:      {output_dir.absolute()}")
    print(f"  Duration:    {args.duration_hours}h {'(indefinite)' if args.duration_hours == 0 else ''}")
    print(f"  Batch size:  {args.batch_size} ticks")
    print("═" * 65)

    # ── Initialize recorder ────────────────────────────────────────────
    if use_parquet:
        recorder = MultiAssetRecorder(
            base_dir=output_dir,
            batch_size=args.batch_size,
            verbose=args.verbose,
        )
    else:
        csv_recorder = CsvTickRecorder(output_dir, verbose=args.verbose)

    # ── Single market mode ──────────────────────────────────────────────
    if args.market_id:
        print(f"\n  Recording market: {args.market_id}")
        print("  ⚠️  Single-market mode requires a token_id (clobTokenId) for WS subscription.")
        print("     Use --asset or --all mode to auto-discover markets with token IDs.")
        sys.exit(1)

    # ── Asset mode ─────────────────────────────────────────────────────
    assets = []
    if args.all:
        assets = ["BTC", "ETH"]
    elif args.asset:
        assets = [args.asset]
    else:
        print("❌ Specify --asset, --all, or --market-id")
        sys.exit(1)

    all_market_infos = []
    all_tasks = []

    for asset in assets:
        print(f"\n{'─' * 65}")
        print(f"  ASSET: {asset}")
        print(f"{'─' * 65}")

        markets = await find_markets_for_asset(asset)
        if not markets:
            print(f"  ⚠️  No markets found for {asset}")
            continue

        for m in markets:
            info = parse_market(m)
            if not info:
                continue
            print(f"  📊 {info['question']}")
            print(f"     ID: {info['condition_id'][:30]}...")

            # Get token_id for WS subscription (clobTokenIds from Gamma API)
            ws_token_id = info.get("yes_token_id") or info.get("no_token_id")
            if not ws_token_id:
                print("     ⚠️  No clobTokenIds found — skipping")
                continue

            if use_parquet:
                recorder.start_session(
                    asset=asset,
                    market_id=info["condition_id"],
                    question=info["question"],
                )
                task = asyncio.create_task(
                    listen_market_parquet(
                        token_id=ws_token_id,
                        market_id=info["condition_id"],
                        asset=asset,
                        recorder=recorder,
                        duration_hours=args.duration_hours,
                        verbose=args.verbose,
                    ),
                    name=f"ws_{info['condition_id'][:20]}",
                )
            else:
                task = asyncio.create_task(
                    listen_market_csv(
                        token_id=ws_token_id,
                        market_id=info["condition_id"],
                        asset=asset,
                        recorder=csv_recorder,
                        duration_hours=args.duration_hours,
                        verbose=args.verbose,
                    ),
                    name=f"ws_{info['condition_id'][:20]}",
                )

            all_tasks.append((task, info["condition_id"], info["question"], asset))

    if not all_tasks:
        print("\n  ❌ No markets found to record.")
        sys.exit(1)

    print(f"\n  📡 Recording {len(all_tasks)} markets...")
    print("  Press Ctrl+C to stop early\n")

    # ── Wait for all tasks ────────────────────────────────────────────
    try:
        results = await asyncio.gather(*[t for t, _, _, _ in all_tasks])
    except asyncio.CancelledError:
        results = []

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{'═' * 65}")
    print("  RECORDING SUMMARY")
    print(f"{'═' * 65}")

    if use_parquet:
        manifest = recorder.finalize_all()
        total_ticks = manifest.get("total_ticks", 0)

        # Build per-market session summaries from listener results
        sessions = []
        for idx, (_, cid, question, asset) in enumerate(all_tasks):
            ticks = results[idx] if idx < len(results) else 0
            print(f"  {asset:>4} | {ticks:>8} ticks | {question[:60]}")
            sessions.append({
                "asset": asset,
                "market_id": cid,
                "question": question,
                "ticks": ticks,
            })

        print(f"\n  ✅ {total_ticks} total ticks across {len(sessions)} markets")
        print(f"  📋 Parquet: {output_dir.absolute()}/")
        print(f"  📋 Manifest: {output_dir.absolute()}/manifest.json")

        # Write manifest with correct session data
        manifest_path = output_dir / "manifest.json"
        manifest["sessions"] = sessions
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, default=str)

        # Quick size estimate
        total_size = sum(
            f.stat().st_size for f in output_dir.rglob("*.parquet")
        ) if output_dir.exists() else 0
        if total_size > 0:
            print(f"  💾 Total size: {total_size / 1024 / 1024:.1f} MB (zstd compressed)")

    else:
        summaries = csv_recorder.close_all()
        total_ticks = 0
        for s in summaries:
            print(f"  {s['ticks']:>6} ticks → {s['path']}")
            total_ticks += s["ticks"]
        print(f"\n  ✅ {total_ticks} total ticks across {len(summaries)} markets")

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
