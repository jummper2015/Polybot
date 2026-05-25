"""Debug script: find actual BTC/ETH Polymarket markets and test /prices-history endpoint."""
import asyncio
import httpx
import json

async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        # Try different approaches to find BTC markets
        approaches = [
            ("tags=crypto", {"tags": "crypto", "active": "true", "_limit": "5"}),
            ("no filter - all markets", {"active": "true", "_limit": "20"}),
        ]
        
        for label, params in approaches:
            r = await c.get("https://gamma-api.polymarket.com/markets", params=params)
            data = r.json()
            print(f"\n=== {label} ===")
            print(f"Total: {len(data)}")
            
            for m in data[:3]:
                q = m.get("question", "")[:80]
                cid = m.get("conditionId", m.get("condition_id", "?"))
                tokens = m.get("tokens", [])
                token_ids = [t.get("token_id", "?") for t in tokens[:2]]
                print(f"  Q: {q}")
                print(f"    conditionId: {cid}")
                print(f"    tokens: {token_ids}")
        
        # Now try CLOB /prices-history with a token_id
        r = await c.get("https://gamma-api.polymarket.com/markets", 
                        params={"active": "true", "_limit": "30"})
        all_markets = r.json()
        
        crypto_markets = []
        for m in all_markets:
            q = m.get("question", "").lower()
            if any(term in q for term in ["btc", "bitcoin", "eth", "ethereum"]):
                crypto_markets.append(m)
        
        print(f"\n=== Crypto-related markets found: {len(crypto_markets)} ===")
        for m in crypto_markets[:5]:
            q = m.get("question", "")[:100]
            cid = m.get("conditionId", m.get("condition_id", "?"))
            tokens = m.get("tokens", [])
            print(f"  Q: {q}")
            print(f"    conditionId: {cid}")
            yes_token = None
            for t in tokens:
                outcome = t.get("outcome", "").lower()
                token_id = t.get("token_id", "")
                print(f"    Token: {token_id} ({outcome})")
                if outcome == "yes":
                    yes_token = token_id
            
            if yes_token:
                print(f"    Testing /prices-history with yes_token={yes_token}")
                try:
                    r2 = await c.get(
                        "https://clob.polymarket.com/prices-history",
                        params={"market": yes_token, "interval": "1d"}
                    )
                    print(f"    Status: {r2.status_code}")
                    resp = r2.json()
                    if isinstance(resp, list):
                        print(f"    Data points: {len(resp)}")
                        if resp:
                            print(f"    First: {resp[0]}")
                            print(f"    Last: {resp[-1]}")
                    else:
                        print(f"    Response keys: {list(resp.keys())[:5]}")
                        history = resp.get("history", [])
                        print(f"    History points: {len(history)}")
                        if history:
                            print(f"    First: {history[0]}")
                except Exception as e:
                    print(f"    Error: {e}")

asyncio.run(main())
