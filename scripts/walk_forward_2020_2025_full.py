#!/usr/bin/env python3
"""
完整 Walk-Forward 測試：2020-2025
使用技術指標策略（RSI + MA + Volume）測試長期穩定性
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict
import json
from pathlib import Path


@dataclass
class StrategyConfig:
    """策略配置"""
    rsi_oversold: int = 30
    rsi_overbought: int = 70
    volume_spike_threshold: float = 2.0  # 2x 平均成交量
    min_confidence: float = 0.6
    tp_pct: float = 0.0015
    sl_pct: float = 0.0010
    time_stop_minutes: int = 180
    leverage: int = 15
    fee_per_trade: float = 0.0004  # 0.04%


@dataclass
class TradeResult:
    """交易結果"""
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    side: str
    pnl_pct: float
    pnl_with_leverage: float
    exit_reason: str
    confidence: float


class TechnicalStrategy:
    """技術指標策略（替代大單策略用於長期測試）"""
    
    @staticmethod
    def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        """計算 RSI"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    @staticmethod
    def generate_signal(df: pd.DataFrame, idx: int, config: StrategyConfig) -> tuple:
        """生成交易信號"""
        row = df.iloc[idx]
        
        # 檢查數據完整性
        if pd.isna(row['rsi']) or pd.isna(row['ma7']) or pd.isna(row['ma25']):
            return 'NEUTRAL', 0.0
        
        score = 0.0
        
        # === RSI 信號（權重 40%）===
        rsi = row['rsi']
        if rsi < config.rsi_oversold:
            score += 0.4 * (config.rsi_oversold - rsi) / config.rsi_oversold
        elif rsi > config.rsi_overbought:
            score -= 0.4 * (rsi - config.rsi_overbought) / (100 - config.rsi_overbought)
        
        # === MA 趨勢（權重 30%）===
        if row['ma7'] > row['ma25']:
            ma_strength = (row['ma7'] - row['ma25']) / row['ma25']
            score += 0.3 * min(ma_strength * 100, 1.0)
        elif row['ma7'] < row['ma25']:
            ma_strength = (row['ma25'] - row['ma7']) / row['ma25']
            score -= 0.3 * min(ma_strength * 100, 1.0)
        
        # === 成交量突增（權重 30%）===
        if 'volume_ma7' in row and not pd.isna(row['volume_ma7']):
            volume_ratio = row['volume'] / row['volume_ma7']
            if volume_ratio > config.volume_spike_threshold:
                # 成交量突增 + 價格上漲 = 買入信號
                if row['close'] > row['open']:
                    score += 0.3 * min((volume_ratio - config.volume_spike_threshold), 1.0)
                else:
                    score -= 0.3 * min((volume_ratio - config.volume_spike_threshold), 1.0)
        
        # === 判斷信號 ===
        signal = 'NEUTRAL'
        confidence = 0.0
        
        if score > 0.5:
            signal = 'LONG'
            confidence = min(abs(score), 1.0)
        elif score < -0.5:
            signal = 'SHORT'
            confidence = min(abs(score), 1.0)
        
        return signal, confidence


class YearlyWalkForwardTester:
    """年度 Walk-Forward 測試器"""
    
    def __init__(self, data_file: str):
        print("="*70)
        print("🔄 Walk-Forward 測試：2020-2025 完整測試")
        print("="*70)
        print()
        
        # 載入數據
        print("📂 載入數據...")
        self.df = pd.read_parquet(data_file)
        self.df['timestamp'] = pd.to_datetime(self.df['timestamp'])
        
        # 計算技術指標
        print("📊 計算技術指標...")
        self.df['rsi'] = TechnicalStrategy.calculate_rsi(self.df['close'], period=14)
        self.df['ma7'] = self.df['close'].rolling(window=7).mean()
        self.df['ma25'] = self.df['close'].rolling(window=25).mean()
        self.df['volume_ma7'] = self.df['volume'].rolling(window=7).mean()
        
        # 過濾完整數據
        self.df = self.df.dropna(subset=['rsi', 'ma7', 'ma25'])
        
        print(f"✅ 數據準備完成")
        print(f"   時間範圍: {self.df['timestamp'].min()} ~ {self.df['timestamp'].max()}")
        print(f"   K線數量: {len(self.df):,}")
        print()
    
    def run_backtest_for_year(self, year: int, config: StrategyConfig) -> List[TradeResult]:
        """運行指定年份的回測"""
        df_year = self.df[self.df['timestamp'].dt.year == year].copy()
        
        if len(df_year) == 0:
            return []
        
        trades = []
        position = None
        
        for i in range(len(df_year)):
            current_time = df_year.iloc[i]['timestamp']
            current_price = df_year.iloc[i]['close']
            
            # 檢查平倉
            if position is not None:
                exit_signal = None
                exit_reason = None
                
                if position['side'] == 'LONG':
                    pnl_pct = (current_price - position['entry_price']) / position['entry_price']
                else:
                    pnl_pct = (position['entry_price'] - current_price) / position['entry_price']
                
                # 止盈
                if pnl_pct >= config.tp_pct:
                    exit_signal = True
                    exit_reason = 'TP'
                # 止損
                elif pnl_pct <= -config.sl_pct:
                    exit_signal = True
                    exit_reason = 'SL'
                # 時間止損
                elif (current_time - position['entry_time']).total_seconds() / 60 >= config.time_stop_minutes:
                    exit_signal = True
                    exit_reason = 'TIME_STOP'
                
                if exit_signal:
                    pnl_with_leverage = pnl_pct * config.leverage
                    
                    trade = TradeResult(
                        entry_time=position['entry_time'],
                        entry_price=position['entry_price'],
                        exit_time=current_time,
                        exit_price=current_price,
                        side=position['side'],
                        pnl_pct=pnl_pct,
                        pnl_with_leverage=pnl_with_leverage,
                        exit_reason=exit_reason,
                        confidence=position['confidence']
                    )
                    trades.append(trade)
                    position = None
            
            # 檢查開倉
            if position is None:
                signal, confidence = TechnicalStrategy.generate_signal(df_year, i, config)
                
                if signal != 'NEUTRAL' and confidence >= config.min_confidence:
                    position = {
                        'side': signal,
                        'entry_time': current_time,
                        'entry_price': current_price,
                        'confidence': confidence
                    }
        
        # 強制平倉
        if position is not None:
            last_price = df_year.iloc[-1]['close']
            last_time = df_year.iloc[-1]['timestamp']
            
            if position['side'] == 'LONG':
                pnl_pct = (last_price - position['entry_price']) / position['entry_price']
            else:
                pnl_pct = (position['entry_price'] - last_price) / position['entry_price']
            
            pnl_with_leverage = pnl_pct * config.leverage
            
            trade = TradeResult(
                entry_time=position['entry_time'],
                entry_price=position['entry_price'],
                exit_time=last_time,
                exit_price=last_price,
                side=position['side'],
                pnl_pct=pnl_pct,
                pnl_with_leverage=pnl_with_leverage,
                exit_reason='END_OF_YEAR',
                confidence=position['confidence']
            )
            trades.append(trade)
        
        return trades
    
    def run_walk_forward(self):
        """運行完整 Walk-Forward 測試"""
        print("🔄 開始 Walk-Forward 測試...")
        print()
        
        # 使用寬鬆配置（確保產生足夠交易）
        config = StrategyConfig(
            rsi_oversold=35,  # 放寬
            rsi_overbought=65,  # 放寬
            volume_spike_threshold=1.5,  # 降低
            min_confidence=0.4,  # 降低（原 0.6）
            tp_pct=0.0015,
            sl_pct=0.0010,
            time_stop_minutes=180,
            leverage=15,
            fee_per_trade=0.0004
        )
        
        years = [2020, 2021, 2022, 2023, 2024, 2025]
        all_results = {}
        
        for year in years:
            print(f"📅 測試 {year} 年...")
            
            trades = self.run_backtest_for_year(year, config)
            
            if len(trades) == 0:
                print(f"   ⚠️ 無交易")
                print()
                continue
            
            # 計算指標
            wins = sum(1 for t in trades if t.pnl_with_leverage > 0)
            losses = len(trades) - wins
            win_rate = wins / len(trades) * 100
            total_return = sum(t.pnl_with_leverage for t in trades)
            
            # 扣除手續費
            total_fee = len(trades) * config.fee_per_trade * config.leverage
            total_return_after_fee = total_return - total_fee
            
            # 年度天數
            year_df = self.df[self.df['timestamp'].dt.year == year]
            days = (year_df['timestamp'].max() - year_df['timestamp'].min()).days + 1
            trades_per_day = len(trades) / days
            
            # 保存結果
            all_results[year] = {
                'trades': len(trades),
                'wins': wins,
                'losses': losses,
                'win_rate': win_rate,
                'total_return': total_return,
                'total_return_after_fee': total_return_after_fee,
                'trades_per_day': trades_per_day,
                'days': days
            }
            
            print(f"   交易數: {len(trades)}")
            print(f"   勝率: {win_rate:.1f}%")
            print(f"   頻率: {trades_per_day:.2f} 筆/天")
            print(f"   回報（扣費前）: {total_return:+.2f}%")
            print(f"   回報（扣費後）: {total_return_after_fee:+.2f}%")
            print()
        
        return all_results, config
    
    def print_summary(self, results: Dict):
        """打印總結"""
        print("="*70)
        print("📊 Walk-Forward 測試總結（2020-2025）")
        print("="*70)
        print()
        
        print("年度表現:")
        print()
        print("年份  交易數  勝率    頻率      扣費前     扣費後")
        print("-" * 60)
        
        for year, data in sorted(results.items()):
            print(f"{year}  {data['trades']:4d}    {data['win_rate']:5.1f}%  "
                  f"{data['trades_per_day']:4.2f}/天  {data['total_return']:+7.2f}%  "
                  f"{data['total_return_after_fee']:+7.2f}%")
        
        print()
        
        # 總計
        total_trades = sum(r['trades'] for r in results.values())
        total_wins = sum(r['wins'] for r in results.values())
        overall_win_rate = total_wins / total_trades * 100 if total_trades > 0 else 0
        total_return_before = sum(r['total_return'] for r in results.values())
        total_return_after = sum(r['total_return_after_fee'] for r in results.values())
        
        print(f"總計  {total_trades:4d}    {overall_win_rate:5.1f}%  "
              f"    -     {total_return_before:+7.2f}%  {total_return_after:+7.2f}%")
        print()
        
        # 關鍵洞察
        print("🔍 關鍵發現:")
        print()
        
        best_year = max(results.items(), key=lambda x: x[1]['total_return_after_fee'])
        worst_year = min(results.items(), key=lambda x: x[1]['total_return_after_fee'])
        
        print(f"   最佳年份: {best_year[0]} ({best_year[1]['total_return_after_fee']:+.2f}%)")
        print(f"   最差年份: {worst_year[0]} ({worst_year[1]['total_return_after_fee']:+.2f}%)")
        print()
        
        positive_years = sum(1 for r in results.values() if r['total_return_after_fee'] > 0)
        print(f"   盈利年份: {positive_years}/{len(results)}")
        print()
    
    def save_results(self, results: Dict, config: StrategyConfig):
        """保存結果"""
        output_file = 'backtest_results/walk_forward_2020_2025_full.json'
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        
        output = {
            'test_period': '2020-2025',
            'strategy': 'Technical Indicators (RSI + MA + Volume)',
            'config': {
                'rsi_oversold': config.rsi_oversold,
                'rsi_overbought': config.rsi_overbought,
                'volume_spike_threshold': config.volume_spike_threshold,
                'min_confidence': config.min_confidence,
                'tp_pct': config.tp_pct,
                'sl_pct': config.sl_pct,
                'time_stop_minutes': config.time_stop_minutes,
                'leverage': config.leverage,
                'fee_per_trade': config.fee_per_trade
            },
            'yearly_results': results,
            'summary': {
                'total_trades': sum(r['trades'] for r in results.values()),
                'overall_win_rate': sum(r['wins'] for r in results.values()) / sum(r['trades'] for r in results.values()) * 100 if sum(r['trades'] for r in results.values()) > 0 else 0,
                'total_return_before_fee': sum(r['total_return'] for r in results.values()),
                'total_return_after_fee': sum(r['total_return_after_fee'] for r in results.values())
            }
        }
        
        with open(output_file, 'w') as f:
            json.dump(output, f, indent=2, default=str)
        
        print(f"💾 結果已保存: {output_file}")
        print()


def main():
    """主函數"""
    data_file = 'data/historical/BTCUSDT_15m.parquet'
    
    tester = YearlyWalkForwardTester(data_file)
    results, config = tester.run_walk_forward()
    tester.print_summary(results)
    tester.save_results(results, config)
    
    print("="*70)
    print("✅ Walk-Forward 測試完成！")
    print("="*70)


if __name__ == '__main__':
    main()
