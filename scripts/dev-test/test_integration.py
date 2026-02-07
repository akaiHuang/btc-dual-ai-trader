#!/usr/bin/env python3
"""測試策略管理器整合"""

import sys
import os

# 添加項目根目錄到路徑
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.strategy.strategy_manager import StrategyManager

def test_strategy_manager():
    """測試策略管理器基本功能"""
    print("=" * 80)
    print("🧪 測試策略管理器整合")
    print("=" * 80)
    print()
    
    # 初始化策略管理器
    print("1️⃣ 初始化策略管理器...")
    manager = StrategyManager()
    print(f"   ✅ 成功載入策略配置")
    print()
    
    # 獲取所有啟用的模式
    print("2️⃣ 獲取所有啟用的策略模式...")
    active_modes = manager.get_all_modes()
    print(f"   ✅ 找到 {len(active_modes)} 個啟用的策略:")
    for mode in active_modes:
        info = manager.get_strategy_info(mode)
        print(f"      {info['emoji']} {info['name']} - {info['leverage']}x 槓桿, {info['position_size']*100:.0f}% 倉位")
    print()
    
    # 測試風控檢查
    print("3️⃣ 測試風控檢查功能...")
    test_market_data = {
        'vpin': 0.5,
        'spread_bps': 5.0,
        'total_depth': 10.0,
        'obi': 0.3
    }
    test_signal = {
        'direction': 'LONG',
        'strength': 0.7
    }
    
    # 測試 Mode 0 (baseline, 無風控)
    mode = 'mode_0_baseline'
    can_trade, reasons = manager.apply_risk_control(mode, test_market_data, test_signal)
    print(f"   {manager.get_strategy_info(mode)['emoji']} {mode}: {'✅ 通過' if can_trade else '❌ 阻擋'}")
    if reasons:
        print(f"      原因: {', '.join(reasons)}")
    
    # 測試 Mode 3 (full_control, 完整風控)
    mode = 'mode_3_full_control'
    can_trade, reasons = manager.apply_risk_control(mode, test_market_data, test_signal)
    print(f"   {manager.get_strategy_info(mode)['emoji']} {mode}: {'✅ 通過' if can_trade else '❌ 阻擋'}")
    if reasons:
        print(f"      原因: {', '.join(reasons)}")
    
    print()
    
    # 測試高風險市場條件
    print("4️⃣ 測試高風險市場條件...")
    risky_market_data = {
        'vpin': 0.8,  # 高毒性
        'spread_bps': 15.0,  # 寬價差
        'total_depth': 2.0,  # 低流動性
        'obi': 0.3
    }
    
    for mode in ['mode_0_baseline', 'mode_1_vpin_only', 'mode_2_liquidity_only', 'mode_3_full_control']:
        if mode in active_modes:
            can_trade, reasons = manager.apply_risk_control(mode, risky_market_data, test_signal)
            info = manager.get_strategy_info(mode)
            status = '✅ 通過' if can_trade else '❌ 阻擋'
            print(f"   {info['emoji']} {info['name']}: {status}")
            if reasons:
                for reason in reasons:
                    print(f"      • {reason}")
    
    print()
    print("=" * 80)
    print("✅ 測試完成!")
    print("=" * 80)

if __name__ == "__main__":
    test_strategy_manager()
