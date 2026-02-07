"""
🐋 主力策略識別系統 (Whale Strategy Detector) v2.0
===================================================

基於您的設計文檔，實現主力策略識別與預測

主力策略類型：
1. 吸籌建倉 (ACCUMULATION) - 低位隱蔽大量買入，為拉升準備
2. 誘空吸籌 (BEAR_TRAP) - 製造空頭陷阱，誘使散戶拋售
3. 誘多派發 (BULL_TRAP) - 製造多頭陷阱，誘使散戶追高
4. 拉高出貨 (PUMP_DUMP) - 快速拉抬後高位出貨
5. 洗盤震倉 (SHAKE_OUT) - 震盪清洗意志不堅定籌碼
6. 試盤探測 (TESTING) - 小幅拉升/打壓試探市場
7. 對敲拉抬 (WASH_TRADING) - 自買自賣製造成交假象
8. 砸盤打壓 (DUMP) - 高位大手筆拋售打壓價格
9. 正常波動 (NORMAL)

v2.0 更新：
- 新增：支撐/壓力突破檢測器
- 新增：K線形態分析器（長上下影、針狀K線）
- 新增：成交量衰竭檢測
- 新增：訂單簿隱藏大單檢測
- 新增：價量背離分析
- 增強：各策略特徵信號量化

Author: AI Trading System
Created: 2025-11-25
Updated: 2025-11-25
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
from collections import deque
from datetime import datetime
import numpy as np
import json
import statistics


class WhaleStrategy(Enum):
    """主力策略類型"""
    ACCUMULATION = "吸籌建倉"      # 低位隱蔽買入
    BEAR_TRAP = "誘空吸籌"         # 假跌破誘空
    BULL_TRAP = "誘多派發"         # 假突破出貨
    PUMP_DUMP = "拉高出貨"         # 快速拉升後出貨
    SHAKE_OUT = "洗盤震倉"         # 震盪清洗籌碼
    TESTING = "試盤探測"           # 試探市場反應
    WASH_TRADING = "對敲拉抬"      # 自買自賣製造量
    DUMP = "砸盤打壓"              # 大手筆拋售
    NORMAL = "正常波動"            # 無明顯主力行為


@dataclass
class StrategyProbability:
    """策略機率分布"""
    strategy: WhaleStrategy
    probability: float
    confidence: float
    signals: List[str]
    
    def to_dict(self) -> Dict:
        return {
            "strategy": self.strategy.value,
            "probability": round(self.probability, 3),
            "confidence": round(self.confidence, 3),
            "signals": self.signals
        }


@dataclass
class WhaleRetailConflict:
    """主力 vs 散戶對峙狀態"""
    whale_direction: str           # "BULLISH" / "BEARISH" / "NEUTRAL"
    retail_direction: str          # "BULLISH" / "BEARISH" / "NEUTRAL"
    conflict_level: float          # 0-1, 對峙程度
    likely_winner: str             # "WHALE" / "RETAIL" / "UNCERTAIN"
    reasoning: str


@dataclass
class StrategyPrediction:
    """策略預測結果"""
    timestamp: str
    current_price: float
    
    # 策略識別
    detected_strategy: WhaleStrategy
    strategy_probabilities: List[StrategyProbability]
    
    # 對峙狀態
    conflict_state: WhaleRetailConflict
    
    # 下一步預測
    predicted_action: str          # "PUMP" / "DUMP" / "CONSOLIDATE" / "BREAKOUT"
    predicted_price_target: float
    prediction_confidence: float
    expected_timeframe_minutes: int
    
    # 信號特徵
    key_signals: List[str]
    risk_warnings: List[str]


class ChipConcentrationCalculator:
    """
    籌碼集中度計算器
    衡量籌碼從散戶向主力集中的程度
    """
    
    def __init__(self, history_size: int = 100):
        self.large_trade_history: deque = deque(maxlen=history_size)
        self.small_trade_history: deque = deque(maxlen=history_size)
        
    def add_trade(self, volume_usdt: float, is_buy: bool, timestamp: float):
        """記錄交易，區分大單和小單"""
        trade = {
            "volume": volume_usdt,
            "is_buy": is_buy,
            "timestamp": timestamp
        }
        
        # 大單門檻：10000 USDT
        if volume_usdt >= 10000:
            self.large_trade_history.append(trade)
        else:
            self.small_trade_history.append(trade)
    
    def calculate_concentration(self) -> Tuple[float, str]:
        """
        計算籌碼集中度
        
        Returns:
            (concentration_score, interpretation)
            score: 0-1, 越高表示籌碼越集中於主力
        """
        if not self.large_trade_history or not self.small_trade_history:
            return 0.5, "數據不足"
        
        # 計算大單淨買入
        large_buy = sum(t["volume"] for t in self.large_trade_history if t["is_buy"])
        large_sell = sum(t["volume"] for t in self.large_trade_history if not t["is_buy"])
        large_net = large_buy - large_sell
        large_total = large_buy + large_sell
        
        # 計算小單淨買入
        small_buy = sum(t["volume"] for t in self.small_trade_history if t["is_buy"])
        small_sell = sum(t["volume"] for t in self.small_trade_history if not t["is_buy"])
        small_net = small_buy - small_sell
        small_total = small_buy + small_sell
        
        # 集中度 = 大單佔比 + 大單與小單方向差異
        if large_total + small_total == 0:
            return 0.5, "無交易"
        
        # 大單佔比 (0-1)
        large_ratio = large_total / (large_total + small_total)
        
        # 方向差異 (如果大單買小單賣，集中度高)
        if large_total > 0 and small_total > 0:
            large_direction = large_net / large_total  # -1 to 1
            small_direction = small_net / small_total  # -1 to 1
            direction_divergence = (large_direction - small_direction) / 2  # -1 to 1
        else:
            direction_divergence = 0
        
        # 綜合得分
        concentration = 0.5 + (large_ratio - 0.5) * 0.5 + direction_divergence * 0.5
        concentration = max(0, min(1, concentration))
        
        # 解讀
        if concentration > 0.7:
            interpretation = "籌碼高度集中，主力吸籌明顯"
        elif concentration > 0.55:
            interpretation = "籌碼略微集中，主力可能在布局"
        elif concentration < 0.3:
            interpretation = "籌碼分散，主力可能在派發"
        elif concentration < 0.45:
            interpretation = "籌碼略分散，注意主力動向"
        else:
            interpretation = "籌碼分布正常"
        
        return concentration, interpretation


class StopHuntDetector:
    """
    止損掃蕩檢測器
    檢測異常的長上下影線（主力掃損行為）
    """
    
    def __init__(self, atr_multiplier: float = 2.0, lookback: int = 20):
        self.atr_multiplier = atr_multiplier
        self.lookback = lookback
        self.candle_history: deque = deque(maxlen=lookback * 2)
        
    def add_candle(self, open_: float, high: float, low: float, close: float, volume: float):
        """添加K線數據"""
        self.candle_history.append({
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "body": abs(close - open_),
            "upper_shadow": high - max(open_, close),
            "lower_shadow": min(open_, close) - low,
            "total_range": high - low
        })
    
    def calculate_stop_hunt_index(self) -> Tuple[float, List[str]]:
        """
        計算止損掃蕩指數
        
        Returns:
            (index, signals)
            index: 0-100, 越高越可能是主力掃損
        """
        if len(self.candle_history) < self.lookback:
            return 0, ["數據不足"]
        
        recent = list(self.candle_history)[-self.lookback:]
        
        # 計算 ATR
        ranges = [c["total_range"] for c in recent]
        atr = np.mean(ranges)
        
        if atr == 0:
            return 0, ["ATR為零"]
        
        signals = []
        hunt_score = 0
        
        for i, candle in enumerate(recent):
            # 檢測長下影線（下方掃損）
            if candle["lower_shadow"] > self.atr_multiplier * atr:
                lower_ratio = candle["lower_shadow"] / candle["total_range"] if candle["total_range"] > 0 else 0
                if lower_ratio > 0.6:  # 下影線佔比 > 60%
                    hunt_score += 20
                    signals.append(f"K線{i}: 長下影線掃損 (影線佔比{lower_ratio:.0%})")
            
            # 檢測長上影線（上方掃損）
            if candle["upper_shadow"] > self.atr_multiplier * atr:
                upper_ratio = candle["upper_shadow"] / candle["total_range"] if candle["total_range"] > 0 else 0
                if upper_ratio > 0.6:  # 上影線佔比 > 60%
                    hunt_score += 20
                    signals.append(f"K線{i}: 長上影線掃損 (影線佔比{upper_ratio:.0%})")
            
            # 檢測針狀K線（上下都長）
            if (candle["upper_shadow"] > atr and 
                candle["lower_shadow"] > atr and
                candle["body"] < atr * 0.5):
                hunt_score += 30
                signals.append(f"K線{i}: 針狀K線，雙向掃損")
        
        # 歸一化到 0-100
        hunt_index = min(100, hunt_score)
        
        return hunt_index, signals


class SupportResistanceBreakDetector:
    """
    🆕 支撐/壓力突破檢測器
    檢測假突破和真突破，識別誘空/誘多陷阱
    """
    
    def __init__(self, lookback: int = 50):
        self.price_history: deque = deque(maxlen=lookback * 2)
        self.volume_history: deque = deque(maxlen=lookback * 2)
        self.lookback = lookback
        
    def add_price(self, price: float, volume: float, timestamp: float):
        """添加價格和成交量數據"""
        self.price_history.append({
            "price": price,
            "volume": volume,
            "timestamp": timestamp
        })
        self.volume_history.append(volume)
    
    def detect_support_resistance(self) -> Tuple[List[float], List[float]]:
        """
        檢測支撐和壓力位
        
        Returns:
            (supports, resistances)
        """
        if len(self.price_history) < self.lookback:
            return [], []
        
        prices = [p["price"] for p in self.price_history]
        
        # 簡單方法：找局部高低點
        supports = []
        resistances = []
        
        for i in range(5, len(prices) - 5):
            # 局部最低點 = 支撐
            if prices[i] == min(prices[i-5:i+6]):
                supports.append(prices[i])
            # 局部最高點 = 壓力
            if prices[i] == max(prices[i-5:i+6]):
                resistances.append(prices[i])
        
        # 合併相近的點
        supports = self._merge_levels(supports, tolerance_pct=0.5)
        resistances = self._merge_levels(resistances, tolerance_pct=0.5)
        
        return supports, resistances
    
    def _merge_levels(self, levels: List[float], tolerance_pct: float = 0.5) -> List[float]:
        """合併相近的支撐/壓力位"""
        if not levels:
            return []
        
        levels = sorted(levels)
        merged = [levels[0]]
        
        for level in levels[1:]:
            if abs(level - merged[-1]) / merged[-1] * 100 < tolerance_pct:
                # 合併：取平均
                merged[-1] = (merged[-1] + level) / 2
            else:
                merged.append(level)
        
        return merged
    
    def detect_breakout(self, current_price: float, current_volume: float) -> Dict:
        """
        檢測突破/跌破行為
        
        Returns:
            {
                "type": "RESISTANCE_BREAK" / "SUPPORT_BREAK" / "NONE",
                "level": 價位,
                "is_likely_fake": bool,  # 是否可能是假突破
                "signals": []
            }
        """
        supports, resistances = self.detect_support_resistance()
        
        if not supports and not resistances:
            return {"type": "NONE", "level": 0, "is_likely_fake": False, "signals": ["數據不足"]}
        
        result = {"type": "NONE", "level": 0, "is_likely_fake": False, "signals": []}
        
        # 計算平均成交量
        avg_volume = np.mean(list(self.volume_history)) if self.volume_history else 0
        
        # 檢查壓力突破
        for resistance in sorted(resistances, reverse=True):
            if current_price > resistance * 1.002:  # 突破 0.2%
                result["type"] = "RESISTANCE_BREAK"
                result["level"] = resistance
                
                # 判斷是否假突破
                # 假突破特徵：量能不足
                if current_volume < avg_volume * 1.3:
                    result["is_likely_fake"] = True
                    result["signals"].append("⚠️ 突破量能不足，可能是假突破")
                else:
                    result["signals"].append("✅ 突破伴隨放量")
                break
        
        # 檢查支撐跌破
        for support in sorted(supports):
            if current_price < support * 0.998:  # 跌破 0.2%
                result["type"] = "SUPPORT_BREAK"
                result["level"] = support
                
                # 判斷是否假跌破
                if current_volume < avg_volume * 1.3:
                    result["is_likely_fake"] = True
                    result["signals"].append("⚠️ 跌破量能不足，可能是誘空")
                else:
                    result["signals"].append("✅ 跌破伴隨放量")
                break
        
        return result


class VolumeExhaustionDetector:
    """
    🆕 成交量衰竭檢測器
    檢測趨勢末端的成交量萎縮，識別拉高出貨/砸盤結束
    """
    
    def __init__(self, lookback: int = 20):
        self.volume_history: deque = deque(maxlen=lookback)
        self.price_direction_history: deque = deque(maxlen=lookback)
        
    def add_data(self, volume: float, price_change_pct: float):
        """添加成交量和價格變化"""
        self.volume_history.append(volume)
        direction = 1 if price_change_pct > 0 else (-1 if price_change_pct < 0 else 0)
        self.price_direction_history.append(direction)
    
    def detect_exhaustion(self) -> Tuple[bool, str, List[str]]:
        """
        檢測成交量衰竭
        
        Returns:
            (is_exhausted, trend_direction, signals)
            trend_direction: "UP" / "DOWN" / "NONE"
        """
        if len(self.volume_history) < 10:
            return False, "NONE", ["數據不足"]
        
        volumes = list(self.volume_history)
        directions = list(self.price_direction_history)
        
        # 計算近期趨勢方向
        recent_directions = directions[-10:]
        up_count = sum(1 for d in recent_directions if d > 0)
        down_count = sum(1 for d in recent_directions if d < 0)
        
        if up_count > 6:
            trend = "UP"
        elif down_count > 6:
            trend = "DOWN"
        else:
            trend = "NONE"
        
        signals = []
        
        # 檢測成交量遞減
        # 將成交量分為前半和後半
        first_half = volumes[:len(volumes)//2]
        second_half = volumes[len(volumes)//2:]
        
        avg_first = np.mean(first_half)
        avg_second = np.mean(second_half)
        
        is_exhausted = False
        
        if avg_first > 0:
            volume_decay_ratio = avg_second / avg_first
            
            if volume_decay_ratio < 0.6:  # 成交量萎縮超過 40%
                is_exhausted = True
                signals.append(f"📉 成交量萎縮 {(1-volume_decay_ratio)*100:.0f}%")
                
                if trend == "UP":
                    signals.append("⚠️ 上漲趨勢中成交量萎縮，可能見頂")
                elif trend == "DOWN":
                    signals.append("💡 下跌趨勢中成交量萎縮，賣壓衰竭")
        
        return is_exhausted, trend, signals


class HiddenOrderDetector:
    """
    🆕 隱藏大單檢測器
    檢測訂單簿中的隱藏性大單（冰山單）
    """
    
    def __init__(self, history_size: int = 30):
        self.orderbook_snapshots: deque = deque(maxlen=history_size)
        self.execution_history: deque = deque(maxlen=100)
        
    def add_orderbook_snapshot(self, bids: List[List[float]], asks: List[List[float]], timestamp: float):
        """
        添加訂單簿快照
        bids/asks: [[price, volume], ...]
        """
        self.orderbook_snapshots.append({
            "bids": bids[:10],  # 只保留前10檔
            "asks": asks[:10],
            "timestamp": timestamp
        })
    
    def add_execution(self, price: float, volume: float, is_buy: bool):
        """記錄成交"""
        self.execution_history.append({
            "price": price,
            "volume": volume,
            "is_buy": is_buy,
            "timestamp": datetime.now().timestamp()
        })
    
    def detect_hidden_orders(self) -> Dict:
        """
        檢測隱藏大單
        
        特徵：
        1. 某價位被反覆吃掉但掛單量不減少（冰山單）
        2. 成交量遠大於顯示的掛單量
        
        Returns:
            {
                "hidden_bid_detected": bool,
                "hidden_ask_detected": bool,
                "hidden_bid_level": price,
                "hidden_ask_level": price,
                "signals": []
            }
        """
        result = {
            "hidden_bid_detected": False,
            "hidden_ask_detected": False,
            "hidden_bid_level": 0,
            "hidden_ask_level": 0,
            "signals": []
        }
        
        if len(self.orderbook_snapshots) < 5 or len(self.execution_history) < 10:
            result["signals"].append("數據不足")
            return result
        
        # 分析買單方隱藏單
        bid_levels = {}
        for snapshot in self.orderbook_snapshots:
            for bid in snapshot["bids"][:5]:
                price, volume = bid[0], bid[1]
                price_key = round(price, 0)
                if price_key not in bid_levels:
                    bid_levels[price_key] = []
                bid_levels[price_key].append(volume)
        
        # 找出掛單量穩定但被大量吃掉的價位
        for price_key, volumes in bid_levels.items():
            if len(volumes) >= 5:
                # 掛單量變化不大但持續存在 = 可能是冰山單
                volume_std = np.std(volumes)
                volume_mean = np.mean(volumes)
                if volume_mean > 0 and volume_std / volume_mean < 0.3:  # 變異係數 < 30%
                    # 檢查是否有大量成交
                    executions_at_level = [
                        e["volume"] for e in self.execution_history 
                        if round(e["price"], 0) == price_key and e["is_buy"]
                    ]
                    total_executed = sum(executions_at_level)
                    if total_executed > volume_mean * 3:  # 成交量遠大於顯示量
                        result["hidden_bid_detected"] = True
                        result["hidden_bid_level"] = price_key
                        result["signals"].append(f"💰 發現隱藏買單 @${price_key:.0f} (累計吃貨 {total_executed:.2f})")
        
        # 類似分析賣單方
        ask_levels = {}
        for snapshot in self.orderbook_snapshots:
            for ask in snapshot["asks"][:5]:
                price, volume = ask[0], ask[1]
                price_key = round(price, 0)
                if price_key not in ask_levels:
                    ask_levels[price_key] = []
                ask_levels[price_key].append(volume)
        
        for price_key, volumes in ask_levels.items():
            if len(volumes) >= 5:
                volume_std = np.std(volumes)
                volume_mean = np.mean(volumes)
                if volume_mean > 0 and volume_std / volume_mean < 0.3:
                    executions_at_level = [
                        e["volume"] for e in self.execution_history 
                        if round(e["price"], 0) == price_key and not e["is_buy"]
                    ]
                    total_executed = sum(executions_at_level)
                    if total_executed > volume_mean * 3:
                        result["hidden_ask_detected"] = True
                        result["hidden_ask_level"] = price_key
                        result["signals"].append(f"📤 發現隱藏賣單 @${price_key:.0f} (累計出貨 {total_executed:.2f})")
        
        return result


class PriceVolumeDivergenceDetector:
    """
    🆕 價量背離檢測器
    檢測價格與成交量的背離，識別主力意圖
    """
    
    def __init__(self, lookback: int = 20):
        self.price_history: deque = deque(maxlen=lookback)
        self.volume_history: deque = deque(maxlen=lookback)
        
    def add_data(self, price: float, volume: float):
        """添加價格和成交量"""
        self.price_history.append(price)
        self.volume_history.append(volume)
    
    def detect_divergence(self) -> Dict:
        """
        檢測價量背離
        
        類型：
        1. 量價背離（多頭）：價格新低但成交量萎縮 → 可能見底
        2. 量價背離（空頭）：價格新高但成交量萎縮 → 可能見頂
        3. 量增價平：成交量放大但價格不動 → 主力吸籌/派發
        
        Returns:
            {
                "divergence_type": "BULLISH" / "BEARISH" / "ACCUMULATION" / "DISTRIBUTION" / "NONE",
                "strength": 0-1,
                "signals": []
            }
        """
        if len(self.price_history) < 10:
            return {"divergence_type": "NONE", "strength": 0, "signals": ["數據不足"]}
        
        prices = list(self.price_history)
        volumes = list(self.volume_history)
        
        # 分為前後兩段
        mid = len(prices) // 2
        first_prices = prices[:mid]
        second_prices = prices[mid:]
        first_volumes = volumes[:mid]
        second_volumes = volumes[mid:]
        
        signals = []
        divergence_type = "NONE"
        strength = 0
        
        # 計算價格趨勢
        price_change = (np.mean(second_prices) - np.mean(first_prices)) / np.mean(first_prices) * 100
        
        # 計算成交量趨勢
        volume_change = (np.mean(second_volumes) - np.mean(first_volumes)) / np.mean(first_volumes) * 100 if np.mean(first_volumes) > 0 else 0
        
        # 價格下跌但成交量萎縮 → 多頭背離
        if price_change < -0.5 and volume_change < -20:
            divergence_type = "BULLISH"
            strength = min(1, abs(price_change) * 0.1 + abs(volume_change) * 0.02)
            signals.append(f"📈 多頭背離：價跌{price_change:.1f}%，量縮{volume_change:.0f}% → 賣壓衰竭")
        
        # 價格上漲但成交量萎縮 → 空頭背離
        elif price_change > 0.5 and volume_change < -20:
            divergence_type = "BEARISH"
            strength = min(1, abs(price_change) * 0.1 + abs(volume_change) * 0.02)
            signals.append(f"📉 空頭背離：價漲{price_change:.1f}%，量縮{volume_change:.0f}% → 追漲乏力")
        
        # 價格橫盤但成交量放大 → 主力動作
        elif abs(price_change) < 0.3 and volume_change > 50:
            # 判斷是吸籌還是派發需要看大單方向，這裡簡化處理
            divergence_type = "ACCUMULATION"
            strength = min(1, volume_change * 0.01)
            signals.append(f"🔄 量增價平：成交量放大{volume_change:.0f}%，價格僅變化{price_change:.1f}%")
            signals.append("💡 主力可能正在吸籌或派發，需結合大單方向判斷")
        
        return {
            "divergence_type": divergence_type,
            "strength": strength,
            "signals": signals
        }


class WaterfallDropDetector:
    """
    🆕 瀑布式下跌檢測器
    檢測砸盤打壓的特徵：連續大陰線、天量後萎縮
    """
    
    def __init__(self, lookback: int = 10):
        self.candle_history: deque = deque(maxlen=lookback)
        
    def add_candle(self, open_: float, high: float, low: float, close: float, volume: float):
        """添加K線"""
        self.candle_history.append({
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "is_bearish": close < open_,
            "body_pct": abs(close - open_) / open_ * 100 if open_ > 0 else 0
        })
    
    def detect_waterfall(self) -> Dict:
        """
        檢測瀑布式下跌
        
        Returns:
            {
                "is_waterfall": bool,
                "consecutive_bearish": int,
                "total_drop_pct": float,
                "volume_pattern": "CLIMAX" / "EXHAUSTING" / "NORMAL",
                "signals": []
            }
        """
        if len(self.candle_history) < 5:
            return {
                "is_waterfall": False,
                "consecutive_bearish": 0,
                "total_drop_pct": 0,
                "volume_pattern": "NORMAL",
                "signals": ["數據不足"]
            }
        
        candles = list(self.candle_history)
        signals = []
        
        # 計算連續陰線數
        consecutive_bearish = 0
        for c in reversed(candles):
            if c["is_bearish"]:
                consecutive_bearish += 1
            else:
                break
        
        # 計算總跌幅
        if candles[0]["open"] > 0:
            total_drop_pct = (candles[-1]["close"] - candles[0]["open"]) / candles[0]["open"] * 100
        else:
            total_drop_pct = 0
        
        # 分析成交量模式
        volumes = [c["volume"] for c in candles]
        first_half_vol = np.mean(volumes[:len(volumes)//2])
        second_half_vol = np.mean(volumes[len(volumes)//2:])
        
        if first_half_vol > 0:
            vol_ratio = second_half_vol / first_half_vol
            if vol_ratio < 0.5:
                volume_pattern = "EXHAUSTING"  # 放量後萎縮
            elif vol_ratio > 2:
                volume_pattern = "CLIMAX"  # 恐慌放量
            else:
                volume_pattern = "NORMAL"
        else:
            volume_pattern = "NORMAL"
        
        # 判斷是否瀑布式下跌
        is_waterfall = consecutive_bearish >= 3 and total_drop_pct < -2
        
        if is_waterfall:
            signals.append(f"🌊 瀑布式下跌：連續{consecutive_bearish}根陰線，跌幅{total_drop_pct:.1f}%")
            if volume_pattern == "EXHAUSTING":
                signals.append("📉 成交量萎縮，砸盤力度減弱，可能接近尾聲")
            elif volume_pattern == "CLIMAX":
                signals.append("⚠️ 恐慌性放量，可能還有下跌空間")
        
        return {
            "is_waterfall": is_waterfall,
            "consecutive_bearish": consecutive_bearish,
            "total_drop_pct": total_drop_pct,
            "volume_pattern": volume_pattern,
            "signals": signals
        }


class OrderFlowDistortionDetector:
    """
    委託異動率檢測器
    檢測 Spoofing 等操縱行為
    """
    
    def __init__(self, history_size: int = 60):
        self.order_events: deque = deque(maxlen=history_size)
        
    def add_order_event(self, event_type: str, price: float, volume: float, 
                        duration_seconds: float, was_filled: bool):
        """
        記錄委託事件
        
        event_type: "ADD" / "CANCEL" / "MODIFY"
        """
        self.order_events.append({
            "type": event_type,
            "price": price,
            "volume": volume,
            "duration": duration_seconds,
            "filled": was_filled,
            "timestamp": datetime.now().timestamp()
        })
    
    def calculate_distortion_index(self) -> Tuple[float, List[str]]:
        """
        計算委託異動率
        
        Returns:
            (index, signals)
            index: 0-100, 越高越可能有操縱
        """
        if len(self.order_events) < 10:
            return 0, ["數據不足"]
        
        signals = []
        distortion_score = 0
        
        # 統計快速撤單（持續時間 < 5秒且未成交）
        quick_cancels = [e for e in self.order_events 
                        if e["type"] == "CANCEL" 
                        and e["duration"] < 5 
                        and not e["filled"]]
        
        quick_cancel_ratio = len(quick_cancels) / len(self.order_events)
        if quick_cancel_ratio > 0.3:
            distortion_score += 40
            signals.append(f"快速撤單率高: {quick_cancel_ratio:.0%}")
        
        # 統計大單快速撤單
        large_quick_cancels = [e for e in quick_cancels if e["volume"] > 10000]
        if len(large_quick_cancels) > 3:
            distortion_score += 30
            signals.append(f"大單快速撤單: {len(large_quick_cancels)}筆 (可能Spoofing)")
        
        # 統計同一價位反覆掛撤
        from collections import Counter
        price_events = Counter(round(e["price"], 0) for e in self.order_events)
        repeated_prices = [p for p, c in price_events.items() if c > 5]
        if repeated_prices:
            distortion_score += 30
            signals.append(f"同價位反覆掛撤: {len(repeated_prices)}個價位")
        
        return min(100, distortion_score), signals


class WhalePressureIndex:
    """
    主力買賣壓強度指數 (WPI)
    量化主力在當前市場的買入或賣出壓力
    """
    
    def __init__(self):
        self.large_trades: deque = deque(maxlen=100)  # 大單交易記錄
        self.orderbook_snapshots: deque = deque(maxlen=20)  # 訂單簿快照
        
    def add_large_trade(self, volume_usdt: float, is_buy: bool):
        """記錄大額成交"""
        self.large_trades.append({
            "volume": volume_usdt,
            "is_buy": is_buy,
            "timestamp": datetime.now().timestamp()
        })
    
    def add_orderbook_snapshot(self, large_bid_volume: float, large_ask_volume: float):
        """記錄訂單簿大單掛單量"""
        self.orderbook_snapshots.append({
            "bid": large_bid_volume,
            "ask": large_ask_volume,
            "timestamp": datetime.now().timestamp()
        })
    
    def calculate_wpi(self) -> Tuple[float, str]:
        """
        計算主力壓強度指數
        
        Returns:
            (wpi, interpretation)
            wpi: -1 到 +1
                 > 0: 主力買壓
                 < 0: 主力賣壓
        """
        if not self.large_trades:
            return 0, "無大單數據"
        
        # 計算大單淨成交
        buy_volume = sum(t["volume"] for t in self.large_trades if t["is_buy"])
        sell_volume = sum(t["volume"] for t in self.large_trades if not t["is_buy"])
        total_volume = buy_volume + sell_volume
        
        if total_volume == 0:
            return 0, "無交易量"
        
        trade_wpi = (buy_volume - sell_volume) / total_volume
        
        # 結合訂單簿掛單
        if self.orderbook_snapshots:
            recent_ob = list(self.orderbook_snapshots)[-5:]
            avg_bid = np.mean([s["bid"] for s in recent_ob])
            avg_ask = np.mean([s["ask"] for s in recent_ob])
            
            if avg_bid + avg_ask > 0:
                orderbook_wpi = (avg_bid - avg_ask) / (avg_bid + avg_ask)
            else:
                orderbook_wpi = 0
            
            # 綜合 (成交 60%, 掛單 40%)
            wpi = trade_wpi * 0.6 + orderbook_wpi * 0.4
        else:
            wpi = trade_wpi
        
        # 解讀
        if wpi > 0.5:
            interpretation = "主力強力買入，可能準備拉升"
        elif wpi > 0.2:
            interpretation = "主力溫和買入"
        elif wpi < -0.5:
            interpretation = "主力強力賣出，可能準備打壓"
        elif wpi < -0.2:
            interpretation = "主力溫和賣出"
        else:
            interpretation = "主力動向不明確"
        
        return wpi, interpretation


class WhaleStrategyDetector:
    """
    🐋 主力策略識別系統 v2.0
    
    整合所有指標，識別當前主力策略並預測下一步行為
    
    v2.0 增強：
    - 支撐/壓力突破檢測
    - 價量背離分析
    - 成交量衰竭檢測
    - 隱藏大單檢測
    - 瀑布式下跌檢測
    """
    
    def __init__(self):
        # 原有子指標計算器
        self.chip_calculator = ChipConcentrationCalculator()
        self.stop_hunt_detector = StopHuntDetector()
        self.order_flow_detector = OrderFlowDistortionDetector()
        self.wpi_calculator = WhalePressureIndex()
        
        # 🆕 新增檢測器
        self.sr_break_detector = SupportResistanceBreakDetector()
        self.volume_exhaustion_detector = VolumeExhaustionDetector()
        self.hidden_order_detector = HiddenOrderDetector()
        self.pv_divergence_detector = PriceVolumeDivergenceDetector()
        self.waterfall_detector = WaterfallDropDetector()
        
        # 歷史記錄
        self.prediction_history: deque = deque(maxlen=100)
        self.validation_results: deque = deque(maxlen=100)
        
        # 策略識別參數
        self.strategy_signatures = self._init_strategy_signatures()
    
    def _init_strategy_signatures(self) -> Dict[WhaleStrategy, Dict]:
        """初始化各策略的特徵簽名（更新版）"""
        return {
            WhaleStrategy.ACCUMULATION: {
                "description": "低位隱蔽大量買入，為後續拉升準備",
                "obi_range": (-0.3, 0.3),           # OBI 中性或略正
                "vpin_range": (0.0, 0.4),           # VPIN 低（非知情交易）
                "wpi_range": (0.1, 0.6),            # 主力淨買入
                "chip_concentration_range": (0.55, 1.0),  # 籌碼集中
                "price_change_range": (-0.5, 0.5),  # 價格橫盤或緩漲
                "volume_characteristic": "底部放量但價格緩漲",
                "key_signals": ["大單買入頻繁", "量價背離(價平量增)"]
            },
            WhaleStrategy.BEAR_TRAP: {
                "description": "製造空頭陷阱，誘使散戶拋售，主力趁低吸籌",
                "obi_range": (-0.5, 0.1),           # 先看空後反轉
                "vpin_range": (0.3, 0.7),           # 中等知情交易
                "wpi_range": (0.2, 0.8),            # 跌破時主力買入
                "stop_hunt_index_min": 40,          # 有掃損跡象
                "price_pattern": "跌破支撐後快速拉回",
                "key_signals": ["跌破支撐後反彈", "長下影線刺穿", "下方隱藏買單"]
            },
            WhaleStrategy.BULL_TRAP: {
                "description": "製造多頭陷阱，誘使散戶追高，主力趁高出貨",
                "obi_range": (-0.1, 0.5),           # 先看多後反轉
                "vpin_range": (0.3, 0.7),           # 中等知情交易
                "wpi_range": (-0.8, -0.2),          # 突破時主力賣出
                "stop_hunt_index_min": 40,          # 有掃損跡象
                "price_pattern": "突破壓力後快速回落",
                "key_signals": ["突破量能不足", "長上影線假突破", "高位賣單積累"]
            },
            WhaleStrategy.PUMP_DUMP: {
                "description": "快速拉抬價格吸引跟風盤，隨後高位出貨",
                "obi_range": (0.3, 1.0),            # 強烈買盤假象
                "vpin_range": (0.5, 1.0),           # 高知情交易
                "wpi_range": (-0.5, 0.5),           # 主力實際在出貨
                "volume_spike": True,               # 巨量
                "price_pattern": "快速拉升後暴跌",
                "key_signals": ["巨量急拉", "短時間漲幅驚人", "成交量先增後衰"]
            },
            WhaleStrategy.SHAKE_OUT: {
                "description": "震盪下跌清洗意志不堅定籌碼，為下一波拉升清除阻力",
                "obi_range": (-0.4, 0.4),           # 反覆震盪
                "vpin_range": (0.2, 0.5),           # 中等毒性
                "stop_hunt_index_min": 60,          # 高掃損
                "chip_concentration_range": (0.5, 0.8),
                "price_pattern": "寬幅震盪，長上下影",
                "key_signals": ["多日來回拉鋸", "長上下影線頻繁", "下跌量縮(賣壓衰竭)", "低位隱藏買單"]
            },
            WhaleStrategy.TESTING: {
                "description": "小幅拉升或打壓試探市場拋壓和多空力量",
                "volume_characteristic": "成交量有限",
                "price_pattern": "短暫拉高/打壓後回歸原位",
                "duration": "短暫 (< 5分鐘)",
                "key_signals": ["突發短暫拉高/砸盤", "量能有限", "價格迅速回歸", "大單快閃掛撤"]
            },
            WhaleStrategy.WASH_TRADING: {
                "description": "自買自賣製造成交假象，拉抬價格吸引注意",
                "order_flow_distortion_min": 50,    # 高異動率
                "volume_characteristic": "量大但無淨流入",
                "vpin_range": (0.0, 0.3),           # 低毒性（假交易）
                "key_signals": ["量大但無持續淨流入", "同價位連續對敲成交"]
            },
            WhaleStrategy.DUMP: {
                "description": "高位連續大手筆拋售，造成價格暴跌",
                "obi_range": (-1.0, -0.3),          # 強烈賣盤
                "vpin_range": (0.4, 1.0),           # 高知情交易
                "wpi_range": (-1.0, -0.4),          # 主力大量賣出
                "price_pattern": "連續大陰線快速下跌",
                "key_signals": ["瀑布式下跌", "天量後縮量", "買單快速後撤"]
            }
        }
    
    def analyze(
        self,
        obi: float,
        vpin: float,
        current_price: float,
        price_change_pct: float,
        volume_ratio: float,
        whale_net_qty: float,
        funding_rate: float,
        liquidation_pressure_long: float,
        liquidation_pressure_short: float,
        recent_candles: List[Dict] = None,
        orderbook_snapshot: Dict = None,
        current_volume: float = 0  # 🆕 當前成交量
    ) -> StrategyPrediction:
        """
        分析當前市場狀況，識別主力策略 (v2.0 增強版)
        
        Args:
            obi: 訂單簿失衡 (-1 to 1)
            vpin: 知情交易機率 (0 to 1)
            current_price: 當前價格
            price_change_pct: 價格變化百分比
            volume_ratio: 成交量比率 (相對平均)
            whale_net_qty: 鯨魚淨買入量 (BTC)
            funding_rate: 資金費率
            liquidation_pressure_long: 多頭爆倉壓力
            liquidation_pressure_short: 空頭爆倉壓力
            recent_candles: 近期K線數據
            orderbook_snapshot: 訂單簿快照
            current_volume: 當前成交量 (USDT)
        """
        
        # 1. 更新原有子指標
        if recent_candles:
            for c in recent_candles[-20:]:
                self.stop_hunt_detector.add_candle(
                    c.get("open", 0), c.get("high", 0), 
                    c.get("low", 0), c.get("close", 0),
                    c.get("volume", 0)
                )
                # 🆕 更新瀑布檢測器
                self.waterfall_detector.add_candle(
                    c.get("open", 0), c.get("high", 0),
                    c.get("low", 0), c.get("close", 0),
                    c.get("volume", 0)
                )
                # 🆕 更新價量背離檢測器
                if c.get("close", 0) > 0 and c.get("open", 0) > 0:
                    pct_change = (c["close"] - c["open"]) / c["open"] * 100
                    self.pv_divergence_detector.add_data(c["close"], c.get("volume", 0))
                    self.volume_exhaustion_detector.add_data(c.get("volume", 0), pct_change)
        
        # 🆕 更新支撐/壓力檢測器
        if current_price > 0:
            self.sr_break_detector.add_price(current_price, current_volume, datetime.now().timestamp())
        
        # 🆕 更新訂單簿隱藏單檢測器
        if orderbook_snapshot:
            bids = orderbook_snapshot.get("bids", [])
            asks = orderbook_snapshot.get("asks", [])
            if bids and asks:
                self.hidden_order_detector.add_orderbook_snapshot(
                    bids, asks, datetime.now().timestamp()
                )
        
        # 2. 計算原有專用指標
        wpi, wpi_interpretation = self.wpi_calculator.calculate_wpi()
        chip_concentration, chip_interpretation = self.chip_calculator.calculate_concentration()
        stop_hunt_index, stop_hunt_signals = self.stop_hunt_detector.calculate_stop_hunt_index()
        order_distortion, distortion_signals = self.order_flow_detector.calculate_distortion_index()
        
        # 🆕 3. 計算新增指標
        breakout_info = self.sr_break_detector.detect_breakout(current_price, current_volume)
        volume_exhausted, exhaust_trend, exhaust_signals = self.volume_exhaustion_detector.detect_exhaustion()
        hidden_order_info = self.hidden_order_detector.detect_hidden_orders()
        divergence_info = self.pv_divergence_detector.detect_divergence()
        waterfall_info = self.waterfall_detector.detect_waterfall()
        
        # 4. 計算各策略的機率 (v2.0 增強版)
        strategy_scores = self._calculate_strategy_scores_v2(
            obi=obi,
            vpin=vpin,
            wpi=wpi,
            chip_concentration=chip_concentration,
            stop_hunt_index=stop_hunt_index,
            order_distortion=order_distortion,
            price_change_pct=price_change_pct,
            volume_ratio=volume_ratio,
            whale_net_qty=whale_net_qty,
            funding_rate=funding_rate,
            # 🆕 新增指標
            breakout_info=breakout_info,
            volume_exhausted=volume_exhausted,
            exhaust_trend=exhaust_trend,
            hidden_order_info=hidden_order_info,
            divergence_info=divergence_info,
            waterfall_info=waterfall_info
        )
        
        # 5. 轉換為機率分布
        strategy_probs = self._scores_to_probabilities(strategy_scores)
        
        # 6. 判斷主力 vs 散戶對峙
        conflict_state = self._analyze_conflict(
            wpi=wpi,
            obi=obi,
            liquidation_pressure_long=liquidation_pressure_long,
            liquidation_pressure_short=liquidation_pressure_short,
            funding_rate=funding_rate
        )
        
        # 7. 預測下一步行為
        detected_strategy = max(strategy_probs, key=lambda x: x.probability)
        prediction = self._predict_next_action(
            detected_strategy=detected_strategy.strategy,
            conflict_state=conflict_state,
            current_price=current_price,
            wpi=wpi,
            vpin=vpin
        )
        
        # 8. 生成關鍵信號和風險警告 (v2.0 增強版)
        key_signals, risk_warnings = self._generate_signals_and_warnings_v2(
            strategy_probs=strategy_probs,
            conflict_state=conflict_state,
            vpin=vpin,
            stop_hunt_index=stop_hunt_index,
            order_distortion=order_distortion,
            # 🆕 新增指標
            breakout_info=breakout_info,
            volume_exhausted=volume_exhausted,
            exhaust_signals=exhaust_signals,
            hidden_order_info=hidden_order_info,
            divergence_info=divergence_info,
            waterfall_info=waterfall_info
        )
        
        result = StrategyPrediction(
            timestamp=datetime.now().isoformat(),
            current_price=current_price,
            detected_strategy=detected_strategy.strategy,
            strategy_probabilities=strategy_probs,
            conflict_state=conflict_state,
            predicted_action=prediction["action"],
            predicted_price_target=prediction["price_target"],
            prediction_confidence=prediction["confidence"],
            expected_timeframe_minutes=prediction["timeframe"],
            key_signals=key_signals,
            risk_warnings=risk_warnings
        )
        
        # 記錄預測供後續驗證
        self.prediction_history.append(result)
        
        return result
    
    def _calculate_strategy_scores_v2(
        self,
        obi: float,
        vpin: float,
        wpi: float,
        chip_concentration: float,
        stop_hunt_index: float,
        order_distortion: float,
        price_change_pct: float,
        volume_ratio: float,
        whale_net_qty: float,
        funding_rate: float,
        # 🆕 新增指標
        breakout_info: Dict,
        volume_exhausted: bool,
        exhaust_trend: str,
        hidden_order_info: Dict,
        divergence_info: Dict,
        waterfall_info: Dict
    ) -> Dict[WhaleStrategy, float]:
        """計算各策略的匹配分數 (v2.0 增強版)"""
        
        scores = {}
        
        # ========== 吸籌建倉 ==========
        # 特徵：底部放量但價格緩漲、大單買入頻繁、量增價平
        acc_score = 0
        if -0.3 <= obi <= 0.3:
            acc_score += 15
        if vpin < 0.4:
            acc_score += 15
        if wpi > 0.1:
            acc_score += 20
        if chip_concentration > 0.55:
            acc_score += 15
        if -0.5 <= price_change_pct <= 0.5 and volume_ratio > 1.2:
            acc_score += 15  # 量增價平
        # 🆕 新指標加成
        if divergence_info.get("divergence_type") == "ACCUMULATION":
            acc_score += 20
        if hidden_order_info.get("hidden_bid_detected"):
            acc_score += 15  # 發現隱藏買單
        scores[WhaleStrategy.ACCUMULATION] = acc_score
        
        # ========== 誘空吸籌 ==========
        # 特徵：跌破支撐後快速拉回、長下影刺穿、下方隱藏買單
        bear_trap_score = 0
        if obi < 0:
            bear_trap_score += 10
        if wpi > 0.2:
            bear_trap_score += 20  # 關鍵：跌時主力買
        if stop_hunt_index > 40:
            bear_trap_score += 20
        if price_change_pct < -0.3:  # 先跌
            bear_trap_score += 10
        if chip_concentration > 0.5:
            bear_trap_score += 10
        # 🆕 新指標加成
        if breakout_info.get("type") == "SUPPORT_BREAK" and breakout_info.get("is_likely_fake"):
            bear_trap_score += 25  # 假跌破！關鍵信號
        if hidden_order_info.get("hidden_bid_detected"):
            bear_trap_score += 15  # 下方有隱藏買單承接
        scores[WhaleStrategy.BEAR_TRAP] = bear_trap_score
        
        # ========== 誘多派發 ==========
        # 特徵：突破壓力後快速回落、長上影假突破、高位賣單積累
        bull_trap_score = 0
        if obi > 0:
            bull_trap_score += 10
        if wpi < -0.2:
            bull_trap_score += 20  # 關鍵：漲時主力賣
        if stop_hunt_index > 40:
            bull_trap_score += 20
        if price_change_pct > 0.3:  # 先漲
            bull_trap_score += 10
        if chip_concentration < 0.5:
            bull_trap_score += 10
        # 🆕 新指標加成
        if breakout_info.get("type") == "RESISTANCE_BREAK" and breakout_info.get("is_likely_fake"):
            bull_trap_score += 25  # 假突破！關鍵信號
        if hidden_order_info.get("hidden_ask_detected"):
            bull_trap_score += 15  # 上方有隱藏賣單派發
        if divergence_info.get("divergence_type") == "BEARISH":
            bull_trap_score += 15  # 空頭背離
        scores[WhaleStrategy.BULL_TRAP] = bull_trap_score
        
        # ========== 拉高出貨 ==========
        # 特徵：巨量急拉、短時間漲幅驚人、成交量先增後衰
        pump_dump_score = 0
        if obi > 0.3:
            pump_dump_score += 15
        if vpin > 0.5:
            pump_dump_score += 20
        if volume_ratio > 3:
            pump_dump_score += 20
        if price_change_pct > 1:
            pump_dump_score += 15
        if wpi < 0 and obi > 0:  # 量價背離
            pump_dump_score += 15
        # 🆕 新指標加成
        if volume_exhausted and exhaust_trend == "UP":
            pump_dump_score += 20  # 上漲途中量能衰竭
        if divergence_info.get("divergence_type") == "BEARISH":
            pump_dump_score += 10
        scores[WhaleStrategy.PUMP_DUMP] = pump_dump_score
        
        # ========== 洗盤震倉 ==========
        # 特徵：寬幅震盪、長上下影頻繁、下跌量縮、低位隱藏買單
        shake_out_score = 0
        if stop_hunt_index > 60:
            shake_out_score += 30  # 高掃損是關鍵
        if -0.4 <= obi <= 0.4:
            shake_out_score += 10
        if 0.2 <= vpin <= 0.5:
            shake_out_score += 10
        if abs(price_change_pct) < 0.3:
            shake_out_score += 10
        if 0.5 <= chip_concentration <= 0.8:
            shake_out_score += 10
        # 🆕 新指標加成
        if volume_exhausted and exhaust_trend == "DOWN":
            shake_out_score += 15  # 下跌中量能衰竭 = 賣壓枯竭
        if hidden_order_info.get("hidden_bid_detected"):
            shake_out_score += 15  # 低位有人承接
        scores[WhaleStrategy.SHAKE_OUT] = shake_out_score
        
        # ========== 試盤探測 ==========
        # 特徵：突發短暫拉高/砸盤但量有限、價格迅速回歸
        testing_score = 0
        if volume_ratio < 0.5:
            testing_score += 25
        if 0.2 <= abs(price_change_pct) <= 0.5:
            testing_score += 20
        if abs(obi) > 0.2 and volume_ratio < 1:
            testing_score += 20
        if abs(wpi) < 0.2:
            testing_score += 15
        # 🆕 新指標加成
        if order_distortion > 30 and order_distortion < 60:
            testing_score += 15  # 有委託異動但不劇烈
        scores[WhaleStrategy.TESTING] = testing_score
        
        # ========== 對敲拉抬 ==========
        # 特徵：量大但無淨流入、同價位連續對敲
        wash_score = 0
        if order_distortion > 50:
            wash_score += 35
        if volume_ratio > 2 and vpin < 0.3:
            wash_score += 25
        if abs(wpi) < 0.1 and volume_ratio > 1.5:
            wash_score += 25
        # 🆕 新指標：量增但無方向
        if divergence_info.get("divergence_type") == "ACCUMULATION" and abs(wpi) < 0.15:
            wash_score += 15  # 可能是假吸籌
        scores[WhaleStrategy.WASH_TRADING] = wash_score
        
        # ========== 砸盤打壓 ==========
        # 特徵：瀑布式下跌、天量後縮量、買單快速後撤
        dump_score = 0
        if obi < -0.3:
            dump_score += 20
        if vpin > 0.4:
            dump_score += 15
        if wpi < -0.4:
            dump_score += 20
        if price_change_pct < -0.5:
            dump_score += 15
        if volume_ratio > 2:
            dump_score += 10
        # 🆕 新指標加成
        if waterfall_info.get("is_waterfall"):
            dump_score += 25  # 瀑布式下跌！
        if waterfall_info.get("volume_pattern") == "EXHAUSTING":
            dump_score += 10  # 但成交量萎縮，可能接近尾聲
        scores[WhaleStrategy.DUMP] = dump_score
        
        # ========== 正常波動 ==========
        max_score = max(scores.values()) if scores else 0
        if max_score < 40:
            scores[WhaleStrategy.NORMAL] = 60
        else:
            scores[WhaleStrategy.NORMAL] = max(0, 40 - max_score / 2)
        
        return scores
    
    def _generate_signals_and_warnings_v2(
        self,
        strategy_probs: List[StrategyProbability],
        conflict_state: WhaleRetailConflict,
        vpin: float,
        stop_hunt_index: float,
        order_distortion: float,
        # 🆕 新增指標
        breakout_info: Dict,
        volume_exhausted: bool,
        exhaust_signals: List[str],
        hidden_order_info: Dict,
        divergence_info: Dict,
        waterfall_info: Dict
    ) -> Tuple[List[str], List[str]]:
        """生成關鍵信號和風險警告 (v2.0 增強版)"""
        
        key_signals = []
        risk_warnings = []
        
        # 主要策略信號
        if strategy_probs:
            top = strategy_probs[0]
            if top.probability > 0.4:
                key_signals.append(f"🎯 主力策略: {top.strategy.value} ({top.probability:.0%})")
        
        # 對峙信號
        if conflict_state.conflict_level > 0.6:
            key_signals.append(f"⚔️ 多空對峙: {conflict_state.reasoning}")
        
        # 🆕 突破信號
        if breakout_info.get("type") != "NONE":
            if breakout_info.get("is_likely_fake"):
                risk_warnings.append(f"⚠️ {breakout_info['type']} 可能是假突破！")
            else:
                key_signals.append(f"📊 {breakout_info['type']} @${breakout_info['level']:,.0f}")
            for sig in breakout_info.get("signals", []):
                key_signals.append(sig)
        
        # 🆕 價量背離信號
        if divergence_info.get("divergence_type") not in [None, "NONE"]:
            for sig in divergence_info.get("signals", []):
                key_signals.append(sig)
        
        # 🆕 成交量衰竭信號
        if volume_exhausted:
            for sig in exhaust_signals:
                key_signals.append(sig)
        
        # 🆕 隱藏大單信號
        for sig in hidden_order_info.get("signals", []):
            key_signals.append(sig)
        
        # 🆕 瀑布式下跌信號
        if waterfall_info.get("is_waterfall"):
            for sig in waterfall_info.get("signals", []):
                risk_warnings.append(sig)
        
        # 原有風險警告
        if vpin > 0.6:
            risk_warnings.append(f"⚠️ 高毒性流量 (VPIN={vpin:.2f})，市場可能被操控")
        
        if stop_hunt_index > 50:
            risk_warnings.append(f"⚠️ 止損掃蕩指數高 ({stop_hunt_index})，注意假突破")
        
        if order_distortion > 50:
            risk_warnings.append(f"⚠️ 委託異動率高 ({order_distortion})，可能有Spoofing")
        
        # 特定策略警告
        for prob in strategy_probs[:3]:
            if prob.strategy == WhaleStrategy.PUMP_DUMP and prob.probability > 0.3:
                risk_warnings.append("🚨 可能是拉高出貨，不要追高！")
            if prob.strategy == WhaleStrategy.BULL_TRAP and prob.probability > 0.3:
                risk_warnings.append("🚨 可能是誘多陷阱，突破可能是假的！")
            if prob.strategy == WhaleStrategy.BEAR_TRAP and prob.probability > 0.3:
                key_signals.append("💡 可能是誘空陷阱，考慮逢低買入")
        
        return key_signals, risk_warnings
    
    def _calculate_strategy_scores(
        self,
        obi: float,
        vpin: float,
        wpi: float,
        chip_concentration: float,
        stop_hunt_index: float,
        order_distortion: float,
        price_change_pct: float,
        volume_ratio: float,
        whale_net_qty: float,
        funding_rate: float
    ) -> Dict[WhaleStrategy, float]:
        """計算各策略的匹配分數"""
        
        scores = {}
        
        # 吸籌建倉
        acc_score = 0
        if -0.3 <= obi <= 0.3:
            acc_score += 20
        if vpin < 0.4:
            acc_score += 20
        if wpi > 0.1:
            acc_score += 20
        if chip_concentration > 0.55:
            acc_score += 20
        if -0.5 <= price_change_pct <= 0.5 and volume_ratio > 1.2:
            acc_score += 20
        scores[WhaleStrategy.ACCUMULATION] = acc_score
        
        # 誘空吸籌
        bear_trap_score = 0
        if obi < 0:
            bear_trap_score += 15
        if wpi > 0.2:
            bear_trap_score += 25  # 關鍵：跌時主力買
        if stop_hunt_index > 40:
            bear_trap_score += 30
        if price_change_pct < -0.3:  # 先跌
            bear_trap_score += 15
        if chip_concentration > 0.5:
            bear_trap_score += 15
        scores[WhaleStrategy.BEAR_TRAP] = bear_trap_score
        
        # 誘多派發
        bull_trap_score = 0
        if obi > 0:
            bull_trap_score += 15
        if wpi < -0.2:
            bull_trap_score += 25  # 關鍵：漲時主力賣
        if stop_hunt_index > 40:
            bull_trap_score += 30
        if price_change_pct > 0.3:  # 先漲
            bull_trap_score += 15
        if chip_concentration < 0.5:
            bull_trap_score += 15
        scores[WhaleStrategy.BULL_TRAP] = bull_trap_score
        
        # 拉高出貨
        pump_dump_score = 0
        if obi > 0.3:
            pump_dump_score += 20
        if vpin > 0.5:
            pump_dump_score += 25
        if volume_ratio > 3:
            pump_dump_score += 25
        if price_change_pct > 1:
            pump_dump_score += 15
        if wpi < 0 and obi > 0:  # 量價背離
            pump_dump_score += 15
        scores[WhaleStrategy.PUMP_DUMP] = pump_dump_score
        
        # 洗盤震倉
        shake_out_score = 0
        if stop_hunt_index > 60:
            shake_out_score += 40
        if -0.4 <= obi <= 0.4:
            shake_out_score += 15
        if 0.2 <= vpin <= 0.5:
            shake_out_score += 15
        if abs(price_change_pct) < 0.3:
            shake_out_score += 15
        if 0.5 <= chip_concentration <= 0.8:
            shake_out_score += 15
        scores[WhaleStrategy.SHAKE_OUT] = shake_out_score
        
        # 試盤探測
        testing_score = 0
        if volume_ratio < 0.5:
            testing_score += 30
        if 0.2 <= abs(price_change_pct) <= 0.5:
            testing_score += 25
        if abs(obi) > 0.2 and volume_ratio < 1:
            testing_score += 25
        if abs(wpi) < 0.2:
            testing_score += 20
        scores[WhaleStrategy.TESTING] = testing_score
        
        # 對敲拉抬
        wash_score = 0
        if order_distortion > 50:
            wash_score += 40
        if volume_ratio > 2 and vpin < 0.3:
            wash_score += 30
        if abs(wpi) < 0.1 and volume_ratio > 1.5:
            wash_score += 30
        scores[WhaleStrategy.WASH_TRADING] = wash_score
        
        # 砸盤打壓
        dump_score = 0
        if obi < -0.3:
            dump_score += 25
        if vpin > 0.4:
            dump_score += 20
        if wpi < -0.4:
            dump_score += 25
        if price_change_pct < -0.5:
            dump_score += 15
        if volume_ratio > 2:
            dump_score += 15
        scores[WhaleStrategy.DUMP] = dump_score
        
        # 正常波動（其他都低時）
        max_score = max(scores.values()) if scores else 0
        if max_score < 40:
            scores[WhaleStrategy.NORMAL] = 60
        else:
            scores[WhaleStrategy.NORMAL] = max(0, 40 - max_score / 2)
        
        return scores
    
    def _scores_to_probabilities(
        self, 
        scores: Dict[WhaleStrategy, float]
    ) -> List[StrategyProbability]:
        """將分數轉換為機率分布"""
        
        total = sum(scores.values())
        if total == 0:
            total = 1
        
        probs = []
        for strategy, score in scores.items():
            prob = score / total
            confidence = min(1.0, score / 80)  # 80分以上為高信心
            
            signals = []
            if score > 50:
                signals.append(f"{strategy.value}特徵明顯")
            elif score > 30:
                signals.append(f"可能為{strategy.value}")
            
            probs.append(StrategyProbability(
                strategy=strategy,
                probability=prob,
                confidence=confidence,
                signals=signals
            ))
        
        # 按機率排序
        probs.sort(key=lambda x: x.probability, reverse=True)
        return probs
    
    def _analyze_conflict(
        self,
        wpi: float,
        obi: float,
        liquidation_pressure_long: float,
        liquidation_pressure_short: float,
        funding_rate: float
    ) -> WhaleRetailConflict:
        """分析主力 vs 散戶對峙狀態"""
        
        # 主力方向 (根據 WPI)
        if wpi > 0.2:
            whale_direction = "BULLISH"
        elif wpi < -0.2:
            whale_direction = "BEARISH"
        else:
            whale_direction = "NEUTRAL"
        
        # 散戶方向 (根據資金費率和爆倉壓力)
        # 高正資金費率 = 散戶做多
        # 高多頭爆倉壓力 = 散戶槓桿做多
        retail_bullish_score = 0
        if funding_rate > 0.0001:
            retail_bullish_score += 1
        if liquidation_pressure_long > 50:
            retail_bullish_score += 1
        
        retail_bearish_score = 0
        if funding_rate < -0.0001:
            retail_bearish_score += 1
        if liquidation_pressure_short > 50:
            retail_bearish_score += 1
        
        if retail_bullish_score > retail_bearish_score:
            retail_direction = "BULLISH"
        elif retail_bearish_score > retail_bullish_score:
            retail_direction = "BEARISH"
        else:
            retail_direction = "NEUTRAL"
        
        # 對峙程度
        if whale_direction != retail_direction and whale_direction != "NEUTRAL" and retail_direction != "NEUTRAL":
            conflict_level = 0.8 + abs(wpi) * 0.2
            likely_winner = "WHALE"
            reasoning = f"主力{whale_direction}與散戶{retail_direction}對峙，主力通常勝出"
        elif whale_direction == retail_direction and whale_direction != "NEUTRAL":
            conflict_level = 0.2
            likely_winner = "UNCERTAIN"
            reasoning = f"主力與散戶同向{whale_direction}，注意反轉風險"
        else:
            conflict_level = 0.4
            likely_winner = "UNCERTAIN"
            reasoning = "多空方向不明確"
        
        return WhaleRetailConflict(
            whale_direction=whale_direction,
            retail_direction=retail_direction,
            conflict_level=conflict_level,
            likely_winner=likely_winner,
            reasoning=reasoning
        )
    
    def _predict_next_action(
        self,
        detected_strategy: WhaleStrategy,
        conflict_state: WhaleRetailConflict,
        current_price: float,
        wpi: float,
        vpin: float
    ) -> Dict:
        """預測主力下一步行動"""
        
        predictions = {
            WhaleStrategy.ACCUMULATION: {
                "action": "CONSOLIDATE",
                "price_pct": 0.5,
                "timeframe": 60,
                "confidence": 0.6
            },
            WhaleStrategy.BEAR_TRAP: {
                "action": "PUMP",
                "price_pct": 1.5,
                "timeframe": 30,
                "confidence": 0.7
            },
            WhaleStrategy.BULL_TRAP: {
                "action": "DUMP",
                "price_pct": -1.5,
                "timeframe": 30,
                "confidence": 0.7
            },
            WhaleStrategy.PUMP_DUMP: {
                "action": "DUMP",
                "price_pct": -3.0,
                "timeframe": 15,
                "confidence": 0.8
            },
            WhaleStrategy.SHAKE_OUT: {
                "action": "BREAKOUT",
                "price_pct": 2.0 if wpi > 0 else -2.0,
                "timeframe": 120,
                "confidence": 0.5
            },
            WhaleStrategy.TESTING: {
                "action": "CONSOLIDATE",
                "price_pct": 0.3,
                "timeframe": 30,
                "confidence": 0.5
            },
            WhaleStrategy.WASH_TRADING: {
                "action": "CONSOLIDATE",
                "price_pct": 0.2,
                "timeframe": 60,
                "confidence": 0.4
            },
            WhaleStrategy.DUMP: {
                "action": "DUMP",
                "price_pct": -2.0,
                "timeframe": 20,
                "confidence": 0.75
            },
            WhaleStrategy.NORMAL: {
                "action": "CONSOLIDATE",
                "price_pct": 0.1,
                "timeframe": 60,
                "confidence": 0.4
            }
        }
        
        pred = predictions.get(detected_strategy, predictions[WhaleStrategy.NORMAL])
        
        # 調整置信度
        if conflict_state.likely_winner == "WHALE":
            pred["confidence"] *= 1.2
        if vpin > 0.6:
            pred["confidence"] *= 0.8  # 高毒性降低信心
        
        pred["confidence"] = min(0.95, pred["confidence"])
        
        return {
            "action": pred["action"],
            "price_target": current_price * (1 + pred["price_pct"] / 100),
            "confidence": pred["confidence"],
            "timeframe": pred["timeframe"]
        }
    
    def _generate_signals_and_warnings(
        self,
        strategy_probs: List[StrategyProbability],
        conflict_state: WhaleRetailConflict,
        vpin: float,
        stop_hunt_index: float,
        order_distortion: float
    ) -> Tuple[List[str], List[str]]:
        """生成關鍵信號和風險警告"""
        
        key_signals = []
        risk_warnings = []
        
        # 主要策略信號
        if strategy_probs:
            top = strategy_probs[0]
            if top.probability > 0.4:
                key_signals.append(f"🎯 主力策略: {top.strategy.value} ({top.probability:.0%})")
        
        # 對峙信號
        if conflict_state.conflict_level > 0.6:
            key_signals.append(f"⚔️ 多空對峙: {conflict_state.reasoning}")
        
        # 風險警告
        if vpin > 0.6:
            risk_warnings.append(f"⚠️ 高毒性流量 (VPIN={vpin:.2f})，市場可能被操控")
        
        if stop_hunt_index > 50:
            risk_warnings.append(f"⚠️ 止損掃蕩指數高 ({stop_hunt_index})，注意假突破")
        
        if order_distortion > 50:
            risk_warnings.append(f"⚠️ 委託異動率高 ({order_distortion})，可能有Spoofing")
        
        # 特定策略警告
        for prob in strategy_probs[:3]:
            if prob.strategy == WhaleStrategy.PUMP_DUMP and prob.probability > 0.3:
                risk_warnings.append("🚨 可能是拉高出貨，不要追高！")
            if prob.strategy == WhaleStrategy.BULL_TRAP and prob.probability > 0.3:
                risk_warnings.append("🚨 可能是誘多陷阱，突破可能是假的！")
            if prob.strategy == WhaleStrategy.BEAR_TRAP and prob.probability > 0.3:
                key_signals.append("💡 可能是誘空陷阱，考慮逢低買入")
        
        return key_signals, risk_warnings
    
    def validate_prediction(
        self,
        prediction_id: int,
        actual_price: float,
        actual_outcome: str
    ) -> Dict:
        """
        驗證之前的預測
        
        Args:
            prediction_id: 預測在歷史中的索引
            actual_price: 實際價格
            actual_outcome: 實際結果 ("CORRECT" / "WRONG" / "PARTIAL")
        """
        if prediction_id >= len(self.prediction_history):
            return {"error": "Invalid prediction ID"}
        
        prediction = self.prediction_history[prediction_id]
        
        # 計算預測誤差
        price_error_pct = abs(actual_price - prediction.predicted_price_target) / prediction.current_price * 100
        
        # 判斷方向是否正確
        predicted_direction = "UP" if prediction.predicted_price_target > prediction.current_price else "DOWN"
        actual_direction = "UP" if actual_price > prediction.current_price else "DOWN"
        direction_correct = predicted_direction == actual_direction
        
        validation = {
            "prediction_timestamp": prediction.timestamp,
            "predicted_strategy": prediction.detected_strategy.value,
            "predicted_action": prediction.predicted_action,
            "predicted_price_target": prediction.predicted_price_target,
            "actual_price": actual_price,
            "price_error_pct": price_error_pct,
            "direction_correct": direction_correct,
            "outcome": actual_outcome,
            "confidence_was": prediction.prediction_confidence
        }
        
        self.validation_results.append(validation)
        
        return validation
    
    def get_accuracy_stats(self) -> Dict:
        """獲取預測準確率統計"""
        if not self.validation_results:
            return {"error": "No validations yet"}
        
        total = len(self.validation_results)
        correct = sum(1 for v in self.validation_results if v["outcome"] == "CORRECT")
        partial = sum(1 for v in self.validation_results if v["outcome"] == "PARTIAL")
        direction_correct = sum(1 for v in self.validation_results if v["direction_correct"])
        
        avg_error = np.mean([v["price_error_pct"] for v in self.validation_results])
        
        return {
            "total_predictions": total,
            "correct_count": correct,
            "partial_count": partial,
            "accuracy_pct": correct / total * 100 if total > 0 else 0,
            "direction_accuracy_pct": direction_correct / total * 100 if total > 0 else 0,
            "avg_price_error_pct": avg_error
        }
    
    def to_prompt_context(self, prediction: StrategyPrediction) -> str:
        """
        將分析結果轉換為 LLM 可用的 prompt 上下文
        """
        probs_str = "\n".join([
            f"  - {p.strategy.value}: {p.probability:.0%}"
            for p in prediction.strategy_probabilities[:5]
        ])
        
        signals_str = "\n".join([f"  • {s}" for s in prediction.key_signals])
        warnings_str = "\n".join([f"  • {w}" for w in prediction.risk_warnings])
        
        return f"""
## 🐋 主力策略分析報告

**時間**: {prediction.timestamp}
**當前價格**: ${prediction.current_price:,.2f}

### 策略識別
**檢測到的主力策略**: {prediction.detected_strategy.value}

策略機率分布:
{probs_str}

### 多空對峙
- 主力方向: {prediction.conflict_state.whale_direction}
- 散戶方向: {prediction.conflict_state.retail_direction}
- 對峙程度: {prediction.conflict_state.conflict_level:.0%}
- 預測勝方: {prediction.conflict_state.likely_winner}
- 分析: {prediction.conflict_state.reasoning}

### 下一步預測
- 預測行動: {prediction.predicted_action}
- 目標價格: ${prediction.predicted_price_target:,.2f}
- 置信度: {prediction.prediction_confidence:.0%}
- 預期時間框架: {prediction.expected_timeframe_minutes} 分鐘

### 關鍵信號
{signals_str}

### ⚠️ 風險警告
{warnings_str}
"""


# ==================== 便捷函數 ====================

def create_detector() -> WhaleStrategyDetector:
    """創建主力策略檢測器實例"""
    return WhaleStrategyDetector()


def quick_analyze(
    detector: WhaleStrategyDetector,
    obi: float,
    vpin: float,
    price: float,
    price_change_pct: float,
    volume_ratio: float = 1.0,
    whale_net_qty: float = 0,
    funding_rate: float = 0,
    liq_pressure_long: float = 50,
    liq_pressure_short: float = 50
) -> str:
    """快速分析並返回文字報告"""
    
    prediction = detector.analyze(
        obi=obi,
        vpin=vpin,
        current_price=price,
        price_change_pct=price_change_pct,
        volume_ratio=volume_ratio,
        whale_net_qty=whale_net_qty,
        funding_rate=funding_rate,
        liquidation_pressure_long=liq_pressure_long,
        liquidation_pressure_short=liq_pressure_short
    )
    
    return detector.to_prompt_context(prediction)


# ==================== 測試 ====================

if __name__ == "__main__":
    print("🐋 主力策略識別系統測試")
    print("=" * 60)
    
    detector = create_detector()
    
    # 模擬一個「誘空吸籌」場景
    test_report = quick_analyze(
        detector=detector,
        obi=-0.3,           # 訂單簿偏空
        vpin=0.45,          # 中等知情交易
        price=87000,
        price_change_pct=-0.5,  # 價格下跌
        volume_ratio=1.5,
        whale_net_qty=5.0,   # 但鯨魚在買！
        funding_rate=-0.0002,
        liq_pressure_long=40,
        liq_pressure_short=60
    )
    
    print(test_report)
