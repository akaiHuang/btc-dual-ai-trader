#!/usr/bin/env python3
"""
簡化版即時交易模擬 - 僅使用真實交易數據

配置:
- 啟動金: 100 USDT
- 數據源: 真實 Binance aggTrade (僅交易數據，不用訂單簿)
- 槓桿: 真實（根據策略決定）
- 費率: Taker 0.05% + Funding 0.003%/小時
"""

import asyncio
from binance import AsyncClient, BinanceSocketManager
from datetime import datetime
import time
import json
import sys
from pathlib import Path
from collections import deque

# 添加項目路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.exchange.signed_volume_tracker import SignedVolumeTracker
from src.exchange.vpin_calculator import VPINCalculator


class SimplePosition:
    """簡化持倉"""
    
    TAKER_FEE = 0.0005  # 0.05%
    FUNDING_RATE = 0.00003  # 0.003%/小時
    
    def __init__(self, entry_price: float, direction: str, size: float, 
                 leverage: float, stop_loss_pct: float, take_profit_pct: float,
                 timestamp: float, capital: float = 100.0):
        self.entry_price = entry_price
        self.direction = direction
        self.size = size  # 倉位比例 (0-1)
        self.leverage = leverage
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.timestamp = timestamp
        self.capital = capital
        
        # 實際控制資產
        self.position_value = capital * size * leverage
        self.entry_fee = self.position_value * self.TAKER_FEE
        
    def check_exit(self, current_price: float):
        """檢查止損/止盈"""
        if self.direction == "LONG":
            price_change = ((current_price - self.entry_price) / self.entry_price) * 100
            pnl_pct = price_change * self.leverage
        else:  # SHORT
            price_change = ((self.entry_price - current_price) / self.entry_price) * 100
            pnl_pct = price_change * self.leverage
        
        if pnl_pct <= -self.stop_loss_pct:
            return ("STOP_LOSS", pnl_pct)
        if pnl_pct >= self.take_profit_pct:
            return ("TAKE_PROFIT", pnl_pct)
        
        return None
    
    def close(self, exit_price: float, reason: str, timestamp: float):
        """平倉"""
        holding_hours = (timestamp - self.timestamp) / 1000 / 3600
        funding_fee = self.position_value * self.FUNDING_RATE * holding_hours
        exit_fee = self.position_value * self.TAKER_FEE
        
        if self.direction == "LONG":
            price_pnl = ((exit_price - self.entry_price) / self.entry_price) * self.position_value
        else:
            price_pnl = ((self.entry_price - exit_price) / self.entry_price) * self.position_value
        
        total_fees = self.entry_fee + exit_fee + funding_fee
        net_pnl = price_pnl - total_fees
        
        used_capital = self.capital * self.size
        pnl_pct = (net_pnl / used_capital) * 100
        
        return {
            'entry_price': self.entry_price,
            'exit_price': exit_price,
            'direction': self.direction,
            'exit_reason': reason,
            'pnl_usdt': net_pnl,
            'pnl_pct': pnl_pct,
            'holding_hours': holding_hours,
            'total_fees': total_fees,
            'size': self.size,
            'leverage': self.leverage
        }


class SimpleTradingSimulator:
    """簡化交易模擬器 - 只用交易數據"""
    
    def __init__(self, capital: float = 100.0, duration_minutes: int = 60,
                 version: str = "original"):
        self.capital = capital
        self.initial_capital = capital
        self.duration_minutes = duration_minutes
        self.version = version  # "original" 或 "adjusted"
        
        # 策略參數
        if version == "adjusted":
            # 調整版本
            self.vpin_threshold = 0.7
            self.signal_threshold = 0.5
            self.allow_danger = True  # DANGER 允許交易
        else:
            # 原始版本
            self.vpin_threshold = 0.5
            self.signal_threshold = 0.6
            self.allow_danger = False
        
        # 指標（只用交易數據）
        self.volume_tracker = SignedVolumeTracker(window_size=20)
        self.vpin_calculator = VPINCalculator(bucket_size=50, num_buckets=50)
        
        # 狀態
        self.latest_price = 0
        self.prices = deque(maxlen=50)  # 價格歷史
        self.trade_count = 0
        self.warmup_complete = False
        
        # 持倉
        self.position = None
        self.closed_positions = []
        
        # 統計
        self.total_decisions = 0
        self.trade_signals = 0
        self.blocked_signals = 0
        self.trades_executed = 0
        
        # 時間控制
        self.start_time = time.time()
        self.last_decision_time = 0
        self.decision_interval = 15  # 15秒決策一次
        
    def process_trade(self, msg):
        """處理交易數據"""
        if msg.get('e') == 'error':
            return
        
        price = float(msg['p'])
        qty = float(msg['q'])
        is_buyer_maker = msg['m']
        
        self.latest_price = price
        self.prices.append(price)
        
        # 更新指標
        trade_dict = {
            'p': price,
            'q': qty,
            'm': is_buyer_maker
        }
        
        self.volume_tracker.add_trade(trade_dict)
        self.vpin_calculator.process_trade(trade_dict)
        
        self.trade_count += 1
        
        # 熱身
        if not self.warmup_complete and self.trade_count >= 50:
            self.warmup_complete = True
            print(f"\n✅ 熱身完成（50 筆交易）")
    
    def make_decision(self, timestamp: float):
        """簡化決策邏輯"""
        if not self.warmup_complete or len(self.prices) < 20:
            return
        
        self.total_decisions += 1
        
        # 計算指標
        signed_volume = self.volume_tracker.calculate_signed_volume()
        vpin = self.vpin_calculator.calculate_vpin()
        
        # 價格動能（簡單移動平均偏離）
        recent_prices = list(self.prices)[-20:]
        sma = sum(recent_prices) / len(recent_prices)
        price_deviation = (self.latest_price - sma) / sma
        
        # 簡單信號生成
        signal = "NEUTRAL"
        confidence = 0.0
        
        # 強買入信號: 正向成交量 + 價格上漲 + 低 VPIN
        if signed_volume > 0.1 and price_deviation > 0.001:
            signal = "LONG"
            confidence = min(abs(signed_volume) * 0.5 + abs(price_deviation) * 50, 1.0)
        
        # 強賣出信號: 負向成交量 + 價格下跌 + 低 VPIN
        elif signed_volume < -0.1 and price_deviation < -0.001:
            signal = "SHORT"
            confidence = min(abs(signed_volume) * 0.5 + abs(price_deviation) * 50, 1.0)
        
        # 風險評估
        risk_blocked = False
        if vpin > self.vpin_threshold:
            risk_blocked = True
        
        # 調整版本允許更高風險
        if self.version == "adjusted" and vpin < 0.8:
            risk_blocked = False
        
        # 輸出決策（每 4 次）
        if self.total_decisions % 4 == 1:
            signal_emoji = "📈" if signal == "LONG" else "📉" if signal == "SHORT" else "⚖️"
            risk_emoji = "🔴" if risk_blocked else "🟢"
            
            print(f"\n[{datetime.fromtimestamp(timestamp/1000).strftime('%H:%M:%S')}] 決策 #{self.total_decisions}")
            print(f"  價格: ${self.latest_price:.2f}")
            print(f"  信號: {signal_emoji} {signal} (信心: {confidence:.3f})")
            print(f"  風險: {risk_emoji} VPIN={vpin:.3f} ({'阻擋' if risk_blocked else '允許'})")
            print(f"  指標: 成交量={signed_volume:+.2f} | 偏離={price_deviation*100:+.3f}%")
            if self.position:
                current_pnl = self.get_current_pnl()
                print(f"  持倉: {self.position.direction} @ ${self.position.entry_price:.2f} | 盈虧: {current_pnl:+.2f}%")
        
        # 檢查現有持倉
        if self.position:
            exit_signal = self.position.check_exit(self.latest_price)
            if exit_signal:
                reason, pnl = exit_signal
                result = self.position.close(self.latest_price, reason, timestamp)
                self.closed_positions.append(result)
                
                # 更新資金
                self.capital += result['pnl_usdt']
                
                print(f"\n  💰 平倉: {self.position.direction} @ ${self.latest_price:.2f}")
                print(f"     原因: {reason}")
                print(f"     盈虧: {result['pnl_usdt']:+.2f} USDT ({result['pnl_pct']:+.2f}%)")
                print(f"     持倉: {result['holding_hours']:.1f}小時")
                print(f"     資金: {self.capital:.2f} USDT")
                
                self.position = None
                return
        
        # 開新倉位
        if not self.position and signal != "NEUTRAL" and confidence >= self.signal_threshold:
            if risk_blocked:
                self.blocked_signals += 1
            else:
                self.trade_signals += 1
                
                # 根據信心度決定倉位
                if confidence >= 0.7:
                    size, leverage = 0.8, 10  # 激進
                    stop_loss, take_profit = 3.0, 5.0
                elif confidence >= 0.5:
                    size, leverage = 0.5, 5   # 中等
                    stop_loss, take_profit = 5.0, 8.0
                else:
                    size, leverage = 0.3, 3   # 保守
                    stop_loss, take_profit = 8.0, 12.0
                
                self.position = SimplePosition(
                    entry_price=self.latest_price,
                    direction=signal,
                    size=size,
                    leverage=leverage,
                    stop_loss_pct=stop_loss,
                    take_profit_pct=take_profit,
                    timestamp=timestamp,
                    capital=self.capital
                )
                
                self.trades_executed += 1
                
                print(f"\n  🔔 開倉: {signal} @ ${self.latest_price:.2f}")
                print(f"     倉位: {size*100:.0f}% × {leverage}x = {size*leverage*100:.0f}% 敞口")
                print(f"     止損: -{stop_loss:.1f}% | 止盈: +{take_profit:.1f}%")
    
    def get_current_pnl(self):
        """獲取當前持倉盈虧百分比"""
        if not self.position:
            return 0.0
        
        if self.position.direction == "LONG":
            price_change = ((self.latest_price - self.position.entry_price) / self.position.entry_price) * 100
        else:
            price_change = ((self.position.entry_price - self.latest_price) / self.position.entry_price) * 100
        
        return price_change * self.position.leverage
    
    async def run(self, output_file: str = None):
        """運行模擬"""
        print("="*60)
        print(f"🚀 簡化交易模擬 ({self.version.upper()})")
        print("="*60)
        print(f"💰 啟動金: {self.capital} USDT")
        print(f"⏱️  時長: {self.duration_minutes} 分鐘")
        print(f"📊 數據源: 真實 Binance aggTrade (無訂單簿)")
        print(f"⚙️  參數: VPIN {self.vpin_threshold} | 信號 {self.signal_threshold}")
        print(f"💸 費率: Taker 0.05% | Funding 0.003%/hr")
        print()
        
        client = await AsyncClient.create()
        bsm = BinanceSocketManager(client)
        trade_socket = bsm.aggtrade_socket('BTCUSDT')
        
        last_report = time.time()
        
        try:
            print("🔌 連接 Binance WebSocket...")
            
            async with trade_socket as ts:
                print("✅ 已連接\n")
                
                while True:
                    elapsed = (time.time() - self.start_time) / 60
                    if elapsed >= self.duration_minutes:
                        break
                    
                    # 接收交易
                    try:
                        msg = await asyncio.wait_for(ts.recv(), timeout=1.0)
                        self.process_trade(msg)
                    except asyncio.TimeoutError:
                        continue
                    
                    # 決策
                    current_time = time.time()
                    if self.warmup_complete and current_time - self.last_decision_time >= self.decision_interval:
                        self.make_decision(datetime.now().timestamp() * 1000)
                        self.last_decision_time = current_time
                    
                    # 每 2 分鐘報告
                    if current_time - last_report >= 120:
                        remaining = self.duration_minutes - elapsed
                        roi = ((self.capital - self.initial_capital) / self.initial_capital) * 100
                        print(f"\n⏱️  已運行: {elapsed:.1f}分 | 剩餘: {remaining:.1f}分")
                        print(f"📊 決策: {self.total_decisions} | 執行: {self.trades_executed} | 完成: {len(self.closed_positions)}")
                        print(f"💰 資金: {self.capital:.2f} USDT | ROI: {roi:+.2f}%")
                        last_report = current_time
        
        except KeyboardInterrupt:
            print("\n\n⚠️  用戶中斷")
        finally:
            # 強制平倉
            if self.position:
                result = self.position.close(self.latest_price, "FORCE_CLOSE", 
                                             datetime.now().timestamp() * 1000)
                self.closed_positions.append(result)
                self.capital += result['pnl_usdt']
                print(f"\n🔔 強制平倉: {result['pnl_usdt']:+.2f} USDT")
            
            await client.close_connection()
            
            # 輸出統計
            self.print_statistics()
            
            # 保存結果
            if output_file:
                self.save_results(output_file)
    
    def print_statistics(self):
        """輸出統計"""
        print("\n" + "="*60)
        print(f"📊 測試統計 ({self.version.upper()})")
        print("="*60)
        print()
        print(f"📈 決策統計:")
        print(f"   總決策: {self.total_decisions}")
        print(f"   信號: {self.trade_signals} ({self.trade_signals/max(self.total_decisions,1)*100:.1f}%)")
        print(f"   阻擋: {self.blocked_signals}")
        print(f"   執行: {self.trades_executed}")
        print()
        
        if self.closed_positions:
            wins = [p for p in self.closed_positions if p['pnl_usdt'] > 0]
            losses = [p for p in self.closed_positions if p['pnl_usdt'] <= 0]
            
            total_pnl = sum(p['pnl_usdt'] for p in self.closed_positions)
            total_pnl_pct = (total_pnl / self.initial_capital) * 100
            
            print(f"💰 交易績效:")
            print(f"   完成: {len(self.closed_positions)} 筆")
            print(f"   勝率: {len(wins)/len(self.closed_positions)*100:.1f}% ({len(wins)}/{len(self.closed_positions)})")
            print(f"   總盈虧: {total_pnl:+.2f} USDT ({total_pnl_pct:+.2f}%)")
            print(f"   最終資金: {self.capital:.2f} USDT")
            print(f"   ROI: {((self.capital - self.initial_capital) / self.initial_capital * 100):+.2f}%")
            
            if wins:
                avg_win = sum(p['pnl_usdt'] for p in wins) / len(wins)
                max_win = max(p['pnl_usdt'] for p in wins)
                print(f"   平均獲利: +{avg_win:.2f} USDT")
                print(f"   最大獲利: +{max_win:.2f} USDT")
            
            if losses:
                avg_loss = sum(p['pnl_usdt'] for p in losses) / len(losses)
                max_loss = min(p['pnl_usdt'] for p in losses)
                print(f"   平均虧損: {avg_loss:.2f} USDT")
                print(f"   最大虧損: {max_loss:.2f} USDT")
            
            # 風險指標
            total_fees = sum(p['total_fees'] for p in self.closed_positions)
            avg_holding = sum(p['holding_hours'] for p in self.closed_positions) / len(self.closed_positions)
            print(f"\n   總費用: {total_fees:.2f} USDT")
            print(f"   平均持倉: {avg_holding:.2f} 小時")
        else:
            print("⚠️  沒有完成的交易")
        print()
    
    def save_results(self, filename: str):
        """保存結果"""
        results = {
            'version': self.version,
            'timestamp': datetime.now().isoformat(),
            'parameters': {
                'initial_capital': self.initial_capital,
                'final_capital': self.capital,
                'vpin_threshold': self.vpin_threshold,
                'signal_threshold': self.signal_threshold,
                'allow_danger': self.allow_danger
            },
            'statistics': {
                'total_decisions': self.total_decisions,
                'trade_signals': self.trade_signals,
                'blocked_signals': self.blocked_signals,
                'trades_executed': self.trades_executed,
                'trades_completed': len(self.closed_positions)
            },
            'closed_positions': self.closed_positions
        }
        
        with open(filename, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"✅ 結果已保存: {filename}")


async def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    version = sys.argv[2] if len(sys.argv) > 2 else "original"
    output_file = sys.argv[3] if len(sys.argv) > 3 else None
    
    simulator = SimpleTradingSimulator(
        capital=100.0,
        duration_minutes=duration,
        version=version
    )
    
    await simulator.run(output_file)


if __name__ == "__main__":
    asyncio.run(main())
