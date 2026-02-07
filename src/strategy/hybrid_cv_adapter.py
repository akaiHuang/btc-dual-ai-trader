"""
Hybrid Strategy Time-Series CV Adapter
======================================

將 HybridFundingTechnicalStrategy 適配到 Time-Series CV 框架
"""

import sys
sys.path.insert(0, '/Users/akaihuangm1/Desktop/btn')

import pandas as pd
import numpy as np
from typing import Dict, List, Any
from pathlib import Path

from src.strategy.hybrid_funding_technical import HybridFundingTechnicalStrategy
from scripts.test_hybrid_strategy import HybridBacktester


class HybridCVStrategy:
    """
    將混合策略適配到 Time-Series CV 框架
    
    實現 Time-Series CV 需要的介面：
    - get_param_grid(): 返回參數搜索空間
    - fit(df_train, param_grid): 在訓練集上搜索最優參數
    - backtest(df_test, params): 用凍結參數在測試集上回測
    """
    
    def __init__(self):
        self.best_params = None
        self.train_results = []
    
    def get_param_grid(self) -> Dict[str, List]:
        """
        返回參數搜索空間
        
        Returns:
            參數網格字典
        """
        return {
            # Funding Rate 參數（Z-score）
            "funding_zscore_threshold": [1.5, 2.0, 2.5],
            "funding_lookback_days": [60, 90],
            
            # RSI 參數
            "rsi_oversold": [25, 30, 35],
            "rsi_overbought": [65, 70, 75],
            
            # 成交量參數
            "volume_spike_threshold": [1.5, 2.0, 2.5],
            
            # 信號閾值
            "signal_score_threshold": [0.3, 0.4, 0.5, 0.6],
            
            # 其他
            "require_funding_confirmation": [False],
        }
    
    def _generate_param_combinations(self, param_grid: Dict) -> List[Dict]:
        """生成所有參數組合"""
        import itertools
        
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        
        combinations = []
        for combo in itertools.product(*values):
            param_dict = dict(zip(keys, combo))
            combinations.append(param_dict)
        
        return combinations
    
    def _score_result(self, result: Dict) -> float:
        """
        評分函數
        
        綜合考慮：
        - 回報率（主要）
        - 勝率（次要）
        - 交易頻率（懲罰過少）
        - 回撤（懲罰）
        
        Args:
            result: 回測結果
            
        Returns:
            綜合評分
        """
        return_pct = result.get('return_pct', 0) / 100  # 轉為小數
        win_rate = result.get('win_rate', 0)
        trades_per_day = result.get('trades_per_day', 0)
        
        # 基礎分數
        score = return_pct * 0.5 + win_rate * 0.3
        
        # 交易頻率調整
        if trades_per_day < 0.01:  # < 0.01 筆/天太少
            score *= 0.5
        elif trades_per_day < 0.05:
            score *= 0.8
        
        return score
    
    def fit(self, df_train: pd.DataFrame, param_grid: Dict = None) -> Dict:
        """
        在訓練集上搜索最優參數
        
        Args:
            df_train: 訓練數據
            param_grid: 參數網格（可選，默認使用 get_param_grid()）
            
        Returns:
            最優參數字典
        """
        if param_grid is None:
            param_grid = self.get_param_grid()
        
        param_combinations = self._generate_param_combinations(param_grid)
        
        print(f"   Grid Search: 測試 {len(param_combinations)} 組參數...")
        
        best_score = -float('inf')
        best_params = None
        best_result = None
        
        for i, params in enumerate(param_combinations):
            if i % 20 == 0 and i > 0:
                print(f"   進度: {i}/{len(param_combinations)} ({i/len(param_combinations)*100:.1f}%)")
            
            try:
                # 創建策略
                strategy = HybridFundingTechnicalStrategy(**params)
                backtest_engine = HybridBacktester()
                
                # 回測
                result = backtest_engine.backtest(df_train, strategy)
                
                # 評分
                score = self._score_result(result)
                
                # 記錄
                self.train_results.append({
                    'params': params,
                    'result': result,
                    'score': score
                })
                
                # 更新最優
                if score > best_score:
                    best_score = score
                    best_params = params
                    best_result = result
                    
            except Exception as e:
                print(f"   警告: 參數 {params} 測試失敗: {e}")
                continue
        
        self.best_params = best_params
        
        print(f"   ✅ 最優參數: {best_params}")
        print(f"   訓練集表現: {best_result['total_trades']}筆, "
              f"{best_result['win_rate']:.1%}勝率, "
              f"{best_result['return_pct']:+.1f}%回報")
        
        return best_params
    
    def backtest(self, df_test: pd.DataFrame, params: Dict) -> Dict:
        """
        用凍結參數在測試集上回測
        
        Args:
            df_test: 測試數據
            params: 凍結的參數（來自 fit()）
            
        Returns:
            標準化的回測結果
        """
        # 創建策略（使用凍結參數）
        strategy = HybridFundingTechnicalStrategy(**params)
        backtest_engine = HybridBacktester()
        
        # 回測
        result = backtest_engine.backtest(df_test, strategy)
        
        # 標準化輸出（符合 Time-Series CV 接口）
        return {
            "trades": result["total_trades"],
            "win_rate": result["win_rate"],
            "return": result["return_pct"] / 100,  # 轉為小數
            "return_pct": result["return_pct"],
            "trades_per_day": result["trades_per_day"],
            "final_capital": result["final_capital"],
            "win_trades": result.get("win_trades", 0),
            "loss_trades": result.get("loss_trades", 0),
        }


def demo_cv_run():
    """
    演示如何使用 Time-Series CV 框架測試混合策略
    """
    print("="*70)
    print("🧪 Time-Series CV 演示：混合策略")
    print("="*70)
    print()
    
    # 載入數據
    print("📂 載入數據...")
    df_all = pd.read_parquet('data/historical/BTCUSDT_15m_with_l0.parquet')
    df_all['timestamp'] = pd.to_datetime(df_all['timestamp'])
    df_all['year'] = df_all['timestamp'].dt.year
    
    print(f"✅ 數據載入完成: {len(df_all)} 根 K 線")
    print(f"   時間範圍: {df_all['timestamp'].min()} ~ {df_all['timestamp'].max()}")
    print()
    
    # 創建策略
    strategy = HybridCVStrategy()
    
    # 運行 Time-Series CV
    print("🔄 運行 Time-Series CV...")
    print()
    
    results = []
    
    # Fold 1: Train 2020 → Test 2021
    print("="*70)
    print("📊 Fold 1: Train 2020 → Test 2021")
    print("="*70)
    
    df_train = df_all[df_all['year'] == 2020].copy()
    df_test = df_all[df_all['year'] == 2021].copy()
    
    print(f"訓練集: {len(df_train)} 根 K 線")
    print(f"測試集: {len(df_test)} 根 K 線")
    print()
    
    # 訓練（搜索參數）
    best_params = strategy.fit(df_train)
    print()
    
    # 測試（凍結參數）
    print("測試集評估（參數凍結）...")
    test_result = strategy.backtest(df_test, best_params)
    
    print(f"✅ 測試集結果: {test_result['trades']}筆, "
          f"{test_result['win_rate']:.1%}勝率, "
          f"{test_result['return_pct']:+.1f}%回報")
    print()
    
    results.append({
        'fold': 1,
        'train_period': '2020',
        'test_year': 2021,
        'best_params': best_params,
        'test_result': test_result
    })
    
    # 可以繼續添加更多 Folds...
    # Fold 2: Train 2020-2021 → Test 2022
    # Fold 3: Train 2020-2022 → Test 2023
    # ...
    
    print("="*70)
    print("📈 Time-Series CV 結果總結")
    print("="*70)
    
    for r in results:
        print(f"\nFold {r['fold']}: Train {r['train_period']} → Test {r['test_year']}")
        print(f"  最優參數: {r['best_params']}")
        test = r['test_result']
        print(f"  測試表現: {test['trades']}筆, "
              f"{test['win_rate']:.1%}勝率, "
              f"{test['return_pct']:+.1f}%回報, "
              f"{test['trades_per_day']:.2f}筆/天")
    
    print()
    print("="*70)


if __name__ == "__main__":
    demo_cv_run()
