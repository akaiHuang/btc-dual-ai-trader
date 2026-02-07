#!/usr/bin/env python3
"""
dYdX API 速率限制測試器
========================

測試 dYdX Indexer REST API 的速率限制，找出安全的請求頻率。

官方限制:
- Indexer REST: 100 requests / 10 sec per IP
- 理論最大: 10 req/sec

測試項目:
1. 連續請求直到 429
2. 不同間隔的持續請求
3. 混合端點測試
4. 找出安全的請求速率

Usage:
    python scripts/test_dydx_rate_limit.py
"""

import asyncio
import time
import aiohttp
import statistics
from datetime import datetime
from typing import List, Dict, Tuple
from collections import defaultdict

# dYdX Mainnet Indexer
INDEXER_URL = "https://indexer.dydx.trade/v4"

# 測試端點
ENDPOINTS = {
    "markets": "/perpetualMarkets?ticker=BTC-USD",
    "orderbook": "/orderbooks/perpetualMarket/BTC-USD",
    "trades": "/trades/perpetualMarket/BTC-USD?limit=10",
    "candles": "/candles/perpetualMarkets/BTC-USD?resolution=1MIN&limit=10",
}


class RateLimitTester:
    def __init__(self):
        self.results: List[Dict] = []
        self.session: aiohttp.ClientSession = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()
    
    async def make_request(self, endpoint: str) -> Tuple[int, float]:
        """發送請求並返回狀態碼和延遲"""
        url = f"{INDEXER_URL}{endpoint}"
        start = time.time()
        
        try:
            async with self.session.get(url, timeout=10) as resp:
                latency = time.time() - start
                return resp.status, latency
        except Exception as e:
            return -1, time.time() - start
    
    async def test_burst(self, count: int = 20, endpoint: str = "markets") -> Dict:
        """
        測試 1: 連續快速請求 (Burst Test)
        找出多少個連續請求後會觸發 429
        """
        print(f"\n{'='*60}")
        print(f"🧪 測試 1: Burst 測試 ({count} 個連續請求)")
        print(f"{'='*60}")
        
        results = []
        first_429_at = None
        
        for i in range(count):
            status, latency = await self.make_request(ENDPOINTS[endpoint])
            results.append({
                "index": i + 1,
                "status": status,
                "latency": latency
            })
            
            status_icon = "✅" if status == 200 else "❌" if status == 429 else "⚠️"
            print(f"  {i+1:3d}. {status_icon} Status: {status}, Latency: {latency*1000:.0f}ms")
            
            if status == 429 and first_429_at is None:
                first_429_at = i + 1
        
        success_count = sum(1 for r in results if r["status"] == 200)
        error_count = sum(1 for r in results if r["status"] == 429)
        
        print(f"\n📊 Burst 測試結果:")
        print(f"   成功: {success_count}/{count}")
        print(f"   429 錯誤: {error_count}")
        if first_429_at:
            print(f"   首次 429 發生在第 {first_429_at} 個請求")
        
        return {
            "test": "burst",
            "total": count,
            "success": success_count,
            "errors": error_count,
            "first_429_at": first_429_at
        }
    
    async def test_interval(self, interval_ms: int, duration_sec: int = 10, endpoint: str = "markets") -> Dict:
        """
        測試 2: 固定間隔請求
        測試特定間隔是否會觸發 429
        """
        print(f"\n{'='*60}")
        print(f"🧪 測試: 間隔 {interval_ms}ms ({duration_sec}秒)")
        print(f"{'='*60}")
        
        results = []
        start_time = time.time()
        request_count = 0
        
        while time.time() - start_time < duration_sec:
            status, latency = await self.make_request(ENDPOINTS[endpoint])
            request_count += 1
            results.append({
                "time": time.time() - start_time,
                "status": status,
                "latency": latency
            })
            
            status_icon = "✅" if status == 200 else "❌" if status == 429 else "⚠️"
            elapsed = time.time() - start_time
            print(f"  [{elapsed:5.1f}s] {status_icon} Status: {status}, Latency: {latency*1000:.0f}ms")
            
            # 等待指定間隔
            await asyncio.sleep(interval_ms / 1000)
        
        success_count = sum(1 for r in results if r["status"] == 200)
        error_count = sum(1 for r in results if r["status"] == 429)
        avg_latency = statistics.mean(r["latency"] for r in results) * 1000
        
        print(f"\n📊 間隔 {interval_ms}ms 測試結果:")
        print(f"   總請求: {request_count}")
        print(f"   成功: {success_count} ({success_count/request_count*100:.1f}%)")
        print(f"   429 錯誤: {error_count}")
        print(f"   平均延遲: {avg_latency:.0f}ms")
        print(f"   實際速率: {request_count/duration_sec:.2f} req/sec")
        
        return {
            "test": f"interval_{interval_ms}ms",
            "interval_ms": interval_ms,
            "duration_sec": duration_sec,
            "total": request_count,
            "success": success_count,
            "errors": error_count,
            "success_rate": success_count/request_count*100,
            "actual_rate": request_count/duration_sec,
            "avg_latency_ms": avg_latency
        }
    
    async def test_mixed_endpoints(self, interval_ms: int, duration_sec: int = 10) -> Dict:
        """
        測試 3: 混合端點請求
        模擬真實使用場景 (多個端點交替請求)
        """
        print(f"\n{'='*60}")
        print(f"🧪 測試: 混合端點 (間隔 {interval_ms}ms, {duration_sec}秒)")
        print(f"{'='*60}")
        
        endpoint_names = list(ENDPOINTS.keys())
        results = defaultdict(list)
        start_time = time.time()
        request_count = 0
        
        while time.time() - start_time < duration_sec:
            endpoint = endpoint_names[request_count % len(endpoint_names)]
            status, latency = await self.make_request(ENDPOINTS[endpoint])
            request_count += 1
            results[endpoint].append({
                "status": status,
                "latency": latency
            })
            
            status_icon = "✅" if status == 200 else "❌" if status == 429 else "⚠️"
            elapsed = time.time() - start_time
            print(f"  [{elapsed:5.1f}s] {status_icon} {endpoint}: {status}, {latency*1000:.0f}ms")
            
            await asyncio.sleep(interval_ms / 1000)
        
        total_success = sum(sum(1 for r in v if r["status"] == 200) for v in results.values())
        total_errors = sum(sum(1 for r in v if r["status"] == 429) for v in results.values())
        
        print(f"\n📊 混合端點測試結果:")
        print(f"   總請求: {request_count}")
        print(f"   成功: {total_success} ({total_success/request_count*100:.1f}%)")
        print(f"   429 錯誤: {total_errors}")
        
        for ep, ep_results in results.items():
            ep_success = sum(1 for r in ep_results if r["status"] == 200)
            print(f"   - {ep}: {ep_success}/{len(ep_results)}")
        
        return {
            "test": "mixed_endpoints",
            "interval_ms": interval_ms,
            "total": request_count,
            "success": total_success,
            "errors": total_errors
        }
    
    async def find_safe_rate(self) -> Dict:
        """
        測試 4: 二分搜尋找出安全速率
        """
        print(f"\n{'='*60}")
        print(f"🔍 尋找安全速率...")
        print(f"{'='*60}")
        
        # 測試不同間隔
        test_intervals = [50, 100, 150, 200, 250, 300, 400, 500]
        safe_intervals = []
        
        for interval in test_intervals:
            print(f"\n--- 測試 {interval}ms 間隔 ---")
            
            # 短時間測試
            results = []
            error_found = False
            
            for _ in range(20):  # 20 個請求
                status, _ = await self.make_request(ENDPOINTS["markets"])
                results.append(status)
                if status == 429:
                    error_found = True
                    break
                await asyncio.sleep(interval / 1000)
            
            success_rate = sum(1 for s in results if s == 200) / len(results) * 100
            
            if not error_found:
                safe_intervals.append(interval)
                print(f"  ✅ {interval}ms: 安全 (成功率 {success_rate:.0f}%)")
            else:
                print(f"  ❌ {interval}ms: 不安全 (成功率 {success_rate:.0f}%)")
            
            # 等待冷卻
            await asyncio.sleep(2)
        
        min_safe = min(safe_intervals) if safe_intervals else None
        
        print(f"\n📊 安全速率搜尋結果:")
        print(f"   安全間隔: {safe_intervals}")
        if min_safe:
            print(f"   最小安全間隔: {min_safe}ms")
            print(f"   建議速率: {1000/min_safe:.2f} req/sec")
            print(f"   建議 10 秒內最大請求: {int(10 * 1000/min_safe)}")
        
        return {
            "safe_intervals": safe_intervals,
            "min_safe_interval_ms": min_safe,
            "recommended_rate": 1000/min_safe if min_safe else None
        }


async def main():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║           dYdX API 速率限制測試器 v1.0                           ║
║                                                                  ║
║  官方限制: 100 requests / 10 sec (Indexer REST)                 ║
║  理論最大: 10 req/sec                                            ║
╚══════════════════════════════════════════════════════════════════╝
    """)
    
    print("⏳ 開始前等待 5 秒 (確保之前的限制已重置)...")
    await asyncio.sleep(5)
    
    async with RateLimitTester() as tester:
        all_results = []
        
        # 測試 1: Burst 測試
        print("\n" + "="*70)
        print("📋 階段 1: Burst 測試 (連續快速請求)")
        print("="*70)
        result = await tester.test_burst(count=15)
        all_results.append(result)
        
        print("\n⏳ 冷卻 15 秒...")
        await asyncio.sleep(15)
        
        # 測試 2: 不同間隔測試
        print("\n" + "="*70)
        print("📋 階段 2: 固定間隔測試")
        print("="*70)
        
        intervals_to_test = [100, 200, 300, 500]
        
        for interval in intervals_to_test:
            result = await tester.test_interval(interval_ms=interval, duration_sec=10)
            all_results.append(result)
            
            print("\n⏳ 冷卻 10 秒...")
            await asyncio.sleep(10)
        
        # 測試 3: 混合端點
        print("\n" + "="*70)
        print("📋 階段 3: 混合端點測試")
        print("="*70)
        result = await tester.test_mixed_endpoints(interval_ms=300, duration_sec=10)
        all_results.append(result)
        
        print("\n⏳ 冷卻 10 秒...")
        await asyncio.sleep(10)
        
        # 測試 4: 找出安全速率
        print("\n" + "="*70)
        print("📋 階段 4: 尋找安全速率")
        print("="*70)
        safe_result = await tester.find_safe_rate()
        
        # 總結
        print("\n" + "="*70)
        print("📊 最終報告")
        print("="*70)
        
        print("\n1️⃣ Burst 測試:")
        burst = next((r for r in all_results if r["test"] == "burst"), None)
        if burst:
            print(f"   連續請求 {burst['total']} 個")
            print(f"   首次 429 出現在第 {burst['first_429_at']} 個請求" if burst['first_429_at'] else "   未觸發 429")
        
        print("\n2️⃣ 間隔測試結果:")
        for result in all_results:
            if result["test"].startswith("interval_"):
                status = "✅ 安全" if result["errors"] == 0 else f"❌ {result['errors']} 個 429"
                print(f"   {result['interval_ms']}ms: {status} (實際 {result['actual_rate']:.2f} req/sec)")
        
        print("\n3️⃣ 推薦設定:")
        if safe_result["min_safe_interval_ms"]:
            min_interval = safe_result["min_safe_interval_ms"]
            # 加 50% 安全邊際
            safe_interval = int(min_interval * 1.5)
            safe_rate = 1000 / safe_interval
            
            print(f"   ✅ 最小安全間隔: {min_interval}ms")
            print(f"   ✅ 建議間隔 (含安全邊際): {safe_interval}ms")
            print(f"   ✅ 建議速率: {safe_rate:.2f} req/sec")
            print(f"   ✅ 10 秒內最大請求: {int(safe_rate * 10)}")
            
            print(f"\n4️⃣ 程式碼建議:")
            print(f"""
    # dYdX API 速率控制設定
    _api_call_interval = {safe_interval / 1000:.2f}  # 每次呼叫間隔 {safe_interval}ms
    _max_calls_per_10s = {int(safe_rate * 10 * 0.8)}  # 10 秒內最大呼叫 (保守)
    
    # 或使用緩存 TTL
    CACHE_TTL = {{
        'price': 3.0,      # 價格緩存 3 秒
        'positions': 5.0,  # 持倉緩存 5 秒  
        'balance': 5.0,    # 餘額緩存 5 秒
        'orderbook': 2.0,  # 訂單簿緩存 2 秒
    }}
            """)
        else:
            print("   ⚠️ 無法確定安全速率，建議使用保守設定:")
            print("   - 間隔: 500ms")
            print("   - 速率: 2 req/sec")


if __name__ == "__main__":
    asyncio.run(main())
