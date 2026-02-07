#!/usr/bin/env python3
"""
快速測試 v2.1 策略
"""

import pandas as pd
import json
from pathlib import Path
from datetime import datetime
import sys
sys.path.append(str(Path(__file__).parent.parent))

from src.strategy.mvp_strategy_v2 import MVPStrategyV2
from typing import Dict, List


def backtest_v21(year: int, version: str = '2.1') -> Dict:
    """
    執行 v2.1 回測
    
    Args:
        year: 測試年份
        version: 版本號
        
    Returns:
        回測結果字典
    """
    # 載入數據
    data_path = Path(f"data/historical/BTCUSDT_15m.parquet")
    print(f"\n📊 載入數據: {data_path}")
    df = pd.read_parquet(data_path)
    
    # 確保 timestamp 是 datetime
    if 'timestamp' in df.columns and not isinstance(df['timestamp'].iloc[0], pd.Timestamp):
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    
    # 篩選年份
    df_year = df[df.index.year == year].copy()
    print(f"✅ {year} 年數據: {len(df_year)} 根 K 線")
    
    # 初始化策略 (v2.1 參數)
    strategy = MVPStrategyV2(
        # 動態止盈止損 (MFE/MAE 優化後)
        atr_tp_multiplier=1.82,
        atr_sl_multiplier=1.39,
        
        # Phase 0 全開
        enable_consolidation_filter=True,
        enable_timezone_filter=True,
        enable_cost_filter=True,
    )
    
    # 回測變量
    position = None
    trades = []
    capital = 10000.0
    position_size = 300.0
    fee_rate = 0.0005
    
    # 統計
    filtered_signals = {
        'consolidation': 0,
        'timezone': 0,
        'cost': 0,
        'confirmation': 0
    }
    
    # 掃描 K 線
    print(f"🔄 開始回測 {year} 年...")
    
    for i in range(100, len(df_year)):
        current_time = df_year.index[i]
        current_price = df_year.iloc[i]['close']
        
        # 準備 DataFrame 切片
        df_slice = df_year.iloc[max(0, i-100):i+1].copy()
        
        # 檢查平倉
        if position:
            entry_time = position['entry_time']
            entry_price = position['entry_price']
            direction = position['direction']
            tp_price = position['tp_price']
            sl_price = position['sl_price']
            
            # 計算持倉時間
            holding_minutes = (current_time - entry_time).total_seconds() / 60
            
            exit_reason = None
            exit_price = current_price
            
            # 檢查止盈止損
            if direction == 'LONG':
                if current_price >= tp_price:
                    exit_reason = 'TAKE_PROFIT'
                    exit_price = tp_price
                elif current_price <= sl_price:
                    exit_reason = 'STOP_LOSS'
                    exit_price = sl_price
            else:  # SHORT
                if current_price <= tp_price:
                    exit_reason = 'TAKE_PROFIT'
                    exit_price = tp_price
                elif current_price >= sl_price:
                    exit_reason = 'STOP_LOSS'
                    exit_price = sl_price
            
            # 時間止損
            if not exit_reason and holding_minutes >= strategy.time_stop_minutes:
                exit_reason = 'TIME_STOP'
                exit_price = current_price
            
            # 平倉
            if exit_reason:
                # 計算盈虧
                if direction == 'LONG':
                    pnl_gross = (exit_price - entry_price) / entry_price * position_size
                else:
                    pnl_gross = (entry_price - exit_price) / entry_price * position_size
                
                fee = position_size * fee_rate * 2
                pnl_net = pnl_gross - fee
                capital += pnl_net
                
                trades.append({
                    'entry_time': entry_time.isoformat(),
                    'exit_time': current_time.isoformat(),
                    'direction': direction,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl_gross': round(pnl_gross, 2),
                    'pnl_net': round(pnl_net, 2),
                    'fee': round(fee, 2),
                    'exit_reason': exit_reason,
                    'holding_minutes': round(holding_minutes, 1)
                })
                
                position = None
        
        # 開倉信號
        if not position:
            signal = strategy.generate_signal(df_slice, current_time)
            
            if signal.direction:
                # 記錄過濾統計
                for filter_name, passed in signal.filters_passed.items():
                    if not passed:
                        filtered_signals[filter_name] += 1
                
                # 檢查是否所有過濾都通過
                if all(signal.filters_passed.values()):
                    # 開倉
                    position = {
                        'entry_time': current_time,
                        'entry_price': signal.entry_price,
                        'direction': signal.direction,
                        'tp_price': signal.take_profit_price,
                        'sl_price': signal.stop_loss_price,
                    }
    
    # 計算統計
    winning_trades = [t for t in trades if t['pnl_net'] > 0]
    losing_trades = [t for t in trades if t['pnl_net'] <= 0]
    
    total_trades = len(trades)
    win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0
    
    total_pnl_gross = sum(t['pnl_gross'] for t in trades)
    total_pnl_net = sum(t['pnl_net'] for t in trades)
    total_fee = sum(t['fee'] for t in trades)
    
    # 出場原因統計
    exit_reasons = {}
    for t in trades:
        reason = t['exit_reason']
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    
    summary = {
        'year': year,
        'version': version,
        'total_trades': total_trades,
        'winning_trades': len(winning_trades),
        'losing_trades': len(losing_trades),
        'win_rate': round(win_rate, 2),
        'total_pnl_gross': round(total_pnl_gross, 2),
        'total_pnl_net': round(total_pnl_net, 2),
        'total_fee': round(total_fee, 2),
        'final_capital': round(capital, 2),
        'exit_reasons': exit_reasons,
        'filtered_signals': filtered_signals,
    }
    
    result = {
        'summary': summary,
        'trades': trades[:100],  # 只保存前 100 筆
        'params': {
            'atr_tp_multiplier': strategy.atr_tp_multiplier,
            'atr_sl_multiplier': strategy.atr_sl_multiplier,
            'time_stop_minutes': strategy.time_stop_minutes,
            'enable_consolidation_filter': strategy.enable_consolidation_filter,
            'enable_timezone_filter': strategy.enable_timezone_filter,
            'enable_cost_filter': strategy.enable_cost_filter,
        }
    }
    
    return result


def main():
    """測試 v2.1"""
    
    print("="*80)
    print("🚀 MVP Strategy v2.1 測試")
    print("="*80)
    print("\n修正內容:")
    print("1. ✅ 修復盤整過濾 API")
    print("2. ✅ 優化 TP/SL (ATR 1.82/1.39,基於 MFE/MAE 分析)")
    print("3. ✅ 保持時間止損 30 分鐘")
    
    # 測試所有年份
    years = [2021, 2022, 2023, 2024, 2025]
    results = {}
    
    for year in years:
        result = backtest_v21(year, version='2.1')
        results[year] = result
        
        # 保存結果
        output_path = Path(f"backtest_results/walk_forward/test_{year}_v2.1.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        # 顯示摘要
        s = result['summary']
        print(f"\n【{year} 年結果】")
        print(f"  交易數: {s['total_trades']}")
        print(f"  勝率: {s['win_rate']}%")
        print(f"  淨利: ${s['total_pnl_net']}")
        print(f"  時間止損: {s['exit_reasons'].get('TIME_STOP', 0)} ({s['exit_reasons'].get('TIME_STOP', 0) / s['total_trades'] * 100 if s['total_trades'] > 0 else 0:.1f}%)")
        print(f"  過濾統計: {s['filtered_signals']}")
    
    print("\n" + "="*80)
    print("🎉 v2.1 測試完成！")
    print("="*80)
    
    # 重點對比 2025 年
    if 2025 in results:
        s = results[2025]['summary']
        print(f"\n【2025 重點指標】")
        print(f"  交易數: {s['total_trades']}")
        print(f"  勝率: {s['win_rate']}% (目標 >45%)")
        print(f"  淨利: ${s['total_pnl_net']} (目標 >$0)")
        
        time_stop_pct = s['exit_reasons'].get('TIME_STOP', 0) / s['total_trades'] * 100 if s['total_trades'] > 0 else 0
        print(f"  時間止損: {time_stop_pct:.1f}% (目標 <55%)")
        
        # 判斷是否達標
        goals_met = 0
        if s['win_rate'] >= 45:
            print(f"  ✅ 勝率達標")
            goals_met += 1
        else:
            print(f"  ⚠️ 勝率未達標 (差 {45 - s['win_rate']:.1f}%)")
        
        if s['total_pnl_net'] > 0:
            print(f"  ✅ 淨利轉正")
            goals_met += 1
        else:
            print(f"  ⚠️ 淨利未轉正 (差 ${-s['total_pnl_net']:.2f})")
        
        if time_stop_pct < 55:
            print(f"  ✅ 時間止損達標")
            goals_met += 1
        else:
            print(f"  ⚠️ 時間止損未達標 (超出 {time_stop_pct - 55:.1f}%)")
        
        print(f"\n  總計: {goals_met}/3 個目標達成")


if __name__ == '__main__':
    main()
