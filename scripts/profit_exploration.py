#!/usr/bin/env python3
"""
盈利策略探索 - 分析所有可能的盈利方式
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
                                'wpi': t.get('wpi', 0),
                                'vpin': t.get('vpin', 0),
                                'funding_rate': t.get('funding_rate', 0),
                                'oi_change_pct': t.get('oi_change_pct', 0),
                                'max_pnl_pct': t.get('max_pnl_pct', t.get('max_profit_pct', 0)),
                                'max_drawdown_pct': t.get('max_drawdown_pct', 0),
                                'hold_seconds': t.get('hold_seconds', 0),
                                'status': t.get('status', ''),
                                'volatility_5m': t.get('volatility_5m', 0),
                            }
                            if normalized['entry_time']:
                                all_trades.append(normalized)
                except:
                    pass
    
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
    print("🔍 盈利策略探索")
    print("=" * 80)
    print(f"\n📁 載入 {len(trades)} 筆交易")
    
    # ========== 方案 A: 中等持倉時間 ==========
    print("\n" + "=" * 80)
    print("📊 方案 A: 只做「中等持倉」交易 (5-15 分鐘)")
    print("=" * 80)
    
    medium_trades = [t for t in trades if 300 <= t['hold_seconds'] < 900]
    print(f"\n   符合條件: {len(medium_trades)}/{len(trades)} 筆")
    
    if medium_trades:
        pnl = sum(t['pnl'] for t in medium_trades)
        wins = len([t for t in medium_trades if t['pnl'] > 0])
        wr = wins / len(medium_trades) * 100
        print(f"   PnL: ${pnl:.2f}")
        print(f"   勝率: {wr:.1f}%")
        print(f"   平均: ${pnl/len(medium_trades):.2f}/筆")
        
        # 看這些交易的特徵
        print(f"\n   這些交易的特徵:")
        avg_prob = sum(t['probability'] for t in medium_trades) / len(medium_trades)
        avg_obi = sum(t['obi'] for t in medium_trades) / len(medium_trades)
        phases = {}
        for t in medium_trades:
            p = t['market_phase']
            phases[p] = phases.get(p, 0) + 1
        print(f"   - 平均機率: {avg_prob:.0%}")
        print(f"   - 平均 OBI: {avg_obi:.2f}")
        print(f"   - 市場階段: {phases}")
    
    # ========== 方案 C: 趨勢跟隨 ==========
    print("\n" + "=" * 80)
    print("📊 方案 C: 趨勢跟隨 (鯨魚當過濾器)")
    print("=" * 80)
    
    # 分析: 如果只在 OBI 方向與交易方向一致時進場
    print("\n   規則: OBI > 0 才做多, OBI < 0 才做空")
    
    trend_following = []
    for t in trades:
        obi = t.get('obi', 0)
        side = t.get('side', '')
        if (side == 'LONG' and obi > 0) or (side == 'SHORT' and obi < 0):
            trend_following.append(t)
    
    if trend_following:
        pnl = sum(t['pnl'] for t in trend_following)
        wins = len([t for t in trend_following if t['pnl'] > 0])
        wr = wins / len(trend_following) * 100
        print(f"   符合條件: {len(trend_following)}/{len(trades)} 筆")
        print(f"   PnL: ${pnl:.2f}")
        print(f"   勝率: {wr:.1f}%")
    
    # 更嚴格的趨勢跟隨
    print("\n   更嚴格: |OBI| > 0.3 且方向一致")
    strict_trend = []
    for t in trades:
        obi = t.get('obi', 0)
        side = t.get('side', '')
        if (side == 'LONG' and obi > 0.3) or (side == 'SHORT' and obi < -0.3):
            strict_trend.append(t)
    
    if strict_trend:
        pnl = sum(t['pnl'] for t in strict_trend)
        wins = len([t for t in strict_trend if t['pnl'] > 0])
        wr = wins / len(strict_trend) * 100
        print(f"   符合條件: {len(strict_trend)}/{len(trades)} 筆")
        print(f"   PnL: ${pnl:.2f}")
        print(f"   勝率: {wr:.1f}%")
    
    # ========== 其他盈利方式探索 ==========
    print("\n" + "=" * 80)
    print("🔍 其他盈利方式探索")
    print("=" * 80)
    
    # 1. 反向交易
    print("\n   💡 方案 D: 反向交易 (逆向操作)")
    reversed_pnl = sum(-t['pnl'] for t in trades)
    print(f"   如果全部反向: ${reversed_pnl:.2f}")
    print(f"   → {'✅ 可行' if reversed_pnl > 0 else '❌ 不可行'}")
    
    # 2. 只做多 or 只做空
    print("\n   💡 方案 E: 單向交易")
    long_trades = [t for t in trades if t['side'] == 'LONG']
    short_trades = [t for t in trades if t['side'] == 'SHORT']
    
    if long_trades:
        long_pnl = sum(t['pnl'] for t in long_trades)
        long_wr = len([t for t in long_trades if t['pnl'] > 0]) / len(long_trades) * 100
        print(f"   只做多: {len(long_trades)} 筆, ${long_pnl:.2f}, 勝率 {long_wr:.0f}%")
    
    if short_trades:
        short_pnl = sum(t['pnl'] for t in short_trades)
        short_wr = len([t for t in short_trades if t['pnl'] > 0]) / len(short_trades) * 100
        print(f"   只做空: {len(short_trades)} 筆, ${short_pnl:.2f}, 勝率 {short_wr:.0f}%")
    
    # 3. 根據市場階段
    print("\n   💡 方案 F: 只做特定市場階段")
    for phase in ['ACCUMULATION', 'DISTRIBUTION', 'MARKUP', 'MARKDOWN']:
        phase_trades = [t for t in trades if t['market_phase'] == phase]
        if phase_trades:
            pnl = sum(t['pnl'] for t in phase_trades)
            wr = len([t for t in phase_trades if t['pnl'] > 0]) / len(phase_trades) * 100
            emoji = "✅" if pnl > 0 else "❌"
            print(f"   {phase}: {len(phase_trades)} 筆, ${pnl:.2f}, 勝率 {wr:.0f}% {emoji}")
    
    # 4. 時間過濾
    print("\n   💡 方案 G: 時段過濾")
    time_slots = {
        "亞洲盤 (8-16)": lambda h: 8 <= h < 16,
        "歐洲盤 (16-24)": lambda h: 16 <= h < 24,
        "美洲盤 (0-8)": lambda h: 0 <= h < 8,
        "活躍時段 (9-12, 21-24)": lambda h: (9 <= h < 12) or (21 <= h < 24),
    }
    
    for name, filter_fn in time_slots.items():
        slot_trades = []
        for t in trades:
            try:
                dt = datetime.fromisoformat(t['entry_time'].replace('Z', ''))
                if filter_fn(dt.hour):
                    slot_trades.append(t)
            except:
                pass
        
        if slot_trades:
            pnl = sum(t['pnl'] for t in slot_trades)
            wr = len([t for t in slot_trades if t['pnl'] > 0]) / len(slot_trades) * 100
            emoji = "✅" if pnl > 0 else "❌"
            print(f"   {name}: {len(slot_trades)} 筆, ${pnl:.2f}, 勝率 {wr:.0f}% {emoji}")
    
    # 5. Funding Rate 套利
    print("\n   💡 方案 H: Funding Rate 套利")
    print("   概念: 當 funding rate 極端時反向交易")
    
    high_funding = [t for t in trades if abs(t.get('funding_rate', 0)) > 0.0005]
    if high_funding:
        print(f"   極端 funding 時: {len(high_funding)} 筆")
        # 當 funding 高時做空 (因為多頭要付費)
        # 當 funding 負時做多 (因為空頭要付費)
        correct_trades = []
        for t in high_funding:
            fr = t.get('funding_rate', 0)
            if (fr > 0.0005 and t['side'] == 'SHORT') or (fr < -0.0005 and t['side'] == 'LONG'):
                correct_trades.append(t)
        
        if correct_trades:
            pnl = sum(t['pnl'] for t in correct_trades)
            print(f"   符合 funding 方向: {len(correct_trades)} 筆, ${pnl:.2f}")
    else:
        print("   沒有極端 funding 的交易記錄")
    
    # 6. 組合策略
    print("\n   💡 方案 I: 組合最佳條件")
    
    best_combo = []
    for t in trades:
        # 條件組合: 中等持倉 + 趨勢跟隨
        hold_ok = 300 <= t['hold_seconds'] < 900
        obi = t.get('obi', 0)
        side = t.get('side', '')
        trend_ok = (side == 'LONG' and obi > 0) or (side == 'SHORT' and obi < 0)
        
        if hold_ok and trend_ok:
            best_combo.append(t)
    
    if best_combo:
        pnl = sum(t['pnl'] for t in best_combo)
        wins = len([t for t in best_combo if t['pnl'] > 0])
        wr = wins / len(best_combo) * 100 if best_combo else 0
        print(f"   中等持倉 + 趨勢跟隨: {len(best_combo)} 筆, ${pnl:.2f}, 勝率 {wr:.0f}%")
    
    # ========== 結論 ==========
    print("\n" + "=" * 80)
    print("💡 盈利方式總結")
    print("=" * 80)
    
    # 找出所有正 PnL 的方案
    profitable = []
    
    # 方案 A
    if medium_trades:
        pnl = sum(t['pnl'] for t in medium_trades)
        if pnl > 0:
            profitable.append(("A: 中等持倉", len(medium_trades), pnl))
    
    # 方案 D 反向
    if reversed_pnl > 0:
        profitable.append(("D: 反向交易", len(trades), reversed_pnl))
    
    # 方案 E 單向
    if long_trades:
        pnl = sum(t['pnl'] for t in long_trades)
        if pnl > 0:
            profitable.append(("E: 只做多", len(long_trades), pnl))
    if short_trades:
        pnl = sum(t['pnl'] for t in short_trades)
        if pnl > 0:
            profitable.append(("E: 只做空", len(short_trades), pnl))
    
    if profitable:
        print("\n   ✅ 可能盈利的方案:")
        for name, count, pnl in sorted(profitable, key=lambda x: -x[2]):
            print(f"   - {name}: {count} 筆, ${pnl:.2f}")
    else:
        print("\n   ❌ 沒有找到明顯盈利的方案")
    
    print("""
    
    🎯 核心問題與建議:
    
    1. 手續費問題 (核心)
       - 100x 槓桿 + $100 倉位 = $8 手續費 (8%)
       - 必須每筆賺 >8% 才能打平
       → 建議: 降槓桿或加大倉位
    
    2. 信號質量問題
       - 鯨魚信號可能適合「方向判斷」而非「精確入場」
       - Scalping 需要極高精度，鯨魚信號可能不夠
       → 建議: 改成 Swing Trading (持倉小時~天)
    
    3. 可能有效的新方向:
       
       a) 事件驅動交易
          - 等待大戶異動事件
          - 不是連續交易，而是等待高確信度信號
       
       b) 區間交易 (Mean Reversion)
          - 鯨魚信號判斷大方向
          - 用技術指標 (RSI/BB) 找回撤入場點
       
       c) 網格交易
          - 鯨魚判斷大方向
          - 在該方向設置網格
          - 利用波動賺差價
       
       d) 純 Funding 套利
          - 不預測方向
          - 只在 funding 極端時反向交易
          - 賺 funding fee
    """)

if __name__ == "__main__":
    main()
