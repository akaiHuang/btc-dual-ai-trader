#!/usr/bin/env python3
"""
深度分析：找出剩餘虧損的可能改善點
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

def v57_filter(trade):
    strategy = trade.get('strategy', '')
    direction = trade.get('direction', '')
    prob = trade.get('probability', 0)
    obi = trade.get('obi', 0)
    
    if strategy == 'DISTRIBUTION' and direction == 'SHORT':
        return False
    if prob < 0.75 or prob > 0.92:
        return False
    if direction == 'LONG' and (obi < 0.2 or obi > 0.85):
        return False
    return True

def main():
    trades = load_all_trades()
    v57_trades = [t for t in trades if v57_filter(t)]
    
    print("=" * 80)
    print("🔍 分析 v5.7 過濾後 20 筆交易的可改善空間")
    print("=" * 80)
    
    # 分類交易
    wins = [t for t in v57_trades if t.get('net_pnl_usdt', 0) > 0]
    losses = [t for t in v57_trades if t.get('net_pnl_usdt', 0) <= 0]
    
    print(f"\n📊 交易分類")
    print(f"   獲利: {len(wins)} 筆, 總計 ${sum(t.get('net_pnl_usdt', 0) for t in wins):.2f}")
    print(f"   虧損: {len(losses)} 筆, 總計 ${sum(t.get('net_pnl_usdt', 0) for t in losses):.2f}")
    
    # 分析每筆虧損
    print(f"\n📋 虧損交易分析:")
    
    for t in losses:
        ts = t.get('timestamp', '')[:16]
        max_p = t.get('max_profit_pct', 0)
        max_dd = abs(t.get('max_drawdown_pct', 0))
        pnl = t.get('net_pnl_usdt', 0)
        strategy = t.get('strategy', '')
        prob = t.get('probability', 0)
        obi = t.get('obi', 0)
        
        # 分類虧損類型
        if max_p < 1:
            loss_type = "無動能 (從未漲)"
            v591_simulated = -5  # 無動能止損
        elif max_p >= 4 and pnl < 0:
            loss_type = "先漲後跌"
            v591_simulated = -4  # 先漲保護
        else:
            loss_type = "正常波動"
            v591_simulated = pnl  # 無法改善
        
        improvement = v591_simulated - pnl
        can_improve = "✅ 可改善" if improvement > 0 else "⚠️ 難改善"
        
        print(f"\n   {ts}")
        print(f"   策略: {strategy} {prob:.0%} | OBI: {obi:.2f}")
        print(f"   走勢: 最高+{max_p:.1f}%, 最低-{max_dd:.1f}%")
        print(f"   PnL: ${pnl:.2f} → v5.9.1: ${v591_simulated:.2f}")
        print(f"   類型: {loss_type} | {can_improve}")
    
    # 計算理論最佳
    print(f"\n" + "=" * 80)
    print("📊 理論最佳情況分析")
    print("=" * 80)
    
    # 假設所有交易都能在最高點出場
    best_case_pnl = 0
    for t in v57_trades:
        max_p = t.get('max_profit_pct', 0)
        position = t.get('position_size_usdt', 100)
        fee = 4
        
        if max_p > 0:
            profit = position * (max_p / 100) - fee
        else:
            profit = -fee  # 最差情況只虧手續費
        
        best_case_pnl += profit
    
    actual_pnl = sum(t.get('net_pnl_usdt', 0) for t in v57_trades)
    
    print(f"   實際 PnL: ${actual_pnl:.2f}")
    print(f"   理論最佳 (在最高點全部出場): ${best_case_pnl:.2f}")
    print(f"   差距: ${best_case_pnl - actual_pnl:.2f}")
    
    # 分析獲利交易的出場時機
    print(f"\n📋 獲利交易出場效率:")
    for t in wins:
        ts = t.get('timestamp', '')[:16]
        max_p = t.get('max_profit_pct', 0)
        pnl = t.get('net_pnl_usdt', 0)
        position = t.get('position_size_usdt', 100)
        fee = 4
        
        best_pnl = position * (max_p / 100) - fee if max_p > 0 else -fee
        efficiency = (pnl + fee) / (best_pnl + fee) * 100 if best_pnl > -fee else 0
        
        print(f"   {ts} | 實際 ${pnl:+.2f} / 最佳 ${best_pnl:+.2f} | 效率 {efficiency:.0f}%")
    
    avg_efficiency = sum(
        ((t.get('net_pnl_usdt', 0) + 4) / (100 * t.get('max_profit_pct', 1) / 100) * 100)
        for t in wins if t.get('max_profit_pct', 0) > 0
    ) / len(wins) if wins else 0
    
    print(f"\n   平均出場效率: {avg_efficiency:.0f}%")
    
    # 建議
    print(f"\n" + "=" * 80)
    print("💡 優化建議")
    print("=" * 80)
    
    print(f"""
    1. 無動能止損 (已實作 v5.9)
       - 進場後從未漲超過 1% 且虧損 5% 時提早止損
       - 可挽救 5 筆交易，省 $18
    
    2. 先漲保護 (已實作 v5.9.1)
       - 曾漲超過 4% 後回撤到 0% 時保本出場
       - 可挽救 2 筆交易，省 $10
    
    3. ⚠️ 剩餘虧損難以避免
       - 部分交易一進場就小漲 1-2% 後暴跌
       - 這類交易既不觸發無動能止損，也不觸發先漲保護
       - 只能靠更嚴格的進場過濾
    
    4. 💡 可能的額外優化
       - 時段過濾：分析虧損交易的時間分布
       - 連續交易限制：虧損後暫停 N 分鐘
       - 市場狀態過濾：高波動時段不交易
    """)

if __name__ == "__main__":
    main()
