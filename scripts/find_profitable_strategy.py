#!/usr/bin/env python3
"""
終極分析：找出能賺錢的策略組合
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
    
    print("=" * 80)
    print("🔍 終極分析：找出能賺錢的策略")
    print("=" * 80)
    print(f"\n總交易數: {len(trades)}")
    
    # ============================================================
    # 1. 按各種維度分析
    # ============================================================
    
    # 時段分析
    print(f"\n" + "=" * 80)
    print("📊 時段分析 (哪個時段最賺錢?)")
    print("=" * 80)
    
    hour_stats = defaultdict(lambda: {'trades': [], 'pnl': 0, 'wins': 0})
    for t in trades:
        ts = t.get('timestamp', '')
        if 'T' in ts:
            hour = int(ts.split('T')[1][:2])
            hour_stats[hour]['trades'].append(t)
            hour_stats[hour]['pnl'] += t.get('net_pnl_usdt', 0)
            if t.get('net_pnl_usdt', 0) > 0:
                hour_stats[hour]['wins'] += 1
    
    print(f"\n{'時段':<8} {'筆數':<6} {'PnL':<12} {'勝率':<8} {'平均':<10}")
    print("-" * 50)
    for hour in sorted(hour_stats.keys()):
        stats = hour_stats[hour]
        count = len(stats['trades'])
        winrate = stats['wins'] / count * 100 if count > 0 else 0
        avg = stats['pnl'] / count if count > 0 else 0
        icon = "✅" if stats['pnl'] > 0 else "❌"
        print(f"{icon} {hour:02d}:00   {count:<6} ${stats['pnl']:<+10.2f} {winrate:<7.0f}% ${avg:<+.2f}")
    
    # 找出獲利時段
    profitable_hours = [h for h, s in hour_stats.items() if s['pnl'] > 0]
    print(f"\n💰 獲利時段: {sorted(profitable_hours)}")
    
    # 策略分析  
    print(f"\n" + "=" * 80)
    print("📊 策略分析")
    print("=" * 80)
    
    strategy_stats = defaultdict(lambda: {'trades': [], 'pnl': 0, 'wins': 0})
    for t in trades:
        strategy = t.get('strategy', 'UNKNOWN')
        strategy_stats[strategy]['trades'].append(t)
        strategy_stats[strategy]['pnl'] += t.get('net_pnl_usdt', 0)
        if t.get('net_pnl_usdt', 0) > 0:
            strategy_stats[strategy]['wins'] += 1
    
    print(f"\n{'策略':<20} {'筆數':<6} {'PnL':<12} {'勝率':<8}")
    print("-" * 50)
    for strategy in sorted(strategy_stats.keys(), key=lambda x: strategy_stats[x]['pnl'], reverse=True):
        stats = strategy_stats[strategy]
        count = len(stats['trades'])
        winrate = stats['wins'] / count * 100 if count > 0 else 0
        icon = "✅" if stats['pnl'] > 0 else "❌"
        print(f"{icon} {strategy:<18} {count:<6} ${stats['pnl']:<+10.2f} {winrate:<.0f}%")
    
    # 方向分析
    print(f"\n" + "=" * 80)
    print("📊 方向分析")
    print("=" * 80)
    
    direction_stats = defaultdict(lambda: {'pnl': 0, 'count': 0, 'wins': 0})
    for t in trades:
        direction = t.get('direction', 'UNKNOWN')
        direction_stats[direction]['count'] += 1
        direction_stats[direction]['pnl'] += t.get('net_pnl_usdt', 0)
        if t.get('net_pnl_usdt', 0) > 0:
            direction_stats[direction]['wins'] += 1
    
    for direction, stats in direction_stats.items():
        winrate = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
        icon = "✅" if stats['pnl'] > 0 else "❌"
        print(f"{icon} {direction}: {stats['count']}筆, PnL ${stats['pnl']:.2f}, 勝率 {winrate:.0f}%")
    
    # ============================================================
    # 2. 組合分析：找出獲利組合
    # ============================================================
    print(f"\n" + "=" * 80)
    print("🔥 組合分析：找出能賺錢的條件組合")
    print("=" * 80)
    
    # 測試不同過濾條件組合
    filters = [
        # (名稱, 過濾函數)
        ("機率 80-88%", lambda t: 0.80 <= t.get('probability', 0) <= 0.88),
        ("機率 85-90%", lambda t: 0.85 <= t.get('probability', 0) <= 0.90),
        ("OBI < 0.5", lambda t: t.get('obi', 1) < 0.5),
        ("OBI > 0.5", lambda t: t.get('obi', 0) > 0.5),
        ("ACCUMULATION 做多", lambda t: t.get('strategy') == 'ACCUMULATION' and t.get('direction') == 'LONG'),
        ("RE_ACCUMULATION", lambda t: t.get('strategy') == 'RE_ACCUMULATION'),
        ("BEAR_TRAP", lambda t: t.get('strategy') == 'BEAR_TRAP'),
        ("只做多", lambda t: t.get('direction') == 'LONG'),
        ("只做空", lambda t: t.get('direction') == 'SHORT'),
        ("獲利時段", lambda t: int(t.get('timestamp', 'T00').split('T')[1][:2]) in profitable_hours if 'T' in t.get('timestamp', '') else False),
        ("曾漲超過3%", lambda t: t.get('max_profit_pct', 0) >= 3),
        ("最大回撤<8%", lambda t: abs(t.get('max_drawdown_pct', 100)) < 8),
    ]
    
    print(f"\n單一過濾條件效果:")
    print(f"{'條件':<25} {'筆數':<6} {'PnL':<12} {'勝率':<8}")
    print("-" * 55)
    
    profitable_filters = []
    for name, filter_func in filters:
        filtered = [t for t in trades if filter_func(t)]
        if len(filtered) >= 3:  # 至少 3 筆才有意義
            pnl = sum(t.get('net_pnl_usdt', 0) for t in filtered)
            wins = len([t for t in filtered if t.get('net_pnl_usdt', 0) > 0])
            winrate = wins / len(filtered) * 100
            icon = "✅" if pnl > 0 else "❌"
            print(f"{icon} {name:<23} {len(filtered):<6} ${pnl:<+10.2f} {winrate:<.0f}%")
            
            if pnl > 0:
                profitable_filters.append((name, filter_func, pnl, len(filtered), winrate))
    
    # ============================================================
    # 3. 組合最佳過濾器
    # ============================================================
    print(f"\n" + "=" * 80)
    print("💎 最佳過濾組合")
    print("=" * 80)
    
    if profitable_filters:
        # 嘗試組合獲利過濾器
        from itertools import combinations
        
        best_combo = None
        best_pnl = -999999
        
        for r in range(1, min(4, len(profitable_filters) + 1)):
            for combo in combinations(profitable_filters, r):
                # 組合所有過濾器
                def combined_filter(t):
                    return all(f[1](t) for f in combo)
                
                filtered = [t for t in trades if combined_filter(t)]
                if len(filtered) >= 3:
                    pnl = sum(t.get('net_pnl_usdt', 0) for t in filtered)
                    wins = len([t for t in filtered if t.get('net_pnl_usdt', 0) > 0])
                    winrate = wins / len(filtered) * 100
                    
                    # 找 PnL 最高且勝率 > 60% 的組合
                    if pnl > best_pnl and winrate >= 60:
                        best_pnl = pnl
                        best_combo = (combo, filtered, pnl, winrate)
        
        if best_combo:
            combo, filtered, pnl, winrate = best_combo
            print(f"\n🏆 最佳組合:")
            print(f"   條件: {' + '.join([f[0] for f in combo])}")
            print(f"   筆數: {len(filtered)}")
            print(f"   PnL: ${pnl:+.2f}")
            print(f"   勝率: {winrate:.0f}%")
            
            print(f"\n📋 這些交易:")
            for t in filtered:
                ts = t.get('timestamp', '')[:16]
                strategy = t.get('strategy', '')
                prob = t.get('probability', 0)
                pnl = t.get('net_pnl_usdt', 0)
                max_p = t.get('max_profit_pct', 0)
                icon = "✅" if pnl > 0 else "❌"
                print(f"   {icon} {ts} | {strategy} {prob:.0%} | +{max_p:.1f}% | ${pnl:+.2f}")
    
    # ============================================================
    # 4. 理論最佳：如果完美出場
    # ============================================================
    print(f"\n" + "=" * 80)
    print("🎯 理論分析：如果能在最高點出場")
    print("=" * 80)
    
    # 只看曾經有獲利機會的交易
    had_profit = [t for t in trades if t.get('max_profit_pct', 0) >= 3]
    print(f"\n曾漲超過 3% 的交易: {len(had_profit)} 筆")
    
    if had_profit:
        actual_pnl = sum(t.get('net_pnl_usdt', 0) for t in had_profit)
        
        # 如果在 3% 出場
        theoretical_pnl = len(had_profit) * (100 * 0.03 - 4)  # 3% 獲利 - $4 手續費
        
        print(f"   實際 PnL: ${actual_pnl:.2f}")
        print(f"   如果都在 3% 出場: ${theoretical_pnl:.2f}")
        print(f"   差距: ${theoretical_pnl - actual_pnl:.2f}")
        
        # 分析為什麼沒抓到
        print(f"\n   這些交易的問題:")
        missed_profit = 0
        for t in had_profit:
            max_p = t.get('max_profit_pct', 0)
            actual = t.get('net_pnl_usdt', 0)
            potential = 100 * (max_p / 100) - 4
            
            if actual < potential * 0.5:  # 只拿到不到一半
                missed_profit += potential - actual
                ts = t.get('timestamp', '')[:16]
                print(f"   {ts} | 最高+{max_p:.1f}% (潛在${potential:+.2f}) 但只賺 ${actual:+.2f}")
        
        print(f"\n   錯過的獲利: ${missed_profit:.2f}")
    
    # ============================================================
    # 5. 結論與建議
    # ============================================================
    print(f"\n" + "=" * 80)
    print("💡 結論與建議")
    print("=" * 80)
    
    total_pnl = sum(t.get('net_pnl_usdt', 0) for t in trades)
    total_fees = len(trades) * 4  # 每筆約 $4 手續費
    
    print(f"""
    📊 現狀分析:
    - 總交易: {len(trades)} 筆
    - 總 PnL: ${total_pnl:.2f}
    - 總手續費: ~${total_fees:.0f}
    - 如果 0 手續費: ${total_pnl + total_fees:.2f}
    
    🔑 關鍵發現:
    1. 手續費是巨大成本 (每筆 ~$4，占 100U 本金的 4%)
    2. 需要 4% 以上的價格移動才能打平手續費
    3. 100x 槓桿 = 0.04% 價格移動 = 4% 獲利
    
    💡 可能的解決方案:
    1. 降低槓桿 (50x)，降低手續費比例
    2. 增加本金 (200U)，降低手續費比例
    3. 使用 Maker 單，手續費 0.02% vs 0.04%
    4. 等待更大的行情再進場
    5. 減少交易次數，提高每次交易的品質
    """)

if __name__ == "__main__":
    main()
