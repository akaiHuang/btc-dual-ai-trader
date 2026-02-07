#!/usr/bin/env python3
"""
Task 1.6.1 - Phase C: 即時交易模擬測試
使用真實市場數據測試 Phase C 決策系統的交易表現
"""

import asyncio
import json
import time
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional
import websockets
import random

# 導入 Phase C 決策系統
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from src.strategy.signal_generator import SignalGenerator
from src.strategy.regime_filter import RegimeFilter
from src.strategy.execution_engine import ExecutionEngine
from src.strategy.layered_trading_engine import LayeredTradingEngine


class Position:
    """持倉追蹤"""
    def __init__(self, entry_price: float, direction: str, size: float, 
                 leverage: float, stop_loss: float, take_profit: float, 
                 timestamp: float):
        self.entry_price = entry_price
        self.direction = direction  # LONG or SHORT
        self.size = size  # 倉位大小百分比
        self.leverage = leverage
        self.stop_loss_pct = stop_loss
        self.take_profit_pct = take_profit
        self.timestamp = timestamp
        self.entry_time = datetime.fromtimestamp(timestamp)
        self.exit_price: Optional[float] = None
        self.exit_time: Optional[datetime] = None
        self.pnl_pct: Optional[float] = None
        self.exit_reason: Optional[str] = None
        
    def check_exit(self, current_price: float) -> Optional[tuple]:
        """檢查是否觸發止損或止盈"""
        if self.direction == "LONG":
            pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100 * self.leverage
            
            # 止損
            if pnl_pct <= -self.stop_loss_pct:
                return ("STOP_LOSS", pnl_pct)
            
            # 止盈
            if pnl_pct >= self.take_profit_pct:
                return ("TAKE_PROFIT", pnl_pct)
                
        else:  # SHORT
            pnl_pct = ((self.entry_price - current_price) / self.entry_price) * 100 * self.leverage
            
            # 止損
            if pnl_pct <= -self.stop_loss_pct:
                return ("STOP_LOSS", pnl_pct)
            
            # 止盈
            if pnl_pct >= self.take_profit_pct:
                return ("TAKE_PROFIT", pnl_pct)
        
        return None
    
    def close(self, exit_price: float, reason: str, timestamp: float):
        """平倉"""
        self.exit_price = exit_price
        self.exit_time = datetime.fromtimestamp(timestamp)
        self.exit_reason = reason
        
        if self.direction == "LONG":
            self.pnl_pct = ((exit_price - self.entry_price) / self.entry_price) * 100 * self.leverage
        else:
            self.pnl_pct = ((self.entry_price - exit_price) / self.entry_price) * 100 * self.leverage
    
    def get_unrealized_pnl(self, current_price: float) -> float:
        """獲取未實現盈虧"""
        if self.direction == "LONG":
            return ((current_price - self.entry_price) / self.entry_price) * 100 * self.leverage
        else:
            return ((self.entry_price - current_price) / self.entry_price) * 100 * self.leverage


class MarketDataSimulator:
    """市場數據模擬器 - 從真實價格生成微觀結構指標"""
    
    def __init__(self):
        self.price_history = deque(maxlen=100)
        self.volume_history = deque(maxlen=50)
        
    def simulate_market_indicators(self, price: float, volume: float = None) -> Dict:
        """從價格和成交量模擬市場微觀結構指標"""
        self.price_history.append(price)
        if volume:
            self.volume_history.append(volume)
        
        if len(self.price_history) < 10:
            return None
        
        # 計算價格動量
        recent_prices = list(self.price_history)[-20:]
        price_change = (recent_prices[-1] - recent_prices[0]) / recent_prices[0]
        volatility = np.std([recent_prices[i]/recent_prices[i-1] - 1 
                            for i in range(1, len(recent_prices))])
        
        # 模擬 OBI (基於價格趨勢)
        obi = np.clip(price_change * 50, -1, 1)  # 放大價格變化
        obi_velocity = (obi - getattr(self, 'last_obi', 0)) * 10
        obi_velocity = np.clip(obi_velocity, -0.3, 0.3)
        self.last_obi = obi
        
        # 模擬 Signed Volume (基於價格方向)
        if len(recent_prices) >= 2:
            price_direction = 1 if recent_prices[-1] > recent_prices[-2] else -1
            signed_volume = price_direction * 30 * (1 + abs(price_change) * 100)
        else:
            signed_volume = 0
        
        # 模擬 Microprice Pressure
        microprice_pressure = np.clip(price_change * 30, -1, 1)
        
        # 模擬 VPIN (基於波動率)
        vpin = min(volatility * 50, 0.9)  # 高波動 = 高 VPIN
        
        # 模擬 Spread (基於波動率)
        spread_bps = 2.0 + volatility * 1000  # 基礎 2bps + 波動率影響
        
        # 模擬 Depth
        total_depth = max(5.0, 15.0 - volatility * 500)  # 波動高 = 深度低
        
        # 模擬 Depth Imbalance
        depth_imbalance = np.clip(price_change * 20, -0.9, 0.9)
        
        return {
            # Signal Layer
            'obi': float(obi),
            'obi_velocity': float(obi_velocity),
            'signed_volume': float(signed_volume),
            'microprice_pressure': float(microprice_pressure),
            
            # Regime Layer
            'vpin': float(vpin),
            'spread_bps': float(spread_bps),
            'total_depth': float(total_depth),
            'depth_imbalance': float(depth_imbalance),
            
            # 價格
            'price': price,
            'timestamp': time.time()
        }


import numpy as np


class LiveTradingSimulator:
    """持倉追蹤"""
    def __init__(self, entry_price: float, direction: str, size: float, 
                 leverage: float, stop_loss: float, take_profit: float, 
                 timestamp: float):
        self.entry_price = entry_price
        self.direction = direction  # LONG or SHORT
        self.size = size  # 倉位大小百分比
        self.leverage = leverage
        self.stop_loss_pct = stop_loss
        self.take_profit_pct = take_profit
        self.timestamp = timestamp
        self.entry_time = datetime.fromtimestamp(timestamp)
        self.exit_price: Optional[float] = None
        self.exit_time: Optional[datetime] = None
        self.pnl_pct: Optional[float] = None
        self.exit_reason: Optional[str] = None
        
    def check_exit(self, current_price: float) -> Optional[tuple]:
        """檢查是否觸發止損或止盈"""
        if self.direction == "LONG":
            pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100 * self.leverage
            
            # 止損
            if pnl_pct <= -self.stop_loss_pct:
                return ("STOP_LOSS", pnl_pct)
            
            # 止盈
            if pnl_pct >= self.take_profit_pct:
                return ("TAKE_PROFIT", pnl_pct)
                
        else:  # SHORT
            pnl_pct = ((self.entry_price - current_price) / self.entry_price) * 100 * self.leverage
            
            # 止損
            if pnl_pct <= -self.stop_loss_pct:
                return ("STOP_LOSS", pnl_pct)
            
            # 止盈
            if pnl_pct >= self.take_profit_pct:
                return ("TAKE_PROFIT", pnl_pct)
        
        return None
    
    def close(self, exit_price: float, reason: str, timestamp: float):
        """平倉"""
        self.exit_price = exit_price
        self.exit_time = datetime.fromtimestamp(timestamp)
        self.exit_reason = reason
        
        if self.direction == "LONG":
            self.pnl_pct = ((exit_price - self.entry_price) / self.entry_price) * 100 * self.leverage
        else:
            self.pnl_pct = ((self.entry_price - exit_price) / self.entry_price) * 100 * self.leverage
    
    def get_unrealized_pnl(self, current_price: float) -> float:
        """獲取未實現盈虧"""
        if self.direction == "LONG":
            return ((current_price - self.entry_price) / self.entry_price) * 100 * self.leverage
        else:
            return ((self.entry_price - current_price) / self.entry_price) * 100 * self.leverage


class LiveTradingSimulator:
    """即時交易模擬器"""
    
    def __init__(self):
        # Phase C 決策引擎
        self.trading_engine = LayeredTradingEngine()
        
        # 市場數據模擬器
        self.market_simulator = MarketDataSimulator()
        
        # 市場數據
        self.latest_price = 0.0
        self.price_history = deque(maxlen=100)
        
        # 交易追蹤
        self.open_position: Optional[Position] = None
        self.closed_positions: List[Position] = []
        self.decisions_log: List[Dict] = []
        
        # 統計
        self.total_decisions = 0
        self.trade_signals = 0
        self.trades_executed = 0
        self.blocked_by_regime = 0
        
    async def connect_websocket(self):
        """連接 Binance WebSocket"""
        symbol = "btcusdt"
        streams = [
            f"{symbol}@depth20@100ms",  # 訂單簿
            f"{symbol}@aggTrade"         # 成交數據
        ]
        url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
        
        return await websockets.connect(url)
    
    def process_orderbook(self, data: Dict):
        """處理訂單簿數據"""
        try:
            # 更新當前價格（使用最佳買賣中點）
            bids = data.get('bids', [])
            asks = data.get('asks', [])
            
            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                self.latest_price = (best_bid + best_ask) / 2
                self.price_history.append(self.latest_price)
                
        except Exception as e:
            print(f"⚠️  訂單簿處理錯誤: {e}")
    
    def process_trade(self, data: Dict):
        """處理成交數據"""
        try:
            price = float(data['p'])
            quantity = float(data['q'])
            # 只更新價格
            self.latest_price = price
                
        except Exception as e:
            print(f"⚠️  成交數據處理錯誤: {e}")
    
    def get_market_data(self) -> Optional[Dict]:
        """獲取當前市場數據"""
        try:
            if self.latest_price == 0:
                return None
            
            # 使用模擬器生成市場指標
            market_data = self.market_simulator.simulate_market_indicators(self.latest_price)
            
            return market_data
            
        except Exception as e:
            print(f"⚠️  市場數據獲取錯誤: {e}")
            return None
    
    def make_decision(self, market_data: Dict):
        """做出交易決策"""
        self.total_decisions += 1
        
        # 使用 Phase C 決策引擎
        decision = self.trading_engine.process_market_data(market_data)
        
        # 記錄決策
        log_entry = {
            'timestamp': market_data['timestamp'],
            'datetime': datetime.fromtimestamp(market_data['timestamp']).isoformat(),
            'price': market_data['price'],
            'decision': decision
        }
        self.decisions_log.append(log_entry)
        
        # 統計
        signal_direction = decision['signal']['direction']
        if signal_direction in ['LONG', 'SHORT']:
            self.trade_signals += 1
        
        can_trade = decision['can_trade']
        if not can_trade and signal_direction in ['LONG', 'SHORT']:
            self.blocked_by_regime += 1
        
        # 處理交易邏輯
        self.handle_trading(decision, market_data)
    
    def handle_trading(self, decision: Dict, market_data: Dict):
        """處理交易執行"""
        current_price = market_data['price']
        timestamp = market_data['timestamp']
        
        # 1. 檢查是否需要平倉現有持倉
        if self.open_position:
            # 檢查止損/止盈
            exit_result = self.open_position.check_exit(current_price)
            if exit_result:
                reason, pnl = exit_result
                self.close_position(current_price, reason, timestamp)
                print(f"\n🔔 平倉 [{reason}]")
                print(f"   方向: {self.open_position.direction}")
                print(f"   進場: ${self.open_position.entry_price:.2f}")
                print(f"   出場: ${current_price:.2f}")
                print(f"   盈虧: {pnl:+.2f}%")
                print(f"   持倉時間: {(timestamp - self.open_position.timestamp)/60:.1f} 分鐘")
                return
            
            # 檢查反向信號（提前平倉）
            signal_direction = decision['signal']['direction']
            if signal_direction != self.open_position.direction and signal_direction != 'NEUTRAL':
                pnl = self.open_position.get_unrealized_pnl(current_price)
                self.close_position(current_price, "REVERSE_SIGNAL", timestamp)
                print(f"\n🔔 平倉 [反向信號]")
                print(f"   方向: {self.open_position.direction}")
                print(f"   盈虧: {pnl:+.2f}%")
                return
        
        # 2. 檢查是否可以開新倉
        if not self.open_position and decision['can_trade']:
            signal_direction = decision['signal']['direction']
            
            if signal_direction in ['LONG', 'SHORT']:
                execution = decision['execution']
                
                # 創建新持倉
                position = Position(
                    entry_price=current_price,
                    direction=signal_direction,
                    size=execution['position_size'],
                    leverage=execution['leverage'],
                    stop_loss=execution['stop_loss_pct'],
                    take_profit=execution['take_profit_pct'],
                    timestamp=timestamp
                )
                
                self.open_position = position
                self.trades_executed += 1
                
                print(f"\n🚀 開倉 [{execution['style']}]")
                print(f"   方向: {signal_direction}")
                print(f"   價格: ${current_price:.2f}")
                print(f"   倉位: {execution['position_size']*100:.0f}%")
                print(f"   槓桿: {execution['leverage']:.1f}x")
                print(f"   止損: {execution['stop_loss_pct']:.2f}%")
                print(f"   止盈: {execution['take_profit_pct']:.2f}%")
                print(f"   信心度: {decision['signal']['confidence']:.3f}")
    
    def close_position(self, exit_price: float, reason: str, timestamp: float):
        """平倉"""
        if self.open_position:
            self.open_position.close(exit_price, reason, timestamp)
            self.closed_positions.append(self.open_position)
            self.open_position = None
    
    def print_statistics(self):
        """輸出統計結果"""
        print("\n" + "="*60)
        print("📊 交易模擬統計")
        print("="*60)
        
        # 基本統計
        print(f"\n📈 決策統計:")
        print(f"   總決策數:     {self.total_decisions}")
        print(f"   交易信號:     {self.trade_signals} ({self.trade_signals/max(1,self.total_decisions)*100:.1f}%)")
        print(f"   風險阻擋:     {self.blocked_by_regime} ({self.blocked_by_regime/max(1,self.trade_signals)*100:.1f}% 的信號)")
        print(f"   實際執行:     {self.trades_executed} ({self.trades_executed/max(1,self.total_decisions)*100:.1f}%)")
        
        # 持倉統計
        if self.open_position:
            print(f"\n📍 當前持倉:")
            pnl = self.open_position.get_unrealized_pnl(self.latest_price)
            holding_time = (time.time() - self.open_position.timestamp) / 60
            print(f"   方向:         {self.open_position.direction}")
            print(f"   進場價:       ${self.open_position.entry_price:.2f}")
            print(f"   當前價:       ${self.latest_price:.2f}")
            print(f"   未實現盈虧:   {pnl:+.2f}%")
            print(f"   持倉時間:     {holding_time:.1f} 分鐘")
        
        # 已平倉交易統計
        if self.closed_positions:
            print(f"\n💰 已平倉交易:")
            print(f"   交易筆數:     {len(self.closed_positions)}")
            
            winning_trades = [p for p in self.closed_positions if p.pnl_pct > 0]
            losing_trades = [p for p in self.closed_positions if p.pnl_pct <= 0]
            
            win_rate = len(winning_trades) / len(self.closed_positions) * 100
            print(f"   勝率:         {win_rate:.1f}% ({len(winning_trades)}/{len(self.closed_positions)})")
            
            total_pnl = sum(p.pnl_pct for p in self.closed_positions)
            avg_pnl = total_pnl / len(self.closed_positions)
            print(f"   總盈虧:       {total_pnl:+.2f}%")
            print(f"   平均盈虧:     {avg_pnl:+.2f}%")
            
            if winning_trades:
                avg_win = sum(p.pnl_pct for p in winning_trades) / len(winning_trades)
                max_win = max(p.pnl_pct for p in winning_trades)
                print(f"   平均盈利:     +{avg_win:.2f}%")
                print(f"   最大盈利:     +{max_win:.2f}%")
            
            if losing_trades:
                avg_loss = sum(p.pnl_pct for p in losing_trades) / len(losing_trades)
                max_loss = min(p.pnl_pct for p in losing_trades)
                print(f"   平均虧損:     {avg_loss:.2f}%")
                print(f"   最大虧損:     {max_loss:.2f}%")
            
            # 詳細交易列表
            print(f"\n📋 交易明細:")
            for i, pos in enumerate(self.closed_positions, 1):
                duration = (pos.exit_time.timestamp() - pos.timestamp) / 60
                print(f"   {i}. {pos.direction:5s} | "
                      f"${pos.entry_price:>8.2f} → ${pos.exit_price:>8.2f} | "
                      f"{pos.pnl_pct:>+6.2f}% | "
                      f"{duration:>5.1f}分 | "
                      f"{pos.exit_reason}")
        
        # Phase C 系統統計
        if self.total_decisions > 0:
            print(f"\n🎯 Phase C 系統表現:")
            stats = self.trading_engine.get_comprehensive_statistics()
            
            if 'signal' in stats:
                signal_stats = stats['signal']
                print(f"   信號分布:")
                print(f"     LONG:       {signal_stats['signal_counts']['LONG']}")
                print(f"     SHORT:      {signal_stats['signal_counts']['SHORT']}")
                print(f"     NEUTRAL:    {signal_stats['signal_counts']['NEUTRAL']}")
            
            if 'regime' in stats:
                regime_stats = stats['regime']
                print(f"   風險過濾:")
                print(f"     安全檢查:   {regime_stats['total_checks']}")
                print(f"     允許交易:   {regime_stats['safe_count']}")
                print(f"     阻擋交易:   {regime_stats['blocked_count']} ({regime_stats['block_rate']*100:.1f}%)")
            
            if 'execution' in stats:
                execution_stats = stats['execution']
                print(f"   執行風格:")
                print(f"     AGGRESSIVE: {execution_stats['style_counts']['AGGRESSIVE']}")
                print(f"     MODERATE:   {execution_stats['style_counts']['MODERATE']}")
                print(f"     CONSERVATIVE: {execution_stats['style_counts']['CONSERVATIVE']}")
    
    async def run(self, duration_minutes: int = 5):
        """運行模擬"""
        print("="*60)
        print("🚀 Task 1.6.1 - Phase C: 即時交易模擬")
        print("="*60)
        print(f"\n⏱️  運行時長: {duration_minutes} 分鐘")
        print(f"💹 交易對: BTCUSDT")
        print(f"📊 決策頻率: 每 10 秒一次決策")
        print(f"\n🔌 連接 Binance WebSocket...\n")
        
        ws = await self.connect_websocket()
        print("✅ 已連接\n")
        
        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)
        
        last_decision_time = 0
        decision_interval = 10  # 10秒做一次決策
        
        last_status_time = start_time
        status_interval = 60  # 60秒輸出一次狀態
        
        try:
            while time.time() < end_time:
                # 接收 WebSocket 數據
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(message)
                    
                    stream = data['stream']
                    payload = data['data']
                    
                    # 處理訂單簿
                    if 'depth20' in stream:
                        self.process_orderbook(payload)
                    
                    # 處理成交
                    elif 'aggTrade' in stream:
                        self.process_trade(payload)
                    
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    print(f"⚠️  WebSocket 錯誤: {e}")
                    continue
                
                # 定期做決策
                current_time = time.time()
                if current_time - last_decision_time >= decision_interval:
                    market_data = self.get_market_data()
                    if market_data:
                        self.make_decision(market_data)
                    last_decision_time = current_time
                
                # 定期輸出狀態
                if current_time - last_status_time >= status_interval:
                    elapsed = (current_time - start_time) / 60
                    remaining = (end_time - current_time) / 60
                    print(f"\n⏱️  已運行: {elapsed:.1f}分鐘 | 剩餘: {remaining:.1f}分鐘")
                    print(f"📊 決策數: {self.total_decisions} | 交易數: {self.trades_executed}")
                    if self.open_position:
                        pnl = self.open_position.get_unrealized_pnl(self.latest_price)
                        print(f"📍 持倉: {self.open_position.direction} @ ${self.open_position.entry_price:.2f} | 盈虧: {pnl:+.2f}%")
                    last_status_time = current_time
                
        except KeyboardInterrupt:
            print("\n\n⚠️  用戶中斷")
        finally:
            await ws.close()
            
            # 如果還有持倉，強制平倉
            if self.open_position:
                self.close_position(self.latest_price, "SIMULATION_END", time.time())
                print(f"\n🔔 模擬結束，強制平倉")
            
            # 輸出最終統計
            self.print_statistics()
            
            # 保存詳細日誌
            self.save_logs()
    
    def save_logs(self):
        """保存交易日誌"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 保存決策日誌
        log_file = f"data/simulation_log_{timestamp}.json"
        with open(log_file, 'w') as f:
            json.dump({
                'decisions': self.decisions_log,
                'closed_positions': [
                    {
                        'direction': p.direction,
                        'entry_price': p.entry_price,
                        'exit_price': p.exit_price,
                        'entry_time': p.entry_time.isoformat(),
                        'exit_time': p.exit_time.isoformat(),
                        'pnl_pct': p.pnl_pct,
                        'size': p.size,
                        'leverage': p.leverage,
                        'exit_reason': p.exit_reason
                    }
                    for p in self.closed_positions
                ],
                'statistics': {
                    'total_decisions': self.total_decisions,
                    'trade_signals': self.trade_signals,
                    'trades_executed': self.trades_executed,
                    'blocked_by_regime': self.blocked_by_regime
                }
            }, f, indent=2)
        
        print(f"\n💾 日誌已保存: {log_file}")


async def main():
    """主函數"""
    simulator = LiveTradingSimulator()
    
    # 運行 5 分鐘模擬
    await simulator.run(duration_minutes=5)


if __name__ == "__main__":
    asyncio.run(main())
