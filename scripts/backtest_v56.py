#!/usr/bin/env python3
"""
v5.6 回測分析腳本
用歷史交易紀錄模擬 v5.6 的過濾邏輯
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

# v5.6 觀察型策略
OBSERVE_STRATEGIES = [
    'FAKEOUT', 'SPOOFING', 'CONSOLIDATION_SHAKE', 'STOP_HUNT',
    'WHIPSAW', 'SLOW_BLEED', 'WASH_TRADING', 'LAYERING', 'NORMAL'
]

OBSERVE_THRESHOLD = 0.75  # 觀察策略高於此機率就不交易

def would_v56_block(trade: Dict) -> Tuple[bool, str]:
    """
    檢查 v5.6 是否會阻擋這筆交易
    
    Returns:
        (是否阻擋, 原因)
    """
    strategy_probs = trade.get('strategy_probs', {})
    if not strategy_probs:
        return False, "無策略機率數據"
    
    # 找出最高的可交易策略機率
    tradeable_prob = trade.get('probability', 0)
    tradeable_strategy = trade.get('strategy', 'UNKNOWN')
    
    # 找出最高的觀察型策略機率
    best_observe_strategy = None
    best_observe_prob = 0
    
    for strat, prob in strategy_probs.items():
        if strat in OBSERVE_STRATEGIES and prob > best_observe_prob:
            best_observe_prob = prob
            best_observe_strategy = strat
    
    # v5.6 過濾條件
    if best_observe_strategy:
        if best_observe_prob >= tradeable_prob:
            return True, f"觀察策略 {best_observe_strategy}({best_observe_prob:.0%}) >= 可交易 {tradeable_strategy}({tradeable_prob:.0%})"
        if best_observe_prob >= OBSERVE_THRESHOLD:
            return True, f"觀察策略 {best_observe_strategy}({best_observe_prob:.0%}) >= 警戒門檻 75%"
    
    return False, "通過"

def analyze_trade(trade: Dict) -> Dict:
    """分析單筆交易"""
    would_block, reason = would_v56_block(trade)
    
    net_pnl = trade.get('net_pnl_usdt', 0)
    status = trade.get('status', 'UNKNOWN')
    
    is_win = net_pnl > 0
    
    return {
        'trade_id': trade.get('trade_id', 'N/A'),
        'timestamp': trade.get('timestamp', 'N/A')[:19],
        'strategy': trade.get('strategy', 'N/A'),
        'probability': trade.get('probability', 0),
        'strategy_probs': trade.get('strategy_probs', {}),
        'direction': trade.get('direction', 'N/A'),
        'net_pnl': net_pnl,
        'status': status,
        'is_win': is_win,
        'v56_would_block': would_block,
        'v56_reason': reason
    }

def main():
    logs_dir = Path("/Users/akaihuangm1/Desktop/btn/logs")
    
    all_trades = []
    
    # 收集所有交易
    for json_file in logs_dir.rglob("trades_*.json"):
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)
            
            trades = data.get('trades', [])
            for trade in trades:
                if trade.get('status', '').startswith('CLOSED'):
                    all_trades.append(analyze_trade(trade))
        except Exception as e:
            continue
    
    if not all_trades:
        print("❌ 沒有找到任何交易紀錄")
        return
    
    # 分類統計
    blocked_trades = [t for t in all_trades if t['v56_would_block']]
    passed_trades = [t for t in all_trades if not t['v56_would_block']]
    
    blocked_wins = [t for t in blocked_trades if t['is_win']]
    blocked_losses = [t for t in blocked_trades if not t['is_win']]
    
    passed_wins = [t for t in passed_trades if t['is_win']]
    passed_losses = [t for t in passed_trades if not t['is_win']]
    
    # 計算 PnL
    blocked_pnl = sum(t['net_pnl'] for t in blocked_trades)
    passed_pnl = sum(t['net_pnl'] for t in passed_trades)
    total_pnl = sum(t['net_pnl'] for t in all_trades)
    
    print("=" * 80)
    print("📊 v5.6 回測分析報告")
    print("=" * 80)
    
    print(f"\n📈 總交易統計")
    print(f"   總交易數: {len(all_trades)} 筆")
    print(f"   總 PnL: ${total_pnl:.2f}")
    print(f"   總勝率: {len([t for t in all_trades if t['is_win']])/len(all_trades)*100:.1f}%")
    
    print(f"\n🚫 v5.6 會阻擋的交易 ({len(blocked_trades)} 筆)")
    print(f"   勝: {len(blocked_wins)} 筆, 敗: {len(blocked_losses)} 筆")
    if blocked_trades:
        blocked_winrate = len(blocked_wins) / len(blocked_trades) * 100
        print(f"   勝率: {blocked_winrate:.1f}%")
    print(f"   PnL: ${blocked_pnl:.2f}")
    
    print(f"\n✅ v5.6 會放行的交易 ({len(passed_trades)} 筆)")
    print(f"   勝: {len(passed_wins)} 筆, 敗: {len(passed_losses)} 筆")
    if passed_trades:
        passed_winrate = len(passed_wins) / len(passed_trades) * 100
        print(f"   勝率: {passed_winrate:.1f}%")
    print(f"   PnL: ${passed_pnl:.2f}")
    
    # 效果評估
    print(f"\n📊 v5.6 過濾效果")
    print(f"   原始 PnL: ${total_pnl:.2f}")
    print(f"   過濾後 PnL: ${passed_pnl:.2f}")
    improvement = passed_pnl - total_pnl
    print(f"   改善: ${improvement:+.2f}")
    
    # 被阻擋的交易詳情
    if blocked_trades:
        print(f"\n" + "=" * 80)
        print("🚫 被阻擋交易詳情")
        print("=" * 80)
        
        for t in blocked_trades:
            win_icon = "✅" if t['is_win'] else "❌"
            print(f"\n{win_icon} {t['timestamp']} | {t['strategy']} {t['probability']:.0%} | {t['direction']}")
            print(f"   PnL: ${t['net_pnl']:.2f} | {t['status']}")
            print(f"   策略機率: {t['strategy_probs']}")
            print(f"   阻擋原因: {t['v56_reason']}")
    
    # 放行但虧損的交易 (潛在漏洞)
    passed_big_losses = [t for t in passed_losses if t['net_pnl'] < -5]
    if passed_big_losses:
        print(f"\n" + "=" * 80)
        print("⚠️ 放行但大額虧損的交易 (潛在漏洞)")
        print("=" * 80)
        
        for t in passed_big_losses:
            print(f"\n❌ {t['timestamp']} | {t['strategy']} {t['probability']:.0%} | {t['direction']}")
            print(f"   PnL: ${t['net_pnl']:.2f} | {t['status']}")
            print(f"   策略機率: {t['strategy_probs']}")
            print(f"   放行原因: {t['v56_reason']}")

if __name__ == "__main__":
    main()
