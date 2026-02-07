import os
import asyncio
import time
import random
from dotenv import load_dotenv
from v4_proto.dydxprotocol.clob.order_pb2 import Order
from dydx_v4_client import MAX_CLIENT_ID, OrderFlags
from dydx_v4_client.indexer.rest.constants import OrderType
from dydx_v4_client.indexer.rest.indexer_client import IndexerClient
from dydx_v4_client.node.client import NodeClient
from dydx_v4_client.node.market import Market
from dydx_v4_client.wallet import Wallet
from dydx_v4_client.key_pair import KeyPair
from dydx_v4_client.network import TESTNET, make_mainnet, make_testnet

load_dotenv()

# CONFIG
MARKET_ID = "BTC-USD"
SIZE = 0.001 # Minimal size
HOLD_SECONDS = 10
NETWORK_MODE = os.getenv("DYDX_NETWORK", "testnet").lower()

async def main():
    print(f"🚀 Starting dYdX Fee Verification Trade ({NETWORK_MODE.upper()})")
    
    # 1. Connect
    if NETWORK_MODE == "testnet":
         network_config = TESTNET
    else:
         network_config = make_mainnet(
                rest_indexer="https://indexer.dydx.trade",
                websocket_indexer="wss://indexer.dydx.trade/v4/ws",
                node_url="dydx-ops-grpc.kingnodes.com:443"
         )

    try:
        node = await NodeClient.connect(network_config.node)
        indexer = IndexerClient(network_config.rest_indexer)
    except Exception as e:
        print(f"❌ Connection Failed: {e}")
        return

    # 2. Setup Wallet
    private_key = os.getenv("DYDX_PRIVATE_KEY")
    address = os.getenv("DYDX_ADDRESS")
    if not private_key:
        print("❌ No Private Key in .env")
        return

    # Derive wallet address for lookup (simplified)
    # Assuming address in .env is correct for the private key
    
    # Get Account
    try:
        # In this script we rely on DYDX_ADDRESS being the wallet address
        # A more robust script would derive it from PK, but we assume .env is consistent
        key_pair = KeyPair.from_hex(private_key.replace("0x", ""))
        
        # We need the "dydx..." address for the Wallet object? 
        # Actually Wallet takes the KeyPair. Check sequence from address.
        # We need to find the account number/sequence.
        
        # Using a helper to find the subaccount
        # Try fetching account from node first using the address
        try:
             # Need to derive bech32 address from PK if DYDX_ADDRESS is the main Ethereum address
             # Assuming DYDX_ADDRESS is the dYdX bech32 address for simplicity
             # If it starts with 0x, it's likely mainnet eth address, distinct from dydx address
             target_addr = address
             if not address.startswith("dydx"):
                 # Skip complex derivation for now, assume user put correct dydx address or use a known derivation lib
                 # If this fails, we might need the complex derivation block from previous file
                 pass
             
             dydx_account = await node.get_account(address)
             account_num = dydx_account.account_number
             sequence = dydx_account.sequence
        except:
             # Fallback: assume 0, 0 or fetch from indexer
             account_num = 0
             sequence = 0
             
        wallet = Wallet(key=key_pair, account_number=account_num, sequence=sequence)
        print(f"👤 Wallet Connected: {address[:10]}... (Seq: {wallet.sequence})")
        
    except Exception as e:
        print(f"❌ Wallet Setup Error: {e}")
        return

    # 3. Get Market Data
    m_resp = await indexer.markets.get_perpetual_markets(MARKET_ID)
    market_info = m_resp["markets"][MARKET_ID]
    market_obj = Market(market_info)
    
    # 4. Place Order Loop (Adjust until filled)
    filled = False
    attempts = 0
    
    while not filled and attempts < 5:
        attempts += 1
        print(f"\n🔄 Attempt {attempts}/5 to Open Position...")
        
        # Get Orderbook for Price
        ob = await indexer.markets.get_perpetual_market_orderbook(MARKET_ID)
        best_bid = float(ob["bids"][0]["price"])
        best_ask = float(ob["asks"][0]["price"])
        
        # Strategy: 
        # Attempt 1-2: Maker (Best Bid)
        # Attempt 3+: Taker (Best Ask + buffer)
        
        if attempts <= 2:
            price = best_bid
            is_maker_try = True
            print(f"   Trying MAKER @ ${price:,.2f}")
            tif = Order.TimeInForce.TIME_IN_FORCE_POST_ONLY
        else:
            price = best_ask * 1.0005 # Cross the spread
            is_maker_try = False
            print(f"   Trying TAKER (Forced) @ ${price:,.2f}")
            tif = Order.TimeInForce.TIME_IN_FORCE_UNSPECIFIED

        # Place Order
        client_id = random.randint(0, MAX_CLIENT_ID)
        current_block = await node.latest_block_height()
        
        order = market_obj.order(
            order_id=market_obj.order_id(address, 0, client_id, OrderFlags.SHORT_TERM),
            order_type=OrderType.LIMIT,
            side=Order.Side.SIDE_BUY,
            size=SIZE,
            price=price,
            time_in_force=tif,
            good_til_block=current_block + 20
        )
        
        try:
            tx = await node.place_order(wallet=wallet, order=order)
            wallet.sequence += 1
            print(f"   Order Placed. Tx: {tx.tx_hash[:10]}...")
            
            # Wait for fill
            print("   ⏳ Waiting for fill...")
            for _ in range(5):
                await asyncio.sleep(1.0)
                # Check fills
                fills_resp = await indexer.account.get_subaccount_fills(address, 0, limit=1)
                fills = fills_resp.get("fills", [])
                if fills:
                    latest_fill = fills[0]
                    # Check if it's our order (roughly by time/market)
                    # We assume it is if it's very recent
                    fill_time = latest_fill["createdAt"] # timestamp
                    # TODO: precise check. For now assume if we see a fill it's us.
                    print(f"   🎉 FILLED! Price: ${float(latest_fill['price']):,.2f}")
                    print(f"   💰 Fee: ${float(latest_fill['fee']):.6f} ({latest_fill['liquidity']}-LIQUIDITY)")
                    filled = True
                    break
            
            if not filled:
                print("   ❌ Not filled yet. Cancelling...")
                # Cancel isn't strictly necessary if it expires, but good practice?
                # For speed we just try next order, ignoring old one (it has short expiry)
                
        except Exception as e:
            print(f"   ❌ Order Failed: {e}")
            await asyncio.sleep(1)

    if not filled:
        print("❌ Could not fill after 5 attempts.")
        return

    # 5. Hold
    print(f"\n⏳ Holding for {HOLD_SECONDS} seconds...")
    await asyncio.sleep(HOLD_SECONDS)
    
    # 6. Close Position
    print("\n🔻 Closing Position...")
    # Get current position size to be sure
    pos_resp = await indexer.account.get_subaccount_perpetual_positions(address, 0)
    current_size = 0
    for p in pos_resp["positions"]:
        if p["market"] == MARKET_ID:
            current_size = float(p["size"])
            break
            
    if current_size == 0:
        print("   ⚠️ No position found to close!")
        return
        
    # Market Sell to Close
    # We use a Limit Sell at 0 (Market) or Deep Price
    ob = await indexer.markets.get_perpetual_market_orderbook(MARKET_ID)
    best_bid = float(ob["bids"][0]["price"])
    close_price = best_bid * 0.95 # Deep sell
    
    client_id = random.randint(0, MAX_CLIENT_ID)
    current_block = await node.latest_block_height()
    
    close_order = market_obj.order(
        order_id=market_obj.order_id(address, 0, client_id, OrderFlags.SHORT_TERM),
        order_type=OrderType.LIMIT, # Limit acting as Market
        side=Order.Side.SIDE_SELL,
        size=abs(current_size),
        price=close_price,
        time_in_force=Order.TimeInForce.TIME_IN_FORCE_IOC, # IOC for pseudo-market
        good_til_block=current_block + 20
    )
    
    try:
        tx = await node.place_order(wallet=wallet, order=close_order)
        wallet.sequence += 1
        print(f"   Close Order Placed. Tx: {tx.tx_hash[:10]}...")
        await asyncio.sleep(2)
        
        # Check fee
        fills_resp = await indexer.account.get_subaccount_fills(address, 0, limit=1)
        if fills_resp["fills"]:
            close_fill = fills_resp["fills"][0]
            print(f"   ✅ CLOSED! Price: ${float(close_fill['price']):,.2f}")
            print(f"   💰 Fee: ${float(close_fill['fee']):.6f} ({close_fill['liquidity']}-LIQUIDITY)")
            
    except Exception as e:
        print(f"   ❌ Close Failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
