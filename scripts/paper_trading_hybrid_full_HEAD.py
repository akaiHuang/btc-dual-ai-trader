#!/usr/bin/env python3
"""
🚀 Hybrid Multi-Mode Paper Trading System
完整版本 - 保留所有原版 paper_trading_system.py 的顯示格式

運行方式:
python3 scripts/paper_trading_hybrid_full.py 3  # 運行 3 小時
python3 scripts/paper_trading_hybrid_full.py 0.5  # 運行 30 分鐘
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import time
import json
import csv
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from collections import deque
import asyncio
import websockets
try:
    from fetch_binance_leverage_data import BinanceLeverageDataFetcher, save_payload
except ImportError:
    try:
        from scripts.fetch_binance_leverage_data import BinanceLeverageDataFetcher, save_payload
    except ImportError:
        BinanceLeverageDataFetcher = None
        save_payload = None

from src.strategy.hybrid_multi_mode import (
    MultiModeHybridStrategy, 
    TradingMode
)
from src.strategy.signal_generator import SignalGenerator
from src.strategy.mode_config_manager import ModeConfigManager
from src.strategy.rule_engine import RuleEngine
from src.exchange.obi_calculator import OBICalculator
from src.exchange.signed_volume_tracker import SignedVolumeTracker
from src.exchange.vpin_calculator import VPINCalculator
from src.exchange.spread_depth_monitor import SpreadDepthMonitor
from src.utils.consolidation_detector import ConsolidationDetector
from src.utils.market_regime_detector import MarketRegimeDetector, MarketRegime
from src.utils.cost_aware_filter import CostAwareFilter, CostDecision
from src.metrics.leverage_pressure import (
    LiquidationPressureSnapshot,
    PressureLevel,
    load_snapshot_from_file,
    render_panel,
)


# ==================== 數據類 ====================

@dataclass
class LossTrade:
    """虧損交易記錄"""
    trade_id: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    position_size: float
    leverage: int
    direction: str
    loss_amount: float
    loss_percent: float
    holding_time_seconds: int
    rsi_at_entry: Optional[float]
    spread_at_entry: Optional[float]
    volume_at_entry: Optional[float]
    volatility_at_entry: Optional[float]
    obi_at_entry: Optional[float]
    vpin_at_entry: Optional[float]
    exit_reason: str
    sl_percent: float
    tp_percent: float
    strategy: str


class SimulatedOrder:
    """模擬訂單 - 完整的交易生命週期管理"""
    
    def __init__(
        self,
        strategy: str,
        direction: str,  # "LONG" or "SHORT"
        leverage: int,
        size: float,  # 倉位比例 (0-1)
        entry_price: float,
        actual_entry_price: float,
        position_value: float,  # USDT 投資金額
        take_profit_pct: float,  # 止盈百分比
        stop_loss_pct: float,  # 止損百分比
        trailing_stop_pct: Optional[float] = None,
        max_holding_hours: Optional[float] = None,
        min_holding_seconds: float = 10.0,  # 🆕 最小持倉時間（秒）
        entry_time: Optional[str] = None,
        market_data: Optional[dict] = None,
        order_id: Optional[str] = None,
        is_maker: bool = False  # 🆕 是否為 Maker 訂單
    ):
        self.strategy = strategy
        self.direction = direction
        self.leverage = leverage
        self.size = size
        self.entry_price = entry_price
        self.actual_entry_price = actual_entry_price
        self.position_value = position_value
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.dynamic_stop_loss_pct = stop_loss_pct  # 可根據風控動態收緊
        self.trailing_stop_pct = trailing_stop_pct
        self.max_holding_hours = max_holding_hours
        self.min_holding_seconds = min_holding_seconds  # 🆕
        self.min_reverse_exit_seconds = max(min_holding_seconds, 45.0)
        self.entry_time = entry_time or datetime.now().isoformat()
        self.market_data = market_data or {}
        self.order_id = order_id or f"{strategy}_{int(time.time()*1000)}"
        self.is_maker = is_maker
        
        # 開倉費用
        # Maker: -0.01% (返佣), Taker: 0.05%
        fee_rate = -0.0001 if is_maker else 0.0005
        self.entry_fee = position_value * leverage * fee_rate
        
        # 進場原因（例如 LARGE_TRADE_FOLLOW 等）
        self.entry_reason = market_data.get('entry_reason')

        # 動態風控狀態
        self.vpin_risk_mode = False
        self.vpin_risk_trigger_time = None
        self.reverse_profit_buffer = take_profit_pct * 0.4  # 超過此利潤不因反向信號出場
        self.vpin_lock_profit_threshold = take_profit_pct * 0.8

        # 追蹤用
        self.peak_pnl_pct = 0  # 記錄最高盈利百分比
        self.is_blocked = False
        self.blocked_reasons = []
        
        # 平倉信息（稍後填寫）
        self.exit_price = None
        self.exit_time = None
        self.exit_reason = None
        self.exit_fee = 0
        self.funding_fee = 0
        self.pnl_usdt = 0
        self.roi = 0
        self.holding_seconds = 0
        self.total_fees = 0
        
        # 進場時的市場指標
        self.entry_obi = market_data.get('obi', 0)
        self.entry_vpin = market_data.get('vpin', 0)
        self.entry_spread = market_data.get('spread_bps', 0)
        
    def block(self, reasons: List[str]):
        """阻擋交易"""
        self.is_blocked = True
        self.blocked_reasons = reasons
    
    def update_unrealized_pnl(self, current_price: float) -> Tuple[float, float]:
        """更新未實現盈虧
        
        Returns:
            (unrealized_pnl_usdt, unrealized_pnl_pct)
        """
        if self.direction == "LONG":
            price_change = (current_price - self.actual_entry_price) / self.actual_entry_price
        else:  # SHORT
            price_change = (self.actual_entry_price - current_price) / self.actual_entry_price
        
        # 計算未實現盈虧（未扣除費用）
        unrealized_pnl_pct = price_change * self.leverage * 100
        unrealized_pnl_usdt = self.position_value * price_change * self.leverage
        
        # 更新峰值盈利（用於追蹤止損）
        if unrealized_pnl_pct > self.peak_pnl_pct:
            self.peak_pnl_pct = unrealized_pnl_pct
        
        return unrealized_pnl_usdt, unrealized_pnl_pct
    
    def check_exit(
        self,
        current_price: float,
        market_data: dict,
        current_timestamp: str
    ) -> Optional[str]:
        """檢查是否應該平倉"""
        
        # 計算持有時間
        entry_dt = datetime.fromisoformat(self.entry_time)
        current_dt = datetime.fromisoformat(current_timestamp)
        holding_seconds = (current_dt - entry_dt).total_seconds()
        
        # 🆕 最小持倉時間保護：避免開倉即平倉
        if holding_seconds < self.min_holding_seconds:
            return None
        
        # 計算當前盈虧
        _, pnl_pct = self.update_unrealized_pnl(current_price)
        
        holding_hours = holding_seconds / 3600
        
        # ========== 1. 止盈 ==========
        if pnl_pct >= self.take_profit_pct:
            return "TAKE_PROFIT"
        
        # ========== 2. 止損 ==========
        active_stop_loss_pct = self.dynamic_stop_loss_pct
        if pnl_pct <= -active_stop_loss_pct:
            return "VPIN_PROTECTIVE_STOP" if self.vpin_risk_mode else "STOP_LOSS"
        
        # ========== 3. 追蹤止損（如果有設定）==========
        if self.trailing_stop_pct and self.peak_pnl_pct > 0:
            # 計算從峰值回撤的幅度
            drawdown_from_peak = self.peak_pnl_pct - pnl_pct
            trailing_distance = self.take_profit_pct * self.trailing_stop_pct  # 例如 TP=1.5%, Trailing=30% => 0.45%
            
            # 1. 最小持倉時間檢查（避免過早平倉）
            min_holding_seconds = 60  # 至少持有 60 秒
            if holding_seconds < min_holding_seconds:
                return None  # 不執行追蹤止損
            
            # 2. 必須已達到一定盈利才啟動追蹤止損
            min_profit_threshold = self.take_profit_pct * 0.3  # 例如 TP=1.5% => 至少要有 0.45% 利潤
            if self.peak_pnl_pct < min_profit_threshold:
                return None  # 盈利還不夠，不啟動追蹤
            
            # 3. 檢查是否回撤過多
            if drawdown_from_peak >= trailing_distance:
                # 如果從峰值回撤超過 trailing_distance，平倉
                return "TRAILING_STOP"
        
        # ========== 4. 超時平倉 ==========
        if self.max_holding_hours and holding_hours >= self.max_holding_hours:
            if pnl_pct > 0:  # 盈利時超時 -> TIME_LIMIT
                return "TIME_LIMIT"
            else:  # 虧損時超時 -> TIME_STOP
                return "TIME_STOP"
        
        # ========== 5. VPIN 突增檢查（風險信號）==========
        current_vpin = market_data.get('vpin', 0)
        vpin_spike_threshold = 0.8  # VPIN > 0.8 表示市場可能劇烈波動
        
        # 如果 VPIN 突增 且 已有盈利 -> 提前鎖定利潤
        if current_vpin > vpin_spike_threshold:
            tightened_stop = max(self.stop_loss_pct * 0.6, 0.4)
            if not self.vpin_risk_mode:
                self.vpin_risk_mode = True
                self.vpin_risk_trigger_time = holding_seconds
                self.dynamic_stop_loss_pct = min(self.dynamic_stop_loss_pct, tightened_stop)
            else:
                self.dynamic_stop_loss_pct = min(self.dynamic_stop_loss_pct, tightened_stop)

            if (pnl_pct >= self.vpin_lock_profit_threshold and 
                    holding_seconds >= self.min_reverse_exit_seconds):
                return "VPIN_LOCK_PROFIT"
        else:
            if self.vpin_risk_mode and holding_seconds - (self.vpin_risk_trigger_time or 0) >= 120:
                self.vpin_risk_mode = False
                self.dynamic_stop_loss_pct = self.stop_loss_pct
                self.vpin_risk_trigger_time = None
        
        # ========== 6. OBI 反轉檢查（市場情緒反轉）==========
        current_obi = market_data.get('obi', 0)
        
        # 如果進場時的 OBI 信號與當前 OBI 出現明顯反轉 -> 平倉
        reverse_allowed = True
        if holding_seconds < self.min_reverse_exit_seconds and pnl_pct > -active_stop_loss_pct:
            reverse_allowed = False
        if pnl_pct >= self.reverse_profit_buffer:
            reverse_allowed = False

        if reverse_allowed:
            if self.direction == "LONG":
                if self.entry_obi > 0 and current_obi < -0.3:
                    return "REVERSE_SIGNAL"
            else:  # SHORT
                if self.entry_obi < 0 and current_obi > 0.3:
                    return "REVERSE_SIGNAL"
        
        return None
    
    def close(self, exit_price: float, reason: str, timestamp: str):
        """平倉並計算完整盈虧"""
        self.exit_price = exit_price
        self.exit_time = timestamp
        self.exit_reason = reason
        
        # 計算持有時間
        entry_dt = datetime.fromisoformat(self.entry_time)
        exit_dt = datetime.fromisoformat(timestamp)
        self.holding_seconds = (exit_dt - entry_dt).total_seconds()
        holding_hours = self.holding_seconds / 3600
        
        # 計算價格變動
        if self.direction == "LONG":
            price_change_pct = (exit_price - self.actual_entry_price) / self.actual_entry_price
        else:  # SHORT
            price_change_pct = (self.actual_entry_price - exit_price) / self.actual_entry_price
        
        # 計算毛利（未扣費用）
        gross_pnl_usdt = self.position_value * price_change_pct * self.leverage
        
        # 計算平倉費用
        # 如果是 Maker 進場，假設離場也是 Maker (網格策略通常兩邊掛單)
        # 這裡我們假設死魚盤策略離場也是 Maker (-0.01%)
        fee_rate = -0.0001 if getattr(self, 'is_maker', False) else 0.0005
        self.exit_fee = self.position_value * self.leverage * fee_rate
        
        # 計算資金費率（假設每8小時收費一次，0.01%）
        funding_periods = holding_hours / 8
        self.funding_fee = self.position_value * self.leverage * 0.0001 * funding_periods
        
        # 總費用
        self.total_fees = self.entry_fee + self.exit_fee + self.funding_fee
        
        # 淨盈虧
        self.pnl_usdt = gross_pnl_usdt - self.total_fees
        
        # ROI（相對於投資金額）
        self.roi = (self.pnl_usdt / self.position_value) * 100
    
    def to_dict(self) -> dict:
        """轉換為字典（用於記錄）"""
        return {
            'order_id': self.order_id,
            'strategy': self.strategy,
            'direction': self.direction,
            'leverage': self.leverage,
            'size': self.size,
            'entry_price': self.entry_price,
            'actual_entry_price': self.actual_entry_price,
            'position_value': self.position_value,
            'take_profit_pct': self.take_profit_pct,
            'stop_loss_pct': self.stop_loss_pct,
            'entry_reason': self.entry_reason,
            'entry_time': self.entry_time,
            'exit_price': self.exit_price,
            'exit_time': self.exit_time,
            'exit_reason': self.exit_reason,
            'holding_seconds': self.holding_seconds,
            'entry_fee': self.entry_fee,
            'exit_fee': self.exit_fee,
            'funding_fee': self.funding_fee,
            'total_fees': self.total_fees,
            'pnl_usdt': self.pnl_usdt,
            'roi': self.roi,
            'entry_obi': self.entry_obi,
            'entry_vpin': self.entry_vpin,
            'entry_spread': self.entry_spread,
            'is_blocked': self.is_blocked,
            'blocked_reasons': self.blocked_reasons
        }


class LossTradeAnalyzer:
    """虧損交易分析器 - 錯單放大鏡"""
    
    def __init__(self):
        self.loss_trades: List[LossTrade] = []
    
    def record_loss(self, trade: LossTrade):
        """記錄虧損交易"""
        self.loss_trades.append(trade)
    
    def analyze(self) -> dict:
        """分析虧損交易"""
        if not self.loss_trades:
            return {
                'total_losses': 0,
                'avg_loss_percent': 0,
                'most_common_exit_reason': None,
                'worst_trade': None
            }
        
        total_loss = sum(t.loss_amount for t in self.loss_trades)
        avg_loss_pct = np.mean([t.loss_percent for t in self.loss_trades])
        
        # 統計最常見的平倉原因
        exit_reasons = [t.exit_reason for t in self.loss_trades]
        most_common = max(set(exit_reasons), key=exit_reasons.count)
        
        # 找出最大虧損交易
        worst = max(self.loss_trades, key=lambda t: t.loss_amount)
        
        return {
            'total_losses': len(self.loss_trades),
            'total_loss_amount': total_loss,
            'avg_loss_percent': avg_loss_pct,
            'most_common_exit_reason': most_common,
            'worst_trade': asdict(worst)
        }
    
    def save_to_json(self, filepath: str):
        """儲存到 JSON"""
        data = {
            'analysis': self.analyze(),
            'all_trades': [asdict(t) for t in self.loss_trades]
        }
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


class TimeZoneAnalyzer:
    """時間區間分析器"""
    
    def __init__(self):
        self.trades_by_hour = {h: [] for h in range(24)}
    
    def record_trade(self, entry_time: str, exit_time: str, profit: float, is_win: bool, strategy: str):
        """記錄交易"""
        entry_hour = datetime.fromisoformat(entry_time).hour
        self.trades_by_hour[entry_hour].append({
            'entry_time': entry_time,
            'exit_time': exit_time,
            'profit': profit,
            'is_win': is_win,
            'strategy': strategy
        })
    
    def analyze(self) -> dict:
        """分析每小時表現"""
        hourly_stats = {}
        for hour, trades in self.trades_by_hour.items():
            if not trades:
                continue
            
            wins = sum(1 for t in trades if t['is_win'])
            total = len(trades)
            total_profit = sum(t['profit'] for t in trades)
            
            hourly_stats[hour] = {
                'total_trades': total,
                'wins': wins,
                'losses': total - wins,
                'win_rate': wins / total if total > 0 else 0,
                'total_profit': total_profit,
                'avg_profit': total_profit / total if total > 0 else 0
            }
        
        return hourly_stats
    
    def save_to_json(self, filepath: str):
        """儲存到 JSON"""
        with open(filepath, 'w') as f:
            json.dump(self.analyze(), f, indent=2, ensure_ascii=False)


# ==================== 主系統 ====================

class HybridPaperTradingSystem:
    """Hybrid Multi-Mode Paper Trading System"""
    
    def __init__(
        self,
        initial_capital: float = 100.0,
        max_position_pct: float = 0.5,
        test_duration_hours: float = 3.0
    ):
        self.initial_capital = initial_capital
        self.max_position_pct = max_position_pct
        self.test_duration_hours = test_duration_hours

        # 目前啟用的狙擊模式（2025-11 Prime persona 升級 + 專業狙擊手）
        self.active_modes: List[TradingMode] = [
            TradingMode.M0_ULTRA_SAFE,
            TradingMode.M1_SAFE_PRIME,
            TradingMode.M2_NORMAL_PRIME,
            TradingMode.M_FISH_MARKET_MAKER,
            TradingMode.M6_SIGNAL_SANDBOX,
            TradingMode.M7_BREAKOUT_SNIPER,
            TradingMode.M8_VOLUME_SNIPER,
            TradingMode.M9_VOLATILITY_SNIPER,
            TradingMode.M_WHALE_WATCHER,
            TradingMode.M_LP_WHALE_BURST,
            TradingMode.MUP_DIRECTIONAL_LONG,
            TradingMode.MDOWN_DIRECTIONAL_SHORT
        ]
        
        # M_NEW: 一開場立即做空測試模式（50U, 20x, 持續4小時直到爆倉）
        self.m_new_config = {
            'enabled': True,
            'position_usdt': 50.0,
            'leverage': 20,
            'direction': 'SHORT',
            'duration_hours': 4.0,
            'entry_triggered': False,
            'liquidation_price': None,
            'order': None
        }
        self.direction_probe_config: Dict[TradingMode, dict] = {
            TradingMode.MUP_DIRECTIONAL_LONG: {
                'label': 'Mup Bias Probe',
                'direction': 'LONG',
                'confidence': 0.55,
                'cooldown': 10.0
            },
            TradingMode.MDOWN_DIRECTIONAL_SHORT: {
                'label': 'Mdown Bias Probe',
                'direction': 'SHORT',
                'confidence': 0.55,
                'cooldown': 10.0
            }
        }
        # 模式風格：用於判斷 Sniper 決策與止損邏輯
        self.mode_styles: Dict[TradingMode, str] = {
            TradingMode.M0_ULTRA_SAFE: 'baseline',
            TradingMode.M1_SAFE_PRIME: 'trend',
            TradingMode.M2_NORMAL_PRIME: 'scalper',
            TradingMode.M_FISH_MARKET_MAKER: 'reversion',
            TradingMode.M6_SIGNAL_SANDBOX: 'sandbox',
            TradingMode.M7_BREAKOUT_SNIPER: 'breakout',
            TradingMode.M8_VOLUME_SNIPER: 'volume',
            TradingMode.M9_VOLATILITY_SNIPER: 'volatility',
            TradingMode.M_WHALE_WATCHER: 'whale',
            TradingMode.M_LP_WHALE_BURST: 'lp_whale_burst',
            TradingMode.MUP_DIRECTIONAL_LONG: 'direction_probe_long',
            TradingMode.MDOWN_DIRECTIONAL_SHORT: 'direction_probe_short'
        }
        # 顯示用的標籤 / emoji（便於區分新版/舊版）
        self.mode_labels: Dict[TradingMode, str] = {
            TradingMode.M0_ULTRA_SAFE: "M0 Ultra Safe",
            TradingMode.M1_SAFE_PRIME: "M1′ Trend Sniper",
            TradingMode.M2_NORMAL_PRIME: "M2′ Scalper Sniper",
            TradingMode.M_FISH_MARKET_MAKER: "M🐟 Fish Market Maker",
            TradingMode.M6_SIGNAL_SANDBOX: "M6 Sandbox",
            TradingMode.M_WHALE_WATCHER: "M🐳 Whale Watcher",
            TradingMode.M_LP_WHALE_BURST: "M🥊 LP Whale Burst",
            TradingMode.MUP_DIRECTIONAL_LONG: "Mup Bias Probe",
            TradingMode.MDOWN_DIRECTIONAL_SHORT: "Mdown Bias Probe"
        }
        self.mode_emojis: Dict[TradingMode, str] = {
            TradingMode.M0_ULTRA_SAFE: '🛡️M0',
            TradingMode.M1_SAFE_PRIME: '🥷M1′',
            TradingMode.M2_NORMAL_PRIME: '⚡M2′',
            TradingMode.M_FISH_MARKET_MAKER: '🐟M',
            TradingMode.M6_SIGNAL_SANDBOX: '🧪M6',
            TradingMode.M_WHALE_WATCHER: '🐳M',
            TradingMode.M_LP_WHALE_BURST: 'M🥊',
            TradingMode.MUP_DIRECTIONAL_LONG: '🟢Mup',
            TradingMode.MDOWN_DIRECTIONAL_SHORT: '🔴Mdown'
        }
        
        # M_NEW 獨立餘額
        self.m_new_balance = 100.0
        self.trend_gating_rules: Dict[TradingMode, dict] = {
            TradingMode.M1_SAFE_PRIME: {
                'allowed_states': None,  # Allow all states
                'min_confidence': 0.35,
                'label': 'Trend Sniper active in all market conditions'
            },
            TradingMode.M2_NORMAL_PRIME: {
                'allowed_states': None,  # Allow all states
                'min_confidence': 0.25,
                'label': 'Scalper Sniper active in all market conditions'
            },
            TradingMode.M7_BREAKOUT_SNIPER: {
                'allowed_states': None,  # Allow all states
                'min_confidence': 0.4,
                'label': 'Breakout Sniper active in all market conditions'
            },
            TradingMode.M8_VOLUME_SNIPER: {
                'allowed_states': None,  # Allow all states
                'min_confidence': 0.3,
                'label': 'Volume Sniper active in all market conditions'
            }
        }
        self.regime_mode_policies: Dict[str, dict] = {
            MarketRegime.BULL.value: {
                'allow': {
                    TradingMode.M0_ULTRA_SAFE,
                    TradingMode.M1_SAFE_PRIME,
                    TradingMode.M2_NORMAL_PRIME,
                    TradingMode.M7_BREAKOUT_SNIPER,
                    TradingMode.M8_VOLUME_SNIPER,
                    TradingMode.M9_VOLATILITY_SNIPER,
                    TradingMode.M6_SIGNAL_SANDBOX,
                    TradingMode.M_WHALE_WATCHER,
                    TradingMode.M_LP_WHALE_BURST,
                    TradingMode.MUP_DIRECTIONAL_LONG,
                    TradingMode.MDOWN_DIRECTIONAL_SHORT
                },
                'bias': 'LONG_BIAS',
                'reason': 'Regime gating: BULL favors directional modes'
            },
            MarketRegime.BEAR.value: {
                'allow': {
                    TradingMode.M0_ULTRA_SAFE,
                    TradingMode.M1_SAFE_PRIME,
                    TradingMode.M2_NORMAL_PRIME,
                    TradingMode.M7_BREAKOUT_SNIPER,
                    TradingMode.M8_VOLUME_SNIPER,
                    TradingMode.M9_VOLATILITY_SNIPER,
                    TradingMode.M6_SIGNAL_SANDBOX,
                    TradingMode.M_WHALE_WATCHER,
                    TradingMode.M_LP_WHALE_BURST,
                    TradingMode.MUP_DIRECTIONAL_LONG,
                    TradingMode.MDOWN_DIRECTIONAL_SHORT
                },
                'bias': 'SHORT_BIAS',
                'reason': 'Regime gating: BEAR favors directional modes'
            },
            MarketRegime.NEUTRAL.value: {
                'allow': {
                    TradingMode.M0_ULTRA_SAFE,
                    TradingMode.M2_NORMAL_PRIME,
                    TradingMode.M_FISH_MARKET_MAKER,
                    TradingMode.M6_SIGNAL_SANDBOX,
                    TradingMode.M9_VOLATILITY_SNIPER,
                    TradingMode.M_WHALE_WATCHER,
                    TradingMode.M_LP_WHALE_BURST,
                    TradingMode.MUP_DIRECTIONAL_LONG,
                    TradingMode.MDOWN_DIRECTIONAL_SHORT
                },
                'bias': 'NEUTRAL',
                'reason': 'Regime gating: Neutral favors scalper/reversion'
            },
            MarketRegime.CONSOLIDATION.value: {
                'allow': {
                    TradingMode.M0_ULTRA_SAFE,
                    TradingMode.M1_SAFE_PRIME,
                    TradingMode.M2_NORMAL_PRIME,
                    TradingMode.M_FISH_MARKET_MAKER,
                    TradingMode.M6_SIGNAL_SANDBOX,
                    TradingMode.M7_BREAKOUT_SNIPER,
                    TradingMode.M8_VOLUME_SNIPER,
                    TradingMode.M9_VOLATILITY_SNIPER,
                    TradingMode.M_WHALE_WATCHER,
                    TradingMode.M_LP_WHALE_BURST,
                    TradingMode.MUP_DIRECTIONAL_LONG,
                    TradingMode.MDOWN_DIRECTIONAL_SHORT
                },
                'bias': 'NO_TREND',
                'reason': 'Consolidation: All modes allowed (testing)'
            }
        }
        
        # 初始化 Exchange
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        
        # WebSocket 相關（多流: bookTicker + depth + aggTrade）
        self.ws_streams = [
            "btcusdt@bookTicker",
            "btcusdt@depth20@100ms",
            "btcusdt@aggTrade"
        ]
        self.ws_url = f"wss://fstream.binance.com/stream?streams={'/'.join(self.ws_streams)}"
        self.orderbook_data = None
        self.orderbook_timestamp = None
        self.latest_price = None

        # Legacy 指標模組
        self.obi_calc = OBICalculator(depth_limit=20)
        self.signed_volume = SignedVolumeTracker(symbol="BTCUSDT", window_size=150)
        self.vpin_calc = VPINCalculator(symbol="BTCUSDT", bucket_size=20000, num_buckets=40)
        self.spread_depth = SpreadDepthMonitor(symbol="BTCUSDT", depth_levels=10)
        self.consolidation_detector = ConsolidationDetector(
            bb_width_threshold=0.02,
            atr_threshold=0.005
        )
        self.market_regime_detector = MarketRegimeDetector(
            ma_short=7,
            ma_long=25,
            consolidation_threshold=0.003,
            strong_trend_threshold=0.01
        )
        self.cost_filter = CostAwareFilter(
            max_fee_ratio=0.30,
            warning_fee_ratio=0.20,
            min_profit_usd=3.0
        )
        
        # 🆕 動態策略配置系統
        self.mode_config_manager = ModeConfigManager(
            config_path="config/trading_strategies_dynamic.json"
        )
        self.rule_engine = RuleEngine()
        self.last_config_reload_time = time.time()
        self.config_reload_interval = 10.0  # 每 10 秒檢查一次配置更新
        
        # 初始化 6 個 Hybrid 策略（M0-M5）
        self.strategies: Dict[TradingMode, MultiModeHybridStrategy] = {}
        self.mode_info = {}
        
        # 使用類的 MODE_CONFIGS
        self.MODE_CONFIGS = MultiModeHybridStrategy.MODE_CONFIGS
        
        for mode in self.active_modes:
            config = self.MODE_CONFIGS[mode]
            self.strategies[mode] = MultiModeHybridStrategy(
                initial_mode=mode
            )
            
            # 模式資訊 - 處理特殊命名 (M_WHALE_WATCHER)
            mode_name = mode.name
            prefix = mode_name.split('_')[0]
            if mode_name.startswith('M_'):
                # 例如 M_WHALE_WATCHER -> 以 W 作為 fallback
                mode_tag = prefix[1:] or 'W'
            else:
                numeric_part = ''.join(ch for ch in prefix if ch.isdigit())
                stripped = prefix[1:] if prefix.startswith('M') else prefix
                mode_tag = numeric_part or stripped or prefix
            
            emoji = self.mode_emojis.get(mode, f'🤖M{mode_tag}')
            label = self.mode_labels.get(mode, mode.name)
            self.mode_info[mode] = {
                'name': label,
                'emoji': emoji,
                'target_trades': config.target_frequency,
                'leverage': config.leverage
            }
        
        # 餘額追蹤（每個模式獨立）
        self.balances = {mode: initial_capital for mode in self.active_modes}
        
        # 持倉追蹤
        self.orders: Dict[TradingMode, List[SimulatedOrder]] = {
            mode: [] for mode in self.active_modes
        }
        
        # Sniper 模式用的價格歷史（秒級動態分析）
        self.price_history = deque(maxlen=6000)  # 約 10 分鐘
        self.price_bars = {
            'high': deque(maxlen=200),
            'low': deque(maxlen=200),
            'close': deque(maxlen=200),
            'volume': deque(maxlen=200),
            'timestamp': deque(maxlen=200)
        }
        self.pending_volume = 0.0
        # 多時間框架趨勢視窗設定
        self.trend_windows_config = {
            'short': {
                'seconds': 45,
                'min_samples': 35,
                'price_threshold': 0.00035,
                'score_threshold': 0.35,
                'weights': {'price': 0.5, 'obi': 0.25, 'flow': 0.15, 'vpin': 0.10}
            },
            'medium': {
                'seconds': 180,
                'min_samples': 60,
                'price_threshold': 0.0006,
                'score_threshold': 0.30,
                'weights': {'price': 0.45, 'obi': 0.25, 'flow': 0.20, 'vpin': 0.10}
            },
            'long': {
                'seconds': 900,
                'min_samples': 80,
                'price_threshold': 0.0010,
                'score_threshold': 0.25,
                'weights': {'price': 0.40, 'obi': 0.25, 'flow': 0.25, 'vpin': 0.10}
            }
        }
        self.longest_trend_window = max(cfg['seconds'] for cfg in self.trend_windows_config.values())
        self.trend_feature_history = {
            'obi': deque(maxlen=6000),
            'vpin': deque(maxlen=6000),
            'large_flow': deque(maxlen=2000)
        }
        self.trend_state_cache = {
            'trend_state': 'UNKNOWN',
            'trend_confidence': 0.0,
            'trend_alignment': {}
        }
        self.structure_config = {
            'atr_period': 21,
            'swing_confirm_bars': 4,
            'min_swing_distance_pct': 0.0008,
            'persistence_required': 3,
            'pullback_buffer_mult': 0.6
        }
        self.structure_state = {
            'swings': deque(maxlen=12),
            'direction': 'UNKNOWN',
            'persistence': 0,
            'last_break_ts': None,
            'last_break_side': None
        }
        self.sniper_config = {
            'lookback_seconds': 30,
            'min_samples': 80,
            'momentum_floor_pct': 0.05,  # 未槓桿價格變動 0.05%（提高）
            'volatility_guard_multiplier': 1.8,  # 提高波動保護
            'min_net_edge_pct': 0.8,  # 扣費後至少 0.8% 淨報酬（提高）
            'edge_take_profit_ratio': 0.85,
            'edge_stop_ratio': 0.35,
            # M7 Breakout Sniper 特有參數
            'breakout_lookback_bars': 20,  # 回還 20 個 K 棒找高低點
            'breakout_threshold_pct': 0.003,  # 突破 0.3% 算有效
            # M8 Volume Sniper 特有參數
            'volume_zscore_threshold': 2.5,  # 量能 z-score > 2.5 算異常
            'volume_window_bars': 50,  # 用 50 個 K 棒計算 z-score
            # M9 Volatility Sniper 特有參數
            'volatility_threshold_pct': 0.006,  # ATR > 0.6% 算高波動
            'volatility_window_bars': 30  # 用 30 個 K 棒計算 ATR
        }
        # 強 funding 訊號時允許覆蓋 OBI 的方向判斷
        self.strong_funding_override = 3.5

        # 微觀結構信號生成器（OBI / Microprice / Signed Volume）
        self.signal_generator = SignalGenerator(symbol="BTCUSDT")
        self.last_snapshot_meta = None
        self.last_snapshot_time = None
        
        # 🆕 開倉冷卻追蹤（每個模式獨立）
        self.last_entry_time: Dict[TradingMode, float] = {
            mode: 0 for mode in self.active_modes
        }
        
        # 🆕 設定每個模式的冷卻時間（秒）- 配合每日 20-40 次目標
        # 假設一天 24 小時，要 20-40 次 => 平均 36-72 分鐘一次
        # 但我們允許多個模式並行，所以每個模式可以設定不同的冷卻
        self.entry_cooldown: Dict[TradingMode, float] = {
            TradingMode.M0_ULTRA_SAFE: 120,  # 2 分鐘（最保守）
            TradingMode.M1_SAFE_PRIME: 75,   # Trend Sniper：更密集但仍克制
            TradingMode.M2_NORMAL_PRIME: 90, # Scalper Sniper：降風險後拉長 cooldown
            TradingMode.M_FISH_MARKET_MAKER: 25,  # Fish Market Maker：快速進出
            TradingMode.M6_SIGNAL_SANDBOX: 60,  # 診斷模式降為中頻
            TradingMode.M7_BREAKOUT_SNIPER: 45 * 60,  # 45 分鐘 - 突破不常出現
            TradingMode.M8_VOLUME_SNIPER: 30 * 60,    # 30 分鐘 - 量能異常較少見
            TradingMode.M9_VOLATILITY_SNIPER: 20 * 60, # 20 分鐘 - 高波動窗口中等頻率
            TradingMode.M_LP_WHALE_BURST: 60 * 60,     # 60 分鐘 - 爆倉 + 鯨魚 setup 稀有
            TradingMode.M_WHALE_WATCHER: 30,           # 鯨魚跟單：依大單節奏調整即可
            TradingMode.MUP_DIRECTIONAL_LONG: 10,      # Mup 方向探針：維持短冷卻即可重上
            TradingMode.MDOWN_DIRECTIONAL_SHORT: 10    # Mdown 方向探針：維持短冷卻即可重上
        }
        
        # 🆕 連虧保護機制
        self.consecutive_losses: Dict[TradingMode, int] = {
            mode: 0 for mode in self.active_modes
        }
        self.loss_cooldown_until: Dict[TradingMode, float] = {
            mode: 0 for mode in self.active_modes
        }
        
        # 🆕 M🐳 反轉頻率限制（防刷單洗盤）
        self.whale_reversal_tracker: Dict[TradingMode, dict] = {
            TradingMode.M_WHALE_WATCHER: {
                'last_direction': None,        # 上一次方向
                'reversal_count': 0,           # 30分鐘內反轉次數
                'reversal_timestamps': [],     # 反轉時間戳記
                'penalty_cooldown': 0,         # 懲罰性冷卻（秒）
            }
        }
        
        # 🆕 大單追蹤（跟隨機構足跡, 看「一段時間內淨方向」）
        self.large_trade_threshold = 1.0  # 單筆大單門檻 (BTC)
        # 最近一小段時間內的大單列表，用來累積多空總量
        self.recent_large_trades = deque(maxlen=50)
        # 趨勢用的大單歷史（較長視窗）
        self.large_trade_history = deque(maxlen=800)
        # 由最近大單計算出的「淨方向」訊號
        self.large_trade_signal = {
            'direction': None,   # 'LONG' / 'SHORT'
            'timestamp': 0.0,
            'net_qty': 0.0       # 多空淨量（多 - 空）
        }
        # 多空總和視窗 & 增強視窗（秒）
        self.large_trade_agg_window = 30   # 只看最近 30 秒內的大單
        self.large_trade_boost_window = 60 # 發出淨方向訊號後 60 秒內加權
        self.large_trade_positioning = {
            'max_risk_block': 0.85,
            'reduce_threshold': 0.65,
            'reduced_size_multiplier': 0.55
        }

        # Phase 4：槓桿爆倉壓力模組設定
        self.liq_pressure_config = {
            'data_path': Path("data/liquidation_pressure/latest_snapshot.json"),
            'refresh_interval': 10.0,
            'stale_seconds': 240.0,
            'threshold_discount': 0.85,
            'size_boost_pct': 0.2,
            'max_size_multiplier': 1.35,
            'confidence_bonus': 0.12,
            'block_conflict_on_extreme': True
        }
        self.offensive_pressure_modes = {
            TradingMode.M2_NORMAL_PRIME,
            TradingMode.M_FISH_MARKET_MAKER,
            TradingMode.M7_BREAKOUT_SNIPER,
            TradingMode.M8_VOLUME_SNIPER,
            TradingMode.M9_VOLATILITY_SNIPER,
            TradingMode.M_WHALE_WATCHER,
            TradingMode.M_LP_WHALE_BURST
        }
        self._liq_pressure_snapshot: Optional[LiquidationPressureSnapshot] = None
        self._liq_pressure_snapshot_dict: Optional[Dict[str, Any]] = None
        self._liq_pressure_last_load: float = 0.0
        self._liq_pressure_last_mtime: float = 0.0
        
        # 分析器
        self.loss_analyzer = LossTradeAnalyzer()
        self.time_analyzer = TimeZoneAnalyzer()
        
        # 測試開始時間
        self.start_time = datetime.now()
        self.end_time = self.start_time + timedelta(hours=test_duration_hours)
        
        # 🆕 即時保存檔案結構 (仿照 paper_trading_system.py)
        self.save_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.session_folder = datetime.now().strftime('pt_%Y%m%d_%H%M')
        self.session_dir = f"data/paper_trading/{self.session_folder}"
        
        # 確保資料夾存在
        os.makedirs(self.session_dir, exist_ok=True)
        
        # 檔案路徑
        self.json_filename = f"{self.session_dir}/trading_data.json"
        self.log_filename = f"{self.session_dir}/trading.log"
        self.terminal_log_filename = f"{self.session_dir}/terminal_output.txt"
        
        print(f"\n{'='*80}")
        print(f"🚀 Hybrid Multi-Mode Paper Trading System")
        print(f"{'='*80}\n")
        print(f"📁 本次交易資料將儲存至: {self.session_dir}")
        print(f"⏰ 測試時長: {test_duration_hours} 小時")
        print(f"💵 初始資金: ${initial_capital:.2f} USDT（每個模式獨立）")
        print(f"📊 測試模式: {len(self.active_modes)} 個 Hybrid 模式（含診斷模式）並行測試\n")
        
        print(f"✅ 載入 {len(self.active_modes)} 個 Hybrid 策略:\n")
        
        categories = {
            "⚔️ 主力作戰模式 (核心獲利策略)": [
                TradingMode.M0_ULTRA_SAFE,
                TradingMode.M1_SAFE_PRIME,
                TradingMode.M2_NORMAL_PRIME,
                TradingMode.M_FISH_MARKET_MAKER
            ],
            "🎯 特種戰術模式 (針對特定事件)": [
                TradingMode.M_WHALE_WATCHER,
                TradingMode.M_LP_WHALE_BURST,
                TradingMode.M7_BREAKOUT_SNIPER,
                TradingMode.M8_VOLUME_SNIPER,
                TradingMode.M9_VOLATILITY_SNIPER
            ],
            "🧪 診斷與工具模式 (非比較用)": [
                TradingMode.M6_SIGNAL_SANDBOX,
                TradingMode.MUP_DIRECTIONAL_LONG,
                TradingMode.MDOWN_DIRECTIONAL_SHORT
            ]
        }

        for category, modes in categories.items():
            print(f"   {category}")
            for mode in modes:
                if mode in self.active_modes:
                    info = self.mode_info[mode]
                    config = self.MODE_CONFIGS[mode]
                    print(f"      {info['emoji']} {info['name']} - {config.leverage}x 槓桿")
                    print(f"         目標: {config.target_frequency}")
                    print(f"         參數: Z={config.funding_zscore_threshold:.1f}, Signal={config.signal_score_threshold:.1f}")
            print()
        
        print(f"\n{'='*80}")
        self._print_market_indicators_guide()
        print(f"{'='*80}\n")
        
        # 🆕 初始化保存檔案
        self._init_save_file()
        self.signal_log_file = f"{self.session_dir}/signal_diagnostics.csv"
        self._init_signal_log_file()
        
        # 🆕 初始化鯨魚反轉分析檔案
        self.whale_flip_log_file = f"{self.session_dir}/whale_flip_analysis.csv"
        self._init_whale_flip_log_file()
    
    def _print_market_indicators_guide(self):
        """列印市場指標說明"""
        print("\n📖 市場指標說明")
        print(f"{'─'*80}")
        
        print("📊 OBI (Order Book Imbalance)     訂單簿失衡度 [-1, 1]")
        print("   • 正值 = 買盤強勢 | 負值 = 賣盤強勢 | 0 = 平衡")
        print("   • 越接近 ±1 代表失衡越嚴重")
        
        print("\n⚖️  VPIN (Volume-Synchronized)    交易流毒性指標 [0, 1]")
        print("   • 接近 1 = 高毒性（知情交易者主導）")
        print("   • 接近 0 = 低毒性（流動性良好）")

    # ---------------- Phase 4 helpers (liquidation pressure) ---------------- #

    def _maybe_load_liquidation_pressure(self) -> Optional[LiquidationPressureSnapshot]:
        cfg = self.liq_pressure_config
        path = Path(cfg['data_path']) if not isinstance(cfg['data_path'], Path) else cfg['data_path']
        now = time.time()

        if now - self._liq_pressure_last_load < cfg['refresh_interval']:
            return self._liq_pressure_snapshot

        self._liq_pressure_last_load = now
        if not path.exists():
            return self._liq_pressure_snapshot

        try:
            mtime = path.stat().st_mtime
        except OSError:
            return self._liq_pressure_snapshot

        if self._liq_pressure_snapshot and mtime == self._liq_pressure_last_mtime:
            return self._liq_pressure_snapshot

        snapshot = load_snapshot_from_file(str(path))
        if snapshot:
            self._liq_pressure_snapshot = snapshot
            self._liq_pressure_snapshot_dict = snapshot.to_dict()
            self._liq_pressure_last_mtime = mtime
        return self._liq_pressure_snapshot

    def _get_liquidation_pressure_dict(self) -> Optional[Dict[str, Any]]:
        snapshot = self._maybe_load_liquidation_pressure()
        if snapshot:
            if (
                not self._liq_pressure_snapshot_dict
                or self._liq_pressure_snapshot_dict.get('collected_at') != snapshot.collected_at
            ):
                self._liq_pressure_snapshot_dict = snapshot.to_dict()
        return self._liq_pressure_snapshot_dict

    def _liquidation_snapshot_age(self, snapshot: LiquidationPressureSnapshot) -> Optional[float]:
        if not snapshot or not snapshot.collected_at:
            return None
        try:
            dt = datetime.fromisoformat(snapshot.collected_at)
        except ValueError:
            return None
        return max(0.0, time.time() - dt.timestamp())

    def _render_liquidation_pressure_panel(self) -> Optional[str]:
        snapshot = self._maybe_load_liquidation_pressure()
        if not snapshot:
            return None
        panel = render_panel(snapshot)
        age = self._liquidation_snapshot_age(snapshot)
        stale_seconds = self.liq_pressure_config.get('stale_seconds', 240.0)
        if age is not None and age > stale_seconds:
            panel += f"\n⚠️ 爆倉壓力資料已 {age/60:.1f} 分鐘前，請重新 fetch"
        return panel

    def _evaluate_lp_whale_burst_signal(
        self,
        mode: TradingMode,
        snapshot: dict,
        pressure_obj: Optional[LiquidationPressureSnapshot],
        obi: float
    ) -> Tuple[Optional[dict], str]:
        """Evaluate whether LP + whale conditions align for M🥊."""
        default_rules = {
            'L_long_liq_min': 70,
            'L_short_liq_min': 70,
            'liq_diff_min': 25,
            'whale_dominance_min': 0.6,
            'obi_long_min': 0.1,
            'obi_short_max': -0.1
        }

        if not pressure_obj:
            return None, 'M🥊 waiting for liquidation snapshot'

        # Guard against stale data
        liq_age = snapshot.get('liquidation_age_seconds')
        stale_seconds = self.liq_pressure_config.get('stale_seconds', 240.0)
        if liq_age and liq_age > stale_seconds:
            minutes = liq_age / 60
            return None, f"M🥊 liquidation data stale ({minutes:.1f}m)"

        dynamic_cfg = self.mode_config_manager.get_config(mode.name) or {}
        entry_rules = dynamic_cfg.get('entry_rules', {})
        thresholds = {k: entry_rules.get(k, v) for k, v in default_rules.items()}

        whale_signal = getattr(self, 'large_trade_signal', {}) or {}
        dominance = float(whale_signal.get('dominance_ratio', 0.0) or 0.0)
        net_direction = whale_signal.get('direction')
        net_qty = float(whale_signal.get('net_qty', 0.0) or 0.0)

        long_score = pressure_obj.long_score
        short_score = pressure_obj.short_score
        diff = abs(long_score - short_score)
        long_ready = (
            long_score >= thresholds['L_long_liq_min'] and
            (long_score - short_score) >= thresholds['liq_diff_min']
        )
        short_ready = (
            short_score >= thresholds['L_short_liq_min'] and
            (short_score - long_score) >= thresholds['liq_diff_min']
        )

        if not long_ready and not short_ready:
            return None, (
                f"M🥊 waiting for LP imbalance (L={long_score:.1f}, S={short_score:.1f})"
            )
        if not net_direction:
            return None, 'M🥊 waiting for whale direction'
        if dominance < thresholds['whale_dominance_min']:
            return None, (
                f"M🥊 whale dominance {dominance:.2f} < {thresholds['whale_dominance_min']:.2f}"
            )

        direction = None
        reason = ''
        if (
            long_ready
            and net_direction == 'SHORT'
            and obi <= thresholds['obi_short_max']
        ):
            direction = 'SHORT'
            reason = (
                f"M🥊 SHORT: L_liq={long_score:.1f}, diff={long_score - short_score:.1f}, "
                f"whale={dominance:.2f}, obi={obi:.2f}"
            )
        elif (
            short_ready
            and net_direction == 'LONG'
            and obi >= thresholds['obi_long_min']
        ):
            direction = 'LONG'
            reason = (
                f"M🥊 LONG: S_liq={short_score:.1f}, diff={short_score - long_score:.1f}, "
                f"whale={dominance:.2f}, obi={obi:.2f}"
            )
        else:
            if net_direction == 'SHORT' and not long_ready:
                return None, 'M🥊 whale SHORT but long-side pressure not extreme'
            if net_direction == 'LONG' and not short_ready:
                return None, 'M🥊 whale LONG but short-side pressure not extreme'
            if direction is None:
                return None, 'M🥊 waiting for OBI alignment with whale bias'

        score_component = max(long_score, short_score) / 100
        diff_component = diff / 100
        confidence = min(
            1.0,
            0.4 * score_component + 0.3 * dominance + 0.2 * diff_component + 0.1 * pressure_obj.bias_confidence
        )
        size_boost = max(0.0, dominance - thresholds['whale_dominance_min'])
        size_multiplier = 1.0 + min(0.5, size_boost * 0.8)

        signal = {
            'direction': direction,
            'reason': reason,
            'confidence': confidence,
            'dominance': dominance,
            'net_qty': net_qty,
            'long_score': long_score,
            'short_score': short_score,
            'diff': diff,
            'size_multiplier': size_multiplier,
            'thresholds': thresholds
        }
        return signal, ''

    def _make_direction_probe_decision(
        self,
        mode: TradingMode,
        direction: str,
        market_data: dict
    ) -> dict:
        """Generate decisions for the always-on direction probes (Mup/Mdown)."""
        open_positions = [
            o for o in self.orders[mode]
            if not o.is_blocked and o.exit_time is None
        ]
        if open_positions:
            return {
                'action': 'HOLD',
                'reason': 'Direction probe already holding position'
            }

        now_ts = time.time()
        last_entry = self.last_entry_time.get(mode, 0.0)
        base_cooldown = self.entry_cooldown.get(mode, 0.0)
        probe_cfg = self.direction_probe_config.get(mode, {})
        cooldown = probe_cfg.get('cooldown', base_cooldown)
        remaining = cooldown - (now_ts - last_entry)
        if cooldown and remaining > 0:
            return {
                'action': 'HOLD',
                'reason': f'Direction probe cooldown: {remaining:.1f}s remaining'
            }

        label = probe_cfg.get('label', mode.name)
        confidence = probe_cfg.get('confidence', 0.5)
        market_data['direction_probe'] = {
            'target': direction,
            'label': label
        }
        market_data['entry_reason'] = 'DIRECTION_PROBE'
        return {
            'action': direction,
            'reason': f"{label} auto-{direction.lower()}",
            'confidence': confidence
        }
        
        print("\n📏 Spread (Bid-Ask Spread)        買賣價差 (基點 bps)")
        print("   • 越小 = 流動性越好")
        print("   • 越大 = 流動性差或波動大")
        
        print("\n📚 Depth Imbalance                訂單簿深度失衡")
        print("   • Bids > Asks = 買盤深度強")
        print("   • Asks > Bids = 賣盤深度強")
        
        print("\n💰 Funding Rate Z-score           資金費率標準化")
        print("   • 正值 = 多頭資金成本高（偏空信號）")
        print("   • 負值 = 空頭資金成本高（偏多信號）")
        
        print("\n📈 Signal Score                   技術指標組合分數")
        print("   • 正值 = 多頭信號")
        print("   • 負值 = 空頭信號")
        print("   • 絕對值越大 = 信號越強")
        
        print(f"\n{'─'*80}")
        print("🎨 圖示說明")
        print(f"{'─'*80}")
        print("交易方向:  📈 LONG | 📉 SHORT | ⚖️  NEUTRAL")
        print("風險等級:  🟢 SAFE | 🟡 WARNING | 🔴 CRITICAL")
        print("狀態指示:  ✅ 正常 | ⚠️  警告 | ❌ 錯誤")
        print("開倉:      🚀 開倉  |  🔔 平倉")
        print("平倉原因:  🎯 TAKE_PROFIT (止盈)  |  🛑 STOP_LOSS (止損)  |  🔄 REVERSE_SIGNAL (反向)")
        print("           ⏰ TIME_LIMIT (超時止盈)  |  ⏱️  TIME_STOP (超時止損)  |  📉 TRAILING_STOP (追蹤止損)")
        print("           ☠️  VPIN_SPIKE (VPIN突增)")
        
        print(f"\n{'─'*80}")
        print("🎯 Hybrid 模式對比:")
        print(f"{'─'*80}")
        print("Hybrid 策略核心邏輯:")
        print("   📌 資金費率信號 (Funding Rate Z-score): 判斷市場情緒偏向")
        print("   📌 技術指標組合 (Signal Score): RSI + ATR + 成交量綜合評分")
        print("   📌 雙重確認機制: 兩者同時達標才進場,降低假訊號")
        print()
        for mode in self.active_modes:
            config = self.MODE_CONFIGS[mode]
            
            # 處理特殊命名 (M_WHALE_WATCHER)
            mode_name = mode.name
            if mode_name.startswith('M_') and len(mode_name.split('_')[0]) == 1:
                # M_WHALE_WATCHER 這類特殊命名
                mode_display = self.mode_emojis.get(mode, '🤖')
                label = self.mode_labels.get(mode, mode_name)
                risk_level = '🟡 中等'
            else:
                # M0, M1, M2 等標準命名
                mode_num = int(mode_name.split('_')[0][1]) if len(mode_name.split('_')[0]) > 1 else 0
                mode_display = f"🤖M{mode_num}"
                label = self.mode_labels.get(mode, mode_name)
                risk_level = '🟢 保守' if mode_num <= 1 else '🟡 穩健' if mode_num == 2 else '🔴 激進'
            
            print(f"   {mode_display} {label}")
            print(f"      🎯 目標頻率: {config.target_frequency}")
            print(f"      🛡️  風險等級: {risk_level}")
            print(f"      📊 進場條件: Funding Z > {config.funding_zscore_threshold:.1f} AND Signal > {config.signal_score_threshold:.1f}")
            print(f"      ⚡ 槓桿倍數: {config.leverage}x")
            print(f"      🎯 止盈/止損: TP={config.tp_pct:.1f}% / SL={config.sl_pct:.1f}% (現貨百分比)")
            print()
    
    def _record_price(self, price: Optional[float]):
        """紀錄最新價格供 Sniper 模式估算動能"""
        if price is None:
            return
        self.price_history.append((time.time(), price))

    def _update_price_bars(self, best_bid: Optional[float], best_ask: Optional[float]):
        """同步 OHLCV 緩存供市場狀態偵測使用"""
        if best_bid is None or best_ask is None:
            return
        close_price = (best_bid + best_ask) / 2
        self.price_bars['high'].append(best_ask)
        self.price_bars['low'].append(best_bid)
        self.price_bars['close'].append(close_price)
        self.price_bars['volume'].append(self.pending_volume)
        self.price_bars['timestamp'].append(time.time())
        self.pending_volume = 0.0

    def _record_trend_features(self, timestamp: float, obi: float, vpin: Optional[float]):
        """記錄 OBI / VPIN / 大單淨向量供多時間框架趨勢使用"""
        vpin_value = float(vpin) if vpin is not None else 0.0
        self.trend_feature_history['obi'].append((timestamp, float(obi)))
        self.trend_feature_history['vpin'].append((timestamp, vpin_value))
        large_flow_bias = self._compute_large_trade_bias(timestamp, window_seconds=60)
        self.trend_feature_history['large_flow'].append((timestamp, large_flow_bias))
        self._prune_large_trade_history(timestamp)

    def _prune_large_trade_history(self, timestamp: float):
        """移除過舊的大單資料以維持計算效率"""
        cutoff = timestamp - max(self.longest_trend_window, self.large_trade_agg_window)
        while self.large_trade_history and self.large_trade_history[0]['time'] < cutoff:
            self.large_trade_history.popleft()

    def _check_whale_persistence(self, net_direction: str, now_ts: float) -> dict:
        """檢查大單方向是否在多個連續窗口都保持一致
        
        Returns:
            dict: {
                'is_persistent': bool,
                'consistent_windows': int,
                'reason': str
            }
        """
        windows = [5, 10, 15]  # 0-5s, 5-10s, 10-15s
        consistent_count = 0
        
        for window_end in windows:
            window_start = window_end - 5
            
            # 計算這個窗口內的淨方向
            long_qty = sum(
                t['qty'] for t in self.recent_large_trades 
                if t['direction'] == 'LONG' 
                and window_start <= (now_ts - t['timestamp']) < window_end
            )
            short_qty = sum(
                t['qty'] for t in self.recent_large_trades 
                if t['direction'] == 'SHORT' 
                and window_start <= (now_ts - t['timestamp']) < window_end
            )
            
            if long_qty == 0 and short_qty == 0:
                continue
                
            window_direction = 'LONG' if long_qty > short_qty else 'SHORT'
            
            if window_direction == net_direction:
                consistent_count += 1
        
        is_persistent = consistent_count >= 2  # 至少 2 個窗口一致（降低到 2）
        
        return {
            'is_persistent': is_persistent,
            'consistent_windows': consistent_count,
            'reason': f'Whale signal consistent in {consistent_count}/3 windows'
        }
    
    def _compute_large_trade_bias(self, now_ts: float, window_seconds: float) -> float:
        if not self.large_trade_history:
            return 0.0
        cutoff = now_ts - window_seconds
        longs = 0.0
        shorts = 0.0
        for trade in self.large_trade_history:
            if trade['time'] < cutoff:
                continue
            if trade['direction'] == 'LONG':
                longs += trade['qty']
            else:
                shorts += trade['qty']
        total = longs + shorts
        if total <= 0:
            return 0.0
        return (longs - shorts) / total

    def _get_history_average(self, history_key: str, now_ts: float, window_seconds: float) -> float:
        history = self.trend_feature_history.get(history_key)
        if not history:
            return 0.0
        values = [value for ts, value in history if now_ts - ts <= window_seconds]
        if not values:
            return 0.0
        return float(np.mean(values))

    def _compute_trend_state(self, timestamp: float) -> dict:
        if not self.price_history:
            return self.trend_state_cache

        window_states: Dict[str, dict] = {}
        up_count = 0
        down_count = 0
        active_windows = 0

        for name, config in self.trend_windows_config.items():
            prices = [price for ts, price in self.price_history if timestamp - ts <= config['seconds']]
            if len(prices) < config['min_samples']:
                window_states[name] = {
                    'direction': 'INSUFFICIENT',
                    'score': 0.0,
                    'price_change_pct': 0.0,
                    'obi_mean': 0.0,
                    'vpin_mean': 0.0,
                    'large_flow_bias': 0.0,
                    'strength': 0.0
                }
                continue

            active_windows += 1
            start_price = prices[0]
            end_price = prices[-1]
            price_change_pct = ((end_price - start_price) / start_price) if start_price else 0.0
            obi_mean = self._get_history_average('obi', timestamp, config['seconds'])
            vpin_mean = self._get_history_average('vpin', timestamp, config['seconds'])
            large_flow_bias = self._compute_large_trade_bias(timestamp, config['seconds'])

            price_component = float(np.tanh(price_change_pct / config['price_threshold'])) * config['weights']['price']
            obi_component = float(np.tanh(obi_mean * 2.5)) * config['weights']['obi']
            flow_component = float(np.tanh(large_flow_bias)) * config['weights']['flow']
            vpin_component = -max(0.0, vpin_mean - 0.55) * config['weights']['vpin'] * 2.0
            score = price_component + obi_component + flow_component + vpin_component

            direction = 'NEUTRAL'
            if score >= config['score_threshold']:
                direction = 'UP'
                up_count += 1
            elif score <= -config['score_threshold']:
                direction = 'DOWN'
                down_count += 1

            strength = min(1.0, abs(score) / max(config['score_threshold'] * 2, 1e-9))
            window_states[name] = {
                'direction': direction,
                'score': round(float(score), 4),
                'price_change_pct': price_change_pct,
                'obi_mean': obi_mean,
                'vpin_mean': vpin_mean,
                'large_flow_bias': large_flow_bias,
                'strength': strength
            }

        if active_windows == 0:
            return self.trend_state_cache

        consensus = max(up_count, down_count) / max(active_windows, 1)
        trend_state = 'RANGE'
        if up_count >= 2 and down_count == 0:
            trend_state = 'STRONG_UP'
        elif down_count >= 2 and up_count == 0:
            trend_state = 'STRONG_DOWN'
        elif up_count > down_count:
            trend_state = 'LEAN_UP'
        elif down_count > up_count:
            trend_state = 'LEAN_DOWN'

        if consensus < 0.34:
            trend_state = 'RANGE'

        result = {
            'trend_state': trend_state,
            'trend_confidence': round(float(consensus), 3),
            'trend_alignment': window_states
        }
        self.trend_state_cache = result
        return result

    def _calculate_atr(self, highs: List[float], lows: List[float], closes: List[float], period: int) -> Optional[float]:
        if not highs or not lows or not closes or len(highs) < period + 1:
            return None
        tr_values = []
        for i in range(1, len(highs)):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i-1]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_values.append(tr)
        if len(tr_values) < period:
            return None
        recent_tr = tr_values[-period:]
        return float(np.mean(recent_tr)) if recent_tr else None

    def _compute_swings(self, highs: List[float], lows: List[float], timestamps: List[float], atr: Optional[float]) -> List[dict]:
        confirm = self.structure_config['swing_confirm_bars']
        if len(highs) < confirm * 2 + 1 or len(timestamps) != len(highs):
            return []
        swings = []
        min_distance = self.structure_config['min_swing_distance_pct']
        for idx in range(confirm, len(highs) - confirm):
            high_window = highs[idx-confirm: idx+confirm+1]
            low_window = lows[idx-confirm: idx+confirm+1]
            ts = timestamps[idx]
            price_ref = highs[idx]
            if price_ref >= max(high_window):
                if not swings or swings[-1]['type'] != 'HIGH' or price_ref > swings[-1]['price']:
                    swings.append({'type': 'HIGH', 'price': price_ref, 'ts': ts})
            price_ref = lows[idx]
            if price_ref <= min(low_window):
                if not swings or swings[-1]['type'] != 'LOW' or price_ref < swings[-1]['price']:
                    swings.append({'type': 'LOW', 'price': price_ref, 'ts': ts})

        swings.sort(key=lambda s: s['ts'])
        filtered = []
        for swing in swings:
            if filtered and filtered[-1]['type'] == swing['type']:
                if swing['type'] == 'HIGH' and swing['price'] >= filtered[-1]['price']:
                    filtered[-1] = swing
                elif swing['type'] == 'LOW' and swing['price'] <= filtered[-1]['price']:
                    filtered[-1] = swing
                continue
            if filtered:
                prev_price = filtered[-1]['price']
                if prev_price > 0:
                    distance_pct = abs(swing['price'] - prev_price) / prev_price
                    if distance_pct < min_distance:
                        continue
            filtered.append(swing)

        if atr:
            atr_threshold = atr * 0.15
            compacted = []
            for swing in filtered:
                if not compacted:
                    compacted.append(swing)
                    continue
                prev = compacted[-1]
                if swing['type'] == prev['type']:
                    if swing['type'] == 'HIGH' and swing['price'] - prev['price'] >= atr_threshold:
                        compacted[-1] = swing
                    elif swing['type'] == 'LOW' and prev['price'] - swing['price'] >= atr_threshold:
                        compacted[-1] = swing
                else:
                    compacted.append(swing)
            filtered = compacted

        return filtered[-12:]

    def _assess_structure_direction(self, swings: List[dict]) -> Tuple[str, int]:
        if len(swings) < 4:
            return 'UNKNOWN', 0
        highs = [s for s in swings if s['type'] == 'HIGH']
        lows = [s for s in swings if s['type'] == 'LOW']
        if len(highs) < 2 or len(lows) < 2:
            return 'UNKNOWN', 0

        hh = highs[-1]['price'] > highs[-2]['price']
        hl = lows[-1]['price'] > lows[-2]['price']
        lh = highs[-1]['price'] < highs[-2]['price']
        ll = lows[-1]['price'] < lows[-2]['price']

        direction = 'RANGE'
        compare_high = None
        compare_low = None
        if hh and hl:
            direction = 'BULLISH'
            compare_high = lambda a, b: a > b
            compare_low = lambda a, b: a > b
        elif lh and ll:
            direction = 'BEARISH'
            compare_high = lambda a, b: a < b
            compare_low = lambda a, b: a < b
        else:
            return 'RANGE', 0

        def streak(values: List[dict], comparator) -> int:
            if len(values) < 2:
                return 0
            count = 0
            for i in range(len(values) - 1, 0, -1):
                if comparator(values[i]['price'], values[i-1]['price']):
                    count += 1
                else:
                    break
            return count

        high_streak = streak(highs, compare_high)
        low_streak = streak(lows, compare_low)
        persistence = min(high_streak, low_streak)
        return direction, persistence

    def _detect_market_churn(self) -> dict:
        """檢測市場震盪（防止在震盪區間頻繁交易）
        
        Returns:
            dict: {
                'is_churning': bool,
                'atr_pct': float,
                'bb_width_pct': float,
                'price_range_pct': float,
                'reason': str
            }
        """
        if len(self.price_bars['close']) < 50:
            return {
                'is_churning': False,
                'atr_pct': 0,
                'bb_width_pct': 0,
                'price_range_pct': 0,
                'reason': 'Insufficient data'
            }
        
        closes = list(self.price_bars['close'])[-50:]
        highs = list(self.price_bars['high'])[-50:]
        lows = list(self.price_bars['low'])[-50:]
        
        # 1. 計算 ATR (Average True Range)
        atr = self._calculate_atr(highs, lows, closes, period=14)
        atr_pct = (atr / closes[-1]) * 100 if closes[-1] > 0 and atr else 0
        
        # 2. 計算布林帶寬度
        bb_std = np.std(closes[-20:])
        bb_width_pct = (bb_std * 2 / closes[-1]) * 100 if closes[-1] > 0 else 0
        
        # 3. 計算最近 30 個 bar 的價格區間
        recent_closes = closes[-30:]
        price_range_pct = ((max(recent_closes) - min(recent_closes)) / closes[-1]) * 100 if closes[-1] > 0 else 0
        
        # 判斷震盪：ATR 過小 且 價格區間過窄
        is_churning = (atr_pct < 0.15 and price_range_pct < 0.3) or bb_width_pct < 0.4
        
        reason = ''
        if is_churning:
            if atr_pct < 0.15:
                reason = f'Low volatility: ATR={atr_pct:.3f}%'
            elif price_range_pct < 0.3:
                reason = f'Narrow range: {price_range_pct:.3f}%'
            elif bb_width_pct < 0.4:
                reason = f'Tight BB: {bb_width_pct:.3f}%'
        
        return {
            'is_churning': is_churning,
            'atr_pct': atr_pct,
            'bb_width_pct': bb_width_pct,
            'price_range_pct': price_range_pct,
            'reason': reason
        }
    
    def _analyze_trend_structure(self, now_ts: float, mid_price: Optional[float]) -> dict:
        highs = list(self.price_bars['high'])
        lows = list(self.price_bars['low'])
        closes = list(self.price_bars['close'])
        timestamps = list(self.price_bars['timestamp'])
        if not highs or len(highs) < 40 or mid_price is None:
            return {
                'swings': [],
                'direction': 'UNKNOWN',
                'persistence': 0,
                'structure_break': False,
                'pullback_ready': False,
                'atr': None
            }

        atr = self._calculate_atr(highs, lows, closes, self.structure_config['atr_period'])
        swings = self._compute_swings(highs, lows, timestamps, atr)
        direction, persistence = self._assess_structure_direction(swings)

        last_low = next((s for s in reversed(swings) if s['type'] == 'LOW'), None)
        last_high = next((s for s in reversed(swings) if s['type'] == 'HIGH'), None)
        structure_break = False
        break_side = None
        pullback_ready = False
        buffer_mult = self.structure_config['pullback_buffer_mult']
        if direction == 'BULLISH' and last_low:
            threshold = (atr or 0) * 0.35
            if mid_price < last_low['price'] - threshold:
                structure_break = True
                break_side = 'BELOW_LAST_LOW'
            elif atr:
                pullback_ready = (mid_price - last_low['price']) <= atr * buffer_mult
        elif direction == 'BEARISH' and last_high:
            threshold = (atr or 0) * 0.35
            if mid_price > last_high['price'] + threshold:
                structure_break = True
                break_side = 'ABOVE_LAST_HIGH'
            elif atr:
                pullback_ready = (last_high['price'] - mid_price) <= atr * buffer_mult

        if structure_break:
            self.structure_state['last_break_ts'] = now_ts
            self.structure_state['last_break_side'] = break_side

        meta = {
            'swings': swings,
            'direction': direction,
            'persistence': persistence,
            'structure_break': structure_break,
            'pullback_ready': pullback_ready,
            'atr': atr,
            'last_low': last_low['price'] if last_low else None,
            'last_high': last_high['price'] if last_high else None,
            'last_break_ts': self.structure_state.get('last_break_ts'),
            'last_break_side': self.structure_state.get('last_break_side')
        }

        self.structure_state['swings'] = deque(swings[-12:], maxlen=12)
        self.structure_state['direction'] = direction
        self.structure_state['persistence'] = persistence
        return meta

    def _build_market_snapshot(self) -> Optional[dict]:
        """彙整當前市場資訊，提供決策與風控使用"""
        if self.latest_price is None:
            return None

        obi_data = self.obi_calc.get_current_obi()
        orderbook = getattr(self.obi_calc, 'orderbook', {}) or {}
        bids = orderbook.get('bids') or []
        asks = orderbook.get('asks') or []
        if obi_data is None or not bids or not asks:
            return None

        bid_volume = sum(float(q) for _, q in bids)
        ask_volume = sum(float(q) for _, q in asks)
        total_volume = bid_volume + ask_volume
        obi = float(obi_data.get('obi', 0.0))

        spread_data = self.spread_depth.calculate_spread(bids, asks)
        depth_data = self.spread_depth.calculate_depth(bids, asks)
        signed_vol = self.signed_volume.calculate_signed_volume()
        volume_imbalance = self.signed_volume.calculate_volume_imbalance() or {}
        vpin_value = self.vpin_calc.calculate_vpin()
        vpin_level, vpin_action, _ = self.vpin_calc.assess_toxicity(vpin_value)

        best_bid_price, best_bid_qty = bids[0]
        best_ask_price, best_ask_qty = asks[0]
        mid_price = (best_bid_price + best_ask_price) / 2
        depth_sum = best_bid_qty + best_ask_qty
        microprice = (
            (best_bid_price * best_ask_qty + best_ask_price * best_bid_qty) / depth_sum
            if depth_sum > 0 else mid_price
        )
        microprice_pressure = ((microprice - mid_price) / mid_price) if mid_price else 0.0

        closes = list(self.price_bars['close'])
        recent_high = None
        recent_low = None
        range_position = None
        range_width_pct = 0.0
        short_ma = None
        long_ma = None
        trend_strength = 0.0
        late_entry_risk = 0.0
        range_extension_ratio = 0.0
        range_extension_direction = None
        if closes:
            window = closes[-30:] if len(closes) >= 30 else closes
            recent_high = max(window)
            recent_low = min(window)
            span = 0.0
            if mid_price and recent_high and recent_low and recent_high > recent_low:
                span = recent_high - recent_low
                range_width_pct = span / mid_price if mid_price else 0.0
                range_position = (mid_price - recent_low) / max(span, 1e-9)
                range_position = max(0.0, min(1.0, range_position))
                fair_mid = (recent_high + recent_low) / 2
                half_span = max(span / 2, mid_price * 0.0001)
                range_extension_ratio = abs(mid_price - fair_mid) / half_span if half_span else 0.0
                range_extension_ratio = float(min(range_extension_ratio, 2.0))
                late_entry_risk = min(1.0, range_extension_ratio / 1.1)
                range_extension_direction = 'ABOVE_RANGE' if mid_price > fair_mid else 'BELOW_RANGE'
        if len(closes) >= 10:
            short_window = closes[-10:]
            short_ma = float(np.mean(short_window))
        if len(closes) >= 30:
            long_window = closes[-30:]
            long_ma = float(np.mean(long_window))
        elif closes:
            long_ma = float(np.mean(closes))
        if short_ma and long_ma:
            if long_ma != 0:
                trend_strength = (short_ma - long_ma) / long_ma

        now_ts = time.time()
        obi_velocity = 0.0
        signed_volume_rate = 0.0
        if self.last_snapshot_meta and self.last_snapshot_time:
            dt = max(now_ts - self.last_snapshot_time, 1e-6)
            prev = self.last_snapshot_meta
            obi_velocity = (obi - prev.get('obi', 0.0)) / dt
            signed_volume_rate = (signed_vol - prev.get('signed_volume', 0.0)) / dt

        lookback = self.sniper_config['lookback_seconds']
        prices = [price for ts, price in self.price_history if now_ts - ts <= lookback]
        sniper_ready = len(prices) >= self.sniper_config['min_samples']
        momentum_pct = 0.0
        volatility_pct = 0.0
        if sniper_ready:
            first_price = prices[0]
            last_price = prices[-1]
            if first_price > 0:
                momentum_pct = ((last_price - first_price) / first_price) * 100
            returns = []
            for i in range(1, len(prices)):
                prev_price = prices[i-1]
                if prev_price:
                    returns.append(((prices[i] - prev_price) / prev_price) * 100)
            if returns:
                volatility_pct = float(np.std(returns))

        market_regime = 'UNKNOWN'
        regime_details: Dict[str, float] = {}
        consolidation_flag = False
        consolidation_reason = None
        if len(self.price_bars['close']) >= 60:
            df = pd.DataFrame({
                'high': list(self.price_bars['high'])[-60:],
                'low': list(self.price_bars['low'])[-60:],
                'close': list(self.price_bars['close'])[-60:],
                'volume': list(self.price_bars['volume'])[-60:]
            })
            regime_result = self.market_regime_detector.detect_regime(df, return_details=True)
            if isinstance(regime_result, tuple):
                regime_enum, metrics = regime_result
                market_regime = regime_enum.value if isinstance(regime_enum, MarketRegime) else str(regime_enum)
                if isinstance(metrics, dict):
                    regime_details = {
                        'ma_distance': metrics.get('ma_distance'),
                        'volatility': metrics.get('volatility'),
                        'volume_ratio': metrics.get('volume_ratio')
                    }
            else:
                market_regime = regime_result.value if isinstance(regime_result, MarketRegime) else str(regime_result)

            high_arr = np.array(df['high'], dtype=float)
            low_arr = np.array(df['low'], dtype=float)
            close_arr = np.array(df['close'], dtype=float)
            cons_state = self.consolidation_detector.is_consolidating(high_arr, low_arr, close_arr)
            consolidation_flag = cons_state.is_consolidating
            consolidation_reason = cons_state.reason

        snapshot = {
            'obi': obi,
            'spread_bps': float(spread_data.get('spread_bps', 0.0)),
            'spread_absolute': float(spread_data.get('absolute_spread', 0.0)),
            'vpin': float(vpin_value) if vpin_value is not None else 0.3,
            'vpin_level': vpin_level,
            'vpin_action': vpin_action,
            'funding_zscore': obi * 5,
            'signal_score': abs(obi) * 2,
            'momentum_pct': momentum_pct,
            'volatility_pct': volatility_pct,
            'sniper_ready': sniper_ready,
            'obi_velocity': obi_velocity,
            'signed_volume_proxy': signed_volume_rate,
            'signed_volume': signed_vol,
            'volume_imbalance': volume_imbalance.get('imbalance', 0.0),
            'microprice_pressure': microprice_pressure,
            'mid_price': mid_price,
            'microprice': microprice,
            'depth_imbalance': depth_data.get('depth_imbalance', 0.0),
            'total_depth': depth_data.get('total_depth', 0.0),
            'market_regime': market_regime,
            'regime_details': regime_details,
            'is_consolidating': consolidation_flag,
            'consolidation_reason': consolidation_reason,
            'trend_strength': trend_strength,
            'short_term_ma': short_ma,
            'long_term_ma': long_ma,
            'price': self.latest_price,  # 🆕 確保 price 存在於 snapshot 中
            'recent_swing_high': recent_high,
            'recent_swing_low': recent_low,
            'range_position': range_position,
            'range_width_pct': range_width_pct,
            'structure_buffer_default': 0.0015,
            'late_entry_risk': late_entry_risk,
            'range_extension_ratio': range_extension_ratio,
            'range_extension_direction': range_extension_direction
        }

        self._record_trend_features(now_ts, obi, snapshot['vpin'])
        trend_meta = self._compute_trend_state(now_ts)
        structure_meta = self._analyze_trend_structure(now_ts, mid_price)
        snapshot.update(trend_meta)
        snapshot['trend_structure'] = structure_meta
        snapshot['trend_persistence'] = structure_meta.get('persistence', 0)
        snapshot['structure_direction'] = structure_meta.get('direction')
        snapshot['structure_break'] = structure_meta.get('structure_break', False)
        snapshot['pullback_ready'] = structure_meta.get('pullback_ready', False)

        # 🆕 計算並加入技術指標 (RSI, Stoch, Bollinger)
        tech_indicators = self._calculate_technical_indicators()
        snapshot.update(tech_indicators)

        self.last_snapshot_meta = snapshot
        self.last_snapshot_time = now_ts
        return snapshot

    def _evaluate_sniper_edge(
        self,
        mode: TradingMode,
        config,
        snapshot: dict,
        direction: str
    ) -> dict:
        """計算狙擊模式的預估淨報酬，依 persona 風格微調門檻"""
        direction_sign = 1 if direction == 'LONG' else -1
        momentum_pct_dir = snapshot['momentum_pct'] * direction_sign

        style = self.mode_styles.get(mode, 'baseline')
        volatility_guard_multiplier = self.sniper_config['volatility_guard_multiplier']
        momentum_floor_pct = self.sniper_config['momentum_floor_pct']
        min_net_edge_pct = self.sniper_config['min_net_edge_pct']

        if style == 'trend':
            momentum_floor_pct *= 0.9  # 趨勢模式可接受稍低的瞬時動能
        elif style == 'scalper':
            volatility_guard_multiplier *= 0.8  # Scalper 更關注微結構，降低波動保護
            min_net_edge_pct *= 0.9
        elif style == 'reversion':
            volatility_guard_multiplier *= 1.15  # 逆勢需要更強保護
            momentum_floor_pct *= 0.7  # 但允許較低的動能進場
        elif style == 'breakout':
            momentum_floor_pct *= 1.2  # 突破需要更強動能確認
            min_net_edge_pct *= 1.1  # 提高淨報酬要求
        elif style == 'volume':
            volatility_guard_multiplier *= 0.9  # 量能突增時波動可接受
            min_net_edge_pct *= 0.95
        elif style == 'volatility':
            volatility_guard_multiplier *= 0.7  # 高波動本身就是策略一部分
            momentum_floor_pct *= 1.1  # 但需要更強動能

        volatility_guard = snapshot['volatility_pct'] * volatility_guard_multiplier
        expected_move_pct = max(0.0, momentum_pct_dir - volatility_guard)
        expected_move_levered_pct = expected_move_pct * config.leverage
        fee_cost_pct = 0.001 * config.leverage
        net_edge_pct = expected_move_levered_pct - fee_cost_pct
        eligible = (
            momentum_pct_dir >= momentum_floor_pct
            and net_edge_pct >= min_net_edge_pct
        )
        return {
            'eligible': eligible,
            'expected_move_levered_pct': expected_move_levered_pct,
            'net_edge_pct': net_edge_pct,
            'fee_cost_pct': fee_cost_pct,
            'momentum_pct_dir': momentum_pct_dir,
            'volatility_guard': volatility_guard,
            'momentum_floor_pct': momentum_floor_pct,
            'min_net_edge_pct': min_net_edge_pct,
            'reason': (
                None
                if eligible
                else f"Edge too weak: momentum {momentum_pct_dir:.3f}%, net {net_edge_pct:.2f}%"
            )
        }

    def _detect_breakout(self, current_price: float, snapshot: dict) -> dict:
        """🎯 M7 突破偵測：判斷是否突破近期高低點"""
        lookback = self.sniper_config['breakout_lookback_bars']
        threshold_pct = self.sniper_config['breakout_threshold_pct']
        
        highs = list(self.price_bars['high'])
        lows = list(self.price_bars['low'])
        
        if len(highs) < lookback or len(lows) < lookback:
            return {'breakout': False, 'direction': None, 'reason': 'Not enough bars'}
        
        recent_high = max(highs[-lookback:])
        recent_low = min(lows[-lookback:])
        
        # 突破確認：價格超過高點 + OBI 同向 + 量能配合
        obi = snapshot.get('obi', 0)
        is_breakout_up = (
            current_price > recent_high * (1 + threshold_pct)
            and obi > 0.15  # 買盤失衡
        )
        is_breakout_down = (
            current_price < recent_low * (1 - threshold_pct)
            and obi < -0.15  # 賣盤失衡
        )
        
        if is_breakout_up:
            return {
                'breakout': True,
                'direction': 'LONG',
                'level': recent_high,
                'distance_pct': (current_price - recent_high) / recent_high * 100
            }
        elif is_breakout_down:
            return {
                'breakout': True,
                'direction': 'SHORT',
                'level': recent_low,
                'distance_pct': (recent_low - current_price) / recent_low * 100
            }
        else:
            return {'breakout': False, 'direction': None, 'reason': 'No clear breakout'}

    def _detect_volume_surge(self, snapshot: dict) -> dict:
        """📦 M8 量能異常偵測：計算成交量 z-score"""
        window = self.sniper_config['volume_window_bars']
        threshold = self.sniper_config['volume_zscore_threshold']
        
        volumes = list(self.price_bars['volume'])
        if len(volumes) < window:
            return {'surge': False, 'zscore': 0, 'reason': 'Not enough volume data'}
        
        recent_volumes = volumes[-window:]
        mean_vol = np.mean(recent_volumes)
        std_vol = np.std(recent_volumes)
        
        if std_vol == 0:
            return {'surge': False, 'zscore': 0, 'reason': 'No volume variation'}
        
        current_vol = volumes[-1] if volumes else 0
        zscore = (current_vol - mean_vol) / std_vol
        
        # 結合 OBI 與動能判斷方向 (Offensive Upgrade)
        obi = snapshot.get('obi', 0)
        momentum = snapshot.get('momentum_pct', 0)
        is_surge = zscore >= threshold
        
        if is_surge:
            # 優先使用動能方向，若動能不明顯則使用 OBI
            if abs(momentum) > 0.02:
                direction = 'LONG' if momentum > 0 else 'SHORT'
            else:
                direction = 'LONG' if obi > 0 else 'SHORT'
                
            return {
                'surge': True,
                'zscore': zscore,
                'direction': direction,
                'current_vol': current_vol,
                'mean_vol': mean_vol,
                'momentum': momentum
            }
        else:
            return {'surge': False, 'zscore': zscore, 'reason': 'Volume below threshold'}

    def _detect_volatility_window(self, snapshot: dict) -> dict:
        """⚡ M9 波動窗口偵測：識別高波動期間"""
        window = self.sniper_config['volatility_window_bars']
        threshold_pct = self.sniper_config['volatility_threshold_pct']
        
        highs = list(self.price_bars['high'])
        lows = list(self.price_bars['low'])
        closes = list(self.price_bars['close'])
        
        if len(highs) < window or len(lows) < window or len(closes) < window:
            return {'high_volatility': False, 'atr_pct': 0, 'reason': 'Not enough bars'}
        
        # 計算 ATR (Average True Range)
        trs = []
        for i in range(1, window + 1):
            idx = -i
            high = highs[idx]
            low = lows[idx]
            prev_close = closes[idx - 1] if len(closes) > abs(idx) else closes[idx]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        
        atr = np.mean(trs)
        current_price = closes[-1] if closes else snapshot.get('price', 1)
        atr_pct = atr / current_price if current_price > 0 else 0
        
        is_high_vol = atr_pct >= threshold_pct
        
        if is_high_vol:
            return {
                'high_volatility': True,
                'atr_pct': atr_pct,
                'threshold_pct': threshold_pct,
                'amplification': atr_pct / threshold_pct
            }
        else:
            return {
                'high_volatility': False,
                'atr_pct': atr_pct,
                'reason': f'ATR {atr_pct:.4f}% < threshold {threshold_pct}%'
            }

    def _init_whale_flip_log_file(self):
        """建立鯨魚反轉分析 CSV"""
        headers = [
            'timestamp',
            'event_type',      # WARNING, REVERSAL, PREDICTION
            'current_dir',
            'potential_dir',
            'net_qty',
            'total_qty',
            'dominance',
            'obi',
            'price',
            'prediction_prob',
            'is_high_impact',
            'ma_20',
            'rsi_14',
            'stoch_k',
            'stoch_d',
            'boll_upper',
            'boll_lower',
            'price_change_since_last_flip'
        ]
        
        if not os.path.exists(self.whale_flip_log_file):
            with open(self.whale_flip_log_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(headers)

    def _calculate_technical_indicators(self) -> dict:
        """計算技術指標 (基於 price_bars tick 數據)"""
        if len(self.price_bars['close']) < 30:
            return {}
        
        try:
            df = pd.DataFrame({
                'close': list(self.price_bars['close']),
                'high': list(self.price_bars['high']),
                'low': list(self.price_bars['low'])
            })
            
            # MA 20
            df['ma_20'] = df['close'].rolling(window=20).mean()
            
            # Bollinger Bands (20, 2)
            df['std'] = df['close'].rolling(window=20).std()
            df['boll_upper'] = df['ma_20'] + (df['std'] * 2)
            df['boll_lower'] = df['ma_20'] - (df['std'] * 2)
            
            # RSI 14
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            rs = gain / loss
            df['rsi_14'] = 100 - (100 / (1 + rs))
            
            # StochRSI (14, 14, 3, 3)
            min_rsi = df['rsi_14'].rolling(window=14).min()
            max_rsi = df['rsi_14'].rolling(window=14).max()
            # Avoid division by zero
            denominator = max_rsi - min_rsi
            denominator = denominator.replace(0, 1e-9)
            
            df['stoch_k'] = (df['rsi_14'] - min_rsi) / denominator * 100
            df['stoch_d'] = df['stoch_k'].rolling(window=3).mean()
            
            latest = df.iloc[-1]
            return {
                'ma_20': float(latest['ma_20']) if not pd.isna(latest['ma_20']) else 0.0,
                'rsi_14': float(latest['rsi_14']) if not pd.isna(latest['rsi_14']) else 50.0,
                'stoch_k': float(latest['stoch_k']) if not pd.isna(latest['stoch_k']) else 50.0,
                'stoch_d': float(latest['stoch_d']) if not pd.isna(latest['stoch_d']) else 50.0,
                'boll_upper': float(latest['boll_upper']) if not pd.isna(latest['boll_upper']) else 0.0,
                'boll_lower': float(latest['boll_lower']) if not pd.isna(latest['boll_lower']) else 0.0
            }
        except Exception as e:
            print(f"Error calculating indicators: {e}")
            return {}

    def _analyze_whale_flip_risk(self, net_qty, total_qty, dominance, long_qty, short_qty):
        """分析鯨魚反轉風險與衝擊預測"""
        whale_config = self.MODE_CONFIGS.get(TradingMode.M_WHALE_WATCHER)
        if not whale_config: return
        
        pred_config = getattr(whale_config, 'whale_flip_prediction', {})
        if not pred_config.get('enabled', False): return
        
        current_signal = self.large_trade_signal
        current_dir = current_signal.get('direction', 'NONE')
        
        # 判斷潛在方向
        potential_dir = 'LONG' if net_qty > 0 else 'SHORT'
        
        now_ts = time.time()
        
        # 取得 OBI
        obi = self.obi_calc.get_obi() if hasattr(self, 'obi_calc') else 0
        
        # 計算技術指標
        indicators = self._calculate_technical_indicators()
        
        # 計算距離上次反轉的價格變化
        price_change_since_last = 0.0
        if hasattr(self, 'last_flip_price') and self.last_flip_price > 0:
            price_change_since_last = (self.latest_price - self.last_flip_price) / self.last_flip_price * 100
        
        event_type = "MONITOR"
        prob = 0.0
        is_high_impact = False
        should_print = False
        
        # 1. 反轉預警：方向改變 (且原本有方向)
        if current_dir != 'NONE' and potential_dir != current_dir:
            # 🆕 過濾雜訊：只有當新方向的集中度 > reversal_sensitivity 時才視為反轉
            # 使用動態參數控制靈敏度 (預設 0.3)
            reversal_threshold = pred_config.get('reversal_sensitivity', 0.3)
            
            if dominance < reversal_threshold:
                event_type = "DECAY"
                should_print = True
            else:
                event_type = "WARNING"
                should_print = True
                
                # 2. 衝擊預測 (>1% 機率)
                impact_thresholds = pred_config.get('high_impact_thresholds', {})
                high_qty = abs(net_qty) >= impact_thresholds.get('net_qty', 10.0)
                high_dom = dominance >= impact_thresholds.get('dominance', 0.8)
                
                obi_aligned = (obi > 0.15 and potential_dir == 'LONG') or (obi < -0.15 and potential_dir == 'SHORT')
                
                prob = 0.3 # 基礎機率
                if high_qty: prob += 0.25
                if high_dom: prob += 0.25
                if obi_aligned: prob += 0.2
                
                if prob >= 0.5:
                    event_type = "PREDICTION"
                    is_high_impact = True
        
        # 3. 衰退預警：同方向但集中度下降
        elif current_dir != 'NONE' and potential_dir == current_dir:
            warning_dom = pred_config.get('warning_threshold_dominance', 0.65)
            if dominance < warning_dom:
                event_type = "DECAY"
                should_print = True

        # 頻率限制僅用於 Print，不影響 Log
        last_warn = getattr(self, 'last_flip_warning_time', 0)
        if should_print and (now_ts - last_warn >= 5.0):
            self.last_flip_warning_time = now_ts
            if event_type == "WARNING" or event_type == "PREDICTION":
                self.last_flip_price = self.latest_price
                print(f"   ⚠️  [M🐳 反轉預警] 資金流向改變! {current_dir} -> {potential_dir} (Dom: {dominance:.2f}, Net: {abs(net_qty):.1f})")
                if is_high_impact:
                    print(f"   🔮 [M🐳 衝擊預測] 預計波動 > 1% 機率: {prob*100:.0f}% (OBI: {obi:.2f})")
            elif event_type == "DECAY":
                # 區分是方向改變導致的衰退，還是同方向衰退
                if potential_dir != current_dir:
                    reversal_threshold = pred_config.get('reversal_sensitivity', 0.3)
                    print(f"   📉 [M🐳 動能衰退] 趨勢中立化 (Dom: {dominance:.2f} < {reversal_threshold}, Net: {abs(net_qty):.1f})")
                else:
                    print(f"   📉 [M🐳 衰退預警] 集中度下降 ({dominance:.2f} < {warning_dom}, Net: {abs(net_qty):.1f})")

        # 寫入 CSV (即時寫入)
        try:
            with open(self.whale_flip_log_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().isoformat(),
                    event_type,
                    current_dir,
                    potential_dir,
                    f"{net_qty:.4f}",
                    f"{total_qty:.4f}",
                    f"{dominance:.4f}",
                    f"{obi:.4f}",
                    f"{self.latest_price:.2f}",
                    f"{prob:.2f}",
                    is_high_impact,
                    f"{indicators.get('ma_20', 0):.2f}",
                    f"{indicators.get('rsi_14', 0):.2f}",
                    f"{indicators.get('stoch_k', 0):.2f}",
                    f"{indicators.get('stoch_d', 0):.2f}",
                    f"{indicators.get('boll_upper', 0):.2f}",
                    f"{indicators.get('boll_lower', 0):.2f}",
                    f"{price_change_since_last:.4f}"
                ])
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            print(f"Error writing to whale flip log: {e}")

    async def connect_websocket(self):
        """連接 WebSocket 獲取即時訂單簿（帶自動重連）"""
        print("🔌 連接 Binance WebSocket...")
        
        retry_count = 0
        max_retries = 10
        
        while datetime.now() < self.end_time:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=10
                ) as ws:
                    if retry_count > 0:
                        print(f"✅ WebSocket 重新連接成功\n")
                    else:
                        print("✅ WebSocket 已連接\n")
                    
                    retry_count = 0  # 重置重試計數
                    
                    while datetime.now() < self.end_time:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                            data = json.loads(msg)
                            stream = data.get('stream', '')
                            payload = data.get('data', {})
                            if not stream or not payload:
                                continue

                            if 'bookTicker' in stream:
                                best_bid = float(payload.get('b', 0))
                                best_ask = float(payload.get('a', 0))
                                if best_bid and best_ask:
                                    self.latest_price = (best_bid + best_ask) / 2
                                    event_time = payload.get('E') or payload.get('u')
                                    if event_time:
                                        self.orderbook_timestamp = datetime.fromtimestamp(
                                            event_time / 1000
                                        ).isoformat()
                                    self._record_price(self.latest_price)
                                    self._update_price_bars(best_bid, best_ask)
                            elif 'depth' in stream:
                                bids = [[float(p), float(q)] for p, q in payload.get('b', [])[:20]]
                                asks = [[float(p), float(q)] for p, q in payload.get('a', [])[:20]]
                                if bids and asks:
                                    self.obi_calc.update_orderbook(bids, asks)
                                    self.spread_depth.update(bids, asks)
                                    self.orderbook_data = {
                                        'bids': bids,
                                        'asks': asks,
                                        'timestamp': payload.get('E')
                                    }
                            elif 'aggTrade' in stream:
                                trade = {
                                    'p': payload.get('p'),
                                    'q': payload.get('q'),
                                    'T': payload.get('T'),
                                    'm': payload.get('m'),
                                    'isBuyerMaker': payload.get('m')
                                }
                                self.signed_volume.add_trade(trade)
                                self.vpin_calc.process_trade(trade)
                                try:
                                    trade_qty = float(payload.get('q', 0.0))
                                    self.pending_volume += trade_qty
                                    
                                    # 🆕 大單偵測 - 先全部記錄下來，之後用「多空總和」決定淨方向
                                    if trade_qty >= self.large_trade_threshold:
                                        is_buyer_maker = payload.get('m')  # True=賣單吃買盤(偏空), False=買單吃賣盤(偏多)
                                        direction = 'SHORT' if is_buyer_maker else 'LONG'
                                        now_ts = time.time()
                                        self.recent_large_trades.append({
                                            'time': now_ts,
                                            'qty': trade_qty,
                                            'price': float(payload.get('p', 0)),
                                            'direction': direction
                                        })
                                        self.large_trade_history.append({
                                            'time': now_ts,
                                            'qty': trade_qty,
                                            'direction': direction
                                        })

                                        # 只保留最近 large_trade_agg_window 秒內的大單
                                        cutoff = now_ts - self.large_trade_agg_window
                                        while self.recent_large_trades and self.recent_large_trades[0]['time'] < cutoff:
                                            self.recent_large_trades.popleft()

                                        # 🆕 取得動態參數 (M_WHALE_WATCHER)
                                        whale_cfg = self.mode_config_manager.get_config('M_WHALE_WATCHER') or {}
                                        whale_rules = whale_cfg.get('entry_rules', {}).get('whale_dominance', {})
                                        min_count = whale_rules.get('min_count', 5)
                                        min_total_qty = whale_rules.get('min_total_qty', 3.0)

                                        # 檢查樣本數是否足夠
                                        if len(self.recent_large_trades) < min_count:
                                            continue  # 樣本數不足，跳過訊號發出

                                        # 計算這段時間內多空總和
                                        long_qty = sum(t['qty'] for t in self.recent_large_trades if t['direction'] == 'LONG')
                                        short_qty = sum(t['qty'] for t in self.recent_large_trades if t['direction'] == 'SHORT')
                                        net_qty = long_qty - short_qty

                                        # 確保至少有 min_total_qty BTC 的總量才發出訊號
                                        total_qty = long_qty + short_qty
                                        if total_qty < min_total_qty:
                                            continue  # 總量太小，不可信

                                        # 只有當一邊明顯佔優時才發出方向訊號
                                        dominance_ratio = abs(net_qty) / total_qty if total_qty > 0 else 0
                                        
                                        # 計算大單加權平均價格 (VWAP of whales)
                                        vwap_sum = sum(t['price'] * t['qty'] for t in self.recent_large_trades)
                                        vwap_qty = sum(t['qty'] for t in self.recent_large_trades)
                                        whale_vwap = vwap_sum / vwap_qty if vwap_qty > 0 else float(payload.get('p', 0))

                                        # 🆕 M🐳 反轉預警與衝擊預測
                                        self._analyze_whale_flip_risk(net_qty, total_qty, dominance_ratio, long_qty, short_qty)
                                        
                                        if dominance_ratio >= 0.6:  # 至少 60% 以上集中在同一方向
                                            net_direction = 'LONG' if net_qty > 0 else 'SHORT'
                                            self.large_trade_signal = {
                                                'direction': net_direction,
                                                'timestamp': now_ts,
                                                'net_qty': net_qty,
                                                'dominance_ratio': dominance_ratio,
                                                'long_qty': long_qty,
                                                'short_qty': short_qty,
                                                'total_qty': total_qty,
                                                'whale_vwap': whale_vwap  # 🐳 鯨魚成本價
                                            }
                                            print(
                                                f"   🐋 大單淨方向訊號: {net_direction} | 多={long_qty:.2f} BTC, 空={short_qty:.2f} BTC, "
                                                f"淨量={net_qty:.2f} BTC, 集中度={dominance_ratio:.2f}"
                                            )
                                except (TypeError, ValueError):
                                    pass
                            
                        except asyncio.TimeoutError:
                            continue
                        except websockets.exceptions.ConnectionClosed:
                            print("⚠️  WebSocket 連接關閉，準備重連...")
                            break
                        except Exception as e:
                            print(f"⚠️  接收數據錯誤: {e}")
                            continue
                            
            except Exception as e:
                retry_count += 1
                if retry_count >= max_retries:
                    print(f"❌ WebSocket 連接失敗次數過多，放棄重連")
                    break
                
                wait_time = min(retry_count * 2, 10)  # 最多等 10 秒
                print(f"⚠️  WebSocket 連接失敗 (嘗試 {retry_count}/{max_retries}): {e}")
                print(f"⏳ {wait_time} 秒後重試...")
                await asyncio.sleep(wait_time)
    
    def make_decision(self, mode: TradingMode, snapshot: Optional[dict] = None) -> dict:
        """為特定模式生成交易決策（整合 Hybrid + Sniper 模式）"""
        if snapshot is None:
            snapshot = self._build_market_snapshot()
        if snapshot is None:
            return {'action': 'HOLD', 'reason': 'No market snapshot'}

        is_sandbox = (mode == TradingMode.M6_SIGNAL_SANDBOX)
        allow_relaxed = (mode != TradingMode.M0_ULTRA_SAFE)

        style = self.mode_styles.get(mode, 'baseline')
        config = self.MODE_CONFIGS[mode]

        market_data = {
            'obi': snapshot.get('obi', 0.0),
            'spread_bps': snapshot.get('spread_bps', 0.0),
            'spread_absolute': snapshot.get('absolute_spread', 0.0),
            'vpin': snapshot.get('vpin', 0.0),
            'vpin_level': snapshot.get('vpin_level'),
            'vpin_action': snapshot.get('vpin_action'),
            'funding_zscore': snapshot.get('funding_zscore', 0.0),
            'signal_score': snapshot.get('signal_score', 0.0),
            'momentum_pct': snapshot.get('momentum_pct', 0.0),
            'volatility_pct': snapshot.get('volatility_pct', 0.0),
            'obi_velocity': snapshot.get('obi_velocity', 0.0),
            'signed_volume': snapshot.get('signed_volume', 0.0),
            'volume_imbalance': snapshot.get('volume_imbalance', 0.0),
            'depth_imbalance': snapshot.get('depth_imbalance', 0.0),
            'microprice_pressure': snapshot.get('microprice_pressure', 0.0),
            'mid_price': snapshot.get('mid_price'),
            'microprice': snapshot.get('microprice'),
            'trend_strength': snapshot.get('trend_strength', 0.0),
            'trend_state': snapshot.get('trend_state'),
            'trend_confidence': snapshot.get('trend_confidence', 0.0),
            'trend_persistence': snapshot.get('trend_persistence', 0),
            'structure_direction': snapshot.get('direction'),
            'structure_break': snapshot.get('structure_break', False),
            'pullback_ready': snapshot.get('pullback_ready', False),
            'late_entry_risk': snapshot.get('late_entry_risk', 0.0),
            'range_extension_ratio': snapshot.get('range_extension_ratio', 0.0),
            'range_extension_direction': snapshot.get('range_extension_direction'),
            'range_position': snapshot.get('range_position'),
            'range_width_pct': snapshot.get('range_width_pct', 0.0),
            'recent_swing_high': snapshot.get('recent_swing_high'),
            'recent_swing_low': snapshot.get('recent_swing_low'),
            'structure_buffer_pct': snapshot.get('structure_buffer_default', 0.0015),
            'market_regime': snapshot.get('market_regime'),
            'is_consolidating': snapshot.get('is_consolidating'),
            'consolidation_reason': snapshot.get('consolidation_reason'),
            'timestamp': self.orderbook_timestamp,
            'mode_style': style
        }

        pressure_obj: Optional[LiquidationPressureSnapshot] = snapshot.get('liquidation_pressure_obj')
        if pressure_obj:
            market_data['liquidation_pressure'] = pressure_obj.to_dict()
            market_data['liquidation_bias'] = pressure_obj.directional_bias
            market_data['liquidation_bias_confidence'] = pressure_obj.bias_confidence
            market_data['liquidation_levels'] = {
                'long': pressure_obj.long_level.value,
                'short': pressure_obj.short_level.value
            }

        pressure_alignment: Optional[str] = None
        pressure_confidence = 0.0
        pressure_support_level: Optional[PressureLevel] = None
        if pressure_obj and pressure_obj.directional_bias in {'LONG', 'SHORT'}:
            pressure_alignment = pressure_obj.directional_bias
            pressure_confidence = pressure_obj.bias_confidence
            pressure_support_level = (
                pressure_obj.long_level if pressure_alignment == 'SHORT' else pressure_obj.short_level
            )
            market_data['liquidation_alignment'] = pressure_alignment

        lp_whale_trigger: Optional[dict] = None

        def finalize(decision: dict) -> dict:
            decision.setdefault('market_data', market_data)
            self._log_signal_snapshot(mode, decision, snapshot)
            return decision

        if style in {'direction_probe_long', 'direction_probe_short'}:
            target_direction = 'LONG' if style == 'direction_probe_long' else 'SHORT'
            return finalize(self._make_direction_probe_decision(mode, target_direction, market_data))

        trend_strength = market_data.get('trend_strength', 0.0)
        trend_state = market_data.get('trend_state')
        trend_confidence = market_data.get('trend_confidence', 0.0)
        trend_persistence = market_data.get('trend_persistence', 0)
        structure_break = market_data.get('structure_break', False)
        late_entry_risk = market_data.get('late_entry_risk', 0.0)
        range_position = market_data.get('range_position')
        range_width_pct = market_data.get('range_width_pct', 0.0)
        sniper_ready = snapshot.get('sniper_ready', False)
        obi = market_data['obi']
        regime_value = snapshot.get('market_regime')
        regime_policy = self.regime_mode_policies.get(regime_value)
        if regime_policy:
            market_data['regime_bias'] = regime_policy.get('bias')
            market_data['regime_value'] = regime_value
            if mode not in regime_policy['allow']:
                return finalize({'action': 'HOLD', 'reason': regime_policy.get('reason', f'Regime gating: {regime_value}')})
        gating_rule = self.trend_gating_rules.get(mode)
        if gating_rule and not is_sandbox:
            allowed_states = gating_rule.get('allowed_states') or set()
            min_conf = gating_rule.get('min_confidence', 0.0)
            if not trend_state or trend_state not in allowed_states or trend_confidence < min_conf:
                reason = (
                    gating_rule.get('label') or 'Trend gating active'
                )
                detail = (
                    f"trend_state={trend_state}, conf={trend_confidence:.2f}, "
                    f"required={allowed_states}, min_conf={min_conf:.2f}"
                )
                market_data['trend_gate_reason'] = detail
                return finalize({'action': 'HOLD', 'reason': reason})
        
        # M8 Volume Sniper 可能會覆蓋方向
        action_override = None
        
        if style == 'trend' and not is_sandbox:
            if structure_break:
                return finalize({'action': 'HOLD', 'reason': 'Trend Sniper avoids structure break'})
            if trend_persistence < self.structure_config['persistence_required']:
                return finalize({'action': 'HOLD', 'reason': 'Trend Sniper waiting for structure persistence'})
            if abs(trend_strength) < 0.03:
                return finalize({'action': 'HOLD', 'reason': 'Trend Sniper waiting for directional edge'})
            if snapshot.get('is_consolidating'):
                return finalize({'action': 'HOLD', 'reason': 'Trend Sniper avoids consolidation'})
        if style == 'scalper' and not is_sandbox:
            micro_impulse = abs(market_data['obi_velocity']) + abs(market_data['microprice_pressure'])
            if micro_impulse < 0.0004:
                return finalize({'action': 'HOLD', 'reason': 'Scalper waiting for micro impulse'})
        if style == 'reversion' and not is_sandbox:
            # 取得動態參數
            entry_rules = getattr(config, 'entry_rules', {})
            dead_market_config = entry_rules.get('dead_market', {})
            
            # 🆕 死魚盤網格模式 (Dead Market Grid)
            churn_info = self._detect_market_churn()
            
            # 預設參數 (如果 config 沒讀到)
            dm_enabled = dead_market_config.get('enabled', False)
            atr_threshold = dead_market_config.get('atr_threshold_pct', 0.05)
            grid_buy_level = dead_market_config.get('grid_buy_level', 0.15)
            grid_sell_level = dead_market_config.get('grid_sell_level', 0.85)
            maker_fee_enabled = dead_market_config.get('maker_fee_enabled', True)
            
            is_dead_market = churn_info['atr_pct'] < atr_threshold
            
            if dm_enabled and is_dead_market:
                # 死魚盤模式：只在極端位置掛 Maker 單
                if range_position is not None:
                    if range_position < grid_buy_level: # 接近下緣
                        return finalize({
                            'action': 'LONG', 
                            'reason': f'M🐟 Dead Market Grid Buy (ATR={churn_info["atr_pct"]:.4f}% < {atr_threshold}%)',
                            'is_maker': maker_fee_enabled, # 標記為 Maker
                            'confidence': 0.8 # 高信心因為是區間邊緣
                        })
                    elif range_position > grid_sell_level: # 接近上緣
                        return finalize({
                            'action': 'SHORT', 
                            'reason': f'M🐟 Dead Market Grid Sell (ATR={churn_info["atr_pct"]:.4f}% < {atr_threshold}%)',
                            'is_maker': maker_fee_enabled, # 標記為 Maker
                            'confidence': 0.8
                        })
                    else:
                        return finalize({'action': 'HOLD', 'reason': 'M🐟 Dead Market Grid waiting for extremes'})
                else:
                     return finalize({'action': 'HOLD', 'reason': 'M🐟 Dead Market Grid waiting for range info'})

            # 正常 Reversion 邏輯 (Taker)
            if range_position is None or range_width_pct < 0.0015:
                return finalize({'action': 'HOLD', 'reason': 'Reversion waiting for wide range'})
            if 0.22 < range_position < 0.78:
                return finalize({'action': 'HOLD', 'reason': 'Reversion waiting for extremes'})
        
        # 🎯 M7 Breakout Sniper 特殊處理
        if style == 'breakout':
            if structure_break:
                return finalize({'action': 'HOLD', 'reason': 'Breakout Sniper reset after structure break'})
            breakout_info = self._detect_breakout(self.latest_price, snapshot)
            if not breakout_info['breakout']:
                return finalize({'action': 'HOLD', 'reason': f"M7 Breakout: {breakout_info.get('reason', 'waiting')}"})
            market_data['breakout_info'] = breakout_info
            # 突破方向必須與 OBI 一致
            if breakout_info['direction'] != ('LONG' if obi > 0 else 'SHORT'):
                return finalize({'action': 'HOLD', 'reason': 'M7 Breakout direction mismatch with OBI'})
        
        # 📦 M8 Volume Sniper 特殊處理 (Offensive Mode)
        if style == 'volume':
            # M8 忽略結構破壞，因為量能突增往往伴隨結構改變
            # if structure_break:
            #    return finalize({'action': 'HOLD', 'reason': 'Volume Sniper paused on structure break'})
            
            volume_info = self._detect_volume_surge(snapshot)
            if not volume_info['surge']:
                return finalize({'action': 'HOLD', 'reason': f"M8 Volume: {volume_info.get('reason', 'waiting')}"})
            market_data['volume_surge_info'] = volume_info
            # 強制使用量能判斷的方向
            action_override = volume_info['direction']
            
            # M8 進入攻擊模式，標記為 relaxed 以繞過部分防守濾網
            allow_relaxed = True
        
        # ⚡ M9 Volatility Sniper 特殊處理
        if style == 'volatility':
            volatility_info = self._detect_volatility_window(snapshot)
            if not volatility_info['high_volatility']:
                return finalize({'action': 'HOLD', 'reason': f"M9 Volatility: {volatility_info.get('reason', 'waiting')}"})
            market_data['volatility_window_info'] = volatility_info
            # 高波動期需要更強的信號確認
            if abs(obi) < 0.2:
                return finalize({'action': 'HOLD', 'reason': 'M9 needs stronger OBI in high volatility'})
        
        # 🐳 M_WHALE_WATCHER 特殊處理 - 純粹跟隨大單集中度
        if style == 'whale':
            config = self.MODE_CONFIGS[mode]
            whale_signal = self.large_trade_signal
            dominance = whale_signal.get('dominance_ratio', 0.0)
            net_direction = whale_signal.get('direction', None)
            net_qty = whale_signal.get('net_qty', 0.0)
            
            # 🆕 Phase 5.1: 檢查反轉頻率限制（防刷單洗盤）
            tracker = self.whale_reversal_tracker.get(mode)
            if tracker:
                now = time.time()
                
                # 清理 30 分鐘前的反轉記錄
                tracker['reversal_timestamps'] = [
                    ts for ts in tracker['reversal_timestamps'] 
                    if now - ts < 1800  # 30 分鐘
                ]
                tracker['reversal_count'] = len(tracker['reversal_timestamps'])
                
                # 檢查是否在懲罰冷卻中
                if now < tracker['penalty_cooldown']:
                    remaining = tracker['penalty_cooldown'] - now
                    return finalize({
                        'action': 'HOLD', 
                        'reason': f'M🐳 Reversal penalty: {remaining/60:.1f}min remaining (flipped {tracker["reversal_count"]} times)'
                    })
                
                # 檢查反轉次數
                if net_direction and net_direction != tracker['last_direction'] and tracker['last_direction'] is not None:
                    # 即將反轉
                    if tracker['reversal_count'] >= 2:  # 30 分鐘內已反轉 2 次
                        # 指數退避：2 分鐘 → 5 分鐘 → 10 分鐘
                        penalty_minutes = [2, 5, 10, 20][min(tracker['reversal_count'] - 2, 3)]
                        tracker['penalty_cooldown'] = now + (penalty_minutes * 60)
                        
                        return finalize({
                            'action': 'HOLD', 
                            'reason': f'M🐳 Too many reversals: {tracker["reversal_count"]} flips in 30min, cooldown {penalty_minutes}min'
                        })
            
            # 🆕 Phase 5.2: 震盪過濾（防止在震盪區間交易）
            churn_info = self._detect_market_churn()
            
            # 🆕 Phase 5.3: 波動率硬地板 (Hard Volatility Floor)
            # 如果 ATR < 手續費成本，無論訊號多強一律不交易
            entry_rules = getattr(config, 'entry_rules', {})
            anti_churn_config = entry_rules.get('anti_churn', {})
            volatility_hard_floor_pct = anti_churn_config.get('volatility_hard_floor_pct', 0.05)
            
            if churn_info['atr_pct'] < volatility_hard_floor_pct:
                return finalize({
                    'action': 'HOLD',
                    'reason': f'M🐳 Volatility Hard Floor: ATR={churn_info["atr_pct"]:.4f}% < {volatility_hard_floor_pct}% (Dead Market)'
                })

            if churn_info['is_churning']:
                # 震盪時提高進場門檻 (從 Config 讀取，預設 0.8)
                whale_rules = getattr(config, 'entry_rules', {}).get('whale_dominance', {})
                dominance_min_churn = whale_rules.get('min_churn', 0.8)
                
                if dominance < dominance_min_churn:
                    return finalize({
                        'action': 'HOLD',
                        'reason': f'M🐳 Market churning: {churn_info["reason"]}, need dominance > {dominance_min_churn} (current {dominance:.2f})'
                    })
                market_data['churn_detected'] = True
                market_data['churn_info'] = churn_info
            
            # 取得動態參數
            entry_rules = getattr(config, 'entry_rules', {})
            ignore_cons_config = entry_rules.get('ignore_consolidation', True)
            
            # 🆕 OBI & VPIN 濾網
            obi_rules = entry_rules.get('obi', {})
            vpin_rules = entry_rules.get('vpin', {})
            
            current_obi = snapshot['obi']
            current_vpin = snapshot['vpin']
            
            # VPIN 檢查 (過濾高毒性流動性)
            vpin_max = vpin_rules.get('max', 1.0)
            if current_vpin > vpin_max:
                 return finalize({'action': 'HOLD', 'reason': f'M🐳 VPIN too high: {current_vpin:.2f}'})
            
            # 智能判斷：是否忽略盤整濾網？
            if ignore_cons_config is True:
                # 舊模式：總是忽略 (Aggressive)
                allow_relaxed = True
            elif ignore_cons_config == "AUTO":
                # 🆕 智能模式 (Smart): 只有當訊號夠強時，才無視盤整
                # 條件 1: 集中度極高 (>= 0.85)
                # 條件 2: 淨量極大 (>= 10 BTC)
                is_strong_signal = (dominance >= 0.85) or (abs(net_qty) >= 10.0)
                
                if is_strong_signal:
                    allow_relaxed = True
                    market_data['whale_breakout_reason'] = f"Strong Signal (Dom={dominance:.2f}, Qty={net_qty:.1f})"
                else:
                    # 訊號不夠強，且處於盤整 -> 乖乖防守
                    allow_relaxed = False
            # else False: 嚴格遵守盤整濾網 (Defensive)

            # 取得動態參數
            entry_rules = getattr(config, 'entry_rules', {})
            whale_rules = entry_rules.get('whale_dominance', {})
            ultra_burst_min = whale_rules.get('ultra_burst_min', 0.9)
            entry_min = whale_rules.get('min', 0.6)

            # UltraBurst 模式：集中度極端高時，直接強制進場
            if dominance >= ultra_burst_min and net_direction:
                action = net_direction
                reason = (
                    f"M🐳 UltraBurst {action}: dominance={dominance:.2f}, "
                    f"net_qty={whale_signal.get('net_qty', 0):.2f} BTC"
                )
                confidence = min(1.0, max(0.75, dominance))
                market_data['whale_dominance'] = dominance
                market_data['whale_net_qty'] = whale_signal.get('net_qty', 0)
                market_data['whale_mode'] = 'ULTRA_BURST'
                return finalize({'action': action, 'reason': reason, 'confidence': confidence})

            # 一般情況：進場條件：集中度 >= entry_min
            if dominance < entry_min:
                return finalize({'action': 'HOLD', 'reason': f'M🐳 Whale: dominance {dominance:.2f} < {entry_min}'})
            
            if not net_direction:
                return finalize({'action': 'HOLD', 'reason': 'M🐳 Whale: no clear direction'})
            
            # 🆕 Phase 5.3: 大單持續性檢測（防假突破）
            persistence_check = self._check_whale_persistence(net_direction, time.time())
            if not persistence_check['is_persistent']:
                return finalize({
                    'action': 'HOLD',
                    'reason': f'M🐳 Signal not persistent: {persistence_check["reason"]}'
                })
            market_data['whale_persistence'] = persistence_check
            
            # 🆕 OBI 方向確認 (避免逆勢接刀)
            if net_direction == 'LONG':
                obi_min = obi_rules.get('min_long', -1.0)
                if current_obi < obi_min:
                    return finalize({'action': 'HOLD', 'reason': f'M🐳 OBI too bearish for LONG: {current_obi:.2f} < {obi_min}'})
            elif net_direction == 'SHORT':
                obi_max = obi_rules.get('max_short', 1.0)
                if current_obi > obi_max:
                    return finalize({'action': 'HOLD', 'reason': f'M🐳 OBI too bullish for SHORT: {current_obi:.2f} > {obi_max}'})

            # 🆕 Phase 5.4: 價格動能確認（強化版）- 避免假突破
            price_conf_pct = whale_rules.get('price_confirmation_pct', 0.05)  # 預設 0.05%
            momentum_min_pct = whale_rules.get('price_momentum_min_pct', 0.02)  # 30 秒內至少 0.02%
            whale_vwap = whale_signal.get('whale_vwap', 0.0)
            current_price = snapshot['price']
            
            # 檢查價格動能（30 秒內的變動）
            if len(self.price_history) >= 30:
                price_30s_ago = None
                now_ts = time.time()
                for ts, price in self.price_history:
                    if now_ts - ts >= 30:
                        price_30s_ago = price
                        break
                
                if price_30s_ago:
                    momentum_pct = abs((current_price - price_30s_ago) / price_30s_ago) * 100
                    
                    if momentum_pct < momentum_min_pct:
                        return finalize({
                            'action': 'HOLD',
                            'reason': f'M🐳 Momentum too weak: {momentum_pct:.3f}% < {momentum_min_pct}% (no price follow-through)'
                        })
                    market_data['price_momentum_30s'] = momentum_pct
            
            # 價格確認：等待突破鯨魚 VWAP
            if price_conf_pct > 0 and whale_vwap > 0:
                if net_direction == 'LONG':
                    required_price = whale_vwap * (1 + price_conf_pct/100)
                    if current_price < required_price:
                        return finalize({'action': 'HOLD', 'reason': f'M🐳 Price wait: {current_price:.1f} < {required_price:.1f} (VWAP {whale_vwap:.1f})'})
                elif net_direction == 'SHORT':
                    required_price = whale_vwap * (1 - price_conf_pct/100)
                    if current_price > required_price:
                        return finalize({'action': 'HOLD', 'reason': f'M🐳 Price wait: {current_price:.1f} > {required_price:.1f} (VWAP {whale_vwap:.1f})'})

            # 直接使用大單方向
            action = net_direction
            reason = f"M🐳 Whale Follow {action}: dominance={dominance:.2f}, net_qty={whale_signal.get('net_qty', 0):.2f} BTC"
            confidence = min(dominance, 1.0)
            market_data['whale_dominance'] = dominance
            market_data['whale_net_qty'] = whale_signal.get('net_qty', 0)
            
            # 🆕 記錄反轉（如果方向改變）
            if tracker and action != tracker['last_direction'] and tracker['last_direction'] is not None:
                tracker['reversal_timestamps'].append(time.time())
                tracker['last_direction'] = action
                print(f"   🔄 [M🐳 Direction Change] {tracker['last_direction']} -> {action} (Total flips in 30min: {len(tracker['reversal_timestamps'])})")
            elif tracker and tracker['last_direction'] is None:
                tracker['last_direction'] = action
            
            # 跳過後續所有檢查，直接返回
            return finalize({'action': action, 'reason': reason, 'confidence': confidence})

        if style == 'lp_whale_burst':
            allow_relaxed = True
            sniper_ready = True
            market_data['lp_whale_mode'] = True
            lp_whale_trigger, hold_reason = self._evaluate_lp_whale_burst_signal(mode, snapshot, pressure_obj, obi)
            if lp_whale_trigger is None:
                return finalize({'action': 'HOLD', 'reason': hold_reason})
            market_data['lp_whale_signal'] = {
                'dominance': lp_whale_trigger['dominance'],
                'net_qty': lp_whale_trigger['net_qty'],
                'long_score': lp_whale_trigger['long_score'],
                'short_score': lp_whale_trigger['short_score'],
                'diff': lp_whale_trigger['diff'],
                'thresholds': lp_whale_trigger['thresholds']
            }
            if lp_whale_trigger.get('size_multiplier'):
                market_data['position_size_multiplier'] = lp_whale_trigger['size_multiplier']

        if not sniper_ready and not allow_relaxed:
            return finalize({'action': 'HOLD', 'reason': 'Sniper momentum not ready'})
        if not sniper_ready and allow_relaxed:
            market_data['sniper_relaxed'] = True
        
        open_positions = [o for o in self.orders[mode] if not o.is_blocked and o.exit_time is None]
        if open_positions:
            return finalize({'action': 'HOLD', 'reason': 'Already have position'})
        
        now_ts = time.time()
        last_entry = self.last_entry_time[mode]
        cooldown = self.entry_cooldown[mode]
        
        # 🆕 檢查連虧冷卻
        if now_ts < self.loss_cooldown_until[mode]:
            remaining = self.loss_cooldown_until[mode] - now_ts
            return finalize({'action': 'HOLD', 'reason': f'Loss cooldown: {remaining/60:.1f}min remaining'})
        
        # 🆕 檢查大單跟單機會（使用「淨方向」訊號）
        large_trade_boost = False
        large_trade_allowed = True
        late_entry_size_multiplier = 1.0
        if now_ts - self.large_trade_signal['timestamp'] < self.large_trade_boost_window and self.large_trade_signal['direction']:
            large_trade_boost = True
            market_data['large_trade_boost'] = True
            market_data['large_trade_direction'] = self.large_trade_signal['direction']
            market_data['large_trade_net_qty'] = self.large_trade_signal['net_qty']

        if now_ts - last_entry < cooldown:
            remaining = cooldown - (now_ts - last_entry)
            return finalize({'action': 'HOLD', 'reason': f'Cooldown: {remaining:.1f}s remaining'})

        obi = market_data['obi']
        spread_bps = market_data['spread_bps']

        MAX_SPREAD_BPS = 3.0
        MIN_OBI_THRESHOLD = 0.1

        if spread_bps > MAX_SPREAD_BPS:
            return finalize({'action': 'HOLD', 'reason': f'Spread {spread_bps:.2f}bps too wide'})
        if abs(obi) < MIN_OBI_THRESHOLD:
            return finalize({'action': 'HOLD', 'reason': f'OBI {obi:.4f} too neutral'})

        if snapshot.get('is_consolidating'):
            reason = snapshot.get('consolidation_reason') or 'Consolidation filter'
            if not allow_relaxed:
                return finalize({'action': 'HOLD', 'reason': reason})
            market_data['consolidation_flag'] = True
            market_data['consolidation_reason'] = reason

        if snapshot.get('market_regime') == MarketRegime.CONSOLIDATION.value:
            if not allow_relaxed:
                return finalize({'action': 'HOLD', 'reason': 'Market regime = CONSOLIDATION'})
            market_data['market_regime_flag'] = MarketRegime.CONSOLIDATION.value

        if snapshot.get('vpin_level') in ['DANGER', 'CRITICAL']:
            if not allow_relaxed:
                return finalize({'action': 'HOLD', 'reason': f"VPIN level {snapshot['vpin_level']}: {snapshot.get('vpin_action', '')}"})
            market_data['vpin_flag'] = market_data.get('vpin_level')

        config = self.MODE_CONFIGS[mode]
        funding_zscore = market_data['funding_zscore']
        signal_score = market_data['signal_score']
        vpin = market_data['vpin']
        signal_threshold = config.signal_score_threshold
        funding_threshold = config.funding_zscore_threshold

        if (
            pressure_alignment
            and pressure_support_level in {PressureLevel.HIGH, PressureLevel.EXTREME}
            and mode in self.offensive_pressure_modes
        ):
            discount = self.liq_pressure_config.get('threshold_discount', 1.0)
            signal_threshold *= discount
            funding_threshold *= discount
            allow_relaxed = True
            market_data['liquidation_threshold_discount'] = discount
            market_data['liquidation_pressure_supports'] = pressure_alignment

        VPIN_MAX = 0.7
        if vpin > VPIN_MAX and not allow_relaxed:
            return finalize({'action': 'HOLD', 'reason': f'VPIN {vpin:.4f} too high'})
        if vpin > VPIN_MAX and allow_relaxed:
            market_data['vpin_flag'] = market_data.get('vpin_flag', 'HIGH')

        # 🐋 大單跟單時對 VPIN 再加一層保護：避免在極端高毒性下亂做單
        if large_trade_boost and vpin > 0.8:
            return finalize({'action': 'HOLD', 'reason': f'Large trade follow blocked by high VPIN {vpin:.2f}'})

        action = 'HOLD'
        reason = 'Waiting for Hybrid signal'
        confidence = 0.0

        if lp_whale_trigger:
            action = lp_whale_trigger['direction']
            reason = lp_whale_trigger['reason']
            confidence = lp_whale_trigger['confidence']
            market_data['entry_reason'] = 'LP_WHALE_BURST'

        strong_funding_override = abs(funding_zscore) >= self.strong_funding_override
        funding_direction = 'LONG' if funding_zscore < 0 else 'SHORT'

        # 🐋 大單跟單邏輯：如果有大單信號且方向明確，優先採用
        if (
            action == 'HOLD'
            and large_trade_boost
            and large_trade_allowed
            and self.large_trade_signal['direction']
        ):
            large_direction = self.large_trade_signal['direction']
            net_qty = self.large_trade_signal['net_qty']
            # 降低門檻，只要有基本信號就跟，但仍需基本 Signal 分數
            if signal_score > signal_threshold * 0.6:
                action = large_direction
                reason = (
                    f'🐋 Large Trade Follow {large_direction}: '
                    f'net_qty={net_qty:.2f} BTC, Signal={signal_score:.2f}'
                )
                confidence = 0.75  # 大單跟單給予固定信心度
                market_data['entry_reason'] = 'LARGE_TRADE_FOLLOW'
                if late_entry_size_multiplier < 1.0:
                    market_data['position_size_multiplier'] = late_entry_size_multiplier

        if action == 'HOLD' and (abs(funding_zscore) > funding_threshold and
            signal_score > signal_threshold):
            # 🆕 M8 Volume Sniper 使用量能方向覆蓋
            if action_override:
                action = action_override
                reason = f'M8 Volume {action}: FZ={funding_zscore:.2f}, Signal={signal_score:.2f}, Volume Surge'
                confidence = min((signal_score / signal_threshold), 1.0)
            elif obi > 0:
                action = 'LONG'
                reason = f'Hybrid LONG: FZ={funding_zscore:.2f}, Signal={signal_score:.2f}, OBI={obi:.4f}'
                confidence = min((signal_score / signal_threshold), 1.0)
            elif obi < 0:
                action = 'SHORT'
                reason = f'Hybrid SHORT: FZ={funding_zscore:.2f}, Signal={signal_score:.2f}, OBI={obi:.4f}'
                confidence = min((signal_score / signal_threshold), 1.0)
            elif strong_funding_override:
                action = funding_direction
                reason = (f'Funding override {funding_direction}: '
                          f'FZ={funding_zscore:.2f}, Signal={signal_score:.2f}, OBI={obi:.4f}')
                confidence = min((abs(funding_zscore) / self.strong_funding_override), 1.0)

        if action == 'HOLD' and allow_relaxed:
            relaxed_multiplier = 0.7 if is_sandbox else 0.85
            relaxed_threshold = signal_threshold * relaxed_multiplier
            if signal_score > relaxed_threshold:
                action = 'LONG' if (obi >= 0 or funding_zscore < 0) else 'SHORT'
                reason = (f'Relaxed entry ({mode.name}): Signal={signal_score:.2f}, '
                          f'FZ={funding_zscore:.2f}, OBI={obi:.4f}')
                confidence = min((signal_score / relaxed_threshold), 1.0)

        # 🆕 反向指標邏輯 (Invert Signal)
        if config.invert_signal and action in ['LONG', 'SHORT']:
            original_action = action
            action = 'SHORT' if action == 'LONG' else 'LONG'
            reason = f"[INVERTED] {reason} (Original: {original_action})"
            market_data['inverted_from'] = original_action

        signal_input = {
            'obi': obi,
            'obi_velocity': snapshot.get('obi_velocity', 0.0),
            'signed_volume': snapshot.get('signed_volume', 0.0),
            'microprice_pressure': snapshot.get('microprice_pressure', 0.0),
           
            'timestamp': time.time() * 1000,
            # 方便之後在 diagnostics 裡分析
            'funding_zscore': funding_zscore,
            'signal_score': signal_score,
            'vpin': vpin,
            'mode': mode.name,
            'style': style,
            'large_trade_boost': large_trade_boost,
            'large_trade_direction': self.large_trade_signal['direction'] if large_trade_boost else None,
            'large_trade_net_qty': self.large_trade_signal['net_qty'] if large_trade_boost else 0.0
        }
        micro_signal, micro_confidence, micro_details = self.signal_generator.generate_signal(signal_input)
        market_data.update({
            'micro_signal': micro_signal,
            'micro_confidence': micro_confidence,
            'micro_components': micro_details.get('components', {}) if micro_details else {}
        })

        if action in ['LONG', 'SHORT']:
            edge = self._evaluate_sniper_edge(mode, config, snapshot, action)
            if not edge['eligible']:
                if not allow_relaxed:
                    return finalize({'action': 'HOLD', 'reason': edge['reason'] or 'Edge too weak'})
                market_data['edge_warning'] = edge['reason'] or 'Edge too weak'
            
            if pressure_obj and pressure_alignment in {'LONG', 'SHORT'}:
                if pressure_alignment == action:
                    bonus = self.liq_pressure_config.get('confidence_bonus', 0.0)
                    if bonus > 0:
                        confidence = min(1.0, max(confidence, 0.0) + bonus)
                        market_data['liquidation_confidence_bonus'] = bonus
                    boost_multiplier = 1.0 + self.liq_pressure_config.get('size_boost_pct', 0.0)
                    max_size = self.liq_pressure_config.get('max_size_multiplier', 1.0)
                    new_multiplier = min(
                        market_data.get('position_size_multiplier', 1.0) * boost_multiplier,
                        max_size
                    )
                    market_data['position_size_multiplier'] = new_multiplier
                else:
                    market_data['liquidation_conflict'] = {
                        'bias': pressure_alignment,
                        'confidence': pressure_confidence
                    }
                    if (
                        self.liq_pressure_config.get('block_conflict_on_extreme', True)
                        and pressure_support_level == PressureLevel.EXTREME
                        and pressure_confidence >= 0.2
                    ):
                        return finalize({'action': 'HOLD', 'reason': f'Liquidation pressure blocks {action}'})
                    confidence *= 0.85

            # 若開啟反向模式，則忽略微觀信號的一致性檢查 (因為我們就是要反著做)
            check_micro_consistency = not config.invert_signal and style != 'lp_whale_burst'
            
            if check_micro_consistency and (micro_signal != action or micro_signal == 'NEUTRAL'):
                block_reason = f"Microstructure signal {micro_signal} (conf {micro_confidence:.2f})"
                if not allow_relaxed:
                    return finalize({'action': 'HOLD', 'reason': block_reason})
                market_data['micro_warning'] = block_reason

            cost_analysis = self.cost_filter.should_trade(
                entry_price=self.latest_price,
                take_profit_percent=config.tp_pct,
                stop_loss_percent=config.sl_pct,
                position_size=self.max_position_pct,
                leverage=config.leverage,
                direction=action,
                is_maker=False,
                account_balance=self.balances[mode]
            )
            market_data.update({
                'cost_decision': cost_analysis.decision.value,
                'cost_reason': cost_analysis.reason
            })
            if cost_analysis.decision == CostDecision.REJECT:
                return finalize({'action': 'HOLD', 'reason': f"Cost filter: {cost_analysis.reason}"})

            market_data.update({
                'expected_move_levered_pct': edge['expected_move_levered_pct'],
                'net_edge_pct': edge['net_edge_pct'],
                'fee_cost_pct': edge['fee_cost_pct'],
                'momentum_floor_pct': self.sniper_config['momentum_floor_pct'],
                'edge_take_profit_ratio': self.sniper_config['edge_take_profit_ratio'],
                'edge_stop_ratio': self.sniper_config['edge_stop_ratio']
            })
            combined_confidence = (confidence + micro_confidence) / 2 if confidence > 0 else micro_confidence
            confidence = combined_confidence
            decision_reason = reason + f" | Edge {edge['net_edge_pct']:.2f}% | Micro {micro_confidence:.2f}"
        else:
            decision_reason = reason

        decision = {
            'action': action,
            'confidence': confidence,
            'market_data': market_data,
            'timestamp': self.orderbook_timestamp,
            'reason': decision_reason
        }

        return finalize(decision)
    
    def _execute_m_new_entry(self, snapshot: dict):
        """執行 M_NEW 的進場邏輯（一開場立即做空）"""
        if not self.m_new_config['enabled'] or self.m_new_config['entry_triggered']:
            return
        
        direction = self.m_new_config['direction']
        leverage = self.m_new_config['leverage']
        position_usdt = self.m_new_config['position_usdt']
        
        # 創建訂單
        order = SimulatedOrder(
            strategy="M_NEW",
            direction=direction,
            leverage=leverage,
            size=1.0,  # 100% 倉位
            entry_price=self.latest_price,
            actual_entry_price=self.latest_price * (1.0002 if direction == "LONG" else 0.9998),
            position_value=position_usdt,
            take_profit_pct=10.0,  # 不太可能觸发
            stop_loss_pct=5.0,     # 設定很寬，主要靠爆倉
            trailing_stop_pct=None,
            max_holding_hours=self.m_new_config['duration_hours'],
            min_holding_seconds=0,
            entry_time=self.orderbook_timestamp,
            market_data={}
        )
        
        # 計算爆倉價格
        if direction == "SHORT":
            # 做空：爆倉價 = 入場價 * (1 + 1/槓桿)
            liquidation_price = order.actual_entry_price * (1 + 1/leverage)
        else:
            # 做多：爆倉價 = 入場價 * (1 - 1/槓桿)
            liquidation_price = order.actual_entry_price * (1 - 1/leverage)
        
        self.m_new_config['liquidation_price'] = liquidation_price
        self.m_new_config['order'] = order
        self.m_new_config['entry_triggered'] = True
        
        print("\n" + "="*80)
        print(f"🔥 M_NEW 測試模式啟動！")
        print(f"   方向: {direction} | 槓桿: {leverage}x")
        print(f"   進場價: ${order.actual_entry_price:.2f}")
        print(f"   💀 爆倉價: ${liquidation_price:.2f}")
        print(f"   投入: ${position_usdt} USDT")
        print(f"   持續時間: {self.m_new_config['duration_hours']:.1f} 小時")
        print("="*80 + "\n")
    
    def _check_m_new_liquidation(self, snapshot: dict):
        """檢查 M_NEW 是否觸發爆倉"""
        if not self.m_new_config['enabled'] or not self.m_new_config['order']:
            return
        
        order = self.m_new_config['order']
        if order.exit_time:  # 已平倉
            return
        
        liquidation_price = self.m_new_config['liquidation_price']
        current_price = self.latest_price
        
        # 檢查是否爆倉
        is_liquidated = False
        if order.direction == "SHORT" and current_price >= liquidation_price:
            is_liquidated = True
        elif order.direction == "LONG" and current_price <= liquidation_price:
            is_liquidated = True
        
        if is_liquidated:
            # 爆倉！
            order.close(
                exit_price=liquidation_price,
                reason="💀 LIQUIDATION",
                timestamp=self.orderbook_timestamp
            )
            self.m_new_balance += order.pnl_usdt
            
            print("\n" + "💀"*40)
            print(f"💀💀💀 M_NEW 爆倉！")
            print(f"   進場價: ${order.actual_entry_price:.2f}")
            print(f"   爆倉價: ${liquidation_price:.2f}")
            print(f"   虧損: {order.pnl_usdt:+.2f} USDT ({order.roi*100:+.2f}%)")
            print(f"   剩餘資金: ${self.m_new_balance:.2f}")
            print("💀"*40 + "\n")
    
    def _calculate_dynamic_leverage(self, mode: TradingMode, base_leverage: int, decision: dict, snapshot: dict) -> int:
        """
        動態計算槓桿倍數
        根據信心度、爆倉壓力、市場狀態動態調整槓桿
        最高支援到 125x (Binance BTCUSDT 上限)
        """
        config = self.MODE_CONFIGS[mode]
        max_lev = getattr(config, 'max_dynamic_leverage', base_leverage)
        max_lev = min(max_lev, 125)  # 硬上限 125x
        
        confidence = decision.get('confidence', 0.5)
        
        # 基礎槓桿
        final_leverage = base_leverage
        
        # 1. 信心度加成
        if confidence >= 0.9:
            final_leverage *= 2.0  # 極高信心 -> 2倍槓桿
        elif confidence >= 0.8:
            final_leverage *= 1.5  # 高信心 -> 1.5倍槓桿
            
        # 2. 爆倉壓力加成 (Sniper 模式專用)
        if mode == TradingMode.M_LP_WHALE_BURST:
            l_long = snapshot.get('L_long_liq', 0)
            l_short = snapshot.get('L_short_liq', 0)
            action = decision.get('action')
            
            # 如果做空且多頭壓力極大 -> 加大槓桿
            if action == 'SHORT' and l_long >= 80:
                final_leverage *= 1.5
            # 如果做多且空頭壓力極大 -> 加大槓桿
            elif action == 'LONG' and l_short >= 80:
                final_leverage *= 1.5
                
        # 3. 限制範圍
        final_leverage = int(round(final_leverage))
        final_leverage = max(1, min(final_leverage, max_lev))
        
        return final_leverage

    def check_entries(self, snapshot: Optional[dict]):
        """檢查所有模式是否應該開倉"""
        if snapshot is None:
            return
        
        # 🆕 M_NEW: 一開場立即做空
        if self.m_new_config['enabled'] and not self.m_new_config['entry_triggered']:
            self._execute_m_new_entry(snapshot)
        
        # 每 60 秒列印一次調試資訊和排行榜
        if not hasattr(self, 'last_debug_time'):
            self.last_debug_time = 0
        
        current_time = time.time()
        show_debug = (current_time - self.last_debug_time) >= 60
        
        if show_debug:
            # 🆕 顯示排行榜（仿照 paper_trading_system.py）
            self.print_leaderboard()
            self.last_debug_time = current_time
        
        for mode in self.active_modes:
            # 🆕 高 VPIN 風險冷卻：若剛在高 VPIN 區被風控平倉，暫時禁止重新開倉
            if hasattr(self, 'high_vpin_cooldown_until'):
                cooldown_until = self.high_vpin_cooldown_until.get(mode)
                if cooldown_until and time.time() < cooldown_until:
                    continue
            decision = self.make_decision(mode, snapshot)
            
            # 顯示調試資訊
            if show_debug and mode in [TradingMode.M2_NORMAL_PRIME, TradingMode.M_FISH_MARKET_MAKER]:
                print(f"   [{mode.name}] Action: {decision['action']}, Reason: {decision.get('reason', 'N/A')}")
            
            if decision['action'] in ['LONG', 'SHORT']:
                # 檢查是否已有持倉
                open_positions = [
                    o for o in self.orders[mode] 
                    if not o.is_blocked and o.exit_time is None
                ]
                
                if len(open_positions) > 0:
                    continue  # 已有持倉，跳過
                
                # 計算倉位
                config = self.MODE_CONFIGS[mode]
                base_position_value = self.balances[mode] * self.max_position_pct
                decision_market_data = decision.get('market_data', {})
                max_size_boost = self.liq_pressure_config.get('max_size_multiplier', 1.0)
                size_multiplier = float(decision_market_data.get('position_size_multiplier', 1.0))
                size_multiplier = max(0.1, min(size_multiplier, max_size_boost))
                position_value = base_position_value * size_multiplier
                position_size = min(
                    self.max_position_pct * size_multiplier,
                    self.max_position_pct * max_size_boost
                )

                # 🆕 在高 VPIN 區時，要求淨 edge 需高於手續費的倍數，避免在噪音區硬做單
                # 但 M_WHALE_WATCHER 和 M_LP_WHALE_BURST 豁免此檢查，因為它們本身就是追逐高 VPIN 事件
                vpin_for_entry = snapshot.get('vpin', 0.0)
                if vpin_for_entry >= 0.75 and mode not in {TradingMode.M_WHALE_WATCHER, TradingMode.M_LP_WHALE_BURST}:
                    # 若 diagnostics 尚未計算 edge，就保守地跳過，避免在極端區貿然交易
                    expected_move_levered = snapshot.get('expected_move_levered_pct')
                    fee_cost_pct = 0.001 * config.leverage
                    min_net_edge = fee_cost_pct * 2.0  # 至少要有 2 倍手續費空間
                    if expected_move_levered is None or expected_move_levered <= min_net_edge:
                        continue
                
                # 🆕 重新計算 TP/SL - 確保扣費後有淨利
                # 手續費成本: 0.05% * 2 (開+平) * 槓桿 = 0.1% * 槓桿
                # 例如 10x 槓桿 => 1% 成本
                tp_pct = config.tp_pct
                sl_pct = config.sl_pct
                
                fee_cost_pct = 0.001 * config.leverage  # 0.1% * 槓桿
                
                # 目標淨利：1-3%（現貨百分比）
                # TP: 至少 fee_cost_pct + 1.5%（保守），最高 fee_cost_pct + 3%（激進）
                # SL: 控制在 1-1.5%（現貨百分比）
                
                style = self.mode_styles.get(mode, 'baseline')
                trailing_stop_pct = 0.3
                max_holding_hours = 24
                min_holding_seconds = 20.0

                if mode == TradingMode.M0_ULTRA_SAFE:
                    tp_pct = fee_cost_pct + 2.3
                    sl_pct = 1.1
                    trailing_stop_pct = 0.35

                    max_holding_hours = 36
                    min_holding_seconds = 30.0
                elif style == 'trend':
                    tp_pct = fee_cost_pct + 2.4
                    sl_pct = 1.1
                    trailing_stop_pct = 0.25
                    max_holding_hours = 30
                    min_holding_seconds = 50.0
                elif style == 'scalper':
                    tp_pct = fee_cost_pct + 2.4
                    sl_pct = 1.2
                    trailing_stop_pct = 0.25
                    max_holding_hours = 10
                    min_holding_seconds = 45.0
                elif style == 'reversion':
                    tp_pct = fee_cost_pct + 1.8
                    sl_pct = 1.4
                    trailing_stop_pct = 0.18
                    max_holding_hours = 10
                    min_holding_seconds = 35.0
                elif mode == TradingMode.M6_SIGNAL_SANDBOX:
                    tp_pct = fee_cost_pct + 2.2
                    sl_pct = 1.2
                    min_holding_seconds = 50.0
                    trailing_stop_pct = 0.28
                elif style == 'whale':
                    # 🐳 M_WHALE_WATCHER: 更寬的止盈空間，因為要等集中度下降才出場
                    tp_pct = fee_cost_pct + 5.0
                    sl_pct = 2.0
                    trailing_stop_pct = 0.5
                    max_holding_hours = 24
                    min_holding_seconds = 60.0
                else:
                    tp_pct = fee_cost_pct + 2.0
                    sl_pct = 1.0
                    min_holding_seconds = max(min_holding_seconds, 30.0)
                
                # 🆕 動態計算槓桿
                dynamic_leverage = self._calculate_dynamic_leverage(mode, config.leverage, decision, snapshot)

                # 創建訂單
                order = SimulatedOrder(
                    strategy=mode.name,
                    direction=decision['action'],
                    leverage=dynamic_leverage,
                    size=position_size,
                    entry_price=self.latest_price,
                    actual_entry_price=self.latest_price * (1 + 0.0002),  # 滑點
                    position_value=position_value,
                    take_profit_pct=tp_pct,  # 🆕 動態計算
                    stop_loss_pct=sl_pct,  # 🆕 動態計算
                    trailing_stop_pct=trailing_stop_pct,
                    max_holding_hours=max_holding_hours,
                    min_holding_seconds=min_holding_seconds,
                    entry_time=self.orderbook_timestamp,
                    market_data=decision_market_data,
                    is_maker=decision.get('is_maker', False)  # 🆕 傳遞 Maker 標記
                )
                
                # 🆕 確保 entry_reason 被記錄
                if decision.get('reason'):
                    order.entry_reason = decision['reason']
                
                # 顯示開倉資訊（完整格式，仿照 paper_trading_system.py）
                strategy_info = self.mode_info[mode]
                direction_emoji = "📈" if order.direction == "LONG" else "📉"
                direction_text = "做多" if order.direction == "LONG" else "做空"
                border_icon = "🟢" if decision['confidence'] > 0.7 else "🟡" if decision['confidence'] > 0.5 else "🔴"
                
                expected_hold = "動態調整"
                current_time = datetime.now().strftime('%H:%M:%S')
                market_data = decision_market_data.copy()
                
                # 🐳 M_WHALE_WATCHER 特殊顯示：使用 Maker 價格
                if mode == TradingMode.M_WHALE_WATCHER:
                    try:
                        # Maker 價格 = 掛單在最佳買一/賣一，等待成交
                        best_bid = self.orderbook_data['bids'][0][0] if self.orderbook_data and self.orderbook_data.get('bids') else self.latest_price
                        best_ask = self.orderbook_data['asks'][0][0] if self.orderbook_data and self.orderbook_data.get('asks') else self.latest_price
                        maker_price = best_bid if order.direction == "LONG" else best_ask
                        
                        print()
                        print(f"{border_icon}{'=' * 78}{border_icon}")
                        print(f"{direction_emoji} [{strategy_info['emoji']}] {strategy_info['name']} - {direction_text} | {current_time}")
                        print(f"{'─' * 80}")
                        print(f"   💰 Maker 掛單價格: ${maker_price:,.2f}")
                        print(f"   📊 槓桿: {config.leverage}x | 倉位: {position_size*100:.1f}%")
                        print(f"   🎯 止盈: +{tp_pct:.2f}% | 🛑 止損: -{sl_pct:.2f}%")
                        print(f"   🐳 大單集中度: {market_data.get('whale_dominance', 0):.2f}")
                        print(f"   🐳 大單淨量: {market_data.get('whale_net_qty', 0):.2f} BTC")
                        print(f"   📝 原因: {decision.get('reason', 'N/A')}")
                        print(f"{border_icon}{'=' * 78}{border_icon}\n")
                    except Exception as e:
                        print(f"   ❌ [M🐳] 顯示開倉訊息時出錯: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    print(f"✨✨✨ [{strategy_info['emoji']}] {border_icon}{direction_emoji} 開倉 - {direction_text} ⏰ 開倉時間: {current_time}")
                    print()
                    print(f"   💵 投資金額: ${order.position_value:.2f} USDT / ⚡ 槓桿倍數: {order.leverage}x")
                    print(f"   ⏱️  預估持有: {expected_hold}")
                    print(f"   ───")
                    print(f"   💰 進場價格: {order.actual_entry_price:.2f} USDT / 📊 進場 OBI: {market_data.get('obi', 0):+.4f}")
                    btc_amount = order.position_value / order.actual_entry_price
                    print(f"   🪙 倉位大小: {btc_amount:.4f} BTC / 📐 倉位比例: {order.size*100:.0f}%")
                    print()
                
                # 記錄訂單
                self.orders[mode].append(order)
                
                # 🆕 更新最後開倉時間
                self.last_entry_time[mode] = time.time()
    
    def check_exits(self, snapshot: Optional[dict]):
        """檢查所有持倉是否應該平倉"""
        if snapshot is None:
            return
        
        # 🆕 M_NEW: 檢查爆倉
        if self.m_new_config['enabled'] and self.m_new_config['order'] is not None:
            self._check_m_new_liquidation(snapshot)
        
        for mode in self.active_modes:
            # 🆕 Mup/Mdown: 永遠不平倉（作為開場指標）
            if mode in {TradingMode.MUP_DIRECTIONAL_LONG, TradingMode.MDOWN_DIRECTIONAL_SHORT}:
                continue

            # 獲取該模式的開倉訂單
            open_orders = [
                o for o in self.orders[mode]
                if not o.is_blocked and o.exit_time is None
            ]
            
            # 🐳 M_WHALE_WATCHER 特殊出場邏輯：集中度 < 0.6 立即平倉
            if mode == TradingMode.M_WHALE_WATCHER and open_orders:
                whale_signal = self.large_trade_signal
                dominance = whale_signal.get('dominance_ratio', 0.0)
                
                # 取得動態參數
                config = self.MODE_CONFIGS[mode]
                exit_rules = getattr(config, 'exit_rules', {})
                whale_rules = exit_rules.get('whale_dominance', {})
                exit_max = whale_rules.get('max', 0.6)
                
                # 1. 集中度不足 -> 撤退
                if dominance < exit_max:
                    for position in open_orders:
                        position.close(
                            exit_price=self.latest_price,
                            reason=f"M🐳 Whale: dominance {dominance:.2f} < {exit_max}",
                            timestamp=self.orderbook_timestamp
                        )
                        self.balances[mode] += position.pnl_usdt
                        
                        # 顯示出場資訊
                        roi_pct = position.roi * 100
                        emoji = "✅" if position.roi > 0 else "❌"
                        print(f"\n{emoji} [{self.mode_info[mode]['emoji']}] 平倉: {position.direction}")
                        print(f"   價格: ${position.actual_entry_price:.2f} → ${self.latest_price:.2f}")
                        print(f"   ROI: {roi_pct:+.2f}%")
                        print(f"   原因: {position.exit_reason}")
                    continue

                # 2. 鯨魚方向反轉 -> 立即停損/止盈
                current_whale_dir = whale_signal.get('direction')
                
                # 取得 flip_enabled 參數
                flip_config = getattr(config, 'whale_flip_prediction', {})
                flip_enabled = flip_config.get('enabled', False)
                
                if flip_enabled and current_whale_dir:
                    for position in open_orders:
                        # 如果大單方向與持倉方向相反，且集中度仍高（代表對手盤強勢）
                        if position.direction != current_whale_dir:
                            position.close(
                                exit_price=self.latest_price,
                                reason=f"M🐳 Whale Flip: Direction changed to {current_whale_dir} (Dom {dominance:.2f})",
                                timestamp=self.orderbook_timestamp
                            )
                            self.balances[mode] += position.pnl_usdt
                            
                            # 顯示出場資訊
                            roi_pct = position.roi * 100
                            emoji = "✅" if position.roi > 0 else "❌"
                            print(f"\n{emoji} [{self.mode_info[mode]['emoji']}] 反轉平倉: {position.direction} -> {current_whale_dir}")
                            print(f"   價格: ${position.actual_entry_price:.2f} → ${self.latest_price:.2f}")
                            print(f"   ROI: {roi_pct:+.2f}%")
                            print(f"   原因: {position.exit_reason}")
                    
                    # 如果有觸發反轉平倉，continue 避免進入下方一般檢查
                    if any(o.exit_time is not None for o in open_orders):
                        continue
            
            for position in open_orders:
                # 檢查平倉條件
                exit_reason = position.check_exit(
                    current_price=self.latest_price,
                    market_data=snapshot,
                    current_timestamp=self.orderbook_timestamp
                )
                
                if exit_reason:
                    # 平倉
                    position.close(
                        exit_price=self.latest_price,
                        reason=exit_reason,
                        timestamp=self.orderbook_timestamp
                    )
                    
                    # 更新餘額
                    self.balances[mode] += position.pnl_usdt

                    # 🆕 高 VPIN / 反向訊號保護後的短期冷卻：避免在轉折區來回被磨
                    is_high_vpin_exit = (
                        exit_reason in ["VPIN_PROTECTIVE_STOP", "VPIN_LOCK_PROFIT"]
                        or (exit_reason == "REVERSE_SIGNAL" and position.market_data.get("vpin", 0) >= 0.75)
                    )
                    if is_high_vpin_exit:
                        # 在高 VPIN 區剛被打出場，暫停該模式重新進場一段時間
                        cooldown_seconds = 120.0
                        current_ts = time.time()
                        if not hasattr(self, 'high_vpin_cooldown_until'):
                            self.high_vpin_cooldown_until = {}
                        self.high_vpin_cooldown_until[mode] = max(
                            self.high_vpin_cooldown_until.get(mode, 0),
                            current_ts + cooldown_seconds
                        )
                    
                    # 🆕 更新連虧追蹤
                    if position.roi < 0:
                        self.consecutive_losses[mode] += 1
                        # 連虧 3 筆：cooldown 延長 1 小時
                        if self.consecutive_losses[mode] >= 3:
                            self.loss_cooldown_until[mode] = time.time() + 3600
                            print(f"   ⚠️  [{self.mode_info[mode]['emoji']}] 連虧 {self.consecutive_losses[mode]} 筆，暫停 1 小時")
                    else:
                        self.consecutive_losses[mode] = 0  # 獲利重置
                    
                    # 顯示平倉資訊
                    strategy_info = self.mode_info[mode]
                    direction_emoji = "📈" if position.direction == "LONG" else "📉"
                    direction_text = "LONG" if position.direction == "LONG" else "SHORT"
                    
                    exit_reason_emoji = {
                        'TAKE_PROFIT': '🎯',
                        'STOP_LOSS': '🛑',
                        'REVERSE_SIGNAL': '🔄',
                        'TIME_LIMIT': '⏰',
                        'TRAILING_STOP': '📉',
                        'TIME_STOP': '⏱️',
                        'VPIN_SPIKE': '☠️',
                        'STRUCTURE_BREAK': '📐'
                    }.get(exit_reason, '🔔')
                    
                    # 根據盈虧選擇圖示和文字
                    if position.roi > 0:
                        result_icon = "🟢"
                        result_text = "獲利平倉"
                        pnl_color = "🟢"
                    elif position.roi < 0:
                        result_icon = "🔴"
                        result_text = "虧損平倉"
                        pnl_color = "🔴"
                    else:
                        result_icon = "⚪"
                        result_text = "持平平倉"
                        pnl_color = ""
                    
                    # 格式化持有時間
                    hold_seconds = position.holding_seconds
                    if hold_seconds < 60:
                        hold_time_str = f"{hold_seconds:.1f} 秒"
                    elif hold_seconds < 3600:
                        hold_time_str = f"{hold_seconds/60:.1f} 分鐘"
                    else:
                        hold_time_str = f"{hold_seconds/3600:.2f} 小時"
                    
                    current_time = datetime.now().strftime('%H:%M:%S')
                    
                    print()
                    print(f"✨✨✨ [{strategy_info['emoji']}] {result_icon} {result_text} ⏰ 平倉時間: {current_time}")
                    print()
                    print(f"   💰 本次盈虧: {pnl_color}{position.roi:+.2f}% ({position.pnl_usdt:+.2f} USDT)")
                    print(f"   💵 投資金額: ${position.position_value:.2f} USDT / ⚡ 槓桿倍數: {position.leverage}x")
                    print(f"   ⏱️  持有時長: {hold_time_str}")
                    print(f"   🤖 方向: {direction_emoji} {direction_text}")
                    print(f"   💰 進場價格: {position.actual_entry_price:.2f} USDT / 💰 出場價格: {position.exit_price:.2f} USDT")
                    print(f"   📊 出場 OBI: {snapshot.get('obi', 0):+.4f}")
                    print(f"   ───")
                    print(f"   📊 手續費明細:")
                    print(f"      • 開倉手續費: {position.entry_fee:.4f} USDT / • 平倉手續費: {position.exit_fee:.4f} USDT")
                    print(f"      • 資金費率: {position.funding_fee:.4f} USDT / • 總手續費: {position.total_fees:.4f} USDT")
                    print(f"   💰 當前餘額: {self.balances[mode]:.2f} USDT (起始 {self.initial_capital:.2f})")
                    print(f"   {exit_reason_emoji} 平倉原因: {exit_reason}")
                    print()
                    
                    # 記錄虧損交易
                    if position.roi < 0:
                        loss_trade = LossTrade(
                            trade_id=position.order_id,
                            entry_time=position.entry_time,
                            exit_time=position.exit_time,
                            entry_price=position.entry_price,
                            exit_price=position.exit_price,
                            position_size=position.size,
                            leverage=position.leverage,
                            direction=position.direction,
                            loss_amount=abs(position.pnl_usdt),
                            loss_percent=abs(position.roi / 100),
                            holding_time_seconds=int(position.holding_seconds),
                            rsi_at_entry=None,
                            spread_at_entry=position.market_data.get('spread_bps'),
                            volume_at_entry=None,
                            volatility_at_entry=None,
                            obi_at_entry=position.entry_obi,
                            vpin_at_entry=position.market_data.get('vpin'),
                            exit_reason=exit_reason,
                            sl_percent=position.stop_loss_pct,
                            tp_percent=position.take_profit_pct,
                            strategy=mode.name
                        )
                        self.loss_analyzer.record_loss(loss_trade)
                    
                    # 記錄交易到時間分析器
                    self.time_analyzer.record_trade(
                        entry_time=position.entry_time,
                        exit_time=position.exit_time,
                        profit=position.pnl_usdt,
                        is_win=(position.roi > 0),
                        strategy=mode.name
                    )
                    
                    # 🆕 立即保存到檔案（仿照 paper_trading_system.py）
                    self._append_order_to_file(mode, position)
    
    def print_status(self):
        """定期列印狀態"""
        print(f"\n{'─'*80}")
        print(f"⏰ 時間: {datetime.now().strftime('%H:%M:%S')} | 價格: ${self.latest_price:.2f}")
        print(f"{'─'*80}\n")

        panel = self._render_liquidation_pressure_panel()
        if panel:
            print(panel)
            print()
        
        for mode in self.active_modes:
            strategy_info = self.mode_info[mode]
            balance = self.balances[mode]
            pnl = balance - self.initial_capital
            pnl_pct = (pnl / self.initial_capital) * 100
            
            # 統計該模式的交易
            all_orders = self.orders[mode]
            closed_orders = [o for o in all_orders if o.exit_time is not None]
            open_orders = [o for o in all_orders if o.exit_time is None and not o.is_blocked]
            
            wins = len([o for o in closed_orders if o.roi > 0])
            losses = len([o for o in closed_orders if o.roi < 0])
            win_rate = (wins / len(closed_orders) * 100) if closed_orders else 0
            
            status_icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            
            print(f"{strategy_info['emoji']} {strategy_info['name']}")
            print(f"   {status_icon} 餘額: ${balance:.2f} USDT ({pnl_pct:+.2f}%)")
            print(f"   📊 交易: {len(closed_orders)}筆 | 勝率: {win_rate:.1f}% | 持倉: {len(open_orders)}筆")
            
            # 顯示持倉狀態
            for pos in open_orders:
                unrealized_pnl_usdt, unrealized_pnl_pct = pos.update_unrealized_pnl(self.latest_price)
                holding_seconds = (
                    datetime.fromisoformat(self.orderbook_timestamp) - 
                    datetime.fromisoformat(pos.entry_time)
                ).total_seconds()
                
                pos_icon = "🟢" if unrealized_pnl_pct > 0 else "🔴" if unrealized_pnl_pct < 0 else "⚪"
                dir_emoji = "📈" if pos.direction == "LONG" else "📉"
                
                print(f"   ✨ [{datetime.now().strftime('%H:%M:%S')}] 📊 持倉狀態: [{strategy_info['emoji']}]")
                print(f"      {dir_emoji} {pos.direction} 💵 ${pos.position_value:.2f} USDT / ⚡{pos.leverage}x @ ${pos.actual_entry_price:.2f}")
                print(f"      {pos_icon} [🌟] 未實現: {unrealized_pnl_pct:+.2f}% | ⏱️ 持倉: {int(holding_seconds)}秒")
        
        # 🆕 顯示 M_NEW 狀態
        if self.m_new_config['enabled']:
            m_new_pnl = self.m_new_balance - 100.0
            m_new_pct = (m_new_pnl / 100.0) * 100
            status_icon = "💀" if m_new_pnl <= -50 else "🔴" if m_new_pnl < 0 else "🟢"
            
            print(f"🔥M_NEW (20x 做空測試)")
            print(f"   {status_icon} 餘額: ${self.m_new_balance:.2f} USDT ({m_new_pct:+.2f}%)")
            
            order = self.m_new_config.get('order')
            if order and order.exit_time is None:
                # 持倉中
                unrealized_pnl_usdt, unrealized_pnl_pct = order.update_unrealized_pnl(self.latest_price)
                holding_seconds = (
                    datetime.fromisoformat(self.orderbook_timestamp) - 
                    datetime.fromisoformat(order.entry_time)
                ).total_seconds()
                
                pos_icon = "🟢" if unrealized_pnl_pct > 0 else "🔴" if unrealized_pnl_pct < 0 else "⚪"
                liquidation_price = self.m_new_config['liquidation_price']
                
                print(f"   📊 持倉: 1筆")
                print(f"   ✨ [{datetime.now().strftime('%H:%M:%S')}] 📊 持倉狀態: [🔥M_NEW]")
                print(f"      📉 SHORT 💵 ${order.position_value:.2f} USDT / ⚡{order.leverage}x @ ${order.actual_entry_price:.2f}")
                print(f"      {pos_icon} [🌟] 未實現: {unrealized_pnl_pct:+.2f}% | ⏱️ 持倉: {int(holding_seconds)}秒")
                print(f"      💀 爆倉價: ${liquidation_price:.2f} USDT | ⏰ 剩餘時間: {self.m_new_config['duration_hours'] - holding_seconds/3600:.2f}小時")
            else:
                print(f"   📊 持倉: 0筆")
    
    def _sync_configs(self):
        """從 ModeConfigManager 同步配置到 self.MODE_CONFIGS"""
        new_configs = self.mode_config_manager.get_all_enabled_modes()
        updated_count = 0
        
        for mode_name, config_dict in new_configs.items():
            try:
                # 嘗試匹配 TradingMode
                mode = getattr(TradingMode, mode_name, None)
                if mode and mode in self.MODE_CONFIGS:
                    current_config = self.MODE_CONFIGS[mode]
                    
                    # 更新欄位
                    for k, v in config_dict.items():
                        # 允許動態新增屬性 (如 entry_rules, whale_flip_prediction)
                        setattr(current_config, k, v)
                    
                    # 特別處理 invert_signal
                    if 'invert_signal' in config_dict:
                        current_config.invert_signal = config_dict['invert_signal']
                    
                    updated_count += 1
            except Exception as e:
                print(f"⚠️ Config sync failed for {mode_name}: {e}")
        
        if updated_count > 0:
            print(f"✅ Synced {updated_count} mode configs from manager")

    async def _run_liquidation_pressure_updater(self):
        """背景任務：定期更新爆倉壓力數據"""
        if not BinanceLeverageDataFetcher:
            print("⚠️ 無法導入 BinanceLeverageDataFetcher，自動更新功能失效")
            return

        print("🔄 啟動爆倉壓力自動更新服務 (每 60 秒)...")
        fetcher = BinanceLeverageDataFetcher(
            symbol="BTCUSDT",
            period="5m",
            limit=30,
            force_limit=50,
            timeout=10.0
        )
        outfile = self.liq_pressure_config['data_path']
        
        while datetime.now() < self.end_time:
            try:
                # 在執行緒池中運行同步的 fetch
                loop = asyncio.get_running_loop()
                payload = await loop.run_in_executor(None, fetcher.collect)
                
                # 保存檔案
                await loop.run_in_executor(None, lambda: save_payload(payload, str(outfile)))
                
                # 立即更新內部緩存
                snapshot = load_snapshot_from_file(str(outfile))
                if snapshot:
                    self._liq_pressure_snapshot = snapshot
                    self._liq_pressure_snapshot_dict = snapshot.to_dict()
                    self._liq_pressure_last_mtime = time.time()
                
            except Exception as e:
                print(f"⚠️ 爆倉壓力更新失敗: {e}")
            
            # 等待 60 秒
            await asyncio.sleep(60)

    async def _run_auto_optimizer(self):
        """背景任務：定期執行策略優化分析"""
        print("🔄 啟動策略優化自動分析服務 (每 30 分鐘)...")
        
        optimizer_script = Path(__file__).parent / "optimize_strategies.py"
        if not optimizer_script.exists():
            print(f"⚠️ 找不到優化腳本: {optimizer_script}")
            return

        while datetime.now() < self.end_time:
            # 初始等待 10 分鐘，之後每 30 分鐘執行一次
            await asyncio.sleep(1800) 
            
            try:
                print(f"\n🔍 [{datetime.now().strftime('%H:%M:%S')}] 正在執行策略優化分析...")
                
                # 使用 subprocess 執行優化腳本
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(optimizer_script),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                stdout, stderr = await process.communicate()
                
                if stdout:
                    output = stdout.decode().strip()
                    # 只顯示關鍵的優化建議部分
                    print("\n" + "="*40)
                    print("🤖 AI 策略優化建議報告")
                    print("="*40)
                    # 過濾並顯示輸出
                    for line in output.split('\n'):
                        if "✨ 優化建議" in line or "分析模式" in line:
                            print(line)
                    print("="*40 + "\n")
                    
                if stderr:
                    err = stderr.decode().strip()
                    if err:
                        print(f"⚠️ 優化分析警告: {err}")
                        
            except Exception as e:
                print(f"⚠️ 自動優化執行失敗: {e}")

    async def run(self):
        """運行測試"""
        # 啟動 WebSocket
        ws_task = asyncio.create_task(self.connect_websocket())
        
        # 啟動爆倉壓力更新
        asyncio.create_task(self._run_liquidation_pressure_updater())
        
        # 🆕 啟動自動優化分析
        asyncio.create_task(self._run_auto_optimizer())
        
        # 等待訂單簿數據
        while self.orderbook_data is None:
            await asyncio.sleep(0.1)
        
        print("✅ 開始測試...")
        print(f"🔄 動態配置: {self.mode_config_manager.config_path}")
        print(f"✅ 已載入: {len(self.mode_config_manager.get_all_enabled_modes())} 個動態模式\n")
        
        # 🆕 初始同步配置
        self._sync_configs()
        
        decision_count = 0
        last_status_time = time.time()
        first_loop = True  # 🆕 第一輪標記
        
        try:
            while datetime.now() < self.end_time:
                # 🆕 定期檢查配置更新（熱更新）
                now = time.time()
                if now - self.last_config_reload_time >= self.config_reload_interval:
                    if self.mode_config_manager.reload_if_updated():
                        print(f"\n🔄 [{datetime.now().strftime('%H:%M:%S')}] Config hot-reloaded!")
                        self._sync_configs()  # 🆕 同步更新到策略配置
                    self.last_config_reload_time = now
                decision_count += 1
                snapshot = self._build_market_snapshot()
                if snapshot is None:
                    await asyncio.sleep(0.5)
                    continue
                
                # 🆕 先檢查舊單要不要出場（除了第一輪）
                if not first_loop:
                    self.check_exits(snapshot)
                else:
                    first_loop = False
                
                # 再檢查新單進場機會
                self.check_entries(snapshot)
                
                # 每 30 秒列印狀態
                if time.time() - last_status_time >= 30:
                    self.print_status()
                    last_status_time = time.time()
                
                await asyncio.sleep(5)  # 每 5 秒檢查一次
                
        except KeyboardInterrupt:
            print("\n⚠️  使用者中斷測試\n")
        except asyncio.CancelledError:
            print("\n⚠️  測試已取消\n")
        except Exception as e:
            print(f"\n❌ 測試錯誤: {e}\n")
        finally:
            ws_task.cancel()
            try:
                await ws_task
            except asyncio.CancelledError:
                pass
            self.generate_report()
    
    def print_leaderboard(self):
        """顯示資金競賽排行榜（仿照 paper_trading_system.py）"""
        print()
        print("🏆 資金競賽排行榜:")
        print("-" * 80)
        
        # 計算每個模式的總資產（餘額 + 未實現盈虧）
        mode_balances = []
        for mode in self.active_modes:
            strategy_info = self.mode_info[mode]
            mode_label = f"{strategy_info['emoji']} {strategy_info['name'][:12]}"
            
            balance = self.balances[mode]
            
            # 計算未實現盈虧
            unrealized_pnl_usdt = 0
            open_orders = [o for o in self.orders[mode] if o.is_blocked and o.exit_time is None]
            if open_orders:
                for position in open_orders:
                    unrealized_usdt, _ = position.update_unrealized_pnl(self.latest_price)
                    unrealized_pnl_usdt += unrealized_usdt
            
            total_equity = balance + unrealized_pnl_usdt
            
            mode_balances.append((mode, mode_label, balance, unrealized_pnl_usdt, total_equity, 'MODE'))
        
        # 🆕 加入 M_NEW
        if self.m_new_config['enabled']:
            m_new_balance = self.m_new_balance
            m_new_unrealized = 0.0
            
            # 計算 M_NEW 未實現盈虧
            if self.m_new_config['order'] and self.m_new_config['order'].exit_time is None:
                order = self.m_new_config['order']
                m_new_unrealized, _ = order.update_unrealized_pnl(self.latest_price)
            
            m_new_total = m_new_balance + m_new_unrealized
            mode_balances.append((None, "🔥M_NEW", m_new_balance, m_new_unrealized, m_new_total, 'M_NEW'))
        
        # ====== 排行榜 #1: 已實現損益 ======
        print("💰 [已實現損益] 排行榜 (槓桿後，扣除所有手續費):")
        print("-" * 80)
        
        # 按已實現餘額排序
        realized_ranking = sorted(mode_balances, key=lambda x: x[2], reverse=True)
        rank_emojis = ['🥇', '🥈', '🥉'] + [f'{i:2d}.' for i in range(4, len(realized_ranking)+1)]
        
        for i, (mode, label, balance, unrealized, total, mode_type) in enumerate(realized_ranking):
            if i >= len(rank_emojis):
                break
            rank = rank_emojis[i]
            
            if mode_type == 'M_NEW':
                realized_pnl = balance - 100.0
                realized_pct = (realized_pnl / 100.0) * 100
                pnl_emoji = "🟢" if realized_pct >= 0 else "🔴" if realized_pct < 0 else "⚪"
                leverage = self.m_new_config['leverage']
            else:
                realized_pnl = balance - self.initial_capital
                realized_pct = (realized_pnl / self.initial_capital) * 100
                pnl_emoji = "🟢" if realized_pct >= 0 else "🔴" if realized_pct < 0 else "⚪"
                config = self.MODE_CONFIGS[mode]
                leverage = config.leverage
            
            print(f"{rank} {label} | 💰 {balance:.2f} USDT ({realized_pnl:+.2f}) | {pnl_emoji} {realized_pct:+.2f}% | ⚡ {leverage}x槓桿")
        
        print("-" * 80)
        
        # ====== 排行榜 #2: 未實現損益 ======
        print("📊 [未實現損益] 排行榜 (槓桿後，扣除所有手續費):")
        print("-" * 80)
        
        # 按未實現盈虧排序
        unrealized_ranking = sorted(mode_balances, key=lambda x: x[3], reverse=True)
        
        for i, (mode, label, balance, unrealized, total, mode_type) in enumerate(unrealized_ranking):
            if i >= len(rank_emojis):
                break
            rank = rank_emojis[i]
            
            if mode_type == 'M_NEW':
                leverage = self.m_new_config['leverage']
            else:
                config = self.MODE_CONFIGS[mode]
                leverage = config.leverage
            
            if unrealized != 0:
                unrealized_pct = (unrealized / 100.0 if mode_type == 'M_NEW' else unrealized / self.initial_capital) * 100
                pnl_emoji = "🟢" if unrealized >= 0 else "🔴" if unrealized < 0 else "⚪"
                print(f"{rank} {label} | 📊 [🌟] {unrealized:+.2f} USDT | {pnl_emoji} {unrealized_pct:+.2f}% | ⚡ {leverage}x槓桿")
            else:
                print(f"{rank} {label} | 📊 無持倉 | ⚪ 0.00% | ⚡ {leverage}x槓桿")
        
        print("-" * 80)
        print()
    
    def _init_save_file(self):
        """初始化 JSON 保存檔案"""
        data = {
            'metadata': {
                'start_timestamp': self.save_timestamp,
                'end_timestamp': None,
                'test_duration_hours': self.test_duration_hours,
                'initial_capital': self.initial_capital,
                'total_decisions': 0,
                'final_balances': {}
            },
            'orders': {mode.name: [] for mode in self.active_modes}
        }
        
        with open(self.json_filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _init_signal_log_file(self):
        """建立 signal diagnostics CSV，方便後續紀錄指標快照"""
        headers = [
            'timestamp',
            'mode',
            'style',
            'decision_stage',
            'action',
            'reason',
            'signal_score',
            'funding_zscore',
            'obi',
            'vpin',
            'spread_bps',
            'microprice_pressure',
            'micro_signal',
            'micro_confidence',
            'large_trade_boost',
            'large_trade_direction',
            'large_trade_net_qty',
            'entry_reason',
            # 🆕 新增完整指標欄位
            'price',
            'rsi_14',
            'stoch_k',
            'stoch_d',
            'ma_20',
            'boll_upper',
            'boll_lower',
            'market_regime',
            'is_consolidating',
            'momentum_pct',
            'volatility_pct',
            'trend_strength',
            'range_position',
            'ma_distance',
            'volume_ratio'
        ]

        try:
            with open(self.signal_log_file, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
        except Exception as e:
            print(f"⚠️  初始化 signal log 失敗: {e}")

    def _log_signal_snapshot(self, mode: TradingMode, decision: dict, snapshot: dict):
        """將決策前的市場指標紀錄到 CSV 以方便日後診斷"""
        if not getattr(self, 'signal_log_file', None):
            return

        market_data = decision.get('market_data', {})
        row = [
            datetime.now().isoformat(),
            mode.name,
            market_data.get('mode_style'),
            decision.get('reason', decision.get('action', 'UNKNOWN')),
            decision.get('action', 'HOLD'),
            decision.get('reason', ''),
            snapshot.get('signal_score'),
            snapshot.get('funding_zscore'),
            snapshot.get('obi'),
            snapshot.get('vpin'),
            snapshot.get('spread_bps'),
            snapshot.get('microprice_pressure'),
            market_data.get('micro_signal'),
            market_data.get('micro_confidence'),
            market_data.get('large_trade_boost', False),
            market_data.get('large_trade_direction'),
            market_data.get('large_trade_net_qty', 0.0),
            market_data.get('entry_reason'),
            # 🆕 寫入完整指標數據
            snapshot.get('price'),
            snapshot.get('rsi_14'),
            snapshot.get('stoch_k'),
            snapshot.get('stoch_d'),
            snapshot.get('ma_20'),
            snapshot.get('boll_upper'),
            snapshot.get('boll_lower'),
            snapshot.get('market_regime'),
            snapshot.get('is_consolidating'),
            snapshot.get('momentum_pct'),
            snapshot.get('volatility_pct'),
            snapshot.get('trend_strength'),
            snapshot.get('range_position'),
            snapshot.get('regime_details', {}).get('ma_distance'),
            snapshot.get('regime_details', {}).get('volume_ratio')
        ]

        try:
            with open(self.signal_log_file, 'a', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(row)
        except Exception as e:
            print(f"⚠️  寫入 signal log 失敗: {e}")
    
    def _append_order_to_file(self, mode: TradingMode, order: SimulatedOrder):
        """每筆交易立即追加到 JSON 檔案（仿照 paper_trading_system.py）"""
        try:
            # 讀取現有資料
            with open(self.json_filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 追加訂單
            data['orders'][mode.name].append(order.to_dict())
            
            # 寫回檔案
            with open(self.json_filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            print(f"⚠️  保存訂單失敗: {e}")
    
    def generate_report(self):
        """生成最終報告（仿照 paper_trading_system.py）"""
        print(f"\n{'='*80}")
        print(f"📊 測試完成 - 最終報告")
        print(f"{'='*80}\n")
        
        total_trades = 0
        total_pnl = 0
        
        for mode in self.active_modes:
            strategy_info = self.mode_info[mode]
            balance = self.balances[mode]
            pnl = balance - self.initial_capital
            pnl_pct = (pnl / self.initial_capital) * 100
            
            all_orders = self.orders[mode]
            closed_orders = [o for o in all_orders if o.exit_time is not None]
            
            wins = len([o for o in closed_orders if o.roi > 0])
            losses = len([o for o in closed_orders if o.roi < 0])
            win_rate = (wins / len(closed_orders) * 100) if closed_orders else 0
            
            total_trades += len(closed_orders)
            total_pnl += pnl
            
            status_icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            
            print(f"{strategy_info['emoji']} {strategy_info['name']}")
            print(f"   {status_icon} 最終餘額: ${balance:.2f} USDT ({pnl_pct:+.2f}%)")
            print(f"   📊 總交易: {len(closed_orders)}筆")
            print(f"   ✅ 勝場: {wins}筆 | ❌ 敗場: {losses}筆")
            print(f"   📈 勝率: {win_rate:.1f}%")
            print()
        
        print(f"{'─'*80}")
        print(f"💰 總體表現:")
        print(f"   總交易數: {total_trades}筆")
        mode_capital = self.initial_capital * len(self.active_modes)
        pct_total = (total_pnl / mode_capital) * 100 if mode_capital else 0.0
        print(f"   總盈虧: ${total_pnl:.2f} USDT ({pct_total:+.2f}%)")
        
        # 🆕 M_NEW 報告
        if self.m_new_config['enabled']:
            m_new_pnl = self.m_new_balance - 100.0
            m_new_pct = (m_new_pnl / 100.0) * 100
            status_icon = "💀" if m_new_pnl <= -50 else "🔴" if m_new_pnl < 0 else "🟢"
            print(f"\n🔥 M_NEW 測試模式 (20x 做空):")
            print(f"   {status_icon} 最終餘額: ${self.m_new_balance:.2f} USDT ({m_new_pct:+.2f}%)")
            if self.m_new_config['order']:
                order = self.m_new_config['order']
                if order.exit_time:
                    print(f"   📊 狀態: 已平倉 ({order.exit_reason})")
                else:
                    print(f"   📊 狀態: 持倉中")
            else:
                print(f"   📊 狀態: 未觸發")
        
        print(f"{'='*80}\n")
        
        # 🆕 更新 JSON 的 metadata（仿照 paper_trading_system.py）
        try:
            with open(self.json_filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 更新最終 metadata
            data['metadata']['end_timestamp'] = datetime.now().strftime('%Y%m%d_%H%M%S')
            data['metadata']['final_balances'] = {
                mode.name: self.balances[mode] for mode in self.active_modes
            }
            
            with open(self.json_filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            print(f"💾 最終數據已保存: {self.json_filename}")
            
        except Exception as e:
            print(f"⚠️  更新最終數據失敗: {e}")
        
        # 🆕 保存可讀的 TXT log（仿照 paper_trading_system.py）
        try:
            with open(self.log_filename, 'w', encoding='utf-8') as f:
                f.write("=" * 80 + "\n")
                f.write("📝 Hybrid Paper Trading Log - 完整記錄\n")
                f.write("=" * 80 + "\n")
                f.write(f"⏰ 測試時間: {self.save_timestamp}\n")
                f.write(f"💰 初始資金: {self.initial_capital} USDT (每模式)\n")
                f.write(f"⏱️  測試時長: {self.test_duration_hours} 小時\n")
                f.write("=" * 80 + "\n\n")
                
                # 寫入每種模式的詳細記錄
                for mode in self.active_modes:
                    orders = self.orders[mode]
                    
                    strategy_info = self.mode_info[mode]
                    mode_name = f"{strategy_info['emoji']} {strategy_info['name']}"                    
                    f.write(f"\n{mode_name}\n")
                    f.write("-" * 80 + "\n")
                    f.write(f"訂單總數: {len(orders)}\n")
                    f.write(f"當前餘額: {self.balances[mode]:.2f} USDT\n\n")
                    
                    if orders:
                        for i, order in enumerate(orders, 1):
                            f.write(f"訂單 #{i}\n")
                            f.write(f"  方向: {order.direction}\n")
                            f.write(f"  進場價: {order.actual_entry_price}\n")
                            f.write(f"  投入金額: {order.position_value}\n")
                            if order.exit_price:
                                f.write(f"  出場價: {order.exit_price}\n")
                                f.write(f"  盈虧: {order.pnl_usdt:.2f} USDT\n")
                            f.write("\n")
                    else:
                        f.write("  無訂單記錄\n\n")
                
                f.write("=" * 80 + "\n")
                f.write("報告結束\n")
                f.write("=" * 80 + "\n")
            
            print(f"📝 可讀 Log 已保存: {self.log_filename}")
            
        except Exception as e:
            print(f"⚠️  保存 TXT log 失敗: {e}")
    
if __name__ == "__main__":
    # 簡單的參數解析
    duration_hours = 8.0
    if len(sys.argv) > 1:
        try:
            duration_hours = float(sys.argv[1])
        except ValueError:
            print("⚠️ 無效的時間參數，使用預設值 8.0 小時")
    
    print(f"🚀 啟動 Hybrid Multi-Mode Paper Trading System")
    print(f"⏱️  測試時長: {duration_hours} 小時")
    
    # 創建並運行系統
    system = HybridPaperTradingSystem(test_duration_hours=duration_hours)
    
    try:
        asyncio.run(system.run())
    except KeyboardInterrupt:
        print("\n⚠️ 用戶中斷測試")
        system.generate_report()
    except Exception as e:
        print(f"\n❌ 發生未預期錯誤: {e}")
        import traceback
        traceback.print_exc()
