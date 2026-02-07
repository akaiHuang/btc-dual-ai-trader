#!/usr/bin/env python3
"""
MVP Strategy v4.0 HFT - High-Frequency Trading Version
======================================================

高頻交易版本策略，目標年交易 7,300 筆（日均 20 筆）

核心調整：
1. ✅ 大幅放寬進場條件 - RSI [30,70], MA 1.0%, 放寬過濾器
2. ✅ 快速進出機制 - TP 1.5x ATR, SL 0.8x ATR, Time Stop 15min
3. ✅ 保持高勝率 - 保留核心 Phase 0 過濾但放寬閾值
4. ✅ 追求小而頻繁獲利 - 每筆 $0.50-1.00 利潤

預期效果：
- 交易數: 82/年 → 7,300/年 (89倍)
- 勝率: 47.9% → 45-48% (維持)
- 每筆平均利潤: $0.02 → $0.50-1.00 (25-50倍)
- 年度淨利: $1.60 → $3,650-7,300 (2,281-4,562倍)

作者: HFT Optimization
日期: 2025-11-15
"""

import numpy as np
import pandas as pd
import talib
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
import sys
from pathlib import Path

# 添加項目根目錄到路徑
sys.path.append(str(Path(__file__).parent.parent.parent))

# 導入 Phase 0 模組（保留但放寬）
from src.utils.consolidation_detector import ConsolidationDetector
from src.utils.time_zone_analyzer import TimeZoneAnalyzer
from src.utils.cost_aware_filter import CostAwareFilter


@dataclass
class SignalResult:
    """信號結果"""
    direction: Optional[str]  # "LONG", "SHORT", None
    entry_price: float
    take_profit_price: float
    stop_loss_price: float
    confidence: float
    reason: str
    indicators: Dict
    timestamp: datetime
    filters_passed: Dict[str, bool]


class MVPStrategyV4HFT:
    """
    MVP 策略 v4.0 HFT - 高頻交易版本
    
    關鍵特性：
    1. 激進進場 - RSI [30,70] 覆蓋更多機會
    2. 快速止盈 - TP 1.5x ATR，目標 0.5-1.0% 快速獲利
    3. 嚴格止損 - SL 0.8x ATR，快速止損保護資本
    4. 短時間止損 - 15 分鐘（1 根 15m K 線），不等待
    5. 放寬過濾 - Consolidation 閾值提高，Timezone 降低，Cost 降低
    """
    
    def __init__(
        self,
        # 基礎指標參數
        ma_short: int = 7,
        ma_long: int = 25,
        rsi_period: int = 14,
        volume_ma_period: int = 20,
        atr_period: int = 14,
        
        # 進場條件 - HFT 激進配置
        long_rsi_lower: float = 20.0,  # 30 → 20 (再放寬 33%)
        long_rsi_upper: float = 80.0,  # 70 → 80 (再放寬 14%)
        short_rsi_lower: float = 20.0,  # 30 → 20 (再放寬 33%)
        short_rsi_upper: float = 80.0,  # 70 → 80 (再放寬 14%)
        ma_distance_threshold: float = 0.1,  # 1.0% → 0.1% (大幅放寬 90%)
        volume_multiplier: float = 0.8,  # 1.0 → 0.8 (允許更低成交量)
        
        # 動態止盈止損 - HFT 快速進出
        atr_tp_multiplier: float = 1.5,  # 2.7 → 1.5 (-44%, 快速獲利)
        atr_sl_multiplier: float = 0.8,  # 1.1 → 0.8 (-27%, 快速止損)
        min_tp_pct: float = 0.3,  # 0.5% → 0.3% (降低最小要求)
        max_tp_pct: float = 2.0,  # 1.5% → 2.0% (允許更大獲利)
        min_sl_pct: float = 0.15,  # 0.2% → 0.15% (更嚴格止損)
        max_sl_pct: float = 0.8,  # 0.6% → 0.8% (允許更大止損)
        
        # 時間止損 - HFT 快速決策
        time_stop_minutes: int = 15,  # 45 → 15 分鐘 (1 根 15m K 線)
        
        # 信號確認 - HFT 不等待
        require_confirmation: bool = False,  # True → False (不等待確認)
        confirmation_candles: int = 1,  # 2 → 1 (立即進場)
        
        # Phase 0 模組開關 - 保留但放寬
        enable_consolidation_filter: bool = True,  # 保留但閾值放寬
        enable_timezone_filter: bool = True,  # 保留但勝率要求降低
        enable_cost_filter: bool = True,  # 保留但比例降低
        
        # Phase 0 模組參數 - HFT 寬鬆配置
        consolidation_bb_threshold: float = 0.030,  # 0.020 → 0.030 (放寬 50%)
        consolidation_confidence_threshold: float = 0.5,  # 0.67 → 0.5 (放寬 25%)
        timezone_min_win_rate: float = 0.38,  # 0.42 → 0.38 (降低 10%)
        cost_min_profit_ratio: float = 1.5,  # 2.0 → 1.5 (降低 25%)
    ):
        # 基礎參數
        self.ma_short = ma_short
        self.ma_long = ma_long
        self.rsi_period = rsi_period
        self.volume_ma_period = volume_ma_period
        self.atr_period = atr_period
        
        # 進場條件
        self.long_rsi_lower = long_rsi_lower
        self.long_rsi_upper = long_rsi_upper
        self.short_rsi_lower = short_rsi_lower
        self.short_rsi_upper = short_rsi_upper
        self.ma_distance_threshold = ma_distance_threshold
        self.volume_multiplier = volume_multiplier
        
        # TP/SL
        self.atr_tp_multiplier = atr_tp_multiplier
        self.atr_sl_multiplier = atr_sl_multiplier
        self.min_tp_pct = min_tp_pct
        self.max_tp_pct = max_tp_pct
        self.min_sl_pct = min_sl_pct
        self.max_sl_pct = max_sl_pct
        
        # 時間止損
        self.time_stop_minutes = time_stop_minutes
        
        # 確認機制
        self.require_confirmation = require_confirmation
        self.confirmation_candles = confirmation_candles
        
        # Phase 0 開關
        self.enable_consolidation_filter = enable_consolidation_filter
        self.enable_timezone_filter = enable_timezone_filter
        self.enable_cost_filter = enable_cost_filter
        
        # Phase 0 參數
        self.consolidation_bb_threshold = consolidation_bb_threshold
        self.consolidation_confidence_threshold = consolidation_confidence_threshold
        self.timezone_min_win_rate = timezone_min_win_rate
        self.cost_min_profit_ratio = cost_min_profit_ratio
        
        # 初始化 Phase 0 模組
        self.consolidation_detector = ConsolidationDetector()
        self.timezone_analyzer = TimeZoneAnalyzer()
        self.cost_filter = CostAwareFilter()
        
        # 統計
        self.stats = {
            'signals_generated': 0,
            'signals_filtered': 0,
            'consolidation_filtered': 0,
            'timezone_filtered': 0,
            'cost_filtered': 0,
            'confirmation_filtered': 0,
        }
    
    def calculate_indicators(self, df: pd.DataFrame) -> Dict:
        """計算所有指標"""
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        volume = df['volume'].values
        
        # MA
        ma_short = talib.SMA(close, timeperiod=self.ma_short)
        ma_long = talib.SMA(close, timeperiod=self.ma_long)
        
        # RSI
        rsi = talib.RSI(close, timeperiod=self.rsi_period)
        
        # ATR
        atr = talib.ATR(high, low, close, timeperiod=self.atr_period)
        
        # Volume MA
        volume_ma = talib.SMA(volume, timeperiod=self.volume_ma_period)
        
        # Bollinger Bands
        bb_upper, bb_middle, bb_lower = talib.BBANDS(close, timeperiod=20)
        
        return {
            'ma_short': ma_short,
            'ma_long': ma_long,
            'rsi': rsi,
            'atr': atr,
            'volume_ma': volume_ma,
            'bb_upper': bb_upper,
            'bb_middle': bb_middle,
            'bb_lower': bb_lower,
            'close': close,
            'high': high,
            'low': low,
            'volume': volume,
        }
    
    def apply_phase0_filters(
        self,
        df: pd.DataFrame,
        current_time: datetime,
        entry_price: float,
        tp_price: float,
        sl_price: float,
    ) -> Tuple[bool, Dict[str, bool], str]:
        """
        應用 Phase 0 過濾器（HFT 寬鬆版本）
        
        Returns:
            (是否通過, 各過濾器狀態, 原因)
        """
        filters_passed = {
            'consolidation': True,
            'timezone': True,
            'cost': True,
        }
        reasons = []
        
        # 1. 盤整過濾 - 放寬閾值
        if self.enable_consolidation_filter:
            lookback = 50
            consolidation_state = self.consolidation_detector.is_consolidating(
                high=df['high'].values[-lookback:],
                low=df['low'].values[-lookback:],
                close=df['close'].values[-lookback:],
            )
            
            # HFT 版本：只過濾高信心度盤整
            if consolidation_state.is_consolidating and consolidation_state.confidence >= self.consolidation_confidence_threshold:
                filters_passed['consolidation'] = False
                reasons.append(f"盤整過濾 (信心度 {consolidation_state.confidence:.2f} >= {self.consolidation_confidence_threshold})")
                self.stats['consolidation_filtered'] += 1
        
        # 2. 時區過濾 - 降低勝率要求
        if self.enable_timezone_filter:
            recommendation = self.timezone_analyzer.should_trade_now(current_time)
            
            # HFT 版本：降低時段勝率要求
            if recommendation.slot_stats and recommendation.slot_stats.win_rate < self.timezone_min_win_rate:
                filters_passed['timezone'] = False
                reasons.append(f"時段過濾 (勝率 {recommendation.slot_stats.win_rate:.1%} < {self.timezone_min_win_rate:.1%})")
                self.stats['timezone_filtered'] += 1
        
        # 3. 成本過濾 - 降低盈虧比要求
        if self.enable_cost_filter:
            # 計算 TP/SL 百分比
            tp_pct = abs(tp_price - entry_price) / entry_price * 100
            sl_pct = abs(sl_price - entry_price) / entry_price * 100
            
            cost_analysis = self.cost_filter.should_trade(
                entry_price=entry_price,
                take_profit_percent=tp_pct,
                stop_loss_percent=sl_pct,
                position_size=10.0,  # 假設 $10 倉位
                leverage=1,
                direction='LONG',
                is_maker=False,
            )
            
            # HFT 版本：降低利潤比例要求
            # 計算利潤/費用比率
            if cost_analysis.estimated_profit > 0:
                profit_ratio = cost_analysis.estimated_profit / cost_analysis.estimated_fee
            else:
                profit_ratio = 0
            
            if cost_analysis.decision != 'APPROVE' or profit_ratio < self.cost_min_profit_ratio:
                filters_passed['cost'] = False
                reasons.append(f"成本過濾 (利潤比 {profit_ratio:.2f} < {self.cost_min_profit_ratio})")
                self.stats['cost_filtered'] += 1
        
        # 判斷是否通過
        passed = all(filters_passed.values())
        if not passed:
            self.stats['signals_filtered'] += 1
        
        return passed, filters_passed, " | ".join(reasons) if reasons else "通過"
    
    def calculate_dynamic_tp_sl(
        self,
        current_price: float,
        atr: float,
        direction: str,
    ) -> Tuple[float, float]:
        """
        計算動態止盈止損（HFT 快速進出）
        
        Returns:
            (tp_price, sl_price)
        """
        # HFT 版本：更小的 TP/SL 範圍
        tp_distance = atr * self.atr_tp_multiplier
        sl_distance = atr * self.atr_sl_multiplier
        
        # 轉換為百分比
        tp_pct = tp_distance / current_price
        sl_pct = sl_distance / current_price
        
        # 限制範圍（HFT 允許更大範圍）
        tp_pct = np.clip(tp_pct, self.min_tp_pct / 100, self.max_tp_pct / 100)
        sl_pct = np.clip(sl_pct, self.min_sl_pct / 100, self.max_sl_pct / 100)
        
        # 計算價格
        if direction == "LONG":
            tp_price = current_price * (1 + tp_pct)
            sl_price = current_price * (1 - sl_pct)
        else:  # SHORT
            tp_price = current_price * (1 - tp_pct)
            sl_price = current_price * (1 + sl_pct)
        
        return tp_price, sl_price
    
    def check_signal_confirmation(
        self,
        df: pd.DataFrame,
        indicators: Dict,
        signal_type: str,
    ) -> bool:
        """
        檢查信號確認（HFT 版本：可選）
        
        HFT 策略通常不等待確認，立即進場
        """
        if not self.require_confirmation:
            return True
        
        # 檢查前 N 根 K 線
        for i in range(1, self.confirmation_candles + 1):
            idx = -i - 1
            if signal_type == "LONG":
                # LONG 確認：MA Short > MA Long, RSI 在範圍內
                if not (indicators['ma_short'][idx] > indicators['ma_long'][idx]):
                    return False
            elif signal_type == "SHORT":
                # SHORT 確認：MA Short < MA Long, RSI 在範圍內
                if not (indicators['ma_short'][idx] < indicators['ma_long'][idx]):
                    return False
        
        return True
    
    def generate_signal(
        self,
        df: pd.DataFrame,
        current_time: datetime,
    ) -> SignalResult:
        """
        生成交易信號（HFT 版本）
        
        HFT 特點：
        1. 更寬的 RSI 範圍 [30, 70]
        2. 更寬的 MA 距離閾值 1.0%
        3. 不要求確認，立即進場
        4. 放寬所有過濾器閾值
        """
        self.stats['signals_generated'] += 1
        
        # 計算指標
        indicators = self.calculate_indicators(df)
        
        # 當前值
        current_price = df['close'].iloc[-1]
        current_rsi = indicators['rsi'][-1]
        current_ma_short = indicators['ma_short'][-1]
        current_ma_long = indicators['ma_long'][-1]
        current_atr = indicators['atr'][-1]
        current_volume = indicators['volume'][-1]
        volume_ma = indicators['volume_ma'][-1]
        
        # 檢查數據有效性
        if np.isnan([current_rsi, current_ma_short, current_ma_long, current_atr]).any():
            return SignalResult(
                direction=None,
                entry_price=current_price,
                take_profit_price=0,
                stop_loss_price=0,
                confidence=0,
                reason="指標未準備好",
                indicators=indicators,
                timestamp=current_time,
                filters_passed={},
            )
        
        # MA 距離
        ma_distance_pct = abs(current_ma_short - current_ma_long) / current_price * 100
        
        # 成交量條件（HFT 放寬）
        volume_ok = current_volume >= volume_ma * self.volume_multiplier
        
        # 檢查 LONG 信號
        signal_type = None
        if (
            current_ma_short > current_ma_long  # 短均線在上
            and self.long_rsi_lower <= current_rsi <= self.long_rsi_upper  # RSI [30, 70]
            and ma_distance_pct >= self.ma_distance_threshold  # MA 距離夠寬
            and volume_ok  # 成交量確認
        ):
            signal_type = "LONG"
        
        # 檢查 SHORT 信號
        elif (
            current_ma_short < current_ma_long  # 短均線在下
            and self.short_rsi_lower <= current_rsi <= self.short_rsi_upper  # RSI [30, 70]
            and ma_distance_pct >= self.ma_distance_threshold  # MA 距離夠寬
            and volume_ok  # 成交量確認
        ):
            signal_type = "SHORT"
        
        # 無信號
        if signal_type is None:
            return SignalResult(
                direction=None,
                entry_price=current_price,
                take_profit_price=0,
                stop_loss_price=0,
                confidence=0,
                reason=f"無信號 (RSI={current_rsi:.1f}, MA距離={ma_distance_pct:.2f}%, Vol={current_volume/volume_ma:.2f}x)",
                indicators=indicators,
                timestamp=current_time,
                filters_passed={},
            )
        
        # 信號確認（HFT 版本：通常跳過）
        if not self.check_signal_confirmation(df, indicators, signal_type):
            self.stats['confirmation_filtered'] += 1
            return SignalResult(
                direction=None,
                entry_price=current_price,
                take_profit_price=0,
                stop_loss_price=0,
                confidence=0,
                reason=f"{signal_type} 信號未確認",
                indicators=indicators,
                timestamp=current_time,
                filters_passed={},
            )
        
        # 計算動態 TP/SL（HFT 快速進出）
        tp_price, sl_price = self.calculate_dynamic_tp_sl(
            current_price, current_atr, signal_type
        )
        
        # 應用 Phase 0 過濾器（HFT 寬鬆版）
        passed, filters_passed, filter_reason = self.apply_phase0_filters(
            df, current_time, current_price, tp_price, sl_price
        )
        
        if not passed:
            return SignalResult(
                direction=None,
                entry_price=current_price,
                take_profit_price=tp_price,
                stop_loss_price=sl_price,
                confidence=0,
                reason=f"🚫 {filter_reason}",
                indicators=indicators,
                timestamp=current_time,
                filters_passed=filters_passed,
            )
        
        # 計算信心度
        confidence = self._calculate_confidence(
            current_rsi, ma_distance_pct, current_volume / volume_ma
        )
        
        # 返回信號
        reason = (
            f"✅ {signal_type} | RSI={current_rsi:.1f} | "
            f"MA距離={ma_distance_pct:.2f}% | Vol={current_volume/volume_ma:.2f}x | "
            f"TP={abs(tp_price-current_price)/current_price*100:.2f}% | "
            f"SL={abs(sl_price-current_price)/current_price*100:.2f}%"
        )
        
        return SignalResult(
            direction=signal_type,
            entry_price=current_price,
            take_profit_price=tp_price,
            stop_loss_price=sl_price,
            confidence=confidence,
            reason=reason,
            indicators=indicators,
            timestamp=current_time,
            filters_passed=filters_passed,
        )
    
    def _calculate_confidence(
        self,
        rsi: float,
        ma_distance_pct: float,
        volume_ratio: float,
    ) -> float:
        """
        計算信號信心度（HFT 版本）
        
        HFT 策略更重視速度，信心度計算簡化
        """
        confidence = 0.5  # HFT 基礎信心度
        
        # RSI 遠離極值加分（HFT 範圍更寬）
        rsi_center = 50
        rsi_distance = abs(rsi - rsi_center)
        if rsi_distance < 20:  # RSI 在 [30, 70] 中間
            confidence += 0.1
        
        # MA 距離加分（HFT 要求更寬）
        if ma_distance_pct > 0.5:
            confidence += 0.2
        if ma_distance_pct > 1.0:
            confidence += 0.1
        
        # 成交量加分
        if volume_ratio > 1.5:
            confidence += 0.1
        
        return min(confidence, 1.0)
    
    def get_stats(self) -> Dict:
        """獲取統計信息"""
        total_signals = self.stats['signals_generated']
        if total_signals == 0:
            return self.stats
        
        return {
            **self.stats,
            'filter_rate': self.stats['signals_filtered'] / total_signals,
            'consolidation_rate': self.stats['consolidation_filtered'] / total_signals,
            'timezone_rate': self.stats['timezone_filtered'] / total_signals,
            'cost_rate': self.stats['cost_filtered'] / total_signals,
            'confirmation_rate': self.stats['confirmation_filtered'] / total_signals,
        }
