#!/usr/bin/env python3
"""
🎯 止盈止損優化分析器
=======================

分析幣安和 dYdX 的市場數據，找出最佳止盈止損設定。

分析項目:
1. 價格波動率 (Volatility) - ATR, 標準差
2. 點差分析 (Spread) - 確保 SL/TP > spread
3. 典型價格擺動 (Swing) - 正常波動範圍
4. 回撤分析 (Drawdown) - 趨勢中的回撤
5. 噪音過濾建議 - 區分噪音 vs 信號

使用方式:
    python scripts/analyze_sl_tp_optimization.py --hours 24
    python scripts/analyze_sl_tp_optimization.py --hours 4 --realtime

Author: AI Assistant
Version: 1.0.0
Date: 2025-12-17
"""

import asyncio
import aiohttp
import json
import time
import argparse
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from collections import deque
import statistics
import math

# ANSI 顏色
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    END = '\033[0m'


@dataclass
class MarketStats:
    """市場統計數據"""
    # 基本資訊
    symbol: str = "BTC-USD"
    timeframe: str = "1h"
    data_points: int = 0
    
    # 波動率指標
    atr_pct: float = 0.0           # ATR 百分比
    std_pct: float = 0.0           # 標準差百分比
    volatility_1h: float = 0.0     # 1小時波動率
    volatility_4h: float = 0.0     # 4小時波動率
    volatility_24h: float = 0.0    # 24小時波動率
    
    # 點差分析
    avg_spread_pct: float = 0.0    # 平均點差
    max_spread_pct: float = 0.0    # 最大點差
    p95_spread_pct: float = 0.0    # 95 百分位點差
    
    # 價格擺動
    avg_swing_pct: float = 0.0     # 平均擺動
    max_swing_up: float = 0.0      # 最大向上擺動
    max_swing_down: float = 0.0    # 最大向下擺動
    
    # 回撤分析
    avg_pullback_pct: float = 0.0  # 平均回撤 (趨勢中)
    max_pullback_pct: float = 0.0  # 最大回撤
    
    # 噪音分析
    noise_floor_pct: float = 0.0   # 噪音地板 (無意義波動)
    signal_threshold: float = 0.0  # 信號閾值
    
    # 建議設定
    suggested_sl_min: float = 0.0
    suggested_sl_max: float = 0.0
    suggested_tp_min: float = 0.0
    suggested_tp_max: float = 0.0


class BinanceAnalyzer:
    """幣安數據分析器"""
    
    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol
        self.base_url = "https://fapi.binance.com"
        
    async def get_klines(self, interval: str = "1m", limit: int = 1000) -> List[Dict]:
        """獲取 K 線數據"""
        url = f"{self.base_url}/fapi/v1/klines"
        params = {
            "symbol": self.symbol,
            "interval": interval,
            "limit": limit
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return [
                        {
                            "time": d[0],
                            "open": float(d[1]),
                            "high": float(d[2]),
                            "low": float(d[3]),
                            "close": float(d[4]),
                            "volume": float(d[5])
                        }
                        for d in data
                    ]
        return []
    
    async def get_recent_trades(self, limit: int = 1000) -> List[Dict]:
        """獲取最近成交"""
        url = f"{self.base_url}/fapi/v1/trades"
        params = {"symbol": self.symbol, "limit": limit}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
        return []
    
    async def get_orderbook(self, limit: int = 20) -> Dict:
        """獲取訂單簿"""
        url = f"{self.base_url}/fapi/v1/depth"
        params = {"symbol": self.symbol, "limit": limit}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
        return {}


class DydxAnalyzer:
    """dYdX 數據分析器"""
    
    def __init__(self, symbol: str = "BTC-USD", network: str = "mainnet"):
        self.symbol = symbol
        if network == "mainnet":
            self.base_url = "https://indexer.dydx.trade/v4"
        else:
            self.base_url = "https://indexer.v4testnet.dydx.exchange/v4"
    
    async def get_candles(self, resolution: str = "1MIN", limit: int = 100) -> List[Dict]:
        """獲取 K 線數據"""
        url = f"{self.base_url}/candles/perpetualMarkets/{self.symbol}"
        params = {"resolution": resolution, "limit": limit}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candles = data.get("candles", [])
                    return [
                        {
                            "time": c.get("startedAt"),
                            "open": float(c.get("open", 0)),
                            "high": float(c.get("high", 0)),
                            "low": float(c.get("low", 0)),
                            "close": float(c.get("close", 0)),
                            "volume": float(c.get("baseTokenVolume", 0))
                        }
                        for c in candles
                    ]
        return []
    
    async def get_orderbook(self) -> Dict:
        """獲取訂單簿"""
        url = f"{self.base_url}/orderbooks/perpetualMarket/{self.symbol}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        return {}


class RealtimeMonitor:
    """即時 WebSocket 監控"""
    
    def __init__(self):
        self.spreads: deque = deque(maxlen=10000)
        self.prices: deque = deque(maxlen=10000)
        self.swings: deque = deque(maxlen=1000)
        self.running = False
        
    async def monitor_dydx(self, duration_sec: int = 300):
        """監控 dYdX WebSocket"""
        import websockets
        
        ws_url = "wss://indexer.dydx.trade/v4/ws"
        self.running = True
        
        start_time = time.time()
        last_price = 0.0
        swing_start_price = 0.0
        swing_direction = 0  # 1=up, -1=down
        
        print(f"\n{Colors.CYAN}📡 連接 dYdX WebSocket...{Colors.END}")
        
        try:
            async with websockets.connect(ws_url, ping_interval=30) as ws:
                # 訂閱
                await ws.send(json.dumps({
                    "type": "subscribe",
                    "channel": "v4_orderbook",
                    "id": "BTC-USD"
                }))
                
                print(f"{Colors.GREEN}✅ 已連接，收集數據中 ({duration_sec}秒)...{Colors.END}\n")
                
                async for message in ws:
                    if time.time() - start_time > duration_sec:
                        break
                    
                    try:
                        data = json.loads(message)
                        contents = data.get("contents", {})
                        
                        bids = contents.get("bids", [])
                        asks = contents.get("asks", [])
                        
                        if bids and asks:
                            # 處理不同格式
                            if isinstance(bids[0], dict):
                                bid = float(bids[0].get("price", 0))
                                ask = float(asks[0].get("price", 0))
                            else:
                                bid = float(bids[0][0])
                                ask = float(asks[0][0])
                            
                            if bid > 0 and ask > 0:
                                spread_pct = (ask - bid) / bid * 100
                                mid_price = (bid + ask) / 2
                                
                                self.spreads.append(spread_pct)
                                self.prices.append(mid_price)
                                
                                # 追蹤擺動
                                if last_price > 0:
                                    change_pct = (mid_price - last_price) / last_price * 100
                                    
                                    # 檢測方向變化
                                    if swing_direction == 0:
                                        swing_direction = 1 if change_pct > 0 else -1
                                        swing_start_price = last_price
                                    elif (swing_direction == 1 and change_pct < -0.01) or \
                                         (swing_direction == -1 and change_pct > 0.01):
                                        # 方向改變，記錄擺動
                                        swing_pct = abs(mid_price - swing_start_price) / swing_start_price * 100
                                        if swing_pct > 0.01:  # 過濾太小的擺動
                                            self.swings.append(swing_pct)
                                        swing_start_price = mid_price
                                        swing_direction = 1 if change_pct > 0 else -1
                                
                                last_price = mid_price
                                
                                # 進度顯示
                                elapsed = time.time() - start_time
                                progress = int(elapsed / duration_sec * 20)
                                bar = "█" * progress + "░" * (20 - progress)
                                print(f"\r  [{bar}] {elapsed:.0f}s | "
                                      f"Spread: {spread_pct:.4f}% | "
                                      f"Samples: {len(self.spreads)}", end="")
                                
                    except Exception as e:
                        pass
                        
        except Exception as e:
            print(f"\n{Colors.RED}❌ WebSocket 錯誤: {e}{Colors.END}")
        
        print(f"\n\n{Colors.GREEN}✅ 數據收集完成！{Colors.END}")
        
        return {
            "spreads": list(self.spreads),
            "prices": list(self.prices),
            "swings": list(self.swings)
        }


class SLTPOptimizer:
    """止盈止損優化器"""
    
    def __init__(self):
        self.binance = BinanceAnalyzer()
        self.dydx = DydxAnalyzer()
        self.realtime = RealtimeMonitor()
        
    async def analyze(self, hours: int = 24, realtime_sec: int = 0) -> MarketStats:
        """執行完整分析"""
        stats = MarketStats()
        
        print(f"\n{Colors.HEADER}{'='*60}")
        print(f"  🎯 止盈止損優化分析器")
        print(f"  分析時間範圍: {hours} 小時")
        print(f"{'='*60}{Colors.END}\n")
        
        # 1. 獲取歷史 K 線數據
        print(f"{Colors.BLUE}📊 [1/5] 獲取歷史數據...{Colors.END}")
        
        # 幣安 1m K 線 (最多 1000 根 = ~16小時)
        klines_1m = await self.binance.get_klines("1m", min(hours * 60, 1000))
        # 幣安 5m K 線 (更長時間範圍)
        klines_5m = await self.binance.get_klines("5m", min(hours * 12, 1000))
        # 幣安 1h K 線
        klines_1h = await self.binance.get_klines("1h", min(hours, 500))
        
        print(f"  - 1m K線: {len(klines_1m)} 根")
        print(f"  - 5m K線: {len(klines_5m)} 根")
        print(f"  - 1h K線: {len(klines_1h)} 根")
        
        stats.data_points = len(klines_1m)
        
        # 2. 計算波動率
        print(f"\n{Colors.BLUE}📈 [2/5] 計算波動率...{Colors.END}")
        
        volatility = self._calculate_volatility(klines_1m, klines_5m, klines_1h)
        stats.atr_pct = volatility["atr_pct"]
        stats.std_pct = volatility["std_pct"]
        stats.volatility_1h = volatility["vol_1h"]
        stats.volatility_4h = volatility["vol_4h"]
        stats.volatility_24h = volatility["vol_24h"]
        
        print(f"  - ATR%: {stats.atr_pct:.4f}%")
        print(f"  - 標準差%: {stats.std_pct:.4f}%")
        print(f"  - 1h 波動率: {stats.volatility_1h:.4f}%")
        print(f"  - 4h 波動率: {stats.volatility_4h:.4f}%")
        print(f"  - 24h 波動率: {stats.volatility_24h:.4f}%")
        
        # 3. 點差分析
        print(f"\n{Colors.BLUE}💰 [3/5] 點差分析...{Colors.END}")
        
        # 從 dYdX 獲取訂單簿
        orderbook = await self.dydx.get_orderbook()
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        
        if bids and asks:
            bid = float(bids[0].get("price", 0))
            ask = float(asks[0].get("price", 0))
            current_spread = (ask - bid) / bid * 100
            print(f"  - 當前點差: {current_spread:.4f}%")
            stats.avg_spread_pct = current_spread
        
        # 如果有即時監控數據
        if realtime_sec > 0:
            print(f"\n{Colors.CYAN}⏱️  執行即時監控 ({realtime_sec}秒)...{Colors.END}")
            rt_data = await self.realtime.monitor_dydx(realtime_sec)
            
            if rt_data["spreads"]:
                spreads = rt_data["spreads"]
                stats.avg_spread_pct = statistics.mean(spreads)
                stats.max_spread_pct = max(spreads)
                stats.p95_spread_pct = sorted(spreads)[int(len(spreads) * 0.95)]
                
                print(f"  - 平均點差: {stats.avg_spread_pct:.4f}%")
                print(f"  - 最大點差: {stats.max_spread_pct:.4f}%")
                print(f"  - P95 點差: {stats.p95_spread_pct:.4f}%")
            
            if rt_data["swings"]:
                swings = rt_data["swings"]
                stats.avg_swing_pct = statistics.mean(swings)
                print(f"  - 平均擺動: {stats.avg_swing_pct:.4f}%")
        
        # 4. 擺動與回撤分析
        print(f"\n{Colors.BLUE}📉 [4/5] 擺動與回撤分析...{Colors.END}")
        
        swing_analysis = self._analyze_swings(klines_1m, klines_5m)
        stats.avg_swing_pct = swing_analysis["avg_swing"]
        stats.max_swing_up = swing_analysis["max_up"]
        stats.max_swing_down = swing_analysis["max_down"]
        stats.avg_pullback_pct = swing_analysis["avg_pullback"]
        stats.max_pullback_pct = swing_analysis["max_pullback"]
        
        print(f"  - 平均擺動: {stats.avg_swing_pct:.4f}%")
        print(f"  - 最大向上: {stats.max_swing_up:.4f}%")
        print(f"  - 最大向下: {stats.max_swing_down:.4f}%")
        print(f"  - 平均回撤: {stats.avg_pullback_pct:.4f}%")
        print(f"  - 最大回撤: {stats.max_pullback_pct:.4f}%")
        
        # 5. 計算建議設定
        print(f"\n{Colors.BLUE}🎯 [5/5] 計算建議設定...{Colors.END}")
        
        suggestions = self._calculate_suggestions(stats)
        stats.suggested_sl_min = suggestions["sl_min"]
        stats.suggested_sl_max = suggestions["sl_max"]
        stats.suggested_tp_min = suggestions["tp_min"]
        stats.suggested_tp_max = suggestions["tp_max"]
        stats.noise_floor_pct = suggestions["noise_floor"]
        stats.signal_threshold = suggestions["signal_threshold"]
        
        return stats
    
    def _calculate_volatility(self, klines_1m: List[Dict], klines_5m: List[Dict], klines_1h: List[Dict]) -> Dict:
        """計算各時間框架的波動率"""
        result = {
            "atr_pct": 0.0,
            "std_pct": 0.0,
            "vol_1h": 0.0,
            "vol_4h": 0.0,
            "vol_24h": 0.0
        }
        
        if not klines_1m:
            return result
        
        # ATR 計算 (使用 1m K線)
        atrs = []
        for i in range(1, len(klines_1m)):
            high = klines_1m[i]["high"]
            low = klines_1m[i]["low"]
            prev_close = klines_1m[i-1]["close"]
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            atr_pct = tr / prev_close * 100
            atrs.append(atr_pct)
        
        if atrs:
            result["atr_pct"] = statistics.mean(atrs)
        
        # 收盤價變化標準差
        returns = []
        for i in range(1, len(klines_1m)):
            ret = (klines_1m[i]["close"] - klines_1m[i-1]["close"]) / klines_1m[i-1]["close"] * 100
            returns.append(ret)
        
        if returns:
            result["std_pct"] = statistics.stdev(returns)
        
        # 不同時間框架的波動率
        if len(klines_1h) >= 2:
            vol_1h = []
            for i in range(1, min(len(klines_1h), 25)):  # 最近24小時
                high = klines_1h[i]["high"]
                low = klines_1h[i]["low"]
                mid = (high + low) / 2
                vol = (high - low) / mid * 100
                vol_1h.append(vol)
            if vol_1h:
                result["vol_1h"] = statistics.mean(vol_1h)
        
        if len(klines_1h) >= 4:
            # 4小時波動 (用 4 根 1h K線)
            vol_4h = []
            for i in range(0, min(len(klines_1h) - 3, 6), 4):
                high = max(k["high"] for k in klines_1h[i:i+4])
                low = min(k["low"] for k in klines_1h[i:i+4])
                mid = (high + low) / 2
                vol = (high - low) / mid * 100
                vol_4h.append(vol)
            if vol_4h:
                result["vol_4h"] = statistics.mean(vol_4h)
        
        if len(klines_1h) >= 24:
            # 24小時波動
            high = max(k["high"] for k in klines_1h[:24])
            low = min(k["low"] for k in klines_1h[:24])
            mid = (high + low) / 2
            result["vol_24h"] = (high - low) / mid * 100
        
        return result
    
    def _analyze_swings(self, klines_1m: List[Dict], klines_5m: List[Dict]) -> Dict:
        """分析價格擺動和回撤"""
        result = {
            "avg_swing": 0.0,
            "max_up": 0.0,
            "max_down": 0.0,
            "avg_pullback": 0.0,
            "max_pullback": 0.0
        }
        
        # 使用 5m K線分析擺動
        klines = klines_5m if klines_5m else klines_1m
        if len(klines) < 10:
            return result
        
        swings_up = []
        swings_down = []
        pullbacks = []
        
        # 找出局部高低點
        highs = []
        lows = []
        
        for i in range(2, len(klines) - 2):
            # 局部高點
            if (klines[i]["high"] > klines[i-1]["high"] and 
                klines[i]["high"] > klines[i-2]["high"] and
                klines[i]["high"] > klines[i+1]["high"] and
                klines[i]["high"] > klines[i+2]["high"]):
                highs.append((i, klines[i]["high"]))
            
            # 局部低點
            if (klines[i]["low"] < klines[i-1]["low"] and 
                klines[i]["low"] < klines[i-2]["low"] and
                klines[i]["low"] < klines[i+1]["low"] and
                klines[i]["low"] < klines[i+2]["low"]):
                lows.append((i, klines[i]["low"]))
        
        # 計算擺動
        all_points = sorted(highs + lows, key=lambda x: x[0])
        
        for i in range(1, len(all_points)):
            prev_idx, prev_price = all_points[i-1]
            curr_idx, curr_price = all_points[i]
            
            swing_pct = (curr_price - prev_price) / prev_price * 100
            
            if swing_pct > 0:
                swings_up.append(swing_pct)
            else:
                swings_down.append(abs(swing_pct))
        
        # 計算回撤 (上漲後的回調)
        for i in range(len(highs)):
            high_idx, high_price = highs[i]
            
            # 找這個高點之後的最低點
            min_after = high_price
            for j in range(high_idx + 1, min(high_idx + 20, len(klines))):
                if klines[j]["low"] < min_after:
                    min_after = klines[j]["low"]
            
            if min_after < high_price:
                pullback = (high_price - min_after) / high_price * 100
                pullbacks.append(pullback)
        
        if swings_up:
            result["max_up"] = max(swings_up)
        if swings_down:
            result["max_down"] = max(swings_down)
        
        all_swings = swings_up + swings_down
        if all_swings:
            result["avg_swing"] = statistics.mean(all_swings)
        
        if pullbacks:
            result["avg_pullback"] = statistics.mean(pullbacks)
            result["max_pullback"] = max(pullbacks)
        
        return result
    
    def _calculate_suggestions(self, stats: MarketStats) -> Dict:
        """根據分析結果計算建議的 SL/TP 設定"""
        
        # 噪音地板 = 點差 + 1倍 ATR
        noise_floor = stats.avg_spread_pct + stats.atr_pct
        
        # 信號閾值 = 2倍噪音地板
        signal_threshold = noise_floor * 2
        
        # 止損建議
        # 最小 SL = 噪音地板 * 1.5 (避免被噪音洗掉)
        # 最大 SL = 平均回撤 * 1.2
        sl_min = max(noise_floor * 1.5, stats.avg_spread_pct * 3)
        sl_max = max(stats.avg_pullback_pct * 1.2, stats.volatility_1h * 0.8)
        
        # 止盈建議
        # 最小 TP = 平均擺動 * 0.6 (保守)
        # 最大 TP = 平均擺動 * 1.2 (積極)
        tp_min = max(stats.avg_swing_pct * 0.6, sl_min * 1.5)  # RR >= 1.5
        tp_max = max(stats.avg_swing_pct * 1.2, stats.volatility_4h * 0.5)
        
        # 確保合理範圍
        sl_min = max(0.05, min(sl_min, 1.0))  # 0.05% - 1.0%
        sl_max = max(sl_min, min(sl_max, 2.0))  # sl_min - 2.0%
        tp_min = max(0.08, min(tp_min, 1.5))  # 0.08% - 1.5%
        tp_max = max(tp_min, min(tp_max, 3.0))  # tp_min - 3.0%
        
        return {
            "noise_floor": noise_floor,
            "signal_threshold": signal_threshold,
            "sl_min": sl_min,
            "sl_max": sl_max,
            "tp_min": tp_min,
            "tp_max": tp_max
        }
    
    def print_report(self, stats: MarketStats):
        """輸出分析報告"""
        
        print(f"\n{Colors.HEADER}{'='*60}")
        print(f"  📊 分析報告")
        print(f"{'='*60}{Colors.END}")
        
        print(f"\n{Colors.BOLD}【波動率分析】{Colors.END}")
        print(f"  ├─ ATR%:      {stats.atr_pct:.4f}%")
        print(f"  ├─ 標準差%:   {stats.std_pct:.4f}%")
        print(f"  ├─ 1h 波動:   {stats.volatility_1h:.4f}%")
        print(f"  ├─ 4h 波動:   {stats.volatility_4h:.4f}%")
        print(f"  └─ 24h 波動:  {stats.volatility_24h:.4f}%")
        
        print(f"\n{Colors.BOLD}【點差分析】{Colors.END}")
        print(f"  ├─ 平均點差:  {stats.avg_spread_pct:.4f}%")
        print(f"  ├─ 最大點差:  {stats.max_spread_pct:.4f}%")
        print(f"  └─ P95 點差:  {stats.p95_spread_pct:.4f}%")
        
        print(f"\n{Colors.BOLD}【擺動分析】{Colors.END}")
        print(f"  ├─ 平均擺動:  {stats.avg_swing_pct:.4f}%")
        print(f"  ├─ 最大向上:  {stats.max_swing_up:.4f}%")
        print(f"  └─ 最大向下:  {stats.max_swing_down:.4f}%")
        
        print(f"\n{Colors.BOLD}【回撤分析】{Colors.END}")
        print(f"  ├─ 平均回撤:  {stats.avg_pullback_pct:.4f}%")
        print(f"  └─ 最大回撤:  {stats.max_pullback_pct:.4f}%")
        
        print(f"\n{Colors.BOLD}【噪音分析】{Colors.END}")
        print(f"  ├─ 噪音地板:  {stats.noise_floor_pct:.4f}% (低於此為噪音)")
        print(f"  └─ 信號閾值:  {stats.signal_threshold:.4f}% (高於此為有效信號)")
        
        print(f"\n{Colors.HEADER}{'='*60}")
        print(f"  🎯 建議止盈止損設定")
        print(f"{'='*60}{Colors.END}")
        
        print(f"\n{Colors.GREEN}【止損 (Stop Loss)】{Colors.END}")
        print(f"  ├─ 最小 SL:   {Colors.YELLOW}{stats.suggested_sl_min:.2f}%{Colors.END}")
        print(f"  ├─ 最大 SL:   {Colors.YELLOW}{stats.suggested_sl_max:.2f}%{Colors.END}")
        print(f"  └─ 建議範圍:  {stats.suggested_sl_min:.2f}% ~ {stats.suggested_sl_max:.2f}%")
        
        print(f"\n{Colors.GREEN}【止盈 (Take Profit)】{Colors.END}")
        print(f"  ├─ 最小 TP:   {Colors.YELLOW}{stats.suggested_tp_min:.2f}%{Colors.END}")
        print(f"  ├─ 最大 TP:   {Colors.YELLOW}{stats.suggested_tp_max:.2f}%{Colors.END}")
        print(f"  └─ 建議範圍:  {stats.suggested_tp_min:.2f}% ~ {stats.suggested_tp_max:.2f}%")
        
        # 風險回報比
        rr_min = stats.suggested_tp_min / stats.suggested_sl_max
        rr_max = stats.suggested_tp_max / stats.suggested_sl_min
        
        print(f"\n{Colors.BOLD}【風險回報比 (R:R)】{Colors.END}")
        print(f"  └─ 範圍: {rr_min:.2f} ~ {rr_max:.2f}")
        
        # 具體設定建議
        print(f"\n{Colors.HEADER}{'='*60}")
        print(f"  📝 Trading Card 設定建議")
        print(f"{'='*60}{Colors.END}")
        
        # 保守策略
        print(f"\n{Colors.CYAN}【保守策略】(適合震盪市){Colors.END}")
        conservative_sl = (stats.suggested_sl_min + stats.suggested_sl_max) / 2
        conservative_tp = stats.suggested_tp_min * 1.2
        print(f'''
  "stop_loss_pct": {conservative_sl:.2f},
  "take_profit_pct": {conservative_tp:.2f},
  "trailing_stop_pct": {conservative_sl * 0.8:.2f}
''')
        
        # 積極策略
        print(f"{Colors.CYAN}【積極策略】(適合趨勢市){Colors.END}")
        aggressive_sl = stats.suggested_sl_max
        aggressive_tp = stats.suggested_tp_max
        print(f'''
  "stop_loss_pct": {aggressive_sl:.2f},
  "take_profit_pct": {aggressive_tp:.2f},
  "trailing_stop_pct": {aggressive_sl * 0.6:.2f}
''')
        
        # 階梯止盈
        print(f"{Colors.CYAN}【階梯止盈策略】{Colors.END}")
        tp1 = stats.suggested_tp_min
        tp2 = (stats.suggested_tp_min + stats.suggested_tp_max) / 2
        tp3 = stats.suggested_tp_max
        print(f'''
  "staged_exit": {{
    "enabled": true,
    "stages": [
      {{"threshold": {tp1:.2f}, "exit_pct": 30}},
      {{"threshold": {tp2:.2f}, "exit_pct": 40}},
      {{"threshold": {tp3:.2f}, "exit_pct": 30}}
    ]
  }}
''')
        
        # spread_guard 建議
        print(f"{Colors.CYAN}【Spread Guard 設定】{Colors.END}")
        base_entry = max(stats.avg_spread_pct * 2, 0.08)
        min_halt = max(stats.p95_spread_pct * 1.5, base_entry * 1.5) if stats.p95_spread_pct > 0 else base_entry * 1.5
        print(f'''
  "spread_guard": {{
    "enabled": true,
    "base_entry": {base_entry:.2f},
    "min_entry": {base_entry * 0.5:.2f},
    "max_entry": {base_entry * 2:.2f},
    "min_halt": {min_halt:.2f}
  }}
''')
        
        print(f"\n{Colors.GREEN}✅ 分析完成！{Colors.END}\n")


async def main():
    parser = argparse.ArgumentParser(description="止盈止損優化分析器")
    parser.add_argument("--hours", type=int, default=24, help="分析時間範圍 (小時)")
    parser.add_argument("--realtime", type=int, default=0, help="即時監控時間 (秒)")
    args = parser.parse_args()
    
    optimizer = SLTPOptimizer()
    
    try:
        stats = await optimizer.analyze(
            hours=args.hours,
            realtime_sec=args.realtime
        )
        optimizer.print_report(stats)
        
        # 保存結果
        result = {
            "timestamp": datetime.now().isoformat(),
            "hours_analyzed": args.hours,
            "stats": {
                "atr_pct": stats.atr_pct,
                "std_pct": stats.std_pct,
                "volatility_1h": stats.volatility_1h,
                "volatility_4h": stats.volatility_4h,
                "volatility_24h": stats.volatility_24h,
                "avg_spread_pct": stats.avg_spread_pct,
                "avg_swing_pct": stats.avg_swing_pct,
                "avg_pullback_pct": stats.avg_pullback_pct,
                "max_pullback_pct": stats.max_pullback_pct,
                "noise_floor_pct": stats.noise_floor_pct
            },
            "suggestions": {
                "stop_loss_min": stats.suggested_sl_min,
                "stop_loss_max": stats.suggested_sl_max,
                "take_profit_min": stats.suggested_tp_min,
                "take_profit_max": stats.suggested_tp_max
            }
        }
        
        with open("data/sl_tp_analysis.json", "w") as f:
            json.dump(result, f, indent=2)
        print(f"📁 結果已保存至 data/sl_tp_analysis.json\n")
        
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}⚠️ 已中斷{Colors.END}")
    except Exception as e:
        print(f"\n{Colors.RED}❌ 錯誤: {e}{Colors.END}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
