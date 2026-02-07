#!/usr/bin/env python3
"""
Whale Testnet Trader v14.9.6
============================
🆕 v14.9.6: 修復止損方向錯誤問題 (止損 < 手續費 ROE 時)

修復內容：
- 當止損 ROE% < 手續費 ROE% 時，止損價格會在「止盈方向」
- 例: stop=0.75%, fee_ROE=3.75% → LONG 止損價高於進場（錯誤！）
- 修復: 自動調整止損為 fee_ROE + 0.5%，確保止損方向正確

功能：
1. dYdX WebSocket + REST API 即時數據接收
2. 主力策略分析 (六維評分系統)
3. dYdX 真實交易 (含 Maker/Taker 手續費)
4. 即時 JSON 記錄 (供 TensorFlow 訓練)
5. 損益追蹤與統計
6. N%鎖N% 動態止盈止損

數據源：
- 價格: dYdX Indexer Oracle Price
- 訂單簿: dYdX WebSocket (實時)
- 交易: dYdX REST API (每秒輪詢)

交易執行：
- Paper Mode: 模擬交易 (使用 dYdX 價格)
- Real Mode: dYdX 真實交易 (含手續費)

Author: AI Assistant
Date: 2025-12-09
Version: v12.9 - dYdX 數據源 (取代 Binance)
"""

import os
import sys
import json
import time
import asyncio
import websockets
import threading
import subprocess
import signal
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, asdict, field
from collections import deque
from enum import Enum
import logging
import re

# 🆕 抑制第三方庫的 HTTP 請求日誌 (避免刷屏)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("dydx_v4_client").setLevel(logging.WARNING)


# 添加專案路徑
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src" / "strategy"))

# 🆕 dYdX Integration
try:
    from dydx.dydx_trader import DydxTrader
    DYDX_AVAILABLE = True
except ImportError:
    DYDX_AVAILABLE = False
    print("⚠️ dYdX module not found or failed to import")

# 🆕 v13.5 Trading Card Manager (完整參數卡片系統)
try:
    from trading_card_manager import TradingCardManager
    CARD_MANAGER_AVAILABLE = True
except ImportError:
    try:
        from scripts.trading_card_manager import TradingCardManager
        CARD_MANAGER_AVAILABLE = True
    except ImportError:
        CARD_MANAGER_AVAILABLE = False
        TradingCardManager = None
        print("ℹ️ TradingCardManager not available (optional)")

# 🆕 dYdX Whale Trader Integration (Aggressive Maker)
try:
    from dydx_whale_trader import DydxAPI, DydxConfig as DydxTradingConfig
    DYDX_WHALE_AVAILABLE = True
except ImportError:
    try:
        from scripts.dydx_whale_trader import DydxAPI, DydxConfig as DydxTradingConfig
        DYDX_WHALE_AVAILABLE = True
    except ImportError:
        DYDX_WHALE_AVAILABLE = False
        DydxAPI = None
        DydxTradingConfig = None
        print("ℹ️ dYdX Whale Trader (Aggressive Maker) not available")

import ccxt

# 嘗試導入偵測器
try:
    from whale_strategy_detector_v4 import WhaleStrategyDetectorV4
    DETECTOR_AVAILABLE = True
except ImportError:
    DETECTOR_AVAILABLE = False
    print("⚠️ WhaleStrategyDetectorV4 not available")

# 嘗試導入信號橋接器 (用於真實交易系統)
try:
    from whale_signal_bridge import WhaleSignalBridge, SignalAction
    SIGNAL_BRIDGE_AVAILABLE = True
except ImportError:
    try:
        from scripts.whale_signal_bridge import WhaleSignalBridge, SignalAction
        SIGNAL_BRIDGE_AVAILABLE = True
    except ImportError:
        SIGNAL_BRIDGE_AVAILABLE = False
        print("ℹ️ WhaleSignalBridge not available (optional)")

# 嘗試導入多時間框架分析器
try:
    from multi_timeframe_analyzer import MultiTimeframeAnalyzer, MTFSnapshot, TimeframeSignal
    MTF_AVAILABLE = True
except ImportError:
    try:
        from scripts.multi_timeframe_analyzer import MultiTimeframeAnalyzer, MTFSnapshot, TimeframeSignal
        MTF_AVAILABLE = True
    except ImportError:
        MTF_AVAILABLE = False
        TimeframeSignal = None  # Fallback
        print("ℹ️ MultiTimeframeAnalyzer not available (optional)")

# 🆕 v13.6: 統一回測數據收集器
try:
    from backtest_data_collector import BacktestDataCollector
    BACKTEST_COLLECTOR_AVAILABLE = True
except ImportError:
    try:
        from scripts.backtest_data_collector import BacktestDataCollector
        BACKTEST_COLLECTOR_AVAILABLE = True
    except ImportError:
        BACKTEST_COLLECTOR_AVAILABLE = False
        BacktestDataCollector = None
        print("ℹ️ BacktestDataCollector not available (optional)")

# 🆕 v13.7: 自動回測模組 (虧損觸發回測)
try:
    from auto_backtest_module import (
        AutoBacktestModule, 
        BacktestTriggerConfig, 
        TradingMode,
        TradeRecord as BacktestTradeRecord,
        create_auto_backtest_module
    )
    AUTO_BACKTEST_AVAILABLE = True
except ImportError:
    try:
        from scripts.auto_backtest_module import (
            AutoBacktestModule,
            BacktestTriggerConfig,
            TradingMode,
            TradeRecord as BacktestTradeRecord,
            create_auto_backtest_module
        )
        AUTO_BACKTEST_AVAILABLE = True
    except ImportError:
        AUTO_BACKTEST_AVAILABLE = False
        AutoBacktestModule = None
        BacktestTriggerConfig = None
        TradingMode = None
        BacktestTradeRecord = None
        create_auto_backtest_module = None
        print("ℹ️ AutoBacktestModule not available (optional)")

# 🆕 v13.8: 追單保護模組
try:
    from chase_protection import (
        ChaseProtectionModule,
        ChaseProtectionConfig,
        create_chase_protection
    )
    CHASE_PROTECTION_AVAILABLE = True
except ImportError:
    try:
        from scripts.chase_protection import (
            ChaseProtectionModule,
            ChaseProtectionConfig,
            create_chase_protection
        )
        CHASE_PROTECTION_AVAILABLE = True
    except ImportError:
        CHASE_PROTECTION_AVAILABLE = False
        ChaseProtectionModule = None
        ChaseProtectionConfig = None
        create_chase_protection = None
        print("ℹ️ ChaseProtectionModule not available (optional)")

# 🆕 v13.9: 早期逃命偵測器 (微利後反轉止損優化)
try:
    from early_exit_detector import (
        EarlyExitDetector,
        EarlyExitConfig,
        EarlyExitReason,
        create_early_exit_detector
    )
    EARLY_EXIT_AVAILABLE = True
except ImportError:
    try:
        from scripts.early_exit_detector import (
            EarlyExitDetector,
            EarlyExitConfig,
            EarlyExitReason,
            create_early_exit_detector
        )
        EARLY_EXIT_AVAILABLE = True
    except ImportError:
        EARLY_EXIT_AVAILABLE = False
        EarlyExitDetector = None
        EarlyExitConfig = None
        EarlyExitReason = None
        create_early_exit_detector = None
        print("ℹ️ EarlyExitDetector not available (optional)")


# ============================================================
# 動態策略配置載入
# ============================================================

def _coerce_float(value: Any, default: float = 0.0) -> float:
    """Best-effort numeric coercion for config values.

    Supports int/float, numeric strings, and strings like "50X (...)".
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)) and len(value) > 0:
        return _coerce_float(value[0], default=default)
    if isinstance(value, str):
        s = value.strip()
        try:
            return float(s)
        except Exception:
            match = re.search(r"[-+]?\d*\.?\d+", s)
            if match:
                try:
                    return float(match.group(0))
                except Exception:
                    return default
    return default


def _coerce_int(value: Any, default: int = 0) -> int:
    return int(_coerce_float(value, default=float(default)))

def _generate_constrained_balanced_sequence(
    batch_size: int,
    *,
    max_streak: int = 3,
    max_imbalance: int = 4,
    rng: Optional["random.Random"] = None,
) -> List[str]:
    """
    Generate a LONG/SHORT sequence with exact 50/50 counts per batch while
    limiting long streaks / imbalance to avoid early one-sided runs.
    """
    import random

    if batch_size < 2 or batch_size % 2 != 0:
        raise ValueError("batch_size must be an even integer >= 2")

    rng = rng or random.Random()

    remaining = {"LONG": batch_size // 2, "SHORT": batch_size // 2}
    counts = {"LONG": 0, "SHORT": 0}
    seq: List[str] = []

    last_dir: Optional[str] = None
    streak_len = 0

    def _allowed(direction: str) -> bool:
        if remaining[direction] <= 0:
            return False
        if max_streak and last_dir == direction and streak_len >= max_streak:
            return False
        if max_imbalance and max_imbalance > 0:
            nl = counts["LONG"] + (1 if direction == "LONG" else 0)
            ns = counts["SHORT"] + (1 if direction == "SHORT" else 0)
            if abs(nl - ns) > max_imbalance:
                return False
        return True

    for _ in range(batch_size):
        candidates = [d for d in ("LONG", "SHORT") if _allowed(d)]
        if not candidates:
            candidates = [d for d in ("LONG", "SHORT") if remaining[d] > 0]
        if not candidates:
            break

        weights = [remaining[d] for d in candidates]
        pick = rng.choices(candidates, weights=weights, k=1)[0]

        seq.append(pick)
        remaining[pick] -= 1
        counts[pick] += 1

        if pick == last_dir:
            streak_len += 1
        else:
            last_dir = pick
            streak_len = 1

    return seq

def _fee_leverage_multiplier(config: Any, leverage: float) -> float:
    """Return the leverage multiplier for fee-to-ROE conversions."""
    try:
        if getattr(config, 'fee_apply_leverage', True) is False:
            return 1.0
    except Exception:
        pass
    return leverage

def load_trading_strategy() -> Dict:
    """載入動態交易策略配置"""
    strategy_file = Path("config/whale_trading_strategy.json")
    if strategy_file.exists():
        with open(strategy_file) as f:
            return json.load(f)
    return {}


# ============================================================
# 🔊 聲音通知系統 (macOS)
# ============================================================

def play_sound(sound_type: str):
    """
    播放交易通知音效 (macOS)
    
    Args:
        sound_type: 'long_entry', 'short_entry', 'profit_exit', 'loss_exit'
    """
    try:
        # 使用 macOS 內建音效
        sounds = {
            'long_entry': '/System/Library/Sounds/Submarine.aiff',  # 進場統一用潛水艇聲
            'short_entry': '/System/Library/Sounds/Submarine.aiff', # 進場統一用潛水艇聲
            'profit_exit': '/System/Library/Sounds/Glass.aiff',     # 獲利平倉用清脆玻璃聲
            'loss_exit': '/System/Library/Sounds/Basso.aiff',       # 虧損平倉 - 低沉警告
        }
        
        sound_file = sounds.get(sound_type)
        if sound_file and os.path.exists(sound_file):
            # 背景播放，不阻塞主程序
            subprocess.Popen(
                ['afplay', sound_file],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        # 不再使用語音備用方案
    except Exception as e:
        pass  # 靜默失敗，不影響交易

def save_trading_strategy(strategy: Dict):
    """保存交易策略配置 (供 AI 優化)"""
    strategy_file = Path("config/whale_trading_strategy.json")
    strategy['meta']['last_updated'] = datetime.now().isoformat()
    with open(strategy_file, 'w') as f:
        json.dump(strategy, f, indent=2, ensure_ascii=False)


# 🆕 v10.3 動態策略配置載入
_ctx_strategy_config: Dict = {}
_ctx_strategy_config_mtime: float = 0

def load_ctx_strategy_config(force_reload: bool = False) -> Dict:
    """
    載入專屬獲利模式策略配置 (支援熱更新)
    配置檔: config/whale_ctx_strategy.json
    """
    global _ctx_strategy_config, _ctx_strategy_config_mtime
    
    config_path = Path("config/whale_ctx_strategy.json")
    
    if not config_path.exists():
        return {}
    
    # 檢查是否需要重新載入 (檔案修改時間變化)
    try:
        current_mtime = config_path.stat().st_mtime
        if not force_reload and current_mtime == _ctx_strategy_config_mtime and _ctx_strategy_config:
            return _ctx_strategy_config
        
        with open(config_path, 'r', encoding='utf-8') as f:
            _ctx_strategy_config = json.load(f)
        _ctx_strategy_config_mtime = current_mtime
        
        version = _ctx_strategy_config.get('_version', 'unknown')
        print(f"📋 已載入策略配置 {config_path.name} (版本: {version})")
        
        return _ctx_strategy_config
    except Exception as e:
        print(f"⚠️ 載入策略配置失敗: {e}")
        return {}


# ============================================================
# 配置
# ============================================================

@dataclass
class TradingConfig:
    """
    🎴 交易配置 (v13.6 - 卡片系統)
    
    所有參數皆從 config/trading_cards/*.json 載入
    此 dataclass 僅定義型別，不設預設值
    
    使用方式:
        config = TradingConfig.from_card()           # 載入 master_config.json 指定的卡片
        config = TradingConfig.from_card("scalp")    # 載入指定卡片
        
    參數文檔: docs/TRADING_CONFIG_CHANGELOG.md
    """
    # === 卡片識別 ===
    card_id: str = None
    card_name: str = None

    # === 基本設定 ===
    symbol: str = None
    leverage: int = None
    leverage_min: int = None
    leverage_max: int = None
    position_size_usdt: float = None
    
    # === 測試與驗證 ===
    auto_confirm: bool = None
    base_balance_deduct: float = None
    verify_real_pnl: bool = None
    zero_budget_stop_enabled: bool = None
    zero_budget_stop_epsilon_usdt: float = None
    
    # === 交易模式 ===
    paper_mode: bool = None
    paper_price_source: str = None  # "dydx" / "binance"
    dydx_price_source: str = None  # "api" / "ws" (預設使用 api)
    use_testnet: bool = None
    reverse_mode: bool = None
    mtf_first_mode: bool = None
    contextual_mode: bool = None
    random_entry_mode: bool = None  # 🎲 隨機進場實驗模式
    random_entry_pure: bool = None  # 🎲 純隨機進場 (繞過所有進場過濾)
    random_entry_balance_enabled: bool = None  # 🎲 是否強制 50/50 平衡
    random_entry_balance_batch_size: int = None  # 🎲 平衡批次大小 (預設 20)
    random_entry_balance_prefill_size: int = None  # 🎲 預先產生方向數 (預設 30)
    random_entry_balance_max_streak: int = None  # 🎲 最大連續同向 (預設 3)
    random_entry_balance_max_imbalance: int = None  # 🎲 批次內最大多空差 (預設 4)
    random_entry_balance_trend_bias_enabled: bool = None  # 🎲 是否允許趨勢偏向 (預設 false)
    entry_veto_enabled: bool = None  # 🛡️ 是否啟用進場 Veto 過濾
    taker_on_emergency_only: bool = None  # 只在緊急平倉時使用 taker 手續費
    fee_apply_leverage: bool = None  # 手續費轉 ROE 是否乘槓桿
    
    # === 策略卡片系統 ===
    strategy_card_enabled: bool = None
    default_entry_card: str = None
    default_exit_card: str = None
    default_risk_card: str = None
    auto_card_switch: bool = None

    # === 自動回測整合 (v5) ===
    auto_backtest_integration: Dict[str, Any] = None
    
    # === dYdX 同步 ===
    dydx_sync_mode: bool = None
    dydx_btc_size: float = None
    dydx_ws_confirm_timeout_sec: float = None
    dydx_desync_close_sec: float = None
    dydx_sl_wait_timeout_sec: float = None
    dydx_sl_sync_grace_sec: float = None
    dydx_sl_missing_grace_sec: float = None
    dydx_sl_missing_min_interval_sec: float = None
    dydx_paper_master: bool = None
    dydx_resync_open_cooldown_sec: float = None
    dydx_maker_timeout_sec: float = None
    dydx_use_reference_entry_price: bool = None
    dydx_use_reference_exit_price: bool = None
    
    # === 預掛單模式 ===
    pre_entry_mode: bool = None
    pre_entry_threshold: float = None
    pre_entry_cancel_threshold: float = None
    pre_entry_timeout_sec: float = None
    pre_entry_price_offset: float = None
    pre_take_profit_pct: float = None
    pre_stop_loss_pct: float = None
    
    # === 階段性鎖利 ===
    profit_lock_stages: list = None
    
    # === N%鎖N% 策略 ===
    use_n_lock_n: bool = None
    n_lock_n_threshold: float = None
    n_lock_n_buffer: float = None
    
    # === 🆕 v3 中間數鎖利 ===
    use_midpoint_lock: bool = None
    midpoint_ratio: float = None  # 預設 0.5 = 最高獲利的一半
    lock_start_pct: float = None
    min_lock_pct: float = None
    sl_update_cooldown_sec: float = None
    sl_update_min_diff_pct: float = None

    # === TP 更新策略 ===
    tp_update_on_phase_change: bool = None
    tp_update_on_integer_cross: bool = None
    tp_update_integer_step: float = None
    tp_update_integer_offset: float = None
    tp_update_cooldown_sec: float = None
    tp_update_min_price_diff_pct: float = None
    tp_update_policy: str = None  # "extend" / "tighten"

    
    def __post_init__(self):
        """初始化後處理 - 轉換 profit_lock_stages 格式"""
        if self.profit_lock_stages is not None:
            # 確保是 tuple 格式
            self.profit_lock_stages = [
                tuple(s) if isinstance(s, list) else s 
                for s in self.profit_lock_stages
            ]
        if self.pre_stop_loss_pct is None and self.stop_loss_pct is not None:
            self.pre_stop_loss_pct = self.stop_loss_pct
        if self.pre_take_profit_pct is None and self.target_profit_pct is not None:
            self.pre_take_profit_pct = self.target_profit_pct
    
    @classmethod
    def from_card(cls, card_id: str = None, card_manager: 'TradingCardManager' = None) -> 'TradingConfig':
        """
        🎴 從交易卡片創建 TradingConfig
        
        Args:
            card_id: 卡片 ID (如 'scalp_aggressive', 'trending_bull')
                    如果為 None，使用 master_config.json 中的 active_card
            card_manager: 可選的 TradingCardManager 實例
        
        Returns:
            使用卡片參數初始化的 TradingConfig
            
        Example:
            # 使用當前啟用的卡片
            config = TradingConfig.from_card()
            
            # 使用指定卡片
            config = TradingConfig.from_card("high_volatility")
        """
        if not CARD_MANAGER_AVAILABLE:
            raise RuntimeError("❌ TradingCardManager 不可用，無法載入配置！請確認 scripts/trading_card_manager.py 存在")
        
        try:
            # 創建或使用傳入的 card_manager
            if card_manager is None:
                card_manager = TradingCardManager()
            
            # 如果指定了 card_id，切換到該卡片
            if card_id:
                card_manager.switch_card(card_id, "from_card() 指定")
            
            # 獲取卡片的展平參數
            card_params = card_manager.get_config_dict()
            
            if not card_params:
                raise ValueError(f"❌ 卡片參數為空，無法創建配置")
            
            # 創建 TradingConfig，只使用 TradingConfig 有的欄位
            # 獲取 TradingConfig 的所有欄位名
            from dataclasses import fields
            valid_fields = {f.name for f in fields(cls)}
            
            # 過濾掉 TradingConfig 沒有的欄位
            filtered_params = {k: v for k, v in card_params.items() if k in valid_fields}
            
            # 特殊處理 profit_lock_stages (需要轉換格式)
            if 'profit_lock_stages' in filtered_params:
                stages = filtered_params['profit_lock_stages']
                if isinstance(stages, list) and stages:
                    # 確保是 tuple 格式
                    filtered_params['profit_lock_stages'] = [tuple(s) if isinstance(s, list) else s for s in stages]
            
            active_card = card_manager.get_active_card()
            if active_card:
                if 'card_id' in valid_fields:
                    filtered_params['card_id'] = active_card.meta.card_id
                if 'card_name' in valid_fields:
                    filtered_params['card_name'] = active_card.meta.card_name

            # 創建配置
            config = cls(**filtered_params)

            if active_card:
                print(f"🎴 已載入卡片: {active_card.meta.card_name}")
                print(f"   📊 市場情況: {active_card.meta.market_condition}")
                print(f"   ⚠️ 風險等級: {active_card.meta.risk_level}")
                print(f"   🎯 預期勝率: {active_card.meta.expected_win_rate:.0%}")
            
            return config
            
        except Exception as e:
            raise RuntimeError(f"❌ 從卡片載入失敗: {e}") from e
    
    # === 專屬獲利模式配置 ===
    ctx_obi_very_high: float = None
    ctx_obi_high_threshold: float = None
    ctx_obi_mode_a_min: float = None
    ctx_obi_neutral_min: float = None
    ctx_obi_neutral_max: float = None
    ctx_obi_low_threshold: float = None
    ctx_price_drop_min: float = None
    ctx_price_drop_max: float = None
    ctx_price_stable_pct: float = None
    ctx_prob_mode_c_min: float = None
    ctx_prob_mode_c_max: float = None
    ctx_prob_low_threshold: float = None
    ctx_prob_danger_threshold: float = None
    ctx_enable_smart_reverse: bool = None
    ctx_strategy_config_path: str = None
    
    # === 分析頻率 ===
    ws_interval_sec: float = None
    analysis_interval_sec: float = None
    min_trade_interval_sec: float = None
    
    # === MTF 策略配置 ===
    mtf_hold_minutes: float = None
    mtf_min_rsi_long: float = None
    mtf_max_rsi_long: float = None
    mtf_min_rsi_short: float = None
    mtf_max_rsi_short: float = None
    mtf_alignment_threshold: float = None
    mtf_emergency_stop_pct: float = None
    
    # === 目標與止損 ===
    target_profit_pct: float = None
    target_profit_min_pct: float = None
    target_profit_max_pct: float = None
    target_net_profit_pct: float = None
    stop_loss_pct: float = None
    stop_loss_min_pct: float = None
    stop_loss_max_pct: float = None
    early_stop_grace_sec: float = None
    noise_stop_sigma: float = None
    max_hold_minutes: float = None
    max_hold_min_minutes: float = None
    max_hold_max_minutes: float = None
    
    # === 手續費 ===
    maker_fee_pct: float = None
    taker_fee_pct: float = None
    use_maker_simulation: bool = None
    
    # === Maker 分批進場 ===
    maker_entry_batches: int = None
    maker_entry_duration_sec: float = None
    maker_price_offset_pct: float = None
    
    # === 風控 ===
    max_daily_trades: int = None
    max_daily_loss_usdt: float = None
    max_concurrent_positions: int = None
    
    # === 信號穩定性 ===
    min_probability: float = None
    min_confidence: float = None
    signal_confirm_seconds: int = None
    min_signal_advantage: float = None
    
    # === 雙週期策略 ===
    fast_window_seconds: int = None
    medium_window_seconds: int = None
    slow_window_seconds: int = None
    
    # === 六維信號系統 ===
    six_dim_enabled: bool = None
    six_dim_alignment_threshold: int = None
    six_dim_min_score_to_trade: int = None
    six_dim_min_score_long: int = None      # v14.12: LONG專用門檻
    six_dim_min_score_short: int = None     # v14.12: SHORT專用門檻
    six_dim_high_confidence: int = None     # v14.12: 高信心分數
    
    # === 方向過濾 (v13.6) ===
    allowed_directions: list = None  # ['LONG', 'SHORT'] 或 ['SHORT'] 等
    
    # === v5 自適應進化系統 (auto_optimize) ===
    auto_optimize_enabled: bool = None          # 是否啟用 auto_optimize (只有 v5 卡片為 True)
    auto_optimize_config_dir: str = None        # auto_optimized 配置目錄
    auto_optimize_config_pattern: str = None    # auto_optimized 配置檔案模式
    auto_optimize_use_latest: bool = None       # 是否使用最新配置
    auto_optimize_fallback_to_default: bool = None  # 找不到配置時使用預設
    auto_optimize_reload_interval_sec: int = None   # 重新載入間隔
    
    # === Warm-up 與動能確認 ===
    warmup_seconds: float = None
    require_momentum_confirm: bool = None
    
    # === 價格方向確認 ===
    price_confirm_enabled: bool = None
    price_confirm_threshold: float = None
    
    # === 急漲急跌快速進場 (v14.14) ===
    spike_fast_entry_enabled: bool = None
    spike_fast_entry_threshold: float = None
    spike_fast_entry_window_sec: int = None
    spike_auto_adjust: bool = None
    
    # === OBI 線配置 ===
    obi_line_weight: int = None
    obi_long_threshold: float = None
    obi_short_threshold: float = None
    obi_strong_threshold: float = None
    
    # === 動能線配置 ===
    momentum_line_weight: int = None
    momentum_long_threshold: float = None
    momentum_short_threshold: float = None
    momentum_strong_threshold: float = None
    
    # === 成交量線配置 ===
    volume_line_weight: int = None
    volume_long_threshold: float = None
    volume_short_threshold: float = None
    volume_strong_threshold: float = None
    
    # === 策略 Hysteresis ===
    strategy_confirm_count: int = None
    strategy_lead_threshold: float = None
    
    # === 無明顯主力狀態 ===
    min_dominant_prob: float = None
    min_lead_gap: float = None
    
    # === 機率門檻 ===
    actionable_prob_threshold: float = None
    
    # === 反轉策略 ===
    reversal_mode_enabled: bool = None
    reversal_config_path: str = None
    reversal_price_threshold: float = None
    reversal_obi_threshold: float = None
    consecutive_losses_to_switch: int = None
    
    # === 連續虧損冷卻 ===
    max_consecutive_losses: int = None
    consecutive_loss_cooldown_min: int = None
    obi_consistency_check: bool = None
    consecutive_wins_to_restore: int = None
    
    # === 鯨魚偵測 ===
    whale_alert_threshold_usdt: float = None
    whale_emergency_threshold_usdt: float = None
    
    # === 價格異動偵測 ===
    price_spike_enabled: bool = None
    price_spike_threshold_pct: float = None
    price_spike_window_sec: float = None
    price_spike_alert_cooldown: float = None
    whale_cascade_count: int = None
    whale_cascade_window_sec: int = None

    # === 交易所價差 ===
    max_exchange_spread_pct: float = None
    max_dydx_spread_pct: float = None
    max_dydx_jump_1s_pct: float = None
    binance_sentiment_enabled: bool = None
    binance_obi_threshold: float = None
    
    # === 動態止盈系統 ===
    dynamic_profit_enabled: bool = None
    dynamic_profit_config_path: str = None
    smart_exit_gross_target: float = None
    smart_exit_net_target: float = None
    quick_profit_time_limit: float = None
    
    # === 無動能快速止損 ===
    no_momentum_enabled: bool = None
    no_momentum_check_after_min: float = None
    no_momentum_min_profit: float = None
    no_momentum_loss_trigger: float = None
    
    # === 兩階段止盈止損 ===
    two_phase_exit_enabled: bool = None
    phase1_fee_threshold_pct: float = None
    phase1_strict_stop_loss_pct: float = None
    phase1_target_pct: float = None
    phase2_trailing_start_pct: float = None
    phase2_trailing_offset_pct: float = None
    phase2_max_target_pct: float = None
    historical_avg_win_pct: float = None
    historical_avg_loss_pct: float = None
    
    # === 🆕 v4.1 dYdX 環境模擬 ===
    dydx_simulation: Dict = None  # {enabled, entry_slippage_pct, exit_slippage_pct, min_hold_seconds, ...}


# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 v10.9.2 動態兩階段止盈止損管理器 (JSON 配置版)
# ═══════════════════════════════════════════════════════════════════════════════

class TwoPhaseExitManager:
    """
    動態兩階段止盈止損系統 (v10.9.2 JSON配置版)
    
    🆕 v10.9.2 新功能:
    1. 所有參數從 JSON 配置檔讀取
    2. 啟動時自動分析歷史交易記錄，計算平均獲利%
    3. 動態更新配置檔
    
    配置檔: config/two_phase_exit_config.json
    """
    
    CONFIG_FILE = Path("config/two_phase_exit_config.json")
    TRADES_DIR = Path("logs/whale_paper_trader")
    
    def __init__(self, config: 'TradingConfig', logger=None):
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # 載入 JSON 配置
        self.json_config = self._load_json_config()
        
        # 🆕 啟動時分析歷史交易記錄
        self._analyze_historical_trades()
        
        # 從 JSON 載入參數
        self._apply_config()
        
        # 🆕 市場條件緩存
        self.market_condition = {
            'quality': 'NORMAL',
            'score': 50,
            'volatility': 0,
            'trend_strength': 0,
            'last_update': 0
        }
        
        # 運行時狀態
        self.trade_stats = {
            'wins': [],
            'losses': [],
            'phase1_exits': 0,
            'phase2_exits': 0,
            'strict_sl_triggered': 0
        }
    
    def _load_json_config(self) -> Dict:
        """載入 JSON 配置檔"""
        if self.CONFIG_FILE.exists():
            try:
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.logger.info(f"✅ 載入兩階段配置: {self.CONFIG_FILE}")
                    return config
            except Exception as e:
                self.logger.warning(f"載入配置失敗: {e}，使用預設值")
        return self._get_default_config()
    
    def _get_default_config(self) -> Dict:
        """預設配置"""
        return {
            "enabled": True,
            "phase1": {
                "fee_threshold_pct": 4.0,
                "base_stop_loss_pct": 4.0,
                "base_target_pct": 4.0
            },
            "phase2": {
                "trailing_start_pct": 4.0,
                "base_trailing_offset_pct": 1.5,
                "base_max_target_pct": 6.0
            },
            "market_quality": {
                "good_threshold": 65,
                "bad_threshold": 35
            },
            "dynamic_adjustment": {
                "good": {"tp_multiplier": 1.3, "risk_ratio": 1.0},
                "normal": {"tp_multiplier": 1.0, "risk_ratio": 0.9},
                "bad": {"tp_multiplier": 0.85, "risk_ratio": 0.75}
            },
            "historical_stats": {
                "avg_win_pct": 3.76,
                "avg_loss_pct": 9.59
            }
        }
    
    def _apply_config(self):
        """套用 JSON 配置"""
        cfg = self.json_config
        
        self.enabled = cfg.get('enabled', True)
        
        # Phase 1
        p1 = cfg.get('phase1', {})
        self.fee_threshold = p1.get('fee_threshold_pct', 4.0)
        self.base_phase1_stop_loss = p1.get('base_stop_loss_pct', 4.0)
        self.base_phase1_target = p1.get('base_target_pct', 4.0)
        
        # Phase 2
        p2 = cfg.get('phase2', {})
        self.phase2_trailing_start = p2.get('trailing_start_pct', 4.0)
        self.base_phase2_trailing_offset = p2.get('base_trailing_offset_pct', 1.5)
        self.base_phase2_max_target = p2.get('base_max_target_pct', 6.0)
        
        # Market Quality
        mq = cfg.get('market_quality', {})
        self.good_threshold = mq.get('good_threshold', 65)
        self.bad_threshold = mq.get('bad_threshold', 35)
        
        # Dynamic Adjustment
        self.dynamic_adj = cfg.get('dynamic_adjustment', {})
        
        # Historical Stats
        stats = cfg.get('historical_stats', {})
        self.historical_avg_win = stats.get('avg_win_pct', 3.76)
        self.historical_avg_loss = stats.get('avg_loss_pct', 9.59)
        self.historical_max_win = stats.get('max_win_pct', 5.05)
        
        self.logger.info(f"📊 兩階段參數: 平均獲利={self.historical_avg_win:.2f}% | "
                        f"止損={self.base_phase1_stop_loss:.1f}% | 目標={self.base_phase1_target:.1f}%")
    
    def _analyze_historical_trades(self):
        """
        🆕 啟動時分析歷史交易記錄
        
        掃描最近 10 個 trades_*.json 檔案，計算：
        - 平均獲利 %
        - 平均虧損 %
        - 最大獲利 %
        - 勝率
        """
        if not self.TRADES_DIR.exists():
            self.logger.info("📁 交易記錄目錄不存在，跳過歷史分析")
            return
        
        # 找所有 trades_*.json 檔案，按時間排序
        trade_files = sorted(
            self.TRADES_DIR.glob("trades_*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True  # 最新的在前
        )[:15]  # 只取最近 15 個檔案
        
        if not trade_files:
            self.logger.info("📁 沒有找到交易記錄檔案")
            return
        
        all_wins = []
        all_losses = []
        analyzed_files = []
        
        for trade_file in trade_files:
            try:
                with open(trade_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    trades = data.get('trades', [])
                    
                    for trade in trades:
                        # 只分析已關閉的交易
                        status = trade.get('status', '')
                        if not status.startswith('CLOSED'):
                            continue
                        
                        net_pnl = trade.get('net_pnl_usdt', 0)
                        pnl_pct = trade.get('pnl_pct', 0)
                        
                        # 計算淨利率 (槓桿後 ROE%，trade.net_pnl_usdt 已含手續費)
                        pos_usdt = trade.get('position_size_usdt', 0) or 0
                        lev = trade.get('actual_leverage', trade.get('leverage', 0)) or 0
                        try:
                            lev = float(lev)
                        except Exception:
                            lev = 0
                        if pos_usdt and lev:
                            net_pnl_pct = (net_pnl / pos_usdt) * lev * 100
                        else:
                            net_pnl_pct = pnl_pct
                        
                        if net_pnl > 0:
                            all_wins.append(net_pnl_pct)
                        elif net_pnl < 0:
                            all_losses.append(abs(net_pnl_pct))
                    
                    analyzed_files.append(trade_file.name)
                    
            except Exception as e:
                self.logger.warning(f"分析 {trade_file.name} 失敗: {e}")
        
        # 計算統計
        if all_wins or all_losses:
            avg_win = sum(all_wins) / len(all_wins) if all_wins else 0
            avg_loss = sum(all_losses) / len(all_losses) if all_losses else 0
            max_win = max(all_wins) if all_wins else 0
            min_win = min(all_wins) if all_wins else 0
            win_count = len(all_wins)
            loss_count = len(all_losses)
            total = win_count + loss_count
            win_rate = win_count / total if total > 0 else 0
            
            self.logger.info(f"📊 歷史交易分析完成:")
            self.logger.info(f"   分析檔案: {len(analyzed_files)} 個")
            self.logger.info(f"   總交易: {total} 筆 | 勝: {win_count} 負: {loss_count}")
            self.logger.info(f"   勝率: {win_rate:.1%}")
            self.logger.info(f"   平均獲利: +{avg_win:.2f}% | 平均虧損: -{avg_loss:.2f}%")
            self.logger.info(f"   最大獲利: +{max_win:.2f}% | 最小獲利: +{min_win:.2f}%")
            
            # 更新 JSON 配置
            if total >= 5:  # 至少 5 筆交易才更新
                self._update_config_stats({
                    'avg_win_pct': round(avg_win, 2),
                    'avg_loss_pct': round(avg_loss, 2),
                    'max_win_pct': round(max_win, 2),
                    'min_win_pct': round(min_win, 2),
                    'win_count': win_count,
                    'loss_count': loss_count,
                    'win_rate': round(win_rate, 3),
                    'total_trades_analyzed': total,
                    'last_analyzed': datetime.now().isoformat(),
                    'analyzed_files': analyzed_files[:5]  # 只記錄最近 5 個
                })
                
                # 更新實例變數
                self.json_config['historical_stats']['avg_win_pct'] = round(avg_win, 2)
                self.json_config['historical_stats']['avg_loss_pct'] = round(avg_loss, 2)
    
    def _update_config_stats(self, stats: Dict):
        """更新 JSON 配置檔的統計數據"""
        try:
            if self.CONFIG_FILE.exists():
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            else:
                config = self._get_default_config()
            
            config['historical_stats'] = stats
            config['_last_updated'] = datetime.now().isoformat()
            config['_auto_updated'] = True
            
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"✅ 已更新配置檔: {self.CONFIG_FILE}")
        except Exception as e:
            self.logger.warning(f"更新配置檔失敗: {e}")
    
    def _load_stats(self):
        """載入歷史交易統計 (已整合到 _analyze_historical_trades)"""
        pass  # 保持向後兼容
    
    def _save_stats(self):
        """保存交易統計"""
        stats_file = Path("logs/two_phase_exit_stats.json")
        stats_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {
                'avg_win_pct': self.historical_avg_win,
                'avg_loss_pct': self.historical_avg_loss,
                'trade_stats': self.trade_stats,
                'last_updated': datetime.now().isoformat()
            }
            with open(stats_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.logger.warning(f"保存兩階段統計失敗: {e}")
    
    def update_market_condition(self, market_data: Dict):
        """
        🆕 更新市場條件評估
        
        Args:
            market_data: 包含 volatility, obi, trend_strength 等
        """
        volatility = market_data.get('volatility_5m', 0)
        obi = abs(market_data.get('obi', 0))
        price_change_1m = abs(market_data.get('price_change_1m', 0))
        price_change_5m = abs(market_data.get('price_change_5m', 0))
        
        # 計算市場品質分數 (0-100)
        score = 50  # 基礎分
        
        # 1. 波動率評估 (低波動 = 好)
        if volatility < 0.3:
            score += 15  # 低波動加分
        elif volatility > 0.8:
            score -= 15  # 高波動扣分
        
        # 2. OBI 強度 (強 OBI = 趨勢明確 = 好)
        if obi > 0.5:
            score += 20  # 強 OBI 加分
        elif obi < 0.2:
            score -= 10  # 弱 OBI 扣分
        
        # 3. 價格動量一致性
        if price_change_1m * price_change_5m > 0:  # 同向
            if abs(price_change_5m) > 0.3:
                score += 15  # 有明確趨勢
        else:  # 反向 = 震盪
            score -= 10
        
        # 限制範圍
        score = max(0, min(100, score))
        
        # 判斷等級
        if score >= 65:
            quality = 'GOOD'
        elif score >= 35:
            quality = 'NORMAL'
        else:
            quality = 'BAD'
        
        self.market_condition = {
            'quality': quality,
            'score': score,
            'volatility': volatility,
            'obi': obi,
            'trend_strength': abs(price_change_5m),
            'last_update': time.time()
        }
    
    def get_dynamic_params(self, net_pnl_pct: float = 0) -> Dict:
        """
        🆕 根據市場條件獲取動態參數 (從 JSON 配置讀取)
        
        核心邏輯：
        1. 止盈以歷史平均為基礎，條件好放寬、條件差收緊
        2. 止損從止盈計算 (確保 R:R 合理)
        3. 階段1根據淨利進度動態調整止損
        
        Args:
            net_pnl_pct: 當前淨利百分比 (用於階段性止損調整)
        
        Returns:
            Dict with adjusted parameters
        """
        quality = self.market_condition.get('quality', 'NORMAL')
        score = self.market_condition.get('score', 50)
        
        # 從 JSON 配置讀取參數
        phase1_cfg = self.json_config.get('phase1', {})
        phase2_cfg = self.json_config.get('phase2', {})
        market_mult = self.json_config.get('market_quality_multipliers', {})
        staged_sl = self.json_config.get('staged_stop_loss_adjustment', {})
        
        # ═══════════════════════════════════════════════════════
        # 🎯 步驟1: 計算動態止盈目標 (以歷史平均為基礎)
        # ═══════════════════════════════════════════════════════
        base_target = self.historical_avg_win  # 歷史平均獲利 (約 3.76%)
        
        # 從 JSON 配置獲取市場乘數
        quality_config = market_mult.get(quality, market_mult.get('NORMAL', {}))
        tp_multiplier = quality_config.get('tp_multiplier', 1.0)
        
        # 動態止盈目標
        min_target = phase1_cfg.get('min_target_pct', 3.0)
        phase1_target = round(max(base_target * tp_multiplier, min_target), 1)
        
        # P2 從 P1 延伸
        phase2_extension = phase2_cfg.get('max_target_pct', 6.0) - phase1_cfg.get('base_target_pct', 3.76)
        phase2_max_target = round(max(phase1_target + phase2_extension, phase2_cfg.get('max_target_pct', 5.0)), 1)
        
        # ═══════════════════════════════════════════════════════
        # 🛡️ 步驟2: 從止盈計算止損 (確保 R:R ≥ 1:1)
        # ═══════════════════════════════════════════════════════
        # 從 JSON 配置獲取風險係數
        risk_ratio = quality_config.get('risk_ratio', 0.9)
        
        # 🔧 v10.19 fix7: 止損必須大於開倉手續費，否則一開就觸發！
        # 100X 槓桿手續費約 4%，止損至少要 5% 才有意義
        entry_fee_pct = phase1_cfg.get('fee_threshold_pct', 4.0)
        min_stop_loss_above_fee = entry_fee_pct + 1.0  # 手續費 + 1% 緩衝
        
        base_stop_loss = round(max(phase1_target * risk_ratio, min_stop_loss_above_fee), 1)
        
        # ═══════════════════════════════════════════════════════
        # 📊 步驟3: 階段性動態止損調整 (根據淨利進度)
        # ═══════════════════════════════════════════════════════
        # 從 JSON 配置讀取閾值
        threshold_1 = staged_sl.get('threshold_1_pct', 1.0)
        threshold_2 = staged_sl.get('threshold_2_pct', 2.0)
        threshold_3 = staged_sl.get('threshold_3_pct', 3.0)
        mult_1 = staged_sl.get('multiplier_below_threshold_1', 1.0)
        mult_2 = staged_sl.get('multiplier_at_threshold_1', 0.75)
        mult_3 = staged_sl.get('multiplier_at_threshold_2', 0.5)
        # 🔧 v10.19 fix7: 最小止損必須大於開倉手續費
        min_sl = staged_sl.get('min_stop_loss_pct', 5.0)  # 從 1.5 改為 5.0
        protect_buffer = staged_sl.get('protect_profit_buffer', 1.0)
        
        if net_pnl_pct >= threshold_3:
            # 已經接近目標，保本為主
            phase1_stop_loss = max(0.5, net_pnl_pct - protect_buffer)
        elif net_pnl_pct >= threshold_2:
            phase1_stop_loss = base_stop_loss * mult_3  # 收緊
        elif net_pnl_pct >= threshold_1:
            phase1_stop_loss = base_stop_loss * mult_2  # 稍微收緊
        else:
            phase1_stop_loss = base_stop_loss * mult_1  # 給予空間
        
        phase1_stop_loss = round(max(phase1_stop_loss, min_sl), 1)
        
        # ═══════════════════════════════════════════════════════
        # 🔒 步驟4: 追蹤止盈參數 (從 JSON 配置)
        # ═══════════════════════════════════════════════════════
        trailing_offset = quality_config.get('trailing_offset', 1.5)
        
        return {
            'phase1_stop_loss': phase1_stop_loss,
            'phase1_target': phase1_target,
            'phase2_trailing_offset': trailing_offset,
            'phase2_max_target': phase2_max_target,
            'quality': quality,
            'score': score,
            'base_target': base_target,      # 歷史平均
            'risk_ratio': risk_ratio,         # 風險係數
            'base_stop_loss': base_stop_loss  # 基礎止損
        }
    
    def get_current_phase(self, net_pnl_pct: float, max_net_pnl_pct: float, 
                          market_data: Dict = None) -> Dict:
        """
        判斷當前所處階段 (動態版)
        
        Args:
            net_pnl_pct: 當前淨利百分比
            max_net_pnl_pct: 最大淨利百分比 (用於追蹤止盈)
            market_data: 市場數據 (用於動態調整)
            
        Returns:
            Dict with phase info
        """
        if not self.enabled:
            return {'phase': 0, 'name': 'DISABLED'}
        
        # 更新市場條件
        if market_data:
            self.update_market_condition(market_data)
        
        # 獲取動態參數 (傳入 net_pnl_pct 用於階段性止損)
        params = self.get_dynamic_params(net_pnl_pct)
        
        if net_pnl_pct < self.fee_threshold:
            # 第一階段：費用突破期
            return {
                'phase': 1,
                'name': '費用突破期',
                'emoji': '🎯',
                'stop_loss_pct': params['phase1_stop_loss'],
                'target_pct': params['phase1_target'],
                'progress_pct': (net_pnl_pct / self.fee_threshold * 100) if self.fee_threshold > 0 else 0,
                'message': f'目標突破 {self.fee_threshold}% 手續費門檻',
                'market_quality': params['quality'],
                'market_score': params['score'],
                'base_target': params.get('base_target', 0),
                'risk_ratio': params.get('risk_ratio', 1.0)
            }
        else:
            # 第二階段：鎖利期
            # 計算動態追蹤止盈位置
            trailing_offset = params['phase2_trailing_offset']
            trailing_stop_pnl = max(0, max_net_pnl_pct - trailing_offset)
            
            # 確保止盈不低於費用門檻
            trailing_stop_pnl = max(trailing_stop_pnl, self.fee_threshold - 1)
            
            return {
                'phase': 2,
                'name': '鎖利期',
                'emoji': '🔒',
                'stop_loss_pct': trailing_stop_pnl,  # 動態追蹤
                'target_pct': params['phase2_max_target'],
                'trailing_from': max_net_pnl_pct,
                'trailing_offset': trailing_offset,
                'message': f'追蹤止盈: 最高 {max_net_pnl_pct:.1f}% → 鎖定 {trailing_stop_pnl:.1f}%',
                'market_quality': params['quality'],
                'market_score': params['score']
            }
    
    def check_exit(self, trade: 'TradeRecord', net_pnl_pct: float, max_net_pnl_pct: float, 
                   hold_time_min: float, market_data: Dict = None) -> Dict:
        """
        檢查是否應該退出 (動態版)
        
        Args:
            trade: 交易記錄
            net_pnl_pct: 當前淨利百分比
            max_net_pnl_pct: 最大淨利百分比
            hold_time_min: 持倉時間 (分鐘)
            market_data: 市場數據 (用於動態調整)
            
        Returns:
            Dict with: should_exit, reason, phase_info
        """
        if not self.enabled:
            return {'should_exit': False, 'reason': '', 'phase_info': {}}
        
        phase_info = self.get_current_phase(net_pnl_pct, max_net_pnl_pct, market_data)
        params = self.get_dynamic_params(net_pnl_pct)  # 傳入 net_pnl_pct
        
        result = {
            'should_exit': False,
            'reason': '',
            'phase_info': phase_info,
            'net_pnl_pct': net_pnl_pct,
            'max_net_pnl_pct': max_net_pnl_pct,
            'dynamic_params': params
        }
        
        quality = params['quality']
        quality_emoji = '🟢' if quality == 'GOOD' else '🟡' if quality == 'NORMAL' else '🔴'
        
        if phase_info['phase'] == 1:
            # ============================================
            # 第一階段：費用突破期
            # ============================================
            stop_loss = params['phase1_stop_loss']
            target = params['phase1_target']
            
            # 🚨 嚴格止損：虧損超過止損線立即退出
            if net_pnl_pct <= -stop_loss:
                result['should_exit'] = True
                result['reason'] = f'🚨 P1嚴格止損 {quality_emoji}: {net_pnl_pct:.1f}% ≤ -{stop_loss:.1f}%'
                self.trade_stats['strict_sl_triggered'] += 1
                return result
            
            # ⚡ 快速獲利：達到第一階段目標
            if net_pnl_pct >= target:
                result['should_exit'] = True
                result['reason'] = f'⚡ P1目標達成 {quality_emoji}: {net_pnl_pct:.1f}% ≥ {target:.1f}%'
                self.trade_stats['phase1_exits'] += 1
                return result
            
            # ⏰ 時間止損：根據市場條件調整
            time_limit = 5 if quality == 'GOOD' else 4 if quality == 'NORMAL' else 3
            profit_floor = 1 if quality == 'GOOD' else 1.5 if quality == 'NORMAL' else 2
            
            if hold_time_min >= time_limit and net_pnl_pct < profit_floor:
                result['should_exit'] = True
                result['reason'] = f'⏰ P1時間止損 {quality_emoji}: {hold_time_min:.1f}分鐘淨利僅 {net_pnl_pct:.1f}%'
                return result
        
        elif phase_info['phase'] == 2:
            # ============================================
            # 第二階段：鎖利期
            # ============================================
            trailing_stop = phase_info['stop_loss_pct']
            max_target = params['phase2_max_target']
            
            # 🔒 追蹤止盈觸發
            if net_pnl_pct <= trailing_stop:
                result['should_exit'] = True
                result['reason'] = f'🔒 P2追蹤止盈 {quality_emoji}: {net_pnl_pct:.1f}% ≤ 鎖定 {trailing_stop:.1f}%'
                self.trade_stats['phase2_exits'] += 1
                return result
            
            # 🎯 達到最大目標
            if net_pnl_pct >= max_target:
                result['should_exit'] = True
                result['reason'] = f'🎯 P2目標達成 {quality_emoji}: {net_pnl_pct:.1f}% ≥ {max_target:.1f}%'
                self.trade_stats['phase2_exits'] += 1
                return result
        
        return result
    
    def record_trade_result(self, net_pnl_pct: float, is_win: bool):
        """記錄交易結果，更新統計"""
        if is_win:
            self.trade_stats['wins'].append(net_pnl_pct)
            # 保留最近 50 筆
            if len(self.trade_stats['wins']) > 50:
                self.trade_stats['wins'] = self.trade_stats['wins'][-50:]
            # 更新平均獲利
            if self.trade_stats['wins']:
                self.historical_avg_win = sum(self.trade_stats['wins']) / len(self.trade_stats['wins'])
        else:
            self.trade_stats['losses'].append(abs(net_pnl_pct))
            if len(self.trade_stats['losses']) > 50:
                self.trade_stats['losses'] = self.trade_stats['losses'][-50:]
            if self.trade_stats['losses']:
                self.historical_avg_loss = sum(self.trade_stats['losses']) / len(self.trade_stats['losses'])
        
        # 🆕 動態調整基礎止損
        # 原則：止損不應超過平均獲利 (風險報酬至少 1:1)
        if self.historical_avg_win > 0:
            # 止損 = 平均獲利 (確保 R:R ≥ 1:1)
            self.base_phase1_stop_loss = min(self.base_phase1_stop_loss, self.historical_avg_win + 0.5)
        
        self._save_stats()
    
    def get_display_info(self, net_pnl_pct: float, max_net_pnl_pct: float) -> str:
        """獲取顯示資訊"""
        if not self.enabled:
            return ""
        
        phase_info = self.get_current_phase(net_pnl_pct, max_net_pnl_pct)
        params = self.get_dynamic_params(net_pnl_pct)
        
        if phase_info['phase'] == 1:
            progress = phase_info.get('progress_pct', 0)
            sl = params['phase1_stop_loss']
            return f"{phase_info['emoji']} P1: {progress:.0f}%→費用 | SL:-{sl:.1f}%"
        elif phase_info['phase'] == 2:
            trailing = phase_info.get('stop_loss_pct', 0)
            return f"{phase_info['emoji']} P2: 鎖{trailing:.1f}% | Max:{max_net_pnl_pct:.1f}%"
        
        return ""


class TradeStatus(Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_TIMEOUT = "CLOSED_TIMEOUT"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    CLOSED_NO_MOMENTUM = "CLOSED_NO_MOMENTUM"  # 🆕 v5.9: 無動能快速止損
    CLOSED_PROFIT_PROTECTION = "CLOSED_PROFIT_PROTECTION"  # 🆕 v5.9.1: 先漲保護
    FAILED = "FAILED"


@dataclass
class TradeRecord:
    """交易記錄 (用於 TensorFlow 訓練)"""
    # 基本資訊
    trade_id: str
    timestamp: str
    
    # 策略資訊
    strategy: str
    probability: float
    confidence: float
    direction: str  # LONG / SHORT
    
    # 進場資訊
    entry_price: float
    entry_time: str
    leverage: int
    position_size_usdt: float
    position_size_btc: float
    
    # 目標
    take_profit_price: float
    stop_loss_price: float
    
    # 進場時市場狀態 (特徵)
    obi: float = 0.0
    wpi: float = 0.0
    vpin: float = 0.0
    funding_rate: float = 0.0
    oi_change_pct: float = 0.0
    liq_pressure_long: float = 50.0
    liq_pressure_short: float = 50.0
    price_change_1m: float = 0.0
    price_change_5m: float = 0.0
    volatility_5m: float = 0.0
    
    # 策略機率分布
    strategy_probs: Dict[str, float] = field(default_factory=dict)
    
    # 🆕 Maker 分批進場資訊
    entry_type: str = "MAKER"      # MAKER / TAKER
    entry_batches: int = 5         # 分批次數
    entry_duration_sec: float = 0  # 實際進場耗時
    avg_entry_price: float = 0.0   # 平均進場價格 (分批後)
    entry_slippage_pct: float = 0.0  # 模擬滑點
    
    # 🆕 損益平衡價格 (最重要的指標!)
    breakeven_price: float = 0.0   # 超過此價才開始獲利
    
    # 🆕 動態調整後的參數
    actual_leverage: int = 75      # 實際使用的槓桿
    actual_target_pct: float = 0.12  # 實際目標價格移動 %
    actual_stop_loss_pct: float = 0.08  # 實際止損 %
    actual_max_hold_min: float = 15.0  # 實際最長持倉時間
    market_volatility: float = 0.0  # 進場時市場波動度
    
    # 🆕 v13.4 六維評分記錄 (方便事後分析)
    six_dim_long_score: int = 0           # 進場時多方評分 (0-12)
    six_dim_short_score: int = 0          # 進場時空方評分 (0-12)
    six_dim_fast_dir: str = ""            # 快線方向
    six_dim_medium_dir: str = ""          # 中線方向
    six_dim_slow_dir: str = ""            # 慢線方向
    six_dim_obi_dir: str = ""             # OBI 方向
    six_dim_momentum_dir: str = ""        # 動能方向
    six_dim_volume_dir: str = ""          # 成交量方向
    
    # 🐋 v10.6 鯨魚策略欄位
    is_whale_trade: bool = False          # 是否為鯨魚策略交易
    whale_direction: str = ""             # 鯨魚方向 (LONG/SHORT)
    whale_buy_value: float = 0.0          # 鯨魚買入金額
    whale_sell_value: float = 0.0         # 鯨魚賣出金額
    whale_trade_count: int = 0            # 鯨魚大單數量
    whale_expected_profit_pct: float = 0.0  # 鯨魚預期獲利 (價格%)
    whale_target_price: float = 0.0       # 🆕 鯨魚目標價格 (達到即平倉)
    whale_estimated_impact_pct: float = 0.0  # 🆕 預估價格影響
    whale_profit_lock_enabled: bool = False  # 鯨魚鎖利是否啟用
    
    # 結果 (平倉後填入)
    status: str = "OPEN"
    exit_price: float = 0.0
    exit_time: str = ""
    pnl_usdt: float = 0.0
    pnl_pct: float = 0.0           # 槓桿後盈虧 %
    price_move_pct: float = 0.0    # 價格移動 %
    fee_usdt: float = 0.0
    net_pnl_usdt: float = 0.0      # 扣手續費後淨盈虧
    hold_seconds: float = 0.0
    max_profit_pct: float = 0.0    # 持倉期間最大浮盈
    max_drawdown_pct: float = 0.0  # 持倉期間最大回撤
    
    def to_dict(self) -> Dict:
        d = asdict(self)
        return d


# ============================================================
# Binance Testnet API
# ============================================================

import requests
import hmac
import hashlib
from urllib.parse import urlencode

class BinanceTestnetAPI:
    """
    Binance Futures Testnet API 客戶端
    直接使用 REST API (因為 ccxt sandbox mode 已停用)
    """
    
    def __init__(self):
        self.base_url = "https://testnet.binancefuture.com"
        self.api_key = ""
        self.api_secret = ""
        self._load_api_keys()
    
    def _load_api_keys(self):
        """從 .env 載入 API Keys"""
        env_file = Path(".env")
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('BINANCE_TESTNET_API_KEY='):
                        self.api_key = line.split('=', 1)[1].strip()
                    elif line.startswith('BINANCE_TESTNET_API_SECRET='):
                        self.api_secret = line.split('=', 1)[1].strip()
        
        if self.api_key:
            print(f"✅ Testnet API Key 已載入")
        else:
            print(f"⚠️ 未找到 Testnet API Key")
    
    def _sign(self, params: Dict) -> str:
        """生成簽名"""
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()
        return query_string + '&signature=' + signature
    
    def _headers(self) -> Dict:
        return {'X-MBX-APIKEY': self.api_key}
    
    def get_account(self) -> Dict:
        """獲取帳戶資訊"""
        params = {'timestamp': int(time.time() * 1000)}
        url = f"{self.base_url}/fapi/v2/account?{self._sign(params)}"
        response = requests.get(url, headers=self._headers())
        return response.json() if response.status_code == 200 else {}
    
    def get_balance(self) -> Dict:
        """獲取餘額資訊"""
        account = self.get_account()
        return {
            'total': float(account.get('totalWalletBalance', 0)),
            'available': float(account.get('availableBalance', 0)),
            'unrealized_pnl': float(account.get('totalUnrealizedProfit', 0))
        }
    
    def place_order(self, symbol: str, side: str, quantity: float,
                   expected_price: float = 0, verify: bool = True) -> Dict:
        """
        下市價單 (帶驗證)
        
        Returns:
            Dict: {'success': bool, 'order': dict, 'error': str, ...}
        """
        if verify and expected_price > 0:
            return self.market_order_with_verification(symbol, side, quantity, expected_price)
        return self.market_order(symbol, side, quantity)
    
    def close_position(self, symbol: str, reason: str = "manual") -> Dict:
        """
        平倉 (帶完整錯誤處理)
        
        Returns:
            Dict: {'success': bool, 'order': dict, 'error': str, 'reason': str}
        """
        result = {
            'success': False,
            'order': None,
            'error': '',
            'reason': reason,
            'position_amt': 0,
            'close_price': 0
        }
        
        pos = self.get_position(symbol)
        if not pos:
            result['error'] = "沒有持倉可平倉"
            return result
        
        amt = float(pos['positionAmt'])
        if amt == 0:
            result['error'] = "持倉數量為 0"
            return result
        
        result['position_amt'] = amt
        side = 'SELL' if amt > 0 else 'BUY'
        quantity = abs(amt)
        
        # 執行平倉
        order_result = self.market_order(symbol, side, quantity)
        
        if order_result['success']:
            result['success'] = True
            result['order'] = order_result['order']
            result['close_price'] = order_result['filled_price']
        else:
            result['error'] = order_result['error']
        
        return result
    
    def get_position(self, symbol: str = "BTCUSDT") -> Optional[Dict]:
        """獲取持倉"""
        params = {'symbol': symbol, 'timestamp': int(time.time() * 1000)}
        url = f"{self.base_url}/fapi/v2/positionRisk?{self._sign(params)}"
        response = requests.get(url, headers=self._headers())
        if response.status_code == 200:
            for pos in response.json():
                if float(pos.get('positionAmt', 0)) != 0:
                    return pos
        return None
    
    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """設置槓桿"""
        params = {
            'symbol': symbol,
            'leverage': leverage,
            'timestamp': int(time.time() * 1000)
        }
        url = f"{self.base_url}/fapi/v1/leverage?{self._sign(params)}"
        response = requests.post(url, headers=self._headers())
        return response.status_code == 200
    
    def set_position_mode(self, dual: bool = False) -> bool:
        """設置持倉模式 (One-way / Hedge)"""
        params = {
            'dualSidePosition': 'true' if dual else 'false',
            'timestamp': int(time.time() * 1000)
        }
        url = f"{self.base_url}/fapi/v1/positionSide/dual?{self._sign(params)}"
        response = requests.post(url, headers=self._headers())
        return response.status_code == 200 or 'No need to change' in response.text
    
    def market_order(self, symbol: str, side: str, quantity: float, 
                     timeout: int = 10, retries: int = 3) -> Dict:
        """
        下市價單 (帶超時和重試機制)
        
        Args:
            symbol: 交易對
            side: 'BUY' or 'SELL'
            quantity: 數量
            timeout: 超時秒數
            retries: 重試次數
            
        Returns:
            Dict: {'success': bool, 'order': dict/None, 'error': str, 'attempts': int}
        """
        # 精度處理 - BTC 最多 3 位小數
        quantity = round(quantity, 3)
        
        result = {
            'success': False,
            'order': None,
            'error': '',
            'attempts': 0,
            'filled_price': 0.0,
            'filled_qty': 0.0
        }
        
        for attempt in range(retries):
            result['attempts'] = attempt + 1
            try:
                params = {
                    'symbol': symbol,
                    'side': side.upper(),
                    'type': 'MARKET',
                    'quantity': quantity,
                    'timestamp': int(time.time() * 1000)
                }
                url = f"{self.base_url}/fapi/v1/order?{self._sign(params)}"
                
                response = requests.post(
                    url, 
                    headers=self._headers(),
                    timeout=timeout
                )
                
                if response.status_code == 200:
                    order = response.json()
                    result['success'] = True
                    result['order'] = order
                    result['filled_price'] = float(order.get('avgPrice', 0))
                    result['filled_qty'] = float(order.get('executedQty', 0))
                    
                    # 驗證成交
                    if order.get('status') == 'FILLED':
                        print(f"✅ 訂單成交: {side} {result['filled_qty']} @ ${result['filled_price']:,.2f}")
                    elif order.get('status') == 'PARTIALLY_FILLED':
                        print(f"⚠️ 部分成交: {result['filled_qty']}/{quantity}")
                    
                    return result
                else:
                    error_msg = response.text
                    result['error'] = error_msg
                    print(f"⚠️ 下單失敗 (嘗試 {attempt+1}/{retries}): {error_msg}")
                    
            except requests.Timeout:
                result['error'] = f"訂單超時 ({timeout}秒)"
                print(f"⏱️ 訂單超時 (嘗試 {attempt+1}/{retries})")
                
            except Exception as e:
                result['error'] = str(e)
                print(f"❌ 下單異常 (嘗試 {attempt+1}/{retries}): {e}")
            
            # 重試前等待
            if attempt < retries - 1:
                time.sleep(1)
        
        return result
    
    def market_order_with_verification(self, symbol: str, side: str, quantity: float,
                                       expected_price: float, max_slippage_pct: float = 0.1) -> Dict:
        """
        下市價單並驗證成交價格滑點
        """
        result = self.market_order(symbol, side, quantity)
        
        if result['success'] and result['filled_price'] > 0:
            slippage = abs(result['filled_price'] - expected_price) / expected_price * 100
            result['slippage_pct'] = slippage
            
            if slippage > max_slippage_pct:
                print(f"⚠️ 滑點警告: {slippage:.3f}% (預期 ${expected_price:,.2f}, 實際 ${result['filled_price']:,.2f})")
                result['slippage_warning'] = True
            else:
                result['slippage_warning'] = False
        
        return result

    def get_price(self, symbol: str = "BTCUSDT") -> float:
        """獲取當前價格"""
        response = requests.get(f"{self.base_url}/fapi/v1/ticker/price?symbol={symbol}")
        if response.status_code == 200:
            return float(response.json()['price'])
        return 0.0
    
    def get_mark_price(self, symbol: str = "BTCUSDT") -> float:
        """獲取標記價格 (用於盈虧計算)"""
        response = requests.get(f"{self.base_url}/fapi/v1/premiumIndex?symbol={symbol}")
        if response.status_code == 200:
            return float(response.json().get('markPrice', 0))
        return 0.0
    
    def get_ticker(self, symbol: str = "BTCUSDT") -> Dict:
        """獲取 24hr ticker 數據"""
        response = requests.get(f"{self.base_url}/fapi/v1/ticker/24hr?symbol={symbol}")
        if response.status_code == 200:
            return response.json()
        return {}
    
    def get_orderbook(self, symbol: str = "BTCUSDT", limit: int = 5) -> Dict:
        """獲取訂單簿"""
        response = requests.get(f"{self.base_url}/fapi/v1/depth?symbol={symbol}&limit={limit}")
        if response.status_code == 200:
            return response.json()
        return {'bids': [], 'asks': []}
    
    def get_recent_trades(self, symbol: str = "BTCUSDT", limit: int = 100) -> List:
        """獲取最近成交"""
        response = requests.get(f"{self.base_url}/fapi/v1/trades?symbol={symbol}&limit={limit}")
        if response.status_code == 200:
            return response.json()
        return []
    
    def get_market_data(self, symbol: str = "BTCUSDT") -> Dict:
        """獲取完整市場數據 (一次請求多項數據)"""
        price = self.get_price(symbol)
        orderbook = self.get_orderbook(symbol)
        
        # 計算 OBI
        bids = [[float(b[0]), float(b[1])] for b in orderbook.get('bids', [])]
        asks = [[float(a[0]), float(a[1])] for a in orderbook.get('asks', [])]
        
        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        total_vol = bid_vol + ask_vol
        obi = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0
        
        return {
            'price': price,
            'bid_price': float(bids[0][0]) if bids else 0,
            'ask_price': float(asks[0][0]) if asks else 0,
            'obi': obi,
            'bid_depth': bid_vol,
            'ask_depth': ask_vol,
            'bids': bids,
            'asks': asks
        }


# ============================================================
# 🆕 v14.8: 進階風控系統 (Advanced Risk Control System)
# 包含: 動態 Band + VWAP 滑點預估 + Oracle 守門員 + 狀態機 + 三段式出場
# ============================================================

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional
import math

class TradingState(Enum):
    """交易狀態機狀態"""
    CAN_TRADE = "CAN_TRADE"     # 正常交易
    SUSPECT = "SUSPECT"         # 可疑狀態 (縮倉/提高門檻)
    HALT = "HALT"               # 暫停新倉 (只能平倉)
    ESCAPE = "ESCAPE"           # 逃命模式 (強制平倉)


class ExitPhase(Enum):
    """出場階段"""
    NORMAL = "NORMAL"           # 正常 (limit/post-only)
    STAGED = "STAGED"           # 分批出場 (每 200ms 出 10-20%)
    EMERGENCY = "EMERGENCY"     # 緊急出場 (市價全平)


@dataclass
class MarketSnapshot:
    """市場即時快照"""
    # 幣安數據
    binance_mid: float = 0.0       # 幣安 (bid+ask)/2
    binance_bid: float = 0.0       # 幣安最佳買價
    binance_ask: float = 0.0       # 幣安最佳賣價
    binance_timestamp: float = 0.0  # 幣安報價時間戳
    
    # dYdX 數據
    dydx_mid: float = 0.0          # dYdX (bid+ask)/2
    dydx_bid: float = 0.0          # dYdX 最佳買價
    dydx_ask: float = 0.0          # dYdX 最佳賣價
    dydx_oracle: float = 0.0       # dYdX Oracle/Mark 價格
    dydx_timestamp: float = 0.0    # dYdX 報價時間戳
    dydx_spread: float = 0.0       # dYdX (ask-bid)/mid 點差
    
    # 衍生指標
    vol_1s: float = 0.0            # 1 秒波動率 (EMA)
    lat_ms: float = 0.0            # 兩邊報價延遲 (ms)
    vol_per_sec: float = 0.0       # 每秒波動估計
    
    # 訂單簿深度
    dydx_bids: List[List[float]] = field(default_factory=list)  # [[price, qty], ...]
    dydx_asks: List[List[float]] = field(default_factory=list)


@dataclass
class DynamicBandConfig:
    """動態 Band 配置"""
    # Entry Band 參數 (v14.9: 放寬以容納 DataHub 延遲 ~0.15-0.20%)
    base_entry: float = 0.25       # 基礎進場門檻 % (0.10→0.25)
    min_entry: float = 0.15        # 最小進場門檻 % (0.06→0.15)
    max_entry: float = 0.50        # 最大進場門檻 % (0.25→0.50)
    k_vol: float = 2.0             # 波動放大係數
    k_spread: float = 1.5          # 點差放大係數
    k_lat: float = 0.5             # 延遲放大係數
    
    # Halt Band 參數 (同步放寬)
    halt_mult: float = 2.5         # Halt = Entry × 此係數
    min_halt: float = 0.40         # 最小 Halt 門檻 % (0.20→0.40)
    max_halt: float = 1.00         # 最大 Halt 門檻 % (0.60→1.00)
    
    # Oracle Gap 參數
    oracle_warn: float = 0.08      # Oracle 警告門檻 %
    oracle_halt: float = 0.20      # Oracle 暫停門檻 %
    
    # 數據新鮮度
    max_data_age_ms: float = 3000  # 數據最大年齡 (ms)
    
    # Escape 參數
    escape_timeout_sec: float = 5.0  # 連續超標多久進入 ESCAPE


class AdvancedRiskController:
    """
    進階風控控制器
    
    🎯 核心功能:
    1. 動態 Band 計算 (根據波動/點差/延遲調整)
    2. VWAP 滑點預估 (用 orderbook 估計實際成交價)
    3. Oracle Gap 守門員 (偵測 dYdX 局部異常)
    4. 交易狀態機 (CAN_TRADE/SUSPECT/HALT/ESCAPE)
    5. 三段式出場保護 (正常/分批/強退)
    
    📊 狀態轉移:
    CAN_TRADE ←→ SUSPECT ←→ HALT → ESCAPE
    """
    
    def __init__(self, config: 'TradingConfig' = None):
        # 配置
        self.band_config = DynamicBandConfig()
        
        # 從 trading config 載入設定
        if config:
            spread_guard_cfg = getattr(config, 'spread_guard', {})
            if isinstance(spread_guard_cfg, dict):
                # Entry Band 參數
                self.band_config.base_entry = spread_guard_cfg.get('base_entry', 0.10)
                self.band_config.min_entry = spread_guard_cfg.get('min_entry', 0.06)
                self.band_config.max_entry = spread_guard_cfg.get('max_entry', 0.25)
                self.band_config.k_vol = spread_guard_cfg.get('k_vol', 2.0)
                self.band_config.k_spread = spread_guard_cfg.get('k_spread', 1.5)
                self.band_config.k_lat = spread_guard_cfg.get('k_lat', 0.5)
                
                # Halt Band 參數
                self.band_config.halt_mult = spread_guard_cfg.get('halt_mult', 2.5)
                self.band_config.min_halt = spread_guard_cfg.get('min_halt', 0.20)
                self.band_config.max_halt = spread_guard_cfg.get('max_halt', 0.60)
                
                # Oracle Gap 參數
                self.band_config.oracle_warn = spread_guard_cfg.get('oracle_warn', 0.08)
                self.band_config.oracle_halt = spread_guard_cfg.get('oracle_halt', 0.20)
                
                # 數據新鮮度
                self.band_config.max_data_age_ms = spread_guard_cfg.get('max_data_age_ms', 3000)
                
                # Escape 參數
                self.band_config.escape_timeout_sec = spread_guard_cfg.get('escape_timeout_sec', 5.0)
        
        # 數據源引用 (啟動時設定)
        self._binance_ws: Optional['BinanceWebSocket'] = None
        self._dydx_ws: Optional['DydxWebSocket'] = None
        
        # 狀態
        self.current_state: TradingState = TradingState.CAN_TRADE
        self.exit_phase: ExitPhase = ExitPhase.NORMAL
        self.state_since: float = time.time()
        self.halt_since: float = 0.0  # 進入 HALT 的時間
        self._band_out_since: Optional[float] = None  # 🔧 v14.6.42: Band 外開始時間
        
        # 動態 Band (計算後的值)
        self.band_entry: float = 0.10
        self.band_halt: float = 0.30
        
        # 波動率追蹤 (EMA)
        self._vol_ema: float = 0.0
        self._vol_ema_alpha: float = 0.3
        self._last_price: float = 0.0
        self._last_price_time: float = 0.0
        
        # 歷史記錄
        self.snapshot_history: deque = deque(maxlen=100)
        self.state_history: deque = deque(maxlen=50)
        
        # 統計
        self.stats = {
            'total_checks': 0,
            'can_trade_count': 0,
            'suspect_count': 0,
            'halt_count': 0,
            'escape_count': 0,
            'blocked_by_oracle': 0,
            'blocked_by_spread': 0,
            'blocked_by_stale_data': 0,
        }
        
        # 冷卻
        self.cooldown_until: float = 0.0
        self.cooldown_duration: float = 30.0  # 預設冷卻 30 秒
        
        logging.info("✅ AdvancedRiskController 初始化完成")
    
    def set_data_sources(self, binance_ws: 'BinanceWebSocket', dydx_ws: 'DydxWebSocket'):
        """設定數據源"""
        self._binance_ws = binance_ws
        self._dydx_ws = dydx_ws
        logging.info("✅ 風控系統數據源已連接")
    
    # ═══════════════════════════════════════════════════════════════════
    # 1. 數據收集
    # ═══════════════════════════════════════════════════════════════════
    
    def get_market_snapshot(self) -> MarketSnapshot:
        """
        獲取市場即時快照
        
        收集幣安和 dYdX 的即時數據用於風控計算
        """
        snapshot = MarketSnapshot()
        now = time.time()
        
        # 幣安數據
        if self._binance_ws:
            snapshot.binance_mid = self._binance_ws.current_price or 0
            snapshot.binance_bid = getattr(self._binance_ws, 'bid_price', 0) or 0
            snapshot.binance_ask = getattr(self._binance_ws, 'ask_price', 0) or 0
            
            # 取得時間戳 (毫秒 -> 秒)
            binance_ts_ms = getattr(self._binance_ws, 'last_trade_time', 0) or 0
            if binance_ts_ms > 0:
                binance_ts = binance_ts_ms / 1000
                # 如果時間戳過舊 (>10秒)，但有價格，說明 WebSocket 連接正常，使用當前時間
                if snapshot.binance_mid > 0 and (now - binance_ts) > 10:
                    snapshot.binance_timestamp = now
                else:
                    snapshot.binance_timestamp = binance_ts
            else:
                snapshot.binance_timestamp = now if snapshot.binance_mid > 0 else 0
        
        # dYdX 數據
        if self._dydx_ws:
            snapshot.dydx_bid = getattr(self._dydx_ws, 'bid_price', 0) or 0
            snapshot.dydx_ask = getattr(self._dydx_ws, 'ask_price', 0) or 0
            
            # 計算 mid price，優先用 bid/ask，否則用 current_price
            if snapshot.dydx_bid > 0 and snapshot.dydx_ask > 0:
                snapshot.dydx_mid = (snapshot.dydx_bid + snapshot.dydx_ask) / 2
            else:
                snapshot.dydx_mid = getattr(self._dydx_ws, 'current_price', 0) or 0
            
            # 如果 dydx_mid 還是 0，嘗試用幣安價格作為備用
            if snapshot.dydx_mid <= 0 and snapshot.binance_mid > 0:
                snapshot.dydx_mid = snapshot.binance_mid  # 備用：假設兩交易所價格接近
            
            # dYdX 時間戳處理 (同樣邏輯)
            dydx_ts_ms = getattr(self._dydx_ws, 'last_trade_time', 0) or 0
            if dydx_ts_ms > 0:
                dydx_ts = dydx_ts_ms / 1000
                # 如果時間戳過舊 (>10秒)，但有價格，使用當前時間
                if snapshot.dydx_mid > 0 and (now - dydx_ts) > 10:
                    snapshot.dydx_timestamp = now
                else:
                    snapshot.dydx_timestamp = dydx_ts
            else:
                snapshot.dydx_timestamp = now if snapshot.dydx_mid > 0 else 0
            snapshot.dydx_bids = getattr(self._dydx_ws, 'bids', [])[:10] if getattr(self._dydx_ws, 'bids', None) else []
            snapshot.dydx_asks = getattr(self._dydx_ws, 'asks', [])[:10] if getattr(self._dydx_ws, 'asks', None) else []
            
            # dYdX 點差
            if snapshot.dydx_mid > 0 and snapshot.dydx_bid > 0 and snapshot.dydx_ask > 0:
                snapshot.dydx_spread = (snapshot.dydx_ask - snapshot.dydx_bid) / snapshot.dydx_mid * 100
            else:
                snapshot.dydx_spread = 0.0
            
            # Oracle 價格 (如果有)
            snapshot.dydx_oracle = getattr(self._dydx_ws, 'oracle_price', 0) or snapshot.dydx_mid
        
        # 計算延遲
        if snapshot.binance_timestamp > 0 and snapshot.dydx_timestamp > 0:
            snapshot.lat_ms = abs(snapshot.binance_timestamp - snapshot.dydx_timestamp) * 1000
        
        # 計算波動率 (EMA)
        if snapshot.binance_mid > 0 and self._last_price > 0:
            dt = now - self._last_price_time
            if dt > 0 and dt < 5:  # 5秒內有效
                pct_change = abs(snapshot.binance_mid - self._last_price) / self._last_price * 100
                self._vol_ema = self._vol_ema_alpha * pct_change + (1 - self._vol_ema_alpha) * self._vol_ema
        
        snapshot.vol_1s = self._vol_ema
        snapshot.vol_per_sec = self._vol_ema  # 簡化
        
        # 更新追蹤
        if snapshot.binance_mid > 0:
            self._last_price = snapshot.binance_mid
            self._last_price_time = now
        
        # 記錄歷史
        self.snapshot_history.append({
            'time': now,
            'snapshot': snapshot
        })
        
        return snapshot
    
    # ═══════════════════════════════════════════════════════════════════
    # 2. 動態 Band 計算
    # ═══════════════════════════════════════════════════════════════════
    
    def calculate_dynamic_bands(self, snapshot: MarketSnapshot) -> Tuple[float, float]:
        """
        計算動態 Entry/Halt Band
        
        公式:
        band_entry = clamp(
            max(base_entry, k_vol * vol_1s, k_spread * spread_now, k_lat * lat_factor),
            min_entry, max_entry
        )
        band_halt = clamp(band_entry * halt_mult, min_halt, max_halt)
        
        Returns:
            (band_entry, band_halt) 百分比
        """
        cfg = self.band_config
        
        # 各因素貢獻
        vol_factor = cfg.k_vol * snapshot.vol_1s
        spread_factor = cfg.k_spread * snapshot.dydx_spread
        lat_factor = cfg.k_lat * (snapshot.lat_ms / 1000) * snapshot.vol_per_sec
        
        # Entry Band: 取最大值，然後 clamp
        raw_entry = max(cfg.base_entry, vol_factor, spread_factor, lat_factor)
        band_entry = max(cfg.min_entry, min(cfg.max_entry, raw_entry))
        
        # Halt Band: Entry 的倍數，然後 clamp
        raw_halt = band_entry * cfg.halt_mult
        band_halt = max(cfg.min_halt, min(cfg.max_halt, raw_halt))
        
        # 保存計算結果
        self.band_entry = band_entry
        self.band_halt = band_halt
        
        return band_entry, band_halt
    
    # ═══════════════════════════════════════════════════════════════════
    # 3. VWAP 滑點預估
    # ═══════════════════════════════════════════════════════════════════
    
    def estimate_fill_price(self, snapshot: MarketSnapshot, side: str, qty_btc: float) -> Tuple[float, float]:
        """
        用 orderbook 估計預期成交價 (VWAP)
        
        Args:
            snapshot: 市場快照
            side: "BUY" 或 "SELL"
            qty_btc: 下單數量 (BTC)
        
        Returns:
            (expected_fill_price, slippage_pct)
        """
        if side == "BUY":
            # 買入: 吃 asks
            book = snapshot.dydx_asks
            best_price = snapshot.dydx_ask
        else:
            # 賣出: 吃 bids
            book = snapshot.dydx_bids
            best_price = snapshot.dydx_bid
        
        if not book or best_price <= 0:
            return snapshot.dydx_mid, 0.0
        
        # 累積計算 VWAP
        remaining_qty = qty_btc
        total_value = 0.0
        total_qty = 0.0
        
        for level in book:
            if len(level) >= 2:
                price = float(level[0])
                size = float(level[1])
                
                fill_qty = min(remaining_qty, size)
                total_value += price * fill_qty
                total_qty += fill_qty
                remaining_qty -= fill_qty
                
                if remaining_qty <= 0:
                    break
        
        if total_qty <= 0:
            return best_price, 0.0
        
        vwap = total_value / total_qty
        slippage_pct = abs(vwap - best_price) / best_price * 100
        
        return vwap, slippage_pct
    
    def calculate_effective_diff(self, snapshot: MarketSnapshot, side: str, qty_btc: float) -> float:
        """
        計算有效價差 (含滑點)
        
        effective_diff = |expected_fill_price - binance_mid| / binance_mid * 100
        """
        # 防護：如果幣安或 dYdX 價格無效，返回 0 (允許交易)
        if snapshot.binance_mid <= 0 or snapshot.dydx_mid <= 0:
            return 0.0
        
        expected_fill, _ = self.estimate_fill_price(snapshot, side, qty_btc)
        
        # 防護：如果預期成交價無效，使用 dYdX mid
        if expected_fill <= 0:
            expected_fill = snapshot.dydx_mid
        
        effective_diff = abs(expected_fill - snapshot.binance_mid) / snapshot.binance_mid * 100
        
        return effective_diff
    
    # ═══════════════════════════════════════════════════════════════════
    # 4. Oracle Gap 守門員
    # ═══════════════════════════════════════════════════════════════════
    
    def calculate_oracle_gap(self, snapshot: MarketSnapshot) -> Tuple[float, str]:
        """
        計算 Oracle Gap
        
        oracle_gap = |dydx_mid - dydx_oracle| / dydx_oracle * 100
        
        Returns:
            (gap_pct, status: 'OK' | 'WARN' | 'HALT')
        """
        if snapshot.dydx_oracle <= 0 or snapshot.dydx_mid <= 0:
            return 0.0, 'OK'
        
        gap = abs(snapshot.dydx_mid - snapshot.dydx_oracle) / snapshot.dydx_oracle * 100
        
        cfg = self.band_config
        if gap >= cfg.oracle_halt:
            return gap, 'HALT'
        elif gap >= cfg.oracle_warn:
            return gap, 'WARN'
        else:
            return gap, 'OK'
    
    # ═══════════════════════════════════════════════════════════════════
    # 5. 交易狀態機
    # ═══════════════════════════════════════════════════════════════════
    
    def update_state(self, snapshot: MarketSnapshot, has_position: bool = False, 
                     qty_btc: float = 0.002, side: str = "BUY") -> TradingState:
        """
        更新交易狀態
        
        狀態轉移邏輯:
        - effective_diff <= band_entry 且 oracle_gap == OK → CAN_TRADE
        - effective_diff <= band_halt 或 oracle_gap == WARN → SUSPECT
        - effective_diff > band_halt 或 oracle_gap == HALT 或 數據過期 → HALT
        - 已持倉且連續超標超過 T_escape 秒 → ESCAPE
        
        Returns:
            新的交易狀態
        """
        now = time.time()
        self.stats['total_checks'] += 1
        
        # 檢查冷卻期
        if now < self.cooldown_until:
            return self.current_state
        
        # 計算動態 Band
        band_entry, band_halt = self.calculate_dynamic_bands(snapshot)
        
        # 計算有效價差 (含滑點)
        effective_diff = self.calculate_effective_diff(snapshot, side, qty_btc)
        
        # 計算 Oracle Gap
        oracle_gap, oracle_status = self.calculate_oracle_gap(snapshot)
        
        # 檢查數據新鮮度
        data_age_ms = max(
            (now - snapshot.binance_timestamp) * 1000 if snapshot.binance_timestamp > 0 else 9999,
            (now - snapshot.dydx_timestamp) * 1000 if snapshot.dydx_timestamp > 0 else 9999
        )
        data_stale = data_age_ms > self.band_config.max_data_age_ms
        
        # 決定新狀態
        old_state = self.current_state
        new_state = TradingState.CAN_TRADE
        reason = ""
        
        if data_stale:
            new_state = TradingState.HALT
            reason = f"數據過期 ({data_age_ms:.0f}ms)"
            self.stats['blocked_by_stale_data'] += 1
        elif oracle_status == 'HALT':
            new_state = TradingState.HALT
            reason = f"Oracle 異常 ({oracle_gap:.3f}%)"
            self.stats['blocked_by_oracle'] += 1
        elif effective_diff > band_halt:
            new_state = TradingState.HALT
            reason = f"價差過大 ({effective_diff:.3f}% > {band_halt:.3f}%)"
            self.stats['blocked_by_spread'] += 1
        elif oracle_status == 'WARN' or effective_diff > band_entry:
            new_state = TradingState.SUSPECT
            reason = f"可疑 (diff:{effective_diff:.3f}%, oracle:{oracle_gap:.3f}%)"
            self.stats['suspect_count'] += 1
        else:
            new_state = TradingState.CAN_TRADE
            reason = "正常"
            self.stats['can_trade_count'] += 1
        
        # ESCAPE 邏輯: 已持倉 + HALT 超過 T 秒
        if has_position and new_state == TradingState.HALT:
            if self.halt_since == 0:
                self.halt_since = now
            elif now - self.halt_since > self.band_config.escape_timeout_sec:
                new_state = TradingState.ESCAPE
                reason = f"連續 HALT 超過 {self.band_config.escape_timeout_sec}s → ESCAPE"
                self.stats['escape_count'] += 1
        else:
            self.halt_since = 0
        
        # 狀態改變
        if new_state != old_state:
            self.current_state = new_state
            self.state_since = now
            
            self.state_history.append({
                'time': now,
                'from': old_state.value,
                'to': new_state.value,
                'reason': reason,
                'effective_diff': effective_diff,
                'oracle_gap': oracle_gap,
                'band_entry': band_entry,
                'band_halt': band_halt
            })
            
            logging.info(f"🔄 狀態轉移: {old_state.value} → {new_state.value} ({reason})")
        
        return new_state
    
    # ═══════════════════════════════════════════════════════════════════
    # 6. 三段式出場
    # ═══════════════════════════════════════════════════════════════════
    
    def get_exit_phase(self, snapshot: MarketSnapshot, has_position: bool, 
                       position_entry_time: float = 0) -> Tuple[ExitPhase, Dict]:
        """
        判斷出場階段
        
        - NORMAL: Band 內，正常出場 (limit/post-only)
        - STAGED: Band 外 1-3 秒，分批出場 (每批 10-20%)
        - EMERGENCY: Band 外超過 T 秒，強制全平
        
        Returns:
            (出場階段, 執行參數)
        """
        if not has_position:
            return ExitPhase.NORMAL, {}
        
        now = time.time()
        band_entry, band_halt = self.calculate_dynamic_bands(snapshot)
        effective_diff = self.calculate_effective_diff(snapshot, "SELL", 0.002)  # 簡化
        
        # � v14.9.6: 節流 Band DEBUG 訊息，每 30 秒打印一次摘要
        last_band_log = getattr(self, '_last_band_debug_ts', 0)
        if now - last_band_log >= 30.0:
            self._last_band_debug_ts = now
            status = "✅ Band內" if effective_diff <= band_entry else f"⚠️ Band外 {effective_diff:.4f}%>{band_entry:.4f}%"
            if getattr(self.trader, 'dydx_sync_enabled', False):
                print(f"📊 [Band] {status} | Binance=${snapshot.binance_mid:.0f} | dYdX=${snapshot.dydx_bid:.0f}")
            else:
                print(f"📊 [Band] {status} | Binance=${snapshot.binance_mid:.0f}")
        
        # Band 內: 正常
        if effective_diff <= band_entry:
            self.exit_phase = ExitPhase.NORMAL
            # 🔧 v14.6.42: 重置 Band 外計時器，避免下次進場時錯誤計算
            self._band_out_since = None
            return ExitPhase.NORMAL, {
                'order_type': 'LIMIT',
                'post_only': True,
                'reduce_only': True
            }
        
        # Band 外: 計算持續時間
        if self.exit_phase == ExitPhase.NORMAL or self._band_out_since is None:
            # 剛進入 Band 外，開始計時
            self._band_out_since = now
            self.exit_phase = ExitPhase.STAGED
        
        band_out_duration = now - self._band_out_since
        
        # Band 外 1-3 秒: 分批出場
        if band_out_duration < 3.0:
            return ExitPhase.STAGED, {
                'order_type': 'IOC',
                'batch_pct': 0.2,  # 每批 20%
                'batch_interval_ms': 500,  # 每 500ms 一批
                'max_slippage_pct': 0.1,  # 最大滑點 0.1%
                'reduce_only': True
            }
        
        # Band 外超過 3 秒: 緊急全平
        self.exit_phase = ExitPhase.EMERGENCY
        return ExitPhase.EMERGENCY, {
            'order_type': 'MARKET',
            'reduce_only': True,
            'reason': f'Band 外超過 {band_out_duration:.1f}s'
        }
    
    # ═══════════════════════════════════════════════════════════════════
    # 7. 開倉檢查 (整合所有風控)
    # ═══════════════════════════════════════════════════════════════════
    
    def can_open_position(self, direction: str, qty_btc: float = 0.002) -> Tuple[bool, str, Dict]:
        """
        檢查是否可以開倉 (整合所有風控)
        
        Args:
            direction: "LONG" 或 "SHORT"
            qty_btc: 預計下單數量
        
        Returns:
            (允許開倉, 原因, 詳細資訊)
        """
        now = time.time()
        
        # 檢查冷卻期
        if now < self.cooldown_until:
            remaining = self.cooldown_until - now
            return False, f"🧊 冷卻中 ({remaining:.0f}s)", {'cooldown': True}
        
        # 獲取市場快照
        snapshot = self.get_market_snapshot()
        
        # 更新狀態
        side = "BUY" if direction == "LONG" else "SELL"
        state = self.update_state(snapshot, has_position=False, qty_btc=qty_btc, side=side)
        
        # 構建詳細資訊
        oracle_gap, oracle_status = self.calculate_oracle_gap(snapshot)
        effective_diff = self.calculate_effective_diff(snapshot, side, qty_btc)
        
        info = {
            'state': state.value,
            'band_entry': self.band_entry,
            'band_halt': self.band_halt,
            'effective_diff': effective_diff,
            'oracle_gap': oracle_gap,
            'oracle_status': oracle_status,
            'vol_1s': snapshot.vol_1s,
            'dydx_spread': snapshot.dydx_spread,
            'lat_ms': snapshot.lat_ms,
            'binance_mid': snapshot.binance_mid,
            'dydx_mid': snapshot.dydx_mid,
            'dydx_oracle': snapshot.dydx_oracle
        }
        
        # 根據狀態決定
        if state == TradingState.CAN_TRADE:
            return True, f"✅ 可交易 (diff:{effective_diff:.3f}% < {self.band_entry:.3f}%)", info
        
        elif state == TradingState.SUSPECT:
            # SUSPECT: 可開但縮倉/提高門檻
            # 這裡先警告但允許，由上層決定是否縮倉
            return True, f"⚠️ SUSPECT: 建議縮倉 (diff:{effective_diff:.3f}%)", info
        
        elif state in [TradingState.HALT, TradingState.ESCAPE]:
            reason = f"🚫 {state.value}: 禁止新倉 (diff:{effective_diff:.3f}%, oracle:{oracle_gap:.3f}%)"
            return False, reason, info
        
        return False, "❓ 未知狀態", info
    
    def start_cooldown(self, duration: float = None):
        """啟動冷卻期"""
        if duration is None:
            duration = self.cooldown_duration
        self.cooldown_until = time.time() + duration
        logging.info(f"🧊 啟動冷卻期 {duration}s")
    
    def get_stats(self) -> Dict:
        """獲取統計"""
        return {
            **self.stats,
            'current_state': self.current_state.value,
            'band_entry': self.band_entry,
            'band_halt': self.band_halt,
            'state_duration': time.time() - self.state_since
        }
    
    # ═══════════════════════════════════════════════════════════════════
    # 向後兼容方法 (保留舊 API)
    # ═══════════════════════════════════════════════════════════════════
    
    def get_binance_price(self) -> float:
        """[向後兼容] 獲取幣安價格"""
        if self._binance_ws:
            return self._binance_ws.current_price
        return 0.0
    
    def calculate_spread(self, dydx_price: float, binance_price: float = None) -> Dict:
        """
        [向後兼容] 計算價差
        
        Returns:
            兼容舊格式的價差資訊
        """
        if binance_price is None or binance_price <= 0:
            binance_price = self.get_binance_price()
        
        if binance_price <= 0 or dydx_price <= 0:
            return {
                'spread_pct': 0,
                'spread_usdt': 0,
                'dydx_premium': False,
                'status': 'NO_DATA',
                'binance_price': binance_price,
                'dydx_price': dydx_price
            }
        
        spread_usdt = dydx_price - binance_price
        spread_pct = abs(spread_usdt) / binance_price * 100
        dydx_premium = spread_usdt > 0
        
        # 根據 band_entry 和 band_halt 判斷狀態
        if spread_pct >= self.band_halt:
            status = 'BLOCKED'
        elif spread_pct >= self.band_entry:
            status = 'DANGER'
        elif spread_pct >= self.band_config.oracle_warn:
            status = 'WARNING'
        else:
            status = 'OK'
        
        return {
            'spread_pct': spread_pct,
            'spread_usdt': spread_usdt,
            'dydx_premium': dydx_premium,
            'status': status,
            'binance_price': binance_price,
            'dydx_price': dydx_price
        }
    
    def start_binance_feed(self):
        """[向後兼容] 啟動幣安數據源 (現在由外部管理)"""
        pass  # 不再需要，由外部設定 binance_ws
    
    def stop_binance_feed(self):
        """[向後兼容] 停止幣安數據源 (現在由外部管理)"""
        pass  # 不再需要


# 向後兼容: 保留舊類名
BinanceDydxSpreadGuard = AdvancedRiskController


# ============================================================
# 🆕 dYdX WebSocket 數據接收器 (取代 Binance)
# ============================================================

# 🔧 v13.0: 導入 Data Hub (WebSocket 優先 + 本機快取共享)
try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.dydx_data_hub import DydxDataHub, get_data_hub, MarketData
    _HAS_DATA_HUB = True
except ImportError:
    _HAS_DATA_HUB = False
    logging.warning("⚠️ 未找到 DydxDataHub，使用舊版 REST 輪詢")

# 🔧 v12.11: 導入共享速率限制器 (備用)
try:
    from src.shared_rate_limiter import SharedRateLimiter, get_shared_limiter
    _HAS_SHARED_LIMITER = True
except ImportError:
    _HAS_SHARED_LIMITER = False


class DydxWebSocket:
    """
    dYdX WebSocket 數據接收器
    使用 dYdX v4 Indexer WebSocket 獲取即時數據
    
    優點:
    - 真實交易用的數據源
    - 與實際交易所一致的價格
    - 原生支援 BTC-USD 永續合約
    
    🔧 v13.0: 重構為使用 DydxDataHub
    - 純 WebSocket 數據流 (訂閱 trades/orderbook/candles)
    - 本機 JSON 快取讓多個 bot 共享數據
    - 第一個 bot 當 master，其他 bot 讀取本機快取
    - REST 僅用於啟動初始化 (1次)
    """
    
    def __init__(self, symbol: str = "BTC-USD", network: str = "mainnet"):
        self.symbol = symbol
        self.network = network
        
        # 🔧 v13.0: 使用 Data Hub
        if _HAS_DATA_HUB:
            self._hub = DydxDataHub(
                symbol=symbol,
                network=network,
                big_trade_threshold=1000  # dYdX 適配：$1K
            )
            self._use_hub = True
            logging.info(f"✅ 使用 DydxDataHub (純 WebSocket + 本機快取)")
        else:
            self._hub = None
            self._use_hub = False
            logging.warning("⚠️ 使用舊版 REST 輪詢模式")
        
        # 兼容舊 API 的屬性 (備用/舊模式)
        self.current_price = 0.0
        self.bid_price = 0.0
        self.ask_price = 0.0
        self.last_trade_time = 0
        self.trades_1s: deque = deque(maxlen=100)
        self.trades_1m: deque = deque(maxlen=6000)
        
        # 訂單簿
        self.bids: List[List[float]] = []
        self.asks: List[List[float]] = []
        
        # 大單追蹤
        self.big_trades: deque = deque(maxlen=100)
        self.big_trade_threshold = 1000
        
        # 統計
        self.buy_volume_1s = 0.0
        self.sell_volume_1s = 0.0
        
        # OBI 平滑化 (EMA)
        self._obi_history: deque = deque(maxlen=10)
        self._obi_ema = 0.0
        self._obi_ema_alpha = 0.3
        
        # 控制
        self.running = False
        self._ws_thread = None
        
        # K 線緩存
        self._candles_1m: List[Dict] = []
        
        # 🔧 舊模式的 REST/WS 設定 (僅在沒有 Data Hub 時使用)
        if not self._use_hub:
            if network == "mainnet":
                self.ws_base = "wss://indexer.dydx.trade/v4/ws"
                self.rest_base = "https://indexer.dydx.trade/v4"
            else:
                self.ws_base = "wss://indexer.v4testnet.dydx.exchange/v4/ws"
                self.rest_base = "https://indexer.v4testnet.dydx.exchange/v4"
            
            self._last_rest_fetch = 0
            self._rest_fetch_interval = 2.0
            self._candles_last_fetch = 0
            
            if _HAS_SHARED_LIMITER:
                self._rate_limiter = get_shared_limiter()
            else:
                self._rate_limiter = None
            
            self._consecutive_429s = 0
            self._backoff_until = 0
    
    def _sync_from_hub(self):
        """從 Data Hub 同步數據到本地屬性"""
        if not self._hub:
            return
        
        data = self._hub.get_data()
        
        self.current_price = data.current_price
        self.bid_price = data.bid_price
        self.ask_price = data.ask_price
        self.last_trade_time = data.last_trade_time
        
        self.bids = data.bids
        self.asks = data.asks
        
        # 轉換交易到 deque
        self.trades_1s.clear()
        self.trades_1m.clear()
        now = time.time() * 1000
        for t in data.recent_trades:
            if now - t['time'] < 1000:
                self.trades_1s.append(t)
            if now - t['time'] < 60000:
                self.trades_1m.append(t)
        
        self.big_trades.clear()
        for t in data.big_trades:
            self.big_trades.append(t)
        
        self.buy_volume_1s = data.buy_volume_1m / 60  # 近似
        self.sell_volume_1s = data.sell_volume_1m / 60
        
        self._candles_1m = data.candles_1m
    
    async def _fetch_rest_data(self):
        """使用 REST API 獲取數據 (作為 WebSocket 的備援/補充)"""
        import aiohttp
        
        # 🔧 v12.11: 檢查退避時間
        if time.time() < self._backoff_until:
            logging.debug(f"⏳ 429 退避中，跳過 REST 請求")
            return
        
        try:
            # 🔧 v12.11: 使用共享速率限制器
            if self._rate_limiter:
                if not await self._rate_limiter.acquire(timeout=5.0):
                    logging.warning("⏳ 速率限制: 無法獲取 API 配額")
                    return
            
            async with aiohttp.ClientSession() as session:
                # 獲取訂單簿
                async with session.get(f"{self.rest_base}/orderbooks/perpetualMarket/{self.symbol}") as resp:
                    # 🔧 v12.11: 處理 429
                    if resp.status == 429:
                        self._handle_429(resp)
                        return
                    
                    if resp.status == 200:
                        self._consecutive_429s = 0  # 重置計數器
                        data = await resp.json()
                        bids = data.get("bids", [])
                        asks = data.get("asks", [])
                        
                        self.bids = [[float(b["price"]), float(b["size"])] for b in bids[:10]]
                        self.asks = [[float(a["price"]), float(a["size"])] for a in asks[:10]]
                        
                        if self.bids:
                            self.bid_price = self.bids[0][0]
                        if self.asks:
                            self.ask_price = self.asks[0][0]
                        
                        # 🔧 v14.6.38: 統一使用訂單簿中間價 (顯示和成交一致)
                        if self.bids and self.asks:
                            self.current_price = (self.bid_price + self.ask_price) / 2
                
                # 🔧 v12.11: 再次檢查速率限制
                if self._rate_limiter:
                    if not await self._rate_limiter.acquire(timeout=5.0):
                        return
                
                # 獲取最近交易 (用於交易追蹤和大單偵測，不用於價格)
                async with session.get(f"{self.rest_base}/trades/perpetualMarket/{self.symbol}?limit=50") as resp:
                    if resp.status == 429:
                        self._handle_429(resp)
                        return
                    
                    if resp.status == 200:
                        self._consecutive_429s = 0
                        data = await resp.json()
                        trades = data.get("trades", [])
                        
                        # 🔧 v14.6.38: 不再用最新成交價更新 current_price
                        # 統一使用訂單簿中間價，確保顯示和成交一致
                        
                        for t in trades:
                            price = float(t.get("price", 0))
                            size = float(t.get("size", 0))
                            side = t.get("side", "")
                            created_at = t.get("createdAt", "")
                            
                            # 解析時間
                            try:
                                from datetime import datetime
                                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                                trade_time = dt.timestamp() * 1000
                            except:
                                trade_time = time.time() * 1000
                            
                            self.last_trade_time = trade_time
                            value_usdt = price * size
                            is_buy = side == "BUY"
                            
                            trade = {
                                'price': price,
                                'qty': size,
                                'is_buy': is_buy,
                                'time': trade_time,
                                'value_usdt': value_usdt
                            }
                            
                            # 避免重複添加
                            if not any(abs(t['time'] - trade_time) < 100 for t in list(self.trades_1m)[-10:]):
                                self.trades_1s.append(trade)
                                self.trades_1m.append(trade)
                                
                                # 大單追蹤
                                if value_usdt >= self.big_trade_threshold:
                                    self.big_trades.append(trade)
                
                # 獲取市場價格 (只在沒有交易價格時使用 Oracle Price 作為 fallback)
                # 🔧 v12.9.1: 優先使用最新成交價，Oracle Price 只作備援
                if self.current_price == 0:
                    # 🔧 v12.11: 速率限制
                    if self._rate_limiter:
                        if not await self._rate_limiter.acquire(timeout=5.0):
                            return
                    
                    async with session.get(f"{self.rest_base}/perpetualMarkets?ticker={self.symbol}") as resp:
                        if resp.status == 429:
                            self._handle_429(resp)
                            return
                        if resp.status == 200:
                            data = await resp.json()
                            markets = data.get("markets", {})
                            market = markets.get(self.symbol, {})
                            oracle_price = float(market.get("oraclePrice", 0))
                            if oracle_price > 0:
                                self.current_price = oracle_price
                            
        except Exception as e:
            logging.debug(f"dYdX REST fetch error: {e}")
    
    def _handle_429(self, resp):
        """
        🔧 v12.11: 處理 429 Too Many Requests
        使用指數退避避免持續觸發限制
        """
        self._consecutive_429s += 1
        
        # 讀取 Retry-After header
        retry_after = resp.headers.get("Retry-After", "")
        try:
            wait_seconds = float(retry_after) if retry_after else 0
        except:
            wait_seconds = 0
        
        # 指數退避: 基礎 2 秒，每次翻倍，最長 60 秒
        base_backoff = 2.0
        backoff = min(60.0, base_backoff * (2 ** (self._consecutive_429s - 1)))
        
        # 如果有 Retry-After，使用較大的值
        actual_wait = max(wait_seconds, backoff)
        self._backoff_until = time.time() + actual_wait
        
        # 🔧 動態調整 REST 輪詢間隔
        if self._consecutive_429s >= 3:
            self._rest_fetch_interval = min(10.0, self._rest_fetch_interval * 1.5)
            logging.warning(
                f"⚠️ 429 連續 {self._consecutive_429s} 次，REST 間隔調整為 {self._rest_fetch_interval:.1f}s"
            )
        
        logging.warning(
            f"🚫 dYdX 429: 退避 {actual_wait:.1f}s (連續: {self._consecutive_429s})"
        )

    async def _fetch_candles(self):
        """獲取 K 線數據 (用於計算價格變化)"""
        import aiohttp
        
        # 🔧 v12.11: 退避檢查
        if time.time() < self._backoff_until:
            return
        
        try:
            # 🔧 v12.11: 速率限制
            if self._rate_limiter:
                if not await self._rate_limiter.acquire(timeout=5.0):
                    return
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.rest_base}/candles/perpetualMarkets/{self.symbol}",
                    params={"resolution": "1MIN", "limit": 10}
                ) as resp:
                    if resp.status == 429:
                        self._handle_429(resp)
                        return
                    if resp.status == 200:
                        self._consecutive_429s = 0
                        data = await resp.json()
                        self._candles_1m = data.get("candles", [])
        except Exception as e:
            logging.debug(f"dYdX candles fetch error: {e}")
    
    
    async def _handle_ws_message(self, ws):
        """處理 WebSocket 消息"""
        async for message in ws:
            if not self.running:
                break
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")
                
                if msg_type == "channel_data":
                    contents = data.get("contents", {})
                    
                    # 處理交易數據
                    if "trades" in contents:
                        for t in contents["trades"]:
                            price = float(t.get("price", 0))
                            size = float(t.get("size", 0))
                            side = t.get("side", "")
                            
                            if price > 0:
                                self.current_price = price
                                trade_time = time.time() * 1000
                                self.last_trade_time = trade_time
                                value_usdt = price * size
                                is_buy = side == "BUY"
                                
                                trade = {
                                    'price': price,
                                    'qty': size,
                                    'is_buy': is_buy,
                                    'time': trade_time,
                                    'value_usdt': value_usdt
                                }
                                self.trades_1s.append(trade)
                                self.trades_1m.append(trade)
                                
                                if value_usdt >= self.big_trade_threshold:
                                    self.big_trades.append(trade)
                    
                    # 處理訂單簿數據
                    if "bids" in contents or "asks" in contents:
                        if "bids" in contents:
                            self.bids = [[float(b["price"]), float(b["size"])] for b in contents["bids"][:10]]
                            if self.bids:
                                self.bid_price = self.bids[0][0]
                        if "asks" in contents:
                            self.asks = [[float(a["price"]), float(a["size"])] for a in contents["asks"][:10]]
                            if self.asks:
                                self.ask_price = self.asks[0][0]
                                
            except Exception as e:
                logging.debug(f"WS message error: {e}")
    
    async def _run_ws(self):
        """運行 WebSocket + REST 混合數據獲取"""
        while self.running:
            try:
                # 嘗試 WebSocket 連接
                async with websockets.connect(self.ws_base) as ws:
                    # 訂閱交易和訂單簿
                    subscribe_trades = {
                        "type": "subscribe",
                        "channel": "v4_trades",
                        "id": self.symbol
                    }
                    subscribe_orderbook = {
                        "type": "subscribe", 
                        "channel": "v4_orderbook",
                        "id": self.symbol
                    }
                    
                    await ws.send(json.dumps(subscribe_trades))
                    await ws.send(json.dumps(subscribe_orderbook))
                    
                    # 同時運行 WebSocket 監聽和 REST 輪詢
                    async def rest_poller():
                        while self.running:
                            now = time.time()
                            if now - self._last_rest_fetch >= self._rest_fetch_interval:
                                await self._fetch_rest_data()
                                self._last_rest_fetch = now
                            if now - self._candles_last_fetch >= 15:  # 🔧 v12.10: 每15秒更新 K 線 (快速偵測)
                                await self._fetch_candles()
                                self._candles_last_fetch = now
                            await asyncio.sleep(0.5)
                    
                    await asyncio.gather(
                        self._handle_ws_message(ws),
                        rest_poller()
                    )
                    
            except Exception as e:
                if self.running:
                    logging.warning(f"dYdX WebSocket 重連中... {e}")
                    # 在重連期間使用純 REST
                    await self._fetch_rest_data()
                    await asyncio.sleep(2)
    
    def start(self):
        """啟動數據接收"""
        self.running = True
        
        # 🔧 v13.0: 優先使用 Data Hub
        if self._use_hub and self._hub:
            # 🔧 v14.9: 啟動前清除舊快取避免價格延遲
            cache_file = Path("/tmp/dydx_data_hub.json")
            lock_file = Path("/tmp/dydx_data_hub.lock")
            if cache_file.exists() or lock_file.exists():
                try:
                    if cache_file.exists():
                        cache_file.unlink()
                    if lock_file.exists():
                        lock_file.unlink()
                    print("🧹 已清除舊 DataHub 快取")
                except Exception as e:
                    logging.warning(f"清除快取失敗: {e}")
            
            self._hub.start()
            
            # 啟動同步線程
            def sync_loop():
                while self.running:
                    self._sync_from_hub()
                    time.sleep(0.05)  # 50ms 同步一次
            
            self._ws_thread = threading.Thread(target=sync_loop, daemon=True)
            self._ws_thread.start()
            
            data = self._hub.get_data()
            role = "🔑 Master" if data.master_pid == os.getpid() else "👥 Consumer"
            print(f"✅ dYdX WebSocket 已啟動 ({role})")
            print(f"   📡 純 WebSocket 數據流 (無 REST 輪詢)")
            print(f"   💾 本機快取共享: /tmp/dydx_data_hub.json")
            return
        
        # 舊模式: REST 輪詢
        def run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_ws())
        
        self._ws_thread = threading.Thread(target=run_loop, daemon=True)
        self._ws_thread.start()
        
        if hasattr(self, '_rate_limiter') and self._rate_limiter:
            stats = self._rate_limiter.get_stats()
            print(f"✅ dYdX WebSocket 已啟動 (舊模式: REST 輪詢)")
            print(f"   🔒 共享速率限制: {stats['active_processes']} 個進程活躍")
        else:
            print("✅ dYdX WebSocket 已啟動 (舊模式: REST 輪詢)")
    
    def stop(self):
        """停止數據接收"""
        self.running = False
        
        # 🔧 v13.0: 停止 Data Hub
        if self._use_hub and self._hub:
            self._hub.stop()
        
        # 舊模式: 清理速率限制器
        if hasattr(self, '_rate_limiter') and self._rate_limiter:
            self._rate_limiter.cleanup()
        
        print("⏹️ dYdX WebSocket 已停止")
    
    def get_obi(self, use_ema: bool = True) -> float:
        """
        計算訂單簿失衡 (Order Book Imbalance)
        
        dYdX 訂單簿深度較淺，使用 EMA 平滑減少雜訊
        
        Args:
            use_ema: 是否使用 EMA 平滑 (預設 True)
        """
        if not self.bids or not self.asks:
            return self._obi_ema if use_ema else 0.0
        
        # 使用更多層級 (10層) 來計算，減少單一大單的影響
        depth = min(10, len(self.bids), len(self.asks))
        bid_vol = sum(q for _, q in self.bids[:depth])
        ask_vol = sum(q for _, q in self.asks[:depth])
        
        if bid_vol + ask_vol == 0:
            return self._obi_ema if use_ema else 0.0
        
        raw_obi = (bid_vol - ask_vol) / (bid_vol + ask_vol)
        
        # EMA 平滑處理
        if use_ema:
            self._obi_history.append(raw_obi)
            if self._obi_ema == 0.0 and len(self._obi_history) > 0:
                # 初始化：使用第一個值
                self._obi_ema = raw_obi
            else:
                # EMA 更新：new_ema = alpha * current + (1-alpha) * old_ema
                self._obi_ema = self._obi_ema_alpha * raw_obi + (1 - self._obi_ema_alpha) * self._obi_ema
            return self._obi_ema
        
        return raw_obi
    
    def get_trade_imbalance_1s(self, window_sec: int = 30) -> float:
        """
        計算買賣不平衡 (dYdX 交易頻率較低，預設使用 30 秒窗口)
        
        Args:
            window_sec: 時間窗口秒數，預設 30 秒 (dYdX 適配)
        """
        now = time.time() * 1000
        window_ms = window_sec * 1000
        try:
            trades_copy = list(self.trades_1s)
            recent = [t for t in trades_copy if now - t['time'] < window_ms]
        except RuntimeError:
            return 0.0
        
        buy_vol = sum(t['qty'] for t in recent if t['is_buy'])
        sell_vol = sum(t['qty'] for t in recent if not t['is_buy'])
        
        total = buy_vol + sell_vol
        if total == 0:
            return 0.0
        
        return (buy_vol - sell_vol) / total
    
    def get_price_change(self, seconds: int) -> float:
        """計算 N 秒價格變化 %"""
        now = time.time() * 1000
        trades_copy = list(self.trades_1m)
        if not trades_copy or self.current_price == 0:
            return 0.0
        
        target_time = now - seconds * 1000
        candidate_price = None
        for t in reversed(trades_copy):
            if t['time'] <= target_time:
                candidate_price = t['price']
                break
        
        if candidate_price is None:
            candidate_price = trades_copy[0]['price']
        
        if candidate_price == 0:
            return 0.0
        return (self.current_price - candidate_price) / candidate_price * 100
    
    def get_big_trades_stats(self, seconds: int = 60) -> Dict:
        """獲取大單統計"""
        now = time.time() * 1000
        try:
            big_trades_copy = list(self.big_trades)
            recent_big = [t for t in big_trades_copy if now - t['time'] < seconds * 1000]
        except RuntimeError:
            recent_big = []
        
        big_buy = [t for t in recent_big if t['is_buy']]
        big_sell = [t for t in recent_big if not t['is_buy']]
        
        # 方向穩定性分析
        direction_changes = 0
        last_dominant_direction = None
        direction_stable_since = now
        
        if recent_big:
            sorted_trades = sorted(recent_big, key=lambda t: t['time'])
            slice_duration_ms = 5000
            time_slices = {}
            
            for trade in sorted_trades:
                slice_key = int(trade['time'] // slice_duration_ms)
                if slice_key not in time_slices:
                    time_slices[slice_key] = {'buy_value': 0, 'sell_value': 0}
                if trade['is_buy']:
                    time_slices[slice_key]['buy_value'] += trade['value_usdt']
                else:
                    time_slices[slice_key]['sell_value'] += trade['value_usdt']
            
            for slice_key in sorted(time_slices.keys()):
                slice_data = time_slices[slice_key]
                total = slice_data['buy_value'] + slice_data['sell_value']
                if total > 0:
                    buy_ratio = slice_data['buy_value'] / total
                    if buy_ratio > 0.6:
                        current_dir = "LONG"
                    elif buy_ratio < 0.4:
                        current_dir = "SHORT"
                    else:
                        current_dir = "NEUTRAL"
                    
                    if last_dominant_direction and current_dir != "NEUTRAL" and last_dominant_direction != current_dir:
                        direction_changes += 1
                        direction_stable_since = slice_key * slice_duration_ms
                    
                    if current_dir != "NEUTRAL":
                        last_dominant_direction = current_dir
        
        stable_duration_ms = now - direction_stable_since
        
        return {
            'big_trade_count': len(recent_big),
            'big_buy_count': len(big_buy),
            'big_sell_count': len(big_sell),
            'big_buy_volume': sum(t['qty'] for t in big_buy),
            'big_sell_volume': sum(t['qty'] for t in big_sell),
            'big_buy_value': sum(t['value_usdt'] for t in big_buy),
            'big_sell_value': sum(t['value_usdt'] for t in big_sell),
            'recent_big_trades': recent_big[-5:],
            'direction_changes': direction_changes,
            'stable_duration_sec': stable_duration_ms / 1000,
            'last_direction': last_dominant_direction,
        }
    
    def get_full_snapshot(self) -> Dict:
        """獲取完整市場快照"""
        big_stats = self.get_big_trades_stats()
        
        return {
            'timestamp': datetime.now().isoformat(),
            'price': self.current_price,
            'bid': self.bid_price,
            'ask': self.ask_price,
            'spread': self.ask_price - self.bid_price if self.bid_price > 0 else 0,
            'obi': self.get_obi(),
            'trade_imbalance_1s': self.get_trade_imbalance_1s(),
            'price_change_1m': self.get_price_change(60),
            'price_change_5m': self.get_price_change(300),
            'bid_depth': sum(q for _, q in self.bids[:5]) if self.bids else 0,
            'ask_depth': sum(q for _, q in self.asks[:5]) if self.asks else 0,
            **big_stats
        }


# ============================================================
# WebSocket 數據接收器 (Binance - 保留作為備援)
# ============================================================

class BinanceWebSocket:
    """
    Binance WebSocket 數據接收器 (備援用)
    注意: 主要使用 DydxWebSocket，此類別保留作為備援
    """
    
    def __init__(self, symbol: str = "btcusdt", use_testnet: bool = True):
        self.symbol = symbol.lower()
        self.use_testnet = use_testnet
        
        # 注意: Testnet 沒有公開的 WebSocket，統一使用正式網
        # 正式網和 Testnet 的價格通常差異很小 (< $10)
        # 持倉的盈虧計算會使用 Testnet REST API 的 markPrice
        base_url = "wss://fstream.binance.com"
        
        self.ws_url = f"{base_url}/ws/{self.symbol}@aggTrade"
        self.depth_url = f"{base_url}/ws/{self.symbol}@depth5@100ms"
        
        # 數據存儲
        self.current_price = 0.0
        self.bid_price = 0.0
        self.ask_price = 0.0
        self.last_trade_time = 0
        self.trades_1s: deque = deque(maxlen=100)  # 最近 1 秒交易
        self.trades_1m: deque = deque(maxlen=6000) # 最近 1 分鐘交易
        
        # 訂單簿
        self.bids: List[List[float]] = []  # [[price, qty], ...]
        self.asks: List[List[float]] = []
        
        # 大單追蹤 (>$8K USDT) - 拆單識別優化 (Iceberg Detection)
        self.big_trades: deque = deque(maxlen=100)  # 最近大單
        self.big_trade_threshold = 8000  # User Request: 8K for split order detection
        
        # 統計
        self.buy_volume_1s = 0.0
        self.sell_volume_1s = 0.0
        
        # 控制
        self.running = False
        self._ws_thread = None
    
    async def _handle_agg_trade(self, ws):
        """處理逐筆成交數據"""
        async for message in ws:
            if not self.running:
                break
            try:
                data = json.loads(message)
                price = float(data['p'])
                qty = float(data['q'])
                is_buyer_maker = data['m']  # True = 賣方主動, False = 買方主動
                trade_time = data['T']
                
                self.current_price = price
                self.last_trade_time = trade_time
                
                value_usdt = price * qty
                
                trade = {
                    'price': price,
                    'qty': qty,
                    'is_buy': not is_buyer_maker,
                    'time': trade_time,
                    'value_usdt': value_usdt
                }
                self.trades_1s.append(trade)
                self.trades_1m.append(trade)
                
                # 追蹤大單
                if value_usdt >= self.big_trade_threshold:
                    self.big_trades.append(trade)
                
            except Exception as e:
                pass
    
    async def _handle_depth(self, ws):
        """處理訂單簿數據"""
        async for message in ws:
            if not self.running:
                break
            try:
                data = json.loads(message)
                self.bids = [[float(p), float(q)] for p, q in data.get('b', [])]
                self.asks = [[float(p), float(q)] for p, q in data.get('a', [])]
                
                if self.bids:
                    self.bid_price = self.bids[0][0]
                if self.asks:
                    self.ask_price = self.asks[0][0]
                    
            except Exception as e:
                pass
    
    async def _run_ws(self):
        """運行 WebSocket"""
        while self.running:
            try:
                async with websockets.connect(self.ws_url) as ws_trade:
                    async with websockets.connect(self.depth_url) as ws_depth:
                        await asyncio.gather(
                            self._handle_agg_trade(ws_trade),
                            self._handle_depth(ws_depth)
                        )
            except Exception as e:
                if self.running:
                    print(f"⚠️ WebSocket 重連中... {e}")
                    await asyncio.sleep(1)
    
    def start(self):
        """啟動 WebSocket"""
        self.running = True
        
        def run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_ws())
        
        self._ws_thread = threading.Thread(target=run_loop, daemon=True)
        self._ws_thread.start()
        print("✅ WebSocket 已啟動")
    
    def stop(self):
        """停止 WebSocket"""
        self.running = False
        print("⏹️ WebSocket 已停止")
    
    def get_obi(self) -> float:
        """計算訂單簿失衡 (Order Book Imbalance)"""
        if not self.bids or not self.asks:
            return 0.0
        
        bid_vol = sum(q for _, q in self.bids[:5])
        ask_vol = sum(q for _, q in self.asks[:5])
        
        if bid_vol + ask_vol == 0:
            return 0.0
        
        return (bid_vol - ask_vol) / (bid_vol + ask_vol)
    
    def get_trade_imbalance_1s(self) -> float:
        """計算 1 秒內買賣不平衡"""
        now = time.time() * 1000
        # 🔧 修復：先複製資料再迭代，避免 RuntimeError: deque mutated during iteration
        try:
            trades_copy = list(self.trades_1s)
            recent = [t for t in trades_copy if now - t['time'] < 1000]
        except RuntimeError:
            return 0.0
        
        buy_vol = sum(t['qty'] for t in recent if t['is_buy'])
        sell_vol = sum(t['qty'] for t in recent if not t['is_buy'])
        
        total = buy_vol + sell_vol
        if total == 0:
            return 0.0
        
        return (buy_vol - sell_vol) / total
    
    def get_price_change(self, seconds: int) -> float:
        """計算 N 秒價格變化 %"""
        now = time.time() * 1000
        trades_copy = list(self.trades_1m)
        if not trades_copy or self.current_price == 0:
            return 0.0
        
        # 目標時間點 = N 秒前，找「最接近且不晚於」目標時間的成交價
        # 🔧 v10.19 fix5: 使用逆序迭代（新→舊），找到第一個 <= target 的就是最接近的
        target_time = now - seconds * 1000
        candidate_price = None
        for t in reversed(trades_copy):
            if t['time'] <= target_time:
                candidate_price = t['price']
                break
        # 若資料尚未累積到 N 秒，退而取最早一筆成交作為基準
        if candidate_price is None:
            candidate_price = trades_copy[0]['price']
        
        if candidate_price == 0:
            return 0.0
        return (self.current_price - candidate_price) / candidate_price * 100
    
    def get_big_trades_stats(self, seconds: int = 60) -> Dict:
        """
        獲取大單統計 (用於 TensorFlow)
        v10.7: 增加方向穩定性分析
        """
        now = time.time() * 1000
        # 🔧 修復：先複製資料再迭代，避免 RuntimeError
        try:
            big_trades_copy = list(self.big_trades)
            recent_big = [t for t in big_trades_copy if now - t['time'] < seconds * 1000]
        except RuntimeError:
            recent_big = []
        
        big_buy = [t for t in recent_big if t['is_buy']]
        big_sell = [t for t in recent_big if not t['is_buy']]
        
        # v10.7: 分析方向變化次數 (用於穩定性檢查)
        direction_changes = 0
        last_dominant_direction = None
        direction_stable_since = now
        
        # 按時間排序分析方向變化
        if recent_big:
            sorted_trades = sorted(recent_big, key=lambda t: t['time'])
            
            # 每 5 秒切片分析主導方向
            slice_duration_ms = 5000  # 5 秒
            time_slices = {}
            for trade in sorted_trades:
                slice_key = int(trade['time'] // slice_duration_ms)
                if slice_key not in time_slices:
                    time_slices[slice_key] = {'buy_value': 0, 'sell_value': 0}
                if trade['is_buy']:
                    time_slices[slice_key]['buy_value'] += trade['value_usdt']
                else:
                    time_slices[slice_key]['sell_value'] += trade['value_usdt']
            
            # 計算方向變化次數
            for slice_key in sorted(time_slices.keys()):
                slice_data = time_slices[slice_key]
                total = slice_data['buy_value'] + slice_data['sell_value']
                if total > 0:
                    buy_ratio = slice_data['buy_value'] / total
                    if buy_ratio > 0.6:
                        current_dir = "LONG"
                    elif buy_ratio < 0.4:
                        current_dir = "SHORT"
                    else:
                        current_dir = "NEUTRAL"
                    
                    if last_dominant_direction and current_dir != "NEUTRAL" and last_dominant_direction != current_dir:
                        direction_changes += 1
                        direction_stable_since = slice_key * slice_duration_ms
                    
                    if current_dir != "NEUTRAL":
                        last_dominant_direction = current_dir
        
        # 計算穩定時間 (毫秒)
        stable_duration_ms = now - direction_stable_since
        
        return {
            'big_trade_count': len(recent_big),
            'big_buy_count': len(big_buy),
            'big_sell_count': len(big_sell),
            'big_buy_volume': sum(t['qty'] for t in big_buy),
            'big_sell_volume': sum(t['qty'] for t in big_sell),
            'big_buy_value': sum(t['value_usdt'] for t in big_buy),
            'big_sell_value': sum(t['value_usdt'] for t in big_sell),
            'recent_big_trades': recent_big[-5:],  # 最近 5 筆大單
            # v10.7: 穩定性指標
            'direction_changes': direction_changes,
            'stable_duration_sec': stable_duration_ms / 1000,
            'last_direction': last_dominant_direction,
        }
    
    def get_full_snapshot(self) -> Dict:
        """
        獲取完整市場快照 (用於 TensorFlow 記錄)
        """
        big_stats = self.get_big_trades_stats()
        
        return {
            'timestamp': datetime.now().isoformat(),
            'price': self.current_price,
            'bid': self.bid_price,
            'ask': self.ask_price,
            'spread': self.ask_price - self.bid_price if self.bid_price > 0 else 0,
            'obi': self.get_obi(),
            'trade_imbalance_1s': self.get_trade_imbalance_1s(),
            'price_change_1m': self.get_price_change(60),
            'price_change_5m': self.get_price_change(300),
            # 訂單簿深度
            'bid_depth': sum(q for _, q in self.bids[:5]) if self.bids else 0,
            'ask_depth': sum(q for _, q in self.asks[:5]) if self.asks else 0,
            # 大單資料
            **big_stats
        }

# ============================================================
# Testnet 交易執行器
# ============================================================

class TestnetTrader:
    """
    交易執行器
    支援兩種模式：
    1. paper_mode=True: 模擬交易 (不需要 API)
    2. paper_mode=False: 正式網真實交易
    3. dydx_sync_mode=True: 同步到 dYdX 真實交易 (Aggressive Maker)
    """
    
    def __init__(self, config: TradingConfig):
        self.config = config
        self.paper_mode = config.paper_mode
        
        # 🆕 v10.9 兩階段止盈止損管理器
        self._two_phase_exit_manager = TwoPhaseExitManager(config) if config.two_phase_exit_enabled else None
        
        if not self.paper_mode:
            # 使用直接 API 代替 ccxt (ccxt sandbox 已棄用)
            self.testnet_api = BinanceTestnetAPI()
            self.exchange = None  # 不再使用 ccxt
            print("✅ 使用 Testnet 直接 API")
        else:
            self.testnet_api = None
            self.exchange = None
            print("📝 模擬交易模式 (不需要 API)")
        
        # 🆕 dYdX 同步交易 (Aggressive Maker)
        self.dydx_api: Optional['DydxAPI'] = None
        self.dydx_real_position: Optional[Dict] = None  # 追蹤 dYdX 真實倉位
        # 🔧 v14.6.10: 確保 dydx_sync_enabled 是 bool 而非 None
        self.dydx_sync_enabled = bool(config.dydx_sync_mode) if config.dydx_sync_mode else False
        self.dydx_initial_balance: Optional[float] = None  # 🆕 dYdX 起始餘額
        # 🆕 v14.6.35: 記錄啟動時間，用於統計只計算本次運行的交易
        self._session_start_time: str = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        # 🆕 v14.12.1: 初始化 logger
        import logging
        self.logger = logging.getLogger("WhaleTrader")

        # 🧠 v14.6.14: dYdX 掛單記憶（避免只追蹤「最新一張」導致漏取消）
        # key: order_id(int) -> {order_type, market, created_ts, kind}
        self._dydx_order_registry: Dict[int, Dict] = {}
        # JSONL 記憶日誌（可追查掛單/取消順序是否打結）
        self._dydx_order_journal_path = Path("logs/dydx_order_journal.jsonl")
        # 每次啟動唯一識別碼（方便串起同一次執行的所有事件）
        self._dydx_run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
        self._dydx_journal_failed_once = False
        # 讓 journal 檔案一定會生成（即使沒有成交/沒有掛單）
        self._journal_dydx_event(
            "run_start",
            run_id=self._dydx_run_id,
            paper_mode=self.paper_mode,
            dydx_sync_enabled=self.dydx_sync_enabled,
            argv=list(getattr(sys, 'argv', [])),
        )
        # 429 緩解 / 快取
        self._dydx_pos_cache: Optional[List[Dict]] = None
        self._dydx_pos_cache_time: float = 0.0
        self._dydx_pos_backoff_until: float = 0.0
        self._dydx_market_cache: Optional[Dict] = None
        self._dydx_market_cache_time: float = 0.0
        self._dydx_market_backoff_until: float = 0.0

        # 🆕 v12.0 預掛單狀態追蹤（需在 dYdX 啟動清理前初始化）
        self.pending_entry_order: Optional[Dict] = None  # 待成交的進場掛單
        self.pending_tp_order: Optional[Dict] = None     # 待成交的止盈掛單
        self.pending_sl_order: Optional[Dict] = None     # 🆕 v14.6: 待成交的止損掛單
        self._pending_sl_update: Optional[Dict] = None   # 🆕 v14.6.30: 待執行的止損更新
        self.pre_entry_mode = config.pre_entry_mode      # 是否啟用預掛單模式

        if self.dydx_sync_enabled and DYDX_WHALE_AVAILABLE:
            self._init_dydx_sync()
        
        # 交易記錄
        self.trades: List[TradeRecord] = []
        self.active_trade: Optional[TradeRecord] = None

        # 🆕 v14.6.31: dYdX 掛單/取消節流與退避
        # 避免取消失敗時仍持續新掛單，導致「未平倉訂單 10 筆上限」與 block rate limit。
        self._dydx_tx_backoff_until: float = 0.0
        self._last_sl_update_attempt_ts: float = 0.0
        self._last_sl_update_stop_pct: Optional[float] = None
        self._dydx_sl_missing_since: Optional[float] = None
        self._last_sl_missing_attempt_ts: float = 0.0
        self._last_dydx_resync_open_ts: float = 0.0
        self._sl_update_inflight: bool = False
        self._last_sl_update_exec_ts: float = 0.0
        self._last_sl_update_exec_stop_pct: Optional[float] = None
        self._last_dydx_bracket_sweep_ts: float = 0.0
        
        # 外部持倉緩存 (避免頻繁查詢 API)
        self._external_position_cache: Optional[Dict] = None
        self._external_position_cache_time: float = 0
        self._external_position_cache_ttl: float = 0.0  # 不緩存，每次都獲取最新數據
        
        # 🆕 Paper Trading 資金追蹤
        self.initial_balance: float = 100.0  # 起始資金 100 USDT
        self.current_balance: float = 100.0  # 當前資金
        
        # 🆕 10U Test: 顯示餘額調整
        self.balance_deduction = config.base_balance_deduct
        self.real_balance_cache: float = 0.0
        self.real_pnl_cache: float = 0.0
        self.dydx_oracle_price_cache: float = 0.0  # 🔧 v14.6.21: dYdX Oracle Price 緩存供 Dashboard 使用
        
        # 統計
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.last_trade_time = 0
        self.win_count = 0
        self.loss_count = 0
        self.consecutive_losses = 0           # 🆕 連續虧損計數
        self.cooldown_until: float = 0        # 🆕 連續虧損冷卻截止時間
        self.last_loss_time: float = 0        # 🆕 上次虧損時間
        self.session_start_time = datetime.now()

        # 🆕 v14.1: 強制平衡隨機進場 (每 N 筆保證 50/50)
        self._balanced_batch_size: int = _coerce_int(getattr(self.config, "random_entry_balance_batch_size", 20), default=20) or 20
        if self._balanced_batch_size < 2:
            self._balanced_batch_size = 20
        if self._balanced_batch_size % 2 != 0:
            self._balanced_batch_size += 1
        self._balanced_prefill_size: int = _coerce_int(getattr(self.config, "random_entry_balance_prefill_size", 30), default=30) or 30
        self._balanced_max_streak: int = _coerce_int(getattr(self.config, "random_entry_balance_max_streak", 3), default=3) or 0
        self._balanced_max_imbalance: int = _coerce_int(getattr(self.config, "random_entry_balance_max_imbalance", 4), default=4) or 0
        self._random_wave1: List[str] = []
        self._random_wave2: List[str] = []
        self._random_active_wave: int = 1

        # 日誌
        mode_suffix = "paper" if self.paper_mode else "live"
        self.log_dir = Path(f"logs/whale_{mode_suffix}_trader")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        session_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.trades_file = self.log_dir / f"trades_{session_timestamp}.json"
        # 🆕 獨立的信號記錄檔 (記錄所有信號，包括被拒絕的)
        self.signals_file = self.log_dir / f"signals_{session_timestamp}.json"
        self._signal_logs = []  # 信號日誌列表
        
        # 🆕 每次啟動都重新開始，不載入舊記錄
        print(f"📝 新交易會話開始，起始資金: ${self.initial_balance:.2f} USDT")
        
        # 🆕 信號橋接器 (用於連接真實交易系統)
        self.signal_bridge = None
        self.signal_bridge_enabled = False
        if SIGNAL_BRIDGE_AVAILABLE and self.paper_mode:
            try:
                self.signal_bridge = WhaleSignalBridge.get_instance()
                self.signal_bridge.start_server()
                self.signal_bridge_enabled = True
                print("🔌 信號橋接器已啟動 (等待真實交易系統連接)")
            except Exception as e:
                print(f"⚠️ 信號橋接器啟動失敗: {e}")
        
        # 🆕 v13.4 策略卡片系統
        self.card_manager = None
        self.card_system_enabled = getattr(config, 'strategy_card_enabled', False)
        if self.card_system_enabled:
            try:
                from scripts.strategy_card_manager import StrategyCardManager
                self.card_manager = StrategyCardManager()
                # 啟用預設卡片組合
                default_entry = getattr(config, 'default_entry_card', None) or 'six_dim_strict'
                default_exit = getattr(config, 'default_exit_card', None) or 'lock_profit'
                default_risk = getattr(config, 'default_risk_card', None) or 'adaptive'
                self.card_manager.activate_combination(default_entry, default_exit, default_risk, "startup")
                print(f"🎴 策略卡片系統已啟用")
                print(self.card_manager.show_active_cards())
            except Exception as e:
                print(f"⚠️ 策略卡片系統初始化失敗: {e}")
    
    def apply_card_parameters(self, market_data: Optional[Dict] = None) -> bool:
        """
        🎴 應用策略卡片參數到 config
        
        如果啟用了自動切換，會根據市場狀態選擇最佳卡片
        
        Args:
            market_data: 市場數據 (用於自動切換判斷)
        
        Returns:
            是否有切換卡片
        """
        if not self.card_manager:
            return False
        
        # 自動選擇卡片
        switched = False
        if self.config.auto_card_switch and market_data:
            new_combo = self.card_manager.auto_select_cards(market_data)
            if new_combo:
                switched = True
                print(f"\n🔄 自動切換卡片: {new_combo.regime}")
        
        # 獲取合併參數並應用到 config
        params = self.card_manager.get_merged_parameters()
        for key, value in params.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        
        return switched
    
    def record_card_trade_result(self, is_win: bool, pnl: float):
        """記錄交易結果到卡片系統"""
        if self.card_manager:
            self.card_manager.record_trade_result(is_win, pnl)

    # 🆕 dYdX 真實 PnL 驗證
    async def _update_dydx_real_position(self):
        """
        Fetch real dYdX position and update balance/PnL for verification
        
        🔧 v14.4: 使用緩存減少 API 呼叫
        - Balance: 緩存 5 秒
        - Positions: 使用 _get_dydx_positions_with_cache (3 秒緩存)
        - Price: 優先使用 WebSocket
        """
        if not self.dydx_api:
            return

        try:
            # ========== 🔧 v14.4: Balance 緩存 5 秒 ==========
            now = time.time()
            if now - getattr(self, '_dydx_balance_time', 0) < 5.0:
                real_total_equity = getattr(self, '_dydx_balance_cache', 0)
            else:
                real_total_equity = await self.dydx_api.get_account_balance()
                self._dydx_balance_cache = real_total_equity
                self._dydx_balance_time = now
            
            if self.dydx_initial_balance is None and real_total_equity > 0:
                self.dydx_initial_balance = real_total_equity
                
            self.real_balance_cache = max(0, real_total_equity - self.balance_deduction)

            # 2. Get Positions (快取 + 429 backoff)
            positions = await self._get_dydx_positions_with_cache()
            real_pnl = 0.0
            
            # 🔧 v14.6.22: 每次都更新 Oracle Price (不只有持倉時)
            try:
                oracle_price = await self.dydx_api.get_price()
                if oracle_price and oracle_price > 0:
                    self.dydx_oracle_price_cache = oracle_price
            except Exception:
                pass
            
            # Find BTC position
            current_price = self.dydx_oracle_price_cache or 0
            for pos in positions:
                if pos.get('market') == 'BTC-USD':
                    # Calculate unrealized PnL safely
                    entry_price = float(pos.get('entryPrice', 0))
                    size = float(pos.get('size', 0))
                    side = pos.get('side', '')
                    
                    # 使用已更新的 Oracle Price
                    if current_price <= 0 and hasattr(self, 'ws') and self.ws and self.ws.current_price > 0:
                        # Fallback: 使用 WS 價格
                        current_price = self.ws.current_price
                    
                    if size > 0 and entry_price > 0 and current_price > 0:
                        if side == 'LONG':
                            real_pnl = (current_price - entry_price) * size
                        else:
                            real_pnl = (entry_price - current_price) * size
                    break
            
            self.real_pnl_cache = real_pnl
            
            # 3. Log PnL Comparison (only if we have an active paper trade)
            if self.active_trade:
                paper_pnl = self.active_trade.unrealized_pnl
                diff = real_pnl - paper_pnl
                # If difference > $0.5, log a warning
                if abs(diff) > 0.5:
                   # self.logger.warning(f"⚠️ PnL Diff: Real ${real_pnl:.2f} vs Paper ${paper_pnl:.2f} (Diff: ${diff:.2f})")
                   pass

        except Exception as e:
            # self.logger.warning(f"Failed to update dYdX real position: {e}")
            pass

    async def stop_dydx_trading(self, reason: str):
        """
        停止 dYdX 同步交易：
        - 清掃未平倉訂單
        - 若有持倉則以緊急/止損方式平倉 (IOC)
        - 關閉 sync flag，避免後續再下單
        """
        if not (self.dydx_sync_enabled and self.dydx_api):
            return

        try:
            await self._dydx_sweep_open_orders(reason=f"stop_trading:{reason}", market="BTC-USD")
        except Exception:
            pass

        try:
            live_pos = None
            try:
                positions = await self.dydx_api.get_positions()
            except Exception:
                positions = []
            for pos in positions or []:
                if pos.get("market") != "BTC-USD":
                    continue
                raw_size = _coerce_float(pos.get("size", 0.0), default=0.0)
                if abs(raw_size) <= 0.0001:
                    continue
                live_pos = pos
                break

            if live_pos:
                raw_size = _coerce_float(live_pos.get("size", 0.0), default=0.0)
                entry_price = _coerce_float(live_pos.get("entryPrice", 0.0), default=0.0)
                self.dydx_real_position = {
                    "side": "LONG" if raw_size > 0 else "SHORT",
                    "size": abs(raw_size),
                    "entry_price": entry_price,
                    "entry_time": datetime.now(),
                }

            if self.dydx_real_position and abs(_coerce_float(self.dydx_real_position.get("size", 0.0), default=0.0)) > 0.0001:
                await self._dydx_close_position(reason=reason, is_stop_loss=True)
        except Exception:
            pass

        self.dydx_sync_enabled = False
        try:
            self.config.dydx_sync_mode = False
        except Exception:
            pass

    async def reconcile_dydx_position(self, paper_has_position: bool, current_price: float, market_data: Optional[Dict] = None):
        """
        🆕 v13.3 增強版: 確保 dYdX 實倉與紙本倉一致
        🔧 v14.6.11: 優先使用 WebSocket 持倉數據 (更即時)
        
        場景處理:
        1. 紙本無倉 + dYdX 有倉 → 以 Paper 為主，優先平掉 dYdX
        2. 紙本有倉 + dYdX 無倉 → 以 Paper 為主，嘗試補開 dYdX
        
        Args:
            paper_has_position: 紙本是否有持倉
            current_price: 當前價格
            market_data: 可選的市場數據，用於判斷是否應該同步進場
        """
        if not self.dydx_sync_enabled or not self.dydx_api:
            return

        # 🆕 v14.6.11: 優先使用 WebSocket 持倉數據 (更即時)
        ws_position = None
        if hasattr(self, 'dydx_ws') and self.dydx_ws:
            ws_pos = self.dydx_ws.get_position("BTC-USD")
            if ws_pos:
                ws_position = {
                    'market': 'BTC-USD',
                    'size': ws_pos['raw_size'],  # 保持正負號
                    'entryPrice': ws_pos['entry_price']
                }
                # WebSocket 數據比 REST API 新鮮，優先使用
                ws_age = time.time() - self.dydx_ws.position_updated
                if ws_age < 10:  # WebSocket 數據 10 秒內有效
                    pass  # 使用 WebSocket 數據
                else:
                    ws_position = None  # WebSocket 數據過舊，回退到 REST API

        # 取得持倉數據：WebSocket 優先，REST API 兜底
        if ws_position:
            positions = [ws_position]
        else:
            positions = await self._get_dydx_positions_with_cache()
        
        live_pos = None
        for pos in positions:
            # 🔧 v14.6.2: 修復 - size 可能是負數 (SHORT)，用 abs() 檢測
            if pos.get('market') == 'BTC-USD' and abs(float(pos.get('size', 0))) > 0.0001:
                live_pos = pos
                break

        paper_master = bool(getattr(self.config, "dydx_paper_master", False))

        if paper_has_position or not live_pos:
            if getattr(self, "_dydx_desync_since", None) is not None:
                self._dydx_desync_since = None

        if not paper_has_position and live_pos:
            if paper_master:
                if getattr(self, "_dydx_desync_since", None) is None:
                    self._dydx_desync_since = time.time()

                now_ts = time.time()
                desync_since = getattr(self, "_dydx_desync_since", now_ts) or now_ts
                desync_age = now_ts - desync_since
                desync_close_sec = _coerce_float(getattr(self.config, "dydx_desync_close_sec", 120.0), default=120.0)

                has_conditional = False
                local_has_conditional = bool(self.pending_sl_order)
                registry_has_conditional = False
                try:
                    registry_has_conditional = any(
                        meta.get("order_type") == "CONDITIONAL"
                        for meta in getattr(self, "_dydx_order_registry", {}).values()
                    )
                except Exception:
                    registry_has_conditional = False
                try:
                    conditional_orders = await self._get_open_conditional_orders("BTC-USD")
                    has_conditional = bool(conditional_orders)
                except Exception:
                    has_conditional = False
                has_conditional = bool(has_conditional or local_has_conditional or registry_has_conditional)

                if has_conditional and desync_age < desync_close_sec:
                    print(f"   ⏸️ 殘留倉位有條件單，延後清理 ({desync_age:.0f}s/{desync_close_sec:.0f}s)")
                    return

                raw_size = float(live_pos.get('size', 0))
                if abs(raw_size) > 0.0001 and self.dydx_api:
                    print(f"   🔄 [PaperMaster] dYdX 有倉但 Paper 無倉，平倉同步 {raw_size:.4f} BTC...")
                    try:
                        ok, fill_price = await self._dydx_close_position(reason="paper_master_desync", is_stop_loss=True)
                        if ok:
                            print(f"   ✅ dYdX 殘留倉位已清理 @ ${fill_price:,.2f}")
                            self.dydx_real_position = None
                            try:
                                self.pending_tp_order = None
                                self.pending_sl_order = None
                                self._dydx_order_registry.clear()
                            except Exception:
                                pass
                        else:
                            print("   ⚠️ dYdX 殘留倉位清理失敗，稍後重試")
                    except Exception as e:
                        print(f"   ❌ dYdX 殘留倉位清理異常: {e}")
                return
            if getattr(self, "_dydx_desync_since", None) is None:
                self._dydx_desync_since = time.time()
            # 🔧 v14.9.12: 防止止損後立即同步 (避免無限循環)
            # 檢查是否在止損冷卻期內
            last_emergency_ts = getattr(self, '_last_emergency_stop_ts', 0.0)
            now_ts = time.time()
            emergency_cooldown_sec = 60.0  # 🔧 v14.9.12: 延長到 60 秒 (原 30 秒不夠)
            
            if now_ts - last_emergency_ts < emergency_cooldown_sec:
                remaining = emergency_cooldown_sec - (now_ts - last_emergency_ts)
                # 節流日誌：每 10 秒輸出一次
                last_log_ts = getattr(self, '_last_sync_cooldown_log_ts', 0.0)
                if now_ts - last_log_ts >= 10.0:
                    self._last_sync_cooldown_log_ts = now_ts
                    print(f"⏳ [Sync] 止損冷卻中，{remaining:.0f}秒後可同步 (防止 SYNC 無限循環)")
                    desync_since = getattr(self, "_dydx_desync_since", now_ts) or now_ts
                    desync_age = now_ts - desync_since
                    desync_close_sec = _coerce_float(getattr(self.config, "dydx_desync_close_sec", 120.0), default=120.0)

                    has_conditional = False
                    local_has_conditional = bool(self.pending_sl_order)
                    registry_has_conditional = False
                    try:
                        registry_has_conditional = any(
                            meta.get("order_type") == "CONDITIONAL"
                            for meta in getattr(self, "_dydx_order_registry", {}).values()
                        )
                    except Exception:
                        registry_has_conditional = False
                    try:
                        conditional_orders = await self._get_open_conditional_orders("BTC-USD")
                        has_conditional = bool(conditional_orders)
                    except Exception:
                        has_conditional = False
                    has_conditional = bool(has_conditional or local_has_conditional or registry_has_conditional)

                    if has_conditional and desync_age < desync_close_sec:
                        print(f"   ⏸️ 殘留倉位有條件單，延後清理 ({desync_age:.0f}s/{desync_close_sec:.0f}s)")
                        return
                    if has_conditional and desync_age >= desync_close_sec:
                        print(f"   ⚠️ 殘留倉位超時 {desync_age:.0f}s，仍有條件單，強制清理")

                    # 🔧 v14.9.12: 冷卻期內直接平掉 dYdX 殘留倉位
                    # 這是解決問題的關鍵：止損後 dYdX 還有倉就直接平掉
                    raw_size = float(live_pos.get('size', 0))
                    if abs(raw_size) > 0.0001 and self.dydx_api:
                        print(f"   🔄 [v14.9.12] 清理 dYdX 殘留倉位 {raw_size:.4f} BTC...")
                        try:
                            side_to_close = "SHORT" if raw_size > 0 else "LONG"
                            size_to_close = abs(raw_size)
                            tx_hash, fill_price = await self.dydx_api.place_fast_order(
                                side=side_to_close,
                                size=size_to_close,
                                maker_timeout=0.0,
                                fallback_to_ioc=True
                            )
                            if tx_hash and fill_price > 0:
                                print(f"   ✅ dYdX 殘留倉位已清理 @ ${fill_price:,.2f}")
                                self.dydx_real_position = None
                                # 不重置冷卻，繼續等待完整冷卻期
                            else:
                                print(f"   ⚠️ dYdX 殘留倉位清理失敗，{remaining:.0f}秒後重試")
                        except Exception as e:
                            print(f"   ❌ dYdX 殘留倉位清理異常: {e}")
                return  # 跳過同步
            
            # 🔧 v14.6.2: 從 size 正負判斷方向
            raw_size = float(live_pos.get('size', 0))
            side = "LONG" if raw_size > 0 else "SHORT"
            size = abs(raw_size)
            entry_price = float(live_pos.get('entryPrice', current_price))
            
            # 🔧 v14.9.11: 驗證進場價格合理性 (防止同步假倉位)
            # 如果進場價格與當前價格差距超過 5%，視為無效數據
            price_diff_pct = abs(entry_price - current_price) / current_price * 100 if current_price > 0 else 0
            if price_diff_pct > 5.0:
                print(f"⚠️ [Sync] 跳過同步: 進場價格 ${entry_price:,.2f} 與當前 ${current_price:,.2f} 差距 {price_diff_pct:.1f}% 過大")
                return  # 跳過同步
            
            # 🔧 v14.6.12: 強制同步到 Paper (不再平倉！)
            # 只要 dYdX 有倉但 Paper 無倉，就同步 (不需要 Veto 檢查)
            print(f"🔄 [Sync] dYdX 有倉但 Paper 無倉，強制同步: {side} {size} BTC @ ${entry_price:,.2f}")
            
            # 計算 TP/SL（用和 Paper 開倉一致的「價格移動%」語義），避免 SYNC 交易欄位被 TradeRecord 預設值污染
            # - leverage: 用 dYdX 上限 50 cap（避免 Paper 顯示與 dYdX 不一致）
            leverage = _coerce_int(getattr(self.config, 'leverage', 50), default=50)
            if leverage <= 0:
                leverage = 50
            leverage = min(int(leverage), 50)

            # 盡可能用當前 market_data 推導動態參數（若沒有，回退到卡片固定值）
            target_pct = None
            stop_loss_pct = None
            max_hold_min = None
            fee_pct = None
            volatility = 0.0
            if market_data:
                try:
                    dyn = self.calculate_dynamic_params(market_data)
                    leverage = min(_coerce_int(dyn.get('leverage', leverage), default=leverage), 50)
                    target_pct = _coerce_float(dyn.get('target_pct', 0), default=0.0) or None
                    stop_loss_pct = _coerce_float(dyn.get('stop_loss_pct', 0), default=0.0) or None
                    max_hold_min = _coerce_float(dyn.get('max_hold_min', 0), default=0.0) or None
                    fee_pct = _coerce_float(dyn.get('fee_pct', 0), default=0.0)
                    volatility = _coerce_float(dyn.get('volatility', 0.0), default=0.0)
                except Exception:
                    pass

            if target_pct is None:
                target_pct = _coerce_float(getattr(self.config, 'target_profit_pct', 1.5), default=1.5)
            if stop_loss_pct is None:
                stop_loss_pct = _coerce_float(getattr(self.config, 'stop_loss_pct', 1.0), default=1.0)
            if max_hold_min is None:
                max_hold_min = _coerce_float(getattr(self.config, 'max_hold_minutes', 10.0), default=10.0)
            if fee_pct is None:
                try:
                    fee_pct = self.config.maker_fee_pct if self.config.use_maker_simulation else self.config.taker_fee_pct
                except Exception:
                    fee_pct = 0.0

            # 目標/止損以「槓桿後 ROE%」表示；換算成價格需把 round-trip 手續費加回去
            tp_total_fee_pct = (fee_pct or 0.0) * 2
            lev_f = _coerce_float(leverage, default=50.0)
            if lev_f <= 0:
                lev_f = 50.0
            tp_price_move_pct = _coerce_float(target_pct, default=0.0) / lev_f + tp_total_fee_pct

            taker_fee_pct = _coerce_float(getattr(self.config, 'taker_fee_pct', 0.0), default=0.0)
            exit_fee_pct = taker_fee_pct
            if getattr(self.config, 'taker_on_emergency_only', False):
                exit_fee_pct = (fee_pct or 0.0) if self.config.use_maker_simulation else taker_fee_pct
            sl_total_fee_pct = (fee_pct or 0.0) + exit_fee_pct
            stop_loss_pct = _coerce_float(stop_loss_pct, default=0.0)
            fee_mult = _fee_leverage_multiplier(self.config, lev_f)
            min_stop_loss_pct = sl_total_fee_pct * fee_mult + 0.1
            if stop_loss_pct < min_stop_loss_pct:
                stop_loss_pct = min_stop_loss_pct
            sl_price_move_pct = (-stop_loss_pct / lev_f) + sl_total_fee_pct  # signed

            if side == "LONG":
                tp_price = entry_price * (1 + tp_price_move_pct / 100)
                sl_price = entry_price * (1 + sl_price_move_pct / 100)
            else:
                tp_price = entry_price * (1 - tp_price_move_pct / 100)
                sl_price = entry_price * (1 - sl_price_move_pct / 100)

            # 計算損益平衡（用於 dashboard 真正淨盈虧）
            total_fee_pct = (fee_pct or 0.0) * 2
            if side == "LONG":
                breakeven_price = entry_price * (1 + total_fee_pct / 100)
            else:
                breakeven_price = entry_price * (1 - total_fee_pct / 100)
            
            # 創建 Paper 倉位
            trade_id = f"SYNC_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            trade = TradeRecord(
                trade_id=trade_id,
                timestamp=datetime.now().isoformat(),
                strategy="DYDX_SYNC",
                probability=0.8,
                confidence=0.8,
                direction=side,
                entry_price=entry_price,
                entry_time=datetime.now().isoformat(),
                leverage=leverage,
                position_size_usdt=size * entry_price,
                position_size_btc=size,
                take_profit_price=tp_price,
                stop_loss_price=sl_price,
                obi=market_data.get('obi', 0) if market_data else 0,
                wpi=market_data.get('trade_imbalance', 0) if market_data else 0,
                vpin=market_data.get('vpin', 0) if market_data else 0,
                funding_rate=market_data.get('funding_rate', 0) if market_data else 0,
                oi_change_pct=market_data.get('oi_change_pct', 0) if market_data else 0,
                liq_pressure_long=market_data.get('liq_pressure_long', 50) if market_data else 50,
                liq_pressure_short=market_data.get('liq_pressure_short', 50) if market_data else 50,
                price_change_1m=market_data.get('price_change_1m', 0) if market_data else 0,
                price_change_5m=market_data.get('price_change_5m', 0) if market_data else 0,
                volatility_5m=market_data.get('volatility_5m', 0) if market_data else 0,
                strategy_probs=market_data.get('strategy_probs', {}) if market_data else {},
                entry_type="SYNC",
                entry_batches=1,
                entry_duration_sec=0.0,
                avg_entry_price=entry_price,
                entry_slippage_pct=0.0,
                breakeven_price=breakeven_price,
                actual_leverage=leverage,
                actual_target_pct=target_pct,
                actual_stop_loss_pct=stop_loss_pct,
                actual_max_hold_min=max_hold_min,
                market_volatility=volatility,
                status="OPEN",
            )
            
            self.active_trade = trade
            self.trades.append(trade)
            
            # 同步 dYdX 追蹤
            self.dydx_real_position = {
                "side": side,
                "size": size,
                "entry_price": entry_price,
                "entry_time": datetime.now()
            }
            
            print(f"✅ [Sync] Paper 倉位已同步: {side} {size} BTC @ ${entry_price:,.2f}")
            print(f"   📊 TP: ${tp_price:,.2f} | SL: ${sl_price:,.2f}")
            
            # 🆕 v14.6.12: 同步後立即計算 N%鎖N% 並掛止損單
            current_pnl = self.calculate_current_pnl_pct(current_price)
            trade.max_profit_pct = max(current_pnl, 0)  # 初始化最大獲利
            
            stop_loss_pct, stage_name = self.get_progressive_stop_loss(trade.max_profit_pct)
            print(f"   🔐 N%鎖N%: 當前 {current_pnl:.2f}% | 最高 {trade.max_profit_pct:.2f}% | 止損線 {stop_loss_pct:.2f}%")
            
            if stop_loss_pct > -999:  # 有效的止損線
                try:
                    ok = await self.update_dydx_stop_loss_async(stop_loss_pct)
                    if ok:
                        print(f"   📋 已補掛 dYdX 止損單 ({stage_name})")
                    else:
                        if self.pending_sl_order:
                            print(f"   📋 dYdX 止損單已存在 ({stage_name})")
                        else:
                            print(f"   ⚠️ 補掛 dYdX 止損單失敗 ({stage_name})")
                except Exception as e:
                    print(f"   ⚠️ 補掛 dYdX 止損單失敗: {e}")

        if paper_has_position and not live_pos:
            if paper_master:
                trade = getattr(self, "active_trade", None)
                if not trade:
                    return
                now_ts = time.time()
                cooldown = _coerce_float(
                    getattr(self.config, "dydx_resync_open_cooldown_sec", 8.0),
                    default=8.0
                )
                last_open_ts = getattr(self, "_last_dydx_resync_open_ts", 0.0)
                if now_ts - last_open_ts < cooldown:
                    return

                direction = str(trade.direction).upper()
                entry_price = _coerce_float(getattr(trade, "entry_price", 0.0), default=0.0)
                target_pct = _coerce_float(getattr(trade, "actual_target_pct", None), default=0.0)
                if target_pct <= 0:
                    target_pct = _coerce_float(getattr(self.config, "target_profit_pct", 1.5), default=1.5)
                stop_pct = _coerce_float(getattr(trade, "actual_stop_loss_pct", None), default=0.0)
                if stop_pct <= 0:
                    stop_pct = _coerce_float(getattr(self.config, "stop_loss_pct", 1.0), default=1.0)
                leverage = _coerce_int(getattr(trade, "actual_leverage", None), default=0)
                if leverage <= 0:
                    leverage = _coerce_int(getattr(self.config, "leverage", 50), default=50)

                print(f"🔄 [PaperMaster] Paper 有倉但 dYdX 無倉，補開 dYdX: {direction} @ ${entry_price:,.2f}")
                try:
                    self._last_dydx_resync_open_ts = now_ts
                    use_ref_entry_price = bool(getattr(self.config, 'dydx_use_reference_entry_price', False))
                    ok, fill_price = await self._dydx_open_position_v2(
                        direction=direction,
                        entry_price=entry_price or current_price,
                        target_pct=target_pct,
                        stop_pct=stop_pct,
                        leverage=leverage,
                        reference_price=entry_price if use_ref_entry_price else None,
                        use_reference_price=use_ref_entry_price,
                    )
                    if ok:
                        print(f"✅ [PaperMaster] dYdX 已補開: ${fill_price:,.2f}")
                    else:
                        print("⚠️ [PaperMaster] dYdX 補開失敗，稍後重試")
                except Exception as e:
                    print(f"❌ [PaperMaster] dYdX 補開異常: {e}")
                return
            # ✅ v14.6.41: dYdX 已平倉但 Paper 還在持倉（常見於 TP/SL 條件單成交、或手動平倉）
            # 這會導致 Dashboard 顯示「dYdX 無持倉 但 Paper 有倉」的幻影狀態。
            # 解法：嘗試從 fills 找到「該筆交易」的實際平倉成交價，並同步關閉 Paper。
            paper_trade = getattr(self, 'active_trade', None)
            if paper_trade and self.dydx_api:
                now_ts = time.time()
                last_check = getattr(self, '_dydx_missing_pos_fills_check_ts', 0.0)
                # 節流：避免每秒狂刷 fills API
                if now_ts - last_check >= 5.0:
                    self._dydx_missing_pos_fills_check_ts = now_ts

                    exit_price: float | None = None
                    exit_created_at: str | None = None
                    open_created_at: str | None = None
                    try:
                        from datetime import timezone, timedelta

                        # Paper 的 entry_time 是本地 naive 時間字串；轉成 UTC 以比對 fills.createdAt (Z)
                        entry_dt_utc = None
                        try:
                            entry_dt_utc = datetime.fromisoformat(paper_trade.entry_time).astimezone(timezone.utc)
                        except Exception:
                            entry_dt_utc = None

                        # 只抓最近 50 筆，並用 entry_time 做時間窗縮小
                        fills = await self.dydx_api.get_recent_fills(limit=50)
                        btc_fills = [f for f in (fills or []) if (f.get("market") or f.get("ticker")) == "BTC-USD"]

                        def _parse_fill_dt(fill: dict) -> Optional[datetime]:
                            s = str(fill.get("createdAt", "") or "")
                            if not s:
                                return None
                            try:
                                return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
                            except Exception:
                                return None

                        # 依時間由舊到新排序
                        btc_fills = [f for f in btc_fills if _parse_fill_dt(f) is not None]
                        btc_fills.sort(key=lambda f: _parse_fill_dt(f) or datetime.min.replace(tzinfo=timezone.utc))

                        open_side = "BUY" if paper_trade.direction == "LONG" else "SELL"
                        close_side = "SELL" if paper_trade.direction == "LONG" else "BUY"

                        target_size = 0.0
                        try:
                            target_size = float(getattr(paper_trade, "position_size_btc", 0) or 0)
                        except Exception:
                            target_size = 0.0
                        if target_size <= 0:
                            try:
                                target_size = float(getattr(self.config, "dydx_btc_size", 0) or 0)
                            except Exception:
                                target_size = 0.0

                        size_tol = max(1e-6, target_size * 0.2) if target_size > 0 else 0.0

                        # 1) 找到最像「本次進場」的 open fill（時間接近 entry_time + size 匹配 + 價格接近）
                        open_idx: int | None = None
                        best_score: float | None = None
                        for i, f in enumerate(btc_fills):
                            if str(f.get("side", "")).upper() != open_side:
                                continue
                            dt = _parse_fill_dt(f)
                            if entry_dt_utc and dt:
                                # 進場 fill 通常會早於 Paper entry_time 幾秒，給 5 分鐘安全窗
                                if dt < entry_dt_utc - timedelta(minutes=5) or dt > entry_dt_utc + timedelta(minutes=5):
                                    continue
                            try:
                                sz = abs(float(f.get("size", 0) or 0))
                            except Exception:
                                continue
                            if target_size > 0 and abs(sz - target_size) > size_tol:
                                continue
                            try:
                                price = float(f.get("price", 0) or 0)
                            except Exception:
                                price = 0.0
                            # score：越靠近 entry_time & entry_price 越好
                            score = 0.0
                            if entry_dt_utc and dt:
                                score += abs((dt - entry_dt_utc).total_seconds())
                            if price > 0 and getattr(paper_trade, "entry_price", 0) > 0:
                                score += abs(price - float(paper_trade.entry_price))
                            if best_score is None or score < best_score:
                                best_score = score
                                open_idx = i
                                open_created_at = str(f.get("createdAt") or "") or None

                        # 2) 從 open fill 往後推進，直到淨倉位回到 0 → 視為平倉 fill
                        if open_idx is not None:
                            pos = 0.0
                            opened = False
                            for f in btc_fills[open_idx:]:
                                side = str(f.get("side", "")).upper()
                                try:
                                    sz = float(f.get("size", 0) or 0)
                                except Exception:
                                    continue
                                if side == "BUY":
                                    pos += sz
                                elif side == "SELL":
                                    pos -= sz
                                if not opened and abs(pos) > 0.00001:
                                    opened = True
                                if opened and abs(pos) <= 0.00001 and side == close_side:
                                    try:
                                        exit_price = float(f.get("price", 0) or 0)
                                    except Exception:
                                        exit_price = None
                                    exit_created_at = str(f.get("createdAt") or "") or None
                                    break
                    except Exception:
                        exit_price = None

                    if exit_price and exit_price > 0:
                        print(f"✅ [Reconcile] dYdX 已平倉，Paper 同步平倉 @ ${exit_price:,.2f}")
                        try:
                            self._journal_dydx_event(
                                "paper_closed_by_dydx",
                                exit_price=exit_price,
                                open_created_at=open_created_at,
                                exit_created_at=exit_created_at,
                                paper_trade_id=getattr(paper_trade, "trade_id", None),
                                paper_entry_price=getattr(paper_trade, "entry_price", None),
                            )
                        except Exception:
                            pass

                        # 🧹 清理本地追蹤，避免 close_position 走 StrictSync 再下單
                        self.dydx_real_position = None
                        try:
                            self.pending_tp_order = None
                            self.pending_sl_order = None
                            self._dydx_order_registry.clear()
                        except Exception:
                            pass

                        # 🧹 位置已平 → 清掃殘留 TP/SL（若已無單，cancelled=0 也沒關係）
                        try:
                            await self._dydx_sweep_open_orders(reason="reconcile_dydx_closed", market="BTC-USD")
                        except Exception:
                            pass

                        # 同步關閉 Paper（用真實成交價）
                        self.close_position(reason="🔗 dYdX 已平倉同步", exit_price=exit_price)
                        return

            # 🔧 v14.6.8: 若找不到 fills 證據，保留原本「30 秒窗口」避免 API 延遲誤判
            if self.dydx_real_position:
                entry_time = self.dydx_real_position.get('entry_time')
                if entry_time:
                    if isinstance(entry_time, datetime):
                        elapsed = (datetime.now() - entry_time).total_seconds()
                    else:
                        elapsed = 999  # 無效時間，允許清除

                    if elapsed < 30:
                        # 剛開倉不到 30 秒，可能是 API 延遲
                        print(f"⏳ [Reconcile] dYdX API 尚未同步 ({elapsed:.0f}s)，保留內部追蹤")
                    else:
                        # 超過 30 秒 API 仍無倉位，可能是真的沒開成功
                        print(f"⚠️ [Reconcile] dYdX API 無倉位 ({elapsed:.0f}s)，清除追蹤")
                        self.dydx_real_position = None
                else:
                    # 無時間戳，清除追蹤
                    self.dydx_real_position = None

    async def _get_dydx_positions_with_cache(self) -> List[Dict]:
        """
        具備 429 backoff 的 positions 取得器
        - 🔧 v14.3: 快取 3 秒內的查詢 (原 1.2s，減少呼叫)
        - 遇到 429 時退避 5 秒，期間回傳快取
        """
        now = time.time()
        cache_ttl = 3.0  # 🔧 v14.3: 增加到 3 秒
        backoff_seconds = 5.0  # 🔧 v14.3: 增加到 5 秒

        # 若在退避期內，直接回快取
        if now < self._dydx_pos_backoff_until and self._dydx_pos_cache is not None:
            return self._dydx_pos_cache

        # 若有新鮮快取，直接回
        if self._dydx_pos_cache and (now - self._dydx_pos_cache_time) < cache_ttl:
            return self._dydx_pos_cache

        try:
            positions = await self.dydx_api.get_positions()
            self._dydx_pos_cache = positions or []
            self._dydx_pos_cache_time = now
            return self._dydx_pos_cache
        except Exception as e:
            msg = str(e)
            if "429" in msg:
                self._dydx_pos_backoff_until = now + backoff_seconds
                # 回傳舊快取以避免中斷
                return self._dydx_pos_cache or []
            # 其他錯誤也回快取，避免連續噴錯
            return self._dydx_pos_cache or []

    async def _get_dydx_market_with_cache(self) -> Dict:
        """
        具備 429 backoff 的 market 查詢，用於 oracle/mark price 等。
        🔧 v14.3: 增加緩存時間
        """
        now = time.time()
        cache_ttl = 3.0  # 🔧 v14.3: 增加到 3 秒
        backoff_seconds = 5.0  # 🔧 v14.3: 增加到 5 秒

        if now < self._dydx_market_backoff_until and self._dydx_market_cache is not None:
            return self._dydx_market_cache

        if self._dydx_market_cache and (now - self._dydx_market_cache_time) < cache_ttl:
            return self._dydx_market_cache

        try:
            market = await self.dydx_api.get_market(self.config.symbol_dydx if hasattr(self.config, 'symbol_dydx') else "BTC-USD")
            if market:
                self._dydx_market_cache = market
                self._dydx_market_cache_time = now
            return self._dydx_market_cache or {}
        except Exception as e:
            msg = str(e)
            if "429" in msg:
                self._dydx_market_backoff_until = now + backoff_seconds
            return self._dydx_market_cache or {}

    def _init_dydx_sync(self):
        """
        🆕 初始化 dYdX 同步交易 (Aggressive Maker)
        使用 dydx_whale_trader.py 的交易執行邏輯
        """
        if not DYDX_WHALE_AVAILABLE:
            print("⚠️ dYdX Whale Trader 模組不可用")
            self.dydx_sync_enabled = False
            return

        self._journal_dydx_event(
            "sync_init_start",
            network="mainnet",
            symbol="BTC-USD",
        )
        
        try:
            # 創建 dYdX 配置
            # 🔧 同步模式槓桿以 dYdX 可用上限為準（BTC 通常 <= 50），避免 Paper/dYdX PnL 與顯示不一致
            cfg_lev = _coerce_int(getattr(self.config, 'leverage', 50), default=50)
            if cfg_lev <= 0:
                cfg_lev = 50
            cfg_lev = min(cfg_lev, 50)
            dydx_config = DydxTradingConfig(
                network="mainnet",
                symbol="BTC-USD",
                leverage=cfg_lev,
                paper_trading=False,  # 真實交易
                sync_real_trading=True,
                fixed_btc_size=self.config.dydx_btc_size
            )
            
            # 創建 API 客戶端
            self.dydx_api = DydxAPI(dydx_config)
            self._journal_dydx_event(
                "sync_api_created",
                network="mainnet",
                symbol="BTC-USD",
                leverage=cfg_lev,
            )
            
            # 🆕 v14.6.11: 取得 dYdX 地址用於 WebSocket 持倉訂閱
            dydx_address = os.getenv("DYDX_ADDRESS", "")
            if dydx_address:
                print(f"📍 dYdX 地址: {dydx_address[:12]}...{dydx_address[-6:]}")
                self._journal_dydx_event(
                    "sync_address_present",
                    address_prefix=dydx_address[:12],
                    address_suffix=dydx_address[-6:],
                )
            
            # 🆕 v13.0: 創建 WebSocket 客戶端 (統一資料源)
            # 🔧 v14.6.11: 傳入 address 以訂閱持倉更新
            try:
                from scripts.dydx_whale_trader import DydxWebSocketClient
                self.dydx_ws = DydxWebSocketClient(symbol="BTC-USD", network="mainnet", address=dydx_address if dydx_address else None)
                print("📶 dYdX WebSocket 客戶端已創建 (含持倉訂閱)" if dydx_address else "📶 dYdX WebSocket 客戶端已創建")
            except ImportError:
                self.dydx_ws = None
                print("⚠️ dYdX WebSocket 客戶端不可用")
            
            # 🔧 v14.2: 立即連接 dYdX API (帶重試)
            print(f"🔗 正在連接 dYdX...")
            import asyncio
            try:
                connected = asyncio.run(self.dydx_api.connect())
                if connected:
                    print(f"✅ dYdX API 連接成功! (Node + Wallet 已初始化)")
                    # 取得初始餘額
                    balance = asyncio.run(self.dydx_api.get_account_balance())
                    if self.dydx_initial_balance is None:
                        self.dydx_initial_balance = balance
                    print(f"💰 dYdX 餘額: ${balance:.2f}")
                    
                    # 🆕 v14.6.29: 啟動時清空殘留的未平倉掛單
                    # 避免上次程式異常終止後，殘留的 TP/SL 掛單影響新一輪交易
                    try:
                        cancelled = asyncio.run(self._dydx_sweep_open_orders(
                            reason="startup_cleanup", 
                            market="BTC-USD"
                        ))
                        if cancelled > 0:
                            print(f"🧹 已清空 {cancelled} 筆殘留掛單 (啟動清理)")
                        else:
                            print(f"✅ 無殘留掛單")
                    except Exception as sweep_err:
                        print(f"⚠️ 啟動清理掛單失敗: {sweep_err}")
                    
                    self._journal_dydx_event(
                        "sync_connected",
                        result="ok",
                        balance=balance,
                    )
                else:
                    print("⚠️ dYdX API 連接失敗，交易可能受影響")
                    self._journal_dydx_event(
                        "sync_connected",
                        result="error",
                    )
            except Exception as e:
                print(f"⚠️ dYdX 連接異常: {e}")
                self._journal_dydx_event(
                    "sync_connect_exception",
                    error=str(e),
                )
            
        except Exception as e:
            print(f"❌ dYdX 初始化失敗: {e}")
            self._journal_dydx_event(
                "sync_init_failed",
                error=str(e),
            )
            self.dydx_sync_enabled = False
            self.dydx_api = None
            self.dydx_ws = None
    
    async def _connect_dydx(self) -> bool:
        """非同步連接 dYdX (帶重試)"""
        if not self.dydx_api:
            return False
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                connected = await self.dydx_api.connect()
                if connected:
                    balance = await self.dydx_api.get_account_balance()
                    # 🆕 記錄起始餘額 (只記錄一次)
                    if self.dydx_initial_balance is None:
                        self.dydx_initial_balance = balance
                    print(f"✅ dYdX API 連接成功! 餘額: ${balance:.2f}")
                    
                    # 🆕 v13.0: 連接 WebSocket (統一資料源)
                    if hasattr(self, 'dydx_ws') and self.dydx_ws:
                        ws_connected = await self.dydx_ws.connect()
                        if ws_connected:
                            print("✅ dYdX WebSocket 連接成功 (統一資料源)")
                        else:
                            print("⚠️ dYdX WebSocket 連接失敗，使用 REST API")
                    
                    return True
                else:
                    if attempt < max_retries - 1:
                        wait_time = 2 * (attempt + 1)
                        print(f"⏳ dYdX 連接失敗，等待 {wait_time}s 後重試 ({attempt + 1}/{max_retries})...")
                        await asyncio.sleep(wait_time)
                    else:
                        print("❌ dYdX 連接失敗")
                        self.dydx_sync_enabled = False
                        return False
            except Exception as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    wait_time = 3 * (attempt + 1)
                    print(f"⏳ dYdX 429 限速，等待 {wait_time}s 後重試 ({attempt + 1}/{max_retries})...")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"❌ dYdX 連接錯誤: {e}")
                    self.dydx_sync_enabled = False
                    return False
        
        return False
    
    async def _dydx_open_position(self, direction: str, entry_price: float) -> bool:
        """
        🆕 在 dYdX 開真實倉位 (Aggressive Maker)
        
        Args:
            direction: "LONG" 或 "SHORT"
            entry_price: 參考進場價 (Maker 會使用最佳價格)
        
        Returns:
            是否成功
        """
        if not self.dydx_sync_enabled or not self.dydx_api:
            return False
        
        try:
            size = self.config.dydx_btc_size
            print(f"🔴 dYdX 同步開倉 (Maker): {direction} {size:.4f} BTC")
            
            # 使用 Aggressive Maker 開倉
            tx_hash, fill_price = await self.dydx_api.place_aggressive_limit_order(
                side=direction,
                size=size,
                timeout_seconds=10.0
            )
            
            if tx_hash and fill_price > 0:
                self.dydx_real_position = {
                    "side": direction,
                    "size": size,
                    "entry_price": fill_price,
                    "entry_time": datetime.now()
                }
                print(f"✅ dYdX Maker 成交! 價格: ${fill_price:,.2f}")
                
                # 🆕 v14.9.7: 實作交易所端條件單 (進場後立即掛 SL/TP)
                # 1. 掛止損單 (Server-side STOP_MARKET)
                try:
                    sl_pct = getattr(self.config, 'stop_loss_pct', 0.75)
                    # 確保止損方向正確 (負值代表虧損)
                    await self.update_dydx_stop_loss_async(-abs(sl_pct))
                    print(f"   🛡️ dYdX 交易所端止損單已掛出 (-{abs(sl_pct):.2f}% ROE)")
                except Exception as e:
                    print(f"   ⚠️ dYdX 止損單掛出失敗: {e}")

                # 2. 掛止盈單 (Server-side TAKE_PROFIT)
                try:
                    tp_pct = getattr(self.config, 'target_profit_pct', 1.5)
                    lev = getattr(self.config, 'leverage', 10)
                    if direction == "LONG":
                        tp_price = fill_price * (1 + tp_pct / lev / 100)
                    else:
                        tp_price = fill_price * (1 - tp_pct / lev / 100)
                    
                    await self.update_dydx_take_profit_async(tp_price, tp_pct)
                    print(f"   🎯 dYdX 交易所端止盈單已掛出 (+{tp_pct:.2f}% ROE @ ${tp_price:,.2f})")
                except Exception as e:
                    print(f"   ⚠️ dYdX 止盈單掛出失敗: {e}")

                # 顯示真實餘額
                balance = await self.dydx_api.get_account_balance()
                print(f"💰 dYdX 餘額: ${balance:.2f}")
                return True
            else:
                print("⚠️ dYdX 掛單超時未成交")
                return False
                
        except Exception as e:
            print(f"❌ dYdX 開倉失敗: {e}")
            return False
    
    async def _dydx_open_position_v2(
        self,
        direction: str,
        entry_price: float,
        target_pct: float,
        stop_pct: float,
        leverage: Optional[int] = None,
        reference_price: Optional[float] = None,
        use_reference_price: bool = False,
    ) -> tuple[bool, float]:
        """
        🆕 v2: 在 dYdX 開真實倉位，返回成交價格
        
        Args:
            direction: "LONG" 或 "SHORT"
            entry_price: 參考進場價
        
        Returns:
            (是否成功, 成交價格)
        """
        if not self.dydx_sync_enabled or not self.dydx_api:
            return False, 0.0
        
        try:
            size = self.config.dydx_btc_size

            # ✅ 開新一輪前先清空未平倉掛單（避免舊 TP/SL/中間數止損累積造成誤判與 order count limit）
            await self._dydx_sweep_open_orders(reason="pre_open_new_round", market="BTC-USD")
            
            # 🔧 檢查餘額是否足夠
            balance = await self.dydx_api.get_account_balance()
            btc_price = await self.dydx_api.get_price()
            if leverage is None:
                leverage = _coerce_int(getattr(self.config, 'leverage', 50), default=50)
            leverage = _coerce_int(leverage, default=50)
            if leverage <= 0:
                leverage = 50
            
            # 計算所需保證金
            required_margin = (size * btc_price) / leverage

            self._journal_dydx_event(
                "open_attempt",
                side=direction,
                size=size,
                leverage=leverage,
                balance=balance,
                btc_price=btc_price,
                required_margin=required_margin,
            )
            
            if balance < required_margin * 1.1:  # 需要 110% 保證金
                # 自動調整 size
                max_size = (balance * leverage * 0.9) / btc_price
                max_size = round(max_size, 4)  # 四捨五入到 4 位小數
                
                if max_size < 0.0001:  # 最小交易單位
                    print(f"❌ dYdX 餘額不足: ${balance:.2f} (需要 ${required_margin:.2f})")
                    return False, 0.0
                
                print(f"⚠️ 餘額不足 ${balance:.2f}，自動調整倉位: {size:.4f} → {max_size:.4f} BTC")
                size = max_size
            
            print(f"🔴 dYdX 同步開倉 (快速模式): {direction} {size:.4f} BTC")
            
            # 🔧 v14.9.5: 支援短暫 Maker 嘗試，降低滑點
            # maker_timeout=0 會直接跳過 Maker 嘗試
            maker_timeout = _coerce_float(getattr(self.config, "dydx_maker_timeout_sec", 0.0), default=0.0)
            if maker_timeout < 0:
                maker_timeout = 0.0
            tx_hash, fill_price = await self.dydx_api.place_fast_order(
                side=direction,
                size=size,
                maker_timeout=maker_timeout,
                fallback_to_ioc=True
            )

            confirmed_via_rest = False
            confirmed_pos = None
            if not (tx_hash and fill_price > 0):
                rest_confirm_timeout = _coerce_float(
                    getattr(self.config, "dydx_rest_confirm_timeout_sec", 1.5),
                    default=1.5,
                )
                confirm_deadline = time.time() + max(0.2, rest_confirm_timeout)
                while time.time() < confirm_deadline:
                    await asyncio.sleep(0.25)
                    try:
                        if hasattr(self.dydx_api, "get_positions_fresh"):
                            positions = await self.dydx_api.get_positions_fresh()
                        else:
                            positions = await self.dydx_api.get_positions()
                    except Exception:
                        positions = []
                    for pos in positions or []:
                        if pos.get("market") != "BTC-USD":
                            continue
                        raw_size = _coerce_float(pos.get("size", 0.0), default=0.0)
                        if abs(raw_size) <= 0.0001:
                            continue
                        actual_side = "LONG" if raw_size > 0 else "SHORT"
                        actual_size = abs(raw_size)
                        actual_entry = _coerce_float(pos.get("entryPrice", 0.0), default=0.0)
                        if actual_entry <= 0:
                            try:
                                fills = await self.dydx_api.get_recent_fills(limit=5)
                            except Exception:
                                fills = []
                            close_side = "BUY" if actual_side == "LONG" else "SELL"
                            for fill in fills or []:
                                if fill.get("market") != "BTC-USD":
                                    continue
                                if fill.get("side") == close_side:
                                    actual_entry = _coerce_float(fill.get("price", 0.0), default=0.0)
                                    break
                        if actual_entry <= 0:
                            continue
                        fill_price = actual_entry
                        size = actual_size if actual_size > 0 else size
                        direction = actual_side
                        confirmed_pos = {
                            "side": actual_side,
                            "size": actual_size,
                            "entry_price": actual_entry,
                            "entry_time": datetime.now(),
                        }
                        confirmed_via_rest = True
                        break
                    if confirmed_via_rest:
                        break

            position_confirmed = bool(tx_hash and fill_price > 0) or confirmed_via_rest
            if not position_confirmed:
                print("⚠️ dYdX 開倉未確認，暫不掛 TP/SL，等待下一輪同步")
                self._journal_dydx_event(
                    "open_unconfirmed",
                    side=direction,
                    size=size,
                    fill_price=fill_price,
                    tx_present=bool(tx_hash),
                )
                return False, 0.0

            if confirmed_pos:
                self.dydx_real_position = confirmed_pos

            if tx_hash and fill_price > 0 or confirmed_via_rest:
                if confirmed_via_rest:
                    print(f"✅ dYdX 開倉確認 (REST): {direction} {size:.4f} BTC @ ${fill_price:,.2f}")
                    self._journal_dydx_event(
                        "open_confirmed_rest",
                        side=direction,
                        size=size,
                        fill_price=fill_price,
                        tx_present=bool(tx_hash),
                    )
                self._journal_dydx_event(
                    "open_filled",
                    side=direction,
                    size=size,
                    fill_price=fill_price,
                    tx=str(tx_hash)[:200] if tx_hash else None,
                )
                self.dydx_real_position = {
                    "side": direction,
                    "size": size,
                    "entry_price": fill_price,
                    "entry_time": datetime.now(),
                    "tp_order_id": 0,  # 🆕 追蹤 TP 訂單
                    "sl_order_id": 0,  # 🆕 追蹤 SL 訂單
                }
                print(f"✅ dYdX 成交! 價格: ${fill_price:,.2f}")
                
                # 🆕 v14.6.11: 等待 WebSocket 確認持倉 (可配置)
                ws_timeout_sec = _coerce_float(
                    getattr(self.config, "dydx_ws_confirm_timeout_sec", 5.0),
                    default=5.0,
                )
                if ws_timeout_sec and ws_timeout_sec > 0 and hasattr(self, 'dydx_ws') and self.dydx_ws:
                    ws_confirmed = False
                    start_ts = time.time()
                    while (time.time() - start_ts) < ws_timeout_sec:
                        await asyncio.sleep(0.25)
                        if self.dydx_ws.has_position("BTC-USD"):
                            ws_confirmed = True
                            print(f"📶 [WS] 持倉已確認!")
                            break
                    if not ws_confirmed:
                        print(f"⚠️ [WS] 未收到持倉確認 ({ws_timeout_sec:.1f}s)，但訂單已成交")
                
                # ═══════════════════════════════════════════════════════════
                # 🔧 v14.6.10: 確認持倉後立即掛 TP/SL 條件單
                # 使用 GTT 限價單作為止盈，CONDITIONAL 條件單作為止損
                # ═══════════════════════════════════════════════════════════
                # target/stop 以「淨 ROE%」表示，掛單價格需把 round-trip 手續費加回去
                target_pct = _coerce_float(target_pct, default=1.5)
                stop_pct = _coerce_float(stop_pct, default=1.0)

                lev_f = _coerce_float(leverage, default=50.0)
                if lev_f <= 0:
                    lev_f = 50.0

                entry_fee_pct = _coerce_float(
                    (self.config.maker_fee_pct if self.config.use_maker_simulation else self.config.taker_fee_pct),
                    default=0.0,
                )
                taker_fee_pct = _coerce_float(getattr(self.config, 'taker_fee_pct', 0.0), default=0.0)

                tp_total_fee_pct = entry_fee_pct * 2
                tp_price_move_pct = (target_pct / lev_f) + tp_total_fee_pct

                exit_fee_pct = taker_fee_pct
                if getattr(self.config, 'taker_on_emergency_only', False):
                    exit_fee_pct = entry_fee_pct if self.config.use_maker_simulation else taker_fee_pct
                sl_total_fee_pct = entry_fee_pct + exit_fee_pct
                # 🔧 v14.9.6: 最小止損 ROE% 必須 > 手續費 ROE%，否則止損方向會錯
                fee_mult = _fee_leverage_multiplier(self.config, lev_f)
                min_stop_pct = sl_total_fee_pct * fee_mult + 0.5  # 手續費 ROE + 0.5% 緩衝
                if stop_pct < min_stop_pct:
                    print(f"   ⚠️ [v14.9.6] 止損 {stop_pct:.2f}% 太小 (< 手續費 {sl_total_fee_pct * fee_mult:.2f}%)，"
                          f"調整為 {min_stop_pct:.2f}%")
                    stop_pct = min_stop_pct
                sl_price_move_pct = (-stop_pct / lev_f) + sl_total_fee_pct

                entry_price_base = (
                    reference_price
                    if use_reference_price and reference_price and reference_price > 0
                    else fill_price
                )
                if direction == "LONG":
                    tp_price = entry_price_base * (1 + tp_price_move_pct / 100)
                    sl_price = entry_price_base * (1 + sl_price_move_pct / 100)
                else:
                    tp_price = entry_price_base * (1 - tp_price_move_pct / 100)
                    sl_price = entry_price_base * (1 - sl_price_move_pct / 100)
                
                print(f"\n📋 掛 dYdX TP/SL 條件單...")
                if entry_price_base != fill_price:
                    print(f"   進場: ${fill_price:,.2f} | 參考: ${entry_price_base:,.2f} | 方向: {direction}")
                else:
                    print(f"   進場: ${fill_price:,.2f} | 方向: {direction}")
                print(f"   止盈: ${tp_price:,.2f} (+{target_pct:.3f}%)")
                print(f"   止損: ${sl_price:,.2f} (-{stop_pct:.3f}%)")
                
                # 掛止損單 (CONDITIONAL 條件單)
                try:
                    sl_tx, sl_order_id = await self.dydx_api.place_stop_loss_order(
                        side=direction,
                        size=size,
                        stop_price=sl_price,
                        time_to_live_seconds=3600
                    )
                    if sl_tx and sl_order_id:
                        self.dydx_real_position["sl_order_id"] = sl_order_id
                        # 同步 dashboard 顯示
                        # 🔧 v14.6.28: 統一符號系統 - 負值代表虧損止損，正值代表鎖利
                        self.pending_sl_order = {
                            'direction': direction,
                            'entry_price': entry_price_base,
                            'sl_price': sl_price,
                            'stop_pct': -stop_pct,  # 開倉時是虧損止損，用負值
                            'leverage': leverage,
                            'created_time': time.time(),
                            'status': 'PENDING',
                            'dydx_order_id': sl_order_id,
                        }
                        # 🧠 註冊 dYdX 訂單
                        self._register_dydx_order(sl_order_id, order_type="CONDITIONAL", kind="SL")
                        self._journal_dydx_event(
                            "sl_placed",
                            order_id=sl_order_id,
                            order_type="CONDITIONAL",
                            sl_price=sl_price,
                            stop_pct=stop_pct,
                            leverage=leverage,
                            tx=str(sl_tx)[:200] if sl_tx else None,
                        )
                        print(f"   ✅ 止損單已掛! ID: {sl_order_id}")
                    else:
                        # 若遇到 order count / rate limit，先清掃再重試一次
                        try:
                            err = self.dydx_api.get_last_tx_error() if self.dydx_api else {}
                        except Exception:
                            err = {}
                        swept = await self._dydx_sweep_on_limit_error(err, reason="sl_place_limit")
                        if swept:
                            sl_tx2, sl_order_id2 = await self.dydx_api.place_stop_loss_order(
                                side=direction,
                                size=size,
                                stop_price=sl_price,
                                time_to_live_seconds=3600,
                            )
                            if sl_tx2 and sl_order_id2:
                                self.dydx_real_position["sl_order_id"] = sl_order_id2
                                self.pending_sl_order = {
                                    'direction': direction,
                                    'entry_price': entry_price_base,
                                    'sl_price': sl_price,
                                    'stop_pct': -stop_pct,  # 開倉時是虧損止損，用負值
                                    'leverage': leverage,
                                    'created_time': time.time(),
                                    'status': 'PENDING',
                                    'dydx_order_id': sl_order_id2,
                                }
                                self._register_dydx_order(sl_order_id2, order_type="CONDITIONAL", kind="SL")
                                self._journal_dydx_event(
                                    "sl_placed",
                                    order_id=sl_order_id2,
                                    order_type="CONDITIONAL",
                                    sl_price=sl_price,
                                    stop_pct=stop_pct,
                                    leverage=leverage,
                                    tx=str(sl_tx2)[:200] if sl_tx2 else None,
                                )
                                print(f"   ✅ 止損單已掛! ID: {sl_order_id2}")
                            else:
                                print(f"   ⚠️ 止損單失敗")
                                try:
                                    err = self.dydx_api.get_last_tx_error() if self.dydx_api else {}
                                except Exception:
                                    err = {}
                                self._journal_dydx_event(
                                    "sl_place_failed",
                                    sl_price=sl_price,
                                    stop_pct=stop_pct,
                                    leverage=leverage,
                                    attempt=2,
                                    last_tx_error=err,
                                )
                        else:
                            # 若非 limit，取消條件單再重試一次
                            await self._dydx_cancel_conditional_orders(reason="sl_place_failed_cancel_conditional_retry")
                            sl_tx2, sl_order_id2 = await self.dydx_api.place_stop_loss_order(
                                side=direction,
                                size=size,
                                stop_price=sl_price,
                                time_to_live_seconds=3600,
                            )
                            if sl_tx2 and sl_order_id2:
                                self.dydx_real_position["sl_order_id"] = sl_order_id2
                                self.pending_sl_order = {
                                    'direction': direction,
                                    'entry_price': entry_price_base,
                                    'sl_price': sl_price,
                                    'stop_pct': -stop_pct,  # 開倉時是虧損止損，用負值
                                    'leverage': leverage,
                                    'created_time': time.time(),
                                    'status': 'PENDING',
                                    'dydx_order_id': sl_order_id2,
                                }
                                self._register_dydx_order(sl_order_id2, order_type="CONDITIONAL", kind="SL")
                                self._journal_dydx_event(
                                    "sl_placed",
                                    order_id=sl_order_id2,
                                    order_type="CONDITIONAL",
                                    sl_price=sl_price,
                                    stop_pct=stop_pct,
                                    leverage=leverage,
                                    tx=str(sl_tx2)[:200] if sl_tx2 else None,
                                )
                                print(f"   ✅ 止損單已掛! ID: {sl_order_id2}")
                            else:
                                print(f"   ⚠️ 止損單失敗")
                                try:
                                    err = self.dydx_api.get_last_tx_error() if self.dydx_api else {}
                                except Exception:
                                    err = {}
                                self._journal_dydx_event(
                                    "sl_place_failed",
                                    sl_price=sl_price,
                                    stop_pct=stop_pct,
                                    leverage=leverage,
                                    attempt=2,
                                    last_tx_error=err,
                                )
                except Exception as e:
                    print(f"   ❌ 止損單異常: {e}")
                    try:
                        err = self.dydx_api.get_last_tx_error() if self.dydx_api else {}
                    except Exception:
                        err = {}
                    self._journal_dydx_event(
                        "sl_place_exception",
                        sl_price=sl_price,
                        stop_pct=stop_pct,
                        leverage=leverage,
                        error=str(e),
                        last_tx_error=err,
                    )

                # 掛止盈單 (GTT 限價單)
                try:
                    tp_tx, tp_order_id = await self.dydx_api.place_take_profit_order(
                        side=direction,
                        size=size,
                        tp_price=tp_price,
                        time_to_live_seconds=3600
                    )
                    if tp_tx and tp_order_id:
                        self.dydx_real_position["tp_order_id"] = tp_order_id
                        # 同步 dashboard 顯示
                        self.pending_tp_order = {
                            'direction': direction,
                            'entry_price': entry_price_base,
                            'tp_price': tp_price,
                            'target_pct': target_pct,
                            'leverage': leverage,
                            'created_time': time.time(),
                            'status': 'PENDING',
                            'dydx_order_id': tp_order_id,
                        }
                        # 🧠 註冊 dYdX 訂單
                        self._register_dydx_order(tp_order_id, order_type="LONG_TERM", kind="TP")
                        self._journal_dydx_event(
                            "tp_placed",
                            order_id=tp_order_id,
                            order_type="LONG_TERM",
                            tp_price=tp_price,
                            target_pct=target_pct,
                            leverage=leverage,
                            tx=str(tp_tx)[:200] if tp_tx else None,
                        )
                        print(f"   ✅ 止盈單已掛! ID: {tp_order_id}")
                    else:
                        print(f"   ⚠️ 止盈單失敗")
                        try:
                            err = self.dydx_api.get_last_tx_error() if self.dydx_api else {}
                        except Exception:
                            err = {}
                        swept = await self._dydx_sweep_on_limit_error(err, reason="tp_place_limit")
                        if swept:
                            tp_tx2, tp_order_id2 = await self.dydx_api.place_take_profit_order(
                                side=direction,
                                size=size,
                                tp_price=tp_price,
                                time_to_live_seconds=3600,
                            )
                            if tp_tx2 and tp_order_id2:
                                self.dydx_real_position["tp_order_id"] = tp_order_id2
                                self.pending_tp_order = {
                                    'direction': direction,
                                    'entry_price': entry_price_base,
                                    'tp_price': tp_price,
                                    'target_pct': target_pct,
                                    'leverage': leverage,
                                    'created_time': time.time(),
                                    'status': 'PENDING',
                                    'dydx_order_id': tp_order_id2,
                                }
                                self._register_dydx_order(tp_order_id2, order_type="LONG_TERM", kind="TP")
                                self._journal_dydx_event(
                                    "tp_placed",
                                    order_id=tp_order_id2,
                                    order_type="LONG_TERM",
                                    tp_price=tp_price,
                                    target_pct=target_pct,
                                    leverage=leverage,
                                    tx=str(tp_tx2)[:200] if tp_tx2 else None,
                                )
                                print(f"   ✅ 止盈單已掛! ID: {tp_order_id2}")
                            else:
                                try:
                                    err = self.dydx_api.get_last_tx_error() if self.dydx_api else {}
                                except Exception:
                                    err = {}
                                self._journal_dydx_event(
                                    "tp_place_failed",
                                    tp_price=tp_price,
                                    target_pct=target_pct,
                                    leverage=leverage,
                                    attempt=2,
                                    last_tx_error=err,
                                )
                        else:
                            self._journal_dydx_event(
                                "tp_place_failed",
                                tp_price=tp_price,
                                target_pct=target_pct,
                                leverage=leverage,
                                attempt=1,
                                last_tx_error=err,
                            )
                except Exception as e:
                    print(f"   ❌ 止盈單異常: {e}")
                    try:
                        err = self.dydx_api.get_last_tx_error() if self.dydx_api else {}
                    except Exception:
                        err = {}
                    self._journal_dydx_event(
                        "tp_place_exception",
                        tp_price=tp_price,
                        target_pct=target_pct,
                        leverage=leverage,
                        error=str(e),
                        last_tx_error=err,
                    )

                try:
                    await self._log_dydx_protection_snapshot(reason="post_open")
                except Exception:
                    pass
                
                # 顯示真實餘額
                balance = await self.dydx_api.get_account_balance()
                print(f"💰 dYdX 餘額: ${balance:.2f}")
                return True, fill_price
            else:
                print("⚠️ dYdX 開倉失敗")
                self._journal_dydx_event(
                    "open_failed",
                    side=direction,
                    size=size,
                    tx_present=bool(tx_hash),
                    fill_price=fill_price,
                )
                return False, 0.0
                
        except Exception as e:
            print(f"❌ dYdX 開倉失敗: {e}")
            self._journal_dydx_event(
                "open_exception",
                side=direction,
                error=str(e),
            )
            return False, 0.0
    
    async def _dydx_close_position(self, reason: str, is_stop_loss: bool = False) -> tuple[bool, float]:
        """
        🆕 在 dYdX 平真實倉位
        
        🆕 v14.5: 新增執行時間追蹤
        
        Args:
            reason: 平倉原因
            is_stop_loss: 是否止損 (止損用市價，止盈用 Maker)
        
        Returns:
            (是否成功, 成交價格)
        """
        if not self.dydx_sync_enabled or not self.dydx_api or not self.dydx_real_position:
            return False, 0.0
        
        import time as time_module
        start_time = time_module.time()
        
        try:
            pos = self.dydx_real_position

            async def _fetch_rest_position(fallback_entry_price: float):
                try:
                    positions = await self.dydx_api.get_positions()
                except Exception:
                    return None

                for rest_pos in positions or []:
                    if rest_pos.get("market") != "BTC-USD":
                        continue
                    raw_size = _coerce_float(rest_pos.get("size", 0.0), default=0.0)
                    if abs(raw_size) <= 0.0001:
                        continue

                    side_raw = str(rest_pos.get("side", "")).upper()
                    if side_raw in ("BUY", "LONG"):
                        side = "LONG"
                    elif side_raw in ("SELL", "SHORT"):
                        side = "SHORT"
                    else:
                        side = "LONG" if raw_size > 0 else "SHORT"

                    entry_price = _coerce_float(rest_pos.get("entryPrice", 0.0), default=0.0)
                    if entry_price <= 0:
                        entry_price = fallback_entry_price

                    return {
                        "side": side,
                        "size": abs(raw_size),
                        "entry_price": entry_price,
                        "entry_time": datetime.now(),
                    }

                return None

            rest_mismatch = False
            rest_pos = await _fetch_rest_position(_coerce_float(pos.get("entry_price", 0.0), default=0.0))
            if rest_pos:
                local_side = str(pos.get("side", "")).upper() if pos else ""
                if local_side and rest_pos["side"] != local_side:
                    rest_mismatch = True
                    print(f"⚠️ [REST] 持倉方向不一致，本地={local_side} REST={rest_pos['side']}，以 REST 為準")
                self.dydx_real_position = rest_pos
                pos = rest_pos
            elif pos:
                print("⚠️ [REST] 未取得持倉，先使用本地追蹤資料平倉")

            # ✅ 平倉前先把未平倉掛單清空（避免 TP/SL 交錯成交、或留單累積）
            await self._dydx_sweep_open_orders(reason=f"pre_close:{reason}", market="BTC-USD")
            
            max_attempts = 2 if rest_mismatch else 1
            tx_hash = None
            fill_price = 0.0
            trigger_price = pos.get("entry_price", 0.0)

            for attempt in range(1, max_attempts + 1):
                if attempt > 1:
                    retry_pos = await _fetch_rest_position(_coerce_float(pos.get("entry_price", 0.0), default=0.0))
                    if retry_pos:
                        self.dydx_real_position = retry_pos
                        pos = retry_pos
                    print("🔁 [REST] 方向不一致，重試平倉一次")

                # 🆕 v14.5: 記錄觸發時的即時價格 (用於計算滑點)
                try:
                    trigger_price = await self.dydx_api.get_price()
                except Exception:
                    trigger_price = pos.get("entry_price", 0.0)  # fallback

                label = "同步平倉" if attempt == 1 else "重試平倉"
                print(f"🔴 dYdX {label}: {pos['side']} {pos['size']:.4f} BTC | 原因: {reason}")
                print(f"   📊 觸發價: ${trigger_price:,.2f}")

                self._journal_dydx_event(
                    "close_attempt" if attempt == 1 else "close_retry",
                    side=pos.get('side'),
                    size=pos.get('size'),
                    entry_price=pos.get('entry_price'),
                    trigger_price=trigger_price,
                    reason=reason,
                    is_stop_loss=is_stop_loss,
                    attempt=attempt,
                )

                # 使用 Aggressive 平倉 (止損用市價)
                tx_hash, fill_price = await self.dydx_api.close_position_aggressive(
                    side=pos['side'],
                    size=pos['size'],
                    timeout_seconds=5.0,
                    is_stop_loss=is_stop_loss
                )

                if tx_hash:
                    break
            
            execution_time = time_module.time() - start_time
            
            if tx_hash:
                # 計算盈虧
                if pos['side'] == "LONG":
                    pnl_pct = (fill_price - pos['entry_price']) / pos['entry_price'] * 100
                else:
                    pnl_pct = (pos['entry_price'] - fill_price) / pos['entry_price'] * 100
                
                pnl_usd = pos['size'] * pos['entry_price'] * (pnl_pct / 100)
                
                # 🆕 v14.5: 計算滑點
                slippage_usd = abs(fill_price - trigger_price)
                slippage_pct = slippage_usd / trigger_price * 100
                
                emoji = "🟢" if pnl_usd > 0 else "🔴"
                print(f"✅ dYdX 平倉成功!")
                print(f"   💰 成交價: ${fill_price:,.2f} | {emoji} {pnl_pct:+.2f}% (${pnl_usd:+.2f})")
                print(f"   ⏱️ 執行時間: {execution_time:.2f}s | 滑點: ${slippage_usd:.2f} ({slippage_pct:.3f}%)")
                
                # 顯示真實餘額
                balance = await self.dydx_api.get_account_balance()
                print(f"💰 dYdX 真實餘額: ${balance:.2f}")

                self._journal_dydx_event(
                    "close_filled",
                    side=pos.get('side'),
                    size=pos.get('size'),
                    entry_price=pos.get('entry_price'),
                    fill_price=fill_price,
                    pnl_pct=pnl_pct,
                    pnl_usd=pnl_usd,
                    execution_time_sec=execution_time,
                    reason=reason,
                    tx=str(tx_hash)[:200] if tx_hash else None,
                )
                
                # 🆕 v14.6.11: 等待 WebSocket 確認平倉 (最多 3 秒)
                if hasattr(self, 'dydx_ws') and self.dydx_ws:
                    for _ in range(6):  # 最多等 3 秒 (6 x 0.5s)
                        await asyncio.sleep(0.5)
                        if not self.dydx_ws.has_position("BTC-USD"):
                            print(f"📶 [WS] 平倉已確認!")
                            break

                # ✅ v14.6.26: 平倉後必須清空所有未成交掛單，才能開新單
                await self._dydx_sweep_open_orders(reason=f"post_close:{reason}", market="BTC-USD")
                
                # 清除本地追蹤
                self.pending_sl_order = None
                self.pending_tp_order = None
                post_pos = await _fetch_rest_position(_coerce_float(pos.get("entry_price", 0.0), default=0.0))
                if post_pos:
                    self.dydx_real_position = post_pos
                    print("⚠️ [REST] 平倉後仍有持倉，保留實倉追蹤")
                else:
                    self.dydx_real_position = None

                if post_pos:
                    print("   ⚠️ REST仍有持倉，暫不開新單")
                else:
                    print(f"   ✅ 所有掛單已清空，可以開新單")
                return True, fill_price
            else:
                print(f"❌ dYdX 平倉失敗 (耗時: {execution_time:.2f}s)")
                self._journal_dydx_event(
                    "close_failed",
                    side=pos.get('side'),
                    size=pos.get('size'),
                    entry_price=pos.get('entry_price'),
                    execution_time_sec=execution_time,
                    reason=reason,
                )
                return False, 0.0
                
        except Exception as e:
            execution_time = time_module.time() - start_time
            print(f"❌ dYdX 平倉錯誤: {e} (耗時: {execution_time:.2f}s)")
            try:
                self._journal_dydx_event(
                    "close_exception",
                    error=str(e),
                    execution_time_sec=execution_time,
                    reason=reason,
                )
            except Exception:
                pass
            return False, 0.0
    
    def _init_exchange(self) -> ccxt.Exchange:
        """初始化交易所連接"""
        # 優先讀取 .env 文件
        env_file = Path(".env")
        api_key = ""
        api_secret = ""
        
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('BINANCE_TESTNET_API_KEY='):
                        api_key = line.split('=', 1)[1].strip()
                    elif line.startswith('BINANCE_TESTNET_API_SECRET='):
                        api_secret = line.split('=', 1)[1].strip()
        
        # 如果 .env 沒有，嘗試 config.json
        if not api_key or not api_secret:
            config_file = Path("config/config.json")
            if config_file.exists():
                with open(config_file) as f:
                    cfg = json.load(f)
                testnet_cfg = cfg.get('binance_testnet', {})
                binance_cfg = cfg.get('binance', {})
                api_key = testnet_cfg.get('api_key') or binance_cfg.get('api_key', '')
                api_secret = testnet_cfg.get('api_secret') or binance_cfg.get('api_secret', '')
        
        # 檢查 API key 是否已設定
        if not api_key or api_key.startswith('YOUR_'):
            print(f"\n{'='*60}")
            print(f"⚠️  請先設定 Binance Testnet API Key!")
            print(f"")
            print(f"方法1: 編輯 .env 文件:")
            print(f"   BINANCE_TESTNET_API_KEY=你的KEY")
            print(f"   BINANCE_TESTNET_API_SECRET=你的SECRET")
            print(f"")
            print(f"方法2: 編輯 config/config.json")
            print(f"")
            print(f"取得位置: https://testnet.binancefuture.com/")
            print(f"{'='*60}\n")
        else:
            print(f"✅ 已讀取 Testnet API Key (來自 .env)")
        
        # Testnet 配置
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
                'adjustForTimeDifference': True,
            },
            'sandbox': True,  # 啟用 Testnet
        })
        
        # 設置 Testnet endpoints
        exchange.set_sandbox_mode(True)
        
        return exchange
    
    def _load_trades(self):
        """載入今日交易記錄"""
        if self.trades_file.exists():
            with open(self.trades_file) as f:
                data = json.load(f)
            self.trades = [TradeRecord(**t) for t in data.get('trades', [])]
            self.daily_trades = data.get('daily_trades', 0)
            self.daily_pnl = data.get('daily_pnl', 0.0)
    
    def _save_trades(self):
        """保存交易記錄"""
        # 計算統計資訊
        closed_trades = [t for t in self.trades if t.status != "OPEN"]
        wins = [t for t in closed_trades if t.net_pnl_usdt > 0]
        losses = [t for t in closed_trades if t.net_pnl_usdt <= 0]
        
        data = {
            'trades': [t.to_dict() for t in self.trades],
            'daily_trades': self.daily_trades,
            'daily_pnl': self.daily_pnl,
            'total_pnl': self.total_pnl,
            'last_updated': datetime.now().isoformat(),
            # 🆕 v14.9.7: 增加統計摘要 (方便分析)
            'statistics': {
                'total_trades': len(closed_trades),
                'open_trades': len([t for t in self.trades if t.status == "OPEN"]),
                'wins': len(wins),
                'losses': len(losses),
                'win_rate': len(wins) / len(closed_trades) * 100 if closed_trades else 0,
                'total_pnl_usdt': sum(t.net_pnl_usdt for t in closed_trades),
                'avg_win': sum(t.net_pnl_usdt for t in wins) / len(wins) if wins else 0,
                'avg_loss': sum(t.net_pnl_usdt for t in losses) / len(losses) if losses else 0,
                'avg_hold_seconds': sum(t.hold_seconds for t in closed_trades) / len(closed_trades) if closed_trades else 0,
                'profit_factor': abs(sum(t.net_pnl_usdt for t in wins) / sum(t.net_pnl_usdt for t in losses)) if losses and sum(t.net_pnl_usdt for t in losses) != 0 else 0,
            },
            # 🆕 會話資訊
            'session': {
                'mode': 'live' if not self.paper_mode else 'paper',
                'dydx_sync': self.dydx_sync_enabled,
                'initial_balance': self.initial_balance,
                'current_balance': self.current_balance,
                'session_start': self.session_start_time.isoformat() if hasattr(self, 'session_start_time') else None,
            }
        }
        with open(self.trades_file, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def _save_signal_log(self, signal_type: str, direction: str, reason: str, 
                         market_data: Dict = None, six_dim: Dict = None, 
                         mtf_filter: Dict = None, alignment: Dict = None):
        """
        🆕 v10.18: 實時記錄信號到獨立的 signals_*.json 檔案
        包含進場和被拒絕的情況，方便事後分析
        不管有沒有交易都會記錄！
        """
        market_data = market_data or {}
        six_dim = six_dim or {}
        mtf_filter = mtf_filter or {}
        alignment = alignment or {}
        
        signal_record = {
            'timestamp': datetime.now().isoformat(),
            'signal_type': signal_type,  # REJECTED_MTF, REJECTED_SCORE, ENTERED
            'direction': direction,
            'reason': reason,
            'price': market_data.get('current_price', 0),
            # 六維資訊
            'six_dim': {
                'long_score': six_dim.get('long_score', 0),
                'short_score': six_dim.get('short_score', 0),
                'fast_dir': six_dim.get('fast_dir', ''),
                'medium_dir': six_dim.get('medium_dir', ''),
                'slow_dir': six_dim.get('slow_dir', ''),
                'obi_dir': six_dim.get('obi_dir', ''),
                'momentum_dir': six_dim.get('momentum_dir', ''),
                'volume_dir': six_dim.get('volume_dir', ''),
            },
            # MTF 資訊 (v14.16: 移除不存在的 rsi_30m, rsi_2h)
            'mtf': {
                'rsi_1m': mtf_filter.get('rsi_1m') or 0,
                'rsi_5m': mtf_filter.get('rsi_5m') or 0,
                'rsi_15m': mtf_filter.get('rsi_15m') or 0,
                'rsi_30m': 0,  # MTF 分析器無此時間框架
                'rsi_1h': mtf_filter.get('rsi_1h') or 0,
                'rsi_2h': 0,  # MTF 分析器無此時間框架
                'rsi_4h': mtf_filter.get('rsi_4h') or 0,
                'direction': mtf_filter.get('mtf_direction') or '',
                'aligned': mtf_filter.get('mtf_aligned', False),
            },
            # 市場狀態
            'market': {
                'obi': market_data.get('obi', 0),
                'regime': market_data.get('regime', ''),
                'strategy': market_data.get('primary_strategy', {}).get('strategy', ''),
            },
            # 對齊時間
            'alignment': alignment
        }
        
        # 添加到信號日誌列表
        self._signal_logs.append(signal_record)
        
        # 🆕 實時保存到獨立的信號檔案 (不是 trades 檔案)
        self._save_signals()
    
    def _save_signals(self):
        """保存信號記錄到獨立檔案"""
        data = {
            'signals': self._signal_logs,
            'total_signals': len(self._signal_logs),
            'entered_count': sum(1 for s in self._signal_logs if s['signal_type'] == 'ENTERED'),
            'rejected_count': sum(1 for s in self._signal_logs if s['signal_type'].startswith('REJECTED')),
            'last_updated': datetime.now().isoformat()
        }
        with open(self.signals_file, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def get_external_position(self) -> Optional[Dict]:
        """
        獲取 Testnet 上的真實持倉 (不是系統開的倉)
        用於顯示外部手動開的持倉
        帶緩存機制，避免每秒查詢 API
        """
        # 🆕 v14.6.25: 如果啟用 dYdX 同步，優先返回 dYdX 持倉
        if getattr(self, 'dydx_sync_enabled', False) and getattr(self, 'dydx_api', None):
            # 優先使用 WebSocket 緩存的持倉 (更即時)
            if hasattr(self, 'dydx_ws') and self.dydx_ws:
                ws_pos = self.dydx_ws.get_position("BTC-USD")
                if ws_pos:
                    size = float(ws_pos.get('raw_size', 0))
                    if abs(size) > 0.0001:
                        # 取得當前價格 (優先用 Oracle)
                        curr_price = getattr(self, 'dydx_oracle_price_cache', 0)
                        if not curr_price:
                            curr_price = getattr(self.dydx_ws, 'current_price', 0)
                        
                        # 取得槓桿 (dYdX 預設 50x)
                        lev = 50
                        try:
                            if hasattr(self.config, 'leverage'):
                                lev = int(self.config.leverage)
                        except: pass

                        return {
                            'side': 'LONG' if size > 0 else 'SHORT',
                            'size': abs(size),
                            'entry_price': float(ws_pos.get('entry_price', 0)),
                            'mark_price': curr_price,
                            'unrealized_pnl': 0, # 暫不計算 USD 盈虧
                            'leverage': lev,
                            'liquidation_price': 0
                        }

            # 回退到 REST API 緩存
            d_pos = getattr(self, 'dydx_real_position', None)
            if d_pos:
                return {
                    'side': d_pos['side'],
                    'size': d_pos['size'],
                    'entry_price': d_pos['entry_price'],
                    'mark_price': getattr(self, 'dydx_oracle_price_cache', 0),
                    'unrealized_pnl': 0,
                    'leverage': 50, # 預設
                    'liquidation_price': 0
                }

        if self.paper_mode or not self.testnet_api:
            return None
        
        # 檢查緩存
        now = time.time()
        if (self._external_position_cache_time > 0 and 
            now - self._external_position_cache_time < self._external_position_cache_ttl):
            return self._external_position_cache
        
        try:
            pos = self.testnet_api.get_position("BTCUSDT")
            if pos:
                amt = float(pos.get('positionAmt', 0))
                if amt != 0:
                    result = {
                        'side': 'LONG' if amt > 0 else 'SHORT',
                        'size': abs(amt),
                        'entry_price': float(pos.get('entryPrice', 0)),
                        'mark_price': float(pos.get('markPrice', 0)),
                        'unrealized_pnl': float(pos.get('unRealizedProfit', 0)),
                        'leverage': int(pos.get('leverage', 100)),
                        'liquidation_price': float(pos.get('liquidationPrice', 0))
                    }
                    self._external_position_cache = result
                    self._external_position_cache_time = now
                    return result
            
            # 無持倉，也緩存結果
            self._external_position_cache = None
            self._external_position_cache_time = now
            return None
        except Exception as e:
            # 不打印錯誤，靜默處理，返回緩存
            return self._external_position_cache
    
    def set_leverage(self) -> bool:
        """設置槓桿"""
        if self.paper_mode:
            print(f"📝 [模擬] 槓桿設置為 {self.config.leverage}X")
            return True
            
        try:
            symbol = self.config.symbol.replace('/', '')
            # 使用 Testnet API
            if self.testnet_api:
                success = self.testnet_api.set_leverage(symbol, self.config.leverage)
                if success:
                    print(f"✅ 槓桿已設置為 {self.config.leverage}X")
                return success
            return False
        except Exception as e:
            print(f"⚠️ 設置槓桿失敗: {e}")
            return False
    
    def _build_random_wave(self) -> List[str]:
        """建立一波隨機方向 (預設 50/50)"""
        import random

        batch_size = getattr(self, "_balanced_batch_size", 20) or 20
        max_streak = getattr(self, "_balanced_max_streak", 3) or 0
        max_imbalance = getattr(self, "_balanced_max_imbalance", 4) or 0

        if getattr(self.config, "random_entry_balance_enabled", True) is False:
            return [random.choice(["LONG", "SHORT"]) for _ in range(batch_size)]

        return _generate_constrained_balanced_sequence(
            batch_size,
            max_streak=max_streak,
            max_imbalance=max_imbalance,
        )

    def _ensure_random_waves(self):
        """確保隨機波次存在 (不會在同波未用盡時回補)"""
        if not self._random_wave1 and not self._random_wave2:
            self._random_wave1 = self._build_random_wave()
            self._random_wave2 = self._build_random_wave()
            if getattr(self.config, "random_entry_balance_enabled", True) is not False:
                half = len(self._random_wave1) // 2
                print(
                    f"🎲 初始化隨機波: {half} LONG + {half} SHORT | batch={len(self._random_wave1)} streak≤{getattr(self, '_balanced_max_streak', 0)} imbalance≤{getattr(self, '_balanced_max_imbalance', 0)}"
                )
        elif self._random_active_wave == 1 and not self._random_wave2:
            self._random_wave2 = self._build_random_wave()
        elif self._random_active_wave == 2 and not self._random_wave1:
            self._random_wave1 = self._build_random_wave()

    def _roll_random_waves_if_needed(self):
        """當目前波次用盡時切換，並回補另一波"""
        if self._random_active_wave == 1 and not self._random_wave1:
            self._random_active_wave = 2
            self._random_wave1 = self._build_random_wave()
            print("🎲 第1波已用盡 → 切換第2波，補第1波")
        elif self._random_active_wave == 2 and not self._random_wave2:
            self._random_active_wave = 1
            self._random_wave2 = self._build_random_wave()
            print("🎲 第2波已用盡 → 切換第1波，補第2波")

    def _get_balanced_random_direction(self) -> str:
        """
        🆕 v14.1 強制平衡隨機進場

        每 N 筆交易保證 LONG:SHORT = 50:50
        並加入 streak/imbalance 約束避免早期偏向。
        """
        import random

        if getattr(self.config, "random_entry_balance_enabled", True) is False:
            return random.choice(["LONG", "SHORT"])

        self._ensure_random_waves()
        self._roll_random_waves_if_needed()

        active_wave = self._random_wave1 if self._random_active_wave == 1 else self._random_wave2
        direction = active_wave.pop(0)
        remaining_long = active_wave.count("LONG")
        remaining_short = active_wave.count("SHORT")
        if len(active_wave) % 5 == 0:
            print(f"   📊 平衡狀態: 剩餘 {remaining_long}L/{remaining_short}S")
        return direction

    def get_balanced_direction_preview(self, count: int) -> List[str]:
        """Preview upcoming balanced random directions without consuming the waves."""
        if getattr(self.config, "random_entry_balance_enabled", True) is False:
            return []

        self._ensure_random_waves()
        preview = list(self._random_wave1) + list(self._random_wave2)
        return preview[:count]
    
    def can_trade(self) -> tuple[bool, str]:
        """檢查是否可以交易"""
        # 檢查持倉
        if self.active_trade:
            return False, "已有持倉中"

        # 連續虧損冷卻
        if time.time() < self.cooldown_until:
            remain = self.cooldown_until - time.time()
            return False, f"連續虧損冷卻中 ({remain/60:.1f}分)"
        
        # 檢查交易間隔
        if time.time() - self.last_trade_time < self.config.min_trade_interval_sec:
            remain = self.config.min_trade_interval_sec - (time.time() - self.last_trade_time)
            return False, f"交易冷卻中 ({remain:.0f}s)"
        
        # 🔧 v10.8: 移除每日交易上限 (讓 AI 自由交易)
        # if self.daily_trades >= self.config.max_daily_trades:
        #     return False, f"已達每日交易上限 ({self.config.max_daily_trades})"
        
        # 檢查每日虧損
        if self.daily_pnl <= -self.config.max_daily_loss_usdt:
            return False, f"已達每日虧損上限 (${self.config.max_daily_loss_usdt})"
        
        return True, "OK"
    
    def calculate_dynamic_params(self, market_data: Dict) -> Dict:
        """
        根據市場波動度動態計算交易參數
        
        高波動 → 放大目標、放大止損、縮短持倉、降低槓桿
        低波動 → 縮小目標、縮小止損、延長持倉、提高槓桿
        
        Returns:
            Dict with: leverage, target_pct, stop_loss_pct, max_hold_min
        """
        # 計算市場波動度 (使用 5 分鐘價格變化)
        volatility = abs(market_data.get('price_change_5m', 0))
        
        # 波動度等級 (0-1)
        # 低波動: < 0.1%  → 0.0
        # 中波動: 0.1-0.3% → 0.5
        # 高波動: > 0.5% → 1.0
        if volatility < 0.1:
            vol_level = 0.0
        elif volatility < 0.3:
            vol_level = (volatility - 0.1) / 0.2 * 0.5
        elif volatility < 0.5:
            vol_level = 0.5 + (volatility - 0.3) / 0.2 * 0.3
        else:
            vol_level = min(1.0, 0.8 + (volatility - 0.5) / 0.5 * 0.2)
        
        # 動態槓桿: 高波動降槓桿，低波動升槓桿
        # 波動 0 → 100X, 波動 1 → 50X
        leverage = int(self.config.leverage_max - vol_level * (self.config.leverage_max - self.config.leverage_min))
        leverage = max(self.config.leverage_min, min(self.config.leverage_max, leverage))
        
        # 動態目標: 高波動放大目標
        # 波動 0 → 0.08%, 波動 1 → 0.18%
        target_pct = self.config.target_profit_min_pct + vol_level * (self.config.target_profit_max_pct - self.config.target_profit_min_pct)
        
        # 動態止損: 高波動放大止損
        # 噪音自適應：取 1m/5m 噪音倍數與預設值的較大者，並限制上限避免過度放寬
        noise_1m = market_data.get('noise_1m_pct', 0)
        noise_5m = market_data.get('noise_5m_pct', 0)
        noise_based_sl_price_pct = max(noise_1m * 3, noise_5m * 2)  # 價格%
        noise_based_sl_roe_pct = noise_based_sl_price_pct * leverage  # 轉為 ROE%
        pre_sl = _coerce_float(
            self.config.pre_stop_loss_pct if self.config.pre_stop_loss_pct is not None else self.config.stop_loss_pct,
            default=0.0,
        )
        stop_loss_pct = max(pre_sl, noise_based_sl_roe_pct)
        stop_loss_pct = min(stop_loss_pct, pre_sl * 2 if pre_sl > 0 else stop_loss_pct)
        
        # 動態持倉時間: 高波動縮短，低波動延長
        # 波動 0 → 20min, 波動 1 → 10min
        max_hold_min = self.config.max_hold_max_minutes - vol_level * (self.config.max_hold_max_minutes - self.config.max_hold_min_minutes)
        
        # 計算預期淨利潤 (槓桿後 ROE%，含手續費)
        # target_pct: 目標「淨」ROE% (扣除手續費後)
        # fee_pct: 名目本金費率(%)，槓桿會放大成 ROE：round-trip = fee_pct * 2 * leverage
        fee_pct = self.config.maker_fee_pct if self.config.use_maker_simulation else self.config.taker_fee_pct
        fee_mult = _fee_leverage_multiplier(self.config, leverage)
        fee_impact_roe_pct = (fee_pct * 2) * fee_mult
        expected_net_profit_pct = target_pct
        expected_gross_profit_pct = target_pct + fee_impact_roe_pct
        
        return {
            'leverage': leverage,
            'target_pct': round(target_pct, 4),
            'stop_loss_pct': round(stop_loss_pct, 4),
            'max_hold_min': round(max_hold_min, 1),
            'volatility': volatility,
            'vol_level': vol_level,
            'expected_net_profit_pct': round(expected_net_profit_pct, 2),
            'expected_gross_profit_pct': round(expected_gross_profit_pct, 2),
            'fee_impact_roe_pct': round(fee_impact_roe_pct, 2),
            'fee_pct': fee_pct
        }
    
    def simulate_maker_entry(self, current_price: float, direction: str, position_btc: float) -> Dict:
        """
        模擬 Maker 分批進場
        
        假設在 10-20 秒內分 5 批掛單進場，每批用略優於市價的價格
        模擬實際掛單成交的價格波動
        
        Returns:
            Dict with: avg_price, batches, duration_sec, slippage_pct
        """
        import random
        
        batches = self.config.maker_entry_batches
        duration = self.config.maker_entry_duration_sec
        offset_pct = self.config.maker_price_offset_pct
        
        batch_size = position_btc / batches
        batch_prices = []
        
        for i in range(batches):
            # 模擬價格波動 (±0.01% 隨機波動)
            price_noise = random.uniform(-0.0001, 0.0001)
            
            # Maker 掛單通常比市價略優
            if direction == "LONG":
                # 買入掛比市價低一點
                batch_price = current_price * (1 - offset_pct / 100 + price_noise)
            else:
                # 賣出掛比市價高一點
                batch_price = current_price * (1 + offset_pct / 100 + price_noise)
            
            batch_prices.append(batch_price)
        
        # 計算加權平均價格
        avg_price = sum(batch_prices) / len(batch_prices)
        
        # 計算實際滑點 (相對於首次報價)
        slippage_pct = abs(avg_price - current_price) / current_price * 100
        
        return {
            'avg_price': avg_price,
            'batches': batches,
            'duration_sec': duration,
            'slippage_pct': slippage_pct,
            'batch_prices': batch_prices,
            'entry_type': 'MAKER'
        }
    
    # ═══════════════════════════════════════════════════════════════════════════════
    # 🆕 v13.3 共用 Veto 檢查 (紙本 + dYdX 同步使用)
    # ═══════════════════════════════════════════════════════════════════════════════
    
    def check_entry_veto(self, direction: str, market_data: Dict, ignore_score: bool = False) -> Tuple[bool, str]:
        """
        🆕 v13.3 統一的 Veto 檢查函數
        
        確保紙本和 dYdX 同步交易使用相同的過濾邏輯
        
        Args:
            direction: "LONG" or "SHORT"
            market_data: 市場數據（包含六維分數、OBI、MTF 等）
            ignore_score: 是否忽略六維分數檢查 (用於隨機進場模式)
        
        Returns:
            (passed, reject_reason) - passed=True 表示通過檢查
        """
        # 🆕 v14.12.1: 獲取 logger (TestnetTrader 可能沒有 self.logger)
        import logging
        logger = getattr(self, 'logger', None)
        if not logger and hasattr(self, '_parent_system'):
            logger = getattr(self._parent_system, 'logger', None)
        if not logger:
            logger = logging.getLogger("WhaleTrader")

        # 🆕 可選：全域關閉 Veto 過濾
        if getattr(self.config, 'entry_veto_enabled', True) is False:
            return True, ""

        # 1. 六維分數檢查 (v14.12: 根據方向使用不同門檻)
        if not ignore_score:
            six_dim = market_data.get('six_dim', {})
            if direction == "LONG":
                score = six_dim.get('long_score', 0)
                # v14.12: LONG使用專用門檻 (校正結果: LONG準確率46.9%, 需要更高門檻)
                min_score = getattr(self.config, 'six_dim_min_score_long', None) or self.config.six_dim_min_score_to_trade
            else:
                score = six_dim.get('short_score', 0)
                # v14.12: SHORT使用專用門檻 (校正結果: SHORT準確率66.7%)
                min_score = getattr(self.config, 'six_dim_min_score_short', None) or self.config.six_dim_min_score_to_trade
            
            if score < min_score:
                reason = f"六維分數不足 ({direction}: {score} < {min_score})"
                logger.info(f"🛡️ Veto 阻擋: {reason}")
                return False, reason
        
        # v14.12: 高信心標記
        six_dim = market_data.get('six_dim', {})
        score = six_dim.get('long_score' if direction == "LONG" else 'short_score', 0)
        high_conf_threshold = getattr(self.config, 'six_dim_high_confidence', None) or 12
        is_high_confidence = score >= high_conf_threshold
        
        # 2. OBI 方向衝突檢查
        obi = market_data.get('obi', 0)
        obi_dir = six_dim.get('obi_dir', 'NEUTRAL')
        momentum_dir = six_dim.get('momentum_dir', 'NEUTRAL')
        volume_dir = six_dim.get('volume_dir', 'NEUTRAL')
        
        # 🆕 v14.16.5: 隨機進場模式下放寬方向衝突檢查
        if direction == "LONG":
            if not ignore_score:
                if obi_dir == 'SHORT' or momentum_dir == 'SHORT' or volume_dir == 'SHORT':
                    reason = f"方向衝突: OBI/動能/量偏空"
                    logger.info(f"🛡️ Veto 阻擋: {reason}")
                    return False, reason
            
            if obi < -0.4:  # 隨機模式也檢查極端背離 (從 -0.2 放寬到 -0.4)
                reason = f"OBI {obi:.2f} < -0.4 嚴重背離"
                logger.info(f"🛡️ Veto 阻擋: {reason}")
                return False, reason
        else:  # SHORT
            if not ignore_score:
                if obi_dir == 'LONG' or momentum_dir == 'LONG' or volume_dir == 'LONG':
                    reason = f"方向衝突: OBI/動能/量偏多"
                    logger.info(f"🛡️ Veto 阻擋: {reason}")
                    return False, reason
            
            if obi > 0.4:  # 隨機模式也檢查極端背離 (從 0.2 放寬到 0.4)
                reason = f"OBI {obi:.2f} > 0.4 嚴重背離"
                logger.info(f"🛡️ Veto 阻擋: {reason}")
                return False, reason
        
        # 3. 價格確認檢查
        if self.config.price_confirm_enabled and not ignore_score:
            price_change_1m = market_data.get('price_change_1m', 0)
            threshold = self.config.price_confirm_threshold
            
            if direction == "LONG" and price_change_1m < -threshold:
                reason = f"價格確認失敗: 做多但價跌 {price_change_1m:.3f}%"
                logger.info(f"🛡️ Veto 阻擋: {reason}")
                return False, reason
            elif direction == "SHORT" and price_change_1m > threshold:
                reason = f"價格確認失敗: 做空但價漲 {price_change_1m:.3f}%"
                logger.info(f"🛡️ Veto 阻擋: {reason}")
                return False, reason
        
        # 4. 交易所價差檢查
        spread_pct = market_data.get('exchange_spread_pct', 0)
        max_exchange_spread_pct = getattr(self.config, 'max_exchange_spread_pct', None)
        if max_exchange_spread_pct is not None and spread_pct and spread_pct > max_exchange_spread_pct:
            reason = f"跨所價差過大 ({spread_pct:.3f}%)"
            logger.info(f"🛡️ Veto 阻擋: {reason}")
            return False, reason
        # 5. Binance OBI 反向檢查
        if self.config.binance_sentiment_enabled:
            bin_obi = market_data.get('binance_obi', None)
            if bin_obi is not None:
                thr = self.config.binance_obi_threshold
                if direction == "LONG" and bin_obi <= -thr:
                    reason = f"Binance OBI 反向 ({bin_obi:.3f})"
                    logger.info(f"🛡️ Veto 阻擋: {reason}")
                    return False, reason
                if direction == "SHORT" and bin_obi >= thr:
                    reason = f"Binance OBI 反向 ({bin_obi:.3f})"
                    logger.info(f"🛡️ Veto 阻擋: {reason}")
                    return False, reason
        
        # 6. 🆕 v14.16: 動能竭盡檢查 (防止追高殺低)
        # 核心邏輯：如果 1 分鐘內已經跑了太遠，通常會伴隨回調，此時進場容易被「割」
        price_change_1m = market_data.get('price_change_1m', 0)
        exhaustion_threshold = getattr(self.config, 'momentum_exhaustion_threshold', 0.15)
        if direction == "LONG" and price_change_1m > exhaustion_threshold:
            reason = f"動能竭盡: 1m 漲幅 {price_change_1m:.3f}% 過大，避免追高"
            logger.info(f"🛡️ Veto 阻擋: {reason}")
            return False, reason
        if direction == "SHORT" and price_change_1m < -exhaustion_threshold:
            reason = f"動能竭盡: 1m 跌幅 {price_change_1m:.3f}% 過大，避免殺低"
            logger.info(f"🛡️ Veto 阻擋: {reason}")
            return False, reason

        # 7. 🆕 v14.16: 趨勢一致性檢查 (1m vs 5m)
        # 核心邏輯：大趨勢不對時，小級別的隨機進場容易失敗
        price_change_5m = market_data.get('price_change_5m', 0)
        # 🔧 v14.16.1: 放寬趨勢門檻 (0.05% -> 0.15%) 並修正格式
        trend_threshold = 0.15
        if direction == "LONG" and price_change_5m < -trend_threshold:
            reason = f"趨勢逆向: 5m 趨勢偏空 ({price_change_5m:.3f}%)"
            logger.info(f"🛡️ Veto 阻擋: {reason}")
            return False, reason
        if direction == "SHORT" and price_change_5m > trend_threshold:
            reason = f"趨勢逆向: 5m 趨勢偏多 ({price_change_5m:.3f}%)"
            logger.info(f"🛡️ Veto 阻擋: {reason}")
            return False, reason

        # ✅ 通過所有 Veto 檢查
        return True, ""
    
    # ═══════════════════════════════════════════════════════════════════════════════
    # 🆕 v12.0 預掛單模式 (提前進場，更好價格)
    # ═══════════════════════════════════════════════════════════════════════════════
    
    def place_pre_entry_order(
        self,
        direction: str,
        current_price: float,
        signal_strength: float,
        market_data: Dict
    ) -> Optional[Dict]:
        """
        🆕 v12.0 預掛單進場 (v12.12.1 增強過濾)
        
        信號達到閾值(如 90%)時，提前掛 Limit Order
        - 做空: 掛賣單在當前價 (或 +$5 更好價格)
        - 做多: 掛買單在當前價 (或 -$5 更好價格)
        
        🔧 v12.12.1: 增加智能過濾
        - 1分鐘價格變化方向必須與交易方向一致
        - 六維信號不能完全對稱 (必須有明顯傾向)
        
        Args:
            direction: "LONG" or "SHORT"
            current_price: 當前價格
            signal_strength: 信號強度 (0-1)
            market_data: 市場數據
        
        Returns:
            掛單資訊 Dict 或 None
        """
        if not self.pre_entry_mode:
            return None
        
        # 已有掛單或持倉，不重複掛
        if self.pending_entry_order or self.active_trade:
            return None
        
        # 🔧 v14.1: 檢查價格是否有效 (防止 API rate limit 導致的 0 價格)
        if current_price <= 0:
            return None
        
        # 檢查是否可以交易
        can, reason = self.can_trade()
        if not can:
            return None
        
        # 🆕 v13.3: 共用 Veto 檢查 (確保紙本和 dYdX 同步使用相同邏輯)
        veto_passed, veto_reason = self.check_entry_veto(direction, market_data)
        if not veto_passed:
            print(f"⛔ 預掛單被 Veto: {veto_reason}")
            return None
        
        # 計算掛單價格 (確保立即成交)
        # 🔧 v13.2: 改用百分比適應高價位 BTC
        # 用戶選擇: 0.005% (約 $4.6) 幾乎等於市價，但技術上是 Maker
        slippage_pct = 0.005 / 100 
        offset = current_price * slippage_pct
        
        if direction == "LONG":
            # 🔧 做多: 掛買單在當前價 -0.05% (等待回調接針)
            # 這變成了 Maker 單，只有價格跌下來才會成交
            order_price = current_price - offset
        else:
            # 🔧 做空: 掛賣單在當前價 +0.05% (等待反彈接針)
            order_price = current_price + offset
        
        # 計算倉位大小
        position_btc = self.config.position_size_usdt / current_price
        import math
        position_btc = math.ceil(position_btc * 1000) / 1000
        
        # 創建預掛單記錄
        self.pending_entry_order = {
            'direction': direction,
            'order_price': order_price,
            'current_price': current_price,
            'position_btc': position_btc,
            'signal_strength': signal_strength,
            'created_time': time.time(),
            'market_data': market_data.copy(),
            'status': 'PENDING',  # PENDING, FILLED, CANCELLED
            'fill_price': None,
            'fill_time': None
        }
        
        print(f"\n{'='*60}")
        print(f"📋 預掛單已建立 [{direction}]")
        print(f"   信號強度: {signal_strength*100:.1f}%")
        print(f"   當前價格: ${current_price:,.2f}")
        print(f"   掛單價格: ${order_price:,.2f} ({'↓' if direction == 'LONG' else '↑'}${offset})")
        print(f"   倉位: {position_btc:.4f} BTC")
        print(f"   等待成交...")
        print(f"{'='*60}\n")
        
        return self.pending_entry_order
    
    def check_pre_entry_fill(self, current_price: float, signal_strength: float) -> Optional[float]:
        """
        🆕 v12.0 檢查預掛單是否成交
        
        模擬 Limit Order 成交邏輯:
        - 做多掛單: 價格跌到或低於掛單價 → 成交
        - 做空掛單: 價格漲到或高於掛單價 → 成交
        
        Args:
            current_price: 當前價格
            signal_strength: 當前信號強度
        
        Returns:
            成交價格 或 None
        """
        if not self.pending_entry_order:
            return None
        
        order = self.pending_entry_order
        order_price = order['order_price']
        direction = order['direction']
        created_time = order['created_time']
        
        # 檢查是否需要取消 (信號減弱或超時)
        elapsed = time.time() - created_time
        
        # 1. 信號減弱到取消閾值
        if signal_strength < self.config.pre_entry_cancel_threshold:
            print(f"⚠️ 預掛單取消: 信號減弱到 {signal_strength*100:.1f}% < {self.config.pre_entry_cancel_threshold*100:.0f}%")
            self.cancel_pre_entry_order("信號減弱")
            return None
        
        # 2. 超時
        if elapsed > self.config.pre_entry_timeout_sec:
            print(f"⚠️ 預掛單取消: 超時 {elapsed:.0f}秒 > {self.config.pre_entry_timeout_sec:.0f}秒")
            self.cancel_pre_entry_order("超時")
            return None
        
        # 3. 檢查是否成交
        filled = False
        if direction == "LONG":
            # 做多: 價格跌到掛單價以下就成交
            if current_price <= order_price:
                filled = True
        else:
            # 做空: 價格漲到掛單價以上就成交
            if current_price >= order_price:
                filled = True
        
        if filled:
            # 成交! 用掛單價作為成交價 (Limit Order 保證不比掛單價差)
            fill_price = order_price
            order['status'] = 'FILLED'
            order['fill_price'] = fill_price
            order['fill_time'] = time.time()
            
            print(f"\n{'='*60}")
            print(f"✅ 預掛單成交! [{direction}]")
            print(f"   成交價: ${fill_price:,.2f}")
            print(f"   當前價: ${current_price:,.2f}")
            print(f"   優於市價: ${abs(current_price - fill_price):.2f}")
            print(f"   等待時間: {elapsed:.1f}秒")
            print(f"{'='*60}\n")
            
            return fill_price
        
        return None
    
    def cancel_pre_entry_order(self, reason: str = "手動取消"):
        """🆕 v12.0 取消預掛單"""
        if self.pending_entry_order:
            order = self.pending_entry_order
            print(f"❌ 預掛單已取消: {order['direction']} @ ${order['order_price']:,.2f} | 原因: {reason}")
            self.pending_entry_order = None
    
    def place_pre_take_profit_order(self, entry_price: float, direction: str, leverage: int) -> Optional[Dict]:
        """
        🆕 v12.0 掛止盈單
        
        進場成交後，立刻計算止盈價格並掛單
        🔧 v12.1: 支援 dYdX 同步掛止盈單
        
        Args:
            entry_price: 進場價格
            direction: "LONG" or "SHORT"
            leverage: 槓桿倍數
        
        Returns:
            止盈掛單資訊
        """
        target_pct = self.config.pre_take_profit_pct  # 目標淨利 % (槓桿後 ROE)

        leverage = _coerce_float(leverage, default=50.0)
        if leverage <= 0:
            leverage = 50.0

        # 計算止盈價格 (含手續費)
        # net_ROE = (price_move_pct - total_fee_pct) * leverage
        # price_move_pct = net_ROE/leverage + total_fee_pct
        maker_fee_pct = _coerce_float(getattr(self.config, 'maker_fee_pct', 0.0), default=0.0)
        taker_fee_pct = _coerce_float(getattr(self.config, 'taker_fee_pct', 0.0), default=0.0)
        entry_fee_pct = maker_fee_pct if self.config.use_maker_simulation else taker_fee_pct
        exit_fee_pct = maker_fee_pct if self.config.use_maker_simulation else taker_fee_pct
        total_fee_pct = entry_fee_pct + exit_fee_pct

        price_move_pct = _coerce_float(target_pct, default=0.0) / leverage + total_fee_pct
        
        if direction == "LONG":
            # 做多: 止盈價 = 進場價 × (1 + 價格變動%)
            tp_price = entry_price * (1 + price_move_pct / 100)
        else:
            # 做空: 止盈價 = 進場價 × (1 - 價格變動%)
            tp_price = entry_price * (1 - price_move_pct / 100)
        
        self.pending_tp_order = {
            'direction': direction,
            'entry_price': entry_price,
            'tp_price': tp_price,
            'target_pct': target_pct,
            'leverage': leverage,
            'created_time': time.time(),
            'status': 'PENDING',
            'dydx_order_id': None  # 🆕 追蹤 dYdX 訂單 ID
        }
        
        print(f"📈 止盈掛單已設定:")
        print(f"   進場價: ${entry_price:,.2f}")
        print(f"   止盈價: ${tp_price:,.2f}")
        print(f"   目標: +{target_pct:.1f}% (槓桿後)")
        print(f"   價格變動需: {'+' if direction == 'LONG' else '-'}{price_move_pct:.3f}%")
        
        # 🆕 v12.1: dYdX 同步掛止盈單
        # 🔧 v14.6.17: 檢查是否已有 TP 訂單，避免重複掛單
        if self.dydx_sync_enabled and self.dydx_api and self.dydx_real_position:
            existing_tp_id = self.dydx_real_position.get("tp_order_id", 0)
            if existing_tp_id and existing_tp_id > 0:
                print(f"   ⚠️ 已有 dYdX TP 訂單 ID: {existing_tp_id}，跳過重複掛單")
            else:
                try:
                    import asyncio
                    dydx_size = self.dydx_real_position.get('size', 0)
                    if dydx_size > 0:
                        print(f"   🔴 同步 dYdX 止盈單...")
                        tx_hash, order_id = asyncio.run(
                            self.dydx_api.place_take_profit_order(
                                side=direction,
                                size=dydx_size,
                                tp_price=tp_price,
                                time_to_live_seconds=3600  # 1 小時有效
                            )
                        )
                        if tx_hash and order_id:
                            self.pending_tp_order['dydx_order_id'] = order_id
                            self.dydx_real_position["tp_order_id"] = order_id  # 🔧 v14.6.17: 記錄以防重複
                            print(f"   ✅ dYdX 止盈單已掛! ID: {order_id}")
                        else:
                            print(f"   ⚠️ dYdX 止盈單掛單失敗")
                except Exception as e:
                    print(f"   ⚠️ dYdX 止盈單同步失敗: {e}")
        
        return self.pending_tp_order
    
    def place_pre_stop_loss_order(self, entry_price: float, direction: str, leverage: int, stop_pct: float = None) -> Optional[Dict]:
        """
        🆕 v14.6 掛止損單
        
        進場成交後，立刻計算止損價格並掛單
        dYdX 支援同時掛 TP 和 SL (都是 reduce_only)
        
        Args:
            entry_price: 進場價格
            direction: "LONG" or "SHORT"
            leverage: 槓桿倍數
            stop_pct: 止損% (槓桿後)，預設使用 config.pre_stop_loss_pct
        
        Returns:
            止損掛單資訊
        """
        if stop_pct is None:
            stop_pct = self.config.pre_stop_loss_pct  # 止損% (槓桿後 ROE)

        leverage = _coerce_float(leverage, default=50.0)
        if leverage <= 0:
            leverage = 50.0

        # 計算止損價格 (含手續費，保守估計平倉為 Taker)
        # net_ROE = (price_move_pct - total_fee_pct) * leverage
        # price_move_pct = -net_loss/leverage + total_fee_pct  (loss is negative)
        maker_fee_pct = _coerce_float(getattr(self.config, 'maker_fee_pct', 0.0), default=0.0)
        taker_fee_pct = _coerce_float(getattr(self.config, 'taker_fee_pct', 0.0), default=0.0)
        entry_fee_pct = maker_fee_pct if self.config.use_maker_simulation else taker_fee_pct
        exit_fee_pct = taker_fee_pct
        if getattr(self.config, 'taker_on_emergency_only', False):
            exit_fee_pct = entry_fee_pct if self.config.use_maker_simulation else taker_fee_pct
        total_fee_pct = entry_fee_pct + exit_fee_pct

        stop_pct = _coerce_float(stop_pct, default=0.0)
        fee_mult = _fee_leverage_multiplier(self.config, leverage)
        min_stop_pct = total_fee_pct * fee_mult + 0.1
        if stop_pct < min_stop_pct:
            stop_pct = min_stop_pct

        signed_price_move_pct = (-stop_pct / leverage) + total_fee_pct

        if direction == "LONG":
            sl_price = entry_price * (1 + signed_price_move_pct / 100)
        else:
            sl_price = entry_price * (1 - signed_price_move_pct / 100)
        
        self.pending_sl_order = {
            'direction': direction,
            'entry_price': entry_price,
            'sl_price': sl_price,
            'stop_pct': -stop_pct,  # 🔧 v14.6.28: 統一符號（負=虧損）
            'leverage': leverage,
            'created_time': time.time(),
            'status': 'PENDING',
            'dydx_order_id': None  # 追蹤 dYdX 訂單 ID
        }
        
        print(f"📉 止損掛單已設定:")
        print(f"   進場價: ${entry_price:,.2f}")
        print(f"   止損價: ${sl_price:,.2f}")
        print(f"   止損: -{stop_pct:.1f}% (槓桿後)")
        print(f"   價格變動需: {'-' if direction == 'LONG' else '+'}{abs(signed_price_move_pct):.3f}% (含費)")
        
        # 🆕 v14.6: dYdX 同步掛止損單
        if self.dydx_sync_enabled and self.dydx_api and self.dydx_real_position:
            try:
                import asyncio
                dydx_size = self.dydx_real_position.get('size', 0)
                if dydx_size > 0:
                    print(f"   🔴 同步 dYdX 止損單...")
                    tx_hash, order_id = asyncio.run(
                        self.dydx_api.place_stop_loss_order(
                            side=direction,
                            size=dydx_size,
                            stop_price=sl_price,
                            time_to_live_seconds=3600  # 1 小時有效
                        )
                    )
                    if tx_hash and order_id:
                        self.pending_sl_order['dydx_order_id'] = order_id
                        print(f"   ✅ dYdX 止損單已掛! ID: {order_id}")
                    else:
                        print(f"   ⚠️ dYdX 止損單掛單失敗")
            except Exception as e:
                print(f"   ⚠️ dYdX 止損單同步失敗: {e}")
        
        return self.pending_sl_order
    
    def place_dydx_tp_sl_orders(self, entry_price: float, direction: str, leverage: int) -> Dict[str, bool]:
        """
        🆕 v14.6 同時掛止盈和止損單 (dYdX 雙向預掛)
        
        dYdX v4 支援同時掛多個 reduce_only 訂單:
        - TP: 止盈限價單 (LIMIT + reduce_only)
        - SL: 止損條件單 (STOP_LOSS + reduce_only)
        
        當其中一個成交後，需要取消另一個
        
        Args:
            entry_price: 進場價格
            direction: "LONG" or "SHORT"
            leverage: 槓桿倍數
        
        Returns:
            {'tp': True/False, 'sl': True/False}
        """
        result = {'tp': False, 'sl': False}
        
        if not self.dydx_sync_enabled or not self.dydx_api:
            print("⚠️ dYdX Sync 未啟用，僅設定 Paper 掛單")
            self.place_pre_take_profit_order(entry_price, direction, leverage)
            return result

        import asyncio

        # 確認 dYdX 真實持倉，避免本地追蹤過時時誤掛單
        positions = None
        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False

        if in_loop:
            print("⚠️ dYdX 持倉確認需要同步執行，略過掛單 (避免 async loop nested)")
            return result

        try:
            if hasattr(self.dydx_api, "get_positions_fresh"):
                positions = asyncio.run(self.dydx_api.get_positions_fresh())
            else:
                positions = asyncio.run(self.dydx_api.get_positions())
        except Exception as e:
            print(f"⚠️ dYdX 持倉確認失敗: {e}")
            return result

        actual_side = None
        actual_size = 0.0
        actual_entry = 0.0
        for pos in positions or []:
            if pos.get("market") != "BTC-USD":
                continue
            raw_size = _coerce_float(pos.get("size", 0.0), default=0.0)
            if abs(raw_size) <= 0.0001:
                continue
            actual_side = "LONG" if raw_size > 0 else "SHORT"
            actual_size = abs(raw_size)
            actual_entry = _coerce_float(pos.get("entryPrice", 0.0), default=0.0)
            break

        if not actual_side or actual_size <= 0:
            print("⚠️ dYdX 無持倉，跳過掛單")
            return result

        if actual_entry <= 0:
            try:
                fills = asyncio.run(self.dydx_api.get_recent_fills(limit=5))
            except Exception:
                fills = []
            close_side = "BUY" if actual_side == "LONG" else "SELL"
            for fill in fills or []:
                if fill.get("market") != "BTC-USD":
                    continue
                if fill.get("side") == close_side:
                    actual_entry = _coerce_float(fill.get("price", 0.0), default=0.0)
                    if actual_entry > 0:
                        break

        if actual_entry > 0 and abs(actual_entry - entry_price) / max(entry_price, 1.0) > 0.001:
            print(f"   ⚠️ dYdX 進場價不同步，改用實際進場價 ${actual_entry:,.2f}")
            entry_price = actual_entry

        if actual_side != direction:
            print(f"   ⚠️ dYdX 方向不同步，改用實際方向 {actual_side}")
            direction = actual_side

        existing_tp_id = 0
        existing_sl_id = 0
        if self.dydx_real_position:
            existing_tp_id = self.dydx_real_position.get("tp_order_id", 0)
            existing_sl_id = self.dydx_real_position.get("sl_order_id", 0)

        self.dydx_real_position = {
            "side": direction,
            "size": actual_size,
            "entry_price": entry_price,
            "entry_time": datetime.now(),
            "tp_order_id": existing_tp_id,
            "sl_order_id": existing_sl_id,
        }

        dydx_size = actual_size

        # 先清掉殘留掛單，避免新掛單被舊單卡住
        sweep_interval_sec = _coerce_float(
            getattr(self.config, "dydx_bracket_sweep_interval_sec", 5.0),
            default=5.0,
        )
        now_ts = time.time()
        last_sweep_ts = getattr(self, "_last_dydx_bracket_sweep_ts", 0.0)
        if (now_ts - last_sweep_ts) >= sweep_interval_sec:
            try:
                asyncio.run(self._dydx_sweep_open_orders(reason="pre_place_brackets", market="BTC-USD"))
                self._last_dydx_bracket_sweep_ts = now_ts
            except Exception as e:
                print(f"⚠️ dYdX 清單失敗: {e}")

        print(f"\n{'='*60}")
        print(f"📊 dYdX 雙向預掛單 (TP + SL)")
        print(f"{'='*60}")

        leverage = _coerce_float(leverage, default=50.0)
        if leverage <= 0:
            leverage = 50.0

        maker_fee_pct = _coerce_float(getattr(self.config, 'maker_fee_pct', 0.0), default=0.0)
        taker_fee_pct = _coerce_float(getattr(self.config, 'taker_fee_pct', 0.0), default=0.0)
        entry_fee_pct = maker_fee_pct if self.config.use_maker_simulation else taker_fee_pct

        # 計算止盈價 (含手續費)
        target_pct = self.config.pre_take_profit_pct
        tp_total_fee_pct = entry_fee_pct * 2  # 預設 TP 也走同一費率
        tp_price_move = _coerce_float(target_pct, default=0.0) / leverage + tp_total_fee_pct
        if direction == "LONG":
            tp_price = entry_price * (1 + tp_price_move / 100)
        else:
            tp_price = entry_price * (1 - tp_price_move / 100)
        
        # 計算止損價 (含手續費，保守估計平倉為 Taker)
        stop_pct = self.config.pre_stop_loss_pct
        stop_pct = _coerce_float(stop_pct, default=0.0)
        exit_fee_pct = taker_fee_pct
        if getattr(self.config, 'taker_on_emergency_only', False):
            exit_fee_pct = entry_fee_pct if self.config.use_maker_simulation else taker_fee_pct
        sl_total_fee_pct = entry_fee_pct + exit_fee_pct
        fee_mult = _fee_leverage_multiplier(self.config, leverage)
        min_stop_pct = sl_total_fee_pct * fee_mult + 0.1
        if stop_pct < min_stop_pct:
            stop_pct = min_stop_pct
        sl_price_move = (-stop_pct / leverage) + sl_total_fee_pct  # signed
        if direction == "LONG":
            sl_price = entry_price * (1 + sl_price_move / 100)
        else:
            sl_price = entry_price * (1 - sl_price_move / 100)
        
        print(f"   方向: {direction} | 進場: ${entry_price:,.2f}")
        print(f"   止盈: ${tp_price:,.2f} (+{target_pct:.1f}%)")
        print(f"   止損: ${sl_price:,.2f} (-{stop_pct:.1f}%)")
        print(f"   數量: {dydx_size:.4f} BTC")
        
        # 🔧 v14.6.17: 檢查是否已有 TP 訂單，避免重複掛單
        existing_tp_id = self.dydx_real_position.get("tp_order_id", 0) if self.dydx_real_position else 0
        
        # 1. 掛止損單 (STOP_LOSS + reduce_only)
        try:
            print(f"\n   📉 掛 dYdX 止損單...")
            tx_hash_sl, order_id_sl = asyncio.run(
                self.dydx_api.place_stop_loss_order(
                    side=direction,
                    size=dydx_size,
                    stop_price=sl_price,
                    time_to_live_seconds=3600
                )
            )
            if tx_hash_sl and order_id_sl:
                self.pending_sl_order = {
                    'direction': direction,
                    'entry_price': entry_price,
                    'sl_price': sl_price,
                    'stop_pct': -stop_pct,  # 🔧 v14.6.28: 統一符號（負=虧損）
                    'leverage': leverage,
                    'created_time': time.time(),
                    'status': 'PENDING',
                    'dydx_order_id': order_id_sl
                }
                result['sl'] = True
                print(f"   ✅ 止損單已掛! ID: {order_id_sl}")
            else:
                print(f"   ❌ 止損單掛單失敗")
        except Exception as e:
            print(f"   ❌ 止損單異常: {e}")

        # 2. 掛止盈單 (LIMIT + reduce_only)
        if existing_tp_id and existing_tp_id > 0:
            print(f"\n   ⚠️ 已有 TP 訂單 ID: {existing_tp_id}，跳過止盈掛單")
            result['tp'] = True  # 已有訂單，視為成功
        else:
            try:
                print(f"\n   📈 掛 dYdX 止盈單...")
                tx_hash_tp, order_id_tp = asyncio.run(
                    self.dydx_api.place_take_profit_order(
                        side=direction,
                        size=dydx_size,
                        tp_price=tp_price,
                        time_to_live_seconds=3600
                    )
                )
                if tx_hash_tp and order_id_tp:
                    self.pending_tp_order = {
                        'direction': direction,
                        'entry_price': entry_price,
                        'tp_price': tp_price,
                        'target_pct': target_pct,
                        'leverage': leverage,
                        'created_time': time.time(),
                        'status': 'PENDING',
                        'dydx_order_id': order_id_tp
                    }
                    self.dydx_real_position["tp_order_id"] = order_id_tp  # 🔧 v14.6.17: 記錄以防重複
                    result['tp'] = True
                    print(f"   ✅ 止盈單已掛! ID: {order_id_tp}")
                else:
                    print(f"   ❌ 止盈單掛單失敗")
            except Exception as e:
                print(f"   ❌ 止盈單異常: {e}")
        
        print(f"\n{'='*60}")
        if result['tp'] and result['sl']:
            print(f"✅ 雙向預掛單完成! TP + SL 都已掛在 dYdX")
        elif result['tp'] or result['sl']:
            print(f"⚠️ 部分成功: TP={result['tp']}, SL={result['sl']}")
        else:
            print(f"❌ 雙向預掛單失敗")
        print(f"{'='*60}\n")
        
        return result

    def check_pre_take_profit_fill(self, current_price: float) -> Optional[float]:
        """
        🆕 v12.0 檢查止盈掛單是否成交
        
        Returns:
            成交價格 或 None
        """
        if not self.pending_tp_order or not self.active_trade:
            return None
        
        tp = self.pending_tp_order
        tp_price = tp['tp_price']
        direction = tp['direction']
        
        filled = False
        if direction == "LONG":
            # 做多: 價格漲到止盈價就成交
            if current_price >= tp_price:
                filled = True
        else:
            # 做空: 價格跌到止盈價就成交
            if current_price <= tp_price:
                filled = True
        
        if filled:
            fill_price = tp_price  # Limit Order 保證成交價
            tp['status'] = 'FILLED'
            print(f"🎯 止盈掛單成交! @ ${fill_price:,.2f}")
            
            # 🆕 v14.6: TP 成交後取消 SL
            self._cancel_pending_sl_order("TP 已成交")
            
            self.pending_tp_order = None
            return fill_price
        
        return None
    
    def check_pre_stop_loss_fill(self, current_price: float) -> Optional[float]:
        """
        🆕 v14.6 檢查止損掛單是否成交
        
        Returns:
            成交價格 或 None
        """
        if not self.pending_sl_order or not self.active_trade:
            return None
        
        sl = self.pending_sl_order
        sl_price = sl['sl_price']
        direction = sl['direction']
        
        filled = False
        if direction == "LONG":
            # 做多: 價格跌到止損價就成交
            if current_price <= sl_price:
                filled = True
        else:
            # 做空: 價格漲到止損價就成交
            if current_price >= sl_price:
                filled = True
        
        if filled:
            fill_price = sl_price  # Stop Order 觸發價
            sl['status'] = 'FILLED'
            print(f"🚨 止損掛單觸發! @ ${fill_price:,.2f}")
            
            # 🆕 v14.6: SL 成交後取消 TP
            self._cancel_pending_tp_order("SL 已觸發")
            
            self.pending_sl_order = None
            return fill_price
        
        return None
    
    def _cancel_pending_tp_order(self, reason: str = ""):
        """🆕 v14.6 取消待成交的 TP 掛單"""
        if not self.pending_tp_order:
            return
        
        tp = self.pending_tp_order
        print(f"   🔴 取消 TP 掛單 @ ${tp['tp_price']:,.2f} ({reason})")
        
        # 取消 dYdX 上的訂單 (TP 是 LONG_TERM/GTT 類型)
        if self.dydx_sync_enabled and self.dydx_api and tp.get('dydx_order_id'):
            try:
                import asyncio
                asyncio.run(self.dydx_api.cancel_order(tp['dydx_order_id'], order_type="LONG_TERM"))
                print(f"   ✅ dYdX TP 訂單已取消")
                self._journal_dydx_event(
                    "tp_cancelled",
                    order_id=tp.get('dydx_order_id'),
                    order_type="LONG_TERM",
                    reason=reason,
                    result="ok",
                )
                self._unregister_dydx_order(tp.get('dydx_order_id'), reason=reason)
            except Exception as e:
                print(f"   ⚠️ 取消 dYdX TP 失敗: {e}")
                self._journal_dydx_event(
                    "tp_cancel_failed",
                    order_id=tp.get('dydx_order_id'),
                    order_type="LONG_TERM",
                    reason=reason,
                    result="error",
                    error=str(e),
                )
        
        self.pending_tp_order = None
    
    def _cancel_pending_sl_order(self, reason: str = ""):
        """🆕 v14.6 取消待成交的 SL 掛單"""
        if not self.pending_sl_order:
            return
        
        sl = self.pending_sl_order
        print(f"   🔴 取消 SL 掛單 @ ${sl['sl_price']:,.2f} ({reason})")
        
        # 取消 dYdX 上的訂單 (SL 是 CONDITIONAL 類型)
        if self.dydx_sync_enabled and self.dydx_api and sl.get('dydx_order_id'):
            try:
                import asyncio
                asyncio.run(self.dydx_api.cancel_order(sl['dydx_order_id'], order_type="CONDITIONAL"))
                print(f"   ✅ dYdX SL 訂單已取消")
                self._journal_dydx_event(
                    "sl_cancelled",
                    order_id=sl.get('dydx_order_id'),
                    order_type="CONDITIONAL",
                    reason=reason,
                    result="ok",
                )
                self._unregister_dydx_order(sl.get('dydx_order_id'), reason=reason)
            except Exception as e:
                print(f"   ⚠️ 取消 dYdX SL 失敗: {e}")
                self._journal_dydx_event(
                    "sl_cancel_failed",
                    order_id=sl.get('dydx_order_id'),
                    order_type="CONDITIONAL",
                    reason=reason,
                    result="error",
                    error=str(e),
                )
        
        self.pending_sl_order = None
    
    def cancel_all_pending_orders(self, reason: str = "平倉"):
        """🆕 v14.6 取消所有待成交掛單"""
        self._cancel_pending_tp_order(reason)
        self._cancel_pending_sl_order(reason)

        # 🧠 v14.6.14: 取消所有已知的 dYdX 訂單（包含歷史中間數/N%N 更新造成的舊單）
        self._cancel_all_known_dydx_orders(reason)

    def _journal_dydx_event(self, event: str, **fields):
        """🧠 v14.6.14: JSONL 記憶日誌（用於追查掛單/取消順序）"""
        try:
            payload = {
                "ts": datetime.now().isoformat(),
                "run_id": getattr(self, "_dydx_run_id", None),
                "cwd": os.getcwd(),
                "event": event,
                "trade_id": getattr(getattr(self, 'active_trade', None), 'trade_id', None),
                "dydx_has_position": bool(self.dydx_real_position),
                "dydx_sync_enabled": bool(getattr(self, "dydx_sync_enabled", False)),
                **fields,
            }
            self._dydx_order_journal_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._dydx_order_journal_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:
            # 只提醒一次，避免大量刷屏
            if not getattr(self, "_dydx_journal_failed_once", False):
                self._dydx_journal_failed_once = True
                try:
                    print(f"⚠️ dYdX journal 寫入失敗: {e} | path={self._dydx_order_journal_path}")
                except Exception:
                    pass

    def _register_dydx_order(self, order_id: int, order_type: str, kind: str, market: str = "BTC-USD"):
        try:
            oid = int(order_id)
            if oid <= 0:
                return
            self._dydx_order_registry[oid] = {
                "order_type": order_type,
                "kind": kind,
                "market": market,
                "created_ts": time.time(),
            }
            self._journal_dydx_event(
                "order_registered",
                order_id=oid,
                order_type=order_type,
                kind=kind,
                market=market,
            )
        except Exception:
            pass

    def _unregister_dydx_order(self, order_id: int, reason: str = ""):
        try:
            oid = int(order_id)
            if oid in self._dydx_order_registry:
                meta = self._dydx_order_registry.pop(oid)
                self._journal_dydx_event(
                    "order_unregistered",
                    order_id=oid,
                    reason=reason,
                    **meta,
                )
        except Exception:
            pass

    def _cancel_all_known_dydx_orders(self, reason: str = ""):
        """嘗試取消 registry 內所有尚未清掉的 dYdX 訂單。
        
        🔧 v14.6.32: 如果 registry 取消失敗，fallback 到 sweep 模式（從 API 取得正確 GTBT）
        """
        if not (self.dydx_sync_enabled and self.dydx_api):
            return
        if not self._dydx_order_registry:
            return

        # 避免迭代時修改 dict
        order_items = list(self._dydx_order_registry.items())
        failed_count = 0
        for order_id, meta in order_items:
            order_type = meta.get("order_type")
            try:
                import asyncio
                asyncio.run(self.dydx_api.cancel_order(order_id, order_type=order_type))
                print(f"   ✅ dYdX 訂單已取消: {order_id} ({order_type})")
                self._journal_dydx_event(
                    "order_cancelled",
                    order_id=order_id,
                    order_type=order_type,
                    cancel_reason=reason,
                    result="ok",
                )
                self._unregister_dydx_order(order_id, reason=reason)
            except Exception as e:
                failed_count += 1
                self._journal_dydx_event(
                    "order_cancel_failed",
                    order_id=order_id,
                    order_type=order_type,
                    cancel_reason=reason,
                    result="error",
                    error=str(e),
                )
        
        # 🔧 v14.6.32: 如果有失敗的，用 sweep 模式補救（它會從 API 取得正確的 GTBT）
        if failed_count > 0:
            try:
                import asyncio
                asyncio.run(self._dydx_sweep_open_orders(reason=f"fallback_sweep:{reason}", market="BTC-USD"))
            except Exception:
                pass

    async def _dydx_sweep_open_orders(self, reason: str, market: str = "BTC-USD") -> int:
        """在開新一輪/平倉/止損更新前，統一清空 dYdX 未平倉掛單。

        目的：避免 TP/SL/中間數更新造成掛單累積，導致誤判或觸發 equity tier 的 order count limit。
        """
        if not (self.dydx_sync_enabled and self.dydx_api):
            return 0

        cancelled = 0
        had_local_tracking = bool(self.pending_tp_order or self.pending_sl_order or self._dydx_order_registry)
        sweep_ok = False
        try:
            cancelled = await self.dydx_api.cancel_open_orders(symbol=market, status=["OPEN", "UNTRIGGERED"])
            # 條件單有時需要 CONDITIONAL flag 取消才會真的消失，額外補一槍
            try:
                cancelled += await self.dydx_api.cancel_all_conditional_orders()
            except Exception:
                pass
            sweep_ok = True
            self._journal_dydx_event(
                "orders_swept",
                market=market,
                cancelled_count=cancelled,
                reason=reason,
                result="ok",
            )
        except Exception as e:
            self._journal_dydx_event(
                "orders_sweep_failed",
                market=market,
                reason=reason,
                result="error",
                error=str(e),
            )

        # 🔧 v14.6.31: 只有在 sweep 明確成功、或本來就沒有本地追蹤時，才清掉本地狀態。
        # 否則（例如取消 tx 被 rate limit / order count limit 擋下），清掉本地會導致下一輪誤以為沒單 → 繼續新掛單 → 疊到 10 筆上限。
        if sweep_ok and (cancelled > 0 or not had_local_tracking):
            try:
                self.pending_tp_order = None
                self.pending_sl_order = None
            except Exception:
                pass
            try:
                self._dydx_order_registry.clear()
            except Exception:
                pass

        return cancelled

    async def _dydx_sweep_on_limit_error(self, err: dict, reason: str, market: str = "BTC-USD") -> bool:
        """遇到 order count / rate limit 時，清掃殘留訂單後再重試。"""
        try:
            code = int(err.get("code") or 0)
        except Exception:
            code = 0
        if code not in (10001, 5001):
            return False

        now_ts = time.time()
        last_ts = getattr(self, "_last_dydx_limit_sweep_ts", 0.0)
        if now_ts - last_ts < 8.0:
            return False
        self._last_dydx_limit_sweep_ts = now_ts

        try:
            await self._dydx_sweep_open_orders(reason=reason, market=market)
            self._journal_dydx_event(
                "limit_sweep",
                reason=reason,
                code=code,
                error=err,
            )
            return True
        except Exception:
            return False

    async def _dydx_cancel_conditional_orders(self, reason: str) -> tuple[int, int]:
        """只取消條件單（STOP/TP 類），用於止損更新/補掛時避免誤取消剛掛好的 TP 限價單。

        Returns:
            (found_count, cancelled_count)
        """
        if not (self.dydx_sync_enabled and self.dydx_api):
            return 0, 0

        found_count = 0
        cancelled_count = 0
        try:
            found_count, cancelled_count = await self.dydx_api.cancel_all_conditional_orders(return_details=True)
            result = "ok" if (found_count == 0 or cancelled_count == found_count) else "partial"
            self._journal_dydx_event(
                "conditional_orders_cancelled",
                found_count=found_count,
                cancelled_count=cancelled_count,
                reason=reason,
                result=result,
            )
        except Exception as e:
            self._journal_dydx_event(
                "conditional_orders_cancel_failed",
                reason=reason,
                result="error",
                error=str(e),
            )
            return 0, 0

        # 只有在「沒有條件單」或「已全部取消」時，才清掉本地追蹤。
        # 否則（partial），保留本地狀態避免下一輪誤以為沒單 → 繼續新掛單 → 疊到 order count limit。
        if found_count == 0 or cancelled_count == found_count:
            try:
                self.pending_sl_order = None
            except Exception:
                pass
            try:
                for oid, meta in list(self._dydx_order_registry.items()):
                    if meta.get('order_type') == 'CONDITIONAL':
                        self._dydx_order_registry.pop(oid, None)
            except Exception:
                pass

        return found_count, cancelled_count

    async def _dydx_cancel_open_tp_orders(self, reason: str, symbol: str = "BTC-USD") -> int:
        """取消所有非條件單（TP/GTT），避免止盈重複掛單累積。"""
        if not (self.dydx_sync_enabled and self.dydx_api):
            return 0

        try:
            orders = await self.dydx_api.get_open_orders(status=["OPEN", "UNTRIGGERED"], symbol=symbol)
        except Exception:
            return 0

        cancelled = 0
        for order in orders or []:
            if self._is_dydx_conditional_order(order):
                continue
            try:
                client_id = int(order.get("clientId") or order.get("client_id") or 0)
            except Exception:
                client_id = 0
            if client_id <= 0:
                continue

            gtbt = 0
            gtbt_str = order.get("goodTilBlockTime") or order.get("good_til_block_time")
            if gtbt_str:
                try:
                    dt = datetime.fromisoformat(str(gtbt_str).replace("Z", "+00:00"))
                    gtbt = int(dt.timestamp())
                except Exception:
                    gtbt = 0

            try:
                success = await self.dydx_api.cancel_order(
                    client_id,
                    order_type="LONG_TERM",
                    good_til_block_time=gtbt,
                )
            except Exception:
                success = False

            if success:
                cancelled += 1
                self._journal_dydx_event(
                    "tp_cancelled",
                    order_id=client_id,
                    order_type="LONG_TERM",
                    reason=reason,
                    result="ok",
                )
                self._unregister_dydx_order(client_id, reason=reason)
            else:
                self._journal_dydx_event(
                    "tp_cancel_failed",
                    order_id=client_id,
                    order_type="LONG_TERM",
                    reason=reason,
                    result="error",
                )

        if cancelled > 0:
            try:
                self.pending_tp_order = None
            except Exception:
                pass
            try:
                if self.dydx_real_position:
                    self.dydx_real_position["tp_order_id"] = 0
            except Exception:
                pass

        return cancelled

    def _is_dydx_conditional_order(self, order: dict) -> bool:
        status = str(order.get("status", "")).upper()
        if status == "UNTRIGGERED":
            return True
        if order.get("conditionType") or order.get("condition_type"):
            return True
        if order.get("triggerPrice") or order.get("trigger_price") or order.get("conditionalOrderTriggerSubticks"):
            return True
        return False

    def _extract_dydx_conditional_trigger_price(self, order: dict) -> float:
        for key in ("triggerPrice", "trigger_price", "stopPrice", "stop_price"):
            try:
                val = float(order.get(key, 0) or 0)
            except Exception:
                continue
            if val > 0:
                return val
        return 0.0

    async def _get_open_conditional_orders(self, symbol: str = "BTC-USD") -> list[dict]:
        if not self.dydx_sync_enabled or not self.dydx_api:
            return []
        try:
            orders = await self.dydx_api.get_open_orders(symbol=symbol)
        except Exception:
            return []
        return [o for o in orders if self._is_dydx_conditional_order(o)]

    def _get_open_conditional_orders_sync(self, symbol: str = "BTC-USD") -> list[dict]:
        import asyncio

        if not self.dydx_sync_enabled or not self.dydx_api:
            return []
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.run(self._get_open_conditional_orders(symbol))
            except Exception:
                return []
        return []

    def _summarize_dydx_orders(self, orders: list[dict]) -> list[dict]:
        summaries: list[dict] = []
        for order in orders or []:
            try:
                trigger_price = self._extract_dydx_conditional_trigger_price(order)
            except Exception:
                trigger_price = 0.0
            summaries.append({
                "order_id": order.get("id") or order.get("orderId") or order.get("order_id"),
                "client_id": order.get("clientId") or order.get("client_id"),
                "status": order.get("status"),
                "side": order.get("side"),
                "size": order.get("size"),
                "price": order.get("price"),
                "trigger_price": trigger_price or None,
                "order_type": order.get("type") or order.get("orderType") or order.get("order_type"),
                "reduce_only": order.get("reduceOnly") if "reduceOnly" in order else order.get("reduce_only"),
                "created_at": order.get("createdAt") or order.get("created_at"),
                "is_conditional": self._is_dydx_conditional_order(order),
            })
        return summaries

    async def _get_dydx_open_orders_snapshot(self, symbol: str = "BTC-USD") -> dict:
        if not self.dydx_sync_enabled or not self.dydx_api:
            return {
                "orders": [],
                "orders_count": 0,
                "sl_count": 0,
                "tp_count": 0,
            }
        try:
            orders = await self.dydx_api.get_open_orders(status=["OPEN", "UNTRIGGERED"], symbol=symbol)
        except Exception:
            orders = []
        sl_orders = [o for o in orders if self._is_dydx_conditional_order(o)]
        tp_orders = [o for o in orders if not self._is_dydx_conditional_order(o)]
        return {
            "orders": self._summarize_dydx_orders(orders),
            "orders_count": len(orders),
            "sl_count": len(sl_orders),
            "tp_count": len(tp_orders),
        }

    async def _log_dydx_protection_snapshot(self, reason: str, symbol: str = "BTC-USD"):
        if not self.dydx_sync_enabled or not self.dydx_api:
            return
        snapshot = await self._get_dydx_open_orders_snapshot(symbol=symbol)
        position_payload = None
        if self.dydx_real_position:
            position_payload = dict(self.dydx_real_position)
            entry_time = position_payload.get("entry_time")
            if isinstance(entry_time, datetime):
                position_payload["entry_time"] = entry_time.isoformat()
        self._journal_dydx_event(
            "protection_snapshot",
            reason=reason,
            orders_count=snapshot.get("orders_count", 0),
            sl_count=snapshot.get("sl_count", 0),
            tp_count=snapshot.get("tp_count", 0),
            open_orders=snapshot.get("orders", []),
            pending_sl=dict(self.pending_sl_order) if self.pending_sl_order else None,
            pending_tp=dict(self.pending_tp_order) if self.pending_tp_order else None,
            dydx_position=position_payload,
        )

    async def _ensure_dydx_protection_orders(self, reason: str = "periodic_check", symbol: str = "BTC-USD") -> None:
        if not self.dydx_sync_enabled or not self.dydx_api:
            return
        if not self.active_trade or not self.dydx_real_position:
            return

        live_pos = None
        try:
            if hasattr(self.dydx_api, "get_positions_fresh"):
                positions = await self.dydx_api.get_positions_fresh()
            else:
                positions = await self.dydx_api.get_positions()
        except Exception:
            positions = []
        for pos in positions or []:
            if pos.get("market") != symbol:
                continue
            raw_size = _coerce_float(pos.get("size", 0.0), default=0.0)
            if abs(raw_size) <= 0.0001:
                continue
            side = "LONG" if raw_size > 0 else "SHORT"
            entry_price = _coerce_float(pos.get("entryPrice", 0.0), default=0.0)
            live_pos = {
                "side": side,
                "size": abs(raw_size),
                "entry_price": entry_price,
                "entry_time": datetime.now(),
            }
            break
        if not live_pos:
            return
        self.dydx_real_position = live_pos

        now_ts = time.time()
        cooldown_sec = _coerce_float(getattr(self.config, "dydx_protection_check_cooldown_sec", 6.0), default=6.0)
        if now_ts - getattr(self, "_last_dydx_protection_check_ts", 0.0) < cooldown_sec:
            return
        self._last_dydx_protection_check_ts = now_ts

        snapshot = await self._get_dydx_open_orders_snapshot(symbol=symbol)
        sl_count = snapshot.get("sl_count", 0)
        tp_count = snapshot.get("tp_count", 0)
        has_sl = sl_count > 0
        has_tp = tp_count > 0

        if tp_count > 1:
            await self._dydx_cancel_open_tp_orders(reason="tp_dedupe", symbol=symbol)
            snapshot = await self._get_dydx_open_orders_snapshot(symbol=symbol)
            tp_count = snapshot.get("tp_count", 0)
            has_tp = tp_count > 0

        if has_sl and has_tp:
            return

        self._journal_dydx_event(
            "protection_missing",
            reason=reason,
            has_sl=has_sl,
            has_tp=has_tp,
            orders_count=snapshot.get("orders_count", 0),
            open_orders=snapshot.get("orders", []),
        )

        trade = self.active_trade
        direction = str(getattr(trade, "direction", "")).upper()
        if direction not in ("LONG", "SHORT"):
            return

        entry_price = _coerce_float(getattr(trade, "entry_price", 0.0), default=0.0)
        if entry_price <= 0:
            entry_price = _coerce_float(self.dydx_real_position.get("entry_price", 0.0), default=0.0)
        if entry_price <= 0:
            return

        leverage = getattr(trade, "actual_leverage", None) or getattr(trade, "leverage", None)
        leverage = _coerce_float(leverage, default=_coerce_float(getattr(self.config, "leverage", 50), default=50.0))
        if leverage <= 0:
            leverage = 50.0

        if not has_sl:
            stop_pct, stage_name = self.get_progressive_stop_loss(trade.max_profit_pct or 0.0)
            ok = await self.update_dydx_stop_loss_async(stop_pct)
            self._journal_dydx_event(
                "sl_missing_repair",
                reason=reason,
                stop_pct=stop_pct,
                stage=stage_name,
                result="ok" if ok else "error",
            )

        if not has_tp:
            target_pct = _coerce_float(getattr(trade, "actual_target_pct", 0.0), default=0.0)
            if target_pct <= 0:
                target_pct = _coerce_float(getattr(self.config, "pre_take_profit_pct", 0.0), default=0.0)
            if target_pct <= 0:
                target_pct = _coerce_float(getattr(self.config, "target_profit_pct", 0.0), default=0.0)
            if target_pct <= 0:
                return

            entry_fee_pct = _coerce_float(
                (self.config.maker_fee_pct if self.config.use_maker_simulation else self.config.taker_fee_pct),
                default=0.0,
            )
            tp_total_fee_pct = entry_fee_pct * 2
            tp_price_move_pct = target_pct / leverage + tp_total_fee_pct

            if direction == "LONG":
                tp_price = entry_price * (1 + tp_price_move_pct / 100)
            else:
                tp_price = entry_price * (1 - tp_price_move_pct / 100)

            try:
                dydx_size = abs(_coerce_float(self.dydx_real_position.get("size", 0.0), default=0.0))
                tx_hash, order_id = await self.dydx_api.place_take_profit_order(
                    side=direction,
                    size=dydx_size,
                    tp_price=tp_price,
                    time_to_live_seconds=3600,
                )
            except Exception as e:
                tx_hash, order_id = None, 0
                self._journal_dydx_event(
                    "tp_missing_repair_failed",
                    reason=reason,
                    error=str(e),
                    tp_price=tp_price,
                    target_pct=target_pct,
                )

            if not (tx_hash and order_id):
                try:
                    err = self.dydx_api.get_last_tx_error() if self.dydx_api else {}
                except Exception:
                    err = {}
                swept = await self._dydx_sweep_on_limit_error(err, reason="tp_repair_limit")
                if swept:
                    try:
                        dydx_size = abs(_coerce_float(self.dydx_real_position.get("size", 0.0), default=0.0))
                        tx_hash, order_id = await self.dydx_api.place_take_profit_order(
                            side=direction,
                            size=dydx_size,
                            tp_price=tp_price,
                            time_to_live_seconds=3600,
                        )
                    except Exception:
                        tx_hash, order_id = None, 0

            if tx_hash and order_id:
                self.pending_tp_order = {
                    "direction": direction,
                    "entry_price": entry_price,
                    "tp_price": tp_price,
                    "target_pct": target_pct,
                    "leverage": leverage,
                    "created_time": time.time(),
                    "status": "PENDING",
                    "dydx_order_id": order_id,
                }
                try:
                    if self.dydx_real_position is not None:
                        self.dydx_real_position["tp_order_id"] = order_id
                except Exception:
                    pass
                self._register_dydx_order(order_id, order_type="LONG_TERM", kind="TP_REPAIR")
                self._journal_dydx_event(
                    "tp_missing_repair",
                    reason=reason,
                    order_id=order_id,
                    tp_price=tp_price,
                    target_pct=target_pct,
                    result="ok",
                )
            else:
                self._journal_dydx_event(
                    "tp_missing_repair_failed",
                    reason=reason,
                    tp_price=tp_price,
                    target_pct=target_pct,
                    result="error",
                )

        await self._log_dydx_protection_snapshot(reason=f"{reason}:after_repair", symbol=symbol)
    
    async def update_dydx_stop_loss_async(self, new_stop_pct: float) -> bool:
        """
        🆕 v14.6 動態更新 dYdX 止損單 (用於中間位鎖利)
        
        當最大獲利更新時，調整止損單的觸發價格
        流程: 取消舊 SL → 掛新 SL
        
        Args:
            new_stop_pct: 新的止損% (槓桿後)
        
        Returns:
            是否成功
        """
        if not self.dydx_sync_enabled or not self.dydx_api:
            return False
        
        now_ts = time.time()
        guard_sec = _coerce_float(getattr(self.config, "sl_update_guard_sec", 1.0), default=1.0)
        last_exec = getattr(self, "_last_sl_update_exec_ts", 0.0)
        if (now_ts - last_exec) < guard_sec:
            return False
        self._last_sl_update_exec_ts = now_ts
        self._last_sl_update_exec_stop_pct = float(new_stop_pct)

        refreshed_pos = None
        try:
            positions = await self._get_dydx_positions_with_cache()
            for pos in positions:
                if pos.get('market') == 'BTC-USD' and abs(float(pos.get('size', 0))) > 0.0001:
                    raw_size = float(pos.get('size', 0))
                    refreshed_pos = {
                        "side": "LONG" if raw_size > 0 else "SHORT",
                        "size": abs(raw_size),
                        "entry_price": _coerce_float(pos.get("entryPrice", 0.0), default=0.0),
                    }
                    break
            if refreshed_pos:
                self.dydx_real_position = {
                    "side": refreshed_pos["side"],
                    "size": refreshed_pos["size"],
                    "entry_price": refreshed_pos["entry_price"],
                    "entry_time": datetime.now(),
                }
        except Exception:
            refreshed_pos = None

        if not self.dydx_real_position:
            return False

        # backoff 期間不要嘗試掛/取消（避免 block rate limit）
        now_ts = time.time()
        if now_ts < getattr(self, "_dydx_tx_backoff_until", 0.0):
            return False

        trade = self.active_trade
        trade_direction = None
        trade_entry_price = None
        max_pnl_pct = 0.0
        if trade:
            trade_direction = trade.direction
            trade_entry_price = trade.entry_price
            max_pnl_pct = trade.max_profit_pct if trade.max_profit_pct else 0.0
        else:
            pos = self.dydx_real_position or {}
            trade_direction = pos.get("side") or pos.get("direction")
            trade_entry_price = _coerce_float(pos.get("entry_price", 0.0), default=0.0)
            if not trade_direction or trade_entry_price <= 0:
                return False
            side_norm = str(trade_direction).upper()
            if side_norm in ("BUY", "LONG"):
                trade_direction = "LONG"
            elif side_norm in ("SELL", "SHORT"):
                trade_direction = "SHORT"
            else:
                return False
            max_pnl_pct = _coerce_float(getattr(self, "_dydx_max_pnl", 0.0), default=0.0)

        if refreshed_pos:
            refreshed_side = refreshed_pos.get("side")
            refreshed_entry = _coerce_float(refreshed_pos.get("entry_price", 0.0), default=0.0)
            if refreshed_side and trade_direction and refreshed_side != trade_direction:
                print(f"   ⚠️ dYdX 倉位方向不一致，使用實際方向 {refreshed_side}")
                trade_direction = refreshed_side
            use_ref_entry_price = bool(getattr(self.config, "dydx_use_reference_entry_price", False))
            if refreshed_entry > 0 and not use_ref_entry_price:
                trade_entry_price = refreshed_entry

        leverage = None
        if trade:
            leverage = getattr(trade, "actual_leverage", None) or getattr(trade, "leverage", None)
        if leverage is None:
            leverage = self.config.leverage
        leverage = _coerce_float(leverage, default=50.0)
        if leverage <= 0:
            leverage = 50.0
        
        # 🔧 v14.9.9 止損價計算（徹底修復止損方向問題）
        # 
        # new_stop_pct 的含義：
        # - 正數：鎖利（止損時仍有盈利）
        # - 負數：虧損止損
        # 
        # 關鍵理解：
        # - LONG 持倉：價格上漲獲利，下跌虧損
        #   - 鎖利止損：設在進場價上方（價格跌回來觸發）
        #   - dYdX 觸發：Oracle <= stop_price
        # 
        # - SHORT 持倉：價格下跌獲利，上漲虧損
        #   - 鎖利止損：設在進場價下方（價格漲回來觸發）
        #   - dYdX 觸發：Oracle >= stop_price
        #
        # 計算方式：
        # - 鎖利 X%（槓桿後 ROE）→ 價格變動 = X% / leverage
        # - LONG 鎖利：止損價 = 進場價 × (1 + 價格變動)  // 進場價上方
        # - SHORT 鎖利：止損價 = 進場價 × (1 - 價格變動) // 進場價下方
        #
        # 將「槓桿後 ROE%」換算成「價格變動%」，並把 round-trip 手續費加回去
        # net_ROE = (price_move_pct - total_fee_pct) * leverage
        maker_fee_pct = _coerce_float(getattr(self.config, 'maker_fee_pct', 0.0), default=0.0)
        taker_fee_pct = _coerce_float(getattr(self.config, 'taker_fee_pct', 0.0), default=0.0)
        entry_fee_pct = maker_fee_pct if self.config.use_maker_simulation else taker_fee_pct
        exit_fee_pct = taker_fee_pct
        if getattr(self.config, 'taker_on_emergency_only', False):
            exit_fee_pct = entry_fee_pct if self.config.use_maker_simulation else taker_fee_pct
        total_fee_pct = entry_fee_pct + exit_fee_pct
        
        # 🔧 v14.9.6: 最小止損 ROE% 保護
        # 當止損線 < 手續費 ROE 時，止損價會在「止盈方向」，這是錯誤的！
        # 例: stop=-0.75%, fee_ROE=3.75% → price_move=0.06%（漲）→ LONG止損價高於進場（錯）
        # 修復: 確保 |stop| > fee_ROE，讓價格變動方向正確
        fee_mult = _fee_leverage_multiplier(self.config, leverage)
        fee_roe_pct = total_fee_pct * fee_mult  # 手續費轉 ROE%
        # 只對虧損止損（負值）做保護，鎖利（正值）不需要
        if new_stop_pct < 0:
            # 虧損止損：取絕對值後比較
            min_loss_roe = fee_roe_pct + 0.5  # 至少 > 手續費 + 0.5% 緩衝
            if abs(new_stop_pct) < min_loss_roe:
                # 原始止損太小，調整為最小值（但保持負號）
                adjusted_stop = -min_loss_roe
                print(f"   ⚠️ [v14.9.6] 止損 {new_stop_pct:.2f}% 太小 (< 手續費 {fee_roe_pct:.2f}%)，"
                      f"調整為 {adjusted_stop:.2f}%")
                new_stop_pct = adjusted_stop
        
        # 🔧 v14.9.9: 修正價格計算方向
        # price_move_pct = 鎖利的價格變動百分比（正數）
        price_move_pct = (new_stop_pct / leverage) + total_fee_pct
        
        if trade_direction == "LONG":
            # LONG 持倉：
            # - 鎖利 (new_stop_pct > 0): 止損價在進場價上方
            # - 虧損 (new_stop_pct < 0): 止損價在進場價下方
            # 公式相同：進場價 × (1 + price_move_pct)
            # 正 price_move → 高於進場價 → 鎖利
            # 負 price_move → 低於進場價 → 虧損止損
            new_sl_price = trade_entry_price * (1 + price_move_pct / 100)
        else:
            # SHORT 持倉：
            # - 鎖利 (new_stop_pct > 0): 止損價在進場價下方（但高於當前價）
            # - 虧損 (new_stop_pct < 0): 止損價在進場價上方
            # 🔧 v14.9.9: SHORT 的止損方向與 LONG 相反！
            # 正 price_move → 低於進場價 → 鎖利（價格漲回來時觸發）
            # 負 price_move → 高於進場價 → 虧損止損（價格繼續漲時觸發）
            new_sl_price = trade_entry_price * (1 - price_move_pct / 100)
        
        # 🔧 v14.6.41 增加日誌：顯示 SL 價格與進場價的關係
        if new_stop_pct > 0:
            price_diff_pct = abs(new_sl_price - trade_entry_price) / trade_entry_price * 100
            print(f"   📊 [v14.6.41] 鎖利 SL: ${new_sl_price:,.2f} | 進場: ${trade_entry_price:,.2f} | "
                  f"價差: {price_diff_pct:.4f}% | 最高獲利: {max_pnl_pct:.2f}% | 鎖住: {new_stop_pct:.2f}%")
        
        # 檢查是否需要更新 (新止損價比舊的更有利)
        if self.pending_sl_order:
            old_sl_price = self.pending_sl_order.get('sl_price', 0)
            if trade_direction == "LONG":
                # LONG: 新止損價應該更高 (鎖住更多利潤)
                if new_sl_price <= old_sl_price:
                    return False  # 不需更新
            else:
                # SHORT: 新止損價應該更低 (鎖住更多利潤)
                if new_sl_price >= old_sl_price:
                    return False  # 不需更新

        # 🛡️ 若本地沒有 pending_sl_order（常見於重啟後狀態遺失），
        # 先清空交易所上的條件單，避免重複掛多張 SL。
        if not self.pending_sl_order:
            try:
                await self._dydx_cancel_conditional_orders(reason="sl_update_no_local_tracking")
            except Exception:
                pass
        
        print(f"🔄 更新 dYdX 止損: ${new_sl_price:,.2f} ({new_stop_pct:+.2f}%)")

        # 🔧 v14.6.31: 更新鎖利止損只清「條件單」，保留 TP 限價單
        # 避免每次中間數/N%鎖N% 更新都把 TP 清掉，導致交易所只剩最初止損單的錯覺。
        found_count, cancelled_count = 0, 0
        try:
            found_count, cancelled_count = await self._dydx_cancel_conditional_orders(reason="sl_update_replace")
        except Exception as e:
            print(f"   ⚠️ 取消條件單失敗: {e}")

        # 若找到條件單但未能全部取消，先 backoff，避免疊單觸發 order count limit
        if found_count > 0 and cancelled_count < found_count:
            self._dydx_tx_backoff_until = time.time() + 12.0
            try:
                err = self.dydx_api.get_last_tx_error() if self.dydx_api else {}
            except Exception:
                err = {}
            if err:
                print(f"   ⚠️ 條件單可能未清乾淨，暫停掛新止損 (backoff 12s) | last_tx_error={err}")
            else:
                print(f"   ⚠️ 條件單可能未清乾淨，暫停掛新止損 (backoff 12s)")
            return False

        # pending_sl_order 會在 _dydx_cancel_conditional_orders() 成功時清掉
        self.pending_sl_order = None

        # 2) 掛新 SL
        try:
            dydx_size = abs(_coerce_float(self.dydx_real_position.get('size', 0), default=0.0))
            tx_hash, order_id = await self.dydx_api.place_stop_loss_order(
                side=trade_direction,
                size=dydx_size,
                stop_price=new_sl_price,
                time_to_live_seconds=3600,
            )

            if not (tx_hash and order_id):
                # 交易所拒單時不要狂刷重試，避免 block rate limit + 疊單
                try:
                    err = self.dydx_api.get_last_tx_error() if self.dydx_api else {}
                except Exception:
                    err = {}
                code = err.get('code')
                codespace = err.get('codespace')

                # 常見：order count limit / block rate limit
                if code in (10001, 5001):
                    swept = await self._dydx_sweep_on_limit_error(err, reason="sl_update_limit")
                    if swept:
                        tx_hash, order_id = await self.dydx_api.place_stop_loss_order(
                            side=trade_direction,
                            size=dydx_size,
                            stop_price=new_sl_price,
                            time_to_live_seconds=3600,
                        )
                        if tx_hash and order_id:
                            self.pending_sl_order = {
                                'direction': trade_direction,
                                'entry_price': trade_entry_price,
                                'sl_price': new_sl_price,
                                'stop_pct': new_stop_pct,
                                'leverage': leverage,
                                'created_time': time.time(),
                                'status': 'PENDING',
                                'dydx_order_id': order_id
                            }
                            self._register_dydx_order(order_id, order_type="CONDITIONAL", kind="SL_UPDATE")
                            self._journal_dydx_event(
                                "sl_updated",
                                order_id=order_id,
                                order_type="CONDITIONAL",
                                sl_price=new_sl_price,
                                stop_pct=new_stop_pct,
                                leverage=leverage,
                                result="ok",
                            )
                            try:
                                await self._log_dydx_protection_snapshot(reason="sl_updated")
                            except Exception:
                                pass
                            print(f"   ✅ 新止損單已掛! ID: {order_id}")
                            return True

                    backoff = 15.0 if code == 5001 else 12.0
                    self._dydx_tx_backoff_until = time.time() + backoff
                    print(f"   ⚠️ dYdX 拒單，進入 backoff {backoff:.0f}s | codespace={codespace} code={code}")
                    try:
                        await self._dydx_cancel_conditional_orders(reason="sl_place_failed_backoff_cleanup")
                    except Exception:
                        pass
                    return False

                # 其他未知失敗：短暫 backoff，交給下一輪再嘗試
                self._dydx_tx_backoff_until = time.time() + 8.0
                return False

            if tx_hash and order_id:
                self.pending_sl_order = {
                    'direction': trade_direction,
                    'entry_price': trade_entry_price,
                    'sl_price': new_sl_price,
                    'stop_pct': new_stop_pct,
                    'leverage': leverage,
                    'created_time': time.time(),
                    'status': 'PENDING',
                    'dydx_order_id': order_id
                }
                # 🧠 註冊 dYdX 訂單（動態止損更新）
                self._register_dydx_order(order_id, order_type="CONDITIONAL", kind="SL_UPDATE")
                self._journal_dydx_event(
                    "sl_updated",
                    order_id=order_id,
                    order_type="CONDITIONAL",
                    sl_price=new_sl_price,
                    stop_pct=new_stop_pct,
                    leverage=leverage,
                    result="ok",
                )
                try:
                    await self._log_dydx_protection_snapshot(reason="sl_updated")
                except Exception:
                    pass
                print(f"   ✅ 新止損單已掛! ID: {order_id}")
                return True

            print(f"   ❌ 新止損單失敗")
            return False
        except Exception as e:
            print(f"   ❌ 更新止損異常: {e}")
            self._journal_dydx_event(
                "sl_update_failed",
                sl_price=new_sl_price,
                stop_pct=new_stop_pct,
                leverage=leverage,
                error=str(e),
            )
            return False

    def update_dydx_stop_loss(self, new_stop_pct: float) -> bool:
        """
        同步包裝器：在同步流程中安全呼叫 `update_dydx_stop_loss_async`。

        ⚠️ 若已在 async event loop 內，請改用 `await update_dydx_stop_loss_async(...)`，
        避免 `asyncio.run()` nested event loop 導致止損單未掛出。
        """
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.update_dydx_stop_loss_async(new_stop_pct))

        print("⚠️ update_dydx_stop_loss() 在 async context 內被呼叫，請改用 await update_dydx_stop_loss_async()")
        return False

    async def update_dydx_take_profit_async(self, new_tp_price: float, new_target_pct: Optional[float] = None) -> bool:
        """
        🆕 v14.6.18 動態更新 dYdX 止盈單 (由外部策略決定更新時機)
        
        當動態止盈策略需要調整 TP 價格時調用
        流程: 取消舊 TP → 掛新 TP
        
        注意: 由於 dYdX v4 的 reduce_only 限制，TP 訂單無法設為 reduce_only
        因此必須確保每次只有一張 TP 單，避免重複成交開反向倉
        
        Args:
            new_tp_price: 新的止盈價格
            new_target_pct: 新的止盈目標(槓桿後 ROE%)，可選

        Returns:
            是否成功
        """
        if not self.dydx_sync_enabled or not self.dydx_api or not self.dydx_real_position:
            return False
        
        if not self.active_trade:
            return False
        
        trade = self.active_trade
        if new_tp_price <= 0:
            return False
        
        print(f"🔄 更新 dYdX 止盈: ${new_tp_price:,.2f}")

        # 1) 取消舊 TP（避免留下多張訂單造成開反向倉）
        cancelled_tp = 0
        try:
            cancelled_tp = await self._dydx_cancel_open_tp_orders(reason="tp_update_replace")
        except Exception:
            cancelled_tp = 0

        if cancelled_tp == 0:
            old_order_id = None
            if self.pending_tp_order:
                old_order_id = self.pending_tp_order.get('dydx_order_id')
            if old_order_id:
                try:
                    cancelled = await self.dydx_api.cancel_order(int(old_order_id), order_type="LONG_TERM")
                    if cancelled:
                        self._unregister_dydx_order(int(old_order_id), reason="tp_update_replace")
                        print(f"   ✅ 舊 TP 訂單已取消")
                except Exception as e:
                    print(f"   ⚠️ 取消舊止盈單失敗: {e}")
        
        # 清除 dydx_real_position 中的 tp_order_id
        if self.dydx_real_position:
            self.dydx_real_position["tp_order_id"] = 0
        self.pending_tp_order = None

        # 2) 掛新 TP
        target_pct = _coerce_float(new_target_pct, default=0.0)
        if target_pct <= 0 and self.pending_tp_order:
            target_pct = _coerce_float(self.pending_tp_order.get('target_pct', 0.0), default=0.0)

        try:
            dydx_size = self.dydx_real_position.get('size', 0)
            tx_hash, order_id = await self.dydx_api.place_take_profit_order(
                side=trade.direction,
                size=dydx_size,
                tp_price=new_tp_price,
                time_to_live_seconds=3600,
            )

            if tx_hash and order_id:
                self.pending_tp_order = {
                    'direction': trade.direction,
                    'entry_price': trade.entry_price,
                    'tp_price': new_tp_price,
                    'target_pct': target_pct,
                    'leverage': trade.actual_leverage or self.config.leverage,
                    'created_time': time.time(),
                    'status': 'PENDING',
                    'dydx_order_id': order_id
                }
                if target_pct > 0:
                    trade.actual_target_pct = target_pct
                self.dydx_real_position["tp_order_id"] = order_id
                # 🧠 註冊 dYdX 訂單（動態止盈更新）
                self._register_dydx_order(order_id, order_type="LONG_TERM", kind="TP_UPDATE")
                self._journal_dydx_event(
                    "tp_updated",
                    order_id=order_id,
                    order_type="LONG_TERM",
                    tp_price=new_tp_price,
                    result="ok",
                )
                try:
                    await self._log_dydx_protection_snapshot(reason="tp_updated")
                except Exception:
                    pass
                print(f"   ✅ 新止盈單已掛! ID: {order_id}")
                return True

            print(f"   ❌ 新止盈單失敗")
            try:
                err = self.dydx_api.get_last_tx_error() if self.dydx_api else {}
            except Exception:
                err = {}
            swept = await self._dydx_sweep_on_limit_error(err, reason="tp_update_limit")
            if swept:
                tx_hash, order_id = await self.dydx_api.place_take_profit_order(
                    side=trade.direction,
                    size=dydx_size,
                    tp_price=new_tp_price,
                    time_to_live_seconds=3600,
                )
                if tx_hash and order_id:
                    self.pending_tp_order = {
                        'direction': trade.direction,
                        'entry_price': trade.entry_price,
                        'tp_price': new_tp_price,
                        'target_pct': target_pct,
                        'leverage': trade.actual_leverage or self.config.leverage,
                        'created_time': time.time(),
                        'status': 'PENDING',
                        'dydx_order_id': order_id
                    }
                    if target_pct > 0:
                        trade.actual_target_pct = target_pct
                    self.dydx_real_position["tp_order_id"] = order_id
                    self._register_dydx_order(order_id, order_type="LONG_TERM", kind="TP_UPDATE")
                    self._journal_dydx_event(
                        "tp_updated",
                        order_id=order_id,
                        order_type="LONG_TERM",
                        tp_price=new_tp_price,
                        result="ok",
                    )
                    try:
                        await self._log_dydx_protection_snapshot(reason="tp_updated")
                    except Exception:
                        pass
                    print(f"   ✅ 新止盈單已掛! ID: {order_id}")
                    return True
            return False
        except Exception as e:
            print(f"   ❌ 更新止盈異常: {e}")
            self._journal_dydx_event(
                "tp_update_failed",
                tp_price=new_tp_price,
                error=str(e),
            )
            return False

    def update_dydx_take_profit(self, new_tp_price: float, new_target_pct: Optional[float] = None) -> bool:
        """
        同步包裝器：在同步流程中安全呼叫 `update_dydx_take_profit_async`。
        """
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.update_dydx_take_profit_async(new_tp_price, new_target_pct))

        print("⚠️ update_dydx_take_profit() 在 async context 內被呼叫，請改用 await update_dydx_take_profit_async()")
        return False

    def maybe_update_dydx_take_profit(self, current_price: float, market_data: Optional[Dict] = None) -> None:
        """
        依照設定條件更新 dYdX TP：
        - 只在 Phase1→Phase2 或 max_profit_pct 跨整數門檻時觸發
        - 含冷卻時間與最小變動門檻
        """
        if not self.dydx_sync_enabled or not self.dydx_api or not self.dydx_real_position:
            return
        if not self.active_trade or not self.pending_tp_order:
            return
        if not self.pending_tp_order.get('dydx_order_id'):
            return

        cfg = self.config
        use_phase = bool(getattr(cfg, "tp_update_on_phase_change", False))
        use_integer = bool(getattr(cfg, "tp_update_on_integer_cross", False))
        if not use_phase and not use_integer:
            return

        now_ts = time.time()
        cooldown_sec = _coerce_float(getattr(cfg, "tp_update_cooldown_sec", 0.0), default=0.0)
        if cooldown_sec > 0 and (now_ts - getattr(self, "_last_tp_update_ts", 0.0)) < cooldown_sec:
            return

        trade = self.active_trade
        candidates: list[tuple[str, float]] = []

        # 1) Phase1 → Phase2 觸發
        if use_phase and self.config.two_phase_exit_enabled:
            try:
                net_pnl_pct = self.calculate_current_pnl_pct(current_price, include_fees=True)
                max_net = getattr(trade, "max_net_profit_pct", None)
                if max_net is None:
                    max_net = net_pnl_pct
                else:
                    max_net = max(max_net, net_pnl_pct)
                trade.max_net_profit_pct = max_net

                two_phase_mgr = getattr(self, "_two_phase_exit_manager", None)
                if two_phase_mgr is None:
                    two_phase_mgr = TwoPhaseExitManager(self.config)

                phase_info = two_phase_mgr.get_current_phase(net_pnl_pct, max_net, market_data or {})
                current_phase = phase_info.get("phase")
                last_phase = getattr(trade, "_tp_last_phase", None)
                if last_phase is None:
                    trade._tp_last_phase = current_phase
                elif last_phase == 1 and current_phase == 2:
                    phase_target = _coerce_float(phase_info.get("target_pct", 0.0), default=0.0)
                    if phase_target > 0:
                        candidates.append(("phase", phase_target))
                    trade._tp_last_phase = current_phase
                else:
                    trade._tp_last_phase = current_phase
            except Exception:
                pass

        # 2) max_profit_pct 跨整數門檻觸發
        if use_integer:
            step = _coerce_float(getattr(cfg, "tp_update_integer_step", 1.0), default=1.0)
            if step > 0:
                max_profit_pct = _coerce_float(getattr(trade, "max_profit_pct", 0.0), default=0.0)
                if max_profit_pct >= step:
                    current_step = int(max_profit_pct // step)
                    last_step = getattr(trade, "_tp_last_int_step", 0)
                    if current_step > last_step:
                        offset = _coerce_float(getattr(cfg, "tp_update_integer_offset", 0.0), default=0.0)
                        integer_target = current_step * step + offset
                        if integer_target > 0:
                            candidates.append(("integer", integer_target))
                        trade._tp_last_int_step = current_step

        if not candidates:
            return

        # 依更新策略選擇目標
        policy = str(getattr(cfg, "tp_update_policy", "extend")).strip().lower()
        if policy == "tighten":
            reason, target_pct = min(candidates, key=lambda x: x[1])
        else:
            reason, target_pct = max(candidates, key=lambda x: x[1])

        current_target_pct = _coerce_float(self.pending_tp_order.get("target_pct", 0.0), default=0.0)
        if current_target_pct <= 0:
            current_target_pct = _coerce_float(getattr(trade, "actual_target_pct", 0.0), default=0.0)
        if current_target_pct <= 0:
            current_target_pct = _coerce_float(getattr(self.config, "target_profit_pct", 0.0), default=0.0)

        if policy == "tighten" and target_pct >= current_target_pct:
            return
        if policy != "tighten" and target_pct <= current_target_pct:
            return

        entry_price = trade.entry_price
        leverage = trade.actual_leverage if hasattr(trade, "actual_leverage") else trade.leverage
        leverage = _coerce_float(leverage, default=50.0)
        if leverage <= 0:
            leverage = 50.0

        entry_fee_pct = _coerce_float(
            (self.config.maker_fee_pct if self.config.use_maker_simulation else self.config.taker_fee_pct),
            default=0.0,
        )
        tp_total_fee_pct = entry_fee_pct * 2
        tp_price_move_pct = (target_pct / leverage) + tp_total_fee_pct

        if trade.direction == "LONG":
            new_tp_price = entry_price * (1 + tp_price_move_pct / 100)
        else:
            new_tp_price = entry_price * (1 - tp_price_move_pct / 100)

        old_tp_price = _coerce_float(self.pending_tp_order.get("tp_price", 0.0), default=0.0)
        if old_tp_price > 0:
            price_diff_pct = abs(new_tp_price - old_tp_price) / old_tp_price * 100
            min_diff = _coerce_float(getattr(cfg, "tp_update_min_price_diff_pct", 0.0), default=0.0)
            if min_diff > 0 and price_diff_pct < min_diff:
                return

        ok = self.update_dydx_take_profit(new_tp_price, target_pct)
        if ok:
            self._last_tp_update_ts = now_ts
            self._last_tp_update_target_pct = target_pct
            print(f"   🔄 TP更新({reason}): 目標 {target_pct:.2f}% → 價格 ${new_tp_price:,.2f}")

    def calculate_current_pnl_pct(self, current_price: float, include_fees: bool = True) -> float:
        """
        🆕 v12.0 計算當前持倉的盈虧百分比 (槓桿後)
        
        以實際進場價計算，不是模擬價
        
        🔧 v14.6.40: 修正手續費影響計算
        - 手續費影響 = fee_rate × 2 (開倉+平倉) × 槓桿
        - 不再用 breakeven 反算，避免邏輯錯誤

        Args:
            current_price: 當前價格
            include_fees: 是否扣除 round-trip 手續費 (預設 True)
        """
        if not self.active_trade:
            return 0.0
        
        trade = self.active_trade
        entry_price = trade.entry_price
        leverage = trade.actual_leverage if hasattr(trade, 'actual_leverage') else trade.leverage
        leverage = _coerce_float(leverage, default=50.0)
        if leverage <= 0:
            leverage = 50.0
        
        if trade.direction == "LONG":
            price_move_pct = (current_price - entry_price) / entry_price * 100
        else:
            price_move_pct = (entry_price - current_price) / entry_price * 100

        gross_pnl = price_move_pct * leverage
        if not include_fees:
            return gross_pnl

        # 🔧 v14.6.40: 簡化手續費影響計算
        # 手續費影響 ROE% = (開倉費率 + 平倉費率) × 槓桿
        fee_pct = self.config.taker_fee_pct  # 預設 TAKER
        if hasattr(trade, 'entry_type') and trade.entry_type == 'MAKER':
            fee_pct = self.config.maker_fee_pct
        total_fee_pct = (fee_pct or 0.0) * 2  # 開倉 + 平倉
        fee_mult = _fee_leverage_multiplier(self.config, leverage)
        fee_impact_roe = total_fee_pct * fee_mult / 100 * 100  # 換算為 ROE%

        return gross_pnl - fee_impact_roe
    
    def get_progressive_stop_loss(self, max_pnl_pct: float) -> tuple[float, str]:
        """
        🆕 v12.8 階段性鎖利止損 (支援 N%鎖N%)
        🆕 v12.9 新增中間數鎖利 (高頻策略)
        
        根據【最大盈虧%】，返回應該設定的止損%
        核心原則: 達到 +N% 就鎖住 N%，不追高
        
        Args:
            max_pnl_pct: 最大盈虧% (槓桿後) - 注意是最大值，不是當前值
        
        Returns:
            (止損%, 階段名稱)
        """
        # 先算「階段表」的基準鎖利線（Fallback）
        stages = self.config.profit_lock_stages
        if stages:
            stage_stop = None
            stage_name = None
            for min_pnl, max_pnl, lock_at in stages:
                if min_pnl <= max_pnl_pct < max_pnl:
                    stage_stop = lock_at
                    if lock_at < 0:
                        stage_name = f"止損 {lock_at:.1f}%"
                    elif lock_at == 0:
                        stage_name = "保本"
                    else:
                        stage_name = f"鎖利 +{lock_at:.1f}%"
                    break

            if stage_stop is None:
                last_stage = stages[-1]
                stage_stop = last_stage[2]
                stage_name = f"鎖利 +{last_stage[2]:.1f}%"
        else:
            base_sl = -self.config.pre_stop_loss_pct if self.config.pre_stop_loss_pct else -0.1
            stage_stop = base_sl
            stage_name = "預設"

        # 併用規則：當多個鎖利機制同時啟用時，取「更保護」的那條（數值較高）
        candidates: list[tuple[float, str, str]] = [(stage_stop, stage_name, "stages")]

        # 🆕 v12.9 中間數鎖利
        if self.config.use_midpoint_lock and max_pnl_pct > 0:
            lock_start_pct = _coerce_float(getattr(self.config, "lock_start_pct", 0.0), default=0.0)
            min_lock_pct = _coerce_float(getattr(self.config, "min_lock_pct", 0.0), default=0.0)
            if lock_start_pct <= 0 or max_pnl_pct >= lock_start_pct:
                ratio = self.config.midpoint_ratio or 0.5
                midpoint_stop = max_pnl_pct * ratio
                if min_lock_pct > 0:
                    midpoint_stop = max(midpoint_stop, min_lock_pct)
                min_stop = -self.config.pre_stop_loss_pct if self.config.pre_stop_loss_pct else -0.1
                midpoint_stop = max(midpoint_stop, min_stop)
                midpoint_name = f"📍 中間數: 鎖住 +{midpoint_stop:.2f}% (最高{max_pnl_pct:.2f}%×{ratio:.0%})"
                candidates.append((midpoint_stop, midpoint_name, "midpoint"))

        # 🆕 v12.8 N%鎖N% 策略
        n_lock_threshold = getattr(self.config, "n_lock_n_threshold", 1.0)
            
        if self.config.use_n_lock_n and max_pnl_pct >= n_lock_threshold:
            lock_level = int(max_pnl_pct)
            lock_at = lock_level - self.config.n_lock_n_buffer
            nlock_name = f"🔐 N%鎖N%: 鎖住 +{lock_at:.1f}%"
            candidates.append((lock_at, nlock_name, "n_lock_n"))

        # 🆕 v14.17: 中間數優先，其次 N%鎖N%，最後才是階段表
        midpoint_candidates = [c for c in candidates if c[2] == "midpoint"]
        nlock_candidates = [c for c in candidates if c[2] == "n_lock_n"]
        stage_candidates = [c for c in candidates if c[2] == "stages"]

        if midpoint_candidates:
            best_stop, best_name, best_kind = max(midpoint_candidates, key=lambda x: x[0])
        elif nlock_candidates:
            best_stop, best_name, best_kind = max(nlock_candidates, key=lambda x: x[0])
        else:
            best_stop, best_name, best_kind = max(stage_candidates, key=lambda x: x[0])

        # 節流日誌（避免每 tick 狂刷）
        if best_kind == "midpoint" and best_stop > 0:
            if not hasattr(self, '_last_midpoint_log_pct') or abs(best_stop - self._last_midpoint_log_pct) >= 0.1:
                print(f"   🔐 {best_name}")
                self._last_midpoint_log_pct = best_stop
        elif best_kind == "n_lock_n" and best_stop > 0:
            if not hasattr(self, '_last_nlock_log_pct') or abs(best_stop - self._last_nlock_log_pct) >= 0.1:
                print(f"   🔐 {best_name}")
                self._last_nlock_log_pct = best_stop

        return best_stop, best_name
    
    def check_progressive_stop_loss(self, current_price: float) -> Optional[tuple[str, float]]:
        """
        🆕 v12.8 檢查階段性止損是否觸發 (支援 N%鎖N%)
        
        核心邏輯:
        1. 用【最大盈利】決定鎖利級別 (達到 +N% 就鎖住 N%)
        2. 用【當前盈利】判斷是否觸發止損
        
        🆕 v14.5 dYdX 同步提前觸發機制:
        - 當 dYdX Sync 啟用時，提前 0.05% 觸發平倉
        - 補償網路延遲造成的價格滑點 (約 0.5-2 秒)
        
        Args:
            current_price: 當前價格
        
        Returns:
            (出場原因, 出場價格) 或 None
        """
        if not self.active_trade:
            return None
        
        trade = self.active_trade
        
        # 計算當前盈虧%
        # 🔧 v14.6.41: 止損判斷應使用【毛盈虧】(不含手續費)
        # 原因: 手續費是固定成本，止損應該看「價格移動」是否超過閾值
        # 例: 52x槓桿, 0.08% round-trip fee = 4.16% ROE 損失
        # 如果用【淨盈虧】判斷，開倉就會顯示 -4.16%，立即觸發止損
        current_pnl_pct = self.calculate_current_pnl_pct(current_price, include_fees=False)
        
        # 🆕 寬限期：入場後 early_stop_grace_sec 內，價格移動若未超過噪音*sigma，則不觸發止損
        try:
            entry_ts = datetime.fromisoformat(trade.entry_time)
            elapsed = (datetime.now() - entry_ts).total_seconds()
            if elapsed < self.config.early_stop_grace_sec:
                noise = max(self._get_noise_pct(60), self._get_noise_pct(300))
                move_pct = abs((current_price - trade.entry_price) / trade.entry_price * 100)
                if move_pct < noise * self.config.noise_stop_sigma:
                    return None
        except Exception:
            pass
        
        # 更新交易記錄的最大盈利
        old_max_pnl = trade.max_profit_pct
        if current_pnl_pct > trade.max_profit_pct:
            trade.max_profit_pct = current_pnl_pct
            # 🆕 v14.6.16: 當最高獲利創新高時，印出日誌
            if trade.max_profit_pct > 0 and (trade.max_profit_pct - old_max_pnl) >= 0.1:
                print(f"   📈 最高獲利更新: +{trade.max_profit_pct:.2f}% (舊: +{old_max_pnl:.2f}%)")
        
        # 🆕 v12.8: 用【最大盈利】決定止損線
        # 這樣即使價格回落，止損線也不會下降
        stop_loss_pct, stage_name = self.get_progressive_stop_loss(trade.max_profit_pct)
        
        # 🆕 v14.6.3: 當最大獲利提高時，動態更新 dYdX 止損單
        # 🔧 v14.6.9: 改進 - 即使獲利沒創新高，只要 stop_loss_pct > 0 且沒有掛單就補掛
        # 🔧 v14.6.26: 每次需要更新止損時，先清空所有未成交掛單再掛新單
        # 🔧 v14.6.27: 修復 - N%鎖N% 止損線也要同步到 dYdX (即使還在虧損區)
        # 🔧 v14.9.8: 修復 - 中間數鎖利應該要動態更新 dYdX 止損單
        if self.dydx_sync_enabled and self.dydx_real_position:
            should_update_sl = False
            min_diff_pct = _coerce_float(getattr(self.config, "sl_update_min_diff_pct", 0.05), default=0.05)
            force_update_sl = False
            current_sl_pct = None

            if self.pending_sl_order:
                current_sl_pct = self.pending_sl_order.get('stop_pct', 0)
            
            # 情況 1: 獲利創新高 → 更新止損單
            # 🔧 v14.9.8: 移除 stop_loss_pct > 0 限制，只要獲利創新高就該檢查更新
            if trade.max_profit_pct > old_max_pnl:
                # 檢查新的止損線是否比當前更有利
                if current_sl_pct is None or stop_loss_pct > current_sl_pct + min_diff_pct:
                    should_update_sl = True
                    print(f"   📈 [v14.9.8] 獲利創新高 {trade.max_profit_pct:.2f}% → 更新止損 {stop_loss_pct:+.2f}%")
                
            # 情況 2: 應該要有止損單但沒有 → 補掛（節流 + 缺單寬限）
            if not self.pending_sl_order:
                registry_has_conditional = False
                try:
                    registry_has_conditional = any(
                        meta.get("order_type") == "CONDITIONAL"
                        for meta in (self._dydx_order_registry or {}).values()
                    )
                except Exception:
                    registry_has_conditional = False
                pending_update = getattr(self, "_pending_sl_update", None)

                if registry_has_conditional or pending_update:
                    # 已有條件單或已排程更新，不視為缺單
                    self._dydx_sl_missing_since = None
                else:
                    now_ts = time.time()
                    missing_since = getattr(self, "_dydx_sl_missing_since", None)
                    if missing_since is None:
                        self._dydx_sl_missing_since = now_ts
                        missing_since = now_ts
                    missing_age = now_ts - missing_since
                    missing_grace_sec = _coerce_float(
                        getattr(self.config, "dydx_sl_missing_grace_sec", 3.0),
                        default=3.0
                    )
                    missing_min_interval_sec = _coerce_float(
                        getattr(self.config, "dydx_sl_missing_min_interval_sec", 8.0),
                        default=8.0
                    )
                    last_missing_attempt = getattr(self, "_last_sl_missing_attempt_ts", 0.0)
                    if missing_age >= missing_grace_sec and (now_ts - last_missing_attempt) >= missing_min_interval_sec:
                        should_update_sl = True
                        self._last_sl_missing_attempt_ts = now_ts
                        print(f"   📋 [v14.6.9] 補掛 dYdX 止損單 ({stop_loss_pct:+.2f}%)")
            else:
                self._dydx_sl_missing_since = None
            
            # 情況 3: N%鎖N% 止損線改變 → 更新止損單 (即使還在虧損區)
            # 🔧 v14.6.28: 符號統一後直接比較（負=虧損，正=鎖利）
            if current_sl_pct is not None:
                # 如果 N%鎖N% 計算的止損線更有利（更高），就更新
                if stop_loss_pct > current_sl_pct + min_diff_pct:  # 加容差避免頻繁更新
                    should_update_sl = True
                    # 🔧 v14.6.36: 節流打印，移到實際更新時才打印

                # 進入鎖利區（由負轉正）時，優先強制更新一次，避免停留在虧損止損
                if stop_loss_pct >= 0 and current_sl_pct < 0:
                    should_update_sl = True
                    force_update_sl = True
            elif stop_loss_pct >= 0:
                # 沒有本地 SL 追蹤但進入鎖利區，強制補掛
                should_update_sl = True
                force_update_sl = True
            
            if should_update_sl:
                # 🔧 v14.6.31: 節流 + backoff，避免取消/掛單失敗時狂刷，造成 block rate limit 與疊單
                now_ts = time.time()
                if now_ts < getattr(self, "_dydx_tx_backoff_until", 0.0):
                    # backoff 期間不排程
                    pass
                else:
                    cooldown_sec = _coerce_float(getattr(self.config, "sl_update_cooldown_sec", 8.0), default=8.0)
                    last_ts = getattr(self, "_last_sl_update_attempt_ts", 0.0)
                    last_stop = getattr(self, "_last_sl_update_stop_pct", None)
                    is_same = (last_stop is not None and abs(float(stop_loss_pct) - float(last_stop)) < min_diff_pct)

                    if force_update_sl and not is_same:
                        print(f"   📋 [v14.6.42] 🔴 進入鎖利區，優先更新 dYdX 止損單: {stop_loss_pct:+.2f}%")
                        try:
                            import asyncio
                            try:
                                asyncio.get_running_loop()
                                asyncio.create_task(self.update_dydx_stop_loss_async(float(stop_loss_pct)))
                            except RuntimeError:
                                asyncio.run(self.update_dydx_stop_loss_async(float(stop_loss_pct)))
                            self._last_sl_update_attempt_ts = now_ts
                            self._last_sl_update_stop_pct = float(stop_loss_pct)
                        except Exception as e:
                            print(f"   ⚠️ [v14.6.42] 鎖利區更新失敗: {e}")
                    elif (now_ts - last_ts) >= cooldown_sec and not is_same:
                        # 🔧 v14.9.10: 只要進入鎖利區且變動較大，就立即同步更新 dYdX 止損單
                        # 避免排程來不及執行就被觸發平倉的問題
                        diff_pct = abs(float(stop_loss_pct) - float(last_stop or -999))
                        # 只要是鎖利 (>=0) 且變動超過 0.1% ROE，就立即更新
                        should_immediate = (stop_loss_pct >= 0 and diff_pct >= 0.1)
                        
                        if should_immediate:
                            print(f"   📋 [v14.9.10] 🔴 立即更新 dYdX 止損單: {stop_loss_pct:+.2f}% (變動: {diff_pct:.2f}%)")
                            try:
                                import asyncio
                                try:
                                    loop = asyncio.get_running_loop()
                                    # 已在 async context，用 create_task (不會 block)
                                    asyncio.create_task(self.update_dydx_stop_loss_async(float(stop_loss_pct)))
                                except RuntimeError:
                                    # 不在 async context，直接 asyncio.run
                                    asyncio.run(self.update_dydx_stop_loss_async(float(stop_loss_pct)))
                                self._last_sl_update_attempt_ts = now_ts
                                self._last_sl_update_stop_pct = float(stop_loss_pct)
                            except Exception as e:
                                print(f"   ⚠️ [v14.6.34] 立即更新失敗: {e}")
                        else:
                            self._pending_sl_update = {
                                'stop_pct': float(stop_loss_pct),
                                'timestamp': now_ts,
                                'reason': 'progressive_stop_loss',
                            }
                            self._last_sl_update_attempt_ts = now_ts
                            self._last_sl_update_stop_pct = float(stop_loss_pct)
                            print(f"   📋 [v14.6.31] 排程 dYdX 止損單更新: {stop_loss_pct:+.2f}%")
        
        # 🆕 v14.5: dYdX Sync 提前觸發機制
        # 補償 dYdX 平倉的網路延遲 (約 0.5-2 秒)
        # 🔧 v14.6.40: 降低緩衝值，避免過度提前平倉
        dydx_sync_buffer_pct = 0.0
        if self.dydx_sync_enabled and self.dydx_real_position:
            leverage = trade.actual_leverage if trade.actual_leverage else self.config.leverage
            leverage = _coerce_float(leverage, default=50.0)
            if leverage <= 0:
                leverage = 50.0
            # 🔧 v14.6.40: 改用固定 ROE 緩衝 (不再乘以槓桿)
            # 原本: 0.05% 價格 × 50X = 2.5% ROE 緩衝 (太大)
            # 現在: 固定 0.03% ROE 緩衝 (約 $0.05 價格 / $87000 × 50X = 0.003%)
            dydx_sync_buffer_pct = 0.03  # 固定 0.03% ROE 緩衝
            # 只在正獲利時啟用提前觸發，避免放大虧損
            if stop_loss_pct > 0:
                stop_loss_pct += dydx_sync_buffer_pct
                stage_name = f"{stage_name} [+{dydx_sync_buffer_pct:.2f}% dYdX緩衝]"
        
        # 🆕 v12.10: 計算止損線對應的精確價格 (預掛單概念)
        # 這確保出場價格是止損線價格，而不是當前市價 (減少滑點)
        leverage = trade.actual_leverage if trade.actual_leverage else self.config.leverage
        leverage = _coerce_float(leverage, default=50.0)
        if leverage <= 0:
            leverage = 50.0

        # 槓桿盈虧% → 價格移動% (含 round-trip 手續費)
        total_fee_pct = 0.0
        be_price = getattr(trade, 'breakeven_price', 0.0) or 0.0
        if be_price > 0 and trade.entry_price > 0:
            if trade.direction == "LONG":
                total_fee_pct = max(0.0, (be_price / trade.entry_price - 1) * 100)
            else:
                total_fee_pct = max(0.0, (1 - be_price / trade.entry_price) * 100)
        else:
            fee_pct = self.config.maker_fee_pct if self.config.use_maker_simulation else self.config.taker_fee_pct
            total_fee_pct = max(0.0, (fee_pct or 0.0) * 2)

        price_move_pct_for_sl = (stop_loss_pct / leverage) + total_fee_pct
        
        if trade.direction == "LONG":
            # LONG: 盈利 = (當前價 - 進場價) / 進場價 * 100 * 槓桿
            # 止損價 = 進場價 * (1 + price_move_pct_for_sl / 100)
            stop_price = trade.entry_price * (1 + price_move_pct_for_sl / 100)
        else:
            # SHORT: 盈利 = (進場價 - 當前價) / 進場價 * 100 * 槓桿
            # 止損價 = 進場價 * (1 - price_move_pct_for_sl / 100)
            stop_price = trade.entry_price * (1 - price_move_pct_for_sl / 100)
        
        # 檢查是否觸發止損
        # 止損觸發條件: 當前盈虧 ≤ 止損線
        if current_pnl_pct <= stop_loss_pct:
            emoji = "🔐" if stop_loss_pct >= 1.0 else ("🔒" if stop_loss_pct >= 0 else "🚨")
            reason = f"{emoji} {stage_name}: 當前 {current_pnl_pct:.2f}% ≤ 止損線 {stop_loss_pct:.2f}% (最高 {trade.max_profit_pct:.2f}%)"
            
            # 🆕 v14.6.33: 混合止損策略
            # - is_emergency=False: 等待 dYdX 條件單觸發（精確價位出場）
            # - is_emergency=True: 緊急市價平倉（虧損超過緊急線）
            # 🔧 v14.9.9: 收緊緊急止損線，避免大虧損
            # 問題: 原本 -0.75% × 2.5 = -1.88%，太寬鬆導致 23 筆超過 -1.5%
            is_emergency = False
            if stop_loss_pct >= 0:  # 🔧 鎖利區（止損線在獲利區）
                # 鎖利區的緊急線 = 止損線 - 1.5%（縮短等待時間）
                # 例: 鎖利 +0.91% → 緊急線 -0.59%
                emergency_threshold = stop_loss_pct - 1.5
            else:  # 虧損區
                # 🔧 v14.9.9: 收緊緊急線
                # 原本: 止損線 × 2.5 (太寬鬆)
                # 現在: 止損線 × 1.5 (更積極保護)
                # 例: -0.75% → -1.125% (約 -1.1%)
                emergency_threshold = stop_loss_pct * 1.5
            
            if current_pnl_pct <= emergency_threshold:
                is_emergency = True
                reason = f"🆘 緊急止損 {stage_name}: 當前 {current_pnl_pct:.2f}% << 緊急線 {emergency_threshold:.2f}%"
            
            # 🆕 v12.10: 使用止損線價格出場 (預掛單概念)
            # 在 Paper Trading 中，這模擬了 Limit 止損單成交在精確價位
            # 而非 Market Order 吃滑點
            # 🆕 v14.6.33: 返回 (reason, stop_price, is_emergency)
            return reason, stop_price, is_emergency
        
        return None

    def open_position(
        self,
        direction: str,  # "LONG" or "SHORT"
        current_price: float,
        strategy: str,
        probability: float,
        confidence: float,
        market_data: Dict,
        is_limit_fill: bool = False,  # 🆕 v13.2: 是否為限價單成交 (若是，不需模擬滑點)
        override_price: Optional[float] = None, # 🆕 Strict Sync: 強制使用真實成交價
        override_size: Optional[float] = None   # 🆕 Strict Sync: 強制使用真實成交量
    ) -> Optional[TradeRecord]:
        """
        開倉 (支援動態參數調整 + Maker 分批進場模擬)
        """
        can, reason = self.can_trade()
        if not can:
            print(f"⚠️ 無法開倉: {reason}")
            return None
        
        try:
            # 🆕 動態計算參數
            dynamic_params = self.calculate_dynamic_params(market_data)
            leverage = dynamic_params['leverage']
            target_pct = dynamic_params['target_pct']
            stop_loss_pct = dynamic_params['stop_loss_pct']
            max_hold_min = dynamic_params['max_hold_min']
            fee_pct = dynamic_params['fee_pct']
            
            # 🔧 v14.9.3: 修正倉位計算
            # position_size_usdt = 本金 (margin)
            # 名義價值 = 本金 × 槓桿
            margin_usdt = self.config.position_size_usdt  # 本金 (如 $100)
            notional_usdt = margin_usdt * leverage  # 名義價值 = $100 × 55 = $5500
            
            # 計算部位大小 (BTC) = 名義價值 / 當前價格
            position_btc = notional_usdt / current_price
            
            # 精度處理 (BTC 最多 4 位小數，dYdX 支援到 0.0001)
            import math
            position_btc = math.floor(position_btc * 10000) / 10000  # 向下取整到 0.0001
            
            # 確保符合最小名義價值 ($100)
            min_notional = 100.0
            if position_btc * current_price < min_notional:
                position_btc = math.ceil(min_notional / current_price * 10000) / 10000
            
            # 計算實際名義價值 (USDT)
            position_size_usdt = position_btc * current_price

            # Initialize variables
            entry_price = current_price
            entry_type = 'TAKER' # Default to TAKER
            entry_slippage = 0.0
            entry_duration = 0.0
            entry_batches = 1

            # 標記進場類型
            if is_limit_fill:
                entry_type = "MAKER"
                entry_slippage = 0.0
            elif self.config.use_maker_simulation and self.paper_mode:
                maker_result = self.simulate_maker_entry(current_price, direction, position_btc)
                entry_price = maker_result["avg_price"]
                entry_type = "MAKER"
                entry_batches = int(maker_result.get("batches", 1) or 1)
                entry_duration = float(maker_result.get("duration_sec", 0.0) or 0.0)
                entry_slippage = float(maker_result.get("slippage_pct", 0.0) or 0.0)
            
            # 🆕 v4.1: dYdX 滑點模擬 (Paper Trading Only, 非 Sync 模式)
            dydx_sim = getattr(self.config, 'dydx_simulation', None)
            if dydx_sim and dydx_sim.get('enabled', False) and self.paper_mode and not self.dydx_sync_enabled:
                entry_slippage_pct = dydx_sim.get('entry_slippage_pct', 0.1)
                
                # 計算滑點價格
                # LONG: 買入時會買貴 (price + slippage)
                # SHORT: 賣出時會賣便宜 (price - slippage)
                if direction == "LONG":
                    entry_price = current_price * (1 + entry_slippage_pct / 100)
                else:
                    entry_price = current_price * (1 - entry_slippage_pct / 100)
                
                entry_slippage = entry_slippage_pct
                entry_type = 'DYDX_SIM'
                
                # 顯示滑點資訊
                slippage_cost = abs(entry_price - current_price)
                print(f"   📉 dYdX 滑點模擬: {direction} @ ${entry_price:,.2f} (滑點 ${slippage_cost:.2f} / {entry_slippage_pct}%)")

            use_ref_entry_price = False
            reference_entry_price = entry_price
            if self.dydx_sync_enabled and self.dydx_api:
                use_ref_entry_price = bool(getattr(self.config, 'dydx_use_reference_entry_price', False))
                if use_ref_entry_price:
                    bid = _coerce_float(market_data.get('bid', 0.0), default=0.0)
                    ask = _coerce_float(market_data.get('ask', 0.0), default=0.0)
                    if direction == "LONG" and bid > 0:
                        reference_entry_price = bid
                    elif direction == "SHORT" and ask > 0:
                        reference_entry_price = ask
                    elif bid > 0 and ask > 0:
                        reference_entry_price = (bid + ask) / 2
            
            # 🆕 5. Strict Sync Execution (dYdX)
            # 在計算 TP/SL 前先執行真實交易，確保價格正確
            if self.dydx_sync_enabled and self.dydx_api:
                # 🔧 同步模式下，Paper 的槓桿必須與 dYdX 設定一致，避免 PnL/鎖利線不同步
                dydx_cfg_lev = None
                try:
                    dydx_cfg_lev = _coerce_int(getattr(getattr(self.dydx_api, 'config', None), 'leverage', None), default=0)
                except Exception:
                    dydx_cfg_lev = None

                leverage = _coerce_int(getattr(self.config, 'leverage', leverage), default=int(leverage) if leverage else 50)
                if leverage <= 0:
                    leverage = 50
                if dydx_cfg_lev and dydx_cfg_lev > 0:
                    leverage = int(dydx_cfg_lev)
                leverage = min(int(leverage), 50)
                if override_price is not None:
                     # 外部已執行 (e.g. from run loop or pre-entry fill)
                     entry_price = override_price
                     entry_slippage = 0.0
                     if use_ref_entry_price:
                         reference_entry_price = override_price
                else:
                     # 內部執行 dYdX
                     print(f"🔒 [StrictSync] 同步開倉: {direction}...")
                     import asyncio
                     
                     # 🛡️ Safety: 避免 dYdX 已有倉位仍繼續加倉 (Paper 無倉時特別危險)
                     # 🔧 v14.9.8: 以 REST API 為準 (最可靠)，修正本地追蹤錯誤
                     try:
                         # ⚠️ 開倉安全檢查必須用 fresh REST（不要用快取），避免快取空窗導致重複開倉累積倉位
                         if hasattr(self.dydx_api, "get_positions_fresh"):
                             positions = asyncio.run(self.dydx_api.get_positions_fresh())
                         else:
                             positions = asyncio.run(self.dydx_api.get_positions())
                         has_btc_position = False
                         for pos in positions or []:
                             if pos.get('market') == 'BTC-USD' and abs(_coerce_float(pos.get('size', 0), default=0.0)) > 0.0001:
                                 has_btc_position = True
                                 print("⚠️ [StrictSync] dYdX 已有持倉(REST 確認)，先同步/平倉後再開新倉")
                                 return None
                         
                         # 🔧 v14.9.8: REST 確認無持倉，清空可能過時的本地追蹤
                         if not has_btc_position:
                             if self.dydx_real_position:
                                 print(f"🔄 [v14.9.8] REST 確認無持倉，清空過時的 dydx_real_position")
                                 self.dydx_real_position = None
                             if hasattr(self, 'dydx_ws') and self.dydx_ws and self.dydx_ws.has_position("BTC-USD"):
                                 print(f"⚠️ [v14.9.8] REST 無持倉但 WS 有，可能是 WS 延遲")
                     except Exception as e:
                         print(f"⚠️ [StrictSync] REST 檢查失敗: {e}，改用本地追蹤")
                         # 回退到本地追蹤
                         if self.dydx_real_position and abs(_coerce_float(self.dydx_real_position.get('size', 0), default=0.0)) > 0.0001:
                             print("⚠️ [StrictSync] dYdX 已有持倉(本地追蹤)，先同步/平倉後再開新倉")
                             return None
                     
                     # 🔧 v14.3: 移除 API 冷卻延遲 (WebSocket Data Hub 已解決 429 問題)
                     # 舊代碼: time.sleep(2.0)  # 等待 API 限速恢復
                     
                     # 使用 v2 開倉 (即時)
                     try:
                         dydx_success, dydx_fill_price = asyncio.run(
                             self._dydx_open_position_v2(
                                 direction,
                                 entry_price,
                                 target_pct=target_pct,
                                 stop_pct=stop_loss_pct,
                                 leverage=leverage,
                                 reference_price=reference_entry_price if use_ref_entry_price else None,
                                 use_reference_price=use_ref_entry_price,
                             )
                         )
                         
                         if not dydx_success:
                              print(f"❌ [StrictSync] dYdX 開倉失敗 -> 取消 Paper Trade")
                              return None
                         
                         # Sync Success
                         old_entry = entry_price
                         actual_fill_price = dydx_fill_price
                         if use_ref_entry_price and reference_entry_price > 0:
                             entry_price = reference_entry_price
                         else:
                             entry_price = actual_fill_price
                         entry_slippage = 0.0
                         
                         # 確保 Size 同步 (以 dYdX 真實成交為準)
                         actual_size = 0.0
                         if self.dydx_real_position:
                             actual_size = _coerce_float(self.dydx_real_position.get("size", 0.0), default=0.0)
                         if actual_size and actual_size > 0:
                             position_btc = actual_size
                         else:
                             position_btc = self.config.dydx_btc_size
                         position_size_usdt = position_btc * entry_price

                         if use_ref_entry_price and reference_entry_price > 0:
                             print(f"✅ [StrictSync] dYdX 成交: ${actual_fill_price:,.2f} (參考進場: ${entry_price:,.2f})")
                         else:
                             print(f"✅ [StrictSync] dYdX 成交: ${entry_price:,.2f} (Paper: ${old_entry:,.2f})")

                         # 🧯 v14.6.14: 以 dYdX 真實方向為準，避免 Paper 與 dYdX 多空相反
                         actual_side = None
                         try:
                             if self.dydx_real_position and self.dydx_real_position.get('side'):
                                 actual_side = str(self.dydx_real_position.get('side')).upper()
                         except Exception:
                             actual_side = None
                         if actual_side in ("LONG", "SHORT") and actual_side != direction:
                             print(f"⚠️ [StrictSync] 方向不一致，改用 dYdX 真實方向: {direction} → {actual_side}")
                             self._journal_dydx_event(
                                 "side_mismatch_corrected",
                                 intended_side=direction,
                                 actual_side=actual_side,
                             )
                             direction = actual_side
                     except Exception as e:
                         print(f"❌ [StrictSync] dYdX 執行錯誤: {e}")
                         return None
            
            # 計算止盈止損價格 (基於實際進場價)

            # target_pct / stop_loss_pct 以「淨 ROE%」表示，換算成價格需把 round-trip 手續費加回去
            leverage = _coerce_float(leverage, default=50.0)
            if leverage <= 0:
                leverage = 50.0

            entry_fee_pct = _coerce_float(fee_pct, default=0.0)  # 名目本金費率(%)
            taker_fee_pct = _coerce_float(getattr(self.config, 'taker_fee_pct', 0.0), default=0.0)

            # TP: 假設平倉費率與開倉相同 (Maker/Maker 或 Taker/Taker)
            tp_total_fee_pct = entry_fee_pct * 2
            tp_price_move_pct = _coerce_float(target_pct, default=0.0) / leverage + tp_total_fee_pct

            # SL: 保守估計平倉為 Taker (Maker + Taker)
            stop_loss_pct = _coerce_float(stop_loss_pct, default=0.0)
            exit_fee_pct = taker_fee_pct
            if getattr(self.config, 'taker_on_emergency_only', False):
                exit_fee_pct = entry_fee_pct if self.config.use_maker_simulation else taker_fee_pct
            sl_total_fee_pct = entry_fee_pct + exit_fee_pct
            fee_mult = _fee_leverage_multiplier(self.config, leverage)
            min_stop_loss_pct = sl_total_fee_pct * fee_mult + 0.1
            if stop_loss_pct < min_stop_loss_pct:
                stop_loss_pct = min_stop_loss_pct
            sl_price_move_pct = (-stop_loss_pct / leverage) + sl_total_fee_pct  # signed

            if direction == "LONG":
                tp_price = entry_price * (1 + tp_price_move_pct / 100)
                sl_price = entry_price * (1 + sl_price_move_pct / 100)
                side = 'buy'
            else:
                tp_price = entry_price * (1 - tp_price_move_pct / 100)
                sl_price = entry_price * (1 - sl_price_move_pct / 100)
                side = 'sell'
            
            # 下單 (真實交易或模擬)
            if not self.paper_mode and self.testnet_api and not self.dydx_sync_enabled:
                symbol = self.config.symbol.replace('/', '')
                side_upper = 'BUY' if side == 'buy' else 'SELL'
                order = self.testnet_api.place_order(symbol, side_upper, position_btc)
                if not order:
                    print("❌ API 下單失敗")
                    return None
            else:
                if not self.paper_mode and self.testnet_api and self.dydx_sync_enabled:
                    print("ℹ️ [dYdX Sync] 跳過 Binance 下單")
                # Paper Trading 模擬
                order = {'id': 'PAPER', 'status': 'filled'}
            
            # 創建交易記錄
            trade_id = f"WT_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.trades):04d}"
            
            trade = TradeRecord(
                trade_id=trade_id,
                timestamp=datetime.now().isoformat(),
                strategy=strategy,
                probability=probability,
                confidence=confidence,
                direction=direction,
                entry_price=entry_price,
                entry_time=datetime.now().isoformat(),
                leverage=leverage,
                position_size_usdt=position_size_usdt, # Use calculated position_size_usdt
                position_size_btc=position_btc, # Use calculated position_btc
                take_profit_price=tp_price,
                stop_loss_price=sl_price,
                obi=market_data.get('obi', 0),
                wpi=market_data.get('trade_imbalance', 0),
                vpin=market_data.get('vpin', 0),
                funding_rate=market_data.get('funding_rate', 0),
                oi_change_pct=market_data.get('oi_change_pct', 0),
                liq_pressure_long=market_data.get('liq_pressure_long', 50),
                liq_pressure_short=market_data.get('liq_pressure_short', 50),
                price_change_1m=market_data.get('price_change_1m', 0),
                price_change_5m=market_data.get('price_change_5m', 0),
                volatility_5m=market_data.get('volatility_5m', 0),
                strategy_probs=market_data.get('strategy_probs', {}),
                # 🆕 Maker 分批進場資訊
                entry_type=entry_type,
                entry_batches=entry_batches,
                entry_duration_sec=entry_duration,
                avg_entry_price=entry_price,
                entry_slippage_pct=entry_slippage,
                # 🐋 v10.6 鯨魚策略資訊
                is_whale_trade=market_data.get('whale_status', {}).get('signal_valid', False),
                whale_direction=market_data.get('whale_status', {}).get('whale_direction', ''),
                whale_buy_value=market_data.get('whale_status', {}).get('big_buy_value', 0.0),
                whale_sell_value=market_data.get('whale_status', {}).get('big_sell_value', 0.0),
                whale_trade_count=market_data.get('whale_status', {}).get('big_trade_count', 0),
                whale_expected_profit_pct=market_data.get('whale_status', {}).get('expected_profit_pct', 0.0),
                whale_target_price=market_data.get('whale_status', {}).get('expected_target_price', 0.0),
                whale_estimated_impact_pct=market_data.get('whale_status', {}).get('estimated_impact_pct', 0.0),
                whale_profit_lock_enabled=market_data.get('whale_profit_lock', {}).get('enabled', False),
                # 🆕 v13.4 六維評分記錄
                six_dim_long_score=market_data.get('six_dim', {}).get('long_score', 0),
                six_dim_short_score=market_data.get('six_dim', {}).get('short_score', 0),
                six_dim_fast_dir=market_data.get('six_dim', {}).get('fast_dir', ''),
                six_dim_medium_dir=market_data.get('six_dim', {}).get('medium_dir', ''),
                six_dim_slow_dir=market_data.get('six_dim', {}).get('slow_dir', ''),
                six_dim_obi_dir=market_data.get('six_dim', {}).get('obi_dir', ''),
                six_dim_momentum_dir=market_data.get('six_dim', {}).get('momentum_dir', ''),
                six_dim_volume_dir=market_data.get('six_dim', {}).get('volume_dir', ''),
                # 🆕 動態參數
                actual_leverage=leverage,
                actual_target_pct=target_pct,
                actual_stop_loss_pct=stop_loss_pct,
                actual_max_hold_min=max_hold_min,
                market_volatility=dynamic_params['volatility'],
                status="OPEN"
            )
            
            self.active_trade = trade
            self.trades.append(trade)
            self.daily_trades += 1
            self.last_trade_time = time.time()
            
            # 🔧 v14.6.25: 計算手續費 (基於名義價值)
            # 名義價值 = BTC_size × entry_price = trade.position_size_usdt
            trade.fee_usdt = trade.position_size_usdt * fee_pct / 100
            
            # 🆕 計算損益平衡價格 (超過此價才開始獲利)
            # 公式: 進場價 × (1 ± 總手續費% / 槓桿)
            # 總手續費 = 開倉手續費 + 預估平倉手續費
            total_fee_pct = fee_pct * 2  # 開倉 + 平倉
            fee_price_impact = total_fee_pct / 100  # 換算為比例
            
            if direction == "LONG":
                # 做多: 價格要漲過手續費才獲利
                trade.breakeven_price = entry_price * (1 + fee_price_impact)
            else:
                # 做空: 價格要跌過手續費才獲利
                trade.breakeven_price = entry_price * (1 - fee_price_impact)
            
            self._save_trades()
            
            # 計算損益平衡距離
            breakeven_distance = abs(trade.breakeven_price - entry_price) / entry_price * 100
            
            print(f"\n{'='*60}")
            print(f"📈 開倉成功: {trade_id}")
            print(f"   方向: {direction}")
            print(f"   策略: {strategy} ({probability:.1%})")
            print(f"   進場: ${entry_price:,.2f} ({entry_type}, {entry_batches}批)")
            print(f"   💰 損益平衡: ${trade.breakeven_price:,.2f} ({breakeven_distance:.4f}%)")
            print(f"   止盈: ${tp_price:,.2f} ({target_pct:+.3f}%)")
            print(f"   止損: ${sl_price:,.2f} ({-stop_loss_pct:+.3f}%)")
            print(f"   部位: {position_btc:.6f} BTC (${self.config.position_size_usdt})")
            print(f"   槓桿: {leverage}X (動態調整)")
            print(f"   手續費: ${trade.fee_usdt:.2f} (開倉)")
            print(f"   預期淨利潤: {dynamic_params['expected_net_profit_pct']:.1f}%")
            print(f"   最長持倉: {max_hold_min:.0f} 分鐘")
            
            # 🐋 v10.6 鯨魚策略提示
            if trade.is_whale_trade:
                print(f"   🐋 鯨魚模式: {trade.whale_direction}")
                print(f"      大單數量: {trade.whale_trade_count} 筆")
                print(f"      鯨魚金額: ${max(trade.whale_buy_value, trade.whale_sell_value):,.0f}")
                print(f"      預估影響: {trade.whale_estimated_impact_pct:.3%}")
                print(f"      🎯 目標價: ${trade.whale_target_price:,.2f} ({trade.whale_expected_profit_pct:+.3%})")
                if trade.whale_profit_lock_enabled:
                    print(f"      🔒 鯨魚鎖利: 已啟用 (達到目標價即平倉)")
            print(f"{'='*60}\n")
            
            # 🔊 播放進場音效
            play_sound('long_entry' if direction == 'LONG' else 'short_entry')
            
            # 🆕 發送開倉信號到真實交易系統
            if self.signal_bridge_enabled and self.signal_bridge:
                try:
                    action = SignalAction.OPEN_LONG if direction == 'LONG' else SignalAction.OPEN_SHORT
                    self.signal_bridge.send_signal(
                        action=action,
                        entry_price=entry_price,
                        quantity_btc=position_btc,
                        quantity_usdt=self.config.position_size_usdt,
                        leverage=leverage,
                        strategy_name=strategy,
                        probability=probability,
                        confidence=confidence,
                        take_profit=tp_price,
                        stop_loss=sl_price,
                        extra_data={
                            'trade_id': trade_id,
                            'entry_type': entry_type,
                            'entry_batches': entry_batches,
                            'breakeven_price': trade.breakeven_price,
                            'market_data': market_data
                        }
                    )
                    print(f"   📡 信號已發送到真實交易系統")
                except Exception as e:
                    print(f"   ⚠️ 發送信號失敗: {e}")
            
            # 🆕 dYdX 同步處理 (Strict Sync 已移至 TradeRecord 創建前)
            # 此處不再執行後置同步
            
            # 🆕 v13.9.1: 開始早期逃命追蹤 (含六維分數)
            if hasattr(self, '_parent_system') and self._parent_system:
                parent = self._parent_system
                if hasattr(parent, 'early_exit_detector') and parent.early_exit_detector:
                    try:
                        six_dim = market_data.get('six_dim', {})
                        parent.early_exit_detector.start_tracking(
                            entry_price=entry_price,
                            direction=direction,
                            entry_obi=market_data.get('obi', 0.0),
                            long_score=six_dim.get('long_score', 0.0),
                            short_score=six_dim.get('short_score', 0.0)
                        )
                    except Exception as e:
                        print(f"   ⚠️ 早期逃命追蹤啟動失敗: {e}")

            # 🔧 v14.6.10: dYdX TP/SL 已在 _dydx_open_position_v2 確認成交後立即掛單
            # 不需要在這裡重複掛單

            
            return trade
            
        except Exception as e:
            print(f"❌ 開倉失敗: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def close_position(self, reason: str, exit_price: float, force_market_close: bool = False) -> Optional[TradeRecord]:
        """
        平倉
        """
        if not self.active_trade:
            return None
        
        trade = self.active_trade
        
        # 🆕 v14.2: dYdX Sync 最小持倉時間保護
        # 確保 dYdX 有足夠時間成交 (開倉需要 5-10 秒)
        if self.dydx_sync_enabled and self.dydx_real_position:
            entry_time = datetime.fromisoformat(trade.entry_time)
            hold_seconds = (datetime.now() - entry_time).total_seconds()
            min_hold_for_dydx = 15.0  # 最少 15 秒 (給 dYdX 成交 + 緩衝)
            
            # 只有 TP/SL/強制平倉 可以繞過此限制
            reason_upper = reason.upper()
            is_forced_exit = (
                "止損" in reason
                or "止盈" in reason
                or "STOP" in reason_upper
                or "TAKE" in reason_upper
                or "PRE_TP" in reason_upper
                or "PRE_SL" in reason_upper
                or "強制" in reason
            )
            
            if hold_seconds < min_hold_for_dydx and not is_forced_exit:
                remaining = min_hold_for_dydx - hold_seconds
                print(f"⏳ [dYdX Sync] 最小持倉保護: 還需 {remaining:.1f}s (原因: {reason})")
                return None
        
        try:
            # 平倉 (反向市價單)
            if trade.direction == "LONG":
                side = 'sell'
            else:
                side = 'buy'
            
            # 真實交易或模擬
            if not self.paper_mode and self.testnet_api:
                symbol = self.config.symbol.replace('/', '')
                order = self.testnet_api.close_position(symbol)
                if not order:
                    print("❌ API 平倉失敗")
                    return None
            else:
                # Paper Trading 模擬
                
                # 🆕 5. Strict Sync Close
                if self.dydx_sync_enabled and self.dydx_api:
                    # 只有當真實持倉存在時才執行同步
                    if self.dydx_real_position:
                        print(f"🔒 [StrictSync] 準備同步平倉 ({reason})...")
                        import asyncio
                        use_ref_exit_price = bool(getattr(self.config, 'dydx_use_reference_exit_price', False))
                        
                        # 🆕 v14.6.1: 檢查是否是預掛單觸發的出場
                        # 如果是 TP/SL 預掛單觸發，dYdX 上的訂單會自動成交
                        # 不需要再下市價單，使用傳入的 exit_price (預掛價格)
                        force_market_close = bool(force_market_close)
                        is_pre_order_exit = (
                            "PRE_TP" in reason or 
                            "PRE_SL" in reason or
                            (self.pending_tp_order and "止盈" in reason) or
                            (self.pending_sl_order and "止損" in reason)
                        ) and not force_market_close
                        
                        if is_pre_order_exit:
                            # 預掛單觸發：dYdX 會自動成交
                            if not use_ref_exit_price:
                                # 🔧 v14.6.23: 查詢 dYdX API 取得實際成交價 (預掛單價格可能有滑點)
                                actual_fill_price = exit_price
                                try:
                                    # 嘗試從最近成交取得實際價格
                                    fills = asyncio.run(self.dydx_api.get_recent_fills(limit=3))
                                    if fills:
                                        # 找最近的平倉成交 (與開倉方向相反)
                                        close_side = "BUY" if self.dydx_real_position.get('side') == "SHORT" else "SELL"
                                        for fill in fills:
                                            if fill.get('side') == close_side:
                                                actual_fill_price = float(fill.get('price', exit_price))
                                                print(f"📊 [StrictSync] dYdX 實際成交價: ${actual_fill_price:,.2f}")
                                                break
                                except Exception as e:
                                    print(f"⚠️ 取得成交價失敗: {e}，使用預掛價格")
                                
                                exit_price = actual_fill_price
                            print(f"✅ [StrictSync] 預掛單出場: ${exit_price:,.2f}")
                            # 清除本地追蹤（dYdX 訂單已自動成交）
                            self.cancel_all_pending_orders(f"預掛單成交: {reason}")
                            self.dydx_real_position = None
                            try:
                                asyncio.run(self._dydx_sweep_open_orders(reason="pre_order_exit", market="BTC-USD"))
                                asyncio.run(self._log_dydx_protection_snapshot(reason="pre_order_exit"))
                            except Exception:
                                pass
                        else:
                            # 非預掛單觸發：需要下市價單
                            # (1) 🆕 v14.6: 取消所有預掛單 (TP + SL)
                            self.cancel_all_pending_orders("StrictSync 平倉")

                            # (2) 執行市價平倉
                            is_stop_loss = "止損" in reason or "STOP" in reason.upper()
                            try:
                                dydx_success, dydx_close_price = asyncio.run(self._dydx_close_position(reason, is_stop_loss))
                                
                                if not dydx_success:
                                     print(f"❌ [StrictSync] dYdX 平倉失敗 -> 暫停 Paper Close")
                                     # 🔧 v14.9.12: 平倉失敗時設置長冷卻期
                                     self._last_emergency_stop_ts = time.time() + 300  # 5 分鐘冷卻
                                     return None
                                
                                # Sync Real Price (市價會有滑點)
                                slippage = abs(dydx_close_price - exit_price)
                                slippage_pct = slippage / exit_price * 100
                                print(f"✅ [StrictSync] dYdX 市價平倉: ${dydx_close_price:,.2f}")
                                print(f"   📉 與計畫價差: ${slippage:.2f} ({slippage_pct:.3f}%)")
                                if not use_ref_exit_price:
                                    exit_price = dydx_close_price
                                
                            except Exception as e:
                                 print(f"❌ [StrictSync] 執行異常: {e}")
                                 # 🔧 v14.9.12: 異常時設置長冷卻期
                                 self._last_emergency_stop_ts = time.time() + 300  # 5 分鐘冷卻
                                 return None
                    else:
                        print(f"⚠️ [StrictSync] dYdX 無真實持倉，僅執行 Paper Close")

                order = {'id': 'PAPER', 'status': 'filled'}
            
            # 🆕 v4.1: dYdX 出場滑點模擬 (Paper Trading Only, 非 Sync 模式)
            # 🔧 v14.6.40: 若出場價為「止損線精確價」(預掛單概念)，不應再套用滑點，否則會把鎖利直接滑回虧損
            original_exit_price = exit_price
            dydx_sim = getattr(self.config, 'dydx_simulation', None)
            is_precise_stopline_exit = ("止損線" in str(reason)) and ("緊急止損" not in str(reason))
            if (
                dydx_sim
                and dydx_sim.get('enabled', False)
                and self.paper_mode
                and not self.dydx_sync_enabled
                and not is_precise_stopline_exit
            ):
                exit_slippage_pct = dydx_sim.get('exit_slippage_pct', 0.1)

                # 計算滑點價格
                # LONG 平倉: 賣出時會賣便宜 (price - slippage)
                # SHORT 平倉: 買入時會買貴 (price + slippage)
                if trade.direction == "LONG":
                    exit_price = original_exit_price * (1 - exit_slippage_pct / 100)
                else:
                    exit_price = original_exit_price * (1 + exit_slippage_pct / 100)

                slippage_cost = abs(exit_price - original_exit_price)
                print(f"   📉 dYdX 出場滑點: ${exit_price:,.2f} (滑點 ${slippage_cost:.2f} / {exit_slippage_pct}%)")
            
            # 計算盈虧
            # 🔧 v14.6.25: 修正 PnL 計算公式
            # 正確公式: PnL = BTC_size × 價差
            # position_size_usdt = BTC_size × entry_price (名義價值)
            # price_move_pct = 價差 / entry_price × 100
            # 所以: PnL = position_size_usdt × price_move_pct / 100
            #           = (BTC × entry_price) × (價差 / entry_price × 100) / 100
            #           = BTC × 價差 ✅
            
            if trade.direction == "LONG":
                price_move_pct = (exit_price - trade.entry_price) / trade.entry_price * 100
            else:
                price_move_pct = (trade.entry_price - exit_price) / trade.entry_price * 100
            
            # 使用交易記錄中的實際槓桿
            leverage = trade.actual_leverage if hasattr(trade, 'actual_leverage') and trade.actual_leverage else trade.leverage
            
            # 🔧 v14.6.25: 槓桿後盈虧% (用於顯示和保證金回報率計算)
            pnl_pct = price_move_pct * leverage  # 相對於保證金的盈虧%
            
            # 🔧 v14.6.25: 正確的 USDT 盈虧計算
            # pnl_usdt = 名義價值 × 價格移動% (不乘槓桿，因為名義價值已經是 BTC × price)
            pnl_usdt = trade.position_size_usdt * price_move_pct / 100
            
            # 手續費 (開倉 + 平倉) - 基於名義價值計算
            # - 開倉費已在 trade.fee_usdt 記錄
            # - 平倉費依原因估算：止損/強制平倉 → Taker；止盈/鎖利 → Maker(若啟用 Maker 模擬)
            maker_fee_pct = _coerce_float(getattr(self.config, 'maker_fee_pct', 0.0), default=0.0)
            taker_fee_pct = _coerce_float(getattr(self.config, 'taker_fee_pct', 0.0), default=0.0)
            r = str(reason)
            r_up = r.upper()
            is_emergency = force_market_close or ("EMERGENCY" in r_up) or ("緊急" in r)
            is_stop = ("SL" in r_up) or ("STOP" in r_up) or ("止損" in r)
            is_profit = ("TP" in r_up) or ("TAKE" in r_up) or ("PROFIT" in r_up) or ("止盈" in r) or ("鎖" in r)
            if getattr(self.config, 'taker_on_emergency_only', False):
                exit_fee_pct = taker_fee_pct if is_emergency else (maker_fee_pct if self.config.use_maker_simulation else taker_fee_pct)
            else:
                if is_stop or is_emergency:
                    exit_fee_pct = taker_fee_pct
                elif is_profit:
                    exit_fee_pct = maker_fee_pct if self.config.use_maker_simulation else taker_fee_pct
                else:
                    exit_fee_pct = taker_fee_pct

            exit_fee = trade.position_size_usdt * exit_fee_pct / 100  # 🔧 v14.6.25: 移除多餘的 leverage
            total_fee = trade.fee_usdt + exit_fee
            net_pnl = pnl_usdt - total_fee
            
            # 持倉時間
            entry_time = datetime.fromisoformat(trade.entry_time)
            hold_seconds = (datetime.now() - entry_time).total_seconds()
            
            # 更新記錄
            trade.status = reason
            trade.exit_price = exit_price
            trade.exit_time = datetime.now().isoformat()
            trade.price_move_pct = price_move_pct
            trade.pnl_pct = pnl_pct
            trade.pnl_usdt = pnl_usdt
            trade.fee_usdt = total_fee
            trade.net_pnl_usdt = net_pnl
            trade.hold_seconds = hold_seconds
            
            # 更新統計
            self.daily_pnl += net_pnl
            self.total_pnl += net_pnl
            self.current_balance += net_pnl  # 🆕 更新當前資金
            self.active_trade = None
            
            # 🆕 更新勝負統計
            if net_pnl > 0:
                self.win_count += 1
                self.consecutive_losses = 0  # 🔧 Reset consecutive losses
            else:
                self.loss_count += 1
                self.consecutive_losses += 1 # 🔧 Increment consecutive losses
                self.last_loss_time = time.time()
                print(f"   ⚠️ 連續虧損: {self.consecutive_losses} 次 (閾值: {self.config.max_consecutive_losses})")
                if self.consecutive_losses >= self.config.max_consecutive_losses:
                    self.cooldown_until = time.time() + self.config.consecutive_loss_cooldown_min * 60
                    self.consecutive_losses = 0  # 冷卻後重新計數
                    print(f"   ⏸️ 觸發冷卻 {self.config.consecutive_loss_cooldown_min} 分鐘，暫停交易至 {datetime.fromtimestamp(self.cooldown_until).strftime('%H:%M:%S')}")
            
            # 🆕 v14.9.7: 記錄完整的平倉事件到 journal (方便 debug 和分析)
            self._journal_dydx_event(
                "trade_closed",
                trade_id=trade.trade_id,
                direction=trade.direction,
                entry_price=trade.entry_price,
                exit_price=exit_price,
                price_move_pct=price_move_pct,
                pnl_pct=pnl_pct,
                pnl_usdt=pnl_usdt,
                net_pnl_usdt=net_pnl,
                fee_usdt=total_fee,
                hold_seconds=hold_seconds,
                exit_reason=reason,
                leverage=leverage,
                position_size_usdt=trade.position_size_usdt,
                position_size_btc=trade.position_size_btc,
                is_win=(net_pnl > 0),
                current_balance=self.current_balance,
                win_count=self.win_count,
                loss_count=self.loss_count,
                consecutive_losses=self.consecutive_losses,
                # 進場時的市場狀態 (回顧分析用)
                entry_obi=getattr(trade, 'obi', 0),
                entry_six_dim_long=getattr(trade, 'six_dim_long_score', 0),
                entry_six_dim_short=getattr(trade, 'six_dim_short_score', 0),
            )
            
            self._save_trades()
            
            emoji = "✅" if net_pnl > 0 else "❌"
            print(f"\n{'='*60}")
            print(f"{emoji} 平倉: {trade.trade_id}")
            print(f"   原因: {reason}")
            print(f"   進場: ${trade.entry_price:,.2f}")
            print(f"   出場: ${exit_price:,.2f}")
            print(f"   價格移動: {price_move_pct:+.3f}%")
            print(f"   槓桿盈虧: {pnl_pct:+.2f}% (${pnl_usdt:+.2f})")
            print(f"   手續費: ${total_fee:.2f}")
            print(f"   淨盈虧: ${net_pnl:+.2f}")
            print(f"   持倉時間: {hold_seconds:.0f}秒")
            print(f"   當前資金: ${self.current_balance:.2f} ({(self.current_balance/self.initial_balance-1)*100:+.2f}%)")
            print(f"{'='*60}\n")
            
            # 🔊 播放平倉音效（獲利/虧損）
            play_sound('profit_exit' if net_pnl > 0 else 'loss_exit')
            
            # 🆕 發送平倉信號到真實交易系統
            if self.signal_bridge_enabled and self.signal_bridge:
                try:
                    action = SignalAction.CLOSE_LONG if trade.direction == 'LONG' else SignalAction.CLOSE_SHORT
                    self.signal_bridge.send_signal(
                        action=action,
                        entry_price=exit_price,
                        quantity_btc=trade.position_size_btc,
                        quantity_usdt=trade.position_size_usdt,
                        leverage=leverage,
                        strategy_name=f"EXIT:{reason}",
                        probability=0.0,
                        confidence=0.0,
                        take_profit=0.0,
                        stop_loss=0.0,
                        extra_data={
                            'trade_id': trade.trade_id,
                            'exit_reason': reason,
                            'entry_price': trade.entry_price,
                            'exit_price': exit_price,
                            'paper_pnl': net_pnl,
                            'paper_pnl_pct': pnl_pct,
                            'hold_seconds': hold_seconds
                        }
                    )
                    print(f"   📡 平倉信號已發送到真實交易系統")
                except Exception as e:
                    print(f"   ⚠️ 發送平倉信號失敗: {e}")
            
            # 🆕 dYdX (Strict Sync 已移至 PnL 計算前)
            # 此處不再執行後置同步
            
            # 🆕 v13.7: 記錄到自動回測模組
            if hasattr(self, '_parent_system') and self._parent_system:
                parent = self._parent_system
                if hasattr(parent, 'auto_backtest') and parent.auto_backtest and BacktestTradeRecord:
                    try:
                        backtest_trade = BacktestTradeRecord(
                            timestamp=trade.exit_time,
                            direction=trade.direction,
                            entry_price=trade.entry_price,
                            exit_price=exit_price,
                            pnl_pct=pnl_pct,
                            pnl_usdt=net_pnl,
                            size_btc=trade.position_size_btc,
                            hold_time_sec=int(hold_seconds),
                            six_dim_score=getattr(trade, 'six_dim_score', 0),
                            win=(net_pnl > 0)
                        )
                        
                        trigger_result = parent.auto_backtest.record_trade(backtest_trade)
                        
                        if trigger_result.get('triggered'):
                            print(f"\n⚠️ 自動回測觸發！")
                            print(f"   原因: {trigger_result.get('reason')}")
                            print(f"   行動: {trigger_result.get('action')}")
                    except Exception as e:
                        print(f"   ⚠️ 記錄到自動回測模組失敗: {e}")
                
                # 🆕 v13.8: 記錄到追單保護模組
                if hasattr(parent, 'chase_protection') and parent.chase_protection:
                    try:
                        parent.chase_protection.record_trade(
                            direction=trade.direction,
                            entry_price=trade.entry_price,
                            exit_price=exit_price,
                            pnl_pct=pnl_pct,
                            is_win=(net_pnl > 0),
                            six_dim_score=getattr(trade, 'six_dim_score', 0),
                            hold_time_sec=hold_seconds,
                            market_price=trade.entry_price
                        )
                    except Exception as e:
                        print(f"   ⚠️ 記錄到追單保護模組失敗: {e}")
                
                # 🆕 v13.9: 停止早期逃命追蹤
                if hasattr(parent, 'early_exit_detector') and parent.early_exit_detector:
                    try:
                        parent.early_exit_detector.stop_tracking()
                    except Exception as e:
                        pass

            # 🧹 平倉後強制清掃 dYdX 殘留掛單（避免舊單卡住下一筆策略）
            if self.dydx_sync_enabled and self.dydx_api:
                try:
                    import asyncio
                    asyncio.run(self._dydx_sweep_open_orders(reason=f"post_trade_close:{reason}", market="BTC-USD"))
                    asyncio.run(self._log_dydx_protection_snapshot(reason=f"post_trade_close:{reason}"))
                except Exception:
                    pass
            
            return trade
            
        except Exception as e:
            print(f"❌ 平倉失敗: {e}")
            return None
    
    def check_exit_conditions(self, price_ctx: Dict, strategy_config: Dict = None, 
                               market_data: Dict = None) -> tuple[Optional[str], Dict]:
        """
        智能動態平倉檢查
        
        Args:
            price_ctx: 價格上下文 (mid/bid/ask/oracle)
            strategy_config: 動態策略配置 (從 JSON 載入)
            
        Returns:
            (平倉原因 or None, 動作詳情)
        """
        if not self.active_trade:
            return None, {}
        
        trade = self.active_trade
        action_details = {}

        # 支援舊呼叫：若傳入的是數字，視為 mid 價
        if isinstance(price_ctx, (int, float)):
            mid_price = _coerce_float(price_ctx, default=0.0)
            bid_price = mid_price
            ask_price = mid_price
            oracle_price = mid_price
        else:
            mid_price = _coerce_float(price_ctx.get('mid', 0.0), default=0.0)
            bid_price = _coerce_float(price_ctx.get('bid', 0.0), default=0.0)
            ask_price = _coerce_float(price_ctx.get('ask', 0.0), default=0.0)
            oracle_price = _coerce_float(price_ctx.get('oracle', 0.0), default=0.0)

        if mid_price <= 0:
            mid_price = bid_price if bid_price > 0 else ask_price
        if bid_price <= 0:
            bid_price = mid_price
        if ask_price <= 0:
            ask_price = mid_price
        if oracle_price <= 0:
            oracle_price = mid_price

        net_price = bid_price if trade.direction == "LONG" else ask_price
        sl_trigger_price = oracle_price
        tp_trigger_price = net_price
        current_price = mid_price
        
        # ════════════════════════════════════════════════════════════════════
        # 🆕 v14.8: 三段式出場保護 (最高優先！)
        # Phase 1 (NORMAL): Band 內，正常 limit 出場
        # Phase 2 (STAGED): Band 外 1-3 秒，分批 IOC 出場
        # Phase 3 (EMERGENCY): Band 外超過 3 秒，市價全平
        # ════════════════════════════════════════════════════════════════════
        if hasattr(self, '_parent_system') and self._parent_system:
            parent = self._parent_system
            if hasattr(parent, 'spread_guard') and parent.spread_guard:
                try:
                    # 獲取市場快照
                    snapshot = parent.spread_guard.get_market_snapshot()
                    
                    # 計算持倉入場時間
                    entry_time_dt = datetime.fromisoformat(trade.entry_time)
                    position_entry_time = entry_time_dt.timestamp()
                    
                    # 判斷出場階段
                    exit_phase, exit_params = parent.spread_guard.get_exit_phase(
                        snapshot=snapshot,
                        has_position=True,
                        position_entry_time=position_entry_time
                    )
                    
                    # 記錄出場資訊
                    action_details['exit_phase'] = exit_phase.value
                    action_details['exit_params'] = exit_params
                    
                    # 🚨 緊急出場模式 (Band 外超過 3 秒)
                    if exit_phase == ExitPhase.EMERGENCY:
                        action_details['emergency_exit'] = True
                        action_details['exit_reason'] = exit_params.get('reason', 'Band外超時')
                        print(f"🚨 [三段式出場] 緊急平倉觸發: {exit_params.get('reason')}")
                        
                        # 啟動冷卻期
                        parent.spread_guard.start_cooldown(30.0)
                        
                        return "EMERGENCY_BAND_EXIT", action_details
                    
                    # ⚠️ 分批出場模式 (Band 外 1-3 秒) - 標記但不直接觸發
                    elif exit_phase == ExitPhase.STAGED:
                        action_details['staged_exit_recommended'] = True
                        action_details['batch_pct'] = exit_params.get('batch_pct', 0.2)
                        
                except Exception as e:
                    # 三段式出場檢查失敗不應影響正常出場
                    pass
        
        # ════════════════════════════════════════════════════════════════════
        # 🆕 v10.13: 三線系統反轉平倉
        # 持多倉時空方開始累積 → 平倉
        # 持空倉時多方開始累積 → 平倉
        # 🔧 v10.19 fix8: 提高反轉閾值，避免頻繁假反轉
        # 🔧 v10.19 fix10: 增加最小持倉時間保護
        # 🔧 v10.21: 可透過卡片設定關閉 (exit_rules.three_line_reversal_enabled)
        # ════════════════════════════════════════════════════════════════════
        
        # 檢查卡片是否關閉三線反轉
        three_line_enabled = True
        if strategy_config:
            exit_rules = strategy_config.get('exit_rules', {})
            three_line_enabled = exit_rules.get('three_line_reversal_enabled', True)
        
        three_line_exit_threshold = 15.0  # 🔧 v10.20: 延長至15秒，等待更大波動
        min_hold_for_reversal = 300.0  # 🔧 v10.20: 最小持倉5分鐘 (配合市場週期 ~9分鐘)
        
        # 計算持倉時間
        entry_time = datetime.fromisoformat(trade.entry_time)
        hold_seconds = (datetime.now() - entry_time).total_seconds()
        
        # 從 WhaleTestnetTrader 獲取三線累積時間 (通過 market_data 傳遞)
        # 🔧 v10.21: 只有在 three_line_enabled=True 時才觸發
        if three_line_enabled and market_data and hold_seconds >= min_hold_for_reversal:
            long_secs = market_data.get('long_alignment_seconds', 0)
            short_secs = market_data.get('short_alignment_seconds', 0)
            
            # 🔧 v10.19 fix8: 只有在反向強勢且當前方向歸零時才觸發
            # 持多倉，但空方開始累積 (且多方已完全消失)
            if trade.direction == "LONG" and short_secs >= three_line_exit_threshold and long_secs <= 1.0:
                exit_reason = f"🔄 三線反轉: 空方累積 {short_secs:.1f}s → 平多倉"
                action_details['three_line_exit'] = True
                action_details['reverse_direction'] = 'SHORT'
                action_details['reverse_seconds'] = short_secs
                print(f"⚠️ {exit_reason}")
                return "THREE_LINE_REVERSAL", action_details
            
            # 持空倉，但多方開始累積 (且空方已完全消失)
            if trade.direction == "SHORT" and long_secs >= three_line_exit_threshold and short_secs <= 1.0:
                exit_reason = f"🔄 三線反轉: 多方累積 {long_secs:.1f}s → 平空倉"
                action_details['three_line_exit'] = True
                action_details['reverse_direction'] = 'LONG'
                action_details['reverse_seconds'] = long_secs
                print(f"⚠️ {exit_reason}")
                return "THREE_LINE_REVERSAL", action_details
        
        # ════════════════════════════════════════════════════════════════════
        # 🆕 dYdX 同步模式：使用真實入場價計算盈虧
        # 解決問題：Paper Trading 和真實交易盈虧%不同步
        # ════════════════════════════════════════════════════════════════════
        
        # 判斷使用哪個入場價
        use_real_entry = False
        real_entry_price = trade.entry_price  # 預設用 Paper
        
        # 如果有 dYdX 真實倉位，使用真實入場價
        if hasattr(self, 'dydx_sync_enabled') and self.dydx_sync_enabled:
            dydx_pos = getattr(self, 'dydx_real_position', None)
            if dydx_pos and dydx_pos.get('side') == trade.direction:
                real_entry_price = dydx_pos['entry_price']
                use_real_entry = True
        
        # 計算當前浮動盈虧 (相對於真實或虛擬進場價)
        if trade.direction == "LONG":
            price_move = (current_price - real_entry_price) / real_entry_price * 100  # mid
            net_price_move = (net_price - real_entry_price) / real_entry_price * 100  # bid
            sl_price_move = (sl_trigger_price - real_entry_price) / real_entry_price * 100  # oracle
        else:
            price_move = (real_entry_price - current_price) / real_entry_price * 100  # mid
            net_price_move = (real_entry_price - net_price) / real_entry_price * 100  # ask
            sl_price_move = (real_entry_price - sl_trigger_price) / real_entry_price * 100  # oracle
        
        # 使用動態槓桿
        leverage = trade.actual_leverage if hasattr(trade, 'actual_leverage') and trade.actual_leverage else trade.leverage
        
        # 槓桿後盈虧 (未扣手續費)
        current_pnl_pct = price_move * leverage
        
        # 🔧 v14.6.39: 修正 net_pnl_pct 計算
        # 舊邏輯有 BUG: 用 breakeven_price 做差值計算會導致一開倉就 -4.x%
        # 正確邏輯: 淨盈虧% = 毛利% - 手續費影響%
        #   手續費影響% = (開倉費率 + 平倉費率) × 槓桿
        
        # 計算手續費對 ROE 的影響
        fee_rate = self.config.taker_fee_pct  # 預設 TAKER
        if hasattr(trade, 'entry_type') and trade.entry_type == 'MAKER':
            fee_rate = self.config.maker_fee_pct
        # 費用為 0 時直接用毛利
        if fee_rate == 0 and self.config.maker_fee_pct == 0 and self.config.taker_fee_pct == 0:
            fee_impact_roe_pct = 0
        else:
            # 開倉 + 平倉手續費 (各一次)
            total_fee_rate = fee_rate * 2
            fee_mult = _fee_leverage_multiplier(self.config, leverage)
            fee_impact_roe_pct = total_fee_rate * fee_mult / 100 * 100  # 換算為 ROE%
        
        net_pnl_pct = net_price_move * leverage - fee_impact_roe_pct
        is_profitable = net_pnl_pct > 0
        
        # 更新最大浮盈/回撤 (使用淨盈虧)
        trade.max_profit_pct = max(trade.max_profit_pct, net_pnl_pct)
        trade.max_drawdown_pct = min(trade.max_drawdown_pct, net_pnl_pct)
        
        action_details = {
            'price_move_pct': price_move,
            'current_price': current_price,
            'net_price': net_price,
            'oracle_price': sl_trigger_price,
            'entry_price': real_entry_price,  # 🆕 使用真實入場價
            'use_real_entry': use_real_entry,  # 🆕 標記是否用真實入場價
            'breakeven_price': trade.breakeven_price,
            'is_profitable': is_profitable,
            'pnl_pct': current_pnl_pct,
            'net_pnl_pct': net_pnl_pct,
            'max_profit_pct': trade.max_profit_pct,
            'max_drawdown_pct': trade.max_drawdown_pct
        }
        
        # ════════════════════════════════════════════════════════════════════
        # 🆕 v13.9.1: 早期逃命檢查 (在所有止盈止損邏輯之前)
        # 目的：偵測「微利後反轉止損」的早期信號，提前止損
        # 新增：六維分數監控 (對向分數激增 / 分數差收窄)
        # ════════════════════════════════════════════════════════════════════
        if hasattr(self, '_parent_system') and self._parent_system:
            parent = self._parent_system
            if hasattr(parent, 'early_exit_detector') and parent.early_exit_detector:
                try:
                    # 更新追蹤並檢查 (含六維分數)
                    current_obi = market_data.get('obi', 0.0) if market_data else 0.0
                    six_dim = market_data.get('six_dim', {}) if market_data else {}
                    should_early_exit, exit_reason_enum, exit_msg = parent.early_exit_detector.update_and_check(
                        current_price=current_price,
                        current_obi=current_obi,
                        long_score=six_dim.get('long_score'),
                        short_score=six_dim.get('short_score')
                    )
                    
                    if should_early_exit:
                        # 只有在微利狀態才觸發早期逃命 (避免影響正常獲利的交易)
                        if 0 < net_pnl_pct < 1.0:  # 微利狀態
                            print(f"🚨 早期逃命信號: {exit_msg}")
                            action_details['early_exit'] = True
                            action_details['early_exit_reason'] = exit_reason_enum.value if exit_reason_enum else 'unknown'
                            action_details['early_exit_msg'] = exit_msg
                            return "EARLY_EXIT", action_details
                except Exception as e:
                    pass  # 早期逃命檢查失敗不應影響正常交易
        
        # 載入動態策略配置
        if strategy_config is None:
            strategy_config = load_trading_strategy()
        
        profit_config = strategy_config.get('profit_strategy', {})
        sl_config = strategy_config.get('stop_loss_strategy', {})
        
        # ============================================================
        # 🎯 v10.9 兩階段止盈止損系統 (最高優先)
        # 解決問題：33勝21負卻虧77%
        # ============================================================
        if self.config.two_phase_exit_enabled:
            # 計算持倉時間
            entry_time = datetime.fromisoformat(trade.entry_time)
            hold_minutes = (datetime.now() - entry_time).total_seconds() / 60
            
            # 獲取兩階段管理器的判斷 (從 WhaleTestnetTrader 傳入)
            two_phase_mgr = getattr(self, '_two_phase_exit_manager', None)
            if two_phase_mgr is None:
                # 創建臨時管理器
                two_phase_mgr = TwoPhaseExitManager(self.config)
            
            two_phase_result = two_phase_mgr.check_exit(
                trade=trade,
                net_pnl_pct=net_pnl_pct,
                max_net_pnl_pct=trade.max_profit_pct,
                hold_time_min=hold_minutes,
                market_data=market_data  # 🆕 傳入市場數據用於動態調整
            )
            
            # 保存階段資訊供顯示用
            action_details['two_phase_info'] = two_phase_result.get('phase_info', {})
            
            if two_phase_result.get('should_exit', False):
                exit_reason = two_phase_result.get('reason', '兩階段止盈止損')
                action_details['exit_reason'] = exit_reason
                print(f"🎯 兩階段系統觸發: {exit_reason}")
                return "CLOSED_TWO_PHASE", action_details
        
        # ============================================================
        # 🐋 v10.6 鯨魚策略專屬鎖利 (優先執行)
        # ============================================================
        if hasattr(trade, 'is_whale_trade') and trade.is_whale_trade and trade.whale_profit_lock_enabled:
            # 載入鯨魚鎖利配置
            ctx_cfg = load_ctx_strategy_config()
            whale_cfg = ctx_cfg.get('whale_strategy', {})
            whale_lock_cfg = whale_cfg.get('profit_lock', {})
            
            initial_lock_pct = whale_lock_cfg.get('initial_lock_pct', 0.05)
            trailing_pct = whale_lock_cfg.get('trailing_pct', 0.03)
            
            # 鯨魚策略的預期獲利目標
            expected_profit = trade.whale_expected_profit_pct * 100 * leverage  # 轉換為槓桿後%
            
            # 🐋 鯨魚鎖利邏輯
            # 當達到初始鎖利門檻時，啟動追蹤止損
            if net_pnl_pct >= initial_lock_pct:
                # 計算鯨魚追蹤止損價格
                trailing_sl_pct = net_pnl_pct - trailing_pct  # 保留 trailing_pct 的獲利
                
                if trailing_sl_pct > 0:
                    # 計算對應的價格
                    price_offset = (trailing_sl_pct / leverage / 100) * trade.entry_price
                    
                    if trade.direction == "LONG":
                        whale_sl_price = trade.breakeven_price + price_offset
                        if whale_sl_price > trade.stop_loss_price:
                            trade.stop_loss_price = whale_sl_price
                            action_details['whale_trailing_active'] = True
                            action_details['whale_locked_pct'] = trailing_sl_pct
                    else:
                        whale_sl_price = trade.breakeven_price - price_offset
                        if whale_sl_price < trade.stop_loss_price or trade.stop_loss_price <= 0:
                            trade.stop_loss_price = whale_sl_price
                            action_details['whale_trailing_active'] = True
                            action_details['whale_locked_pct'] = trailing_sl_pct
                    
                    # 鯨魚鎖利更新 (debug)
                    pass
            
            # ═══════════════════════════════════════════════════════════
            # 🐋 v10.6 鯨魚目標價平倉 (最高優先)
            # 達到預估目標價時立即平倉，最大化鎖利
            # ═══════════════════════════════════════════════════════════
            whale_target_price = getattr(trade, 'whale_target_price', 0)
            
            if whale_target_price > 0:
                # 檢查是否達到目標價
                if trade.direction == "LONG" and tp_trigger_price >= whale_target_price:
                    action_details['whale_target_reached'] = True
                    action_details['whale_target_price'] = whale_target_price
                    action_details['exit_reason'] = f'🐋🎯 鯨魚目標達成! 價格${tp_trigger_price:,.2f} ≥ 目標${whale_target_price:,.2f}'
                    self.logger.info(f"🐋🎯 鯨魚目標價達成: {tp_trigger_price:,.2f} ≥ {whale_target_price:,.2f}")
                    return "CLOSED_WHALE_TARGET", action_details
                
                elif trade.direction == "SHORT" and tp_trigger_price <= whale_target_price:
                    action_details['whale_target_reached'] = True
                    action_details['whale_target_price'] = whale_target_price
                    action_details['exit_reason'] = f'🐋🎯 鯨魚目標達成! 價格${tp_trigger_price:,.2f} ≤ 目標${whale_target_price:,.2f}'
                    self.logger.info(f"🐋🎯 鯨魚目標價達成: {tp_trigger_price:,.2f} ≤ {whale_target_price:,.2f}")
                    return "CLOSED_WHALE_TARGET", action_details
                
                # 計算距離目標的進度
                if trade.direction == "LONG":
                    progress_to_target = (tp_trigger_price - trade.entry_price) / (whale_target_price - trade.entry_price) * 100
                else:
                    progress_to_target = (trade.entry_price - tp_trigger_price) / (trade.entry_price - whale_target_price) * 100
                
                action_details['whale_target_progress'] = progress_to_target
                
                # 達到 80% 目標時啟動緊密追蹤
                if progress_to_target >= 80:
                    tight_trailing_pct = 0.02  # 0.02% 緊密追蹤
                    price_offset = (tight_trailing_pct / 100) * current_price
                    
                    if trade.direction == "LONG":
                        tight_sl = current_price - price_offset
                        if tight_sl > trade.stop_loss_price:
                            trade.stop_loss_price = tight_sl
                            action_details['whale_tight_trailing'] = True
                    else:
                        tight_sl = current_price + price_offset
                        if tight_sl < trade.stop_loss_price or trade.stop_loss_price <= 0:
                            trade.stop_loss_price = tight_sl
                            action_details['whale_tight_trailing'] = True
            
            # 🐋 備用: 基於淨盈虧的鎖利 (如果沒有目標價)
            else:
                expected_profit = trade.whale_expected_profit_pct * 100 * leverage
                if expected_profit > 0 and net_pnl_pct >= expected_profit * 0.8:
                    action_details['whale_target_reached'] = True
                    action_details['whale_expected_profit'] = expected_profit
                    action_details['exit_reason'] = f'🐋 鯨魚目標達成 ({net_pnl_pct:.1f}% ≥ {expected_profit * 0.8:.1f}%)'
                    return "CLOSED_WHALE_TARGET", action_details
        
        # ============================================================
        # 🆕 v8.0 MTF 緊急止損 (突發反轉事件)
        # ============================================================
        # 這是 MTF-First 模式的核心保護：當 MTF 趨勢突然反轉時緊急出場
        mtf_emergency_stop_pct = self.config.mtf_emergency_stop_pct or 0.15
        
        # 檢查緊急虧損（無論盈虧，價格快速反向移動就觸發）
        if mtf_emergency_stop_pct and abs(price_move) >= mtf_emergency_stop_pct and price_move < 0:
            action_details['mtf_emergency_stop'] = True
            action_details['price_move_pct'] = price_move
            action_details['emergency_threshold'] = mtf_emergency_stop_pct
            action_details['exit_reason'] = f'🚨 緊急止損 (價格反向 {price_move:.2f}% > {mtf_emergency_stop_pct}%)'
            return "CLOSED_EMERGENCY", action_details
        
        # ============================================================
        # 動態止損檢查
        # ============================================================
        current_sl_pct = sl_config.get('initial_stop_loss_pct', 0.10)
        
        # 時間收緊止損
        entry_time = datetime.fromisoformat(trade.entry_time)
        hold_minutes = (datetime.now() - entry_time).total_seconds() / 60
        
        time_rules = sl_config.get('time_based_tightening', {}).get('rules', [])
        for rule in time_rules:
            if hold_minutes >= rule['after_minutes']:
                current_sl_pct = min(current_sl_pct, rule['tighten_to_pct'])
        
        # ============================================================
        # 🆕 v2.0 階梯鎖盈 - 基於淨盈虧動態調整止損
        # ============================================================
        stepped_lock_config = profit_config.get('stepped_lock', {})
        dynamic_sl_config = sl_config.get('dynamic_based_on_net_pnl', {})
        
        if stepped_lock_config.get('enabled', False) or dynamic_sl_config.get('enabled', False):
            # 使用淨盈虧來決定鎖盈
            lock_rules = dynamic_sl_config.get('rules', []) or stepped_lock_config.get('stages', [])
            
            for rule in reversed(lock_rules):  # 從高到低檢查
                threshold = rule.get('net_pnl_above') or rule.get('net_pnl_range', [0, 0])[0]
                lock_to = rule.get('set_sl_to_net_pnl') or rule.get('lock_profit_pct', 0)
                
                if net_pnl_pct >= threshold:
                    # 計算鎖定價格
                    # lock_to 是淨盈虧%，需要轉換為價格
                    # 淨盈虧% = (exit_price - breakeven_price) / entry_price * 100 * leverage
                    # lock_price = breakeven_price + (lock_to / leverage / 100) * entry_price
                    
                    if trade.breakeven_price > 0:
                        price_offset = (lock_to / leverage / 100) * trade.entry_price
                        if trade.direction == "LONG":
                            lock_price = trade.breakeven_price + price_offset
                            if lock_price > trade.stop_loss_price:
                                trade.stop_loss_price = lock_price
                                action_details['locked_profit_pct'] = lock_to
                                action_details['lock_stage'] = rule.get('name', f'Lock {lock_to}%')
                                action_details['trailing_stop_active'] = True
                        else:
                            lock_price = trade.breakeven_price - price_offset
                            if lock_price < trade.stop_loss_price:
                                trade.stop_loss_price = lock_price
                                action_details['locked_profit_pct'] = lock_to
                                action_details['lock_stage'] = rule.get('name', f'Lock {lock_to}%')
                                action_details['trailing_stop_active'] = True
                    break  # 只執行最高匹配的規則
        
        # 檢查止損 (包含鎖盈觸發)
        if sl_price_move <= -current_sl_pct:
            action_details['stop_loss_pct'] = current_sl_pct
            return "CLOSED_SL", action_details
        
        # 🆕 v3.0 檢查鎖盈止損是否被觸發（包括保本鎖定）
        if action_details.get('trailing_stop_active', False) and trade.stop_loss_price > 0:
            if trade.direction == "LONG" and sl_trigger_price <= trade.stop_loss_price:
                locked_pct = action_details.get('locked_profit_pct', 0)
                if locked_pct > 0:
                    action_details['exit_reason'] = f'鎖盈觸發 (保住 {locked_pct:.1f}%)'
                else:
                    action_details['exit_reason'] = '保本止損觸發 (0% 鎖定)'
                return "CLOSED_LOCK_PROFIT", action_details
            elif trade.direction == "SHORT" and sl_trigger_price >= trade.stop_loss_price:
                locked_pct = action_details.get('locked_profit_pct', 0)
                if locked_pct > 0:
                    action_details['exit_reason'] = f'鎖盈觸發 (保住 {locked_pct:.1f}%)'
                else:
                    action_details['exit_reason'] = '保本止損觸發 (0% 鎖定)'
                return "CLOSED_LOCK_PROFIT", action_details
        
        # ============================================================
        # 動態止盈策略 (舊邏輯保留作為備用)
        # ============================================================
        dynamic_config = profit_config.get('dynamic_trailing', {})
        
        if dynamic_config.get('enabled', False):
            stages = dynamic_config.get('stages', [])
            
            for stage in stages:
                profit_range = stage.get('profit_range', [0, 0])
                
                # 檢查是否在此獲利階段
                if profit_range[0] <= price_move < profit_range[1]:
                    action = stage.get('action')
                    action_details['current_stage'] = stage.get('name')
                    action_details['stage_action'] = action
                    
                    if action == 'move_sl_to_breakeven':
                        # 止損移至開倉價 (保本)
                        if trade.direction == "LONG":
                            trade.stop_loss_price = trade.entry_price * 1.001  # 加一點 buffer
                        else:
                            trade.stop_loss_price = trade.entry_price * 0.999
                        action_details['new_stop_loss'] = trade.stop_loss_price
                        
                    elif action == 'partial_close':
                        # 部分平倉 (這裡只設標記，實際執行在外部)
                        close_pct = stage.get('close_percent', 50)
                        action_details['partial_close_percent'] = close_pct
                        action_details['suggest_partial_close'] = True
                        
                    elif action in ['tight_trailing', 'very_tight_trailing']:
                        # 追蹤止盈
                        trailing_pct = stage.get('trailing_pct', 0.02)
                        
                        # 計算追蹤止損價
                        if trade.direction == "LONG":
                            new_sl = current_price * (1 - trailing_pct / 100)
                            if new_sl > trade.stop_loss_price:
                                trade.stop_loss_price = new_sl
                        else:
                            new_sl = current_price * (1 + trailing_pct / 100)
                            if new_sl < trade.stop_loss_price:
                                trade.stop_loss_price = new_sl
                        
                        action_details['trailing_pct'] = trailing_pct
                        action_details['new_stop_loss'] = trade.stop_loss_price
                    
                    break  # 只執行一個階段
        
        # ============================================================
        # 🆕 v4.0 智能動態止盈 (基於觀察: 毛利+6% 淨利+3% 最佳)
        # ============================================================
        # 注意: 這個方法需要從 WhaleTestnetSystem 調用，而非 TestnetTrader
        # 因為 smart_exit_info 需要在外部計算後傳入
        smart_exit_info = action_details.get('smart_exit_info', {})
        if smart_exit_info.get('should_exit', False):
            action_details['smart_exit_triggered'] = True
            action_details['smart_exit_reason'] = smart_exit_info.get('exit_reason', '智能止盈')
            return "CLOSED_SMART_TP", action_details
        
        # ============================================================
        # 固定止盈檢查 (作為最後保護)
        # ============================================================
        max_target = profit_config.get('fixed', {}).get('target_profit_pct', 0.20)
        if net_price_move >= max_target:
            return "CLOSED_TP", action_details
        
        # ============================================================
        # 🆕 v5.9 無動能快速止損 (基於數據分析)
        # 觀察：進場後從未漲超過 1% 的交易，92% 最終虧損
        # 策略：如果 3 分鐘內沒有漲超過 1%，且已虧損 5%，則快速止損
        # ============================================================
        no_momentum_config = strategy_config.get('no_momentum_early_exit', {
            'enabled': True,
            'check_after_minutes': 3,      # 進場 3 分鐘後開始檢查
            'min_profit_threshold': 1.0,   # 最大獲利需超過 1%
            'loss_trigger': -5.0,          # 當前虧損達 5% 時觸發
        })
        
        if no_momentum_config.get('enabled', True):
            check_after = no_momentum_config.get('check_after_minutes', 3)
            min_profit_needed = no_momentum_config.get('min_profit_threshold', 1.0)
            loss_trigger = no_momentum_config.get('loss_trigger', -5.0)
            
            # 條件檢查：
            # 1. 持倉超過指定時間
            # 2. 最大獲利從未超過閾值 (表示進場方向錯誤)
            # 3. 當前處於虧損狀態
            if hold_minutes >= check_after:
                if trade.max_profit_pct < min_profit_needed and net_pnl_pct <= loss_trigger:
                    action_details['no_momentum_exit'] = True
                    action_details['hold_minutes'] = hold_minutes
                    action_details['max_profit_achieved'] = trade.max_profit_pct
                    action_details['current_loss'] = net_pnl_pct
                    action_details['exit_reason'] = f'無動能止損 (持倉{hold_minutes:.0f}分, 最高僅+{trade.max_profit_pct:.1f}%, 當前{net_pnl_pct:.1f}%)'
                    return "CLOSED_NO_MOMENTUM", action_details
        
        # ============================================================
        # 趨勢反轉檢查 (保護已獲利的部位)
        # 🆕 v5.9.1 修改: 曾漲超過 4% 後回撤到 0% 即觸發保護
        # ============================================================
        reversal_config = strategy_config.get('trend_reversal_handling', {})
        # v5.9.1: 強制啟用先漲保護
        if True:  # 始終檢查
            # 🆕 v5.9.1: 先漲保護
            # 若曾漲超過 4%，但當前虧損，則在 0% 附近保本出場
            profit_protection_trigger = 4.0  # 曾漲超過 4% 啟動保護
            protection_exit_threshold = 0.5  # 回撤到 0.5% 以下就出場
            
            if trade.max_profit_pct >= profit_protection_trigger:
                if net_pnl_pct <= protection_exit_threshold:
                    action_details['profit_protection_exit'] = True
                    action_details['max_profit_achieved'] = trade.max_profit_pct
                    action_details['current_pnl'] = net_pnl_pct
                    action_details['exit_reason'] = f'先漲保護 (曾+{trade.max_profit_pct:.1f}%, 現{net_pnl_pct:.1f}%)'
                    return "CLOSED_PROFIT_PROTECTION", action_details
            
            # 原有的反轉保護邏輯 (作為備用)
            if reversal_config.get('enabled', False):
                min_profit_to_protect = 3.0  # 至少要有 3% 獲利才啟動保護
                reversal_threshold_pct = reversal_config.get('reversal_threshold_pct', 2.0)  # 回撤 2% 才平倉
                
                if trade.max_profit_pct >= min_profit_to_protect:
                    drawdown_from_peak = trade.max_profit_pct - current_pnl_pct
                    # 當回撤超過最大獲利的 60% 或絕對值超過閾值時，才觸發保護
                    if drawdown_from_peak >= max(reversal_threshold_pct, trade.max_profit_pct * 0.6):
                        # 且只有當剩餘獲利 < 1% 時才強制平倉 (確保至少還有獲利)
                        if current_pnl_pct < 1.0:
                            action_details['reversal_detected'] = True
                            action_details['drawdown_from_peak'] = drawdown_from_peak
                            action_details['remaining_profit_pct'] = current_pnl_pct
                            return "CLOSED_REVERSAL", action_details
        
        # ============================================================
        # 持倉時間限制 (使用動態參數)
        # ============================================================
        max_hold = trade.actual_max_hold_min if hasattr(trade, 'actual_max_hold_min') and trade.actual_max_hold_min else self.config.max_hold_minutes
        if hold_minutes >= max_hold:
            action_details['hold_minutes'] = hold_minutes
            action_details['max_hold_minutes'] = max_hold
            return "CLOSED_TIMEOUT", action_details
        
        return None, action_details
    
    def get_summary(self) -> Dict:
        """獲取統計摘要"""
        # 🔧 v13.6.1: 無論是否有交易，都計算運行時間
        runtime = datetime.now() - self.session_start_time
        runtime_str = f"{int(runtime.total_seconds() // 3600)}h {int((runtime.total_seconds() % 3600) // 60)}m"
        
        if not self.trades:
            return {
                "message": "尚無交易",
                "runtime": runtime_str,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "total_fees": 0,
                "avg_hold_seconds": 0,
                "best_trade": 0,
                "worst_trade": 0,
                "active_position": None,
                "daily_trades": self.daily_trades,
                "daily_pnl": self.daily_pnl,
                "initial_balance": self.initial_balance,
                "current_balance": self.current_balance,
                "profit_pct": 0,
            }
        
        closed = [t for t in self.trades if t.status != "OPEN"]
        wins = [t for t in closed if t.net_pnl_usdt > 0]
        losses = [t for t in closed if t.net_pnl_usdt <= 0]
        
        return {
            "total_trades": len(closed),
            "wins": self.win_count,
            "losses": self.loss_count,
            "win_rate": self.win_count / len(closed) if closed else 0,
            "total_pnl": self.total_pnl,
            "total_fees": sum(t.fee_usdt for t in closed),
            "avg_hold_seconds": sum(t.hold_seconds for t in closed) / len(closed) if closed else 0,
            "best_trade": max((t.net_pnl_usdt for t in closed), default=0),
            "worst_trade": min((t.net_pnl_usdt for t in closed), default=0),
            "active_position": self.active_trade.trade_id if self.active_trade else None,
            "daily_trades": self.daily_trades,
            "daily_pnl": self.daily_pnl,
            # 🆕 新增資金追蹤
            "initial_balance": self.initial_balance,
            "current_balance": self.current_balance,
            "profit_pct": (self.current_balance / self.initial_balance - 1) * 100,
            "runtime": runtime_str,
        }
    
    def get_dydx_real_stats(self) -> Dict:
        """
        🆕 v14.6.25: 從 dYdX API 獲取真實交易統計
        
        這是最準確的統計，直接從交易所 API 計算
        """
        if not self.dydx_sync_enabled or not self.dydx_api:
            return None
        
        try:
            import asyncio
            import aiohttp
            import os
            
            address = os.getenv('DYDX_ADDRESS')
            if not address:
                return None
            
            base_url = "https://indexer.dydx.trade/v4"
            
            async def fetch_stats():
                async with aiohttp.ClientSession() as session:
                    # 獲取帳戶
                    async with session.get(f"{base_url}/addresses/{address}/subaccountNumber/0") as resp:
                        if resp.status != 200:
                            return None
                        data = await resp.json()
                        account = data.get('subaccount', {})
                        equity = float(account.get('equity', 0))
                    
                    # 獲取成交紀錄
                    async with session.get(f"{base_url}/fills?address={address}&subaccountNumber=0&limit=100") as resp:
                        if resp.status != 200:
                            return None
                        data = await resp.json()
                        fills = data.get('fills', [])
                    
                    # 🆕 v14.6.35: 只計算本次運行後的交易
                    session_start = self._session_start_time
                    fills = [f for f in fills if f.get('createdAt', '') >= session_start]
                    
                    if not fills:
                        return {
                            'equity': equity,
                            'total_trades': 0,
                            'wins': 0,
                            'losses': 0,
                            'win_rate': 0,
                            'total_pnl': 0,
                            'avg_pnl': 0,
                        }
                    
                    # 計算交易統計
                    fills = sorted(fills, key=lambda x: x.get('createdAt', ''))
                    trades = []
                    current_trade = None
                    
                    for fill in fills:
                        side = fill.get('side', '')
                        size = float(fill.get('size', 0))
                        price = float(fill.get('price', 0))
                        
                        if current_trade is None:
                            current_trade = {'entry_side': side, 'entry_price': price, 'size': size}
                        else:
                            if (current_trade['entry_side'] == 'BUY' and side == 'SELL') or \
                               (current_trade['entry_side'] == 'SELL' and side == 'BUY'):
                                if current_trade['entry_side'] == 'BUY':
                                    pnl = (price - current_trade['entry_price']) * size
                                else:
                                    pnl = (current_trade['entry_price'] - price) * size
                                trades.append({'pnl': pnl})
                                current_trade = None
                            else:
                                current_trade = {'entry_side': side, 'entry_price': price, 'size': size}
                    
                    wins = [t for t in trades if t['pnl'] > 0]
                    losses = [t for t in trades if t['pnl'] <= 0]
                    total_pnl = sum(t['pnl'] for t in trades)
                    
                    return {
                        'equity': equity,
                        'total_trades': len(trades),
                        'wins': len(wins),
                        'losses': len(losses),
                        'win_rate': len(wins) / len(trades) * 100 if trades else 0,
                        'total_pnl': total_pnl,
                        'avg_pnl': total_pnl / len(trades) if trades else 0,
                        'best_trade': max((t['pnl'] for t in trades), default=0),
                        'worst_trade': min((t['pnl'] for t in trades), default=0),
                    }
            
            return asyncio.run(fetch_stats())
        except Exception as e:
            print(f"⚠️ 獲取 dYdX 真實統計失敗: {e}")
            return None


# ============================================================
# 主交易系統
# ============================================================

class WhaleTestnetSystem:
    """
    整合系統：dYdX WebSocket + 主力分析 + 交易執行
    
    🆕 v12.9: 改用 dYdX 作為主要數據源
    - 價格數據: dYdX Indexer API
    - 訂單簿: dYdX WebSocket
    - 交易數據: dYdX REST + WebSocket
    """
    
    def __init__(self, config: TradingConfig = None):
        self.config = config or TradingConfig()
        
        # 初始化組件
        # 初始化組件
        # 🆕 v13.2: Hybrid Strategy -使用 Binance WebSocket 作為主要訊號源 (Brain)
        self.binance_ws = BinanceWebSocket(symbol="btcusdt", use_testnet=False)
        # 🆕 v12.9: dYdX WebSocket 作為執行數據源 (Hands)
        self.ws = DydxWebSocket(symbol="BTC-USD", network="mainnet")
        self.trader = TestnetTrader(self.config)
        self.trader._parent_system = self  # 🆕 v13.7: 連接到父系統 (用於自動回測模組)
        
        # 🆕 v14.7: 幣安-dYdX 價差保護系統
        self.spread_guard = BinanceDydxSpreadGuard(self.config)
        
        # 🆕 dYdX Integration (Sync Wrapper) - 用於交易執行
        self.dydx: Optional[DydxTrader] = None
        if DYDX_AVAILABLE and not self._use_binance_paper_source():
            try:
                print(f"🔗 正在連接到 dYdX...")
                self.dydx = DydxTrader()
                asyncio.run(self.dydx.connect())
                print(f"✅ dYdX 連接成功 (數據源 + 交易執行)")
            except Exception as e:
                print(f"⚠️ dYdX 連接失敗: {e}")
                self.dydx = None
        
        # Testnet 價格緩存
        self._testnet_price: float = 0.0
        self._testnet_price_time: float = 0
        self._testnet_data_cache: Dict = {}
        
        # 偵測器
        if DETECTOR_AVAILABLE:
            self.detector = WhaleStrategyDetectorV4()
        else:
            self.detector = None
        
        # 🆕 MTF 多時間框架分析器 (使用 dYdX 數據)
        self.mtf_analyzer = None
        self.mtf_enabled = True  # 預設啟用
        if MTF_AVAILABLE and self.mtf_enabled:
            try:
                self.mtf_analyzer = MultiTimeframeAnalyzer(symbol="BTC-USD", enabled=True)
                print("📊 MTF 多時間框架分析器已啟用 (15m/1h/4h) - 數據源: Binance Futures (Brain)")
            except Exception as e:
                print(f"⚠️ MTF 分析器啟動失敗: {e}")
                self.mtf_analyzer = None
        
        # 狀態
        self.running = False
        self.last_analysis_time = 0
        self.last_strategy_analysis_time = 0  # 🆕 策略分析時間（每 30 秒）
        self.iteration = 0
        
        # 市場數據
        self.market_data = {}
        self.cached_strategy_data = {}  # 🆕 緩存的策略分析結果
        
        # 🆕 信號穩定性追蹤 (防止被洗)
        self.signal_history: List[Dict] = []  # 最近 N 秒的信號記錄
        self.confirmed_signal: Optional[Dict] = None  # 已確認的穩定信號
        self.signal_confirm_start: float = 0  # 信號確認開始時間
        
        # 🆕 v2.0 雙週期策略分析
        self.fast_strategy_history: List[Dict] = []  # 🆕 v10.10: 快線: 最近 5 秒
        self.medium_strategy_history: List[Dict] = []  # 🆕 v10.10: 中線: 最近 30 秒
        self.slow_strategy_history: List[Dict] = []  # 慢線: 最近 5 分鐘
        self.last_fast_analysis_time: float = 0  # 🆕 v10.10: 上次快線分析時間
        self.fast_signal_cache: Dict = {}  # 🆕 v10.10: 快線信號緩存
        self.price_history: deque = deque()  # 價格歷史，用於噪音計算
        
        # 🆕 v10.12: 多空雙進度條競爭模式
        self.long_alignment_start: float = 0  # 多方對齊開始時間
        self.long_alignment_seconds: float = 0  # 多方累積秒數
        self.short_alignment_start: float = 0  # 空方對齊開始時間  
        self.short_alignment_seconds: float = 0  # 空方累積秒數
        self.min_alignment_seconds: float = 30.0  # 🔧 v14.7: 延長至30秒，確保信號穩定
        
        # 🆕 v2.0 策略狀態追蹤 (Hysteresis)
        self.current_regime: Optional[str] = None     # 當前確認的主力狀態
        self.pending_regime: Optional[str] = None     # 等待確認的新狀態
        self.regime_confirm_count: int = 0            # 連續確認次數
        self.regime_history: List[Dict] = []          # 狀態歷史記錄
        self.last_regime_change: float = 0            # 上次狀態切換時間
        
        # 🆕 v3.0 反轉策略追蹤
        self.reversal_config = self._load_reversal_config()
        self.market_regime: str = "NORMAL"            # NORMAL 或 REVERSAL
        self.consecutive_losses: int = 0              # 連續虧損次數
        self.consecutive_wins: int = 0                # 連續獲利次數
        self.reversal_trade_history: List[Dict] = []  # 反轉交易記錄
        self.cooldown_until: float = 0                # 連續虧損冷卻截止時間
        self.last_loss_time: float = 0                # 最近一次虧損時間戳
        
        # 🆕 v4.0 動態止盈系統
        self.trade_exit_history: List[Dict] = []      # 出場歷史 (用於分析最佳止盈點)
        
        # 🆕 v10.9 兩階段止盈止損系統
        self.two_phase_exit = TwoPhaseExitManager(self.config) if self.config.two_phase_exit_enabled else None
        if self.two_phase_exit:
            print("🎯 v10.9 兩階段止盈止損系統已啟用")
            print(f"   第一階段: 嚴格止損 -{self.config.phase1_strict_stop_loss_pct}% | 目標 +{self.config.phase1_target_pct}%")
            print(f"   第二階段: 追蹤鎖利 (回撤 {self.config.phase2_trailing_offset_pct}%)")
        
        # TensorFlow 訓練資料
        self.training_records: List[Dict] = []
        self.training_data_dir = Path("logs/whale_training")
        self.training_data_dir.mkdir(parents=True, exist_ok=True)
        self.training_file = self.training_data_dir / f"training_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        # 日誌 (必須在其他使用 logger 的方法之前初始化)
        self._setup_logging()
        
        # 🆕 v4.0 載入動態止盈配置 (需要 logger)
        self.dynamic_profit_config = self._load_dynamic_profit_config()
        
        # 🆕 v5: 初始化自適應進化系統 (auto_optimized)
        # 只有使用 random_entry_smart_exit_v5 卡片時才會執行
        self._init_auto_optimize_v5()
        
        # 🆕 v13.6: 統一回測數據收集器
        self.backtest_collector = None
        if BACKTEST_COLLECTOR_AVAILABLE and BacktestDataCollector:
            try:
                # 獲取當前使用的卡片名稱作為 session_id
                card_name = getattr(self.config, 'trading_card', 'default')
                session_id = f"{card_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                self.backtest_collector = BacktestDataCollector(
                    session_id=session_id,
                    data_dir="data/backtest_sessions",
                    auto_save=True,
                    compress=True
                )
                print(f"📊 v13.6 回測數據收集器已啟用")
                print(f"   存儲路徑: {self.backtest_collector.session_dir}")
            except Exception as e:
                print(f"⚠️ 回測數據收集器初始化失敗: {e}")
                self.backtest_collector = None
        
        # 🆕 v13.7: 自動回測模組 (虧損 25% 觸發)
        # 🔧 v14.17: 允許由卡片關閉 auto_backtest_integration.enabled
        self.auto_backtest = None
        auto_backtest_cfg = getattr(self.config, "auto_backtest_integration", None)
        auto_backtest_enabled = True
        if isinstance(auto_backtest_cfg, dict):
            auto_backtest_enabled = auto_backtest_cfg.get("enabled", True)

        if auto_backtest_enabled and AUTO_BACKTEST_AVAILABLE and create_auto_backtest_module:
            try:
                self.auto_backtest = create_auto_backtest_module(
                    loss_threshold=25.0,           # 累計虧損 25% 觸發
                    consecutive_loss_limit=5,      # 連續虧損 5 次觸發
                    auto_backtest_hours=24         # 每 24 小時自動回測
                )
                # 設定回調
                self.auto_backtest.on_trigger_backtest = self._on_backtest_triggered
                self.auto_backtest.on_mode_change = self._on_trading_mode_changed
                self.auto_backtest.on_new_card_ready = self._on_new_card_ready
                
                # 設定初始模式
                if self.config.paper_mode:
                    self.auto_backtest.set_mode(TradingMode.PAPER)
                else:
                    self.auto_backtest.set_mode(TradingMode.REAL)
                
                print(f"🔄 v13.7 自動回測模組已啟用")
                print(f"   觸發條件: 累計虧損 ≥25% 或 連續虧損 ≥5 次")
                print(f"   行為: 暫停真實交易，保留虛擬交易，自動回測分析")
            except Exception as e:
                print(f"⚠️ 自動回測模組初始化失敗: {e}")
                self.auto_backtest = None
        elif not auto_backtest_enabled:
            print("🔕 v13.7 自動回測模組已關閉 (卡片設定: auto_backtest_integration.enabled=false)")
        
        # 🆕 v13.8: 追單保護模組
        self.chase_protection = None
        if CHASE_PROTECTION_AVAILABLE and create_chase_protection:
            try:
                # 🆕 v14.10: 每次啟動自動清除舊 session 的追單保護狀態
                chase_state_file = Path("data/chase_protection_state.json")
                if chase_state_file.exists():
                    chase_state_file.unlink()
                    print(f"🔄 已清除舊的追單保護狀態 (新 session 開始)")
                
                # 從卡片配置讀取追單保護設定
                chase_config = getattr(self.config, 'chase_protection', {})
                # 🎲 檢查是否啟用 (支援隨機進場模式)
                chase_enabled = chase_config.get('enabled', True)
                if chase_enabled and not getattr(self.config, 'random_entry_mode', False):
                    self.chase_protection = create_chase_protection(
                        same_direction_cooldown_sec=chase_config.get('same_direction_cooldown_sec', 120.0),
                        max_consecutive_same_direction=chase_config.get('max_consecutive_same_direction', 2),
                        price_move_block_pct=chase_config.get('price_move_block_pct', 1.0),
                        strong_signal_bypass=chase_config.get('strong_signal_bypass', False),
                        strong_signal_min_score=chase_config.get('strong_signal_min_score', 12)
                    )
                    print(f"🛡️ v13.8 追單保護模組已啟用")
                    print(f"   同方向冷卻: {chase_config.get('same_direction_cooldown_sec', 120)}秒")
                    print(f"   最大連續同向: {chase_config.get('max_consecutive_same_direction', 2)} 筆")
                    print(f"   價格移動阻擋: {chase_config.get('price_move_block_pct', 1.0)}%")
                    if chase_config.get('strong_signal_bypass', False):
                        print(f"   ⚡ 強信號繞過: 啟用 (≥{chase_config.get('strong_signal_min_score', 12)}分)")
                else:
                    print(f"🎲 追單保護已關閉 (隨機進場模式或 enabled=false)")
            except Exception as e:
                print(f"⚠️ 追單保護模組初始化失敗: {e}")
                self.chase_protection = None
        
        # 🆕 v13.9: 早期逃命偵測器 (微利後反轉止損優化)
        self.early_exit_detector = None
        if EARLY_EXIT_AVAILABLE and create_early_exit_detector:
            try:
                # 從卡片配置讀取早期逃命設定
                early_exit_config = getattr(self.config, 'early_exit', {})
                self.early_exit_detector = create_early_exit_detector(
                    profit_drawdown_threshold=early_exit_config.get('profit_drawdown_threshold', 0.50),
                    stall_time_threshold_sec=early_exit_config.get('stall_time_threshold_sec', 15.0),
                    quick_reversal_drop_pct=early_exit_config.get('quick_reversal_drop_pct', 0.5),
                    min_profit_for_drawdown_check=early_exit_config.get('min_profit_for_drawdown_check', 0.3),
                    grace_period_sec=early_exit_config.get('grace_period_sec', 3.0)
                )
                print(f"🚨 v13.9 早期逃命偵測器已啟用")
                print(f"   獲利回撤閾值: {early_exit_config.get('profit_drawdown_threshold', 0.50)*100:.0f}%")
                print(f"   微利停滯時間: {early_exit_config.get('stall_time_threshold_sec', 15.0)}秒")
                print(f"   快速反轉跌幅: {early_exit_config.get('quick_reversal_drop_pct', 0.5)}%")
            except Exception as e:
                print(f"⚠️ 早期逃命偵測器初始化失敗: {e}")
                self.early_exit_detector = None
        
        # 🆕 v14.11: 急跌急漲門檻建議 (基於幣安 ATR)
        self._show_spike_threshold_suggestion()
        
        # 🆕 v14.1: 強制平衡隨機進場 (每 N 筆保證 50/50)
        self._balanced_batch_size: int = _coerce_int(getattr(self.config, "random_entry_balance_batch_size", 20), default=20) or 20
        if self._balanced_batch_size < 2:
            self._balanced_batch_size = 20
        if self._balanced_batch_size % 2 != 0:
            self._balanced_batch_size += 1
        self._balanced_prefill_size: int = _coerce_int(getattr(self.config, "random_entry_balance_prefill_size", 30), default=30) or 30
        self._balanced_max_streak: int = _coerce_int(getattr(self.config, "random_entry_balance_max_streak", 3), default=3) or 0
        self._balanced_max_imbalance: int = _coerce_int(getattr(self.config, "random_entry_balance_max_imbalance", 4), default=4) or 0
        self._random_wave1: List[str] = []
        self._random_wave2: List[str] = []
        self._random_active_wave: int = 1

    def _use_binance_paper_source(self) -> bool:
        """紙上模式下是否強制使用 Binance 作為價格/訊號來源"""
        return bool(
            self.config.paper_mode
            and getattr(self.config, "paper_price_source", "dydx") == "binance"
        )

    def _get_market_ws(self):
        """取得市場數據來源 WebSocket (依紙上來源切換)"""
        if self._use_binance_paper_source():
            return self.binance_ws
        return self.ws

    def _get_market_price(self) -> float:
        """取得市場價格 (依紙上來源切換)"""
        api_price = self._get_dydx_api_mid_price()
        if api_price > 0:
            return api_price
        ws = self._get_market_ws()
        return getattr(ws, "current_price", 0) if ws else 0.0

    def _should_use_dydx_api_price(self) -> bool:
        if self._use_binance_paper_source():
            return False
        if not self.dydx:
            return False
        mode = str(getattr(self.config, "dydx_price_source", "") or "").lower()
        if mode:
            return mode in ("api", "rest", "indexer")
        return True

    def _get_dydx_api_bid_ask(self) -> Tuple[float, float]:
        if not self._should_use_dydx_api_price():
            return 0.0, 0.0

        now = time.time()
        if now - getattr(self, "_dydx_api_book_time", 0.0) < 1.0:
            cached_bid = getattr(self, "_dydx_api_best_bid", 0.0)
            cached_ask = getattr(self, "_dydx_api_best_ask", 0.0)
            if cached_bid > 0 and cached_ask > 0:
                return cached_bid, cached_ask

        try:
            orderbook = asyncio.run(
                self.dydx.get_orderbook(
                    self.config.symbol_dydx if hasattr(self.config, "symbol_dydx") else "BTC-USD"
                )
            )
            if orderbook:
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])
                best_bid = float(bids[0].get("price", 0)) if bids else 0.0
                best_ask = float(asks[0].get("price", 0)) if asks else 0.0
                if best_bid > 0 and best_ask > 0:
                    self._dydx_api_best_bid = best_bid
                    self._dydx_api_best_ask = best_ask
                    self._dydx_api_book_time = now
                    return best_bid, best_ask
        except Exception:
            pass

        return (
            getattr(self, "_dydx_api_best_bid", 0.0),
            getattr(self, "_dydx_api_best_ask", 0.0),
        )

    def _get_dydx_api_mid_price(self) -> float:
        bid, ask = self._get_dydx_api_bid_ask()
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return 0.0
    
    def _show_spike_threshold_suggestion(self):
        """
        🆕 v14.11: 顯示急跌急漲門檻建議 (基於幣安 ATR)
        
        只顯示建議，不自動修改配置。讓用戶觀察準確度後決定。
        """
        try:
            import ccxt
            import numpy as np
            
            # 獲取配置中的當前門檻
            spike_config = getattr(self.config, 'spike_fast_entry', {})
            current_threshold = spike_config.get('spike_fast_entry_threshold', 0.40)
            auto_adjust = spike_config.get('spike_auto_adjust', False)
            
            # 查詢幣安
            exchange = ccxt.binance({'timeout': 5000})
            ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1m', limit=20)
            
            if len(ohlcv) < 15:
                print(f"⚠️ 幣安數據不足，無法計算 ATR")
                return
            
            closes = [c[4] for c in ohlcv]
            highs = [c[2] for c in ohlcv]
            lows = [c[3] for c in ohlcv]
            
            # 計算 ATR
            tr_list = []
            for i in range(1, len(ohlcv)):
                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i] - closes[i-1])
                )
                tr_list.append(tr)
            
            atr_14 = np.mean(tr_list[-14:])
            atr_pct = (atr_14 / closes[-1]) * 100
            
            # 計算建議門檻 (ATR × 3，範圍 0.30% ~ 0.50%)
            suggested = max(0.30, min(0.50, round(atr_pct * 3, 2)))
            
            # 判斷市場狀態
            if atr_pct > 0.15:
                market_state = "🔥 高波動"
            elif atr_pct > 0.10:
                market_state = "📈 中高波動"
            elif atr_pct > 0.06:
                market_state = "📊 中波動"
            else:
                market_state = "😴 低波動"
            
            print(f"\n📊 v14.11 急跌急漲門檻分析 (幣安 ATR)")
            print(f"   市場狀態: {market_state} (ATR={atr_pct:.3f}%)")
            print(f"   當前門檻: {current_threshold}%")
            print(f"   建議門檻: {suggested}%")
            
            # 判斷是否需要調整
            diff = abs(current_threshold - suggested)
            if diff >= 0.05:
                if auto_adjust:
                    # 自動調整模式 (需要用戶明確啟用)
                    print(f"   ⚡ 自動調整: {current_threshold}% → {suggested}%")
                    # 動態修改配置
                    if hasattr(self.config, 'spike_fast_entry'):
                        self.config.spike_fast_entry['spike_fast_entry_threshold'] = suggested
                    else:
                        self.config.spike_fast_entry_threshold = suggested
                else:
                    if suggested > current_threshold:
                        print(f"   💡 建議: 可調高至 {suggested}% (減少假信號)")
                    else:
                        print(f"   💡 建議: 可調低至 {suggested}% (抓住更多機會)")
                    print(f"   ℹ️ 若要自動調整，請在配置加入 spike_auto_adjust: true")
            else:
                print(f"   ✅ 當前門檻適合當前市場")
            
            print()
            
        except Exception as e:
            print(f"⚠️ ATR 分析失敗 (非關鍵): {e}")
    
    def _build_random_wave(self) -> List[str]:
        """建立一波隨機方向 (預設 50/50)"""
        import random

        batch_size = getattr(self, "_balanced_batch_size", 20) or 20
        max_streak = getattr(self, "_balanced_max_streak", 3) or 0
        max_imbalance = getattr(self, "_balanced_max_imbalance", 4) or 0

        if getattr(self.config, 'random_entry_balance_enabled', True) is False:
            return [random.choice(["LONG", "SHORT"]) for _ in range(batch_size)]

        return _generate_constrained_balanced_sequence(
            batch_size,
            max_streak=max_streak,
            max_imbalance=max_imbalance,
        )

    def _ensure_random_waves(self):
        """確保隨機波次存在 (不會在同波未用盡時回補)"""
        if not self._random_wave1 and not self._random_wave2:
            self._random_wave1 = self._build_random_wave()
            self._random_wave2 = self._build_random_wave()
            if getattr(self.config, 'random_entry_balance_enabled', True) is not False:
                half = len(self._random_wave1) // 2
                print(
                    f"🎲 初始化隨機波: {half} LONG + {half} SHORT | batch={len(self._random_wave1)} streak≤{getattr(self, '_balanced_max_streak', 0)} imbalance≤{getattr(self, '_balanced_max_imbalance', 0)}"
                )
        elif self._random_active_wave == 1 and not self._random_wave2:
            self._random_wave2 = self._build_random_wave()
        elif self._random_active_wave == 2 and not self._random_wave1:
            self._random_wave1 = self._build_random_wave()

    def _roll_random_waves_if_needed(self):
        """當目前波次用盡時切換，並回補另一波"""
        if self._random_active_wave == 1 and not self._random_wave1:
            self._random_active_wave = 2
            self._random_wave1 = self._build_random_wave()
            print("🎲 第1波已用盡 → 切換第2波，補第1波")
        elif self._random_active_wave == 2 and not self._random_wave2:
            self._random_active_wave = 1
            self._random_wave2 = self._build_random_wave()
            print("🎲 第2波已用盡 → 切換第1波，補第2波")

    def _get_active_wave_remaining(self) -> int:
        if getattr(self.config, 'random_entry_balance_enabled', True) is False:
            return 0
        self._ensure_random_waves()
        if self._random_active_wave == 1:
            return len(self._random_wave1)
        return len(self._random_wave2)

    def _return_random_direction(self, direction: str):
        """Veto/風控拒絕時，把方向放回當前波次末尾"""
        if getattr(self.config, 'random_entry_balance_enabled', True) is False:
            return
        if self._random_active_wave == 1:
            self._random_wave1.append(direction)
        else:
            self._random_wave2.append(direction)
    
    def _get_balanced_random_direction(self, market_data: Optional[Dict] = None) -> str:
        """
        🆕 v14.1 強制平衡隨機進場
        以固定批次 50/50 隨機產生方向；可選擇關閉趨勢偏向，避免長時間單邊。
        
        Returns:
            'LONG' 或 'SHORT'
        """
        import random

        if getattr(self.config, 'random_entry_balance_enabled', True) is False:
            return random.choice(["LONG", "SHORT"])

        self._ensure_random_waves()
        self._roll_random_waves_if_needed()
        active_wave = self._random_wave1 if self._random_active_wave == 1 else self._random_wave2

        # 可選：趨勢偏向（預設關閉，避免長時間單邊）
        if getattr(self.config, "random_entry_balance_trend_bias_enabled", False) and market_data:
            bias = "NEUTRAL"
            mtf = market_data.get("mtf_analysis", {}) or {}
            if mtf:
                bias = mtf.get("overall_bias", "NEUTRAL")
            if bias == "NEUTRAL":
                p5m = market_data.get("price_change_5m", 0) or 0
                if p5m > 0.15:
                    bias = "LONG"
                elif p5m < -0.15:
                    bias = "SHORT"
            if bias in ("LONG", "SHORT") and bias in active_wave:
                idx = active_wave.index(bias)
                direction = active_wave.pop(idx)
                print(f"🎲 趨勢偏向隨機: 偵測到 {bias} 趨勢，優先取出 {direction}")
                return direction

        direction = active_wave.pop(0)
        remaining_long = active_wave.count("LONG")
        remaining_short = active_wave.count("SHORT")
        if len(active_wave) % 5 == 0:
            print(f"   📊 平衡狀態: 剩餘 {remaining_long}L/{remaining_short}S")
        return direction

    def _get_balanced_direction_preview(self, count: int = 20) -> Tuple[List[str], List[str]]:
        """Preview upcoming random directions without consuming the waves."""
        if count <= 0:
            return [], []

        if getattr(self.config, 'random_entry_balance_enabled', True) is False:
            import random
            return [random.choice(["LONG", "SHORT"]) for _ in range(count)], []

        self._ensure_random_waves()
        return list(self._random_wave1)[:count], list(self._random_wave2)[:count]
    
    def _on_backtest_triggered(self, reason: str, stats: Dict):
        """回測觸發回調"""
        self.logger.warning(f"⚠️ 自動回測觸發！原因: {reason}")
        self.logger.warning(f"   當前統計: {json.dumps(stats, ensure_ascii=False)}")
        
        # 發出聲音警報
        play_sound('error')
        
        # 執行回測
        if self.auto_backtest:
            result = self.auto_backtest.run_backtest(hours=24)
            self.logger.info(f"📊 回測完成: {result.analysis_summary}")
            
            # 🆕 只有 v5 卡片 (auto_optimize_enabled=True) 才生成新卡片
            if getattr(self.config, 'auto_optimize_enabled', False):
                new_card_path = self.auto_backtest.generate_new_card(
                    result, 
                    f"auto_optimized_{datetime.now().strftime('%Y%m%d_%H%M')}"
                )
                self.logger.info(f"🃏 v5: 新卡片已生成: {new_card_path}")
            else:
                self.logger.info(f"ℹ️ 非 v5 卡片，跳過生成 auto_optimized 配置")
    
    def _on_trading_mode_changed(self, old_mode, new_mode, reason: str):
        """交易模式變更回調"""
        self.logger.warning(f"🔄 交易模式變更: {old_mode.value} → {new_mode.value} (原因: {reason})")
        
        if new_mode == TradingMode.PAUSED:
            # 暫停真實交易，但保留 paper trading
            self.config.paper_mode = True
            self.logger.warning("⚠️ 真實交易已暫停，切換至虛擬模式")
            play_sound('error')
    
    def _on_new_card_ready(self, card_path: str, card_config: Dict):
        """新卡片就緒回調 (預留給 LLM 系統)"""
        self.logger.info(f"🃏 新交易卡片就緒: {card_path}")
        self.logger.info(f"   推薦方向: {card_config.get('allowed_directions', [])}")
        
        # 🆕 v5: 如果使用 auto_optimize 卡片，自動應用新配置
        if getattr(self.config, 'auto_optimize_enabled', False):
            self._apply_auto_optimized_config(card_config)
            self.logger.info(f"✅ v5 自適應: 已自動應用新配置")
    
    def _load_auto_optimized_config(self) -> Optional[Dict]:
        """
        🆕 v5: 載入最新的 auto_optimized 配置
        
        只有當 config.auto_optimize_enabled = True 時才會執行
        (即使用 random_entry_smart_exit_v5 卡片)
        
        Returns:
            最新的 auto_optimized 配置，或 None
        """
        if not getattr(self.config, 'auto_optimize_enabled', False):
            return None
        
        config_dir = Path(getattr(self.config, 'auto_optimize_config_dir', 'config/trading_cards/auto_optimized'))
        pattern = getattr(self.config, 'auto_optimize_config_pattern', 'auto_optimized_*.json')
        
        try:
            # 找到所有 auto_optimized 配置
            auto_files = sorted(config_dir.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
            
            if not auto_files:
                self.logger.info("📭 沒有找到 auto_optimized 配置，使用預設參數")
                return None
            
            # 載入最新的配置
            latest_file = auto_files[0]
            with open(latest_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            self.logger.info(f"🔄 v5: 載入 auto_optimized 配置: {latest_file.name}")
            self.logger.info(f"   生成時間: {config.get('created_at', 'unknown')}")
            self.logger.info(f"   基於交易數: {config.get('metadata', {}).get('based_on_trades', 0)}")
            
            return config
            
        except Exception as e:
            self.logger.warning(f"⚠️ 載入 auto_optimized 配置失敗: {e}")
            return None
    
    def _apply_auto_optimized_config(self, auto_config: Dict) -> bool:
        """
        🆕 v5: 應用 auto_optimized 配置到當前交易系統
        
        可調整的參數:
        - allowed_directions: 交易方向 (LONG/SHORT/BOTH)
        - six_dim_min_score_to_trade: 六維信號閾值
        - stop_loss_pct / take_profit_pct: 風報比
        - max_hold_time_sec: 最長持倉時間
        
        Args:
            auto_config: auto_optimized 配置
            
        Returns:
            是否成功應用
        """
        if not auto_config:
            return False
        
        try:
            applied = []
            
            # 1. 應用交易方向
            if 'allowed_directions' in auto_config:
                directions = auto_config['allowed_directions']
                if hasattr(self.config, 'allowed_directions'):
                    self.config.allowed_directions = directions
                    applied.append(f"方向: {directions}")
            
            # 2. 應用六維信號閾值
            if 'six_dim_min_score_to_trade' in auto_config:
                score = auto_config['six_dim_min_score_to_trade']
                if hasattr(self.config, 'six_dim_min_score_to_trade'):
                    self.config.six_dim_min_score_to_trade = score
                    applied.append(f"六維閾值: {score}")
            
            # 3. 應用止損
            if 'stop_loss_pct' in auto_config:
                sl = auto_config['stop_loss_pct']
                if hasattr(self.config, 'pre_stop_loss_pct'):
                    self.config.pre_stop_loss_pct = sl
                    applied.append(f"止損: {sl}%")
            
            # 4. 應用止盈
            if 'take_profit_pct' in auto_config:
                tp = auto_config['take_profit_pct']
                if hasattr(self.config, 'target_profit_pct'):
                    self.config.target_profit_pct = tp
                    applied.append(f"止盈: {tp}%")
            
            # 5. 應用持倉時間
            if 'max_hold_time_sec' in auto_config:
                hold_sec = auto_config['max_hold_time_sec']
                hold_min = hold_sec / 60
                if hasattr(self.config, 'max_hold_minutes'):
                    self.config.max_hold_minutes = hold_min
                    applied.append(f"持倉: {hold_min:.1f}分")
            
            # 6. 應用最低信心度
            if 'min_confidence' in auto_config:
                conf = auto_config['min_confidence']
                if hasattr(self.config, 'min_confidence'):
                    self.config.min_confidence = conf
                    applied.append(f"信心度: {conf}")
            
            if applied:
                self.logger.info(f"✅ v5 已應用 auto_optimized: {', '.join(applied)}")
                return True
            else:
                self.logger.info("📝 auto_optimized 配置無可應用的參數")
                return False
                
        except Exception as e:
            self.logger.error(f"❌ 應用 auto_optimized 配置失敗: {e}")
            return False
    
    def _init_auto_optimize_v5(self):
        """
        🆕 v5: 初始化自適應進化系統
        
        只在使用 random_entry_smart_exit_v5 卡片時執行
        """
        if not getattr(self.config, 'auto_optimize_enabled', False):
            return
        
        print("=" * 60)
        print("🔄 v5 自適應進化系統啟動中...")
        print("=" * 60)
        
        # 載入最新的 auto_optimized 配置
        auto_config = self._load_auto_optimized_config()
        
        if auto_config:
            # 應用配置
            success = self._apply_auto_optimized_config(auto_config)
            
            if success:
                print(f"✅ 已載入並應用 auto_optimized 配置")
                print(f"   📊 允許方向: {auto_config.get('allowed_directions', ['LONG', 'SHORT'])}")
                print(f"   🎯 六維閾值: {auto_config.get('six_dim_min_score_to_trade', 6)}")
                print(f"   🛡️ 止損: {auto_config.get('stop_loss_pct', 0.5)}%")
                print(f"   💰 止盈: {auto_config.get('take_profit_pct', 1.0)}%")
                print(f"   ⏱️ 預期勝率: {auto_config.get('metadata', {}).get('expected_win_rate', 50):.1f}%")
        else:
            print("📭 使用預設參數 (未找到 auto_optimized 配置)")
        
        print("=" * 60)
    
    def _load_reversal_config(self) -> Dict:
        """載入反轉策略配置"""
        # 防止 None 值
        config_path_str = self.config.reversal_config_path or "config/reversal_strategy.json"
        config_path = Path(config_path_str)
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 從配置恢復狀態
                    stats = config.get('statistics', {})
                    self.consecutive_losses = stats.get('consecutive_losses', 0)
                    self.consecutive_wins = stats.get('consecutive_wins', 0)
                    self.market_regime = config.get('market_regime', {}).get('current', 'NORMAL')
                    return config
            except Exception as e:
                print(f"⚠️ 載入反轉配置失敗: {e}")
        return {
            'reversal_mode': {'enabled': True},
            'market_regime': {'current': 'NORMAL'},
            'statistics': {}
        }
    
    def _save_reversal_config(self):
        """保存反轉策略配置"""
        # 防止 None 值
        config_path_str = self.config.reversal_config_path or "config/reversal_strategy.json"
        config_path = Path(config_path_str)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 更新統計數據
        self.reversal_config['statistics']['last_updated'] = datetime.now().isoformat()
        self.reversal_config['statistics']['consecutive_losses'] = self.consecutive_losses
        self.reversal_config['statistics']['consecutive_wins'] = self.consecutive_wins
        self.reversal_config['market_regime']['current'] = self.market_regime
        
        # 記錄模式切換
        if len(self.reversal_config.get('statistics', {}).get('regime_switches', [])) < 100:
            pass  # 保留最近 100 次切換記錄
        
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.reversal_config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存反轉配置失敗: {e}")
    
    def _load_dynamic_profit_config(self) -> Dict:
        """載入動態止盈配置"""
        # 防止 None 值
        config_path_str = self.config.dynamic_profit_config_path or "config/ai_profit_dynamic.json"
        config_path = Path(config_path_str)
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.logger.info(f"✅ 載入動態止盈配置: 毛利目標 {config.get('current_active_target', {}).get('gross_target_pct', 6.0)}%")
                    return config
            except Exception as e:
                self.logger.warning(f"⚠️ 載入動態止盈配置失敗: {e}")
        
        # 預設配置
        return {
            'dynamic_profit_system': {'enabled': True},
            'current_active_target': {
                'gross_target_pct': 6.0,
                'net_target_pct': 3.0
            }
        }
    
    def _save_dynamic_profit_config(self):
        """保存動態止盈配置 (包含交易歷史分析結果)"""
        # 防止 None 值
        config_path_str = self.config.dynamic_profit_config_path or "config/ai_profit_dynamic.json"
        config_path = Path(config_path_str)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 🔧 v14.6.41: 確保 current_active_target 存在
        if 'current_active_target' not in self.dynamic_profit_config:
            self.dynamic_profit_config['current_active_target'] = {
                'gross_target_pct': 6.0,
                'net_target_pct': 3.0
            }
        self.dynamic_profit_config['current_active_target']['last_updated'] = datetime.now().isoformat()
        
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.dynamic_profit_config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存動態止盈配置失敗: {e}")
    
    def calculate_smart_exit_target(self, trade: 'TradeRecord', current_price: float) -> Dict:
        """
        🆕 v4.0 計算智能止盈目標
        
        基於觀察：浮動+6% 淨盈虧+3% 時是最佳止盈點
        
        考慮因素:
        1. 當前槓桿倍數
        2. BTC 價格水平
        3. 市場波動率
        4. 歷史交易表現
        
        Returns:
            Dict with: gross_target_pct, net_target_pct, should_exit, reason
        """
        config = self.dynamic_profit_config
        leverage = trade.actual_leverage or self.config.leverage
        
        # 1. 基礎目標 (根據槓桿)
        leverage_targets = config.get('leverage_based_targets', {})
        leverage_key = f"{leverage}x"
        
        # 找最接近的槓桿配置
        if leverage_key not in leverage_targets:
            if leverage >= 90:
                leverage_key = "100x"
            elif leverage >= 60:
                leverage_key = "75x"
            else:
                leverage_key = "50x"
        
        base_targets = leverage_targets.get(leverage_key, {}).get('normal', {
            'gross': 6.0, 'net': 3.0
        })
        gross_target = base_targets.get('gross', 6.0)
        net_target = base_targets.get('net', 3.0)
        
        # 2. 價格調整
        price_adjustment = 1.0
        if config.get('price_based_adjustment', {}).get('enabled', False):
            for rule in config.get('price_based_adjustment', {}).get('rules', []):
                price_range = rule.get('price_range', [0, 999999])
                if price_range[0] <= current_price < price_range[1]:
                    price_adjustment = rule.get('adjustment', 1.0)
                    break
        
        # 3. 波動率調整
        volatility = self.market_data.get('volatility_5m', 0)
        vol_adjustment = 1.0
        if config.get('volatility_based_adjustment', {}).get('enabled', False):
            for rule in config.get('volatility_based_adjustment', {}).get('rules', []):
                vol_range = rule.get('volatility_range', [0, 999])
                if vol_range[0] <= volatility < vol_range[1]:
                    vol_adjustment = rule.get('adjustment', 1.0)
                    break
        
        # 4. 計算最終目標
        final_gross = gross_target * price_adjustment * vol_adjustment
        final_net = net_target * price_adjustment * vol_adjustment
        
        # 5. 計算當前盈虧
        if trade.direction == "LONG":
            price_move_pct = (current_price - trade.entry_price) / trade.entry_price * 100
        else:
            price_move_pct = (trade.entry_price - current_price) / trade.entry_price * 100
        
        gross_pnl_pct = price_move_pct * leverage
        
        # 手續費計算 (佔本金百分比)
        # 手續費 = 名義價值 × 費率 = 本金 × 槓桿 × 費率
        # 手續費佔本金比例 = 槓桿 × 費率 × 2 (進出場)
        fee_rate = (self.config.maker_fee_pct if self.config.use_maker_simulation else self.config.taker_fee_pct) / 100
        fee_mult = _fee_leverage_multiplier(self.config, leverage)
        total_fee_pct = fee_rate * fee_mult * 2 * 100  # 進出場手續費佔本金百分比
        net_pnl_pct = gross_pnl_pct - total_fee_pct
        
        # 6. 判斷是否應該止盈
        should_exit = False
        exit_reason = ""
        
        smart_rules = config.get('smart_exit_rules', {})
        
        # 規則1: 快速獲利即走
        hold_time_min = (time.time() - (datetime.fromisoformat(trade.entry_time).timestamp() if trade.entry_time else time.time())) / 60
        if smart_rules.get('rule_1_quick_profit', {}).get('enabled', True):
            if net_pnl_pct >= net_target and hold_time_min < self.config.quick_profit_time_limit:
                should_exit = True
                exit_reason = f"⚡ 快速獲利 ({hold_time_min:.1f}分鐘達 {net_pnl_pct:.1f}% 淨利)"
        
        # 規則2: 毛淨差距達標 (觀察到的最佳點)
        if smart_rules.get('rule_2_gross_net_gap', {}).get('enabled', True):
            if gross_pnl_pct >= final_gross and net_pnl_pct >= final_net * 0.8:
                should_exit = True
                exit_reason = f"🎯 最佳止盈點 (毛利 {gross_pnl_pct:.1f}% / 淨利 {net_pnl_pct:.1f}%)"
        
        # 🆕 v2.0 規則3: 達到目標淨利 (從配置讀取)
        strategy_config = load_trading_strategy()
        smart_exit_config = strategy_config.get('profit_strategy', {}).get('smart_exit', {}).get('rules', {})
        
        target_reached = smart_exit_config.get('target_reached', {})
        if target_reached.get('enabled', True):
            target_net = target_reached.get('target_net_pnl', 10.0)
            if net_pnl_pct >= target_net:
                should_exit = True
                exit_reason = f"🎯 達到目標 ({net_pnl_pct:.1f}% >= {target_net}% 淨利)"
        
        # 🆕 v2.0 規則4: 快速獲利 (從配置讀取)
        quick_profit = smart_exit_config.get('quick_profit', {})
        if quick_profit.get('enabled', True):
            quick_net = quick_profit.get('min_net_pnl', 6.0)
            quick_time = quick_profit.get('max_hold_minutes', 3)
            if net_pnl_pct >= quick_net and hold_time_min <= quick_time:
                should_exit = True
                exit_reason = f"⚡ 快速獲利 ({hold_time_min:.1f}分鐘達 {net_pnl_pct:.1f}% 淨利)"
        
        # 🆕 v2.0 規則5: 時間衰減 (從配置讀取，提高門檻)
        time_decay = smart_exit_config.get('time_decay', {})
        if time_decay.get('enabled', True):
            decay_after = time_decay.get('after_minutes', 15)
            decay_min_pnl = time_decay.get('min_net_pnl', 4.0)
            if hold_time_min > decay_after and net_pnl_pct >= decay_min_pnl:
                should_exit = True
                exit_reason = f"⏰ 時間衰減 ({hold_time_min:.0f}分鐘，保住 {net_pnl_pct:.1f}% 淨利)"
        
        return {
            'gross_target_pct': round(final_gross, 2),
            'net_target_pct': round(final_net, 2),
            'current_gross_pnl_pct': round(gross_pnl_pct, 2),
            'current_net_pnl_pct': round(net_pnl_pct, 2),
            'should_exit': should_exit,
            'exit_reason': exit_reason,
            'price_adjustment': price_adjustment,
            'volatility_adjustment': vol_adjustment,
            'hold_time_min': round(hold_time_min, 1)
        }
    
    def record_exit_for_analysis(self, trade: 'TradeRecord', exit_reason: str):
        """記錄出場數據，用於分析最佳止盈點"""
        exit_record = {
            'timestamp': datetime.now().isoformat(),
            'strategy': trade.strategy,
            'direction': trade.direction,
            'leverage': trade.actual_leverage,
            'entry_price': trade.entry_price,
            'exit_price': trade.exit_price,
            'gross_pnl_pct': trade.pnl_pct,
            'net_pnl_usdt': trade.net_pnl_usdt,
            'net_pnl_pct': trade.net_pnl_usdt / trade.position_size_usdt * 100 if trade.position_size_usdt else 0,
            'hold_time_min': (datetime.fromisoformat(trade.exit_time) - datetime.fromisoformat(trade.entry_time)).total_seconds() / 60 if trade.exit_time and trade.entry_time else 0,
            'exit_reason': exit_reason,
            'max_profit_pct': trade.max_profit_pct,
            'btc_price': trade.exit_price
        }
        
        self.trade_exit_history.append(exit_record)
        
        # 保留最近 50 筆
        if len(self.trade_exit_history) > 50:
            self.trade_exit_history = self.trade_exit_history[-50:]
        
        # 每 5 筆交易自動分析一次
        if len(self.trade_exit_history) % 5 == 0:
            self._analyze_optimal_exit_points()
    
    def _analyze_optimal_exit_points(self):
        """分析歷史交易，找出最佳止盈點"""
        if len(self.trade_exit_history) < 5:
            return
        
        # 只分析獲利交易
        winning_trades = [t for t in self.trade_exit_history if t['net_pnl_usdt'] > 0]
        if not winning_trades:
            return
        
        # 計算平均值
        avg_gross = sum(t['gross_pnl_pct'] for t in winning_trades) / len(winning_trades)
        avg_net = sum(t['net_pnl_pct'] for t in winning_trades) / len(winning_trades)
        avg_hold = sum(t['hold_time_min'] for t in winning_trades) / len(winning_trades)
        
        # 更新配置建議
        analysis = self.dynamic_profit_config.get('trade_history_analysis', {})
        analysis['last_analysis'] = datetime.now().isoformat()
        analysis['sample_size'] = len(winning_trades)
        analysis['avg_gross_at_exit'] = round(avg_gross, 2)
        analysis['avg_net_at_exit'] = round(avg_net, 2)
        analysis['avg_hold_time_winners'] = round(avg_hold, 1)
        
        # 如果自動調整啟用，更新目標
        if analysis.get('auto_adjust', {}).get('enabled', False):
            current_target = self.dynamic_profit_config.get('current_active_target', {})
            
            # 如果歷史平均明顯偏離目標，調整
            if avg_net > 0:
                # 平均淨利作為新目標的參考 (保守: 使用 80% 的平均值)
                suggested_net = avg_net * 0.8
                max_adj = analysis.get('auto_adjust', {}).get('max_adjustment_pct', 20) / 100
                
                current_net = current_target.get('net_target_pct', 3.0)
                new_net = max(current_net * (1 - max_adj), 
                             min(current_net * (1 + max_adj), suggested_net))
                
                current_target['net_target_pct'] = round(new_net, 2)
                current_target['gross_target_pct'] = round(new_net * 2, 2)  # 毛利約為淨利的 2 倍
        
        self.dynamic_profit_config['trade_history_analysis'] = analysis
        self._save_dynamic_profit_config()
        
        self.logger.info(f"📊 止盈分析: 獲利平均毛利 {avg_gross:.1f}% / 淨利 {avg_net:.1f}% / 持倉 {avg_hold:.1f}分鐘")

    def _update_trade_result(self, is_win: bool, trade_info: Dict):
        """
        更新交易結果，自動切換市場模式
        
        Args:
            is_win: 是否獲利
            trade_info: 交易資訊 (策略、方向、盈虧等)
        """
        # 安全取得閾值，避免 NoneType 比較錯誤
        try:
            consecutive_wins_to_restore = int(getattr(self.config, 'consecutive_wins_to_restore', None) or 3)
        except Exception:
            consecutive_wins_to_restore = 3

        try:
            consecutive_losses_to_switch = int(getattr(self.config, 'consecutive_losses_to_switch', None) or 5)
        except Exception:
            consecutive_losses_to_switch = 5

        # 確保計數為 int，避免 None 或其他型別導致比較失敗
        if not isinstance(self.consecutive_losses, int):
            try:
                self.consecutive_losses = int(self.consecutive_losses or 0)
            except Exception:
                self.consecutive_losses = 0
        if not isinstance(self.consecutive_wins, int):
            try:
                self.consecutive_wins = int(self.consecutive_wins or 0)
            except Exception:
                self.consecutive_wins = 0

        if is_win:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            
            # 連續獲利 N 次，恢復正常模式
            if self.consecutive_wins >= consecutive_wins_to_restore:
                if self.market_regime == "REVERSAL":
                    self.logger.info(f"🔄 連續獲利 {self.consecutive_wins} 次，恢復正常模式")
                    self._switch_market_regime("NORMAL")
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            
            # 連續虧損 N 次，切換反轉模式
            if self.consecutive_losses >= consecutive_losses_to_switch:
                if self.market_regime == "NORMAL":
                    self.logger.info(f"⚠️ 連續虧損 {self.consecutive_losses} 次，切換反轉模式")
                    self._switch_market_regime("REVERSAL")
        
        # 記錄交易
        self.reversal_trade_history.append({
            'timestamp': datetime.now().isoformat(),
            'is_win': is_win,
            'market_regime': self.market_regime,
            **trade_info
        })
        
        # 保存配置
        self._save_reversal_config()
        
        # 記錄完成，繼續執行
        pass
    
    def _switch_market_regime(self, new_regime: str):
        """切換市場模式"""
        old_regime = self.market_regime
        self.market_regime = new_regime
        
        # 記錄切換
        switch_record = {
            'timestamp': datetime.now().isoformat(),
            'from': old_regime,
            'to': new_regime,
            'consecutive_losses': self.consecutive_losses,
            'consecutive_wins': self.consecutive_wins
        }
        
        if 'regime_switches' not in self.reversal_config.get('statistics', {}):
            self.reversal_config['statistics']['regime_switches'] = []
        self.reversal_config['statistics']['regime_switches'].append(switch_record)
        
        self.logger.info(f"📊 市場模式切換: {old_regime} → {new_regime}")
    
    def _get_reversal_direction(self, strategy: str, original_direction: str, data: Dict) -> tuple[str, str]:
        """
        根據訊號過濾決定是否執行交易
        
        🆕 v6.0 三重過濾邏輯 (基於 35 筆歷史數據分析)：
        
        ❌ 移除所有反轉邏輯
        ✅ 依照主力訊號執行
        ✅ OBI 甜蜜區過濾器
        ✅ 5分鐘趨勢過濾器 (避免追漲殺跌)
        
        【數據驗證結果】
        - 原始勝率: 51%, 總損益: -138 USDT
        - 新過濾後: 86% 勝率, 總損益: +41 USDT
        
        【三重過濾規則】
        派發做空: -0.7 <= OBI < 0 且 5分鐘盤整 (-0.05% ~ 0.05%)
        吸籌做多: 0 <= OBI < 0.7 且 5分鐘不下跌 (>= -0.05%)
        
        Args:
            strategy: 策略名稱 (ACCUMULATION, DISTRIBUTION 等)
            original_direction: 原始方向 (LONG, SHORT)
            data: 市場數據
            
        Returns:
            (實際方向, 說明) - 如果不交易，返回 ("SKIP", 說明)
        """
        # 🆕 v7.0: 反向模式跳過這些過濾 (因為方向會反轉，過濾邏輯也應該反轉)
        if self.config.reverse_mode:
            return original_direction, "v7.0 反向模式: 跳過方向過濾"
        
        if not self.config.reversal_mode_enabled:
            return original_direction, "過濾器關閉"
        
        obi = data.get('obi', 0)
        price_change_1m = data.get('price_change_1m', 0)
        price_change_5m = data.get('price_change_5m', 0)
        
        # 閾值設定 (基於數據分析) 🔧 v12.12: 恢復保護但適度放寬
        obi_min_short = -0.7   # 派發做空 OBI 下限 (太極端會反彈)
        obi_max_short = 0.15   # 🔧 v12.12: 派發做空 OBI 上限 (允許輕微買壓)
        obi_min_long = -0.15   # 🔧 v12.12: 吸籌做多 OBI 下限 (允許輕微賣壓)
        obi_max_long = 0.7     # 吸籌做多 OBI 上限
        trend_threshold = 0.08  # 🔧 v12.12: 5分鐘趨勢閾值 (恢復到 0.08%)
        
        # ============================================================
        # 【派發做空】OBI 甜蜜區 + 5分鐘盤整
        # ============================================================
        if strategy == "DISTRIBUTION" and original_direction == "SHORT":
            # 1. 檢查 OBI 甜蜜區 (-0.7 ~ 0)
            if obi >= obi_max_short:
                return "SKIP", f"⚠️ 派發但OBI正向({obi:.3f}) 買盤強 → 觀望"
            if obi < obi_min_short:
                return "SKIP", f"⚠️ 派發但OBI過低({obi:.3f}) 賣壓過重將反彈 → 觀望"
            
            # 2. 檢查 5分鐘趨勢 (需盤整，避免追跌)
            if price_change_5m < -trend_threshold:
                return "SKIP", f"⏸️ 派發+OBI正常 但5分鐘已跌({price_change_5m:+.3f}%) 避免追跌 → 觀望"
            if price_change_5m > trend_threshold:
                return "SKIP", f"⏸️ 派發+OBI正常 但5分鐘上漲({price_change_5m:+.3f}%) 趨勢反向 → 觀望"
            
            return "SHORT", f"✅ 派發+OBI({obi:.3f})+5m盤整({price_change_5m:+.3f}%) → 做空"
        
        # ============================================================
        # 【吸籌做多】OBI 甜蜜區 + 5分鐘不下跌
        # ============================================================
        elif strategy == "ACCUMULATION" and original_direction == "LONG":
            # 1. 檢查 OBI 甜蜜區 (0 ~ 0.7)
            if obi < obi_min_long:
                return "SKIP", f"⚠️ 吸籌但OBI負向({obi:.3f}) 賣盤強 → 觀望"
            if obi >= obi_max_long:
                return "SKIP", f"⚠️ 吸籌但OBI過高({obi:.3f}) 可能誘多 → 觀望"
            
            # 2. 檢查 5分鐘趨勢 (不能下跌)
            if price_change_5m < -trend_threshold:
                return "SKIP", f"⏸️ 吸籌+OBI正常 但5分鐘下跌({price_change_5m:+.3f}%) 趨勢反向 → 觀望"
            
            return "LONG", f"✅ 吸籌+OBI({obi:.3f})+5m穩定({price_change_5m:+.3f}%) → 做多"
        
        # ============================================================
        # 【其他策略】直接執行
        # ============================================================
        return original_direction, f"其他策略 → 正常執行"
    
    def _setup_logging(self):
        """設置日誌"""
        log_file = self.trader.log_dir / f"system_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
        self.logger = logging.getLogger("WhaleTestnet")
        self.logger.setLevel(logging.INFO)
        
        if not self.logger.handlers:
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setFormatter(logging.Formatter(
                '%(asctime)s | %(levelname)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            ))
            self.logger.addHandler(fh)
            
    def _get_dydx_funding(self) -> float:
        """獲取 dYdX 資金費率"""
        if not self.dydx: return 0.0
        try:
            now = time.time()
            # 緩存 60 秒 (資金費率變化不快)
            if now - getattr(self, '_dydx_funding_time', 0) < 60.0:
                return getattr(self, '_dydx_funding', 0)
            
            market = asyncio.run(self.dydx.get_market(self.config.symbol_dydx if hasattr(self.config, 'symbol_dydx') else "BTC-USD"))
            if market:
                funding = float(market.get('nextFundingRate', 0)) * 100
                self._dydx_funding = funding
                self._dydx_funding_time = now
                return funding
        except Exception:
            pass
        return 0.0
    
    def get_current_price(self) -> float:
        """
        獲取當前價格
        - 🔧 v14.6.38: 使用訂單簿中間價 (顯示和成交一致)
        """
        # dYdX API 訂單簿中間價 (優先於 WS)
        api_mid = self._get_dydx_api_mid_price()
        if api_mid > 0:
            return api_mid

        # 🔧 v12.9.2: Paper 模式使用指定來源 (Binance / dYdX)
        if self.config.paper_mode:
            market_ws = self._get_market_ws()
            ws_price = getattr(market_ws, 'current_price', 0) if market_ws else 0
            if ws_price > 0:
                return ws_price
            if self._use_binance_paper_source():
                return 0.0
        
        # 🔧 v14.4: 優先使用 WebSocket 價格 (不消耗 REST 配額)
        if hasattr(self, 'ws') and self.ws and self.ws.current_price > 0:
            return self.ws.current_price
        
        # 🆕 dYdX Integration (非 Paper 模式時使用，緩存 5 秒減少 API 呼叫)
        if self.dydx:
            try:
                # 🔧 v14.4: 緩存 5 秒 (原 0.5 秒太短)
                now = time.time()
                if now - getattr(self, '_dydx_price_time', 0) < 5.0:
                    return getattr(self, '_dydx_price', 0)
                
                market = asyncio.run(self.dydx.get_market(self.config.symbol_dydx if hasattr(self.config, 'symbol_dydx') else "BTC-USD"))
                if market:
                    price = float(market.get('oraclePrice', 0))
                    if price > 0:
                        self._dydx_price = price
                        self._dydx_price_time = now
                        return price
            except Exception as e:
                # self.logger.error(f"dYdX price fetch error: {e}")
                pass

        if self.config.paper_mode:
            return self._get_market_price()
        
        # Testnet 模式: 每次都獲取最新價格 (不緩存)
        try:
            if self.trader.testnet_api:
                price = self.trader.testnet_api.get_price()
                if price > 0:
                    return price
        except Exception:
            pass
        
        # 失敗時回退到 WebSocket
        return self.ws.current_price
    
    def get_current_price_for_trading(self) -> float:
        """
        🔧 v14.6.38: 獲取交易用的當前價格
        
        統一使用 Orderbook 中間價，確保顯示和成交一致
        
        Returns:
            float: dYdX 訂單簿中間價 (sync模式) 或指定紙上來源的 WebSocket 價格
        """
        # dYdX API 訂單簿中間價 (優先於 WS)
        api_mid = self._get_dydx_api_mid_price()
        if api_mid > 0:
            return api_mid

        # 非 sync 模式: 使用指定紙上來源
        market_ws = self._get_market_ws()
        if market_ws and getattr(market_ws, 'current_price', 0) > 0:
            return market_ws.current_price
        if self._use_binance_paper_source():
            return 0.0
        
        return 0.0

    def _get_price_context(self) -> Dict[str, float]:
        """取得交易價格上下文 (mid/bid/ask/oracle)"""
        ctx = {
            'mid': 0.0,
            'bid': 0.0,
            'ask': 0.0,
            'oracle': 0.0,
            'source': 'dydx',
        }

        use_dydx = getattr(self.trader, 'dydx_sync_enabled', False)
        market_ws = self.ws if use_dydx else self._get_market_ws()
        if self._use_binance_paper_source() and not use_dydx:
            ctx['source'] = 'binance'

        api_bid, api_ask = self._get_dydx_api_bid_ask()
        if api_bid > 0 and api_ask > 0:
            bid = api_bid
            ask = api_ask
            mid = (bid + ask) / 2
            ctx['source'] = 'dydx_api'
        else:
            bid = _coerce_float(getattr(market_ws, 'bid_price', 0.0), default=0.0) if market_ws else 0.0
            ask = _coerce_float(getattr(market_ws, 'ask_price', 0.0), default=0.0) if market_ws else 0.0
            mid = _coerce_float(getattr(market_ws, 'current_price', 0.0), default=0.0) if market_ws else 0.0
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2

        oracle = 0.0
        if use_dydx:
            oracle = _coerce_float(getattr(self.trader, 'dydx_oracle_price_cache', 0.0), default=0.0)
        if oracle <= 0 and market_ws is not None:
            oracle = _coerce_float(getattr(market_ws, 'oracle_price', 0.0), default=0.0)
        if oracle <= 0:
            oracle = mid

        if bid <= 0:
            bid = mid
        if ask <= 0:
            ask = mid

        ctx['mid'] = mid
        ctx['bid'] = bid
        ctx['ask'] = ask
        ctx['oracle'] = oracle
        return ctx

    def get_entry_price(self, direction: str, price_ctx: Optional[Dict[str, float]] = None) -> float:
        """進場判斷價格 (LONG 用 bid, SHORT 用 ask)"""
        ctx = price_ctx or self._get_price_context()
        if direction == "LONG":
            return ctx.get('bid', 0.0) or ctx.get('mid', 0.0)
        return ctx.get('ask', 0.0) or ctx.get('mid', 0.0)

    def get_tp_check_price(self, direction: str, price_ctx: Optional[Dict[str, float]] = None) -> float:
        """止盈觸發價格 (LONG 用 bid, SHORT 用 ask)"""
        return self.get_entry_price(direction, price_ctx)

    def get_sl_check_price(self, price_ctx: Optional[Dict[str, float]] = None) -> float:
        """止損觸發價格 (使用 oracle/mark)"""
        ctx = price_ctx or self._get_price_context()
        return ctx.get('oracle', 0.0) or ctx.get('mid', 0.0)

    def get_net_price_for_direction(self, direction: str, price_ctx: Optional[Dict[str, float]] = None) -> float:
        """淨成交價 (出場用)"""
        return self.get_tp_check_price(direction, price_ctx)
    
    def get_testnet_market_data(self) -> Dict:
        """獲取 Testnet 市場數據 (緩存 1 秒)"""
        if self.config.paper_mode:
            return {}
        
        now = time.time()
        if now - self._testnet_data_cache.get('_time', 0) < 1.0:
            return self._testnet_data_cache
        
        try:
                # 獲取價格偵測器的數據 (Binance Source)
                # 🔧 v13.2 fix: 使用 self.detector 而非 self.price_detector
                data = self.detector.get_market_data() if self.detector else {}
                if not data:
                     # Fallback if detector is missing
                     data = {
                         'current_price': self._get_market_price(),
                         'obi': self._get_market_ws().get_obi(),
                         'trade_imbalance': 0,
                         'price_change_1m': 0,
                         'price_change_5m': 0,
                         'big_buy_value': 0,
                         'big_sell_value': 0
                     }
                data['_time'] = now
                self._testnet_data_cache = data
                return data
        except Exception:
            pass
        
        return {}

    def analyze_market(self, force_strategy_analysis: bool = False) -> Dict:
        """
        分析市場 (分層更新)
        
        - 即時數據: 每秒更新 (價格、OBI、大單)
        - 策略分析: 每 30 秒更新 (主力策略識別)
        
        數據來源:
        - Testnet 模式: 價格使用 Testnet API，其他使用正式網 WebSocket
        - Paper 模式: 全部使用正式網 WebSocket
        
        Args:
            force_strategy_analysis: 強制執行策略分析
        """
        use_binance_paper = self._use_binance_paper_source()
        source_ws = self._get_market_ws()
        source_name = "Binance" if use_binance_paper else "dYdX"

        # 獲取完整快照 (包含大單資料)
        ws_snapshot = source_ws.get_full_snapshot()
        
        # 獲取價格 (Testnet 模式使用 Testnet API)
        current_price = self.get_current_price()

        # 更新價格歷史 (用於噪音/波動計算)
        now_ts = time.time()
        self.price_history.append((now_ts, current_price))
        # 僅保留 5 分鐘資料
        while self.price_history and now_ts - self.price_history[0][0] > 300:
            self.price_history.popleft()
        noise_1m_pct = self._get_noise_pct(60)
        noise_5m_pct = self._get_noise_pct(300)
        
        # Testnet/dYdX 模式: 嘗試獲取 OBI
        testnet_obi = None
        binance_price = None
        binance_obi = None

        if not use_binance_paper:
            # 🆕 v13.0: 優先使用 dYdX WebSocket OBI (統一資料源)
            if hasattr(self.trader, 'dydx_ws') and self.trader.dydx_ws:
                dydx_ws_obi = self.trader.dydx_ws.obi
                if self.trader.dydx_ws.last_update > 0:  # 有收到數據
                    testnet_obi = dydx_ws_obi

        # Binance 作為外部情緒來源（僅收集，不做價格基準）
        if hasattr(self, 'binance_ws') and self.binance_ws:
            if getattr(self.binance_ws, 'current_price', 0) > 0:
                binance_price = self.binance_ws.current_price
            try:
                binance_obi = self.binance_ws.get_obi()
            except Exception:
                binance_obi = None
        
        if not use_binance_paper:
            # 🆕 dYdX REST Integration (備用)
            if testnet_obi is None and self.dydx:
                try:
                    # 🔧 v14.3: 緩存機制 (3秒，減少 API 呼叫)
                    now = time.time()
                    if now - getattr(self, '_dydx_obi_time', 0) < 3.0:
                        testnet_obi = getattr(self, '_dydx_obi', 0)
                    else:
                        orderbook = asyncio.run(self.dydx.get_orderbook(self.config.symbol_dydx if hasattr(self.config, 'symbol_dydx') else "BTC-USD"))
                        if orderbook:
                            bids = orderbook.get("bids", [])
                            asks = orderbook.get("asks", [])
                            bid_vol = sum(float(b['size']) for b in bids)
                            ask_vol = sum(float(a['size']) for a in asks)
                            if (bid_vol + ask_vol) > 0:
                                obi = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                                self._dydx_obi = obi
                                self._dydx_obi_time = now
                                testnet_obi = obi
                except Exception:
                    pass

            if testnet_obi is None and not self.config.paper_mode:
                testnet_data = self.get_testnet_market_data()
                if testnet_data.get('obi') is not None:
                    testnet_obi = testnet_data['obi']

        if use_binance_paper and testnet_obi is None:
            testnet_obi = binance_obi
        
        # ====== 即時數據 (每秒更新) ======
        data_price = current_price if current_price > 0 else source_ws.current_price
        data_bid = source_ws.bid_price
        data_ask = source_ws.ask_price
        if not use_binance_paper:
            api_bid, api_ask = self._get_dydx_api_bid_ask()
            if api_bid > 0 and api_ask > 0:
                data_bid = api_bid
                data_ask = api_ask
                data_price = (api_bid + api_ask) / 2
        
        data = {
            'price': data_price,
            'obi': testnet_obi if testnet_obi is not None else source_ws.get_obi(),
            'trade_imbalance': source_ws.get_trade_imbalance_1s(),
            'price_change_1s': source_ws.get_price_change(1),
            'price_change_1m': source_ws.get_price_change(60),
            'price_change_5m': source_ws.get_price_change(300),
            'noise_1m_pct': noise_1m_pct,
            'noise_5m_pct': noise_5m_pct,
            'bid': data_bid,
            'ask': data_ask, # 注意: Binance Spread 可能比 dYdX 小
            'spread_pct': (data_ask - data_bid) / data_bid * 100 if data_bid > 0 else 0,
            
            # 🆕 v13.2: 顯式標記數據來源
            'data_source': source_name,
            'binance_price': binance_price,
            'binance_obi': binance_obi,
            'exchange_spread_pct': (abs(binance_price - data_price) / data_price * 100) if (binance_price and data_price > 0) else 0,
        }
        
        # 🔧 v14.6.35: 複製 deque 避免多執行緒競爭 (RuntimeError: deque mutated during iteration)
        try:
            big_trades_snapshot = list(source_ws.big_trades)
        except RuntimeError:
            big_trades_snapshot = []
        
        # 大單資料
        data.update({
            'big_trade_count': len(big_trades_snapshot),
            'big_buy_count': len([t for t in big_trades_snapshot if t.get('is_buy')]),
            'big_sell_count': len([t for t in big_trades_snapshot if not t.get('is_buy')]),
            'big_buy_volume': sum(t.get('qty', 0) for t in big_trades_snapshot if t.get('is_buy')),
            'big_sell_volume': sum(t.get('qty', 0) for t in big_trades_snapshot if not t.get('is_buy')),
            'big_buy_value': sum(t.get('value_usdt', 0) for t in big_trades_snapshot if t.get('is_buy')), 
            'big_sell_value': sum(t.get('value_usdt', 0) for t in big_trades_snapshot if not t.get('is_buy')),
            # 訂單簿深度
            'bid_depth': ws_snapshot.get('bid_depth', 0),
            'ask_depth': ws_snapshot.get('ask_depth', 0),
            # 🆕 資金費率
            'funding_rate': self._get_dydx_funding() if (self.dydx and not use_binance_paper) else 0,
        })
        
        # ====== 策略分析 (每 N 秒更新或強制更新) ======
        should_analyze_strategy = (
            force_strategy_analysis or 
            time.time() - self.last_strategy_analysis_time >= self.config.analysis_interval_sec
        )
        
        if should_analyze_strategy and self.detector:
            self.last_strategy_analysis_time = time.time()
            
            try:
                # 🆕 v13.2: 餵給 Detector 的數據也必須是 Binance Brain
                if use_binance_paper:
                    target_ws = self.binance_ws
                else:
                    target_ws = self.binance_ws if self.binance_ws.current_price > 0 else self.ws
                
                # 先更新數據到偵測器 (模擬 1 秒 K 線)
                candle = {
                    'open': target_ws.current_price * 0.9999,
                    'high': max(target_ws.current_price * 1.0001, target_ws.ask_price),
                    'low': min(target_ws.current_price * 0.9999, target_ws.bid_price),
                    'close': target_ws.current_price,
                    'volume': sum(t.get('value_usdt', 0) for t in list(target_ws.trades_1s)[-10:])
                }
                self.detector.update_data(
                    candle=candle,
                    bids=target_ws.bids,
                    asks=target_ws.asks
                )
                
                # 將最近的交易傳遞給偵測器
                for trade in list(target_ws.trades_1s)[-5:]:
                    self.detector.update_data(trade={
                        'volume_usdt': trade.get('value_usdt', 0),
                        'is_buy': trade.get('is_buy', False),
                        'price': trade.get('price', 0)
                    })
                
                # 執行分析
                snapshot = self.detector.analyze(
                    current_price=data['price'],
                    obi=data['obi'],
                    vpin=0,  # WebSocket 暫不計算
                    wpi=data['trade_imbalance'],  # 用交易不平衡近似
                    funding_rate=data.get('funding_rate', 0),
                    oi_change_pct=0,
                    liquidation_pressure_long=50,
                    liquidation_pressure_short=50,
                    price_change_1m_pct=data['price_change_1m'],
                    price_change_5m_pct=data['price_change_5m']
                )
                
                # 緩存策略分析結果
                self.cached_strategy_data = {
                    'snapshot': snapshot,
                    'primary_strategy': snapshot.primary_strategy,
                    'entry_signal': snapshot.entry_signal,
                    'strategy_probs': snapshot.strategy_probabilities or {},
                    'overall_bias': snapshot.overall_bias,
                    'overall_confidence': snapshot.overall_confidence,
                    'trading_allowed': snapshot.trading_allowed,
                    'key_signals': snapshot.key_signals or [],
                    'risk_warnings': snapshot.risk_warnings or [],
                    'analysis_time': time.time(),
                    # 🆕 v13.0: 資料來源追蹤
                    'has_whale_detection': bool(snapshot.strategy_probabilities),
                    'is_fallback_probability': False
                }
                
                # 即使偵測器沒有返回策略，也基於市場數據計算即時分析
                if not self.cached_strategy_data['strategy_probs']:
                    self.cached_strategy_data['strategy_probs'] = self._calculate_realtime_strategy_probs(data)
                    # 🆕 v13.0: 標記為 fallback 機率
                    self.cached_strategy_data['is_fallback_probability'] = True
                    self.cached_strategy_data['has_whale_detection'] = False
                
                # 🆕 v2.0 雙週期分析 + Hysteresis
                self._update_dual_period_analysis(self.cached_strategy_data['strategy_probs'])
                
                self.logger.info(f"🔄 策略分析更新 | 主要: {snapshot.primary_strategy.strategy.value if snapshot.primary_strategy else 'N/A'} | Regime: {self.current_regime or '觀察中'}")
                
            except Exception as e:
                self.logger.error(f"分析錯誤: {e}")
                # 出錯時也提供即時分析
                if not self.cached_strategy_data.get('strategy_probs'):
                    self.cached_strategy_data['strategy_probs'] = self._calculate_realtime_strategy_probs(data)
                    # 🆕 v13.0: 標記為 fallback 機率
                    self.cached_strategy_data['is_fallback_probability'] = True
                    self.cached_strategy_data['has_whale_detection'] = False
        
        # 🆕 v10.10/v10.19: 每秒更新快線/中線 + 六維分數
        # 以前只在有 strategy_probs 時才跑，導致六維分數缺失 → 直接每秒跑
        self._update_realtime_signals(data)
        
        # ====== 合併即時數據 + 緩存的策略數據 ======
        data.update(self.cached_strategy_data)
        
        # 計算距離下次分析的時間
        next_analysis_in = max(0, self.config.analysis_interval_sec - (time.time() - self.last_strategy_analysis_time))
        data['next_strategy_analysis'] = next_analysis_in
        
        self.market_data = data
        
        # 🆕 v13.2: 注入 dYdX 上下文 (用於 _check_hybrid_risks)
        # 這保證了 _check_hybrid_risks 總是有 dYdX 的最新數據
        if use_binance_paper:
            data['dydx_context'] = {}
        else:
            data['dydx_context'] = {
                'price': self.ws.current_price,
                'obi': self.ws.get_obi(),
                'last_update': self.ws.last_trade_time
            }
        
        # 記錄到 TensorFlow 訓練資料
        self._record_training_data(data)
        
        return data

    def _get_noise_pct(self, window_seconds: int) -> float:
        """
        計算指定時間窗口內價格的百分比標準差 (噪音強度)
        返回值為百分比，例如 0.05 代表 0.05%
        """
        if not self.price_history:
            return 0.0
        now_ts = time.time()
        prices = [p for ts, p in self.price_history if now_ts - ts <= window_seconds and p > 0]
        if len(prices) < 5:
            return 0.0
        mean_price = sum(prices) / len(prices)
        if mean_price <= 0:
            return 0.0
        variance = sum(((p - mean_price) / mean_price * 100) ** 2 for p in prices) / len(prices)
        return (variance ** 0.5)

    def _check_hybrid_risks(self, data: Dict) -> Tuple[bool, str]:
        """
        🛡️ Hybrid Strategy Risk Guards (混合策略風險防護罩)
        檢查 dYdX 執行面的風險 (價差、流動性、費率)
        
        🆕 v14.8: 整合 AdvancedRiskController 進階風控系統
        - 動態 Band (根據波動/點差/延遲調整)
        - VWAP 滑點預估
        - Oracle Gap 守門員
        - 交易狀態機 (CAN_TRADE/SUSPECT/HALT/ESCAPE)
        """
        # 0. 如果沒啟用混合模式 (純模擬或無 dYdX 連接)，直接通過
        if not getattr(self.trader, 'dydx_sync_enabled', False) or not getattr(self.trader, 'dydx_ws', None):
            return True, ""

        dydx_context = data.get('dydx_context', {})
        if not dydx_context:
            return True, "" # 無數據視為通過

        binance_price = data.get('current_price', 0)
        dydx_price = dydx_context.get('price', 0)
        
        # 🆕 v14.8: 使用 AdvancedRiskController 進行完整風控檢查
        if hasattr(self, 'spread_guard') and self.spread_guard:
            # 取得當前信號方向
            signal_direction = data.get('signal_status', {}).get('pending_direction', 'NEUTRAL')
            if not signal_direction or signal_direction == 'NEUTRAL':
                signal_direction = self.fast_signal_cache.get('direction', 'NEUTRAL')
            
            # 計算下單量 (簡化: 使用 config 的 position_size_btc)
            qty_btc = getattr(self.config, 'position_size_btc', 0.002)
            
            # 🎯 整合風控檢查
            can_open, reason, risk_info = self.spread_guard.can_open_position(signal_direction, qty_btc)
            
            # 記錄詳細資訊到 data 供 Dashboard 顯示
            data['spread_guard'] = risk_info
            data['risk_state'] = risk_info.get('state', 'UNKNOWN')
            data['band_entry'] = risk_info.get('band_entry', 0)
            data['band_halt'] = risk_info.get('band_halt', 0)
            data['effective_diff'] = risk_info.get('effective_diff', 0)
            data['oracle_gap'] = risk_info.get('oracle_gap', 0)
            
            if not can_open:
                return False, reason
            
            # SUSPECT 狀態: 記錄警告
            if risk_info.get('state') == 'SUSPECT':
                data['spread_warning'] = reason
                data['suggested_size_reduction'] = 0.5  # 建議縮倉 50%
        else:
            # Fallback: 傳統價差檢查 (Spread Check)
            if binance_price > 0 and dydx_price > 0:
                diff = abs(binance_price - dydx_price)
                if diff > 50:
                    return False, f"⚠️ 價差過大 (${diff:.1f} > $50)"

        # 2. 資金費率窗口 (Funding Window Blackout)
        # dYdX v4 每小時整點結算。避開 XX:59:00 ~ XX:01:00
        import time
        current_struct = time.localtime()
        minute = current_struct.tm_min
        if minute == 59 or minute == 0:
             return False, f"⚠️ 資金費率結算窗口 ({minute}分)"

        # 3. 流動性與價差否決 (Liquidity & Spread Veto)
        signal_direction = data.get('fast_signal_cache', {}).get('direction', 'NEUTRAL')
        dydx_obi = dydx_context.get('obi', 0)
        
        # 🆕 v14.9.7: 增加 Spread 價差濾網 (防止進場即虧損)
        # 取得 dYdX 即時價差
        dydx_bid = getattr(self.trader.dydx_ws, 'bid_price', 0)
        dydx_ask = getattr(self.trader.dydx_ws, 'ask_price', 0)
        if dydx_bid > 0 and dydx_ask > 0:
            dydx_spread_pct = (dydx_ask - dydx_bid) / dydx_price * 100
            max_spread = getattr(self.config, 'max_dydx_spread_pct', 0.02) 
            if dydx_spread_pct > max_spread:
                return False, f"⚠️ dYdX 價差過大 ({dydx_spread_pct:.3f}% > {max_spread}%)"
        
        # 🆕 v14.9.9: 增加 1s 跳價濾網 (防止在極端波動中進場)
        price_change_1s = abs(data.get('price_change_1s', 0))
        max_jump = getattr(self.config, 'max_dydx_jump_1s_pct', 0.05)
        
        # 檢查冷卻時間
        now_ts = time.time()
        if now_ts < getattr(self, '_jump_cooldown_until', 0.0):
            remaining = self._jump_cooldown_until - now_ts
            return False, f"⚠️ 價格跳動冷卻中 (剩餘 {remaining:.1f}s)"

        if price_change_1s > max_jump:
            # 觸發 20 秒冷卻
            self._jump_cooldown_until = now_ts + 20.0
            return False, f"⚠️ dYdX 價格跳動過大 ({price_change_1s:.3f}% > {max_jump}%) -> 進入 20s 冷卻"

        if signal_direction == 'LONG' and dydx_obi < -0.3:
             return False, f"⚠️ dYdX 本地賣壓過大 (OBI {dydx_obi:.2f})"
        if signal_direction == 'SHORT' and dydx_obi > 0.3:
             return False, f"⚠️ dYdX 本地買壓過大 (OBI {dydx_obi:.2f})"
             
        return True, "Passed"

    def _detect_alpha_opportunities(self, data: Dict) -> Dict:
        """
        ⚔️ Alpha Detectors (優勢攻擊偵測)
        偵測 dYdX 特有的獲利機會 (滯後、真空、費率)
        """
        alpha_flags = {
            'force_taker': False,
            'passive_maker': False,
            'alpha_reason': ""
        }
        
        if not getattr(self.trader, 'dydx_sync_enabled', False):
            return alpha_flags
            
        dydx_context = data.get('dydx_context', {})
        if not dydx_context:
            return alpha_flags
            
        binance_price = data.get('current_price', 0)
        dydx_price = dydx_context.get('price', 0)
        
        if binance_price <= 0 or dydx_price <= 0:
            return alpha_flags
            
        # 1. 滯後偵測 (Lag Detector)
        # Binance 已經跑了，dYdX 還沒動 -> aggressive taker
        price_diff = binance_price - dydx_price
        
        # 信號是用 Binance 算的，如果 Binance > dYdX $20 (且信號看多)，代表 dYdX 滯後
        # 我們應該直接 Taker 吃單
        if price_diff > 20: # Binance 比 dYdX 貴 $20
            alpha_flags['force_taker'] = True
            alpha_flags['alpha_reason'] = f"🚀 滯後套利 (Call) Diff +${price_diff:.1f}"
        elif price_diff < -20: # Binance 比 dYdX 便宜 $20
             alpha_flags['force_taker'] = True
             alpha_flags['alpha_reason'] = f"🚀 滯後套利 (Put) Diff ${price_diff:.1f}"
             
        # 2. 真空填補 (Gap/Spread Detector)
        # 如果 dYdX 買賣價差很大，我們可以掛 Maker
        # (需要 best_bid/best_ask，暫時用 OBI 近似或忽略)
        
        return alpha_flags
    
    def _calculate_realtime_strategy_probs(self, data: Dict) -> Dict[str, float]:
        """
        基於即時市場數據計算策略機率 (當偵測器沒有返回結果時使用)
        這是一個簡化的即時分析，確保 Dashboard 永遠有數據顯示
        """
        obi = data.get('obi', 0)
        wpi = data.get('trade_imbalance', 0)
        price_change_1m = data.get('price_change_1m', 0)
        price_change_5m = data.get('price_change_5m', 0)
        big_buy = data.get('big_buy_value', 0)
        big_sell = data.get('big_sell_value', 0)
        
        probs = {}
        
        # 根據 OBI (訂單簿失衡) 計算
        obi_abs = abs(obi)
        
        # 吸籌建倉: OBI 偏買 + WPI 偏買
        if obi > 0.15 and wpi > -0.1:  # 🔧 v12.11: 降低門檻 (0.2->0.15)
            probs['ACCUMULATION'] = min(0.4 + obi * 0.5 + wpi * 0.3, 0.95)
        else:
            probs['ACCUMULATION'] = max(0.05, 0.1 + obi * 0.2)
        
        # 派發出貨: OBI 偏賣 + WPI 偏賣
        if obi < -0.15 and wpi < 0.1:  # 🔧 v12.11: 降低門檻 (-0.2->-0.15)
            probs['DISTRIBUTION'] = min(0.4 + abs(obi) * 0.5 + abs(wpi) * 0.3, 0.95)
        else:
            probs['DISTRIBUTION'] = max(0.05, 0.1 + abs(obi) * 0.2 if obi < 0 else 0.05)
        
        # 鋸齒洗盤: 價格波動大但方向不明確
        volatility = abs(price_change_1m) + abs(price_change_5m)
        if volatility > 0.12 and abs(obi) < 0.25:  # 🔧 v12.11: 大幅降低波動門檻 (0.3->0.12)
            probs['WHIPSAW'] = min(0.4 + volatility * 0.8, 0.9)  # 提高基礎機率
        else:
            probs['WHIPSAW'] = 0.05
        
        # 多頭陷阱: 價格上漲但大單賣出
        if price_change_5m > 0.1 and big_sell > big_buy * 1.2:  # 🔧 v12.11: 降低價格門檻 (0.2->0.1) 和大單比例 (1.5->1.2)
            probs['BULL_TRAP'] = min(0.4 + price_change_5m * 0.5, 0.8)
        else:
            probs['BULL_TRAP'] = 0.05
        
        # 空頭陷阱: 價格下跌但大單買入
        if price_change_5m < -0.1 and big_buy > big_sell * 1.2:  # 🔧 v12.11: 降低價格門檻 (-0.2->-0.1) 和大單比例 (1.5->1.2)
            probs['BEAR_TRAP'] = min(0.4 + abs(price_change_5m) * 0.5, 0.8)
        else:
            probs['BEAR_TRAP'] = 0.05
        
        # 趨勢推動: 強方向 + 大單配合
        if wpi > 0.3 and big_buy > big_sell:  # 🔧 v12.11: 降低 WPI 門檻 (0.5->0.3)
            probs['MOMENTUM_PUSH'] = min(0.3 + wpi * 0.4, 0.7)
        elif wpi < -0.3 and big_sell > big_buy:
            probs['MOMENTUM_PUSH'] = min(0.3 + abs(wpi) * 0.4, 0.7)
        else:
            probs['MOMENTUM_PUSH'] = 0.05
        
        # 獵殺止損: 快速價格波動
        if abs(price_change_1m) > 0.15:  # 🔧 v12.11: 降低價格門檻 (0.3->0.15)
            probs['STOP_HUNT'] = min(0.3 + abs(price_change_1m) * 0.8, 0.8)
        else:
            probs['STOP_HUNT'] = 0.05
        
        # 正常波動 (基礎) - 隨波動率降低而升高
        probs['NORMAL'] = max(0.1, 0.35 - volatility * 0.5 - obi_abs * 0.2)
        
        # 其他策略設為低機率
        for strategy in ['FAKEOUT', 'SPOOFING', 'CONSOLIDATION_SHAKE', 'FLASH_CRASH', 
                         'SLOW_BLEED', 'RE_ACCUMULATION', 'RE_DISTRIBUTION',
                         'LONG_SQUEEZE', 'SHORT_SQUEEZE', 'CASCADE_LIQUIDATION',
                         'TREND_CONTINUATION', 'REVERSAL', 'PUMP_DUMP', 
                         'WASH_TRADING', 'LAYERING']:
            if strategy not in probs:
                probs[strategy] = 0.02
        
        return probs

    def _update_dual_period_analysis(self, strategy_probs: Dict[str, float]):
        """
        🆕 v10.10 三週期分析 + Hysteresis
        
        1. 快線 (5秒): 極短期信號，用於即時判斷
        2. 中線 (30秒): 即時強弱指標
        3. 慢線 (5分鐘): 主力 game plan
        4. Hysteresis: 策略需連續 N 次確認才切換
        5. 無主力狀態: 低分散時顯示觀望
        """
        now = time.time()
        
        # ====== 1. 更新快線歷史 (最近 5 秒) ======
        self.fast_strategy_history.append({
            'timestamp': now,
            'probs': strategy_probs.copy()
        })
        # 清理過期數據
        self.fast_strategy_history = [
            h for h in self.fast_strategy_history 
            if now - h['timestamp'] < self.config.fast_window_seconds
        ]
        
        # ====== 1.5. 更新中線歷史 (最近 30 秒) ======
        self.medium_strategy_history.append({
            'timestamp': now,
            'probs': strategy_probs.copy()
        })
        # 清理過期數據
        self.medium_strategy_history = [
            h for h in self.medium_strategy_history 
            if now - h['timestamp'] < self.config.medium_window_seconds
        ]
        
        # ====== 2. 更新慢線歷史 (最近 5 分鐘) ======
        self.slow_strategy_history.append({
            'timestamp': now,
            'probs': strategy_probs.copy()
        })
        # 清理過期數據
        self.slow_strategy_history = [
            h for h in self.slow_strategy_history 
            if now - h['timestamp'] < self.config.slow_window_seconds
        ]
        
        # ====== 2.5. 計算快線加權平均機率 (5秒極短期) ======
        fast_avg_probs = self._calculate_weighted_avg_probs(self.fast_strategy_history, half_life=2.5)
        
        # ====== 2.6. 計算中線加權平均機率 (30秒短期) ======
        medium_avg_probs = self._calculate_weighted_avg_probs(self.medium_strategy_history, half_life=15)
        
        # ====== 3. 計算慢線加權平均機率 (主力 game plan) ======
        slow_avg_probs = self._calculate_weighted_avg_probs(self.slow_strategy_history)
        
        # ====== 3.5. 計算快線信號 (5秒判斷) ======
        fast_sorted = sorted(fast_avg_probs.items(), key=lambda x: x[1], reverse=True) if fast_avg_probs else []
        fast_top1 = fast_sorted[0] if fast_sorted else (None, 0)
        
        # 快線多空判斷
        fast_long_strategies = {'ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 'SHORT_SQUEEZE', 'FLASH_CRASH'}
        fast_short_strategies = {'DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 'LONG_SQUEEZE', 'PUMP_DUMP'}
        fast_long_prob = sum(fast_avg_probs.get(s, 0) for s in fast_long_strategies)
        fast_short_prob = sum(fast_avg_probs.get(s, 0) for s in fast_short_strategies)
        fast_direction = 'LONG' if fast_long_prob > fast_short_prob else 'SHORT' if fast_short_prob > fast_long_prob else 'NEUTRAL'
        fast_advantage = abs(fast_long_prob - fast_short_prob)
        
        # 快線信號緩存
        self.fast_signal_cache = {
            'timestamp': now,
            'top_strategy': fast_top1[0],
            'top_prob': fast_top1[1],
            'long_prob': fast_long_prob,
            'short_prob': fast_short_prob,
            'direction': fast_direction,
            'advantage': fast_advantage,
            'sample_count': len(self.fast_strategy_history)
        }
        
        # ====== 3.6. 計算中線信號 (30秒判斷) ======
        medium_sorted = sorted(medium_avg_probs.items(), key=lambda x: x[1], reverse=True) if medium_avg_probs else []
        medium_top1 = medium_sorted[0] if medium_sorted else (None, 0)
        medium_long_prob = sum(medium_avg_probs.get(s, 0) for s in fast_long_strategies)
        medium_short_prob = sum(medium_avg_probs.get(s, 0) for s in fast_short_strategies)
        medium_direction = 'LONG' if medium_long_prob > medium_short_prob else 'SHORT' if medium_short_prob > medium_long_prob else 'NEUTRAL'
        medium_advantage = abs(medium_long_prob - medium_short_prob)
        
        # ====== 4. 找出慢線排名前兩名的策略 ======
        sorted_probs = sorted(slow_avg_probs.items(), key=lambda x: x[1], reverse=True)
        
        if len(sorted_probs) >= 2:
            top1_strategy, top1_prob = sorted_probs[0]
            top2_strategy, top2_prob = sorted_probs[1]
            lead_gap = top1_prob - top2_prob
        elif len(sorted_probs) == 1:
            top1_strategy, top1_prob = sorted_probs[0]
            top2_strategy, top2_prob = None, 0
            lead_gap = top1_prob
        else:
            top1_strategy, top1_prob = None, 0
            top2_strategy, top2_prob = None, 0
            lead_gap = 0
        
        # ====== 5. 判斷是否為「無明顯主力」狀態 ======
        # 🔧 修正 NoneType 比較錯誤
        min_dominant_prob = getattr(self.config, 'min_dominant_prob', None) or 0.30
        min_lead_gap = getattr(self.config, 'min_lead_gap', None) or 0.10
        
        is_no_dominant = (
            top1_prob < min_dominant_prob or  # 第一名太弱
            lead_gap < min_lead_gap           # 領先差距太小
        )
        
        # ====== 6. Hysteresis 狀態切換 ======
        # 🔧 修正 NoneType 比較錯誤
        actionable_prob_threshold = getattr(self.config, 'actionable_prob_threshold', None) or 0.50
        
        if is_no_dominant:
            # 無明顯主力 → 重置等待狀態
            candidate_regime = "NO_DOMINANT"
        elif top1_prob >= actionable_prob_threshold:
            # 機率夠高 → 候選新狀態
            candidate_regime = top1_strategy
        else:
            # 機率不夠 → 還在觀察
            candidate_regime = "OBSERVING"
        
        # 檢查是否要切換 regime
        if candidate_regime == self.pending_regime:
            # 連續確認中
            self.regime_confirm_count += 1
        else:
            # 新的候選狀態
            self.pending_regime = candidate_regime
            self.regime_confirm_count = 1
        
        # 達到確認次數 → 正式切換
        # 🔧 修正 NoneType 比較錯誤
        strategy_confirm_count = getattr(self.config, 'strategy_confirm_count', None) or 3
        if self.regime_confirm_count >= strategy_confirm_count:
            if self.current_regime != candidate_regime:
                old_regime = self.current_regime
                self.current_regime = candidate_regime
                self.last_regime_change = now
                
                # 記錄歷史
                self.regime_history.append({
                    'timestamp': now,
                    'from': old_regime,
                    'to': candidate_regime,
                    'top1_prob': top1_prob,
                    'lead_gap': lead_gap
                })
                
                self.logger.info(f"🔄 主力狀態切換: {old_regime} → {candidate_regime} (機率: {top1_prob:.1%}, 領先: {lead_gap:.1%})")
        
        # ====== 7. 更新緩存數據供 Dashboard 顯示 ======
        self.cached_strategy_data['dual_period'] = {
            'fast_window': self.config.fast_window_seconds,
            'medium_window': self.config.medium_window_seconds,
            'slow_window': self.config.slow_window_seconds,
            'fast_count': len(self.fast_strategy_history),
            'medium_count': len(self.medium_strategy_history),
            'slow_count': len(self.slow_strategy_history),
            'fast_avg_probs': fast_avg_probs,
            'medium_avg_probs': medium_avg_probs,
            'slow_avg_probs': slow_avg_probs,
            # 🆕 v10.10: 快線信號
            'fast_top_strategy': fast_top1[0],
            'fast_top_prob': fast_top1[1],
            'fast_direction': fast_direction,
            'fast_long_prob': fast_long_prob,
            'fast_short_prob': fast_short_prob,
            'fast_advantage': fast_advantage,
            # 🆕 v10.10: 中線信號
            'medium_top_strategy': medium_top1[0],
            'medium_top_prob': medium_top1[1],
            'medium_direction': medium_direction,
            'medium_long_prob': medium_long_prob,
            'medium_short_prob': medium_short_prob,
            'medium_advantage': medium_advantage,
            # 慢線信號
            'top1': top1_strategy,
            'top1_prob': top1_prob,
            'top2': top2_strategy,
            'top2_prob': top2_prob,
            'lead_gap': lead_gap,
            'current_regime': self.current_regime,
            'pending_regime': self.pending_regime,
            'regime_confirm_count': self.regime_confirm_count,
            'regime_confirm_required': strategy_confirm_count,
            'is_no_dominant': is_no_dominant,
            'is_actionable': (
                not is_no_dominant and 
                top1_prob >= actionable_prob_threshold and
                self.current_regime == candidate_regime
            )
        }
    
    def _calculate_weighted_avg_probs(self, history: List[Dict], half_life: float = 300) -> Dict[str, float]:
        """
        計算加權平均機率 (越新的權重越高)
        
        Args:
            history: 歷史數據列表
            half_life: 權重半衰期 (秒)，越小則越重視新數據
        """
        if not history:
            return {}
        
        # 使用指數衰減權重
        now = time.time()
        total_weight = 0
        weighted_probs: Dict[str, float] = {}
        
        for h in history:
            # 時間權重: 越新越重要 
            age = now - h['timestamp']
            weight = 2 ** (-age / half_life)
            total_weight += weight
            
            for strategy, prob in h['probs'].items():
                if strategy not in weighted_probs:
                    weighted_probs[strategy] = 0
                weighted_probs[strategy] += prob * weight
        
        # 正規化
        if total_weight > 0:
            for strategy in weighted_probs:
                weighted_probs[strategy] /= total_weight
        
        return weighted_probs

    def _update_realtime_signals(self, data: Dict):
        """
        🆕 v10.10: 每秒更新即時快線/中線信號
        這個函數在每次 analyze_market() 時都會被調用
        即使沒有完整的 30 秒策略分析，也能提供即時判斷
        """
        now = time.time()
        
        # 使用即時市場數據計算一個簡化的策略機率
        realtime_probs = self._calculate_realtime_strategy_probs(data)
        
        # 更新快線歷史 (5秒窗口)
        self.fast_strategy_history.append({
            'timestamp': now,
            'probs': realtime_probs.copy()
        })
        self.fast_strategy_history = [
            h for h in self.fast_strategy_history 
            if now - h['timestamp'] < self.config.fast_window_seconds
        ]
        
        # 更新中線歷史 (30秒窗口)
        self.medium_strategy_history.append({
            'timestamp': now,
            'probs': realtime_probs.copy()
        })
        self.medium_strategy_history = [
            h for h in self.medium_strategy_history 
            if now - h['timestamp'] < self.config.medium_window_seconds
        ]
        
        # 計算快線信號
        fast_avg = self._calculate_weighted_avg_probs(self.fast_strategy_history, half_life=2.5)
        fast_long_strategies = {'ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 'SHORT_SQUEEZE', 'FLASH_CRASH'}
        fast_short_strategies = {'DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 'LONG_SQUEEZE', 'PUMP_DUMP'}
        
        fast_long_prob = sum(fast_avg.get(s, 0) for s in fast_long_strategies)
        fast_short_prob = sum(fast_avg.get(s, 0) for s in fast_short_strategies)
        fast_direction = 'LONG' if fast_long_prob > fast_short_prob else 'SHORT' if fast_short_prob > fast_long_prob else 'NEUTRAL'
        fast_advantage = abs(fast_long_prob - fast_short_prob)
        
        # 計算中線信號
        medium_avg = self._calculate_weighted_avg_probs(self.medium_strategy_history, half_life=15)
        medium_long_prob = sum(medium_avg.get(s, 0) for s in fast_long_strategies)
        medium_short_prob = sum(medium_avg.get(s, 0) for s in fast_short_strategies)
        medium_direction = 'LONG' if medium_long_prob > medium_short_prob else 'SHORT' if medium_short_prob > medium_long_prob else 'NEUTRAL'
        medium_advantage = abs(medium_long_prob - medium_short_prob)
        
        # 找出最高機率策略
        fast_sorted = sorted(fast_avg.items(), key=lambda x: x[1], reverse=True) if fast_avg else []
        medium_sorted = sorted(medium_avg.items(), key=lambda x: x[1], reverse=True) if medium_avg else []
        
        fast_top1 = fast_sorted[0] if fast_sorted else (None, 0)
        medium_top1 = medium_sorted[0] if medium_sorted else (None, 0)
        
        # 更新緩存供 Dashboard 顯示
        if 'dual_period' not in self.cached_strategy_data:
            self.cached_strategy_data['dual_period'] = {}
        
        self.cached_strategy_data['dual_period'].update({
            # 快線 (5秒)
            'fast_window': self.config.fast_window_seconds,
            'fast_count': len(self.fast_strategy_history),
            'fast_avg_probs': fast_avg,
            'fast_top_strategy': fast_top1[0],
            'fast_top_prob': fast_top1[1],
            'fast_direction': fast_direction,
            'fast_long_prob': fast_long_prob,
            'fast_short_prob': fast_short_prob,
            'fast_advantage': fast_advantage,
            # 中線 (30秒)
            'medium_window': self.config.medium_window_seconds,
            'medium_count': len(self.medium_strategy_history),
            'medium_avg_probs': medium_avg,
            'medium_top_strategy': medium_top1[0],
            'medium_top_prob': medium_top1[1],
            'medium_direction': medium_direction,
            'medium_long_prob': medium_long_prob,
            'medium_short_prob': medium_short_prob,
            'medium_advantage': medium_advantage,
        })
        
        # 更新快線信號緩存
        self.fast_signal_cache = {
            'timestamp': now,
            'top_strategy': fast_top1[0],
            'top_prob': fast_top1[1],
            'long_prob': fast_long_prob,
            'short_prob': fast_short_prob,
            'direction': fast_direction,
            'advantage': fast_advantage,
            'sample_count': len(self.fast_strategy_history)
        }
        
        # 🆕 v10.19: 更新六維分數 (修復 bug - 確保在 should_enter 之前計算)
        self._update_six_dim_analysis(data)

    def _update_six_dim_analysis(self, data: Dict):
        """
        🆕 v10.19: 每秒更新六維分數分析
        
        這個函數從 render_dashboard 中提取出來，確保六維分數在進場決策前就計算好。
        修復問題: 原本六維分數只在 render_dashboard 時計算，但 should_enter 在之前執行，
        導致六維分數永遠是 0。
        """
        now = time.time()
        
        # 從 cached_strategy_data 獲取三線方向
        dual_period = self.cached_strategy_data.get('dual_period', {})
        
        # 快線/中線方向 (剛在 _update_realtime_signals 更新)
        fast_dir = dual_period.get('fast_direction', 'NEUTRAL')
        med_dir = dual_period.get('medium_direction', 'NEUTRAL')
        
        # 🔧 v10.19 fix: 直接使用 self.current_regime，不從 dual_period 讀取
        # 因為 dual_period 在 _update_realtime_signals 時還沒有 current_regime
        slow_regime = self.current_regime or 'N/A'
        slow_long_strategies = {'ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 'SHORT_SQUEEZE', 'FLASH_CRASH'}
        slow_short_strategies = {'DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 'LONG_SQUEEZE', 'PUMP_DUMP'}
        slow_neutral_strategies = {'CONSOLIDATION_SHAKE', 'FAKE_BREAKOUT', 'RETEST_SUPPORT', 'RETEST_RESISTANCE'}
        
        if slow_regime in slow_long_strategies:
            slow_dir = 'LONG'
        elif slow_regime in slow_short_strategies:
            slow_dir = 'SHORT'
        elif slow_regime in slow_neutral_strategies:
            obi_now = data.get('obi', 0)
            if obi_now > 0.3:
                slow_dir = 'LONG_WEAK'
            elif obi_now < -0.3:
                slow_dir = 'SHORT_WEAK'
            else:
                slow_dir = 'NEUTRAL'
        else:
            slow_dir = 'NEUTRAL'
        
        # 計算多空分數 (快線+中線+慢線各貢獻)
        long_score = 0
        short_score = 0
        
        # 快線 (5秒) - 1分
        if fast_dir == 'LONG': long_score += 1
        elif fast_dir == 'SHORT': short_score += 1
        
        # 中線 (30秒) - 2分
        if med_dir == 'LONG': long_score += 2
        elif med_dir == 'SHORT': short_score += 2
        
        # 慢線 (5分) - 3分或1分(弱)
        if slow_dir == 'LONG': long_score += 3
        elif slow_dir == 'SHORT': short_score += 3
        elif slow_dir == 'LONG_WEAK': long_score += 1
        elif slow_dir == 'SHORT_WEAK': short_score += 1
        
        # 🆕 新增三維信號 (OBI + 動能 + 成交量)
        obi = data.get('obi', 0)
        # 🔧 v14.16: 動能線改用 1 分鐘價格變化 (原 5 分鐘太滯後)
        # 這樣能更快反應價格趨勢，避免「價格跌但六維看多」的問題
        price_change_1m = data.get('price_change_1m', 0)
        big_buy_value = data.get('big_buy_value', 0)
        big_sell_value = data.get('big_sell_value', 0)
        
        # OBI 線方向判斷 (±2分)
        obi_dir = 'NEUTRAL'
        if obi > self.config.obi_long_threshold:
            obi_dir = 'LONG'
            long_score += self.config.obi_line_weight
        elif obi < self.config.obi_short_threshold:
            obi_dir = 'SHORT'
            short_score += self.config.obi_line_weight
        
        # 動能線方向判斷 (±2分) - 🔧 v14.16: 改用 1 分鐘價格變化
        momentum_dir = 'NEUTRAL'
        if price_change_1m > self.config.momentum_long_threshold:
            momentum_dir = 'LONG'
            long_score += self.config.momentum_line_weight
        elif price_change_1m < self.config.momentum_short_threshold:
            momentum_dir = 'SHORT'
            short_score += self.config.momentum_line_weight
        
        # 成交量線方向判斷 (±2分)
        volume_dir = 'NEUTRAL'
        volume_ratio = big_buy_value / big_sell_value if big_sell_value > 0 else 1.0
        if volume_ratio > self.config.volume_long_threshold:
            volume_dir = 'LONG'
            long_score += self.config.volume_line_weight
        elif volume_ratio < self.config.volume_short_threshold:
            volume_dir = 'SHORT'
            short_score += self.config.volume_line_weight
        
        # 保存六維數據供 should_enter 使用
        six_dim_data = {
            'fast_dir': fast_dir,
            'medium_dir': med_dir,
            'slow_dir': slow_dir,
            'obi_dir': obi_dir,
            'obi_value': obi,
            'momentum_dir': momentum_dir,
            'momentum_value': price_change_1m,  # 🔧 v14.16: 改用 1 分鐘價格變化
            'volume_dir': volume_dir,
            'volume_ratio': volume_ratio,
            'long_score': long_score,
            'short_score': short_score,
            'score': max(long_score, short_score),
        }
        # 將六維數據寫入本次分析的 data，避免被稍後的 self.market_data = data 覆蓋掉
        data['six_dim'] = six_dim_data
        
        # 同步到 signal_status 供交易紀錄使用
        if 'signal_status' not in data:
            data['signal_status'] = {}
        data['signal_status']['six_dim'] = six_dim_data
        
        # 🆕 v10.19: 更新對齊時間 (用於競爭判定)
        alignment_threshold = self.config.six_dim_alignment_threshold if self.config.six_dim_enabled else 4
        
        # 🔧 v10.19 fix9: 多空互斥 - 只有一方能累積，另一方要衰減
        # 決定哪一方更強
        long_stronger = long_score > short_score
        short_stronger = short_score > long_score
        neutral = long_score == short_score
        
        # 多方對齊: score >= threshold 且 多方更強（或相等時都不累積）
        if long_score >= alignment_threshold and long_stronger:
            if self.long_alignment_start == 0:
                self.long_alignment_start = now
            self.long_alignment_seconds = now - self.long_alignment_start
            # 🔧 同時清除空方累積
            self.short_alignment_start = 0
            self.short_alignment_seconds = max(0, self.short_alignment_seconds - 1.0)  # 加速衰減
        elif not short_stronger:  # 不是空方更強時，多方正常衰減
            self.long_alignment_start = 0
            self.long_alignment_seconds = max(0, self.long_alignment_seconds - 0.5)
        
        # 空方對齊: score >= threshold 且 空方更強
        if short_score >= alignment_threshold and short_stronger:
            if self.short_alignment_start == 0:
                self.short_alignment_start = now
            self.short_alignment_seconds = now - self.short_alignment_start
            # 🔧 同時清除多方累積
            self.long_alignment_start = 0
            self.long_alignment_seconds = max(0, self.long_alignment_seconds - 1.0)  # 加速衰減
        elif not long_stronger:  # 不是多方更強時，空方正常衰減
            self.short_alignment_start = 0
            self.short_alignment_seconds = max(0, self.short_alignment_seconds - 0.5)

    def _record_training_data(self, data: Dict):
        """
        記錄 TensorFlow 訓練資料 (每次分析都記錄)
        """
        record = {
            'timestamp': datetime.now().isoformat(),
            'price': data.get('price', 0),
            'obi': data.get('obi', 0),
            'trade_imbalance': data.get('trade_imbalance', 0),
            'price_change_1m': data.get('price_change_1m', 0),
            'price_change_5m': data.get('price_change_5m', 0),
            'spread_pct': data.get('spread_pct', 0),
            'bid_depth': data.get('bid_depth', 0),
            'ask_depth': data.get('ask_depth', 0),
            # 大單資料
            'big_trade_count': data.get('big_trade_count', 0),
            'big_buy_count': data.get('big_buy_count', 0),
            'big_sell_count': data.get('big_sell_count', 0),
            'big_buy_volume': data.get('big_buy_volume', 0),
            'big_sell_volume': data.get('big_sell_volume', 0),
            'big_buy_value': data.get('big_buy_value', 0),
            'big_sell_value': data.get('big_sell_value', 0),
            # 策略機率
            'strategy_probs': data.get('strategy_probs', {}),
            # 信號
            'has_signal': data.get('entry_signal') is not None,
            'primary_strategy': data.get('primary_strategy').strategy.name if data.get('primary_strategy') else None,
        }
        
        self.training_records.append(record)
        
        # 每 100 筆保存一次
        if len(self.training_records) % 100 == 0:
            self._save_training_data()
    
    def _save_training_data(self):
        """
        保存 TensorFlow 訓練資料
        
        存儲位置:
        - data/tensorflow_training/raw/market_snapshots_{date}.jsonl (市場快照)
        - data/tensorflow_training/raw/trade_records_{date}.json (交易記錄)
        """
        if not self.training_records:
            return
        
        date_str = datetime.now().strftime('%Y%m%d')
        tf_dir = Path("data/tensorflow_training/raw")
        tf_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. 保存市場快照 (JSONL 格式 - 每行一筆)
        snapshot_file = tf_dir / f"market_snapshots_{date_str}.jsonl"
        with open(snapshot_file, 'a') as f:  # 追加模式
            for record in self.training_records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        
        # 2. 保存交易記錄
        trades_file = tf_dir / f"trade_records_{date_str}.json"
        trades_data = {
            'trades': [t.to_dict() for t in self.trader.trades] if hasattr(self.trader, 'trades') else [],
            'total_trades': len(self.trader.trades) if hasattr(self.trader, 'trades') else 0,
            'last_updated': datetime.now().isoformat(),
            'config': {
                'leverage': self.config.leverage,
                'position_size_usdt': self.config.position_size_usdt,
                'target_profit_pct': self.config.target_profit_pct,
                'stop_loss_pct': self.config.stop_loss_pct
            }
        }
        with open(trades_file, 'w') as f:
            json.dump(trades_data, f, indent=2, ensure_ascii=False)
        
        # 3. 同時保存一份到 logs (備份)
        backup_data = {
            'records': self.training_records[-100:],  # 只保留最近 100 筆
            'total_records': len(self.training_records),
            'last_updated': datetime.now().isoformat(),
            'trades': trades_data['trades']
        }
        with open(self.training_file, 'w') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        
        # 清空已保存的記錄 (避免記憶體爆炸)
        saved_count = len(self.training_records)
        self.training_records = []
        
        print(f"💾 已保存 {saved_count} 筆訓練資料到 {tf_dir}")
    
    def should_enter(self) -> tuple[bool, str, Dict]:
        """
        評估是否應該進場 (帶信號穩定性驗證)
        
        📌 v2.1 單週期模式:
        - 使用 30 秒即時策略分析
        - 保留即時數據驗證 (OBI, WPI, 價格變動)
        - 保留信號穩定性追蹤
        
        📌 v8.0 MTF-First 模式:
        - 以 15 分鐘時間框架為主
        - 不依賴主力策略偵測
        - 直接從 MTF 分析器生成信號
        
        Returns:
            (是否進場, 方向, 市場數據)
        """
        data = self.market_data
        
        # 🎲 隨機進場模式 - 繞過所有過濾，只靠風控 (平衡版)
        # 🔧 v14.1.1: 只有在可以交易時才消耗隨機方向，避免方向被浪費
        # 🔧 v14.16: 隨機進場也加入 Veto 檢查，避免在極端行情下進場
        if self.config.random_entry_mode:
            balance_enabled = getattr(self.config, 'random_entry_balance_enabled', True) is not False

            # 🔧 v14.16.3: Debug log
            if self.iteration % 10 == 0:
                self.logger.info(f"🎲 隨機進場模式檢查中... (Iteration {self.iteration})")

            runtime_seconds = (datetime.now() - self.trader.session_start_time).total_seconds()
            if runtime_seconds < self.config.warmup_seconds:
                remaining = self.config.warmup_seconds - runtime_seconds
                self.market_data['signal_status'] = {
                    'mode': '🎲 隨機進場 (平衡)',
                    'direction': None,
                    'reject_reason': f"🔄 Warm-up 期 ({remaining:.0f}秒後開放交易)",
                    'queue_remaining': self._get_active_wave_remaining() if balance_enabled else 0,
                }
                return False, "", data
            
            # 先檢查是否可以交易
            can_trade, trade_reason = self.trader.can_trade()
            if not can_trade:
                queue_remaining = self._get_active_wave_remaining() if balance_enabled else 0
                if self.iteration % 10 == 0:
                    self.logger.info(f"⏸️ 隨機進場暫停: {trade_reason}")
                # 不能交易時，只返回 False，不消耗隨機方向
                self.market_data['signal_status'] = {
                    'mode': '🎲 隨機進場 (平衡)',
                    'direction': None,
                    'reject_reason': trade_reason,
                    'queue_remaining': queue_remaining,
                }
                return False, "", data

            max_spread_pct = getattr(self.config, 'max_dydx_spread_pct', None)
            if max_spread_pct is not None:
                bid = data.get('bid', 0) or 0.0
                ask = data.get('ask', 0) or 0.0
                mid = data.get('price', 0) or 0.0
                if bid > 0 and ask > 0:
                    if mid <= 0:
                        mid = (bid + ask) / 2
                    spread_pct = (ask - bid) / mid * 100 if mid > 0 else 0.0
                    spread_bps = spread_pct * 100
                    max_spread_bps = max_spread_pct * 100
                    if spread_pct > max_spread_pct:
                        self.market_data['signal_status'] = {
                            'mode': '🎲 隨機進場 (平衡)',
                            'direction': None,
                            'reject_reason': f"點差過大 ({spread_bps:.2f}bps > {max_spread_bps:.2f}bps)",
                            'queue_remaining': self._get_active_wave_remaining() if balance_enabled else 0,
                        }
                        return False, "", data
                else:
                    self.market_data['signal_status'] = {
                        'mode': '🎲 隨機進場 (平衡)',
                        'direction': None,
                        'reject_reason': "點差資料不足 (bid/ask 缺失)",
                        'queue_remaining': self._get_active_wave_remaining() if balance_enabled else 0,
                    }
                    return False, "", data
            
            # 可以交易，獲取下一個隨機方向
            direction = self._get_balanced_random_direction(data)
            
            # 🆕 v14.16: 隨機進場也需要通過 Veto 檢查 (動能竭盡、趨勢逆向等)
            # 🔧 v14.16.2: 隨機進場模式下忽略六維分數檢查
            if getattr(self.config, 'entry_veto_enabled', True):
                veto_passed, veto_reason = self.trader.check_entry_veto(direction, data, ignore_score=True)
                if not veto_passed:
                    # Veto 失敗，不消耗方向，放回隊列末尾（避免隊列頭部卡死）
                    if balance_enabled:
                        self._return_random_direction(direction)
                    if self.iteration % 10 == 0:
                        self.logger.info(f"🛡️ 隨機進場 Veto 阻擋: {veto_reason} (方向 {direction} 已移至隊列末尾)")
                    self.market_data['signal_status'] = {
                        'mode': '🎲 隨機進場 (平衡)',
                        'direction': direction,
                        'reject_reason': f"Veto 阻擋: {veto_reason}",
                        'queue_remaining': self._get_active_wave_remaining() if balance_enabled else 0,
                    }
                    return False, "", data

            # 🔧 v14.17: random_entry_mode 預設即為純隨機（除非明確設 random_entry_pure=false）
            random_entry_pure = getattr(self.config, 'random_entry_pure', None) is not False
            if random_entry_pure:
                # 設定 signal_status 供 Dashboard / 風控守門員使用
                self.market_data['signal_status'] = {
                    'mode': '🎲 隨機進場 (純隨機)',
                    'direction': direction,
                    'pending_direction': direction,
                    'reject_reason': None,
                    'queue_remaining': self._get_active_wave_remaining() if balance_enabled else 0,
                }

                # 🛡️ 仍保留 dYdX 交易面風控 (價差/跳價/資金費率窗口等)
                passed, reject_reason = self._check_hybrid_risks(data)
                if not passed:
                    if balance_enabled:
                        self._return_random_direction(direction)
                    self.market_data['signal_status']['reject_reason'] = reject_reason
                    return False, "", data

                return True, direction, data

            # 可以交易且通過 Veto
            self.logger.info(f"🚀 隨機進場觸發: {direction}")
        
        # 🆕 v13.2: 執行混合策略風險檢查 (Hybrid Risk Guards)
        passed, reject_reason = self._check_hybrid_risks(data)
        if not passed:
            self.market_data['signal_status']['reject_reason'] = reject_reason
            return False, "", data

        # 🔧 v13.4: Consecutive Loss Cooldown Check (Disabled by User)
        # if self.consecutive_losses >= self.config.max_consecutive_losses:
        #     pass

        # 🔧 v13.4: OBI Consistency Check (Global)
        if self.config.obi_consistency_check:
            obi = data.get('obi', 0)
            # If attempting to calculate potential direction based on strategy?
            # We don't know direction yet. This check is best done AFTER determining direction.
            # But we can check extreme paradoxes.
            pass

        # 🆕 v13.2: 偵測 Alpha 機會 (Lag/Gap)
        alpha_flags = self._detect_alpha_opportunities(data)
        if alpha_flags['force_taker']:
             data['alpha_flags'] = alpha_flags

        # 🆕 v9.0: 情境式策略模式分流
        if self.config.contextual_mode:
            return self._should_enter_contextual()
        
        # 🆕 v8.0: MTF-First 模式分流
        if self.config.mtf_first_mode:
            return self._should_enter_mtf_first()
        
        # The original `data = self.market_data` is now redundant due to the insertion point.
        # It's removed to avoid reassigning `data` after it's been used by the new checks.
        strategy_probs = data.get('strategy_probs', {})
        
        # 🆕 v12.11: Warm-up 期檢查 (C方案)
        runtime_seconds = (datetime.now() - self.trader.session_start_time).total_seconds()
        if runtime_seconds < self.config.warmup_seconds:
            remaining = self.config.warmup_seconds - runtime_seconds
            # 初始化 signal_status
            self.market_data['signal_status'] = {
                'reject_reason': f"🔄 Warm-up 期 ({remaining:.0f}秒後開放交易)",
                'long_prob': 0, 'short_prob': 0, 'signal_advantage': 0,
            }
            return False, "", data
        
        # 🆕 v13.0: Fallback Veto - 若無主力偵測，直接觀望
        is_fallback = data.get('is_fallback_probability', False)
        has_whale = data.get('has_whale_detection', True)  # 預設 True 避免誤阻擋
        
        if is_fallback or not has_whale:
            self.market_data['signal_status'] = {
                'reject_reason': "⏸️ 無主力偵測結果，觀望中",
                'long_prob': 0, 'short_prob': 0, 'signal_advantage': 0,
            }
            return False, "", data
        
        # 🆕 v13.0: Regime Veto - 若無確認主力狀態，觀望
        if not self.current_regime:
            self.market_data['signal_status'] = {
                'reject_reason': "⏸️ 無明顯主力活動，觀望中",
                'long_prob': 0, 'short_prob': 0, 'signal_advantage': 0,
            }
            return False, "", data
        
        long_prob = strategy_probs.get('ACCUMULATION', 0) + strategy_probs.get('RE_ACCUMULATION', 0)
        short_prob = strategy_probs.get('DISTRIBUTION', 0) + strategy_probs.get('RE_DISTRIBUTION', 0)
        
        # 計算方向優勢 (用於 Dashboard 顯示)
        signal_advantage = abs(long_prob - short_prob)
        
        # 更新信號狀態到 market_data (用於 Dashboard 顯示)
        self.market_data['signal_status'] = {
            'long_prob': long_prob,
            'short_prob': short_prob,
            'signal_advantage': signal_advantage,
            'pending_direction': None,
            'confirm_progress': 0,
            'confirm_required': self.config.signal_confirm_seconds,
            'is_stable': False,
            'reject_reason': None,
            # 單週期模式 - 不顯示 regime
            'current_regime': None,
            'regime_confirm': None
        }
        
        # ============ 單週期：從即時 probs 找最佳策略 ============
        
        # 定義可交易策略及其方向
        tradeable_strategies = {
            # 做多策略
            'ACCUMULATION': 'LONG',      # 吸籌建倉
            'RE_ACCUMULATION': 'LONG',   # 再吸籌
            'BEAR_TRAP': 'LONG',         # 空頭陷阱
            'SHORT_SQUEEZE': 'LONG',     # 空頭擠壓
            'FLASH_CRASH': 'LONG',       # 閃崩洗盤
            # 做空策略
            'DISTRIBUTION': 'SHORT',     # 派發出貨
            'RE_DISTRIBUTION': 'SHORT',  # 再派發
            'BULL_TRAP': 'SHORT',        # 多頭陷阱
            'LONG_SQUEEZE': 'SHORT',     # 多頭擠壓
            'PUMP_DUMP': 'SHORT',        # 拉高出貨
        }
        
        # 從即時 strategy_probs 找出最高機率的可交易策略
        best_strategy = None
        best_prob = 0
        trade_direction = None
        
        for strategy_name, prob in strategy_probs.items():
            if strategy_name in tradeable_strategies and prob > best_prob:
                best_prob = prob
                best_strategy = strategy_name
                trade_direction = tradeable_strategies[strategy_name]
        
        # 如果沒找到可交易策略
        if not best_strategy:
            self._reset_signal_tracking()
            self.market_data['signal_status']['reject_reason'] = "無可交易策略"
            return False, "", data
        
        # 🔧 v13.4: OBI Consistency Check
        if self.config.obi_consistency_check:
            # LONG but OBI very negative
            if trade_direction == 'LONG' and obi < -0.2:
                 self.market_data['signal_status']['reject_reason'] = f"⛔ OBI {obi:.2f} < -0.2 (嚴重背離) -> 阻擋做多"
                 return False, "", data
            # SHORT but OBI very positive
            if trade_direction == 'SHORT' and obi > 0.2:
                 self.market_data['signal_status']['reject_reason'] = f"⛔ OBI {obi:.2f} > 0.2 (嚴重背離) -> 阻擋做空"
                 return False, "", data

        # 🔧 v13.4: Enhanced Confidence Check (User Request)
        # Even if probability is high, confidence must be decent
        confidence = data.get('confidence', 0) or 0
        if confidence < self.config.min_confidence:
             # Exception: Maybe allow if Probability is SUPER high (>0.9)?
             if best_prob < 0.90:
                 self.market_data['signal_status']['reject_reason'] = f"⛔ 信心不足 {confidence:.2f} < {self.config.min_confidence}"
                 return False, "", data

        # 🆕 v5.5 觀察型策略優先檢查
        # 定義觀察型策略（這些策略表示市場不適合交易）
        observe_strategies = [
            'FAKEOUT',           # 假突破 - 容易被騙
            'SPOOFING',          # 欺騙掛單 - 訂單簿不可信
            'CONSOLIDATION_SHAKE',  # 盤整洗盤 - 無方向
            'STOP_HUNT',         # 獵殺止損 - 極端波動
            'WHIPSAW',           # 來回洗 - 雙向被殺
            'SLOW_BLEED',        # 陰跌洗盤 - 假底部
            'WASH_TRADING',      # 對敲交易 - 成交量假象
            'LAYERING',          # 分層掛單 - 價格操縱
            'NORMAL',            # 正常波動 - 無主力訊號
        ]
        
        # 找出最高機率的觀察型策略
        best_observe_strategy = None
        best_observe_prob = 0
        
        for strategy_name, prob in strategy_probs.items():
            if strategy_name in observe_strategies and prob > best_observe_prob:
                best_observe_prob = prob
                best_observe_strategy = strategy_name
        
        # 🆕 v12.11: 恢復主力辨識系統否決權 (Whale Recognition Veto)
        # 用戶反饋: "辨識方向的策略好像有點要加強。一直被主力洗掉。"
        # 策略: 當主要信號是洗盤或誘騙行為時，強制觀望，不進場。
        # 🔧 v12.11 Fix: 必須檢查 best_observe_strategy，而不是 best_strategy (因為 best_strategy 已經過濾掉觀察型了)
        # 規則: 如果觀察型策略機率 > 60% 且 > 可交易策略機率，則否決
        if best_observe_strategy and best_observe_prob > 0.6 and best_observe_prob > best_prob:
            self.market_data['signal_status']['reject_reason'] = f"🚫 主力洗盤警告 ({best_observe_strategy} {best_observe_prob*100:.1f}%)"
            return False, "", data

        obi = data.get('obi', 0)
        
        # 🆕 v7.0: 反向模式跳過 v5.7 過濾 (這些規則是過度擬合的)
        # 🔧 dYdX: 暫時停用 v5.7 過濾（手續費已納入 ROE 計算，策略需自行控頻）
        if False and not self.config.reverse_mode:
            # 🚫 v5.7 過濾 1: 停止 DISTRIBUTION 做空
            if best_strategy == 'DISTRIBUTION' and trade_direction == 'SHORT':
                self._reset_signal_tracking()
                self.market_data['signal_status']['reject_reason'] = (
                    f"⛔ v5.7: DISTRIBUTION 做空歷史勝率僅 42-48% → 不交易"
                )
                self.logger.info(f"⛔ v5.7 停止 DISTRIBUTION 做空 (歷史勝率太低)")
                return False, "", data
            
            # 🚫 v5.7 過濾 2: 機率區間過濾 (75-90% 最佳)
            if best_prob < 0.75:
                self._reset_signal_tracking()
                self.market_data['signal_status']['reject_reason'] = (
                    f"⛔ v5.7: 機率 {best_prob:.0%} < 75% (65-75%區間勝率僅47.8%) → 不交易"
                )
                self.logger.info(f"⛔ v5.7 機率過低: {best_prob:.0%} < 75%")
                return False, "", data
            
            if best_prob > 0.92:
                self._reset_signal_tracking()
                self.market_data['signal_status']['reject_reason'] = (
                    f"⛔ v5.7: 機率 {best_prob:.0%} > 92% (過度自信，95-100%勝率僅53.8%) → 不交易"
                )
                self.logger.info(f"⛔ v5.7 機率過高: {best_prob:.0%} > 92%")
                return False, "", data
            
            # 🚫 v5.7 過濾 3: OBI 區間過濾 (做多時需要適度買壓)
            if trade_direction == 'LONG':
                if obi < 0.2:
                    self._reset_signal_tracking()
                    self.market_data['signal_status']['reject_reason'] = (
                        f"⛔ v5.7: OBI {obi:.2f} < 0.2 (買壓不足，做多風險高) → 不交易"
                    )
                    self.logger.info(f"⛔ v5.7 OBI 過低做多: {obi:.2f}")
                    return False, "", data
                if obi > 0.85:
                    self._reset_signal_tracking()
                    self.market_data['signal_status']['reject_reason'] = (
                        f"⛔ v5.7: OBI {obi:.2f} > 0.85 (買壓過強，可能誘多) → 不交易"
                    )
                    self.logger.info(f"⛔ v5.7 OBI 過高做多: {obi:.2f}")
                    return False, "", data
        else:
            self.logger.debug(f"🔄 v7.0 反向模式: 跳過 v5.7 過濾規則")
        
        # ✅ v5.7: 觀察策略不再過濾 (數據顯示觀察>=90% 勝率最高！)
        # 僅記錄供分析
        if best_observe_strategy and best_observe_prob >= 0.75:
            self.market_data['signal_status']['observe_info'] = f"{best_observe_strategy}({best_observe_prob:.0%})"
            self.logger.info(f"ℹ️ v5.7 觀察策略高機率 (不過濾): {best_observe_strategy}({best_observe_prob:.0%})")
        
        # ============ 信號穩定性檢查 ============
        
        # 🆕 v7.0 反向模式: 降低所有機率門檻要求
        # 因為我們是做反向交易，原策略不需要高機率
        if self.config.reverse_mode:
            min_prob_threshold = 0.10  # 只要有任何信號就行
            min_conf_threshold = 0.08
            min_adv_threshold = 0.05
            self.logger.debug(f"🔄 v7.0 反向模式: 使用寬鬆門檻 (prob>{min_prob_threshold:.0%}, adv>{min_adv_threshold:.0%})")
        else:
            min_prob_threshold = self.config.min_probability
            min_conf_threshold = self.config.min_confidence
            min_adv_threshold = self.config.min_signal_advantage
        
        # 1. 檢查機率門檻 (v5.7 已在上面用 75% 過濾，這裡用 config 作為備援)
        if best_prob < min_prob_threshold:
            self._reset_signal_tracking()
            self.market_data['signal_status']['reject_reason'] = f"機率不足 ({best_prob:.0%} < {min_prob_threshold:.0%})"
            return False, "", data
        
        # 用 best_prob 作為 confidence (簡化)
        confidence = best_prob * 0.8
        if confidence < min_conf_threshold:
            self._reset_signal_tracking()
            self.market_data['signal_status']['reject_reason'] = f"信心不足 ({confidence:.0%} < {min_conf_threshold:.0%})"
            return False, "", data
        
        # 3. 🆕 檢查多空信號衝突 (防止被洗!)
        # 計算方向優勢
        if trade_direction == "LONG":
            signal_advantage = long_prob - short_prob
        else:
            signal_advantage = short_prob - long_prob
        
        self.market_data['signal_status']['signal_advantage'] = signal_advantage
        self.market_data['signal_status']['pending_direction'] = trade_direction
        
        # 如果優勢不夠大，不交易 (防止 68% vs 68% 的矛盾)
        if signal_advantage < min_adv_threshold:
            self._reset_signal_tracking()
            self.market_data['signal_status']['reject_reason'] = f"⚠️ 多空衝突! 優勢不足 ({signal_advantage:.0%} < {min_adv_threshold:.0%})"
            return False, "", data
        
        # 🆕 v5.4 數據品質過濾器（取代舊的混沌過濾）
        # ⚠️ 根據歷史數據分析：
        #    - 缺少 strategy_probs 的交易勝率只有 41.7%
        #    - 有完整 strategy_probs（即使有矛盾）的交易勝率 59.5%
        #    - 高主策略機率 + 有矛盾策略的交易勝率竟然高達 87.5%！
        # 結論：矛盾不是問題，數據不完整才是問題
        
        # 檢查策略機率數據是否完整
        strategy_count = len([p for p in strategy_probs.values() if p > 0.01])
        
        if strategy_count <= 1:
            # 只有一個策略有機率，說明偵測器沒有完整分析
            self.market_data['signal_status']['data_quality'] = 'LOW'
            self.logger.debug(f"ℹ️ 策略機率數據不完整 (僅 {strategy_count} 個策略)")
            # 不強制過濾，但記錄警告
        else:
            self.market_data['signal_status']['data_quality'] = 'GOOD'
        
        # 記錄矛盾策略資訊（供分析，不過濾）
        chaos_strategies = {
            'LONG': ['DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 'LONG_SQUEEZE', 'PUMP_DUMP', 'SLOW_BLEED'],
            'SHORT': ['ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 'SHORT_SQUEEZE', 'FLASH_CRASH']
        }
        
        conflicting_strategies = chaos_strategies.get(trade_direction, [])
        high_conflict_probs = []
        
        for strat in conflicting_strategies:
            prob = strategy_probs.get(strat, 0)
            if prob >= 0.5:
                high_conflict_probs.append((strat, prob))
        
        if high_conflict_probs:
            conflict_names = ", ".join([f"{s}({p:.0%})" for s, p in high_conflict_probs])
            self.market_data['signal_status']['conflicting_strategies'] = conflict_names
            # ℹ️ v5.4: 不再因為矛盾策略而過濾，歷史數據顯示這些交易反而更好
            self.logger.debug(f"ℹ️ 偵測到矛盾策略: {conflict_names}（不過濾，僅記錄）")
        
        # 3.5 🆕 即時數據驗證 + 反轉策略
        obi = data.get('obi', 0)
        price_change_1m = data.get('price_change_1m', 0)
        wpi = data.get('trade_imbalance', 0)  # 大單不平衡
        
        # 🆕 v4.2 反轉策略檢查 (支援 SKIP 觀望)
        reversal_triggered = False
        reversal_reason = ""
        original_direction = trade_direction
        
        if self.config.reversal_mode_enabled and best_strategy in ['ACCUMULATION', 'DISTRIBUTION']:
            new_direction, reason = self._get_reversal_direction(best_strategy, trade_direction, data)
            
            # 🆕 v4.2 處理 SKIP (觀望) 情況
            if new_direction == "SKIP":
                self._reset_signal_tracking()
                self.market_data['signal_status']['reject_reason'] = reason
                self.market_data['signal_status']['reversal_skip'] = True
                return False, "", data
            
            if new_direction != trade_direction:
                reversal_triggered = True
                reversal_reason = reason
                trade_direction = new_direction
                self.market_data['signal_status']['reversal_triggered'] = True
                self.market_data['signal_status']['reversal_reason'] = reason
                self.logger.info(f"🔄 反轉策略觸發: {best_strategy} {original_direction}→{new_direction} | {reason}")
        
        # 更新 pending_direction (可能已反轉)
        self.market_data['signal_status']['pending_direction'] = trade_direction
        
        # 🆕 v5.0 MTF 多時間框架過濾
        if self.mtf_analyzer and self.mtf_enabled:
            mtf_allowed, mtf_reason = self.mtf_analyzer.get_trade_filter(trade_direction)
            if not mtf_allowed:
                # 🆕 v5.2 主力策略與 MTF 矛盾時 → 觀望，不強制切換
                # 原因：主力說「派發」但 MTF 看多，可能是頂部信號
                #       強制切換成做多會跟主力對著幹，風險太高
                self._reset_signal_tracking()
                self.market_data['signal_status']['mtf_conflict'] = True
                self.logger.info(f"🚫 MTF 矛盾: 主力={best_strategy} vs {mtf_reason} → 觀望")
                return False, "", data

        # 主力 regime 防呆：無明顯主力時不交易
        if self.current_regime in (None, "NO_DOMINANT"):
            self._reset_signal_tracking()
            self.market_data['signal_status']['reject_reason'] = "無明顯主力，觀望"
            return False, "", data

        # 六維 veto：分數不足或方向衝突直接觀望
        six_dim = data.get('six_dim', {})
        # 🔧 v13.4: 根據交易方向取對應分數 (修復 bug: 原本用不存在的 'score' 欄位)
        if trade_direction == "LONG":
            six_score = six_dim.get('long_score', 0)
            # v14.12: LONG使用專用門檻 (校正: LONG準確率46.9%)
            min_score = getattr(self.config, 'six_dim_min_score_long', None) or self.config.six_dim_min_score_to_trade
        else:
            six_score = six_dim.get('short_score', 0)
            # v14.12: SHORT使用專用門檻 (校正: SHORT準確率66.7%)
            min_score = getattr(self.config, 'six_dim_min_score_short', None) or self.config.six_dim_min_score_to_trade
        
        if six_score < min_score:
            self._reset_signal_tracking()
            self.market_data['signal_status']['reject_reason'] = f"六維分數不足 ({trade_direction}: {six_score} < {min_score})"
            return False, "", data

        # OBI/動能/成交量方向與交易方向衝突時直接拒絕
        obi_dir = six_dim.get('obi_dir', 'NEUTRAL')
        momentum_dir = six_dim.get('momentum_dir', 'NEUTRAL')
        volume_dir = six_dim.get('volume_dir', 'NEUTRAL')
        if trade_direction == "LONG" and (obi_dir == 'SHORT' or momentum_dir == 'SHORT' or volume_dir == 'SHORT'):
            self._reset_signal_tracking()
            self.market_data['signal_status']['reject_reason'] = "方向衝突：OBI/動能/量偏空"
            return False, "", data
        if trade_direction == "SHORT" and (obi_dir == 'LONG' or momentum_dir == 'LONG' or volume_dir == 'LONG'):
            self._reset_signal_tracking()
            self.market_data['signal_status']['reject_reason'] = "方向衝突：OBI/動能/量偏多"
            return False, "", data

        # 交易所價差防呆：dYdX vs Binance 價差過大時觀望
        spread_pct_exch = data.get('exchange_spread_pct', 0)
        if spread_pct_exch and spread_pct_exch > self.config.max_exchange_spread_pct:
            self._reset_signal_tracking()
            self.market_data['signal_status']['reject_reason'] = f"跨所價差過大 ({spread_pct_exch:.3f}% > {self.config.max_exchange_spread_pct}%)"
            return False, "", data

        # Binance 情緒旁證：OBI 反向強烈時否決
        if self.config.binance_sentiment_enabled:
            bin_obi = data.get('binance_obi', None)
            if bin_obi is not None:
                thr = self.config.binance_obi_threshold
                if trade_direction == "LONG" and bin_obi <= -thr:
                    self._reset_signal_tracking()
                    self.market_data['signal_status']['reject_reason'] = f"Binance OBI 反向 ({bin_obi:.3f} ≤ -{thr})"
                    return False, "", data
                if trade_direction == "SHORT" and bin_obi >= thr:
                    self._reset_signal_tracking()
                    self.market_data['signal_status']['reject_reason'] = f"Binance OBI 反向 ({bin_obi:.3f} ≥ {thr})"
                    return False, "", data

        # 🔧 v13.2 吸籌/派發策略強化過濾 (防假信號)
        # 用戶反饋: "吸籌建倉做多，但是連被洗掉四次"
        # 原因: 在盤整或下跌趨勢中出現假吸籌
        if best_strategy in ['ACCUMULATION', 'DISTRIBUTION']:
            if self.mtf_analyzer and self.mtf_analyzer.latest_snapshot:
                score = self.mtf_analyzer.latest_snapshot.alignment_score
                # 嚴格要求: 吸籌必須 MTF Score >= 0 (不能逆勢)
                if trade_direction == "LONG" and score < 0:
                    self._reset_signal_tracking()
                    self.market_data['signal_status']['reject_reason'] = f"🚫 吸籌過濾: MTF 趨勢偏空 ({score:+.0f})，可能是下跌中繼"
                    self.logger.info(f"🚫 吸籌過濾: MTF={score:+.0f} vs LONG")
                    return False, "", data
                # 嚴格要求: 派發必須 MTF Score <= 0
                elif trade_direction == "SHORT" and score > 0:
                    self._reset_signal_tracking()
                    self.market_data['signal_status']['reject_reason'] = f"🚫 派發過濾: MTF 趨勢偏多 ({score:+.0f})，可能是上漲中繼"
                    self.logger.info(f"🚫 派發過濾: MTF={score:+.0f} vs SHORT")
                    return False, "", data
        
        # 即時驗證 (反轉後重新檢查) - 🔧 v12.12: 恢復保護機制
        # 🆕 v7.0: 反向模式跳過這些檢查
        if not self.config.reverse_mode:
            if trade_direction == "SHORT":
                # 做空前檢查：市場不能明顯偏多 (除非是反轉模式)
                if not reversal_triggered:  # 正常模式才檢查
                    if obi > 0.4:  # 🔧 v12.12: 0.6→0.4 (OBI 強烈偏買方)
                        self._reset_signal_tracking()
                        self.market_data['signal_status']['reject_reason'] = f"❌ OBI偏買 ({obi:.2f} > 0.4)，不適合做空"
                        return False, "", data
                    if price_change_1m > 0.08:  # 🔧 v12.12: 0.15%→0.08% (價格漲過快)
                        self._reset_signal_tracking()
                        self.market_data['signal_status']['reject_reason'] = f"❌ 價格在漲 ({price_change_1m:.3f}%)，不適合做空"
                        return False, "", data
                    if wpi > 0.6:  # 🔧 v12.12: 0.8→0.6 (大單偏買)
                        self._reset_signal_tracking()
                        self.market_data['signal_status']['reject_reason'] = f"❌ 大單偏買 (WPI={wpi:.2f})，不適合做空"
                        return False, "", data
            
            elif trade_direction == "LONG":
                # 做多前檢查：市場不能明顯偏空 (除非是反轉模式)
                if not reversal_triggered:  # 正常模式才檢查
                    if obi < -0.4:  # 🔧 v12.12: -0.6→-0.4 (OBI 強烈偏賣方)
                        self._reset_signal_tracking()
                        self.market_data['signal_status']['reject_reason'] = f"❌ OBI偏賣 ({obi:.2f} < -0.4)，不適合做多"
                        return False, "", data
                    if price_change_1m < -0.08:  # 🔧 v12.12: -0.15%→-0.08% (價格跌過快)
                        self._reset_signal_tracking()
                        self.market_data['signal_status']['reject_reason'] = f"❌ 價格在跌 ({price_change_1m:.3f}%)，不適合做多"
                        return False, "", data
                    if wpi < -0.6:  # 🔧 v12.12: -0.8→-0.6 (大單偏賣)
                        self._reset_signal_tracking()
                        self.market_data['signal_status']['reject_reason'] = f"❌ 大單偏賣 (WPI={wpi:.2f})，不適合做多"
                        return False, "", data
        
        # 4. 🆕 信號穩定性追蹤 (需持續 N 秒)
        current_signal = {
            'direction': trade_direction,
            'strategy': best_strategy,  # 🔧 使用 best_strategy
            'probability': best_prob,   # 🔧 使用 best_prob
            'timestamp': time.time()
        }
        
        # 🔧 將策略資訊存入 data，供後續使用
        data['detected_strategy'] = {
            'name': best_strategy,
            'probability': best_prob,
            'confidence': confidence,
            'direction': trade_direction
        }
        
        # 記錄信號
        self.signal_history.append(current_signal)
        # 只保留最近 10 秒的記錄
        self.signal_history = [s for s in self.signal_history 
                              if time.time() - s['timestamp'] < 10]
        
        # 檢查信號是否一致
        is_stable = self._is_signal_stable(trade_direction)
        self.market_data['signal_status']['is_stable'] = is_stable
        
        if not is_stable:
            self.market_data['signal_status']['reject_reason'] = "信號不穩定 (方向變動中)"
            return False, "", data
        
        # 5. 信號確認時間檢查
        if self.confirmed_signal is None or self.confirmed_signal['direction'] != trade_direction:
            # 新的信號方向，開始計時
            self.confirmed_signal = current_signal
            self.signal_confirm_start = time.time()
            self.market_data['signal_status']['confirm_progress'] = 0
            self.market_data['signal_status']['reject_reason'] = f"確認中... (0/{self.config.signal_confirm_seconds}秒)"
            return False, "", data
        
        # 檢查是否已確認足夠時間
        confirm_duration = time.time() - self.signal_confirm_start
        self.market_data['signal_status']['confirm_progress'] = min(confirm_duration, self.config.signal_confirm_seconds)
        
        if confirm_duration < self.config.signal_confirm_seconds:
            self.market_data['signal_status']['reject_reason'] = f"確認中... ({confirm_duration:.1f}/{self.config.signal_confirm_seconds}秒)"
            return False, "", data
        
        # 🆕 v12.11: 動能確認 (嚴格模式 - 目標勝率 70%)
        # 做多時動能必須 > 0，做空時動能必須 < 0
        if self.config.require_momentum_confirm:
            six_dim = data.get('six_dim', {})
            momentum_dir = six_dim.get('momentum_dir', 'NEUTRAL')
            momentum_value = six_dim.get('momentum_value', 0)
            
            if trade_direction == 'LONG' and momentum_dir == 'SHORT':
                self.market_data['signal_status']['reject_reason'] = f"❌ 動能反向 (做多但價格下跌 {momentum_value:.3f}%)"
                return False, "", data
            elif trade_direction == 'SHORT' and momentum_dir == 'LONG':
                self.market_data['signal_status']['reject_reason'] = f"❌ 動能反向 (做空但價格上漲 {momentum_value:.3f}%)"
                return False, "", data
        
        # 🆕 v7.1: 智慧反向交易模式
        # 根據實測數據：
        # - ACCUMULATION 做多信號 → 反向做空 = 勝率 75%，正確！
        # - DISTRIBUTION 做空信號 → 反向做多 = 勝率 33%，錯誤！
        # 結論：只反向 ACCUMULATION 類信號（做多→做空）
        if self.config.reverse_mode:
            original_direction = trade_direction
            
            # 只反向做多信號 (ACCUMULATION 等)
            # 做空信號 (DISTRIBUTION 等) 保持不變
            accumulation_strategies = ['ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 'SHORT_SQUEEZE', 'FLASH_CRASH']
            
            if best_strategy in accumulation_strategies or original_direction == "LONG":
                trade_direction = "SHORT"
                self.logger.info(f"🔄 v7.1 反向模式: {best_strategy}({original_direction}) → {trade_direction} (吸籌類做空)")
                self.market_data['signal_status']['reversed'] = True
            else:
                # DISTRIBUTION 類保持做空
                self.logger.info(f"🔄 v7.1 保持方向: {best_strategy}({original_direction}) → {trade_direction} (派發類維持)")
                self.market_data['signal_status']['reversed'] = False
                
            self.market_data['signal_status']['original_direction'] = original_direction
        
        # ✅ 信號已確認，可以交易
        self.market_data['signal_status']['reject_reason'] = None
        
        # 🆕 v14.16: 最後一道 Veto 檢查 (動能竭盡、趨勢逆向等)
        veto_passed, veto_reason = self.trader.check_entry_veto(trade_direction, data)
        if not veto_passed:
            self._reset_signal_tracking()
            self.market_data['signal_status']['reject_reason'] = f"Veto 阻擋: {veto_reason}"
            return False, "", data
            
        return True, trade_direction, data
    
    # ============================================================
    # 🆕 v8.0 MTF-First 策略模式
    # ============================================================
    
    def _should_enter_mtf_first(self) -> tuple[bool, str, Dict]:
        """
        🆕 v8.0 MTF-First 策略：以 15 分鐘時間框架為主
        
        核心理念：
        - 不依賴主力策略偵測（那些信號反而是反指標）
        - 直接使用 MTF 分析器的 15m 趨勢作為主要信號
        - 搭配 1h/4h 確認，避免逆大勢
        - 固定 15 分鐘持倉，依週期交易
        
        Returns:
            (是否進場, 方向, 市場數據)
        """
        data = self.market_data
        
        # 初始化狀態顯示
        self.market_data['signal_status'] = {
            'mode': 'MTF_FIRST_v8.0',
            'pending_direction': None,
            'reject_reason': None,
            'mtf_15m_signal': None,
            'mtf_1h_signal': None,
            'mtf_4h_signal': None,
            'alignment_score': 0,
            'predicted_entry': 0,
            'predicted_exit': 0,
        }
        
        # 檢查 MTF 分析器是否可用
        if not self.mtf_analyzer or not self.mtf_analyzer.enabled:
            self.market_data['signal_status']['reject_reason'] = "MTF 分析器未啟用"
            return False, "", data
        
        snapshot = self.mtf_analyzer.latest_snapshot
        if not snapshot:
            self.market_data['signal_status']['reject_reason'] = "等待 MTF 數據..."
            return False, "", data
        
        # 獲取 15m 時間框架數據（主要信號來源）
        tf_15m = snapshot.tf_15m
        tf_1h = snapshot.tf_1h
        tf_4h = snapshot.tf_4h
        
        if not tf_15m:
            self.market_data['signal_status']['reject_reason'] = "15m 數據未就緒"
            return False, "", data
        
        # 記錄 MTF 信號狀態
        self.market_data['signal_status']['mtf_15m_signal'] = tf_15m.signal.value if tf_15m else 'N/A'
        self.market_data['signal_status']['mtf_1h_signal'] = tf_1h.signal.value if tf_1h else 'N/A'
        self.market_data['signal_status']['mtf_4h_signal'] = tf_4h.signal.value if tf_4h else 'N/A'
        self.market_data['signal_status']['alignment_score'] = snapshot.alignment_score
        
        # ============ 決定交易方向 ============
        trade_direction = None
        signal_reason = ""
        
        # 🔧 v13.4: Relaxed MTF Logic (Allow Counter-Trend if MTF is Weak)
        # Old Logic: Strict Follow 15m
        
        strong_reversal_threshold = 0.85 # If strategy prob > 85%, allow counter-trend
        
        if tf_15m.signal == TimeframeSignal.BULLISH:
            if snapshot.alignment_score < 50: # Weak Bullish
                 # Check if we have strong SHORT signal
                 short_prob = strategy_probs.get('DISTRIBUTION', 0) + strategy_probs.get('RE_DISTRIBUTION', 0)
                 if short_prob > strong_reversal_threshold:
                      trade_direction = "SHORT"
                      signal_reason = f"📉 逆勢做空 (MTF弱多 {snapshot.alignment_score} + 強空訊號 {short_prob:.0%})"
                 else:
                      trade_direction = "LONG"
                      signal_reason = f"15m 看多 (RSI={tf_15m.rsi:.1f})"
            else:
                 trade_direction = "LONG"
                 signal_reason = f"15m 看多 (RSI={tf_15m.rsi:.1f})"

        elif tf_15m.signal == TimeframeSignal.BEARISH:
             if snapshot.alignment_score < 50: # Weak Bearish
                 # Check if we have strong LONG signal
                 long_prob = strategy_probs.get('ACCUMULATION', 0) + strategy_probs.get('RE_ACCUMULATION', 0)
                 if long_prob > strong_reversal_threshold:
                      trade_direction = "LONG"
                      signal_reason = f"📈 逆勢做多 (MTF弱空 {snapshot.alignment_score} + 強多訊號 {long_prob:.0%})"
                 else:
                      trade_direction = "SHORT"
                      signal_reason = f"15m 看空 (RSI={tf_15m.rsi:.1f})"
             else:
                 trade_direction = "SHORT"
                 signal_reason = f"15m 看空 (RSI={tf_15m.rsi:.1f})"
        else:
            self.market_data['signal_status']['reject_reason'] = f"15m 中性 (RSI={tf_15m.rsi:.1f})，等待方向"
            return False, "", data
        
        self.market_data['signal_status']['pending_direction'] = trade_direction
        
        # ============ RSI 過濾 ============
        rsi = tf_15m.rsi
        
        if trade_direction == "LONG":
            # 做多：RSI 不能太高（超買）也不能太低（恐慌中）
            if rsi < self.config.mtf_min_rsi_long:
                self.market_data['signal_status']['reject_reason'] = f"RSI {rsi:.1f} < {self.config.mtf_min_rsi_long} 超賣恐慌中，等待企穩"
                return False, "", data
            if rsi > self.config.mtf_max_rsi_long:
                self.market_data['signal_status']['reject_reason'] = f"RSI {rsi:.1f} > {self.config.mtf_max_rsi_long} 接近超買，風險高"
                return False, "", data
        else:  # SHORT
            # 做空：RSI 不能太低（超賣反彈風險）也不能太高（強勢中）
            if rsi < self.config.mtf_min_rsi_short:
                self.market_data['signal_status']['reject_reason'] = f"RSI {rsi:.1f} < {self.config.mtf_min_rsi_short} 超賣區，做空風險高"
                return False, "", data
            if rsi > self.config.mtf_max_rsi_short:
                self.market_data['signal_status']['reject_reason'] = f"RSI {rsi:.1f} > {self.config.mtf_max_rsi_short} 極端超買，等待確認"
                return False, "", data
        
        # ============ 🆕 v8.0.1: 主力策略衝突檢查 ============
        # 當主力策略偵測到高機率的反向信號時，不交易
        # 這是從歷史數據學到的教訓：主力策略雖然是反指標，但高機率信號還是有參考價值
        strategy_probs = data.get('strategy_probs', {})
        
        # 定義方向對應的主力策略
        if trade_direction == "LONG":
            # 做多時，檢查派發/做空類策略
            contrary_strategies = ['DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 'LONG_SQUEEZE', 'PUMP_DUMP']
        else:
            # 做空時，檢查吸籌/做多類策略
            contrary_strategies = ['ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 'SHORT_SQUEEZE', 'FLASH_CRASH']
        
        # 找最高的反向策略機率
        max_contrary_prob = 0
        max_contrary_name = ""
        for strat in contrary_strategies:
            prob = strategy_probs.get(strat, 0)
            if prob > max_contrary_prob:
                max_contrary_prob = prob
                max_contrary_name = strat
        
        # 如果反向策略機率 > 60%，不交易
        if max_contrary_prob > 0.60:
            self.market_data['signal_status']['reject_reason'] = (
                f"⚠️ 主力策略衝突: {max_contrary_name}({max_contrary_prob:.0%}) 與 MTF {trade_direction} 方向相反"
            )
            self.logger.info(f"⚠️ v8.0.1 主力衝突: {max_contrary_name}={max_contrary_prob:.0%} vs MTF={trade_direction}")
            return False, "", data
        
        # ============ MTF 對齊確認 ============
        alignment_score = snapshot.alignment_score
        
        # 做多需要正向對齊，做空需要負向對齊
        if trade_direction == "LONG" and alignment_score < self.config.mtf_alignment_threshold:
            self.market_data['signal_status']['reject_reason'] = f"MTF 對齊 {alignment_score:+.0f} < +{self.config.mtf_alignment_threshold:.0f}，大時間框架不支持做多"
            return False, "", data
        
        if trade_direction == "SHORT" and alignment_score > -self.config.mtf_alignment_threshold:
            self.market_data['signal_status']['reject_reason'] = f"MTF 對齊 {alignment_score:+.0f} > -{self.config.mtf_alignment_threshold:.0f}，大時間框架不支持做空"
            return False, "", data
        
        # ============ 計算預估價格 ============
        current_price = snapshot.current_price
        
        if trade_direction == "LONG":
            # 做多：止盈目標 = 阻力位或固定百分比
            predicted_entry = current_price
            if snapshot.nearest_resistance > current_price:
                predicted_exit = min(
                    snapshot.nearest_resistance,
                    current_price * (1 + self.config.target_profit_pct / 100)
                )
            else:
                predicted_exit = current_price * (1 + self.config.target_profit_pct / 100)
            emergency_stop = current_price * (1 - self.config.mtf_emergency_stop_pct / 100)
        else:
            # 做空：止盈目標 = 支撐位或固定百分比
            predicted_entry = current_price
            if snapshot.nearest_support > 0 and snapshot.nearest_support < current_price:
                predicted_exit = max(
                    snapshot.nearest_support,
                    current_price * (1 - self.config.target_profit_pct / 100)
                )
            else:
                predicted_exit = current_price * (1 - self.config.target_profit_pct / 100)
            emergency_stop = current_price * (1 + self.config.mtf_emergency_stop_pct / 100)
        
        self.market_data['signal_status']['predicted_entry'] = predicted_entry
        self.market_data['signal_status']['predicted_exit'] = predicted_exit
        self.market_data['signal_status']['emergency_stop'] = emergency_stop
        
        # ============ 簡化信號確認（MTF 已經是穩定信號）============
        # MTF 15m 數據本身就是平滑過的，不需要像主力策略那樣等待確認
        # 只做基本的方向一致性檢查
        
        current_signal = {
            'direction': trade_direction,
            'strategy': 'MTF_FIRST',
            'probability': abs(alignment_score) / 100,
            'timestamp': time.time()
        }
        
        data['detected_strategy'] = {
            'name': f'MTF_15m_{trade_direction}',
            'probability': abs(alignment_score) / 100,
            'confidence': tf_15m.strength / 100 if tf_15m else 0,
            'direction': trade_direction
        }
        
        # 快速確認（只需 2 秒）
        self.signal_history.append(current_signal)
        self.signal_history = [s for s in self.signal_history 
                              if time.time() - s['timestamp'] < 5]
        
        if len(self.signal_history) < 2:
            self.market_data['signal_status']['reject_reason'] = "MTF 信號確認中 (1/2秒)"
            return False, "", data
        
        # 檢查最近信號方向一致
        for sig in self.signal_history[-2:]:
            if sig['direction'] != trade_direction:
                self._reset_signal_tracking()
                self.market_data['signal_status']['reject_reason'] = "MTF 信號方向變化，重新確認"
                return False, "", data
        
        # ✅ 信號確認通過
        self.market_data['signal_status']['reject_reason'] = None
        
        # 🆕 v14.16: 最後一道 Veto 檢查 (動能竭盡、趨勢逆向等)
        veto_passed, veto_reason = self.trader.check_entry_veto(trade_direction, data)
        if not veto_passed:
            self.market_data['signal_status']['reject_reason'] = f"Veto 阻擋: {veto_reason}"
            return False, "", data

        self.logger.info(
            f"🆕 v8.0 MTF-First 信號確認: {trade_direction} | "
            f"15m={tf_15m.signal.value} RSI={rsi:.1f} | "
            f"對齊={alignment_score:+.0f} | "
            f"預估進場=${predicted_entry:,.0f} → 出場=${predicted_exit:,.0f}"
        )
        
        return True, trade_direction, data
    
    # ============================================================
    # 🐋 v10.7 鯨魚策略模組 (Whale Strategy Module)
    # ============================================================
    
    def _check_whale_signal(self, whale_cfg: Dict) -> tuple[bool, str, Dict]:
        """
        🐋 v10.7 鯨魚信號檢測與策略 (整合主力模式 + 穩定性過濾)
        
        核心邏輯:
        1. 檢測最近時間窗口內的大單交易
        2. 判斷大單方向 (買入/賣出主導)
        3. 🆕 v10.7 穩定性過濾 - 防止追入快速反轉信號
        4. 🆕 v10.7 防追單 - 價格已移動太多則拒絕
        5. 檢查主力模式是否與鯨魚方向一致 (加分/扣分)
        6. 檢查價格是否與鯨魚同方向
        7. 計算預期價格目標 (主力確認時調高目標)
        8. 設定嚴格鎖利參數
        
        Returns:
            (是否進場, 方向, 市場數據含鯨魚信息)
        """
        data = self.market_data.copy()
        strategy_probs = data.get('strategy_probs', {})
        
        # 獲取配置參數 (dYdX 交易量較低，降低門檻)
        big_trade_threshold = whale_cfg.get('big_trade_threshold', 1000)
        direction_match_required = whale_cfg.get('direction_match_required', True)
        min_whale_count = whale_cfg.get('min_whale_count', 2)
        time_window_sec = whale_cfg.get('time_window_sec', 30)  # v10.7: 縮短為 30 秒
        
        # 🆕 v10.7 穩定性過濾配置
        stability_cfg = whale_cfg.get('stability_filter', {})
        stability_enabled = stability_cfg.get('enabled', True)
        min_stable_seconds = stability_cfg.get('min_stable_seconds', 10)
        max_direction_changes = stability_cfg.get('max_direction_changes', 1)
        
        # 🆕 v10.7 防追單配置
        anti_chase_cfg = stability_cfg.get('anti_chase', {})
        anti_chase_enabled = anti_chase_cfg.get('enabled', True)
        max_price_move_pct = anti_chase_cfg.get('max_price_move_pct', 0.08)
        chase_check_window = anti_chase_cfg.get('check_window_sec', 15)
        
        # 主力確認配置
        strategy_confirm_cfg = whale_cfg.get('strategy_confirmation', {})
        require_strategy_match = strategy_confirm_cfg.get('required', False)
        strategy_bonus_pct = strategy_confirm_cfg.get('bonus_pct', 20)
        
        profit_lock_cfg = whale_cfg.get('profit_lock', {})
        expected_price_cfg = whale_cfg.get('expected_price', {})

        # 獲取大單統計 (從 WebSocket 數據收集器)
        market_ws = self._get_market_ws()
        big_stats = market_ws.get_big_trades_stats(seconds=time_window_sec)
        big_trade_count = big_stats.get('big_trade_count', 0)
        big_buy_count = big_stats.get('big_buy_count', 0)
        big_sell_count = big_stats.get('big_sell_count', 0)
        big_buy_value = big_stats.get('big_buy_value', 0)
        big_sell_value = big_stats.get('big_sell_value', 0)
        recent_big_trades = big_stats.get('recent_big_trades', [])
        
        # v10.7: 獲取穩定性指標
        direction_changes = big_stats.get('direction_changes', 0)
        stable_duration_sec = big_stats.get('stable_duration_sec', 0)
        
        # 初始化鯨魚狀態
        data['whale_status'] = {
            'mode': 'v10.7_WHALE_MODE',
            'big_trade_count': big_trade_count,
            'big_buy_count': big_buy_count,
            'big_sell_count': big_sell_count,
            'big_buy_value': big_buy_value,
            'big_sell_value': big_sell_value,
            'time_window_sec': time_window_sec,
            'whale_direction': None,
            'price_direction_match': False,
            # v10.7: 穩定性指標
            'direction_changes': direction_changes,
            'stable_duration_sec': stable_duration_sec,
            'stability_check_passed': False,
            'strategy_match': False,  # 🆕 主力模式匹配
            'signal_valid': False,
        }
        
        # 🔍 檢查是否有足夠的鯨魚信號
        if big_trade_count < min_whale_count:
            self.logger.debug(f"🐋 鯨魚信號不足: {big_trade_count}/{min_whale_count} 筆大單")
            return False, "", data
        
        # 🎯 判斷鯨魚方向
        whale_direction = None
        total_whale_value = big_buy_value + big_sell_value
        
        if total_whale_value > 0:
            buy_ratio = big_buy_value / total_whale_value
            sell_ratio = big_sell_value / total_whale_value
            
            # 買入主導 (>60% 買單價值)
            if buy_ratio > 0.6:
                whale_direction = "LONG"
            # 賣出主導 (>60% 賣單價值)
            elif sell_ratio > 0.6:
                whale_direction = "SHORT"
            else:
                self.logger.debug(f"🐋 鯨魚方向不明: 買入{buy_ratio:.0%} vs 賣出{sell_ratio:.0%}")
                return False, "", data
        else:
            self.logger.debug("🐋 無有效鯨魚交易")
            return False, "", data
        
        data['whale_status']['whale_direction'] = whale_direction
        data['whale_status']['buy_ratio'] = buy_ratio
        data['whale_status']['sell_ratio'] = sell_ratio
        
        # ═══════════════════════════════════════════════════════════════════
        # 🆕 v10.12 穩定性過濾 - 改用三線系統
        # 如果三線系統已勝出，直接使用三線系統的穩定時間
        # ═══════════════════════════════════════════════════════════════════
        if stability_enabled:
            # 🆕 v10.12: 優先檢查三線系統的穩定時間
            three_line_stable_sec = 0
            if whale_direction == "LONG":
                three_line_stable_sec = self.long_alignment_seconds
            elif whale_direction == "SHORT":
                three_line_stable_sec = self.short_alignment_seconds
            
            # 使用三線系統或原本的穩定時間，取較大值
            effective_stable_sec = max(stable_duration_sec, three_line_stable_sec)
            
            # 檢查 1: 方向變化次數 (仍然保留)
            if direction_changes > max_direction_changes:
                self.logger.info(
                    f"🐋⚠️ 穩定性不足: 方向變化 {direction_changes} 次 > 最大允許 {max_direction_changes} → 拒絕"
                )
                data['whale_status']['reject_reason'] = f"方向變化太頻繁 ({direction_changes} 次)"
                return False, "", data
            
            # 檢查 2: 方向穩定時間 (使用三線系統或原本穩定時間)
            if effective_stable_sec < min_stable_seconds:
                self.logger.info(
                    f"🐋⚠️ 穩定性不足: 方向穩定 {effective_stable_sec:.1f}s < 最小要求 {min_stable_seconds}s → 拒絕"
                )
                data['whale_status']['reject_reason'] = f"方向穩定時間不足 ({effective_stable_sec:.1f}s)"
                return False, "", data
            
            data['whale_status']['stability_check_passed'] = True
            self.logger.debug(f"🐋✅ 穩定性檢查通過: 方向變化 {direction_changes} 次, 穩定 {stable_duration_sec:.1f}s")
        
        # ═══════════════════════════════════════════════════════════════════
        # 🆕 v10.8 鯨魚策略 MTF 過濾 (防止逆大趨勢交易)
        # 1h RSI < 45 不做多 (空頭趨勢)
        # 1h RSI > 55 不做空 (多頭趨勢)
        # ═══════════════════════════════════════════════════════════════════
        mtf_cfg = whale_cfg.get('mtf_filter', {})
        whale_mtf_enabled = mtf_cfg.get('enabled', True)  # 預設啟用
        
        if whale_mtf_enabled and self.mtf_analyzer and self.mtf_analyzer.latest_snapshot:
            mtf_snapshot = self.mtf_analyzer.latest_snapshot
            tf_1h = mtf_snapshot.tf_1h
            
            if tf_1h:
                rsi_1h = tf_1h.rsi
                bullish_threshold = mtf_cfg.get('bullish_threshold', 55)
                bearish_threshold = mtf_cfg.get('bearish_threshold', 45)
                
                # 🚫 空頭趨勢 (RSI < 45) 不做多
                if rsi_1h < bearish_threshold and whale_direction == "LONG":
                    self.logger.info(
                        f"🐋🚫 MTF 過濾: 1h RSI={rsi_1h:.0f} < {bearish_threshold} (空頭趨勢) → 拒絕鯨魚做多"
                    )
                    data['whale_status']['reject_reason'] = f"1h RSI={rsi_1h:.0f} 空頭趨勢，拒絕做多"
                    data['whale_status']['mtf_rejected'] = True
                    return False, "", data
                
                # 🚫 多頭趨勢 (RSI > 55) 不做空
                if rsi_1h > bullish_threshold and whale_direction == "SHORT":
                    self.logger.info(
                        f"🐋🚫 MTF 過濾: 1h RSI={rsi_1h:.0f} > {bullish_threshold} (多頭趨勢) → 拒絕鯨魚做空"
                    )
                    data['whale_status']['reject_reason'] = f"1h RSI={rsi_1h:.0f} 多頭趨勢，拒絕做空"
                    data['whale_status']['mtf_rejected'] = True
                    return False, "", data
                
                self.logger.info(f"🐋✅ MTF 過濾通過: 1h RSI={rsi_1h:.0f}, 方向={whale_direction}")
                data['whale_status']['mtf_rsi_1h'] = rsi_1h
        
        # ═══════════════════════════════════════════════════════════════════
        # 🆕 v10.7 防追單檢查 (Anti-Chase Filter)
        # 價格已經大幅移動時不追入
        # ═══════════════════════════════════════════════════════════════════
        if anti_chase_enabled:
            # 獲取較短窗口的價格變化
            price_change_15s = market_ws.get_price_change(chase_check_window)
            
            # 獲取 1 分鐘價格變化 (防止長趨勢末端追單)
            max_price_move_1m_pct = anti_chase_cfg.get('max_price_move_1m_pct', 0.3)
            price_change_1m = data.get('price_change_1m', 0)
            
            # DEBUG: 打印防追單檢查數值 (強制 INFO 級別)
            self.logger.info(f"🔍 防追單檢查: Dir={whale_direction}, 1m={price_change_1m:.3f}%, Limit={max_price_move_1m_pct:.3f}%")
            
            # 做多時，價格已漲太多 → 不追
            if whale_direction == "LONG":
                if price_change_15s > max_price_move_pct:
                    self.logger.info(
                        f"🐋⚠️ 防追單(短線): 價格已漲 {price_change_15s:.2%} > {max_price_move_pct}% → 不追多"
                    )
                    data['whale_status']['reject_reason'] = f"短線已漲 {price_change_15s:.2%}，不追多"
                    return False, "", data
                
                if price_change_1m > max_price_move_1m_pct:
                    self.logger.info(
                        f"🐋⚠️ 防追單(長線): 1m價格已漲 {price_change_1m:.2%} > {max_price_move_1m_pct}% → 不追多"
                    )
                    data['whale_status']['reject_reason'] = f"1m已漲 {price_change_1m:.2%}，不追多"
                    return False, "", data
                
                # 🆕 v10.8 做多時不能在價格下跌中進場！
                if price_change_1m < -0.05:  # 1分鐘跌超過 0.05%
                    self.logger.info(
                        f"🐋⚠️ 防追單(反向): 價格在跌 {price_change_1m:.2%}，做多時機不對 → 拒絕"
                    )
                    data['whale_status']['reject_reason'] = f"價格在跌 {price_change_1m:.2%}，不適合做多"
                    return False, "", data
            
            # 做空時，價格已跌太多 → 不追
            if whale_direction == "SHORT":
                if price_change_15s < -max_price_move_pct:
                    self.logger.info(
                        f"🐋⚠️ 防追單(短線): 價格已跌 {price_change_15s:.2%} > {max_price_move_pct}% → 不追空"
                    )
                    data['whale_status']['reject_reason'] = f"短線已跌 {price_change_15s:.2%}，不追空"
                    return False, "", data
                
                if price_change_1m < -max_price_move_1m_pct:
                    self.logger.info(
                        f"🐋⚠️ 防追單(長線): 1m價格已跌 {price_change_1m:.2%} > {max_price_move_1m_pct}% → 不追空"
                    )
                    data['whale_status']['reject_reason'] = f"1m已跌 {price_change_1m:.2%}，不追空"
                    return False, "", data
                
                # 🆕 v10.8 做空時不能在價格上漲中進場！
                if price_change_1m > 0.05:  # 1分鐘漲超過 0.05%
                    self.logger.info(
                        f"🐋⚠️ 防追單(反向): 價格在漲 {price_change_1m:.2%}，做空時機不對 → 拒絕"
                    )
                    data['whale_status']['reject_reason'] = f"價格在漲 {price_change_1m:.2%}，不適合做空"
                    return False, "", data
            
            self.logger.info(f"🐋✅ 防追單檢查通過: 15s={price_change_15s:.2%}, 1m={price_change_1m:.2%}")
        
        # ═══════════════════════════════════════════════════════════════════
        # 🆕 v10.6 主力模式確認 (Strategy Confirmation)
        # 檢查鯨魚方向是否與主力策略一致
        # ═══════════════════════════════════════════════════════════════════
        long_strategies = {'ACCUMULATION', 'RE_ACCUMULATION', 'BEAR_TRAP', 'SHORT_SQUEEZE', 'FLASH_CRASH'}
        short_strategies = {'DISTRIBUTION', 'RE_DISTRIBUTION', 'BULL_TRAP', 'LONG_SQUEEZE', 'PUMP_DUMP'}
        
        # 找出當前主導策略
        best_strategy = None
        best_prob = 0
        for strat, prob in strategy_probs.items():
            if prob > best_prob:
                best_prob = prob
                best_strategy = strat
        
        # 判斷主力方向
        strategy_direction = None
        if best_strategy in long_strategies:
            strategy_direction = "LONG"
        elif best_strategy in short_strategies:
            strategy_direction = "SHORT"
        
        # 檢查是否匹配
        strategy_match = (whale_direction == strategy_direction) if strategy_direction else False
        data['whale_status']['strategy_match'] = strategy_match
        data['whale_status']['best_strategy'] = best_strategy
        data['whale_status']['best_strategy_prob'] = best_prob
        data['whale_status']['strategy_direction'] = strategy_direction
        
        # 信號強度計算
        signal_strength = 1.0
        if strategy_match:
            signal_strength = 1.0 + (strategy_bonus_pct / 100)  # 主力確認加成
            self.logger.info(f"🐋✅ 主力確認: {best_strategy}({best_prob:.0%}) 與鯨魚{whale_direction}一致 → 信號+{strategy_bonus_pct}%")
        elif strategy_direction and strategy_direction != whale_direction:
            # 主力與鯨魚衝突
            if require_strategy_match:
                self.logger.info(f"🐋⚠️ 主力衝突: {best_strategy}({best_prob:.0%})={strategy_direction} vs 鯨魚={whale_direction} → 放棄")
                data['whale_status']['reject_reason'] = f"主力{strategy_direction}與鯨魚{whale_direction}衝突"
                return False, "", data
            else:
                signal_strength = 0.7  # 衝突時降低信號強度
                self.logger.info(f"🐋⚠️ 主力衝突但允許: {best_strategy}={strategy_direction} vs 鯨魚={whale_direction} → 信號-30%")
        else:
            self.logger.debug(f"🐋 無明確主力方向，僅依賴鯨魚信號")
        
        data['whale_status']['signal_strength'] = signal_strength
        
        # 🔄 檢查價格是否與鯨魚同方向
        price_change_1m = data.get('price_change_1m', 0)
        price_change_5m = data.get('price_change_5m', 0)
        
        if direction_match_required:
            # 鯨魚做多，價格應該上漲
            if whale_direction == "LONG" and price_change_1m < -0.05:  # 價格跌超過 0.05%
                self.logger.info(f"🐋 鯨魚做多但價格下跌 {price_change_1m:.2%} → 不跟進")
                data['whale_status']['reject_reason'] = f"鯨魚做多但價格下跌 {price_change_1m:.2%}"
                return False, "", data
            
            # 鯨魚做空，價格應該下跌
            if whale_direction == "SHORT" and price_change_1m > 0.05:  # 價格漲超過 0.05%
                self.logger.info(f"🐋 鯨魚做空但價格上漲 {price_change_1m:.2%} → 不跟進")
                data['whale_status']['reject_reason'] = f"鯨魚做空但價格上漲 {price_change_1m:.2%}"
                return False, "", data
            
            data['whale_status']['price_direction_match'] = True
        
        # 📊 計算預期價格目標 (基於鯨魚訂單量)
        # 🔧 v14.6.24: 使用正確價格源 (dYdX sync 模式用 Oracle Price)
        current_price = self.get_current_price_for_trading()
        expected_profit_pct = 0.0
        expected_target_price = current_price
        
        if expected_price_cfg.get('calculate_from_size', True):
            # ═══════════════════════════════════════════════════════════
            # 🐋 v10.6 鯨魚價格預估模型
            # ═══════════════════════════════════════════════════════════
            # 
            # 原理: 大單會對價格造成衝擊 (Price Impact)
            # 根據經驗公式: Impact ≈ sqrt(Volume / ADV) * Constant
            # 
            # 簡化模型:
            # - 每 $10K USDT 大單 → 約 0.01% 價格影響
            # - 每 $50K USDT 大單 → 約 0.03% 價格影響 (非線性)
            # - 每 $100K USDT 大單 → 約 0.05% 價格影響
            # ═══════════════════════════════════════════════════════════
            
            total_whale_usd = max(big_buy_value, big_sell_value)
            
            # 非線性價格影響計算 (sqrt 模型更符合實際)
            # 基準: $10K = 0.01%, $100K = 0.032% (sqrt(10) * 0.01)
            base_impact_per_10k = expected_price_cfg.get('price_impact_per_10k', 0.01)
            use_sqrt_model = expected_price_cfg.get('use_sqrt_model', True)
            
            if use_sqrt_model and total_whale_usd > 0:
                # sqrt 模型: impact = base * sqrt(volume / 10000)
                import math
                volume_factor = math.sqrt(total_whale_usd / 10000)
                estimated_impact = base_impact_per_10k * volume_factor
            else:
                # 線性模型
                estimated_impact = (total_whale_usd / 10000) * base_impact_per_10k
            
            # 考慮多筆大單的累積效應
            if big_trade_count > 1:
                # 多筆大單 = 更強的趨勢，預期影響增加
                multi_trade_bonus = 1 + (big_trade_count - 1) * 0.1  # 每多一筆 +10%
                estimated_impact *= min(multi_trade_bonus, 1.5)  # 上限 150%
            
            # 考慮買賣不平衡程度
            imbalance_ratio = max(buy_ratio, sell_ratio)
            if imbalance_ratio > 0.7:  # 強烈不平衡
                estimated_impact *= 1.2
            elif imbalance_ratio > 0.8:  # 極度不平衡
                estimated_impact *= 1.4
            
            # 🆕 主力確認加成
            estimated_impact *= signal_strength
            
            # 預期獲利設定
            # - 保守目標: 影響的 60% (考慮滑點和手續費)
            # - 最低: 0.03% (確保覆蓋手續費)
            # - 最高: 0.15% (避免過度貪婪), 主力確認時可到 0.20%
            min_target_pct = expected_price_cfg.get('min_target_pct', 0.03)
            max_target_pct = expected_price_cfg.get('max_target_pct', 0.15)
            if strategy_match:
                max_target_pct = expected_price_cfg.get('max_target_with_strategy', 0.20)
            conservative_factor = expected_price_cfg.get('conservative_factor', 0.6)
            
            expected_profit_pct = estimated_impact * conservative_factor
            expected_profit_pct = max(min_target_pct, min(expected_profit_pct, max_target_pct))
            
            # 計算目標價格
            if whale_direction == "LONG":
                expected_target_price = current_price * (1 + expected_profit_pct / 100)
            else:
                expected_target_price = current_price * (1 - expected_profit_pct / 100)
            
            strategy_info = f"主力{best_strategy}✅" if strategy_match else f"主力{best_strategy or 'N/A'}"
            self.logger.info(
                f"🐋 價格預估: 鯨魚量${total_whale_usd:,.0f} × {big_trade_count}筆 | {strategy_info} | "
                f"預估影響={estimated_impact:.3%} | 目標={expected_profit_pct:.3%} | "
                f"目標價=${expected_target_price:,.2f}"
            )
        
        data['whale_status']['expected_profit_pct'] = expected_profit_pct
        data['whale_status']['expected_target_price'] = expected_target_price
        data['whale_status']['total_whale_value'] = max(big_buy_value, big_sell_value)
        data['whale_status']['estimated_impact_pct'] = estimated_impact if 'estimated_impact' in dir() else 0
        
        # 💰 設定鯨魚專屬鎖利參數
        if profit_lock_cfg.get('enabled', True):
            initial_lock_pct = profit_lock_cfg.get('initial_lock_pct', 0.05)
            trailing_pct = profit_lock_cfg.get('trailing_pct', 0.03)
            update_interval_sec = profit_lock_cfg.get('update_interval_sec', 1)
            
            data['whale_profit_lock'] = {
                'enabled': True,
                'initial_lock_pct': initial_lock_pct,
                'trailing_pct': trailing_pct,
                'update_interval_sec': update_interval_sec,
                'dynamic_target': True,  # 啟用動態目標
            }
        
        # ✅ 鯨魚信號有效
        data['whale_status']['signal_valid'] = True
        data['signal_status'] = {
            'mode': 'v10.6_WHALE_PRIORITY',
            'condition_matched': f"🐋 鯨魚{whale_direction} | 大單{big_trade_count}筆 | 金額${max(big_buy_value, big_sell_value):,.0f}",
            'pending_direction': whale_direction,
            'reject_reason': None,
        }
        
        self.logger.info(
            f"🐋 v10.7 鯨魚信號確認: {whale_direction} | "
            f"大單{big_trade_count}筆 (買{big_buy_count}/賣{big_sell_count}) | "
            f"買入${big_buy_value:,.0f} vs 賣出${big_sell_value:,.0f} | "
            f"價格變化1m={price_change_1m:.2%} | "
            f"預期獲利={expected_profit_pct:.2%}"
        )
        
        return True, whale_direction, data
    
    # ============================================================
    # 🆕 v10.1 專屬獲利模式 (Exclusive Profit Modes)
    # ============================================================
    
    def _should_enter_contextual(self) -> tuple[bool, str, Dict]:
        """
        🆕 v10.16 六維信號競爭模式
        
        ═══════════════════════════════════════════════════════════════════════
        核心邏輯: 多空信號競爭 (先到10秒者勝) - 準確度更高!
        ═══════════════════════════════════════════════════════════════════════
        
        📊 六維分析 (總分 12 分):
        ───────────────────────────────────────────────────────────────────────
        原三線:
          • 快線 (5秒):  ±1 分
          • 中線 (30秒): ±2 分  
          • 慢線 (5分):  ±3 分
        新三維:
          • OBI 線:      ±2 分 (訂單簿失衡)
          • 動能線:      ±2 分 (價格動能)
          • 成交量線:    ±2 分 (大單方向)
        
        對齊門檻: ≥8 分 (67%) 開始累積時間
        
        🏆 勝負判定:
        ───────────────────────────────────────────────────────────────────────
        • 多方先累積到 10 秒 → 做多
        • 空方先累積到 10 秒 → 做空
        • 雙方都達標 → 不交易 (混亂)
        
        🔄 信號反轉平倉:
        ───────────────────────────────────────────────────────────────────────
        • 持多倉時，空方開始累積 (≥3秒) → 平倉
        • 持空倉時，多方開始累積 (≥3秒) → 平倉
        
        Returns:
            (是否進場, 方向, 市場數據)
        """
        data = self.market_data
        
        # 🆕 v13.2: 執行混合策略風險檢查 (Hybrid Risk Guards)
        # 即便是 Contextual Mode 也要檢查價差和費率窗口
        passed, reject_reason = self._check_hybrid_risks(data)
        if not passed:
            self.market_data['signal_status']['reject_reason'] = reject_reason
            return False, "HYBRID_RISK", {}
        
        # 🔧 v13.6.3: 移除舊的 Veto 過濾器
        # 在六維系統下，不需要依賴主力偵測器的結果
        # 六維分數本身已經足夠判斷方向
        # ═══════════════════════════════════════════════════════════════════
        
        # 🆕 v13.1: Funding Rate 偏向過濾
        funding_rate = data.get('funding_rate', 0)
        funding_bias = ""
        funding_penalty = 0.0
        
        if funding_rate > 0.0001:  # > 0.01%
            funding_bias = "bearish"
            funding_penalty = min(0.15, funding_rate * 100)
        elif funding_rate < -0.0001:  # < -0.01%
            funding_bias = "bullish"
            funding_penalty = min(0.15, abs(funding_rate) * 100)
        
        data['funding_bias'] = funding_bias
        data['funding_penalty'] = funding_penalty
        
        # ═══════════════════════════════════════════════════════════════════
        
        # 初始化狀態
        mode_name = 'SIX_DIM_v10.16' if self.config.six_dim_enabled else 'THREE_LINE_v10.13'
        self.market_data['signal_status'] = {
            'mode': mode_name,
            'pending_direction': None,
            'reject_reason': None,
            'three_line_direction': None,
            'use_three_line': False,
        }
        
        # ═══════════════════════════════════════════════════════════════════
        # 🆕 v14.16: 提前更新 MTF 資料，確保即使六維未通過也能記錄 RSI
        # 修復: signal log 中 RSI 全部為 0 的問題
        # ═══════════════════════════════════════════════════════════════════
        if self.mtf_analyzer and self.mtf_analyzer.latest_snapshot:
            snapshot = self.mtf_analyzer.latest_snapshot
            tf_15m = snapshot.tf_15m
            tf_1h = snapshot.tf_1h
            tf_5m = snapshot.tf_5m
            tf_1m = snapshot.tf_1m
            tf_4h = snapshot.tf_4h
            
            rsi_1m = tf_1m.rsi if tf_1m else None
            rsi_5m = tf_5m.rsi if tf_5m else None
            rsi_15m = tf_15m.rsi if tf_15m else None
            rsi_1h = tf_1h.rsi if tf_1h else None
            rsi_4h = tf_4h.rsi if tf_4h else None
            
            # 提前設置 mtf_filter (即使六維未勝出也會有值)
            self.market_data['signal_status']['mtf_filter'] = {
                'rsi_1m': rsi_1m,
                'rsi_5m': rsi_5m,
                'rsi_15m': rsi_15m,
                'rsi_1h': rsi_1h,
                'rsi_4h': rsi_4h,
                'mtf_direction': None,  # 稍後設置
                'mtf_aligned': False,
            }
        
        # ═══════════════════════════════════════════════════════════════════
        # 🚨 v14.11: 急跌急漲快速進場 (繞過六維累積時間)
        # 當偵測到快速大幅變動時，直接進場捕捉趨勢
        # ═══════════════════════════════════════════════════════════════════
        # 🔧 v14.15: 修復配置讀取 - None 時才用默認值
        _spike_enabled = getattr(self.config, 'spike_fast_entry_enabled', None)
        _spike_threshold = getattr(self.config, 'spike_fast_entry_threshold', None)
        _spike_window = getattr(self.config, 'spike_fast_entry_window_sec', None)
        
        spike_fast_entry_enabled = _spike_enabled if _spike_enabled is not None else True
        spike_fast_entry_threshold = _spike_threshold if _spike_threshold is not None else 0.25  # 0.25% 觸發 (v14.14)
        spike_fast_entry_window = _spike_window if _spike_window is not None else 60  # 60 秒窗口 (v14.14)
        
        if spike_fast_entry_enabled:
            try:
                market_ws = self._get_market_ws()
                price_change_pct = market_ws.get_price_change(spike_fast_entry_window)
                
                if abs(price_change_pct) >= spike_fast_entry_threshold:
                    # 急跌急漲偵測！
                    spike_direction = "SHORT" if price_change_pct < 0 else "LONG"
                    
                    # 🛡️ 檢查方向是否允許
                    allowed_directions = getattr(self.config, 'allowed_directions', None)
                    if allowed_directions is None:
                        allowed_directions = ['LONG', 'SHORT']
                    
                    if spike_direction in allowed_directions:
                        # 🆕 v14.16: 快速進場也需要通過 Veto 檢查
                        veto_passed, veto_reason = self.trader.check_entry_veto(spike_direction, data)
                        if not veto_passed:
                            self.logger.info(f"🚫 v14.11 急{'跌' if spike_direction == 'SHORT' else '漲'}被 Veto: {veto_reason}")
                            return False, "", data

                        # ✅ 觸發快速進場！
                        spike_emoji = "📉" if spike_direction == "SHORT" else "📈"
                        self.logger.info(f"🚨 v14.11 急{'跌' if spike_direction == 'SHORT' else '漲'}快速進場! "
                                        f"{price_change_pct:+.2f}% (>±{spike_fast_entry_threshold}%) → {spike_direction}")
                        
                        # 設置信號狀態
                        six_dim = self.market_data.get('six_dim', {})
                        self.market_data['signal_status']['mode'] = 'SPIKE_FAST_ENTRY_v14.11'
                        self.market_data['signal_status']['three_line_direction'] = spike_direction
                        self.market_data['signal_status']['use_three_line'] = True
                        self.market_data['signal_status']['pending_direction'] = spike_direction
                        self.market_data['signal_status']['spike_entry'] = True
                        self.market_data['signal_status']['spike_change_pct'] = price_change_pct
                        
                        return True, spike_direction, data
            except Exception as e:
                self.logger.debug(f"急跌急漲偵測異常: {e}")
        
        # ═══════════════════════════════════════════════════════════════════
        # 🏆 v10.16: 六維競爭系統 - 直接決定方向
        # ═══════════════════════════════════════════════════════════════════
        long_stable = self.long_alignment_seconds >= self.min_alignment_seconds
        short_stable = self.short_alignment_seconds >= self.min_alignment_seconds
        
        # 獲取六維分數
        six_dim = self.market_data.get('six_dim', {})
        long_score = six_dim.get('long_score', 0)
        short_score = six_dim.get('short_score', 0)
        max_score = 12 if self.config.six_dim_enabled else 6
        
        # 🆕 高勝率: 最低分數門檻 - 沒有信號就不交易！
        # 🔧 v13.4: 預設值改為 8 (原本是 2，太寬鬆)
        min_score = getattr(self.config, 'six_dim_min_score_to_trade', 8)
        
        # 判定勝負
        if long_stable and not short_stable:
            # 多方勝出！
            direction = "LONG"
            self.market_data['signal_status']['three_line_direction'] = direction
            self.market_data['signal_status']['use_three_line'] = True
            self.market_data['signal_status']['pending_direction'] = direction
            self.logger.info(f"🟢 v10.16 六維系統: 多方勝出! ({self.long_alignment_seconds:.1f}s) 分數:{long_score}/{max_score}")
            
        elif short_stable and not long_stable:
            # 空方勝出！
            direction = "SHORT"
            self.market_data['signal_status']['three_line_direction'] = direction
            self.market_data['signal_status']['use_three_line'] = True
            self.market_data['signal_status']['pending_direction'] = direction
            self.logger.info(f"🔴 v10.16 六維系統: 空方勝出! ({self.short_alignment_seconds:.1f}s) 分數:{short_score}/{max_score}")
            
        elif long_stable and short_stable:
            # 雙方都達標 = 混亂
            self.market_data['signal_status']['reject_reason'] = "⚠️ 多空都達標，信號混亂 → 觀望"
            self.logger.warning(f"⚠️ v10.16 六維系統: 多空都達標，不交易")
            return False, "", data
            
        else:
            # 尚未有勝出者
            # 🔧 v13.6.1: 即使尚未勝出，也設置 pending_direction 供 pre_entry 系統提前掛單
            # 這樣當信號強度達到 pre_entry_threshold 時可以掛單
            if self.long_alignment_seconds > self.short_alignment_seconds:
                self.market_data['signal_status']['reject_reason'] = f"⏳ 多方領先 ({self.long_alignment_seconds:.1f}s/{self.min_alignment_seconds}s) 分數:{long_score}/{max_score}"
                self.market_data['signal_status']['pending_direction'] = "LONG"  # 🆕 提前設置
            elif self.short_alignment_seconds > self.long_alignment_seconds:
                self.market_data['signal_status']['reject_reason'] = f"⏳ 空方領先 ({self.short_alignment_seconds:.1f}s/{self.min_alignment_seconds}s) 分數:{short_score}/{max_score}"
                self.market_data['signal_status']['pending_direction'] = "SHORT"  # 🆕 提前設置
            else:
                self.market_data['signal_status']['reject_reason'] = f"⏳ 等待六維對齊... (多:{long_score} vs 空:{short_score})/{max_score}"
            return False, "", data
        
        # ═══════════════════════════════════════════════════════════════════
        # 🆕 v13.6: 方向過濾 (卡片可設定 allowed_directions)
        # 用於限制只做多或只做空，根據回測數據優化
        # ═══════════════════════════════════════════════════════════════════
        allowed_directions = getattr(self.config, 'allowed_directions', None)
        if allowed_directions is None:
            allowed_directions = ['LONG', 'SHORT']  # 預設允許雙向
        if direction not in allowed_directions:
            self.market_data['signal_status']['reject_reason'] = f"🚫 方向過濾: {direction} 不在允許列表 {allowed_directions}"
            self.logger.info(f"🚫 v13.6 方向過濾: {direction} 被卡片配置排除")
            return False, "", data
        
        # ═══════════════════════════════════════════════════════════════════
        # 🆕 v13.6 + v14.13: 六維分數檢查 (使用校正後的方向專用門檻)
        # ═══════════════════════════════════════════════════════════════════
        winning_score = long_score if direction == "LONG" else short_score
        # 🔧 v14.13: 修復校正門檻未生效 bug - 使用方向專用門檻
        if direction == "LONG":
            calibrated_min_score = getattr(self.config, 'six_dim_min_score_long', None) or min_score
        else:
            calibrated_min_score = getattr(self.config, 'six_dim_min_score_short', None) or min_score
        
        if winning_score < calibrated_min_score:
            self.market_data['signal_status']['reject_reason'] = f"🚫 六維分數太低 ({direction}: {winning_score}/{max_score} < {calibrated_min_score}) → 沒信號不交易"
            return False, "", data
        
        # ═══════════════════════════════════════════════════════════════════
        # 🆕 高勝率: 價格方向確認 (禁止反向交易!)
        # 只做跟實際價格趨勢同方向的單
        # ═══════════════════════════════════════════════════════════════════
        if getattr(self.config, 'price_confirm_enabled', True):
            price_change_1m = self.market_data.get('price_change_1m', 0)
            price_threshold = getattr(self.config, 'price_confirm_threshold', 0.01)
            
            # 計算價格趨勢方向
            if price_change_1m > price_threshold:
                price_trend = "UP"
            elif price_change_1m < -price_threshold:
                price_trend = "DOWN"
            else:
                price_trend = "FLAT"
            
            # 🚫 禁止反向交易 (這是提升勝率的核心!)
            if direction == "LONG" and price_trend == "DOWN":
                self.market_data['signal_status']['reject_reason'] = f"🚫 價格確認: 要做多但價格跌 {price_change_1m:+.3%} → 禁止反向"
                self.logger.info(f"🚫 高勝率價格確認失敗: 六維說LONG但價格跌 {price_change_1m:+.3%}")
                return False, "", data
            elif direction == "SHORT" and price_trend == "UP":
                self.market_data['signal_status']['reject_reason'] = f"🚫 價格確認: 要做空但價格漲 {price_change_1m:+.3%} → 禁止反向"
                self.logger.info(f"🚫 高勝率價格確認失敗: 六維說SHORT但價格漲 {price_change_1m:+.3%}")
                return False, "", data
            
            # ✅ 價格方向確認通過
            if price_trend != "FLAT":
                self.logger.info(f"✅ 高勝率價格確認通過: {direction} 與價格趨勢 {price_trend} 一致 ({price_change_1m:+.3%})")
        
        # ═══════════════════════════════════════════════════════════════════
        # 🆕 v10.15: MTF 過濾 + 能量加成 (改用 15m RSI 更靈敏)
        # 三線與MTF同方向 = 能量大，易獲利
        # ═══════════════════════════════════════════════════════════════════
        ctx_cfg = load_ctx_strategy_config()
        mtf_cfg = ctx_cfg.get('mtf_filter', {})
        mtf_filter_enabled = mtf_cfg.get('enabled', False)
        mtf_aligned = False  # 三線與MTF是否同方向
        mtf_direction = None  # MTF方向
        
        # 🔧 v14.16: 從已設置的 mtf_filter 獲取 RSI (在函數開頭已設置)
        existing_mtf = self.market_data.get('signal_status', {}).get('mtf_filter', {})
        rsi_15m = existing_mtf.get('rsi_15m')
        rsi_1h = existing_mtf.get('rsi_1h')
        
        if rsi_15m or rsi_1h:
            # 使用 15m RSI 作為主要判斷 (更靈敏)
            # 1h RSI 作為參考 (大趨勢)
            rsi_main = rsi_15m if rsi_15m else rsi_1h
            
            if rsi_main:
                # 🆕 v10.15: 調整門檻為 15m 更適合的值
                bullish_threshold = 50  # 15m RSI > 50 = 多頭
                bearish_threshold = 50  # 15m RSI < 50 = 空頭
                
                # 判斷 MTF 方向 (用 15m RSI)
                if rsi_main > bullish_threshold:
                    mtf_direction = "LONG"
                elif rsi_main < bearish_threshold:
                    mtf_direction = "SHORT"
                else:
                    mtf_direction = "NEUTRAL"
                
                # 🔧 v14.16: 更新已有的 mtf_filter 而不是覆蓋
                self.market_data['signal_status']['mtf_filter'].update({
                    'mtf_direction': mtf_direction,
                    'bullish': rsi_main > bullish_threshold,
                    'bearish': rsi_main < bearish_threshold,
                })
                
                # 檢查三線與MTF是否同方向
                if direction == mtf_direction:
                    mtf_aligned = True
                    self.market_data['signal_status']['mtf_aligned'] = True
                    self.logger.info(f"💪 v10.15 能量加成! 三線({direction}) + MTF 15m RSI={rsi_main:.0f} ({mtf_direction}) 同方向")
                
                # MTF 過濾 (只在啟用時才阻擋，且使用更嚴格的門檻)
                if mtf_filter_enabled:
                    strict_bullish = 55  # 嚴格多頭門檻
                    strict_bearish = 45  # 嚴格空頭門檻
                    
                    # RSI > 55 不做空 (強多頭)
                    if rsi_main > strict_bullish and direction == "SHORT":
                        self.market_data['signal_status']['reject_reason'] = f"🚫 MTF: 15m RSI={rsi_main:.0f}>{strict_bullish} 強多頭 → 不做空"
                        self.logger.info(f"🔴 v10.15 MTF 過濾: 15m RSI={rsi_main:.0f} → 拒絕做空")
                        return False, "", data
                    
                    # RSI < 45 不做多 (強空頭)
                    if rsi_main < strict_bearish and direction == "LONG":
                        reject_reason = f"🚫 MTF: 15m RSI={rsi_main:.0f}<{strict_bearish} 強空頭 → 不做多"
                        self.market_data['signal_status']['reject_reason'] = reject_reason
                        self.logger.info(f"🔴 v10.15 MTF 過濾: 15m RSI={rsi_main:.0f} → 拒絕做多")
                        # 🆕 記錄被拒絕的信號
                        self.trader._save_signal_log(
                            'REJECTED_MTF', direction, reject_reason,
                            market_data={'current_price': self._get_market_price(), 'obi': data.get('obi', 0), 'regime': self.market_regime},
                            six_dim=six_dim,
                            mtf_filter=self.market_data.get('signal_status', {}).get('mtf_filter', {}),
                            alignment={'long_sec': round(self.long_alignment_seconds, 1), 'short_sec': round(self.short_alignment_seconds, 1)}
                        )
                        return False, "", data
        
        # ═══════════════════════════════════════════════════════════════════
        # ✅ 通過所有檢查，允許進場
        # ═══════════════════════════════════════════════════════════════════
        energy_msg = "💪能量加成!" if mtf_aligned else ""
        self.market_data['signal_status']['mtf_aligned'] = mtf_aligned
        
        # 顯示六維信息
        six_dim_info = ""
        if self.config.six_dim_enabled and six_dim:
            obi_dir = six_dim.get('obi_dir', 'N/A')
            mom_dir = six_dim.get('momentum_dir', 'N/A')
            vol_dir = six_dim.get('volume_dir', 'N/A')
            six_dim_info = f" | OBI:{obi_dir} 動能:{mom_dir} 成交量:{vol_dir}"
        
        self.logger.info(f"✅ v10.16 六維系統允許進場: {direction} (分數:{long_score if direction=='LONG' else short_score}/{max_score}){six_dim_info} {energy_msg}")
        
        # 🆕 v14.16: 最後一道 Veto 檢查
        veto_passed, veto_reason = self.trader.check_entry_veto(direction, data)
        if not veto_passed:
            self.market_data['signal_status']['reject_reason'] = f"Veto 阻擋: {veto_reason}"
            return False, "", data

        # 🆕 記錄成功進場的信號
        current_score = long_score if direction == "LONG" else short_score
        self.trader._save_signal_log(
            'ENTERED', direction, f"六維{direction} 分數:{current_score}/{max_score} {energy_msg}",
            market_data={'current_price': self._get_market_price(), 'obi': data.get('obi', 0), 'regime': self.market_regime},
            six_dim=six_dim,
            mtf_filter=self.market_data.get('signal_status', {}).get('mtf_filter', {}),
            alignment={'long_sec': round(self.long_alignment_seconds, 1), 'short_sec': round(self.short_alignment_seconds, 1)}
        )
        
        return True, direction, data
    
    def _get_obi_zone(self, obi: float) -> str:
        """獲取 OBI 區域描述"""
        if obi > self.config.ctx_obi_very_high:
            return "極高(SHORT+穩定佳)"
        elif obi > self.config.ctx_obi_high_threshold:
            return "高(LONG+低機率佳)"
        elif obi > self.config.ctx_obi_mode_a_min:
            return "中高(MODE_A抄底佳)"
        elif obi < self.config.ctx_obi_low_threshold:
            return "低(禁止做多!)"
        else:
            return "中性(MODE_C精準空)"
    
    def _reset_signal_tracking(self):
        """重置信號追蹤狀態"""
        self.confirmed_signal = None
        self.signal_confirm_start = 0
    
    def _record_signal_snapshot(self):
        """
        🆕 每秒記錄當前信號狀態快照
        不管有沒有進場都會記錄，用於事後分析
        """
        # 🆕 v14.16.2: 直接從 MTF analyzer 讀取 RSI，不依賴 signal_status
        mtf_filter = {}
        if self.mtf_analyzer and self.mtf_analyzer.latest_snapshot:
            snapshot = self.mtf_analyzer.latest_snapshot
            mtf_filter = {
                'rsi_1m': snapshot.tf_1m.rsi if snapshot.tf_1m else None,
                'rsi_5m': snapshot.tf_5m.rsi if snapshot.tf_5m else None,
                'rsi_15m': snapshot.tf_15m.rsi if snapshot.tf_15m else None,
                'rsi_1h': snapshot.tf_1h.rsi if snapshot.tf_1h else None,
                'rsi_4h': snapshot.tf_4h.rsi if snapshot.tf_4h else None,
                'mtf_direction': snapshot.trend_filter if snapshot else '',
                'mtf_aligned': 'ALIGNED' in (snapshot.trend_alignment or ''),
            }
        
        # 取得當前六維數據
        six_dim = self.market_data.get('six_dim', {})
        data = self.market_data
        
        # 判斷當前信號狀態
        long_stable = self.long_alignment_seconds >= self.min_alignment_seconds
        short_stable = self.short_alignment_seconds >= self.min_alignment_seconds
        
        if long_stable and not short_stable:
            signal_status = "LONG_READY"
            direction = "LONG"
        elif short_stable and not long_stable:
            signal_status = "SHORT_READY"
            direction = "SHORT"
        elif long_stable and short_stable:
            signal_status = "CONFLICT"
            direction = "NONE"
        else:
            # 判斷哪方領先
            if self.long_alignment_seconds > self.short_alignment_seconds:
                signal_status = "LONG_LEADING"
                direction = "LONG"
            elif self.short_alignment_seconds > self.long_alignment_seconds:
                signal_status = "SHORT_LEADING"
                direction = "SHORT"
            else:
                signal_status = "NEUTRAL"
                direction = "NONE"
        
        # 🔧 v14.16.2: mtf_filter 已在函數開頭從 MTF analyzer 直接讀取
        
        # 記錄快照
        self.trader._save_signal_log(
            signal_status, direction, 
            f"L:{self.long_alignment_seconds:.1f}s S:{self.short_alignment_seconds:.1f}s",
            market_data={'current_price': self._get_market_price(), 'obi': data.get('obi', 0), 'regime': self.market_regime},
            six_dim=six_dim,
            mtf_filter=mtf_filter,
            alignment={'long_sec': round(self.long_alignment_seconds, 1), 'short_sec': round(self.short_alignment_seconds, 1)}
        )
        
        # 🆕 v13.6: 同時記錄到統一回測收集器
        if self.backtest_collector:
            try:
                self.backtest_collector.record_signal(
                    signal_type=signal_status,
                    direction=direction,
                    reason=f"L:{self.long_alignment_seconds:.1f}s S:{self.short_alignment_seconds:.1f}s",
                    price=self._get_market_price(),
                    six_dim={
                        'long_score': six_dim.get('long_score', 0),
                        'short_score': six_dim.get('short_score', 0),
                        'fast_dir': six_dim.get('fast_dir', 'NEUTRAL'),
                        'medium_dir': six_dim.get('medium_dir', 'NEUTRAL'),
                        'slow_dir': six_dim.get('slow_dir', 'NEUTRAL'),
                        'obi_dir': six_dim.get('obi_dir', 'NEUTRAL'),
                        'momentum_dir': six_dim.get('momentum_dir', 'NEUTRAL'),
                        'volume_dir': six_dim.get('volume_dir', 'NEUTRAL'),
                    },
                    mtf={
                        'rsi_1m': mtf_filter.get('rsi_1m', 50),
                        'rsi_5m': mtf_filter.get('rsi_5m', 50),
                        'rsi_15m': mtf_filter.get('rsi_15m', 50),
                    },
                    market={
                        'obi': data.get('obi', 0),
                        'regime': self.market_regime,
                    },
                    alignment={
                        'long_sec': round(self.long_alignment_seconds, 1),
                        'short_sec': round(self.short_alignment_seconds, 1)
                    }
                )
            except Exception:
                pass  # 靜默失敗

    def _is_signal_stable(self, expected_direction: str) -> bool:
        """
        檢查最近的信號是否穩定一致
        
        需要最近 signal_confirm_seconds 秒內的信號都是同一方向
        """
        if len(self.signal_history) < 3:
            return False
        
        # 檢查最近的信號是否都是同一方向
        recent_signals = [s for s in self.signal_history 
                        if time.time() - s['timestamp'] < self.config.signal_confirm_seconds]
        
        if len(recent_signals) < 3:
            return False
        
        for sig in recent_signals:
            if sig['direction'] != expected_direction:
                return False
        
        return True
    
    def check_whale_emergency(self) -> Optional[Dict]:
        """
        🚨 緊急大單偵測 (每秒執行)
        
        偵測鯨魚砸盤/拉盤，需要即時反應：
        1. 單筆超大單 (>$500K) - 緊急警報
        2. 連環大單 (10秒內 3+ 筆 >$100K 同方向) - 連環砸盤/拉盤
        
        Returns:
            警報資訊 dict 或 None
        """
        market_ws = self._get_market_ws()
        big_trades = list(market_ws.big_trades)
        if not big_trades:
            return None
        
        now = time.time()
        alerts = []
        
        # 1. 檢查單筆超大單
        recent_mega_trades = [
            t for t in big_trades 
            if now - t.get('timestamp', 0) < 3  # 最近 3 秒
            and t.get('value_usdt', 0) >= self.config.whale_emergency_threshold_usdt
        ]
        
        for t in recent_mega_trades:
            direction = "賣出砸盤" if not t.get('is_buy') else "買入拉盤"
            alerts.append({
                'type': 'MEGA_WHALE',
                'level': 'EMERGENCY',
                'value': t.get('value_usdt', 0),
                'direction': 'SHORT' if not t.get('is_buy') else 'LONG',
                'message': f"🚨 緊急! 鯨魚{direction} ${t.get('value_usdt', 0)/1000:.0f}K",
                'action': 'CLOSE_OPPOSITE'  # 建議平掉反向倉位
            })
        
        # 2. 檢查連環大單 (cascade) - 防止 None 值
        cascade_window = self.config.whale_cascade_window_sec or 300
        alert_threshold = self.config.whale_alert_threshold_usdt or 100000
        cascade_count = self.config.whale_cascade_count or 3
        
        recent_big = [
            t for t in big_trades
            if now - t.get('timestamp', 0) < cascade_window
            and t.get('value_usdt', 0) >= alert_threshold
        ]
        
        if len(recent_big) >= cascade_count:
            # 統計買賣方向
            buy_count = sum(1 for t in recent_big if t.get('is_buy'))
            sell_count = len(recent_big) - buy_count
            
            if sell_count >= cascade_count:
                total_value = sum(t.get('value_usdt', 0) for t in recent_big if not t.get('is_buy'))
                alerts.append({
                    'type': 'CASCADE_SELL',
                    'level': 'WARNING',
                    'count': sell_count,
                    'total_value': total_value,
                    'direction': 'SHORT',
                    'message': f"⚠️ 連環砸盤! {sell_count}筆賣單 共${total_value/1000:.0f}K",
                    'action': 'CLOSE_LONG'
                })
            
            if buy_count >= cascade_count:
                total_value = sum(t.get('value_usdt', 0) for t in recent_big if t.get('is_buy'))
                alerts.append({
                    'type': 'CASCADE_BUY',
                    'level': 'WARNING',
                    'count': buy_count,
                    'total_value': total_value,
                    'direction': 'LONG',
                    'message': f"⚠️ 連環拉盤! {buy_count}筆買單 共${total_value/1000:.0f}K",
                    'action': 'CLOSE_SHORT'
                })
        
        if alerts:
            # 取最嚴重的警報
            emergency = [a for a in alerts if a['level'] == 'EMERGENCY']
            return emergency[0] if emergency else alerts[0]
        
        return None
    
    def check_price_spike(self) -> Optional[Dict]:
        """
        🚨 v12.10: 急跌急漲偵測 (每秒執行)
        
        偵測價格異常波動，捕捉 1 分鐘內的急速變動:
        - 急跌: 價格下跌 > 0.25% (做空信號)
        - 急漲: 價格上漲 > 0.25% (做多信號)
        
        Returns:
            警報資訊 dict 或 None
        """
        if not self.config.price_spike_enabled:
            return None
        
        # 冷卻時間檢查
        now = time.time()
        cooldown = self.config.price_spike_alert_cooldown or 60
        if hasattr(self, '_last_price_spike_alert'):
            if now - self._last_price_spike_alert < cooldown:
                return None
        
        # 計算價格變化 (防止 None 值)
        window_sec = self.config.price_spike_window_sec or 60
        market_ws = self._get_market_ws()
        price_change_pct = market_ws.get_price_change(int(window_sec))
        
        threshold = self.config.price_spike_threshold_pct or 1.0
        
        if abs(price_change_pct) >= threshold:
            self._last_price_spike_alert = now
            
            if price_change_pct <= -threshold:
                # 急跌
                return {
                    'type': 'PRICE_SPIKE',
                    'direction': 'SHORT',
                    'price_change_pct': price_change_pct,
                    'level': 'ALERT',
                    'message': f"📉 急跌警報! {price_change_pct:.2f}% (60秒內)",
                    'signal': 'SHORT',  # 建議做空
                    'timestamp': now
                }
            else:
                # 急漲
                return {
                    'type': 'PRICE_SPIKE',
                    'direction': 'LONG',
                    'price_change_pct': price_change_pct,
                    'level': 'ALERT',
                    'message': f"📈 急漲警報! +{price_change_pct:.2f}% (60秒內)",
                    'signal': 'LONG',  # 建議做多
                    'timestamp': now
                }
        
        return None
    
    def handle_whale_emergency(self, alert: Dict) -> bool:
        """
        處理鯨魚緊急警報
        
        Returns:
            是否執行了緊急平倉
        """
        if not self.trader.active_trade:
            return False
        
        trade = self.trader.active_trade
        action = alert.get('action', '')
        
        # 檢查是否需要緊急平倉
        should_close = False
        close_reason = ""
        
        if action == 'CLOSE_OPPOSITE':
            # 超大單：平掉反向倉位
            if alert['direction'] == 'SHORT' and trade.direction == 'LONG':
                should_close = True
                close_reason = f"🚨 鯨魚砸盤緊急平倉 (做多→砸盤)"
            elif alert['direction'] == 'LONG' and trade.direction == 'SHORT':
                should_close = True
                close_reason = f"🚨 鯨魚拉盤緊急平倉 (做空→拉盤)"
        
        elif action == 'CLOSE_LONG' and trade.direction == 'LONG':
            should_close = True
            close_reason = f"⚠️ 連環砸盤平多單"
        
        elif action == 'CLOSE_SHORT' and trade.direction == 'SHORT':
            should_close = True
            close_reason = f"⚠️ 連環拉盤平空單"
        
        if should_close:
            self.logger.warning(f"{close_reason} | {alert['message']}")
            # 🔧 v14.6.23: 使用正確價格源 (dYdX sync 模式用 Oracle Price)
            price_ctx = self._get_price_context()
            exit_price = self.get_net_price_for_direction(trade.direction, price_ctx)
            self.trader.close_position(close_reason, exit_price)
            return True
        
        return False
    
    def get_entry_suggestion(self, direction: str, current_price: float) -> Dict:
        """
        根據策略分析結果生成進場建議
        
        Args:
            direction: 'LONG' 或 'SHORT'
            current_price: 當前價格
            
        Returns:
            包含進場價、止盈、止損等建議的字典
        """
        # 基於策略類型調整 TP/SL
        strategy = self.market_data.get('primary_strategy')
        
        # 預設參數
        tp_pct = 0.005  # 0.5% 止盈
        sl_pct = 0.003  # 0.3% 止損
        
        if strategy:
            strategy_name = strategy.strategy.value if hasattr(strategy, 'strategy') else str(strategy)
            
            # 根據不同策略調整 TP/SL
            if '推動' in strategy_name or '拉升' in strategy_name:
                tp_pct = 0.008  # 趨勢策略用較大 TP
                sl_pct = 0.004
            elif '突破' in strategy_name:
                tp_pct = 0.010  # 突破策略用更大 TP
                sl_pct = 0.005
            elif '吸籌' in strategy_name or '建倉' in strategy_name:
                tp_pct = 0.006
                sl_pct = 0.003
        
        if direction == 'LONG':
            entry_price = current_price
            tp_price = current_price * (1 + tp_pct)
            sl_price = current_price * (1 - sl_pct)
        else:  # SHORT
            entry_price = current_price
            tp_price = current_price * (1 - tp_pct)
            sl_price = current_price * (1 + sl_pct)
        
        risk_reward = tp_pct / sl_pct if sl_pct > 0 else 1.0
        
        return {
            'direction': direction,
            'entry_price': entry_price,
            'tp_price': tp_price,
            'sl_price': sl_price,
            'tp_pct': tp_pct,
            'sl_pct': sl_pct,
            'expected_pnl_pct': tp_pct,
            'risk_reward_ratio': risk_reward
        }
    
    def handle_prediction_error(self, error_type: str, details: Dict):
        """
        處理預測錯誤，用於後續分析改進
        
        Args:
            error_type: 錯誤類型 ('false_signal', 'missed_opportunity', 'early_exit', etc.)
            details: 錯誤詳情
        """
        market_ws = self._get_market_ws()
        error_record = {
            'timestamp': datetime.now().isoformat(),
            'error_type': error_type,
            'price': market_ws.current_price,
            'market_data': {
                'obi': market_ws.get_obi(),
                'trade_imbalance': market_ws.get_trade_imbalance_1s(),
            },
            'details': details
        }
        
        # 記錄到日誌
        self.logger.warning(f"預測錯誤: {error_type} | {details}")
        
        # 可以保存到檔案供後續分析
        error_file = self.training_data_dir / "prediction_errors.json"
        try:
            if error_file.exists():
                with open(error_file, 'r') as f:
                    errors = json.load(f)
            else:
                errors = []
            
            errors.append(error_record)
            
            with open(error_file, 'w') as f:
                json.dump(errors, f, indent=2, default=str)
        except Exception as e:
            self.logger.error(f"保存預測錯誤失敗: {e}")
    
    def render_dashboard(self) -> str:
        """渲染即時儀表板 (整合策略分析 + WebSocket 數據)"""
        R = '\033[0m'
        B = '\033[1m'
        g = '\033[32m'
        r = '\033[31m'
        y = '\033[33m'
        c = '\033[36m'
        G_ = '\033[92m'
        R_ = '\033[91m'
        Y_ = '\033[93m'
        m = '\033[35m'  # magenta
        
        now = datetime.now().strftime('%H:%M:%S')
        
        # 價格來源：
        # - 🔧 v14.6.36: 統一使用 dYdX Oracle Price (交易和顯示一致)
        price = self.get_current_price()
        price_ctx = self._get_price_context()
        
        # 交易模式標籤
        mode_label = f"{Y_}[PAPER 模擬]{R}" if self.config.paper_mode else f"{G_}[TESTNET 測試網]{R}"
        if getattr(self.trader, 'dydx_sync_enabled', False):
            mode_label += f" + {R_}🔴dYdX 同步{R}"
        
        # 🔧 v14.6.38: 顯示卡片名稱
        card_id = getattr(self.config, 'card_id', '') or ''
        card_display = getattr(self.config, 'card_name', None) or card_id or 'whale_trader'
        
        lines = []
        lines.append(f"{c}{'='*80}{R}")
        lines.append(f"{c}{B}card {card_display}{R} {mode_label}  {Y_}BTC ${price:,.2f}{R}  {now}  #{self.iteration}")
        lines.append(f"{c}{'='*80}{R}")
        
        # 🆕 v14.x: 隨機入場模式時，隱藏策略分析區塊 (因為不使用分析進場)
        show_strategy_analysis = not getattr(self.config, 'random_entry_mode', False)
        
        # 隨機模式簡潔提示
        if not show_strategy_analysis:
            lines.append(f"\n{Y_}🎲 隨機入場模式{R} - 策略分析區塊已隱藏 (進場方向隨機，出場按止盈止損)")
            wave1 = []
            wave2 = []
            try:
                wave1, wave2 = self._get_balanced_direction_preview()
            except Exception:
                wave1, wave2 = [], []
            if wave1 or wave2:
                def _dir_icon(d: str) -> str:
                    return "🟢" if d == "LONG" else "🔴"
                wave1_line = ", ".join(_dir_icon(d) for d in wave1)
                wave2_line = ", ".join(_dir_icon(d) for d in wave2)
                lines.append(f"   第1波：{wave1_line}")
                if wave2_line or wave2:
                    lines.append(f"   第2波：{wave2_line}")
        
        # ==================== 第一區塊：即時市場數據 ====================
        if show_strategy_analysis:
            obi = self.market_data.get('obi', 0)
            wpi = self.market_data.get('trade_imbalance', 0)
            obi_c = G_ if obi > 0.3 else R_ if obi < -0.3 else y
            wpi_c = G_ if wpi > 0.3 else R_ if wpi < -0.3 else y
            
            lines.append(f"\n{B}📊 即時數據 (Binance Brain){R}")
            lines.append(f"   OBI: {obi_c}{obi:+.3f}{R}  WPI: {wpi_c}{wpi:+.3f}{R}")
            lines.append(f"   1分鐘: {self.market_data.get('price_change_1m', 0):+.3f}%  "
                        f"5分鐘: {self.market_data.get('price_change_5m', 0):+.3f}%")
            lines.append(f"   Spread: {self.market_data.get('spread_pct', 0):.4f}%")
            
            # 🆕 v14.8: 進階風控狀態顯示
            if hasattr(self, 'spread_guard') and self.spread_guard:
                # 獲取即時市場快照和風控狀態
                snapshot = self.spread_guard.get_market_snapshot()
                risk_state = self.spread_guard.current_state
                band_entry = self.spread_guard.band_entry
                band_halt = self.spread_guard.band_halt
                
                # 計算各項指標
                oracle_gap, oracle_status = self.spread_guard.calculate_oracle_gap(snapshot)
                effective_diff = self.spread_guard.calculate_effective_diff(snapshot, "BUY", 0.002)
                
                # 狀態顏色編碼
                state_colors = {
                    'CAN_TRADE': (G_, '✅'),
                    'SUSPECT': (y, '⚠️'),
                    'HALT': (R_, '🛑'),
                    'ESCAPE': (R_, '🚨')
                }
                state_c, state_icon = state_colors.get(risk_state.value, (y, '❓'))
                
                lines.append(f"\n{B}🛡️ 進階風控 v14.9{R}")
                lines.append(f"   狀態: {state_c}{risk_state.value}{R} {state_icon}  "
                           f"Band: {band_entry:.3f}%/{band_halt:.3f}%")
                lines.append(f"   價差: {effective_diff:.4f}%  Oracle: {oracle_gap:.4f}% ({oracle_status})")
                lines.append(f"   波動: {snapshot.vol_1s:.4f}%  延遲: {snapshot.lat_ms:.0f}ms")
                
                # 如果不是 CAN_TRADE，顯示原因
                if risk_state != TradingState.CAN_TRADE:
                    state_duration = time.time() - self.spread_guard.state_since
                    lines.append(f"   {state_c}持續 {state_duration:.0f}s{R}")
            
            # 大單統計
            big_count = self.market_data.get('big_trade_count', 0)
            big_buy = self.market_data.get('big_buy_value', 0)
            big_sell = self.market_data.get('big_sell_value', 0)
            if big_count > 0:
                buy_c = G_ if big_buy > big_sell else y
                sell_c = R_ if big_sell > big_buy else y
                lines.append(f"\n{B}🐋 大單追蹤 (>$8K){R}")
                lines.append(f"   數量: {big_count}筆  買: {buy_c}${big_buy/1000:.1f}K{R}  賣: {sell_c}${big_sell/1000:.1f}K{R}")
        
        # 🚨 鯨魚緊急警報 (警報一律顯示)
        whale_alert = self.market_data.get('whale_alert')
        if whale_alert:
            alert_level = whale_alert.get('level', '')
            if alert_level == 'EMERGENCY':
                lines.append(f"\n{R_}{'!'*60}{R}")
                lines.append(f"{R_}{B}🚨 緊急警報: {whale_alert['message']}{R}")
                lines.append(f"{R_}{'!'*60}{R}")
            else:
                lines.append(f"\n{Y_}⚠️ 警告: {whale_alert['message']}{R}")
        
        # 🆕 v12.10: 急跌急漲警報 (警報一律顯示)
        price_spike = self.market_data.get('price_spike')
        if price_spike:
            spike_dir = price_spike.get('direction', '')
            spike_pct = price_spike.get('price_change_pct', 0)
            spike_msg = price_spike.get('message', '')
            if spike_dir == 'SHORT':
                # 急跌 - 紅色警示
                lines.append(f"\n{R_}{'*'*60}{R}")
                lines.append(f"{R_}{B}{spike_msg}  →  建議: 做空{R}")
                lines.append(f"{R_}{'*'*60}{R}")
            else:
                # 急漲 - 綠色警示
                lines.append(f"\n{G_}{'*'*60}{R}")
                lines.append(f"{G_}{B}{spike_msg}  →  建議: 做多{R}")
                lines.append(f"{G_}{'*'*60}{R}")
        
        # ==================== 第二區塊：主力策略分析 (完整) ====================
        # 隨機入場模式時跳過此區塊
        strategy_probs = self.market_data.get('strategy_probs', {}) if show_strategy_analysis else {}
        
        if show_strategy_analysis:
            next_analysis = self.market_data.get('next_strategy_analysis', 0)
            buffer_count = self.market_data.get('buffer_count', 0)
            
            lines.append(f"\n{c}{'-'*80}{R}")
            lines.append(f"{B}🎯 主力策略識別系統 v5.9{R}  {G_}(即時){R} | {y}決策: {next_analysis:.0f}秒後 ({buffer_count}筆){R}")
            lines.append(f"{c}{'-'*80}{R}")
        
        if strategy_probs:
            # 按類別分組顯示
            categories = {
                '誘騙類': ['BULL_TRAP', 'BEAR_TRAP', 'FAKEOUT', 'STOP_HUNT', 'SPOOFING'],
                '清洗類': ['WHIPSAW', 'CONSOLIDATION_SHAKE', 'FLASH_CRASH', 'SLOW_BLEED'],
                '吸籌派發': ['ACCUMULATION', 'DISTRIBUTION', 'RE_ACCUMULATION', 'RE_DISTRIBUTION'],
                '爆倉類': ['LONG_SQUEEZE', 'SHORT_SQUEEZE', 'CASCADE_LIQUIDATION'],
                '趨勢類': ['MOMENTUM_PUSH', 'TREND_CONTINUATION', 'REVERSAL'],
                '特殊類': ['PUMP_DUMP', 'WASH_TRADING', 'LAYERING'],
            }
            
            # 中文名稱映射
            name_map = {
                'BULL_TRAP': '多頭陷阱', 'BEAR_TRAP': '空頭陷阱', 'FAKEOUT': '假突破',
                'STOP_HUNT': '獵殺止損', 'SPOOFING': '幌騙', 'WHIPSAW': '鋸齒洗盤',
                'CONSOLIDATION_SHAKE': '盤整洗盤', 'FLASH_CRASH': '閃崩洗盤', 'SLOW_BLEED': '陰跌洗盤',
                'ACCUMULATION': '吸籌建倉', 'DISTRIBUTION': '派發出貨', 
                'RE_ACCUMULATION': '再吸籌', 'RE_DISTRIBUTION': '再派發',
                'LONG_SQUEEZE': '多頭擠壓', 'SHORT_SQUEEZE': '空頭擠壓', 'CASCADE_LIQUIDATION': '連環爆倉',
                'MOMENTUM_PUSH': '趨勢推動', 'TREND_CONTINUATION': '趨勢延續', 'REVERSAL': '趨勢反轉',
                'PUMP_DUMP': '拉高出貨', 'WASH_TRADING': '對敲拉抬', 'LAYERING': '層疊掛單',
                'NORMAL': '正常波動'
            }
            
            # 建議方向
            direction_map = {
                'BULL_TRAP': ('做空', R_), 'BEAR_TRAP': ('做多', G_), 'FAKEOUT': ('觀望', y),
                'STOP_HUNT': ('觀望', y), 'SPOOFING': ('觀望', y), 'WHIPSAW': ('觀望', y),
                'CONSOLIDATION_SHAKE': ('觀望', y), 'FLASH_CRASH': ('做多', G_), 'SLOW_BLEED': ('觀望', y),
                'ACCUMULATION': ('做多', G_), 'DISTRIBUTION': ('做空', R_),
                'RE_ACCUMULATION': ('做多', G_), 'RE_DISTRIBUTION': ('做空', R_),
                'LONG_SQUEEZE': ('做空', R_), 'SHORT_SQUEEZE': ('做多', G_), 'CASCADE_LIQUIDATION': ('觀望', y),
                'MOMENTUM_PUSH': ('觀望', y), 'TREND_CONTINUATION': ('觀望', y), 'REVERSAL': ('觀望', y),
                'PUMP_DUMP': ('做空', R_), 'WASH_TRADING': ('觀望', y), 'LAYERING': ('觀望', y),
            }
            
            # 🆕 v4.0 陷阱類型定義 (這些策略本身就是陷阱/洗盤，跟單會虧錢)
            trap_strategies = {
                'BULL_TRAP': 100,      # 多頭陷阱 - 做多必虧
                'BEAR_TRAP': 100,      # 空頭陷阱 - 做空必虧
                'FAKEOUT': 90,         # 假突破 - 追突破必虧
                'STOP_HUNT': 85,       # 獵殺止損 - 設止損被打
                'SPOOFING': 80,        # 幌騙 - 假單誘導
                'WHIPSAW': 95,         # 鋸齒洗盤 - 兩邊打臉
                'CONSOLIDATION_SHAKE': 70,  # 盤整洗盤
                'FLASH_CRASH': 60,     # 閃崩 - 恐慌性陷阱
                'SLOW_BLEED': 50,      # 陰跌 - 溫水煮青蛙
                'PUMP_DUMP': 95,       # 拉高出貨 - 追高必虧
                'WASH_TRADING': 75,    # 對敲 - 假量誘導
                'LAYERING': 70,        # 層疊掛單 - 虛假掛單
                'LONG_SQUEEZE': 40,    # 多頭擠壓 - 部分陷阱性質
                'SHORT_SQUEEZE': 40,   # 空頭擠壓 - 部分陷阱性質
            }
            # 這些是真實機會，不是陷阱
            opportunity_strategies = ['ACCUMULATION', 'DISTRIBUTION', 'RE_ACCUMULATION', 'RE_DISTRIBUTION',
                                      'MOMENTUM_PUSH', 'TREND_CONTINUATION', 'REVERSAL', 'CASCADE_LIQUIDATION']
            
            # 找出最高機率的策略
            sorted_all = sorted(strategy_probs.items(), key=lambda x: x[1], reverse=True)
            top_strategy_name = sorted_all[0][0] if sorted_all else ''
            top_strategy_prob = sorted_all[0][1] if sorted_all else 0
            
            for cat_name, strategies in categories.items():
                cat_probs = [(s, strategy_probs.get(s, 0)) for s in strategies]
                max_prob = max(p for _, p in cat_probs) if cat_probs else 0
                
                # 類別標題 (如果有高機率就高亮)
                cat_c = Y_ if max_prob > 0.5 else y if max_prob > 0.2 else ''
                lines.append(f"\n{cat_c}📁 {cat_name}{R}")
                
                for s_name, prob in cat_probs:
                    cn_name = name_map.get(s_name, s_name)
                    direction, dir_c = direction_map.get(s_name, ('觀望', y))
                    
                    # 機率條
                    bar_len = int(prob * 20)
                    bar = '█' * bar_len + '░' * (20 - bar_len)
                    
                    # 顏色
                    if prob > 0.7:
                        prob_c = G_
                    elif prob > 0.3:
                        prob_c = Y_
                    else:
                        prob_c = ''
                    
                    # 高亮最高的策略
                    highlight = '>>> ' if s_name == top_strategy_name and prob > 0.3 else '    '
                    
                    # 🆕 v4.0 計算陷阱機率 = 策略本身陷阱性 × 當前偵測機率
                    base_trap = trap_strategies.get(s_name, 0)
                    if s_name in opportunity_strategies:
                        # 機會型策略：陷阱機率 = 相反策略的機率
                        if s_name == 'ACCUMULATION':
                            trap_prob = strategy_probs.get('DISTRIBUTION', 0) * 30  # 可能是假吸籌
                        elif s_name == 'DISTRIBUTION':
                            trap_prob = strategy_probs.get('ACCUMULATION', 0) * 30
                        else:
                            trap_prob = 0
                    else:
                        # 陷阱型策略：陷阱機率 = 基礎陷阱性 × 偵測機率
                        trap_prob = base_trap * prob
                    
                    # 陷阱機率顏色
                    if trap_prob >= 50:
                        trap_c = R_
                        trap_icon = "⚠️"
                    elif trap_prob >= 20:
                        trap_c = Y_
                        trap_icon = "⚡"
                    else:
                        trap_c = G_
                        trap_icon = "✓"
                    
                    lines.append(f"{highlight}{cn_name:<10} |{prob_c}{bar}{R}| {prob:5.1%} | {dir_c}{direction}{R} | {trap_c}{trap_icon}{trap_prob:4.0f}%{R}")
            else:
                lines.append(f"   {y}⏳ 偵測器初始化中...{R}")
        
        # ==================== 第三區塊：觸發策略與信號 ====================
        # 隨機入場模式時跳過 MTF 和三週期分析
        if show_strategy_analysis:
            lines.append(f"\n{c}{'-'*80}{R}")
        
        # 🆕 v5.0 MTF 多時間框架分析
        if show_strategy_analysis and self.mtf_analyzer and self.mtf_enabled and self.mtf_analyzer.latest_snapshot:
            mtf = self.mtf_analyzer.latest_snapshot
            lines.append(f"{B}📊 MTF 多時間框架分析{R}")
            
            # 各時間框架一行顯示
            tf_displays = []
            for tf_name, tf_data in [("1m", mtf.tf_1m), ("5m", mtf.tf_5m), ("15m", mtf.tf_15m), ("1h", mtf.tf_1h), ("4h", mtf.tf_4h)]:
                if tf_data:
                    if tf_data.signal.value == "多":
                        tf_c = G_
                        tf_icon = "🟢"
                    elif tf_data.signal.value == "空":
                        tf_c = R_
                        tf_icon = "🔴"
                    else:
                        tf_c = y
                        tf_icon = "⚪"
                    tf_displays.append(f"{tf_name}:{tf_c}{tf_icon}{tf_data.signal.value}{R}(RSI:{tf_data.rsi:.0f})")
                else:
                    tf_displays.append(f"{tf_name}:⏳")
            
            lines.append(f"   時間框架: {' | '.join(tf_displays)}")
            
            # 趨勢對齊
            align_c = G_ if mtf.alignment_score > 30 else (R_ if mtf.alignment_score < -30 else y)
            align_icon = "✅" if "ALIGNED" in mtf.trend_alignment else "⚠️"
            lines.append(f"   趨勢對齊: {align_icon} {mtf.trend_alignment}  分數: {align_c}{mtf.alignment_score:+.0f}{R}  主趨勢: {mtf.dominant_trend.value}")
            
            # 關鍵價位
            if mtf.nearest_support > 0 or mtf.nearest_resistance > 0:
                support_str = f"${mtf.nearest_support:,.0f}" if mtf.nearest_support > 0 else "N/A"
                resist_str = f"${mtf.nearest_resistance:,.0f}" if mtf.nearest_resistance > 0 else "N/A"
                lines.append(f"   支撐: {G_}{support_str}{R}  |  阻力: {R_}{resist_str}{R}")
            
            # 交易過濾
            filter_c = G_ if mtf.trend_filter == "ALLOW_ALL" else (Y_ if "ONLY" in mtf.trend_filter else R_)
            filter_icon = "🟢" if mtf.trend_filter == "ALLOW_ALL" else ("🔵" if "ONLY" in mtf.trend_filter else "🔴")
            lines.append(f"   交易過濾: {filter_c}{filter_icon} {mtf.trend_filter}{R} - {mtf.filter_reason}")
            
            # 🆕 v4.0 K線預測 (5分鐘)
            try:
                pred = self.mtf_analyzer.predict_next_candle()
                if pred and pred.get('confidence', 0) > 0:
                    # 方向圖示
                    if pred['direction'] == 'LONG':
                        dir_c, dir_icon = G_, "🟢"
                    elif pred['direction'] == 'SHORT':
                        dir_c, dir_icon = R_, "🔴"
                    else:
                        dir_c, dir_icon = y, "⚪"
                    
                    # 信心度進度條
                    conf = pred['confidence']
                    conf_bar = "█" * int(conf / 10) + "░" * (10 - int(conf / 10))
                    
                    lines.append(f"\n{B}🔮 K線預測 (5m){R}")
                    lines.append(f"   預測: {dir_c}{dir_icon} {pred['direction']}{R}  信心: [{conf_bar}] {conf:.0f}%")
                    
                    # 價格預測
                    if pred.get('entry_price', 0) > 0:
                        lines.append(f"   進場: ${pred['entry_price']:,.2f}  |  止盈: {G_}${pred['take_profit']:,.2f}{R}  |  止損: {R_}${pred['stop_loss']:,.2f}{R}")
                    
                    # 預測理由 (最多顯示 2 個)
                    reasons = pred.get('reasons', [])[:2]
                    if reasons:
                        lines.append(f"   理由: {' | '.join(reasons)}")
            except Exception as e:
                pass  # 預測失敗時不影響顯示
            
            lines.append("")
        
        # 整體偏向 (隨機模式也跳過)
        if show_strategy_analysis:
            bias = self.market_data.get('overall_bias', 'NEUTRAL')
            confidence = self.market_data.get('overall_confidence', 0)
            bias_c = G_ if bias == 'BULLISH' else R_ if bias == 'BEARISH' else y
            lines.append(f"{B}📈 市場偏向{R}: {bias_c}{bias}{R}  信心: {confidence:.0%}")
        
        # 交易允許 (隨機模式也跳過)
        if show_strategy_analysis:
            trading_allowed = self.market_data.get('trading_allowed', False)
            allow_c = G_ if trading_allowed else R_
            lines.append(f"{B}🔐 交易允許{R}: {allow_c}{'✅ 是' if trading_allowed else '❌ 否'}{R}")
            
            # 🆕 v3.0 反轉策略模式
            regime_c = Y_ if self.market_regime == "REVERSAL" else G_
            regime_icon = "🔄" if self.market_regime == "REVERSAL" else "📊"
            lines.append(f"{B}{regime_icon} 市場模式{R}: {regime_c}{self.market_regime}{R}  |  連勝: {self.consecutive_wins}  連敗: {self.consecutive_losses}")
        
        # 🆕 v10.10: 三週期信號顯示 (5秒 / 30秒 / 5分鐘)
        dual_period = self.cached_strategy_data.get('dual_period', {})
        if show_strategy_analysis and dual_period:
            lines.append(f"\n{B}⚡ 三週期信號分析 (v10.11){R}")
            
            # 快線 (5秒) - 標記為「參考」
            fast_dir = dual_period.get('fast_direction', 'NEUTRAL')
            fast_adv = dual_period.get('fast_advantage', 0)
            fast_long = dual_period.get('fast_long_prob', 0)
            fast_short = dual_period.get('fast_short_prob', 0)
            fast_count = dual_period.get('fast_count', 0)
            
            fast_dir_c = G_ if fast_dir == 'LONG' else (R_ if fast_dir == 'SHORT' else y)
            # 雙向進度條: 多(綠)◄ ▌►空(紅)
            fast_total = fast_long + fast_short if (fast_long + fast_short) > 0 else 1
            fast_l_ratio = fast_long / fast_total
            fast_bar_len = 16
            fast_l_len = int(fast_l_ratio * fast_bar_len)
            fast_r_len = fast_bar_len - fast_l_len
            fast_bar = f"{G_}{'█' * fast_l_len}{R_}{'█' * fast_r_len}{R}"
            lines.append(f"   {y}⚡ 5秒快線{R}: {fast_dir_c}{fast_dir:^7}{R} [{fast_bar}] {G_}{fast_long:>3.0%}{R}|{R_}{fast_short:<3.0%}{R} {y}(會跳){R}")
            
            # 中線 (30秒) - 這是關鍵
            med_dir = dual_period.get('medium_direction', 'NEUTRAL')
            med_adv = dual_period.get('medium_advantage', 0)
            med_long = dual_period.get('medium_long_prob', 0)
            med_short = dual_period.get('medium_short_prob', 0)
            med_count = dual_period.get('medium_count', 0)
            
            med_dir_c = G_ if med_dir == 'LONG' else (R_ if med_dir == 'SHORT' else y)
            med_adv_c = G_ if med_adv >= 0.15 else (Y_ if med_adv >= 0.08 else R_)
            # 雙向進度條
            med_total = med_long + med_short if (med_long + med_short) > 0 else 1
            med_l_ratio = med_long / med_total
            med_bar_len = 16
            med_l_len = int(med_l_ratio * med_bar_len)
            med_r_len = med_bar_len - med_l_len
            med_bar = f"{G_}{'█' * med_l_len}{R_}{'█' * med_r_len}{R}"
            lines.append(f"   {B}🔄 30秒中線{R}: {med_dir_c}{med_dir:^7}{R} [{med_bar}] {G_}{med_long:>3.0%}{R}|{R_}{med_short:<3.0%}{R} 優勢:{med_adv_c}{med_adv:.0%}{R}")
            
            # 慢線 (5分鐘) - 主力 Game Plan
            slow_top1 = dual_period.get('top1', 'N/A')
            slow_top1_prob = dual_period.get('top1_prob', 0)
            slow_regime = dual_period.get('current_regime', 'N/A')
            slow_count = dual_period.get('slow_count', 0)
            is_actionable = dual_period.get('is_actionable', False)
            
            regime_c = G_ if is_actionable else (Y_ if slow_regime not in ['NO_DOMINANT', 'OBSERVING', None] else R_)
            action_tag = "✅ 可行動" if is_actionable else "⏳ 觀望中"
            lines.append(f"   {B}📊 5分慢線{R}: {regime_c}{slow_regime}{R} ({slow_top1}:{slow_top1_prob:.0%})  {action_tag}  ({slow_count}樣本)")
            
            # 🔧 v10.19: 從 _update_six_dim_analysis 計算的結果讀取，不重新計算
            six_dim = self.market_data.get('six_dim', {})
            long_score = six_dim.get('long_score', 0)
            short_score = six_dim.get('short_score', 0)
            obi_dir = six_dim.get('obi_dir', 'NEUTRAL')
            obi = six_dim.get('obi_value', 0)
            momentum_dir = six_dim.get('momentum_dir', 'NEUTRAL')
            price_change_5m = six_dim.get('momentum_value', 0)
            volume_dir = six_dim.get('volume_dir', 'NEUTRAL')
            volume_ratio = six_dim.get('volume_ratio', 1.0)
            
            # 🆕 v10.16: 使用六維對齊門檻
            alignment_threshold = self.config.six_dim_alignment_threshold if self.config.six_dim_enabled else 4
            max_score = 12 if self.config.six_dim_enabled else 6
            
            # 🆕 v10.16: 顯示六維信號
            if self.config.six_dim_enabled:
                lines.append(f"   {c}─────────────────────────────────────────{R}")
                lines.append(f"   {B}🎲 六維信號系統 (v10.16){R}")
                
                # OBI 線
                obi_c = G_ if obi_dir == 'LONG' else (R_ if obi_dir == 'SHORT' else y)
                obi_strong = "💪" if abs(obi) > self.config.obi_strong_threshold else ""
                lines.append(f"   {c}📈 OBI線{R}:    {obi_c}{obi_dir:^7}{R} ({obi:+.2f}) {obi_strong}")
                
                # 動能線 (price_change_5m 已經是百分比，不要再用 % 格式化)
                mom_c = G_ if momentum_dir == 'LONG' else (R_ if momentum_dir == 'SHORT' else y)
                mom_strong = "💪" if abs(price_change_5m) > self.config.momentum_strong_threshold else ""
                lines.append(f"   {c}🚀 動能線{R}:   {mom_c}{momentum_dir:^7}{R} ({price_change_5m:+.3f}%) {mom_strong}")
                
                # 成交量線
                vol_c = G_ if volume_dir == 'LONG' else (R_ if volume_dir == 'SHORT' else y)
                vol_strong = "💪" if volume_ratio > self.config.volume_strong_threshold or volume_ratio < (1/self.config.volume_strong_threshold) else ""
                lines.append(f"   {c}📊 成交量線{R}: {vol_c}{volume_dir:^7}{R} (買/賣={volume_ratio:.2f}) {vol_strong}")
            
            # 顯示多空競爭進度條
            lines.append(f"   {c}─────────────────────────────────────────{R}")
            lines.append(f"   {B}🏆 多空信號競爭 (先到{self.min_alignment_seconds:.0f}秒者勝){R}")
            
            # 🔧 v12.12.2: 整合過濾條件到進度條顯示
            # 讀取 signal_status 判斷是否真的會下單
            signal_status = self.market_data.get('signal_status', {})
            reject_reason = signal_status.get('reject_reason', '')
            use_three_line = signal_status.get('use_three_line', False)
            three_line_dir = signal_status.get('three_line_direction', '')
            
            # 判斷時間是否達標
            long_time_ok = self.long_alignment_seconds >= self.min_alignment_seconds
            short_time_ok = self.short_alignment_seconds >= self.min_alignment_seconds
            
            # 🔧 真正的「會下單」條件：時間達標 + 沒有被過濾 + use_three_line=True
            long_will_trade = long_time_ok and use_three_line and three_line_dir == "LONG" and not reject_reason
            short_will_trade = short_time_ok and use_three_line and three_line_dir == "SHORT" and not reject_reason
            
            # 🔧 如果時間達標但被過濾，顯示被過濾狀態而非 100%
            long_blocked = long_time_ok and not long_will_trade and (not short_time_ok or three_line_dir == "LONG")
            short_blocked = short_time_ok and not short_will_trade and (not long_time_ok or three_line_dir == "SHORT")
            
            # 多方進度條
            long_prog = min(self.long_alignment_seconds / self.min_alignment_seconds, 1.0)
            long_filled = int(long_prog * 15)
            long_bar = f"{'█' * long_filled}{'░' * (15 - long_filled)}"
            
            if long_will_trade:
                long_status = "✅ 進場!"
                long_c = G_
            elif long_blocked:
                long_status = "🚫 被過濾"
                long_c = Y_
            elif long_time_ok:
                long_status = "⏳ 等待..."
                long_c = Y_
            else:
                long_status = f"{self.long_alignment_seconds:.1f}s"
                long_c = G_ if long_score >= alignment_threshold else y
            
            lines.append(f"   {long_c}🟢 多 [{long_bar}] {long_prog:>6.0%} {long_status} (分數:{long_score}/{max_score}){R}")
            
            # 空方進度條
            short_prog = min(self.short_alignment_seconds / self.min_alignment_seconds, 1.0)
            short_filled = int(short_prog * 15)
            short_bar = f"{'█' * short_filled}{'░' * (15 - short_filled)}"
            
            if short_will_trade:
                short_status = "✅ 進場!"
                short_c = R_
            elif short_blocked:
                short_status = "🚫 被過濾"
                short_c = Y_
            elif short_time_ok:
                short_status = "⏳ 等待..."
                short_c = Y_
            else:
                short_status = f"{self.short_alignment_seconds:.1f}s"
                short_c = R_ if short_score >= alignment_threshold else y
            
            lines.append(f"   {short_c}🔴 空 [{short_bar}] {short_prog:>6.0%} {short_status} (分數:{short_score}/{max_score}){R}")
            
            # 勝負判定 + 過濾原因顯示
            mtf_aligned = signal_status.get('mtf_aligned', False)
            mtf_filter = signal_status.get('mtf_filter', {})
            mtf_dir = mtf_filter.get('mtf_direction', 'N/A')
            rsi_15m = mtf_filter.get('rsi_15m', 0)
            rsi_1h = mtf_filter.get('rsi_1h', 0)
            
            energy_tag = f" {G_}💪能量加成!{R}" if mtf_aligned else ""
            
            if long_will_trade:
                lines.append(f"   {G_}🎯 多方勝出! 執行做多!{energy_tag}{R}")
            elif short_will_trade:
                lines.append(f"   {R_}🎯 空方勝出! 執行做空!{energy_tag}{R}")
            elif long_time_ok and short_time_ok:
                lines.append(f"   {Y_}⚠️ 雙方都達標，信號混亂 → 觀望{R}")
            elif reject_reason:
                # 顯示被過濾的原因
                lines.append(f"   {Y_}→ {reject_reason}{R}")
            elif long_score >= alignment_threshold:
                lines.append(f"   {Y_}⏳ 多方領先中...等待累積到{self.min_alignment_seconds:.0f}秒{R}")
            elif short_score >= alignment_threshold:
                lines.append(f"   {Y_}⏳ 空方領先中...等待累積到{self.min_alignment_seconds:.0f}秒{R}")
            else:
                lines.append(f"   {y}⏳ 信號分散中 (多:{long_score} vs 空:{short_score}) → 等待對齊{R}")
            
            # 🆕 v10.15: 顯示 15m 和 1h RSI
            if mtf_filter:
                mtf_c = G_ if mtf_dir == 'LONG' else (R_ if mtf_dir == 'SHORT' else y)
                rsi_15m_c = G_ if rsi_15m and rsi_15m > 50 else (R_ if rsi_15m and rsi_15m < 50 else y)
                rsi_1h_c = G_ if rsi_1h and rsi_1h > 50 else (R_ if rsi_1h and rsi_1h < 50 else y)
                rsi_15m_str = f"{rsi_15m:.0f}" if rsi_15m else 'N/A'
                rsi_1h_str = f"{rsi_1h:.0f}" if rsi_1h else 'N/A'
                lines.append(f"   {c}MTF: 15m RSI={rsi_15m_c}{rsi_15m_str}{R} | 1h RSI={rsi_1h_c}{rsi_1h_str}{R} → {mtf_c}{mtf_dir}{R}")
        
        # 🆕 信號穩定性狀態 (防止被主力洗的關鍵指標) - 隨機模式跳過
        signal_status = self.market_data.get('signal_status', {})
        if show_strategy_analysis and signal_status:
            # 🆕 v9.0: 情境式策略專用顯示
            if signal_status.get('mode') == 'CONTEXTUAL_v9.0':
                lines.append(f"\n{B}🎯 情境式策略 v9.0{R}")
                
                # 時段與反向狀態
                current_hour = signal_status.get('current_hour', 0)
                is_reverse_hour = signal_status.get('is_reverse_hour', False)
                will_reverse = signal_status.get('will_reverse', False)
                
                hour_c = R_ if is_reverse_hour else (G_ if current_hour in self.config.ctx_good_hours else y)
                hour_tag = " [反向時段]" if is_reverse_hour else (" [好時段]" if current_hour in self.config.ctx_good_hours else "")
                lines.append(f"   時段: {hour_c}{current_hour}:00{R}{hour_tag}")
                
                # 條件匹配
                condition = signal_status.get('condition_matched', '')
                obi = signal_status.get('obi', 0)
                best_prob = signal_status.get('best_prob', 0)
                best_strat = signal_status.get('best_strategy', '')
                
                obi_c = G_ if obi > 0.5 else (R_ if obi < -0.5 else y)
                lines.append(f"   OBI: {obi_c}{obi:.2f}{R}  |  主力: {best_strat} ({best_prob:.0%})")
                
                if condition:
                    cond_c = G_ if condition.startswith('✅') else (R_ if condition.startswith('❌') else Y_)
                    lines.append(f"   條件: {cond_c}{condition}{R}")
                
                # 方向與反向
                pending = signal_status.get('pending_direction')
                final = signal_status.get('final_direction')
                if pending and final:
                    if will_reverse:
                        lines.append(f"   {Y_}🔄 反向操作: {pending} → {B}{final}{R}")
                    else:
                        dir_c = G_ if final == 'LONG' else R_
                        lines.append(f"   方向: {dir_c}{B}{final}{R}")
                
                # 拒絕原因
                reject = signal_status.get('reject_reason')
                if reject:
                    reject_c = Y_ if "確認" in reject else R_
                    lines.append(f"   {reject_c}→ {reject}{R}")
            
            # 🆕 v8.0: MTF-First 模式專用顯示
            elif signal_status.get('mode') == 'MTF_FIRST_v8.0':
                lines.append(f"\n{B}🎯 MTF-First v8.0 信號狀態{R}")
                
                # MTF 時間框架信號
                mtf_15m = signal_status.get('mtf_15m_signal', 'N/A')
                mtf_1h = signal_status.get('mtf_1h_signal', 'N/A')
                mtf_4h = signal_status.get('mtf_4h_signal', 'N/A')
                alignment = signal_status.get('alignment_score', 0)
                
                mtf_15m_c = G_ if mtf_15m == '多' else (R_ if mtf_15m == '空' else y)
                mtf_1h_c = G_ if mtf_1h == '多' else (R_ if mtf_1h == '空' else y)
                mtf_4h_c = G_ if mtf_4h == '多' else (R_ if mtf_4h == '空' else y)
                align_c = G_ if alignment > 30 else (R_ if alignment < -30 else y)
                
                lines.append(f"   15分鐘: {mtf_15m_c}{mtf_15m}{R} | 1小時: {mtf_1h_c}{mtf_1h}{R} | 4小時: {mtf_4h_c}{mtf_4h}{R}")
                lines.append(f"   對齊分數: {align_c}{alignment:+.0f}{R}  (需 ≥±{self.config.mtf_alignment_threshold:.0f})")
                
                # 預測價格
                predicted_entry = signal_status.get('predicted_entry', 0)
                predicted_exit = signal_status.get('predicted_exit', 0)
                emergency_stop = signal_status.get('emergency_stop', 0)
                pending = signal_status.get('pending_direction')
                
                if predicted_entry > 0 and pending:
                    dir_c = G_ if pending == 'LONG' else R_
                    profit_pct = abs(predicted_exit - predicted_entry) / predicted_entry * 100 if predicted_entry > 0 else 0
                    lines.append(f"   方向: {dir_c}{B}{pending}{R}")
                    lines.append(f"   預估進場: {y}${predicted_entry:,.0f}{R}")
                    lines.append(f"   預估目標: {G_}${predicted_exit:,.0f}{R} (+{profit_pct:.2f}%)")
                    lines.append(f"   緊急止損: {R_}${emergency_stop:,.0f}{R} (-{self.config.mtf_emergency_stop_pct:.2f}%)")
                
                # 拒絕原因
                reject = signal_status.get('reject_reason')
                if reject:
                    reject_c = Y_ if "確認" in reject else R_
                    lines.append(f"   {reject_c}→ {reject}{R}")
            # 🔧 v12.12.2: 移除重複的「三線系統決策」區塊
            # 資訊已整合到上方的「多空信號競爭」進度條
            
            # 只保留拒絕原因的最後顯示（如果還沒顯示過的話）
            # 這個區塊已經在上方的競爭進度條中處理了
        
        # 🆕 v10.14: 三線系統進場建議 (簡化版) - 隨機模式跳過
        signal_status = self.market_data.get('signal_status', {})
        three_line_dir = signal_status.get('three_line_direction')
        use_three_line = signal_status.get('use_three_line', False)
        mtf_aligned = signal_status.get('mtf_aligned', False)
        
        if show_strategy_analysis and use_three_line and three_line_dir and not self.trader.active_trade:
            dir_c = G_ if three_line_dir == "LONG" else R_
            dir_txt = "做多" if three_line_dir == "LONG" else "做空"
            current_price = self._get_market_price()
            
            # 計算止盈止損 (基於當前價格)
            tp_pct = 0.006 if mtf_aligned else 0.004  # 能量加成時目標較大
            sl_pct = 0.003
            
            if three_line_dir == "LONG":
                tp_price = current_price * (1 + tp_pct)
                sl_price = current_price * (1 - sl_pct)
            else:
                tp_price = current_price * (1 - tp_pct)
                sl_price = current_price * (1 + sl_pct)
            
            energy_tag = "💪能量加成" if mtf_aligned else ""
            
            # 🆕 v10.14: 檢查交易冷卻狀態
            can_trade_now, trade_status = self.trader.can_trade()
            
            if can_trade_now:
                lines.append(f"\n{B}📊 可進場! {energy_tag}{R}")
                lines.append(f"   {dir_c}✅ 三線系統已勝出: {dir_txt}{R}")
                lines.append(f"   {B}當前價格: ${current_price:,.2f} ← 系統將自動進場{R}")
                lines.append(f"   止盈目標: {G_}${tp_price:,.2f}{R} (+{tp_pct*100:.1f}%)")
                lines.append(f"   止損價格: {R_}${sl_price:,.2f}{R} (-{sl_pct*100:.1f}%)")
                lines.append(f"   風險報酬比: 1:{tp_pct/sl_pct:.1f}")
            else:
                lines.append(f"\n{Y_}📊 信號就緒但暫緩 {energy_tag}{R}")
                lines.append(f"   {dir_c}✅ 三線系統已勝出: {dir_txt}{R}")
                lines.append(f"   {Y_}⏳ {trade_status}{R}")
        
        # 進場信號 - 隨機模式跳過
        if show_strategy_analysis and self.market_data.get('entry_signal'):
            sig = self.market_data['entry_signal']
            dir_c = G_ if '多' in sig.direction.value else R_
            lines.append(f"\n{B}💡 進場信號{R}")
            lines.append(f"   {dir_c}{'='*20} {sig.direction.value} {'='*20}{R}")
            lines.append(f"   進場價: ${sig.entry_price:,.2f}  TP: ${sig.take_profit:,.2f}  SL: ${sig.stop_loss:,.2f}")
            lines.append(f"   倉位: {sig.position_size_pct:.0f}%  緊急度: {sig.urgency}")
        
        # 風險警告 - 隨機模式跳過
        warnings = self.market_data.get('risk_warnings', [])
        if show_strategy_analysis and warnings:
            lines.append(f"\n{R_}⚠️ 風險警告{R}")
            for w in warnings[:3]:
                lines.append(f"   {R_}{w}{R}")
        
        # ==================== 第四區塊：持倉與統計 ====================
        lines.append(f"\n{c}{'-'*80}{R}")
        lines.append(f"{B}💰 持倉狀態{R}")
        
        # 🆕 v10.13: 三線反轉警告 (持倉時顯示)
        suppress_three_line = card_id.startswith(("random_entry_smart_exit_v4", "random_entry_smart_exit_v5"))
        if self.trader.active_trade and not suppress_three_line:
            trade_dir = self.trader.active_trade.direction
            reverse_secs = 0
            reverse_threshold = 3.0  # 與 check_exit_conditions 一致
            
            if trade_dir == "LONG":
                reverse_secs = self.short_alignment_seconds
                if reverse_secs > 0:
                    prog = min(reverse_secs / reverse_threshold, 1.0)
                    bar_len = int(prog * 10)
                    bar = f"{'█' * bar_len}{'░' * (10 - bar_len)}"
                    if reverse_secs >= reverse_threshold:
                        lines.append(f"   {R_}🔄 三線反轉! 空方累積 {reverse_secs:.1f}s ≥ {reverse_threshold}s → 準備平倉!{R}")
                    else:
                        lines.append(f"   {Y_}⚠️ 空方正在累積: [{bar}] {reverse_secs:.1f}s / {reverse_threshold}s{R}")
            else:
                reverse_secs = self.long_alignment_seconds
                if reverse_secs > 0:
                    prog = min(reverse_secs / reverse_threshold, 1.0)
                    bar_len = int(prog * 10)
                    bar = f"{'█' * bar_len}{'░' * (10 - bar_len)}"
                    if reverse_secs >= reverse_threshold:
                        lines.append(f"   {R_}🔄 三線反轉! 多方累積 {reverse_secs:.1f}s ≥ {reverse_threshold}s → 準備平倉!{R}")
                    else:
                        lines.append(f"   {Y_}⚠️ 多方正在累積: [{bar}] {reverse_secs:.1f}s / {reverse_threshold}s{R}")
        
        # 🔴 重要：永遠以 Testnet 真實持倉為準
        external_pos = self.trader.get_external_position()
        system_trade = self.trader.active_trade
        
        if external_pos:
            # Testnet 有真實持倉
            entry = external_pos['entry_price']
            mark = external_pos['mark_price']
            side = external_pos['side']
            lev = external_pos['leverage']
            size = external_pos['size']
            unrealized = external_pos['unrealized_pnl']
            
            if side == 'LONG':
                float_pnl = (mark - entry) / entry * 100 * lev
            else:
                float_pnl = (entry - mark) / entry * 100 * lev
            
            pnl_c = G_ if unrealized > 0 else R_
            dir_icon = "🟢" if side == "LONG" else "🔴"
            
            # 檢查是否與系統持倉一致
            if system_trade:
                # 比較方向和進場價是否一致
                same_direction = system_trade.direction == side
                same_entry = abs(system_trade.entry_price - entry) < 1  # 誤差 $1 以內
                if same_direction and same_entry:
                    # 系統持倉與 Testnet 一致，顯示系統策略名稱
                    lines.append(f"   {dir_icon} {system_trade.strategy}  {side}")
                else:
                    # 不一致，標記為外部持倉/混合狀態
                    label = "[dYdX 持倉]" if getattr(self.trader, "dydx_sync_enabled", False) else "[Testnet 持倉]"
                    lines.append(f"   {dir_icon} {y}{label}{R}  {side}")
                    lines.append(f"   ⚠️ 系統追蹤: {system_trade.direction} @ ${system_trade.entry_price:,.2f}")
            else:
                label = "[dYdX 持倉]" if getattr(self.trader, "dydx_sync_enabled", False) else "[外部持倉]"
                lines.append(f"   {dir_icon} {y}{label}{R}  {side}")
            
            lines.append(f"   數量: {size:.6f} BTC  進場: ${entry:,.2f}")
            lines.append(f"   當前: ${mark:,.2f}  槓桿: {lev}X")
            lines.append(f"   未實現盈虧: {pnl_c}${unrealized:+.2f}{R} ({pnl_c}{float_pnl:+.2f}%{R})")
            if external_pos['liquidation_price'] > 0:
                lines.append(f"   爆倉價: ${external_pos['liquidation_price']:,.2f}")
            
            # 🆕 v4.0 智能止盈目標 (Testnet 真實持倉)
            smart_exit = self.market_data.get('smart_exit_info', {})
            if smart_exit:
                gross_target = smart_exit.get('gross_target_pct', 10.0)
                net_target = smart_exit.get('net_target_pct', 6.0)
                curr_gross = smart_exit.get('current_gross_pnl_pct', 0)
                curr_net = smart_exit.get('current_net_pnl_pct', 0)
                
                # 進度條
                gross_progress = min(100, max(0, curr_gross / gross_target * 100)) if gross_target > 0 else 0
                net_progress = min(100, max(0, curr_net / net_target * 100)) if net_target > 0 else 0
                
                gross_bar_len = int(gross_progress / 5)
                net_bar_len = int(net_progress / 5)
                
                gross_bar = '█' * gross_bar_len + '░' * (20 - gross_bar_len)
                net_bar = '█' * net_bar_len + '░' * (20 - net_bar_len)
                
                gross_c = G_ if curr_gross >= gross_target else Y_ if curr_gross > 0 else R_
                net_c2 = G_ if curr_net >= net_target else Y_ if curr_net > 0 else R_
                
                lines.append(f"   {B}🎯 智能止盈目標{R} (觀察: +10%毛利 / +6%淨利)")
                lines.append(f"      毛利: {gross_c}{curr_gross:+.1f}%{R} |{gross_bar}| 目標 {gross_target:.1f}%")
                lines.append(f"      淨利: {net_c2}{curr_net:+.1f}%{R} |{net_bar}| 目標 {net_target:.1f}%")
                
                if smart_exit.get('should_exit', False):
                    lines.append(f"      {G_}✅ 建議止盈: {smart_exit.get('exit_reason', '')}{R}")
            
            # 🆕 v10.9.1 兩階段止盈止損狀態 (動態調整版)
            if self.two_phase_exit and system_trade:
                curr_net = smart_exit.get('current_net_pnl_pct', 0) if smart_exit else 0
                max_net = system_trade.max_profit_pct if hasattr(system_trade, 'max_profit_pct') else curr_net
                
                # 更新市場品質評分
                self.two_phase_exit.update_market_condition(self.market_data)
                quality = self.two_phase_exit.market_condition.get('quality', 'NORMAL')
                quality_score = self.two_phase_exit.market_condition.get('score', 50)
                
                # 市場品質顯示
                quality_icons = {'GOOD': ('🟢', G_), 'NORMAL': ('🟡', Y_), 'BAD': ('🔴', R_)}
                q_icon, q_color = quality_icons.get(quality, ('🟡', Y_))
                lines.append(f"   {B}📊 市場品質{R} {q_icon} {q_color}{quality}{R} (評分: {quality_score}/100)")
                
                # 取得動態參數
                phase_info = self.two_phase_exit.get_current_phase(curr_net, max_net, self.market_data)
                
                phase_emoji = phase_info.get('emoji', '🎯')
                phase_name = phase_info.get('name', '未知')
                phase_sl = phase_info.get('stop_loss_pct', 4.0)
                phase_tp = phase_info.get('target_pct', 6.0)
                
                # 顯示動態調整提示
                if quality == 'GOOD':
                    adj_hint = f"{G_}⬆ 放寬{R}"
                elif quality == 'BAD':
                    adj_hint = f"{R_}⬇ 收緊{R}"
                else:
                    adj_hint = f"{Y_}◼ 標準{R}"
                
                if phase_info.get('phase') == 1:
                    progress = phase_info.get('progress_pct', 0)
                    lines.append(f"   {B}{phase_emoji} 第一階段: {phase_name}{R} [{adj_hint}]")
                    lines.append(f"      費用突破進度: {Y_}{progress:.0f}%{R} → 4%門檻")
                    lines.append(f"      動態止損: {R_}-{phase_sl:.1f}%{R} | 動態目標: {G_}+{phase_tp:.1f}%{R}")
                elif phase_info.get('phase') == 2:
                    trailing_from = phase_info.get('trailing_from', max_net)
                    trailing_offset = phase_info.get('trailing_offset', 2.0)
                    lines.append(f"   {B}{phase_emoji} 第二階段: {phase_name}{R} [{adj_hint}]")
                    lines.append(f"      追蹤止盈: 最高 {G_}{trailing_from:.1f}%{R} → 鎖定 {Y_}{phase_sl:.1f}%{R}")
                    lines.append(f"      動態回撤: {trailing_offset:.1f}% | 最大目標: +{phase_tp:.1f}%")
            
            # 🆕 v4.0 持倉管理建議 (Testnet 真實持倉)
            if self.mtf_analyzer and self.mtf_enabled and system_trade:
                try:
                    pos_signal = self.mtf_analyzer.get_position_management_signal(
                        position_direction=system_trade.direction,
                        entry_price=system_trade.entry_price,
                        current_price=mark
                    )
                    if pos_signal:
                        action = pos_signal.get('action', 'HOLD')
                        action_icons = {
                            'ADD': ('➕', G_),
                            'REDUCE': ('➖', Y_),
                            'CLOSE': ('❌', R_),
                            'HOLD': ('⏸️', y)
                        }
                        icon, act_c = action_icons.get(action, ('⏸️', y))
                        reason = pos_signal.get('reason', '')
                        conf = pos_signal.get('confidence', 50)
                        lines.append(f"   {B}📈 持倉管理{R} {icon} {act_c}{action}{R} ({conf}%信心)")
                        if reason:
                            lines.append(f"      {y}→ {reason}{R}")
                except Exception as e:
                    pass
                    
        elif system_trade:
            # 只有系統追蹤的持倉（可能是 paper mode 或 API 已平倉）
            t = system_trade
            leverage = t.actual_leverage if hasattr(t, 'actual_leverage') and t.actual_leverage else t.leverage

            # 🔧 v14.6.15: 若啟用 dYdX 同步，Paper 區塊的浮動% 以 dYdX 價格/槓桿做對齊
            # 避免同一時間點因「不同價格源/不同槓桿」導致 Paper 與 dYdX 顯示差很多。
            # 🔧 v14.6.21: 改用 dYdX Oracle Price 緩存 (來自 sync_position_loop REST API)
            price_for_pnl = price
            try:
                dydx_sync_on = getattr(self.trader, 'dydx_sync_enabled', False) == True
                dydx_api_ok = getattr(self.trader, 'dydx_api', None) is not None
                if dydx_sync_on and dydx_api_ok:
                    # 對齊槓桿（以 dYdX config 為準，並 cap 到 50）
                    d_leverage = 50
                    api_cfg = getattr(getattr(self.trader, 'dydx_api', None), 'config', None)
                    lev = getattr(api_cfg, 'leverage', None) if api_cfg else None
                    if isinstance(lev, (int, float)) and lev > 0:
                        d_leverage = int(lev)
                    else:
                        lev2 = getattr(getattr(self.trader, 'config', None), 'leverage', None)
                        if isinstance(lev2, (int, float)) and lev2 > 0:
                            d_leverage = int(lev2)
                        elif isinstance(lev2, (list, tuple)) and len(lev2) > 0:
                            d_leverage = int(lev2[0])
                    leverage = min(int(d_leverage), 50)

                    # 🔧 v14.6.22: 對齊價格 - 使用 Trader 緩存的 Oracle Price
                    # sync loop 每 0.5 秒更新一次 dydx_oracle_price_cache (REST API 0.5秒緩存)
                    oracle_price = getattr(self.trader, 'dydx_oracle_price_cache', 0)
                    if oracle_price and float(oracle_price) > 0:
                        price_for_pnl = float(oracle_price)
                    else:
                        # Fallback: 使用 API 的 _price_cache
                        api_cache = getattr(self.trader.dydx_api, '_price_cache', 0)
                        if api_cache and float(api_cache) > 0:
                            price_for_pnl = float(api_cache)
            except Exception:
                pass

            mid_price = price_for_pnl if price_for_pnl > 0 else price_ctx.get('mid', 0.0)
            bid_price = price_ctx.get('bid', 0.0) or mid_price
            ask_price = price_ctx.get('ask', 0.0) or mid_price
            if t.direction == "LONG":
                float_pnl = (mid_price - t.entry_price) / t.entry_price * 100 * leverage
                net_price = bid_price
            else:
                float_pnl = (t.entry_price - mid_price) / t.entry_price * 100 * leverage
                net_price = ask_price
            
            pnl_c = G_ if float_pnl > 0 else R_
            dir_icon = "🟢" if t.direction == "LONG" else "🔴"
            
            # 計算手續費影響
            fee_pct = self.config.maker_fee_pct if self.config.use_maker_simulation else self.config.taker_fee_pct
            fee_mult = _fee_leverage_multiplier(self.config, leverage)
            total_fee_impact = fee_pct * 2 * fee_mult  # 雙向手續費 (ROE%)
            if t.direction == "LONG":
                net_pnl = (net_price - t.entry_price) / t.entry_price * 100 * leverage - total_fee_impact
            else:
                net_pnl = (t.entry_price - net_price) / t.entry_price * 100 * leverage - total_fee_impact
            net_c = G_ if net_pnl > 0 else R_
            
            if self.trader.paper_mode:
                entry_type = t.entry_type if hasattr(t, 'entry_type') and t.entry_type else "TAKER"
                lines.append(f"   {dir_icon} [Paper Trading] {t.strategy}")
                lines.append(f"   進場: ${t.entry_price:,.2f} ({entry_type})")
            else:
                lines.append(f"   {dir_icon} {R_}[⚠️ 系統追蹤 - API 無持倉]{R}")
                lines.append(f"   {t.strategy}  進場: ${t.entry_price:,.2f}")
            
            # 顯示動態參數
            target_pct = t.actual_target_pct if hasattr(t, 'actual_target_pct') and t.actual_target_pct else self.config.target_profit_pct
            sl_pct = t.actual_stop_loss_pct if hasattr(t, 'actual_stop_loss_pct') and t.actual_stop_loss_pct else self.config.stop_loss_pct
            max_hold = t.actual_max_hold_min if hasattr(t, 'actual_max_hold_min') and t.actual_max_hold_min else self.config.max_hold_minutes
            volatility = t.market_volatility if hasattr(t, 'market_volatility') and t.market_volatility else 0
            
            # 🆕 損益平衡價格判斷
            breakeven = t.breakeven_price if hasattr(t, 'breakeven_price') and t.breakeven_price > 0 else t.entry_price
            if t.direction == "LONG":
                is_profitable = net_price > breakeven
                distance_to_be = (breakeven - net_price) / net_price * 100 if net_price < breakeven else 0
            else:
                is_profitable = net_price < breakeven
                distance_to_be = (net_price - breakeven) / net_price * 100 if net_price > breakeven else 0
            
            # 🔧 v14.6.40: 修正淨盈虧計算
            # 正確公式: 淨盈虧% = 毛利% - 手續費影響%
            # 手續費影響% = (開倉費率 + 平倉費率) × 槓桿
            real_net_pnl = net_pnl
            net_c = G_ if real_net_pnl > 0 else R_
            be_c = G_ if is_profitable else Y_
            
            lines.append(f"   槓桿: {leverage}X (動態)")
            lines.append(f"   💰 損益平衡: {be_c}${breakeven:,.2f}{R} {'✅ 已獲利' if is_profitable else f'⏳ 差 {distance_to_be:.4f}%'}")
            lines.append(f"   浮動: {pnl_c}{float_pnl:+.2f}%{R}  💵 淨盈虧: {net_c}{real_net_pnl:+.2f}%{R}")
            lines.append(f"   TP: ${t.take_profit_price:,.2f} (+{target_pct:.3f}%)  SL: ${t.stop_loss_price:,.2f} (-{sl_pct:.3f}%)")
            spread_pct = (ask_price - bid_price) / mid_price * 100 if mid_price > 0 and ask_price > 0 and bid_price > 0 else 0.0
            spread_bps = spread_pct * 100
            lines.append(f"   Bid/Ask: ${bid_price:,.2f} / ${ask_price:,.2f}  Spread: {spread_pct:.4f}% ({spread_bps:.1f}bps)")
            
            # 🆕 v4.0 智能止盈目標
            smart_exit = self.market_data.get('smart_exit_info', {})
            if smart_exit:
                gross_target = smart_exit.get('gross_target_pct', 10.0)
                net_target = smart_exit.get('net_target_pct', 6.0)
                curr_gross = smart_exit.get('current_gross_pnl_pct', 0)
                curr_net = smart_exit.get('current_net_pnl_pct', 0)
                
                # 進度條
                gross_progress = min(100, max(0, curr_gross / gross_target * 100)) if gross_target > 0 else 0
                net_progress = min(100, max(0, curr_net / net_target * 100)) if net_target > 0 else 0
                
                gross_bar_len = int(gross_progress / 5)
                net_bar_len = int(net_progress / 5)
                
                gross_bar = '█' * gross_bar_len + '░' * (20 - gross_bar_len)
                net_bar = '█' * net_bar_len + '░' * (20 - net_bar_len)
                
                gross_c = G_ if curr_gross >= gross_target else Y_ if curr_gross > 0 else R_
                net_c2 = G_ if curr_net >= net_target else Y_ if curr_net > 0 else R_
                
                lines.append(f"   {B}🎯 智能止盈目標{R} (觀察: +10%毛利 / +6%淨利)")
                lines.append(f"      毛利: {gross_c}{curr_gross:+.1f}%{R} |{gross_bar}| 目標 {gross_target:.1f}%")
                lines.append(f"      淨利: {net_c2}{curr_net:+.1f}%{R} |{net_bar}| 目標 {net_target:.1f}%")
                
                if smart_exit.get('should_exit', False):
                    lines.append(f"      {G_}✅ 建議止盈: {smart_exit.get('exit_reason', '')}{R}")
            
            # 🆕 v10.9 兩階段止盈止損狀態 (Paper Trading)
            if self.two_phase_exit and t:
                smart_exit_paper = self.market_data.get('smart_exit_info', {})
                curr_net_p = smart_exit_paper.get('current_net_pnl_pct', 0) if smart_exit_paper else real_net_pnl
                max_net_p = t.max_profit_pct if hasattr(t, 'max_profit_pct') else curr_net_p
                phase_info_p = self.two_phase_exit.get_current_phase(curr_net_p, max_net_p)
                
                phase_emoji = phase_info_p.get('emoji', '🎯')
                phase_name = phase_info_p.get('name', '未知')
                phase_sl = phase_info_p.get('stop_loss_pct', 4.0)
                phase_tp = phase_info_p.get('target_pct', 6.0)
                
                if phase_info_p.get('phase') == 1:
                    progress = phase_info_p.get('progress_pct', 0)
                    lines.append(f"   {B}{phase_emoji} 第一階段: {phase_name}{R}")
                    lines.append(f"      費用突破進度: {Y_}{progress:.0f}%{R} → 4%門檻")
                    lines.append(f"      嚴格止損: {R_}-{phase_sl:.1f}%{R} | 目標: {G_}+{phase_tp:.1f}%{R}")
                elif phase_info_p.get('phase') == 2:
                    trailing_from = phase_info_p.get('trailing_from', max_net_p)
                    lines.append(f"   {B}{phase_emoji} 第二階段: {phase_name}{R}")
                    lines.append(f"      追蹤止盈: 最高 {G_}{trailing_from:.1f}%{R} → 鎖定 {Y_}{phase_sl:.1f}%{R}")
                    lines.append(f"      回撤容許: {self.config.phase2_trailing_offset_pct:.1f}% | 最大目標: +{phase_tp:.1f}%")
            
            # 🆕 v12.8 N%鎖N% 鎖利顯示
            if t:
                current_pnl = self.trader.calculate_current_pnl_pct(net_price)
                max_pnl = t.max_profit_pct if hasattr(t, 'max_profit_pct') else current_pnl
                if current_pnl > max_pnl:
                    max_pnl = current_pnl
                lock_pct, stage_name = self.trader.get_progressive_stop_loss(max_pnl)
                
                # 計算止損價格
                if t.direction == "LONG":
                    sl_price = t.entry_price * (1 + lock_pct / leverage / 100)
                else:
                    sl_price = t.entry_price * (1 - lock_pct / leverage / 100)
                
                lock_c = G_ if lock_pct >= 1.0 else (G_ if lock_pct >= 0 else (Y_ if lock_pct >= -0.5 else R_))
                
                if self.config.use_n_lock_n:
                    lines.append(f"   {B}🔐 N%鎖N% 鎖利{R} (v12.8)")
                    lines.append(f"      當前: {pnl_c}{current_pnl:+.2f}%{R} | 最高: {G_}{max_pnl:+.2f}%{R}")
                    lines.append(f"      狀態: {lock_c}{stage_name}{R}")
                    lines.append(f"      止損線: {lock_c}{lock_pct:+.1f}%{R} @ ${sl_price:,.2f}")
                    
                    # 顯示下一階段目標
                    if max_pnl < 1.0:
                        lines.append(f"      下階段: 達 {G_}+1.0%{R} → 🔐 鎖住 +1%")
                    else:
                        next_level = int(max_pnl) + 1
                        lines.append(f"      下階段: 達 {G_}+{next_level}.0%{R} → 🔐 鎖住 +{next_level}%")
                else:
                    lines.append(f"   {B}📊 階段性鎖利{R} (v12.2)")
                    lines.append(f"      當前: {current_pnl:+.2f}% | 階段: {lock_c}{stage_name}{R}")
                    lines.append(f"      止損線: {lock_c}{lock_pct:+.1f}%{R} @ ${sl_price:,.2f}")
            
            # 🆕 v4.0 持倉管理建議
            if self.mtf_analyzer and self.mtf_enabled:
                try:
                    pos_signal = self.mtf_analyzer.get_position_management_signal(
                        position_direction=t.direction,
                        entry_price=t.entry_price,
                        current_price=price
                    )
                    if pos_signal:
                        action = pos_signal.get('action', 'HOLD')
                        action_icons = {
                            'ADD': ('➕', G_),
                            'REDUCE': ('➖', Y_),
                            'CLOSE': ('❌', R_),
                            'HOLD': ('⏸️', y)
                        }
                        icon, act_c = action_icons.get(action, ('⏸️', y))
                        reason = pos_signal.get('reason', '')
                        conf = pos_signal.get('confidence', 50)
                        size_pct = pos_signal.get('suggested_size_pct', 0)
                        
                        lines.append(f"   {B}📋 持倉建議{R}: {act_c}{icon} {action}{R} ({conf}% 信心)")
                        if reason:
                            lines.append(f"      理由: {reason}")
                        if size_pct > 0 and action != 'HOLD':
                            lines.append(f"      建議操作: {size_pct}% 倉位")
                except Exception:
                    pass  # 信號取得失敗不影響顯示
            
            # 持倉時間
            entry_time = datetime.fromisoformat(t.entry_time)
            hold_min = (datetime.now() - entry_time).total_seconds() / 60
            time_c = Y_ if hold_min > max_hold * 0.8 else R
            lines.append(f"   持倉: {time_c}{hold_min:.1f}/{max_hold:.0f}分鐘{R}")
        else:
            lines.append(f"   {y}無持倉{R}")
            mid_price = price_ctx.get('mid', 0.0)
            bid_price = price_ctx.get('bid', 0.0) or mid_price
            ask_price = price_ctx.get('ask', 0.0) or mid_price
            spread_pct = (ask_price - bid_price) / mid_price * 100 if mid_price > 0 and ask_price > 0 and bid_price > 0 else 0.0
            spread_bps = spread_pct * 100
            lines.append(f"   Mid: ${mid_price:,.2f}")
            lines.append(f"   Bid/Ask: ${bid_price:,.2f} / ${ask_price:,.2f}  Spread: {spread_pct:.4f}% ({spread_bps:.1f}bps)")
        
        # ════════════════════════════════════════════════════════════════════
        # 🆕 dYdX 真實交易同步狀態 (--sync 模式)
        # 🔧 v14.6.6: 顯示與 Paper Trading 一致的詳細持倉信息
        # 🔧 v14.6.10: 明確檢查 True (避免 None 問題)
        # ════════════════════════════════════════════════════════════════════
        dydx_sync_on = getattr(self.trader, 'dydx_sync_enabled', False) == True
        dydx_api_ok = getattr(self.trader, 'dydx_api', None) is not None
        if dydx_sync_on and dydx_api_ok:
            lines.append(f"\n{c}{'-'*80}{R}")
            lines.append(f"{B}🔗 dYdX 真實同步狀態{R}")
            
            # 🔧 v14.3: 使用緩存版本避免 429
            try:
                import asyncio
                positions = asyncio.run(self.trader._get_dydx_positions_with_cache())
                btc_pos = None
                for pos in positions:
                    # 🔧 v14.6.8: 使用 abs() 檢查非零 (SHORT 有負數 size)
                    size_val = float(pos.get("size", 0))
                    if pos.get("market") == "BTC-USD" and abs(size_val) > 0.00001:
                        btc_pos = pos
                        break
                
                # 🔧 v14.6.8: 如果 API 沒有持倉但內部追蹤有，使用追蹤數據
                internal_pos = getattr(self.trader, 'dydx_real_position', None)
                if not btc_pos and internal_pos and internal_pos.get('size', 0) > 0:
                    # API 延遲，顯示內部追蹤的數據 (可能剛開倉)
                    lines.append(f"   {Y_}⏳ [API 同步中] 使用內部追蹤數據{R}")
                    btc_pos = {
                        "market": "BTC-USD",
                        "size": internal_pos['size'] if internal_pos['side'] == 'LONG' else -internal_pos['size'],
                        "entryPrice": internal_pos['entry_price']
                    }
                
                if btc_pos:
                    d_size = float(btc_pos.get("size", 0))
                    d_entry = float(btc_pos.get("entryPrice", 0))
                    # 🔧 v14.6.14: 優先使用 API 的 side 欄位
                    d_side = None
                    raw_side = btc_pos.get("side") or btc_pos.get("positionSide")
                    if raw_side:
                        s = str(raw_side).upper()
                        if s in ("LONG", "BUY"):
                            d_side = "LONG"
                        elif s in ("SHORT", "SELL"):
                            d_side = "SHORT"
                    if d_side is None:
                        d_side = "LONG" if d_size > 0 else "SHORT"
                    d_size = abs(d_size)
                    
                    # 🔧 v14.6.22: 使用 dYdX Oracle Price (而非 Binance 或慢速的 WS)
                    # 優先順序: dydx_oracle_price_cache > dYdX WebSocket > Binance
                    dydx_current_price = price  # 預設用 Binance
                    oracle_cache = getattr(self.trader, 'dydx_oracle_price_cache', 0)
                    if oracle_cache and oracle_cache > 0:
                        dydx_current_price = oracle_cache
                    elif hasattr(self.trader, 'dydx_ws') and self.trader.dydx_ws:
                        ws_price = getattr(self.trader.dydx_ws, 'current_price', 0)
                        if ws_price > 0:
                            dydx_current_price = ws_price
                    
                    # 計算真實盈虧 (使用 dYdX 價格)
                    # 🔧 v14.6.25: 修正槓桿獲取邏輯，優先使用 50X (dYdX BTC 預設)
                    d_leverage = 50
                    try:
                        # 嘗試從 API 配置獲取，如果沒有則維持 50
                        api_cfg = getattr(getattr(self.trader, 'dydx_api', None), 'config', None)
                        lev = getattr(api_cfg, 'leverage', None) if api_cfg else None
                        if isinstance(lev, (int, float)) and lev > 0:
                            d_leverage = int(lev)
                        # 只有在非 dYdX 模式下才回退到 config.leverage
                    except Exception:
                        pass
                    
                    if d_leverage <= 0:
                        d_leverage = 50
                    d_leverage = min(int(d_leverage), 50)
                    
                    if d_side == "LONG":
                        d_pnl_pct_raw = (dydx_current_price - d_entry) / d_entry * 100
                    else:
                        d_pnl_pct_raw = (d_entry - dydx_current_price) / d_entry * 100
                    
                    # 🔧 v14.6.25: 修正 ROE% 計算，確保與交易所一致
                    d_pnl_pct = d_pnl_pct_raw * d_leverage
                    
                    d_pnl_usd = d_size * d_entry * (d_pnl_pct_raw / 100)  # USD 用原始%
                    d_icon = "🟢" if d_side == "LONG" else "🔴"
                    d_pnl_c = G_ if d_pnl_pct > 0 else R_
                    
                    # ════════════════════════════════════════════════════════
                    # 🆕 v14.6.6: 顯示與 Paper Trading 一致的詳細信息
                    # ════════════════════════════════════════════════════════
                    lines.append(f"   {d_icon} [dYdX 真實] {d_side}")
                    lines.append(f"   進場: ${d_entry:,.2f} | 數量: {d_size:.4f} BTC")
                    lines.append(f"   槓桿: {d_leverage}X (ROE% 基於此槓桿)")
                    
                    # 損益平衡狀態
                    if abs(d_pnl_pct) < 0.01:
                        be_status = "⏳ 差 0.0000%"
                    elif d_pnl_pct > 0:
                        be_status = "✅ 已獲利"
                    else:
                        be_status = f"❌ 虧損中"
                    lines.append(f"   💰 損益平衡: ${d_entry:,.2f} {be_status}")
                    lines.append(f"   浮動: {d_pnl_c}{d_pnl_pct:+.2f}%{R}  💵 淨盈虧: {d_pnl_c}{d_pnl_pct:+.2f}%{R} (${d_pnl_usd:+.2f})")
                    
                    # TP/SL 價格 (從 Paper Trading 讀取)
                    # 🔧 v14.6.6 修復: 使用 active_trade 而非 current_trade
                    # 🔧 v14.6.7 修復: 使用正確屬性名 take_profit_price / stop_loss_price
                    paper_trade = getattr(self.trader, 'active_trade', None)
                    open_tp_info = None
                    open_sl_info = None
                    
                    # 🔧 v14.6.11: 即使沒有 paper_trade 也顯示 dYdX 的 TP/SL
                    if paper_trade:
                        tp_price = getattr(paper_trade, 'take_profit_price', 0)
                        sl_price = getattr(paper_trade, 'stop_loss_price', 0)
                        if tp_price > 0 and sl_price > 0:
                            # 🔧 顯示「實際價格移動%」(相對進場價)，避免 TP/SL 價格與百分比不一致
                            try:
                                if d_side == "LONG":
                                    tp_pct = (tp_price - d_entry) / d_entry * 100
                                    sl_pct = (d_entry - sl_price) / d_entry * 100
                                else:
                                    tp_pct = (d_entry - tp_price) / d_entry * 100
                                    sl_pct = (sl_price - d_entry) / d_entry * 100
                                tp_pct = abs(tp_pct)
                                sl_pct = abs(sl_pct)
                            except Exception:
                                tp_pct = 0.0
                                sl_pct = 0.0
                            lines.append(f"   TP: ${tp_price:,.2f} (+{tp_pct:.3f}%)  SL: ${sl_price:,.2f} (-{sl_pct:.3f}%)")
                    else:
                        # 🔧 v14.6.11: Paper 沒倉時，從 dYdX 預掛單取得 TP/SL
                        pending_tp = getattr(self.trader, 'pending_tp_order', None)
                        pending_sl = getattr(self.trader, 'pending_sl_order', None)
                        tp_p = pending_tp.get('tp_price', 0) if pending_tp else 0
                        sl_p = pending_sl.get('sl_price', 0) if pending_sl else 0

                        # 若本地追蹤缺失，從 indexer 取得實際 open orders 顯示
                        if (tp_p <= 0 or sl_p <= 0) and getattr(self.trader, 'dydx_api', None):
                            try:
                                import asyncio
                                open_orders = asyncio.run(
                                    self.trader.dydx_api.get_open_orders(
                                        status=["OPEN", "UNTRIGGERED"],
                                        symbol="BTC-USD",
                                    )
                                )
                            except Exception:
                                open_orders = []

                            if open_orders:
                                expected_exit_side = "SELL" if d_side == "LONG" else "BUY"
                                tp_best_dist = None
                                sl_best_dist = None

                                for order in open_orders:
                                    side_raw = str(order.get("side", "") or "").upper()
                                    if side_raw in ("LONG", "BUY"):
                                        side_norm = "BUY"
                                    elif side_raw in ("SHORT", "SELL"):
                                        side_norm = "SELL"
                                    else:
                                        side_norm = side_raw

                                    if expected_exit_side and side_norm and side_norm != expected_exit_side:
                                        continue

                                    otype = str(order.get("type", "") or "").upper()
                                    trigger_price = _coerce_float(order.get("triggerPrice", 0.0), default=0.0)
                                    price = _coerce_float(order.get("price", 0.0), default=0.0)
                                    is_conditional = False
                                    if trigger_price > 0:
                                        is_conditional = True
                                    elif "STOP" in otype or "TAKE_PROFIT" in otype:
                                        is_conditional = True

                                    if is_conditional and sl_p <= 0:
                                        sl_price = trigger_price if trigger_price > 0 else price
                                        if sl_price <= 0:
                                            continue
                                        if (d_side == "LONG" and sl_price > d_entry) or (d_side == "SHORT" and sl_price < d_entry):
                                            continue
                                        dist = abs(sl_price - d_entry)
                                        if sl_best_dist is None or dist < sl_best_dist:
                                            sl_best_dist = dist
                                            sl_p = sl_price
                                            open_sl_info = {
                                                "price": sl_price,
                                                "client_id": order.get("clientId") or order.get("id"),
                                                "source": "indexer",
                                            }
                                        continue

                                    if (not is_conditional) and tp_p <= 0:
                                        tp_price = price
                                        if tp_price <= 0:
                                            continue
                                        if (d_side == "LONG" and tp_price < d_entry) or (d_side == "SHORT" and tp_price > d_entry):
                                            continue
                                        dist = abs(tp_price - d_entry)
                                        if tp_best_dist is None or dist < tp_best_dist:
                                            tp_best_dist = dist
                                            tp_p = tp_price
                                            open_tp_info = {
                                                "price": tp_price,
                                                "client_id": order.get("clientId") or order.get("id"),
                                                "source": "indexer",
                                            }

                        if tp_p > 0 or sl_p > 0:
                            tp_text = f"${tp_p:,.2f}" if tp_p > 0 else "--"
                            sl_text = f"${sl_p:,.2f}" if sl_p > 0 else "--"
                            lines.append(f"   {Y_}[dYdX 預掛單]{R} TP: {tp_text}  SL: {sl_text}")
                        
                        # 🔧 v14.9.6: 不要在 Dashboard 渲染時執行同步，只顯示提示
                        # 同步應該在主交易循環中處理
                        lines.append(f"   {Y_}⚠️ dYdX 有倉但 Paper 無倉 - 請等待自動同步...{R}")
                        # 標記需要同步，主循環會處理
                        if not getattr(self.trader, '_pending_dydx_sync', False):
                            self.trader._pending_dydx_sync = True
                    
                    # 同步內部追蹤變數（確保後續止損補掛使用最新 entry/size）
                    self.trader.dydx_real_position = {
                        "side": d_side,
                        "size": d_size,
                        "entry_price": d_entry,
                    }

                    # 🔐 N%鎖N% 鎖利狀態 (從 Paper Trading 複製)
                    # 🔧 v14.6.11: 即使沒有 paper_trade 也顯示基本 N%鎖N% (使用 dYdX 數據)
                    if self.config.use_n_lock_n:
                        current_pnl = d_pnl_pct  # 使用 dYdX 真實盈虧
                        
                        if paper_trade:
                            max_pnl = paper_trade.max_profit_pct if hasattr(paper_trade, 'max_profit_pct') else current_pnl
                        else:
                            # 沒有 paper_trade 時，使用 dYdX 追蹤的 max
                            max_pnl = getattr(self.trader, '_dydx_max_pnl', current_pnl)
                        
                        if current_pnl > max_pnl:
                            max_pnl = current_pnl
                            self.trader._dydx_max_pnl = max_pnl  # 更新追蹤
                        
                        lock_pct, stage_name = self.trader.get_progressive_stop_loss(max_pnl)
                        
                        # 計算止損價格
                        if d_side == "LONG":
                            sl_price = d_entry * (1 + lock_pct / d_leverage / 100)
                        else:
                            sl_price = d_entry * (1 - lock_pct / d_leverage / 100)
                        
                        lock_c = G_ if lock_pct >= 1.0 else (G_ if lock_pct >= 0 else (Y_ if lock_pct >= -0.5 else R_))
                        
                        lines.append(f"   {B}🔐 N%鎖N% 鎖利{R} (v12.8)")
                        lines.append(f"      當前: {d_pnl_c}{current_pnl:+.2f}%{R} | 最高: {G_}{max_pnl:+.2f}%{R}")
                        lines.append(f"      狀態: {lock_c}{stage_name}{R}")
                        lines.append(f"      止損線: {lock_c}{lock_pct:+.1f}%{R} @ ${sl_price:,.2f}")
                        
                        # 顯示下一階段目標
                        if max_pnl < 1.0:
                            lines.append(f"      下階段: 達 {G_}+1.0%{R} → 🔐 鎖住 +1%")
                        else:
                            next_level = int(max_pnl) + 1
                            lines.append(f"      下階段: 達 {G_}+{next_level}.0%{R} → 🔐 鎖住 +{next_level}%")
                        
                        # 🆕 v14.6.9: 顯示 dYdX 止損掛單狀態
                        # 🔧 v14.6.28: 符號統一後直接使用（負=虧損，正=鎖利）
                        pending_sl = getattr(self.trader, 'pending_sl_order', None)
                        sl_update_needed = False
                        sl_update_reason = ""
                        if pending_sl:
                            sl_order_price = pending_sl.get('sl_price', 0)
                            sl_order_pct = pending_sl.get('stop_pct', 0)
                            # 符號統一：正值=鎖利，負值=虧損
                            if sl_order_pct >= 0:
                                lines.append(f"      {G_}📋 dYdX 止損掛單: ${sl_order_price:,.2f} (鎖 {sl_order_pct:+.2f}%){R}")
                            else:
                                lines.append(f"      {Y_}📋 dYdX 止損掛單: ${sl_order_price:,.2f} (止損 {sl_order_pct:.2f}%){R}")
                            if lock_pct > 0:
                                try:
                                    sl_order_pct = float(sl_order_pct)
                                    if lock_pct > sl_order_pct + 0.05:
                                        sl_update_needed = True
                                        sl_update_reason = f"掛單 {sl_order_pct:+.2f}% < 鎖利 {lock_pct:+.2f}%"
                                except Exception:
                                    pass
                        elif open_sl_info:
                            sl_order_price = open_sl_info.get("price", 0)
                            if sl_order_price > 0:
                                lines.append(f"      {Y_}📋 dYdX 止損掛單: ${sl_order_price:,.2f} (indexer){R}")
                                if lock_pct > 0:
                                    try:
                                        price_tol = max(0.01, sl_price * 0.001)
                                        if (d_side == "LONG" and sl_order_price < sl_price - price_tol) or (
                                            d_side == "SHORT" and sl_order_price > sl_price + price_tol
                                        ):
                                            sl_update_needed = True
                                            sl_update_reason = "止損掛單偏弱"
                                    except Exception:
                                        pass
                        else:
                            if lock_pct > 0:
                                lines.append(f"      {Y_}⚠️ 應掛 dYdX 止損單 (鎖利 {lock_pct:+.1f}%) 但尚未掛單{R}")

                                # 🛡️ 重要：鎖利應該要真的落地到交易所條件單
                                # 避免只在畫面顯示「應該鎖住」但實際沒掛單。
                                try:
                                    last_ts = getattr(self.trader, '_last_sl_autoplace_ts', 0.0)
                                    now_ts = time.time()
                                    # 每 10 秒最多嘗試一次，避免 Dashboard 刷新造成狂打 API
                                    if now_ts - last_ts >= 10.0:
                                        self.trader._last_sl_autoplace_ts = now_ts
                                        ok = self.trader.update_dydx_stop_loss(lock_pct)
                                        if ok:
                                            # 更新後下一輪會透過 pending_sl_order 顯示掛單資訊
                                            lines.append(f"      {G_}✅ 已自動補掛 dYdX 止損單{R}")
                                except Exception as e:
                                    lines.append(f"      {Y_}⚠️ 自動補掛失敗: {e}{R}")
                            else:
                                lines.append(f"      {y}📋 無 dYdX 止損掛單 (尚未進入鎖利區){R}")

                        if lock_pct > 0 and sl_update_needed:
                            lines.append(f"      {Y_}⚠️ dYdX 止損掛單偏弱，需更新 ({sl_update_reason}){R}")
                            try:
                                last_ts = getattr(self.trader, '_last_sl_autoplace_ts', 0.0)
                                now_ts = time.time()
                                # 每 10 秒最多嘗試一次，避免 Dashboard 刷新造成狂打 API
                                if now_ts - last_ts >= 10.0:
                                    self.trader._last_sl_autoplace_ts = now_ts
                                    ok = self.trader.update_dydx_stop_loss(lock_pct)
                                    if ok:
                                        lines.append(f"      {G_}✅ 已更新 dYdX 止損單 (鎖利 {lock_pct:+.2f}%){R}")
                            except Exception as e:
                                lines.append(f"      {Y_}⚠️ 自動更新失敗: {e}{R}")
                    
                else:
                    # 🔧 v14.6.8: 改進無持倉提示，顯示 API 回傳數量與 Paper 狀態
                    paper_trade = getattr(self.trader, 'active_trade', None)
                    # 🔧 只計算「非零倉位」數，避免誤把全市場 0 倉位清單當成持倉筆數
                    api_pos_count = 0
                    if positions:
                        for p in positions:
                            try:
                                if abs(float(p.get("size", 0))) > 0.00001:
                                    api_pos_count += 1
                            except Exception:
                                continue
                    if paper_trade:
                        # Paper 有倉但 dYdX 沒倉 - 異常狀態!
                        lines.append(f"   {R_}⚠️ dYdX 無持倉 但 Paper 有倉!{R}")
                        lines.append(f"   {R_}   可能原因: 開倉失敗/API 延遲/已被平倉{R}")
                        lines.append(f"   {y}   API 回傳持倉數: {api_pos_count}{R}")
                        
                        # 🆕 v14.6.19: 自動清空 dYdX 殘留訂單 (避免孤兒 TP/SL 單)
                        # 每 60 秒最多觸發一次清單
                        last_sweep_time = getattr(self.trader, '_last_orphan_sweep_time', 0)
                        now_ts = time.time()
                        if now_ts - last_sweep_time > 60:
                            self.trader._last_orphan_sweep_time = now_ts
                            try:
                                import asyncio
                                if self.trader.dydx_api:
                                    # 清空所有 OPEN + UNTRIGGERED 訂單
                                    cancelled = asyncio.run(
                                        self.trader.dydx_api.cancel_open_orders(
                                            symbol="BTC-USD",
                                            status=["OPEN", "UNTRIGGERED"]
                                        )
                                    )
                                    if cancelled > 0:
                                        lines.append(f"   {Y_}🧹 已清空 {cancelled} 筆孤兒訂單{R}")
                                        self.logger.warning(f"🧹 Paper有倉dYdX無倉，已清空 {cancelled} 筆殘留訂單")
                                    # 同時清空本地追蹤
                                    self.trader.pending_tp_order = None
                                    self.trader.pending_sl_order = None
                                    if self.trader.dydx_real_position:
                                        self.trader.dydx_real_position["tp_order_id"] = 0
                                        self.trader.dydx_real_position["sl_order_id"] = 0
                            except Exception as e:
                                lines.append(f"   {R_}⚠️ 清單失敗: {e}{R}")
                    else:
                        lines.append(f"   {y}無 dYdX 持倉{R} (API 回傳: {api_pos_count} 筆)")
                    self.trader.dydx_real_position = None
            except Exception as e:
                lines.append(f"   {R_}查詢失敗: {e}{R}")
        
        # 🆕 資金統計 (類似圖片排行榜)
        summary = self.trader.get_summary()
        initial = summary.get('initial_balance', 100)
        current = summary.get('current_balance', 100)
        profit_pct = summary.get('profit_pct', 0)
        wins = summary.get('wins', 0)
        losses = summary.get('losses', 0)
        total_trades = wins + losses
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0
        runtime = summary.get('runtime', '0h 0m')

        closed_trades = [t for t in getattr(self.trader, 'trades', []) if getattr(t, 'status', '') != "OPEN"]
        win_trades = [t for t in closed_trades if getattr(t, 'net_pnl_usdt', 0) > 0]
        loss_trades = [t for t in closed_trades if getattr(t, 'net_pnl_usdt', 0) <= 0]
        avg_win = sum(t.net_pnl_usdt for t in win_trades) / len(win_trades) if win_trades else 0.0
        avg_loss = sum(t.net_pnl_usdt for t in loss_trades) / len(loss_trades) if loss_trades else 0.0
        
        # ════════════════════════════════════════════════════════════════════
        # 🆕 dYdX 同步模式：使用真實餘額
        # ════════════════════════════════════════════════════════════════════
        # 🆕 dYdX 同步模式：使用真實餘額 (Cache)
        # ════════════════════════════════════════════════════════════════════
        dydx_balance = getattr(self.trader, 'real_balance_cache', 0)
        
        # 顏色判斷
        balance_c = G_ if current >= initial else R_
        pnl_c = G_ if profit_pct >= 0 else R_
        
        lines.append(f"\n{c}{'-'*80}{R}")
        
        # 🆕 根據模式顯示不同標題
        # 🔧 v14.6.10: 只要啟用 dydx_sync 就顯示 dYdX 統計 (餘額可能延遲更新)
        dydx_sync_active = getattr(self.trader, 'dydx_sync_enabled', False) == True
        if dydx_sync_active:
            lines.append(f"{B}💰 dYdX 真實交易統計 (10U Test Mode){R}  運行: {runtime}")
            lines.append(f"{c}{'-'*80}{R}")
            
            # 使用 cache 中的餘額 (已經在 update 中扣除了 deduction)
            # 處理 Initial Balance
            real_initial = getattr(self.trader, 'dydx_initial_balance', 0) or (dydx_balance + getattr(self.trader, 'balance_deduction', 0))
            # 顯示用的 Initial (扣除 deduction)
            display_initial = max(0, real_initial - getattr(self.trader, 'balance_deduction', 0))
            
            # 🔧 v14.6.10: 如果餘額還沒載入，顯示提示
            if dydx_balance <= 0:
                lines.append(f"   {Y_}⏳ 正在載入 dYdX 餘額...{R}")
            else:
                # 計算 PnL (基於顯示用的數據，或者真實數據變動，結果一樣)
                # Pnl = Current Display - Initial Display
                dydx_pnl = dydx_balance - display_initial
                dydx_pnl_pct = (dydx_pnl / display_initial * 100) if display_initial > 0 else 0
                dydx_pnl_c = G_ if dydx_pnl >= 0 else R_
                
                lines.append(f"   💵 模擬餘額: ${display_initial:.2f} → 當前: {dydx_pnl_c}${dydx_balance:.2f}{R} ({dydx_pnl_c}{dydx_pnl_pct:+.2f}%{R})")

            # 🔧 v14.6.15: 明確標示 Paper 的基準，避免與 dYdX 10U 基準直接比較造成誤解
            paper_pnl = current - initial
            lines.append(
                f"   📊 Paper 參考: ${initial:.2f} → ${current:.2f} ("
                f"{pnl_c}${paper_pnl:+.2f}{R}, {pnl_c}{profit_pct:+.2f}%{R})"
            )

            # 🧾 最近成交（用於與 dYdX 線上交易紀錄快速比對）
            try:
                import asyncio
                fills = asyncio.run(self.trader.dydx_api.get_recent_fills(limit=1))
                if fills and len(fills) > 0:
                    f0 = fills[0]
                    f_mkt = f0.get('market', '')
                    f_side = f0.get('side', '')
                    f_size = f0.get('size', '')
                    f_price = f0.get('price', '')
                    f_time = f0.get('createdAt', '')
                    lines.append(f"   🧾 最近成交: {f_mkt} {f_side} {f_size} @ {f_price} ({f_time})")
            except Exception:
                pass
            
            # 🆕 v14.6.25: 從 dYdX API 獲取真實統計 (最準確)
            real_stats = self.trader.get_dydx_real_stats()
            if real_stats:
                r_trades = real_stats.get('total_trades', 0)
                r_wins = real_stats.get('wins', 0)
                r_losses = real_stats.get('losses', 0)
                r_win_rate = real_stats.get('win_rate', 0)
                r_total_pnl = real_stats.get('total_pnl', 0)
                r_avg_pnl = real_stats.get('avg_pnl', 0)
                r_best = real_stats.get('best_trade', 0)
                r_worst = real_stats.get('worst_trade', 0)
                
                r_pnl_c = G_ if r_total_pnl >= 0 else R_
                r_avg_c = G_ if r_avg_pnl >= 0 else R_
                r_best_c = G_ if r_best >= 0 else R_
                r_worst_c = G_ if r_worst >= 0 else R_
                
                lines.append(f"\n   📊 dYdX 真實統計 (API):")
                lines.append(f"      總交易: {r_trades}筆  |  勝: {G_}{r_wins}{R}  敗: {R_}{r_losses}{R}  |  勝率: {r_win_rate:.1f}%")
                lines.append(f"      總盈虧: {r_pnl_c}${r_total_pnl:+.2f}{R}  |  平均: {r_avg_c}${r_avg_pnl:+.2f}{R}/筆")
                lines.append(f"      最佳: {r_best_c}${r_best:+.2f}{R}  最差: {r_worst_c}${r_worst:+.2f}{R}")
        else:
            lines.append(f"{B}💰 Paper Trading 統計{R}  運行: {runtime}")
            lines.append(f"{c}{'-'*80}{R}")
            # 資金顯示
            lines.append(f"   💵 起始資金: ${initial:.2f} USDT")
            lines.append(f"   💰 當前資金: {balance_c}${current:.2f} USDT{R} ({pnl_c}{profit_pct:+.2f}%{R})")
        
        # 交易統計 (Paper)
        lines.append(f"\n   📊 Paper 交易統計:")
        lines.append(f"      總交易: {total_trades}筆  |  勝: {G_}{wins}{R}  敗: {R_}{losses}{R}  |  勝率: {win_rate:.1f}%")
        
        if total_trades > 0:
            avg_pnl = (current - initial) / total_trades
            avg_c = G_ if avg_pnl >= 0 else R_
            lines.append(f"      平均盈虧: {avg_c}${avg_pnl:+.2f}{R}/筆")
            best = summary.get('best_trade', 0)
            worst = summary.get('worst_trade', 0)
            # 🔧 根據實際正負值選擇顏色
            best_c = G_ if best >= 0 else R_
            worst_c = G_ if worst >= 0 else R_
            lines.append(f"      最佳: {best_c}${best:+.2f}{R}  最差: {worst_c}${worst:+.2f}{R}")
            avg_win_c = G_ if avg_win >= 0 else R_
            avg_loss_c = G_ if avg_loss >= 0 else R_
            lines.append(f"      最佳平均: {avg_win_c}${avg_win:+.2f}{R}  最差平均: {avg_loss_c}${avg_loss:+.2f}{R}")
        
        # 交易狀態
        can_trade, reason = self.trader.can_trade()
        status_c = G_ if can_trade else y
        lines.append(f"\n{B}⏱️ 交易狀態{R}: {status_c}{reason}{R}")
        
        lines.append(f"\n{c}{'='*80}{R}")
        
        return "\n".join(lines)
    
    def _preload_strategy_history(self):
        """
        🆕 v13.2: 預載過去 5 分鐘的策略歷史數據 (Binance Brain)
        使用 Binance Futures 1 分鐘 K 線數據，確保初始策略準確
        """
        import asyncio
        import aiohttp
        
        print(f"📥 預載 Binance 歷史 K 線數據 (修正初始策略)...")
        
        async def fetch_history():
            try:
                # Binance Futures API
                base_url = "https://fapi.binance.com/fapi/v1/klines"
                params = {
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "limit": 10
                }
                
                now = time.time()
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(base_url, params=params) as resp:
                        if resp.status != 200:
                            print(f"⚠️ Binance History Fetch Failed: {resp.status}")
                            return 0
                        
                        raw_data = await resp.json()
                        if not raw_data:
                            return 0
                            
                        # Binance Format: [Open Time, Open, High, Low, Close, Volume, Close Time, Quote Vol, Trades, ...]
                        # 轉換為標準格式
                        candles = []
                        for k in raw_data:
                            candles.append({
                                "startedAt": k[0], # ms timestamp
                                "open": float(k[1]),
                                "high": float(k[2]),
                                "low": float(k[3]),
                                "close": float(k[4]),
                                "usdVolume": float(k[7]), # Quote Asset Volume (USDT)
                                "trades": int(k[8])
                            })
                        
                        # 按時間排序 (舊->新) - 確保順序
                        candles.sort(key=lambda x: x.get("startedAt"))
                        
                        samples_added = 0
                        
                        # 為每根 K 線生成模擬的秒級數據 (每根 K 線生成 10 個樣本點)
                        for candle in candles:
                            try:
                                close = candle["close"]
                                open_p = candle["open"]
                                high = candle["high"]
                                low = candle["low"]
                                volume = candle["usdVolume"]
                                
                                # 解析時間 (Binance 使用 ms timestamp)
                                base_ts = int(candle["startedAt"] / 1000)
                                
                                # Skip too old data
                                if now - base_ts > self.config.slow_window_seconds + 300: # 寬容一點
                                    continue
                                    
                                # 計算基礎屬性
                                is_bullish = close > open_p
                                body_size = abs(close - open_p)
                                wick_upper = high - max(open_p, close)
                                wick_lower = min(open_p, close) - low
                                
                                # 估算策略機率
                                probs = {}
                                
                                # 1. 吸籌/派發 (基於漲跌和實體)
                                if is_bullish:
                                    strength = min(body_size / (high - low + 1e-6), 0.8)
                                    probs['ACCUMULATION'] = 0.2 + strength * 0.6
                                    probs['DISTRIBUTION'] = 0.1
                                else:
                                    strength = min(body_size / (high - low + 1e-6), 0.8)
                                    probs['DISTRIBUTION'] = 0.2 + strength * 0.6
                                    probs['ACCUMULATION'] = 0.1
                                    
                                # 2. 陷阱類 (基於影線)
                                if wick_lower > body_size * 2: # 長下影線
                                    probs['BEAR_TRAP'] = 0.6
                                    probs['ACCUMULATION'] += 0.2
                                elif wick_upper > body_size * 2: # 長上影線
                                    probs['BULL_TRAP'] = 0.6
                                    probs['DISTRIBUTION'] += 0.2
                                    
                                # 3. 填充其他策略
                                for strategy in ['RE_ACCUMULATION', 'SHORT_SQUEEZE', 'FLASH_CRASH', 
                                                'RE_DISTRIBUTION', 'LONG_SQUEEZE', 'PUMP_DUMP', 'WHIPSAW']:
                                    if strategy not in probs:
                                        probs[strategy] = 0.05
                                        
                                # 生成樣本點 (每 6 秒一個，填滿 1 分鐘)
                                for i in range(0, 60, 6):
                                    sample_ts = base_ts + i
                                    age = now - sample_ts
                                    
                                    # 只填充符合時間窗口的歷史
                                    entry = {
                                        'timestamp': float(sample_ts),
                                        'probs': probs.copy()
                                    }
                                    
                                    if age < self.config.slow_window_seconds:
                                        self.slow_strategy_history.append(entry)
                                        samples_added += 1
                                        
                                    if age < self.config.medium_window_seconds:
                                        self.medium_strategy_history.append(entry)
                                    
                                    if age < self.config.fast_window_seconds:
                                        self.fast_strategy_history.append(entry)
                                        
                            except Exception as e:
                                continue
                                
                        return samples_added
                        
            except Exception as e:
                print(f"⚠️ 歷史數據預載失敗: {e}")
                return 0
        
        try:
            count = asyncio.run(fetch_history())
            print(f"✅ 已預載 {count} 個歷史數據點 (Binance)")
        except Exception as e:
            print(f"⚠️ 預載錯誤: {e}")
    
    def run(self, hours: float = 1.0):
        """運行交易系統"""
        self.running = True
        print(f"🚀 啟動 Whale Testnet Trader (v13.2 Hybrid)...")
        use_binance_paper = self._use_binance_paper_source()
        
        # 🆕 v13.6: 設定信號處理 (Ctrl+C, Ctrl+Z 都會觸發優雅關閉)
        def graceful_shutdown(signum, frame):
            sig_name = 'SIGINT' if signum == signal.SIGINT else 'SIGTSTP' if signum == signal.SIGTSTP else 'SIGTERM'
            print(f"\n\n⚠️ 收到 {sig_name} 信號，準備優雅關閉...")
            self.running = False
        
        signal.signal(signal.SIGINT, graceful_shutdown)   # Ctrl+C
        signal.signal(signal.SIGTSTP, graceful_shutdown)  # Ctrl+Z
        signal.signal(signal.SIGTERM, graceful_shutdown)  # kill 命令
        
        # 🆕 v13.6: 自動保存計時器
        self._last_auto_save_time = time.time()
        self._auto_save_interval = 30  # 每 30 秒自動保存
        
        # 啟動 WebSocket
        self.binance_ws.start()
        print("✅ Binance Signal Brain 啟動")
        if not use_binance_paper:
            self.ws.start()
        
        # 🆕 v14.8: 啟動 SpreadGuard 並設定數據源
        if hasattr(self, 'spread_guard') and self.spread_guard:
            # 設定數據源 (幣安 + dYdX WebSocket)
            self.spread_guard.set_data_sources(self.binance_ws, self.ws)
            print("✅ 幣安-dYdX 價差保護系統啟動")
        
        end_time = time.time() + hours * 3600
        
        mode_str = "PAPER 模擬" if self.config.paper_mode else "TESTNET 測試網"
        
        # 🆕 dYdX 同步模式標識
        dydx_sync_str = ""
        if self.config.dydx_sync_mode:
            dydx_sync_str = " + 🔴dYdX 同步"
            # 🔧 v14.2: 只在尚未連接時才連接，避免重複連接導致 429
            # (已在 _init_dydx_components() 中連接)
        
        # 🆕 獲取 dYdX 真實餘額
        dydx_balance = None
        if self.config.dydx_sync_mode and self.trader.dydx_sync_enabled and self.trader.dydx_api:
            import asyncio
            try:
                dydx_balance = asyncio.run(self.trader.dydx_api.get_account_balance())
            except:
                pass
        
        print(f"\n{'='*70}")
        print(f"🐋 Whale Trader v1.0 [{mode_str}{dydx_sync_str}]")
        print(f"   ⚠️  注意: {'純模擬，使用正式網數據' if self.config.paper_mode else '使用 Binance API'}")
        if self.config.dydx_sync_mode:
            btc_size = getattr(self.config, 'dydx_btc_size', 0.01)
            usdc_size = getattr(self.config, 'dydx_usdc_size', None)
            if usdc_size:
                print(f"   🔴 dYdX 同步: 啟用 (Aggressive Maker, ${usdc_size:.0f} USDC → {btc_size:.4f} BTC)")
            else:
                print(f"   🔴 dYdX 同步: 啟用 (Aggressive Maker, {btc_size} BTC)")
    
            # 🆕 10U Test Banner
            if getattr(self.config, 'balance_deduction', 0) > 0:
                real_bal = getattr(self.trader, 'real_balance_cache', 0) 
                # If cache is 0 (first run), we might still want to show the logic or just wait for dashboard
                # But here we can't easily get the real balance yet without async. 
                # So we just print the deduction note.
                print(f"   💵 模擬餘額模式: 真實餘額 - ${self.config.base_balance_deduct} (約 10U)")
            else:
                print(f"   💵 dYdX 真實餘額: ${getattr(self.trader, 'current_balance', 0):.2f} USDT")
        else:
            print(f"   💵 起始資金: ${self.trader.initial_balance:.2f} USDT (Paper Trading)")
        print(f"   運行時間: {hours} 小時")
        print(f"   槓桿: {self.config.leverage_min}-{self.config.leverage_max}X (動態調整)")
        print(f"   每筆: ${self.config.position_size_usdt}")
        print(f"   目標淨利潤: 5-10% (槓桿後扣手續費)")
        print(f"   持倉時間: {self.config.max_hold_min_minutes:.0f}-{self.config.max_hold_max_minutes:.0f} 分鐘 (動態)")
        if self.config.use_maker_simulation:
            batches = _coerce_int(getattr(self.config, "maker_entry_batches", 1), default=1)
            duration = _coerce_float(getattr(self.config, "maker_entry_duration_sec", 0.0), default=0.0)
            print(f"   進場方式: Maker 分批掛單 ({batches}批, {duration:g}秒)")
        else:
            print(f"   進場方式: Taker 市價單")
        print(f"   手續費: Maker {self.config.maker_fee_pct}% | Taker {self.config.taker_fee_pct}%")
        print(f"{'='*70}\n")
        
        # 設置槓桿
        self.trader.set_leverage()
        
        # 啟動 WebSocket
        if not use_binance_paper:
            self.ws.start()
        time.sleep(2)  # 等待連接
        
        # 🆕 v12.10: 預載歷史數據 (不用等 5 分鐘)
        self._preload_strategy_history()
        
        # 隱藏游標
        print("\033[?25l", end="")
        
        try:
            while self.running and time.time() < end_time:
                self.iteration += 1
                
                # ========== 即時監控 (每秒) ==========
                
                # 1. 🚨 緊急大單偵測 (鯨魚砸盤/拉盤警報)
                whale_alert = self.check_whale_emergency()
                if whale_alert:
                    self.market_data['whale_alert'] = whale_alert
                    # 如果有持倉，檢查是否需要緊急平倉
                    if self.trader.active_trade:
                        emergency_closed = self.handle_whale_emergency(whale_alert)
                        if emergency_closed:
                            self.logger.warning(f"🚨 緊急平倉執行! {whale_alert['message']}")
                else:
                    self.market_data['whale_alert'] = None
                
                # 1.5 🆕 v12.10: 急跌急漲偵測 (價格異動警報)
                price_spike = self.check_price_spike()
                if price_spike:
                    self.market_data['price_spike'] = price_spike
                    self.logger.info(price_spike['message'])
                else:
                    # 保留最近一次警報 30 秒供 Dashboard 顯示
                    if self.market_data.get('price_spike'):
                        if time.time() - self.market_data['price_spike'].get('timestamp', 0) > 30:
                            self.market_data['price_spike'] = None

                # 1.6 🆕 dYdX 實倉同步 (防殘留/幻影倉)
                # 🔧 v13.3: 傳入 market_data 以支援進場同步
                # 🔧 v14.6.24: 使用正確價格源 (dYdX sync 模式用 Oracle Price)
                if self.config.dydx_sync_mode and self.trader.dydx_api:
                    # 🔧 v14.9.6: 處理 Dashboard 觸發的同步請求
                    if getattr(self.trader, '_pending_dydx_sync', False):
                        self.trader._pending_dydx_sync = False
                        self.logger.info("🔄 處理 Dashboard 觸發的 dYdX 同步請求...")
                    
                    try:
                        reconcile_price = self.get_current_price_for_trading()
                        asyncio.run(
                            self.trader.reconcile_dydx_position(
                                paper_has_position=bool(self.trader.active_trade),
                                current_price=reconcile_price,
                                market_data=data  # 🆕 v13.3: 傳入市場數據
                            )
                        )
                    except Exception as e:
                        self.logger.debug(f"dYdX reconcile skipped: {e}")
                    
                    # 🔧 v14.6.10: 無論有無持倉都更新 dYdX 餘額 (Dashboard 統計需要)
                    try:
                        asyncio.run(self.trader._update_dydx_real_position())
                    except Exception as e:
                        self.logger.debug(f"dYdX balance update skipped: {e}")

                    # 🛑 10U 測試：當可用預算歸零就停止 dYdX 交易並退出
                    try:
                        if getattr(self.config, "zero_budget_stop_enabled", False):
                            eps = _coerce_float(getattr(self.config, "zero_budget_stop_epsilon_usdt", 0.0), default=0.0)
                            remaining = _coerce_float(getattr(self.trader, "real_balance_cache", 0.0), default=0.0)
                            if remaining <= eps:
                                self.logger.warning(
                                    f"🛑 dYdX 測試預算已歸零：remaining={remaining:.4f} <= eps={eps:.4f}，停止交易"
                                )
                                asyncio.run(self.trader.stop_dydx_trading(reason="ZERO_BUDGET_STOP"))
                                self.running = False
                                break
                    except Exception as e:
                        self.logger.debug(f"zero budget stop skipped: {e}")
                    
                    # 🆕 v14.6.16: 檢查 WebSocket 是否偵測到持倉被平倉 → 自動清掃訂單
                    # 🔧 v14.9.7: 同步關閉 Paper 倉位和清空 dydx_real_position
                    try:
                        if hasattr(self.trader, 'dydx_ws') and self.trader.dydx_ws:
                            closed_market = self.trader.dydx_ws.check_position_closed()
                            if closed_market:
                                self.logger.info(f"🧹 [WS] 偵測到 {closed_market} 持倉已平，自動清掃訂單...")
                                asyncio.run(self.trader._dydx_sweep_open_orders(
                                    reason=f"ws_position_closed:{closed_market}",
                                    market=closed_market
                                ))
                                
                                paper_master = bool(getattr(self.trader.config, "dydx_paper_master", False))
                                if self.trader.active_trade and not paper_master:
                                    # 🔧 v14.9.7: 同步關閉 Paper 倉位 (dYdX 條件單已觸發)
                                    current_price = self.get_current_price_for_trading()
                                    self.logger.info(f"🔗 [WS→Paper] dYdX 條件單已觸發，同步關閉 Paper 倉位 @ ${current_price:,.2f}")
                                    self.trader.close_position(f"dydx_sl_triggered:{closed_market}", current_price)
                                
                                # 清空 dydx_real_position（Paper 為主時留給 reconcile 補開）
                                self.trader.dydx_real_position = None
                                self.logger.info(f"✅ [WS] dydx_real_position 已清空")
                    except Exception as e:
                        self.logger.debug(f"WS position close check skipped: {e}")
                
                # 2. 檢查持倉止盈止損 (即時)
                if self.trader.active_trade:
                    # 🆕 v10.13: 傳入三線累積秒數給平倉檢查
                    self.market_data['long_alignment_seconds'] = self.long_alignment_seconds
                    self.market_data['short_alignment_seconds'] = self.short_alignment_seconds
                    
                    # 🔧 v14.6.10: 移到外面去了，這裡不再需要

                    # 🔧 v14.6.24b: 先取得交易價格 (dYdX sync 模式用 Oracle Price)
                    price_ctx = self._get_price_context()
                    trading_price = price_ctx.get('mid', 0.0) or self.get_current_price_for_trading()

                    # 🆕 v4.0 計算智能止盈
                    smart_exit = {}
                    if self.config.dynamic_profit_enabled:
                        smart_exit = self.calculate_smart_exit_target(
                            self.trader.active_trade, 
                            trading_price
                        )
                        # 將智能止盈資訊傳遞給 check_exit_conditions
                        self.market_data['smart_exit_info'] = smart_exit
                    
                    # 檢查退出條件 (傳入智能止盈資訊)
                    strategy_config = load_trading_strategy()  # 使用本地定義的函數
                    
                    # 在 action_details 中加入智能止盈資訊
                    exit_reason, action_details = self.trader.check_exit_conditions(
                        price_ctx,
                        strategy_config,
                        self.market_data  # 🆕 v10.9.1 傳入市場數據用於動態調整
                    )
                    
                    # 如果普通止盈未觸發，檢查智能止盈
                    if not exit_reason and smart_exit.get('should_exit', False):
                        exit_reason = "CLOSED_SMART_TP"
                        action_details['smart_exit_reason'] = smart_exit.get('exit_reason', '')
                    
                    if exit_reason:
                        # 記錄平倉前的交易資訊
                        trade = self.trader.active_trade
                        trade_info = {
                            'strategy': trade.strategy,
                            'direction': trade.direction,
                            'entry_price': trade.entry_price,
                            'exit_reason': exit_reason,
                            'market_regime': self.market_regime
                        }
                        
                        # 🆕 v4.0 記錄智能止盈資訊
                        if exit_reason == "CLOSED_SMART_TP":
                            smart_reason = action_details.get('smart_exit_reason', smart_exit.get('exit_reason', ''))
                            self.logger.info(f"🎯 智能止盈觸發: {smart_reason}")
                        
                        # 執行平倉
                        # 🔧 v14.6.23: 使用正確價格源 (dYdX sync 模式用 Oracle Price)
                        exit_price = self.get_net_price_for_direction(trade.direction, price_ctx)
                        closed_trade = self.trader.close_position(exit_reason, exit_price)
                        
                        # 🆕 v3.0 更新反轉策略統計
                        if closed_trade:
                            is_win = closed_trade.net_pnl_usdt > 0
                            trade_info['net_pnl'] = closed_trade.net_pnl_usdt
                            self._update_trade_result(is_win, trade_info)
                            
                            # 🆕 v4.0 記錄出場數據，用於分析最佳止盈點
                            self.record_exit_for_analysis(closed_trade, exit_reason)
                            
                            # 🆕 v10.9 記錄兩階段統計
                            if self.two_phase_exit:
                                net_pnl_pct = closed_trade.net_pnl_usdt / closed_trade.position_size_usdt * 100 if closed_trade.position_size_usdt else 0
                                self.two_phase_exit.record_trade_result(net_pnl_pct, is_win)
                            
                            # 🆕 v13.6: 記錄交易到統一回測收集器
                            if self.backtest_collector:
                                try:
                                    self.backtest_collector.record_trade({
                                        'trade_id': closed_trade.trade_id,
                                        'direction': closed_trade.direction,
                                        'strategy': closed_trade.strategy,
                                        'entry_price': closed_trade.entry_price,
                                        'exit_price': closed_trade.exit_price,
                                        'entry_time': closed_trade.entry_time,
                                        'exit_time': closed_trade.exit_time,
                                        'pnl_pct': closed_trade.pnl_pct,
                                        'pnl_usdt': closed_trade.pnl_usdt,
                                        'net_pnl_usdt': closed_trade.net_pnl_usdt,
                                        'fee_usdt': closed_trade.fee_usdt,
                                        'exit_reason': exit_reason,
                                        'hold_seconds': closed_trade.hold_seconds,
                                        'leverage': closed_trade.actual_leverage or closed_trade.leverage,
                                        'position_size_usdt': closed_trade.position_size_usdt,
                                        'position_size_btc': closed_trade.position_size_btc,
                                        'obi': closed_trade.obi,
                                        'six_dim_long': closed_trade.six_dim_long_score,
                                        'six_dim_short': closed_trade.six_dim_short_score,
                                        'probability': closed_trade.probability,
                                        'confidence': closed_trade.confidence,
                                    })
                                except Exception as e:
                                    self.logger.debug(f"記錄交易到收集器失敗: {e}")
                
                # 3. 分析市場 (即時數據每秒更新，策略分析每 30 秒更新)
                self.analyze_market()
                
                # 🆕 v13.6: 記錄價格數據到回測收集器 (每秒)
                market_price = self._get_market_price()
                if self.backtest_collector and market_price > 0:
                    try:
                        # 從 market_data 取得即時指標
                        obi = self.market_data.get('obi', 0)
                        volume_1m = self.market_data.get('volume_1m', 0)
                        price_change_1m = self.market_data.get('price_change_1m', 0)
                        price_change_5m = self.market_data.get('price_change_5m', 0)
                        
                        self.backtest_collector.record_price(
                            price=market_price,
                            obi=obi,
                            volume_1m=volume_1m,
                            price_change_1m=price_change_1m,
                            price_change_5m=price_change_5m
                        )
                    except Exception as e:
                        pass  # 靜默失敗，不影響主流程
                
                # 🆕 每10秒記錄當前信號狀態 (不管有沒有進場)
                if self.iteration % 10 == 0:
                    self._record_signal_snapshot()
                
                # 3.5 🆕 更新 MTF 多時間框架分析 (每分鐘自動更新內部數據)
                if self.mtf_analyzer and self.mtf_enabled:
                    try:
                        self.mtf_analyzer.update(market_price)
                    except Exception as e:
                        self.logger.warning(f"MTF 更新失敗: {e}")
                
                # 4. 檢查進場信號 (每秒檢查，信號確認後立即執行)
                should_enter, direction, data = self.should_enter()
                
                # 🔧 v14.1: 價格為 0 時跳過交易邏輯 (API rate limit)
                if market_price <= 0:
                    time.sleep(1)
                    continue
                
                # 🆕 v12.0 預掛單模式處理
                # ═══════════════════════════════════════════════════════════════
                if self.config.pre_entry_mode and not self.trader.active_trade:
                    price_ctx = self._get_price_context()
                    # 獲取信號強度
                    detected = data.get('detected_strategy', {})
                    strategy_prob = detected.get('probability', 0)
                    pending_direction = data.get('signal_status', {}).get('pending_direction') or direction
                    
                    # 🔧 v12.12.1: 用六維競爭進度作為信號強度，而非策略機率
                    # 策略機率通常只有 10-20%，永遠達不到 90% 閾值
                    # 六維競爭進度才是真正反映信號準備程度的指標
                    if pending_direction == "LONG":
                        signal_strength = min(self.long_alignment_seconds / self.min_alignment_seconds, 1.0)
                    elif pending_direction == "SHORT":
                        signal_strength = min(self.short_alignment_seconds / self.min_alignment_seconds, 1.0)
                    else:
                        signal_strength = 0
                    
                    # 1. 檢查現有預掛單是否成交
                    if self.trader.pending_entry_order:
                        # 🔧 v14.6.23: 使用正確價格源
                        order_dir = self.trader.pending_entry_order.get('direction', 'LONG')
                        check_price = self.get_entry_price(order_dir, price_ctx)
                        fill_price = self.trader.check_pre_entry_fill(
                            check_price,
                            signal_strength
                        )
                        if fill_price:
                            # 預掛單成交! 建立交易記錄
                            order = self.trader.pending_entry_order
                            self.logger.info(f"✅ 預掛單成交: {order['direction']} @ ${fill_price:,.2f}")
                            
                            # 使用成交價開倉
                            self.trader.open_position(
                                direction=order['direction'],
                                current_price=fill_price,  # 使用掛單成交價
                                strategy=detected.get('name', 'PRE_ENTRY'),
                                probability=order['signal_strength'],
                                confidence=detected.get('confidence', 0),
                                market_data=order['market_data'],
                                is_limit_fill=True  # 🆕 v13.2: 標記為 Limit Fill，使用精確成交價
                            )
                            
                            # 🆕 v14.6: 同時掛止盈 + 止損單 (dYdX 雙向預掛)
                            # 🔧 v12.1: 使用 active_trade.entry_price (可能已被 dYdX 成交價更新)
                            if self.trader.active_trade:
                                trade = self.trader.active_trade
                                leverage = trade.actual_leverage or trade.leverage
                                # 🔧 重要: 用 trade.entry_price 而非 fill_price
                                # 因為 open_position 內部會用 dYdX 成交價更新 entry_price
                                actual_entry = trade.entry_price
                                
                                # 🆕 v14.6: 優先使用雙向預掛單
                                # 🔧 v14.6.17: 檢查是否已有 TP 訂單，避免重複掛單造成開新倉
                                existing_tp_id = 0
                                if self.trader.dydx_real_position:
                                    existing_tp_id = self.trader.dydx_real_position.get("tp_order_id", 0)
                                
                                if existing_tp_id and existing_tp_id > 0:
                                    self.logger.info(f"⚠️ 已有 TP 訂單 ID: {existing_tp_id}，跳過重複掛單")
                                elif self.trader.dydx_sync_enabled and self.trader.dydx_api:
                                    self.trader.place_dydx_tp_sl_orders(
                                        entry_price=actual_entry,
                                        direction=trade.direction,
                                        leverage=leverage
                                    )
                                else:
                                    # Fallback: 只掛 TP
                                    self.trader.place_pre_take_profit_order(
                                        entry_price=actual_entry,
                                        direction=trade.direction,
                                        leverage=leverage
                                    )
                                self.logger.info(f"📈 掛單價基於: ${actual_entry:,.2f}")
                            
                            self.trader.pending_entry_order = None
                            self._reset_signal_tracking()
                    
                    # 2. 信號達到預掛閾值，建立新預掛單
                    elif signal_strength >= self.config.pre_entry_threshold and pending_direction:
                        can_trade_now, _ = self.trader.can_trade()
                        if can_trade_now:
                            # 🔧 v14.6.24: 使用正確價格源
                            pre_entry_price = self.get_entry_price(pending_direction, price_ctx)
                            # 🔧 v12.12.1: 包含六維分數供智能過濾
                            six_dim = data.get('six_dim', {})
                            self.trader.place_pre_entry_order(
                                direction=pending_direction,
                                current_price=pre_entry_price,
                                signal_strength=signal_strength,
                                market_data={
                                    'obi': data.get('obi', 0),
                                    'wpi': data.get('trade_imbalance', 0),
                                    'strategy_probs': data.get('strategy_probs', {}),
                                    'price_change_1m': data.get('price_change_1m', 0),
                                    'price_change_5m': data.get('price_change_5m', 0),
                                    # 🆕 v12.12.1: 六維信號分數
                                    'six_dim_score': {
                                        'long': six_dim.get('long_score', 0),
                                        'short': six_dim.get('short_score', 0),
                                        'fast_dir': six_dim.get('fast_dir', 'NEUTRAL'),
                                        'medium_dir': six_dim.get('medium_dir', 'NEUTRAL'),
                                        'slow_dir': six_dim.get('slow_dir', 'NEUTRAL'),
                                    }
                                }
                            )
                
                # 🆕 v12.0 檢查止盈掛單成交
                # 🔧 v14.6.24: 使用正確價格源 (dYdX sync 模式用 Oracle Price)
                if self.trader.active_trade and self.trader.pending_tp_order:
                    price_ctx = self._get_price_context()
                    tp_direction = self.trader.pending_tp_order.get('direction')
                    tp_check_price = self.get_tp_check_price(tp_direction, price_ctx)
                    tp_fill = self.trader.check_pre_take_profit_fill(tp_check_price)
                    if tp_fill:
                        # 止盈成交! 平倉
                        self.trader.close_position("CLOSED_PRE_TP", tp_fill)
                        self._reset_signal_tracking()
                
                # 🆕 v14.6.1 檢查預掛止損單成交 (dYdX 同步模式優先使用預掛價格)
                # 🔧 v14.6.24: 使用正確價格源 (dYdX sync 模式用 Oracle Price)
                if self.trader.active_trade and self.trader.pending_sl_order and self.trader.dydx_sync_enabled:
                    price_ctx = self._get_price_context()
                    sl_check_price = self.get_sl_check_price(price_ctx)
                    sl_fill = self.trader.check_pre_stop_loss_fill(sl_check_price)
                    if sl_fill:
                        # 止損成交! 使用精確預掛價格平倉
                        self.trader.close_position("CLOSED_PRE_SL", sl_fill)
                        self._reset_signal_tracking()
                        # 🔧 跳過後續的 progressive stop loss 檢查，避免重複處理
                        continue
                
                # 🆕 v12.2 階段性止損檢查 (Progressive Stop Loss)
                # 🔧 v14.6.1: 如果 dYdX 有預掛 SL 單，讓預掛單自動成交，不走市價
                # 🔧 v14.6.24: 使用正確價格源 (dYdX sync 模式用 Oracle Price)
                if self.trader.active_trade:
                    # 如果有預掛 SL 且是 dYdX 同步模式，依賴預掛單而非市價
                    has_pending_sl = (
                        self.trader.dydx_sync_enabled and 
                        self.trader.pending_sl_order and 
                        self.trader.pending_sl_order.get('dydx_order_id')
                    )
                    exchange_conditional_orders = []
                    exchange_has_conditional = False
                    if self.trader.dydx_sync_enabled and self.trader.dydx_api:
                        exchange_conditional_orders = self.trader._get_open_conditional_orders_sync("BTC-USD")
                        exchange_has_conditional = bool(exchange_conditional_orders)
                    if exchange_has_conditional and not has_pending_sl:
                        has_pending_sl = True
                    has_sl_evidence = bool(self.trader.pending_sl_order or exchange_has_conditional)
                    
                    # 🔧 v14.6.37: 檢查 dYdX 止損單價格是否與軟體止損線匹配
                    # 如果不匹配，不應該等待 dYdX 條件單（會錯過鎖利！）
                    sl_order_synced = False
                    if has_pending_sl and self.trader.pending_sl_order:
                        dydx_sl_pct = self.trader.pending_sl_order.get('stop_pct', -999)
                        # 會在 check_progressive_stop_loss 中計算軟體止損線
                        # 這裡先標記為 True，後面再驗證
                        sl_order_synced = True
                    
                    price_ctx = self._get_price_context()
                    psl_check_price = self.get_sl_check_price(price_ctx)
                    sl_result = self.trader.check_progressive_stop_loss(psl_check_price)

                    # 🆕 TP 更新策略（僅在指定時機調整 TP）
                    tp_update_price = self.get_tp_check_price(self.trader.active_trade.direction, price_ctx)
                    self.trader.maybe_update_dydx_take_profit(tp_update_price, self.market_data)
                    
                    # 🆕 v14.6.31: 執行排程的 dYdX 止損單更新（節流 + backoff）
                    if hasattr(self.trader, '_pending_sl_update') and self.trader._pending_sl_update:
                        pending = self.trader._pending_sl_update
                        stop_pct = pending.get('stop_pct', 0)
                        try:
                            now_ts = time.time()
                            if now_ts < getattr(self.trader, "_dydx_tx_backoff_until", 0.0):
                                ok = False
                            else:
                                # 只呼叫一次（update_dydx_stop_loss_async 內部已包含掃單/保護邏輯）
                                ok = self.trader.update_dydx_stop_loss(stop_pct)
                            if ok:
                                self.logger.info(f"   ✅ [v14.6.31] dYdX 止損單已掛: {stop_pct:+.2f}%")
                            else:
                                self.logger.warning(f"   ⚠️ [v14.6.31] dYdX 止損單掛單失敗")
                        except Exception as e:
                            self.logger.error(f"   ❌ [v14.6.31] dYdX 止損單異常: {e}")
                        finally:
                            self.trader._pending_sl_update = None  # 清空排程

                    if self.trader.dydx_sync_enabled and self.trader.dydx_api and self.trader.dydx_real_position:
                        try:
                            asyncio.run(self.trader._ensure_dydx_protection_orders(reason="active_trade_check"))
                        except Exception as e:
                            self.logger.debug(f"dYdX protection check skipped: {e}")
                    
                    if sl_result:
                        # 🆕 v14.6.33: 解析返回值 (reason, exit_price, is_emergency)
                        if len(sl_result) == 3:
                            reason, exit_price, is_emergency = sl_result
                        else:
                            reason, exit_price = sl_result
                            is_emergency = True  # 舊格式，預設緊急
                        
                        # 🔧 v14.6.36: 節流「等待 dYdX 條件單」訊息，每 30 秒最多打印一次
                        now_ts = time.time()
                        last_wait_log_ts = getattr(self, '_last_wait_dydx_log_ts', 0)
                        should_log_wait = (now_ts - last_wait_log_ts) >= 30.0

                        sl_sync_grace_active = False
                        sl_sync_grace_sec = _coerce_float(
                            getattr(self.trader.config, "dydx_sl_sync_grace_sec", 4.0),
                            default=4.0
                        )
                        pending_sl_update = getattr(self.trader, "_pending_sl_update", None)
                        last_sl_update_ts = getattr(self.trader, "_last_sl_update_attempt_ts", 0.0)
                        if pending_sl_update or (last_sl_update_ts and (now_ts - last_sl_update_ts) <= sl_sync_grace_sec):
                            sl_sync_grace_active = True
                            if not has_pending_sl:
                                has_pending_sl = True
                            if not has_sl_evidence:
                                has_sl_evidence = True
                        
                        # 🔧 v14.6.37: 檢查 dYdX 止損單是否與軟體止損線同步
                        # 如果 dYdX 止損單的觸發價格與軟體計算的止損線差距太大，不能等待
                        dydx_sl_synced = False
                        expected_stop_pct = None
                        pct_diff = None
                        price_diff_pct = None
                        dydx_sl_price = 0.0
                        dydx_sl_pct = None
                        exchange_sl_price = 0.0
                        leverage = 0.0
                        try:
                            trade_lev = getattr(self.trader.active_trade, 'actual_leverage', None)
                            if not trade_lev:
                                trade_lev = getattr(self.trader.active_trade, 'leverage', None)
                            leverage = _coerce_float(trade_lev, default=50.0)
                        except Exception:
                            leverage = 50.0
                        if leverage <= 0:
                            leverage = 50.0
                        if has_pending_sl and exit_price <= 0:
                            dydx_sl_synced = True

                        if has_pending_sl and self.trader.pending_sl_order:
                            dydx_sl_price = self.trader.pending_sl_order.get('sl_price', 0)
                            dydx_sl_pct = self.trader.pending_sl_order.get('stop_pct', None)

                            try:
                                if self.trader.active_trade:
                                    expected_stop_pct, _ = self.trader.get_progressive_stop_loss(
                                        self.trader.active_trade.max_profit_pct
                                    )
                            except Exception:
                                expected_stop_pct = None

                            if dydx_sl_pct is not None and expected_stop_pct is not None:
                                try:
                                    pct_diff = abs(float(dydx_sl_pct) - float(expected_stop_pct))
                                    # 🛡️ 用 ROE% 比較同步性，避免高槓桿下價格差距過寬誤判
                                    dydx_sl_synced = pct_diff <= 0.1
                                except Exception:
                                    pct_diff = None

                            if pct_diff is None:
                                price_diff_pct = abs(dydx_sl_price - exit_price) / exit_price * 100 if exit_price > 0 else 999
                                # 🔧 動態價格容差（依槓桿縮放），避免 0.5% 太寬
                                price_tol = max(0.01, 0.1 / leverage)
                                dydx_sl_synced = price_diff_pct < price_tol

                        if not dydx_sl_synced and exchange_has_conditional:
                            for order in exchange_conditional_orders:
                                trigger_price = self.trader._extract_dydx_conditional_trigger_price(order)
                                if trigger_price > 0:
                                    if exchange_sl_price <= 0 or (exit_price > 0 and abs(trigger_price - exit_price) < abs(exchange_sl_price - exit_price)):
                                        exchange_sl_price = trigger_price
                            if exchange_sl_price > 0 and exit_price > 0:
                                price_diff_pct = abs(exchange_sl_price - exit_price) / exit_price * 100
                                price_tol = max(0.01, 0.1 / leverage)
                                if price_diff_pct < price_tol:
                                    dydx_sl_synced = True
                            elif expected_stop_pct is not None and expected_stop_pct > 0:
                                dydx_sl_synced = True

                        if not dydx_sl_synced and sl_sync_grace_active:
                            dydx_sl_synced = True

                        if not dydx_sl_synced and should_log_wait and has_sl_evidence:
                            if pct_diff is not None and dydx_sl_pct is not None and expected_stop_pct is not None:
                                self.logger.warning(
                                    f"   ⚠️ [v14.6.37] dYdX 止損單未同步! dYdX={dydx_sl_pct:+.2f}% vs 軟體={expected_stop_pct:+.2f}% (差距 {pct_diff:.2f}%)"
                                )
                            else:
                                ref_price = exchange_sl_price or dydx_sl_price or exit_price
                                diff_pct = price_diff_pct
                                if diff_pct is None and ref_price > 0 and exit_price > 0:
                                    diff_pct = abs(ref_price - exit_price) / exit_price * 100
                                if diff_pct is None:
                                    diff_pct = 999
                                self.logger.warning(
                                    f"   ⚠️ [v14.6.37] dYdX 止損單未同步! dYdX=${ref_price:,.2f} vs 軟體=${exit_price:,.2f} (差距 {diff_pct:.2f}%)"
                                )
                        
                        # 🔧 v14.9.9: 等待超時保護
                        # 如果等待 dYdX 條件單超過等待時間還沒觸發，強制本地平倉
                        wait_start_key = '_dydx_sl_wait_start'
                        wait_timeout_sec = _coerce_float(getattr(self.trader.config, "dydx_sl_wait_timeout_sec", 12.0), default=12.0)
                        if not hasattr(self, wait_start_key) or getattr(self, wait_start_key, 0) == 0:
                            setattr(self, wait_start_key, now_ts)
                        wait_elapsed = now_ts - getattr(self, wait_start_key, now_ts)
                        wait_timeout = wait_elapsed >= wait_timeout_sec
                        
                        # 🆕 v14.6.33 + v14.6.37 + v14.9.9: 混合止損策略
                        # 只有當 dYdX 止損單已同步、非緊急、且未超時時，才等待 dYdX 條件單
                        if has_pending_sl and dydx_sl_synced and not is_emergency and not wait_timeout:
                            # 有預掛 SL、已同步、且非緊急：信任 dYdX 條件單
                            if should_log_wait:
                                self.logger.info(f"📉 階段性止損觸發: {reason}")
                                self.logger.info(f"   ⏳ [v14.6.33] 等待 dYdX 條件單觸發 (目標價: ${exit_price:,.2f})")
                                self.logger.info(f"   💡 dYdX 止損單會在 Oracle Price 到達時自動成交")
                                self._last_wait_dydx_log_ts = now_ts
                            # 不執行 close_position，讓 dYdX 條件單處理
                            continue
                        
                        # 🆕 v14.9.13: 強制本地平倉前，先用 REST 確認保護單是否仍有效/可補掛
                        if (wait_timeout or (has_pending_sl and not dydx_sl_synced)) and not is_emergency:
                            if self.trader.dydx_sync_enabled and self.trader.dydx_api:
                                rest_has_position = False
                                rest_conditionals = []
                                try:
                                    positions = asyncio.run(self.trader._get_dydx_positions_with_cache())
                                except Exception:
                                    positions = []
                                for pos in positions or []:
                                    if pos.get("market") != "BTC-USD":
                                        continue
                                    size = _coerce_float(pos.get("size", 0.0), default=0.0)
                                    if abs(size) > 0.0001:
                                        rest_has_position = True
                                        break
                                if rest_has_position:
                                    rest_conditionals = self.trader._get_open_conditional_orders_sync("BTC-USD")
                                    if not rest_conditionals:
                                        try:
                                            asyncio.run(self.trader._ensure_dydx_protection_orders(reason="pre_forced_close"))
                                        except Exception as e:
                                            self.logger.warning(f"   ⚠️ [v14.9.13] dYdX 保護單補掛失敗: {e}")
                                        rest_conditionals = self.trader._get_open_conditional_orders_sync("BTC-USD")
                                if rest_has_position and rest_conditionals:
                                    if should_log_wait:
                                        self.logger.warning("   ✅ [v14.9.13] REST 確認條件單存在，延長等待避免本地平倉")
                                        self._last_wait_dydx_log_ts = now_ts
                                    setattr(self, wait_start_key, now_ts)
                                    continue

                        # 重置等待計時器
                        setattr(self, wait_start_key, 0)
                        
                        if wait_timeout:
                            self.logger.warning(f"   ⏰ [v14.9.9] 等待 dYdX 條件單超時 ({wait_elapsed:.1f}s)，強制本地平倉!")
                        
                        self.logger.info(f"📉 階段性止損觸發: {reason}")
                        
                        # 🔧 v14.9.12: 任何止損都設置冷卻標記防止無限循環同步
                        # (不只緊急止損，普通止損也需要！因為止損後 Paper 清空會觸發 SYNC)
                        self.trader._last_emergency_stop_ts = time.time()
                        self.logger.info(f"   ⏳ [v14.9.12] 設置 30 秒止損冷卻期 (防止 SYNC 循環)")
                        
                        # 🔧 v14.9.10: 緊急止損時，對 dYdX 發出真實的市價平倉指令
                        if is_emergency and self.trader.dydx_sync_enabled and self.trader.dydx_real_position:
                            self.logger.warning(f"   🆘 [v14.9.10] 緊急市價平倉 dYdX 真實倉位!")
                            dydx_close_success = False
                            try:
                                import asyncio
                                # 先清理所有條件單
                                try:
                                    asyncio.run(self.trader._dydx_cancel_conditional_orders(reason="emergency_stop_cleanup"))
                                except Exception as e:
                                    self.logger.warning(f"   ⚠️ 清理條件單失敗: {e}")
                                
                                # 市價平倉 (用 place_fast_order 反向平倉)
                                dydx_pos = self.trader.dydx_real_position
                                dydx_size = dydx_pos.get('size', 0)
                                dydx_direction = dydx_pos.get('direction', 'LONG')
                                if dydx_size > 0:
                                    # 反向平倉: LONG → 賣出, SHORT → 買入
                                    close_side = "SHORT" if dydx_direction == "LONG" else "LONG"
                                    self.logger.warning(f"   🔴 發送 dYdX 市價平倉: 平{dydx_direction} {dydx_size} BTC (發送 {close_side})")
                                    tx_hash, fill_price = asyncio.run(
                                        self.trader.dydx_api.place_fast_order(
                                            side=close_side,
                                            size=dydx_size,
                                            maker_timeout=0.0,  # 直接 IOC 市價
                                            fallback_to_ioc=True
                                        )
                                    )
                                    if tx_hash and fill_price > 0:
                                        self.logger.info(f"   ✅ dYdX 市價平倉成功: ${fill_price:,.2f}")
                                        self.trader.dydx_real_position = None
                                        dydx_close_success = True
                                    else:
                                        self.logger.error(f"   ❌ dYdX 市價平倉失敗! 保留 Paper 倉位，避免 SYNC 循環")
                            except Exception as e:
                                self.logger.error(f"   ❌ dYdX 緊急平倉異常: {e}")
                            
                            # 🔧 v14.9.12: dYdX 平倉失敗時不關閉 Paper 倉位
                            # 這樣可以防止 SYNC 循環 (Paper 有倉 + dYdX 有倉 = 不會觸發 SYNC)
                            if not dydx_close_success:
                                self.logger.warning(f"   ⚠️ [v14.9.12] dYdX 平倉失敗，保留 Paper 倉位避免 SYNC 循環")
                                self.trader._last_emergency_stop_ts = time.time() + 300  # 延長冷卻 5 分鐘
                                continue  # 跳過本次止損，等待下次重試
                        elif has_pending_sl and is_emergency:
                            # 有預掛 SL 但緊急：先取消 dYdX 條件單，再市價平倉
                            self.logger.warning(f"   🆘 [v14.6.33] 緊急平倉！虧損超過安全閾值")
                            self.logger.info(f"   🔒 使用預掛 SL 精確價格: ${exit_price:,.2f}")
                        elif has_pending_sl and not dydx_sl_synced:
                            # 有預掛 SL 但未同步：軟體直接平倉（避免錯過鎖利）
                            self.logger.warning(f"   ⚡ [v14.6.37] dYdX 止損單未同步，軟體直接平倉!")
                        else:
                            # 無預掛 SL（非 dYdX sync 模式）：直接平倉
                            pass
                        
                        force_market_close = bool(self.trader.pending_sl_order and not dydx_sl_synced)
                        self.trader.close_position(reason, exit_price, force_market_close=force_market_close)
                        self._reset_signal_tracking()
                
                # ⚡ 信號確認完成，立即進場！
                # 🔧 v13.6.4: 無論 pre_entry_mode，當六維勝出時都直接進場
                # 原因: pre_entry 的 Maker 邏輯會錯過趨勢行情
                can_trade_now, trade_status = self.trader.can_trade()
                
                if should_enter and not self.trader.active_trade and can_trade_now:
                    # 🆕 v13.8: 追單保護檢查
                    # 🔧 v14.6.24: 使用正確價格源 (dYdX sync 模式用 Oracle Price)
                    chase_check_price = self.get_current_price_for_trading()
                    if self.chase_protection:
                        # 記錄價格
                        self.chase_protection.record_price(chase_check_price)
                        
                        # 獲取六維分數
                        six_dim = data.get('six_dim', {})
                        six_dim_score = six_dim.get('long_score', 0) if direction == "LONG" else six_dim.get('short_score', 0)
                        
                        # 檢查是否允許進場
                        chase_allowed, chase_reason, chase_details = self.chase_protection.check_entry(
                            direction=direction,
                            current_price=chase_check_price,
                            six_dim_score=six_dim_score
                        )
                        
                        if not chase_allowed:
                            self.logger.warning(f"🛡️ 追單保護阻擋: {chase_reason}")
                            if chase_details.get('recommendation'):
                                self.logger.info(f"   💡 {chase_details['recommendation']}")
                            self.market_data['signal_status'] = self.market_data.get('signal_status', {})
                            self.market_data['signal_status']['chase_blocked'] = chase_reason
                            # 跳過本次進場
                            should_enter = False
                        else:
                            # 顯示警告
                            for warning in chase_details.get('warnings', []):
                                self.logger.info(f"   {warning}")
                
                # 🆕 v14.15: dYdX Sync 模式下，進場前快速檢查 (WS + API)
                dydx_entry_blocked = False
                if should_enter and self.config.dydx_sync_mode and self.trader.dydx_api:
                    try:
                        # 1. WS 即時檢查：是否已有持倉
                        if hasattr(self.trader, 'dydx_ws') and self.trader.dydx_ws:
                            if self.trader.dydx_ws.has_position("BTC-USD"):
                                self.logger.warning("⚠️ [WS] dYdX 已有持倉，跳過進場")
                                dydx_entry_blocked = True
                        
                        # 2. 餘額檢查：是否足夠開倉
                        if not dydx_entry_blocked:
                            balance = getattr(self.trader, 'real_balance_cache', 0) or 0
                            btc_price = data.get('price', 90000)
                            btc_size = getattr(self.config, 'dydx_btc_size', 0.002)
                            leverage = getattr(self.config, 'leverage', 50)
                            required_margin = (btc_size * btc_price) / leverage * 1.1  # 110% 安全邊際
                            
                            if balance < required_margin:
                                self.logger.warning(f"⚠️ [餘額] 不足開倉: ${balance:.2f} < ${required_margin:.2f}")
                                dydx_entry_blocked = True
                        
                        # 3. Paper 端檢查：active_trade
                        if not dydx_entry_blocked and self.trader.active_trade:
                            self.logger.warning("⚠️ [Paper] 已有持倉，跳過進場")
                            dydx_entry_blocked = True
                            
                    except Exception as e:
                        self.logger.debug(f"dYdX 進場檢查異常: {e}")
                
                if should_enter and not self.trader.active_trade and can_trade_now and not dydx_entry_blocked:
                    # 🔧 使用 detected_strategy 而非 primary_strategy
                    detected = data.get('detected_strategy', {})
                    
                    # 🎲 v14.1.1: 隨機進場模式使用專用策略名稱
                    if self.config.random_entry_mode:
                        strategy_name = 'RANDOM_BALANCED'
                        probability = 0.5
                        confidence = 0.5
                    else:
                        strategy_name = detected.get('name', 'SIX_DIM')
                        probability = detected.get('probability', 0)
                        confidence = detected.get('confidence', 0)
                    
                    # 提供進場建議
                    # 🔧 v14.6.24: 使用正確價格源 (dYdX sync 模式用 Oracle Price)
                    price_ctx = self._get_price_context()
                    entry_suggestion_price = price_ctx.get('mid', 0.0) or self.get_current_price_for_trading()
                    suggestion = self.get_entry_suggestion(direction, entry_suggestion_price)
                    self.market_data['entry_suggestion'] = suggestion
                    
                    # 🆕 v13.6.5: Aggressive Entry - 加一點滑點確保成交
                    # 做多: 用稍高價格買入 (確保能買到)
                    # 做空: 用稍低價格賣出 (確保能賣到)
                    aggressive_slippage_pct = 0.003  # 0.003% ≈ $2.7 @ $90k
                    # 🔧 v14.6.23: 使用正確價格源 (dYdX sync 模式用 Oracle Price)
                    base_price = self.get_entry_price(direction, price_ctx)
                    if direction == "LONG":
                        entry_price = base_price * (1 + aggressive_slippage_pct / 100)
                    else:  # SHORT
                        entry_price = base_price * (1 - aggressive_slippage_pct / 100)
                    
                    # 🎲 v14.1.1: 顯示正確的進場原因
                    if self.config.random_entry_mode:
                        queue_status = f"剩餘 {self._get_active_wave_remaining()} 筆"
                        self.logger.info(f"🎲 平衡隨機進場: {direction} @ ${entry_price:,.2f} ({queue_status})")
                    else:
                        self.logger.info(f"✅ 六維信號勝出，執行進場: {direction} @ ${entry_price:,.2f} (滑點 {aggressive_slippage_pct}%)")
                    
                    # 🔧 v14.13: 修復 six_dim 資訊傳入 bug
                    six_dim_info = self.market_data.get('six_dim', {})
                    trade_result = self.trader.open_position(
                        direction=direction,
                        current_price=entry_price,  # 🔧 使用調整後的價格
                        strategy=strategy_name,
                        probability=probability,
                        confidence=confidence,
                        market_data={
                            'obi': data.get('obi', 0),
                            'wpi': data.get('trade_imbalance', 0),
                            'strategy_probs': data.get('strategy_probs', {}),
                            'price_change_1m': data.get('price_change_1m', 0),
                            'price_change_5m': data.get('price_change_5m', 0),
                            # 🆕 v14.13: 加入六維指標完整資訊
                            'six_dim': {
                                'long_score': six_dim_info.get('long_score', 0),
                                'short_score': six_dim_info.get('short_score', 0),
                                'fast_dir': six_dim_info.get('fast_dir', ''),
                                'medium_dir': six_dim_info.get('medium_dir', ''),
                                'slow_dir': six_dim_info.get('slow_dir', ''),
                                'obi_dir': six_dim_info.get('obi_dir', ''),
                                'momentum_dir': six_dim_info.get('momentum_dir', ''),
                                'volume_dir': six_dim_info.get('volume_dir', ''),
                            },
                        }
                    )
                    
                    # 🆕 v14.15: 檢查開倉是否成功，失敗則刷新餘額快取
                    if trade_result is None:
                        if self.config.dydx_sync_mode and self.trader.dydx_api:
                            self.logger.warning(f"⚠️ 開倉失敗，刷新 dYdX 狀態")
                            # 立即刷新餘額快取，下次檢查會用新數據
                            try:
                                import asyncio
                                asyncio.run(self.trader._update_dydx_real_position())
                            except Exception:
                                pass
                        else:
                            self.logger.warning(f"⚠️ 開倉失敗")
                    
                    # 重置信號追蹤，避免重複進場
                    self._reset_signal_tracking()
                
                # ========== 定期決策 (每 30 秒) ==========
                
                # 5. 定期策略分析同步
                if time.time() - self.last_analysis_time >= self.config.analysis_interval_sec:
                    self.last_analysis_time = time.time()
                    
                    # 🆕 v14.6.16: 定期檢查 dYdX 是否有殘留訂單 (無持倉時自動清掃)
                    if self.config.dydx_sync_mode and self.trader.dydx_api:
                        try:
                            # 如果 Paper 端沒有持倉，檢查 dYdX 是否還有掛單
                            if not self.trader.active_trade:
                                # 檢查 dYdX WebSocket 持倉狀態
                                ws_has_pos = (hasattr(self.trader, 'dydx_ws') and 
                                              self.trader.dydx_ws and 
                                              self.trader.dydx_ws.has_position("BTC-USD"))
                                # 檢查 API 持倉狀態
                                api_has_pos = bool(self.trader.dydx_real_position and 
                                                   self.trader.dydx_real_position.get('size', 0) > 0.00001)
                                
                                # 如果 dYdX 端也沒有持倉，但還有掛單記錄，執行清掃
                                if not ws_has_pos and not api_has_pos:
                                    now_ts = time.time()
                                    last_sweep_ts = getattr(self.trader, "_last_flat_order_sweep_ts", 0.0)
                                    if (now_ts - last_sweep_ts) >= 15.0:
                                        self.trader._last_flat_order_sweep_ts = now_ts
                                        open_orders = asyncio.run(
                                            self.trader.dydx_api.get_open_orders(
                                                status=["OPEN", "UNTRIGGERED"],
                                                symbol="BTC-USD"
                                            )
                                        )
                                        cache_age = now_ts - getattr(self.trader.dydx_api, "_open_orders_cache_time", 0.0)
                                        open_orders_fresh = cache_age <= 5.0
                                        if open_orders:
                                            self.logger.info("🧹 [定期] 無持倉但有殘留掛單，執行清掃...")
                                            asyncio.run(self.trader._dydx_sweep_open_orders(
                                                reason="periodic_no_position_sweep",
                                                market="BTC-USD"
                                            ))
                                            try:
                                                asyncio.run(self.trader._log_dydx_protection_snapshot(
                                                    reason="periodic_no_position_sweep"
                                                ))
                                            except Exception:
                                                pass
                                        elif open_orders_fresh and (self.trader.pending_tp_order or self.trader.pending_sl_order or self.trader._dydx_order_registry):
                                            self.logger.info("🧹 [定期] 無持倉且交易所無掛單，清除本地追蹤...")
                                            self.trader.pending_tp_order = None
                                            self.trader.pending_sl_order = None
                                            try:
                                                self.trader._dydx_order_registry.clear()
                                            except Exception:
                                                pass
                        except Exception as e:
                            self.logger.debug(f"Periodic position check skipped: {e}")
                
                # 4. 渲染儀表板
                dashboard = self.render_dashboard()
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.write(dashboard)
                sys.stdout.flush()
                
                # 5. 日誌記錄
                if self.iteration % 10 == 0:  # 每 10 秒記錄一次
                    self.logger.info(
                        f"#{self.iteration} | "
                        f"BTC=${self._get_market_price():,.2f} | "
                        f"OBI={self.market_data.get('obi', 0):+.3f} | "
                        f"Position={'OPEN' if self.trader.active_trade else 'NONE'}"
                    )
                
                # 🆕 v13.6: 每 30 秒自動保存交易和回測數據
                if time.time() - self._last_auto_save_time >= self._auto_save_interval:
                    try:
                        # 保存交易記錄
                        self.trader._save_trades()
                        
                        # 保存回測數據 (增量保存)
                        if self.backtest_collector:
                            self.backtest_collector.save_incremental()
                        
                        self._last_auto_save_time = time.time()
                        self.logger.debug(f"💾 自動保存完成 (每 {self._auto_save_interval} 秒)")
                    except Exception as e:
                        self.logger.warning(f"自動保存失敗: {e}")
                
                # 6. 等待 1 秒
                time.sleep(self.config.ws_interval_sec)
        
        except KeyboardInterrupt:
            print("\n\n⚠️ 收到停止信號...")
        
        finally:
            # 顯示游標
            print("\033[?25h", end="")

            # 🆕 v14.6.43: 收尾清理 dYdX 殘留倉位/掛單 (避免停止後卡倉)
            if self.config.dydx_sync_mode and self.trader.dydx_sync_enabled and self.trader.dydx_api:
                try:
                    import asyncio
                    asyncio.run(self.trader.stop_dydx_trading(reason="session_end"))
                except Exception as e:
                    print(f"⚠️ dYdX 收尾清理失敗: {e}")
            
            # 🆕 v13.6: 停止回測數據收集器並保存最終數據
            if self.backtest_collector:
                try:
                    # 記錄終端機輸出摘要 (在 stop 之前)
                    self.backtest_collector.record_terminal_log(
                        f"Session ended. Total trades: {self.trader.win_count + self.trader.loss_count}",
                        level="INFO"
                    )
                    # stop() 會自動呼叫 save_full()
                    final_file = self.backtest_collector.stop()
                    print(f"\n📊 v13.6 回測數據已保存:")
                    print(f"   路徑: {final_file}")
                    print(f"   價格記錄: {len(self.backtest_collector.prices)} 筆")
                    print(f"   信號記錄: {len(self.backtest_collector.signals)} 筆")
                    print(f"   交易記錄: {len(self.backtest_collector.trades)} 筆")
                except Exception as e:
                    print(f"⚠️ 保存回測數據失敗: {e}")
            
            # 停止 WebSocket
            if not use_binance_paper:
                self.ws.stop()
            if hasattr(self, 'binance_ws'):
                self.binance_ws.stop()
            # 關閉 dYdX WebSocket / session
            if getattr(self.trader, 'dydx_ws', None):
                try:
                    self.trader.dydx_ws.stop()
                except Exception:
                    pass
            if getattr(self.trader, 'dydx_api', None):
                try:
                    # 若有 aiohttp session，確保關閉避免 Unclosed client session
                    if hasattr(self.trader.dydx_api, '_session'):
                        import asyncio
                        asyncio.run(self.trader.dydx_api._session.close())
                except Exception:
                    pass
            
            # 保存交易記錄
            self.trader._save_trades()
            
            # 保存 TensorFlow 訓練資料
            self._save_training_data()
            print(f"\n📊 TensorFlow 訓練資料已保存:")
            print(f"   記錄數: {len(self.training_records)}")
            print(f"   路徑: {self.training_file}")
            # 打印最終報告
            self._print_final_report()
    
    def _print_final_report(self):
        """打印最終報告"""
        summary = self.trader.get_summary()
        
        print(f"\n{'='*70}")
        print(f"📊 WHALE TESTNET TRADER - 最終報告")
        print(f"{'='*70}")
        
        print(f"\n💰 交易統計:")
        print(f"   總交易數: {summary.get('total_trades', 0)}")
        print(f"   獲勝: {summary.get('wins', 0)}  虧損: {summary.get('losses', 0)}")
        print(f"   勝率: {summary.get('win_rate', 0):.1%}")
        
        print(f"\n💵 盈虧統計:")
        print(f"   總盈虧: ${summary.get('total_pnl', 0):+.2f}")
        print(f"   手續費: ${summary.get('total_fees', 0):.2f}")
        print(f"   最佳: ${summary.get('best_trade', 0):+.2f}")
        print(f"   最差: ${summary.get('worst_trade', 0):+.2f}")
        
        print(f"\n⏱️ 時間統計:")
        print(f"   平均持倉: {summary.get('avg_hold_seconds', 0):.0f}秒")
        
        print(f"\n📁 交易記錄已保存至:")
        print(f"   {self.trader.trades_file}")
        
        print(f"\n{'='*70}")


# ============================================================
# 主程式
# ============================================================

def load_config_file() -> Dict:
    """載入配置檔"""
    config_path = Path("config/whale_testnet_config.json")
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


async def fetch_dydx_1m_candles(hours: float = 24.0, market: str = "BTC-USD") -> list[dict]:
    """
    從 dYdX indexer 拉取 1m candles（近 hours 小時）。
    無需錢包/權限，僅用於波動度/停損距離分析。
    """
    import aiohttp
    from datetime import datetime, timedelta, timezone

    urls = [
        f"https://indexer.dydx.trade/v4/candles/perpetualMarkets/{market}",
        f"https://indexer.v4.dydx.exchange/v4/candles/perpetualMarkets/{market}",
    ]

    end_time = datetime.now(timezone.utc)
    target_candles = int(max(1.0, hours) * 60) + 10

    candles: list[dict] = []
    url_idx = 0

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        while len(candles) < target_candles:
            url = urls[url_idx % len(urls)]
            params = {
                "resolution": "1MIN",
                "limit": 100,
                "toISO": end_time.isoformat(),
            }
            try:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        url_idx += 1
                        if url_idx >= len(urls) * 2:
                            break
                        continue
                    data = await resp.json()
                    batch = data.get("candles", []) or []
                    if not batch:
                        break
                    candles.extend(batch)
                    last_time = batch[-1].get("startedAt")
                    if not last_time:
                        break
                    end_time = datetime.fromisoformat(last_time.replace("Z", "+00:00")) - timedelta(seconds=1)
            except Exception:
                url_idx += 1
                if url_idx >= len(urls) * 2:
                    break
                continue

    return candles[:target_candles]


def analyze_1m_stop_hit_rate(
    candles: list[dict],
    *,
    leverage: float,
    maker_fee_pct: float,
    taker_fee_pct: float,
    stop_net_roe_pcts: list[float],
    assume_maker_entry: bool = True,
) -> str:
    """
    用 1m candle 的 open/high/low 估算停損距離被打到的比例：
    - LONG: 一分鐘內最大逆向幅度 = (open-low)/open
    - SHORT: 一分鐘內最大逆向幅度 = (high-open)/open

    stop_net_roe_pcts：以「淨 ROE%」表示的停損門檻（扣完手續費後）。
    """
    leverage = _coerce_float(leverage, default=50.0)
    if leverage <= 0:
        leverage = 50.0

    entry_fee_pct = maker_fee_pct if assume_maker_entry else taker_fee_pct
    sl_total_fee_pct = entry_fee_pct + taker_fee_pct  # 保守：停損出場視為 Taker

    adverse_long: list[float] = []
    adverse_short: list[float] = []
    for c in candles or []:
        try:
            o = float(c.get("open", 0))
            h = float(c.get("high", 0))
            l = float(c.get("low", 0))
        except Exception:
            continue
        if o <= 0 or h <= 0 or l <= 0:
            continue
        adverse_long.append(max(0.0, (o - l) / o * 100.0))
        adverse_short.append(max(0.0, (h - o) / o * 100.0))

    if not adverse_long:
        return "❌ 無法取得有效 1m candles（open/high/low 缺失）"

    def _q(values: list[float], q: float) -> float:
        vals = sorted(values)
        if not vals:
            return 0.0
        q = max(0.0, min(1.0, q))
        idx = int(round(q * (len(vals) - 1)))
        return vals[idx]

    lines: list[str] = []
    lines.append(f"📊 dYdX 1m 逆向波動統計 (samples={len(adverse_long)})")
    lines.append(f"   假設：進場={'Maker' if assume_maker_entry else 'Taker'} / 停損出場=Taker")
    lines.append(f"   手續費(%)：entry={entry_fee_pct:.6f} | taker={taker_fee_pct:.6f} | SL total={sl_total_fee_pct:.6f}")

    for label, series in (("LONG", adverse_long), ("SHORT", adverse_short)):
        lines.append(
            f"   {label} 逆向幅度(價格%)：p50={_q(series,0.50):.4f} | p90={_q(series,0.90):.4f} | p95={_q(series,0.95):.4f} | max={_q(series,1.00):.4f}"
        )

    lines.append("")
    for stop_net in stop_net_roe_pcts:
        stop_net = _coerce_float(stop_net, default=0.0)
        price_dist_pct = (stop_net / leverage) - sl_total_fee_pct
        if price_dist_pct <= 0:
            lines.append(
                f"🧯 停損(淨ROE)={stop_net:.2f}% → 價格距離={price_dist_pct:.6f}% (<=0：落在費用區，幾乎必打)"
            )
            continue
        long_hit = sum(1 for x in adverse_long if x >= price_dist_pct) / len(adverse_long) * 100
        short_hit = sum(1 for x in adverse_short if x >= price_dist_pct) / len(adverse_short) * 100
        lines.append(
            f"🧯 停損(淨ROE)={stop_net:.2f}% → 價格距離≈{price_dist_pct:.4f}% | 1m 命中率：LONG {long_hit:.1f}% / SHORT {short_hit:.1f}%"
        )

    p95 = max(_q(adverse_long, 0.95), _q(adverse_short, 0.95))
    implied_net = (p95 + sl_total_fee_pct) * leverage
    lines.append("")
    lines.append(f"💡 參考：若想讓 1m 逆向波動 p95 不容易打到，淨ROE停損 ≈ (p95+費用)*槓桿 = {implied_net:.2f}%")
    return "\n".join(lines)


def main():
    import argparse
    
    # 先載入配置檔
    file_config = load_config_file()
    mode_config = file_config.get('mode', {})
    
    # 預設從配置檔讀取 paper_mode (預設 False = 真實 Testnet)
    default_paper_mode = mode_config.get('paper_mode', False)
    
    parser = argparse.ArgumentParser(description='Whale Trading System v13.5 (Card System)')
    parser.add_argument('--hours', type=float, default=1.0, help='運行時間（小時）')
    parser.add_argument('--leverage', type=int, default=None, help='槓桿倍數 (覆蓋卡片設定)')
    parser.add_argument('--size', type=float, default=None, help='每筆交易金額 USDT (覆蓋卡片設定)')
    parser.add_argument('--interval', type=float, default=None, help='分析間隔秒 (覆蓋卡片設定)')
    parser.add_argument('--paper', action='store_true', default=default_paper_mode, help='模擬交易模式')
    parser.add_argument('--live', action='store_true', help='真實交易模式 (覆蓋配置檔)')
    parser.add_argument('--reverse', action='store_true', help='🆕 v7.0: 反向交易模式 (LONG↔SHORT)')
    parser.add_argument('--mtf', action='store_true', help='🆕 v8.0: MTF優先策略 (15分鐘週期)')
    parser.add_argument('--ctx', action='store_true', help='🆕 v9.0: 情境式策略 (數據驅動+智能反向)')
    parser.add_argument('--sync', action='store_true', help='🆕 dYdX 同步模式 (Paper + 真實 dYdX 交易)')
    parser.add_argument('--btc', type=float, default=None, help='dYdX 固定 BTC 倉位 (如 0.002)')
    parser.add_argument('--usdc', type=float, default=None, help='🆕 dYdX 倉位 USDC 金額 (如 100，會自動換算 BTC)')
    parser.add_argument('--report', action='store_true', help='查看報告')
    
    # 🆕 v13.5 卡片系統參數
    parser.add_argument('--card', type=str, default=None, help='🎴 使用指定卡片 (如 scalp_aggressive, trending_bull)')
    parser.add_argument('--list-cards', action='store_true', help='🎴 列出所有可用卡片')
    
    # 🆕 10U Test Arguments
    parser.add_argument('--auto-confirm', '--auto', action='store_true', help='跳過確認 (Automated)')
    parser.add_argument('--base_balance_deduct', type=float, default=25.83, help='顯示餘額扣除額 (模擬 10U)')
    parser.add_argument('--stop_on_zero_budget', action='store_true', help='🛑 10U 測試：當 (dYdX equity - base_balance_deduct) <= 0 時停止 dYdX 交易並退出')
    parser.add_argument('--no_stop_on_zero_budget', action='store_true', help='不啟用 zero budget 停止機制')
    parser.add_argument('--zero_budget_epsilon', type=float, default=0.0, help='zero budget 判斷緩衝 (預設 0.0)')

    # 🧪 分析工具：dYdX 1m candles 停損命中率
    parser.add_argument('--analyze-dydx-1m', action='store_true', help='分析近 N 小時 dYdX 1m 逆向波動，估算停損(淨ROE)命中率後退出')
    parser.add_argument('--analyze-hours', type=float, default=24.0, help='--analyze-dydx-1m 的回看小時數 (預設 24)')
    parser.add_argument('--analyze-market', type=str, default="BTC-USD", help='--analyze-dydx-1m 市場 (預設 BTC-USD)')
    parser.add_argument('--analyze-stop-net-roe', type=float, nargs='+', default=[2.35, 5.0], help='要評估的停損淨ROE%% 清單 (預設: 2.35 5.0)')
    
    args = parser.parse_args()
    
    # 🎴 列出所有卡片
    if args.list_cards:
        if CARD_MANAGER_AVAILABLE:
            manager = TradingCardManager()
            print(manager.show_cards_summary())
        else:
            print("❌ TradingCardManager 不可用")
        return
    
    if args.report:
        # 查看報告
        paper_mode = not args.live if args.live else args.paper
        config = TradingConfig(paper_mode=paper_mode)
        trader = TestnetTrader(config)
        summary = trader.get_summary()
        
        print(f"\n{'='*50}")
        print(f"📊 交易報告 ({'模擬' if paper_mode else '真實'})")
        print(f"{'='*50}")
        for k, v in summary.items():
            print(f"   {k}: {v}")
        print(f"{'='*50}\n")
        return
    
    # 🔧 dYdX sync 模式自動啟用 ctx (六維信號系統)
    use_ctx = getattr(args, 'ctx', False) or getattr(args, 'sync', False)
    
    # 🎴 v13.6: 預設從卡片系統載入配置
    # 優先順序: 命令列 --card > master_config.json 的 active_card > hardcode 預設值
    if CARD_MANAGER_AVAILABLE:
        card_manager = TradingCardManager()
        
        # 決定使用哪張卡片
        if args.card:
            # 命令列指定卡片
            target_card = args.card
        else:
            # 從 master_config.json 讀取 active_card
            target_card = card_manager.active_card_id
        
        print(f"\n🎴 載入交易卡片: {target_card}")
        config = TradingConfig.from_card(target_card, card_manager)
        
        # 覆蓋卡片設定 (命令列優先)
        if args.leverage is not None:
            config.leverage = args.leverage
            # 🔧 覆蓋動態槓桿範圍，避免 leverage_min/max 仍鎖在卡片值
            config.leverage_min = args.leverage
            config.leverage_max = args.leverage
        if args.size is not None:
            config.position_size_usdt = args.size
        if args.interval is not None:
            config.analysis_interval_sec = args.interval
    else:
        # Fallback: 卡片系統不可用時使用 hardcode
        print(f"\n⚠️ 卡片系統不可用，使用預設配置")
        config = TradingConfig(
            leverage=args.leverage or 50,
            position_size_usdt=args.size or 100.0,
            analysis_interval_sec=args.interval or 3.0,
        )
    
    # 覆蓋模式設定 (命令列優先，其次尊重卡片設定)
    if args.live:
        paper_mode = False
    elif args.paper != default_paper_mode:
        paper_mode = args.paper
    else:
        paper_mode = config.paper_mode if config.paper_mode is not None else default_paper_mode
    config.paper_mode = paper_mode

    print(f"\n📋 模式: {'PAPER 模擬' if paper_mode else 'TESTNET 真實交易'}")
    print(f"   (配置檔: paper_mode={default_paper_mode}, 命令列: --live={args.live})")
    config.reverse_mode = args.reverse
    config.mtf_first_mode = getattr(args, 'mtf', False)
    # 🔧 v13.6.2: 只有明確指定 --ctx 或 --sync 時才覆蓋，否則尊重卡片設定
    if getattr(args, 'ctx', False) or getattr(args, 'sync', False):
        config.contextual_mode = True
    # 如果卡片沒設定 contextual_mode，預設為 True (六維系統)
    if config.contextual_mode is None:
        config.contextual_mode = True
    config.dydx_sync_mode = getattr(args, 'sync', False)
    
    # 🆕 v14.9.4: 支援 --usdc 參數，自動換算 BTC 數量
    if getattr(args, 'usdc', None):
        # 用戶指定 USDC 金額，需要根據當前價格換算 BTC
        # 先取得即時價格
        try:
            import requests
            resp = requests.get('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT', timeout=5)
            btc_price = float(resp.json()['price'])
            usdc_amount = args.usdc
            btc_size = usdc_amount / btc_price
            # 取整到 4 位小數 (dYdX 支援 0.0001)
            import math
            btc_size = math.floor(btc_size * 10000) / 10000
            # 確保至少 $100 名義價值
            if btc_size * btc_price < 100:
                btc_size = math.ceil(100 / btc_price * 10000) / 10000
            config.dydx_btc_size = btc_size
            config.dydx_usdc_size = usdc_amount  # 保存原始 USDC 金額
            print(f"💰 --usdc {usdc_amount} → {btc_size:.4f} BTC (@ ${btc_price:,.0f})")
        except Exception as e:
            print(f"⚠️ 無法取得 BTC 價格，使用預設 0.01 BTC: {e}")
            config.dydx_btc_size = 0.01
    elif getattr(args, 'btc', None):
        config.dydx_btc_size = args.btc
    else:
        config.dydx_btc_size = 0.01  # 預設值
    
    config.pre_entry_mode = True  # 🔧 強制啟用 Maker Mode
    if config.dydx_sync_mode:
        # dYdX Sync 預設強制 Maker (Post-only) 以避免 Taker 手續費放大成 ROE 地獄
        config.use_maker_simulation = True
    config.auto_confirm = args.auto_confirm
    config.base_balance_deduct = args.base_balance_deduct

    # 🛑 10U 測試：自動停止 (預設：若你自訂 base_balance_deduct 且啟用 --sync，則自動開啟；可用 --no_stop_on_zero_budget 關閉)
    if args.no_stop_on_zero_budget:
        config.zero_budget_stop_enabled = False
    elif args.stop_on_zero_budget:
        config.zero_budget_stop_enabled = True
    else:
        base_deduct_is_custom = (args.base_balance_deduct is not None and args.base_balance_deduct != 25.83)
        config.zero_budget_stop_enabled = bool(config.dydx_sync_mode and base_deduct_is_custom)
    config.zero_budget_stop_epsilon_usdt = _coerce_float(getattr(args, 'zero_budget_epsilon', 0.0), default=0.0)

    # 🧪 分析模式：dYdX 1m 停損命中率（不進入交易迴圈）
    if getattr(args, 'analyze_dydx_1m', False):
        try:
            lev = _coerce_float(getattr(config, 'leverage', 50), default=50.0)
            maker_fee = _coerce_float(getattr(config, 'maker_fee_pct', 0.005), default=0.005)
            taker_fee = _coerce_float(getattr(config, 'taker_fee_pct', 0.04), default=0.04)
            assume_maker = bool(getattr(config, 'use_maker_simulation', False) or getattr(args, 'sync', False))
            candles = asyncio.run(fetch_dydx_1m_candles(hours=getattr(args, 'analyze_hours', 24.0), market=getattr(args, 'analyze_market', "BTC-USD")))
            report = analyze_1m_stop_hit_rate(
                candles,
                leverage=lev,
                maker_fee_pct=maker_fee,
                taker_fee_pct=taker_fee,
                stop_net_roe_pcts=list(getattr(args, 'analyze_stop_net_roe', [2.35, 5.0])),
                assume_maker_entry=assume_maker,
            )
            print("\n" + report + "\n")
        except Exception as e:
            print(f"❌ 分析失敗: {e}")
        return
    
    if args.reverse:
        print(f"\n🔄 反向交易模式已啟用!")
        print(f"   信號 LONG → 執行 SHORT")
        print(f"   信號 SHORT → 執行 LONG")
    
    if getattr(args, 'mtf', False):
        print(f"\n📊 v8.0 MTF-First 策略已啟用!")
        print(f"   📈 以 15 分鐘時間框架趨勢為主")
        print(f"   ⏱️ 固定持倉 15 分鐘")
        print(f"   🎯 目標: +0.25%  止損: -0.12%")
        print(f"   🚨 突發事件自動停止交易")
    
    if getattr(args, 'ctx', False):
        print(f"\n🎯 v9.0 情境式策略已啟用!")
        print(f"   📊 基於 91 筆歷史交易數據分析")
        print(f"   🔄 智能反向: 凌晨01-04時自動反向操作")
        print(f"   ✅ 最佳條件: 低機率+做空(100%), OBI高+穩定(80%)")
        print(f"   ❌ 避開條件: OBI低+高機率(25%), 追空(33%)")
        print(f"   ⏰ 好時段: 00,04,10-12,15,22時 (勝率73%)")
    
    # 🆕 dYdX 同步模式確認
    if getattr(args, 'sync', False):
        print(f"\n{'='*60}")
        print(f"🔴 dYdX 同步真實交易模式!")
        print(f"   這是真實資金交易，請確認:")
        print(f"   • BTC 倉位: {args.btc} BTC")
        print(f"   • 策略: Aggressive Maker (Post-only)")
        print(f"   • 手續費: Maker {config.maker_fee_pct}% | Taker {config.taker_fee_pct}%")
        print(f"   • 最小持倉: 15 秒 (等待 dYdX 成交)")
        print(f"")
        
        if not args.auto_confirm:
            confirm = input("   輸入 'yes' 確認開始 dYdX 真實交易: ")
            if confirm.lower() != 'yes':
                print("已取消")
                return
        else:
            print("   ✅ 自動確認: 已跳過輸入")
            
        print(f"{'='*60}\n")
    
    # 🔧 v14.9.3: Binance Testnet 確認 (只在非 paper 且非 dYdX sync 模式時顯示)
    if not paper_mode and not getattr(args, 'sync', False):
        print(f"\n{'='*60}")
        print(f"🧪 TESTNET 模式 - 使用 Binance Futures Testnet")
        print(f"   (這不是真實交易，使用的是測試網虛擬資金)")
        print(f"")
        print(f"   槓桿: {args.leverage}X")
        print(f"   每筆: ${args.size} (Testnet 虛擬資金)")
        print(f"")
        
        if not args.auto_confirm:
            confirm = input("   輸入 'YES' 確認開始 Testnet 交易: ")
            if confirm != 'YES':
                print("已取消")
                return
        else:
            print("   ✅ 自動確認: 已跳過輸入")

        print(f"{'='*60}\n")
    
    system = WhaleTestnetSystem(config)
    system.run(hours=args.hours)


if __name__ == "__main__":
    main()
