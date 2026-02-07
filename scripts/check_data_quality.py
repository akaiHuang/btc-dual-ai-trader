"""
檢查下載資料的時間範圍和品質
"""

from pathlib import Path
import pandas as pd
from datetime import datetime


def check_data_quality():
    """檢查資料品質"""
    data_dir = Path("data/historical")
    parquet_files = sorted(data_dir.glob("BTCUSDT_*.parquet"))
    
    if not parquet_files:
        print("❌ 沒有找到資料檔案")
        return
    
    print("\n📊 資料品質檢查\n")
    print("=" * 80)
    
    for file in parquet_files:
        interval = file.stem.split('_')[1]
        
        # 讀取資料
        df = pd.read_parquet(file)
        
        # 時間範圍
        start_time = df.iloc[0]['timestamp']
        end_time = df.iloc[-1]['timestamp']
        
        # 計算天數
        days = (end_time - start_time).days
        
        # 檢查缺失
        expected_rows = {
            '1m': days * 24 * 60,
            '3m': days * 24 * 20,
            '5m': days * 24 * 12,
            '15m': days * 24 * 4,
            '30m': days * 24 * 2,
            '1h': days * 24,
            '4h': days * 6,
            '1d': days,
            '1w': days // 7,
        }
        
        expected = expected_rows.get(interval, 0)
        actual = len(df)
        completeness = (actual / expected * 100) if expected > 0 else 0
        
        print(f"\n{interval} K線:")
        print(f"   時間範圍: {start_time} ~ {end_time}")
        print(f"   天數: {days} 天")
        print(f"   K線數: {actual:,} (預期: {expected:,}, 完整度: {completeness:.1f}%)")
        
        # 檢查價格範圍
        print(f"   價格範圍: ${df['low'].min():,.2f} ~ ${df['high'].max():,.2f}")
        
        # 檢查成交量
        print(f"   成交量範圍: {df['volume'].min():.4f} ~ {df['volume'].max():.4f}")
        
        # 檢查缺失值
        missing = df.isnull().sum().sum()
        print(f"   缺失值: {missing}")
        
        # 檢查重複
        duplicates = df.duplicated(subset=['timestamp']).sum()
        print(f"   重複值: {duplicates}")
    
    print("\n" + "=" * 80)
    print("\n💡 建議:")
    
    if days < 365:
        print(f"   ⚠️  實際資料只有 {days} 天，遠少於 5 年（1825 天）")
        print(f"   ⚠️  Binance Testnet 可能只提供有限的歷史資料")
        print(f"   💡 建議切換到 Mainnet (生產環境) 以獲取完整的 5 年資料")
    else:
        print("   ✅ 資料時間範圍符合預期")
    
    print()


if __name__ == '__main__':
    check_data_quality()
