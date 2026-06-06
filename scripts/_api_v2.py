"""Find price prediction markets and test /prices-history with real token IDs."""
import asyncio
import json

import httpx


async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        # Search for price-related markets
        print("=== SEARCHING FOR PRICE PREDICTION MARKETS ===")
        r = await c.get("https://gamma-api.polymarket.com/markets",
                       params={"active": "true", "_limit": "50"})
        markets = r.json()

        # Filter by keywords suggesting price predictions
        price_keywords = ["above", "below", "$", "price", "bitcoin", "btc", "ethereum", "eth"]
        price_markets = []
        for m in markets:
            q = m.get("question", "").lower()
            if any(kw in q for kw in price_keywords):
                price_markets.append(m)

        print(f"Price-related markets: {len(price_markets)}")
        for m in price_markets[:10]:
            q = m.get("question", "")[:120]
            cid = m.get("conditionId", "")
            tokens = m.get("clobTokenIds", [])
            print(f"  Q: {q}")
            print(f"    conditionId: {cid}")
            print(f"    clobTokenIds: {tokens}")

            # Test prices-history with first clob token
            if tokens:
                token_id = tokens[0]
                try:
                    r2 = await c.get(
                        "https://clob.polymarket.com/prices-history",
                        params={"market": token_id, "interval": "max", "fidelity": 60}
                    )
                    print(f"    /prices-history status: {r2.status_code}")
                    data = r2.json()
                    if isinstance(data, list):
                        print(f"    Data points: {len(data)}")
                        if data:
                            print(f"    First: {json.dumps(data[0])}")
                            print(f"    Last: {json.dumps(data[-1])}")
                    elif isinstance(data, dict):
                        history = data.get("history", [])
                        print(f"    History points: {len(history)}")
                        if history:
                            print(f"    First: {json.dumps(history[0])}")
                    else:
                        print(f"    Response: {str(data)[:200]}")
                except Exception as e:
                    print(f"    Error: {e}")
            print()

        # Search resolved/closed markets too (they might have full history)
        print("\n=== RESOLVED PRICE MARKETS ===")
        r3 = await c.get("https://gamma-api.polymarket.com/markets",
                        params={"closed": "true", "_limit": "50"})
        resolved = r3.json()
        price_resolved = [m for m in resolved if any(kw in m.get("question","").lower() for kw in price_keywords)]
        print(f"Resolved price markets: {len(price_resolved)}")

        # Try to find the specific BTC/ETH daily prediction markets
        btc_markets = [m for m in (markets + resolved) if "bitcoin" in m.get("question","").lower() and ("above" in m.get("question","").lower() or "price" in m.get("question","").lower())]
        print(f"\nBTC price markets: {len(btc_markets)}")
        for m in btc_markets[:5]:
            q = m.get("question", "")[:120]
            tokens = m.get("clobTokenIds", [])
            active_flag = m.get("active", False)
            print(f"  [{active_flag}] Q: {q}")
            print(f"    clobTokenIds: {tokens}")

            if tokens:
                token_id = tokens[0]
                try:
                    r4 = await c.get(
                        "https://clob.polymarket.com/prices-history",
                        params={"market": token_id, "interval": "all", "fidelity": 30}
                    )
                    data = r4.json()
                    if isinstance(data, list):
                        print(f"    History: {len(data)} pts, status={r4.status_code}")
                        if data:
                            print(f"    Sample: {data[0]}")
                    elif isinstance(data, dict):
                        print(f"    History: {data}")
                    else:
                        print(f"    Type={type(data)} val={str(data)[:100]}")
                except Exception as e:
                    print(f"    Error: {e}")

asyncio.run(main())
