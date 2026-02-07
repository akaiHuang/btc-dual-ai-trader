"""
Binance Exchange Client Module
提供 Binance API 的高級封裝，包含錯誤處理、重試機制、速率限制
"""

import time
import hmac
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException
import requests

from ..core.config import get_config

logger = logging.getLogger(__name__)


class BinanceClient:
    """
    Binance API 客戶端封裝
    
    功能：
    - 自動重試機制（指數退避）
    - 速率限制管理
    - 錯誤處理與日誌記錄
    - REST API 和 WebSocket 支援
    """
    
    # Binance API 限制
    MAX_REQUESTS_PER_MINUTE = 1200
    MAX_ORDERS_PER_SECOND = 10
    MAX_ORDERS_PER_DAY = 200000
    
    # 重試配置
    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 2  # 秒（指數退避基數）
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = True):
        """
        初始化 Binance 客戶端
        
        Args:
            api_key: API Key（若為 None 則從配置讀取）
            api_secret: API Secret（若為 None 則從配置讀取）
            testnet: 是否使用測試網
        """
        config = get_config()
        
        self.api_key = api_key or config.binance.api_key
        self.api_secret = api_secret or config.binance.api_secret
        self.testnet = testnet if testnet is not None else config.binance.testnet
        
        # 初始化 python-binance 客戶端
        self.client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet
        )
        
        # 設置 Testnet URL
        if self.testnet:
            self.client.API_URL = 'https://testnet.binance.vision/api'
            logger.info("使用 Binance Testnet")
        else:
            logger.warning("使用 Binance 正式環境！")
        
        # 速率限制追蹤
        self._request_count = 0
        self._request_window_start = time.time()
        self._last_request_time = 0
        
        logger.info(f"BinanceClient 初始化成功 (Testnet: {self.testnet})")
    
    def _check_rate_limit(self):
        """檢查並執行速率限制"""
        current_time = time.time()
        
        # 重置計數器（每分鐘）
        if current_time - self._request_window_start > 60:
            self._request_count = 0
            self._request_window_start = current_time
        
        # 檢查是否超過限制
        if self._request_count >= self.MAX_REQUESTS_PER_MINUTE:
            sleep_time = 60 - (current_time - self._request_window_start)
            if sleep_time > 0:
                logger.warning(f"達到速率限制，等待 {sleep_time:.2f} 秒")
                time.sleep(sleep_time)
                self._request_count = 0
                self._request_window_start = time.time()
        
        self._request_count += 1
        self._last_request_time = current_time
    
    def _retry_request(self, func, *args, **kwargs) -> Any:
        """
        執行 API 請求，帶重試機制
        
        Args:
            func: 要執行的函數
            *args: 函數參數
            **kwargs: 函數關鍵字參數
            
        Returns:
            API 響應結果
            
        Raises:
            BinanceAPIException: API 錯誤（重試後仍失敗）
            BinanceRequestException: 網路錯誤（重試後仍失敗）
        """
        last_exception = None
        
        for attempt in range(self.MAX_RETRIES):
            try:
                # 檢查速率限制
                self._check_rate_limit()
                
                # 執行請求
                result = func(*args, **kwargs)
                
                # 成功則返回
                if attempt > 0:
                    logger.info(f"重試成功（嘗試 {attempt + 1}/{self.MAX_RETRIES}）")
                
                return result
                
            except BinanceAPIException as e:
                last_exception = e
                
                # 某些錯誤不應重試
                if e.code in [-2015, -1022]:  # Invalid API key, Invalid signature
                    logger.error(f"API 驗證失敗: {e.message}")
                    raise
                
                # 速率限制錯誤
                if e.code == -1003:
                    logger.warning("觸發 Binance 速率限制，等待後重試")
                    time.sleep(60)
                    continue
                
                # 其他錯誤：指數退避
                if attempt < self.MAX_RETRIES - 1:
                    retry_delay = self.RETRY_DELAY_BASE ** attempt
                    logger.warning(f"API 錯誤 {e.code}: {e.message}，{retry_delay}秒後重試（{attempt + 1}/{self.MAX_RETRIES}）")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"API 請求失敗，已重試 {self.MAX_RETRIES} 次: {e.message}")
                    raise
                    
            except BinanceRequestException as e:
                last_exception = e
                
                if attempt < self.MAX_RETRIES - 1:
                    retry_delay = self.RETRY_DELAY_BASE ** attempt
                    logger.warning(f"網路錯誤: {e}，{retry_delay}秒後重試（{attempt + 1}/{self.MAX_RETRIES}）")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"網路請求失敗，已重試 {self.MAX_RETRIES} 次: {e}")
                    raise
            
            except Exception as e:
                last_exception = e
                logger.error(f"未預期的錯誤: {e}")
                raise
        
        # 如果所有重試都失敗
        raise last_exception
    
    # ==========================================
    # 市場資料 API
    # ==========================================
    
    def get_server_time(self) -> int:
        """
        獲取 Binance 伺服器時間
        
        Returns:
            伺服器時間戳（毫秒）
        """
        result = self._retry_request(self.client.get_server_time)
        return result['serverTime']
    
    def get_symbol_ticker(self, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """
        獲取交易對當前價格
        
        Args:
            symbol: 交易對符號
            
        Returns:
            包含 symbol 和 price 的字典
        """
        return self._retry_request(self.client.get_symbol_ticker, symbol=symbol)
    
    def get_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "3m",
        limit: int = 500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None
    ) -> List[List]:
        """
        獲取 K 線資料
        
        Args:
            symbol: 交易對符號
            interval: K 線時間間隔（1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M）
            limit: 返回數量（最大 1000）
            start_time: 起始時間（毫秒時間戳）
            end_time: 結束時間（毫秒時間戳）
            
        Returns:
            K 線資料列表，每個元素為:
            [
                開盤時間, 開盤價, 最高價, 最低價, 收盤價, 成交量,
                收盤時間, 成交額, 成交筆數, 主動買入成交量, 主動買入成交額, 忽略
            ]
        """
        return self._retry_request(
            self.client.get_klines,
            symbol=symbol,
            interval=interval,
            limit=limit,
            startTime=start_time,
            endTime=end_time
        )
    
    def get_order_book(self, symbol: str = "BTCUSDT", limit: int = 100) -> Dict[str, Any]:
        """
        獲取訂單簿（Order Book）深度資料
        
        Args:
            symbol: 交易對符號
            limit: 深度檔位（5, 10, 20, 50, 100, 500, 1000, 5000）
            
        Returns:
            包含 bids（買單）和 asks（賣單）的字典
            每個訂單為 [價格, 數量] 格式
        """
        return self._retry_request(self.client.get_order_book, symbol=symbol, limit=limit)
    
    def get_recent_trades(self, symbol: str = "BTCUSDT", limit: int = 500) -> List[Dict]:
        """
        獲取最近成交記錄
        
        Args:
            symbol: 交易對符號
            limit: 返回數量（最大 1000）
            
        Returns:
            成交記錄列表
        """
        return self._retry_request(self.client.get_recent_trades, symbol=symbol, limit=limit)
    
    def get_24h_ticker(self, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """
        獲取 24 小時價格變動統計
        
        Args:
            symbol: 交易對符號
            
        Returns:
            包含 24h 統計資料的字典
        """
        return self._retry_request(self.client.get_ticker, symbol=symbol)
    
    # ==========================================
    # 帳戶資料 API
    # ==========================================
    
    def get_account_info(self) -> Dict[str, Any]:
        """
        獲取帳戶資訊
        
        Returns:
            帳戶資訊字典，包含餘額、權限等
        """
        return self._retry_request(self.client.get_account)
    
    def get_balance(self, asset: str = "USDT") -> Dict[str, str]:
        """
        獲取指定資產餘額
        
        Args:
            asset: 資產符號（BTC, USDT, ETH 等）
            
        Returns:
            包含 free（可用）和 locked（凍結）的字典
        """
        account = self.get_account_info()
        for balance in account['balances']:
            if balance['asset'] == asset:
                return balance
        return {'asset': asset, 'free': '0.0', 'locked': '0.0'}
    
    # ==========================================
    # 交易 API（謹慎使用）
    # ==========================================
    
    def create_test_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        創建測試訂單（不會實際下單）
        
        Args:
            symbol: 交易對符號
            side: BUY 或 SELL
            order_type: LIMIT, MARKET, STOP_LOSS_LIMIT 等
            quantity: 數量
            price: 價格（LIMIT 訂單必填）
            **kwargs: 其他參數（timeInForce, stopPrice 等）
            
        Returns:
            測試訂單結果
        """
        params = {
            'symbol': symbol,
            'side': side,
            'type': order_type,
            'quantity': quantity,
        }
        
        if price:
            params['price'] = price
        
        params.update(kwargs)
        
        return self._retry_request(self.client.create_test_order, **params)
    
    def get_exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        獲取交易規則和交易對資訊
        
        Args:
            symbol: 交易對符號（可選，若為 None 則返回所有交易對）
            
        Returns:
            交易所資訊字典
        """
        if symbol:
            return self._retry_request(self.client.get_symbol_info, symbol)
        else:
            return self._retry_request(self.client.get_exchange_info)
    
    # ==========================================
    # 輔助方法
    # ==========================================
    
    def ping(self) -> bool:
        """
        測試連線
        
        Returns:
            連線是否正常
        """
        try:
            self._retry_request(self.client.ping)
            return True
        except Exception as e:
            logger.error(f"Ping 失敗: {e}")
            return False
    
    def get_system_status(self) -> Dict[str, Any]:
        """
        獲取系統狀態
        
        Returns:
            系統狀態字典（status: 0=正常, 1=維護）
        """
        return self._retry_request(self.client.get_system_status)
    
    def __repr__(self) -> str:
        return f"BinanceClient(testnet={self.testnet}, api_key={self.api_key[:8]}...)"
