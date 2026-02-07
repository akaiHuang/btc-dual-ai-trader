"""
檢查歷史資料下載進度
"""

from pathlib import Path
import json


def check_progress():
    """檢查下載進度"""
    data_dir = Path("data/historical")
    
    if not data_dir.exists():
        print("❌ 資料目錄不存在")
        return
    
    print("\n📊 下載進度檢查\n")
    print("=" * 60)
    
    # 檢查 parquet 檔案
    parquet_files = sorted(data_dir.glob("*.parquet"))
    
    if not parquet_files:
        print("⏳ 尚未完成任何檔案下載")
    else:
        print(f"\n✅ 已完成檔案: {len(parquet_files)}\n")
        
        total_size = 0
        for file in parquet_files:
            size_mb = file.stat().st_size / 1024 / 1024
            total_size += size_mb
            print(f"   {file.name:30s}  {size_mb:>8.2f} MB")
        
        print(f"\n   總大小: {total_size:.2f} MB")
    
    # 檢查批次檔案（下載中）
    batch_files = sorted(data_dir.glob("*_batch_*.parquet"))
    
    if batch_files:
        print(f"\n⏳ 下載中 (批次檔案): {len(batch_files)}")
        for file in batch_files[:5]:  # 只顯示前5個
            print(f"   {file.name}")
        if len(batch_files) > 5:
            print(f"   ... 還有 {len(batch_files) - 5} 個批次檔案")
    
    # 檢查統計檔案
    stats_file = data_dir / "download_stats.json"
    if stats_file.exists():
        print(f"\n📄 統計資訊:")
        with open(stats_file, 'r', encoding='utf-8') as f:
            stats = json.load(f)
        
        print(f"   總K線數: {stats['total_rows']:,} rows")
        print(f"   總大小: {stats['total_size_mb']:.2f} MB")
        print(f"   耗時: {stats['duration_minutes']:.2f} 分鐘")
        
        print(f"\n   各時間框架:")
        for interval, info in stats['intervals'].items():
            print(f"      {interval:>4s}: {info['rows']:>10,} rows, {info['size_mb']:>8.2f} MB")
    
    print("\n" + "=" * 60 + "\n")


if __name__ == '__main__':
    check_progress()
