"""
Time-Series Cross-Validation Framework
=====================================

嚴格時間序列交叉驗證，確保：
1. 訓練數據 < 測試數據（時間順序）
2. 測試期間參數凍結，不看未來
3. 支持參數網格搜索
4. 避免過擬合

Week 1 任務：建立基礎 CV 框架
根據 DYNAMIC_OPTIMIZATION_PLAN.md V2.0
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass
import json
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class CVFold:
    """單個 CV Fold 的定義"""
    fold_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    
    def __str__(self):
        return (f"Fold {self.fold_id}: "
                f"Train[{self.train_start.date()} to {self.train_end.date()}] "
                f"→ Test[{self.test_start.date()} to {self.test_end.date()}]")


@dataclass
class CVResult:
    """CV 結果"""
    fold_id: int
    best_params: Dict[str, Any]
    train_metrics: Dict[str, float]
    test_metrics: Dict[str, float]
    trades: List[Dict]
    
    def to_dict(self):
        return {
            'fold_id': self.fold_id,
            'best_params': self.best_params,
            'train_metrics': self.train_metrics,
            'test_metrics': self.test_metrics,
            'n_trades': len(self.trades)
        }


class TimeSeriesCV:
    """
    時間序列交叉驗證器
    
    核心原則：
    - 擴展窗口：每次 fold 訓練集都包含之前所有數據
    - 時間順序：嚴格保證 train < test
    - 參數凍結：測試期間使用訓練期最優參數，絕不調整
    """
    
    def __init__(
        self,
        data: pd.DataFrame,
        start_year: int = 2020,
        end_year: int = 2025,
        test_duration_months: int = 12
    ):
        """
        Args:
            data: 完整歷史數據，必須包含 timestamp 欄位
            start_year: 起始年份
            end_year: 結束年份
            test_duration_months: 每個測試期長度（月）
        """
        self.data = data.copy()
        self.data['timestamp'] = pd.to_datetime(self.data['timestamp'])
        self.data = self.data.sort_values('timestamp').reset_index(drop=True)
        
        self.start_year = start_year
        self.end_year = end_year
        self.test_duration_months = test_duration_months
        
        # 生成 folds
        self.folds = self._generate_folds()
        
        logger.info(f"初始化 TimeSeriesCV: {len(self.folds)} folds")
        for fold in self.folds:
            logger.info(f"  {fold}")
    
    def _generate_folds(self) -> List[CVFold]:
        """生成擴展窗口 CV folds"""
        folds = []
        fold_id = 1
        
        # 起始：用第一年訓練，第二年測試
        current_test_start = datetime(self.start_year + 1, 1, 1)
        
        while current_test_start.year <= self.end_year:
            # 訓練集：從開始到測試開始前
            train_start = datetime(self.start_year, 1, 1)
            train_end = current_test_start - timedelta(days=1)
            
            # 測試集：當前年度
            test_start = current_test_start
            test_end = datetime(current_test_start.year, 12, 31, 23, 59, 59)
            
            # 確保數據存在
            train_data = self.data[
                (self.data['timestamp'] >= train_start) & 
                (self.data['timestamp'] <= train_end)
            ]
            test_data = self.data[
                (self.data['timestamp'] >= test_start) & 
                (self.data['timestamp'] <= test_end)
            ]
            
            if len(train_data) > 0 and len(test_data) > 0:
                folds.append(CVFold(
                    fold_id=fold_id,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end
                ))
                fold_id += 1
            
            # 下一個測試期
            current_test_start = datetime(current_test_start.year + 1, 1, 1)
        
        return folds
    
    def get_fold_data(self, fold: CVFold) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        獲取指定 fold 的訓練和測試數據
        
        Returns:
            (train_df, test_df)
        """
        train_df = self.data[
            (self.data['timestamp'] >= fold.train_start) & 
            (self.data['timestamp'] <= fold.train_end)
        ].copy()
        
        test_df = self.data[
            (self.data['timestamp'] >= fold.test_start) & 
            (self.data['timestamp'] <= fold.test_end)
        ].copy()
        
        return train_df, test_df
    
    def grid_search(
        self,
        fold: CVFold,
        strategy_class,
        param_grid: Dict[str, List[Any]],
        backtest_engine
    ) -> Tuple[Dict[str, Any], Dict[str, float]]:
        """
        在訓練集上進行網格搜索
        
        Args:
            fold: CV fold
            strategy_class: 策略類
            param_grid: 參數網格，例如 {'threshold': [0.001, 0.0015, 0.002]}
            backtest_engine: 回測引擎
            
        Returns:
            (best_params, best_metrics)
        """
        train_df, _ = self.get_fold_data(fold)
        
        logger.info(f"\n{'='*70}")
        logger.info(f"Fold {fold.fold_id} - 網格搜索")
        logger.info(f"訓練數據: {len(train_df)} 根 K 線")
        logger.info(f"參數網格: {param_grid}")
        logger.info(f"{'='*70}\n")
        
        # 生成所有參數組合
        param_combinations = self._generate_param_combinations(param_grid)
        logger.info(f"總共 {len(param_combinations)} 種參數組合\n")
        
        best_params = None
        best_score = -np.inf
        best_metrics = None
        
        for i, params in enumerate(param_combinations, 1):
            logger.info(f"測試組合 {i}/{len(param_combinations)}: {params}")
            
            # 創建策略實例
            strategy = strategy_class(**params)
            
            # 在訓練集上回測
            result = backtest_engine.run(
                strategy=strategy,
                data=train_df,
                initial_capital=10000,
                leverage=params.get('leverage', 20)
            )
            
            # 評分標準：夏普比率（考慮風險調整回報）
            sharpe = result['summary'].get('sharpe_ratio', 0)
            win_rate = result['summary'].get('win_rate', 0)
            total_return = result['summary'].get('total_return_pct', 0)
            
            # 綜合評分：夏普 * 0.5 + 勝率 * 0.3 + 回報 * 0.2
            score = sharpe * 0.5 + win_rate * 0.3 + total_return * 0.002
            
            logger.info(f"  勝率: {win_rate:.1%}, 回報: {total_return:.2%}, "
                       f"夏普: {sharpe:.3f}, 評分: {score:.4f}")
            
            if score > best_score:
                best_score = score
                best_params = params.copy()
                best_metrics = result['summary'].copy()
                logger.info(f"  ✅ 新的最佳參數！評分: {score:.4f}")
            
            logger.info("")
        
        logger.info(f"\n{'='*70}")
        logger.info(f"Fold {fold.fold_id} - 最佳參數")
        logger.info(f"{'='*70}")
        logger.info(f"參數: {best_params}")
        logger.info(f"訓練集表現:")
        logger.info(f"  勝率: {best_metrics.get('win_rate', 0):.1%}")
        logger.info(f"  回報: {best_metrics.get('total_return_pct', 0):.2%}")
        logger.info(f"  夏普: {best_metrics.get('sharpe_ratio', 0):.3f}")
        logger.info(f"  交易數: {best_metrics.get('total_trades', 0)}")
        logger.info(f"{'='*70}\n")
        
        return best_params, best_metrics
    
    def _generate_param_combinations(
        self, 
        param_grid: Dict[str, List[Any]]
    ) -> List[Dict[str, Any]]:
        """生成所有參數組合"""
        import itertools
        
        keys = list(param_grid.keys())
        values = [param_grid[k] for k in keys]
        
        combinations = []
        for combination in itertools.product(*values):
            combinations.append(dict(zip(keys, combination)))
        
        return combinations
    
    def evaluate_fold(
        self,
        fold: CVFold,
        strategy_class,
        param_grid: Dict[str, List[Any]],
        backtest_engine
    ) -> CVResult:
        """
        評估單個 fold
        
        流程：
        1. 在訓練集上網格搜索找最優參數
        2. 用最優參數在測試集上評估（參數凍結）
        3. 返回結果
        """
        # Step 1: 訓練集網格搜索
        best_params, train_metrics = self.grid_search(
            fold=fold,
            strategy_class=strategy_class,
            param_grid=param_grid,
            backtest_engine=backtest_engine
        )
        
        # Step 2: 測試集評估（參數凍結）
        _, test_df = self.get_fold_data(fold)
        
        logger.info(f"\n{'='*70}")
        logger.info(f"Fold {fold.fold_id} - 測試集評估（參數凍結）")
        logger.info(f"{'='*70}")
        logger.info(f"測試數據: {len(test_df)} 根 K 線")
        logger.info(f"使用參數: {best_params}\n")
        
        # 創建策略實例（使用訓練期最優參數）
        strategy = strategy_class(**best_params)
        
        # 在測試集上回測
        result = backtest_engine.run(
            strategy=strategy,
            data=test_df,
            initial_capital=10000,
            leverage=best_params.get('leverage', 20)
        )
        
        test_metrics = result['summary']
        trades = result['trades']
        
        logger.info(f"測試集表現:")
        logger.info(f"  勝率: {test_metrics.get('win_rate', 0):.1%}")
        logger.info(f"  回報: {test_metrics.get('total_return_pct', 0):.2%}")
        logger.info(f"  夏普: {test_metrics.get('sharpe_ratio', 0):.3f}")
        logger.info(f"  交易數: {test_metrics.get('total_trades', 0)}")
        logger.info(f"{'='*70}\n")
        
        return CVResult(
            fold_id=fold.fold_id,
            best_params=best_params,
            train_metrics=train_metrics,
            test_metrics=test_metrics,
            trades=trades
        )
    
    def run_cv(
        self,
        strategy_class,
        param_grid: Dict[str, List[Any]],
        backtest_engine,
        output_dir: Optional[Path] = None
    ) -> List[CVResult]:
        """
        執行完整 Walk-Forward CV
        
        Args:
            strategy_class: 策略類
            param_grid: 參數網格
            backtest_engine: 回測引擎
            output_dir: 結果保存目錄
            
        Returns:
            所有 fold 的結果列表
        """
        logger.info(f"\n{'='*70}")
        logger.info(f"開始 Walk-Forward Cross-Validation")
        logger.info(f"{'='*70}")
        logger.info(f"總 Folds: {len(self.folds)}")
        logger.info(f"策略: {strategy_class.__name__}")
        logger.info(f"參數網格: {param_grid}")
        logger.info(f"{'='*70}\n")
        
        results = []
        
        for fold in self.folds:
            logger.info(f"\n{'#'*70}")
            logger.info(f"# {fold}")
            logger.info(f"{'#'*70}\n")
            
            result = self.evaluate_fold(
                fold=fold,
                strategy_class=strategy_class,
                param_grid=param_grid,
                backtest_engine=backtest_engine
            )
            
            results.append(result)
            
            # 保存單個 fold 結果
            if output_dir:
                self._save_fold_result(result, output_dir)
        
        # 保存完整報告
        if output_dir:
            self._save_summary_report(results, output_dir)
        
        return results
    
    def _save_fold_result(self, result: CVResult, output_dir: Path):
        """保存單個 fold 結果"""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        fold_file = output_dir / f"fold_{result.fold_id}.json"
        
        with open(fold_file, 'w') as f:
            json.dump({
                'fold_id': result.fold_id,
                'best_params': result.best_params,
                'train_metrics': result.train_metrics,
                'test_metrics': result.test_metrics,
                'trades': result.trades
            }, f, indent=2, default=str)
        
        logger.info(f"✅ Fold {result.fold_id} 結果已保存: {fold_file}")
    
    def _save_summary_report(self, results: List[CVResult], output_dir: Path):
        """保存匯總報告"""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 計算平均表現
        avg_train_win_rate = np.mean([r.train_metrics.get('win_rate', 0) for r in results])
        avg_test_win_rate = np.mean([r.test_metrics.get('win_rate', 0) for r in results])
        
        avg_train_return = np.mean([r.train_metrics.get('total_return_pct', 0) for r in results])
        avg_test_return = np.mean([r.test_metrics.get('total_return_pct', 0) for r in results])
        
        avg_train_sharpe = np.mean([r.train_metrics.get('sharpe_ratio', 0) for r in results])
        avg_test_sharpe = np.mean([r.test_metrics.get('sharpe_ratio', 0) for r in results])
        
        summary = {
            'n_folds': len(results),
            'average_metrics': {
                'train': {
                    'win_rate': avg_train_win_rate,
                    'total_return_pct': avg_train_return,
                    'sharpe_ratio': avg_train_sharpe
                },
                'test': {
                    'win_rate': avg_test_win_rate,
                    'total_return_pct': avg_test_return,
                    'sharpe_ratio': avg_test_sharpe
                }
            },
            'fold_results': [r.to_dict() for r in results]
        }
        
        summary_file = output_dir / "cv_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        
        logger.info(f"\n{'='*70}")
        logger.info(f"Walk-Forward CV 完成！")
        logger.info(f"{'='*70}")
        logger.info(f"總 Folds: {len(results)}")
        logger.info(f"\n平均訓練集表現:")
        logger.info(f"  勝率: {avg_train_win_rate:.1%}")
        logger.info(f"  回報: {avg_train_return:.2%}")
        logger.info(f"  夏普: {avg_train_sharpe:.3f}")
        logger.info(f"\n平均測試集表現:")
        logger.info(f"  勝率: {avg_test_win_rate:.1%}")
        logger.info(f"  回報: {avg_test_return:.2%}")
        logger.info(f"  夏普: {avg_test_sharpe:.3f}")
        logger.info(f"\n匯總報告: {summary_file}")
        logger.info(f"{'='*70}\n")


if __name__ == "__main__":
    # 簡單測試
    print("TimeSeriesCV 框架已建立！")
    print("\n使用方式:")
    print("1. 載入數據: df = pd.read_parquet('data/historical/BTCUSDT_15m_with_l0.parquet')")
    print("2. 創建 CV: cv = TimeSeriesCV(df, start_year=2020, end_year=2025)")
    print("3. 執行: results = cv.run_cv(strategy_class, param_grid, backtest_engine)")
