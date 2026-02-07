"""
MFE/MAE 分析工具 - Maximum Favorable/Adverse Excursion Analysis
分析每筆交易的最大浮盈和最大浮虧，找出最佳 TP/SL 設定點

作者: Walk-Forward v3.0 項目
日期: 2025-11-14
"""

import json
import os
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime


class MFEMAEAnalyzer:
    """
    MFE/MAE 分析器
    
    MFE (Maximum Favorable Excursion): 交易過程中的最大浮盈
    MAE (Maximum Adverse Excursion): 交易過程中的最大浮虧
    
    用途：
    1. 找出大多數盈利交易的 MFE 分佈 → 設定合理的 TP
    2. 找出大多數虧損交易的 MAE 分佈 → 設定合理的 SL
    3. 避免 TP 太貪心、SL 太寬鬆導致時間止損
    """
    
    def __init__(self, results_dir: str = 'backtest_results/walk_forward'):
        self.results_dir = results_dir
        self.all_trades = []
        self.winning_trades = []
        self.losing_trades = []
        
    def load_v21_trades(self) -> None:
        """載入 v2.1 所有年份的交易記錄"""
        years = [2021, 2022, 2023, 2024, 2025]
        
        print("📂 載入 v2.1 交易記錄...")
        for year in years:
            filepath = os.path.join(self.results_dir, f'test_{year}_v2.1.json')
            if not os.path.exists(filepath):
                print(f"⚠️ 找不到 {filepath}")
                continue
            
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            trades = data.get('trades', [])
            for trade in trades:
                trade['year'] = year
                self.all_trades.append(trade)
            
            print(f"  ✅ {year}: {len(trades)} 筆交易")
        
        # 分類勝敗交易
        self.winning_trades = [t for t in self.all_trades if t['pnl_net'] > 0]
        self.losing_trades = [t for t in self.all_trades if t['pnl_net'] <= 0]
        
        print(f"\n📊 總計: {len(self.all_trades)} 筆交易")
        print(f"  🟢 盈利: {len(self.winning_trades)} 筆 ({len(self.winning_trades)/len(self.all_trades)*100:.1f}%)")
        print(f"  🔴 虧損: {len(self.losing_trades)} 筆 ({len(self.losing_trades)/len(self.all_trades)*100:.1f}%)")
    
    def calculate_mfe_mae(self, data_dir: str = 'data') -> None:
        """
        計算每筆交易的 MFE 和 MAE
        
        注意: 由於沒有 tick 數據，這裡使用簡化計算：
        - 盈利交易: MFE = pnl_gross, MAE = 估計值
        - 虧損交易: MAE = |pnl_gross|, MFE = 估計值
        
        實際交易系統應該記錄每筆交易的 tick 級別浮盈浮虧
        """
        print("\n🔍 計算 MFE/MAE...")
        
        for trade in self.all_trades:
            entry_price = trade['entry_price']
            exit_price = trade['exit_price']
            direction = trade['direction']
            pnl_gross_pct = abs(trade['pnl_gross']) / 300  # 假設 $300 position
            
            # 簡化計算（實際應該用 tick 數據）
            if trade['pnl_net'] > 0:
                # 盈利交易: MFE >= pnl, MAE 估計為小虧損
                trade['mfe_pct'] = pnl_gross_pct * 1.2  # 假設最大浮盈比實現利潤高 20%
                trade['mae_pct'] = pnl_gross_pct * 0.3  # 假設中間有過浮虧
            else:
                # 虧損交易: MAE = |pnl|, MFE 估計為小盈利
                trade['mae_pct'] = pnl_gross_pct * 1.1
                trade['mfe_pct'] = pnl_gross_pct * 0.2  # 假設中間有過浮盈
            
            # 計算實際 pnl 百分比（用於對比）
            if direction == 'LONG':
                trade['pnl_pct'] = (exit_price - entry_price) / entry_price
            else:  # SHORT
                trade['pnl_pct'] = (entry_price - exit_price) / entry_price
        
        print("  ✅ MFE/MAE 計算完成")
    
    def analyze_distributions(self) -> Dict:
        """分析 MFE/MAE 分佈"""
        print("\n📈 分析分佈...")
        
        # 盈利交易的 MFE 分佈
        winning_mfe = [t['mfe_pct'] * 100 for t in self.winning_trades]
        winning_mae = [t['mae_pct'] * 100 for t in self.winning_trades]
        
        # 虧損交易的 MAE 分佈
        losing_mfe = [t['mfe_pct'] * 100 for t in self.losing_trades]
        losing_mae = [t['mae_pct'] * 100 for t in self.losing_trades]
        
        analysis = {
            'winning_trades': {
                'count': len(self.winning_trades),
                'mfe': {
                    'mean': np.mean(winning_mfe) if winning_mfe else 0,
                    'median': np.median(winning_mfe) if winning_mfe else 0,
                    'p25': np.percentile(winning_mfe, 25) if winning_mfe else 0,
                    'p50': np.percentile(winning_mfe, 50) if winning_mfe else 0,
                    'p75': np.percentile(winning_mfe, 75) if winning_mfe else 0,
                    'p90': np.percentile(winning_mfe, 90) if winning_mfe else 0,
                    'min': np.min(winning_mfe) if winning_mfe else 0,
                    'max': np.max(winning_mfe) if winning_mfe else 0
                },
                'mae': {
                    'mean': np.mean(winning_mae) if winning_mae else 0,
                    'median': np.median(winning_mae) if winning_mae else 0,
                    'p25': np.percentile(winning_mae, 25) if winning_mae else 0,
                    'p50': np.percentile(winning_mae, 50) if winning_mae else 0,
                    'p75': np.percentile(winning_mae, 75) if winning_mae else 0,
                    'p90': np.percentile(winning_mae, 90) if winning_mae else 0
                }
            },
            'losing_trades': {
                'count': len(self.losing_trades),
                'mfe': {
                    'mean': np.mean(losing_mfe) if losing_mfe else 0,
                    'median': np.median(losing_mfe) if losing_mfe else 0,
                    'p25': np.percentile(losing_mfe, 25) if losing_mfe else 0,
                    'p50': np.percentile(losing_mfe, 50) if losing_mfe else 0,
                    'p75': np.percentile(losing_mfe, 75) if losing_mfe else 0,
                    'p90': np.percentile(losing_mfe, 90) if losing_mfe else 0
                },
                'mae': {
                    'mean': np.mean(losing_mae) if losing_mae else 0,
                    'median': np.median(losing_mae) if losing_mae else 0,
                    'p25': np.percentile(losing_mae, 25) if losing_mae else 0,
                    'p50': np.percentile(losing_mae, 50) if losing_mae else 0,
                    'p75': np.percentile(losing_mae, 75) if losing_mae else 0,
                    'p90': np.percentile(losing_mae, 90) if losing_mae else 0,
                    'min': np.min(losing_mae) if losing_mae else 0,
                    'max': np.max(losing_mae) if losing_mae else 0
                }
            }
        }
        
        return analysis
    
    def recommend_tp_sl(self, analysis: Dict) -> Dict:
        """
        基於 MFE/MAE 分析推薦 TP/SL 設定
        
        邏輯：
        1. TP 應該設在盈利交易 MFE 的 70-80 百分位（不要太貪心）
        2. SL 應該設在虧損交易 MAE 的 50-60 百分位（給一點空間）
        """
        print("\n💡 生成 TP/SL 推薦...")
        
        # TP 推薦：盈利交易 MFE 的 75 百分位
        tp_target = analysis['winning_trades']['mfe']['p75']
        
        # SL 推薦：虧損交易 MAE 的 50 百分位
        sl_target = analysis['losing_trades']['mae']['median']
        
        # 假設當前 ATR = 0.4%（BTC 15m 平均值）
        current_atr = 0.4
        
        # 計算建議的 ATR 倍數
        recommended_tp_multiplier = tp_target / current_atr if current_atr > 0 else 2.0
        recommended_sl_multiplier = sl_target / current_atr if current_atr > 0 else 1.0
        
        recommendations = {
            'take_profit': {
                'target_pct': float(tp_target),
                'current_atr_pct': float(current_atr),
                'recommended_atr_multiplier': float(round(recommended_tp_multiplier, 2)),
                'current_setting': float(2.0),
                'adjustment_needed': bool(recommended_tp_multiplier != 2.0),
                'adjustment_pct': float((recommended_tp_multiplier - 2.0) / 2.0 * 100)
            },
            'stop_loss': {
                'target_pct': float(sl_target),
                'current_atr_pct': float(current_atr),
                'recommended_atr_multiplier': float(round(recommended_sl_multiplier, 2)),
                'current_setting': float(1.0),
                'adjustment_needed': bool(recommended_sl_multiplier != 1.0),
                'adjustment_pct': float((recommended_sl_multiplier - 1.0) / 1.0 * 100)
            }
        }
        
        return recommendations
    
    def print_summary(self, analysis: Dict, recommendations: Dict) -> None:
        """打印分析摘要"""
        print("\n" + "="*80)
        print("📊 MFE/MAE 分析報告 - v2.1 (2021-2025)")
        print("="*80)
        
        print("\n【盈利交易 MFE 分佈】(最大浮盈)")
        mfe_data = analysis['winning_trades']['mfe']
        print(f"  筆數: {analysis['winning_trades']['count']}")
        print(f"  平均: {mfe_data['mean']:.3f}%")
        print(f"  中位數: {mfe_data['median']:.3f}%")
        print(f"  25%: {mfe_data['p25']:.3f}%  |  50%: {mfe_data['p50']:.3f}%  |  75%: {mfe_data['p75']:.3f}%  |  90%: {mfe_data['p90']:.3f}%")
        print(f"  範圍: {mfe_data['min']:.3f}% ~ {mfe_data['max']:.3f}%")
        
        print("\n【盈利交易 MAE 分佈】(最大浮虧)")
        mae_data = analysis['winning_trades']['mae']
        print(f"  平均: {mae_data['mean']:.3f}%")
        print(f"  中位數: {mae_data['median']:.3f}%")
        print(f"  25%: {mae_data['p25']:.3f}%  |  50%: {mae_data['p50']:.3f}%  |  75%: {mae_data['p75']:.3f}%")
        
        print("\n【虧損交易 MAE 分佈】(最大浮虧)")
        mae_data = analysis['losing_trades']['mae']
        print(f"  筆數: {analysis['losing_trades']['count']}")
        print(f"  平均: {mae_data['mean']:.3f}%")
        print(f"  中位數: {mae_data['median']:.3f}%")
        print(f"  25%: {mae_data['p25']:.3f}%  |  50%: {mae_data['p50']:.3f}%  |  75%: {mae_data['p75']:.3f}%  |  90%: {mae_data['p90']:.3f}%")
        print(f"  範圍: {mae_data['min']:.3f}% ~ {mae_data['max']:.3f}%")
        
        print("\n【虧損交易 MFE 分佈】(最大浮盈)")
        mfe_data = analysis['losing_trades']['mfe']
        print(f"  平均: {mfe_data['mean']:.3f}%")
        print(f"  中位數: {mfe_data['median']:.3f}%")
        print(f"  25%: {mfe_data['p25']:.3f}%  |  50%: {mfe_data['p50']:.3f}%  |  75%: {mfe_data['p75']:.3f}%")
        
        print("\n" + "="*80)
        print("💡 TP/SL 參數推薦")
        print("="*80)
        
        tp = recommendations['take_profit']
        print(f"\n【Take Profit】")
        print(f"  目標: {tp['target_pct']:.3f}% (盈利交易 MFE 的 75 百分位)")
        print(f"  當前 ATR: {tp['current_atr_pct']:.3f}%")
        print(f"  建議 ATR 倍數: {tp['recommended_atr_multiplier']:.2f}x")
        print(f"  當前設定: {tp['current_setting']:.2f}x")
        if tp['adjustment_needed']:
            change = tp['recommended_atr_multiplier'] - tp['current_setting']
            print(f"  ⚠️ 需要調整: {change:+.2f}x ({change/tp['current_setting']*100:+.1f}%)")
        else:
            print(f"  ✅ 當前設定合理")
        
        sl = recommendations['stop_loss']
        print(f"\n【Stop Loss】")
        print(f"  目標: {sl['target_pct']:.3f}% (虧損交易 MAE 的 50 百分位)")
        print(f"  當前 ATR: {sl['current_atr_pct']:.3f}%")
        print(f"  建議 ATR 倍數: {sl['recommended_atr_multiplier']:.2f}x")
        print(f"  當前設定: {sl['current_setting']:.2f}x")
        if sl['adjustment_needed']:
            change = sl['recommended_atr_multiplier'] - sl['current_setting']
            print(f"  ⚠️ 需要調整: {change:+.2f}x ({change/sl['current_setting']*100:+.1f}%)")
        else:
            print(f"  ✅ 當前設定合理")
        
        print("\n" + "="*80)
    
    def save_results(self, analysis: Dict, recommendations: Dict, output_path: str = 'data/mfe_mae_analysis_v21.json') -> None:
        """儲存分析結果"""
        results = {
            'timestamp': datetime.now().isoformat(),
            'version': 'v2.1',
            'total_trades': len(self.all_trades),
            'winning_trades': len(self.winning_trades),
            'losing_trades': len(self.losing_trades),
            'analysis': analysis,
            'recommendations': recommendations,
            # 只儲存簡化的交易資訊（避免 JSON 序列化問題）
            'trades_summary': {
                'winning_pnl_pcts': [t.get('pnl_pct', 0) * 100 for t in self.winning_trades],
                'losing_pnl_pcts': [t.get('pnl_pct', 0) * 100 for t in self.losing_trades]
            }
        }
        
        # 確保目錄存在
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\n💾 分析結果已儲存: {output_path}")
    
    def run_full_analysis(self) -> None:
        """執行完整分析流程"""
        print("\n🚀 開始 MFE/MAE 完整分析")
        print("="*80)
        
        # 1. 載入交易記錄
        self.load_v21_trades()
        
        # 2. 計算 MFE/MAE
        self.calculate_mfe_mae()
        
        # 3. 分析分佈
        analysis = self.analyze_distributions()
        
        # 4. 生成推薦
        recommendations = self.recommend_tp_sl(analysis)
        
        # 5. 打印摘要
        self.print_summary(analysis, recommendations)
        
        # 6. 儲存結果
        self.save_results(analysis, recommendations)
        
        print("\n✅ MFE/MAE 分析完成！")


if __name__ == '__main__':
    analyzer = MFEMAEAnalyzer()
    analyzer.run_full_analysis()
