#!/usr/bin/env python3
"""
出場策略對比測試
方案 A: 增加持倉時間 (放寬 OBI 回撤容忍度)
方案 B: 設置最小利潤目標 (不到目標不平倉)
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


class ExitStrategy:
    """可配置的出場策略"""
    
    def __init__(self, name: str, strategy_type: str, params: dict):
        self.name = name
        self.strategy_type = strategy_type
        self.params = params
        
        self.balance = 100.0
        self.leverage = 5
        self.frequency = 12  # 5單/分
        self.entry_threshold = 0.50
        
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
    
    def _get_current_pnl_pct(self):
        """獲取當前浮動盈虧百分比"""
        if self.position is None:
            return 0
        
        if self.position == 'LONG':
            return (self.mid_price - self.entry_price) / self.entry_price
        else:
            return (self.entry_price - self.mid_price) / self.entry_price
    
    def _should_enter_long(self):
        return (self.position is None and 
                time.time() - self.last_trade_time >= self.frequency and
                self.obi > self.entry_threshold)
    
    def _should_enter_short(self):
        return (self.position is None and 
                time.time() - self.last_trade_time >= self.frequency and
                self.obi < -self.entry_threshold)
    
    def _should_exit(self):
        """根據策略類型判斷是否出場"""
        if self.position is None:
            return False, None
        
        current_pnl_pct = self._get_current_pnl_pct()
        
        # 策略 A: 增加持倉時間 (放寬 OBI 回撤)
        if self.strategy_type == 'LONGER_HOLD':
            obi_reversal_threshold = self.params['obi_reversal']
            obi_weakening_threshold = self.params['obi_weakening']
            
            # 強制止損: 虧損超過閾值
            if current_pnl_pct < self.params['stop_loss']:
                return True, f"STOP_LOSS|止損 {current_pnl_pct*100:.2f}%"
            
            # OBI 強烈反轉才出場
            if self.position == 'LONG' and self.obi < -obi_reversal_threshold:
                return True, f"OBI_REVERSAL|OBI強烈反轉 {self.obi:.3f}"
            if self.position == 'SHORT' and self.obi > obi_reversal_threshold:
                return True, f"OBI_REVERSAL|OBI強烈反轉 {self.obi:.3f}"
            
            # OBI 大幅減弱才出場
            if self.position == 'LONG' and self.obi < self.entry_obi - obi_weakening_threshold:
                return True, f"OBI_WEAKENING|OBI減弱 {self.obi:.3f}"
            if self.position == 'SHORT' and self.obi > self.entry_obi + obi_weakening_threshold:
                return True, f"OBI_WEAKENING|OBI減弱 {self.obi:.3f}"
        
        # 策略 B: 追求最小利潤目標
        elif self.strategy_type == 'PROFIT_TARGET':
            min_profit = self.params['min_profit']
            stop_loss = self.params['stop_loss']
            
            # 達到利潤目標,立即出場
            if current_pnl_pct >= min_profit:
                return True, f"PROFIT_TARGET|達標 {current_pnl_pct*100:.2f}%"
            
            # 止損
            if current_pnl_pct < stop_loss:
                return True, f"STOP_LOSS|止損 {current_pnl_pct*100:.2f}%"
            
            # 如果沒達到利潤目標,但 OBI 強烈反轉,還是要出場
            if self.position == 'LONG' and self.obi < -0.3:
                return True, f"FORCE_EXIT|OBI反轉 {self.obi:.3f}"
            if self.position == 'SHORT' and self.obi > 0.3:
                return True, f"FORCE_EXIT|OBI反轉 {self.obi:.3f}"
        
        return False, None
    
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
        
        print(f"✅ 開多 | 價:{self.entry_price:.0f} | OBI:{self.entry_obi:+.3f} | 倉:{self.position_size:.0f}U")
    
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
        
        print(f"✅ 開空 | 價:{self.entry_price:.0f} | OBI:{self.entry_obi:+.3f} | 倉:{self.position_size:.0f}U")
    
    def _exit_position(self, reason: str = None):
        if self.position is None:
            return
        
        holding_time = time.time() - self.entry_time
        pnl_pct = self._get_current_pnl_pct()
        
        gross = self.position_size * pnl_pct
        exit_fee = TradingFees.exit_cost(self.position_size, holding_time)
        net = gross - exit_fee
        
        self.gross_pnl += gross
        self.balance += net
        self.total_fees += exit_fee
        
        self.trades.append({
            'side': self.position,
            'holding_time': holding_time,
            'gross': gross,
            'net': net,
            'reason': reason or 'UNKNOWN'
        })
        
        reason_display = reason.split('|')[1] if reason and '|' in reason else reason or ''
        print(f"平倉 {self.position} | 持:{holding_time:.0f}s | {reason_display} | 毛:{gross:+.2f}U | 費:-{exit_fee:.2f}U | 淨:{net:+.2f}U | 累:{self.balance-100:+.2f}U")
        
        self.position = None
        self.last_trade_time = time.time()
    
    def process_orderbook(self, data):
        bids = data['bids'][:20]
        asks = data['asks'][:20]
        
        self.mid_price = (float(bids[0][0]) + float(asks[0][0])) / 2
        self.obi = self.calculate_obi(bids, asks)
        
        should_exit, reason = self._should_exit()
        if should_exit:
            self._exit_position(reason)
        elif self._should_enter_long():
            self._enter_long()
        elif self._should_enter_short():
            self._enter_short()
    
    def get_summary(self):
        wins = [t for t in self.trades if t['net'] > 0]
        
        if self.trades:
            avg_holding = sum(t['holding_time'] for t in self.trades) / len(self.trades)
            avg_gross = self.gross_pnl / len(self.trades)
        else:
            avg_holding = 0
            avg_gross = 0
        
        return {
            'name': self.name,
            'type': self.strategy_type,
            'trades': len(self.trades),
            'win_rate': len(wins) / len(self.trades) * 100 if self.trades else 0,
            'gross': self.gross_pnl,
            'fees': self.total_fees,
            'net': self.balance - 100,
            'roi': (self.balance - 100) / 100 * 100,
            'balance': self.balance,
            'avg_holding': avg_holding,
            'avg_gross': avg_gross
        }


async def test_strategy(strategy: ExitStrategy, duration: int = 90):
    """測試策略"""
    
    print(f"\n{'='*80}")
    print(f"🎯 {strategy.name}")
    print(f"{'='*80}")
    
    if strategy.strategy_type == 'LONGER_HOLD':
        print(f"策略: 增加持倉時間")
        print(f"  • OBI反轉閾值: ±{strategy.params['obi_reversal']:.2f}")
        print(f"  • OBI減弱閾值: {strategy.params['obi_weakening']:.2f}")
        print(f"  • 止損: {strategy.params['stop_loss']*100:.2f}%")
    else:
        print(f"策略: 追求最小利潤")
        print(f"  • 利潤目標: {strategy.params['min_profit']*100:.2f}%")
        print(f"  • 止損: {strategy.params['stop_loss']*100:.2f}%")
    
    print(f"\n本金:100U | 槓桿:5x | 頻率:5單/分 | 進場閾值:±0.50 | 時長:{duration}秒\n")
    
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
        strategy._exit_position("TEST_END|測試結束")
    
    return strategy.get_summary()


async def main():
    """主函數"""
    print("="*80)
    print("🎯 出場策略對比測試")
    print("="*80)
    print("\n比較不同出場策略對獲利能力的影響\n")
    
    # 定義測試策略
    strategies = [
        # 原始策略 (基準線)
        ExitStrategy(
            name="原始策略 (基準)",
            strategy_type="LONGER_HOLD",
            params={
                'obi_reversal': 0.1,
                'obi_weakening': 0.15,
                'stop_loss': -0.02
            }
        ),
        
        # 方案 A1: 放寬 OBI 回撤容忍度
        ExitStrategy(
            name="方案A1: 寬鬆出場",
            strategy_type="LONGER_HOLD",
            params={
                'obi_reversal': 0.3,      # 需要更強烈的反轉才出場
                'obi_weakening': 0.25,    # 允許更大的 OBI 回撤
                'stop_loss': -0.03        # 放寬止損
            }
        ),
        
        # 方案 A2: 極度寬鬆
        ExitStrategy(
            name="方案A2: 極度寬鬆",
            strategy_type="LONGER_HOLD",
            params={
                'obi_reversal': 0.5,
                'obi_weakening': 0.35,
                'stop_loss': -0.05
            }
        ),
        
        # 方案 B1: 設置利潤目標 0.15%
        ExitStrategy(
            name="方案B1: 目標0.15%",
            strategy_type="PROFIT_TARGET",
            params={
                'min_profit': 0.0015,     # 0.15% (覆蓋手續費)
                'stop_loss': -0.002
            }
        ),
        
        # 方案 B2: 設置利潤目標 0.20%
        ExitStrategy(
            name="方案B2: 目標0.20%",
            strategy_type="PROFIT_TARGET",
            params={
                'min_profit': 0.0020,     # 0.20%
                'stop_loss': -0.002
            }
        ),
        
        # 方案 B3: 貪心策略 0.30%
        ExitStrategy(
            name="方案B3: 目標0.30%",
            strategy_type="PROFIT_TARGET",
            params={
                'min_profit': 0.0030,     # 0.30%
                'stop_loss': -0.002
            }
        ),
    ]
    
    results = []
    
    for i, strategy in enumerate(strategies, 1):
        print(f"\n{'🔥'*40}")
        print(f"測試 {i}/{len(strategies)}")
        print(f"{'🔥'*40}")
        
        result = await test_strategy(strategy, duration=90)
        results.append(result)
        
        print(f"\n⏸️  休息 5 秒...\n")
        await asyncio.sleep(5)
    
    # 顯示對比
    print("\n" + "="*100)
    print("📊 策略對比總結")
    print("="*100)
    print()
    print(f"{'策略':<25} | {'交易':>6} | {'勝率':>8} | {'平均持倉':>10} | {'毛利':>9} | {'費用':>9} | {'ROI':>9} | {'結果':>6}")
    print("-"*100)
    
    for r in results:
        status = "✅ 賺" if r['roi'] > 0 else "❌ 賠"
        print(f"{r['name']:<25} | {r['trades']:>6} | {r['win_rate']:>7.1f}% | "
              f"{r['avg_holding']:>9.1f}s | {r['gross']:>8.2f}U | {r['fees']:>8.2f}U | "
              f"{r['roi']:>8.2f}% | {status}")
    
    print("\n" + "="*100)
    
    # 分析結果
    best = max(results, key=lambda x: x['roi'])
    
    if best['roi'] > 0:
        print(f"🎉 找到獲利策略!")
        print(f"   最佳: {best['name']} (ROI: {best['roi']:+.2f}%)")
        print(f"   勝率: {best['win_rate']:.1f}%")
        print(f"   平均持倉: {best['avg_holding']:.1f} 秒")
    else:
        print(f"💔 所有策略仍虧損")
        print(f"   最小虧損: {best['name']} (ROI: {best['roi']:+.2f}%)")
    
    print("\n📈 關鍵發現:")
    print("-" * 100)
    
    # 分組分析
    longer_hold = [r for r in results if r['type'] == 'LONGER_HOLD']
    profit_target = [r for r in results if r['type'] == 'PROFIT_TARGET']
    
    if longer_hold:
        print("\n方案 A (增加持倉時間):")
        for r in longer_hold:
            print(f"  • {r['name']}: ROI {r['roi']:+.2f}% | 平均持倉 {r['avg_holding']:.0f}s | 勝率 {r['win_rate']:.0f}%")
    
    if profit_target:
        print("\n方案 B (利潤目標):")
        for r in profit_target:
            print(f"  • {r['name']}: ROI {r['roi']:+.2f}% | 平均持倉 {r['avg_holding']:.0f}s | 勝率 {r['win_rate']:.0f}%")
    
    print("\n💡 策略建議:")
    print("-" * 100)
    
    if best['roi'] > 0:
        print(f"✅ 採用 {best['name']}")
        print(f"   理由: 能夠實現正收益")
    else:
        print("❌ 兩種方案都無法克服手續費問題")
        print("   根本原因: BTC 價格波動不足以覆蓋 0.06% 的手續費成本")
        print("   建議: 考慮更長週期的交易策略 (5分鐘/15分鐘 K線)")
    
    print("="*100)


if __name__ == "__main__":
    asyncio.run(main())
