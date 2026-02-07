"""
執行 Funding Rate 策略的 Time-Series CV
=========================================

目標：
1. 驗證 CV 框架正確性
2. 找到 2020-2025 年的最優參數
3. 避免過擬合

根據 DYNAMIC_OPTIMIZATION_PLAN.md Week 1
"""

import sys
sys.path.insert(0, '/Users/akaihuangm1/Desktop/btn')

import pandas as pd
from pathlib import Path
from src.evaluation.timeseries_cv import TimeSeriesCV
from src.backtest.backtest_engine import BacktestEngine
from src.strategy.funding_rate_strategy import FundingRateStrategy

def main():
    print("="*70)
    print("Funding Rate 策略 - Time-Series Cross-Validation")
    print("="*70)
    print()
    
    # 1. 載入數據
    print("📂 載入數據...")
    df = pd.read_parquet('data/historical/BTCUSDT_15m_with_l0.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    print(f"✅ 數據載入完成: {len(df)} 根 K 線")
    print(f"   時間範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    print()
    
    # 2. 創建 CV
    print("🔧 創建 Time-Series CV...")
    cv = TimeSeriesCV(
        data=df,
        start_year=2020,
        end_year=2025,
        test_duration_months=12
    )
    print()
    
    # 3. 定義參數網格
    # 根據之前的測試結果，我們知道：
    # - 2020年: 交易過多（515筆），需要提高閾值
    # - 2021年: 72.6% 勝率，threshold=0.001 很好
    # - 2023-2025: Funding 極端值少，需要降低閾值
    
    param_grid = {
        'threshold': [0.0008, 0.001, 0.0012, 0.0015, 0.0020],  # Funding Rate 閾值
        'leverage': [15, 20, 25],  # 槓桿
        'lookback_hours': [8, 12, 24]  # 回望時間
    }
    
    print("📊 參數網格:")
    for key, values in param_grid.items():
        print(f"   {key}: {values}")
    print(f"   總組合數: {len(param_grid['threshold']) * len(param_grid['leverage']) * len(param_grid['lookback_hours'])}")
    print()
    
    # 4. 創建回測引擎
    backtest_engine = BacktestEngine()
    
    # 5. 執行 CV
    output_dir = Path('backtest_results/timeseries_cv')
    
    results = cv.run_cv(
        strategy_class=FundingRateStrategy,
        param_grid=param_grid,
        backtest_engine=backtest_engine,
        output_dir=output_dir
    )
    
    # 6. 分析結果
    print("\n" + "="*70)
    print("📈 結果分析")
    print("="*70)
    print()
    
    for result in results:
        fold = cv.folds[result.fold_id - 1]
        print(f"\nFold {result.fold_id}: {fold.test_start.year} 年")
        print(f"  最優參數: {result.best_params}")
        print(f"  訓練集: 勝率 {result.train_metrics.get('win_rate', 0):.1%}, "
              f"回報 {result.train_metrics.get('total_return_pct', 0):.2%}")
        print(f"  測試集: 勝率 {result.test_metrics.get('win_rate', 0):.1%}, "
              f"回報 {result.test_metrics.get('total_return_pct', 0):.2%}, "
              f"交易數 {result.test_metrics.get('total_trades', 0)}")
        
        # 檢查過擬合
        train_return = result.train_metrics.get('total_return_pct', 0)
        test_return = result.test_metrics.get('total_return_pct', 0)
        
        if train_return > 0 and test_return > 0:
            if test_return > train_return * 0.7:
                print(f"  ✅ 泛化良好（測試/訓練 = {test_return/train_return:.2f}）")
            else:
                print(f"  ⚠️ 可能過擬合（測試/訓練 = {test_return/train_return:.2f}）")
        elif test_return > 0:
            print(f"  ✅ 測試集盈利")
        else:
            print(f"  ❌ 測試集虧損")
    
    print("\n" + "="*70)
    print(f"✅ 完成！結果保存在: {output_dir}")
    print("="*70)


if __name__ == "__main__":
    main()
