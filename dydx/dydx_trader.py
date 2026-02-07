"""
dYdX 交易機器人 - 基礎模組
============================
這個模組提供 dYdX 交易平台的基本功能：
- 連線測試
- 取得市場資料
- 取得帳戶資訊
- 下單/取消訂單
"""

import os
import asyncio
import logging
from dotenv import load_dotenv
from typing import Optional, Dict, Any

# 抑制 HTTP 請求日誌 (避免刷屏)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# 載入環境變數
load_dotenv()

# dYdX v4 客戶端
from dydx_v4_client.indexer.rest.indexer_client import IndexerClient


# 網路設定
NETWORK_CONFIG = {
    "mainnet": {
        "rest_indexer": "https://indexer.dydx.trade",
        "websocket_indexer": "wss://indexer.dydx.trade/v4/ws",
    },
    "testnet": {
        "rest_indexer": "https://indexer.v4testnet.dydx.exchange",
        "websocket_indexer": "wss://indexer.v4testnet.dydx.exchange/v4/ws",
    }
}


class DydxTrader:
    """dYdX 交易類別"""
    
    def __init__(self):
        """初始化交易器"""
        self.network = os.getenv("DYDX_NETWORK", "testnet")
        self.private_key = os.getenv("DYDX_PRIVATE_KEY", "")
        self.address = os.getenv("DYDX_ADDRESS", "")
        self.subaccount_number = int(os.getenv("DYDX_SUBACCOUNT_NUMBER", "0"))
        
        # 設定網路
        self.network_config = NETWORK_CONFIG.get(self.network, NETWORK_CONFIG["testnet"])
            
        # 初始化客戶端
        self.indexer_client: Optional[IndexerClient] = None
        
    async def connect(self):
        """連接到 dYdX"""
        print(f"🔗 連接到 dYdX {self.network}...")
        
        # 初始化 Indexer 客戶端 (用於查詢市場資料和帳戶)
        self.indexer_client = IndexerClient(self.network_config["rest_indexer"])
        
        if self.address:
            print(f"📍 錢包地址: {self.address}")
        else:
            print("⚠️  未設定錢包地址")
            
        if self.private_key:
            print("🔑 已設定私鑰 (用於交易簽名)")
        else:
            print("⚠️  未設定私鑰，僅可使用市場資料與帳戶查詢功能")
            
        print("✅ 連接成功!")
        
    # ==================== 市場資料 ====================
    
    async def get_markets(self) -> Dict[str, Any]:
        """取得所有永續合約市場"""
        response = await self.indexer_client.markets.get_perpetual_markets()
        return response
    
    async def get_market(self, market: str = "BTC-USD") -> Dict[str, Any]:
        """取得特定市場資料"""
        response = await self.indexer_client.markets.get_perpetual_markets(market)
        return response.get("markets", {}).get(market, {})
    
    async def get_orderbook(self, market: str = "BTC-USD") -> Dict[str, Any]:
        """取得訂單簿"""
        response = await self.indexer_client.markets.get_perpetual_market_orderbook(market)
        return response
    
    async def get_trades(self, market: str = "BTC-USD", limit: int = 100) -> Dict[str, Any]:
        """取得最近交易"""
        response = await self.indexer_client.markets.get_perpetual_market_trades(
            market, 
            limit=limit
        )
        return response
    
    async def get_candles(
        self, 
        market: str = "BTC-USD", 
        resolution: str = "1HOUR",
        limit: int = 100
    ) -> Dict[str, Any]:
        """取得 K 線數據"""
        response = await self.indexer_client.markets.get_perpetual_market_candles(
            market,
            resolution=resolution,
            limit=limit
        )
        return response
    
    async def get_funding_rate(self, market: str = "BTC-USD") -> Dict[str, Any]:
        """取得資金費率"""
        response = await self.indexer_client.markets.get_perpetual_market_historical_funding(
            market,
            limit=1
        )
        return response
    
    # ==================== 帳戶資料 ====================
    
    async def get_account(self) -> Dict[str, Any]:
        """取得帳戶資訊"""
        if not self.address:
            raise ValueError("未設定錢包地址")
        
        response = await self.indexer_client.account.get_subaccount(
            self.address,
            self.subaccount_number
        )
        return response
    
    async def get_positions(self) -> Dict[str, Any]:
        """取得當前持倉"""
        if not self.address:
            raise ValueError("未設定錢包地址")
            
        response = await self.indexer_client.account.get_subaccount_perpetual_positions(
            self.address,
            self.subaccount_number
        )
        return response
    
    async def get_orders(self, status: str = None) -> Dict[str, Any]:
        """取得訂單"""
        if not self.address:
            raise ValueError("未設定錢包地址")
            
        response = await self.indexer_client.account.get_subaccount_orders(
            self.address,
            self.subaccount_number,
            status=status
        )
        return response
    
    async def get_fills(self, market: str = None, limit: int = 100) -> Dict[str, Any]:
        """取得成交記錄"""
        if not self.address:
            raise ValueError("未設定錢包地址")
            
        response = await self.indexer_client.account.get_subaccount_fills(
            self.address,
            self.subaccount_number,
            ticker=market,
            limit=limit
        )
        return response
    
    async def get_historical_pnl(self) -> Dict[str, Any]:
        """取得歷史損益"""
        if not self.address:
            raise ValueError("未設定錢包地址")
            
        response = await self.indexer_client.account.get_subaccount_historical_pnls(
            self.address,
            self.subaccount_number
        )
        return response


async def test_connection():
    """測試 API 連線"""
    print("=" * 50)
    print("🚀 dYdX API 連線測試")
    print("=" * 50)
    
    trader = DydxTrader()
    await trader.connect()
    
    print("\n📊 取得 BTC-USD 市場資料...")
    try:
        market = await trader.get_market("BTC-USD")
        if market:
            print(f"   市場: BTC-USD")
            print(f"   Oracle 價格: ${float(market.get('oraclePrice', 0)):,.2f}")
            print(f"   24h 交易量: ${float(market.get('volume24H', 0)):,.2f}")
            print(f"   未平倉量: ${float(market.get('openInterest', 0)):,.2f}")
            print(f"   資金費率: {float(market.get('nextFundingRate', 0)) * 100:.4f}%")
        else:
            print("   無法取得市場資料")
    except Exception as e:
        print(f"   ❌ 錯誤: {e}")
    
    print("\n📖 取得訂單簿...")
    try:
        orderbook = await trader.get_orderbook("BTC-USD")
        if orderbook:
            bids = orderbook.get("bids", [])[:3]
            asks = orderbook.get("asks", [])[:3]
            print(f"   賣單 (前3):")
            for ask in reversed(asks):
                print(f"     ${float(ask['price']):,.2f} | {float(ask['size']):.4f} BTC")
            print(f"   ─────────────")
            print(f"   買單 (前3):")
            for bid in bids:
                print(f"     ${float(bid['price']):,.2f} | {float(bid['size']):.4f} BTC")
    except Exception as e:
        print(f"   ❌ 錯誤: {e}")
        
    print("\n📈 取得最近交易...")
    try:
        trades = await trader.get_trades("BTC-USD", limit=5)
        if trades and trades.get("trades"):
            for trade in trades["trades"][:5]:
                side = "🟢買" if trade["side"] == "BUY" else "🔴賣"
                print(f"   {side} ${float(trade['price']):,.2f} | {float(trade['size']):.4f} BTC")
    except Exception as e:
        print(f"   ❌ 錯誤: {e}")
    
    # 如果有設定地址，測試帳戶功能
    if trader.address and trader.address != "dydx1...":
        print("\n👤 取得帳戶資訊...")
        try:
            account = await trader.get_account()
            if account:
                subaccount = account.get("subaccount", {})
                equity = float(subaccount.get("equity", 0))
                free_collateral = float(subaccount.get("freeCollateral", 0))
                print(f"   帳戶權益: ${equity:,.2f}")
                print(f"   可用保證金: ${free_collateral:,.2f}")
        except Exception as e:
            print(f"   ❌ 錯誤: {e}")
            
        print("\n📊 取得持倉...")
        try:
            positions = await trader.get_positions()
            if positions and positions.get("positions"):
                for pos in positions["positions"]:
                    side = "🟢多" if pos["side"] == "LONG" else "🔴空"
                    print(f"   {pos['market']} {side} | 數量: {pos['size']} | 入場: ${float(pos['entryPrice']):,.2f}")
            else:
                print("   無持倉")
        except Exception as e:
            print(f"   ❌ 錯誤: {e}")
    else:
        print("\n⚠️  請在 .env 設定 DYDX_ADDRESS 以查看帳戶資訊")
    
    print("\n" + "=" * 50)
    print("✅ 測試完成!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_connection())
