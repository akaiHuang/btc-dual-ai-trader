"""
測試 Market Replay Engine
"""

import sys
import os
from pathlib import Path

# 添加專案根目錄到路徑
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import logging
from src.backtesting.market_replay_engine import MarketReplayEngine

if __name__ == "__main__":
    # 切換到專案根目錄
    os.chdir(project_root)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )
    
    engine = MarketReplayEngine(capital=100.0)
    
    # 回測 2024-11-10 一整天
    engine.replay(
        start_date="2024-11-10",
        end_date="2024-11-10",
        verbose=True,
        progress_interval=300  # 每 5 分鐘顯示一次進度
    )
