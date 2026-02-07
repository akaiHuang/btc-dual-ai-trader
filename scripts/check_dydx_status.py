#!/usr/bin/env python3
"""
查詢 dYdX 帳戶狀態：交易紀錄、未平倉訂單、持倉
"""
import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

async def main():
    address = os.getenv('DYDX_ADDRESS', '')
    subaccount = os.getenv('DYDX_SUBACCOUNT_NUMBER', '0')
    
    if not address:
        print('❌ 無法取得 DYDX_ADDRESS')
        return
    
    print(f'📍 dYdX 地址: {address[:20]}...{address[-10:]}')
    print(f'📍 子帳戶: {subaccount}')
    print()
    
    async with aiohttp.ClientSession() as session:
        # 1. 查詢 fills
        print('=' * 100)
        print('📊 dYdX 交易紀錄 (最近 20 筆 fills)')
        print('=' * 100)
        url = f'https://indexer.dydx.trade/v4/fills?address={address}&subaccountNumber={subaccount}&limit=20'
        async with session.get(url) as resp:
            data = await resp.json()
            fills = data.get('fills', [])
            if fills:
                for f in fills:
                    side = f.get('side', '?')
                    size = float(f.get('size', 0))
                    price = float(f.get('price', 0))
                    fee = float(f.get('fee', 0))
                    created = f.get('createdAt', '')[:19]
                    order_id = f.get('orderId', '')[:8]
                    market = f.get('market', '?')
                    print(f'{created} | {market:8} | {side:5} | {size:.4f} @ ${price:,.2f} | fee: ${fee:.4f} | order: {order_id}...')
            else:
                print('無交易紀錄')
        
        print()
        
        # 2. 查詢未平倉訂單 (OPEN)
        print('=' * 100)
        print('📋 dYdX 未平倉訂單 (OPEN)')
        print('=' * 100)
        url = f'https://indexer.dydx.trade/v4/orders?address={address}&subaccountNumber={subaccount}&status=OPEN'
        async with session.get(url) as resp:
            data = await resp.json()
            orders = data if isinstance(data, list) else data.get('orders', [])
            if orders:
                for o in orders:
                    order_id = o.get('id', '')[:16]
                    side = o.get('side', '?')
                    size = float(o.get('size', 0))
                    price = float(o.get('price', 0))
                    status = o.get('status', '?')
                    order_type = o.get('type', '?')
                    trigger = o.get('triggerPrice', '')
                    created = o.get('createdAt', '')[:19]
                    print(f'{order_id}... | {status:12} | {order_type:20} | {side:5} | {size:.4f} @ ${price:,.2f} | trigger: {trigger} | {created}')
            else:
                print('無 OPEN 訂單')
        
        print()
        
        # 3. 查詢未觸發條件單 (UNTRIGGERED)
        print('=' * 100)
        print('📋 dYdX 未觸發條件單 (UNTRIGGERED)')
        print('=' * 100)
        url = f'https://indexer.dydx.trade/v4/orders?address={address}&subaccountNumber={subaccount}&status=UNTRIGGERED'
        async with session.get(url) as resp:
            data = await resp.json()
            orders = data if isinstance(data, list) else data.get('orders', [])
            if orders:
                for o in orders:
                    order_id = o.get('id', '')[:16]
                    side = o.get('side', '?')
                    size = float(o.get('size', 0))
                    price = float(o.get('price', 0))
                    status = o.get('status', '?')
                    order_type = o.get('type', '?')
                    trigger = o.get('triggerPrice', '')
                    created = o.get('createdAt', '')[:19]
                    print(f'{order_id}... | {status:12} | {order_type:20} | {side:5} | {size:.4f} @ ${price:,.2f} | trigger: {trigger} | {created}')
            else:
                print('無 UNTRIGGERED 訂單')
        
        print()
        
        # 4. 查詢持倉
        print('=' * 100)
        print('💰 dYdX 當前持倉')
        print('=' * 100)
        url = f'https://indexer.dydx.trade/v4/addresses/{address}/subaccountNumber/{subaccount}'
        async with session.get(url) as resp:
            data = await resp.json()
            subaccount_data = data.get('subaccount', {})
            positions = subaccount_data.get('openPerpetualPositions', {})
            equity = float(subaccount_data.get('equity', 0))
            
            if positions:
                for market, p in positions.items():
                    side = p.get('side', '?')
                    size = float(p.get('size', 0))
                    entry = float(p.get('entryPrice', 0))
                    unrealized = float(p.get('unrealizedPnl', 0))
                    if abs(size) > 0.00001:
                        print(f'{market} | {side} | {size:.4f} @ ${entry:,.2f} | 未實現 PnL: ${unrealized:+.2f}')
                
                has_pos = any(abs(float(p.get('size', 0))) > 0.00001 for p in positions.values())
                if not has_pos:
                    print('無持倉 (所有倉位 size = 0)')
            else:
                print('無持倉')
            
            print(f'\n💵 帳戶權益: ${equity:.2f}')

        # 5. 計算交易盈虧
        print()
        print('=' * 100)
        print('📈 交易盈虧分析 (配對開平倉)')
        print('=' * 100)
        
        # 取得更多 fills 來分析
        url = f'https://indexer.dydx.trade/v4/fills?address={address}&subaccountNumber={subaccount}&limit=100'
        async with session.get(url) as resp:
            data = await resp.json()
            fills = data.get('fills', [])
            
            if fills:
                # 按時間排序（舊到新）
                fills.sort(key=lambda x: x.get('createdAt', ''))
                
                # 簡單配對：按順序配對開平倉
                total_pnl = 0
                total_fee = 0
                trade_count = 0
                
                i = 0
                while i < len(fills) - 1:
                    f1 = fills[i]
                    f2 = fills[i + 1]
                    
                    side1 = f1.get('side', '')
                    side2 = f2.get('side', '')
                    
                    # 如果是相反方向，視為一組交易
                    if (side1 == 'BUY' and side2 == 'SELL') or (side1 == 'SELL' and side2 == 'BUY'):
                        price1 = float(f1.get('price', 0))
                        price2 = float(f2.get('price', 0))
                        size1 = float(f1.get('size', 0))
                        size2 = float(f2.get('size', 0))
                        fee1 = float(f1.get('fee', 0))
                        fee2 = float(f2.get('fee', 0))
                        
                        # 計算 PnL
                        if side1 == 'BUY':  # LONG
                            pnl = (price2 - price1) * min(size1, size2)
                        else:  # SHORT
                            pnl = (price1 - price2) * min(size1, size2)
                        
                        fee = fee1 + fee2
                        net_pnl = pnl - fee
                        
                        pnl_pct = ((price2 - price1) / price1 * 100) if side1 == 'BUY' else ((price1 - price2) / price2 * 100)
                        
                        emoji = '🟢' if net_pnl > 0 else '🔴'
                        print(f'交易 #{trade_count + 1}: {side1} @ ${price1:,.2f} → {side2} @ ${price2:,.2f} | PnL: {emoji} ${net_pnl:+.2f} ({pnl_pct:+.2f}%) | fee: ${fee:.4f}')
                        
                        total_pnl += pnl
                        total_fee += fee
                        trade_count += 1
                        i += 2
                    else:
                        i += 1
                
                print()
                print(f'📊 總交易數: {trade_count}')
                print(f'💰 總毛利: ${total_pnl:+.2f}')
                print(f'💸 總手續費: ${total_fee:.4f}')
                print(f'💵 淨利潤: ${total_pnl - total_fee:+.2f}')

if __name__ == '__main__':
    asyncio.run(main())
