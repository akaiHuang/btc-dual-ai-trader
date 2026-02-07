"""
Market Replay Engine - 市場重放引擎

從歷史數據重建真實市場環境，用於策略回測
"""

import time
import logging
from typing import Optional, Dict, List, Callable
from datetime import datetime
from collections import deque

from .historical_data_loader import HistoricalDataLoader
from ..exchange.obi_calculator import OBICalculator
from ..exchange.signed_volume_tracker import SignedVolumeTracker
from ..exchange.vpin_calculator import VPINCalculator
from ..exchange.spread_depth_monitor import SpreadDepthMonitor
from ..strategy.layered_trading_engine import LayeredTradingEngine

logger = logging.getLogger(__name__)


class Position:
    """持倉"""
    
    # 手續費結構
    MAKER_FEE = 0.0002  # 0.02%
    TAKER_FEE = 0.0005  # 0.05%
    FUNDING_RATE_HOURLY = 0.00003  # 0.003% per hour
    
    def __init__(
        self,
        entry_price: float,
        direction: str,
        size: float,
        leverage: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        timestamp: float,
        capital: float = 100.0
    ):
        self.entry_price = entry_price
        self.direction = direction  # "LONG" or "SHORT"
        self.size = size  # 倉位比例 (0-1)
        self.leverage = leverage
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.timestamp = timestamp
        self.entry_time = datetime.fromtimestamp(timestamp / 1000)
        
        # 資本和倉位計算
        self.capital = capital
        self.position_value = capital * size * leverage
        self.position_size_btc = self.position_value / entry_price
        
        # 手續費
        self.entry_fee = self.position_value * self.TAKER_FEE
        
        # 平倉信息
        self.exit_price: Optional[float] = None
        self.exit_time: Optional[datetime] = None
        self.exit_reason: Optional[str] = None
        self.exit_fee: Optional[float] = None
        self.funding_fee: Optional[float] = None
        self.pnl_usdt: Optional[float] = None
        self.pnl_pct: Optional[float] = None
    
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


class MarketReplayEngine:
    """
    市場重放引擎
    
    從歷史數據重建市場環境，執行策略回測
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        capital: float = 100.0,
        data_dir: str = "data/historical"
    ):
        """
        初始化市場重放引擎
        
        Args:
            symbol: 交易對
            capital: 初始資金 (USDT)
            data_dir: 歷史數據目錄
        """
        self.symbol = symbol
        self.capital = capital
        
        # 數據加載器
        self.data_loader = HistoricalDataLoader(data_dir)
        
        # Phase B 指標計算器
        self.obi_calculator = OBICalculator(symbol=symbol)
        self.volume_tracker = SignedVolumeTracker(symbol=symbol, window_size=50)
        self.vpin_calculator = VPINCalculator(symbol=symbol, bucket_size=50, num_buckets=50)
        self.spread_monitor = SpreadDepthMonitor(symbol=symbol)
        
        # Phase C 決策引擎
        self.trading_engine = LayeredTradingEngine()
        
        # 市場狀態
        self.latest_price = 0.0
        self.latest_orderbook = None
        self.warmup_complete = False
        self.min_warmup_trades = 50
        
        # 交易狀態
        self.open_position: Optional[Position] = None
        self.closed_positions: List[Position] = []
        
        # 統計
        self.total_decisions = 0
        self.trade_signals = 0
        self.trades_executed = 0
        self.blocked_by_regime = 0
        
        # 決策間隔（秒）
        self.decision_interval = 15
        self.last_decision_time = 0
        
        logger.info(f"初始化 MarketReplayEngine: {symbol}, 資金: {capital} USDT")
    
    def process_orderbook(self, data: Dict):
        """處理訂單簿更新"""
        self.latest_orderbook = data
        
        bids = data['bids']
        asks = data['asks']
        
        # 更新最新價格（使用中間價）
        if bids and asks:
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            self.latest_price = (best_bid + best_ask) / 2
        
        # 更新 spread 和 depth
        self.spread_monitor.update(bids, asks)
    
    def process_trade(self, data: Dict):
        """處理交易數據"""
        price = float(data['price'])
        qty = float(data['qty'])
        is_buyer_maker = data['is_buyer_maker']
        
        # 構建 trade dict
        trade = {
            'p': price,
            'q': qty,
            'm': is_buyer_maker
        }
        
        # 更新 Signed Volume
        self.volume_tracker.add_trade(trade)
        
        # 更新 VPIN
        self.vpin_calculator.process_trade(trade)
        
        # 檢查是否完成熱身
        if not self.warmup_complete:
            if self.volume_tracker.stats['total_trades'] >= self.min_warmup_trades:
                self.warmup_complete = True
                logger.info(f"✅ 熱身完成（{self.min_warmup_trades} 筆交易）")
    
    def get_market_data(self) -> Optional[Dict]:
        """獲取當前市場數據"""
        if not self.latest_orderbook:
            return None
        
        bids = self.latest_orderbook['bids']
        asks = self.latest_orderbook['asks']
        
        # 計算 OBI
        obi = self.obi_calculator.calculate_obi(bids, asks)
        if obi is None:
            return None
        
        # 計算 Microprice
        microprice_data = self.obi_calculator.calculate_microprice(bids, asks)
        if not microprice_data:
            return None
        
        microprice = microprice_data['microprice']
        microprice_pressure = microprice_data['pressure']
        
        # 計算 OBI velocity
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
            vpin = 0.3
        
        # Spread
        spread_data = self.spread_monitor.calculate_spread(bids, asks)
        if not spread_data:
            return None
        
        # Depth
        depth_data = self.spread_monitor.calculate_depth(bids, asks)
        if not depth_data:
            return None
        
        return {
            'price': self.latest_price,
            'obi': obi,
            'obi_velocity': obi_velocity,
            'microprice': microprice,
            'microprice_pressure': microprice_pressure,
            'signed_volume': signed_vol,
            'vpin': vpin,
            'spread_bps': spread_data['spread_bps'],
            'total_depth': depth_data['total_depth'],
            'depth_imbalance': depth_data['depth_imbalance']
        }
    
    def make_decision(self, timestamp: float, verbose: bool = False):
        """執行交易決策"""
        if not self.warmup_complete:
            return
        
        # 獲取市場數據
        market_data = self.get_market_data()
        if not market_data:
            return
        
        # 使用 Phase C 引擎處理
        decision = self.trading_engine.process_market_data(market_data)
        
        self.total_decisions += 1
        current_price = market_data['price']
        
        # 檢查是否有交易信號
        if decision['signal']['direction'] != "NEUTRAL":
            self.trade_signals += 1
        
        # 檢查是否被風險過濾器阻擋
        if not decision['regime']['is_safe'] and decision['signal']['direction'] != "NEUTRAL":
            self.blocked_by_regime += 1
        
        # 顯示決策（可選）
        if verbose and self.total_decisions % 4 == 0:  # 每 60 秒顯示一次
            self._print_decision(decision, market_data, timestamp)
        
        # 檢查持倉止損/止盈
        if self.open_position:
            exit_result = self.open_position.check_exit(current_price)
            if exit_result:
                reason, pnl_pct = exit_result
                self.close_position(current_price, reason, timestamp)
        
        # 如果沒有持倉且有交易信號
        if not self.open_position and decision['execution']:
            execution = decision['execution']
            
            # 確認執行策略不是 NO_TRADE
            if execution['execution_style'] != "NO_TRADE":
                signal_direction = decision['signal']['direction']
                confidence = decision['signal']['confidence']
                
                # 創建新持倉
                position = Position(
                    entry_price=current_price,
                    direction=signal_direction,
                    size=execution['position_size'],
                    leverage=execution['leverage'],
                    stop_loss_pct=execution['stop_loss_pct'],
                    take_profit_pct=execution['take_profit_pct'],
                    timestamp=timestamp,
                    capital=self.capital
                )
                
                self.open_position = position
                self.trades_executed += 1
                
                if verbose:
                    logger.info(f"\n🚀 開倉 [{execution['execution_style']}]")
                    logger.info(f"   方向: {signal_direction}")
                    logger.info(f"   價格: ${current_price:.2f}")
                    logger.info(f"   倉位: {execution['position_size']*100:.0f}%, 槓桿: {execution['leverage']:.1f}x")
    
    def close_position(self, exit_price: float, reason: str, timestamp: float):
        """平倉"""
        if self.open_position:
            pos = self.open_position
            pos.close(exit_price, reason, timestamp)
            
            logger.info(f"🔔 平倉 [{reason}]")
            logger.info(f"   進場: ${pos.entry_price:.2f} → 出場: ${exit_price:.2f}")
            logger.info(f"   淨利: {pos.pnl_usdt:+.4f} USDT ({pos.pnl_pct:+.2f}%)")
            
            self.closed_positions.append(pos)
            self.open_position = None
    
    def _print_decision(self, decision: Dict, market_data: Dict, timestamp: float):
        """打印決策信息"""
        dt = datetime.fromtimestamp(timestamp / 1000)
        
        signal = decision['signal']
        regime = decision['regime']
        
        # 信號 emoji
        signal_emoji = "📈" if signal['direction'] == "LONG" else "📉" if signal['direction'] == "SHORT" else "⚖️"
        
        # 風險 emoji
        risk_emoji = {"SAFE": "🟢", "WARNING": "🟡", "DANGER": "🟠", "CRITICAL": "🔴"}
        
        logger.info(f"\n[{dt.strftime('%H:%M:%S')}] 決策 #{self.total_decisions}")
        logger.info(f"  價格: ${market_data['price']:.2f}")
        logger.info(f"  信號: {signal_emoji} {signal['direction']} (信心度: {signal['confidence']:.3f})")
        logger.info(f"  風險: {risk_emoji[regime['risk_level']]} {regime['risk_level']}")
    
    def replay(
        self,
        start_date: str,
        end_date: str,
        verbose: bool = True,
        progress_interval: int = 120
    ):
        """
        回放歷史市場數據並執行策略
        
        Args:
            start_date: 開始日期
            end_date: 結束日期
            verbose: 是否顯示詳細信息
            progress_interval: 進度顯示間隔（秒）
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 Market Replay 回測")
        logger.info(f"{'='*60}")
        logger.info(f"交易對: {self.symbol}")
        logger.info(f"時間範圍: {start_date} 到 {end_date}")
        logger.info(f"初始資金: {self.capital} USDT")
        logger.info(f"{'='*60}\n")
        
        # 加載 K線數據
        self.data_loader.load_klines(
            symbol=self.symbol,
            interval="1m",
            start_date=start_date,
            end_date=end_date
        )
        
        # 生成市場事件
        logger.info("生成市場事件...")
        events = self.data_loader.get_market_events(start_date, end_date)
        
        logger.info(f"開始回放 {len(events)} 個事件...\n")
        
        start_time = time.time()
        last_progress_time = 0
        
        for i, event in enumerate(events):
            event_type = event['type']
            event_data = event['data']
            event_timestamp = event['timestamp'].timestamp() * 1000  # 轉換為毫秒時間戳
            
            if event_type == "ORDERBOOK":
                self.process_orderbook(event_data)
            elif event_type == "TRADE":
                self.process_trade(event_data)
            
            # 檢查是否需要做決策
            if event_timestamp - self.last_decision_time >= self.decision_interval * 1000:
                self.make_decision(event_timestamp, verbose=verbose)
                self.last_decision_time = event_timestamp
            
            # 顯示進度
            if verbose and time.time() - last_progress_time >= progress_interval:
                progress = (i + 1) / len(events) * 100
                logger.info(f"\n進度: {progress:.1f}% ({i+1}/{len(events)} 事件)")
                if self.open_position:
                    pnl_usdt, pnl_pct = self.open_position.get_unrealized_pnl(self.latest_price)
                    logger.info(f"當前持倉: {self.open_position.direction}, 未實現盈虧: {pnl_usdt:+.4f} USDT ({pnl_pct:+.2f}%)")
                last_progress_time = time.time()
        
        # 如果還有未平倉的持倉，強制平倉
        if self.open_position:
            self.close_position(self.latest_price, "BACKTEST_END", event_timestamp)
        
        elapsed = time.time() - start_time
        logger.info(f"\n回測完成！用時: {elapsed:.1f} 秒")
        
        # 打印統計
        self.print_statistics()
    
    def print_statistics(self):
        """打印回測統計"""
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 回測統計")
        logger.info(f"{'='*60}")
        
        logger.info(f"\n📈 決策統計:")
        logger.info(f"   總決策數:     {self.total_decisions}")
        logger.info(f"   交易信號:     {self.trade_signals} ({self.trade_signals/max(1,self.total_decisions)*100:.1f}%)")
        logger.info(f"   風險阻擋:     {self.blocked_by_regime} ({self.blocked_by_regime/max(1,self.trade_signals)*100:.1f}% 的信號)")
        logger.info(f"   實際執行:     {self.trades_executed} ({self.trades_executed/max(1,self.total_decisions)*100:.1f}%)")
        
        if self.closed_positions:
            logger.info(f"\n💰 交易統計:")
            logger.info(f"   交易筆數:     {len(self.closed_positions)}")
            
            winning_trades = [p for p in self.closed_positions if p.pnl_usdt > 0]
            losing_trades = [p for p in self.closed_positions if p.pnl_usdt <= 0]
            
            win_rate = len(winning_trades) / len(self.closed_positions) * 100
            logger.info(f"   勝率:         {win_rate:.1f}%")
            
            total_pnl_usdt = sum(p.pnl_usdt for p in self.closed_positions)
            total_pnl_pct = (total_pnl_usdt / self.capital) * 100
            logger.info(f"   總淨利:       {total_pnl_usdt:+.4f} USDT ({total_pnl_pct:+.2f}%)")
            
            total_fees = sum(p.entry_fee + p.exit_fee + p.funding_fee for p in self.closed_positions)
            logger.info(f"   總手續費:     -{total_fees:.4f} USDT")
        else:
            logger.info(f"\n⚠️  沒有完成的交易")


if __name__ == "__main__":
    # 測試代碼
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )
    
    engine = MarketReplayEngine(capital=100.0)
    
    # 回測 2024-11-10 一整天
    engine.replay(
        start_date="2024-11-10",
        end_date="2024-11-10",
        verbose=True,
        progress_interval=300  # 每 5 分鐘顯示一次進度
    )
