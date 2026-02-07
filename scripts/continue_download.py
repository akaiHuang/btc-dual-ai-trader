"""
繼續下載剩餘的時間框架
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import time
import pandas as pd
from tqdm import tqdm
import json

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.exchange.binance_client import BinanceClient


class DataDownloader:
    """簡化的資料下載器"""
    
    MAX_KLINES_PER_REQUEST = 1000
    
    def __init__(self):
        self.client = BinanceClient()
        # 使用 Mainnet
        from binance.client import Client
        self.client.client = Client(api_key="", api_secret="")
        self.data_dir = Path("data/historical")
        self.data_dir.mkdir(parents=True, exist_ok=True)
    
    def download_interval(self, interval: str, years: int = 5):
        """下載單一時間框架"""
        print(f"\n📥 下載 {interval} K線資料")
        
        end_time = datetime.now()
        start_time = end_time - timedelta(days=years * 365)
        
        symbol = "BTCUSDT"
        all_klines = []
        current_start = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)
        
        # 預估請求次數
        estimated_requests = 2629 if interval == '1m' else 1000
        pbar = tqdm(total=estimated_requests, desc=f"  {interval}", unit="req")
        
        while current_start < end_ts:
            try:
                klines = self.client.get_klines(
                    symbol=symbol,
                    interval=interval,
                    start_time=current_start,
                    limit=self.MAX_KLINES_PER_REQUEST
                )
                
                if not klines:
                    break
                
                all_klines.extend(klines)
                current_start = int(klines[-1][6]) + 1
                pbar.update(1)
                
                time.sleep(0.05)  # 避免速率限制
                
            except Exception as e:
                print(f"\n❌ 錯誤: {e}")
                time.sleep(5)
                continue
        
        pbar.close()
        
        # 轉換為 DataFrame
        df = pd.DataFrame(all_klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
        
        for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'taker_buy_base', 'taker_buy_quote']:
            df[col] = df[col].astype(float)
        
        df['trades'] = df[col].astype(int)
        df = df.drop(columns=['ignore'])
        
        # 去重排序
        df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
        
        # 儲存
        file_path = self.data_dir / f"{symbol}_{interval}.parquet"
        df.to_parquet(file_path, index=False)
        
        file_size_mb = file_path.stat().st_size / 1024 / 1024
        
        print(f"   ✅ 完成！{len(df):,} rows, {file_size_mb:.2f} MB")
        print(f"   時間範圍: {df.iloc[0]['timestamp']} ~ {df.iloc[-1]['timestamp']}")
        
        return {
            'rows': len(df),
            'size_mb': round(file_size_mb, 2),
            'start': df.iloc[0]['timestamp'].isoformat(),
            'end': df.iloc[-1]['timestamp'].isoformat(),
        }


def main():
    """主函數"""
    print("\n🚀 繼續下載剩餘時間框架\n")
    
    downloader = DataDownloader()
    
    # 檢查已完成的
    completed = []
    for interval in ['1m', '3m', '15m', '1h', '1d', '1w']:
        file_path = downloader.data_dir / f"BTCUSDT_{interval}.parquet"
        if file_path.exists():
            completed.append(interval)
    
    print(f"已完成: {', '.join(completed) if completed else '無'}")
    
    # 剩餘的
    remaining = ['3m', '15m', '1h', '1d', '1w']
    remaining = [i for i in remaining if i not in completed]
    
    if not remaining:
        print("✅ 所有時間框架已下載完成！")
        return
    
    print(f"待下載: {', '.join(remaining)}\n")
    
    # 統計資訊
    stats = {'intervals': {}, 'total_rows': 0, 'total_size_mb': 0}
    
    # 加載已有的統計
    stats_file = downloader.data_dir / 'download_stats.json'
    if stats_file.exists():
        with open(stats_file, 'r') as f:
            stats = json.load(f)
    
    stats['start_time'] = datetime.now().isoformat()
    
    # 下載
    for interval in remaining:
        try:
            result = downloader.download_interval(interval, years=5)
            stats['intervals'][interval] = result
            stats['total_rows'] += result['rows']
            stats['total_size_mb'] += result['size_mb']
        except Exception as e:
            print(f"❌ {interval} 下載失敗: {e}")
            import traceback
            traceback.print_exc()
    
    stats['end_time'] = datetime.now().isoformat()
    
    # 儲存統計
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    
    print("\n✅ 下載完成！")
    print(f"總K線數: {stats['total_rows']:,}")
    print(f"總大小: {stats['total_size_mb']:.2f} MB")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️  使用者中斷")
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
