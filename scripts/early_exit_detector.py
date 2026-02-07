#!/usr/bin/env python3
"""
早期逃命偵測器 v1.1
==================
🎯 目標：偵測「微利後反轉止損」的早期信號，提前止損

📊 基於 3209 筆交易 + 69296 筆信號數據分析：
- 20% 交易屬於「微利後反轉」（最高利潤 > 0.3% 但最終虧損）
- 這些交易的特徵：最高利潤平均 0.64%，反轉後虧損 -0.70%
- 正常獲利交易：最高利潤平均 2.13%，能鎖住 63% 的最高利潤

🚨 早期逃命信號：
1. 獲利回撤率 > 50%（曾經賺 1%，回撤到 0.5% 以下）
2. 微利時間過長（利潤 < 1% 持續超過 15 秒）
3. OBI 方向反轉（OBI 從正轉負或從負轉正）
4. 動能衰退（連續 5 秒利潤下降）
5. 六維對向分數增加 ≥2（41% 反轉 vs 25% 正常）⭐ NEW
6. 六維分數差收窄 ≥1.5（對方力量變強）⭐ NEW

v1.1 更新:
- 加入六維分數監控 (opposite_score_increase, score_gap_narrowing)
- 基於信號分析的關鍵發現
"""

import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from datetime import datetime
from enum import Enum


class EarlyExitReason(Enum):
    """早期逃命原因"""
    NONE = "none"
    PROFIT_DRAWDOWN = "profit_drawdown"      # 獲利回撤過大
    STALL_TOO_LONG = "stall_too_long"        # 微利停滯太久
    OBI_REVERSAL = "obi_reversal"            # OBI 反轉
    MOMENTUM_DECAY = "momentum_decay"         # 動能衰退
    QUICK_REVERSAL = "quick_reversal"         # 快速反轉
    OPPOSITE_SCORE_SURGE = "opposite_score_surge"  # 六維對向分數激增 ⭐ NEW
    SCORE_GAP_NARROWING = "score_gap_narrowing"    # 六維分數差收窄 ⭐ NEW
    COMBINED = "combined"                     # 多重信號


@dataclass
class EarlyExitConfig:
    """早期逃命配置"""
    enabled: bool = True
    
    # 獲利回撤閾值
    profit_drawdown_threshold: float = 0.50   # 回撤 50% 觸發
    min_profit_for_drawdown_check: float = 0.3  # 至少要有 0.3% 利潤才檢查回撤
    
    # 微利停滯閾值
    stall_profit_threshold: float = 0.8        # 利潤 < 0.8% 視為「微利」
    stall_time_threshold_sec: float = 15.0     # 微利持續 15 秒觸發
    
    # OBI 反轉閾值
    obi_reversal_enabled: bool = True
    obi_reversal_threshold: float = 0.15       # OBI 變化 > 0.15 觸發
    
    # 動能衰退
    momentum_decay_enabled: bool = True
    momentum_decay_samples: int = 5            # 連續 5 個樣本利潤下降
    momentum_decay_min_drop: float = 0.1       # 每次至少下降 0.1%
    
    # 快速反轉
    quick_reversal_enabled: bool = True
    quick_reversal_time_sec: float = 5.0       # 5 秒內
    quick_reversal_drop_pct: float = 0.5       # 從最高利潤下跌 0.5%
    
    # 六維分數監控 ⭐ NEW (基於信號分析)
    six_dim_enabled: bool = True
    opposite_score_increase_threshold: float = 2.0   # 對向分數增加 ≥2 觸發 (41% vs 25%)
    score_gap_narrowing_threshold: float = 1.5       # 分數差收窄 ≥1.5 觸發
    
    # 安全緩衝
    grace_period_sec: float = 3.0              # 進場後 3 秒不檢查
    min_profit_to_protect: float = 0.2         # 至少要有 0.2% 利潤才觸發保護


@dataclass
class ProfitTracker:
    """利潤追蹤器"""
    entry_time: float
    entry_price: float
    direction: str  # "LONG" or "SHORT"
    entry_obi: float = 0.0
    
    # 六維分數初始值 ⭐ NEW
    entry_long_score: float = 0.0
    entry_short_score: float = 0.0
    
    # 追蹤數據
    max_profit_pct: float = 0.0
    max_profit_time: float = 0.0
    current_profit_pct: float = 0.0
    profit_history: List[Tuple[float, float]] = field(default_factory=list)  # (timestamp, profit)
    obi_history: List[Tuple[float, float]] = field(default_factory=list)     # (timestamp, obi)
    
    # 六維分數歷史 ⭐ NEW
    six_dim_history: List[Tuple[float, float, float]] = field(default_factory=list)  # (timestamp, long_score, short_score)
    
    # 微利停滯追蹤
    stall_start_time: Optional[float] = None
    
    def update(self, current_price: float, current_obi: float = 0.0, 
               long_score: float = None, short_score: float = None):
        """更新追蹤數據 (支援六維分數)"""
        now = time.time()
        
        # 計算當前利潤
        if self.direction == "LONG":
            self.current_profit_pct = (current_price - self.entry_price) / self.entry_price * 100
        else:
            self.current_profit_pct = (self.entry_price - current_price) / self.entry_price * 100
        
        # 更新最高利潤
        if self.current_profit_pct > self.max_profit_pct:
            self.max_profit_pct = self.current_profit_pct
            self.max_profit_time = now
        
        # 記錄歷史
        self.profit_history.append((now, self.current_profit_pct))
        self.obi_history.append((now, current_obi))
        
        # 記錄六維分數歷史 ⭐ NEW
        if long_score is not None and short_score is not None:
            self.six_dim_history.append((now, long_score, short_score))
            if len(self.six_dim_history) > 100:
                self.six_dim_history = self.six_dim_history[-50:]
        
        # 保持歷史在合理範圍
        if len(self.profit_history) > 100:
            self.profit_history = self.profit_history[-50:]
        if len(self.obi_history) > 100:
            self.obi_history = self.obi_history[-50:]


class EarlyExitDetector:
    """早期逃命偵測器"""
    
    def __init__(self, config: EarlyExitConfig = None):
        self.config = config or EarlyExitConfig()
        self.tracker: Optional[ProfitTracker] = None
        
    def start_tracking(self, entry_price: float, direction: str, entry_obi: float = 0.0,
                       long_score: float = 0.0, short_score: float = 0.0):
        """開始追蹤新交易 (支援六維分數初始值)"""
        self.tracker = ProfitTracker(
            entry_time=time.time(),
            entry_price=entry_price,
            direction=direction,
            entry_obi=entry_obi,
            entry_long_score=long_score,
            entry_short_score=short_score
        )
        
    def stop_tracking(self):
        """停止追蹤"""
        self.tracker = None
        
    def update_and_check(self, current_price: float, current_obi: float = 0.0,
                         long_score: float = None, short_score: float = None) -> Tuple[bool, EarlyExitReason, str]:
        """
        更新數據並檢查是否應該早期逃命 (支援六維分數監控)
        
        Returns:
            (should_exit, reason, message)
        """
        if not self.config.enabled or not self.tracker:
            return False, EarlyExitReason.NONE, ""
        
        # 更新追蹤數據 (包含六維分數)
        self.tracker.update(current_price, current_obi, long_score, short_score)
        
        now = time.time()
        hold_time = now - self.tracker.entry_time
        
        # 安全緩衝期
        if hold_time < self.config.grace_period_sec:
            return False, EarlyExitReason.NONE, ""
        
        # 至少要有一點利潤才觸發保護
        if self.tracker.max_profit_pct < self.config.min_profit_to_protect:
            return False, EarlyExitReason.NONE, ""
        
        reasons = []
        messages = []
        
        # 1️⃣ 獲利回撤檢查
        if self.tracker.max_profit_pct >= self.config.min_profit_for_drawdown_check:
            if self.tracker.max_profit_pct > 0:
                drawdown_ratio = 1 - (self.tracker.current_profit_pct / self.tracker.max_profit_pct)
                if drawdown_ratio >= self.config.profit_drawdown_threshold:
                    reasons.append(EarlyExitReason.PROFIT_DRAWDOWN)
                    messages.append(f"獲利回撤 {drawdown_ratio*100:.0f}% (最高 {self.tracker.max_profit_pct:.2f}% → 當前 {self.tracker.current_profit_pct:.2f}%)")
        
        # 2️⃣ 微利停滯檢查
        if self.tracker.current_profit_pct < self.config.stall_profit_threshold and self.tracker.current_profit_pct > 0:
            if self.tracker.stall_start_time is None:
                self.tracker.stall_start_time = now
            elif now - self.tracker.stall_start_time >= self.config.stall_time_threshold_sec:
                reasons.append(EarlyExitReason.STALL_TOO_LONG)
                messages.append(f"微利停滯 {now - self.tracker.stall_start_time:.0f}秒 (利潤 {self.tracker.current_profit_pct:.2f}%)")
        else:
            self.tracker.stall_start_time = None
        
        # 3️⃣ OBI 反轉檢查
        if self.config.obi_reversal_enabled and len(self.tracker.obi_history) >= 2:
            obi_change = current_obi - self.tracker.entry_obi
            
            # 檢查是否反轉
            if self.tracker.direction == "LONG":
                # LONG 時，OBI 從正轉負是危險信號
                if self.tracker.entry_obi > 0.1 and current_obi < -0.05:
                    reasons.append(EarlyExitReason.OBI_REVERSAL)
                    messages.append(f"OBI 反轉 {self.tracker.entry_obi:.2f} → {current_obi:.2f}")
            else:
                # SHORT 時，OBI 從負轉正是危險信號
                if self.tracker.entry_obi < -0.1 and current_obi > 0.05:
                    reasons.append(EarlyExitReason.OBI_REVERSAL)
                    messages.append(f"OBI 反轉 {self.tracker.entry_obi:.2f} → {current_obi:.2f}")
        
        # 4️⃣ 動能衰退檢查
        if self.config.momentum_decay_enabled:
            history = self.tracker.profit_history
            if len(history) >= self.config.momentum_decay_samples:
                recent = history[-self.config.momentum_decay_samples:]
                is_decaying = True
                for i in range(1, len(recent)):
                    if recent[i][1] >= recent[i-1][1]:
                        is_decaying = False
                        break
                    if recent[i-1][1] - recent[i][1] < self.config.momentum_decay_min_drop:
                        is_decaying = False
                        break
                
                if is_decaying:
                    total_drop = recent[0][1] - recent[-1][1]
                    reasons.append(EarlyExitReason.MOMENTUM_DECAY)
                    messages.append(f"動能衰退 連續 {self.config.momentum_decay_samples} 次下跌 (共 {total_drop:.2f}%)")
        
        # 5️⃣ 快速反轉檢查
        if self.config.quick_reversal_enabled:
            time_since_max = now - self.tracker.max_profit_time
            if time_since_max <= self.config.quick_reversal_time_sec:
                drop_from_max = self.tracker.max_profit_pct - self.tracker.current_profit_pct
                if drop_from_max >= self.config.quick_reversal_drop_pct:
                    reasons.append(EarlyExitReason.QUICK_REVERSAL)
                    messages.append(f"快速反轉 {time_since_max:.1f}秒內跌 {drop_from_max:.2f}%")
        
        # 6️⃣ 六維對向分數激增檢查 ⭐ NEW
        # 數據分析: 對向分數增加 ≥2 的情況，微利反轉 41% vs 正常 25%
        if self.config.six_dim_enabled and long_score is not None and short_score is not None:
            if self.tracker.direction == "LONG":
                # LONG 時監控 short_score 增加
                opposite_entry = self.tracker.entry_short_score
                opposite_current = short_score
                target_entry = self.tracker.entry_long_score
                target_current = long_score
            else:
                # SHORT 時監控 long_score 增加
                opposite_entry = self.tracker.entry_long_score
                opposite_current = long_score
                target_entry = self.tracker.entry_short_score
                target_current = short_score
            
            # 對向分數增加檢查
            opposite_increase = opposite_current - opposite_entry
            if opposite_increase >= self.config.opposite_score_increase_threshold:
                reasons.append(EarlyExitReason.OPPOSITE_SCORE_SURGE)
                messages.append(f"⚠️ 對向分數激增 +{opposite_increase:.1f} ({opposite_entry:.1f}→{opposite_current:.1f})")
            
            # 分數差收窄檢查
            entry_gap = target_entry - opposite_entry
            current_gap = target_current - opposite_current
            gap_narrowing = entry_gap - current_gap
            if gap_narrowing >= self.config.score_gap_narrowing_threshold:
                reasons.append(EarlyExitReason.SCORE_GAP_NARROWING)
                messages.append(f"⚠️ 分數差收窄 -{gap_narrowing:.1f} (差距 {entry_gap:.1f}→{current_gap:.1f})")
        
        # 綜合判斷
        if len(reasons) >= 2:
            return True, EarlyExitReason.COMBINED, " | ".join(messages)
        elif len(reasons) == 1:
            return True, reasons[0], messages[0]
        
        return False, EarlyExitReason.NONE, ""
    
    def get_status(self) -> Dict:
        """獲取當前追蹤狀態"""
        if not self.tracker:
            return {"tracking": False}
        
        status = {
            "tracking": True,
            "direction": self.tracker.direction,
            "entry_price": self.tracker.entry_price,
            "current_profit_pct": self.tracker.current_profit_pct,
            "max_profit_pct": self.tracker.max_profit_pct,
            "hold_time_sec": time.time() - self.tracker.entry_time,
            "samples": len(self.tracker.profit_history)
        }
        
        # 加入六維分數狀態 ⭐ NEW
        if self.tracker.six_dim_history:
            latest = self.tracker.six_dim_history[-1]
            status["current_long_score"] = latest[1]
            status["current_short_score"] = latest[2]
            status["entry_long_score"] = self.tracker.entry_long_score
            status["entry_short_score"] = self.tracker.entry_short_score
        
        return status


def create_early_exit_detector(
    profit_drawdown_threshold: float = 0.50,
    stall_time_threshold_sec: float = 15.0,
    quick_reversal_drop_pct: float = 0.5,
    six_dim_enabled: bool = True,
    opposite_score_increase_threshold: float = 2.0,
    score_gap_narrowing_threshold: float = 1.5,
    **kwargs
) -> EarlyExitDetector:
    """工廠函數：創建早期逃命偵測器 (支援六維分數配置)"""
    config = EarlyExitConfig(
        profit_drawdown_threshold=profit_drawdown_threshold,
        stall_time_threshold_sec=stall_time_threshold_sec,
        quick_reversal_drop_pct=quick_reversal_drop_pct,
        six_dim_enabled=six_dim_enabled,
        opposite_score_increase_threshold=opposite_score_increase_threshold,
        score_gap_narrowing_threshold=score_gap_narrowing_threshold,
        **{k: v for k, v in kwargs.items() if hasattr(EarlyExitConfig, k)}
    )
    return EarlyExitDetector(config)


# ============================================================
# 測試
# ============================================================
if __name__ == "__main__":
    print("🧪 測試早期逃命偵測器 v1.1 (含六維分數監控)")
    print("=" * 60)
    
    detector = create_early_exit_detector()
    
    # 模擬一個「微利後反轉」的交易 (含六維分數變化)
    entry_price = 100000.0
    entry_long_score = 8.0
    entry_short_score = 4.0
    
    detector.start_tracking(
        entry_price, "LONG", 
        entry_obi=0.2,
        long_score=entry_long_score,
        short_score=entry_short_score
    )
    
    # 模擬價格和六維分數變動 (微利後反轉情境)
    scenarios = [
        # (價格, OBI, long_score, short_score)
        (100050, 0.18, 8.0, 4.0),   # +0.05%
        (100100, 0.15, 7.5, 4.5),   # +0.10%
        (100200, 0.12, 7.0, 5.0),   # +0.20%
        (100350, 0.08, 6.5, 5.5),   # +0.35% (最高點)
        (100300, 0.05, 6.0, 6.0),   # +0.30% (對方開始追上)
        (100200, 0.02, 5.5, 6.5),   # +0.20% (對向分數增加 +2.5!) ⚠️
        (100100, -0.05, 5.0, 7.0),  # +0.10%
        (100000, -0.10, 4.5, 7.5),  # +0.00%
        (99900, -0.15, 4.0, 8.0),   # -0.10%
    ]
    
    print("\n📊 模擬交易過程:")
    print("-" * 60)
    
    for i, (price, obi, l_score, s_score) in enumerate(scenarios):
        time.sleep(0.5)
        should_exit, reason, msg = detector.update_and_check(
            price, current_obi=obi, long_score=l_score, short_score=s_score
        )
        status = detector.get_status()
        print(f"[{i+1}] 價格 {price:,.0f} | 利潤 {status['current_profit_pct']:+.2f}% | 最高 {status['max_profit_pct']:.2f}% | L:{l_score} S:{s_score}")
        if should_exit:
            print(f"    🚨 早期逃命信號: {reason.value}")
            print(f"    📝 {msg}")
            break
    
    print("\n" + "=" * 60)
    print("✅ 測試完成")
