#!/usr/bin/env python3
"""
價格分析優化 - 從歷史交易中找出最佳止盈止損策略
"""

import json
from pathlib import Path
from typing import Dict, List
from collections import defaultdict

def load_all_trades():
    """載入所有交易"""
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
    
    return all_trades

def analyze_price_patterns(trades: List[Dict]):
    """分析價格模式"""
    
    print("=" * 80)
    print("📊 價格模式分析")
    print("=" * 80)
    
    # 1. 分析止盈止損的觸發情況
    print("\n📈 1. 出場原因分析")
    print("-" * 60)
    
    exit_reasons = defaultdict(lambda: {'count': 0, 'pnl': 0, 'trades': []})
    
    for t in trades:
        status = t.get('status', 'UNKNOWN')
        pnl = t.get('net_pnl_usdt', 0)
        exit_reasons[status]['count'] += 1
        exit_reasons[status]['pnl'] += pnl
        exit_reasons[status]['trades'].append(t)
    
    for status, data in sorted(exit_reasons.items(), key=lambda x: x[1]['count'], reverse=True):
        avg = data['pnl'] / data['count'] if data['count'] > 0 else 0
        print(f"   {status}: {data['count']}筆 | 總PnL ${data['pnl']:.2f} | 平均 ${avg:.2f}")
    
    # 2. 分析最大獲利與最大虧損
    print("\n📈 2. 持倉期間價格波動分析")
    print("-" * 60)
    
    max_profits = []
    max_drawdowns = []
    
    for t in trades:
        max_profit = t.get('max_profit_pct', 0)
        max_dd = t.get('max_drawdown_pct', 0)
        pnl = t.get('net_pnl_usdt', 0)
        
        max_profits.append(max_profit)
        max_drawdowns.append(max_dd)
    
    if max_profits:
        avg_max_profit = sum(max_profits) / len(max_profits)
        avg_max_dd = sum(max_drawdowns) / len(max_drawdowns)
        print(f"   平均最大獲利: {avg_max_profit:.2f}%")
        print(f"   平均最大回撤: {avg_max_dd:.2f}%")
    
    # 3. 分析獲利交易 vs 虧損交易的特徵
    print("\n📈 3. 獲利 vs 虧損交易特徵")
    print("-" * 60)
    
    wins = [t for t in trades if t.get('net_pnl_usdt', 0) > 0]
    losses = [t for t in trades if t.get('net_pnl_usdt', 0) <= 0]
    
    if wins:
        win_max_profit = sum(t.get('max_profit_pct', 0) for t in wins) / len(wins)
        win_max_dd = sum(t.get('max_drawdown_pct', 0) for t in wins) / len(wins)
        win_hold = sum(t.get('hold_seconds', 0) for t in wins) / len(wins)
        win_pnl = sum(t.get('net_pnl_usdt', 0) for t in wins) / len(wins)
        print(f"   ✅ 獲利交易 ({len(wins)}筆):")
        print(f"      平均獲利: ${win_pnl:.2f}")
        print(f"      平均最大獲利: {win_max_profit:.2f}%")
        print(f"      平均最大回撤: {win_max_dd:.2f}%")
        print(f"      平均持倉: {win_hold:.0f}秒 ({win_hold/60:.1f}分)")
    
    if losses:
        loss_max_profit = sum(t.get('max_profit_pct', 0) for t in losses) / len(losses)
        loss_max_dd = sum(t.get('max_drawdown_pct', 0) for t in losses) / len(losses)
        loss_hold = sum(t.get('hold_seconds', 0) for t in losses) / len(losses)
        loss_pnl = sum(t.get('net_pnl_usdt', 0) for t in losses) / len(losses)
        print(f"   ❌ 虧損交易 ({len(losses)}筆):")
        print(f"      平均虧損: ${loss_pnl:.2f}")
        print(f"      平均最大獲利: {loss_max_profit:.2f}%")
        print(f"      平均最大回撤: {loss_max_dd:.2f}%")
        print(f"      平均持倉: {loss_hold:.0f}秒 ({loss_hold/60:.1f}分)")
    
    # 4. 關鍵發現：虧損交易曾經獲利嗎？
    print("\n📈 4. 關鍵發現：虧損交易是否曾經獲利？")
    print("-" * 60)
    
    loss_had_profit = [t for t in losses if t.get('max_profit_pct', 0) > 1]
    loss_never_profit = [t for t in losses if t.get('max_profit_pct', 0) <= 1]
    
    print(f"   虧損但曾獲利 >1%: {len(loss_had_profit)}筆")
    if loss_had_profit:
        for t in loss_had_profit[:5]:
            print(f"      - 最高 +{t.get('max_profit_pct', 0):.1f}% → 最終 ${t.get('net_pnl_usdt', 0):.2f}")
    
    print(f"   虧損且從未獲利: {len(loss_never_profit)}筆")
    
    # 5. 模擬不同止盈點的效果
    print("\n📈 5. 不同止盈點模擬 (假設完美執行)")
    print("-" * 60)
    
    # 計算如果在不同的最大獲利點出場，結果會如何
    tp_levels = [1, 2, 3, 4, 5, 6, 8, 10]  # %
    
    for tp in tp_levels:
        simulated_pnl = 0
        wins_at_tp = 0
        
        for t in trades:
            max_profit = t.get('max_profit_pct', 0)
            actual_pnl = t.get('net_pnl_usdt', 0)
            leverage = t.get('leverage', 100)
            position = t.get('position_size_usdt', 100)
            fee = t.get('fee_usdt', 4)
            
            if max_profit >= tp:
                # 能夠在 tp% 出場
                gross_profit = position * (tp / 100)
                net_profit = gross_profit - fee
                simulated_pnl += net_profit
                wins_at_tp += 1
            else:
                # 無法達到 tp%，使用實際結果
                simulated_pnl += actual_pnl
        
        winrate = wins_at_tp / len(trades) * 100
        avg_pnl = simulated_pnl / len(trades)
        icon = "✅" if simulated_pnl > 0 else "⚠️" if simulated_pnl > -50 else "❌"
        print(f"   {icon} TP={tp}%: 勝率 {winrate:.1f}% | 總PnL ${simulated_pnl:.2f} | 平均 ${avg_pnl:.2f}")
    
    # 6. 模擬不同止損點的效果
    print("\n📈 6. 不同止損點模擬")
    print("-" * 60)
    
    sl_levels = [3, 4, 5, 6, 8, 10, 12]  # %
    
    for sl in sl_levels:
        simulated_pnl = 0
        losses_at_sl = 0
        
        for t in trades:
            max_dd = abs(t.get('max_drawdown_pct', 0))
            actual_pnl = t.get('net_pnl_usdt', 0)
            position = t.get('position_size_usdt', 100)
            fee = t.get('fee_usdt', 4)
            
            if max_dd >= sl:
                # 會觸發止損
                gross_loss = position * (sl / 100)
                net_loss = -gross_loss - fee
                simulated_pnl += net_loss
                losses_at_sl += 1
            else:
                # 不會觸發止損，使用實際結果
                simulated_pnl += actual_pnl
        
        loss_rate = losses_at_sl / len(trades) * 100
        avg_pnl = simulated_pnl / len(trades)
        icon = "✅" if simulated_pnl > 0 else "⚠️" if simulated_pnl > -50 else "❌"
        print(f"   {icon} SL={sl}%: 觸發率 {loss_rate:.1f}% | 總PnL ${simulated_pnl:.2f} | 平均 ${avg_pnl:.2f}")
    
    # 7. 組合優化：找最佳 TP/SL 組合
    print("\n📈 7. 最佳 TP/SL 組合搜索")
    print("-" * 60)
    
    best_combo = None
    best_pnl = -999999
    
    results = []
    
    for tp in [2, 3, 4, 5, 6, 8]:
        for sl in [3, 4, 5, 6, 8, 10]:
            simulated_pnl = 0
            wins = 0
            
            for t in trades:
                max_profit = t.get('max_profit_pct', 0)
                max_dd = abs(t.get('max_drawdown_pct', 0))
                position = t.get('position_size_usdt', 100)
                fee = t.get('fee_usdt', 4)
                
                # 假設先觸發的先執行 (簡化：看哪個數值大)
                if max_profit >= tp and max_dd < sl:
                    # 止盈成功
                    net = position * (tp / 100) - fee
                    simulated_pnl += net
                    wins += 1
                elif max_dd >= sl:
                    # 止損觸發
                    net = -position * (sl / 100) - fee
                    simulated_pnl += net
                else:
                    # 兩者都沒觸發，使用實際結果
                    simulated_pnl += t.get('net_pnl_usdt', 0)
                    if t.get('net_pnl_usdt', 0) > 0:
                        wins += 1
            
            winrate = wins / len(trades) * 100
            results.append({
                'tp': tp,
                'sl': sl,
                'pnl': simulated_pnl,
                'winrate': winrate,
                'rr': tp / sl  # 風險報酬比
            })
            
            if simulated_pnl > best_pnl:
                best_pnl = simulated_pnl
                best_combo = (tp, sl, winrate)
    
    # 排序並顯示前 10 名
    results.sort(key=lambda x: x['pnl'], reverse=True)
    
    print(f"\n   🏆 最佳組合 TOP 10:")
    for i, r in enumerate(results[:10]):
        icon = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "  "
        status = "✅" if r['pnl'] > 0 else "⚠️"
        print(f"   {icon} TP={r['tp']}% SL={r['sl']}% | 勝率 {r['winrate']:.1f}% | PnL ${r['pnl']:.2f} | RR 1:{r['rr']:.1f}")
    
    if best_combo:
        print(f"\n   🎯 建議設定: TP={best_combo[0]}% SL={best_combo[1]}% (勝率 {best_combo[2]:.1f}%)")
    
    # 8. 持倉時間分析
    print("\n📈 8. 持倉時間分析")
    print("-" * 60)
    
    time_buckets = [
        (0, 60, "0-1分鐘"),
        (60, 180, "1-3分鐘"),
        (180, 300, "3-5分鐘"),
        (300, 600, "5-10分鐘"),
        (600, 99999, "10分鐘+"),
    ]
    
    for low, high, name in time_buckets:
        bucket_trades = [t for t in trades if low <= t.get('hold_seconds', 0) < high]
        if bucket_trades:
            wins = len([t for t in bucket_trades if t.get('net_pnl_usdt', 0) > 0])
            pnl = sum(t.get('net_pnl_usdt', 0) for t in bucket_trades)
            winrate = wins / len(bucket_trades) * 100
            icon = "✅" if winrate >= 55 and pnl > 0 else "⚠️" if winrate >= 50 else "❌"
            print(f"   {icon} {name}: {len(bucket_trades)}筆 | 勝率 {winrate:.1f}% | PnL ${pnl:.2f}")
    
    return best_combo

def main():
    trades = load_all_trades()
    print(f"📊 載入 {len(trades)} 筆交易進行價格分析\n")
    
    best = analyze_price_patterns(trades)
    
    print("\n" + "=" * 80)
    print("💡 優化建議總結")
    print("=" * 80)
    
    if best:
        print(f"""
根據 {len(trades)} 筆歷史交易分析:

🎯 最佳止盈止損設定:
   止盈 (TP): {best[0]}%
   止損 (SL): {best[1]}%
   預期勝率: {best[2]:.1f}%
   風險報酬比: 1:{best[0]/best[1]:.1f}

📝 實作建議:
   1. 修改 target_profit_pct 為 {best[0] / 100:.4f}
   2. 修改 stop_loss_pct 為 {best[1] / 100:.4f}
   3. 使用 100x 槓桿時，{best[0]}% 價格變動 = {best[0]}% 獲利
""")

if __name__ == "__main__":
    main()
