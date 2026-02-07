#!/usr/bin/env python3
"""
dYdX API 速率限制測試器 v2.0
============================

修正版本：
1. ✅ 429 時立即停火 + 讀取 Retry-After + Exponential Backoff
2. ✅ 更長的冷卻時間 (30-60 秒)
3. ✅ 讀取並顯示所有 Rate Limit Headers
4. ✅ 慢啟動機制

官方限制:
- Indexer REST: 100 requests / 10 sec per IP
- 理論最大: 10 req/sec

Usage:
    python scripts/test_dydx_rate_limit_v2.py
"""

import asyncio
import time
import aiohttp
import random
from datetime import datetime
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field

# dYdX Mainnet Indexer
INDEXER_URL = "https://indexer.dydx.trade/v4"

# 測試端點
ENDPOINTS = {
    "markets": "/perpetualMarkets?ticker=BTC-USD",
    "orderbook": "/orderbooks/perpetualMarket/BTC-USD",
    "trades": "/trades/perpetualMarket/BTC-USD?limit=10",
    "candles": "/candles/perpetualMarkets/BTC-USD?resolution=1MIN&limit=10",
}

# 顏色輸出
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


@dataclass
class RequestResult:
    """請求結果"""
    status: int
    latency: float
    retry_after: Optional[str] = None
    headers: Dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    
    @property
    def is_success(self) -> bool:
        return self.status == 200
    
    @property
    def is_rate_limited(self) -> bool:
        return self.status == 429
    
    def get_rate_limit_info(self) -> Dict:
        """提取 Rate Limit 相關 headers"""
        info = {}
        for key, value in self.headers.items():
            key_lower = key.lower()
            if 'rate' in key_lower or 'limit' in key_lower or 'retry' in key_lower:
                info[key] = value
        return info


class RateLimitTesterV2:
    def __init__(self):
        self.session: aiohttp.ClientSession = None
        self.request_log: List[RequestResult] = []
        self.total_requests = 0
        self.total_429s = 0
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()
    
    async def make_request(self, endpoint: str) -> RequestResult:
        """發送請求並返回完整結果（包含 headers）"""
        url = f"{INDEXER_URL}{endpoint}"
        start = time.time()
        
        try:
            async with self.session.get(url, timeout=10) as resp:
                await resp.read()  # 確保連線乾淨釋放
                latency = time.time() - start
                
                result = RequestResult(
                    status=resp.status,
                    latency=latency,
                    retry_after=resp.headers.get("Retry-After"),
                    headers=dict(resp.headers)
                )
                
                self.request_log.append(result)
                self.total_requests += 1
                if result.is_rate_limited:
                    self.total_429s += 1
                
                return result
                
        except asyncio.TimeoutError:
            return RequestResult(status=-1, latency=time.time() - start)
        except Exception as e:
            print(f"  {Colors.RED}⚠️ 請求錯誤: {e}{Colors.RESET}")
            return RequestResult(status=-1, latency=time.time() - start)
    
    async def wait_with_backoff(self, result: RequestResult, base_wait: float = 20.0) -> float:
        """
        429 後的退避等待
        
        1. 優先使用 Retry-After header
        2. 否則使用 exponential backoff + jitter
        """
        if result.retry_after:
            try:
                wait_time = float(result.retry_after)
                print(f"  {Colors.YELLOW}⏳ 服務器要求等待 {wait_time}s (Retry-After){Colors.RESET}")
            except ValueError:
                wait_time = base_wait
                print(f"  {Colors.YELLOW}⏳ Retry-After 無法解析: {result.retry_after}, 使用預設 {wait_time}s{Colors.RESET}")
        else:
            # Exponential backoff with jitter
            jitter = random.uniform(0.5, 1.5)
            wait_time = base_wait * jitter
            print(f"  {Colors.YELLOW}⏳ 無 Retry-After，使用退避等待 {wait_time:.1f}s{Colors.RESET}")
        
        # 顯示 Rate Limit Headers
        rate_info = result.get_rate_limit_info()
        if rate_info:
            print(f"  {Colors.CYAN}📋 Rate Limit Headers:{Colors.RESET}")
            for k, v in rate_info.items():
                print(f"     {k}: {v}")
        else:
            print(f"  {Colors.CYAN}📋 未發現 Rate Limit Headers{Colors.RESET}")
        
        # 顯示所有 headers (用於調試)
        print(f"  {Colors.CYAN}📋 所有 Response Headers:{Colors.RESET}")
        for k, v in result.headers.items():
            print(f"     {k}: {v}")
        
        await asyncio.sleep(wait_time)
        return wait_time
    
    async def test_single_request(self) -> RequestResult:
        """測試單一請求（檢查 IP 是否被限速）"""
        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}🧪 測試 0: 單一請求檢查{Colors.RESET}")
        print(f"{'='*60}")
        
        result = await self.make_request(ENDPOINTS["markets"])
        
        status_icon = "✅" if result.is_success else "❌" if result.is_rate_limited else "⚠️"
        color = Colors.GREEN if result.is_success else Colors.RED
        
        print(f"  {color}{status_icon} Status: {result.status}, Latency: {result.latency*1000:.0f}ms{Colors.RESET}")
        
        if result.is_rate_limited:
            print(f"\n  {Colors.RED}⚠️ IP 目前被限速中！{Colors.RESET}")
            await self.wait_with_backoff(result, base_wait=60.0)
        
        return result
    
    async def test_burst_with_stop(self, max_count: int = 50) -> Dict:
        """
        測試連續請求直到 429，然後立即停火
        """
        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}🧪 測試 1: Burst 測試（429 立即停火）{Colors.RESET}")
        print(f"{'='*60}")
        
        results = []
        first_429_at = None
        
        for i in range(max_count):
            result = await self.make_request(ENDPOINTS["markets"])
            results.append(result)
            
            status_icon = "✅" if result.is_success else "❌" if result.is_rate_limited else "⚠️"
            color = Colors.GREEN if result.is_success else Colors.RED
            print(f"  {i+1:3d}. {color}{status_icon} Status: {result.status}, Latency: {result.latency*1000:.0f}ms{Colors.RESET}")
            
            if result.is_rate_limited:
                first_429_at = i + 1
                print(f"\n  {Colors.RED}🛑 429 偵測！立即停火！{Colors.RESET}")
                await self.wait_with_backoff(result, base_wait=30.0)
                break
        
        success_count = sum(1 for r in results if r.is_success)
        
        print(f"\n{Colors.BOLD}📊 Burst 測試結果:{Colors.RESET}")
        print(f"   成功請求: {success_count}")
        if first_429_at:
            print(f"   {Colors.RED}首次 429 在第 {first_429_at} 個請求{Colors.RESET}")
            print(f"   {Colors.YELLOW}=> 連續 burst 上限約 {first_429_at - 1} 個請求{Colors.RESET}")
        else:
            print(f"   {Colors.GREEN}未觸發 429 (全部成功){Colors.RESET}")
        
        return {
            "test": "burst",
            "total": len(results),
            "success": success_count,
            "first_429_at": first_429_at,
            "max_burst": first_429_at - 1 if first_429_at else max_count
        }
    
    async def test_sustained_rate(self, target_rate: float, duration_sec: int = 15) -> Dict:
        """
        測試持續速率（遇到 429 立即停火 + 慢啟動恢復）
        
        Args:
            target_rate: 目標速率 (requests per second)
            duration_sec: 測試時長
        """
        interval = 1.0 / target_rate
        
        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}🧪 測試: 持續速率 {target_rate:.1f} req/sec (間隔 {interval*1000:.0f}ms){Colors.RESET}")
        print(f"{'='*60}")
        
        results = []
        start_time = time.time()
        current_interval = interval
        
        while time.time() - start_time < duration_sec:
            result = await self.make_request(ENDPOINTS["markets"])
            results.append(result)
            
            elapsed = time.time() - start_time
            status_icon = "✅" if result.is_success else "❌" if result.is_rate_limited else "⚠️"
            color = Colors.GREEN if result.is_success else Colors.RED
            
            print(f"  [{elapsed:5.1f}s] {color}{status_icon} Status: {result.status}, Latency: {result.latency*1000:.0f}ms{Colors.RESET}")
            
            if result.is_rate_limited:
                print(f"\n  {Colors.RED}🛑 429 偵測！停火 + 慢啟動{Colors.RESET}")
                await self.wait_with_backoff(result, base_wait=20.0)
                
                # 慢啟動：速率減半
                current_interval = min(current_interval * 2, 5.0)  # 最慢 5 秒一次
                print(f"  {Colors.YELLOW}🐢 慢啟動: 間隔調整為 {current_interval*1000:.0f}ms{Colors.RESET}")
            else:
                # 成功後逐漸恢復速率
                if current_interval > interval:
                    current_interval = max(current_interval * 0.9, interval)
            
            await asyncio.sleep(current_interval)
        
        success_count = sum(1 for r in results if r.is_success)
        error_count = sum(1 for r in results if r.is_rate_limited)
        actual_duration = time.time() - start_time
        
        print(f"\n{Colors.BOLD}📊 持續速率測試結果:{Colors.RESET}")
        print(f"   目標速率: {target_rate:.1f} req/sec")
        print(f"   總請求: {len(results)}")
        print(f"   成功: {success_count} ({success_count/len(results)*100:.1f}%)")
        print(f"   429 錯誤: {error_count}")
        print(f"   實際速率: {len(results)/actual_duration:.2f} req/sec")
        
        return {
            "test": f"sustained_{target_rate}",
            "target_rate": target_rate,
            "total": len(results),
            "success": success_count,
            "errors": error_count,
            "success_rate": success_count/len(results)*100,
            "actual_rate": len(results)/actual_duration
        }
    
    async def find_safe_rate_progressive(self) -> Dict:
        """
        漸進式尋找安全速率
        從低速率開始，逐步增加直到觸發 429
        """
        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}🔍 漸進式尋找安全速率{Colors.RESET}")
        print(f"{'='*60}")
        
        # 從低到高測試
        test_rates = [0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        safe_rate = 0
        
        for rate in test_rates:
            print(f"\n{Colors.BLUE}--- 測試 {rate:.1f} req/sec ---{Colors.RESET}")
            
            interval = 1.0 / rate
            results = []
            hit_429 = False
            
            # 每個速率測試 10 個請求
            for i in range(10):
                result = await self.make_request(ENDPOINTS["markets"])
                results.append(result)
                
                status_icon = "✅" if result.is_success else "❌"
                color = Colors.GREEN if result.is_success else Colors.RED
                print(f"  {i+1:2d}. {color}{status_icon}{Colors.RESET}", end=" ")
                
                if result.is_rate_limited:
                    hit_429 = True
                    print(f"\n  {Colors.RED}🛑 429! 停止測試此速率{Colors.RESET}")
                    await self.wait_with_backoff(result, base_wait=30.0)
                    break
                
                await asyncio.sleep(interval)
            
            print()  # 換行
            
            success_count = sum(1 for r in results if r.is_success)
            
            if not hit_429:
                safe_rate = rate
                print(f"  {Colors.GREEN}✅ {rate:.1f} req/sec 安全 ({success_count}/10 成功){Colors.RESET}")
            else:
                print(f"  {Colors.RED}❌ {rate:.1f} req/sec 不安全 ({success_count}/{len(results)} 成功){Colors.RESET}")
                break
            
            # 冷卻
            print(f"  {Colors.CYAN}⏳ 冷卻 15 秒...{Colors.RESET}")
            await asyncio.sleep(15)
        
        print(f"\n{Colors.BOLD}📊 安全速率搜尋結果:{Colors.RESET}")
        print(f"   {Colors.GREEN}最高安全速率: {safe_rate:.1f} req/sec{Colors.RESET}")
        print(f"   {Colors.GREEN}建議間隔: {1000/safe_rate:.0f}ms{Colors.RESET}" if safe_rate > 0 else "")
        print(f"   {Colors.YELLOW}建議保守設定: {safe_rate * 0.7:.1f} req/sec (70%){Colors.RESET}" if safe_rate > 0 else "")
        
        return {
            "max_safe_rate": safe_rate,
            "recommended_interval_ms": 1000/safe_rate if safe_rate > 0 else None,
            "conservative_rate": safe_rate * 0.7 if safe_rate > 0 else None
        }


async def main():
    print(f"""
{Colors.BOLD}╔══════════════════════════════════════════════════════════════════╗
║         dYdX API 速率限制測試器 v2.0 (修正版)                    ║
║                                                                  ║
║  修正:                                                           ║
║  ✅ 429 時立即停火 + Exponential Backoff                        ║
║  ✅ 讀取 Retry-After 和 Rate Limit Headers                      ║
║  ✅ 更長的冷卻時間 (30-60 秒)                                    ║
║  ✅ 慢啟動恢復機制                                               ║
║                                                                  ║
║  官方限制: 100 requests / 10 sec (Indexer REST)                 ║
╚══════════════════════════════════════════════════════════════════╝
{Colors.RESET}""")
    
    print(f"{Colors.YELLOW}⏳ 開始前等待 30 秒（確保之前的限制完全重置）...{Colors.RESET}")
    await asyncio.sleep(30)
    
    async with RateLimitTesterV2() as tester:
        # 測試 0: 檢查 IP 狀態
        print(f"\n{Colors.BOLD}{'='*70}")
        print("📋 階段 0: 檢查 IP 是否被限速")
        print(f"{'='*70}{Colors.RESET}")
        
        result = await tester.test_single_request()
        
        if result.is_rate_limited:
            print(f"\n{Colors.RED}⚠️ IP 仍被限速，等待 60 秒...{Colors.RESET}")
            await asyncio.sleep(60)
        
        # 測試 1: Burst 測試
        print(f"\n{Colors.BOLD}{'='*70}")
        print("📋 階段 1: Burst 測試（找出連續請求上限）")
        print(f"{'='*70}{Colors.RESET}")
        
        burst_result = await tester.test_burst_with_stop(max_count=30)
        
        print(f"\n{Colors.YELLOW}⏳ 冷卻 45 秒...{Colors.RESET}")
        await asyncio.sleep(45)
        
        # 測試 2: 漸進式尋找安全速率
        print(f"\n{Colors.BOLD}{'='*70}")
        print("📋 階段 2: 漸進式尋找安全速率")
        print(f"{'='*70}{Colors.RESET}")
        
        safe_result = await tester.find_safe_rate_progressive()
        
        # 最終報告
        print(f"\n{Colors.BOLD}{'='*70}")
        print("📊 最終報告")
        print(f"{'='*70}{Colors.RESET}")
        
        print(f"\n{Colors.BOLD}1️⃣ Burst 測試:{Colors.RESET}")
        print(f"   連續 burst 上限: {burst_result['max_burst']} 個請求")
        
        print(f"\n{Colors.BOLD}2️⃣ 安全速率:{Colors.RESET}")
        if safe_result['max_safe_rate'] > 0:
            print(f"   最高安全速率: {safe_result['max_safe_rate']:.1f} req/sec")
            print(f"   建議間隔: {safe_result['recommended_interval_ms']:.0f}ms")
            print(f"   保守設定: {safe_result['conservative_rate']:.1f} req/sec")
        else:
            print(f"   {Colors.RED}無法確定安全速率{Colors.RESET}")
        
        print(f"\n{Colors.BOLD}3️⃣ 統計:{Colors.RESET}")
        print(f"   總請求數: {tester.total_requests}")
        print(f"   總 429 數: {tester.total_429s}")
        
        print(f"\n{Colors.BOLD}4️⃣ 程式碼建議:{Colors.RESET}")
        if safe_result['max_safe_rate'] > 0:
            safe_rate = safe_result['conservative_rate']
            interval = 1000 / safe_rate
            print(f"""
    # dYdX API 速率控制設定 (基於測試結果)
    _api_call_interval = {interval/1000:.2f}  # 每次呼叫間隔 {interval:.0f}ms
    _max_calls_per_10s = {int(safe_rate * 10)}  # 10 秒內最大呼叫
    
    # 緩存 TTL 建議
    CACHE_TTL = {{
        'price': 3.0,      # 價格緩存 3 秒
        'positions': 5.0,  # 持倉緩存 5 秒  
        'balance': 5.0,    # 餘額緩存 5 秒
        'orderbook': 2.0,  # 訂單簿緩存 2 秒
    }}
    
    # 429 退避設定
    BACKOFF_CONFIG = {{
        'base_wait': 20.0,    # 基礎等待秒數
        'max_wait': 60.0,     # 最大等待秒數
        'multiplier': 2.0,    # 每次失敗翻倍
    }}
            """)
        else:
            print(f"""
    # ⚠️ 無法測得安全速率，使用超保守設定
    _api_call_interval = 2.0   # 每 2 秒一次
    _max_calls_per_10s = 5     # 10 秒最多 5 次
            """)
        
        print(f"\n{Colors.BOLD}5️⃣ 架構建議:{Colors.RESET}")
        print("""
    🔹 市場數據應使用 WebSocket (v4-orderbook, v4-trades, v4-markets)
    🔹 REST 只用於:
       - 啟動時初始化 snapshot
       - 每 30-60 秒校正一次
       - WebSocket 斷線時補資料
    🔹 下單/查詢帳戶才需要 REST，且應有緩存
        """)


if __name__ == "__main__":
    asyncio.run(main())
