#!/usr/bin/env python3
"""
使用真實 Funding Rate 數據回測
測試 2020-2021 年（Funding 極端值較多的時期）
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json
from pathlib import Path


class RealFundingBacktester:
    """使用真實 Funding Rate 的回測器"""
    
    def __init__(
        self,
        initial_capital: float = 100.0,
        tp_pct: float = 0.0015,
        sl_pct: float = 0.0010,
        time_stop_minutes: int = 180,
        funding_long_threshold: float = 0.0010,  # 0.10% 做空
        funding_short_threshold: float = -0.0010,  # -0.10% 做多
        max_leverage: int = 20,
        taker_fee: float = 0.0004
    ):
        self.initial_capital = initial_capital
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.time_stop_minutes = time_stop_minutes
        self.funding_long_threshold = funding_long_threshold
        self.funding_short_threshold = funding_short_threshold
        self.max_leverage = max_leverage
        self.taker_fee = taker_fee
        
        self.trades: List[Dict] = []
        self.capital = initial_capital
        self.current_position = None
    
    def check_signal(self, row: pd.Series) -> Optional[Dict]:
        """檢查是否有信號"""
        funding = row['fundingRate']
        
        # 多頭過熱 → 做空
        if funding >= self.funding_long_threshold:
            return {
                'direction': 'SHORT',
                'trigger': 'funding_long_squeeze',
                'funding_rate': funding,
                'confidence': min(abs(funding) / 0.0030, 1.0)  # 0.30% = 100% 信心
            }
        
        # 空頭過熱 → 做多
        if funding <= self.funding_short_threshold:
            return {
                'direction': 'LONG',
                'trigger': 'funding_short_squeeze',
                'funding_rate': funding,
                'confidence': min(abs(funding) / 0.0030, 1.0)
            }
        
        return None
    
    def calculate_tp_sl(self, entry_price: float, direction: str) -> tuple:
        """計算 TP/SL"""
        if direction == 'LONG':
            tp = entry_price * (1 + self.tp_pct)
            sl = entry_price * (1 - self.sl_pct)
        else:  # SHORT
            tp = entry_price * (1 - self.tp_pct)
            sl = entry_price * (1 + self.sl_pct)
        return tp, sl
    
    def run_backtest(
        self,
        df: pd.DataFrame,
        start_date: str,
        end_date: str
    ) -> Dict:
        """運行回測"""
        print(f"\n{'='*70}")
        print(f"🔄 回測期間: {start_date} ~ {end_date}")
        print(f"{'='*70}")
        
        df = df[(df['timestamp'] >= start_date) & (df['timestamp'] < end_date)].copy()
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        print(f"數據量: {len(df):,} 根 K 線")
        
        # 統計 Funding 極端值
        extreme_count = ((df['fundingRate'] >= self.funding_long_threshold) | 
                        (df['fundingRate'] <= self.funding_short_threshold)).sum()
        print(f"極端 Funding 次數: {extreme_count} ({extreme_count/len(df)*100:.2f}%)")
        print()
        
        self.capital = self.initial_capital
        self.trades = []
        self.current_position = None
        
        for i in range(len(df)):
            row = df.iloc[i]
            
            # 檢查是否需要平倉
            if self.current_position:
                exit_info = self._check_exit(row, df[i:min(i+20, len(df))])
                if exit_info:
                    self._close_position(exit_info)
            
            # 檢查是否有新信號
            if not self.current_position:
                signal = self.check_signal(row)
                if signal:
                    self._open_position(row, signal)
        
        # 強制平倉未平倉位
        if self.current_position:
            self._close_position({
                'exit_time': df.iloc[-1]['timestamp'],
                'exit_price': df.iloc[-1]['close'],
                'reason': 'END_OF_PERIOD'
            })
        
        return self._generate_summary()
    
    def _open_position(self, row: pd.Series, signal: Dict):
        """開倉"""
        entry_price = row['close']
        tp, sl = self.calculate_tp_sl(entry_price, signal['direction'])
        
        # 動態槓桿（根據信心）
        leverage = int(self.max_leverage * signal['confidence'])
        leverage = max(10, min(leverage, self.max_leverage))
        
        self.current_position = {
            'entry_time': row['timestamp'],
            'entry_price': entry_price,
            'direction': signal['direction'],
            'tp': tp,
            'sl': sl,
            'leverage': leverage,
            'trigger': signal['trigger'],
            'funding_rate': signal['funding_rate'],
            'capital_at_entry': self.capital
        }
    
    def _check_exit(self, current_row: pd.Series, future_window: pd.DataFrame) -> Optional[Dict]:
        """檢查是否觸發出場"""
        pos = self.current_position
        
        # 時間止損
        time_elapsed = (current_row['timestamp'] - pos['entry_time']).total_seconds() / 60
        if time_elapsed > self.time_stop_minutes:
            return {
                'exit_time': current_row['timestamp'],
                'exit_price': current_row['close'],
                'reason': 'TIME_STOP'
            }
        
        # 檢查未來窗口
        for _, row in future_window.iterrows():
            if pos['direction'] == 'LONG':
                if row['high'] >= pos['tp']:
                    return {'exit_time': row['timestamp'], 'exit_price': pos['tp'], 'reason': 'TP'}
                if row['low'] <= pos['sl']:
                    return {'exit_time': row['timestamp'], 'exit_price': pos['sl'], 'reason': 'SL'}
            else:  # SHORT
                if row['low'] <= pos['tp']:
                    return {'exit_time': row['timestamp'], 'exit_price': pos['tp'], 'reason': 'TP'}
                if row['high'] >= pos['sl']:
                    return {'exit_time': row['timestamp'], 'exit_price': pos['sl'], 'reason': 'SL'}
        
        return None
    
    def _close_position(self, exit_info: Dict):
        """平倉"""
        pos = self.current_position
        
        # 計算盈虧
        if pos['direction'] == 'LONG':
            pnl_pct = (exit_info['exit_price'] - pos['entry_price']) / pos['entry_price']
        else:  # SHORT
            pnl_pct = (pos['entry_price'] - exit_info['exit_price']) / pos['entry_price']
        
        # 槓桿後盈虧
        pnl_pct_leveraged = pnl_pct * pos['leverage']
        
        # 扣除手續費（開倉+平倉）
        fee = 2 * self.taker_fee * pos['leverage']
        pnl_pct_final = pnl_pct_leveraged - fee
        
        # 更新資金
        pnl_dollar = pos['capital_at_entry'] * pnl_pct_final
        self.capital = pos['capital_at_entry'] + pnl_dollar
        
        # 記錄交易
        trade = {
            'entry_time': pos['entry_time'],
            'exit_time': exit_info['exit_time'],
            'direction': pos['direction'],
            'entry_price': pos['entry_price'],
            'exit_price': exit_info['exit_price'],
            'tp': pos['tp'],
            'sl': pos['sl'],
            'leverage': pos['leverage'],
            'trigger': pos['trigger'],
            'funding_rate': pos['funding_rate'],
            'exit_reason': exit_info['reason'],
            'pnl_pct': pnl_pct,
            'pnl_pct_leveraged': pnl_pct_leveraged,
            'pnl_pct_final': pnl_pct_final,
            'pnl_dollar': pnl_dollar,
            'capital_before': pos['capital_at_entry'],
            'capital_after': self.capital,
            'holding_minutes': (exit_info['exit_time'] - pos['entry_time']).total_seconds() / 60
        }
        self.trades.append(trade)
        self.current_position = None
    
    def _generate_summary(self) -> Dict:
        """生成統計摘要"""
        if not self.trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'final_capital': self.capital,
                'total_return_pct': 0
            }
        
        df_trades = pd.DataFrame(self.trades)
        
        wins = (df_trades['pnl_dollar'] > 0).sum()
        losses = (df_trades['pnl_dollar'] <= 0).sum()
        
        return {
            'total_trades': len(self.trades),
            'wins': int(wins),
            'losses': int(losses),
            'win_rate': wins / len(self.trades),
            'avg_pnl_pct': df_trades['pnl_pct_final'].mean(),
            'avg_holding_minutes': df_trades['holding_minutes'].mean(),
            'final_capital': self.capital,
            'total_return_pct': (self.capital - self.initial_capital) / self.initial_capital,
            'trades': self.trades
        }


def main():
    """主函數"""
    print("="*70)
    print("🚀 真實 Funding Rate 回測")
    print("="*70)
    
    # 讀取數據
    df = pd.read_parquet('data/historical/BTCUSDT_15m_with_l0.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    print(f"\n📂 數據範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    print(f"總 K 線數: {len(df):,} 根")
    
    # 分年測試（2020-2021 極端值較多）
    test_periods = [
        ('2020-01-01', '2021-01-01', '2020年（COVID暴跌+復甦）'),
        ('2021-01-01', '2022-01-01', '2021年（大牛市）'),
        ('2022-01-01', '2023-01-01', '2022年（熊市）'),
    ]
    
    all_results = {}
    
    for start, end, desc in test_periods:
        print(f"\n{'='*70}")
        print(f"📊 測試: {desc}")
        print(f"{'='*70}")
        
        backtester = RealFundingBacktester(
            initial_capital=100.0,
            tp_pct=0.0015,
            sl_pct=0.0010,
            time_stop_minutes=180,
            funding_long_threshold=0.0010,  # 0.10%
            funding_short_threshold=-0.0010,
            max_leverage=20
        )
        
        result = backtester.run_backtest(df, start, end)
        all_results[desc] = result
        
        print(f"\n📈 結果:")
        print(f"   總交易: {result['total_trades']} 筆")
        if result['total_trades'] > 0:
            print(f"   勝場: {result['wins']} | 敗場: {result['losses']}")
            print(f"   勝率: {result['win_rate']*100:.1f}%")
            print(f"   平均持倉: {result['avg_holding_minutes']:.1f} 分鐘")
            print(f"   最終資金: {result['final_capital']:.2f} U")
            print(f"   總回報: {result['total_return_pct']*100:+.1f}%")
            
            # 按月統計
            df_trades = pd.DataFrame(result['trades'])
            df_trades['month'] = pd.to_datetime(df_trades['entry_time']).dt.to_period('M')
            monthly = df_trades.groupby('month').size()
            print(f"   每月平均: {monthly.mean():.1f} 筆")
        else:
            print("   ⚠️  沒有任何交易")
    
    # 保存結果
    output_path = Path('backtest_results/real_funding_test.json')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 轉換為可序列化格式
    for key in all_results:
        if 'trades' in all_results[key]:
            for trade in all_results[key]['trades']:
                trade['entry_time'] = str(trade['entry_time'])
                trade['exit_time'] = str(trade['exit_time'])
    
    with open(output_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    
    print(f"\n💾 結果已保存: {output_path}")
    
    # 總結
    print(f"\n{'='*70}")
    print("🎯 總結")
    print(f"{'='*70}")
    
    for desc, result in all_results.items():
        if result['total_trades'] > 0:
            print(f"{desc}:")
            print(f"  {result['total_trades']} 筆交易, {result['win_rate']*100:.1f}% 勝率, {result['total_return_pct']*100:+.1f}% 回報")
        else:
            print(f"{desc}: 無交易")


if __name__ == "__main__":
    main()
