"""
OBI 實時交易演示
使用真實 Binance WebSocket 數據進行 OBI 買賣決策模擬
"""

import sys
import os
import asyncio
from datetime import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.exchange.obi_calculator import OBICalculator, OBISignal, ExitSignalType


class OBITradingDemo:
    """OBI 交易演示系統"""
    
    def __init__(self):
        self.calculator = OBICalculator(
            symbol="BTCUSDT",
            depth_limit=20,
            exit_obi_threshold=0.2,
            exit_trend_periods=5,
            extreme_regression_threshold=0.5
        )
        
        # 交易狀態
        self.position = None  # None / 'LONG' / 'SHORT'
        self.entry_price = None
        self.entry_obi = None
        self.entry_time = None
        
        # 統計
        self.trades = []
        self.total_pnl = 0.0
        self.update_count = 0
        
        # 模擬資金
        self.capital = 10000  # 10,000 USDT
        self.position_size = 0.1  # 0.1 BTC
        
        # 註冊回調
        self.calculator.on_obi_update = self.on_obi_update
        self.calculator.on_exit_signal = self.on_exit_signal
    
    def print_header(self):
        """打印表頭"""
        print("\n" + "="*100)
        print(f"{'時間':^20} | {'OBI':^8} | {'信號':^12} | {'趨勢':^12} | {'倉位':^8} | {'動作':^15} | {'PnL':^10}")
        print("="*100)
    
    def print_status(self, obi, signal, trend, action="", pnl_str=""):
        """打印當前狀態"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        position_str = self.position if self.position else "空倉"
        
        # 顏色編碼（終端機支援）
        if signal in [OBISignal.STRONG_BUY, OBISignal.BUY]:
            signal_color = f"\033[92m{signal.value}\033[0m"  # 綠色
        elif signal in [OBISignal.STRONG_SELL, OBISignal.SELL]:
            signal_color = f"\033[91m{signal.value}\033[0m"  # 紅色
        else:
            signal_color = signal.value
        
        if action:
            action_color = f"\033[93m{action}\033[0m"  # 黃色
        else:
            action_color = action
        
        print(f"{timestamp:^20} | {obi:^+8.4f} | {signal_color:^12} | {trend if trend else 'N/A':^12} | "
              f"{position_str:^8} | {action_color:^15} | {pnl_str:^10}")
    
    def on_obi_update(self, data):
        """OBI 更新回調"""
        self.update_count += 1
        
        obi = data['obi']
        signal = OBISignal(data['signal'])
        trend = data.get('trend')
        
        # 每 10 次更新顯示一次
        if self.update_count % 10 != 0:
            return
        
        # 檢查進場機會（如果沒有持倉）
        if not self.position:
            if signal == OBISignal.STRONG_BUY and obi > 0.35:
                self.enter_long(obi)
                self.print_status(obi, signal, trend, "🟢 開多單", "")
            elif signal == OBISignal.STRONG_SELL and obi < -0.35:
                self.enter_short(obi)
                self.print_status(obi, signal, trend, "🔴 開空單", "")
            else:
                self.print_status(obi, signal, trend, "觀望", "")
        
        # 如果有持倉，顯示當前 PnL
        else:
            pnl = self.calculate_unrealized_pnl()
            pnl_str = f"{pnl:+.2f}%"
            self.print_status(obi, signal, trend, f"持有 {self.position}", pnl_str)
    
    def on_exit_signal(self, data):
        """離場訊號回調"""
        if not self.position:
            return
        
        signal_type = data['signal_type']
        details = data['details']
        
        print(f"\n🚨 離場訊號: {signal_type}")
        print(f"   原因: {details['reason']}")
        print(f"   嚴重性: {details['severity']}")
        
        # 出場
        self.exit_position(details['current_obi'])
    
    def enter_long(self, obi):
        """開多單"""
        self.position = 'LONG'
        self.entry_price = 106000  # 模擬當前價格（實際應從訂單簿獲取）
        self.entry_obi = obi
        self.entry_time = datetime.now()
        
        # 設置 OBI 計算器的持倉狀態
        self.calculator.set_position('LONG', obi)
        
        print(f"\n✅ 開多單")
        print(f"   進場價格: {self.entry_price:.2f} USDT")
        print(f"   進場 OBI: {obi:.4f}")
        print(f"   倉位大小: {self.position_size} BTC")
    
    def enter_short(self, obi):
        """開空單"""
        self.position = 'SHORT'
        self.entry_price = 106000  # 模擬當前價格
        self.entry_obi = obi
        self.entry_time = datetime.now()
        
        # 設置 OBI 計算器的持倉狀態
        self.calculator.set_position('SHORT', obi)
        
        print(f"\n✅ 開空單")
        print(f"   進場價格: {self.entry_price:.2f} USDT")
        print(f"   進場 OBI: {obi:.4f}")
        print(f"   倉位大小: {self.position_size} BTC")
    
    def exit_position(self, current_obi):
        """平倉"""
        if not self.position:
            return
        
        exit_price = 106100  # 模擬當前價格（實際應從訂單簿獲取）
        holding_time = (datetime.now() - self.entry_time).total_seconds()
        
        # 計算 PnL
        if self.position == 'LONG':
            pnl_pct = (exit_price - self.entry_price) / self.entry_price * 100
        else:  # SHORT
            pnl_pct = (self.entry_price - exit_price) / self.entry_price * 100
        
        pnl_usdt = self.entry_price * self.position_size * (pnl_pct / 100)
        
        # 記錄交易
        trade = {
            'position': self.position,
            'entry_price': self.entry_price,
            'exit_price': exit_price,
            'entry_obi': self.entry_obi,
            'exit_obi': current_obi,
            'pnl_pct': pnl_pct,
            'pnl_usdt': pnl_usdt,
            'holding_time': holding_time
        }
        self.trades.append(trade)
        self.total_pnl += pnl_usdt
        
        print(f"\n✅ 平倉: {self.position}")
        print(f"   出場價格: {exit_price:.2f} USDT")
        print(f"   出場 OBI: {current_obi:.4f}")
        print(f"   持倉時間: {holding_time:.1f} 秒")
        print(f"   本次 PnL: {pnl_pct:+.2f}% ({pnl_usdt:+.2f} USDT)")
        print(f"   累計 PnL: {self.total_pnl:+.2f} USDT")
        
        # 重置狀態
        self.position = None
        self.entry_price = None
        self.entry_obi = None
        self.entry_time = None
        self.calculator.set_position(None)
    
    def calculate_unrealized_pnl(self):
        """計算未實現 PnL"""
        if not self.position:
            return 0.0
        
        current_price = 106050  # 模擬當前價格
        
        if self.position == 'LONG':
            pnl_pct = (current_price - self.entry_price) / self.entry_price * 100
        else:  # SHORT
            pnl_pct = (self.entry_price - current_price) / self.entry_price * 100
        
        return pnl_pct
    
    def print_summary(self):
        """打印交易總結"""
        print("\n" + "="*100)
        print("交易總結")
        print("="*100)
        
        if not self.trades:
            print("沒有完成的交易")
            return
        
        win_trades = [t for t in self.trades if t['pnl_pct'] > 0]
        loss_trades = [t for t in self.trades if t['pnl_pct'] <= 0]
        
        print(f"\n總交易次數: {len(self.trades)}")
        print(f"獲利次數: {len(win_trades)}")
        print(f"虧損次數: {len(loss_trades)}")
        print(f"勝率: {len(win_trades)/len(self.trades)*100:.1f}%")
        print(f"\n累計 PnL: {self.total_pnl:+.2f} USDT")
        print(f"平均 PnL: {self.total_pnl/len(self.trades):+.2f} USDT")
        
        if win_trades:
            avg_win = sum(t['pnl_usdt'] for t in win_trades) / len(win_trades)
            print(f"平均獲利: +{avg_win:.2f} USDT")
        
        if loss_trades:
            avg_loss = sum(t['pnl_usdt'] for t in loss_trades) / len(loss_trades)
            print(f"平均虧損: {avg_loss:.2f} USDT")
        
        print("\n交易明細:")
        print("-" * 100)
        for i, trade in enumerate(self.trades, 1):
            print(f"{i}. {trade['position']:5} | "
                  f"進場 OBI: {trade['entry_obi']:+.4f} | "
                  f"出場 OBI: {trade['exit_obi']:+.4f} | "
                  f"PnL: {trade['pnl_pct']:+.2f}% ({trade['pnl_usdt']:+.2f} USDT) | "
                  f"持倉: {trade['holding_time']:.1f}s")
    
    async def run(self, duration=60):
        """運行交易演示
        
        Args:
            duration: 運行時長（秒），0 表示無限運行
        """
        print("\n" + "🎯"*50)
        print(" " * 40 + "OBI 實時交易演示")
        print("🎯"*50)
        
        print(f"\n📊 初始設定:")
        print(f"   交易對: BTCUSDT")
        print(f"   初始資金: {self.capital} USDT")
        print(f"   倉位大小: {self.position_size} BTC")
        print(f"   進場條件: OBI > 0.35 (STRONG_BUY) 或 OBI < -0.35 (STRONG_SELL)")
        print(f"   離場條件: OBI 翻轉 / 趨勢轉弱 / 極端回歸 / 劇烈變化")
        print(f"   運行時長: {duration} 秒" if duration > 0 else "   運行時長: 無限（按 Ctrl+C 停止）")
        
        self.print_header()
        
        # 啟動 WebSocket
        try:
            if duration > 0:
                # 有時限運行
                await asyncio.wait_for(
                    self.calculator.start_websocket(),
                    timeout=duration
                )
            else:
                # 無限運行
                await self.calculator.start_websocket()
        
        except asyncio.TimeoutError:
            print(f"\n⏰ 運行時間到 ({duration} 秒)")
        
        except KeyboardInterrupt:
            print(f"\n⚠️ 用戶中斷")
        
        finally:
            self.calculator.stop_websocket()
            
            # 如果還有持倉，強制平倉
            if self.position:
                print("\n⚠️ 強制平倉")
                current_obi = self.calculator.get_current_obi()
                if current_obi:
                    self.exit_position(current_obi['obi'])
            
            # 打印總結
            self.print_summary()
            
            print("\n" + "="*100)
            print(" " * 40 + "✨ 演示完成 ✨")
            print("="*100 + "\n")


async def main():
    """主函數"""
    import argparse
    
    parser = argparse.ArgumentParser(description='OBI 實時交易演示')
    parser.add_argument('--duration', type=int, default=60,
                       help='運行時長（秒），0 表示無限運行（預設: 60）')
    args = parser.parse_args()
    
    demo = OBITradingDemo()
    await demo.run(duration=args.duration)


if __name__ == "__main__":
    asyncio.run(main())
