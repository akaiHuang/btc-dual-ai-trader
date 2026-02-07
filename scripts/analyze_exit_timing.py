#!/usr/bin/env python3
"""
深度分析：找出真正能區分獲利/虧損的特徵
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
    if prob < 0.75:
        return False
    if 1 <= hour <= 6:
        return False
    if prob > 0.95:
        return False
    if direction == 'LONG' and obi < 0.2:
        return False
    
    return True

def main():
    trades = load_all_trades()
    v60_trades = [t for t in trades if v60_filter(t)]
    
    print("=" * 80)
    print("🔬 深度分析：獲利 vs 虧損交易的差異")
    print("=" * 80)
    
    winners = [t for t in v60_trades if t.get('net_pnl_usdt', 0) > 0]
    losers = [t for t in v60_trades if t.get('net_pnl_usdt', 0) <= 0]
    
    print(f"\n獲利: {len(winners)} 筆, 虧損: {len(losers)} 筆")
    
    # 關鍵觀察：虧損交易的 max_profit_pct
    print(f"\n📊 最高獲利點分析:")
    winner_max_p = [t.get('max_profit_pct', 0) for t in winners]
    loser_max_p = [t.get('max_profit_pct', 0) for t in losers]
    
    print(f"   獲利組最高點: 平均 {sum(winner_max_p)/len(winner_max_p):.1f}%, 範圍 {min(winner_max_p):.1f}%~{max(winner_max_p):.1f}%")
    print(f"   虧損組最高點: 平均 {sum(loser_max_p)/len(loser_max_p):.1f}%, 範圍 {min(loser_max_p):.1f}%~{max(loser_max_p):.1f}%")
    
    # 發現：虧損組有 4 筆 max_profit = 0 (從未漲過)
    never_up = [t for t in losers if t.get('max_profit_pct', 0) < 0.5]
    print(f"\n   虧損組中「從未漲過」的交易: {len(never_up)} 筆")
    print(f"   這些交易的虧損: ${sum(t.get('net_pnl_usdt', 0) for t in never_up):.2f}")
    
    # 排除這些後的效果
    had_profit = [t for t in losers if t.get('max_profit_pct', 0) >= 0.5]
    print(f"\n   如果這 {len(never_up)} 筆能在 -5% 止損:")
    saved = sum(t.get('net_pnl_usdt', 0) for t in never_up) - len(never_up) * (-9)
    print(f"   可省: ${-saved:.2f}")
    
    # 剩餘虧損交易分析
    print(f"\n📊 剩餘虧損交易 (曾經漲過但最終虧損):")
    for t in had_profit:
        ts = t.get('timestamp', '')[:16]
        max_p = t.get('max_profit_pct', 0)
        max_dd = t.get('max_drawdown_pct', 0)
        pnl = t.get('net_pnl_usdt', 0)
        print(f"   {ts} | 最高+{max_p:.1f}% 最低{max_dd:.1f}% | ${pnl:.2f}")
        print(f"      → 如果在 +{max_p:.0f}% 出場: ${100 * max_p/100 - 4:.2f}")
    
    # 計算理論最佳
    print(f"\n" + "=" * 80)
    print("💰 理論最佳策略分析")
    print("=" * 80)
    
    # 如果所有交易都能在最高點出場
    theoretical_best = sum(100 * t.get('max_profit_pct', 0) / 100 - 4 for t in v60_trades)
    actual = sum(t.get('net_pnl_usdt', 0) for t in v60_trades)
    
    print(f"\n   實際 PnL: ${actual:.2f}")
    print(f"   理論最佳 (都在最高點出): ${theoretical_best:.2f}")
    print(f"   差距: ${theoretical_best - actual:.2f}")
    
    # 如果只要能抓到 50% 的潛在獲利
    half_potential = sum(100 * t.get('max_profit_pct', 0) / 100 * 0.5 - 4 for t in v60_trades)
    print(f"   如果抓到 50% 潛在獲利: ${half_potential:.2f}")
    
    # 如果設置 2% 的 trailing stop
    print(f"\n📊 Trailing Stop 模擬:")
    
    for trailing in [1.0, 1.5, 2.0, 2.5]:
        total = 0
        for t in v60_trades:
            max_p = t.get('max_profit_pct', 0)
            if max_p > trailing:
                # 有機會在 (max_p - trailing) 出場
                exit_pct = max_p - trailing
                pnl = 100 * exit_pct / 100 - 4
            else:
                # 用實際結果
                pnl = t.get('net_pnl_usdt', 0)
            total += pnl
        
        icon = "✅" if total > 0 else "❌"
        print(f"   {icon} Trailing {trailing}%: ${total:.2f}")
    
    # 結論
    print(f"\n" + "=" * 80)
    print("💡 結論")
    print("=" * 80)
    
    print(f"""
    🎯 核心問題: 出場時機不對
    
    1. 獲利交易平均最高點: {sum(winner_max_p)/len(winner_max_p):.1f}%
       但很多沒能在高點出場
    
    2. 虧損交易中有 {len(never_up)} 筆「從未漲過」
       → v5.9 的無動能止損已經在處理這個
    
    3. 有 {len(had_profit)} 筆「先漲後跌」
       → v5.9.1 的先漲保護已經在處理這個
    
    💡 真正的解決方案: 更緊的 Trailing Stop
    
    當獲利達到 2% 時，設置 1.5% 的 trailing stop
    這樣即使回撤，也能鎖住至少 0.5% 的獲利
    """)

if __name__ == "__main__":
    main()
