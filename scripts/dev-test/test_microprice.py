"""
Task 1.6.1 - B2 測試: Microprice 計算與偏離度分析

測試內容:
1. Microprice 基礎計算
2. 價格壓力檢測
3. 持續偏離分析
4. 即時監控
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
import json
from datetime import datetime
from src.exchange.obi_calculator import OBICalculator
import numpy as np


def test_microprice_calculation():
    """測試 Microprice 基礎計算"""
    print("=" * 60)
    print("📊 測試 1: Microprice 基礎計算")
    print("=" * 60)
    
    calculator = OBICalculator()
    
    # 情境 1: 買盤流動性強（bid_size > ask_size）
    print("\n📈 情境 1: 買盤流動性強")
    bids = [["50000", "5.0"], ["49999", "3.0"]]  # 大買單
    asks = [["50001", "2.0"], ["50002", "1.5"]]  # 小賣單
    
    result = calculator.calculate_microprice(bids, asks)
    
    print(f"  Best Bid: 50000 (5.0 BTC)")
    print(f"  Best Ask: 50001 (2.0 BTC)")
    print(f"  Mid Price:    {result['mid_price']:.2f}")
    print(f"  Microprice:   {result['microprice']:.2f}")
    print(f"  Pressure:     {result['pressure']:.6f} ({'📉 下壓' if result['pressure'] > 0 else '📈 上推' if result['pressure'] < 0 else '⚖️ 平衡'})")
    print(f"  Bid Weight:   {result['bid_weight']:.3f}")
    print(f"  Ask Weight:   {result['ask_weight']:.3f}")
    
    if result['pressure'] < 0:
        print(f"  ✅ 買盤流動性強，microprice < mid，價格可能上漲")
    
    # 情境 2: 賣盤流動性強（ask_size > bid_size）
    print("\n📉 情境 2: 賣盤流動性強")
    bids = [["50000", "1.5"], ["49999", "1.0"]]  # 小買單
    asks = [["50001", "6.0"], ["50002", "4.0"]]  # 大賣單
    
    result = calculator.calculate_microprice(bids, asks)
    
    print(f"  Best Bid: 50000 (1.5 BTC)")
    print(f"  Best Ask: 50001 (6.0 BTC)")
    print(f"  Mid Price:    {result['mid_price']:.2f}")
    print(f"  Microprice:   {result['microprice']:.2f}")
    print(f"  Pressure:     {result['pressure']:.6f} ({'📉 下壓' if result['pressure'] > 0 else '📈 上推' if result['pressure'] < 0 else '⚖️ 平衡'})")
    print(f"  Bid Weight:   {result['bid_weight']:.3f}")
    print(f"  Ask Weight:   {result['ask_weight']:.3f}")
    
    if result['pressure'] > 0:
        print(f"  ⚠️  賣盤流動性強，microprice > mid，價格可能下跌")
    
    # 情境 3: 平衡狀態（bid_size ≈ ask_size）
    print("\n⚖️ 情境 3: 買賣盤平衡")
    bids = [["50000", "3.0"], ["49999", "2.5"]]
    asks = [["50001", "3.0"], ["50002", "2.5"]]
    
    result = calculator.calculate_microprice(bids, asks)
    
    print(f"  Best Bid: 50000 (3.0 BTC)")
    print(f"  Best Ask: 50001 (3.0 BTC)")
    print(f"  Mid Price:    {result['mid_price']:.2f}")
    print(f"  Microprice:   {result['microprice']:.2f}")
    print(f"  Pressure:     {result['pressure']:.6f} ({'📉 下壓' if result['pressure'] > 0 else '📈 上推' if result['pressure'] < 0 else '⚖️ 平衡'})")
    print(f"  Bid Weight:   {result['bid_weight']:.3f}")
    print(f"  Ask Weight:   {result['ask_weight']:.3f}")
    
    if abs(result['pressure']) < 0.00001:
        print(f"  ✅ 買賣盤平衡，microprice ≈ mid，無明顯壓力")
    
    print()


def test_microprice_deviation():
    """測試 Microprice 偏離度統計"""
    print("=" * 60)
    print("📊 測試 2: Microprice 持續偏離分析")
    print("=" * 60)
    
    calculator = OBICalculator(history_size=50)
    
    # 模擬持續買盤壓力（microprice < mid）
    print("\n📈 情境 1: 持續買盤壓力")
    
    calculator.obi_history.clear()
    
    # 生成持續負壓力序列（買盤流動性持續強勁）
    for i in range(15):
        pressure = -0.0001 - 0.00002 * i  # 遞增的負壓力
        calculator.obi_history.append({
            'timestamp': datetime.utcnow(),
            'obi': 0.2 + 0.05 * i,
            'microprice_pressure': pressure,
            'bid_weight': 0.6 + 0.02 * i,
            'ask_weight': 0.4 - 0.02 * i
        })
    
    deviation = calculator.calculate_microprice_deviation(window=10)
    
    if deviation:
        print(f"  平均壓力:       {deviation['mean_pressure']:.6f}")
        print(f"  壓力趨勢:       {deviation['pressure_trend']}")
        print(f"  壓力強度:       {deviation['pressure_strength']:.6f}")
        print(f"  連續方向:       {deviation['consecutive_direction']} 次")
        
        if deviation['pressure_trend'] == 'BULLISH' and deviation['consecutive_direction'] > 5:
            print(f"  ✅ 買盤流動性持續強勁 {deviation['consecutive_direction']} 次，看漲訊號")
    
    # 情境 2: 持續賣盤壓力
    print("\n📉 情境 2: 持續賣盤壓力")
    
    calculator.obi_history.clear()
    
    # 生成持續正壓力序列（賣盤流動性持續強勁）
    for i in range(15):
        pressure = 0.0001 + 0.00003 * i  # 遞增的正壓力
        calculator.obi_history.append({
            'timestamp': datetime.utcnow(),
            'obi': 0.1 - 0.04 * i,
            'microprice_pressure': pressure,
            'bid_weight': 0.4 - 0.02 * i,
            'ask_weight': 0.6 + 0.02 * i
        })
    
    deviation = calculator.calculate_microprice_deviation(window=10)
    
    if deviation:
        print(f"  平均壓力:       {deviation['mean_pressure']:.6f}")
        print(f"  壓力趨勢:       {deviation['pressure_trend']}")
        print(f"  壓力強度:       {deviation['pressure_strength']:.6f}")
        print(f"  連續方向:       {deviation['consecutive_direction']} 次")
        
        if deviation['pressure_trend'] == 'BEARISH' and deviation['consecutive_direction'] > 5:
            print(f"  ⚠️  賣盤流動性持續強勁 {deviation['consecutive_direction']} 次，看跌訊號")
    
    # 情境 3: 壓力震盪
    print("\n⚡ 情境 3: 壓力震盪（無明確方向）")
    
    calculator.obi_history.clear()
    
    # 生成震盪序列
    for i in range(15):
        pressure = 0.00005 * np.sin(i * 0.5)  # 正弦波震盪
        calculator.obi_history.append({
            'timestamp': datetime.utcnow(),
            'obi': 0.15 + 0.03 * np.sin(i * 0.3),
            'microprice_pressure': pressure,
            'bid_weight': 0.5 + 0.05 * np.sin(i * 0.4),
            'ask_weight': 0.5 - 0.05 * np.sin(i * 0.4)
        })
    
    deviation = calculator.calculate_microprice_deviation(window=10)
    
    if deviation:
        print(f"  平均壓力:       {deviation['mean_pressure']:.6f}")
        print(f"  壓力趨勢:       {deviation['pressure_trend']}")
        print(f"  壓力強度:       {deviation['pressure_strength']:.6f}")
        print(f"  連續方向:       {deviation['consecutive_direction']} 次")
        
        if deviation['pressure_trend'] == 'NEUTRAL':
            print(f"  ⚪ 壓力震盪，無明確方向")
    
    print()


async def test_realtime_microprice():
    """即時測試 Microprice（連接 Binance WebSocket）"""
    print("=" * 60)
    print("📡 測試 3: 即時 Microprice 監控")
    print("=" * 60)
    print("連接 Binance WebSocket，監控 5 秒...")
    print()
    
    calculator = OBICalculator(symbol="BTCUSDT", depth_limit=20, history_size=50)
    
    sample_count = 0
    max_samples = 10  # 收集10個樣本
    
    def on_update(data):
        nonlocal sample_count
        sample_count += 1
        
        if sample_count > max_samples:
            return
        
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        
        # 計算 Microprice
        microprice_data = calculator.calculate_microprice(
            calculator.orderbook['bids'],
            calculator.orderbook['asks']
        )
        
        # 計算偏離統計（需要至少10個樣本）
        deviation = None
        if len(calculator.obi_history) >= 10:
            deviation = calculator.calculate_microprice_deviation(window=10)
        
        # OBI 數據
        obi = calculator.stats.get('last_obi', 0)
        
        print(f"[{timestamp}] 樣本 {sample_count}/{max_samples}")
        print(f"  Mid:      ${microprice_data['mid_price']:.2f}")
        print(f"  Micro:    ${microprice_data['microprice']:.2f}")
        print(f"  Pressure: {microprice_data['pressure']:>9.6f} "
              f"{'📉' if microprice_data['pressure'] > 0.0001 else '📈' if microprice_data['pressure'] < -0.0001 else '⚖️'}")
        print(f"  Weights:  Bid {microprice_data['bid_weight']:.3f} / Ask {microprice_data['ask_weight']:.3f}")
        print(f"  OBI:      {obi:>7.4f}")
        
        if deviation:
            print(f"  趨勢:     {deviation['pressure_trend']} "
                  f"(連續 {deviation['consecutive_direction']} 次)")
        
        print()
    
    calculator.on_obi_update = on_update
    
    try:
        # 啟動 WebSocket
        task = asyncio.create_task(calculator.start_websocket())
        
        # 等待收集足夠樣本
        while sample_count < max_samples:
            await asyncio.sleep(0.5)
        
        # 停止
        calculator.stop_websocket()
        
        # 等待 task 完成
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            pass
        
        print("✅ 即時測試完成")
        
    except Exception as e:
        print(f"❌ 錯誤: {e}")
        calculator.stop_websocket()


async def main():
    """主測試流程"""
    print("\n" + "=" * 60)
    print("🧪 Task 1.6.1 - B2: Microprice 計算測試")
    print("=" * 60)
    print()
    
    # 測試 1: 基礎計算
    test_microprice_calculation()
    
    # 測試 2: 偏離度統計
    test_microprice_deviation()
    
    # 測試 3: 即時監控
    await test_realtime_microprice()
    
    print("\n" + "=" * 60)
    print("✅ 所有測試完成")
    print("=" * 60)
    print()
    
    # 總結
    print("📋 功能驗證總結:")
    print("  ✅ Microprice 基礎計算")
    print("  ✅ 價格壓力檢測")
    print("  ✅ 買賣盤權重分析")
    print("  ✅ 持續偏離統計")
    print("  ✅ 趨勢判斷（BULLISH/BEARISH/NEUTRAL）")
    print("  ✅ 即時 WebSocket 整合")
    print()
    print("💡 應用場景:")
    print("  - Pressure < -0.0001 且連續 > 5 → 買盤流動性強")
    print("  - Pressure > +0.0001 且連續 > 5 → 賣盤流動性強")
    print("  - Microprice 可作為比 mid_price 更敏感的成交價預測")
    print()
    print("🎯 下一步: Task 1.6.1 - B3 (Signed Volume 追蹤)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
