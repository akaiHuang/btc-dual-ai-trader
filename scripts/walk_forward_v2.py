#!/usr/bin/env python3
"""
Walk-Forward Optimization for v2.0
===================================

測試 MVP Strategy v2.0 (整合 Phase 0) 的效果

對比目標:
- v1.4 (2025): 勝率 27.8%, 淨利 -$945, 時間止損 86.2%
- v2.0 (2025): 勝率 >42%, 淨利 >$0, 時間止損 <50%
"""

import pandas as pd
import json
from pathlib import Path
from datetime import datetime
import sys
sys.path.append(str(Path(__file__).parent.parent))

from src.strategy.mvp_strategy_v2 import MVPStrategyV2
from typing import Dict, List, Any


class WalkForwardV2:
    """v2.0 專用的 Walk-Forward 測試"""
    
    def __init__(self, data_path: str, output_dir: str):
        self.data_path = Path(data_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 載入數據
        print(f"📊 載入數據: {data_path}")
        df = pd.read_parquet(data_path)
        
        # 確保 timestamp 是 datetime
        if 'timestamp' in df.columns and not isinstance(df['timestamp'].iloc[0], pd.Timestamp):
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # 設置 timestamp 為索引
        if 'timestamp' in df.columns:
            df = df.set_index('timestamp')
        
        self.df = df
        print(f"✅ 數據載入完成: {len(df):,} 根K線")
        print(f"   時間範圍: {df.index.min()} ~ {df.index.max()}")
    
    def backtest_year(
        self,
        year: int,
        position_size: float = 300.0,
        leverage: float = 1.0,
        fee_rate: float = 0.0005
    ) -> Dict[str, Any]:
        """執行單年回測"""
        
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"
        
        # 過濾時間範圍
        mask = (self.df.index >= start_date) & (self.df.index <= end_date)
        df_period = self.df[mask].copy()
        
        print(f"\n🔍 回測期間: {start_date} ~ {end_date}")
        print(f"   K線數: {len(df_period):,}")
        
        if len(df_period) == 0:
            print("⚠️ 警告: 該期間無數據")
            return None
        
        # 初始化策略 v2.0
        strategy = MVPStrategyV2(
            # 使用 v1.4 的基礎參數
            long_rsi_lower=45.0,
            long_rsi_upper=60.0,
            # 動態 TP/SL
            atr_tp_multiplier=2.0,
            atr_sl_multiplier=1.0,
            # 時間止損恢復到 30 分鐘
            time_stop_minutes=30,
            # 啟用 Phase 0 過濾
            enable_consolidation_filter=True,
            enable_timezone_filter=True,
            enable_cost_filter=True
        )
        
        # 交易記錄
        trades = []
        open_position = None
        signal_count = 0
        filtered_count = {
            'consolidation': 0,
            'timezone': 0,
            'cost': 0,
            'confirmation': 0
        }
        
        for i in range(len(df_period)):
            current_time = df_period.index[i]
            
            # 如果有持倉，檢查出場條件
            if open_position:
                current_price = df_period.iloc[i]['close']
                minutes_held = (i - open_position['entry_index']) * 15
                
                # 檢查止盈止損
                if open_position['direction'] == 'LONG':
                    if current_price >= open_position['take_profit_price']:
                        pnl_gross = (current_price - open_position['entry_price']) / open_position['entry_price']
                        exit_reason = 'TP_HIT'
                    elif current_price <= open_position['stop_loss_price']:
                        pnl_gross = (current_price - open_position['entry_price']) / open_position['entry_price']
                        exit_reason = 'SL_HIT'
                    elif minutes_held >= strategy.time_stop_minutes:
                        pnl_gross = (current_price - open_position['entry_price']) / open_position['entry_price']
                        exit_reason = 'TIME_STOP'
                    else:
                        continue
                else:  # SHORT
                    if current_price <= open_position['take_profit_price']:
                        pnl_gross = (open_position['entry_price'] - current_price) / open_position['entry_price']
                        exit_reason = 'TP_HIT'
                    elif current_price >= open_position['stop_loss_price']:
                        pnl_gross = (open_position['entry_price'] - current_price) / open_position['entry_price']
                        exit_reason = 'SL_HIT'
                    elif minutes_held >= strategy.time_stop_minutes:
                        pnl_gross = (open_position['entry_price'] - current_price) / open_position['entry_price']
                        exit_reason = 'TIME_STOP'
                    else:
                        continue
                
                # 計算實際損益
                pnl_dollar = pnl_gross * position_size * leverage
                entry_fee = position_size * leverage * fee_rate
                exit_fee = position_size * leverage * fee_rate
                total_fee = entry_fee + exit_fee
                pnl_net = pnl_dollar - total_fee
                
                # 記錄交易
                trades.append({
                    'entry_time': open_position['entry_time'],
                    'exit_time': current_time,
                    'direction': open_position['direction'],
                    'entry_price': open_position['entry_price'],
                    'exit_price': current_price,
                    'pnl_gross': pnl_dollar,
                    'pnl_net': pnl_net,
                    'fee': total_fee,
                    'exit_reason': exit_reason,
                    'holding_minutes': minutes_held
                })
                
                open_position = None
                continue
            
            # 生成信號
            lookback_df = df_period.iloc[max(0, i-100):i+1]
            if len(lookback_df) < 50:
                continue
            
            signal_result = strategy.generate_signal(lookback_df, current_time)
            
            # 統計過濾原因
            if signal_result.direction is None:
                if '盤整過濾' in signal_result.reason:
                    filtered_count['consolidation'] += 1
                elif '時區過濾' in signal_result.reason:
                    filtered_count['timezone'] += 1
                elif '成本過濾' in signal_result.reason:
                    filtered_count['cost'] += 1
                elif '等待確認' in signal_result.reason:
                    filtered_count['confirmation'] += 1
                continue
            
            # 開倉
            signal_count += 1
            entry_price = lookback_df.iloc[-1]['close']
            open_position = {
                'entry_time': current_time,
                'entry_index': i,
                'direction': signal_result.direction,
                'entry_price': entry_price,
                'take_profit_price': signal_result.take_profit_price,
                'stop_loss_price': signal_result.stop_loss_price
            }
        
        # 統計結果
        if len(trades) == 0:
            print("⚠️ 無交易記錄")
            return None
        
        winning_trades = [t for t in trades if t['pnl_net'] > 0]
        losing_trades = [t for t in trades if t['pnl_net'] <= 0]
        
        win_rate = len(winning_trades) / len(trades) * 100
        total_pnl_gross = sum(t['pnl_gross'] for t in trades)
        total_pnl_net = sum(t['pnl_net'] for t in trades)
        total_fee = sum(t['fee'] for t in trades)
        fee_ratio = (total_fee / total_pnl_gross * 100) if total_pnl_gross > 0 else 0
        
        # 出場原因統計
        exit_reasons = {}
        for t in trades:
            reason = t['exit_reason']
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
        
        # 方向統計
        long_trades = len([t for t in trades if t['direction'] == 'LONG'])
        short_trades = len([t for t in trades if t['direction'] == 'SHORT'])
        
        results = {
            'year': year,
            'version': 'v2.0',
            'summary': {
                'total_trades': len(trades),
                'winning_trades': len(winning_trades),
                'losing_trades': len(losing_trades),
                'win_rate': round(win_rate, 2),
                'total_pnl_gross': round(total_pnl_gross, 2),
                'total_pnl_net': round(total_pnl_net, 2),
                'total_fee': round(total_fee, 2),
                'fee_ratio': round(fee_ratio, 2),
                'avg_pnl_net': round(total_pnl_net / len(trades), 2),
                'long_trades': long_trades,
                'short_trades': short_trades,
                'exit_reasons': exit_reasons,
                'signals_generated': signal_count,
                'filtered_signals': filtered_count
            },
            'trades': trades[:100]  # 只保存前 100 筆（避免文件過大）
        }
        
        print(f"\n📊 回測結果:")
        print(f"   信號產生: {signal_count}")
        print(f"   過濾統計: 盤整{filtered_count['consolidation']} | 時區{filtered_count['timezone']} | 成本{filtered_count['cost']} | 確認{filtered_count['confirmation']}")
        print(f"   總交易: {len(trades)}")
        print(f"   勝率: {win_rate:.1f}%")
        print(f"   淨利: ${total_pnl_net:,.2f}")
        print(f"   費用比: {fee_ratio:.1f}%")
        
        return results
    
    def run_all_years(self, years: List[int]):
        """執行多年回測"""
        
        print(f"\n{'='*60}")
        print(f"🚀 開始 v2.0 Walk-Forward 測試")
        print(f"   測試年份: {years}")
        print(f"{'='*60}")
        
        all_results = []
        
        for year in years:
            print(f"\n{'='*60}")
            print(f"📅 Year {year}")
            print(f"{'='*60}")
            
            result = self.backtest_year(year)
            
            if result is None:
                print(f"⚠️ {year} 年無數據或無交易，跳過")
                continue
            
            # 保存結果
            output_file = self.output_dir / f"test_{year}_v2.0.json"
            with open(output_file, 'w') as f:
                json.dump(result, f, indent=2, default=str)
            print(f"💾 結果已保存: {output_file.name}")
            
            all_results.append(result)
        
        # 生成總結報告
        self._generate_comparison_report(all_results)
        
        return all_results
    
    def _generate_comparison_report(self, results: List[Dict]):
        """生成 v1 vs v2 對比報告"""
        
        report_file = self.output_dir / "V2_COMPARISON_REPORT.md"
        
        with open(report_file, 'w') as f:
            f.write("# 📊 MVP Strategy v2.0 測試報告\n\n")
            f.write(f"**生成時間**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("---\n\n")
            
            f.write("## 一、v2.0 測試結果\n\n")
            f.write("| 年份 | 交易數 | 勝率 | 淨利 | 費用比 | 時間止損% |\n")
            f.write("|------|--------|------|------|--------|----------|\n")
            
            for r in results:
                year = r['year']
                summary = r['summary']
                time_stop_pct = summary['exit_reasons'].get('TIME_STOP', 0) / summary['total_trades'] * 100
                
                f.write(f"| {year} | {summary['total_trades']:,} | {summary['win_rate']:.1f}% | "
                       f"${summary['total_pnl_net']:,.0f} | {summary['fee_ratio']:.0f}% | {time_stop_pct:.1f}% |\n")
            
            f.write("\n---\n\n")
            
            f.write("## 二、Phase 0 過濾效果\n\n")
            
            for r in results:
                year = r['year']
                summary = r['summary']
                filtered = summary['filtered_signals']
                signals = summary['signals_generated']
                trades = summary['total_trades']
                
                f.write(f"### {year} 年\n\n")
                f.write(f"- **信號產生**: {signals:,}\n")
                f.write(f"- **過濾統計**:\n")
                f.write(f"  - 盤整過濾: {filtered['consolidation']:,}\n")
                f.write(f"  - 時區過濾: {filtered['timezone']:,}\n")
                f.write(f"  - 成本過濾: {filtered['cost']:,}\n")
                f.write(f"  - 等待確認: {filtered['confirmation']:,}\n")
                f.write(f"- **實際交易**: {trades:,}\n")
                f.write(f"- **過濾率**: {(1 - trades/max(signals, 1)) * 100:.1f}%\n\n")
            
            f.write("---\n\n")
            
            # 載入 v1.4 的結果進行對比
            v1_file = self.output_dir / "test_2025_v1.4.json"
            if v1_file.exists():
                with open(v1_file) as f:
                    v1_data = json.load(f)
                
                v2_2025 = [r for r in results if r['year'] == 2025]
                if v2_2025:
                    v2_data = v2_2025[0]
                    
                    f.write("## 三、2025年 v1.4 vs v2.0 對比\n\n")
                    f.write("| 指標 | v1.4 | v2.0 | 改進 |\n")
                    f.write("|------|------|------|------|\n")
                    
                    v1_wr = v1_data['summary']['win_rate']
                    v2_wr = v2_data['summary']['win_rate']
                    wr_imp = ((v2_wr - v1_wr) / v1_wr * 100) if v1_wr > 0 else 0
                    f.write(f"| 勝率 | {v1_wr:.1f}% | {v2_wr:.1f}% | {wr_imp:+.1f}% |\n")
                    
                    v1_pnl = v1_data['summary']['total_pnl_net']
                    v2_pnl = v2_data['summary']['total_pnl_net']
                    f.write(f"| 淨利 | ${v1_pnl:,.0f} | ${v2_pnl:,.0f} | ${v2_pnl - v1_pnl:+,.0f} |\n")
                    
                    v1_trades = v1_data['summary']['total_trades']
                    v2_trades = v2_data['summary']['total_trades']
                    trade_red = ((v1_trades - v2_trades) / v1_trades * 100) if v1_trades > 0 else 0
                    f.write(f"| 交易數 | {v1_trades:,} | {v2_trades:,} | -{trade_red:.1f}% |\n")
                    
                    v1_time = v1_data['summary']['exit_reasons'].get('TIME_STOP', 0) / v1_trades * 100
                    v2_time = v2_data['summary']['exit_reasons'].get('TIME_STOP', 0) / v2_trades * 100
                    f.write(f"| 時間止損% | {v1_time:.1f}% | {v2_time:.1f}% | {v2_time - v1_time:+.1f}% |\n")
                    
                    f.write("\n")
                    
                    # 判斷是否達標
                    goals_met = {
                        '勝率 >42%': v2_wr >= 42,
                        '淨利 >$0': v2_pnl > 0,
                        '時間止損 <50%': v2_time < 50
                    }
                    
                    f.write("### 目標達成情況\n\n")
                    for goal, met in goals_met.items():
                        status = '✅' if met else '❌'
                        f.write(f"- {status} {goal}\n")
                    
                    if all(goals_met.values()):
                        f.write("\n### ✅ **所有目標達成！**\n\n")
                    else:
                        f.write("\n### ⚠️ **部分目標未達成，需要進一步優化**\n\n")
            
            f.write("\n---\n\n")
            f.write("**報告結束**\n")
        
        print(f"\n📄 對比報告已生成: {report_file}")


if __name__ == "__main__":
    # 執行 v2.0 測試
    backtest = WalkForwardV2(
        data_path="data/historical/BTCUSDT_15m.parquet",
        output_dir="backtest_results/walk_forward"
    )
    
    # 測試所有年份
    results = backtest.run_all_years(years=[2021, 2022, 2023, 2024, 2025])
    
    print("\n" + "="*60)
    print("🎉 v2.0 測試完成！")
    print("="*60)
