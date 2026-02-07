#!/usr/bin/env python3
"""
分析兩個方向：
1. 放大止盈目標
2. 鯨魚策略是否適合 scalping
"""

import json
import os
from datetime import datetime
from typing import List, Dict

def load_all_trades() -> List[Dict]:
    """載入所有交易數據"""
    all_trades = []
    paper_dir = "logs/whale_paper_trader"
    
    if os.path.exists(paper_dir):
        for f in os.listdir(paper_dir):
            if f.endswith('.json') and 'trades_' in f:
                try:
                    with open(os.path.join(paper_dir, f)) as fp:
                        data = json.load(fp)
                        trades = data.get('trades', []) if isinstance(data, dict) else data
                        for t in trades:
                            normalized = {
                                'entry_time': t.get('entry_time', t.get('timestamp', '')),
                                'side': t.get('side', t.get('direction', '')),
                                'pnl': t.get('pnl', t.get('net_pnl_usdt', 0)),
                                'pnl_pct': t.get('pnl_pct', 0),
                                'market_phase': t.get('market_phase', t.get('strategy', '')),
                                'probability': t.get('probability', 0),
                                'obi': t.get('obi', 0),
                                'max_pnl_pct': t.get('max_pnl_pct', t.get('max_profit_pct', 0)),
                                'max_drawdown_pct': t.get('max_drawdown_pct', 0),
                                'status': t.get('status', ''),
                                'hold_seconds': t.get('hold_seconds', 0),
                            }
                            if normalized['entry_time']:
                                all_trades.append(normalized)
                except:
                    pass
    
    # 去重
    seen = set()
    unique = []
    for t in all_trades:
        key = t['entry_time']
        if key not in seen:
            seen.add(key)
            unique.append(t)
    
    unique.sort(key=lambda x: x['entry_time'])
    return unique

def main():
    os.chdir("/Users/akaihuangm1/Desktop/btn")
    
    trades = load_all_trades()
    print("=" * 80)
    print("📊 策略深度分析 - 尋找盈利之道")
    print("=" * 80)
    print(f"\n📁 載入 {len(trades)} 筆交易")
    
    # ========== 分析 1: 當前盈虧分布 ==========
    print("\n" + "=" * 80)
    print("📈 分析 1: 當前盈虧分布")
    print("=" * 80)
    
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    
    avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
    
    print(f"\n   獲利交易: {len(wins)} 筆, 平均 ${avg_win:.2f}")
    print(f"   虧損交易: {len(losses)} 筆, 平均 ${avg_loss:.2f}")
    print(f"   盈虧比: {abs(avg_win/avg_loss):.2f}:1" if avg_loss else "   盈虧比: N/A")
    
    # 盈虧比需要多少勝率才能打平
    if avg_loss != 0:
        rr_ratio = abs(avg_win / avg_loss)
        breakeven_wr = 1 / (1 + rr_ratio)
        print(f"   打平所需勝率: {breakeven_wr*100:.1f}%")
        print(f"   當前勝率: {len(wins)/len(trades)*100:.1f}%")
    
    # ========== 分析 2: 最大漲幅分布 (潛在止盈空間) ==========
    print("\n" + "=" * 80)
    print("📈 分析 2: 最大漲幅分布 (潛在止盈空間)")
    print("=" * 80)
    
    max_profits = [t['max_pnl_pct'] for t in trades if t['max_pnl_pct'] > 0]
    
    if max_profits:
        print(f"\n   有漲過的交易: {len(max_profits)}/{len(trades)} 筆")
        print(f"   平均最大漲幅: {sum(max_profits)/len(max_profits):.1f}%")
        print(f"   最大漲幅: {max(max_profits):.1f}%")
        
        # 統計不同止盈點能抓到多少交易
        thresholds = [1, 2, 3, 4, 5, 6, 8, 10]
        print(f"\n   止盈點位分析:")
        for tp in thresholds:
            count = len([p for p in max_profits if p >= tp])
            pct = count / len(trades) * 100
            # 計算如果在這個點位止盈，PnL 會是多少
            potential_pnl = 0
            for t in trades:
                if t['max_pnl_pct'] >= tp:
                    # 能止盈
                    potential_pnl += tp - 4  # tp% 獲利 - $4 手續費 (以 $100 倉位計)
                else:
                    # 不能止盈，用原始 PnL
                    potential_pnl += t['pnl']
            print(f"   止盈 {tp}%: {count}/{len(trades)} 筆 ({pct:.0f}%) → 預估 PnL ${potential_pnl:.2f}")
    
    # ========== 分析 3: 持倉時間 vs 獲利 ==========
    print("\n" + "=" * 80)
    print("📈 分析 3: 持倉時間分析")
    print("=" * 80)
    
    # 分析持倉時間
    short_trades = [t for t in trades if t['hold_seconds'] < 300]  # < 5 分鐘
    medium_trades = [t for t in trades if 300 <= t['hold_seconds'] < 900]  # 5-15 分鐘
    long_trades = [t for t in trades if t['hold_seconds'] >= 900]  # > 15 分鐘
    
    for name, subset in [("短 (<5min)", short_trades), ("中 (5-15min)", medium_trades), ("長 (>15min)", long_trades)]:
        if subset:
            pnl = sum(t['pnl'] for t in subset)
            wr = len([t for t in subset if t['pnl'] > 0]) / len(subset) * 100
            print(f"   {name}: {len(subset)} 筆, PnL ${pnl:.2f}, 勝率 {wr:.0f}%")
    
    # ========== 分析 4: Scalping 是否適合 ==========
    print("\n" + "=" * 80)
    print("📈 分析 4: Scalping 可行性")
    print("=" * 80)
    
    # 手續費佔比分析
    fee_per_trade = 4  # $4 來回手續費
    position_size = 100  # $100 倉位
    
    print(f"\n   💰 手續費分析:")
    print(f"   每筆手續費: ${fee_per_trade}")
    print(f"   倉位大小: ${position_size}")
    print(f"   手續費佔比: {fee_per_trade/position_size*100:.1f}%")
    print(f"   → 需要獲利 {fee_per_trade/position_size*100:.1f}% 才能打平！")
    
    # 計算不同槓桿的影響
    print(f"\n   📊 不同槓桿對手續費的影響:")
    for leverage in [100, 50, 20, 10, 5]:
        # 假設相同名義價值 ($10000)
        notional = 10000
        margin = notional / leverage
        fee = notional * 0.0004 * 2  # 0.04% maker 來回
        fee_pct = fee / margin * 100
        print(f"   {leverage}x 槓桿: 保證金 ${margin:.0f}, 手續費 ${fee:.2f} ({fee_pct:.1f}% of 保證金)")
    
    # ========== 分析 5: 如果改用 Swing Trading ==========
    print("\n" + "=" * 80)
    print("📈 分析 5: Swing Trading 模擬 (放大止盈)")
    print("=" * 80)
    
    # 模擬不同止盈設定
    configs = [
        ("當前 (動態止盈)", None),  # 用原始結果
        ("固定 3% 止盈", 3),
        ("固定 5% 止盈", 5),
        ("固定 8% 止盈", 8),
        ("固定 10% 止盈", 10),
    ]
    
    print(f"\n   不同止盈策略模擬 (假設止損 -10%):")
    
    for name, tp_pct in configs:
        if tp_pct is None:
            # 原始結果
            total_pnl = sum(t['pnl'] for t in trades)
            wins_count = len([t for t in trades if t['pnl'] > 0])
        else:
            total_pnl = 0
            wins_count = 0
            for t in trades:
                if t['max_pnl_pct'] >= tp_pct:
                    # 能達到止盈
                    pnl = tp_pct - 4  # 扣手續費
                    total_pnl += pnl
                    wins_count += 1
                elif t['max_drawdown_pct'] >= 10:
                    # 觸及止損
                    total_pnl += -10 - 4
                else:
                    # 既沒止盈也沒止損，用原始結果
                    total_pnl += t['pnl']
                    if t['pnl'] > 0:
                        wins_count += 1
        
        wr = wins_count / len(trades) * 100
        avg = total_pnl / len(trades)
        print(f"   {name:20}: PnL ${total_pnl:+.2f}, 勝率 {wr:.0f}%, 平均 ${avg:+.2f}/筆")
    
    # ========== 分析 6: 最佳策略建議 ==========
    print("\n" + "=" * 80)
    print("💡 結論與建議")
    print("=" * 80)
    
    # 找出最佳止盈點
    best_tp = None
    best_pnl = sum(t['pnl'] for t in trades)
    
    for tp_pct in range(3, 15):
        total_pnl = 0
        for t in trades:
            if t['max_pnl_pct'] >= tp_pct:
                total_pnl += tp_pct - 4
            elif t['max_drawdown_pct'] >= 10:
                total_pnl += -14
            else:
                total_pnl += t['pnl']
        
        if total_pnl > best_pnl:
            best_pnl = total_pnl
            best_tp = tp_pct
    
    print(f"""
    📊 問題診斷:
    1. 手續費太重: 每筆 $4 (4% of 保證金)
    2. 盈虧比差: 平均贏 ${avg_win:.2f} vs 平均輸 ${avg_loss:.2f}
    3. Scalping 在高槓桿下手續費吃太多利潤
    
    🎯 建議方案:
    
    方案 A: 放大止盈目標
    - 最佳止盈點: {best_tp}% (預估 PnL ${best_pnl:.2f})
    - 讓獲利交易賺更多來覆蓋手續費
    
    方案 B: 降低槓桿
    - 從 100x 降到 20x
    - 手續費從 4% 降到 0.8% of 保證金
    - 但需要更大本金
    
    方案 C: 換成 Swing Trading
    - 持倉時間拉長 (小時~天)
    - 目標獲利 3-5%
    - 減少交易頻率，降低手續費總額
    
    方案 D: 改變策略邏輯
    - 不用 scalping，改用趨勢跟隨
    - 鯨魚信號作為趨勢確認，不是進場點
    """)

if __name__ == "__main__":
    main()
