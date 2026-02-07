"""
測試 Mode 8, 9, 10 三種技術指標策略
"""

import sys
import numpy as np
from pathlib import Path

# 添加專案根目錄到路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy.strategy_manager import StrategyManager


def test_three_modes():
    """對比測試三種模式"""
    
    print("=" * 80)
    print("Mode 8, 9, 10 技術指標策略對比測試")
    print("=" * 80)
    
    # 初始化策略管理器
    manager = StrategyManager()
    
    # 獲取三個策略
    mode8 = manager.get_strategy('mode_8_technical_loose')
    mode9 = manager.get_strategy('mode_9_technical_strict')
    mode10 = manager.get_strategy('mode_10_technical_off')
    
    if not all([mode8, mode9, mode10]):
        print("❌ 策略未完全載入")
        return
    
    print("\n策略概覽:")
    print("┌─────────┬──────────────┬──────┬──────┬──────┬──────┬──────┐")
    print("│  Mode   │    名稱      │ 槓桿 │ 倉位 │ 止損 │ 止盈 │ 門檻 │")
    print("├─────────┼──────────────┼──────┼──────┼──────┼──────┼──────┤")
    
    for mode_key, strategy in [
        ('Mode 8', mode8),
        ('Mode 9', mode9),
        ('Mode 10', mode10)
    ]:
        tech_enabled = strategy.risk_control.get('technical_indicators', False)
        threshold = strategy.risk_control.get('min_indicator_agreement', 0) if tech_enabled else 'N/A'
        
        print(f"│ {mode_key:7} │ {strategy.name:12} │ {strategy.leverage}x   │ {strategy.position_size*100:4.0f}% │ {strategy.risk_control['stop_loss']*100:4.1f}% │ {strategy.risk_control['take_profit']*100:4.1f}% │ {threshold:4}  │")
    
    print("└─────────┴──────────────┴──────┴──────┴──────┴──────┴──────┘")
    
    # 測試情境
    scenarios = [
        {
            'name': '超賣反彈（技術指標有效）',
            'signal': {'direction': 'LONG', 'confidence': 0.7},
            'market': {
                'price': 89500,
                'vpin': 0.5,
                'spread_bps': 5,
                'total_depth': 10,
                'obi': -0.6,
                'obi_velocity': 0.1
            },
            'prepare': lambda s: simulate_oversold(s)
        },
        {
            'name': '強勢上漲（技術指標衝突）',
            'signal': {'direction': 'LONG', 'confidence': 0.8},
            'market': {
                'price': 91500,
                'vpin': 0.4,
                'spread_bps': 5,
                'total_depth': 12,
                'obi': 0.7,
                'obi_velocity': 0.2
            },
            'prepare': lambda s: simulate_uptrend(s)
        },
        {
            'name': '震盪市場（指標中性）',
            'signal': {'direction': 'LONG', 'confidence': 0.5},
            'market': {
                'price': 90500,
                'vpin': 0.6,
                'spread_bps': 8,
                'total_depth': 8,
                'obi': 0.2,
                'obi_velocity': 0.05
            },
            'prepare': lambda s: simulate_sideways(s)
        },
        {
            'name': 'VPIN 過高（基本風控）',
            'signal': {'direction': 'LONG', 'confidence': 0.8},
            'market': {
                'price': 90000,
                'vpin': 0.85,  # 超過所有門檻
                'spread_bps': 5,
                'total_depth': 10,
                'obi': 0.5,
                'obi_velocity': 0.1
            },
            'prepare': lambda s: simulate_uptrend(s)
        }
    ]
    
    for i, scenario in enumerate(scenarios, 1):
        print(f"\n{'=' * 80}")
        print(f"情境 {i}: {scenario['name']}")
        print(f"{'=' * 80}")
        
        # 準備價格歷史
        for strategy in [mode8, mode9, mode10]:
            scenario['prepare'](strategy)
        
        market_data = scenario['market']
        signal = scenario['signal']
        
        print(f"\n市場狀態:")
        print(f"  價格: {market_data['price']}")
        print(f"  VPIN: {market_data['vpin']:.2f}")
        print(f"  Spread: {market_data['spread_bps']:.1f} bps")
        print(f"  Depth: {market_data['total_depth']:.1f} BTC")
        print(f"  OBI: {market_data['obi']:.2f}")
        print(f"\n信號: {signal['direction']} (信心度 {signal['confidence']:.0%})")
        
        # 測試三個模式
        results = []
        for mode_name, strategy in [('Mode 8', mode8), ('Mode 9', mode9), ('Mode 10', mode10)]:
            can_enter, reasons = strategy.check_entry(market_data, signal)
            results.append({
                'mode': mode_name,
                'pass': can_enter,
                'reasons': reasons
            })
        
        print(f"\n結果對比:")
        print("┌─────────┬────────┬─────────────────────────────────────────────────────┐")
        print("│  Mode   │ 結果   │ 原因                                                │")
        print("├─────────┼────────┼─────────────────────────────────────────────────────┤")
        
        for result in results:
            status = "✅ 通過" if result['pass'] else "❌ 阻擋"
            mode_name = result['mode']
            
            if result['reasons']:
                # 只顯示第一個原因，太長會換行
                reason = result['reasons'][0][:45] + '...' if len(result['reasons'][0]) > 45 else result['reasons'][0]
            else:
                reason = "-"
            
            print(f"│ {mode_name:7} │ {status:6} │ {reason:51} │")
        
        print("└─────────┴────────┴─────────────────────────────────────────────────────┘")
    
    print(f"\n{'=' * 80}")
    print("總結:")
    print("=" * 80)
    print("\n📊 Mode 8 (寬鬆):")
    print("  • 技術指標門檻 = 1 票")
    print("  • 適合：想要技術指標輔助但不過度限制")
    print("  • 特點：大多數情況能通過，只攔截明顯不利的情況")
    
    print("\n📈 Mode 9 (嚴格):")
    print("  • 技術指標門檻 = 3 票")
    print("  • 適合：只在技術指標強烈同意時才交易")
    print("  • 特點：交易次數少，但技術指標信心度高")
    
    print("\n📉 Mode 10 (關閉):")
    print("  • 完全關閉技術指標檢查")
    print("  • 適合：避免技術指標與 OBI 邏輯衝突")
    print("  • 特點：純 VPIN + 流動性風控，類似 Mode 3")
    
    print("\n💡 建議:")
    print("  1. 實盤同時運行三個模式，對比表現")
    print("  2. Mode 8 可能交易次數最多")
    print("  3. Mode 9 可能交易次數最少")
    print("  4. Mode 10 交易次數介於中間，邏輯最一致")
    print("=" * 80)


def simulate_oversold(strategy):
    """模擬超賣情況（下跌後）"""
    strategy.price_history = []
    strategy.high_history = []
    strategy.low_history = []
    
    for i in range(60):
        price = 92000 - i * 35 + np.random.uniform(-20, 20)
        strategy.update_price_history({'price': price, 'spread_bps': 5})


def simulate_uptrend(strategy):
    """模擬上漲趨勢"""
    strategy.price_history = []
    strategy.high_history = []
    strategy.low_history = []
    
    for i in range(60):
        price = 90000 + i * 25 + np.random.uniform(-20, 20)
        strategy.update_price_history({'price': price, 'spread_bps': 5})


def simulate_sideways(strategy):
    """模擬震盪市場"""
    strategy.price_history = []
    strategy.high_history = []
    strategy.low_history = []
    
    for i in range(60):
        price = 90500 + 300 * np.sin(i * 0.2) + np.random.uniform(-50, 50)
        strategy.update_price_history({'price': price, 'spread_bps': 8})


if __name__ == "__main__":
    test_three_modes()
