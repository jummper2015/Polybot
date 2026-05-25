#!/usr/bin/env python3
"""
Download real historical price data from Polymarket CLOB API.

Finds BTC and ETH prediction markets via Gamma API, then downloads
historical price timeseries via /prices-history endpoint for backtesting.

Usage:
    python scripts/download_historical_data.py
    python scripts/download_historical_data.py --asset BTC --interval 1h
    python scripts/download_historical_data.py --asset ETH --days 30
    python scripts/download_historical_data.py --all --output-dir data/historical

Output:
    data/historical/{asset}_{condition_id}_{date}.csv
    Each CSV contains: timestamp, yes_price, no_price, spread, volume

Endpoint reference:
    GET https://clob.polymarket.com/prices-history
    Params: market (condition_id), interval (1h/6h/1d/all), fidelity (minutes)
"""

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Polymarket API base URLs
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL  = "https://clob.polymarket.com"

# Default output directory (relative to project root)
DEFAULT_OUTPUT_DIR = Path("data/historical")

# How many markets to fetch per asset
MAX_MARKETS_PER_ASSET = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download historical Polymarket price data for backtesting",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/download_historical_data.py --asset BTC
  python scripts/download_historical_data.py --asset ETH --days 90
  python scripts/download_historical_data.py --all --interval 6h
  python scripts/download_historical_data.py --market-id 0xabc... --days 30
        """,
    )
    parser.add_argument("--asset", choices=["BTC", "ETH"],
                        help="Asset tag to filter markets")
    parser.add_argument("--all", action="store_true",
                        help="Download data for both BTC and ETH")
    parser.add_argument("--market-id",
                        help="Download data for a specific condition_id")
    parser.add_argument("--interval", default="all",
                        choices=["max", "all", "1m", "1w", "1d", "6h", "1h"],
                        help="Time interval for price aggregation (default: all)")
    parser.add_argument("--fidelity", type=int, default=1,
                        help="Data fidelity in minutes (default: 1)")
    parser.add_argument("--days", type=int, default=365,
                        help="Days of history to request (default: 365)")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--limit", type=int, default=MAX_MARKETS_PER_ASSET,
                        help=f"Max markets to fetch per asset (default: {MAX_MARKETS_PER_ASSET})")
    parser.add_argument("--verbose", action="store_true",
                        help="Print detailed progress")
    return parser.parse_args()


async def find_markets(asset: str, limit: int = MAX_MARKETS_PER_ASSET) -> list[dict]:
    """
    Find Polymarket markets for a given asset tag (BTC, ETH).

    Queries the Gamma API /markets endpoint and returns a list of
    normalized market dicts with condition_id, question, tokens, etc.
    """
    print(f"  🔍 Finding {asset} markets via Gamma API...")

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        # Fetch both active and closed markets for maximum historical data
        all_markets = []

        for active_flag in [True, False]:
            response = await client.get(
                f"{GAMMA_BASE_URL}/markets",
                params={
                    "active": str(active_flag).lower(),
                    "closed": str(not active_flag).lower(),
                    "tag": asset,
                    "_limit": str(limit),
                    "_order": "volume24hr",
                    "_sort": "desc",
                },
            )
            response.raise_for_status()
            markets = response.json()
            all_markets.extend(markets)

            if len(all_markets) >= limit:
                break

    # Deduplicate by condition_id
    seen = set()
    unique = []
    for m in all_markets:
        cid = m.get("condition_id", m.get("id", ""))
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(m)

    print(f"     Found {len(unique)} unique {asset} markets")
    return unique[:limit]


def parse_market(market: dict) -> dict | None:
    """
    Extract relevant fields from a Gamma API market response.

    Polymarket Gamma API returns:
      - conditionId: the on-chain condition identifier
      - clobTokenIds: [yes_token_id, no_token_id] (large numeric strings)
      - question, active, volume, etc.

    Returns dict with:
        condition_id, question, yes_token_id, no_token_id,
        volume_24h, end_date_iso, active
    """
    condition_id = market.get("conditionId", "")
    if not condition_id:
        return None

    # clobTokenIds is a list of 2: [YES, NO]
    clob_tokens = market.get("clobTokenIds", [])
    yes_token = clob_tokens[0] if len(clob_tokens) > 0 else None
    no_token = clob_tokens[1] if len(clob_tokens) > 1 else None

    # Also check nested tokens array if clobTokenIds is empty
    if not yes_token:
        tokens = market.get("tokens", [])
        for t in tokens:
            token_id = t.get("token_id", t.get("id", ""))
            outcome = t.get("outcome", "").lower()
            if outcome == "yes":
                yes_token = token_id
            elif outcome == "no":
                no_token = token_id

    return {
        "condition_id": condition_id,
        "question": market.get("question", "")[:120],
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "volume_24h": float(market.get("volume24hr", market.get("volume", 0))),
        "end_date_iso": market.get("endDateIso", ""),
        "active": market.get("active", False),
    }


async def download_prices(
    condition_id: str,
    interval: str = "all",
    fidelity: int = 1,
    days: int = 365,
    verbose: bool = False,
) -> list[dict] | None:
    """
    Download historical prices for a market from the CLOB API.

    Uses GET /prices-history with the condition_id as the market parameter.

    Returns list of price points: [{t, price}, ...] or None on failure.
    """
    url = f"{CLOB_BASE_URL}/prices-history"

    # Calculate start timestamp (Unix seconds)
    end_ts = int(datetime.now(timezone.utc).timestamp())
    start_ts = end_ts - (days * 86400)

    params: dict = {
        "market": condition_id,
        "startTs": str(start_ts),
        "endTs": str(end_ts),
        "fidelity": str(fidelity),
    }

    # "all" and "max" are intervals that don't use startTs/endTs
    if interval in ("all", "max"):
        params.pop("startTs", None)
        params.pop("endTs", None)
    params["interval"] = interval

    if verbose:
        print(f"     Requesting: interval={interval} fidelity={fidelity}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if verbose:
                item_count = len(data) if isinstance(data, list) else 0
                print(f"     Received: {item_count} data points")

            return data if isinstance(data, list) else data.get("history", [])

        except httpx.HTTPStatusError as e:
            print(f"     ⚠️  HTTP {e.response.status_code}: {e.response.text[:200]}")
            return None
        except Exception as e:
            print(f"     ⚠️  Error: {e}")
            return None


def prices_to_csv_rows(
    prices: list[dict],
    market_info: dict,
    asset: str,
) -> list[dict]:
    """
    Convert raw /prices-history response to normalized CSV rows.

    The API returns data points like: {"t": 1700000000, "price": 0.76}
    We augment with computed fields for backtesting compatibility.
    """
    if not prices:
        return []

    rows = []
    prev_price = None

    for point in prices:
        # Handle different response formats
        if isinstance(point, dict):
            ts = point.get("t", point.get("timestamp", 0))
            price = float(point.get("price", point.get("p", 0)))
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            ts, price = float(point[0]), float(point[1])
        else:
            continue

        if ts <= 0 or price <= 0:
            continue

        # Convert Unix timestamp (seconds) to ISO datetime
        try:
            timestamp = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (ValueError, OSError):
            # Some timestamps are in milliseconds
            try:
                timestamp = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
            except (ValueError, OSError):
                continue

        yes_price = round(price, 4)
        no_price = round(1.0 - price, 4)

        # Estimate spread from price movement
        if prev_price is not None:
            spread = round(abs(price - prev_price), 4)
        else:
            spread = 0.005  # Default 0.5% spread

        prev_price = price

        rows.append({
            "timestamp": timestamp.isoformat(),
            "yes_price": yes_price,
            "no_price": no_price,
            "best_bid": round(yes_price - spread / 2, 4),
            "best_ask": round(yes_price + spread / 2, 4),
            "spread": spread,
            "volume_24h": market_info.get("volume_24h", 1000.0),
            "market_id": market_info["condition_id"],
            "asset": asset,
        })

    return rows


def save_csv(
    rows: list[dict],
    output_dir: Path,
    asset: str,
    condition_id: str,
) -> Path | None:
    """Save normalized rows to CSV file."""
    if not rows:
        return None

    # Shorten condition_id for filename
    short_id = condition_id[:10] + ".." + condition_id[-6:]
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"{asset}_{short_id}_{date_str}.csv"
    path = output_dir / filename

    fieldnames = [
        "timestamp", "yes_price", "no_price", "best_bid", "best_ask",
        "spread", "volume_24h",
    ]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return path


async def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("═" * 65)
    print("  POLYBOT — Historical Data Downloader")
    print("  Polymarket CLOB API /prices-history")
    print("═" * 65)
    print(f"  Output:      {output_dir.absolute()}")
    print(f"  Interval:    {args.interval}")
    print(f"  Fidelity:    {args.fidelity} min")
    print(f"  Days:        {args.days}")
    print()

    # ── Single market mode ──────────────────────────────────────────
    if args.market_id:
        prices = await download_prices(
            condition_id=args.market_id,
            interval=args.interval,
            fidelity=args.fidelity,
            days=args.days,
            verbose=args.verbose,
        )
        if prices:
            rows = prices_to_csv_rows(prices, {"volume_24h": 0}, "CUSTOM")
            path = save_csv(rows, output_dir, "CUSTOM", args.market_id)
            if path:
                print(f"\n✅ Saved {len(rows)} rows to {path}")
            else:
                print("\n⚠️  No data points extracted")
        else:
            print("\n❌ Failed to download prices")
            sys.exit(1)
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

    total_saved = 0
    all_datasets = []

    for asset in assets:
        print(f"\n{'─' * 65}")
        print(f"  ASSET: {asset}")
        print(f"{'─' * 65}")

        markets = await find_markets(asset, limit=args.limit)
        if not markets:
            print(f"  ⚠️  No markets found for {asset}")
            continue

        for i, raw_market in enumerate(markets):
            info = parse_market(raw_market)
            if not info:
                continue

            cid = info["condition_id"]
            question = info["question"][:100]
            volume = info["volume_24h"]
            active = "ACTIVE" if info["active"] else "RESOLVED"
            yes_token = info["yes_token_id"]

            print(f"\n  [{i+1}/{len(markets)}] {active} | Vol: ${volume:,.0f}")
            print(f"       ID: {cid[:20]}...{cid[-8:]}")
            print(f"       Q:  {question}")

            if not yes_token:
                print(f"       ⚠️  No token IDs available")
                continue

            # Use the YES token ID for /prices-history (not condition_id)
            print(f"       Token: {yes_token[:20]}...{yes_token[-8:]}")

            prices = await download_prices(
                condition_id=yes_token,
                interval=args.interval,
                fidelity=args.fidelity,
                days=args.days,
                verbose=args.verbose,
            )

            if not prices:
                print(f"       ⚠️  No price data (market may be too new or API limited)")
                continue

            rows = prices_to_csv_rows(prices, info, asset)
            if not rows:
                print(f"       ⚠️  Empty dataset after parsing")
                continue

            path = save_csv(rows, output_dir, asset, cid)
            if path:
                date_range = f"{rows[0]['timestamp'][:10]} → {rows[-1]['timestamp'][:10]}"
                print(f"       ✅ Saved {len(rows)} rows | {date_range}")
                print(f"          {path}")
                total_saved += 1
                all_datasets.append({
                    "path": str(path),
                    "rows": len(rows),
                    "asset": asset,
                    "condition_id": cid,
                    "date_range": date_range,
                    "active": info["active"],
                    "volume_24h": volume,
                })

            # Rate limiting: be gentle with the API
            await asyncio.sleep(0.5)

    # ── Summary ─────────────────────────────────────────────────────
    print(f"\n{'═' * 65}")
    print(f"  DOWNLOAD SUMMARY")
    print(f"{'═' * 65}")

    if all_datasets:
        for ds in all_datasets:
            print(f"  {ds['asset']:>4} | {ds['rows']:>6} rows | "
                  f"{ds['date_range']} | {'ACTIVE' if ds['active'] else 'resolved'}")
        print(f"\n  ✅ {total_saved} datasets saved to {output_dir.absolute()}")

        # Save manifest
        manifest_path = output_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump({
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "interval": args.interval,
                "fidelity": args.fidelity,
                "days": args.days,
                "total_datasets": total_saved,
                "datasets": all_datasets,
            }, f, indent=2, default=str)
        print(f"  📋 Manifest: {manifest_path}")
    else:
        print("  ❌ No datasets were saved.")
        print("  Possible reasons:")
        print("     - No BTC/ETH markets found on Polymarket right now")
        print("     - /prices-history returned empty for all markets")
        print("     - Network issues connecting to Polymarket API")
        print("  Try: python scripts/download_historical_data.py --market-id <CONDITION_ID>")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
