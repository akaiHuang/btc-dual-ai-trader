"""
Multi-Mode Hybrid Strategy - 多檔位混合策略
==============================================

支援動態切換不同風險檔位（M0-M5），並可被 LLM 實時調整參數

檔位設計：
- M0 (Ultra Safe): 超保守，只在最漂亮型態出手 (0.1~0.5筆/天)
- M1 (Safe): 保守，高信心度才進場 (0.5~1筆/天)  
- M2 (Normal): 標準，有優勢就上 (3~10筆/天) ← Paper Trading 建議
- M3 (Aggressive): 積極，略有優勢就掃 (10~20筆/天)
- M4 (Very Aggressive): 很激進，捕捉更多機會 (20~30筆/天)
- M5 (Ultra Aggressive): 超激進，壓力測試用 (30+筆/天)

動態調整機制：
1. 實時監控交易表現
2. 根據市況自動調整檔位
3. 支援 LLM API 介入決策
4. 參數熱更新（不需重啟）
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List
from enum import Enum
import json
from datetime import datetime
from pathlib import Path
import sys

# 添加項目根目錄
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.strategy.hybrid_funding_technical import (
    HybridFundingTechnicalStrategy,
    HybridSignal,
    SignalType
)


class TradingMode(Enum):
    """交易模式檔位"""
    M0_ULTRA_SAFE = "M0_ULTRA_SAFE"           # 0.1~0.5筆/天
    M1_SAFE = "M1_SAFE"                       # 0.5~1筆/天
    M2_NORMAL = "M2_NORMAL"                   # 3~10筆/天 (推薦)
    M3_AGGRESSIVE = "M3_AGGRESSIVE"           # 10~20筆/天
    M4_VERY_AGGRESSIVE = "M4_VERY_AGGRESSIVE" # 20~30筆/天
    M5_ULTRA_AGGRESSIVE = "M5_ULTRA_AGGRESSIVE" # 30+筆/天
    M6_SIGNAL_SANDBOX = "M6_SIGNAL_SANDBOX"     # legacy+micro 診斷模式
    # 2025-11 Prime personas（新一代狙擊模式，舊版保留作為 Legacy）
    M1_SAFE_PRIME = "M1_SAFE_PRIME"
    M2_NORMAL_PRIME = "M2_NORMAL_PRIME"
    M_FISH_MARKET_MAKER = "M_FISH_MARKET_MAKER"
    # 2025-11 專業狙擊手系列 - 特殊戰術
    M7_BREAKOUT_SNIPER = "M7_BREAKOUT_SNIPER"       # 突破狙擊
    M8_VOLUME_SNIPER = "M8_VOLUME_SNIPER"           # 量能狙擊
    M9_VOLATILITY_SNIPER = "M9_VOLATILITY_SNIPER"   # 波動狙擊
    M_WHALE_WATCHER = "M_WHALE_WATCHER"             # 🐳 大單跟單模式
    M_LP_WHALE_BURST = "M_LP_WHALE_BURST"           # 🥊 爆倉壓力 + 鯨魚爆擊
    MUP_DIRECTIONAL_LONG = "MUP_DIRECTIONAL_LONG"    # 🟢 自動做多偏向觀察
    MDOWN_DIRECTIONAL_SHORT = "MDOWN_DIRECTIONAL_SHORT"  # 🔴 自動做空偏向觀察
    M_AI_WHALE_HUNTER = "M_AI_WHALE_HUNTER"         # 🐺 AI 主力獵人
    M_INVERSE_WOLF = "M_INVERSE_WOLF"               # 🐺🔄 反向 Wolf (跟 M🐺 對著幹)
    M_DRAGON = "M_DRAGON"                   # 🐲 AI Dragon (Kimi-k2)
    M_DRAGON2 = "M_DRAGON2"                 # 🐲2 AI Dragon V2 (改良版: 加入鯨魚過濾, 縮短持倉)
    # 2025-11-26 新策略：優化持倉時間與止盈止損
    M_SHRIMP = "M_SHRIMP"                           # 🦐 Shrimp: 最小持倉 2 分鐘, TP=5%, SL=2%
    M_BIRD = "M_BIRD"                               # 🐦 Bird: 反向 Shrimp
    # 🦁 2025-01 Lion: v2.0 Whale Strategy Detector Enhanced
    M_LION = "M_LION"                               # 🦁 AI Lion (GPT + v2.0 Whale Strategy)


@dataclass
class ModeConfig:
    """檔位配置"""
    # 基礎信號門檻
    funding_zscore_threshold: float
    signal_score_threshold: float
    
    # RSI 參數
    rsi_oversold: float
    rsi_overbought: float
    
    # 成交量門檻
    volume_spike_threshold: float
    
    # 風控參數
    leverage: int
    tp_pct: float  # 現貨百分比
    sl_pct: float  # 現貨百分比
    
    # 成本過濾（單位：現貨%）
    min_move_threshold: float  # 最小預期波動（相對手續費倍數）
    
    # 時間控制
    cooldown_minutes: int  # 交易冷卻時間
    time_stop_hours: int   # 持倉時間限制
    
    # 盤整過濾
    min_atr_pct: float  # 最小 ATR 百分比（過濾盤整）
    
    # 描述
    description: str
    target_frequency: str
    
    # 特殊功能
    invert_signal: bool = False  # 是否反轉信號（用於反指標模式）


class MultiModeHybridStrategy:
    """
    多檔位混合策略
    
    核心功能：
    1. 動態切換交易模式（M0-M5）
    2. 實時參數調整
    3. 性能監控與自動優化
    4. LLM API 介入決策
    """
    
    # 預設檔位配置
    MODE_CONFIGS = {
        TradingMode.M0_ULTRA_SAFE: ModeConfig(
            funding_zscore_threshold=3.0,
            signal_score_threshold=0.7,
            rsi_oversold=25,
            rsi_overbought=75,
            volume_spike_threshold=3.0,
            leverage=60,
            tp_pct=0.020,  # 2.0% 現貨
            sl_pct=0.012,  # 1.2% 現貨
            min_move_threshold=4.0,  # 手續費 × 4
            cooldown_minutes=60,
            time_stop_hours=24,
            min_atr_pct=0.005,  # 0.5%
            description="超保守：只在最漂亮型態出手",
            target_frequency="0.1~0.5筆/天"
        ),
        
        TradingMode.M1_SAFE: ModeConfig(
            funding_zscore_threshold=2.5,
            signal_score_threshold=0.6,
            rsi_oversold=28,
            rsi_overbought=72,
            volume_spike_threshold=2.5,
            leverage=65,
            tp_pct=0.018,  # 1.8% 現貨
            sl_pct=0.011,  # 1.1% 現貨
            min_move_threshold=3.5,
            cooldown_minutes=45,
            time_stop_hours=18,
            min_atr_pct=0.004,
            description="保守：高信心度才進場",
            target_frequency="0.5~1筆/天"
        ),
        
        TradingMode.M2_NORMAL: ModeConfig(
            funding_zscore_threshold=2.0,
            signal_score_threshold=0.5,
            rsi_oversold=30,
            rsi_overbought=70,
            volume_spike_threshold=2.0,
            leverage=70,
            tp_pct=0.015,  # 1.5% 現貨
            sl_pct=0.010,  # 1.0% 現貨
            min_move_threshold=2.5,
            cooldown_minutes=30,
            time_stop_hours=12,
            min_atr_pct=0.003,
            description="標準：有優勢就上，不要求完美",
            target_frequency="3~10筆/天"
        ),
        
        TradingMode.M3_AGGRESSIVE: ModeConfig(
            funding_zscore_threshold=1.5,
            signal_score_threshold=0.4,
            rsi_oversold=35,
            rsi_overbought=65,
            volume_spike_threshold=1.8,
            leverage=80,
            tp_pct=0.010,  # 1.0% 現貨
            sl_pct=0.008,  # 0.8% 現貨
            min_move_threshold=2.0,
            cooldown_minutes=15,
            time_stop_hours=8,
            min_atr_pct=0.002,
            description="積極：略有優勢就掃",
            target_frequency="10~20筆/天"
        ),

        TradingMode.MUP_DIRECTIONAL_LONG: ModeConfig(
            funding_zscore_threshold=0.0,
            signal_score_threshold=0.0,
            rsi_oversold=15,
            rsi_overbought=85,
            volume_spike_threshold=0.0,
            leverage=60,
            tp_pct=0.012,   # 1.2% 現貨
            sl_pct=0.009,   # 0.9% 現貨
            min_move_threshold=0.0,
            cooldown_minutes=1,
            time_stop_hours=8,
            min_atr_pct=0.0,
            description="Mup Direction Bias - 自動做多偵測區間偏向",
            target_frequency="Always-on (區間偏向)"
        ),

        TradingMode.MDOWN_DIRECTIONAL_SHORT: ModeConfig(
            funding_zscore_threshold=0.0,
            signal_score_threshold=0.0,
            rsi_oversold=15,
            rsi_overbought=85,
            volume_spike_threshold=0.0,
            leverage=60,
            tp_pct=0.012,
            sl_pct=0.009,
            min_move_threshold=0.0,
            cooldown_minutes=1,
            time_stop_hours=8,
            min_atr_pct=0.0,
            description="Mdown Direction Bias - 自動做空偵測區間偏向",
            target_frequency="Always-on (區間偏向)"
        ),
        
        TradingMode.M4_VERY_AGGRESSIVE: ModeConfig(
            funding_zscore_threshold=1.2,
            signal_score_threshold=0.35,
            rsi_oversold=40,
            rsi_overbought=60,
            volume_spike_threshold=1.5,
            leverage=85,
            tp_pct=0.008,  # 0.8% 現貨
            sl_pct=0.007,  # 0.7% 現貨
            min_move_threshold=1.5,
            cooldown_minutes=10,
            time_stop_hours=6,
            min_atr_pct=0.0015,
            description="很激進：捕捉更多機會",
            target_frequency="20~30筆/天"
        ),
        
        TradingMode.M5_ULTRA_AGGRESSIVE: ModeConfig(
            funding_zscore_threshold=1.0,
            signal_score_threshold=0.3,
            rsi_oversold=45,
            rsi_overbought=55,
            volume_spike_threshold=1.3,
            leverage=90,
            tp_pct=0.005,  # 0.5% 現貨
            sl_pct=0.006,  # 0.6% 現貨
            min_move_threshold=1.2,
            cooldown_minutes=5,
            time_stop_hours=4,
            min_atr_pct=0.001,
            description="超激進：壓力測試、找Bug",
            target_frequency="30+筆/天"
        ),

        TradingMode.M6_SIGNAL_SANDBOX: ModeConfig(
            funding_zscore_threshold=2.0,
            signal_score_threshold=0.48,
            rsi_oversold=32,
            rsi_overbought=68,
            volume_spike_threshold=1.5,
            leverage=65,
            tp_pct=0.016,  # 1.6% 現貨
            sl_pct=0.008,  # 0.8% 現貨
            min_move_threshold=2.5,
            cooldown_minutes=60,
            time_stop_hours=8,
            min_atr_pct=0.003,
            description="診斷模式：legacy+micro 訊號交叉驗證（降風險版）",
            target_frequency="診斷測試專用"
        ),

        # --- 2025 Prime personas：用於實驗新狙擊策略 ---
        TradingMode.M1_SAFE_PRIME: ModeConfig(
            funding_zscore_threshold=2.3,
            signal_score_threshold=0.55,
            rsi_oversold=27,
            rsi_overbought=73,
            volume_spike_threshold=2.3,
            leverage=70,
            tp_pct=0.018,
            sl_pct=0.010,
            min_move_threshold=3.0,
            cooldown_minutes=35,
            time_stop_hours=20,
            min_atr_pct=0.0045,
            description="M1′ Trend Sniper：追著趨勢結構射擊",
            target_frequency="1~3筆/天"
        ),

        TradingMode.M2_NORMAL_PRIME: ModeConfig(
            funding_zscore_threshold=2.0,
            signal_score_threshold=0.50,
            rsi_oversold=30,
            rsi_overbought=70,
            volume_spike_threshold=2.0,
            leverage=65,
            tp_pct=0.019,
            sl_pct=0.007,
            min_move_threshold=2.8,
            cooldown_minutes=90,
            time_stop_hours=8,
            min_atr_pct=0.003,
            description="M2′ Scalper Sniper：捕捉微結構脈衝（保守版）",
            target_frequency="3~8筆/天"
        ),

        TradingMode.M_FISH_MARKET_MAKER: ModeConfig(
            funding_zscore_threshold=1.3,
            signal_score_threshold=0.4,
            rsi_oversold=38,
            rsi_overbought=62,
            volume_spike_threshold=1.6,
            leverage=75,
            tp_pct=0.010,
            sl_pct=0.012,
            min_move_threshold=1.6,
            cooldown_minutes=15,
            time_stop_hours=6,
            min_atr_pct=0.002,
            description="M🐟 Fish Market Maker：死魚盤網格造市",
            target_frequency="8~18筆/天"
        ),

        # --- 專業狙擊手系列：特定戰術優化 ---
        TradingMode.M7_BREAKOUT_SNIPER: ModeConfig(
            funding_zscore_threshold=1.8,
            signal_score_threshold=0.52,
            rsi_oversold=32,
            rsi_overbought=68,
            volume_spike_threshold=2.2,  # 突破必須伴隨量能
            leverage=75,
            tp_pct=0.025,  # 2.5% 現貨（突破往往走更遠）
            sl_pct=0.008,  # 0.8% 現貨（突破失敗快撤）
            min_move_threshold=2.5,
            cooldown_minutes=45,
            time_stop_hours=6,  # 突破不成立則快速平倉
            min_atr_pct=0.004,  # 需要足夠波動才有突破意義
            description="M7 Breakout Sniper：專捕關鍵位突破，高 TP 搏大波段",
            target_frequency="2~5筆/天"
        ),

        TradingMode.M8_VOLUME_SNIPER: ModeConfig(
            funding_zscore_threshold=1.5,
            signal_score_threshold=0.45,
            rsi_oversold=35,
            rsi_overbought=65,
            volume_spike_threshold=3.5,  # 必須是極端量能異常
            leverage=80,
            tp_pct=0.018,  # 1.8% 現貨（跟隨機構快進快出）
            sl_pct=0.009,  # 0.9% 現貨
            min_move_threshold=2.2,
            cooldown_minutes=30,
            time_stop_hours=4,  # 量能效應短暫
            min_atr_pct=0.003,
            description="M8 Volume Sniper：偵測異常量能湧入，跟隨巨鯨足跡",
            target_frequency="3~7筆/天"
        ),

        TradingMode.M9_VOLATILITY_SNIPER: ModeConfig(
            funding_zscore_threshold=1.5,
            signal_score_threshold=0.6,
            rsi_oversold=30,
            rsi_overbought=70,
            volume_spike_threshold=2.0,
            leverage=95,
            tp_pct=2.0,
            sl_pct=1.0,
            min_move_threshold=0.002,
            cooldown_minutes=15,
            time_stop_hours=6,
            min_atr_pct=0.003,
            description="Volatility Sniper - High ATR breakout",
            target_frequency="5-15/day"
        ),

        TradingMode.M_WHALE_WATCHER: ModeConfig(
            funding_zscore_threshold=0.1,
            signal_score_threshold=0.1,
            rsi_oversold=20,
            rsi_overbought=80,
            volume_spike_threshold=1.0,
            leverage=70,
            tp_pct=3.0,
            sl_pct=1.5,
            min_move_threshold=0.001,
            cooldown_minutes=5,
            time_stop_hours=12,
            min_atr_pct=0.001,
            description="Whale Watcher - Follow large trade net direction",
            target_frequency="Variable"
        ),

        TradingMode.M_LP_WHALE_BURST: ModeConfig(
            funding_zscore_threshold=1.5,
            signal_score_threshold=0.6,
            rsi_oversold=30,
            rsi_overbought=70,
            volume_spike_threshold=2.0,
            leverage=75,
            tp_pct=3.0,
            sl_pct=1.3,
            min_move_threshold=0.002,
            cooldown_minutes=60,
            time_stop_hours=8,
            min_atr_pct=0.003,
            description="M🥊 Liquidation + Whale Burst：爆倉壓力結合鯨魚爆擊模式",
            target_frequency="1~5筆/天"
        ),

        TradingMode.M_AI_WHALE_HUNTER: ModeConfig(
            funding_zscore_threshold=0.0,
            signal_score_threshold=0.0,
            rsi_oversold=0,
            rsi_overbought=100,
            volume_spike_threshold=0.0,
            leverage=100,
            tp_pct=5.0,  # 🔧 加大止盈：2.5% -> 5% (扣手續費後仍有空間)
            sl_pct=3.0,  # 🔧 加大止損：1.5% -> 3% (避免被噪音震出)
            min_move_threshold=0.001,
            cooldown_minutes=5,
            time_stop_hours=24,
            min_atr_pct=0.0,
            description="M🐺 AI Whale Hunter - Controlled by GPT-4o-mini",
            target_frequency="AI Driven"
        ),

        TradingMode.M_INVERSE_WOLF: ModeConfig(
            funding_zscore_threshold=0.0,
            signal_score_threshold=0.0,
            rsi_oversold=0,
            rsi_overbought=100,
            volume_spike_threshold=0.0,
            leverage=50,  # 降低槓桿，避免像 M🐺 一樣秒爆
            tp_pct=2.5,
            sl_pct=1.5,
            min_move_threshold=0.001,
            cooldown_minutes=5,
            time_stop_hours=24,
            min_atr_pct=0.0,
            description="M🐺🔄 Inverse Wolf - Do the opposite of Wolf",
            target_frequency="AI Driven (Inverted)",
            invert_signal=True
        ),

        TradingMode.M_DRAGON: ModeConfig(
            funding_zscore_threshold=0.0,
            signal_score_threshold=0.0,
            rsi_oversold=0,
            rsi_overbought=100,
            volume_spike_threshold=0.0,
            leverage=100,
            tp_pct=5.0,  # 🔧 加大止盈：2.5% -> 5% (扣手續費後仍有空間)
            sl_pct=3.0,  # 🔧 加大止損：1.5% -> 3% (避免被噪音震出)
            min_move_threshold=0.001,
            cooldown_minutes=5,
            time_stop_hours=24,
            min_atr_pct=0.0,
            description="M🐲 AI Dragon - Controlled by Kimi-k2",
            target_frequency="AI Driven"
        ),

        # 🐲2 Dragon V2: 改良版 - 解決原版三大問題
        TradingMode.M_DRAGON2: ModeConfig(
            funding_zscore_threshold=0.0,
            signal_score_threshold=0.0,
            rsi_oversold=0,
            rsi_overbought=100,
            volume_spike_threshold=0.0,
            leverage=50,   # 🔧 降低槓桿: 100x -> 50x (更安全)
            tp_pct=3.0,    # 🔧 降低止盈目標: 5% -> 3% (更容易觸發)
            sl_pct=2.0,    # 🔧 收緊止損: 3% -> 2%
            min_move_threshold=0.001,
            cooldown_minutes=2,  # 🔧 縮短冷卻: 5min -> 2min (更頻繁交易)
            time_stop_hours=24,
            min_atr_pct=0.0,
            description="M🐲2 Dragon V2 - 改良版: 鯨魚過濾+縮短持倉+降低止盈",
            target_frequency="AI Driven (Improved)"
        ),

        # 🦐🐦 2025-11-26 新策略：最佳化 V3
        # 🔧 2025-11-26 V3 最佳化：平衡手續費與達成率
        # 25x槓桿(手續費2.5%) + 5%TP(淨2.5%) + 2%SL(淨4.5%)
        # 關鍵: 0.2% 價格變動有 88% 達成率!
        # 預期: 88×2.5 - 12×4.5 = +166% ROI
        TradingMode.M_SHRIMP: ModeConfig(
            funding_zscore_threshold=0.0,
            signal_score_threshold=0.0,
            rsi_oversold=0,
            rsi_overbought=100,
            volume_spike_threshold=0.0,
            leverage=25,   # 🔧 25x 槓桿 (手續費 2.5%)
            tp_pct=5.0,    # 🔧 5% 止盈 (需 0.2% 價格變動, 88% 達成率)
            sl_pct=2.0,    # 🔧 2% 止損 (淨虧損 4.5%)
            min_move_threshold=0.001,
            cooldown_minutes=2,  # 🔧 2 分鐘冷卻 (5% TP 更快)
            time_stop_hours=24,
            min_atr_pct=0.0,
            description="M🦐 AI Shrimp (GPT) - 25x槓桿, 5%TP/2%SL, 88%達成率, 預期+166%",
            target_frequency="AI Driven (Optimized V3)"
        ),

        TradingMode.M_BIRD: ModeConfig(
            funding_zscore_threshold=0.0,
            signal_score_threshold=0.0,
            rsi_oversold=0,
            rsi_overbought=100,
            volume_spike_threshold=0.0,
            leverage=25,   # 🔧 25x 槓桿 (手續費 2.5%)
            tp_pct=5.0,    # 🔧 5% 止盈 (需 0.2% 價格變動, 88% 達成率)
            sl_pct=2.0,    # 🔧 2% 止損 (淨虧損 4.5%)
            min_move_threshold=0.001,
            cooldown_minutes=2,  # 🔧 2 分鐘冷卻
            time_stop_hours=24,
            min_atr_pct=0.0,
            description="M🐦 AI Bird (Kimi) - 25x槓桿, 5%TP/2%SL, 88%達成率, 預期+166%",
            target_frequency="AI Driven (Optimized V3)"
        ),

        # 🦁 M_LION: v2.0 Whale Strategy Detector Enhanced
        # 結合 GPT AI + v2.0 鯨魚策略檢測器
        TradingMode.M_LION: ModeConfig(
            funding_zscore_threshold=0.0,
            signal_score_threshold=0.0,
            rsi_oversold=0,
            rsi_overbought=100,
            volume_spike_threshold=0.0,
            leverage=50,   # 🦁 50x 槓桿 (與 Dragon 相同)
            tp_pct=3.0,    # 🦁 3% 止盈
            sl_pct=2.0,    # 🦁 2% 止損
            min_move_threshold=0.001,
            cooldown_minutes=2,
            time_stop_hours=24,
            min_atr_pct=0.0,
            description="M🦁 AI Lion - GPT + v2.0 Whale Strategy Detector Enhanced",
            target_frequency="AI Driven (v2.0 Enhanced)"
        ),
    }
    
    def __init__(
        self,
        initial_mode: TradingMode = TradingMode.M2_NORMAL,
        enable_llm_advisor: bool = False,
        llm_api_key: Optional[str] = None,
        config_file: Optional[str] = None
    ):
        """
        初始化多檔位策略
        
        Args:
            initial_mode: 初始交易模式
            enable_llm_advisor: 是否啟用 LLM 顧問
            llm_api_key: LLM API 金鑰
            config_file: 自定義配置文件路徑
        """
        self.current_mode = initial_mode
        self.enable_llm_advisor = enable_llm_advisor
        self.llm_api_key = llm_api_key
        
        # 載入配置
        if config_file and Path(config_file).exists():
            self._load_custom_config(config_file)
        else:
            self.mode_configs = self.MODE_CONFIGS.copy()
        
        # 創建基礎策略實例
        self._update_base_strategy()
        
        # 性能追蹤
        self.performance_tracker = {
            'total_signals': 0,
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': 0.0,
            'mode_history': [],
            'last_trade_time': None,
        }
        
        # LLM 建議歷史
        self.llm_suggestions = []
        
    def _update_base_strategy(self):
        """根據當前模式更新基礎策略"""
        config = self.mode_configs[self.current_mode]
        
        self.base_strategy = HybridFundingTechnicalStrategy(
            funding_zscore_threshold=config.funding_zscore_threshold,
            signal_score_threshold=config.signal_score_threshold,
            rsi_oversold=config.rsi_oversold,
            rsi_overbought=config.rsi_overbought,
            volume_spike_threshold=config.volume_spike_threshold,
        )
        
    def switch_mode(self, new_mode: TradingMode, reason: str = "Manual"):
        """
        切換交易模式
        
        Args:
            new_mode: 新模式
            reason: 切換原因
        """
        old_mode = self.current_mode
        self.current_mode = new_mode
        self._update_base_strategy()
        
        # 記錄切換
        self.performance_tracker['mode_history'].append({
            'timestamp': datetime.now().isoformat(),
            'from_mode': old_mode.value,
            'to_mode': new_mode.value,
            'reason': reason
        })
        
        print(f"🔄 Mode switched: {old_mode.value} → {new_mode.value}")
        print(f"   Reason: {reason}")
        print(f"   New config: {self.get_current_config()}")
    
    def get_current_config(self) -> ModeConfig:
        """獲取當前檔位配置"""
        return self.mode_configs[self.current_mode]
    
    def update_config(
        self, 
        mode: TradingMode,
        **kwargs
    ):
        """
        動態更新特定模式的配置
        
        Args:
            mode: 要更新的模式
            **kwargs: 要更新的參數
        """
        config = self.mode_configs[mode]
        
        # 更新配置
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)
                print(f"✅ Updated {mode.value}.{key} = {value}")
            else:
                print(f"⚠️ Unknown parameter: {key}")
        
        # 如果更新的是當前模式，重新初始化策略
        if mode == self.current_mode:
            self._update_base_strategy()
    
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """計算 ATR (Average True Range)"""
        if len(df) < period:
            return 0.0
        
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        
        return atr / close.iloc[-1]  # 返回百分比
    
    def check_cost_filter(
        self, 
        signal: HybridSignal,
        current_price: float,
        config: ModeConfig
    ) -> bool:
        """
        成本過濾：檢查預期波動是否足以覆蓋成本
        
        假設：
        - 手續費 = 0.05% × 2 (開倉+平倉) = 0.1%
        - 滑點 = 0.02%
        - 總成本 = 0.12%
        """
        total_cost_pct = 0.0012  # 0.12%
        
        # 預期波動 = TP 或 SL（取較小者）
        expected_move_pct = min(config.tp_pct, config.sl_pct)
        
        # 檢查是否足夠
        required_move = total_cost_pct * config.min_move_threshold
        
        if expected_move_pct < required_move:
            return False
        
        return True
    
    def check_cooldown(self, last_trade_time: Optional[datetime]) -> bool:
        """檢查冷卻時間"""
        if last_trade_time is None:
            return True
        
        config = self.get_current_config()
        elapsed = (datetime.now() - last_trade_time).total_seconds() / 60
        
        return elapsed >= config.cooldown_minutes
    
    def _generate_ai_whale_signal(self, df: pd.DataFrame, config: ModeConfig) -> Dict[str, Any]:
        """
        生成 AI 主力獵人信號
        讀取 ai_advisor_state.json 的建議
        """
        state_file = Path("ai_advisor_state.json")
        if not state_file.exists():
            return {
                'signal': SignalType.NEUTRAL,
                'reason': 'AI state file not found',
                'mode': self.current_mode.value,
                'config': asdict(config)
            }
            
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
                
            action = state.get('action', 'WAIT')
            confidence = state.get('confidence', 0)
            prediction_time = state.get('prediction_time')
            
            # 檢查信號時效性 (例如 15 分鐘內有效)
            if prediction_time:
                pred_dt = datetime.fromisoformat(prediction_time)
                if (datetime.now() - pred_dt).total_seconds() > 900: # 15 mins
                    return {
                        'signal': SignalType.NEUTRAL,
                        'reason': 'AI signal expired',
                        'mode': self.current_mode.value,
                        'config': asdict(config)
                    }
            
            min_confidence = config.entry_rules.get('ai_confidence_min', 70) if hasattr(config, 'entry_rules') else 70
            
            if confidence < min_confidence:
                return {
                    'signal': SignalType.NEUTRAL,
                    'reason': f'Low AI confidence ({confidence} < {min_confidence})',
                    'mode': self.current_mode.value,
                    'config': asdict(config)
                }
                
            signal_type = SignalType.NEUTRAL
            if action == 'LONG':
                signal_type = SignalType.LONG
            elif action == 'SHORT':
                signal_type = SignalType.SHORT
            
            # ═══════════════════════════════════════════════════════════════════
            # 🆕 v10.7 防追單檢查 (Anti-Chase Filter)
            # ═══════════════════════════════════════════════════════════════════
            try:
                config_path = Path('config/whale_ctx_strategy.json')
                if config_path.exists():
                    with open(config_path, 'r') as f:
                        whale_ctx_cfg = json.load(f)
                        anti_chase_cfg = whale_ctx_cfg.get('whale_strategy', {}).get('stability_filter', {}).get('anti_chase', {})
                        
                        if anti_chase_cfg.get('enabled', True):
                            max_price_move_1m_pct = anti_chase_cfg.get('max_price_move_1m_pct', 0.3)
                            
                            # 計算 1 分鐘價格變化 (假設 df 是 1m K線)
                            if len(df) >= 2:
                                current_close = df['close'].iloc[-1]
                                prev_close = df['close'].iloc[-2]
                                price_change_1m = (current_close - prev_close) / prev_close * 100
                                
                                if action == 'LONG' and price_change_1m > max_price_move_1m_pct:
                                    return {
                                        'signal': SignalType.NEUTRAL,
                                        'reason': f'Anti-Chase: Price up {price_change_1m:.2f}% > {max_price_move_1m_pct}%',
                                        'mode': self.current_mode.value,
                                        'config': asdict(config)
                                    }
                                
                                if action == 'SHORT' and price_change_1m < -max_price_move_1m_pct:
                                    return {
                                        'signal': SignalType.NEUTRAL,
                                        'reason': f'Anti-Chase: Price down {price_change_1m:.2f}% < -{max_price_move_1m_pct}%',
                                        'mode': self.current_mode.value,
                                        'config': asdict(config)
                                    }
            except Exception as e:
                print(f"⚠️ Anti-Chase check failed: {e}")
                
            return {
                'signal': signal_type,
                'reason': f"AI: {action} (Conf: {confidence}%) - {state.get('last_prediction', '')[:50]}...",
                'mode': self.current_mode.value,
                'config': asdict(config),
                'signal_score': confidence / 100.0,
                'funding_zscore': 0.0, # AI 已經考慮過了
                'metrics': {
                    'ai_confidence': confidence,
                    'ai_action': action
                }
            }
            
        except Exception as e:
            return {
                'signal': SignalType.NEUTRAL,
                'reason': f'Error reading AI state: {e}',
                'mode': self.current_mode.value,
                'config': asdict(config)
            }

    def generate_signal(
        self,
        df: pd.DataFrame,
        current_time: Optional[pd.Timestamp] = None
    ) -> Dict[str, Any]:
        """
        生成交易信號（增強版）
        
        Returns:
            包含信號、風控參數、檔位信息的字典
        """
        config = self.get_current_config()
        
        # Special handling for AI Whale Hunter
        if self.current_mode == TradingMode.M_AI_WHALE_HUNTER:
            return self._generate_ai_whale_signal(df, config)
        
        # 1. 計算 ATR（盤整過濾）
        atr_pct = self.calculate_atr(df)
        if atr_pct < config.min_atr_pct:
            return {
                'signal': SignalType.NEUTRAL,
                'reason': f'ATR too low ({atr_pct:.4f}% < {config.min_atr_pct:.4f}%)',
                'mode': self.current_mode.value,
                'config': asdict(config)
            }
        
        # 2. 生成基礎信號
        base_signal = self.base_strategy.generate_signal(df, current_time)
        
        # 3. 檢查冷卻時間
        if not self.check_cooldown(self.performance_tracker['last_trade_time']):
            return {
                'signal': SignalType.NEUTRAL,
                'reason': f'In cooldown period',
                'base_signal': base_signal,
                'mode': self.current_mode.value,
                'config': asdict(config)
            }
        
        # 4. 成本過濾
        if base_signal.signal != SignalType.NEUTRAL:
            current_price = df['close'].iloc[-1]
            if not self.check_cost_filter(base_signal, current_price, config):
                return {
                    'signal': SignalType.NEUTRAL,
                    'reason': f'Failed cost filter',
                    'base_signal': base_signal,
                    'mode': self.current_mode.value,
                    'config': asdict(config)
                }
        
        # 5. LLM 顧問介入（如果啟用）
        if self.enable_llm_advisor and base_signal.signal != SignalType.NEUTRAL:
            llm_decision = self._consult_llm_advisor(df, base_signal, config)
            if llm_decision['override']:
                return {
                    'signal': SignalType.NEUTRAL,
                    'reason': f"LLM override: {llm_decision['reason']}",
                    'base_signal': base_signal,
                    'llm_decision': llm_decision,
                    'mode': self.current_mode.value,
                    'config': asdict(config)
                }
        
        # 6. 返回最終信號 + 風控參數
        return {
            'signal': base_signal.signal,
            'confidence': base_signal.confidence,
            'reasoning': base_signal.reasoning,
            'base_signal': base_signal,
            'mode': self.current_mode.value,
            'config': asdict(config),
            'risk_params': {
                'leverage': config.leverage,
                'tp_pct': config.tp_pct,
                'sl_pct': config.sl_pct,
                'time_stop_hours': config.time_stop_hours
            },
            'atr_pct': atr_pct
        }
    
    def _consult_llm_advisor(
        self,
        df: pd.DataFrame,
        signal: HybridSignal,
        config: ModeConfig
    ) -> Dict[str, Any]:
        """
        諮詢 LLM 顧問
        
        TODO: 實現 LLM API 調用
        """
        # 準備市場數據摘要
        market_summary = self._prepare_market_summary(df, signal)
        
        # 準備性能數據
        performance_summary = self._prepare_performance_summary()
        
        # TODO: 調用 LLM API
        # response = call_llm_api(
        #     api_key=self.llm_api_key,
        #     prompt=self._build_advisor_prompt(market_summary, performance_summary),
        # )
        
        # 暫時返回不干預
        return {
            'override': False,
            'reason': 'LLM advisor not implemented yet',
            'suggestion': None
        }
    
    def _prepare_market_summary(
        self,
        df: pd.DataFrame,
        signal: HybridSignal
    ) -> Dict[str, Any]:
        """準備市場數據摘要"""
        recent = df.tail(20)
        
        return {
            'current_price': df['close'].iloc[-1],
            'price_change_1h': (df['close'].iloc[-1] / df['close'].iloc[-4] - 1) * 100,
            'price_change_4h': (df['close'].iloc[-1] / df['close'].iloc[-16] - 1) * 100,
            'funding_rate': signal.funding_rate,
            'rsi': signal.rsi,
            'volume_ratio': signal.volume_ratio,
            'signal_type': signal.signal.value,
            'confidence': signal.confidence,
        }
    
    def _prepare_performance_summary(self) -> Dict[str, Any]:
        """準備性能數據摘要"""
        tracker = self.performance_tracker
        
        win_rate = 0.0
        if tracker['total_trades'] > 0:
            win_rate = tracker['winning_trades'] / tracker['total_trades']
        
        return {
            'total_trades': tracker['total_trades'],
            'win_rate': win_rate,
            'total_pnl': tracker['total_pnl'],
            'current_mode': self.current_mode.value,
        }
    
    def update_performance(
        self,
        trade_result: Dict[str, Any]
    ):
        """
        更新性能追蹤
        
        Args:
            trade_result: 交易結果字典
        """
        tracker = self.performance_tracker
        
        tracker['total_trades'] += 1
        tracker['last_trade_time'] = datetime.now()
        
        if trade_result['pnl'] > 0:
            tracker['winning_trades'] += 1
        else:
            tracker['losing_trades'] += 1
        
        tracker['total_pnl'] += trade_result['pnl']
        
        # 自動調整檔位（基於性能）
        self._auto_adjust_mode()
    
    def _auto_adjust_mode(self):
        """
        自動調整檔位（基於近期表現）
        
        規則：
        - 連續虧損 → 降檔
        - 連續盈利 → 可考慮升檔
        - 勝率過低 → 降檔
        """
        tracker = self.performance_tracker
        
        # 至少 10 筆交易後才調整
        if tracker['total_trades'] < 10:
            return
        
        win_rate = tracker['winning_trades'] / tracker['total_trades']
        
        # 勝率 < 40% → 降檔
        if win_rate < 0.4:
            if self.current_mode.value > TradingMode.M0_ULTRA_SAFE.value:
                # 降一檔
                mode_list = list(TradingMode)
                current_idx = mode_list.index(self.current_mode)
                new_mode = mode_list[current_idx - 1]
                self.switch_mode(new_mode, f"Auto: Low win rate ({win_rate:.1%})")
        
        # 勝率 > 65% 且盈利 > 5% → 可考慮升檔
        elif win_rate > 0.65 and tracker['total_pnl'] > 5.0:
            if self.current_mode.value < TradingMode.M5_ULTRA_AGGRESSIVE.value:
                # 升一檔
                mode_list = list(TradingMode)
                current_idx = mode_list.index(self.current_mode)
                new_mode = mode_list[current_idx + 1]
                self.switch_mode(new_mode, f"Auto: High performance ({win_rate:.1%}, PnL={tracker['total_pnl']:.1f}%)")
    
    def save_config(self, filepath: str):
        """保存當前配置到文件"""
        config_dict = {
            mode.value: asdict(config)
            for mode, config in self.mode_configs.items()
        }
        
        with open(filepath, 'w') as f:
            json.dump(config_dict, f, indent=2)
        
        print(f"✅ Config saved: {filepath}")
    
    def _load_custom_config(self, filepath: str):
        """從文件載入自定義配置"""
        with open(filepath, 'r') as f:
            config_dict = json.load(f)
        
        self.mode_configs = {}
        for mode_str, params in config_dict.items():
            mode = TradingMode(mode_str)
            self.mode_configs[mode] = ModeConfig(**params)
        
        print(f"✅ Custom config loaded: {filepath}")
    
    def print_all_modes(self):
        """打印所有模式配置"""
        print("="*80)
        print("📊 Multi-Mode Hybrid Strategy - All Configurations")
        print("="*80)
        print()
        
        for mode, config in self.mode_configs.items():
            is_current = "👉 " if mode == self.current_mode else "   "
            print(f"{is_current}{mode.value}")
            print(f"   Description: {config.description}")
            print(f"   Target Frequency: {config.target_frequency}")
            print(f"   Funding Z-score: {config.funding_zscore_threshold}")
            print(f"   Signal Score: {config.signal_score_threshold}")
            print(f"   RSI: {config.rsi_oversold}/{config.rsi_overbought}")
            print(f"   Leverage: {config.leverage}x")
            print(f"   TP/SL: {config.tp_pct:.2%} / {config.sl_pct:.2%}")
            print(f"   Cooldown: {config.cooldown_minutes}min")
            print()


# 快速測試
if __name__ == "__main__":
    print("="*80)
    print("🎮 Multi-Mode Hybrid Strategy")
    print("="*80)
    print()
    
    # 創建策略
    strategy = MultiModeHybridStrategy(
        initial_mode=TradingMode.M2_NORMAL
    )
    
    # 顯示所有模式
    strategy.print_all_modes()
    
    print("✅ Multi-Mode Strategy Ready!")
    print()
    print("🎯 Recommended for Paper Trading: M2_NORMAL (3~10筆/天)")
    print("🔬 For stress testing: M5_ULTRA_AGGRESSIVE (30+筆/天)")
    print()
    print("💡 Next steps:")
    print("   1. Test with paper trading")
    print("   2. Monitor performance")
    print("   3. Auto-adjust mode based on results")
    print("   4. Optional: Enable LLM advisor")
