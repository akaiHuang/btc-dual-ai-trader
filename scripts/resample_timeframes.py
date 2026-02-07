"""
從 1m K線資料重採樣生成其他時間框架
這樣只需要下載 1m 資料，其他時間框架都可以從它生成
"""

from pathlib import Path
import pandas as pd
import numpy as np


def resample_klines(df_1m: pd.DataFrame, interval: str) -> pd.DataFrame:
    """
    從 1m K線重採樣到指定時間框架
    
    Args:
        df_1m: 1分鐘K線資料
        interval: 目標時間框架 (如 '3m', '5m', '15m', '1h' 等)
    
    Returns:
        重採樣後的 DataFrame
    """
    # 設定時間為索引
    df = df_1m.copy()
    df.set_index('timestamp', inplace=True)
    
    # Pandas resample 規則
    resample_rule = interval.replace('m', 'T').replace('h', 'H').replace('d', 'D').replace('w', 'W')
    
    # OHLCV 聚合規則
    resampled = df.resample(resample_rule).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'quote_volume': 'sum',
        'trades': 'sum',
        'taker_buy_base': 'sum',
        'taker_buy_quote': 'sum',
    })
    
    # 移除 NaN（沒有交易的時間段）
    resampled = resampled.dropna()
    
    # 重設索引
    resampled.reset_index(inplace=True)
    
    # 計算 close_time
    interval_ms = {
        '1m': 60000, '3m': 180000, '5m': 300000, '8m': 480000, '10m': 600000,
        '15m': 900000, '30m': 1800000, '1h': 3600000, '2h': 7200000,
        '4h': 14400000, '6h': 21600000, '12h': 43200000, '1d': 86400000,
        '3d': 259200000, '1w': 604800000
    }
    
    ms = interval_ms.get(interval, 60000)
    resampled['close_time'] = resampled['timestamp'] + pd.Timedelta(milliseconds=ms - 1)
    
    return resampled


def generate_all_timeframes():
    """從 1m 資料生成所有需要的時間框架"""
    data_dir = Path("data/historical")
    source_file = data_dir / "BTCUSDT_1m.parquet"
    
    if not source_file.exists():
        print(f"❌ 找不到 1m 資料: {source_file}")
        return
    
    print("\n📊 從 1m K線重採樣生成其他時間框架\n")
    print("=" * 80)
    
    # 讀取 1m 資料
    print(f"\n📥 讀取 1m 資料...")
    df_1m = pd.read_parquet(source_file)
    print(f"   ✅ 已載入 {len(df_1m):,} 根K線")
    print(f"   時間範圍: {df_1m.iloc[0]['timestamp']} ~ {df_1m.iloc[-1]['timestamp']}")
    
    # 需要生成的時間框架
    intervals = ['3m', '5m', '8m', '10m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d', '3d', '1w']
    
    print(f"\n🔄 生成 {len(intervals)} 個時間框架...\n")
    
    for interval in intervals:
        try:
            print(f"   處理 {interval}...", end=" ")
            
            # 重採樣
            df_resampled = resample_klines(df_1m, interval)
            
            # 儲存
            output_file = data_dir / f"BTCUSDT_{interval}.parquet"
            df_resampled.to_parquet(output_file, index=False)
            
            # 統計
            file_size_mb = output_file.stat().st_size / 1024 / 1024
            
            print(f"✅ {len(df_resampled):>10,} rows, {file_size_mb:>6.2f} MB")
            
        except Exception as e:
            print(f"❌ 失敗: {e}")
            continue
    
    print("\n" + "=" * 80)
    print("✅ 重採樣完成！\n")
    
    # 顯示所有檔案
    print("📁 已生成檔案:")
    all_files = sorted(data_dir.glob("BTCUSDT_*.parquet"))
    total_size = 0
    
    for file in all_files:
        size_mb = file.stat().st_size / 1024 / 1024
        total_size += size_mb
        df = pd.read_parquet(file)
        print(f"   {file.name:25s}  {len(df):>10,} rows  {size_mb:>8.2f} MB")
    
    print(f"\n   總大小: {total_size:.2f} MB")
    print()


if __name__ == '__main__':
    try:
        generate_all_timeframes()
    except KeyboardInterrupt:
        print("\n\n⚠️  使用者中斷")
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
