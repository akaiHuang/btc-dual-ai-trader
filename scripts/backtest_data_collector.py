#!/usr/bin/env python3
"""
📊 回測數據收集器 (Backtest Data Collector)

統一收集和儲存交易系統的所有數據，方便未來回測使用。

數據類型:
1. 價格數據 (OHLCV + 訂單簿)
2. 交易記錄 (進出場、PnL)
3. 信號數據 (六維、OBI、動能等)
4. 終端機輸出日誌
5. 系統狀態快照

使用方式:
    collector = BacktestDataCollector(session_id="20251211_120000")
    collector.record_price(price=92000, obi=0.05, ...)
    collector.record_trade(trade_data)
    collector.record_signal(signal_data)
    collector.save()  # 定期保存
"""

import json
import os
import gzip
import threading
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Any
from collections import deque
import time


@dataclass
class PriceSnapshot:
    """價格快照"""
    timestamp: str
    price: float
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0
    obi: float = 0.0                    # Order Book Imbalance
    volume_1m: float = 0.0              # 1分鐘成交量
    buy_volume: float = 0.0             # 買入成交量
    sell_volume: float = 0.0            # 賣出成交量
    funding_rate: float = 0.0           # 資金費率
    open_interest: float = 0.0          # 未平倉量
    price_change_1m: float = 0.0        # 1分鐘價格變化%
    price_change_5m: float = 0.0        # 5分鐘價格變化%


@dataclass  
class SignalSnapshot:
    """信號快照"""
    timestamp: str
    signal_type: str                    # LONG_READY, SHORT_READY, NEUTRAL
    direction: str                      # LONG, SHORT, NONE
    reason: str
    price: float
    
    # 六維系統
    six_dim_long_score: int = 0
    six_dim_short_score: int = 0
    fast_dir: str = ""
    medium_dir: str = ""
    slow_dir: str = ""
    obi_dir: str = ""
    momentum_dir: str = ""
    volume_dir: str = ""
    
    # MTF 指標
    rsi_1m: float = 0.0
    rsi_5m: float = 0.0
    rsi_15m: float = 0.0
    
    # 市場狀態
    obi: float = 0.0
    regime: str = ""
    strategy: str = ""
    
    # 對齊秒數
    long_align_sec: float = 0.0
    short_align_sec: float = 0.0


@dataclass
class TradeRecord:
    """交易記錄"""
    trade_id: str
    timestamp: str
    direction: str
    strategy: str
    
    # 進場
    entry_price: float
    entry_time: str
    leverage: int
    position_size_usdt: float
    position_size_btc: float
    
    # 進場時的指標
    entry_obi: float = 0.0
    entry_six_dim_long: int = 0
    entry_six_dim_short: int = 0
    entry_probability: float = 0.0
    entry_confidence: float = 0.0
    
    # 出場
    exit_price: float = 0.0
    exit_time: str = ""
    exit_reason: str = ""
    
    # 結果
    pnl_pct: float = 0.0
    pnl_usdt: float = 0.0
    net_pnl_usdt: float = 0.0
    fee_usdt: float = 0.0
    hold_seconds: float = 0.0
    
    # 過程中最大值
    max_profit_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    max_price: float = 0.0
    min_price: float = 0.0


@dataclass
class TerminalLog:
    """終端機日誌"""
    timestamp: str
    level: str                          # INFO, WARNING, ERROR, TRADE, SIGNAL
    message: str
    data: Optional[Dict] = None


@dataclass
class SystemState:
    """系統狀態快照"""
    timestamp: str
    mode: str                           # PAPER, REAL, TESTNET
    active_card: str
    daily_trades: int
    daily_pnl_usdt: float
    daily_pnl_pct: float
    total_pnl_usdt: float
    consecutive_wins: int
    consecutive_losses: int
    current_position: Optional[Dict] = None
    last_trade_time: str = ""


class BacktestDataCollector:
    """
    回測數據收集器
    
    統一管理所有回測相關數據的收集、儲存和載入。
    支援實時寫入和批量保存。
    """
    
    # 數據保存間隔 (秒)
    SAVE_INTERVAL = 60
    # 價格數據最大保留數量
    MAX_PRICE_SNAPSHOTS = 86400  # 1天 (1秒1筆)
    # 信號數據最大保留數量
    MAX_SIGNAL_SNAPSHOTS = 10000
    
    def __init__(
        self,
        session_id: str = None,
        data_dir: str = "data/backtest_sessions",
        auto_save: bool = True,
        compress: bool = True
    ):
        """
        初始化收集器
        
        Args:
            session_id: 會話 ID (預設使用時間戳)
            data_dir: 數據儲存目錄
            auto_save: 是否自動定期保存
            compress: 是否壓縮保存
        """
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.data_dir = Path(data_dir)
        self.session_dir = self.data_dir / self.session_id
        self.auto_save = auto_save
        self.compress = compress
        
        # 數據存儲
        self.prices: deque = deque(maxlen=self.MAX_PRICE_SNAPSHOTS)
        self.signals: deque = deque(maxlen=self.MAX_SIGNAL_SNAPSHOTS)
        self.trades: List[TradeRecord] = []
        self.terminal_logs: List[TerminalLog] = []
        self.system_states: List[SystemState] = []
        
        # 會話元數據
        self.metadata = {
            "session_id": self.session_id,
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "version": "1.0",
            "card_used": None,
            "mode": None,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl_pct": 0.0,
            "total_pnl_usdt": 0.0,
            "price_range": {"min": None, "max": None},
        }
        
        # 線程鎖
        self._lock = threading.Lock()
        self._save_thread = None
        self._running = False
        
        # 確保目錄存在
        self.session_dir.mkdir(parents=True, exist_ok=True)
        
        # 啟動自動保存
        if auto_save:
            self._start_auto_save()
        
        print(f"📊 BacktestDataCollector 初始化完成")
        print(f"   會話 ID: {self.session_id}")
        print(f"   儲存路徑: {self.session_dir}")
    
    def _start_auto_save(self):
        """啟動自動保存線程"""
        self._running = True
        self._save_thread = threading.Thread(target=self._auto_save_loop, daemon=True)
        self._save_thread.start()
    
    def _auto_save_loop(self):
        """自動保存循環"""
        while self._running:
            time.sleep(self.SAVE_INTERVAL)
            if self._running:
                self.save_incremental()
    
    def record_price(
        self,
        price: float,
        bid: float = 0.0,
        ask: float = 0.0,
        obi: float = 0.0,
        volume_1m: float = 0.0,
        buy_volume: float = 0.0,
        sell_volume: float = 0.0,
        funding_rate: float = 0.0,
        open_interest: float = 0.0,
        price_change_1m: float = 0.0,
        price_change_5m: float = 0.0,
        timestamp: str = None
    ):
        """記錄價格快照"""
        snapshot = PriceSnapshot(
            timestamp=timestamp or datetime.now().isoformat(),
            price=price,
            bid=bid,
            ask=ask,
            spread=ask - bid if ask and bid else 0,
            obi=obi,
            volume_1m=volume_1m,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            funding_rate=funding_rate,
            open_interest=open_interest,
            price_change_1m=price_change_1m,
            price_change_5m=price_change_5m
        )
        
        with self._lock:
            self.prices.append(snapshot)
            
            # 更新價格範圍
            if self.metadata["price_range"]["min"] is None or price < self.metadata["price_range"]["min"]:
                self.metadata["price_range"]["min"] = price
            if self.metadata["price_range"]["max"] is None or price > self.metadata["price_range"]["max"]:
                self.metadata["price_range"]["max"] = price
    
    def record_signal(
        self,
        signal_type: str,
        direction: str,
        reason: str,
        price: float,
        six_dim: Dict = None,
        mtf: Dict = None,
        market: Dict = None,
        alignment: Dict = None,
        timestamp: str = None
    ):
        """記錄信號快照"""
        six_dim = six_dim or {}
        mtf = mtf or {}
        market = market or {}
        alignment = alignment or {}
        
        snapshot = SignalSnapshot(
            timestamp=timestamp or datetime.now().isoformat(),
            signal_type=signal_type,
            direction=direction,
            reason=reason,
            price=price,
            six_dim_long_score=six_dim.get("long_score", 0),
            six_dim_short_score=six_dim.get("short_score", 0),
            fast_dir=six_dim.get("fast_dir", ""),
            medium_dir=six_dim.get("medium_dir", ""),
            slow_dir=six_dim.get("slow_dir", ""),
            obi_dir=six_dim.get("obi_dir", ""),
            momentum_dir=six_dim.get("momentum_dir", ""),
            volume_dir=six_dim.get("volume_dir", ""),
            rsi_1m=mtf.get("rsi_1m", 0),
            rsi_5m=mtf.get("rsi_5m", 0),
            rsi_15m=mtf.get("rsi_15m", 0),
            obi=market.get("obi", 0),
            regime=market.get("regime", ""),
            strategy=market.get("strategy", ""),
            long_align_sec=alignment.get("long_sec", 0),
            short_align_sec=alignment.get("short_sec", 0)
        )
        
        with self._lock:
            self.signals.append(snapshot)
    
    def record_trade(self, trade_data: Dict) -> TradeRecord:
        """記錄交易"""
        record = TradeRecord(
            trade_id=trade_data.get("trade_id", f"T_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
            timestamp=trade_data.get("timestamp", datetime.now().isoformat()),
            direction=trade_data.get("direction", "UNKNOWN"),
            strategy=trade_data.get("strategy", "UNKNOWN"),
            entry_price=trade_data.get("entry_price", 0),
            entry_time=trade_data.get("entry_time", ""),
            leverage=trade_data.get("leverage", 50),
            position_size_usdt=trade_data.get("position_size_usdt", 0),
            position_size_btc=trade_data.get("position_size_btc", 0),
            entry_obi=trade_data.get("obi", trade_data.get("entry_obi", 0)),
            entry_six_dim_long=trade_data.get("six_dim_long", 0),
            entry_six_dim_short=trade_data.get("six_dim_short", 0),
            entry_probability=trade_data.get("probability", 0),
            entry_confidence=trade_data.get("confidence", 0),
            exit_price=trade_data.get("exit_price", 0),
            exit_time=trade_data.get("exit_time", ""),
            exit_reason=trade_data.get("exit_reason", trade_data.get("status", "")),
            pnl_pct=trade_data.get("pnl_pct", 0),
            pnl_usdt=trade_data.get("pnl_usdt", 0),
            net_pnl_usdt=trade_data.get("net_pnl_usdt", trade_data.get("pnl_usdt", 0)),
            fee_usdt=trade_data.get("fee_usdt", 0),
            hold_seconds=trade_data.get("hold_seconds", 0),
            max_profit_pct=trade_data.get("max_profit_pct", 0),
            max_drawdown_pct=trade_data.get("max_drawdown_pct", 0),
            max_price=trade_data.get("max_price", 0),
            min_price=trade_data.get("min_price", 0)
        )
        
        with self._lock:
            self.trades.append(record)
            
            # 更新統計
            self.metadata["total_trades"] = len(self.trades)
            if record.pnl_pct > 0:
                self.metadata["winning_trades"] += 1
            else:
                self.metadata["losing_trades"] += 1
            self.metadata["total_pnl_pct"] += record.pnl_pct
            self.metadata["total_pnl_usdt"] += record.net_pnl_usdt
        
        return record
    
    def record_terminal_log(
        self,
        message: str,
        level: str = "INFO",
        data: Dict = None,
        timestamp: str = None
    ):
        """記錄終端機日誌"""
        log = TerminalLog(
            timestamp=timestamp or datetime.now().isoformat(),
            level=level,
            message=message,
            data=data
        )
        
        with self._lock:
            self.terminal_logs.append(log)
    
    def record_system_state(
        self,
        mode: str,
        active_card: str,
        daily_trades: int,
        daily_pnl_usdt: float,
        daily_pnl_pct: float,
        total_pnl_usdt: float,
        consecutive_wins: int = 0,
        consecutive_losses: int = 0,
        current_position: Dict = None,
        last_trade_time: str = "",
        timestamp: str = None
    ):
        """記錄系統狀態"""
        state = SystemState(
            timestamp=timestamp or datetime.now().isoformat(),
            mode=mode,
            active_card=active_card,
            daily_trades=daily_trades,
            daily_pnl_usdt=daily_pnl_usdt,
            daily_pnl_pct=daily_pnl_pct,
            total_pnl_usdt=total_pnl_usdt,
            consecutive_wins=consecutive_wins,
            consecutive_losses=consecutive_losses,
            current_position=current_position,
            last_trade_time=last_trade_time
        )
        
        with self._lock:
            self.system_states.append(state)
            
            # 更新元數據
            if self.metadata["mode"] is None:
                self.metadata["mode"] = mode
            if self.metadata["card_used"] is None:
                self.metadata["card_used"] = active_card
    
    def save_incremental(self):
        """增量保存 (只保存新數據)"""
        try:
            with self._lock:
                # 保存最新的價格數據 (最近 1000 筆)
                if self.prices:
                    self._save_json(
                        list(self.prices)[-1000:],
                        "prices_latest.json"
                    )
                
                # 保存所有交易
                if self.trades:
                    self._save_json(
                        [asdict(t) for t in self.trades],
                        "trades.json"
                    )
                
                # 保存最新的信號 (最近 500 筆)
                if self.signals:
                    self._save_json(
                        [asdict(s) for s in list(self.signals)[-500:]],
                        "signals_latest.json"
                    )
                
                # 保存元數據
                self.metadata["end_time"] = datetime.now().isoformat()
                self._save_json(self.metadata, "metadata.json")
                
        except Exception as e:
            print(f"⚠️ 增量保存失敗: {e}")
    
    def save_full(self):
        """完整保存所有數據"""
        try:
            with self._lock:
                # 更新元數據
                self.metadata["end_time"] = datetime.now().isoformat()
                
                # 構建完整數據
                full_data = {
                    "metadata": self.metadata,
                    "prices": [asdict(p) for p in self.prices],
                    "signals": [asdict(s) for s in self.signals],
                    "trades": [asdict(t) for t in self.trades],
                    "terminal_logs": [asdict(l) for l in self.terminal_logs],
                    "system_states": [asdict(s) for s in self.system_states]
                }
                
                # 保存完整數據
                filename = f"session_{self.session_id}_full.json"
                if self.compress:
                    filename += ".gz"
                    filepath = self.session_dir / filename
                    with gzip.open(filepath, 'wt', encoding='utf-8') as f:
                        json.dump(full_data, f, ensure_ascii=False)
                else:
                    filepath = self.session_dir / filename
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(full_data, f, indent=2, ensure_ascii=False)
                
                print(f"💾 完整數據已保存: {filepath}")
                print(f"   價格快照: {len(self.prices)}")
                print(f"   信號記錄: {len(self.signals)}")
                print(f"   交易記錄: {len(self.trades)}")
                print(f"   終端日誌: {len(self.terminal_logs)}")
                
                return filepath
                
        except Exception as e:
            print(f"❌ 完整保存失敗: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _save_json(self, data: Any, filename: str):
        """保存 JSON 文件"""
        filepath = self.session_dir / filename
        
        # 轉換 dataclass 為 dict
        if isinstance(data, list) and data and hasattr(data[0], '__dataclass_fields__'):
            data = [asdict(d) for d in data]
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def stop(self):
        """停止收集器"""
        self._running = False
        if self._save_thread and self._save_thread.is_alive():
            self._save_thread.join(timeout=5)
        
        # 最終完整保存
        return self.save_full()
    
    def get_summary(self) -> Dict:
        """獲取會話摘要"""
        with self._lock:
            win_rate = 0
            if self.metadata["total_trades"] > 0:
                win_rate = self.metadata["winning_trades"] / self.metadata["total_trades"] * 100
            
            return {
                "session_id": self.session_id,
                "duration": self._calculate_duration(),
                "total_trades": self.metadata["total_trades"],
                "win_rate": f"{win_rate:.1f}%",
                "total_pnl_pct": f"{self.metadata['total_pnl_pct']:+.2f}%",
                "total_pnl_usdt": f"${self.metadata['total_pnl_usdt']:+.2f}",
                "price_range": self.metadata["price_range"],
                "data_points": {
                    "prices": len(self.prices),
                    "signals": len(self.signals),
                    "logs": len(self.terminal_logs)
                }
            }
    
    def _calculate_duration(self) -> str:
        """計算會話時長"""
        try:
            start = datetime.fromisoformat(self.metadata["start_time"])
            end = datetime.now()
            delta = end - start
            hours = delta.total_seconds() / 3600
            if hours < 1:
                return f"{delta.total_seconds() / 60:.1f} 分鐘"
            return f"{hours:.1f} 小時"
        except:
            return "N/A"
    
    @classmethod
    def load_session(cls, session_path: str) -> Dict:
        """載入已保存的會話數據"""
        path = Path(session_path)
        
        # 嘗試載入完整數據
        for pattern in ["session_*_full.json.gz", "session_*_full.json"]:
            matches = list(path.glob(pattern))
            if matches:
                filepath = matches[0]
                if filepath.suffix == ".gz":
                    with gzip.open(filepath, 'rt', encoding='utf-8') as f:
                        return json.load(f)
                else:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        return json.load(f)
        
        # 嘗試載入分散的數據文件
        data = {}
        for filename in ["metadata.json", "trades.json", "prices_latest.json", "signals_latest.json"]:
            filepath = path / filename
            if filepath.exists():
                with open(filepath, 'r', encoding='utf-8') as f:
                    key = filename.replace(".json", "").replace("_latest", "")
                    data[key] = json.load(f)
        
        return data


# 全局收集器實例 (方便主程式使用)
_global_collector: Optional[BacktestDataCollector] = None


def get_collector() -> Optional[BacktestDataCollector]:
    """獲取全局收集器"""
    return _global_collector


def init_collector(
    session_id: str = None,
    data_dir: str = "data/backtest_sessions",
    **kwargs
) -> BacktestDataCollector:
    """初始化全局收集器"""
    global _global_collector
    _global_collector = BacktestDataCollector(
        session_id=session_id,
        data_dir=data_dir,
        **kwargs
    )
    return _global_collector


def stop_collector() -> Optional[str]:
    """停止全局收集器並保存"""
    global _global_collector
    if _global_collector:
        result = _global_collector.stop()
        _global_collector = None
        return result
    return None


if __name__ == "__main__":
    # 測試
    print("=" * 60)
    print("📊 BacktestDataCollector 測試")
    print("=" * 60)
    
    collector = BacktestDataCollector(session_id="test_session")
    
    # 模擬記錄數據
    for i in range(10):
        collector.record_price(
            price=92000 + i * 10,
            obi=0.05 * (i % 3 - 1),
            volume_1m=1000000
        )
        
        collector.record_signal(
            signal_type="LONG_READY" if i % 2 == 0 else "SHORT_READY",
            direction="LONG" if i % 2 == 0 else "SHORT",
            reason="Test signal",
            price=92000 + i * 10,
            six_dim={"long_score": 6, "short_score": 4}
        )
    
    # 模擬交易
    collector.record_trade({
        "trade_id": "TEST_001",
        "direction": "LONG",
        "strategy": "SIX_DIM_LONG",
        "entry_price": 92000,
        "entry_time": datetime.now().isoformat(),
        "leverage": 30,
        "position_size_usdt": 100,
        "position_size_btc": 0.001,
        "exit_price": 92100,
        "exit_time": datetime.now().isoformat(),
        "pnl_pct": 3.26,
        "pnl_usdt": 3.26,
        "hold_seconds": 45
    })
    
    collector.record_terminal_log("測試日誌訊息", level="INFO")
    
    # 獲取摘要
    summary = collector.get_summary()
    print(f"\n會話摘要: {json.dumps(summary, indent=2, ensure_ascii=False)}")
    
    # 保存
    result = collector.stop()
    print(f"\n數據已保存至: {result}")
