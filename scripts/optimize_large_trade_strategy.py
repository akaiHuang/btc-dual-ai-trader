#!/usr/bin/env python3
"""
參數優化：Grid Search 找出最優配置
目標：降低交易頻率，提高單筆回報，優化手續費後淨收益
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Tuple
import json
from pathlib import Path
from itertools import product


@dataclass
class OptimizationConfig:
    """優化配置"""
    # 測試參數範圍
    imbalance_thresholds: List[float] = None  # [0.2, 0.3, 0.4, 0.5]
    min_confidence_levels: List[float] = None  # [0.5, 0.6, 0.7]
    min_trade_counts: List[int] = None  # [3, 5, 7]
    tp_pcts: List[float] = None  # [0.0015, 0.0020, 0.0025]
    leverages: List[int] = None  # [10, 15, 20]
    
    # 固定參數
    sl_pct: float = 0.0010
    time_stop_minutes: int = 180
    fee_per_trade: float = 0.0004  # 0.04% 手續費（開倉+平倉）
    
    def __post_init__(self):
        if self.imbalance_thresholds is None:
            self.imbalance_thresholds = [0.2, 0.3, 0.4, 0.5]
        if self.min_confidence_levels is None:
            self.min_confidence_levels = [0.5, 0.6, 0.7]
        if self.min_trade_counts is None:
            self.min_trade_counts = [3, 5, 7]
        if self.tp_pcts is None:
            self.tp_pcts = [0.0015, 0.0020, 0.0025]
        if self.leverages is None:
            self.leverages = [10, 15, 20]


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


@dataclass
class BacktestResult:
    """回測結果"""
    config: Dict
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_return: float
    total_return_after_fee: float
    avg_return_per_trade: float
    trades_per_day: float
    score: float  # 綜合評分


class GridSearchOptimizer:
    """網格搜索優化器"""
    
    def __init__(self, data_file: str, opt_config: OptimizationConfig):
        print("="*70)
        print("🔍 參數優化：Grid Search")
        print("="*70)
        print()
        
        self.opt_config = opt_config
        
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
        print(f"   有大單K線: {len(df_with_trades):,} 根")
        print(f"   時間範圍: {self.start_date} ~ {self.end_date}")
        print()
        
    def generate_signal(self, df: pd.DataFrame, idx: int, 
                       imbalance_threshold: float, 
                       min_trade_count: int) -> Tuple[str, float]:
        """生成交易信號"""
        row = df.iloc[idx]
        
        # 檢查數據完整性
        if pd.isna(row['rsi']) or pd.isna(row['ma7']) or pd.isna(row['ma25']):
            return 'NEUTRAL', 0.0
        
        # 檢查大單數量
        if row['large_trade_count'] < min_trade_count:
            return 'NEUTRAL', 0.0
        
        signal = 'NEUTRAL'
        confidence = 0.0
        
        # === 大單信號（權重 50%）===
        large_trade_score = 0.0
        imbalance = row['large_trade_imbalance']
        
        if imbalance > imbalance_threshold:
            large_trade_score = 0.5
        elif imbalance < -imbalance_threshold:
            large_trade_score = -0.5
        
        # 巨鯨加成
        if row['whale_detected']:
            large_trade_score *= 1.2
        
        # === RSI信號（權重 25%）===
        rsi_score = 0.0
        rsi = row['rsi']
        
        if rsi < 30:
            rsi_score = 0.25
        elif rsi > 70:
            rsi_score = -0.25
        
        # === MA趨勢信號（權重 25%）===
        ma_score = 0.0
        if row['ma7'] > row['ma25']:
            ma_score = 0.25
        elif row['ma7'] < row['ma25']:
            ma_score = -0.25
        
        # === 綜合評分 ===
        total_score = large_trade_score + rsi_score + ma_score
        
        if total_score > 0.5:
            signal = 'LONG'
            confidence = min(abs(total_score), 1.0)
        elif total_score < -0.5:
            signal = 'SHORT'
            confidence = min(abs(total_score), 1.0)
        
        return signal, confidence
    
    def run_backtest(self, imbalance_threshold: float, min_confidence: float,
                    min_trade_count: int, tp_pct: float, leverage: int) -> List[TradeResult]:
        """運行單次回測"""
        df = self.df[
            (self.df['timestamp'] >= self.start_date) & 
            (self.df['timestamp'] <= self.end_date)
        ].copy()
        
        trades = []
        position = None
        
        for i in range(len(df)):
            current_time = df.iloc[i]['timestamp']
            current_price = df.iloc[i]['close']
            
            # 檢查平倉
            if position is not None:
                exit_signal = None
                exit_reason = None
                
                if position['side'] == 'LONG':
                    pnl_pct = (current_price - position['entry_price']) / position['entry_price']
                else:
                    pnl_pct = (position['entry_price'] - current_price) / position['entry_price']
                
                # 止盈
                if pnl_pct >= tp_pct:
                    exit_signal = True
                    exit_reason = 'TP'
                # 止損
                elif pnl_pct <= -self.opt_config.sl_pct:
                    exit_signal = True
                    exit_reason = 'SL'
                # 時間止損
                elif (current_time - position['entry_time']).total_seconds() / 60 >= self.opt_config.time_stop_minutes:
                    exit_signal = True
                    exit_reason = 'TIME_STOP'
                
                if exit_signal:
                    pnl_with_leverage = pnl_pct * leverage
                    
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
                signal, confidence = self.generate_signal(
                    df, i, imbalance_threshold, min_trade_count
                )
                
                if signal != 'NEUTRAL' and confidence >= min_confidence:
                    position = {
                        'side': signal,
                        'entry_time': current_time,
                        'entry_price': current_price,
                        'confidence': confidence
                    }
        
        # 強制平倉
        if position is not None:
            last_price = df.iloc[-1]['close']
            last_time = df.iloc[-1]['timestamp']
            
            if position['side'] == 'LONG':
                pnl_pct = (last_price - position['entry_price']) / position['entry_price']
            else:
                pnl_pct = (position['entry_price'] - last_price) / position['entry_price']
            
            pnl_with_leverage = pnl_pct * leverage
            
            trade = TradeResult(
                entry_time=position['entry_time'],
                entry_price=position['entry_price'],
                exit_time=last_time,
                exit_price=last_price,
                side=position['side'],
                pnl_pct=pnl_pct,
                pnl_with_leverage=pnl_with_leverage,
                exit_reason='END_OF_TEST',
                confidence=position['confidence']
            )
            trades.append(trade)
        
        return trades
    
    def calculate_score(self, result: BacktestResult) -> float:
        """
        計算綜合評分
        
        評分標準:
        - 扣費後回報（主要，權重50%）
        - 勝率（權重30%）
        - 交易頻率適中（權重20%，5-15筆/天最佳）
        """
        # 1. 扣費後回報評分（0-100）
        return_score = max(0, min(100, result.total_return_after_fee * 10))
        
        # 2. 勝率評分（0-100）
        win_rate_score = result.win_rate
        
        # 3. 交易頻率評分（0-100）
        # 最佳頻率：5-15筆/天
        if 5 <= result.trades_per_day <= 15:
            freq_score = 100
        elif result.trades_per_day < 5:
            freq_score = result.trades_per_day / 5 * 100
        else:  # >15
            freq_score = max(0, 100 - (result.trades_per_day - 15) * 5)
        
        # 綜合評分
        total_score = (
            return_score * 0.5 +
            win_rate_score * 0.3 +
            freq_score * 0.2
        )
        
        return total_score
    
    def run_grid_search(self):
        """運行網格搜索"""
        print("🔍 開始網格搜索...")
        print()
        
        # 生成所有參數組合
        param_combinations = list(product(
            self.opt_config.imbalance_thresholds,
            self.opt_config.min_confidence_levels,
            self.opt_config.min_trade_counts,
            self.opt_config.tp_pcts,
            self.opt_config.leverages
        ))
        
        total_combinations = len(param_combinations)
        print(f"總組合數: {total_combinations}")
        print(f"預計時間: {total_combinations * 2} 秒")
        print()
        
        results = []
        test_days = (self.end_date - self.start_date).days + 1
        
        for idx, (imbalance_th, min_conf, min_trades, tp, lev) in enumerate(param_combinations, 1):
            # 運行回測
            trades = self.run_backtest(imbalance_th, min_conf, min_trades, tp, lev)
            
            if len(trades) == 0:
                continue
            
            # 計算指標
            wins = sum(1 for t in trades if t.pnl_with_leverage > 0)
            losses = len(trades) - wins
            win_rate = wins / len(trades) * 100
            total_return = sum(t.pnl_with_leverage for t in trades)
            
            # 計算手續費後回報
            total_fee = len(trades) * self.opt_config.fee_per_trade * lev
            total_return_after_fee = total_return - total_fee
            
            trades_per_day = len(trades) / test_days
            avg_return = total_return / len(trades) if len(trades) > 0 else 0
            
            result = BacktestResult(
                config={
                    'imbalance_threshold': imbalance_th,
                    'min_confidence': min_conf,
                    'min_trade_count': min_trades,
                    'tp_pct': tp,
                    'leverage': lev
                },
                total_trades=len(trades),
                wins=wins,
                losses=losses,
                win_rate=win_rate,
                total_return=total_return,
                total_return_after_fee=total_return_after_fee,
                avg_return_per_trade=avg_return,
                trades_per_day=trades_per_day,
                score=0.0  # 稍後計算
            )
            
            # 計算評分
            result.score = self.calculate_score(result)
            
            results.append(result)
            
            # 進度顯示
            if idx % 10 == 0 or idx == total_combinations:
                print(f"進度: {idx}/{total_combinations} ({idx/total_combinations*100:.1f}%)")
        
        print()
        print(f"✅ 網格搜索完成！有效組合: {len(results)}")
        print()
        
        return results
    
    def print_top_results(self, results: List[BacktestResult], top_n: int = 10):
        """打印最佳結果"""
        # 按評分排序
        sorted_results = sorted(results, key=lambda x: x.score, reverse=True)
        
        print("="*70)
        print(f"🏆 Top {top_n} 最佳配置")
        print("="*70)
        print()
        
        for i, result in enumerate(sorted_results[:top_n], 1):
            print(f"Rank {i}:")
            print(f"  評分: {result.score:.2f}")
            print(f"  配置:")
            for key, value in result.config.items():
                print(f"    {key}: {value}")
            print(f"  表現:")
            print(f"    交易數: {result.total_trades}")
            print(f"    勝率: {result.win_rate:.1f}%")
            print(f"    頻率: {result.trades_per_day:.1f} 筆/天")
            print(f"    回報（扣費前）: {result.total_return:+.2f}%")
            print(f"    回報（扣費後）: {result.total_return_after_fee:+.2f}%")
            print(f"    平均每筆: {result.avg_return_per_trade:+.3f}%")
            print()
    
    def save_results(self, results: List[BacktestResult]):
        """保存結果"""
        output_file = 'backtest_results/grid_search_optimization.json'
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        
        # 轉換為可序列化格式
        results_data = []
        for r in results:
            results_data.append({
                'config': r.config,
                'total_trades': r.total_trades,
                'wins': r.wins,
                'losses': r.losses,
                'win_rate': r.win_rate,
                'total_return': r.total_return,
                'total_return_after_fee': r.total_return_after_fee,
                'avg_return_per_trade': r.avg_return_per_trade,
                'trades_per_day': r.trades_per_day,
                'score': r.score
            })
        
        # 按評分排序
        results_data = sorted(results_data, key=lambda x: x['score'], reverse=True)
        
        with open(output_file, 'w') as f:
            json.dump(results_data, f, indent=2)
        
        print(f"💾 結果已保存: {output_file}")
        print()


def main():
    """主函數"""
    data_file = 'data/historical/BTCUSDT_15m_with_large_trades.parquet'
    
    # 配置優化參數
    opt_config = OptimizationConfig(
        imbalance_thresholds=[0.2, 0.3, 0.4, 0.5],
        min_confidence_levels=[0.5, 0.6, 0.7],
        min_trade_counts=[3, 5, 7],
        tp_pcts=[0.0015, 0.0020, 0.0025],
        leverages=[10, 15, 20]
    )
    
    print(f"參數空間:")
    print(f"  imbalance_threshold: {opt_config.imbalance_thresholds}")
    print(f"  min_confidence: {opt_config.min_confidence_levels}")
    print(f"  min_trade_count: {opt_config.min_trade_counts}")
    print(f"  tp_pct: {opt_config.tp_pcts}")
    print(f"  leverage: {opt_config.leverages}")
    print(f"  總組合: {len(opt_config.imbalance_thresholds) * len(opt_config.min_confidence_levels) * len(opt_config.min_trade_counts) * len(opt_config.tp_pcts) * len(opt_config.leverages)}")
    print()
    
    # 運行優化
    optimizer = GridSearchOptimizer(data_file, opt_config)
    results = optimizer.run_grid_search()
    
    # 打印最佳結果
    optimizer.print_top_results(results, top_n=10)
    
    # 保存結果
    optimizer.save_results(results)
    
    print("="*70)
    print("✅ 參數優化完成！")
    print("="*70)


if __name__ == '__main__':
    main()
