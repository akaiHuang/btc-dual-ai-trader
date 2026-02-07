#!/usr/bin/env python3
"""
多檔位 Paper Trading 啟動器
===========================

支援動態切換 M0-M5 檔位，實時監控與調整

使用方法:
    python scripts/run_paper_trading_multimode.py [mode] [hours]

範例:
    python scripts/run_paper_trading_multimode.py M2 3      # M2 模式運行 3 小時
    python scripts/run_paper_trading_multimode.py M5 1      # M5 激進模式運行 1 小時
    python scripts/run_paper_trading_multimode.py           # M2 默認運行 3 小時

可用模式:
    M0 - Ultra Safe (0.1~0.5筆/天) - 實單用
    M1 - Safe (0.5~1筆/天)
    M2 - Normal (3~10筆/天) ← 推薦 Paper Trading
    M3 - Aggressive (10~20筆/天)
    M4 - Very Aggressive (20~30筆/天)
    M5 - Ultra Aggressive (30+筆/天) ← 壓力測試
"""

import asyncio
import sys
from pathlib import Path

# 添加項目根目錄
sys.path.append(str(Path(__file__).parent.parent))

from src.trading.paper_trading_engine import PaperTradingEngine
from src.strategy.hybrid_multi_mode import MultiModeHybridStrategy, TradingMode


def parse_mode(mode_str: str) -> TradingMode:
    """解析模式字符串"""
    mode_map = {
        'M0': TradingMode.M0_ULTRA_SAFE,
        'M1': TradingMode.M1_SAFE,
        'M2': TradingMode.M2_NORMAL,
        'M3': TradingMode.M3_AGGRESSIVE,
        'M4': TradingMode.M4_VERY_AGGRESSIVE,
        'M5': TradingMode.M5_ULTRA_AGGRESSIVE,
    }
    
    mode_upper = mode_str.upper()
    if mode_upper not in mode_map:
        print(f"❌ Invalid mode: {mode_str}")
        print(f"Available modes: {', '.join(mode_map.keys())}")
        sys.exit(1)
    
    return mode_map[mode_upper]


def main():
    """主程式"""
    # 解析命令行參數
    mode = TradingMode.M2_NORMAL  # 默認 M2
    duration_hours = 3.0  # 默認 3 小時
    
    if len(sys.argv) > 1:
        mode = parse_mode(sys.argv[1])
    
    if len(sys.argv) > 2:
        try:
            duration_hours = float(sys.argv[2])
        except ValueError:
            print(f"❌ Invalid duration: {sys.argv[2]}")
            print(f"Usage: {sys.argv[0]} [mode] [hours]")
            sys.exit(1)
    
    print("="*80)
    print("🎮 Multi-Mode Paper Trading System")
    print("="*80)
    print(f"Mode: {mode.value}")
    print(f"Duration: {duration_hours} hours")
    print("="*80)
    print()
    
    # 創建多檔位策略
    strategy = MultiModeHybridStrategy(
        initial_mode=mode,
        enable_llm_advisor=False,  # TODO: 可以後續啟用
    )
    
    # 顯示當前配置
    config = strategy.get_current_config()
    print(f"📊 Current Configuration:")
    print(f"   Description: {config.description}")
    print(f"   Target Frequency: {config.target_frequency}")
    print(f"   Funding Z-score threshold: {config.funding_zscore_threshold}")
    print(f"   Signal score threshold: {config.signal_score_threshold}")
    print(f"   RSI: {config.rsi_oversold}/{config.rsi_overbought}")
    print(f"   Volume spike: {config.volume_spike_threshold}x")
    print(f"   Leverage: {config.leverage}x")
    print(f"   TP/SL: {config.tp_pct:.2%} / {config.sl_pct:.2%} (spot %)")
    print(f"   Min move: {config.min_move_threshold}x cost")
    print(f"   Cooldown: {config.cooldown_minutes} minutes")
    print(f"   Time stop: {config.time_stop_hours} hours")
    print(f"   Min ATR: {config.min_atr_pct:.3%}")
    print()
    print("="*80)
    print()
    
    # 創建 Paper Trading 引擎
    # 注意：這裡需要修改 Engine 來支援 MultiMode
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
        
        # 顯示性能統計
        print("\n" + "="*80)
        print("📈 Performance Summary")
        print("="*80)
        tracker = strategy.performance_tracker
        print(f"Total Signals: {tracker['total_signals']}")
        print(f"Total Trades: {tracker['total_trades']}")
        if tracker['total_trades'] > 0:
            win_rate = tracker['winning_trades'] / tracker['total_trades']
            print(f"Win Rate: {win_rate:.1%}")
            print(f"Total PnL: {tracker['total_pnl']:.2f}%")
        print()
        
        # 顯示模式切換歷史
        if tracker['mode_history']:
            print("🔄 Mode Switch History:")
            for switch in tracker['mode_history']:
                print(f"   {switch['timestamp']}: {switch['from_mode']} → {switch['to_mode']}")
                print(f"      Reason: {switch['reason']}")
            print()
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
