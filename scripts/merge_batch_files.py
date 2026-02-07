"""
合併批次檔案並完成下載
"""

import sys
from pathlib import Path
import pandas as pd
import json
from datetime import datetime

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def merge_batches_for_interval(data_dir: Path, symbol: str, interval: str):
    """合併特定時間框架的批次檔案"""
    batch_files = sorted(data_dir.glob(f"{symbol}_{interval}_batch_*.parquet"))
    
    if not batch_files:
        print(f"❌ 沒有找到 {interval} 的批次檔案")
        return None
    
    print(f"\n📦 合併 {interval} 批次檔案: {len(batch_files)} 個")
    
    dfs = []
    for batch_file in batch_files:
        try:
            df = pd.read_parquet(batch_file)
            dfs.append(df)
        except Exception as e:
            print(f"   ⚠️  讀取失敗: {batch_file.name} - {e}")
            continue
    
    if not dfs:
        return None
    
    # 合併並去重
    print(f"   合併 {len(dfs)} 個批次...")
    df = pd.concat(dfs, ignore_index=True)
    
    print(f"   去重...")
    before_dedup = len(df)
    df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
    after_dedup = len(df)
    
    if before_dedup != after_dedup:
        print(f"   去除 {before_dedup - after_dedup:,} 筆重複資料")
    
    # 儲存最終檔案
    final_file = data_dir / f"{symbol}_{interval}.parquet"
    df.to_parquet(final_file, index=False)
    file_size_mb = final_file.stat().st_size / 1024 / 1024
    
    print(f"   ✅ 已儲存: {final_file.name}")
    print(f"      K線數: {len(df):,}")
    print(f"      大小: {file_size_mb:.2f} MB")
    print(f"      時間範圍: {df.iloc[0]['timestamp']} ~ {df.iloc[-1]['timestamp']}")
    
    # 刪除批次檔案
    print(f"   🗑️  刪除 {len(batch_files)} 個批次檔案...")
    for batch_file in batch_files:
        batch_file.unlink()
    
    return {
        'rows': len(df),
        'size_mb': round(file_size_mb, 2),
        'start': df.iloc[0]['timestamp'].isoformat(),
        'end': df.iloc[-1]['timestamp'].isoformat(),
    }


def main():
    """主函數"""
    print("\n🔄 合併批次檔案\n")
    print("=" * 60)
    
    data_dir = Path("data/historical")
    symbol = "BTCUSDT"
    
    # 檢測有哪些時間框架的批次檔案
    all_batch_files = list(data_dir.glob(f"{symbol}_*_batch_*.parquet"))
    
    if not all_batch_files:
        print("❌ 沒有找到批次檔案")
        return
    
    # 提取時間框架
    intervals = set()
    for file in all_batch_files:
        # BTCUSDT_1m_batch_0.parquet -> 1m
        parts = file.stem.split('_')
        if len(parts) >= 3:
            intervals.add(parts[1])
    
    intervals = sorted(intervals)
    print(f"找到時間框架: {', '.join(intervals)}\n")
    
    # 統計資訊
    stats = {
        'intervals': {},
        'total_rows': 0,
        'total_size_mb': 0,
        'start_time': datetime.now().isoformat(),
    }
    
    # 逐個合併
    for interval in intervals:
        try:
            result = merge_batches_for_interval(data_dir, symbol, interval)
            if result:
                stats['intervals'][interval] = result
                stats['total_rows'] += result['rows']
                stats['total_size_mb'] += result['size_mb']
        except Exception as e:
            print(f"❌ {interval} 合併失敗: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    stats['end_time'] = datetime.now().isoformat()
    stats['duration_minutes'] = round(
        (datetime.fromisoformat(stats['end_time']) - 
         datetime.fromisoformat(stats['start_time'])).total_seconds() / 60,
        2
    )
    
    # 儲存統計資訊
    stats_file = data_dir / 'download_stats.json'
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    
    # 顯示總結
    print("\n" + "=" * 60)
    print("✅ 合併完成！")
    print("=" * 60)
    print()
    print("📊 統計資訊:")
    print(f"   總K線數: {stats['total_rows']:,} rows")
    print(f"   總大小: {stats['total_size_mb']:.2f} MB")
    print(f"   耗時: {stats['duration_minutes']:.2f} 分鐘")
    print()
    print("📁 各時間框架:")
    for interval, info in stats['intervals'].items():
        days = (pd.to_datetime(info['end']) - pd.to_datetime(info['start'])).days
        print(f"   {interval:>4s}: {info['rows']:>10,} rows, {info['size_mb']:>8.2f} MB, {days:>4} 天")
    print()
    print(f"💾 資料目錄: {data_dir.absolute()}")
    print(f"📄 統計檔案: {stats_file.absolute()}")
    print()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  使用者中斷")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
