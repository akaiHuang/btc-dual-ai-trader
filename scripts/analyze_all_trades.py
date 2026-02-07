#!/usr/bin/env python3
"""
分析所有交易記錄的過濾效果
合併所有 trades_*.json 檔案
"""

import json
import os
from collections import defaultdict

def load_all_trades():
    """載入所有交易記錄"""
    log_dir = 'logs/whale_paper_trader'
    all_trades = []
    
    for f in sorted(os.listdir(log_dir)):
        if f.startswith('trades_') and f.endswith('.json'):
            try:
                with open(os.path.join(log_dir, f)) as fp:
                    data = json.load(fp)
                    trades = data.get('trades', [])
                    # 只取已關閉的交易
                    closed = [t for t in trades if t.get('status', '').startswith('CLOSED')]
                    all_trades.extend(closed)
            except Exception as e:
                print(f"讀取 {f} 失敗: {e}")
    
    return all_trades


def analyze_chaos_filter(trades, threshold=0.6, min_conflict_prob=0.5):
    """分析混沌過濾效果"""
    
    pass_trades = []
    block_trades = []
    
    for trade in trades:
        probs = trade.get('strategy_probs', {})
        direction = trade.get('direction', '')
        main_prob = trade.get('probability', 0)
        
        # 定義矛盾策略
        if direction == 'LONG':
            conflict_strats = ['DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 
                              'LONG_SQUEEZE', 'PUMP_DUMP', 'SLOW_BLEED']
        else:
            conflict_strats = ['ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 
                              'SHORT_SQUEEZE', 'FLASH_CRASH']
        
        # 找高機率矛盾策略
        conflicts = [(s, probs.get(s, 0)) for s in conflict_strats 
                     if probs.get(s, 0) >= min_conflict_prob]
        
        should_block = False
        if conflicts:
            total_conflict = sum(p for _, p in conflicts)
            if total_conflict > main_prob * threshold:
                should_block = True
        
        if should_block:
            block_trades.append(trade)
        else:
            pass_trades.append(trade)
    
    return pass_trades, block_trades


def calculate_stats(trades):
    """計算交易統計"""
    if not trades:
        return {'count': 0, 'wins': 0, 'losses': 0, 'win_rate': 0, 'total_pnl': 0}
    
    wins = [t for t in trades if t.get('net_pnl_usdt', 0) > 0]
    losses = [t for t in trades if t.get('net_pnl_usdt', 0) <= 0]
    total_pnl = sum(t.get('net_pnl_usdt', 0) for t in trades)
    
    return {
        'count': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': len(wins) / len(trades) * 100 if trades else 0,
        'total_pnl': total_pnl
    }


def main():
    trades = load_all_trades()
    
    print("=" * 80)
    print("所有交易的過濾效果分析")
    print("=" * 80)
    print(f"總交易數: {len(trades)}")
    
    # 原始統計
    original_stats = calculate_stats(trades)
    print(f"\n原始數據:")
    print(f"  勝率: {original_stats['win_rate']:.1f}% ({original_stats['wins']}/{original_stats['count']})")
    print(f"  總損益: ${original_stats['total_pnl']:.2f}")
    
    print("\n" + "=" * 80)
    print("不同混沌過濾門檻的效果")
    print("=" * 80)
    
    print(f"\n{'門檻':<8} {'過濾數':>8} {'通過數':>8} | {'過濾勝率':>10} {'過濾損益':>12} | {'通過勝率':>10} {'通過損益':>12}")
    print("-" * 90)
    
    best_threshold = None
    best_pnl = float('-inf')
    
    for threshold in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        pass_trades, block_trades = analyze_chaos_filter(trades, threshold)
        
        pass_stats = calculate_stats(pass_trades)
        block_stats = calculate_stats(block_trades)
        
        marker = " <--" if threshold == 0.6 else ""
        
        block_wr = f"{block_stats['win_rate']:.1f}%" if block_stats['count'] > 0 else "N/A"
        block_pnl = f"${block_stats['total_pnl']:.2f}" if block_stats['count'] > 0 else "N/A"
        
        print(f"{threshold:<8.1f} {block_stats['count']:>8} {pass_stats['count']:>8} | "
              f"{block_wr:>10} {block_pnl:>12} | "
              f"{pass_stats['win_rate']:>9.1f}% ${pass_stats['total_pnl']:>10.2f}{marker}")
        
        if pass_stats['total_pnl'] > best_pnl:
            best_pnl = pass_stats['total_pnl']
            best_threshold = threshold
    
    print(f"\n最佳門檻: {best_threshold} (通過交易損益: ${best_pnl:.2f})")
    
    # 詳細分析被過濾的交易
    print("\n" + "=" * 80)
    print("使用門檻 0.6 的詳細分析")
    print("=" * 80)
    
    pass_trades, block_trades = analyze_chaos_filter(trades, 0.6)
    
    print(f"\n會被過濾的交易 ({len(block_trades)} 筆):")
    print("-" * 60)
    
    for i, t in enumerate(block_trades):
        probs = t.get('strategy_probs', {})
        direction = t.get('direction', '')
        
        if direction == 'LONG':
            conflict_strats = ['DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 
                              'LONG_SQUEEZE', 'PUMP_DUMP', 'SLOW_BLEED']
        else:
            conflict_strats = ['ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 
                              'SHORT_SQUEEZE', 'FLASH_CRASH']
        
        conflicts = [(s, probs.get(s, 0)) for s in conflict_strats if probs.get(s, 0) >= 0.5]
        
        result = "WIN" if t.get('net_pnl_usdt', 0) > 0 else "LOSS"
        pnl = t.get('net_pnl_usdt', 0)
        
        print(f"  {i+1}. {direction} {t.get('strategy')} ({t.get('probability', 0):.0%})")
        print(f"     矛盾: {', '.join([f'{s}({p:.0%})' for s,p in conflicts])}")
        print(f"     結果: {result} ${pnl:.2f}")
    
    # 分析矛盾策略出現模式
    print("\n" + "=" * 80)
    print("矛盾策略出現頻率分析")
    print("=" * 80)
    
    conflict_counts = defaultdict(int)
    conflict_when_win = defaultdict(int)
    conflict_when_loss = defaultdict(int)
    
    for t in trades:
        probs = t.get('strategy_probs', {})
        direction = t.get('direction', '')
        is_win = t.get('net_pnl_usdt', 0) > 0
        
        if direction == 'LONG':
            conflict_strats = ['DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 
                              'LONG_SQUEEZE', 'PUMP_DUMP', 'SLOW_BLEED']
        else:
            conflict_strats = ['ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 
                              'SHORT_SQUEEZE', 'FLASH_CRASH']
        
        for strat in conflict_strats:
            if probs.get(strat, 0) >= 0.5:
                conflict_counts[strat] += 1
                if is_win:
                    conflict_when_win[strat] += 1
                else:
                    conflict_when_loss[strat] += 1
    
    print(f"\n{'矛盾策略':<20} {'出現次數':>10} {'獲利時出現':>12} {'虧損時出現':>12}")
    print("-" * 60)
    
    for strat in sorted(conflict_counts.keys(), key=lambda x: conflict_counts[x], reverse=True):
        cnt = conflict_counts[strat]
        win = conflict_when_win[strat]
        loss = conflict_when_loss[strat]
        print(f"{strat:<20} {cnt:>10} {win:>12} {loss:>12}")
    
    # 有無strategy_probs的分析
    print("\n" + "=" * 80)
    print("數據完整性分析")
    print("=" * 80)
    
    with_probs = [t for t in trades if t.get('strategy_probs') and len(t.get('strategy_probs', {})) > 1]
    without_probs = [t for t in trades if not t.get('strategy_probs') or len(t.get('strategy_probs', {})) <= 1]
    
    print(f"有完整策略機率的交易: {len(with_probs)} 筆")
    print(f"缺少策略機率的交易: {len(without_probs)} 筆")
    
    if without_probs:
        print("\n缺少機率數據的交易將無法被過濾，這可能是個問題！")
    
    # MTF 過濾分析 (如果有數據)
    print("\n" + "=" * 80)
    print("建議")
    print("=" * 80)
    
    pass_stats = calculate_stats(pass_trades)
    block_stats = calculate_stats(block_trades)
    
    if block_stats['count'] > 0:
        if block_stats['total_pnl'] < 0:
            saved = abs(block_stats['total_pnl'])
            print(f"✅ 混沌過濾有效！可以避免 ${saved:.2f} 的虧損")
        else:
            missed = block_stats['total_pnl']
            print(f"⚠️ 混沌過濾會錯過 ${missed:.2f} 的獲利")
    
    filter_rate = len(block_trades) / len(trades) * 100 if trades else 0
    
    if filter_rate > 50:
        print(f"⚠️ 過濾率較高 ({filter_rate:.1f}%)，可能過於保守")
        print("   建議: 調高門檻到 0.7 或 0.8")
    elif filter_rate < 10:
        print(f"⚠️ 過濾率太低 ({filter_rate:.1f}%)，可能沒作用")
        print("   建議: 調低門檻到 0.4 或 0.5")
    else:
        print(f"✅ 過濾率適中 ({filter_rate:.1f}%)")
    
    # 最終建議
    print("\n" + "-" * 40)
    
    if pass_stats['win_rate'] > original_stats['win_rate']:
        improvement = pass_stats['win_rate'] - original_stats['win_rate']
        print(f"預期勝率提升: +{improvement:.1f}% ({original_stats['win_rate']:.1f}% → {pass_stats['win_rate']:.1f}%)")
    
    pnl_improvement = pass_stats['total_pnl'] - original_stats['total_pnl'] + block_stats['total_pnl']
    if pnl_improvement > 0:
        print(f"預期損益改善: +${pnl_improvement:.2f}")


if __name__ == "__main__":
    main()
