"""
OBI 多時間框架交易演示
支援不同時間框架策略：1m / 3m / 5m / 15m
"""

import sys
import os
import asyncio
from datetime import datetime, timedelta
from collections import deque
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.exchange.obi_calculator import OBICalculator, OBISignal, ExitSignalType


class TimeframeConfig:
    """時間框架配置"""
    
    CONFIGS = {
        'HFT': {
            'name': '超高頻 (100ms)',
            'update_interval': 0.1,  # 100ms
            'signal_interval': 0.1,  # 每次更新就檢查
            'min_holding_time': 0,   # 無最小持倉時間
            'entry_threshold': 0.35,  # OBI 進場閾值
            'exit_threshold': 0.2,    # OBI 離場閾值
            'description': '極短線剝頭皮，追求毫秒級價差'
        },
        '1m': {
            'name': '1 分鐘線',
            'update_interval': 0.1,   # 仍用 100ms 更新
            'signal_interval': 1.0,   # 每秒檢查一次信號
            'min_holding_time': 5,    # 最少持倉 5 秒
            'entry_threshold': 0.40,  # 稍高閾值，避免假信號
            'exit_threshold': 0.15,   # 更寬容的離場
            'description': '短線交易，持倉 5-60 秒'
        },
        '3m': {
            'name': '3 分鐘線',
            'update_interval': 0.1,
            'signal_interval': 3.0,   # 每 3 秒檢查
            'min_holding_time': 15,   # 最少持倉 15 秒
            'entry_threshold': 0.45,  # 更高閾值
            'exit_threshold': 0.10,   # 寬鬆離場
            'description': '波段交易，持倉 15 秒-3 分鐘'
        },
        '5m': {
            'name': '5 分鐘線',
            'update_interval': 0.1,
            'signal_interval': 5.0,   # 每 5 秒檢查
            'min_holding_time': 30,   # 最少持倉 30 秒
            'entry_threshold': 0.50,  # 高閾值，確保強勢
            'exit_threshold': 0.05,   # 非常寬鬆
            'description': '趨勢跟隨，持倉 30 秒-5 分鐘'
        },
        '15m': {
            'name': '15 分鐘線',
            'update_interval': 0.1,
            'signal_interval': 15.0,  # 每 15 秒檢查
            'min_holding_time': 60,   # 最少持倉 1 分鐘
            'entry_threshold': 0.55,  # 非常高閾值
            'exit_threshold': 0.0,    # 幾乎不用 OBI 離場
            'description': '中期持倉，依賴 TP/SL 而非 OBI'
        }
    }


class MultiTimeframeOBITrading:
    """多時間框架 OBI 交易系統"""
    
    def __init__(self, timeframe='1m'):
        # 載入配置
        if timeframe not in TimeframeConfig.CONFIGS:
            raise ValueError(f"不支援的時間框架: {timeframe}. 可用: {list(TimeframeConfig.CONFIGS.keys())}")
        
        self.config = TimeframeConfig.CONFIGS[timeframe]
        self.timeframe = timeframe
        
        # 初始化 OBI 計算器
        self.calculator = OBICalculator(
            symbol="BTCUSDT",
            depth_limit=20,
            exit_obi_threshold=self.config['exit_threshold'],
            exit_trend_periods=5,
            extreme_regression_threshold=0.5
        )
        
        # 交易狀態
        self.position = None
        self.entry_price = None
        self.entry_obi = None
        self.entry_time = None
        self.last_check_time = datetime.now()
        
        # OBI 歷史（用於平滑信號）
        self.obi_history = deque(maxlen=10)
        self.signal_history = deque(maxlen=5)
        
        # 統計
        self.trades = []
        self.total_pnl = 0.0
        self.update_count = 0
        self.signal_count = 0
        
        # 模擬
        self.capital = 10000
        self.position_size = 0.1
        
        # 回調
        self.calculator.on_obi_update = self.on_obi_update
        self.calculator.on_exit_signal = self.on_exit_signal
    
    def get_smoothed_obi(self):
        """獲取平滑的 OBI（移動平均）"""
        if len(self.obi_history) < 3:
            return self.obi_history[-1] if self.obi_history else 0.0
        
        # 簡單移動平均
        return sum(self.obi_history) / len(self.obi_history)
    
    def should_check_signal(self):
        """判斷是否應該檢查信號（基於時間框架）"""
        now = datetime.now()
        elapsed = (now - self.last_check_time).total_seconds()
        
        if elapsed >= self.config['signal_interval']:
            self.last_check_time = now
            return True
        return False
    
    def should_allow_exit(self):
        """判斷是否允許離場（基於最小持倉時間）"""
        if not self.position:
            return True
        
        holding_time = (datetime.now() - self.entry_time).total_seconds()
        return holding_time >= self.config['min_holding_time']
    
    def print_header(self):
        """打印表頭"""
        print("\n" + "="*110)
        print(f"{'時間':^20} | {'原始OBI':^10} | {'平滑OBI':^10} | {'信號':^12} | {'倉位':^8} | {'持倉時間':^10} | {'動作':^15} | {'PnL':^10}")
        print("="*110)
    
    def print_status(self, raw_obi, smoothed_obi, signal, action="", pnl_str=""):
        """打印當前狀態"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        position_str = self.position if self.position else "空倉"
        
        # 持倉時間
        if self.position:
            holding_time = (datetime.now() - self.entry_time).total_seconds()
            holding_str = f"{holding_time:.1f}s"
        else:
            holding_str = "-"
        
        # 顏色
        if signal in [OBISignal.STRONG_BUY, OBISignal.BUY]:
            signal_color = f"\033[92m{signal.value}\033[0m"
        elif signal in [OBISignal.STRONG_SELL, OBISignal.SELL]:
            signal_color = f"\033[91m{signal.value}\033[0m"
        else:
            signal_color = signal.value
        
        if action:
            action_color = f"\033[93m{action}\033[0m"
        else:
            action_color = action
        
        print(f"{timestamp:^20} | {raw_obi:^+10.4f} | {smoothed_obi:^+10.4f} | {signal_color:^12} | "
              f"{position_str:^8} | {holding_str:^10} | {action_color:^15} | {pnl_str:^10}")
    
    def on_obi_update(self, data):
        """OBI 更新回調"""
        self.update_count += 1
        
        raw_obi = data['obi']
        signal = OBISignal(data['signal'])
        
        # 記錄歷史
        self.obi_history.append(raw_obi)
        smoothed_obi = self.get_smoothed_obi()
        
        # 檢查是否到了信號檢查時間
        if not self.should_check_signal():
            return
        
        self.signal_count += 1
        
        # 檢查進場機會
        if not self.position:
            # 使用平滑 OBI 判斷
            if smoothed_obi > self.config['entry_threshold']:
                self.enter_long(smoothed_obi)
                self.print_status(raw_obi, smoothed_obi, signal, "🟢 開多單", "")
            elif smoothed_obi < -self.config['entry_threshold']:
                self.enter_short(smoothed_obi)
                self.print_status(raw_obi, smoothed_obi, signal, "🔴 開空單", "")
            else:
                self.print_status(raw_obi, smoothed_obi, signal, "觀望", "")
        
        # 持倉中
        else:
            pnl = self.calculate_unrealized_pnl()
            pnl_str = f"{pnl:+.2f}%"
            self.print_status(raw_obi, smoothed_obi, signal, f"持有 {self.position}", pnl_str)
    
    def on_exit_signal(self, data):
        """離場訊號回調"""
        if not self.position:
            return
        
        # 檢查最小持倉時間
        if not self.should_allow_exit():
            holding_time = (datetime.now() - self.entry_time).total_seconds()
            remaining = self.config['min_holding_time'] - holding_time
            print(f"\n⏳ 離場訊號觸發，但未達最小持倉時間（還需 {remaining:.1f} 秒）")
            return
        
        signal_type = data['signal_type']
        details = data['details']
        
        print(f"\n🚨 離場訊號: {signal_type}")
        print(f"   原因: {details['reason']}")
        print(f"   嚴重性: {details['severity']}")
        
        self.exit_position(details['current_obi'])
    
    def enter_long(self, obi):
        """開多單"""
        self.position = 'LONG'
        self.entry_price = 106000
        self.entry_obi = obi
        self.entry_time = datetime.now()
        
        self.calculator.set_position('LONG', obi)
        
        print(f"\n✅ 開多單 [{self.config['name']}]")
        print(f"   進場價格: {self.entry_price:.2f} USDT")
        print(f"   進場 OBI: {obi:.4f} (平滑值)")
        print(f"   最小持倉: {self.config['min_holding_time']} 秒")
    
    def enter_short(self, obi):
        """開空單"""
        self.position = 'SHORT'
        self.entry_price = 106000
        self.entry_obi = obi
        self.entry_time = datetime.now()
        
        self.calculator.set_position('SHORT', obi)
        
        print(f"\n✅ 開空單 [{self.config['name']}]")
        print(f"   進場價格: {self.entry_price:.2f} USDT")
        print(f"   進場 OBI: {obi:.4f} (平滑值)")
        print(f"   最小持倉: {self.config['min_holding_time']} 秒")
    
    def exit_position(self, current_obi):
        """平倉"""
        if not self.position:
            return
        
        exit_price = 106100
        holding_time = (datetime.now() - self.entry_time).total_seconds()
        
        if self.position == 'LONG':
            pnl_pct = (exit_price - self.entry_price) / self.entry_price * 100
        else:
            pnl_pct = (self.entry_price - exit_price) / self.entry_price * 100
        
        pnl_usdt = self.entry_price * self.position_size * (pnl_pct / 100)
        
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
        print(f"   持倉時間: {holding_time:.1f} 秒")
        print(f"   本次 PnL: {pnl_pct:+.2f}% ({pnl_usdt:+.2f} USDT)")
        print(f"   累計 PnL: {self.total_pnl:+.2f} USDT")
        
        self.position = None
        self.entry_price = None
        self.entry_obi = None
        self.entry_time = None
        self.calculator.set_position(None)
    
    def calculate_unrealized_pnl(self):
        """計算未實現 PnL"""
        if not self.position:
            return 0.0
        
        current_price = 106050
        
        if self.position == 'LONG':
            pnl_pct = (current_price - self.entry_price) / self.entry_price * 100
        else:
            pnl_pct = (self.entry_price - current_price) / self.entry_price * 100
        
        return pnl_pct
    
    def print_summary(self):
        """打印交易總結"""
        print("\n" + "="*110)
        print(f"交易總結 [{self.config['name']}]")
        print("="*110)
        
        print(f"\n策略說明: {self.config['description']}")
        print(f"進場閾值: OBI > {self.config['entry_threshold']:.2f} (多) / < {-self.config['entry_threshold']:.2f} (空)")
        print(f"離場閾值: OBI 變化 > {self.config['exit_threshold']:.2f}")
        print(f"最小持倉: {self.config['min_holding_time']} 秒")
        print(f"信號檢查: 每 {self.config['signal_interval']} 秒")
        
        print(f"\n運行統計:")
        print(f"總更新次數: {self.update_count}")
        print(f"信號檢查次數: {self.signal_count}")
        print(f"更新/信號比: {self.update_count/self.signal_count if self.signal_count > 0 else 0:.1f}x")
        
        if not self.trades:
            print("\n⚠️ 沒有完成的交易")
            return
        
        win_trades = [t for t in self.trades if t['pnl_pct'] > 0]
        loss_trades = [t for t in self.trades if t['pnl_pct'] <= 0]
        
        print(f"\n交易績效:")
        print(f"總交易次數: {len(self.trades)}")
        print(f"獲利次數: {len(win_trades)}")
        print(f"虧損次數: {len(loss_trades)}")
        print(f"勝率: {len(win_trades)/len(self.trades)*100:.1f}%")
        
        print(f"\n損益統計:")
        print(f"累計 PnL: {self.total_pnl:+.2f} USDT ({self.total_pnl/self.capital*100:+.2f}%)")
        print(f"平均 PnL: {self.total_pnl/len(self.trades):+.2f} USDT")
        
        if win_trades:
            avg_win = sum(t['pnl_usdt'] for t in win_trades) / len(win_trades)
            avg_win_time = sum(t['holding_time'] for t in win_trades) / len(win_trades)
            print(f"平均獲利: +{avg_win:.2f} USDT (持倉 {avg_win_time:.1f}s)")
        
        if loss_trades:
            avg_loss = sum(t['pnl_usdt'] for t in loss_trades) / len(loss_trades)
            avg_loss_time = sum(t['holding_time'] for t in loss_trades) / len(loss_trades)
            print(f"平均虧損: {avg_loss:.2f} USDT (持倉 {avg_loss_time:.1f}s)")
        
        avg_holding = sum(t['holding_time'] for t in self.trades) / len(self.trades)
        print(f"平均持倉時間: {avg_holding:.1f} 秒")
        
        print("\n交易明細:")
        print("-" * 110)
        for i, trade in enumerate(self.trades, 1):
            print(f"{i:2d}. {trade['position']:5} | "
                  f"進場 OBI: {trade['entry_obi']:+.4f} | "
                  f"出場 OBI: {trade['exit_obi']:+.4f} | "
                  f"PnL: {trade['pnl_pct']:+.2f}% ({trade['pnl_usdt']:+.2f} USDT) | "
                  f"持倉: {trade['holding_time']:.1f}s")
    
    async def run(self, duration=120):
        """運行交易演示"""
        print("\n" + "🎯"*55)
        print(" " * 40 + f"OBI 多時間框架交易演示 - {self.config['name']}")
        print("🎯"*55)
        
        print(f"\n📊 策略配置:")
        print(f"   時間框架: {self.config['name']}")
        print(f"   策略類型: {self.config['description']}")
        print(f"   進場條件: 平滑 OBI > {self.config['entry_threshold']:.2f} (多) / < {-self.config['entry_threshold']:.2f} (空)")
        print(f"   離場條件: OBI 翻轉/轉弱/極端回歸")
        print(f"   最小持倉: {self.config['min_holding_time']} 秒")
        print(f"   信號檢查: 每 {self.config['signal_interval']} 秒")
        print(f"   初始資金: {self.capital} USDT")
        print(f"   運行時長: {duration} 秒")
        
        self.print_header()
        
        try:
            await asyncio.wait_for(
                self.calculator.start_websocket(),
                timeout=duration
            )
        
        except asyncio.TimeoutError:
            print(f"\n⏰ 運行時間到 ({duration} 秒)")
        
        except KeyboardInterrupt:
            print(f"\n⚠️ 用戶中斷")
        
        finally:
            self.calculator.stop_websocket()
            
            if self.position:
                print("\n⚠️ 強制平倉")
                current_obi = self.calculator.get_current_obi()
                if current_obi:
                    self.exit_position(current_obi['obi'])
            
            self.print_summary()
            
            print("\n" + "="*110)
            print(" " * 45 + "✨ 演示完成 ✨")
            print("="*110 + "\n")


async def main():
    """主函數"""
    import argparse
    
    parser = argparse.ArgumentParser(description='OBI 多時間框架交易演示')
    parser.add_argument('--timeframe', type=str, default='1m',
                       choices=['HFT', '1m', '3m', '5m', '15m'],
                       help='時間框架 (預設: 1m)')
    parser.add_argument('--duration', type=int, default=120,
                       help='運行時長（秒），預設 120')
    args = parser.parse_args()
    
    print("\n" + "="*110)
    print(" " * 35 + "🎯 OBI 多時間框架策略對比 🎯")
    print("="*110)
    print("\n可用時間框架:")
    for key, config in TimeframeConfig.CONFIGS.items():
        print(f"  {key:5s} - {config['name']:15s}: {config['description']}")
    print(f"\n當前選擇: {args.timeframe}")
    print("="*110)
    
    demo = MultiTimeframeOBITrading(timeframe=args.timeframe)
    await demo.run(duration=args.duration)


if __name__ == "__main__":
    asyncio.run(main())
