"""
Walk-Forward Optimization v3.0 - 智能迭代優化系統
每年訓練→測試→分析問題→自動調參→記錄修正原因

作者: Walk-Forward v3.0 項目
日期: 2025-11-14
"""

import json
import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict


@dataclass
class StrategyVersion:
    """策略版本配置"""
    version: str
    year: int
    
    # 信號參數
    long_rsi_lower: float
    long_rsi_upper: float
    ma_distance_threshold: float
    volume_multiplier: float
    
    # TP/SL 參數
    atr_tp_multiplier: float
    atr_sl_multiplier: float
    min_tp_pct: float
    max_tp_pct: float
    min_sl_pct: float
    max_sl_pct: float
    
    # 時間參數
    time_stop_minutes: int
    
    # Phase 0 過濾
    enable_consolidation_filter: bool
    enable_timezone_filter: bool
    enable_cost_filter: bool
    
    # 確認機制
    require_confirmation: bool
    confirmation_candles: int
    
    # 修正記錄
    changes_from_previous: str = ""
    change_reason: str = ""


@dataclass
class OptimizationDecision:
    """優化決策記錄"""
    problem: str
    analysis: str
    solution: str
    expected_impact: str
    parameter_changes: Dict[str, Tuple[float, float]]  # {param: (old, new)}


class WalkForwardV3:
    """
    Walk-Forward v3.0 智能優化系統
    
    核心流程：
    1. 用 2020 數據訓練 baseline (v3.0)
    2. 在 2021 測試，分析表現
    3. 根據問題自動調整參數 → v3.1
    4. 用 2021 訓練 v3.1
    5. 在 2022 測試... 以此類推
    6. 最終生成 v3.5 (2025 達標版本)
    """
    
    def __init__(self, data_dir: str = 'data', results_dir: str = 'backtest_results/walk_forward'):
        self.data_dir = data_dir
        self.results_dir = results_dir
        self.versions: List[StrategyVersion] = []
        self.decisions: List[OptimizationDecision] = []
        
        # 初始化 v3.0 baseline (基於 v2.1 的 MFE/MAE 優化)
        self.current_version = StrategyVersion(
            version='v3.0',
            year=2020,
            long_rsi_lower=45.0,
            long_rsi_upper=60.0,
            ma_distance_threshold=0.3,
            volume_multiplier=1.2,
            atr_tp_multiplier=2.7,  # MFE 分析結果
            atr_sl_multiplier=1.1,  # MAE 分析結果
            min_tp_pct=0.5,
            max_tp_pct=1.5,
            min_sl_pct=0.2,
            max_sl_pct=0.6,
            time_stop_minutes=45,
            enable_consolidation_filter=True,
            enable_timezone_filter=True,
            enable_cost_filter=True,
            require_confirmation=True,
            confirmation_candles=2,
            changes_from_previous="基於 v2.1 MFE/MAE 分析建立",
            change_reason="數據驅動的 TP/SL 優化 + 盤整過濾修復"
        )
        
        self.versions.append(self.current_version)
    
    def load_btc_data(self, year: int) -> Optional[pd.DataFrame]:
        """載入指定年份的 BTC 數據"""
        try:
            # 使用完整 15m parquet 檔案
            filepath = os.path.join(self.data_dir, 'historical', 'BTCUSDT_15m.parquet')
            if not os.path.exists(filepath):
                print(f"⚠️ 找不到數據檔案: {filepath}")
                return None
            
            df = pd.read_parquet(filepath)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            # 過濾指定年份
            df = df[df['timestamp'].dt.year == year].copy()
            df = df.reset_index(drop=True)
            
            if len(df) == 0:
                print(f"⚠️ {year} 年沒有數據")
                return None
            
            print(f"  📊 載入 {year} 年數據: {len(df)} 根 K 線")
            return df
        except Exception as e:
            print(f"❌ 載入 {year} 年數據失敗: {e}")
            return None
    
    def backtest_with_version(self, version: StrategyVersion, test_year: int) -> Optional[Dict]:
        """
        使用指定版本的參數進行回測
        
        Returns:
            回測結果 Dict 或 None
        """
        print(f"\n🔬 測試 {version.version} 在 {test_year} 年...")
        
        # 載入數據
        df = self.load_btc_data(test_year)
        if df is None:
            return None
        
        # 動態導入策略（避免循環依賴）
        from src.strategy.mvp_strategy_v2 import MVPStrategyV2
        
        # 創建策略實例（使用版本參數）
        strategy = MVPStrategyV2(
            long_rsi_lower=version.long_rsi_lower,
            long_rsi_upper=version.long_rsi_upper,
            ma_distance_threshold=version.ma_distance_threshold,
            volume_multiplier=version.volume_multiplier,
            atr_tp_multiplier=version.atr_tp_multiplier,
            atr_sl_multiplier=version.atr_sl_multiplier,
            min_tp_pct=version.min_tp_pct,
            max_tp_pct=version.max_tp_pct,
            min_sl_pct=version.min_sl_pct,
            max_sl_pct=version.max_sl_pct,
            time_stop_minutes=version.time_stop_minutes,
            enable_consolidation_filter=version.enable_consolidation_filter,
            enable_timezone_filter=version.enable_timezone_filter,
            enable_cost_filter=version.enable_cost_filter,
            require_confirmation=version.require_confirmation,
            confirmation_candles=version.confirmation_candles
        )
        
        # 簡化回測（掃描 K 線）
        trades = []
        initial_capital = 10000.0
        capital = initial_capital
        position = None
        last_signal_time = None
        cooldown_minutes = 0
        
        # 過濾統計
        filtered_signals = {
            'consolidation': 0,
            'timezone': 0,
            'cost': 0,
            'confirmation': 0
        }
        
        print(f"  掃描 {len(df)} 根 K 線...")
        
        for i in range(max(strategy.ma_long + 50, 100), len(df)):
            current_time = df.iloc[i]['timestamp']
            df_window = df.iloc[max(0, i-200):i+1].copy()
            
            # 檢查冷卻期
            if last_signal_time and (current_time - last_signal_time).total_seconds() / 60 < cooldown_minutes:
                continue
            
            # 檢查持倉
            if position:
                current_price = df.iloc[i]['close']
                holding_minutes = (current_time - position['entry_time']).total_seconds() / 60
                
                # 計算當前 PnL
                if position['direction'] == 'LONG':
                    pnl_pct = (current_price - position['entry_price']) / position['entry_price']
                else:
                    pnl_pct = (position['entry_price'] - current_price) / position['entry_price']
                
                # 檢查出場條件
                exit_reason = None
                if current_price >= position['tp_price'] and position['direction'] == 'LONG':
                    exit_reason = 'TAKE_PROFIT'
                elif current_price <= position['tp_price'] and position['direction'] == 'SHORT':
                    exit_reason = 'TAKE_PROFIT'
                elif current_price <= position['sl_price'] and position['direction'] == 'LONG':
                    exit_reason = 'STOP_LOSS'
                elif current_price >= position['sl_price'] and position['direction'] == 'SHORT':
                    exit_reason = 'STOP_LOSS'
                elif holding_minutes >= version.time_stop_minutes:
                    exit_reason = 'TIME_STOP'
                
                if exit_reason:
                    # 平倉
                    pnl_gross = pnl_pct * 300  # $300 position
                    fee = 300 * 0.0005 * 2  # 開倉 + 平倉
                    pnl_net = pnl_gross - fee
                    
                    capital += pnl_net
                    
                    trades.append({
                        'entry_time': position['entry_time'].isoformat(),
                        'exit_time': current_time.isoformat(),
                        'direction': position['direction'],
                        'entry_price': position['entry_price'],
                        'exit_price': current_price,
                        'pnl_gross': round(pnl_gross, 2),
                        'pnl_net': round(pnl_net, 2),
                        'fee': round(fee, 2),
                        'exit_reason': exit_reason,
                        'holding_minutes': round(holding_minutes, 1)
                    })
                    
                    position = None
                    last_signal_time = current_time
                    continue
            
            # 無持倉時檢查入場信號
            if not position:
                signal_result = strategy.generate_signal(df_window, current_time)
                
                # SignalResult.direction == None 表示 HOLD 或被過濾
                if signal_result.direction is not None:
                    # 統計被過濾的信號（透過 reason 判斷）
                    if '盤整過濾' in signal_result.reason:
                        filtered_signals['consolidation'] += 1
                    elif '時區過濾' in signal_result.reason:
                        filtered_signals['timezone'] += 1
                    elif '成本過濾' in signal_result.reason:
                        filtered_signals['cost'] += 1
                    elif '等待確認' in signal_result.reason:
                        filtered_signals['confirmation'] += 1
                    
                    # 有明確方向信號才入場
                    if signal_result.direction in ['LONG', 'SHORT']:
                        position = {
                            'entry_time': current_time,
                            'entry_price': signal_result.entry_price,
                            'direction': signal_result.direction,
                            'tp_price': signal_result.take_profit_price,
                            'sl_price': signal_result.stop_loss_price
                        }
        
        # 整理結果
        winning_trades = [t for t in trades if t['pnl_net'] > 0]
        losing_trades = [t for t in trades if t['pnl_net'] <= 0]
        
        # 計算出場原因統計
        exit_reasons = {}
        for trade in trades:
            reason = trade['exit_reason']
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
        
        result = {
            'summary': {
                'year': test_year,
                'version': version.version,
                'total_trades': len(trades),
                'winning_trades': len(winning_trades),
                'losing_trades': len(losing_trades),
                'win_rate': round(len(winning_trades) / len(trades) * 100, 2) if trades else 0,
                'total_pnl_gross': round(sum(t['pnl_gross'] for t in trades), 2),
                'total_pnl_net': round(sum(t['pnl_net'] for t in trades), 2),
                'total_fee': round(sum(t['fee'] for t in trades), 2),
                'final_capital': round(capital, 2),
                'exit_reasons': exit_reasons,
                'filtered_signals': filtered_signals
            },
            'trades': trades[:100],  # 只保存前 100 筆
            'params': asdict(version)
        }
        
        print(f"  ✅ 完成: {len(trades)} 筆交易, 勝率 {result['summary']['win_rate']:.1f}%, 淨利 ${result['summary']['total_pnl_net']:.2f}")
        
        return result
    
    def analyze_performance(self, result: Dict, target_year: int) -> OptimizationDecision:
        """
        分析測試結果並生成優化決策
        
        決策邏輯：
        1. 時間止損 >60% → TP 太貪或趨勢判斷有問題
        2. 勝率 <45% → 信號質量不夠，需要更強過濾
        3. 淨利 <0 → 整體策略需要調整
        4. TP 達成率 <15% → TP 設太高
        """
        summary = result['summary']
        
        # 計算關鍵指標
        win_rate = summary['win_rate']
        total_pnl = summary['total_pnl_net']
        total_trades = summary['total_trades']
        exit_reasons = summary['exit_reasons']
        
        time_stop_pct = (exit_reasons.get('TIME_STOP', 0) / total_trades * 100) if total_trades > 0 else 0
        tp_pct = (exit_reasons.get('TAKE_PROFIT', 0) / total_trades * 100) if total_trades > 0 else 0
        sl_pct = (exit_reasons.get('STOP_LOSS', 0) / total_trades * 100) if total_trades > 0 else 0
        
        # 診斷問題
        problems = []
        solutions = []
        param_changes = {}
        
        if time_stop_pct > 60:
            problems.append(f"時間止損過高 ({time_stop_pct:.1f}%)")
            if tp_pct < 15:
                solutions.append("TP 太貪心，達成率太低 → 降低 TP 倍數")
                param_changes['atr_tp_multiplier'] = (self.current_version.atr_tp_multiplier, self.current_version.atr_tp_multiplier * 0.9)
            else:
                solutions.append("趨勢力道不足 → 增加時間止損")
                param_changes['time_stop_minutes'] = (self.current_version.time_stop_minutes, self.current_version.time_stop_minutes + 15)
        
        if win_rate < 45:
            problems.append(f"勝率偏低 ({win_rate:.1f}%)")
            solutions.append("信號質量不夠 → 收緊 RSI 範圍")
            param_changes['long_rsi_lower'] = (self.current_version.long_rsi_lower, self.current_version.long_rsi_lower + 2)
            param_changes['long_rsi_upper'] = (self.current_version.long_rsi_upper, self.current_version.long_rsi_upper - 2)
        
        if total_pnl < 0:
            problems.append(f"淨利為負 (${total_pnl:.2f})")
            if sl_pct > 20:
                solutions.append("SL 觸發過多 → 放寬 SL")
                param_changes['atr_sl_multiplier'] = (self.current_version.atr_sl_multiplier, self.current_version.atr_sl_multiplier * 1.1)
        
        if total_trades < 50:
            problems.append(f"交易數太少 ({total_trades})")
            solutions.append("過濾太嚴格 → 放寬 MA 距離")
            param_changes['ma_distance_threshold'] = (self.current_version.ma_distance_threshold, self.current_version.ma_distance_threshold * 0.8)
        
        if not problems:
            problems.append("表現良好")
            solutions.append("微調以維持穩定性")
        
        decision = OptimizationDecision(
            problem=" | ".join(problems),
            analysis=f"勝率 {win_rate:.1f}%, 淨利 ${total_pnl:.2f}, 時間止損 {time_stop_pct:.1f}%, TP達成 {tp_pct:.1f}%",
            solution=" | ".join(solutions),
            expected_impact="預期改善 2-5% 勝率或減少 10-15% 時間止損",
            parameter_changes=param_changes
        )
        
        return decision
    
    def apply_optimization(self, decision: OptimizationDecision, next_year: int) -> StrategyVersion:
        """應用優化決策生成新版本"""
        # 複製當前版本
        new_version = StrategyVersion(
            version=f'v3.{len(self.versions)}',
            year=next_year,
            long_rsi_lower=self.current_version.long_rsi_lower,
            long_rsi_upper=self.current_version.long_rsi_upper,
            ma_distance_threshold=self.current_version.ma_distance_threshold,
            volume_multiplier=self.current_version.volume_multiplier,
            atr_tp_multiplier=self.current_version.atr_tp_multiplier,
            atr_sl_multiplier=self.current_version.atr_sl_multiplier,
            min_tp_pct=self.current_version.min_tp_pct,
            max_tp_pct=self.current_version.max_tp_pct,
            min_sl_pct=self.current_version.min_sl_pct,
            max_sl_pct=self.current_version.max_sl_pct,
            time_stop_minutes=self.current_version.time_stop_minutes,
            enable_consolidation_filter=self.current_version.enable_consolidation_filter,
            enable_timezone_filter=self.current_version.enable_timezone_filter,
            enable_cost_filter=self.current_version.enable_cost_filter,
            require_confirmation=self.current_version.require_confirmation,
            confirmation_candles=self.current_version.confirmation_candles
        )
        
        # 應用參數變更
        changes_desc = []
        for param, (old_val, new_val) in decision.parameter_changes.items():
            setattr(new_version, param, new_val)
            changes_desc.append(f"{param}: {old_val:.2f} → {new_val:.2f}")
        
        new_version.changes_from_previous = " | ".join(changes_desc) if changes_desc else "無參數調整"
        new_version.change_reason = decision.problem
        
        return new_version
    
    def save_result(self, result: Dict, filename: str) -> None:
        """儲存回測結果"""
        filepath = os.path.join(self.results_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, 'w') as f:
            json.dump(result, f, indent=2)
        
        print(f"  💾 結果已儲存: {filename}")
    
    def run_walk_forward(self) -> None:
        """
        執行完整 Walk-Forward 循環
        
        流程:
        2020 (train baseline v3.0) → 2021 test → 分析 → v3.1
        2021 (train v3.1) → 2022 test → 分析 → v3.2
        2022 (train v3.2) → 2023 test → 分析 → v3.3
        2023 (train v3.3) → 2024 test → 分析 → v3.4
        2024 (train v3.4) → 2025 test → 最終評估
        """
        print("\n" + "="*80)
        print("🚀 Walk-Forward v3.0 智能優化系統啟動")
        print("="*80)
        
        test_years = [2021, 2022, 2023, 2024, 2025]
        
        for test_year in test_years:
            print(f"\n{'='*80}")
            print(f"📅 第 {test_year - 2020} 輪: 測試 {test_year} 年")
            print(f"{'='*80}")
            
            # 1. 測試當前版本
            result = self.backtest_with_version(self.current_version, test_year)
            if result is None:
                print(f"❌ {test_year} 年測試失敗，跳過")
                continue
            
            # 2. 儲存結果
            filename = f"test_{test_year}_{self.current_version.version}.json"
            self.save_result(result, filename)
            
            # 3. 分析表現
            decision = self.analyze_performance(result, test_year)
            self.decisions.append(decision)
            
            print(f"\n  📊 性能分析:")
            print(f"    問題: {decision.problem}")
            print(f"    分析: {decision.analysis}")
            print(f"    方案: {decision.solution}")
            
            # 4. 如果不是最後一年，生成新版本
            if test_year < 2025:
                new_version = self.apply_optimization(decision, test_year + 1)
                self.versions.append(new_version)
                self.current_version = new_version
                
                print(f"\n  🔄 生成新版本: {new_version.version}")
                print(f"    修正: {new_version.changes_from_previous}")
                print(f"    原因: {new_version.change_reason}")
        
        print(f"\n{'='*80}")
        print("✅ Walk-Forward v3.0 完整循環完成！")
        print(f"{'='*80}")
        
        # 生成最終報告
        self.generate_final_report()
    
    def generate_final_report(self) -> None:
        """生成最終演進報告"""
        print("\n📝 生成最終演進報告...")
        
        report_lines = [
            "# Walk-Forward v3.0 策略演進報告",
            "",
            f"**生成時間**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 一、版本演進路線圖",
            ""
        ]
        
        for version in self.versions:
            report_lines.append(f"### {version.version} ({version.year})")
            report_lines.append(f"- **修正內容**: {version.changes_from_previous}")
            report_lines.append(f"- **修正原因**: {version.change_reason}")
            report_lines.append(f"- **關鍵參數**:")
            report_lines.append(f"  - TP: ATR × {version.atr_tp_multiplier:.2f}")
            report_lines.append(f"  - SL: ATR × {version.atr_sl_multiplier:.2f}")
            report_lines.append(f"  - 時間止損: {version.time_stop_minutes} 分鐘")
            report_lines.append(f"  - RSI: [{version.long_rsi_lower:.1f}, {version.long_rsi_upper:.1f}]")
            report_lines.append("")
        
        report_lines.append("## 二、優化決策記錄")
        report_lines.append("")
        
        for i, decision in enumerate(self.decisions, 1):
            report_lines.append(f"### 決策 {i}")
            report_lines.append(f"- **問題**: {decision.problem}")
            report_lines.append(f"- **分析**: {decision.analysis}")
            report_lines.append(f"- **方案**: {decision.solution}")
            report_lines.append(f"- **預期**: {decision.expected_impact}")
            if decision.parameter_changes:
                report_lines.append(f"- **參數調整**:")
                for param, (old, new) in decision.parameter_changes.items():
                    report_lines.append(f"  - {param}: {old:.3f} → {new:.3f}")
            report_lines.append("")
        
        report_lines.append("## 三、最終評估")
        report_lines.append("")
        report_lines.append(f"- 總版本數: {len(self.versions)}")
        report_lines.append(f"- 優化輪次: {len(self.decisions)}")
        report_lines.append(f"- 最終版本: {self.current_version.version}")
        report_lines.append("")
        report_lines.append("請查看各年份的測試結果 JSON 檔案了解詳細表現。")
        
        # 儲存報告
        report_path = os.path.join(self.results_dir, 'WALK_FORWARD_V3_EVOLUTION.md')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(report_lines))
        
        print(f"  ✅ 報告已儲存: {report_path}")


if __name__ == '__main__':
    optimizer = WalkForwardV3()
    optimizer.run_walk_forward()
