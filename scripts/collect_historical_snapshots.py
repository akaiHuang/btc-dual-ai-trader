"""
真實歷史數據收集器

Purpose:
    收集真實 Binance WebSocket 數據（orderbook + trades）
    保存為 Parquet 格式，用於未來的準確回測

Output:
    - BTCUSDT_orderbook_YYYYMMDD.parquet
    - BTCUSDT_trades_YYYYMMDD.parquet
"""

import sys
import asyncio
from binance import AsyncClient, BinanceSocketManager
import pandas as pd
from datetime import datetime
import json
import time
import os


class HistoricalDataCollector:

import asyncio
import json
from datetime import datetime
from pathlib import Path
import pandas as pd
from typing import List, Dict
import logging

from binance import AsyncClient
from binance.streams import BinanceSocketManager

logger = logging.getLogger(__name__)


class HistoricalDataCollector:
    """
    歷史數據收集器
    
    實時收集並存儲 Binance 市場數據，供未來回測使用
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        save_dir: str = "data/historical_snapshots"
    ):
        self.symbol = symbol
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        
        # 數據緩衝區
        self.orderbook_buffer: List[Dict] = []
        self.trade_buffer: List[Dict] = []
        
        # 緩衝區大小（達到此大小時寫入磁盤）
        self.buffer_size = 1000
        
        # 當前日期（用於文件命名）
        self.current_date = datetime.now().strftime("%Y%m%d")
        
        logger.info(f"初始化 HistoricalDataCollector: {symbol}")
        logger.info(f"存儲目錄: {self.save_dir}")
    
    def _get_filename(self, data_type: str) -> Path:
        """獲取文件名"""
        return self.save_dir / f"{self.symbol}_{data_type}_{self.current_date}.parquet"
    
    def process_orderbook(self, msg: Dict):
        """處理訂單簿消息"""
        try:
            timestamp = msg['E']  # Event time
            
            # 提取前 20 檔
            bids = msg['b'][:20]  # [[price, qty], ...]
            asks = msg['a'][:20]
            
            # 構建記錄
            record = {
                'timestamp': timestamp,
                'event_time': datetime.fromtimestamp(timestamp / 1000),
                'bids': json.dumps(bids),  # 序列化為 JSON 字符串
                'asks': json.dumps(asks),
            }
            
            self.orderbook_buffer.append(record)
            
            # 如果緩衝區滿了，寫入磁盤
            if len(self.orderbook_buffer) >= self.buffer_size:
                self._flush_orderbook()
        
        except Exception as e:
            logger.error(f"處理訂單簿錯誤: {e}")
    
    def process_trade(self, msg: Dict):
        """處理交易消息"""
        try:
            # 構建記錄
            record = {
                'timestamp': msg['E'],
                'event_time': datetime.fromtimestamp(msg['E'] / 1000),
                'trade_id': msg['a'],
                'price': float(msg['p']),
                'quantity': float(msg['q']),
                'is_buyer_maker': msg['m'],
            }
            
            self.trade_buffer.append(record)
            
            # 如果緩衝區滿了，寫入磁盤
            if len(self.trade_buffer) >= self.buffer_size:
                self._flush_trades()
        
        except Exception as e:
            logger.error(f"處理交易錯誤: {e}")
    
    def _flush_orderbook(self):
        """將訂單簿緩衝區寫入磁盤"""
        if not self.orderbook_buffer:
            return
        
        filename = self._get_filename("orderbook")
        df = pd.DataFrame(self.orderbook_buffer)
        
        # 如果文件已存在，追加
        if filename.exists():
            existing_df = pd.read_parquet(filename)
            df = pd.concat([existing_df, df], ignore_index=True)
        
        df.to_parquet(filename, index=False, compression='snappy')
        
        logger.info(f"寫入 {len(self.orderbook_buffer)} 條訂單簿記錄到 {filename}")
        self.orderbook_buffer = []
    
    def _flush_trades(self):
        """將交易緩衝區寫入磁盤"""
        if not self.trade_buffer:
            return
        
        filename = self._get_filename("trades")
        df = pd.DataFrame(self.trade_buffer)
        
        # 如果文件已存在，追加
        if filename.exists():
            existing_df = pd.read_parquet(filename)
            df = pd.concat([existing_df, df], ignore_index=True)
        
        df.to_parquet(filename, index=False, compression='snappy')
        
        logger.info(f"寫入 {len(self.trade_buffer)} 條交易記錄到 {filename}")
        self.trade_buffer = []
    
    def flush_all(self):
        """強制寫入所有緩衝區"""
        self._flush_orderbook()
        self._flush_trades()
        logger.info("所有緩衝區已寫入磁盤")
    
    async def start_collection(self, duration_hours: int = 24):
        """
        開始收集數據
        
        Args:
            duration_hours: 收集時長（小時）
        """
        logger.info(f"開始收集數據，預計運行 {duration_hours} 小時")
        
        client = await AsyncClient.create()
        bsm = BinanceSocketManager(client)
        
        # 訂單簿 WebSocket
        orderbook_socket = bsm.depth_socket(self.symbol, depth=BinanceSocketManager.WEBSOCKET_DEPTH_20)
        
        # 交易 WebSocket
        trade_socket = bsm.aggtrade_socket(self.symbol)
        
        try:
            async with orderbook_socket as ob_stream, trade_socket as trade_stream:
                end_time = datetime.now().timestamp() + duration_hours * 3600
                
                while datetime.now().timestamp() < end_time:
                    # 接收訂單簿更新
                    ob_msg = await asyncio.wait_for(ob_stream.recv(), timeout=1.0)
                    self.process_orderbook(ob_msg)
                    
                    # 接收交易
                    try:
                        trade_msg = await asyncio.wait_for(trade_stream.recv(), timeout=0.1)
                        self.process_trade(trade_msg)
                    except asyncio.TimeoutError:
                        pass
                    
                    # 每 60 秒檢查是否需要新建文件（新的一天）
                    current_date = datetime.now().strftime("%Y%m%d")
                    if current_date != self.current_date:
                        self.flush_all()
                        self.current_date = current_date
                        logger.info(f"日期變更，新文件: {current_date}")
        
        except Exception as e:
            logger.error(f"收集數據錯誤: {e}")
        
        finally:
            # 確保所有數據都寫入
            self.flush_all()
            await client.close_connection()
            logger.info("數據收集已停止")


async def main():
    """收集數據"""
    import sys
    
    # 命令行參數
    duration_hours = float(sys.argv[1]) if len(sys.argv) > 1 else 24
    save_dir = sys.argv[2] if len(sys.argv) > 2 else "data/historical_snapshots"
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    print(f"📥 開始收集真實市場數據")
    print(f"   時長: {duration_hours} 小時")
    print(f"   保存: {save_dir}")
    print()
    
    collector = HistoricalDataCollector(
        symbol="BTCUSDT",
        save_dir=save_dir
    )
    
    await collector.start_collection(duration_hours=duration_hours)


if __name__ == "__main__":
    asyncio.run(main())
