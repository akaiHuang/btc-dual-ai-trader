"""
快速回測 - 減少事件密度以加快速度

用於快速驗證策略邏輯，不追求完美精度
"""

import sys
import os
from pathlib import Path

# 添加專案根目錄到路徑
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import logging
from src.backtesting.market_replay_engine import MarketReplayEngine
from src.backtesting.historical_data_loader import HistoricalDataLoader

if __name__ == "__main__":
    # 切換到專案根目錄
    os.chdir(project_root)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )
    
    print("\n" + "="*80)
    print("⚡ 快速回測模式")
    print("="*80)
    print("說明: 使用較低的事件密度以加快回測速度")
    print("      - 訂單簿更新: 1000ms (vs 100ms)")
    print("      - 交易: 10筆/分鐘 (vs 50筆/分鐘)")
    print("="*80 + "\n")
    
    # 創建引擎
    engine = MarketReplayEngine(capital=100.0)
    
    # 加載數據
    print("加載數據...")
    engine.data_loader.load_klines(
        symbol="BTCUSDT",
        interval="1m",
        start_date="2024-11-10",
        end_date="2024-11-10"
    )
    
    # 生成事件（較少密度）
    print("\n生成市場事件（快速模式）...")
    events = engine.data_loader.get_market_events(
        start_date="2024-11-10",
        end_date="2024-11-10",
        orderbook_update_interval_ms=1000,  # 1 秒更新一次（vs 100ms）
        trades_per_minute=10  # 10 筆交易/分鐘（vs 50）
    )
    
    print(f"生成完成: {len(events)} 個事件")
    print(f"  - 訂單簿更新: {sum(1 for e in events if e['type'] == 'ORDERBOOK')}")
    print(f"  - 交易: {sum(1 for e in events if e['type'] == 'TRADE')}")
    
    # 運行回測
    print("\n開始回放...")
    print("="*80)
    
    import time
    start_time = time.time()
    last_progress_time = 0
    
    for i, event in enumerate(events):
        event_type = event['type']
        event_data = event['data']
        event_timestamp = event['timestamp'].timestamp() * 1000
        
        if event_type == "ORDERBOOK":
            engine.process_orderbook(event_data)
        elif event_type == "TRADE":
            engine.process_trade(event_data)
        
        # 檢查是否需要做決策
        if event_timestamp - engine.last_decision_time >= engine.decision_interval * 1000:
            engine.make_decision(event_timestamp, verbose=False)
            engine.last_decision_time = event_timestamp
        
        # 顯示進度
        if time.time() - last_progress_time >= 30:
            progress = (i + 1) / len(events) * 100
            print(f"進度: {progress:.1f}% ({i+1}/{len(events)} 事件)")
            if engine.open_position:
                pnl_usdt, pnl_pct = engine.open_position.get_unrealized_pnl(engine.latest_price)
                print(f"當前持倉: {engine.open_position.direction}, 未實現盈虧: {pnl_usdt:+.4f} USDT ({pnl_pct:+.2f}%)")
            last_progress_time = time.time()
    
    # 如果還有未平倉的持倉，強制平倉
    if engine.open_position:
        engine.close_position(engine.latest_price, "BACKTEST_END", event_timestamp)
    
    elapsed = time.time() - start_time
    print(f"\n回測完成！用時: {elapsed:.1f} 秒")
    
    # 打印統計
    engine.print_statistics()
