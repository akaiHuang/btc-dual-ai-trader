#!/usr/bin/env python3
"""
Hybrid Multi-Mode Paper Trading System
======================================

使用 paper_trading_system.py 的完整框架，但策略改為 Hybrid M0-M5

功能：
1. 並行測試 6 個 Hybrid 檔位 (M0-M5)
2. 實時顯示未實現/已實現損益
3. 完整訂單簿記錄（JSON）
4. 視覺化對比報告
5. 即時績效監控

使用方法:
    python scripts/paper_trading_hybrid.py 60  # 運行 60 分鐘
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import asyncio
import json
from datetime import datetime
from typing import Dict, List
import pandas as pd
import numpy as np

from src.strategy.hybrid_multi_mode import MultiModeHybridStrategy, TradingMode, SignalType


class HybridPaperTradingSystem:
    """Hybrid 多檔位 Paper Trading 系統"""
    
    # 真實費率
    MAKER_FEE = 0.0002
    TAKER_FEE = 0.0005
    FUNDING_RATE_HOURLY = 0.00003
    SLIPPAGE_BPS = 2
    
    def __init__(self, initial_capital: float = 100.0, decision_interval: int = 15):
        """
        Args:
            initial_capital: 每個策略的初始資金
            decision_interval: 決策間隔（秒）
        """
        self.initial_capital = initial_capital
        self.decision_interval = decision_interval
        
        # 創建 6 個策略實例
        self.strategies = {
            'M0': MultiModeHybridStrategy(TradingMode.M0_ULTRA_SAFE),
            'M1': MultiModeHybridStrategy(TradingMode.M1_SAFE),
            'M2': MultiModeHybridStrategy(TradingMode.M2_NORMAL),
            'M3': MultiModeHybridStrategy(TradingMode.M3_AGGRESSIVE),
            'M4': MultiModeHybridStrategy(TradingMode.M4_VERY_AGGRESSIVE),
            'M5': MultiModeHybridStrategy(TradingMode.M5_ULTRA_AGGRESSIVE),
        }
        
        # 每個策略的餘額
        self.balances = {mode: initial_capital for mode in self.strategies.keys()}
        
        # 訂單記錄
        self.orders = {mode: [] for mode in self.strategies.keys()}
        self.open_positions = {mode: None for mode in self.strategies.keys()}
        
        # 統計
        self.total_decisions = 0
        self.test_start_time = datetime.now()
        self.last_report_update = 0
        
        # 保存檔案
        self._init_save_files()
    
    def _init_save_files(self):
        """初始化保存檔案"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.save_dir = Path('data/paper_trading')
        self.save_dir.mkdir(parents=True, exist_ok=True)
        
        self.json_filename = self.save_dir / f'hybrid_multimode_{timestamp}.json'
        self.log_filename = self.save_dir / f'hybrid_multimode_{timestamp}.txt'
        self.visual_report_filename = self.save_dir / f'hybrid_multimode_{timestamp}_visual.txt'
        
        # 初始化 JSON
        initial_data = {
            'metadata': {
                'system': 'Hybrid Multi-Mode Paper Trading',
                'start_time': self.test_start_time.isoformat(),
                'initial_capital': self.initial_capital,
                'decision_interval': self.decision_interval,
                'modes': list(self.strategies.keys())
            },
            'orders': {mode: [] for mode in self.strategies.keys()}
        }
        
        with open(self.json_filename, 'w') as f:
            json.dump(initial_data, f, indent=2)
        
        print(f"💾 數據將保存到:")
        print(f"   JSON: {self.json_filename}")
        print(f"   Log: {self.log_filename}")
        print(f"   Report: {self.visual_report_filename}")
        print()
    
    async def fetch_market_data(self):
        """獲取市場數據（模擬 - 實際應該從 WebSocket 獲取）"""
        # TODO: 整合真實的 WebSocket 數據
        # 暫時返回模擬數據
        import ccxt
        exchange = ccxt.binance({'options': {'defaultType': 'future'}})
        
        ohlcv = exchange.fetch_ohlcv('BTC/USDT:USDT', '15m', limit=200)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        funding_data = exchange.fetch_funding_rate('BTC/USDT:USDT')
        df['fundingRate'] = funding_data['fundingRate']
        
        current_price = df['close'].iloc[-1]
        current_timestamp = datetime.now().timestamp() * 1000
        
        return df, current_price, current_timestamp
    
    def create_order(self, mode: str, signal_result: Dict, entry_price: float, timestamp: float):
        """創建訂單"""
        config = self.strategies[mode].get_current_config()
        
        order = {
            'order_id': len(self.orders[mode]) + 1,
            'timestamp': timestamp,
            'entry_time': datetime.fromtimestamp(timestamp / 1000),
            'mode': mode,
            'signal': signal_result['signal'].value,
            'confidence': signal_result.get('confidence', 0),
            'reasoning': signal_result.get('reasoning', ''),
            'entry_price': entry_price,
            'leverage': config.leverage,
            'tp_pct': config.tp_pct,
            'sl_pct': config.sl_pct,
            'time_stop_hours': config.time_stop_hours,
            'capital': self.balances[mode],
            'status': 'OPEN'
        }
        
        # 計算滑點後的實際入場價
        slippage = self.SLIPPAGE_BPS / 10000
        if signal_result['signal'] == SignalType.LONG:
            order['actual_entry_price'] = entry_price * (1 + slippage)
            order['tp_price'] = order['actual_entry_price'] * (1 + config.tp_pct)
            order['sl_price'] = order['actual_entry_price'] * (1 - config.sl_pct)
        else:
            order['actual_entry_price'] = entry_price * (1 - slippage)
            order['tp_price'] = order['actual_entry_price'] * (1 - config.tp_pct)
            order['sl_price'] = order['actual_entry_price'] * (1 + config.sl_pct)
        
        # 計算倉位
        order['position_value'] = order['capital'] * config.leverage
        order['entry_fee'] = order['position_value'] * self.TAKER_FEE
        
        # 時間止損
        order['time_stop_timestamp'] = timestamp + (config.time_stop_hours * 3600 * 1000)
        
        self.orders[mode].append(order)
        self.open_positions[mode] = order
        
        return order
    
    def check_exit(self, mode: str, current_price: float, current_timestamp: float):
        """檢查是否觸發出場"""
        order = self.open_positions[mode]
        if not order or order['status'] != 'OPEN':
            return None
        
        # 檢查 TP/SL
        if order['signal'] == 'LONG':
            if current_price >= order['tp_price']:
                return self.close_order(mode, current_price, 'TP', current_timestamp)
            elif current_price <= order['sl_price']:
                return self.close_order(mode, current_price, 'SL', current_timestamp)
        else:  # SHORT
            if current_price <= order['tp_price']:
                return self.close_order(mode, current_price, 'TP', current_timestamp)
            elif current_price >= order['sl_price']:
                return self.close_order(mode, current_price, 'SL', current_timestamp)
        
        # 檢查時間止損
        if current_timestamp >= order['time_stop_timestamp']:
            return self.close_order(mode, current_price, 'TIME_STOP', current_timestamp)
        
        return None
    
    def close_order(self, mode: str, exit_price: float, reason: str, timestamp: float):
        """平倉"""
        order = self.open_positions[mode]
        
        order['exit_price'] = exit_price
        order['exit_time'] = datetime.fromtimestamp(timestamp / 1000)
        order['exit_reason'] = reason
        order['holding_seconds'] = (timestamp - order['timestamp']) / 1000
        order['status'] = 'CLOSED'
        
        # 計算盈虧
        holding_hours = order['holding_seconds'] / 3600
        funding_fee = order['position_value'] * self.FUNDING_RATE_HOURLY * holding_hours
        
        slippage = self.SLIPPAGE_BPS / 10000
        if order['signal'] == 'LONG':
            actual_exit_price = exit_price * (1 - slippage)
            price_pnl = ((actual_exit_price - order['actual_entry_price']) / 
                        order['actual_entry_price']) * order['position_value']
        else:
            actual_exit_price = exit_price * (1 + slippage)
            price_pnl = ((order['actual_entry_price'] - actual_exit_price) / 
                        order['actual_entry_price']) * order['position_value']
        
        exit_fee = order['position_value'] * self.TAKER_FEE
        total_fees = order['entry_fee'] + exit_fee + funding_fee
        
        order['pnl_usdt'] = price_pnl - total_fees
        order['roi'] = (order['pnl_usdt'] / order['capital']) * 100
        
        # 更新餘額
        self.balances[mode] += order['pnl_usdt']
        
        # 清空持倉
        self.open_positions[mode] = None
        
        # 保存
        self._save_order(mode, order)
        
        return order
    
    def _save_order(self, mode: str, order: dict):
        """即時保存訂單"""
        try:
            with open(self.json_filename, 'r') as f:
                data = json.load(f)
            
            # 轉換不可序列化的對象
            order_copy = order.copy()
            if 'entry_time' in order_copy and isinstance(order_copy['entry_time'], datetime):
                order_copy['entry_time'] = order_copy['entry_time'].isoformat()
            if 'exit_time' in order_copy and isinstance(order_copy['exit_time'], datetime):
                order_copy['exit_time'] = order_copy['exit_time'].isoformat()
            
            data['orders'][mode].append(order_copy)
            data['metadata']['current_balances'] = self.balances
            
            with open(self.json_filename, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"⚠️  保存錯誤: {e}")
    
    async def run(self, duration_minutes: int = 60):
        """運行測試"""
        print("="*80)
        print("🎮 Hybrid Multi-Mode Paper Trading System")
        print("="*80)
        print(f"⏰ Duration: {duration_minutes} minutes")
        print(f"💰 Initial Capital: ${self.initial_capital:.2f} per mode")
        print(f"📊 Modes: {len(self.strategies)}")
        print("="*80)
        print()
        
        # 顯示各模式配置
        for mode, strategy in self.strategies.items():
            config = strategy.get_current_config()
            print(f"{mode}: {config.description}")
            print(f"   Target: {config.target_frequency}")
            print(f"   Leverage: {config.leverage}x | TP/SL: {config.tp_pct:.2%}/{config.sl_pct:.2%}")
        print()
        print("="*80)
        print("🔍 Monitoring...")
        print()
        
        end_time = datetime.now().timestamp() + (duration_minutes * 60)
        
        try:
            while datetime.now().timestamp() < end_time:
                self.total_decisions += 1
                
                # 獲取市場數據
                df, current_price, current_timestamp = await self.fetch_market_data()
                
                # 檢查每個模式
                for mode, strategy in self.strategies.items():
                    # 檢查持倉出場
                    if self.open_positions[mode]:
                        closed_order = self.check_exit(mode, current_price, current_timestamp)
                        if closed_order:
                            emoji = "🟢" if closed_order['pnl_usdt'] > 0 else "🔴"
                            print(f"{emoji} {mode} Trade #{closed_order['order_id']} CLOSED")
                            print(f"   {closed_order['signal']} | {closed_order['exit_reason']}")
                            print(f"   ROI: {closed_order['roi']:+.2f}% | Balance: ${self.balances[mode]:.2f}")
                    
                    # 生成信號（無持倉時）
                    elif self.open_positions[mode] is None:
                        result = strategy.generate_signal(df)
                        
                        if result['signal'] != SignalType.NEUTRAL:
                            order = self.create_order(mode, result, current_price, current_timestamp)
                            print(f"🚨 {mode} Trade #{order['order_id']} OPENED")
                            print(f"   {order['signal']} | Confidence: {order['confidence']:.2%}")
                            print(f"   Price: ${current_price:,.2f} | Leverage: {order['leverage']}x")
                
                # 顯示狀態
                timestamp_str = datetime.now().strftime("%H:%M:%S")
                open_count = sum(1 for p in self.open_positions.values() if p is not None)
                print(f"[{timestamp_str}] Decisions: {self.total_decisions} | Open: {open_count}/6", end='\r')
                
                # 等待
                await asyncio.sleep(self.decision_interval)
                
        except KeyboardInterrupt:
            print("\n\n🛑 Stopped by user")
        
        # 強制平倉所有持倉
        df, current_price, current_timestamp = await self.fetch_market_data()
        for mode in self.strategies.keys():
            if self.open_positions[mode]:
                self.close_order(mode, current_price, 'FORCED_EXIT', current_timestamp)
        
        # 生成報告
        self._generate_report()
    
    def _generate_report(self):
        """生成完整報告"""
        print("\n" + "="*80)
        print("📊 Final Report")
        print("="*80)
        print()
        
        for mode in self.strategies.keys():
            closed_orders = [o for o in self.orders[mode] if o['status'] == 'CLOSED']
            
            if closed_orders:
                winning = [o for o in closed_orders if o['pnl_usdt'] > 0]
                win_rate = len(winning) / len(closed_orders) * 100
                total_roi = (self.balances[mode] - self.initial_capital) / self.initial_capital * 100
                
                print(f"{mode}:")
                print(f"   Balance: ${self.balances[mode]:.2f} ({total_roi:+.2f}%)")
                print(f"   Trades: {len(closed_orders)} | Win Rate: {win_rate:.1f}%")
            else:
                print(f"{mode}:")
                print(f"   Balance: ${self.balances[mode]:.2f} (No trades)")
        
        print()
        print(f"💾 Reports saved:")
        print(f"   {self.json_filename}")
        print(f"   {self.log_filename}")
        print("="*80)


async def main():
    """主函數"""
    duration = 60  # 默認 60 分鐘
    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
        except ValueError:
            print("❌ 參數錯誤：請提供分鐘數")
            sys.exit(1)
    
    system = HybridPaperTradingSystem(
        initial_capital=100.0,
        decision_interval=15
    )
    
    await system.run(duration_minutes=duration)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️  測試已中斷")
        sys.exit(0)
