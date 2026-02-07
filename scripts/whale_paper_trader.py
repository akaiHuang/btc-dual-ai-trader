#!/usr/bin/env python3
"""
Whale Paper Trader v1.0
========================
模擬交易系統 - 驗證 Whale Detector v4 預測準確度
支持動態學習與策略調整

功能：
1. 接收 Whale Detector v4 的進場信號
2. 在 Binance Testnet 模擬執行
3. 追蹤每筆交易結果 (勝率、盈虧)
4. 動態調整策略參數
5. 持續學習優化

Author: AI Assistant
Date: 2025-11-28
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
from pathlib import Path
import threading
from collections import defaultdict

# 添加專案路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

import ccxt

# ============================================================
# 數據結構
# ============================================================

class TradeDirection(Enum):
    LONG = "LONG"
    SHORT = "SHORT"

class TradeStatus(Enum):
    PENDING = "PENDING"      # 等待進場
    OPEN = "OPEN"            # 持倉中
    CLOSED_TP = "CLOSED_TP"  # 止盈平倉
    CLOSED_SL = "CLOSED_SL"  # 止損平倉
    CLOSED_MANUAL = "CLOSED_MANUAL"  # 手動平倉
    EXPIRED = "EXPIRED"      # 超時平倉

@dataclass
class TradeRecord:
    """交易記錄"""
    trade_id: str
    strategy: str                    # 觸發的策略 (如 ACCUMULATION)
    direction: TradeDirection
    entry_price: float
    take_profit: float
    stop_loss: float
    position_size_pct: float         # 倉位百分比
    position_size_usd: float         # 實際倉位 USD
    
    # 預測指標
    predicted_probability: float     # 預測機率
    predicted_confidence: float      # 預測信心
    
    # 市場快照
    obi_at_entry: float = 0.0
    wpi_at_entry: float = 0.0
    funding_rate_at_entry: float = 0.0
    
    # 時間戳
    signal_time: str = ""
    entry_time: str = ""
    exit_time: str = ""
    
    # 結果
    status: TradeStatus = TradeStatus.PENDING
    exit_price: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0    # 持倉期間最大回撤
    max_profit_pct: float = 0.0      # 持倉期間最大浮盈
    duration_minutes: float = 0.0
    
    # 學習標籤
    is_successful: bool = False      # 是否成功 (TP or profit > 0)
    
    def to_dict(self) -> Dict:
        d = asdict(self)
        d['direction'] = self.direction.value
        d['status'] = self.status.value
        return d
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'TradeRecord':
        d['direction'] = TradeDirection(d['direction'])
        d['status'] = TradeStatus(d['status'])
        return cls(**d)


@dataclass
class StrategyPerformance:
    """策略表現統計"""
    strategy_name: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl_usd: float = 0.0
    total_pnl_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0       # 總盈利 / 總虧損
    avg_duration_minutes: float = 0.0
    
    # 按方向統計
    long_trades: int = 0
    long_win_rate: float = 0.0
    short_trades: int = 0
    short_win_rate: float = 0.0
    
    # 學習調整
    confidence_multiplier: float = 1.0  # 信心調整係數
    position_multiplier: float = 1.0    # 倉位調整係數
    enabled: bool = True                # 是否啟用


@dataclass 
class LearningConfig:
    """動態學習配置"""
    # 基礎參數
    initial_capital: float = 10000.0
    max_position_pct: float = 0.5        # 最大單筆倉位
    max_concurrent_trades: int = 3       # 最大同時持倉數
    
    # 學習參數
    min_trades_for_learning: int = 10    # 開始學習前最少交易數
    learning_rate: float = 0.1           # 學習速率
    
    # 策略調整閾值
    disable_strategy_win_rate: float = 0.3   # 低於此勝率禁用策略
    boost_strategy_win_rate: float = 0.7     # 高於此勝率加大倉位
    
    # 風險控制
    daily_loss_limit_pct: float = 0.05   # 日虧損限制
    consecutive_loss_pause: int = 5      # 連續虧損暫停
    
    # 模型選擇
    use_ml_model: bool = False           # 是否使用 ML 模型
    use_llm_advisor: bool = False        # 是否使用 LLM 顧問
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'LearningConfig':
        return cls(**d)


# ============================================================
# Paper Trader 主類
# ============================================================

class WhalePaperTrader:
    """
    Whale 模擬交易系統
    """
    
    def __init__(
        self,
        config_path: str = "config/whale_learning_config.json",
        trades_path: str = "logs/whale_paper_trading/trades.json",
        performance_path: str = "logs/whale_paper_trading/strategy_performance.json",
        use_testnet: bool = True
    ):
        self.config_path = Path(config_path)
        self.trades_path = Path(trades_path)
        self.performance_path = Path(performance_path)
        self.use_testnet = use_testnet
        
        # 確保目錄存在
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.trades_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 載入配置
        self.config = self._load_config()
        
        # 載入交易歷史
        self.trades: List[TradeRecord] = self._load_trades()
        
        # 載入策略表現
        self.strategy_performance: Dict[str, StrategyPerformance] = self._load_performance()
        
        # 當前持倉
        self.open_positions: Dict[str, TradeRecord] = {}
        
        # 資金追蹤
        self.current_capital = self.config.initial_capital
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        
        # 初始化交易所
        self.exchange = self._init_exchange()
        
        # 日誌
        self._setup_logging()
        
        self.logger.info("=" * 60)
        self.logger.info("Whale Paper Trader v1.0 initialized")
        self.logger.info(f"  Capital: ${self.config.initial_capital:,.2f}")
        self.logger.info(f"  Historical trades: {len(self.trades)}")
        self.logger.info(f"  Testnet: {self.use_testnet}")
        self.logger.info("=" * 60)
    
    def _setup_logging(self):
        """設置日誌"""
        log_dir = Path("logs/whale_paper_trader")
        log_dir.mkdir(parents=True, exist_ok=True)
        
        log_file = log_dir / f"paper_trader_{datetime.now().strftime('%Y%m%d')}.log"
        
        self.logger = logging.getLogger("WhalePaperTrader")
        self.logger.setLevel(logging.INFO)
        
        # 避免重複 handler
        if not self.logger.handlers:
            # 檔案 handler
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setLevel(logging.INFO)
            fh.setFormatter(logging.Formatter(
                '%(asctime)s | %(levelname)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            ))
            self.logger.addHandler(fh)
            
            # 控制台 handler
            ch = logging.StreamHandler()
            ch.setLevel(logging.INFO)
            ch.setFormatter(logging.Formatter(
                '%(asctime)s | %(message)s',
                datefmt='%H:%M:%S'
            ))
            self.logger.addHandler(ch)
    
    def _init_exchange(self) -> ccxt.Exchange:
        """初始化交易所連接"""
        config_file = Path("config/config.json")
        
        if config_file.exists():
            with open(config_file) as f:
                cfg = json.load(f)
        else:
            cfg = {}
        
        exchange_config = {
            'apiKey': cfg.get('api_key', ''),
            'secret': cfg.get('api_secret', ''),
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
            }
        }
        
        if self.use_testnet:
            exchange_config['options']['testnet'] = True
            # Binance Testnet
            exchange_config['urls'] = {
                'api': {
                    'public': 'https://testnet.binancefuture.com/fapi/v1',
                    'private': 'https://testnet.binancefuture.com/fapi/v1',
                }
            }
        
        exchange = ccxt.binance(exchange_config)
        
        return exchange
    
    def _load_config(self) -> LearningConfig:
        """載入學習配置"""
        if self.config_path.exists():
            with open(self.config_path) as f:
                data = json.load(f)
            # 過濾掉不是 LearningConfig 屬性的 key (如 _comment, strategy_weights 等)
            valid_keys = {f.name for f in LearningConfig.__dataclass_fields__.values()}
            filtered_data = {k: v for k, v in data.items() if k in valid_keys}
            return LearningConfig.from_dict(filtered_data)
        return LearningConfig()
    
    def _save_config(self):
        """保存學習配置"""
        with open(self.config_path, 'w') as f:
            json.dump(self.config.to_dict(), f, indent=2)
    
    def _load_trades(self) -> List[TradeRecord]:
        """載入交易歷史"""
        if self.trades_path.exists():
            with open(self.trades_path) as f:
                data = json.load(f)
            return [TradeRecord.from_dict(t) for t in data]
        return []
    
    def _save_trades(self):
        """保存交易歷史"""
        with open(self.trades_path, 'w') as f:
            json.dump([t.to_dict() for t in self.trades], f, indent=2, ensure_ascii=False)
    
    def _load_performance(self) -> Dict[str, StrategyPerformance]:
        """載入策略表現"""
        if self.performance_path.exists():
            with open(self.performance_path) as f:
                data = json.load(f)
            return {k: StrategyPerformance(**v) for k, v in data.items()}
        return {}
    
    def _save_performance(self):
        """保存策略表現"""
        data = {k: asdict(v) for k, v in self.strategy_performance.items()}
        with open(self.performance_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    # ============================================================
    # 交易執行
    # ============================================================
    
    def should_take_trade(self, signal: Dict) -> Tuple[bool, str]:
        """
        判斷是否應該接受這個交易信號
        
        Returns:
            (是否接受, 原因)
        """
        strategy = signal.get('strategy', 'UNKNOWN')
        direction = signal.get('direction', 'LONG')
        probability = signal.get('probability', 0)
        
        # 1. 檢查持倉數量
        if len(self.open_positions) >= self.config.max_concurrent_trades:
            return False, f"已達最大持倉數 ({self.config.max_concurrent_trades})"
        
        # 2. 檢查日虧損
        if self.daily_pnl < -self.config.daily_loss_limit_pct * self.current_capital:
            return False, f"已達日虧損限制 ({self.config.daily_loss_limit_pct*100}%)"
        
        # 3. 檢查連續虧損
        if self.consecutive_losses >= self.config.consecutive_loss_pause:
            return False, f"連續虧損 {self.consecutive_losses} 次，暫停交易"
        
        # 4. 檢查策略是否啟用
        if strategy in self.strategy_performance:
            perf = self.strategy_performance[strategy]
            if not perf.enabled:
                return False, f"策略 {strategy} 已被禁用"
            
            # 根據歷史勝率調整
            if perf.total_trades >= self.config.min_trades_for_learning:
                if perf.win_rate < self.config.disable_strategy_win_rate:
                    return False, f"策略 {strategy} 勝率過低 ({perf.win_rate:.1%})"
        
        # 5. 檢查機率閾值
        min_probability = 0.6  # 最低機率要求
        if probability < min_probability:
            return False, f"機率不足 ({probability:.1%} < {min_probability:.1%})"
        
        return True, "通過所有檢查"
    
    def calculate_position_size(self, signal: Dict) -> float:
        """
        計算實際倉位大小 (USD)
        根據策略表現動態調整
        """
        strategy = signal.get('strategy', 'UNKNOWN')
        base_pct = signal.get('position_size_pct', 0.3)
        
        # 基礎倉位
        position_usd = self.current_capital * min(base_pct, self.config.max_position_pct)
        
        # 根據策略表現調整
        if strategy in self.strategy_performance:
            perf = self.strategy_performance[strategy]
            if perf.total_trades >= self.config.min_trades_for_learning:
                # 高勝率策略加大倉位
                if perf.win_rate >= self.config.boost_strategy_win_rate:
                    position_usd *= 1.5
                    self.logger.info(f"  策略 {strategy} 勝率高 ({perf.win_rate:.1%})，倉位 x1.5")
                # 低勝率策略減小倉位
                elif perf.win_rate < 0.5:
                    position_usd *= 0.5
                    self.logger.info(f"  策略 {strategy} 勝率低 ({perf.win_rate:.1%})，倉位 x0.5")
        
        return round(position_usd, 2)
    
    def open_trade(self, signal: Dict) -> Optional[TradeRecord]:
        """
        開倉
        
        signal 格式:
        {
            'strategy': 'ACCUMULATION',
            'direction': 'LONG',  # or 'SHORT'
            'entry_price': 91000,
            'take_profit': 92820,
            'stop_loss': 89635,
            'position_size_pct': 0.5,
            'probability': 0.85,
            'confidence': 0.75,
            'obi': 0.345,
            'wpi': 0.562,
            'funding_rate': 0.0001
        }
        """
        # 檢查是否應該交易
        should_trade, reason = self.should_take_trade(signal)
        if not should_trade:
            self.logger.warning(f"拒絕交易: {reason}")
            return None
        
        # 計算倉位
        position_usd = self.calculate_position_size(signal)
        
        # 生成交易 ID
        trade_id = f"WT_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{signal['strategy'][:4]}"
        
        # 創建交易記錄
        trade = TradeRecord(
            trade_id=trade_id,
            strategy=signal['strategy'],
            direction=TradeDirection(signal['direction']),
            entry_price=signal['entry_price'],
            take_profit=signal['take_profit'],
            stop_loss=signal['stop_loss'],
            position_size_pct=signal.get('position_size_pct', 0.3),
            position_size_usd=position_usd,
            predicted_probability=signal.get('probability', 0),
            predicted_confidence=signal.get('confidence', 0),
            obi_at_entry=signal.get('obi', 0),
            wpi_at_entry=signal.get('wpi', 0),
            funding_rate_at_entry=signal.get('funding_rate', 0),
            signal_time=datetime.now().isoformat(),
            entry_time=datetime.now().isoformat(),
            status=TradeStatus.OPEN
        )
        
        # 添加到持倉
        self.open_positions[trade_id] = trade
        
        # 記錄
        self.logger.info("=" * 50)
        self.logger.info(f"🔔 開倉: {trade_id}")
        self.logger.info(f"   策略: {trade.strategy}")
        self.logger.info(f"   方向: {trade.direction.value}")
        self.logger.info(f"   進場: ${trade.entry_price:,.2f}")
        self.logger.info(f"   止盈: ${trade.take_profit:,.2f} ({(trade.take_profit/trade.entry_price-1)*100:+.2f}%)")
        self.logger.info(f"   止損: ${trade.stop_loss:,.2f} ({(trade.stop_loss/trade.entry_price-1)*100:+.2f}%)")
        self.logger.info(f"   倉位: ${position_usd:,.2f}")
        self.logger.info(f"   機率: {trade.predicted_probability:.1%}")
        self.logger.info("=" * 50)
        
        return trade
    
    def update_positions(self, current_price: float) -> List[TradeRecord]:
        """
        更新所有持倉狀態，檢查止盈止損
        
        Returns:
            已平倉的交易列表
        """
        closed_trades = []
        
        for trade_id, trade in list(self.open_positions.items()):
            if trade.status != TradeStatus.OPEN:
                continue
            
            # 計算當前盈虧
            if trade.direction == TradeDirection.LONG:
                current_pnl_pct = (current_price - trade.entry_price) / trade.entry_price
                hit_tp = current_price >= trade.take_profit
                hit_sl = current_price <= trade.stop_loss
            else:  # SHORT
                current_pnl_pct = (trade.entry_price - current_price) / trade.entry_price
                hit_tp = current_price <= trade.take_profit
                hit_sl = current_price >= trade.stop_loss
            
            # 更新最大浮盈/回撤
            trade.max_profit_pct = max(trade.max_profit_pct, current_pnl_pct)
            trade.max_drawdown_pct = min(trade.max_drawdown_pct, current_pnl_pct)
            
            # 檢查止盈
            if hit_tp:
                self._close_trade(trade, current_price, TradeStatus.CLOSED_TP)
                closed_trades.append(trade)
                del self.open_positions[trade_id]
            
            # 檢查止損
            elif hit_sl:
                self._close_trade(trade, current_price, TradeStatus.CLOSED_SL)
                closed_trades.append(trade)
                del self.open_positions[trade_id]
        
        return closed_trades
    
    def _close_trade(self, trade: TradeRecord, exit_price: float, status: TradeStatus):
        """平倉並記錄"""
        trade.exit_price = exit_price
        trade.status = status
        trade.exit_time = datetime.now().isoformat()
        
        # 計算盈虧
        if trade.direction == TradeDirection.LONG:
            trade.pnl_pct = (exit_price - trade.entry_price) / trade.entry_price
        else:
            trade.pnl_pct = (trade.entry_price - exit_price) / trade.entry_price
        
        trade.pnl_usd = trade.position_size_usd * trade.pnl_pct
        
        # 計算持倉時間
        entry_dt = datetime.fromisoformat(trade.entry_time)
        exit_dt = datetime.fromisoformat(trade.exit_time)
        trade.duration_minutes = (exit_dt - entry_dt).total_seconds() / 60
        
        # 判斷是否成功
        trade.is_successful = trade.pnl_pct > 0
        
        # 更新資金
        self.current_capital += trade.pnl_usd
        self.daily_pnl += trade.pnl_usd
        
        # 更新連續虧損計數
        if trade.is_successful:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
        
        # 添加到歷史
        self.trades.append(trade)
        self._save_trades()
        
        # 更新策略表現
        self._update_strategy_performance(trade)
        
        # 記錄
        emoji = "✅" if trade.is_successful else "❌"
        status_text = {
            TradeStatus.CLOSED_TP: "止盈",
            TradeStatus.CLOSED_SL: "止損",
            TradeStatus.CLOSED_MANUAL: "手動",
            TradeStatus.EXPIRED: "超時"
        }.get(status, "未知")
        
        self.logger.info("=" * 50)
        self.logger.info(f"{emoji} 平倉: {trade.trade_id}")
        self.logger.info(f"   方式: {status_text}")
        self.logger.info(f"   進場: ${trade.entry_price:,.2f}")
        self.logger.info(f"   出場: ${exit_price:,.2f}")
        self.logger.info(f"   盈虧: ${trade.pnl_usd:+,.2f} ({trade.pnl_pct:+.2%})")
        self.logger.info(f"   持倉: {trade.duration_minutes:.1f} 分鐘")
        self.logger.info(f"   資金: ${self.current_capital:,.2f}")
        self.logger.info("=" * 50)
    
    # ============================================================
    # 策略學習
    # ============================================================
    
    def _update_strategy_performance(self, trade: TradeRecord):
        """更新策略表現統計"""
        strategy = trade.strategy
        
        if strategy not in self.strategy_performance:
            self.strategy_performance[strategy] = StrategyPerformance(strategy_name=strategy)
        
        perf = self.strategy_performance[strategy]
        
        # 更新計數
        perf.total_trades += 1
        if trade.is_successful:
            perf.winning_trades += 1
        else:
            perf.losing_trades += 1
        
        # 更新盈虧
        perf.total_pnl_usd += trade.pnl_usd
        perf.total_pnl_pct += trade.pnl_pct
        
        # 更新勝率
        perf.win_rate = perf.winning_trades / perf.total_trades if perf.total_trades > 0 else 0
        
        # 按方向統計
        if trade.direction == TradeDirection.LONG:
            perf.long_trades += 1
            long_wins = sum(1 for t in self.trades 
                          if t.strategy == strategy 
                          and t.direction == TradeDirection.LONG 
                          and t.is_successful)
            perf.long_win_rate = long_wins / perf.long_trades if perf.long_trades > 0 else 0
        else:
            perf.short_trades += 1
            short_wins = sum(1 for t in self.trades 
                           if t.strategy == strategy 
                           and t.direction == TradeDirection.SHORT 
                           and t.is_successful)
            perf.short_win_rate = short_wins / perf.short_trades if perf.short_trades > 0 else 0
        
        # 計算平均盈虧
        wins = [t.pnl_pct for t in self.trades if t.strategy == strategy and t.is_successful]
        losses = [t.pnl_pct for t in self.trades if t.strategy == strategy and not t.is_successful]
        
        perf.avg_win_pct = sum(wins) / len(wins) if wins else 0
        perf.avg_loss_pct = sum(losses) / len(losses) if losses else 0
        
        # 計算 Profit Factor
        total_wins = sum(t.pnl_usd for t in self.trades if t.strategy == strategy and t.is_successful)
        total_losses = abs(sum(t.pnl_usd for t in self.trades if t.strategy == strategy and not t.is_successful))
        perf.profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        
        # 平均持倉時間
        durations = [t.duration_minutes for t in self.trades if t.strategy == strategy]
        perf.avg_duration_minutes = sum(durations) / len(durations) if durations else 0
        
        # 動態調整
        self._apply_learning_adjustments(perf)
        
        # 保存
        self._save_performance()
    
    def _apply_learning_adjustments(self, perf: StrategyPerformance):
        """應用學習調整"""
        if perf.total_trades < self.config.min_trades_for_learning:
            return
        
        # 勝率太低 -> 禁用策略
        if perf.win_rate < self.config.disable_strategy_win_rate:
            perf.enabled = False
            self.logger.warning(f"⚠️ 策略 {perf.strategy_name} 勝率過低 ({perf.win_rate:.1%})，已禁用")
        
        # 勝率很高 -> 加大信心
        elif perf.win_rate >= self.config.boost_strategy_win_rate:
            perf.confidence_multiplier = 1.0 + (perf.win_rate - 0.5) * self.config.learning_rate
            perf.position_multiplier = min(1.5, 1.0 + (perf.win_rate - 0.5))
            self.logger.info(f"📈 策略 {perf.strategy_name} 表現優異，倉位係數: {perf.position_multiplier:.2f}")
        
        # 一般表現 -> 正常調整
        else:
            # 根據 profit factor 微調
            if perf.profit_factor > 2:
                perf.position_multiplier = 1.2
            elif perf.profit_factor < 0.8:
                perf.position_multiplier = 0.8
            else:
                perf.position_multiplier = 1.0
    
    # ============================================================
    # 報告與統計
    # ============================================================
    
    def get_summary(self) -> Dict:
        """獲取交易總結"""
        if not self.trades:
            return {"message": "尚無交易記錄"}
        
        total_trades = len(self.trades)
        winning_trades = sum(1 for t in self.trades if t.is_successful)
        
        total_pnl = sum(t.pnl_usd for t in self.trades)
        
        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": total_trades - winning_trades,
            "win_rate": winning_trades / total_trades if total_trades > 0 else 0,
            "total_pnl_usd": total_pnl,
            "total_pnl_pct": total_pnl / self.config.initial_capital,
            "current_capital": self.current_capital,
            "capital_growth_pct": (self.current_capital / self.config.initial_capital - 1),
            "open_positions": len(self.open_positions),
            "strategy_count": len(self.strategy_performance),
            "best_strategy": max(self.strategy_performance.values(), 
                               key=lambda x: x.win_rate if x.total_trades >= 5 else 0,
                               default=None),
            "worst_strategy": min(self.strategy_performance.values(),
                                key=lambda x: x.win_rate if x.total_trades >= 5 else 1,
                                default=None)
        }
    
    def print_report(self):
        """打印詳細報告"""
        summary = self.get_summary()
        
        print("\n" + "=" * 70)
        print("📊 WHALE PAPER TRADER - 交易報告")
        print("=" * 70)
        
        print(f"\n💰 資金狀況:")
        print(f"   初始資金: ${self.config.initial_capital:,.2f}")
        print(f"   當前資金: ${self.current_capital:,.2f}")
        print(f"   總盈虧:   ${summary.get('total_pnl_usd', 0):+,.2f} ({summary.get('capital_growth_pct', 0):+.2%})")
        
        print(f"\n📈 交易統計:")
        print(f"   總交易數: {summary.get('total_trades', 0)}")
        print(f"   獲勝交易: {summary.get('winning_trades', 0)}")
        print(f"   虧損交易: {summary.get('losing_trades', 0)}")
        print(f"   勝率:     {summary.get('win_rate', 0):.1%}")
        
        print(f"\n🎯 策略表現:")
        for name, perf in sorted(self.strategy_performance.items(), 
                                  key=lambda x: x[1].total_pnl_usd, reverse=True):
            status = "✅" if perf.enabled else "❌"
            print(f"   {status} {name}:")
            print(f"      交易數: {perf.total_trades} | 勝率: {perf.win_rate:.1%} | 盈虧: ${perf.total_pnl_usd:+,.2f}")
            print(f"      做多勝率: {perf.long_win_rate:.1%} ({perf.long_trades}筆) | 做空勝率: {perf.short_win_rate:.1%} ({perf.short_trades}筆)")
        
        print("\n" + "=" * 70)


# ============================================================
# ML 學習模組 (可選)
# ============================================================

class MLPredictor:
    """
    機器學習預測器
    使用歷史交易數據訓練模型
    """
    
    def __init__(self, model_path: str = "models/whale_ml_model"):
        self.model_path = Path(model_path)
        self.model = None
        self.scaler = None
        self.is_trained = False
    
    def prepare_features(self, trade_data: Dict) -> List[float]:
        """準備特徵向量"""
        features = [
            trade_data.get('probability', 0),
            trade_data.get('confidence', 0),
            trade_data.get('obi', 0),
            trade_data.get('wpi', 0),
            trade_data.get('funding_rate', 0) * 10000,  # 放大資金費率
            1 if trade_data.get('direction') == 'LONG' else 0,
            # 策略 one-hot (簡化版)
            1 if 'ACCUMULATION' in trade_data.get('strategy', '') else 0,
            1 if 'DISTRIBUTION' in trade_data.get('strategy', '') else 0,
            1 if 'SQUEEZE' in trade_data.get('strategy', '') else 0,
            1 if 'TRAP' in trade_data.get('strategy', '') else 0,
        ]
        return features
    
    def train(self, trades: List[TradeRecord]):
        """訓練模型"""
        try:
            import numpy as np
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.preprocessing import StandardScaler
            from sklearn.model_selection import cross_val_score
            import joblib
        except ImportError:
            print("⚠️ 需要安裝 scikit-learn: pip install scikit-learn")
            return False
        
        if len(trades) < 20:
            print(f"⚠️ 訓練數據不足 ({len(trades)} < 20)")
            return False
        
        # 準備數據
        X = []
        y = []
        
        for trade in trades:
            features = self.prepare_features({
                'probability': trade.predicted_probability,
                'confidence': trade.predicted_confidence,
                'obi': trade.obi_at_entry,
                'wpi': trade.wpi_at_entry,
                'funding_rate': trade.funding_rate_at_entry,
                'direction': trade.direction.value,
                'strategy': trade.strategy
            })
            X.append(features)
            y.append(1 if trade.is_successful else 0)
        
        X = np.array(X)
        y = np.array(y)
        
        # 標準化
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        
        # 訓練
        self.model = RandomForestClassifier(n_estimators=100, random_state=42)
        self.model.fit(X_scaled, y)
        
        # 交叉驗證
        scores = cross_val_score(self.model, X_scaled, y, cv=5)
        print(f"✅ 模型訓練完成")
        print(f"   交叉驗證準確率: {scores.mean():.1%} (+/- {scores.std()*2:.1%})")
        
        # 保存模型
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, self.model_path / "model.pkl")
        joblib.dump(self.scaler, self.model_path / "scaler.pkl")
        
        self.is_trained = True
        return True
    
    def predict(self, trade_data: Dict) -> Tuple[float, float]:
        """
        預測交易成功機率
        
        Returns:
            (成功機率, 模型信心)
        """
        if not self.is_trained:
            return 0.5, 0.0
        
        import numpy as np
        
        features = self.prepare_features(trade_data)
        X = np.array([features])
        X_scaled = self.scaler.transform(X)
        
        prob = self.model.predict_proba(X_scaled)[0][1]  # 成功的機率
        
        # 信心度 = 距離 0.5 的距離
        confidence = abs(prob - 0.5) * 2
        
        return prob, confidence


# ============================================================
# 主程式
# ============================================================

def main():
    """測試模式"""
    print("\n" + "=" * 70)
    print("🐋 WHALE PAPER TRADER v1.0 - 測試模式")
    print("=" * 70)
    
    # 初始化
    trader = WhalePaperTrader(use_testnet=True)
    
    # 模擬信號
    test_signals = [
        {
            'strategy': 'ACCUMULATION',
            'direction': 'LONG',
            'entry_price': 91000,
            'take_profit': 92820,
            'stop_loss': 89635,
            'position_size_pct': 0.3,
            'probability': 0.85,
            'confidence': 0.75,
            'obi': 0.345,
            'wpi': 0.562,
            'funding_rate': 0.0001
        },
        {
            'strategy': 'SHORT_SQUEEZE',
            'direction': 'LONG',
            'entry_price': 91500,
            'take_profit': 94000,
            'stop_loss': 90000,
            'position_size_pct': 0.4,
            'probability': 0.72,
            'confidence': 0.68,
            'obi': 0.5,
            'wpi': 0.8,
            'funding_rate': -0.0002
        }
    ]
    
    # 開倉測試
    for signal in test_signals:
        trade = trader.open_trade(signal)
        if trade:
            print(f"✅ 開倉成功: {trade.trade_id}")
    
    # 模擬價格變動
    print("\n模擬價格變動...")
    prices = [91200, 91500, 91800, 92000, 92500, 92820]  # 模擬上漲
    
    for price in prices:
        print(f"  當前價格: ${price:,.2f}")
        closed = trader.update_positions(price)
        for t in closed:
            print(f"  >> 平倉: {t.trade_id} | {t.status.value} | ${t.pnl_usd:+,.2f}")
        time.sleep(0.5)
    
    # 打印報告
    trader.print_report()


if __name__ == "__main__":
    main()
