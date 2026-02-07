"""
測試技術指標策略 (Mode 8)
"""

import sys
import numpy as np
from pathlib import Path

# 添加專案根目錄到路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy.strategy_manager import StrategyManager


def test_technical_indicator_strategy():
    """測試技術指標策略"""
    
    print("=" * 60)
    print("測試技術指標策略 (Mode 8)")
    print("=" * 60)
    
    # 初始化策略管理器
    manager = StrategyManager()
    
    # 獲取 Mode 8 策略
    mode8 = manager.get_strategy('mode_8_technical_indicators')
    
    if not mode8:
        print("❌ Mode 8 策略未啟用")
        return
    
    print(f"\n策略資訊:")
    print(f"  名稱: {mode8.name}")
    print(f"  Emoji: {mode8.emoji}")
    print(f"  描述: {mode8.description}")
    print(f"  槓桿: {mode8.leverage}x")
    print(f"  倉位: {mode8.position_size * 100}%")
    print(f"  止損: {mode8.risk_control['stop_loss'] * 100}%")
    print(f"  止盈: {mode8.risk_control['take_profit'] * 100}%")
    
    # 模擬市場數據（上漲趨勢）
    print("\n" + "=" * 60)
    print("情境 1: 強勢上漲趨勢 (價格從 90000 漲到 91500)")
    print("=" * 60)
    
    # 重置策略（清空歷史）
    mode8.price_history = []
    mode8.high_history = []
    mode8.low_history = []
    
    base_price = 90000
    for i in range(60):  # 增加到 60 根 K 線
        # 模擬上漲趨勢 + 隨機波動
        trend = i * 25  # 趨勢向上
        noise = np.random.uniform(-50, 50)  # 隨機噪音
        price = base_price + trend + noise
        
        market_data = {
            'price': price,
            'vpin': 0.5,
            'spread_bps': 5,
            'total_depth': 10,
            'obi': 0.3,
            'obi_velocity': 0.1
        }
        
        # 更新價格歷史
        mode8.update_price_history(market_data)
    
    # 獲取技術指標信號
    signals = mode8.get_technical_signals(market_data)
    print(f"\n技術指標信號 (最終價格: {price:.2f}):")
    for indicator, signal in signals.items():
        emoji = "🟢" if "BUY" in signal else "🔴" if "SELL" in signal else "⚪"
        print(f"  {emoji} {indicator}: {signal}")
    
    # 測試做多信號
    long_signal = {'direction': 'LONG', 'confidence': 0.8}
    can_enter, reasons = mode8.check_entry(market_data, long_signal)
    
    print(f"\n做多信號檢查:")
    print(f"  結果: {'✅ 通過' if can_enter else '❌ 被阻擋'}")
    if reasons:
        for reason in reasons:
            print(f"  - {reason}")
    
    # 情境 2: 下跌趨勢
    print("\n" + "=" * 60)
    print("情境 2: 強勢下跌趨勢 (價格從 91500 跌到 90000)")
    print("=" * 60)
    
    # 重置策略
    mode8.price_history = []
    mode8.high_history = []
    mode8.low_history = []
    
    for i in range(60):
        # 模擬下跌趨勢 + 隨機波動
        trend = -i * 25  # 趨勢向下
        noise = np.random.uniform(-50, 50)
        price = 91500 + trend + noise
        
        market_data = {
            'price': price,
            'vpin': 0.4,
            'spread_bps': 5,
            'total_depth': 10,
            'obi': -0.3,
            'obi_velocity': -0.1
        }
        
        mode8.update_price_history(market_data)
    
    # 獲取技術指標信號
    signals = mode8.get_technical_signals(market_data)
    print(f"\n技術指標信號 (最終價格: {price:.2f}):")
    for indicator, signal in signals.items():
        emoji = "🟢" if "BUY" in signal else "🔴" if "SELL" in signal else "⚪"
        print(f"  {emoji} {indicator}: {signal}")
    
    # 測試做空信號
    short_signal = {'direction': 'SHORT', 'confidence': 0.8}
    can_enter, reasons = mode8.check_entry(market_data, short_signal)
    
    print(f"\n做空信號檢查:")
    print(f"  結果: {'✅ 通過' if can_enter else '❌ 被阻擋'}")
    if reasons:
        for reason in reasons:
            print(f"  - {reason}")
    
    # 情境 3: 震盪市場
    print("\n" + "=" * 60)
    print("情境 3: 震盪市場 (價格在 90500 ± 300 波動)")
    print("=" * 60)
    
    # 重置策略
    mode8.price_history = []
    mode8.high_history = []
    mode8.low_history = []
    
    for i in range(60):
        # 正弦波震盪 + 隨機噪音
        sine_wave = 300 * np.sin(i * 0.2)
        noise = np.random.uniform(-50, 50)
        price = 90500 + sine_wave + noise
        
        market_data = {
            'price': price,
            'vpin': 0.6,
            'spread_bps': 8,
            'total_depth': 8,
            'obi': 0.1 * np.sin(i * 0.2),  # OBI 也跟著震盪
            'obi_velocity': 0.05
        }
        
        mode8.update_price_history(market_data)
    
    # 獲取技術指標信號
    signals = mode8.get_technical_signals(market_data)
    print(f"\n技術指標信號 (最終價格: {price:.2f}):")
    for indicator, signal in signals.items():
        emoji = "🟢" if "BUY" in signal else "🔴" if "SELL" in signal else "⚪"
        print(f"  {emoji} {indicator}: {signal}")
    
    # 測試做多信號
    long_signal = {'direction': 'LONG', 'confidence': 0.5}
    can_enter, reasons = mode8.check_entry(market_data, long_signal)
    
    print(f"\n做多信號檢查:")
    print(f"  結果: {'✅ 通過' if can_enter else '❌ 被阻擋'}")
    if reasons:
        for reason in reasons:
            print(f"  - {reason}")
    
    print("\n" + "=" * 60)
    print("測試完成！")
    print("=" * 60)


if __name__ == "__main__":
    test_technical_indicator_strategy()
