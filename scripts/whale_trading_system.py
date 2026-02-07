#!/usr/bin/env python3
"""
Whale Trading System v1.0
==========================
整合 Whale Detector v4 + Paper Trader + 學習系統

這個腳本將：
1. 運行 Whale Detector v4 偵測主力策略
2. 當有進場信號時，自動在模擬帳戶下單
3. 追蹤每筆交易結果
4. 動態學習並調整策略參數
5. 持續優化系統表現

Author: AI Assistant
Date: 2025-11-28
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List
import threading
import signal as sig

# 添加專案路徑
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src" / "strategy"))
sys.path.insert(0, str(Path(__file__).parent))

from whale_detector_v4_test import WhaleDetectorV4Test
from whale_paper_trader import WhalePaperTrader, TradeRecord


class WhaleTradingSystem:
    """
    整合交易系統
    """
    
    def __init__(
        self,
        symbol: str = "BTC/USDT",
        check_interval: int = 30,     # 每 30 秒檢查一次
        use_testnet: bool = True
    ):
        self.symbol = symbol
        self.check_interval = check_interval
        self.use_testnet = use_testnet
        
        # 初始化組件 - 使用測試框架
        self.detector = WhaleDetectorV4Test()
        self.trader = WhalePaperTrader(use_testnet=use_testnet)
        
        # 狀態
        self.is_running = False
        self.iteration = 0
        self.current_price = 0.0
        
        # 統計
        self.signals_received = 0
        self.trades_opened = 0
        self.trades_rejected = 0
        
        # 日誌
        self._setup_logging()
        
        # 設置中斷處理
        sig.signal(sig.SIGINT, self._handle_interrupt)
        sig.signal(sig.SIGTERM, self._handle_interrupt)
    
    def _setup_logging(self):
        """設置日誌"""
        log_dir = Path("logs/whale_trading_system")
        log_dir.mkdir(parents=True, exist_ok=True)
        
        log_file = log_dir / f"trading_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
        self.logger = logging.getLogger("WhaleTradingSystem")
        self.logger.setLevel(logging.INFO)
        
        if not self.logger.handlers:
            # 檔案 handler (詳細)
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                '%(asctime)s | %(levelname)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            ))
            self.logger.addHandler(fh)
            
            # 控制台 handler (簡潔)
            ch = logging.StreamHandler()
            ch.setLevel(logging.INFO)
            ch.setFormatter(logging.Formatter(
                '%(asctime)s | %(message)s',
                datefmt='%H:%M:%S'
            ))
            self.logger.addHandler(ch)
        
        self.log_file = log_file
    
    def _handle_interrupt(self, signum, frame):
        """處理中斷信號"""
        print("\n\n⚠️ 收到停止信號，正在安全關閉...")
        self.is_running = False
    
    def analyze_and_trade(self) -> Optional[TradeRecord]:
        """
        執行一次分析，如有信號則下單
        """
        self.iteration += 1
        
        try:
            # 1. 執行策略偵測 (使用測試框架)
            snapshot = self.detector.analyze()
            self.current_price = self.detector.current_price
            
            # 2. 更新現有持倉
            closed_trades = self.trader.update_positions(self.current_price)
            for trade in closed_trades:
                self.logger.info(f"📤 平倉: {trade.trade_id} | {trade.status.value} | ${trade.pnl_usd:+,.2f}")
            
            # 3. 檢查是否有進場信號
            if not snapshot.entry_signal:
                return None
            
            self.signals_received += 1
            entry = snapshot.entry_signal
            
            self.logger.info(f"🔔 收到進場信號:")
            self.logger.info(f"   策略: {snapshot.primary_strategy.strategy.value if snapshot.primary_strategy else 'N/A'}")
            self.logger.info(f"   方向: {entry.direction.value}")
            self.logger.info(f"   機率: {snapshot.primary_strategy.probability:.1%}" if snapshot.primary_strategy else "")
            
            # 4. 構建交易信號
            direction_str = entry.direction.value
            if '多' in direction_str:
                direction = 'LONG'
            elif '空' in direction_str:
                direction = 'SHORT'
            else:
                direction = direction_str.upper()
            
            signal = {
                'strategy': snapshot.primary_strategy.strategy.name if snapshot.primary_strategy else 'UNKNOWN',
                'direction': direction,
                'entry_price': entry.entry_price,
                'take_profit': entry.take_profit,
                'stop_loss': entry.stop_loss,
                'position_size_pct': entry.position_size_pct / 100,  # 轉為小數
                'probability': snapshot.primary_strategy.probability if snapshot.primary_strategy else 0.5,
                'confidence': snapshot.primary_strategy.confidence if snapshot.primary_strategy else 0.5,
                'obi': self.detector.obi,
                'wpi': self.detector.wpi,
                'funding_rate': self.detector.funding_rate
            }
            
            # 5. 嘗試開倉
            trade = self.trader.open_trade(signal)
            
            if trade:
                self.trades_opened += 1
                return trade
            else:
                self.trades_rejected += 1
                return None
                
        except Exception as e:
            self.logger.error(f"❌ 分析錯誤: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    def render_status(self) -> str:
        """渲染狀態儀表板"""
        now = datetime.now().strftime('%H:%M:%S')
        
        # ANSI 顏色
        R = '\033[0m'
        B = '\033[1m'
        g = '\033[32m'
        r = '\033[31m'
        y = '\033[33m'
        c = '\033[36m'
        G_ = '\033[92m'
        R_ = '\033[91m'
        Y_ = '\033[93m'
        C_ = '\033[96m'
        
        lines = []
        lines.append(f"{c}{'='*65}{R}")
        lines.append(f"{C_}{B}🐋 WHALE TRADING SYSTEM v1.0{R}  {y}{now}{R}  {g}#{self.iteration}{R}")
        lines.append(f"{c}{'='*65}{R}")
        
        # 價格
        lines.append(f"\n{B}📊 市場狀態{R}")
        lines.append(f"   BTC 價格: {Y_}${self.current_price:,.2f}{R}")
        
        # 資金
        capital = self.trader.current_capital
        initial = self.trader.config.initial_capital
        pnl = capital - initial
        pnl_pct = pnl / initial * 100
        pnl_color = G_ if pnl >= 0 else R_
        
        lines.append(f"\n{B}💰 資金狀況{R}")
        lines.append(f"   初始資金: ${initial:,.2f}")
        lines.append(f"   當前資金: ${capital:,.2f}")
        lines.append(f"   總盈虧:   {pnl_color}${pnl:+,.2f} ({pnl_pct:+.2f}%){R}")
        
        # 交易統計
        lines.append(f"\n{B}📈 交易統計{R}")
        lines.append(f"   信號數:   {self.signals_received}")
        lines.append(f"   開倉數:   {self.trades_opened}")
        lines.append(f"   拒絕數:   {self.trades_rejected}")
        lines.append(f"   持倉中:   {len(self.trader.open_positions)}")
        
        # 當前持倉
        if self.trader.open_positions:
            lines.append(f"\n{B}📋 當前持倉{R}")
            for trade_id, trade in self.trader.open_positions.items():
                # 計算浮動盈虧
                if trade.direction.value == "LONG":
                    float_pnl = (self.current_price - trade.entry_price) / trade.entry_price
                else:
                    float_pnl = (trade.entry_price - self.current_price) / trade.entry_price
                
                float_color = G_ if float_pnl >= 0 else R_
                dir_emoji = "🟢" if trade.direction.value == "LONG" else "🔴"
                
                lines.append(f"   {dir_emoji} {trade.strategy[:12]:<12} | "
                           f"${trade.entry_price:,.0f} | "
                           f"{float_color}{float_pnl:+.2%}{R}")
        
        # 最近交易
        recent_trades = self.trader.trades[-5:] if self.trader.trades else []
        if recent_trades:
            lines.append(f"\n{B}📝 最近交易{R}")
            for trade in reversed(recent_trades):
                emoji = "✅" if trade.is_successful else "❌"
                lines.append(f"   {emoji} {trade.strategy[:12]:<12} | "
                           f"{trade.pnl_pct:+.2%} | "
                           f"${trade.pnl_usd:+,.2f}")
        
        # 策略勝率
        if self.trader.strategy_performance:
            lines.append(f"\n{B}🎯 策略勝率{R}")
            for name, perf in sorted(self.trader.strategy_performance.items(),
                                    key=lambda x: x[1].total_trades, reverse=True)[:5]:
                wr = perf.win_rate
                wr_color = G_ if wr >= 0.6 else R_ if wr < 0.4 else y
                status = "✓" if perf.enabled else "✗"
                lines.append(f"   {status} {name[:15]:<15} | "
                           f"{perf.total_trades:>3}筆 | "
                           f"{wr_color}{wr:.1%}{R}")
        
        lines.append(f"\n{c}{'='*65}{R}")
        
        return "\n".join(lines)
    
    def run(self, hours: float = 1.0):
        """
        運行交易系統
        
        Args:
            hours: 運行時間（小時）
        """
        self.is_running = True
        end_time = time.time() + hours * 3600
        
        self.logger.info("=" * 65)
        self.logger.info(f"🐋 Whale Trading System 啟動")
        self.logger.info(f"   運行時間: {hours} 小時")
        self.logger.info(f"   檢查間隔: {self.check_interval} 秒")
        self.logger.info(f"   模擬模式: {'是' if self.use_testnet else '否'}")
        self.logger.info(f"   日誌路徑: {self.log_file}")
        self.logger.info("=" * 65)
        
        # 隱藏游標
        print("\033[?25l", end="")
        
        try:
            while self.is_running and time.time() < end_time:
                # 執行分析與交易
                trade = self.analyze_and_trade()
                
                # 渲染狀態
                status = self.render_status()
                
                # 清屏並顯示
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.write(status)
                sys.stdout.flush()
                
                # 等待
                time.sleep(self.check_interval)
        
        except KeyboardInterrupt:
            pass
        
        finally:
            # 顯示游標
            print("\033[?25h", end="")
            
            # 打印最終報告
            self._print_final_report()
    
    def _print_final_report(self):
        """打印最終報告"""
        print("\n")
        print("=" * 65)
        print("📊 WHALE TRADING SYSTEM - 最終報告")
        print("=" * 65)
        
        # 基本統計
        print(f"\n⏱️ 運行統計:")
        print(f"   總迭代次數: {self.iteration}")
        print(f"   收到信號數: {self.signals_received}")
        print(f"   執行交易數: {self.trades_opened}")
        print(f"   拒絕交易數: {self.trades_rejected}")
        
        # 資金統計
        capital = self.trader.current_capital
        initial = self.trader.config.initial_capital
        total_trades = len(self.trader.trades)
        winning_trades = sum(1 for t in self.trader.trades if t.is_successful)
        
        print(f"\n💰 資金表現:")
        print(f"   初始資金: ${initial:,.2f}")
        print(f"   最終資金: ${capital:,.2f}")
        print(f"   總盈虧:   ${capital - initial:+,.2f} ({(capital/initial-1)*100:+.2f}%)")
        
        print(f"\n📈 交易表現:")
        print(f"   總交易數: {total_trades}")
        print(f"   獲勝交易: {winning_trades}")
        print(f"   虧損交易: {total_trades - winning_trades}")
        if total_trades > 0:
            print(f"   勝率:     {winning_trades/total_trades:.1%}")
        
        # 策略表現
        if self.trader.strategy_performance:
            print(f"\n🎯 策略表現:")
            for name, perf in sorted(self.trader.strategy_performance.items(),
                                    key=lambda x: x[1].total_pnl_usd, reverse=True):
                print(f"\n   📌 {name}:")
                print(f"      交易數: {perf.total_trades} | 勝率: {perf.win_rate:.1%}")
                print(f"      盈虧: ${perf.total_pnl_usd:+,.2f}")
                print(f"      做多: {perf.long_trades}筆 ({perf.long_win_rate:.1%})")
                print(f"      做空: {perf.short_trades}筆 ({perf.short_win_rate:.1%})")
        
        print("\n" + "=" * 65)
        print(f"📁 詳細日誌保存於: {self.log_file}")
        print("=" * 65)


# ============================================================
# 學習訓練腳本
# ============================================================

def train_ml_model():
    """訓練 ML 模型"""
    print("\n" + "=" * 50)
    print("🧠 ML 模型訓練")
    print("=" * 50)
    
    from whale_paper_trader import WhalePaperTrader, MLPredictor
    
    # 載入交易歷史
    trader = WhalePaperTrader()
    trades = trader.trades
    
    if len(trades) < 20:
        print(f"⚠️ 交易數據不足 ({len(trades)} < 20)，需要更多交易記錄")
        return
    
    # 訓練模型
    predictor = MLPredictor()
    success = predictor.train(trades)
    
    if success:
        print("✅ 模型訓練完成！")
        print(f"   模型保存於: models/whale_ml_model/")
    else:
        print("❌ 模型訓練失敗")


# ============================================================
# 主程式
# ============================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Whale Trading System v1.0')
    parser.add_argument('--hours', type=float, default=1.0, help='運行時間（小時）')
    parser.add_argument('--interval', type=int, default=30, help='檢查間隔（秒）')
    parser.add_argument('--train', action='store_true', help='訓練 ML 模型')
    parser.add_argument('--report', action='store_true', help='查看報告')
    
    args = parser.parse_args()
    
    if args.train:
        train_ml_model()
        return
    
    if args.report:
        trader = WhalePaperTrader()
        trader.print_report()
        return
    
    # 運行交易系統
    system = WhaleTradingSystem(
        symbol="BTC/USDT",
        check_interval=args.interval,
        use_testnet=True
    )
    
    system.run(hours=args.hours)


if __name__ == "__main__":
    main()
