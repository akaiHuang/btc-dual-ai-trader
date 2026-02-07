"""
混合策略 Walk-Forward 測試
===========================

測試混合策略（Funding + 技術指標）在 2020-2025 的表現

目標：
- 交易頻率：5-10 筆/天
- 勝率：60-70%
- 各年份穩定盈利
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

from src.strategy.hybrid_funding_technical import (
    HybridFundingTechnicalStrategy,
    SignalType
)


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


class HybridBacktester:
    """混合策略回測器"""
    
    def __init__(
        self,
        initial_capital: float = 10000,
        leverage: int = 10,  # 降低槓桿從 20x 到 10x
        tp_pct: float = 0.015,  # 1.5% TP（現貨百分比）
        sl_pct: float = 0.010,  # 1.0% SL（現貨百分比）
        time_stop_hours: int = 12,  # 12小時時間止損
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
        strategy: HybridFundingTechnicalStrategy
    ) -> Dict:
        """
        回測策略
        
        Args:
            df: K線數據（必須包含 fundingRate）
            strategy: 策略實例
            
        Returns:
            回測結果
        """
        print(f"      開始回測...")
        
        trades = []
        capital = self.initial_capital
        
        in_position = False
        position = None
        
        # 預先計算所有技術指標（避免重複計算）
        df_with_indicators = strategy.calculate_indicators(df.copy())
        
        for idx in range(len(df_with_indicators)):
            if idx % 5000 == 0 and idx > 0:
                print(f"      進度: {idx}/{len(df_with_indicators)} ({idx/len(df_with_indicators)*100:.1f}%)")
            
            row = df_with_indicators.iloc[idx]
            current_time = row['timestamp']
            current_price = row['close']
            
            # 檢查現有倉位
            if in_position:
                # 檢查出場條件
                hours_held = (current_time - position['entry_time']).total_seconds() / 3600
                
                # 計算當前 PnL（現貨百分比，不使用槓桿）
                if position['direction'] == 'LONG':
                    pnl_pct_raw = (current_price - position['entry_price']) / position['entry_price']
                else:  # SHORT
                    pnl_pct_raw = (position['entry_price'] - current_price) / position['entry_price']
                
                # TP（用現貨百分比比較）
                if pnl_pct_raw >= self.tp_pct:
                    # 實際 PnL 加上槓桿
                    pnl_pct_with_leverage = pnl_pct_raw * self.leverage
                    pnl_usd = capital * pnl_pct_with_leverage
                    capital += pnl_usd
                    
                    # 扣除手續費
                    capital -= capital * self.taker_fee * 2  # 進出場各一次
                    
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
                    # 實際 PnL 加上槓桿
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
            
            # 生成新信號
            if not in_position:
                # 直接使用已計算好的指標行
                signal = strategy._generate_signal_from_row(
                    df_with_indicators,
                    idx,
                    current_time
                )
                
                if signal.signal != SignalType.NEUTRAL:
                    # 開倉
                    in_position = True
                    position = {
                        'direction': signal.signal.value,
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
    print("🚀 混合策略 Walk-Forward 測試（2020-2025）")
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
    
    # 測試多組參數（更新為新的參數）
    param_configs = [
        {
            'name': '保守配置（高閾值）',
            'params': {
                'funding_zscore_threshold': 2.5,  # 改用 Z-score
                'funding_lookback_days': 90,
                'rsi_oversold': 25,
                'rsi_overbought': 75,
                'volume_spike_threshold': 2.5,
                'signal_score_threshold': 0.6,  # 改用分數閾值
                'require_funding_confirmation': False
            }
        },
        {
            'name': '平衡配置',
            'params': {
                'funding_zscore_threshold': 2.0,
                'funding_lookback_days': 90,
                'rsi_oversold': 30,
                'rsi_overbought': 70,
                'volume_spike_threshold': 2.0,
                'signal_score_threshold': 0.5,
                'require_funding_confirmation': False
            }
        },
        {
            'name': '激進配置（低閾值）',
            'params': {
                'funding_zscore_threshold': 1.5,
                'funding_lookback_days': 60,
                'rsi_oversold': 35,
                'rsi_overbought': 65,
                'volume_spike_threshold': 1.5,
                'signal_score_threshold': 0.4,
                'require_funding_confirmation': False
            }
        }
    ]
    
    all_results = {}
    
    for config in param_configs:
        config_name = config['name']
        params = config['params']
        
        print("="*70)
        print(f"🔧 測試配置：{config_name}")
        print("="*70)
        print(f"參數: {params}")
        print()
        
        strategy = HybridFundingTechnicalStrategy(**params)
        backtest_engine = HybridBacktester()
        
        year_results = {}
        
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
            
            year_results[year] = result
        
        all_results[config_name] = year_results
        
        # 計算平均表現
        all_trades = sum(r['total_trades'] for r in year_results.values())
        avg_win_rate = np.mean([r['win_rate'] for r in year_results.values() if r['total_trades'] > 0])
        avg_return = np.mean([r['return_pct'] for r in year_results.values()])
        avg_trades_per_day = np.mean([r['trades_per_day'] for r in year_results.values()])
        
        print(f"\n{'='*70}")
        print(f"📈 {config_name} 平均表現")
        print(f"{'='*70}")
        print(f"總交易數: {all_trades} 筆")
        print(f"平均勝率: {avg_win_rate:.1%}")
        print(f"平均回報: {avg_return:+.1f}%")
        print(f"平均頻率: {avg_trades_per_day:.2f} 筆/天")
        print()
    
    # 保存結果
    output_dir = Path('backtest_results/hybrid_strategy')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    with open(output_dir / 'walk_forward_results.json', 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    
    print("="*70)
    print("✅ 測試完成！")
    print(f"結果已保存: {output_dir / 'walk_forward_results.json'}")
    print("="*70)


if __name__ == "__main__":
    main()
