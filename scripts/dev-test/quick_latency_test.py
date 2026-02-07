#!/usr/bin/env python3
"""
Task 1.6.1.1: 簡化版延遲測量工具

快速測量:
1. WebSocket 連接延遲
2. 數據接收延遲  
3. 統計分佈

作者: AI Trading System
日期: 2025-11-10
"""

import asyncio
import time
import json
from datetime import datetime
from typing import List
import websockets
import numpy as np

# 配置
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms"
SAMPLES = 50  # 採樣次數


async def measure_latency():
    """測量 WebSocket 延遲"""
    
    print(f"\n{'='*60}")
    print(f"🚀 WebSocket 延遲測量")
    print(f"{'='*60}")
    print(f"測試時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"目標: Binance BTCUSDT Depth20@100ms")
    print(f"採樣次數: {SAMPLES}")
    print(f"{'='*60}\n")
    
    latencies = []
    message_delays = []
    
    try:
        print("📡 連接中...", end="", flush=True)
        
        async with websockets.connect(BINANCE_WS_URL) as ws:
            print(" ✅ 已連接!\n")
            
            print(f"📊 開始測量 (每10次顯示一次): ", end="", flush=True)
            
            for i in range(SAMPLES):
                # 記錄接收時間
                start = time.perf_counter()
                
                # 接收消息
                message = await ws.recv()
                
                # 計算延遲
                end = time.perf_counter()
                latency_ms = (end - start) * 1000
                
                latencies.append(latency_ms)
                
                # 解析消息時間戳（如果有）
                try:
                    data = json.loads(message)
                    if 'E' in data:  # Event time
                        event_time_ms = data['E']
                        local_time_ms = time.time() * 1000
                        message_delay = local_time_ms - event_time_ms
                        message_delays.append(message_delay)
                except:
                    pass
                
                # 進度
                if (i + 1) % 10 == 0:
                    print(f"{i+1}...", end="", flush=True)
            
            print(" ✅ 完成!\n")
    
    except Exception as e:
        print(f"\n❌ 錯誤: {e}\n")
        return None, None
    
    return latencies, message_delays


def analyze_and_print(latencies: List[float], message_delays: List[float]):
    """分析並打印結果"""
    
    if not latencies:
        print("❌ 沒有收集到數據")
        return
    
    # 統計
    stats = {
        'samples': len(latencies),
        'mean': np.mean(latencies),
        'std': np.std(latencies),
        'median': np.median(latencies),
        'p50': np.percentile(latencies, 50),
        'p95': np.percentile(latencies, 95),
        'p99': np.percentile(latencies, 99),
        'min': np.min(latencies),
        'max': np.max(latencies)
    }
    
    # 打印結果
    print(f"{'='*60}")
    print(f"📈 統計結果")
    print(f"{'='*60}")
    print(f"  樣本數:   {stats['samples']}")
    print(f"  平均:     {stats['mean']:.2f} ms")
    print(f"  標準差:   {stats['std']:.2f} ms  (抖動)")
    print(f"  中位數:   {stats['median']:.2f} ms")
    print(f"  P95:      {stats['p95']:.2f} ms")
    print(f"  P99:      {stats['p99']:.2f} ms  ⭐ 關鍵指標")
    print(f"  最小:     {stats['min']:.2f} ms")
    print(f"  最大:     {stats['max']:.2f} ms")
    print(f"{'='*60}\n")
    
    if message_delays:
        delay_stats = {
            'mean': np.mean(message_delays),
            'p99': np.percentile(message_delays, 99)
        }
        print(f"📡 消息延遲 (本地時間 - 事件時間):")
        print(f"  平均:     {delay_stats['mean']:.2f} ms")
        print(f"  P99:      {delay_stats['p99']:.2f} ms")
        print(f"")
    
    # 建議
    print(f"{'='*60}")
    print(f"💡 策略建議")
    print(f"{'='*60}")
    
    p99 = stats['p99']
    
    if p99 < 50:
        print(f"  延遲等級: ⭐⭐⭐ 優秀")
        print(f"  適合策略: HFT (100ms-1s)")
        print(f"  建議: 可嘗試高頻策略，但需測試下單延遲")
        print(f"  優化: 考慮 VPS colocated 接入進一步降低延遲")
    elif p99 < 100:
        print(f"  延遲等級: ⭐⭐ 良好")
        print(f"  適合策略: 中頻 (1-5秒)")
        print(f"  建議: HFT 有風險，建議 1-5秒級別策略")
        print(f"  優化: 可考慮 VPS 優化")
    elif p99 < 200:
        print(f"  延遲等級: ⭐ 中等")
        print(f"  適合策略: 5分鐘 K線 ✅ (推薦)")
        print(f"  建議: 使用 5-15分鐘 K線策略最合適")
        print(f"  優化: 家用網路可接受，無需 VPS")
    else:
        print(f"  延遲等級: ⚠️  較高")
        print(f"  適合策略: 15分鐘+ 長線")
        print(f"  建議: 不適合任何高頻策略")
        print(f"  優化: 建議檢查網路環境或使用 VPS")
    
    print(f"{'='*60}\n")
    
    # 保存報告
    report = {
        'timestamp': datetime.now().isoformat(),
        'statistics': stats,
        'message_delays': {
            'mean': float(np.mean(message_delays)) if message_delays else None,
            'p99': float(np.percentile(message_delays, 99)) if message_delays else None
        } if message_delays else None,
        'raw_latencies': [float(x) for x in latencies[:20]]  # 只保存前20個樣本
    }
    
    # 保存
    import os
    os.makedirs('data/latency', exist_ok=True)
    
    filename = f'data/latency/latency_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(filename, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"💾 報告已保存: {filename}\n")


async def main():
    """主程序"""
    latencies, message_delays = await measure_latency()
    
    if latencies:
        analyze_and_print(latencies, message_delays)
    
    print("✅ 測量完成!\n")


if __name__ == "__main__":
    asyncio.run(main())
