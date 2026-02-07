#!/usr/bin/env python3
"""
MVP Strategy v1.0 - Baseline Strategy
=====================================

最小可行產品策略：固定規則、無AI優化
用作 Phase 1 baseline 基準線

策略邏輯：
- 多頭進場：MA7 > MA25 AND RSI ∈ [40, 70] AND Volume > MA(20)
- 空頭進場：MA7 < MA25 AND RSI ∈ [30, 60] AND Volume > MA(20)
- 固定止盈：0.5%
- 固定止損：0.25%
- 時間止損：30 分鐘

作者: Phase 1 Baseline
日期: 2025-11-14
"""

import numpy as np
import pandas as pd
import talib
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass


@dataclass
class SignalResult:
    """信號結果"""
    direction: Optional[str]  # "LONG", "SHORT", None
    entry_price: float
    take_profit_price: float
    stop_loss_price: float
    confidence: float  # 固定為 1.0（無AI優化）
    reason: str
    indicators: Dict  # 當前指標值
    timestamp: datetime


class MVPStrategyV1:
    """
    MVP 策略 v1.0
    
    特點：
    1. 完全固定的進場/出場規則
    2. 無任何機器學習或優化
    3. 簡單易懂，方便驗證
    4. 用作後續優化的基準線
    """
    
    def __init__(
        self,
        ma_short: int = 7,
        ma_long: int = 25,
        rsi_period: int = 14,
        volume_ma_period: int = 20,
        long_rsi_lower: float = 40.0,
        long_rsi_upper: float = 70.0,
        short_rsi_lower: float = 30.0,
        short_rsi_upper: float = 60.0,
        take_profit_percent: float = 0.005,  # 0.5%
        stop_loss_percent: float = 0.0025,   # 0.25%
        time_stop_minutes: int = 30
    ):
        """
        初始化 MVP 策略
        
        Args:
            ma_short: 短期均線週期
            ma_long: 長期均線週期
            rsi_period: RSI 週期
            volume_ma_period: 成交量均線週期
            long_rsi_lower: 多頭 RSI 下限
            long_rsi_upper: 多頭 RSI 上限
            short_rsi_lower: 空頭 RSI 下限
            short_rsi_upper: 空頭 RSI 上限
            take_profit_percent: 止盈百分比
            stop_loss_percent: 止損百分比
            time_stop_minutes: 時間止損（分鐘）
        """
        self.ma_short = ma_short
        self.ma_long = ma_long
        self.rsi_period = rsi_period
        self.volume_ma_period = volume_ma_period
        
        self.long_rsi_lower = long_rsi_lower
        self.long_rsi_upper = long_rsi_upper
        self.short_rsi_lower = short_rsi_lower
        self.short_rsi_upper = short_rsi_upper
        
        self.take_profit_percent = take_profit_percent
        self.stop_loss_percent = stop_loss_percent
        self.time_stop_minutes = time_stop_minutes
        
        # 統計
        self.total_signals = 0
        self.long_signals = 0
        self.short_signals = 0
    
    def calculate_indicators(
        self,
        df: pd.DataFrame
    ) -> Dict:
        """
        計算技術指標
        
        Args:
            df: OHLCV DataFrame，需包含 ['close', 'volume'] 欄位
            
        Returns:
            指標字典
        """
        if len(df) < max(self.ma_long, self.rsi_period, self.volume_ma_period):
            return {}
        
        close = df['close'].values
        volume = df['volume'].values
        
        # 移動平均線
        ma_short = talib.SMA(close, timeperiod=self.ma_short)
        ma_long = talib.SMA(close, timeperiod=self.ma_long)
        
        # RSI
        rsi = talib.RSI(close, timeperiod=self.rsi_period)
        
        # 成交量均線
        volume_ma = talib.SMA(volume, timeperiod=self.volume_ma_period)
        
        return {
            'ma_short': ma_short[-1],
            'ma_long': ma_long[-1],
            'rsi': rsi[-1],
            'volume': volume[-1],
            'volume_ma': volume_ma[-1],
            'current_price': close[-1]
        }
    
    def generate_signal(
        self,
        df: pd.DataFrame,
        current_time: Optional[datetime] = None
    ) -> SignalResult:
        """
        生成交易信號
        
        Args:
            df: OHLCV DataFrame
            current_time: 當前時間（用於時間戳）
            
        Returns:
            SignalResult 對象
        """
        if current_time is None:
            current_time = datetime.now()
        
        # 計算指標
        indicators = self.calculate_indicators(df)
        
        if not indicators:
            return SignalResult(
                direction=None,
                entry_price=df['close'].iloc[-1],
                take_profit_price=0.0,
                stop_loss_price=0.0,
                confidence=0.0,
                reason="數據不足，無法計算指標",
                indicators={},
                timestamp=current_time
            )
        
        entry_price = indicators['current_price']
        ma_short = indicators['ma_short']
        ma_long = indicators['ma_long']
        rsi = indicators['rsi']
        volume = indicators['volume']
        volume_ma = indicators['volume_ma']
        
        # 檢查多頭條件
        long_ma_condition = ma_short > ma_long
        long_rsi_condition = self.long_rsi_lower <= rsi <= self.long_rsi_upper
        volume_condition = volume > volume_ma
        
        # 檢查空頭條件
        short_ma_condition = ma_short < ma_long
        short_rsi_condition = self.short_rsi_lower <= rsi <= self.short_rsi_upper
        
        direction = None
        reason_parts = []
        
        # 多頭信號
        if long_ma_condition and long_rsi_condition and volume_condition:
            direction = "LONG"
            self.long_signals += 1
            reason_parts.append(f"MA7({ma_short:.2f}) > MA25({ma_long:.2f})")
            reason_parts.append(f"RSI({rsi:.2f}) ∈ [{self.long_rsi_lower}, {self.long_rsi_upper}]")
            reason_parts.append(f"Volume({volume:.0f}) > MA({volume_ma:.0f})")
        
        # 空頭信號
        elif short_ma_condition and short_rsi_condition and volume_condition:
            direction = "SHORT"
            self.short_signals += 1
            reason_parts.append(f"MA7({ma_short:.2f}) < MA25({ma_long:.2f})")
            reason_parts.append(f"RSI({rsi:.2f}) ∈ [{self.short_rsi_lower}, {self.short_rsi_upper}]")
            reason_parts.append(f"Volume({volume:.0f}) > MA({volume_ma:.0f})")
        
        # 無信號
        else:
            reason_parts.append("條件不滿足")
            if not volume_condition:
                reason_parts.append(f"成交量不足 ({volume:.0f} <= {volume_ma:.0f})")
            if not long_ma_condition and not short_ma_condition:
                reason_parts.append(f"MA 趨勢不明確")
            if not long_rsi_condition and not short_rsi_condition:
                reason_parts.append(f"RSI({rsi:.2f}) 超出範圍")
        
        # 計算止盈/止損
        if direction == "LONG":
            take_profit_price = entry_price * (1 + self.take_profit_percent)
            stop_loss_price = entry_price * (1 - self.stop_loss_percent)
        elif direction == "SHORT":
            take_profit_price = entry_price * (1 - self.take_profit_percent)
            stop_loss_price = entry_price * (1 + self.stop_loss_percent)
        else:
            take_profit_price = 0.0
            stop_loss_price = 0.0
        
        self.total_signals += 1
        
        return SignalResult(
            direction=direction,
            entry_price=entry_price,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
            confidence=1.0 if direction else 0.0,
            reason=" | ".join(reason_parts),
            indicators=indicators,
            timestamp=current_time
        )
    
    def get_time_stop(self, entry_time: datetime) -> datetime:
        """
        計算時間止損
        
        Args:
            entry_time: 進場時間
            
        Returns:
            時間止損時刻
        """
        return entry_time + timedelta(minutes=self.time_stop_minutes)
    
    def get_statistics(self) -> Dict:
        """
        獲取策略統計
        
        Returns:
            統計字典
        """
        return {
            'total_signals': self.total_signals,
            'long_signals': self.long_signals,
            'short_signals': self.short_signals,
            'long_ratio': self.long_signals / max(1, self.total_signals),
            'parameters': {
                'ma_short': self.ma_short,
                'ma_long': self.ma_long,
                'rsi_period': self.rsi_period,
                'volume_ma_period': self.volume_ma_period,
                'take_profit_percent': self.take_profit_percent,
                'stop_loss_percent': self.stop_loss_percent,
                'time_stop_minutes': self.time_stop_minutes
            }
        }


def test_mvp_strategy():
    """測試 MVP 策略"""
    print("=" * 60)
    print("MVP Strategy v1.0 測試")
    print("=" * 60)
    
    # 創建測試數據
    np.random.seed(42)
    n = 100
    prices = 50000 + np.cumsum(np.random.randn(n) * 100)
    volumes = np.random.uniform(100, 200, n)
    
    df = pd.DataFrame({
        'close': prices,
        'volume': volumes
    })
    
    # 初始化策略
    strategy = MVPStrategyV1()
    
    # 測試 1：多頭信號
    print("\n【測試 1：多頭信號】")
    df_long = df.copy()
    df_long['close'].iloc[-10:] += 500  # 製造上漲趨勢
    signal = strategy.generate_signal(df_long)
    print(f"方向: {signal.direction}")
    print(f"進場價: {signal.entry_price:.2f}")
    print(f"止盈價: {signal.take_profit_price:.2f} (+{strategy.take_profit_percent:.2%})")
    print(f"止損價: {signal.stop_loss_price:.2f} (-{strategy.stop_loss_percent:.2%})")
    print(f"原因: {signal.reason}")
    print(f"指標: MA7={signal.indicators.get('ma_short', 0):.2f}, MA25={signal.indicators.get('ma_long', 0):.2f}, RSI={signal.indicators.get('rsi', 0):.2f}")
    
    # 測試 2：空頭信號
    print("\n【測試 2：空頭信號】")
    df_short = df.copy()
    df_short['close'].iloc[-10:] -= 500  # 製造下跌趨勢
    signal = strategy.generate_signal(df_short)
    print(f"方向: {signal.direction}")
    print(f"進場價: {signal.entry_price:.2f}")
    print(f"止盈價: {signal.take_profit_price:.2f} (-{strategy.take_profit_percent:.2%})")
    print(f"止損價: {signal.stop_loss_price:.2f} (+{strategy.stop_loss_percent:.2%})")
    print(f"原因: {signal.reason}")
    
    # 測試 3：無信號
    print("\n【測試 3：無信號（盤整）】")
    df_neutral = df.copy()
    df_neutral['close'].iloc[-10:] = df_neutral['close'].iloc[-10].mean()  # 橫盤
    signal = strategy.generate_signal(df_neutral)
    print(f"方向: {signal.direction}")
    print(f"原因: {signal.reason}")
    
    # 統計
    print("\n【策略統計】")
    stats = strategy.get_statistics()
    print(f"總信號數: {stats['total_signals']}")
    print(f"多頭信號: {stats['long_signals']}")
    print(f"空頭信號: {stats['short_signals']}")
    print(f"多頭比例: {stats['long_ratio']:.1%}")
    
    print("\n✅ 測試完成")


if __name__ == "__main__":
    test_mvp_strategy()
