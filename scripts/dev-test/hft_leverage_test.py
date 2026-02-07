#!/usr/bin/env python3
"""
HFT 槓桿測試腳本
測試不同槓桿倍數對保守 HFT 策略的影響
"""

import asyncio
import json
import time
from datetime import datetime
import websockets


class TradingFees:
    """交易手續費計算 (使用 Maker 訂單)"""
    MAKER_FEE = 0.0002  # 0.02% (掛單手續費)
    SLIPPAGE = 0.0001   # 0.01% (滑價估計)
    FUNDING_RATE = 0.0001  # 0.01% per 8 hours
    
    @classmethod
    def entry_cost(cls, position_value, use_maker=True):
        """進場成本 (手續費 + 滑價)"""
        fee = cls.MAKER_FEE if use_maker else 0.0004
        return position_value * (fee + cls.SLIPPAGE)
    
    @classmethod
    def exit_cost(cls, position_value, holding_seconds, use_maker=True):
        """出場成本 (手續費 + 滑價 + 資金費率)"""
        fee = cls.MAKER_FEE if use_maker else 0.0004
        funding = (holding_seconds / (8 * 3600)) * cls.FUNDING_RATE * position_value
        return position_value * (fee + cls.SLIPPAGE) + funding


class LeverageTestStrategy:
    """槓桿測試策略"""
    
    def __init__(self, name: str, leverage: int, capital: float = 100.0):
        self.name = name
        self.leverage = leverage
        self.capital = capital
        self.balance = capital
        
        # 交易參數
        self.frequency = 0.25  # 4秒檢查一次 (15單/分)
        self.threshold = 0.45  # 進場閾值
        
        # 狀態追蹤
        self.position = None  # 'LONG' or 'SHORT'
        self.entry_price = 0
        self.entry_obi = 0
        self.entry_time = 0
        self.position_size = 0
        
        # 統計數據
        self.trades = []
        self.total_fees = 0
        self.gross_pnl = 0
        
        # OBI 數據
        self.obi = 0
        self.mid_price = 0
        self.last_trade_time = 0
    
    def calculate_obi(self, bids, asks):
        """計算 OBI (Order Book Imbalance)"""
        bid_volume = sum(float(price) * float(qty) for price, qty in bids)
        ask_volume = sum(float(price) * float(qty) for price, qty in asks)
        total_volume = bid_volume + ask_volume
        
        if total_volume == 0:
            return 0
        
        return (bid_volume - ask_volume) / total_volume
    
    def _get_signal(self) -> str:
        """獲取信號類型"""
        if self.obi > 0.5: return "STRONG_BUY"
        elif self.obi > 0.2: return "BUY"
        elif self.obi < -0.5: return "STRONG_SELL"
        elif self.obi < -0.2: return "SELL"
        else: return "NEUTRAL"
    
    def _should_enter_long(self) -> bool:
        """判斷是否應該開多"""
        if self.position is not None:
            return False
        if time.time() - self.last_trade_time < self.frequency:
            return False
        return self.obi > self.threshold
    
    def _should_enter_short(self) -> bool:
        """判斷是否應該開空"""
        if self.position is not None:
            return False
        if time.time() - self.last_trade_time < self.frequency:
            return False
        return self.obi < -self.threshold
    
    def _should_exit(self) -> str:
        """判斷是否應該出場,返回原因"""
        if self.position is None:
            return None
        
        # 1. OBI 反轉 (最高優先級)
        if self.position == 'LONG' and self.obi < -0.1:
            return "OBI_REVERSAL|OBI 翻負,賣盤開始堆積|HIGH"
        if self.position == 'SHORT' and self.obi > 0.1:
            return "OBI_REVERSAL|OBI 翻正,買盤開始堆積|HIGH"
        
        # 2. OBI 趨勢轉弱
        if self.position == 'LONG' and self.obi < self.entry_obi - 0.15:
            return "OBI_WEAKENING|OBI 趨勢轉弱,買盤力量減退|MEDIUM"
        if self.position == 'SHORT' and self.obi > self.entry_obi + 0.15:
            return "OBI_WEAKENING|OBI 趨勢轉強,賣盤力量減退|MEDIUM"
        
        # 3. 極端值回歸
        if self.position == 'LONG' and self.entry_obi > 0.7 and self.obi < 0.5:
            return "EXTREME_REGRESSION|極端買盤回歸,趨勢反轉風險|MEDIUM"
        if self.position == 'SHORT' and self.entry_obi < -0.7 and self.obi > -0.5:
            return "EXTREME_REGRESSION|極端賣盤回歸,趨勢反轉風險|MEDIUM"
        
        return None
    
    def _enter_long(self):
        """開多單"""
        self.position = 'LONG'
        self.entry_price = self.mid_price
        self.entry_obi = self.obi
        self.entry_time = time.time()
        self.position_size = self.balance * self.leverage  # 槓桿倍數
        self.last_trade_time = time.time()
        
        entry_fee = TradingFees.entry_cost(self.position_size)
        self.balance -= entry_fee
        self.total_fees += entry_fee
        
        print(f"\n✅ 開多單 [{self.name}]")
        print(f"   進場價格: {self.entry_price:.2f} USDT")
        print(f"   進場 OBI: {self.entry_obi:+.4f}")
        print(f"   倉位大小: {self.position_size:.2f} USDT (本金 {self.balance:.2f} × {self.leverage}x)")
        print(f"   進場手續費: -{entry_fee:.2f} USDT")
        
        now = datetime.now().strftime("%H:%M:%S")
        print(f"      {now}       | {self.obi:+7.4f} | {'STRONG_BUY':^12} | {'LONG':^8} | {'0.0s':>8} | 🟢 開多單  |           ")
    
    def _enter_short(self):
        """開空單"""
        self.position = 'SHORT'
        self.entry_price = self.mid_price
        self.entry_obi = self.obi
        self.entry_time = time.time()
        self.position_size = self.balance * self.leverage  # 槓桿倍數
        self.last_trade_time = time.time()
        
        entry_fee = TradingFees.entry_cost(self.position_size)
        self.balance -= entry_fee
        self.total_fees += entry_fee
        
        print(f"\n✅ 開空單 [{self.name}]")
        print(f"   進場價格: {self.entry_price:.2f} USDT")
        print(f"   進場 OBI: {self.entry_obi:+.4f}")
        print(f"   倉位大小: {self.position_size:.2f} USDT (本金 {self.balance:.2f} × {self.leverage}x)")
        print(f"   進場手續費: -{entry_fee:.2f} USDT")
        
        now = datetime.now().strftime("%H:%M:%S")
        print(f"      {now}       | {self.obi:+7.4f} | {'STRONG_SELL':^12} | {'SHORT':^8} | {'0.0s':>8} | 🔴 開空單  |           ")
    
    def _exit_position(self, reason_str: str):
        """平倉"""
        if self.position is None:
            return
        
        parts = reason_str.split('|')
        reason_type = parts[0]
        reason_desc = parts[1] if len(parts) > 1 else ""
        severity = parts[2] if len(parts) > 2 else ""
        
        holding_time = time.time() - self.entry_time
        
        # 計算盈亏
        if self.position == 'LONG':
            price_change = (self.mid_price - self.entry_price) / self.entry_price
        else:  # SHORT
            price_change = (self.entry_price - self.mid_price) / self.entry_price
        
        gross_pnl = self.position_size * price_change
        exit_cost = TradingFees.exit_cost(self.position_size, holding_time)
        net_pnl = gross_pnl - exit_cost
        
        self.gross_pnl += gross_pnl
        self.balance += net_pnl
        self.total_fees += exit_cost
        
        # 記錄交易
        self.trades.append({
            'side': self.position,
            'entry_price': self.entry_price,
            'exit_price': self.mid_price,
            'entry_obi': self.entry_obi,
            'exit_obi': self.obi,
            'holding_time': holding_time,
            'gross_pnl': gross_pnl,
            'fees': exit_cost + TradingFees.entry_cost(self.position_size),
            'net_pnl': net_pnl,
            'exit_reason': reason_type
        })
        
        cumulative_pnl = self.balance - 100.0
        
        print(f"\n🚨 離場訊號: {reason_type}")
        print(f"   原因: {reason_desc}")
        print(f"   嚴重性: {severity}")
        print(f"\n✅ 平倉: {self.position}")
        print(f"   出場價格: {self.mid_price:.2f} USDT")
        print(f"   持倉時間: {holding_time:.1f} 秒")
        print(f"   本次毛利: {gross_pnl:+.2f} USDT ({price_change*100:+.2f}%)")
        print(f"   手續費: -{exit_cost:.2f} USDT")
        print(f"   本次淨利: {net_pnl:+.2f} USDT")
        print(f"   累計 PnL: {cumulative_pnl:+.2f} USDT")
        
        self.position = None
        self.last_trade_time = time.time()
    
    def process_orderbook(self, data):
        """處理訂單簿數據"""
        bids = data['bids'][:20]
        asks = data['asks'][:20]
        
        # 計算中間價
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        self.mid_price = (best_bid + best_ask) / 2
        
        # 計算 OBI
        self.obi = self.calculate_obi(bids, asks)
        
        # 檢查出場條件
        exit_reason = self._should_exit()
        if exit_reason:
            self._exit_position(exit_reason)
            return
        
        # 檢查進場條件
        if self._should_enter_long():
            self._enter_long()
        elif self._should_enter_short():
            self._enter_short()
        
        # 顯示當前狀態
        now = datetime.now().strftime("%H:%M:%S")
        signal = self._get_signal()
        
        if self.position:
            holding_time = time.time() - self.entry_time
            if self.position == 'LONG':
                pnl_pct = ((self.mid_price - self.entry_price) / self.entry_price) * 100
            else:
                pnl_pct = ((self.entry_price - self.mid_price) / self.entry_price) * 100
            
            action = f"持有 {self.position}"
            pnl_str = f"{pnl_pct:+.2f}%"
            print(f"      {now}       | {self.obi:+7.4f} | {signal:^12} | {self.position:^8} | {holding_time:>7.1f}s | {action:^10} | {pnl_str:>7}")
        else:
            print(f"      {now}       | {self.obi:+7.4f} | {signal:^12} | {'空倉':^8} | {'-':>8} | {'觀望':^10} |           ")
    
    def get_summary(self):
        """獲取策略摘要"""
        winning_trades = [t for t in self.trades if t['net_pnl'] > 0]
        win_rate = len(winning_trades) / len(self.trades) * 100 if self.trades else 0
        
        net_pnl = self.balance - 100.0
        roi = net_pnl / 100.0 * 100
        fee_pct = self.total_fees / 100.0 * 100
        
        return {
            'name': self.name,
            'leverage': self.leverage,
            'trades': len(self.trades),
            'win_rate': win_rate,
            'gross_pnl': self.gross_pnl,
            'fees': self.total_fees,
            'net_pnl': net_pnl,
            'roi': roi,
            'fee_pct': fee_pct,
            'final_balance': self.balance
        }


async def test_leverage_strategy(leverage: int, duration: int = 90):
    """測試指定槓桿的策略"""
    strategy = LeverageTestStrategy(
        name=f"{leverage}x 槓桿 HFT (15單/分)",
        leverage=leverage
    )
    
    print(f"\n{'='*100}")
    print(f"🎯 {strategy.name}")
    print(f"{'='*100}")
    print(f"初始資金: 100.0 U | 槓桿: {leverage}x | 倉位: {100*leverage} U | 頻率: 15/分")
    print(f"OBI 閾值: ±0.45 | 測試時長: {duration} 秒")
    print(f"{'='*100}\n")
    print(f"{'時間':^18} | {'OBI':^8} | {'信號':^12} | {'倉位':^8} | {'持倉時間':^12} | {'動作':^10} | {'PnL':^9}")
    print(f"{'='*100}")
    
    uri = "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms"
    start_time = time.time()
    
    async with websockets.connect(uri) as websocket:
        while time.time() - start_time < duration:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                data = json.loads(msg)
                strategy.process_orderbook(data)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"錯誤: {e}")
                break
    
    # 如果還有持倉,強制平倉
    if strategy.position:
        strategy._exit_position("TEST_END|測試結束強制平倉|LOW")
    
    return strategy.get_summary()


async def main():
    """主函數 - 測試不同槓桿倍數"""
    print("="*100)
    print("🎯 HFT 槓桿測試 (保守策略 15單/分)")
    print("="*100)
    print(f"\n配置: 100 USDT | 測試槓桿: 5x, 10x, 20x | 90秒/測試")
    print("優化: 使用 Maker 訂單 (0.02%) + 嚴格進場條件 (±0.45)\n")
    
    leverages = [5, 10, 20]
    results = []
    
    for i, leverage in enumerate(leverages, 1):
        print(f"\n{'🔥'*50}")
        print(f"測試 {i}/{len(leverages)}: {leverage}x 槓桿")
        print(f"{'🔥'*50}")
        
        result = await test_leverage_strategy(leverage, duration=90)
        results.append(result)
        
        print(f"\n⏸️  休息 5 秒...\n")
        await asyncio.sleep(5)
    
    # 顯示對比結果
    print("\n" + "="*100)
    print("📊 槓桿對比總結")
    print("="*100)
    print()
    print(f"{'策略':<30} | {'槓桿':>8} | {'交易次數':>10} | {'勝率':>10} | {'淨ROI':>12} | {'手續費':>12} | {'最終資金':>15}")
    print("-"*100)
    
    for r in results:
        print(f"{r['name']:<30} | {r['leverage']:>7}x | {r['trades']:>10} | {r['win_rate']:>9.1f}% | "
              f"{r['roi']:>11.2f}% | {r['fee_pct']:>11.2f}% | {r['final_balance']:>14.2f} U", end='')
        
        if r['roi'] > max(results, key=lambda x: x['roi'] if x != r else float('-inf'), default={'roi': float('-inf')}).get('roi', float('-inf')):
            print(" 🏆")
        else:
            print()
    
    print()
    print("="*100)
    best = max(results, key=lambda x: x['roi'])
    print(f"🏆 最佳槓桿: {best['leverage']}x (淨ROI: {best['roi']:.2f}%)")
    print("="*100)
    
    # 詳細分析
    print("\n📈 槓桿影響分析:")
    print("-" * 100)
    for r in results:
        pnl_status = "✅ 獲利" if r['roi'] > 0 else "❌ 虧損"
        print(f"\n{r['leverage']}x 槓桿: {pnl_status}")
        print(f"  • 交易次數: {r['trades']}")
        print(f"  • 勝率: {r['win_rate']:.1f}%")
        print(f"  • 毛利: {r['gross_pnl']:+.2f} USDT ({r['gross_pnl']/100*100:+.2f}%)")
        print(f"  • 手續費: -{r['fees']:.2f} USDT ({r['fee_pct']:.2f}%)")
        print(f"  • 淨利: {r['net_pnl']:+.2f} USDT ({r['roi']:+.2f}%)")
        print(f"  • 最終資金: {r['final_balance']:.2f} USDT")


if __name__ == "__main__":
    asyncio.run(main())
