#!/usr/bin/env python3
"""
🔌 Testnet WebSocket 監控器
即時接收 Binance Testnet 的持倉變化、訂單更新、價格波動

特點:
1. 即時推播持倉盈虧變化 (每秒更新 ROI%)
2. 偵測手動平倉事件
3. 偵測爆倉事件
4. 回調通知系統
"""

import os
import sys
import json
import time
import hmac
import hashlib
import asyncio
import websockets
import requests
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Dict, Any

# ==================== 配置 ====================

TESTNET_REST_URL = "https://testnet.binancefuture.com"
TESTNET_WS_URL = "wss://stream.binancefuture.com"

# Listen Key 有效期 60 分鐘，需要定期續約
LISTEN_KEY_REFRESH_INTERVAL = 30 * 60  # 30 分鐘續約一次

class TestnetWebSocketMonitor:
    """
    Binance Testnet WebSocket 監控器
    
    使用多個 Stream 即時接收:
    - User Data Stream: 帳戶更新、訂單更新
    - Mark Price Stream: 即時價格 (計算盈虧)
    """
    
    def __init__(self):
        self.api_key = ""
        self.api_secret = ""
        self.listen_key = ""
        self.ws = None
        self.running = False
        
        # 持倉狀態 (用於計算即時盈虧)
        self.positions: Dict[str, Dict] = {}  # symbol -> position info
        self.current_price = 0.0
        self.last_pnl_display = 0  # 上次顯示盈虧的時間
        
        # 回調函數
        self.on_position_update: Optional[Callable] = None  # 持倉更新
        self.on_order_update: Optional[Callable] = None     # 訂單更新
        self.on_account_update: Optional[Callable] = None   # 帳戶更新
        self.on_liquidation: Optional[Callable] = None      # 爆倉事件
        self.on_manual_close: Optional[Callable] = None     # 手動平倉
        self.on_pnl_update: Optional[Callable] = None       # 盈虧更新
        
        self._load_api_keys()
        self._load_positions()  # 載入現有持倉
    
    def _load_api_keys(self):
        """從 .env 讀取 API 金鑰"""
        env_path = Path(__file__).parent.parent / '.env'
        
        if env_path.exists():
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        if key.strip() == 'BINANCE_TESTNET_API_KEY':
                            self.api_key = value.strip()
                        elif key.strip() == 'BINANCE_TESTNET_API_SECRET':
                            self.api_secret = value.strip()
        
        if not self.api_key or not self.api_secret:
            raise ValueError("❌ 缺少 BINANCE_TESTNET_API_KEY 或 BINANCE_TESTNET_API_SECRET")
    
    def _load_positions(self):
        """從交易所載入現有持倉"""
        try:
            timestamp = int(time.time() * 1000)
            params = {'timestamp': timestamp}
            query = '&'.join([f'{k}={v}' for k, v in params.items()])
            signature = hmac.new(
                self.api_secret.encode(), 
                query.encode(), 
                hashlib.sha256
            ).hexdigest()
            
            url = f"{TESTNET_REST_URL}/fapi/v2/positionRisk?{query}&signature={signature}"
            response = requests.get(url, headers={'X-MBX-APIKEY': self.api_key})
            
            if response.status_code == 200:
                for pos in response.json():
                    if pos['symbol'] == 'BTCUSDT' and float(pos['positionAmt']) != 0:
                        position_side = pos['positionSide']  # LONG / SHORT / BOTH
                        self.positions[position_side] = {
                            'symbol': pos['symbol'],
                            'position_amt': float(pos['positionAmt']),
                            'entry_price': float(pos['entryPrice']),
                            'leverage': int(pos['leverage']),
                            'unrealized_pnl': float(pos['unRealizedProfit']),
                            'position_side': position_side
                        }
                        print(f"   📊 載入持倉: {position_side} {abs(float(pos['positionAmt']))} BTC @ ${float(pos['entryPrice']):,.2f}")
        except Exception as e:
            print(f"   ⚠️ 載入持倉失敗: {e}")
    
    def _get_headers(self) -> Dict:
        """取得請求標頭"""
        return {'X-MBX-APIKEY': self.api_key}
    
    def _create_listen_key(self) -> str:
        """建立 Listen Key"""
        url = f"{TESTNET_REST_URL}/fapi/v1/listenKey"
        response = requests.post(url, headers=self._get_headers())
        
        if response.status_code == 200:
            return response.json().get('listenKey', '')
        else:
            raise Exception(f"Failed to create listen key: {response.text}")
    
    def _renew_listen_key(self):
        """續約 Listen Key"""
        if not self.listen_key:
            return
        
        url = f"{TESTNET_REST_URL}/fapi/v1/listenKey"
        response = requests.put(url, headers=self._get_headers())
        
        if response.status_code == 200:
            print(f"   🔄 Listen Key 已續約")
        else:
            print(f"   ⚠️ Listen Key 續約失敗: {response.text}")
            self.listen_key = self._create_listen_key()
    
    def _calculate_pnl(self, position: Dict, current_price: float) -> tuple:
        """
        計算持倉盈虧 - 與 Binance 一致的計算方式
        
        ROI = 未實現盈虧 / 保證金 × 100%
        PnL = (當前價 - 入場價) × 持倉數量
        """
        entry_price = position.get('entry_price', 0)
        position_amt = position.get('position_amt', 0)
        leverage = position.get('leverage', 1)
        
        if entry_price == 0 or position_amt == 0:
            return 0, 0
        
        # 計算未實現盈虧 (PnL USDT)
        if position_amt > 0:  # LONG
            pnl_usdt = (current_price - entry_price) * abs(position_amt)
        else:  # SHORT
            pnl_usdt = (entry_price - current_price) * abs(position_amt)
        
        # 計算保證金 (Margin)
        notional_value = abs(position_amt) * entry_price  # 名義價值
        margin = notional_value / leverage  # 保證金
        
        # ROI = PnL / Margin × 100% (與 Binance 一致)
        pnl_pct = (pnl_usdt / margin) * 100 if margin > 0 else 0
        
        return pnl_usdt, pnl_pct
    
    async def _handle_message(self, message: str):
        """處理 WebSocket 訊息"""
        try:
            data = json.loads(message)
            
            # 處理組合 stream 的訊息格式
            if 'stream' in data:
                stream_name = data['stream']
                payload = data['data']
                
                if 'markPrice' in stream_name:
                    await self._handle_mark_price(payload)
                elif stream_name == self.listen_key:
                    event_type = payload.get('e')
                    if event_type == 'ACCOUNT_UPDATE':
                        await self._handle_account_update(payload)
                    elif event_type == 'ORDER_TRADE_UPDATE':
                        await self._handle_order_update(payload)
            else:
                # 單一 stream 格式
                event_type = data.get('e')
                if event_type == 'markPriceUpdate':
                    await self._handle_mark_price(data)
                elif event_type == 'ACCOUNT_UPDATE':
                    await self._handle_account_update(data)
                elif event_type == 'ORDER_TRADE_UPDATE':
                    await self._handle_order_update(data)
                elif event_type == 'listenKeyExpired':
                    print("   ⚠️ Listen Key 已過期，重新連接...")
                    self.listen_key = self._create_listen_key()
                    
        except Exception as e:
            print(f"   ⚠️ WebSocket 訊息處理錯誤: {e}")
    
    async def _handle_mark_price(self, data: Dict):
        """處理 Mark Price 更新 - 計算即時盈虧"""
        symbol = data.get('s', '')
        if symbol != 'BTCUSDT':
            return
        
        self.current_price = float(data.get('p', 0))
        
        # 每秒只顯示一次盈虧
        current_time = time.time()
        if current_time - self.last_pnl_display < 1:
            return
        self.last_pnl_display = current_time
        
        # 計算並顯示所有持倉的盈虧
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        for side, position in self.positions.items():
            if position.get('position_amt', 0) == 0:
                continue
            
            pnl_usdt, pnl_pct = self._calculate_pnl(position, self.current_price)
            leverage = position.get('leverage', 1)
            
            # 顏色指示
            if pnl_pct >= 0:
                emoji = "🟢" if pnl_pct >= 3 else "📈"
                color_indicator = f"+{pnl_pct:.2f}%"
            else:
                emoji = "🔴" if pnl_pct <= -3 else "📉"
                color_indicator = f"{pnl_pct:.2f}%"
            
            direction = "LONG" if position['position_amt'] > 0 else "SHORT"
            entry = position.get('entry_price', 0)
            
            # 與 Binance 一致的顯示格式
            print(f"\r{emoji} [{timestamp}] {direction} {leverage}x | ${self.current_price:,.1f} | ROI: {color_indicator} | PnL: ${pnl_usdt:+.2f}    ", end='', flush=True)
            
            # 回調
            if self.on_pnl_update:
                await self._safe_callback(self.on_pnl_update, {
                    'symbol': symbol,
                    'position_side': side,
                    'direction': direction,
                    'current_price': self.current_price,
                    'entry_price': entry,
                    'pnl_usdt': pnl_usdt,
                    'pnl_pct': pnl_pct,
                    'position_amt': position.get('position_amt', 0),  # 🆕 加入持倉數量
                    'leverage': leverage  # 🆕 加入槓桿
                })
    
    async def _handle_account_update(self, data: Dict):
        """處理帳戶更新事件"""
        event_reason = data.get('a', {}).get('m', 'UNKNOWN')
        positions = data.get('a', {}).get('P', [])
        balances = data.get('a', {}).get('B', [])
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        for pos in positions:
            symbol = pos.get('s', '')
            if symbol != 'BTCUSDT':
                continue
            
            position_amt = float(pos.get('pa', 0))
            entry_price = float(pos.get('ep', 0))
            unrealized_pnl = float(pos.get('up', 0))
            position_side = pos.get('ps', '')
            
            # 更新本地持倉狀態
            if position_amt != 0:
                self.positions[position_side] = {
                    'symbol': symbol,
                    'position_amt': position_amt,
                    'entry_price': entry_price,
                    'leverage': self.positions.get(position_side, {}).get('leverage', 10),
                    'unrealized_pnl': unrealized_pnl,
                    'position_side': position_side
                }
            else:
                # 持倉被清空
                if position_side in self.positions:
                    del self.positions[position_side]
                
                close_reason = {
                    'ORDER': '訂單成交（手動/系統平倉）',
                    'FUNDING_FEE': '資金費率',
                    'LIQUIDATION': '💀 爆倉',
                    'ADL': '自動減倉'
                }.get(event_reason, f'未知 ({event_reason})')
                
                print(f"\n\n{'🚨'*20}")
                print(f"📡 [{timestamp}] TESTNET 持倉已平倉！")
                print(f"   方向: {position_side}")
                print(f"   原因: {close_reason}")
                print(f"{'🚨'*20}\n")
                
                if event_reason == 'LIQUIDATION' and self.on_liquidation:
                    await self._safe_callback(self.on_liquidation, {
                        'symbol': symbol, 'position_side': position_side, 'reason': event_reason
                    })
                elif self.on_manual_close:
                    await self._safe_callback(self.on_manual_close, {
                        'symbol': symbol, 'position_side': position_side, 'reason': event_reason
                    })
        
        if self.on_account_update:
            for balance in balances:
                if balance.get('a') == 'USDT':
                    await self._safe_callback(self.on_account_update, {
                        'asset': 'USDT',
                        'balance': float(balance.get('wb', 0)),
                        'cross_wallet': float(balance.get('cw', 0))
                    })
    
    async def _handle_order_update(self, data: Dict):
        """處理訂單更新事件"""
        order = data.get('o', {})
        
        symbol = order.get('s', '')
        if symbol != 'BTCUSDT':
            return
        
        order_status = order.get('X', '')
        order_type = order.get('o', '')
        side = order.get('S', '')
        position_side = order.get('ps', '')
        avg_price = float(order.get('ap', 0))
        quantity = float(order.get('q', 0))
        realized_pnl = float(order.get('rp', 0))
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if order_status == 'FILLED':
            emoji = "✅" if realized_pnl >= 0 else "🔴"
            print(f"\n\n{emoji} [{timestamp}] 訂單成交!")
            print(f"   類型: {order_type} {side} {position_side}")
            print(f"   數量: {quantity} BTC @ ${avg_price:,.2f}")
            if realized_pnl != 0:
                print(f"   已實現盈虧: ${realized_pnl:+.2f}\n")
            
            # 更新本地持倉的槓桿
            if position_side in self.positions:
                # 嘗試從 API 獲取正確的槓桿
                self._load_positions()
                
        elif order_status == 'LIQUIDATION':
            print(f"\n\n💀 [{timestamp}] 爆倉!")
            print(f"   方向: {position_side}")
            print(f"   價格: ${avg_price:,.2f}\n")
            
            if self.on_liquidation:
                await self._safe_callback(self.on_liquidation, {
                    'symbol': symbol, 'position_side': position_side,
                    'price': avg_price, 'quantity': quantity
                })
        
        if self.on_order_update:
            await self._safe_callback(self.on_order_update, {
                'symbol': symbol, 'status': order_status, 'type': order_type,
                'side': side, 'position_side': position_side,
                'price': avg_price, 'quantity': quantity, 'realized_pnl': realized_pnl
            })
    
    async def _safe_callback(self, callback: Callable, data: Dict):
        """安全執行回調"""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            print(f"   ⚠️ 回調執行錯誤: {e}")
    
    async def _keep_alive_loop(self):
        """定期續約 Listen Key"""
        while self.running:
            await asyncio.sleep(LISTEN_KEY_REFRESH_INTERVAL)
            if self.running:
                self._renew_listen_key()
    
    async def connect(self):
        """連接 WebSocket (組合 stream: User Data + Mark Price)"""
        print("\n" + "=" * 60)
        print("🔌 正在連接 Testnet WebSocket...")
        print("=" * 60)
        
        try:
            # 建立 Listen Key
            self.listen_key = self._create_listen_key()
            print(f"   ✅ Listen Key 已建立")
            
            # 組合多個 stream
            # - 用戶數據流: 帳戶更新、訂單更新
            # - Mark Price: 即時價格 (每秒)
            streams = [
                self.listen_key,                    # User Data Stream
                "btcusdt@markPrice@1s"              # Mark Price (每秒)
            ]
            
            ws_url = f"{TESTNET_WS_URL}/stream?streams={'/'.join(streams)}"
            
            self.running = True
            
            # 啟動 Keep Alive 任務
            keep_alive_task = asyncio.create_task(self._keep_alive_loop())
            
            async with websockets.connect(ws_url) as ws:
                self.ws = ws
                print(f"   ✅ WebSocket 已連接")
                print(f"   📡 Streams: User Data + Mark Price (每秒)")
                print(f"   💡 即時顯示 ROI% 和 PnL...")
                print("=" * 60 + "\n")
                
                while self.running:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        await self._handle_message(message)
                    except asyncio.TimeoutError:
                        await ws.ping()
                    except websockets.exceptions.ConnectionClosed:
                        print("\n   ⚠️ WebSocket 連接已關閉，重新連接...")
                        break
            
            keep_alive_task.cancel()
            
        except Exception as e:
            print(f"   ❌ WebSocket 連接錯誤: {e}")
            self.running = False
    
    def stop(self):
        """停止監控"""
        self.running = False
        print("\n   🛑 WebSocket 監控已停止")


# ==================== 獨立測試 ====================

async def test_monitor():
    """測試 WebSocket 監控"""
    monitor = TestnetWebSocketMonitor()
    await monitor.connect()


if __name__ == "__main__":
    print("🔌 Testnet WebSocket 監控器 (即時 ROI%)")
    print("按 Ctrl+C 停止\n")
    
    try:
        asyncio.run(test_monitor())
    except KeyboardInterrupt:
        print("\n\n👋 已停止")
