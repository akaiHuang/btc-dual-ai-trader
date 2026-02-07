#!/usr/bin/env python3
"""
深度價格分析 - 找出真正的獲利關鍵
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
    print("🔍 深度價格分析 - 找出虧損根源")
    print("=" * 80)
    
    # 關鍵發現：持倉 5-10 分鐘勝率 81.8%！
    print("\n📊 1. 持倉時間是關鍵！")
    print("-" * 60)
    
    short_hold = [t for t in trades if t.get('hold_seconds', 0) < 300]  # < 5分鐘
    long_hold = [t for t in trades if t.get('hold_seconds', 0) >= 300]   # >= 5分鐘
    
    short_wins = len([t for t in short_hold if t.get('net_pnl_usdt', 0) > 0])
    long_wins = len([t for t in long_hold if t.get('net_pnl_usdt', 0) > 0])
    
    short_pnl = sum(t.get('net_pnl_usdt', 0) for t in short_hold)
    long_pnl = sum(t.get('net_pnl_usdt', 0) for t in long_hold)
    
    print(f"   < 5分鐘: {len(short_hold)}筆 | 勝率 {short_wins/len(short_hold)*100:.1f}% | PnL ${short_pnl:.2f}")
    print(f"   >= 5分鐘: {len(long_hold)}筆 | 勝率 {long_wins/len(long_hold)*100:.1f}% | PnL ${long_pnl:.2f}")
    
    # 分析 SMART_TP 太早出場的問題
    print("\n📊 2. SMART_TP 是否太早出場？")
    print("-" * 60)
    
    smart_tp = [t for t in trades if t.get('status') == 'CLOSED_SMART_TP']
    for t in smart_tp[:10]:
        max_profit = t.get('max_profit_pct', 0)
        actual_pnl = t.get('net_pnl_usdt', 0)
        hold = t.get('hold_seconds', 0)
        print(f"   持倉 {hold:.0f}秒 | 最高 +{max_profit:.1f}% | 實際 ${actual_pnl:.2f}")
    
    avg_smart_tp_profit = sum(t.get('max_profit_pct', 0) for t in smart_tp) / len(smart_tp) if smart_tp else 0
    print(f"\n   SMART_TP 平均最高獲利: {avg_smart_tp_profit:.1f}%")
    print(f"   如果等到 +5%，有多少能達到？")
    
    could_reach_5 = len([t for t in smart_tp if t.get('max_profit_pct', 0) >= 5])
    print(f"   答案: {could_reach_5}/{len(smart_tp)} ({could_reach_5/len(smart_tp)*100:.0f}%)")
    
    # 分析止損交易
    print("\n📊 3. 止損交易分析")
    print("-" * 60)
    
    sl_trades = [t for t in trades if t.get('status') == 'CLOSED_SL']
    
    # 這些止損交易有多少曾經獲利？
    sl_had_profit = [t for t in sl_trades if t.get('max_profit_pct', 0) > 0]
    print(f"   止損交易中曾獲利的: {len(sl_had_profit)}/{len(sl_trades)}")
    
    if sl_had_profit:
        for t in sl_had_profit[:5]:
            max_profit = t.get('max_profit_pct', 0)
            max_dd = t.get('max_drawdown_pct', 0)
            hold = t.get('hold_seconds', 0)
            print(f"      曾 +{max_profit:.1f}% → 最終 {max_dd:.1f}% 止損 | 持倉 {hold:.0f}秒")
    
    # 真正的問題：風險報酬比
    print("\n📊 4. 核心問題：風險報酬比失衡")
    print("-" * 60)
    
    wins = [t for t in trades if t.get('net_pnl_usdt', 0) > 0]
    losses = [t for t in trades if t.get('net_pnl_usdt', 0) <= 0]
    
    avg_win = sum(t.get('net_pnl_usdt', 0) for t in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(t.get('net_pnl_usdt', 0) for t in losses) / len(losses)) if losses else 0
    
    print(f"   平均獲利: +${avg_win:.2f}")
    print(f"   平均虧損: -${avg_loss:.2f}")
    print(f"   風險報酬比: 1:{avg_loss/avg_win:.1f} (虧損是獲利的 {avg_loss/avg_win:.1f} 倍！)")
    
    # 計算需要的勝率
    required_winrate = avg_loss / (avg_win + avg_loss) * 100
    print(f"\n   要打平，需要勝率: {required_winrate:.1f}%")
    print(f"   目前勝率: {len(wins)/len(trades)*100:.1f}%")
    
    # 解決方案模擬
    print("\n📊 5. 解決方案模擬")
    print("-" * 60)
    
    # 方案 A: 減少止損
    print("\n   方案 A: 縮小止損到 -$8 (原 -$14)")
    new_sl = 8
    simulated_pnl_a = 0
    for t in trades:
        pnl = t.get('net_pnl_usdt', 0)
        if pnl < 0:
            simulated_pnl_a += max(pnl, -new_sl)  # 限制最大虧損
        else:
            simulated_pnl_a += pnl
    print(f"      模擬 PnL: ${simulated_pnl_a:.2f}")
    
    # 方案 B: 增加止盈
    print("\n   方案 B: 提高止盈到 +$5 (原 +$3)")
    # 假設能多持倉到 +5%
    simulated_pnl_b = 0
    for t in trades:
        pnl = t.get('net_pnl_usdt', 0)
        max_profit_pct = t.get('max_profit_pct', 0)
        if pnl > 0 and max_profit_pct >= 5:
            simulated_pnl_b += 5 - 4  # $5 獲利 - $4 手續費
        else:
            simulated_pnl_b += pnl
    print(f"      模擬 PnL: ${simulated_pnl_b:.2f}")
    
    # 方案 C: 只在高勝率時段交易 (5-10分鐘)
    print("\n   方案 C: 只計算 5-10 分鐘持倉的交易")
    mid_hold = [t for t in trades if 300 <= t.get('hold_seconds', 0) < 600]
    if mid_hold:
        mid_pnl = sum(t.get('net_pnl_usdt', 0) for t in mid_hold)
        mid_wins = len([t for t in mid_hold if t.get('net_pnl_usdt', 0) > 0])
        print(f"      {len(mid_hold)}筆 | 勝率 {mid_wins/len(mid_hold)*100:.1f}% | PnL ${mid_pnl:.2f}")
    
    # 方案 D: 組合優化
    print("\n   方案 D: 移動止損 (獲利 2% 後把止損移到成本價)")
    simulated_pnl_d = 0
    saved_by_trailing = 0
    for t in trades:
        pnl = t.get('net_pnl_usdt', 0)
        max_profit_pct = t.get('max_profit_pct', 0)
        
        if pnl < 0 and max_profit_pct >= 2:
            # 曾經獲利 2%，如果有移動止損，至少保本
            simulated_pnl_d += -4  # 只損失手續費
            saved_by_trailing += 1
        else:
            simulated_pnl_d += pnl
    
    print(f"      模擬 PnL: ${simulated_pnl_d:.2f}")
    print(f"      移動止損救回: {saved_by_trailing}筆")
    
    # 最終建議
    print("\n" + "=" * 80)
    print("🎯 最終優化建議")
    print("=" * 80)
    
    print("""
📌 核心問題：
   - 平均獲利 +$3，平均虧損 -$13 (1:4 風險報酬比)
   - 需要 80%+ 勝率才能打平，目前只有 54%

📌 解決方案：

   1. 【移動止損】獲利 2% 後，把止損移到成本價
      - 可救回 8 筆本來虧損的交易
      
   2. 【延長持倉】從 3 分鐘延長到 5-10 分鐘
      - 5-10 分鐘持倉勝率高達 81.8%！
      
   3. 【提高止盈】從 0.12% 提高到 0.2%
      - 讓獲利交易有更多利潤空間
      
   4. 【縮小止損】從 0.12% 縮小到 0.08%
      - 減少每筆虧損金額
""")

if __name__ == "__main__":
    main()
