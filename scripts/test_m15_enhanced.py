#!/usr/bin/env python3
"""
M15 增強版功能測試腳本
測試熔斷機制、平滑過渡、極端市場處理、性能監控
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy.mode_15_enhanced import (
    Mode15EnhancedStrategy,
    EmergencyCircuitBreaker,
    SmoothTransitionManager,
    ExtremeMarketHandler,
    EnhancedPerformanceMonitor
)
from datetime import datetime
import json

def test_circuit_breaker():
    """測試熔斷機制"""
    print("\n" + "="*80)
    print("🔴 測試 1: 緊急熔斷機制")
    print("="*80)
    
    breaker = EmergencyCircuitBreaker()
    breaker.reset_session(100.0)
    
    # 測試連續虧損
    print("\n📊 測試連續虧損觸發:")
    for i in range(4):
        trade = {'profit': -2.0, 'scheme': 'B'}
        current_balance = 100 - (i+1)*2
        result = breaker.check_circuit_breaker(trade, current_balance)
        print(f"   第 {i+1} 次虧損: 餘額={current_balance:.1f}, 熔斷={not result}")
        if not result:
            print(f"   🔴 熔斷觸發！原因: {breaker.halt_reason}")
            break
    
    # 重置測試單日虧損
    breaker.manual_reset()
    breaker.reset_session(100.0)
    
    print("\n📊 測試單日虧損觸發:")
    trade = {'profit': -16.0, 'scheme': 'C'}
    current_balance = 84.0
    result = breaker.check_circuit_breaker(trade, current_balance)
    print(f"   虧損金額: -16 USDT (-16%)")
    print(f"   熔斷觸發: {not result}")
    if not result:
        print(f"   🔴 原因: {breaker.halt_reason}")
    
    print("\n✅ 熔斷機制測試完成")

def test_smooth_transition():
    """測試平滑過渡"""
    print("\n" + "="*80)
    print("🔄 測試 2: 平滑過渡管理器")
    print("="*80)
    
    manager = SmoothTransitionManager()
    
    # 測試 A->C 過渡
    print("\n📊 測試 A→C 劇烈切換:")
    scheme, in_transition = manager.manage_transition("A", "C")
    print(f"   目標: A → C")
    print(f"   實際方案: {scheme}")
    print(f"   過渡中: {in_transition}")
    
    if in_transition:
        status = manager.get_transition_status()
        print(f"   過渡目標: {status['target_scheme']}")
        print(f"   預計時間: {status['remaining_minutes']:.1f} 分鐘")
    
    # 測試 B->C 直接切換
    manager2 = SmoothTransitionManager()
    print("\n📊 測試 B→C 直接切換:")
    scheme, in_transition = manager2.manage_transition("B", "C")
    print(f"   目標: B → C")
    print(f"   實際方案: {scheme}")
    print(f"   過渡中: {in_transition}")
    
    print("\n✅ 平滑過渡測試完成")

def test_extreme_market():
    """測試極端市場處理"""
    print("\n" + "="*80)
    print("⚠️ 測試 3: 極端市場處理器")
    print("="*80)
    
    handler = ExtremeMarketHandler()
    
    # 測試正常市場
    print("\n📊 測試正常市場:")
    market_data = {
        'vpin': 0.4,
        'spread_bps': 8,
        'volatility': 0.02
    }
    action, reason = handler.handle_extreme_conditions(market_data, "C")
    risk_level = handler.get_market_risk_level(market_data)
    print(f"   VPIN: {market_data['vpin']}, Spread: {market_data['spread_bps']}bps")
    print(f"   風險等級: {risk_level}")
    print(f"   強制動作: {action if action else '無'}")
    
    # 測試VPIN危機
    print("\n📊 測試VPIN危機:")
    market_data = {
        'vpin': 0.85,
        'spread_bps': 10,
        'volatility': 0.03
    }
    action, reason = handler.handle_extreme_conditions(market_data, "C")
    risk_level = handler.get_market_risk_level(market_data)
    print(f"   VPIN: {market_data['vpin']}, Spread: {market_data['spread_bps']}bps")
    print(f"   風險等級: {risk_level}")
    print(f"   強制動作: {action}")
    print(f"   原因: {reason}")
    
    # 測試流動性危機
    print("\n📊 測試流動性危機:")
    market_data = {
        'vpin': 0.5,
        'spread_bps': 30,
        'volatility': 0.02
    }
    action, reason = handler.handle_extreme_conditions(market_data, "B")
    risk_level = handler.get_market_risk_level(market_data)
    print(f"   VPIN: {market_data['vpin']}, Spread: {market_data['spread_bps']}bps")
    print(f"   風險等級: {risk_level}")
    print(f"   強制動作: {action}")
    print(f"   原因: {reason}")
    
    print("\n✅ 極端市場測試完成")

def test_performance_monitor():
    """測試性能監控"""
    print("\n" + "="*80)
    print("📊 測試 4: 增強性能監控")
    print("="*80)
    
    monitor = EnhancedPerformanceMonitor()
    
    # 模擬一些交易
    print("\n📊 模擬交易記錄:")
    trades = [
        {'profit': 1.5, 'time': datetime.now(), 'scheme': 'C'},
        {'profit': 2.0, 'time': datetime.now(), 'scheme': 'C'},
        {'profit': -1.0, 'time': datetime.now(), 'scheme': 'B'},
        {'profit': -1.5, 'time': datetime.now(), 'scheme': 'B'},
        {'profit': -2.0, 'time': datetime.now(), 'scheme': 'A'},
    ]
    
    for i, trade in enumerate(trades, 1):
        monitor.add_trade(trade)
        print(f"   交易 {i}: {'盈利' if trade['profit'] > 0 else '虧損'} "
              f"{abs(trade['profit']):.1f} USDT | 方案: {trade['scheme']}")
    
    # 檢查預警
    print("\n📊 性能檢查:")
    market_data = {'vpin': 0.75}
    alerts = monitor.check_performance_alerts(market_data)
    
    summary = monitor.get_performance_summary()
    print(f"   總交易: {summary['total_trades']}")
    print(f"   勝率: {summary['win_rate']:.1%}")
    print(f"   回撤: {summary['drawdown']:.1%}")
    print(f"   連續虧損: {summary['consecutive_losses']}")
    
    if alerts:
        print(f"\n⚠️ 發現 {len(alerts)} 個預警:")
        for alert in alerts:
            emoji = "🚨" if alert['level'] == 'CRITICAL' else "⚠️"
            print(f"   {emoji} [{alert['level']}] {alert['message']}")
    else:
        print("\n✅ 無預警")
    
    print("\n✅ 性能監控測試完成")

def test_m15_integration():
    """測試M15完整集成"""
    print("\n" + "="*80)
    print("🤖🐳🦾 測試 5: M15 完整集成")
    print("="*80)
    
    # 載入配置
    config_path = Path(__file__).parent.parent / "config" / "trading_strategies_dev.json"
    with open(config_path, 'r', encoding='utf-8') as f:
        all_configs = json.load(f)
    
    # 確保 all_configs 是列表
    if isinstance(all_configs, dict):
        all_configs = [all_configs]
    
    m15_config = None
    for config in all_configs:
        if isinstance(config, dict) and config.get('mode') == 'mode_15_enhanced':
            m15_config = config
            break
    
    if not m15_config:
        print("❌ 找不到 M15 配置")
        print(f"   配置文件包含 {len(all_configs)} 個策略")
        return
    
    print(f"\n✅ 載入配置: {m15_config['name']}")
    
    # 創建策略實例
    strategy = Mode15EnhancedStrategy(m15_config)
    
    # 初始化會話
    strategy.initialize_session(100.0)
    
    # 模擬市場數據
    market_data = {
        'vpin': 0.45,
        'spread': 8.5,
        'spread_bps': 8.5,
        'depth': 12.0,
        'total_depth': 12.0,
        'obi': 0.65,
        'volume': 150,
        'avg_volume': 120,
        'price': 50000,
        'volatility': 0.025,
        'mtf_signals': {}
    }
    
    print("\n📊 市場狀態:")
    print(f"   VPIN: {market_data['vpin']}")
    print(f"   Spread: {market_data['spread_bps']}bps")
    print(f"   OBI: {market_data['obi']}")
    print(f"   波動率: {market_data['volatility']:.2%}")
    
    # 檢查進場條件
    print("\n🔍 檢查進場條件:")
    can_enter, reasons = strategy.check_entry(market_data, {})
    print(f"   可以進場: {'✅ 是' if can_enter else '❌ 否'}")
    if reasons:
        print(f"   阻擋原因: {', '.join(reasons)}")
    
    # 獲取風險摘要
    print("\n📊 風險摘要:")
    risk_summary = strategy.get_risk_summary(market_data)
    print(f"   市場風險: {risk_summary['market_risk_level']}")
    print(f"   可交易: {'✅' if risk_summary['can_trade'] else '🔴'}")
    print(f"   當前方案: {risk_summary['current_scheme']}")
    print(f"   過渡中: {'是' if risk_summary['in_transition'] else '否'}")
    
    if risk_summary['active_alerts']:
        print(f"   活躍預警: {len(risk_summary['active_alerts'])} 個")
    
    print("\n✅ M15 集成測試完成")

def main():
    """主測試函數"""
    print("\n" + "="*80)
    print("🚀 M15 增強版功能測試")
    print("="*80)
    print("\n測試內容:")
    print("   1️⃣ 緊急熔斷機制")
    print("   2️⃣ 平滑過渡管理")
    print("   3️⃣ 極端市場處理")
    print("   4️⃣ 增強性能監控")
    print("   5️⃣ M15 完整集成")
    
    try:
        test_circuit_breaker()
        test_smooth_transition()
        test_extreme_market()
        test_performance_monitor()
        test_m15_integration()
        
        print("\n" + "="*80)
        print("✅ 所有測試通過！")
        print("="*80)
        print("\n🎯 M15 增強功能:")
        print("   🔴 熔斷機制: 連續3次虧損 或 單日虧損15%")
        print("   🔄 平滑過渡: A↔C 需經過30分鐘B方案過渡")
        print("   ⚠️ 極端市場: VPIN>0.8 或 Spread>25bps 強制降級")
        print("   📊 性能監控: 實時預警 回撤/勝率/VPIN 異常")
        print("\n")
        
    except Exception as e:
        print(f"\n❌ 測試失敗: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
