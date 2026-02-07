#!/usr/bin/env python3
"""
M14 增強版功能測試腳本
測試動態 VPIN、獲利了結和市場感知方案切換
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

# 添加項目根目錄到路徑
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.strategy.mode_14_enhanced import (
    EnhancedMode14Strategy,
    DynamicVPINAdapter,
    DynamicProfitTakingEngine
)


def test_dynamic_vpin():
    """測試動態 VPIN 閾值調整"""
    print("\n" + "="*60)
    print("測試 1: 動態 VPIN 閾值調整")
    print("="*60)
    
    adapter = DynamicVPINAdapter(base_threshold=0.75)
    
    # 測試場景
    scenarios = [
        {
            'name': '平靜市場',
            'data': {'vpin': 0.25, 'obi_velocity': 0.3, 'spread_bps': 3, 'volatility': 0.012, 'obi': 0.65},
            'expected': '接近基準值（0.75附近）'
        },
        {
            'name': '快速變化',
            'data': {'vpin': 0.45, 'obi_velocity': 1.8, 'spread_bps': 5, 'volatility': 0.025, 'obi': 0.72},
            'expected': '大幅降低（~0.45）'
        },
        {
            'name': '流動性差',
            'data': {'vpin': 0.52, 'obi_velocity': 0.6, 'spread_bps': 18, 'volatility': 0.018, 'obi': 0.55},
            'expected': '降低（~0.53）'
        },
        {
            'name': '高波動',
            'data': {'vpin': 0.62, 'obi_velocity': 0.8, 'spread_bps': 8, 'volatility': 0.045, 'obi': 0.68},
            'expected': '降低（~0.56）'
        },
        {
            'name': '極端市場',
            'data': {'vpin': 0.78, 'obi_velocity': 2.5, 'spread_bps': 25, 'volatility': 0.055, 'obi': 0.45},
            'expected': '最低（~0.32）'
        }
    ]
    
    for scenario in scenarios:
        dynamic_threshold = adapter.get_dynamic_threshold(scenario['data'])
        market_state = adapter.get_market_state(scenario['data'])
        is_safe, reason = adapter.enhanced_vpin_filter(scenario['data'])
        
        print(f"\n場景: {scenario['name']}")
        print(f"  VPIN: {scenario['data']['vpin']:.3f}")
        print(f"  OBI速度: {scenario['data']['obi_velocity']:.2f}")
        print(f"  點差: {scenario['data']['spread_bps']:.1f} bps")
        print(f"  波動率: {scenario['data']['volatility']:.3f}")
        print(f"  → 靜態閾值: 0.750")
        print(f"  → 動態閾值: {dynamic_threshold:.3f}")
        print(f"  → 市場狀態: {market_state}")
        print(f"  → 過濾結果: {'✅ 通過' if is_safe else '❌ 拒絕'}")
        if reason:
            print(f"     原因: {reason}")
        print(f"  預期: {scenario['expected']}")
    
    print("\n✅ 測試 1 完成")


def test_profit_taking():
    """測試獲利了結引擎"""
    print("\n" + "="*60)
    print("測試 2: 智能獲利了結")
    print("="*60)
    
    config = {
        'enabled': True
    }
    engine = DynamicProfitTakingEngine(config)
    
    # 測試場景
    scenarios = [
        {
            'name': '方案A - 達到目標',
            'position': {
                'unrealized_pnl_pct': 0.032,
                'entry_time': datetime.now() - timedelta(minutes=4),
                'entry_price': 45000,
                'leverage': 15
            },
            'market_data': {
                'vpin': 0.35,
                'volatility': 0.018
            },
            'scheme': 'A'
        },
        {
            'name': '方案B - 持倉過久',
            'position': {
                'unrealized_pnl_pct': 0.042,
                'entry_time': datetime.now() - timedelta(minutes=12),
                'entry_price': 45000,
                'leverage': 20
            },
            'market_data': {
                'vpin': 0.42,
                'volatility': 0.022
            },
            'scheme': 'B'
        },
        {
            'name': '方案C - VPIN過高',
            'position': {
                'unrealized_pnl_pct': 0.065,
                'entry_time': datetime.now() - timedelta(minutes=6),
                'entry_price': 45000,
                'leverage': 24
            },
            'market_data': {
                'vpin': 0.75,
                'volatility': 0.038
            },
            'scheme': 'C'
        },
        {
            'name': '方案B - 強制平倉',
            'position': {
                'unrealized_pnl_pct': 0.112,
                'entry_time': datetime.now() - timedelta(minutes=8),
                'entry_price': 45000,
                'leverage': 20
            },
            'market_data': {
                'vpin': 0.48,
                'volatility': 0.025
            },
            'scheme': 'B'
        },
        {
            'name': '方案A - 未達標',
            'position': {
                'unrealized_pnl_pct': 0.015,
                'entry_time': datetime.now() - timedelta(minutes=2),
                'entry_price': 45000,
                'leverage': 12
            },
            'market_data': {
                'vpin': 0.28,
                'volatility': 0.015
            },
            'scheme': 'A'
        }
    ]
    
    for scenario in scenarios:
        should_exit, reason, confidence = engine.should_take_profit(
            scenario['position'],
            scenario['market_data'],
            scenario['scheme']
        )
        
        hold_duration = (datetime.now() - scenario['position']['entry_time']).total_seconds() / 60
        
        print(f"\n場景: {scenario['name']}")
        print(f"  當前收益: {scenario['position']['unrealized_pnl_pct']:.2%}")
        print(f"  持倉時間: {hold_duration:.1f} 分鐘")
        print(f"  VPIN: {scenario['market_data']['vpin']:.3f}")
        print(f"  波動率: {scenario['market_data']['volatility']:.3f}")
        print(f"  → 決策: {'💰 平倉' if should_exit else '📊 持有'}")
        print(f"  → 置信度: {confidence:.2f}")
        if reason:
            print(f"  → 原因: {reason}")
    
    print("\n✅ 測試 2 完成")


def test_enhanced_entry():
    """測試增強版進場邏輯"""
    print("\n" + "="*60)
    print("測試 3: 增強版進場判斷")
    print("="*60)
    
    # 加載配置
    config_path = project_root / 'config' / 'trading_strategies_dev.json'
    with open(config_path) as f:
        config = json.load(f)
        m14_config = config['strategies']['mode_14_dynamic_leverage']
    
    strategy = EnhancedMode14Strategy(m14_config)
    
    # 測試場景
    scenarios = [
        {
            'name': '完美信號',
            'data': {
                'price': 45250,
                'vpin': 0.25,
                'obi': 0.75,
                'obi_velocity': 0.4,
                'spread': 6,
                'spread_bps': 6,
                'depth': 8,
                'volume': 2200,
                'avg_volume': 1800,
                'volatility': 0.015,
                'signal_quality': 0.85,
                'mtf_signals': {'1m': 0.82, '5m': 0.78, '15m': 0.75}
            }
        },
        {
            'name': '高VPIN但強信號',
            'data': {
                'price': 45250,
                'vpin': 0.62,
                'obi': 0.88,
                'obi_velocity': 1.2,
                'spread': 9,
                'spread_bps': 9,
                'depth': 5,
                'volume': 2500,
                'avg_volume': 1800,
                'volatility': 0.028,
                'signal_quality': 0.82,
                'mtf_signals': {'1m': 0.85, '5m': 0.82, '15m': 0.78}
            }
        },
        {
            'name': '極端市場',
            'data': {
                'price': 45250,
                'vpin': 0.82,
                'obi': 0.65,
                'obi_velocity': 2.8,
                'spread': 22,
                'spread_bps': 22,
                'depth': 2.5,
                'volume': 3200,
                'avg_volume': 1800,
                'volatility': 0.052,
                'signal_quality': 0.68,
                'mtf_signals': {'1m': 0.70, '5m': 0.65, '15m': 0.62}
            }
        },
        {
            'name': '條件不足',
            'data': {
                'price': 45250,
                'vpin': 0.48,
                'obi': 0.42,
                'obi_velocity': 0.8,
                'spread': 14,
                'spread_bps': 14,
                'depth': 3.5,
                'volume': 1650,
                'avg_volume': 1800,
                'volatility': 0.022,
                'signal_quality': 0.58,
                'mtf_signals': {'1m': 0.55, '5m': 0.52, '15m': 0.50}
            }
        }
    ]
    
    for scenario in scenarios:
        should_enter, reason = strategy.should_enter_trade(scenario['data'])
        dynamic_threshold = strategy.get_dynamic_vpin_threshold(scenario['data'])
        market_state = strategy.get_market_state(scenario['data'])
        
        print(f"\n場景: {scenario['name']}")
        print(f"  VPIN: {scenario['data']['vpin']:.3f} (動態閾值: {dynamic_threshold:.3f})")
        print(f"  市場狀態: {market_state}")
        print(f"  信號質量: {scenario['data']['signal_quality']:.2f}")
        print(f"  OBI: {scenario['data']['obi']:.2f} (速度: {scenario['data']['obi_velocity']:.2f})")
        print(f"  → 決策: {'✅ 進場' if should_enter else '❌ 拒絕'}")
        print(f"  → 原因: {reason}")
    
    print("\n✅ 測試 3 完成")


def test_scheme_switching():
    """測試市場感知的方案切換"""
    print("\n" + "="*60)
    print("測試 4: 市場感知方案切換")
    print("="*60)
    
    # 加載配置
    config_path = project_root / 'config' / 'trading_strategies_dev.json'
    with open(config_path) as f:
        config = json.load(f)
        m14_config = config['strategies']['mode_14_dynamic_leverage']
    
    strategy = EnhancedMode14Strategy(m14_config)
    
    # 模擬連續獲利（觸發升級條件）
    # 通過添加獲利交易記錄
    for i in range(8):
        strategy.strategy_selector.add_trade_result(
            profit=0.015,  # 1.5% 利潤
            entry_time=datetime.now() - timedelta(minutes=5*i)
        )
    
    print("\n當前狀態：")
    print(f"  方案: {strategy.strategy_selector.current_scheme}")
    print(f"  交易記錄數: {len(strategy.strategy_selector.trade_history)}")
    
    # 測試場景
    scenarios = [
        {
            'name': '正常市場 - 允許升級',
            'data': {
                'vpin': 0.35,
                'obi_velocity': 0.6,
                'spread_bps': 7,
                'volatility': 0.018
            }
        },
        {
            'name': '波動市場 - 暫緩升級',
            'data': {
                'vpin': 0.68,
                'obi_velocity': 1.8,
                'spread_bps': 12,
                'volatility': 0.038
            }
        },
        {
            'name': '極端市場 - 阻止升級',
            'data': {
                'vpin': 0.85,
                'obi_velocity': 2.5,
                'spread_bps': 20,
                'volatility': 0.055
            }
        }
    ]
    
    for scenario in scenarios:
        should_upgrade, reason = strategy.strategy_selector.should_upgrade_strategy(
            scenario['data']
        )
        market_state = strategy.vpin_adapter.get_market_state(scenario['data'])
        
        print(f"\n場景: {scenario['name']}")
        print(f"  VPIN: {scenario['data']['vpin']:.3f}")
        print(f"  市場狀態: {market_state}")
        print(f"  → 升級決策: {'✅ 允許' if should_upgrade else '❌ 阻止'}")
        print(f"  → 原因: {reason}")
    
    # 測試降級（市場毒性觸發）
    print("\n\n降級測試：")
    
    # 模擬高VPIN
    market_data_high_vpin = {
        'vpin': 0.88,
        'obi_velocity': 2.2,
        'spread_bps': 18,
        'volatility': 0.048
    }
    
    should_downgrade, reason = strategy.strategy_selector.should_downgrade_strategy(
        current_balance=1200,
        initial_balance=1000,
        market_data=market_data_high_vpin
    )
    
    print(f"高VPIN場景 (VPIN=0.88):")
    print(f"  → 降級決策: {'✅ 觸發' if should_downgrade else '❌ 未觸發'}")
    print(f"  → 原因: {reason}")
    
    print("\n✅ 測試 4 完成")


def main():
    """運行所有測試"""
    print("\n" + "="*60)
    print("M14 增強版功能測試")
    print("="*60)
    
    try:
        test_dynamic_vpin()
        test_profit_taking()
        test_enhanced_entry()
        test_scheme_switching()
        
        print("\n" + "="*60)
        print("✅ 所有測試完成")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ 測試失敗: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
