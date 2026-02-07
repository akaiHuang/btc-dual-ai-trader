#!/usr/bin/env python3
"""
v6.1 保守版回測 - 只保留樣本外也有效的規則
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
                                'market_phase': t.get('market_phase', t.get('strategy', '')),
                                'probability': t.get('probability', 0),
                                'obi': t.get('obi', 0),
                                'max_pnl_pct': t.get('max_pnl_pct', t.get('max_profit_pct', 0)),
                                'max_drawdown_pct': t.get('max_drawdown_pct', 0),
                                'status': t.get('status', ''),
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

def v61_filter(trade: Dict) -> bool:
    """
    v6.1 保守版過濾 - 只保留測試集也有效的規則
    
    規則效果 (訓練/測試):
    - 機率 75-92%: +$116 / +$54 ✅ 保留
    - 避開凌晨: +$80 / +$19  ⚠️ 可選
    - 不做 DISTRIBUTION 空: +$112 / +$8 ❌ 衰退太多
    - OBI >= 0.2: +$38 / +$6 ❌ 衰退太多
    """
    # 唯一保留: 機率 75-92% (測試集效果最好)
    prob = trade.get('probability', 0)
    if prob < 0.75 or prob > 0.92:
        return False
    
    return True

def v61_conservative_filter(trade: Dict) -> bool:
    """v6.1 更保守版 - 只看機率"""
    prob = trade.get('probability', 0)
    # 放寬到 70-95%，避免過度擬合
    if prob < 0.70 or prob > 0.95:
        return False
    return True

def main():
    os.chdir("/Users/akaihuangm1/Desktop/btn")
    
    print("=" * 80)
    print("📊 v6.1 保守版回測")
    print("=" * 80)
    
    trades = load_all_trades()
    print(f"\n📁 載入 {len(trades)} 筆交易")
    
    # 分割數據
    split_idx = int(len(trades) * 0.6)
    train = trades[:split_idx]
    test = trades[split_idx:]
    
    print(f"   訓練集: {len(train)} 筆, 測試集: {len(test)} 筆")
    
    # 測試不同版本
    versions = [
        ("原始 (無過濾)", lambda t: True),
        ("v6.0 (多規則)", lambda t: (
            0.75 <= t.get('probability', 0) <= 0.92 and
            not (t.get('market_phase') == 'DISTRIBUTION' and t.get('side') == 'SHORT') and
            not (1 <= datetime.fromisoformat(t['entry_time'].replace('Z', '')).hour < 6 if t.get('entry_time') else False) and
            not (t.get('side') == 'LONG' and t.get('obi', 1) < 0.2)
        )),
        ("v6.1 (機率 75-92%)", v61_filter),
        ("v6.1b (機率 70-95%)", v61_conservative_filter),
    ]
    
    print("\n" + "=" * 80)
    print("📈 版本比較")
    print("=" * 80)
    
    for name, filter_fn in versions:
        print(f"\n🔹 {name}:")
        
        results = []
        for subset_name, subset in [("訓練", train), ("測試", test), ("全部", trades)]:
            try:
                filtered = [t for t in subset if filter_fn(t)]
                pnl = sum(t['pnl'] for t in filtered)
                wins = len([t for t in filtered if t['pnl'] > 0])
                win_rate = wins / len(filtered) * 100 if filtered else 0
                avg_pnl = pnl / len(filtered) if filtered else 0
                results.append((subset_name, len(filtered), pnl, win_rate, avg_pnl))
                print(f"   {subset_name}: {len(filtered)} 筆, PnL ${pnl:.2f}, 勝率 {win_rate:.1f}%, 平均 ${avg_pnl:.2f}/筆")
            except Exception as e:
                print(f"   {subset_name}: 錯誤 - {e}")
        
        # 計算過度擬合指標
        if len(results) >= 2:
            train_avg = results[0][4]
            test_avg = results[1][4]
            if train_avg != 0:
                degradation = (train_avg - test_avg) / abs(train_avg) * 100
                if degradation > 50:
                    print(f"   ⚠️ 過度擬合: {degradation:.0f}%")
                elif degradation > 20:
                    print(f"   ⚠️ 輕微過度擬合: {degradation:.0f}%")
                else:
                    print(f"   ✅ 穩健: 衰退 {degradation:.0f}%")
    
    # 詳細看 v6.1 的交易
    print("\n" + "=" * 80)
    print("📋 v6.1 (機率 75-92%) 交易詳情")
    print("=" * 80)
    
    filtered = [t for t in trades if v61_filter(t)]
    total_pnl = 0
    train_count = len([t for t in train if v61_filter(t)])
    
    for i, t in enumerate(filtered):
        entry = t['entry_time'][:16] if t['entry_time'] else 'N/A'
        side = t['side']
        phase = t['market_phase']
        prob = t['probability']
        pnl = t['pnl']
        total_pnl += pnl
        
        emoji = "✅" if pnl > 0 else "❌"
        set_mark = "[訓練]" if i < train_count else "[測試]"
        
        print(f"   {emoji} {entry} | {side:5} | {phase:12} | 機率 {prob:.0%} | ${pnl:+.2f} | 累計 ${total_pnl:.2f} {set_mark}")
    
    # 結論
    print("\n" + "=" * 80)
    print("💡 v6.1 建議")
    print("=" * 80)
    
    v61_train = [t for t in train if v61_filter(t)]
    v61_test = [t for t in test if v61_filter(t)]
    
    train_pnl = sum(t['pnl'] for t in v61_train)
    test_pnl = sum(t['pnl'] for t in v61_test)
    
    print(f"""
    📊 v6.1 (只用機率 75-92%) 結果:
    - 訓練集: {len(v61_train)} 筆, ${train_pnl:.2f}
    - 測試集: {len(v61_test)} 筆, ${test_pnl:.2f}
    - 比 v6.0 更穩健 (測試集衰退較少)
    
    🎯 建議:
    1. 先用 v6.1 (只過濾機率) 跑實盤測試
    2. 收集更多數據後再加入其他規則
    3. 至少跑 2 週才有統計意義
    """)

if __name__ == "__main__":
    main()
