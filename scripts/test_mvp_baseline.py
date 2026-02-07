"""
MVP 純技術策略 Baseline 測試
============================

使用相同的 Backtester 測試 MVP 策略（純技術指標，無 Funding Rate）

目標：
- 建立 Baseline control group
- 對比 Hybrid vs MVP
- 評估 Funding Rate 是否真的有貢獻

Author: Strategy Comparison
Date: 2025-11-16
"""

import sys
sys.path.insert(0, '/Users/akaihuangm1/Desktop/btn')

import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Dict
import json
from pathlib import Path
from dataclasses import dataclass, asdict

from src.strategy.mvp_strategy_v2 import MVPStrategyV2


@dataclass
class Trade:
    """交易記錄"""
    entry_time: datetime
    exit_time: datetime
    direction: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_usd: float
    reason: str
    confidence: float


class MVPBacktester:
    """MVP 策略回測器（與 Hybrid 使用相同風控）"""
    
    def __init__(
        self,
        initial_capital: float = 10000,
        leverage: int = 10,
        tp_pct: float = 0.015,  # 1.5% TP（現貨百分比）
        sl_pct: float = 0.010,  # 1.0% SL（現貨百分比）
        time_stop_hours: int = 12,
        taker_fee: float = 0.0004
    ):
        self.initial_capital = initial_capital
        self.leverage = leverage
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.time_stop_hours = time_stop_hours
        self.taker_fee = taker_fee
    
    def backtest(
        self,
        df: pd.DataFrame,
        strategy: MVPStrategyV2
    ) -> Dict:
        """
        回測策略
        
        Args:
            df: K線數據
            strategy: MVP 策略實例
            
        Returns:
            回測結果
        """
        print(f"      開始回測...")
        
        trades = []
        capital = self.initial_capital
        
        in_position = False
        position = None
        
        # 準備數據
        df = df.copy()
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        for idx in range(len(df)):
            if idx % 5000 == 0 and idx > 0:
                print(f"      進度: {idx}/{len(df)} ({idx/len(df)*100:.1f}%)")
            
            row = df.iloc[idx]
            current_time = row['timestamp']
            current_price = row['close']
            
            # 檢查現有倉位
            if in_position:
                hours_held = (current_time - position['entry_time']).total_seconds() / 3600
                
                # 計算當前 PnL（現貨百分比）
                if position['direction'] == 'LONG':
                    pnl_pct_raw = (current_price - position['entry_price']) / position['entry_price']
                else:  # SHORT
                    pnl_pct_raw = (position['entry_price'] - current_price) / position['entry_price']
                
                # TP（用現貨百分比比較）
                if pnl_pct_raw >= self.tp_pct:
                    pnl_pct_with_leverage = pnl_pct_raw * self.leverage
                    pnl_usd = capital * pnl_pct_with_leverage
                    capital += pnl_usd
                    capital -= capital * self.taker_fee * 2
                    
                    trades.append(Trade(
                        entry_time=position['entry_time'],
                        exit_time=current_time,
                        direction=position['direction'],
                        entry_price=position['entry_price'],
                        exit_price=current_price,
                        pnl_pct=pnl_pct_with_leverage,
                        pnl_usd=pnl_usd,
                        reason="TP",
                        confidence=position['confidence']
                    ))
                    
                    in_position = False
                    position = None
                    continue
                
                # SL（用現貨百分比比較）
                elif pnl_pct_raw <= -self.sl_pct:
                    pnl_pct_with_leverage = pnl_pct_raw * self.leverage
                    pnl_usd = capital * pnl_pct_with_leverage
                    capital += pnl_usd
                    capital -= capital * self.taker_fee * 2
                    
                    trades.append(Trade(
                        entry_time=position['entry_time'],
                        exit_time=current_time,
                        direction=position['direction'],
                        entry_price=position['entry_price'],
                        exit_price=current_price,
                        pnl_pct=pnl_pct_with_leverage,
                        pnl_usd=pnl_usd,
                        reason="SL",
                        confidence=position['confidence']
                    ))
                    
                    in_position = False
                    position = None
                    continue
                
                # 時間止損
                elif hours_held >= self.time_stop_hours:
                    pnl_pct_with_leverage = pnl_pct_raw * self.leverage
                    pnl_usd = capital * pnl_pct_with_leverage
                    capital += pnl_usd
                    capital -= capital * self.taker_fee * 2
                    
                    trades.append(Trade(
                        entry_time=position['entry_time'],
                        exit_time=current_time,
                        direction=position['direction'],
                        entry_price=position['entry_price'],
                        exit_price=current_price,
                        pnl_pct=pnl_pct_with_leverage,
                        pnl_usd=pnl_usd,
                        reason="TIME_STOP",
                        confidence=position['confidence']
                    ))
                    
                    in_position = False
                    position = None
                    continue
            
            # 生成新信號（MVP 策略需要整個 DataFrame）
            if not in_position:
                # MVP 策略需要足夠的歷史數據來計算指標
                lookback = 200  # 需要足夠長的歷史數據
                if idx < lookback:
                    continue
                
                # 準備數據切片
                df_slice = df.iloc[max(0, idx-lookback):idx+1].copy()
                
                # 生成信號
                signal = strategy.generate_signal(df_slice, current_time)
                
                if signal and signal.direction:
                    # 開倉
                    in_position = True
                    position = {
                        'direction': signal.direction,
                        'entry_time': current_time,
                        'entry_price': current_price,
                        'confidence': signal.confidence
                    }
        
        # 計算統計
        win_trades = [t for t in trades if t.pnl_pct > 0]
        loss_trades = [t for t in trades if t.pnl_pct <= 0]
        
        total_trades = len(trades)
        win_rate = len(win_trades) / total_trades if total_trades > 0 else 0
        
        return_pct = ((capital - self.initial_capital) / self.initial_capital) * 100
        
        # 計算交易頻率
        if total_trades > 0:
            days = (df['timestamp'].max() - df['timestamp'].min()).days
            trades_per_day = total_trades / days if days > 0 else 0
        else:
            trades_per_day = 0
        
        return {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'return_pct': return_pct,
            'final_capital': capital,
            'trades_per_day': trades_per_day,
            'win_trades': len(win_trades),
            'loss_trades': len(loss_trades),
            'trades': [asdict(t) for t in trades]
        }


def main():
    print("="*70)
    print("🎯 MVP 純技術策略 Baseline 測試（2020-2025）")
    print("="*70)
    print()
    
    # 載入數據
    print("📂 載入數據...")
    df = pd.read_parquet('data/historical/BTCUSDT_15m_with_l0.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)
    
    print(f"✅ 數據載入完成: {len(df)} 根 K 線")
    print(f"   時間範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    print()
    
    # MVP 策略配置（簡化版 - 關閉所有過濾器）
    strategy_config = {
        'ma_short': 7,
        'ma_long': 25,
        'rsi_period': 14,
        'long_rsi_lower': 30.0,  # 放寬 RSI 範圍（原 45）
        'long_rsi_upper': 70.0,  # 放寬 RSI 範圍（原 60）
        'short_rsi_lower': 30.0,  # 放寬 RSI 範圍（原 40）
        'short_rsi_upper': 70.0,  # 放寬 RSI 範圍（原 55）
        'volume_multiplier': 1.0,  # 降低成交量要求（原 1.2）
        'ma_distance_threshold': 0.1,  # 降低 MA 距離要求（原 0.3）
        'require_confirmation': False,  # 關閉連續確認
        'enable_consolidation_filter': False,  # 關閉盤整過濾
        'enable_timezone_filter': False,  # 關閉時區過濾
        'enable_cost_filter': False  # 關閉成本過濾
    }
    
    print("="*70)
    print(f"🔧 MVP 策略配置（純技術指標）")
    print("="*70)
    print(f"參數: {strategy_config}")
    print()
    
    strategy = MVPStrategyV2(**strategy_config)
    backtest_engine = MVPBacktester()
    
    all_results = {}
    
    # 測試每年
    for year in range(2020, 2026):
        df_year = df[df['timestamp'].dt.year == year].copy()
        
        if len(df_year) == 0:
            continue
        
        print(f"\n📊 {year} 年")
        print(f"   數據量: {len(df_year)} 根 K 線")
        
        result = backtest_engine.backtest(df_year, strategy)
        
        print(f"   交易數: {result['total_trades']} 筆")
        print(f"   勝率: {result['win_rate']:.1%}")
        print(f"   回報: {result['return_pct']:+.1f}%")
        print(f"   頻率: {result['trades_per_day']:.2f} 筆/天")
        
        all_results[year] = result
    
    # 計算平均表現
    all_trades = sum(r['total_trades'] for r in all_results.values())
    avg_win_rate = np.mean([r['win_rate'] for r in all_results.values() if r['total_trades'] > 0])
    avg_return = np.mean([r['return_pct'] for r in all_results.values()])
    avg_trades_per_day = np.mean([r['trades_per_day'] for r in all_results.values()])
    
    print(f"\n{'='*70}")
    print(f"📈 MVP 策略平均表現")
    print(f"{'='*70}")
    print(f"總交易數: {all_trades} 筆")
    print(f"平均勝率: {avg_win_rate:.1%}")
    print(f"平均回報: {avg_return:+.1f}%")
    print(f"平均頻率: {avg_trades_per_day:.2f} 筆/天")
    print()
    
    # 保存結果
    output_dir = Path('backtest_results/hybrid_strategy')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    with open(output_dir / 'mvp_baseline_results.json', 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    
    print("="*70)
    print("✅ MVP Baseline 測試完成！")
    print(f"結果已保存: {output_dir / 'mvp_baseline_results.json'}")
    print()
    print("📊 下一步：對比 Hybrid vs MVP")
    print("   查看 walk_forward_results.json（Hybrid）")
    print("   查看 mvp_baseline_results.json（MVP）")
    print("="*70)


if __name__ == "__main__":
    main()
