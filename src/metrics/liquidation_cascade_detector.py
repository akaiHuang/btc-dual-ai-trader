"""Liquidation Cascade Detector - 爆倉瀑布偵測器 v2.0

專門偵測 BTC 的爆倉連鎖反應（Liquidation Cascade），這是加密市場最強烈的訊號之一。

幣安 API 來源:
1. WebSocket: wss://fstream.binance.com/ws/btcusdt@forceOrder (即時爆倉)
2. REST: /fapi/v1/forceOrders (歷史爆倉，需認證)
3. REST: /futures/data/globalLongShortAccountRatio (多空比)
4. REST: /futures/data/openInterestHist (持倉量)
5. REST: /fapi/v1/fundingRate (資金費率)
6. WebSocket: btcusdt@aggTrade (大單偵測)
7. REST: /futures/data/takerlongshortRatio (主動買賣比)

爆倉瀑布特徵:
- 短時間內大量同方向爆倉 (> $5M in 1 min)
- 價格快速單向移動 (> 0.5% in 30s)
- OI 大幅下降 (被強平)
- 成交量暴增 (> 5x avg)

Author: AI Trading System
Version: 2.0.0
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import aiohttp
import websockets

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BINANCE_WS_BASE = "wss://fstream.binance.com/ws"
BINANCE_REST_BASE = "https://fapi.binance.com"

# 爆倉瀑布閾值
CASCADE_THRESHOLDS = {
    "min_cascade_usd": 3_000_000,      # 1分鐘內 $3M 爆倉觸發警報
    "critical_cascade_usd": 10_000_000, # $10M = 極端事件
    "mega_cascade_usd": 50_000_000,    # $50M = 歷史級別
    "price_velocity_pct": 0.3,          # 30秒內 0.3% 價格變動
    "volume_spike_ratio": 3.0,          # 成交量 3 倍突增
    "oi_drop_pct": 0.5,                 # OI 下降 0.5% = 大量被平倉
    "cascade_window_sec": 60,           # 偵測窗口 60 秒
    "burst_window_sec": 10,             # 短期爆發窗口 10 秒
}


class CascadeLevel(str, Enum):
    """爆倉瀑布等級"""
    QUIET = "QUIET"              # 平靜 - 無顯著爆倉
    BUILDING = "BUILDING"        # 醞釀中 - 開始有爆倉累積
    MINOR = "MINOR"              # 小型瀑布 - $1-3M
    SIGNIFICANT = "SIGNIFICANT"  # 顯著瀑布 - $3-10M
    MAJOR = "MAJOR"              # 大型瀑布 - $10-50M
    EXTREME = "EXTREME"          # 極端瀑布 - >$50M (閃崩/閃漲)


class CascadeDirection(str, Enum):
    """爆倉方向"""
    LONG_LIQUIDATION = "LONG_LIQUIDATION"    # 多頭被爆 → 做空機會
    SHORT_LIQUIDATION = "SHORT_LIQUIDATION"  # 空頭被爆 → 做多機會
    MIXED = "MIXED"                          # 雙向爆倉


@dataclass
class LiquidationEvent:
    """單筆爆倉事件"""
    timestamp: float           # Unix timestamp (ms)
    symbol: str
    side: str                  # "SELL" = 多頭被爆, "BUY" = 空頭被爆
    quantity: float
    price: float
    usd_value: float
    
    @classmethod
    def from_ws_message(cls, msg: Dict[str, Any]) -> "LiquidationEvent":
        """從 WebSocket 消息解析"""
        o = msg.get("o", {})
        qty = float(o.get("q", 0))
        price = float(o.get("ap", 0) or o.get("p", 0))
        return cls(
            timestamp=msg.get("E", int(time.time() * 1000)),
            symbol=o.get("s", "BTCUSDT"),
            side=o.get("S", "SELL"),
            quantity=qty,
            price=price,
            usd_value=qty * price
        )


@dataclass
class CascadeAlert:
    """爆倉瀑布警報"""
    timestamp: datetime
    level: CascadeLevel
    direction: CascadeDirection
    total_usd: float                    # 總爆倉金額
    long_liq_usd: float                 # 多頭爆倉金額
    short_liq_usd: float                # 空頭爆倉金額
    event_count: int                    # 爆倉筆數
    price_change_pct: float             # 期間價格變動
    duration_sec: float                 # 持續時間
    velocity: float                     # 爆倉速度 (USD/sec)
    recommended_action: str             # 建議操作
    confidence: float                   # 信心度 0-1
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level.value,
            "direction": self.direction.value,
            "total_usd": round(self.total_usd, 2),
            "long_liq_usd": round(self.long_liq_usd, 2),
            "short_liq_usd": round(self.short_liq_usd, 2),
            "event_count": self.event_count,
            "price_change_pct": round(self.price_change_pct, 4),
            "duration_sec": round(self.duration_sec, 1),
            "velocity": round(self.velocity, 2),
            "recommended_action": self.recommended_action,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class CascadeSnapshot:
    """爆倉瀑布快照"""
    timestamp: datetime
    level: CascadeLevel
    direction: CascadeDirection
    
    # 1分鐘窗口統計
    liq_1m_total_usd: float
    liq_1m_long_usd: float
    liq_1m_short_usd: float
    liq_1m_count: int
    
    # 10秒爆發窗口
    liq_10s_total_usd: float
    liq_10s_velocity: float  # USD/sec
    
    # 5分鐘累積
    liq_5m_total_usd: float
    liq_5m_long_usd: float
    liq_5m_short_usd: float
    
    # 價格相關
    price_now: float
    price_1m_ago: float
    price_change_1m_pct: float
    
    # 建議
    signal: str              # "LONG", "SHORT", "HOLD"
    signal_strength: float   # 0-100
    alert: Optional[CascadeAlert] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level.value,
            "direction": self.direction.value,
            "liq_1m_total_usd": round(self.liq_1m_total_usd, 2),
            "liq_1m_long_usd": round(self.liq_1m_long_usd, 2),
            "liq_1m_short_usd": round(self.liq_1m_short_usd, 2),
            "liq_1m_count": self.liq_1m_count,
            "liq_10s_total_usd": round(self.liq_10s_total_usd, 2),
            "liq_10s_velocity": round(self.liq_10s_velocity, 2),
            "liq_5m_total_usd": round(self.liq_5m_total_usd, 2),
            "liq_5m_long_usd": round(self.liq_5m_long_usd, 2),
            "liq_5m_short_usd": round(self.liq_5m_short_usd, 2),
            "price_now": self.price_now,
            "price_1m_ago": self.price_1m_ago,
            "price_change_1m_pct": round(self.price_change_1m_pct, 4),
            "signal": self.signal,
            "signal_strength": round(self.signal_strength, 1),
            "alert": self.alert.to_dict() if self.alert else None,
        }


class LiquidationCascadeDetector:
    """
    即時爆倉瀑布偵測器
    
    使用幣安 WebSocket 即時監聽爆倉流，偵測連鎖爆倉事件。
    
    偵測邏輯:
    1. 即時收集 btcusdt@forceOrder 流
    2. 滾動計算 10s/1m/5m 窗口的爆倉統計
    3. 當達到閾值時觸發 CascadeAlert
    4. 結合價格變動計算信號強度
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        cascade_callback: Optional[Callable[[CascadeAlert], None]] = None,
        snapshot_callback: Optional[Callable[[CascadeSnapshot], None]] = None,
    ):
        self.symbol = symbol.upper().replace("USDT", "").lower() + "usdt"
        self.cascade_callback = cascade_callback
        self.snapshot_callback = snapshot_callback
        
        # 爆倉事件緩存 (保留 5 分鐘)
        self._events: Deque[LiquidationEvent] = deque(maxlen=10000)
        
        # 價格追蹤
        self._price_history: Deque[Tuple[float, float]] = deque(maxlen=600)  # (ts, price)
        self._current_price: float = 0.0
        
        # 狀態
        self._running: bool = False
        self._ws_task: Optional[asyncio.Task] = None
        self._last_snapshot: Optional[CascadeSnapshot] = None
        self._last_alert: Optional[CascadeAlert] = None
        self._last_alert_time: float = 0
        
        # 統計
        self._total_liq_usd: float = 0.0
        self._long_liq_usd: float = 0.0
        self._short_liq_usd: float = 0.0
        
    # ---------------------- WebSocket 連接 ---------------------- #
    
    async def start(self):
        """啟動 WebSocket 連接"""
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        # 🆕 定期更新 snapshot (即使無爆倉事件)
        self._periodic_task = asyncio.create_task(self._periodic_snapshot_loop())
        print(f"🚀 爆倉瀑布偵測器啟動 - 監聯 {self.symbol}@forceOrder")
        
    async def stop(self):
        """停止 WebSocket 連接"""
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if hasattr(self, '_periodic_task') and self._periodic_task:
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
        print("🛑 爆倉瀑布偵測器已停止")
    
    async def _periodic_snapshot_loop(self):
        """🆕 定期發送 snapshot (每 5 秒)"""
        while self._running:
            try:
                await asyncio.sleep(5)
                if self._running:
                    await self._check_cascade()  # 強制更新快照
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ 定期快照更新錯誤: {e}")
                await asyncio.sleep(5)
        
    async def _ws_loop(self):
        """WebSocket 主循環"""
        streams = [
            f"{self.symbol}@forceOrder",  # 爆倉流
            f"{self.symbol}@aggTrade",     # 成交流 (用於價格追蹤)
        ]
        ws_url = f"{BINANCE_WS_BASE}/{'/'.join(streams)}"
        
        # 使用組合流
        combined_url = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"
        
        reconnect_delay = 1
        max_reconnect_delay = 60
        
        while self._running:
            try:
                async with websockets.connect(combined_url) as ws:
                    print(f"✅ 已連接爆倉流 WebSocket")
                    reconnect_delay = 1
                    
                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_message(message)
                        
            except websockets.ConnectionClosed as e:
                print(f"⚠️ WebSocket 斷線: {e}")
            except Exception as e:
                print(f"❌ WebSocket 錯誤: {e}")
                
            if self._running:
                print(f"🔄 {reconnect_delay}秒後重連...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                
    async def _handle_message(self, message: str):
        """處理 WebSocket 消息"""
        try:
            data = json.loads(message)
            
            # 組合流格式
            if "stream" in data:
                stream = data["stream"]
                payload = data["data"]
                
                if "forceOrder" in stream:
                    await self._handle_liquidation(payload)
                elif "aggTrade" in stream:
                    await self._handle_trade(payload)
            else:
                # 單流格式
                event_type = data.get("e")
                if event_type == "forceOrder":
                    await self._handle_liquidation(data)
                elif event_type == "aggTrade":
                    await self._handle_trade(data)
                    
        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"⚠️ 消息處理錯誤: {e}")
            
    async def _handle_liquidation(self, data: Dict[str, Any]):
        """處理爆倉事件"""
        event = LiquidationEvent.from_ws_message(data)
        self._events.append(event)
        
        # 更新統計
        self._total_liq_usd += event.usd_value
        if event.side == "SELL":  # 多頭被爆
            self._long_liq_usd += event.usd_value
        else:  # 空頭被爆
            self._short_liq_usd += event.usd_value
            
        # 即時打印大額爆倉
        if event.usd_value >= 100_000:  # $100k+
            direction = "🔴多頭" if event.side == "SELL" else "🟢空頭"
            print(f"💥 大額爆倉: {direction} ${event.usd_value/1e6:.2f}M @ {event.price:.0f}")
            
        # 檢查是否觸發瀑布警報
        await self._check_cascade()
        
    async def _handle_trade(self, data: Dict[str, Any]):
        """處理成交 (價格追蹤)"""
        price = float(data.get("p", 0))
        ts = data.get("T", int(time.time() * 1000))
        
        if price > 0:
            self._current_price = price
            self._price_history.append((ts, price))
            
    # ---------------------- 瀑布偵測 ---------------------- #
    
    async def _check_cascade(self):
        """檢查是否觸發爆倉瀑布"""
        now = time.time() * 1000
        
        # 計算各時間窗口統計
        stats_10s = self._calc_window_stats(now, 10 * 1000)
        stats_1m = self._calc_window_stats(now, 60 * 1000)
        stats_5m = self._calc_window_stats(now, 5 * 60 * 1000)
        
        # 計算價格變動
        price_change_1m = self._calc_price_change(now, 60 * 1000)
        
        # 判斷瀑布等級
        level = self._determine_level(stats_1m["total_usd"], stats_10s["velocity"])
        direction = self._determine_direction(stats_1m["long_usd"], stats_1m["short_usd"])
        
        # 生成快照
        snapshot = CascadeSnapshot(
            timestamp=datetime.now(timezone.utc),
            level=level,
            direction=direction,
            liq_1m_total_usd=stats_1m["total_usd"],
            liq_1m_long_usd=stats_1m["long_usd"],
            liq_1m_short_usd=stats_1m["short_usd"],
            liq_1m_count=stats_1m["count"],
            liq_10s_total_usd=stats_10s["total_usd"],
            liq_10s_velocity=stats_10s["velocity"],
            liq_5m_total_usd=stats_5m["total_usd"],
            liq_5m_long_usd=stats_5m["long_usd"],
            liq_5m_short_usd=stats_5m["short_usd"],
            price_now=self._current_price,
            price_1m_ago=price_change_1m["price_ago"],
            price_change_1m_pct=price_change_1m["change_pct"],
            signal=self._generate_signal(level, direction, price_change_1m["change_pct"]),
            signal_strength=self._calc_signal_strength(level, stats_1m, stats_10s),
        )
        
        self._last_snapshot = snapshot
        
        # 觸發快照回調
        if self.snapshot_callback:
            self.snapshot_callback(snapshot)
            
        # 檢查是否需要觸發警報
        if level in [CascadeLevel.SIGNIFICANT, CascadeLevel.MAJOR, CascadeLevel.EXTREME]:
            # 冷卻機制: 30秒內不重複警報
            if now - self._last_alert_time > 30_000:
                alert = self._create_alert(snapshot, stats_1m, price_change_1m)
                self._last_alert = alert
                self._last_alert_time = now
                
                snapshot.alert = alert
                
                if self.cascade_callback:
                    self.cascade_callback(alert)
                    
                # 打印警報
                self._print_alert(alert)
                
    def _calc_window_stats(self, now_ms: float, window_ms: float) -> Dict[str, float]:
        """計算指定時間窗口的爆倉統計"""
        cutoff = now_ms - window_ms
        
        total_usd = 0.0
        long_usd = 0.0
        short_usd = 0.0
        count = 0
        
        for event in self._events:
            if event.timestamp >= cutoff:
                total_usd += event.usd_value
                count += 1
                if event.side == "SELL":
                    long_usd += event.usd_value
                else:
                    short_usd += event.usd_value
                    
        velocity = total_usd / (window_ms / 1000) if window_ms > 0 else 0
        
        return {
            "total_usd": total_usd,
            "long_usd": long_usd,
            "short_usd": short_usd,
            "count": count,
            "velocity": velocity,
        }
        
    def _calc_price_change(self, now_ms: float, window_ms: float) -> Dict[str, float]:
        """計算價格變動"""
        cutoff = now_ms - window_ms
        price_ago = self._current_price
        
        for ts, price in self._price_history:
            if ts >= cutoff:
                price_ago = price
                break
                
        change_pct = 0.0
        if price_ago > 0:
            change_pct = (self._current_price - price_ago) / price_ago * 100
            
        return {
            "price_ago": price_ago,
            "change_pct": change_pct,
        }
        
    def _determine_level(self, total_usd: float, velocity: float) -> CascadeLevel:
        """判斷瀑布等級"""
        thresholds = CASCADE_THRESHOLDS
        
        if total_usd >= thresholds["mega_cascade_usd"]:
            return CascadeLevel.EXTREME
        if total_usd >= thresholds["critical_cascade_usd"]:
            return CascadeLevel.MAJOR
        if total_usd >= thresholds["min_cascade_usd"]:
            return CascadeLevel.SIGNIFICANT
        if total_usd >= 1_000_000:  # $1M
            return CascadeLevel.MINOR
        if total_usd >= 500_000 or velocity >= 50_000:  # $500k or $50k/sec
            return CascadeLevel.BUILDING
        return CascadeLevel.QUIET
        
    def _determine_direction(self, long_usd: float, short_usd: float) -> CascadeDirection:
        """判斷爆倉方向"""
        total = long_usd + short_usd
        if total == 0:
            return CascadeDirection.MIXED
            
        long_ratio = long_usd / total
        
        if long_ratio >= 0.7:
            return CascadeDirection.LONG_LIQUIDATION
        if long_ratio <= 0.3:
            return CascadeDirection.SHORT_LIQUIDATION
        return CascadeDirection.MIXED
        
    def _generate_signal(self, level: CascadeLevel, direction: CascadeDirection, price_change_pct: float) -> str:
        """生成交易信號"""
        if level in [CascadeLevel.QUIET, CascadeLevel.BUILDING]:
            return "HOLD"
            
        # 爆倉瀑布 + 價格已經大跌/大漲 = 反轉機會
        if direction == CascadeDirection.LONG_LIQUIDATION:
            # 多頭被爆，價格下跌，可能到底了
            if price_change_pct < -0.5:
                return "LONG"  # 抄底
            return "SHORT"  # 跟隨趨勢做空
            
        if direction == CascadeDirection.SHORT_LIQUIDATION:
            # 空頭被爆，價格上漲，可能到頂了
            if price_change_pct > 0.5:
                return "SHORT"  # 做空頂部
            return "LONG"  # 跟隨趨勢做多
            
        return "HOLD"  # 混合爆倉不好判斷
        
    def _calc_signal_strength(
        self, 
        level: CascadeLevel, 
        stats_1m: Dict[str, float],
        stats_10s: Dict[str, float]
    ) -> float:
        """計算信號強度 0-100"""
        base_strength = {
            CascadeLevel.QUIET: 0,
            CascadeLevel.BUILDING: 20,
            CascadeLevel.MINOR: 40,
            CascadeLevel.SIGNIFICANT: 60,
            CascadeLevel.MAJOR: 80,
            CascadeLevel.EXTREME: 95,
        }.get(level, 0)
        
        # 方向性加成
        total = stats_1m["long_usd"] + stats_1m["short_usd"]
        if total > 0:
            dominant_ratio = max(stats_1m["long_usd"], stats_1m["short_usd"]) / total
            direction_bonus = (dominant_ratio - 0.5) * 20  # 0-10 bonus
            base_strength += direction_bonus
            
        # 速度加成
        if stats_10s["velocity"] > 500_000:  # $500k/sec
            base_strength += 10
            
        return min(100, max(0, base_strength))
        
    def _create_alert(
        self, 
        snapshot: CascadeSnapshot, 
        stats: Dict[str, float],
        price_change: Dict[str, float]
    ) -> CascadeAlert:
        """創建爆倉瀑布警報"""
        
        # 生成建議
        if snapshot.direction == CascadeDirection.LONG_LIQUIDATION:
            if price_change["change_pct"] < -1.0:
                action = "🔥 多頭連環爆！價格已跌 >1%，考慮抄底做多"
            else:
                action = "⚠️ 多頭爆倉中，優先做空順勢"
        elif snapshot.direction == CascadeDirection.SHORT_LIQUIDATION:
            if price_change["change_pct"] > 1.0:
                action = "🔥 空頭連環爆！價格已漲 >1%，考慮做空回調"
            else:
                action = "⚠️ 空頭爆倉中，優先做多順勢"
        else:
            action = "⚡ 雙向爆倉，波動劇烈，謹慎操作"
            
        return CascadeAlert(
            timestamp=snapshot.timestamp,
            level=snapshot.level,
            direction=snapshot.direction,
            total_usd=stats["total_usd"],
            long_liq_usd=stats["long_usd"],
            short_liq_usd=stats["short_usd"],
            event_count=stats["count"],
            price_change_pct=price_change["change_pct"],
            duration_sec=60.0,
            velocity=snapshot.liq_10s_velocity,
            recommended_action=action,
            confidence=snapshot.signal_strength / 100,
        )
        
    def _print_alert(self, alert: CascadeAlert):
        """打印警報到控制台"""
        level_emoji = {
            CascadeLevel.SIGNIFICANT: "🟡",
            CascadeLevel.MAJOR: "🟠",
            CascadeLevel.EXTREME: "🔴",
        }.get(alert.level, "⚪")
        
        direction_emoji = {
            CascadeDirection.LONG_LIQUIDATION: "📉 多頭爆倉",
            CascadeDirection.SHORT_LIQUIDATION: "📈 空頭爆倉",
            CascadeDirection.MIXED: "⚡ 雙向爆倉",
        }.get(alert.direction, "")
        
        print("\n" + "=" * 60)
        print(f"{level_emoji} 💣 爆倉瀑布警報 - {alert.level.value} {level_emoji}")
        print("=" * 60)
        print(f"⏰ 時間: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"📊 方向: {direction_emoji}")
        print(f"💰 總爆倉: ${alert.total_usd/1e6:.2f}M")
        print(f"   🐂 多頭: ${alert.long_liq_usd/1e6:.2f}M")
        print(f"   🐻 空頭: ${alert.short_liq_usd/1e6:.2f}M")
        print(f"📈 價格變動: {alert.price_change_pct:+.2f}%")
        print(f"⚡ 爆倉速度: ${alert.velocity/1e3:.0f}k/秒")
        print(f"🎯 信心度: {alert.confidence*100:.0f}%")
        print(f"\n💡 {alert.recommended_action}")
        print("=" * 60 + "\n")
        
    # ---------------------- 公開方法 ---------------------- #
    
    def get_snapshot(self) -> Optional[CascadeSnapshot]:
        """獲取最新快照"""
        return self._last_snapshot
        
    def get_last_alert(self) -> Optional[CascadeAlert]:
        """獲取最新警報"""
        return self._last_alert
        
    def get_stats(self) -> Dict[str, Any]:
        """獲取統計信息"""
        return {
            "total_liq_usd": self._total_liq_usd,
            "long_liq_usd": self._long_liq_usd,
            "short_liq_usd": self._short_liq_usd,
            "event_count": len(self._events),
            "current_price": self._current_price,
            "running": self._running,
        }
        
    def render_panel(self) -> str:
        """渲染爆倉瀑布面板 (用於 Paper Trading 顯示)"""
        snapshot = self._last_snapshot
        
        if not snapshot:
            return "💣 爆倉瀑布雷達: ⏳ 等待數據..."
            
        level_bar = {
            CascadeLevel.QUIET: "[----------] 平靜",
            CascadeLevel.BUILDING: "[██--------] 醞釀中",
            CascadeLevel.MINOR: "[████------] 小型",
            CascadeLevel.SIGNIFICANT: "[██████----] 顯著 ⚠️",
            CascadeLevel.MAJOR: "[████████--] 大型 🔥",
            CascadeLevel.EXTREME: "[██████████] 極端 💥",
        }.get(snapshot.level, "[----------]")
        
        direction_text = {
            CascadeDirection.LONG_LIQUIDATION: "📉 多頭被爆",
            CascadeDirection.SHORT_LIQUIDATION: "📈 空頭被爆",
            CascadeDirection.MIXED: "⚡ 雙向爆倉",
        }.get(snapshot.direction, "")
        
        lines = [
            "💣 爆倉瀑布雷達 (Liquidation Cascade)",
            f"📊 瀑布等級: {level_bar}",
            f"➡ 方向: {direction_text}",
            f"💰 1分鐘爆倉: ${snapshot.liq_1m_total_usd/1e6:.2f}M ({snapshot.liq_1m_count}筆)",
            f"   🐂 多頭: ${snapshot.liq_1m_long_usd/1e6:.2f}M",
            f"   🐻 空頭: ${snapshot.liq_1m_short_usd/1e6:.2f}M",
            f"⚡ 爆倉速度: ${snapshot.liq_10s_velocity/1e3:.0f}k/秒",
            f"📈 價格變動: {snapshot.price_change_1m_pct:+.2f}%",
            f"🎯 信號: {snapshot.signal} (強度: {snapshot.signal_strength:.0f})",
        ]
        
        if snapshot.alert:
            lines.append(f"\n💡 {snapshot.alert.recommended_action}")
            
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# REST API 補充數據獲取
# ---------------------------------------------------------------------------

class LiquidationDataFetcher:
    """
    補充數據獲取器
    
    用於獲取幣安提供的其他爆倉相關數據:
    - Taker Buy/Sell Volume Ratio
    - Open Interest Statistics
    - Long/Short Ratio
    """
    
    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol.upper()
        self.session = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()
            
    async def fetch_taker_long_short_ratio(self, period: str = "5m", limit: int = 30) -> List[Dict[str, Any]]:
        """
        獲取主動買賣比 (Taker Buy/Sell Ratio)
        
        這個指標顯示市場上主動買入 vs 主動賣出的比例，
        可以用來判斷市場情緒和可能的爆倉方向。
        
        API: GET /futures/data/takerlongshortRatio
        """
        url = f"{BINANCE_REST_BASE}/futures/data/takerlongshortRatio"
        params = {"symbol": self.symbol, "period": period, "limit": limit}
        
        async with self.session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            return []
            
    async def fetch_open_interest_hist(self, period: str = "5m", limit: int = 30) -> List[Dict[str, Any]]:
        """
        獲取歷史持倉量
        
        OI 大幅下降通常意味著爆倉發生。
        
        API: GET /futures/data/openInterestHist
        """
        url = f"{BINANCE_REST_BASE}/futures/data/openInterestHist"
        params = {"symbol": self.symbol, "period": period, "limit": limit}
        
        async with self.session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            return []
            
    async def fetch_global_long_short_ratio(self, period: str = "5m", limit: int = 30) -> List[Dict[str, Any]]:
        """
        獲取全市場多空比
        
        API: GET /futures/data/globalLongShortAccountRatio
        """
        url = f"{BINANCE_REST_BASE}/futures/data/globalLongShortAccountRatio"
        params = {"symbol": self.symbol, "period": period, "limit": limit}
        
        async with self.session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            return []
            
    async def fetch_top_long_short_ratio(self, period: str = "5m", limit: int = 30) -> List[Dict[str, Any]]:
        """
        獲取頂級交易員多空比
        
        API: GET /futures/data/topLongShortAccountRatio
        """
        url = f"{BINANCE_REST_BASE}/futures/data/topLongShortAccountRatio"
        params = {"symbol": self.symbol, "period": period, "limit": limit}
        
        async with self.session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            return []
            
    async def collect_all(self) -> Dict[str, Any]:
        """收集所有補充數據"""
        tasks = [
            self.fetch_taker_long_short_ratio(),
            self.fetch_open_interest_hist(),
            self.fetch_global_long_short_ratio(),
            self.fetch_top_long_short_ratio(),
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return {
            "taker_long_short": results[0] if not isinstance(results[0], Exception) else [],
            "open_interest_hist": results[1] if not isinstance(results[1], Exception) else [],
            "global_long_short": results[2] if not isinstance(results[2], Exception) else [],
            "top_long_short": results[3] if not isinstance(results[3], Exception) else [],
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# 整合使用示例
# ---------------------------------------------------------------------------

async def main():
    """測試爆倉瀑布偵測器"""
    
    def on_cascade_alert(alert: CascadeAlert):
        print(f"\n🚨 收到爆倉瀑布警報！")
        print(json.dumps(alert.to_dict(), indent=2, ensure_ascii=False))
        
    def on_snapshot(snapshot: CascadeSnapshot):
        # 每 5 秒打印一次快照
        pass
        
    detector = LiquidationCascadeDetector(
        symbol="BTCUSDT",
        cascade_callback=on_cascade_alert,
        snapshot_callback=on_snapshot,
    )
    
    try:
        await detector.start()
        
        # 持續運行並定期打印狀態
        while True:
            await asyncio.sleep(5)
            print(f"\r📊 {detector.render_panel().split(chr(10))[1]}", end="", flush=True)
            
    except KeyboardInterrupt:
        print("\n\n停止偵測器...")
    finally:
        await detector.stop()
        

if __name__ == "__main__":
    asyncio.run(main())
