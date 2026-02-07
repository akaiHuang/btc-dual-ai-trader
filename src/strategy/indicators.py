"""
技術指標模組
封裝 TA-Lib 指標並提供交易信號判斷邏輯
"""

import talib
import numpy as np
import pandas as pd
from typing import Tuple, Optional, Dict, Any
from enum import Enum


class Signal(Enum):
    """交易信號枚舉"""
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class TechnicalIndicators:
    """技術指標計算與信號生成"""
    
    def __init__(self):
        """初始化"""
        self.version = talib.__version__
    
    # ==================== RSI (相對強弱指標) ====================
    
    def calculate_rsi(
        self,
        close: np.ndarray,
        period: int = 14
    ) -> np.ndarray:
        """
        計算 RSI 指標
        
        Args:
            close: 收盤價序列
            period: RSI 週期，預設 14
            
        Returns:
            RSI 值序列 (0-100)
        """
        return talib.RSI(close, timeperiod=period)
    
    def rsi_signal(
        self,
        rsi: float,
        oversold: float = 30,
        overbought: float = 70
    ) -> Signal:
        """
        根據 RSI 值判斷交易信號
        
        Args:
            rsi: RSI 當前值
            oversold: 超賣閾值，預設 30
            overbought: 超買閾值，預設 70
            
        Returns:
            交易信號
        """
        if np.isnan(rsi):
            return Signal.NEUTRAL
        
        if rsi < 20:
            return Signal.STRONG_BUY  # 極度超賣
        elif rsi < oversold:
            return Signal.BUY  # 超賣
        elif rsi > 80:
            return Signal.STRONG_SELL  # 極度超買
        elif rsi > overbought:
            return Signal.SELL  # 超買
        else:
            return Signal.NEUTRAL  # 中性
    
    # ==================== MA (移動平均線) ====================
    
    def calculate_ma(
        self,
        close: np.ndarray,
        period: int = 20,
        ma_type: str = "SMA"
    ) -> np.ndarray:
        """
        計算移動平均線
        
        Args:
            close: 收盤價序列
            period: MA 週期
            ma_type: MA 類型 (SMA/EMA/WMA)
            
        Returns:
            MA 值序列
        """
        if ma_type == "SMA":
            return talib.SMA(close, timeperiod=period)
        elif ma_type == "EMA":
            return talib.EMA(close, timeperiod=period)
        elif ma_type == "WMA":
            return talib.WMA(close, timeperiod=period)
        else:
            raise ValueError(f"不支援的 MA 類型: {ma_type}")
    
    def ma_crossover_signal(
        self,
        short_ma: float,
        long_ma: float,
        prev_short_ma: float,
        prev_long_ma: float
    ) -> Signal:
        """
        根據 MA 交叉判斷信號
        
        Args:
            short_ma: 短週期 MA 當前值
            long_ma: 長週期 MA 當前值
            prev_short_ma: 短週期 MA 前一值
            prev_long_ma: 長週期 MA 前一值
            
        Returns:
            交易信號
        """
        if any(np.isnan([short_ma, long_ma, prev_short_ma, prev_long_ma])):
            return Signal.NEUTRAL
        
        # 黃金交叉 (Golden Cross)
        if prev_short_ma <= prev_long_ma and short_ma > long_ma:
            return Signal.BUY
        
        # 死亡交叉 (Death Cross)
        if prev_short_ma >= prev_long_ma and short_ma < long_ma:
            return Signal.SELL
        
        # 維持趨勢
        if short_ma > long_ma:
            return Signal.NEUTRAL  # 多頭趨勢但無交叉
        else:
            return Signal.NEUTRAL  # 空頭趨勢但無交叉
    
    # ==================== BOLL (布林通道) ====================
    
    def calculate_bollinger_bands(
        self,
        close: np.ndarray,
        period: int = 20,
        nbdevup: float = 2.0,
        nbdevdn: float = 2.0
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        計算布林通道
        
        Args:
            close: 收盤價序列
            period: 週期，預設 20
            nbdevup: 上軌標準差倍數
            nbdevdn: 下軌標準差倍數
            
        Returns:
            (上軌, 中軌, 下軌)
        """
        upper, middle, lower = talib.BBANDS(
            close,
            timeperiod=period,
            nbdevup=nbdevup,
            nbdevdn=nbdevdn,
            matype=0  # SMA
        )
        return upper, middle, lower
    
    def bollinger_signal(
        self,
        price: float,
        upper: float,
        middle: float,
        lower: float
    ) -> Signal:
        """
        根據布林通道判斷信號
        
        Args:
            price: 當前價格
            upper: 上軌
            middle: 中軌
            lower: 下軌
            
        Returns:
            交易信號
        """
        if any(np.isnan([price, upper, middle, lower])):
            return Signal.NEUTRAL
        
        bandwidth = upper - lower
        upper_distance = (upper - price) / bandwidth
        lower_distance = (price - lower) / bandwidth
        
        # 價格觸及下軌 (超賣)
        if lower_distance < 0.1:
            return Signal.BUY
        
        # 價格觸及上軌 (超買)
        if upper_distance < 0.1:
            return Signal.SELL
        
        # 價格接近中軌
        if abs(price - middle) / bandwidth < 0.1:
            return Signal.NEUTRAL
        
        return Signal.NEUTRAL
    
    # ==================== SAR (拋物線指標) ====================
    
    def calculate_sar(
        self,
        high: np.ndarray,
        low: np.ndarray,
        acceleration: float = 0.02,
        maximum: float = 0.2
    ) -> np.ndarray:
        """
        計算拋物線 SAR
        
        Args:
            high: 最高價序列
            low: 最低價序列
            acceleration: 加速因子，預設 0.02
            maximum: 最大加速因子，預設 0.2
            
        Returns:
            SAR 值序列
        """
        return talib.SAR(
            high,
            low,
            acceleration=acceleration,
            maximum=maximum
        )
    
    def sar_signal(
        self,
        price: float,
        sar: float,
        prev_sar: float
    ) -> Signal:
        """
        根據 SAR 判斷信號
        
        Args:
            price: 當前價格
            sar: 當前 SAR 值
            prev_sar: 前一 SAR 值
            
        Returns:
            交易信號
        """
        if any(np.isnan([price, sar, prev_sar])):
            return Signal.NEUTRAL
        
        # SAR 從上方轉到下方 (看漲)
        if prev_sar > price and sar < price:
            return Signal.BUY
        
        # SAR 從下方轉到上方 (看跌)
        if prev_sar < price and sar > price:
            return Signal.SELL
        
        return Signal.NEUTRAL
    
    # ==================== StochRSI (隨機相對強弱) ====================
    
    def calculate_stochrsi(
        self,
        close: np.ndarray,
        timeperiod: int = 14,
        fastk_period: int = 5,
        fastd_period: int = 3
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        計算 StochRSI
        
        Args:
            close: 收盤價序列
            timeperiod: RSI 週期
            fastk_period: FastK 週期
            fastd_period: FastD 週期
            
        Returns:
            (fastk, fastd)
        """
        fastk, fastd = talib.STOCHRSI(
            close,
            timeperiod=timeperiod,
            fastk_period=fastk_period,
            fastd_period=fastd_period,
            fastd_matype=0  # SMA
        )
        return fastk, fastd
    
    def stochrsi_signal(
        self,
        fastk: float,
        fastd: float,
        oversold: float = 20,
        overbought: float = 80
    ) -> Signal:
        """
        根據 StochRSI 判斷信號
        
        Args:
            fastk: FastK 值
            fastd: FastD 值
            oversold: 超賣閾值
            overbought: 超買閾值
            
        Returns:
            交易信號
        """
        if any(np.isnan([fastk, fastd])):
            return Signal.NEUTRAL
        
        # 黃金交叉 + 超賣區
        if fastk > fastd and fastk < oversold:
            return Signal.STRONG_BUY
        
        # 超賣區
        if fastk < oversold and fastd < oversold:
            return Signal.BUY
        
        # 死亡交叉 + 超買區
        if fastk < fastd and fastk > overbought:
            return Signal.STRONG_SELL
        
        # 超買區
        if fastk > overbought and fastd > overbought:
            return Signal.SELL
        
        return Signal.NEUTRAL
    
    # ==================== ATR (真實波動幅度) ====================
    
    def calculate_atr(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = 14
    ) -> np.ndarray:
        """
        計算 ATR
        
        Args:
            high: 最高價序列
            low: 最低價序列
            close: 收盤價序列
            period: ATR 週期
            
        Returns:
            ATR 值序列
        """
        return talib.ATR(high, low, close, timeperiod=period)
    
    def atr_volatility_signal(
        self,
        atr: float,
        avg_atr: float,
        threshold: float = 1.5
    ) -> str:
        """
        根據 ATR 判斷波動性
        
        Args:
            atr: 當前 ATR 值
            avg_atr: 平均 ATR 值
            threshold: 高波動閾值倍數
            
        Returns:
            波動性描述 (LOW/NORMAL/HIGH)
        """
        if np.isnan(atr) or np.isnan(avg_atr):
            return "UNKNOWN"
        
        ratio = atr / avg_atr if avg_atr > 0 else 1.0
        
        if ratio > threshold:
            return "HIGH"  # 高波動
        elif ratio < 1 / threshold:
            return "LOW"  # 低波動
        else:
            return "NORMAL"  # 正常波動
    
    # ==================== 綜合分析 ====================
    
    def analyze_all_indicators(
        self,
        df: pd.DataFrame,
        rsi_period: int = 14,
        ma_short: int = 10,
        ma_long: int = 20,
        bb_period: int = 20,
        atr_period: int = 14
    ) -> Dict[str, Any]:
        """
        計算所有指標並生成綜合分析
        
        Args:
            df: DataFrame，需包含 open, high, low, close, volume
            rsi_period: RSI 週期
            ma_short: 短週期 MA
            ma_long: 長週期 MA
            bb_period: 布林通道週期
            atr_period: ATR 週期
            
        Returns:
            包含所有指標和信號的字典
        """
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        
        # 計算指標
        rsi = self.calculate_rsi(close, rsi_period)
        ma_s = self.calculate_ma(close, ma_short, "EMA")
        ma_l = self.calculate_ma(close, ma_long, "EMA")
        upper, middle, lower = self.calculate_bollinger_bands(close, bb_period)
        sar = self.calculate_sar(high, low)
        fastk, fastd = self.calculate_stochrsi(close)
        atr = self.calculate_atr(high, low, close, atr_period)
        
        # 當前值（最後一個）
        idx = -1
        result = {
            "timestamp": df.index[idx] if isinstance(df.index, pd.DatetimeIndex) else None,
            "price": close[idx],
            "indicators": {
                "rsi": {
                    "value": rsi[idx],
                    "signal": self.rsi_signal(rsi[idx]).value
                },
                "ma": {
                    "short": ma_s[idx],
                    "long": ma_l[idx],
                    "signal": self.ma_crossover_signal(
                        ma_s[idx], ma_l[idx],
                        ma_s[idx-1], ma_l[idx-1]
                    ).value if idx > 0 else Signal.NEUTRAL.value
                },
                "bollinger": {
                    "upper": upper[idx],
                    "middle": middle[idx],
                    "lower": lower[idx],
                    "signal": self.bollinger_signal(
                        close[idx], upper[idx], middle[idx], lower[idx]
                    ).value
                },
                "sar": {
                    "value": sar[idx],
                    "signal": self.sar_signal(
                        close[idx], sar[idx], sar[idx-1]
                    ).value if idx > 0 else Signal.NEUTRAL.value
                },
                "stochrsi": {
                    "fastk": fastk[idx],
                    "fastd": fastd[idx],
                    "signal": self.stochrsi_signal(fastk[idx], fastd[idx]).value
                },
                "atr": {
                    "value": atr[idx],
                    "volatility": self.atr_volatility_signal(
                        atr[idx],
                        np.nanmean(atr[-20:])
                    )
                }
            }
        }
        
        # 綜合信號評分
        signals = [
            result["indicators"]["rsi"]["signal"],
            result["indicators"]["ma"]["signal"],
            result["indicators"]["bollinger"]["signal"],
            result["indicators"]["sar"]["signal"],
            result["indicators"]["stochrsi"]["signal"]
        ]
        
        buy_count = signals.count(Signal.BUY.value) + signals.count(Signal.STRONG_BUY.value) * 2
        sell_count = signals.count(Signal.SELL.value) + signals.count(Signal.STRONG_SELL.value) * 2
        
        if buy_count > sell_count + 2:
            result["综合信號"] = Signal.BUY.value
        elif sell_count > buy_count + 2:
            result["综合信號"] = Signal.SELL.value
        else:
            result["综合信號"] = Signal.NEUTRAL.value
        
        result["信號評分"] = {
            "buy_score": buy_count,
            "sell_score": sell_count,
            "confidence": abs(buy_count - sell_count) / 10
        }
        
        return result


# 單例模式
_indicator_instance = None

def get_indicators() -> TechnicalIndicators:
    """獲取指標實例（單例）"""
    global _indicator_instance
    if _indicator_instance is None:
        _indicator_instance = TechnicalIndicators()
    return _indicator_instance
