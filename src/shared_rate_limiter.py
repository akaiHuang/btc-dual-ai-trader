"""
共享速率限制器 (Shared Rate Limiter)
=====================================

跨進程的 dYdX API 速率限制協調器。
允許多個 bot 同時運行而不會超過 API 限制。

原理:
- 使用文件鎖 (fcntl) 確保原子操作
- 共享狀態文件記錄所有進程的請求時間戳
- 每個進程在發送請求前檢查全局配額

用法:
    from src.shared_rate_limiter import SharedRateLimiter
    
    limiter = SharedRateLimiter()
    
    # 在發送 API 請求前調用
    await limiter.acquire()
    response = await session.get(url)
"""

import asyncio
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SharedRateLimiter:
    """
    跨進程共享的速率限制器
    
    dYdX 限制: 100 requests / 10 sec per IP
    安全設定: 80 requests / 10 sec (留 20% 緩衝)
    """
    
    # 共享狀態文件路徑
    STATE_FILE = Path("/tmp/dydx_rate_limit_state.json")
    LOCK_FILE = Path("/tmp/dydx_rate_limit.lock")
    
    # 速率限制設定
    MAX_REQUESTS_PER_WINDOW = 80  # 保守設定 (官方 100)
    WINDOW_SIZE_SECONDS = 10.0
    
    # 單進程最小間隔 (避免 burst)
    MIN_INTERVAL_MS = 150  # 150ms = ~6.6 req/sec per process
    
    def __init__(
        self,
        max_requests: int = None,
        window_seconds: float = None,
        process_id: str = None
    ):
        """
        初始化共享速率限制器
        
        Args:
            max_requests: 自訂最大請求數 (預設 80)
            window_seconds: 時間窗口秒數 (預設 10)
            process_id: 進程識別碼 (預設使用 PID)
        """
        self.max_requests = max_requests or self.MAX_REQUESTS_PER_WINDOW
        self.window_seconds = window_seconds or self.WINDOW_SIZE_SECONDS
        self.process_id = process_id or f"pid_{os.getpid()}"
        
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()
        
        # 確保狀態文件存在
        self._init_state_file()
        
        logger.info(
            f"🔒 SharedRateLimiter 初始化: {self.process_id} "
            f"(限制: {self.max_requests}/{self.window_seconds}s)"
        )
    
    def _init_state_file(self):
        """初始化共享狀態文件"""
        if not self.STATE_FILE.exists():
            self._write_state({"requests": [], "processes": {}})
    
    def _read_state(self) -> dict:
        """讀取共享狀態 (帶文件鎖)"""
        try:
            with open(self.LOCK_FILE, 'w') as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
                try:
                    if self.STATE_FILE.exists():
                        with open(self.STATE_FILE, 'r') as f:
                            return json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    pass
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.warning(f"讀取狀態文件失敗: {e}")
        
        return {"requests": [], "processes": {}}
    
    def _write_state(self, state: dict):
        """寫入共享狀態 (帶文件鎖)"""
        try:
            with open(self.LOCK_FILE, 'w') as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    with open(self.STATE_FILE, 'w') as f:
                        json.dump(state, f)
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.warning(f"寫入狀態文件失敗: {e}")
    
    def _update_state_atomic(self, new_request_time: float) -> tuple[bool, float]:
        """
        原子更新狀態並檢查是否允許請求
        
        Returns:
            (allowed, wait_time): 是否允許請求，需要等待的時間
        """
        try:
            with open(self.LOCK_FILE, 'w') as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    # 讀取當前狀態
                    state = {"requests": [], "processes": {}}
                    if self.STATE_FILE.exists():
                        try:
                            with open(self.STATE_FILE, 'r') as f:
                                state = json.load(f)
                        except:
                            pass
                    
                    now = time.time()
                    window_start = now - self.window_seconds
                    
                    # 清理過期的請求記錄
                    state["requests"] = [
                        ts for ts in state["requests"] 
                        if ts > window_start
                    ]
                    
                    # 清理不活躍的進程
                    state["processes"] = {
                        pid: last_time 
                        for pid, last_time in state.get("processes", {}).items()
                        if now - last_time < 60  # 60 秒沒活動就清除
                    }
                    
                    # 計算當前請求數
                    current_count = len(state["requests"])
                    active_processes = len(state["processes"]) + 1  # +1 包含自己
                    
                    # 根據活躍進程數動態調整配額
                    per_process_quota = self.max_requests / max(active_processes, 1)
                    my_requests = sum(
                        1 for ts in state["requests"]
                        if state.get("request_owners", {}).get(str(ts)) == self.process_id
                    )
                    
                    # 檢查是否超過全局限制
                    if current_count >= self.max_requests:
                        # 計算需要等待的時間
                        oldest = min(state["requests"]) if state["requests"] else now
                        wait_time = max(0.1, oldest + self.window_seconds - now + 0.1)
                        return False, wait_time
                    
                    # 記錄新請求
                    state["requests"].append(new_request_time)
                    state["processes"][self.process_id] = now
                    
                    # 記錄請求所有者 (用於配額計算)
                    if "request_owners" not in state:
                        state["request_owners"] = {}
                    state["request_owners"][str(new_request_time)] = self.process_id
                    
                    # 清理舊的所有者記錄
                    state["request_owners"] = {
                        ts: owner 
                        for ts, owner in state["request_owners"].items()
                        if float(ts) > window_start
                    }
                    
                    # 寫回狀態
                    with open(self.STATE_FILE, 'w') as f:
                        json.dump(state, f)
                    
                    return True, 0.0
                    
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    
        except Exception as e:
            logger.warning(f"更新狀態失敗: {e}")
            return True, 0.0  # 發生錯誤時允許請求
    
    async def acquire(self, timeout: float = 30.0) -> bool:
        """
        獲取 API 請求許可
        
        Args:
            timeout: 最大等待時間 (秒)
            
        Returns:
            是否成功獲取許可
        """
        async with self._lock:
            start_time = time.time()
            
            while True:
                # 檢查超時
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    logger.warning(f"⏰ 速率限制等待超時 ({timeout}s)")
                    return False
                
                # 確保單進程最小間隔
                time_since_last = (time.time() - self._last_request_time) * 1000
                if time_since_last < self.MIN_INTERVAL_MS:
                    await asyncio.sleep((self.MIN_INTERVAL_MS - time_since_last) / 1000)
                
                # 嘗試獲取許可
                now = time.time()
                allowed, wait_time = self._update_state_atomic(now)
                
                if allowed:
                    self._last_request_time = now
                    return True
                
                # 需要等待
                logger.debug(f"⏳ 速率限制: 等待 {wait_time:.2f}s")
                await asyncio.sleep(min(wait_time, timeout - elapsed))
    
    def get_stats(self) -> dict:
        """獲取當前速率限制統計"""
        state = self._read_state()
        now = time.time()
        window_start = now - self.window_seconds
        
        recent_requests = [ts for ts in state.get("requests", []) if ts > window_start]
        active_processes = {
            pid: last_time 
            for pid, last_time in state.get("processes", {}).items()
            if now - last_time < 60
        }
        
        return {
            "current_requests": len(recent_requests),
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "active_processes": len(active_processes),
            "process_ids": list(active_processes.keys()),
            "usage_percent": len(recent_requests) / self.max_requests * 100,
            "remaining_quota": self.max_requests - len(recent_requests),
        }
    
    def cleanup(self):
        """清理進程記錄 (程式結束時調用)"""
        try:
            with open(self.LOCK_FILE, 'w') as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    if self.STATE_FILE.exists():
                        with open(self.STATE_FILE, 'r') as f:
                            state = json.load(f)
                        
                        # 移除自己的進程記錄
                        if self.process_id in state.get("processes", {}):
                            del state["processes"][self.process_id]
                        
                        with open(self.STATE_FILE, 'w') as f:
                            json.dump(state, f)
                            
                        logger.info(f"🧹 已清理進程記錄: {self.process_id}")
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.warning(f"清理失敗: {e}")


class RateLimitedSession:
    """
    包裝 aiohttp.ClientSession，自動應用速率限制
    
    用法:
        async with RateLimitedSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
    """
    
    def __init__(self, limiter: SharedRateLimiter = None):
        self.limiter = limiter or SharedRateLimiter()
        self._session = None
    
    async def __aenter__(self):
        import aiohttp
        self._session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, *args):
        if self._session:
            await self._session.close()
        self.limiter.cleanup()
    
    async def get(self, url: str, **kwargs):
        """發送 GET 請求 (自動速率限制)"""
        await self.limiter.acquire()
        return await self._session.get(url, **kwargs)
    
    async def post(self, url: str, **kwargs):
        """發送 POST 請求 (自動速率限制)"""
        await self.limiter.acquire()
        return await self._session.post(url, **kwargs)


# 全局實例 (可選)
_global_limiter: Optional[SharedRateLimiter] = None


def get_shared_limiter() -> SharedRateLimiter:
    """獲取全局共享速率限制器"""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = SharedRateLimiter()
    return _global_limiter


# 快捷函數
async def acquire_rate_limit(timeout: float = 30.0) -> bool:
    """
    快捷函數: 獲取 API 請求許可
    
    用法:
        from src.shared_rate_limiter import acquire_rate_limit
        
        if await acquire_rate_limit():
            response = await session.get(url)
    """
    return await get_shared_limiter().acquire(timeout)


def get_rate_limit_stats() -> dict:
    """快捷函數: 獲取速率限制統計"""
    return get_shared_limiter().get_stats()


if __name__ == "__main__":
    # 測試
    import asyncio
    
    async def test():
        limiter = SharedRateLimiter()
        
        print("📊 初始狀態:")
        print(json.dumps(limiter.get_stats(), indent=2))
        
        print("\n🧪 測試 10 次 acquire:")
        for i in range(10):
            start = time.time()
            allowed = await limiter.acquire()
            elapsed = (time.time() - start) * 1000
            print(f"  {i+1}. allowed={allowed}, wait={elapsed:.0f}ms")
        
        print("\n📊 最終狀態:")
        print(json.dumps(limiter.get_stats(), indent=2))
        
        limiter.cleanup()
    
    asyncio.run(test())
