"""
簡化診斷腳本 - 直接輸出決策信息

使用方式：
    python scripts/diagnose_simple.py [minutes]
"""

import asyncio
import sys
import json
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict

sys.path.append(str(Path(__file__).parent.parent))

# 直接複用 real_trading_simulation.py 的邏輯
from scripts.real_trading_simulation import RealTradingSimulator


async def run_diagnosis(duration_minutes: int = 2):
    """運行診斷"""
    print("=" * 80)
    print(f"🔍 簡化診斷模式")
    print("=" * 80)
    print(f"⏰ 時長: {duration_minutes} 分鐘")
    print("=" * 80)
    print()
    
    simulator = RealTradingSimulator()
    
    # 運行指定時長
    await simulator.run(duration_minutes=duration_minutes)
    
    # 分析結果
    print()
    print("=" * 80)
    print("📊 診斷報告")
    print("=" * 80)
    print()
    
    total = simulator.total_decisions
    if total == 0:
        print("❌ 沒有記錄任何決策！")
        return
    
    # 統計信號
    signal_counter = Counter()
    risk_counter = Counter()
    confidence_bins = defaultdict(int)
    vpin_bins = defaultdict(int)
    block_reasons = defaultdict(int)
    
    tradeable_count = 0
    
    for log in simulator.decisions_log:
        decision = log['decision']
        market_data = log['market_data']
        
        signal = decision['signal']['direction']
        confidence = decision['signal']['confidence']
        risk = decision['regime']['risk_level']
        can_trade = decision['can_trade']
        vpin = market_data.get('vpin', 0)
        
        signal_counter[signal] += 1
        risk_counter[risk] += 1
        
        if can_trade:
            tradeable_count += 1
        else:
            reasons = decision['regime']['blocked_reasons']
            for reason in reasons:
                if 'VPIN' in reason:
                    block_reasons['VPIN 過高'] += 1
                elif 'spread' in reason or '價差' in reason:
                    block_reasons['Spread 過寬'] += 1
                elif 'depth' in reason or '深度' in reason:
                    block_reasons['Depth 不足'] += 1
        
        # 信心度分箱
        if confidence < 0.3:
            confidence_bins['< 0.3'] += 1
        elif confidence < 0.4:
            confidence_bins['0.3-0.4'] += 1
        elif confidence < 0.5:
            confidence_bins['0.4-0.5'] += 1
        elif confidence < 0.6:
            confidence_bins['0.5-0.6'] += 1
        else:
            confidence_bins['>= 0.6'] += 1
        
        # VPIN 分箱
        if vpin < 0.3:
            vpin_bins['< 0.3 (SAFE)'] += 1
        elif vpin < 0.5:
            vpin_bins['0.3-0.5 (WARNING)'] += 1
        elif vpin < 0.65:
            vpin_bins['0.5-0.65 (WARNING+)'] += 1
        elif vpin < 0.7:
            vpin_bins['0.65-0.7 (DANGER)'] += 1
        else:
            vpin_bins['>= 0.7 (CRITICAL)'] += 1
    
    # 打印報告
    print(f"📊 基本統計")
    print("─" * 80)
    print(f"   總決策次數: {total}")
    print(f"   可交易決策: {tradeable_count} ({tradeable_count/total*100:.1f}%)")
    print(f"   被阻擋決策: {total - tradeable_count} ({(total-tradeable_count)/total*100:.1f}%)")
    print()
    
    print("🎯 信號分布")
    print("─" * 80)
    for signal in ['LONG', 'SHORT', 'NEUTRAL']:
        count = signal_counter[signal]
        if count > 0:
            emoji = "📈" if signal == "LONG" else "📉" if signal == "SHORT" else "⚖️"
            bar = "█" * min(50, int(count / total * 50))
            print(f"   {emoji} {signal:8s}: {count:3d} ({count/total*100:5.1f}%) {bar}")
    print()
    
    print("🔒 風險等級分布")
    print("─" * 80)
    risk_emoji = {'SAFE': '🟢', 'WARNING': '🟡', 'DANGER': '🟠', 'CRITICAL': '🔴'}
    for risk in ['SAFE', 'WARNING', 'DANGER', 'CRITICAL']:
        count = risk_counter[risk]
        if count > 0:
            emoji = risk_emoji.get(risk, '⚪')
            bar = "█" * min(50, int(count / total * 50))
            print(f"   {emoji} {risk:10s}: {count:3d} ({count/total*100:5.1f}%) {bar}")
    print()
    
    print("💪 信心度分布")
    print("─" * 80)
    for bin_range in ['< 0.3', '0.3-0.4', '0.4-0.5', '0.5-0.6', '>= 0.6']:
        count = confidence_bins[bin_range]
        if count > 0:
            bar = "█" * min(50, int(count / total * 50))
            print(f"   {bin_range:10s}: {count:3d} ({count/total*100:5.1f}%) {bar}")
    print()
    
    print("☠️  VPIN 分布")
    print("─" * 80)
    for bin_range in ['< 0.3 (SAFE)', '0.3-0.5 (WARNING)', '0.5-0.65 (WARNING+)', 
                      '0.65-0.7 (DANGER)', '>= 0.7 (CRITICAL)']:
        count = vpin_bins[bin_range]
        if count > 0:
            bar = "█" * min(50, int(count / total * 50))
            print(f"   {bin_range:20s}: {count:3d} ({count/total*100:5.1f}%) {bar}")
    print()
    
    if block_reasons:
        print("🚫 阻擋原因統計")
        print("─" * 80)
        for reason, count in sorted(block_reasons.items(), key=lambda x: -x[1]):
            bar = "█" * min(50, int(count / (total - tradeable_count) * 50))
            print(f"   {reason:20s}: {count:3d} ({count/(total-tradeable_count)*100:5.1f}%) {bar}")
        print()
    
    # 診斷結論
    print("┌" + "─" * 78 + "┐")
    print("│" + " " * 28 + "🔍 診斷結論" + " " * 38 + "│")
    print("└" + "─" * 78 + "┘")
    print()
    
    if tradeable_count == 0:
        print("❌ 問題：沒有任何可交易機會")
        print()
        
        neutral_pct = signal_counter.get('NEUTRAL', 0) / total * 100
        low_conf_pct = sum(confidence_bins.get(k, 0) for k in ['< 0.3', '0.3-0.4']) / total * 100
        high_vpin_pct = sum(vpin_bins.get(k, 0) for k in ['0.65-0.7 (DANGER)', '>= 0.7 (CRITICAL)']) / total * 100
        
        if neutral_pct > 80:
            print(f"📌 主因 1: 信號太弱（{neutral_pct:.0f}% 為 NEUTRAL）")
            print(f"   → {low_conf_pct:.0f}% 的信心度 < 0.4")
            print("   → 當前閾值: 0.4 (已經很寬鬆)")
            print()
            print("💡 建議：")
            print("   1. 市場可能處於橫盤整理，缺乏明確趨勢")
            print("   2. 可以等待波動加大的時段（如美股開盤）")
            print("   3. 或考慮使用更激進的參數（signal_threshold = 0.3）")
            print()
        
        if high_vpin_pct > 50:
            print(f"📌 主因 2: VPIN 過高（{high_vpin_pct:.0f}% >= 0.65）")
            print(f"   → {vpin_bins.get('>= 0.7 (CRITICAL)', 0)} 次達到 CRITICAL")
            print()
            print("💡 建議：")
            print("   1. 當前市場知情交易者活躍（高風險）")
            print("   2. 建議等待 VPIN 降至 0.5 以下再交易")
            print("   3. 這是保護機制，避免 Flash Crash 損失")
            print()


async def main():
    duration = 2
    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
        except ValueError:
            print("❌ 參數錯誤：請提供分鐘數（整數）")
            sys.exit(1)
    
    await run_diagnosis(duration)


if __name__ == "__main__":
    asyncio.run(main())
