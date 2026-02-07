#!/usr/bin/env python3
"""
Task 1.6.1 - Phase C: 快速交易模擬測試
使用模擬市場數據測試交易表現和獲利率
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import random
import time
from datetime import datetime
from typing import Dict, List, Optional
import numpy as np

from src.strategy.layered_trading_engine import LayeredTradingEngine


class Position:
    """持倉追蹤"""
    def __init__(self, entry_price: float, direction: str, size: float, 
                 leverage: float, stop_loss: float, take_profit: float, 
                 timestamp: float):
        self.entry_price = entry_price
        self.direction = direction
        self.size = size
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
            if pnl_pct <= -self.stop_loss_pct:
                return ("STOP_LOSS", pnl_pct)
            if pnl_pct >= self.take_profit_pct:
                return ("TAKE_PROFIT", pnl_pct)
        else:  # SHORT
            pnl_pct = ((self.entry_price - current_price) / self.entry_price) * 100 * self.leverage
            if pnl_pct <= -self.stop_loss_pct:
                return ("STOP_LOSS", pnl_pct)
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


class SimulatedMarket:
    """模擬市場價格和微觀結構"""
    
    def __init__(self, initial_price: float = 90000.0):
        self.price = initial_price
        self.time_step = 0
        
    def next_tick(self) -> Dict:
        """生成下一個時間步的市場數據"""
        self.time_step += 1
        
        # 模擬價格隨機漫步 + 更強趨勢
        trend = np.sin(self.time_step / 30) * 0.0005  # 更強的振盪趨勢
        noise = random.gauss(0, 0.0004)  # 稍大噪音
        price_change = trend + noise
        self.price *= (1 + price_change)
        
        # 模擬市場微觀結構指標
        # 根據當前趨勢生成相關的指標，增強信號強度
        momentum = np.clip(trend * 200, -0.9, 0.9)  # 更強的動量
        
        # OBI: 基於動量，更極端
        obi = momentum + random.gauss(0, 0.15)
        obi = np.clip(obi, -1, 1)
        
        # OBI Velocity: OBI 變化率，更明顯
        obi_velocity = (obi - getattr(self, '_last_obi', 0)) * 8
        obi_velocity = np.clip(obi_velocity, -0.3, 0.3)
        self._last_obi = obi
        
        # Signed Volume: 跟隨 OBI，更強烈
        signed_volume = obi * 80 + random.gauss(0, 15)
        
        # Microprice Pressure: 跟隨動量，更極端
        microprice_pressure = momentum * 1.5 + random.gauss(0, 0.2)
        microprice_pressure = np.clip(microprice_pressure, -1, 1)
        
        # VPIN: 降低風險頻率，使更多交易可以執行
        if random.random() < 0.02:  # 只有 2% 機率高 VPIN
            vpin = random.uniform(0.6, 0.9)
        else:
            vpin = random.uniform(0.1, 0.35)  # 更低的平均 VPIN
        
        # Spread: 更緊，降低風險阻擋
        if random.random() < 0.05:  # 只有 5% 機率寬價差
            spread_bps = random.uniform(12, 20)
        else:
            spread_bps = random.uniform(2, 6)  # 更緊的價差
        
        # Depth: 更充足，降低風險阻擋
        if random.random() < 0.05:  # 只有 5% 機率低深度
            total_depth = random.uniform(3, 4.5)
        else:
            total_depth = random.uniform(10, 25)  # 更高的深度
        
        # Depth Imbalance: 跟隨 OBI，更極端
        depth_imbalance = obi * 0.8 + random.gauss(0, 0.15)
        depth_imbalance = np.clip(depth_imbalance, -0.9, 0.9)
        
        return {
            'obi': float(obi),
            'obi_velocity': float(obi_velocity),
            'signed_volume': float(signed_volume),
            'microprice_pressure': float(microprice_pressure),
            'vpin': float(vpin),
            'spread_bps': float(spread_bps),
            'total_depth': float(total_depth),
            'depth_imbalance': float(depth_imbalance),
            'price': float(self.price),
            'timestamp': time.time() + self.time_step
        }


class QuickTradingSimulator:
    """快速交易模擬器"""
    
    def __init__(self):
        self.trading_engine = LayeredTradingEngine()
        self.market = SimulatedMarket(initial_price=90000.0)
        
        self.open_position: Optional[Position] = None
        self.closed_positions: List[Position] = []
        
        self.total_decisions = 0
        self.trade_signals = 0
        self.trades_executed = 0
        self.blocked_by_regime = 0
    
    def run_simulation(self, num_ticks: int = 500):
        """運行模擬"""
        print("="*60)
        print("🚀 Task 1.6.1 - Phase C: 快速交易模擬")
        print("="*60)
        print(f"\n⏱️  模擬步數: {num_ticks} 次（每步 = 10 秒）")
        print(f"⏱️  模擬時長: 約 {num_ticks * 10 / 60:.1f} 分鐘")
        print(f"💹 初始價格: ${self.market.price:.2f}")
        print(f"\n🔄 開始模擬...\n")
        
        start_time = time.time()
        last_update = start_time
        
        for i in range(num_ticks):
            # 獲取市場數據
            market_data = self.market.next_tick()
            
            # 做決策
            self.make_decision(market_data)
            
            # 每 10% 進度報告一次
            if (i + 1) % (num_ticks // 10) == 0:
                progress = (i + 1) / num_ticks * 100
                print(f"📊 進度: {progress:.0f}% | 價格: ${market_data['price']:.2f} | "
                      f"決策: {self.total_decisions} | 交易: {self.trades_executed}")
                
                if self.open_position:
                    pnl = self.open_position.get_unrealized_pnl(market_data['price'])
                    print(f"   持倉: {self.open_position.direction} @ ${self.open_position.entry_price:.2f} | 盈虧: {pnl:+.2f}%")
        
        # 強制平倉
        if self.open_position:
            final_price = market_data['price']
            self.close_position(final_price, "SIMULATION_END", market_data['timestamp'])
        
        # 輸出統計
        print(f"\n✅ 模擬完成")
        print(f"⏱️  耗時: {time.time() - start_time:.2f} 秒")
        self.print_statistics()
    
    def make_decision(self, market_data: Dict):
        """做出交易決策"""
        self.total_decisions += 1
        
        # 使用 Phase C 決策引擎
        decision = self.trading_engine.process_market_data(market_data)
        
        # 統計
        signal_direction = decision['signal']['direction']
        if signal_direction in ['LONG', 'SHORT']:
            self.trade_signals += 1
        
        can_trade = decision['can_trade']
        if not can_trade and signal_direction in ['LONG', 'SHORT']:
            self.blocked_by_regime += 1
        
        # 處理交易
        self.handle_trading(decision, market_data)
    
    def handle_trading(self, decision: Dict, market_data: Dict):
        """處理交易執行"""
        current_price = market_data['price']
        timestamp = market_data['timestamp']
        
        # 檢查平倉
        if self.open_position:
            # 檢查止損/止盈
            exit_result = self.open_position.check_exit(current_price)
            if exit_result:
                reason, pnl = exit_result
                self.close_position(current_price, reason, timestamp)
                return
            
            # 檢查反向信號
            signal_direction = decision['signal']['direction']
            if signal_direction != self.open_position.direction and signal_direction != 'NEUTRAL':
                self.close_position(current_price, "REVERSE_SIGNAL", timestamp)
                return
        
        # 檢查開倉
        if not self.open_position and decision['can_trade']:
            signal_direction = decision['signal']['direction']
            
            if signal_direction in ['LONG', 'SHORT']:
                execution = decision['execution']
                
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
        
        # 決策統計
        print(f"\n📈 決策統計:")
        print(f"   總決策數:     {self.total_decisions}")
        print(f"   交易信號:     {self.trade_signals} ({self.trade_signals/max(1,self.total_decisions)*100:.1f}%)")
        print(f"   風險阻擋:     {self.blocked_by_regime} ({self.blocked_by_regime/max(1,self.trade_signals)*100:.1f}% 的信號)")
        print(f"   實際執行:     {self.trades_executed} ({self.trades_executed/max(1,self.total_decisions)*100:.1f}%)")
        
        # 交易結果
        if self.closed_positions:
            print(f"\n💰 交易結果:")
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
                print(f"   平均獲利:     +{avg_win:.2f}%")
                print(f"   最大獲利:     +{max_win:.2f}%")
            
            if losing_trades:
                avg_loss = sum(p.pnl_pct for p in losing_trades) / len(losing_trades)
                max_loss = min(p.pnl_pct for p in losing_trades)
                print(f"   平均虧損:     {avg_loss:.2f}%")
                print(f"   最大虧損:     {max_loss:.2f}%")
            
            # 詳細交易列表
            print(f"\n📋 交易明細:")
            for i, pos in enumerate(self.closed_positions, 1):
                duration = (pos.exit_time.timestamp() - pos.timestamp) / 60
                print(f"   {i:2d}. {pos.direction:5s} | "
                      f"${pos.entry_price:>8.2f} → ${pos.exit_price:>8.2f} | "
                      f"{pos.pnl_pct:>+7.2f}% | "
                      f"{duration:>6.1f}分 | "
                      f"{pos.size*100:>3.0f}% @ {pos.leverage:.0f}x | "
                      f"{pos.exit_reason}")
        else:
            print(f"\n⚠️  沒有完成的交易")
        
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


def main():
    """主函數"""
    simulator = QuickTradingSimulator()
    simulator.run_simulation(num_ticks=500)  # 500 個時間步，約 83 分鐘


if __name__ == "__main__":
    main()
