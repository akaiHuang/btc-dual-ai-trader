#!/usr/bin/env python3
"""
MFE/MAE 分析工具
Maximum Favorable Excursion (最大浮盈)
Maximum Adverse Excursion (最大浮虧)

用於分析交易中的價格波動,找出最優 TP/SL 設置
"""

import json
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple


class MFEMAEAnalyzer:
    """MFE/MAE 分析器"""
    
    def __init__(self, data_file: str):
        """
        Args:
            data_file: 回測結果 JSON 檔案路徑
        """
        self.data_file = Path(data_file)
        self.trades = []
        self.df_1m = None
        
    def load_trades(self) -> None:
        """載入交易記錄"""
        with open(self.data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.trades = data.get('trades', [])
        print(f"✅ 載入 {len(self.trades)} 筆交易記錄")
    
    def load_price_data(self, data_path: str) -> None:
        """
        載入 1 分鐘 K 線數據用於計算 MFE/MAE
        
        Args:
            data_path: BTC 1分鐘數據路徑 (支援 .csv 或 .parquet)
        """
        print(f"📊 載入價格數據: {data_path}")
        
        if data_path.endswith('.parquet'):
            self.df_1m = pd.read_parquet(data_path)
        else:
            self.df_1m = pd.read_csv(data_path)
        
        self.df_1m['timestamp'] = pd.to_datetime(self.df_1m['timestamp'])
        self.df_1m.set_index('timestamp', inplace=True)
        print(f"✅ 載入 {len(self.df_1m)} 根 1m K線")
    
    def calculate_mfe_mae(self) -> pd.DataFrame:
        """
        計算每筆交易的 MFE 和 MAE
        
        Returns:
            DataFrame with columns: entry_time, exit_time, direction, entry_price, 
                                   exit_price, pnl_pct, exit_reason, 
                                   mfe_pct, mae_pct, mfe_time, mae_time
        """
        results = []
        
        for trade in self.trades:
            entry_time = pd.to_datetime(trade['entry_time'])
            exit_time = pd.to_datetime(trade['exit_time'])
            entry_price = trade['entry_price']
            direction = trade['direction']
            
            # 獲取交易期間的價格數據
            mask = (self.df_1m.index >= entry_time) & (self.df_1m.index <= exit_time)
            period_data = self.df_1m.loc[mask]
            
            if len(period_data) == 0:
                continue
            
            # 計算 MFE (Maximum Favorable Excursion)
            if direction == 'LONG':
                # 多單: 最高點 - 入場價
                mfe_price = period_data['high'].max()
                mfe_pct = ((mfe_price - entry_price) / entry_price) * 100
                mfe_time = period_data['high'].idxmax()
                
                # 多單: 入場價 - 最低點
                mae_price = period_data['low'].min()
                mae_pct = ((entry_price - mae_price) / entry_price) * 100
                mae_time = period_data['low'].idxmin()
            else:  # SHORT
                # 空單: 入場價 - 最低點
                mfe_price = period_data['low'].min()
                mfe_pct = ((entry_price - mfe_price) / entry_price) * 100
                mfe_time = period_data['low'].idxmin()
                
                # 空單: 最高點 - 入場價
                mae_price = period_data['high'].max()
                mae_pct = ((mae_price - entry_price) / entry_price) * 100
                mae_time = period_data['high'].idxmax()
            
            # 計算實際 PnL 百分比
            if direction == 'LONG':
                pnl_pct = ((trade['exit_price'] - entry_price) / entry_price) * 100
            else:  # SHORT
                pnl_pct = ((entry_price - trade['exit_price']) / entry_price) * 100
            
            results.append({
                'entry_time': entry_time,
                'exit_time': exit_time,
                'direction': direction,
                'entry_price': entry_price,
                'exit_price': trade['exit_price'],
                'pnl_net': trade['pnl_net'],
                'pnl_pct': pnl_pct,
                'exit_reason': trade['exit_reason'],
                'mfe_pct': mfe_pct,
                'mae_pct': mae_pct,
                'mfe_time': mfe_time,
                'mae_time': mae_time,
                'duration_minutes': (exit_time - entry_time).total_seconds() / 60
            })
        
        return pd.DataFrame(results)
    
    def analyze_distribution(self, df: pd.DataFrame) -> Dict:
        """
        分析 MFE/MAE 分佈,找出最優 TP/SL
        
        Returns:
            Dict with analysis results
        """
        # 基本統計
        stats = {
            'total_trades': len(df),
            'winning_trades': len(df[df['pnl_pct'] > 0]),
            'losing_trades': len(df[df['pnl_pct'] <= 0]),
            'win_rate': len(df[df['pnl_pct'] > 0]) / len(df) * 100,
            
            # MFE 統計
            'mfe_mean': df['mfe_pct'].mean(),
            'mfe_median': df['mfe_pct'].median(),
            'mfe_std': df['mfe_pct'].std(),
            'mfe_percentiles': {
                '25%': df['mfe_pct'].quantile(0.25),
                '50%': df['mfe_pct'].quantile(0.50),
                '75%': df['mfe_pct'].quantile(0.75),
                '90%': df['mfe_pct'].quantile(0.90),
            },
            
            # MAE 統計
            'mae_mean': df['mae_pct'].mean(),
            'mae_median': df['mae_pct'].median(),
            'mae_std': df['mae_pct'].std(),
            'mae_percentiles': {
                '25%': df['mae_pct'].quantile(0.25),
                '50%': df['mae_pct'].quantile(0.50),
                '75%': df['mae_pct'].quantile(0.75),
                '90%': df['mae_pct'].quantile(0.90),
            },
            
            # 時間統計
            'duration_mean': df['duration_minutes'].mean(),
            'duration_median': df['duration_minutes'].median(),
        }
        
        # 勝率 vs TP 關係
        tp_levels = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        tp_hit_rates = {}
        for tp in tp_levels:
            hit_count = len(df[df['mfe_pct'] >= tp])
            tp_hit_rates[f'{tp}%'] = {
                'hit_count': hit_count,
                'hit_rate': (hit_count / len(df)) * 100
            }
        stats['tp_hit_rates'] = tp_hit_rates
        
        # 勝率 vs SL 關係
        sl_levels = [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5]
        sl_hit_rates = {}
        for sl in sl_levels:
            hit_count = len(df[df['mae_pct'] >= sl])
            sl_hit_rates[f'{sl}%'] = {
                'hit_count': hit_count,
                'hit_rate': (hit_count / len(df)) * 100
            }
        stats['sl_hit_rates'] = sl_hit_rates
        
        # 贏家 vs 輸家的 MFE/MAE 對比
        winners = df[df['pnl_pct'] > 0]
        losers = df[df['pnl_pct'] <= 0]
        
        if len(winners) > 0:
            stats['winners'] = {
                'count': len(winners),
                'mfe_mean': winners['mfe_pct'].mean(),
                'mae_mean': winners['mae_pct'].mean(),
                'duration_mean': winners['duration_minutes'].mean(),
            }
        
        if len(losers) > 0:
            stats['losers'] = {
                'count': len(losers),
                'mfe_mean': losers['mfe_pct'].mean(),
                'mae_mean': losers['mae_pct'].mean(),
                'duration_mean': losers['duration_minutes'].mean(),
            }
        
        return stats
    
    def recommend_tp_sl(self, df: pd.DataFrame) -> Dict:
        """
        基於 MFE/MAE 分析推薦最優 TP/SL
        
        策略:
        1. TP: 選擇能捕捉 60-70% MFE 的水平
        2. SL: 選擇能避開 75-80% MAE 的水平
        3. 確保 R:R (Reward:Risk) >= 1.5:1
        """
        # TP 推薦: 取 MFE 的 60-70 百分位
        tp_60 = df['mfe_pct'].quantile(0.60)
        tp_70 = df['mfe_pct'].quantile(0.70)
        recommended_tp = (tp_60 + tp_70) / 2
        
        # SL 推薦: 取 MAE 的 75-80 百分位
        sl_75 = df['mae_pct'].quantile(0.75)
        sl_80 = df['mae_pct'].quantile(0.80)
        recommended_sl = (sl_75 + sl_80) / 2
        
        # 計算 Reward:Risk ratio
        rr_ratio = recommended_tp / recommended_sl if recommended_sl > 0 else 0
        
        # 計算預期命中率
        tp_hit_rate = (len(df[df['mfe_pct'] >= recommended_tp]) / len(df)) * 100
        sl_hit_rate = (len(df[df['mae_pct'] >= recommended_sl]) / len(df)) * 100
        
        # ATR 倍數推薦 (假設當前 ATR ~0.3%)
        assumed_atr_pct = 0.3
        atr_tp_multiplier = recommended_tp / assumed_atr_pct
        atr_sl_multiplier = recommended_sl / assumed_atr_pct
        
        return {
            'recommended_tp_pct': round(recommended_tp, 3),
            'recommended_sl_pct': round(recommended_sl, 3),
            'reward_risk_ratio': round(rr_ratio, 2),
            'tp_hit_rate': round(tp_hit_rate, 1),
            'sl_hit_rate': round(sl_hit_rate, 1),
            'atr_tp_multiplier': round(atr_tp_multiplier, 2),
            'atr_sl_multiplier': round(atr_sl_multiplier, 2),
            'confidence': 'HIGH' if len(df) >= 100 else 'MEDIUM' if len(df) >= 50 else 'LOW'
        }
    
    def generate_report(self, output_path: str = None) -> None:
        """生成完整分析報告"""
        print("\n" + "="*80)
        print("📊 MFE/MAE 分析報告")
        print("="*80)
        
        # 計算 MFE/MAE
        df = self.calculate_mfe_mae()
        
        if len(df) == 0:
            print("❌ 沒有足夠的交易數據")
            return
        
        # 分析分佈
        stats = self.analyze_distribution(df)
        
        print(f"\n【基本統計】")
        print(f"總交易數: {stats['total_trades']}")
        print(f"贏家: {stats['winning_trades']} ({stats['win_rate']:.1f}%)")
        print(f"輸家: {stats['losing_trades']}")
        
        print(f"\n【MFE (最大浮盈) 分佈】")
        print(f"平均: {stats['mfe_mean']:.3f}%")
        print(f"中位數: {stats['mfe_median']:.3f}%")
        print(f"標準差: {stats['mfe_std']:.3f}%")
        print(f"25%: {stats['mfe_percentiles']['25%']:.3f}%")
        print(f"50%: {stats['mfe_percentiles']['50%']:.3f}%")
        print(f"75%: {stats['mfe_percentiles']['75%']:.3f}%")
        print(f"90%: {stats['mfe_percentiles']['90%']:.3f}%")
        
        print(f"\n【MAE (最大浮虧) 分佈】")
        print(f"平均: {stats['mae_mean']:.3f}%")
        print(f"中位數: {stats['mae_median']:.3f}%")
        print(f"標準差: {stats['mae_std']:.3f}%")
        print(f"25%: {stats['mae_percentiles']['25%']:.3f}%")
        print(f"50%: {stats['mae_percentiles']['50%']:.3f}%")
        print(f"75%: {stats['mae_percentiles']['75%']:.3f}%")
        print(f"90%: {stats['mae_percentiles']['90%']:.3f}%")
        
        print(f"\n【TP 命中率分析】")
        for tp_level, data in stats['tp_hit_rates'].items():
            print(f"TP >= {tp_level}: {data['hit_count']} 筆 ({data['hit_rate']:.1f}%)")
        
        print(f"\n【SL 命中率分析】")
        for sl_level, data in stats['sl_hit_rates'].items():
            print(f"MAE >= {sl_level}: {data['hit_count']} 筆 ({data['hit_rate']:.1f}%)")
        
        if 'winners' in stats:
            print(f"\n【贏家特徵】")
            print(f"數量: {stats['winners']['count']}")
            print(f"平均 MFE: {stats['winners']['mfe_mean']:.3f}%")
            print(f"平均 MAE: {stats['winners']['mae_mean']:.3f}%")
            print(f"平均持倉時間: {stats['winners']['duration_mean']:.1f} 分鐘")
        
        if 'losers' in stats:
            print(f"\n【輸家特徵】")
            print(f"數量: {stats['losers']['count']}")
            print(f"平均 MFE: {stats['losers']['mfe_mean']:.3f}%")
            print(f"平均 MAE: {stats['losers']['mae_mean']:.3f}%")
            print(f"平均持倉時間: {stats['losers']['duration_mean']:.1f} 分鐘")
        
        # 生成推薦
        recommendation = self.recommend_tp_sl(df)
        
        print(f"\n{'='*80}")
        print("🎯 TP/SL 推薦參數")
        print("="*80)
        print(f"\n【推薦值】")
        print(f"止盈 (TP): {recommendation['recommended_tp_pct']}%")
        print(f"止損 (SL): {recommendation['recommended_sl_pct']}%")
        print(f"R:R 比例: {recommendation['reward_risk_ratio']}:1")
        
        print(f"\n【命中率預期】")
        print(f"TP 命中率: {recommendation['tp_hit_rate']}%")
        print(f"SL 命中率: {recommendation['sl_hit_rate']}%")
        
        print(f"\n【ATR 倍數建議】")
        print(f"atr_tp_multiplier: {recommendation['atr_tp_multiplier']}")
        print(f"atr_sl_multiplier: {recommendation['atr_sl_multiplier']}")
        
        print(f"\n信心等級: {recommendation['confidence']}")
        print(f"(基於 {len(df)} 筆交易樣本)")
        
        # 保存詳細數據
        if output_path:
            output_file = Path(output_path)
            
            # 保存交易明細
            df.to_csv(output_file.with_suffix('.csv'), index=False)
            print(f"\n✅ 交易明細已保存: {output_file.with_suffix('.csv')}")
            
            # 保存統計報告
            report_data = {
                'stats': stats,
                'recommendation': recommendation
            }
            with open(output_file.with_suffix('.json'), 'w', encoding='utf-8') as f:
                json.dump(report_data, f, indent=2, ensure_ascii=False, default=str)
            print(f"✅ 統計報告已保存: {output_file.with_suffix('.json')}")
        
        print("\n" + "="*80)


def main():
    """主程式"""
    import sys
    
    if len(sys.argv) < 3:
        print("使用方法:")
        print(f"  {sys.argv[0]} <backtest_json> <btc_1m_data> [output_path]")
        print("\n範例:")
        print(f"  {sys.argv[0]} backtest_results/walk_forward/test_2025_v2.0.json data/historical/BTCUSDT_1m_2025.parquet data/mfe_mae_analysis")
        sys.exit(1)
    
    backtest_json = sys.argv[1]
    btc_1m_data = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else None
    
    analyzer = MFEMAEAnalyzer(backtest_json)
    analyzer.load_trades()
    analyzer.load_price_data(btc_1m_data)
    analyzer.generate_report(output_path)


if __name__ == '__main__':
    main()
