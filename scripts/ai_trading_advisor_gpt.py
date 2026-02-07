#!/usr/bin/env python3
"""
AI Trading Advisor - 分析當前交易狀態並提供獲利建議
讀取：
1. 最新的 paper trading 數據
2. 市場快照（爆倉壓力、OI）
3. 當前持倉狀態

輸出：
- 哪些策略表現好/差
- 當前市場機會在哪裡
- 建議調整哪些參數

使用方式：
  python scripts/ai_trading_advisor_gpt.py [hours]
  例如: python scripts/ai_trading_advisor_gpt.py 8  # 運行 8 小時後自動停止
"""

import uuid
import json
import os
import sys
import time
import argparse
import pandas as pd
import io
import threading
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# 🆕 終端機輸出日誌記錄器
# ═══════════════════════════════════════════════════════════════════════════════

class TeeLogger:
    """
    同時輸出到終端機和日誌檔案的記錄器
    每 30 秒自動 flush 到檔案，避免效能問題
    """
    def __init__(self, log_dir="logs/ai_terminal", flush_interval=30):
        self.terminal = sys.stdout
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 建立日誌檔案 (按日期命名)
        self.log_file_path = self.log_dir / f"ai_advisor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self.log_file = open(self.log_file_path, 'w', encoding='utf-8')
        
        # 緩衝區
        self.buffer = []
        self.buffer_lock = threading.Lock()
        self.flush_interval = flush_interval
        self.last_flush_time = time.time()
        
        print(f"📝 終端機輸出將記錄到: {self.log_file_path}")
    
    def write(self, message):
        self.terminal.write(message)
        
        # 添加到緩衝區
        with self.buffer_lock:
            if message.strip():  # 只記錄非空內容
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.buffer.append(f"[{timestamp}] {message}")
        
        # 檢查是否需要 flush
        if time.time() - self.last_flush_time >= self.flush_interval:
            self.flush()
    
    def flush(self):
        self.terminal.flush()
        
        # 寫入緩衝區內容到檔案
        with self.buffer_lock:
            if self.buffer:
                try:
                    for line in self.buffer:
                        self.log_file.write(line)
                        if not line.endswith('\n'):
                            self.log_file.write('\n')
                    self.log_file.flush()
                    self.buffer.clear()
                except Exception as e:
                    self.terminal.write(f"⚠️ 日誌寫入失敗: {e}\n")
        
        self.last_flush_time = time.time()
    
    def close(self):
        self.flush()
        self.log_file.close()
        print(f"\n📝 日誌已儲存到: {self.log_file_path}")

# 全域日誌記錄器
_tee_logger = None

def setup_terminal_logging():
    """啟用終端機輸出日誌記錄"""
    global _tee_logger
    if _tee_logger is None:
        _tee_logger = TeeLogger()
        sys.stdout = _tee_logger
    return _tee_logger

def close_terminal_logging():
    """關閉終端機輸出日誌記錄"""
    global _tee_logger
    if _tee_logger is not None:
        sys.stdout = _tee_logger.terminal
        _tee_logger.close()
        _tee_logger = None

# 狀態檔案，用於記錄 AI 的長期預測
STATE_FILE = "ai_advisor_state.json"
# 策略記憶檔案，用於記錄 AI 的多階段計畫
PLAN_FILE = "ai_strategy_plan.json"
# 學習記憶檔案，用於記錄成功與失敗的經驗
MEMORY_FILE = "ai_learning_memory.json"
# 市場記憶檔案，用於記錄動態市場體制與長期偏見
MARKET_MEMORY_FILE = "ai_market_memory.json"
# 團隊配置檔案，用於動態調整 AI 參數
TEAM_CONFIG_FILE = "config/ai_team_config.json"
# 🆕 AI-Wolf 雙向溝通橋接檔案
BRIDGE_FILE = "ai_wolf_bridge.json"

# 🎯 高精準狙擊策略配置 
# 🔧 v3.1: 「建議範圍」而非「強制目標」
# 重要：交易次數應該是「結果」而不是「目標」
# 市場沒訊號就不做，有訊號才進場，避免被主力收割
SNIPER_CONFIG = {
    "enabled": True,
    
    # === 交易頻率「建議範圍」(不是強制目標!) ===
    "suggested_trades_per_8h": "7-15",  # 🔧 僅供參考，實際依市場
    "min_interval_minutes": 25,       # 最少間隔 25 分鐘 (避免過度交易)
    "max_wait_for_signal": "unlimited", # 🔧 沒訊號就無限等待
    
    # === 槓桿設定 ===
    "base_leverage": 60,              # 基礎槓桿 60x
    "max_leverage": 80,               # 最大槓桿 80x
    "min_leverage": 50,               # 最小槓桿 50x
    
    # === 進場門檻 (必須全部滿足才進場) ===
    "entry_requirements": {
        "min_confidence": 72,         # 🔧 提高到 72% (原 70，提升品質)
        "min_confluence": 3,          # 🔧 提高到 3 個指標一致 (原 2)
        "max_vpin": 0.70,             # 🔧 收緊到 0.70 (原 0.75)
        "min_whale_dominance": 0.55,  # 🔧 提高到 55% (原 50%)
        "whale_alignment": True,      # 必須與鯨魚方向一致
        "require_funding_alignment": False,
    },
    
    # === 風險過濾 (任一觸發就不進場) ===
    "risk_filters": {
        "max_spread_bps": 6,          # 🔧 收緊到 6bps (原 8)
        "avoid_high_volatility": True,
        "avoid_liquidation_cascade": True,
        "avoid_whale_trap": True,
        "whale_trap_threshold": 0.55, # 🔧 收緊陷阱閾值到 55% (原 60%)
        "avoid_funding_extreme": False,
    },
    
    # === 止盈止損 (ROI%) - 🎯 目標 +5~10% ===
    "targets": {
        "take_profit_pct": 8.0,       # 🔧 目標止盈 8% ROI (原 10%，更容易達成)
        "stop_loss_pct": 2.5,         # 🔧 止損 2.5% ROI (原 3.5%，盈虧比 3.2:1)
        "trailing_activation": 5.0,   # 🔧 5% 後啟動追蹤止損 (原 7%)
        "trailing_distance": 2.0,     # 🔧 從高點回撤 2% 就平倉 (原 2.5%)
    },
    
    # === 時間控制 ===
    "timing": {
        "min_holding_seconds": 90,    # 🔧 最少持倉 90 秒 (原 60 秒)
        "max_holding_minutes": 45,    # 🔧 最多持倉 45 分鐘 (原 30，讓利潤有時間發展)
        "cooldown_after_trade": 180,  # 🔧 交易後冷卻 3 分鐘 (原 2 分鐘)
    },
}

# 🆕 翻轉冷卻配置 (避免頻繁翻倉浪費手續費)
# 🔧 v3.0: 配合 8 小時 12 次交易目標
FLIP_COOLDOWN_CONFIG = {
    "enabled": True,
    "cooldown_seconds": 120,           # 🔧 延長到 120 秒 (原 60 秒，減少翻倉)
    "min_profit_to_flip": 4.0,         # 🔧 降到 4% 才允許翻轉 (原 5%，更容易達成)
    "max_loss_force_flip": -2.0,       # 🔧 虧損 2% 強制止損 (原 3%，更快止損)
    "confidence_override": 78,         # 🔧 提高到 78% (原 80%)
    "whale_flip_override": True,       # 鯨魚方向翻轉時可以無視冷卻
}

# 🆕 決策穩定性機制 (防止 AI 反覆無常)
# 🔧 v3.0: 優化為 5 秒判斷週期
DECISION_STABILITY = {
    "enabled": True,
    "required_confirmations": 2,       # 🔧 連續 2 次同方向判斷才執行 (原 3 次，配合 5 秒間隔)
    "confirmation_window_seconds": 15, # 🔧 15 秒內的判斷才算數 (原 30 秒，= 3 次判斷機會)
    "pending_decisions": {},           # 內存：追蹤待確認的決策
}


def calculate_dynamic_leverage(confidence: int, whale_dominance: float, vpin: float) -> int:
    """
    🎯 根據市場條件動態計算槓桿
    
    高信心 + 高鯨魚主導 + 低毒性 = 高槓桿
    """
    base = SNIPER_CONFIG["base_leverage"]
    max_lev = SNIPER_CONFIG["max_leverage"]
    min_lev = SNIPER_CONFIG["min_leverage"]
    
    # 信心度加成 (75-100 → 0-25 分)
    confidence_bonus = max(0, (confidence - 75)) 
    
    # 鯨魚主導度加成 (0.6-1.0 → 0-20 分)
    whale_bonus = max(0, (whale_dominance - 0.6) * 50)
    
    # VPIN 懲罰 (0.5-0.7 → 0-10 分扣除)
    vpin_penalty = max(0, (vpin - 0.5) * 50)
    
    # 計算最終槓桿
    leverage = base + confidence_bonus + whale_bonus - vpin_penalty
    leverage = max(min_lev, min(max_lev, int(leverage)))
    
    return leverage


def check_sniper_entry_conditions(bridge: dict, confidence: int) -> tuple:
    """
    🎯 檢查是否滿足高精準狙擊進場條件
    
    Returns:
        (can_enter: bool, reason: str, recommended_leverage: int)
    """
    if not SNIPER_CONFIG.get("enabled", True):
        return (True, "sniper_disabled", SNIPER_CONFIG["base_leverage"])
    
    wolf_to_ai = bridge.get("wolf_to_ai", {})
    reqs = SNIPER_CONFIG["entry_requirements"]
    filters = SNIPER_CONFIG["risk_filters"]
    
    # 提取市場數據
    whale_status = wolf_to_ai.get("whale_status", {})
    whale_dominance = whale_status.get("dominance", 0)
    whale_direction = whale_status.get("current_direction")
    
    micro = wolf_to_ai.get("market_microstructure", {})
    obi = micro.get("obi", 0)
    vpin = micro.get("vpin", 0)
    spread_bps = micro.get("spread_bps", 0)
    funding_rate = micro.get("funding_rate", 0)
    
    volatility = wolf_to_ai.get("volatility", {})
    atr_pct = volatility.get("atr_pct", 0)
    
    risk = wolf_to_ai.get("risk_indicators", {})
    liquidation_pressure = risk.get("liquidation_pressure", 0)
    whale_trap_prob = risk.get("whale_trap_probability", 0)
    cascade_risk = risk.get("cascade_risk", "LOW")
    
    # 🆕 提取鯨魚訊號品質分析
    whale_effectiveness = wolf_to_ai.get("whale_signal_effectiveness", {})
    quality_score = whale_effectiveness.get("quality_score", 0)
    quality_grade = whale_effectiveness.get("signal_quality", {}).get("grade", "D")
    quality_factors = whale_effectiveness.get("quality_factors", [])
    warning_factors = whale_effectiveness.get("warning_factors", [])
    whale_recommendation = whale_effectiveness.get("recommendation", "WAIT")
    whale_signal_strength = whale_effectiveness.get("signal_strength", "NONE")
    
    rejections = []
    bonuses = []
    
    # === 進場門檻檢查 ===
    if confidence < reqs["min_confidence"]:
        rejections.append(f"低信心 ({confidence}% < {reqs['min_confidence']}%)")
    
    if whale_dominance < reqs["min_whale_dominance"]:
        rejections.append(f"鯨魚主導度低 ({whale_dominance:.2f} < {reqs['min_whale_dominance']})")
    
    # 🔧 v2.1 VPIN 邏輯調整：
    # 高 VPIN 表示有「知情交易者」，如果我們跟鯨魚方向一致，高 VPIN 反而是優勢！
    # 只有當鯨魚主導度低或方向不明時，高 VPIN 才是風險
    # 原版 +47.66% 時期沒有 VPIN 阻擋！
    if vpin > reqs["max_vpin"]:
        # 🔧 v2.1: 降低門檻到 70% (原本是 80%)，讓更多符合條件的訊號通過
        if whale_dominance >= 0.70 and whale_direction in ['LONG', 'SHORT']:
            if vpin > 0.95:
                rejections.append(f"VPIN 極端毒性 ({vpin:.2f} > 0.95)")
            else:
                bonuses.append(f"高 VPIN + 鯨魚主導 (跟隨知情者)")
        else:
            rejections.append(f"VPIN 毒性高 ({vpin:.2f} > {reqs['max_vpin']})")
    
    # 🆕 鯨魚訊號品質檢查 (核心改進！)
    # 不再只看歷史有效率，而是看當前訊號的特徵品質
    if quality_score > 0:  # 有品質評分
        if quality_score < 30:
            rejections.append(f"鯨魚訊號品質差 (Q={quality_score}, Grade={quality_grade})")
        elif quality_score < 50 and whale_signal_strength != "STRONG":
            rejections.append(f"鯨魚訊號品質低 (Q={quality_score}, 需更多確認)")
        elif quality_score >= 70:
            bonuses.append(f"鯨魚訊號品質優 (Q={quality_score}, Grade={quality_grade})")
    
    # 🆕 檢查警告因素
    critical_warnings = [w for w in warning_factors if "⚠️" in w or "矛盾" in w]
    if len(critical_warnings) >= 2:
        rejections.append(f"多個警告信號: {', '.join(critical_warnings[:2])}")
    
    # 🆕 檢查價格反應（如果有當前訊號）
    current_signal = whale_effectiveness.get("current_signal", {})
    if current_signal:
        elapsed = current_signal.get("elapsed_seconds", 0)
        impact = current_signal.get("impact_in_direction", 0)
        
        # 訊號發出超過 45 秒但價格影響 < 0.02%，很可能是假訊號
        if elapsed > 45 and impact < 0.02:
            rejections.append(f"鯨魚訊號無價格反應 ({elapsed:.0f}s, 影響={impact:.3f}%)")
        elif elapsed > 15 and impact >= 0.05:
            bonuses.append(f"價格快速反應 ({elapsed:.0f}s, +{impact:.2f}%)")
    
    # === 風險過濾 ===
    if filters["avoid_high_volatility"] and atr_pct > 0.5:
        rejections.append(f"極端波動 (ATR: {atr_pct:.4f}%)")
    
    if filters["avoid_liquidation_cascade"] and cascade_risk in ["HIGH", "EXTREME"]:
        rejections.append(f"清算連鎖風險 ({cascade_risk})")
    
    if filters["avoid_whale_trap"] and whale_trap_prob > 0.5:
        rejections.append(f"鯨魚陷阱風險 ({whale_trap_prob:.0%})")
    
    if filters["max_spread_bps"] and spread_bps > filters["max_spread_bps"]:
        rejections.append(f"點差過大 ({spread_bps:.1f}bps)")
    
    if filters["avoid_funding_extreme"] and abs(funding_rate) > 0.001:
        rejections.append(f"Funding 極端 ({funding_rate:.4f})")
    
    # 計算動態槓桿
    leverage = calculate_dynamic_leverage(confidence, whale_dominance, vpin)
    
    # 🆕 根據品質分數調整槓桿
    if quality_score >= 80:
        leverage = min(leverage + 15, SNIPER_CONFIG["max_leverage"])  # A 級 +15x
    elif quality_score >= 70:
        leverage = min(leverage + 10, SNIPER_CONFIG["max_leverage"])  # B+ 級 +10x
    elif quality_score < 40:
        leverage = max(leverage - 10, SNIPER_CONFIG["min_leverage"])  # 低品質 -10x
    
    if rejections:
        return (False, " | ".join(rejections), leverage)
    
    # 構建通過原因
    pass_reasons = ["all_conditions_met"]
    if bonuses:
        pass_reasons.extend(bonuses)
    
    return (True, " | ".join(pass_reasons), leverage)

def load_bridge():
    """載入 AI-Wolf 橋接資料"""
    if os.path.exists(BRIDGE_FILE):
        try:
            with open(BRIDGE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "ai_to_wolf": {"command": "WAIT"},
        "wolf_to_ai": {"status": "IDLE"},
        "feedback_loop": {"total_trades": 0}
    }


def check_decision_stability(command: str, confidence: int) -> tuple:
    """
    🧠 決策穩定性檢查 - 防止 AI 反覆無常
    
    需要連續 N 次同方向判斷才執行，避免 5 秒一次判斷造成失憶反覆
    
    Returns:
        (is_stable, reason, confirmed_command)
    """
    if not DECISION_STABILITY.get("enabled", True):
        return True, "stability_disabled", command
    
    # 只對方向性決策做穩定性檢查
    if command not in ["LONG", "SHORT", "CUT_LOSS"]:
        return True, "non_directional", command
    
    required = DECISION_STABILITY.get("required_confirmations", 3)
    window = DECISION_STABILITY.get("confirmation_window_seconds", 30)
    pending = DECISION_STABILITY.get("pending_decisions", {})
    
    current_time = time.time()
    
    # 清理過期的待確認決策
    expired_keys = [k for k, v in pending.items() 
                    if current_time - v.get("first_time", 0) > window]
    for k in expired_keys:
        del pending[k]
    
    # 檢查當前決策
    if command in pending:
        decision = pending[command]
        decision["count"] += 1
        decision["last_time"] = current_time
        decision["confidences"].append(confidence)
        
        if decision["count"] >= required:
            # 達到確認次數，允許執行
            avg_confidence = sum(decision["confidences"]) / len(decision["confidences"])
            del pending[command]  # 清除已確認的決策
            return True, f"confirmed ({decision['count']}x in {current_time - decision['first_time']:.0f}s, avg_conf={avg_confidence:.0f})", command
        else:
            # 還需要更多確認
            return False, f"pending ({decision['count']}/{required})", "HOLD"
    else:
        # 新的決策方向，開始計數
        # 如果之前有其他方向的待確認，清除它
        other_directions = [k for k in pending.keys() if k != command]
        for k in other_directions:
            del pending[k]
        
        pending[command] = {
            "first_time": current_time,
            "last_time": current_time,
            "count": 1,
            "confidences": [confidence]
        }
        return False, f"new_decision (1/{required})", "HOLD"


def check_flip_cooldown(bridge: dict, new_command: str, new_confidence: int = 50) -> tuple:
    """
    🆕 檢查是否在翻轉冷卻期內
    
    Args:
        bridge: 當前 bridge 資料
        new_command: 新的指令 (LONG/SHORT/HOLD/CUT_LOSS)
        new_confidence: 新指令的信心度
    
    Returns:
        (should_flip: bool, reason: str, adjusted_command: str)
    """
    if not FLIP_COOLDOWN_CONFIG.get("enabled", True):
        return (True, "cooldown_disabled", new_command)
    
    # 只檢查 LONG <-> SHORT 的翻轉
    if new_command not in ["LONG", "SHORT"]:
        return (True, "not_directional", new_command)
    
    ai_to_wolf = bridge.get("ai_to_wolf", {})
    last_command = ai_to_wolf.get("command", "WAIT")
    last_timestamp = ai_to_wolf.get("timestamp")
    
    # 檢查是否是方向翻轉
    is_flip = (last_command == "LONG" and new_command == "SHORT") or \
              (last_command == "SHORT" and new_command == "LONG")
    
    if not is_flip:
        return (True, "same_direction", new_command)
    
    # === 翻轉冷卻檢查 ===
    cooldown_seconds = FLIP_COOLDOWN_CONFIG.get("cooldown_seconds", 120)
    
    # 檢查時間冷卻
    if last_timestamp:
        try:
            last_time = datetime.fromisoformat(last_timestamp)
            elapsed = (datetime.now() - last_time).total_seconds()
            
            if elapsed < cooldown_seconds:
                # 在冷卻期內，檢查是否有 override 條件
                
                # Override 1: 高信心度
                confidence_override = FLIP_COOLDOWN_CONFIG.get("confidence_override", 90)
                if new_confidence >= confidence_override:
                    return (True, f"high_confidence_override ({new_confidence}%)", new_command)
                
                # Override 2: 鯨魚翻轉
                wolf_to_ai = bridge.get("wolf_to_ai", {})
                whale_status = wolf_to_ai.get("whale_status", {})
                whale_direction = whale_status.get("current_direction")
                if FLIP_COOLDOWN_CONFIG.get("whale_flip_override", True):
                    if (new_command == "LONG" and whale_direction == "LONG") or \
                       (new_command == "SHORT" and whale_direction == "SHORT"):
                        return (True, f"whale_aligned_override ({whale_direction})", new_command)
                
                # Override 3: 大虧損強制止損
                current_pnl = wolf_to_ai.get("current_pnl_pct", 0)
                max_loss_force = FLIP_COOLDOWN_CONFIG.get("max_loss_force_flip", -3.0)
                if current_pnl < max_loss_force:
                    return (True, f"loss_override ({current_pnl:.2f}%)", new_command)
                
                # Override 4: 有利潤但達到門檻
                min_profit = FLIP_COOLDOWN_CONFIG.get("min_profit_to_flip", 0.5)
                if current_pnl >= min_profit:
                    return (True, f"profit_secured_override ({current_pnl:.2f}%)", new_command)
                
                # 沒有 override，阻止翻轉
                remaining = cooldown_seconds - elapsed
                return (False, f"cooldown_active ({remaining:.0f}s remaining)", "HOLD")
        except Exception as e:
            print(f"   ⚠️ Flip cooldown check error: {e}")
    
    return (True, "no_previous_trade", new_command)

def save_bridge(bridge):
    """儲存 AI-Wolf 橋接資料"""
    bridge['last_updated'] = datetime.now().isoformat()
    with open(BRIDGE_FILE, 'w') as f:
        json.dump(bridge, f, indent=2)

def load_team_config():
    """載入 AI 團隊配置"""
    if os.path.exists(TEAM_CONFIG_FILE):
        try:
            with open(TEAM_CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    # 默認配置
    return {
        "team_dynamics": {"current_mvp": "Macro", "debate_intensity": "HIGH"},
        "agent_profiles": {
            "macro": {"name": "The Macro Seer", "bias": "Conservative"},
            "micro": {"name": "The Scalp Hunter", "bias": "Aggressive"},
            "strategist": {"name": "The Strategist", "bias": "Neutral"}
        },
        "dynamic_parameters": {"max_leverage": 50, "risk_level": "MODERATE"}
    }

def save_team_config(config):
    """儲存 AI 團隊配置"""
    os.makedirs(os.path.dirname(TEAM_CONFIG_FILE), exist_ok=True)
    with open(TEAM_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def find_latest_pt_session():
    """找到最新的 paper trading 會話目錄"""
    pt_dir = Path("data/paper_trading")
    if not pt_dir.exists():
        return None
    
    # 確保只選取目錄且符合命名規則
    sessions = sorted([d for d in pt_dir.iterdir() if d.is_dir() and d.name.startswith("pt_")])
    return sessions[-1] if sessions else None


def load_signal_diagnostics(session_path):
    """載入最新的信號診斷數據 (CSV)"""
    csv_file = session_path / "signal_diagnostics.csv"
    if not csv_file.exists():
        return None
    
    try:
        # 讀取最後 50 行以進行微觀特徵分析
        df = pd.read_csv(csv_file)
        return df.tail(50)
    except Exception as e:
        print(f"⚠️ 讀取 CSV 失敗: {e}")
        return None


def load_whale_flip_analysis(session_path):
    """載入最新的 Whale Flip 分析數據 (CSV)"""
    csv_file = session_path / "whale_flip_analysis.csv"
    if not csv_file.exists():
        return None
    
    try:
        # 讀取更多行數以支援長期分析 (例如 3000 行，確保覆蓋 4 小時)
        df = pd.read_csv(csv_file)
        return df.tail(3000)
    except Exception as e:
        print(f"⚠️ 讀取 Whale Flip CSV 失敗: {e}")
        return None


def load_trading_data(session_path):
    """載入交易數據"""
    json_file = session_path / "trading_data.json"
    if not json_file.exists():
        return None
    
    with open(json_file, 'r') as f:
        return json.load(f)


def load_market_snapshot():
    """載入市場快照"""
    snapshot_path = Path("data/liquidation_pressure/latest_snapshot.json")
    if not snapshot_path.exists():
        return None
    
    with open(snapshot_path, 'r') as f:
        return json.load(f)


def load_advisor_state():
    """載入 AI 的長期預測狀態"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"last_prediction": None, "prediction_time": None, "entry_price": 0, "action": "WAIT"}


def save_advisor_state(state):
    """儲存 AI 的長期預測狀態"""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def load_strategy_plan():
    """載入 AI 的多階段策略計畫"""
    if os.path.exists(PLAN_FILE):
        try:
            with open(PLAN_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "plan_id": str(uuid.uuid4()),
        "created_at": datetime.now().isoformat(),
        "outlook": "NEUTRAL",
        "reasoning": "Initializing...",
        "phases": []
    }

def save_strategy_plan(plan):
    """儲存 AI 的多階段策略計畫"""
    with open(PLAN_FILE, 'w') as f:
        json.dump(plan, f, indent=2)

def load_learning_memory():
    """載入 AI 的學習記憶"""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "stats": {"total": 0, "correct": 0, "accuracy": 0.0},
        "mistakes": [], # 記錄失敗的預測特徵
        "successes": [] # 記錄成功的預測特徵
    }

def save_learning_memory(memory):
    """儲存 AI 的學習記憶"""
    with open(MEMORY_FILE, 'w') as f:
        json.dump(memory, f, indent=2)

def load_market_memory():
    """載入市場記憶 (Regime & Bias)"""
    if os.path.exists(MARKET_MEMORY_FILE):
        try:
            with open(MARKET_MEMORY_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "regime": {"current": "UNKNOWN", "since": datetime.now().isoformat(), "volatility_score": 0.0},
        "strategic_bias": {
            "direction": "NEUTRAL", 
            "strength": 0, 
            "since": datetime.now().isoformat(),
            "pending_change": None # 用於防抖動 (Debounce)
        },
        "short_term_memory": {"last_vpin_spike": None, "last_obi_flip": None}
    }

def save_market_memory(memory):
    """儲存市場記憶"""
    with open(MARKET_MEMORY_FILE, 'w') as f:
        json.dump(memory, f, indent=2)

def evaluate_past_prediction(current_price, previous_state, memory):
    """評估過去的預測是否準確，並更新記憶"""
    if not previous_state.get("prediction_time") or not previous_state.get("entry_price"):
        return memory, None

    pred_time = datetime.fromisoformat(previous_state["prediction_time"])
    time_diff = (datetime.now() - pred_time).total_seconds() / 60.0 # 分鐘
    
    # 至少過 5 分鐘才評估，或者價格波動超過 0.5%
    price_diff_pct = (current_price - previous_state["entry_price"]) / previous_state["entry_price"] * 100
    
    # 🆕 動態止損/止盈檢測：如果方向錯誤且波動超過 0.3% (高槓桿下約 15-30% 損益)，立即判定為失敗
    is_emergency = False
    action = previous_state.get("action", "WAIT")
    
    if action == "LONG" and price_diff_pct < -0.3: is_emergency = True
    if action == "SHORT" and price_diff_pct > 0.3: is_emergency = True
    
    if not is_emergency and time_diff < 5 and abs(price_diff_pct) < 0.5:
        return memory, None # 還太早，不評估

    result = "NEUTRAL"
    
    # 判定勝負
    if action == "LONG":
        if price_diff_pct > 0.2: result = "WIN"
        elif price_diff_pct < -0.2: result = "LOSS"
    elif action == "SHORT":
        if price_diff_pct < -0.2: result = "WIN"
        elif price_diff_pct > 0.2: result = "LOSS"
    elif action == "WAIT":
        # WAIT 的評估比較模糊，假設如果波動很小就是正確的
        if abs(price_diff_pct) < 0.3: result = "WIN"
        else: result = "LOSS" # 錯過了行情

    if result == "NEUTRAL":
        return memory, None
        
    # 🆕 如果是緊急情況，強制標記為嚴重失敗
    if is_emergency:
        result = "SEVERE_LOSS"
        print(f"   🚨 [Emergency] Detected rapid loss! Price moved {price_diff_pct:.2f}% against {action}.")

    # 更新統計
    memory["stats"]["total"] += 1
    if result == "WIN":
        memory["stats"]["correct"] += 1
        # 記錄成功模式 (只保留最近 20 筆)
        memory["successes"].append({
            "time": previous_state["prediction_time"],
            "action": action,
            "context_summary": previous_state.get("last_prediction", "")[:50]
        })
        if len(memory["successes"]) > 20: memory["successes"].pop(0)
    else:
        # 記錄失敗模式 (只保留最近 20 筆)
        memory["mistakes"].append({
            "time": previous_state["prediction_time"],
            "action": action,
            "severity": "HIGH" if result == "SEVERE_LOSS" else "NORMAL",
            "reason": f"Price moved {price_diff_pct:.2f}% against prediction",
            "context_summary": previous_state.get("last_prediction", "")[:50]
        })
        if len(memory["mistakes"]) > 20: memory["mistakes"].pop(0)

    memory["stats"]["accuracy"] = round(memory["stats"]["correct"] / memory["stats"]["total"] * 100, 2)
    
    # 重置預測時間，避免重複評估
    previous_state["prediction_time"] = None 
    save_advisor_state(previous_state)
    save_learning_memory(memory)
    
    return memory, result


# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 主力策略預測器 (Whale Strategy Predictor)
# 主動分析主力可能的意圖、陷阱、和最佳獲利/避險策略
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_whale_strategy(
    price: float,
    ls_ratio: float,
    long_liq: float,
    short_liq: float,
    taker_ratio: float,
    oi_change_pct: float,
    funding_rate: float,
    whale_short_term: dict,
    whale_long_term: dict,
    rt_whale: dict,
    rt_micro: dict,
    cascade_active: bool,
    cascade_direction: str,
    cascade_strength: float
) -> dict:
    """
    🧠 主力策略預測器
    
    分析主力（鯨魚）可能的策略意圖，並提供：
    1. 主力當前策略預測
    2. 潛在陷阱警告
    3. 跟單/反向策略建議
    4. 最佳進場/避險時機
    
    Returns:
        {
            "whale_intent": str,           # 主力意圖
            "predicted_strategy": str,     # 預測的主力策略
            "trap_warning": dict,          # 陷阱警告
            "retail_opportunity": dict,    # 散戶機會
            "danger_zones": list,          # 危險區域
            "optimal_action": str,         # 最佳行動
            "confidence": float            # 信心度 0-100
        }
    """
    result = {
        "whale_intent": "UNKNOWN",
        "predicted_strategy": "HOLD",
        "trap_warning": {"active": False, "type": None, "description": ""},
        "retail_opportunity": {"exists": False, "type": None, "description": ""},
        "danger_zones": [],
        "optimal_action": "WAIT",
        "confidence": 0,
        "analysis_summary": ""
    }
    
    # ═══════════════════════════════════════════════════════════════
    # 1️⃣ 基礎數據整理
    # ═══════════════════════════════════════════════════════════════
    whale_net_qty = rt_whale.get('net_qty_btc', whale_short_term.get('net_qty', 0))
    whale_dominance = rt_whale.get('dominance', whale_short_term.get('dominance', 0))
    obi = rt_micro.get('obi', 0)
    vpin = rt_micro.get('vpin', 0)
    
    # 主力方向判斷
    whale_direction = "NEUTRAL"
    if whale_net_qty > 5:
        whale_direction = "BULLISH"
    elif whale_net_qty < -5:
        whale_direction = "BEARISH"
    
    # ═══════════════════════════════════════════════════════════════
    # 2️⃣ 主力策略模式識別
    # ═══════════════════════════════════════════════════════════════
    
    strategies_detected = []
    confidence_factors = []
    
    # 📌 模式 A: 吸籌 (Accumulation)
    # 特徵: 鯨魚買入 + 價格橫盤/小跌 + OI 上升 + Funding 負或中性
    if (whale_net_qty > 10 and 
        oi_change_pct > 0 and 
        funding_rate <= 0.0001 and
        abs(obi) < 0.3):
        strategies_detected.append({
            "name": "ACCUMULATION",
            "description": "主力正在吸籌 - 悄悄買入不拉盤",
            "whale_goal": "在散戶不注意時低價建立多頭倉位",
            "next_move": "吸籌完成後可能拉盤",
            "retail_strategy": "跟隨做多，但分批進場",
            "confidence": min(80, 50 + whale_net_qty * 2)
        })
        confidence_factors.append(("吸籌模式", 25))
    
    # 📌 模式 B: 派發 (Distribution)
    # 特徵: 鯨魚賣出 + 價格橫盤/小漲 + OI 上升 + Funding 正
    if (whale_net_qty < -10 and 
        oi_change_pct > 0 and 
        funding_rate >= 0.0001 and
        abs(obi) < 0.3):
        strategies_detected.append({
            "name": "DISTRIBUTION",
            "description": "主力正在派發 - 悄悄賣出不砸盤",
            "whale_goal": "在散戶貪婪時高價出貨",
            "next_move": "派發完成後可能砸盤",
            "retail_strategy": "準備做空，或減少多頭倉位",
            "confidence": min(80, 50 + abs(whale_net_qty) * 2)
        })
        confidence_factors.append(("派發模式", 25))
    
    # 📌 模式 C: 多頭陷阱 (Bull Trap)
    # 特徵: 價格突破 + 鯨魚在賣 + VPIN 高 + 散戶在追
    if (whale_net_qty < -5 and 
        taker_ratio > 1.05 and  # 散戶在追多
        vpin > 0.6 and
        obi > 0.2):  # 看起來很強
        strategies_detected.append({
            "name": "BULL_TRAP",
            "description": "🚨 多頭陷阱！假突破 - 主力正在出貨給追高散戶",
            "whale_goal": "誘導散戶追多，然後砸盤收割",
            "next_move": "即將反轉下跌",
            "retail_strategy": "⚠️ 避免追多！等待回落再評估",
            "confidence": min(90, 60 + vpin * 30)
        })
        result["trap_warning"] = {
            "active": True,
            "type": "BULL_TRAP",
            "description": "主力正在設置多頭陷阱，散戶追多中"
        }
        confidence_factors.append(("多頭陷阱", 35))
    
    # 📌 模式 D: 空頭陷阱 (Bear Trap)
    # 特徵: 價格跌破 + 鯨魚在買 + VPIN 高 + 散戶在追空
    if (whale_net_qty > 5 and 
        taker_ratio < 0.95 and  # 散戶在追空
        vpin > 0.6 and
        obi < -0.2):  # 看起來很弱
        strategies_detected.append({
            "name": "BEAR_TRAP",
            "description": "🚨 空頭陷阱！假跌破 - 主力正在吸貨給追空散戶",
            "whale_goal": "誘導散戶追空，然後拉盤軋空",
            "next_move": "即將反轉上漲",
            "retail_strategy": "⚠️ 避免追空！考慮逢低做多",
            "confidence": min(90, 60 + vpin * 30)
        })
        result["trap_warning"] = {
            "active": True,
            "type": "BEAR_TRAP",
            "description": "主力正在設置空頭陷阱，散戶追空中"
        }
        confidence_factors.append(("空頭陷阱", 35))
    
    # 📌 模式 E: 擠壓 (Squeeze Play)
    # 特徵: 高槓桿環境 + 單邊爆倉壓力 + 鯨魚準備觸發
    if long_liq > 0.3 and whale_direction == "BEARISH":
        strategies_detected.append({
            "name": "LONG_SQUEEZE_SETUP",
            "description": "主力準備觸發多頭擠壓",
            "whale_goal": "砸盤觸發多頭爆倉，製造瀑布",
            "next_move": "價格可能快速下跌",
            "retail_strategy": "避免持有多頭，可考慮跟空",
            "confidence": min(85, 50 + long_liq * 100)
        })
        confidence_factors.append(("多頭擠壓準備", 30))
        
    if short_liq > 0.3 and whale_direction == "BULLISH":
        strategies_detected.append({
            "name": "SHORT_SQUEEZE_SETUP",
            "description": "主力準備觸發空頭擠壓",
            "whale_goal": "拉盤觸發空頭爆倉，製造軋空",
            "next_move": "價格可能快速上漲",
            "retail_strategy": "避免持有空頭，可考慮跟多",
            "confidence": min(85, 50 + short_liq * 100)
        })
        confidence_factors.append(("空頭擠壓準備", 30))
    
    # 📌 模式 F: 洗盤 (Shakeout)
    # 特徵: 鯨魚方向不變 + 價格劇烈波動 + 散戶在止損
    if (abs(whale_net_qty) > 15 and 
        vpin > 0.7 and 
        abs(obi) > 0.4):
        shakeout_direction = "多頭" if whale_net_qty > 0 else "空頭"
        strategies_detected.append({
            "name": "SHAKEOUT",
            "description": f"主力洗盤中 - 製造恐慌但不改變方向",
            "whale_goal": f"震出{shakeout_direction}散戶的籌碼",
            "next_move": f"洗盤結束後繼續原方向 ({'上漲' if whale_net_qty > 0 else '下跌'})",
            "retail_strategy": f"堅定持有{shakeout_direction}，不被假動作洗出",
            "confidence": min(75, 50 + abs(whale_net_qty))
        })
        confidence_factors.append(("洗盤模式", 20))
    
    # 📌 模式 G: 瀑布清算 (Cascade Liquidation)
    if cascade_active and cascade_strength > 50:
        cascade_type = "多頭" if cascade_direction == "LONG_SQUEEZE" else "空頭"
        strategies_detected.append({
            "name": "CASCADE_IN_PROGRESS",
            "description": f"🔥 {cascade_type}瀑布清算進行中！",
            "whale_goal": "利用連環爆倉加速價格移動",
            "next_move": "等待瀑布耗盡後反彈",
            "retail_strategy": f"順勢操作，但注意反轉信號",
            "confidence": min(95, 70 + cascade_strength * 0.3)
        })
        confidence_factors.append(("瀑布清算", 40))
    
    # ═══════════════════════════════════════════════════════════════
    # 3️⃣ 綜合判斷與建議
    # ═══════════════════════════════════════════════════════════════
    
    if not strategies_detected:
        # 沒有明顯策略時
        if whale_dominance > 0.5:
            result["whale_intent"] = "POSITIONING"
            result["predicted_strategy"] = whale_direction
            result["optimal_action"] = "FOLLOW_WHALE" if whale_direction != "NEUTRAL" else "WAIT"
            result["confidence"] = 40
            result["analysis_summary"] = "主力正在建倉，方向尚不明確，建議觀望或小倉位跟隨"
        else:
            result["whale_intent"] = "INACTIVE"
            result["predicted_strategy"] = "RANGING"
            result["optimal_action"] = "WAIT"
            result["confidence"] = 30
            result["analysis_summary"] = "主力活動度低，市場震盪，建議觀望"
    else:
        # 有策略時，選擇信心度最高的
        best_strategy = max(strategies_detected, key=lambda x: x['confidence'])
        
        result["whale_intent"] = best_strategy['name']
        result["predicted_strategy"] = best_strategy['name']
        result["confidence"] = best_strategy['confidence']
        result["analysis_summary"] = f"{best_strategy['description']}\n👉 主力目標: {best_strategy['whale_goal']}\n👉 下一步: {best_strategy['next_move']}\n💡 建議: {best_strategy['retail_strategy']}"
        
        # 根據策略決定最佳行動
        if best_strategy['name'] in ['BULL_TRAP', 'DISTRIBUTION', 'LONG_SQUEEZE_SETUP']:
            result["optimal_action"] = "AVOID_LONG"
            result["danger_zones"].append("追多危險")
        elif best_strategy['name'] in ['BEAR_TRAP', 'ACCUMULATION', 'SHORT_SQUEEZE_SETUP']:
            result["optimal_action"] = "AVOID_SHORT"
            result["danger_zones"].append("追空危險")
        elif best_strategy['name'] == 'SHAKEOUT':
            result["optimal_action"] = "HOLD_POSITION"
        elif best_strategy['name'] == 'CASCADE_IN_PROGRESS':
            if cascade_direction == "LONG_SQUEEZE":
                result["optimal_action"] = "SHORT_WITH_CAUTION"
            else:
                result["optimal_action"] = "LONG_WITH_CAUTION"
        
        # 散戶機會識別
        if best_strategy['name'] in ['BEAR_TRAP', 'ACCUMULATION']:
            result["retail_opportunity"] = {
                "exists": True,
                "type": "LONG_OPPORTUNITY",
                "description": "主力在吸籌，可考慮跟隨做多"
            }
        elif best_strategy['name'] in ['BULL_TRAP', 'DISTRIBUTION']:
            result["retail_opportunity"] = {
                "exists": True,
                "type": "SHORT_OPPORTUNITY", 
                "description": "主力在出貨，可考慮做空或觀望"
            }
    
    # 計算總信心度
    total_confidence = sum(cf[1] for cf in confidence_factors)
    result["confidence"] = min(95, max(result["confidence"], total_confidence))
    result["detected_patterns"] = [s['name'] for s in strategies_detected]
    
    return result


def format_whale_strategy_for_prompt(analysis: dict) -> str:
    """將主力策略分析格式化為 Prompt 文字"""
    if not analysis or analysis.get('whale_intent') == 'UNKNOWN':
        return "**主力策略分析**: 數據不足，無法判斷"
    
    trap_warning = ""
    if analysis['trap_warning']['active']:
        trap_warning = f"""
🚨 **陷阱警告**: {analysis['trap_warning']['type']}
   {analysis['trap_warning']['description']}
"""
    
    opportunity = ""
    if analysis['retail_opportunity']['exists']:
        opportunity = f"""
💰 **散戶機會**: {analysis['retail_opportunity']['type']}
   {analysis['retail_opportunity']['description']}
"""
    
    danger = ""
    if analysis['danger_zones']:
        danger = f"""
⚠️ **危險區域**: {', '.join(analysis['danger_zones'])}
"""
    
    return f"""
════════════════════════════════════════════════════════════════
🎯 **主力策略預測** (信心度: {analysis['confidence']}%)
════════════════════════════════════════════════════════════════
🐋 **主力意圖**: {analysis['whale_intent']}
📊 **預測策略**: {analysis['predicted_strategy']}
🎬 **最佳行動**: {analysis['optimal_action']}
{trap_warning}{opportunity}{danger}
📝 **分析摘要**:
{analysis['analysis_summary']}
════════════════════════════════════════════════════════════════
"""


def extract_micro_features(signals_df):
    """提取微觀特徵 (毫秒級特徵模擬)"""
    if signals_df is None or signals_df.empty:
        return {}
    
    # 使用最後 20 筆數據 (假設每筆間隔很短)
    recent = signals_df.tail(20)
    
    features = {
        "vpin_spike": False,
        "obi_flip": False,
        "volatility_increasing": False,
        "avg_vpin": 0.0,
        "avg_obi": 0.0
    }
    
    if 'vpin' in recent.columns:
        vpin_values = recent['vpin'].astype(float)
        features["avg_vpin"] = vpin_values.mean()
        features["vpin_max"] = vpin_values.max()
        # 檢測 VPIN 是否在短時間內急劇上升 (Spike)
        if vpin_values.max() - vpin_values.min() > 0.3 and vpin_values.iloc[-1] > 0.7:
            features["vpin_spike"] = True
            
    if 'obi' in recent.columns:
        obi_values = recent['obi'].astype(float)
        features["avg_obi"] = obi_values.mean()
        # 檢測 OBI 是否發生正負翻轉 (Flip)
        if (obi_values.max() > 0.2 and obi_values.min() < -0.2):
            features["obi_flip"] = True
            
    return features

def update_market_regime(signals_df, market_memory):
    """更新市場體制 (Trending vs Ranging) 並寫入記憶"""
    if signals_df is None or signals_df.empty:
        return "UNKNOWN", market_memory
    
    # 使用 VPIN 和 OBI 的波動性來判斷
    recent = signals_df.tail(50)
    vpin_std = recent['vpin'].std()
    obi_abs_mean = recent['obi'].abs().mean()
    
    # 計算當前分數
    volatility_score = float(vpin_std) if not pd.isna(vpin_std) else 0.0
    trend_score = float(obi_abs_mean) if not pd.isna(obi_abs_mean) else 0.0
    
    # 判斷當前狀態
    current_regime = "RANGING"
    if volatility_score > 0.1 or trend_score > 0.5:
        current_regime = "VOLATILE"
    elif trend_score > 0.3:
        current_regime = "TRENDING"
        
    # 更新記憶 (簡單的滯後邏輯，避免頻繁切換)
    last_regime = market_memory["regime"].get("current", "UNKNOWN")
    
    # 如果狀態改變，記錄時間
    if current_regime != last_regime:
        market_memory["regime"]["current"] = current_regime
        market_memory["regime"]["since"] = datetime.now().isoformat()
    
    market_memory["regime"]["volatility_score"] = volatility_score
    market_memory["regime"]["trend_score"] = trend_score
    
    return current_regime, market_memory

def summarize_mode_performance(trading_data):
    """總結其他模式的表現，以判斷市場特性"""
    if not trading_data or 'modes' not in trading_data:
        return "No trading data available."
    
    modes = trading_data['modes']
    summary = []
    
    # 分類模式
    trend_modes = ['M1', 'M7', 'M8', 'M9']
    mean_reversion_modes = ['M0', 'M2', 'M6']
    whale_modes = ['M_WHALE', 'M_LP_WHALE']
    
    trend_pnl = 0
    mean_pnl = 0
    
    for name, data in modes.items():
        pnl = data.get('pnl_usdt', 0)
        # 簡單的名稱匹配
        is_trend = any(m in name for m in trend_modes)
        is_mean = any(m in name for m in mean_reversion_modes)
        
        if is_trend: trend_pnl += pnl
        if is_mean: mean_pnl += pnl
        
        if pnl != 0:
            summary.append(f"{name}: ${pnl:.2f}")
            
    regime_hint = "UNCLEAR"
    if trend_pnl > mean_pnl and trend_pnl > 0:
        regime_hint = "TRENDING (Trend strategies are winning)"
    elif mean_pnl > trend_pnl and mean_pnl > 0:
        regime_hint = "RANGING (Mean reversion strategies are winning)"
    elif trend_pnl < 0 and mean_pnl < 0:
        regime_hint = "CHOPPY/DIFFICULT (All strategies losing)"
        
    return f"Market Regime Hint: {regime_hint}. Details: {', '.join(summary)}"

def get_llm_client(model_type="openai"):
    """
    獲取 LLM 客戶端 (OpenAI / Ollama / Kimi K2)
    
    支援的 model_type:
    - "openai": 使用 OpenAI GPT (需要 OPENAI_API_KEY)
    - "ollama": 使用本地 Ollama (qwen3:32b 等)
    - "kimi": 使用 Kimi K2 API (需要 KIMI_API_KEY)
    """
    if model_type == "ollama":
        # Ollama 不需要 API Key，base_url 指向本地
        return OpenAI(
            base_url='http://localhost:11434/v1',
            api_key='ollama', # required, but unused
        )
    elif model_type == "kimi":
        # Kimi K2 API
        api_key = os.getenv("KIMI_API_KEY")
        if not api_key:
            print("❌ 未找到 KIMI_API_KEY，請在 .env 中設定")
            return None
        return OpenAI(
            base_url='https://api.moonshot.cn/v1',
            api_key=api_key,
        )
    else:
        # 默認使用 OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("❌ 未找到 OPENAI_API_KEY")
            return None
        return OpenAI(api_key=api_key)

def analyze_with_ai(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state):
    """使用 AI 分析當前交易狀況並提供建議"""
    
    # 讀取配置以決定使用哪個模型
    team_config = load_team_config()
    model_choice = team_config.get("model_config", {}).get("provider", "openai") # openai or ollama
    
    client = get_llm_client(model_choice)
    if not client: return "❌ LLM Client Init Failed"
    
    # 載入記憶與計畫
    current_plan = load_strategy_plan()
    learning_memory = load_learning_memory()
    market_memory = load_market_memory()
    
    # 1. 提取關鍵市場指標
    try:
        latest_oi = market_snapshot['open_interest'][-1]
        latest_ls = market_snapshot['global_long_short'][-1]
        
        oi_val = float(latest_oi['sumOpenInterest'])
        oi_usdt = float(latest_oi['sumOpenInterestValue'])
        price = oi_usdt / oi_val if oi_val > 0 else 0
        ls_ratio = float(latest_ls['longShortRatio'])
        
        # 🆕 提取爆倉壓力 (Liquidation Pressure)
        liq_pressure = market_snapshot.get('liquidation_pressure', {})
        long_liq = liq_pressure.get('L_long_liq', 0)
        short_liq = liq_pressure.get('L_short_liq', 0)
        
    except:
        price = 0
        oi_val = 0
        ls_ratio = 0
        long_liq = 0
        short_liq = 0
    
    # 0. 自我評估與學習
    learning_memory, eval_result = evaluate_past_prediction(price, previous_state, learning_memory)
    if eval_result:
        print(f"   🎓 [Self-Learning] Previous prediction result: {eval_result}. Accuracy: {learning_memory['stats']['accuracy']}%")

    # 2. 提取信號摘要 & 微觀特徵 & 更新市場體制
    signal_summary = ""
    micro_features = extract_micro_features(signals_df)
    market_regime, market_memory = update_market_regime(signals_df, market_memory)
    
    if signals_df is not None and not signals_df.empty:
        latest_signals = signals_df.tail(5)[['mode', 'action', 'reason', 'signal_score', 'obi', 'vpin']].to_dict('records')
        signal_summary = json.dumps(latest_signals, ensure_ascii=False)

    # 3. 提取 Whale Flip 數據 (多重時間框架)
    whale_short_term = {"net_qty": 0, "dominance": 0}
    whale_long_term = {"net_qty": 0, "trend": "NEUTRAL"}
    
    if whale_flip_df is not None and not whale_flip_df.empty:
        # 確保 timestamp 欄位是 datetime 格式
        if 'timestamp' in whale_flip_df.columns:
            whale_flip_df['timestamp'] = pd.to_datetime(whale_flip_df['timestamp'])
            
        # 短期 (最近 15 分鐘)
        # 使用時間過濾而非固定行數
        current_time = pd.Timestamp.now()
        short_term_start = current_time - pd.Timedelta(minutes=15)
        
        if 'timestamp' in whale_flip_df.columns:
            recent_whales = whale_flip_df[whale_flip_df['timestamp'] >= short_term_start]
        else:
            recent_whales = whale_flip_df.tail(20) # Fallback
            
        if 'net_qty' in recent_whales.columns and not recent_whales.empty:
            whale_short_term["net_qty"] = recent_whales['net_qty'].sum()
            whale_short_term["dominance"] = recent_whales['dominance'].mean()
            
        # 長期 (真正鎖定過去 4 小時)
        long_term_start = current_time - pd.Timedelta(hours=4)
        
        if 'timestamp' in whale_flip_df.columns:
            long_term_whales = whale_flip_df[whale_flip_df['timestamp'] >= long_term_start]
        else:
            long_term_whales = whale_flip_df.tail(1000) # Fallback
            
        if 'net_qty' in long_term_whales.columns and not long_term_whales.empty:
            net_qty_sum = long_term_whales['net_qty'].sum()
            whale_long_term["net_qty"] = net_qty_sum
            
            # 根據 4 小時累積量判斷趨勢 (門檻值需要隨時間窗口調整)
            # 4小時的累積量通常較大，提高門檻以過濾雜訊
            if net_qty_sum > 500: whale_long_term["trend"] = "STRONG_ACCUMULATION"
            elif net_qty_sum > 150: whale_long_term["trend"] = "MILD_ACCUMULATION"
            elif net_qty_sum < -500: whale_long_term["trend"] = "STRONG_DISTRIBUTION"
            elif net_qty_sum < -150: whale_long_term["trend"] = "MILD_DISTRIBUTION"

    # 4. 構建 AI Prompt (動態信號 + 長短期記憶 + 堅定決策 + 高槓桿刷單)
    # 這裡將被拆分為多個 Agent 的 Prompt
    pass

def get_agent_opinion(client, agent_name, system_prompt, user_context, model_name="gpt-4o-mini"):
    """獲取單個 Agent 的意見"""
    try:
        # 🔧 GPT-5 系列不支持自訂 temperature 和 max_tokens，移除這些參數
        response = client.chat.completions.create(
            model=model_name, 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_context}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Agent {agent_name} failed: {e}"

def run_council_meeting(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state):
    """召開 AI 戰略委員會會議 (4 Agents Debate)"""
    
    # 讀取配置以決定使用哪個模型
    team_config = load_team_config()
    model_config = team_config.get("model_config", {})
    provider = model_config.get("provider", "openai") # openai or ollama
    model_name = model_config.get("model_name", "gpt-4o-mini") # gpt-4o-mini or qwen3:32b
    
    client = get_llm_client(provider)
    if not client: return "❌ LLM Client Init Failed"
    
    # 載入記憶與計畫
    current_plan = load_strategy_plan()
    learning_memory = load_learning_memory()
    market_memory = load_market_memory()
    team_config = load_team_config()
    
    # --- 數據準備 ---
    # 🆕 v1.3.0: 優先讀取 Testnet 真實數據 (M🐺 = T🐺 統一模式)
    bridge = load_bridge()
    wolf_data = bridge.get('wolf_to_ai', {})
    rt_whale = wolf_data.get('whale_status', {})
    rt_micro = wolf_data.get('market_microstructure', {})
    
    # 🆕 讀取 Testnet 真實持倉狀態 (優先於 Paper)
    testnet_data = wolf_data.get('testnet_trading', {})
    websocket_data = wolf_data.get('websocket_realtime', {})
    
    # 統一數據來源: WebSocket > Testnet > Paper
    if websocket_data.get('has_position'):
        # WebSocket 有即時數據，最準確
        position_source = 'WEBSOCKET'
        has_position = True
        position_info = websocket_data.get('position', {})
        current_pnl_usdt = position_info.get('pnl_usdt', 0)
        current_pnl_pct = position_info.get('pnl_pct', 0)
        position_direction = position_info.get('direction', '')
        entry_price = position_info.get('entry_price', 0)
        leverage = position_info.get('leverage', 75)
        ws_alert = websocket_data.get('alert', {})
        print(f"   📡 [AI數據源] WebSocket 即時: {position_direction} PnL={current_pnl_usdt:.2f} ({current_pnl_pct:.1f}%)")
    elif testnet_data.get('has_position'):
        # Testnet 有持倉
        position_source = 'TESTNET'
        has_position = True
        position_info = testnet_data.get('position', {})
        current_pnl_usdt = testnet_data.get('unrealized_pnl', 0)
        current_pnl_pct = testnet_data.get('pnl_pct', 0)
        position_direction = testnet_data.get('direction', '')
        entry_price = testnet_data.get('entry_price', 0)
        leverage = testnet_data.get('leverage', 75)
        ws_alert = {}
        print(f"   📡 [AI數據源] Testnet: {position_direction} PnL={current_pnl_usdt:.2f}")
    else:
        # 無持倉
        position_source = 'NONE'
        has_position = False
        current_pnl_usdt = 0
        current_pnl_pct = 0
        position_direction = ''
        entry_price = 0
        leverage = 75
        ws_alert = {}
        print(f"   📡 [AI數據源] 無持倉")
    
    # 🆕 讀取 Testnet 帳戶狀態
    testnet_account = testnet_data.get('account', {})
    testnet_balance = testnet_account.get('balance', 100)
    testnet_total_trades = testnet_account.get('total_trades', 0)
    testnet_total_pnl = testnet_account.get('total_pnl', 0)
    
    # 🆕 讀取 Cascade Alert (爆倉潮訊號)
    cascade_alert = wolf_data.get('cascade_alert', {})
    cascade_alert = wolf_data.get('cascade_alert', {})
    cascade_active = cascade_alert.get('active', False)
    cascade_direction = cascade_alert.get('direction', 'NONE')  # LONG_SQUEEZE / SHORT_SQUEEZE / MIXED
    cascade_strength = cascade_alert.get('strength', 0)
    cascade_warning = cascade_alert.get('warning', '')
    cascade_suggestion = cascade_alert.get('suggested_action', '')
    
    # 🆕 讀取鯨魚訊號品質追蹤 (重新設計：不只看歷史，看特徵！)
    whale_effectiveness = wolf_data.get('whale_signal_effectiveness', {})
    whale_signal_recommendation = whale_effectiveness.get('recommendation', 'WAIT')
    whale_effectiveness_rate = whale_effectiveness.get('effectiveness_rate', 0)
    whale_signal_strength = whale_effectiveness.get('signal_strength', 'NONE')
    whale_is_effective = whale_effectiveness.get('is_signal_effective', False)
    
    # 🆕 品質分數 (核心指標！)
    whale_quality_score = whale_effectiveness.get('quality_score', 0)
    whale_quality_info = whale_effectiveness.get('signal_quality', {})
    whale_quality_grade = whale_quality_info.get('grade', 'N/A')
    whale_quality_factors = whale_effectiveness.get('quality_factors', [])
    whale_warning_factors = whale_effectiveness.get('warning_factors', [])
    
    # 印出鯨魚訊號品質分析
    if whale_signal_strength != 'NONE' or whale_quality_score > 0:
        # 品質等級顏色
        grade_emoji = {
            'A': '🟢', 'A-': '🟢', 'B+': '🟡', 'B': '🟡', 
            'B-': '🟠', 'C+': '🟠', 'C': '🔴', 'D': '🔴', 'N/A': '⚪'
        }
        g_emoji = grade_emoji.get(whale_quality_grade, '⚪')
        eff_emoji = "✅" if whale_is_effective else "⏳"
        
        print(f"   🐳 [鯨魚訊號品質]")
        print(f"      ├─ 品質分數: {whale_quality_score:.0f}/100 ({g_emoji} {whale_quality_grade})")
        print(f"      ├─ 強度={whale_signal_strength} | 歷史有效率={whale_effectiveness_rate:.0f}% {eff_emoji}")
        print(f"      └─ 建議: {whale_signal_recommendation}")
        
        # 顯示正面因素 (最多 3 個)
        if whale_quality_factors:
            print(f"      📊 正面因素:")
            for factor in whale_quality_factors[:3]:
                print(f"         ✓ {factor}")
        
        # 顯示警告因素 (重要！)
        if whale_warning_factors:
            print(f"      ⚠️ 警告:")
            for warning in whale_warning_factors[:3]:
                print(f"         ✗ {warning}")
    
    try:
        latest_oi = market_snapshot['open_interest'][-1]
        latest_ls = market_snapshot['global_long_short'][-1]
        oi_val = float(latest_oi['sumOpenInterest'])
        oi_usdt = float(latest_oi['sumOpenInterestValue'])
        price = oi_usdt / oi_val if oi_val > 0 else 0
        ls_ratio = float(latest_ls['longShortRatio'])
        liq_pressure = market_snapshot.get('liquidation_pressure', {})
        long_liq = liq_pressure.get('L_long_liq', 0)
        short_liq = liq_pressure.get('L_short_liq', 0)
        
        # 🆕 讀取更多指標
        taker_ratio = liq_pressure.get('taker_ratio', {})
        taker_buy_sell_ratio = taker_ratio.get('ratio', 1.0) if isinstance(taker_ratio, dict) else 1.0
        oi_change_pct = liq_pressure.get('oi_change_pct', 0)
        if isinstance(oi_change_pct, list):
            oi_change_pct = oi_change_pct[-1] if oi_change_pct else 0
        
        # funding_rate 是一個 list，取最新的值
        funding_rate_data = market_snapshot.get('funding_rate', [])
        if isinstance(funding_rate_data, list) and funding_rate_data:
            funding_rate = float(funding_rate_data[-1].get('fundingRate', 0))
        else:
            funding_rate = 0
    except:
        price = 0; oi_val = 0; ls_ratio = 0; long_liq = 0; short_liq = 0
        taker_buy_sell_ratio = 1.0; oi_change_pct = 0; funding_rate = 0
    
    # 自我評估
    learning_memory, eval_result = evaluate_past_prediction(price, previous_state, learning_memory)
    
    # 特徵提取
    micro_features = extract_micro_features(signals_df)
    market_regime, market_memory = update_market_regime(signals_df, market_memory)
    mode_performance_summary = summarize_mode_performance(trading_data)
    
    signal_summary = ""
    if signals_df is not None and not signals_df.empty:
        latest_signals = signals_df.tail(5)[['mode', 'action', 'reason', 'signal_score', 'obi', 'vpin']].to_dict('records')
        signal_summary = json.dumps(latest_signals, ensure_ascii=False)

    whale_short_term = {"net_qty": 0, "dominance": 0}
    whale_long_term = {"net_qty": 0, "trend": "NEUTRAL"}
    if whale_flip_df is not None and not whale_flip_df.empty:
        recent_whales = whale_flip_df.tail(5)
        if 'net_qty' in recent_whales.columns:
            whale_short_term["net_qty"] = recent_whales['net_qty'].sum()
            whale_short_term["dominance"] = recent_whales['dominance'].mean()
        long_term_whales = whale_flip_df.tail(300)
        if 'net_qty' in long_term_whales.columns:
            net_qty_sum = long_term_whales['net_qty'].sum()
            whale_long_term["net_qty"] = net_qty_sum
            if net_qty_sum > 200: whale_long_term["trend"] = "STRONG_ACCUMULATION"
            elif net_qty_sum > 50: whale_long_term["trend"] = "MILD_ACCUMULATION"
            elif net_qty_sum < -200: whale_long_term["trend"] = "STRONG_DISTRIBUTION"
            elif net_qty_sum < -50: whale_long_term["trend"] = "MILD_DISTRIBUTION"

    # --- 定義 Agents (使用 team_config) ---
    profiles = team_config.get("agent_profiles", {})
    params = team_config.get("dynamic_parameters", {})
    
    # 檢查是否有正在進行的 Grand Strategy
    grand_strategy = current_plan.get("grand_strategy", {"active": False})
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 🎯 Phase 0: 主力策略預測器 (Whale Strategy Predictor)
    # 在所有 Agent 辯論前，先分析主力可能的意圖和陷阱
    # ═══════════════════════════════════════════════════════════════════════════
    whale_strategy_analysis = analyze_whale_strategy(
        price=price,
        ls_ratio=ls_ratio,
        long_liq=long_liq,
        short_liq=short_liq,
        taker_ratio=taker_buy_sell_ratio,
        oi_change_pct=oi_change_pct,
        funding_rate=funding_rate,
        whale_short_term=whale_short_term,
        whale_long_term=whale_long_term,
        rt_whale=rt_whale,
        rt_micro=rt_micro,
        cascade_active=cascade_active,
        cascade_direction=cascade_direction,
        cascade_strength=cascade_strength
    )
    
    # 🔧 v2.1: 防護 whale_strategy_analysis 為 None 的情況
    if whale_strategy_analysis is None:
        whale_strategy_analysis = {
            "whale_intent": "UNKNOWN",
            "trap_warning": {"active": False},
            "optimal_action": "WAIT",
            "confidence": 0,
            "predicted_strategy": "HOLD",
            "danger_zones": [],
            "detected_patterns": []
        }
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 🔧 v2.0 OPTIMIZED PROMPTS - 減少重複，合併規則，降低 Token 消耗
    # ═══════════════════════════════════════════════════════════════════════════
    
    p_macro = profiles.get("macro", {})
    p_micro = profiles.get("micro", {})
    p_strat = profiles.get("strategist", {})
    
    # 🎯 共用的市場狀態摘要 (只傳一次給 Commander)
    market_state_summary = f"""
=== MARKET STATE ===
Price: {price} | LS: {ls_ratio:.2f} | Funding: {funding_rate:.6f}
Whale: {rt_whale.get('current_direction')} ({rt_whale.get('net_qty_btc', 0):.1f} BTC, {rt_whale.get('dominance', 0):.1%})
Micro: OBI={rt_micro.get('obi', 0):.2f}, VPIN={rt_micro.get('vpin', 0):.2f}
Cascade: {"⚠️ " + cascade_direction + f" ({cascade_strength})" if cascade_active else "None"}
Whale Intent: {whale_strategy_analysis.get('whale_intent', '?')}, Trap: {whale_strategy_analysis.get('trap_warning', {}).get('active', False)}
"""
    
    # 1. 👴 Macro - 精簡版
    macro_prompt = f"""You are '{p_macro.get('name', 'Macro')}'. Grand Strategist (1-5H vision).
RULE: Real-time whale > Historical. Cascade strength>60 = follow it.
Task: Direction (BULLISH/BEARISH/NEUTRAL), Thesis, Invalidation price.
Data: Whale(4H)={whale_long_term['trend']}, Modes={mode_performance_summary[:100]}"""
    
    macro_context = f"""{market_state_summary}
Liq Pressure: L={long_liq}, S={short_liq} | Taker={taker_buy_sell_ratio:.2f} | OI Δ={oi_change_pct:+.1f}%
Grand Strategy: {json.dumps(grand_strategy, ensure_ascii=False)[:200]}"""

    # 2. ⚡ Micro - 精簡版
    micro_prompt = f"""You are '{p_micro.get('name', 'Micro')}'. Tactical Navigator.
Task: Validate strategy. ON_TRACK/MINOR_DEVIATION/MAJOR_THREAT. Don't flip-flop.
Data: Whale(15m)={whale_short_term['net_qty']:.1f}BTC, VPIN={micro_features.get('avg_vpin', 0):.2f}"""
    
    micro_context = f"""{market_state_summary}
Strategy: {json.dumps(grand_strategy, ensure_ascii=False)[:150]}
Signals: {signal_summary[:200] if signal_summary else 'None'}"""

    # 3. ⚖️ Strategist - 精簡版  
    hybrid_prompt = f"""You are '{p_strat.get('name', 'Strategist')}'. Discipline checker.
Task: PASS/FAIL discipline. MAINTAIN_COURSE or REVISE_PLAN.
Data: Bias={market_memory['strategic_bias']['direction']}"""
    
    hybrid_context = f"""Plan: {json.dumps(current_plan, ensure_ascii=False)[:200]}
Started: {grand_strategy.get('start_time', 'N/A')}"""

    # --- 執行辯論 (平行調用) ---
    # 🆕 印出主力策略分析結果
    if whale_strategy_analysis.get('confidence', 0) >= 40:
        print(f"   🎯 [主力策略] {whale_strategy_analysis.get('whale_intent', '?')} | 信心度: {whale_strategy_analysis.get('confidence', 0)}%")
        if whale_strategy_analysis.get('trap_warning', {}).get('active'):
            trap_type = whale_strategy_analysis['trap_warning'].get('type', '?')
            print(f"   🚨 [陷阱警告] {trap_type} - {whale_strategy_analysis['trap_warning'].get('description', '')}")
        if whale_strategy_analysis.get('optimal_action', 'WAIT') != 'WAIT':
            print(f"   💡 [建議行動] {whale_strategy_analysis.get('optimal_action', 'WAIT')}")
    
    print(f"   🧠 AI deciding (Model: {model_name})...")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 🚀 GPT-5 優化：單次 API 呼叫（智力足夠，不需要多 Agent 辯論）
    # 之前：4 次 API (Macro + Micro + Hybrid + Commander) = 慢 + 貴
    # 現在：1 次 API (Unified Commander) = 快 + 省
    # ═══════════════════════════════════════════════════════════════════════════

    # --- 4. 👑 The Supreme Commander (裁判) ---
    # 🆕 構建主力策略分析訊息 (精簡版)
    trap_active = whale_strategy_analysis.get('trap_warning', {}).get('active', False)
    # 🔧 v2.2: 強化 None 防護 - 確保不會 NoneType.upper() 錯誤
    whale_intent = whale_strategy_analysis.get('whale_intent') or 'UNKNOWN'
    whale_optimal = whale_strategy_analysis.get('optimal_action') or 'WAIT'
    if whale_intent is None:
        whale_intent = 'UNKNOWN'
    if whale_optimal is None:
        whale_optimal = 'WAIT'

    # 🆕 讀取交易績效 (用於洗盤偵測)
    feedback_loop = bridge.get('feedback_loop', {})
    # 🔧 修正：bridge 使用 consecutive_losses 不是 failure_streak
    failure_streak = feedback_loop.get('consecutive_losses', 0)
    avg_holding_time = feedback_loop.get('avg_holding_time', 0)
    total_trades = feedback_loop.get('total_trades', 0)
    
    # 🆕 洗盤模式偵測
    is_shakeout_mode = whale_intent.upper() in ['SHAKEOUT', 'WASHOUT', 'STOP_HUNT', 'TRAP']
    is_dead_market = wolf_data.get('volatility', {}).get('atr_pct', 0.1) < 0.03
    is_being_washed = failure_streak >= 2 and avg_holding_time < 60  # 連虧 + 快速出場
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 🆕 Phase 2: 讀取 Loss Review 請求 (Paper Trader 發送的虧損分析請求)
    # ═══════════════════════════════════════════════════════════════════════════
    wolf_to_ai = bridge.get('wolf_to_ai', {})
    loss_review = wolf_to_ai.get('loss_review', {})
    has_loss_review = loss_review.get('request_type') == 'LOSS_ANALYSIS'
    
    # 構建 Loss Review 上下文 (如果有請求)
    loss_review_context = ""
    if has_loss_review:
        lr_mode = loss_review.get('mode', 'UNKNOWN')
        lr_streak = loss_review.get('consecutive_losses', 0)
        lr_total_loss = loss_review.get('total_loss_pct', 0)
        lr_prelim = loss_review.get('preliminary_diagnosis', 'UNKNOWN')
        lr_recent = loss_review.get('recent_trades', [])
        
        loss_review_context = f"""
=== 🚨 LOSS REVIEW REQUEST (PRIORITY!) ===
Mode: {lr_mode} | Consecutive Losses: {lr_streak} | Total Loss: {lr_total_loss:.2f}%
Preliminary Diagnosis: {lr_prelim}
Recent Trades:
"""
        for trade in lr_recent[-3:]:  # 只顯示最近 3 筆
            loss_review_context += f"  - {trade.get('direction', '?')} @ ${trade.get('entry_price', 0):.2f} → PnL: {trade.get('pnl_pct', 0):.2f}%, Reason: {trade.get('exit_reason', '?')}\n"
        
        loss_review_context += f"""
**YOUR TASK**: Analyze WHY we're losing and provide `recommended_adjustments` in your output!
Categories: TOXIC_FLOW, WHALE_TRAP, DEAD_MARKET_CHURN, FALSE_BREAKOUT, WHALE_FLIP
"""
        print(f"   🔍 [LOSS REVIEW] Received loss analysis request for {lr_mode}")
        print(f"   🔍 Consecutive: {lr_streak} | Total Loss: {lr_total_loss:.2f}% | Diagnosis: {lr_prelim}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 👑 Commander Prompt - 精簡整合版 v2.0
    # 將所有規則整合到一個清晰的決策框架
    # ═══════════════════════════════════════════════════════════════════════════
    # 計算持倉時間（如果有持倉）
    holding_seconds = 0
    if has_position and wolf_data.get('testnet_trading', {}).get('position', {}).get('entry_time'):
        try:
            entry_time_str = wolf_data['testnet_trading']['position']['entry_time']
            entry_time = datetime.fromisoformat(entry_time_str)
            holding_seconds = (datetime.now() - entry_time).total_seconds()
        except:
            pass
    
    commander_prompt = f"""You are the SUPREME COMMANDER. Make FINAL trading decisions.

=== 🚨 ABSOLUTE RULE #1: WHALE DOMINANCE OVERRIDE (CRITICAL!) ===
**When whale_dominance >= 70%: YOU MUST FOLLOW WHALE DIRECTION!**
- Current whale_direction: {rt_whale.get('current_direction', 'UNKNOWN')}
- Current whale_dominance: {rt_whale.get('dominance', 0):.0%}
- Current whale_net_btc: {rt_whale.get('net_qty_btc', 0):.1f} BTC

IF whale_dominance >= 0.7:
  - Whale=LONG → Your action MUST be LONG or HOLD (NEVER SHORT!)
  - Whale=SHORT → Your action MUST be SHORT or HOLD (NEVER LONG!)
  - VIOLATION = Getting washed by market makers = GUARANTEED LOSS!

=== CORE RULES (PRIORITY ORDER) ===
1. **WHALE DOMINANCE (>70%)**: FOLLOW whale direction UNCONDITIONALLY
2. DIRECTION PROBES (Most Reliable): Mup>Mdown → BULLISH, Mdown>Mup → BEARISH
3. CASCADE (strength>60): MUST follow cascade direction
4. WHALE REALTIME: NetQty<-5 → BEARISH, NetQty>+5 → BULLISH
5. TRAP DETECTED: Do NOT enter trap direction
6. NO FLIP-FLOP: Only change direction on MAJOR structural break

=== LEVERAGE RULES (MIN 50x) ===
- Normal (60-75% conf): 50-75x
- High (75-90% conf): 75-100x  
- CASCADE aligned: 100x
- TRAP detected: 30x

=== ⏱️ MINIMUM HOLDING TIME RULE ===
**IF holding_seconds < 180 (3 minutes): DO NOT issue CUT_LOSS!**
- Exception: Only if PnL < -10% (catastrophic loss)
- Current holding: {holding_seconds:.0f} seconds

=== 📊 MARKET CONTEXT (For Reference Only) ===
- Whale Intent: {whale_intent}
- Failure Streak: {failure_streak} | Avg Holding: {avg_holding_time:.0f}s | Total Trades: {total_trades}
- Note: These are WARNING signals, not trading blockers!

=== CURRENT STATE ===
{market_state_summary}
Position: {"NO_POSITION" if not has_position else f"{position_direction} @ ${entry_price:.2f}, PnL={current_pnl_pct:.1f}%, Holding={holding_seconds:.0f}s"}
Strategy: {grand_strategy.get('direction', 'INACTIVE')} (Active={grand_strategy.get('active', False)})
Probes: {json.dumps(wolf_data.get('direction_probes', {}), ensure_ascii=False)}

=== ADDITIONAL CONTEXT ===
Whale Long-term: {whale_long_term['trend']} ({whale_long_term['net_qty']:.0f} BTC)
Whale Short-term: {whale_short_term['net_qty']:.1f} BTC, Dominance={whale_short_term['dominance']:.1%}
Micro Features: OBI={micro_features.get('avg_obi', 0):.2f}, VPIN={micro_features.get('avg_vpin', 0):.2f}
Mode Performance: {mode_performance_summary[:80]}

=== DECISION FLOW (SIMPLIFIED - Like Original +47.66% Version) ===
1. IF whale_dominance >= 0.7: MUST align with whale direction!
2. IF failure_streak >= 5: CIRCUIT BREAKER - Output HOLD
3. IF Strategy ACTIVE + ON_TRACK: HOLD/ADD (Don't flip-flop!)
4. IF Strategy ACTIVE + MAJOR_THREAT: CUT_LOSS (only if holding > 180s OR PnL < -10%)
5. IF Strategy INACTIVE: Create NEW strategy based on Macro + Whale
6. CASCADE > 60 strength: Follow cascade direction
7. **IF LOSS_REVIEW PRESENT**: Analyze losses and provide recommended_adjustments!

=== 🔍 LOSS REVIEW HANDLING (When loss_review is present) ===
IF you receive a LOSS_REVIEW request in context:
1. Analyze the recent_trades patterns
2. Identify root cause from: TOXIC_FLOW, WHALE_TRAP, DEAD_MARKET_CHURN, FALSE_BREAKOUT, WHALE_FLIP
3. MUST output "recommended_adjustments" with specific parameter changes
4. Set tactical_action to HOLD until parameters are adjusted

Diagnosis Guide:
- TOXIC_FLOW: Large trades hitting our stops → Increase stop_loss_pct, reduce leverage
- WHALE_TRAP: Entered opposite to whale → Increase confidence_threshold, add cooldown
- DEAD_MARKET_CHURN: Low volatility choppy losses → Switch to LIMIT entry, reduce position
- FALSE_BREAKOUT: Breakout failed quickly → Tighter stop, faster trailing
- WHALE_FLIP: Whale changed direction mid-trade → Add cooldown, reduce leverage
{loss_review_context}

OUTPUT JSON (IMPORTANT - Include dynamic_params AND recommended_adjustments!):
{{
  "strategic_bias": "BULLISH|BEARISH",
  "tactical_action": "LONG|SHORT|HOLD|ADD_LONG|ADD_SHORT|CUT_LOSS",
  "recommended_leverage": 50-125,
  "conviction_score": 50-100,
  "whale_reversal_price": 0,
  "cascade_aligned": bool,
  "trap_avoided": bool,
  "dynamic_params": {{
    "leverage": 50-125,
    "take_profit_pct": 5.0-15.0,
    "stop_loss_pct": 2.0-5.0,
    "position_size_pct": 50-100,
    "trailing_activation": 3.0-10.0,
    "trailing_distance": 1.0-3.0,
    "entry_strategy": "MARKET|LIMIT",
    "limit_offset_bps": 0-10,
    "max_holding_minutes": 10-60,
    "add_position_threshold": 2.0-5.0
  }},
  "ai_prediction": {{
    "price_target": number,
    "price_direction": "UP|DOWN",
    "expected_move_pct": 0.1-2.0,
    "time_horizon_minutes": 5-60,
    "invalidation_price": number
  }},
  "grand_strategy_update": {{
    "active": bool,
    "direction": "...",
    "thesis": "...",
    "target_duration_hours": 3,
    "start_time": "{datetime.now().isoformat()}",
    "invalidation_price": 0
  }},
  "recommended_adjustments": {{
    "diagnosis": "TOXIC_FLOW|WHALE_TRAP|DEAD_MARKET_CHURN|FALSE_BREAKOUT|WHALE_FLIP|NONE",
    "confidence_threshold_delta": -5 to +10,
    "stop_loss_pct_delta": -1.0 to +2.0,
    "leverage_multiplier": 0.5 to 1.0,
    "cooldown_minutes": 0-60,
    "strategy_switch": "NONE|SNIPER|SCALP|SWING",
    "reasoning": "Why these adjustments"
  }},
  "analysis": "Brief reason"
}}

=== DYNAMIC PARAMS GUIDELINES ===
- HIGH confidence (>85%) + whale aligned: leverage=100-125, tp=8-12%, sl=2-3%
- MEDIUM confidence (70-85%): leverage=75-100, tp=6-8%, sl=3-4%
- LOW confidence (<70%): leverage=50-75, tp=5-6%, sl=4-5%
- DEAD MARKET (ATR<0.05%): entry_strategy=LIMIT, limit_offset_bps=5-10
- HIGH VOLATILITY (ATR>0.3%): smaller position_size_pct=50-70%, wider trailing
"""
    
    # 🆕 獲取當前持倉狀態
    current_position_status = "NO_POSITION"
    if has_position:
        current_position_status = f"IN_POSITION: {position_direction} @ ${entry_price:.2f}, PnL: {current_pnl_pct:.1f}%"
    
    commander_context = f"""
Current Price: {price}
Market Regime: {market_regime}
Other Modes: {mode_performance_summary[:100]}
REAL-TIME WHALE: {rt_whale}
REAL-TIME MICRO: {rt_micro}
DIRECTION PROBES (Mup/Mdown PnL): {wolf_data.get('direction_probes', {})}

🎯 **CURRENT POSITION STATUS**: {current_position_status}
"""

    try:
        # 🔧 GPT-5 系列不支持自訂 temperature，移除此參數
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": commander_prompt},
                {"role": "user", "content": commander_context}
            ],
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        
        # 🆕 健壯的 JSON 解析 (處理 DeepSeek 等模型可能的空回應)
        if not content or not content.strip():
            print("   ⚠️ [AI Response] Empty response, defaulting to HOLD")
            result = {
                "strategic_bias": "NEUTRAL",
                "tactical_action": "HOLD",
                "conviction_score": 50,
                "analysis": "AI returned empty response, holding position",
                "recommended_leverage": 75
            }
        else:
            try:
                # 嘗試提取 JSON (有時模型會在 JSON 前後加文字)
                import re
                json_match = re.search(r'\{[\s\S]*\}', content)
                if json_match:
                    result = json.loads(json_match.group())
                else:
                    result = json.loads(content)
            except json.JSONDecodeError as je:
                print(f"   ⚠️ [JSON Parse Error] {je}")
                print(f"   ⚠️ Raw content: {content[:200]}...")
                result = {
                    "strategic_bias": "NEUTRAL",
                    "tactical_action": "HOLD",
                    "conviction_score": 50,
                    "analysis": f"JSON parse failed: {str(je)[:50]}",
                    "recommended_leverage": 75
                }
        
        # --- 處理結果 (與之前相同) ---
        analysis_text = result.get('analysis') or "No analysis provided"
        
        new_state = {
            "last_prediction": analysis_text[:100] + "...", 
            "prediction_time": datetime.now().isoformat(),
            "entry_price": price,
            "action": result.get('tactical_action') or 'WAIT',
            "strategic_bias": result.get('strategic_bias') or 'NEUTRAL',
            "confidence": result.get('conviction_score') or 50,
            "whale_reversal_price": result.get('whale_reversal_price', 0),  # 🆕 新增反轉價格
            "full_analysis": f"👑 COMMANDER DECISION (GPT-5-mini):\n{analysis_text}"
        }
        save_advisor_state(new_state)
        
        # 🆕 更新 Bridge: AI → Wolf 指令
        bridge = load_bridge()
        wolf_feedback = bridge.get('wolf_to_ai', {})
        
        # 🆕 翻轉冷卻檢查 (避免頻繁翻倉浪費手續費)
        raw_command = result.get('tactical_action') or 'WAIT'
        raw_confidence = result.get('conviction_score') or 50
        
        should_flip, flip_reason, final_command = check_flip_cooldown(bridge, raw_command, raw_confidence)
        
        if not should_flip:
            print(f"   🛑 [FLIP COOLDOWN] AI wanted {raw_command} but blocked: {flip_reason}")
            print(f"   🛑 Adjusted to: {final_command}")
        elif flip_reason not in ["same_direction", "not_directional", "no_previous_trade", "cooldown_disabled"]:
            print(f"   ✅ [FLIP ALLOWED] {flip_reason}")
        
        # 🆕 v2.4: 決策穩定性檢查 (防止 5 秒判斷造成 AI 反覆無常)
        is_stable, stability_reason, stable_command = check_decision_stability(final_command, raw_confidence)
        
        if not is_stable:
            print(f"   🧠 [STABILITY] AI wants {final_command} but {stability_reason}")
            final_command = stable_command
        elif stability_reason not in ["stability_disabled", "non_directional"]:
            print(f"   🧠 [STABILITY] Decision {stability_reason}")
        
        # 🆕 最小持倉時間保護 (防止過早 CUT_LOSS)
        MIN_HOLDING_SECONDS = 180  # 最少持倉 3 分鐘 (保底)
        CATASTROPHIC_LOSS_PCT = -10.0  # 災難性虧損門檻
        
        # 🆕 最小持倉時間保護 (防止過早 CUT_LOSS 被洗盤洗掉)
        # 🔧 v2.2: 移除「鯨魚翻向無視時間」邏輯，避免被連續洗掉
        # 讓 AI 全權決定，但這裡仍保留基本保護
        whale_dom = rt_whale.get('dominance', 0) or 0
        whale_dir = (rt_whale.get('current_direction') or '').upper()
        
        if final_command == "CUT_LOSS" and has_position:
            # 計算持倉時間
            holding_time = 0
            try:
                testnet_pos = wolf_data.get('testnet_trading', {}).get('position', {})
                if testnet_pos.get('entry_time'):
                    entry_time = datetime.fromisoformat(testnet_pos['entry_time'])
                    holding_time = (datetime.now() - entry_time).total_seconds()
            except:
                pass
            
            # 🆕 v2.3: 智慧翻轉判斷 - 用信號品質指標，不是笨笨等 180 秒
            whale_signal = wolf_feedback.get('whale_signal_effectiveness', {})
            signal_quality = whale_signal.get('quality_score', 50)  # 0-100
            signal_grade = whale_signal.get('signal_quality', {}).get('grade', 'C')  # A/B/C/D/F
            is_effective = whale_signal.get('is_signal_effective', False)  # 價格有沒有跟上
            recommendation = whale_signal.get('recommendation', 'CAUTIOUS')  # TRUST/CAUTIOUS/IGNORE
            warning_factors = whale_signal.get('warning_factors', [])
            
            # 🎯 智慧翻轉條件 (任一滿足就允許提早翻轉)
            smart_flip_allowed = False
            smart_flip_reason = ""
            
            # 條件 1: 高品質信號 (A 或 B 級，分數 >= 70)
            if signal_grade in ['A', 'B'] and signal_quality >= 70:
                smart_flip_allowed = True
                smart_flip_reason = f"高品質信號 (Grade={signal_grade}, Score={signal_quality})"
            
            # 條件 2: 信號有效 + 建議信任
            elif is_effective and recommendation == 'TRUST':
                smart_flip_allowed = True
                smart_flip_reason = f"信號有效且建議信任 (effective={is_effective}, rec={recommendation})"
            
            # 條件 3: 無警告因素 + 中等以上品質
            elif len(warning_factors) == 0 and signal_quality >= 60:
                smart_flip_allowed = True
                smart_flip_reason = f"無警告因素 (warnings=0, score={signal_quality})"
            
            # 條件 4: 災難性虧損 (> -10%)
            elif current_pnl_pct <= CATASTROPHIC_LOSS_PCT:
                smart_flip_allowed = True
                smart_flip_reason = f"災難性虧損 ({current_pnl_pct:.1f}% <= {CATASTROPHIC_LOSS_PCT}%)"
            
            # 條件 5: 持倉超過 180 秒 (保底)
            elif holding_time >= MIN_HOLDING_SECONDS:
                smart_flip_allowed = True
                smart_flip_reason = f"持倉時間足夠 ({holding_time:.0f}s >= {MIN_HOLDING_SECONDS}s)"
            
            if smart_flip_allowed:
                print(f"   🧠 [SMART FLIP] 允許提早翻轉: {smart_flip_reason}")
                print(f"   🐳 Whale Signal: Grade={signal_grade}, Score={signal_quality}, Rec={recommendation}")
                if warning_factors:
                    print(f"   ⚠️ Warnings: {warning_factors}")
            else:
                # 不滿足智慧條件，阻擋翻轉
                print(f"   🛡️ [SMART PROTECT] 信號品質不足，暫緩翻轉")
                print(f"   🐳 Whale Signal: Grade={signal_grade}, Score={signal_quality}, Rec={recommendation}")
                print(f"   ⚠️ Warnings: {warning_factors}")
                print(f"   ⏱️ Holding: {holding_time:.0f}s | PnL: {current_pnl_pct:.1f}%")
                print(f"   🛡️ Adjusted CUT_LOSS → HOLD (等待更好信號)")
                final_command = "HOLD"
        
        # 🎯 高精準狙擊策略檢查 (只有高命中率才進場)
        sniper_can_enter, sniper_reason, sniper_leverage = check_sniper_entry_conditions(bridge, raw_confidence)
        
        if final_command in ["LONG", "SHORT"]:
            if not sniper_can_enter:
                print(f"   🎯 [SNIPER REJECT] {sniper_reason}")
                print(f"   🎯 Adjusted {final_command} → HOLD (等待更好機會)")
                final_command = "HOLD"
            else:
                print(f"   🎯 [SNIPER APPROVED] {sniper_reason} | 動態槓桿: {sniper_leverage}x")
        
        # 🆕 鯨魚方向強制修正 (程式層級保險，防止 AI 判斷錯誤)
        WHALE_OVERRIDE_THRESHOLD = 0.70  # 70% 支配性時強制一致
        whale_dom = rt_whale.get('dominance', 0) or 0
        whale_dir = (rt_whale.get('current_direction') or '').upper()
        
        if whale_dom >= WHALE_OVERRIDE_THRESHOLD and whale_dir in ['LONG', 'SHORT']:
            if final_command == 'LONG' and whale_dir == 'SHORT':
                print(f"   🐳 [WHALE OVERRIDE] AI={final_command} vs Whale={whale_dir} (Dom={whale_dom:.0%})")
                print(f"   🐳 強制修正: LONG → HOLD (鯨魚在做空！)")
                final_command = 'HOLD'
            elif final_command == 'SHORT' and whale_dir == 'LONG':
                print(f"   🐳 [WHALE OVERRIDE] AI={final_command} vs Whale={whale_dir} (Dom={whale_dom:.0%})")
                print(f"   🐳 強制修正: SHORT → HOLD (鯨魚在做多！)")
                final_command = 'HOLD'
            elif final_command in ['LONG', 'SHORT'] and final_command == whale_dir:
                print(f"   🐳 [WHALE ALIGNED] AI={final_command} = Whale={whale_dir} (Dom={whale_dom:.0%}) ✅")
        
        # 🔧 v2.1: 洗盤模式改為「警告」而非「阻擋」
        # 原版 +47.66% 時期沒有強制阻擋，只有警告
        # 過度阻擋會讓 AI 完全不交易，反而錯過機會
        if is_shakeout_mode or is_being_washed:
            if final_command in ['LONG', 'SHORT']:
                print(f"   ⚠️ [SHAKEOUT WARNING] 洗盤模式偵測 (不阻擋，僅警告)")
                print(f"      - Whale Intent: {whale_intent}")
                print(f"      - Failure Streak: {failure_streak}")
                print(f"      - Avg Holding: {avg_holding_time:.0f}s")
                # 🔧 v2.1: 只有在連虧 5 次以上才強制 HOLD (原版的 CIRCUIT BREAKER)
                if failure_streak >= 5:
                    print(f"   🚨 [CIRCUIT BREAKER] 連虧 {failure_streak} 次 → 強制 HOLD")
                    final_command = 'HOLD'
                else:
                    print(f"   ✅ 連虧 {failure_streak} 次 < 5 次，允許交易")
        
        # 使用狙擊策略的止盈止損設定
        targets = SNIPER_CONFIG["targets"]
        
        # 🆕 Phase 1: 讀取 AI 動態參數 (如果 AI 有輸出的話)
        ai_dynamic_params = result.get('dynamic_params', {})
        ai_prediction = result.get('ai_prediction', {})
        
        # 如果 AI 沒有輸出動態參數，使用預設值
        default_dynamic_params = {
            "leverage": sniper_leverage if sniper_can_enter else SNIPER_CONFIG["min_leverage"],
            "take_profit_pct": targets["take_profit_pct"],
            "stop_loss_pct": targets["stop_loss_pct"],
            "position_size_pct": 100,
            "trailing_activation": targets["trailing_activation"],
            "trailing_distance": targets["trailing_distance"],
            "entry_strategy": "MARKET",
            "limit_offset_bps": 0,
            "max_holding_minutes": 30,
            "add_position_threshold": 3.0
        }
        
        # 合併 AI 參數和預設參數 (AI 優先)
        final_dynamic_params = {**default_dynamic_params, **ai_dynamic_params}
        
        # 🛡️ 安全限制 - 防止 AI 設定過於激進的參數
        # 🔧 v2.5: SL 下限提高到 3.5%，避免被小波動洗掉
        final_dynamic_params["leverage"] = max(50, min(125, final_dynamic_params.get("leverage", 75)))
        final_dynamic_params["take_profit_pct"] = max(3.0, min(20.0, final_dynamic_params.get("take_profit_pct", 10.0)))
        final_dynamic_params["stop_loss_pct"] = max(3.5, min(8.0, final_dynamic_params.get("stop_loss_pct", 3.5)))  # 🔧 下限 3.5%
        final_dynamic_params["position_size_pct"] = max(30, min(100, final_dynamic_params.get("position_size_pct", 100)))
        final_dynamic_params["trailing_activation"] = max(2.0, min(15.0, final_dynamic_params.get("trailing_activation", 7.0)))
        final_dynamic_params["trailing_distance"] = max(1.0, min(5.0, final_dynamic_params.get("trailing_distance", 2.5)))  # 🔧 下限 1.0%
        final_dynamic_params["max_holding_minutes"] = max(5, min(120, final_dynamic_params.get("max_holding_minutes", 30)))
        
        # 預設 AI 預測
        default_ai_prediction = {
            "price_target": 0,
            "price_direction": result.get('strategic_bias', 'NEUTRAL').replace('BULLISH', 'UP').replace('BEARISH', 'DOWN'),
            "expected_move_pct": 0.5,
            "time_horizon_minutes": 15,
            "invalidation_price": 0
        }
        final_ai_prediction = {**default_ai_prediction, **ai_prediction}
        
        # 🆕 Phase 2: 處理 AI 的 recommended_adjustments (用於 Loss Review)
        ai_recommended_adjustments = result.get('recommended_adjustments', {})
        default_adjustments = {
            "diagnosis": "NONE",
            "confidence_threshold_delta": 0,
            "stop_loss_pct_delta": 0,
            "leverage_multiplier": 1.0,
            "cooldown_minutes": 0,
            "strategy_switch": "NONE",
            "reasoning": ""
        }
        final_recommended_adjustments = {**default_adjustments, **ai_recommended_adjustments}
        
        # 🆕 如果有 Loss Review 請求且 AI 有給出診斷，印出結果
        if has_loss_review and final_recommended_adjustments.get('diagnosis', 'NONE') != 'NONE':
            print(f"   🔧 [AI DIAGNOSIS] {final_recommended_adjustments['diagnosis']}")
            print(f"   🔧 Adjustments: conf_delta={final_recommended_adjustments['confidence_threshold_delta']}, "
                  f"sl_delta={final_recommended_adjustments['stop_loss_pct_delta']}, "
                  f"lev_mult={final_recommended_adjustments['leverage_multiplier']}")
            print(f"   🔧 Reasoning: {final_recommended_adjustments.get('reasoning', 'N/A')[:100]}")
        
        bridge['ai_to_wolf'] = {
            "command": final_command,  # 🔧 使用經過冷卻+狙擊檢查的指令
            "raw_command": raw_command,  # 🆕 保留原始指令供參考
            "flip_cooldown_applied": not should_flip,  # 🆕 標記是否被冷卻阻止
            "flip_reason": flip_reason,  # 🆕 記錄原因
            "sniper_approved": sniper_can_enter,  # 🆕 狙擊策略是否批准
            "sniper_reason": sniper_reason,  # 🆕 狙擊策略原因
            "direction": result.get('strategic_bias') or 'NEUTRAL',
            "confidence": raw_confidence,
            # 🆕 Phase 1: 保留舊欄位供向後兼容，但也加入 dynamic_params
            "leverage": final_dynamic_params["leverage"],
            "whale_reversal_price": result.get('whale_reversal_price', 0),
            "take_profit_pct": final_dynamic_params["take_profit_pct"],
            "stop_loss_pct": final_dynamic_params["stop_loss_pct"],
            "trailing_activation": final_dynamic_params["trailing_activation"],
            "trailing_distance": final_dynamic_params["trailing_distance"],
            "reasoning": analysis_text[:200],
            "timestamp": datetime.now().isoformat(),
            # 🆕 Phase 1: AI 動態參數 (完整版)
            "dynamic_params": final_dynamic_params,
            # 🆕 Phase 1: AI 價格預測
            "ai_prediction": final_ai_prediction,
            # 🆕 Phase 2: AI 建議調整 (用於 Loss Review)
            "recommended_adjustments": final_recommended_adjustments,
            # 🆕 加入主力策略分析結果
            "whale_strategy": {
                "intent": whale_strategy_analysis.get('whale_intent', 'UNKNOWN'),
                "predicted_strategy": whale_strategy_analysis.get('predicted_strategy', 'HOLD'),
                "optimal_action": whale_strategy_analysis.get('optimal_action', 'WAIT'),
                "trap_warning": whale_strategy_analysis.get('trap_warning', {}),
                "danger_zones": whale_strategy_analysis.get('danger_zones', []),
                "confidence": whale_strategy_analysis.get('confidence', 0),
                "detected_patterns": whale_strategy_analysis.get('detected_patterns', [])
            }
        }
        save_bridge(bridge)

        # 🔄 強制同步 Strategy Plan (確保與 Bridge 同步)
        # 即使 LLM 沒有返回完整的 strategic_plan，也要根據當前決策更新關鍵欄位
        try:
            current_plan = load_strategy_plan()
            
            # 1. 提取 LLM 的計畫細節 (如果有的話)
            if 'strategic_plan' in result and isinstance(result['strategic_plan'], dict):
                plan_update = result['strategic_plan']
                current_plan.update(plan_update)
            
            # 🆕 更新 Grand Strategy
            if 'grand_strategy_update' in result and isinstance(result['grand_strategy_update'], dict):
                current_plan['grand_strategy'] = result['grand_strategy_update']
                
            # 2. 強制覆蓋關鍵狀態 (以 Bridge 決策為準)
            current_plan['market_bias'] = result.get('strategic_bias', 'NEUTRAL')
            current_plan['phase'] = result.get('tactical_action', 'WAIT')
            current_plan['max_leverage'] = result.get('recommended_leverage', 1)
            current_plan['risk_level'] = team_config['dynamic_parameters'].get('risk_level', 'MODERATE')
            
            # 3. 更新元數據
            current_plan['created_at'] = datetime.now().isoformat()
            current_plan['plan_id'] = str(uuid.uuid4())
            
            save_strategy_plan(current_plan)
            print(f"   📝 [Plan Synced] Strategy Plan updated to {current_plan['market_bias']} / {current_plan['phase']}")
        except Exception as e:
            print(f"   ⚠️ Failed to sync strategy plan: {e}")
        
        # 🆕 讀取 Wolf 的完整回饋並做智能調整
        feedback_loop = bridge.get('feedback_loop', {})
        # 🔧 修正：bridge 使用 consecutive_losses 不是 failure_streak
        failure_streak = feedback_loop.get('consecutive_losses', 0)
        
        # 🚨 CIRCUIT BREAKER (熔斷機制)
        if failure_streak >= 5:
            print(f"   🚨 [CIRCUIT BREAKER] Failure streak {failure_streak} detected! Forcing HOLD and RESET.")
            bridge['ai_to_wolf']['command'] = 'HOLD'
            bridge['ai_to_wolf']['reasoning'] = f"CIRCUIT BREAKER: Too many consecutive losses ({failure_streak}). Pausing to realign."
            save_bridge(bridge)
            
            # 重置 Grand Strategy
            current_plan['grand_strategy'] = {"active": False}
            save_strategy_plan(current_plan)
            return "CIRCUIT BREAKER TRIGGERED"

        if wolf_feedback.get('status') == 'IN_POSITION' or has_position:
            # 🆕 使用統一的 Testnet 數據
            pnl_pct = current_pnl_pct if has_position else wolf_feedback.get('current_pnl_pct', 0)
            
            # 📊 Priority 1: 分析鯨魚狀態
            whale_status = wolf_feedback.get('whale_status', {})
            whale_direction = whale_status.get('current_direction')
            whale_dominance = whale_status.get('dominance', 0)
            whale_flip_count = whale_status.get('flip_count_30min', 0)
            
            # 📈 Priority 1: 分析市場微結構
            micro = wolf_feedback.get('market_microstructure', {})
            obi = micro.get('obi', 0)
            vpin = micro.get('vpin', 0)
            funding_rate = micro.get('funding_rate', 0)
            
            # 🌊 Priority 1: 分析波動環境
            volatility = wolf_feedback.get('volatility', {})
            atr_pct = volatility.get('atr_pct', 0)
            is_dead_market = volatility.get('is_dead_market', False)
            regime = volatility.get('regime', 'UNKNOWN')
            
            # 🎯 Priority 2: 檢查預測準確度
            feedback_loop = bridge.get('feedback_loop', {})
            prediction_accuracy = feedback_loop.get('prediction_accuracy', {})
            direction_accuracy = prediction_accuracy.get('direction_accuracy_pct', 0)
            
            # 🚨 Priority 3: 風險警示
            risk_indicators = wolf_feedback.get('risk_indicators', {})
            liquidation_pressure = risk_indicators.get('liquidation_pressure', 0)
            whale_trap_prob = risk_indicators.get('whale_trap_probability', 0)
            
            # 智能決策邏輯
            warnings = []
            profit_adjustments = []
            
            # 🎯 動態調整止盈配置
            profit_config_file = "ai_profit_config.json"
            if os.path.exists(profit_config_file):
                try:
                    with open(profit_config_file, 'r') as f:
                        profit_config = json.load(f)
                    
                    # 根據市場狀況和績效調整止盈目標
                    win_rate = feedback_loop.get('win_rate', 0)
                    total_trades = feedback_loop.get('total_trades', 0)
                    
                    should_update_config = False
                    
                    # 根據勝率動態調整
                    if total_trades >= 5:
                        dynamic_profit = profit_config.get('dynamic_profit_taking', {})
                        base_targets = dynamic_profit.get('base_targets', {})
                        
                        if win_rate >= 70 and base_targets.get('standard', 15.0) < 25.0:
                            # 勝率高,提高止盈目標
                            base_targets['standard'] = 25.0
                            base_targets['dead_market_reversal'] = 15.0
                            base_targets['reversal_ambush'] = 30.0
                            profit_adjustments.append(f"📈 High win rate ({win_rate:.0f}%) → Increased profit targets")
                            should_update_config = True
                        elif win_rate < 30 and base_targets.get('standard', 15.0) > 10.0:
                            # 勝率低,降低止盈目標 (但不能低於手續費成本)
                            base_targets['standard'] = 12.0
                            base_targets['dead_market_reversal'] = 10.0
                            base_targets['reversal_ambush'] = 15.0
                            profit_adjustments.append(f"📉 Low win rate ({win_rate:.0f}%) → Reduced profit targets")
                            should_update_config = True
                    
                    # 根據波動率調整
                    if atr_pct > 0.1:
                        # 高波動,可以提高目標
                        progressive = profit_config.get('dynamic_profit_taking', {}).get('progressive_targets', {})
                        high_stage = progressive.get('stages', {}).get('high', {})
                        if high_stage.get('max_target', 6.0) < 8.0:
                            high_stage['max_target'] = 8.0
                            profit_adjustments.append(f"⚡ High volatility (ATR: {atr_pct:.4f}%) → Max target 8%")
                            should_update_config = True
                    elif atr_pct < 0.02:
                        # 低波動,降低目標
                        progressive = profit_config.get('dynamic_profit_taking', {}).get('progressive_targets', {})
                        high_stage = progressive.get('stages', {}).get('high', {})
                        if high_stage.get('max_target', 6.0) > 3.0:
                            high_stage['max_target'] = 3.0
                            profit_adjustments.append(f"💤 Low volatility (ATR: {atr_pct:.4f}%) → Max target 3%")
                            should_update_config = True
                    
                    # 儲存更新
                    if should_update_config:
                        profit_config['last_updated'] = datetime.now().isoformat()
                        history = profit_config.get('ai_adjustment_history', [])
                        history.append({
                            "timestamp": datetime.now().isoformat(),
                            "reason": f"Auto-adjustment based on WR={win_rate:.0f}%, ATR={atr_pct:.4f}%",
                            "changes": profit_adjustments
                        })
                        profit_config['ai_adjustment_history'] = history[-20:]  # 保留最近 20 次
                        
                        with open(profit_config_file, 'w') as f:
                            json.dump(profit_config, f, indent=2)
                except Exception as e:
                    print(f"   ⚠️ Failed to adjust profit config: {e}")
            
            # 檢查 1: PnL + 鯨魚反轉
            if pnl_pct < -0.5:
                if whale_direction and whale_direction != bridge['ai_to_wolf']['direction']:
                    warnings.append(f"⚠️ WHALE FLIPPED! Now {whale_direction} (Dom: {whale_dominance:.2f})")
                elif whale_flip_count >= 2:
                    warnings.append(f"⚠️ Whale churning ({whale_flip_count} flips) - possible trap")
                else:
                    warnings.append(f"⚠️ Position underwater: {pnl_pct:.2f}%")
            
            # 檢查 2: 死水盤警告
            if is_dead_market and atr_pct < 0.01:
                warnings.append(f"💤 Dead market (ATR: {atr_pct:.4f}%) - low win probability")
            
            # 檢查 3: 極端風險
            if liquidation_pressure > 70:
                warnings.append(f"🔴 High liquidation risk: {liquidation_pressure}/100")
            
            if whale_trap_prob > 0.6:
                warnings.append(f"🪤 Whale trap probability: {whale_trap_prob:.0%}")
            
            # 檢查 4: 預測準確度低
            if direction_accuracy < 40 and feedback_loop.get('total_trades', 0) >= 5:
                warnings.append(f"📉 Low prediction accuracy: {direction_accuracy:.0f}%")
            
            # 檢查 5: 獲利 + 環境確認
            if pnl_pct > 1.0:
                if whale_direction == bridge['ai_to_wolf']['direction']:
                    warnings.append(f"✅ Profitable + Whale aligned ({whale_dominance:.2f} dom) - hold/add")
                else:
                    warnings.append(f"⚠️ Profitable but whale diverging - consider partial exit")
            
            # 輸出所有警告
            if profit_adjustments:
                print(f"   🎯 Profit Config Auto-Adjustments:")
                for adj in profit_adjustments:
                    print(f"      {adj}")
            
            for warning in warnings:
                print(f"   {warning}")
            
            # 輸出關鍵市場指標
            print(f"   📊 Market: {regime} | ATR: {atr_pct:.4f}% | OBI: {obi:.2f} | VPIN: {vpin:.2f} | Fund: {funding_rate:.4f}")
        
        # 更新市場記憶 (Bias) - 帶防抖動
        suggested_bias = result.get('strategic_bias') or 'NEUTRAL'
        current_bias = market_memory["strategic_bias"]["direction"]
        
        if suggested_bias != current_bias:
            pending = market_memory["strategic_bias"].get("pending_change")
            if pending and pending["direction"] == suggested_bias:
                pending["count"] += 1
                if pending["count"] >= 3:
                    market_memory["strategic_bias"]["direction"] = suggested_bias
                    market_memory["strategic_bias"]["since"] = datetime.now().isoformat()
                    market_memory["strategic_bias"]["pending_change"] = None
                    print(f"   🔄 [Bias Flip] Confirmed change to {suggested_bias}")
                else:
                    print(f"   ⏳ [Bias Stability] Potential flip to {suggested_bias} detected ({pending['count']}/3)... Holding {current_bias}")
            else:
                market_memory["strategic_bias"]["pending_change"] = {
                    "direction": suggested_bias,
                    "count": 1,
                    "last_check": datetime.now().isoformat()
                }
                print(f"   ⏳ [Bias Stability] Potential flip to {suggested_bias} detected (1/3)... Holding {current_bias}")
                new_state["strategic_bias"] = current_bias # 強制保持
        else:
             if market_memory["strategic_bias"].get("pending_change"):
                market_memory["strategic_bias"]["pending_change"] = None
        
        save_market_memory(market_memory)
            
        # 處理參數更新
        if 'parameter_updates' in result and result['parameter_updates']:
            updates = result['parameter_updates']
            print(f"   ⚙️ [Auto-Tuning] Commander suggested updates: {updates}")
            # 更新 team_config
            for k, v in updates.items():
                if k in team_config['dynamic_parameters']:
                    team_config['dynamic_parameters'][k] = v
            team_config['recent_adjustments'].append({
                "time": datetime.now().isoformat(),
                "updates": updates,
                "reason": "Commander Decision"
            })
            save_team_config(team_config)
            
        # 格式化輸出 (GPT-5 單次決策，不需要多 Agent 辯論)
        analysis_preview = (result.get('analysis') or "No analysis")[:150]
        
        return f"[{result.get('strategic_bias')} | {result.get('tactical_action')} (Lev x{result.get('recommended_leverage', 1)})] {analysis_preview}"

    except Exception as e:
        return f"❌ Commander failed: {e}"

def analyze_with_ai(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state):
    # 為了兼容舊代碼接口，這裡直接轉發給 run_council_meeting
    return run_council_meeting(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state)


def parse_args():
    """解析命令列參數"""
    parser = argparse.ArgumentParser(description='AI Trading Advisor - GPT Version')
    parser.add_argument('hours', nargs='?', type=float, default=0,
                        help='運行時間（小時），0 表示無限運行')
    parser.add_argument('--interval', type=int, default=20,
                        help='分析間隔（秒），預設 20 秒')
    return parser.parse_args()


def main():
    args = parse_args()
    
    # 🆕 啟用終端機日誌記錄
    logger = setup_terminal_logging()
    
    # 計算結束時間
    start_time = datetime.now()
    end_time = None
    if args.hours > 0:
        end_time = start_time + timedelta(hours=args.hours)
        print(f"⏰ 將在 {args.hours} 小時後自動停止 ({end_time.strftime('%Y-%m-%d %H:%M:%S')})")
    
    print("="*60)
    print("🤖 AI Whale Hunter (Trap Master Mode) - GPT Version")
    print("="*60)
    if end_time:
        print(f"📅 開始時間: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📅 結束時間: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    while True:
        try:
            # 檢查是否超時
            if end_time and datetime.now() >= end_time:
                elapsed = datetime.now() - start_time
                print(f"\n⏰ 運行時間已達 {elapsed.total_seconds()/3600:.2f} 小時，自動停止")
                print(f"🛑 AI Advisor GPT 已停止 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
                break
            
            # 1. 載入數據
            session_path = find_latest_pt_session()
            if not session_path:
                print("❌ No session found.")
                time.sleep(60)
                continue
                
            trading_data = load_trading_data(session_path)
            market_snapshot = load_market_snapshot()
            signals_df = load_signal_diagnostics(session_path)
            whale_flip_df = load_whale_flip_analysis(session_path)
            prev_state = load_advisor_state()
            
            # 2. AI 分析
            current_time = datetime.now().strftime('%H:%M:%S')
            remaining = ""
            if end_time:
                remaining_seconds = (end_time - datetime.now()).total_seconds()
                remaining_hours = remaining_seconds / 3600
                remaining = f" | 剩餘 {remaining_hours:.1f}h"
            
            print(f"\n[{current_time}] 🔍 Analyzing Session: {session_path.name}{remaining}")
            analysis = analyze_with_ai(trading_data, market_snapshot, signals_df, whale_flip_df, prev_state)
            
            print("\n" + analysis)
            print("\n" + "-"*60)
            print(f"💤 Observing fluctuations... (Next check in {args.interval}s)")
            
            time.sleep(args.interval)
            
        except KeyboardInterrupt:
            elapsed = datetime.now() - start_time
            print(f"\n🛑 AI Advisor GPT Stopped. (運行時間: {elapsed.total_seconds()/3600:.2f} 小時)")
            break
        except Exception as e:
            print(f"⚠️ Error: {e}")
            time.sleep(60)
    
    # 🆕 關閉日誌記錄
    close_terminal_logging()

if __name__ == "__main__":
    main()

