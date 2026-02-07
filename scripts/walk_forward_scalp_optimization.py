"""
Walk-Forward Optimization for Scalping Strategy

逐年優化策略參數，避免過度擬合
流程：
  2020 → 訓練 → 模型 v1.0
  2021 → 測試 v1.0 → 優化 → 模型 v1.1
  2022 → 測試 v1.1 → 優化 → 模型 v1.2
  ...
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple
import json
from dataclasses import dataclass, asdict

from src.strategy.scalp_strategy_v1 import ScalpStrategyV1, ScalpSignal
from scripts.test_scalp_strategy import ScalpBacktester


@dataclass
class StrategyConfig:
    """策略配置參數"""
    version: str
    tp_pct: float = 0.0015
    sl_pct: float = 0.001
    time_stop_seconds: int = 180
    
    funding_threshold: float = 0.05
    oi_change_threshold: float = 0.15
    liquidation_threshold: float = 1000
    whale_threshold: float = 2000
    
    default_leverage: int = 15
    high_confidence_leverage: int = 20
    low_confidence_leverage: int = 10
    
    min_confidence: float = 0.6
    
    def to_dict(self) -> dict:
        return asdict(self)


class WalkForwardOptimizer:
    """
    Walk-Forward 優化器
    
    核心思想：
    1. 在 Year N 訓練/優化參數
    2. 在 Year N+1 測試
    3. 根據測試結果調整參數
    4. 重複直到最新年份
    """
    
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.df['timestamp'] = pd.to_datetime(self.df['timestamp'])
        
        # 歷史配置記錄
        self.config_history: List[StrategyConfig] = []
        self.results_history: List[Dict] = []
    
    def run_walk_forward(
        self,
        start_year: int = 2020,
        end_year: int = 2025
    ):
        """
        運行 Walk-Forward 優化
        
        Args:
            start_year: 起始年份（訓練）
            end_year: 結束年份（最終測試）
        """
        print("=" * 80)
        print("🔄 Walk-Forward Optimization - Scalping Strategy")
        print("=" * 80)
        print()
        
        # 初始配置（基於直覺設定）
        current_config = StrategyConfig(
            version="v1.0_baseline",
            tp_pct=0.0015,
            sl_pct=0.001,
            time_stop_seconds=180,
            funding_threshold=0.05,
            oi_change_threshold=0.15,
            liquidation_threshold=1000,
            whale_threshold=2000,
            min_confidence=0.6,
        )
        
        print(f"📋 初始配置 (基於直覺):")
        print(f"   TP: {current_config.tp_pct:.2%}")
        print(f"   SL: {current_config.sl_pct:.2%}")
        print(f"   時間止損: {current_config.time_stop_seconds}s")
        print(f"   最低信心度: {current_config.min_confidence:.0%}")
        print()
        
        # 逐年測試與優化
        for year in range(start_year, end_year + 1):
            print("=" * 80)
            print(f"📅 Year {year}")
            print("=" * 80)
            print()
            
            # 測試當前配置
            print(f"🧪 測試配置: {current_config.version}")
            stats = self._test_year(year, current_config)
            
            # 記錄結果
            self.config_history.append(current_config)
            self.results_history.append({
                'year': year,
                'config': current_config.to_dict(),
                'stats': stats
            })
            
            # 打印當年結果
            self._print_year_summary(year, stats)
            
            # 根據結果優化（除了最後一年）
            if year < end_year:
                print()
                print(f"🔧 根據 {year} 年結果優化參數...")
                current_config = self._optimize_config(
                    current_config,
                    stats,
                    year
                )
                print(f"✅ 新配置: {current_config.version}")
                print()
        
        # 生成最終報告
        print()
        print("=" * 80)
        print("📊 Walk-Forward 優化完成")
        print("=" * 80)
        self._generate_final_report()
        
        # 保存結果
        self._save_results()
    
    def _test_year(
        self,
        year: int,
        config: StrategyConfig
    ) -> Dict:
        """測試某一年的表現"""
        # 篩選數據
        df_year = self.df[self.df['timestamp'].dt.year == year].copy()
        
        if len(df_year) == 0:
            print(f"⚠️  {year} 年無數據")
            return {}
        
        # 創建策略
        strategy = ScalpStrategyV1(
            timeframe="15m",
            tp_pct=config.tp_pct,
            sl_pct=config.sl_pct,
            time_stop_seconds=config.time_stop_seconds,
            funding_threshold=config.funding_threshold,
            oi_change_threshold=config.oi_change_threshold,
            liquidation_threshold=config.liquidation_threshold,
            whale_threshold=config.whale_threshold,
            default_leverage=config.default_leverage,
            high_confidence_leverage=config.high_confidence_leverage,
            low_confidence_leverage=config.low_confidence_leverage,
            min_confidence=config.min_confidence,
        )
        
        # 運行回測
        backtester = ScalpBacktester(strategy)
        stats = backtester.run_backtest(
            self.df,
            start_date=f'{year}-01-01',
            end_date=f'{year}-12-31'
        )
        
        return stats
    
    def _optimize_config(
        self,
        current_config: StrategyConfig,
        stats: Dict,
        year: int
    ) -> StrategyConfig:
        """
        根據測試結果優化配置
        
        優化邏輯：
        1. 勝率太低 (<55%) → 提高過濾閾值（更嚴格）
        2. 勝率很高 (>70%) → 放寬閾值（增加交易數）
        3. 盈虧比太低 (<1.5) → 調整 TP/SL
        4. 交易數太少 (<1/day) → 放寬所有閾值
        5. 時間止損太多 (>10%) → 延長時間或調整 TP
        """
        if not stats:
            return current_config
        
        new_config = StrategyConfig(
            version=f"v1.{year - 2019}_optimized",  # v1.1, v1.2, ...
            tp_pct=current_config.tp_pct,
            sl_pct=current_config.sl_pct,
            time_stop_seconds=current_config.time_stop_seconds,
            funding_threshold=current_config.funding_threshold,
            oi_change_threshold=current_config.oi_change_threshold,
            liquidation_threshold=current_config.liquidation_threshold,
            whale_threshold=current_config.whale_threshold,
            default_leverage=current_config.default_leverage,
            high_confidence_leverage=current_config.high_confidence_leverage,
            low_confidence_leverage=current_config.low_confidence_leverage,
            min_confidence=current_config.min_confidence,
        )
        
        win_rate = stats.get('win_rate', 0)
        profit_factor = stats.get('profit_factor', 0)
        avg_trades_per_day = stats.get('avg_trades_per_day', 0)
        exit_reasons = stats.get('exit_reasons', {})
        time_stop_pct = exit_reasons.get('TIME_STOP', 0) / stats.get('total_trades', 1)
        
        changes = []
        
        # 規則 1: 勝率太低 → 提高閾值
        if win_rate < 0.55:
            new_config.min_confidence += 0.05
            new_config.funding_threshold *= 1.2
            new_config.oi_change_threshold *= 1.2
            changes.append(f"勝率 {win_rate:.1%} 太低 → 提高閾值")
        
        # 規則 2: 勝率很高 → 放寬閾值
        elif win_rate > 0.70:
            new_config.min_confidence -= 0.05
            new_config.funding_threshold *= 0.8
            new_config.oi_change_threshold *= 0.8
            changes.append(f"勝率 {win_rate:.1%} 很高 → 放寬閾值增加交易")
        
        # 規則 3: 盈虧比太低 → 調整 TP/SL
        if profit_factor < 1.5:
            new_config.tp_pct *= 1.1  # TP 放大 10%
            changes.append(f"盈虧比 {profit_factor:.2f} 太低 → TP 提高至 {new_config.tp_pct:.2%}")
        
        # 規則 4: 交易數太少 → 放寬所有閾值
        if avg_trades_per_day < 1.0:
            new_config.funding_threshold *= 0.7
            new_config.oi_change_threshold *= 0.7
            new_config.liquidation_threshold *= 0.7
            new_config.whale_threshold *= 0.7
            new_config.min_confidence = max(0.5, new_config.min_confidence - 0.1)
            changes.append(f"交易數 {avg_trades_per_day:.1f}/天 太少 → 全面放寬閾值")
        
        # 規則 5: 時間止損太多 → 延長時間
        if time_stop_pct > 0.1:
            new_config.time_stop_seconds = int(new_config.time_stop_seconds * 1.5)
            changes.append(f"時間止損 {time_stop_pct:.1%} 太多 → 延長至 {new_config.time_stop_seconds}s")
        
        # 限制範圍
        new_config.tp_pct = max(0.001, min(0.003, new_config.tp_pct))
        new_config.sl_pct = max(0.0005, min(0.002, new_config.sl_pct))
        new_config.time_stop_seconds = max(60, min(600, new_config.time_stop_seconds))
        new_config.min_confidence = max(0.5, min(0.8, new_config.min_confidence))
        new_config.funding_threshold = max(0.01, min(0.15, new_config.funding_threshold))
        new_config.oi_change_threshold = max(0.05, min(0.3, new_config.oi_change_threshold))
        
        # 打印優化調整
        if changes:
            print(f"   調整原因:")
            for change in changes:
                print(f"      - {change}")
        else:
            print(f"   ✅ 當前配置表現良好，保持不變")
        
        return new_config
    
    def _print_year_summary(self, year: int, stats: Dict):
        """打印年度總結"""
        if not stats:
            return
        
        print(f"📈 {year} 年結果:")
        print(f"   總交易: {stats.get('total_trades', 0)} 筆")
        print(f"   勝率: {stats.get('win_rate', 0):.1%}")
        print(f"   總盈虧: {stats.get('total_pnl_pct', 0):.1%} (槓桿後)")
        print(f"   盈虧比: {stats.get('profit_factor', 0):.2f}")
        print(f"   每日交易: {stats.get('avg_trades_per_day', 0):.1f} 筆")
        
        # 判斷表現
        win_rate = stats.get('win_rate', 0)
        total_pnl = stats.get('total_pnl_pct', 0)
        
        if win_rate > 0.65 and total_pnl > 0:
            print(f"   ✅ 表現優秀")
        elif win_rate > 0.55 and total_pnl > 0:
            print(f"   ⚠️  表現一般")
        else:
            print(f"   ❌ 表現不佳")
    
    def _generate_final_report(self):
        """生成最終報告"""
        print()
        print("📊 各年度表現對比:")
        print()
        print(f"{'年份':<8} {'配置':<20} {'交易數':<10} {'勝率':<10} {'總盈虧':<12} {'盈虧比':<10}")
        print("-" * 80)
        
        for result in self.results_history:
            year = result['year']
            config_version = result['config']['version']
            stats = result['stats']
            
            if not stats:
                continue
            
            total_trades = stats.get('total_trades', 0)
            win_rate = stats.get('win_rate', 0)
            total_pnl = stats.get('total_pnl_pct', 0)
            profit_factor = stats.get('profit_factor', 0)
            
            print(f"{year:<8} {config_version:<20} {total_trades:<10} {win_rate:<10.1%} {total_pnl:<12.1%} {profit_factor:<10.2f}")
        
        print()
        
        # 整體統計
        total_trades_all = sum(r['stats'].get('total_trades', 0) for r in self.results_history if r['stats'])
        total_pnl_all = sum(r['stats'].get('total_pnl_pct', 0) for r in self.results_history if r['stats'])
        avg_win_rate = np.mean([r['stats'].get('win_rate', 0) for r in self.results_history if r['stats']])
        
        print(f"📊 整體表現 (所有年份):")
        print(f"   總交易數: {total_trades_all}")
        print(f"   平均勝率: {avg_win_rate:.1%}")
        print(f"   累計盈虧: {total_pnl_all:.1%} (槓桿後)")
        print()
        
        # 最終建議
        latest_config = self.config_history[-1]
        latest_stats = self.results_history[-1]['stats']
        
        if latest_stats and latest_stats.get('win_rate', 0) > 0.6 and latest_stats.get('total_pnl_pct', 0) > 0:
            print(f"✅ 最終配置 {latest_config.version} 可用於實盤測試")
            print(f"   建議: 小額測試 (100-500U)")
        else:
            print(f"⚠️  最終配置仍需改進")
            print(f"   建議: 繼續優化或尋找其他策略")
    
    def _save_results(self):
        """保存結果"""
        output = {
            'optimization_date': datetime.now().isoformat(),
            'config_history': [c.to_dict() for c in self.config_history],
            'results_history': self.results_history,
        }
        
        output_file = 'backtest_results/scalp_walk_forward_optimization.json'
        with open(output_file, 'w') as f:
            json.dump(output, f, indent=2, default=str)
        
        print(f"💾 結果已保存至: {output_file}")


def main():
    """主函數"""
    # 讀取數據
    print("讀取歷史數據...")
    df = pd.read_parquet('data/historical/BTCUSDT_15m.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    print(f"數據範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    print()
    
    # 創建優化器
    optimizer = WalkForwardOptimizer(df)
    
    # 運行 Walk-Forward 優化
    optimizer.run_walk_forward(
        start_year=2020,
        end_year=2025
    )


if __name__ == "__main__":
    main()
