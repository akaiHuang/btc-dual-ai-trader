#!/usr/bin/env python3
"""
Paper Trading Engine - 模擬交易引擎
===================================

連接 Binance testnet 進行模擬交易，驗證策略實際表現

功能：
1. 實時 K 線數據獲取
2. 策略信號生成
3. 訂單執行模擬
4. 持倉管理
5. 性能監控與記錄

作者: Paper Trading System
日期: 2025-11-16
"""

import asyncio
import websockets
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import logging
from pathlib import Path
import sys

# 添加項目根目錄
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.strategy.hybrid_funding_technical import HybridFundingTechnicalStrategy


@dataclass
class Position:
    """持倉信息"""
    entry_time: datetime
    entry_price: float
    direction: str  # "LONG" or "SHORT"
    size: float  # BTC 數量
    leverage: int
    tp_price: float
    sl_price: float
    time_stop_minutes: int
    
    def to_dict(self) -> Dict:
        return {
            **asdict(self),
            'entry_time': self.entry_time.isoformat(),
        }


@dataclass
class Trade:
    """已平倉交易記錄"""
    entry_time: datetime
    exit_time: datetime
    direction: str
    entry_price: float
    exit_price: float
    size: float
    leverage: int
    pnl_usd: float
    pnl_pct: float
    exit_reason: str  # "TP", "SL", "TIME_STOP"
    holding_minutes: float
    
    def to_dict(self) -> Dict:
        return {
            **asdict(self),
            'entry_time': self.entry_time.isoformat(),
            'exit_time': self.exit_time.isoformat(),
        }


class PaperTradingEngine:
    """
    Paper Trading 引擎
    
    模擬真實交易環境，但不實際下單
    """
    
    def __init__(
        self,
        strategy: HybridFundingTechnicalStrategy,
        initial_capital: float = 10000.0,
        position_size_usd: float = 300.0,
        max_positions: int = 1,
        log_dir: str = "logs/paper_trading",
        # 風控參數
        leverage: int = 10,
        tp_pct: float = 0.015,  # 現貨百分比
        sl_pct: float = 0.010,  # 現貨百分比
        time_stop_hours: int = 12,
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.position_size_usd = position_size_usd
        self.max_positions = max_positions
        
        # 風控配置
        self.leverage = leverage
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.time_stop_hours = time_stop_hours
        
        # 持倉與交易記錄
        self.position: Optional[Position] = None
        self.trades: List[Trade] = []
        
        # K 線緩存 (用於策略計算)
        self.kline_buffer = pd.DataFrame()
        self.max_buffer_size = 500  # 保留最近 500 根 K 線
        
        # Funding Rate 緩存
        self.funding_rate = 0.0
        self.funding_history = []
        
        # 日誌設置
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._setup_logging()
        
        # 統計
        self.stats = {
            'signals_generated': 0,
            'trades_opened': 0,
            'trades_closed': 0,
            'winning_trades': 0,
            'losing_trades': 0,
        }
        
        # WebSocket 連接
        self.ws_kline = None
        self.ws_funding = None
        self.running = False
    
    def _setup_logging(self):
        """設置日誌"""
        log_file = self.log_dir / f"paper_trading_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    async def start(self, duration_hours: float = 3.0):
        """
        啟動 Paper Trading
        
        Args:
            duration_hours: 運行時長（小時）
        """
        self.logger.info("="*80)
        self.logger.info("🚀 Paper Trading Engine Starting...")
        self.logger.info("="*80)
        self.logger.info(f"Initial Capital: ${self.initial_capital:,.2f}")
        self.logger.info(f"Position Size: ${self.position_size_usd:,.2f}")
        self.logger.info(f"Duration: {duration_hours} hours")
        self.logger.info(f"Strategy: {self.strategy.__class__.__name__}")
        self.logger.info("="*80)
        
        self.running = True
        end_time = datetime.now() + timedelta(hours=duration_hours)
        
        try:
            # 連接 WebSocket
            await self._connect_websockets()
            
            # 主循環
            while self.running and datetime.now() < end_time:
                await asyncio.sleep(1)
                
                # 檢查持倉
                if self.position:
                    await self._check_position()
            
            self.logger.info("\n⏰ Time limit reached. Stopping...")
            
        except KeyboardInterrupt:
            self.logger.info("\n🛑 Manual stop requested...")
        except Exception as e:
            self.logger.error(f"❌ Error: {e}", exc_info=True)
        finally:
            await self.stop()
    
    async def _connect_websockets(self):
        """連接 Binance WebSocket"""
        # Binance testnet WebSocket
        # 注意：testnet 的 WebSocket 地址可能不同
        # 這裡使用主網地址作為示例（實際應該用 testnet）
        
        self.logger.info("📡 Connecting to Binance WebSocket...")
        
        # K 線 WebSocket (15m)
        kline_url = "wss://stream.binance.com:9443/ws/btcusdt@kline_15m"
        asyncio.create_task(self._kline_handler(kline_url))
        
        # Funding Rate 需要從 REST API 定期獲取
        asyncio.create_task(self._funding_rate_updater())
        
        self.logger.info("✅ WebSocket connected")
    
    async def _kline_handler(self, url: str):
        """K 線數據處理"""
        retry_count = 0
        max_retries = 5
        
        while self.running and retry_count < max_retries:
            try:
                async with websockets.connect(url) as ws:
                    self.logger.info("✅ K-line WebSocket connected")
                    retry_count = 0  # 重置重試計數
                    
                    async for message in ws:
                        if not self.running:
                            break
                        
                        data = json.loads(message)
                        await self._process_kline(data)
                        
            except websockets.exceptions.ConnectionClosed:
                retry_count += 1
                self.logger.warning(f"⚠️ K-line WebSocket disconnected. Retry {retry_count}/{max_retries}...")
                await asyncio.sleep(5)
            except Exception as e:
                self.logger.error(f"❌ K-line handler error: {e}")
                await asyncio.sleep(5)
    
    async def _process_kline(self, data: Dict):
        """處理 K 線數據"""
        if 'k' not in data:
            return
        
        kline = data['k']
        
        # 只處理已完成的 K 線
        if not kline['x']:
            return
        
        # 解析 K 線
        new_kline = {
            'timestamp': pd.to_datetime(kline['t'], unit='ms'),
            'open': float(kline['o']),
            'high': float(kline['h']),
            'low': float(kline['l']),
            'close': float(kline['c']),
            'volume': float(kline['v']),
        }
        
        # 添加到緩存
        self.kline_buffer = pd.concat([
            self.kline_buffer,
            pd.DataFrame([new_kline])
        ], ignore_index=True)
        
        # 限制緩存大小
        if len(self.kline_buffer) > self.max_buffer_size:
            self.kline_buffer = self.kline_buffer.iloc[-self.max_buffer_size:]
        
        self.logger.info(f"📊 New K-line: {new_kline['timestamp']} | "
                        f"O:{new_kline['open']:.2f} H:{new_kline['high']:.2f} "
                        f"L:{new_kline['low']:.2f} C:{new_kline['close']:.2f}")
        
        # 生成交易信號
        if len(self.kline_buffer) >= 100:  # 需要足夠的歷史數據
            await self._generate_signal(new_kline)
    
    async def _funding_rate_updater(self):
        """定期更新 Funding Rate"""
        import aiohttp
        
        # Binance Futures API
        url = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"
        
        while self.running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            self.funding_rate = float(data['lastFundingRate'])
                            
                            self.funding_history.append({
                                'timestamp': datetime.now(),
                                'rate': self.funding_rate
                            })
                            
                            # 保留最近 90 天
                            if len(self.funding_history) > 90 * 3:  # 每天3次
                                self.funding_history = self.funding_history[-270:]
                            
                            self.logger.info(f"💰 Funding Rate updated: {self.funding_rate:.6f}")
            except Exception as e:
                self.logger.error(f"❌ Failed to update Funding Rate: {e}")
            
            # 每 10 分鐘更新一次
            await asyncio.sleep(600)
    
    async def _generate_signal(self, current_kline: Dict):
        """生成交易信號"""
        # 如果已有持倉，不再開新倉
        if self.position is not None:
            return
        
        # 準備策略所需的數據
        df = self.kline_buffer.copy()
        df['fundingRate'] = self.funding_rate  # 添加 Funding Rate
        
        try:
            # 調用策略生成信號
            hybrid_signal = self.strategy.generate_signal(df)
            
            self.stats['signals_generated'] += 1
            
            # 檢查是否有交易信號
            if hybrid_signal.signal.value in ['LONG', 'SHORT']:
                direction = hybrid_signal.signal.value
                entry_price = current_kline['close']
                
                # 計算 TP/SL（使用 Engine 的風控參數）
                if direction == 'LONG':
                    tp_price = entry_price * (1 + self.tp_pct)
                    sl_price = entry_price * (1 - self.sl_pct)
                else:  # SHORT
                    tp_price = entry_price * (1 - self.tp_pct)
                    sl_price = entry_price * (1 + self.sl_pct)
                
                # 構建信號字典
                signal = {
                    'direction': direction,
                    'entry_price': entry_price,
                    'tp_price': tp_price,
                    'sl_price': sl_price,
                    'tp_pct': self.tp_pct,
                    'sl_pct': self.sl_pct,
                    'leverage': self.leverage,
                    'time_stop_minutes': self.time_stop_hours * 60,
                    'confidence': hybrid_signal.confidence,
                    'score': hybrid_signal.confidence,  # 使用 confidence 作為 score
                    'reasoning': hybrid_signal.reasoning
                }
                
                self.logger.info(f"\n{'='*80}")
                self.logger.info(f"🎯 Signal Generated!")
                self.logger.info(f"   Direction: {direction}")
                self.logger.info(f"   Confidence: {hybrid_signal.confidence:.2%}")
                self.logger.info(f"   Entry: ${entry_price:.2f}")
                self.logger.info(f"   TP: ${tp_price:.2f} ({self.tp_pct:.2%})")
                self.logger.info(f"   SL: ${sl_price:.2f} ({self.sl_pct:.2%})")
                self.logger.info(f"   Reasoning: {hybrid_signal.reasoning}")
                self.logger.info(f"={'='*80}\n")
                
                # 開倉
                await self._open_position(signal)
        
        except Exception as e:
            self.logger.error(f"❌ Signal generation error: {e}", exc_info=True)
    
    async def _open_position(self, signal: Dict):
        """開倉"""
        if self.position is not None:
            self.logger.warning("⚠️ Already have a position. Skip opening.")
            return
        
        # 計算倉位大小（BTC 數量）
        btc_size = self.position_size_usd / signal['entry_price']
        
        self.position = Position(
            entry_time=datetime.now(),
            entry_price=signal['entry_price'],
            direction=signal['direction'],
            size=btc_size,
            leverage=signal.get('leverage', 10),
            tp_price=signal['tp_price'],
            sl_price=signal['sl_price'],
            time_stop_minutes=signal.get('time_stop_minutes', 720),  # 12 小時
        )
        
        self.stats['trades_opened'] += 1
        
        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"✅ Position Opened!")
        self.logger.info(f"   Direction: {self.position.direction}")
        self.logger.info(f"   Entry: ${self.position.entry_price:.2f}")
        self.logger.info(f"   Size: {self.position.size:.6f} BTC (${self.position_size_usd:.2f})")
        self.logger.info(f"   Leverage: {self.position.leverage}x")
        self.logger.info(f"   TP: ${self.position.tp_price:.2f}")
        self.logger.info(f"   SL: ${self.position.sl_price:.2f}")
        self.logger.info(f"   Time Stop: {self.position.time_stop_minutes} min")
        self.logger.info(f"={'='*80}\n")
    
    async def _check_position(self):
        """檢查持倉，決定是否平倉"""
        if self.position is None:
            return
        
        # 獲取當前價格
        if len(self.kline_buffer) == 0:
            return
        
        current_price = self.kline_buffer.iloc[-1]['close']
        current_time = datetime.now()
        
        # 計算持倉時間
        holding_minutes = (current_time - self.position.entry_time).total_seconds() / 60
        
        # 檢查平倉條件
        exit_reason = None
        exit_price = current_price
        
        if self.position.direction == "LONG":
            if current_price >= self.position.tp_price:
                exit_reason = "TP"
                exit_price = self.position.tp_price
            elif current_price <= self.position.sl_price:
                exit_reason = "SL"
                exit_price = self.position.sl_price
        else:  # SHORT
            if current_price <= self.position.tp_price:
                exit_reason = "TP"
                exit_price = self.position.tp_price
            elif current_price >= self.position.sl_price:
                exit_reason = "SL"
                exit_price = self.position.sl_price
        
        # 時間止損
        if exit_reason is None and holding_minutes >= self.position.time_stop_minutes:
            exit_reason = "TIME_STOP"
            exit_price = current_price
        
        # 平倉
        if exit_reason:
            await self._close_position(exit_price, exit_reason, current_time)
    
    async def _close_position(self, exit_price: float, exit_reason: str, exit_time: datetime):
        """平倉"""
        if self.position is None:
            return
        
        # 計算盈虧
        if self.position.direction == "LONG":
            pnl_pct = (exit_price - self.position.entry_price) / self.position.entry_price
        else:  # SHORT
            pnl_pct = (self.position.entry_price - exit_price) / self.position.entry_price
        
        # 考慮槓桿
        pnl_pct_leveraged = pnl_pct * self.position.leverage
        pnl_usd = self.position_size_usd * pnl_pct_leveraged
        
        # 扣除手續費 (0.04% taker fee x2)
        fee = self.position_size_usd * 0.0004 * 2
        pnl_usd_net = pnl_usd - fee
        
        # 更新資金
        self.capital += pnl_usd_net
        
        # 記錄交易
        holding_minutes = (exit_time - self.position.entry_time).total_seconds() / 60
        
        trade = Trade(
            entry_time=self.position.entry_time,
            exit_time=exit_time,
            direction=self.position.direction,
            entry_price=self.position.entry_price,
            exit_price=exit_price,
            size=self.position.size,
            leverage=self.position.leverage,
            pnl_usd=pnl_usd_net,
            pnl_pct=pnl_pct_leveraged,
            exit_reason=exit_reason,
            holding_minutes=holding_minutes,
        )
        
        self.trades.append(trade)
        self.stats['trades_closed'] += 1
        
        if pnl_usd_net > 0:
            self.stats['winning_trades'] += 1
        else:
            self.stats['losing_trades'] += 1
        
        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"{'🟢' if pnl_usd_net > 0 else '🔴'} Position Closed: {exit_reason}")
        self.logger.info(f"   Direction: {self.position.direction}")
        self.logger.info(f"   Entry: ${self.position.entry_price:.2f}")
        self.logger.info(f"   Exit: ${exit_price:.2f}")
        self.logger.info(f"   PnL: ${pnl_usd_net:+.2f} ({pnl_pct_leveraged:+.2%})")
        self.logger.info(f"   Fee: ${fee:.2f}")
        self.logger.info(f"   Holding: {holding_minutes:.1f} min")
        self.logger.info(f"   Capital: ${self.capital:,.2f} ({(self.capital/self.initial_capital-1)*100:+.2f}%)")
        self.logger.info(f"={'='*80}\n")
        
        # 清空持倉
        self.position = None
    
    async def stop(self):
        """停止 Paper Trading"""
        self.running = False
        
        # 如果有未平倉的持倉，強制平倉
        if self.position:
            current_price = self.kline_buffer.iloc[-1]['close'] if len(self.kline_buffer) > 0 else self.position.entry_price
            await self._close_position(current_price, "FORCE_CLOSE", datetime.now())
        
        # 生成報告
        self._generate_report()
    
    def _generate_report(self):
        """生成交易報告"""
        self.logger.info("\n" + "="*80)
        self.logger.info("📊 Paper Trading Report")
        self.logger.info("="*80)
        
        # 基本統計
        total_trades = len(self.trades)
        winning_trades = self.stats['winning_trades']
        losing_trades = self.stats['losing_trades']
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        total_pnl = sum(t.pnl_usd for t in self.trades)
        total_return = (self.capital - self.initial_capital) / self.initial_capital * 100
        
        self.logger.info(f"\n【Summary】")
        self.logger.info(f"  Initial Capital: ${self.initial_capital:,.2f}")
        self.logger.info(f"  Final Capital: ${self.capital:,.2f}")
        self.logger.info(f"  Total Return: {total_return:+.2f}%")
        self.logger.info(f"  Total PnL: ${total_pnl:+.2f}")
        
        self.logger.info(f"\n【Trades】")
        self.logger.info(f"  Total Trades: {total_trades}")
        self.logger.info(f"  Winning Trades: {winning_trades}")
        self.logger.info(f"  Losing Trades: {losing_trades}")
        self.logger.info(f"  Win Rate: {win_rate:.2f}%")
        self.logger.info(f"  Signals Generated: {self.stats['signals_generated']}")
        
        if total_trades > 0:
            avg_win = np.mean([t.pnl_usd for t in self.trades if t.pnl_usd > 0]) if winning_trades > 0 else 0
            avg_loss = np.mean([t.pnl_usd for t in self.trades if t.pnl_usd <= 0]) if losing_trades > 0 else 0
            avg_holding = np.mean([t.holding_minutes for t in self.trades])
            
            self.logger.info(f"\n【Performance】")
            self.logger.info(f"  Avg Win: ${avg_win:.2f}")
            self.logger.info(f"  Avg Loss: ${avg_loss:.2f}")
            self.logger.info(f"  Avg Holding: {avg_holding:.1f} min")
            
            # 出場原因統計
            exit_reasons = {}
            for trade in self.trades:
                exit_reasons[trade.exit_reason] = exit_reasons.get(trade.exit_reason, 0) + 1
            
            self.logger.info(f"\n【Exit Reasons】")
            for reason, count in exit_reasons.items():
                pct = count / total_trades * 100
                self.logger.info(f"  {reason}: {count} ({pct:.1f}%)")
        
        # 保存報告到文件
        report_file = self.log_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        report_data = {
            'summary': {
                'initial_capital': self.initial_capital,
                'final_capital': self.capital,
                'total_return': total_return,
                'total_pnl': total_pnl,
            },
            'stats': {
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'win_rate': win_rate,
                'signals_generated': self.stats['signals_generated'],
            },
            'trades': [t.to_dict() for t in self.trades],
        }
        
        with open(report_file, 'w') as f:
            json.dump(report_data, f, indent=2, default=str)
        
        self.logger.info(f"\n✅ Report saved: {report_file}")
        self.logger.info("="*80)


async def main():
    """主程式"""
    # 創建策略實例
    strategy = HybridFundingTechnical(
        # 使用保守配置
        funding_lookback_days=90,
        funding_extreme_threshold=2.5,  # Z-score
        leverage=10,
        tp_pct=0.015,
        sl_pct=0.010,
        time_stop_hours=12,
    )
    
    # 創建 Paper Trading 引擎
    engine = PaperTradingEngine(
        strategy=strategy,
        initial_capital=10000.0,
        position_size_usd=300.0,
        max_positions=1,
    )
    
    # 運行 3 小時
    await engine.start(duration_hours=3.0)


if __name__ == "__main__":
    asyncio.run(main())
