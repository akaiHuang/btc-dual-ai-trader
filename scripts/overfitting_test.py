#!/usr/bin/env python3
"""
過度擬合測試 - 驗證 v6.0 策略是否過度擬合
方法:
1. 樣本內/樣本外分割 (時間順序)
2. 統計顯著性檢驗
3. 規則穩健性分析
"""

import json
import os
from datetime import datetime
from typing import List, Dict

def load_all_trades() -> List[Dict]:
    """載入所有交易數據"""
    all_trades = []
    
    # 載入 paper trader 數據
    paper_dir = "logs/whale_paper_trader"
    if os.path.exists(paper_dir):
        for f in os.listdir(paper_dir):
            if f.endswith('.json') and 'trades_' in f:
                try:
                    with open(os.path.join(paper_dir, f)) as fp:
                        data = json.load(fp)
                        trades = data.get('trades', []) if isinstance(data, dict) else data
                        for t in trades:
                            # 標準化欄位名稱
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
                except Exception as e:
                    print(f"Error loading {f}: {e}")
                    pass
    
    # 去重 (根據 entry_time)
    seen = set()
    unique_trades = []
    for t in all_trades:
        key = t.get('entry_time', '')
        if key and key not in seen:
            seen.add(key)
            unique_trades.append(t)
    
    # 按時間排序
    unique_trades.sort(key=lambda x: x.get('entry_time', ''))
    return unique_trades

def apply_v60_filters(trade: Dict) -> bool:
    """應用 v6.0 過濾規則"""
    # 1. 不做 DISTRIBUTION 空單
    market_phase = trade.get('market_phase', '')
    side = trade.get('side', '')
    if market_phase == 'DISTRIBUTION' and side == 'SHORT':
        return False
    
    # 2. 機率 75-92%
    prob = trade.get('probability', 0)
    if prob < 0.75 or prob > 0.92:
        return False
    
    # 3. 凌晨 1-6 點不交易
    entry_time = trade.get('entry_time', '')
    if entry_time:
        try:
            dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
            hour = dt.hour
            if 1 <= hour < 6:
                return False
        except:
            pass
    
    # 4. 做多 OBI >= 0.2
    obi = trade.get('obi', 0)
    if side == 'LONG' and obi < 0.2:
        return False
    
    return True

def calculate_adjusted_pnl(trade: Dict) -> float:
    """計算調整後 PnL (加上保護機制)"""
    pnl = trade.get('pnl', 0)
    max_pnl_pct = trade.get('max_pnl_pct', 0)
    max_drawdown_pct = trade.get('max_drawdown_pct', 0)
    
    # 無動能止損: 沒漲過 0.5% 且跌 4% 以上
    if max_pnl_pct < 0.5 and max_drawdown_pct >= 4:
        # 限制虧損在 -4%
        adjusted = -4 * 100 / 100 - 4  # $100 * -4% - $4 fee
        return max(pnl, adjusted)
    
    # 先漲保護: 漲過 3% 但結果虧錢
    if max_pnl_pct >= 3 and pnl < 0:
        # 保本出場
        return -4  # 只虧手續費
    
    # 鎖盈: 漲過 3% 的獲利交易用 trailing
    if max_pnl_pct >= 3 and pnl > 0:
        # trailing 1%
        locked_pnl = (max_pnl_pct - 1) * 100 / 100 - 4
        return max(pnl, locked_pnl)
    
    return pnl

def run_overfitting_test():
    print("=" * 80)
    print("📊 過度擬合測試 - v6.0 策略驗證")
    print("=" * 80)
    
    trades = load_all_trades()
    print(f"\n📁 載入 {len(trades)} 筆獨立交易")
    
    if len(trades) < 10:
        print("❌ 數據不足，無法進行可靠的過度擬合測試")
        return
    
    # 顯示時間範圍
    if trades:
        print(f"   時間範圍: {trades[0].get('entry_time', 'N/A')} ~ {trades[-1].get('entry_time', 'N/A')}")
    
    # 分割數據: 前 60% 訓練，後 40% 測試
    split_idx = int(len(trades) * 0.6)
    train_trades = trades[:split_idx]
    test_trades = trades[split_idx:]
    
    print(f"\n📊 數據分割:")
    print(f"   訓練集 (前 60%): {len(train_trades)} 筆")
    print(f"   測試集 (後 40%): {len(test_trades)} 筆")
    
    # 測試 1: 原始策略表現
    print("\n" + "=" * 80)
    print("📈 測試 1: 原始策略 (無過濾)")
    print("=" * 80)
    
    for name, subset in [("訓練集", train_trades), ("測試集", test_trades), ("全部", trades)]:
        total_pnl = sum(t.get('pnl', 0) for t in subset)
        wins = len([t for t in subset if t.get('pnl', 0) > 0])
        win_rate = wins / len(subset) * 100 if subset else 0
        print(f"   {name}: {len(subset)} 筆, PnL ${total_pnl:.2f}, 勝率 {win_rate:.1f}%")
    
    # 測試 2: v6.0 過濾後
    print("\n" + "=" * 80)
    print("📈 測試 2: v6.0 過濾規則")
    print("=" * 80)
    
    for name, subset in [("訓練集", train_trades), ("測試集", test_trades), ("全部", trades)]:
        filtered = [t for t in subset if apply_v60_filters(t)]
        total_pnl = sum(t.get('pnl', 0) for t in filtered)
        wins = len([t for t in filtered if t.get('pnl', 0) > 0])
        win_rate = wins / len(filtered) * 100 if filtered else 0
        pass_rate = len(filtered) / len(subset) * 100 if subset else 0
        print(f"   {name}: {len(filtered)}/{len(subset)} 筆 ({pass_rate:.0f}%), PnL ${total_pnl:.2f}, 勝率 {win_rate:.1f}%")
    
    # 測試 3: v6.0 過濾 + 保護機制
    print("\n" + "=" * 80)
    print("📈 測試 3: v6.0 完整 (過濾 + 保護)")
    print("=" * 80)
    
    for name, subset in [("訓練集", train_trades), ("測試集", test_trades), ("全部", trades)]:
        filtered = [t for t in subset if apply_v60_filters(t)]
        total_pnl = sum(calculate_adjusted_pnl(t) for t in filtered)
        adjusted_wins = len([t for t in filtered if calculate_adjusted_pnl(t) > 0])
        win_rate = adjusted_wins / len(filtered) * 100 if filtered else 0
        print(f"   {name}: {len(filtered)} 筆, 調整後 PnL ${total_pnl:.2f}, 調整後勝率 {win_rate:.1f}%")
    
    # 測試 4: 規則穩健性 - 每條規則單獨測試
    print("\n" + "=" * 80)
    print("📈 測試 4: 單一規則效果")
    print("=" * 80)
    
    rules = [
        ("不做 DISTRIBUTION 空", lambda t: not (t.get('market_phase') == 'DISTRIBUTION' and t.get('side') == 'SHORT')),
        ("機率 75-92%", lambda t: 0.75 <= t.get('probability', 0) <= 0.92),
        ("避開凌晨 1-6 點", lambda t: not (1 <= datetime.fromisoformat(t.get('entry_time', '2000-01-01T12:00:00').replace('Z', '+00:00')).hour < 6) if t.get('entry_time') else True),
        ("做多 OBI >= 0.2", lambda t: not (t.get('side') == 'LONG' and t.get('obi', 1) < 0.2)),
    ]
    
    for rule_name, rule_fn in rules:
        print(f"\n   🔍 {rule_name}:")
        for name, subset in [("訓練", train_trades), ("測試", test_trades)]:
            try:
                filtered = [t for t in subset if rule_fn(t)]
                total_pnl = sum(t.get('pnl', 0) for t in filtered)
                orig_pnl = sum(t.get('pnl', 0) for t in subset)
                improvement = total_pnl - orig_pnl
                print(f"      {name}: {len(filtered)}/{len(subset)} 筆, PnL ${total_pnl:.2f} (vs ${orig_pnl:.2f}, 差 ${improvement:+.2f})")
            except Exception as e:
                print(f"      {name}: 錯誤 - {e}")
    
    # 測試 5: 過度擬合指標
    print("\n" + "=" * 80)
    print("📊 過度擬合評估")
    print("=" * 80)
    
    # 計算訓練集和測試集的表現差異
    train_filtered = [t for t in train_trades if apply_v60_filters(t)]
    test_filtered = [t for t in test_trades if apply_v60_filters(t)]
    
    train_pnl = sum(calculate_adjusted_pnl(t) for t in train_filtered)
    test_pnl = sum(calculate_adjusted_pnl(t) for t in test_filtered)
    
    train_avg = train_pnl / len(train_filtered) if train_filtered else 0
    test_avg = test_pnl / len(test_filtered) if test_filtered else 0
    
    print(f"   訓練集平均 PnL: ${train_avg:.2f}/筆")
    print(f"   測試集平均 PnL: ${test_avg:.2f}/筆")
    
    if train_avg != 0:
        degradation = (train_avg - test_avg) / abs(train_avg) * 100
        print(f"   表現衰退: {degradation:.1f}%")
        
        if degradation > 50:
            print("   ⚠️ 警告: 嚴重過度擬合 (衰退 > 50%)")
        elif degradation > 30:
            print("   ⚠️ 注意: 可能過度擬合 (衰退 30-50%)")
        elif degradation > 10:
            print("   ✅ 輕微衰退 (10-30%), 可接受")
        else:
            print("   ✅ 穩健 (衰退 < 10%)")
    
    # 結論
    print("\n" + "=" * 80)
    print("💡 結論")
    print("=" * 80)
    print("""
    🔍 過度擬合風險分析:
    
    1. 數據量: 只有 5 天數據 (11/28-12/3)
       ⚠️ 風險: 樣本太小，統計意義有限
    
    2. 規則來源:
       - 避開凌晨: 基於市場流動性 (合理)
       - 避開高機率: 基於假信號分析 (需驗證)
       - DISTRIBUTION 不做空: 基於市場結構 (合理)
       - OBI 過濾: 基於訂單簿 (合理)
    
    3. 保護機制:
       - 無動能止損: 邏輯合理 (沒漲就跑)
       - 先漲保護: 邏輯合理 (漲過就鎖)
       
    📌 建議:
    1. 用更長時間的實盤/模擬測試驗證
    2. 逐步放寬參數，觀察穩健性
    3. 關注測試集表現是否與訓練集一致
    """)

if __name__ == "__main__":
    os.chdir("/Users/akaihuangm1/Desktop/btn")
    run_overfitting_test()
