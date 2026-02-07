#!/usr/bin/env python3
"""
啟動 Paper Trading（激進配置）
================================

激進配置：降低閾值，提高交易頻率
目標：5-10 筆/天（基於「賭」的理念 - 在資訊優勢下主動出擊）

配置對比：
保守版（原版）：
  - Z-score: 2.5 sigma（極端才交易）
  - Score threshold: 0.5（需多個信號確認）
  - 結果：0.09 筆/天（太保守）

激進版（本版）：
  - Z-score: 1.5 sigma（較寬鬆）
  - Score threshold: 0.3（單一強信號即可）
  - 預期：5-10 筆/天

風險管理：
  - 槓桿仍維持 10x
  - TP/SL 收緊：1.2% / 0.8%
  - 持倉時間縮短：6 小時

使用方法:
    python scripts/run_paper_trading_aggressive.py [hours]

範例:
    python scripts/run_paper_trading_aggressive.py        # 運行 3 小時（默認）
    python scripts/run_paper_trading_aggressive.py 0.1    # 運行 6 分鐘測試
    python scripts/run_paper_trading_aggressive.py 24     # 運行 24 小時
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
    print("🚀 Starting Paper Trading System (AGGRESSIVE)")
    print("="*80)
    print(f"Duration: {duration_hours} hours")
    print(f"Strategy: Hybrid Funding + Technical (Aggressive)")
    print(f"Configuration:")
    print(f"  - Z-score threshold: 1.5 (vs 2.5 保守版)")
    print(f"  - Score threshold: 0.3 (vs 0.5 保守版)")
    print(f"  - Expected frequency: 5-10 trades/day")
    print(f"  - Risk: TP 1.2% / SL 0.8% / Time 6h")
    print("="*80)
    print()
    
    # 創建策略實例（激進配置）
    strategy = HybridFundingTechnicalStrategy(
        # Funding Rate 配置（降低閾值）
        funding_lookback_days=90,
        funding_zscore_threshold=1.5,  # 🔥 激進：1.5 sigma（vs 2.5）
        
        # RSI 配置（擴大範圍）
        rsi_period=14,
        rsi_oversold=35,  # 🔥 擴大到 35（vs 30）
        rsi_overbought=65,  # 🔥 擴大到 65（vs 70）
        
        # 信號配置（降低閾值）
        signal_score_threshold=0.3,  # 🔥 激進：0.3（vs 0.5）
    )
    
    # 創建 Paper Trading 引擎（收緊風控）
    engine = PaperTradingEngine(
        strategy=strategy,
        initial_capital=10000.0,
        position_size_usd=300.0,  # 單筆 $300
        max_positions=1,
        
        # 🔥 激進風控配置
        leverage=10,
        tp_pct=0.012,  # 1.2% 現貨 TP（vs 1.5%）
        sl_pct=0.008,  # 0.8% 現貨 SL（vs 1.0%）
        time_stop_hours=6,  # 6 小時（vs 12）
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
