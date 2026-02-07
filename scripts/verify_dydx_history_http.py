
import asyncio
import aiohttp
import json
from datetime import datetime

async def main():
    address = "dydx1ul7g6jen6ce84pjacmcwtrg2xyyfe3hgaua7fg"
    url = f"https://indexer.dydx.trade/v4/fills"
    params = {
        "address": address,
        "subaccountNumber": "0",
        "limit": "50"
    }

    print(f"🔍 Querying dYdX Indexer API: {url}")
    print(f"   Address: {address}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                print(f"❌ Error: HTTP {resp.status}")
                text = await resp.text()
                print(text)
                return

            data = await resp.json()
            fills = data.get('fills', [])
            
            print(f"\n✅ Recent Executions (Fills) from dYdX API:")
            print(f"{'Time':<28} | {'Side':<6} | {'Size':<8} | {'Price':<10} | {'Fee'}")
            print("-" * 75)
            
            for fill in fills:
                # Parse time
                created_at = fill['createdAt'] # e.g. 2025-12-10T08:48:02.497Z
                side = fill['side']
                size = fill['size']
                price = fill['price']
                fee = fill['fee']
                market = fill['market']
                
                if market == "BTC-USD":
                    print(f"{created_at:<28} | {side:<6} | {size:<8} | {price:<10} | {fee}")

if __name__ == "__main__":
    asyncio.run(main())
