"""
測試增強版 VPIN 閾值系統
===========================

測試功能：
1. 動態 VPIN 閾值調整
2. 市場狀態識別
3. 分層 VPIN 過濾機制
4. 策略特定 VPIN 配置
"""

import sys
from pathlib import Path

# 添加專案根目錄到 Python 路徑
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.strategy.strategy_manager import TradingStrategy, StrategyManager


def test_dynamic_vpin_threshold():
    """測試動態 VPIN 閾值調整"""
    print("\n" + "="*80)
    print("測試 1: 動態 VPIN 閾值調整")
    print("="*80)
    
    # 創建基礎策略（閾值 0.5）
    config = {
        'name': 'Test Strategy',
        'emoji': '🧪',
        'description': 'Test',
        'leverage': 3,
        'position_size': 0.3,
        'vpin_safe_threshold': 0.5,
        'risk_control': {}
    }
    strategy = TradingStrategy(config)
    
    # 測試場景 1: 平靜市場（無調整）
    market_data_calm = {
        'obi_velocity': 0.2,
        'spread_bps': 3,
        'vpin': 0.3
    }
    threshold = strategy.get_dynamic_vpin_threshold(market_data_calm)
    print(f"\n場景 1: 平靜市場")
    print(f"  OBI Velocity: {market_data_calm['obi_velocity']}")
    print(f"  Spread: {market_data_calm['spread_bps']} bps")
    print(f"  基礎閾值: 0.5")
    print(f"  動態閾值: {threshold:.3f} ✅")
    assert threshold == 0.5, "平靜市場應維持原閾值"
    
    # 測試場景 2: 快速變化市場（降低閾值）
    market_data_fast = {
        'obi_velocity': 1.5,
        'spread_bps': 3,
        'vpin': 0.4
    }
    threshold = strategy.get_dynamic_vpin_threshold(market_data_fast)
    print(f"\n場景 2: 快速變化市場")
    print(f"  OBI Velocity: {market_data_fast['obi_velocity']}")
    print(f"  Spread: {market_data_fast['spread_bps']} bps")
    print(f"  基礎閾值: 0.5")
    print(f"  動態閾值: {threshold:.3f} ✅ (降低 30%)")
    assert threshold == 0.35, "快速變化應降低閾值到 0.35"
    
    # 測試場景 3: 流動性差市場（降低閾值）
    market_data_illiquid = {
        'obi_velocity': 0.3,
        'spread_bps': 15,
        'vpin': 0.35
    }
    threshold = strategy.get_dynamic_vpin_threshold(market_data_illiquid)
    print(f"\n場景 3: 流動性差市場")
    print(f"  OBI Velocity: {market_data_illiquid['obi_velocity']}")
    print(f"  Spread: {market_data_illiquid['spread_bps']} bps")
    print(f"  基礎閾值: 0.5")
    print(f"  動態閾值: {threshold:.3f} ✅ (降低 20%)")
    assert threshold == 0.40, "流動性差應降低閾值到 0.40"
    
    # 測試場景 4: 極端市場（多重因素）
    market_data_extreme = {
        'obi_velocity': 2.0,
        'spread_bps': 20,
        'vpin': 0.6
    }
    threshold = strategy.get_dynamic_vpin_threshold(market_data_extreme)
    print(f"\n場景 4: 極端市場")
    print(f"  OBI Velocity: {market_data_extreme['obi_velocity']}")
    print(f"  Spread: {market_data_extreme['spread_bps']} bps")
    print(f"  基礎閾值: 0.5")
    print(f"  動態閾值: {threshold:.3f} ✅ (降低至最低限 0.3)")
    assert threshold == 0.3, "極端市場應降至最低閾值 0.3"
    
    print("\n✅ 所有動態閾值測試通過!")


def test_market_state_detection():
    """測試市場狀態識別"""
    print("\n" + "="*80)
    print("測試 2: 市場狀態識別")
    print("="*80)
    
    config = {
        'name': 'Test Strategy',
        'emoji': '🧪',
        'description': 'Test',
        'leverage': 3,
        'position_size': 0.3,
        'vpin_safe_threshold': 0.5,
        'risk_control': {}
    }
    strategy = TradingStrategy(config)
    
    # 場景 1: 平靜 CALM
    market_calm = {'vpin': 0.25, 'obi_velocity': 0.3}
    state = strategy.get_market_state(market_calm)
    print(f"\n場景 1: VPIN={market_calm['vpin']}, Velocity={market_calm['obi_velocity']}")
    print(f"  市場狀態: {state} ✅")
    assert state == "CALM"
    
    # 場景 2: 正常 NORMAL
    market_normal = {'vpin': 0.4, 'obi_velocity': 0.7}
    state = strategy.get_market_state(market_normal)
    print(f"\n場景 2: VPIN={market_normal['vpin']}, Velocity={market_normal['obi_velocity']}")
    print(f"  市場狀態: {state} ✅")
    assert state == "NORMAL"
    
    # 場景 3: 波動 VOLATILE
    market_volatile = {'vpin': 0.6, 'obi_velocity': 1.5}
    state = strategy.get_market_state(market_volatile)
    print(f"\n場景 3: VPIN={market_volatile['vpin']}, Velocity={market_volatile['obi_velocity']}")
    print(f"  市場狀態: {state} ✅")
    assert state == "VOLATILE"
    
    # 場景 4: 極端 EXTREME
    market_extreme = {'vpin': 0.8, 'obi_velocity': 2.5}
    state = strategy.get_market_state(market_extreme)
    print(f"\n場景 4: VPIN={market_extreme['vpin']}, Velocity={market_extreme['obi_velocity']}")
    print(f"  市場狀態: {state} ✅")
    assert state == "EXTREME"
    
    print("\n✅ 所有市場狀態測試通過!")


def test_enhanced_vpin_filter():
    """測試增強版 VPIN 過濾器"""
    print("\n" + "="*80)
    print("測試 3: 增強版 VPIN 過濾器（分層決策）")
    print("="*80)
    
    config = {
        'name': 'Test Strategy',
        'emoji': '🧪',
        'description': 'Test',
        'leverage': 3,
        'position_size': 0.3,
        'vpin_safe_threshold': 0.5,
        'risk_control': {}
    }
    strategy = TradingStrategy(config)
    
    # 層級 1: VPIN < 0.3，安全
    market_safe = {'vpin': 0.25, 'obi': 0.5, 'obi_velocity': 0.3, 'spread_bps': 3}
    is_safe, reason = strategy.enhanced_vpin_filter(market_safe)
    print(f"\n層級 1: VPIN < 0.3 (安全)")
    print(f"  VPIN: {market_safe['vpin']}")
    print(f"  結果: {'✅ 通過' if is_safe else '❌ 阻擋'}")
    print(f"  原因: {reason if reason else '無'}")
    assert is_safe == True
    
    # 層級 2: 0.3 <= VPIN < 0.5，略高但可接受
    market_slight = {'vpin': 0.4, 'obi': 0.6, 'obi_velocity': 0.2, 'spread_bps': 3}
    is_safe, reason = strategy.enhanced_vpin_filter(market_slight)
    print(f"\n層級 2: 0.3 <= VPIN < 0.5 (略高)")
    print(f"  VPIN: {market_slight['vpin']}")
    print(f"  結果: {'✅ 通過' if is_safe else '❌ 阻擋'}")
    print(f"  原因: {reason if reason else '無'}")
    assert is_safe == True
    
    # 層級 3a: 0.5 <= VPIN < 0.7，強信號可通過
    market_high_strong = {'vpin': 0.6, 'obi': 0.85, 'obi_velocity': 0.5, 'spread_bps': 5}
    is_safe, reason = strategy.enhanced_vpin_filter(market_high_strong)
    print(f"\n層級 3a: 0.5 <= VPIN < 0.7 + 強信號")
    print(f"  VPIN: {market_high_strong['vpin']}, OBI: {market_high_strong['obi']}")
    print(f"  結果: {'✅ 通過' if is_safe else '❌ 阻擋'}")
    print(f"  原因: {reason if reason else '無'}")
    assert is_safe == True
    
    # 層級 3b: 0.5 <= VPIN < 0.7，弱信號被阻擋
    market_high_weak = {'vpin': 0.6, 'obi': 0.5, 'obi_velocity': 0.3, 'spread_bps': 5}
    is_safe, reason = strategy.enhanced_vpin_filter(market_high_weak)
    print(f"\n層級 3b: 0.5 <= VPIN < 0.7 + 弱信號")
    print(f"  VPIN: {market_high_weak['vpin']}, OBI: {market_high_weak['obi']}")
    print(f"  結果: {'✅ 通過' if is_safe else '❌ 阻擋'}")
    print(f"  原因: {reason}")
    assert is_safe == False
    
    # 層級 4: VPIN >= 0.7，危險，禁止交易
    market_danger = {'vpin': 0.75, 'obi': 0.9, 'obi_velocity': 1.0, 'spread_bps': 8}
    is_safe, reason = strategy.enhanced_vpin_filter(market_danger)
    print(f"\n層級 4: VPIN >= 0.7 (危險)")
    print(f"  VPIN: {market_danger['vpin']}")
    print(f"  結果: {'✅ 通過' if is_safe else '❌ 阻擋'}")
    print(f"  原因: {reason}")
    assert is_safe == False
    
    print("\n✅ 所有 VPIN 過濾器測試通過!")


def test_strategy_specific_thresholds():
    """測試策略特定 VPIN 閾值"""
    print("\n" + "="*80)
    print("測試 4: 策略特定 VPIN 配置")
    print("="*80)
    
    import json
    
    # 直接讀取配置文件檢查閾值
    with open('config/trading_strategies_dev.json', 'r', encoding='utf-8') as f:
        config_data = json.load(f)
    
    # 檢查各策略的 VPIN 閾值
    strategy_thresholds = {
        'mode_0_baseline': 1.0,  # 無風控
        'mode_1_vpin_only': 0.5,  # VPIN 策略
        'mode_3_full_control': 0.4,  # 完整風控（更保守）
        'mode_5_mean_reversion': 0.3,  # 均值回歸（最保守）
        'mode_14_dynamic_leverage': 0.35  # M14
    }
    
    for mode, expected_threshold in strategy_thresholds.items():
        if mode in config_data:
            strategy_config = config_data[mode]
            actual_threshold = strategy_config.get('vpin_safe_threshold')
            name = strategy_config.get('name', mode)
            emoji = strategy_config.get('emoji', '📊')
            
            print(f"\n{emoji} {name}")
            print(f"  預期閾值: {expected_threshold}")
            print(f"  實際閾值: {actual_threshold}")
            print(f"  {'✅ 符合' if abs(actual_threshold - expected_threshold) < 0.01 else '❌ 不符'}")
            assert abs(actual_threshold - expected_threshold) < 0.01, f"{mode} 閾值不符"
    
    print("\n✅ 所有策略特定配置測試通過!")


def test_extreme_market_protection():
    """測試極端市場保護機制"""
    print("\n" + "="*80)
    print("測試 5: 極端市場保護機制")
    print("="*80)
    
    # 模擬日誌中的極端市場條件
    extreme_market = {
        'vpin': 0.977,  # 極高毒性
        'obi': 0.9843,  # 極度失衡
        'obi_velocity': 1.5075,  # 快速變化
        'signed_volume': 2.40,
        'spread_bps': 5,
        'total_depth': 14.32
    }
    
    # 測試各策略在極端市場的反應
    import json
    
    with open('config/trading_strategies_dev.json', 'r', encoding='utf-8') as f:
        config_data = json.load(f)
    
    test_modes = [
        ('mode_0_baseline', True),  # 無風控，應該通過
        ('mode_1_vpin_only', False),  # 有風控，應該阻擋
        ('mode_3_full_control', False),
        ('mode_5_mean_reversion', False),
        ('mode_14_dynamic_leverage', False)
    ]
    
    for mode, should_pass in test_modes:
        if mode not in config_data:
            continue
            
        strategy_config = config_data[mode]
        strategy_config['risk_control'] = {}  # 添加必要欄位
        strategy = TradingStrategy(strategy_config)
        
        # 檢查市場狀態
        market_state = strategy.get_market_state(extreme_market)
        
        # 檢查 VPIN 過濾
        is_safe, reason = strategy.enhanced_vpin_filter(extreme_market)
        
        print(f"\n{strategy.emoji} {strategy.name}")
        print(f"  VPIN 閾值: {strategy.vpin_safe_threshold}")
        print(f"  市場狀態: {market_state}")
        print(f"  是否安全: {'✅ 是' if is_safe else '❌ 否'}")
        print(f"  原因: {reason if reason else '無'}")
        
        # 驗證預期結果
        if should_pass:
            assert is_safe == True, f"{mode} 應在極端市場通過（無風控）"
        else:
            assert is_safe == False, f"{mode} 應在極端市場阻擋交易"
    
    print("\n✅ 極端市場保護測試通過!")


if __name__ == '__main__':
    print("\n" + "="*80)
    print("🧪 增強版 VPIN 閾值系統測試")
    print("="*80)
    
    try:
        test_dynamic_vpin_threshold()
        test_market_state_detection()
        test_enhanced_vpin_filter()
        test_strategy_specific_thresholds()
        test_extreme_market_protection()
        
        print("\n" + "="*80)
        print("🎉 所有測試通過！")
        print("="*80)
        print("\n主要改進：")
        print("  ✅ 動態 VPIN 閾值調整（根據市場波動性）")
        print("  ✅ 市場狀態識別（CALM/NORMAL/VOLATILE/EXTREME）")
        print("  ✅ 分層 VPIN 過濾機制（4個層級）")
        print("  ✅ 策略特定 VPIN 配置（差異化風控）")
        print("  ✅ 極端市場保護機制")
        print("\n效果：")
        print("  📈 在平靜市場正常交易")
        print("  ⚠️  在波動市場謹慎進場")
        print("  🛑 在極端市場禁止交易")
        print("  🎯 保守策略使用更嚴格限制")
        
    except AssertionError as e:
        print(f"\n❌ 測試失敗: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
