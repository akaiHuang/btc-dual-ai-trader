#!/usr/bin/env python3
"""
dYdX TP/SL 測試腳本 v3
========================
根據用戶策略實現：
1. 開倉: 限價 ±$8 掛單
2. 止盈: N%N 動態鎖盈
3. 止損: -0.5% 固定止損，0.5秒監控

使用方式:
    .venv/bin/python scripts/test_dydx_tpsl.py
"""

import os
import sys
import json
import asyncio
import aiohttp
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Callable, List
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dydx_whale_trader import DydxAPI, DydxConfig
    DYDX_AVAILABLE = True
except ImportError as e:
    print(f"❌ 無法載入 dYdX 模組: {e}")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TradingStrategy:
    """交易策略配置"""
    # 開倉
    entry_offset: float = 8.0          # 開倉價格偏移 $8
    entry_timeout: float = 5.0           # 🔧 5秒沒成交就取消 (方向錯了寧願錯過)
    
    # 止損
    stop_loss_pct: float = 2.0         # 🔧 止損 -2.0% (因應高波動，放寬止損)
    
    # 止盈 (N%N 鎖盈)
    # 當利潤達到 N% 時，將止損移動到 entry + (N-0.5)%
    tp_levels: List[float] = field(default_factory=lambda: [0.5, 1.0, 1.5, 2.0, 3.0, 5.0])
    
    # 監控
    monitor_interval: float = 0.5      # 監控間隔 0.5 秒
    leverage: int = 50                 # 槓桿


# ═══════════════════════════════════════════════════════════════════════════════
# dYdX WebSocket 價格監控器
# ═══════════════════════════════════════════════════════════════════════════════

class DydxPriceMonitor:
    """dYdX 實時價格監控器 (WebSocket)"""
    
    def __init__(self, api: DydxAPI, symbol: str = "BTC-USD"):
        self.api = api
        self.symbol = symbol
        self.ws_url = "wss://indexer.dydx.trade/v4/ws"
        self.ws = None
        self.session = None
        self.current_price = 0.0
        self.running = False
        self._listen_task = None
        
    async def start(self):
        """連接 WebSocket 並開始監控"""
        print(f"🔌 連接 dYdX WebSocket...")
        
        try:
            # 先取得初始價格
            self.current_price = await self.api.get_price()
            
            self.session = aiohttp.ClientSession()
            self.ws = await self.session.ws_connect(self.ws_url)
            
            # 訂閱交易頻道
            subscribe_msg = {
                "type": "subscribe",
                "channel": "v4_trades",
                "id": self.symbol
            }
            await self.ws.send_json(subscribe_msg)
            
            self.running = True
            print(f"✅ WebSocket 連接成功! 初始價格: ${self.current_price:,.2f}")
            
            # 啟動背景監聽
            self._listen_task = asyncio.create_task(self._listen_loop())
            
        except Exception as e:
            print(f"❌ WebSocket 連接失敗: {e}")
            self.running = False
    
    async def _listen_loop(self):
        """背景監聽 WebSocket 消息"""
        try:
            async for msg in self.ws:
                if not self.running:
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    self._handle_message(data)
        except Exception as e:
            if self.running:
                print(f"\n⚠️ WebSocket 監聽錯誤: {e}")
    
    def _handle_message(self, data: dict):
        """處理 WebSocket 消息"""
        if data.get("type") == "channel_data":
            contents = data.get("contents", {})
            trades = contents.get("trades", [])
            if trades:
                price = float(trades[0].get("price", 0))
                if price > 0:
                    self.current_price = price
        
    async def get_price(self) -> float:
        """獲取當前價格 (從 WebSocket 緩存)"""
        return self.current_price
    
    async def stop(self):
        """停止監控"""
        self.running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()
        print("🔌 WebSocket 已關閉")


# ═══════════════════════════════════════════════════════════════════════════════
# 持倉管理器
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    """持倉狀態"""
    side: str              # "LONG" or "SHORT"
    size: float            # BTC 數量
    entry_price: float     # 進場價格
    stop_loss_price: float # 止損價格
    current_tp_level: int  # 當前止盈等級 (用於 N%N)
    entry_time: datetime = None
    
    def __post_init__(self):
        self.entry_time = datetime.now()
    
    def pnl_pct(self, current_price: float, leverage: int = 50) -> float:
        """計算盈虧百分比 (含槓桿)"""
        if self.side == "LONG":
            return ((current_price - self.entry_price) / self.entry_price) * 100 * leverage
        else:
            return ((self.entry_price - current_price) / self.entry_price) * 100 * leverage


class PositionManager:
    """持倉管理器 - 實現 N%N 止盈策略"""
    
    def __init__(self, api: DydxAPI, strategy: TradingStrategy):
        self.api = api
        self.strategy = strategy
        self.position: Optional[Position] = None
        self.monitor = DydxPriceMonitor(api)
        self.closed = False
        
    async def open_position(self, side: str, size: float) -> bool:
        """
        開倉 - 使用主程式的 place_fast_order
        
        🔗 對齊主程式邏輯 (Aggressive Maker + IOC fallback)
        """
        print(f"\n📤 開倉 (使用 place_fast_order):")
        print(f"   方向: {side}")
        print(f"   數量: {size:.4f} BTC")
        print(f"   超時: {self.strategy.entry_timeout}秒")
        
        # � 直接使用主程式的 place_fast_order
        tx_hash, fill_price = await self.api.place_fast_order(
            side=side,
            size=size,
            maker_timeout=self.strategy.entry_timeout,
            fallback_to_ioc=True
        )
        
        if not tx_hash or fill_price <= 0:
            print("❌ 開倉失敗或超時")
            return False
        
        # 計算初始止損價格
        sl_price_change = self.strategy.stop_loss_pct / self.strategy.leverage / 100
        if side == "LONG":
            sl_price = fill_price * (1 - sl_price_change)
        else:
            sl_price = fill_price * (1 + sl_price_change)
        
        self.position = Position(
            side=side,
            size=size,
            entry_price=fill_price,
            stop_loss_price=sl_price,
            current_tp_level=0
        )
        
        print(f"\n✅ 開倉成功!")
        print(f"   成交價: ${fill_price:,.2f}")
        print(f"   止損價: ${sl_price:,.2f} (-{self.strategy.stop_loss_pct}%)")
        
        return True
    
    async def _place_limit_order(self, side: str, size: float, price: float) -> tuple:
        """下限價單並等待成交"""
        from dydx_v4_client.node.market import Market
        from dydx_v4_client.indexer.rest.constants import OrderType
        from dydx_v4_client.node.builder import TxOptions
        from dydx_v4_client import MAX_CLIENT_ID, OrderFlags
        from v4_proto.dydxprotocol.clob.order_pb2 import Order
        import random
        
        try:
            market_data = await self.api.indexer.markets.get_perpetual_markets(self.api.config.symbol)
            market_info = market_data.get("markets", {}).get(self.api.config.symbol, {})
            market = Market(market_info)
            
            client_id = random.randint(0, MAX_CLIENT_ID)
            
            # 使用 LONG_TERM 訂單 (GTT)
            order_id = market.order_id(
                self.api.address,
                self.api.subaccount,
                client_id,
                OrderFlags.LONG_TERM
            )
            
            good_til_timestamp = int(datetime.now().timestamp()) + int(self.strategy.entry_timeout)
            
            order_side = Order.Side.SIDE_BUY if side == "LONG" else Order.Side.SIDE_SELL
            
            new_order = market.order(
                order_id=order_id,
                order_type=OrderType.LIMIT,
                side=order_side,
                size=size,
                price=price,
                time_in_force=Order.TimeInForce.TIME_IN_FORCE_UNSPECIFIED,
                reduce_only=False,
                good_til_block=0,
                good_til_block_time=good_til_timestamp,
            )
            
            print(f"   📝 提交限價單 @ ${price:,.2f}...")
            
            if self.api.authenticator_id > 0:
                tx_options = TxOptions(
                    authenticators=[self.api.authenticator_id],
                    sequence=self.api.wallet.sequence,
                    account_number=self.api.wallet.account_number,
                )
                tx = await self.api.node.place_order(
                    wallet=self.api.wallet,
                    order=new_order,
                    tx_options=tx_options,
                )
            else:
                tx = await self.api.node.place_order(
                    wallet=self.api.wallet,
                    order=new_order,
                )
            
            self.api.wallet.sequence += 1
            
            # 等待成交
            start_time = asyncio.get_event_loop().time()
            while (asyncio.get_event_loop().time() - start_time) < self.strategy.entry_timeout:
                positions = await self.api.get_positions()
                for pos in positions:
                    if pos.get("market") == self.api.config.symbol and pos.get("status") == "OPEN":
                        pos_size = abs(float(pos.get("size", 0)))
                        if pos_size >= size * 0.99:
                            return str(tx), float(pos.get("entryPrice", 0))
                
                remaining = self.strategy.entry_timeout - (asyncio.get_event_loop().time() - start_time)
                print(f"\r   ⏳ 等待成交... {remaining:.1f}秒", end="", flush=True)
                await asyncio.sleep(0.5)
            
            print()
            return None, 0.0
            
        except Exception as e:
            print(f"❌ 下單失敗: {e}")
            import traceback
            traceback.print_exc()
            return None, 0.0
    
    async def monitor_position(self):
        """
        監控持倉 - 每 0.5 秒檢查
        
        實現 N%N 止盈:
        - 利潤達 0.5% → 止損移到 entry (保本)
        - 利潤達 1.0% → 止損移到 +0.5%
        - 利潤達 1.5% → 止損移到 +1.0%
        - 以此類推...
        """
        if not self.position:
            return
        
        await self.monitor.start()
        
        print(f"\n👂 開始監控 (每 {self.strategy.monitor_interval}秒)...")
        print(f"   N%N 鎖盈: {self.strategy.tp_levels}")
        
        try:
            while not self.closed and self.position:
                current_price = await self.monitor.get_price()
                pnl = self.position.pnl_pct(current_price, self.strategy.leverage)
                
                # 檢查止損
                if await self._check_stop_loss(current_price, pnl):
                    break
                
                # 檢查 N%N 止盈 (調整止損)
                await self._check_tp_levels(current_price, pnl)
                
                # 顯示狀態
                sl_pct = ((self.position.stop_loss_price - self.position.entry_price) / self.position.entry_price) * 100 * self.strategy.leverage
                print(f"\r💲 ${current_price:,.2f} | 盈虧: {pnl:+.2f}% | SL: ${self.position.stop_loss_price:,.2f} ({sl_pct:+.2f}%)  ", end="", flush=True)
                
                await asyncio.sleep(self.strategy.monitor_interval)
                
        except asyncio.CancelledError:
            print("\n⏹️ 監控被取消")
        finally:
            await self.monitor.stop()
    
    async def _check_stop_loss(self, price: float, pnl: float) -> bool:
        """檢查是否觸發止損"""
        if not self.position:
            return False
        
        triggered = False
        if self.position.side == "LONG" and price <= self.position.stop_loss_price:
            triggered = True
        elif self.position.side == "SHORT" and price >= self.position.stop_loss_price:
            triggered = True
        
        if triggered:
            sl_pnl = self.position.pnl_pct(self.position.stop_loss_price, self.strategy.leverage)
            print(f"\n\n🛑 {'止損' if sl_pnl < 0 else '鎖盈'}觸發!")
            print(f"   當前價: ${price:,.2f}")
            print(f"   止損價: ${self.position.stop_loss_price:,.2f}")
            print(f"   預期盈虧: {sl_pnl:+.2f}%")
            
            await self._close_position()
            return True
        
        return False
    
    async def _check_tp_levels(self, price: float, pnl: float):
        """檢查 N%N 止盈等級，調整止損"""
        if not self.position:
            return
        
        # 找出當前達到的最高等級
        new_level = self.position.current_tp_level
        for i, level in enumerate(self.strategy.tp_levels):
            if pnl >= level:
                new_level = i + 1
        
        # 如果升級，調整止損
        if new_level > self.position.current_tp_level:
            old_level = self.position.current_tp_level
            self.position.current_tp_level = new_level
            
            # 計算新止損價格
            # 達到 N% 時，止損設為 (N - 0.5)%
            lock_pct = self.strategy.tp_levels[new_level - 1] - 0.5
            if lock_pct < 0:
                lock_pct = 0  # 至少保本
            
            price_change = lock_pct / self.strategy.leverage / 100
            if self.position.side == "LONG":
                new_sl = self.position.entry_price * (1 + price_change)
            else:
                new_sl = self.position.entry_price * (1 - price_change)
            
            old_sl = self.position.stop_loss_price
            self.position.stop_loss_price = new_sl
            
            print(f"\n\n📈 鎖盈升級! 等級 {old_level} → {new_level}")
            print(f"   利潤: {pnl:+.2f}%")
            print(f"   止損移動: ${old_sl:,.2f} → ${new_sl:,.2f} (鎖 {lock_pct:+.1f}%)\n")
    
    async def _close_position(self):
        """平倉 - 直接 IOC，不嘗試 Maker"""
        if not self.position:
            return
        
        self.closed = True
        
        print(f"📤 發送平倉單 (直接 IOC)...")
        
        try:
            # 🔧 直接用 IOC，不嘗試 Maker
            tx, fill_price = await self.api._close_ioc_order(
                side=self.position.side,
                size=self.position.size
            )
            
            if tx and fill_price > 0:
                actual_pnl = self.position.pnl_pct(fill_price, self.strategy.leverage)
                print(f"✅ 平倉成功!")
                print(f"   成交價: ${fill_price:,.2f}")
                print(f"   實際盈虧: {actual_pnl:+.2f}%")
            else:
                print("⚠️ IOC 未成交，嘗試強制市價...")
                # 備用：使用 close_fast_order
                tx2, fill_price2 = await self.api.close_fast_order(
                    side=self.position.side,
                    size=self.position.size,
                    maker_timeout=0.5,
                    fallback_to_ioc=True
                )
                if tx2 and fill_price2 > 0:
                    actual_pnl = self.position.pnl_pct(fill_price2, self.strategy.leverage)
                    print(f"✅ 備用平倉成功! 價格: ${fill_price2:,.2f} | 盈虧: {actual_pnl:+.2f}%")
                
        except Exception as e:
            print(f"❌ 平倉錯誤: {e}")
        
        self.position = None
    
    async def force_close(self):
        """強制平倉"""
        if self.position and not self.closed:
            print("\n⚠️ 強制平倉...")
            await self._close_position()


# ═══════════════════════════════════════════════════════════════════════════════
# 測試流程
# ═══════════════════════════════════════════════════════════════════════════════

async def analyze_direction(api: DydxAPI) -> str:
    """快速分析市場方向 (基於 Order Book Imbalance)"""
    try:
        best_bid, best_ask = await api.get_best_bid_ask()
        
        # 簡單分析：比較 bid/ask 的距離
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        
        # 如果 spread 很小，市場平衡，用價格趨勢
        price = await api.get_price()
        
        # 簡單規則：如果當前價格 > mid_price，做空；反之做多
        if price > mid_price:
            return "SHORT"
        else:
            return "LONG"
    except:
        return "LONG"  # 預設做多


async def test_strategy_auto():
    """自動化測試 - 無需手動確認"""
    print("\n" + "="*60)
    print("🤖 dYdX 自動化測試 v4")
    print("="*60)
    
    strategy = TradingStrategy()
    print(f"\n📋 策略配置:")
    print(f"   開倉偏移: ${strategy.entry_offset}")
    print(f"   止損: -{strategy.stop_loss_pct}%")
    print(f"   N%N 等級: {strategy.tp_levels}")
    
    # 初始化
    config = DydxConfig(network="mainnet", paper_trading=False)
    api = DydxAPI(config)
    
    if not await api.connect():
        print("❌ API 連接失敗")
        return
    
    balance = await api.get_account_balance()
    price = await api.get_price()
    print(f"\n💰 帳戶餘額: ${balance:.2f}")
    print(f"📊 當前價格: ${price:,.2f}")
    
    if balance < 5:
        print("❌ 餘額不足")
        return
    
    # 🔧 自動分析方向
    print("\n🔍 分析市場方向...")
    side = await analyze_direction(api)
    print(f"   建議方向: {side}")
    
    size = 0.001
    order_price = price + strategy.entry_offset if side == "LONG" else price - strategy.entry_offset
    
    print(f"\n🚀 自動開倉: {side} {size} BTC @ ${order_price:,.2f}")
    print("   (無需確認，立即執行)")
    
    # 🔧 直接執行，無需確認
    manager = PositionManager(api, strategy)
    
    try:
        if await manager.open_position(side, size):
            await manager.monitor_position()
    except KeyboardInterrupt:
        print("\n\n⏹️ 手動停止")
    finally:
        await manager.force_close()
    
    print("\n" + "="*60)
    print("✅ 測試結束")
    print("="*60)


async def test_strategy():
    """測試完整交易策略 (手動選擇)"""
    print("\n" + "="*60)
    print("🧪 dYdX 交易策略測試 v3")
    print("="*60)
    
    strategy = TradingStrategy()
    print(f"\n📋 策略配置:")
    print(f"   開倉偏移: ${strategy.entry_offset}")
    print(f"   開倉超時: {strategy.entry_timeout}秒")
    print(f"   止損: -{strategy.stop_loss_pct}%")
    print(f"   N%N 等級: {strategy.tp_levels}")
    print(f"   監控間隔: {strategy.monitor_interval}秒")
    
    # 初始化
    config = DydxConfig(network="mainnet", paper_trading=False)
    api = DydxAPI(config)
    
    if not await api.connect():
        print("❌ API 連接失敗")
        return
    
    balance = await api.get_account_balance()
    price = await api.get_price()
    print(f"\n💰 帳戶餘額: ${balance:.2f}")
    print(f"📊 當前價格: ${price:,.2f}")
    
    if balance < 5:
        print("❌ 餘額不足")
        return
    
    # 選擇方向
    print("\n選擇方向:")
    print("   1. LONG (做多)")
    print("   2. SHORT (做空)")
    print("   0. 取消")
    
    try:
        choice = input("\n請選擇 [0-2]: ").strip()
    except EOFError:
        choice = "0"
    
    if choice == "1":
        side = "LONG"
    elif choice == "2":
        side = "SHORT"
    else:
        print("❌ 取消")
        return
    
    size = 0.001
    print(f"\n⚠️ 即將開倉: {side} {size} BTC")
    print(f"   掛單價: ${price + strategy.entry_offset if side=='LONG' else price - strategy.entry_offset:,.2f}")
    
    try:
        confirm = input("\n確認? [y/N]: ").strip().lower()
    except EOFError:
        confirm = "n"
    
    if confirm != "y":
        print("❌ 取消")
        return
    
    # 執行交易
    manager = PositionManager(api, strategy)
    
    try:
        if await manager.open_position(side, size):
            await manager.monitor_position()
    except KeyboardInterrupt:
        print("\n\n⏹️ 手動停止")
    finally:
        await manager.force_close()
    
    print("\n" + "="*60)
    print("✅ 測試結束")
    print("="*60)


async def main():
    """主函數"""
    print("\n" + "="*60)
    print("🧪 dYdX TP/SL 測試腳本 v4")
    print("="*60)
    print(f"   時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   策略: ±$8 掛單 + N%N 鎖盈 + 0.5% 止損")
    
    print("\n選項:")
    print("   1. 手動測試 (選擇方向)")
    print("   2. 🤖 自動測試 (分析方向、立即執行)")
    print("   0. 退出")
    
    try:
        choice = input("\n請選擇 [0-2]: ").strip()
    except EOFError:
        choice = "0"
    
    if choice == "1":
        await test_strategy()
    elif choice == "2":
        await test_strategy_auto()
    else:
        print("\n👋 退出...")


if __name__ == "__main__":
    asyncio.run(main())
