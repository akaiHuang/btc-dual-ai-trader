#!/usr/bin/env python3
"""
🤖 Real Binance Executor - 真實幣安合約交易執行器

功能：
1. 監聽 whale_testnet_trader.py 透過 Socket 發送的信號
2. 在幣安正式網執行真實合約交易
3. 使用 Maker 掛單降低手續費 (0.02%)
4. 記錄交易結果並與 Paper Trading 對比

使用方式：
  # 模擬模式（不下單）
  .venv/bin/python scripts/real_binance_executor.py
  
  # 真實交易模式
  .venv/bin/python scripts/real_binance_executor.py --live

⚠️ 警告：--live 模式會執行真實交易，請謹慎使用！

架構：
  whale_testnet_trader.py --paper
      ↓ WhaleSignalBridge (Socket port 9528)
  real_binance_executor.py (本檔案)
      ↓ ccxt / Binance API
  幣安正式網

Author: AI Assistant
Date: 2025-12-02
"""

import os
import sys
import json
import time
import asyncio
import threading
import argparse
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from pathlib import Path

# 載入環境變數
from dotenv import load_dotenv
load_dotenv()

import ccxt

# 導入信號橋接器
try:
    from whale_signal_bridge import (
        WhaleSocketClient,
        WhaleTradeSignal,
        SignalAction,
        SignalStatus,
        SOCKET_HOST,
        SOCKET_PORT
    )
except ImportError:
    from scripts.whale_signal_bridge import (
        WhaleSocketClient,
        WhaleTradeSignal,
        SignalAction,
        SignalStatus,
        SOCKET_HOST,
        SOCKET_PORT
    )

# ==========================================
# 配置
# ==========================================
SYMBOL = "BTC/USDT"
SYMBOL_BINANCE = "BTCUSDT"

# 預設槓桿（會被信號覆蓋）
DEFAULT_LEVERAGE = 50

# 保證金模式
MARGIN_TYPE = "isolated"  # isolated (逐倉) 或 cross (全倉)

# 交易金額模式
# "signal" = 跟隨信號金額
# "fixed" = 固定金額
# "compound" = 複利模式（使用帳戶全部餘額）
# "percent" = 帳戶餘額百分比
TRADE_AMOUNT_MODE = "signal"
FIXED_TRADE_AMOUNT = 100.0  # 固定金額 (USDT)
PERCENT_TRADE_AMOUNT = 95   # 百分比 (%)
COMPOUND_MIN_AMOUNT = 10.0  # 複利最低金額

# 手續費（Maker）
MAKER_FEE_RATE = 0.0002  # 0.02%
TAKER_FEE_RATE = 0.0005  # 0.05%（備用）

# Maker 掛單設定
USE_MAKER_ORDER = True       # 使用 Maker 掛單
MAKER_PRICE_OFFSET = 0.0001  # 價格偏移 0.01%
MAKER_WAIT_SECONDS = 30      # 等待成交秒數
MAKER_FALLBACK_TAKER = True  # 未成交時改用 Taker

# 信號過期時間（秒）
SIGNAL_EXPIRY_SECONDS = 60

# 記錄檔案
TRADE_LOG_FILE = "data/real_binance_trades.json"
COMPARISON_LOG_FILE = "data/paper_real_comparison.json"


@dataclass
class RealTradeRecord:
    """真實交易記錄"""
    order_id: str
    signal_id: str
    timestamp: str
    symbol: str
    action: str
    side: str
    
    # 訂單資訊
    quantity_btc: float
    quantity_usdt: float
    leverage: int
    
    # 價格資訊
    signal_price: float       # 信號價格
    order_price: float        # 下單價格
    filled_price: float       # 成交價格
    
    # 分析
    slippage_pct: float       # 滑點百分比
    latency_ms: int           # 延遲毫秒
    order_type: str           # MAKER / TAKER
    
    # 損益
    fee_usdt: float
    pnl_usdt: float = 0.0
    pnl_pct: float = 0.0
    
    # Paper Trading 對比
    paper_price: float = 0.0
    paper_pnl_usdt: float = 0.0
    
    # 狀態
    status: str = "EXECUTED"


class RealBinanceExecutor:
    """真實幣安交易執行器"""
    
    def __init__(self, dry_run: bool = True, verbose: bool = True):
        """
        Args:
            dry_run: True = 模擬模式（不下單）, False = 真實交易
            verbose: 是否顯示詳細資訊
        """
        self.dry_run = dry_run
        self.verbose = verbose
        self.running = False
        
        # 交易所 API
        self.exchange: Optional[ccxt.binance] = None
        
        # Socket Client
        self.socket_client: Optional[WhaleSocketClient] = None
        
        # 交易記錄
        self.trade_records: List[RealTradeRecord] = []
        self.pending_signals: Dict[str, dict] = {}
        
        # 持倉追蹤
        self.current_position: Optional[dict] = None
        self.open_trade: Optional[dict] = None
        
        # 統計
        self.total_pnl = 0.0
        self.total_fees = 0.0
        self.completed_trades = 0
        self.win_count = 0
        self.loss_count = 0
        
        # 啟動時間
        self.startup_time = datetime.now()
        
        # 初始化
        self._init_exchange()
        self._load_trade_records()
        self._ensure_data_dir()
        
        self._print_startup_info()
    
    def _ensure_data_dir(self):
        """確保資料目錄存在"""
        os.makedirs("data", exist_ok=True)
    
    def _init_exchange(self):
        """初始化交易所 API"""
        api_key = os.getenv('BINANCE_API_KEY')
        api_secret = os.getenv('BINANCE_API_SECRET')
        
        if not api_key or not api_secret:
            if not self.dry_run:
                print("\n❌ 錯誤：未設定 BINANCE_API_KEY 和 BINANCE_API_SECRET")
                print("   請在 .env 檔案中設定：")
                print("   BINANCE_API_KEY=你的API_KEY")
                print("   BINANCE_API_SECRET=你的API_SECRET")
                sys.exit(1)
            else:
                print("⚠️ API 未設定，僅能使用模擬模式")
                return
        
        try:
            self.exchange = ccxt.binance({
                'apiKey': api_key,
                'secret': api_secret,
                'sandbox': False,  # 正式網
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',
                    'adjustForTimeDifference': True
                }
            })
            
            # 測試連線
            balance = self.exchange.fetch_balance()
            total_equity = float(balance.get('total', {}).get('USDT', 0))
            
            print(f"✅ 幣安 API 連線成功")
            print(f"   💰 帳戶餘額: ${total_equity:,.2f} USDT")
            
        except Exception as e:
            print(f"❌ 幣安 API 連線失敗: {e}")
            if not self.dry_run:
                sys.exit(1)
    
    def _print_startup_info(self):
        """顯示啟動資訊"""
        print("\n" + "=" * 70)
        print("🤖 Real Binance Executor - 真實合約交易執行器")
        print("=" * 70)
        print(f"📌 模式: {'🧪 DRY RUN (模擬)' if self.dry_run else '⚡ LIVE (真實交易)'}")
        print(f"📌 交易對: {SYMBOL}")
        print(f"📌 預設槓桿: {DEFAULT_LEVERAGE}x")
        print(f"📌 保證金: {MARGIN_TYPE}")
        print(f"📌 掛單模式: {'Maker (0.02%)' if USE_MAKER_ORDER else 'Taker (0.05%)'}")
        print(f"📌 交易金額: {TRADE_AMOUNT_MODE}")
        print(f"📌 Socket: {SOCKET_HOST}:{SOCKET_PORT}")
        print(f"📌 啟動時間: {self.startup_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)
        
        if not self.dry_run:
            print("\n" + "⚠️ " * 20)
            print("⚠️  警告：這是真實交易模式！")
            print("⚠️  所有信號都會在幣安正式網執行真實下單！")
            print("⚠️ " * 20 + "\n")
    
    def _load_trade_records(self):
        """載入歷史交易記錄"""
        if os.path.exists(TRADE_LOG_FILE):
            try:
                with open(TRADE_LOG_FILE, 'r') as f:
                    data = json.load(f)
                    self.trade_records = [
                        RealTradeRecord(**r) for r in data.get('trades', [])
                    ]
                    self.total_pnl = data.get('total_pnl', 0.0)
                    self.total_fees = data.get('total_fees', 0.0)
                    self.completed_trades = data.get('completed_trades', 0)
                print(f"📂 載入 {len(self.trade_records)} 筆歷史交易")
            except Exception as e:
                print(f"⚠️ 載入交易記錄失敗: {e}")
    
    def _save_trade_records(self):
        """儲存交易記錄"""
        data = {
            'last_updated': datetime.now().isoformat(),
            'total_pnl': self.total_pnl,
            'total_fees': self.total_fees,
            'completed_trades': self.completed_trades,
            'win_count': self.win_count,
            'loss_count': self.loss_count,
            'trades': [asdict(r) for r in self.trade_records[-100:]]  # 只保留最近100筆
        }
        
        with open(TRADE_LOG_FILE, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def _get_account_balance(self) -> dict:
        """取得帳戶餘額"""
        if not self.exchange:
            return {'available': 0, 'total': 0}
        
        try:
            balance = self.exchange.fetch_balance()
            usdt_balance = balance.get('USDT', {})
            return {
                'available': float(usdt_balance.get('free', 0)),
                'total': float(usdt_balance.get('total', 0))
            }
        except Exception as e:
            print(f"❌ 取得餘額失敗: {e}")
            return {'available': 0, 'total': 0}
    
    def _get_current_price(self) -> float:
        """取得當前價格"""
        if not self.exchange:
            return 0
        
        try:
            ticker = self.exchange.fetch_ticker(SYMBOL)
            return float(ticker['last'])
        except Exception as e:
            print(f"❌ 取得價格失敗: {e}")
            return 0
    
    def _check_position(self) -> Optional[dict]:
        """檢查當前持倉"""
        if not self.exchange:
            return None
        
        try:
            positions = self.exchange.fetch_positions([SYMBOL])
            for pos in positions:
                contracts = float(pos.get('contracts', 0))
                if contracts > 0:
                    return {
                        'side': pos['side'],
                        'contracts': contracts,
                        'entry_price': float(pos.get('entryPrice', 0)),
                        'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                        'leverage': pos.get('leverage')
                    }
            return None
        except Exception as e:
            print(f"❌ 檢查持倉失敗: {e}")
            return None
    
    def _set_leverage(self, leverage: int):
        """設定槓桿"""
        if not self.exchange or self.dry_run:
            return
        
        try:
            self.exchange.set_leverage(leverage, SYMBOL)
            print(f"   ⚙️ 槓桿已設定: {leverage}x")
        except Exception as e:
            # 可能已經是該槓桿
            pass
    
    def _set_margin_type(self, margin_type: str = MARGIN_TYPE):
        """設定保證金模式"""
        if not self.exchange or self.dry_run:
            return
        
        try:
            self.exchange.set_margin_mode(margin_type, SYMBOL)
            print(f"   ⚙️ 保證金模式: {margin_type}")
        except Exception as e:
            # 可能已經是該模式
            pass
    
    def _calculate_quantity(self, signal: dict) -> tuple:
        """計算下單數量"""
        signal_usdt = float(signal.get('quantity_usdt', 0))
        signal_btc = float(signal.get('quantity_btc', 0))
        leverage = int(signal.get('leverage', DEFAULT_LEVERAGE))
        
        if TRADE_AMOUNT_MODE == "signal":
            quantity_usdt = signal_usdt
        elif TRADE_AMOUNT_MODE == "fixed":
            quantity_usdt = FIXED_TRADE_AMOUNT
        elif TRADE_AMOUNT_MODE == "percent":
            balance = self._get_account_balance()
            quantity_usdt = balance['available'] * PERCENT_TRADE_AMOUNT / 100
        elif TRADE_AMOUNT_MODE == "compound":
            balance = self._get_account_balance()
            quantity_usdt = balance['available']
            if quantity_usdt < COMPOUND_MIN_AMOUNT:
                print(f"⚠️ 餘額不足: ${quantity_usdt:.2f} < ${COMPOUND_MIN_AMOUNT}")
                return 0, 0
        else:
            quantity_usdt = signal_usdt
        
        # 計算 BTC 數量
        current_price = self._get_current_price()
        if current_price > 0:
            # 名義價值 = USDT × 槓桿
            notional_value = quantity_usdt * leverage
            quantity_btc = notional_value / current_price
            
            # 精度處理（幣安 BTC 精度為 0.001）
            quantity_btc = float(Decimal(str(quantity_btc)).quantize(
                Decimal('0.001'), rounding=ROUND_DOWN
            ))
        else:
            quantity_btc = signal_btc
        
        return quantity_usdt, quantity_btc
    
    def _place_maker_order(
        self, 
        side: str, 
        quantity: float, 
        price: float,
        reduce_only: bool = False
    ) -> Optional[dict]:
        """
        下 Maker 限價單
        
        Args:
            side: 'buy' 或 'sell'
            quantity: BTC 數量
            price: 限價
            reduce_only: 是否為減倉單
        """
        if self.dry_run:
            print(f"   🧪 [DRY RUN] Maker {side.upper()} {quantity:.4f} BTC @ ${price:,.2f}")
            return {
                'id': f"DRY_{datetime.now().strftime('%H%M%S')}",
                'status': 'closed',
                'filled': quantity,
                'average': price,
                'fee': {'cost': quantity * price * MAKER_FEE_RATE}
            }
        
        if not self.exchange:
            return None
        
        try:
            # 計算掛單價格（比市價好一點點）
            if side == 'buy':
                limit_price = price * (1 - MAKER_PRICE_OFFSET)
            else:
                limit_price = price * (1 + MAKER_PRICE_OFFSET)
            
            limit_price = round(limit_price, 2)
            
            params = {'reduceOnly': reduce_only} if reduce_only else {}
            
            print(f"   📝 下單: Maker {side.upper()} {quantity:.4f} BTC @ ${limit_price:,.2f}")
            
            order = self.exchange.create_limit_order(
                SYMBOL, side, quantity, limit_price, params
            )
            
            order_id = order['id']
            print(f"   📋 訂單 ID: {order_id}")
            
            # 等待成交
            start_time = time.time()
            while time.time() - start_time < MAKER_WAIT_SECONDS:
                order_status = self.exchange.fetch_order(order_id, SYMBOL)
                
                if order_status['status'] == 'closed':
                    print(f"   ✅ Maker 成交: {order_status['filled']:.4f} BTC @ ${order_status['average']:,.2f}")
                    return order_status
                
                if order_status['status'] == 'canceled':
                    print(f"   ❌ 訂單已取消")
                    return None
                
                time.sleep(0.5)
            
            # 超時未成交
            if MAKER_FALLBACK_TAKER:
                print(f"   ⏰ Maker 超時，改用 Taker...")
                self.exchange.cancel_order(order_id, SYMBOL)
                return self._place_taker_order(side, quantity, reduce_only)
            else:
                print(f"   ⏰ Maker 超時，取消訂單")
                self.exchange.cancel_order(order_id, SYMBOL)
                return None
                
        except Exception as e:
            print(f"   ❌ 下單失敗: {e}")
            return None
    
    def _place_taker_order(
        self,
        side: str,
        quantity: float,
        reduce_only: bool = False
    ) -> Optional[dict]:
        """下 Taker 市價單"""
        if self.dry_run:
            price = self._get_current_price()
            print(f"   🧪 [DRY RUN] Taker {side.upper()} {quantity:.4f} BTC @ ${price:,.2f}")
            return {
                'id': f"DRY_{datetime.now().strftime('%H%M%S')}",
                'status': 'closed',
                'filled': quantity,
                'average': price,
                'fee': {'cost': quantity * price * TAKER_FEE_RATE}
            }
        
        if not self.exchange:
            return None
        
        try:
            params = {'reduceOnly': reduce_only} if reduce_only else {}
            
            print(f"   📝 下單: Taker {side.upper()} {quantity:.4f} BTC (市價)")
            
            order = self.exchange.create_market_order(
                SYMBOL, side, quantity, params
            )
            
            print(f"   ✅ Taker 成交: {order['filled']:.4f} BTC @ ${order['average']:,.2f}")
            return order
            
        except Exception as e:
            print(f"   ❌ 下單失敗: {e}")
            return None
    
    def _execute_signal(self, signal: dict):
        """執行交易信號"""
        signal_id = signal.get('signal_id', 'unknown')
        action = signal.get('action', '')
        signal_price = float(signal.get('entry_price', 0))
        leverage = int(signal.get('leverage', DEFAULT_LEVERAGE))
        
        print(f"\n{'=' * 60}")
        print(f"📥 收到信號: {action}")
        print(f"   ID: {signal_id}")
        print(f"   價格: ${signal_price:,.2f}")
        print(f"   槓桿: {leverage}x")
        print(f"   策略: {signal.get('strategy_name', 'N/A')}")
        print(f"{'=' * 60}")
        
        # 檢查信號是否過期
        try:
            signal_time = datetime.fromisoformat(signal.get('timestamp', ''))
            age = (datetime.now() - signal_time).total_seconds()
            if age > SIGNAL_EXPIRY_SECONDS:
                print(f"   ⏰ 信號已過期 ({age:.0f}秒)")
                return
        except:
            pass
        
        # 計算數量
        quantity_usdt, quantity_btc = self._calculate_quantity(signal)
        if quantity_btc <= 0:
            print(f"   ⚠️ 數量無效")
            return
        
        print(f"   💰 交易金額: ${quantity_usdt:.2f} USDT")
        print(f"   📊 BTC 數量: {quantity_btc:.4f}")
        
        # 設定槓桿和保證金
        self._set_leverage(leverage)
        self._set_margin_type()
        
        # 取得當前價格
        current_price = self._get_current_price()
        if current_price <= 0:
            print(f"   ❌ 無法取得價格")
            return
        
        # 執行交易
        start_time = time.time()
        order = None
        order_type = "MAKER" if USE_MAKER_ORDER else "TAKER"
        reduce_only = action in ['CLOSE_LONG', 'CLOSE_SHORT']
        
        if action == 'OPEN_LONG':
            side = 'buy'
        elif action == 'OPEN_SHORT':
            side = 'sell'
        elif action == 'CLOSE_LONG':
            side = 'sell'
        elif action == 'CLOSE_SHORT':
            side = 'buy'
        else:
            print(f"   ⚠️ 未知動作: {action}")
            return
        
        if USE_MAKER_ORDER:
            order = self._place_maker_order(side, quantity_btc, current_price, reduce_only)
        else:
            order = self._place_taker_order(side, quantity_btc, reduce_only)
        
        latency_ms = int((time.time() - start_time) * 1000)
        
        if not order:
            print(f"   ❌ 訂單執行失敗")
            return
        
        # 計算滑點
        filled_price = float(order.get('average', current_price))
        slippage = (filled_price - signal_price) / signal_price * 100
        if side == 'sell':
            slippage = -slippage
        
        # 計算手續費
        fee_rate = MAKER_FEE_RATE if order_type == "MAKER" else TAKER_FEE_RATE
        fee_usdt = quantity_btc * filled_price * fee_rate
        
        # 記錄交易
        record = RealTradeRecord(
            order_id=str(order.get('id', 'N/A')),
            signal_id=signal_id,
            timestamp=datetime.now().isoformat(),
            symbol=SYMBOL_BINANCE,
            action=action,
            side=side.upper(),
            quantity_btc=quantity_btc,
            quantity_usdt=quantity_usdt,
            leverage=leverage,
            signal_price=signal_price,
            order_price=current_price,
            filled_price=filled_price,
            slippage_pct=slippage,
            latency_ms=latency_ms,
            order_type=order_type,
            fee_usdt=fee_usdt,
            paper_price=signal_price,
            paper_pnl_usdt=float(signal.get('paper_pnl', 0))
        )
        
        # 追蹤開倉/平倉
        if action in ['OPEN_LONG', 'OPEN_SHORT']:
            self.open_trade = {
                'action': action,
                'price': filled_price,
                'quantity': quantity_btc,
                'fee': fee_usdt,
                'signal_id': signal_id
            }
            self.current_position = {
                'side': 'long' if action == 'OPEN_LONG' else 'short',
                'entry_price': filled_price,
                'quantity': quantity_btc
            }
        
        elif action in ['CLOSE_LONG', 'CLOSE_SHORT'] and self.open_trade:
            # 計算損益
            entry_price = self.open_trade['price']
            
            if action == 'CLOSE_LONG':
                pnl = (filled_price - entry_price) * quantity_btc * leverage
            else:
                pnl = (entry_price - filled_price) * quantity_btc * leverage
            
            total_fee = self.open_trade['fee'] + fee_usdt
            net_pnl = pnl - total_fee
            pnl_pct = net_pnl / quantity_usdt * 100
            
            record.pnl_usdt = net_pnl
            record.pnl_pct = pnl_pct
            record.fee_usdt = total_fee
            
            self.total_pnl += net_pnl
            self.total_fees += total_fee
            self.completed_trades += 1
            
            if net_pnl > 0:
                self.win_count += 1
            else:
                self.loss_count += 1
            
            self.open_trade = None
            self.current_position = None
            
            print(f"\n   📊 交易結果:")
            print(f"      進場: ${entry_price:,.2f}")
            print(f"      出場: ${filled_price:,.2f}")
            print(f"      槓桿損益: ${pnl:+.2f}")
            print(f"      手續費: ${total_fee:.4f}")
            print(f"      淨損益: ${net_pnl:+.2f} ({pnl_pct:+.2f}%)")
        
        self.trade_records.append(record)
        self._save_trade_records()
        self.total_fees += fee_usdt
        
        print(f"\n   ✅ 執行完成")
        print(f"      成交價: ${filled_price:,.2f}")
        print(f"      滑點: {slippage:+.4f}%")
        print(f"      延遲: {latency_ms}ms")
        print(f"      手續費: ${fee_usdt:.4f} ({order_type})")
    
    def _on_signal_received(self, signal: dict):
        """Socket 收到信號時的回調"""
        try:
            self._execute_signal(signal)
        except Exception as e:
            print(f"❌ 執行信號時發生錯誤: {e}")
            import traceback
            traceback.print_exc()
    
    def _print_status(self):
        """顯示當前狀態"""
        runtime = datetime.now() - self.startup_time
        hours = runtime.total_seconds() / 3600
        
        win_rate = (self.win_count / self.completed_trades * 100) if self.completed_trades > 0 else 0
        
        print(f"\n{'=' * 60}")
        print(f"📊 Real Binance Executor 狀態")
        print(f"{'=' * 60}")
        print(f"   ⏱️ 運行時間: {hours:.1f} 小時")
        print(f"   📈 總交易: {self.completed_trades} 筆")
        print(f"   🎯 勝率: {win_rate:.1f}% ({self.win_count}勝 / {self.loss_count}敗)")
        print(f"   💰 總損益: ${self.total_pnl:+.2f}")
        print(f"   💸 總手續費: ${self.total_fees:.4f}")
        
        if self.current_position:
            print(f"\n   📍 當前持倉:")
            print(f"      方向: {'🟢 做多' if self.current_position['side'] == 'long' else '🔴 做空'}")
            print(f"      進場價: ${self.current_position['entry_price']:,.2f}")
            print(f"      數量: {self.current_position['quantity']:.4f} BTC")
        else:
            print(f"\n   📍 當前持倉: 無")
        
        print(f"{'=' * 60}")
    
    def run(self):
        """啟動執行器"""
        self.running = True
        
        print(f"\n🔌 連接 Socket Server ({SOCKET_HOST}:{SOCKET_PORT})...")
        
        # 啟動 Socket Client
        self.socket_client = WhaleSocketClient(SOCKET_HOST, SOCKET_PORT)
        self.socket_client.start_in_thread(self._on_signal_received)
        
        print("✅ 開始監聽信號...")
        print("   按 Ctrl+C 停止\n")
        
        # 定期顯示狀態
        status_interval = 300  # 5 分鐘
        last_status_time = time.time()
        
        try:
            while self.running:
                time.sleep(1)
                
                # 定期顯示狀態
                if time.time() - last_status_time > status_interval:
                    self._print_status()
                    last_status_time = time.time()
                    
        except KeyboardInterrupt:
            print("\n\n⚠️ 收到停止信號...")
        finally:
            self.stop()
    
    def stop(self):
        """停止執行器"""
        self.running = False
        
        if self.socket_client:
            self.socket_client.stop()
        
        self._print_status()
        self._save_trade_records()
        
        print("\n🛑 Real Binance Executor 已停止")


def main():
    parser = argparse.ArgumentParser(
        description='Real Binance Executor - 真實合約交易執行器'
    )
    parser.add_argument(
        '--live', 
        action='store_true',
        help='真實交易模式（預設為模擬模式）'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='減少輸出'
    )
    
    args = parser.parse_args()
    
    dry_run = not args.live
    verbose = not args.quiet
    
    if not dry_run:
        print("\n" + "⚠️ " * 30)
        print("⚠️  警告：你即將啟動真實交易模式！")
        print("⚠️  所有信號都會在幣安正式網執行真實下單！")
        print("⚠️ " * 30)
        
        confirm = input("\n輸入 'YES' 確認啟動真實交易: ")
        if confirm != 'YES':
            print("已取消")
            return
    
    executor = RealBinanceExecutor(dry_run=dry_run, verbose=verbose)
    executor.run()


if __name__ == "__main__":
    main()
