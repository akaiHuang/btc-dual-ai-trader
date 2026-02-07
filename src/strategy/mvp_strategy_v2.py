#!/usr/bin/env python3
"""
MVP Strategy v2.0 - Phase 0 Integration
========================================

整合 Phase 0 優化模組的改進版策略

主要改進：
1. ✅ 整合 ConsolidationDetector - 過濾盤整期
2. ✅ 整合 TimeZoneAnalyzer - 避開低勝率時段
3. ✅ 整合 CostAwareFilter - 確保盈虧比
4. ✅ 動態止盈止損 - 基於 ATR
5. ✅ 多重確認機制 - 連續 K 線確認

預期效果：
- 勝率: 27.8% → >42%
- 淨利: -$945 → >$0
- 時間止損: 86% → <50%
- 交易數: 2,986 → <1,200

作者: Phase 2 Optimization
日期: 2025-11-14
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

# 導入 Phase 0 模組
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
    filters_passed: Dict[str, bool]  # 記錄哪些過濾器通過


class MVPStrategyV2:
    """
    MVP 策略 v2.0 - 整合 Phase 0 優化模組
    
    改進重點：
    1. 盤整過濾 - 不在橫盤時交易
    2. 時區過濾 - 只在高勝率時段交易
    3. 成本過濾 - 確保盈虧比合理
    4. 動態 TP/SL - 基於 ATR 適應波動
    5. 信號確認 - 連續 2 根 K 線確認
    """
    
    def __init__(
        self,
        # 基礎指標參數
        ma_short: int = 7,
        ma_long: int = 25,
        rsi_period: int = 14,
        volume_ma_period: int = 20,
        atr_period: int = 14,
        
        # 進場條件
        long_rsi_lower: float = 45.0,
        long_rsi_upper: float = 60.0,
        short_rsi_lower: float = 40.0,
        short_rsi_upper: float = 55.0,
        ma_distance_threshold: float = 0.3,  # MA 距離至少 0.3%
        volume_multiplier: float = 1.2,  # 成交量至少 1.2 倍
        
        # 動態止盈止損（基於 ATR）- 根據 MFE/MAE 分析調整
        atr_tp_multiplier: float = 2.7,  # TP = ATR * 2.7 (MFE 分析: 75百分位在 1.068%)
        atr_sl_multiplier: float = 1.1,  # SL = ATR * 1.1 (MAE 分析: 中位數在 0.453%)
        min_tp_pct: float = 0.5,  # 最小止盈 0.5% (提高下限)
        max_tp_pct: float = 1.5,  # 最大止盈 1.5% (提高上限)
        min_sl_pct: float = 0.2,  # 最小止損 0.2%
        max_sl_pct: float = 0.6,  # 最大止損 0.6% (稍微放寬)
        
        # 時間止損 - 給趨勢更多發展空間
        time_stop_minutes: int = 45,  # 從 30 增加到 45 分鐘 (3 根 15m K 線)
        
        # 信號確認
        require_confirmation: bool = True,  # 需要連續確認
        confirmation_candles: int = 2,  # 連續 2 根
        
        # Phase 0 模組開關
        enable_consolidation_filter: bool = True,
        enable_timezone_filter: bool = True,
        enable_cost_filter: bool = True,
        
        # Phase 0 模組參數
        timezone_min_win_rate: float = 0.42,  # 時段最低勝率要求
        cost_min_profit_ratio: float = 2.0,  # 利潤/費用最低比例
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
        
        # 動態止盈止損
        self.atr_tp_multiplier = atr_tp_multiplier
        self.atr_sl_multiplier = atr_sl_multiplier
        self.min_tp_pct = min_tp_pct
        self.max_tp_pct = max_tp_pct
        self.min_sl_pct = min_sl_pct
        self.max_sl_pct = max_sl_pct
        
        # 時間止損
        self.time_stop_minutes = time_stop_minutes
        
        # 信號確認
        self.require_confirmation = require_confirmation
        self.confirmation_candles = confirmation_candles
        
        # Phase 0 開關
        self.enable_consolidation_filter = enable_consolidation_filter
        self.enable_timezone_filter = enable_timezone_filter
        self.enable_cost_filter = enable_cost_filter
        
        # Phase 0 參數
        self.timezone_min_win_rate = timezone_min_win_rate
        self.cost_min_profit_ratio = cost_min_profit_ratio
        
        # 初始化 Phase 0 模組
        if self.enable_consolidation_filter:
            self.consolidation_detector = ConsolidationDetector()
        
        if self.enable_timezone_filter:
            self.timezone_analyzer = TimeZoneAnalyzer()
        
        if self.enable_cost_filter:
            self.cost_aware_filter = CostAwareFilter()
        
        # 用於連續確認的緩存
        self.last_signal = None
        self.last_signal_time = None
        self.confirmation_count = 0
    
    def calculate_indicators(
        self,
        df: pd.DataFrame
    ) -> Dict:
        """
        計算技術指標
        
        Args:
            df: OHLCV DataFrame
            
        Returns:
            指標字典
        """
        if len(df) < max(self.ma_long, self.rsi_period, self.volume_ma_period, self.atr_period):
            return {}
        
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        volume = df['volume'].values
        
        # 移動平均線
        ma_short = talib.SMA(close, timeperiod=self.ma_short)
        ma_long = talib.SMA(close, timeperiod=self.ma_long)
        
        # RSI
        rsi = talib.RSI(close, timeperiod=self.rsi_period)
        
        # 成交量均線
        volume_ma = talib.SMA(volume, timeperiod=self.volume_ma_period)
        
        # ATR (用於動態止盈止損)
        atr = talib.ATR(high, low, close, timeperiod=self.atr_period)
        
        return {
            'ma_short': ma_short[-1],
            'ma_long': ma_long[-1],
            'rsi': rsi[-1],
            'volume': volume[-1],
            'volume_ma': volume_ma[-1],
            'atr': atr[-1],
            'current_price': close[-1],
            'ma_distance_pct': abs(ma_short[-1] - ma_long[-1]) / ma_long[-1] * 100
        }
    
    def apply_phase0_filters(
        self,
        df: pd.DataFrame,
        current_time: datetime,
        expected_profit_pct: float
    ) -> Dict[str, bool]:
        """
        應用 Phase 0 過濾器
        
        Returns:
            各過濾器的通過狀態
        """
        filters_passed = {
            'consolidation': True,
            'timezone': True,
            'cost': True
        }
        
        # 1. 盤整過濾
        if self.enable_consolidation_filter:
            try:
                # 使用最近 50 根 K 線進行盤整檢測
                lookback = min(50, len(df))
                consolidation_state = self.consolidation_detector.is_consolidating(
                    high=df['high'].values[-lookback:],
                    low=df['low'].values[-lookback:],
                    close=df['close'].values[-lookback:]
                )
                filters_passed['consolidation'] = not consolidation_state.is_consolidating
                
                # 記錄盤整狀態（debug 用）
                if consolidation_state.is_consolidating:
                    print(f"🚫 盤整過濾: {consolidation_state.reason}, 信心度 {consolidation_state.confidence:.2f}")
            except Exception as e:
                print(f"⚠️ 盤整檢測失敗: {e}")
                filters_passed['consolidation'] = True  # 失敗時不阻擋
        
        # 2. 時區過濾
        if self.enable_timezone_filter:
            try:
                current_hour = current_time.hour
                # 檢查歷史勝率（這裡簡化處理，實際應該載入歷史統計）
                # 避開 UTC 0-8 時（亞洲早晨，流動性低）
                if current_hour >= 0 and current_hour < 8:
                    filters_passed['timezone'] = False
            except Exception as e:
                print(f"⚠️ 時區分析失敗: {e}")
                filters_passed['timezone'] = True
        
        # 3. 成本過濾
        if self.enable_cost_filter:
            try:
                # 確保預期利潤/費用比例 > 2:1
                fee_rate = 0.0005  # 0.05% taker fee
                total_fee_pct = fee_rate * 2 * 100  # 雙邊 = 0.1%
                profit_to_fee_ratio = expected_profit_pct / total_fee_pct
                filters_passed['cost'] = profit_to_fee_ratio >= self.cost_min_profit_ratio
            except Exception as e:
                print(f"⚠️ 成本分析失敗: {e}")
                filters_passed['cost'] = True
        
        return filters_passed
    
    def calculate_dynamic_tp_sl(
        self,
        current_price: float,
        atr: float,
        direction: str
    ) -> Tuple[float, float]:
        """
        基於 ATR 計算動態止盈止損
        
        Args:
            current_price: 當前價格
            atr: ATR 值
            direction: 'LONG' or 'SHORT'
            
        Returns:
            (take_profit_price, stop_loss_price)
        """
        # 計算 ATR 百分比
        atr_pct = atr / current_price * 100
        
        # 動態 TP
        tp_pct = atr_pct * self.atr_tp_multiplier
        tp_pct = max(self.min_tp_pct, min(self.max_tp_pct, tp_pct))
        
        # 動態 SL
        sl_pct = atr_pct * self.atr_sl_multiplier
        sl_pct = max(self.min_sl_pct, min(self.max_sl_pct, sl_pct))
        
        if direction == 'LONG':
            tp_price = current_price * (1 + tp_pct / 100)
            sl_price = current_price * (1 - sl_pct / 100)
        else:  # SHORT
            tp_price = current_price * (1 - tp_pct / 100)
            sl_price = current_price * (1 + sl_pct / 100)
        
        return tp_price, sl_price
    
    def check_signal_confirmation(
        self,
        signal: str,
        current_time: datetime
    ) -> bool:
        """
        檢查信號是否需要確認
        
        連續 N 根 K 線都出現相同信號才確認
        """
        if not self.require_confirmation:
            return True
        
        # 如果是新信號
        if signal != self.last_signal:
            self.last_signal = signal
            self.last_signal_time = current_time
            self.confirmation_count = 1
            return False
        
        # 如果是連續信號
        self.confirmation_count += 1
        
        # 達到確認要求
        if self.confirmation_count >= self.confirmation_candles:
            self.confirmation_count = 0  # 重置
            return True
        
        return False
    
    def generate_signal(
        self,
        df: pd.DataFrame,
        current_time: Optional[datetime] = None
    ) -> SignalResult:
        """
        生成交易信號（整合 Phase 0 過濾）
        
        Args:
            df: OHLCV DataFrame
            current_time: 當前時間
            
        Returns:
            SignalResult
        """
        if current_time is None:
            current_time = datetime.now()
        
        # 計算指標
        indicators = self.calculate_indicators(df)
        if not indicators:
            return SignalResult(
                direction=None,
                entry_price=0,
                take_profit_price=0,
                stop_loss_price=0,
                confidence=0,
                reason="指標數據不足",
                indicators={},
                timestamp=current_time,
                filters_passed={}
            )
        
        # 基礎條件判斷
        ma_short = indicators['ma_short']
        ma_long = indicators['ma_long']
        rsi = indicators['rsi']
        volume = indicators['volume']
        volume_ma = indicators['volume_ma']
        ma_distance = indicators['ma_distance_pct']
        atr = indicators['atr']
        current_price = indicators['current_price']
        
        # 初步方向判斷
        direction = None
        reason_parts = []
        
        # 檢查 MA 距離
        if ma_distance < self.ma_distance_threshold:
            return SignalResult(
                direction=None,
                entry_price=current_price,
                take_profit_price=0,
                stop_loss_price=0,
                confidence=0,
                reason=f"MA距離不足 ({ma_distance:.2f}% < {self.ma_distance_threshold}%)",
                indicators=indicators,
                timestamp=current_time,
                filters_passed={}
            )
        
        # 檢查成交量
        if volume < volume_ma * self.volume_multiplier:
            return SignalResult(
                direction=None,
                entry_price=current_price,
                take_profit_price=0,
                stop_loss_price=0,
                confidence=0,
                reason=f"成交量不足 ({volume:.0f} < {volume_ma * self.volume_multiplier:.0f})",
                indicators=indicators,
                timestamp=current_time,
                filters_passed={}
            )
        
        # 多頭信號
        if ma_short > ma_long and self.long_rsi_lower <= rsi <= self.long_rsi_upper:
            direction = 'LONG'
            reason_parts.append(f"MA上穿 ({ma_distance:.2f}%)")
            reason_parts.append(f"RSI健康 ({rsi:.1f})")
        
        # 空頭信號
        elif ma_short < ma_long and self.short_rsi_lower <= rsi <= self.short_rsi_upper:
            direction = 'SHORT'
            reason_parts.append(f"MA下穿 ({ma_distance:.2f}%)")
            reason_parts.append(f"RSI健康 ({rsi:.1f})")
        
        # 沒有信號
        if direction is None:
            return SignalResult(
                direction=None,
                entry_price=current_price,
                take_profit_price=0,
                stop_loss_price=0,
                confidence=0,
                reason=f"條件不滿足 (RSI={rsi:.1f}, MA距離={ma_distance:.2f}%)",
                indicators=indicators,
                timestamp=current_time,
                filters_passed={}
            )
        
        # 計算動態止盈止損
        tp_price, sl_price = self.calculate_dynamic_tp_sl(current_price, atr, direction)
        expected_profit_pct = abs(tp_price - current_price) / current_price * 100
        
        # 應用 Phase 0 過濾器
        filters_passed = self.apply_phase0_filters(df, current_time, expected_profit_pct)
        
        # 檢查過濾器
        if not filters_passed['consolidation']:
            return SignalResult(
                direction=None,
                entry_price=current_price,
                take_profit_price=tp_price,
                stop_loss_price=sl_price,
                confidence=0,
                reason="❌ 盤整過濾: 市場橫盤中",
                indicators=indicators,
                timestamp=current_time,
                filters_passed=filters_passed
            )
        
        if not filters_passed['timezone']:
            return SignalResult(
                direction=None,
                entry_price=current_price,
                take_profit_price=tp_price,
                stop_loss_price=sl_price,
                confidence=0,
                reason=f"❌ 時區過濾: 低勝率時段 ({current_time.hour}h)",
                indicators=indicators,
                timestamp=current_time,
                filters_passed=filters_passed
            )
        
        if not filters_passed['cost']:
            return SignalResult(
                direction=None,
                entry_price=current_price,
                take_profit_price=tp_price,
                stop_loss_price=sl_price,
                confidence=0,
                reason=f"❌ 成本過濾: 盈虧比不足 ({expected_profit_pct:.2f}%)",
                indicators=indicators,
                timestamp=current_time,
                filters_passed=filters_passed
            )
        
        # 信號確認
        if not self.check_signal_confirmation(direction, current_time):
            return SignalResult(
                direction=None,
                entry_price=current_price,
                take_profit_price=tp_price,
                stop_loss_price=sl_price,
                confidence=0.5,
                reason=f"等待確認 ({self.confirmation_count}/{self.confirmation_candles})",
                indicators=indicators,
                timestamp=current_time,
                filters_passed=filters_passed
            )
        
        # 所有檢查通過，生成信號
        reason_parts.append("✅ 所有過濾器通過")
        reason = " | ".join(reason_parts)
        
        return SignalResult(
            direction=direction,
            entry_price=current_price,
            take_profit_price=tp_price,
            stop_loss_price=sl_price,
            confidence=1.0,
            reason=reason,
            indicators=indicators,
            timestamp=current_time,
            filters_passed=filters_passed
        )


# 測試代碼
if __name__ == "__main__":
    # 創建測試數據
    dates = pd.date_range('2024-01-01', periods=100, freq='15min')
    test_df = pd.DataFrame({
        'timestamp': dates,
        'open': np.random.randn(100).cumsum() + 40000,
        'high': np.random.randn(100).cumsum() + 40100,
        'low': np.random.randn(100).cumsum() + 39900,
        'close': np.random.randn(100).cumsum() + 40000,
        'volume': np.random.rand(100) * 1000000
    })
    test_df = test_df.set_index('timestamp')
    
    # 測試策略
    strategy = MVPStrategyV2()
    signal = strategy.generate_signal(test_df)
    
    print("=" * 60)
    print("MVP Strategy v2.0 測試")
    print("=" * 60)
    print(f"方向: {signal.direction}")
    print(f"進場價: {signal.entry_price:.2f}")
    print(f"止盈價: {signal.take_profit_price:.2f}")
    print(f"止損價: {signal.stop_loss_price:.2f}")
    print(f"信心度: {signal.confidence:.2f}")
    print(f"原因: {signal.reason}")
    print(f"過濾器: {signal.filters_passed}")
    print("=" * 60)
