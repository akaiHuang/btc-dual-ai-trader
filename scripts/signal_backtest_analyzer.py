#!/usr/bin/env python3
"""
🎯 信號回測分析器 v1.0
=======================
分析歷史信號的 MFE/MAE，找出最佳 TP/SL/持倉時間組合

功能:
1. 記錄信號後的走勢 (MFE/MAE)
2. 網格掃描 TP/SL/持倉時間組合
3. 波動率過濾
4. 信號分級 (A/B/C)

使用:
    python scripts/signal_backtest_analyzer.py --analyze
    python scripts/signal_backtest_analyzer.py --live  # 即時收集數據
"""

import os
import sys
import json
import asyncio
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict
import statistics

# 添加項目根目錄到路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════════════════════
# 數據結構
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SignalRecord:
    """信號記錄"""
    timestamp: str                    # 信號時間
    direction: str                    # LONG/SHORT
    entry_price: float                # 進場價格
    signal_strength: float            # 信號強度 (0-1)
    strategy_name: str                # 策略名稱
    
    # 市場數據 (信號時刻)
    obi: float = 0.0                  # Order Book Imbalance
    wpi: float = 0.0                  # Whale Position Index
    volatility_1m: float = 0.0        # 1分鐘波動率
    volatility_5m: float = 0.0        # 5分鐘波動率
    atr_pct: float = 0.0              # ATR%
    
    # 後續走勢 (回測時填充)
    prices_after: List[float] = field(default_factory=list)  # 之後的價格序列
    
    # MFE/MAE (回測計算)
    mfe_5m: float = 0.0               # 5分鐘內最大有利波動
    mae_5m: float = 0.0               # 5分鐘內最大不利波動
    mfe_10m: float = 0.0              # 10分鐘
    mae_10m: float = 0.0
    mfe_30m: float = 0.0              # 30分鐘
    mae_30m: float = 0.0
    
    # 最終結果
    final_pnl_pct: float = 0.0        # 最終盈虧%
    exit_reason: str = ""             # 出場原因


@dataclass 
class BacktestResult:
    """回測結果"""
    tp_pct: float                     # 止盈%
    sl_pct: float                     # 止損%
    max_hold_min: int                 # 最大持倉分鐘
    fee_pct: float                    # 手續費%
    
    # 統計
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    
    avg_win_pct: float = 0.0          # 平均獲利%
    avg_loss_pct: float = 0.0         # 平均虧損%
    avg_pnl_pct: float = 0.0          # 平均盈虧%
    
    expected_value: float = 0.0       # 期望值 E
    profit_factor: float = 0.0        # 盈虧比
    max_drawdown: float = 0.0         # 最大回撤
    
    # 分類統計
    tp_exits: int = 0                 # 止盈出場次數
    sl_exits: int = 0                 # 止損出場次數
    time_exits: int = 0               # 時間出場次數


@dataclass
class SignalGrade:
    """信號分級"""
    grade: str                        # A/B/C
    description: str
    win_rate: float
    avg_mfe: float
    avg_mae: float
    recommended_tp: float
    recommended_sl: float
    trade_count: int


# ═══════════════════════════════════════════════════════════════════════════════
# 信號回測分析器
# ═══════════════════════════════════════════════════════════════════════════════

class SignalBacktestAnalyzer:
    """信號回測分析器"""
    
    DATA_DIR = Path("data/signal_analysis")
    SIGNALS_FILE = DATA_DIR / "collected_signals.json"
    RESULTS_FILE = DATA_DIR / "backtest_results.json"
    
    def __init__(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.signals: List[SignalRecord] = []
        self.results: List[BacktestResult] = []
        
        # 網格掃描參數
        self.tp_grid = [0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.8, 1.0]  # %
        self.sl_grid = [0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]  # %
        self.time_grid = [3, 5, 7, 10, 15, 20, 30]  # 分鐘
        self.fee_pct = 0.04  # 單邊手續費% (of notional)
        
        # 波動率門檻
        self.min_volatility_pct = 0.15  # 最小波動率 (低於此不交易)
        
    def load_signals(self) -> List[SignalRecord]:
        """載入已收集的信號"""
        if self.SIGNALS_FILE.exists():
            with open(self.SIGNALS_FILE, 'r') as f:
                data = json.load(f)
                self.signals = [SignalRecord(**s) for s in data]
                print(f"✅ 載入 {len(self.signals)} 筆信號記錄")
        return self.signals
    
    def save_signals(self):
        """保存信號記錄"""
        with open(self.SIGNALS_FILE, 'w') as f:
            json.dump([asdict(s) for s in self.signals], f, indent=2, default=str)
        print(f"💾 已保存 {len(self.signals)} 筆信號")
    
    def load_from_trade_logs(self, log_dir: str = "logs/whale_paper_trader"):
        """從交易日誌載入信號"""
        log_path = Path(log_dir)
        if not log_path.exists():
            print(f"❌ 日誌目錄不存在: {log_dir}")
            return
        
        trade_files = sorted(log_path.glob("trades_*.json"))
        print(f"📁 找到 {len(trade_files)} 個交易日誌")
        
        for file in trade_files:
            try:
                with open(file, 'r') as f:
                    data = json.load(f)
                
                trades = data.get('trades', [])
                for trade in trades:
                    signal = SignalRecord(
                        timestamp=trade.get('entry_time', ''),
                        direction=trade.get('direction', 'LONG'),
                        entry_price=trade.get('entry_price', 0),
                        signal_strength=trade.get('probability', 0),
                        strategy_name=trade.get('strategy', 'UNKNOWN'),
                        obi=trade.get('obi', 0),
                        wpi=trade.get('wpi', 0),
                        final_pnl_pct=trade.get('pnl_pct', 0),
                        exit_reason=trade.get('status', '')
                    )
                    
                    # 計算 MFE/MAE (如果有最大浮盈/浮虧記錄)
                    if 'max_profit_pct' in trade:
                        signal.mfe_30m = trade['max_profit_pct']
                    if 'max_drawdown_pct' in trade:
                        signal.mae_30m = abs(trade['max_drawdown_pct'])
                    
                    self.signals.append(signal)
                    
            except Exception as e:
                print(f"⚠️ 載入失敗 {file}: {e}")
        
        print(f"✅ 共載入 {len(self.signals)} 筆信號")
        self.save_signals()
    
    def calculate_mfe_mae(self, signal: SignalRecord, prices: List[float], 
                          intervals: List[int] = [5, 10, 30]) -> Dict:
        """
        計算 MFE (Max Favorable Excursion) 和 MAE (Max Adverse Excursion)
        
        Args:
            signal: 信號記錄
            prices: 之後的價格序列 (每分鐘一個)
            intervals: 要計算的時間間隔 (分鐘)
        """
        if not prices:
            return {}
        
        entry = signal.entry_price
        is_long = signal.direction == "LONG"
        
        results = {}
        for mins in intervals:
            # 取該時間範圍內的價格
            price_slice = prices[:mins] if len(prices) >= mins else prices
            
            if not price_slice:
                continue
            
            if is_long:
                # 做多: 上漲是有利，下跌是不利
                max_price = max(price_slice)
                min_price = min(price_slice)
                mfe = (max_price - entry) / entry * 100
                mae = (entry - min_price) / entry * 100
            else:
                # 做空: 下跌是有利，上漲是不利
                max_price = max(price_slice)
                min_price = min(price_slice)
                mfe = (entry - min_price) / entry * 100
                mae = (max_price - entry) / entry * 100
            
            results[f'mfe_{mins}m'] = max(0, mfe)
            results[f'mae_{mins}m'] = max(0, mae)
        
        return results
    
    def simulate_trade(self, signal: SignalRecord, tp_pct: float, sl_pct: float,
                       max_hold_min: int, fee_pct: float = 0.0) -> Tuple[float, str]:
        """
        模擬單筆交易
        
        Args:
            signal: 信號記錄
            tp_pct: 止盈% (槓桿後)
            sl_pct: 止損%
            max_hold_min: 最大持倉分鐘
            fee_pct: 單邊手續費%
        
        Returns:
            (盈虧%, 出場原因)
        """
        # 使用 MFE/MAE 來判斷出場
        # 假設 50X 槓桿，價格變動 = 盈虧% / 50
        leverage = 50
        
        # 價格變動閾值
        tp_price_pct = tp_pct / leverage
        sl_price_pct = sl_pct / leverage
        
        is_long = signal.direction == "LONG"
        
        # 檢查各時間點的 MFE/MAE
        time_points = [
            (5, signal.mfe_5m, signal.mae_5m),
            (10, signal.mfe_10m, signal.mae_10m),
            (30, signal.mfe_30m, signal.mae_30m),
        ]
        
        fee_roe_pct = fee_pct * 2 * leverage  # round-trip ROE 影響

        for mins, mfe, mae in time_points:
            if mins > max_hold_min:
                break
            
            # 止盈觸發 (MFE >= TP)
            if mfe >= tp_price_pct:
                pnl = tp_pct - fee_roe_pct
                return pnl, "TP"
            
            # 止損觸發 (MAE >= SL)
            if mae >= sl_price_pct:
                pnl = -sl_pct - fee_roe_pct
                return pnl, "SL"
        
        # 時間到了，按最終價格出場
        # 用 30 分鐘的 MFE/MAE 估算最終價格
        if signal.mfe_30m > signal.mae_30m:
            # 最終偏向有利
            final_move = (signal.mfe_30m - signal.mae_30m) / 2
            pnl = final_move * leverage - fee_roe_pct
        else:
            # 最終偏向不利
            final_move = (signal.mae_30m - signal.mfe_30m) / 2
            pnl = -final_move * leverage - fee_roe_pct
        
        return pnl, "TIME"
    
    def grid_search(self, signals: List[SignalRecord] = None) -> List[BacktestResult]:
        """
        網格掃描所有 TP/SL/時間組合
        """
        if signals is None:
            signals = self.signals
        
        if not signals:
            print("❌ 沒有信號數據")
            return []
        
        print(f"\n🔍 網格掃描開始...")
        print(f"   TP 範圍: {self.tp_grid}")
        print(f"   SL 範圍: {self.sl_grid}")
        print(f"   時間範圍: {self.time_grid}")
        print(f"   總組合數: {len(self.tp_grid) * len(self.sl_grid) * len(self.time_grid)}")
        print(f"   信號數量: {len(signals)}")
        
        results = []
        
        for tp in self.tp_grid:
            for sl in self.sl_grid:
                for time_min in self.time_grid:
                    result = self._backtest_params(signals, tp, sl, time_min)
                    results.append(result)
        
        # 按期望值排序
        results.sort(key=lambda x: x.expected_value, reverse=True)
        self.results = results
        
        return results
    
    def _backtest_params(self, signals: List[SignalRecord], 
                         tp_pct: float, sl_pct: float, 
                         max_hold_min: int) -> BacktestResult:
        """回測單一參數組合"""
        result = BacktestResult(
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            max_hold_min=max_hold_min,
            fee_pct=self.fee_pct
        )
        
        pnls = []
        wins_pnl = []
        losses_pnl = []
        
        for signal in signals:
            pnl, exit_reason = self.simulate_trade(
                signal, tp_pct, sl_pct, max_hold_min, self.fee_pct
            )
            
            pnls.append(pnl)
            result.total_trades += 1
            
            if exit_reason == "TP":
                result.tp_exits += 1
            elif exit_reason == "SL":
                result.sl_exits += 1
            else:
                result.time_exits += 1
            
            if pnl > 0:
                result.wins += 1
                wins_pnl.append(pnl)
            else:
                result.losses += 1
                losses_pnl.append(pnl)
        
        # 計算統計
        if result.total_trades > 0:
            result.win_rate = result.wins / result.total_trades * 100
            result.avg_pnl_pct = sum(pnls) / len(pnls)
        
        if wins_pnl:
            result.avg_win_pct = sum(wins_pnl) / len(wins_pnl)
        
        if losses_pnl:
            result.avg_loss_pct = sum(losses_pnl) / len(losses_pnl)
        
        # 期望值 E = (勝率 × 平均獲利) + ((1-勝率) × 平均虧損)
        if result.total_trades > 0:
            win_rate = result.wins / result.total_trades
            result.expected_value = (
                win_rate * result.avg_win_pct + 
                (1 - win_rate) * result.avg_loss_pct
            )
        
        # 盈虧比
        if result.avg_loss_pct != 0:
            result.profit_factor = abs(result.avg_win_pct / result.avg_loss_pct)
        
        # 最大回撤 (簡化計算)
        if pnls:
            cumsum = np.cumsum(pnls)
            running_max = np.maximum.accumulate(cumsum)
            drawdown = running_max - cumsum
            result.max_drawdown = max(drawdown) if len(drawdown) > 0 else 0
        
        return result
    
    def analyze_volatility_filter(self, signals: List[SignalRecord] = None):
        """分析波動率過濾效果"""
        if signals is None:
            signals = self.signals
        
        print("\n" + "="*70)
        print("📊 波動率過濾分析")
        print("="*70)
        
        # 按波動率分組
        low_vol = [s for s in signals if s.volatility_5m < 0.1]
        mid_vol = [s for s in signals if 0.1 <= s.volatility_5m < 0.3]
        high_vol = [s for s in signals if s.volatility_5m >= 0.3]
        
        groups = [
            ("低波動 (<0.1%)", low_vol),
            ("中波動 (0.1-0.3%)", mid_vol),
            ("高波動 (>0.3%)", high_vol),
        ]
        
        for name, group in groups:
            if not group:
                print(f"\n{name}: 無數據")
                continue
            
            wins = sum(1 for s in group if s.final_pnl_pct > 0)
            win_rate = wins / len(group) * 100
            avg_pnl = sum(s.final_pnl_pct for s in group) / len(group)
            avg_mfe = sum(s.mfe_30m for s in group) / len(group)
            avg_mae = sum(s.mae_30m for s in group) / len(group)
            
            print(f"\n{name}:")
            print(f"   交易數: {len(group)}")
            print(f"   勝率: {win_rate:.1f}%")
            print(f"   平均盈虧: {avg_pnl:+.2f}%")
            print(f"   平均 MFE: {avg_mfe:.3f}%")
            print(f"   平均 MAE: {avg_mae:.3f}%")
            
            # 建議
            if avg_mfe < 0.1:
                print(f"   ⚠️ 建議: MFE 太小，這類行情不適合交易")
            elif avg_mae > avg_mfe:
                print(f"   ⚠️ 建議: MAE > MFE，風險太大")
            else:
                print(f"   ✅ 可交易，MFE/MAE 比: {avg_mfe/avg_mae:.2f}")
    
    def grade_signals(self, signals: List[SignalRecord] = None) -> Dict[str, SignalGrade]:
        """
        信號分級 (A/B/C)
        
        A 級: 高勝率 + 高 MFE + 低 MAE
        B 級: 中等表現
        C 級: 低勝率或高風險
        """
        if signals is None:
            signals = self.signals
        
        print("\n" + "="*70)
        print("🏆 信號分級分析")
        print("="*70)
        
        # 按策略分組
        by_strategy = defaultdict(list)
        for s in signals:
            by_strategy[s.strategy_name].append(s)
        
        grades = {}
        
        for strategy, group in by_strategy.items():
            if len(group) < 5:  # 樣本太少跳過
                continue
            
            wins = sum(1 for s in group if s.final_pnl_pct > 0)
            win_rate = wins / len(group) * 100
            avg_mfe = sum(s.mfe_30m for s in group) / len(group)
            avg_mae = sum(s.mae_30m for s in group) / len(group)
            avg_pnl = sum(s.final_pnl_pct for s in group) / len(group)
            
            # 分級邏輯
            if win_rate >= 65 and avg_mfe > avg_mae * 1.5:
                grade = "A"
                desc = "高勝率 + 好的 MFE/MAE 比"
            elif win_rate >= 55 and avg_mfe > avg_mae:
                grade = "B"
                desc = "中等表現，可選擇性交易"
            else:
                grade = "C"
                desc = "不建議交易或需要更嚴格條件"
            
            # 建議 TP/SL
            recommended_tp = round(avg_mfe * 50 * 0.7, 1)  # 70% of MFE
            recommended_sl = round(avg_mae * 50 * 0.8, 1)  # 80% of MAE (給一些緩衝)
            
            grades[strategy] = SignalGrade(
                grade=grade,
                description=desc,
                win_rate=win_rate,
                avg_mfe=avg_mfe,
                avg_mae=avg_mae,
                recommended_tp=recommended_tp,
                recommended_sl=recommended_sl,
                trade_count=len(group)
            )
        
        # 顯示結果
        for strategy, g in sorted(grades.items(), key=lambda x: x[1].grade):
            emoji = "🟢" if g.grade == "A" else "🟡" if g.grade == "B" else "🔴"
            print(f"\n{emoji} [{g.grade}] {strategy}")
            print(f"   交易數: {g.trade_count} | 勝率: {g.win_rate:.1f}%")
            print(f"   MFE: {g.avg_mfe:.3f}% | MAE: {g.avg_mae:.3f}%")
            print(f"   建議 TP: {g.recommended_tp:.1f}% | SL: {g.recommended_sl:.1f}%")
            print(f"   {g.description}")
        
        return grades
    
    def print_best_results(self, top_n: int = 20):
        """顯示最佳參數組合"""
        if not self.results:
            print("❌ 請先執行 grid_search()")
            return
        
        print("\n" + "="*80)
        print("🏆 最佳參數組合 (按期望值排序)")
        print("="*80)
        print(f"{'排名':<4} {'TP%':<6} {'SL%':<6} {'時間':<6} {'勝率':<8} {'期望值':<10} {'盈虧比':<8} {'TP出場':<8} {'SL出場':<8}")
        print("-"*80)
        
        for i, r in enumerate(self.results[:top_n], 1):
            # 跳過負期望值
            if r.expected_value < 0:
                continue
            
            print(f"{i:<4} {r.tp_pct:<6.1f} {r.sl_pct:<6.1f} {r.max_hold_min:<6} "
                  f"{r.win_rate:<8.1f} {r.expected_value:<10.3f} {r.profit_factor:<8.2f} "
                  f"{r.tp_exits:<8} {r.sl_exits:<8}")
        
        # 最佳推薦
        if self.results:
            best = self.results[0]
            print("\n" + "="*80)
            print("⭐ 最佳推薦參數")
            print("="*80)
            print(f"   止盈: {best.tp_pct:.1f}%")
            print(f"   止損: {best.sl_pct:.1f}%")
            print(f"   最大持倉: {best.max_hold_min} 分鐘")
            print(f"   期望值: {best.expected_value:.3f}%")
            print(f"   勝率: {best.win_rate:.1f}%")
            print(f"   盈虧比: {best.profit_factor:.2f}")
    
    def print_negative_combinations(self):
        """顯示負期望值組合 (應該避免)"""
        if not self.results:
            return
        
        negative = [r for r in self.results if r.expected_value < 0]
        
        if not negative:
            print("\n✅ 沒有負期望值組合")
            return
        
        print("\n" + "="*80)
        print("⚠️ 應避免的參數組合 (負期望值)")
        print("="*80)
        
        # 按期望值排序 (最差的在前)
        negative.sort(key=lambda x: x.expected_value)
        
        for r in negative[:10]:
            print(f"   TP={r.tp_pct:.1f}% SL={r.sl_pct:.1f}% Time={r.max_hold_min}min "
                  f"→ E={r.expected_value:.3f}% (勝率{r.win_rate:.1f}%)")
    
    def export_config(self, output_file: str = "config/optimal_tp_sl.json"):
        """導出最佳配置"""
        if not self.results:
            print("❌ 請先執行 grid_search()")
            return
        
        best = self.results[0]
        
        config = {
            "optimal_params": {
                "take_profit_pct": best.tp_pct,
                "stop_loss_pct": best.sl_pct,
                "max_hold_minutes": best.max_hold_min,
                "expected_value": best.expected_value,
                "win_rate": best.win_rate,
                "profit_factor": best.profit_factor,
            },
            "volatility_filter": {
                "min_volatility_pct": self.min_volatility_pct,
                "description": "低於此波動率不交易"
            },
            "analysis_date": datetime.now().isoformat(),
            "total_signals_analyzed": len(self.signals),
        }
        
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"\n💾 配置已導出到: {output_file}")


# ═══════════════════════════════════════════════════════════════════════════════
# 主程式
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="信號回測分析器")
    parser.add_argument("--analyze", action="store_true", help="分析已收集的信號")
    parser.add_argument("--load-logs", action="store_true", help="從交易日誌載入信號")
    parser.add_argument("--export", action="store_true", help="導出最佳配置")
    
    args = parser.parse_args()
    
    analyzer = SignalBacktestAnalyzer()
    
    if args.load_logs:
        analyzer.load_from_trade_logs()
    else:
        analyzer.load_signals()
    
    if not analyzer.signals:
        print("\n⚠️ 沒有信號數據，請先執行:")
        print("   python scripts/signal_backtest_analyzer.py --load-logs")
        return
    
    if args.analyze or True:  # 預設執行分析
        # 1. 網格掃描
        analyzer.grid_search()
        
        # 2. 顯示最佳結果
        analyzer.print_best_results()
        
        # 3. 顯示應避免的組合
        analyzer.print_negative_combinations()
        
        # 4. 波動率分析
        analyzer.analyze_volatility_filter()
        
        # 5. 信號分級
        analyzer.grade_signals()
    
    if args.export:
        analyzer.export_config()


if __name__ == "__main__":
    main()
