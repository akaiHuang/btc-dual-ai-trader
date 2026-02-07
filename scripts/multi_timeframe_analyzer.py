#!/usr/bin/env python3
"""
📊 Multi-Timeframe Analyzer (MTF) v2.0
======================================

多時間框架分析模組：
🆕 v2.0: 改用 dYdX Indexer API 拉取 K 線
- 即時從 dYdX 拉取 15m/1h/4h K 線
- 計算各時間框架的趨勢方向
- 識別關鍵支撐/阻力位
- 提供趨勢對齊信號（過濾逆勢交易）

數據源: https://indexer.dydx.trade/v4

Author: AI Trading System
Updated: 2025-12-09
"""

import time
import threading
import requests
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from collections import deque
from enum import Enum
import numpy as np


class TrendDirection(Enum):
    """趨勢方向"""
    STRONG_UP = "🟢強勢上漲"
    UP = "🟢上漲"
    NEUTRAL = "⚪盤整"
    DOWN = "🔴下跌"
    STRONG_DOWN = "🔴強勢下跌"


class TimeframeSignal(Enum):
    """時間框架信號"""
    BULLISH = "多"
    BEARISH = "空"
    NEUTRAL = "中"


@dataclass
class KlineData:
    """K線數據"""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    
    @property
    def is_bullish(self) -> bool:
        return self.close > self.open
    
    @property
    def body_pct(self) -> float:
        return abs(self.close - self.open) / self.open * 100 if self.open > 0 else 0
    
    @property
    def upper_wick_pct(self) -> float:
        body_high = max(self.open, self.close)
        return (self.high - body_high) / self.open * 100 if self.open > 0 else 0
    
    @property
    def lower_wick_pct(self) -> float:
        body_low = min(self.open, self.close)
        return (body_low - self.low) / self.open * 100 if self.open > 0 else 0


@dataclass
class TimeframeAnalysis:
    """單一時間框架分析結果"""
    timeframe: str
    trend: TrendDirection
    signal: TimeframeSignal
    strength: float  # 0-100
    ema_fast: float
    ema_slow: float
    rsi: float
    atr_pct: float
    key_resistance: float
    key_support: float
    last_update: float


@dataclass
class MTFSnapshot:
    """多時間框架快照"""
    timestamp: str
    current_price: float
    
    # 各時間框架分析
    tf_1m: Optional[TimeframeAnalysis] = None
    tf_5m: Optional[TimeframeAnalysis] = None
    tf_15m: Optional[TimeframeAnalysis] = None
    tf_1h: Optional[TimeframeAnalysis] = None
    tf_4h: Optional[TimeframeAnalysis] = None
    
    # 綜合判斷
    trend_alignment: str = "N/A"  # "ALIGNED_UP" / "ALIGNED_DOWN" / "MIXED"
    alignment_score: float = 0.0  # -100 到 +100
    dominant_trend: TrendDirection = TrendDirection.NEUTRAL
    
    # 關鍵價位
    nearest_resistance: float = 0.0
    nearest_support: float = 0.0
    
    # 交易建議
    trend_filter: str = "ALLOW_ALL"  # "LONG_ONLY" / "SHORT_ONLY" / "ALLOW_ALL" / "NO_TRADE"
    filter_reason: str = ""
    
    def to_display_lines(self) -> List[str]:
        """生成顯示行"""
        lines = []
        
        # 標題
        lines.append("┌─────────────────────────────────────────────────────────┐")
        lines.append("│  📊 MTF 多時間框架分析                                    │")
        lines.append("├─────────────────────────────────────────────────────────┤")
        
        # 各時間框架
        for tf_name, tf in [("1m ", self.tf_1m), ("5m ", self.tf_5m), ("15m", self.tf_15m), ("1h ", self.tf_1h), ("4h ", self.tf_4h)]:
            if tf:
                trend_icon = self._trend_icon(tf.trend)
                signal_color = self._signal_color(tf.signal)
                lines.append(f"│  {tf_name}: {trend_icon} {tf.trend.value:<12} │ RSI:{tf.rsi:5.1f} │ {signal_color}{tf.signal.value}\033[0m │ 強度:{tf.strength:3.0f}% │")
            else:
                lines.append(f"│  {tf_name}: ⏳ 載入中...                                      │")
        
        lines.append("├─────────────────────────────────────────────────────────┤")
        
        # 趨勢對齊
        align_icon = "✅" if "ALIGNED" in self.trend_alignment else "⚠️"
        align_color = "\033[92m" if self.alignment_score > 30 else ("\033[91m" if self.alignment_score < -30 else "\033[93m")
        lines.append(f"│  趨勢對齊: {align_icon} {self.trend_alignment:<12} │ 分數: {align_color}{self.alignment_score:+.0f}\033[0m │ {self.dominant_trend.value} │")
        
        # 關鍵價位
        price_str = f"${self.current_price:,.0f}"
        resist_str = f"${self.nearest_resistance:,.0f}" if self.nearest_resistance > 0 else "N/A"
        support_str = f"${self.nearest_support:,.0f}" if self.nearest_support > 0 else "N/A"
        lines.append(f"│  支撐: {support_str:<10} │ 現價: {price_str:<10} │ 阻力: {resist_str:<10} │")
        
        # 交易過濾
        filter_icon = "🟢" if self.trend_filter == "ALLOW_ALL" else ("🔵" if "ONLY" in self.trend_filter else "🔴")
        lines.append(f"│  交易過濾: {filter_icon} {self.trend_filter:<12} │ {self.filter_reason:<25} │")
        
        lines.append("└─────────────────────────────────────────────────────────┘")
        
        return lines
    
    def _trend_icon(self, trend: TrendDirection) -> str:
        return {
            TrendDirection.STRONG_UP: "⬆️⬆️",
            TrendDirection.UP: "⬆️ ",
            TrendDirection.NEUTRAL: "➡️ ",
            TrendDirection.DOWN: "⬇️ ",
            TrendDirection.STRONG_DOWN: "⬇️⬇️",
        }.get(trend, "❓")
    
    def _signal_color(self, signal: TimeframeSignal) -> str:
        return {
            TimeframeSignal.BULLISH: "\033[92m",  # 綠色
            TimeframeSignal.BEARISH: "\033[91m",  # 紅色
            TimeframeSignal.NEUTRAL: "\033[93m",  # 黃色
        }.get(signal, "")


class MultiTimeframeAnalyzer:
    """
    多時間框架分析器
    
    🆕 v2.0: 改用 dYdX Indexer API 拉取 K 線數據
    分析多時間框架趨勢 (15m/1h/4h)
    """
    
    # 🆕 Binance Futures API (Brain Source)
    BASE_URL = "https://fapi.binance.com/fapi/v1/klines"
    
    # 時間框架配置 (Binance interval 格式)
    TIMEFRAMES = {
        "1m":  {"interval": "1m",  "lookback": 60, "update_sec": 10},
        "5m":  {"interval": "5m",  "lookback": 50, "update_sec": 30},
        "15m": {"interval": "15m", "lookback": 50, "update_sec": 60},
        "1h":  {"interval": "1h",  "lookback": 30, "update_sec": 180},
        "4h":  {"interval": "4h",  "lookback": 20, "update_sec": 600},
    }
    
    # EMA 週期
    EMA_FAST = 9
    EMA_SLOW = 21
    RSI_PERIOD = 14
    
    def __init__(self, symbol: str = "BTC-USD", enabled: bool = True):
        self.symbol = "BTCUSDT" # Binance symbol format
        self.enabled = enabled
        
        # K 線數據緩存
        self.klines: Dict[str, List[KlineData]] = {}
        self.analysis: Dict[str, TimeframeAnalysis] = {}
        
        # 更新控制
        self.last_update: Dict[str, float] = {}
        self.running = False
        self.update_thread: Optional[threading.Thread] = None
        
        # 最新快照
        self.latest_snapshot: Optional[MTFSnapshot] = None
        self.current_price: float = 0.0
        
        # 初始化
        if enabled:
            self._initial_fetch()
    
    def _initial_fetch(self):
        """初始拉取所有時間框架數據"""
        for tf in self.TIMEFRAMES:
            try:
                self._fetch_klines(tf)
                self._analyze_timeframe(tf)
            except Exception as e:
                print(f"⚠️ MTF 初始化 {tf} 失敗: {e}")
        
        # 🆕 v14.16.1: 初始化後立即生成 snapshot，避免 latest_snapshot 為 None
        if self.analysis:
            try:
                # 取得最近的收盤價作為初始價格
                if '1m' in self.klines and self.klines['1m']:
                    initial_price = self.klines['1m'][-1].close
                else:
                    initial_price = 0
                self.latest_snapshot = self._generate_snapshot(initial_price)
            except Exception as e:
                print(f"⚠️ MTF 初始 snapshot 生成失敗: {e}")
    
    def _fetch_klines(self, timeframe: str) -> bool:
        """🆕 從 Binance Futures 拉取 K 線 (Brain Source)"""
        try:
            config = self.TIMEFRAMES[timeframe]
            # Binance Klines API
            params = {
                "symbol": "BTCUSDT",
                "interval": config["interval"],
                "limit": config["lookback"]
            }
            
            # 使用 requests (同步)
            response = requests.get(self.BASE_URL, params=params, timeout=5)
            response.raise_for_status()
            raw_data = response.json()
            
            if not isinstance(raw_data, list):
                return False
                
            # Binance: [Open Time, Open, High, Low, Close, Volume, Close Time, Quote Vol, Trades, ...]
            klines = []
            for k in raw_data:
                try:
                    # Binance 使用 ms timestamp
                    timestamp = int(k[0]) 
                    
                    klines.append(KlineData(
                        timestamp=timestamp,
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[7])  # 使用 Quote Asset Volume (USDT)
                    ))
                except Exception as e:
                    continue
            
            # Binance 返回順序是舊->新，符合分析需求 (不用 reverse)
            # 但原本 dYdX 是 新->舊 reverse 變 舊->新
            # 我們這裡直接確保是 舊->新
            
            self.klines[timeframe] = klines
            self.last_update[timeframe] = time.time()
            return True
            
        except Exception as e:
            print(f"⚠️ MTF 拉取 {timeframe} 失敗 (Binance): {e}")
            return False
    
    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """計算 EMA"""
        if len(prices) < period:
            return prices[-1] if prices else 0
        
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        
        return ema
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """計算 RSI"""
        if len(prices) < period + 1:
            return 50.0
        
        changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [max(0, c) for c in changes[-period:]]
        losses = [abs(min(0, c)) for c in changes[-period:]]
        
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def _calculate_atr(self, klines: List[KlineData], period: int = 14) -> float:
        """計算 ATR (Average True Range)"""
        if len(klines) < period + 1:
            return 0
        
        true_ranges = []
        for i in range(1, len(klines)):
            high = klines[i].high
            low = klines[i].low
            prev_close = klines[i-1].close
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)
        
        return sum(true_ranges[-period:]) / period
    
    def _find_support_resistance(self, klines: List[KlineData]) -> Tuple[float, float]:
        """找出支撐和阻力位"""
        if len(klines) < 5:
            return 0, 0
        
        # 使用最近 K 線的高低點
        highs = [k.high for k in klines[-20:]]
        lows = [k.low for k in klines[-20:]]
        
        # 找出明顯的高點和低點（作為阻力和支撐）
        resistance = max(highs)
        support = min(lows)
        
        # 找出多次測試的價位
        current_price = klines[-1].close
        
        # 在當前價格上方找最近的阻力
        upper_levels = sorted([h for h in highs if h > current_price])
        if upper_levels:
            resistance = upper_levels[0]
        
        # 在當前價格下方找最近的支撐
        lower_levels = sorted([l for l in lows if l < current_price], reverse=True)
        if lower_levels:
            support = lower_levels[0]
        
        return support, resistance
    
    def _analyze_timeframe(self, timeframe: str) -> Optional[TimeframeAnalysis]:
        """分析單一時間框架"""
        klines = self.klines.get(timeframe, [])
        if len(klines) < 20:
            return None
        
        # 收盤價序列
        closes = [k.close for k in klines]
        current_price = closes[-1]
        
        # EMA
        ema_fast = self._calculate_ema(closes, self.EMA_FAST)
        ema_slow = self._calculate_ema(closes, self.EMA_SLOW)
        
        # RSI
        rsi = self._calculate_rsi(closes, self.RSI_PERIOD)
        
        # ATR
        atr = self._calculate_atr(klines)
        atr_pct = (atr / current_price * 100) if current_price > 0 else 0
        
        # 支撐/阻力
        support, resistance = self._find_support_resistance(klines)
        
        # 趨勢判斷
        ema_diff_pct = (ema_fast - ema_slow) / ema_slow * 100 if ema_slow > 0 else 0
        price_vs_ema = (current_price - ema_slow) / ema_slow * 100 if ema_slow > 0 else 0
        
        # 最近 5 根 K 線的動量
        recent_momentum = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] > 0 else 0
        
        # 綜合判斷趨勢
        if ema_diff_pct > 0.5 and rsi > 55 and recent_momentum > 0.5:
            trend = TrendDirection.STRONG_UP
            signal = TimeframeSignal.BULLISH
            strength = min(100, 50 + ema_diff_pct * 10 + (rsi - 50))
        elif ema_diff_pct > 0.1 and price_vs_ema > 0:
            trend = TrendDirection.UP
            signal = TimeframeSignal.BULLISH
            strength = min(80, 40 + ema_diff_pct * 10)
        elif ema_diff_pct < -0.5 and rsi < 45 and recent_momentum < -0.5:
            trend = TrendDirection.STRONG_DOWN
            signal = TimeframeSignal.BEARISH
            strength = min(100, 50 + abs(ema_diff_pct) * 10 + (50 - rsi))
        elif ema_diff_pct < -0.1 and price_vs_ema < 0:
            trend = TrendDirection.DOWN
            signal = TimeframeSignal.BEARISH
            strength = min(80, 40 + abs(ema_diff_pct) * 10)
        else:
            trend = TrendDirection.NEUTRAL
            signal = TimeframeSignal.NEUTRAL
            strength = 30
        
        analysis = TimeframeAnalysis(
            timeframe=timeframe,
            trend=trend,
            signal=signal,
            strength=strength,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rsi=rsi,
            atr_pct=atr_pct,
            key_resistance=resistance,
            key_support=support,
            last_update=time.time()
        )
        
        self.analysis[timeframe] = analysis
        return analysis
    
    def update(self, current_price: float) -> MTFSnapshot:
        """
        更新分析（根據各時間框架的更新頻率）
        
        Args:
            current_price: 當前價格
            
        Returns:
            MTFSnapshot: 最新快照
        """
        self.current_price = current_price
        now = time.time()
        
        # 檢查是否需要更新各時間框架
        for tf, config in self.TIMEFRAMES.items():
            last = self.last_update.get(tf, 0)
            if now - last >= config["update_sec"]:
                if self._fetch_klines(tf):
                    self._analyze_timeframe(tf)
        
        # 生成快照
        snapshot = self._generate_snapshot(current_price)
        self.latest_snapshot = snapshot
        return snapshot
    
    def _generate_snapshot(self, current_price: float) -> MTFSnapshot:
        """生成 MTF 快照"""
        tf_1m = self.analysis.get("1m")
        tf_5m = self.analysis.get("5m")
        tf_15m = self.analysis.get("15m")
        tf_1h = self.analysis.get("1h")
        tf_4h = self.analysis.get("4h")
        
        # 計算趨勢對齊分數 (-100 到 +100)
        # 權重調整: 1m(1), 5m(1), 15m(1), 1h(2), 4h(3) -> 總分 120
        alignment_score = 0
        signals = []
        
        tf_weights = [
            (tf_1m, 1), (tf_5m, 1), (tf_15m, 1), 
            (tf_1h, 2), (tf_4h, 3)
        ]
        
        for tf, weight in tf_weights:
            if tf:
                if tf.signal == TimeframeSignal.BULLISH:
                    alignment_score += weight * 15
                elif tf.signal == TimeframeSignal.BEARISH:
                    alignment_score -= weight * 15
        
        # 趨勢對齊判斷 (分數範圍擴大，標準稍微放寬)
        if alignment_score >= 60:
            trend_alignment = "ALIGNED_UP"
            dominant_trend = TrendDirection.STRONG_UP if alignment_score >= 90 else TrendDirection.UP
        elif alignment_score <= -60:
            trend_alignment = "ALIGNED_DOWN"
            dominant_trend = TrendDirection.STRONG_DOWN if alignment_score <= -90 else TrendDirection.DOWN
        else:
            trend_alignment = "MIXED"
            dominant_trend = TrendDirection.NEUTRAL
        
        # 關鍵價位（取最近的支撐/阻力）
        # 包含 1m/5m 的短線支撐阻力
        active_tfs = [t for t in [tf_1m, tf_5m, tf_15m, tf_1h, tf_4h] if t]
        
        resistances = [tf.key_resistance for tf in active_tfs if tf.key_resistance > current_price]
        supports = [tf.key_support for tf in active_tfs if tf.key_support < current_price]
        
        nearest_resistance = min(resistances) if resistances else 0
        nearest_support = max(supports) if supports else 0
        
        # 交易過濾建議
        trend_filter = "ALLOW_ALL"
        filter_reason = ""
        
        if alignment_score >= 50:
            trend_filter = "LONG_ONLY"
            filter_reason = "多時間框架看多，僅做多"
        elif alignment_score <= -50:
            trend_filter = "SHORT_ONLY"
            filter_reason = "多時間框架看空，僅做空"
        elif all(tf and tf.signal == TimeframeSignal.NEUTRAL for tf in [tf_1h, tf_4h]):
            trend_filter = "ALLOW_ALL"
            filter_reason = "大週期盤整，可雙向"
        else:
            trend_filter = "ALLOW_ALL"
            filter_reason = "趨勢混合，謹慎交易"
        
        # 如果價格接近關鍵位，給予提示
        if nearest_resistance > 0:
            dist_to_resist = (nearest_resistance - current_price) / current_price * 100
            if dist_to_resist < 0.2: # 短線更敏感
                filter_reason = f"⚠️ 接近阻力 ${nearest_resistance:,.0f}"
        
        if nearest_support > 0:
            dist_to_support = (current_price - nearest_support) / current_price * 100
            if dist_to_support < 0.2:
                filter_reason = f"⚠️ 接近支撐 ${nearest_support:,.0f}"
        
        return MTFSnapshot(
            timestamp=datetime.now().isoformat(),
            current_price=current_price,
            tf_1m=tf_1m,
            tf_5m=tf_5m,
            tf_15m=tf_15m,
            tf_1h=tf_1h,
            tf_4h=tf_4h,
            trend_alignment=trend_alignment,
            alignment_score=alignment_score,
            dominant_trend=dominant_trend,
            nearest_resistance=nearest_resistance,
            nearest_support=nearest_support,
            trend_filter=trend_filter,
            filter_reason=filter_reason
        )
    
    def get_trade_filter(self, direction: str) -> Tuple[bool, str]:
        """
        檢查交易是否符合 MTF 過濾
        
        Args:
            direction: "LONG" 或 "SHORT"
            
        Returns:
            (allowed, reason): 是否允許交易，原因
        """
        if not self.enabled or not self.latest_snapshot:
            return True, "MTF 未啟用"
        
        snapshot = self.latest_snapshot
        
        if snapshot.trend_filter == "LONG_ONLY" and direction == "SHORT":
            return False, f"MTF 過濾: 多時間框架看多 ({snapshot.alignment_score:+.0f})，禁止做空"
        
        if snapshot.trend_filter == "SHORT_ONLY" and direction == "LONG":
            return False, f"MTF 過濾: 多時間框架看空 ({snapshot.alignment_score:+.0f})，禁止做多"
        
        return True, "MTF 允許"
    
    def get_display_lines(self) -> List[str]:
        """獲取顯示行"""
        if not self.enabled:
            return ["│ 📊 MTF: 未啟用                                              │"]
        
        if not self.latest_snapshot:
            return ["│ 📊 MTF: 載入中...                                            │"]
        
        return self.latest_snapshot.to_display_lines()
    
    def get_compact_status(self) -> str:
        """獲取單行狀態"""
        if not self.enabled or not self.latest_snapshot:
            return "MTF: N/A"
        
        s = self.latest_snapshot
        
        # 各時間框架簡要
        tf_status = []
        for tf_name, tf in [("15m", s.tf_15m), ("1h", s.tf_1h), ("4h", s.tf_4h)]:
            if tf:
                icon = "🟢" if tf.signal == TimeframeSignal.BULLISH else ("🔴" if tf.signal == TimeframeSignal.BEARISH else "⚪")
                tf_status.append(f"{tf_name}{icon}")
            else:
                tf_status.append(f"{tf_name}?")
        
        align_icon = "✅" if "ALIGNED" in s.trend_alignment else "⚠️"
        
        return f"MTF: {' '.join(tf_status)} | {align_icon} {s.alignment_score:+.0f} | {s.trend_filter}"
    
    # ============================================================
    # 🆕 v2.0 K 線預測與交易決策
    # ============================================================
    
    def predict_next_candle(self, timeframe: str = "5m") -> Dict[str, Any]:
        """
        🆕 v2.0 預測下一根 K 線方向
        
        基於多種技術指標預測下一根 K 線的方向:
        1. EMA 趨勢
        2. RSI 動量
        3. 最近 K 線形態
        4. 成交量變化
        5. 多時間框架對齊
        
        Returns:
            Dict: {
                'direction': 'LONG' | 'SHORT' | 'NEUTRAL',
                'confidence': 0-100,
                'predicted_high': float,
                'predicted_low': float,
                'entry_price': float,
                'take_profit': float,
                'stop_loss': float,
                'reasons': List[str]
            }
        """
        result = {
            'direction': 'NEUTRAL',
            'confidence': 0,
            'predicted_high': 0,
            'predicted_low': 0,
            'entry_price': 0,
            'take_profit': 0,
            'stop_loss': 0,
            'reasons': [],
            'add_position_signal': False,
            'close_position_signal': False,
        }
        
        # 取得 K 線數據
        klines = self.klines.get(timeframe) or self.klines.get("15m", [])
        if len(klines) < 20:
            result['reasons'].append("K線數據不足")
            return result
        
        closes = [k.close for k in klines]
        current_price = closes[-1]
        
        # 計算指標
        ema_fast = self._calculate_ema(closes, self.EMA_FAST)
        ema_slow = self._calculate_ema(closes, self.EMA_SLOW)
        rsi = self._calculate_rsi(closes, self.RSI_PERIOD)
        atr = self._calculate_atr(klines)
        
        # 評分系統
        bullish_score = 0
        bearish_score = 0
        reasons = []
        
        # 1. EMA 趨勢 (權重 30%)
        ema_diff_pct = (ema_fast - ema_slow) / ema_slow * 100 if ema_slow > 0 else 0
        if ema_diff_pct > 0.1:
            bullish_score += 30
            reasons.append(f"EMA 多頭排列 ({ema_diff_pct:+.2f}%)")
        elif ema_diff_pct < -0.1:
            bearish_score += 30
            reasons.append(f"EMA 空頭排列 ({ema_diff_pct:+.2f}%)")
        
        # 2. RSI 動量 (權重 25%)
        if rsi > 55:
            bullish_score += min(25, (rsi - 50) * 0.5)
            reasons.append(f"RSI 偏多 ({rsi:.1f})")
        elif rsi < 45:
            bearish_score += min(25, (50 - rsi) * 0.5)
            reasons.append(f"RSI 偏空 ({rsi:.1f})")
        
        # 3. 最近 K 線形態 (權重 20%)
        recent_candles = klines[-5:]
        bullish_candles = sum(1 for k in recent_candles if k.is_bullish)
        if bullish_candles >= 4:
            bullish_score += 20
            reasons.append(f"近5根K線 {bullish_candles} 根陽線")
        elif bullish_candles <= 1:
            bearish_score += 20
            reasons.append(f"近5根K線 {5-bullish_candles} 根陰線")
        
        # 4. 價格位置 vs EMA (權重 15%)
        price_vs_ema = (current_price - ema_slow) / ema_slow * 100
        if price_vs_ema > 0.1:
            bullish_score += 15
            reasons.append(f"價格在 EMA 上方 ({price_vs_ema:+.2f}%)")
        elif price_vs_ema < -0.1:
            bearish_score += 15
            reasons.append(f"價格在 EMA 下方 ({price_vs_ema:+.2f}%)")
        
        # 5. 多時間框架對齊 (權重 10%)
        if self.latest_snapshot:
            align_score = self.latest_snapshot.alignment_score
            if align_score > 30:
                bullish_score += 10
                reasons.append(f"MTF 多頭對齊 ({align_score:+.0f})")
            elif align_score < -30:
                bearish_score += 10
                reasons.append(f"MTF 空頭對齊 ({align_score:+.0f})")
        
        # 計算預測方向和信心度
        total_score = bullish_score + bearish_score
        if bullish_score > bearish_score + 15:
            result['direction'] = 'LONG'
            result['confidence'] = min(95, bullish_score)
        elif bearish_score > bullish_score + 15:
            result['direction'] = 'SHORT'
            result['confidence'] = min(95, bearish_score)
        else:
            result['direction'] = 'NEUTRAL'
            result['confidence'] = 50 - abs(bullish_score - bearish_score)
        
        result['reasons'] = reasons
        
        # 預測價格範圍 (基於 ATR)
        atr_multiplier = 1.5  # 預測範圍約 1.5 ATR
        result['predicted_high'] = current_price + atr * atr_multiplier
        result['predicted_low'] = current_price - atr * atr_multiplier
        
        # 計算建議價格
        if result['direction'] == 'LONG':
            result['entry_price'] = current_price  # 市價進場
            result['take_profit'] = current_price + atr * 2  # 2 ATR 止盈
            result['stop_loss'] = current_price - atr * 1.5  # 1.5 ATR 止損
        elif result['direction'] == 'SHORT':
            result['entry_price'] = current_price
            result['take_profit'] = current_price - atr * 2
            result['stop_loss'] = current_price + atr * 1.5
        
        return result
    
    def get_position_management_signal(self, position_direction: str, entry_price: float, current_price: float) -> Dict[str, Any]:
        """
        🆕 v2.0 持倉管理信號
        
        判斷是否應該加倉或平倉
        
        Args:
            position_direction: 'LONG' 或 'SHORT'
            entry_price: 進場價格
            current_price: 當前價格
            
        Returns:
            Dict: {
                'action': 'HOLD' | 'ADD' | 'REDUCE' | 'CLOSE',
                'reason': str,
                'confidence': 0-100,
                'suggested_size_pct': float  # 建議操作倉位百分比
            }
        """
        result = {
            'action': 'HOLD',
            'reason': '持續觀察',
            'confidence': 50,
            'suggested_size_pct': 0
        }
        
        if not self.latest_snapshot:
            return result
        
        # 計算當前盈虧
        if position_direction == 'LONG':
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100
        
        snapshot = self.latest_snapshot
        prediction = self.predict_next_candle()
        
        # 獲取 1h 時間框架 RSI
        tf_1h = snapshot.tf_1h
        rsi_1h = tf_1h.rsi if tf_1h else 50
        
        # ═══════════════════════════════════════════════════════════
        # 加倉信號
        # ═══════════════════════════════════════════════════════════
        
        # 條件1: 方向對齊 + 小幅回調 + 預測方向一致
        if pnl_pct > 0 and pnl_pct < 3:  # 有獲利但還不多
            if prediction['direction'] == position_direction:
                if prediction['confidence'] >= 70:
                    result['action'] = 'ADD'
                    result['reason'] = f"趨勢延續確認 ({prediction['confidence']}% 信心)"
                    result['confidence'] = prediction['confidence']
                    result['suggested_size_pct'] = 30  # 加倉 30%
                    return result
        
        # 條件2: 突破關鍵阻力/支撐
        if position_direction == 'LONG' and current_price > snapshot.nearest_resistance:
            if snapshot.nearest_resistance > 0:
                result['action'] = 'ADD'
                result['reason'] = f"突破阻力 ${snapshot.nearest_resistance:,.0f}"
                result['confidence'] = 75
                result['suggested_size_pct'] = 25
                return result
        
        if position_direction == 'SHORT' and current_price < snapshot.nearest_support:
            if snapshot.nearest_support > 0:
                result['action'] = 'ADD'
                result['reason'] = f"跌破支撐 ${snapshot.nearest_support:,.0f}"
                result['confidence'] = 75
                result['suggested_size_pct'] = 25
                return result
        
        # ═══════════════════════════════════════════════════════════
        # 減倉/平倉信號
        # ═══════════════════════════════════════════════════════════
        
        # 條件1: 方向反轉
        if prediction['direction'] != position_direction and prediction['direction'] != 'NEUTRAL':
            if prediction['confidence'] >= 60:
                result['action'] = 'REDUCE'
                result['reason'] = f"預測方向反轉 → {prediction['direction']}"
                result['confidence'] = prediction['confidence']
                result['suggested_size_pct'] = 50  # 減倉 50%
                return result
        
        # 條件2: RSI 超買/超賣
        if position_direction == 'LONG' and rsi_1h > 70:
            result['action'] = 'REDUCE'
            result['reason'] = f"RSI 超買 ({rsi_1h:.1f})，考慮獲利了結"
            result['confidence'] = 60
            result['suggested_size_pct'] = 30
            return result
        
        if position_direction == 'SHORT' and rsi_1h < 30:
            result['action'] = 'REDUCE'
            result['reason'] = f"RSI 超賣 ({rsi_1h:.1f})，考慮獲利了結"
            result['confidence'] = 60
            result['suggested_size_pct'] = 30
            return result
        
        # 條件3: 接近關鍵價位
        if position_direction == 'LONG' and snapshot.nearest_resistance > 0:
            dist_to_resist = (snapshot.nearest_resistance - current_price) / current_price * 100
            if dist_to_resist < 0.2 and pnl_pct > 2:
                result['action'] = 'REDUCE'
                result['reason'] = f"接近阻力 ${snapshot.nearest_resistance:,.0f}"
                result['confidence'] = 65
                result['suggested_size_pct'] = 40
                return result
        
        if position_direction == 'SHORT' and snapshot.nearest_support > 0:
            dist_to_support = (current_price - snapshot.nearest_support) / current_price * 100
            if dist_to_support < 0.2 and pnl_pct > 2:
                result['action'] = 'REDUCE'
                result['reason'] = f"接近支撐 ${snapshot.nearest_support:,.0f}"
                result['confidence'] = 65
                result['suggested_size_pct'] = 40
                return result
        
        # 條件4: MTF 強烈反向
        if position_direction == 'LONG' and snapshot.alignment_score < -50:
            result['action'] = 'CLOSE'
            result['reason'] = f"MTF 強烈看空 ({snapshot.alignment_score:+.0f})"
            result['confidence'] = 80
            result['suggested_size_pct'] = 100
            return result
        
        if position_direction == 'SHORT' and snapshot.alignment_score > 50:
            result['action'] = 'CLOSE'
            result['reason'] = f"MTF 強烈看多 ({snapshot.alignment_score:+.0f})"
            result['confidence'] = 80
            result['suggested_size_pct'] = 100
            return result
        
        return result
    
    def get_prediction_display(self) -> List[str]:
        """獲取預測顯示行"""
        pred = self.predict_next_candle()
        
        lines = []
        lines.append("┌─────────────────────────────────────────────────────────┐")
        lines.append("│  🔮 K線預測 (5分鐘)                                       │")
        lines.append("├─────────────────────────────────────────────────────────┤")
        
        # 方向和信心度
        dir_icon = "🟢 做多" if pred['direction'] == 'LONG' else ("🔴 做空" if pred['direction'] == 'SHORT' else "⚪ 觀望")
        conf_bar = "█" * int(pred['confidence'] / 10) + "░" * (10 - int(pred['confidence'] / 10))
        lines.append(f"│  方向: {dir_icon:<8} │ 信心度: [{conf_bar}] {pred['confidence']:.0f}% │")
        
        # 預測價格
        if pred['predicted_high'] > 0:
            lines.append(f"│  預測高: ${pred['predicted_high']:,.2f} │ 預測低: ${pred['predicted_low']:,.2f} │")
        
        # 建議價格
        if pred['entry_price'] > 0:
            lines.append(f"│  進場: ${pred['entry_price']:,.2f} │ 止盈: ${pred['take_profit']:,.2f} │ 止損: ${pred['stop_loss']:,.2f} │")
        
        # 原因
        lines.append("├─────────────────────────────────────────────────────────┤")
        for reason in pred['reasons'][:3]:
            lines.append(f"│  • {reason:<50} │")
        
        lines.append("└─────────────────────────────────────────────────────────┘")
        
        return lines


# ============================================================
# 獨立測試
# ============================================================

if __name__ == "__main__":
    print("📊 Multi-Timeframe Analyzer 測試")
    print("=" * 60)
    
    mtf = MultiTimeframeAnalyzer(symbol="BTCUSDT", enabled=True)
    
    # 模擬當前價格
    test_price = 96500.0
    
    # 更新分析
    snapshot = mtf.update(test_price)
    
    # 顯示結果
    print("\n".join(mtf.get_display_lines()))
    
    print("\n" + "=" * 60)
    print(f"單行狀態: {mtf.get_compact_status()}")
    
    # 測試過濾
    print("\n交易過濾測試:")
    for direction in ["LONG", "SHORT"]:
        allowed, reason = mtf.get_trade_filter(direction)
        status = "✅ 允許" if allowed else "❌ 禁止"
        print(f"  {direction}: {status} - {reason}")
