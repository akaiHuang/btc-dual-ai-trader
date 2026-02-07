#!/usr/bin/env python3
"""
Walk-Forward Optimization - 真實 Funding Rate 版本

方法：
1. 用 2020 年數據訓練/優化參數
2. 用 2021 年測試（Out-of-Sample）
3. 根據 2021 年結果修正參數
4. 用 2022 年測試
5. 持續到 2025 年

目標：
- 每年至少 10 筆/天交易
- 3-5 天獲利 100%（需要每天 26% 回報）
- 勝率 70%+
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json
from pathlib import Path
from dataclasses import dataclass, asdict


@dataclass
class StrategyConfig:
    """策略配置"""
    version: str
    year: int
    
    # Funding 閾值
    funding_long_threshold: float = 0.0010  # 做空閾值
    funding_short_threshold: float = -0.0010  # 做多閾值
    
    # TP/SL
    tp_pct: float = 0.0015
    sl_pct: float = 0.0010
    time_stop_minutes: int = 180
    
    # 槓桿
    max_leverage: int = 20
    min_leverage: int = 10
    
    # 費用
    taker_fee: float = 0.0004
    
    # 額外過濾
    min_confidence: float = 0.50  # 最低信心
    use_trend_filter: bool = False  # 是否使用趨勢過濾


class RealFundingStrategy:
    """真實 Funding Rate 策略"""
    
    def __init__(self, config: StrategyConfig):
        self.config = config
    
    def calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """計算技術指標"""
        df = df.copy()
        
        # MA
        df['ma_7'] = df['close'].rolling(7).mean()
        df['ma_25'] = df['close'].rolling(25).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        return df
    
    def check_signal(self, df: pd.DataFrame, idx: int) -> Optional[Dict]:
        """檢查信號"""
        row = df.iloc[idx]
        funding = row['fundingRate']
        
        # 基本 Funding 檢查
        direction = None
        trigger = None
        
        if funding >= self.config.funding_long_threshold:
            direction = 'SHORT'
            trigger = 'funding_long_squeeze'
        elif funding <= self.config.funding_short_threshold:
            direction = 'LONG'
            trigger = 'funding_short_squeeze'
        else:
            return None
        
        # 計算信心（基於 Funding 強度）
        confidence = min(abs(funding) / 0.0030, 1.0)
        
        if confidence < self.config.min_confidence:
            return None
        
        # 趨勢過濾（可選）
        if self.config.use_trend_filter:
            if pd.isna(row['ma_7']) or pd.isna(row['ma_25']):
                return None
            
            # 多頭趨勢：MA7 > MA25
            is_uptrend = row['ma_7'] > row['ma_25']
            
            # Funding 做空信號需要在上升趨勢中（逆勢）
            if direction == 'SHORT' and not is_uptrend:
                return None
            
            # Funding 做多信號需要在下跌趨勢中（逆勢）
            if direction == 'LONG' and is_uptrend:
                return None
        
        return {
            'direction': direction,
            'trigger': trigger,
            'funding_rate': funding,
            'confidence': confidence,
            'rsi': row.get('rsi', None)
        }


class WalkForwardBacktester:
    """Walk-Forward 回測器"""
    
    def __init__(self, initial_capital: float = 100.0):
        self.initial_capital = initial_capital
        
    def run_backtest(
        self,
        df: pd.DataFrame,
        config: StrategyConfig,
        year: int
    ) -> Dict:
        """運行回測"""
        # 篩選年份數據
        df_year = df[df['timestamp'].dt.year == year].copy()
        df_year = df_year.sort_values('timestamp').reset_index(drop=True)
        
        print(f"\n{'='*70}")
        print(f"📊 回測 {year} 年（使用 {config.year} 年優化的參數）")
        print(f"{'='*70}")
        print(f"數據量: {len(df_year):,} 根 K 線")
        
        # 計算技術指標
        strategy = RealFundingStrategy(config)
        df_year = strategy.calculate_technical_indicators(df_year)
        
        # 統計極端 Funding
        extreme_count = (
            (df_year['fundingRate'] >= config.funding_long_threshold) |
            (df_year['fundingRate'] <= config.funding_short_threshold)
        ).sum()
        print(f"極端 Funding 次數: {extreme_count} ({extreme_count/len(df_year)*100:.2f}%)")
        
        # 回測主循環
        capital = self.initial_capital
        trades = []
        current_position = None
        
        for i in range(len(df_year)):
            row = df_year.iloc[i]
            
            # 檢查平倉
            if current_position:
                future_window = df_year[i:min(i+20, len(df_year))]
                exit_info = self._check_exit(current_position, row, future_window)
                if exit_info:
                    trade = self._close_position(current_position, exit_info, capital, config)
                    trades.append(trade)
                    capital = trade['capital_after']
                    current_position = None
                    
                    if capital <= 0:
                        break
            
            # 檢查開倉
            if not current_position:
                signal = strategy.check_signal(df_year, i)
                if signal:
                    current_position = self._open_position(row, signal, capital, config)
        
        # 強制平倉
        if current_position:
            exit_info = {
                'exit_time': df_year.iloc[-1]['timestamp'],
                'exit_price': df_year.iloc[-1]['close'],
                'reason': 'END_OF_PERIOD'
            }
            trade = self._close_position(current_position, exit_info, capital, config)
            trades.append(trade)
            capital = trade['capital_after']
        
        return self._generate_summary(trades, capital, year, len(df_year))
    
    def _open_position(self, row: pd.Series, signal: Dict, capital: float, config: StrategyConfig) -> Dict:
        """開倉"""
        entry_price = row['close']
        
        if signal['direction'] == 'LONG':
            tp = entry_price * (1 + config.tp_pct)
            sl = entry_price * (1 - config.sl_pct)
        else:
            tp = entry_price * (1 - config.tp_pct)
            sl = entry_price * (1 + config.sl_pct)
        
        # 動態槓桿
        leverage = int(config.min_leverage + (config.max_leverage - config.min_leverage) * signal['confidence'])
        leverage = max(config.min_leverage, min(leverage, config.max_leverage))
        
        return {
            'entry_time': row['timestamp'],
            'entry_price': entry_price,
            'direction': signal['direction'],
            'tp': tp,
            'sl': sl,
            'leverage': leverage,
            'trigger': signal['trigger'],
            'funding_rate': signal['funding_rate'],
            'capital_at_entry': capital
        }
    
    def _check_exit(self, pos: Dict, current_row: pd.Series, future_window: pd.DataFrame) -> Optional[Dict]:
        """檢查出場"""
        # 時間止損
        time_elapsed = (current_row['timestamp'] - pos['entry_time']).total_seconds() / 60
        if time_elapsed > 180:  # 3小時
            return {
                'exit_time': current_row['timestamp'],
                'exit_price': current_row['close'],
                'reason': 'TIME_STOP'
            }
        
        # 價格止損/止盈
        for _, row in future_window.iterrows():
            if pos['direction'] == 'LONG':
                if row['high'] >= pos['tp']:
                    return {'exit_time': row['timestamp'], 'exit_price': pos['tp'], 'reason': 'TP'}
                if row['low'] <= pos['sl']:
                    return {'exit_time': row['timestamp'], 'exit_price': pos['sl'], 'reason': 'SL'}
            else:
                if row['low'] <= pos['tp']:
                    return {'exit_time': row['timestamp'], 'exit_price': pos['tp'], 'reason': 'TP'}
                if row['high'] >= pos['sl']:
                    return {'exit_time': row['timestamp'], 'exit_price': pos['sl'], 'reason': 'SL'}
        
        return None
    
    def _close_position(self, pos: Dict, exit_info: Dict, capital: float, config: StrategyConfig) -> Dict:
        """平倉"""
        if pos['direction'] == 'LONG':
            pnl_pct = (exit_info['exit_price'] - pos['entry_price']) / pos['entry_price']
        else:
            pnl_pct = (pos['entry_price'] - exit_info['exit_price']) / pos['entry_price']
        
        pnl_pct_leveraged = pnl_pct * pos['leverage']
        fee = 2 * config.taker_fee * pos['leverage']
        pnl_pct_final = pnl_pct_leveraged - fee
        
        pnl_dollar = pos['capital_at_entry'] * pnl_pct_final
        new_capital = pos['capital_at_entry'] + pnl_dollar
        
        return {
            'entry_time': pos['entry_time'],
            'exit_time': exit_info['exit_time'],
            'direction': pos['direction'],
            'entry_price': pos['entry_price'],
            'exit_price': exit_info['exit_price'],
            'leverage': pos['leverage'],
            'exit_reason': exit_info['reason'],
            'pnl_pct_final': pnl_pct_final,
            'pnl_dollar': pnl_dollar,
            'capital_before': pos['capital_at_entry'],
            'capital_after': new_capital,
            'holding_minutes': (exit_info['exit_time'] - pos['entry_time']).total_seconds() / 60
        }
    
    def _generate_summary(self, trades: List[Dict], final_capital: float, year: int, total_candles: int) -> Dict:
        """生成摘要"""
        if not trades:
            return {
                'year': year,
                'total_trades': 0,
                'win_rate': 0,
                'final_capital': final_capital,
                'total_return_pct': (final_capital - self.initial_capital) / self.initial_capital,
                'trades_per_day': 0,
                'avg_holding_minutes': 0
            }
        
        df_trades = pd.DataFrame(trades)
        wins = (df_trades['pnl_dollar'] > 0).sum()
        
        # 計算天數
        days = total_candles * 15 / 60 / 24
        
        return {
            'year': year,
            'total_trades': len(trades),
            'wins': int(wins),
            'losses': len(trades) - int(wins),
            'win_rate': wins / len(trades),
            'final_capital': final_capital,
            'total_return_pct': (final_capital - self.initial_capital) / self.initial_capital,
            'trades_per_day': len(trades) / days,
            'avg_holding_minutes': df_trades['holding_minutes'].mean(),
            'trades': trades
        }


class WalkForwardOptimizer:
    """Walk-Forward 優化器"""
    
    def __init__(self):
        self.backtester = WalkForwardBacktester(initial_capital=100.0)
    
    def optimize_on_year(self, df: pd.DataFrame, train_year: int) -> StrategyConfig:
        """在指定年份上優化參數"""
        print(f"\n{'='*70}")
        print(f"🔧 使用 {train_year} 年數據優化參數...")
        print(f"{'='*70}")
        
        # 測試不同閾值
        thresholds = [0.0010, 0.0015, 0.0020]
        best_config = None
        best_score = -999999
        
        for threshold in thresholds:
            config = StrategyConfig(
                version=f"wf_v1",
                year=train_year,
                funding_long_threshold=threshold,
                funding_short_threshold=-threshold,
                tp_pct=0.0015,
                sl_pct=0.0010,
                min_confidence=0.50,
                max_leverage=20
            )
            
            result = self.backtester.run_backtest(df, config, train_year)
            
            # 評分：勝率 * 交易數量 * 回報率
            if result['total_trades'] > 0:
                score = (
                    result['win_rate'] * 100 +
                    min(result['trades_per_day'], 20) * 5 +  # 鼓勵 10-20 筆/天
                    result['total_return_pct'] * 50
                )
                
                print(f"   閾值 {threshold:.4f}: {result['total_trades']}筆, "
                      f"{result['win_rate']*100:.1f}%勝率, "
                      f"{result['trades_per_day']:.1f}筆/天, "
                      f"{result['total_return_pct']*100:+.1f}%回報 "
                      f"→ 評分 {score:.1f}")
                
                if score > best_score:
                    best_score = score
                    best_config = config
        
        # 如果沒有找到有效配置，使用默認值
        if best_config is None:
            print(f"\n⚠️  警告：{train_year}年沒有極端Funding，使用默認配置")
            best_config = StrategyConfig(
                version=f"wf_v1",
                year=train_year,
                funding_long_threshold=0.0010,
                funding_short_threshold=-0.0010,
                tp_pct=0.0015,
                sl_pct=0.0010,
                min_confidence=0.50,
                max_leverage=20
            )
        else:
            print(f"\n✅ 最佳配置: 閾值 {best_config.funding_long_threshold:.4f}")
        
        return best_config
    
    def run_walk_forward(self, df: pd.DataFrame) -> Dict:
        """執行完整 Walk-Forward"""
        print("\n" + "="*70)
        print("🚀 Walk-Forward Optimization - 2020→2025")
        print("="*70)
        
        years = [2020, 2021, 2022, 2023, 2024, 2025]
        all_results = {}
        
        # 用 2020 優化
        current_config = self.optimize_on_year(df, 2020)
        
        for i, year in enumerate(years):
            print(f"\n{'='*70}")
            print(f"📈 測試 {year} 年（使用前一年優化的參數）")
            print(f"{'='*70}")
            
            # 運行測試
            result = self.backtester.run_backtest(df, current_config, year)
            all_results[year] = {
                'config': asdict(current_config),
                'result': result
            }
            
            # 顯示結果
            print(f"\n結果:")
            print(f"   總交易: {result['total_trades']} 筆")
            if result['total_trades'] > 0:
                print(f"   勝率: {result['win_rate']*100:.1f}%")
                print(f"   交易頻率: {result['trades_per_day']:.1f} 筆/天")
                print(f"   最終資金: {result['final_capital']:.2f} U")
                print(f"   回報率: {result['total_return_pct']*100:+.1f}%")
                
                # 檢查是否達到 3-5 天翻倍目標
                days_to_double = self._calculate_days_to_double(result['total_return_pct'], year)
                if days_to_double:
                    print(f"   💰 預估翻倍時間: {days_to_double:.1f} 天")
            
            # 如果不是最後一年，用當前年份重新優化
            if i < len(years) - 1:
                current_config = self.optimize_on_year(df, year)
        
        return all_results
    
    def _calculate_days_to_double(self, annual_return: float, year: int) -> Optional[float]:
        """計算翻倍所需天數"""
        if annual_return <= 0:
            return None
        
        # 假設年回報率均勻分布
        days_in_year = 365
        daily_return = (1 + annual_return) ** (1/days_in_year) - 1
        
        if daily_return <= 0:
            return None
        
        # 計算 100U → 200U 需要多少天
        days_to_double = np.log(2) / np.log(1 + daily_return)
        return days_to_double


def main():
    """主函數"""
    print("="*70)
    print("🎯 Walk-Forward Optimization - 真實 Funding Rate")
    print("="*70)
    
    # 讀取數據
    df = pd.read_parquet('data/historical/BTCUSDT_15m_with_l0.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    print(f"\n📂 數據: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    print(f"總 K 線: {len(df):,} 根")
    
    # 運行 Walk-Forward
    optimizer = WalkForwardOptimizer()
    all_results = optimizer.run_walk_forward(df)
    
    # 保存結果
    output_path = Path('backtest_results/walk_forward_real_funding.json')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 序列化
    for year in all_results:
        if 'result' in all_results[year] and 'trades' in all_results[year]['result']:
            for trade in all_results[year]['result']['trades']:
                trade['entry_time'] = str(trade['entry_time'])
                trade['exit_time'] = str(trade['exit_time'])
    
    with open(output_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    
    print(f"\n💾 結果已保存: {output_path}")
    
    # 總結
    print(f"\n{'='*70}")
    print("📊 Walk-Forward 總結")
    print(f"{'='*70}")
    
    for year in sorted(all_results.keys()):
        result = all_results[year]['result']
        if result['total_trades'] > 0:
            print(f"{year}: {result['total_trades']:>4}筆, "
                  f"{result['win_rate']*100:>5.1f}%勝率, "
                  f"{result['trades_per_day']:>5.1f}筆/天, "
                  f"{result['total_return_pct']*100:>+7.1f}%回報")
        else:
            print(f"{year}: 無交易")


if __name__ == "__main__":
    main()
