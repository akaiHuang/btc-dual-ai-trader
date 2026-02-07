#!/usr/bin/env python3
"""
dYdX 鯨魚交易機器人
===================
基於 whale_testnet_trader.py 的策略邏輯
使用 dYdX v4 API (含 Maker/Taker 手續費)

特點:
- 手續費納入回測/盈虧
- 50X 槓桿
- 六維評分系統
- 兩階段止盈止損
"""

import os
import sys
import time
import asyncio
import json
import random
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from dotenv import load_dotenv
import aiohttp  # 🆕 Fixed: Import at module level

# 抑制 HTTP 請求日誌 (避免刷屏)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

# 載入環境變數
load_dotenv()

# dYdX v4 客戶端
try:
    from dydx_v4_client.indexer.rest.indexer_client import IndexerClient
    from dydx_v4_client.indexer.rest.constants import OrderType
    from dydx_v4_client.node.client import NodeClient
    from dydx_v4_client.node.market import Market
    from dydx_v4_client.node.builder import TxOptions  # 🆕 Permissioned Keys
    from dydx_v4_client.wallet import Wallet
    from dydx_v4_client.key_pair import KeyPair
    from dydx_v4_client import MAX_CLIENT_ID, OrderFlags
    from v4_proto.dydxprotocol.clob.order_pb2 import Order
    DYDX_AVAILABLE = True
except ImportError as e:
    print(f"⚠️  dYdX SDK 未安裝: {e}")
    print("   請執行: pip install dydx-v4-client")
    DYDX_AVAILABLE = False

# 設置日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DydxConfig:
    """dYdX 交易配置 - 混合策略 (Hybrid Dynamic Scalping)"""
    # 網路
    network: str = "mainnet"  # mainnet 或 testnet
    
    # 交易參數
    symbol: str = "BTC-USD"
    leverage: int = 50
    position_size_pct: float = 0.10  # 使用 10% 資金
    
    # 交易手續費 (% of notional)
    maker_fee_pct: float = 0.005
    taker_fee_pct: float = 0.04
    
    # ═══════════════════════════════════════════════════════════════════
    # 🎯 混合策略參數 (Hybrid Strategy - 點差安全 + 爆擊能力)
    # ═══════════════════════════════════════════════════════════════════
    
    # 固定止盈止損 (保底)
    target_profit_pct: float = 2.0    # 固定目標 2% (讓追蹤止盈先觸發)
    stop_loss_pct: float = 1.0        # 止損 1.0%
    
    # 🆕 動態追蹤止盈 (核心機制)
    trailing_start_pct: float = 0.5   # 🔧 獲利 0.5% 開始追蹤 (脫離點差危險區)
    trailing_offset_pct: float = 0.3  # 🔧 回撤 0.3% 就平倉 (給價格呼吸空間)
    
    # 🆕 三線反轉緊急煞車 (虧損時絕對執行)
    reversal_enabled: bool = True     # 啟用三線反轉
    reversal_threshold_sec: float = 15.0  # 反轉信號累積 15 秒
    reversal_loss_only: bool = False  # False=任何時候都檢查, True=只在虧損時檢查
    
    # 兩階段出場 (原本的，作為備用)
    phase1_target_pct: float = 2.0    # 第一階段目標
    phase1_stop_loss_pct: float = 1.0 # 第一階段止損
    phase2_trailing_start_pct: float = 1.0  # 開始追蹤止盈
    phase2_trailing_offset_pct: float = 0.5  # 追蹤偏移
    
    # 六維評分閾值
    six_dim_threshold: int = 4  # 至少 4/12 分才進場
    
    # 時間控制
    min_hold_seconds: int = 15   # 🔧 最短持倉 15 秒 (允許快速退出)
    max_hold_minutes: int = 30   # 最長持倉 30 分鐘
    
    # Paper Trading
    paper_trading: bool = True
    paper_initial_balance: float = 1000.0

    # 🎲 隨機進場模式
    random_entry_mode: bool = False
    random_entry_balance_enabled: bool = True
    random_entry_balance_batch_size: int = 20
    random_entry_balance_prefill_size: int = 30
    random_entry_balance_max_streak: int = 3
    random_entry_balance_max_imbalance: int = 4

    # 🔐 鎖利顯示 (僅用於狀態顯示)
    use_midpoint_lock: bool = True
    midpoint_ratio: float = 0.7
    lock_start_pct: float = 0.0
    min_lock_pct: float = 0.0
    use_n_lock_n: bool = True
    n_lock_n_threshold: float = 1.0
    n_lock_n_buffer: float = 0.0
    
    # 🆕 同步真實交易模式
    sync_real_trading: bool = False  # 是否同步到真實交易
    real_position_size_pct: float = 0.05  # 真實交易使用 5% 資金 (較保守)
    fixed_btc_size: float = 0.0  # 🆕 固定 BTC 倉位 (覆蓋 size_pct)


# ═══════════════════════════════════════════════════════════════════════════════
# 隨機平衡序列工具
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_constrained_balanced_sequence(
    batch_size: int,
    *,
    max_streak: int = 3,
    max_imbalance: int = 4,
    rng: Optional["random.Random"] = None,
) -> List[str]:
    """
    Generate a LONG/SHORT sequence with exact 50/50 counts per batch while
    limiting long streaks / imbalance to avoid early one-sided runs.
    """
    import random

    if batch_size < 2 or batch_size % 2 != 0:
        raise ValueError("batch_size must be an even integer >= 2")

    rng = rng or random.Random()

    remaining = {"LONG": batch_size // 2, "SHORT": batch_size // 2}
    counts = {"LONG": 0, "SHORT": 0}
    seq: List[str] = []

    last_dir: Optional[str] = None
    streak_len = 0

    def _allowed(direction: str) -> bool:
        if remaining[direction] <= 0:
            return False
        if max_streak and last_dir == direction and streak_len >= max_streak:
            return False
        if max_imbalance and max_imbalance > 0:
            nl = counts["LONG"] + (1 if direction == "LONG" else 0)
            ns = counts["SHORT"] + (1 if direction == "SHORT" else 0)
            if abs(nl - ns) > max_imbalance:
                return False
        return True

    for _ in range(batch_size):
        candidates = [d for d in ("LONG", "SHORT") if _allowed(d)]
        if not candidates:
            candidates = [d for d in ("LONG", "SHORT") if remaining[d] > 0]
        if not candidates:
            break

        weights = [remaining[d] for d in candidates]
        pick = rng.choices(candidates, weights=weights, k=1)[0]

        seq.append(pick)
        remaining[pick] -= 1
        counts[pick] += 1

        if pick == last_dir:
            streak_len += 1
        else:
            last_dir = pick
            streak_len = 1

    return seq


# 網路設定
NETWORK_CONFIG = {
    "mainnet": {
        "rest_indexer": "https://indexer.dydx.trade",
        "websocket_indexer": "wss://indexer.dydx.trade/v4/ws",
        "node_grpc": "dydx-ops-grpc.kingnodes.com:443",
    },
    "testnet": {
        "rest_indexer": "https://indexer.v4testnet.dydx.exchange",
        "websocket_indexer": "wss://indexer.v4testnet.dydx.exchange/v4/ws",
        "node_grpc": "test-dydx-grpc.kingnodes.com:443",
    }
}


# ═══════════════════════════════════════════════════════════════════════════════
# 🆕 v13.0: dYdX WebSocket 客戶端 (統一資料源)
# ═══════════════════════════════════════════════════════════════════════════════

class DydxWebSocketClient:
    """
    dYdX Indexer WebSocket 客戶端
    
    提供實時數據:
    - 價格 (來自 trades)
    - Orderbook (來自 orderbook)
    - OBI (前 N 檔計算)
    - WPI (成交流分析)
    """
    
    def __init__(self, symbol: str = "BTC-USD", network: str = "mainnet", address: str = None):
        self.symbol = symbol
        self.network = network
        self.ws_url = NETWORK_CONFIG[network]["websocket_indexer"]
        self.address = address  # 🆕 v14.6.11: 用於訂閱持倉更新
        
        # 實時數據
        self.current_price: float = 0.0
        self.price_source: str = ""  # trades | orderbook_mid
        self.price_updated: float = 0.0
        self.orderbook: Dict[str, List] = {"bids": [], "asks": []}
        self.recent_trades: List[Dict] = []
        self.last_update: float = 0.0
        
        # 🆕 v14.6.11: 持倉狀態 (從 WebSocket 更新)
        self.positions: Dict[str, Dict] = {}  # market -> position data
        self.position_updated: float = 0.0
        
        # 計算指標
        self.obi: float = 0.0
        self.wpi: float = 0.0
        
        # 連線狀態
        self._ws = None
        self._running = False
        self._task = None

    def _level_price(self, level) -> float:
        """Extract price from an orderbook level that may be a dict or list."""
        try:
            if isinstance(level, dict):
                return float(level.get("price", 0))
            # common format: [price, size]
            if isinstance(level, (list, tuple)) and len(level) >= 1:
                return float(level[0])
        except Exception:
            return 0.0
        return 0.0

    def _update_price_from_orderbook(self):
        """Update current_price using best bid/ask mid-price when available."""
        bids = self.orderbook.get("bids") or []
        asks = self.orderbook.get("asks") or []
        if not bids or not asks:
            return
        best_bid = self._level_price(bids[0])
        best_ask = self._level_price(asks[0])
        if best_bid > 0 and best_ask > 0:
            self.current_price = (best_bid + best_ask) / 2
            self.price_source = "orderbook_mid"
            self.price_updated = time.time()
    
    async def connect(self) -> bool:
        """連接 WebSocket"""
        try:
            import aiohttp
            
            logger.info(f"🔌 連接 dYdX WebSocket ({self.network})...")
            
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(self.ws_url)
            
            # 訂閱頻道
            await self._subscribe()
            
            # 啟動接收循環
            self._running = True
            self._task = asyncio.create_task(self._receive_loop())
            
            logger.info("✅ dYdX WebSocket 連接成功")
            return True
            
        except Exception as e:
            logger.error(f"❌ WebSocket 連接失敗: {e}")
            return False
    
    async def _subscribe(self):
        """訂閱頻道"""
        # 訂閱交易 (價格更新)
        await self._ws.send_json({
            "type": "subscribe",
            "channel": "v4_trades",
            "id": self.symbol
        })
        
        # 訂閱 Orderbook
        await self._ws.send_json({
            "type": "subscribe",
            "channel": "v4_orderbook",
            "id": self.symbol
        })
        
        # 🆕 v14.6.11: 訂閱持倉更新 (如果有地址)
        if self.address:
            await self._ws.send_json({
                "type": "subscribe",
                "channel": "v4_subaccounts",
                "id": f"{self.address}/0"  # subaccount 0
            })
            logger.info(f"📡 已訂閱: v4_trades, v4_orderbook, v4_subaccounts ({self.symbol})")
        else:
            logger.info(f"📡 已訂閱: v4_trades, v4_orderbook ({self.symbol})")
    
    async def _receive_loop(self):
        """接收消息循環"""
        import time
        
        while self._running:
            try:
                msg = await asyncio.wait_for(self._ws.receive(), timeout=30)
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_message(data)
                    
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    logger.warning("⚠️ WebSocket 連線中斷，嘗試重連...")
                    break
                    
            except asyncio.TimeoutError:
                # 發送心跳
                await self._ws.ping()
                
            except Exception as e:
                logger.error(f"WebSocket 錯誤: {e}")
                break
    
    async def _handle_message(self, data: Dict):
        """處理消息"""
        import time
        
        channel = data.get("channel", "")
        msg_type = data.get("type", "")
        
        if msg_type == "subscribed":
            return
        
        # 交易更新 (價格)
        if channel == "v4_trades":
            trades = data.get("contents", {}).get("trades", [])
            if trades:
                for trade in trades:
                    price = float(trade.get("price", 0))
                    size = float(trade.get("size", 0))
                    side = trade.get("side", "")
                    
                    if price > 0:
                        self.current_price = price
                        self.price_source = "trades"
                        self.price_updated = time.time()
                    
                    # 更新成交紀錄 (最近 100 筆)
                    self.recent_trades.append({
                        "price": price,
                        "size": size,
                        "side": side,
                        "time": time.time()
                    })
                    self.recent_trades = self.recent_trades[-100:]
                
                # 計算 WPI
                self._calculate_wpi()
        
        # Orderbook 更新
        elif channel == "v4_orderbook":
            contents = data.get("contents", {})
            
            if "bids" in contents:
                self.orderbook["bids"] = contents["bids"]
            if "asks" in contents:
                self.orderbook["asks"] = contents["asks"]
            
            # 計算 OBI
            self._calculate_obi()

            # 價格更新：用 orderbook 中價避免「沒成交就卡價」
            self._update_price_from_orderbook()
        
        # 🆕 v14.6.11: 持倉更新
        elif channel == "v4_subaccounts":
            contents = data.get("contents", {})
            
            # 處理持倉變化
            if "perpetualPositions" in contents:
                positions = contents["perpetualPositions"]
                for pos in positions:
                    market = pos.get("market", "")
                    size = float(pos.get("size", 0))
                    entry_price = float(pos.get("entryPrice", 0))

                    # 🔧 v14.6.14: 優先使用 side 欄位判斷多空（避免 size 不帶正負造成誤判）
                    raw_side = pos.get("side") or pos.get("positionSide")
                    side = None
                    if raw_side:
                        s = str(raw_side).upper()
                        if s in ("LONG", "BUY"):
                            side = "LONG"
                        elif s in ("SHORT", "SELL"):
                            side = "SHORT"
                    if side is None:
                        side = "LONG" if size > 0 else "SHORT" if size < 0 else "NONE"

                    # 統一 raw_size 正負號
                    raw_size = size
                    if side == "LONG":
                        raw_size = abs(size)
                    elif side == "SHORT":
                        raw_size = -abs(size)
                    
                    if abs(size) > 0.00001:
                        self.positions[market] = {
                            "market": market,
                            "side": side,
                            "size": abs(size),
                            "entry_price": entry_price,
                            "raw_size": raw_size
                        }
                        logger.info(f"📊 [WS] 持倉更新: {market} {side} {abs(size):.4f} @ ${entry_price:,.2f}")
                    else:
                        # 持倉已平
                        if market in self.positions:
                            logger.info(f"📊 [WS] 持倉已平: {market}")
                            del self.positions[market]
                            # 🆕 v14.6.16: 標記需要清掃訂單
                            self._position_closed_market = market
                            self._position_closed_time = time.time()
                    
                    self.position_updated = time.time()
            
            # 處理訂單狀態 (填充/取消)
            if "orders" in contents:
                orders = contents["orders"]
                for order in orders:
                    status = order.get("status", "")
                    if status == "FILLED":
                        logger.info(f"✅ [WS] 訂單成交: {order.get('side')} {order.get('size')} @ ${order.get('price')}")
        
        self.last_update = time.time()
    
    def check_position_closed(self) -> Optional[str]:
        """
        🆕 v14.6.16: 檢查是否有持倉被平倉 (用於觸發訂單清掃)
        
        Returns:
            被平倉的 market 名稱，或 None
        """
        market = getattr(self, '_position_closed_market', None)
        if market:
            # 清除標記 (只觸發一次)
            self._position_closed_market = None
            return market
        return None
    
    def get_position(self, market: str = "BTC-USD") -> Optional[Dict]:
        """🆕 v14.6.11: 取得指定市場的持倉"""
        return self.positions.get(market)
    
    def has_position(self, market: str = "BTC-USD") -> bool:
        """🆕 v14.6.11: 檢查是否有持倉"""
        pos = self.positions.get(market)
        return pos is not None and pos.get("size", 0) > 0.00001
    
    def _calculate_obi(self, depth: int = 20):
        """計算 OBI (只用前 N 檔，避免深度稀釋)"""
        try:
            # 取前 N 檔
            top_bids = self.orderbook["bids"][:depth]
            top_asks = self.orderbook["asks"][:depth]
            
            bid_vol = sum(float(b.get("size", b[1]) if isinstance(b, dict) else b[1]) 
                         for b in top_bids if b)
            ask_vol = sum(float(a.get("size", a[1]) if isinstance(a, dict) else a[1]) 
                         for a in top_asks if a)
            
            total = bid_vol + ask_vol
            if total > 0:
                self.obi = (bid_vol - ask_vol) / total
            else:
                self.obi = 0.0
                
        except Exception as e:
            logger.warning(f"OBI 計算錯誤: {e}")
            self.obi = 0.0
    
    def _calculate_wpi(self, window_seconds: int = 60):
        """計算 WPI (Whale Pressure Index)"""
        import time
        try:
            now = time.time()
            cutoff = now - window_seconds
            
            recent = [t for t in self.recent_trades if t["time"] > cutoff]
            
            if not recent:
                self.wpi = 0.0
                return
            
            buy_volume = sum(t["size"] * t["price"] for t in recent if t["side"] == "BUY")
            sell_volume = sum(t["size"] * t["price"] for t in recent if t["side"] == "SELL")
            
            total = buy_volume + sell_volume
            if total > 0:
                self.wpi = (buy_volume - sell_volume) / total
            else:
                self.wpi = 0.0
                
        except Exception as e:
            logger.warning(f"WPI 計算錯誤: {e}")
            self.wpi = 0.0
    
    def get_data(self) -> Dict:
        """取得所有即時數據"""
        import time
        return {
            "price": self.current_price,
            "price_source": self.price_source,
            "price_age_seconds": time.time() - self.price_updated if self.price_updated > 0 else -1,
            "obi": self.obi,
            "wpi": self.wpi,
            "orderbook": self.orderbook,
            "recent_trades_count": len(self.recent_trades),
            "data_age_seconds": time.time() - self.last_update if self.last_update > 0 else -1,
            "source": "dydx_websocket"
        }
    
    async def disconnect(self):
        """斷開連線"""
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        if self._ws:
            await self._ws.close()
        
        if hasattr(self, '_session'):
            await self._session.close()
        
        logger.info("🔌 dYdX WebSocket 已斷開")


# ═══════════════════════════════════════════════════════════════════════════════
# dYdX API 客戶端
# ═══════════════════════════════════════════════════════════════════════════════

class DydxAPI:
    """dYdX v4 API 封裝"""
    
    # 🆕 v14.4: 嚴格速率控制 (100 requests / 10 sec = 最多 10/sec)
    # 實際上要留餘裕給其他組件，設定為最多 5 次/秒
    _last_api_call_time: float = 0.0
    _api_call_interval: float = 0.25  # 🔧 每次呼叫間隔 250ms (最多 4 次/秒)
    _api_call_count: int = 0
    _api_call_window_start: float = 0.0
    _api_calls_in_window: int = 0
    _max_calls_per_10s: int = 50  # 🔧 每 10 秒最多 50 次 (留一半給其他組件)
    
    def __init__(self, config: DydxConfig):
        self.config = config
        self.network_config = NETWORK_CONFIG.get(config.network, NETWORK_CONFIG["testnet"])
        
        # 從環境變數讀取
        self.address = os.getenv("DYDX_ADDRESS", "")  # 主帳戶 (資金所在)
        self.wallet_address = os.getenv("WALLET_ADDRESS", "")  # API 錢包 (簽名用)
        self.private_key = os.getenv("DYDX_PRIVATE_KEY", "")
        self.subaccount = int(os.getenv("DYDX_SUBACCOUNT_NUMBER", "0"))
        self.authenticator_id = int(os.getenv("DYDX_AUTHENTICATOR_ID", "0"))  # 🆕 Permissioned Key
        
        # 客戶端
        self.indexer: Optional[IndexerClient] = None
        self.node: Optional[NodeClient] = None
        self.wallet: Optional[Wallet] = None
        self.market_info: Dict = {}
    
    async def _rate_limit(self):
        """🆕 v14.4: 嚴格 API 速率控制"""
        import time
        now = time.time()
        
        # 1. 檢查 10 秒窗口內的呼叫次數
        if now - DydxAPI._api_call_window_start > 10.0:
            # 重置窗口
            DydxAPI._api_call_window_start = now
            DydxAPI._api_calls_in_window = 0
        
        # 如果已達到限制，等待直到窗口重置
        if DydxAPI._api_calls_in_window >= DydxAPI._max_calls_per_10s:
            wait_time = 10.0 - (now - DydxAPI._api_call_window_start) + 0.5
            if wait_time > 0:
                logger.warning(f"⏳ API 配額用盡，等待 {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)
                DydxAPI._api_call_window_start = time.time()
                DydxAPI._api_calls_in_window = 0
        
        # 2. 確保呼叫間隔
        elapsed = now - DydxAPI._last_api_call_time
        if elapsed < DydxAPI._api_call_interval:
            await asyncio.sleep(DydxAPI._api_call_interval - elapsed)
        
        # 更新計數
        DydxAPI._last_api_call_time = time.time()
        DydxAPI._api_calls_in_window += 1
        DydxAPI._api_call_count += 1
        
    async def connect(self) -> bool:
        """連接到 dYdX (帶 429 重試)"""
        max_retries = 5
        
        for attempt in range(max_retries):
            try:
                await self._rate_limit()  # 速率控制
                logger.info(f"🔗 連接 dYdX {self.config.network}...")
                
                # 初始化 Indexer 客戶端
                self.indexer = IndexerClient(self.network_config["rest_indexer"])
                
                # 取得市場資訊
                market_data = await self.indexer.markets.get_perpetual_markets(self.config.symbol)
                self.market_info = market_data.get("markets", {}).get(self.config.symbol, {})
                
                if not self.market_info:
                    logger.error(f"❌ 無法取得 {self.config.symbol} 市場資訊")
                    return False
                
                oracle_price = float(self.market_info.get("oraclePrice", 0))
                logger.info(f"✅ 連接成功! {self.config.symbol} 價格: ${oracle_price:,.2f}")
                
                # 如果有私鑰，且需要真實交易（非純 paper 或同步模式），初始化節點連接
                if self.private_key and (not self.config.paper_trading or self.config.sync_real_trading):
                    await self._init_node_client()
                
                return True
                
            except Exception as e:
                error_str = str(e)
                if "429" in error_str and attempt < max_retries - 1:
                    wait_time = 3 * (attempt + 1)  # 3s, 6s, 9s, 12s
                    logger.warning(f"⏳ dYdX 429 限速，等待 {wait_time}s 後重試 ({attempt + 1}/{max_retries})...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"❌ 連接失敗: {e}")
                    return False
        
        return False
    
    async def _init_node_client(self):
        """初始化節點客戶端 (用於真實下單) - 支援 Permissioned Keys"""
        try:
            from dydx_v4_client.network import make_mainnet, make_testnet, TESTNET
            
            if self.config.network == "mainnet":
                node_config = make_mainnet(
                    rest_indexer=self.network_config["rest_indexer"],
                    websocket_indexer=self.network_config["websocket_indexer"],
                    node_url=self.network_config["node_grpc"]
                )
            else:
                node_config = TESTNET
            
            self.node = await NodeClient.connect(node_config.node)
            
            # 處理私鑰
            private_key = self.private_key
            if private_key.startswith("0x"):
                private_key = private_key[2:]
            
            # 建立 API 錢包 (用於簽名)
            key_pair = KeyPair.from_hex(private_key)
            
            # 🆕 使用主帳戶的 account_number 和 sequence (Permissioned Keys 需要)
            try:
                main_account = await self.node.get_account(self.address)
                account_number = main_account.account_number
                account_sequence = main_account.sequence
                logger.info(f"📊 主帳戶 Account#{account_number}, Seq#{account_sequence}")
            except Exception as e:
                logger.warning(f"⚠️  無法取得主帳戶資訊: {e}")
                account_number = 0
                account_sequence = 0
            
            self.wallet = Wallet(
                key=key_pair,
                account_number=account_number,
                sequence=account_sequence
            )
            
            logger.info(f"🔑 API 錢包: {self.wallet.address}")
            logger.info(f"💰 主帳戶: {self.address}")
            if self.authenticator_id > 0:
                logger.info(f"🔐 Authenticator ID: {self.authenticator_id}")
            
        except Exception as e:
            logger.warning(f"⚠️  節點連接失敗 (Paper Trading 模式不需要): {e}")
    
    async def get_price(self) -> float:
        """
        取得當前價格
        
        🔧 v14.6.22: 改為 0.5 秒緩存，加快 Oracle Price 更新速度
        """
        try:
            # 🔧 v14.6.22: 0.5 秒緩存 (每秒最多 2 次 API 呼叫)
            now = time.time()
            if now - getattr(self, '_price_cache_time', 0) < 0.5:
                cached = getattr(self, '_price_cache', 0)
                if cached > 0:
                    return cached
            
            await self._rate_limit()  # 速率控制
            market = await self.indexer.markets.get_perpetual_markets(self.config.symbol)
            price = float(market.get("markets", {}).get(self.config.symbol, {}).get("oraclePrice", 0))
            
            # 更新緩存
            if price > 0:
                self._price_cache = price
                self._price_cache_time = now
            return price
        except Exception as e:
            logger.error(f"取得價格失敗: {e}")
            # 返回緩存值
            return getattr(self, '_price_cache', 0)
    
    async def get_best_bid_ask(self, retry_on_429: bool = True) -> Tuple[float, float]:
        """
        取得最佳買賣價 (Best Bid / Best Ask)
        
        🔧 v14.2: 增加 429 重試和緩存機制
        
        Returns:
            (best_bid, best_ask) - 最佳買價, 最佳賣價
        """
        max_retries = 3 if retry_on_429 else 1
        
        for attempt in range(max_retries):
            try:
                await self._rate_limit()  # 速率控制
                orderbook = await self.indexer.markets.get_perpetual_market_orderbook(self.config.symbol)
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])
                
                best_bid = float(bids[0].get("price", 0)) if bids else 0.0
                best_ask = float(asks[0].get("price", 0)) if asks else 0.0
                
                if best_bid > 0 and best_ask > 0:
                    return best_bid, best_ask
                    
            except Exception as e:
                error_str = str(e)
                if "429" in error_str and attempt < max_retries - 1:
                    wait_time = 1.5 * (attempt + 1)  # 1.5s, 3s, 4.5s
                    logger.warning(f"⏳ 429 限速，等待 {wait_time}s 後重試 ({attempt + 1}/{max_retries})...")
                    await asyncio.sleep(wait_time)
                    continue
                logger.error(f"取得 Bid/Ask 失敗: {e}")
                return 0.0, 0.0
        
        return 0.0, 0.0
    
    async def get_orderbook(self) -> Dict:
        """取得訂單簿"""
        try:
            return await self.indexer.markets.get_perpetual_market_orderbook(self.config.symbol)
        except Exception as e:
            logger.error(f"取得訂單簿失敗: {e}")
            return {}
    
    async def get_trades(self, limit: int = 100) -> List[Dict]:
        """取得最近交易"""
        try:
            response = await self.indexer.markets.get_perpetual_market_trades(
                self.config.symbol, limit=limit
            )
            return response.get("trades", [])
        except Exception as e:
            logger.error(f"取得交易失敗: {e}")
            return []
    
    async def get_candles(self, resolution: str = "1MIN", limit: int = 100) -> List[Dict]:
        """取得 K 線"""
        try:
            response = await self.indexer.markets.get_perpetual_market_candles(
                self.config.symbol,
                resolution=resolution,
                limit=limit
            )
            return response.get("candles", [])
        except Exception as e:
            logger.error(f"取得 K 線失敗: {e}")
            return []
    
    async def get_funding_rate(self) -> float:
        """取得資金費率"""
        try:
            response = await self.indexer.markets.get_perpetual_market_historical_funding(
                self.config.symbol, limit=1
            )
            fundings = response.get("historicalFunding", [])
            if fundings:
                return float(fundings[0].get("rate", 0))
            return 0.0
        except Exception as e:
            logger.error(f"取得資金費率失敗: {e}")
            return 0.0
    
    async def get_account_balance(self) -> float:
        """
        取得帳戶餘額
        
        🔧 v14.4: 增加 5 秒緩存減少 API 呼叫
        """
        if self.config.paper_trading:
            return self.config.paper_initial_balance
        
        try:
            if not self.address:
                return 0.0
            
            # 🔧 v14.4: 5 秒緩存
            now = time.time()
            if now - getattr(self, '_balance_cache_time', 0) < 5.0:
                cached = getattr(self, '_balance_cache', 0)
                if cached > 0:
                    return cached
            
            await self._rate_limit()  # 速率控制
            response = await self.indexer.account.get_subaccount(self.address, self.subaccount)
            balance = float(response.get("subaccount", {}).get("equity", 0))
            
            # 更新緩存
            self._balance_cache = balance
            self._balance_cache_time = now
            return balance
        except Exception as e:
            logger.error(f"取得餘額失敗: {e}")
            # 返回緩存值
            return getattr(self, '_balance_cache', 0)
    
    async def get_positions(self) -> List[Dict]:
        """
        取得持倉
        
        🔧 v14.4: 增加 3 秒緩存減少 API 呼叫
        """
        if self.config.paper_trading and not self.config.sync_real_trading:
            return []
        
        try:
            if not self.address:
                return []
            
            # 🔧 v14.4: 3 秒緩存
            now = time.time()
            if now - getattr(self, '_positions_cache_time', 0) < 3.0:
                cached = getattr(self, '_positions_cache', None)
                if cached is not None:
                    return cached
            
            await self._rate_limit()  # 速率控制
            response = await self.indexer.account.get_subaccount_perpetual_positions(
                self.address, self.subaccount
            )
            positions = response.get("positions", [])
            
            # 更新緩存
            self._positions_cache = positions
            self._positions_cache_time = now
            return positions
        except Exception as e:
            logger.error(f"取得持倉失敗: {e}")
            # 返回緩存值
            return getattr(self, '_positions_cache', [])

    async def get_positions_fresh(self) -> List[Dict]:
        """
        取得持倉 (強制更新，不使用快取)

        用於下單後確認持倉，避免快取空窗造成誤判。
        """
        if self.config.paper_trading and not self.config.sync_real_trading:
            return []
        if not self.address:
            return []

        try:
            await self._rate_limit()
            response = await self.indexer.account.get_subaccount_perpetual_positions(
                self.address, self.subaccount
            )
            positions = response.get("positions", [])
            self._positions_cache = positions
            self._positions_cache_time = time.time()
            return positions
        except Exception as e:
            logger.error(f"取得持倉失敗 (fresh): {e}")
            return getattr(self, '_positions_cache', [])

    async def get_open_orders(
        self,
        status: str | list[str] = ("OPEN", "UNTRIGGERED"),
        symbol: str | None = None,
    ) -> list[dict]:
        """
        取得未成交掛單 (OPEN/UNTRIGGERED)

        🔧 v14.6.45: 增加 2 秒緩存減少 API 呼叫
        """
        if self.config.paper_trading and not self.config.sync_real_trading:
            return []
        if not self.address or not self.indexer:
            return []

        try:
            now = time.time()
            cache_ttl = 2.0
            cached = getattr(self, "_open_orders_cache", None)
            cached_time = getattr(self, "_open_orders_cache_time", 0.0)
            if cached is not None and (now - cached_time) < cache_ttl:
                if symbol:
                    return [o for o in cached if (o.get("market") or o.get("ticker")) == symbol]
                return cached

            await self._rate_limit()

            statuses = status if isinstance(status, list) else [status]
            orders: list[dict] = []
            for st in statuses:
                try:
                    response = await self.indexer.account.get_subaccount_orders(
                        self.address,
                        self.subaccount,
                        status=st,
                    )
                    orders.extend(self._extract_orders(response))
                except Exception:
                    continue

            self._open_orders_cache = orders
            self._open_orders_cache_time = now

            if symbol:
                orders = [o for o in orders if (o.get("market") or o.get("ticker")) == symbol]
            return orders
        except Exception as e:
            logger.error(f"取得未成交掛單失敗: {e}")
            return []

    @staticmethod
    def _extract_orders(response) -> list[dict]:
        """Handle both list and dict responses from the indexer client."""
        if response is None:
            return []
        if isinstance(response, list):
            return response
        if isinstance(response, dict):
            if isinstance(response.get("orders"), list):
                return response.get("orders", [])
            if isinstance(response.get("data"), list):
                return response.get("data", [])
        return []
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 🆕 Aggressive Maker 下單系統 (零滑點策略)
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def place_fast_order(
        self, 
        side: str, 
        size: float,
        maker_timeout: float = 5.0,
        fallback_to_ioc: bool = True
    ) -> Tuple[Optional[str], float]:
        """
        🆕 快速成交策略 - 優先 Maker，超時自動 IOC
        
        📊 策略:
        1. 先嘗試 Aggressive Maker (1次，5秒超時)
        2. 若失敗，自動改用 IOC 市價單立即成交
        
        💰 手續費 (dYdX v4, % of notional):
        - Maker: maker_fee_pct
        - IOC/Taker: taker_fee_pct
        ⚠️ IOC 可能有滑點 (設定 0.5% 容忍度)
        
        Args:
            side: "LONG" 或 "SHORT"
            size: BTC 數量
            maker_timeout: Maker 超時秒數 (預設 5 秒)
            fallback_to_ioc: 是否自動切換 IOC (預設 True)
        
        Returns:
            (交易哈希, 成交價格)
        """
        if not self.node or not self.wallet:
            logger.error("❌ 節點或錢包未連接")
            return None, 0.0
        
        # 🔧 每次下單前刷新 sequence (防止 sequence 過期)
        await self._refresh_sequence()
        
        # 🔧 v14.9.5: 如果 maker_timeout <= 0，直接跳過 Maker，用 IOC
        if maker_timeout <= 0:
            logger.info(f"📤 直接使用 IOC 市價單 (跳過 Maker)...")
            ioc_result = await self._place_ioc_order(side, size)
            if ioc_result[0] is None:
                logger.error(f"❌ IOC 失敗! tx={ioc_result[0]}, price={ioc_result[1]}")
            return ioc_result
        
        # 1️⃣ 先嘗試 Maker (只試一次，快速失敗)
        logger.info(f"📤 嘗試 Maker 掛單 ({maker_timeout}s 超時)...")
        result = await self._try_place_order(
            side=side,
            size=size,
            timeout_seconds=maker_timeout,
            attempt=1,
            max_attempts=1
        )
        
        if result[0] is not None:
            logger.info(f"✅ Maker 成交! 費率: {self.config.maker_fee_pct}%")
            return result
        
        # 🔧 v14.9.4: 記錄 Maker 失敗原因
        logger.warning(f"⚠️ Maker 下單失敗 (tx={result[0]}, price={result[1]})")
        
        # 2️⃣ Maker 失敗，改用 IOC 市價單
        if fallback_to_ioc:
            logger.info(f"🔄 Maker 超時，改用 IOC 市價單...")
            ioc_result = await self._place_ioc_order(side, size)
            if ioc_result[0] is None:
                logger.error(f"❌ IOC 也失敗! tx={ioc_result[0]}, price={ioc_result[1]}")
            return ioc_result
        else:
            logger.warning(f"⚠️ 不允許 fallback_to_ioc，直接返回失敗")
        
        return None, 0.0
    
    async def _refresh_sequence(self):
        """🔧 刷新主帳戶的 sequence (Permissioned Keys 需要)"""
        try:
            main_account = await self.node.get_account(self.address)
            old_seq = self.wallet.sequence
            self.wallet.sequence = main_account.sequence
            if old_seq != self.wallet.sequence:
                logger.info(f"🔄 Sequence 已更新: {old_seq} → {self.wallet.sequence}")
        except Exception as e:
            logger.warning(f"⚠️ 無法刷新 sequence: {e}")
    
    def _check_tx_success(self, transaction) -> bool:
        """
        🔧 v14.4: 檢查交易是否成功提交
        
        dYdX 返回的 transaction 可能包含 error code
        如果有 error code 且不是 0，表示失敗
        """
        tx_str = str(transaction)

        # 保存最近一次交易錯誤資訊，供上層做 backoff / 修復策略使用
        try:
            if not hasattr(self, "_last_tx_error"):
                self._last_tx_error = None
        except Exception:
            pass
        # 檢查是否有 error code
        if "code:" in tx_str:
            # code: 0 表示成功，其他都是失敗
            if "code: 0" in tx_str or "code:0" in tx_str:
                try:
                    self._last_tx_error = None
                except Exception:
                    pass
                return True
            # 有其他 error code
            try:
                import re
                m_code = re.search(r"\bcode:\s*(\d+)", tx_str)
                m_space = re.search(r"\bcodespace:\s*\"?([A-Za-z0-9_\-]+)\"?", tx_str)
                m_raw = re.search(r"\braw_log:\s*\"([^\"]*)\"", tx_str)
                self._last_tx_error = {
                    "code": int(m_code.group(1)) if m_code else None,
                    "codespace": m_space.group(1) if m_space else None,
                    "raw_log": m_raw.group(1) if m_raw else None,
                    "tx_excerpt": tx_str[:400],
                }
            except Exception:
                try:
                    self._last_tx_error = {"tx_excerpt": tx_str[:400]}
                except Exception:
                    pass
            logger.warning(f"⚠️ 交易失敗: {tx_str[:200]}...")
            return False
        # 沒有 code 欄位，假設成功
        try:
            self._last_tx_error = None
        except Exception:
            pass
        return True

    def get_last_tx_error(self) -> dict:
        """回傳最近一次交易失敗的錯誤資訊（若無則回傳空 dict）。"""
        try:
            return dict(getattr(self, "_last_tx_error", None) or {})
        except Exception:
            return {}
    
    async def _handle_tx_result(self, transaction) -> bool:
        """
        🔧 v14.4: 處理交易結果，管理 sequence
        
        Returns:
            True: 交易成功，sequence 已遞增
            False: 交易失敗，已重新同步 sequence
        """
        if self._check_tx_success(transaction):
            self.wallet.sequence += 1
            return True
        else:
            await self._refresh_sequence()
            return False
    
    async def _place_ioc_order(
        self, 
        side: str, 
        size: float
    ) -> Tuple[Optional[str], float]:
        """
        IOC 市價單 - 立即成交或取消
        
        使用 IOC (Immediate-Or-Cancel) 確保立即成交
        價格設定為對手盤最佳價 ± 滑點容忍度
        """
        try:
            best_bid, best_ask = await self.get_best_bid_ask()
            if best_bid <= 0 or best_ask <= 0:
                logger.error("❌ 無法取得價格")
                return None, 0.0
            
            # 🔧 v14.9.4: 滑點增加到 0.4% (原 0.3% 偶發未成交)
            slippage = 0.004  # 0.4% 滑點容忍
            if side == "LONG":
                # 買入：用 Ask 價 + 滑點
                limit_price = best_ask * (1 + slippage)
                order_side = Order.Side.SIDE_BUY
                logger.info(f"🟢 IOC 買入: ${limit_price:,.2f} (Ask ${best_ask:,.2f} + 0.4%)")
            else:
                # 賣出：用 Bid 價 - 滑點
                limit_price = best_bid * (1 - slippage)
                order_side = Order.Side.SIDE_SELL
                logger.info(f"🔴 IOC 賣出: ${limit_price:,.2f} (Bid ${best_bid:,.2f} - 0.4%)")
            
            # 準備訂單
            market_data = await self.indexer.markets.get_perpetual_markets(self.config.symbol)
            market_info = market_data.get("markets", {}).get(self.config.symbol, {})
            market = Market(market_info)
            
            client_id = random.randint(0, MAX_CLIENT_ID)
            order_id = market.order_id(
                self.address,
                self.subaccount,
                client_id,
                OrderFlags.SHORT_TERM
            )
            
            current_block = await self.node.latest_block_height()
            good_til_block = current_block + 10
            
            # IOC 訂單
            new_order = market.order(
                order_id=order_id,
                order_type=OrderType.LIMIT,
                side=order_side,
                size=size,
                price=limit_price,
                time_in_force=Order.TimeInForce.TIME_IN_FORCE_IOC,  # 🔑 IOC!
                reduce_only=False,
                good_til_block=good_til_block,
            )
            
            # 提交
            logger.info(f"📤 提交 IOC 訂單: {side} {size:.4f} BTC @ ${limit_price:,.2f}")
            
            if self.authenticator_id > 0:
                tx_options = TxOptions(
                    authenticators=[self.authenticator_id],
                    sequence=self.wallet.sequence,
                    account_number=self.wallet.account_number,
                )
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                    tx_options=tx_options,
                )
            else:
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                )
            
            # 🔧 v14.4: 只有成功提交才遞增 sequence
            tx_str = str(transaction)
            logger.info(f"📝 Transaction 原始返回: {tx_str[:200]}...")  # 添加調試
            if "code:" in tx_str and "code: 0" not in tx_str:
                logger.warning(f"⚠️ IOC 交易提交失敗，重新同步 sequence... 完整返回: {tx_str}")
                await self._refresh_sequence()
                return None, 0.0
            
            self.wallet.sequence += 1
            logger.info(f"📝 IOC 訂單已提交: {transaction}")
            
            # 等待成交 (IOC 應該很快)
            is_filled = await self._wait_for_fill(
                order_id=client_id,
                side=side,
                size=size,
                timeout=3.0  # IOC 只等 3 秒
            )
            
            if is_filled:
                # 🔧 IOC 成交價 ≈ 對手盤價格 (best_ask 買入, best_bid 賣出)
                fill_price = best_ask if side == "LONG" else best_bid
                logger.info(
                    f"✅ IOC 成交! 價格: ${fill_price:,.2f} | "
                    f"可能為 Taker 費率 {self.config.taker_fee_pct}%"
                )
                return str(transaction), fill_price
            else:
                # 🔧 v14.9.4: 檢查是否真的有持倉（可能是 _wait_for_fill 的緩存問題）
                await asyncio.sleep(0.5)  # 額外等待
                positions = await self.get_positions()
                for pos in positions:
                    if pos.get("market") == self.config.symbol and pos.get("status") == "OPEN":
                        pos_size = float(pos.get("size", 0))
                        if abs(pos_size) >= size * 0.99:
                            # IOC 成交價 ≈ 對手盤價格
                            fill_price = best_ask if side == "LONG" else best_bid
                            logger.info(f"✅ IOC 延遲確認成交! 價格: ${fill_price:,.2f}")
                            return str(transaction), fill_price
                logger.warning(f"⚠️ IOC 未成交 (訂單已提交但無持倉)")
                return None, 0.0
            
        except Exception as e:
            logger.error(f"❌ IOC 下單失敗: {e}")
            import traceback
            traceback.print_exc()
            return None, 0.0

    async def place_aggressive_limit_order(
        self, 
        side: str, 
        size: float,
        timeout_seconds: float = 10.0,
        price_offset: Optional[float] = None,
        max_retries: int = 3
    ) -> Tuple[Optional[str], float]:
        """
        積極掛單 (Aggressive Maker) - 零滑點策略 + 自動重試
        
        🎯 核心思想:
        - 做多 (LONG): 掛在 Best Bid + offset (搶第一買單)
        - 做空 (SHORT): 掛在 Best Ask - offset (搶第一賣單)
        - 超時未成交 → 取消訂單並重試 (更激進價格)
        
        📊 動態 offset 策略:
        - 第1次: 90% spread (離對手盤 $0.1-0.5)
        - 第2次: 95% spread (更激進)
        - 第3次: 99% spread (幾乎貼著對手盤)
        
        Args:
            side: "LONG" 或 "SHORT"
            size: BTC 數量
            timeout_seconds: 每次嘗試超時秒數 (預設 10 秒)
            price_offset: 價格偏移量 (None=動態計算)
            max_retries: 最大重試次數 (預設 3)
        
        Returns:
            (交易哈希, 成交價格) - 失敗返回 (None, 0.0)
        """
        if not self.node or not self.wallet:
            logger.error("❌ 節點或錢包未連接，無法下單")
            return None, 0.0
        
        # 🔧 v14.6.6: 檢查最小訂單數量 (dYdX BTC-USD 最小 0.0001)
        MIN_ORDER_SIZE = 0.0001
        if size < MIN_ORDER_SIZE:
            logger.error(f"❌ 訂單數量 {size:.6f} BTC 低於最小訂單數量 {MIN_ORDER_SIZE} BTC")
            return None, 0.0
        
        # 🆕 重試機制: 每次更激進
        for attempt in range(max_retries):
            result = await self._try_place_order(
                side=side,
                size=size,
                timeout_seconds=timeout_seconds,
                attempt=attempt + 1,
                max_attempts=max_retries
            )
            
            if result[0] is not None:  # 成功
                return result
            
            if attempt < max_retries - 1:
                logger.info(f"🔄 重試 {attempt + 2}/{max_retries} (更激進價格)...")
        
        return None, 0.0
    
    async def _try_place_order(
        self,
        side: str,
        size: float,
        timeout_seconds: float,
        attempt: int,
        max_attempts: int,
        best_bid: Optional[float] = None,
        best_ask: Optional[float] = None,
        limit_price: Optional[float] = None
    ) -> Tuple[Optional[str], float]:
        """單次下單嘗試"""
        try:
            # 1️⃣ 取得最佳買賣價
            if best_bid is None or best_ask is None or best_bid <= 0 or best_ask <= 0:
                best_bid, best_ask = await self.get_best_bid_ask()
                if best_bid <= 0 or best_ask <= 0:
                    logger.error("❌ 無法取得 Bid/Ask 價格")
                    return None, 0.0
            
            spread = best_ask - best_bid
            
            # 確保最小安全邊際 = spread 的 20% 或 $1，取較大值
            min_safety = max(spread * 0.20, 1.0)
            custom_price = limit_price is not None
            
            # 2️⃣ 動態計算 offset (根據重試次數越來越激進)
            # 🔧 修復: 限制最大 80% spread，確保安全邊際至少 20% spread
            # 第1次: 50% spread (保守)
            # 第2次: 65% spread
            # 第3次: 80% spread (最激進，但保留 20% 安全邊際)
            if not custom_price:
                aggression = 0.50 + (attempt - 1) * 0.15  # 0.50, 0.65, 0.80
                price_offset = spread * aggression
                logger.info(
                    f"📊 [{attempt}/{max_attempts}] Bid: ${best_bid:,.2f} | Ask: ${best_ask:,.2f} | Spread: ${spread:.2f} | "
                    f"Offset: ${price_offset:.2f} (激進度:{aggression:.0%}) | 安全邊際: ${min_safety:.2f}"
                )
            else:
                price_offset = 0.0
                logger.info(
                    f"📊 [{attempt}/{max_attempts}] Bid: ${best_bid:,.2f} | Ask: ${best_ask:,.2f} | Spread: ${spread:.2f} | "
                    f"Offset: 自訂 | 安全邊際: ${min_safety:.2f}"
                )
            
            # 3️⃣ 計算掛單價格 (Aggressive Maker)
            # 🔑 確保不穿越對手盤，否則 POST_ONLY 會被拒絕
            # 🔧 安全邊際改為 min_safety (至少 $1 或 20% spread)
            if side == "LONG":
                # 做多: 掛在 Best Bid + offset，但必須 < Best Ask - 安全邊際
                if custom_price:
                    max_price = best_ask - min_safety
                    if limit_price >= max_price:
                        limit_price = max_price
                        logger.warning(f"⚠️ 調整為 ${limit_price:,.2f} (Best Ask - ${min_safety:.2f})")
                    price_offset = max(0.0, limit_price - best_bid)
                else:
                    limit_price = best_bid + price_offset
                    max_price = best_ask - min_safety
                    if limit_price >= max_price:
                        limit_price = max_price
                        logger.warning(f"⚠️ 調整為 ${limit_price:,.2f} (Best Ask - ${min_safety:.2f})")
                order_side = Order.Side.SIDE_BUY
                logger.info(f"🟢 LONG 掛單價: ${limit_price:,.2f} (距 Ask ${best_ask - limit_price:.2f})")
            else:
                # 做空: 掛在 Best Ask - offset，但必須 > Best Bid + 安全邊際
                if custom_price:
                    min_price = best_bid + min_safety
                    if limit_price <= min_price:
                        limit_price = min_price
                        logger.warning(f"⚠️ 調整為 ${limit_price:,.2f} (Best Bid + ${min_safety:.2f})")
                    price_offset = max(0.0, best_ask - limit_price)
                else:
                    limit_price = best_ask - price_offset
                    min_price = best_bid + min_safety
                    if limit_price <= min_price:
                        limit_price = min_price
                        logger.warning(f"⚠️ 調整為 ${limit_price:,.2f} (Best Bid + ${min_safety:.2f})")
                order_side = Order.Side.SIDE_SELL
                logger.info(f"🔴 SHORT 掛單價: ${limit_price:,.2f} (距 Bid ${limit_price - best_bid:.2f})")
            
            # 4️⃣ 準備訂單
            market_data = await self.indexer.markets.get_perpetual_markets(self.config.symbol)
            market_info = market_data.get("markets", {}).get(self.config.symbol, {})
            market = Market(market_info)
            
            client_id = random.randint(0, MAX_CLIENT_ID)
            order_id = market.order_id(
                self.address,
                self.subaccount,
                client_id,
                OrderFlags.SHORT_TERM
            )
            
            current_block = await self.node.latest_block_height()
            # 短期有效 (約 10 個區塊 ≈ 15 秒)
            good_til_block = current_block + 10
            
            # 4️⃣ 建立限價單 (Maker Only)
            # 🔑 TIME_IN_FORCE_POST_ONLY = 只做 Maker，不會吃單
            new_order = market.order(
                order_id=order_id,
                order_type=OrderType.LIMIT,  # 限價單
                side=order_side,
                size=size,
                price=limit_price,
                time_in_force=Order.TimeInForce.TIME_IN_FORCE_POST_ONLY,  # 🔑 Maker Only!
                reduce_only=False,
                good_til_block=good_til_block,
            )
            
            # 5️⃣ 提交訂單
            logger.info(f"📤 提交 Maker 限價單: {side} {size:.4f} BTC @ ${limit_price:,.2f}")
            
            if self.authenticator_id > 0:
                tx_options = TxOptions(
                    authenticators=[self.authenticator_id],
                    sequence=self.wallet.sequence,
                    account_number=self.wallet.account_number,
                )
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                    tx_options=tx_options,
                )
            else:
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                )
            
            # 🔧 v14.4: 只有成功提交才遞增 sequence
            tx_str = str(transaction)
            if "code:" in tx_str and "code: 0" not in tx_str:
                # 交易失敗 (有 error code)，不遞增 sequence
                logger.warning(f"⚠️ 交易提交失敗，重新同步 sequence...")
                await self._refresh_sequence()
                return None, 0.0
            
            self.wallet.sequence += 1
            logger.info(f"📝 訂單已掛出! 交易哈希: {transaction}")
            
            # 6️⃣ 等待成交 (輪詢檢查)
            is_filled = await self._wait_for_fill(
                order_id=client_id,
                side=side,
                size=size,
                timeout=timeout_seconds
            )
            
            if is_filled:
                # 🔧 對於 POST_ONLY Maker 單，成交價 = 掛單價
                logger.info(f"✅ 成交! 價格: ${limit_price:,.2f} (零滑點)")
                return str(transaction), limit_price
            else:
                logger.warning(f"⏱️ 超時未成交，訂單已自動過期")
                return None, 0.0
            
        except Exception as e:
            logger.error(f"❌ 掛單失敗: {e}")
            import traceback
            traceback.print_exc()
            return None, 0.0
    
    async def place_take_profit_order(
        self,
        side: str,
        size: float,
        tp_price: float,
        time_to_live_seconds: int = 3600  # 預設 1 小時有效
    ) -> Tuple[Optional[str], int]:
        """
        🆕 掛止盈限價單 (GTT - Good Till Time)
        
        掛一個遠離當前價格的限價單作為止盈單
        - 不等待成交，立即返回
        
        🔧 v14.6.16: 增加 Oracle Price 驗證
        - LONG: TP 價格必須 > Oracle Price (期待價格上漲)
        - SHORT: TP 價格必須 < Oracle Price (期待價格下跌)
        
        🔧 v14.6.18: 增加持倉數量校驗
        - 確保 TP 單數量不超過實際持倉 (防止開反向倉)
        - 由於 dYdX v4 限制，GTT 訂單無法使用 reduce_only
        
        Args:
            side: 當前持倉方向 "LONG" 或 "SHORT" (會反向掛單)
            size: BTC 數量
            tp_price: 止盈價格
            time_to_live_seconds: 有效時間 (秒)
        
        Returns:
            (交易哈希, order_id) - 失敗返回 (None, 0)
        """
        if not self.node or not self.wallet:
            logger.error("❌ 節點或錢包未連接")
            return None, 0
        
        try:
            # 🆕 v14.6.18: 校驗持倉數量，防止 TP 單超過持倉造成開反向倉
            positions = await self.get_positions()
            btc_position = None
            for p in positions:
                if p.get('market') == 'BTC-USD' and abs(float(p.get('size', 0))) > 0.00001:
                    btc_position = p
                    break
            if btc_position:
                actual_size = abs(float(btc_position.get('size', 0)))
                if size > actual_size:
                    logger.warning(f"⚠️ TP 數量 {size:.4f} > 持倉 {actual_size:.4f}，自動調整為持倉數量")
                    size = actual_size
            
            # 🔧 v14.6.16: 取得當前 Oracle Price 並驗證 TP 價格
            oracle_price = await self.get_price()
            original_tp = tp_price
            
            if side == "LONG":
                # LONG TP: 價格必須 > Oracle Price (掛賣單等待價格上漲)
                if tp_price <= oracle_price:
                    # 自動調整為 Oracle Price + 最小偏移量 (0.1%)
                    tp_price = oracle_price * 1.001
                    logger.warning(f"⚠️ TP 價格 ${original_tp:,.2f} <= Oracle ${oracle_price:,.2f}，自動調整為 ${tp_price:,.2f}")
            else:
                # SHORT TP: 價格必須 < Oracle Price (掛買單等待價格下跌)
                if tp_price >= oracle_price:
                    # 自動調整為 Oracle Price - 最小偏移量 (0.1%)
                    tp_price = oracle_price * 0.999
                    logger.warning(f"⚠️ TP 價格 ${original_tp:,.2f} >= Oracle ${oracle_price:,.2f}，自動調整為 ${tp_price:,.2f}")
            
            # 平倉方向相反
            if side == "LONG":
                # 平多 = 賣出
                order_side = Order.Side.SIDE_SELL
                logger.info(f"📈 掛止盈賣單: {size:.4f} BTC @ ${tp_price:,.2f} (Oracle: ${oracle_price:,.2f})")
            else:
                # 平空 = 買入
                order_side = Order.Side.SIDE_BUY
                logger.info(f"📉 掛止盈買單: {size:.4f} BTC @ ${tp_price:,.2f} (Oracle: ${oracle_price:,.2f})")
            
            market_data = await self.indexer.markets.get_perpetual_markets(self.config.symbol)
            market_info = market_data.get("markets", {}).get(self.config.symbol, {})
            market = Market(market_info)
            
            client_id = random.randint(0, MAX_CLIENT_ID)
            order_id = market.order_id(
                self.address,
                self.subaccount,
                client_id,
                OrderFlags.LONG_TERM  # 🔧 v14.6: 長期訂單 (不用 reduce_only)
            )
            
            # 計算過期時間 (Unix timestamp)
            good_til_timestamp = int(datetime.now().timestamp()) + time_to_live_seconds
            
            # 🔧 v14.6: 不用 reduce_only，直接掛反向限價單
            # 當價格到達時會自動成交，效果等同於止盈
            new_order = market.order(
                order_id=order_id,
                order_type=OrderType.LIMIT,
                side=order_side,
                size=size,
                price=tp_price,
                time_in_force=Order.TimeInForce.TIME_IN_FORCE_UNSPECIFIED,  # GTT
                reduce_only=False,  # 🔧 v14.6: 不用 reduce_only (dYdX v4 限制)
                good_til_block=0,
                good_til_block_time=good_til_timestamp,
            )
            
            logger.info(f"📤 提交止盈單 (GTT): 平{side} {size:.4f} BTC @ ${tp_price:,.2f} | 有效至 {datetime.fromtimestamp(good_til_timestamp)}")
            
            if self.authenticator_id > 0:
                tx_options = TxOptions(
                    authenticators=[self.authenticator_id],
                    sequence=self.wallet.sequence,
                    account_number=self.wallet.account_number,
                )
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                    tx_options=tx_options,
                )
            else:
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                )
            
            # 🔧 v14.4: 檢查交易結果
            # 🔧 v14.6.13: account sequence mismatch 時，refresh 後自動重試一次
            if not await self._handle_tx_result(transaction):
                logger.warning("⚠️ 止盈單提交失敗，刷新 sequence 後重試一次...")
                if self.authenticator_id > 0:
                    retry_tx_options = TxOptions(
                        authenticators=[self.authenticator_id],
                        sequence=self.wallet.sequence,
                        account_number=self.wallet.account_number,
                    )
                    transaction = await self.node.place_order(
                        wallet=self.wallet,
                        order=new_order,
                        tx_options=retry_tx_options,
                    )
                else:
                    transaction = await self.node.place_order(
                        wallet=self.wallet,
                        order=new_order,
                    )
                if not await self._handle_tx_result(transaction):
                    return None, 0
            
            logger.info(f"✅ 止盈單已掛! ID: {client_id}")
            return str(transaction), client_id
            
        except Exception as e:
            logger.error(f"❌ 掛止盈單失敗: {e}")
            import traceback
            traceback.print_exc()
            return None, 0
    
    async def place_stop_loss_order(
        self,
        side: str,
        size: float,
        stop_price: float,
        time_to_live_seconds: int = 3600  # 預設 1 小時有效
    ) -> Tuple[Optional[str], int]:
        """
        🆕 v12.13: 掛止損單 (Stop Market Order)
        
        使用 dYdX v4 原生條件單：
        - 當 Oracle Price 達到 stop_price 時，自動觸發市價平倉
        
        🔧 v14.6.18: 增加持倉數量校驗
        - 確保 SL 單數量不超過實際持倉 (防止開反向倉)
        
        Args:
            side: 當前持倉方向 "LONG" 或 "SHORT" (會反向平倉)
            size: BTC 數量
            stop_price: 止損觸發價格
            time_to_live_seconds: 有效時間 (秒)
        
        Returns:
            (交易哈希, order_id) - 失敗返回 (None, 0)
        """
        if not self.node or not self.wallet:
            logger.error("❌ 節點或錢包未連接")
            return None, 0
        
        # 🔧 v14.6.6: 檢查最小訂單數量
        MIN_ORDER_SIZE = 0.0001
        if size < MIN_ORDER_SIZE:
            logger.error(f"❌ 止損單數量 {size:.6f} BTC 低於最小訂單數量 {MIN_ORDER_SIZE} BTC")
            return None, 0
        
        try:
            # 🆕 v14.6.18: 校驗持倉數量，防止 SL 單超過持倉造成開反向倉
            positions = await self.get_positions()
            btc_position = None
            for p in positions:
                if p.get('market') == 'BTC-USD' and abs(float(p.get('size', 0))) > 0.00001:
                    btc_position = p
                    break
            if btc_position:
                actual_size = abs(float(btc_position.get('size', 0)))
                if size > actual_size:
                    logger.warning(f"⚠️ SL 數量 {size:.4f} > 持倉 {actual_size:.4f}，自動調整為持倉數量")
                    size = actual_size
            
            # 平倉方向相反
            if side == "LONG":
                # 平多 = 賣出 (止損在下方)
                order_side = Order.Side.SIDE_SELL
                # LONG 止損：價格下跌到 stop_price 時觸發 (Oracle <= stop_price)
                condition_type = Order.ConditionType.CONDITION_TYPE_STOP_LOSS
                logger.info(f"📉 掛止損賣單: {size:.4f} BTC @ ${stop_price:,.2f} (觸發條件: Oracle <= ${stop_price:,.2f})")
            else:
                # 平空 = 買入 (止損在上方)
                order_side = Order.Side.SIDE_BUY
                # SHORT 止損：價格上漲到 stop_price 時觸發 (Oracle >= stop_price)
                condition_type = Order.ConditionType.CONDITION_TYPE_STOP_LOSS
                logger.info(f"📈 掛止損買單: {size:.4f} BTC @ ${stop_price:,.2f} (觸發條件: Oracle >= ${stop_price:,.2f})")
            
            market_data = await self.indexer.markets.get_perpetual_markets(self.config.symbol)
            market_info = market_data.get("markets", {}).get(self.config.symbol, {})
            market = Market(market_info)
            
            client_id = random.randint(0, MAX_CLIENT_ID)
            order_id = market.order_id(
                self.address,
                self.subaccount,
                client_id,
                OrderFlags.CONDITIONAL  # 🔑 使用條件單標記
            )
            
            # 計算過期時間 (Unix timestamp)
            good_til_timestamp = int(datetime.now().timestamp()) + time_to_live_seconds
            
            # 🔧 計算 trigger subticks (dYdX 使用 subticks 而非 price)
            trigger_subticks = market.calculate_subticks(stop_price)
            
            # 🆕 v14.6.38: 止損限價加滑點容差，避免快速行情時成交過差
            # LONG 止損 = 賣出 → 限價要低於觸發價（給滑點空間）
            # SHORT 止損 = 買入 → 限價要高於觸發價（給滑點空間）
            SLIPPAGE_BUFFER_PCT = 0.15  # 0.15% 滑點容差
            if side == "LONG":
                # 賣出限價 = 觸發價 - 0.15% (保證能成交)
                limit_price = stop_price * (1 - SLIPPAGE_BUFFER_PCT / 100)
            else:
                # 買入限價 = 觸發價 + 0.15% (保證能成交)
                limit_price = stop_price * (1 + SLIPPAGE_BUFFER_PCT / 100)
            logger.info(f"   📊 [v14.6.38] 止損觸發價: ${stop_price:,.2f} → 限價: ${limit_price:,.2f} (±{SLIPPAGE_BUFFER_PCT}%)")
            
            # 🆕 dYdX v4 條件單 (Stop Loss)
            # 使用 condition_type + conditional_order_trigger_subticks
            # 🔧 v14.6: 不用 reduce_only (dYdX v4 限制)
            new_order = market.order(
                order_id=order_id,
                order_type=OrderType.LIMIT,  # 條件單觸發後變限價單
                side=order_side,
                size=size,
                price=limit_price,  # 🔧 v14.6.38: 使用有滑點容差的限價
                time_in_force=Order.TimeInForce.TIME_IN_FORCE_UNSPECIFIED,  # GTT 效果
                reduce_only=False,  # 🔧 v14.6: 不用 reduce_only
                good_til_block=0,  # GTT 不用 block
                good_til_block_time=good_til_timestamp,
                condition_type=condition_type,  # 🔑 STOP_LOSS 條件
                conditional_order_trigger_subticks=trigger_subticks,  # 🔑 觸發 subticks
            )
            
            logger.info(f"📤 提交止損單: 平{side} {size:.4f} BTC | 觸發: ${stop_price:,.2f} | 有效至 {datetime.fromtimestamp(good_til_timestamp)}")
            
            if self.authenticator_id > 0:
                tx_options = TxOptions(
                    authenticators=[self.authenticator_id],
                    sequence=self.wallet.sequence,
                    account_number=self.wallet.account_number,
                )
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                    tx_options=tx_options,
                )
            else:
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                )
            
            # 🔧 v14.4: 檢查交易結果
            # 🔧 v14.6.13: account sequence mismatch 時，refresh 後自動重試一次
            if not await self._handle_tx_result(transaction):
                logger.warning("⚠️ 止損單提交失敗，刷新 sequence 後重試一次...")
                if self.authenticator_id > 0:
                    retry_tx_options = TxOptions(
                        authenticators=[self.authenticator_id],
                        sequence=self.wallet.sequence,
                        account_number=self.wallet.account_number,
                    )
                    transaction = await self.node.place_order(
                        wallet=self.wallet,
                        order=new_order,
                        tx_options=retry_tx_options,
                    )
                else:
                    transaction = await self.node.place_order(
                        wallet=self.wallet,
                        order=new_order,
                    )
                if not await self._handle_tx_result(transaction):
                    return None, 0
            
            logger.info(f"✅ 止損單已掛! ID: {client_id}")
            return str(transaction), client_id
            
        except Exception as e:
            logger.error(f"❌ 掛止損單失敗: {e}")
            import traceback
            traceback.print_exc()
            return None, 0
    
    async def cancel_order(
        self, 
        client_id: int, 
        order_type: str = "LONG_TERM",
        good_til_block_time: int = 0
    ) -> bool:
        """
        🆕 取消訂單
        
        Args:
            client_id: 訂單 client ID
            order_type: 訂單類型 - "SHORT_TERM", "LONG_TERM", "CONDITIONAL"
            good_til_block_time: 訂單的 goodTilBlockTime (必須 >= 原訂單)
        
        Returns:
            是否成功取消
        """
        if not self.node or not self.wallet:
            logger.error("❌ 節點或錢包未連接")
            return False
        
        try:
            market_data = await self.indexer.markets.get_perpetual_markets(self.config.symbol)
            market_info = market_data.get("markets", {}).get(self.config.symbol, {})
            market = Market(market_info)
            
            # 根據訂單類型選擇 OrderFlags
            order_flags_map = {
                "SHORT_TERM": OrderFlags.SHORT_TERM,
                "LONG_TERM": OrderFlags.LONG_TERM,
                "CONDITIONAL": OrderFlags.CONDITIONAL,
            }
            order_flags = order_flags_map.get(order_type, OrderFlags.LONG_TERM)
            
            order_id = market.order_id(
                self.address,
                self.subaccount,
                client_id,
                order_flags
            )
            
            # 🔧 v14.6.32: 不同訂單類型需要不同的取消參數
            # ⚠️ 關鍵修復: goodTilBlockTime 必須 >= 原訂單的 goodTilBlockTime
            import time
            if order_type == "SHORT_TERM":
                # SHORT_TERM: 使用 good_til_block
                current_block = await self.node.latest_block_height()
                cancel_params = {"good_til_block": current_block + 10}
            else:
                # LONG_TERM/CONDITIONAL: 使用 good_til_block_time (Unix timestamp)
                # 🔧 v14.6.32: 如果沒有提供，使用足夠大的時間 (30 天後)
                # dYdX 要求: cancel goodTilBlockTime >= order goodTilBlockTime
                if good_til_block_time > 0:
                    cancel_gtbt = good_til_block_time
                else:
                    # 使用 30 天後的時間戳，確保 >= 任何現有訂單的 goodTilBlockTime
                    cancel_gtbt = int(time.time()) + 30 * 24 * 60 * 60  # 30 天
                cancel_params = {"good_til_block_time": cancel_gtbt}
            
            if self.authenticator_id > 0:
                tx_options = TxOptions(
                    authenticators=[self.authenticator_id],
                    sequence=self.wallet.sequence,
                    account_number=self.wallet.account_number,
                )
                result = await self.node.cancel_order(
                    wallet=self.wallet,
                    order_id=order_id,
                    tx_options=tx_options,
                    **cancel_params,
                )
            else:
                result = await self.node.cancel_order(
                    wallet=self.wallet,
                    order_id=order_id,
                    **cancel_params,
                )
            
            # 🔧 v14.4: 檢查交易結果
            if not await self._handle_tx_result(result):
                return False
            
            logger.info(f"✅ 訂單已取消: {client_id} (type={order_type})")
            return True
            
        except Exception as e:
            logger.error(f"❌ 取消訂單失敗: {e}")
            return False
    
    async def cancel_all_conditional_orders(self, return_details: bool = False):
        """
        🆕 v12.13: 取消所有條件單 (TP/SL)
        
        用於動態調整止損時：先取消舊的 TP/SL 單，再下新的。
        
        🔧 v14.6.32: 修復 goodTilBlockTime 問題
        
        Returns:
            - return_details=False: 取消的訂單數量 (int)
            - return_details=True: (找到的條件單數量, 成功取消的數量)
        """
        if not self.node or not self.wallet:
            logger.error("❌ 節點或錢包未連接")
            return 0
        
        try:
            # 獲取所有未成交/未觸發的條件單
            # dYdX UI 的「未平倉訂單」通常包含 OPEN + UNTRIGGERED
            orders: list[dict] = []
            for st in ("OPEN", "UNTRIGGERED"):
                try:
                    response = await self.indexer.account.get_subaccount_orders(
                        self.address,
                        self.subaccount,
                        status=st,
                    )
                    orders.extend(self._extract_orders(response))
                except Exception:
                    continue
            
            found_count = 0
            cancelled_count = 0
            for order in orders:
                otype = str(order.get("type", "") or "").upper()
                flags = None
                try:
                    flags = int(order.get("orderFlags", -1))
                except Exception:
                    flags = None

                # 只取消條件單 (用 orderFlags 最準；type 字串做 fallback)
                is_conditional = False
                try:
                    is_conditional = (flags == int(OrderFlags.CONDITIONAL))
                except Exception:
                    is_conditional = False
                if not is_conditional:
                    if otype in {"STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP_LIMIT", "TAKE_PROFIT_LIMIT", "STOP_LOSS", "TAKE_PROFIT"}:
                        is_conditional = True
                    elif "STOP" in otype or "TAKE_PROFIT" in otype:
                        is_conditional = True

                if is_conditional:
                    client_id = int(order.get("clientId", 0))
                    if client_id > 0:
                        found_count += 1
                        # 🔧 v14.6.32: 提取訂單的 goodTilBlockTime
                        gtbt = 0
                        gtbt_str = order.get("goodTilBlockTime", "")
                        if gtbt_str:
                            try:
                                from datetime import datetime
                                dt = datetime.fromisoformat(gtbt_str.replace("Z", "+00:00"))
                                gtbt = int(dt.timestamp())
                            except Exception:
                                pass
                        
                        # 🔧 v14.6.27: 條件單必須用 OrderFlags.CONDITIONAL 取消
                        success = await self.cancel_order(client_id, order_type="CONDITIONAL", good_til_block_time=gtbt)
                        if success:
                            cancelled_count += 1
            
            if cancelled_count > 0:
                logger.info(f"✅ 已取消 {cancelled_count} 個條件單")
            
            if return_details:
                return found_count, cancelled_count
            return cancelled_count
            
        except Exception as e:
            logger.error(f"❌ 取消條件單失敗: {e}")
            if return_details:
                return 0, 0
            return 0

    async def cancel_open_orders(
        self,
        symbol: str | None = None,
        status: str | list[str] = "OPEN",
    ) -> int:
        """取消子帳戶所有未成交掛單（可選 market 過濾）。

        注意：dYdX v4 的 cancel_order 需要 (client_id + order_type/flags)，
        這裡用 indexer 的 order 欄位做保守推斷。
        
        🔧 v14.6.32: 修復 goodTilBlockTime 問題 - 提取訂單的 GTBT 傳給 cancel_order
        """
        if not self.node or not self.wallet:
            logger.error("❌ 節點或錢包未連接")
            return 0

        try:
            statuses = status if isinstance(status, list) else [status]

            orders: list[dict] = []
            for st in statuses:
                try:
                    response = await self.indexer.account.get_subaccount_orders(
                        self.address,
                        self.subaccount,
                        status=st,
                    )
                    orders.extend(self._extract_orders(response))
                except Exception:
                    continue

            cancelled_count = 0
            for order in orders:
                try:
                    # 過濾 market (有些 indexer 回傳使用 ticker 欄位)
                    if symbol:
                        order_market = order.get("market") or order.get("ticker")
                        if order_market != symbol:
                            continue
                    client_id = int(order.get("clientId", 0) or 0)
                    if client_id <= 0:
                        continue

                    # 推斷 order_type
                    order_type = "LONG_TERM"
                    otype = str(order.get("type", "") or "").upper()
                    tif = str(order.get("timeInForce", "") or "").upper()
                    flags = None
                    try:
                        flags = int(order.get("orderFlags", -1))
                    except Exception:
                        flags = None

                    # 優先用 orderFlags 判定（最準）
                    if flags == int(OrderFlags.CONDITIONAL):
                        order_type = "CONDITIONAL"
                    elif flags == int(OrderFlags.LONG_TERM):
                        order_type = "LONG_TERM"
                    elif flags == int(OrderFlags.SHORT_TERM):
                        order_type = "SHORT_TERM"
                    else:
                        # fallback：舊 heuristics
                        if otype in {"STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP_LIMIT", "TAKE_PROFIT_LIMIT", "STOP_LOSS", "TAKE_PROFIT"}:
                            order_type = "CONDITIONAL"
                        elif "STOP" in otype or "TAKE_PROFIT" in otype:
                            order_type = "CONDITIONAL"
                        elif tif in {"IOC", "FOK"}:
                            order_type = "SHORT_TERM"

                    # 🔧 v14.6.32: 提取訂單的 goodTilBlockTime
                    gtbt = 0
                    gtbt_str = order.get("goodTilBlockTime", "")
                    if gtbt_str:
                        try:
                            from datetime import datetime
                            # dYdX 返回 ISO 格式: "2025-07-12T01:30:00.000Z"
                            dt = datetime.fromisoformat(gtbt_str.replace("Z", "+00:00"))
                            gtbt = int(dt.timestamp())
                        except Exception:
                            pass

                    success = await self.cancel_order(client_id, order_type=order_type, good_til_block_time=gtbt)
                    if success:
                        cancelled_count += 1
                except Exception:
                    continue

            if cancelled_count > 0:
                logger.info(f"✅ 已取消 {cancelled_count} 筆未成交掛單" + (f" (market={symbol})" if symbol else ""))
            return cancelled_count

        except Exception as e:
            logger.error(f"❌ 取消未成交掛單失敗: {e}")
            return 0

    async def get_recent_fills(self, limit: int = 5) -> list[dict]:
        """取得最近 fills（用於與 dYdX 線上紀錄比對）。"""
        try:
            import aiohttp

            limit = max(1, min(int(limit), 50))
            url = (
                f"https://indexer.dydx.trade/v4/fills?address={self.address}"
                f"&subaccountNumber={self.subaccount}&limit={limit}"
            )
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    fills = data.get("fills", data) if isinstance(data, dict) else data
                    if not isinstance(fills, list):
                        return []
                    return fills
        except Exception:
            return []

    async def _wait_for_fill(
        self, 
        order_id: int, 
        side: str, 
        size: float, 
        timeout: float
    ) -> bool:
        """
        等待訂單成交
        
        Returns:
            是否成交 (True/False)
        """
        start_time = asyncio.get_event_loop().time()
        check_interval = 0.5  # 每 0.5 秒檢查一次
        
        # 🔧 v14.9.4: 第一次強制清除緩存，確保讀取最新持倉
        self._positions_cache_time = 0
        
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                # 🔧 v14.9.4: 每次檢查都強制清除緩存
                self._positions_cache_time = 0
                
                # 檢查是否有持倉
                positions = await self.get_positions()
                for pos in positions:
                    if pos.get("market") == self.config.symbol:
                        # 🔧 只檢查 OPEN 狀態的持倉 (歷史倉位 size=0)
                        if pos.get("status") != "OPEN":
                            continue
                        pos_size = float(pos.get("size", 0))
                        if abs(pos_size) >= size * 0.99:  # 允許 1% 誤差
                            return True
                
                await asyncio.sleep(check_interval)
                
            except Exception as e:
                logger.warning(f"檢查成交狀態失敗: {e}")
                await asyncio.sleep(check_interval)
        
        logger.warning(f"⏱️ 超時未成交，訂單已自動過期")
        return False
    
    async def place_market_order(self, side: str, size: float, price: float) -> Optional[str]:
        """
        🚨 已棄用 - 保留向後兼容
        市價單會產生巨大滑點，改用 place_aggressive_limit_order
        """
        logger.warning("⚠️  place_market_order 已棄用，改用 Aggressive Maker")
        tx_hash, _ = await self.place_aggressive_limit_order(side, size)
        return tx_hash
    
    async def close_fast_order(
        self, 
        side: str, 
        size: float,
        maker_timeout: float = 5.0,
        fallback_to_ioc: bool = True
    ) -> Tuple[Optional[str], float]:
        """
        🆕 快速平倉策略 - 優先 Maker，超時自動 IOC
        
        📊 策略 (與 place_fast_order 對稱):
        1. 先嘗試 Aggressive Maker 平倉 (5秒超時)
        2. 若失敗，自動改用 IOC 市價單立即平倉
        
        💰 手續費 (dYdX v4, % of notional):
        - Maker: maker_fee_pct
        - IOC/Taker: taker_fee_pct
        
        Args:
            side: 當前持倉方向 "LONG" 或 "SHORT" (會自動反向平倉)
            size: 平倉數量
            maker_timeout: Maker 超時秒數 (預設 5 秒)
            fallback_to_ioc: 是否自動切換 IOC (預設 True)
        
        Returns:
            (交易哈希, 成交價格)
        """
        if not self.node or not self.wallet:
            logger.error("❌ 節點或錢包未連接")
            return None, 0.0
        
        # 🔧 每次平倉前刷新 sequence (防止 sequence 過期)
        await self._refresh_sequence()
        
        # 🔧 v14.6.11: dYdX v4 規定 reduce_only 必須是 IOC
        # Maker 平倉 (POST_ONLY + reduce_only) 會報錯 9003
        # 所以直接使用 IOC 市價單平倉，不再嘗試 Maker
        logger.info(f"📤 使用 IOC 市價單平倉 (dYdX v4 規定)...")
        return await self._close_ioc_order(side, size)
        
        return None, 0.0
    
    async def _try_close_maker(
        self, 
        side: str, 
        size: float,
        timeout_seconds: float = 5.0
    ) -> Tuple[Optional[str], float]:
        """嘗試 Maker 平倉 (POST_ONLY with reduce_only)"""
        try:
            best_bid, best_ask = await self.get_best_bid_ask()
            if best_bid <= 0 or best_ask <= 0:
                logger.error("❌ 無法取得 Bid/Ask 價格")
                return None, 0.0
            
            spread = best_ask - best_bid
            
            # 🔧 動態安全邊距: 至少 spread 的 20% 或 $1
            # 這樣確保不會 cross maker price (Error 2003)
            safety_margin = max(spread * 0.2, 1.0)
            
            # 平倉方向相反
            if side == "LONG":
                # 平多 = 賣出，掛在 Best Ask - 安全邊距 (搶第一賣單)
                limit_price = best_ask - safety_margin
                order_side = Order.Side.SIDE_SELL
                logger.info(f"🔴 平多(賣): ${limit_price:,.2f} (Ask ${best_ask:,.2f} - ${safety_margin:.2f})")
            else:
                # 平空 = 買入，掛在 Best Bid + 安全邊距 (搶第一買單)
                limit_price = best_bid + safety_margin
                order_side = Order.Side.SIDE_BUY
                logger.info(f"🟢 平空(買): ${limit_price:,.2f} (Bid ${best_bid:,.2f} + ${safety_margin:.2f})")
            
            market_data = await self.indexer.markets.get_perpetual_markets(self.config.symbol)
            market_info = market_data.get("markets", {}).get(self.config.symbol, {})
            market = Market(market_info)
            
            client_id = random.randint(0, MAX_CLIENT_ID)
            order_id = market.order_id(
                self.address,
                self.subaccount,
                client_id,
                OrderFlags.SHORT_TERM
            )
            
            current_block = await self.node.latest_block_height()
            good_til_block = current_block + 5
            
            new_order = market.order(
                order_id=order_id,
                order_type=OrderType.LIMIT,
                side=order_side,
                size=size,
                price=limit_price,
                time_in_force=Order.TimeInForce.TIME_IN_FORCE_POST_ONLY,
                reduce_only=True,  # 🔑 只平倉!
                good_til_block=good_til_block,
            )
            
            logger.info(f"📤 提交 Maker 平倉單: 平{side} {size:.4f} BTC @ ${limit_price:,.2f}")
            
            if self.authenticator_id > 0:
                tx_options = TxOptions(
                    authenticators=[self.authenticator_id],
                    sequence=self.wallet.sequence,
                    account_number=self.wallet.account_number,
                )
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                    tx_options=tx_options,
                )
            else:
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                )
            
            # 🔧 v14.4: 檢查交易結果
            if not await self._handle_tx_result(transaction):
                return None, 0.0
            
            # 等待平倉成交
            is_closed = await self._wait_for_close_fill(timeout_seconds)
            
            if is_closed:
                # 🔧 對於 POST_ONLY Maker 單，成交價 = 掛單價
                return str(transaction), limit_price
            else:
                return None, 0.0
            
        except Exception as e:
            logger.error(f"❌ Maker 平倉失敗: {e}")
            import traceback
            traceback.print_exc()
            return None, 0.0
    
    async def _check_has_position(self, expected_side: str = None) -> Tuple[bool, float]:
        """
        🔧 v14.6.5: 在發送平倉訂單前檢查是否真的有持倉
        
        Returns:
            (has_position, position_size) - 是否有持倉，以及持倉大小
        """
        try:
            positions = await self.get_positions()
            for pos in positions:
                if pos.get("market") == self.config.symbol:
                    size = float(pos.get("size", 0))
                    if abs(size) > 0.0001:
                        # 檢查方向是否匹配
                        actual_side = "LONG" if size > 0 else "SHORT"
                        if expected_side and actual_side != expected_side:
                            logger.warning(f"⚠️ 持倉方向不符: 預期 {expected_side}, 實際 {actual_side}")
                            return False, 0.0
                        return True, abs(size)
            return False, 0.0
        except Exception as e:
            logger.error(f"❌ 檢查持倉失敗: {e}")
            return False, 0.0

    async def _get_live_position(self) -> Tuple[Optional[str], float]:
        """取得當前持倉方向與大小 (REST 為準)。"""
        try:
            positions = await self.get_positions()
            for pos in positions:
                if pos.get("market") == self.config.symbol:
                    size = float(pos.get("size", 0))
                    if abs(size) > 0.0001:
                        side = "LONG" if size > 0 else "SHORT"
                        return side, abs(size)
            return None, 0.0
        except Exception as e:
            logger.error(f"❌ 取得持倉失敗: {e}")
            return None, 0.0
    
    async def _close_ioc_order(
        self, 
        side: str, 
        size: float
    ) -> Tuple[Optional[str], float]:
        """
        IOC 平倉單 - 立即成交或取消
        
        使用 IOC (Immediate-Or-Cancel) 確保立即平倉
        價格設定為對手盤最佳價 ± 滑點容忍度
        """
        try:
            def _is_reduce_only_error(err: dict) -> bool:
                raw = " ".join(str(err.get(k, "")) for k in ("raw_log", "tx_excerpt"))
                raw_l = raw.lower()
                return "reduce-only orders cannot increase the position size" in raw or "reduce-only" in raw_l

            max_attempts = 2
            attempt = 0
            while attempt < max_attempts:
                attempt += 1

                live_side, actual_size = await self._get_live_position()
                if not live_side:
                    logger.warning("⚠️ dYdX 無持倉，視為已平倉")
                    fill_price = await self._get_last_fill_price()
                    if fill_price <= 0:
                        fill_price = await self.get_price()
                    return "REST_NO_POSITION", fill_price

                if live_side != side:
                    logger.warning(f"⚠️ 持倉方向不符: 預期 {side}, 實際 {live_side}，改用實際方向")
                    side = live_side

                # 使用實際持倉大小，防止 reduce-only 錯誤
                if abs(actual_size - size) > 0.0001:
                    logger.warning(f"⚠️ 持倉大小不符: 預期 {size:.4f}, 實際 {actual_size:.4f}，使用實際大小")
                    size = actual_size

                best_bid, best_ask = await self.get_best_bid_ask()
                if best_bid <= 0 or best_ask <= 0:
                    logger.error("❌ 無法取得價格")
                    return None, 0.0

                # 🔧 v14.3: 滑點降到 0.1% (原 0.5% 太高)
                slippage = 0.001  # 0.1% 滑點容忍
                if side == "LONG":
                    # 平多 = 賣出：用 Bid 價 - 滑點 (確保立即成交)
                    limit_price = best_bid * (1 - slippage)
                    order_side = Order.Side.SIDE_SELL
                    logger.info(f"🔴 IOC 平多(賣): ${limit_price:,.2f} (Bid ${best_bid:,.2f} - 0.1%)")
                else:
                    # 平空 = 買入：用 Ask 價 + 滑點
                    limit_price = best_ask * (1 + slippage)
                    order_side = Order.Side.SIDE_BUY
                    logger.info(f"🟢 IOC 平空(買): ${limit_price:,.2f} (Ask ${best_ask:,.2f} + 0.1%)")

                # 準備訂單
                market_data = await self.indexer.markets.get_perpetual_markets(self.config.symbol)
                market_info = market_data.get("markets", {}).get(self.config.symbol, {})
                market = Market(market_info)

                client_id = random.randint(0, MAX_CLIENT_ID)
                order_id = market.order_id(
                    self.address,
                    self.subaccount,
                    client_id,
                    OrderFlags.SHORT_TERM
                )

                current_block = await self.node.latest_block_height()
                good_til_block = current_block + 10

                # IOC 平倉訂單
                new_order = market.order(
                    order_id=order_id,
                    order_type=OrderType.LIMIT,
                    side=order_side,
                    size=size,
                    price=limit_price,
                    time_in_force=Order.TimeInForce.TIME_IN_FORCE_IOC,  # 🔑 IOC!
                    reduce_only=True,  # 🔑 只平倉!
                    good_til_block=good_til_block,
                )

                # 提交
                logger.info(f"📤 提交 IOC 平倉訂單: 平{side} {size:.4f} BTC @ ${limit_price:,.2f}")

                if self.authenticator_id > 0:
                    tx_options = TxOptions(
                        authenticators=[self.authenticator_id],
                        sequence=self.wallet.sequence,
                        account_number=self.wallet.account_number,
                    )
                    transaction = await self.node.place_order(
                        wallet=self.wallet,
                        order=new_order,
                        tx_options=tx_options,
                    )
                else:
                    transaction = await self.node.place_order(
                        wallet=self.wallet,
                        order=new_order,
                    )

                # 🔧 v14.4: 檢查交易結果
                if not await self._handle_tx_result(transaction):
                    last_err = self.get_last_tx_error()
                    live_side2, live_size2 = await self._get_live_position()
                    if not live_side2:
                        logger.warning("⚠️ 平倉失敗但 REST 已無持倉，視為已平倉")
                        fill_price = await self._get_last_fill_price()
                        if fill_price <= 0:
                            fill_price = await self.get_price()
                        return "REST_NO_POSITION", fill_price
                    if attempt < max_attempts and _is_reduce_only_error(last_err):
                        logger.warning("⚠️ reduce-only 方向/大小可能變動，重試一次")
                        side = live_side2
                        size = live_size2 if live_size2 > 0 else size
                        continue
                    return None, 0.0

                logger.info(f"📝 IOC 平倉訂單已提交: {transaction}")

                # 等待成交 (IOC 應該很快)
                is_closed = await self._wait_for_close_fill(timeout=3.0)  # IOC 只等 3 秒

                if is_closed:
                    # 🔧 IOC 成交價 ≈ 對手盤價格 (best_bid 或 best_ask)
                    fill_price = best_bid if side == "LONG" else best_ask
                    logger.info(f"✅ IOC 平倉成交! 價格: ${fill_price:,.2f} | 手續費: $0 (dYdX v4)")
                    return str(transaction), fill_price

                logger.warning("⚠️ IOC 平倉未成交，嘗試強制市價平倉...")
                return await self._force_close_market(side, size)

            return None, 0.0
            
        except Exception as e:
            logger.error(f"❌ IOC 平倉失敗: {e}")
            import traceback
            traceback.print_exc()
            return None, 0.0

    async def close_position_aggressive(
        self, 
        side: str, 
        size: float, 
        timeout_seconds: float = 5.0,
        is_stop_loss: bool = False
    ) -> Tuple[Optional[str], float]:
        """
        🆕 平倉策略 (已升級為快速平倉)
        - 止盈: 先 Maker 5s → IOC fallback
        - 止損: 直接 IOC (安全優先) ⚡
        
        Args:
            side: 當前持倉方向 "LONG" 或 "SHORT"
            size: 平倉數量
            timeout_seconds: 超時秒數
            is_stop_loss: 是否為止損 (True = 直接 IOC)
        
        Returns:
            (交易哈希, 成交價格)
        """
        # 🚨 止損 = 直接 IOC，不嘗試 Maker
        if is_stop_loss:
            logger.warning("🚨 止損觸發! 直接 IOC 平倉")
            return await self._close_ioc_order(side, size)
        
        # 止盈 = 嘗試快速平倉 (Maker → IOC)
        return await self.close_fast_order(
            side=side,
            size=size,
            maker_timeout=timeout_seconds,
            fallback_to_ioc=True
        )
    
    async def _wait_for_close_fill(self, timeout: float) -> bool:
        """等待平倉成交 (持倉歸零)
        
        Returns:
            是否成交 (True/False)
        """
        start_time = asyncio.get_event_loop().time()
        check_interval = 0.5
        
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                positions = await self.get_positions()
                has_position = False
                for pos in positions:
                    if pos.get("market") == self.config.symbol:
                        size = float(pos.get("size", 0))
                        if abs(size) > 0.0001:
                            has_position = True
                            break
                
                if not has_position:
                    return True
                
                await asyncio.sleep(check_interval)
                
            except Exception as e:
                logger.warning(f"檢查平倉狀態失敗: {e}")
                await asyncio.sleep(check_interval)
        
        return False
    
    async def _get_last_fill_price(self) -> float:
        """從 fills API 獲取最近一筆成交價"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                url = f"https://indexer.dydx.trade/v4/fills?address={self.address}&subaccountNumber={self.subaccount}&limit=1"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        fills = data.get('fills', data) if isinstance(data, dict) else data
                        if fills and len(fills) > 0:
                            price = float(fills[0].get('price', 0))
                            if price > 0:
                                logger.info(f"📊 實際成交價 (from fills): ${price:,.2f}")
                                return price
        except Exception as e:
            logger.warning(f"獲取 fills 失敗: {e}")
        return 0.0
    
    async def _force_close_market(self, side: str, size: float) -> Tuple[Optional[str], float]:
        """強制市價平倉 (緊急用)"""
        logger.warning("🚨 執行緊急市價平倉!")
        
        try:
            # 🔧 v14.6.5: 先檢查是否真的有持倉
            has_pos, actual_size = await self._check_has_position(side)
            if not has_pos:
                logger.warning(f"⚠️ dYdX 無 {side} 持倉，可能已被止損單平倉")
                # 嘗試獲取最後成交價
                fill_price = await self._get_last_fill_price()
                return None, fill_price if fill_price > 0 else await self.get_price()
            
            # 使用實際持倉大小
            if abs(actual_size - size) > 0.0001:
                logger.warning(f"⚠️ 使用實際持倉大小: {actual_size:.4f} (原 {size:.4f})")
                size = actual_size
            
            best_bid, best_ask = await self.get_best_bid_ask()
            price = (best_bid + best_ask) / 2
            
            market_data = await self.indexer.markets.get_perpetual_markets(self.config.symbol)
            market_info = market_data.get("markets", {}).get(self.config.symbol, {})
            market = Market(market_info)
            
            client_id = random.randint(0, MAX_CLIENT_ID)
            order_id = market.order_id(
                self.address,
                self.subaccount,
                client_id,
                OrderFlags.SHORT_TERM
            )
            
            current_block = await self.node.latest_block_height()
            good_til_block = current_block + 20
            
            if side == "LONG":
                slippage_price = price * 0.95
                order_side = Order.Side.SIDE_SELL
            else:
                slippage_price = price * 1.05
                order_side = Order.Side.SIDE_BUY
            
            new_order = market.order(
                order_id=order_id,
                order_type=OrderType.MARKET,
                side=order_side,
                size=size,
                price=slippage_price,
                time_in_force=Order.TimeInForce.TIME_IN_FORCE_IOC,
                reduce_only=True,
                good_til_block=good_til_block,
            )
            
            if self.authenticator_id > 0:
                tx_options = TxOptions(
                    authenticators=[self.authenticator_id],
                    sequence=self.wallet.sequence,
                    account_number=self.wallet.account_number,
                )
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                    tx_options=tx_options,
                )
            else:
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                )
            
            # 🔧 v14.4: 檢查交易結果
            if not await self._handle_tx_result(transaction):
                return None, 0.0
            
            return str(transaction), price
            
        except Exception as e:
            logger.error(f"❌ 緊急平倉失敗: {e}")
            return None, 0.0
    
    async def close_position(self, side: str, size: float, price: float) -> Optional[str]:
        """
        平倉
        
        Args:
            side: 當前持倉方向 "LONG" 或 "SHORT"
            size: 平倉數量
            price: 當前價格
        
        Returns:
            交易哈希
        """
        if not self.node or not self.wallet:
            logger.error("❌ 節點或錢包未連接，無法平倉")
            return None
        
        try:
            market_data = await self.indexer.markets.get_perpetual_markets(self.config.symbol)
            market_info = market_data.get("markets", {}).get(self.config.symbol, {})
            market = Market(market_info)
            
            client_id = random.randint(0, MAX_CLIENT_ID)
            order_id = market.order_id(
                self.address,
                self.subaccount,
                client_id,
                OrderFlags.SHORT_TERM
            )
            
            current_block = await self.node.latest_block_height()
            good_til_block = current_block + 20
            
            # 平倉方向相反
            if side == "LONG":
                slippage_price = price * 0.95  # 賣出平多
                order_side = Order.Side.SIDE_SELL
            else:
                slippage_price = price * 1.05  # 買入平空
                order_side = Order.Side.SIDE_BUY
            
            new_order = market.order(
                order_id=order_id,
                order_type=OrderType.MARKET,
                side=order_side,
                size=size,
                price=slippage_price,
                time_in_force=Order.TimeInForce.TIME_IN_FORCE_IOC,
                reduce_only=True,  # 只平倉
                good_til_block=good_til_block,
            )
            
            logger.info(f"📤 提交平倉訂單: 平{side} {size:.4f} BTC @ ${price:,.2f}")
            
            # 🆕 如果有 authenticator_id，使用 TxOptions
            if self.authenticator_id > 0:
                tx_options = TxOptions(
                    authenticators=[self.authenticator_id],
                    sequence=self.wallet.sequence,
                    account_number=self.wallet.account_number,
                )
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                    tx_options=tx_options,
                )
            else:
                transaction = await self.node.place_order(
                    wallet=self.wallet,
                    order=new_order,
                )
            
            # 🔧 v14.4: 檢查交易結果
            if not await self._handle_tx_result(transaction):
                return None
            
            logger.info(f"✅ 平倉訂單已提交! 交易哈希: {transaction}")
            return str(transaction)
            
        except Exception as e:
            logger.error(f"❌ 平倉失敗: {e}")
            import traceback
            traceback.print_exc()
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# 六維評分系統 (與原版相同)
# ═══════════════════════════════════════════════════════════════════════════════

class SixDimensionScorer:
    """六維評分系統"""
    
    def __init__(self, api: DydxAPI):
        self.api = api
        
    async def calculate_score(self) -> Tuple[int, str, Dict]:
        """
        計算六維評分
        返回: (總分, 方向, 詳細分數)
        """
        scores = {
            "momentum_1m": 0,
            "momentum_5m": 0,
            "obi": 0,
            "funding": 0,
            "volume": 0,
            "trend": 0
        }
        
        try:
            # 取得數據
            candles_1m = await self.api.get_candles("1MIN", 10)
            candles_5m = await self.api.get_candles("5MINS", 10)
            orderbook = await self.api.get_orderbook()
            funding_rate = await self.api.get_funding_rate()
            trades = await self.api.get_trades(100)
            
            # 1. 1分鐘動量
            if candles_1m and len(candles_1m) >= 2:
                close_now = float(candles_1m[0].get("close", 0))
                close_prev = float(candles_1m[1].get("close", 0))
                if close_prev > 0:
                    change_1m = (close_now - close_prev) / close_prev * 100
                    if change_1m > 0.02:
                        scores["momentum_1m"] = 2  # 多
                    elif change_1m < -0.02:
                        scores["momentum_1m"] = -2  # 空
            
            # 2. 5分鐘動量
            if candles_5m and len(candles_5m) >= 2:
                close_now = float(candles_5m[0].get("close", 0))
                close_prev = float(candles_5m[1].get("close", 0))
                if close_prev > 0:
                    change_5m = (close_now - close_prev) / close_prev * 100
                    if change_5m > 0.05:
                        scores["momentum_5m"] = 2  # 多
                    elif change_5m < -0.05:
                        scores["momentum_5m"] = -2  # 空
            
            # 3. OBI (Order Book Imbalance)
            if orderbook:
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])
                if bids and asks:
                    bid_volume = sum(float(b.get("size", 0)) for b in bids[:10])
                    ask_volume = sum(float(a.get("size", 0)) for a in asks[:10])
                    total = bid_volume + ask_volume
                    if total > 0:
                        obi = (bid_volume - ask_volume) / total
                        if obi > 0.2:
                            scores["obi"] = 2  # 買壓大
                        elif obi < -0.2:
                            scores["obi"] = -2  # 賣壓大
            
            # 4. 資金費率
            if funding_rate != 0:
                if funding_rate > 0.0001:  # 正費率 (多頭付空頭)
                    scores["funding"] = -2  # 傾向做空
                elif funding_rate < -0.0001:  # 負費率
                    scores["funding"] = 2  # 傾向做多
            
            # 5. 成交量趨勢
            if trades and len(trades) >= 10:
                recent_buy = sum(1 for t in trades[:50] if t.get("side") == "BUY")
                recent_sell = 50 - recent_buy
                if recent_buy > 30:
                    scores["volume"] = 2  # 買入主導
                elif recent_sell > 30:
                    scores["volume"] = -2  # 賣出主導
            
            # 6. 趨勢 (用 5 根 5 分鐘 K 線)
            if candles_5m and len(candles_5m) >= 5:
                closes = [float(c.get("close", 0)) for c in candles_5m[:5]]
                if all(closes[i] >= closes[i+1] for i in range(4)):
                    scores["trend"] = 2  # 上升趨勢
                elif all(closes[i] <= closes[i+1] for i in range(4)):
                    scores["trend"] = -2  # 下降趨勢
            
        except Exception as e:
            logger.error(f"計算六維評分失敗: {e}")
        
        # 計算總分
        total_score = sum(scores.values())
        
        # 決定方向
        if total_score >= 4:
            direction = "LONG"
        elif total_score <= -4:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"
        
        return total_score, direction, scores


# ═══════════════════════════════════════════════════════════════════════════════
# 🔄 三線反轉檢測器 (Three Line Reversal Detector)
# ═══════════════════════════════════════════════════════════════════════════════

class ThreeLineReversalDetector:
    """
    三線反轉檢測器
    
    當持有方向與市場趨勢背離超過一定時間，觸發反轉信號。
    - 快線 (5秒): 即時動量
    - 中線 (30秒): 短期趨勢
    - 慢線 (5分鐘): 中期趨勢
    """
    
    def __init__(self, api, threshold_sec: float = 15.0):
        self.api = api
        self.threshold_sec = threshold_sec
        self.reversal_start_time: Optional[datetime] = None
        self.last_check_time: Optional[datetime] = None
        
    async def check_reversal(self, position_side: str) -> Tuple[bool, float, str]:
        """
        檢查是否觸發三線反轉
        
        Args:
            position_side: 當前持倉方向 "LONG" 或 "SHORT"
        
        Returns:
            (is_triggered, accumulated_seconds, reason)
        """
        try:
            # 取得三線數據
            candles_1m = await self.api.get_candles("1MIN", 5)
            orderbook = await self.api.get_orderbook()
            trades = await self.api.get_trades(50)
            
            # 計算各線趨勢
            fast_trend = self._calc_fast_trend(trades)  # 快線: 最近交易
            mid_trend = self._calc_mid_trend(candles_1m)  # 中線: 1分K
            slow_trend = self._calc_slow_trend(candles_1m)  # 慢線: 5分趨勢
            
            # 檢查是否與持倉方向背離
            if position_side == "LONG":
                # 多單時，三線都看空 = 危險
                is_adverse = (fast_trend < 0 and mid_trend < 0)
            else:
                # 空單時，三線都看多 = 危險
                is_adverse = (fast_trend > 0 and mid_trend > 0)
            
            now = datetime.now()
            
            if is_adverse:
                if self.reversal_start_time is None:
                    self.reversal_start_time = now
                
                accumulated = (now - self.reversal_start_time).total_seconds()
                
                if accumulated >= self.threshold_sec:
                    reason = f"三線反轉 (快:{fast_trend:+.0f} 中:{mid_trend:+.0f} 累積{accumulated:.0f}秒)"
                    return True, accumulated, reason
                
                return False, accumulated, ""
            else:
                # 趨勢恢復，重置計時
                self.reversal_start_time = None
                return False, 0, ""
                
        except Exception as e:
            logger.warning(f"三線反轉檢測錯誤: {e}")
            return False, 0, ""
    
    def _calc_fast_trend(self, trades: List[Dict]) -> int:
        """計算快線趨勢 (最近交易買賣比)"""
        if not trades or len(trades) < 10:
            return 0
        
        recent = trades[:20]
        buys = sum(1 for t in recent if t.get("side") == "BUY")
        sells = len(recent) - buys
        
        if buys > sells + 3:
            return 1  # 看多
        elif sells > buys + 3:
            return -1  # 看空
        return 0
    
    def _calc_mid_trend(self, candles: List[Dict]) -> int:
        """計算中線趨勢 (最近 2 根 K 線)"""
        if not candles or len(candles) < 2:
            return 0
        
        close_now = float(candles[0].get("close", 0))
        close_prev = float(candles[1].get("close", 0))
        
        if close_prev == 0:
            return 0
        
        change = (close_now - close_prev) / close_prev * 100
        
        if change > 0.02:
            return 1
        elif change < -0.02:
            return -1
        return 0
    
    def _calc_slow_trend(self, candles: List[Dict]) -> int:
        """計算慢線趨勢 (5 根 K 線)"""
        if not candles or len(candles) < 5:
            return 0
        
        close_now = float(candles[0].get("close", 0))
        close_5ago = float(candles[4].get("close", 0))
        
        if close_5ago == 0:
            return 0
        
        change = (close_now - close_5ago) / close_5ago * 100
        
        if change > 0.1:
            return 1
        elif change < -0.1:
            return -1
        return 0
    
    def reset(self):
        """重置狀態"""
        self.reversal_start_time = None


# ═══════════════════════════════════════════════════════════════════════════════
# 🆕 真實持倉追蹤器 (獨立於虛擬交易)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RealPosition:
    """真實持倉狀態"""
    side: str  # LONG 或 SHORT
    size: float  # BTC 數量
    entry_price: float  # 真實成交價
    entry_time: datetime
    leverage: int = 50
    max_pnl_pct: float = 0.0  # 追蹤最高獲利
    
    def pnl_pct(self, current_price: float) -> float:
        """計算當前獲利% (含槓桿)"""
        if self.side == "LONG":
            return (current_price - self.entry_price) / self.entry_price * 100 * self.leverage
        else:
            return (self.entry_price - current_price) / self.entry_price * 100 * self.leverage
    
    def pnl_usd(self, current_price: float) -> float:
        """計算當前盈虧 USD"""
        if self.side == "LONG":
            return (current_price - self.entry_price) * self.size
        else:
            return (self.entry_price - current_price) * self.size
    
    def update_max_pnl(self, current_price: float) -> float:
        """更新最高獲利"""
        pnl = self.pnl_pct(current_price)
        if pnl > self.max_pnl_pct:
            self.max_pnl_pct = pnl
        return self.max_pnl_pct


# ═══════════════════════════════════════════════════════════════════════════════
# Paper Trading 引擎
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PaperPosition:
    """Paper Trading 持倉"""
    side: str  # LONG 或 SHORT
    size: float  # BTC 數量
    entry_price: float
    entry_time: datetime
    strategy: str = "HYBRID"
    entry_type: str = "TAKER"
    leverage: int = 50
    max_pnl_pct: float = 0.0  # 🆕 追蹤最高獲利
    
    def pnl_pct(self, current_price: float) -> float:
        """計算盈虧百分比"""
        if self.entry_price == 0:
            return 0.0
        
        if self.side == "LONG":
            return (current_price - self.entry_price) / self.entry_price * 100 * self.leverage
        else:
            return (self.entry_price - current_price) / self.entry_price * 100 * self.leverage
    
    def pnl_usd(self, current_price: float) -> float:
        """計算盈虧金額 (USD)"""
        pnl_pct = self.pnl_pct(current_price)
        notional = self.size * self.entry_price
        margin = notional / self.leverage
        return margin * (pnl_pct / 100)
    
    def update_max_pnl(self, current_price: float) -> float:
        """更新最高獲利"""
        pnl = self.pnl_pct(current_price)
        if pnl > self.max_pnl_pct:
            self.max_pnl_pct = pnl
        return self.max_pnl_pct


class PaperTradingEngine:
    """Paper Trading 引擎"""
    
    def __init__(self, config: DydxConfig, sync_mode: bool = False):
        self.config = config
        self.balance = config.paper_initial_balance
        self.position: Optional[PaperPosition] = None
        self.trades: List[Dict] = []
        self.logs_dir = Path("logs/dydx_paper_trader")
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.sync_mode = sync_mode  # 🆕 同步模式標記
        
    def open_position(
        self,
        side: str,
        price: float,
        strategy: str = "HYBRID",
        entry_type: str = "TAKER",
    ) -> bool:
        """開倉"""
        if self.position:
            logger.warning("已有持倉，無法開新倉")
            return False
        
        # 計算倉位大小
        margin = self.balance * self.config.position_size_pct
        notional = margin * self.config.leverage
        size = notional / price
        
        strategy = strategy or "HYBRID"
        entry_type = (entry_type or "TAKER").upper()

        self.position = PaperPosition(
            side=side,
            size=size,
            entry_price=price,
            entry_time=datetime.now(),
            strategy=strategy,
            entry_type=entry_type,
            leverage=self.config.leverage
        )
        
        logger.info(f"📈 開倉 {side} | 價格: ${price:,.2f} | 數量: {size:.4f} BTC")
        return True
    
    def close_position(self, price: float, reason: str = "") -> Dict:
        """平倉"""
        if not self.position:
            return {}
        
        pnl_pct = self.position.pnl_pct(price)
        # 扣除手續費 (ROE 影響會被槓桿放大；止損視為 Taker)
        r = (reason or "").lower()
        is_stop = ("stop" in r) or ("sl" in r) or ("止損" in reason)
        entry_fee_pct = self.config.maker_fee_pct if self.position.entry_type == "MAKER" else self.config.taker_fee_pct
        exit_fee_pct = self.config.taker_fee_pct if is_stop else self.config.maker_fee_pct
        fee_impact_roe_pct = (entry_fee_pct + exit_fee_pct) * self.position.leverage
        net_pnl_pct = pnl_pct - fee_impact_roe_pct
        
        # 計算 USD 盈虧
        margin = self.balance * self.config.position_size_pct
        pnl_usd = margin * (net_pnl_pct / 100)
        
        # 更新餘額
        self.balance += pnl_usd
        
        # 記錄交易
        trade = {
            "side": self.position.side,
            "strategy": self.position.strategy,
            "entry_type": self.position.entry_type,
            "entry_price": self.position.entry_price,
            "exit_price": price,
            "size": self.position.size,
            "pnl_pct": pnl_pct,
            "net_pnl_pct": net_pnl_pct,
            "pnl_usd": pnl_usd,
            "entry_time": self.position.entry_time.isoformat(),
            "exit_time": datetime.now().isoformat(),
            "hold_seconds": (datetime.now() - self.position.entry_time).total_seconds(),
            "reason": reason
        }
        self.trades.append(trade)
        
        win_lose = "🟢獲利" if pnl_usd > 0 else "🔴虧損"
        logger.info(f"📉 平倉 {self.position.side} | {win_lose} ${pnl_usd:+.2f} ({net_pnl_pct:+.2f}%) | 原因: {reason}")
        
        # 🆕 只有純 Paper 模式才顯示虛擬餘額，sync 模式顯示真實餘額
        if not self.sync_mode:
            logger.info(f"💰 餘額: ${self.balance:.2f}")
        
        self.position = None
        self._save_trades()
        
        return trade
    
    def _save_trades(self):
        """保存交易記錄"""
        filename = self.logs_dir / f"trades_{datetime.now().strftime('%Y%m%d')}.json"
        data = {
            "balance": self.balance,
            "total_trades": len(self.trades),
            "wins": len([t for t in self.trades if t["pnl_usd"] > 0]),
            "losses": len([t for t in self.trades if t["pnl_usd"] <= 0]),
            "trades": self.trades
        }
        with open(filename, "w") as f:
            json.dump(data, f, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════════════
# 主交易機器人
# ═══════════════════════════════════════════════════════════════════════════════

class DydxWhaleTrader:
    """dYdX 鯨魚交易機器人 - 混合策略 (Hybrid Dynamic Scalping)"""
    
    def __init__(self, config: DydxConfig):
        self.config = config
        self.api = DydxAPI(config)
        self.scorer = SixDimensionScorer(self.api)
        # 🆕 傳入 sync_mode，讓 paper engine 知道是否同步真實交易
        self.paper_engine = PaperTradingEngine(config, sync_mode=config.sync_real_trading) if config.paper_trading else None
        self.reversal_detector: Optional[ThreeLineReversalDetector] = None  # 🆕 三線反轉
        self.running = False
        
        # 🆕 真實持倉獨立追蹤
        self.real_position: Optional[RealPosition] = None
        self.real_position_size: float = 0.0  # 兼容舊代碼
        self._random_wave1: List[str] = []
        self._random_wave2: List[str] = []
        self._random_active_wave: int = 1

    def _build_random_wave(self) -> List[str]:
        batch_size = int(self.config.random_entry_balance_batch_size or 20)
        if batch_size < 2:
            batch_size = 2
        if batch_size % 2 != 0:
            batch_size += 1

        max_streak = int(self.config.random_entry_balance_max_streak or 0)
        max_imbalance = int(self.config.random_entry_balance_max_imbalance or 0)

        if not self.config.random_entry_balance_enabled:
            return [random.choice(["LONG", "SHORT"]) for _ in range(batch_size)]

        return _generate_constrained_balanced_sequence(
            batch_size,
            max_streak=max_streak,
            max_imbalance=max_imbalance,
        )

    def _ensure_random_waves(self):
        if not self._random_wave1:
            self._random_wave1 = self._build_random_wave()
        if not self._random_wave2:
            self._random_wave2 = self._build_random_wave()

    def _roll_random_waves_if_needed(self):
        if self._random_active_wave == 1 and not self._random_wave1:
            self._random_active_wave = 2
            self._random_wave1 = self._build_random_wave()
            logger.info("🎲 第1波已用盡 → 切換第2波，補第1波")
        elif self._random_active_wave == 2 and not self._random_wave2:
            self._random_active_wave = 1
            self._random_wave2 = self._build_random_wave()
            logger.info("🎲 第2波已用盡 → 切換第1波，補第2波")

    def _get_balanced_random_direction(self) -> str:
        """🎲 強制平衡隨機進場"""
        self._ensure_random_waves()
        self._roll_random_waves_if_needed()

        if self._random_active_wave == 1:
            direction = self._random_wave1.pop(0)
        else:
            direction = self._random_wave2.pop(0)

        self._roll_random_waves_if_needed()
        return direction

    def _get_balanced_direction_preview(self) -> Tuple[List[str], List[str]]:
        """Preview upcoming random directions without consuming the waves."""
        self._ensure_random_waves()
        return list(self._random_wave1), list(self._random_wave2)

    def _log_random_entry_status(self):
        """顯示隨機入場模式狀態與方向預覽"""
        logger.info("🎲 隨機入場模式 - 策略分析區塊已隱藏 (進場方向隨機，出場按止盈止損)")
        wave1, wave2 = self._get_balanced_direction_preview()
        if wave1:
            def _dir_icon(d: str) -> str:
                return "🟢" if d == "LONG" else "🔴"
            wave1_line = ", ".join(_dir_icon(d) for d in wave1)
            wave2_line = ", ".join(_dir_icon(d) for d in wave2)
            logger.info(f"   第1波：{wave1_line}")
            if wave2_line:
                logger.info(f"   第2波：{wave2_line}")

    def _get_progressive_lock(self, max_pnl_pct: float) -> Tuple[float, str]:
        """計算顯示用鎖利線 (不影響交易邏輯)"""
        base_sl = -(self.config.stop_loss_pct or 0.1)
        candidates: List[Tuple[float, str, str]] = []

        if self.config.use_midpoint_lock and max_pnl_pct > 0:
            lock_start = self.config.lock_start_pct or 0.0
            if lock_start <= 0 or max_pnl_pct >= lock_start:
                ratio = self.config.midpoint_ratio or 0.5
                midpoint_stop = max_pnl_pct * ratio
                if self.config.min_lock_pct > 0:
                    midpoint_stop = max(midpoint_stop, self.config.min_lock_pct)
                midpoint_stop = max(midpoint_stop, base_sl)
                midpoint_name = f"📍 中間數: 鎖住 +{midpoint_stop:.2f}% (最高{max_pnl_pct:.2f}%×{ratio:.0%})"
                candidates.append((midpoint_stop, midpoint_name, "midpoint"))

        if self.config.use_n_lock_n and max_pnl_pct >= self.config.n_lock_n_threshold:
            lock_level = int(max_pnl_pct)
            lock_at = max(lock_level - self.config.n_lock_n_buffer, base_sl)
            nlock_name = f"🔐 N%鎖N%: 鎖住 +{lock_at:.1f}%"
            candidates.append((lock_at, nlock_name, "n_lock_n"))

        if candidates:
            for kind in ("midpoint", "n_lock_n"):
                bucket = [c for c in candidates if c[2] == kind]
                if bucket:
                    lock_pct, name, _ = max(bucket, key=lambda x: x[0])
                    return lock_pct, name

        return base_sl, f"止損 {base_sl:.1f}%"
        
    async def start(self, hours: float = 8.0):
        """啟動交易"""
        if not DYDX_AVAILABLE:
            logger.error("❌ dYdX SDK 未安裝，無法啟動")
            return
        
        # 連接
        if not await self.api.connect():
            return
        
        # 🆕 初始化三線反轉檢測器
        if self.config.reversal_enabled:
            self.reversal_detector = ThreeLineReversalDetector(
                self.api, 
                self.config.reversal_threshold_sec
            )
        
        self.running = True
        end_time = datetime.now() + timedelta(hours=hours)
        
        # 顯示模式
        if self.config.sync_real_trading:
            mode_str = "Paper Trading + 🔴同步真實交易"
        elif self.config.paper_trading:
            mode_str = "Paper Trading (模擬)"
        else:
            mode_str = "🔴真實交易"
        
        logger.info("=" * 60)
        logger.info(f"🚀 dYdX 鯨魚交易機器人啟動 (混合策略)")
        logger.info(f"   模式: {mode_str}")
        logger.info(f"   槓桿: {self.config.leverage}X")
        logger.info(f"   手續費: Maker {self.config.maker_fee_pct}% | Taker {self.config.taker_fee_pct}%")
        logger.info(f"   📈 追蹤止盈: {self.config.trailing_start_pct}% 啟動, {self.config.trailing_offset_pct}% 回撤平倉")
        logger.info(f"   🔄 三線反轉: {'啟用' if self.config.reversal_enabled else '停用'} ({self.config.reversal_threshold_sec}秒)")
        if self.config.random_entry_mode:
            logger.info("   🎲 隨機入場: 啟用 (平衡 50/50)")
        if self.config.sync_real_trading:
            if self.config.fixed_btc_size > 0:
                logger.info(f"   🔴 真實倉位: {self.config.fixed_btc_size:.4f} BTC (固定)")
            else:
                logger.info(f"   真實倉位比例: {self.config.real_position_size_pct*100:.0f}%")
        logger.info(f"   運行時間: {hours} 小時")
        logger.info("=" * 60)
        
        # 如果是同步模式，檢查現有持倉
        if self.config.sync_real_trading:
            await self._sync_existing_positions()
        
        cycle = 0
        while self.running and datetime.now() < end_time:
            cycle += 1
            try:
                await self._trading_cycle(cycle)
            except Exception as e:
                logger.error(f"交易循環錯誤: {e}")
            
            await asyncio.sleep(5)  # 5 秒一個循環
        
        logger.info("🛑 交易結束")
        self._print_summary()
    
    async def _sync_existing_positions(self):
        """同步檢查現有真實持倉"""
        try:
            positions = await self.api.get_positions()
            for pos in positions:
                if pos.get("market") == self.config.symbol:
                    side = pos.get("side", "")
                    size = float(pos.get("size", 0))
                    entry_price = float(pos.get("entryPrice", 0))
                    
                    if abs(size) > 0:
                        logger.info(f"📊 發現現有持倉: {side} {abs(size):.4f} BTC @ ${entry_price:,.2f}")
                        self.real_position_size = abs(size)
                        
                        # 🆕 建立 RealPosition 追蹤
                        self.real_position = RealPosition(
                            side=side.upper(),
                            size=abs(size),
                            entry_price=entry_price,
                            entry_time=datetime.now(),  # 無法知道原始開倉時間
                            leverage=self.config.leverage
                        )
        except Exception as e:
            logger.warning(f"檢查現有持倉失敗: {e}")
    
    async def _trading_cycle(self, cycle: int):
        """一個交易循環"""
        price = await self.api.get_price()
        if price == 0:
            return
        
        # 檢查持倉
        if self.paper_engine and self.paper_engine.position:
            # 每 6 個循環 (約 30 秒) 顯示持倉狀態
            if cycle % 6 == 0:
                await self._display_position_status(price)
            await self._check_exit(price)
            return
        
        # 計算六維評分
        if self.config.random_entry_mode:
            if cycle % 12 == 0:
                self._log_random_entry_status()
            direction = self._get_balanced_random_direction()
            await self._open_position(direction, price, strategy="RANDOM_BALANCED", entry_type="TAKER")
            return

        score, direction, details = await self.scorer.calculate_score()
        
        # 每 12 個循環 (約 1 分鐘) 輸出狀態
        if cycle % 12 == 0:
            logger.info(f"📊 BTC ${price:,.2f} | 六維: {score:+d}/12 → {direction} | {details}")
        
        # 判斷是否開倉
        if abs(score) >= self.config.six_dim_threshold:
            if direction in ["LONG", "SHORT"]:
                await self._open_position(direction, price, strategy="SIX_DIM", entry_type="TAKER")
    
    async def _display_position_status(self, price: float):
        """顯示持倉狀態 (虛擬 + 真實分開顯示)"""
        pos = self.paper_engine.position
        if not pos:
            return
        
        bid, ask = await self.api.get_best_bid_ask()
        mid = price
        if mid <= 0 and bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        if bid <= 0 or ask <= 0:
            bid = mid
            ask = mid

        spread_pct = (ask - bid) / mid * 100 if mid > 0 and ask > 0 and bid > 0 else 0.0
        spread_bps = spread_pct * 100

        paper_pnl_pct = pos.pnl_pct(mid)
        hold_time = (datetime.now() - pos.entry_time).total_seconds()
        max_pnl = pos.update_max_pnl(mid)

        entry_type = str(getattr(pos, "entry_type", "TAKER")).upper()
        strategy = getattr(pos, "strategy", "HYBRID")
        leverage = pos.leverage

        entry_fee_pct = self.config.maker_fee_pct if entry_type == "MAKER" else self.config.taker_fee_pct
        exit_fee_pct = self.config.taker_fee_pct if paper_pnl_pct < 0 else self.config.maker_fee_pct
        fee_impact_roe_pct = (entry_fee_pct + exit_fee_pct) * leverage
        net_pnl_pct = paper_pnl_pct - fee_impact_roe_pct

        net_price = bid if pos.side == "LONG" else ask
        fee_total_pct = entry_fee_pct + exit_fee_pct
        if pos.side == "LONG":
            breakeven = pos.entry_price * (1 + fee_total_pct / 100)
            is_profitable = net_price > breakeven
            distance_to_be = (breakeven - net_price) / net_price * 100 if net_price < breakeven else 0.0
        else:
            breakeven = pos.entry_price * (1 - fee_total_pct / 100)
            is_profitable = net_price < breakeven
            distance_to_be = (net_price - breakeven) / net_price * 100 if net_price > breakeven else 0.0

        target_pct = self.config.target_profit_pct
        stop_pct = self.config.stop_loss_pct
        target_move = target_pct / leverage / 100
        stop_move = stop_pct / leverage / 100
        if pos.side == "LONG":
            tp_price = pos.entry_price * (1 + target_move)
            sl_price = pos.entry_price * (1 - stop_move)
        else:
            tp_price = pos.entry_price * (1 - target_move)
            sl_price = pos.entry_price * (1 + stop_move)

        dir_icon = "🟢" if pos.side == "LONG" else "🔴"
        lines = []
        lines.append(f"{dir_icon} [Paper Trading] {strategy}")
        lines.append(f"進場: ${pos.entry_price:,.2f} ({entry_type})")
        lines.append(f"槓桿: {leverage:.1f}X (動態)")
        if is_profitable:
            lines.append(f"💰 損益平衡: ${breakeven:,.2f} ✅ 已獲利")
        else:
            lines.append(f"💰 損益平衡: ${breakeven:,.2f} ⏳ 差 {distance_to_be:.4f}%")
        lines.append(f"浮動: {paper_pnl_pct:+.2f}%  💵 淨盈虧: {net_pnl_pct:+.2f}%")
        lines.append(f"TP: ${tp_price:,.2f} (+{target_pct:.3f}%)  SL: ${sl_price:,.2f} (-{stop_pct:.3f}%)")
        if bid > 0 and ask > 0:
            lines.append(f"Bid/Ask: ${bid:,.2f} / ${ask:,.2f}  Spread: {spread_pct:.4f}% ({spread_bps:.1f}bps)")

        if max_pnl > 0 and (self.config.use_midpoint_lock or self.config.use_n_lock_n):
            lock_pct, stage_name = self._get_progressive_lock(max_pnl)
            if pos.side == "LONG":
                lock_price = pos.entry_price * (1 + lock_pct / leverage / 100)
            else:
                lock_price = pos.entry_price * (1 - lock_pct / leverage / 100)
            lines.append("🔐 N%鎖N% 鎖利 (v12.8)")
            lines.append(f"   當前: {paper_pnl_pct:+.2f}% | 最高: {max_pnl:+.2f}%")
            lines.append(f"   狀態: {stage_name}")
            lines.append(f"   止損線: {lock_pct:+.1f}% @ ${lock_price:,.2f}")
            if self.config.use_n_lock_n:
                if max_pnl < 1.0:
                    lines.append("   下階段: 達 +1.0% → 🔐 鎖住 +1%")
                else:
                    next_level = int(max_pnl) + 1
                    lines.append(f"   下階段: 達 +{next_level}.0% → 🔐 鎖住 +{next_level}%")

        hold_min = hold_time / 60
        lines.append(f"持倉: {hold_min:.1f}/{self.config.max_hold_minutes:.0f}分鐘")

        # 🆕 真實持倉狀態 (獨立顯示)
        if self.real_position:
            real_pnl_pct = self.real_position.pnl_pct(mid)
            real_pnl_usd = self.real_position.pnl_usd(mid)
            real_max_pnl = self.real_position.update_max_pnl(mid)
            lines.append(
                f"🔴 真實: {self.real_position.side} @ ${self.real_position.entry_price:,.2f} → "
                f"${mid:,.2f} | 獲利: {real_pnl_pct:+.2f}% (${real_pnl_usd:+.2f}) | 最高: {real_max_pnl:.2f}%"
            )

            slippage = abs(self.real_position.entry_price - pos.entry_price)
            if slippage > 0.01:
                lines.append(f"   滑點: ${slippage:.2f}")

        logger.info("\n" + "\n".join(lines))
    
    async def _open_position(
        self,
        side: str,
        price: float,
        strategy: str = "HYBRID",
        entry_type: str = "TAKER",
    ):
        """
        開倉 (同步模式: 真實先成交 → 虛擬才記錄)
        
        🎯 同步邏輯:
        1. 先嘗試真實開倉 (Aggressive Maker)
        2. 成交後 → 虛擬用真實成交價記錄
        3. 未成交 → 虛擬也不開 (保持一致)
        """
        
        if self.config.sync_real_trading:
            # ═══════════════════════════════════════════════════════════════
            # 同步模式: 真實優先
            # ═══════════════════════════════════════════════════════════════
            real_filled, fill_price = await self._sync_open_real_position(side, price)
            
            if real_filled and fill_price > 0:
                # ✅ 真實成交 → 虛擬用真實成交價
                if self.paper_engine:
                    self.paper_engine.open_position(
                        side,
                        fill_price,
                        strategy=strategy,
                        entry_type="MAKER",
                    )
                    logger.info(f"📝 虛擬同步: {side} @ ${fill_price:,.2f} (與真實一致)")
            else:
                # ❌ 真實未成交 → 虛擬也不開
                logger.warning("⏱️ 真實掛單未成交，虛擬也跳過此次開倉")
        else:
            # ═══════════════════════════════════════════════════════════════
            # 純虛擬模式
            # ═══════════════════════════════════════════════════════════════
            if self.paper_engine:
                self.paper_engine.open_position(
                    side,
                    price,
                    strategy=strategy,
                    entry_type=entry_type,
                )
    
    async def _sync_open_real_position(self, side: str, price: float) -> tuple[bool, float]:
        """
        同步開真實倉位 - 使用 Aggressive Maker (零滑點策略)
        
        🎯 核心改進:
        - 使用限價掛單而非市價單
        - 超時 5 秒未成交 → 放棄 (信號已過期)
        - 寧可錯過，不接受滑點
        
        Returns:
            (是否成交, 成交價格)
        """
        try:
            # 取得帳戶餘額 (複利: 每次都用最新餘額)
            balance = await self.api.get_account_balance()
            if balance <= 0:
                logger.warning("⚠️  真實帳戶餘額不足，跳過同步")
                return False, 0.0
            
            # 計算真實倉位大小
            if self.config.fixed_btc_size > 0:
                # 固定 BTC 倉位 (不複利)
                size = self.config.fixed_btc_size
                margin = size * price / self.config.leverage
            else:
                # 百分比倉位 (複利)
                margin = balance * self.config.real_position_size_pct
                notional = margin * self.config.leverage
                size = notional / price
                logger.info(f"💰 複利計算: 餘額 ${balance:.2f} × {self.config.real_position_size_pct*100:.1f}% = ${margin:.2f} 保證金")
            
            # dYdX BTC-USD 最小單位 0.0001
            size = round(size, 4)
            if size < 0.0001:
                size = 0.0001
            
            logger.info(f"🔴 同步真實開倉 (Maker): {side} {size:.4f} BTC (${margin:.2f} 保證金)")
            
            # 🆕 使用 Aggressive Maker 下單 (5 秒超時)
            tx_hash, fill_price = await self.api.place_aggressive_limit_order(
                side=side, 
                size=size, 
                timeout_seconds=5.0,
                price_offset=1.0  # $1 偏移，搶第一檔
            )
            
            if tx_hash and fill_price > 0:
                self.real_position_size = size
                
                # 建立真實持倉追蹤 (使用實際成交價)
                self.real_position = RealPosition(
                    side=side,
                    size=size,
                    entry_price=fill_price,  # 🔑 使用 Maker 成交價
                    entry_time=datetime.now(),
                    leverage=self.config.leverage
                )
                
                slippage = fill_price - price
                logger.info(f"✅ Maker 成交! 價格: ${fill_price:,.2f} (vs Oracle ${price:,.2f}, 差異: ${slippage:+.2f})")
                return True, fill_price
            else:
                logger.warning("⏱️ 掛單超時未成交，放棄本次開倉")
                logger.info("💡 原因: 市場快速移動或流動性不足，信號可能已過期")
                return False, 0.0
                
        except Exception as e:
            logger.error(f"同步開倉錯誤: {e}")
            return False, 0.0
    
    async def _get_real_entry_price(self) -> float:
        """從 API 取得真實持倉的成交價"""
        try:
            positions = await self.api.get_positions()
            for pos in positions:
                if pos.get("market") == self.config.symbol:
                    size = float(pos.get("size", 0))
                    if size != 0:  # 有開放持倉
                        return float(pos.get("entryPrice", 0))
            return 0.0
        except Exception as e:
            logger.warning(f"取得真實成交價失敗: {e}")
            return 0.0
    
    async def _check_exit(self, price: float):
        """檢查是否平倉 (混合策略) - 虛擬和真實獨立計算"""
        if not self.paper_engine or not self.paper_engine.position:
            return
        
        pos = self.paper_engine.position
        paper_pnl_pct = pos.pnl_pct(price)
        hold_time = (datetime.now() - pos.entry_time).total_seconds()
        
        # 更新 Paper 最高獲利
        paper_max_pnl = pos.update_max_pnl(price)
        
        # 🆕 獨立計算真實持倉的獲利%
        real_pnl_pct = 0.0
        real_max_pnl = 0.0
        if self.real_position:
            real_pnl_pct = self.real_position.pnl_pct(price)
            real_max_pnl = self.real_position.update_max_pnl(price)
        
        reason = None
        real_reason = None  # 🆕 真實持倉的平倉理由
        
        # ═══════════════════════════════════════════════════════════════
        # 🔄 三線反轉檢測 (緊急煞車 - 虧損時優先)
        # ═══════════════════════════════════════════════════════════════
        if self.reversal_detector and self.config.reversal_enabled:
            is_reversal, accumulated, reversal_reason = await self.reversal_detector.check_reversal(pos.side)
            
            if is_reversal:
                # Paper: 虧損時絕對執行，微利時保護
                if paper_pnl_pct < 0:
                    reason = f"🔄{reversal_reason}"
                elif paper_pnl_pct < self.config.trailing_start_pct:
                    reason = f"🔄{reversal_reason} (微利保護)"
                
                # 🆕 Real: 獨立判斷
                if self.real_position:
                    if real_pnl_pct < 0:
                        real_reason = f"🔄{reversal_reason}"
                    elif real_pnl_pct < self.config.trailing_start_pct:
                        real_reason = f"🔄{reversal_reason} (微利保護)"
        
        # ═══════════════════════════════════════════════════════════════
        # 1️⃣ 追蹤止盈 - 鎖定利潤 (不受最短持倉限制)
        # ═══════════════════════════════════════════════════════════════
        # Paper 追蹤止盈
        if not reason and paper_max_pnl >= self.config.trailing_start_pct:
            drawdown = paper_max_pnl - paper_pnl_pct
            if drawdown >= self.config.trailing_offset_pct:
                reason = f"🔒追蹤止盈 (最高{paper_max_pnl:.2f}% → 現{paper_pnl_pct:.2f}%)"
        
        # 🆕 Real 追蹤止盈 (獨立計算)
        if not real_reason and self.real_position and real_max_pnl >= self.config.trailing_start_pct:
            drawdown = real_max_pnl - real_pnl_pct
            if drawdown >= self.config.trailing_offset_pct:
                real_reason = f"🔒追蹤止盈 (最高{real_max_pnl:.2f}% → 現{real_pnl_pct:.2f}%)"
        
        # 最短持倉時間檢查 (追蹤止盈和三線反轉不受此限制)
        if not reason and not real_reason and hold_time < self.config.min_hold_seconds:
            return
        
        # ═══════════════════════════════════════════════════════════════
        # 2️⃣ 止損 (標記為止損，平倉時直接市價)
        # ═══════════════════════════════════════════════════════════════
        is_stop_loss = False  # 🆕 止損標記
        real_is_stop_loss = False
        
        if not reason and paper_pnl_pct <= -self.config.phase1_stop_loss_pct:
            reason = f"🚨止損 ({paper_pnl_pct:.2f}%)"
            is_stop_loss = True
        
        # 🆕 Real 止損 (獨立)
        if not real_reason and self.real_position and real_pnl_pct <= -self.config.phase1_stop_loss_pct:
            real_reason = f"🚨止損 ({real_pnl_pct:.2f}%)"
            real_is_stop_loss = True
        
        # ═══════════════════════════════════════════════════════════════
        # 3️⃣ 固定止盈 (備用)
        # ═══════════════════════════════════════════════════════════════
        if not reason and paper_pnl_pct >= self.config.phase1_target_pct:
            reason = f"止盈 ({paper_pnl_pct:.2f}%)"
        
        # 🆕 Real 止盈 (獨立)
        if not real_reason and self.real_position and real_pnl_pct >= self.config.phase1_target_pct:
            real_reason = f"止盈 ({real_pnl_pct:.2f}%)"
        
        # ═══════════════════════════════════════════════════════════════
        # 4️⃣ 超時
        # ═══════════════════════════════════════════════════════════════
        if not reason and hold_time > self.config.max_hold_minutes * 60:
            reason = f"超時 ({hold_time/60:.1f}分鐘)"
        
        if not real_reason and self.real_position and hold_time > self.config.max_hold_minutes * 60:
            real_reason = f"超時 ({hold_time/60:.1f}分鐘)"
        
        # ═══════════════════════════════════════════════════════════════
        # 執行平倉
        # ═══════════════════════════════════════════════════════════════
        
        # 🆕 先處理真實持倉 (基於真實獲利%判斷)
        if real_reason and self.config.sync_real_trading and self.real_position:
            logger.info(f"🔴 真實持倉: {real_reason} | 獲利: {real_pnl_pct:+.2f}%")
            await self._sync_close_real_position(self.real_position.side, price, real_is_stop_loss)
            self.real_position = None
        
        # Paper 平倉
        if reason:
            side = pos.side
            logger.info(f"📝 虛擬持倉: {reason} | 獲利: {paper_pnl_pct:+.2f}%")
            self.paper_engine.close_position(price, reason)
            
            # 重置三線反轉檢測器
            if self.reversal_detector:
                self.reversal_detector.reset()
            
            # 如果虛擬平倉了但真實還沒平 (理論上不應該發生，但作為保險)
            if self.config.sync_real_trading and self.real_position:
                logger.warning("⚠️  虛擬已平倉，同步平真實倉位")
                await self._sync_close_real_position(side, price, is_stop_loss)
                self.real_position = None
    
    async def _sync_close_real_position(self, side: str, price: float, is_stop_loss: bool = False):
        """
        同步平真實倉位
        
        平倉策略:
        - 止盈/追蹤止盈/超時: 先嘗試 Maker，超時再市價
        - 止損: 🚨 直接市價 (安全優先)
        """
        try:
            real_size = self.real_position.size if self.real_position else self.real_position_size
            
            if real_size <= 0:
                return
            
            if is_stop_loss:
                logger.warning(f"🚨 止損平倉 (直接市價): 平{side} {real_size:.4f} BTC")
            else:
                logger.info(f"🔴 同步真實平倉 (Maker): 平{side} {real_size:.4f} BTC")
            
            # 🆕 傳遞止損標記
            tx_hash, fill_price = await self.api.close_position_aggressive(
                side=side, 
                size=real_size, 
                timeout_seconds=5.0,
                is_stop_loss=is_stop_loss  # 🔑 止損直接市價
            )
            
            if tx_hash:
                self.real_position_size = 0
                if fill_price > 0:
                    logger.info(f"✅ Maker 平倉成交! 價格: ${fill_price:,.2f}")
                else:
                    logger.info(f"✅ 平倉成功 (可能為緊急市價)")
                
                # 🆕 平倉後顯示 dYdX 真實餘額
                real_balance = await self.api.get_balance()
                logger.info(f"💰 dYdX 真實餘額: ${real_balance:.2f}")
            else:
                logger.error("❌ 真實平倉失敗")
                
        except Exception as e:
            logger.error(f"同步平倉錯誤: {e}")
    
    def _print_summary(self):
        """輸出總結"""
        if not self.paper_engine:
            return
        
        trades = self.paper_engine.trades
        if not trades:
            logger.info("📊 無交易記錄")
            return
        
        wins = [t for t in trades if t["pnl_usd"] > 0]
        losses = [t for t in trades if t["pnl_usd"] <= 0]
        total_pnl = sum(t["pnl_usd"] for t in trades)
        avg_win = sum(t["pnl_usd"] for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t["pnl_usd"] for t in losses) / len(losses) if losses else 0.0
        
        logger.info("=" * 60)
        logger.info("📊 交易總結")
        logger.info("=" * 60)
        logger.info(f"   總交易: {len(trades)} 筆")
        logger.info(f"   勝率: {len(wins)}/{len(trades)} ({len(wins)/len(trades)*100:.1f}%)")
        logger.info(f"   總盈虧: ${total_pnl:+.2f}")
        logger.info(f"   最佳平均: ${avg_win:+.2f} | 最差平均: ${avg_loss:+.2f}")
        logger.info(f"   最終餘額: ${self.paper_engine.balance:.2f}")
        logger.info(f"   報酬率: {(self.paper_engine.balance/self.config.paper_initial_balance-1)*100:+.2f}%")
        logger.info("=" * 60)
    
    def stop(self):
        """停止交易"""
        self.running = False


# ═══════════════════════════════════════════════════════════════════════════════
# 主程式
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="dYdX 鯨魚交易機器人")
    parser.add_argument("--paper", action="store_true", help="Paper Trading 模式 (模擬)")
    parser.add_argument("--sync", action="store_true", help="同步真實交易 (Paper + 真實)")
    parser.add_argument("--real", action="store_true", help="純真實交易 (危險!)")
    parser.add_argument("--hours", type=float, default=8.0, help="運行時間 (小時)")
    parser.add_argument("--network", type=str, default="mainnet", choices=["mainnet", "testnet"])
    parser.add_argument("--test", action="store_true", help="測試連接")
    parser.add_argument("--size", type=float, default=0.05, help="真實交易倉位比例 (預設 5%%)")
    parser.add_argument("--btc", type=float, default=0.0, help="固定 BTC 倉位大小 (覆蓋 --size)")
    parser.add_argument("--random-entry", action="store_true", help="啟用隨機入場模式")
    
    args = parser.parse_args()
    
    # 決定模式
    if args.real:
        # 純真實交易
        paper_trading = False
        sync_real = False
        print("\n⚠️  警告: 純真實交易模式!")
        confirm = input("確認要使用真實資金交易? (輸入 'yes' 確認): ")
        if confirm.lower() != 'yes':
            print("已取消")
            return
    elif args.sync:
        # Paper + 同步真實
        paper_trading = True
        sync_real = True
        print("\n⚠️  警告: 同步真實交易模式!")
        if args.btc > 0:
            print(f"   真實交易倉位: {args.btc:.4f} BTC (固定)")
        else:
            print(f"   將使用 {args.size*100:.0f}% 帳戶資金進行真實交易")
        confirm = input("確認要同步真實交易? (輸入 'yes' 確認): ")
        if confirm.lower() != 'yes':
            print("已取消")
            return
    else:
        # 純 Paper Trading
        paper_trading = True
        sync_real = False
    
    config = DydxConfig(
        network=args.network,
        paper_trading=paper_trading or args.test,
        sync_real_trading=sync_real,
        real_position_size_pct=args.size,
        fixed_btc_size=args.btc
    )
    config.random_entry_mode = args.random_entry
    
    trader = DydxWhaleTrader(config)
    
    if args.test:
        # 只測試連接
        if await trader.api.connect():
            price = await trader.api.get_price()
            funding = await trader.api.get_funding_rate()
            balance = await trader.api.get_account_balance()
            
            print("\n📊 測試結果:")
            print(f"   BTC 價格: ${price:,.2f}")
            print(f"   資金費率: {funding*100:.4f}%")
            print(f"   帳戶餘額: ${balance:,.2f}")
            
            # 檢查真實持倉
            positions = await trader.api.get_positions()
            if positions:
                print("\n📊 當前持倉:")
                for pos in positions:
                    side = pos.get("side", "")
                    size = float(pos.get("size", 0))
                    entry = float(pos.get("entryPrice", 0))
                    pnl = float(pos.get("unrealizedPnl", 0))
                    print(f"   {pos.get('market')} {side} {abs(size):.4f} BTC @ ${entry:,.2f} | PnL: ${pnl:,.2f}")
            else:
                print("\n📊 當前無持倉")
            
            score, direction, details = await trader.scorer.calculate_score()
            print(f"\n   六維評分: {score:+d}/12 → {direction}")
            print(f"   詳細: {details}")
    else:
        try:
            await trader.start(args.hours)
        except KeyboardInterrupt:
            logger.info("🛑 收到中斷信號，正在關閉...")
            trader.running = False
            trader._print_summary()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 交易機器人已停止")
