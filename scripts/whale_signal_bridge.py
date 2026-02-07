#!/usr/bin/env python3
"""
🐋 Whale Signal Bridge - 交易信號橋接系統

這個模組負責：
1. 將 whale_testnet_trader.py 的交易信號即時發送
2. 透過 Socket 傳輸給真實交易執行器
3. 記錄信號歷史供分析比對

架構：
  whale_testnet_trader.py (Paper Trading)
      ↓ WhaleSignalBridge.send_signal()
  whale_signal_bridge.py (本檔案)
      ↓ Socket 發送 (port 9528)
  real_binance_executor.py
      ↓ 執行真實交易
  Binance API (正式網)

Author: AI Assistant
Date: 2025-12-02
"""

import json
import os
import threading
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum
import logging

# 設定 logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WhaleSignalBridge")

# ==========================================
# 配置
# ==========================================
SIGNAL_BRIDGE_FILE = "data/whale_trade_signals.json"
SOCKET_HOST = "127.0.0.1"
SOCKET_PORT = 9528  # 🐋 Whale 專用 port

# 信號過期時間（秒）
SIGNAL_EXPIRY_SECONDS = 60


class SignalAction(Enum):
    """信號動作類型"""
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"


class SignalStatus(Enum):
    """信號狀態"""
    PENDING = "PENDING"          # 等待執行
    EXECUTING = "EXECUTING"      # 執行中
    EXECUTED = "EXECUTED"        # 已執行
    FAILED = "FAILED"            # 執行失敗
    EXPIRED = "EXPIRED"          # 已過期


@dataclass
class WhaleTradeSignal:
    """🐋 交易信號資料結構"""
    signal_id: str                    # 唯一信號 ID
    timestamp: str                    # 信號產生時間
    symbol: str                       # 交易對 (BTCUSDT)
    action: str                       # 動作 (OPEN_LONG, CLOSE_SHORT, etc.)
    side: str                         # BUY / SELL
    
    # 倉位資訊
    entry_price: float                # 進場價格
    quantity_usdt: float              # 數量 (USDT)
    quantity_btc: float               # 數量 (BTC)
    leverage: int                     # 槓桿倍數
    
    # 止損止盈
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    
    # 策略資訊
    strategy_name: str = ""           # 策略名稱
    strategy_confidence: float = 0.0  # 策略信心度
    
    # Paper Trading 資訊
    paper_pnl: float = 0.0            # Paper Trading 損益
    paper_pnl_pct: float = 0.0        # Paper Trading 損益百分比
    
    # 狀態追蹤
    status: str = "PENDING"
    
    # 真實交易對照（由 executor 填入）
    real_order_id: Optional[str] = None
    real_entry_price: Optional[float] = None
    real_executed_time: Optional[str] = None
    real_pnl: Optional[float] = None
    slippage_pct: Optional[float] = None
    latency_ms: Optional[int] = None


# ==========================================
# Socket Server
# ==========================================
class WhaleSocketServer:
    """🐋 信號 Socket 伺服器"""
    
    def __init__(self, host: str = SOCKET_HOST, port: int = SOCKET_PORT):
        self.host = host
        self.port = port
        self.clients: set = set()
        self.server = None
        self.running = False
        self._loop = None
        self._thread = None
        
    async def handle_client(self, reader, writer):
        """處理新連線"""
        addr = writer.get_extra_info('peername')
        logger.info(f"🔗 新連線: {addr}")
        self.clients.add(writer)
        
        try:
            while self.running:
                try:
                    data = await asyncio.wait_for(reader.read(1024), timeout=30)
                    if not data:
                        break
                    
                    message = data.decode().strip()
                    if message == "PING":
                        writer.write(b"PONG\n")
                        await writer.drain()
                    elif message == "SUBSCRIBE":
                        writer.write(b"OK:SUBSCRIBED\n")
                        await writer.drain()
                        logger.info(f"📡 客戶端已訂閱: {addr}")
                    elif message.startswith("ACK:"):
                        # 確認收到信號
                        signal_id = message.split(":")[1]
                        logger.info(f"✅ 客戶端確認收到信號: {signal_id}")
                        
                except asyncio.TimeoutError:
                    # 發送心跳
                    try:
                        writer.write(b"HEARTBEAT\n")
                        await writer.drain()
                    except:
                        break
                        
        except Exception as e:
            logger.error(f"❌ 客戶端錯誤 {addr}: {e}")
        finally:
            self.clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass
            logger.info(f"🔌 斷線: {addr}")
    
    async def broadcast_signal(self, signal_data: dict):
        """廣播信號給所有客戶端"""
        if not self.clients:
            logger.warning("⚠️ 無已連線客戶端")
            return 0
        
        message = json.dumps(signal_data) + "\n"
        message_bytes = message.encode()
        
        sent_count = 0
        disconnected = set()
        
        for writer in self.clients:
            try:
                writer.write(message_bytes)
                await writer.drain()
                sent_count += 1
            except Exception as e:
                logger.error(f"❌ 發送失敗: {e}")
                disconnected.add(writer)
        
        for writer in disconnected:
            self.clients.discard(writer)
            try:
                writer.close()
            except:
                pass
        
        return sent_count
    
    async def start_async(self):
        """異步啟動伺服器"""
        self.running = True
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port
        )
        
        addr = self.server.sockets[0].getsockname()
        logger.info(f"🐋 Whale Socket Server 啟動: {addr[0]}:{addr[1]}")
        
        async with self.server:
            await self.server.serve_forever()
    
    def start_in_thread(self):
        """在新執行緒中啟動伺服器"""
        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self.start_async())
            except Exception as e:
                logger.error(f"❌ Socket Server 錯誤: {e}")
        
        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()
        logger.info(f"🐋 Socket Server 執行緒已啟動 (port {self.port})")
        return self
    
    def send_signal(self, signal_data: dict) -> int:
        """同步發送信號"""
        if not self._loop or not self.running:
            logger.warning("⚠️ Server 未啟動")
            return 0
        
        future = asyncio.run_coroutine_threadsafe(
            self.broadcast_signal(signal_data),
            self._loop
        )
        try:
            return future.result(timeout=5)
        except Exception as e:
            logger.error(f"❌ 發送信號失敗: {e}")
            return 0
    
    def get_client_count(self) -> int:
        """取得已連線客戶端數量"""
        return len(self.clients)
    
    def stop(self):
        """停止伺服器"""
        self.running = False
        if self.server:
            self.server.close()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("🛑 Socket Server 已停止")


# ==========================================
# Socket Client
# ==========================================
class WhaleSocketClient:
    """🐋 信號 Socket 客戶端"""
    
    def __init__(self, host: str = SOCKET_HOST, port: int = SOCKET_PORT):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self.connected = False
        self.on_signal: Optional[Callable[[dict], None]] = None
        self._loop = None
        self._thread = None
        self.running = False
        self._reconnect_delay = 1  # 重連延遲（秒）
        
    async def connect_async(self) -> bool:
        """異步連線"""
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
            self.connected = True
            
            # 訂閱信號
            self.writer.write(b"SUBSCRIBE\n")
            await self.writer.drain()
            
            # 等待確認
            response = await asyncio.wait_for(self.reader.readline(), timeout=5)
            if b"OK:SUBSCRIBED" in response:
                logger.info(f"🔗 已連線並訂閱: {self.host}:{self.port}")
                return True
            else:
                logger.warning(f"⚠️ 訂閱回應異常: {response}")
                return True  # 仍然視為連線成功
                
        except Exception as e:
            logger.error(f"❌ 連線失敗: {e}")
            self.connected = False
            return False
    
    async def listen_async(self):
        """異步監聽信號"""
        while self.running:
            try:
                if not self.connected:
                    # 嘗試重連
                    logger.info(f"🔄 嘗試重連...")
                    if await self.connect_async():
                        self._reconnect_delay = 1
                    else:
                        await asyncio.sleep(self._reconnect_delay)
                        self._reconnect_delay = min(self._reconnect_delay * 2, 30)
                        continue
                
                # 讀取訊息
                data = await asyncio.wait_for(
                    self.reader.readline(), 
                    timeout=35  # 比心跳間隔長一點
                )
                
                if not data:
                    logger.warning("⚠️ 連線中斷")
                    self.connected = False
                    continue
                
                message = data.decode().strip()
                
                if message == "HEARTBEAT":
                    # 回應心跳
                    self.writer.write(b"PING\n")
                    await self.writer.drain()
                elif message == "PONG":
                    pass  # 心跳回應
                elif message.startswith("{"):
                    # JSON 信號
                    try:
                        signal_data = json.loads(message)
                        logger.info(f"📥 收到信號: {signal_data.get('action')} @ ${signal_data.get('entry_price', 0):,.2f}")
                        
                        # 發送確認
                        signal_id = signal_data.get('signal_id', 'unknown')
                        self.writer.write(f"ACK:{signal_id}\n".encode())
                        await self.writer.drain()
                        
                        # 回調處理
                        if self.on_signal:
                            self.on_signal(signal_data)
                            
                    except json.JSONDecodeError as e:
                        logger.error(f"❌ JSON 解析失敗: {e}")
                        
            except asyncio.TimeoutError:
                # 發送心跳
                if self.connected and self.writer:
                    try:
                        self.writer.write(b"PING\n")
                        await self.writer.drain()
                    except:
                        self.connected = False
            except Exception as e:
                logger.error(f"❌ 監聽錯誤: {e}")
                self.connected = False
                await asyncio.sleep(1)
    
    def start_in_thread(self, on_signal: Callable[[dict], None]):
        """在新執行緒中啟動監聽"""
        self.on_signal = on_signal
        self.running = True
        
        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self.listen_async())
            except Exception as e:
                logger.error(f"❌ Client 錯誤: {e}")
        
        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()
        logger.info(f"🐋 Socket Client 執行緒已啟動")
        return self
    
    def stop(self):
        """停止客戶端"""
        self.running = False
        self.connected = False
        if self.writer:
            try:
                self.writer.close()
            except:
                pass
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("🛑 Socket Client 已停止")


# ==========================================
# Signal Bridge
# ==========================================
class WhaleSignalBridge:
    """🐋 信號橋接器主類別"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        """單例模式"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def get_instance(cls, **kwargs):
        """取得單例實例"""
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance
    
    def __init__(self, enable_socket: bool = True):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        
        self.signals: Dict[str, WhaleTradeSignal] = {}
        self.signal_lock = threading.Lock()
        self._signal_counter = 0
        
        # Socket Server
        self.socket_enabled = enable_socket
        self._socket_server: Optional[WhaleSocketServer] = None
        
        # 確保資料目錄存在
        os.makedirs("data", exist_ok=True)
        
        # 載入歷史信號
        self._load_signals()
        
        # 啟動 Socket Server
        if enable_socket:
            self._start_socket_server()
    
    def _start_socket_server(self):
        """啟動 Socket Server"""
        try:
            self._socket_server = WhaleSocketServer()
            self._socket_server.start_in_thread()
            print(f"🔌 Whale Socket Server 已啟動 (port {SOCKET_PORT})")
        except Exception as e:
            logger.error(f"❌ Socket Server 啟動失敗: {e}")
            self.socket_enabled = False
    
    def _generate_signal_id(self) -> str:
        """產生唯一信號 ID"""
        self._signal_counter += 1
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"WS_{timestamp}_{self._signal_counter:04d}"
    
    def _load_signals(self):
        """載入歷史信號"""
        if os.path.exists(SIGNAL_BRIDGE_FILE):
            try:
                with open(SIGNAL_BRIDGE_FILE, 'r') as f:
                    data = json.load(f)
                    for signal_data in data.get('signals', []):
                        signal = WhaleTradeSignal(**signal_data)
                        self.signals[signal.signal_id] = signal
                logger.info(f"📂 載入 {len(self.signals)} 個歷史信號")
            except Exception as e:
                logger.error(f"❌ 載入信號失敗: {e}")
    
    def _save_signals(self):
        """儲存信號到檔案"""
        with self.signal_lock:
            # 只保留最近 100 筆
            recent_signals = sorted(
                self.signals.values(),
                key=lambda s: s.timestamp,
                reverse=True
            )[:100]
            
            data = {
                'last_updated': datetime.now().isoformat(),
                'total_signals': len(recent_signals),
                'signals': [asdict(s) for s in recent_signals]
            }
            
            with open(SIGNAL_BRIDGE_FILE, 'w') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
    
    def send_signal(
        self,
        action: str,
        symbol: str,
        entry_price: float,
        quantity_usdt: float,
        quantity_btc: float,
        leverage: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        strategy_name: str = "",
        strategy_confidence: float = 0.0,
        paper_pnl: float = 0.0,
        paper_pnl_pct: float = 0.0
    ) -> Optional[str]:
        """
        發送交易信號
        
        Args:
            action: 動作 (OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT)
            symbol: 交易對 (BTCUSDT)
            entry_price: 進場/出場價格
            quantity_usdt: 交易金額 (USDT)
            quantity_btc: 交易數量 (BTC)
            leverage: 槓桿倍數
            stop_loss: 止損價格
            take_profit: 止盈價格
            strategy_name: 策略名稱
            strategy_confidence: 策略信心度
            paper_pnl: Paper Trading 損益
            paper_pnl_pct: Paper Trading 損益百分比
            
        Returns:
            signal_id 或 None（發送失敗時）
        """
        signal_id = self._generate_signal_id()
        timestamp = datetime.now().isoformat()
        
        # 判斷 side
        if action in ['OPEN_LONG', 'CLOSE_SHORT']:
            side = 'BUY'
        else:
            side = 'SELL'
        
        signal = WhaleTradeSignal(
            signal_id=signal_id,
            timestamp=timestamp,
            symbol=symbol,
            action=action,
            side=side,
            entry_price=entry_price,
            quantity_usdt=quantity_usdt,
            quantity_btc=quantity_btc,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy_name=strategy_name,
            strategy_confidence=strategy_confidence,
            paper_pnl=paper_pnl,
            paper_pnl_pct=paper_pnl_pct,
            status="PENDING"
        )
        
        # 儲存信號
        with self.signal_lock:
            self.signals[signal_id] = signal
        self._save_signals()
        
        # 透過 Socket 發送
        if self.socket_enabled and self._socket_server:
            signal_data = asdict(signal)
            sent_count = self._socket_server.send_signal(signal_data)
            
            if sent_count > 0:
                print(f"   ⚡ Socket 即時發送: {sent_count} 客戶端")
                logger.info(f"📤 信號已發送: {action} @ ${entry_price:,.2f} -> {sent_count} 客戶端")
            else:
                logger.warning(f"⚠️ 無客戶端接收信號")
        else:
            logger.warning(f"⚠️ Socket 未啟用，僅儲存到檔案")
        
        return signal_id
    
    def update_signal_status(
        self,
        signal_id: str,
        status: str,
        real_order_id: Optional[str] = None,
        real_entry_price: Optional[float] = None,
        real_pnl: Optional[float] = None,
        slippage_pct: Optional[float] = None,
        latency_ms: Optional[int] = None
    ):
        """更新信號狀態（由 executor 回報）"""
        with self.signal_lock:
            if signal_id in self.signals:
                signal = self.signals[signal_id]
                signal.status = status
                signal.real_executed_time = datetime.now().isoformat()
                
                if real_order_id:
                    signal.real_order_id = real_order_id
                if real_entry_price:
                    signal.real_entry_price = real_entry_price
                if real_pnl is not None:
                    signal.real_pnl = real_pnl
                if slippage_pct is not None:
                    signal.slippage_pct = slippage_pct
                if latency_ms is not None:
                    signal.latency_ms = latency_ms
                
                self._save_signals()
                logger.info(f"📝 信號狀態更新: {signal_id} -> {status}")
    
    def get_client_count(self) -> int:
        """取得已連線客戶端數量"""
        if self._socket_server:
            return self._socket_server.get_client_count()
        return 0
    
    def get_pending_signals(self) -> List[WhaleTradeSignal]:
        """取得等待中的信號"""
        with self.signal_lock:
            return [s for s in self.signals.values() if s.status == "PENDING"]
    
    def get_recent_signals(self, limit: int = 10) -> List[WhaleTradeSignal]:
        """取得最近的信號"""
        with self.signal_lock:
            return sorted(
                self.signals.values(),
                key=lambda s: s.timestamp,
                reverse=True
            )[:limit]
    
    def stop(self):
        """停止橋接器"""
        if self._socket_server:
            self._socket_server.stop()
        logger.info("🛑 WhaleSignalBridge 已停止")


# ==========================================
# 全域實例
# ==========================================
_bridge_instance: Optional[WhaleSignalBridge] = None


def get_bridge(enable_socket: bool = True) -> WhaleSignalBridge:
    """取得橋接器單例"""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = WhaleSignalBridge(enable_socket=enable_socket)
    return _bridge_instance


def start_server() -> WhaleSocketServer:
    """啟動獨立的 Socket Server"""
    server = WhaleSocketServer()
    server.start_in_thread()
    return server


def get_server() -> Optional[WhaleSocketServer]:
    """取得已啟動的 Server"""
    bridge = get_bridge(enable_socket=True)
    return bridge._socket_server


# ==========================================
# 測試
# ==========================================
if __name__ == "__main__":
    print("=" * 60)
    print("🐋 Whale Signal Bridge 測試")
    print("=" * 60)
    
    # 啟動橋接器
    bridge = get_bridge(enable_socket=True)
    
    import time
    time.sleep(1)  # 等待 Socket Server 啟動
    
    print(f"\n📡 已連線客戶端: {bridge.get_client_count()}")
    
    # 發送測試信號
    print("\n📤 發送測試信號...")
    signal_id = bridge.send_signal(
        action="OPEN_LONG",
        symbol="BTCUSDT",
        entry_price=95000.0,
        quantity_usdt=100.0,
        quantity_btc=0.00105,
        leverage=100,
        stop_loss=94000.0,
        take_profit=96000.0,
        strategy_name="派發出貨",
        strategy_confidence=0.75
    )
    
    if signal_id:
        print(f"✅ 信號已發送: {signal_id}")
    else:
        print("❌ 信號發送失敗")
    
    print("\n⏳ 等待 30 秒（測試客戶端連線）...")
    print("   使用 Ctrl+C 結束")
    
    try:
        time.sleep(30)
    except KeyboardInterrupt:
        pass
    
    bridge.stop()
    print("\n✅ 測試完成")
