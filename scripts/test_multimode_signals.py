#!/usr/bin/env python3
"""
多檔位策略實時測試與分析
===========================

完整模擬交易系統，包含：
1. 實時信號監控
2. 完整訂單簿記錄
3. 盈虧分析與統計
4. JSON + TXT 報告生成

使用方法:
    python scripts/test_multimode_signals.py M5 3  # M5 模式測試 3 小時
"""

import sys
import asyncio
import pandas as pd
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import ccxt

# 添加項目根目錄
sys.path.append(str(Path(__file__).parent.parent))

from src.strategy.hybrid_multi_mode import MultiModeHybridStrategy, TradingMode, SignalType


class SimulatedTrade:
    """模擬交易記錄"""
    
    # 真實 Binance 費率
    MAKER_FEE = 0.0002  # 0.02%
    TAKER_FEE = 0.0005  # 0.05%
    FUNDING_RATE_HOURLY = 0.00003  # ~0.003%/hr
    SLIPPAGE_BPS = 2  # 2 bps 滑點
    
    def __init__(self, trade_id: int, timestamp: float, signal_result: Dict,
                 entry_price: float, capital: float = 100.0):
        # 基本信息
        self.trade_id = trade_id
        self.timestamp = timestamp
        self.entry_time = datetime.fromtimestamp(timestamp / 1000)
        
        # 信號信息
        self.signal = signal_result['signal']
        self.confidence = signal_result.get('confidence', 0)
        self.reasoning = signal_result.get('reasoning', '')
        self.mode = signal_result['mode']
        self.risk_params = signal_result['risk_params']
        
        # 倉位信息
        self.capital = capital
        self.leverage = self.risk_params['leverage']
        self.position_size = 1.0  # 100% 倉位
        self.position_value = capital * self.leverage
        
        # 入場
        self.entry_price = entry_price
        slippage = self.SLIPPAGE_BPS / 10000
        if self.signal == SignalType.LONG:
            self.actual_entry_price = entry_price * (1 + slippage)
        else:
            self.actual_entry_price = entry_price * (1 - slippage)
        
        self.entry_fee = self.position_value * self.TAKER_FEE
        
        # 止損止盈
        self.tp_pct = self.risk_params['tp_pct']
        self.sl_pct = self.risk_params['sl_pct']
        
        if self.signal == SignalType.LONG:
            self.tp_price = self.actual_entry_price * (1 + self.tp_pct)
            self.sl_price = self.actual_entry_price * (1 - self.sl_pct)
        else:  # SHORT
            self.tp_price = self.actual_entry_price * (1 - self.tp_pct)
            self.sl_price = self.actual_entry_price * (1 + self.sl_pct)
        
        # 時間止損
        self.time_stop_hours = self.risk_params['time_stop_hours']
        self.time_stop_timestamp = timestamp + (self.time_stop_hours * 3600 * 1000)
        
        # 出場信息
        self.exit_price = None
        self.exit_time = None
        self.exit_reason = None
        self.holding_seconds = None
        self.status = "OPEN"
        
        # 盈虧
        self.pnl_usdt = 0.0
        self.roi = 0.0
    
    def check_exit(self, current_price: float, current_timestamp: float) -> bool:
        """檢查是否觸發出場條件"""
        if self.status != "OPEN":
            return False
        
        # 檢查止盈
        if self.signal == SignalType.LONG:
            if current_price >= self.tp_price:
                self.close(current_price, "TP", current_timestamp)
                return True
            elif current_price <= self.sl_price:
                self.close(current_price, "SL", current_timestamp)
                return True
        else:  # SHORT
            if current_price <= self.tp_price:
                self.close(current_price, "TP", current_timestamp)
                return True
            elif current_price >= self.sl_price:
                self.close(current_price, "SL", current_timestamp)
                return True
        
        # 檢查時間止損
        if current_timestamp >= self.time_stop_timestamp:
            self.close(current_price, "TIME_STOP", current_timestamp)
            return True
        
        return False
    
    def close(self, exit_price: float, reason: str, timestamp: float):
        """平倉計算盈虧"""
        self.exit_price = exit_price
        self.exit_time = datetime.fromtimestamp(timestamp / 1000)
        self.exit_reason = reason
        self.holding_seconds = (timestamp - self.timestamp) / 1000
        self.status = "CLOSED"
        
        # 計算持倉時間
        holding_hours = self.holding_seconds / 3600
        
        # 資金費率
        funding_fee = self.position_value * self.FUNDING_RATE_HOURLY * holding_hours
        
        # 出場滑點
        slippage = self.SLIPPAGE_BPS / 10000
        if self.signal == SignalType.LONG:
            actual_exit_price = exit_price * (1 - slippage)
        else:
            actual_exit_price = exit_price * (1 + slippage)
        
        # 出場手續費
        exit_fee = self.position_value * self.TAKER_FEE
        
        # 價格盈虧
        if self.signal == SignalType.LONG:
            price_pnl = ((actual_exit_price - self.actual_entry_price) / 
                        self.actual_entry_price) * self.position_value
        else:  # SHORT
            price_pnl = ((self.actual_entry_price - actual_exit_price) / 
                        self.actual_entry_price) * self.position_value
        
        # 總盈虧
        total_fees = self.entry_fee + exit_fee + funding_fee
        self.pnl_usdt = price_pnl - total_fees
        self.roi = (self.pnl_usdt / self.capital) * 100
    
    def to_dict(self) -> dict:
        """轉換為字典"""
        return {
            'trade_id': self.trade_id,
            'timestamp': self.timestamp,
            'entry_time': self.entry_time.isoformat(),
            'signal': self.signal.value,
            'confidence': self.confidence,
            'reasoning': self.reasoning,
            'mode': self.mode,
            'leverage': self.leverage,
            'entry_price': self.entry_price,
            'actual_entry_price': self.actual_entry_price,
            'tp_price': self.tp_price,
            'sl_price': self.sl_price,
            'exit_price': self.exit_price,
            'exit_time': self.exit_time.isoformat() if self.exit_time else None,
            'exit_reason': self.exit_reason,
            'holding_seconds': self.holding_seconds,
            'pnl_usdt': self.pnl_usdt,
            'roi': self.roi,
            'status': self.status
        }




class MultiModeTester:
    """多檔位策略測試器"""
    
    def __init__(self, mode: TradingMode, duration_hours: float = 3.0):
        self.mode = mode
        self.duration_hours = duration_hours
        self.strategy = MultiModeHybridStrategy(initial_mode=mode)
        
        # 資金
        self.initial_capital = 100.0
        self.balance = 100.0
        
        # 交易記錄
        self.trades: List[SimulatedTrade] = []
        self.open_trades: List[SimulatedTrade] = []
        self.signal_count = 0
        self.check_count = 0
        
        # 測試時間
        self.test_start_time = datetime.now()
        self.end_timestamp = (datetime.now() + timedelta(hours=duration_hours)).timestamp() * 1000
        
        # 保存檔案
        self._init_save_files()
        
        # Exchange
        self.exchange = ccxt.binance({
            'options': {
                'defaultType': 'future'  # 使用合約市場
            }
        })
    
    def _init_save_files(self):
        """初始化保存檔案"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.save_dir = Path('data/paper_trading')
        self.save_dir.mkdir(parents=True, exist_ok=True)
        
        self.json_filename = self.save_dir / f'multimode_{self.mode.value}_{timestamp}.json'
        self.log_filename = self.save_dir / f'multimode_{self.mode.value}_{timestamp}.txt'
        self.report_filename = self.save_dir / f'multimode_{self.mode.value}_{timestamp}_report.txt'
        
        # 初始化 JSON
        initial_data = {
            'metadata': {
                'mode': self.mode.value,
                'duration_hours': self.duration_hours,
                'start_time': self.test_start_time.isoformat(),
                'initial_capital': self.initial_capital,
                'config': self.strategy.get_current_config().__dict__
            },
            'trades': []
        }
        
        with open(self.json_filename, 'w') as f:
            json.dump(initial_data, f, indent=2)
    
    async def fetch_data(self):
        """獲取實時數據"""
        ohlcv = self.exchange.fetch_ohlcv('BTC/USDT:USDT', '15m', limit=200)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        funding_data = self.exchange.fetch_funding_rate('BTC/USDT:USDT')
        current_funding = funding_data['fundingRate']
        df['fundingRate'] = current_funding
        
        return df, current_funding
    
    async def run(self):
        """運行測試"""
        config = self.strategy.get_current_config()
        
        print("="*80)
        print(f"🎮 Multi-Mode Paper Trading Test")
        print("="*80)
        print(f"Mode: {self.mode.value}")
        print(f"Duration: {self.duration_hours} hours")
        print(f"Initial Capital: ${self.initial_capital:.2f}")
        print()
        print(f"📊 Configuration:")
        print(f"   Target Frequency: {config.target_frequency}")
        print(f"   Funding Z-score: {config.funding_zscore_threshold}")
        print(f"   Signal Score: {config.signal_score_threshold}")
        print(f"   RSI: {config.rsi_oversold}/{config.rsi_overbought}")
        print(f"   Leverage: {config.leverage}x")
        print(f"   TP/SL: {config.tp_pct:.2%} / {config.sl_pct:.2%}")
        print(f"   Cooldown: {config.cooldown_minutes}min")
        print("="*80)
        print()
        
        print("🔍 Monitoring...")
        print()
        
        try:
            while True:
                current_timestamp = datetime.now().timestamp() * 1000
                
                # 檢查結束時間
                if current_timestamp >= self.end_timestamp:
                    print("\n⏰ Time limit reached. Stopping...")
                    break
                
                # 獲取數據
                df, funding_rate = await self.fetch_data()
                current_price = df['close'].iloc[-1]
                self.check_count += 1
                
                # 檢查持倉是否觸發出場
                for trade in self.open_trades[:]:  # 複製列表避免修改問題
                    if trade.check_exit(current_price, current_timestamp):
                        self.open_trades.remove(trade)
                        self.balance += trade.pnl_usdt
                        
                        # 即時保存
                        self._save_trade(trade)
                        
                        # 顯示平倉
                        emoji = "🟢" if trade.pnl_usdt > 0 else "🔴"
                        print(f"{emoji} Trade #{trade.trade_id} CLOSED")
                        print(f"   {trade.signal.value} | Exit: {trade.exit_reason}")
                        print(f"   Entry: ${trade.actual_entry_price:,.2f} → Exit: ${trade.exit_price:,.2f}")
                        print(f"   Holding: {trade.holding_seconds/60:.1f}min | ROI: {trade.roi:+.2f}%")
                        print(f"   Balance: ${self.balance:.2f} ({(self.balance-100):+.2f}%)")
                        print()
                
                # 生成信號（只在沒有持倉時）
                if len(self.open_trades) == 0:
                    result = self.strategy.generate_signal(df)
                    
                    if result['signal'] != SignalType.NEUTRAL:
                        self.signal_count += 1
                        
                        # 創建交易
                        trade = SimulatedTrade(
                            trade_id=len(self.trades) + 1,
                            timestamp=current_timestamp,
                            signal_result=result,
                            entry_price=current_price,
                            capital=self.balance
                        )
                        
                        self.trades.append(trade)
                        self.open_trades.append(trade)
                        
                        # 顯示開倉
                        timestamp_str = datetime.now().strftime("%H:%M:%S")
                        print(f"🚨 [{timestamp_str}] SIGNAL #{self.signal_count}")
                        print(f"   Direction: {trade.signal.value}")
                        print(f"   Confidence: {trade.confidence:.2%}")
                        print(f"   Price: ${current_price:,.2f}")
                        print(f"   Leverage: {trade.leverage}x")
                        print(f"   TP: ${trade.tp_price:,.2f} (+{trade.tp_pct:.2%})")
                        print(f"   SL: ${trade.sl_price:,.2f} (-{trade.sl_pct:.2%})")
                        print(f"   Time Stop: {trade.time_stop_hours}h")
                        print(f"   Reasoning: {result['reasoning']}")
                        print()
                else:
                    # 顯示持倉狀態
                    trade = self.open_trades[0]
                    holding_time = (current_timestamp - trade.timestamp) / 1000 / 60
                    
                    # 計算未實現盈虧
                    if trade.signal == SignalType.LONG:
                        unrealized_pnl_pct = (current_price - trade.actual_entry_price) / trade.actual_entry_price * 100 * trade.leverage
                    else:
                        unrealized_pnl_pct = (trade.actual_entry_price - current_price) / trade.actual_entry_price * 100 * trade.leverage
                    
                    timestamp_str = datetime.now().strftime("%H:%M:%S")
                    emoji = "🟢" if unrealized_pnl_pct > 0 else "🔴"
                    print(f"[{timestamp_str}] {emoji} Trade #{trade.trade_id} | {trade.signal.value} | Holding {holding_time:.1f}min | P&L: {unrealized_pnl_pct:+.2f}%", end='\r')
                
                # 等待 15 秒
                await asyncio.sleep(15)
                
        except KeyboardInterrupt:
            print("\n\n🛑 Stopped by user")
        
        # 強制平倉所有持倉
        df, _ = await self.fetch_data()
        current_price = df['close'].iloc[-1]
        current_timestamp = datetime.now().timestamp() * 1000
        
        for trade in self.open_trades:
            trade.close(current_price, "FORCED_EXIT", current_timestamp)
            self.balance += trade.pnl_usdt
            self._save_trade(trade)
        
        # 生成報告
        self._generate_report()
    
    def _save_trade(self, trade: SimulatedTrade):
        """即時保存交易"""
        try:
            with open(self.json_filename, 'r') as f:
                data = json.load(f)
            
            data['trades'].append(trade.to_dict())
            data['metadata']['current_balance'] = self.balance
            
            with open(self.json_filename, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"⚠️  Save error: {e}")
    
    def _generate_report(self):
        """生成完整報告"""
        elapsed_time = (datetime.now() - self.test_start_time).total_seconds()
        elapsed_minutes = elapsed_time / 60
        
        closed_trades = [t for t in self.trades if t.status == "CLOSED"]
        winning_trades = [t for t in closed_trades if t.pnl_usdt > 0]
        losing_trades = [t for t in closed_trades if t.pnl_usdt < 0]
        
        total_pnl = sum(t.pnl_usdt for t in closed_trades)
        total_roi = (self.balance - self.initial_capital) / self.initial_capital * 100
        win_rate = len(winning_trades) / len(closed_trades) * 100 if closed_trades else 0
        
        # 保存 TXT 報告
        with open(self.log_filename, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write(f"📊 Multi-Mode Paper Trading Report\n")
            f.write("="*80 + "\n\n")
            
            f.write(f"⏱️  Test Duration: {elapsed_minutes:.1f} minutes ({elapsed_time:.0f} seconds)\n")
            f.write(f"📅 Start Time: {self.test_start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"📅 End Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"🎮 Mode: {self.mode.value}\n")
            f.write(f"📊 Check Count: {self.check_count}\n")
            f.write(f"🚨 Signal Count: {self.signal_count}\n")
            f.write("\n")
            
            f.write("="*80 + "\n")
            f.write("💰 SUMMARY\n")
            f.write("="*80 + "\n\n")
            
            f.write(f"Initial Capital: ${self.initial_capital:.2f}\n")
            f.write(f"Final Balance: ${self.balance:.2f}\n")
            f.write(f"Total P&L: ${total_pnl:+.2f}\n")
            f.write(f"Total ROI: {total_roi:+.2f}%\n")
            f.write("\n")
            
            f.write(f"Total Trades: {len(closed_trades)}\n")
            f.write(f"Winning Trades: {len(winning_trades)}\n")
            f.write(f"Losing Trades: {len(losing_trades)}\n")
            f.write(f"Win Rate: {win_rate:.1f}%\n")
            f.write("\n")
            
            if winning_trades:
                avg_win = sum(t.pnl_usdt for t in winning_trades) / len(winning_trades)
                f.write(f"Average Win: ${avg_win:+.2f}\n")
            
            if losing_trades:
                avg_loss = sum(t.pnl_usdt for t in losing_trades) / len(losing_trades)
                f.write(f"Average Loss: ${avg_loss:+.2f}\n")
            
            f.write("\n")
            
            # 交易明細
            f.write("="*80 + "\n")
            f.write("📝 TRADE DETAILS\n")
            f.write("="*80 + "\n\n")
            
            for trade in closed_trades:
                emoji = "🟢" if trade.pnl_usdt > 0 else "🔴"
                f.write(f"{emoji} Trade #{trade.trade_id}\n")
                f.write(f"   Time: {trade.entry_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"   Direction: {trade.signal.value}\n")
                f.write(f"   Confidence: {trade.confidence:.2%}\n")
                f.write(f"   Entry: ${trade.actual_entry_price:,.2f}\n")
                f.write(f"   Exit: ${trade.exit_price:,.2f} ({trade.exit_reason})\n")
                f.write(f"   Holding: {trade.holding_seconds/60:.1f} minutes\n")
                f.write(f"   P&L: ${trade.pnl_usdt:+.2f} ({trade.roi:+.2f}%)\n")
                f.write(f"   Leverage: {trade.leverage}x\n")
                f.write(f"   Reasoning: {trade.reasoning}\n")
                f.write("\n")
        
        # 顯示報告
        print("\n" + "="*80)
        print("📊 Test Summary")
        print("="*80)
        print(f"Duration: {elapsed_minutes:.1f} minutes")
        print(f"Mode: {self.mode.value}")
        print(f"Checks: {self.check_count} | Signals: {self.signal_count}")
        print()
        print(f"Initial Capital: ${self.initial_capital:.2f}")
        print(f"Final Balance: ${self.balance:.2f}")
        print(f"Total ROI: {total_roi:+.2f}%")
        print()
        print(f"Total Trades: {len(closed_trades)}")
        print(f"Win Rate: {win_rate:.1f}%")
        print()
        print(f"💾 Reports saved:")
        print(f"   JSON: {self.json_filename}")
        print(f"   Log: {self.log_filename}")
        print("="*80)


async def fetch_realtime_data():
    """獲取實時數據"""
    # 使用 ccxt 獲取數據
    exchange = ccxt.binance({
        'options': {
            'defaultType': 'future'  # 使用合約市場
        }
    })
    
    # 獲取 K 線數據（15分鐘，最近 200 根）
    ohlcv = exchange.fetch_ohlcv('BTC/USDT:USDT', '15m', limit=200)  # 使用 USDT-M 合約
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    
    # 獲取 Funding Rate
    funding_data = exchange.fetch_funding_rate('BTC/USDT:USDT')
    current_funding = funding_data['fundingRate']
    
    # 添加 fundingRate 列（假設恆定，實際應該每根 K 線都有）
    df['fundingRate'] = current_funding
    
    return df


def parse_mode(mode_str: str) -> TradingMode:
    """解析模式"""
    mode_map = {
        'M0': TradingMode.M0_ULTRA_SAFE,
        'M1': TradingMode.M1_SAFE,
        'M2': TradingMode.M2_NORMAL,
        'M3': TradingMode.M3_AGGRESSIVE,
        'M4': TradingMode.M4_VERY_AGGRESSIVE,
        'M5': TradingMode.M5_ULTRA_AGGRESSIVE,
    }
    
    mode_upper = mode_str.upper()
    if mode_upper not in mode_map:
        print(f"❌ Invalid mode: {mode_str}")
        print(f"Available: {', '.join(mode_map.keys())}")
        sys.exit(1)
    
    return mode_map[mode_upper]


async def main():
    """主程式"""
    # 解析參數
    mode = TradingMode.M2_NORMAL  # 默認 M2
    duration_hours = 3.0  # 默認 3 小時
    
    if len(sys.argv) > 1:
        mode = parse_mode(sys.argv[1])
    
    if len(sys.argv) > 2:
        try:
            duration_hours = float(sys.argv[2])
        except ValueError:
            print(f"❌ Invalid duration: {sys.argv[2]}")
            print(f"Usage: {sys.argv[0]} [mode] [hours]")
            sys.exit(1)
    
    # 創建測試器並運行
    tester = MultiModeTester(mode, duration_hours)
    await tester.run()


if __name__ == "__main__":
    asyncio.run(main())
