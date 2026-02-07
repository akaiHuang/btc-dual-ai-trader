"""
歷史資料下載腳本
下載 5 年 BTC/USDT K線資料並儲存到本地

時間框架：1m, 3m, 15m, 1h, 1d, 1w
預估資料量：~50GB
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


class HistoricalDataDownloader:
    """歷史資料下載器"""
    
    # Binance K線資料限制
    MAX_KLINES_PER_REQUEST = 1000
    
    # 時間框架與毫秒對應
    INTERVAL_MS = {
        '1m': 60 * 1000,
        '3m': 3 * 60 * 1000,
        '5m': 5 * 60 * 1000,
        '15m': 15 * 60 * 1000,
        '30m': 30 * 60 * 1000,
        '1h': 60 * 60 * 1000,
        '2h': 2 * 60 * 60 * 1000,
        '4h': 4 * 60 * 60 * 1000,
        '6h': 6 * 60 * 60 * 1000,
        '12h': 12 * 60 * 60 * 1000,
        '1d': 24 * 60 * 60 * 1000,
        '1w': 7 * 24 * 60 * 60 * 1000,
    }
    
    def __init__(self, symbol: str = "BTCUSDT", data_dir: str = "data/historical", use_mainnet: bool = True):
        """
        初始化下載器
        
        Args:
            symbol: 交易對符號
            data_dir: 資料儲存目錄
            use_mainnet: 是否使用 Mainnet（True=正式環境，False=測試網）
        """
        self.client = BinanceClient()
        
        # 如果使用 Mainnet，直接覆蓋 API URL（無需 API Key 即可取得市場資料）
        if use_mainnet:
            from binance.client import Client
            self.client.client = Client(api_key="", api_secret="")  # 公開資料無需 API Key
            print(f"✅ 初始化下載器 (Mainnet - 正式環境)")
        else:
            print(f"✅ 初始化下載器 (Testnet - 測試網)")
        
        self.symbol = symbol
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"   交易對: {symbol}")
        print(f"   資料目錄: {self.data_dir.absolute()}")
        print()
    
    def calculate_required_requests(self, interval: str, years: int) -> int:
        """
        計算需要的請求次數
        
        Args:
            interval: 時間框架
            years: 年數
            
        Returns:
            請求次數
        """
        interval_ms = self.INTERVAL_MS[interval]
        total_ms = years * 365 * 24 * 60 * 60 * 1000
        total_klines = total_ms // interval_ms
        requests = (total_klines // self.MAX_KLINES_PER_REQUEST) + 1
        return requests
    
    def download_klines(
        self,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        save_batch_size: int = 10000
    ) -> pd.DataFrame:
        """
        下載指定時間範圍的 K線資料
        
        Args:
            interval: 時間框架
            start_time: 開始時間
            end_time: 結束時間
            save_batch_size: 批次儲存大小
            
        Returns:
            K線資料 DataFrame
        """
        print(f"📥 下載 {interval} K線資料")
        print(f"   時間範圍: {start_time.date()} ~ {end_time.date()}")
        
        # 計算預估請求次數
        days = (end_time - start_time).days
        estimated_requests = self.calculate_required_requests(interval, days / 365)
        print(f"   預估請求: ~{estimated_requests} 次")
        
        # 準備資料容器
        all_klines = []
        current_start = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)
        
        # 進度條
        pbar = tqdm(total=estimated_requests, desc=f"  {interval}", unit="req")
        
        batch_count = 0
        while current_start < end_ts:
            try:
                # 下載一批資料
                klines = self.client.get_klines(
                    symbol=self.symbol,
                    interval=interval,
                    start_time=current_start,
                    limit=self.MAX_KLINES_PER_REQUEST
                )
                
                if not klines:
                    break
                
                # 添加到列表
                all_klines.extend(klines)
                
                # 更新起始時間
                current_start = int(klines[-1][6]) + 1  # 最後一根K線的收盤時間 + 1ms
                
                # 更新進度條
                pbar.update(1)
                
                # 批次儲存（避免記憶體溢出）
                if len(all_klines) >= save_batch_size:
                    self._save_batch(all_klines, interval, batch_count)
                    batch_count += 1
                    all_klines = []
                
                # 避免觸發速率限制
                time.sleep(0.1)
                
            except Exception as e:
                print(f"\n❌ 下載錯誤: {e}")
                print(f"   當前時間戳: {current_start}")
                time.sleep(5)
                continue
        
        pbar.close()
        
        # 儲存剩餘資料
        if all_klines:
            self._save_batch(all_klines, interval, batch_count)
        
        # 合併所有批次
        df = self._merge_batches(interval, batch_count + 1)
        
        print(f"   ✅ 完成！總共 {len(df):,} 根K線")
        print(f"   時間範圍: {df.iloc[0]['timestamp']} ~ {df.iloc[-1]['timestamp']}")
        print()
        
        return df
    
    def _save_batch(self, klines: list, interval: str, batch_num: int):
        """儲存批次資料"""
        df = self._klines_to_dataframe(klines)
        batch_file = self.data_dir / f"{self.symbol}_{interval}_batch_{batch_num}.parquet"
        df.to_parquet(batch_file, index=False)
    
    def _merge_batches(self, interval: str, num_batches: int) -> pd.DataFrame:
        """合併所有批次並刪除臨時檔案"""
        dfs = []
        
        for i in range(num_batches):
            batch_file = self.data_dir / f"{self.symbol}_{interval}_batch_{i}.parquet"
            if batch_file.exists():
                dfs.append(pd.read_parquet(batch_file))
                batch_file.unlink()  # 刪除臨時檔案
        
        if not dfs:
            return pd.DataFrame()
        
        # 合併並去重
        df = pd.concat(dfs, ignore_index=True)
        df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
        
        # 儲存最終檔案
        final_file = self.data_dir / f"{self.symbol}_{interval}.parquet"
        df.to_parquet(final_file, index=False)
        print(f"   💾 已儲存: {final_file.name} ({len(df):,} rows, {final_file.stat().st_size / 1024 / 1024:.2f} MB)")
        
        return df
    
    def _klines_to_dataframe(self, klines: list) -> pd.DataFrame:
        """
        將 K線資料轉換為 DataFrame
        
        Args:
            klines: K線資料列表
            
        Returns:
            DataFrame
        """
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        
        # 轉換資料類型
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
        
        for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'taker_buy_base', 'taker_buy_quote']:
            df[col] = df[col].astype(float)
        
        df['trades'] = df['trades'].astype(int)
        
        # 刪除不需要的欄位
        df = df.drop(columns=['ignore'])
        
        return df
    
    def download_all_intervals(
        self,
        intervals: list = ['1m', '3m', '15m', '1h', '1d', '1w'],
        years: int = 5
    ):
        """
        下載所有時間框架的資料
        
        Args:
            intervals: 時間框架列表
            years: 下載年數
        """
        print("=" * 60)
        print("📊 BTC/USDT 歷史資料下載")
        print("=" * 60)
        print()
        
        end_time = datetime.now()
        start_time = end_time - timedelta(days=years * 365)
        
        print(f"時間範圍: {start_time.date()} ~ {end_time.date()}")
        print(f"時間框架: {', '.join(intervals)}")
        print(f"交易對: {self.symbol}")
        print()
        
        # 統計資訊
        stats = {
            'intervals': {},
            'total_rows': 0,
            'total_size_mb': 0,
            'start_time': datetime.now().isoformat(),
        }
        
        # 逐個下載
        for interval in intervals:
            try:
                df = self.download_klines(interval, start_time, end_time)
                
                file_path = self.data_dir / f"{self.symbol}_{interval}.parquet"
                file_size_mb = file_path.stat().st_size / 1024 / 1024
                
                stats['intervals'][interval] = {
                    'rows': len(df),
                    'size_mb': round(file_size_mb, 2),
                    'start': df.iloc[0]['timestamp'].isoformat(),
                    'end': df.iloc[-1]['timestamp'].isoformat(),
                }
                
                stats['total_rows'] += len(df)
                stats['total_size_mb'] += file_size_mb
                
            except Exception as e:
                print(f"❌ {interval} 下載失敗: {e}")
                continue
        
        stats['end_time'] = datetime.now().isoformat()
        stats['duration_minutes'] = round(
            (datetime.fromisoformat(stats['end_time']) - 
             datetime.fromisoformat(stats['start_time'])).total_seconds() / 60,
            2
        )
        
        # 儲存統計資訊
        stats_file = self.data_dir / 'download_stats.json'
        with open(stats_file, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        
        # 顯示總結
        print("=" * 60)
        print("✅ 下載完成！")
        print("=" * 60)
        print()
        print("📊 統計資訊:")
        print(f"   總K線數: {stats['total_rows']:,} rows")
        print(f"   總大小: {stats['total_size_mb']:.2f} MB")
        print(f"   耗時: {stats['duration_minutes']:.2f} 分鐘")
        print()
        print("📁 各時間框架:")
        for interval, info in stats['intervals'].items():
            print(f"   {interval:>4s}: {info['rows']:>10,} rows, {info['size_mb']:>8.2f} MB")
        print()
        print(f"💾 資料目錄: {self.data_dir.absolute()}")
        print(f"📄 統計檔案: {stats_file.absolute()}")
        print()


def main():
    """主函數"""
    print("\n🚀 Task 1.3: 歷史資料收集\n")
    
    # 創建下載器（使用 Mainnet 獲取完整歷史資料）
    downloader = HistoricalDataDownloader(
        symbol="BTCUSDT",
        data_dir="data/historical",
        use_mainnet=True  # 使用正式環境獲取完整資料
    )
    
    # 下載資料（5年）
    intervals = ['1m', '3m', '15m', '1h', '1d', '1w']
    downloader.download_all_intervals(intervals=intervals, years=5)
    
    print("🎯 Task 1.3 完成！")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  使用者中斷下載")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
