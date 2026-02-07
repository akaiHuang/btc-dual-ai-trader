#!/usr/bin/env python3
"""
測試 AI-Wolf Bridge 增強功能
驗證三個優先級的資料是否正確填充
"""

import json
import os
from datetime import datetime

def test_bridge_structure():
    """測試 Bridge 結構完整性"""
    bridge_file = "ai_wolf_bridge.json"
    
    if not os.path.exists(bridge_file):
        print("❌ Bridge file not found!")
        return False
    
    with open(bridge_file, 'r') as f:
        bridge = json.load(f)
    
    print("="*70)
    print("🔍 AI-Wolf Bridge 結構測試")
    print("="*70)
    
    # 測試基本結構
    required_sections = ['ai_to_wolf', 'wolf_to_ai', 'feedback_loop']
    for section in required_sections:
        status = "✅" if section in bridge else "❌"
        print(f"{status} Section: {section}")
    
    print("\n" + "="*70)
    print("📊 Priority 1: 鯨魚追蹤 (Whale Status)")
    print("="*70)
    
    wolf_to_ai = bridge.get('wolf_to_ai', {})
    whale_status = wolf_to_ai.get('whale_status', {})
    
    required_whale_fields = [
        'current_direction',
        'dominance',
        'flip_count_30min',
        'net_qty_btc'
    ]
    
    for field in required_whale_fields:
        status = "✅" if field in whale_status else "❌"
        value = whale_status.get(field, 'N/A')
        print(f"{status} {field}: {value}")
    
    print("\n" + "="*70)
    print("🔬 Priority 1: 市場微結構 (Market Microstructure)")
    print("="*70)
    
    micro = wolf_to_ai.get('market_microstructure', {})
    required_micro_fields = [
        'obi',
        'vpin',
        'spread_bps',
        'funding_rate',
        'depth_imbalance'
    ]
    
    for field in required_micro_fields:
        status = "✅" if field in micro else "❌"
        value = micro.get(field, 'N/A')
        print(f"{status} {field}: {value}")
    
    print("\n" + "="*70)
    print("🌊 Priority 1: 波動環境 (Volatility)")
    print("="*70)
    
    volatility = wolf_to_ai.get('volatility', {})
    required_vol_fields = [
        'atr_pct',
        'regime',
        'is_dead_market',
        'bb_width_pct'
    ]
    
    for field in required_vol_fields:
        status = "✅" if field in volatility else "❌"
        value = volatility.get(field, 'N/A')
        print(f"{status} {field}: {value}")
    
    print("\n" + "="*70)
    print("🎯 Priority 2: 預測準確度 (Prediction Accuracy)")
    print("="*70)
    
    feedback = bridge.get('feedback_loop', {})
    prediction_accuracy = feedback.get('prediction_accuracy', {})
    recent_predictions = feedback.get('recent_predictions', [])
    
    if prediction_accuracy:
        print(f"✅ avg_price_error_pct: {prediction_accuracy.get('avg_price_error_pct', 'N/A')}")
        print(f"✅ direction_accuracy_pct: {prediction_accuracy.get('direction_accuracy_pct', 'N/A')}")
        print(f"✅ sample_size: {prediction_accuracy.get('sample_size', 'N/A')}")
    else:
        print("⚠️ No prediction accuracy data (normal if no trades completed)")
    
    print(f"\n📝 Recent predictions count: {len(recent_predictions)}")
    if recent_predictions:
        latest = recent_predictions[-1]
        print(f"   Latest: Predicted ${latest.get('predicted_price', 0):.0f} | "
              f"Actual ${latest.get('actual_price', 0):.0f} | "
              f"Error: {latest.get('error_pct', 0):.2f}%")
    
    print("\n" + "="*70)
    print("⚠️ Priority 3: 風險指標 (Risk Indicators)")
    print("="*70)
    
    risk = wolf_to_ai.get('risk_indicators', {})
    required_risk_fields = [
        'liquidation_pressure',
        'orderbook_toxicity',
        'whale_trap_probability'
    ]
    
    for field in required_risk_fields:
        status = "✅" if field in risk else "❌"
        value = risk.get(field, 'N/A')
        
        # 風險等級標示
        if field == 'liquidation_pressure' and isinstance(value, (int, float)):
            if value > 70:
                indicator = "🔴 HIGH"
            elif value > 40:
                indicator = "🟡 MEDIUM"
            else:
                indicator = "🟢 LOW"
            print(f"{status} {field}: {value} {indicator}")
        elif field == 'whale_trap_probability' and isinstance(value, (int, float)):
            if value > 0.6:
                indicator = "🔴 HIGH"
            elif value > 0.3:
                indicator = "🟡 MEDIUM"
            else:
                indicator = "🟢 LOW"
            print(f"{status} {field}: {value:.2f} {indicator}")
        else:
            print(f"{status} {field}: {value}")
    
    print("\n" + "="*70)
    print("📈 完整性評分")
    print("="*70)
    
    total_fields = (
        len(required_whale_fields) +
        len(required_micro_fields) +
        len(required_vol_fields) +
        len(required_risk_fields)
    )
    
    present_fields = sum([
        sum(1 for f in required_whale_fields if f in whale_status),
        sum(1 for f in required_micro_fields if f in micro),
        sum(1 for f in required_vol_fields if f in volatility),
        sum(1 for f in required_risk_fields if f in risk)
    ])
    
    completeness = (present_fields / total_fields) * 100
    
    if completeness == 100:
        grade = "🏆 PERFECT"
    elif completeness >= 80:
        grade = "✅ EXCELLENT"
    elif completeness >= 60:
        grade = "⚠️ GOOD"
    else:
        grade = "❌ NEEDS WORK"
    
    print(f"\nBridge 完整性: {present_fields}/{total_fields} fields ({completeness:.1f}%) {grade}")
    
    return completeness == 100


def test_ai_reading_capability():
    """測試 AI 是否能正確讀取新增資料"""
    print("\n" + "="*70)
    print("🤖 AI 讀取能力測試")
    print("="*70)
    
    bridge_file = "ai_wolf_bridge.json"
    
    if not os.path.exists(bridge_file):
        print("❌ Bridge file not found!")
        return False
    
    with open(bridge_file, 'r') as f:
        bridge = json.load(f)
    
    wolf_to_ai = bridge.get('wolf_to_ai', {})
    
    # 模擬 AI 讀取邏輯
    print("\n🔍 AI 正在分析...")
    
    # 1. 鯨魚狀態
    whale_status = wolf_to_ai.get('whale_status', {})
    whale_direction = whale_status.get('current_direction')
    whale_dominance = whale_status.get('dominance', 0)
    
    if whale_direction:
        print(f"✅ AI 識別鯨魚方向: {whale_direction} (集中度: {whale_dominance:.2f})")
    else:
        print("⚠️ AI 無法識別鯨魚方向")
    
    # 2. 市場狀態
    micro = wolf_to_ai.get('market_microstructure', {})
    volatility = wolf_to_ai.get('volatility', {})
    
    if micro and volatility:
        obi = micro.get('obi', 0)
        regime = volatility.get('regime', 'UNKNOWN')
        is_dead = volatility.get('is_dead_market', False)
        
        print(f"✅ AI 識別市場狀態: {regime} | OBI: {obi:.2f} | 死水盤: {is_dead}")
    else:
        print("⚠️ AI 無法完整識別市場狀態")
    
    # 3. 風險評估
    risk = wolf_to_ai.get('risk_indicators', {})
    if risk:
        liq_pressure = risk.get('liquidation_pressure', 0)
        trap_prob = risk.get('whale_trap_probability', 0)
        
        print(f"✅ AI 評估風險: 清算壓力 {liq_pressure}/100 | 陷阱機率 {trap_prob:.0%}")
    else:
        print("⚠️ AI 無法評估風險")
    
    return True


if __name__ == "__main__":
    print("\n🚀 開始測試 AI-Wolf Bridge 增強功能\n")
    
    test1_passed = test_bridge_structure()
    test2_passed = test_ai_reading_capability()
    
    print("\n" + "="*70)
    print("📊 測試總結")
    print("="*70)
    print(f"Bridge 結構測試: {'✅ PASSED' if test1_passed else '❌ FAILED'}")
    print(f"AI 讀取測試: {'✅ PASSED' if test2_passed else '❌ FAILED'}")
    
    if test1_passed and test2_passed:
        print("\n🎉 所有測試通過！Bridge 增強功能正常運作。")
    else:
        print("\n⚠️ 部分測試失敗，需要檢查實現。")
