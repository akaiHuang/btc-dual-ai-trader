#!/usr/bin/env python3
"""
深度分析：找出真正能盈利的交易模式
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

def main():
    trades = load_all_trades()
    
    print("=" * 80)
    print("🔍 深度分析：找出真正能盈利的模式")
    print("=" * 80)
    
    # 先做 v5.7 過濾
    v57_trades = []
    for t in trades:
        strategy = t.get('strategy', '')
        direction = t.get('direction', '')
        prob = t.get('probability', 0)
        obi = t.get('obi', 0)
        
        # v5.7 過濾
        if strategy == 'DISTRIBUTION' and direction == 'SHORT':
            continue
        if prob < 0.75 or prob > 0.92:
            continue
        if direction == 'LONG' and (obi < 0.2 or obi > 0.85):
            continue
        
        v57_trades.append(t)
    
    print(f"\n📊 v5.7 過濾後: {len(v57_trades)} 筆交易")
    
    # 分析每筆交易的走勢模式
    print(f"\n" + "=" * 80)
    print("📈 走勢模式分析")
    print("=" * 80)
    
    # 模式分類
    patterns = {
        'win_direct': [],      # 直接上漲獲利
        'win_dip_then_up': [], # 先跌後漲獲利
        'loss_direct': [],     # 直接下跌虧損
        'loss_up_then_down': [], # 先漲後跌虧損
        'loss_dip_stay': [],   # 下跌後沒回
    }
    
    for t in v57_trades:
        max_profit = t.get('max_profit_pct', 0)
        max_dd = abs(t.get('max_drawdown_pct', 0))
        pnl = t.get('net_pnl_usdt', 0)
        
        if pnl > 0:  # 獲利
            if max_dd < 2:
                patterns['win_direct'].append(t)
            else:
                patterns['win_dip_then_up'].append(t)
        else:  # 虧損
            if max_profit < 2:
                if max_dd > 8:
                    patterns['loss_dip_stay'].append(t)
                else:
                    patterns['loss_direct'].append(t)
            else:
                patterns['loss_up_then_down'].append(t)
    
    for name, group in patterns.items():
        count = len(group)
        if count == 0:
            continue
        total_pnl = sum(t.get('net_pnl_usdt', 0) for t in group)
        avg_max_profit = sum(t.get('max_profit_pct', 0) for t in group) / count
        avg_max_dd = sum(abs(t.get('max_drawdown_pct', 0)) for t in group) / count
        
        name_cn = {
            'win_direct': '✅ 直接上漲獲利',
            'win_dip_then_up': '✅ 先跌後漲獲利',
            'loss_direct': '❌ 直接下跌虧損',
            'loss_up_then_down': '❌ 先漲後跌虧損',
            'loss_dip_stay': '❌ 暴跌不回虧損',
        }
        
        print(f"\n{name_cn[name]}: {count} 筆, PnL: ${total_pnl:.2f}")
        print(f"   平均最高: +{avg_max_profit:.1f}%, 平均最低: -{avg_max_dd:.1f}%")
    
    # 關鍵發現：先跌後漲獲利
    print(f"\n" + "=" * 80)
    print("🔑 關鍵發現：先跌後漲的交易")
    print("=" * 80)
    
    dip_then_up = patterns['win_dip_then_up']
    if dip_then_up:
        print(f"\n這 {len(dip_then_up)} 筆交易中途跌過但最終獲利:")
        for t in dip_then_up:
            max_p = t.get('max_profit_pct', 0)
            max_dd = abs(t.get('max_drawdown_pct', 0))
            pnl = t.get('net_pnl_usdt', 0)
            print(f"   最高 +{max_p:.1f}%, 最低 -{max_dd:.1f}% → 最終 ${pnl:+.2f}")
        
        avg_dd = sum(abs(t.get('max_drawdown_pct', 0)) for t in dip_then_up) / len(dip_then_up)
        max_dd = max(abs(t.get('max_drawdown_pct', 0)) for t in dip_then_up)
        print(f"\n   平均回撤: {avg_dd:.1f}%, 最大回撤: {max_dd:.1f}%")
        print(f"   ⚠️ 止損設在 {max_dd + 1:.0f}% 以上才不會被洗掉！")
    
    # 虧損交易分析
    print(f"\n" + "=" * 80)
    print("💡 虧損交易可避免分析")
    print("=" * 80)
    
    loss_trades = patterns['loss_direct'] + patterns['loss_up_then_down'] + patterns['loss_dip_stay']
    
    for t in loss_trades:
        max_p = t.get('max_profit_pct', 0)
        max_dd = abs(t.get('max_drawdown_pct', 0))
        pnl = t.get('net_pnl_usdt', 0)
        strategy = t.get('strategy', '')
        prob = t.get('probability', 0)
        obi = t.get('obi', 0)
        ts = t.get('timestamp', '')[:16]
        
        # 判斷是否可避免
        avoidable = ""
        if max_p >= 4:
            avoidable = f"💡 如果在 +{max_p:.0f}% 止盈可避免"
        elif max_dd > 12:
            avoidable = f"💡 如果止損在 -{max_dd:.0f}% 可減損"
        else:
            avoidable = "⚠️ 難以避免"
        
        print(f"{ts} | {strategy} {prob:.0%} | +{max_p:.1f}% / -{max_dd:.1f}% → ${pnl:.2f}")
        print(f"   {avoidable}")
    
    # 優化建議
    print(f"\n" + "=" * 80)
    print("🎯 優化建議 (v5.9)")
    print("=" * 80)
    
    # 計算最佳止損點
    all_dds = [abs(t.get('max_drawdown_pct', 0)) for t in v57_trades if t.get('net_pnl_usdt', 0) > 0]
    if all_dds:
        max_winning_dd = max(all_dds)
        print(f"\n1. 止損設定:")
        print(f"   - 獲利交易中最大回撤: {max_winning_dd:.1f}%")
        print(f"   - 建議止損: {max_winning_dd + 2:.0f}% (給予緩衝)")
        print(f"   - 不要收緊止損！會洗掉獲利交易")
    
    # 計算最佳止盈點
    wins = [t for t in v57_trades if t.get('net_pnl_usdt', 0) > 0]
    if wins:
        avg_max_profit = sum(t.get('max_profit_pct', 0) for t in wins) / len(wins)
        print(f"\n2. 止盈設定:")
        print(f"   - 獲利交易平均最高點: +{avg_max_profit:.1f}%")
        print(f"   - 但要考慮能否抓到這個點")
    
    # 分析可避免的虧損
    avoidable_loss = 0
    avoidable_count = 0
    for t in loss_trades:
        max_p = t.get('max_profit_pct', 0)
        pnl = t.get('net_pnl_usdt', 0)
        if max_p >= 4:  # 如果曾經漲到 +4%
            # 假設在 +4% 止盈
            could_have = 100 * 0.04 - 4  # $4 獲利 - $4 手續費 = $0
            saved = could_have - pnl
            avoidable_loss += saved
            avoidable_count += 1
    
    print(f"\n3. 提早止盈策略:")
    print(f"   - {avoidable_count} 筆虧損交易曾經 +4% 以上")
    print(f"   - 如果在 +4% 止盈可挽回: ${avoidable_loss:.2f}")
    
    # 計算新策略效果
    print(f"\n" + "=" * 80)
    print("📊 模擬 v5.9 (寬止損 + 早止盈)")
    print("=" * 80)
    
    # v5.9 策略：
    # - 止損保持 12%（不要收緊）
    # - 止盈改成 4%（提早獲利）
    
    v59_pnl = 0
    v59_wins = 0
    
    for t in v57_trades:
        max_p = t.get('max_profit_pct', 0)
        max_dd = abs(t.get('max_drawdown_pct', 0))
        pnl = t.get('net_pnl_usdt', 0)
        
        # 模擬 v5.9
        if max_p >= 4:  # 能達到 4% 止盈
            simulated = 100 * 0.04 - 4  # = $0
            v59_wins += 1
        elif max_dd >= 12:  # 觸發 12% 止損
            simulated = -100 * 0.12 - 4  # = -$16
        else:  # 其他用實際
            simulated = pnl
            if pnl > 0:
                v59_wins += 1
        
        v59_pnl += simulated
    
    v59_winrate = v59_wins / len(v57_trades) * 100 if v57_trades else 0
    original_pnl = sum(t.get('net_pnl_usdt', 0) for t in v57_trades)
    
    print(f"\n   v5.7 原始: ${original_pnl:.2f}, 勝率 {len([t for t in v57_trades if t.get('net_pnl_usdt', 0) > 0])/len(v57_trades)*100:.0f}%")
    print(f"   v5.9 模擬: ${v59_pnl:.2f}, 勝率 {v59_winrate:.0f}%")
    print(f"   改善: ${v59_pnl - original_pnl:+.2f}")

if __name__ == "__main__":
    main()
