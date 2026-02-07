#!/usr/bin/env python3
"""
v6.0 回測 - 避免高回撤策略
"""

import json
from pathlib import Path

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

def v60_filter(trade):
    """
    v6.0 過濾策略：避免高回撤交易
    
    基於數據分析：
    1. 凌晨 1-6 點高回撤率 67% → 不交易
    2. 機率 > 95% 高回撤率高 → 不交易  
    3. OBI < 0.2 (做多時) 高回撤率高 → 不交易
    4. 保留 v5.7 基礎過濾
    """
    strategy = trade.get('strategy', '')
    direction = trade.get('direction', '')
    prob = trade.get('probability', 0)
    obi = trade.get('obi', 0)
    ts = trade.get('timestamp', '')
    
    # 取得小時
    hour = 12  # 預設
    if 'T' in ts:
        try:
            hour = int(ts.split('T')[1][:2])
        except:
            pass
    
    # v5.7 基礎過濾
    if strategy == 'DISTRIBUTION' and direction == 'SHORT':
        return False, "DISTRIBUTION 做空"
    if prob < 0.75:
        return False, f"機率 < 75%"
    
    # 🆕 v6.0 避免高回撤過濾
    
    # 1. 凌晨 1-6 點不交易 (67% 高回撤率)
    if 1 <= hour <= 6:
        return False, f"凌晨 {hour} 點 (高回撤時段)"
    
    # 2. 機率 > 95% 不交易 (可能是假信號)
    if prob > 0.95:
        return False, f"機率 {prob:.0%} > 95% (過度自信)"
    
    # 3. 做多時 OBI < 0.2 不交易
    if direction == 'LONG' and obi < 0.2:
        return False, f"OBI {obi:.2f} < 0.2 (買壓不足)"
    
    return True, "通過"

def simulate_v60_exit(trade):
    """
    v6.0 出場策略：快速止損
    
    1. 如果進場後沒漲過且跌超過 5%，立刻止損
    2. 如果曾漲超過 4% 後回撤到 0%，保本出場
    """
    max_profit = trade.get('max_profit_pct', 0)
    max_dd = abs(trade.get('max_drawdown_pct', 0))
    actual_pnl = trade.get('net_pnl_usdt', 0)
    position = 100
    fee = 4
    
    # 無動能止損：沒漲過且跌超過 5%
    if max_profit < 1 and max_dd >= 5:
        return -position * 0.05 - fee, "無動能止損 @-5%"
    
    # 先漲保護：曾漲超過 4% 但最終虧損
    if max_profit >= 4 and actual_pnl <= 0:
        return -fee, "先漲保護 @0%"
    
    return actual_pnl, "實際結果"

def main():
    trades = load_all_trades()
    
    print("=" * 80)
    print("📊 v6.0 回測 - 避免高回撤策略")
    print("=" * 80)
    
    print(f"\n🎯 v6.0 策略核心:")
    print(f"   1. 凌晨 1-6 點不交易 (67% 高回撤率)")
    print(f"   2. 機率 > 95% 不交易 (可能假信號)")
    print(f"   3. 做多 OBI < 0.2 不交易 (買壓不足)")
    print(f"   4. 無動能止損 + 先漲保護")
    
    # 模擬
    passed = []
    blocked = []
    
    for t in trades:
        ok, reason = v60_filter(t)
        if not ok:
            blocked.append({'trade': t, 'reason': reason})
        else:
            sim_pnl, exit_reason = simulate_v60_exit(t)
            passed.append({
                'trade': t,
                'simulated_pnl': sim_pnl,
                'exit_reason': exit_reason,
                'actual_pnl': t.get('net_pnl_usdt', 0)
            })
    
    # 統計
    total_original = sum(t.get('net_pnl_usdt', 0) for t in trades)
    blocked_pnl = sum(t['trade'].get('net_pnl_usdt', 0) for t in blocked)
    v60_pnl = sum(p['simulated_pnl'] for p in passed)
    v60_wins = len([p for p in passed if p['simulated_pnl'] > 0])
    v60_winrate = v60_wins / len(passed) * 100 if passed else 0
    
    print(f"\n📈 結果")
    print(f"   原始 (68 筆): ${total_original:.2f}")
    print(f"   v6.0 過濾後 ({len(passed)} 筆): ${v60_pnl:.2f}")
    print(f"   被阻擋 ({len(blocked)} 筆): ${blocked_pnl:.2f}")
    print(f"   勝率: {v60_winrate:.1f}%")
    print(f"   總改善: ${v60_pnl - total_original:+.2f}")
    
    # 分析被阻擋的交易
    print(f"\n📋 被阻擋交易分析:")
    blocked_by_reason = {}
    for b in blocked:
        reason = b['reason'].split('(')[0].strip()
        if reason not in blocked_by_reason:
            blocked_by_reason[reason] = {'count': 0, 'pnl': 0}
        blocked_by_reason[reason]['count'] += 1
        blocked_by_reason[reason]['pnl'] += b['trade'].get('net_pnl_usdt', 0)
    
    for reason, stats in sorted(blocked_by_reason.items(), key=lambda x: x[1]['pnl']):
        icon = "✅" if stats['pnl'] < 0 else "⚠️"
        print(f"   {icon} {reason}: {stats['count']}筆, ${stats['pnl']:.2f}")
    
    # v6.0 交易詳情
    print(f"\n📋 v6.0 放行交易:")
    for p in sorted(passed, key=lambda x: x['trade'].get('timestamp', '')):
        t = p['trade']
        ts = t.get('timestamp', '')[:16]
        strategy = t.get('strategy', '')
        prob = t.get('probability', 0)
        actual = p['actual_pnl']
        simulated = p['simulated_pnl']
        exit_reason = p['exit_reason']
        
        diff = simulated - actual
        icon = "✅" if simulated > 0 else "❌"
        diff_icon = "📈" if diff > 0 else ""
        
        print(f"   {icon} {ts} | {strategy} {prob:.0%} | ${actual:+.2f} → ${simulated:+.2f} {diff_icon} | {exit_reason}")
    
    # 風險報酬比
    wins = [p for p in passed if p['simulated_pnl'] > 0]
    losses = [p for p in passed if p['simulated_pnl'] <= 0]
    
    if wins and losses:
        avg_win = sum(p['simulated_pnl'] for p in wins) / len(wins)
        avg_loss = abs(sum(p['simulated_pnl'] for p in losses) / len(losses))
        required_wr = avg_loss / (avg_win + avg_loss) * 100
        
        print(f"\n📊 風險報酬比")
        print(f"   平均獲利: +${avg_win:.2f}")
        print(f"   平均虧損: -${avg_loss:.2f}")
        print(f"   打平所需勝率: {required_wr:.1f}%")
        print(f"   v6.0 勝率: {v60_winrate:.1f}%")
        
        if v60_winrate > required_wr:
            expected_per_trade = avg_win * (v60_winrate/100) - avg_loss * (1 - v60_winrate/100)
            print(f"   ✅ 勝率 > 打平點，預期每筆 ${expected_per_trade:+.2f}")
        else:
            print(f"   ⚠️ 勝率差距 {required_wr - v60_winrate:.1f}%")

if __name__ == "__main__":
    main()
