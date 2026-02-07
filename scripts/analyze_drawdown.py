#!/usr/bin/env python3
"""
分析：如何在進場時預測「回撤是否會超過 8%」
"""

import json
from pathlib import Path
from collections import defaultdict

def load_all_trades():
    logs_dir = Path("/Users/akaihuangm1/Desktop/btn/logs")
    all_trades = []
    for json_file in logs_dir.rglob("trades_*.json"):
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)
            for trade in data.get('trades', []):
                if trade.get('status', '').startswith('CLOSED'):
                    all_trades.append(trade)
        except:
            continue
    return all_trades

def main():
    trades = load_all_trades()
    
    # 分類
    low_dd = [t for t in trades if abs(t.get('max_drawdown_pct', 100)) < 8]
    high_dd = [t for t in trades if abs(t.get('max_drawdown_pct', 0)) >= 8]
    
    print("=" * 80)
    print("🔬 分析：低回撤 vs 高回撤交易的差異")
    print("=" * 80)
    
    print(f"\n低回撤 (<8%): {len(low_dd)} 筆, PnL ${sum(t.get('net_pnl_usdt', 0) for t in low_dd):.2f}")
    print(f"高回撤 (>=8%): {len(high_dd)} 筆, PnL ${sum(t.get('net_pnl_usdt', 0) for t in high_dd):.2f}")
    
    # 比較各種進場特徵
    features = ['probability', 'obi', 'strategy', 'direction']
    
    print(f"\n" + "=" * 80)
    print("📊 進場特徵比較 (可用於預測回撤)")
    print("=" * 80)
    
    for feature in features:
        print(f"\n--- {feature} ---")
        
        # 低回撤組
        low_vals = [t.get(feature, 'N/A') for t in low_dd]
        high_vals = [t.get(feature, 'N/A') for t in high_dd]
        
        if feature in ['probability', 'obi']:
            low_numeric = [v for v in low_vals if isinstance(v, (int, float))]
            high_numeric = [v for v in high_vals if isinstance(v, (int, float))]
            
            if low_numeric and high_numeric:
                print(f"   低回撤組: 平均 {sum(low_numeric)/len(low_numeric):.3f}")
                print(f"   高回撤組: 平均 {sum(high_numeric)/len(high_numeric):.3f}")
        else:
            from collections import Counter
            low_counter = Counter(low_vals)
            high_counter = Counter(high_vals)
            print(f"   低回撤組: {dict(low_counter)}")
            print(f"   高回撤組: {dict(high_counter)}")
    
    # 時段分析
    print(f"\n--- 時段 ---")
    low_hours = []
    high_hours = []
    for t in low_dd:
        ts = t.get('timestamp', '')
        if 'T' in ts:
            low_hours.append(int(ts.split('T')[1][:2]))
    for t in high_dd:
        ts = t.get('timestamp', '')
        if 'T' in ts:
            high_hours.append(int(ts.split('T')[1][:2]))
    
    from collections import Counter
    print(f"   低回撤組時段: {dict(sorted(Counter(low_hours).items()))}")
    print(f"   高回撤組時段: {dict(sorted(Counter(high_hours).items()))}")
    
    # 高回撤交易詳細列表
    print(f"\n" + "=" * 80)
    print("📋 高回撤交易詳情 (這些是虧損來源)")
    print("=" * 80)
    
    for t in sorted(high_dd, key=lambda x: x.get('max_drawdown_pct', 0)):
        ts = t.get('timestamp', '')[:16]
        strategy = t.get('strategy', '')
        prob = t.get('probability', 0)
        obi = t.get('obi', 0)
        max_p = t.get('max_profit_pct', 0)
        max_dd = t.get('max_drawdown_pct', 0)
        pnl = t.get('net_pnl_usdt', 0)
        
        icon = "✅" if pnl > 0 else "❌"
        print(f"{icon} {ts} | {strategy} {prob:.0%} OBI:{obi:.2f} | +{max_p:.1f}%/{max_dd:.1f}% | ${pnl:+.2f}")
    
    # 找出可預測的特徵
    print(f"\n" + "=" * 80)
    print("🎯 可行的預測方法")
    print("=" * 80)
    
    # 測試不同條件能否預測高回撤
    tests = [
        ("OBI > 0.7 (買壓不足)", lambda t: t.get('obi', 0) > 0.7),
        ("OBI < 0.3 (賣壓過大)", lambda t: t.get('obi', 1) < 0.3),
        ("機率 > 90% (過度自信)", lambda t: t.get('probability', 0) > 0.90),
        ("機率 100% (極端信號)", lambda t: t.get('probability', 0) >= 1.0),
        ("凌晨 1-6 點", lambda t: 1 <= int(t.get('timestamp', 'T00').split('T')[1][:2]) <= 6 if 'T' in t.get('timestamp', '') else False),
        ("晚上 20-23 點", lambda t: 20 <= int(t.get('timestamp', 'T00').split('T')[1][:2]) <= 23 if 'T' in t.get('timestamp', '') else False),
    ]
    
    print(f"\n{'測試條件':<25} {'高回撤率':<12} {'避免高回撤':<15}")
    print("-" * 55)
    
    for name, test_func in tests:
        matched = [t for t in trades if test_func(t)]
        if len(matched) >= 3:
            high_dd_in_matched = len([t for t in matched if abs(t.get('max_drawdown_pct', 0)) >= 8])
            high_dd_rate = high_dd_in_matched / len(matched) * 100
            
            # 如果排除這些交易
            remaining = [t for t in trades if not test_func(t)]
            remaining_high_dd = len([t for t in remaining if abs(t.get('max_drawdown_pct', 0)) >= 8])
            remaining_high_dd_rate = remaining_high_dd / len(remaining) * 100 if remaining else 0
            
            icon = "⚠️" if high_dd_rate > 50 else "✅"
            print(f"{icon} {name:<23} {high_dd_rate:>5.0f}% ({high_dd_in_matched}/{len(matched)}) 排除後{remaining_high_dd_rate:.0f}%")
    
    # 結論
    print(f"\n" + "=" * 80)
    print("💡 v6.0 建議策略")
    print("=" * 80)
    
    print(f"""
    🎯 核心策略: 避免高回撤交易
    
    回撤 < 8% 的交易: +$51.23, 84% 勝率 ✅
    回撤 >= 8% 的交易: -$326.30, 36% 勝率 ❌
    
    可能的預測方法:
    1. 機率 100% 時不交易 (可能是假信號)
    2. 特定時段謹慎 (凌晨、深夜)
    3. 設置更緊的早期止損
    
    但關鍵問題是: 我們無法在進場時預測回撤
    
    💡 更實際的方法:
    1. 進場後 2 分鐘內如果跌超過 5%，立刻止損
    2. 不要等到 12% 止損，太晚了
    3. 快速止損，保留資金做下一筆
    """)

if __name__ == "__main__":
    main()
