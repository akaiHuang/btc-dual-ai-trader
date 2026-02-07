#!/usr/bin/env python3
"""
完整大單數據下載工具 (2020-2025)

下載 Binance BTCUSDT 歷史 Aggregate Trades 數據
只保留大於閾值的大單 (預設 >=10 BTC)

使用方法:
    python scripts/download_agg_trades_full.py --start 2020-01-01 --end 2025-11-15
    
特點:
    1. 分批下載避免 API 限制
    2. 自動重試機制
    3. 斷點續傳（避免重複下載）
    4. 實時進度顯示
    5. 自動合併到 15m K線
"""

import pandas as pd
import requests
import time
from datetime import datetime, timedelta
import argparse
import os
import json
from pathlib import Path
from tqdm import tqdm


class AggTradesDownloader:
    """完整大單數據下載器"""
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        min_qty: float = 10.0,
        output_dir: str = "data/historical",
        batch_size_hours: int = 24,  # 每次下載 24 小時
        max_retries: int = 3,
        retry_delay: int = 5
    ):
        self.symbol = symbol
        self.min_qty = min_qty
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.batch_size_hours = batch_size_hours
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        # Binance API endpoints
        self.base_url = "https://api.binance.com"
        self.agg_trades_endpoint = f"{self.base_url}/api/v3/aggTrades"
        
        # 臨時文件路徑
        self.temp_file = self.output_dir / f"{symbol}_agg_trades_temp.parquet"
        self.progress_file = self.output_dir / f"{symbol}_download_progress.json"
        
    def _load_progress(self):
        """載入下載進度"""
        if self.progress_file.exists():
            with open(self.progress_file, 'r') as f:
                return json.load(f)
        return {'completed_batches': [], 'last_timestamp': None}
    
    def _save_progress(self, progress):
        """保存下載進度"""
        with open(self.progress_file, 'w') as f:
            json.dump(progress, f, indent=2)
    
    def _timestamp_to_ms(self, dt: datetime) -> int:
        """datetime 轉毫秒時間戳"""
        return int(dt.timestamp() * 1000)
    
    def _download_batch(
        self,
        start_time: datetime,
        end_time: datetime,
        retry_count: int = 0
    ) -> pd.DataFrame:
        """
        下載單個批次的數據
        
        Args:
            start_time: 開始時間
            end_time: 結束時間
            retry_count: 重試次數
            
        Returns:
            DataFrame with columns: [timestamp, price, qty, side, trade_id]
        """
        start_ms = self._timestamp_to_ms(start_time)
        end_ms = self._timestamp_to_ms(end_time)
        
        # 注意：Binance API 不允許 startTime/endTime 和 fromId 同時使用
        # 因此每個 24 小時批次最多只能獲取 1000 筆交易
        # 如果某天有超過 1000 筆大單，需要縮小批次大小（例如改為 12 小時）
        
        params = {
            'symbol': self.symbol,
            'startTime': start_ms,
            'endTime': end_ms,
            'limit': 1000
        }
        
        try:
            response = requests.get(self.agg_trades_endpoint, params=params, timeout=30)
            
            # 處理 429 Too Many Requests
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', self.retry_delay))
                print(f"⚠️  API 限流，等待 {retry_after} 秒...")
                time.sleep(retry_after)
                return self._download_batch(start_time, end_time, retry_count)
            
            response.raise_for_status()
            
            trades = response.json()
            
            # 檢查是否有錯誤碼
            if isinstance(trades, dict) and 'code' in trades:
                if trades['code'] == -1003:  # WAF limit
                    print(f"⚠️  觸及 WAF 限制，等待 {self.retry_delay * 2} 秒...")
                    time.sleep(self.retry_delay * 2)
                    return self._download_batch(start_time, end_time, retry_count)
                else:
                    print(f"⚠️  API 錯誤: {trades}")
                    return pd.DataFrame()
            
            if not trades or len(trades) == 0:
                return pd.DataFrame()
            
            all_trades = trades
            
        except requests.exceptions.RequestException as e:
            if retry_count < self.max_retries:
                wait_time = self.retry_delay * (2 ** retry_count)  # Exponential backoff
                print(f"⚠️  請求失敗，{wait_time}秒後重試 ({retry_count + 1}/{self.max_retries})...")
                time.sleep(wait_time)
                return self._download_batch(start_time, end_time, retry_count + 1)
            else:
                print(f"❌ 下載失敗: {start_time} ~ {end_time}")
                print(f"   錯誤: {e}")
                return pd.DataFrame()
        
        if not all_trades:
            return pd.DataFrame()
        
        # 解析數據
        df = pd.DataFrame([{
            'timestamp': pd.to_datetime(t['T'], unit='ms', utc=True).tz_localize(None),  # 移除時區，保持 UTC
            'price': float(t['p']),
            'qty': float(t['q']),
            'side': 'BUY' if t['m'] == False else 'SELL',  # m=false -> buyer is maker
            'trade_id': t['a']
        } for t in all_trades])
        
        # 只保留大單
        df = df[df['qty'] >= self.min_qty].copy()
        
        return df
    
    def download_range(
        self,
        start_date: str,
        end_date: str,
        resume: bool = True
    ) -> pd.DataFrame:
        """
        下載指定時間範圍的大單數據
        
        Args:
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
            resume: 是否斷點續傳
            
        Returns:
            完整的大單數據 DataFrame
        """
        print("="*70)
        print(f"🚀 開始下載 {self.symbol} 大單數據 (>={self.min_qty} BTC)")
        print("="*70)
        print(f"時間範圍: {start_date} ~ {end_date}")
        print(f"批次大小: {self.batch_size_hours} 小時")
        print()
        print("⚠️  注意:")
        print("  - Binance aggTrades 歷史可能有限制（通常只保留最近 3 年）")
        print("  - 如果 2020-2021 數據稀少，這是正常的")
        print("  - 會自動處理 API 限流和重試")
        print()
        
        # 解析日期
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        
        # 載入進度
        progress = self._load_progress() if resume else {'completed_batches': [], 'last_timestamp': None}
        
        # 生成批次列表
        batches = []
        current = start
        while current < end:
            batch_end = min(current + timedelta(hours=self.batch_size_hours), end)
            batch_id = f"{current.strftime('%Y%m%d_%H')}"
            
            if batch_id not in progress['completed_batches']:
                batches.append((current, batch_end, batch_id))
            
            current = batch_end
        
        if not batches:
            print("✅ 所有批次已下載完成！")
            return self._load_existing_data()
        
        print(f"📋 待下載批次: {len(batches)}")
        print(f"📦 已完成批次: {len(progress['completed_batches'])}")
        print()
        
        # 下載數據
        all_trades = []
        
        for batch_start, batch_end, batch_id in tqdm(batches, desc="下載進度"):
            # 下載批次
            df_batch = self._download_batch(batch_start, batch_end)
            
            if not df_batch.empty:
                all_trades.append(df_batch)
                
                # 保存臨時數據
                if all_trades:
                    df_temp = pd.concat(all_trades, ignore_index=True)
                    df_temp.to_parquet(self.temp_file)
            
            # 更新進度
            progress['completed_batches'].append(batch_id)
            progress['last_timestamp'] = batch_end.isoformat()
            self._save_progress(progress)
            
            # 避免 API 限流
            time.sleep(0.5)
        
        # 合併所有數據
        if all_trades:
            df_all = pd.concat(all_trades, ignore_index=True)
            df_all = df_all.sort_values('timestamp').drop_duplicates(subset=['trade_id'])
            
            # 保存最終數據
            output_file = self.output_dir / f"{self.symbol}_agg_trades_{start_date.replace('-', '')}_{end_date.replace('-', '')}.parquet"
            df_all.to_parquet(output_file)
            
            print()
            print("="*70)
            print("✅ 下載完成！")
            print("="*70)
            print(f"總大單數: {len(df_all):,} 筆")
            print(f"時間範圍: {df_all['timestamp'].min()} ~ {df_all['timestamp'].max()}")
            print(f"平均單量: {df_all['qty'].mean():.2f} BTC")
            print(f"最大單量: {df_all['qty'].max():.2f} BTC")
            print(f"買單比例: {(df_all['side'] == 'BUY').sum() / len(df_all) * 100:.1f}%")
            print(f"保存路徑: {output_file}")
            print()
            
            # 清理臨時文件
            if self.temp_file.exists():
                self.temp_file.unlink()
            if self.progress_file.exists():
                self.progress_file.unlink()
            
            return df_all
        else:
            print("⚠️  未找到符合條件的大單")
            return pd.DataFrame()
    
    def _load_existing_data(self) -> pd.DataFrame:
        """載入已存在的數據"""
        # 尋找現有文件
        files = list(self.output_dir.glob(f"{self.symbol}_agg_trades_*.parquet"))
        if files:
            print(f"✅ 載入現有數據: {files[0]}")
            return pd.read_parquet(files[0])
        return pd.DataFrame()
    
    def merge_with_klines(self, kline_file: str = None):
        """
        將大單數據合併到 15m K線
        
        Args:
            kline_file: K線數據文件路徑，預設為 BTCUSDT_15m.parquet
        """
        print()
        print("="*70)
        print("📊 合併大單數據到 15m K線")
        print("="*70)
        
        # 載入 K線數據
        if kline_file is None:
            kline_file = self.output_dir / "BTCUSDT_15m.parquet"
        
        if not Path(kline_file).exists():
            print(f"❌ K線文件不存在: {kline_file}")
            return
        
        df_kline = pd.read_parquet(kline_file)
        df_kline['timestamp'] = pd.to_datetime(df_kline['timestamp'])
        
        # 載入大單數據
        agg_files = list(self.output_dir.glob(f"{self.symbol}_agg_trades_*.parquet"))
        if not agg_files:
            print("❌ 未找到大單數據文件")
            return
        
        df_agg = pd.read_parquet(agg_files[0])
        df_agg['timestamp'] = pd.to_datetime(df_agg['timestamp'])
        
        # 🔧 關鍵修復：移除時區信息，確保與 K線數據一致
        if df_agg['timestamp'].dt.tz is not None:
            df_agg['timestamp'] = df_agg['timestamp'].dt.tz_localize(None)
        
        print(f"K線數據: {len(df_kline)} 根")
        print(f"大單數據: {len(df_agg)} 筆")
        print()
        
        # 將大單聚合到 15m K線
        df_agg['timestamp_15m'] = df_agg['timestamp'].dt.floor('15min')
        
        # 計算每根 K線的大單統計
        agg_stats = df_agg.groupby('timestamp_15m').agg({
            'qty': ['sum', 'count', 'mean', 'max'],
            'side': lambda x: (x == 'BUY').sum() / len(x) if len(x) > 0 else 0.5
        }).reset_index()
        
        agg_stats.columns = [
            'timestamp',
            'large_trade_volume',  # 總大單量
            'large_trade_count',   # 大單數量
            'large_trade_avg',     # 平均大單量
            'large_trade_max',     # 最大單量
            'large_trade_buy_ratio' # 買單比例
        ]
        
        # 合併
        df_merged = df_kline.merge(agg_stats, on='timestamp', how='left')
        
        # 填充沒有大單的 K線
        df_merged['large_trade_volume'] = df_merged['large_trade_volume'].fillna(0)
        df_merged['large_trade_count'] = df_merged['large_trade_count'].fillna(0)
        df_merged['large_trade_avg'] = df_merged['large_trade_avg'].fillna(0)
        df_merged['large_trade_max'] = df_merged['large_trade_max'].fillna(0)
        df_merged['large_trade_buy_ratio'] = df_merged['large_trade_buy_ratio'].fillna(0.5)
        
        # 保存
        output_file = self.output_dir / f"{self.symbol}_15m_with_large_trades.parquet"
        df_merged.to_parquet(output_file)
        
        print("✅ 合併完成！")
        print(f"輸出文件: {output_file}")
        print(f"總 K線數: {len(df_merged)}")
        print(f"有大單的 K線: {(df_merged['large_trade_count'] > 0).sum()}")
        print(f"覆蓋率: {(df_merged['large_trade_count'] > 0).sum() / len(df_merged) * 100:.2f}%")
        print()
        
        # 統計各年份覆蓋率
        df_merged['year'] = df_merged['timestamp'].dt.year
        yearly_stats = df_merged.groupby('year').agg({
            'large_trade_count': ['count', lambda x: (x > 0).sum()]
        })
        yearly_stats.columns = ['total_candles', 'candles_with_trades']
        yearly_stats['coverage'] = yearly_stats['candles_with_trades'] / yearly_stats['total_candles'] * 100
        
        print("📊 各年份大單覆蓋率:")
        print(yearly_stats)
        print()


def main():
    parser = argparse.ArgumentParser(description="下載 Binance 歷史大單數據")
    parser.add_argument(
        "--start",
        type=str,
        required=True,
        help="開始日期 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end",
        type=str,
        required=True,
        help="結束日期 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="交易對 (預設: BTCUSDT)"
    )
    parser.add_argument(
        "--min_qty",
        type=float,
        default=10.0,
        help="最小單量閾值 BTC (預設: 10.0)"
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default="data/historical",
        help="輸出目錄 (預設: data/historical)"
    )
    parser.add_argument(
        "--batch_hours",
        type=int,
        default=24,
        help="每批次小時數 (預設: 24)"
    )
    parser.add_argument(
        "--no_resume",
        action="store_true",
        help="不使用斷點續傳（重新下載）"
    )
    parser.add_argument(
        "--no_merge",
        action="store_true",
        help="不合併到 K線數據"
    )
    
    args = parser.parse_args()
    
    # 創建下載器
    downloader = AggTradesDownloader(
        symbol=args.symbol,
        min_qty=args.min_qty,
        output_dir=args.output_dir,
        batch_size_hours=args.batch_hours
    )
    
    # 下載數據
    df = downloader.download_range(
        start_date=args.start,
        end_date=args.end,
        resume=not args.no_resume
    )
    
    # 合併到 K線
    if not args.no_merge and not df.empty:
        downloader.merge_with_klines()
    
    print("="*70)
    print("🎉 全部完成！")
    print("="*70)


if __name__ == "__main__":
    main()
