#!/usr/bin/env python3
"""
階段2: 使用真實大單數據的 Walk-Forward 回測
測試方案A策略：大單流向 + 技術指標
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import json
from pathlib import Path


@dataclass
class StrategyConfig:
    """策略配置"""
    # 大單閾值
    large_trade_threshold: float = 5.0  # BTC，最小大單定義
    imbalance_threshold: float = 0.3  # 不平衡度閾值 [-1, 1]
    min_trade_count: int = 3  # 最少大單數量
    
    # 技術指標
    rsi_oversold: float = 30
    rsi_overbought: float = 70
    use_ma_filter: bool = True  # 是否使用MA趨勢過濾
    
    # 風控參數
    tp_pct: float = 0.0015  # 止盈 0.15%
    sl_pct: float = 0.0010  # 止損 0.10%
    time_stop_minutes: int = 180  # 時間止損 3小時
    leverage: int = 15  # 槓桿
    
    # 信心度閾值
    min_confidence: float = 0.5  # 最低信心度才交易


@dataclass
class TradeResult:
    """交易結果"""
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    side: str  # 'LONG' or 'SHORT'
    pnl_pct: float
    pnl_with_leverage: float
    exit_reason: str  # 'TP', 'SL', 'TIME_STOP'
    confidence: float
    large_trade_count: int
    large_trade_imbalance: float


class RealLargeTradeBacktester:
    """真實大單數據回測引擎"""
    
    def __init__(self, config: StrategyConfig):
        self.config = config
        
    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Tuple[str, float]:
        """
        生成交易信號
        
        Args:
            df: K線數據（含大單特徵）
            current_idx: 當前K線索引
            
        Returns:
            (signal, confidence): 信號類型和信心度
        """
        row = df.iloc[current_idx]
        
        # 檢查數據完整性
        if pd.isna(row['rsi']) or pd.isna(row['ma7']) or pd.isna(row['ma25']):
            return 'NEUTRAL', 0.0
        
        # 檢查是否有大單數據
        if row['large_trade_count'] < self.config.min_trade_count:
            return 'NEUTRAL', 0.0
        
        signal = 'NEUTRAL'
        confidence = 0.0
        reasons = []
        
        # === 1. 大單信號（權重 50%）===
        large_trade_score = 0.0
        imbalance = row['large_trade_imbalance']
        
        if imbalance > self.config.imbalance_threshold:
            # 買入壓力
            large_trade_score = 0.5
            reasons.append(f"大單買入 ({imbalance:.2f})")
        elif imbalance < -self.config.imbalance_threshold:
            # 賣出壓力
            large_trade_score = -0.5
            reasons.append(f"大單賣出 ({imbalance:.2f})")
        
        # 巨鯨加成
        if row['whale_detected']:
            large_trade_score *= 1.2
            reasons.append(f"巨鯨 ({row['max_single_trade']:.0f} BTC)")
        
        # === 2. RSI信號（權重 25%）===
        rsi_score = 0.0
        rsi = row['rsi']
        
        if rsi < self.config.rsi_oversold:
            rsi_score = 0.25
            reasons.append(f"RSI超賣 ({rsi:.0f})")
        elif rsi > self.config.rsi_overbought:
            rsi_score = -0.25
            reasons.append(f"RSI超買 ({rsi:.0f})")
        
        # === 3. MA趨勢信號（權重 25%）===
        ma_score = 0.0
        if self.config.use_ma_filter:
            if row['ma7'] > row['ma25']:
                ma_score = 0.25
                reasons.append("MA多頭")
            elif row['ma7'] < row['ma25']:
                ma_score = -0.25
                reasons.append("MA空頭")
        
        # === 綜合評分 ===
        total_score = large_trade_score + rsi_score + ma_score
        
        # 確定信號和信心度
        if total_score > 0.5:
            signal = 'LONG'
            confidence = min(abs(total_score), 1.0)
        elif total_score < -0.5:
            signal = 'SHORT'
            confidence = min(abs(total_score), 1.0)
        else:
            signal = 'NEUTRAL'
            confidence = 0.0
        
        return signal, confidence
    
    def run_backtest(self, df: pd.DataFrame, start_date: str, end_date: str) -> List[TradeResult]:
        """
        運行回測
        
        Args:
            df: K線數據（含大單特徵）
            start_date: 開始日期
            end_date: 結束日期
            
        Returns:
            交易結果列表
        """
        df = df.copy()
        df = df[(df['timestamp'] >= start_date) & (df['timestamp'] <= end_date)]
        
        if len(df) == 0:
            return []
        
        trades = []
        position = None  # 當前持倉
        
        for i in range(len(df)):
            current_time = df.iloc[i]['timestamp']
            current_price = df.iloc[i]['close']
            
            # 檢查是否需要平倉
            if position is not None:
                exit_signal = None
                exit_reason = None
                
                # 計算當前盈虧
                if position['side'] == 'LONG':
                    pnl_pct = (current_price - position['entry_price']) / position['entry_price']
                else:  # SHORT
                    pnl_pct = (position['entry_price'] - current_price) / position['entry_price']
                
                # 檢查止盈
                if pnl_pct >= self.config.tp_pct:
                    exit_signal = True
                    exit_reason = 'TP'
                
                # 檢查止損
                elif pnl_pct <= -self.config.sl_pct:
                    exit_signal = True
                    exit_reason = 'SL'
                
                # 檢查時間止損
                elif (current_time - position['entry_time']).total_seconds() / 60 >= self.config.time_stop_minutes:
                    exit_signal = True
                    exit_reason = 'TIME_STOP'
                
                # 平倉
                if exit_signal:
                    pnl_with_leverage = pnl_pct * self.config.leverage
                    
                    trade = TradeResult(
                        entry_time=position['entry_time'],
                        entry_price=position['entry_price'],
                        exit_time=current_time,
                        exit_price=current_price,
                        side=position['side'],
                        pnl_pct=pnl_pct,
                        pnl_with_leverage=pnl_with_leverage,
                        exit_reason=exit_reason,
                        confidence=position['confidence'],
                        large_trade_count=position['large_trade_count'],
                        large_trade_imbalance=position['large_trade_imbalance']
                    )
                    trades.append(trade)
                    position = None
            
            # 如果無持倉，檢查是否開倉
            if position is None:
                signal, confidence = self.generate_signal(df, i)
                
                if signal != 'NEUTRAL' and confidence >= self.config.min_confidence:
                    # 開倉
                    position = {
                        'side': signal,
                        'entry_time': current_time,
                        'entry_price': current_price,
                        'confidence': confidence,
                        'large_trade_count': df.iloc[i]['large_trade_count'],
                        'large_trade_imbalance': df.iloc[i]['large_trade_imbalance']
                    }
        
        # 如果回測結束時還有持倉，強制平倉
        if position is not None:
            last_price = df.iloc[-1]['close']
            last_time = df.iloc[-1]['timestamp']
            
            if position['side'] == 'LONG':
                pnl_pct = (last_price - position['entry_price']) / position['entry_price']
            else:
                pnl_pct = (position['entry_price'] - last_price) / position['entry_price']
            
            pnl_with_leverage = pnl_pct * self.config.leverage
            
            trade = TradeResult(
                entry_time=position['entry_time'],
                entry_price=position['entry_price'],
                exit_time=last_time,
                exit_price=last_price,
                side=position['side'],
                pnl_pct=pnl_pct,
                pnl_with_leverage=pnl_with_leverage,
                exit_reason='END_OF_TEST',
                confidence=position['confidence'],
                large_trade_count=position['large_trade_count'],
                large_trade_imbalance=position['large_trade_imbalance']
            )
            trades.append(trade)
        
        return trades


class WalkForwardOptimizer:
    """Walk-Forward 優化器（日內版本）"""
    
    def __init__(self, data_file: str):
        print("="*70)
        print("🚀 Walk-Forward 回測（真實大單數據）")
        print("="*70)
        print()
        
        # 載入數據
        print("📂 載入數據...")
        self.df = pd.read_parquet(data_file)
        self.df['timestamp'] = pd.to_datetime(self.df['timestamp'])
        
        # 只取有大單的時間範圍
        df_with_trades = self.df[self.df['large_trade_count'] > 0]
        if len(df_with_trades) == 0:
            raise ValueError("數據中沒有大單記錄！")
        
        self.start_date = df_with_trades['timestamp'].min()
        self.end_date = df_with_trades['timestamp'].max()
        
        print(f"✅ 數據載入完成")
        print(f"   總K線: {len(self.df):,} 根")
        print(f"   有大單K線: {len(df_with_trades):,} 根")
        print(f"   時間範圍: {self.start_date} ~ {self.end_date}")
        print()
    
    def run_daily_walk_forward(self):
        """運行日內 Walk-Forward 測試"""
        # 獲取所有日期
        df_range = self.df[
            (self.df['timestamp'] >= self.start_date) & 
            (self.df['timestamp'] <= self.end_date)
        ]
        dates = pd.to_datetime(df_range['timestamp'].dt.date).unique()
        dates = sorted(dates)
        
        print(f"📅 測試天數: {len(dates)} 天")
        print(f"   {dates[0].date()} ~ {dates[-1].date()}")
        print()
        
        # 使用固定配置（因為只有7天數據，不做優化）
        config = StrategyConfig(
            imbalance_threshold=0.3,
            min_trade_count=3,
            tp_pct=0.0015,
            sl_pct=0.0010,
            time_stop_minutes=180,
            leverage=15,
            min_confidence=0.5
        )
        
        print("📊 策略配置:")
        print(f"   大單不平衡閾值: ±{config.imbalance_threshold}")
        print(f"   最少大單數: {config.min_trade_count}")
        print(f"   TP/SL: {config.tp_pct*100:.2f}% / {config.sl_pct*100:.2f}%")
        print(f"   時間止損: {config.time_stop_minutes} 分鐘")
        print(f"   槓桿: {config.leverage}x")
        print(f"   最低信心度: {config.min_confidence}")
        print()
        
        # 回測引擎
        backtester = RealLargeTradeBacktester(config)
        
        # 存儲所有交易
        all_trades = []
        daily_results = []
        
        print("="*70)
        print("🔄 開始逐日回測...")
        print("="*70)
        print()
        
        for i, date in enumerate(dates):
            date_str = date.strftime('%Y-%m-%d')
            next_date = date + timedelta(days=1)
            next_date_str = next_date.strftime('%Y-%m-%d')
            
            print(f"Day {i+1}/{len(dates)}: {date_str}")
            
            # 運行回測
            trades = backtester.run_backtest(self.df, date_str, next_date_str)
            
            if len(trades) > 0:
                wins = sum(1 for t in trades if t.pnl_with_leverage > 0)
                losses = len(trades) - wins
                win_rate = wins / len(trades) * 100
                total_return = sum(t.pnl_with_leverage for t in trades)
                
                print(f"   交易數: {len(trades)}")
                print(f"   勝率: {win_rate:.1f}% ({wins}勝/{losses}敗)")
                print(f"   回報: {total_return:+.2f}%")
                
                daily_results.append({
                    'date': date_str,
                    'trades': len(trades),
                    'wins': wins,
                    'losses': losses,
                    'win_rate': win_rate,
                    'return': total_return
                })
            else:
                print(f"   交易數: 0")
                daily_results.append({
                    'date': date_str,
                    'trades': 0,
                    'wins': 0,
                    'losses': 0,
                    'win_rate': 0.0,
                    'return': 0.0
                })
            
            all_trades.extend(trades)
            print()
        
        # 總結
        self.print_summary(all_trades, daily_results, config)
        
        # 保存結果
        self.save_results(all_trades, daily_results, config)
        
        return all_trades, daily_results
    
    def print_summary(self, trades: List[TradeResult], daily_results: List[Dict], config: StrategyConfig):
        """打印回測總結"""
        print("="*70)
        print("📊 Walk-Forward 回測總結")
        print("="*70)
        print()
        
        if len(trades) == 0:
            print("⚠️ 無交易記錄")
            return
        
        # 基本統計
        total_trades = len(trades)
        wins = sum(1 for t in trades if t.pnl_with_leverage > 0)
        losses = total_trades - wins
        win_rate = wins / total_trades * 100
        
        print(f"總交易數: {total_trades}")
        print(f"勝率: {win_rate:.2f}% ({wins}勝/{losses}敗)")
        print()
        
        # 回報統計
        total_return = sum(t.pnl_with_leverage for t in trades)
        avg_win = np.mean([t.pnl_with_leverage for t in trades if t.pnl_with_leverage > 0]) if wins > 0 else 0
        avg_loss = np.mean([t.pnl_with_leverage for t in trades if t.pnl_with_leverage < 0]) if losses > 0 else 0
        
        print(f"總回報: {total_return:+.2f}%")
        print(f"平均盈利: {avg_win:+.2f}%")
        print(f"平均虧損: {avg_loss:+.2f}%")
        print(f"盈虧比: {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "N/A")
        print()
        
        # 出場原因
        exit_reasons = {}
        for t in trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
        
        print("出場原因:")
        for reason, count in exit_reasons.items():
            print(f"  {reason}: {count} ({count/total_trades*100:.1f}%)")
        print()
        
        # 交易頻率
        test_days = len(daily_results)
        trades_per_day = total_trades / test_days
        
        print(f"交易頻率: {trades_per_day:.1f} 筆/天")
        print()
        
        # 評估
        print("🎯 目標達成情況:")
        print(f"   目標頻率: 10+ 筆/天")
        print(f"   實際頻率: {trades_per_day:.1f} 筆/天 {'✅' if trades_per_day >= 10 else '❌'}")
        print(f"   目標勝率: 60%+")
        print(f"   實際勝率: {win_rate:.1f}% {'✅' if win_rate >= 60 else '❌'}")
        print()
    
    def save_results(self, trades: List[TradeResult], daily_results: List[Dict], config: StrategyConfig):
        """保存回測結果"""
        output_file = 'backtest_results/walk_forward_real_large_trades.json'
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        
        # 轉換為可序列化格式
        trades_data = [
            {
                'entry_time': t.entry_time.isoformat(),
                'entry_price': t.entry_price,
                'exit_time': t.exit_time.isoformat(),
                'exit_price': t.exit_price,
                'side': t.side,
                'pnl_pct': t.pnl_pct,
                'pnl_with_leverage': t.pnl_with_leverage,
                'exit_reason': t.exit_reason,
                'confidence': t.confidence,
                'large_trade_count': t.large_trade_count,
                'large_trade_imbalance': t.large_trade_imbalance
            }
            for t in trades
        ]
        
        result = {
            'config': {
                'imbalance_threshold': config.imbalance_threshold,
                'min_trade_count': config.min_trade_count,
                'tp_pct': config.tp_pct,
                'sl_pct': config.sl_pct,
                'time_stop_minutes': config.time_stop_minutes,
                'leverage': config.leverage,
                'min_confidence': config.min_confidence
            },
            'daily_results': daily_results,
            'trades': trades_data,
            'summary': {
                'total_trades': len(trades),
                'wins': sum(1 for t in trades if t.pnl_with_leverage > 0),
                'losses': sum(1 for t in trades if t.pnl_with_leverage < 0),
                'win_rate': sum(1 for t in trades if t.pnl_with_leverage > 0) / len(trades) * 100 if len(trades) > 0 else 0,
                'total_return': sum(t.pnl_with_leverage for t in trades),
                'trades_per_day': len(trades) / len(daily_results) if len(daily_results) > 0 else 0
            }
        }
        
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        print(f"💾 結果已保存: {output_file}")
        print()


def main():
    """主函數"""
    data_file = 'data/historical/BTCUSDT_15m_with_large_trades.parquet'
    
    optimizer = WalkForwardOptimizer(data_file)
    optimizer.run_daily_walk_forward()
    
    print("="*70)
    print("✅ Walk-Forward 回測完成！")
    print("="*70)


if __name__ == '__main__':
    main()
