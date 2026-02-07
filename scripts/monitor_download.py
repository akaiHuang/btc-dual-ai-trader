"""
實時監控下載進度
"""

import time
from pathlib import Path


def monitor_download():
    """監控下載進度"""
    log_file = Path("data/download.log")
    
    if not log_file.exists():
        print("❌ 日誌檔案不存在")
        return
    
    print("\n📊 實時監控下載進度")
    print("=" * 60)
    print("按 Ctrl+C 停止監控\n")
    
    last_size = 0
    
    try:
        while True:
            # 讀取日誌最後幾行
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # 顯示最後 10 行
            print("\033[2J\033[H")  # 清除螢幕
            print("\n📊 實時監控下載進度 (每 5 秒更新)")
            print("=" * 60)
            
            for line in lines[-10:]:
                print(line.rstrip())
            
            print("\n" + "=" * 60)
            
            # 顯示日誌檔案大小
            current_size = log_file.stat().st_size / 1024
            print(f"日誌大小: {current_size:.2f} KB (增長: +{current_size - last_size:.2f} KB)")
            last_size = current_size
            
            # 檢查資料檔案
            data_dir = Path("data/historical")
            if data_dir.exists():
                parquet_files = list(data_dir.glob("*.parquet"))
                batch_files = list(data_dir.glob("*_batch_*.parquet"))
                
                print(f"已完成檔案: {len(parquet_files)}")
                print(f"批次檔案: {len(batch_files)}")
                
                if parquet_files:
                    total_size = sum(f.stat().st_size for f in parquet_files) / 1024 / 1024
                    print(f"資料大小: {total_size:.2f} MB")
            
            print("\n按 Ctrl+C 停止監控")
            
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("\n\n✅ 停止監控")


if __name__ == '__main__':
    monitor_download()
