#!/usr/bin/env python3
"""
單獨測試：真實數據收集（1分鐘）
"""

import asyncio
from binance import AsyncClient, BinanceSocketManager
from datetime import datetime
import time

async def test_data_collection():
    print("="*60)
    print("📥 測試真實 Binance 數據接收")
    print("="*60)
    print(f"開始: {datetime.now().strftime('%H:%M:%S')}")
    print("運行 60 秒...\n")
    
    client = await AsyncClient.create()
    bsm = BinanceSocketManager(client)
    
    depth_socket = bsm.depth_socket('BTCUSDT')
    trade_socket = bsm.aggtrade_socket('BTCUSDT')
    
    orderbook_count = 0
    trade_count = 0
    start_time = time.time()
    last_report = start_time
    
    latest_price = 0
    
    try:
        print("✅ WebSocket 已連接\n")
        
        async with depth_socket as ds, trade_socket as ts:
            # 創建兩個獨立任務處理流
            async def process_depth():
                nonlocal orderbook_count, latest_price
                while time.time() - start_time < 60:
                    try:
                        msg = await ds.recv()
                        # 打印第一條消息看結構
                        if orderbook_count == 0:
                            print(f"首條訂單簿消息類型: {msg.get('e', 'unknown')}")
                        
                        if msg.get('e') != 'error':
                            orderbook_count += 1
                            # depth socket 返回的是差分更新，不是完整訂單簿
                            # 有 'b' (bids) 和 'a' (asks) 欄位
                            if 'b' in msg and 'a' in msg:
                                if msg['b'] and msg['a']:
                                    bid = float(msg['b'][0][0])
                                    ask = float(msg['a'][0][0])
                                    latest_price = (bid + ask) / 2
                    except Exception as e:
                        print(f"訂單簿錯誤: {e}")
                        if 'queue' in str(e).lower():
                            await asyncio.sleep(0.1)
                        else:
                            break
            
            async def process_trade():
                nonlocal trade_count, latest_price
                while time.time() - start_time < 60:
                    try:
                        msg = await ts.recv()
                        if msg.get('e') != 'error' and 'p' in msg:
                            trade_count += 1
                            latest_price = float(msg['p'])
                    except Exception as e:
                        if 'queue' in str(e).lower():
                            await asyncio.sleep(0.1)
                        else:
                            break
            
            async def report():
                nonlocal last_report
                while time.time() - start_time < 60:
                    if time.time() - last_report >= 10:
                        elapsed = time.time() - start_time
                        print(f"⏱️  {elapsed:.0f}秒 | 訂單簿: {orderbook_count} | 交易: {trade_count} | 價格: ${latest_price:.2f}")
                        last_report = time.time()
                    await asyncio.sleep(1)
            
            # 並行運行三個任務
            await asyncio.gather(
                process_depth(),
                process_trade(),
                report(),
                return_exceptions=True
            )
    
    finally:
        await client.close_connection()
    
    print(f"\n✅ 測試完成")
    print(f"   總訂單簿更新: {orderbook_count}")
    print(f"   總交易: {trade_count}")
    print(f"   最後價格: ${latest_price:.2f}")
    print(f"   平均速率:")
    print(f"     - 訂單簿: {orderbook_count/60:.1f} 條/秒")
    print(f"     - 交易: {trade_count/60:.1f} 筆/秒")
    
    # 驗證數據質量
    if orderbook_count > 0 and trade_count > 0:
        print(f"\n✅ 數據接收正常，可以開始完整測試")
        return True
    else:
        print(f"\n❌ 數據接收異常")
        return False

if __name__ == "__main__":
    try:
        result = asyncio.run(test_data_collection())
        exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  測試被中斷")
        exit(1)
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
