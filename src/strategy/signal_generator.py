"""
Signal Generator - Task 1.6.1 Phase C

Purpose:
    整合多個微觀結構指標，生成統一的多空信號。
    
Architecture:
    Layer 1 (Signal) → Layer 2 (Regime) → Layer 3 (Execution)
    
Indicators Integrated:
    1. OBI (Order Book Imbalance) - 40% 權重
    2. OBI Velocity (dOBI/dt) - 20% 權重
    3. Signed Volume - 30% 權重
    4. Microprice Pressure - 10% 權重

Output:
    - Signal: LONG / SHORT / NEUTRAL
    - Confidence: 0.0 - 1.0
    - Components: 各指標的詳細貢獻

Author: GitHub Copilot
Created: 2025-11-10 (Task 1.6.1 - C1)
"""

from typing import Dict, Tuple, Optional, List
from datetime import datetime
from collections import deque
import numpy as np


class SignalGenerator:
    """
    信號生成器 - 整合多個微觀結構指標產生交易信號
    
    使用加權評分系統：
    - OBI: 40% （訂單簿失衡，最重要）
    - OBI Velocity: 20% （動能變化）
    - Signed Volume: 30% （成交量確認）
    - Microprice Pressure: 10% （價格壓力）
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        obi_weight: float = 0.4,
        velocity_weight: float = 0.2,
        volume_weight: float = 0.3,
        microprice_weight: float = 0.1,
        long_threshold: float = 0.6,
        short_threshold: float = 0.6,
        history_size: int = 100
    ):
        """
        初始化信號生成器
        
        Args:
            symbol: 交易對
            obi_weight: OBI 權重
            velocity_weight: OBI 速度權重
            volume_weight: 簽名成交量權重
            microprice_weight: 微觀價格壓力權重
            long_threshold: 做多信號閾值（0-1）
            short_threshold: 做空信號閾值（0-1）
            history_size: 歷史記錄大小
        """
        self.symbol = symbol
        
        # 權重配置
        self.obi_weight = obi_weight
        self.velocity_weight = velocity_weight
        self.volume_weight = volume_weight
        self.microprice_weight = microprice_weight
        
        # 確保權重總和為 1.0
        total_weight = obi_weight + velocity_weight + volume_weight + microprice_weight
        if abs(total_weight - 1.0) > 0.001:
            raise ValueError(f"權重總和必須為 1.0，當前為 {total_weight}")
        
        # 閾值配置
        self.long_threshold = long_threshold
        self.short_threshold = short_threshold
        
        # 歷史記錄
        self.signal_history: deque = deque(maxlen=history_size)
        
        # 統計數據
        self.total_signals_generated = 0
        self.long_signals = 0
        self.short_signals = 0
        self.neutral_signals = 0
    
    def generate_signal(self, market_data: dict) -> Tuple[str, float, dict]:
        """
        生成交易信號
        
        Args:
            market_data: 市場數據字典，包含：
                - 'obi': OBI 值（-1 到 1）
                - 'obi_velocity': OBI 速度
                - 'signed_volume': 簽名成交量（正=買，負=賣）
                - 'microprice_pressure': 微觀價格壓力
                - 'timestamp': 時間戳（可選）
        
        Returns:
            (signal, confidence, details)
            - signal: "LONG" / "SHORT" / "NEUTRAL"
            - confidence: 信號強度（0-1）
            - details: 各指標的詳細貢獻
        """
        # 提取指標數據
        obi = market_data.get('obi', 0)
        obi_velocity = market_data.get('obi_velocity', 0)
        signed_volume = market_data.get('signed_volume', 0)
        microprice_pressure = market_data.get('microprice_pressure', 0)
        timestamp = market_data.get('timestamp', datetime.now().timestamp() * 1000)
        
        # 計算多空評分（0 到 1 範圍）
        long_score = 0.0
        short_score = 0.0
        
        # === 指標 1: OBI（40% 權重）===
        obi_contribution_long = 0.0
        obi_contribution_short = 0.0
        
        if obi > 0.3:
            # 強烈買單優勢
            obi_contribution_long = self.obi_weight * min(obi, 1.0)
        elif obi > 0.1:
            # 輕微買單優勢
            obi_contribution_long = self.obi_weight * 0.5 * (obi / 0.3)
        elif obi < -0.3:
            # 強烈賣單優勢
            obi_contribution_short = self.obi_weight * min(abs(obi), 1.0)
        elif obi < -0.1:
            # 輕微賣單優勢
            obi_contribution_short = self.obi_weight * 0.5 * (abs(obi) / 0.3)
        
        long_score += obi_contribution_long
        short_score += obi_contribution_short
        
        # === 指標 2: OBI Velocity（20% 權重）===
        velocity_contribution_long = 0.0
        velocity_contribution_short = 0.0
        
        if obi_velocity > 0.05:
            # OBI 快速上升（買盤加速）
            velocity_contribution_long = self.velocity_weight * min(obi_velocity / 0.1, 1.0)
        elif obi_velocity < -0.05:
            # OBI 快速下降（賣盤加速）
            velocity_contribution_short = self.velocity_weight * min(abs(obi_velocity) / 0.1, 1.0)
        
        long_score += velocity_contribution_long
        short_score += velocity_contribution_short
        
        # === 指標 3: Signed Volume（30% 權重）===
        volume_contribution_long = 0.0
        volume_contribution_short = 0.0
        
        # 正規化簽名成交量（避免極端值）
        normalized_volume = np.tanh(signed_volume / 100)  # 假設 100 BTC 為顯著值
        
        if normalized_volume > 0:
            # 買方成交量優勢
            volume_contribution_long = self.volume_weight * normalized_volume
        else:
            # 賣方成交量優勢
            volume_contribution_short = self.volume_weight * abs(normalized_volume)
        
        long_score += volume_contribution_long
        short_score += volume_contribution_short
        
        # === 指標 4: Microprice Pressure（10% 權重）===
        microprice_contribution_long = 0.0
        microprice_contribution_short = 0.0
        
        if microprice_pressure > 0:
            # 買方價格壓力
            microprice_contribution_long = self.microprice_weight * min(microprice_pressure, 1.0)
        else:
            # 賣方價格壓力
            microprice_contribution_short = self.microprice_weight * min(abs(microprice_pressure), 1.0)
        
        long_score += microprice_contribution_long
        short_score += microprice_contribution_short
        
        # === 決策邏輯 ===
        if long_score > self.long_threshold and long_score > short_score:
            signal = "LONG"
            confidence = long_score
            self.long_signals += 1
        elif short_score > self.short_threshold and short_score > long_score:
            signal = "SHORT"
            confidence = short_score
            self.short_signals += 1
        else:
            signal = "NEUTRAL"
            confidence = max(long_score, short_score)
            self.neutral_signals += 1
        
        # 記錄詳細資訊
        details = {
            'signal': signal,
            'confidence': confidence,
            'long_score': long_score,
            'short_score': short_score,
            'components': {
                'obi': {
                    'value': obi,
                    'long_contribution': obi_contribution_long,
                    'short_contribution': obi_contribution_short,
                    'weight': self.obi_weight
                },
                'velocity': {
                    'value': obi_velocity,
                    'long_contribution': velocity_contribution_long,
                    'short_contribution': velocity_contribution_short,
                    'weight': self.velocity_weight
                },
                'volume': {
                    'value': signed_volume,
                    'normalized': normalized_volume,
                    'long_contribution': volume_contribution_long,
                    'short_contribution': volume_contribution_short,
                    'weight': self.volume_weight
                },
                'microprice': {
                    'value': microprice_pressure,
                    'long_contribution': microprice_contribution_long,
                    'short_contribution': microprice_contribution_short,
                    'weight': self.microprice_weight
                }
            },
            'timestamp': timestamp
        }
        
        # 儲存歷史
        self.signal_history.append(details)
        self.total_signals_generated += 1
        
        return signal, confidence, details
    
    def get_signal_statistics(self) -> dict:
        """
        獲取信號統計資料
        
        Returns:
            統計資料字典
        """
        stats = {
            'symbol': self.symbol,
            'total_signals': self.total_signals_generated,
            'long_signals': self.long_signals,
            'short_signals': self.short_signals,
            'neutral_signals': self.neutral_signals,
            'long_ratio': self.long_signals / self.total_signals_generated if self.total_signals_generated > 0 else 0,
            'short_ratio': self.short_signals / self.total_signals_generated if self.total_signals_generated > 0 else 0,
            'neutral_ratio': self.neutral_signals / self.total_signals_generated if self.total_signals_generated > 0 else 0,
        }
        
        # 計算平均信心度
        if self.signal_history:
            long_confidences = [s['confidence'] for s in self.signal_history if s['signal'] == 'LONG']
            short_confidences = [s['confidence'] for s in self.signal_history if s['signal'] == 'SHORT']
            
            stats.update({
                'avg_long_confidence': np.mean(long_confidences) if long_confidences else 0,
                'avg_short_confidence': np.mean(short_confidences) if short_confidences else 0,
                'history_size': len(self.signal_history)
            })
        
        return stats
    
    def get_recent_signals(self, count: int = 10) -> List[dict]:
        """
        獲取最近的信號記錄
        
        Args:
            count: 返回的記錄數量
        
        Returns:
            最近的信號列表
        """
        if not self.signal_history:
            return []
        
        return list(self.signal_history)[-count:]
    
    def analyze_signal_consistency(self, window: int = 10) -> dict:
        """
        分析信號一致性（連續同向信號）
        
        Args:
            window: 分析窗口大小
        
        Returns:
            一致性分析結果
        """
        if len(self.signal_history) < window:
            return {
                'sufficient_data': False,
                'message': f'需要至少 {window} 個信號，當前只有 {len(self.signal_history)}'
            }
        
        recent = list(self.signal_history)[-window:]
        signals = [s['signal'] for s in recent]
        
        # 計算連續性
        consecutive_long = 0
        consecutive_short = 0
        consecutive_neutral = 0
        
        for sig in reversed(signals):
            if sig == 'LONG':
                consecutive_long += 1
                if consecutive_short > 0 or consecutive_neutral > 0:
                    break
            elif sig == 'SHORT':
                consecutive_short += 1
                if consecutive_long > 0 or consecutive_neutral > 0:
                    break
            else:
                consecutive_neutral += 1
                if consecutive_long > 0 or consecutive_short > 0:
                    break
        
        # 計算趨勢
        long_count = signals.count('LONG')
        short_count = signals.count('SHORT')
        neutral_count = signals.count('NEUTRAL')
        
        if long_count > window * 0.6:
            trend = 'STRONG_BULLISH'
        elif short_count > window * 0.6:
            trend = 'STRONG_BEARISH'
        elif long_count > short_count:
            trend = 'BULLISH'
        elif short_count > long_count:
            trend = 'BEARISH'
        else:
            trend = 'RANGING'
        
        return {
            'sufficient_data': True,
            'window_size': window,
            'consecutive_long': consecutive_long,
            'consecutive_short': consecutive_short,
            'consecutive_neutral': consecutive_neutral,
            'long_count': long_count,
            'short_count': short_count,
            'neutral_count': neutral_count,
            'trend': trend,
            'consistency_score': max(long_count, short_count) / window
        }
    
    def reset(self):
        """重置生成器"""
        self.signal_history.clear()
        self.total_signals_generated = 0
        self.long_signals = 0
        self.short_signals = 0
        self.neutral_signals = 0
