"""
運行多時段回測

測試不同時間範圍的策略表現
"""

import sys
import os
from pathlib import Path

# 添加專案根目錄到路徑
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import logging
from datetime import datetime, timedelta
from src.backtesting.market_replay_engine import MarketReplayEngine

def run_backtest(start_date: str, end_date: str, name: str):
    """運行單次回測"""
    print(f"\n{'='*80}")
    print(f"開始回測: {name}")
    print(f"{'='*80}\n")
    
    engine = MarketReplayEngine(capital=100.0)
    
    engine.replay(
        start_date=start_date,
        end_date=end_date,
        verbose=False,  # 關閉詳細輸出，只看統計
        progress_interval=120
    )
    
    return engine


if __name__ == "__main__":
    # 切換到專案根目錄
    os.chdir(project_root)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )
    
    # 定義回測場景
    scenarios = [
        {
            "name": "單日測試 (2024-11-10)",
            "start": "2024-11-10",
            "end": "2024-11-10"
        },
        {
            "name": "一週測試 (2024-11-04 to 2024-11-10)",
            "start": "2024-11-04",
            "end": "2024-11-10"
        },
        {
            "name": "兩週測試 (2024-10-28 to 2024-11-10)",
            "start": "2024-10-28",
            "end": "2024-11-10"
        },
        {
            "name": "一個月測試 (2024-10-11 to 2024-11-10)",
            "start": "2024-10-11",
            "end": "2024-11-10"
        },
    ]
    
    results = []
    
    # 運行所有場景
    for scenario in scenarios:
        try:
            engine = run_backtest(
                start_date=scenario["start"],
                end_date=scenario["end"],
                name=scenario["name"]
            )
            
            # 收集結果
            if engine.closed_positions:
                total_pnl = sum(p.pnl_usdt for p in engine.closed_positions)
                total_pnl_pct = (total_pnl / engine.capital) * 100
                win_rate = len([p for p in engine.closed_positions if p.pnl_usdt > 0]) / len(engine.closed_positions) * 100
                
                results.append({
                    "name": scenario["name"],
                    "trades": len(engine.closed_positions),
                    "win_rate": win_rate,
                    "total_pnl_usdt": total_pnl,
                    "total_pnl_pct": total_pnl_pct
                })
            else:
                results.append({
                    "name": scenario["name"],
                    "trades": 0,
                    "win_rate": 0,
                    "total_pnl_usdt": 0,
                    "total_pnl_pct": 0
                })
        
        except Exception as e:
            print(f"\n❌ 回測失敗: {scenario['name']}")
            print(f"   錯誤: {e}\n")
            continue
    
    # 打印總結
    print(f"\n{'='*80}")
    print(f"📊 回測總結")
    print(f"{'='*80}\n")
    
    print(f"{'場景':<40} {'交易數':<10} {'勝率':<10} {'總收益':<15}")
    print(f"{'-'*80}")
    
    for result in results:
        print(f"{result['name']:<40} "
              f"{result['trades']:<10} "
              f"{result['win_rate']:<10.1f}% "
              f"{result['total_pnl_pct']:>+7.2f}% ({result['total_pnl_usdt']:>+8.2f} USDT)")
    
    print(f"\n{'='*80}\n")
