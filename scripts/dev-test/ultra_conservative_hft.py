#!/usr/bin/env python3
"""
超保守 HFT 測試 - 降低交易頻率
測試 5單/分, 3單/分, 2單/分
"""

import asyncio
import json
import time
from datetime import datetime
import websockets


class TradingFees:
    MAKER_FEE = 0.0002
    SLIPPAGE = 0.0001
    FUNDING_RATE = 0.0001
    
    @classmethod
    def entry_cost(cls, position_value):
        return position_value * (cls.MAKER_FEE + cls.SLIPPAGE)
    
    @classmethod
    def exit_cost(cls, position_value, holding_seconds):
        funding = (holding_seconds / (8 * 3600)) * cls.FUNDING_RATE * position_value
        return position_value * (cls.MAKER_FEE + cls.SLIPPAGE) + funding


class ConservativeStrategy:
    def __init__(self, name: str, frequency: float, threshold: float):
        self.name = name
        self.frequency = frequency
        self.threshold = threshold
        self.balance = 100.0
        self.leverage = 5
        
        self.position = None
        self.entry_price = 0
        self.entry_obi = 0
        self.entry_time = 0
        self.position_size = 0
        
        self.trades = []
        self.total_fees = 0
        self.gross_pnl = 0
        
        self.obi = 0
        self.mid_price = 0
        self.last_trade_time = 0
    
    def calculate_obi(self, bids, asks):
        bid_volume = sum(float(price) * float(qty) for price, qty in bids)
        ask_volume = sum(float(price) * float(qty) for price, qty in asks)
        total = bid_volume + ask_volume
        return (bid_volume - ask_volume) / total if total > 0 else 0
    
    def _should_enter_long(self):
        return (self.position is None and 
                time.time() - self.last_trade_time >= self.frequency and
                self.obi > self.threshold)
    
    def _should_enter_short(self):
        return (self.position is None and 
                time.time() - self.last_trade_time >= self.frequency and
                self.obi < -self.threshold)
    
    def _should_exit(self):
        if self.position is None:
            return False
        if self.position == 'LONG' and (self.obi < -0.1 or self.obi < self.entry_obi - 0.15):
            return True
        if self.position == 'SHORT' and (self.obi > 0.1 or self.obi > self.entry_obi + 0.15):
            return True
        return False
    
    def _enter_long(self):
        self.position = 'LONG'
        self.entry_price = self.mid_price
        self.entry_obi = self.obi
        self.entry_time = time.time()
        self.position_size = self.balance * self.leverage
        
        entry_fee = TradingFees.entry_cost(self.position_size)
        self.balance -= entry_fee
        self.total_fees += entry_fee
        self.last_trade_time = time.time()
        
        print(f"✅ 開多 | 價:{self.entry_price:.0f} | OBI:{self.entry_obi:+.3f}")
    
    def _enter_short(self):
        self.position = 'SHORT'
        self.entry_price = self.mid_price
        self.entry_obi = self.obi
        self.entry_time = time.time()
        self.position_size = self.balance * self.leverage
        
        entry_fee = TradingFees.entry_cost(self.position_size)
        self.balance -= entry_fee
        self.total_fees += entry_fee
        self.last_trade_time = time.time()
        
        print(f"✅ 開空 | 價:{self.entry_price:.0f} | OBI:{self.entry_obi:+.3f}")
    
    def _exit_position(self):
        holding_time = time.time() - self.entry_time
        
        if self.position == 'LONG':
            pct = (self.mid_price - self.entry_price) / self.entry_price
        else:
            pct = (self.entry_price - self.mid_price) / self.entry_price
        
        gross = self.position_size * pct
        exit_fee = TradingFees.exit_cost(self.position_size, holding_time)
        net = gross - exit_fee
        
        self.gross_pnl += gross
        self.balance += net
        self.total_fees += exit_fee
        
        self.trades.append({'net': net})
        
        print(f"平倉 {self.position} | 持:{holding_time:.0f}s | 毛:{gross:+.2f} | 費:-{exit_fee:.2f} | 淨:{net:+.2f} | 累:{self.balance-100:+.2f}")
        
        self.position = None
        self.last_trade_time = time.time()
    
    def process_orderbook(self, data):
        bids = data['bids'][:20]
        asks = data['asks'][:20]
        
        self.mid_price = (float(bids[0][0]) + float(asks[0][0])) / 2
        self.obi = self.calculate_obi(bids, asks)
        
        if self._should_exit():
            self._exit_position()
        elif self._should_enter_long():
            self._enter_long()
        elif self._should_enter_short():
            self._enter_short()
    
    def get_summary(self):
        wins = [t for t in self.trades if t['net'] > 0]
        return {
            'name': self.name,
            'trades': len(self.trades),
            'win_rate': len(wins) / len(self.trades) * 100 if self.trades else 0,
            'gross': self.gross_pnl,
            'fees': self.total_fees,
            'net': self.balance - 100,
            'roi': (self.balance - 100) / 100 * 100,
            'balance': self.balance
        }


async def test_strategy(frequency, threshold, duration=90):
    per_min = 60 / frequency
    strategy = ConservativeStrategy(
        name=f"{per_min:.0f}單/分(閾±{threshold})",
        frequency=frequency,
        threshold=threshold
    )
    
    print(f"\n{'='*80}")
    print(f"🎯 {strategy.name}")
    print(f"{'='*80}")
    print(f"頻率:{frequency}秒/次 | 閾值:±{threshold} | 槓桿:5x | 時長:{duration}秒\n")
    
    uri = "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms"
    start = time.time()
    
    async with websockets.connect(uri) as ws:
        while time.time() - start < duration:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                strategy.process_orderbook(json.loads(msg))
            except:
                continue
    
    if strategy.position:
        strategy._exit_position()
    
    return strategy.get_summary()


async def main():
    print("="*80)
    print("🎯 超保守 HFT 測試 - 能否戰勝手續費?")
    print("="*80)
    
    configs = [
        (12, 0.50),  # 5單/分
        (20, 0.48),  # 3單/分
        (30, 0.45),  # 2單/分
    ]
    
    results = []
    for freq, threshold in configs:
        result = await test_strategy(freq, threshold, 90)
        results.append(result)
        print(f"\n休息 5 秒...\n")
        await asyncio.sleep(5)
    
    print("\n" + "="*80)
    print("📊 對比結果")
    print("="*80)
    print(f"{'策略':<20} | {'交易':>6} | {'勝率':>8} | {'毛利':>8} | {'費用':>8} | {'ROI':>8} | {'結果':>6}")
    print("-"*80)
    
    for r in results:
        status = "✅ 賺" if r['roi'] > 0 else "❌ 賠"
        print(f"{r['name']:<20} | {r['trades']:>6} | {r['win_rate']:>7.1f}% | "
              f"{r['gross']:>7.2f}U | {r['fees']:>7.2f}U | {r['roi']:>7.2f}% | {status}")
    
    print("\n" + "="*80)
    best = max(results, key=lambda x: x['roi'])
    if best['roi'] > 0:
        print(f"🎉 找到獲利策略! {best['name']} ROI: {best['roi']:+.2f}%")
    else:
        print(f"💔 最小虧損: {best['name']} ROI: {best['roi']:+.2f}%")
        print("\n原因分析:")
        print(f"  • 手續費佔比: {best['fees']/100*100:.2f}%")
        print(f"  • 毛利佔比: {best['gross']/100*100:.2f}%")
        print(f"  • 問題: 手續費 > 毛利")
    print("="*80)


if __name__ == "__main__":
    asyncio.run(main())
