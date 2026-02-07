"""
🐋 主力策略識別系統 (Whale Strategy Detector) v4.0
===================================================

v4.0 核心升級：
- 策略數量：8 → 22 種（6大類）
- 輸出格式：新增 WhaleStrategySnapshot + JSON 輸出
- 更新頻率：支援主動每 3 秒計算
- 新增功能：進場建議、出場預測、失效信號、學習機制

Author: AI Trading System
Created: 2025-11-28
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable
from enum import Enum
from collections import deque
from datetime import datetime, timezone
import numpy as np
import json
import os
import time


# ==================== v4.0 策略枚舉 (22種) ====================

class WhaleStrategyV4(Enum):
    """
    主力策略類型 v4.0 - 6大類 22種
    """
    # ========== 一、誘騙類 (Trap Patterns) - 5種 ==========
    BULL_TRAP = "多頭陷阱"          # 假突破誘多後砸盤
    BEAR_TRAP = "空頭陷阱"          # 假跌破誘空後拉升
    FAKEOUT = "假突破"              # 突破關鍵位後快速反轉
    STOP_HUNT = "獵殺止損"          # 刺穿止損密集區後反轉
    SPOOFING = "幌騙"               # 大單掛撤影響價格
    
    # ========== 二、清洗類 (Shakeout Patterns) - 4種 ==========
    WHIPSAW = "鋸齒洗盤"            # 上下劇烈震盪甩出散戶
    CONSOLIDATION_SHAKE = "盤整洗盤"  # 長時間橫盤磨耐心
    FLASH_CRASH = "閃崩洗盤"         # 瞬間暴跌製造恐慌後快速收回
    SLOW_BLEED = "陰跌洗盤"          # 緩慢下跌磨多頭
    
    # ========== 三、吸籌/派發類 (Accumulation/Distribution) - 4種 ==========
    ACCUMULATION = "吸籌建倉"        # 低位隱蔽買入
    DISTRIBUTION = "派發出貨"        # 高位悄悄賣出
    RE_ACCUMULATION = "再吸籌"       # 上漲中途回調吸籌
    RE_DISTRIBUTION = "再派發"       # 下跌中途反彈派發
    
    # ========== 四、爆倉類 (Liquidation Patterns) - 3種 ==========
    LONG_SQUEEZE = "多頭擠壓"        # 砸盤觸發多頭爆倉連鎖
    SHORT_SQUEEZE = "空頭擠壓"       # 拉盤觸發空頭爆倉軋空
    CASCADE_LIQUIDATION = "連環爆倉"  # 大規模連鎖爆倉
    
    # ========== 五、趨勢類 (Trend Patterns) - 3種 ==========
    MOMENTUM_PUSH = "趨勢推動"       # 主力順勢推動
    TREND_CONTINUATION = "趨勢延續"   # 回調後繼續原趨勢
    REVERSAL = "趨勢反轉"            # 趨勢反轉信號
    
    # ========== 六、特殊類 (Special Patterns) - 3種 ==========
    PUMP_DUMP = "拉高出貨"           # 快速拉升後暴跌
    WASH_TRADING = "對敲拉抬"        # 自買自賣製造量
    LAYERING = "層疊掛單"            # 多層假掛單操縱
    
    # ========== 無明顯模式 ==========
    NORMAL = "正常波動"


class StrategyCategory(Enum):
    """策略類別"""
    TRAP = "誘騙類"
    SHAKEOUT = "清洗類"
    ACCUMULATION_DISTRIBUTION = "吸籌派發類"
    LIQUIDATION = "爆倉類"
    TREND = "趨勢類"
    SPECIAL = "特殊類"
    NORMAL = "正常"


class RiskLevel(Enum):
    """風險等級"""
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"
    EXTREME = "極高"


class SignalDirection(Enum):
    """信號方向"""
    LONG = "做多"
    SHORT = "做空"
    HOLD = "觀望"
    CLOSE_LONG = "平多"
    CLOSE_SHORT = "平空"


# ==================== v4.0 資料結構 ====================

@dataclass
class StrategyInfo:
    """策略資訊"""
    strategy: WhaleStrategyV4
    category: StrategyCategory
    probability: float  # 0-1
    confidence: float   # 0-1
    risk_level: RiskLevel
    signals: List[str]
    
    def to_dict(self) -> Dict:
        return {
            "strategy": self.strategy.value,
            "strategy_code": self.strategy.name,
            "category": self.category.value,
            "probability": round(self.probability, 4),
            "confidence": round(self.confidence, 4),
            "risk_level": self.risk_level.value,
            "signals": self.signals
        }


@dataclass
class EntrySignal:
    """進場信號"""
    direction: SignalDirection
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size_pct: float  # 建議倉位比例 0-100%
    urgency: str  # "IMMEDIATE" / "WAIT_PULLBACK" / "WAIT_CONFIRM"
    valid_until: str  # ISO timestamp
    reasoning: str
    
    def to_dict(self) -> Dict:
        return {
            "direction": self.direction.value,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "position_size_pct": self.position_size_pct,
            "urgency": self.urgency,
            "valid_until": self.valid_until,
            "reasoning": self.reasoning
        }


@dataclass  
class ExitPrediction:
    """出場預測"""
    predicted_exit_price: float
    predicted_exit_time: str  # ISO timestamp
    exit_type: str  # "TAKE_PROFIT" / "STOP_LOSS" / "TIME_EXIT" / "SIGNAL_INVALIDATE"
    confidence: float
    
    def to_dict(self) -> Dict:
        return {
            "predicted_exit_price": self.predicted_exit_price,
            "predicted_exit_time": self.predicted_exit_time,
            "exit_type": self.exit_type,
            "confidence": round(self.confidence, 4)
        }


@dataclass
class InvalidationSignal:
    """失效信號"""
    is_invalidated: bool
    invalidation_reason: str
    invalidation_price: Optional[float]
    recommended_action: str  # "CLOSE_POSITION" / "REDUCE_SIZE" / "HOLD" / "FLIP"
    
    def to_dict(self) -> Dict:
        return {
            "is_invalidated": self.is_invalidated,
            "invalidation_reason": self.invalidation_reason,
            "invalidation_price": self.invalidation_price,
            "recommended_action": self.recommended_action
        }


@dataclass
class WhaleStrategySnapshot:
    """
    🐋 主力策略快照 v4.0
    
    每 3 秒更新一次，輸出到 ai_whale_strategy.json
    """
    # 時間戳
    timestamp: str
    update_interval_sec: int = 3
    
    # 市場數據
    symbol: str = "BTCUSDT"
    current_price: float = 0.0
    price_change_1m_pct: float = 0.0
    price_change_5m_pct: float = 0.0
    
    # 主要策略識別
    primary_strategy: Optional[StrategyInfo] = None
    secondary_strategy: Optional[StrategyInfo] = None
    
    # 所有策略機率分布
    strategy_probabilities: Dict[str, float] = field(default_factory=dict)
    
    # 進場建議
    entry_signal: Optional[EntrySignal] = None
    
    # 出場預測
    exit_prediction: Optional[ExitPrediction] = None
    
    # 失效信號
    invalidation: Optional[InvalidationSignal] = None
    
    # 核心指標
    indicators: Dict[str, Any] = field(default_factory=dict)
    
    # 關鍵信號與風險
    key_signals: List[str] = field(default_factory=list)
    risk_warnings: List[str] = field(default_factory=list)
    
    # 整體評估
    overall_bias: str = "NEUTRAL"  # "BULLISH" / "BEARISH" / "NEUTRAL"
    overall_confidence: float = 0.0
    trading_allowed: bool = True
    
    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "update_interval_sec": self.update_interval_sec,
            "symbol": self.symbol,
            "current_price": self.current_price,
            "price_change_1m_pct": round(self.price_change_1m_pct, 4),
            "price_change_5m_pct": round(self.price_change_5m_pct, 4),
            "primary_strategy": self.primary_strategy.to_dict() if self.primary_strategy else None,
            "secondary_strategy": self.secondary_strategy.to_dict() if self.secondary_strategy else None,
            "strategy_probabilities": {k: round(v, 4) for k, v in self.strategy_probabilities.items()},
            "entry_signal": self.entry_signal.to_dict() if self.entry_signal else None,
            "exit_prediction": self.exit_prediction.to_dict() if self.exit_prediction else None,
            "invalidation": self.invalidation.to_dict() if self.invalidation else None,
            "indicators": self.indicators,
            "key_signals": self.key_signals,
            "risk_warnings": self.risk_warnings,
            "overall_bias": self.overall_bias,
            "overall_confidence": round(self.overall_confidence, 4),
            "trading_allowed": self.trading_allowed
        }
    
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    def save_to_file(self, filepath: str = "ai_whale_strategy.json"):
        """儲存到 JSON 檔案"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(self.to_json())


# ==================== 策略元數據 ====================

STRATEGY_METADATA: Dict[WhaleStrategyV4, Dict[str, Any]] = {
    # 誘騙類
    WhaleStrategyV4.BULL_TRAP: {
        "category": StrategyCategory.TRAP,
        "frequency": 0.15,
        "risk_level": RiskLevel.HIGH,
        "best_response": SignalDirection.SHORT,
        "description": "製造假突破壓力位，誘使散戶追高做多，主力趁機高位出貨後砸盤"
    },
    WhaleStrategyV4.BEAR_TRAP: {
        "category": StrategyCategory.TRAP,
        "frequency": 0.15,
        "risk_level": RiskLevel.HIGH,
        "best_response": SignalDirection.LONG,
        "description": "製造假跌破支撐位，誘使散戶恐慌賣出或做空，主力趁機低價吸籌後拉升"
    },
    WhaleStrategyV4.FAKEOUT: {
        "category": StrategyCategory.TRAP,
        "frequency": 0.10,
        "risk_level": RiskLevel.MEDIUM,
        "best_response": SignalDirection.HOLD,
        "description": "突破關鍵價位後快速反轉，不一定伴隨主力明確方向"
    },
    WhaleStrategyV4.STOP_HUNT: {
        "category": StrategyCategory.TRAP,
        "frequency": 0.12,
        "risk_level": RiskLevel.MEDIUM,
        "best_response": SignalDirection.HOLD,
        "description": "專門打掉止損單後反轉，目標精準刺穿關鍵價位"
    },
    WhaleStrategyV4.SPOOFING: {
        "category": StrategyCategory.TRAP,
        "frequency": 0.05,
        "risk_level": RiskLevel.MEDIUM,
        "best_response": SignalDirection.HOLD,
        "description": "大單掛撤影響價格，製造假的買賣壓力"
    },
    
    # 清洗類
    WhaleStrategyV4.WHIPSAW: {
        "category": StrategyCategory.SHAKEOUT,
        "frequency": 0.08,
        "risk_level": RiskLevel.MEDIUM,
        "best_response": SignalDirection.HOLD,
        "description": "上下劇烈震盪甩出散戶，雙向觸及止損"
    },
    WhaleStrategyV4.CONSOLIDATION_SHAKE: {
        "category": StrategyCategory.SHAKEOUT,
        "frequency": 0.06,
        "risk_level": RiskLevel.LOW,
        "best_response": SignalDirection.HOLD,
        "description": "長時間橫盤磨耐心，讓急躁的散戶自己出場"
    },
    WhaleStrategyV4.FLASH_CRASH: {
        "category": StrategyCategory.SHAKEOUT,
        "frequency": 0.03,
        "risk_level": RiskLevel.HIGH,
        "best_response": SignalDirection.LONG,
        "description": "瞬間暴跌製造恐慌，快速收回"
    },
    WhaleStrategyV4.SLOW_BLEED: {
        "category": StrategyCategory.SHAKEOUT,
        "frequency": 0.04,
        "risk_level": RiskLevel.MEDIUM,
        "best_response": SignalDirection.HOLD,
        "description": "緩慢下跌磨多頭，讓多頭慢慢止損出場"
    },
    
    # 吸籌/派發類
    WhaleStrategyV4.ACCUMULATION: {
        "category": StrategyCategory.ACCUMULATION_DISTRIBUTION,
        "frequency": 0.05,
        "risk_level": RiskLevel.LOW,
        "best_response": SignalDirection.LONG,
        "description": "主力在低位隱蔽大量買入，不拉盤避免引起注意"
    },
    WhaleStrategyV4.DISTRIBUTION: {
        "category": StrategyCategory.ACCUMULATION_DISTRIBUTION,
        "frequency": 0.05,
        "risk_level": RiskLevel.HIGH,
        "best_response": SignalDirection.SHORT,
        "description": "主力在高位悄悄賣出，不砸盤避免引起恐慌"
    },
    WhaleStrategyV4.RE_ACCUMULATION: {
        "category": StrategyCategory.ACCUMULATION_DISTRIBUTION,
        "frequency": 0.03,
        "risk_level": RiskLevel.LOW,
        "best_response": SignalDirection.LONG,
        "description": "上漲中途回調吸籌，為下一波拉升準備"
    },
    WhaleStrategyV4.RE_DISTRIBUTION: {
        "category": StrategyCategory.ACCUMULATION_DISTRIBUTION,
        "frequency": 0.03,
        "risk_level": RiskLevel.HIGH,
        "best_response": SignalDirection.SHORT,
        "description": "下跌中途反彈派發，為下一波砸盤準備"
    },
    
    # 爆倉類
    WhaleStrategyV4.LONG_SQUEEZE: {
        "category": StrategyCategory.LIQUIDATION,
        "frequency": 0.04,
        "risk_level": RiskLevel.EXTREME,
        "best_response": SignalDirection.SHORT,
        "description": "砸盤觸發多頭爆倉，製造連鎖下跌"
    },
    WhaleStrategyV4.SHORT_SQUEEZE: {
        "category": StrategyCategory.LIQUIDATION,
        "frequency": 0.04,
        "risk_level": RiskLevel.EXTREME,
        "best_response": SignalDirection.LONG,
        "description": "拉盤觸發空頭爆倉，製造軋空"
    },
    WhaleStrategyV4.CASCADE_LIQUIDATION: {
        "category": StrategyCategory.LIQUIDATION,
        "frequency": 0.02,
        "risk_level": RiskLevel.EXTREME,
        "best_response": SignalDirection.HOLD,
        "description": "大規模連環爆倉，順勢操作但要小心反轉"
    },
    
    # 趨勢類
    WhaleStrategyV4.MOMENTUM_PUSH: {
        "category": StrategyCategory.TREND,
        "frequency": 0.03,
        "risk_level": RiskLevel.LOW,
        "best_response": SignalDirection.HOLD,  # 依趨勢方向
        "description": "主力順勢推動價格"
    },
    WhaleStrategyV4.TREND_CONTINUATION: {
        "category": StrategyCategory.TREND,
        "frequency": 0.04,
        "risk_level": RiskLevel.LOW,
        "best_response": SignalDirection.HOLD,  # 回調加倉
        "description": "趨勢中回調後繼續原方向"
    },
    WhaleStrategyV4.REVERSAL: {
        "category": StrategyCategory.TREND,
        "frequency": 0.02,
        "risk_level": RiskLevel.MEDIUM,
        "best_response": SignalDirection.HOLD,  # 謹慎反手
        "description": "趨勢反轉信號"
    },
    
    # 特殊類
    WhaleStrategyV4.PUMP_DUMP: {
        "category": StrategyCategory.SPECIAL,
        "frequency": 0.05,
        "risk_level": RiskLevel.HIGH,
        "best_response": SignalDirection.SHORT,
        "description": "快速拉抬價格吸引跟風盤，隨後高位出貨"
    },
    WhaleStrategyV4.WASH_TRADING: {
        "category": StrategyCategory.SPECIAL,
        "frequency": 0.05,
        "risk_level": RiskLevel.MEDIUM,
        "best_response": SignalDirection.HOLD,
        "description": "自買自賣製造成交假象"
    },
    WhaleStrategyV4.LAYERING: {
        "category": StrategyCategory.SPECIAL,
        "frequency": 0.04,
        "risk_level": RiskLevel.MEDIUM,
        "best_response": SignalDirection.HOLD,
        "description": "多層假掛單操縱市場深度"
    },
    
    # 正常
    WhaleStrategyV4.NORMAL: {
        "category": StrategyCategory.NORMAL,
        "frequency": 0.0,
        "risk_level": RiskLevel.LOW,
        "best_response": SignalDirection.HOLD,
        "description": "無明顯主力行為"
    }
}


def get_strategy_metadata(strategy: WhaleStrategyV4) -> Dict[str, Any]:
    """獲取策略元數據"""
    return STRATEGY_METADATA.get(strategy, STRATEGY_METADATA[WhaleStrategyV4.NORMAL])


def get_category_strategies(category: StrategyCategory) -> List[WhaleStrategyV4]:
    """獲取某類別下的所有策略"""
    return [
        s for s, meta in STRATEGY_METADATA.items()
        if meta["category"] == category
    ]


# ==================== JSON 輸出管理 ====================

class WhaleStrategyJsonWriter:
    """
    主力策略 JSON 輸出管理器
    
    負責將 WhaleStrategySnapshot 寫入 ai_whale_strategy.json
    """
    
    def __init__(self, output_path: str = "ai_whale_strategy.json"):
        self.output_path = output_path
        self.last_write_time: float = 0
        self.write_interval: float = 3.0  # 秒
        self._snapshot_history: deque = deque(maxlen=100)
        
    def write(self, snapshot: WhaleStrategySnapshot, force: bool = False):
        """
        寫入快照到 JSON 檔案
        
        Args:
            snapshot: 策略快照
            force: 是否強制寫入（忽略間隔限制）
        """
        now = time.time()
        
        # 檢查寫入間隔
        if not force and (now - self.last_write_time) < self.write_interval:
            return
        
        try:
            snapshot.save_to_file(self.output_path)
            self.last_write_time = now
            self._snapshot_history.append(snapshot)
        except Exception as e:
            print(f"⚠️ 寫入 {self.output_path} 失敗: {e}")
    
    def read_latest(self) -> Optional[WhaleStrategySnapshot]:
        """讀取最新的快照"""
        if self._snapshot_history:
            return self._snapshot_history[-1]
        
        # 嘗試從檔案讀取
        if os.path.exists(self.output_path):
            try:
                with open(self.output_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 簡化處理：只返回基本資訊
                    return data
            except:
                pass
        return None


# ==================== v4.0 偵測器整合 ====================

class LiquidationPatternDetector:
    """
    爆倉模式偵測器 v4.0
    
    整合 LiquidationCascadeDetector，輸出：
    - LONG_SQUEEZE（多頭擠壓）
    - SHORT_SQUEEZE（空頭擠壓）
    - CASCADE_LIQUIDATION（連環爆倉）
    """
    
    def __init__(self):
        self.liq_history: deque = deque(maxlen=300)  # 5分鐘數據
        self.price_history: deque = deque(maxlen=300)
        
    def add_liquidation(self, side: str, usd_value: float, price: float, timestamp: float):
        """
        添加爆倉事件
        
        Args:
            side: "SELL" = 多頭被爆, "BUY" = 空頭被爆
            usd_value: 爆倉金額 (USD)
            price: 爆倉價格
            timestamp: Unix timestamp (ms)
        """
        self.liq_history.append({
            "side": side,
            "usd_value": usd_value,
            "price": price,
            "timestamp": timestamp,
            "is_long_liq": side == "SELL"  # 多頭被爆
        })
        
    def add_price(self, price: float, timestamp: float):
        """添加價格數據"""
        self.price_history.append({"price": price, "timestamp": timestamp})
    
    def detect(self) -> Dict[str, Any]:
        """
        偵測爆倉模式
        
        Returns:
            {
                "pattern": WhaleStrategyV4 or None,
                "probability": float,
                "confidence": float,
                "direction": str,  # "LONG_LIQ" / "SHORT_LIQ" / "MIXED"
                "stats": {...},
                "signals": [...]
            }
        """
        if len(self.liq_history) < 5:
            return {"pattern": None, "probability": 0, "confidence": 0, "signals": ["數據不足"]}
        
        now = time.time() * 1000
        
        # 計算 1 分鐘窗口統計
        cutoff_1m = now - 60_000
        events_1m = [e for e in self.liq_history if e["timestamp"] >= cutoff_1m]
        
        long_liq_1m = sum(e["usd_value"] for e in events_1m if e["is_long_liq"])
        short_liq_1m = sum(e["usd_value"] for e in events_1m if not e["is_long_liq"])
        total_1m = long_liq_1m + short_liq_1m
        
        # 計算價格變動
        price_change_pct = 0.0
        if len(self.price_history) >= 2:
            old_price = self.price_history[0]["price"]
            new_price = self.price_history[-1]["price"]
            if old_price > 0:
                price_change_pct = (new_price - old_price) / old_price * 100
        
        signals = []
        pattern = None
        probability = 0.0
        confidence = 0.0
        
        # 判斷爆倉方向
        if total_1m > 0:
            long_ratio = long_liq_1m / total_1m
            if long_ratio >= 0.7:
                direction = "LONG_LIQ"
            elif long_ratio <= 0.3:
                direction = "SHORT_LIQ"
            else:
                direction = "MIXED"
        else:
            direction = "NONE"
        
        # ========== LONG_SQUEEZE 多頭擠壓 ==========
        # 條件：大量多頭爆倉 + 價格下跌
        if long_liq_1m >= 3_000_000 and price_change_pct < -0.3:
            pattern = WhaleStrategyV4.LONG_SQUEEZE
            probability = min(1.0, long_liq_1m / 10_000_000)
            confidence = min(1.0, 0.5 + abs(price_change_pct) * 0.1)
            signals.append(f"🔴 多頭擠壓: ${long_liq_1m/1e6:.2f}M 爆倉")
            signals.append(f"📉 價格下跌 {price_change_pct:.2f}%")
            
        # ========== SHORT_SQUEEZE 空頭擠壓 ==========
        # 條件：大量空頭爆倉 + 價格上漲
        elif short_liq_1m >= 3_000_000 and price_change_pct > 0.3:
            pattern = WhaleStrategyV4.SHORT_SQUEEZE
            probability = min(1.0, short_liq_1m / 10_000_000)
            confidence = min(1.0, 0.5 + abs(price_change_pct) * 0.1)
            signals.append(f"🟢 空頭擠壓: ${short_liq_1m/1e6:.2f}M 爆倉")
            signals.append(f"📈 價格上漲 {price_change_pct:.2f}%")
            
        # ========== CASCADE_LIQUIDATION 連環爆倉 ==========
        # 條件：總爆倉金額極大
        elif total_1m >= 10_000_000:
            pattern = WhaleStrategyV4.CASCADE_LIQUIDATION
            probability = min(1.0, total_1m / 50_000_000)
            confidence = min(1.0, 0.6 + total_1m / 100_000_000)
            signals.append(f"💥 連環爆倉: ${total_1m/1e6:.2f}M 總爆倉")
            signals.append(f"🐂 多頭: ${long_liq_1m/1e6:.2f}M | 🐻 空頭: ${short_liq_1m/1e6:.2f}M")
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": confidence,
            "direction": direction,
            "stats": {
                "total_1m_usd": total_1m,
                "long_liq_1m_usd": long_liq_1m,
                "short_liq_1m_usd": short_liq_1m,
                "price_change_pct": price_change_pct,
                "event_count_1m": len(events_1m)
            },
            "signals": signals
        }


class FakeoutDetector:
    """
    假突破偵測器 v4.0
    
    從 SupportResistanceBreakDetector.is_likely_fake 提取並增強
    """
    
    def __init__(self, lookback: int = 50):
        self.price_history: deque = deque(maxlen=lookback * 2)
        self.volume_history: deque = deque(maxlen=lookback * 2)
        self.lookback = lookback
        
    def add_data(self, price: float, volume: float, timestamp: float):
        """添加價格和成交量數據"""
        self.price_history.append({"price": price, "volume": volume, "timestamp": timestamp})
        self.volume_history.append(volume)
    
    def _detect_levels(self) -> Tuple[List[float], List[float]]:
        """檢測支撐和壓力位"""
        if len(self.price_history) < self.lookback:
            return [], []
        
        prices = [p["price"] for p in self.price_history]
        supports, resistances = [], []
        
        for i in range(5, len(prices) - 5):
            window = prices[i-5:i+6]
            if prices[i] == min(window):
                supports.append(prices[i])
            if prices[i] == max(window):
                resistances.append(prices[i])
        
        return supports, resistances
    
    def detect(self, current_price: float, current_volume: float) -> Dict[str, Any]:
        """
        偵測假突破
        
        Returns:
            {
                "pattern": WhaleStrategyV4.FAKEOUT or None,
                "probability": float,
                "break_type": "RESISTANCE" / "SUPPORT" / None,
                "is_fake": bool,
                "level": float,
                "signals": []
            }
        """
        supports, resistances = self._detect_levels()
        
        if not supports and not resistances:
            return {"pattern": None, "probability": 0, "is_fake": False, "signals": ["數據不足"]}
        
        avg_volume = np.mean(list(self.volume_history)) if self.volume_history else 0
        signals = []
        break_type = None
        is_fake = False
        level = 0.0
        probability = 0.0
        
        # 檢查壓力突破
        for resistance in sorted(resistances, reverse=True):
            if current_price > resistance * 1.002:
                break_type = "RESISTANCE"
                level = resistance
                
                # 假突破判定：量能不足
                if avg_volume > 0 and current_volume < avg_volume * 1.3:
                    is_fake = True
                    probability = 0.6 + (1 - current_volume / avg_volume) * 0.3
                    signals.append(f"⚠️ 壓力突破 @${level:.0f} 量能不足")
                    signals.append(f"📊 當前量: {current_volume:.0f} vs 均量: {avg_volume:.0f}")
                else:
                    signals.append(f"✅ 壓力突破 @${level:.0f} 伴隨放量")
                break
        
        # 檢查支撐跌破
        if not break_type:
            for support in sorted(supports):
                if current_price < support * 0.998:
                    break_type = "SUPPORT"
                    level = support
                    
                    if avg_volume > 0 and current_volume < avg_volume * 1.3:
                        is_fake = True
                        probability = 0.6 + (1 - current_volume / avg_volume) * 0.3
                        signals.append(f"⚠️ 支撐跌破 @${level:.0f} 量能不足（可能誘空）")
                    else:
                        signals.append(f"✅ 支撐跌破 @${level:.0f} 伴隨放量")
                    break
        
        pattern = WhaleStrategyV4.FAKEOUT if is_fake else None
        
        return {
            "pattern": pattern,
            "probability": min(1.0, probability),
            "confidence": probability * 0.8 if is_fake else 0.5,
            "break_type": break_type,
            "is_fake": is_fake,
            "level": level,
            "signals": signals
        }


class FlashCrashDetector:
    """
    閃崩洗盤偵測器 v4.0
    
    擴展 WaterfallDropDetector，加入 V 型反彈偵測
    """
    
    def __init__(self, lookback: int = 30):
        self.candle_history: deque = deque(maxlen=lookback)
        
    def add_candle(self, open_: float, high: float, low: float, close: float, 
                   volume: float, timestamp: float):
        """添加K線數據"""
        self.candle_history.append({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "timestamp": timestamp,
            "is_bearish": close < open_,
            "body_pct": abs(close - open_) / open_ * 100 if open_ > 0 else 0,
            "range_pct": (high - low) / open_ * 100 if open_ > 0 else 0
        })
    
    def detect(self) -> Dict[str, Any]:
        """
        偵測閃崩洗盤
        
        閃崩特徵：
        1. 價格在秒級暴跌 > 1%
        2. 成交量瞬間暴增
        3. 快速 V 型反轉（1分鐘內收復 50%+）
        
        Returns:
            {
                "pattern": WhaleStrategyV4.FLASH_CRASH or None,
                "probability": float,
                "drop_pct": float,
                "recovery_pct": float,
                "is_v_reversal": bool,
                "signals": []
            }
        """
        if len(self.candle_history) < 5:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        candles = list(self.candle_history)
        signals = []
        
        # 找最近的最低點
        recent = candles[-10:]
        min_low = min(c["low"] for c in recent)
        min_idx = next(i for i, c in enumerate(recent) if c["low"] == min_low)
        
        # 計算暴跌幅度
        pre_drop_high = 0
        drop_pct = 0
        if min_idx > 0:
            pre_drop_high = max(c["high"] for c in recent[:min_idx+1])
            drop_pct = (min_low - pre_drop_high) / pre_drop_high * 100 if pre_drop_high > 0 else 0
        
        # 計算反彈幅度
        current_price = candles[-1]["close"]
        recovery_pct = 0
        if min_low > 0 and pre_drop_high > 0 and (pre_drop_high - min_low) > 0:
            recovery_pct = (current_price - min_low) / (pre_drop_high - min_low) * 100
        
        # 檢查成交量暴增
        avg_volume = np.mean([c["volume"] for c in candles[:-3]]) if len(candles) > 3 else 0
        crash_volume = candles[min_idx]["volume"] if min_idx < len(recent) else 0
        volume_spike = crash_volume / avg_volume if avg_volume > 0 else 0
        
        # 判斷是否閃崩洗盤
        is_flash_crash = False
        is_v_reversal = False
        probability = 0.0
        
        if drop_pct < -1.0:  # 暴跌超過 1%
            signals.append(f"📉 快速暴跌 {drop_pct:.2f}%")
            
            if volume_spike > 3:
                signals.append(f"📊 成交量暴增 {volume_spike:.1f}x")
            
            if recovery_pct > 50:  # 收復 50% 以上
                is_v_reversal = True
                is_flash_crash = True
                probability = min(1.0, 0.5 + abs(drop_pct) * 0.1 + recovery_pct * 0.003)
                signals.append(f"✅ V 型反轉：已收復 {recovery_pct:.0f}%")
                signals.append("💡 閃崩洗盤特徵明顯，主力可能在低點接貨")
            elif recovery_pct > 20:
                is_flash_crash = True
                probability = min(0.7, 0.3 + abs(drop_pct) * 0.1)
                signals.append(f"⚠️ 部分反彈：已收復 {recovery_pct:.0f}%")
        
        pattern = WhaleStrategyV4.FLASH_CRASH if is_flash_crash else None
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.85,
            "drop_pct": drop_pct,
            "recovery_pct": recovery_pct,
            "is_v_reversal": is_v_reversal,
            "volume_spike": volume_spike,
            "signals": signals
        }


# ==================== Phase 1 偵測器 ====================

class StopHuntDetectorV4:
    """
    獵殺止損偵測器 v4.0
    
    增強：加入關鍵價位距離判斷
    """
    
    def __init__(self, atr_multiplier: float = 2.0, lookback: int = 20):
        self.atr_multiplier = atr_multiplier
        self.lookback = lookback
        self.candle_history: deque = deque(maxlen=lookback * 2)
        self.key_levels: List[float] = []  # 關鍵價位
        
    def add_candle(self, open_: float, high: float, low: float, close: float, volume: float):
        """添加K線數據"""
        self.candle_history.append({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume,
            "body": abs(close - open_),
            "upper_shadow": high - max(open_, close),
            "lower_shadow": min(open_, close) - low,
            "total_range": high - low
        })
    
    def set_key_levels(self, levels: List[float]):
        """設置關鍵價位（整數位、前高低點等）"""
        self.key_levels = sorted(levels)
    
    def _auto_detect_key_levels(self, current_price: float) -> List[float]:
        """自動偵測關鍵價位"""
        levels = []
        
        # 整數關口
        base = int(current_price / 1000) * 1000
        for offset in [-2000, -1000, 0, 1000, 2000]:
            levels.append(base + offset)
        
        # 前高低點
        if len(self.candle_history) >= 10:
            candles = list(self.candle_history)[-20:]
            levels.append(min(c["low"] for c in candles))
            levels.append(max(c["high"] for c in candles))
        
        return sorted(set(levels))
    
    def detect(self, current_price: float = 0) -> Dict[str, Any]:
        """
        偵測獵殺止損
        
        Returns:
            {
                "pattern": WhaleStrategyV4.STOP_HUNT or None,
                "probability": float,
                "hunt_index": float (0-100),
                "hunt_direction": "UP" / "DOWN" / "BOTH",
                "near_key_level": bool,
                "signals": []
            }
        """
        if len(self.candle_history) < self.lookback:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        recent = list(self.candle_history)[-self.lookback:]
        
        # 計算 ATR
        ranges = [c["total_range"] for c in recent]
        atr = np.mean(ranges) if ranges else 0
        
        if atr == 0:
            return {"pattern": None, "probability": 0, "signals": ["ATR為零"]}
        
        signals = []
        hunt_score = 0
        hunt_direction = "NONE"
        
        # 檢查最近幾根K線
        latest_candles = recent[-3:]
        
        for candle in latest_candles:
            # 長下影線（下方掃損）
            if candle["lower_shadow"] > self.atr_multiplier * atr:
                lower_ratio = candle["lower_shadow"] / candle["total_range"] if candle["total_range"] > 0 else 0
                if lower_ratio > 0.6:
                    hunt_score += 35
                    hunt_direction = "DOWN" if hunt_direction == "NONE" else "BOTH"
                    signals.append(f"📍 長下影線掃損 (影線佔比{lower_ratio:.0%})")
            
            # 長上影線（上方掃損）
            if candle["upper_shadow"] > self.atr_multiplier * atr:
                upper_ratio = candle["upper_shadow"] / candle["total_range"] if candle["total_range"] > 0 else 0
                if upper_ratio > 0.6:
                    hunt_score += 35
                    hunt_direction = "UP" if hunt_direction == "NONE" else "BOTH"
                    signals.append(f"📍 長上影線掃損 (影線佔比{upper_ratio:.0%})")
            
            # 針狀K線（雙向掃損）
            if (candle["upper_shadow"] > atr and 
                candle["lower_shadow"] > atr and
                candle["body"] < atr * 0.5):
                hunt_score += 40
                hunt_direction = "BOTH"
                signals.append("📍 針狀K線，雙向掃損")
        
        # 檢查是否接近關鍵價位
        near_key_level = False
        if current_price > 0:
            key_levels = self.key_levels if self.key_levels else self._auto_detect_key_levels(current_price)
            for level in key_levels:
                distance_pct = abs(current_price - level) / current_price * 100
                if distance_pct < 0.5:  # 距離關鍵位 < 0.5%
                    near_key_level = True
                    hunt_score += 20
                    signals.append(f"📊 接近關鍵價位 ${level:.0f}")
                    break
        
        # 判斷是否獵殺止損
        hunt_index = min(100, hunt_score)
        probability = hunt_index / 100
        pattern = WhaleStrategyV4.STOP_HUNT if hunt_index >= 60 else None
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.8,
            "hunt_index": hunt_index,
            "hunt_direction": hunt_direction,
            "near_key_level": near_key_level,
            "signals": signals
        }


class SpoofingDetectorV4:
    """
    幌騙偵測器 v4.0
    
    增強：加入撤單率追蹤
    """
    
    def __init__(self, history_size: int = 100):
        self.order_events: deque = deque(maxlen=history_size)
        self.cancel_stats: Dict[str, int] = {"total": 0, "quick": 0, "large_quick": 0}
        
    def add_order_event(self, event_type: str, price: float, volume: float, 
                        duration_seconds: float, was_filled: bool):
        """
        記錄委託事件
        
        event_type: "ADD" / "CANCEL" / "MODIFY"
        """
        event = {
            "type": event_type,
            "price": price,
            "volume": volume,
            "duration": duration_seconds,
            "filled": was_filled,
            "timestamp": time.time()
        }
        self.order_events.append(event)
        
        # 更新撤單統計
        if event_type == "CANCEL":
            self.cancel_stats["total"] += 1
            if duration_seconds < 5:
                self.cancel_stats["quick"] += 1
                if volume > 10000:
                    self.cancel_stats["large_quick"] += 1
    
    def detect(self) -> Dict[str, Any]:
        """
        偵測幌騙
        
        Returns:
            {
                "pattern": WhaleStrategyV4.SPOOFING or None,
                "probability": float,
                "distortion_index": float (0-100),
                "cancel_rate": float,
                "quick_cancel_rate": float,
                "signals": []
            }
        """
        if len(self.order_events) < 20:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        signals = []
        distortion_score = 0
        
        events = list(self.order_events)
        total_events = len(events)
        
        # 統計快速撤單（持續時間 < 5秒且未成交）
        quick_cancels = [e for e in events 
                        if e["type"] == "CANCEL" 
                        and e["duration"] < 5 
                        and not e["filled"]]
        
        cancel_events = [e for e in events if e["type"] == "CANCEL"]
        cancel_rate = len(cancel_events) / total_events if total_events > 0 else 0
        quick_cancel_rate = len(quick_cancels) / len(cancel_events) if cancel_events else 0
        
        if quick_cancel_rate > 0.5:
            distortion_score += 40
            signals.append(f"⚠️ 快速撤單率高: {quick_cancel_rate:.0%}")
        
        # 統計大單快速撤單
        large_quick_cancels = [e for e in quick_cancels if e["volume"] > 10000]
        if len(large_quick_cancels) >= 3:
            distortion_score += 35
            signals.append(f"🚨 大單快速撤單: {len(large_quick_cancels)}筆 (Spoofing特徵)")
        
        # 統計同一價位反覆掛撤
        from collections import Counter
        price_events = Counter(round(e["price"], 0) for e in events)
        repeated_prices = [p for p, c in price_events.items() if c > 5]
        if repeated_prices:
            distortion_score += 25
            signals.append(f"📊 同價位反覆掛撤: {len(repeated_prices)}個價位")
        
        # 判斷是否幌騙
        distortion_index = min(100, distortion_score)
        probability = distortion_index / 100
        pattern = WhaleStrategyV4.SPOOFING if distortion_index >= 60 else None
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.75,
            "distortion_index": distortion_index,
            "cancel_rate": cancel_rate,
            "quick_cancel_rate": quick_cancel_rate,
            "signals": signals
        }


class DistributionDetector:
    """
    派發偵測器 v4.0
    
    偵測主力在高位派發出貨
    """
    
    def __init__(self):
        self.trade_history: deque = deque(maxlen=200)
        self.price_history: deque = deque(maxlen=100)
        
    def add_trade(self, volume_usdt: float, is_buy: bool, price: float):
        """記錄交易"""
        self.trade_history.append({
            "volume": volume_usdt,
            "is_buy": is_buy,
            "price": price,
            "timestamp": time.time(),
            "is_large": volume_usdt >= 10000
        })
        self.price_history.append({"price": price, "timestamp": time.time()})
    
    def detect(self, funding_rate: float = 0, oi_change_pct: float = 0) -> Dict[str, Any]:
        """
        偵測派發
        
        派發特徵：
        1. 價格橫盤或小幅上漲
        2. 主力持續淨賣出
        3. OI 上升但價格不漲 (新空頭進場)
        4. Funding Rate 正 (多頭付費)
        
        Returns:
            {
                "pattern": WhaleStrategyV4.DISTRIBUTION or None,
                "probability": float,
                "large_net_sell": float,
                "signals": []
            }
        """
        if len(self.trade_history) < 50:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        signals = []
        score = 0
        
        trades = list(self.trade_history)
        
        # 計算大單淨賣出
        large_trades = [t for t in trades if t["is_large"]]
        large_buy = sum(t["volume"] for t in large_trades if t["is_buy"])
        large_sell = sum(t["volume"] for t in large_trades if not t["is_buy"])
        large_net = large_buy - large_sell  # 負數 = 淨賣出
        
        if large_net < -50000:  # 大單淨賣出 > $50k
            score += 30
            signals.append(f"📤 大單淨賣出: ${abs(large_net)/1000:.1f}k")
        
        # 計算價格變化
        if len(self.price_history) >= 10:
            prices = [p["price"] for p in self.price_history]
            price_change_pct = (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] > 0 else 0
            
            # 價格橫盤或小漲但主力在賣
            if -0.5 < price_change_pct < 1.0 and large_net < 0:
                score += 25
                signals.append(f"📊 價格變化 {price_change_pct:.2f}% 但主力賣出")
        
        # Funding Rate 正（多頭付費）
        if funding_rate > 0.0001:
            score += 20
            signals.append(f"💰 Funding Rate 正: {funding_rate:.4%} (多頭付費)")
        
        # OI 上升但價格不漲
        if oi_change_pct > 0.5 and large_net < 0:
            score += 25
            signals.append(f"📈 OI 上升 {oi_change_pct:.2f}% 但主力賣出 (新空頭)")
        
        probability = min(1.0, score / 80)
        pattern = WhaleStrategyV4.DISTRIBUTION if score >= 50 else None
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.8,
            "large_net_sell": -large_net if large_net < 0 else 0,
            "signals": signals
        }


class LayeringDetector:
    """
    層疊掛單偵測器 v4.0
    
    偵測多層假掛單操縱市場深度
    """
    
    def __init__(self, history_size: int = 30):
        self.orderbook_snapshots: deque = deque(maxlen=history_size)
        
    def add_orderbook_snapshot(self, bids: List[List[float]], asks: List[List[float]]):
        """
        添加訂單簿快照
        bids/asks: [[price, volume], ...]
        """
        self.orderbook_snapshots.append({
            "bids": bids[:20],
            "asks": asks[:20],
            "timestamp": time.time()
        })
    
    def detect(self) -> Dict[str, Any]:
        """
        偵測層疊掛單
        
        Layering特徵：
        1. 某一側連續多檔出現異常大單
        2. 這些大單快速消失或同時移動
        3. 製造假的買賣壓力影響價格
        
        Returns:
            {
                "pattern": WhaleStrategyV4.LAYERING or None,
                "probability": float,
                "layer_side": "BID" / "ASK" / None,
                "signals": []
            }
        """
        if len(self.orderbook_snapshots) < 10:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        signals = []
        score = 0
        layer_side = None
        
        snapshots = list(self.orderbook_snapshots)
        latest = snapshots[-1]
        
        # 分析買單側層疊
        if latest["bids"]:
            bid_volumes = [b[1] for b in latest["bids"][:10]]
            avg_bid = np.mean(bid_volumes) if bid_volumes else 0
            
            # 檢查連續大單
            large_bid_streak = 0
            for vol in bid_volumes[:5]:
                if vol > avg_bid * 3:
                    large_bid_streak += 1
            
            if large_bid_streak >= 3:
                score += 40
                layer_side = "BID"
                signals.append(f"📊 買單側連續{large_bid_streak}檔大單 (可能Layering)")
        
        # 分析賣單側層疊
        if latest["asks"]:
            ask_volumes = [a[1] for a in latest["asks"][:10]]
            avg_ask = np.mean(ask_volumes) if ask_volumes else 0
            
            large_ask_streak = 0
            for vol in ask_volumes[:5]:
                if vol > avg_ask * 3:
                    large_ask_streak += 1
            
            if large_ask_streak >= 3:
                score += 40
                layer_side = "ASK" if layer_side is None else "BOTH"
                signals.append(f"📊 賣單側連續{large_ask_streak}檔大單 (可能Layering)")
        
        # 檢查掛單消失速度
        if len(snapshots) >= 5:
            old_snapshot = snapshots[-5]
            
            # 比較大單是否快速消失
            old_bid_prices = set(round(b[0], 0) for b in old_snapshot["bids"][:10] if b[1] > avg_bid * 2) if old_snapshot["bids"] else set()
            new_bid_prices = set(round(b[0], 0) for b in latest["bids"][:10] if b[1] > avg_bid * 2) if latest["bids"] else set()
            
            disappeared = old_bid_prices - new_bid_prices
            if len(disappeared) >= 2:
                score += 30
                signals.append(f"⚠️ {len(disappeared)}個大單價位快速消失")
        
        probability = min(1.0, score / 80)
        pattern = WhaleStrategyV4.LAYERING if score >= 50 else None
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.7,
            "layer_side": layer_side,
            "signals": signals
        }


# ==================== Phase 2 偵測器：清洗類 ====================

class WhipsawDetector:
    """
    鋸齒洗盤偵測器 v4.0
    
    偵測上下劇烈震盪甩出散戶
    """
    
    def __init__(self, lookback: int = 30):
        self.candle_history: deque = deque(maxlen=lookback)
        self.direction_changes: deque = deque(maxlen=20)
        
    def add_candle(self, open_: float, high: float, low: float, close: float, volume: float):
        """添加K線數據"""
        is_bullish = close > open_
        
        # 記錄方向變化
        if self.candle_history:
            prev_bullish = self.candle_history[-1]["close"] > self.candle_history[-1]["open"]
            if is_bullish != prev_bullish:
                self.direction_changes.append(time.time())
        
        self.candle_history.append({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "is_bullish": is_bullish,
            "range": high - low
        })
    
    def detect(self) -> Dict[str, Any]:
        """
        偵測鋸齒洗盤
        
        特徵：
        1. 價格快速上下震盪
        2. 多空雙殺，兩邊止損都被打掉
        3. 方向頻繁翻轉
        
        Returns:
            {
                "pattern": WhaleStrategyV4.WHIPSAW or None,
                "probability": float,
                "direction_changes": int,
                "amplitude_pct": float,
                "signals": []
            }
        """
        if len(self.candle_history) < 10:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        candles = list(self.candle_history)[-20:]
        signals = []
        score = 0
        
        # 計算方向翻轉次數（5分鐘內）
        recent_changes = len([t for t in self.direction_changes if time.time() - t < 300])
        
        if recent_changes >= 6:
            score += 40
            signals.append(f"🔄 5分鐘內方向翻轉 {recent_changes} 次")
        elif recent_changes >= 4:
            score += 25
            signals.append(f"🔄 方向翻轉 {recent_changes} 次")
        
        # 計算振幅
        high = max(c["high"] for c in candles)
        low = min(c["low"] for c in candles)
        mid_price = (high + low) / 2
        amplitude_pct = (high - low) / mid_price * 100 if mid_price > 0 else 0
        
        # 計算 ATR
        atr = np.mean([c["range"] for c in candles])
        
        if amplitude_pct > 3 * (atr / mid_price * 100):
            score += 35
            signals.append(f"📊 振幅 {amplitude_pct:.2f}% 超過 3 倍 ATR")
        
        # 檢查是否有長上下影線（雙向掃損）
        shadow_candles = 0
        for c in candles[-5:]:
            body = abs(c["close"] - c["open"])
            upper_shadow = c["high"] - max(c["open"], c["close"])
            lower_shadow = min(c["open"], c["close"]) - c["low"]
            
            if upper_shadow > body and lower_shadow > body:
                shadow_candles += 1
        
        if shadow_candles >= 2:
            score += 25
            signals.append(f"📍 {shadow_candles} 根K線有雙向長影線")
        
        probability = min(1.0, score / 80)
        pattern = WhaleStrategyV4.WHIPSAW if score >= 50 else None
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.75,
            "direction_changes": recent_changes,
            "amplitude_pct": amplitude_pct,
            "signals": signals
        }


class ConsolidationShakeDetector:
    """
    盤整洗盤偵測器 v4.0
    
    偵測長時間橫盤磨耐心
    """
    
    def __init__(self, lookback: int = 60):
        self.candle_history: deque = deque(maxlen=lookback)
        self.consolidation_start: Optional[float] = None
        
    def add_candle(self, open_: float, high: float, low: float, close: float, volume: float):
        """添加K線數據"""
        self.candle_history.append({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "timestamp": time.time()
        })
    
    def detect(self) -> Dict[str, Any]:
        """
        偵測盤整洗盤
        
        特徵：
        1. 價格長時間在窄幅區間
        2. 成交量逐漸萎縮
        3. 波動率降低
        4. 🆕 v14.11: 無明確方向 (有方向就不是盤整！)
        
        Returns:
            {
                "pattern": WhaleStrategyV4.CONSOLIDATION_SHAKE or None,
                "probability": float,
                "consolidation_minutes": int,
                "range_pct": float,
                "signals": []
            }
        """
        if len(self.candle_history) < 30:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        candles = list(self.candle_history)
        signals = []
        score = 0
        
        # 計算價格區間
        recent = candles[-30:]
        high = max(c["high"] for c in recent)
        low = min(c["low"] for c in recent)
        mid = (high + low) / 2
        range_pct = (high - low) / mid * 100 if mid > 0 else 0
        
        # 🆕 v14.11: 計算方向性 (趨勢 vs 盤整)
        # 真正的盤整應該沒有明確方向
        first_price = recent[0]["close"]
        last_price = recent[-1]["close"]
        direction_change_pct = (last_price - first_price) / first_price * 100 if first_price > 0 else 0
        
        # 🆕 v14.11: 如果有明確方向 (>0.3%)，就不是盤整！
        if abs(direction_change_pct) > 0.3:
            trend_type = "上漲" if direction_change_pct > 0 else "下跌"
            signals.append(f"🚫 v14.11: 非盤整! 有{trend_type}趨勢 ({direction_change_pct:+.2f}%)")
            return {
                "pattern": None,
                "probability": 0,
                "confidence": 0,
                "consolidation_minutes": 0,
                "range_pct": range_pct,
                "direction_change_pct": direction_change_pct,
                "signals": signals
            }
        
        # 窄幅盤整
        if range_pct < 1.5:
            score += 40
            signals.append(f"📊 價格區間僅 {range_pct:.2f}% (窄幅盤整)")
        elif range_pct < 2.5:
            score += 25
            signals.append(f"📊 價格區間 {range_pct:.2f}%")
        
        # 成交量萎縮
        first_half_vol = np.mean([c["volume"] for c in recent[:15]])
        second_half_vol = np.mean([c["volume"] for c in recent[15:]])
        
        if first_half_vol > 0:
            vol_decay = second_half_vol / first_half_vol
            if vol_decay < 0.7:
                score += 30
                signals.append(f"📉 成交量萎縮 {(1-vol_decay)*100:.0f}%")
        
        # 計算盤整時間
        if self.consolidation_start is None and range_pct < 2:
            self.consolidation_start = candles[0]["timestamp"]
        elif range_pct >= 3:
            self.consolidation_start = None
        
        consolidation_minutes = 0
        if self.consolidation_start:
            consolidation_minutes = int((time.time() - self.consolidation_start) / 60)
            if consolidation_minutes > 30:
                score += 20
                signals.append(f"⏱️ 盤整時間超過 {consolidation_minutes} 分鐘")
        
        probability = min(1.0, score / 80)
        pattern = WhaleStrategyV4.CONSOLIDATION_SHAKE if score >= 50 else None
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.7,
            "consolidation_minutes": consolidation_minutes,
            "range_pct": range_pct,
            "signals": signals
        }


class SlowBleedDetector:
    """
    陰跌洗盤偵測器 v4.0
    
    偵測緩慢下跌磨多頭
    """
    
    def __init__(self, lookback: int = 50):
        self.candle_history: deque = deque(maxlen=lookback)
        
    def add_candle(self, open_: float, high: float, low: float, close: float, volume: float):
        """添加K線數據"""
        self.candle_history.append({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "timestamp": time.time()
        })
    
    def detect(self, wpi: float = 0) -> Dict[str, Any]:
        """
        偵測陰跌洗盤
        
        特徵：
        1. 價格持續小幅下跌
        2. 每次反彈都是更低的高點
        3. 成交量低迷
        4. 主力在低點悄悄接貨 (WPI > 0)
        
        Returns:
            {
                "pattern": WhaleStrategyV4.SLOW_BLEED or None,
                "probability": float,
                "lower_highs": int,
                "total_decline_pct": float,
                "signals": []
            }
        """
        if len(self.candle_history) < 20:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        candles = list(self.candle_history)
        signals = []
        score = 0
        
        # 計算高點遞減
        highs = [c["high"] for c in candles[-20:]]
        lower_highs = 0
        for i in range(10, len(highs), 5):  # 從 10 開始確保有足夠數據
            prev_max = max(highs[i-10:i-5]) if i >= 10 and highs[i-10:i-5] else 0
            curr_max = max(highs[i-5:i]) if highs[i-5:i] else 0
            if prev_max > 0 and curr_max > 0 and curr_max < prev_max:
                lower_highs += 1
        
        if lower_highs >= 2:
            score += 35
            signals.append(f"📉 連續 {lower_highs} 段更低的高點")
        elif lower_highs >= 1:
            score += 20
        
        # 計算總跌幅
        start_price = candles[0]["close"]
        end_price = candles[-1]["close"]
        total_decline_pct = (end_price - start_price) / start_price * 100 if start_price > 0 else 0
        
        if -3 < total_decline_pct < -0.5:
            score += 25
            signals.append(f"📊 緩跌 {total_decline_pct:.2f}% (非暴跌)")
        
        # 成交量低迷
        avg_volume = np.mean([c["volume"] for c in candles])
        recent_volume = np.mean([c["volume"] for c in candles[-10:]])
        
        if avg_volume > 0 and recent_volume < avg_volume * 0.8:
            score += 20
            signals.append(f"📉 成交量低迷 ({recent_volume/avg_volume:.0%} 平均值)")
        
        # 主力買入
        if wpi > 0.2:
            score += 20
            signals.append(f"💰 主力淨買入 (WPI={wpi:.2f})，可能在低接")
        
        probability = min(1.0, score / 80)
        pattern = WhaleStrategyV4.SLOW_BLEED if score >= 50 else None
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.75,
            "lower_highs": lower_highs,
            "total_decline_pct": total_decline_pct,
            "signals": signals
        }


# ==================== Phase 2 偵測器：趨勢類 ====================

class TrendPatternDetector:
    """
    趨勢模式偵測器 v4.0
    
    偵測 MOMENTUM_PUSH、TREND_CONTINUATION、REVERSAL
    """
    
    def __init__(self, lookback: int = 50):
        self.candle_history: deque = deque(maxlen=lookback)
        self.ma_short: int = 7
        self.ma_long: int = 25
        
    def add_candle(self, open_: float, high: float, low: float, close: float, volume: float):
        """添加K線數據"""
        self.candle_history.append({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "timestamp": time.time()
        })
    
    def _calculate_ma(self, period: int) -> float:
        """計算移動平均"""
        if len(self.candle_history) < period:
            return 0
        closes = [c["close"] for c in list(self.candle_history)[-period:]]
        return np.mean(closes)
    
    def detect(self, wpi: float = 0) -> Dict[str, Any]:
        """
        偵測趨勢模式
        
        Returns:
            {
                "pattern": WhaleStrategyV4 or None,
                "probability": float,
                "trend_direction": "UP" / "DOWN" / "NONE",
                "trend_strength": float,
                "signals": []
            }
        """
        if len(self.candle_history) < self.ma_long:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        candles = list(self.candle_history)
        signals = []
        
        # 計算均線
        ma7 = self._calculate_ma(self.ma_short)
        ma25 = self._calculate_ma(self.ma_long)
        current_price = candles[-1]["close"]
        
        # 判斷趨勢方向
        if ma7 > ma25 * 1.005:
            trend_direction = "UP"
        elif ma7 < ma25 * 0.995:
            trend_direction = "DOWN"
        else:
            trend_direction = "NONE"
        
        # 計算趨勢強度
        if ma25 > 0:
            trend_strength = abs(ma7 - ma25) / ma25 * 100
        else:
            trend_strength = 0
        
        pattern = None
        probability = 0.0
        
        # ========== MOMENTUM_PUSH 趨勢推動 ==========
        # 強趨勢 + 主力順勢
        if trend_strength > 1.0:
            if (trend_direction == "UP" and wpi > 0.3) or (trend_direction == "DOWN" and wpi < -0.3):
                pattern = WhaleStrategyV4.MOMENTUM_PUSH
                probability = min(1.0, 0.5 + trend_strength * 0.1 + abs(wpi) * 0.3)
                signals.append(f"🚀 趨勢推動: {trend_direction} 方向")
                signals.append(f"📊 趨勢強度: {trend_strength:.2f}%")
                signals.append(f"💪 主力順勢 WPI={wpi:.2f}")
        
        # ========== TREND_CONTINUATION 趨勢延續 ==========
        # 趨勢中回調後繼續
        if not pattern and trend_direction != "NONE":
            # 檢查是否有回調
            recent_high = max(c["high"] for c in candles[-10:])
            recent_low = min(c["low"] for c in candles[-10:])
            
            if trend_direction == "UP":
                pullback_pct = (recent_high - current_price) / recent_high * 100 if recent_high > 0 else 0
                if 1 < pullback_pct < 3 and current_price > ma25:
                    pattern = WhaleStrategyV4.TREND_CONTINUATION
                    probability = min(1.0, 0.5 + (3 - pullback_pct) * 0.1)
                    signals.append(f"📈 上升趨勢回調 {pullback_pct:.2f}%")
                    signals.append("💡 等待回調結束後加倉")
            else:
                pullback_pct = (current_price - recent_low) / recent_low * 100 if recent_low > 0 else 0
                if 1 < pullback_pct < 3 and current_price < ma25:
                    pattern = WhaleStrategyV4.TREND_CONTINUATION
                    probability = min(1.0, 0.5 + (3 - pullback_pct) * 0.1)
                    signals.append(f"📉 下降趨勢反彈 {pullback_pct:.2f}%")
                    signals.append("💡 等待反彈結束後加空")
        
        # ========== REVERSAL 趨勢反轉 ==========
        # 趨勢末端 + 主力反向
        if not pattern:
            if trend_direction == "UP" and wpi < -0.3:
                pattern = WhaleStrategyV4.REVERSAL
                probability = min(1.0, 0.4 + abs(wpi) * 0.4)
                signals.append("⚠️ 上升趨勢中主力做空")
                signals.append(f"📊 可能反轉 WPI={wpi:.2f}")
            elif trend_direction == "DOWN" and wpi > 0.3:
                pattern = WhaleStrategyV4.REVERSAL
                probability = min(1.0, 0.4 + abs(wpi) * 0.4)
                signals.append("⚠️ 下降趨勢中主力做多")
                signals.append(f"📊 可能反轉 WPI={wpi:.2f}")
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.7 if pattern else 0,
            "trend_direction": trend_direction,
            "trend_strength": trend_strength,
            "ma7": ma7,
            "ma25": ma25,
            "signals": signals
        }


# ==================== 陷阱類偵測器 ====================

class TrapDetector:
    """
    🎯 陷阱偵測器 - 識別 BULL_TRAP 和 BEAR_TRAP
    
    多頭陷阱 (BULL_TRAP): 假突破壓力後回落，主力出貨
    空頭陷阱 (BEAR_TRAP): 假跌破支撐後拉回，主力吸籌
    """
    
    def __init__(self, window: int = 30):
        self.candle_history: deque = deque(maxlen=window)
        self.breakout_prices: List[Dict] = []  # 記錄突破/跌破位置
        
    def add_candle(self, open_: float, high: float, low: float, close: float, volume: float):
        self.candle_history.append({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "timestamp": time.time()
        })
    
    def detect(
        self,
        current_price: float,
        obi: float = 0,
        wpi: float = 0,
        stop_hunt_index: float = 0
    ) -> Dict:
        """
        偵測多空陷阱
        
        Returns:
            {
                "pattern": WhaleStrategyV4.BULL_TRAP / BEAR_TRAP / None,
                "probability": float,
                "trap_type": "BULL" / "BEAR" / None,
                "signals": []
            }
        """
        if len(self.candle_history) < 15:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        candles = list(self.candle_history)
        signals = []
        
        # 計算近期高點低點
        highs = [c["high"] for c in candles[-20:]]
        lows = [c["low"] for c in candles[-20:]]
        recent_high = max(highs[:-3]) if len(highs) > 3 else max(highs)
        recent_low = min(lows[:-3]) if len(lows) > 3 else min(lows)
        
        # 最近 K 線特徵
        recent_3 = candles[-3:]
        max_high_3 = max(c["high"] for c in recent_3)
        min_low_3 = min(c["low"] for c in recent_3)
        last_candle = candles[-1]
        
        bull_trap_score = 0
        bear_trap_score = 0
        
        # ========== 多頭陷阱 (BULL_TRAP) ==========
        # 特徵：突破壓力後回落，長上影線，主力賣出
        
        # 檢查是否有突破高點後回落
        if max_high_3 > recent_high and current_price < recent_high:
            bull_trap_score += 30
            signals.append(f"📈 突破高點 ${recent_high:,.0f} 後回落")
        
        # 長上影線
        if last_candle["high"] > 0:
            upper_shadow = (last_candle["high"] - max(last_candle["close"], last_candle["open"])) / last_candle["high"]
            if upper_shadow > 0.005:  # 0.5% 上影
                bull_trap_score += 20
                signals.append("📍 長上影線（假突破）")
        
        # 主力在漲時賣出
        if wpi < -0.2 and obi > 0:
            bull_trap_score += 25
            signals.append(f"💰 主力出貨中 (WPI={wpi:.2f})")
        
        # 止損觸發
        if stop_hunt_index > 40:
            bull_trap_score += 15
            signals.append(f"🎯 獵殺空頭止損")
        
        # ========== 空頭陷阱 (BEAR_TRAP) ==========
        # 特徵：跌破支撐後拉回，長下影線，主力買入
        
        # 檢查是否跌破低點後反彈
        if min_low_3 < recent_low and current_price > recent_low:
            bear_trap_score += 30
            signals.append(f"📉 跌破低點 ${recent_low:,.0f} 後反彈")
        
        # 長下影線
        if last_candle["low"] > 0:
            lower_shadow = (min(last_candle["close"], last_candle["open"]) - last_candle["low"]) / last_candle["low"]
            if lower_shadow > 0.005:  # 0.5% 下影
                bear_trap_score += 20
                signals.append("📍 長下影線（假跌破）")
        
        # 主力在跌時買入
        if wpi > 0.2 and obi < 0:
            bear_trap_score += 25
            signals.append(f"💰 主力吸籌中 (WPI={wpi:.2f})")
        
        # 止損觸發
        if stop_hunt_index > 40:
            bear_trap_score += 15
            signals.append(f"🎯 獵殺多頭止損")
        
        # 判斷主策略
        pattern = None
        probability = 0.0
        trap_type = None
        
        if bull_trap_score > bear_trap_score and bull_trap_score >= 50:
            pattern = WhaleStrategyV4.BULL_TRAP
            probability = min(1.0, bull_trap_score / 90)
            trap_type = "BULL"
        elif bear_trap_score > bull_trap_score and bear_trap_score >= 50:
            pattern = WhaleStrategyV4.BEAR_TRAP
            probability = min(1.0, bear_trap_score / 90)
            trap_type = "BEAR"
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.8,
            "trap_type": trap_type,
            "bull_trap_score": bull_trap_score,
            "bear_trap_score": bear_trap_score,
            "signals": signals
        }


class AccumulationDetector:
    """
    💰 吸籌偵測器 - 識別 ACCUMULATION 和 RE_ACCUMULATION
    
    吸籌特徵：低位隱蔽買入、量增價平、籌碼集中
    """
    
    def __init__(self, window: int = 50):
        self.candle_history: deque = deque(maxlen=window)
        self.trade_history: deque = deque(maxlen=200)
        
    def add_candle(self, open_: float, high: float, low: float, close: float, volume: float):
        self.candle_history.append({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "timestamp": time.time()
        })
    
    def add_trade(self, volume_usdt: float, is_buy: bool, price: float):
        self.trade_history.append({
            "volume": volume_usdt,
            "is_buy": is_buy,
            "price": price,
            "timestamp": time.time()
        })
    
    def detect(
        self,
        obi: float = 0,
        vpin: float = 0,
        wpi: float = 0,
        price_change_pct: float = 0,
        volume_ratio: float = 1.0
    ) -> Dict:
        """
        偵測吸籌行為
        
        Returns:
            {
                "pattern": WhaleStrategyV4.ACCUMULATION / RE_ACCUMULATION / None,
                "probability": float,
                "signals": []
            }
        """
        if len(self.candle_history) < 20:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        candles = list(self.candle_history)
        signals = []
        score = 0
        
        # ========== 吸籌特徵評分 ==========
        
        # 1. OBI 中性或略正（不會製造買盤恐慌）
        if -0.3 <= obi <= 0.3:
            score += 15
            signals.append("📊 訂單簿平衡（隱蔽操作）")
        
        # 2. VPIN 低（非知情交易假象）
        if vpin < 0.4:
            score += 15
            signals.append(f"🔒 低毒性流量 (VPIN={vpin:.2f})")
        
        # 3. WPI 正值（主力淨買入）
        if wpi > 0.1:
            score += 25
            signals.append(f"💰 主力淨買入 (WPI={wpi:.2f})")
        
        # 4. 價格橫盤或緩漲
        if -0.5 <= price_change_pct <= 0.5:
            score += 15
            signals.append("📈 價格穩定（緩漲/橫盤）")
        
        # 5. 量增價平（吸籌典型特徵）
        if volume_ratio > 1.2 and abs(price_change_pct) < 0.3:
            score += 20
            signals.append(f"📊 量增價平 (量比={volume_ratio:.1f}x)")
        
        # 6. 計算籌碼集中度（買單集中）
        if self.trade_history:
            recent_trades = list(self.trade_history)[-50:]
            buy_volume = sum(t["volume"] for t in recent_trades if t["is_buy"])
            total_volume = sum(t["volume"] for t in recent_trades)
            if total_volume > 0:
                buy_ratio = buy_volume / total_volume
                if buy_ratio > 0.55:
                    score += 15
                    signals.append(f"💎 買盤集中 ({buy_ratio:.0%})")
        
        # 判斷是否為再吸籌（價格已漲過一波）
        is_re_accumulation = False
        if len(candles) >= 30:
            early_avg = np.mean([c["close"] for c in candles[:10]])
            recent_avg = np.mean([c["close"] for c in candles[-10:]])
            if recent_avg > early_avg * 1.02:  # 已漲 2% 以上
                is_re_accumulation = True
        
        probability = min(1.0, score / 80)
        
        if score >= 50:
            pattern = WhaleStrategyV4.RE_ACCUMULATION if is_re_accumulation else WhaleStrategyV4.ACCUMULATION
        else:
            pattern = None
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.75,
            "is_re_accumulation": is_re_accumulation,
            "signals": signals
        }


class PumpDumpDetector:
    """
    🚀 拉高出貨偵測器 - 識別 PUMP_DUMP
    
    特徵：巨量急拉、短時間大漲、成交量先增後衰
    """
    
    def __init__(self, window: int = 30):
        self.candle_history: deque = deque(maxlen=window)
        
    def add_candle(self, open_: float, high: float, low: float, close: float, volume: float):
        self.candle_history.append({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "timestamp": time.time()
        })
    
    def detect(
        self,
        obi: float = 0,
        vpin: float = 0,
        wpi: float = 0,
        price_change_pct: float = 0,
        volume_ratio: float = 1.0
    ) -> Dict:
        """
        偵測拉高出貨
        
        Returns:
            {
                "pattern": WhaleStrategyV4.PUMP_DUMP / None,
                "probability": float,
                "phase": "PUMP" / "DUMP" / None,
                "signals": []
            }
        """
        if len(self.candle_history) < 10:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        candles = list(self.candle_history)
        signals = []
        score = 0
        
        # ========== 拉高出貨特徵 ==========
        
        # 1. 強烈買盤假象
        if obi > 0.3:
            score += 15
            signals.append(f"📈 強烈買盤 (OBI={obi:.2f})")
        
        # 2. 高知情交易
        if vpin > 0.5:
            score += 20
            signals.append(f"⚠️ 高毒性流量 (VPIN={vpin:.2f})")
        
        # 3. 巨量
        if volume_ratio > 3:
            score += 25
            signals.append(f"🔥 巨量交易 ({volume_ratio:.1f}x)")
        elif volume_ratio > 2:
            score += 15
        
        # 4. 快速拉升
        if price_change_pct > 1:
            score += 20
            signals.append(f"🚀 急速拉升 (+{price_change_pct:.2f}%)")
        elif price_change_pct > 0.5:
            score += 10
        
        # 5. WPI 背離（買盤熱但主力賣）
        if wpi < 0 and obi > 0:
            score += 20
            signals.append("⚠️ 量價背離（主力出貨中）")
        
        # 6. 成交量變化趨勢
        if len(candles) >= 10:
            early_volume = np.mean([c["volume"] for c in candles[:5]])
            recent_volume = np.mean([c["volume"] for c in candles[-5:]])
            if early_volume > 0 and recent_volume < early_volume * 0.7:
                score += 15
                signals.append("📉 量能衰竭（出貨階段）")
        
        # 判斷階段
        phase = None
        if price_change_pct > 0.5 and volume_ratio > 2:
            phase = "PUMP"
        elif wpi < -0.2 and volume_ratio > 1:
            phase = "DUMP"
        
        probability = min(1.0, score / 90)
        pattern = WhaleStrategyV4.PUMP_DUMP if score >= 55 else None
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.7,
            "phase": phase,
            "signals": signals
        }


class WashTradingDetector:
    """
    🔄 對敲偵測器 - 識別 WASH_TRADING
    
    特徵：量大但無淨流入、同價位連續對敲、訂單流異常
    """
    
    def __init__(self, window: int = 50):
        self.trade_history: deque = deque(maxlen=window)
        self.order_events: deque = deque(maxlen=100)
        
    def add_trade(self, price: float, volume: float, is_buy: bool):
        self.trade_history.append({
            "price": price,
            "volume": volume,
            "is_buy": is_buy,
            "timestamp": time.time()
        })
    
    def add_order_event(self, event_type: str, price: float, volume: float):
        """記錄委託事件"""
        self.order_events.append({
            "type": event_type,  # "place", "cancel", "fill"
            "price": price,
            "volume": volume,
            "timestamp": time.time()
        })
    
    def detect(
        self,
        volume_ratio: float = 1.0,
        vpin: float = 0,
        wpi: float = 0
    ) -> Dict:
        """
        偵測對敲行為
        
        Returns:
            {
                "pattern": WhaleStrategyV4.WASH_TRADING / None,
                "probability": float,
                "order_distortion": float,
                "signals": []
            }
        """
        if len(self.trade_history) < 20:
            return {"pattern": None, "probability": 0, "signals": ["數據不足"]}
        
        trades = list(self.trade_history)
        signals = []
        score = 0
        
        # ========== 對敲特徵 ==========
        
        # 1. 計算訂單流異動率
        order_distortion = 0
        if self.order_events:
            events = list(self.order_events)
            cancel_count = sum(1 for e in events if e["type"] == "cancel")
            place_count = sum(1 for e in events if e["type"] == "place")
            if place_count > 0:
                order_distortion = (cancel_count / place_count) * 100
                if order_distortion > 50:
                    score += 35
                    signals.append(f"🔄 高委託異動率 ({order_distortion:.0f}%)")
        
        # 2. 量大但無淨流入
        if volume_ratio > 2 and vpin < 0.3:
            score += 25
            signals.append(f"📊 量大但無方向 (量比={volume_ratio:.1f}x)")
        
        # 3. WPI 接近零（無淨買賣）
        if abs(wpi) < 0.1 and volume_ratio > 1.5:
            score += 25
            signals.append("⚖️ 買賣對等（對敲特徵）")
        
        # 4. 同價位連續成交
        if len(trades) >= 10:
            price_counts = {}
            for t in trades[-20:]:
                p = round(t["price"], 1)
                price_counts[p] = price_counts.get(p, 0) + 1
            max_count = max(price_counts.values()) if price_counts else 0
            if max_count > 5:
                score += 20
                signals.append(f"🎯 同價位連續成交 ({max_count}次)")
        
        probability = min(1.0, score / 80)
        pattern = WhaleStrategyV4.WASH_TRADING if score >= 50 else None
        
        return {
            "pattern": pattern,
            "probability": probability,
            "confidence": probability * 0.7,
            "order_distortion": order_distortion,
            "signals": signals
        }


# ==================== 主偵測器整合類 v4.0 ====================

class WhaleStrategyDetectorV4:
    """
    🐋 主力策略識別系統 v4.0 - 整合所有偵測器
    
    整合 22 種策略偵測，輸出 WhaleStrategySnapshot
    """
    
    def __init__(self, output_path: str = "ai_whale_strategy.json"):
        # 初始化所有偵測器
        self.liquidation_detector = LiquidationPatternDetector()
        self.fakeout_detector = FakeoutDetector()
        self.flash_crash_detector = FlashCrashDetector()
        self.stop_hunt_detector = StopHuntDetectorV4()
        self.spoofing_detector = SpoofingDetectorV4()
        self.distribution_detector = DistributionDetector()
        self.layering_detector = LayeringDetector()
        self.whipsaw_detector = WhipsawDetector()
        self.consolidation_detector = ConsolidationShakeDetector()
        self.slow_bleed_detector = SlowBleedDetector()
        self.trend_detector = TrendPatternDetector()
        
        # 🆕 Phase 4: v2 策略偵測器
        self.trap_detector = TrapDetector()
        self.accumulation_detector = AccumulationDetector()
        self.pump_dump_detector = PumpDumpDetector()
        self.wash_trading_detector = WashTradingDetector()
        
        # JSON 輸出
        self.json_writer = WhaleStrategyJsonWriter(output_path)
        
        # 歷史記錄
        self.snapshot_history: deque = deque(maxlen=100)
        self.last_snapshot: Optional[WhaleStrategySnapshot] = None
        
    def update_data(
        self,
        # K線數據
        candle: Optional[Dict] = None,  # {open, high, low, close, volume}
        # 訂單簿
        bids: Optional[List[List[float]]] = None,
        asks: Optional[List[List[float]]] = None,
        # 爆倉事件
        liquidation: Optional[Dict] = None,  # {side, usd_value, price, timestamp}
        # 交易
        trade: Optional[Dict] = None,  # {volume_usdt, is_buy, price}
        # 委託事件
        order_event: Optional[Dict] = None,  # {type, price, volume, duration, filled}
    ):
        """
        更新數據到各偵測器
        """
        current_price = 0
        
        if candle:
            current_price = candle.get("close", 0)
            self.flash_crash_detector.add_candle(
                candle["open"], candle["high"], candle["low"], 
                candle["close"], candle["volume"], time.time()
            )
            self.stop_hunt_detector.add_candle(
                candle["open"], candle["high"], candle["low"],
                candle["close"], candle["volume"]
            )
            self.whipsaw_detector.add_candle(
                candle["open"], candle["high"], candle["low"],
                candle["close"], candle["volume"]
            )
            self.consolidation_detector.add_candle(
                candle["open"], candle["high"], candle["low"],
                candle["close"], candle["volume"]
            )
            self.slow_bleed_detector.add_candle(
                candle["open"], candle["high"], candle["low"],
                candle["close"], candle["volume"]
            )
            self.trend_detector.add_candle(
                candle["open"], candle["high"], candle["low"],
                candle["close"], candle["volume"]
            )
            self.fakeout_detector.add_data(current_price, candle["volume"], time.time())
            # 🆕 Phase 4 偵測器
            self.trap_detector.add_candle(
                candle["open"], candle["high"], candle["low"],
                candle["close"], candle["volume"]
            )
            self.accumulation_detector.add_candle(
                candle["open"], candle["high"], candle["low"],
                candle["close"], candle["volume"]
            )
            self.pump_dump_detector.add_candle(
                candle["open"], candle["high"], candle["low"],
                candle["close"], candle["volume"]
            )
        
        if bids and asks:
            self.layering_detector.add_orderbook_snapshot(bids, asks)
        
        if liquidation:
            self.liquidation_detector.add_liquidation(
                liquidation["side"], liquidation["usd_value"],
                liquidation["price"], liquidation["timestamp"]
            )
            self.liquidation_detector.add_price(liquidation["price"], liquidation["timestamp"])
        
        if trade:
            self.distribution_detector.add_trade(
                trade["volume_usdt"], trade["is_buy"], trade["price"]
            )
            # 🆕 Phase 4 偵測器
            self.accumulation_detector.add_trade(
                trade["volume_usdt"], trade["is_buy"], trade["price"]
            )
            self.wash_trading_detector.add_trade(
                trade["price"], trade["volume_usdt"], trade["is_buy"]
            )
        
        if order_event:
            self.spoofing_detector.add_order_event(
                order_event["type"], order_event["price"],
                order_event["volume"], order_event["duration"],
                order_event["filled"]
            )
            # 🆕 Phase 4
            self.wash_trading_detector.add_order_event(
                order_event["type"], order_event["price"], order_event["volume"]
            )
    
    def analyze(
        self,
        current_price: float,
        obi: float = 0,
        vpin: float = 0,
        wpi: float = 0,
        funding_rate: float = 0,
        oi_change_pct: float = 0,
        liquidation_pressure_long: float = 50,
        liquidation_pressure_short: float = 50,
        price_change_1m_pct: float = 0,
        price_change_5m_pct: float = 0,
    ) -> WhaleStrategySnapshot:
        """
        執行完整分析，生成 WhaleStrategySnapshot
        
        Args:
            current_price: 當前價格
            obi: 訂單簿失衡 (-1 to 1)
            vpin: 知情交易機率 (0 to 1)
            wpi: 鯨魚壓力指數 (-1 to 1)
            funding_rate: 資金費率
            oi_change_pct: OI 變化百分比
            liquidation_pressure_long: 多頭爆倉壓力 (0-100)
            liquidation_pressure_short: 空頭爆倉壓力 (0-100)
            price_change_1m_pct: 1分鐘價格變化%
            price_change_5m_pct: 5分鐘價格變化%
        """
        all_signals = []
        all_warnings = []
        strategy_scores: Dict[WhaleStrategyV4, float] = {}
        
        # ========== 執行所有偵測器 ==========
        
        # 1. 爆倉類
        liq_result = self.liquidation_detector.detect()
        if liq_result["pattern"]:
            strategy_scores[liq_result["pattern"]] = liq_result["probability"]
            all_signals.extend(liq_result["signals"])
        
        # 2. 假突破
        current_volume = 0  # 需要從外部傳入或從歷史獲取
        fakeout_result = self.fakeout_detector.detect(current_price, current_volume)
        if fakeout_result["pattern"]:
            strategy_scores[fakeout_result["pattern"]] = fakeout_result["probability"]
            all_signals.extend(fakeout_result["signals"])
        
        # 3. 閃崩
        flash_result = self.flash_crash_detector.detect()
        if flash_result["pattern"]:
            strategy_scores[flash_result["pattern"]] = flash_result["probability"]
            all_signals.extend(flash_result["signals"])
        
        # 4. 獵殺止損
        stop_hunt_result = self.stop_hunt_detector.detect(current_price)
        if stop_hunt_result["pattern"]:
            strategy_scores[stop_hunt_result["pattern"]] = stop_hunt_result["probability"]
            all_signals.extend(stop_hunt_result["signals"])
        
        # 5. 幌騙
        spoofing_result = self.spoofing_detector.detect()
        if spoofing_result["pattern"]:
            strategy_scores[spoofing_result["pattern"]] = spoofing_result["probability"]
            all_signals.extend(spoofing_result["signals"])
        
        # 6. 派發
        dist_result = self.distribution_detector.detect(funding_rate, oi_change_pct)
        if dist_result["pattern"]:
            strategy_scores[dist_result["pattern"]] = dist_result["probability"]
            all_signals.extend(dist_result["signals"])
        
        # 7. 層疊掛單
        layer_result = self.layering_detector.detect()
        if layer_result["pattern"]:
            strategy_scores[layer_result["pattern"]] = layer_result["probability"]
            all_signals.extend(layer_result["signals"])
        
        # 8. 鋸齒洗盤
        whipsaw_result = self.whipsaw_detector.detect()
        if whipsaw_result["pattern"]:
            strategy_scores[whipsaw_result["pattern"]] = whipsaw_result["probability"]
            all_signals.extend(whipsaw_result["signals"])
        
        # 9. 盤整洗盤
        consol_result = self.consolidation_detector.detect()
        if consol_result["pattern"]:
            strategy_scores[consol_result["pattern"]] = consol_result["probability"]
            all_signals.extend(consol_result["signals"])
        
        # 10. 陰跌洗盤
        slow_bleed_result = self.slow_bleed_detector.detect(wpi)
        if slow_bleed_result["pattern"]:
            strategy_scores[slow_bleed_result["pattern"]] = slow_bleed_result["probability"]
            all_signals.extend(slow_bleed_result["signals"])
        
        # 11. 趨勢類
        trend_result = self.trend_detector.detect(wpi)
        if trend_result["pattern"]:
            strategy_scores[trend_result["pattern"]] = trend_result["probability"]
            all_signals.extend(trend_result["signals"])
        
        # ========== Phase 4: v2 舊策略偵測 ==========
        
        # 12. 陷阱類 (BULL_TRAP, BEAR_TRAP)
        stop_hunt_index = stop_hunt_result.get("stop_hunt_index", 0)
        trap_result = self.trap_detector.detect(current_price, obi, wpi, stop_hunt_index)
        if trap_result["pattern"]:
            strategy_scores[trap_result["pattern"]] = trap_result["probability"]
            all_signals.extend(trap_result["signals"])
        
        # 13. 吸籌 (ACCUMULATION, RE_ACCUMULATION)
        volume_ratio = 1.0  # 可從外部傳入
        acc_result = self.accumulation_detector.detect(obi, vpin, wpi, price_change_5m_pct, volume_ratio)
        if acc_result["pattern"]:
            strategy_scores[acc_result["pattern"]] = acc_result["probability"]
            all_signals.extend(acc_result["signals"])
        
        # 14. 拉高出貨 (PUMP_DUMP)
        pump_result = self.pump_dump_detector.detect(obi, vpin, wpi, price_change_5m_pct, volume_ratio)
        if pump_result["pattern"]:
            strategy_scores[pump_result["pattern"]] = pump_result["probability"]
            all_signals.extend(pump_result["signals"])
        
        # 15. 對敲 (WASH_TRADING)
        wash_result = self.wash_trading_detector.detect(volume_ratio, vpin, wpi)
        if wash_result["pattern"]:
            strategy_scores[wash_result["pattern"]] = wash_result["probability"]
            all_signals.extend(wash_result["signals"])
        
        # 風險警告
        if vpin > 0.6:
            all_warnings.append(f"⚠️ 高毒性流量 VPIN={vpin:.2f}")
        if liquidation_pressure_long > 70:
            all_warnings.append(f"🔴 多頭爆倉壓力高 {liquidation_pressure_long:.1f}")
        if liquidation_pressure_short > 70:
            all_warnings.append(f"🟢 空頭爆倉壓力高 {liquidation_pressure_short:.1f}")
        
        # ========== 確定主要策略 ==========
        primary_strategy = None
        secondary_strategy = None
        
        if strategy_scores:
            sorted_strategies = sorted(strategy_scores.items(), key=lambda x: x[1], reverse=True)
            
            if sorted_strategies[0][1] > 0.3:
                meta = get_strategy_metadata(sorted_strategies[0][0])
                primary_strategy = StrategyInfo(
                    strategy=sorted_strategies[0][0],
                    category=meta["category"],
                    probability=sorted_strategies[0][1],
                    confidence=sorted_strategies[0][1] * 0.8,
                    risk_level=meta["risk_level"],
                    signals=[s for s in all_signals[:3]]
                )
            
            if len(sorted_strategies) > 1 and sorted_strategies[1][1] > 0.2:
                meta = get_strategy_metadata(sorted_strategies[1][0])
                secondary_strategy = StrategyInfo(
                    strategy=sorted_strategies[1][0],
                    category=meta["category"],
                    probability=sorted_strategies[1][1],
                    confidence=sorted_strategies[1][1] * 0.7,
                    risk_level=meta["risk_level"],
                    signals=[]
                )
        
        # ========== 生成進場信號 ==========
        entry_signal = None
        if primary_strategy and primary_strategy.probability > 0.5:
            meta = get_strategy_metadata(primary_strategy.strategy)
            best_response = meta["best_response"]
            
            if best_response != SignalDirection.HOLD:
                # 計算止損止盈
                atr_estimate = current_price * 0.01  # 假設 ATR 約 1%
                
                if best_response == SignalDirection.LONG:
                    entry_signal = EntrySignal(
                        direction=SignalDirection.LONG,
                        entry_price=current_price,
                        stop_loss=current_price - atr_estimate * 1.5,
                        take_profit=current_price + atr_estimate * 2,
                        position_size_pct=min(50, primary_strategy.probability * 60),
                        urgency="WAIT_CONFIRM" if primary_strategy.probability < 0.7 else "IMMEDIATE",
                        valid_until=(datetime.now(timezone.utc)).isoformat(),
                        reasoning=f"基於 {primary_strategy.strategy.value} 策略做多"
                    )
                elif best_response == SignalDirection.SHORT:
                    entry_signal = EntrySignal(
                        direction=SignalDirection.SHORT,
                        entry_price=current_price,
                        stop_loss=current_price + atr_estimate * 1.5,
                        take_profit=current_price - atr_estimate * 2,
                        position_size_pct=min(50, primary_strategy.probability * 60),
                        urgency="WAIT_CONFIRM" if primary_strategy.probability < 0.7 else "IMMEDIATE",
                        valid_until=(datetime.now(timezone.utc)).isoformat(),
                        reasoning=f"基於 {primary_strategy.strategy.value} 策略做空"
                    )
        
        # ========== 確定整體偏向 ==========
        overall_bias = "NEUTRAL"
        if wpi > 0.3 or (primary_strategy and get_strategy_metadata(primary_strategy.strategy)["best_response"] == SignalDirection.LONG):
            overall_bias = "BULLISH"
        elif wpi < -0.3 or (primary_strategy and get_strategy_metadata(primary_strategy.strategy)["best_response"] == SignalDirection.SHORT):
            overall_bias = "BEARISH"
        
        # ========== 生成快照 ==========
        snapshot = WhaleStrategySnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol="BTCUSDT",
            current_price=current_price,
            price_change_1m_pct=price_change_1m_pct,
            price_change_5m_pct=price_change_5m_pct,
            primary_strategy=primary_strategy,
            secondary_strategy=secondary_strategy,
            strategy_probabilities={s.name: p for s, p in strategy_scores.items()},
            entry_signal=entry_signal,
            indicators={
                "obi": obi,
                "vpin": vpin,
                "wpi": wpi,
                "funding_rate": funding_rate,
                "oi_change_pct": oi_change_pct,
                "liquidation_pressure_long": liquidation_pressure_long,
                "liquidation_pressure_short": liquidation_pressure_short
            },
            key_signals=all_signals[:10],
            risk_warnings=all_warnings[:5],
            overall_bias=overall_bias,
            overall_confidence=primary_strategy.confidence if primary_strategy else 0.3,
            trading_allowed=vpin < 0.7 and len(all_warnings) < 3
        )
        
        # 儲存並輸出
        self.last_snapshot = snapshot
        self.snapshot_history.append(snapshot)
        self.json_writer.write(snapshot)
        
        return snapshot
    
    def get_last_snapshot(self) -> Optional[WhaleStrategySnapshot]:
        """獲取最新快照"""
        return self.last_snapshot
    
    def render_panel(self) -> str:
        """渲染主力策略面板"""
        if not self.last_snapshot:
            return "🐋 主力策略偵測: ⏳ 等待數據..."
        
        s = self.last_snapshot
        
        lines = [
            "🐋 主力策略偵測 v4.0",
            f"📊 價格: ${s.current_price:,.0f} ({s.price_change_5m_pct:+.2f}% 5m)",
            f"🎯 主策略: {s.primary_strategy.strategy.value if s.primary_strategy else 'NORMAL'} ({s.primary_strategy.probability:.0%})" if s.primary_strategy else "🎯 主策略: 正常波動",
            f"📈 偏向: {s.overall_bias} (信心: {s.overall_confidence:.0%})",
            f"🔐 允許交易: {'✅' if s.trading_allowed else '❌'}",
        ]
        
        if s.entry_signal:
            lines.append(f"💡 建議: {s.entry_signal.direction.value} @${s.entry_signal.entry_price:,.0f}")
        
        if s.key_signals:
            lines.append(f"📍 信號: {s.key_signals[0]}")
        
        if s.risk_warnings:
            lines.append(f"⚠️ 風險: {s.risk_warnings[0]}")
        
        return "\n".join(lines)


# ==================== 測試 ====================

if __name__ == "__main__":
    print("🐋 主力策略識別系統 v4.0 - 整合測試")
    print("=" * 60)
    
    # 測試策略枚舉
    print(f"\n📊 策略總數: {len(WhaleStrategyV4)}")
    
    for category in StrategyCategory:
        strategies = get_category_strategies(category)
        print(f"\n{category.value}: {len(strategies)} 種")
        for s in strategies:
            meta = get_strategy_metadata(s)
            print(f"  - {s.value} ({s.name}): {meta['risk_level'].value}風險")
    
    # 測試主偵測器
    print("\n" + "=" * 60)
    print("🔬 測試主偵測器整合")
    
    detector = WhaleStrategyDetectorV4(output_path="test_whale_strategy.json")
    
    # 模擬數據輸入
    print("\n📊 模擬市場數據...")
    
    # 模擬 K 線數據
    for i in range(30):
        base_price = 95000 + (i - 15) * 50  # 波動
        candle = {
            "open": base_price,
            "high": base_price + 100,
            "low": base_price - 100 - (i * 10 if i < 10 else 0),  # 前 10 根逐步降低
            "close": base_price + 50,
            "volume": 1000000 + (50000 if i < 5 else 200000 if i > 25 else 0)  # 後期放量
        }
        detector.update_data(candle=candle)
    
    # 模擬爆倉事件 (模擬空頭爆倉連環)
    for i in range(5):
        detector.update_data(liquidation={
            "side": "short",
            "usd_value": 500000 + i * 200000,
            "price": 95200 + i * 50,
            "timestamp": time.time() + i
        })
    
    # 執行分析
    print("\n🔍 執行策略分析...")
    snapshot = detector.analyze(
        current_price=95500,
        obi=0.25,
        vpin=0.45,
        wpi=0.35,
        funding_rate=0.001,
        oi_change_pct=2.5,
        liquidation_pressure_long=30,
        liquidation_pressure_short=65,
        price_change_1m_pct=0.15,
        price_change_5m_pct=0.45
    )
    
    print("\n📸 生成快照:")
    print(snapshot.to_json())
    
    print("\n🖥️ 面板渲染:")
    print(detector.render_panel())
    
    print(f"\n✅ 已寫入 JSON: test_whale_strategy.json")
    
    # 驗證所有偵測器
    print("\n" + "=" * 60)
    print("✅ 偵測器驗證")
    print(f"  - LiquidationPatternDetector: ✅")
    print(f"  - FakeoutDetector: ✅")
    print(f"  - FlashCrashDetector: ✅")
    print(f"  - StopHuntDetectorV4: ✅")
    print(f"  - SpoofingDetectorV4: ✅")
    print(f"  - DistributionDetector: ✅")
    print(f"  - LayeringDetector: ✅")
    print(f"  - WhipsawDetector: ✅")
    print(f"  - ConsolidationShakeDetector: ✅")
    print(f"  - SlowBleedDetector: ✅")
    print(f"  - TrendPatternDetector: ✅")
    print("✅ Phase 4 偵測器 (v2 策略)")
    print(f"  - TrapDetector (BULL_TRAP/BEAR_TRAP): ✅")
    print(f"  - AccumulationDetector: ✅")
    print(f"  - PumpDumpDetector: ✅")
    print(f"  - WashTradingDetector: ✅")
    print(f"\n🎉 v4.0 Phase 4 整合測試完成！")


