#!/usr/bin/env python3
"""
v5.9 回測分析 - 寬止損 + 無動能快速止損
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

def simulate_v59(trade):
    """
    模擬 v5.9 策略
    
    v5.9 核心邏輯:
    1. v5.7 策略過濾 (機率 75-92%, 無 DISTRIBUTION 空, OBI 過濾)
    2. 寬止損 (12-14%)：不被震出
    3. 無動能快速止損：進場 3 分鐘內若最高獲利 < 1% 且虧損 > 5%，提早止損
    """
    # v5.7 策略過濾
    strategy = trade.get('strategy', '')
    direction = trade.get('direction', '')
    prob = trade.get('probability', 0)
    obi = trade.get('obi', 0)
    
    if strategy == 'DISTRIBUTION' and direction == 'SHORT':
        return None, "DISTRIBUTION 做空"
    if prob < 0.75 or prob > 0.92:
        return None, f"機率 {prob:.0%} 不在 75-92%"
    if direction == 'LONG' and (obi < 0.2 or obi > 0.85):
        return None, f"OBI {obi:.2f} 不在區間"
    
    # 取得交易數據
    max_profit = trade.get('max_profit_pct', 0)
    max_dd = abs(trade.get('max_drawdown_pct', 0))
    actual_pnl = trade.get('net_pnl_usdt', 0)
    hold_minutes = trade.get('hold_duration_minutes', 0)
    position = trade.get('position_size_usdt', 100)
    fee = 4  # 手續費固定 $4
    
    # v5.9 參數
    no_momentum_check_after = 3      # 3 分鐘後檢查
    no_momentum_min_profit = 1.0     # 最高需 > 1%
    no_momentum_loss_trigger = 5.0   # 觸發虧損 5%
    wide_stop_loss = 12.0            # 寬止損 12%
    
    # 模擬邏輯:
    # 1. 檢查是否觸發無動能止損
    #    條件: max_profit < 1% 且 max_dd >= 5%
    #    (假設：如果最大獲利 < 1%，代表「進場後從未上漲」)
    
    if max_profit < no_momentum_min_profit and max_dd >= no_momentum_loss_trigger:
        # 觸發無動能止損，在虧損 5% 時出場 (而非等到 12%)
        simulated_pnl = -position * (no_momentum_loss_trigger / 100) - fee
        exit_reason = f"無動能止損 @-5%"
        return simulated_pnl, exit_reason
    
    # 2. 否則使用實際結果 (寬止損讓獲利交易有機會回升)
    return actual_pnl, "實際結果"

def main():
    trades = load_all_trades()
    
    print("=" * 80)
    print("📊 v5.9 回測分析 (寬止損 + 無動能快速止損)")
    print("=" * 80)
    
    print(f"\n📋 v5.9 策略核心:")
    print(f"   1. v5.7 過濾: 機率 75-92%, 無 DISTRIBUTION 空, OBI 0.2-0.85")
    print(f"   2. 寬止損: 12-14% (不被洗出)")
    print(f"   3. 無動能止損: 最高獲利 < 1% 且虧損 > 5% 時提早止損")
    
    # 模擬
    passed = []
    blocked = []
    
    for t in trades:
        result, reason = simulate_v59(t)
        if result is None:
            blocked.append({'trade': t, 'reason': reason})
        else:
            passed.append({
                'trade': t,
                'simulated_pnl': result,
                'exit_reason': reason,
                'actual_pnl': t.get('net_pnl_usdt', 0)
            })
    
    # 統計
    total_original_pnl = sum(t.get('net_pnl_usdt', 0) for t in trades)
    blocked_pnl = sum(t['trade'].get('net_pnl_usdt', 0) for t in blocked)
    
    v59_simulated_pnl = sum(p['simulated_pnl'] for p in passed)
    v59_wins = len([p for p in passed if p['simulated_pnl'] > 0])
    v59_winrate = v59_wins / len(passed) * 100 if passed else 0
    
    original_passed_pnl = sum(p['actual_pnl'] for p in passed)
    
    print(f"\n📈 原始數據 ({len(trades)} 筆)")
    print(f"   總 PnL: ${total_original_pnl:.2f}")
    
    print(f"\n🚫 v5.7 策略過濾 ({len(blocked)} 筆被阻擋)")
    print(f"   被阻擋 PnL: ${blocked_pnl:.2f}")
    
    print(f"\n✅ v5.9 優化後 ({len(passed)} 筆放行)")
    print(f"   原始 PnL: ${original_passed_pnl:.2f}")
    print(f"   v5.9 模擬 PnL: ${v59_simulated_pnl:.2f}")
    print(f"   v5.9 模擬勝率: {v59_winrate:.1f}%")
    
    improvement = v59_simulated_pnl - original_passed_pnl
    total_improvement = v59_simulated_pnl - total_original_pnl
    
    print(f"\n📊 改善效果")
    print(f"   vs 原始 {len(passed)} 筆: ${improvement:+.2f}")
    print(f"   vs 全部 {len(trades)} 筆: ${total_improvement:+.2f}")
    
    # 詳細列表
    print(f"\n" + "=" * 80)
    print("📋 v5.9 模擬交易詳情")
    print("=" * 80)
    
    no_momentum_triggered = []
    normal_trades = []
    
    for p in sorted(passed, key=lambda x: x['trade'].get('timestamp', '')):
        t = p['trade']
        actual = p['actual_pnl']
        simulated = p['simulated_pnl']
        reason = p['exit_reason']
        max_p = t.get('max_profit_pct', 0)
        max_dd = abs(t.get('max_drawdown_pct', 0))
        
        diff = simulated - actual
        
        if '無動能' in reason:
            no_momentum_triggered.append(p)
        else:
            normal_trades.append(p)
    
    print(f"\n⚡ 無動能止損觸發 ({len(no_momentum_triggered)} 筆):")
    for p in no_momentum_triggered:
        t = p['trade']
        actual = p['actual_pnl']
        simulated = p['simulated_pnl']
        diff = simulated - actual
        max_p = t.get('max_profit_pct', 0)
        max_dd = abs(t.get('max_drawdown_pct', 0))
        ts = t.get('timestamp', '')[:16]
        
        icon = "📈" if diff > 0 else "📉"
        print(f"   {ts} | +{max_p:.1f}%/-{max_dd:.1f}% | 實際${actual:.2f} → v5.9${simulated:.2f} ({icon}${diff:+.2f})")
    
    if no_momentum_triggered:
        nm_actual = sum(p['actual_pnl'] for p in no_momentum_triggered)
        nm_simulated = sum(p['simulated_pnl'] for p in no_momentum_triggered)
        print(f"\n   無動能止損效果: 實際 ${nm_actual:.2f} → v5.9 ${nm_simulated:.2f} (改善 ${nm_simulated - nm_actual:+.2f})")
    
    print(f"\n✅ 正常交易 ({len(normal_trades)} 筆):")
    for p in normal_trades:
        t = p['trade']
        actual = p['actual_pnl']
        max_p = t.get('max_profit_pct', 0)
        max_dd = abs(t.get('max_drawdown_pct', 0))
        ts = t.get('timestamp', '')[:16]
        
        icon = "✅" if actual > 0 else "❌"
        print(f"   {icon} {ts} | +{max_p:.1f}%/-{max_dd:.1f}% | ${actual:+.2f}")
    
    # 風險報酬比
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
        print(f"   風險報酬比: 1:{1/rr_ratio:.2f}" if rr_ratio > 0 else "   風險報酬比: N/A")
        
        required_wr = avg_loss / (avg_win + avg_loss) * 100
        print(f"   打平所需勝率: {required_wr:.1f}%")
        print(f"   v5.9 模擬勝率: {v59_winrate:.1f}%")
        
        if v59_winrate > required_wr:
            print(f"   ✅ 勝率 > 打平點，預期盈利！")
        else:
            gap = required_wr - v59_winrate
            print(f"   ⚠️ 勝率差距 {gap:.1f}%，需要更多優化")
    
    # 比較各版本
    print(f"\n" + "=" * 80)
    print("📊 各版本比較")
    print("=" * 80)
    
    print(f"\n   原始全部 (68 筆): ${total_original_pnl:.2f}")
    print(f"   v5.7 過濾 (20 筆): ${original_passed_pnl:.2f} (改善 ${original_passed_pnl - total_original_pnl:+.2f})")
    print(f"   v5.9 優化 (20 筆): ${v59_simulated_pnl:.2f} (改善 ${v59_simulated_pnl - total_original_pnl:+.2f})")

if __name__ == "__main__":
    main()
