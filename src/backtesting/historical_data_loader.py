"""
歷史數據加載器

負責從 parquet 文件加載歷史 K線數據，並生成模擬的訂單簿和交易數據
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class HistoricalDataLoader:
    """
    歷史數據加載器
    
    從 parquet 文件加載歷史數據，並生成模擬的市場事件流
    """
    
    def __init__(self, data_dir: str = "data/historical"):
        """
        初始化數據加載器
        
        Args:
            data_dir: 歷史數據目錄
        """
        self.data_dir = Path(data_dir)
        self.klines_df: Optional[pd.DataFrame] = None
        
        logger.info(f"初始化 HistoricalDataLoader，數據目錄: {self.data_dir}")
    
    def load_klines(
        self,
        symbol: str,
        interval: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """
        加載 K線數據
        
        Args:
            symbol: 交易對（例如：BTCUSDT）
            interval: 時間間隔（1m, 3m, 5m, 15m, 1h, 1d 等）
            start_date: 開始日期（YYYY-MM-DD）
            end_date: 結束日期（YYYY-MM-DD）
        
        Returns:
            K線數據 DataFrame
        """
        # 如果指定了日期，嘗試使用年度文件
        if start_date and interval == "1m":
            start_year = pd.to_datetime(start_date).year
            year_file = self.data_dir / f"{symbol}_{interval}_{start_year}.parquet"
            
            if year_file.exists():
                logger.info(f"加載年度 K線數據: {year_file}")
                df = pd.read_parquet(year_file)
            else:
                # 回退到主文件
                file_path = self.data_dir / f"{symbol}_{interval}.parquet"
                if not file_path.exists():
                    raise FileNotFoundError(f"找不到數據文件: {file_path} 或 {year_file}")
                logger.info(f"加載 K線數據: {file_path}")
                df = pd.read_parquet(file_path)
        else:
            # 構建文件路徑
            file_path = self.data_dir / f"{symbol}_{interval}.parquet"
            
            if not file_path.exists():
                raise FileNotFoundError(f"找不到數據文件: {file_path}")
            
            logger.info(f"加載 K線數據: {file_path}")
            df = pd.read_parquet(file_path)
        
        # 確保 timestamp 是 datetime 類型
        if 'timestamp' in df.columns and not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # 篩選日期範圍
        if start_date:
            start_dt = pd.to_datetime(start_date)
            df = df[df['timestamp'] >= start_dt]
        
        if end_date:
            end_dt = pd.to_datetime(end_date)
            # 如果 end_date 只有日期沒有時間，延長到當天結束
            if end_dt.hour == 0 and end_dt.minute == 0 and end_dt.second == 0:
                end_dt = end_dt + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            df = df[df['timestamp'] <= end_dt]
        
        # 按時間排序
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        self.klines_df = df
        
        logger.info(f"加載完成: {len(df)} 條 K線數據")
        logger.info(f"時間範圍: {df['timestamp'].min()} 到 {df['timestamp'].max()}")
        
        return df
    
    def generate_orderbook_snapshots(
        self,
        kline_row: pd.Series,
        num_levels: int = 20
    ) -> Dict:
        """
        根據 K線數據生成模擬訂單簿快照
        
        策略：
        1. 使用 close 價格作為中間價
        2. 使用 high-low 範圍估算 spread
        3. 使用 volume 分配到各價格層級
        
        Args:
            kline_row: K線數據行
            num_levels: 價格層級數量
        
        Returns:
            訂單簿快照 {'bids': [[price, size], ...], 'asks': [[price, size], ...]}
        """
        close_price = float(kline_row['close'])
        high_price = float(kline_row['high'])
        low_price = float(kline_row['low'])
        volume = float(kline_row['volume'])
        
        # 估算 spread（使用 high-low 的 10%）
        spread = (high_price - low_price) * 0.1
        if spread < close_price * 0.0001:  # 最小 1bps
            spread = close_price * 0.0001
        
        # 生成買單（bids）- 價格遞減
        bids = []
        bid_start_price = close_price - spread / 2
        for i in range(num_levels):
            # 價格：越遠離中間價，價格差距越大
            price_offset = spread * (i + 1) * (1 + i * 0.1)
            price = bid_start_price - price_offset
            
            # 數量：越遠離中間價，數量越大（模擬真實訂單簿）
            size = (volume / num_levels) * (1 + i * 0.2) * np.random.uniform(0.8, 1.2)
            
            bids.append([price, size])
        
        # 生成賣單（asks）- 價格遞增
        asks = []
        ask_start_price = close_price + spread / 2
        for i in range(num_levels):
            # 價格：越遠離中間價，價格差距越大
            price_offset = spread * (i + 1) * (1 + i * 0.1)
            price = ask_start_price + price_offset
            
            # 數量：越遠離中間價，數量越大
            size = (volume / num_levels) * (1 + i * 0.2) * np.random.uniform(0.8, 1.2)
            
            asks.append([price, size])
        
        return {
            'bids': bids,
            'asks': asks,
            'timestamp': kline_row['timestamp']
        }
    
    def generate_trades(
        self,
        kline_row: pd.Series,
        trades_per_minute: int = 50
    ) -> List[Dict]:
        """
        根據 K線數據生成模擬交易
        
        策略：
        1. 使用 taker_buy_base 和 volume 計算買賣比例
        2. 價格在 open-close 之間隨機分布，趨向 close
        3. 時間戳在該 K線時間範圍內均勻分布
        
        Args:
            kline_row: K線數據行
            trades_per_minute: 每分鐘交易數量
        
        Returns:
            交易列表 [{'price': float, 'qty': float, 'is_buyer_maker': bool, 'timestamp': datetime}, ...]
        """
        timestamp = kline_row['timestamp']
        open_price = float(kline_row['open'])
        close_price = float(kline_row['close'])
        high_price = float(kline_row['high'])
        low_price = float(kline_row['low'])
        volume = float(kline_row['volume'])
        taker_buy_volume = float(kline_row['taker_buy_base'])
        
        # 計算買賣比例
        buy_ratio = taker_buy_volume / volume if volume > 0 else 0.5
        
        trades = []
        avg_trade_size = volume / trades_per_minute
        
        for i in range(trades_per_minute):
            # 時間戳：在該分鐘內均勻分布
            trade_time = timestamp + timedelta(seconds=i * 60 / trades_per_minute)
            
            # 價格：使用三角分布，趨向 close
            # 處理 open == close 的情況
            if abs(open_price - close_price) < 0.01:  # 幾乎沒有變動
                # 在 high-low 範圍內隨機
                if abs(high_price - low_price) < 0.01:
                    price = close_price  # 完全沒變動，使用收盤價
                else:
                    price = np.random.triangular(low_price, close_price, high_price)
            elif np.random.random() < 0.6:
                # 60% 概率在 close 附近
                price = np.random.triangular(
                    min(open_price, close_price),
                    close_price,
                    max(open_price, close_price)
                )
            else:
                # 40% 在全範圍
                if abs(high_price - low_price) < 0.01:
                    price = close_price
                else:
                    price = np.random.triangular(low_price, close_price, high_price)
            
            # 數量：對數正態分布（小單多，大單少）
            qty = avg_trade_size * np.random.lognormal(0, 0.5)
            
            # 買賣方向
            is_buyer_maker = np.random.random() > buy_ratio
            
            trades.append({
                'price': price,
                'qty': qty,
                'is_buyer_maker': is_buyer_maker,
                'timestamp': trade_time
            })
        
        return trades
    
    def get_market_events(
        self,
        start_date: str,
        end_date: str,
        orderbook_update_interval_ms: int = 100,
        trades_per_minute: int = 50
    ) -> List[Dict]:
        """
        生成完整的市場事件流（訂單簿更新 + 交易）
        
        Args:
            start_date: 開始日期
            end_date: 結束日期
            orderbook_update_interval_ms: 訂單簿更新間隔（毫秒）
            trades_per_minute: 每分鐘交易數量
        
        Returns:
            事件列表，按時間排序 [{'type': 'ORDERBOOK'|'TRADE', 'data': ..., 'timestamp': ...}, ...]
        """
        if self.klines_df is None:
            raise ValueError("請先調用 load_klines() 加載數據")
        
        logger.info(f"生成市場事件流: {start_date} 到 {end_date}")
        
        # 篩選日期範圍
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        
        # 如果 end_date 只有日期沒有時間，延長到當天結束
        if end_dt.hour == 0 and end_dt.minute == 0 and end_dt.second == 0:
            end_dt = end_dt + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        
        df = self.klines_df[
            (self.klines_df['timestamp'] >= start_dt) &
            (self.klines_df['timestamp'] <= end_dt)
        ].copy()
        
        logger.info(f"篩選後 K線數量: {len(df)}")
        
        events = []
        total_klines = len(df)
        
        # 對每根 K線生成事件
        for idx, (_, row) in enumerate(df.iterrows()):
            # 顯示進度（每 100 根 K線）
            if idx % 100 == 0:
                progress = (idx / total_klines) * 100
                logger.info(f"  生成事件進度: {progress:.1f}% ({idx}/{total_klines})")
            
            kline_start_time = row['timestamp']
            
            # 生成訂單簿快照（每 100ms 一次）
            updates_per_minute = 60000 // orderbook_update_interval_ms
            for i in range(updates_per_minute):
                update_time = kline_start_time + timedelta(milliseconds=i * orderbook_update_interval_ms)
                
                orderbook = self.generate_orderbook_snapshots(row)
                orderbook['timestamp'] = update_time
                
                events.append({
                    'type': 'ORDERBOOK',
                    'data': orderbook,
                    'timestamp': update_time
                })
            
            # 生成交易
            trades = self.generate_trades(row, trades_per_minute)
            for trade in trades:
                events.append({
                    'type': 'TRADE',
                    'data': trade,
                    'timestamp': trade['timestamp']
                })
        
        # 按時間排序
        events.sort(key=lambda x: x['timestamp'])
        
        logger.info(f"生成完成: {len(events)} 個事件")
        logger.info(f"  - 訂單簿更新: {sum(1 for e in events if e['type'] == 'ORDERBOOK')}")
        logger.info(f"  - 交易: {sum(1 for e in events if e['type'] == 'TRADE')}")
        
        return events


if __name__ == "__main__":
    # 測試代碼
    logging.basicConfig(level=logging.INFO)
    
    loader = HistoricalDataLoader()
    
    # 加載 2024 年 11 月 10 日的數據
    df = loader.load_klines(
        symbol="BTCUSDT",
        interval="1m",
        start_date="2024-11-10",
        end_date="2024-11-10"
    )
    
    print(f"\n加載 K線數據: {len(df)} 條")
    print(f"時間範圍: {df['timestamp'].min()} 到 {df['timestamp'].max()}")
    
    # 生成市場事件
    events = loader.get_market_events(
        start_date="2024-11-10 00:00:00",
        end_date="2024-11-10 00:05:00"
    )
    
    print(f"\n生成市場事件: {len(events)} 個")
    print(f"\n前 5 個事件:")
    for i, event in enumerate(events[:5]):
        print(f"{i+1}. {event['type']:10s} @ {event['timestamp']}")
