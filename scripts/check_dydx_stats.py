#!/usr/bin/env python3
"""
查詢 dYdX 真實帳戶餘額和交易統計
"""
import asyncio
import os
import sys
import aiohttp
sys.path.insert(0, '/Users/akaihuangm1/Desktop/btn')

from dotenv import load_dotenv
load_dotenv()

async def main():
    # 設定
    address = os.getenv('DYDX_ADDRESS')
    
    if not address:
        print("❌ 請設定 DYDX_ADDRESS 環境變數")
        return
    
    base_url = "https://indexer.dydx.trade/v4"
    
    async with aiohttp.ClientSession() as session:
        # 1. 獲取真實餘額
        print('=' * 60)
        print('💰 dYdX 錢包真實餘額')
        print('=' * 60)
        
        try:
            async with session.get(f"{base_url}/addresses/{address}/subaccountNumber/0") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    account = data.get('subaccount', {})
                    equity = float(account.get('equity', 0))
                    free_collateral = float(account.get('freeCollateral', 0))
                    print(f'   地址: {address}')
                    print(f'   總權益 (Equity): ${equity:,.2f}')
                    print(f'   可用保證金: ${free_collateral:,.2f}')
        except Exception as e:
            print(f'   ❌ 獲取帳戶失敗: {e}')

        # 2. 獲取歷史交易紀錄 (fills)
        print()
        print('=' * 60)
        print('📜 歷史成交紀錄')
        print('=' * 60)
        
        fills = []
        try:
            async with session.get(f"{base_url}/fills?address={address}&subaccountNumber=0&limit=100") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    fills = data.get('fills', [])
        except Exception as e:
            print(f'   ❌ 獲取成交紀錄失敗: {e}')
        
        if fills:
            # 按時間排序
            fills = sorted(fills, key=lambda x: x.get('createdAt', ''), reverse=True)
            
            print(f'   總成交: {len(fills)} 筆')
            print()
            
            # 計算每筆交易的盈虧
            trades = []
            current_trade = None
            
            for fill in reversed(fills):  # 從舊到新
                side = fill.get('side', '')
                size = float(fill.get('size', 0))
                price = float(fill.get('price', 0))
                fee = float(fill.get('fee', 0))
                time_str = fill.get('createdAt', '')[:19]
                
                if current_trade is None:
                    # 開倉
                    current_trade = {
                        'entry_side': side,
                        'entry_price': price,
                        'size': size,
                        'entry_time': time_str,
                        'entry_fee': fee
                    }
                else:
                    # 平倉 (方向相反)
                    if (current_trade['entry_side'] == 'BUY' and side == 'SELL') or \
                       (current_trade['entry_side'] == 'SELL' and side == 'BUY'):
                        # 計算盈虧
                        if current_trade['entry_side'] == 'BUY':  # LONG
                            pnl = (price - current_trade['entry_price']) * size
                        else:  # SHORT
                            pnl = (current_trade['entry_price'] - price) * size
                        
                        total_fee = current_trade['entry_fee'] + fee
                        net_pnl = pnl - total_fee
                        
                        trades.append({
                            'entry_time': current_trade['entry_time'],
                            'exit_time': time_str,
                            'direction': 'LONG' if current_trade['entry_side'] == 'BUY' else 'SHORT',
                            'entry_price': current_trade['entry_price'],
                            'exit_price': price,
                            'size': size,
                            'gross_pnl': pnl,
                            'fee': total_fee,
                            'net_pnl': net_pnl
                        })
                        current_trade = None
                    else:
                        # 同方向 = 加倉，更新
                        current_trade = {
                            'entry_side': side,
                            'entry_price': price,
                            'size': size,
                            'entry_time': time_str,
                            'entry_fee': fee
                        }
            
            # 顯示交易統計
            if trades:
                wins = [t for t in trades if t['net_pnl'] > 0]
                losses = [t for t in trades if t['net_pnl'] <= 0]
                total_pnl = sum(t['net_pnl'] for t in trades)
                total_fee = sum(t['fee'] for t in trades)
                
                print('   📊 交易統計:')
                print(f'      總交易: {len(trades)} 筆')
                print(f'      勝: {len(wins)}  敗: {len(losses)}')
                print(f'      勝率: {len(wins)/len(trades)*100:.1f}%')
                print(f'      總盈虧: ${total_pnl:+,.2f}')
                print(f'      總手續費: ${total_fee:,.4f}')
                if trades:
                    print(f'      平均盈虧: ${total_pnl/len(trades):+,.2f}/筆')
                print()
                
                print('   📜 最近交易明細:')
                for i, t in enumerate(reversed(trades[-10:]), 1):
                    dir_emoji = '🟢' if t['direction'] == 'LONG' else '🔴'
                    pnl_emoji = '✅' if t['net_pnl'] > 0 else '❌'
                    print(f'      {i}. {dir_emoji} {t["direction"]} {t["size"]} BTC')
                    print(f'         進: ${t["entry_price"]:,.0f} → 出: ${t["exit_price"]:,.0f}')
                    print(f'         {pnl_emoji} 淨盈虧: ${t["net_pnl"]:+,.2f} (手續費: ${t["fee"]:.4f})')
                    print(f'         時間: {t["entry_time"]} → {t["exit_time"]}')
                    print()
            else:
                print('   無完整交易紀錄')
            
            # 顯示原始成交
            print('   📝 原始成交紀錄 (最近 10 筆):')
            for fill in fills[:10]:
                side = fill.get('side', '')
                size = fill.get('size', '')
                price = float(fill.get('price', 0))
                fee = float(fill.get('fee', 0))
                time_str = fill.get('createdAt', '')[:19]
                side_emoji = '🟢' if side == 'BUY' else '🔴'
                print(f'      {side_emoji} {side} {size} @ ${price:,.2f} (fee: ${fee:.4f}) [{time_str}]')
        else:
            print('   無成交紀錄')

if __name__ == '__main__':
    asyncio.run(main())
