#!/usr/bin/env python3
"""
分析：什麼因素決定「暴跌後會回升」vs「暴跌後不回」
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
    
    # v5.7 過濾
    v57_trades = []
    for t in trades:
        strategy = t.get('strategy', '')
        direction = t.get('direction', '')
        prob = t.get('probability', 0)
        obi = t.get('obi', 0)
        
        if strategy == 'DISTRIBUTION' and direction == 'SHORT':
            continue
        if prob < 0.75 or prob > 0.92:
            continue
        if direction == 'LONG' and (obi < 0.2 or obi > 0.85):
            continue
        
        v57_trades.append(t)
    
    print("=" * 100)
    print("🔬 深度分析：什麼決定暴跌後是否回升")
    print("=" * 100)
    
    # 分類：跌超過 5% 的交易
    deep_dip_trades = [t for t in v57_trades if abs(t.get('max_drawdown_pct', 0)) >= 5]
    
    print(f"\n📊 跌超過 5% 的交易: {len(deep_dip_trades)} 筆")
    
    # 區分回升 vs 不回升
    recovered = [t for t in deep_dip_trades if t.get('net_pnl_usdt', 0) > 0]
    not_recovered = [t for t in deep_dip_trades if t.get('net_pnl_usdt', 0) <= 0]
    
    print(f"\n✅ 暴跌後回升獲利: {len(recovered)} 筆")
    print(f"❌ 暴跌後沒回虧損: {len(not_recovered)} 筆")
    
    # 比較特徵
    print(f"\n" + "=" * 100)
    print("📊 特徵比較")
    print("=" * 100)
    
    features = ['strategy', 'probability', 'obi', 'volume_ratio', 'trend', 'direction']
    
    for feature in features:
        print(f"\n--- {feature} ---")
        
        # 回升組
        if recovered:
            vals = [t.get(feature, 'N/A') for t in recovered]
            if all(isinstance(v, (int, float)) for v in vals if v != 'N/A'):
                numeric = [v for v in vals if isinstance(v, (int, float))]
                if numeric:
                    avg = sum(numeric) / len(numeric)
                    print(f"   回升組: 平均 {avg:.3f}, 範圍 {min(numeric):.3f} ~ {max(numeric):.3f}")
            else:
                from collections import Counter
                c = Counter(vals)
                print(f"   回升組: {dict(c)}")
        
        # 不回升組
        if not_recovered:
            vals = [t.get(feature, 'N/A') for t in not_recovered]
            if all(isinstance(v, (int, float)) for v in vals if v != 'N/A'):
                numeric = [v for v in vals if isinstance(v, (int, float))]
                if numeric:
                    avg = sum(numeric) / len(numeric)
                    print(f"   不回升: 平均 {avg:.3f}, 範圍 {min(numeric):.3f} ~ {max(numeric):.3f}")
            else:
                from collections import Counter
                c = Counter(vals)
                print(f"   不回升: {dict(c)}")
    
    # 詳細列表
    print(f"\n" + "=" * 100)
    print("📋 詳細交易列表 (跌超過 5%)")
    print("=" * 100)
    
    print(f"\n✅ 暴跌後回升獲利:")
    for t in recovered:
        ts = t.get('timestamp', '')[:16]
        strategy = t.get('strategy', '')
        prob = t.get('probability', 0)
        obi = t.get('obi', 0)
        vol_ratio = t.get('volume_ratio', 0)
        max_p = t.get('max_profit_pct', 0)
        max_dd = abs(t.get('max_drawdown_pct', 0))
        pnl = t.get('net_pnl_usdt', 0)
        hold = t.get('hold_duration_minutes', 0)
        
        print(f"   {ts} | {strategy} {prob:.0%} | OBI:{obi:.2f} | Vol:{vol_ratio:.1f}x")
        print(f"      最高+{max_p:.1f}% 最低-{max_dd:.1f}% | 持倉{hold:.0f}分 | ${pnl:+.2f}")
    
    print(f"\n❌ 暴跌後沒回虧損:")
    for t in not_recovered:
        ts = t.get('timestamp', '')[:16]
        strategy = t.get('strategy', '')
        prob = t.get('probability', 0)
        obi = t.get('obi', 0)
        vol_ratio = t.get('volume_ratio', 0)
        max_p = t.get('max_profit_pct', 0)
        max_dd = abs(t.get('max_drawdown_pct', 0))
        pnl = t.get('net_pnl_usdt', 0)
        hold = t.get('hold_duration_minutes', 0)
        
        print(f"   {ts} | {strategy} {prob:.0%} | OBI:{obi:.2f} | Vol:{vol_ratio:.1f}x")
        print(f"      最高+{max_p:.1f}% 最低-{max_dd:.1f}% | 持倉{hold:.0f}分 | ${pnl:+.2f}")
    
    # 找出區分條件
    print(f"\n" + "=" * 100)
    print("🎯 可能的區分條件")
    print("=" * 100)
    
    # 檢查是否有明顯區分
    # 1. 檢查進場時是否已經有負面跡象
    if recovered and not_recovered:
        # OBI 差異
        rec_obi = [t.get('obi', 0) for t in recovered]
        not_rec_obi = [t.get('obi', 0) for t in not_recovered]
        avg_rec_obi = sum(rec_obi) / len(rec_obi)
        avg_not_obi = sum(not_rec_obi) / len(not_rec_obi)
        
        print(f"\n1. OBI (訂單簿不平衡):")
        print(f"   回升組平均: {avg_rec_obi:.3f}")
        print(f"   不回升組平均: {avg_not_obi:.3f}")
        if avg_rec_obi != avg_not_obi:
            print(f"   → OBI {'高' if avg_rec_obi > avg_not_obi else '低'} 的更可能回升")
        
        # Volume ratio 差異
        rec_vol = [t.get('volume_ratio', 0) for t in recovered]
        not_rec_vol = [t.get('volume_ratio', 0) for t in not_recovered]
        avg_rec_vol = sum(rec_vol) / len(rec_vol) if rec_vol else 0
        avg_not_vol = sum(not_rec_vol) / len(not_rec_vol) if not_rec_vol else 0
        
        print(f"\n2. Volume Ratio (成交量比):")
        print(f"   回升組平均: {avg_rec_vol:.2f}x")
        print(f"   不回升組平均: {avg_not_vol:.2f}x")
        
        # 最大獲利差異 (進場時機)
        rec_mp = [t.get('max_profit_pct', 0) for t in recovered]
        not_rec_mp = [t.get('max_profit_pct', 0) for t in not_recovered]
        
        print(f"\n3. 最大獲利點 (進場時機品質):")
        print(f"   回升組平均最高: +{sum(rec_mp)/len(rec_mp):.1f}%")
        print(f"   不回升組平均最高: +{sum(not_rec_mp)/len(not_rec_mp):.1f}%")
        if sum(rec_mp)/len(rec_mp) > sum(not_rec_mp)/len(not_rec_mp):
            print(f"   → 回升組曾經漲過，不回升組一進場就跌")
            print(f"   💡 建議：進場後快速跌 (沒先漲) 就快停損")
    
    # 實際建議
    print(f"\n" + "=" * 100)
    print("💡 最終建議")
    print("=" * 100)
    
    # 分析不回升組的特徵
    never_up = [t for t in not_recovered if t.get('max_profit_pct', 0) < 1]
    print(f"\n🚨 危險特徵：進場後從未上漲超過 1%")
    print(f"   這類交易: {len(never_up)} 筆")
    total_loss = sum(t.get('net_pnl_usdt', 0) for t in never_up)
    print(f"   造成虧損: ${total_loss:.2f}")
    
    print(f"\n📋 建議策略:")
    print(f"   1. 進場後如果 5 分鐘內沒有上漲超過 1%，提早停損")
    print(f"   2. 保持寬鬆止損 (12-14%)，不要被洗掉")
    print(f"   3. 在 +4-5% 時部分止盈，鎖定利潤")

if __name__ == "__main__":
    main()
