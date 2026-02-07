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
# 🆕 爆倉瀑布即時偵測
try:
    from src.metrics.liquidation_cascade_detector import (
        LiquidationCascadeDetector,
        CascadeAlert,
        CascadeSnapshot,
        CascadeLevel,
        CascadeDirection,
    )
    LIQUIDATION_CASCADE_AVAILABLE = True
except ImportError:
    LIQUIDATION_CASCADE_AVAILABLE = False
    LiquidationCascadeDetector = None
    CascadeAlert = None
    print("⚠️ LiquidationCascadeDetector 不可用，即時爆倉偵測已停用")

from src.exchange.maker_order_manager import (
    MakerOrderManager,
    MakerOrder,
    MakerOrderStatus,
    calculate_fee_impact,
    should_use_maker
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
        is_maker: bool = False,  # 🆕 是否為 Maker 訂單
        # 🆕 Maker 掛單參數
        maker_limit_price: float = 0,  # Maker 掛單價格
        maker_timeout_seconds: float = 30.0,  # 超時秒數
        maker_allow_taker_fallback: bool = False  # 🔧 超時後取消訂單，不使用 Taker（避免高手續費風險）
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
        
        # 🆕 Maker 掛單狀態
        self.maker_limit_price = maker_limit_price
        self.maker_timeout_seconds = maker_timeout_seconds
        self.maker_allow_taker_fallback = maker_allow_taker_fallback
        self.maker_status = "PENDING" if (is_maker and maker_limit_price > 0) else "FILLED"
        # PENDING = 等待成交, FILLED = 已成交, CANCELLED = 已取消, TAKER_FALLBACK = 超時後用Taker
        self.maker_created_time = time.time() if self.maker_status == "PENDING" else None
        self.maker_filled_time = None if self.maker_status == "PENDING" else time.time()
        
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
    
    def check_maker_fill(self, current_price: float, high_price: float = None, low_price: float = None) -> str:
        """
        🆕 檢查 Maker 掛單是否應該成交
        
        模擬邏輯：
        - LONG 訂單：當價格下跌到掛單價或更低時成交
        - SHORT 訂單：當價格上漲到掛單價或更高時成交
        - 使用 high/low 可以檢查是否在某根K線內觸及
        
        Returns:
            "FILLED" - 已成交
            "PENDING" - 仍在等待
            "TIMEOUT_TAKER" - 超時，用 Taker 補單
            "TIMEOUT_CANCELLED" - 超時，取消訂單
        """
        if self.maker_status != "PENDING":
            return self.maker_status
        
        # 檢查超時
        elapsed = time.time() - self.maker_created_time
        if elapsed > self.maker_timeout_seconds:
            if self.maker_allow_taker_fallback:
                # 超時：用 Taker 補單
                self.maker_status = "TAKER_FALLBACK"
                self.is_maker = False
                self.actual_entry_price = current_price * (1.0002 if self.direction == "LONG" else 0.9998)
                self.maker_filled_time = time.time()
                # 重新計算手續費
                fee_rate = 0.0005  # Taker
                self.entry_fee = self.position_value * self.leverage * fee_rate
                return "TIMEOUT_TAKER"
            else:
                self.maker_status = "CANCELLED"
                return "TIMEOUT_CANCELLED"
        
        # 檢查是否成交
        # 使用 high/low 範圍檢查（如果提供）
        price_touched = False
        
        if self.direction == "LONG":
            # 做多：掛買單，價格需要下跌到掛單價
            check_price = low_price if low_price else current_price
            if check_price <= self.maker_limit_price:
                price_touched = True
        else:  # SHORT
            # 做空：掛賣單，價格需要上漲到掛單價
            check_price = high_price if high_price else current_price
            if check_price >= self.maker_limit_price:
                price_touched = True
        
        if price_touched:
            self.maker_status = "FILLED"
            self.maker_filled_time = time.time()
            self.actual_entry_price = self.maker_limit_price  # 以掛單價成交
            self.entry_time = datetime.now().isoformat()  # 更新進場時間為實際成交時間
            return "FILLED"
        
        return "PENDING"
        
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
        
        # 🔧 方案 D: 延長最小持倉時間 (10s -> 60s)，給價格更多時間發展
        effective_min_holding = max(self.min_holding_seconds, 60.0)
        if holding_seconds < effective_min_holding:
            return None
        
        # 計算當前盈虧
        _, pnl_pct = self.update_unrealized_pnl(current_price)
        
        # 🔧 方案 C: 手續費感知 - 計算「扣除手續費後的真實盈虧」
        # Taker 雙邊手續費 = 0.1% * leverage = 10% ROI (100x槓桿下)
        fee_rate = -0.0001 if getattr(self, 'is_maker', False) else 0.0005
        total_fee_pct = fee_rate * self.leverage * 2 * 100  # 開+平倉，轉為百分比
        net_pnl_pct = pnl_pct - total_fee_pct  # 扣除手續費後的淨盈虧
        
        holding_hours = holding_seconds / 3600
        
        # ========== 1. 止盈 (使用淨盈虧判斷) ==========
        # 🔧 方案 C: 只有當「淨盈虧」達到止盈目標時才平倉
        if net_pnl_pct >= self.take_profit_pct:
            return "TAKE_PROFIT"
        
        # ========== 2. 止損 (仍使用毛盈虧，避免虧損擴大) ==========
        active_stop_loss_pct = self.dynamic_stop_loss_pct
        if pnl_pct <= -active_stop_loss_pct:
            return "VPIN_PROTECTIVE_STOP" if self.vpin_risk_mode else "STOP_LOSS"
        
        # ========== 3. 追蹤止損（如果有設定）==========
        if self.trailing_stop_pct and self.peak_pnl_pct > 0:
            # 計算從峰值回撤的幅度
            drawdown_from_peak = self.peak_pnl_pct - pnl_pct
            
            # 🔧 v2.1: 支持兩種模式
            # 正數 = 比例模式 (舊邏輯): trailing_distance = TP * trailing_stop_pct
            # 負數 = 絕對值模式 (AI): trailing_distance = abs(trailing_stop_pct)
            if self.trailing_stop_pct < 0:
                # AI 絕對值模式：直接使用 AI 指定的回調百分比
                trailing_distance = abs(self.trailing_stop_pct)
            else:
                # 舊的比例模式
                trailing_distance = self.take_profit_pct * self.trailing_stop_pct
            
            # 1. 最小持倉時間檢查（避免過早平倉）
            min_holding_seconds = 60  # 至少持有 60 秒
            if holding_seconds < min_holding_seconds:
                return None  # 不執行追蹤止損
            
            # 2. 必須已達到一定盈利才啟動追蹤止損
            # 🔧 v2.1: AI 模式使用 trailing_activation 參數
            if self.trailing_stop_pct < 0:
                # AI 模式：需要達到一定獲利才啟動 (預設 5%)
                min_profit_threshold = 5.0  # AI 模式至少獲利 5% 才啟動追蹤
            else:
                min_profit_threshold = self.take_profit_pct * 0.3
            
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
        vpin_spike_threshold = 0.85  # 🔧 提高門檻，避免過度敏感
        
        # 🔧 修復：計算合理的收緊止損（考慮手續費）
        # 原本 0.4% 在高槓桿下太緊，改為至少保證不會因手續費立即止損
        fee_aware_min_sl = 0.1 * self.leverage * 0.01 * 2 + 0.5  # 手續費成本 + 緩衝
        
        # 🔧 v2.6: 獲取策略類型，AI 模式不要動態收緊止損
        is_ai_mode = 'AI' in self.strategy or 'WHALE_HUNTER' in self.strategy or 'DRAGON' in self.strategy
        
        # 如果 VPIN 突增 且 已有盈利 -> 提前鎖定利潤
        # 🔧 v2.6: AI 模式不收緊止損，讓 AI 自己決定
        if current_vpin > vpin_spike_threshold and not is_ai_mode:
            # 🔧 收緊止損但不能低於手續費成本
            tightened_stop = max(self.stop_loss_pct * 0.7, fee_aware_min_sl, 1.5)  # 至少 1.5%
            if not self.vpin_risk_mode:
                self.vpin_risk_mode = True
                self.vpin_risk_trigger_time = holding_seconds
                self.dynamic_stop_loss_pct = min(self.dynamic_stop_loss_pct, tightened_stop)
            else:
                # 🔧 收緊止損但保持合理範圍
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

        # 目前啟用的狙擊模式 - 專注 M🐺 調整
        self.active_modes: List[TradingMode] = [
            TradingMode.M_AI_WHALE_HUNTER,   # 🐺 主策略
            TradingMode.M_DRAGON,            # 🐲 Dragon (Bridge 驅動)
        ]
        
        # M_NEW: 已停用
        self.m_new_config = {
            'enabled': False,
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
            TradingMode.MDOWN_DIRECTIONAL_SHORT: 'direction_probe_short',
            TradingMode.M_AI_WHALE_HUNTER: 'ai_whale_hunter',
            TradingMode.M_INVERSE_WOLF: 'ai_whale_hunter',  # 🐺🔄 Inverse Wolf 使用相同的邏輯，但在內部會反轉信號
            TradingMode.M_DRAGON: 'ai_whale_hunter',  # 🐲 Dragon 使用相同的邏輯，但在內部會區分 Bridge
            TradingMode.M_DRAGON2: 'ai_dragon2',      # 🐲2 Dragon V2: 改良版，加入鯨魚過濾
            TradingMode.M_SHRIMP: 'ai_shrimp',  # 🦐 Shrimp: 優化持倉時間版本
            TradingMode.M_BIRD: 'ai_shrimp',     # 🐦 Bird: 反向 Shrimp
            TradingMode.M_LION: 'ai_lion'        # 🦁 Lion: v2.0 Whale Strategy Enhanced
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
            TradingMode.MDOWN_DIRECTIONAL_SHORT: "Mdown Bias Probe",
            TradingMode.M_AI_WHALE_HUNTER: "M🐺 AI Whale Hunter",
            TradingMode.M_INVERSE_WOLF: "M🐺🔄 Inverse Wolf",
            TradingMode.M_DRAGON: "M🐲 AI Dragon",
            TradingMode.M_DRAGON2: "M🐲2 Dragon V2",
            TradingMode.M_SHRIMP: "M🦐 AI Shrimp (GPT)",
            TradingMode.M_BIRD: "M🐦 AI Bird (Kimi)",
            TradingMode.M_LION: "M🦁 AI Lion (v2.0)"
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
            TradingMode.MDOWN_DIRECTIONAL_SHORT: '🔴Mdown',
            TradingMode.M_AI_WHALE_HUNTER: '🐺M',
            TradingMode.M_INVERSE_WOLF: '🐺🔄',
            TradingMode.M_DRAGON: '🐲M',
            TradingMode.M_DRAGON2: '🐲2',
            TradingMode.M_SHRIMP: '🦐M',
            TradingMode.M_BIRD: '🐦M',
            TradingMode.M_LION: '🦁M'
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
                    TradingMode.MDOWN_DIRECTIONAL_SHORT,
                    TradingMode.M_AI_WHALE_HUNTER,
                    TradingMode.M_INVERSE_WOLF,
                    TradingMode.M_DRAGON,
                    TradingMode.M_DRAGON2,
                    TradingMode.M_SHRIMP,
                    TradingMode.M_BIRD,
                    TradingMode.M_LION
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
                    TradingMode.MDOWN_DIRECTIONAL_SHORT,
                    TradingMode.M_AI_WHALE_HUNTER,
                    TradingMode.M_INVERSE_WOLF,
                    TradingMode.M_DRAGON,
                    TradingMode.M_DRAGON2,
                    TradingMode.M_SHRIMP,
                    TradingMode.M_BIRD,
                    TradingMode.M_LION
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
                    TradingMode.MDOWN_DIRECTIONAL_SHORT,
                    TradingMode.M_AI_WHALE_HUNTER,
                    TradingMode.M_INVERSE_WOLF,
                    TradingMode.M_DRAGON,
                    TradingMode.M_DRAGON2,
                    TradingMode.M_SHRIMP,
                    TradingMode.M_BIRD,
                    TradingMode.M_LION
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
                    TradingMode.MDOWN_DIRECTIONAL_SHORT,
                    TradingMode.M_AI_WHALE_HUNTER,
                    TradingMode.M_INVERSE_WOLF,
                    TradingMode.M_DRAGON,
                    TradingMode.M_DRAGON2,
                    TradingMode.M_SHRIMP,
                    TradingMode.M_BIRD,
                    TradingMode.M_LION
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
        
        # 🆕 動態獲利配置系統 (手續費感知)
        self.profit_config_path = "config/ai_profit_dynamic.json"
        self.profit_config = self._load_profit_config()
        self.last_profit_config_reload = time.time()
        
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
        
        # 🆕 Bar 累積器 (🔧 v3.0: 每 3 秒生成一個 bar，配合 AI 5 秒判斷)
        self._bar_interval = 3  # 秒 (原 5 秒)
        self._current_bar = {
            'open': None,
            'high': None,
            'low': None,
            'close': None,
            'volume': 0.0,
            'start_time': None
        }
        
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
            TradingMode.MDOWN_DIRECTIONAL_SHORT: 10,   # Mdown 方向探針：維持短冷卻即可重上
            TradingMode.M_AI_WHALE_HUNTER: 180,        # 🔧 v3.0: AI Whale Hunter: 3 分鐘冷卻 (原 60s，配合 8h/12次目標)
            TradingMode.M_DRAGON: 180,                 # 🔧 v3.0: AI Dragon: 3 分鐘冷卻
            TradingMode.M_SHRIMP: 180,                 # 🦐 Shrimp: 3 分鐘冷卻（配合最大持倉時間）
            TradingMode.M_BIRD: 180                    # 🐦 Bird: 3 分鐘冷卻（配合最大持倉時間）
        }
        
        # 🆕 連虧保護機制 (v10.7 AI 智能復盤)
        self.consecutive_losses: Dict[TradingMode, int] = {
            mode: 0 for mode in self.active_modes
        }
        self.loss_cooldown_until: Dict[TradingMode, float] = {
            mode: 0 for mode in self.active_modes
        }
        # 🆕 v10.7 AI 智能復盤：儲存最近虧損供 AI 分析
        self.pending_loss_review: Dict[TradingMode, dict] = {}
        
        # 🆕 M🐳 反轉頻率限制（防刷單洗盤）
        self.whale_reversal_tracker: Dict[TradingMode, dict] = {
            TradingMode.M_WHALE_WATCHER: {
                'last_direction': None,        # 上一次方向
                'reversal_count': 0,           # 30分鐘內反轉次數
                'reversal_timestamps': [],     # 反轉時間戳記
                'penalty_cooldown': 0,         # 懲罰性冷卻（秒）
            }
        }
        
        # 🏷️ Maker 訂單管理器 (降低手續費成本)
        self.maker_manager = MakerOrderManager(
            default_timeout=60.0,           # 預設等待 60 秒
            default_taker_fallback=False,   # 🔧 超時取消訂單，不使用 Taker（避免高手續費風險）
            maker_offset_bps=1.0            # 掛單偏移 1 個基點
        )
        # 🔧 改為全 Taker 模式 - 犧牲手續費換取即時成交
        # Taker 成本 (60x): 0.05% * 60 * 2 = 6% ROI
        # 需要 TP >= 11% 才能淨賺 5%
        self.maker_enabled = False  # 🔧 關閉 Maker，全用 Taker
        self.maker_stats_display_interval = 300  # 每 5 分鐘顯示一次統計
        self.last_maker_stats_time = 0
        
        # ═══════════════════════════════════════════════════════════════
        # 🆕 延遲進場確認機制 (Entry Delay Confirmation)
        # 目的：避免追高殺低，等待 5 秒確認信號穩定
        # ═══════════════════════════════════════════════════════════════
        self.entry_delay_enabled = True  # 啟用延遲進場
        self.entry_delay_seconds = 5.0   # 延遲 5 秒
        self.pending_entry_signals = {}  # {mode: {'signal': {...}, 'timestamp': time, 'price_at_signal': float}}
        
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
        
        # 🆕 爆倉瀑布即時偵測器
        self._cascade_detector: Optional[LiquidationCascadeDetector] = None
        self._last_cascade_snapshot: Optional[CascadeSnapshot] = None
        self._last_cascade_alert: Optional[CascadeAlert] = None
        self._cascade_signal_active: bool = False
        self._cascade_signal_direction: str = "HOLD"
        self._cascade_signal_strength: float = 0.0
        
        # 🆕 鯨魚訊號有效性追蹤器 (Whale Signal Effectiveness Tracker)
        # 追蹤鯨魚訊號發出後的價格變化，驗證訊號是否真的影響價格
        self.whale_signal_tracker = {
            'signal_history': deque(maxlen=100),  # 最近 100 個訊號記錄
            'current_signal': None,               # 當前活躍訊號
            'effectiveness_stats': {
                'total_signals': 0,
                'effective_signals': 0,           # 價格朝訊號方向移動 >= 閾值
                'ineffective_signals': 0,         # 價格無反應或反向
                'avg_price_impact_pct': 0,        # 平均價格影響 %
                'avg_response_time_sec': 0,       # 平均反應時間（秒）
            },
            'config': {
                'min_impact_pct': 0.05,           # 最小有效影響 0.05%
                'max_wait_seconds': 120,          # 最長等待 2 分鐘
                'check_intervals': [15, 30, 60, 120],  # 檢查時間點（秒）
            }
        }
        
        # 🆕 鯨魚訊號品質特徵追蹤 (用於判斷當前訊號是否可信)
        self.whale_signal_quality_tracker = {
            'recent_trades_detail': deque(maxlen=200),  # 詳細的大單記錄
            'price_at_signal': 0,                        # 訊號發出時的價格
            'orderbook_at_signal': {},                   # 訊號發出時的訂單簿狀態
        }
        
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
            "🤖 AI 雙龍對決 (Wolf vs Dragon)": [
                TradingMode.M_AI_WHALE_HUNTER,
                TradingMode.M_DRAGON
            ],
            "🦐🐦 AI 優化持倉 (Shrimp vs Bird)": [
                TradingMode.M_SHRIMP,
                TradingMode.M_BIRD
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
        """Evaluate whether LP + whale + cascade conditions align for M🥊."""
        default_rules = {
            'L_long_liq_min': 70,
            'L_short_liq_min': 70,
            'liq_diff_min': 25,
            'whale_dominance_min': 0.6,
            'obi_long_min': 0.1,
            'obi_short_max': -0.1,
            # 🆕 爆倉瀑布相關閾值
            'cascade_strength_min': 40,        # 瀑布信號強度閾值
            'cascade_boost_confidence': 0.15,  # 瀑布信號加成
            'cascade_override_threshold': 70,  # 高強度瀑布可覆蓋部分條件
        }

        # 🆕 檢查即時爆倉瀑布信號
        cascade_signal = snapshot.get('cascade_signal', {})
        cascade_active = cascade_signal.get('active', False)
        cascade_direction = cascade_signal.get('direction', 'HOLD')
        cascade_strength = cascade_signal.get('strength', 0.0)
        
        # 🆕 如果有強烈的爆倉瀑布信號，可以放寬其他條件
        cascade_override = (
            cascade_active and 
            cascade_strength >= default_rules['cascade_override_threshold'] and
            cascade_direction in ['LONG', 'SHORT']
        )

        if not pressure_obj and not cascade_override:
            return None, 'M🥊 waiting for liquidation snapshot'

        # Guard against stale data (如果有瀑布覆蓋則跳過)
        if not cascade_override:
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

        # 獲取爆倉壓力分數
        long_score = pressure_obj.long_score if pressure_obj else 50.0
        short_score = pressure_obj.short_score if pressure_obj else 50.0
        diff = abs(long_score - short_score)
        
        # 🆕 爆倉瀑布可以提升壓力分數的判定
        if cascade_active and cascade_strength >= thresholds['cascade_strength_min']:
            if cascade_direction == 'SHORT':  # 多頭被爆，應該做空
                long_score = max(long_score, cascade_strength)  # 提升多頭壓力
            elif cascade_direction == 'LONG':  # 空頭被爆，應該做多
                short_score = max(short_score, cascade_strength)  # 提升空頭壓力
            diff = abs(long_score - short_score)
        
        long_ready = (
            long_score >= thresholds['L_long_liq_min'] and
            (long_score - short_score) >= thresholds['liq_diff_min']
        )
        short_ready = (
            short_score >= thresholds['L_short_liq_min'] and
            (short_score - long_score) >= thresholds['liq_diff_min']
        )

        # 🆕 爆倉瀑布強信號可以直接觸發（覆蓋模式）
        if cascade_override:
            if cascade_direction == 'SHORT':
                long_ready = True
            elif cascade_direction == 'LONG':
                short_ready = True
            # 如果沒有鯨魚方向，用瀑布方向
            if not net_direction:
                net_direction = cascade_direction
                dominance = max(dominance, 0.5)  # 給予基礎 dominance

        if not long_ready and not short_ready:
            return None, (
                f"M🥊 waiting for LP imbalance (L={long_score:.1f}, S={short_score:.1f})"
            )
        if not net_direction:
            return None, 'M🥊 waiting for whale direction'
        
        # 🆕 如果有瀑布信號，降低 dominance 要求
        dominance_threshold = thresholds['whale_dominance_min']
        if cascade_active and cascade_strength >= thresholds['cascade_strength_min']:
            dominance_threshold *= 0.7  # 降低 30%
            
        if dominance < dominance_threshold:
            return None, (
                f"M🥊 whale dominance {dominance:.2f} < {dominance_threshold:.2f}"
            )

        direction = None
        reason = ''
        if (
            long_ready
            and net_direction == 'SHORT'
            and obi <= thresholds['obi_short_max']
        ):
            direction = 'SHORT'
            cascade_tag = f" 🔥CASCADE={cascade_strength:.0f}" if cascade_active else ""
            reason = (
                f"M🥊 SHORT: L_liq={long_score:.1f}, diff={long_score - short_score:.1f}, "
                f"whale={dominance:.2f}, obi={obi:.2f}{cascade_tag}"
            )
        elif (
            short_ready
            and net_direction == 'LONG'
            and obi >= thresholds['obi_long_min']
        ):
            direction = 'LONG'
            cascade_tag = f" 🔥CASCADE={cascade_strength:.0f}" if cascade_active else ""
            reason = (
                f"M🥊 LONG: S_liq={short_score:.1f}, diff={short_score - long_score:.1f}, "
                f"whale={dominance:.2f}, obi={obi:.2f}{cascade_tag}"
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
        
        # 🆕 加入爆倉瀑布對 confidence 的加成
        cascade_bonus = 0.0
        if cascade_active and cascade_strength >= thresholds['cascade_strength_min']:
            cascade_bonus = thresholds['cascade_boost_confidence'] * (cascade_strength / 100)
        
        confidence = min(
            1.0,
            0.35 * score_component + 0.25 * dominance + 0.2 * diff_component + 
            0.1 * (pressure_obj.bias_confidence if pressure_obj else 0.5) + cascade_bonus
        )
        size_boost = max(0.0, dominance - thresholds['whale_dominance_min'])
        
        # 🆕 爆倉瀑布也能加大 size
        if cascade_active and cascade_strength >= 60:
            size_boost += 0.2  # 額外 20% size boost
            
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
            'thresholds': thresholds,
            # 🆕 爆倉瀑布相關
            'cascade_active': cascade_active,
            'cascade_direction': cascade_direction,
            'cascade_strength': cascade_strength,
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
        "Hybrid 策略核心邏輯:"
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
        """
        🔧 修正版：累積真正的 OHLC bar (每 5 秒一個)
        
        之前的問題：把每個 tick 的 bid/ask 當作 high/low，
        導致 ATR 計算的是「買賣價差」而非實際價格波動！
        
        修正：累積價格，每 5 秒生成一個真正的 OHLC bar
        """
        if best_bid is None or best_ask is None:
            return
        
        mid_price = (best_bid + best_ask) / 2
        now = time.time()
        
        # 初始化當前 bar
        if self._current_bar['start_time'] is None:
            self._current_bar = {
                'open': mid_price,
                'high': mid_price,
                'low': mid_price,
                'close': mid_price,
                'volume': self.pending_volume,
                'start_time': now
            }
            self.pending_volume = 0.0
            return
        
        # 更新當前 bar 的 high/low/close
        self._current_bar['high'] = max(self._current_bar['high'], mid_price)
        self._current_bar['low'] = min(self._current_bar['low'], mid_price)
        self._current_bar['close'] = mid_price
        self._current_bar['volume'] += self.pending_volume
        self.pending_volume = 0.0
        
        # 檢查是否該結束當前 bar (每 5 秒)
        elapsed = now - self._current_bar['start_time']
        if elapsed >= self._bar_interval:
            # 完成當前 bar，加入歷史
            self.price_bars['high'].append(self._current_bar['high'])
            self.price_bars['low'].append(self._current_bar['low'])
            self.price_bars['close'].append(self._current_bar['close'])
            self.price_bars['volume'].append(self._current_bar['volume'])
            self.price_bars['timestamp'].append(now)
            
            # 開始新的 bar
            self._current_bar = {
                'open': mid_price,
                'high': mid_price,
                'low': mid_price,
                'close': mid_price,
                'volume': 0.0,
                'start_time': now
            }

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
            compare_low = lambda a, b: a < b
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
        
        # 🆕 加入即時爆倉瀑布信號
        cascade_signal = self.get_cascade_signal()
        snapshot['cascade_signal'] = cascade_signal
        snapshot['cascade_active'] = cascade_signal.get('active', False)
        snapshot['cascade_direction'] = cascade_signal.get('direction', 'HOLD')
        snapshot['cascade_strength'] = cascade_signal.get('strength', 0.0)

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
                                        min_dominance = whale_rules.get('min', 0.6)

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
                                        
                                        if dominance_ratio >= min_dominance:  # 使用動態配置的集中度門檻
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
            'structure_buffer_default': 0.0015,
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

        # 🧠 M_AI_WHALE_HUNTER 特殊處理 - AI Trap Master 2.0
        if style == 'ai_whale_hunter':
            try:
                # 判斷是 M_WOLF (GPT-4), M_INVERSE_WOLF, 還是 M_DRAGON (Qwen3)
                is_dragon = mode.name == 'M_DRAGON'
                is_inverse = mode.name == 'M_INVERSE_WOLF'
                
                bridge_file = "ai_dragon_bridge.json" if is_dragon else "ai_wolf_bridge.json"
                
                # 定義反轉包裝器
                def finalize_wrapper(d):
                    if is_inverse:
                        act = d.get('action')
                        if act == 'LONG': d['action'] = 'SHORT'
                        elif act == 'SHORT': d['action'] = 'LONG'
                        elif act == 'ADD_LONG': d['action'] = 'ADD_SHORT'
                        elif act == 'ADD_SHORT': d['action'] = 'ADD_LONG'
                        
                        if act in ['LONG', 'SHORT', 'ADD_LONG', 'ADD_SHORT']:
                            d['reason'] = f"[INVERTED] {d.get('reason', '')}"
                    return finalize(d)

                # 讀取 Bridge 數據
                if not os.path.exists(bridge_file):
                    return finalize_wrapper({'action': 'HOLD', 'reason': f'{mode.name} Bridge not found'})
                
                with open(bridge_file, 'r') as f:
                    bridge = json.load(f)
                
                ai_cmd = bridge.get('ai_to_dragon' if is_dragon else 'ai_to_wolf', {})
                wolf_status = bridge.get('dragon_to_ai' if is_dragon else 'wolf_to_ai', {})
                
                # AI 指令
                action = ai_cmd.get('command', 'WAIT')
                ai_direction = ai_cmd.get('direction', 'NEUTRAL')
                confidence = ai_cmd.get('confidence', 50)
                whale_reversal_price = ai_cmd.get('whale_reversal_price', 0)
                pred_time_str = ai_cmd.get('timestamp')
                
                # 🆕 Phase 1: 讀取 AI 動態參數
                ai_dynamic_params = ai_cmd.get('dynamic_params', {})
                ai_prediction = ai_cmd.get('ai_prediction', {})
                
                # 動態參數 (AI 控制)
                ai_leverage = ai_dynamic_params.get('leverage', 60)
                ai_take_profit = ai_dynamic_params.get('take_profit_pct', 10.0)
                ai_stop_loss = ai_dynamic_params.get('stop_loss_pct', 3.5)
                ai_position_size = ai_dynamic_params.get('position_size_pct', 100)
                ai_trailing_activation = ai_dynamic_params.get('trailing_activation', 7.0)
                ai_trailing_distance = ai_dynamic_params.get('trailing_distance', 2.5)
                ai_entry_strategy = ai_dynamic_params.get('entry_strategy', 'MARKET')
                ai_max_holding = ai_dynamic_params.get('max_holding_minutes', 30)
                
                # AI 預測
                ai_price_target = ai_prediction.get('price_target', 0)
                ai_expected_move = ai_prediction.get('expected_move_pct', 0.5)
                
                # 市場狀態 (從 Bridge 讀取)
                whale_status_data = wolf_status.get('whale_status', {})
                whale_direction_from_bridge = whale_status_data.get('current_direction')
                whale_dominance_from_bridge = whale_status_data.get('dominance', 0)
                
                # 🔧 修復：優先使用實時大單訊號，而不是 Bridge 的鯨魚狀態
                # Bridge 的 whale_direction 可能是舊的 AI 分析，實時大單更準確
                realtime_whale = getattr(self, 'large_trade_signal', {})
                realtime_direction = realtime_whale.get('direction')
                realtime_dominance = realtime_whale.get('dominance_ratio', 0)
                
                # 如果實時大單有明確方向且集中度高，優先使用
                if realtime_direction and realtime_dominance >= 0.65:
                    whale_direction = realtime_direction
                    whale_dominance = realtime_dominance
                else:
                    whale_direction = whale_direction_from_bridge
                    whale_dominance = whale_dominance_from_bridge
                
                micro = wolf_status.get('market_microstructure', {})
                obi_value = micro.get('obi', 0)
                vpin_value = micro.get('vpin', 0)
                
                volatility = wolf_status.get('volatility', {})
                atr_pct = volatility.get('atr_pct', 0)
                is_dead_market = volatility.get('is_dead_market', False)
                
                current_price = self.latest_price
                
                # 🔧 顯示實時 vs Bridge 的差異
                if realtime_direction and realtime_direction != whale_direction_from_bridge:
                    print(f"   {'🐲' if is_dragon else '🐺'} ⚠️ 實時大單({realtime_direction})≠Bridge({whale_direction_from_bridge})")
                
                # 🆕 顯示 AI 動態參數
                if ai_dynamic_params:
                    print(f"   {'🐲' if is_dragon else '🐺'} 🤖 AI動態: Lev={ai_leverage}x, TP={ai_take_profit}%, SL={ai_stop_loss}%, Size={ai_position_size}%")
                
                print(f"   {'🐲' if is_dragon else '🐺'} {mode.name}: AI={action}, Whale={whale_direction}, Dom={whale_dominance:.2f}, ATR={atr_pct:.4f}%")
                
                # 檢查訊號時效
                is_fresh = False
                if pred_time_str:
                    pred_time = datetime.fromisoformat(pred_time_str)
                    age_seconds = (datetime.now() - pred_time).total_seconds()
                    is_fresh = age_seconds < 120
                    if not is_fresh:
                        return finalize_wrapper({'action': 'HOLD', 'reason': f'{mode.name} Signal stale ({age_seconds:.0f}s)'})
                
                # ═══════════════════════════════════════════════════════════
                # 🆕 v10.7 防追單檢查 (Anti-Chase Filter)
                # ═══════════════════════════════════════════════════════════
                try:
                    with open('config/whale_ctx_strategy.json', 'r') as f:
                        whale_ctx_cfg = json.load(f)
                        anti_chase_cfg = whale_ctx_cfg.get('whale_strategy', {}).get('stability_filter', {}).get('anti_chase', {})
                        
                        if anti_chase_cfg.get('enabled', True):
                            max_price_move_1m_pct = anti_chase_cfg.get('max_price_move_1m_pct', 0.3)
                            
                            # 計算 1 分鐘價格變化 (使用 price_history)
                            price_change_1m = 0.0
                            if len(self.price_history) > 0:
                                now_ts = time.time()
                                price_1m_ago = None
                                for ts, p in self.price_history:
                                    if now_ts - ts >= 60:
                                        price_1m_ago = p
                                        break # 找到最近的一個大於 60s 的
                                
                                if price_1m_ago:
                                    price_change_1m = (current_price - price_1m_ago) / price_1m_ago * 100
                                    
                                    if action == 'LONG' and price_change_1m > max_price_move_1m_pct:
                                        print(f"   🚫 [{mode.name}] Anti-Chase: Price up {price_change_1m:.2f}% > {max_price_move_1m_pct}% → Block LONG")
                                        return finalize_wrapper({'action': 'HOLD', 'reason': f'Anti-Chase: Price up {price_change_1m:.2f}%'})
                                    
                                    if action == 'SHORT' and price_change_1m < -max_price_move_1m_pct:
                                        print(f"   🚫 [{mode.name}] Anti-Chase: Price down {price_change_1m:.2f}% < -{max_price_move_1m_pct}% → Block SHORT")
                                        return finalize_wrapper({'action': 'HOLD', 'reason': f'Anti-Chase: Price down {price_change_1m:.2f}%'})
                except Exception as e:
                    print(f"⚠️ Anti-Chase check failed in Paper Trader: {e}")

                # ═══════════════════════════════════════════════════════════
                # 🎯 策略 2: 三重確認機制 (AI + 鯨魚 + OBI) - 僅作為參考顯示
                # ═══════════════════════════════════════════════════════════
                
                # 🔧 v2.0: 移除覆蓋邏輯！AI 說 HOLD 就是 HOLD
                # 原因：當 AI 因 Circuit Breaker 連虧暫停時，不應該自作主張進場
                # 其他保護機制（反向冷卻、洗盤偵測）已經足夠
                inferred_action = action  # LONG, SHORT, HOLD, WAIT
                if action == "HOLD":
                    # 僅記錄方向供參考，不覆蓋
                    if ai_direction == "BULLISH":
                        inferred_action = "LONG"  # 僅供顯示
                    elif ai_direction == "BEARISH":
                        inferred_action = "SHORT"  # 僅供顯示
                
                ai_agrees = (ai_direction == "BULLISH" and inferred_action == "LONG") or (ai_direction == "BEARISH" and inferred_action == "SHORT")
                whale_agrees = whale_direction and whale_direction == ("LONG" if inferred_action in ["LONG", "BULLISH"] else "SHORT")
                obi_agrees = (obi_value > 0.3 and inferred_action == "LONG") or (obi_value < -0.3 and inferred_action == "SHORT")
                
                confluence_count = sum([ai_agrees, whale_agrees, obi_agrees])
                
                print(f"   {'🐲' if is_dragon else '🐺'} Confluence: AI={ai_agrees}, Whale={whale_agrees}, OBI={obi_agrees} ({confluence_count}/3)")
                
                # 🚫 v2.0: 移除覆蓋邏輯！AI 說 HOLD 就不進場
                # 舊邏輯會在 confluence >= 2 時覆蓋為 LONG/SHORT，這是錯誤的
                if action == "HOLD":
                    # 顯示為什麼 HOLD（方便除錯）
                    print(f"   {'🐲' if is_dragon else '🐺'} ⏸️ AI 說 HOLD (方向傾向: {inferred_action}) → 遵守 HOLD，不覆蓋")
                
                # 🆕 如果 AI 說 CUT_LOSS 但鯨魚方向明確且集中度高 (≥0.75)
                # 這表示 AI 平倉後，市場有新的明確方向，直接跟隨鯨魚進場
                if action == "CUT_LOSS" and whale_direction and whale_dominance >= 0.75:
                    new_action = whale_direction  # LONG 或 SHORT
                    print(f"   {'🐲' if is_dragon else '🐺'} 🐋 AI說CUT_LOSS但鯨魚方向明確({whale_direction}, Dom={whale_dominance:.2f})")
                    print(f"   {'🐲' if is_dragon else '🐺'} 🐋 直接跟隨鯨魚進場: {new_action}")
                    action = new_action

                # ═══════════════════════════════════════════════════════════
                # 🔥 策略 1: 死水盤區間套利 (學習 M🐟)
                # ═══════════════════════════════════════════════════════════
                # 🐟 死水盤判斷 - ATR < 0.05% 或 Bridge 標記為死水
                # ═══════════════════════════════════════════════════════════
                dead_market_threshold = 0.05  # ATR < 0.05% 視為死水
                is_dead = is_dead_market or atr_pct < dead_market_threshold
                
                if is_dead:
                    print(f"   🐟 [{mode.name}] 死水盤偵測! ATR={atr_pct:.4f}% < {dead_market_threshold}% → 切換 Maker 模式")
                    
                    # 在死水盤環境下,利用 AI 預測的反轉點做區間交易
                    if whale_reversal_price > 0:
                        reversal_distance_pct = abs(current_price - whale_reversal_price) / current_price * 100
                        
                        # 0.3% 以內視為接近反轉點
                        if reversal_distance_pct < 0.3:
                            # 在預測反轉點附近,做反向單 (抄底/摸頂)
                            if ai_direction == "BEARISH" and current_price <= whale_reversal_price:
                                # AI 預測會跌,當前價格已到達/低於反轉點 → 做多抄底
                                return finalize_wrapper({
                                    'action': 'LONG',
                                    'reason': f'{mode.name} 🐟Dead Market Buy (Maker): ${current_price:.0f} at reversal ${whale_reversal_price:.0f}',
                                    'confidence': 0.85,
                                    'leverage': 50,
                                    'is_maker': True,  # 🔧 死水盤用 Maker
                                    'market_data': {
                                        'position_size_multiplier': 1.0,
                                        'trap_master_mode': 'dead_market_reversal',
                                        'is_dead_market': True,
                                        'quick_profit_target': 3.0  # 死水盤目標小 3% (Maker 無成本)
                                    }
                                })
                            elif ai_direction == "BULLISH" and current_price >= whale_reversal_price:
                                # AI 預測會漲,當前價格已到達/高於反轉點 → 做空摸頂
                                return finalize_wrapper({
                                    'action': 'SHORT',
                                    'reason': f'{mode.name} 🐟Dead Market Sell (Maker): ${current_price:.0f} at reversal ${whale_reversal_price:.0f}',
                                    'confidence': 0.85,
                                    'leverage': 50,
                                    'is_maker': True,  # 🔧 死水盤用 Maker
                                    'market_data': {
                                        'position_size_multiplier': 1.0,
                                        'trap_master_mode': 'dead_market_reversal',
                                        'is_dead_market': True,
                                        'quick_profit_target': 3.0  # 死水盤目標小 3%
                                    }
                                })
                    
                    # 🚨 死水盤突破檢查 (新增)
                    # 如果 AI 和 OBI 強烈一致 (2/3)，且信心度高，允許突破交易
                    if confluence_count >= 2 and confidence > 0.6:
                        print(f"   🚀 Dead Market Breakout Detected! Falling through to standard logic... (Conf: {confidence})")
                        # 不返回 HOLD，讓程式繼續往下執行標準策略
                    else:
                        # 🔧 死水盤但不在反轉點附近，且無突破跡象 → 必須等待
                        print(f"   🐟 [{mode.name}] 死水盤無突破跡象，HOLD 等待 (Conf: {confluence_count}/3, {confidence}%)")
                        return finalize_wrapper({'action': 'HOLD', 'reason': f'{mode.name} Dead Market: waiting for reversal or breakout (Conf: {confluence_count}/3)'})
                
                # ═══════════════════════════════════════════════════════════
                # 🎯 策略 2: 三重確認機制 (AI + 鯨魚 + OBI)
                # ═══════════════════════════════════════════════════════════
                
                # (已在上方計算 confluence_count)
                
                # 需要至少 2/3 一致才交易 -> 移除此限制，完全聽從 AI
                # if confluence_count < 2:
                #    return finalize({'action': 'HOLD', 'reason': f'M🐺 Low confluence: {confluence_count}/3'})
                
                # ═══════════════════════════════════════════════════════════
                # ⚡ 策略 3: 反轉點埋伏 (中等波動環境)
                # ═══════════════════════════════════════════════════════════
                if whale_reversal_price > 0:
                    reversal_distance_pct = abs(current_price - whale_reversal_price) / current_price * 100
                    
                    # 在反轉點 0.1%-0.5% 區間埋伏
                    if 0.1 < reversal_distance_pct < 0.5:
                        # 高集中度 (>0.8) 時才埋伏
                        if whale_dominance > 0.8:
                            # 做反向單,等待反轉
                            ambush_action = "LONG" if whale_direction == "SHORT" else "SHORT"
                            
                            return finalize_wrapper({
                                'action': ambush_action,
                                'reason': f'{mode.name} Ambush: {reversal_distance_pct:.2f}% from reversal (Dom: {whale_dominance:.2f})',
                                'confidence': min(0.9, confidence / 100.0),
                                'leverage': 50,  # 🔧 50x
                                'market_data': {
                                    'position_size_multiplier': 1.0,
                                    'trap_master_mode': 'reversal_ambush',
                                    'quick_profit_target': 7.0  # 淨利 7%
                                }
                            })
                
                # ═══════════════════════════════════════════════════════════
                # 🛡️ 硬指標保險絲檢查 - 防止 AI 失誤
                # ═══════════════════════════════════════════════════════════
                hard_fuse = self.profit_config.get('hard_fuse', {})
                if hard_fuse.get('enabled', True):
                    # 🆕 檢查 0: 鯨魚數據是否可用
                    # whale_dominance = 0 表示沒有收集到鯨魚數據（系統剛啟動/WebSocket斷線）
                    # 這不是「沒有主力」，而是「看不到主力」→ 盲目進場風險極高！
                    if whale_dominance == 0 or whale_dominance is None:
                        print(f"   🛡️ [保險絲] 鯨魚數據不可用 (dominance=0) → HOLD")
                        print(f"   ⚠️ 原因: 系統剛啟動/數據斷線，無法判斷市場方向")
                        return finalize_wrapper({'action': 'HOLD', 'reason': f'{mode.name} Fuse: No whale data available (dominance=0)'})
                    
                    # 檢查 1: 鯨魚集中度不足
                    min_dom = hard_fuse.get('min_whale_dominance', 0.5)
                    if whale_dominance < min_dom:
                        print(f"   🛡️ [保險絲] 鯨魚集中度 {whale_dominance:.2f} < {min_dom} → HOLD")
                        return finalize_wrapper({'action': 'HOLD', 'reason': f'{mode.name} Fuse: Low whale dominance ({whale_dominance:.2f})'})
                    
                    # 🚨 檢查 2: ABSOLUTE RULE #1 - 高集中度時必須跟隨鯨魚
                    # 當 whale_dominance >= 70% 時，AI 必須與 whale_direction 一致
                    # 否則 HOLD 等待方向一致，不可逆勢進場！
                    if whale_dominance >= 0.70 and whale_direction:
                        ai_action_direction = "LONG" if action == "LONG" else ("SHORT" if action == "SHORT" else None)
                        if ai_action_direction and ai_action_direction != whale_direction:
                            print(f"   🚨 [ABSOLUTE RULE #1] 鯨魚集中度 {whale_dominance:.0%} >= 70%!")
                            print(f"   🚨 AI說{action} 但鯨魚方向={whale_direction} → 強制跟隨鯨魚!")
                            # 強制覆蓋為鯨魚方向
                            action = whale_direction
                            print(f"   🐋 強制執行: {action} (跟隨鯨魚)")
                    
                    # 檢查 3: OBI 與 AI 方向強烈相反
                    max_obi_against = hard_fuse.get('max_obi_against', 0.5)
                    if action == 'LONG' and obi_value < -max_obi_against:
                        print(f"   🛡️ [保險絲] AI說LONG但OBI={obi_value:.2f} 極度看空 → HOLD")
                        return finalize_wrapper({'action': 'HOLD', 'reason': f'{mode.name} Fuse: OBI strongly against LONG ({obi_value:.2f})'})
                    if action == 'SHORT' and obi_value > max_obi_against:
                        print(f"   🛡️ [保險絲] AI說SHORT但OBI={obi_value:.2f} 極度看多 → HOLD")
                        return finalize_wrapper({'action': 'HOLD', 'reason': f'{mode.name} Fuse: OBI strongly against SHORT ({obi_value:.2f})'})
                
                # ═══════════════════════════════════════════════════════════
                # 📊 策略 4: 標準 AI 信號 - 使用 AI 動態參數
                # ═══════════════════════════════════════════════════════════
                if action in ['LONG', 'SHORT']:
                    # 🆕 Phase 2: 使用 AI 動態參數 (而不是硬編碼)
                    leverage = ai_leverage  # AI 決定槓桿
                    size_mult = ai_position_size / 100.0  # AI 決定倉位大小
                    
                    # 構建 reason 顯示 AI 參數
                    reason_suffix = f"(AI: {leverage}x, TP={ai_take_profit}%, SL={ai_stop_loss}%)"
                    
                    return finalize_wrapper({
                        'action': action,
                        'reason': f'{mode.name} AI Signal: {action} {reason_suffix}',
                        'confidence': confidence / 100.0,
                        'leverage': leverage,
                        'market_data': {
                            'position_size_multiplier': size_mult,
                            'trap_master_mode': 'ai_dynamic',
                            'quick_profit_target': ai_take_profit,  # 🆕 AI 控制止盈
                            # 🆕 Phase 1: 傳遞完整的 AI 動態參數
                            'ai_dynamic_params': {
                                'take_profit_pct': ai_take_profit,
                                'stop_loss_pct': ai_stop_loss,
                                'trailing_activation': ai_trailing_activation,
                                'trailing_distance': ai_trailing_distance,
                                'entry_strategy': ai_entry_strategy,
                                'max_holding_minutes': ai_max_holding,
                            },
                            'ai_prediction': {
                                'price_target': ai_price_target,
                                'expected_move_pct': ai_expected_move,
                            }
                        }
                    })
                
                # 其他指令 (ADD/CUT_LOSS/HOLD)
                elif action in ['ADD_LONG', 'ADD_SHORT']:
                    # 🆕 加倉也使用 AI 動態參數
                    return finalize_wrapper({
                        'action': action,
                        'reason': f'{mode.name} Add Position: {action}',
                        'confidence': confidence / 100.0,
                        'leverage': ai_leverage,  # 🆕 AI 決定
                        'market_data': {
                            'position_size_multiplier': ai_position_size / 100.0,
                            'ai_dynamic_params': ai_dynamic_params
                        }
                    })
                elif action == 'CUT_LOSS':
                    return finalize_wrapper({
                        'action': 'HOLD',
                        'reason': f'{mode.name} CUT_LOSS signal',
                        'confidence': 0.0,
                        'market_data': {'force_exit': True}
                    })
                else:
                    return finalize_wrapper({'action': 'HOLD', 'reason': f'{mode.name} {action}'})
                    
            except Exception as e:
                return finalize_wrapper({'action': 'HOLD', 'reason': f'{mode.name} Error: {str(e)}'})

        # 🐲2 M_DRAGON2 策略 - 使用獨立的 Dragon2 Advisor (Kimi + Wolf Prompt)
        # 目的: 純粹比較 GPT-4 vs Kimi 模型差異
        if style == 'ai_dragon2':
            try:
                # 🐲2 使用 Dragon Bridge，但讀取 ai_to_dragon2 (由 ai_trading_advisor_dragon2.py 寫入)
                bridge_file = "ai_dragon_bridge.json"
                
                def finalize_wrapper_dragon2(d):
                    return finalize(d)

                # 讀取 Bridge 數據
                if not os.path.exists(bridge_file):
                    return finalize_wrapper_dragon2({'action': 'HOLD', 'reason': 'M_DRAGON2 Bridge not found'})
                
                with open(bridge_file, 'r') as f:
                    bridge = json.load(f)
                
                # 🆕 Dragon2 讀取專屬的 ai_to_dragon2 (由 ai_trading_advisor_dragon2.py 寫入)
                ai_cmd = bridge.get('ai_to_dragon2', {})
                if not ai_cmd or not ai_cmd.get('command'):
                    # Fallback: 如果 dragon2 advisor 還沒運行，使用原版 dragon 的指令
                    ai_cmd = bridge.get('ai_to_dragon', {})
                dragon_status = bridge.get('dragon_to_ai', {})
                
                # AI 指令
                action = ai_cmd.get('command', 'WAIT')
                ai_direction = ai_cmd.get('direction', 'NEUTRAL')
                confidence = ai_cmd.get('confidence', 50)
                whale_reversal_price = ai_cmd.get('whale_reversal_price', 0)
                pred_time_str = ai_cmd.get('timestamp')
                
                # 市場狀態 (從 Bridge 讀取)
                whale_status_data = dragon_status.get('whale_status', {})
                whale_direction = whale_status_data.get('current_direction')
                whale_dominance = whale_status_data.get('dominance', 0)
                
                micro = dragon_status.get('market_microstructure', {})
                obi_value = micro.get('obi', 0)
                vpin_value = micro.get('vpin', 0)
                
                volatility = dragon_status.get('volatility', {})
                atr_pct = volatility.get('atr_pct', 0)
                is_dead_market = volatility.get('is_dead_market', False)
                
                current_price = self.latest_price
                
                print(f"   🐲2 Dragon V2: AI={action}, Whale={whale_direction}, Dom={whale_dominance:.2f}, ATR={atr_pct:.4f}%")
                
                # 檢查訊號時效
                is_fresh = False
                if pred_time_str:
                    pred_time = datetime.fromisoformat(pred_time_str)
                    age_seconds = (datetime.now() - pred_time).total_seconds()
                    is_fresh = age_seconds < 120
                    if not is_fresh:
                        return finalize_wrapper_dragon2({'action': 'HOLD', 'reason': f'M_DRAGON2 Signal stale ({age_seconds:.0f}s)'})
                
                # ═══════════════════════════════════════════════════════════
                # 🎯 三重確認機制 (AI + 鯨魚 + OBI) - 與 Wolf 完全相同
                # ═══════════════════════════════════════════════════════════
                
                ai_agrees = (ai_direction == "BULLISH" and action == "LONG") or (ai_direction == "BEARISH" and action == "SHORT")
                whale_agrees = whale_direction and whale_direction == ("LONG" if action == "LONG" else "SHORT")
                obi_agrees = (obi_value > 0.3 and action == "LONG") or (obi_value < -0.3 and action == "SHORT")
                
                confluence_count = sum([ai_agrees, whale_agrees, obi_agrees])
                
                print(f"   🐲2 Confluence: AI={ai_agrees}, Whale={whale_agrees}, OBI={obi_agrees} ({confluence_count}/3)")

                # ═══════════════════════════════════════════════════════════
                # 🔥 策略 1: 死水盤區間套利 (與 Wolf 相同)
                # ═══════════════════════════════════════════════════════════
                if is_dead_market or atr_pct < 0.05:
                    if whale_reversal_price > 0:
                        reversal_distance_pct = abs(current_price - whale_reversal_price) / current_price * 100
                        
                        if reversal_distance_pct < 0.3:
                            if ai_direction == "BEARISH" and current_price <= whale_reversal_price:
                                return finalize_wrapper_dragon2({
                                    'action': 'LONG',
                                    'reason': f'M_DRAGON2 Dead Market Grid Buy: ${current_price:.0f} at reversal ${whale_reversal_price:.0f}',
                                    'confidence': 0.85,
                                    'leverage': 75,
                                    'market_data': {
                                        'position_size_multiplier': 1.0,
                                        'trap_master_mode': 'dead_market_reversal',
                                        'quick_profit_target': 0.4
                                    }
                                })
                            elif ai_direction == "BULLISH" and current_price >= whale_reversal_price:
                                return finalize_wrapper_dragon2({
                                    'action': 'SHORT',
                                    'reason': f'M_DRAGON2 Dead Market Grid Sell: ${current_price:.0f} at reversal ${whale_reversal_price:.0f}',
                                    'confidence': 0.85,
                                    'leverage': 75,
                                    'market_data': {
                                        'position_size_multiplier': 1.0,
                                        'trap_master_mode': 'dead_market_reversal',
                                        'quick_profit_target': 0.4
                                    }
                                })
                    
                    if confluence_count >= 2 and confidence > 0.6:
                        print(f"   🚀 Dead Market Breakout Detected!")
                    else:
                        # 🔧 死水盤無突破跡象，必須 HOLD
                        print(f"   🐟 [M_DRAGON2] 死水盤無突破跡象，HOLD 等待")
                        return finalize_wrapper_dragon2({'action': 'HOLD', 'reason': f'M_DRAGON2 Dead Market: waiting for breakout'})
                
                # 🚨 ABSOLUTE RULE #1: 高集中度時必須跟隨鯨魚
                if whale_dominance >= 0.70 and whale_direction:
                    ai_action_direction = "LONG" if action == "LONG" else ("SHORT" if action == "SHORT" else None)
                    if ai_action_direction and ai_action_direction != whale_direction:
                        print(f"   🚨 [ABSOLUTE RULE #1] 鯨魚集中度 {whale_dominance:.0%} >= 70%!")
                        print(f"   🚨 AI說{action} 但鯨魚方向={whale_direction} → 強制跟隨鯨魚!")
                        action = whale_direction
                        print(f"   🐋 強制執行: {action} (跟隨鯨魚)")
                
                # ═══════════════════════════════════════════════════════════
                # ⚡ 策略 2: 反轉點埋伏 (與 Wolf 相同)
                # ═══════════════════════════════════════════════════════════
                if whale_reversal_price > 0:
                    reversal_distance_pct = abs(current_price - whale_reversal_price) / current_price * 100
                    
                    if 0.1 < reversal_distance_pct < 0.5:
                        if whale_dominance > 0.8:
                            ambush_action = "LONG" if whale_direction == "SHORT" else "SHORT"
                            
                            return finalize_wrapper_dragon2({
                                'action': ambush_action,
                                'reason': f'M_DRAGON2 Ambush: {reversal_distance_pct:.2f}% from reversal (Dom: {whale_dominance:.2f})',
                                'confidence': min(0.9, confidence / 100.0),
                                'leverage': 60,
                                'market_data': {
                                    'position_size_multiplier': 1.2,
                                    'trap_master_mode': 'reversal_ambush',
                                    'quick_profit_target': 1.0
                                }
                            })
                
                # ═══════════════════════════════════════════════════════════
                # 📊 策略 3: 標準 AI 信號 (與 Wolf 相同)
                # ═══════════════════════════════════════════════════════════
                if action in ['LONG', 'SHORT']:
                    if confluence_count == 3 and whale_dominance > 0.8:
                        leverage = 65
                        size_mult = 1.3
                        reason_suffix = "(Full confluence + High dom)"
                    else:
                        leverage = min(55, max(30, int(confidence * 0.8)))
                        size_mult = 1.0
                        reason_suffix = f"(AI Command Override)"
                    
                    return finalize_wrapper_dragon2({
                        'action': action,
                        'reason': f'M_DRAGON2 AI Signal: {action} {reason_suffix}',
                        'confidence': confidence / 100.0,
                        'leverage': leverage,
                        'market_data': {
                            'position_size_multiplier': size_mult,
                            'trap_master_mode': 'standard',
                            'quick_profit_target': 0.8
                        }
                    })
                
                elif action in ['ADD_LONG', 'ADD_SHORT']:
                    return finalize_wrapper_dragon2({
                        'action': action,
                        'reason': f'M_DRAGON2 Add Position: {action}',
                        'confidence': confidence / 100.0,
                        'leverage': 50,
                        'market_data': {'position_size_multiplier': 1.5}
                    })
                elif action == 'CUT_LOSS':
                    return finalize_wrapper_dragon2({
                        'action': 'HOLD',
                        'reason': f'M_DRAGON2 CUT_LOSS signal',
                        'confidence': 0.0,
                        'market_data': {'force_exit': True}
                    })
                else:
                    return finalize_wrapper_dragon2({'action': 'HOLD', 'reason': f'M_DRAGON2 {action}'})
                    
            except Exception as e:
                return finalize_wrapper_dragon2({'action': 'HOLD', 'reason': f'M_DRAGON2 Error: {str(e)}'})

        # 🦐🐦 M_SHRIMP & M_BIRD 策略 - 優化持倉時間版本
        # 🦐 Shrimp = 基於 Wolf (GPT-4) 的 Bridge
        # 🐦 Bird = 基於 Dragon (Kimi) 的 Bridge
        # 基於數據分析：2-3分鐘持倉勝率最高 (79-100%)
        if style == 'ai_shrimp':
            try:
                # 判斷是 Shrimp (GPT) 還是 Bird (Kimi)
                is_bird = mode.name == 'M_BIRD'
                
                # 🦐 Shrimp 使用 Wolf Bridge (GPT-4)
                # 🐦 Bird 使用 Dragon Bridge (Kimi)
                bridge_file = "ai_dragon_bridge.json" if is_bird else "ai_wolf_bridge.json"
                ai_key = "ai_to_dragon" if is_bird else "ai_to_wolf"
                status_key = "dragon_to_ai" if is_bird else "wolf_to_ai"
                
                # 不需要反轉，因為各自獨立的 AI
                def finalize_wrapper_shrimp(d):
                    return finalize(d)

                # 讀取 Bridge 數據
                if not os.path.exists(bridge_file):
                    return finalize_wrapper_shrimp({'action': 'HOLD', 'reason': f'{mode.name} Bridge not found'})
                
                with open(bridge_file, 'r') as f:
                    bridge = json.load(f)
                
                ai_cmd = bridge.get(ai_key, {})
                wolf_status = bridge.get(status_key, {})
                
                # AI 指令 (加入 None 安全檢查)
                action = ai_cmd.get('command') or 'WAIT'
                ai_direction = ai_cmd.get('direction') or 'NEUTRAL'
                confidence = ai_cmd.get('confidence')
                if confidence is None:
                    confidence = 50
                pred_time_str = ai_cmd.get('timestamp')
                
                # 市場狀態 (從 Bridge 讀取)
                whale_status_data = wolf_status.get('whale_status', {})
                whale_direction = whale_status_data.get('current_direction')
                whale_dominance = whale_status_data.get('dominance', 0)
                
                micro = wolf_status.get('market_microstructure', {})
                obi_value = micro.get('obi', 0)
                
                prefix = "🐦" if is_bird else "🦐"
                print(f"   {prefix} {mode.name}: AI={action}, Whale={whale_direction}, Dom={whale_dominance:.2f}, Conf={confidence}")
                
                # 🎯 關鍵差異 1: 更高的進場門檻 (confidence > 70%)
                if confidence < 70:
                    return finalize_wrapper_shrimp({'action': 'HOLD', 'reason': f'{mode.name} Low confidence: {confidence} < 70'})
                
                # 🔧 修正：如果 AI 命令是 HOLD/WAIT，但有明確方向，用方向來推斷
                if action in ['HOLD', 'WAIT', None, '']:
                    # 嘗試從 direction 推斷
                    if ai_direction == "BULLISH" and confidence >= 70:
                        action = "LONG"
                        print(f"   {prefix} 推斷: HOLD+BULLISH → LONG")
                    elif ai_direction == "BEARISH" and confidence >= 70:
                        action = "SHORT"
                        print(f"   {prefix} 推斷: HOLD+BEARISH → SHORT")
                    else:
                        return finalize_wrapper_shrimp({'action': 'HOLD', 'reason': f'{mode.name} No clear direction'})
                
                # 檢查訊號時效
                is_fresh = False
                if pred_time_str:
                    pred_time = datetime.fromisoformat(pred_time_str)
                    age_seconds = (datetime.now() - pred_time).total_seconds()
                    is_fresh = age_seconds < 120
                    if not is_fresh:
                        return finalize_wrapper_shrimp({'action': 'HOLD', 'reason': f'{mode.name} Signal stale ({age_seconds:.0f}s)'})
                
                # 🎯 關鍵差異 2: 三重確認 (AI + 鯨魚方向 + 鯨魚集中度)
                ai_agrees = (ai_direction == "BULLISH" and action == "LONG") or (ai_direction == "BEARISH" and action == "SHORT")
                whale_agrees = whale_direction and whale_direction == ("LONG" if action == "LONG" else "SHORT")
                dominance_high = whale_dominance > 0.7
                
                confluence_count = sum([ai_agrees, whale_agrees, dominance_high])
                
                print(f"   {prefix} Confluence: AI={ai_agrees}, Whale={whale_agrees}, Dom={dominance_high} ({confluence_count}/3)")
                
                # 需要至少 2/3 確認
                if confluence_count < 2:
                    return finalize_wrapper_shrimp({'action': 'HOLD', 'reason': f'{mode.name} Low confluence: {confluence_count}/3'})
                
                # ═══════════════════════════════════════════════════════════
                # 🔧 修復: 手續費計算問題
                # 50x 槓桿 × Taker 0.05% × 2 = 5% 手續費
                # 原本 TP=5% 扣掉手續費後淨盈利=0%！
                # 
                # 解決方案: 
                # 1. 降低槓桿到 20x (手續費 2%)
                # 2. 提高 TP 到 12% (淨盈利 10%)
                # 3. SL 維持 3% (實際虧損 5%)
                # 4. 盈虧比 = 10%/5% = 2:1，需要勝率 33%
                # ═══════════════════════════════════════════════════════════
                
                if action in ['LONG', 'SHORT']:
                    # ═══════════════════════════════════════════════════════════
                    # 🦐🐦 優化設定 (2025-11-26 第二版)
                    # 目標: 即使勝率只有 22%, 也能盈利
                    # 
                    # 計算 (V3 最佳化):
                    # - 25x 槓桿: 手續費 = 0.05% × 25 × 2 = 2.5%
                    # - TP 5%: 淨盈利 = 5% - 2.5% = 2.5%
                    # - SL 2%: 淨虧損 = 2% + 2.5% = 4.5%
                    # - 盈虧比 = 2.5% / 4.5% = 0.56
                    # - 需要勝率 = 1/(1+0.56) = 64%
                    # - 關鍵: 0.2% 價格變動達成率 = 88%!
                    # - 88% 勝率預期: 88×2.5 - 12×4.5 = +166% 🎉
                    # ═══════════════════════════════════════════════════════════
                    
                    shrimp_leverage = 25       # 🔧 25x 槓桿 (手續費 2.5%)
                    shrimp_tp = 5.0            # 🔧 5% 止盈 (淨盈利 2.5%), 需0.2%價格變動
                    shrimp_sl = 2.0            # 🔧 2% 止損 (淨虧損 4.5%)
                    
                    # 計算預期盈虧
                    fee_pct = 0.0005 * shrimp_leverage * 2 * 100  # 1.5%
                    net_tp = shrimp_tp - fee_pct  # 18.5%
                    net_sl = shrimp_sl + fee_pct  # 4.5%
                    rr = net_tp / net_sl  # 4.1
                    wr_needed = 1 / (1 + rr) * 100  # 20%
                    
                    print(f"   {prefix} 🔧 優化V2: Lev={shrimp_leverage}x, TP={shrimp_tp}% (淨{net_tp:.1f}%), SL={shrimp_sl}% (淨{net_sl:.1f}%), 盈虧比={rr:.1f}, 需勝率{wr_needed:.0f}%")
                    
                    return finalize_wrapper_shrimp({
                        'action': action,
                        'reason': f'{mode.name}: {action} (Conf={confidence}, {confluence_count}/3)',
                        'confidence': confidence / 100.0,
                        'leverage': shrimp_leverage,
                        'market_data': {
                            'position_size_multiplier': 1.0,
                            'trap_master_mode': 'shrimp_optimized_v3',
                            'min_holding_seconds': 60,      # 🔧 最小持倉 1 分鐘 (5% TP 更快達成)
                            'max_holding_seconds': 600,     # 🔧 最大持倉 10 分鐘 (0.2% 價格變動夠快)
                            'take_profit_pct': shrimp_tp,   # 5% ROI 止盈
                            'stop_loss_pct': shrimp_sl,     # 2% ROI 止損
                            'leverage': shrimp_leverage,
                            'disable_trailing_stop': True
                        }
                    })
                else:
                    return finalize_wrapper_shrimp({'action': 'HOLD', 'reason': f'{mode.name} {action}'})
                    
            except Exception as e:
                return finalize_wrapper_shrimp({'action': 'HOLD', 'reason': f'{mode.name} Error: {str(e)}'})

        # 🦁 M_LION 策略 - v2.0 Whale Strategy Detector Enhanced (GPT + v2.0)
        # 使用與 M🐺 相同的 GPT 模型，但有獨立的 Bridge 和 v2.0 策略檢測
        if style == 'ai_lion':
            try:
                bridge_file = "ai_lion_bridge.json"
                
                def finalize_wrapper_lion(d):
                    return finalize(d)

                # 讀取 Bridge 數據
                if not os.path.exists(bridge_file):
                    return finalize_wrapper_lion({'action': 'HOLD', 'reason': 'M_LION Bridge not found'})
                
                with open(bridge_file, 'r') as f:
                    bridge = json.load(f)
                
                ai_cmd = bridge.get('ai_to_wolf', {})
                lion_status = bridge.get('wolf_to_ai', {})
                v2_detection = bridge.get('v2_strategy_detection', {})
                
                # AI 指令
                action = ai_cmd.get('command', 'WAIT')
                ai_direction = ai_cmd.get('direction', 'NEUTRAL')
                confidence = ai_cmd.get('confidence', 50)
                v2_strategy = ai_cmd.get('v2_detected_strategy', 'UNKNOWN')
                v2_aligned = ai_cmd.get('v2_strategy_aligned', False)
                pred_time_str = ai_cmd.get('timestamp')
                
                # v2.0 策略檢測結果
                detected_strategy = v2_detection.get('detected_strategy', 'UNKNOWN')
                conflict_state = v2_detection.get('conflict_state', 'UNKNOWN')
                v2_predicted_action = v2_detection.get('predicted_action', 'HOLD')
                v2_confidence = v2_detection.get('prediction_confidence', 0)
                
                # 市場狀態 (從 Bridge 讀取)
                whale_status_data = lion_status.get('whale_status', {})
                whale_direction = whale_status_data.get('current_direction')
                whale_dominance = whale_status_data.get('dominance', 0)
                
                micro = lion_status.get('market_microstructure', {})
                obi_value = micro.get('obi', 0)
                vpin_value = micro.get('vpin', 0)
                
                current_price = self.latest_price
                
                print(f"   🦁 M_LION: AI={action}, v2.0={detected_strategy}, Aligned={v2_aligned}, Conf={confidence}, v2Conf={v2_confidence:.0%}")
                
                # 檢查訊號時效
                is_fresh = False
                if pred_time_str:
                    try:
                        pred_time = datetime.fromisoformat(pred_time_str)
                        age_seconds = (datetime.now() - pred_time).total_seconds()
                        is_fresh = age_seconds < 120
                        if not is_fresh:
                            return finalize_wrapper_lion({'action': 'HOLD', 'reason': f'M_LION Signal stale ({age_seconds:.0f}s)'})
                    except:
                        pass
                
                # 🦁 v2.0 策略增強邏輯
                # 如果 v2.0 檢測到危險策略，優先避險
                danger_strategies = ['BULL_TRAP', 'PUMP_DUMP', 'DUMP']
                safe_strategies = ['ACCUMULATION', 'BEAR_TRAP']
                
                # 🚨 危險策略警告
                if detected_strategy in danger_strategies:
                    if action == 'LONG':
                        print(f"   🦁 ⚠️ DANGER: v2.0 detected {detected_strategy} but AI wants LONG - BLOCKING!")
                        return finalize_wrapper_lion({'action': 'HOLD', 'reason': f'v2.0 {detected_strategy} blocks LONG'})
                
                # 🚨 反向陷阱警告
                if detected_strategy == 'BEAR_TRAP' and action == 'SHORT':
                    print(f"   🦁 ⚠️ TRAP: v2.0 detected BEAR_TRAP but AI wants SHORT - BLOCKING!")
                    return finalize_wrapper_lion({'action': 'HOLD', 'reason': f'v2.0 BEAR_TRAP blocks SHORT'})
                
                # 🎯 三重確認: AI + v2.0 + OBI
                ai_agrees = (ai_direction == "BULLISH" and action == "LONG") or (ai_direction == "BEARISH" and action == "SHORT")
                v2_agrees = v2_aligned and v2_confidence > 0.6
                obi_agrees = (obi_value > 0.3 and action == "LONG") or (obi_value < -0.3 and action == "SHORT")
                
                confluence_count = sum([ai_agrees, v2_agrees, obi_agrees])
                
                print(f"   🦁 Confluence: AI={ai_agrees}, v2.0={v2_agrees}, OBI={obi_agrees} ({confluence_count}/3)")
                
                # 🎯 高信心進場
                if confidence < 60:
                    return finalize_wrapper_lion({'action': 'HOLD', 'reason': f'M_LION Low confidence: {confidence} < 60'})
                
                # 需要至少 2/3 確認才進場
                if confluence_count < 2:
                    return finalize_wrapper_lion({'action': 'HOLD', 'reason': f'M_LION Low confluence: {confluence_count}/3'})
                
                # 執行交易
                if action == 'LONG':
                    lion_leverage = min(50, max(30, int(confidence * 0.6)))
                    return finalize_wrapper_lion({
                        'action': 'LONG',
                        'reason': f'🦁 Lion LONG: v2.0={detected_strategy}, AI={ai_direction}, Conf={confluence_count}/3',
                        'confidence': confidence / 100,
                        'leverage': lion_leverage,
                        'market_data': {
                            'position_size_multiplier': 1.0,
                            'lion_mode': True,
                            'v2_strategy': detected_strategy,
                            'v2_conflict_state': conflict_state
                        }
                    })
                elif action == 'SHORT':
                    lion_leverage = min(50, max(30, int(confidence * 0.6)))
                    return finalize_wrapper_lion({
                        'action': 'SHORT',
                        'reason': f'🦁 Lion SHORT: v2.0={detected_strategy}, AI={ai_direction}, Conf={confluence_count}/3',
                        'confidence': confidence / 100,
                        'leverage': lion_leverage,
                        'market_data': {
                            'position_size_multiplier': 1.0,
                            'lion_mode': True,
                            'v2_strategy': detected_strategy,
                            'v2_conflict_state': conflict_state
                        }
                    })
                else:
                    return finalize_wrapper_lion({'action': 'HOLD', 'reason': f'M_LION {action}'})
                    
            except Exception as e:
                return finalize_wrapper_lion({'action': 'HOLD', 'reason': f'M_LION Error: {str(e)}'})

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
                confidence = min((signal_score / signal_threshold), 1.0) if signal_threshold > 0 else 1.0
            elif obi < 0:
                action = 'SHORT'
                reason = f'Hybrid SHORT: FZ={funding_zscore:.2f}, Signal={signal_score:.2f}, OBI={obi:.4f}'
                confidence = min((signal_score / signal_threshold), 1.0) if signal_threshold > 0 else 1.0
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
    
    def _update_wolf_status_to_bridge(self, status: str, position: Optional[SimulatedOrder], snapshot: dict, is_dragon: bool = False):
        """更新 M🐺 或 M🐲 的狀態到 Bridge（回報給 AI）- 完整版"""
        try:
            bridge_file = "ai_dragon_bridge.json" if is_dragon else "ai_wolf_bridge.json"
            agent_key = "dragon_to_ai" if is_dragon else "wolf_to_ai"
            
            if not os.path.exists(bridge_file):
                return
            
            with open(bridge_file, 'r') as f:
                bridge = json.load(f)
            
            wolf_status = {
                "status": status,  # IDLE, OPENING, IN_POSITION, CLOSING
                "timestamp": datetime.now().isoformat()
            }
            
            # 🆕 Priority 0: 爆倉瀑布警報 (最重要！)
            cascade_signal = snapshot.get('cascade_signal', {})
            cascade_active = cascade_signal.get('active', False)
            cascade_direction = cascade_signal.get('direction', 'HOLD')
            cascade_strength = cascade_signal.get('strength', 0)
            
            wolf_status["liquidation_cascade"] = {
                "active": cascade_active,
                "direction": cascade_direction,
                "strength": round(cascade_strength, 1),
                "alert_level": self._get_cascade_alert_level(cascade_strength),
                "recommended_action": self._get_cascade_recommendation(cascade_direction, cascade_strength, position),
            }
            
            # 🆕 如果爆倉瀑布活躍，加入醒目警告
            if cascade_active and cascade_strength >= 40:
                wolf_status["URGENT_ALERT"] = {
                    "type": "LIQUIDATION_CASCADE",
                    "message": f"⚠️ 爆倉瀑布進行中！方向={cascade_direction}, 強度={cascade_strength:.0f}",
                    "action": self._get_cascade_recommendation(cascade_direction, cascade_strength, position),
                }
            
            # 🐳 Priority 1: 鯨魚行為追蹤
            whale_signal = self.large_trade_signal
            whale_tracker = self.whale_reversal_tracker.get(TradingMode.M_WHALE_WATCHER, {})
            wolf_status["whale_status"] = {
                "current_direction": whale_signal.get('direction'),
                "dominance": round(whale_signal.get('dominance_ratio', 0), 3),
                "flip_count_30min": whale_tracker.get('reversal_count', 0),
                "net_qty_btc": round(whale_signal.get('net_qty', 0), 2),
                "last_flip_time": whale_tracker.get('last_flip_time', None)
            }
            
            # 🆕 Priority 1.5: 鯨魚訊號品質分析器
            # 傳入更多市場數據進行特徵分析
            whale_effectiveness = self._update_whale_signal_effectiveness(
                direction=whale_signal.get('direction'),
                dominance=whale_signal.get('dominance_ratio', 0),
                net_qty=whale_signal.get('net_qty', 0),
                current_price=self.latest_price,
                obi=snapshot.get('obi', 0),
                vpin=snapshot.get('vpin', 0),
                whale_vwap=whale_signal.get('vwap', self.latest_price)
            )
            wolf_status["whale_signal_effectiveness"] = whale_effectiveness
            
            # 🔬 Priority 1: 市場微結構
            wolf_status["market_microstructure"] = {
                "obi": round(snapshot.get('obi', 0), 3),
                "vpin": round(snapshot.get('vpin', 0), 3),
                "spread_bps": round(snapshot.get('spread_bps', 0), 2),
                "funding_rate": round(snapshot.get('funding_rate', 0), 4),
                "depth_imbalance": round(snapshot.get('depth_imbalance', 0), 3)
            }
            
            # 🌊 Priority 1: 波動環境
            churn_info = self._detect_market_churn()
            wolf_status["volatility"] = {
                "atr_pct": round(churn_info.get('atr_pct', 0), 4),
                "regime": snapshot.get('regime', 'UNKNOWN'),
                "is_dead_market": churn_info.get('is_dead_market', False),
                "bb_width_pct": round(snapshot.get('bb_width_pct', 0), 4)
            }
            
            if position:
                _, unrealized_pnl_pct = position.update_unrealized_pnl(self.latest_price)
                holding_seconds = (datetime.now() - datetime.fromisoformat(position.entry_time)).total_seconds()
                
                wolf_status.update({
                    "position": {
                        "direction": position.direction,
                        "entry_price": position.actual_entry_price,
                        "leverage": position.leverage,
                        "size": position.size
                    },
                    "entry_price": position.actual_entry_price,
                    "current_pnl_usdt": round(unrealized_pnl_pct * position.position_value * position.leverage / 100, 2),
                    "current_pnl_pct": round(unrealized_pnl_pct, 2),
                    "holding_seconds": int(holding_seconds),
                    "market_reaction": {
                        "price_moved_as_expected": self._check_price_movement(position),
                        "divergence_pct": round(self._calculate_divergence(position), 3),
                        "vpin_spike": snapshot.get('vpin', 0) > 0.8,
                        "whale_flipped": self._detect_whale_flip(position),
                        # 🆕 爆倉瀑布與持倉方向的衝突檢測
                        "cascade_conflict": self._check_cascade_conflict(position, cascade_direction, cascade_strength)
                    }
                })
            else:
                wolf_status.update({
                    "position": None,
                    "entry_price": 0,
                    "current_pnl_usdt": 0,
                    "current_pnl_pct": 0,
                    "holding_seconds": 0
                })
            
            # 🎯 Priority 3: 風險指標
            wolf_status["risk_indicators"] = {
                "liquidation_pressure": self._calculate_liquidation_pressure(snapshot),
                "orderbook_toxicity": round(snapshot.get('vpin', 0), 3),
                "whale_trap_probability": self._estimate_whale_trap_probability(),
                # 🆕 爆倉瀑布風險
                "cascade_risk": "HIGH" if cascade_active and cascade_strength >= 60 else "MEDIUM" if cascade_active else "LOW"
            }
            
            # 🆕 Priority 4: 方向探針損益 (Mup/Mdown) - 最直接的市場方向指標！
            wolf_status["direction_probes"] = self._get_direction_probe_pnl()
            
            bridge[agent_key] = wolf_status
            bridge['last_updated'] = datetime.now().isoformat()
            
            # 🔧 清理過期事件 (減少 token 消耗)
            # - maker_timeout_event: 超過 30 分鐘就清除
            # - rollback_events: 只保留最近 3 筆
            if 'maker_timeout_event' in bridge:
                try:
                    event_time = datetime.fromisoformat(bridge['maker_timeout_event'].get('timestamp', ''))
                    if (datetime.now() - event_time).total_seconds() > 1800:  # 30 分鐘
                        del bridge['maker_timeout_event']
                except:
                    pass
            
            if 'rollback_events' in bridge:
                bridge['rollback_events'] = bridge['rollback_events'][-3:]
            
            with open(bridge_file, 'w') as f:
                json.dump(bridge, f, indent=2)
                
        except Exception as e:
            print(f"   ⚠️ Failed to update bridge: {e}")
    
    def _update_whale_signal_effectiveness(
        self, 
        direction: Optional[str], 
        dominance: float, 
        net_qty: float,
        current_price: float,
        obi: float = 0,
        vpin: float = 0,
        whale_vwap: float = 0
    ) -> dict:
        """
        🆕 鯨魚訊號品質分析器 (Whale Signal Quality Analyzer)
        
        不只看歷史，更重要的是分析當前訊號的「特徵」來判斷是否可信：
        
        🎯 可信訊號的特徵 (真實大戶意圖):
        1. 連續性：多筆大單朝同一方向，而非單筆
        2. 價格跟隨：大單後價格有明顯反應
        3. 訂單簿變化：OBI 朝同方向移動
        4. 成交量放大：伴隨高成交量
        5. 持續時間：訊號維持一段時間（非瞬間）
        
        ⚠️ 假訊號的特徵 (試探單/誘騙單):
        1. 孤立大單：只有一筆，無後續
        2. 價格無反應：大單後價格平穩
        3. 快速反轉：訊號很快消失
        4. OBI 矛盾：大單方向與 OBI 不一致
        5. 低 VPIN：沒有真實的資訊不對稱
        
        Returns:
            完整的訊號品質分析報告
        """
        now = time.time()
        tracker = self.whale_signal_tracker
        config = tracker['config']
        stats = tracker['effectiveness_stats']
        
        # 使用傳入的市場微結構數據（不再重新讀取）
        # obi, vpin, whale_vwap 都是函數參數
        
        result = {
            "signal_quality": {},           # 🆕 訊號品質特徵分析
            "current_signal": None,
            "is_signal_effective": False,
            "price_impact_pct": 0,
            "effectiveness_rate": 0,
            "signal_strength": "NONE",
            "recommendation": "WAIT",
            "quality_score": 0,             # 🆕 綜合品質分數 0-100
            "quality_factors": [],          # 🆕 品質因素列表
            "warning_factors": [],          # 🆕 警告因素列表
            "avg_response_time": 0,
            "recent_signals_summary": {
                "total": len(tracker['signal_history']),
                "effective": 0,
                "ineffective": 0
            }
        }
        
        # ═══════════════════════════════════════════════════════════
        # 1. 分析當前訊號的「品質特徵」
        # ═══════════════════════════════════════════════════════════
        quality_score = 0
        quality_factors = []
        warning_factors = []
        
        if direction in ['LONG', 'SHORT'] and dominance >= 0.4:
            
            # 特徵 1: 連續性分析 - 檢查最近大單是否連續同方向
            recent_trades = list(self.recent_large_trades)
            if recent_trades:
                same_direction_count = sum(1 for t in recent_trades[-10:] if t.get('direction') == direction)
                total_recent = min(10, len(recent_trades))
                continuity_ratio = same_direction_count / total_recent if total_recent > 0 else 0
                
                if continuity_ratio >= 0.8:
                    quality_score += 25
                    quality_factors.append(f"連續性高 ({same_direction_count}/{total_recent} 同向)")
                elif continuity_ratio >= 0.6:
                    quality_score += 15
                    quality_factors.append(f"連續性中 ({same_direction_count}/{total_recent} 同向)")
                elif continuity_ratio < 0.5:
                    warning_factors.append(f"連續性低 (混亂訊號 {same_direction_count}/{total_recent})")
            
            # 特徵 2: OBI 一致性 - 訂單簿是否支持這個方向
            obi_supports = (direction == 'LONG' and obi > 0.2) or (direction == 'SHORT' and obi < -0.2)
            obi_conflicts = (direction == 'LONG' and obi < -0.3) or (direction == 'SHORT' and obi > 0.3)
            
            if obi_supports:
                quality_score += 20
                quality_factors.append(f"OBI 支持 ({obi:.2f})")
            elif obi_conflicts:
                quality_score -= 10
                warning_factors.append(f"⚠️ OBI 矛盾 ({obi:.2f} vs {direction})")
            
            # 特徵 3: VPIN 資訊不對稱 - 高 VPIN 表示有資訊優勢者在場
            if vpin >= 0.6:
                quality_score += 15
                quality_factors.append(f"VPIN 高 ({vpin:.2f}) - 資訊不對稱")
            elif vpin < 0.3:
                warning_factors.append(f"VPIN 低 ({vpin:.2f}) - 可能是噪音")
            
            # 特徵 4: 淨量規模 - 越大越有意義
            if abs(net_qty) >= 20:
                quality_score += 20
                quality_factors.append(f"大規模 ({abs(net_qty):.1f} BTC)")
            elif abs(net_qty) >= 10:
                quality_score += 15
                quality_factors.append(f"中規模 ({abs(net_qty):.1f} BTC)")
            elif abs(net_qty) >= 5:
                quality_score += 10
                quality_factors.append(f"小規模 ({abs(net_qty):.1f} BTC)")
            else:
                warning_factors.append(f"量太小 ({abs(net_qty):.1f} BTC)")
            
            # 特徵 5: 支配度穩定性 - 高支配度更可信
            if dominance >= 0.85:
                quality_score += 20
                quality_factors.append(f"高支配度 ({dominance:.0%})")
            elif dominance >= 0.7:
                quality_score += 15
                quality_factors.append(f"中支配度 ({dominance:.0%})")
            elif dominance >= 0.6:
                quality_score += 10
            else:
                warning_factors.append(f"低支配度 ({dominance:.0%})")
            
            # 特徵 6: 大單頻率 - 檢查是否有持續的大單流入
            recent_30s = [t for t in recent_trades if now - t.get('time', 0) < 30]
            trade_frequency = len(recent_30s)
            
            if trade_frequency >= 5:
                quality_score += 15
                quality_factors.append(f"高頻大單 ({trade_frequency}筆/30s)")
            elif trade_frequency >= 3:
                quality_score += 10
                quality_factors.append(f"中頻大單 ({trade_frequency}筆/30s)")
            elif trade_frequency <= 1:
                warning_factors.append(f"孤立大單 ({trade_frequency}筆/30s)")
            
            # 特徵 7: 價格位置分析 - 大單是在追高還是在低位吸貨
            # 使用傳入的 whale_vwap 參數
            effective_vwap = whale_vwap if whale_vwap > 0 else current_price
            price_vs_vwap_pct = (current_price - effective_vwap) / effective_vwap * 100 if effective_vwap > 0 else 0
            
            if direction == 'LONG':
                # 做多時，如果當前價格低於鯨魚成本，更可信（鯨魚還沒出場）
                if price_vs_vwap_pct < -0.1:
                    quality_score += 10
                    quality_factors.append(f"價格低於鯨魚成本 ({price_vs_vwap_pct:.2f}%)")
                elif price_vs_vwap_pct > 0.3:
                    warning_factors.append(f"價格已高於鯨魚成本 ({price_vs_vwap_pct:.2f}%)")
            elif direction == 'SHORT':
                # 做空時，如果當前價格高於鯨魚成本，更可信
                if price_vs_vwap_pct > 0.1:
                    quality_score += 10
                    quality_factors.append(f"價格高於鯨魚成本 ({price_vs_vwap_pct:.2f}%)")
                elif price_vs_vwap_pct < -0.3:
                    warning_factors.append(f"價格已低於鯨魚成本 ({price_vs_vwap_pct:.2f}%)")
        
        # 限制分數範圍
        quality_score = max(0, min(100, quality_score))
        
        result['quality_score'] = quality_score
        result['quality_factors'] = quality_factors
        result['warning_factors'] = warning_factors
        result['signal_quality'] = {
            'score': quality_score,
            'grade': 'A' if quality_score >= 80 else 'B' if quality_score >= 60 else 'C' if quality_score >= 40 else 'D',
            'obi': round(obi, 3),
            'vpin': round(vpin, 3),
            'factors_positive': len(quality_factors),
            'factors_warning': len(warning_factors)
        }
        
        # ═══════════════════════════════════════════════════════════
        # 2. 更新當前訊號追蹤（保留原有邏輯）
        # ═══════════════════════════════════════════════════════════
        current_signal = tracker['current_signal']
        
        if current_signal:
            elapsed = now - current_signal['start_time']
            
            if current_signal['start_price'] > 0:
                price_change_pct = (current_price - current_signal['start_price']) / current_signal['start_price'] * 100
                expected_direction = current_signal['direction']
                
                if expected_direction == 'LONG':
                    impact_in_expected_direction = price_change_pct
                elif expected_direction == 'SHORT':
                    impact_in_expected_direction = -price_change_pct
                else:
                    impact_in_expected_direction = 0
                
                current_signal['price_impact_pct'] = round(price_change_pct, 4)
                current_signal['elapsed_seconds'] = round(elapsed, 1)
                current_signal['impact_in_direction'] = round(impact_in_expected_direction, 4)
                
                is_effective = impact_in_expected_direction >= config['min_impact_pct']
                current_signal['is_effective'] = is_effective
                
                if impact_in_expected_direction > current_signal.get('max_impact', 0):
                    current_signal['max_impact'] = impact_in_expected_direction
                    current_signal['time_to_max'] = elapsed
                
                result['current_signal'] = current_signal
                result['is_signal_effective'] = is_effective
                result['price_impact_pct'] = round(impact_in_expected_direction, 4)
            
            # 訊號過期
            if elapsed >= config['max_wait_seconds']:
                current_signal['final_result'] = 'EFFECTIVE' if current_signal.get('is_effective') else 'INEFFECTIVE'
                current_signal['end_time'] = now
                current_signal['quality_score'] = quality_score  # 記錄品質分數
                
                stats['total_signals'] += 1
                if current_signal.get('is_effective'):
                    stats['effective_signals'] += 1
                else:
                    stats['ineffective_signals'] += 1
                
                if stats['total_signals'] > 0:
                    old_avg = stats['avg_price_impact_pct']
                    stats['avg_price_impact_pct'] = (
                        old_avg * (stats['total_signals'] - 1) + current_signal.get('max_impact', 0)
                    ) / stats['total_signals']
                    
                    old_time = stats['avg_response_time_sec']
                    response_time = current_signal.get('time_to_max', config['max_wait_seconds'])
                    stats['avg_response_time_sec'] = (
                        old_time * (stats['total_signals'] - 1) + response_time
                    ) / stats['total_signals']
                
                tracker['signal_history'].append(current_signal)
                tracker['current_signal'] = None
        
        # ═══════════════════════════════════════════════════════════
        # 3. 開始追蹤新訊號
        # ═══════════════════════════════════════════════════════════
        MIN_DOMINANCE_TO_TRACK = 0.6
        MIN_NET_QTY_TO_TRACK = 5.0
        
        if (tracker['current_signal'] is None and 
            direction in ['LONG', 'SHORT'] and
            dominance >= MIN_DOMINANCE_TO_TRACK and
            abs(net_qty) >= MIN_NET_QTY_TO_TRACK):
            
            new_signal = {
                'direction': direction,
                'dominance': round(dominance, 3),
                'net_qty_btc': round(net_qty, 2),
                'start_price': current_price,
                'start_time': now,
                'price_impact_pct': 0,
                'elapsed_seconds': 0,
                'impact_in_direction': 0,
                'is_effective': False,
                'max_impact': 0,
                'time_to_max': 0,
                'initial_quality_score': quality_score  # 記錄初始品質
            }
            tracker['current_signal'] = new_signal
            result['current_signal'] = new_signal
            
            # 保存訊號發出時的市場狀態
            self.whale_signal_quality_tracker['price_at_signal'] = current_price
            self.whale_signal_quality_tracker['orderbook_at_signal'] = {
                'obi': obi, 'vpin': vpin
            }
        
        # ═══════════════════════════════════════════════════════════
        # 4. 計算歷史統計
        # ═══════════════════════════════════════════════════════════
        if stats['total_signals'] > 0:
            effectiveness_rate = stats['effective_signals'] / stats['total_signals'] * 100
            result['effectiveness_rate'] = round(effectiveness_rate, 1)
            result['avg_response_time'] = round(stats['avg_response_time_sec'], 1)
        
        recent_signals = list(tracker['signal_history'])[-10:]
        effective_count = sum(1 for s in recent_signals if s.get('final_result') == 'EFFECTIVE')
        result['recent_signals_summary']['effective'] = effective_count
        result['recent_signals_summary']['ineffective'] = len(recent_signals) - effective_count
        
        # ═══════════════════════════════════════════════════════════
        # 5. 生成最終建議（結合品質分數 + 歷史有效率）
        # ═══════════════════════════════════════════════════════════
        if dominance >= 0.8 and abs(net_qty) >= 10:
            result['signal_strength'] = "STRONG"
        elif dominance >= 0.6 and abs(net_qty) >= 5:
            result['signal_strength'] = "MEDIUM"
        elif dominance >= 0.4:
            result['signal_strength'] = "WEAK"
        else:
            result['signal_strength'] = "NONE"
        
        # 🆕 基於品質分數的建議（這比歷史更重要！）
        if result['signal_strength'] == "NONE":
            result['recommendation'] = "NO_SIGNAL"
        elif quality_score >= 70:
            result['recommendation'] = "TRUST"      # 品質高 → 信任
        elif quality_score >= 50:
            result['recommendation'] = "CAUTIOUS"   # 品質中 → 謹慎
        elif quality_score >= 30:
            result['recommendation'] = "WAIT"       # 品質低 → 等待更多確認
        else:
            result['recommendation'] = "IGNORE"     # 品質很差 → 忽略
        
        return result
    
    def _get_cascade_alert_level(self, strength: float) -> str:
        """獲取爆倉瀑布警報等級"""
        if strength >= 80:
            return "EXTREME"  # 極端 - 必須立即處理
        elif strength >= 60:
            return "HIGH"     # 高 - 強烈建議調整
        elif strength >= 40:
            return "MEDIUM"   # 中 - 需要注意
        elif strength >= 20:
            return "LOW"      # 低 - 輕微影響
        return "NONE"
    
    def _get_cascade_recommendation(self, direction: str, strength: float, position: Optional[SimulatedOrder]) -> str:
        """根據爆倉瀑布生成建議操作"""
        if strength < 40:
            return "CONTINUE"  # 繼續原策略
        
        if not position:
            # 沒有持倉
            if strength >= 60:
                if direction == "LONG":
                    return "CONSIDER_LONG"  # 空頭被爆，考慮做多
                elif direction == "SHORT":
                    return "CONSIDER_SHORT"  # 多頭被爆，考慮做空
            return "WAIT_AND_OBSERVE"  # 等待觀察
        
        # 有持倉
        pos_direction = position.direction
        
        if strength >= 70:
            # 強烈爆倉瀑布
            if (direction == "LONG" and pos_direction == "SHORT") or \
               (direction == "SHORT" and pos_direction == "LONG"):
                return "URGENT_EXIT"  # 持倉方向與瀑布方向相反，緊急平倉！
            else:
                return "HOLD_WITH_TRAILING"  # 順勢，加入追蹤止盈
        elif strength >= 50:
            if (direction == "LONG" and pos_direction == "SHORT") or \
               (direction == "SHORT" and pos_direction == "LONG"):
                return "TIGHTEN_STOP"  # 收緊止損
            else:
                return "HOLD"  # 順勢，繼續持有
        
        return "MONITOR"  # 持續監控
    
    def _check_cascade_conflict(self, position: SimulatedOrder, cascade_direction: str, cascade_strength: float) -> dict:
        """檢查持倉與爆倉瀑布是否衝突"""
        if cascade_strength < 40:
            return {"conflict": False, "severity": "NONE", "message": "無衝突"}
        
        pos_direction = position.direction
        
        # 多頭被爆 (cascade=SHORT) 但我們做多 → 衝突
        # 空頭被爆 (cascade=LONG) 但我們做空 → 衝突
        is_conflict = (
            (cascade_direction == "SHORT" and pos_direction == "LONG") or
            (cascade_direction == "LONG" and pos_direction == "SHORT")
        )
        
        if is_conflict:
            severity = "CRITICAL" if cascade_strength >= 70 else "HIGH" if cascade_strength >= 50 else "MEDIUM"
            return {
                "conflict": True,
                "severity": severity,
                "message": f"⚠️ 持倉 {pos_direction} 與爆倉瀑布 {cascade_direction} 衝突！強度={cascade_strength:.0f}"
            }
        
        return {"conflict": False, "severity": "NONE", "message": "方向一致"}

    def _get_direction_probe_pnl(self) -> dict:
        """
        🆕 獲取方向探針 (Mup/Mdown) 的損益
        
        這是最直接的市場方向指標！
        - Mup 賺錢 → 市場在漲
        - Mdown 賺錢 → 市場在跌
        - 差值大 → 趨勢明確
        - 持倉時間 > 60 秒才有參考價值！
        
        Returns:
            {
                "mup_pnl_usdt": float,       # Mup 損益
                "mdown_pnl_usdt": float,     # Mdown 損益
                "market_direction": str,     # BULLISH / BEARISH / NEUTRAL
                "direction_strength": float, # 方向強度 0-100
                "signal": str               # AI 建議
            }
        """
        MIN_HOLDING_SECONDS = 60  # 至少持倉 60 秒才有參考價值
        
        result = {
            "mup_pnl_usdt": 0.0,
            "mdown_pnl_usdt": 0.0,
            "mup_pnl_pct": 0.0,
            "mdown_pnl_pct": 0.0,
            "mup_holding_seconds": 0,
            "mdown_holding_seconds": 0,
            "market_direction": "NEUTRAL",
            "direction_strength": 0.0,
            "signal": "HOLD",
            "data_reliability": "LOW",  # LOW / MEDIUM / HIGH
            "description": ""
        }
        
        try:
            now = datetime.now()
            mup_valid = False
            mdown_valid = False
            
            # 獲取 Mup 持倉損益
            mup_orders = self.orders.get(TradingMode.MUP_DIRECTIONAL_LONG, [])
            mup_active = [o for o in mup_orders if not o.is_blocked and o.exit_time is None]
            if mup_active:
                mup_pos = mup_active[-1]
                mup_holding = (now - mup_pos.entry_time).total_seconds()
                result["mup_holding_seconds"] = int(mup_holding)
                _, mup_pnl_pct = mup_pos.update_unrealized_pnl(self.latest_price)
                result["mup_pnl_pct"] = round(mup_pnl_pct, 2)
                result["mup_pnl_usdt"] = round(mup_pnl_pct * mup_pos.position_value * mup_pos.leverage / 100, 2)
                mup_valid = mup_holding >= MIN_HOLDING_SECONDS
            
            # 獲取 Mdown 持倉損益
            mdown_orders = self.orders.get(TradingMode.MDOWN_DIRECTIONAL_SHORT, [])
            mdown_active = [o for o in mdown_orders if not o.is_blocked and o.exit_time is None]
            if mdown_active:
                mdown_pos = mdown_active[-1]
                mdown_holding = (now - mdown_pos.entry_time).total_seconds()
                result["mdown_holding_seconds"] = int(mdown_holding)
                _, mdown_pnl_pct = mdown_pos.update_unrealized_pnl(self.latest_price)
                result["mdown_pnl_pct"] = round(mdown_pnl_pct, 2)
                result["mdown_pnl_usdt"] = round(mdown_pnl_pct * mdown_pos.position_value * mdown_pos.leverage / 100, 2)
                mdown_valid = mdown_holding >= MIN_HOLDING_SECONDS
            
            # 設置數據可靠性
            if mup_valid and mdown_valid:
                result["data_reliability"] = "HIGH"
            elif mup_valid or mdown_valid:
                result["data_reliability"] = "MEDIUM"
            else:
                result["data_reliability"] = "LOW"
                result["description"] = f"⚠️ 數據不可靠！Mup持倉{result['mup_holding_seconds']}秒, Mdown持倉{result['mdown_holding_seconds']}秒 (需>{MIN_HOLDING_SECONDS}秒)"
                return result  # 數據不可靠，直接返回 NEUTRAL
            
            # 計算方向強度（只用有效的數據）
            mup_pnl = result["mup_pnl_usdt"] if mup_valid else 0
            mdown_pnl = result["mdown_pnl_usdt"] if mdown_valid else 0
            diff = mup_pnl - mdown_pnl  # 正數 = 多頭優勢，負數 = 空頭優勢
            
            # 方向強度 = |差值| / 2 (假設每邊最大 10 USDT)
            result["direction_strength"] = min(abs(diff) * 5, 100)  # 2 USDT 差異 = 10% 強度
            
            # 持倉時間加成（持倉越久，數據越可靠）
            avg_holding = (result["mup_holding_seconds"] + result["mdown_holding_seconds"]) / 2
            time_bonus = min(avg_holding / 300, 1.0)  # 300 秒 = 滿分
            result["direction_strength"] = min(result["direction_strength"] * (0.5 + 0.5 * time_bonus), 100)
            
            # 判斷市場方向
            if diff > 1.0:  # Mup 賺超過 Mdown 1 USDT 以上
                result["market_direction"] = "BULLISH"
                if diff > 3.0:
                    result["signal"] = "STRONG_LONG"
                    result["description"] = f"🟢 強烈做多信號！Mup +{mup_pnl:.2f} vs Mdown {mdown_pnl:.2f} (持倉{int(avg_holding)}秒)"
                else:
                    result["signal"] = "LONG"
                    result["description"] = f"🟢 做多優勢。Mup +{mup_pnl:.2f} vs Mdown {mdown_pnl:.2f} (持倉{int(avg_holding)}秒)"
            elif diff < -1.0:  # Mdown 賺超過 Mup 1 USDT 以上
                result["market_direction"] = "BEARISH"
                if diff < -3.0:
                    result["signal"] = "STRONG_SHORT"
                    result["description"] = f"🔴 強烈做空信號！Mdown +{mdown_pnl:.2f} vs Mup {mup_pnl:.2f} (持倉{int(avg_holding)}秒)"
                else:
                    result["signal"] = "SHORT"
                    result["description"] = f"🔴 做空優勢。Mdown +{mdown_pnl:.2f} vs Mup {mup_pnl:.2f} (持倉{int(avg_holding)}秒)"
            else:
                result["market_direction"] = "NEUTRAL"
                result["signal"] = "HOLD"
                result["description"] = f"⚪ 方向不明。Mup {mup_pnl:.2f} vs Mdown {mdown_pnl:.2f} (持倉{int(avg_holding)}秒)"
                
        except Exception as e:
            result["description"] = f"Error: {e}"
        
        return result

    def _notify_ai_maker_timeout(self, mode: TradingMode, order: SimulatedOrder):
        """
        🆕 Maker 掛單超時時通知 AI 重新評估策略
        
        寫入 Bridge 讓 AI 知道：
        1. 掛單失敗的原因（價格沒有觸及）
        2. 當前市場狀態
        3. 建議 AI 重新評估是否還要進場
        """
        if mode not in {TradingMode.M_AI_WHALE_HUNTER, TradingMode.M_DRAGON}:
            return
        
        try:
            is_dragon = mode == TradingMode.M_DRAGON
            bridge_file = "ai_dragon_bridge.json" if is_dragon else "ai_wolf_bridge.json"
            agent_key = "maker_timeout_event"
            
            if not os.path.exists(bridge_file):
                return
            
            with open(bridge_file, 'r') as f:
                bridge = json.load(f)
            
            # 計算價格偏離
            price_diff_pct = (self.latest_price - order.maker_limit_price) / order.maker_limit_price * 100
            
            # 寫入超時事件
            bridge[agent_key] = {
                "event_type": "MAKER_TIMEOUT",
                "timestamp": datetime.now().isoformat(),
                "order_details": {
                    "direction": order.direction,
                    "maker_price": order.maker_limit_price,
                    "current_price": self.latest_price,
                    "price_diff_pct": round(price_diff_pct, 3),
                    "timeout_seconds": order.maker_timeout_seconds,
                    "leverage": order.leverage,
                },
                "market_context": {
                    "price_moved_away": price_diff_pct > 0 if order.direction == "LONG" else price_diff_pct < 0,
                    "interpretation": self._interpret_maker_timeout(order, price_diff_pct),
                },
                "ai_instruction": {
                    "action_required": "RE_EVALUATE",
                    "message": f"Maker 掛單未成交，價格偏離 {price_diff_pct:+.2f}%。請重新評估是否仍要進場。",
                    "suggestions": [
                        "WAIT" if abs(price_diff_pct) > 0.1 else "RETRY_WITH_BETTER_PRICE",
                        "REASSESS_DIRECTION" if abs(price_diff_pct) > 0.2 else None,
                    ]
                }
            }
            
            bridge['last_updated'] = datetime.now().isoformat()
            
            with open(bridge_file, 'w') as f:
                json.dump(bridge, f, indent=2)
                
        except Exception as e:
            print(f"   ⚠️ Failed to notify AI of maker timeout: {e}")
    
    def _interpret_maker_timeout(self, order: SimulatedOrder, price_diff_pct: float) -> str:
        """解讀 Maker 超時的原因"""
        if order.direction == "LONG":
            if price_diff_pct > 0.1:
                return "價格上漲遠離買單，市場偏多"
            elif price_diff_pct < -0.1:
                return "價格下跌但未觸及掛單價，可能還會繼續下跌"
            else:
                return "價格橫盤，流動性不足"
        else:  # SHORT
            if price_diff_pct < -0.1:
                return "價格下跌遠離賣單，市場偏空"
            elif price_diff_pct > 0.1:
                return "價格上漲但未觸及掛單價，可能還會繼續上漲"
            else:
                return "價格橫盤，流動性不足"

    def _trigger_ai_loss_review(
        self, 
        mode: TradingMode, 
        position: SimulatedOrder, 
        snapshot: dict,
        consecutive_losses: int
    ):
        """
        🆕 v10.7 AI 智能復盤機制
        
        虧損後不只是休息，而是調用 LLM 分析虧損原因並即時調整參數。
        
        分析維度:
        1. 市場結構: VPIN 高 → 被做市商獵殺？
        2. 訊號品質: 鯨魚集中度高卻虧損 → 主力出貨誘騙？
        3. 波動環境: ATR 極低 → 死魚盤磨損？
        4. 時機問題: 進場時價格已大幅移動 → 追單？
        """
        if mode not in {TradingMode.M_AI_WHALE_HUNTER, TradingMode.M_DRAGON}:
            return
        
        try:
            is_dragon = mode == TradingMode.M_DRAGON
            bridge_file = "ai_dragon_bridge.json" if is_dragon else "ai_wolf_bridge.json"
            
            if not os.path.exists(bridge_file):
                return
            
            with open(bridge_file, 'r') as f:
                bridge = json.load(f)
            
            # 收集進場時的市場數據
            entry_data = position.market_data or {}
            
            # 計算虧損細節
            loss_pct = abs(position.roi)
            holding_seconds = position.holding_seconds
            
            # 分析虧損原因
            loss_analysis = {
                "event_type": "LOSS_REVIEW_REQUEST",
                "timestamp": datetime.now().isoformat(),
                "urgency": "HIGH" if consecutive_losses >= 3 or loss_pct > 3.0 else "MEDIUM",
                
                # 交易數據
                "trade_details": {
                    "direction": position.direction,
                    "entry_price": position.actual_entry_price,
                    "exit_price": position.exit_price,
                    "loss_pct": round(loss_pct, 2),
                    "loss_usdt": round(abs(position.pnl_usdt), 2),
                    "leverage": position.leverage,
                    "holding_seconds": int(holding_seconds),
                    "exit_reason": position.exit_reason,
                    "consecutive_losses": consecutive_losses,
                },
                
                # 進場時的市場狀態
                "entry_context": {
                    "obi_at_entry": round(entry_data.get('obi', 0), 3),
                    "vpin_at_entry": round(entry_data.get('vpin', 0), 3),
                    "whale_direction": entry_data.get('whale_direction'),
                    "whale_dominance": round(entry_data.get('whale_dominance', 0), 3),
                    "atr_pct": round(entry_data.get('atr_pct', 0), 4),
                    "price_momentum_1m": round(entry_data.get('momentum_pct', 0), 3),
                },
                
                # 出場時的市場狀態
                "exit_context": {
                    "obi_at_exit": round(snapshot.get('obi', 0), 3),
                    "vpin_at_exit": round(snapshot.get('vpin', 0), 3),
                    "current_whale_direction": self.large_trade_signal.get('direction'),
                    "whale_flipped": self._detect_whale_flip(position) if position else False,
                },
                
                # 系統推測的虧損原因
                "preliminary_diagnosis": self._diagnose_loss_cause(position, entry_data, snapshot),
                
                # 要求 AI 回覆的內容
                "ai_instruction": {
                    "action_required": "ANALYZE_AND_ADJUST",
                    "message": f"連續 {consecutive_losses} 次虧損，請分析原因並調整參數。",
                    "questions": [
                        "這次虧損是策略失效(Strategy Failure)還是市場噪音(Market Noise)？",
                        "是否需要提高信心門檻(confidence_threshold)？",
                        "是否需要調整止損比例(stop_loss_pct)？",
                        "是否需要切換策略模式(例如從趨勢切換到區間)？",
                    ],
                    "response_format": {
                        "diagnosis": "STRATEGY_FAILURE | MARKET_NOISE | TIMING_ERROR | WHALE_TRAP",
                        "recommended_adjustments": {
                            "confidence_threshold_delta": "0 | +5 | +10 | +15",
                            "stop_loss_pct_delta": "0 | +0.5 | +1.0",
                            "leverage_multiplier": "1.0 | 0.8 | 0.6",
                            "cooldown_minutes": "0 | 5 | 15 | 30",
                            "strategy_switch": "NONE | RANGE_MODE | ULTRA_SAFE",
                        },
                        "reasoning": "簡短說明調整理由"
                    }
                }
            }
            
            # 寫入 Bridge
            bridge['loss_review'] = loss_analysis
            bridge['last_updated'] = datetime.now().isoformat()
            
            with open(bridge_file, 'w') as f:
                json.dump(bridge, f, indent=2, ensure_ascii=False)
            
            print(f"   📊 [{self.mode_info[mode]['emoji']}] 虧損分析已發送至 AI")
            print(f"      診斷: {loss_analysis['preliminary_diagnosis']['primary_cause']}")
                
        except Exception as e:
            print(f"   ⚠️ Failed to trigger AI loss review: {e}")
    
    def _diagnose_loss_cause(self, position: SimulatedOrder, entry_data: dict, snapshot: dict) -> dict:
        """
        系統自動診斷虧損原因（作為 AI 分析的參考）
        """
        diagnosis = {
            "primary_cause": "UNKNOWN",
            "confidence": 0.5,
            "factors": []
        }
        
        vpin_at_entry = entry_data.get('vpin', 0)
        vpin_at_exit = snapshot.get('vpin', 0)
        whale_dominance = entry_data.get('whale_dominance', 0)
        atr_pct = entry_data.get('atr_pct', 0)
        exit_reason = position.exit_reason if position else ""
        
        # 高 VPIN 環境被獵殺
        if vpin_at_entry > 0.75:
            diagnosis["factors"].append({
                "factor": "HIGH_VPIN_ENTRY",
                "description": "進場時 VPIN > 75%，可能被做市商獵殺",
                "impact": "HIGH"
            })
            diagnosis["primary_cause"] = "TOXIC_FLOW"
            diagnosis["confidence"] = 0.8
        
        # 鯨魚訊號但虧損 → 可能是陷阱
        if whale_dominance > 0.7 and position.roi < -1.0:
            diagnosis["factors"].append({
                "factor": "WHALE_TRAP",
                "description": f"高鯨魚集中度({whale_dominance:.0%})但虧損，可能是誘騙單",
                "impact": "HIGH"
            })
            if diagnosis["primary_cause"] == "UNKNOWN":
                diagnosis["primary_cause"] = "WHALE_TRAP"
                diagnosis["confidence"] = 0.75
        
        # 死魚盤磨損
        if atr_pct < 0.05:
            diagnosis["factors"].append({
                "factor": "DEAD_MARKET",
                "description": f"ATR 僅 {atr_pct:.4f}%，死魚盤環境不適合趨勢策略",
                "impact": "MEDIUM"
            })
            if diagnosis["primary_cause"] == "UNKNOWN":
                diagnosis["primary_cause"] = "DEAD_MARKET_CHURN"
                diagnosis["confidence"] = 0.7
        
        # 快速止損 → 可能是假突破
        if position.holding_seconds < 30 and exit_reason == "STOP_LOSS":
            diagnosis["factors"].append({
                "factor": "QUICK_STOP",
                "description": f"持倉僅 {position.holding_seconds:.0f} 秒即止損，可能是假突破",
                "impact": "MEDIUM"
            })
            if diagnosis["primary_cause"] == "UNKNOWN":
                diagnosis["primary_cause"] = "FALSE_BREAKOUT"
                diagnosis["confidence"] = 0.65
        
        # 鯨魚反轉
        if self._detect_whale_flip(position):
            diagnosis["factors"].append({
                "factor": "WHALE_FLIP",
                "description": "鯨魚方向在持倉期間反轉",
                "impact": "HIGH"
            })
            diagnosis["primary_cause"] = "WHALE_FLIP"
            diagnosis["confidence"] = 0.85
        
        return diagnosis
    
    def _apply_ai_recommended_adjustments(self, mode: TradingMode) -> bool:
        """
        🆕 v10.8 從 AI Bridge 讀取 recommended_adjustments 並應用到 MODE_CONFIGS
        
        這是 AI Loss Review 機制的「回寫」部分:
        1. AI Advisor 分析虧損原因
        2. AI 輸出 recommended_adjustments
        3. 本函數讀取並應用調整
        
        Returns:
            bool: 是否成功應用了調整
        """
        if mode not in {TradingMode.M_AI_WHALE_HUNTER, TradingMode.M_DRAGON}:
            return False
        
        try:
            is_dragon = mode == TradingMode.M_DRAGON
            bridge_file = "ai_dragon_bridge.json" if is_dragon else "ai_wolf_bridge.json"
            
            if not os.path.exists(bridge_file):
                return False
            
            with open(bridge_file, 'r') as f:
                bridge = json.load(f)
            
            # 讀取 AI 的建議調整
            ai_key = 'ai_to_dragon' if is_dragon else 'ai_to_wolf'
            ai_cmd = bridge.get(ai_key, {})
            adjustments = ai_cmd.get('recommended_adjustments', {})
            
            # 檢查是否有有效的診斷 (不是 NONE)
            diagnosis = adjustments.get('diagnosis', 'NONE')
            if diagnosis == 'NONE' or not adjustments:
                return False
            
            mode_emoji = self.mode_info[mode]['emoji']
            print(f"\n   🔧 [{mode_emoji}] 應用 AI 建議調整 (診斷: {diagnosis})")
            
            # 獲取當前配置
            mode_config = MODE_CONFIGS.get(mode, {})
            current_confidence = mode_config.get('confidence_threshold', 70)
            current_sl = mode_config.get('stop_loss_pct', 4.0)
            current_leverage = mode_config.get('leverage', 75)
            
            # 應用調整
            applied_changes = []
            
            # 1. 信心門檻調整
            conf_delta = adjustments.get('confidence_threshold_delta', 0)
            if conf_delta != 0:
                new_confidence = max(50, min(95, current_confidence + conf_delta))
                mode_config['confidence_threshold'] = new_confidence
                applied_changes.append(f"confidence: {current_confidence} → {new_confidence}")
            
            # 2. 止損比例調整
            sl_delta = adjustments.get('stop_loss_pct_delta', 0)
            if sl_delta != 0:
                new_sl = max(2.0, min(10.0, current_sl + sl_delta))
                mode_config['stop_loss_pct'] = new_sl
                applied_changes.append(f"stop_loss: {current_sl}% → {new_sl}%")
            
            # 3. 槓桿乘數調整
            lev_mult = adjustments.get('leverage_multiplier', 1.0)
            if lev_mult != 1.0:
                new_leverage = max(25, min(125, int(current_leverage * lev_mult)))
                mode_config['leverage'] = new_leverage
                applied_changes.append(f"leverage: {current_leverage}x → {new_leverage}x")
            
            # 4. 冷卻時間
            cooldown_mins = adjustments.get('cooldown_minutes', 0)
            if cooldown_mins > 0:
                cooldown_until = datetime.now() + timedelta(minutes=cooldown_mins)
                # 存入 mode_cooldowns (如果存在)
                if hasattr(self, 'mode_cooldowns'):
                    self.mode_cooldowns[mode] = cooldown_until
                applied_changes.append(f"cooldown: {cooldown_mins} min")
            
            # 5. 策略切換
            strategy_switch = adjustments.get('strategy_switch', 'NONE')
            if strategy_switch not in ['NONE', '']:
                mode_config['strategy_mode'] = strategy_switch
                applied_changes.append(f"strategy: → {strategy_switch}")
            
            # 更新 MODE_CONFIGS
            MODE_CONFIGS[mode] = mode_config
            
            # 印出變更
            if applied_changes:
                print(f"   🔧 變更: {', '.join(applied_changes)}")
                print(f"   🔧 原因: {adjustments.get('reasoning', 'N/A')[:80]}")
            
            # 清除 loss_review 請求 (避免重複處理)
            wolf_key = 'wolf_to_ai' if not is_dragon else 'dragon_to_ai'
            if wolf_key in bridge and 'loss_review' in bridge.get(wolf_key, {}):
                del bridge[wolf_key]['loss_review']
                with open(bridge_file, 'w') as f:
                    json.dump(bridge, f, indent=2, ensure_ascii=False)
                print(f"   🔧 已清除 loss_review 請求")
            
            return len(applied_changes) > 0
            
        except Exception as e:
            print(f"   ⚠️ Failed to apply AI adjustments: {e}")
            return False

    def _update_feedback_loop(self, mode: TradingMode, closed_position: SimulatedOrder):
        """更新 feedback loop 統計（交易結果回饋）- 完整版"""
        if mode not in {TradingMode.M_AI_WHALE_HUNTER, TradingMode.M_DRAGON}:
            return
        
        try:
            is_dragon = mode == TradingMode.M_DRAGON
            bridge_file = "ai_dragon_bridge.json" if is_dragon else "ai_wolf_bridge.json"
            
            if not os.path.exists(bridge_file):
                return
            
            with open(bridge_file, 'r') as f:
                bridge = json.load(f)
            
            feedback = bridge.get('feedback_loop', {})
            
            # 更新統計
            feedback['total_trades'] = feedback.get('total_trades', 0) + 1
            is_win = closed_position.pnl_usdt > 0
            
            if is_win:
                feedback['success_streak'] = feedback.get('success_streak', 0) + 1
                feedback['failure_streak'] = 0
            else:
                feedback['failure_streak'] = feedback.get('failure_streak', 0) + 1
                feedback['success_streak'] = 0
            
            # 計算勝率
            total = feedback['total_trades']
            wins = feedback.get('wins', 0) + (1 if is_win else 0)
            feedback['wins'] = wins
            feedback['win_rate'] = round(wins / total * 100, 2) if total > 0 else 0
            
            # 更新最佳/最差交易
            feedback['best_trade_pnl'] = max(feedback.get('best_trade_pnl', 0), closed_position.pnl_usdt)
            feedback['worst_trade_pnl'] = min(feedback.get('worst_trade_pnl', 0), closed_position.pnl_usdt)
            
            # 更新平均持倉時間
            avg_time = feedback.get('avg_holding_time', 0)
            feedback['avg_holding_time'] = (avg_time * (total - 1) + closed_position.holding_seconds) / total
            
            # 記錄最後一筆交易
            feedback['last_trade_result'] = {
                "pnl_usdt": round(closed_position.pnl_usdt, 2),
                "roi": round(closed_position.roi, 2),
                "direction": closed_position.direction,
                "holding_seconds": closed_position.holding_seconds,
                "exit_reason": closed_position.exit_reason,
                "exit_time": datetime.now().isoformat()  # 🆕 記錄平倉時間
            }
            
            # 🆕 根據交易結果自動調整獲利模式
            failure_streak = feedback.get('failure_streak', 0)
            success_streak = feedback.get('success_streak', 0)
            
            # 獲取當前 ATR 和鯨魚集中度
            atr_pct = getattr(self, '_last_atr_pct', 0.001)
            whale_dominance = self.large_trade_signal.get('dominance_ratio', 0.5)
            
            # 調用自動調整
            self._auto_adjust_profit_mode(atr_pct, failure_streak, whale_dominance)
            
            # 🎯 Priority 2: 預測準確度追蹤
            ai_command = bridge.get('ai_to_wolf', {})
            predicted_price = ai_command.get('whale_reversal_price', 0)
            actual_price = closed_position.exit_price
            
            if predicted_price > 0:
                deviation_pct = abs((actual_price - predicted_price) / predicted_price * 100)
                
                prediction_record = {
                    "predicted_price": predicted_price,
                    "actual_price": actual_price,
                    "error_pct": round(deviation_pct, 2),
                    "direction_correct": (
                        (ai_command.get('command') == 'LONG' and closed_position.roi > 0) or
                        (ai_command.get('command') == 'SHORT' and closed_position.roi > 0)
                    )
                }
                
                # 保留最近 10 次預測記錄
                recent_predictions = feedback.get('recent_predictions', [])
                recent_predictions.append(prediction_record)
                feedback['recent_predictions'] = recent_predictions[-10:]
                
                # 計算平均預測誤差
                if recent_predictions:
                    avg_error = sum(p['error_pct'] for p in recent_predictions) / len(recent_predictions)
                    direction_accuracy = sum(1 for p in recent_predictions if p['direction_correct']) / len(recent_predictions) * 100
                    
                    feedback['prediction_accuracy'] = {
                        "avg_price_error_pct": round(avg_error, 2),
                        "direction_accuracy_pct": round(direction_accuracy, 2),
                        "sample_size": len(recent_predictions)
                    }
            
            bridge['feedback_loop'] = feedback
            
            with open(bridge_file, 'w') as f:
                json.dump(bridge, f, indent=2)
                
        except Exception as e:
            print(f"   ⚠️ Failed to update feedback loop: {e}")
    
    def _check_price_movement(self, position: SimulatedOrder) -> bool:
        """檢查價格是否按預期方向移動"""
        if position.direction == "LONG":
            return self.latest_price > position.actual_entry_price
        else:
            return self.latest_price < position.actual_entry_price
    
    def _calculate_divergence(self, position: SimulatedOrder) -> float:
        """計算價格偏離預期的百分比"""
        price_change = (self.latest_price - position.actual_entry_price) / position.actual_entry_price * 100
        if position.direction == "LONG":
            return -price_change if price_change < 0 else 0
        else:
            return price_change if price_change > 0 else 0
    
    def _detect_whale_flip(self, position: SimulatedOrder) -> bool:
        """檢測持倉期間鯨魚是否反向"""
        whale_signal = self.large_trade_signal
        whale_direction = whale_signal.get('direction')
        
        if not whale_direction:
            return False
        
        # 如果鯨魚方向與持倉方向相反，視為反轉
        return whale_direction != position.direction
    
    def _calculate_liquidation_pressure(self, snapshot: dict) -> int:
        """計算清算壓力評分 (0-100)"""
        # 基於多個因素計算清算壓力
        score = 0
        
        # 1. 資金費率壓力 (極端正值=多頭過度槓桿)
        funding_rate = snapshot.get('funding_rate', 0)
        if abs(funding_rate) > 0.01:  # 1%
            score += 40
        elif abs(funding_rate) > 0.005:  # 0.5%
            score += 20
        
        # 2. VPIN 毒性 (高流動性風險)
        vpin = snapshot.get('vpin', 0)
        if vpin > 0.8:
            score += 30
        elif vpin > 0.5:
            score += 15
        
        # 3. OBI 極端失衡 (訂單簿被吃光)
        obi = abs(snapshot.get('obi', 0))
        if obi > 0.7:
            score += 30
        elif obi > 0.5:
            score += 15
        
        return min(score, 100)
    
    def _estimate_whale_trap_probability(self) -> float:
        """估算鯨魚陷阱機率 (0.0-1.0)"""
        whale_signal = self.large_trade_signal
        tracker = self.whale_reversal_tracker.get(TradingMode.M_WHALE_WATCHER, {})
        
        # 基於反轉頻率判斷
        reversal_count = tracker.get('reversal_count', 0)
        dominance = whale_signal.get('dominance_ratio', 0)
        
        # 高反轉次數 + 低集中度 = 可能是洗盤
        if reversal_count >= 3 and dominance < 0.7:
            return 0.8
        elif reversal_count >= 2:
            return 0.5
        elif dominance > 0.9:
            return 0.1  # 極高集中度不太可能是陷阱
        
        return 0.3  # 預設中等風險
    
    # ==================== 🆕 動態獲利配置系統 ====================
    
    def _load_profit_config(self) -> dict:
        """載入動態獲利配置"""
        default_config = {
            "current_mode": "normal",
            "profit_targets": {
                "aggressive": {"min_net_profit_pct": 5.0, "target_tp_pct": 15.0, "target_sl_pct": 5.0},
                "normal": {"min_net_profit_pct": 3.0, "target_tp_pct": 13.0, "target_sl_pct": 4.0},
                "conservative": {"min_net_profit_pct": 1.5, "target_tp_pct": 11.5, "target_sl_pct": 3.5},
                "ultra_safe": {"min_net_profit_pct": 0.5, "target_tp_pct": 10.5, "target_sl_pct": 3.0}
            },
            "fee_config": {"taker_fee_rate": 0.0005, "maker_fee_rate": -0.0001},
            "leverage_config": {"default_leverage": 100}
        }
        
        try:
            if os.path.exists(self.profit_config_path):
                with open(self.profit_config_path, 'r') as f:
                    config = json.load(f)
                    print(f"   📊 [Profit Config] 載入成功: 模式={config.get('current_mode', 'normal')}")
                    return config
        except Exception as e:
            print(f"   ⚠️ [Profit Config] 載入失敗: {e}，使用預設值")
        
        return default_config
    
    def _reload_profit_config_if_needed(self):
        """定期重新載入獲利配置（每 30 秒檢查一次）"""
        if time.time() - self.last_profit_config_reload > 30.0:
            try:
                if os.path.exists(self.profit_config_path):
                    mtime = os.path.getmtime(self.profit_config_path)
                    if mtime > self.last_profit_config_reload:
                        self.profit_config = self._load_profit_config()
                        print(f"   🔄 [Profit Config] 配置已更新: 模式={self.profit_config.get('current_mode', 'normal')}")
            except Exception as e:
                pass
            self.last_profit_config_reload = time.time()
    
    def _get_dynamic_tp_sl(self, mode: TradingMode, leverage: int, is_maker: bool = False) -> Tuple[float, float]:
        """
        根據動態配置計算止盈止損
        
        核心公式：
        - fee_cost_pct = fee_rate * leverage * 2 * 100 (雙邊手續費)
        - target_tp = fee_cost_pct + min_net_profit_pct
        
        🔧 死水盤用 Maker (手續費極低)，正常盤用 Taker (即時成交)
        - Maker (50x): 0.02% * 2 * 50 * 100 = 2% ROI 成本
        - Taker (50x): 0.05% * 2 * 50 * 100 = 5% ROI 成本
        
        Returns:
            (take_profit_pct, stop_loss_pct) - 都是 ROI%
        """
        # 重新載入配置（如果需要）
        self._reload_profit_config_if_needed()
        
        # 🔧 死水盤模式：使用傳入的 is_maker；正常模式：強制 Taker
        # 如果明確傳入 is_maker=True（死水盤），就用 Maker
        # 否則檢查全域設定
        if not is_maker and not self.maker_enabled:
            is_maker = False  # 正常盤強制 Taker
        # 如果 is_maker=True 被傳入，保持不變（死水盤用 Maker）
        
        # 獲取當前模式的目標
        current_mode = self.profit_config.get("current_mode", "normal")
        targets = self.profit_config.get("profit_targets", {}).get(current_mode, {})
        fee_config = self.profit_config.get("fee_config", {})
        
        # 🔧 計算手續費成本
        if is_maker:
            fee_rate = fee_config.get("maker_fee_rate", 0.0002)  # 0.02%
            if fee_rate < 0:
                fee_rate = 0.0  # 返佣 = 免手續費
        else:
            fee_rate = fee_config.get("taker_fee_rate", 0.0005)  # 0.05%
        
        fee_cost_pct = fee_rate * leverage * 2 * 100  # 雙邊手續費，轉為百分比
        
        # 計算目標止盈（確保扣除手續費後仍有淨利）
        min_net_profit = targets.get("min_net_profit_pct", 7.0)  # 🔧 預設淨利 7%
        base_tp = targets.get("target_tp_pct", 13.0)  # 🔧 預設 TP 13%
        base_sl = targets.get("target_sl_pct", 4.0)
        
        # 確保止盈 ≥ 手續費 + 最小淨利
        calculated_tp = max(base_tp, fee_cost_pct + min_net_profit)
        
        # 🔧 止損設定：比手續費成本多 1%，但最多是 TP 的 50%
        min_sl = fee_cost_pct + 1.0
        max_sl = calculated_tp * 0.5  # 風險報酬比至少 2:1
        calculated_sl = min(max(base_sl, min_sl), max_sl)
        
        # AI 模式使用動態配置
        if mode in {TradingMode.M_AI_WHALE_HUNTER, TradingMode.M_DRAGON}:
            return calculated_tp, calculated_sl
        
        # 其他模式保持原有邏輯
        return None, None  # 返回 None 表示使用原有計算
    
    def _update_profit_config_mode(self, new_mode: str, reason: str = "Manual"):
        """更新獲利配置模式"""
        try:
            self.profit_config["current_mode"] = new_mode
            self.profit_config["last_updated"] = datetime.now().isoformat()
            
            # 記錄變更歷史
            if "update_history" not in self.profit_config:
                self.profit_config["update_history"] = []
            
            self.profit_config["update_history"].append({
                "time": datetime.now().isoformat(),
                "mode": new_mode,
                "reason": reason
            })
            
            # 只保留最近 50 筆歷史
            if len(self.profit_config["update_history"]) > 50:
                self.profit_config["update_history"] = self.profit_config["update_history"][-50:]
            
            # 寫入檔案
            with open(self.profit_config_path, 'w') as f:
                json.dump(self.profit_config, f, indent=2)
            
            print(f"   📝 [Profit Config] 模式已更新: {new_mode} (原因: {reason})")
            
        except Exception as e:
            print(f"   ⚠️ [Profit Config] 更新失敗: {e}")
    
    def _auto_adjust_profit_mode(self, atr_pct: float, consecutive_losses: int, whale_dominance: float):
        """根據市場狀況自動調整獲利模式"""
        auto_config = self.profit_config.get("auto_adjustment", {})
        if not auto_config.get("enabled", False):
            return
        
        vol_rules = auto_config.get("volatility_rules", {})
        perf_rules = auto_config.get("performance_rules", {})
        whale_rules = auto_config.get("whale_rules", {})
        
        current_mode = self.profit_config.get("current_mode", "normal")
        new_mode = current_mode
        reason = ""
        
        # 規則 1: 連虧降級
        if consecutive_losses >= perf_rules.get("downgrade_after_losses", 2):
            if current_mode == "aggressive":
                new_mode = "normal"
                reason = f"連虧 {consecutive_losses} 筆，降級為 normal"
            elif current_mode == "normal":
                new_mode = "conservative"
                reason = f"連虧 {consecutive_losses} 筆，降級為 conservative"
            elif current_mode == "conservative":
                new_mode = "ultra_safe"
                reason = f"連虧 {consecutive_losses} 筆，降級為 ultra_safe"
        
        # 規則 2: 高波動 + 鯨魚活躍 -> 激進
        elif atr_pct > vol_rules.get("high_volatility_threshold", 0.003):
            if whale_dominance > whale_rules.get("high_dominance_threshold", 0.8):
                if whale_rules.get("use_aggressive_when_whale_active", True):
                    new_mode = "aggressive"
                    reason = f"高波動 ATR={atr_pct:.4f}% + 鯨魚活躍 Dom={whale_dominance:.2f}"
        
        # 規則 3: 低波動 -> 保守
        elif atr_pct < vol_rules.get("low_volatility_threshold", 0.001):
            if current_mode in ["aggressive", "normal"]:
                new_mode = "conservative"
                reason = f"低波動 ATR={atr_pct:.4f}%，降級為 conservative"
        
        # 規則 4: 極低波動（死魚盤）-> 超安全
        elif atr_pct < vol_rules.get("dead_market_threshold", 0.0005):
            new_mode = "ultra_safe"
            reason = f"死魚盤 ATR={atr_pct:.4f}%，降級為 ultra_safe"
        
        # 如果模式有變化，更新配置
        if new_mode != current_mode:
            self._update_profit_config_mode(new_mode, reason)
    
    def _calculate_dynamic_leverage(self, mode: TradingMode, base_leverage: int, decision: dict, snapshot: dict) -> int:
        """
        動態計算槓桿倍數
        根據信心度、爆倉壓力、市場狀態動態調整槓桿
        最高支援到 125x (Binance BTCUSDT 上限)
        """
        # 0. 如果決策中已經指定了槓桿 (例如 AI 決策)，直接使用
        if 'leverage' in decision:
            specified_lev = int(decision['leverage'])
        else:
            specified_lev = base_leverage

        config = self.MODE_CONFIGS[mode]
        max_lev = getattr(config, 'max_dynamic_leverage', base_leverage)
        max_lev = min(max_lev, 125)  # 硬上限 125x
        
        confidence = decision.get('confidence', 0.5)
        
        # 基礎槓桿
        final_leverage = specified_lev
        
        # 🔧 修復：低波動環境下限制槓桿，避免手續費吃掉利潤
        atr_pct = snapshot.get('atr_pct', 0) if snapshot else 0
        if atr_pct < 0.001:  # ATR < 0.001% = 極低波動
            # 在低波動下，高槓桿 + Taker 費 = 必虧
            # 限制槓桿使得手續費成本 < 預期波動
            # 0.05% × leverage × 2 < expected_move
            # 建議槓桿 ≤ 30x
            max_low_vol_leverage = 30
            if final_leverage > max_low_vol_leverage:
                print(f"   ⚠️ [{mode.name}] 低波動環境(ATR={atr_pct:.4f}%)，槓桿從 {final_leverage}x 降至 {max_low_vol_leverage}x")
                final_leverage = max_low_vol_leverage
        
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
            
            # 🆕 爆倉瀑布加成
            cascade_strength = snapshot.get('cascade_strength', 0)
            cascade_direction = snapshot.get('cascade_direction', 'HOLD')
            if cascade_strength >= 60:
                # 瀑布方向與操作方向一致時，加大槓桿
                if (cascade_direction == 'SHORT' and action == 'SHORT') or \
                   (cascade_direction == 'LONG' and action == 'LONG'):
                    final_leverage *= 1.3  # 額外 30% 槓桿
                    print(f"   🔥 M🥊 CASCADE BOOST: +30% leverage (cascade={cascade_strength:.0f})")
                
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
            
            # 處理加倉指令
            action = decision['action']
            is_adding = False
            
            if action in ['ADD_LONG', 'ADD_SHORT']:
                target_dir = 'LONG' if action == 'ADD_LONG' else 'SHORT'
                open_positions = [o for o in self.orders[mode] if not o.is_blocked and o.exit_time is None]
                
                if len(open_positions) == 0:
                    # 無持倉，視為普通開倉
                    action = target_dir
                    decision['action'] = action # 更新 decision 以便後續使用
                elif open_positions[0].direction == target_dir:
                    # 有同方向持倉，允許加倉 (最多 3 倉)
                    if len(open_positions) < 3:
                        action = target_dir
                        decision['action'] = action
                        is_adding = True
                        print(f"   ➕ [{mode.name}] Pyramiding: Adding to {target_dir} position ({len(open_positions)+1}/3)")
                    else:
                        continue # 已達最大倉位
                else:
                    continue # 方向不一致，忽略

            if decision['action'] in ['LONG', 'SHORT']:
                # ═══════════════════════════════════════════════════════════════
                # 🆕 延遲進場確認機制 (Entry Delay Confirmation)
                # 目的：避免追高殺低，等待 5 秒確認信號穩定
                # 🔧 AI 模式 (M_AI_WHALE_HUNTER, M_DRAGON) 也啟用延遲確認
                # ═══════════════════════════════════════════════════════════════
                style = self.mode_styles.get(mode, 'default')
                apply_delay = self.entry_delay_enabled and style in ['ai_whale_hunter', 'ai_dragon2']
                
                if apply_delay:
                    current_time_ts = time.time()
                    pending = self.pending_entry_signals.get(mode)
                    
                    if pending is None:
                        # 第一次收到信號，開始等待
                        self.pending_entry_signals[mode] = {
                            'signal': decision.copy(),
                            'timestamp': current_time_ts,
                            'price_at_signal': self.latest_price,
                            'direction': decision['action']
                        }
                        print(f"   ⏳ [{mode.name}] 延遲確認中... ({self.entry_delay_seconds}s)")
                        continue  # 等待下一輪
                    else:
                        elapsed = current_time_ts - pending['timestamp']
                        
                        # 檢查方向是否改變
                        if pending['direction'] != decision['action']:
                            # 方向變了，重置等待
                            self.pending_entry_signals[mode] = {
                                'signal': decision.copy(),
                                'timestamp': current_time_ts,
                                'price_at_signal': self.latest_price,
                                'direction': decision['action']
                            }
                            print(f"   🔄 [{mode.name}] 方向變化 {pending['direction']}→{decision['action']}，重新等待...")
                            continue
                        
                        # 檢查是否等待足夠時間
                        if elapsed < self.entry_delay_seconds:
                            # 不需要每次都印出，太吵
                            continue  # 繼續等待
                        
                        # 通過延遲確認！
                        price_change_pct = abs(self.latest_price - pending['price_at_signal']) / pending['price_at_signal'] * 100
                        
                        # 🆕 如果價格變化太大 (>0.3%)，可能是追高/殺低，取消進場
                        if price_change_pct > 0.3:
                            print(f"   ⚠️ [{mode.name}] 價格變化 {price_change_pct:.2f}% 過大，取消進場")
                            del self.pending_entry_signals[mode]
                            continue
                        
                        # 清除等待狀態，繼續進場
                        del self.pending_entry_signals[mode]
                        print(f"   ✅ [{mode.name}] 延遲確認通過! 價格穩定 ({price_change_pct:.3f}%)")
                
                # 檢查是否已有持倉
                open_positions = [
                    o for o in self.orders[mode]
                    if not o.is_blocked and o.exit_time is None
                ]
                
                if len(open_positions) > 0 and not is_adding:
                    continue  # 已有持倉且非加倉，跳過
                
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
                # Mup/Mdown 也不受此限，因為它們是方向探針
                # M_AI_WHALE_HUNTER 也不受此限，因為它完全聽從 AI 指令
                vpin_for_entry = snapshot.get('vpin', 0.0)
                exempt_modes = {
                    TradingMode.M_WHALE_WATCHER, 
                    TradingMode.M_LP_WHALE_BURST,
                    TradingMode.MUP_DIRECTIONAL_LONG,
                    TradingMode.MDOWN_DIRECTIONAL_SHORT,
                    TradingMode.M_AI_WHALE_HUNTER,
                    TradingMode.M_INVERSE_WOLF,
                    TradingMode.M_DRAGON
                }
                if vpin_for_entry >= 0.75 and mode not in exempt_modes:
                    # 若 diagnostics 尚未計算 edge，就保守地跳過，避免在極端區貿然交易
                    expected_move_levered = snapshot.get('expected_move_levered_pct')
                    fee_cost_pct = 0.001 * config.leverage
                    min_net_edge = fee_cost_pct * 2.0  # 至少要有 2 倍手續費空間
                    if expected_move_levered is None or expected_move_levered <= min_net_edge:
                        continue
                
                # 🆕 重新計算 TP/SL - 確保扣費後有淨利
                # 🏷️ 手續費成本計算 (考慮 Maker 優先)
                # Taker: 0.05% * 2 (開+平) * 槓桿 = 0.1% * 槓桿
                # Maker: 0.02% * 2 (開+平) * 槓桿 = 0.04% * 槓桿
                tp_pct = config.tp_pct
                sl_pct = config.sl_pct
                
                # 預設使用 Maker 費率計算 TP/SL (因為我們優先使用 Maker)
                if self.maker_enabled:
                    fee_cost_pct = 0.0004 * config.leverage  # Maker: 0.04% * 槓桿
                else:
                    fee_cost_pct = 0.001 * config.leverage   # Taker: 0.1% * 槓桿
                
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
                elif style == 'ai_whale_hunter':
                    # 🐺🐲 AI 模式：🆕 Phase 2 - 優先使用 AI 動態參數
                    market_data_from_signal = decision.get('market_data', {})
                    ai_dynamic = market_data_from_signal.get('ai_dynamic_params', {})
                    
                    # 🆕 如果有 AI 動態參數，優先使用
                    if ai_dynamic:
                        tp_pct = ai_dynamic.get('take_profit_pct', 10.0)
                        sl_pct = ai_dynamic.get('stop_loss_pct', 3.5)
                        # 🔧 v2.1 修正: trailing_distance 是絕對百分比，不是比例
                        # AI 說 2.5% 就是從高點回調 2.5% 才平倉
                        # 設為負數表示「絕對值模式」，在 check_exit 時會特別處理
                        ai_trailing_pct = ai_dynamic.get('trailing_distance', 2.5)
                        trailing_stop_pct = -ai_trailing_pct  # 負數標記為絕對值模式
                        trailing_activation = ai_dynamic.get('trailing_activation', 7.0)
                        max_holding_minutes = ai_dynamic.get('max_holding_minutes', 30)
                        max_holding_hours = max_holding_minutes / 60.0
                        min_holding_seconds = 60.0
                        
                        # 🔧 強制使用 AI 決定的槓桿
                        dynamic_leverage = decision.get('leverage', 75)
                        
                        # 🔧 確保止損 > 手續費成本
                        maker_fee = self.profit_config.get("maker_fee_rate", 0.0002)
                        if maker_fee < 0:
                            maker_fee = 0.0002
                        fee_cost_pct = maker_fee * dynamic_leverage * 2 * 100
                        min_sl = fee_cost_pct + 1.0
                        if sl_pct < min_sl:
                            sl_pct = min(min_sl, tp_pct * 0.5)
                        
                        # 🆕 顯示 AI 動態參數
                        ai_pred = market_data_from_signal.get('ai_prediction', {})
                        price_target = ai_pred.get('price_target', 0)
                        print(f"   🤖 [{mode.name}] AI動態: TP={tp_pct:.1f}%, SL={sl_pct:.1f}%, Lev={dynamic_leverage}x, MaxHold={max_holding_minutes}min")
                        if price_target > 0:
                            print(f"   🎯 [{mode.name}] AI預測目標價: ${price_target:.0f}")
                    else:
                        # Fallback: 使用舊的動態獲利配置
                        is_maker = decision.get('is_maker', False)
                        dynamic_tp, dynamic_sl = self._get_dynamic_tp_sl(mode, config.leverage, is_maker)
                        
                        if dynamic_tp is not None:
                            tp_pct = dynamic_tp
                            sl_pct = dynamic_sl
                            trailing_stop_pct = 0.4  # AI 模式使用較寬的追蹤止損
                            max_holding_hours = 24
                            min_holding_seconds = 120.0
                            
                            maker_fee = self.profit_config.get("maker_fee_rate", 0.0002)
                            if maker_fee < 0:
                                maker_fee = 0.0002
                            fee_cost_pct = maker_fee * config.leverage * 2 * 100
                            min_sl = fee_cost_pct + 1.0
                            max_reasonable_sl = tp_pct * 0.5
                            
                            if sl_pct < min_sl:
                                sl_pct = min(min_sl, max_reasonable_sl)
                                print(f"   ⚠️ [{mode.name}] 止損調整: {sl_pct:.1f}% (手續費成本: {fee_cost_pct:.1f}%)")
                            
                            current_profit_mode = self.profit_config.get("current_mode", "normal")
                            print(f"   📊 [{mode.name}] 動態獲利: 模式={current_profit_mode}, TP={tp_pct:.1f}%, SL={sl_pct:.1f}%")
                        else:
                            # fallback
                            maker_fee = self.profit_config.get("maker_fee_rate", 0.0002)
                            if maker_fee < 0:
                                maker_fee = 0.0002
                            fee_cost_pct = maker_fee * config.leverage * 2 * 100
                            tp_pct = max(10.0, fee_cost_pct + 5.0)
                            sl_pct = min(tp_pct * 0.5, max(3.0, fee_cost_pct + 1.0))
                            min_holding_seconds = 120.0
                elif style == 'ai_shrimp':
                    # 🦐🐦 Shrimp/Bird 優化策略: 2-3分鐘持倉，5% TP，2% SL
                    # 從 market_data 獲取參數（在 ai_shrimp 信號生成時設定）
                    market_data_from_signal = decision.get('market_data', {})
                    tp_pct = market_data_from_signal.get('take_profit_pct', 5.0)
                    sl_pct = market_data_from_signal.get('stop_loss_pct', 2.0)
                    min_holding_seconds = market_data_from_signal.get('min_holding_seconds', 120.0)
                    max_holding_hours = market_data_from_signal.get('max_holding_seconds', 180.0) / 3600.0
                    trailing_stop_pct = 0.8  # 較寬的追蹤止損，讓利潤跑
                    
                    # 🔧 強制使用 50x 槓桿（覆蓋 config）
                    dynamic_leverage = market_data_from_signal.get('leverage', 50)
                    
                    print(f"   🦐 [{mode.name}] 蝦兵蟹將模式: TP={tp_pct:.1f}%, SL={sl_pct:.1f}%, Hold={min_holding_seconds:.0f}-{max_holding_hours*3600:.0f}s, Lev={dynamic_leverage}x")
                elif style == 'ai_dragon2':
                    # 🐲2 Dragon V2: 改良版設定
                    market_data_from_signal = decision.get('market_data', {})
                    tp_pct = market_data_from_signal.get('take_profit_pct', 3.0)   # 降低止盈
                    sl_pct = market_data_from_signal.get('stop_loss_pct', 2.0)
                    min_holding_seconds = 30.0  # 最小持倉 30 秒
                    max_holding_hours = market_data_from_signal.get('max_holding_seconds', 180.0) / 3600.0
                    trailing_stop_pct = 0.5  # 較緊的追蹤止損
                    
                    # 🔧 強制使用 50x 槓桿
                    dynamic_leverage = 50
                    
                    print(f"   🐲2 [{mode.name}] Dragon V2 模式: TP={tp_pct:.1f}%, SL={sl_pct:.1f}%, MaxHold={max_holding_hours*3600:.0f}s, Lev={dynamic_leverage}x")
                else:
                    tp_pct = fee_cost_pct + 2.0
                    sl_pct = 1.0
                    min_holding_seconds = max(min_holding_seconds, 30.0)
                
                # 🆕 動態計算槓桿 (🦐🐦 ai_shrimp 和 🐲2 ai_dragon2 已在上面設定，跳過)
                if style not in ['ai_shrimp', 'ai_dragon2']:
                    dynamic_leverage = self._calculate_dynamic_leverage(mode, config.leverage, decision, snapshot)

                # 🏷️ Maker 優先機制：決定是否使用 Maker 掛單
                use_maker = False
                maker_price = None
                maker_timeout = 60.0  # 🔧 從 30 秒延長到 60 秒，給 Maker 更多時間成交
                
                if self.maker_enabled:
                    # 獲取最佳買賣價
                    best_bid = self.latest_price
                    best_ask = self.latest_price
                    if self.orderbook_data and self.orderbook_data.get('bids') and self.orderbook_data.get('asks'):
                        best_bid = float(self.orderbook_data['bids'][0][0])
                        best_ask = float(self.orderbook_data['asks'][0][0])
                    
                    # 根據信心度和緊急程度決定
                    confidence = decision.get('confidence', 0.5)
                    trap_mode = decision_market_data.get('trap_master_mode', 'standard')
                    is_breakout = trap_mode in ['breakout', 'momentum']
                    
                    # 判斷緊急程度
                    if trap_mode in ['dead_market_reversal', 'reversal_ambush']:
                        urgency = "LOW"  # 反轉埋伏不急
                    elif confidence > 0.8:
                        urgency = "MEDIUM"
                    elif is_breakout:
                        urgency = "HIGH"
                    else:
                        urgency = "LOW"
                    
                    use_maker, maker_timeout, maker_reason = should_use_maker(urgency, confidence, is_breakout)
                    
                    if use_maker:
                        # 檢查是否有 AI 提供的 whale_reversal_price
                        whale_reversal_price = decision_market_data.get('whale_reversal_price', 0)
                        
                        if whale_reversal_price > 0:
                            # 使用 AI 預測的反轉價格作為限價
                            maker_price = whale_reversal_price
                        else:
                            # 自動計算 Maker 價格
                            maker_price = self.maker_manager.calculate_maker_price(
                                direction=decision['action'],
                                current_price=self.latest_price,
                                best_bid=best_bid,
                                best_ask=best_ask,
                                aggressive=(confidence > 0.75)
                            )
                        
                        print(f"   🏷️ [{mode.name}] {maker_reason}")
                        print(f"      掛單價: ${maker_price:,.2f} | 當前價: ${self.latest_price:,.2f} | 超時: {maker_timeout}s")
                
                # 計算實際進場價格
                if use_maker and maker_price:
                    actual_entry_price = maker_price  # Maker 使用掛單價
                    is_maker_order = True
                else:
                    actual_entry_price = self.latest_price * (1.0002 if decision['action'] == "LONG" else 0.9998)  # Taker 滑點
                    is_maker_order = False

                # 創建訂單
                order = SimulatedOrder(
                    strategy=mode.name,
                    direction=decision['action'],
                    leverage=dynamic_leverage,
                    size=position_size,
                    entry_price=self.latest_price,
                    actual_entry_price=actual_entry_price if not use_maker else self.latest_price,  # Maker 先用當前價，成交後更新
                    position_value=position_value,
                    take_profit_pct=tp_pct,
                    stop_loss_pct=sl_pct,
                    trailing_stop_pct=trailing_stop_pct,
                    max_holding_hours=max_holding_hours,
                    min_holding_seconds=min_holding_seconds,
                    entry_time=self.orderbook_timestamp,
                    market_data=decision_market_data,
                    is_maker=is_maker_order,
                    # 🆕 Maker 掛單參數
                    maker_limit_price=maker_price if use_maker else 0,
                    maker_timeout_seconds=maker_timeout,
                    maker_allow_taker_fallback=False  # 🔧 超時取消，讓 AI 重新評估
                )
                
                # 🆕 確保 entry_reason 被記錄
                if decision.get('reason'):
                    order.entry_reason = decision['reason']
                
                # 顯示開倉資訊
                strategy_info = self.mode_info[mode]
                direction_emoji = "📈" if order.direction == "LONG" else "📉"
                direction_text = "做多" if order.direction == "LONG" else "做空"
                border_icon = "🟢" if decision['confidence'] > 0.7 else "🟡" if decision['confidence'] > 0.5 else "🔴"
                
                # 🏷️ Maker/Taker 標記
                if order.maker_status == "PENDING":
                    order_type_emoji = "⏳"
                    order_type_text = f"Maker 掛單中 @${maker_price:,.2f}"
                elif order.is_maker:
                    order_type_emoji = "🏷️"
                    order_type_text = "Maker"
                else:
                    order_type_emoji = "⚡"
                    order_type_text = "Taker"
                
                fee_cost = calculate_fee_impact(order.leverage, order.is_maker)
                
                expected_hold = "動態調整"
                current_time = datetime.now().strftime('%H:%M:%S')
                market_data = decision_market_data.copy()
                
                # 統一顯示格式
                print()
                print(f"{border_icon}{'=' * 78}{border_icon}")
                print(f"{direction_emoji} [{strategy_info['emoji']}] {strategy_info['name']} - {direction_text} | {current_time}")
                print(f"{'─' * 80}")
                if order.maker_status == "PENDING":
                    print(f"   ⏳ 掛單價格: ${maker_price:,.2f} | 當前價: ${self.latest_price:,.2f} | 超時: {maker_timeout}s")
                else:
                    print(f"   💰 進場價格: ${order.actual_entry_price:,.2f} ({order_type_emoji} {order_type_text})")
                print(f"   📊 槓桿: {order.leverage}x | 倉位: {position_size*100:.1f}% | 投資: ${order.position_value:.2f}")
                print(f"   🎯 止盈: +{tp_pct:.2f}% | 🛑 止損: -{sl_pct:.2f}% | 💸 手續費: {fee_cost['roi_cost']:.1f}% ROI")
                
                # AI 模式特殊資訊
                if style == 'ai_whale_hunter':
                    print(f"   🐳 鯨魚方向: {market_data.get('whale_direction', 'N/A')} | 淨量: {market_data.get('whale_net_qty', 0):.2f} BTC")
                elif style == 'whale':
                    print(f"   🐳 大單集中度: {market_data.get('whale_dominance', 0):.2f}")
                    print(f"   🐳 大單淨量: {market_data.get('whale_net_qty', 0):.2f} BTC")
                
                print(f"   📝 原因: {decision.get('reason', 'N/A')[:80]}")
                print(f"{border_icon}{'=' * 78}{border_icon}\n")
                
                # 記錄訂單
                self.orders[mode].append(order)
                
                # 🆕 更新最後開倉時間
                self.last_entry_time[mode] = time.time()
                
                # 🆕 如果是 M🐺，回報狀態到 Bridge
                if mode == TradingMode.M_AI_WHALE_HUNTER:
                    self._update_wolf_status_to_bridge('IN_POSITION', order, snapshot)
    
    def check_pending_maker_orders(self, snapshot: Optional[dict]):
        """
        🆕 檢查所有 PENDING 狀態的 Maker 掛單是否應該成交
        
        模擬邏輯：
        - 檢查價格是否觸及掛單價
        - 檢查是否超時
        - 超時後根據設定決定是 Taker 補單還是取消
        """
        if snapshot is None:
            return
        
        for mode in self.active_modes:
            pending_orders = [
                o for o in self.orders[mode]
                if not o.is_blocked and o.exit_time is None and getattr(o, 'maker_status', 'FILLED') == "PENDING"
            ]
            
            for order in pending_orders:
                # 嘗試從 K 線數據獲取 high/low
                high_price = None
                low_price = None
                
                # 也可以用 orderbook 的最佳價格模擬
                if self.orderbook_data:
                    if self.orderbook_data.get('bids'):
                        low_price = float(self.orderbook_data['bids'][0][0])
                    if self.orderbook_data.get('asks'):
                        high_price = float(self.orderbook_data['asks'][0][0])
                
                # 檢查是否應該成交
                result = order.check_maker_fill(
                    current_price=self.latest_price,
                    high_price=high_price,
                    low_price=low_price
                )
                
                strategy_info = self.mode_info[mode]
                
                if result == "FILLED":
                    # 成交！
                    elapsed = time.time() - order.maker_created_time
                    print(f"\n   ✅ [{strategy_info['emoji']}] Maker 掛單成交！")
                    print(f"      成交價: ${order.actual_entry_price:,.2f} | 等待: {elapsed:.1f}s")
                    print(f"      💰 手續費節省: Maker -0.01% vs Taker 0.05%")
                    
                    # 更新 Maker 統計
                    if hasattr(self, 'maker_manager'):
                        self.maker_manager.stats['total_orders'] += 1
                        self.maker_manager.stats['filled_as_maker'] += 1
                        fee_saved = order.position_value * order.leverage * 0.0006  # 0.05% - (-0.01%)
                        self.maker_manager.stats['total_fee_saved'] += fee_saved
                    
                elif result == "TIMEOUT_TAKER":
                    # 超時，用 Taker 補單
                    print(f"\n   ⏰ [{strategy_info['emoji']}] Maker 掛單超時，改用 Taker 進場")
                    print(f"      新進場價: ${order.actual_entry_price:,.2f}")
                    
                    # 更新統計
                    if hasattr(self, 'maker_manager'):
                        self.maker_manager.stats['total_orders'] += 1
                        self.maker_manager.stats['taker_fallback'] += 1
                    
                elif result == "TIMEOUT_CANCELLED":
                    # 超時取消 - 通知 AI 重新評估
                    elapsed = order.maker_timeout_seconds
                    print(f"\n   ❌ [{strategy_info['emoji']}] Maker 掛單超時取消 (等待 {elapsed:.0f}s)")
                    print(f"      掛單價: ${order.maker_limit_price:,.2f} | 當前價: ${self.latest_price:,.2f}")
                    print(f"      📝 已通知 AI 重新評估策略")
                    
                    order.is_blocked = True
                    order.blocked_reasons = ["MAKER_TIMEOUT_CANCELLED"]
                    
                    # 🆕 寫入 Bridge 通知 AI 重新評估
                    self._notify_ai_maker_timeout(mode, order)
                    
                    # 更新統計
                    if hasattr(self, 'maker_manager'):
                        self.maker_manager.stats['total_orders'] += 1
                        self.maker_manager.stats['cancelled'] += 1
                
                # PENDING 狀態不做任何事，繼續等待
    
    def check_exits(self, snapshot: Optional[dict]):
        """檢查所有持倉是否應該平倉"""
        if snapshot is None:
            return
        
        # 🆕 先檢查 Maker 掛單狀態
        self.check_pending_maker_orders(snapshot)
        
        # 🆕 M_NEW: 檢查爆倉
        if self.m_new_config['enabled'] and self.m_new_config['order'] is not None:
            self._check_m_new_liquidation(snapshot)
        
        for mode in self.active_modes:
            # 🆕 Mup/Mdown: 永遠不平倉（作為開場指標）
            if mode in {TradingMode.MUP_DIRECTIONAL_LONG, TradingMode.MDOWN_DIRECTIONAL_SHORT}:
                continue

            # 獲取該模式的開倉訂單（排除 PENDING 狀態的 Maker 訂單）
            open_orders = [
                o for o in self.orders[mode]
                if not o.is_blocked and o.exit_time is None and getattr(o, 'maker_status', 'FILLED') != "PENDING"
            ]
            
            # 🧠 M_AI_WHALE_HUNTER & M_DRAGON 特殊出場邏輯：動態止盈 + force_exit
            if mode in {TradingMode.M_AI_WHALE_HUNTER, TradingMode.M_DRAGON} and open_orders:
                try:
                    is_dragon = mode == TradingMode.M_DRAGON
                    bridge_file = "ai_dragon_bridge.json" if is_dragon else "ai_wolf_bridge.json"
                    profit_config_file = "ai_profit_config.json"
                    
                    # 讀取動態止盈配置
                    profit_config = {}
                    if os.path.exists(profit_config_file):
                        with open(profit_config_file, 'r') as f:
                            profit_config = json.load(f).get('dynamic_profit_taking', {})
                    
                    # 讀取 Bridge 數據
                    force_exit = False
                    feedback_loop = {}
                    ai_cmd = {}
                    if os.path.exists(bridge_file):
                        with open(bridge_file, 'r') as f:
                            bridge = json.load(f)
                        # 🔧 修復: Dragon 讀取 ai_to_dragon, Wolf 讀取 ai_to_wolf
                        ai_key = 'ai_to_dragon' if is_dragon else 'ai_to_wolf'
                        ai_cmd = bridge.get(ai_key, {})
                        feedback_loop = bridge.get('feedback_loop', {})
                        if ai_cmd.get('command') == 'CUT_LOSS':
                            force_exit = True
                        
                        # 🆕 v10.8: 檢查並應用 AI 的 recommended_adjustments
                        if ai_cmd.get('recommended_adjustments', {}).get('diagnosis', 'NONE') != 'NONE':
                            self._apply_ai_recommended_adjustments(mode)
                    
                    for position in open_orders:
                        _, unrealized_pnl_pct = position.update_unrealized_pnl(self.latest_price)
                        holding_seconds = (datetime.now() - datetime.fromisoformat(position.entry_time)).total_seconds()
                        
                        trap_mode = position.market_data.get('trap_master_mode', 'standard')
                        
                        # 🆕 初始化變數 (避免後續未定義錯誤)
                        win_rate = feedback_loop.get('win_rate', 0)
                        drawdown = 0
                        trailing_stop_active = False
                        profit_stage = "standard"
                        
                        # ═══════════════════════════════════════════════════════════════
                        # 🎯 v2.0: 優先使用 AI 動態參數 (而非舊的動態止盈邏輯)
                        # ═══════════════════════════════════════════════════════════════
                        
                        # 🆕 檢查是否有 AI 動態參數
                        ai_dynamic_params = position.market_data.get('ai_dynamic_params', {})
                        ai_take_profit = ai_dynamic_params.get('take_profit_pct', 0)
                        ai_stop_loss = ai_dynamic_params.get('stop_loss_pct', 0)
                        
                        # 如果有 AI 動態參數且 trap_mode 是 ai_dynamic，使用 AI 的 TP/SL
                        if trap_mode == 'ai_dynamic' and ai_take_profit > 0:
                            # 🤖 AI 控制模式 - 使用 AI 設定的止盈止損
                            dynamic_target = ai_take_profit
                            stop_loss_pct = ai_stop_loss if ai_stop_loss > 0 else 5.0
                            profit_stage = "ai_controlled"
                            trailing_stop_active = False
                            
                            # AI 的移動止盈
                            ai_trailing_activation = ai_dynamic_params.get('trailing_activation', 0)
                            ai_trailing_distance = ai_dynamic_params.get('trailing_distance', 0)
                            
                            if ai_trailing_activation > 0 and unrealized_pnl_pct >= ai_trailing_activation:
                                if not hasattr(position, 'peak_pnl_pct'):
                                    position.peak_pnl_pct = unrealized_pnl_pct
                                else:
                                    position.peak_pnl_pct = max(position.peak_pnl_pct, unrealized_pnl_pct)
                                
                                drawdown = position.peak_pnl_pct - unrealized_pnl_pct
                                if ai_trailing_distance > 0 and drawdown >= ai_trailing_distance:
                                    trailing_stop_active = True
                                    dynamic_target = unrealized_pnl_pct  # 立即止盈
                            
                            # 最大持倉時間檢查
                            ai_max_holding = ai_dynamic_params.get('max_holding_minutes', 60) * 60
                            if holding_seconds > ai_max_holding:
                                force_exit = True
                                
                        # 檢查是否啟用動態止盈（舊邏輯，作為 fallback）
                        elif not profit_config.get('enabled', True):
                            # 如果禁用,使用固定值
                            base_profit_target = position.market_data.get('quick_profit_target', 0.8)
                            dynamic_target = base_profit_target
                            should_exit = unrealized_pnl_pct > dynamic_target and holding_seconds > 20
                            exit_reason = f"M🐺 Fixed Profit: {unrealized_pnl_pct:.2f}%" if should_exit else None
                        else:
                            # 讀取基礎目標
                            base_targets = profit_config.get('base_targets', {})
                            base_profit_target = base_targets.get(trap_mode, 0.8)
                            
                            # 1️⃣ 根據歷史表現調整
                            performance_adj = profit_config.get('performance_based_adjustment', {})
                            performance_enabled = performance_adj.get('enabled', True)
                            min_trades = performance_adj.get('min_trades_for_adjustment', 5)
                            
                            win_rate = feedback_loop.get('win_rate', 0)
                            total_trades = feedback_loop.get('total_trades', 0)
                            
                            performance_multiplier = 1.0
                            if performance_enabled and total_trades >= min_trades:
                                thresholds = performance_adj.get('win_rate_thresholds', {})
                                
                                excellent = thresholds.get('excellent', {})
                                if win_rate >= excellent.get('min_win_rate', 70):
                                    performance_multiplier = excellent.get('multiplier', 2.0)
                                    max_by_performance = excellent.get('max_target', 6.0)
                                else:
                                    good = thresholds.get('good', {})
                                    poor = thresholds.get('poor', {})
                                    if win_rate >= good.get('min_win_rate', 50):
                                        performance_multiplier = good.get('multiplier', 1.5)
                                        max_by_performance = good.get('max_target', 4.0)
                                    elif win_rate < poor.get('max_win_rate', 30):
                                        performance_multiplier = poor.get('multiplier', 0.6)
                                        max_by_performance = poor.get('max_target', 1.0)
                                    else:
                                        max_by_performance = 6.0
                            else:
                                max_by_performance = 6.0
                            
                            # 2️⃣ 根據當前獲利幅度動態調整
                            progressive = profit_config.get('progressive_targets', {})
                            progressive_enabled = progressive.get('enabled', True)
                            
                            if progressive_enabled:
                                stages = progressive.get('stages', {})
                                
                                if unrealized_pnl_pct <= 0.5:
                                    # minimal stage
                                    minimal = stages.get('minimal', {})
                                    multiplier = minimal.get('target_multiplier', 0.5)
                                    min_target = minimal.get('min_target', 0.3)
                                    dynamic_target = max(min_target, base_profit_target * multiplier)
                                    profit_stage = "minimal"
                                elif unrealized_pnl_pct <= 1.0:
                                    # low stage
                                    dynamic_target = base_profit_target * performance_multiplier
                                    profit_stage = "low"
                                elif unrealized_pnl_pct <= 2.0:
                                    # medium stage
                                    medium = stages.get('medium', {})
                                    dynamic_target = medium.get('target', 2.5)
                                    profit_stage = "medium"
                                else:
                                    # high stage
                                    high = stages.get('high', {})
                                    base = high.get('base_target', 3.0)
                                    rate = high.get('progression_rate', 0.5)
                                    max_target = high.get('max_target', 6.0)
                                    
                                    dynamic_target = base + (unrealized_pnl_pct - 2.0) * rate
                                    dynamic_target = min(dynamic_target, max_target, max_by_performance)
                                    profit_stage = "high"
                            else:
                                dynamic_target = base_profit_target * performance_multiplier
                                profit_stage = "standard"
                            
                            # 3️⃣ 根據持倉時間調整
                            time_adj = profit_config.get('time_based_adjustment', {})
                            if time_adj.get('enabled', True):
                                thresholds = time_adj.get('thresholds', {})
                                
                                if holding_seconds > thresholds.get('long', {}).get('min_seconds', 120):
                                    time_multiplier = thresholds.get('long', {}).get('multiplier', 0.7)
                                elif holding_seconds > thresholds.get('medium', {}).get('max_seconds', 60):
                                    time_multiplier = thresholds.get('medium', {}).get('multiplier', 0.85)
                                else:
                                    time_multiplier = thresholds.get('short', {}).get('multiplier', 1.0)
                                
                                dynamic_target *= time_multiplier
                            
                            # 4️⃣ 移動止盈 (Trailing Stop)
                            trailing = profit_config.get('trailing_stop', {})
                            trailing_enabled = trailing.get('enabled', True)
                            trailing_stop_active = False
                            drawdown = 0
                            
                            if trailing_enabled:
                                activation_pnl = trailing.get('activation_pnl', 2.0)
                                trailing_distance = trailing.get('trailing_distance', 0.5)
                                
                                if unrealized_pnl_pct > activation_pnl:
                                    if not hasattr(position, 'peak_pnl_pct'):
                                        position.peak_pnl_pct = unrealized_pnl_pct
                                    else:
                                        position.peak_pnl_pct = max(position.peak_pnl_pct, unrealized_pnl_pct)
                                    
                                    drawdown = position.peak_pnl_pct - unrealized_pnl_pct
                                    if drawdown > trailing_distance:
                                        trailing_stop_active = True
                                        dynamic_target = unrealized_pnl_pct
                        
                        # ═══════════════════════════════════════════════════════════════
                        # 📊 出場條件判斷
                        # ═══════════════════════════════════════════════════════════════
                        
                        should_exit = False
                        exit_reason = None
                        
                        min_holding = profit_config.get('minimum_holding_time', 20)
                        
                        # 1. 動態止盈達標
                        if unrealized_pnl_pct >= dynamic_target and holding_seconds > min_holding:
                            should_exit = True
                            prefix = "M🐲" if is_dragon else "M🐺"
                            if trailing_stop_active:
                                exit_reason = f"{prefix} Trailing Stop: {unrealized_pnl_pct:.2f}% (Peak: {position.peak_pnl_pct:.2f}%, DD: {drawdown:.2f}%)"
                            elif profit_stage == "ai_controlled":
                                # 🆕 AI 控制模式的止盈訊息
                                exit_reason = f"{prefix} AI Take Profit: {unrealized_pnl_pct:.2f}% (AI Target: {dynamic_target:.2f}%)"
                            else:
                                exit_reason = f"{prefix} Dynamic Exit: {unrealized_pnl_pct:.2f}% (Target: {dynamic_target:.2f}%, Stage: {profit_stage}, WR: {win_rate:.0f}%)"
                        
                        # 2. 強制平倉 (AI CUT_LOSS)
                        elif force_exit:
                            should_exit = True
                            prefix = "M🐲" if is_dragon else "M🐺"
                            exit_reason = f"{prefix} AI CUT_LOSS: Prediction diverged"
                        
                        # 3. 方向反轉 (AI Flip)
                        elif ai_cmd.get('command') in ['LONG', 'SHORT']:
                            ai_command = ai_cmd.get('command')
                            if (position.direction == 'LONG' and ai_command == 'SHORT') or \
                               (position.direction == 'SHORT' and ai_command == 'LONG'):
                                should_exit = True
                                prefix = "M🐲" if is_dragon else "M🐺"
                                exit_reason = f"{prefix} AI Flip: {position.direction} -> {ai_command}"

                        # 4. 止損保護 (Stop Loss) - 🔧 v2.0: 優先使用 AI 動態參數
                        # 優先級: ai_dynamic_params > ai_cmd > 預設值
                        if ai_dynamic_params.get('stop_loss_pct', 0) > 0:
                            stop_loss_pct = ai_dynamic_params.get('stop_loss_pct')
                        else:
                            stop_loss_pct = ai_cmd.get('stop_loss_pct', 5.0)
                        
                        if unrealized_pnl_pct < -stop_loss_pct:
                            should_exit = True
                            prefix = "M🐲" if is_dragon else "M🐺"
                            exit_reason = f"{prefix} Stop Loss: {unrealized_pnl_pct:.2f}% < -{stop_loss_pct}%"
                        
                        if should_exit:
                            position.close(
                                exit_price=self.latest_price,
                                reason=exit_reason,
                                timestamp=self.orderbook_timestamp
                            )
                            self.balances[mode] += position.pnl_usdt
                            
                            emoji = "✅" if position.pnl_usdt > 0 else "❌"
                            prefix = "🐲" if is_dragon else "🐺"
                            print(f"\n{emoji} [{prefix}] {exit_reason}")
                            print(f"   {position.direction}: ${position.actual_entry_price:.2f} → ${self.latest_price:.2f}")
                            print(f"   PnL: ${position.pnl_usdt:.2f} ({position.roi:.2f}%)")
                            print(f"   Holding: {holding_seconds:.0f}s")
                            
                            # 回報平倉到 Bridge
                            self._update_wolf_status_to_bridge('IDLE', None, snapshot, is_dragon=is_dragon)
                            self._update_feedback_loop(mode, position)
                    
                    if any(o.exit_time is not None for o in open_orders):
                        continue
                except Exception as e:
                    print(f"   ⚠️ M🐺/M🐲 exit check error: {e}")
            
            # 🐲2 M_DRAGON2 特殊出場邏輯：鯨魚過濾 + 縮短持倉 + 降低止盈
            if mode == TradingMode.M_DRAGON2 and open_orders:
                try:
                    for position in open_orders:
                        _, unrealized_pnl_pct = position.update_unrealized_pnl(self.latest_price)
                        holding_seconds = (datetime.now() - datetime.fromisoformat(position.entry_time)).total_seconds()
                        
                        # 從 market_data 讀取設定
                        max_holding = position.market_data.get('max_holding_seconds', 180)  # 3 分鐘
                        tp_pct = position.market_data.get('take_profit_pct', 3.0)           # 3% TP
                        sl_pct = position.market_data.get('stop_loss_pct', 2.0)             # 2% SL
                        quick_target = position.market_data.get('quick_profit_target', 2.0) # 2% 快速止盈
                        
                        should_exit = False
                        exit_reason = None
                        
                        # 1. 止損保護 (SL)
                        if unrealized_pnl_pct < -sl_pct:
                            should_exit = True
                            exit_reason = f"M🐲2 Stop Loss: {unrealized_pnl_pct:.2f}% < -{sl_pct}%"
                        
                        # 2. 止盈 (降低目標更容易觸發)
                        elif unrealized_pnl_pct >= tp_pct:
                            should_exit = True
                            exit_reason = f"M🐲2 Take Profit: {unrealized_pnl_pct:.2f}% >= {tp_pct}%"
                        
                        # 3. 快速止盈 (持倉超過 60 秒後，小利潤就出場)
                        elif holding_seconds >= 60 and unrealized_pnl_pct >= quick_target:
                            should_exit = True
                            exit_reason = f"M🐲2 Quick Profit: {unrealized_pnl_pct:.2f}% >= {quick_target}% @ {holding_seconds:.0f}s"
                        
                        # 4. 超過最大持倉時間：強制出場
                        elif holding_seconds >= max_holding:
                            should_exit = True
                            if unrealized_pnl_pct > 0:
                                exit_reason = f"M🐲2 Time Exit (Profit): {unrealized_pnl_pct:.2f}% @ {holding_seconds:.0f}s"
                            else:
                                exit_reason = f"M🐲2 Time Exit (Loss): {unrealized_pnl_pct:.2f}% @ {holding_seconds:.0f}s"
                        
                        if should_exit:
                            position.close(
                                exit_price=self.latest_price,
                                reason=exit_reason,
                                timestamp=self.orderbook_timestamp
                            )
                            self.balances[mode] += position.pnl_usdt
                            
                            emoji = "✅" if position.pnl_usdt > 0 else "❌"
                            print(f"\n{emoji} [🐲2] {exit_reason}")
                            print(f"   {position.direction}: ${position.actual_entry_price:.2f} → ${self.latest_price:.2f}")
                            print(f"   PnL: ${position.pnl_usdt:.2f} ({position.roi:.2f}%)")
                            print(f"   Holding: {holding_seconds:.0f}s")
                    
                    if any(o.exit_time is not None for o in open_orders):
                        continue
                except Exception as e:
                    print(f"   ⚠️ M🐲2 exit check error: {e}")
            
            # 🦐🐦 M_SHRIMP & M_BIRD 特殊出場邏輯：強制持倉時間 + 固定 TP/SL
            if mode in {TradingMode.M_SHRIMP, TradingMode.M_BIRD} and open_orders:
                try:
                    is_bird = mode == TradingMode.M_BIRD
                    prefix = "🐦" if is_bird else "🦐"
                    
                    for position in open_orders:
                        _, unrealized_pnl_pct = position.update_unrealized_pnl(self.latest_price)
                        holding_seconds = (datetime.now() - datetime.fromisoformat(position.entry_time)).total_seconds()
                        
                        # 從 market_data 讀取設定
                        min_holding = position.market_data.get('min_holding_seconds', 120)  # 2 分鐘
                        max_holding = position.market_data.get('max_holding_seconds', 180)  # 3 分鐘
                        tp_pct = position.market_data.get('take_profit_pct', 5.0)           # 5% TP
                        sl_pct = position.market_data.get('stop_loss_pct', 2.0)             # 2% SL
                        
                        should_exit = False
                        exit_reason = None
                        
                        # 1. 止損保護 (SL) - 無論持倉時間，虧損達標就出場
                        if unrealized_pnl_pct < -sl_pct:
                            should_exit = True
                            exit_reason = f"M{prefix} Stop Loss: {unrealized_pnl_pct:.2f}% < -{sl_pct}%"
                        
                        # 2. 最小持倉時間內：只有止損會觸發
                        elif holding_seconds < min_holding:
                            # 不出場，繼續持有
                            pass
                        
                        # 3. 達到最小持倉時間後：檢查止盈
                        elif unrealized_pnl_pct >= tp_pct:
                            should_exit = True
                            exit_reason = f"M{prefix} Take Profit: {unrealized_pnl_pct:.2f}% >= {tp_pct}%"
                        
                        # 4. 超過最大持倉時間：強制出場
                        elif holding_seconds >= max_holding:
                            should_exit = True
                            if unrealized_pnl_pct > 0:
                                exit_reason = f"M{prefix} Time Exit (Profit): {unrealized_pnl_pct:.2f}% @ {holding_seconds:.0f}s"
                            else:
                                exit_reason = f"M{prefix} Time Exit (Loss): {unrealized_pnl_pct:.2f}% @ {holding_seconds:.0f}s"
                        
                        if should_exit:
                            position.close(
                                exit_price=self.latest_price,
                                reason=exit_reason,
                                timestamp=self.orderbook_timestamp
                            )
                            self.balances[mode] += position.pnl_usdt
                            
                            emoji = "✅" if position.pnl_usdt > 0 else "❌"
                            print(f"\n{emoji} [M{prefix}] {exit_reason}")
                            print(f"   {position.direction}: ${position.actual_entry_price:.2f} → ${self.latest_price:.2f}")
                            print(f"   PnL: ${position.pnl_usdt:.2f} ({position.roi:.2f}%)")
                            print(f"   Holding: {holding_seconds:.0f}s (Min: {min_holding}s, Max: {max_holding}s)")
                    
                    if any(o.exit_time is not None for o in open_orders):
                        continue
                except Exception as e:
                    print(f"   ⚠️ M{prefix} exit check error: {e}")
            
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
                    
                    # 🆕 v10.7 更新連虧追蹤 + AI 智能復盤
                    if position.roi < 0:
                        self.consecutive_losses[mode] += 1
                        
                        # 觸發 AI 復盤分析（連虧 2 筆或單筆虧損 > 2%）
                        trigger_review = (
                            self.consecutive_losses[mode] >= 2 or 
                            abs(position.roi) > 2.0
                        )
                        
                        if trigger_review:
                            # 打包虧損數據發送給 AI
                            self._trigger_ai_loss_review(
                                mode=mode,
                                position=position,
                                snapshot=snapshot,
                                consecutive_losses=self.consecutive_losses[mode]
                            )
                            # 短暫冷卻等待 AI 回覆（30 秒）
                            self.loss_cooldown_until[mode] = time.time() + 30
                            print(f"   🤖 [{self.mode_info[mode]['emoji']}] 觸發 AI 復盤分析...")
                        
                        # 連虧 5 筆：強制暫停 30 分鐘（備援機制）
                        if self.consecutive_losses[mode] >= 5:
                            self.loss_cooldown_until[mode] = time.time() + 1800
                            print(f"   ⚠️  [{self.mode_info[mode]['emoji']}] 連虧 {self.consecutive_losses[mode]} 筆，暫停 30 分鐘")
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
        
        # 🏷️ 顯示 Maker 統計
        if self.maker_enabled and time.time() - self.last_maker_stats_time >= self.maker_stats_display_interval:
            stats = self.maker_manager.stats
            maker_rate = stats.get('maker_rate', 0) * 100
            total_orders = stats.get('total_orders', 0)
            fee_saved = stats.get('total_fee_saved', 0)
            if total_orders > 0:
                print(f"🏷️ Maker: {stats['filled_as_maker']}/{total_orders} ({maker_rate:.0f}%) | 💰節省: ${fee_saved:.2f}")
            self.last_maker_stats_time = time.time()
        
        print(f"{'─'*80}\n")

        panel = self._render_liquidation_pressure_panel()
        if panel:
            print(panel)
            print()
        
        # 🆕 顯示即時爆倉瀑布面板
        cascade_panel = self._render_cascade_panel()
        if cascade_panel:
            print(cascade_panel)
            print()
        
        for mode in self.active_modes:
            strategy_info = self.mode_info[mode]
            balance = self.balances[mode]
            pnl = balance - self.initial_capital
            pnl_pct = (pnl / self.initial_capital) * 100
            
            # 統計該模式的交易
            all_orders = self.orders[mode]
            closed_orders = [o for o in all_orders if o.exit_time is not None]
            # 🆕 區分已成交持倉和 PENDING 掛單
            filled_orders = [o for o in all_orders if o.exit_time is None and not o.is_blocked and getattr(o, 'maker_status', 'FILLED') != "PENDING"]
            pending_orders = [o for o in all_orders if o.exit_time is None and not o.is_blocked and getattr(o, 'maker_status', 'FILLED') == "PENDING"]
            open_orders = filled_orders  # 兼容後續邏輯
            
            wins = len([o for o in closed_orders if o.roi > 0])
            losses = len([o for o in closed_orders if o.roi < 0])
            win_rate = (wins / len(closed_orders) * 100) if closed_orders else 0
            
            status_icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            
            print(f"{strategy_info['emoji']} {strategy_info['name']}")
            print(f"   {status_icon} 餘額: ${balance:.2f} USDT ({pnl_pct:+.2f}%)")
            pending_str = f" | ⏳掛單: {len(pending_orders)}" if pending_orders else ""
            print(f"   📊 交易: {len(closed_orders)}筆 | 勝率: {win_rate:.1f}% | 持倉: {len(open_orders)}筆{pending_str}")
            
            # 🆕 顯示 PENDING 掛單狀態
            for pos in pending_orders:
                elapsed = time.time() - pos.maker_created_time
                remaining = pos.maker_timeout_seconds - elapsed
                dir_emoji = "📈" if pos.direction == "LONG" else "📉"
                
                print(f"   ⏳ [{datetime.now().strftime('%H:%M:%S')}] 掛單中: [{strategy_info['emoji']}]")
                print(f"      {dir_emoji} {pos.direction} Maker @${pos.maker_limit_price:,.2f} | 當前: ${self.latest_price:,.2f}")
                
                # 計算與掛單價的距離
                if pos.direction == "LONG":
                    distance_pct = (self.latest_price - pos.maker_limit_price) / pos.maker_limit_price * 100
                    print(f"      📏 距離: +{distance_pct:.2f}% | ⏰ 剩餘: {remaining:.0f}s")
                else:
                    distance_pct = (pos.maker_limit_price - self.latest_price) / self.latest_price * 100
                    print(f"      📏 距離: +{distance_pct:.2f}% | ⏰ 剩餘: {remaining:.0f}s")
            
            # 顯示已成交持倉狀態
            for pos in open_orders:
                unrealized_pnl_usdt, unrealized_pnl_pct = pos.update_unrealized_pnl(self.latest_price)
                holding_seconds = (
                    datetime.fromisoformat(self.orderbook_timestamp) - 
                    datetime.fromisoformat(pos.entry_time)
                ).total_seconds()
                
                pos_icon = "🟢" if unrealized_pnl_pct > 0 else "🔴" if unrealized_pnl_pct < 0 else "⚪"
                dir_emoji = "📈" if pos.direction == "LONG" else "📉"
                
                # 🏷️ 顯示 Maker/Taker 狀態
                if getattr(pos, 'maker_status', None) == "TAKER_FALLBACK":
                    order_type = "⚡Taker(超時補單)"
                elif pos.is_maker:
                    order_type = "🏷️Maker"
                else:
                    order_type = "⚡Taker"
                
                print(f"   ✨ [{datetime.now().strftime('%H:%M:%S')}] 📊 持倉狀態: [{strategy_info['emoji']}]")
                print(f"      {dir_emoji} {pos.direction} {order_type} 💵 ${pos.position_value:.2f} / ⚡{pos.leverage}x @ ${pos.actual_entry_price:.2f}")
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

    # ==================== 🆕 爆倉瀑布即時偵測 ==================== #
    
    def _on_cascade_alert(self, alert: CascadeAlert):
        """爆倉瀑布警報回調"""
        self._last_cascade_alert = alert
        
        # 根據警報更新交易信號
        if alert.level in [CascadeLevel.SIGNIFICANT, CascadeLevel.MAJOR, CascadeLevel.EXTREME]:
            self._cascade_signal_active = True
            self._cascade_signal_strength = alert.confidence * 100
            
            # 根據爆倉方向決定信號
            if alert.direction == CascadeDirection.LONG_LIQUIDATION:
                # 多頭被爆，價格下跌
                if alert.price_change_pct < -1.0:
                    self._cascade_signal_direction = "LONG"  # 抄底
                else:
                    self._cascade_signal_direction = "SHORT"  # 順勢做空
            elif alert.direction == CascadeDirection.SHORT_LIQUIDATION:
                # 空頭被爆，價格上漲
                if alert.price_change_pct > 1.0:
                    self._cascade_signal_direction = "SHORT"  # 做空頂部
                else:
                    self._cascade_signal_direction = "LONG"  # 順勢做多
            else:
                self._cascade_signal_direction = "HOLD"
                
            # 打印警報
            self._print_cascade_alert(alert)
        else:
            self._cascade_signal_active = False
            self._cascade_signal_direction = "HOLD"
            self._cascade_signal_strength = 0.0
            
    def _on_cascade_snapshot(self, snapshot: CascadeSnapshot):
        """爆倉瀑布快照更新回調"""
        self._last_cascade_snapshot = snapshot
        
    def _print_cascade_alert(self, alert: CascadeAlert):
        """打印爆倉瀑布警報"""
        level_emoji = {
            CascadeLevel.SIGNIFICANT: "🟡",
            CascadeLevel.MAJOR: "🟠",
            CascadeLevel.EXTREME: "🔴",
        }.get(alert.level, "⚪")
        
        direction_text = {
            CascadeDirection.LONG_LIQUIDATION: "📉 多頭被爆",
            CascadeDirection.SHORT_LIQUIDATION: "📈 空頭被爆",
            CascadeDirection.MIXED: "⚡ 雙向爆倉",
        }.get(alert.direction, "")
        
        print("\n" + "=" * 60)
        print(f"{level_emoji} 💣 爆倉瀑布警報 - {alert.level.value} {level_emoji}")
        print("=" * 60)
        print(f"⏰ 時間: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"📊 方向: {direction_text}")
        print(f"💰 總爆倉: ${alert.total_usd/1e6:.2f}M")
        print(f"   🐂 多頭: ${alert.long_liq_usd/1e6:.2f}M | 🐻 空頭: ${alert.short_liq_usd/1e6:.2f}M")
        print(f"📈 價格變動: {alert.price_change_pct:+.2f}%")
        print(f"⚡ 爆倉速度: ${alert.velocity/1e3:.0f}k/秒")
        print(f"🎯 信心度: {alert.confidence*100:.0f}%")
        print(f"\n💡 {alert.recommended_action}")
        print(f"🎲 系統信號: {self._cascade_signal_direction} (強度: {self._cascade_signal_strength:.0f})")
        print("=" * 60 + "\n")
        
    def _render_cascade_panel(self) -> Optional[str]:
        """渲染爆倉瀑布面板"""
        if not LIQUIDATION_CASCADE_AVAILABLE or not self._cascade_detector:
            return None
            
        snapshot = self._last_cascade_snapshot
        if not snapshot:
            return "💣 爆倉瀑布雷達: ⏳ 等待數據..."
            
        level_bar = {
            CascadeLevel.QUIET: "[----------] 平靜",
            CascadeLevel.BUILDING: "[██--------] 醞釀中",
            CascadeLevel.MINOR: "[████------] 小型",
            CascadeLevel.SIGNIFICANT: "[██████----] 顯著 ⚠️",
            CascadeLevel.MAJOR: "[████████--] 大型 🔥",
            CascadeLevel.EXTREME: "[██████████] 極端 💥",
        }.get(snapshot.level, "[----------]")
        
        direction_text = {
            CascadeDirection.LONG_LIQUIDATION: "📉 多頭被爆",
            CascadeDirection.SHORT_LIQUIDATION: "📈 空頭被爆",
            CascadeDirection.MIXED: "⚡ 雙向爆倉",
        }.get(snapshot.direction, "")
        
        lines = [
            "💣 爆倉瀑布雷達 (Realtime Liquidation Cascade)",
            f"📊 瀑布等級: {level_bar}",
            f"➡ 方向: {direction_text}",
            f"💰 1分鐘爆倉: ${snapshot.liq_1m_total_usd/1e6:.2f}M ({snapshot.liq_1m_count}筆)",
            f"   🐂 多頭: ${snapshot.liq_1m_long_usd/1e6:.2f}M | 🐻 空頭: ${snapshot.liq_1m_short_usd/1e6:.2f}M",
            f"⚡ 爆倉速度: ${snapshot.liq_10s_velocity/1e3:.0f}k/秒",
            f"📈 價格變動: {snapshot.price_change_1m_pct:+.2f}%",
        ]
        
        if self._cascade_signal_active:
            lines.append(f"🎯 瀑布信號: {self._cascade_signal_direction} (強度: {self._cascade_signal_strength:.0f}) 🔥")
            
        return "\n".join(lines)
        
    async def _run_cascade_detector(self):
        """背景任務：運行即時爆倉瀑布偵測"""
        if not LIQUIDATION_CASCADE_AVAILABLE:
            print("⚠️ 爆倉瀑布偵測器不可用")
            return
            
        print("🔄 啟動即時爆倉瀑布偵測服務 (WebSocket)...")
        
        self._cascade_detector = LiquidationCascadeDetector(
            symbol="BTCUSDT",
            cascade_callback=self._on_cascade_alert,
            snapshot_callback=self._on_cascade_snapshot,
        )
        
        try:
            await self._cascade_detector.start()
        except Exception as e:
            print(f"⚠️ 爆倉瀑布偵測器啟動失敗: {e}")
            
    def get_cascade_signal(self) -> Dict[str, Any]:
        """獲取當前爆倉瀑布信號（供策略使用）"""
        return {
            "active": self._cascade_signal_active,
            "direction": self._cascade_signal_direction,
            "strength": self._cascade_signal_strength,
            "snapshot": self._last_cascade_snapshot.to_dict() if self._last_cascade_snapshot else None,
            "alert": self._last_cascade_alert.to_dict() if self._last_cascade_alert else None,
        }

    async def run(self):
        """運行測試"""
        # 啟動 WebSocket
        ws_task = asyncio.create_task(self.connect_websocket())
        
        # 啟動爆倉壓力更新
        asyncio.create_task(self._run_liquidation_pressure_updater())
        
        # 🆕 啟動即時爆倉瀑布偵測 (WebSocket)
        asyncio.create_task(self._run_cascade_detector())
        
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
                
                # 🆕 定期更新 Bridge (Heartbeat) - 每 10 秒
                # 確保 AI 始終獲得最新的市場微結構數據 (OBI, VPIN)
                if decision_count % 2 == 0: # loop sleep 5s, so %2 is 10s
                    # Update Wolf
                    wolf_mode = TradingMode.M_AI_WHALE_HUNTER
                    wolf_orders = [o for o in self.orders[wolf_mode] if not o.is_blocked and o.exit_time is None]
                    if wolf_orders:
                        self._update_wolf_status_to_bridge('IN_POSITION', wolf_orders[0], snapshot, is_dragon=False)
                    else:
                        self._update_wolf_status_to_bridge('IDLE', None, snapshot, is_dragon=False)
                    
                    # Update Dragon
                    dragon_mode = TradingMode.M_DRAGON
                    dragon_orders = [o for o in self.orders[dragon_mode] if not o.is_blocked and o.exit_time is None]
                    if dragon_orders:
                        self._update_wolf_status_to_bridge('IN_POSITION', dragon_orders[0], snapshot, is_dragon=True)
                    else:
                        self._update_wolf_status_to_bridge('IDLE', None, snapshot, is_dragon=True)

                # 每 30 秒列印狀態
                if time.time() - last_status_time >= 30:
                    self.print_status()
                    last_status_time = time.time()
                
                await asyncio.sleep(2)  # 🔧 v3.0: 每 2 秒檢查一次 (原 5 秒，配合 AI 5 秒判斷)
                
        except KeyboardInterrupt:
            print("\n⚠️  使用者中斷測試\n")
        except asyncio.CancelledError:
            print("\n⚠️  測試已取消\n")
        except Exception as e:
            import traceback
            print(f"\n❌ 測試錯誤: {e}\n")
            traceback.print_exc()
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
        
        # 🆕 嘗試載入 Testnet Portfolio 和 Bridge 數據
        testnet_data = {}
        bridge_data = {}
        ai_leverage_map = {}  # 🆕 儲存 AI 策略的實際槓桿
        sync_config_leverage = {}  # 🆕 從統一配置讀取預設槓桿
        
        try:
            import json
            from pathlib import Path
            project_root = Path(__file__).parent.parent
            
            # 🆕 優先讀取統一配置檔案
            sync_config_path = project_root / 'config' / 'strategy_sync_config.json'
            if sync_config_path.exists():
                with open(sync_config_path, 'r') as f:
                    sync_config = json.load(f)
                    strategies = sync_config.get('strategies', {})
                    # 建立 emoji key -> TradingMode 的槓桿對應
                    strategy_mode_map = {
                        'M🐺': TradingMode.M_AI_WHALE_HUNTER,
                        'M🐲': TradingMode.M_DRAGON,
                        'M🐲2': TradingMode.M_DRAGON2,
                        'M🦁': TradingMode.M_LION,
                        'M🦐': TradingMode.M_SHRIMP,
                        'M🐦': TradingMode.M_BIRD,
                        'M🐟': TradingMode.M_FISH_MARKET_MAKER,
                    }
                    for key, cfg in strategies.items():
                        if key in strategy_mode_map:
                            sync_config_leverage[strategy_mode_map[key]] = cfg.get('leverage', 10)
            
            # 讀取 Testnet Portfolio
            testnet_path = project_root / 'testnet_portfolio.json'
            if testnet_path.exists():
                with open(testnet_path, 'r') as f:
                    portfolio = json.load(f)
                    testnet_data = portfolio.get('strategies', {})
            
            # 🔧 讀取 Bridge 文件以獲取已實現盈虧 + 實際槓桿
            # 優先順序: position.leverage > ai_to_xxx.leverage > sync_config > MODE_CONFIGS
            bridge_configs = [
                ('ai_wolf_bridge.json', 'M🐺', TradingMode.M_AI_WHALE_HUNTER, 'ai_to_wolf', 'wolf_to_ai'),
                ('ai_dragon_bridge.json', 'M🐲', TradingMode.M_DRAGON, 'ai_to_dragon', 'dragon_to_ai'),
                ('ai_dragon2_bridge.json', 'M🐲2', TradingMode.M_DRAGON2, 'ai_to_dragon2', 'dragon_to_ai'),
                ('ai_lion_bridge.json', 'M🦁', TradingMode.M_LION, 'ai_to_lion', 'lion_to_ai'),
                ('ai_shrimp_config.json', 'M🦐', TradingMode.M_SHRIMP, 'ai_to_shrimp', 'shrimp_to_ai'),
            ]
            for bridge_file, key, mode, ai_key, pos_key in bridge_configs:
                bridge_path = project_root / bridge_file
                if bridge_path.exists():
                    with open(bridge_path, 'r') as f:
                        bridge = json.load(f)
                        fb = bridge.get('feedback_loop', {})
                        last_trade = fb.get('last_trade_result', {})
                        bridge_data[key] = {
                            'total_pnl': fb.get('total_pnl', 0),
                            'last_trade_pnl': last_trade.get('pnl_usdt', 0),
                            'total_trades': fb.get('total_trades', 0),
                            'win_rate': fb.get('win_rate', 0)
                        }
                        
                        # 🔧 優先從持倉資訊讀取實際槓桿 (這是最準確的)
                        pos_data = bridge.get(pos_key, {}).get('position', {})
                        if pos_data.get('leverage') and pos_data.get('leverage') > 0:
                            ai_leverage_map[mode] = pos_data.get('leverage')
                        else:
                            # 其次從 AI 指令讀取
                            ai_cmd = bridge.get(ai_key, {})
                            if ai_cmd.get('leverage') and ai_cmd.get('leverage') > 0:
                                ai_leverage_map[mode] = ai_cmd.get('leverage')
                        
        except Exception as e:
            pass
        
        # 計算每個模式的總資產（餘額 + 未實現盈虧）
        mode_balances = []
        for mode in self.active_modes:
            strategy_info = self.mode_info[mode]
            mode_label = f"{strategy_info['emoji']} {strategy_info['name'][:12]}"
            
            balance = self.balances[mode]
            
            # 計算未實現盈虧
            unrealized_pnl_usdt = 0
            open_orders = [o for o in self.orders[mode] if not o.is_blocked and o.exit_time is None]
            if open_orders:
                for position in open_orders:
                    unrealized_usdt, _ = position.update_unrealized_pnl(self.latest_price)
                    unrealized_pnl_usdt += unrealized_usdt
            
            total_equity = balance + unrealized_pnl_usdt
            
            mode_balances.append((mode, mode_label, balance, unrealized_pnl_usdt, total_equity, 'MODE'))
        
        # 🆕 加入 Testnet 策略 (M🐺, M🐲, M🐟) - 從 Bridge 獲取已實現盈虧
        testnet_mapping = {
            'M🐺': ('🐺T', 'M🐺 Testnet'),
            'M🐲': ('🐲T', 'M🐲 Testnet'),
            'M🐟': ('🐟T', 'M🐟 Testnet')
        }
        for key, (emoji, name) in testnet_mapping.items():
            if key in testnet_data:
                td = testnet_data[key]
                # 🔧 直接使用 Portfolio 的實際餘額，而非從 Bridge 計算
                t_balance = td.get('balance', 100.0)  # 實際餘額
                t_unrealized = td.get('unrealized_pnl', 0.0)
                t_leverage = td.get('leverage', 10)
                t_total = t_balance + t_unrealized
                mode_balances.append((None, f"{emoji} {name[:12]}", t_balance, t_unrealized, t_total, 'TESTNET', t_leverage))
        
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
        
        for i, item in enumerate(realized_ranking):
            if i >= len(rank_emojis):
                break
            rank = rank_emojis[i]
            
            # 支援 6 或 7 個元素的 tuple
            if len(item) == 7:
                mode, label, balance, unrealized, total, mode_type, leverage = item
            else:
                mode, label, balance, unrealized, total, mode_type = item
                leverage = None
            
            if mode_type == 'M_NEW':
                realized_pnl = balance - 100.0
                realized_pct = (realized_pnl / 100.0) * 100
                pnl_emoji = "🟢" if realized_pct >= 0 else "🔴" if realized_pct < 0 else "⚪"
                leverage = self.m_new_config['leverage']
            elif mode_type == 'TESTNET':
                realized_pnl = balance - 100.0
                realized_pct = (realized_pnl / 100.0) * 100
                pnl_emoji = "🟢" if realized_pct >= 0 else "🔴" if realized_pct < 0 else "⚪"
                # leverage 已經從 tuple 取得
            else:
                realized_pnl = balance - self.initial_capital
                realized_pct = (realized_pnl / self.initial_capital) * 100
                pnl_emoji = "🟢" if realized_pct >= 0 else "🔴" if realized_pct < 0 else "⚪"
                
                # 🔧 槓桿讀取優先順序: Bridge > sync_config > MODE_CONFIGS
                if mode in ai_leverage_map:
                    leverage = ai_leverage_map[mode]
                elif mode in sync_config_leverage:
                    leverage = sync_config_leverage[mode]
                else:
                    config = self.MODE_CONFIGS[mode]
                    leverage = config.leverage
            
            # Testnet 策略加上 (Testnet) 標記
            testnet_marker = " 🔗Testnet" if mode_type == 'TESTNET' else ""
            print(f"{rank} {label} | 💰 {balance:.2f} USDT ({realized_pnl:+.2f}) | {pnl_emoji} {realized_pct:+.2f}% | ⚡ {leverage}x槓桿{testnet_marker}")
        
        print("-" * 80)
        
        # ====== 排行榜 #2: 未實現損益 ======
        print("📊 [未實現損益] 排行榜 (槓桿後，扣除所有手續費):")
        print("-" * 80)
        
        # 按未實現盈虧排序
        unrealized_ranking = sorted(mode_balances, key=lambda x: x[3], reverse=True)
        
        for i, item in enumerate(unrealized_ranking):
            if i >= len(rank_emojis):
                break
            rank = rank_emojis[i]
            
            # 支援 6 或 7 個元素的 tuple
            if len(item) == 7:
                mode, label, balance, unrealized, total, mode_type, leverage = item
            else:
                mode, label, balance, unrealized, total, mode_type = item
                leverage = None
            
            if mode_type == 'M_NEW':
                leverage = self.m_new_config['leverage']
            elif mode_type == 'TESTNET':
                # leverage 已經從 tuple 取得
                pass
            else:
                # 🔧 槓桿讀取優先順序: Bridge > sync_config > MODE_CONFIGS
                if mode in ai_leverage_map:
                    leverage = ai_leverage_map[mode]
                elif mode in sync_config_leverage:
                    leverage = sync_config_leverage[mode]
                else:
                    config = self.MODE_CONFIGS[mode]
                    leverage = config.leverage
            
            # Testnet 策略加上標記
            testnet_marker = " 🔗Testnet" if mode_type == 'TESTNET' else ""
            
            if unrealized != 0:
                unrealized_pct = (unrealized / 100.0 if mode_type in ['M_NEW', 'TESTNET'] else unrealized / self.initial_capital) * 100
                pnl_emoji = "🟢" if unrealized >= 0 else "🔴" if unrealized < 0 else "⚪"
                print(f"{rank} {label} | 📊 [🌟] {unrealized:+.2f} USDT | {pnl_emoji} {unrealized_pct:+.2f}% | ⚡ {leverage}x槓桿{testnet_marker}")
            else:
                print(f"{rank} {label} | 📊 無持倉 | ⚪ 0.00% | ⚡ {leverage}x槓桿{testnet_marker}")
        
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
        
        # 確保 M_DRAGON 也在 orders 中
        if TradingMode.M_DRAGON.name not in data['orders']:
            data['orders'][TradingMode.M_DRAGON.name] = []
        
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
    
    def _get_ai_signal(self) -> dict:
        """讀取 AI Advisor 的即時信號"""
        state_file = Path("ai_advisor_state.json")
        if not state_file.exists():
            return {'action': 'WAIT', 'confidence': 0, 'reason': 'AI file missing'}
            
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
            
            # 檢查時效性 (60秒內有效，配合 AI 15s 更新頻率)
            prediction_time = state.get('prediction_time')
            if prediction_time:
                pred_dt = datetime.fromisoformat(prediction_time)
                if (datetime.now() - pred_dt).total_seconds() > 60:
                    return {'action': 'WAIT', 'confidence': 0, 'reason': 'AI signal expired'}
            
            return {
                'action': state.get('action', 'WAIT'),
                'confidence': state.get('confidence', 0),
                'reason': state.get('analysis', 'AI Decision')[:50] + '...'
            }
        except Exception as e:
            return {'action': 'WAIT', 'confidence': 0, 'reason': f'AI read error: {e}'}


# ═══════════════════════════════════════════════════════════════════════════════
# 主程式入口
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    # 預設運行時長
    duration = 8.0  # 小時
    initial_capital = 100.0  # USDT
    
    # 解析命令列參數
    if len(sys.argv) > 1:
        try:
            duration = float(sys.argv[1])
        except ValueError:
            print(f"⚠️ 無效的時長參數: {sys.argv[1]}，使用預設值 {duration} 小時")
    
    if len(sys.argv) > 2:
        try:
            initial_capital = float(sys.argv[2])
        except ValueError:
            print(f"⚠️ 無效的資金參數: {sys.argv[2]}，使用預設值 {initial_capital} USDT")
    
    print("\n" + "=" * 60)
    print("🚀 Paper Trading Hybrid Full (純模擬版)")
    print("=" * 60)
    print(f"   運行時長: {duration} 小時")
    print(f"   每策略資金: {initial_capital} USDT")
    print(f"   ⚠️ 此版本不連接 Testnet，純粹 Paper Trading")
    print("=" * 60 + "\n")
    
    # 創建並運行系統
    system = HybridPaperTradingSystem(
        initial_capital=initial_capital,
        max_position_pct=0.5,
        test_duration_hours=duration
    )
    
    try:
        import asyncio
        asyncio.run(system.run())
    except KeyboardInterrupt:
        print("\n\n⏹️ 用戶中斷...")
        system.print_summary()
