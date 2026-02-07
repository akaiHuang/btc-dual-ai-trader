"""
Scalping Strategy V1 - 事件動能極短線策略

專門捕捉 0.1-0.2% 的極短波動
觸發條件：Funding/OI 暴動、清算連鎖、巨鯨異動、新聞衝擊
持倉時間：1-3 分鐘
目標頻率：10-20 筆/天
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, List
from dataclasses import dataclass
from enum import Enum

from src.core.signal_context import SignalContext, Direction, ImpactLevel


class ScalpTrigger(Enum):
    """Scalping 觸發類型"""
    FUNDING_EXPLOSION = "funding_explosion"       # Funding 暴動（爆多單/擠空）
    LIQUIDATION_CASCADE = "liquidation_cascade"   # 清算連鎖
    WHALE_SHOCK = "whale_shock"                   # 巨鯨異動
    NEWS_SHOCK = "news_shock"                     # 新聞衝擊
    OI_SPIKE = "oi_spike"                         # OI 暴增/暴減
    TAPE_AGGRESSION = "tape_aggression"           # 成交流異常攻擊性


@dataclass
class ScalpSignal:
    """Scalping 信號"""
    timestamp: datetime
    direction: Direction          # LONG / SHORT
    trigger_type: ScalpTrigger    # 觸發類型
    confidence: float             # 信心度 (0-1)
    
    entry_price: float
    tp_price: float               # Take Profit 價格
    sl_price: float               # Stop Loss 價格
    
    tp_pct: float = 0.0015        # TP 百分比（默認 0.15%）
    sl_pct: float = 0.001         # SL 百分比（默認 0.1%）
    
    time_stop_seconds: int = 180  # 時間止損（默認 3 分鐘）
    leverage: int = 15            # 槓桿
    
    reason: str = ""              # 觸發原因詳細描述
    context: Optional[SignalContext] = None


class ScalpStrategyV1:
    """
    Scalping 策略 V1 - 事件驅動極短線
    
    核心理念：
    1. 不是 24/7 開槍，而是等待「事件觸發」
    2. 每筆只吃 0.1-0.2%，但槓桿後是 2-4%
    3. 嚴格時間止損 1-3 分鐘
    4. 4 種觸發模式互相獨立
    """
    
    def __init__(
        self,
        # 基礎參數
        timeframe: str = "1m",
        tp_pct: float = 0.0015,      # 0.15%
        sl_pct: float = 0.001,        # 0.1%
        time_stop_seconds: int = 180, # 3 分鐘
        
        # 觸發閾值
        funding_threshold: float = 0.05,      # Funding > 0.05% 才觸發
        oi_change_threshold: float = 0.15,    # OI 變化 > 15% 才觸發
        liquidation_threshold: float = 1000,  # 清算量 > 1000 BTC
        whale_threshold: float = 2000,        # 巨鯨轉移 > 2000 BTC
        
        # 槓桿設定
        default_leverage: int = 15,
        high_confidence_leverage: int = 20,
        low_confidence_leverage: int = 10,
        
        # 其他
        min_confidence: float = 0.6,  # 最低信心度閾值
    ):
        self.timeframe = timeframe
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.time_stop_seconds = time_stop_seconds
        
        self.funding_threshold = funding_threshold
        self.oi_change_threshold = oi_change_threshold
        self.liquidation_threshold = liquidation_threshold
        self.whale_threshold = whale_threshold
        
        self.default_leverage = default_leverage
        self.high_confidence_leverage = high_confidence_leverage
        self.low_confidence_leverage = low_confidence_leverage
        
        self.min_confidence = min_confidence
    
    def should_activate(self, context: SignalContext) -> bool:
        """
        檢查是否應該啟用 Scalping 策略
        只在事件發生時啟用
        """
        return any([
            self._check_funding_explosion(context),
            self._check_liquidation_cascade(context),
            self._check_whale_shock(context),
            self._check_news_shock(context),
            self._check_oi_spike(context),
            self._check_tape_aggression(context),
        ])
    
    def generate_signal(
        self,
        df: pd.DataFrame,
        context: SignalContext
    ) -> Optional[ScalpSignal]:
        """
        生成 Scalping 信號
        
        Args:
            df: K 線數據（1m 或 5m）
            context: 信號上下文（包含 L0-L3 所有數據）
        
        Returns:
            ScalpSignal 或 None
        """
        # 檢查是否應該啟用
        if not self.should_activate(context):
            return None
        
        # 依次檢查各種觸發模式
        signals = []
        
        # 1. Funding 爆倉模式
        signal = self._funding_explosion_signal(context)
        if signal:
            signals.append(signal)
        
        # 2. 清算連鎖模式
        signal = self._liquidation_cascade_signal(context)
        if signal:
            signals.append(signal)
        
        # 3. 巨鯨異動模式
        signal = self._whale_shock_signal(context)
        if signal:
            signals.append(signal)
        
        # 4. 新聞衝擊模式
        signal = self._news_shock_signal(context)
        if signal:
            signals.append(signal)
        
        # 5. OI 暴動模式
        signal = self._oi_spike_signal(context)
        if signal:
            signals.append(signal)
        
        # 6. 成交流攻擊模式
        signal = self._tape_aggression_signal(context)
        if signal:
            signals.append(signal)
        
        # 如果有多個信號，選擇信心度最高的
        if not signals:
            return None
        
        best_signal = max(signals, key=lambda s: s.confidence)
        
        # 信心度低於閾值，不交易
        if best_signal.confidence < self.min_confidence:
            return None
        
        return best_signal
    
    # ========== 觸發條件檢查 ==========
    
    def _check_funding_explosion(self, context: SignalContext) -> bool:
        """檢查 Funding 是否異常"""
        return abs(context.funding_rate) > self.funding_threshold
    
    def _check_liquidation_cascade(self, context: SignalContext) -> bool:
        """檢查是否有大量清算"""
        return context.recent_liquidations_volume > self.liquidation_threshold
    
    def _check_whale_shock(self, context: SignalContext) -> bool:
        """檢查巨鯨活動"""
        return (
            context.whale_alert_level in [ImpactLevel.HIGH, ImpactLevel.MEDIUM] or
            abs(context.net_flow) > self.whale_threshold
        )
    
    def _check_news_shock(self, context: SignalContext) -> bool:
        """檢查新聞衝擊"""
        return (
            context.news_impact_level == ImpactLevel.HIGH and
            context.news_strength > 0.7
        )
    
    def _check_oi_spike(self, context: SignalContext) -> bool:
        """檢查 OI 暴動"""
        return abs(context.oi_change_rate) > self.oi_change_threshold
    
    def _check_tape_aggression(self, context: SignalContext) -> bool:
        """檢查成交流攻擊性"""
        return (
            context.taker_ratio > 1.5 or  # 強勢買入
            context.taker_ratio < 0.67    # 強勢賣出
        )
    
    # ========== 信號生成 ==========
    
    def _funding_explosion_signal(self, context: SignalContext) -> Optional[ScalpSignal]:
        """
        🔻 空頭爆多單模式 / 🔺 多頭擠空模式
        
        邏輯：
        - Funding 極正 + OI 高位 + 跌破清算區 → 做空（爆多單）
        - Funding 極負 + OI 上升 + 突破清算區 → 做多（擠空）
        """
        if not self._check_funding_explosion(context):
            return None
        
        # 🔻 空頭爆多單
        if (context.funding_rate > self.funding_threshold and
            context.oi_at_high_level and
            context.price_breaks_long_liq_zone and
            context.tape_shows_aggressive_sell()):
            
            confidence = self._calculate_confidence([
                context.funding_rate / self.funding_threshold,  # Funding 強度
                1.0 if context.oi_at_high_level else 0.5,
                1.0 if context.price_breaks_long_liq_zone else 0.0,
                min(context.taker_ratio / 0.67, 1.0) if context.taker_ratio < 1.0 else 0.5,
            ])
            
            leverage = self._get_leverage(confidence)
            
            return ScalpSignal(
                timestamp=context.timestamp,
                direction=Direction.SHORT,
                trigger_type=ScalpTrigger.FUNDING_EXPLOSION,
                confidence=confidence,
                entry_price=context.current_price,
                tp_price=context.current_price * (1 - self.tp_pct),
                sl_price=context.current_price * (1 + self.sl_pct),
                tp_pct=self.tp_pct,
                sl_pct=self.sl_pct,
                time_stop_seconds=self.time_stop_seconds,
                leverage=leverage,
                reason=f"Funding {context.funding_rate:.4f} > {self.funding_threshold}, OI 高位, 爆多單清算",
                context=context
            )
        
        # 🔺 多頭擠空
        elif (context.funding_rate < -self.funding_threshold and
              context.oi_change_rate > 0.1 and  # OI 快速上升
              context.price_breaks_short_liq_zone):
            
            confidence = self._calculate_confidence([
                abs(context.funding_rate) / self.funding_threshold,
                min(context.oi_change_rate / 0.2, 1.0),
                1.0 if context.price_breaks_short_liq_zone else 0.0,
                min(context.taker_ratio / 1.5, 1.0) if context.taker_ratio > 1.0 else 0.5,
            ])
            
            leverage = self._get_leverage(confidence)
            
            return ScalpSignal(
                timestamp=context.timestamp,
                direction=Direction.LONG,
                trigger_type=ScalpTrigger.FUNDING_EXPLOSION,
                confidence=confidence,
                entry_price=context.current_price,
                tp_price=context.current_price * (1 + self.tp_pct),
                sl_price=context.current_price * (1 - self.sl_pct),
                tp_pct=self.tp_pct,
                sl_pct=self.sl_pct,
                time_stop_seconds=self.time_stop_seconds,
                leverage=leverage,
                reason=f"Funding {context.funding_rate:.4f} < -{self.funding_threshold}, 擠空開始",
                context=context
            )
        
        return None
    
    def _liquidation_cascade_signal(self, context: SignalContext) -> Optional[ScalpSignal]:
        """
        清算連鎖反應模式
        
        邏輯：
        - 大量多單被清算 → 做空（追殺）
        - 大量空單被清算 → 做多（追漲）
        """
        if not self._check_liquidation_cascade(context):
            return None
        
        # 清算方向判斷
        if context.liquidation_direction == Direction.NEUTRAL:
            return None
        
        # 做反方向
        if context.liquidation_direction == Direction.LONG:
            # 多單被清算 → 做空
            direction = Direction.SHORT
            tp_price = context.current_price * (1 - self.tp_pct)
            sl_price = context.current_price * (1 + self.sl_pct)
            reason_suffix = "多單清算連鎖"
        else:
            # 空單被清算 → 做多
            direction = Direction.LONG
            tp_price = context.current_price * (1 + self.tp_pct)
            sl_price = context.current_price * (1 - self.sl_pct)
            reason_suffix = "空單清算連鎖"
        
        confidence = self._calculate_confidence([
            min(context.recent_liquidations_volume / (self.liquidation_threshold * 2), 1.0),
            0.8 if context.obi * (1 if direction == Direction.LONG else -1) > 0 else 0.5,
            0.8,  # 清算連鎖本身就是高信心信號
        ])
        
        leverage = self._get_leverage(confidence)
        
        return ScalpSignal(
            timestamp=context.timestamp,
            direction=direction,
            trigger_type=ScalpTrigger.LIQUIDATION_CASCADE,
            confidence=confidence,
            entry_price=context.current_price,
            tp_price=tp_price,
            sl_price=sl_price,
            tp_pct=self.tp_pct,
            sl_pct=self.sl_pct,
            time_stop_seconds=self.time_stop_seconds,
            leverage=leverage,
            reason=f"清算量 {context.recent_liquidations_volume:.0f} BTC, {reason_suffix}",
            context=context
        )
    
    def _whale_shock_signal(self, context: SignalContext) -> Optional[ScalpSignal]:
        """
        巨鯨異動模式
        
        邏輯：
        - 大量流入交易所 → 做空（拋壓）
        - 大量流出交易所 → 做多（持有）
        """
        if not self._check_whale_shock(context):
            return None
        
        # 根據鏈上流向決定方向
        if context.net_flow > self.whale_threshold:
            # 流入交易所 → 拋壓
            direction = Direction.SHORT
            tp_price = context.current_price * (1 - self.tp_pct * 1.3)  # TP 稍微放大
            sl_price = context.current_price * (1 + self.sl_pct * 1.2)
            reason = f"巨鯨流入交易所 {context.net_flow:.0f} BTC"
            
        elif context.net_flow < -self.whale_threshold:
            # 流出交易所 → 持有看漲
            direction = Direction.LONG
            tp_price = context.current_price * (1 + self.tp_pct * 1.3)
            sl_price = context.current_price * (1 - self.sl_pct * 1.2)
            reason = f"巨鯨流出交易所 {abs(context.net_flow):.0f} BTC"
        else:
            return None
        
        confidence = self._calculate_confidence([
            min(abs(context.net_flow) / (self.whale_threshold * 3), 1.0),
            0.7 if context.whale_alert_level == ImpactLevel.HIGH else 0.5,
            0.6,  # 鏈上數據有延遲，稍微降低信心度
        ])
        
        leverage = self._get_leverage(confidence)
        
        return ScalpSignal(
            timestamp=context.timestamp,
            direction=direction,
            trigger_type=ScalpTrigger.WHALE_SHOCK,
            confidence=confidence,
            entry_price=context.current_price,
            tp_price=tp_price,
            sl_price=sl_price,
            tp_pct=self.tp_pct * 1.3,  # 巨鯨影響較大，TP 放大
            sl_pct=self.sl_pct * 1.2,
            time_stop_seconds=self.time_stop_seconds,
            leverage=leverage,
            reason=reason,
            context=context
        )
    
    def _news_shock_signal(self, context: SignalContext) -> Optional[ScalpSignal]:
        """
        新聞衝擊模式
        
        邏輯：
        - 高衝擊利多 + 資金流確認 → 做多
        - 高衝擊利空 + 資金流確認 → 做空
        """
        if not self._check_news_shock(context):
            return None
        
        # 方向由新聞決定
        if context.news_bias == 1:
            direction = Direction.LONG
            tp_price = context.current_price * (1 + self.tp_pct * 1.5)  # 新聞波動大，TP 放大
            sl_price = context.current_price * (1 - self.sl_pct * 1.5)
        elif context.news_bias == -1:
            direction = Direction.SHORT
            tp_price = context.current_price * (1 - self.tp_pct * 1.5)
            sl_price = context.current_price * (1 + self.sl_pct * 1.5)
        else:
            return None
        
        # 需要資金流確認
        flow_confirm = (
            (direction == Direction.LONG and context.taker_ratio > 1.2) or
            (direction == Direction.SHORT and context.taker_ratio < 0.83)
        )
        
        if not flow_confirm:
            return None
        
        confidence = self._calculate_confidence([
            context.news_strength,
            0.9 if flow_confirm else 0.3,
            0.8,  # 新聞衝擊通常有效
        ])
        
        leverage = self._get_leverage(confidence)
        
        return ScalpSignal(
            timestamp=context.timestamp,
            direction=direction,
            trigger_type=ScalpTrigger.NEWS_SHOCK,
            confidence=confidence,
            entry_price=context.current_price,
            tp_price=tp_price,
            sl_price=sl_price,
            tp_pct=self.tp_pct * 1.5,  # 新聞波動大
            sl_pct=self.sl_pct * 1.5,
            time_stop_seconds=self.time_stop_seconds,
            leverage=leverage,
            reason=f"新聞衝擊 {context.news_factor.tags if context.news_factor else 'N/A'}, 強度 {context.news_strength:.2f}",
            context=context
        )
    
    def _oi_spike_signal(self, context: SignalContext) -> Optional[ScalpSignal]:
        """
        OI 暴動模式
        
        邏輯：
        - OI 急增 + 價格上漲 → 做多（新多頭）
        - OI 急增 + 價格下跌 → 做空（新空頭）
        - OI 急減 → 平倉潮，暫不交易
        """
        if not self._check_oi_spike(context):
            return None
        
        # OI 減少暫不交易（平倉潮，方向不明）
        if context.oi_change_rate < 0:
            return None
        
        # OI 增加，判斷方向
        # 需要結合價格趨勢和資金流
        if context.taker_ratio > 1.3:
            # 主動買入強 → 做多
            direction = Direction.LONG
            tp_price = context.current_price * (1 + self.tp_pct)
            sl_price = context.current_price * (1 - self.sl_pct)
            reason = "OI 急增 + 主動買入強"
        elif context.taker_ratio < 0.77:
            # 主動賣出強 → 做空
            direction = Direction.SHORT
            tp_price = context.current_price * (1 - self.tp_pct)
            sl_price = context.current_price * (1 + self.sl_pct)
            reason = "OI 急增 + 主動賣出強"
        else:
            return None
        
        confidence = self._calculate_confidence([
            min(context.oi_change_rate / 0.3, 1.0),
            min(abs(1 - context.taker_ratio) / 0.5, 1.0),
            0.7,
        ])
        
        leverage = self._get_leverage(confidence)
        
        return ScalpSignal(
            timestamp=context.timestamp,
            direction=direction,
            trigger_type=ScalpTrigger.OI_SPIKE,
            confidence=confidence,
            entry_price=context.current_price,
            tp_price=tp_price,
            sl_price=sl_price,
            tp_pct=self.tp_pct,
            sl_pct=self.sl_pct,
            time_stop_seconds=self.time_stop_seconds,
            leverage=leverage,
            reason=reason,
            context=context
        )
    
    def _tape_aggression_signal(self, context: SignalContext) -> Optional[ScalpSignal]:
        """
        成交流攻擊模式
        
        邏輯：
        - 連續主動買入 + OBI 正 → 做多
        - 連續主動賣出 + OBI 負 → 做空
        """
        if not self._check_tape_aggression(context):
            return None
        
        # 需要訂單簿和成交流同向確認
        if context.taker_ratio > 1.5 and context.obi > 0.3:
            direction = Direction.LONG
            tp_price = context.current_price * (1 + self.tp_pct * 0.8)  # TP 稍微保守
            sl_price = context.current_price * (1 - self.sl_pct)
            reason = f"主動買入 {context.taker_ratio:.2f}, OBI {context.obi:.2f}"
            
        elif context.taker_ratio < 0.67 and context.obi < -0.3:
            direction = Direction.SHORT
            tp_price = context.current_price * (1 - self.tp_pct * 0.8)
            sl_price = context.current_price * (1 + self.sl_pct)
            reason = f"主動賣出 {context.taker_ratio:.2f}, OBI {context.obi:.2f}"
        else:
            return None
        
        confidence = self._calculate_confidence([
            min(abs(1 - context.taker_ratio) / 0.7, 1.0),
            min(abs(context.obi) / 0.7, 1.0),
            0.65,  # 成交流信號相對弱，信心度稍低
        ])
        
        leverage = self._get_leverage(confidence)
        
        return ScalpSignal(
            timestamp=context.timestamp,
            direction=direction,
            trigger_type=ScalpTrigger.TAPE_AGGRESSION,
            confidence=confidence,
            entry_price=context.current_price,
            tp_price=tp_price,
            sl_price=sl_price,
            tp_pct=self.tp_pct * 0.8,
            sl_pct=self.sl_pct,
            time_stop_seconds=self.time_stop_seconds,
            leverage=leverage,
            reason=reason,
            context=context
        )
    
    # ========== 輔助方法 ==========
    
    def _calculate_confidence(self, factors: List[float]) -> float:
        """
        計算綜合信心度
        
        Args:
            factors: 各個因子的評分 (0-1)
        
        Returns:
            綜合信心度 (0-1)
        """
        # 加權平均
        weights = np.ones(len(factors)) / len(factors)
        confidence = np.average(factors, weights=weights)
        
        # 限制範圍
        return max(0.0, min(1.0, confidence))
    
    def _get_leverage(self, confidence: float) -> int:
        """根據信心度決定槓桿"""
        if confidence >= 0.8:
            return self.high_confidence_leverage
        elif confidence >= 0.65:
            return self.default_leverage
        else:
            return self.low_confidence_leverage
    
    def __repr__(self) -> str:
        return (
            f"ScalpStrategyV1("
            f"timeframe={self.timeframe}, "
            f"tp={self.tp_pct:.2%}, sl={self.sl_pct:.2%}, "
            f"time_stop={self.time_stop_seconds}s)"
        )


# ========== 輔助方法（SignalContext 擴展）==========

def tape_shows_aggressive_sell(context: SignalContext) -> bool:
    """判斷成交流是否顯示主動賣出"""
    return context.taker_ratio < 0.8


def tape_shows_aggressive_buy(context: SignalContext) -> bool:
    """判斷成交流是否顯示主動買入"""
    return context.taker_ratio > 1.2


# 添加到 SignalContext
SignalContext.tape_shows_aggressive_sell = tape_shows_aggressive_sell
SignalContext.tape_shows_aggressive_buy = tape_shows_aggressive_buy


# ========== 測試示例 ==========

if __name__ == "__main__":
    from datetime import datetime
    
    # 創建策略
    strategy = ScalpStrategyV1(
        tp_pct=0.0015,
        sl_pct=0.001,
        time_stop_seconds=180,
        min_confidence=0.6
    )
    
    print(f"策略配置: {strategy}")
    print()
    
    # 測試場景 1: Funding 爆多單
    print("=" * 60)
    print("測試場景 1: Funding 爆多單")
    print("=" * 60)
    
    context1 = SignalContext(
        timestamp=datetime.now(),
        current_price=42000.0,
        funding_rate=0.08,  # 極高 Funding
        oi_at_high_level=True,
        price_breaks_long_liq_zone=True,
        taker_ratio=0.6,  # 主動賣出
        obi=-0.3,
    )
    
    signal1 = strategy.generate_signal(pd.DataFrame(), context1)
    if signal1:
        print(f"✅ 生成信號:")
        print(f"   方向: {signal1.direction.value}")
        print(f"   觸發: {signal1.trigger_type.value}")
        print(f"   信心度: {signal1.confidence:.2%}")
        print(f"   槓桿: {signal1.leverage}x")
        print(f"   進場: ${signal1.entry_price:.2f}")
        print(f"   TP: ${signal1.tp_price:.2f} ({signal1.tp_pct:.2%})")
        print(f"   SL: ${signal1.sl_price:.2f} ({signal1.sl_pct:.2%})")
        print(f"   原因: {signal1.reason}")
    else:
        print("❌ 未生成信號")
    
    print()
    
    # 測試場景 2: 巨鯨流入
    print("=" * 60)
    print("測試場景 2: 巨鯨大量流入交易所")
    print("=" * 60)
    
    context2 = SignalContext(
        timestamp=datetime.now(),
        current_price=42000.0,
        net_flow=3000,  # 3000 BTC 流入
        whale_alert_level=ImpactLevel.HIGH,
        taker_ratio=0.7,
    )
    
    signal2 = strategy.generate_signal(pd.DataFrame(), context2)
    if signal2:
        print(f"✅ 生成信號:")
        print(f"   方向: {signal2.direction.value}")
        print(f"   觸發: {signal2.trigger_type.value}")
        print(f"   信心度: {signal2.confidence:.2%}")
        print(f"   槓桿: {signal2.leverage}x")
        print(f"   原因: {signal2.reason}")
    else:
        print("❌ 未生成信號")
    
    print()
    print("✅ ScalpStrategyV1 測試完成！")
