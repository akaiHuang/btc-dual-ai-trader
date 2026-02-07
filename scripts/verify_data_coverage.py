#!/usr/bin/env python3
"""
驗證大單數據覆蓋率

檢查 2020-2025 各年份的數據完整性
識別數據缺口和異常
"""

import pandas as pd
from pathlib import Path
import argparse


def verify_large_trade_coverage(data_file: str = None):
    """
    驗證大單數據覆蓋率
    
    Args:
        data_file: 大單數據文件路徑
    """
    if data_file is None:
        data_dir = Path("data/historical")
        files = list(data_dir.glob("BTCUSDT_agg_trades_*.parquet"))
        if not files:
            print("❌ 未找到大單數據文件")
            return
        data_file = files[0]
    
    print("="*70)
    print("📊 大單數據覆蓋率驗證")
    print("="*70)
    print(f"數據文件: {data_file}")
    print()
    
    # 載入數據
    df = pd.read_parquet(data_file)
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)  # 移除時區
    
    # 基本統計
    print("📈 基本統計")
    print("-"*70)
    print(f"總大單數: {len(df):,} 筆")
    print(f"時間範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    print(f"平均單量: {df['qty'].mean():.2f} BTC")
    print(f"最大單量: {df['qty'].max():.2f} BTC")
    print(f"買單比例: {(df['side'] == 'BUY').sum() / len(df) * 100:.1f}%")
    print()
    
    # 按年份統計
    df['year'] = df['timestamp'].dt.year
    df['month'] = df['timestamp'].dt.to_period('M')
    
    print("📅 各年份覆蓋率")
    print("-"*70)
    yearly_stats = df.groupby('year').agg({
        'trade_id': 'count',
        'qty': ['sum', 'mean', 'max'],
        'side': lambda x: (x == 'BUY').sum() / len(x) * 100
    }).round(2)
    yearly_stats.columns = ['交易數', '總量(BTC)', '平均(BTC)', '最大(BTC)', '買單%']
    print(yearly_stats)
    print()
    
    # 檢查數據缺口
    print("🔍 數據缺口分析")
    print("-"*70)
    
    # 預期時間範圍
    expected_start = pd.Timestamp('2020-01-01')
    expected_end = pd.Timestamp('2025-11-15')
    actual_start = df['timestamp'].min()
    actual_end = df['timestamp'].max()
    
    if actual_start > expected_start:
        gap_days = (actual_start - expected_start).days
        print(f"⚠️  起始缺口: {expected_start.date()} ~ {actual_start.date()} ({gap_days} 天)")
        print(f"   原因: Binance aggTrades 可能不保留 {expected_start.year} 年數據")
    
    if actual_end < expected_end:
        gap_days = (expected_end - actual_end).days
        print(f"⚠️  結束缺口: {actual_end.date()} ~ {expected_end.date()} ({gap_days} 天)")
    
    # 檢查月度覆蓋率
    monthly_counts = df.groupby('month').size()
    
    # 找出沒有數據的月份
    all_months = pd.period_range(start='2020-01', end='2025-11', freq='M')
    missing_months = [m for m in all_months if m not in monthly_counts.index]
    
    if missing_months:
        print()
        print(f"⚠️  缺失月份: {len(missing_months)} 個月")
        print("   (這些月份沒有 >=10 BTC 的大單)")
        for month in missing_months[:12]:  # 只顯示前 12 個
            print(f"   - {month}")
        if len(missing_months) > 12:
            print(f"   ... 還有 {len(missing_months) - 12} 個月")
    
    # 檢查數據密度
    print()
    print("📊 各月份數據密度")
    print("-"*70)
    monthly_stats = df.groupby('month').agg({
        'trade_id': 'count',
        'qty': 'sum'
    }).rename(columns={'trade_id': '交易數', 'qty': '總量(BTC)'})
    
    # 只顯示有數據的月份（倒序，最近的在前）
    monthly_stats = monthly_stats.sort_index(ascending=False).head(24)
    print(monthly_stats)
    print()
    
    # 評估數據質量
    print("✅ 數據質量評估")
    print("-"*70)
    
    total_expected_months = len(all_months)
    actual_months = len(monthly_counts)
    coverage_pct = actual_months / total_expected_months * 100
    
    print(f"月份覆蓋率: {actual_months}/{total_expected_months} ({coverage_pct:.1f}%)")
    
    if len(df) < 100:
        print("❌ 數據量過少 (<100 筆)")
        print("   建議: 降低 min_qty 閾值（例如 5 BTC）或檢查 API")
    elif len(df) < 1000:
        print("⚠️  數據量偏少 (<1000 筆)")
        print("   可能原因: Binance 不保留完整歷史數據")
    elif len(df) < 5000:
        print("✅ 數據量尚可 (1000-5000 筆)")
        print("   建議: 可以進行初步回測，但樣本略小")
    else:
        print("✅ 數據量充足 (>5000 筆)")
        print("   可以進行可靠的 Walk-Forward 測試")
    
    print()
    
    # 輸出建議
    print("💡 建議")
    print("-"*70)
    
    if actual_start.year >= 2022:
        print("1. Binance aggTrades 只保留最近 2-3 年數據")
        print("   → 無法獲取 2020-2021 完整數據")
        print("   → 建議使用 Funding Rate（已有 2020-2025 完整數據）作為主要 L0 信號")
        print()
    
    if len(df) < 1000:
        print("2. 大單數量偏少")
        print("   → 降低閾值: --min_qty 5.0 或 3.0")
        print("   → 或使用其他 L0 數據源（Funding Rate + Open Interest）")
        print()
    
    if coverage_pct < 50:
        print("3. 月份覆蓋率低")
        print("   → 這是正常的（大單不是每天都有）")
        print("   → 建議結合技術指標策略")
    
    print()


def main():
    parser = argparse.ArgumentParser(description="驗證大單數據覆蓋率")
    parser.add_argument(
        "--file",
        type=str,
        help="大單數據文件路徑（可選，自動尋找）"
    )
    
    args = parser.parse_args()
    verify_large_trade_coverage(args.file)


if __name__ == "__main__":
    main()
