"""
混合策略：Funding Rate + 技術指標
====================================

設計理念：
1. Funding Rate 作為高質量確認信號（捕捉市場情緒極端）
2. 技術指標作為主要信號生成器（提高交易頻率）
3. 多信號融合，提高準確度

信號來源：
- Layer 1: Funding Rate 極端值（當前市場情緒）
- Layer 2: RSI 超買超賣（價格動能反轉）
- Layer 3: MACD 趨勢反轉（中期趨勢變化）
- Layer 4: 成交量突增（突破確認）

目標：
- 交易頻率：5-10 筆/天
- 勝率：60-70%
- 適應市場演變（2020-2025 各年份都能工作）
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional, Dict, Tuple
from enum import Enum


class SignalType(Enum):
    """信號類型"""
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class SignalSource(Enum):
    """信號來源"""
    FUNDING_EXTREME = "funding_extreme"  # Funding 極端
    RSI_EXTREME = "rsi_extreme"  # RSI 極端
    MACD_CROSS = "macd_cross"  # MACD 交叉
    VOLUME_SPIKE = "volume_spike"  # 成交量突增
    COMBINED = "combined"  # 組合信號


@dataclass
class HybridSignal:
    """混合信號"""
    signal: SignalType
    confidence: float  # 0-1
    sources: list  # 信號來源列表
    reasoning: str  # 推理過程
    
    # 各層信號詳情
    funding_signal: Optional[str] = None
    rsi_signal: Optional[str] = None
    macd_signal: Optional[str] = None
    volume_signal: Optional[str] = None
    
    # 技術指標數值
    funding_rate: Optional[float] = None
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal_line: Optional[float] = None
    volume_ratio: Optional[float] = None


class HybridFundingTechnicalStrategy:
    """
    混合策略：Funding Rate + 技術指標
    
    策略邏輯：
    1. 優先級1：Funding 極端 + 技術指標確認 → 高置信度信號
    2. 優先級2：強烈技術信號（多指標共振）→ 中等置信度
    3. 優先級3：單一技術信號 → 低置信度（可選擇性忽略）
    """
    
    def __init__(
        self,
        # Funding Rate 參數（改用動態 Z-score）
        funding_zscore_threshold: float = 2.0,  # Z-score 閾值（標準差倍數）
        funding_lookback_days: int = 90,  # Rolling window（天數）
        
        # RSI 參數
        rsi_period: int = 14,
        rsi_oversold: float = 30,
        rsi_overbought: float = 70,
        
        # MACD 參數
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        
        # 成交量參數
        volume_lookback: int = 20,
        volume_spike_threshold: float = 2.0,  # 成交量突增倍數
        
        # 信號組合參數（改用加權分數）
        signal_score_threshold: float = 0.5,  # 加權分數閾值
        require_funding_confirmation: bool = False  # 是否必須有 Funding 確認
    ):
        self.funding_zscore_threshold = funding_zscore_threshold
        self.funding_lookback_days = funding_lookback_days
        
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        
        self.volume_lookback = volume_lookback
        self.volume_spike_threshold = volume_spike_threshold
        
        self.signal_score_threshold = signal_score_threshold
        self.require_funding_confirmation = require_funding_confirmation
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """計算所有技術指標"""
        df = df.copy()
        
        # 1. Funding Rate Z-score（動態閾值）
        if 'fundingRate' in df.columns:
            # 計算 rolling mean 和 std（使用 K線數量，15分鐘一根）
            lookback_periods = self.funding_lookback_days * 24 * 4  # 90天 = 8640根15分鐘K線
            df['funding_rolling_mean'] = df['fundingRate'].rolling(lookback_periods, min_periods=1).mean()
            df['funding_rolling_std'] = df['fundingRate'].rolling(lookback_periods, min_periods=1).std()
            
            # Z-score = (當前值 - 均值) / 標準差
            df['funding_zscore'] = (df['fundingRate'] - df['funding_rolling_mean']) / df['funding_rolling_std']
            df['funding_zscore'] = df['funding_zscore'].fillna(0)  # 處理 NaN
        
        # 2. RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(self.rsi_period).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # 3. MACD
        exp1 = df['close'].ewm(span=self.macd_fast, adjust=False).mean()
        exp2 = df['close'].ewm(span=self.macd_slow, adjust=False).mean()
        df['macd'] = exp1 - exp2
        df['macd_signal'] = df['macd'].ewm(span=self.macd_signal, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']
        
        # 4. 成交量均值
        df['volume_ma'] = df['volume'].rolling(self.volume_lookback).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma']
        
        # 5. MA (用於趨勢判斷)
        df['ma_20'] = df['close'].rolling(20).mean()
        df['ma_50'] = df['close'].rolling(50).mean()
        
        return df
    
    def check_funding_signal(self, row: pd.Series) -> Tuple[Optional[str], float]:
        """
        檢查 Funding Rate 信號（使用 Z-score）
        
        Returns:
            (signal_type, confidence)
        """
        if 'funding_zscore' not in row or pd.isna(row['funding_zscore']):
            return None, 0.0
        
        zscore = row['funding_zscore']
        
        # 極端正 Z-score → 做空機會（Funding 過高）
        if zscore >= self.funding_zscore_threshold:
            confidence = min(abs(zscore) / 3.0, 1.0)  # 3 sigma = 100% 信心
            return "SHORT", confidence
        
        # 極端負 Z-score → 做多機會（Funding 過低）
        elif zscore <= -self.funding_zscore_threshold:
            confidence = min(abs(zscore) / 3.0, 1.0)
            return "LONG", confidence
        
        return None, 0.0
    
    def check_rsi_signal(self, row: pd.Series) -> Tuple[Optional[str], float]:
        """
        檢查 RSI 信號
        
        Returns:
            (signal_type, confidence)
        """
        if 'rsi' not in row or pd.isna(row['rsi']):
            return None, 0.0
        
        rsi = row['rsi']
        
        # RSI 超賣 → 做多
        if rsi < self.rsi_oversold:
            distance = self.rsi_oversold - rsi
            confidence = min(distance / 20, 1.0)  # 越超賣信心越高
            return "LONG", confidence
        
        # RSI 超買 → 做空
        elif rsi > self.rsi_overbought:
            distance = rsi - self.rsi_overbought
            confidence = min(distance / 20, 1.0)
            return "SHORT", confidence
        
        return None, 0.0
    
    def check_macd_signal(self, df: pd.DataFrame, idx: int) -> Tuple[Optional[str], float]:
        """
        檢查 MACD 交叉信號
        
        Returns:
            (signal_type, confidence)
        """
        if idx < 1:
            return None, 0.0
        
        row = df.iloc[idx]
        prev_row = df.iloc[idx - 1]
        
        if pd.isna(row['macd']) or pd.isna(row['macd_signal']):
            return None, 0.0
        
        # 金叉：MACD 上穿信號線 → 做多
        if (prev_row['macd'] <= prev_row['macd_signal'] and 
            row['macd'] > row['macd_signal']):
            # 信心度基於 MACD 柱狀圖強度
            confidence = min(abs(row['macd_hist']) * 100, 1.0)
            return "LONG", confidence
        
        # 死叉：MACD 下穿信號線 → 做空
        elif (prev_row['macd'] >= prev_row['macd_signal'] and 
              row['macd'] < row['macd_signal']):
            confidence = min(abs(row['macd_hist']) * 100, 1.0)
            return "SHORT", confidence
        
        return None, 0.0
    
    def check_volume_signal(self, row: pd.Series) -> Tuple[bool, float]:
        """
        檢查成交量突增
        
        Returns:
            (has_spike, volume_ratio)
        """
        if 'volume_ratio' not in row or pd.isna(row['volume_ratio']):
            return False, 1.0
        
        volume_ratio = row['volume_ratio']
        
        # 成交量突增
        if volume_ratio >= self.volume_spike_threshold:
            return True, volume_ratio
        
        return False, volume_ratio
    
    def combine_signals(
        self,
        funding_signal: Tuple[Optional[str], float],
        rsi_signal: Tuple[Optional[str], float],
        macd_signal: Tuple[Optional[str], float],
        volume_spike: Tuple[bool, float],
        row: pd.Series
    ) -> HybridSignal:
        """
        組合多個信號，生成最終決策（改用加權分數）
        
        新邏輯：
        1. 每個信號貢獻一個帶方向的分數（LONG 為正，SHORT 為負）
        2. 成交量作為放大器（不獨立加分）
        3. 最終分數 >= threshold → LONG，<= -threshold → SHORT
        """
        # 初始化
        score = 0.0
        signals = []
        reasoning_parts = []
        
        # 1. Funding Rate（權重 40%）
        if funding_signal[0]:
            signals.append(SignalSource.FUNDING_EXTREME)
            contribution = funding_signal[1] * 0.4
            if funding_signal[0] == "LONG":
                score += contribution
            else:  # SHORT
                score -= contribution
            reasoning_parts.append(
                f"Funding {funding_signal[0]} (貢獻{contribution:+.2f})"
            )
        
        # 2. RSI（權重 25%）
        if rsi_signal[0]:
            signals.append(SignalSource.RSI_EXTREME)
            contribution = rsi_signal[1] * 0.25
            if rsi_signal[0] == "LONG":
                score += contribution
            else:  # SHORT
                score -= contribution
            reasoning_parts.append(
                f"RSI {rsi_signal[0]} (貢獻{contribution:+.2f})"
            )
        
        # 3. MACD（權重 25%）
        if macd_signal[0]:
            signals.append(SignalSource.MACD_CROSS)
            contribution = macd_signal[1] * 0.25
            if macd_signal[0] == "LONG":
                score += contribution
            else:  # SHORT
                score -= contribution
            reasoning_parts.append(
                f"MACD {macd_signal[0]} (貢獻{contribution:+.2f})"
            )
        
        # 4. 成交量突增（放大器，10%）
        if volume_spike[0]:
            signals.append(SignalSource.VOLUME_SPIKE)
            score *= 1.1  # 放大現有分數
            reasoning_parts.append(
                f"成交量突增 {volume_spike[1]:.2f}x (×1.1)"
            )
        
        # 決定方向（基於加權分數）
        if len(signals) == 0:
            return HybridSignal(
                signal=SignalType.NEUTRAL,
                confidence=0.0,
                sources=[],
                reasoning="無明確信號",
                funding_rate=row.get('fundingRate'),
                rsi=row.get('rsi')
            )
        
        # 檢查是否必須有 Funding 確認
        if self.require_funding_confirmation and SignalSource.FUNDING_EXTREME not in signals:
            return HybridSignal(
                signal=SignalType.NEUTRAL,
                confidence=0.0,
                sources=signals,
                reasoning="需要 Funding 確認但未滿足",
                funding_rate=row.get('fundingRate'),
                rsi=row.get('rsi')
            )
        
        # 判斷做多還是做空（基於加權分數）
        abs_score = abs(score)
        
        if score >= self.signal_score_threshold:
            direction = SignalType.LONG
            confidence = min(abs_score, 1.0)
        elif score <= -self.signal_score_threshold:
            direction = SignalType.SHORT
            confidence = min(abs_score, 1.0)
        else:
            # 分數不足閾值
            return HybridSignal(
                signal=SignalType.NEUTRAL,
                confidence=abs_score,
                sources=signals,
                reasoning=f"分數不足閾值({score:+.2f} < ±{self.signal_score_threshold}): " + ", ".join(reasoning_parts),
                funding_rate=row.get('fundingRate'),
                rsi=row.get('rsi'),
                macd=row.get('macd'),
                macd_signal_line=row.get('macd_signal'),
                volume_ratio=row.get('volume_ratio')
            )
        
        # 返回最終信號
        return HybridSignal(
            signal=direction,
            confidence=confidence,
            sources=signals,
            reasoning=f"Score={score:+.2f}: " + " + ".join(reasoning_parts),
            funding_signal=funding_signal[0],
            rsi_signal=rsi_signal[0],
            macd_signal=macd_signal[0],
            volume_signal="突增" if volume_spike[0] else None,
            funding_rate=row.get('fundingRate'),
            rsi=row.get('rsi'),
            macd=row.get('macd'),
            macd_signal_line=row.get('macd_signal'),
            volume_ratio=row.get('volume_ratio')
        )
    
    def generate_signal(
        self, 
        df: pd.DataFrame, 
        current_time: Optional[pd.Timestamp] = None
    ) -> HybridSignal:
        """
        生成交易信號
        
        Args:
            df: 歷史 K 線數據（必須包含 fundingRate 欄位）
            current_time: 當前時間（可選）
            
        Returns:
            HybridSignal
        """
        # 計算技術指標
        df = self.calculate_indicators(df)
        
        # 使用最後一根 K 線（注意：df 可能已被切片，使用 -1 確保獲取最後一根）
        row = df.iloc[-1]
        idx = -1  # 對於 check_macd_signal 使用
        
        # 檢查各層信號
        funding_signal = self.check_funding_signal(row)
        rsi_signal = self.check_rsi_signal(row)
        macd_signal = self.check_macd_signal(df, idx)
        volume_spike = self.check_volume_signal(row)
        
        # 組合信號
        final_signal = self.combine_signals(
            funding_signal=funding_signal,
            rsi_signal=rsi_signal,
            macd_signal=macd_signal,
            volume_spike=volume_spike,
            row=row
        )
        
        return final_signal
    
    def _generate_signal_from_row(
        self,
        df: pd.DataFrame,
        idx: int,
        current_time: Optional[pd.Timestamp] = None
    ) -> HybridSignal:
        """
        從已計算指標的 DataFrame 生成信號（優化版本）
        
        Args:
            df: 已包含所有技術指標的 K線數據
            idx: 當前行索引
            current_time: 當前時間
            
        Returns:
            HybridSignal: 混合信號
        """
        # 使用指定索引的數據點
        row = df.iloc[idx]
        
        # 檢查各層信號
        funding_signal = self.check_funding_signal(row)
        rsi_signal = self.check_rsi_signal(row)
        macd_signal = self.check_macd_signal(df, idx)
        volume_spike = self.check_volume_signal(row)
        
        # 組合信號
        final_signal = self.combine_signals(
            funding_signal=funding_signal,
            rsi_signal=rsi_signal,
            macd_signal=macd_signal,
            volume_spike=volume_spike,
            row=row
        )
        
        return final_signal


# 快速測試
if __name__ == "__main__":
    print("="*70)
    print("🧪 混合策略測試")
    print("="*70)
    print()
    
    # 測試參數
    print("策略參數:")
    print("  - Funding 閾值: 0.001")
    print("  - RSI 超買/超賣: 70/30")
    print("  - MACD: 12/26/9")
    print("  - 成交量突增: 2.0x")
    print("  - 最低信心: 0.5")
    print()
    
    print("信號邏輯:")
    print("  1. Funding 極端 + 技術確認 → 高信心 (權重 40%)")
    print("  2. 多指標共振 → 中等信心")
    print("  3. 單一技術信號 → 低信心")
    print()
    
    print("✅ 策略已就緒！")
    print("下一步：運行 Walk-Forward 測試")
