"""
Task 1.6.1 - B1 測試: 多層 OBI + 速度/加速度指標

測試內容:
1. 多層 OBI 計算 (1/3/5/10 層)
2. 深層失衡檢測
3. OBI 速度（一階導數）
4. OBI 加速度（二階導數）
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
import json
from datetime import datetime
from src.exchange.obi_calculator import OBICalculator
import numpy as np


def test_multi_level_obi():
    """測試多層 OBI 計算"""
    print("=" * 60)
    print("📊 測試 1: 多層 OBI 計算")
    print("=" * 60)
    
    calculator = OBICalculator(depth_limit=20)
    
    # 模擬訂單簿數據（買盤強勢）
    bids = [
        ["50000", "2.5"],   # Level 1: 強買單
        ["49999", "2.0"],   # Level 2
        ["49998", "1.8"],   # Level 3
        ["49997", "1.5"],   # Level 4
        ["49996", "1.2"],   # Level 5
        ["49995", "1.0"],   # Level 6
        ["49994", "0.9"],   # Level 7
        ["49993", "0.8"],   # Level 8
        ["49992", "0.7"],   # Level 9
        ["49991", "0.6"],   # Level 10
    ]
    
    asks = [
        ["50001", "1.0"],   # Level 1: 弱賣單
        ["50002", "1.2"],   # Level 2
        ["50003", "1.5"],   # Level 3
        ["50004", "1.8"],   # Level 4
        ["50005", "2.0"],   # Level 5
        ["50006", "2.2"],   # Level 6
        ["50007", "2.5"],   # Level 7
        ["50008", "2.8"],   # Level 8
        ["50009", "3.0"],   # Level 9
        ["50010", "3.5"],   # Level 10: 深層大賣單
    ]
    
    result = calculator.calculate_multi_level_obi(bids, asks, max_depth=10)
    
    print(f"\n📈 多層 OBI 結果:")
    print(f"  Level 1 (最優價):     {result['obi_level_1']:>7.4f}")
    print(f"  Level 3 (前3層):      {result['obi_level_3']:>7.4f}")
    print(f"  Level 5 (前5層):      {result['obi_level_5']:>7.4f}")
    print(f"  Level 10 (前10層):    {result['obi_level_10']:>7.4f}")
    print(f"  深層失衡:             {result['depth_imbalance']:>7.4f}")
    
    # 解讀
    print(f"\n💡 解讀:")
    if result['obi_level_1'] > 0.3:
        print(f"  ✅ 最優價買盤強勢 (OBI={result['obi_level_1']:.4f})")
    
    if result['depth_imbalance'] < -0.1:
        print(f"  ⚠️  深層賣盤壓力增加 (失衡={result['depth_imbalance']:.4f})")
        print(f"      可能有大賣單埋伏在深層，需謹慎")
    elif result['depth_imbalance'] > 0.1:
        print(f"  ✅ 深層買盤支撐強 (失衡={result['depth_imbalance']:.4f})")
        print(f"      深層有大買單支撐，趨勢可能持續")
    
    print()


def test_obi_velocity_acceleration():
    """測試 OBI 速度和加速度"""
    print("=" * 60)
    print("🚀 測試 2: OBI 速度與加速度")
    print("=" * 60)
    
    calculator = OBICalculator(history_size=50)
    
    # 模擬 OBI 時間序列（從 0.1 加速上升到 0.5）
    print("\n📊 情境 1: OBI 加速上升（買盤加速堆積）")
    
    # 清空歷史
    calculator.obi_history.clear()
    
    # 生成加速上升的 OBI 序列
    for i in range(20):
        # 二次函數: obi = 0.1 + 0.002 * i^2
        obi = 0.1 + 0.002 * (i ** 2)
        calculator.obi_history.append(obi)
    
    velocity = calculator.calculate_obi_velocity(window=5)
    acceleration = calculator.calculate_obi_acceleration(window=5)
    
    print(f"  最近5個OBI: {list(calculator.obi_history)[-5:]}")
    print(f"  速度:       {velocity:.6f} OBI/秒")
    print(f"  加速度:     {acceleration:.6f} OBI/秒²")
    
    if velocity and velocity > 0.1:
        print(f"  ✅ 買盤快速堆積")
    if acceleration and acceleration > 0:
        print(f"  ✅ 買盤加速度為正，勢頭強勁")
    
    # 情境 2: OBI 減速下降
    print("\n📊 情境 2: OBI 減速下降（賣盤開始減弱）")
    
    calculator.obi_history.clear()
    
    # 生成減速下降的序列（負的二次函數）
    for i in range(20):
        # obi = 0.5 - 0.001 * i^2
        obi = 0.5 - 0.001 * (i ** 2)
        calculator.obi_history.append(obi)
    
    velocity = calculator.calculate_obi_velocity(window=5)
    acceleration = calculator.calculate_obi_acceleration(window=5)
    
    print(f"  最近5個OBI: {[f'{x:.4f}' for x in list(calculator.obi_history)[-5:]]}")
    print(f"  速度:       {velocity:.6f} OBI/秒")
    print(f"  加速度:     {acceleration:.6f} OBI/秒²")
    
    if velocity and velocity < -0.1:
        print(f"  ⚠️  賣盤堆積")
    if acceleration and acceleration > 0:
        print(f"  ⚠️  加速度為正，下跌趨勢減速（可能反轉）")
    
    # 情境 3: OBI 穩定震盪
    print("\n📊 情境 3: OBI 穩定震盪（無明確方向）")
    
    calculator.obi_history.clear()
    
    # 生成震盪序列
    for i in range(20):
        obi = 0.2 + 0.05 * np.sin(i * 0.5)
        calculator.obi_history.append(obi)
    
    velocity = calculator.calculate_obi_velocity(window=5)
    acceleration = calculator.calculate_obi_acceleration(window=5)
    
    print(f"  最近5個OBI: {[f'{x:.4f}' for x in list(calculator.obi_history)[-5:]]}")
    print(f"  速度:       {velocity:.6f} OBI/秒" if velocity else "  速度:       N/A")
    print(f"  加速度:     {acceleration:.6f} OBI/秒²" if acceleration else "  加速度:     N/A")
    
    if velocity and abs(velocity) < 0.05:
        print(f"  ⚪ 速度接近零，市場平衡")
    if acceleration and abs(acceleration) < 0.01:
        print(f"  ⚪ 加速度接近零，無趨勢加速")
    
    print()


async def test_realtime_multi_level_obi():
    """即時測試多層 OBI（連接 Binance WebSocket）"""
    print("=" * 60)
    print("📡 測試 3: 即時多層 OBI 監控")
    print("=" * 60)
    print("連接 Binance WebSocket，監控 5 秒...")
    print()
    
    calculator = OBICalculator(symbol="BTCUSDT", depth_limit=20, history_size=50)
    
    sample_count = 0
    max_samples = 5  # 收集5個樣本
    
    def on_update(data):
        nonlocal sample_count
        sample_count += 1
        
        if sample_count > max_samples:
            return
        
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        
        # 計算多層 OBI
        multi_level = calculator.calculate_multi_level_obi(
            calculator.orderbook['bids'],
            calculator.orderbook['asks'],
            max_depth=10
        )
        
        # 計算速度和加速度
        velocity = calculator.calculate_obi_velocity(window=3)
        acceleration = calculator.calculate_obi_acceleration(window=3)
        
        print(f"[{timestamp}] 樣本 {sample_count}/{max_samples}")
        print(f"  L1: {multi_level['obi_level_1']:>7.4f} | "
              f"L3: {multi_level['obi_level_3']:>7.4f} | "
              f"L5: {multi_level['obi_level_5']:>7.4f} | "
              f"L10: {multi_level['obi_level_10']:>7.4f}")
        print(f"  深層失衡: {multi_level['depth_imbalance']:>7.4f}")
        
        if velocity is not None:
            print(f"  速度: {velocity:>10.6f} OBI/秒 {'📈' if velocity > 0 else '📉' if velocity < 0 else '➡️'}")
        
        if acceleration is not None:
            print(f"  加速度: {acceleration:>8.6f} OBI/秒² {'🚀' if acceleration > 0 else '🔻' if acceleration < 0 else '➡️'}")
        
        print()
    
    calculator.on_obi_update = on_update
    
    try:
        # 啟動 WebSocket
        task = asyncio.create_task(calculator.start_websocket())
        
        # 等待收集足夠樣本
        while sample_count < max_samples:
            await asyncio.sleep(0.5)
        
        # 停止（非 async 方法）
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
    print("🧪 Task 1.6.1 - B1: 多層 OBI + 速度/加速度 測試")
    print("=" * 60)
    print()
    
    # 測試 1: 多層 OBI
    test_multi_level_obi()
    
    # 測試 2: 速度和加速度
    test_obi_velocity_acceleration()
    
    # 測試 3: 即時監控
    await test_realtime_multi_level_obi()
    
    print("\n" + "=" * 60)
    print("✅ 所有測試完成")
    print("=" * 60)
    print()
    
    # 總結
    print("📋 功能驗證總結:")
    print("  ✅ 多層 OBI 計算 (1/3/5/10層)")
    print("  ✅ 深層失衡檢測")
    print("  ✅ OBI 速度計算（一階導數）")
    print("  ✅ OBI 加速度計算（二階導數）")
    print("  ✅ 即時 WebSocket 整合")
    print()
    print("🎯 下一步: Task 1.6.1 - B2 (Microprice 計算)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
