#!/usr/bin/env python3
"""
Run HFT Backtest v4.0
====================

測試高頻交易策略（MVPStrategyV4HFT）

目標：
- 年交易數: 7,300 筆 (日均 20 筆)
- 勝率: 45-48%
- 每筆平均利潤: $0.50-1.00
- 年度淨利: $3,650-7,300

作者: HFT Testing
日期: 2025-11-15
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List
import json

from src.strategy.mvp_strategy_v4_hft import MVPStrategyV4HFT


def load_data(year: int = 2025) -> pd.DataFrame:
    """載入 BTC 15m 數據"""
    print(f"📊 載入 {year} 年數據...")
    df = pd.read_parquet('data/historical/BTCUSDT_15m.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df[df['timestamp'].dt.year == year].reset_index(drop=True)
    print(f"✅ 載入 {len(df)} 根 15m K 線 ({df['timestamp'].min()} ~ {df['timestamp'].max()})")
    return df


def backtest_hft(df: pd.DataFrame, strategy: MVPStrategyV4HFT, year: int) -> Dict:
    """
    HFT 回測
    
    特點：
    1. 快速進出 - 15 分鐘時間止損
    2. 高頻交易 - 目標日均 20 筆
    3. 小額獲利 - 每筆 $0.50-1.00
    """
    print(f"\n🚀 開始 HFT 回測 ({year} 年)...")
    
    # 回測狀態
    position = None  # {'direction', 'entry_price', 'entry_time', 'tp', 'sl'}
    trades = []
    equity_curve = [10.0]  # 初始資金 $10
    
    # 統計
    stats = {
        'total_trades': 0,
        'winning_trades': 0,
        'losing_trades': 0,
        'total_pnl_gross': 0,
        'total_pnl_net': 0,
        'total_fees': 0,
        'exit_reasons': {
            'TAKE_PROFIT': 0,
            'STOP_LOSS': 0,
            'TIME_STOP': 0,
        },
    }
    
    # 掃描 K 線
    lookback = 100  # 指標計算需要的歷史數據
    for i in range(lookback, len(df)):
        current_time = df.iloc[i]['timestamp']
        current_price = df.iloc[i]['close']
        current_high = df.iloc[i]['high']
        current_low = df.iloc[i]['low']
        
        # 檢查持倉
        if position is not None:
            direction = position['direction']
            entry_price = position['entry_price']
            entry_time = position['entry_time']
            tp_price = position['tp']
            sl_price = position['sl']
            
            # 檢查止盈
            if direction == 'LONG' and current_high >= tp_price:
                exit_price = tp_price
                exit_reason = 'TAKE_PROFIT'
            elif direction == 'SHORT' and current_low <= tp_price:
                exit_price = tp_price
                exit_reason = 'TAKE_PROFIT'
            # 檢查止損
            elif direction == 'LONG' and current_low <= sl_price:
                exit_price = sl_price
                exit_reason = 'STOP_LOSS'
            elif direction == 'SHORT' and current_high >= sl_price:
                exit_price = sl_price
                exit_reason = 'STOP_LOSS'
            # 檢查時間止損（HFT: 15 分鐘）
            elif (current_time - entry_time).total_seconds() >= strategy.time_stop_minutes * 60:
                exit_price = current_price
                exit_reason = 'TIME_STOP'
            else:
                # 持倉中，繼續下一根 K 線
                continue
            
            # 平倉
            if direction == 'LONG':
                pnl_pct = (exit_price - entry_price) / entry_price
            else:  # SHORT
                pnl_pct = (entry_price - exit_price) / entry_price
            
            pnl_gross = equity_curve[-1] * pnl_pct
            fee_open = equity_curve[-1] * 0.00075  # 0.075% 開倉手續費
            fee_close = (equity_curve[-1] + pnl_gross) * 0.00075  # 0.075% 平倉手續費
            total_fee = fee_open + fee_close
            pnl_net = pnl_gross - total_fee
            
            # 更新資金
            new_equity = equity_curve[-1] + pnl_net
            equity_curve.append(new_equity)
            
            # 記錄交易
            trade = {
                'entry_time': entry_time.isoformat(),
                'exit_time': current_time.isoformat(),
                'direction': direction,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'tp_price': tp_price,
                'sl_price': sl_price,
                'pnl_pct': pnl_pct * 100,
                'pnl_gross': pnl_gross,
                'pnl_net': pnl_net,
                'fee': total_fee,
                'exit_reason': exit_reason,
                'holding_time_minutes': (current_time - entry_time).total_seconds() / 60,
            }
            trades.append(trade)
            
            # 更新統計
            stats['total_trades'] += 1
            stats['total_pnl_gross'] += pnl_gross
            stats['total_pnl_net'] += pnl_net
            stats['total_fees'] += total_fee
            stats['exit_reasons'][exit_reason] += 1
            
            if pnl_net > 0:
                stats['winning_trades'] += 1
            else:
                stats['losing_trades'] += 1
            
            # 清空持倉
            position = None
            
            # 打印進度（每 100 筆交易）
            if stats['total_trades'] % 100 == 0:
                print(f"  進度: {i}/{len(df)} K線 | {stats['total_trades']} 筆交易 | 當前資金: ${new_equity:.2f}")
        
        # 無持倉，檢查信號
        if position is None:
            # 準備數據窗口
            df_window = df.iloc[i-lookback+1:i+1].copy()
            
            # 生成信號
            signal_result = strategy.generate_signal(df_window, current_time)
            
            # 有效信號？
            if signal_result.direction in ['LONG', 'SHORT']:
                position = {
                    'direction': signal_result.direction,
                    'entry_price': signal_result.entry_price,
                    'entry_time': current_time,
                    'tp': signal_result.take_profit_price,
                    'sl': signal_result.stop_loss_price,
                }
    
    # 計算總結指標
    stats['win_rate'] = (stats['winning_trades'] / stats['total_trades'] * 100) if stats['total_trades'] > 0 else 0
    stats['avg_pnl_per_trade'] = (stats['total_pnl_net'] / stats['total_trades']) if stats['total_trades'] > 0 else 0
    stats['final_equity'] = equity_curve[-1]
    stats['total_return_pct'] = (equity_curve[-1] - 10) / 10 * 100
    
    # 計算最大回撤
    equity_array = np.array(equity_curve)
    running_max = np.maximum.accumulate(equity_array)
    drawdown = (equity_array - running_max) / running_max * 100
    stats['max_drawdown_pct'] = abs(drawdown.min())
    
    # 計算 Sharpe Ratio（簡化版）
    if len(trades) > 1:
        pnl_list = [t['pnl_net'] for t in trades]
        stats['sharpe_ratio'] = np.mean(pnl_list) / np.std(pnl_list) if np.std(pnl_list) > 0 else 0
    else:
        stats['sharpe_ratio'] = 0
    
    # 獲取策略統計
    strategy_stats = strategy.get_stats()
    
    return {
        'summary': stats,
        'trades': trades,
        'equity_curve': equity_curve,
        'strategy_stats': strategy_stats,
    }


def print_results(results: Dict, year: int):
    """打印結果"""
    summary = results['summary']
    trades = results['trades']
    strategy_stats = results['strategy_stats']
    
    print(f"\n{'='*80}")
    print(f"📊 HFT v4.0 回測結果 ({year} 年)")
    print(f"{'='*80}")
    
    print(f"\n📈 交易統計:")
    print(f"  總交易數:    {summary['total_trades']} 筆")
    print(f"  勝率:        {summary['win_rate']:.1f}% ({summary['winning_trades']}勝 / {summary['losing_trades']}敗)")
    print(f"  淨利潤:      ${summary['total_pnl_net']:,.2f}")
    print(f"  總手續費:    ${summary['total_fees']:,.2f}")
    print(f"  最終資金:    ${summary['final_equity']:.2f} (初始 $10)")
    print(f"  總回報率:    {summary['total_return_pct']:.1f}%")
    print(f"  最大回撤:    {summary['max_drawdown_pct']:.2f}%")
    print(f"  Sharpe:      {summary['sharpe_ratio']:.2f}")
    
    print(f"\n💰 每筆交易:")
    print(f"  平均盈利:    ${summary['avg_pnl_per_trade']:.2f}")
    
    print(f"\n🚪 退出原因:")
    exit_reasons = summary['exit_reasons']
    total = summary['total_trades']
    for reason, count in exit_reasons.items():
        pct = count / total * 100 if total > 0 else 0
        print(f"  {reason:15s} {count:4d} 筆 ({pct:5.1f}%)")
    
    print(f"\n🔍 信號過濾統計:")
    print(f"  總信號數:        {strategy_stats['signals_generated']}")
    print(f"  過濾數:          {strategy_stats['signals_filtered']} ({strategy_stats.get('filter_rate', 0)*100:.1f}%)")
    print(f"  - 盤整過濾:      {strategy_stats['consolidation_filtered']} ({strategy_stats.get('consolidation_rate', 0)*100:.1f}%)")
    print(f"  - 時段過濾:      {strategy_stats['timezone_filtered']} ({strategy_stats.get('timezone_rate', 0)*100:.1f}%)")
    print(f"  - 成本過濾:      {strategy_stats['cost_filtered']} ({strategy_stats.get('cost_rate', 0)*100:.1f}%)")
    print(f"  - 確認過濾:      {strategy_stats['confirmation_filtered']} ({strategy_stats.get('confirmation_rate', 0)*100:.1f}%)")
    
    # 分析持倉時間
    if trades:
        holding_times = [t['holding_time_minutes'] for t in trades]
        print(f"\n⏱️  持倉時間:")
        print(f"  平均: {np.mean(holding_times):.1f} 分鐘")
        print(f"  中位數: {np.median(holding_times):.1f} 分鐘")
        print(f"  最短: {np.min(holding_times):.1f} 分鐘")
        print(f"  最長: {np.max(holding_times):.1f} 分鐘")
    
    print(f"\n{'='*80}")
    
    # 每日交易統計
    if trades:
        days = (pd.to_datetime(trades[-1]['exit_time']) - pd.to_datetime(trades[0]['entry_time'])).days
        if days > 0:
            trades_per_day = len(trades) / days
            print(f"\n📅 日均交易: {trades_per_day:.1f} 筆/天 (目標: 20 筆/天)")
            if trades_per_day >= 18:
                print(f"   ✅ 已達到 HFT 目標頻率！")
            elif trades_per_day >= 10:
                print(f"   ⚠️  接近目標，需要進一步放寬過濾器")
            else:
                print(f"   ❌ 遠低於目標，需要大幅調整策略參數")


def save_results(results: Dict, year: int, version: str = 'v4.0'):
    """保存結果"""
    output_dir = Path('backtest_results/hft')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = output_dir / f'test_{year}_{version}.json'
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 結果已保存: {output_file}")


def main():
    """主函數"""
    # 載入數據
    year = 2025
    df = load_data(year)
    
    # 創建 HFT 策略
    strategy = MVPStrategyV4HFT(
        # HFT 超激進參數
        long_rsi_lower=20.0,  # 20-80 更寬範圍
        long_rsi_upper=80.0,
        short_rsi_lower=20.0,
        short_rsi_upper=80.0,
        ma_distance_threshold=0.1,  # 0.1% 極低要求
        volume_multiplier=0.8,  # 允許低成交量
        atr_tp_multiplier=2.0,  # 提高 TP 以覆蓋手續費
        atr_sl_multiplier=0.8,
        min_tp_pct=0.5,  # 最小 TP 0.5%
        time_stop_minutes=15,
        require_confirmation=False,
        enable_consolidation_filter=False,  # 關閉盤整過濾
        enable_timezone_filter=False,  # 關閉時段過濾
        enable_cost_filter=False,  # 關閉成本過濾器測試
        consolidation_bb_threshold=0.030,
        consolidation_confidence_threshold=0.5,
        timezone_min_win_rate=0.35,  # 降低到 35%
        cost_min_profit_ratio=1.0,  # 降低到 1.0
    )
    
    # 運行回測
    results = backtest_hft(df, strategy, year)
    
    # 打印結果
    print_results(results, year)
    
    # 保存結果
    save_results(results, year, 'v4.0')


if __name__ == '__main__':
    main()
