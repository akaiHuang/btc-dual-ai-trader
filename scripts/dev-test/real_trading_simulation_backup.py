#!/usr/bin/env python3
"""
Task 1.6.1 - Phase C: 真實市場交易模擬測試
使用真實 Binance WebSocket 數據，模擬交易執行，計算獲利成功率
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
import websockets

from src.exchange.obi_calculator import OBICalculator
from src.exchange.signed_volume_tracker import SignedVolumeTracker
from src.exchange.vpin_calculator import VPINCalculator
from src.exchange.spread_depth_monitor import SpreadDepthMonitor
from src.strategy.layered_trading_engine import LayeredTradingEngine


class Position:
    """持倉追蹤（含手續費計算）"""
    
    # 交易參數
    MAKER_FEE = 0.0002  # 0.02% Maker 手續費
    TAKER_FEE = 0.0005  # 0.05% Taker 手續費
    FUNDING_RATE_HOURLY = 0.00003  # 約 0.003% 每小時資金費率（年化約 26%）
    
    def __init__(self, entry_price: float, direction: str, size: float, 
                 leverage: float, stop_loss: float, take_profit: float, 
                 timestamp: float, capital: float = 100.0):
        self.entry_price = entry_price
        self.direction = direction
        self.size = size  # 倉位比例 (0-1)
        self.leverage = leverage
        self.stop_loss_pct = stop_loss
        self.take_profit_pct = take_profit
        self.timestamp = timestamp
        self.entry_time = datetime.fromtimestamp(timestamp / 1000)
        self.capital = capital  # 本金（USDT）
        
        # 計算實際倉位值
        self.position_value = capital * size * leverage  # 實際控制的資產價值
        self.position_size_btc = self.position_value / entry_price  # BTC 數量
        
        # 進場手續費（使用 TAKER_FEE，因為是市價單）
        self.entry_fee = self.position_value * self.TAKER_FEE
        
        self.exit_price: Optional[float] = None
        self.exit_time: Optional[datetime] = None
        self.exit_fee: Optional[float] = None
        self.funding_fee: Optional[float] = None
        self.pnl_usdt: Optional[float] = None
        self.pnl_pct: Optional[float] = None
        self.exit_reason: Optional[str] = None
        
    def check_exit(self, current_price: float) -> Optional[tuple]:
        """檢查是否觸發止損或止盈"""
        # 計算未實現盈虧（價格變動百分比 × 槓桿）
        if self.direction == "LONG":
            price_change_pct = ((current_price - self.entry_price) / self.entry_price) * 100
            pnl_pct = price_change_pct * self.leverage
            
            # 止損
            if pnl_pct <= -self.stop_loss_pct:
                return ("STOP_LOSS", pnl_pct)
            
            # 止盈
            if pnl_pct >= self.take_profit_pct:
                return ("TAKE_PROFIT", pnl_pct)
                
        else:  # SHORT
            price_change_pct = ((self.entry_price - current_price) / self.entry_price) * 100
            pnl_pct = price_change_pct * self.leverage
            
            # 止損
            if pnl_pct <= -self.stop_loss_pct:
                return ("STOP_LOSS", pnl_pct)
            
            # 止盈
            if pnl_pct >= self.take_profit_pct:
                return ("TAKE_PROFIT", pnl_pct)
        
        return None
    
    def close(self, exit_price: float, reason: str, timestamp: float):
        """平倉並計算所有費用"""
        self.exit_price = exit_price
        self.exit_time = datetime.fromtimestamp(timestamp / 1000)
        self.exit_reason = reason
        
        # 1. 計算持倉時間（小時）
        holding_hours = (timestamp - self.timestamp) / 1000 / 3600
        
        # 2. 計算資金費率（每小時收費）
        self.funding_fee = self.position_value * self.FUNDING_RATE_HOURLY * holding_hours
        
        # 3. 計算出場手續費（使用 TAKER_FEE）
        exit_value = self.position_size_btc * exit_price
        self.exit_fee = exit_value * self.TAKER_FEE
        
        # 4. 計算價格變動盈虧（USDT）
        if self.direction == "LONG":
            price_pnl = (exit_price - self.entry_price) * self.position_size_btc
        else:  # SHORT
            price_pnl = (self.entry_price - exit_price) * self.position_size_btc
        
        # 5. 總盈虧（USDT） = 價格盈虧 - 進場手續費 - 出場手續費 - 資金費率
        self.pnl_usdt = price_pnl - self.entry_fee - self.exit_fee - self.funding_fee
        
        # 6. 盈虧百分比（相對於使用的本金）
        used_capital = self.capital * self.size
        self.pnl_pct = (self.pnl_usdt / used_capital) * 100
    
    def get_unrealized_pnl(self, current_price: float) -> tuple:
        """獲取未實現盈虧（USDT 和 百分比）"""
        # 價格變動盈虧
        if self.direction == "LONG":
            price_pnl = (current_price - self.entry_price) * self.position_size_btc
        else:
            price_pnl = (self.entry_price - current_price) * self.position_size_btc
        
        # 預估持倉時間和資金費率
        holding_hours = (time.time() * 1000 - self.timestamp) / 1000 / 3600
        estimated_funding = self.position_value * self.FUNDING_RATE_HOURLY * holding_hours
        
        # 未實現盈虧（已扣除進場費用，預估出場費用和資金費率）
        exit_value = self.position_size_btc * current_price
        estimated_exit_fee = exit_value * self.TAKER_FEE
        
        unrealized_pnl_usdt = price_pnl - self.entry_fee - estimated_exit_fee - estimated_funding
        
        # 百分比
        used_capital = self.capital * self.size
        unrealized_pnl_pct = (unrealized_pnl_usdt / used_capital) * 100
        
        return unrealized_pnl_usdt, unrealized_pnl_pct


class RealTradingSimulator:
    """真實市場交易模擬器"""
    
    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol.upper()
        
        # Phase B 指標
        self.obi_calculator = OBICalculator(symbol=symbol)
        self.volume_tracker = SignedVolumeTracker(symbol=symbol, window_size=50)
        self.vpin_calculator = VPINCalculator(symbol=symbol, bucket_size=50, num_buckets=50)
        self.spread_monitor = SpreadDepthMonitor(symbol=symbol)
        
        # Phase C 決策引擎
        self.trading_engine = LayeredTradingEngine()
        
        # 市場數據
        self.latest_price = 0.0
        self.latest_orderbook = None
        self.orderbook_timestamp = 0
        
        # 交易追蹤
        self.open_position: Optional[Position] = None
        self.closed_positions: List[Position] = []
        self.decisions_log: List[Dict] = []
        
        # 統計
        self.total_decisions = 0
        self.trade_signals = 0
        self.trades_executed = 0
        self.blocked_by_regime = 0
        
        # 數據收集狀態
        self.warmup_complete = False
        self.min_warmup_trades = 50  # 至少需要50筆交易熱身
        
        # 輸出文件
        self.output_file = None
        
    def process_orderbook(self, data: Dict):
        """處理訂單簿數據"""
        try:
            bids = [[float(p), float(q)] for p, q in data['bids']]
            asks = [[float(p), float(q)] for p, q in data['asks']]
            
            if not bids or not asks:
                return
            
            # 更新價格
            mid_price = (bids[0][0] + asks[0][0]) / 2
            self.latest_price = mid_price
            self.latest_orderbook = {'bids': bids, 'asks': asks}
            self.orderbook_timestamp = data.get('E', time.time() * 1000)
            
            # 更新 Spread & Depth
            self.spread_monitor.update(bids, asks)
            
        except Exception as e:
            print(f"⚠️  訂單簿處理錯誤: {e}")
    
    def process_trade(self, data: Dict):
        """處理成交數據"""
        try:
            # 添加到 Signed Volume Tracker
            self.volume_tracker.add_trade(data)
            
            # 添加到 VPIN Calculator
            self.vpin_calculator.process_trade(data)
            
            # 檢查是否完成熱身
            if not self.warmup_complete:
                if self.volume_tracker.stats['total_trades'] >= self.min_warmup_trades:
                    self.warmup_complete = True
                    print(f"✅ 數據熱身完成（{self.volume_tracker.stats['total_trades']} 筆交易）\n")
            
        except Exception as e:
            print(f"⚠️  成交數據處理錯誤: {e}")
    
    def get_market_data(self) -> Optional[Dict]:
        """獲取當前市場數據"""
        try:
            if not self.warmup_complete:
                return None
            
            if self.latest_price == 0 or not self.latest_orderbook:
                return None
            
            # 解析訂單簿
            bids = self.latest_orderbook['bids']
            asks = self.latest_orderbook['asks']
            
            # 計算 OBI
            obi = self.obi_calculator.calculate_obi(bids, asks)
            
            # 計算 Microprice
            microprice_data = self.obi_calculator.calculate_microprice(bids, asks)
            if not microprice_data:
                return None
            
            microprice = microprice_data['microprice']
            microprice_pressure = microprice_data['pressure']
            
            # 計算 OBI velocity（變化率）
            if not hasattr(self, 'obi_history'):
                self.obi_history = deque(maxlen=10)
            self.obi_history.append(obi)
            
            obi_velocity = 0.0
            if len(self.obi_history) >= 2:
                obi_velocity = self.obi_history[-1] - self.obi_history[-2]
            
            # 計算 Signed Volume
            signed_vol = self.volume_tracker.calculate_signed_volume(window=20)
            if signed_vol is None:
                return None
            
            # VPIN
            vpin = self.vpin_calculator.get_current_vpin()
            if vpin is None:
                vpin = 0.3  # 默認值
            
            # 計算 Spread
            spread_data = self.spread_monitor.calculate_spread(bids, asks)
            if not spread_data:
                return None
            
            # 計算 Depth
            depth_data = self.spread_monitor.calculate_depth(bids, asks)
            if not depth_data:
                return None
            
            # 整合市場數據
            market_data = {
                # Signal Layer
                'obi': float(obi),
                'obi_velocity': float(obi_velocity),
                'signed_volume': float(signed_vol),
                'microprice_pressure': float(microprice_pressure),
                
                # Regime Layer
                'vpin': float(vpin),
                'spread_bps': float(spread_data['spread_bps']),
                'total_depth': float(depth_data['total_depth']),
                'depth_imbalance': float(depth_data['depth_imbalance']),
                
                # 價格
                'price': self.latest_price,
                'timestamp': self.orderbook_timestamp
            }
            
            return market_data
            
        except Exception as e:
            print(f"⚠️  市場數據獲取錯誤: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def make_decision(self, market_data: Dict, verbose: bool = True):
        """做出交易決策"""
        self.total_decisions += 1
        
        # 使用 Phase C 決策引擎
        decision = self.trading_engine.process_market_data(market_data)
        
        # 記錄決策
        log_entry = {
            'timestamp': market_data['timestamp'],
            'datetime': datetime.fromtimestamp(market_data['timestamp'] / 1000).isoformat(),
            'price': market_data['price'],
            'decision': decision,
            'market_data': market_data
        }
        self.decisions_log.append(log_entry)
        
        # 統計
        signal_direction = decision['signal']['direction']
        if signal_direction in ['LONG', 'SHORT']:
            self.trade_signals += 1
        
        can_trade = decision['can_trade']
        if not can_trade and signal_direction in ['LONG', 'SHORT']:
            self.blocked_by_regime += 1
        
        # 顯示決策（每次都顯示詳細資訊）
        if not verbose:
            return
        
        timestamp = datetime.fromtimestamp(market_data['timestamp'] / 1000).strftime('%H:%M:%S')
        
        signal_display = "⚖️  NEUTRAL"
        if signal_direction == "LONG":
            signal_display = "📈 LONG"
        elif signal_direction == "SHORT":
            signal_display = "📉 SHORT"
        
        signal_confidence = decision['signal']['confidence']
        
        risk_level = decision['regime']['risk_level']
        can_trade = decision['can_trade']
        risk_display = f"{'🟢' if can_trade else '🔴'} {risk_level}"
        
        print(f"\n{'='*70}")
        print(f"[{timestamp}] 決策 #{self.total_decisions}")
        print(f"{'='*70}")
        print(f"💰 當前價格: ${market_data['price']:.2f}")
        
        # 持倉狀態
        if self.open_position:
            pos = self.open_position
            unrealized_pnl_usdt, unrealized_pnl_pct = pos.get_unrealized_pnl(market_data['price'])
            holding_minutes = (market_data['timestamp'] - pos.timestamp) / 1000 / 60
            print(f"� 持倉狀態: {pos.direction}")
            print(f"   進場價格: ${pos.entry_price:.2f}")
            print(f"   當前盈虧: {unrealized_pnl_usdt:+.4f} USDT ({unrealized_pnl_pct:+.2f}%)")
            print(f"   持倉時間: {holding_minutes:.1f} 分鐘")
            print(f"   止損線: -{pos.stop_loss_pct:.2f}% | 止盈線: +{pos.take_profit_pct:.2f}%")
        else:
            print(f"📊 持倉狀態: 空倉")
        
        print(f"\n🎯 交易信號:")
        print(f"   方向: {signal_display}")
        print(f"   信心度: {signal_confidence:.3f}")
        print(f"   風險等級: {risk_display}")
        
        # 顯示市場指標
        print(f"\n📈 市場指標:")
        print(f"   OBI (訂單簿失衡): {market_data['obi']:>7.4f}")
        print(f"   OBI Velocity (變化率): {market_data['obi_velocity']:>7.4f}")
        print(f"   Signed Volume (淨量): {market_data['signed_volume']:>7.2f}")
        print(f"   VPIN (毒性): {market_data['vpin']:.3f}")
        print(f"   Spread (價差): {market_data['spread_bps']:>6.2f} bps")
        print(f"   Depth (深度): {market_data['total_depth']:.2f} BTC")
        
        # 如果被阻擋，顯示原因
        if not can_trade and signal_direction in ['LONG', 'SHORT']:
            blocked_reasons = decision['regime']['blocked_reasons']
            print(f"\n🚫 交易被阻擋:")
            for reason in blocked_reasons:
                print(f"   • {reason}")
        
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
                duration = (timestamp - self.open_position.timestamp) / 1000 / 60
                print(f"   持倉時間: {duration:.1f} 分鐘")
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
                
                print(f"\n{'='*70}")
                print(f"🚀 開倉 #{self.trades_executed} [{execution['style'].upper()}]")
                print(f"{'='*70}")
                print(f"📍 基本資訊:")
                print(f"   方向: {signal_direction}")
                print(f"   進場價格: ${current_price:.2f}")
                print(f"   進場時間: {datetime.fromtimestamp(timestamp/1000).strftime('%Y-%m-%d %H:%M:%S')}")
                
                print(f"\n💰 資金配置:")
                print(f"   本金: {position.capital:.2f} USDT")
                print(f"   倉位比例: {execution['position_size']*100:.1f}%")
                print(f"   使用資金: {position.capital * execution['position_size']:.2f} USDT")
                print(f"   槓桿倍數: {execution['leverage']:.1f}x")
                print(f"   控制資產: {position.position_value:.2f} USDT")
                print(f"   BTC 數量: {position.position_size_btc:.6f} BTC")
                
                print(f"\n💸 費用明細:")
                print(f"   進場手續費: {position.entry_fee:.4f} USDT ({Position.TAKER_FEE*100:.2f}%)")
                print(f"   資金費率: {Position.FUNDING_RATE_HOURLY*100:.3f}%/小時")
                print(f"   預估費用: ~{position.position_value * Position.FUNDING_RATE_HOURLY:.4f} USDT/小時")
                
                print(f"\n🎯 風控設定:")
                print(f"   止損: -{execution['stop_loss_pct']:.2f}%")
                print(f"   止盈: +{execution['take_profit_pct']:.2f}%")
                print(f"   信心度: {decision['signal']['confidence']:.3f}")
                
                print(f"\n📊 預期收益:")
                used_capital = position.capital * execution['position_size']
                expected_profit = used_capital * execution['take_profit_pct'] / 100
                expected_loss = used_capital * execution['stop_loss_pct'] / 100
                print(f"   止盈收益: +{expected_profit:.2f} USDT")
                print(f"   止損虧損: -{expected_loss:.2f} USDT")
                print(f"   風險收益比: 1:{execution['take_profit_pct']/execution['stop_loss_pct']:.2f}")
                print(f"{'='*70}")
    
    def close_position(self, exit_price: float, reason: str, timestamp: float):
        """平倉"""
        if self.open_position:
            pos = self.open_position
            pos.close(exit_price, reason, timestamp)
            
            # 計算持倉時間
            holding_time = (pos.exit_time - pos.entry_time).total_seconds()
            holding_minutes = holding_time / 60
            holding_hours = holding_time / 3600
            
            # 計算價格變動
            if pos.direction == "LONG":
                price_change_pct = ((exit_price - pos.entry_price) / pos.entry_price) * 100
            else:
                price_change_pct = ((pos.entry_price - exit_price) / pos.entry_price) * 100
            
            # 顯示平倉詳細資訊
            print(f"\n{'='*70}")
            print(f"🔔 平倉 #{len(self.closed_positions)+1} [{reason}]")
            print(f"{'='*70}")
            print(f"📍 基本資訊:")
            print(f"   方向: {pos.direction}")
            print(f"   進場價格: ${pos.entry_price:.2f}")
            print(f"   出場價格: ${exit_price:.2f}")
            print(f"   價格變動: {price_change_pct:+.4f}%")
            print(f"   持倉時間: {holding_minutes:.1f} 分鐘 ({holding_hours:.2f} 小時)")
            
            print(f"\n💰 倉位明細:")
            used_capital = pos.capital * pos.size
            print(f"   本金: {pos.capital:.2f} USDT")
            print(f"   使用資金: {used_capital:.2f} USDT ({pos.size*100:.1f}%)")
            print(f"   槓桿倍數: {pos.leverage:.1f}x")
            print(f"   控制資產: {pos.position_value:.2f} USDT")
            print(f"   BTC 數量: {pos.position_size_btc:.6f} BTC")
            
            print(f"\n💸 費用明細:")
            print(f"   進場手續費: {pos.entry_fee:.4f} USDT")
            print(f"   出場手續費: {pos.exit_fee:.4f} USDT")
            print(f"   資金費率: {pos.funding_fee:.4f} USDT ({holding_hours:.2f}h)")
            total_fees = pos.entry_fee + pos.exit_fee + pos.funding_fee
            print(f"   總費用: {total_fees:.4f} USDT ({total_fees/used_capital*100:.3f}%)")
            
            print(f"\n📊 盈虧結算:")
            # 價格盈虧
            if pos.direction == "LONG":
                price_pnl = (exit_price - pos.entry_price) * pos.position_size_btc
            else:
                price_pnl = (pos.entry_price - exit_price) * pos.position_size_btc
            
            print(f"   價格盈虧: {price_pnl:+.4f} USDT")
            print(f"   扣除費用: -{total_fees:.4f} USDT")
            print(f"   淨盈虧: {pos.pnl_usdt:+.4f} USDT")
            print(f"   投資報酬率: {pos.pnl_pct:+.2f}%")
            
            # 更新資金
            print(f"\n💵 資金變化:")
            old_capital = pos.capital
            new_capital = old_capital + pos.pnl_usdt
            print(f"   平倉前: {old_capital:.2f} USDT")
            print(f"   平倉後: {new_capital:.2f} USDT")
            print(f"   變動: {pos.pnl_usdt:+.4f} USDT ({pos.pnl_pct:+.2f}%)")
            print(f"{'='*70}")
            
            # 記錄到已平倉列表
            self.closed_positions.append(pos)
            print(f"\n   收益明細:")
            
            # 計算毛利（價格變動）
            if pos.direction == "LONG":
                price_change_pct = ((exit_price - pos.entry_price) / pos.entry_price) * 100
            else:
                price_change_pct = ((pos.entry_price - exit_price) / pos.entry_price) * 100
            
            gross_pnl = (exit_price - pos.entry_price) * pos.position_size_btc if pos.direction == "LONG" else (pos.entry_price - exit_price) * pos.position_size_btc
            
            print(f"   - 毛利: {gross_pnl:+.4f} USDT ({price_change_pct:+.2f}%)")
            print(f"   - 進場費: -{pos.entry_fee:.4f} USDT")
            print(f"   - 出場費: -{pos.exit_fee:.4f} USDT")
            print(f"   - 資金費: -{pos.funding_fee:.4f} USDT")
            print(f"   - 淨利: {pos.pnl_usdt:+.4f} USDT ({pos.pnl_pct:+.2f}%)")
            
            self.closed_positions.append(pos)
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
            pnl_usdt, pnl_pct = self.open_position.get_unrealized_pnl(self.latest_price)
            holding_time = (time.time() * 1000 - self.open_position.timestamp) / 1000 / 60
            print(f"   方向:         {self.open_position.direction}")
            print(f"   進場價:       ${self.open_position.entry_price:.2f}")
            print(f"   當前價:       ${self.latest_price:.2f}")
            print(f"   未實現盈虧:   {pnl_usdt:+.4f} USDT ({pnl_pct:+.2f}%)")
            print(f"   持倉時間:     {holding_time:.1f} 分鐘")
        
        # 已平倉交易統計
        if self.closed_positions:
            print(f"\n💰 已平倉交易:")
            print(f"   交易筆數:     {len(self.closed_positions)}")
            
            winning_trades = [p for p in self.closed_positions if p.pnl_usdt > 0]
            losing_trades = [p for p in self.closed_positions if p.pnl_usdt <= 0]
            
            win_rate = len(winning_trades) / len(self.closed_positions) * 100
            print(f"   ✅ 勝率:         {win_rate:.1f}% ({len(winning_trades)}/{len(self.closed_positions)})")
            
            # 總計（USDT 和 百分比）
            total_pnl_usdt = sum(p.pnl_usdt for p in self.closed_positions)
            total_pnl_pct = sum(p.pnl_pct for p in self.closed_positions)
            total_fees = sum(p.entry_fee + p.exit_fee + p.funding_fee for p in self.closed_positions)
            
            print(f"   總淨利:       {total_pnl_usdt:+.4f} USDT ({total_pnl_pct:+.2f}%)")
            print(f"   總手續費:     -{total_fees:.4f} USDT")
            
            avg_pnl_usdt = total_pnl_usdt / len(self.closed_positions)
            avg_pnl_pct = total_pnl_pct / len(self.closed_positions)
            print(f"   平均盈虧:     {avg_pnl_usdt:+.4f} USDT ({avg_pnl_pct:+.2f}%)")
            
            if winning_trades:
                avg_win_usdt = sum(p.pnl_usdt for p in winning_trades) / len(winning_trades)
                avg_win_pct = sum(p.pnl_pct for p in winning_trades) / len(winning_trades)
                max_win_usdt = max(p.pnl_usdt for p in winning_trades)
                max_win_pct = max(p.pnl_pct for p in winning_trades)
                print(f"   平均獲利:     +{avg_win_usdt:.4f} USDT (+{avg_win_pct:.2f}%)")
                print(f"   最大獲利:     +{max_win_usdt:.4f} USDT (+{max_win_pct:.2f}%)")
            
            if losing_trades:
                avg_loss_usdt = sum(p.pnl_usdt for p in losing_trades) / len(losing_trades)
                avg_loss_pct = sum(p.pnl_pct for p in losing_trades) / len(losing_trades)
                max_loss_usdt = min(p.pnl_usdt for p in losing_trades)
                max_loss_pct = min(p.pnl_pct for p in losing_trades)
                print(f"   平均虧損:     {avg_loss_usdt:.4f} USDT ({avg_loss_pct:.2f}%)")
                print(f"   最大虧損:     {max_loss_usdt:.4f} USDT ({max_loss_pct:.2f}%)")
            
            # 詳細交易列表
            print(f"\n📋 交易明細:")
            for i, pos in enumerate(self.closed_positions, 1):
                duration = (pos.exit_time.timestamp() - pos.timestamp / 1000) / 60
                total_fee = pos.entry_fee + pos.exit_fee + pos.funding_fee
                print(f"   {i:2d}. {pos.direction:5s} | "
                      f"${pos.entry_price:>8.2f} → ${pos.exit_price:>8.2f} | "
                      f"{pos.pnl_usdt:>+8.4f} USDT ({pos.pnl_pct:>+7.2f}%) | "
                      f"費用: {total_fee:.4f} | "
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
    
    async def run(self, duration_minutes: int = 30):
        """運行模擬"""
        print("\n" + "="*80)
        print("🚀 Task 1.6.1 - Phase C: 真實市場交易模擬")
        print("="*80)
        
        # 指標說明
        print("\n" + "─"*80)
        print("📖 市場指標說明")
        print("─"*80)
        print("📊 OBI (Order Book Imbalance)     訂單簿失衡度 [-1, 1]")
        print("   • 正值 = 買盤強勢 | 負值 = 賣盤強勢 | 0 = 平衡")
        print("   • 越接近 ±1 代表失衡越嚴重")
        print()
        print("⚡ OBI Velocity                    OBI 變化率 (速度)")
        print("   • 正值 = 買盤增強 | 負值 = 賣盤增強")
        print("   • 絕對值越大代表變化越快")
        print()
        print("📈 Signed Volume                   淨成交量 (買-賣)")
        print("   • 正值 = 主動買單多 | 負值 = 主動賣單多")
        print("   • 反映短期買賣壓力")
        print()
        print("☠️  VPIN (Volume-Synchronized PIN)  毒性指標 [0, 1]")
        print("   • 0 = 低風險 | 1 = 高風險")
        print("   • >0.5 表示知情交易者活躍，需謹慎")
        print()
        print("💹 Spread                          買賣價差 (bps)")
        print("   • 越小 = 流動性越好 | 越大 = 流動性差")
        print()
        print("🏊 Depth                           訂單簿深度 (BTC)")
        print("   • 前5檔買賣單總量，反映市場承接力")
        print("─"*80)
        
        # 圖示說明
        print("\n" + "─"*80)
        print("🎨 圖示說明")
        print("─"*80)
        print("交易方向:  📈 LONG (做多)  |  📉 SHORT (做空)  |  ⚖️  NEUTRAL (中立)")
        print("風險等級:  🟢 SAFE (安全)  |  🟡 WARNING (警告)  |  🟠 DANGER (危險)  |  🔴 CRITICAL (嚴重)")
        print("持倉狀態:  🏦 空倉  |  📊 持倉中")
        print("開倉:      🚀 開倉  |  🔔 平倉")
        print("平倉原因:  🎯 TAKE_PROFIT (止盈)  |  🛑 STOP_LOSS (止損)  |  🔄 REVERSE_SIGNAL (反向)")
        print("─"*80)
        
        print(f"\n⏱️  測試配置:")
        print(f"   ⏰ 運行時長: {duration_minutes} 分鐘")
        print(f"   💹 交易對: {self.symbol}")
        print(f"   ⚡ 決策頻率: 每 15 秒")
        print(f"   🔥 熱身要求: {self.min_warmup_trades} 筆交易")
        
        print(f"\n💰 資金配置:")
        print(f"   💵 初始本金: 100 USDT")
        print(f"   📊 最大槓桿: 10x")
        print(f"   📐 倉位策略: 🐢 保守 30% | 🚶 中等 50% | 🏃 激進 80%")
        
        print(f"\n💸 費率設定:")
        print(f"   ✅ Maker 手續費: {Position.MAKER_FEE*100:.2f}%")
        print(f"   💳 Taker 手續費: {Position.TAKER_FEE*100:.2f}%")
        print(f"   💰 資金費率: {Position.FUNDING_RATE_HOURLY*100:.3f}%/小時")
        
        print(f"\n🎯 風控設定:")
        print(f"   🐢 保守模式: 槓桿 3x | 止損 8% | 止盈 12%")
        print(f"   🚶 中等模式: 槓桿 5x | 止損 5% | 止盈 8%")
        print(f"   🏃 激進模式: 槓桿 10x | 止損 3% | 止盈 5%")
        
        print(f"\n🔌 連接 Binance WebSocket...")
        print(f"📥 收集市場數據中（需要至少 {self.min_warmup_trades} 筆交易熱身）...\n")
        
        # WebSocket URLs
        symbol_lower = self.symbol.lower()
        depth_url = f"wss://stream.binance.com:9443/ws/{symbol_lower}@depth20@100ms"
        trade_url = f"wss://stream.binance.com:9443/ws/{symbol_lower}@aggTrade"
        
        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)
        
        last_decision_time = 0
        decision_interval = 15  # 15秒做一次決策
        
        last_status_time = start_time
        status_interval = 300  # 5分鐘輸出一次狀態摘要
        
        try:
            async with websockets.connect(depth_url) as depth_ws, \
                       websockets.connect(trade_url) as trade_ws:
                
                print("✅ WebSocket 已連接\n")
                
                while time.time() < end_time:
                    # 接收數據
                    try:
                        # 同時監聽兩個 WebSocket
                        done, pending = await asyncio.wait(
                            [
                                asyncio.create_task(depth_ws.recv()),
                                asyncio.create_task(trade_ws.recv())
                            ],
                            timeout=1.0,
                            return_when=asyncio.FIRST_COMPLETED
                        )
                        
                        for task in done:
                            message = task.result()
                            data = json.loads(message)
                            
                            if 'e' in data:  # Trade event
                                self.process_trade(data)
                            else:  # Orderbook
                                self.process_orderbook(data)
                        
                        # 取消未完成的任務
                        for task in pending:
                            task.cancel()
                        
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        print(f"⚠️  接收錯誤: {e}")
                        continue
                    
                    # 定期做決策
                    current_time = time.time()
                    if self.warmup_complete and current_time - last_decision_time >= decision_interval:
                        market_data = self.get_market_data()
                        if market_data:
                            self.make_decision(market_data)
                        last_decision_time = current_time
                    
                    # 定期輸出狀態
                    if current_time - last_status_time >= status_interval:
                        elapsed = (current_time - start_time) / 60
                        remaining = (end_time - current_time) / 60
                        print(f"\n⏱️  已運行: {elapsed:.1f}分鐘 | 剩餘: {remaining:.1f}分鐘")
                        print(f"📊 決策數: {self.total_decisions} | 交易數: {self.trades_executed} | 價格: ${self.latest_price:.2f}")
                        if self.open_position:
                            pnl = self.open_position.get_unrealized_pnl(self.latest_price)
                            print(f"📍 持倉: {self.open_position.direction} @ ${self.open_position.entry_price:.2f} | 盈虧: {pnl:+.2f}%")
                        last_status_time = current_time
                
        except KeyboardInterrupt:
            print("\n\n⚠️  用戶中斷")
        finally:
            # 如果還有持倉，強制平倉
            if self.open_position:
                self.close_position(self.latest_price, "SIMULATION_END", time.time() * 1000)
                print(f"\n🔔 模擬結束，強制平倉")
            
            # 輸出最終統計
            self.print_statistics()
            
            # 保存詳細日誌
            self.save_logs()
    
    def save_logs(self):
        """保存交易日誌"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"data/real_trading_log_{timestamp}.json"
        
        with open(log_file, 'w') as f:
            json.dump({
                'symbol': self.symbol,
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
                    'blocked_by_regime': self.blocked_by_regime,
                    'win_rate': len([p for p in self.closed_positions if p.pnl_pct > 0]) / len(self.closed_positions) * 100 if self.closed_positions else 0,
                    'total_pnl': sum(p.pnl_pct for p in self.closed_positions) if self.closed_positions else 0
                }
            }, f, indent=2)
        
        print(f"\n💾 日誌已保存: {log_file}")


async def main():
    """主函數"""
    import sys
    
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 1440
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    simulator = RealTradingSimulator(symbol="BTCUSDT")
    
    if output_file:
        simulator.output_file = output_file
    
    await simulator.run(duration_minutes=duration)


if __name__ == "__main__":
    asyncio.run(main())
