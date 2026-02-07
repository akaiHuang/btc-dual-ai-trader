"""
HFT 策略績效對比回測
對比三種交易頻率：
1. 當前 HFT (25 單/分鐘, 0.42 單/秒)
2. 真正 HFT (300 單/分鐘, 5 單/秒)
3. 極限 HFT (600 單/分鐘, 10 單/秒)

包含真實費用：
- Binance Futures 手續費: Maker 0.02%, Taker 0.04%
- 槓桿資金費率: 每 8 小時 0.01% (約 0.00000347%/秒)
- 滑點損失: 依據訂單簿深度
"""

import sys
import os
import asyncio
from datetime import datetime, timedelta
from collections import deque
from typing import Dict, List
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.exchange.obi_calculator import OBICalculator, OBISignal, ExitSignalType


class TradingFees:
    """交易費用計算器"""
    
    # Binance Futures 費用
    MAKER_FEE = 0.0002  # 0.02%
    TAKER_FEE = 0.0004  # 0.04%
    
    # 資金費率 (每 8 小時 0.01%)
    FUNDING_RATE_8H = 0.0001  # 0.01%
    FUNDING_RATE_PER_SECOND = FUNDING_RATE_8H / (8 * 3600)  # 每秒
    
    @staticmethod
    def calculate_trading_fee(position_value: float, is_maker: bool = False) -> float:
        """計算交易手續費"""
        fee_rate = TradingFees.MAKER_FEE if is_maker else TradingFees.TAKER_FEE
        return position_value * fee_rate
    
    @staticmethod
    def calculate_funding_fee(position_value: float, holding_seconds: float) -> float:
        """計算資金費率"""
        return position_value * TradingFees.FUNDING_RATE_PER_SECOND * holding_seconds
    
    @staticmethod
    def calculate_slippage(position_value: float, market_volatility: float = 0.0001) -> float:
        """計算滑點損失（簡化模型）"""
        # 市價單滑點約 0.01-0.05%
        return position_value * market_volatility


class HFTBacktester:
    """HFT 策略回測器"""
    
    def __init__(self, 
                 strategy_name: str,
                 initial_capital: float = 100.0,
                 leverage: int = 5,
                 target_frequency: float = 0.42,  # 單/秒
                 entry_threshold: float = 0.35,
                 exit_threshold: float = 0.20):
        
        self.strategy_name = strategy_name
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.leverage = leverage
        self.target_frequency = target_frequency
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        
        # OBI 計算器
        self.calculator = OBICalculator(
            symbol="BTCUSDT",
            depth_limit=20,
            exit_obi_threshold=exit_threshold,
            exit_trend_periods=5,
            extreme_regression_threshold=0.5
        )
        
        # 交易狀態
        self.position = None  # 'LONG' or 'SHORT'
        self.entry_price = None
        self.entry_time = None
        self.entry_obi = None
        self.position_size_btc = 0.0
        self.position_value_usdt = 0.0
        
        # 速率控制
        self.last_trade_time = datetime.now()
        self.min_trade_interval = 1.0 / target_frequency if target_frequency > 0 else 0
        
        # 統計
        self.trades: List[Dict] = []
        self.total_trading_fees = 0.0
        self.total_funding_fees = 0.0
        self.total_slippage = 0.0
        self.start_time = None
        self.update_count = 0
        
        # 回調
        self.calculator.on_obi_update = self.on_obi_update
        self.calculator.on_exit_signal = self.on_exit_signal
    
    def can_trade_now(self) -> bool:
        """檢查是否可以交易（頻率限制）"""
        now = datetime.now()
        elapsed = (now - self.last_trade_time).total_seconds()
        return elapsed >= self.min_trade_interval
    
    def get_current_price(self) -> float:
        """獲取當前價格（模擬）"""
        # 實際應該從訂單簿獲取，這裡簡化為固定值
        return 106000.0
    
    def calculate_position_size(self, price: float) -> float:
        """計算持倉大小（BTC）"""
        # 使用槓桿後的可用資金
        available = self.current_capital * self.leverage
        # 保守起見，只用 90% 資金
        position_value = available * 0.9
        # 轉換為 BTC
        btc_amount = position_value / price
        return btc_amount
    
    def on_obi_update(self, data):
        """OBI 更新回調"""
        self.update_count += 1
        
        # 每 100 次更新顯示進度
        if self.update_count % 100 == 0:
            elapsed = (datetime.now() - self.start_time).total_seconds()
            print(f"   進度: {self.update_count} 次更新, {len(self.trades)} 筆交易, {elapsed:.1f}s", end='\r')
        
        obi = data['obi']
        signal = OBISignal(data['signal'])
        
        # 檢查進場機會
        if not self.position and self.can_trade_now():
            if signal == OBISignal.STRONG_BUY and obi > self.entry_threshold:
                self.enter_long(obi)
            elif signal == OBISignal.STRONG_SELL and obi < -self.entry_threshold:
                self.enter_short(obi)
    
    def on_exit_signal(self, data):
        """離場信號回調"""
        if not self.position:
            return
        
        signal_type = data['signal_type']
        current_obi = data['details']['current_obi']
        
        # 離場
        self.exit_position(current_obi, f"OBI_{signal_type}")
    
    def enter_long(self, obi: float):
        """開多單"""
        price = self.get_current_price()
        btc_amount = self.calculate_position_size(price)
        position_value = btc_amount * price
        
        # 計算開倉費用
        trading_fee = TradingFees.calculate_trading_fee(position_value, is_maker=False)
        slippage = TradingFees.calculate_slippage(position_value, 0.0002)
        
        # 扣除費用
        self.current_capital -= trading_fee
        self.current_capital -= slippage
        
        self.total_trading_fees += trading_fee
        self.total_slippage += slippage
        
        # 記錄持倉
        self.position = 'LONG'
        self.entry_price = price
        self.entry_time = datetime.now()
        self.entry_obi = obi
        self.position_size_btc = btc_amount
        self.position_value_usdt = position_value
        self.last_trade_time = datetime.now()
        
        # 設置 OBI 計算器狀態
        self.calculator.set_position('LONG', obi)
    
    def enter_short(self, obi: float):
        """開空單"""
        price = self.get_current_price()
        btc_amount = self.calculate_position_size(price)
        position_value = btc_amount * price
        
        # 計算開倉費用
        trading_fee = TradingFees.calculate_trading_fee(position_value, is_maker=False)
        slippage = TradingFees.calculate_slippage(position_value, 0.0002)
        
        # 扣除費用
        self.current_capital -= trading_fee
        self.current_capital -= slippage
        
        self.total_trading_fees += trading_fee
        self.total_slippage += slippage
        
        # 記錄持倉
        self.position = 'SHORT'
        self.entry_price = price
        self.entry_time = datetime.now()
        self.entry_obi = obi
        self.position_size_btc = btc_amount
        self.position_value_usdt = position_value
        self.last_trade_time = datetime.now()
        
        # 設置 OBI 計算器狀態
        self.calculator.set_position('SHORT', obi)
    
    def exit_position(self, current_obi: float, reason: str = "Manual"):
        """平倉"""
        if not self.position:
            return
        
        exit_price = self.get_current_price()
        exit_time = datetime.now()
        holding_seconds = (exit_time - self.entry_time).total_seconds()
        
        # 計算價格變動盈虧
        if self.position == 'LONG':
            price_pnl_pct = (exit_price - self.entry_price) / self.entry_price
        else:  # SHORT
            price_pnl_pct = (self.entry_price - exit_price) / self.entry_price
        
        # 槓桿放大盈虧
        leveraged_pnl_pct = price_pnl_pct * self.leverage
        price_pnl_usdt = self.current_capital * leveraged_pnl_pct
        
        # 計算平倉費用
        exit_position_value = self.position_size_btc * exit_price
        trading_fee = TradingFees.calculate_trading_fee(exit_position_value, is_maker=False)
        slippage = TradingFees.calculate_slippage(exit_position_value, 0.0002)
        funding_fee = TradingFees.calculate_funding_fee(self.position_value_usdt, holding_seconds)
        
        # 總盈虧
        total_pnl = price_pnl_usdt - trading_fee - slippage - funding_fee
        
        # 更新資金
        self.current_capital += total_pnl
        
        # 累計費用
        self.total_trading_fees += trading_fee
        self.total_slippage += slippage
        self.total_funding_fees += funding_fee
        
        # 記錄交易
        trade = {
            'position': self.position,
            'entry_price': self.entry_price,
            'exit_price': exit_price,
            'entry_obi': self.entry_obi,
            'exit_obi': current_obi,
            'holding_seconds': holding_seconds,
            'price_pnl_pct': price_pnl_pct * 100,
            'leveraged_pnl_pct': leveraged_pnl_pct * 100,
            'price_pnl_usdt': price_pnl_usdt,
            'trading_fee': trading_fee,
            'slippage': slippage,
            'funding_fee': funding_fee,
            'total_pnl': total_pnl,
            'reason': reason
        }
        self.trades.append(trade)
        
        # 重置狀態
        self.position = None
        self.entry_price = None
        self.entry_time = None
        self.entry_obi = None
        self.position_size_btc = 0.0
        self.position_value_usdt = 0.0
        self.calculator.set_position(None)
    
    def print_summary(self):
        """打印回測總結"""
        if not self.start_time:
            return
        
        duration = (datetime.now() - self.start_time).total_seconds()
        
        print("\n" + "="*100)
        print(f"策略回測總結: {self.strategy_name}")
        print("="*100)
        
        # 基本信息
        print(f"\n📊 基本信息:")
        print(f"   初始資金: {self.initial_capital:.2f} USDT")
        print(f"   最終資金: {self.current_capital:.2f} USDT")
        print(f"   槓桿倍數: {self.leverage}x")
        print(f"   目標頻率: {self.target_frequency:.2f} 單/秒 ({self.target_frequency*60:.0f} 單/分鐘)")
        print(f"   運行時長: {duration:.1f} 秒")
        print(f"   OBI 更新: {self.update_count} 次")
        
        if not self.trades:
            print("\n⚠️ 沒有完成的交易")
            return
        
        # 交易統計
        win_trades = [t for t in self.trades if t['total_pnl'] > 0]
        loss_trades = [t for t in self.trades if t['total_pnl'] <= 0]
        
        print(f"\n📈 交易統計:")
        print(f"   總交易次數: {len(self.trades)}")
        print(f"   獲利次數: {len(win_trades)}")
        print(f"   虧損次數: {len(loss_trades)}")
        print(f"   勝率: {len(win_trades)/len(self.trades)*100:.1f}%")
        print(f"   實際頻率: {len(self.trades)/duration:.2f} 單/秒 ({len(self.trades)/duration*60:.1f} 單/分鐘)")
        
        # 盈虧統計
        total_pnl = sum(t['total_pnl'] for t in self.trades)
        roi = (self.current_capital - self.initial_capital) / self.initial_capital * 100
        
        print(f"\n💰 盈虧統計:")
        print(f"   價格盈虧: {sum(t['price_pnl_usdt'] for t in self.trades):+.2f} USDT")
        print(f"   交易手續費: -{self.total_trading_fees:.2f} USDT")
        print(f"   滑點損失: -{self.total_slippage:.2f} USDT")
        print(f"   資金費率: -{self.total_funding_fees:.2f} USDT")
        print(f"   ─────────────────────")
        print(f"   淨盈虧: {total_pnl:+.2f} USDT")
        print(f"   投資報酬率: {roi:+.2f}%")
        
        # 平均統計
        avg_holding = sum(t['holding_seconds'] for t in self.trades) / len(self.trades)
        avg_pnl = total_pnl / len(self.trades)
        
        print(f"\n📊 平均統計:")
        print(f"   平均持倉: {avg_holding:.2f} 秒")
        print(f"   平均盈虧: {avg_pnl:+.4f} USDT/筆")
        
        if win_trades:
            avg_win = sum(t['total_pnl'] for t in win_trades) / len(win_trades)
            print(f"   平均獲利: +{avg_win:.4f} USDT/筆")
        
        if loss_trades:
            avg_loss = sum(t['total_pnl'] for t in loss_trades) / len(loss_trades)
            print(f"   平均虧損: {avg_loss:.4f} USDT/筆")
        
        # 費用佔比
        total_fees = self.total_trading_fees + self.total_slippage + self.total_funding_fees
        gross_pnl = total_pnl + total_fees
        
        print(f"\n💸 費用分析:")
        print(f"   總費用: {total_fees:.2f} USDT ({total_fees/self.initial_capital*100:.2f}%)")
        if gross_pnl > 0:
            print(f"   費用侵蝕: {total_fees/gross_pnl*100:.1f}% (佔毛利)")
        print(f"   手續費: {self.total_trading_fees:.2f} USDT ({self.total_trading_fees/total_fees*100:.1f}%)")
        print(f"   滑點: {self.total_slippage:.2f} USDT ({self.total_slippage/total_fees*100:.1f}%)")
        print(f"   資金費率: {self.total_funding_fees:.2f} USDT ({self.total_funding_fees/total_fees*100:.1f}%)")
        
        # 外推年化收益
        if duration > 0:
            hourly_roi = roi / (duration / 3600)
            daily_roi = hourly_roi * 24
            monthly_roi = daily_roi * 30
            yearly_roi = daily_roi * 365
            
            print(f"\n🚀 外推收益 (假設維持相同績效):")
            print(f"   時化: {hourly_roi:+.2f}%")
            print(f"   日化: {daily_roi:+.2f}%")
            print(f"   月化: {monthly_roi:+.2f}%")
            print(f"   年化: {yearly_roi:+.2f}%")
            print(f"   ⚠️ 注意: 實際交易會受市場波動、流動性等影響")
        
        print("\n" + "="*100)
    
    async def run(self, duration: int = 120):
        """運行回測"""
        self.start_time = datetime.now()
        
        print(f"\n🚀 開始回測: {self.strategy_name}")
        print(f"   初始資金: {self.initial_capital} USDT")
        print(f"   槓桿: {self.leverage}x")
        print(f"   目標頻率: {self.target_frequency} 單/秒")
        print(f"   運行時長: {duration} 秒")
        print(f"   進場閾值: OBI > {self.entry_threshold}")
        print(f"   離場閾值: OBI 變化 > {self.exit_threshold}")
        
        try:
            await asyncio.wait_for(
                self.calculator.start_websocket(),
                timeout=duration
            )
        except asyncio.TimeoutError:
            pass
        except KeyboardInterrupt:
            print("\n⚠️ 用戶中斷")
        finally:
            self.calculator.stop_websocket()
            
            # 強制平倉
            if self.position:
                current_obi = self.calculator.get_current_obi()
                if current_obi:
                    self.exit_position(current_obi['obi'], "Forced_Close")
            
            # 打印總結
            self.print_summary()


async def compare_strategies():
    """對比三種策略"""
    
    print("\n" + "🎯"*50)
    print(" " * 30 + "HFT 策略績效對比回測")
    print("🎯"*50)
    
    strategies = [
        {
            'name': '當前 HFT (25 單/分鐘)',
            'frequency': 0.42,  # 單/秒
            'entry_threshold': 0.35,
            'duration': 90  # 縮短到 90 秒
        },
        {
            'name': '真正 HFT (300 單/分鐘)',
            'frequency': 5.0,  # 單/秒
            'entry_threshold': 0.38,  # 稍高閾值
            'duration': 90
        },
        {
            'name': '極限 HFT (600 單/分鐘)',
            'frequency': 10.0,  # 單/秒
            'entry_threshold': 0.40,  # 更高閾值
            'duration': 90
        }
    ]
    
    results = []
    
    for idx, strategy in enumerate(strategies, 1):
        print(f"\n{'='*100}")
        print(f"測試 {idx}/3: {strategy['name']}")
        print(f"{'='*100}")
        
        backtester = HFTBacktester(
            strategy_name=strategy['name'],
            initial_capital=100.0,
            leverage=5,
            target_frequency=strategy['frequency'],
            entry_threshold=strategy['entry_threshold']
        )
        
        await backtester.run(duration=strategy['duration'])
        
        # 記錄結果
        results.append({
            'name': strategy['name'],
            'initial': 100.0,
            'final': backtester.current_capital,
            'roi': (backtester.current_capital - 100.0) / 100.0 * 100,
            'trades': len(backtester.trades),
            'win_rate': len([t for t in backtester.trades if t['total_pnl'] > 0]) / len(backtester.trades) * 100 if backtester.trades else 0,
            'total_fees': backtester.total_trading_fees + backtester.total_slippage + backtester.total_funding_fees
        })
        
        # 間隔 5 秒
        if idx < len(strategies):
            print(f"\n⏳ 等待 5 秒後開始下一個測試...")
            await asyncio.sleep(5)
    
    # 打印對比總結
    print("\n" + "="*100)
    print(" " * 35 + "📊 策略對比總結")
    print("="*100)
    
    print(f"\n{'策略':^30} | {'初始':^10} | {'最終':^10} | {'ROI':^10} | {'交易數':^8} | {'勝率':^8} | {'總費用':^10}")
    print("-"*100)
    
    for result in results:
        print(f"{result['name']:^30} | "
              f"{result['initial']:^10.2f} | "
              f"{result['final']:^10.2f} | "
              f"{result['roi']:^+10.2f}% | "
              f"{result['trades']:^8} | "
              f"{result['win_rate']:^8.1f}% | "
              f"{result['total_fees']:^10.2f}")
    
    # 找出最佳策略
    best_strategy = max(results, key=lambda x: x['roi'])
    
    print("\n" + "="*100)
    print(f"🏆 最佳策略: {best_strategy['name']}")
    print(f"   投資報酬率: {best_strategy['roi']:+.2f}%")
    print(f"   最終資金: {best_strategy['final']:.2f} USDT")
    print(f"   勝率: {best_strategy['win_rate']:.1f}%")
    print("="*100)


async def main():
    """主函數"""
    await compare_strategies()


if __name__ == "__main__":
    asyncio.run(main())
