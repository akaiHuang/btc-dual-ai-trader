"""
dYdX 數據共享中心 (Data Hub)
============================

解決多 Bot 運行時的 API 限流問題：
1. 使用純 WebSocket 作為主要數據源
2. 本機 JSON 快取讓多個 bot 共享數據
3. REST 僅用於啟動初始化和 WS 斷線補救

架構:
- 第一個啟動的 bot 成為「數據主機」(master)
- 後續 bot 成為「數據消費者」(consumer)
- 所有 bot 透過本機 JSON 檔案共享數據

dYdX Indexer WebSocket 頻道:
- v4_trades: 即時交易
- v4_orderbook: 訂單簿 (實時更新)
- v4_candles: K 線數據 (實時更新)
- v4_markets: 市場資訊

Author: AI Assistant
Version: 1.0.0
"""

import json
import time
import fcntl
import os
import asyncio
import websockets
import logging
import threading
from pathlib import Path
from typing import Dict, Optional, List, Any, Callable
from dataclasses import dataclass, field, asdict
from collections import deque
from datetime import datetime, timezone
import aiohttp
import ssl

# 抑制 HTTP 請求日誌 (避免刷屏)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

# 數據快取檔案路徑
DATA_HUB_PATH = Path("/tmp/dydx_data_hub.json")
LOCK_FILE_PATH = Path("/tmp/dydx_data_hub.lock")

# 大單歷史保存路徑 (長期保存)
BIG_TRADES_DIR = Path(__file__).parent.parent / "data" / "big_trades"

# WebSocket 設定
WS_RECONNECT_DELAY = 2.0
WS_PING_INTERVAL = 30.0
WS_TIMEOUT = 60.0


@dataclass
class MarketData:
    """市場數據結構"""
    # 基本價格
    current_price: float = 0.0
    bid_price: float = 0.0
    ask_price: float = 0.0
    spread_pct: float = 0.0
    
    # 訂單簿 (top 10)
    bids: List[List[float]] = field(default_factory=list)
    asks: List[List[float]] = field(default_factory=list)
    
    # K 線 (1m, 最近 60 根)
    candles_1m: List[Dict] = field(default_factory=list)
    
    # 即時交易
    recent_trades: List[Dict] = field(default_factory=list)
    big_trades: List[Dict] = field(default_factory=list)
    
    # 成交量統計
    buy_volume_1m: float = 0.0
    sell_volume_1m: float = 0.0
    
    # 時間戳
    last_update: float = 0.0
    last_trade_time: float = 0.0
    
    # 數據來源狀態
    ws_connected: bool = False
    master_pid: int = 0
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'MarketData':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class DydxDataHub:
    """
    dYdX 數據共享中心
    
    功能:
    1. 自動選舉 master/consumer 角色
    2. Master: 維護 WebSocket 連接，寫入本機快取
    3. Consumer: 讀取本機快取，不發送任何網路請求
    
    使用方式:
    ```python
    hub = DydxDataHub(symbol="BTC-USD")
    hub.start()
    
    # 獲取數據
    data = hub.get_data()
    print(f"價格: {data.current_price}")
    
    hub.stop()
    ```
    """
    
    def __init__(
        self,
        symbol: str = "BTC-USD",
        network: str = "mainnet",
        big_trade_threshold: float = 1000.0,
        on_data_update: Optional[Callable[[MarketData], None]] = None,
        ssl_verify: bool = True,
    ):
        self.symbol = symbol
        self.network = network
        self.big_trade_threshold = big_trade_threshold
        self.on_data_update = on_data_update
        self.ssl_verify = ssl_verify
        
        # WebSocket URLs
        if network == "mainnet":
            self.ws_url = "wss://indexer.dydx.trade/v4/ws"
            self.rest_url = "https://indexer.dydx.trade/v4"
        else:
            self.ws_url = "wss://indexer.v4testnet.dydx.exchange/v4/ws"
            self.rest_url = "https://indexer.v4testnet.dydx.exchange/v4"
        
        # 角色狀態
        self._is_master = False
        self._pid = os.getpid()
        
        # 數據存儲
        self._data = MarketData()
        self._trades_buffer: deque = deque(maxlen=1000)
        
        # 控制
        self._running = False
        self._ws_thread: Optional[threading.Thread] = None
        self._read_thread: Optional[threading.Thread] = None
        self._lock_fd: Optional[int] = None
        
        # WebSocket 狀態
        self._ws_connected = False
        self._last_ws_message = 0

        # Consumer -> Master 接手機制
        self._last_takeover_attempt = 0.0
        self._takeover_warned_stale = False
        
        # 訂單簿狀態 (用於增量更新)
        self._orderbook_bids: Dict[str, float] = {}
        self._orderbook_asks: Dict[str, float] = {}
        
        # 大單歷史保存
        self._big_trades_file: Optional[Path] = None
        self._big_trades_date: str = ""
        self._saved_trade_ids: set = set()  # 避免重複保存
    
    def start(self):
        """啟動數據中心"""
        self._running = True
        
        # 嘗試獲取 master 鎖
        self._try_become_master()
        
        if self._is_master:
            logging.info(f"🔑 dYdX Data Hub: 成為 MASTER (PID: {self._pid})")
            print(f"🔑 dYdX Data Hub: 成為數據主機 (PID: {self._pid})")
            
            # Master: 初始化大單歷史保存
            self._init_big_trades_history()
            
            # Master: 啟動 WebSocket
            self._ws_thread = threading.Thread(target=self._run_ws_loop, daemon=True)
            self._ws_thread.start()
        else:
            logging.info(f"👥 dYdX Data Hub: 成為 CONSUMER (PID: {self._pid})")
            print(f"👥 dYdX Data Hub: 成為數據消費者 (PID: {self._pid})，讀取本機快取")
            
            # Consumer: 啟動本機檔案讀取
            self._read_thread = threading.Thread(target=self._run_read_loop, daemon=True)
            self._read_thread.start()
    
    def stop(self):
        """停止數據中心"""
        self._running = False
        
        # 釋放 master 鎖
        if self._is_master and self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except:
                pass
        
        logging.info(f"⏹️ dYdX Data Hub 已停止 (PID: {self._pid})")
    
    def get_data(self) -> MarketData:
        """獲取當前市場數據"""
        return self._data
    
    def _try_become_master(self):
        """嘗試成為 master"""
        try:
            # 創建/打開鎖文件
            self._lock_fd = os.open(
                str(LOCK_FILE_PATH),
                os.O_RDWR | os.O_CREAT,
                0o666
            )
            
            # 嘗試獲取排他鎖 (非阻塞)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            
            # 成功獲取鎖 = master
            self._is_master = True
            
            # 寫入 PID
            os.ftruncate(self._lock_fd, 0)
            os.lseek(self._lock_fd, 0, os.SEEK_SET)
            os.write(self._lock_fd, str(self._pid).encode())
            
        except (BlockingIOError, OSError):
            # 無法獲取鎖 = consumer
            self._is_master = False
            if self._lock_fd:
                os.close(self._lock_fd)
                self._lock_fd = None
    
    def _run_ws_loop(self):
        """Master: 運行 WebSocket 事件循環"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._ws_main())
    
    async def _ws_main(self):
        """Master: WebSocket 主循環"""
        # 啟動時先用 REST 獲取初始快照
        await self._fetch_initial_snapshot()
        
        while self._running:
            try:
                connect_kwargs = {
                    "ping_interval": WS_PING_INTERVAL,
                    "ping_timeout": WS_TIMEOUT,
                }
                
                if not self.ssl_verify:
                    ssl_opt = ssl.create_default_context()
                    ssl_opt.check_hostname = False
                    ssl_opt.verify_mode = ssl.CERT_NONE
                    connect_kwargs["ssl"] = ssl_opt
                
                async with websockets.connect(
                    self.ws_url,
                    **connect_kwargs
                ) as ws:
                    logging.info(f"✅ dYdX WebSocket 已連接 ({self.ws_url})")
                    print(f"✅ dYdX WebSocket 已連接")
                    self._ws_connected = True
                    self._data.ws_connected = True
                    self._data.master_pid = self._pid
                    
                    # 訂閱頻道
                    await self._subscribe_channels(ws)
                    
                    # 同時運行消息處理和定期保存
                    await asyncio.gather(
                        self._handle_messages(ws),
                        self._periodic_save()
                    )
                    
            except Exception as e:
                self._ws_connected = False
                self._data.ws_connected = False
                if self._running:
                    logging.warning(f"⚠️ dYdX WebSocket 斷線: {e}")
                    await asyncio.sleep(WS_RECONNECT_DELAY)
    
    async def _subscribe_channels(self, ws):
        """訂閱所有需要的頻道"""
        channels = [
            {"type": "subscribe", "channel": "v4_trades", "id": self.symbol},
            {"type": "subscribe", "channel": "v4_orderbook", "id": self.symbol},
            {"type": "subscribe", "channel": "v4_candles", "id": f"{self.symbol}/1MIN"},
        ]
        
        for msg in channels:
            await ws.send(json.dumps(msg))
            logging.debug(f"📡 訂閱: {msg['channel']}")
    
    async def _handle_messages(self, ws):
        """處理 WebSocket 消息"""
        async for message in ws:
            if not self._running:
                break
            
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")
                channel = data.get("channel", "")
                
                self._last_ws_message = time.time()
                
                if msg_type in ("subscribed", "channel_data", "channel_batch_data"):
                    contents = data.get("contents", {})
                    
                    if channel == "v4_trades":
                        self._process_trades(contents)
                    elif channel == "v4_orderbook":
                        self._process_orderbook(contents, msg_type == "subscribed")
                    elif channel == "v4_candles":
                        self._process_candles(contents)
                    
                    # 更新時間戳
                    self._data.last_update = time.time()
                    
                    # 觸發回調
                    if self.on_data_update:
                        self.on_data_update(self._data)
                        
            except Exception as e:
                logging.debug(f"WS 消息處理錯誤: {e}")
    
    def _process_trades(self, contents: Dict):
        """處理交易數據"""
        trades = contents.get("trades", [])
        
        for t in trades:
            try:
                price = float(t.get("price", 0))
                size = float(t.get("size", 0))
                side = t.get("side", "")
                
                if price <= 0:
                    continue
                
                self._data.current_price = price
                self._data.last_trade_time = time.time() * 1000
                
                value_usdt = price * size
                is_buy = side == "BUY"
                
                trade = {
                    'price': price,
                    'qty': size,
                    'is_buy': is_buy,
                    'time': self._data.last_trade_time,
                    'value_usdt': value_usdt
                }
                
                self._trades_buffer.append(trade)
                
                # 更新成交量
                if is_buy:
                    self._data.buy_volume_1m += value_usdt
                else:
                    self._data.sell_volume_1m += value_usdt
                
                # 大單追蹤
                if value_usdt >= self.big_trade_threshold:
                    if len(self._data.big_trades) >= 100:
                        self._data.big_trades = self._data.big_trades[-99:]
                    self._data.big_trades.append(trade)
                    
                    # 保存到歷史文件 (長期保存)
                    self._save_big_trade_to_history(trade)
                    
            except Exception as e:
                logging.debug(f"交易處理錯誤: {e}")
        
        # 更新最近交易列表
        self._data.recent_trades = list(self._trades_buffer)[-100:]
    
    def _process_orderbook(self, contents: Dict, is_snapshot: bool = False):
        """處理訂單簿數據 (支援增量更新)
        
        🔧 v14.9.1: 修復增量更新格式問題
        - Snapshot (訂閱時): dict 格式 {'price': '87105', 'size': '0.1148'}
        - Incremental (更新時): list 格式 ['86713', '0.5007']
        """
        def parse_order_entry(entry):
            """解析訂單簿條目 (支援 dict 和 list 兩種格式)"""
            if isinstance(entry, dict):
                # Snapshot 格式: {'price': '87105', 'size': '0.1148'}
                price = entry.get("price", "")
                size = float(entry.get("size", 0))
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                # Incremental 格式: ['86713', '0.5007']
                price = str(entry[0])
                size = float(entry[1])
            else:
                price = ""
                size = 0.0
            return price, size
        
        if is_snapshot:
            # 完整快照
            self._orderbook_bids = {}
            self._orderbook_asks = {}
            
            for b in contents.get("bids", []):
                price, size = parse_order_entry(b)
                if size > 0 and price:
                    self._orderbook_bids[price] = size
            
            for a in contents.get("asks", []):
                price, size = parse_order_entry(a)
                if size > 0 and price:
                    self._orderbook_asks[price] = size
        else:
            # 增量更新
            for b in contents.get("bids", []):
                price, size = parse_order_entry(b)
                if not price:
                    continue
                if size > 0:
                    self._orderbook_bids[price] = size
                elif price in self._orderbook_bids:
                    del self._orderbook_bids[price]
            
            for a in contents.get("asks", []):
                price, size = parse_order_entry(a)
                if not price:
                    continue
                if size > 0:
                    self._orderbook_asks[price] = size
                elif price in self._orderbook_asks:
                    del self._orderbook_asks[price]
        
        # 排序並取 top 10
        sorted_bids = sorted(
            [(float(p), s) for p, s in self._orderbook_bids.items()],
            key=lambda x: x[0],
            reverse=True
        )[:10]
        
        sorted_asks = sorted(
            [(float(p), s) for p, s in self._orderbook_asks.items()],
            key=lambda x: x[0]
        )[:10]
        
        self._data.bids = [[p, s] for p, s in sorted_bids]
        self._data.asks = [[p, s] for p, s in sorted_asks]
        
        # 更新 bid/ask 價格
        if self._data.bids:
            self._data.bid_price = self._data.bids[0][0]
        if self._data.asks:
            self._data.ask_price = self._data.asks[0][0]
        
        # 🔧 v14.9.2: 交叉檢查 - 如果 bid > ask，訂單簿可能有問題，需要修正
        if self._data.bid_price > 0 and self._data.ask_price > 0:
            if self._data.bid_price > self._data.ask_price:
                # 訂單簿交叉，取中間值作為參考
                mid_price = (self._data.bid_price + self._data.ask_price) / 2
                # 清除可能錯誤的訂單簿，等待下次更新
                self._orderbook_bids = {p: s for p, s in self._orderbook_bids.items() if float(p) <= mid_price}
                self._orderbook_asks = {p: s for p, s in self._orderbook_asks.items() if float(p) >= mid_price}
                # 重新排序
                sorted_bids = sorted([(float(p), s) for p, s in self._orderbook_bids.items()], key=lambda x: x[0], reverse=True)[:10]
                sorted_asks = sorted([(float(p), s) for p, s in self._orderbook_asks.items()], key=lambda x: x[0])[:10]
                self._data.bids = [[p, s] for p, s in sorted_bids]
                self._data.asks = [[p, s] for p, s in sorted_asks]
                if self._data.bids:
                    self._data.bid_price = self._data.bids[0][0]
                if self._data.asks:
                    self._data.ask_price = self._data.asks[0][0]
        
        # 計算 spread
        if self._data.bid_price > 0 and self._data.ask_price > 0:
            self._data.spread_pct = (self._data.ask_price - self._data.bid_price) / self._data.bid_price * 100
        
        # 🔧 v14.9: 更新時間戳 (orderbook 更新頻率高，確保數據新鮮度)
        self._data.last_trade_time = time.time() * 1000
    
    def _process_candles(self, contents: Dict):
        """處理 K 線數據"""
        candles = contents.get("candles", [])
        
        if candles:
            # 更新或追加 K 線
            for candle in candles:
                started_at = candle.get("startedAt", "")
                
                # 查找是否已存在
                found = False
                for i, existing in enumerate(self._data.candles_1m):
                    if existing.get("startedAt") == started_at:
                        self._data.candles_1m[i] = candle
                        found = True
                        break
                
                if not found:
                    self._data.candles_1m.append(candle)
            
            # 保留最近 60 根
            self._data.candles_1m = sorted(
                self._data.candles_1m,
                key=lambda x: x.get("startedAt", ""),
                reverse=True
            )[:60]
    
    async def _periodic_save(self):
        """定期保存數據到本機檔案"""
        while self._running:
            try:
                # 每 100ms 保存一次
                await asyncio.sleep(0.1)
                
                # 清理舊成交量 (每分鐘重置)
                self._cleanup_volumes()
                
                # 保存到檔案
                self._save_to_file()
                
            except Exception as e:
                logging.debug(f"保存錯誤: {e}")
    
    def _cleanup_volumes(self):
        """清理舊的成交量統計"""
        now = time.time() * 1000
        one_minute_ago = now - 60000
        
        # 過濾舊交易
        old_buy = self._data.buy_volume_1m
        old_sell = self._data.sell_volume_1m
        
        self._data.buy_volume_1m = sum(
            t['value_usdt'] for t in self._trades_buffer
            if t['time'] > one_minute_ago and t['is_buy']
        )
        self._data.sell_volume_1m = sum(
            t['value_usdt'] for t in self._trades_buffer
            if t['time'] > one_minute_ago and not t['is_buy']
        )
    
    def _save_to_file(self):
        """保存數據到本機檔案 (原子寫入)"""
        try:
            temp_path = DATA_HUB_PATH.with_suffix('.tmp')
            
            with open(temp_path, 'w') as f:
                json.dump(self._data.to_dict(), f)
            
            # 原子替換
            temp_path.rename(DATA_HUB_PATH)
            
        except Exception as e:
            logging.debug(f"檔案保存錯誤: {e}")
    
    # ===== 大單歷史保存功能 =====
    
    def _init_big_trades_history(self):
        """初始化大單歷史保存目錄"""
        try:
            BIG_TRADES_DIR.mkdir(parents=True, exist_ok=True)
            self._rotate_big_trades_file()
            logging.info(f"📁 大單歷史保存目錄: {BIG_TRADES_DIR}")
        except Exception as e:
            logging.warning(f"⚠️ 無法建立大單歷史目錄: {e}")
    
    def _rotate_big_trades_file(self):
        """根據日期切換大單歷史文件"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._big_trades_date:
            self._big_trades_date = today
            self._big_trades_file = BIG_TRADES_DIR / f"{self.symbol}_{today}.jsonl"
            self._saved_trade_ids.clear()
            
            # 載入已保存的交易 ID (避免重複)
            if self._big_trades_file.exists():
                try:
                    with open(self._big_trades_file, 'r') as f:
                        for line in f:
                            trade = json.loads(line.strip())
                            trade_id = f"{trade['time']}_{trade['price']}_{trade['qty']}"
                            self._saved_trade_ids.add(trade_id)
                except Exception:
                    pass
    
    def _save_big_trade_to_history(self, trade: Dict):
        """保存單筆大單到歷史文件 (JSONL 格式)"""
        if not self._is_master or not self._big_trades_file:
            return
        
        try:
            # 檢查日期是否需要切換
            self._rotate_big_trades_file()
            
            # 生成交易 ID (避免重複)
            trade_id = f"{trade['time']}_{trade['price']}_{trade['qty']}"
            if trade_id in self._saved_trade_ids:
                return
            
            # 增強交易記錄
            enhanced_trade = {
                **trade,
                'symbol': self.symbol,
                'datetime': datetime.fromtimestamp(
                    trade['time'] / 1000, tz=timezone.utc
                ).isoformat(),
                'side': 'BUY' if trade['is_buy'] else 'SELL',
                'bid_price': self._data.bid_price,
                'ask_price': self._data.ask_price,
                'spread_pct': self._data.spread_pct,
            }
            
            # 追加到 JSONL 文件
            with open(self._big_trades_file, 'a') as f:
                f.write(json.dumps(enhanced_trade) + '\n')
            
            self._saved_trade_ids.add(trade_id)
            logging.debug(f"💾 大單已保存: ${trade['value_usdt']:,.0f}")
            
        except Exception as e:
            logging.debug(f"大單保存錯誤: {e}")
    
    def _run_read_loop(self):
        """Consumer: 讀取本機快取"""
        while self._running:
            try:
                self._read_from_file()
                # 🆕 Consumer failover: 若 master 掛掉/鎖釋放，consumer 會自動接手成為 master
                # 避免 /tmp 快取卡住，導致價格長時間不更新。
                if not self._is_master:
                    self._maybe_takeover_master()
                    if self._is_master:
                        return  # 已升級成 master，read loop 結束（改由 WS loop 更新）
                time.sleep(0.05)  # 50ms 讀取間隔
            except Exception as e:
                logging.debug(f"讀取錯誤: {e}")
                time.sleep(0.1)

    def _maybe_takeover_master(self):
        """
        Consumer: 監測快取是否停更，必要時嘗試取得鎖並接手成為 master。
        
        🔧 v14.10 增強: 
        - 如果鎖檔案無法釋放但 master PID 已不存在，強制刪除鎖檔案
        - 避免殭屍鎖導致數據無限期過期
        """
        now = time.time()

        # 節流：避免每 50ms 都嘗試搶鎖
        if now - self._last_takeover_attempt < 2.0:
            return
        self._last_takeover_attempt = now

        # 若快取檔不存在，或 last_update 過舊，才需要嘗試接手
        last_update = getattr(self._data, "last_update", 0.0) or 0.0
        data_age = (now - last_update) if last_update > 0 else float("inf")
        stale = (not DATA_HUB_PATH.exists()) or data_age > 5.0
        if not stale:
            return

        if not self._takeover_warned_stale:
            self._takeover_warned_stale = True
            try:
                logging.warning(f"⚠️ dYdX DataHub 快取停更 {data_age:.1f}s，嘗試接手成為 master...")
            except Exception:
                pass

        old_master_pid = getattr(self._data, "master_pid", 0)
        
        # 🔧 v14.10: 檢查 master PID 是否還活著
        if old_master_pid > 0 and data_age > 10.0:
            try:
                os.kill(old_master_pid, 0)  # 檢查進程是否存在
            except OSError:
                # 進程不存在，強制刪除鎖檔案
                try:
                    logging.warning(f"🗑️ 舊 master (PID: {old_master_pid}) 已死亡，清理殭屍鎖...")
                    LOCK_FILE_PATH.unlink(missing_ok=True)
                except Exception as e:
                    logging.debug(f"清理鎖檔案失敗: {e}")
        
        self._try_become_master()
        if not self._is_master:
            return

        # 成功取得鎖：升級為 master
        try:
            logging.info(f"🔑 dYdX Data Hub: Consumer 接手成為 MASTER (PID: {self._pid}, old_master_pid={old_master_pid})")
        except Exception:
            pass
        print(f"🔑 dYdX Data Hub: Consumer 接手成為數據主機 (PID: {self._pid})")

        # 更新共享狀態（即使 WS 尚未連上，也先寫入 pid，避免其他 consumer 誤判）
        try:
            self._data.master_pid = self._pid
            self._data.ws_connected = False
            self._data.last_update = time.time()
            self._save_to_file()
        except Exception:
            pass

        # 初始化大單歷史保存 + 啟動 WebSocket
        try:
            self._init_big_trades_history()
        except Exception:
            pass
        self._ws_thread = threading.Thread(target=self._run_ws_loop, daemon=True)
        self._ws_thread.start()
    
    def _read_from_file(self):
        """從本機檔案讀取數據"""
        try:
            if not DATA_HUB_PATH.exists():
                return
            
            with open(DATA_HUB_PATH, 'r') as f:
                data = json.load(f)
            
            self._data = MarketData.from_dict(data)
            
            # 觸發回調
            if self.on_data_update:
                self.on_data_update(self._data)
                
        except (json.JSONDecodeError, FileNotFoundError):
            pass
    
    async def _fetch_initial_snapshot(self):
        """啟動時獲取初始數據快照 (僅 master 使用一次)"""
        logging.info("📥 獲取初始數據快照...")
        
        try:
            connector = None
            if not self.ssl_verify:
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                # 獲取訂單簿
                async with session.get(
                    f"{self.rest_url}/orderbooks/perpetualMarket/{self.symbol}"
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._process_orderbook(data, is_snapshot=True)
                
                # 獲取 K 線
                async with session.get(
                    f"{self.rest_url}/candles/perpetualMarkets/{self.symbol}",
                    params={"resolution": "1MIN", "limit": 60}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._data.candles_1m = data.get("candles", [])
                
                # 獲取最近交易
                async with session.get(
                    f"{self.rest_url}/trades/perpetualMarket/{self.symbol}",
                    params={"limit": 100}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        trades = data.get("trades", [])
                        for t in trades:
                            price = float(t.get("price", 0))
                            if price > 0:
                                self._data.current_price = price
                                break
            
            logging.info("✅ 初始數據快照完成")
            
        except Exception as e:
            logging.warning(f"⚠️ 初始快照獲取失敗: {e}")
    
    # ===== 兼容舊 API 的屬性 =====
    
    @property
    def current_price(self) -> float:
        return self._data.current_price
    
    @property
    def bid_price(self) -> float:
        return self._data.bid_price
    
    @property
    def ask_price(self) -> float:
        return self._data.ask_price
    
    @property
    def bids(self) -> List[List[float]]:
        return self._data.bids
    
    @property
    def asks(self) -> List[List[float]]:
        return self._data.asks
    
    @property
    def big_trades(self) -> List[Dict]:
        return self._data.big_trades
    
    @property
    def trades_1s(self):
        """兼容舊 API: 返回最近 1 秒的交易"""
        now = time.time() * 1000
        return [t for t in self._data.recent_trades if now - t['time'] < 1000]
    
    @property
    def trades_1m(self):
        """兼容舊 API: 返回最近 1 分鐘的交易"""
        now = time.time() * 1000
        return [t for t in self._data.recent_trades if now - t['time'] < 60000]
    
    @property
    def buy_volume_1s(self) -> float:
        now = time.time() * 1000
        return sum(
            t['value_usdt'] for t in self._data.recent_trades
            if now - t['time'] < 1000 and t['is_buy']
        )
    
    @property
    def sell_volume_1s(self) -> float:
        now = time.time() * 1000
        return sum(
            t['value_usdt'] for t in self._data.recent_trades
            if now - t['time'] < 1000 and not t['is_buy']
        )
    
    def get_price_change(self, period_seconds: int) -> float:
        """計算價格變化百分比"""
        if not self._data.candles_1m:
            return 0.0
        
        try:
            current = self._data.current_price
            if current <= 0:
                return 0.0
            
            # 根據時間找對應的 K 線
            candles_needed = max(1, period_seconds // 60)
            if len(self._data.candles_1m) > candles_needed:
                old_candle = self._data.candles_1m[candles_needed]
                old_price = float(old_candle.get("open", current))
                return ((current - old_price) / old_price) * 100
            
        except Exception:
            pass
        
        return 0.0


# ===== 全局實例管理 =====

_global_hub: Optional[DydxDataHub] = None

def get_data_hub(
    symbol: str = "BTC-USD",
    network: str = "mainnet",
    ssl_verify: bool = True,
) -> DydxDataHub:
    """
    獲取全局 Data Hub 實例
    
    使用方式:
    ```python
    from src.dydx_data_hub import get_data_hub
    
    hub = get_data_hub()
    hub.start()
    
    data = hub.get_data()
    print(f"BTC: ${data.current_price}")
    ```
    """
    global _global_hub
    
    if _global_hub is None:
        _global_hub = DydxDataHub(symbol=symbol, network=network, ssl_verify=ssl_verify)
    
    return _global_hub


def cleanup_data_hub():
    """清理全局 Data Hub"""
    global _global_hub
    if _global_hub:
        _global_hub.stop()
        _global_hub = None


if __name__ == "__main__":
    # 測試
    import sys
    
    logging.basicConfig(level=logging.INFO)
    
    print("🧪 測試 dYdX Data Hub...")
    
    hub = DydxDataHub(symbol="BTC-USD")
    hub.start()
    
    try:
        for i in range(30):
            time.sleep(1)
            data = hub.get_data()
            print(
                f"[{i+1}s] 價格: ${data.current_price:,.2f} | "
                f"Bid: ${data.bid_price:,.2f} | Ask: ${data.ask_price:,.2f} | "
                f"Spread: {data.spread_pct:.4f}% | "
                f"WS: {'✅' if data.ws_connected else '❌'} | "
                f"Master: {data.master_pid}"
            )
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop()
        print("✅ 測試完成")
