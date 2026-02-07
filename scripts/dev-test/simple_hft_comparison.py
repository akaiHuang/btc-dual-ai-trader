#!/usr/bin/env python3
"""
HFT 策略績效對比工具（包含真實手續費計算）
簡化版 - 直接使用 WebSocket 訂閱行情
"""

import asyncio
import websockets
import json
from datetime import datetime
from typing import List, Dict, Optional


class TradingFees:
    """交易手續費計算器"""
    
    MAKER_FEE = 0.0002  # 0.02% (掛單)
    TAKER_FEE = 0.0004  # 0.04% (吃單)
    FUNDING_RATE_PER_SEC = 0.0001 / (8 * 3600)  # 0.01% per 8h
    SLIPPAGE = 0.0001  # 0.01% (優化後降低滑點預估)
    
    @classmethod
    def entry_cost(cls, position_value: float, use_maker: bool = True) -> float:
        """進場成本 (使用 Maker 訂單降低成本)"""
        fee = cls.MAKER_FEE if use_maker else cls.TAKER_FEE
        return position_value * (fee + cls.SLIPPAGE)
    
    @classmethod
    def exit_cost(cls, position_value: float, holding_seconds: float, use_maker: bool = True) -> float:
        """離場成本"""
        fee = cls.MAKER_FEE if use_maker else cls.TAKER_FEE
        trading_fee = position_value * fee
        funding_fee = position_value * cls.FUNDING_RATE_PER_SEC * holding_seconds
        slippage = position_value * cls.SLIPPAGE
        return trading_fee + funding_fee + slippage


class HFTStrategy:
    """HFT 策略回測"""
    
    def __init__(
        self,
        name: str,
        initial_capital: float = 100.0,
        leverage: int = 5,
        frequency: float = 0.42,  # orders/sec
        obi_threshold: float = 0.35,
        duration: int = 90
    ):
        self.name = name
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.leverage = leverage
        self.frequency = frequency
        self.obi_threshold = obi_threshold
        self.duration = duration
        
        # 倉位大小
        self.position_size = (initial_capital * leverage) * 0.9
        
        # 交易記錄
        self.trades: List[Dict] = []
        self.position: Optional[Dict] = None
        
        # 費用追蹤
        self.total_fees = 0.0
        
        # 運行狀態
        self.start_time = None
        self.last_trade_time = 0
        self.update_count = 0
        
        # OBI 計算
        self.obi = 0.0
        self.mid_price = 0.0
    
    def calculate_obi(self, bids: List, asks: List) -> float:
        """計算 OBI"""
        if not bids or not asks:
            return 0.0
        
        bid_volume = sum(float(qty) for _, qty in bids[:20])
        ask_volume = sum(float(qty) for _, qty in asks[:20])
        total_volume = bid_volume + ask_volume
        
        if total_volume == 0:
            return 0.0
        
        obi = (bid_volume - ask_volume) / total_volume
        
        # 計算中間價
        best_bid = float(bids[0][0]) if bids else 0
        best_ask = float(asks[0][0]) if asks else 0
        self.mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
        
        return obi
    
    async def process_orderbook(self, data: dict):
        """處理訂單簿數據"""
        self.update_count += 1
        
        # 計算 OBI
        bids = data.get('bids', [])
        asks = data.get('asks', [])
        self.obi = self.calculate_obi(bids, asks)
        
        current_time = datetime.now().timestamp()
        elapsed = current_time - self.start_time
        
        # 獲取 OBI 信號
        signal = self._get_signal()
        
        # 檢查是否需要離場
        if self.position:
            exit_reason = self._should_exit()
            if exit_reason:
                await self._exit_position(exit_reason)
                return
            
            # 打印持倉狀態
            holding_time = elapsed - self.position['entry_time']
            price_change = (self.mid_price - self.position['entry_price']) / self.position['entry_price']
            if self.position['side'] == 'SHORT':
                price_change = -price_change
            pnl_pct = price_change * 100
            
            print(f"      {datetime.now().strftime('%H:%M:%S')}       "
                  f"|  {self.obi:+.4f}   "
                  f"| {signal:^12} "
                  f"| {self.position['side']:^8} "
                  f"| {holding_time:>6.1f}s "
                  f"| 持有 {self.position['side']} "
                  f"| {pnl_pct:>+7.2f}%")
        else:
            # 檢查是否可以進場 (控制頻率)
            min_interval = 1.0 / self.frequency
            if elapsed - self.last_trade_time < min_interval:
                # 打印觀望狀態
                print(f"      {datetime.now().strftime('%H:%M:%S')}       "
                      f"|  {self.obi:+.4f}   "
                      f"| {signal:^12} "
                      f"|    空倉    "
                      f"|     -      "
                      f"|   觀望   "
                      f"|           ")
                return
            
            # 進場邏輯 (使用更嚴格的條件)
            if self.obi > self.obi_threshold:
                await self._enter_long()
                self.last_trade_time = elapsed
            elif self.obi < -self.obi_threshold:
                await self._enter_short()
                self.last_trade_time = elapsed
            else:
                # 打印觀望狀態
                print(f"      {datetime.now().strftime('%H:%M:%S')}       "
                      f"|  {self.obi:+.4f}   "
                      f"| {signal:^12} "
                      f"|    空倉    "
                      f"|     -      "
                      f"|   觀望   "
                      f"|           ")
    
    def _get_signal(self) -> str:
        """獲取 OBI 信號"""
        if self.obi > 0.5:
            return "STRONG_BUY"
        elif self.obi > 0.2:
            return "BUY"
        elif self.obi < -0.5:
            return "STRONG_SELL"
        elif self.obi < -0.2:
            return "SELL"
        else:
            return "NEUTRAL"
    
    def _should_exit(self) -> Optional[str]:
        """檢查是否應該離場,返回離場原因"""
        if not self.position:
            return None
        
        side = self.position['side']
        entry_obi = self.position['entry_obi']
        
        # OBI 翻轉 (HIGH priority)
        if side == 'LONG' and self.obi < -0.1:
            return "OBI_REVERSAL - OBI 翻負，賣盤開始堆積"
        if side == 'SHORT' and self.obi > 0.1:
            return "OBI_REVERSAL - OBI 翻正，買盤開始堆積"
        
        # OBI 趨勢轉弱 (MEDIUM priority)
        if side == 'LONG' and self.obi < entry_obi - 0.15:
            return "OBI_WEAKENING - OBI 趨勢轉弱，買盤力量減退"
        if side == 'SHORT' and self.obi > entry_obi + 0.15:
            return "OBI_WEAKENING - OBI 趨勢轉強，賣盤力量減退"
        
        # 極端回歸 (從極端值回落)
        if side == 'LONG' and entry_obi > 0.7 and self.obi < 0.5:
            return "EXTREME_REGRESSION - 從極端買盤高位回落"
        if side == 'SHORT' and entry_obi < -0.7 and self.obi > -0.5:
            return "EXTREME_REGRESSION - 從極端賣盤低位反彈"
        
        return None
    
    async def _enter_long(self):
        """開多單"""
        if self.position:
            return
        
        # 計算交易成本
        entry_cost = TradingFees.entry_cost(self.position_size)
        self.capital -= entry_cost
        self.total_fees += entry_cost
        
        self.position = {
            'side': 'LONG',
            'entry_price': self.mid_price,
            'entry_time': datetime.now().timestamp() - self.start_time,
            'entry_obi': self.obi
        }
        
        # 打印進場信息
        print(f"\n✅ 開多單 [{self.name}]")
        print(f"   進場價格: {self.mid_price:.2f} USDT")
        print(f"   進場 OBI: {self.obi:+.4f}")
        print(f"   倉位大小: {self.position_size:.2f} USDT")
        print(f"      {datetime.now().strftime('%H:%M:%S')}       "
              f"|  {self.obi:+.4f}   "
              f"| {self._get_signal():^12} "
              f"|   LONG   "
              f"|    0.0s    "
              f"| 🟢 開多單  "
              f"|           ")
    
    async def _enter_short(self):
        """開空單"""
        if self.position:
            return
        
        # 計算交易成本
        entry_cost = TradingFees.entry_cost(self.position_size)
        self.capital -= entry_cost
        self.total_fees += entry_cost
        
        self.position = {
            'side': 'SHORT',
            'entry_price': self.mid_price,
            'entry_time': datetime.now().timestamp() - self.start_time,
            'entry_obi': self.obi
        }
        
        # 打印進場信息
        print(f"\n✅ 開空單 [{self.name}]")
        print(f"   進場價格: {self.mid_price:.2f} USDT")
        print(f"   進場 OBI: {self.obi:+.4f}")
        print(f"   倉位大小: {self.position_size:.2f} USDT")
        print(f"      {datetime.now().strftime('%H:%M:%S')}       "
              f"|  {self.obi:+.4f}   "
              f"| {self._get_signal():^12} "
              f"|  SHORT   "
              f"|    0.0s    "
              f"| 🔴 開空單  "
              f"|           ")
    
    async def _exit_position(self, reason: str = "時間到"):
        """平倉"""
        if not self.position:
            return
        
        side = self.position['side']
        entry_price = self.position['entry_price']
        entry_time = self.position['entry_time']
        
        # 計算持倉時間
        current_time = datetime.now().timestamp() - self.start_time
        holding_time = current_time - entry_time
        
        # 計算價格變動
        if side == 'LONG':
            price_change_pct = (self.mid_price - entry_price) / entry_price
        else:  # SHORT
            price_change_pct = (entry_price - self.mid_price) / entry_price
        
        # 計算毛利
        gross_pnl = self.position_size * price_change_pct
        
        # 計算所有手續費
        exit_cost = TradingFees.exit_cost(self.position_size, holding_time)
        net_pnl = gross_pnl - exit_cost
        
        # 更新資金
        self.capital += net_pnl
        self.total_fees += exit_cost
        
        # 記錄交易
        self.trades.append({
            'side': side,
            'holding_time': holding_time,
            'gross_pnl': gross_pnl,
            'net_pnl': net_pnl,
            'fees': exit_cost
        })
        
        # 打印離場信息
        if "時間到" not in reason:
            reason_type = reason.split(" - ")[0] if " - " in reason else reason
            reason_desc = reason.split(" - ")[1] if " - " in reason else reason
            severity = "HIGH" if "REVERSAL" in reason_type else "MEDIUM"
            
            print(f"\n🚨 離場訊號: {reason_type}")
            print(f"   原因: {reason_desc}")
            print(f"   嚴重性: {severity}")
        
        cumulative_pnl = self.capital - self.initial_capital
        
        print(f"\n✅ 平倉: {side}")
        print(f"   出場價格: {self.mid_price:.2f} USDT")
        print(f"   持倉時間: {holding_time:.1f} 秒")
        print(f"   本次毛利: {gross_pnl:+.2f} USDT ({price_change_pct*100:+.2f}%)")
        print(f"   手續費: -{exit_cost:.2f} USDT")
        print(f"   本次淨利: {net_pnl:+.2f} USDT")
        print(f"   累計 PnL: {cumulative_pnl:+.2f} USDT\n")
        
        self.position = None
    
    async def run(self):
        """運行回測"""
        print(f"\n{'='*100}")
        print(f"🎯 {self.name}")
        print(f"{'='*100}")
        print(f"初始資金: {self.initial_capital} U | 槓桿: {self.leverage}x | "
              f"倉位: {self.position_size:.0f} U | 頻率: {self.frequency*60:.0f}/分")
        print(f"OBI 閾值: ±{self.obi_threshold} | 測試時長: {self.duration} 秒")
        print(f"{'='*100}\n")
        
        # 打印表頭
        print(f"{'時間':^20} | {'OBI':^12} | {'信號':^12} | {'倉位':^10} | {'持倉時間':^12} | {'動作':^12} | {'PnL':^10}")
        print("="*100)
        
        self.start_time = datetime.now().timestamp()
        
        # 連接 WebSocket
        uri = "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms"
        
        async with websockets.connect(uri) as ws:
            end_time = self.start_time + self.duration
            
            while datetime.now().timestamp() < end_time:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(msg)
                    await self.process_orderbook(data)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    print(f"\n錯誤: {e}")
                    continue
        
        # 強制平倉
        if self.position:
            print(f"\n⏰ 運行時間到 ({self.duration} 秒)")
            print("⚠️  強制平倉\n")
            await self._exit_position("時間到")
        
        return self.get_results()
    
    def get_results(self) -> Dict:
        """獲取回測結果"""
        print(f"\n\n{'='*100}")
        print(f"📊 {self.name} - 結果")
        print(f"{'='*100}\n")
        
        total_trades = len(self.trades)
        if total_trades == 0:
            print("⚠️  無交易記錄")
            return {'strategy': self.name, 'total_trades': 0}
        
        win_trades = len([t for t in self.trades if t['net_pnl'] > 0])
        win_rate = win_trades / total_trades * 100
        
        gross_pnl = sum(t['gross_pnl'] for t in self.trades)
        net_pnl = self.capital - self.initial_capital
        
        roi_gross = gross_pnl / self.initial_capital * 100
        roi_net = net_pnl / self.initial_capital * 100
        fee_impact = self.total_fees / self.initial_capital * 100
        
        avg_holding = sum(t['holding_time'] for t in self.trades) / total_trades
        
        print(f"交易次數: {total_trades} | 勝率: {win_rate:.1f}% | 平均持倉: {avg_holding:.1f}s")
        print(f"毛利: {gross_pnl:+.2f} U ({roi_gross:+.2f}%)")
        print(f"手續費: -{self.total_fees:.2f} U ({fee_impact:.2f}%)")
        print(f"淨利: {net_pnl:+.2f} U ({roi_net:+.2f}%)")
        print(f"最終資金: {self.capital:.2f} U")
        
        # 年化收益
        periods_per_year = (365 * 24 * 3600) / self.duration
        ann_return = roi_net * periods_per_year
        print(f"年化收益: {ann_return:+.0f}%")
        
        print(f"{'='*100}\n")
        
        return {
            'strategy': self.name,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'roi_net': roi_net,
            'fee_impact': fee_impact,
            'final_capital': self.capital
        }


async def main():
    """主函數"""
    print("\n" + "="*100)
    print("🎯 HFT 策略績效對比 (含真實手續費)")
    print("="*100)
    print("\n配置: 100 USDT | 5x 槓桿 | 90秒/策略")
    print("優化: 使用 Maker 訂單 (0.02%) + 更嚴格進場條件\n")
    
    strategies = [
        {
            'name': '保守 HFT (15單/分)',
            'frequency': 0.25,  # 15/60
            'threshold': 0.45   # 更嚴格的閾值
        },
        {
            'name': '穩健 HFT (30單/分)',
            'frequency': 0.5,   # 30/60
            'threshold': 0.40
        },
        {
            'name': '激進 HFT (60單/分)',
            'frequency': 1.0,   # 60/60
            'threshold': 0.38
        }
    ]
    
    results = []
    
    for i, config in enumerate(strategies, 1):
        print(f"\n{'🔥'*50}")
        print(f"測試 {i}/3: {config['name']}")
        print(f"{'🔥'*50}")
        
        strategy = HFTStrategy(
            name=config['name'],
            initial_capital=100.0,
            leverage=5,
            frequency=config['frequency'],
            obi_threshold=config['threshold'],
            duration=90
        )
        
        result = await strategy.run()
        results.append(result)
        
        if i < len(strategies):
            print(f"⏳ 等待 5 秒...\n")
            await asyncio.sleep(5)
    
    # 對比總結
    print("\n" + "="*100)
    print("📊 策略對比總結")
    print("="*100 + "\n")
    
    print(f"{'策略':<25} | {'交易次數':>8} | {'勝率':>8} | {'淨ROI':>10} | {'手續費':>10} | {'最終資金':>12}")
    print("-"*100)
    
    best = max(results, key=lambda x: x.get('roi_net', -999))
    
    for r in results:
        if r['total_trades'] > 0:
            marker = " 🏆" if r == best else ""
            print(f"{r['strategy']:<25} | "
                  f"{r['total_trades']:>8} | "
                  f"{r['win_rate']:>7.1f}% | "
                  f"{r['roi_net']:>+9.2f}% | "
                  f"{r['fee_impact']:>9.2f}% | "
                  f"{r['final_capital']:>11.2f} U{marker}")
    
    print("\n" + "="*100)
    print(f"🏆 最佳策略: {best['strategy']} (淨ROI: {best['roi_net']:+.2f}%)")
    print("="*100 + "\n")


if __name__ == '__main__':
    asyncio.run(main())
