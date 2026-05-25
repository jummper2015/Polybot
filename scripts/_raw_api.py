"""Print raw Gamma API response to understand the data structure."""
import asyncio, httpx, json

async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        # Get raw market data
        r = await c.get("https://gamma-api.polymarket.com/markets",
                        params={"active": "true", "_limit": "3"})
        data = r.json()
        for i, m in enumerate(data):
            print(f"=== Market {i} ALL FIELDS ===")
            for k, v in sorted(m.items()):
                val = str(v)[:200]
                print(f"  {k}: {val}")
            print()

        # Try searching for crypto specifically
        print("\n=== CRYPTO SEARCH ===")
        for term in ["Bitcoin", "BTC above", "Ethereum"]:
            r2 = await c.get("https://gamma-api.polymarket.com/markets",
                            params={"closed": "false", "_limit": "10"})
            markets = r2.json()
            crypto = [m for m in markets if term.lower() in m.get("question", "").lower()]
            print(f"  '{term}': {len(crypto)} matches")
            for m in crypto[:3]:
                cid = m.get("conditionId", m.get("condition_id", "?"))
                q = m.get("question", "")[:100]
                tokens = [(t.get("token_id", "?"), t.get("outcome", "?")) for t in m.get("tokens", [])]
                print(f"    Q: {q}")
                print(f"    conditionId: {cid}")
                print(f"    tokens: {tokens}")

        # Try events endpoint
        print("\n=== EVENTS (TAG=crypto) ===")
        r3 = await c.get("https://gamma-api.polymarket.com/events",
                        params={"tag": "crypto", "active": "true", "_limit": "3"})
        print(f"  Status: {r3.status_code}")
        events = r3.json()
        print(f"  Events: {len(events)}")
        for ev in events[:2]:
            title = ev.get("title", "?")[:80]
            markets = ev.get("markets", [])
            print(f"  Event: {title}")
            print(f"    Markets: {len(markets)}")
            for m in markets[:2]:
                q = m.get("question", "")[:80]
                tokens = [(t.get("token_id", "?"), t.get("outcome", "?")) for t in m.get("tokens", [])]
                print(f"    Q: {q}")
                print(f"    tokens: {tokens}")

asyncio.run(main())
