#!/usr/bin/env python3
"""
啟動 Paper Trading
==================

快速啟動腳本，運行 Hybrid 策略的模擬交易

使用方法:
    python scripts/run_paper_trading.py [hours]

範例:
    python scripts/run_paper_trading.py        # 運行 3 小時（默認）
    python scripts/run_paper_trading.py 1      # 運行 1 小時
    python scripts/run_paper_trading.py 24     # 運行 24 小時
"""

import asyncio
import sys
from pathlib import Path

# 添加項目根目錄
sys.path.append(str(Path(__file__).parent.parent))

from src.trading.paper_trading_engine import PaperTradingEngine
from src.strategy.hybrid_funding_technical import HybridFundingTechnicalStrategy


def main():
    """主程式"""
    # 解析命令行參數
    duration_hours = 3.0  # 默認 3 小時
    if len(sys.argv) > 1:
        try:
            duration_hours = float(sys.argv[1])
        except ValueError:
            print(f"❌ Invalid duration: {sys.argv[1]}")
            print(f"Usage: {sys.argv[0]} [hours]")
            sys.exit(1)
    
    print("="*80)
    print("🚀 Starting Paper Trading System")
    print("="*80)
    print(f"Duration: {duration_hours} hours")
    print(f"Strategy: Hybrid Funding + Technical")
    print(f"Configuration: Conservative (Z-score 2.5, 10x leverage)")
    print("="*80)
    print()
    
    # 創建策略實例（保守配置）
    strategy = HybridFundingTechnicalStrategy(
        # Funding Rate 配置
        funding_lookback_days=90,
        funding_zscore_threshold=2.5,  # 保守：2.5 sigma
        
        # 信號配置
        signal_score_threshold=0.5,
        rsi_period=14,
        rsi_oversold=30,
        rsi_overbought=70,
    )
    
    # 創建 Paper Trading 引擎
    engine = PaperTradingEngine(
        strategy=strategy,
        initial_capital=10000.0,
        position_size_usd=300.0,
        max_positions=1,
    )
    
    # 運行
    try:
        asyncio.run(engine.start(duration_hours=duration_hours))
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
