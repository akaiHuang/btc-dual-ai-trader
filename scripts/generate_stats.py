"""
生成完整的資料統計報告
"""

from pathlib import Path
import pandas as pd
import json
from datetime import datetime


def generate_stats():
    """生成統計資訊"""
    data_dir = Path("data/historical")
    symbol = "BTCUSDT"
    intervals = ['1m', '3m', '15m', '1h', '1d', '1w']
    
    print("\n📊 資料統計報告\n")
    print("=" * 80)
    
    stats = {
        'symbol': symbol,
        'download_date': datetime.now().isoformat(),
        'intervals': {},
        'total_rows': 0,
        'total_size_mb': 0,
    }
    
    for interval in intervals:
        file_path = data_dir / f"{symbol}_{interval}.parquet"
        
        if not file_path.exists():
            print(f"❌ {interval}: 檔案不存在")
            continue
        
        # 讀取資料
        df = pd.read_parquet(file_path)
        file_size_mb = file_path.stat().st_size / 1024 / 1024
        
        # 時間範圍
        start_time = df.iloc[0]['timestamp']
        end_time = df.iloc[-1]['timestamp']
        days = (end_time - start_time).days
        
        stats['intervals'][interval] = {
            'rows': len(df),
            'size_mb': round(file_size_mb, 2),
            'start': start_time.isoformat(),
            'end': end_time.isoformat(),
            'days': days,
        }
        
        stats['total_rows'] += len(df)
        stats['total_size_mb'] += file_size_mb
        
        print(f"✅ {interval:>4s}: {len(df):>10,} rows, {file_size_mb:>8.2f} MB, {days:>4} 天")
        print(f"         {start_time.date()} ~ {end_time.date()}")
        print()
    
    stats['total_size_mb'] = round(stats['total_size_mb'], 2)
    
    print("=" * 80)
    print(f"\n總計: {stats['total_rows']:,} 根K線, {stats['total_size_mb']:.2f} MB\n")
    
    # 儲存
    stats_file = data_dir / 'download_stats.json'
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    
    print(f"💾 已儲存: {stats_file}")
    
    return stats


if __name__ == '__main__':
    generate_stats()
