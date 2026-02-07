"""
盤整偵測器 - Consolidation Detector
使用 Bollinger Band %B + ATR 偵測市場盤整狀態
盤整期間禁止交易，避免假突破和頻繁止損

作者: Phase 0 優化項目
日期: 2025-11-14
"""

import numpy as np
import talib
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ConsolidationState:
    """盤整狀態數據類"""
    is_consolidating: bool
    bb_width: float  # Bollinger Band 寬度
    bb_percent_b: float  # %B 指標
    atr_ratio: float  # ATR / 收盤價比率
    confidence: float  # 盤整信心度 (0-1)
    reason: str  # 判斷原因
    timestamp: datetime


class ConsolidationDetector:
    """
    盤整偵測器
    
    核心邏輯：
    1. Bollinger Band 收窄（寬度 < 閾值）
    2. %B 在 0.3-0.7 範圍內（價格在中軌附近）
    3. ATR 下降（波動率降低）
    
    組合這三個條件判斷是否盤整
    """
    
    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        atr_period: int = 14,
        bb_width_threshold: float = 0.02,  # BB 寬度 < 2% 視為收窄
        atr_threshold: float = 0.005,  # ATR / Price < 0.5% 視為低波動
        percent_b_lower: float = 0.3,  # %B 下限
        percent_b_upper: float = 0.7,  # %B 上限
        min_data_points: int = 50  # 最少需要的數據點
    ):
        """
        初始化盤整偵測器
        
        Args:
            bb_period: Bollinger Band 週期
            bb_std: Bollinger Band 標準差倍數
            atr_period: ATR 週期
            bb_width_threshold: BB 寬度閾值（相對值）
            atr_threshold: ATR 閾值（相對價格）
            percent_b_lower: %B 下限
            percent_b_upper: %B 上限
            min_data_points: 最少數據點數
        """
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.atr_period = atr_period
        self.bb_width_threshold = bb_width_threshold
        self.atr_threshold = atr_threshold
        self.percent_b_lower = percent_b_lower
        self.percent_b_upper = percent_b_upper
        self.min_data_points = min_data_points
        
        # 歷史狀態（用於平滑判斷）
        self.history_length = 5
        self.recent_states = []
    
    def calculate_bollinger_bands(
        self,
        close: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        計算 Bollinger Bands
        
        Returns:
            (upper_band, middle_band, lower_band)
        """
        upper, middle, lower = talib.BBANDS(
            close,
            timeperiod=self.bb_period,
            nbdevup=self.bb_std,
            nbdevdn=self.bb_std,
            matype=0  # SMA
        )
        return upper, middle, lower
    
    def calculate_percent_b(
        self,
        close: float,
        upper: float,
        lower: float
    ) -> float:
        """
        計算 %B 指標
        %B = (Close - Lower Band) / (Upper Band - Lower Band)
        
        Returns:
            %B 值，範圍通常在 0-1，但可能超出
        """
        if upper == lower:
            return 0.5  # 避免除零
        return (close - lower) / (upper - lower)
    
    def calculate_bb_width(
        self,
        upper: float,
        lower: float,
        middle: float
    ) -> float:
        """
        計算 Bollinger Band 寬度（相對中軌）
        Width = (Upper - Lower) / Middle
        
        Returns:
            BB 相對寬度
        """
        if middle == 0:
            return 0
        return (upper - lower) / middle
    
    def calculate_atr_ratio(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray
    ) -> float:
        """
        計算 ATR 相對於價格的比率
        
        Returns:
            ATR / Close 比率
        """
        atr = talib.ATR(high, low, close, timeperiod=self.atr_period)
        if len(atr) == 0 or np.isnan(atr[-1]):
            return 0
        
        current_close = close[-1]
        if current_close == 0:
            return 0
        
        return atr[-1] / current_close
    
    def is_consolidating(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        use_smoothing: bool = True
    ) -> ConsolidationState:
        """
        判斷當前市場是否處於盤整狀態
        
        Args:
            high: 最高價序列
            low: 最低價序列
            close: 收盤價序列
            use_smoothing: 是否使用平滑（多次判斷取平均）
            
        Returns:
            ConsolidationState 對象
        """
        # 數據驗證
        if len(close) < self.min_data_points:
            return ConsolidationState(
                is_consolidating=False,
                bb_width=0,
                bb_percent_b=0.5,
                atr_ratio=0,
                confidence=0,
                reason="數據不足",
                timestamp=datetime.now()
            )
        
        # 1. 計算 Bollinger Bands
        upper, middle, lower = self.calculate_bollinger_bands(close)
        
        # 檢查計算結果
        if np.isnan(upper[-1]) or np.isnan(lower[-1]) or np.isnan(middle[-1]):
            return ConsolidationState(
                is_consolidating=False,
                bb_width=0,
                bb_percent_b=0.5,
                atr_ratio=0,
                confidence=0,
                reason="BB 計算失敗",
                timestamp=datetime.now()
            )
        
        # 2. 計算當前指標值
        current_close = close[-1]
        current_upper = upper[-1]
        current_lower = lower[-1]
        current_middle = middle[-1]
        
        bb_width = self.calculate_bb_width(
            current_upper, current_lower, current_middle
        )
        percent_b = self.calculate_percent_b(
            current_close, current_upper, current_lower
        )
        atr_ratio = self.calculate_atr_ratio(high, low, close)
        
        # 3. 判斷條件
        conditions = {
            'bb_narrow': bb_width < self.bb_width_threshold,
            'percent_b_center': (
                self.percent_b_lower < percent_b < self.percent_b_upper
            ),
            'low_volatility': atr_ratio < self.atr_threshold
        }
        
        # 4. 計算信心度
        confidence = sum(conditions.values()) / len(conditions)
        
        # 5. 判斷是否盤整（至少滿足 2/3 條件）
        is_consolidating = sum(conditions.values()) >= 2
        
        # 6. 生成判斷原因
        reasons = []
        if conditions['bb_narrow']:
            reasons.append(f"BB收窄({bb_width:.4f})")
        if conditions['percent_b_center']:
            reasons.append(f"%B居中({percent_b:.2f})")
        if conditions['low_volatility']:
            reasons.append(f"低波動({atr_ratio:.4f})")
        
        reason = " + ".join(reasons) if reasons else "條件不足"
        
        # 7. 創建狀態對象
        state = ConsolidationState(
            is_consolidating=is_consolidating,
            bb_width=bb_width,
            bb_percent_b=percent_b,
            atr_ratio=atr_ratio,
            confidence=confidence,
            reason=reason,
            timestamp=datetime.now()
        )
        
        # 8. 平滑處理（避免頻繁切換）
        if use_smoothing:
            self.recent_states.append(is_consolidating)
            if len(self.recent_states) > self.history_length:
                self.recent_states.pop(0)
            
            # 如果最近 N 次判斷中多數為盤整，才判定為盤整
            if len(self.recent_states) >= 3:
                smoothed_result = sum(self.recent_states) >= (len(self.recent_states) / 2)
                state.is_consolidating = smoothed_result
        
        return state
    
    def get_detailed_analysis(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray
    ) -> Dict[str, Any]:
        """
        獲取詳細的盤整分析報告
        
        Returns:
            包含所有指標和判斷邏輯的字典
        """
        state = self.is_consolidating(high, low, close, use_smoothing=False)
        
        # 計算歷史統計
        upper, middle, lower = self.calculate_bollinger_bands(close)
        bb_widths = []
        for i in range(max(0, len(close) - 20), len(close)):
            if not np.isnan(upper[i]) and not np.isnan(lower[i]):
                width = self.calculate_bb_width(upper[i], lower[i], middle[i])
                bb_widths.append(width)
        
        return {
            'current_state': state,
            'thresholds': {
                'bb_width_threshold': self.bb_width_threshold,
                'atr_threshold': self.atr_threshold,
                'percent_b_range': (self.percent_b_lower, self.percent_b_upper)
            },
            'statistics': {
                'avg_bb_width_20': np.mean(bb_widths) if bb_widths else 0,
                'current_vs_avg': (
                    state.bb_width / np.mean(bb_widths) 
                    if bb_widths and np.mean(bb_widths) > 0 
                    else 0
                )
            },
            'recommendation': (
                "🚫 禁止交易 - 市場盤整中" 
                if state.is_consolidating 
                else "✅ 允許交易 - 市場有方向"
            )
        }
    
    def reset(self):
        """重置歷史狀態"""
        self.recent_states = []


# ==================== 使用範例 ====================
if __name__ == "__main__":
    # 創建檢測器
    detector = ConsolidationDetector(
        bb_width_threshold=0.02,  # 2%
        atr_threshold=0.005,  # 0.5%
    )
    
    # 模擬數據（100 根 K 線）
    np.random.seed(42)
    n = 100
    
    # 模擬盤整行情（小幅波動）
    consolidation_price = 50000 + np.random.randn(n) * 100
    
    high = consolidation_price + np.abs(np.random.randn(n) * 50)
    low = consolidation_price - np.abs(np.random.randn(n) * 50)
    close = consolidation_price
    
    # 檢測
    state = detector.is_consolidating(high, low, close)
    
    print("=== 盤整偵測結果 ===")
    print(f"是否盤整: {state.is_consolidating}")
    print(f"BB 寬度: {state.bb_width:.4f} (閾值: {detector.bb_width_threshold})")
    print(f"%B 值: {state.bb_percent_b:.4f}")
    print(f"ATR 比率: {state.atr_ratio:.4f} (閾值: {detector.atr_threshold})")
    print(f"信心度: {state.confidence:.2%}")
    print(f"原因: {state.reason}")
    
    # 詳細分析
    analysis = detector.get_detailed_analysis(high, low, close)
    print(f"\n{analysis['recommendation']}")
    print(f"當前 BB 寬度 vs 平均: {analysis['statistics']['current_vs_avg']:.2f}x")
