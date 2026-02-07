#!/usr/bin/env python3
"""
分析過濾條件嚴格程度
檢查混沌過濾和MTF過濾是否太嚴格
"""

import json
import os
from datetime import datetime

def analyze_trades():
    """分析之前的交易在新過濾邏輯下會如何處理"""
    
    log_dir = 'logs/whale_paper_trader'
    
    # 指定分析哪個檔案 - 使用有27筆交易的那個
    trade_file = 'trades_20251202_184133.json'
    
    if not os.path.exists(os.path.join(log_dir, trade_file)):
        trade_files = [f for f in os.listdir(log_dir) if f.startswith('trades_') and f.endswith('.json')]
        if not trade_files:
            print("找不到交易記錄")
            return
        trade_file = sorted(trade_files)[-1]
    
    with open(os.path.join(log_dir, trade_file)) as f:
        data = json.load(f)
    
    print("=" * 70)
    print("過濾條件嚴格程度分析")
    print("=" * 70)
    print(f"交易記錄: {trade_file}")
    print(f"總交易數: {len(data['trades'])}")
    print()
    
    # 統計
    chaos_filtered = 0
    mtf_filtered = 0
    would_pass = 0
    
    for i, trade in enumerate(data['trades']):
        print(f"Trade #{i+1}: {trade['direction']} {trade['strategy']} ({trade['probability']:.1%})")
        print(f"  結果: {trade['status']} | 淨利: ${trade.get('net_pnl_usdt', 0):.2f}")
        
        probs = trade.get('strategy_probs', {})
        
        # 混沌過濾分析
        if trade['direction'] == 'LONG':
            conflict_strats = ['DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 
                              'LONG_SQUEEZE', 'PUMP_DUMP', 'SLOW_BLEED']
        else:
            conflict_strats = ['ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 
                              'SHORT_SQUEEZE', 'FLASH_CRASH']
        
        # 只看 >= 50% 的矛盾策略
        conflicts = [(s, probs.get(s, 0)) for s in conflict_strats if probs.get(s, 0) >= 0.5]
        
        chaos_block = False
        if conflicts:
            total_conflict = sum(p for _, p in conflicts)
            main_prob = trade['probability']
            threshold = main_prob * 0.6
            
            print(f"  矛盾策略: {', '.join([f'{s}({p:.0%})' for s,p in conflicts])}")
            print(f"  矛盾總機率: {total_conflict:.1%} vs 門檻: {threshold:.1%}")
            
            if total_conflict > threshold:
                print(f"  >>> 混沌過濾: 會被阻擋!")
                chaos_block = True
                chaos_filtered += 1
        
        if not chaos_block:
            would_pass += 1
            result_emoji = "✅" if trade.get('net_pnl_usdt', 0) > 0 else "❌"
            print(f"  {result_emoji} 混沌過濾: 會通過")
        
        print()
    
    # 統計結果
    print("=" * 70)
    print("過濾統計")
    print("=" * 70)
    total = len(data['trades'])
    print(f"總交易: {total}")
    print(f"混沌過濾阻擋: {chaos_filtered} ({chaos_filtered/total*100:.1f}%)")
    print(f"會通過交易: {would_pass} ({would_pass/total*100:.1f}%)")
    
    # 分析過濾效果
    print()
    print("=" * 70)
    print("過濾效果分析")
    print("=" * 70)
    
    pass_trades = []
    block_trades = []
    
    for trade in data['trades']:
        probs = trade.get('strategy_probs', {})
        
        if trade['direction'] == 'LONG':
            conflict_strats = ['DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 
                              'LONG_SQUEEZE', 'PUMP_DUMP', 'SLOW_BLEED']
        else:
            conflict_strats = ['ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 
                              'SHORT_SQUEEZE', 'FLASH_CRASH']
        
        conflicts = [(s, probs.get(s, 0)) for s in conflict_strats if probs.get(s, 0) >= 0.5]
        
        if conflicts:
            total_conflict = sum(p for _, p in conflicts)
            if total_conflict > trade['probability'] * 0.6:
                block_trades.append(trade)
                continue
        
        pass_trades.append(trade)
    
    # 計算過濾效果
    if block_trades:
        block_pnl = sum(t.get('net_pnl_usdt', 0) for t in block_trades)
        print(f"被過濾的交易損益總和: ${block_pnl:.2f}")
        if block_pnl < 0:
            print("  >>> 好的過濾! 避免了虧損")
        else:
            print("  >>> 過度過濾! 錯過了獲利")
    
    if pass_trades:
        pass_pnl = sum(t.get('net_pnl_usdt', 0) for t in pass_trades)
        print(f"通過的交易損益總和: ${pass_pnl:.2f}")
    
    print()
    print("=" * 70)
    print("建議")
    print("=" * 70)
    
    filter_rate = chaos_filtered / total * 100 if total > 0 else 0
    
    if filter_rate > 80:
        print("⚠️ 過濾率太高 (>80%)，可能會錯過好機會")
        print("   建議: 提高混沌門檻 (0.6 -> 0.8)")
    elif filter_rate > 50:
        print("⚠️ 過濾率偏高 (50-80%)")
        print("   建議: 觀察一下，可能需要微調")
    elif filter_rate < 20:
        print("⚠️ 過濾率太低 (<20%)，可能沒有起到作用")
        print("   建議: 降低混沌門檻 (0.6 -> 0.4)")
    else:
        print("✅ 過濾率合理 (20-50%)")
    
    # 分析各策略出現頻率
    print()
    print("=" * 70)
    print("策略機率分佈分析")
    print("=" * 70)
    
    all_probs = {}
    for trade in data['trades']:
        for strat, prob in trade.get('strategy_probs', {}).items():
            if strat not in all_probs:
                all_probs[strat] = []
            all_probs[strat].append(prob)
    
    print(f"{'策略':<20} {'平均機率':>10} {'最高':>8} {'>=50%次數':>10}")
    print("-" * 50)
    for strat in sorted(all_probs.keys()):
        probs = all_probs[strat]
        avg = sum(probs) / len(probs)
        max_p = max(probs)
        high_count = sum(1 for p in probs if p >= 0.5)
        print(f"{strat:<20} {avg:>9.1%} {max_p:>7.1%} {high_count:>10}")


def simulate_different_thresholds():
    """模擬不同門檻的過濾效果"""
    
    log_dir = 'logs/whale_paper_trader'
    
    # 指定分析哪個檔案 - 使用有27筆交易的那個
    trade_file = 'trades_20251202_184133.json'
    
    if not os.path.exists(os.path.join(log_dir, trade_file)):
        trade_files = [f for f in os.listdir(log_dir) if f.startswith('trades_') and f.endswith('.json')]
        if not trade_files:
            return
        trade_file = sorted(trade_files)[-1]
    
    with open(os.path.join(log_dir, trade_file)) as f:
        data = json.load(f)
    
    print()
    print("=" * 70)
    print("不同門檻模擬")
    print("=" * 70)
    
    thresholds = [0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
    
    print(f"{'門檻':<8} {'過濾數':>8} {'通過數':>8} {'過濾損益':>12} {'通過損益':>12}")
    print("-" * 50)
    
    for threshold in thresholds:
        pass_trades = []
        block_trades = []
        
        for trade in data['trades']:
            probs = trade.get('strategy_probs', {})
            
            if trade['direction'] == 'LONG':
                conflict_strats = ['DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 
                                  'LONG_SQUEEZE', 'PUMP_DUMP', 'SLOW_BLEED']
            else:
                conflict_strats = ['ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 
                                  'SHORT_SQUEEZE', 'FLASH_CRASH']
            
            conflicts = [(s, probs.get(s, 0)) for s in conflict_strats if probs.get(s, 0) >= 0.5]
            
            if conflicts:
                total_conflict = sum(p for _, p in conflicts)
                if total_conflict > trade['probability'] * threshold:
                    block_trades.append(trade)
                    continue
            
            pass_trades.append(trade)
        
        block_pnl = sum(t.get('net_pnl_usdt', 0) for t in block_trades)
        pass_pnl = sum(t.get('net_pnl_usdt', 0) for t in pass_trades)
        
        marker = " <-- 目前" if threshold == 0.6 else ""
        print(f"{threshold:<8.1f} {len(block_trades):>8} {len(pass_trades):>8} ${block_pnl:>10.2f} ${pass_pnl:>10.2f}{marker}")


if __name__ == "__main__":
    analyze_trades()
    simulate_different_thresholds()
