#!/usr/bin/env python3
"""
Task 1.6.1 - Phase C: 真實市場交易模擬測試（調整參數版本）

調整內容:
1. VPIN 閾值: 0.5 → 0.7 (放寬)
2. 信號信心度閾值: 0.6 → 0.5 (降低)
3. 風險過濾: 僅 CRITICAL 阻擋 (DANGER 允許交易)
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import asyncio
import json
import time
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional
from binance import AsyncClient, BinanceSocketManager

from src.exchange.obi_calculator import OBICalculator
from src.exchange.signed_volume_tracker import SignedVolumeTracker
from src.exchange.vpin_calculator import VPINCalculator
from src.exchange.spread_depth_monitor import SpreadDepthMonitor
from src.strategy.signal_generator import SignalGenerator
from src.strategy.regime_filter import RegimeFilter
from src.strategy.execution_engine import ExecutionEngine


class Position:
    """持倉追蹤（含手續費計算）"""
    
    MAKER_FEE = 0.0002
    TAKER_FEE = 0.0005
    FUNDING_RATE_HOURLY = 0.00003
    
    def __init__(self, entry_price: float, direction: str, size: float, 
                 leverage: float, stop_loss: float, take_profit: float, 
                 timestamp: float, capital: float = 100.0):
        self.entry_price = entry_price
        self.direction = direction
        self.size = size
        self.leverage = leverage
        self.stop_loss_pct = stop_loss
        self.take_profit_pct = take_profit
        self.timestamp = timestamp
        self.entry_time = datetime.fromtimestamp(timestamp / 1000)
        self.capital = capital
        
        self.position_value = capital * size * leverage
        self.position_size_btc = self.position_value / entry_price
        self.entry_fee = self.position_value * self.TAKER_FEE
        
        self.exit_price: Optional[float] = None
        self.exit_time: Optional[datetime] = None
        self.exit_fee: Optional[float] = None
        self.funding_fee: Optional[float] = None
        self.pnl_usdt: Optional[float] = None
        self.pnl_pct: Optional[float] = None
        self.exit_reason: Optional[str] = None
        
    def check_exit(self, current_price: float) -> Optional[tuple]:
        if self.direction == "LONG":
            price_change_pct = ((current_price - self.entry_price) / self.entry_price) * 100
            pnl_pct = price_change_pct * self.leverage
            
            if pnl_pct <= -self.stop_loss_pct:
                return ("STOP_LOSS", pnl_pct)
            if pnl_pct >= self.take_profit_pct:
                return ("TAKE_PROFIT", pnl_pct)
        else:
            price_change_pct = ((self.entry_price - current_price) / self.entry_price) * 100
            pnl_pct = price_change_pct * self.leverage
            
            if pnl_pct <= -self.stop_loss_pct:
                return ("STOP_LOSS", pnl_pct)
            if pnl_pct >= self.take_profit_pct:
                return ("TAKE_PROFIT", pnl_pct)
        
        return None
    
    def close(self, exit_price: float, reason: str, timestamp: float):
        self.exit_price = exit_price
        self.exit_time = datetime.fromtimestamp(timestamp / 1000)
        self.exit_reason = reason
        
        holding_hours = (timestamp - self.timestamp) / 1000 / 3600
        self.funding_fee = self.position_value * self.FUNDING_RATE_HOURLY * holding_hours
        self.exit_fee = self.position_value * self.TAKER_FEE
        
        if self.direction == "LONG":
            price_pnl = ((self.exit_price - self.entry_price) / self.entry_price) * self.position_value
        else:
            price_pnl = ((self.entry_price - self.exit_price) / self.entry_price) * self.position_value
        
        gross_pnl = price_pnl
        total_fees = self.entry_fee + self.exit_fee + self.funding_fee
        self.pnl_usdt = gross_pnl - total_fees
        
        used_capital = self.capital * self.size
        self.pnl_pct = (self.pnl_usdt / used_capital) * 100


class AdjustedTradingSimulation:
    """調整參數版本的交易模擬"""
    
    def __init__(self, symbol: str = "BTCUSDT", capital: float = 100.0, duration_minutes: int = 1440):
        self.symbol = symbol
        self.capital = capital
        self.duration_minutes = duration_minutes
        
        # Phase B 指標（調整參數）
        self.obi_calculator = OBICalculator(symbol=symbol)
        self.volume_tracker = SignedVolumeTracker(window_size=20)
        self.vpin_calculator = VPINCalculator(bucket_size=50, num_buckets=50)
        self.spread_monitor = SpreadDepthMonitor(symbol=symbol)
        
        # Phase C 策略引擎（調整參數）
        self.signal_generator = SignalGenerator(
            symbol=symbol,
            long_threshold=0.5,   # 降低: 0.6 → 0.5
            short_threshold=0.5   # 降低: 0.6 → 0.5
        )
        
        self.regime_filter = RegimeFilter(
            symbol=symbol,
            vpin_threshold=0.7,   # 放寬: 0.5 → 0.7
            spread_bps_threshold=10.0,
            min_depth_btc=5.0,
            depth_imbalance_threshold=0.7
        )
        
        self.execution_engine = ExecutionEngine(
            symbol=symbol,
            capital=capital,
            moderate_confidence=0.5,    # 降低: 0.6 → 0.5
            aggressive_confidence=0.7   # 降低: 0.8 → 0.7
        )
        
        # 狀態
        self.latest_orderbook: Optional[Dict] = None
        self.latest_price: Optional[float] = None
        self.warmup_complete = False
        self.trade_count = 0
        
        # 持倉
        self.open_position: Optional[Position] = None
        self.closed_positions: List[Position] = []
        
        # 統計
        self.total_decisions = 0
        self.trade_signals = 0
        self.blocked_by_regime = 0
        self.trades_executed = 0
        
        # OBI 歷史（用於計算 velocity）
        self.obi_history: deque = deque(maxlen=10)
        
        # 時間控制
        self.start_time = time.time()
        self.last_decision_time = 0
        self.decision_interval = 15  # 15 秒決策一次
        
        # 輸出文件
        self.output_file = None
        self.trade_log = []
        
    async def process_orderbook(self, msg):
        """處理訂單簿更新"""
        if msg['e'] == 'error':
            return
        
        bids = [[float(p), float(q)] for p, q in msg['bids'][:20]]
        asks = [[float(p), float(q)] for p, q in msg['asks'][:20]]
        
        self.latest_orderbook = {
            'bids': bids,
            'asks': asks,
            'timestamp': msg.get('E', datetime.now().timestamp() * 1000)
        }
        
        # 計算最新價格（中間價）
        if bids and asks:
            self.latest_price = (bids[0][0] + asks[0][0]) / 2
        
        # 更新 OBI
        self.obi_calculator.update_orderbook(bids, asks)
        
        # 更新 Spread & Depth
        self.spread_monitor.update(bids, asks)
    
    async def process_trade(self, msg):
        """處理交易數據"""
        if msg['e'] == 'error':
            return
        
        trade = {
            'p': float(msg['p']),
            'q': float(msg['q']),
            'm': msg['m']
        }
        
        self.volume_tracker.add_trade(trade)
        self.vpin_calculator.process_trade(trade)
        self.trade_count += 1
        
        # 熱身：需要至少 50 筆交易
        if not self.warmup_complete and self.trade_count >= 50:
            self.warmup_complete = True
            print(f"\n✅ 數據熱身完成（50 筆交易）\n")
    
    def make_decision(self, timestamp: float, verbose: bool = False):
        """執行交易決策（調整參數版本）"""
        if not self.warmup_complete:
            return
        
        if not self.latest_orderbook or not self.latest_price:
            return
        
        self.total_decisions += 1
        
        # 計算所有指標
        obi = self.obi_calculator.calculate()
        self.obi_history.append(obi)
        
        obi_velocity = 0.0
        if len(self.obi_history) >= 2:
            obi_velocity = self.obi_history[-1] - self.obi_history[-2]
        
        signed_volume = self.volume_tracker.get_signed_volume()
        microprice, pressure = self.obi_calculator.calculate_microprice(
            self.latest_orderbook['bids'],
            self.latest_orderbook['asks']
        )
        vpin = self.vpin_calculator.calculate_vpin()
        spread_data = self.spread_monitor.get_spread_metrics()
        depth_data = self.spread_monitor.get_depth_metrics()
        
        # 構建市場數據
        market_data = {
            'obi': obi,
            'obi_velocity': obi_velocity,
            'signed_volume': signed_volume,
            'microprice_pressure': pressure,
            'vpin': vpin,
            'spread_bps': spread_data['spread_bps'],
            'total_depth': depth_data['total_depth'],
            'depth_imbalance': depth_data['depth_imbalance'],
            'timestamp': timestamp,
            'price': self.latest_price
        }
        
        # Layer 1: 生成信號
        signal, confidence, signal_details = self.signal_generator.generate_signal(market_data)
        
        # Layer 2: 風險評估
        is_safe, risk_level, regime_details = self.regime_filter.check_regime(market_data)
        
        # 調整: 只有 CRITICAL 才阻擋（DANGER 允許）
        is_safe_adjusted = risk_level != "CRITICAL"
        
        # Layer 3: 執行決策
        execution = self.execution_engine.make_execution_decision(
            signal=signal,
            confidence=confidence,
            is_safe=is_safe_adjusted,  # 使用調整後的安全判斷
            market_data=market_data
        )
        
        # 輸出決策信息
        if verbose or self.total_decisions % 4 == 1:
            signal_emoji = "📈" if signal == "LONG" else "📉" if signal == "SHORT" else "⚖️ "
            risk_emoji = {"SAFE": "🟢", "WARNING": "🟡", "DANGER": "🟠", "CRITICAL": "🔴"}.get(risk_level, "❓")
            
            print(f"\n[{datetime.fromtimestamp(timestamp/1000).strftime('%H:%M:%S')}] 決策 #{self.total_decisions}")
            print(f"  價格: ${self.latest_price:.2f}")
            print(f"  信號: {signal_emoji} {signal} (信心度: {confidence:.3f})")
            print(f"  風險: {risk_emoji} {risk_level} {'✓ 允許' if is_safe_adjusted else '✗ 阻擋'}")
            print(f"  市場指標:")
            print(f"    OBI: {obi:+7.4f} | Velocity: {obi_velocity:+7.4f}")
            print(f"    Volume: {signed_volume:+7.2f} | VPIN: {vpin:.3f}")
            print(f"    Spread: {spread_data['spread_bps']:6.2f}bps | Depth: {depth_data['total_depth']:.2f} BTC")
        
        # 檢查現有持倉的止損/止盈
        if self.open_position:
            exit_signal = self.open_position.check_exit(self.latest_price)
            if exit_signal:
                reason, pnl = exit_signal
                self.close_position(self.latest_price, reason, timestamp)
                return
        
        # 開新倉位
        if not self.open_position and signal != "NEUTRAL" and execution['execution_style'] != "NO_TRADE":
            if not is_safe_adjusted:
                self.blocked_by_regime += 1
            else:
                self.trade_signals += 1
                self.open_position = Position(
                    entry_price=self.latest_price,
                    direction=signal,
                    size=execution['position_size'],
                    leverage=execution['leverage'],
                    stop_loss=execution['stop_loss_pct'],
                    take_profit=execution['take_profit_pct'],
                    timestamp=timestamp,
                    capital=self.capital
                )
                self.trades_executed += 1
                
                print(f"\n  🔔 開倉: {signal} @ ${self.latest_price:.2f}")
                print(f"     倉位: {execution['position_size']*100:.0f}% × {execution['leverage']}x = {execution['position_size']*execution['leverage']*100:.0f}% 敞口")
                print(f"     止損: -{execution['stop_loss_pct']:.1f}% | 止盈: +{execution['take_profit_pct']:.1f}%")
                
                self.trade_log.append({
                    'timestamp': timestamp,
                    'type': 'OPEN',
                    'direction': signal,
                    'price': self.latest_price,
                    'size': execution['position_size'],
                    'leverage': execution['leverage'],
                    'confidence': confidence,
                    'risk_level': risk_level
                })
    
    def close_position(self, exit_price: float, reason: str, timestamp: float):
        """平倉"""
        if not self.open_position:
            return
        
        self.open_position.close(exit_price, reason, timestamp)
        self.closed_positions.append(self.open_position)
        
        print(f"\n  💰 平倉: {self.open_position.direction} @ ${exit_price:.2f} ({reason})")
        print(f"     持倉時間: {(timestamp - self.open_position.timestamp)/1000/60:.1f} 分鐘")
        print(f"     P&L: {self.open_position.pnl_usdt:+.2f} USDT ({self.open_position.pnl_pct:+.2f}%)")
        
        self.trade_log.append({
            'timestamp': timestamp,
            'type': 'CLOSE',
            'direction': self.open_position.direction,
            'entry_price': self.open_position.entry_price,
            'exit_price': exit_price,
            'pnl_usdt': self.open_position.pnl_usdt,
            'pnl_pct': self.open_position.pnl_pct,
            'reason': reason
        })
        
        self.open_position = None
    
    async def run(self, output_file: str = None):
        """運行模擬"""
        self.output_file = output_file
        
        print("="*60)
        print("🚀 Phase C: 真實市場交易模擬（調整參數版本）")
        print("="*60)
        print()
        print("⚙️  參數調整:")
        print("   VPIN 閾值: 0.5 → 0.7 (放寬)")
        print("   信號閾值: 0.6 → 0.5 (降低)")
        print("   風險過濾: 僅 CRITICAL 阻擋 (DANGER 允許)")
        print()
        print(f"⏱️  運行時長: {self.duration_minutes} 分鐘")
        print(f"💹 交易對: {self.symbol}")
        print(f"📊 決策頻率: 每 {self.decision_interval} 秒一次決策")
        print()
        
        client = await AsyncClient.create()
        bsm = BinanceSocketManager(client)
        
        depth_socket = bsm.depth_socket(self.symbol)
        trade_socket = bsm.aggtrade_socket(self.symbol)
        
        print("🔌 連接 Binance WebSocket...")
        print("📥 收集市場數據中（需要至少 50 筆交易熱身）...")
        print()
        
        try:
            async with depth_socket as ds, trade_socket as ts:
                print("✅ WebSocket 已連接")
                print()
                
                last_report_time = time.time()
                
                while True:
                    # 檢查運行時長
                    elapsed_minutes = (time.time() - self.start_time) / 60
                    if elapsed_minutes >= self.duration_minutes:
                        break
                    
                    # 接收數據
                    depth_msg = await asyncio.wait_for(ds.recv(), timeout=1.0)
                    await self.process_orderbook(depth_msg)
                    
                    trade_msg = await asyncio.wait_for(ts.recv(), timeout=1.0)
                    await self.process_trade(trade_msg)
                    
                    # 決策
                    current_time = time.time()
                    if current_time - self.last_decision_time >= self.decision_interval:
                        self.make_decision(datetime.now().timestamp() * 1000)
                        self.last_decision_time = current_time
                    
                    # 每 2 分鐘報告一次
                    if current_time - last_report_time >= 120:
                        remaining_minutes = self.duration_minutes - elapsed_minutes
                        print(f"\n⏱️  已運行: {elapsed_minutes:.1f}分鐘 | 剩餘: {remaining_minutes:.1f}分鐘")
                        print(f"📊 決策數: {self.total_decisions} | 交易數: {self.trades_executed} | 價格: ${self.latest_price:.2f}")
                        last_report_time = current_time
                    
                    await asyncio.sleep(0.01)
        
        except asyncio.TimeoutError:
            pass
        except KeyboardInterrupt:
            print("\n\n⚠️  用戶中斷測試")
        finally:
            # 強制平倉
            if self.open_position:
                self.close_position(self.latest_price, "FORCE_CLOSE", datetime.now().timestamp() * 1000)
            
            await client.close_connection()
            
            # 輸出統計
            self.print_statistics()
            
            # 保存結果
            if self.output_file:
                self.save_results()
    
    def print_statistics(self):
        """輸出統計結果"""
        print("\n" + "="*60)
        print("📊 測試統計（調整參數版本）")
        print("="*60)
        print()
        print("📈 決策統計:")
        print(f"   總決策數:     {self.total_decisions}")
        print(f"   交易信號:     {self.trade_signals} ({self.trade_signals/max(self.total_decisions,1)*100:.1f}%)")
        print(f"   風險阻擋:     {self.blocked_by_regime}")
        print(f"   實際執行:     {self.trades_executed}")
        print()
        
        if self.closed_positions:
            print("💰 交易績效:")
            winning_trades = [p for p in self.closed_positions if p.pnl_usdt > 0]
            losing_trades = [p for p in self.closed_positions if p.pnl_usdt <= 0]
            
            total_pnl = sum(p.pnl_usdt for p in self.closed_positions)
            win_rate = len(winning_trades) / len(self.closed_positions) * 100
            
            print(f"   完成交易:     {len(self.closed_positions)} 筆")
            print(f"   獲勝交易:     {len(winning_trades)} 筆")
            print(f"   虧損交易:     {len(losing_trades)} 筆")
            print(f"   勝率:         {win_rate:.1f}%")
            print(f"   總盈虧:       {total_pnl:+.2f} USDT ({total_pnl/self.capital*100:+.2f}%)")
            
            if winning_trades:
                avg_win = sum(p.pnl_usdt for p in winning_trades) / len(winning_trades)
                print(f"   平均獲利:     +{avg_win:.2f} USDT")
            
            if losing_trades:
                avg_loss = sum(p.pnl_usdt for p in losing_trades) / len(losing_trades)
                print(f"   平均虧損:     {avg_loss:.2f} USDT")
        else:
            print("⚠️  沒有完成的交易")
        print()
    
    def save_results(self):
        """保存結果到 JSON"""
        results = {
            'test_type': 'Phase C Adjusted Parameters',
            'parameters': {
                'vpin_threshold': 0.7,
                'signal_threshold': 0.5,
                'risk_filter': 'CRITICAL_ONLY'
            },
            'statistics': {
                'total_decisions': self.total_decisions,
                'trade_signals': self.trade_signals,
                'blocked_by_regime': self.blocked_by_regime,
                'trades_executed': self.trades_executed
            },
            'trades': self.trade_log,
            'closed_positions': [
                {
                    'entry_price': p.entry_price,
                    'exit_price': p.exit_price,
                    'direction': p.direction,
                    'pnl_usdt': p.pnl_usdt,
                    'pnl_pct': p.pnl_pct,
                    'exit_reason': p.exit_reason,
                    'holding_minutes': (p.exit_time.timestamp() - p.entry_time.timestamp()) / 60
                }
                for p in self.closed_positions
            ]
        }
        
        with open(self.output_file, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"✅ 結果已保存到: {self.output_file}")


async def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 1440
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    simulation = AdjustedTradingSimulation(
        symbol="BTCUSDT",
        capital=100.0,
        duration_minutes=duration
    )
    
    await simulation.run(output_file)


if __name__ == "__main__":
    asyncio.run(main())
