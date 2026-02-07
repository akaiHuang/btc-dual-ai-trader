
import asyncio
import os
import json
from datetime import datetime
from dydx_v4_client.indexer.rest.indexer_client import IndexerClient
from dydx_v4_client.config import IndexerConfig
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def main():
    try:
        # Initialize Indexer Client manually
        indexer_url = "https://indexer.dydx.trade"
        indexer_config = IndexerConfig(
            rest_endpoint=indexer_url,
            websocket_endpoint="wss://indexer.dydx.trade/v4/ws"
        )
        indexer_client = IndexerClient(indexer_config)
        
        # Get address from env or config (hardcoded for now as seen in logs)
        address = "dydx1ul7g6jen6ce84pjacmcwtrg2xyyfe3hgaua7fg" 
        
        print(f"🔍 Verifying trades for address: {address}")
        
        # Fetch fills (executions)
        # Note: API might paginate, we just want recent ones
        fills_response = await indexer_client.account.get_subaccount_fills(
            address=address,
            subaccount_number=0,
            limit=50
        )
        
        print(f"\n✅ Recent Executions (Fills) from dYdX API:")
        print(f"{'Time':<28} | {'Side':<6} | {'Size':<8} | {'Price':<10} | {'Fee'}")
        print("-" * 75)
        
        fills = fills_response.get('fills', [])
        
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

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
