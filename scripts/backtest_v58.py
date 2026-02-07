#!/usr/bin/env python3
"""
v5.8 回測分析 - 價格優化版本
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

def simulate_v58(trade):
    """
    模擬 v5.8 的價格優化
    
    v5.8 設定:
    - 止盈: 0.15-0.25% (提高)
    - 止損: 0.05-0.08% (收緊)
    - 持倉: 5-10 分鐘 (最佳區間)
    - 智能止盈: 毛利 8%, 淨利 5%
    """
    # v5.7 策略過濾 (保留)
    strategy = trade.get('strategy', '')
    direction = trade.get('direction', '')
    prob = trade.get('probability', 0)
    obi = trade.get('obi', 0)
    
    # v5.7 過濾
    if strategy == 'DISTRIBUTION' and direction == 'SHORT':
        return None, "DISTRIBUTION 做空"
    if prob < 0.75:
        return None, f"機率 < 75%"
    if prob > 0.92:
        return None, f"機率 > 92%"
    if direction == 'LONG' and (obi < 0.2 or obi > 0.85):
        return None, f"OBI 不在區間"
    
    # 模擬 v5.8 價格優化
    max_profit_pct = trade.get('max_profit_pct', 0)  # 最大浮動獲利 %
    max_dd_pct = abs(trade.get('max_drawdown_pct', 0))  # 最大回撤 %
    actual_pnl = trade.get('net_pnl_usdt', 0)
    position = trade.get('position_size_usdt', 100)
    fee = 4  # 手續費固定 $4
    
    # v5.8 參數
    new_tp_pct = 8.0  # 新止盈目標 8%
    new_sl_pct = 6.0  # 新止損 6%
    
    # 模擬邏輯：
    # 1. 如果最大獲利 >= 8%，視為止盈成功
    # 2. 如果最大回撤 >= 6%，且最大獲利 < 8%，視為止損
    # 3. 否則使用實際結果
    
    if max_profit_pct >= new_tp_pct:
        # 能達到新止盈目標
        simulated_pnl = position * (new_tp_pct / 100) - fee
        return simulated_pnl, f"TP@{new_tp_pct}%"
    elif max_dd_pct >= new_sl_pct and max_profit_pct < new_tp_pct:
        # 會觸發新止損
        simulated_pnl = -position * (new_sl_pct / 100) - fee
        return simulated_pnl, f"SL@{new_sl_pct}%"
    else:
        # 其他情況用實際結果 (可能因智能止盈或超時出場)
        return actual_pnl, "實際結果"

def main():
    trades = load_all_trades()
    
    print("=" * 80)
    print("📊 v5.8 回測分析 (價格優化版)")
    print("=" * 80)
    
    # v5.8 完整模擬
    passed = []
    blocked = []
    
    for t in trades:
        result, reason = simulate_v58(t)
        if result is None:
            blocked.append({'trade': t, 'reason': reason})
        else:
            passed.append({
                'trade': t,
                'simulated_pnl': result,
                'exit_reason': reason,
                'actual_pnl': t.get('net_pnl_usdt', 0)
            })
    
    # 計算統計
    total_original_pnl = sum(t.get('net_pnl_usdt', 0) for t in trades)
    blocked_pnl = sum(t['trade'].get('net_pnl_usdt', 0) for t in blocked)
    
    v58_simulated_pnl = sum(p['simulated_pnl'] for p in passed)
    v58_wins = len([p for p in passed if p['simulated_pnl'] > 0])
    v58_winrate = v58_wins / len(passed) * 100 if passed else 0
    
    original_passed_pnl = sum(p['actual_pnl'] for p in passed)
    
    print(f"\n📈 原始數據 ({len(trades)} 筆)")
    print(f"   總 PnL: ${total_original_pnl:.2f}")
    
    print(f"\n🚫 v5.7 策略過濾 ({len(blocked)} 筆被阻擋)")
    print(f"   被阻擋 PnL: ${blocked_pnl:.2f}")
    
    print(f"\n✅ v5.8 優化後 ({len(passed)} 筆放行)")
    print(f"   原始 PnL (這 {len(passed)} 筆): ${original_passed_pnl:.2f}")
    print(f"   v5.8 模擬 PnL: ${v58_simulated_pnl:.2f}")
    print(f"   v5.8 模擬勝率: {v58_winrate:.1f}%")
    
    improvement = v58_simulated_pnl - original_passed_pnl
    total_improvement = v58_simulated_pnl - total_original_pnl
    
    print(f"\n📊 改善效果")
    print(f"   vs 原始 {len(passed)} 筆: ${improvement:+.2f}")
    print(f"   vs 全部 {len(trades)} 筆: ${total_improvement:+.2f}")
    
    # 詳細交易列表
    print(f"\n" + "=" * 80)
    print("📋 v5.8 模擬交易詳情")
    print("=" * 80)
    
    for p in sorted(passed, key=lambda x: x['trade'].get('timestamp', '')):
        t = p['trade']
        actual = p['actual_pnl']
        simulated = p['simulated_pnl']
        reason = p['exit_reason']
        max_p = t.get('max_profit_pct', 0)
        max_dd = t.get('max_drawdown_pct', 0)
        
        diff = simulated - actual
        icon = "✅" if simulated > 0 else "❌"
        diff_icon = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
        
        print(f"{icon} {t.get('timestamp', '')[:16]} | 最高+{max_p:.1f}% 最低{max_dd:.1f}%")
        print(f"   實際: ${actual:.2f} → v5.8: ${simulated:.2f} ({diff_icon} ${diff:+.2f}) | {reason}")
    
    # 風險報酬比分析
    print(f"\n" + "=" * 80)
    print("📊 風險報酬比分析")
    print("=" * 80)
    
    wins = [p for p in passed if p['simulated_pnl'] > 0]
    losses = [p for p in passed if p['simulated_pnl'] <= 0]
    
    if wins and losses:
        avg_win = sum(p['simulated_pnl'] for p in wins) / len(wins)
        avg_loss = abs(sum(p['simulated_pnl'] for p in losses) / len(losses))
        rr_ratio = avg_win / avg_loss if avg_loss > 0 else 0
        
        print(f"   平均獲利: +${avg_win:.2f}")
        print(f"   平均虧損: -${avg_loss:.2f}")
        print(f"   風險報酬比: 1:{1/rr_ratio:.1f}" if rr_ratio > 0 else "   風險報酬比: N/A")
        
        # 計算打平所需勝率
        required_wr = avg_loss / (avg_win + avg_loss) * 100
        print(f"   打平所需勝率: {required_wr:.1f}%")
        print(f"   v5.8 模擬勝率: {v58_winrate:.1f}%")
        
        if v58_winrate > required_wr:
            print(f"   ✅ 勝率 > 打平點，預期盈利！")
        else:
            print(f"   ⚠️ 勝率 < 打平點，預期虧損")

if __name__ == "__main__":
    main()
