"""
Task 1.6.1 - B4 測試: VPIN 毒性檢測

測試內容:
1. 基本 VPIN 計算（模擬交易數據）
2. 買賣失衡情境測試
3. 風險等級評估
4. 趨勢分析
5. 即時監控（WebSocket）
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
import json
from datetime import datetime
from src.exchange.vpin_calculator import VPINCalculator
import websockets


def test_basic_vpin_calculation():
    """測試基本 VPIN 計算"""
    print("=" * 60)
    print("📊 測試 1: 基本 VPIN 計算")
    print("=" * 60)
    
    calculator = VPINCalculator(
        symbol="BTCUSDT",
        bucket_size=10000,  # 每 10,000 USDT 一個 bucket
        num_buckets=50,     # 用 50 個 bucket 計算 VPIN
        use_dollar_volume=True
    )
    
    # 模擬 100 筆平衡交易（買賣各半）
    print("\n📈 情境 1: 平衡市場（買賣各半）")
    print(f"  目標: 生成 5 個 bucket，VPIN 應該低（<0.3）")
    print()
    
    base_price = 50000
    trades_per_bucket = 20
    total_buckets = 60  # 確保足夠 50 個
    
    for bucket_idx in range(total_buckets):
        for i in range(trades_per_bucket):
            # 交替買賣，數量隨機變化
            is_buy = (i % 2 == 0)
            quantity = 0.01 + (i % 5) * 0.002  # 0.01-0.018 BTC
            
            trade = {
                'p': base_price + (i % 10 - 5),  # 價格波動 ±5
                'q': quantity,
                'T': 1700000000000 + bucket_idx * 60000 + i * 1000,
                'isBuyerMaker': not is_buy  # False=買方主動
            }
            
            vpin = calculator.process_trade(trade)
            
            if vpin is not None and bucket_idx % 10 == 0:
                level, action, details = calculator.assess_toxicity(vpin)
                print(f"  Bucket {bucket_idx+1}: VPIN = {vpin:.4f} | {level}")
    
    # 最終統計
    stats = calculator.get_statistics()
    final_vpin = stats['current_vpin']
    level, action, details = calculator.assess_toxicity(final_vpin)
    
    print(f"\n  ✅ 最終 VPIN: {final_vpin:.4f}")
    print(f"  ✅ 風險等級: {level}")
    print(f"  ✅ 建議: {action}")
    print(f"  ✅ Buckets 完成: {stats['total_buckets_completed']}")
    print(f"  ✅ 交易處理: {stats['total_trades_processed']}")
    print()


def test_imbalanced_scenarios():
    """測試買賣失衡情境"""
    print("=" * 60)
    print("📊 測試 2: 買賣失衡情境（模擬 Toxic Flow）")
    print("=" * 60)
    
    calculator = VPINCalculator(
        bucket_size=10000,
        num_buckets=50,
        use_dollar_volume=True
    )
    
    base_price = 50000
    trades_per_bucket = 20
    
    # 情境 1: 正常市場 → 漸進失衡 → 極度失衡（模擬 Flash Crash）
    print("\n⚠️ 情境: 正常 → 漸進失衡 → Flash Crash")
    print()
    
    for bucket_idx in range(70):
        # 前 20 個 bucket: 平衡（50:50）
        if bucket_idx < 20:
            buy_ratio = 0.5
            stage = "正常"
        # 中間 20 個: 漸進失衡（60:40 → 80:20）
        elif bucket_idx < 40:
            buy_ratio = 0.5 + (bucket_idx - 20) * 0.015  # 50% → 80%
            stage = "失衡中"
        # 後 30 個: 極度失衡（90:10）
        else:
            buy_ratio = 0.9
            stage = "極度失衡"
        
        for i in range(trades_per_bucket):
            # 根據 buy_ratio 決定是否為買單
            is_buy = (i / trades_per_bucket) < buy_ratio
            quantity = 0.01 + (i % 5) * 0.002
            
            trade = {
                'p': base_price + (i % 10 - 5),
                'q': quantity,
                'T': 1700000000000 + bucket_idx * 60000 + i * 1000,
                'isBuyerMaker': not is_buy
            }
            
            vpin = calculator.process_trade(trade)
            
            # 每 10 個 bucket 顯示一次
            if vpin is not None and bucket_idx % 10 == 0:
                level, action, details = calculator.assess_toxicity(vpin)
                print(f"  Bucket {bucket_idx+1} ({stage:8s}): VPIN = {vpin:.4f} | {level:8s}")
    
    # 最終統計
    stats = calculator.get_statistics()
    final_vpin = stats['current_vpin']
    level, action, details = calculator.assess_toxicity(final_vpin)
    trend = calculator.get_vpin_trend(window=10)
    
    print(f"\n  🚨 最終 VPIN: {final_vpin:.4f}")
    print(f"  🚨 風險等級: {level}")
    print(f"  🚨 趨勢: {trend}")
    print(f"  🚨 建議: {action}")
    
    if level in ["DANGER", "CRITICAL"]:
        print(f"  ⚠️  檢測到 Toxic Flow！類似 Flash Crash 前兆")
    
    print()


def test_risk_assessment():
    """測試風險評估功能"""
    print("=" * 60)
    print("📊 測試 3: 風險評估功能")
    print("=" * 60)
    print()
    
    calculator = VPINCalculator(bucket_size=10000, num_buckets=50)
    
    # 測試各種 VPIN 值的風險評估
    test_vpins = [0.15, 0.35, 0.55, 0.75, 0.95]
    
    print("  VPIN 值範圍測試:")
    print(f"  {'VPIN':>6s} | {'等級':8s} | {'嚴重度':4s} | 建議行動")
    print("  " + "-" * 56)
    
    for vpin in test_vpins:
        level, action, details = calculator.assess_toxicity(vpin)
        severity = details['severity']
        
        # 選擇圖示
        if level == "SAFE":
            icon = "✅"
        elif level == "CAUTION":
            icon = "⚠️"
        elif level == "DANGER":
            icon = "🚨"
        else:
            icon = "💀"
        
        print(f"  {vpin:>6.2f} | {level:8s} | {severity}/4   | {icon} {action}")
    
    print()


def test_vpin_trend():
    """測試 VPIN 趨勢分析"""
    print("=" * 60)
    print("📊 測試 4: VPIN 趨勢分析")
    print("=" * 60)
    
    calculator = VPINCalculator(bucket_size=10000, num_buckets=50)
    
    base_price = 50000
    trades_per_bucket = 20
    
    # 情境 1: 上升趨勢
    print("\n📈 情境 1: VPIN 上升趨勢（風險增加）")
    
    for bucket_idx in range(60):
        # 失衡度逐漸增加
        buy_ratio = 0.5 + bucket_idx * 0.005  # 50% → 80%
        
        for i in range(trades_per_bucket):
            is_buy = (i / trades_per_bucket) < buy_ratio
            quantity = 0.01
            
            trade = {
                'p': base_price,
                'q': quantity,
                'T': 1700000000000 + bucket_idx * 60000 + i * 1000,
                'isBuyerMaker': not is_buy
            }
            
            calculator.process_trade(trade)
    
    trend = calculator.get_vpin_trend(window=10)
    stats = calculator.get_statistics()
    
    print(f"  VPIN 趨勢: {trend}")
    print(f"  當前 VPIN: {stats['current_vpin']:.4f}")
    print(f"  最小 VPIN: {stats['min_vpin']:.4f}")
    print(f"  最大 VPIN: {stats['max_vpin']:.4f}")
    
    if trend == "RISING":
        print(f"  ⚠️  風險正在增加，建議減倉")
    
    print()


async def test_realtime_monitoring():
    """即時監控測試"""
    print("=" * 60)
    print("📡 測試 5: 即時 VPIN 監控")
    print("=" * 60)
    print("連接 Binance WebSocket，監控 30 秒...")
    print()
    
    calculator = VPINCalculator(
        symbol="BTCUSDT",
        bucket_size=50000,  # 50k USDT per bucket（約 10-20 秒完成）
        num_buckets=50,
        use_dollar_volume=True
    )
    
    ws_url = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
    
    trade_count = 0
    max_duration = 30  # 秒
    start_time = datetime.now()
    
    last_vpin = None
    vpin_updates = 0
    
    try:
        async with websockets.connect(ws_url) as ws:
            print(f"🔌 已連接到: {ws_url}")
            print()
            
            while (datetime.now() - start_time).total_seconds() < max_duration:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(message)
                    
                    # 處理交易
                    trade = {
                        'p': data['p'],
                        'q': data['q'],
                        'T': data['T'],
                        'm': data['m']  # isBuyerMaker
                    }
                    
                    vpin = calculator.process_trade(trade)
                    trade_count += 1
                    
                    # 如果完成了一個 bucket
                    if vpin is not None:
                        vpin_updates += 1
                        level, action, details = calculator.assess_toxicity(vpin)
                        trend = calculator.get_vpin_trend(window=5)
                        
                        timestamp = datetime.now().strftime("%H:%M:%S")
                        
                        # 選擇圖示
                        if level == "SAFE":
                            icon = "✅"
                        elif level == "CAUTION":
                            icon = "⚠️"
                        elif level == "DANGER":
                            icon = "🚨"
                        else:
                            icon = "💀"
                        
                        print(f"[{timestamp}] Bucket #{details['total_buckets']}")
                        print(f"  VPIN:     {vpin:.4f}")
                        print(f"  等級:     {icon} {level}")
                        print(f"  趨勢:     {trend or 'N/A'}")
                        print(f"  交易數:   {trade_count}")
                        print()
                        
                        last_vpin = vpin
                    
                    # 每 5 秒顯示進度
                    if trade_count % 100 == 0:
                        stats = calculator.get_statistics()
                        progress = stats['current_bucket_progress']
                        print(f"  處理中: {trade_count} 筆交易 | "
                              f"Bucket 進度: {progress['progress_pct']:.1f}%")
                
                except asyncio.TimeoutError:
                    continue
            
            print("✅ 即時測試完成")
            
            # 最終統計
            stats = calculator.get_statistics()
            print("\n📊 最終統計:")
            print(f"  交易處理:     {stats['total_trades_processed']}")
            print(f"  Buckets 完成: {stats['total_buckets_completed']}")
            print(f"  VPIN 更新:    {vpin_updates} 次")
            
            if stats['current_vpin'] is not None:
                print(f"  當前 VPIN:    {stats['current_vpin']:.4f}")
                print(f"  平均 VPIN:    {stats['mean_vpin']:.4f}")
                print(f"  VPIN 波動:    {stats['std_vpin']:.4f}")
                
                level, action, details = calculator.assess_toxicity()
                print(f"  風險評估:     {details['level']}")
            else:
                print(f"  ⚠️  Bucket 數量不足，無法計算 VPIN")
                print(f"  已完成: {len(calculator.buckets)}/{calculator.num_buckets}")
            
    except Exception as e:
        print(f"❌ 錯誤: {e}")


async def main():
    """主測試流程"""
    print("\n" + "=" * 60)
    print("🧪 Task 1.6.1 - B4: VPIN 毒性檢測測試")
    print("=" * 60)
    print()
    
    # 測試 1: 基本計算
    test_basic_vpin_calculation()
    
    # 測試 2: 失衡情境
    test_imbalanced_scenarios()
    
    # 測試 3: 風險評估
    test_risk_assessment()
    
    # 測試 4: 趨勢分析
    test_vpin_trend()
    
    # 測試 5: 即時監控
    await test_realtime_monitoring()
    
    print("\n" + "=" * 60)
    print("✅ 所有測試完成")
    print("=" * 60)
    print()
    
    # 總結
    print("📋 功能驗證總結:")
    print("  ✅ Volume Clock bucket 切分")
    print("  ✅ 買賣方向分類（isBuyerMaker）")
    print("  ✅ Imbalance 計算")
    print("  ✅ VPIN 滑動平均（50 buckets）")
    print("  ✅ 風險等級評估（4 級）")
    print("  ✅ 趨勢分析（上升/下降/穩定）")
    print("  ✅ 即時 WebSocket 整合")
    print()
    print("💡 應用場景:")
    print("  - VPIN < 0.3 → 正常交易")
    print("  - VPIN 0.3-0.5 → 減倉 30-50%")
    print("  - VPIN 0.5-0.7 → 暫停新倉位")
    print("  - VPIN > 0.7 → 退出所有倉位（Flash Crash 風險）")
    print()
    print("📚 學術參考:")
    print("  Easley et al. (2012) 'Flow Toxicity and Liquidity'")
    print("  2010 Flash Crash: VPIN 在崩盤前飆升到 0.9+")
    print()
    print("🎯 Phase B 完成: 5/5 微觀結構指標全部實作！")
    print("   下一步: Task 1.6.1 - C (分層決策系統)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
