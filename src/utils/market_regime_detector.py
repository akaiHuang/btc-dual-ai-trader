#!/usr/bin/env python3
"""
市場狀態偵測器 (Market Regime Detector)

判斷當前市場處於：
- BULL: 強勁上升趨勢
- BEAR: 強勁下降趨勢
- NEUTRAL: 弱趨勢，有方向但不明確
- CONSOLIDATION: 盤整，橫向移動

整合技術：
- MA 距離（趨勢強度）
- ATR/價格（波動率）
- 成交量趨勢（確認信號）

作者：AI Trading System
日期：2025-11-14
"""

from enum import Enum
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np


class MarketRegime(Enum):
    """市場狀態枚舉"""
    BULL = "BULL"  # 強勁多頭
    BEAR = "BEAR"  # 強勁空頭
    NEUTRAL = "NEUTRAL"  # 中性/弱趨勢
    CONSOLIDATION = "CONSOLIDATION"  # 盤整


class MarketRegimeDetector:
    """
    市場狀態偵測器
    
    整合多個技術指標判斷市場狀態：
    1. MA 距離 - 趨勢方向與強度
    2. ATR 相對值 - 波動率水平
    3. 成交量比率 - 趨勢確認
    """
    
    def __init__(
        self,
        ma_short: int = 7,
        ma_long: int = 25,
        atr_period: int = 14,
        volume_ma_period: int = 20,
        consolidation_threshold: float = 0.003,  # MA 距離 < 0.3% 視為盤整
        strong_trend_threshold: float = 0.01,    # MA 距離 > 1% 視為強趨勢
        low_volatility_threshold: float = 0.015,  # ATR/價格 < 1.5% 視為低波動
        high_volatility_threshold: float = 0.03,  # ATR/價格 > 3% 視為高波動
        range_ratio_consolidation: float = 0.004,
        directional_vol_threshold: float = 0.0005
    ):
        """
        初始化市場狀態偵測器
        
        Args:
            ma_short: 短期均線週期
            ma_long: 長期均線週期
            atr_period: ATR 週期
            volume_ma_period: 成交量均線週期
            consolidation_threshold: 盤整閾值（MA 距離百分比）
            strong_trend_threshold: 強趨勢閾值（MA 距離百分比）
            low_volatility_threshold: 低波動閾值（ATR/價格百分比）
            high_volatility_threshold: 高波動閾值（ATR/價格百分比）
        """
        self.ma_short = ma_short
        self.ma_long = ma_long
        self.atr_period = atr_period
        self.volume_ma_period = volume_ma_period
        
        # 閾值設定
        self.consolidation_threshold = consolidation_threshold
        self.strong_trend_threshold = strong_trend_threshold
        self.low_volatility_threshold = low_volatility_threshold
        self.high_volatility_threshold = high_volatility_threshold
        self.range_ratio_consolidation = range_ratio_consolidation
        self.directional_vol_threshold = directional_vol_threshold
        self.range_window = max(self.ma_long * 2, 60)
        
    def detect_regime(
        self,
        df: pd.DataFrame,
        return_details: bool = False
    ) -> MarketRegime | tuple[MarketRegime, Dict[str, Any]]:
        """
        檢測當前市場狀態
        
        Args:
            df: 包含 OHLCV 數據的 DataFrame
                必需欄位: close, high, low, volume
                選填欄位: ma7, ma25, atr (如果沒有會自動計算)
            return_details: 是否返回詳細指標
            
        Returns:
            MarketRegime 或 (MarketRegime, details)
            
        判斷邏輯：
        1. CONSOLIDATION: MA 距離小 + 低波動 + 成交量萎縮
        2. BULL: MA7 > MA25 + 強趨勢 + 成交量支撐
        3. BEAR: MA7 < MA25 + 強趨勢 + 成交量支撐
        4. NEUTRAL: 其他情況（弱趨勢）
        """
        if df.empty or len(df) < max(self.ma_long, self.volume_ma_period):
            regime = MarketRegime.NEUTRAL
            if return_details:
                return regime, {"error": "數據不足"}
            return regime
        
        # 計算必要指標
        metrics = self._calculate_metrics(df)
        
        # 判斷市場狀態
        regime = self._classify_regime(metrics)
        
        if return_details:
            return regime, metrics
        return regime
    
    def _calculate_metrics(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        計算判斷指標
        
        Returns:
            包含以下指標的字典：
            - ma_distance: MA 距離百分比
            - ma_trend: MA 趨勢方向 (+1/-1)
            - volatility: ATR/價格 百分比
            - volume_ratio: 當前成交量 / 均值成交量
        """
        # 確保數據是排序的
        df = df.sort_index()
        
        # 計算或使用現有的 MA
        if 'ma7' not in df.columns or 'ma25' not in df.columns:
            df['ma7'] = df['close'].rolling(window=self.ma_short).mean()
            df['ma25'] = df['close'].rolling(window=self.ma_long).mean()
        
        # 計算或使用現有的 ATR
        if 'atr' not in df.columns:
            df['atr'] = self._calculate_atr(df)
        
        # 取最新值
        latest = df.iloc[-1]
        ma7 = latest['ma7']
        ma25 = latest['ma25']
        close = latest['close']
        atr = latest['atr']
        
        # 1. MA 距離（趨勢強度）
        ma_distance = abs(ma7 - ma25) / ma25 if ma25 > 0 else 0
        
        # 2. MA 趨勢方向
        ma_trend = 1 if ma7 > ma25 else -1
        
        # 3. 波動率（ATR 相對值）
        volatility = atr / close if close > 0 else 0
        
        # 4. 成交量比率
        volume_ma = df['volume'].rolling(window=self.volume_ma_period).mean().iloc[-1]
        current_volume = latest['volume']
        volume_ratio = current_volume / volume_ma if volume_ma > 0 else 1.0
        
        # 5. 額外：價格相對 MA25 位置（輔助判斷）
        price_vs_ma25 = (close - ma25) / ma25 if ma25 > 0 else 0

        returns = df['close'].pct_change()
        directional_volatility = returns.tail(self.ma_long).mean()
        recent_high = df['high'].tail(self.range_window).max()
        recent_low = df['low'].tail(self.range_window).min()
        range_ratio = ((recent_high - recent_low) / close) if close > 0 else 0
        
        return {
            'ma_distance': ma_distance,
            'ma_trend': ma_trend,
            'volatility': volatility,
            'volume_ratio': volume_ratio,
            'price_vs_ma25': price_vs_ma25,
            'directional_volatility': directional_volatility,
            'range_ratio': range_ratio,
            'close': close,
            'ma7': ma7,
            'ma25': ma25,
            'atr': atr
        }
    
    def _calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        """計算 ATR（Average True Range）"""
        high = df['high']
        low = df['low']
        close = df['close']
        
        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # ATR = TR 的移動平均
        atr = tr.rolling(window=self.atr_period).mean()
        
        return atr
    
    def _classify_regime(self, metrics: Dict[str, float]) -> MarketRegime:
        """
        根據指標分類市場狀態
        
        優先級：
        1. CONSOLIDATION（盤整最優先判斷）
        2. BULL / BEAR（強趨勢）
        3. NEUTRAL（默認）
        """
        ma_distance = metrics['ma_distance']
        ma_trend = metrics['ma_trend']
        volatility = metrics['volatility']
        volume_ratio = metrics['volume_ratio']
        range_ratio = metrics['range_ratio']
        directional_volatility = metrics['directional_volatility']
        
        # === 1. 判斷 CONSOLIDATION ===
        # 條件：MA 距離小 + 低波動
        is_consolidation = (
            ma_distance < self.consolidation_threshold and
            volatility < self.low_volatility_threshold and
            range_ratio < self.range_ratio_consolidation
        )
        
        if is_consolidation:
            return MarketRegime.CONSOLIDATION
        
        # === 2. 判斷強趨勢 BULL/BEAR ===
        # 條件：MA 距離大 + 成交量支撐
        is_strong_trend = (
            ma_distance > self.strong_trend_threshold and
            volume_ratio > 0.8 and
            abs(directional_volatility) > self.directional_vol_threshold
        )
        
        if is_strong_trend:
            if ma_trend > 0:
                return MarketRegime.BULL
            else:
                return MarketRegime.BEAR
        
        # === 3. 默認 NEUTRAL ===
        # 有趨勢但不夠強，或成交量不足
        return MarketRegime.NEUTRAL
    
    def get_regime_description(self, regime: MarketRegime) -> str:
        """獲取市場狀態的中文描述"""
        descriptions = {
            MarketRegime.BULL: "強勁多頭 - 上升趨勢明確，波動適中",
            MarketRegime.BEAR: "強勁空頭 - 下降趨勢明確，波動適中",
            MarketRegime.NEUTRAL: "中性盤勢 - 趨勢不明確或成交量不足",
            MarketRegime.CONSOLIDATION: "盤整狀態 - 橫向移動，波動低迷"
        }
        return descriptions.get(regime, "未知狀態")
    
    def should_trade(self, regime: MarketRegime) -> bool:
        """
        根據市場狀態判斷是否適合交易
        
        Returns:
            True: 適合交易
            False: 不適合交易（盤整期）
        """
        # 盤整期禁止交易
        return regime != MarketRegime.CONSOLIDATION


# ============================================
# 測試與示例
# ============================================

def test_market_regime_detector():
    """測試市場狀態偵測器"""
    print("=" * 60)
    print("測試市場狀態偵測器")
    print("=" * 60)
    
    # 創建測試數據
    import numpy as np
    dates = pd.date_range('2024-01-01', periods=100, freq='15min')
    
    # 測試案例 1: 強勁多頭
    print("\n【案例 1: 強勁多頭】")
    df_bull = pd.DataFrame({
        'close': np.linspace(40000, 45000, 100),  # 持續上漲
        'high': np.linspace(40100, 45100, 100),
        'low': np.linspace(39900, 44900, 100),
        'volume': np.random.uniform(100, 200, 100)  # 穩定成交量
    }, index=dates)
    
    detector = MarketRegimeDetector()
    regime, details = detector.detect_regime(df_bull, return_details=True)
    
    print(f"狀態: {regime.value}")
    print(f"描述: {detector.get_regime_description(regime)}")
    print(f"MA 距離: {details['ma_distance']:.4f} ({details['ma_distance']*100:.2f}%)")
    print(f"波動率: {details['volatility']:.4f} ({details['volatility']*100:.2f}%)")
    print(f"成交量比率: {details['volume_ratio']:.2f}")
    print(f"可交易: {'✅ 是' if detector.should_trade(regime) else '❌ 否'}")
    
    # 測試案例 2: 盤整
    print("\n【案例 2: 盤整狀態】")
    df_consolidation = pd.DataFrame({
        'close': 42000 + np.random.uniform(-50, 50, 100),  # 窄幅震盪
        'high': 42050 + np.random.uniform(-50, 50, 100),
        'low': 41950 + np.random.uniform(-50, 50, 100),
        'volume': np.random.uniform(50, 80, 100)  # 低成交量
    }, index=dates)
    
    regime, details = detector.detect_regime(df_consolidation, return_details=True)
    
    print(f"狀態: {regime.value}")
    print(f"描述: {detector.get_regime_description(regime)}")
    print(f"MA 距離: {details['ma_distance']:.4f} ({details['ma_distance']*100:.2f}%)")
    print(f"波動率: {details['volatility']:.4f} ({details['volatility']*100:.2f}%)")
    print(f"成交量比率: {details['volume_ratio']:.2f}")
    print(f"可交易: {'✅ 是' if detector.should_trade(regime) else '❌ 否'}")
    
    # 測試案例 3: 強勁空頭
    print("\n【案例 3: 強勁空頭】")
    df_bear = pd.DataFrame({
        'close': np.linspace(45000, 40000, 100),  # 持續下跌
        'high': np.linspace(45100, 40100, 100),
        'low': np.linspace(44900, 39900, 100),
        'volume': np.random.uniform(150, 250, 100)  # 較高成交量
    }, index=dates)
    
    regime, details = detector.detect_regime(df_bear, return_details=True)
    
    print(f"狀態: {regime.value}")
    print(f"描述: {detector.get_regime_description(regime)}")
    print(f"MA 距離: {details['ma_distance']:.4f} ({details['ma_distance']*100:.2f}%)")
    print(f"波動率: {details['volatility']:.4f} ({details['volatility']*100:.2f}%)")
    print(f"成交量比率: {details['volume_ratio']:.2f}")
    print(f"可交易: {'✅ 是' if detector.should_trade(regime) else '❌ 否'}")
    
    print("\n" + "=" * 60)
    print("✅ 測試完成")
    print("=" * 60)


if __name__ == "__main__":
    test_market_regime_detector()
