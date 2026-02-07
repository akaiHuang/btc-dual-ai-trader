#!/usr/bin/env python3
"""
v5.7 回測分析腳本
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple

OBSERVE_STRATEGIES = [
    'FAKEOUT', 'SPOOFING', 'CONSOLIDATION_SHAKE', 'STOP_HUNT',
    'WHIPSAW', 'SLOW_BLEED', 'WASH_TRADING', 'LAYERING', 'NORMAL'
]

def would_v57_block(trade: Dict) -> Tuple[bool, str]:
    """v5.7 過濾邏輯"""
    strategy = trade.get('strategy', '')
    direction = trade.get('direction', '')
    prob = trade.get('probability', 0)
    obi = trade.get('obi', 0)
    
    # 過濾 1: 停止 DISTRIBUTION 做空
    if strategy == 'DISTRIBUTION' and direction == 'SHORT':
        return True, "DISTRIBUTION 做空 (歷史勝率42-48%)"
    
    # 過濾 2: 機率區間 75-92%
    if prob < 0.75:
        return True, f"機率 {prob:.0%} < 75%"
    if prob > 0.92:
        return True, f"機率 {prob:.0%} > 92%"
    
    # 過濾 3: OBI 區間 (做多時)
    if direction == 'LONG':
        if obi < 0.2:
            return True, f"OBI {obi:.2f} < 0.2 (買壓不足)"
        if obi > 0.85:
            return True, f"OBI {obi:.2f} > 0.85 (可能誘多)"
    
    return False, "通過"

def main():
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
    
    if not all_trades:
        print("❌ 沒有找到任何交易紀錄")
        return
    
    # 分類
    blocked = []
    passed = []
    
    for trade in all_trades:
        would_block, reason = would_v57_block(trade)
        trade['v57_blocked'] = would_block
        trade['v57_reason'] = reason
        
        if would_block:
            blocked.append(trade)
        else:
            passed.append(trade)
    
    # 計算統計
    def calc_stats(trades):
        if not trades:
            return 0, 0, 0, 0
        wins = len([t for t in trades if t.get('net_pnl_usdt', 0) > 0])
        total_pnl = sum(t.get('net_pnl_usdt', 0) for t in trades)
        winrate = wins / len(trades) * 100
        avg_pnl = total_pnl / len(trades)
        return len(trades), winrate, total_pnl, avg_pnl
    
    total_n, total_wr, total_pnl, total_avg = calc_stats(all_trades)
    blocked_n, blocked_wr, blocked_pnl, blocked_avg = calc_stats(blocked)
    passed_n, passed_wr, passed_pnl, passed_avg = calc_stats(passed)
    
    print("=" * 80)
    print("📊 v5.7 回測分析報告")
    print("=" * 80)
    
    print(f"\n📈 原始交易 ({total_n} 筆)")
    print(f"   勝率: {total_wr:.1f}%")
    print(f"   總 PnL: ${total_pnl:.2f}")
    print(f"   平均 PnL: ${total_avg:.2f}")
    
    print(f"\n🚫 v5.7 會阻擋的交易 ({blocked_n} 筆)")
    print(f"   勝率: {blocked_wr:.1f}%")
    print(f"   總 PnL: ${blocked_pnl:.2f}")
    print(f"   平均 PnL: ${blocked_avg:.2f}")
    
    print(f"\n✅ v5.7 會放行的交易 ({passed_n} 筆)")
    print(f"   勝率: {passed_wr:.1f}%")
    print(f"   總 PnL: ${passed_pnl:.2f}")
    print(f"   平均 PnL: ${passed_avg:.2f}")
    
    print(f"\n📊 v5.7 過濾效果")
    print(f"   原始 PnL: ${total_pnl:.2f}")
    print(f"   過濾後 PnL: ${passed_pnl:.2f}")
    improvement = passed_pnl - total_pnl
    print(f"   改善: ${improvement:+.2f}")
    
    if blocked_wr > 0:
        print(f"\n   ⚠️ 被阻擋交易勝率: {blocked_wr:.1f}%")
        if blocked_wr > passed_wr:
            print(f"   ⚠️ 警告：被阻擋交易勝率 > 放行交易勝率！")
        else:
            print(f"   ✅ 正確：被阻擋交易勝率 < 放行交易勝率")
    
    # 按阻擋原因分類
    print(f"\n" + "=" * 80)
    print("📋 阻擋原因分類")
    print("=" * 80)
    
    reasons = {}
    for t in blocked:
        reason = t['v57_reason']
        if reason not in reasons:
            reasons[reason] = {'trades': [], 'wins': 0, 'pnl': 0}
        reasons[reason]['trades'].append(t)
        reasons[reason]['pnl'] += t.get('net_pnl_usdt', 0)
        if t.get('net_pnl_usdt', 0) > 0:
            reasons[reason]['wins'] += 1
    
    for reason, data in sorted(reasons.items(), key=lambda x: len(x[1]['trades']), reverse=True):
        n = len(data['trades'])
        wr = data['wins'] / n * 100 if n > 0 else 0
        pnl = data['pnl']
        icon = "✅" if pnl < 0 else "⚠️"  # 阻擋虧損交易是好的
        print(f"{icon} {reason}: {n}筆 | 勝率 {wr:.1f}% | PnL ${pnl:.2f}")
    
    # 放行交易詳情
    print(f"\n" + "=" * 80)
    print("✅ 放行交易詳情")
    print("=" * 80)
    
    for t in sorted(passed, key=lambda x: x.get('timestamp', '')):
        pnl = t.get('net_pnl_usdt', 0)
        icon = "✅" if pnl > 0 else "❌"
        prob = t.get('probability', 0)
        obi = t.get('obi', 0)
        print(f"{icon} {t.get('timestamp', 'N/A')[:16]} | {t.get('strategy')} {prob:.0%} | {t.get('direction')} | OBI {obi:.2f} | ${pnl:.2f}")

if __name__ == "__main__":
    main()
