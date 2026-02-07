#!/usr/bin/env python3
"""
價格同步測試工具
驗證 Binance 和 dYdX 的價格是否一致
"""

import asyncio
import aiohttp
import time
from datetime import datetime

async def get_binance_price():
    """獲取 Binance 永續合約價格"""
    url = "https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            return float(data['price'])

async def get_dydx_price():
    """獲取 dYdX 價格"""
    url = "https://indexer.dydx.trade/v4/perpetualMarkets?ticker=BTC-USD"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            markets = data.get('markets', {})
            btc_market = markets.get('BTC-USD', {})
            oracle_price = float(btc_market.get('oraclePrice', 0))
            # 使用 oracle price 作為主要價格 (dYdX 用這個計算盈虧)
            return oracle_price, oracle_price

async def get_dydx_position(address: str = "dydx1ul7g6jen6ce84pjacmcwtrg2xyyfe3hgaua7fg"):
    """獲取 dYdX 持倉"""
    url = f"https://indexer.dydx.trade/v4/addresses/{address}/subaccountNumber/0"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            positions = data.get('subaccount', {}).get('openPerpetualPositions', {})
            btc_pos = positions.get('BTC-USD')
            if btc_pos:
                return {
                    'side': btc_pos.get('side'),
                    'size': float(btc_pos.get('size', 0)),
                    'entry_price': float(btc_pos.get('entryPrice', 0)),
                    'unrealized_pnl': float(btc_pos.get('unrealizedPnl', 0)),
                }
            return None

async def main():
    print("="*70)
    print("🔍 價格同步測試工具")
    print("="*70)
    
    # 獲取價格
    binance_price = await get_binance_price()
    dydx_oracle, dydx_index = await get_dydx_price()
    position = await get_dydx_position()
    
    print(f"\n📊 當前價格比較:")
    print(f"   Binance Futures: ${binance_price:,.2f}")
    print(f"   dYdX Oracle:     ${dydx_oracle:,.2f}")
    print(f"   dYdX Index:      ${dydx_index:,.2f}")
    
    # 計算差異
    diff_oracle = (binance_price - dydx_oracle) / dydx_oracle * 100
    diff_index = (binance_price - dydx_index) / dydx_index * 100
    
    print(f"\n📈 價格差異:")
    print(f"   Binance vs dYdX Oracle: {diff_oracle:+.4f}%")
    print(f"   Binance vs dYdX Index:  {diff_index:+.4f}%")
    
    # 持倉分析
    if position:
        print(f"\n💰 dYdX 持倉:")
        print(f"   方向: {position['side']}")
        print(f"   大小: {position['size']:.4f} BTC")
        print(f"   進場價: ${position['entry_price']:,.2f}")
        print(f"   未實現盈虧: ${position['unrealized_pnl']:.2f}")
        
        # 用不同價格計算盈虧
        entry = position['entry_price']
        size = abs(position['size'])
        side = position['side']
        
        print(f"\n📊 盈虧計算 (使用不同價格源):")
        
        for name, price in [("Binance", binance_price), ("dYdX Oracle", dydx_oracle), ("dYdX Index", dydx_index)]:
            if side == "LONG":
                pnl_pct = (price - entry) / entry * 100
            else:
                pnl_pct = (entry - price) / entry * 100
            
            pnl_usd = size * entry * (pnl_pct / 100)
            print(f"   {name:15s}: {pnl_pct:+.4f}% (${pnl_usd:+.2f})")
        
        # 槓桿後盈虧
        print(f"\n📊 槓桿後盈虧 (47x):")
        for name, price in [("Binance", binance_price), ("dYdX Oracle", dydx_oracle), ("dYdX Index", dydx_index)]:
            if side == "LONG":
                pnl_pct = (price - entry) / entry * 100 * 47
            else:
                pnl_pct = (entry - price) / entry * 100 * 47
            
            print(f"   {name:15s}: {pnl_pct:+.2f}%")
    else:
        print(f"\n💰 無 dYdX 持倉")
    
    print("\n" + "="*70)

if __name__ == "__main__":
    asyncio.run(main())
