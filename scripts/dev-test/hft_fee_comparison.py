#!/usr/bin/env python3
"""
HFT 手續費對比測試
測試極低頻率交易能否克服手續費問題
"""

import asyncio
import json

# 添加專案根目錄到 Python 路徑
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.exchange.binance_client import BinanceClient
from src.exchange.obi_calculator import OBICalculator


class TradingFees:
    """交易手續費計算器"""
    
    # 幣安合約費率 (VIP 0)
    MAKER_FEE = 0.0002  # 0.02% 掛單費用
    TAKER_FEE = 0.0004  # 0.04% 吃單費用
    FUNDING_RATE_8H = 0.0001  # 0.01% 資金費率 (每 8 小時)
    FUNDING_RATE_PER_SECOND = FUNDING_RATE_8H / (8 * 3600)  # 每秒資金費率
    
    @staticmethod
    def calculate_trading_fee(position_value: float, is_maker: bool = False) -> float:
        """
        計算交易手續費
        
        Args:
            position_value: 倉位價值 (USDT)
            is_maker: 是否掛單 (True = Maker, False = Taker)
            
        Returns:
            手續費 (USDT)
        """
        fee_rate = TradingFees.MAKER_FEE if is_maker else TradingFees.TAKER_FEE
        return position_value * fee_rate
    
    @staticmethod
    def calculate_funding_fee(position_value: float, holding_seconds: float) -> float:
        """
        計算資金費率成本
        
        Args:
            position_value: 倉位價值 (USDT)
            holding_seconds: 持倉時間 (秒)
            
        Returns:
            資金費用 (USDT)
        """
        return position_value * TradingFees.FUNDING_RATE_PER_SECOND * holding_seconds
    
    @staticmethod
    def calculate_slippage(position_value: float, market_volatility: float = 0.0002) -> float:
        """
        計算滑點成本
        
        Args:
            position_value: 倉位價值 (USDT)
            market_volatility: 市場波動率 (默認 0.02%)
            
        Returns:
            滑點成本 (USDT)
        """
        return position_value * market_volatility


class HFTBacktester:
    """HFT 策略回測器（含手續費）"""
    
    def __init__(
        self,
        strategy_name: str,
        initial_capital: float = 100.0,  # 100 USDT
        leverage: int = 5,  # 5 倍槓桿
        target_frequency: float = 0.42,  # 目標交易頻率 (orders/sec)
        obi_threshold: float = 0.35,  # OBI 進場閾值
        test_duration: int = 90,  # 測試時長 (秒)
    ):
        self.strategy_name = strategy_name
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.leverage = leverage
        self.target_frequency = target_frequency
        self.obi_threshold = obi_threshold
        self.test_duration = test_duration
        
        # 計算單次倉位大小 (使用槓桿後的 90%)
        self.position_size_usdt = (initial_capital * leverage) * 0.9
        
        # 交易記錄
        self.trades = []
        self.current_position = None  # {'side': 'LONG'/'SHORT', 'entry_price', 'entry_time', 'entry_obi'}
        
        # 手續費追蹤
        self.total_trading_fees = 0.0  # 交易手續費
        self.total_funding_fees = 0.0  # 資金費率
        self.total_slippage = 0.0  # 滑點成本
        
        # 統計
        self.start_time = None
        self.update_count = 0
        self.last_trade_time = 0
        
        # OBI 計算器
        self.obi_calc = OBICalculator(
            symbol='BTCUSDT',
            depth_limit=20,
            history_size=100
        )
        
        # 幣安客戶端
        self.client = None
    
    async def run(self):
        """運行回測"""
        print(f"\n{'='*100}")
        print(f"🎯 策略: {self.strategy_name}")
        print(f"{'='*100}")
        print(f"初始資金: {self.initial_capital} USDT")
        print(f"槓桿倍數: {self.leverage}x")
        print(f"單次倉位: {self.position_size_usdt:.2f} USDT")
        print(f"目標頻率: {self.target_frequency:.2f} 單/秒 ({self.target_frequency*60:.0f} 單/分鐘)")
        print(f"OBI 閾值: ±{self.obi_threshold}")
        print(f"測試時長: {self.test_duration} 秒")
        print(f"{'='*100}\n")
        
        self.start_time = datetime.now().timestamp()
        
        # 創建幣安客戶端
        self.client = BinanceClient()
        await self.client.connect()
        
        # 訂閱深度數據
        await self.client.subscribe_depth('BTCUSDT', self.on_depth_update)
        
        # 運行指定時長
        await asyncio.sleep(self.test_duration)
        
        # 平倉
        if self.current_position:
            current_price = self.obi_calc.mid_price if self.obi_calc.mid_price else 100000
            await self.exit_position(current_price, self.obi_calc.obi, "時間到")
        
        # 斷開連接
        await self.client.disconnect()
        
        # 打印總結
        self.print_summary()
    
    async def on_depth_update(self, data: dict):
        """處理深度數據更新"""
        self.update_count += 1
        
        # 計算 OBI
        bids = [(float(p), float(q)) for p, q in data['bids']]
        asks = [(float(p), float(q)) for p, q in data['asks']]
        self.obi_calc.calculate(bids, asks)
        
        # 檢查離場信號 (優先)
        if self.current_position:
            exit_signal = self.obi_calc.check_exit_signal(
                self.current_position['side'],
                self.current_position['entry_obi']
            )
            
            if exit_signal:
                await self.exit_position(
                    self.obi_calc.mid_price,
                    self.obi_calc.obi,
                    exit_signal.signal_type.value
                )
                return
        
        # 檢查進場機會 (控制頻率)
        current_time = datetime.now().timestamp()
        elapsed = current_time - self.start_time
        
        # 計算最小交易間隔
        min_interval = 1.0 / self.target_frequency if self.target_frequency > 0 else 0.1
        
        if elapsed - self.last_trade_time < min_interval:
            return
        
        if not self.current_position:
            obi = self.obi_calc.obi
            
            # 做多信號
            if obi > self.obi_threshold:
                await self.enter_long(self.obi_calc.mid_price, obi)
                self.last_trade_time = elapsed
            
            # 做空信號
            elif obi < -self.obi_threshold:
                await self.enter_short(self.obi_calc.mid_price, obi)
                self.last_trade_time = elapsed
        
        # 進度顯示
        if self.update_count % 100 == 0:
            elapsed_time = datetime.now().timestamp() - self.start_time
            print(f"\r   進度: {elapsed_time:.0f}s / {self.test_duration}s | "
                  f"交易: {len(self.trades)} | "
                  f"資金: {self.capital:.2f} USDT", end='')
    
    async def enter_long(self, price: float, obi: float):
        """開多單"""
        if self.current_position:
            return
        
        # 計算交易成本
        trading_fee = TradingFees.calculate_trading_fee(self.position_size_usdt, is_maker=False)
        slippage = TradingFees.calculate_slippage(self.position_size_usdt, market_volatility=0.0002)
        
        # 從資金中扣除
        self.capital -= (trading_fee + slippage)
        self.total_trading_fees += trading_fee
        self.total_slippage += slippage
        
        self.current_position = {
            'side': 'LONG',
            'entry_price': price,
            'entry_time': datetime.now().timestamp(),
            'entry_obi': obi
        }
    
    async def enter_short(self, price: float, obi: float):
        """開空單"""
        if self.current_position:
            return
        
        # 計算交易成本
        trading_fee = TradingFees.calculate_trading_fee(self.position_size_usdt, is_maker=False)
        slippage = TradingFees.calculate_slippage(self.position_size_usdt, market_volatility=0.0002)
        
        # 從資金中扣除
        self.capital -= (trading_fee + slippage)
        self.total_trading_fees += trading_fee
        self.total_slippage += slippage
        
        self.current_position = {
            'side': 'SHORT',
            'entry_price': price,
            'entry_time': datetime.now().timestamp(),
            'entry_obi': obi
        }
    
    async def exit_position(self, exit_price: float, exit_obi: float, reason: str):
        """平倉"""
        if not self.current_position:
            return
        
        side = self.current_position['side']
        entry_price = self.current_position['entry_price']
        entry_time = self.current_position['entry_time']
        
        # 計算持倉時間
        holding_time = datetime.now().timestamp() - entry_time
        
        # 計算價格變動
        if side == 'LONG':
            price_change_pct = (exit_price - entry_price) / entry_price
        else:  # SHORT
            price_change_pct = (entry_price - exit_price) / entry_price
        
        # 計算毛利
        gross_pnl = self.position_size_usdt * price_change_pct
        
        # 計算所有手續費
        exit_trading_fee = TradingFees.calculate_trading_fee(self.position_size_usdt, is_maker=False)
        funding_fee = TradingFees.calculate_funding_fee(self.position_size_usdt, holding_time)
        exit_slippage = TradingFees.calculate_slippage(self.position_size_usdt, market_volatility=0.0002)
        
        # 淨利潤 = 毛利 - 所有費用
        net_pnl = gross_pnl - exit_trading_fee - funding_fee - exit_slippage
        
        # 更新資金
        self.capital += net_pnl
        self.total_trading_fees += exit_trading_fee
        self.total_funding_fees += funding_fee
        self.total_slippage += exit_slippage
        
        # 記錄交易
        self.trades.append({
            'side': side,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'holding_time': holding_time,
            'gross_pnl': gross_pnl,
            'net_pnl': net_pnl,
            'trading_fees': exit_trading_fee,
            'funding_fee': funding_fee,
            'slippage': exit_slippage,
            'reason': reason
        })
        
        self.current_position = None
    
    def print_summary(self):
        """打印交易總結"""
        print(f"\n\n{'='*100}")
        print(f"📊 {self.strategy_name} - 交易總結")
        print(f"{'='*100}\n")
        
        # 基本統計
        total_trades = len(self.trades)
        win_trades = len([t for t in self.trades if t['net_pnl'] > 0])
        loss_trades = total_trades - win_trades
        win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0
        
        # 損益統計
        total_gross_pnl = sum(t['gross_pnl'] for t in self.trades)
        total_net_pnl = self.capital - self.initial_capital
        roi_gross = (total_gross_pnl / self.initial_capital * 100) if self.initial_capital > 0 else 0
        roi_net = (total_net_pnl / self.initial_capital * 100) if self.initial_capital > 0 else 0
        
        # 手續費統計
        total_fees = self.total_trading_fees + self.total_funding_fees + self.total_slippage
        fee_impact = (total_fees / self.initial_capital * 100) if self.initial_capital > 0 else 0
        
        # 平均統計
        avg_holding_time = sum(t['holding_time'] for t in self.trades) / total_trades if total_trades > 0 else 0
        avg_gross_pnl = total_gross_pnl / total_trades if total_trades > 0 else 0
        avg_net_pnl = total_net_pnl / total_trades if total_trades > 0 else 0
        
        print("📈 績效指標:")
        print(f"   總交易次數: {total_trades}")
        print(f"   獲利次數: {win_trades}")
        print(f"   虧損次數: {loss_trades}")
        print(f"   勝率: {win_rate:.1f}%")
        print(f"   平均持倉時間: {avg_holding_time:.1f} 秒\n")
        
        print("💰 損益分析:")
        print(f"   初始資金: {self.initial_capital:.2f} USDT")
        print(f"   最終資金: {self.capital:.2f} USDT")
        print(f"   毛利: {total_gross_pnl:+.2f} USDT ({roi_gross:+.2f}%)")
        print(f"   淨利: {total_net_pnl:+.2f} USDT ({roi_net:+.2f}%)")
        print(f"   平均毛利/交易: {avg_gross_pnl:+.2f} USDT")
        print(f"   平均淨利/交易: {avg_net_pnl:+.2f} USDT\n")
        
        print("💸 手續費明細:")
        print(f"   交易手續費: -{self.total_trading_fees:.2f} USDT ({self.total_trading_fees/self.initial_capital*100:.2f}%)")
        print(f"   資金費率: -{self.total_funding_fees:.2f} USDT ({self.total_funding_fees/self.initial_capital*100:.2f}%)")
        print(f"   滑點成本: -{self.total_slippage:.2f} USDT ({self.total_slippage/self.initial_capital*100:.2f}%)")
        print(f"   總手續費: -{total_fees:.2f} USDT ({fee_impact:.2f}%)")
        print(f"   手續費影響: {roi_gross - roi_net:.2f}%\n")
        
        # 年化收益
        if self.test_duration > 0:
            periods_per_year = (365 * 24 * 3600) / self.test_duration
            annualized_return = roi_net * periods_per_year
            print(f"📅 年化預估:")
            print(f"   年化收益率: {annualized_return:+.2f}%")
            print(f"   (基於 {self.test_duration} 秒測試數據推算)\n")
        
        print(f"{'='*100}\n")
        
        return {
            'strategy_name': self.strategy_name,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'roi_gross': roi_gross,
            'roi_net': roi_net,
            'total_fees': total_fees,
            'fee_impact': fee_impact,
            'final_capital': self.capital
        }


async def compare_strategies():
    """對比三種策略"""
    print("\n" + "="*100)
    print("🎯 HFT 策略績效對比 (含真實手續費)")
    print("="*100)
    print("\n測試配置:")
    print("  • 初始資金: 100 USDT")
    print("  • 槓桿倍數: 5x")
    print("  • 測試時長: 90 秒/策略")
    print("  • 手續費: Taker 0.04%, Maker 0.02%")
    print("  • 資金費率: 0.01% / 8小時")
    print("  • 滑點: 0.02%")
    print("\n")
    
    strategies = [
        {
            'name': '當前 HFT (25單/分鐘)',
            'frequency': 0.42,  # 25/60
            'threshold': 0.35,
        },
        {
            'name': '真正 HFT (300單/分鐘)',
            'frequency': 5.0,  # 300/60
            'threshold': 0.38,
        },
        {
            'name': '極限 HFT (600單/分鐘)',
            'frequency': 10.0,  # 600/60
            'threshold': 0.40,
        }
    ]
    
    results = []
    
    for i, config in enumerate(strategies, 1):
        print(f"\n{'🔥' * 50}")
        print(f"測試 {i}/3: {config['name']}")
        print(f"{'🔥' * 50}\n")
        
        backtester = HFTBacktester(
            strategy_name=config['name'],
            initial_capital=100.0,
            leverage=5,
            target_frequency=config['frequency'],
            obi_threshold=config['threshold'],
            test_duration=90
        )
        
        result = await backtester.run()
        results.append(result)
        
        # 策略間等待 5 秒
        if i < len(strategies):
            print(f"\n⏳ 等待 5 秒後測試下一個策略...\n")
            await asyncio.sleep(5)
    
    # 打印對比總結
    print("\n" + "="*100)
    print("📊 策略績效對比總結")
    print("="*100 + "\n")
    
    print(f"{'策略名稱':<30} | {'交易次數':>8} | {'勝率':>8} | {'毛利':>10} | {'淨利':>10} | {'手續費':>10} | {'最終資金':>12}")
    print("-" * 100)
    
    best_strategy = None
    best_roi = float('-inf')
    
    for r in results:
        print(f"{r['strategy_name']:<30} | "
              f"{r['total_trades']:>8} | "
              f"{r['win_rate']:>7.1f}% | "
              f"{r['roi_gross']:>+9.2f}% | "
              f"{r['roi_net']:>+9.2f}% | "
              f"{r['fee_impact']:>9.2f}% | "
              f"{r['final_capital']:>11.2f} U")
        
        if r['roi_net'] > best_roi:
            best_roi = r['roi_net']
            best_strategy = r['strategy_name']
    
    print("\n" + "="*100)
    print(f"🏆 最佳策略: {best_strategy} (淨 ROI: {best_roi:+.2f}%)")
    print("="*100 + "\n")


if __name__ == '__main__':
    asyncio.run(compare_strategies())
