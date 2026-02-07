#!/usr/bin/env python3
"""
v5.9.1 回測 - 新增「先漲保護」邏輯
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

def simulate_v591(trade):
    """
    模擬 v5.9.1 策略
    
    v5.9.1 新增:
    - 先漲保護: 若曾漲超過 4%，則設置保本止損
    """
    # v5.7 策略過濾
    strategy = trade.get('strategy', '')
    direction = trade.get('direction', '')
    prob = trade.get('probability', 0)
    obi = trade.get('obi', 0)
    
    if strategy == 'DISTRIBUTION' and direction == 'SHORT':
        return None, "DISTRIBUTION 做空"
    if prob < 0.75 or prob > 0.92:
        return None, f"機率不在區間"
    if direction == 'LONG' and (obi < 0.2 or obi > 0.85):
        return None, f"OBI 不在區間"
    
    max_profit = trade.get('max_profit_pct', 0)
    max_dd = abs(trade.get('max_drawdown_pct', 0))
    actual_pnl = trade.get('net_pnl_usdt', 0)
    position = trade.get('position_size_usdt', 100)
    fee = 4
    
    # v5.9.1 參數
    no_momentum_min_profit = 1.0
    no_momentum_loss_trigger = 5.0
    profit_protection_trigger = 4.0  # 🆕 曾漲超過 4% 啟動保護
    
    # 檢查 1: 無動能止損
    if max_profit < no_momentum_min_profit and max_dd >= no_momentum_loss_trigger:
        simulated_pnl = -position * (no_momentum_loss_trigger / 100) - fee
        return simulated_pnl, "無動能止損 @-5%"
    
    # 檢查 2: 先漲保護 (新增)
    # 若曾漲超過 4% 但最終虧損，假設在回撤到 0% 時保本出場
    if max_profit >= profit_protection_trigger and actual_pnl <= 0:
        simulated_pnl = 0 - fee  # 保本出場 (只付手續費)
        return simulated_pnl, f"先漲保護 (曾+{max_profit:.1f}%)"
    
    return actual_pnl, "實際結果"

def main():
    trades = load_all_trades()
    
    print("=" * 80)
    print("📊 v5.9.1 回測 (寬止損 + 無動能止損 + 先漲保護)")
    print("=" * 80)
    
    print(f"\n📋 v5.9.1 新增策略:")
    print(f"   1. v5.9: 無動能止損 (最高<1% 且虧損>5% 時提早止損)")
    print(f"   2. 🆕 先漲保護: 曾漲超過 4% 後回撤到 0% 時保本出場")
    
    passed = []
    blocked = []
    
    for t in trades:
        result, reason = simulate_v591(t)
        if result is None:
            blocked.append({'trade': t, 'reason': reason})
        else:
            passed.append({
                'trade': t,
                'simulated_pnl': result,
                'exit_reason': reason,
                'actual_pnl': t.get('net_pnl_usdt', 0)
            })
    
    total_original_pnl = sum(t.get('net_pnl_usdt', 0) for t in trades)
    v591_pnl = sum(p['simulated_pnl'] for p in passed)
    v591_wins = len([p for p in passed if p['simulated_pnl'] > 0])
    v591_winrate = v591_wins / len(passed) * 100 if passed else 0
    original_passed_pnl = sum(p['actual_pnl'] for p in passed)
    
    print(f"\n📈 結果")
    print(f"   原始全部 (68 筆): ${total_original_pnl:.2f}")
    print(f"   v5.7 過濾後 (20 筆): ${original_passed_pnl:.2f}")
    print(f"   v5.9.1 優化 (20 筆): ${v591_pnl:.2f}")
    print(f"   總改善: ${v591_pnl - total_original_pnl:+.2f}")
    print(f"   勝率: {v591_winrate:.1f}%")
    
    # 詳細
    print(f"\n📋 v5.9.1 觸發詳情:")
    
    protected = [p for p in passed if '保護' in p['exit_reason'] or '無動能' in p['exit_reason']]
    for p in protected:
        t = p['trade']
        actual = p['actual_pnl']
        simulated = p['simulated_pnl']
        diff = simulated - actual
        max_p = t.get('max_profit_pct', 0)
        max_dd = abs(t.get('max_drawdown_pct', 0))
        ts = t.get('timestamp', '')[:16]
        reason = p['exit_reason']
        
        icon = "📈" if diff > 0 else "📉"
        print(f"   {ts} | +{max_p:.1f}%/-{max_dd:.1f}% | 實際${actual:.2f} → ${simulated:.2f} ({icon}${diff:+.2f}) | {reason}")
    
    protection_savings = sum(p['simulated_pnl'] - p['actual_pnl'] for p in protected)
    print(f"\n   保護機制總省: ${protection_savings:+.2f}")
    
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
        print(f"   v5.9.1 勝率: {v591_winrate:.1f}%")
        
        if v591_winrate > required_wr:
            print(f"   ✅ 勝率 > 打平點，預期盈利！")
        else:
            print(f"   ⚠️ 勝率差距 {required_wr - v591_winrate:.1f}%")

if __name__ == "__main__":
    main()
