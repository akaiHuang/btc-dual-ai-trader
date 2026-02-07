#!/usr/bin/env python3
"""
v6.0 完整回測 - 包含所有保護機制
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
    strategy = trade.get('strategy', '')
    direction = trade.get('direction', '')
    prob = trade.get('probability', 0)
    obi = trade.get('obi', 0)
    ts = trade.get('timestamp', '')
    
    hour = 12
    if 'T' in ts:
        try:
            hour = int(ts.split('T')[1][:2])
        except:
            pass
    
    if strategy == 'DISTRIBUTION' and direction == 'SHORT':
        return False
    if prob < 0.75 or prob > 0.95:
        return False
    if 1 <= hour <= 6:
        return False
    if direction == 'LONG' and obi < 0.2:
        return False
    
    return True

def simulate_v60_complete(trade):
    """完整 v6.0 出場策略"""
    max_profit = trade.get('max_profit_pct', 0)
    max_dd = abs(trade.get('max_drawdown_pct', 0))
    actual_pnl = trade.get('net_pnl_usdt', 0)
    
    # 1. 無動能止損: 從未漲過且跌超過 5%
    if max_profit < 1.0 and max_dd >= 5.0:
        return -100 * 0.05 - 4, "無動能止損"
    
    # 2. 先漲保護: 曾漲超過 4% 但最終虧損
    if max_profit >= 4.0 and actual_pnl <= 0:
        return -4, "先漲保護"
    
    # 3. 早期鎖盈: 曾漲超過 3% 就設 trailing stop 1.5%
    if max_profit >= 3.0:
        # 假設能在 max_profit - 1.5% 出場
        exit_pct = max(max_profit - 1.5, 0)
        simulated = 100 * (exit_pct / 100) - 4
        # 只有當比實際好才用
        if simulated > actual_pnl:
            return simulated, f"鎖盈 @{exit_pct:.1f}%"
    
    return actual_pnl, "實際結果"

def main():
    trades = load_all_trades()
    
    print("=" * 80)
    print("📊 v6.0 完整回測 (進場過濾 + 出場優化)")
    print("=" * 80)
    
    print(f"\n🎯 v6.0 完整策略:")
    print(f"   進場過濾:")
    print(f"   - 不做 DISTRIBUTION 空")
    print(f"   - 機率 75-95%")
    print(f"   - 凌晨 1-6 點不交易")
    print(f"   - 做多 OBI >= 0.2")
    print(f"   出場優化:")
    print(f"   - 無動能止損 (從未漲 1% 且跌 5%)")
    print(f"   - 先漲保護 (曾漲 4% 回撤到 0)")
    print(f"   - 早期鎖盈 (曾漲 3% 設 1.5% trailing)")
    
    # 模擬
    v60_trades = [t for t in trades if v60_filter(t)]
    
    results = []
    for t in v60_trades:
        sim_pnl, reason = simulate_v60_complete(t)
        results.append({
            'trade': t,
            'simulated': sim_pnl,
            'actual': t.get('net_pnl_usdt', 0),
            'reason': reason
        })
    
    total_original = sum(t.get('net_pnl_usdt', 0) for t in trades)
    blocked_pnl = sum(t.get('net_pnl_usdt', 0) for t in trades if not v60_filter(t))
    v60_pnl = sum(r['simulated'] for r in results)
    v60_wins = len([r for r in results if r['simulated'] > 0])
    v60_winrate = v60_wins / len(results) * 100 if results else 0
    
    print(f"\n📈 結果")
    print(f"   原始 (68 筆): ${total_original:.2f}")
    print(f"   v6.0 ({len(results)} 筆): ${v60_pnl:.2f}")
    print(f"   勝率: {v60_winrate:.1f}%")
    print(f"   總改善: ${v60_pnl - total_original:+.2f}")
    
    # 詳細
    print(f"\n📋 交易詳情:")
    for r in sorted(results, key=lambda x: x['trade'].get('timestamp', '')):
        t = r['trade']
        ts = t.get('timestamp', '')[:16]
        actual = r['actual']
        simulated = r['simulated']
        reason = r['reason']
        max_p = t.get('max_profit_pct', 0)
        
        diff = simulated - actual
        icon = "✅" if simulated > 0 else "❌"
        diff_str = f"📈+${diff:.2f}" if diff > 0.5 else ""
        
        print(f"   {icon} {ts} | 最高+{max_p:.1f}% | ${actual:+.2f} → ${simulated:+.2f} {diff_str} | {reason}")
    
    # 風險報酬比
    wins = [r for r in results if r['simulated'] > 0]
    losses = [r for r in results if r['simulated'] <= 0]
    
    if wins and losses:
        avg_win = sum(r['simulated'] for r in wins) / len(wins)
        avg_loss = abs(sum(r['simulated'] for r in losses) / len(losses))
        required_wr = avg_loss / (avg_win + avg_loss) * 100
        
        print(f"\n📊 風險報酬比")
        print(f"   平均獲利: +${avg_win:.2f}")
        print(f"   平均虧損: -${avg_loss:.2f}")
        print(f"   打平所需勝率: {required_wr:.1f}%")
        print(f"   v6.0 勝率: {v60_winrate:.1f}%")
        
        if v60_winrate >= required_wr:
            expected = avg_win * (v60_winrate/100) - avg_loss * (1 - v60_winrate/100)
            print(f"   ✅ 勝率 >= 打平點！")
            print(f"   預期每筆: ${expected:+.2f}")
            print(f"   預期 {len(results)} 筆: ${expected * len(results):+.2f}")
        else:
            print(f"   ⚠️ 勝率差距: {required_wr - v60_winrate:.1f}%")
    
    # 保護機制效果
    print(f"\n📊 保護機制效果:")
    for reason in ['無動能止損', '先漲保護', '鎖盈']:
        affected = [r for r in results if reason in r['reason']]
        if affected:
            actual_sum = sum(r['actual'] for r in affected)
            simulated_sum = sum(r['simulated'] for r in affected)
            saved = simulated_sum - actual_sum
            print(f"   {reason}: {len(affected)}筆, 省 ${saved:+.2f}")

if __name__ == "__main__":
    main()
