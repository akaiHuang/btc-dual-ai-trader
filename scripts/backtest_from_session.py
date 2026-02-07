#!/usr/bin/env python3
"""
Backtest from Session Data
==========================
使用 BacktestDataCollector 收集的數據進行策略回測

用法:
    python scripts/backtest_from_session.py <session_dir> [--card <card_name>]
    python scripts/backtest_from_session.py data/backtest_sessions/test_card_v2
"""

import json
import gzip
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict


@dataclass
class SimulatedTrade:
    """模擬交易記錄"""
    entry_time: str
    entry_price: float
    direction: str
    exit_time: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_pct: float = 0.0
    net_pnl_usdt: float = 0.0
    hold_seconds: float = 0.0
    signal_type: str = ""
    obi: float = 0.0
    six_dim_long: int = 0
    six_dim_short: int = 0


class SessionBacktester:
    """基於會話數據的回測器"""
    
    def __init__(self, session_dir: str, card_config: Dict = None):
        """
        初始化回測器
        
        Args:
            session_dir: 會話數據目錄
            card_config: 卡片配置 (可選)
        """
        self.session_dir = Path(session_dir)
        self.card_config = card_config or self._default_card()
        
        # 載入數據
        self.data = self._load_session_data()
        
        # 回測結果
        self.simulated_trades: List[SimulatedTrade] = []
        
    def _default_card(self) -> Dict:
        """預設卡片配置"""
        return {
            "name": "default",
            "min_trade_interval_sec": 10,
            "leverage": 50,
            "position_size_usdt": 1000,
            "entry_conditions": {
                "min_signal_score": 3,
                "allowed_directions": ["LONG", "SHORT"],
                "obi_filter": {
                    "enabled": True,
                    "long_min": -0.2,
                    "short_max": 0.2
                }
            },
            "exit_conditions": {
                "take_profit_pct": 0.06,
                "stop_loss_pct": 0.03,
                "max_hold_seconds": 1800
            }
        }
    
    def _load_session_data(self) -> Dict:
        """載入會話數據"""
        # 尋找完整數據檔案
        full_files = list(self.session_dir.glob("session_*_full.json*"))
        if not full_files:
            raise FileNotFoundError(f"找不到會話數據: {self.session_dir}")
        
        data_file = full_files[0]
        
        # 根據副檔名決定開啟方式
        if str(data_file).endswith('.gz'):
            with gzip.open(data_file, 'rt', encoding='utf-8') as f:
                return json.load(f)
        else:
            with open(data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
    
    def run_backtest(self, strategy: str = "replay") -> Dict:
        """
        執行回測
        
        Args:
            strategy: 回測策略
                - "replay": 重播原始交易
                - "signal_filter": 基於信號過濾
                - "obi_filter": 基於 OBI 過濾
        
        Returns:
            回測結果
        """
        if strategy == "replay":
            return self._replay_original_trades()
        elif strategy == "signal_filter":
            return self._backtest_signal_filter()
        elif strategy == "obi_filter":
            return self._backtest_obi_filter()
        else:
            raise ValueError(f"未知策略: {strategy}")
    
    def _replay_original_trades(self) -> Dict:
        """重播原始交易"""
        trades = self.data.get("trades", [])
        
        total_pnl = 0
        wins = 0
        losses = 0
        
        for t in trades:
            pnl = t.get("pnl_pct", 0)
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            else:
                losses += 1
        
        win_rate = wins / len(trades) * 100 if trades else 0
        
        return {
            "strategy": "replay",
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl_pct": total_pnl,
            "avg_pnl_pct": total_pnl / len(trades) if trades else 0,
            "trades": trades
        }
    
    def _backtest_signal_filter(self) -> Dict:
        """基於信號過濾的回測"""
        signals = self.data.get("signals", [])
        trades = self.data.get("trades", [])
        
        # 找出符合條件的信號
        min_score = self.card_config["entry_conditions"]["min_signal_score"]
        allowed_dirs = self.card_config["entry_conditions"]["allowed_directions"]
        
        filtered_signals = []
        for s in signals:
            direction = s.get("direction", "NONE")
            if direction not in allowed_dirs:
                continue
                
            if direction == "LONG":
                score = s.get("six_dim_long_score", 0)
            else:
                score = s.get("six_dim_short_score", 0)
            
            if score >= min_score:
                filtered_signals.append(s)
        
        # 比對原始交易，計算如果只在這些信號進場的結果
        # (簡化版: 假設每個信號都會進場)
        
        return {
            "strategy": "signal_filter",
            "original_signals": len(signals),
            "filtered_signals": len(filtered_signals),
            "filter_rate": len(filtered_signals) / len(signals) * 100 if signals else 0,
            "note": "信號過濾模式 - 需要更完整的價格數據進行精確回測"
        }
    
    def _backtest_obi_filter(self) -> Dict:
        """基於 OBI 過濾的回測"""
        trades = self.data.get("trades", [])
        obi_config = self.card_config["entry_conditions"]["obi_filter"]
        
        if not obi_config.get("enabled", False):
            return self._replay_original_trades()
        
        long_min = obi_config.get("long_min", -0.5)
        short_max = obi_config.get("short_max", 0.5)
        
        filtered_trades = []
        for t in trades:
            direction = t.get("direction", "")
            obi = t.get("entry_obi", 0)
            
            # OBI 過濾邏輯
            if direction == "LONG" and obi >= long_min:
                filtered_trades.append(t)
            elif direction == "SHORT" and obi <= short_max:
                filtered_trades.append(t)
        
        # 計算過濾後的結果
        total_pnl = sum(t.get("pnl_pct", 0) for t in filtered_trades)
        wins = sum(1 for t in filtered_trades if t.get("pnl_pct", 0) > 0)
        losses = len(filtered_trades) - wins
        win_rate = wins / len(filtered_trades) * 100 if filtered_trades else 0
        
        return {
            "strategy": "obi_filter",
            "original_trades": len(trades),
            "filtered_trades": len(filtered_trades),
            "filter_rate": len(filtered_trades) / len(trades) * 100 if trades else 0,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl_pct": total_pnl,
            "avg_pnl_pct": total_pnl / len(filtered_trades) if filtered_trades else 0
        }
    
    def analyze_by_direction(self) -> Dict:
        """按方向分析交易"""
        trades = self.data.get("trades", [])
        
        long_trades = [t for t in trades if t.get("direction") == "LONG"]
        short_trades = [t for t in trades if t.get("direction") == "SHORT"]
        
        def calc_stats(trade_list: List) -> Dict:
            if not trade_list:
                return {"count": 0, "wins": 0, "win_rate": 0, "total_pnl": 0, "avg_pnl": 0}
            
            wins = sum(1 for t in trade_list if t.get("pnl_pct", 0) > 0)
            total_pnl = sum(t.get("pnl_pct", 0) for t in trade_list)
            
            return {
                "count": len(trade_list),
                "wins": wins,
                "losses": len(trade_list) - wins,
                "win_rate": wins / len(trade_list) * 100,
                "total_pnl": total_pnl,
                "avg_pnl": total_pnl / len(trade_list)
            }
        
        return {
            "LONG": calc_stats(long_trades),
            "SHORT": calc_stats(short_trades)
        }
    
    def analyze_by_hold_time(self) -> Dict:
        """按持倉時間分析"""
        trades = self.data.get("trades", [])
        
        time_buckets = {
            "<10s": [],
            "10-30s": [],
            "30-60s": [],
            "60-300s": [],
            ">300s": []
        }
        
        for t in trades:
            hold = t.get("hold_seconds", 0)
            if hold < 10:
                time_buckets["<10s"].append(t)
            elif hold < 30:
                time_buckets["10-30s"].append(t)
            elif hold < 60:
                time_buckets["30-60s"].append(t)
            elif hold < 300:
                time_buckets["60-300s"].append(t)
            else:
                time_buckets[">300s"].append(t)
        
        result = {}
        for bucket, trades_in_bucket in time_buckets.items():
            if trades_in_bucket:
                wins = sum(1 for t in trades_in_bucket if t.get("pnl_pct", 0) > 0)
                total_pnl = sum(t.get("pnl_pct", 0) for t in trades_in_bucket)
                result[bucket] = {
                    "count": len(trades_in_bucket),
                    "win_rate": wins / len(trades_in_bucket) * 100,
                    "total_pnl": total_pnl,
                    "avg_pnl": total_pnl / len(trades_in_bucket)
                }
            else:
                result[bucket] = {"count": 0, "win_rate": 0, "total_pnl": 0, "avg_pnl": 0}
        
        return result
    
    def generate_report(self) -> str:
        """生成回測報告"""
        metadata = self.data.get("metadata", {})
        
        replay = self._replay_original_trades()
        direction_analysis = self.analyze_by_direction()
        hold_time_analysis = self.analyze_by_hold_time()
        
        report = []
        report.append("=" * 60)
        report.append("📊 SESSION BACKTEST REPORT")
        report.append("=" * 60)
        report.append("")
        
        # 會話資訊
        report.append("📁 會話資訊:")
        report.append(f"   ID: {metadata.get('session_id', 'N/A')}")
        report.append(f"   開始: {metadata.get('start_time', 'N/A')}")
        report.append(f"   結束: {metadata.get('end_time', 'N/A')}")
        report.append(f"   價格範圍: ${metadata.get('price_range', {}).get('min', 0):,.0f} - ${metadata.get('price_range', {}).get('max', 0):,.0f}")
        report.append("")
        
        # 總體統計
        report.append("📈 總體統計:")
        report.append(f"   總交易數: {replay['total_trades']}")
        report.append(f"   勝率: {replay['win_rate']:.1f}% ({replay['wins']}勝 / {replay['losses']}負)")
        report.append(f"   總 PnL: {replay['total_pnl_pct']:+.2f}%")
        report.append(f"   平均 PnL: {replay['avg_pnl_pct']:+.3f}%")
        report.append("")
        
        # 方向分析
        report.append("🔄 方向分析:")
        for direction, stats in direction_analysis.items():
            report.append(f"   {direction}:")
            report.append(f"      數量: {stats['count']} ({stats['win_rate']:.1f}% 勝率)")
            report.append(f"      PnL: {stats['total_pnl']:+.2f}% (平均 {stats['avg_pnl']:+.3f}%)")
        report.append("")
        
        # 持倉時間分析
        report.append("⏱️ 持倉時間分析:")
        for bucket, stats in hold_time_analysis.items():
            if stats['count'] > 0:
                report.append(f"   {bucket}: {stats['count']} 筆, {stats['win_rate']:.1f}% 勝率, {stats['avg_pnl']:+.3f}% 平均")
        report.append("")
        
        report.append("=" * 60)
        
        return "\n".join(report)


def load_card_config(card_path: str) -> Dict:
    """載入卡片配置"""
    with open(card_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="從會話數據進行回測")
    parser.add_argument("session_dir", help="會話數據目錄")
    parser.add_argument("--card", help="卡片配置檔案路徑", default=None)
    parser.add_argument("--strategy", choices=["replay", "signal_filter", "obi_filter"], 
                       default="replay", help="回測策略")
    parser.add_argument("--output", help="輸出報告檔案", default=None)
    
    args = parser.parse_args()
    
    # 載入卡片配置
    card_config = None
    if args.card:
        card_config = load_card_config(args.card)
        print(f"📋 使用卡片: {card_config.get('name', args.card)}")
    
    # 建立回測器
    print(f"📂 載入會話數據: {args.session_dir}")
    backtester = SessionBacktester(args.session_dir, card_config)
    
    # 執行回測
    print(f"🔄 執行回測 (策略: {args.strategy})")
    result = backtester.run_backtest(args.strategy)
    
    # 生成報告
    report = backtester.generate_report()
    print(report)
    
    # 輸出到檔案
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"\n📄 報告已保存: {args.output}")


if __name__ == "__main__":
    main()
