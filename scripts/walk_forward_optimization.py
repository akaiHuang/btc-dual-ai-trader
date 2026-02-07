#!/usr/bin/env python3
"""
Walk-Forward Optimization 漸進式優化系統

從 2020 年開始訓練 baseline，逐年驗證並優化：
2020 → 訓練 v1.0 → 2021 驗證 → 優化 v1.1
2021 → 訓練 v1.1 → 2022 驗證 → 優化 v1.2
...
2024 → 訓練 v1.4 → 2025 驗證（目標達標）
"""

import pandas as pd
import json
from pathlib import Path
from datetime import datetime
import sys
sys.path.append(str(Path(__file__).parent.parent))

from src.strategy.mvp_strategy_v1 import MVPStrategyV1
from dataclasses import dataclass, asdict
from typing import Dict, List, Any


@dataclass
class StrategyParams:
    """策略參數配置"""
    version: str
    # 進場條件
    rsi_long_min: float = 40.0
    rsi_long_max: float = 70.0
    rsi_short_min: float = 30.0
    rsi_short_max: float = 60.0
    ma_distance_threshold: float = 0.0  # MA 距離閾值 (%)
    volume_multiplier: float = 1.0  # 成交量倍數
    
    # 出場條件
    take_profit_pct: float = 0.5
    stop_loss_pct: float = 0.25
    time_stop_minutes: int = 30
    
    # 冷卻期
    cooldown_candles: int = 0  # 信號冷卻期 (K線數)
    
    def to_dict(self) -> Dict:
        return asdict(self)


class WalkForwardBacktest:
    """漸進式回測系統"""
    
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
    
    def backtest_with_params(
        self, 
        params: StrategyParams, 
        start_date: str, 
        end_date: str,
        position_size: float = 300.0,
        leverage: float = 1.0,
        fee_rate: float = 0.0005
    ) -> Dict[str, Any]:
        """執行回測"""
        
        # 過濾時間範圍
        mask = (self.df.index >= start_date) & (self.df.index <= end_date)
        df_period = self.df[mask].copy()
        
        print(f"\n🔍 回測期間: {start_date} ~ {end_date}")
        print(f"   K線數: {len(df_period):,}")
        
        if len(df_period) == 0:
            print("⚠️ 警告: 該期間無數據")
            return None
        
        # 初始化策略
        strategy = MVPStrategyV1()
        
        # 覆寫策略參數
        strategy.rsi_long_min = params.rsi_long_min
        strategy.rsi_long_max = params.rsi_long_max
        strategy.rsi_short_min = params.rsi_short_min
        strategy.rsi_short_max = params.rsi_short_max
        strategy.take_profit_pct = params.take_profit_pct
        strategy.stop_loss_pct = params.stop_loss_pct
        strategy.time_stop_minutes = params.time_stop_minutes
        
        # 交易記錄
        trades = []
        open_position = None
        last_signal_index = -999  # 用於冷卻期
        
        for i in range(len(df_period)):
            current_time = df_period.index[i]
            
            # 如果有持倉，檢查出場條件
            if open_position:
                current_price = df_period.iloc[i]['close']
                minutes_held = (i - open_position['entry_index']) * 15  # 假設15分鐘K線
                
                # 檢查止盈
                if open_position['direction'] == 'LONG':
                    if current_price >= open_position['take_profit_price']:
                        pnl_gross = (current_price - open_position['entry_price']) / open_position['entry_price']
                        exit_reason = 'TP_HIT'
                    elif current_price <= open_position['stop_loss_price']:
                        pnl_gross = (current_price - open_position['entry_price']) / open_position['entry_price']
                        exit_reason = 'SL_HIT'
                    elif minutes_held >= params.time_stop_minutes:
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
                    elif minutes_held >= params.time_stop_minutes:
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
            
            # 檢查冷卻期
            if params.cooldown_candles > 0:
                if i - last_signal_index < params.cooldown_candles:
                    continue
            
            # 生成信號
            lookback_df = df_period.iloc[max(0, i-100):i+1]
            if len(lookback_df) < 50:
                continue
            
            signal_result = strategy.generate_signal(lookback_df)
            indicators = None  # 初始化指標變量
            
            if signal_result.direction is not None:  # 有信號（LONG或SHORT）
                # 檢查 MA 距離閾值
                if params.ma_distance_threshold > 0:
                    indicators = strategy.calculate_indicators(lookback_df)
                    if indicators:  # 確保有指標數據
                        ma_distance = abs(indicators['ma_short'] - indicators['ma_long']) / indicators['ma_long'] * 100
                        if ma_distance < params.ma_distance_threshold:
                            continue
                
                # 檢查成交量條件
                if params.volume_multiplier > 1.0:
                    if not indicators:  # 如果前面沒有計算過
                        indicators = strategy.calculate_indicators(lookback_df)
                    if indicators and lookback_df.iloc[-1]['volume'] < indicators['volume_ma'] * params.volume_multiplier:
                        continue
                
                # 開倉
                entry_price = lookback_df.iloc[-1]['close']
                open_position = {
                    'entry_time': current_time,
                    'entry_index': i,
                    'direction': signal_result.direction,
                    'entry_price': entry_price,
                    'take_profit_price': signal_result.take_profit_price,
                    'stop_loss_price': signal_result.stop_loss_price
                }
                last_signal_index = i
        
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
            'params': params.to_dict(),
            'period': {'start': start_date, 'end': end_date},
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
                'exit_reasons': exit_reasons
            },
            'trades': trades
        }
        
        print(f"\n📊 回測結果:")
        print(f"   總交易: {len(trades)}")
        print(f"   勝率: {win_rate:.1f}%")
        print(f"   淨利: ${total_pnl_net:,.2f}")
        print(f"   費用比: {fee_ratio:.1f}%")
        
        return results
    
    def optimize_params(self, train_results: Dict, test_results: Dict) -> StrategyParams:
        """根據訓練和測試結果優化參數"""
        
        current_params = StrategyParams(**train_results['params'])
        new_version = f"v1.{int(current_params.version.split('.')[1]) + 1}"
        
        print(f"\n🔧 分析問題並優化參數...")
        
        test_summary = test_results['summary']
        improvements = []
        
        # 問題 1: 勝率低於 45%
        if test_summary['win_rate'] < 45:
            print(f"   ⚠️ 勝率過低 ({test_summary['win_rate']:.1f}%)")
            # 收緊 RSI 範圍
            current_params.rsi_long_min = min(45.0, current_params.rsi_long_min + 2.5)
            current_params.rsi_long_max = max(65.0, current_params.rsi_long_max - 2.5)
            improvements.append(f"收緊RSI範圍: LONG [{current_params.rsi_long_min}, {current_params.rsi_long_max}]")
        
        # 問題 2: 費用比過高（>50%）
        if test_summary['fee_ratio'] > 50:
            print(f"   ⚠️ 費用比過高 ({test_summary['fee_ratio']:.1f}%)")
            # 增加冷卻期
            current_params.cooldown_candles = max(3, current_params.cooldown_candles + 2)
            improvements.append(f"增加冷卻期: {current_params.cooldown_candles} K線")
            
            # 增加 MA 距離閾值
            current_params.ma_distance_threshold = min(0.5, current_params.ma_distance_threshold + 0.1)
            improvements.append(f"增加MA距離閾值: {current_params.ma_distance_threshold}%")
        
        # 問題 3: TIME_STOP 比例過高（>50%）
        time_stop_pct = test_summary['exit_reasons'].get('TIME_STOP', 0) / test_summary['total_trades'] * 100
        if time_stop_pct > 50:
            print(f"   ⚠️ 時間止損過多 ({time_stop_pct:.1f}%)")
            # 縮短時間止損
            current_params.time_stop_minutes = max(15, current_params.time_stop_minutes - 5)
            improvements.append(f"縮短時間止損: {current_params.time_stop_minutes} 分鐘")
            
            # 縮小止盈目標
            current_params.take_profit_pct = max(0.3, current_params.take_profit_pct - 0.05)
            improvements.append(f"縮小止盈目標: {current_params.take_profit_pct}%")
        
        # 問題 4: 成交量過濾不足
        if test_summary['total_trades'] > 5000:  # 如果交易過多
            print(f"   ⚠️ 交易過於頻繁 ({test_summary['total_trades']})")
            current_params.volume_multiplier = min(1.5, current_params.volume_multiplier + 0.1)
            improvements.append(f"提高成交量門檻: {current_params.volume_multiplier}x")
        
        current_params.version = new_version
        
        print(f"\n✅ 優化建議:")
        for imp in improvements:
            print(f"   • {imp}")
        
        return current_params
    
    def run_walk_forward(self, start_year: int = 2020, end_year: int = 2024):
        """執行完整的 walk-forward 流程"""
        
        print(f"\n{'='*60}")
        print(f"🚀 開始 Walk-Forward Optimization")
        print(f"   訓練年份: {start_year} ~ {end_year}")
        print(f"   驗證年份: {start_year+1} ~ {end_year+1}")
        print(f"{'='*60}")
        
        # 初始參數 (v1.0)
        params = StrategyParams(version="v1.0")
        
        all_results = []
        
        for year in range(start_year, end_year + 1):
            print(f"\n{'='*60}")
            print(f"📅 Year {year}: 訓練 & 驗證")
            print(f"{'='*60}")
            
            train_start = f"{year}-01-01"
            train_end = f"{year}-12-31"
            test_start = f"{year+1}-01-01"
            test_end = f"{year+1}-12-31"
            
            # 訓練階段
            print(f"\n📚 [訓練階段] {year}")
            train_results = self.backtest_with_params(params, train_start, train_end)
            
            if train_results is None:
                print(f"⚠️ {year} 年無數據，跳過")
                continue
            
            # 保存訓練結果
            train_file = self.output_dir / f"train_{year}_{params.version}.json"
            with open(train_file, 'w') as f:
                json.dump(train_results, f, indent=2, default=str)
            print(f"💾 訓練結果已保存: {train_file.name}")
            
            # 驗證階段
            print(f"\n🧪 [驗證階段] {year+1}")
            test_results = self.backtest_with_params(params, test_start, test_end)
            
            if test_results is None:
                print(f"⚠️ {year+1} 年無數據，跳過")
                continue
            
            # 保存驗證結果
            test_file = self.output_dir / f"test_{year+1}_{params.version}.json"
            with open(test_file, 'w') as f:
                json.dump(test_results, f, indent=2, default=str)
            print(f"💾 驗證結果已保存: {test_file.name}")
            
            # 記錄結果
            all_results.append({
                'train_year': year,
                'test_year': year + 1,
                'version': params.version,
                'train_summary': train_results['summary'],
                'test_summary': test_results['summary']
            })
            
            # 優化參數（為下一年準備）
            if year < end_year:
                params = self.optimize_params(train_results, test_results)
                print(f"\n🔄 準備使用 {params.version} 進行下一年訓練")
        
        # 生成總結報告
        self._generate_summary_report(all_results)
        
        return all_results
    
    def _generate_summary_report(self, results: List[Dict]):
        """生成總結報告"""
        
        report_file = self.output_dir / "WALK_FORWARD_SUMMARY.md"
        
        with open(report_file, 'w') as f:
            f.write("# 📊 Walk-Forward Optimization 總結報告\n\n")
            f.write(f"**生成時間**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("---\n\n")
            
            f.write("## 一、逐年優化結果\n\n")
            f.write("| 訓練年 | 測試年 | 版本 | 訓練勝率 | 測試勝率 | 測試淨利 | 測試費用比 |\n")
            f.write("|--------|--------|------|----------|----------|----------|------------|\n")
            
            for r in results:
                train_wr = r['train_summary']['win_rate']
                test_wr = r['test_summary']['win_rate']
                test_pnl = r['test_summary']['total_pnl_net']
                test_fee = r['test_summary']['fee_ratio']
                
                f.write(f"| {r['train_year']} | {r['test_year']} | {r['version']} | "
                       f"{train_wr:.1f}% | {test_wr:.1f}% | ${test_pnl:,.0f} | {test_fee:.0f}% |\n")
            
            f.write("\n---\n\n")
            
            f.write("## 二、性能演進分析\n\n")
            
            # 勝率演進
            f.write("### 2.1 勝率演進\n\n")
            f.write("```\n")
            for r in results:
                test_wr = r['test_summary']['win_rate']
                bar = '█' * int(test_wr / 2)
                f.write(f"{r['test_year']} ({r['version']}): {bar} {test_wr:.1f}%\n")
            f.write("```\n\n")
            
            # 淨利演進
            f.write("### 2.2 淨利演進\n\n")
            f.write("```\n")
            for r in results:
                pnl = r['test_summary']['total_pnl_net']
                symbol = '✅' if pnl > 0 else '❌'
                f.write(f"{r['test_year']} ({r['version']}): ${pnl:>10,.2f} {symbol}\n")
            f.write("```\n\n")
            
            # 費用比演進
            f.write("### 2.3 費用比演進\n\n")
            f.write("```\n")
            for r in results:
                fee_ratio = r['test_summary']['fee_ratio']
                status = '✅' if fee_ratio < 50 else '⚠️' if fee_ratio < 100 else '❌'
                f.write(f"{r['test_year']} ({r['version']}): {fee_ratio:>6.1f}% {status}\n")
            f.write("```\n\n")
            
            f.write("---\n\n")
            
            f.write("## 三、最終評估\n\n")
            
            # 最後一年的結果（2025）
            if len(results) > 0:
                final = results[-1]
                final_test = final['test_summary']
                
                f.write(f"### 🎯 2025年驗證結果 ({final['version']})\n\n")
                f.write(f"- **勝率**: {final_test['win_rate']:.1f}% {'✅' if final_test['win_rate'] >= 45 else '❌'}\n")
                f.write(f"- **淨利**: ${final_test['total_pnl_net']:,.2f} {'✅' if final_test['total_pnl_net'] > 0 else '❌'}\n")
                f.write(f"- **費用比**: {final_test['fee_ratio']:.1f}% {'✅' if final_test['fee_ratio'] < 50 else '❌'}\n")
                f.write(f"- **總交易**: {final_test['total_trades']:,}\n\n")
                
                # 判斷是否達標
                meets_target = (
                    final_test['win_rate'] >= 45 and
                    final_test['total_pnl_net'] > 0 and
                    final_test['fee_ratio'] < 50
                )
                
                if meets_target:
                    f.write("### ✅ **目標達成！**\n\n")
                    f.write("策略已達到預期目標，可以進入實盤測試階段。\n\n")
                else:
                    f.write("### ⚠️ **目標未達成**\n\n")
                    f.write("建議繼續優化參數或考慮引入更多過濾條件。\n\n")
            
            f.write("---\n\n")
            f.write("**報告結束**\n")
        
        print(f"\n📄 總結報告已生成: {report_file}")


if __name__ == "__main__":
    # 執行 walk-forward optimization
    backtest = WalkForwardBacktest(
        data_path="data/historical/BTCUSDT_15m.parquet",
        output_dir="backtest_results/walk_forward"
    )
    
    results = backtest.run_walk_forward(start_year=2020, end_year=2024)
    
    print("\n" + "="*60)
    print("🎉 Walk-Forward Optimization 完成！")
    print("="*60)
