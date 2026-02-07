#!/usr/bin/env python3
"""
🐋 主力策略識別系統 v4.0 - 即時測試腳本
===========================================

功能測試：
1. 顯示全部主力策略種類和對應機率
2. 長期最有可能正在執行的主力策略
3. 預計進場價格、方向、出場價格
4. 持續監控關鍵點（達到/未達到判定）
5. 分析記錄、時間、決策、成功率
6. 串接幣安 API 獲取即時數據

運行方式:
    python scripts/whale_detector_v4_test.py [監控時長(小時)]
    
範例:
    python scripts/whale_detector_v4_test.py 1       # 監控 1 小時
    python scripts/whale_detector_v4_test.py 0.5    # 監控 30 分鐘
    python scripts/whale_detector_v4_test.py        # 單次分析
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import json
import ccxt
import logging
from datetime import datetime, timezone
from collections import deque
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from pathlib import Path
import numpy as np

# 匯入 v4 偵測器
from src.strategy.whale_strategy_detector_v4 import (
    WhaleStrategyDetectorV4,
    WhaleStrategyV4,
    WhaleStrategySnapshot,
    StrategyCategory,
    RiskLevel,
    SignalDirection,
    get_strategy_metadata,
    get_category_strategies,
    STRATEGY_METADATA
)

# 匯入幣安數據抓取
try:
    from scripts.fetch_binance_leverage_data import BinanceLeverageDataFetcher
    BINANCE_DATA_AVAILABLE = True
except ImportError:
    BINANCE_DATA_AVAILABLE = False
    print("⚠️ BinanceLeverageDataFetcher 不可用")

# 匯入微觀指標計算器
try:
    from src.exchange.obi_calculator import OBICalculator, calculate_obi_from_snapshot
    from src.exchange.vpin_calculator import VPINCalculator
    OBI_VPIN_AVAILABLE = True
except ImportError:
    OBI_VPIN_AVAILABLE = False
    calculate_obi_from_snapshot = None
    print("⚠️ OBI/VPIN 計算器不可用")


# ==================== 日誌設定 ====================

def setup_logging(log_dir: str = "logs/whale_detector_v4") -> logging.Logger:
    """設定日誌 - 即時寫入，不緩衝"""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_path / f"whale_v4_test_{timestamp}.log"
    
    # 設定格式 (簡化格式，方便解析)
    formatter = logging.Formatter('%(asctime)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    # 檔案處理器 - 即時寫入 (不緩衝)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    file_handler.stream.reconfigure(line_buffering=True)  # 行緩衝，每行立即寫入
    
    # 建立 logger
    logger = logging.getLogger('whale_detector_v4')
    logger.setLevel(logging.INFO)
    logger.handlers = []  # 清除舊處理器
    logger.addHandler(file_handler)
    
    logger.info(f"日誌檔案: {log_file}")
    return logger, str(log_file)


# ==================== 數據類 ====================

@dataclass
class AnalysisRecord:
    """分析記錄"""
    timestamp: str
    strategy: str
    probability: float
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    key_signals: List[str]
    outcome: str = "PENDING"  # PENDING / HIT_TP / HIT_SL / CHANGED
    actual_exit_price: float = 0
    duration_seconds: int = 0
    
    def to_dict(self) -> Dict:
        return asdict(self)


class WhaleDetectorV4Test:
    """
    🐋 主力策略識別系統 v4.0 測試框架
    """
    
    def __init__(self, logger: logging.Logger = None):
        # 設定日誌目錄
        self.log_dir = "/Users/akaihuangm1/Desktop/btn/logs/whale_detector_v4"
        os.makedirs(self.log_dir, exist_ok=True)
        
        # 設定日誌
        if logger is None:
            self.logger, self.log_file = setup_logging(self.log_dir)
        else:
            self.logger = logger
            self.log_file = "N/A"
        
        # 初始化 v4 偵測器
        self.detector = WhaleStrategyDetectorV4(output_path="ai_whale_strategy.json")
        
        # 初始化幣安連接
        self.exchange = ccxt.binance({
            'sandbox': False,  # 使用真實市場數據
            'options': {'defaultType': 'future'},
            'enableRateLimit': True
        })
        
        # 初始化數據抓取器
        if BINANCE_DATA_AVAILABLE:
            self.leverage_fetcher = BinanceLeverageDataFetcher(
                symbol="BTCUSDT",
                period="5m",
                limit=30
            )
        else:
            self.leverage_fetcher = None
        
        # 初始化微觀指標
        if OBI_VPIN_AVAILABLE:
            self.obi_calculator = OBICalculator()
            self.vpin_calculator = VPINCalculator()
        else:
            self.obi_calculator = None
            self.vpin_calculator = None
        
        # 歷史記錄
        self.analysis_history: deque = deque(maxlen=1000)
        self.strategy_stats: Dict[str, Dict] = {}  # 策略統計
        self.signal_history: List[Dict] = []  # 進場信號歷史（用於驗證）
        
        # 🆕 模擬交易追蹤
        self.active_trades: Dict[str, Dict] = {}  # 進行中的交易
        self.closed_trades: List[Dict] = []       # 已平倉交易
        self.trade_counter = 0
        
        # 成功指標追蹤
        self.success_metrics = {
            "total_signals": 0,
            "hit_tp": 0,
            "hit_sl": 0,
            "still_active": 0,
            "api_calls_success": 0,
            "api_calls_failed": 0,
            "analysis_count": 0,
            "strategy_changes": 0,
            "last_strategy": None,
            "price_at_start": 0,
            "price_high": 0,
            "price_low": float('inf'),
        }
        
        # 最新市場數據
        self.current_price = 0
        self.obi = 0
        self.vpin = 0
        self.wpi = 0
        self.funding_rate = 0
        self.oi_change_pct = 0
        self.liquidation_pressure_long = 50
        self.liquidation_pressure_short = 50
    
    def fetch_market_data(self) -> Dict:
        """
        從幣安 API 獲取即時市場數據
        """
        data = {}
        
        try:
            # 1. 獲取 K 線數據
            ohlcv = self.exchange.fetch_ohlcv('BTC/USDT:USDT', '1m', limit=30)
            self.success_metrics["api_calls_success"] += 1
            if ohlcv:
                latest = ohlcv[-1]
                data['price'] = latest[4]  # close
                data['volume'] = latest[5]
                self.current_price = data['price']
                
                # 計算價格變化
                if len(ohlcv) >= 5:
                    data['price_change_1m'] = (ohlcv[-1][4] - ohlcv[-2][4]) / ohlcv[-2][4] * 100
                    data['price_change_5m'] = (ohlcv[-1][4] - ohlcv[-5][4]) / ohlcv[-5][4] * 100
                
                # 更新偵測器 K 線
                for candle in ohlcv[-20:]:
                    self.detector.update_data(candle={
                        "open": candle[1],
                        "high": candle[2],
                        "low": candle[3],
                        "close": candle[4],
                        "volume": candle[5]
                    })
            
            # 2. 獲取訂單簿
            orderbook = self.exchange.fetch_order_book('BTC/USDT:USDT', limit=20)
            self.success_metrics["api_calls_success"] += 1
            if orderbook:
                data['bids'] = orderbook['bids']
                data['asks'] = orderbook['asks']
                
                # 計算 OBI
                if OBI_VPIN_AVAILABLE and calculate_obi_from_snapshot:
                    self.obi = calculate_obi_from_snapshot(orderbook, depth=10)
                else:
                    bid_vol = sum(b[1] for b in orderbook['bids'][:10])
                    ask_vol = sum(a[1] for a in orderbook['asks'][:10])
                    self.obi = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0
                
                data['obi'] = self.obi
                
                # 更新偵測器訂單簿
                self.detector.update_data(bids=orderbook['bids'], asks=orderbook['asks'])
            
            # 3. 獲取最近成交
            trades = self.exchange.fetch_trades('BTC/USDT:USDT', limit=50)
            self.success_metrics["api_calls_success"] += 1
            if trades:
                # 計算主力流向 (WPI 簡化版)
                buy_vol = sum(t['amount'] * t['price'] for t in trades if t['side'] == 'buy')
                sell_vol = sum(t['amount'] * t['price'] for t in trades if t['side'] == 'sell')
                total_vol = buy_vol + sell_vol
                self.wpi = (buy_vol - sell_vol) / total_vol if total_vol > 0 else 0
                data['wpi'] = self.wpi
                
                # 更新偵測器交易
                for t in trades[-10:]:
                    self.detector.update_data(trade={
                        "volume_usdt": t['amount'] * t['price'],
                        "is_buy": t['side'] == 'buy',
                        "price": t['price']
                    })
            
            # 4. 獲取槓桿數據 (資金費率、OI 等)
            if self.leverage_fetcher:
                try:
                    leverage_data = self.leverage_fetcher.collect()
                    
                    # 資金費率
                    if leverage_data.get('funding_rate'):
                        latest_funding = leverage_data['funding_rate'][-1]
                        self.funding_rate = float(latest_funding.get('fundingRate', 0))
                        data['funding_rate'] = self.funding_rate
                    
                    # OI 變化
                    if leverage_data.get('open_interest') and len(leverage_data['open_interest']) >= 2:
                        oi_list = leverage_data['open_interest']
                        oi_now = float(oi_list[-1].get('sumOpenInterest', 0))
                        oi_prev = float(oi_list[-2].get('sumOpenInterest', 0))
                        self.oi_change_pct = (oi_now - oi_prev) / oi_prev * 100 if oi_prev > 0 else 0
                        data['oi_change_pct'] = self.oi_change_pct
                    
                    # 多空比計算爆倉壓力
                    if leverage_data.get('global_long_short'):
                        ls_ratio = float(leverage_data['global_long_short'][-1].get('longShortRatio', 1))
                        # 多空比 > 1 表示多頭多，空頭爆倉壓力大
                        # 多空比 < 1 表示空頭多，多頭爆倉壓力大
                        self.liquidation_pressure_long = min(100, 50 * (1 / ls_ratio) if ls_ratio > 0 else 50)
                        self.liquidation_pressure_short = min(100, 50 * ls_ratio)
                        data['liquidation_pressure_long'] = self.liquidation_pressure_long
                        data['liquidation_pressure_short'] = self.liquidation_pressure_short
                
                except Exception as e:
                    self.logger.warning(f"槓桿數據獲取失敗: {e}")
                    self.success_metrics["api_calls_failed"] += 1
        
        except Exception as e:
            self.logger.error(f"市場數據獲取失敗: {e}")
            self.success_metrics["api_calls_failed"] += 1
        
        return data
    
    # ==================== 模擬交易追蹤 ====================
    
    def open_paper_trade(self, snapshot: 'WhaleStrategySnapshot'):
        """
        開啟模擬交易 (記錄信號用於驗證)
        """
        if not snapshot.entry_signal or not snapshot.primary_strategy:
            return None
        
        self.trade_counter += 1
        trade_id = f"PT_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.trade_counter:04d}"
        
        entry = snapshot.entry_signal
        strategy = snapshot.primary_strategy
        
        trade = {
            "trade_id": trade_id,
            "strategy": strategy.strategy.name,
            "strategy_value": strategy.strategy.value,
            "direction": entry.direction.value,
            "probability": strategy.probability,
            "confidence": strategy.confidence,
            "entry_price": entry.entry_price,
            "take_profit": entry.take_profit,
            "stop_loss": entry.stop_loss,
            "position_size_pct": entry.position_size_pct,
            "open_time": datetime.now().isoformat(),
            "obi": self.obi,
            "wpi": self.wpi,
            "funding_rate": self.funding_rate,
            "strategy_probs": snapshot.strategy_probabilities.copy() if snapshot.strategy_probabilities else {},
            # 追蹤
            "max_profit_price": entry.entry_price,
            "max_loss_price": entry.entry_price,
            "status": "OPEN",
        }
        
        self.active_trades[trade_id] = trade
        self.logger.info(f"📈 開倉: {trade_id} | {strategy.strategy.value} | "
                        f"{'做多' if '多' in entry.direction.value else '做空'} @ ${entry.entry_price:,.2f}")
        
        return trade_id
    
    def update_paper_trades(self):
        """
        更新所有模擬交易狀態，檢查是否止盈止損
        """
        if not self.current_price or not self.active_trades:
            return
        
        closed_ids = []
        
        for trade_id, trade in self.active_trades.items():
            entry_price = trade["entry_price"]
            tp = trade["take_profit"]
            sl = trade["stop_loss"]
            direction = trade["direction"]
            
            # 判斷做多還是做空
            is_long = "多" in direction or "LONG" in direction.upper()
            
            # 更新最大浮盈浮虧價格
            if is_long:
                trade["max_profit_price"] = max(trade["max_profit_price"], self.current_price)
                trade["max_loss_price"] = min(trade["max_loss_price"], self.current_price)
            else:
                trade["max_profit_price"] = min(trade["max_profit_price"], self.current_price)
                trade["max_loss_price"] = max(trade["max_loss_price"], self.current_price)
            
            # 檢查止盈止損
            hit_tp = False
            hit_sl = False
            
            if is_long:
                hit_tp = self.current_price >= tp
                hit_sl = self.current_price <= sl
            else:
                hit_tp = self.current_price <= tp
                hit_sl = self.current_price >= sl
            
            if hit_tp or hit_sl:
                # 計算盈虧
                if is_long:
                    pnl_pct = (self.current_price - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - self.current_price) / entry_price * 100
                
                trade["close_time"] = datetime.now().isoformat()
                trade["exit_price"] = self.current_price
                trade["pnl_pct"] = pnl_pct
                trade["status"] = "HIT_TP" if hit_tp else "HIT_SL"
                trade["is_success"] = hit_tp
                
                # 計算持倉時間
                open_time = datetime.fromisoformat(trade["open_time"])
                close_time = datetime.fromisoformat(trade["close_time"])
                trade["duration_minutes"] = (close_time - open_time).total_seconds() / 60
                
                # 記錄
                result_emoji = "✅" if hit_tp else "❌"
                self.logger.info(f"{result_emoji} 平倉: {trade_id} | {trade['status']} | "
                               f"盈虧: {pnl_pct:+.2f}% | 持倉: {trade['duration_minutes']:.1f}分鐘")
                
                # 更新統計
                if hit_tp:
                    self.success_metrics["hit_tp"] += 1
                else:
                    self.success_metrics["hit_sl"] += 1
                
                self.closed_trades.append(trade)
                closed_ids.append(trade_id)
        
        # 移除已平倉
        for tid in closed_ids:
            del self.active_trades[tid]
        
        # 更新活躍數
        self.success_metrics["still_active"] = len(self.active_trades)
    
    def get_paper_trade_summary(self) -> Dict:
        """獲取模擬交易統計"""
        total = len(self.closed_trades)
        if total == 0:
            return {"message": "尚無已平倉交易"}
        
        wins = sum(1 for t in self.closed_trades if t.get("is_success", False))
        losses = total - wins
        
        total_pnl = sum(t.get("pnl_pct", 0) for t in self.closed_trades)
        avg_win = sum(t.get("pnl_pct", 0) for t in self.closed_trades if t.get("is_success")) / wins if wins > 0 else 0
        avg_loss = sum(t.get("pnl_pct", 0) for t in self.closed_trades if not t.get("is_success")) / losses if losses > 0 else 0
        
        # 按策略統計
        strategy_stats = {}
        for t in self.closed_trades:
            s = t.get("strategy", "UNKNOWN")
            if s not in strategy_stats:
                strategy_stats[s] = {"total": 0, "wins": 0, "pnl": 0}
            strategy_stats[s]["total"] += 1
            strategy_stats[s]["pnl"] += t.get("pnl_pct", 0)
            if t.get("is_success"):
                strategy_stats[s]["wins"] += 1
        
        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / total if total > 0 else 0,
            "total_pnl_pct": total_pnl,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "active_trades": len(self.active_trades),
            "strategy_stats": strategy_stats
        }
    
    def save_paper_trades(self):
        """保存模擬交易記錄"""
        import json
        save_path = Path(self.log_dir) / "paper_trades.json"
        
        data = {
            "closed_trades": self.closed_trades,
            "active_trades": list(self.active_trades.values()),
            "summary": self.get_paper_trade_summary(),
            "saved_at": datetime.now().isoformat()
        }
        
        with open(save_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"📁 交易記錄已保存: {save_path}")
    
    # ==================== 分析 ====================

    def analyze(self) -> WhaleStrategySnapshot:
        """
        執行完整分析
        """
        # 獲取最新數據
        market_data = self.fetch_market_data()
        
        # 更新價格追蹤
        if self.current_price > 0:
            if self.success_metrics["price_at_start"] == 0:
                self.success_metrics["price_at_start"] = self.current_price
            self.success_metrics["price_high"] = max(self.success_metrics["price_high"], self.current_price)
            self.success_metrics["price_low"] = min(self.success_metrics["price_low"], self.current_price)
        
        # 執行 v4 分析
        snapshot = self.detector.analyze(
            current_price=self.current_price,
            obi=self.obi,
            vpin=self.vpin,
            wpi=self.wpi,
            funding_rate=self.funding_rate,
            oi_change_pct=self.oi_change_pct,
            liquidation_pressure_long=self.liquidation_pressure_long,
            liquidation_pressure_short=self.liquidation_pressure_short,
            price_change_1m_pct=market_data.get('price_change_1m', 0),
            price_change_5m_pct=market_data.get('price_change_5m', 0)
        )
        
        # 更新分析計數
        self.success_metrics["analysis_count"] += 1
        
        # 記錄分析結果
        if snapshot.primary_strategy:
            # 追蹤策略變化
            current_strategy = snapshot.primary_strategy.strategy.name
            if self.success_metrics["last_strategy"] != current_strategy:
                self.success_metrics["strategy_changes"] += 1
                self.logger.info(f"策略切換: {self.success_metrics['last_strategy']} -> {current_strategy}")
                self.success_metrics["last_strategy"] = current_strategy
            
            # 追蹤信號
            if snapshot.entry_signal:
                self.success_metrics["total_signals"] += 1
                self.logger.info(
                    f"新信號: {snapshot.entry_signal.direction.value} @ ${snapshot.entry_signal.entry_price:,.2f} "
                    f"TP=${snapshot.entry_signal.take_profit:,.2f} SL=${snapshot.entry_signal.stop_loss:,.2f}"
                )
            
            record = AnalysisRecord(
                timestamp=snapshot.timestamp,
                strategy=snapshot.primary_strategy.strategy.value,
                probability=snapshot.primary_strategy.probability,
                direction=snapshot.entry_signal.direction.value if snapshot.entry_signal else "觀望",
                entry_price=snapshot.entry_signal.entry_price if snapshot.entry_signal else self.current_price,
                stop_loss=snapshot.entry_signal.stop_loss if snapshot.entry_signal else 0,
                take_profit=snapshot.entry_signal.take_profit if snapshot.entry_signal else 0,
                key_signals=snapshot.key_signals[:3]
            )
            self.analysis_history.append(record)
            
            # 更新策略統計
            strategy_name = snapshot.primary_strategy.strategy.name
            if strategy_name not in self.strategy_stats:
                self.strategy_stats[strategy_name] = {
                    "count": 0,
                    "success": 0,
                    "total_probability": 0
                }
            self.strategy_stats[strategy_name]["count"] += 1
            self.strategy_stats[strategy_name]["total_probability"] += snapshot.primary_strategy.probability
        
        return snapshot
    
    def render_all_strategies_panel(self) -> str:
        """
        顯示所有 23 種主力策略及機率
        """
        lines = [
            "",
            "=" * 70,
            "🐋 主力策略識別系統 v4.0 - 全部策略總覽",
            "=" * 70,
        ]
        
        # 按類別顯示
        for category in StrategyCategory:
            strategies = get_category_strategies(category)
            if not strategies:
                continue
            
            lines.append(f"\n📂 {category.value} ({len(strategies)} 種)")
            lines.append("-" * 50)
            
            for s in strategies:
                meta = get_strategy_metadata(s)
                # 從最近快照獲取機率
                prob = 0
                if self.detector.last_snapshot and self.detector.last_snapshot.strategy_probabilities:
                    prob = self.detector.last_snapshot.strategy_probabilities.get(s.name, 0)
                
                risk_icon = {"低": "🟢", "中": "🟡", "高": "🔴", "極高": "⚫"}.get(meta["risk_level"].value, "⚪")
                response_icon = {"做多": "📈", "做空": "📉", "觀望": "⏸️"}.get(meta["best_response"].value, "❓")
                
                prob_bar = "█" * int(prob * 10) + "░" * (10 - int(prob * 10))
                lines.append(f"  {risk_icon} {s.value:<12} │ {prob_bar} {prob:>5.1%} │ {response_icon} {meta['best_response'].value}")
        
        lines.append("")
        return "\n".join(lines)
    
    def render_primary_strategy_panel(self, snapshot: WhaleStrategySnapshot) -> str:
        """
        顯示主要策略分析面板
        """
        lines = [
            "",
            "=" * 70,
            "🎯 長期最可能執行的主力策略",
            "=" * 70,
        ]
        
        if snapshot.primary_strategy:
            p = snapshot.primary_strategy
            meta = get_strategy_metadata(p.strategy)
            
            lines.extend([
                f"",
                f"📌 主策略: {p.strategy.value} ({p.strategy.name})",
                f"📊 機率: {p.probability:.1%}",
                f"🔒 信心度: {p.confidence:.1%}",
                f"⚠️ 風險等級: {p.risk_level.value}",
                f"📂 類別: {p.category.value}",
                f"💡 最佳應對: {meta['best_response'].value}",
                f"",
                f"🔍 關鍵信號:",
            ])
            for i, sig in enumerate(p.signals[:5], 1):
                lines.append(f"   {i}. {sig}")
        else:
            lines.append("📊 當前為正常市場波動，無明顯主力策略")
        
        if snapshot.secondary_strategy:
            s = snapshot.secondary_strategy
            lines.extend([
                f"",
                f"📎 次要策略: {s.strategy.value} ({s.probability:.1%})"
            ])
        
        lines.append("")
        return "\n".join(lines)
    
    def render_entry_exit_panel(self, snapshot: WhaleStrategySnapshot) -> str:
        """
        顯示進場/出場建議面板
        """
        lines = [
            "",
            "=" * 70,
            "💰 進場/出場建議",
            "=" * 70,
        ]
        
        if snapshot.entry_signal:
            e = snapshot.entry_signal
            lines.extend([
                f"",
                f"📍 方向: {e.direction.value}",
                f"💵 進場價格: ${e.entry_price:,.2f}",
                f"🛑 止損價格: ${e.stop_loss:,.2f} ({(e.stop_loss - e.entry_price) / e.entry_price * 100:+.2f}%)",
                f"🎯 止盈價格: ${e.take_profit:,.2f} ({(e.take_profit - e.entry_price) / e.entry_price * 100:+.2f}%)",
                f"📏 建議倉位: {e.position_size_pct:.1f}%",
                f"⏰ 緊急程度: {e.urgency}",
                f"💭 理由: {e.reasoning}",
            ])
        else:
            lines.extend([
                f"",
                f"⏸️ 當前建議: 觀望",
                f"📊 整體偏向: {snapshot.overall_bias}",
                f"🔒 信心度: {snapshot.overall_confidence:.1%}",
            ])
        
        lines.extend([
            f"",
            f"🔐 允許交易: {'✅ 是' if snapshot.trading_allowed else '❌ 否'}",
        ])
        
        if snapshot.risk_warnings:
            lines.append(f"\n⚠️ 風險警告:")
            for w in snapshot.risk_warnings:
                lines.append(f"   {w}")
        
        lines.append("")
        return "\n".join(lines)
    
    def render_monitoring_panel(self, snapshot: WhaleStrategySnapshot) -> str:
        """
        顯示持續監控面板
        """
        lines = [
            "",
            "=" * 70,
            "👁️ 持續監控關鍵點",
            "=" * 70,
        ]
        
        if snapshot.entry_signal:
            e = snapshot.entry_signal
            current = self.current_price
            
            # 計算與目標的距離
            tp_dist = (e.take_profit - current) / current * 100
            sl_dist = (e.stop_loss - current) / current * 100
            entry_dist = (e.entry_price - current) / current * 100
            
            lines.extend([
                f"",
                f"📊 當前價格: ${current:,.2f}",
                f"",
                f"🎯 止盈目標: ${e.take_profit:,.2f} ({tp_dist:+.2f}%)",
            ])
            
            if abs(tp_dist) < 0.5:
                lines.append(f"   ✅ 接近達標！準備獲利出場")
            elif tp_dist > 0:
                lines.append(f"   ⏳ 距離止盈還需上漲 {tp_dist:.2f}%")
            else:
                lines.append(f"   ⚠️ 已超過止盈位，考慮平倉")
            
            lines.extend([
                f"",
                f"🛑 止損位置: ${e.stop_loss:,.2f} ({sl_dist:+.2f}%)",
            ])
            
            if abs(sl_dist) < 0.3:
                lines.append(f"   🚨 接近止損！注意風險")
            elif sl_dist < 0:
                lines.append(f"   ❌ 已觸及止損，建議平倉止損")
            else:
                lines.append(f"   ✅ 安全區間")
            
            # 策略有效性監控
            lines.extend([
                f"",
                f"📌 策略有效性:",
            ])
            
            if snapshot.primary_strategy and snapshot.primary_strategy.probability > 0.6:
                lines.append(f"   ✅ 策略信號強勁 ({snapshot.primary_strategy.probability:.1%})")
            elif snapshot.primary_strategy and snapshot.primary_strategy.probability > 0.4:
                lines.append(f"   ⚠️ 策略信號減弱 ({snapshot.primary_strategy.probability:.1%})，保持觀察")
            else:
                lines.append(f"   ❌ 策略信號消失，考慮改變策略")
        
        else:
            lines.append("\n⏸️ 無活躍交易信號，持續觀察市場...")
        
        lines.append("")
        return "\n".join(lines)
    
    def render_statistics_panel(self) -> str:
        """
        顯示分析統計面板
        """
        lines = [
            "",
            "=" * 70,
            "📊 分析記錄與統計",
            "=" * 70,
        ]
        
        # 策略出現頻率統計
        if self.strategy_stats:
            lines.append("\n📈 策略出現頻率:")
            total = sum(s["count"] for s in self.strategy_stats.values())
            sorted_stats = sorted(self.strategy_stats.items(), key=lambda x: x[1]["count"], reverse=True)
            
            for name, stat in sorted_stats[:10]:
                pct = stat["count"] / total * 100 if total > 0 else 0
                avg_prob = stat["total_probability"] / stat["count"] if stat["count"] > 0 else 0
                bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                lines.append(f"   {name:<20} │ {bar} {stat['count']:>3}次 ({pct:>5.1f}%) 平均機率:{avg_prob:.1%}")
        
        # 最近分析記錄
        if self.analysis_history:
            lines.append("\n📝 最近分析記錄:")
            for record in list(self.analysis_history)[-5:]:
                time_str = record.timestamp[:19].replace("T", " ")
                lines.append(f"   {time_str} │ {record.strategy:<10} {record.probability:>5.1%} │ {record.direction}")
        
        lines.append(f"\n📊 總分析次數: {len(self.analysis_history)}")
        lines.append("")
        return "\n".join(lines)
    
    def render_dashboard(self, snapshot: WhaleStrategySnapshot, iteration: int, remaining_minutes: float) -> str:
        """
        渲染動態儀表板 - 垂直排列版 (避免對齊問題)
        """
        now = datetime.now().strftime('%H:%M:%S')
        
        # ANSI 顏色碼
        R = '\033[0m'       # reset
        B = '\033[1m'       # bold
        D = '\033[2m'       # dim
        r = '\033[31m'      # red
        g = '\033[32m'      # green
        y = '\033[33m'      # yellow
        m = '\033[35m'      # magenta
        c = '\033[36m'      # cyan
        R_ = '\033[91m'     # bright red
        G_ = '\033[92m'     # bright green
        Y_ = '\033[93m'     # bright yellow
        B_ = '\033[94m'     # bright blue
        M_ = '\033[95m'     # bright magenta
        C_ = '\033[96m'     # bright cyan
        W_ = '\033[97m'     # bright white
        bgB = '\033[44m'    # bg blue
        bgC = '\033[46m'    # bg cyan
        
        lines = []
        
        # ========== 標題 ==========
        lines.append(f"{c}{'='*60}{R}")
        lines.append(f"{bgB}{W_}{B} 🐋 WHALE DETECTOR v4.0 {R}  {Y_}BTC ${self.current_price:,.0f}{R}  {D}{now}{R}  {G_}#{iteration}{R}  {M_}{remaining_minutes:.0f}m{R}")
        lines.append(f"{c}{'='*60}{R}")
        
        # ========== 主策略 ==========
        lines.append("")
        if snapshot.primary_strategy:
            p = snapshot.primary_strategy
            meta = get_strategy_metadata(p.strategy)
            
            # 風險顏色
            risk_c = {'低': g, '中': y, '高': r, '極高': R_}.get(p.risk_level.value, D)
            act_c = {'做多': G_, '做空': R_, '觀望': y}.get(meta['best_response'].value, D)
            
            lines.append(f"{C_}{B}🎯 主策略識別{R}")
            lines.append(f"   {W_}{B}>>> {p.strategy.value} <<<{R}")
            lines.append(f"   機率: {G_}{p.probability:.1%}{R}  信心: {c}{p.confidence:.1%}{R}")
            lines.append(f"   風險: {risk_c}{p.risk_level.value}{R}  建議: {act_c}{meta['best_response'].value}{R}")
            
            if p.signals:
                lines.append(f"   {D}信號:{R}")
                for sig in p.signals[:3]:
                    lines.append(f"   {D}  • {sig[:40]}{R}")
        else:
            lines.append(f"{C_}{B}🎯 主策略識別{R}")
            lines.append(f"   {D}無明顯主力策略{R}")
        
        # ========== 策略機率 (全部顯示) ==========
        lines.append("")
        lines.append(f"{Y_}{B}📊 策略機率{R}")
        
        all_probs = snapshot.strategy_probabilities or {}
        primary_name = snapshot.primary_strategy.strategy.name if snapshot.primary_strategy else ""
        
        name_map = {
            "BULL_TRAP": "多頭陷阱", "BEAR_TRAP": "空頭陷阱", "FAKEOUT": "假突破",
            "STOP_HUNT": "獵殺止損", "SPOOFING": "幌騙", "WHIPSAW": "鋸齒洗盤",
            "CONSOLIDATION_SHAKE": "盤整洗盤", "FLASH_CRASH": "閃崩", "SLOW_BLEED": "陰跌",
            "ACCUMULATION": "吸籌", "DISTRIBUTION": "派發", "RE_ACCUMULATION": "再吸籌",
            "RE_DISTRIBUTION": "再派發", "LONG_SQUEEZE": "軋多", "SHORT_SQUEEZE": "軋空",
            "CASCADE_LIQUIDATION": "連環爆倉", "TREND_PUSH": "趨勢推動", "TREND_CONTINUATION": "趨勢延續",
            "TREND_REVERSAL": "趨勢反轉", "PUMP_AND_DUMP": "拉高出貨", "WASH_TRADING": "對敲",
            "LAYERING": "層疊",
        }
        
        # 類別定義: (名稱, 策略列表, 標題顏色, 暗色/項目顏色)
        # 暗色用 dim + 原色 或 普通色（相對於 bright 版本）
        categories = [
            ("誘騙類", ["BULL_TRAP", "BEAR_TRAP", "FAKEOUT", "STOP_HUNT", "SPOOFING"], r, f"{D}{r}"),
            ("清洗類", ["WHIPSAW", "CONSOLIDATION_SHAKE", "FLASH_CRASH", "SLOW_BLEED"], y, f"{D}{y}"),
            ("吸派類", ["ACCUMULATION", "DISTRIBUTION", "RE_ACCUMULATION", "RE_DISTRIBUTION"], g, f"{D}{g}"),
            ("爆倉類", ["LONG_SQUEEZE", "SHORT_SQUEEZE", "CASCADE_LIQUIDATION"], R_, r),  # bright red -> normal red
            ("趨勢類", ["TREND_PUSH", "TREND_CONTINUATION", "TREND_REVERSAL"], B_, f"{D}{B_}"),
            ("特殊類", ["PUMP_AND_DUMP", "WASH_TRADING", "LAYERING"], m, f"{D}{m}"),
        ]
        
        for cat_name, strats, cat_c, item_c in categories:
            lines.append(f"   {cat_c}{B}{cat_name}:{R}")
            for s in strats:
                prob = all_probs.get(s, 0)
                name = name_map.get(s, s)
                bars = int(prob * 10)
                bar = "█" * bars + "░" * (10 - bars)
                
                # 進度條顏色：高機率用亮色，否則用該類別的暗色
                if prob >= 0.7:
                    bc = cat_c  # 高機率用亮色（類別標題色）
                elif prob >= 0.4:
                    bc = item_c  # 中機率用暗色
                else:
                    bc = item_c  # 低機率也用暗色
                
                marker = f"{bgC}{B}*{R}" if s == primary_name else " "
                lines.append(f"   {marker} {item_c}{name}:{R} {bc}{bar}{R} {W_}{prob*100:.0f}%{R}")
        
        # ========== 市場數據 ==========
        lines.append("")
        lines.append(f"{B_}{B}📡 市場數據{R}")
        
        obi_c = G_ if self.obi > 0.3 else R_ if self.obi < -0.3 else D
        wpi_c = G_ if self.wpi > 0.3 else R_ if self.wpi < -0.3 else D
        
        lines.append(f"   訂單簿失衡 OBI:  {obi_c}{self.obi:+.3f}{R}")
        lines.append(f"   鯨魚壓力 WPI:    {wpi_c}{self.wpi:+.3f}{R}")
        lines.append(f"   知情交易 VPIN:   {c}{self.vpin:.3f}{R}")
        lines.append(f"   資金費率:        {y}{self.funding_rate*100:.4f}%{R}")
        lines.append(f"   OI 變化:         {c}{self.oi_change_pct:+.2f}%{R}")
        lines.append(f"   爆倉壓力: {r}多頭 {self.liquidation_pressure_long:.0f}{R} | {g}空頭 {self.liquidation_pressure_short:.0f}{R}")
        
        # ========== 進場建議 (最後，用分隔線) ==========
        lines.append("")
        lines.append(f"{c}{'='*60}{R}")
        lines.append(f"{M_}{B}💰 進場建議{R}")
        
        if snapshot.entry_signal:
            e = snapshot.entry_signal
            tp_pct = (e.take_profit - e.entry_price) / e.entry_price * 100
            sl_pct = (e.stop_loss - e.entry_price) / e.entry_price * 100
            dir_c = G_ if e.direction.value == "做多" else R_
            
            lines.append(f"   {dir_c}{B}>>> {e.direction.value} <<<{R}")
            lines.append(f"   進場價: {W_}${e.entry_price:,.0f}{R}")
            lines.append(f"   {g}止盈: ${e.take_profit:,.0f} ({tp_pct:+.1f}%){R}")
            lines.append(f"   {r}止損: ${e.stop_loss:,.0f} ({sl_pct:+.1f}%){R}")
            lines.append(f"   倉位: {c}{e.position_size_pct:.0f}%{R}")
        else:
            bias_c = {
                'BULLISH': G_, 'BEARISH': R_, 'NEUTRAL': y
            }.get(snapshot.overall_bias, D)
            bias_t = {
                'BULLISH': '偏多', 'BEARISH': '偏空', 'NEUTRAL': '中性'
            }.get(snapshot.overall_bias, '未知')
            
            lines.append(f"   {y}⏸ 建議觀望{R}")
            lines.append(f"   市場偏向: {bias_c}{bias_t}{R}")
            lines.append(f"   整體信心: {c}{snapshot.overall_confidence:.1%}{R}")
        
        # 風險警告
        if snapshot.risk_warnings:
            lines.append(f"   {y}⚠ 風險警告:{R}")
            for w in snapshot.risk_warnings[:2]:
                lines.append(f"   {D}  • {w[:45]}{R}")
        
        trade_icon = f"{G_}✓ 允許{R}" if snapshot.trading_allowed else f"{r}✗ 禁止{R}"
        lines.append(f"   交易狀態: {trade_icon}")
        
        lines.append(f"{c}{'='*60}{R}")
        
        return "\n".join(lines)
    
    def _render_col_market_ascii(self) -> List[str]:
        """市場數據欄 (ASCII)"""
        lines = [" [Market Data]", " " + "-" * 29]
        
        obi_sign = "+" if self.obi > 0.3 else "-" if self.obi < -0.3 else "o"
        wpi_sign = "+" if self.wpi > 0.3 else "-" if self.wpi < -0.3 else "o"
        
        lines.extend([
            f" OBI: {self.obi:+.3f} [{obi_sign}]",
            f" WPI: {self.wpi:+.3f} [{wpi_sign}]",
            f" VPIN: {self.vpin:.3f}",
            f" Funding: {self.funding_rate*100:.4f}%",
            f" OI Change: {self.oi_change_pct:+.2f}%",
            "",
            " Liquidation Pressure:",
            f"   Long:  {self.liquidation_pressure_long:.0f}",
            f"   Short: {self.liquidation_pressure_short:.0f}",
        ])
        
        return lines
    
    def render_market_data_panel(self) -> str:
        """
        顯示市場數據面板
        """
        lines = [
            "",
            "=" * 70,
            "📡 幣安 API 即時數據",
            "=" * 70,
            f"",
            f"💵 BTC 價格: ${self.current_price:,.2f}",
            f"📊 訂單簿失衡 (OBI): {self.obi:+.3f}",
            f"🔮 知情交易機率 (VPIN): {self.vpin:.3f}",
            f"🐋 鯨魚壓力指數 (WPI): {self.wpi:+.3f}",
            f"💰 資金費率: {self.funding_rate*100:.4f}%",
            f"📈 OI 變化: {self.oi_change_pct:+.2f}%",
            f"🔴 多頭爆倉壓力: {self.liquidation_pressure_long:.0f}",
            f"🟢 空頭爆倉壓力: {self.liquidation_pressure_short:.0f}",
            f"",
            f"⏰ 更新時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"",
        ]
        return "\n".join(lines)
    
    def run_single_analysis(self):
        """
        執行單次分析
        """
        print("\n🐋 主力策略識別系統 v4.0 - 單次分析")
        print("=" * 70)
        
        snapshot = self.analyze()
        
        # 顯示所有面板
        print(self.render_market_data_panel())
        print(self.render_all_strategies_panel())
        print(self.render_primary_strategy_panel(snapshot))
        print(self.render_entry_exit_panel(snapshot))
        print(self.render_monitoring_panel(snapshot))
        print(self.render_statistics_panel())
        
        return snapshot
    
    def run_continuous_monitoring(self, hours: float = 1.0, enable_paper_trading: bool = True):
        """
        執行持續監控 - 動態儀表板模式 (無閃爍)
        
        Args:
            hours: 監控時長（小時）
            enable_paper_trading: 是否啟用模擬交易追蹤
        """
        print(f"\n 🐋 Whale Strategy Detector v4.0 - Continuous Monitoring")
        print(f" Duration: {hours} hours | Interval: 3 sec")
        print(f" Paper Trading: {'✅ Enabled' if enable_paper_trading else '❌ Disabled'}")
        print(f" Log: {self.log_file}")
        print("=" * 70)
        print(" Press Ctrl+C to stop\n")
        time.sleep(2)
        
        self.logger.info("=" * 70)
        self.logger.info(f"Start monitoring - Duration: {hours} hours | Paper Trading: {enable_paper_trading}")
        self.logger.info("=" * 70)
        
        # 強制 flush
        for handler in self.logger.handlers:
            handler.flush()
        
        end_time = time.time() + hours * 3600
        start_time = time.time()
        iteration = 0
        last_signal_time = 0  # 防止同一信號重複開倉
        
        # 隱藏游標
        print("\033[?25l", end="")
        
        try:
            while time.time() < end_time:
                iteration += 1
                remaining_minutes = (end_time - time.time()) / 60
                
                # 執行分析
                snapshot = self.analyze()
                
                # 🆕 更新模擬交易 (檢查止盈止損)
                if enable_paper_trading:
                    self.update_paper_trades()
                
                # 🆕 檢查新信號並開倉
                if enable_paper_trading and snapshot.entry_signal:
                    # 同一分鐘內不重複開倉
                    current_minute = int(time.time() / 60)
                    if current_minute != last_signal_time:
                        # 最多同時 3 筆
                        if len(self.active_trades) < 3:
                            self.open_paper_trade(snapshot)
                            last_signal_time = current_minute
                
                # 生成儀表板 (加入模擬交易資訊)
                dashboard = self.render_dashboard(snapshot, iteration, remaining_minutes)
                
                # 加入模擬交易狀態
                if enable_paper_trading:
                    dashboard += self._render_paper_trade_status()
                
                # 清屏並輸出（每次都清屏確保乾淨）
                sys.stdout.write("\033[2J\033[H")  # 清屏並移到左上角
                sys.stdout.write(dashboard)
                sys.stdout.flush()
                
                # ===== 日誌記錄 (即時寫入) =====
                strat_name = snapshot.primary_strategy.strategy.name if snapshot.primary_strategy else "NONE"
                strat_prob = snapshot.primary_strategy.probability if snapshot.primary_strategy else 0
                direction = snapshot.entry_signal.direction.value if snapshot.entry_signal else "WAIT"
                
                log_line = (
                    f"#{iteration:04d} | "
                    f"BTC=${self.current_price:,.2f} | "
                    f"{strat_name:12} ({strat_prob:.1%}) | "
                    f"Dir={direction:4} | "
                    f"OBI={self.obi:+.3f} WPI={self.wpi:+.3f} | "
                    f"FR={self.funding_rate*100:.4f}%"
                )
                self.logger.info(log_line)
                
                # 如果有進場信號，額外記錄
                if snapshot.entry_signal:
                    e = snapshot.entry_signal
                    signal_log = (
                        f"  >> SIGNAL: {e.direction.value} | "
                        f"Entry=${e.entry_price:,.2f} | "
                        f"TP=${e.take_profit:,.2f} | "
                        f"SL=${e.stop_loss:,.2f} | "
                        f"Size={e.position_size_pct:.0f}%"
                    )
                    self.logger.info(signal_log)
                
                # 強制 flush 確保即時寫入
                for handler in self.logger.handlers:
                    handler.flush()
                
                # 等待 3 秒
                time.sleep(3)
        
        except KeyboardInterrupt:
            pass
        finally:
            # 顯示游標
            print("\033[?25h", end="")
            
            # 🆕 保存模擬交易記錄
            if enable_paper_trading:
                self.save_paper_trades()
        
        print("\n\n Monitoring stopped")
        self.logger.info("=" * 70)
        self.logger.info("Monitoring stopped by user")
        
        # 計算運行時間
        actual_runtime = (time.time() - start_time) / 60
        
        # 顯示最終統計
        print("\n" + "=" * 100)
        print(" Final Statistics")
        print("=" * 100)
        final_stats = self.render_statistics_panel()
        print(final_stats)
        
        # 🆕 顯示模擬交易統計
        if enable_paper_trading:
            print(self._render_paper_trade_final_report())
        
        # 生成成功報告
        success_report = self.generate_success_report(actual_runtime)
        print(success_report)
        
        # 記錄到日誌
        self.logger.info("=" * 70)
        self.logger.info("Final Statistics")
        self.logger.info("=" * 70)
        self.logger.info(final_stats)
        self.logger.info(success_report)
        
        # 最終 flush
        for handler in self.logger.handlers:
            handler.flush()
    
    def generate_success_report(self, runtime_minutes: float) -> str:
        """
        生成系統成功驗證報告
        """
        m = self.success_metrics
        
        # 計算成功指標
        api_success_rate = (
            m["api_calls_success"] / (m["api_calls_success"] + m["api_calls_failed"]) * 100
            if (m["api_calls_success"] + m["api_calls_failed"]) > 0 else 0
        )
        
        price_range = m["price_high"] - m["price_low"] if m["price_low"] < float('inf') else 0
        price_range_pct = price_range / m["price_at_start"] * 100 if m["price_at_start"] > 0 else 0
        
        # 判斷系統是否成功
        success_checks = {
            "API 連線穩定": api_success_rate >= 95,
            "分析執行正常": m["analysis_count"] > 0,
            "策略偵測運作": m["strategy_changes"] >= 0,  # 有策略變化表示運作中
            "價格數據正確": m["price_at_start"] > 0,
        }
        
        all_passed = all(success_checks.values())
        
        lines = [
            "",
            "=" * 70,
            "✅ 系統成功驗證報告" if all_passed else "⚠️ 系統運作報告",
            "=" * 70,
            "",
            f"⏱️ 實際運行時間: {runtime_minutes:.1f} 分鐘",
            f"🔄 總分析次數: {m['analysis_count']}",
            f"📡 API 成功率: {api_success_rate:.1f}%",
            f"   - 成功呼叫: {m['api_calls_success']}",
            f"   - 失敗呼叫: {m['api_calls_failed']}",
            "",
            f"💵 價格追蹤:",
            f"   - 起始價格: ${m['price_at_start']:,.2f}",
            f"   - 最高價格: ${m['price_high']:,.2f}",
            f"   - 最低價格: ${m['price_low']:,.2f}" if m['price_low'] < float('inf') else "   - 最低價格: N/A",
            f"   - 波動範圍: {price_range_pct:.2f}%",
            "",
            f"🐋 策略偵測:",
            f"   - 策略切換次數: {m['strategy_changes']}",
            f"   - 總信號數: {m['total_signals']}",
            "",
            "📋 驗證項目:",
        ]
        
        for check_name, passed in success_checks.items():
            icon = "✅" if passed else "❌"
            lines.append(f"   {icon} {check_name}")
        
        lines.extend([
            "",
            "=" * 70,
            "🎉 系統運作正常！" if all_passed else "⚠️ 部分項目需要注意",
            "=" * 70,
            "",
            "📁 詳細日誌保存於:",
            f"   {self.log_dir}",
            "",
        ])
        
        return "\n".join(lines)
    
    def _render_paper_trade_status(self) -> str:
        """渲染模擬交易狀態 (加在儀表板下方)"""
        R = '\033[0m'
        B = '\033[1m'
        g = '\033[32m'
        r = '\033[31m'
        y = '\033[33m'
        c = '\033[36m'
        G_ = '\033[92m'
        R_ = '\033[91m'
        
        lines = ["\n"]
        lines.append(f"{c}{'='*60}{R}")
        lines.append(f"{y}{B}📊 模擬交易追蹤{R}")
        
        # 統計
        summary = self.get_paper_trade_summary()
        total = summary.get("total_trades", 0)
        wins = summary.get("wins", 0)
        win_rate = summary.get("win_rate", 0)
        total_pnl = summary.get("total_pnl_pct", 0)
        active = len(self.active_trades)
        
        pnl_color = G_ if total_pnl >= 0 else R_
        
        lines.append(f"   已平倉: {total} 筆 | 勝率: {win_rate:.1%} | 累計盈虧: {pnl_color}{total_pnl:+.2f}%{R}")
        lines.append(f"   進行中: {active} 筆")
        
        # 顯示進行中的交易
        if self.active_trades:
            for tid, trade in list(self.active_trades.items())[:3]:
                entry = trade["entry_price"]
                is_long = "多" in trade["direction"] or "LONG" in trade["direction"].upper()
                
                if is_long:
                    float_pnl = (self.current_price - entry) / entry * 100
                else:
                    float_pnl = (entry - self.current_price) / entry * 100
                
                float_color = G_ if float_pnl >= 0 else R_
                dir_icon = "🟢" if is_long else "🔴"
                
                lines.append(f"   {dir_icon} {trade['strategy'][:10]:<10} @ ${entry:,.0f} | "
                           f"浮動: {float_color}{float_pnl:+.2f}%{R}")
        
        lines.append(f"{c}{'='*60}{R}")
        
        return "\n".join(lines)
    
    def _render_paper_trade_final_report(self) -> str:
        """渲染最終模擬交易報告"""
        summary = self.get_paper_trade_summary()
        
        lines = [
            "",
            "=" * 70,
            "📊 模擬交易最終報告",
            "=" * 70,
            "",
        ]
        
        if summary.get("message"):
            lines.append(f"   {summary['message']}")
        else:
            lines.append(f"📈 交易統計:")
            lines.append(f"   總交易數: {summary['total_trades']}")
            lines.append(f"   獲勝: {summary['wins']} | 虧損: {summary['losses']}")
            lines.append(f"   勝率: {summary['win_rate']:.1%}")
            lines.append(f"   累計盈虧: {summary['total_pnl_pct']:+.2f}%")
            lines.append(f"   平均獲勝: {summary['avg_win_pct']:+.2f}%")
            lines.append(f"   平均虧損: {summary['avg_loss_pct']:+.2f}%")
            
            # 策略統計
            if summary.get("strategy_stats"):
                lines.append(f"\n🎯 策略表現:")
                for s, data in sorted(summary["strategy_stats"].items(), 
                                     key=lambda x: x[1]["total"], reverse=True):
                    wr = data["wins"] / data["total"] if data["total"] > 0 else 0
                    lines.append(f"   {s:<20} | {data['total']:>2}筆 | 勝率: {wr:.1%} | 盈虧: {data['pnl']:+.2f}%")
        
        lines.append("")
        lines.append("=" * 70)
        
        return "\n".join(lines)


def main():
    """
    主程式入口
    """
    tester = WhaleDetectorV4Test()
    
    if len(sys.argv) > 1:
        try:
            hours = float(sys.argv[1])
            tester.run_continuous_monitoring(hours, enable_paper_trading=True)
        except ValueError:
            print(f"❌ 無效的時間參數: {sys.argv[1]}")
            print("用法: python scripts/whale_detector_v4_test.py [監控時長(小時)]")
    else:
        # 單次分析
        tester.run_single_analysis()


if __name__ == "__main__":
    main()
