#!/usr/bin/env python3
"""
最佳策略分析 - 找出提高勝率與獲利的最佳條件組合
"""

import json
from pathlib import Path
from typing import Dict, List
from collections import defaultdict

def load_all_trades():
    """載入所有交易"""
    logs_dir = Path("/Users/akaihuangm1/Desktop/btn/logs")
    all_trades = []
    
    for json_file in logs_dir.rglob("trades_*.json"):
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)
            trades = data.get('trades', [])
            for trade in trades:
                if trade.get('status', '').startswith('CLOSED'):
                    all_trades.append(trade)
        except:
            continue
    
    return all_trades

def analyze_by_condition(trades: List[Dict], condition_fn, condition_name: str):
    """按條件分析交易"""
    matched = [t for t in trades if condition_fn(t)]
    if not matched:
        return None
    
    wins = [t for t in matched if t.get('net_pnl_usdt', 0) > 0]
    total_pnl = sum(t.get('net_pnl_usdt', 0) for t in matched)
    avg_pnl = total_pnl / len(matched)
    winrate = len(wins) / len(matched) * 100
    
    return {
        'condition': condition_name,
        'count': len(matched),
        'wins': len(wins),
        'losses': len(matched) - len(wins),
        'winrate': winrate,
        'total_pnl': total_pnl,
        'avg_pnl': avg_pnl
    }

def main():
    trades = load_all_trades()
    print(f"📊 載入 {len(trades)} 筆交易進行分析\n")
    
    results = []
    
    # ============================================================
    # 1. 按策略分析
    # ============================================================
    print("=" * 80)
    print("📈 1. 按策略類型分析")
    print("=" * 80)
    
    strategies = set(t.get('strategy', 'N/A') for t in trades)
    for strat in sorted(strategies):
        r = analyze_by_condition(trades, lambda t, s=strat: t.get('strategy') == s, f"策略={strat}")
        if r and r['count'] >= 3:
            results.append(r)
            icon = "✅" if r['winrate'] >= 55 and r['total_pnl'] > 0 else "⚠️" if r['winrate'] >= 50 else "❌"
            print(f"{icon} {strat}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f} | 平均 ${r['avg_pnl']:.2f}")
    
    # ============================================================
    # 2. 按機率區間分析
    # ============================================================
    print("\n" + "=" * 80)
    print("📈 2. 按策略機率區間分析")
    print("=" * 80)
    
    prob_ranges = [
        (0.65, 0.75, "65-75%"),
        (0.75, 0.85, "75-85%"),
        (0.85, 0.95, "85-95%"),
        (0.95, 1.01, "95-100%"),
    ]
    
    for low, high, name in prob_ranges:
        r = analyze_by_condition(
            trades, 
            lambda t, l=low, h=high: l <= t.get('probability', 0) < h,
            f"機率 {name}"
        )
        if r and r['count'] >= 3:
            results.append(r)
            icon = "✅" if r['winrate'] >= 55 and r['total_pnl'] > 0 else "⚠️" if r['winrate'] >= 50 else "❌"
            print(f"{icon} {name}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # ============================================================
    # 3. 按方向分析
    # ============================================================
    print("\n" + "=" * 80)
    print("📈 3. 按交易方向分析")
    print("=" * 80)
    
    for direction in ['LONG', 'SHORT']:
        r = analyze_by_condition(trades, lambda t, d=direction: t.get('direction') == d, f"方向={direction}")
        if r:
            results.append(r)
            icon = "✅" if r['winrate'] >= 55 and r['total_pnl'] > 0 else "❌"
            print(f"{icon} {direction}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # ============================================================
    # 4. 按 OBI 區間分析
    # ============================================================
    print("\n" + "=" * 80)
    print("📈 4. 按 OBI 區間分析")
    print("=" * 80)
    
    obi_ranges = [
        (-1.0, -0.3, "強賣壓 (<-0.3)"),
        (-0.3, 0.0, "弱賣壓 (-0.3~0)"),
        (0.0, 0.3, "弱買壓 (0~0.3)"),
        (0.3, 0.7, "中買壓 (0.3~0.7)"),
        (0.7, 1.1, "強買壓 (>0.7)"),
    ]
    
    for low, high, name in obi_ranges:
        r = analyze_by_condition(
            trades,
            lambda t, l=low, h=high: l <= t.get('obi', 0) < h,
            f"OBI {name}"
        )
        if r and r['count'] >= 3:
            results.append(r)
            icon = "✅" if r['winrate'] >= 55 and r['total_pnl'] > 0 else "⚠️" if r['winrate'] >= 50 else "❌"
            print(f"{icon} {name}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # ============================================================
    # 5. 按觀察策略機率分析
    # ============================================================
    print("\n" + "=" * 80)
    print("📈 5. 按觀察策略機率分析 (FAKEOUT, CONSOLIDATION_SHAKE 等)")
    print("=" * 80)
    
    observe_strategies = ['FAKEOUT', 'CONSOLIDATION_SHAKE', 'SLOW_BLEED', 'SPOOFING', 'WHIPSAW']
    
    def get_max_observe(t):
        probs = t.get('strategy_probs', {})
        return max((probs.get(s, 0) for s in observe_strategies), default=0)
    
    observe_ranges = [
        (0, 0.5, "觀察<50%"),
        (0.5, 0.75, "觀察50-75%"),
        (0.75, 0.9, "觀察75-90%"),
        (0.9, 1.1, "觀察>=90%"),
    ]
    
    for low, high, name in observe_ranges:
        r = analyze_by_condition(
            trades,
            lambda t, l=low, h=high: l <= get_max_observe(t) < h,
            name
        )
        if r and r['count'] >= 3:
            results.append(r)
            icon = "✅" if r['winrate'] >= 55 and r['total_pnl'] > 0 else "⚠️" if r['winrate'] >= 50 else "❌"
            print(f"{icon} {name}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # ============================================================
    # 6. 按數據完整度分析
    # ============================================================
    print("\n" + "=" * 80)
    print("📈 6. 按策略機率數據完整度分析")
    print("=" * 80)
    
    def count_strategies(t):
        probs = t.get('strategy_probs', {})
        return len([p for p in probs.values() if p > 0.02])
    
    data_quality = [
        (0, 3, "數據不完整 (1-2策略)"),
        (3, 6, "數據一般 (3-5策略)"),
        (6, 30, "數據完整 (6+策略)"),
    ]
    
    for low, high, name in data_quality:
        r = analyze_by_condition(
            trades,
            lambda t, l=low, h=high: l <= count_strategies(t) < h,
            name
        )
        if r and r['count'] >= 3:
            results.append(r)
            icon = "✅" if r['winrate'] >= 55 and r['total_pnl'] > 0 else "⚠️" if r['winrate'] >= 50 else "❌"
            print(f"{icon} {name}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # ============================================================
    # 7. 組合條件分析 - 找最佳組合
    # ============================================================
    print("\n" + "=" * 80)
    print("🎯 7. 組合條件分析 - 尋找最佳交易條件")
    print("=" * 80)
    
    # 組合 1: ACCUMULATION + 高機率 + OBI 正向
    r = analyze_by_condition(
        trades,
        lambda t: (t.get('strategy') == 'ACCUMULATION' and 
                   t.get('probability', 0) >= 0.85 and
                   t.get('obi', 0) >= 0.3),
        "ACCUMULATION + 機率>=85% + OBI>=0.3"
    )
    if r and r['count'] >= 2:
        icon = "✅" if r['winrate'] >= 55 else "❌"
        print(f"{icon} {r['condition']}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # 組合 2: ACCUMULATION + 低觀察策略機率
    r = analyze_by_condition(
        trades,
        lambda t: (t.get('strategy') == 'ACCUMULATION' and 
                   get_max_observe(t) < 0.75),
        "ACCUMULATION + 觀察策略<75%"
    )
    if r and r['count'] >= 2:
        icon = "✅" if r['winrate'] >= 55 else "❌"
        print(f"{icon} {r['condition']}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # 組合 3: 完整數據 + 高機率
    r = analyze_by_condition(
        trades,
        lambda t: (count_strategies(t) >= 6 and t.get('probability', 0) >= 0.75),
        "完整數據 + 機率>=75%"
    )
    if r and r['count'] >= 2:
        icon = "✅" if r['winrate'] >= 55 else "❌"
        print(f"{icon} {r['condition']}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # 組合 4: 只做 LONG
    r = analyze_by_condition(
        trades,
        lambda t: t.get('direction') == 'LONG' and t.get('probability', 0) >= 0.85,
        "只做 LONG + 機率>=85%"
    )
    if r and r['count'] >= 2:
        icon = "✅" if r['winrate'] >= 55 else "❌"
        print(f"{icon} {r['condition']}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # 組合 5: 不完整數據但高機率
    r = analyze_by_condition(
        trades,
        lambda t: (count_strategies(t) <= 2 and t.get('probability', 0) >= 0.85),
        "不完整數據 + 機率>=85%"
    )
    if r and r['count'] >= 2:
        icon = "✅" if r['winrate'] >= 55 else "❌"
        print(f"{icon} {r['condition']}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # 組合 6: 完整數據 + 低觀察
    r = analyze_by_condition(
        trades,
        lambda t: (count_strategies(t) >= 6 and get_max_observe(t) < 0.5),
        "完整數據 + 觀察<50%"
    )
    if r and r['count'] >= 2:
        icon = "✅" if r['winrate'] >= 55 else "❌"
        print(f"{icon} {r['condition']}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # 組合 7: DISTRIBUTION + 完整數據
    r = analyze_by_condition(
        trades,
        lambda t: (t.get('strategy') == 'DISTRIBUTION' and count_strategies(t) >= 6),
        "DISTRIBUTION + 完整數據"
    )
    if r and r['count'] >= 2:
        icon = "✅" if r['winrate'] >= 55 else "❌"
        print(f"{icon} {r['condition']}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # 組合 8: DISTRIBUTION + 不完整數據
    r = analyze_by_condition(
        trades,
        lambda t: (t.get('strategy') == 'DISTRIBUTION' and count_strategies(t) <= 2),
        "DISTRIBUTION + 不完整數據"
    )
    if r and r['count'] >= 2:
        icon = "✅" if r['winrate'] >= 55 else "❌"
        print(f"{icon} {r['condition']}: {r['count']}筆 | 勝率 {r['winrate']:.1f}% | PnL ${r['total_pnl']:.2f}")
    
    # ============================================================
    # 8. 最佳策略建議
    # ============================================================
    print("\n" + "=" * 80)
    print("💡 最佳策略建議")
    print("=" * 80)
    
    # 找出最佳條件
    good_results = [r for r in results if r['winrate'] >= 55 and r['count'] >= 5]
    good_results.sort(key=lambda x: (x['winrate'], x['total_pnl']), reverse=True)
    
    if good_results:
        print("\n🏆 高勝率條件 (勝率>=55%, 樣本>=5):")
        for r in good_results[:5]:
            print(f"   ✅ {r['condition']}: {r['winrate']:.1f}% 勝率, ${r['total_pnl']:.2f} PnL, {r['count']}筆")
    
    # 找出最差條件
    bad_results = [r for r in results if r['winrate'] < 45 and r['count'] >= 5]
    bad_results.sort(key=lambda x: x['winrate'])
    
    if bad_results:
        print("\n⛔ 應避免的條件 (勝率<45%, 樣本>=5):")
        for r in bad_results[:5]:
            print(f"   ❌ {r['condition']}: {r['winrate']:.1f}% 勝率, ${r['total_pnl']:.2f} PnL, {r['count']}筆")

if __name__ == "__main__":
    main()
