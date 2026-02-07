#!/usr/bin/env python3
"""
Simple Backtest System - Phase 1 Baseline
=========================================

最小回測系統：
- 單一策略
- 單一時間框架
- 單一回測期間
- 4 核心指標：勝率、毛利、淨利、費用比

作者: Phase 1 Baseline
日期: 2025-11-14
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import sys

# 添加專案根目錄
sys.path.append(str(Path(__file__).parent.parent))

from src.strategy.mvp_strategy_v1 import MVPStrategyV1


@dataclass
class Trade:
    """交易記錄"""
    trade_id: int
    direction: str  # LONG/SHORT
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    take_profit_price: float
    stop_loss_price: float
    exit_reason: str  # TP_HIT/SL_HIT/TIME_STOP
    pnl_gross: float  # 毛利（未扣費用）
    pnl_net: float    # 淨利（扣除費用）
    fees_paid: float  # 手續費
    holding_minutes: int
    
    def to_dict(self) -> Dict:
        """轉為字典（datetime → str）"""
        d = asdict(self)
        d['entry_time'] = self.entry_time.isoformat()
        d['exit_time'] = self.exit_time.isoformat()
        return d


@dataclass
class BacktestReport:
    """回測報告"""
    # 基本信息
    strategy_name: str
    timeframe: str
    start_date: str
    end_date: str
    total_candles: int
    
    # 核心指標
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    
    # 損益
    total_pnl_gross: float  # 毛利
    total_pnl_net: float    # 淨利
    avg_pnl_gross: float
    avg_pnl_net: float
    
    # 費用
    total_fees: float
    fee_to_profit_ratio: float  # 費用/毛利
    
    # 其他
    avg_holding_minutes: float
    max_win: float
    max_loss: float
    
    # 交易列表
    trades: List[Dict]
    
    def to_dict(self) -> Dict:
        """轉為字典"""
        return asdict(self)
    
    def save_to_file(self, filepath: str):
        """保存到文件"""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"✅ 報告已保存: {filepath}")
    
    def print_summary(self):
        """打印摘要"""
        print("\n" + "=" * 80)
        print(f"  📊 回測報告 - {self.strategy_name}")
        print("=" * 80)
        
        print(f"\n【基本信息】")
        print(f"  時間框架: {self.timeframe}")
        print(f"  回測期間: {self.start_date} ~ {self.end_date}")
        print(f"  總K線數: {self.total_candles:,}")
        
        print(f"\n【交易統計】")
        print(f"  總交易數: {self.total_trades}")
        print(f"  獲勝交易: {self.winning_trades} ({self.win_rate:.1%})")
        print(f"  虧損交易: {self.losing_trades}")
        
        print(f"\n【損益分析】")
        print(f"  總毛利: ${self.total_pnl_gross:.2f}")
        print(f"  總淨利: ${self.total_pnl_net:.2f}")
        print(f"  平均毛利: ${self.avg_pnl_gross:.2f}")
        print(f"  平均淨利: ${self.avg_pnl_net:.2f}")
        print(f"  最大單筆盈利: ${self.max_win:.2f}")
        print(f"  最大單筆虧損: ${self.max_loss:.2f}")
        
        print(f"\n【費用分析】")
        print(f"  總手續費: ${self.total_fees:.2f}")
        print(f"  費用/毛利比: {self.fee_to_profit_ratio:.1%}")
        
        print(f"\n【持倉分析】")
        print(f"  平均持倉時間: {self.avg_holding_minutes:.1f} 分鐘")
        
        print("\n" + "=" * 80)


class SimpleBacktest:
    """
    簡易回測引擎
    
    限制：
    - 只支持單一策略
    - 只支持單一時間框架
    - 只支持單一資產（BTC/USDT）
    - 固定倉位大小
    - 無滑點模擬（假設成交在 K線 close 價）
    """
    
    def __init__(
        self,
        strategy: MVPStrategyV1,
        position_size: float = 0.1,  # BTC
        leverage: int = 3,
        taker_fee_rate: float = 0.0005,  # 0.05%
        maker_fee_rate: float = 0.0002,  # 0.02%
        use_maker: bool = False
    ):
        """
        初始化回測引擎
        
        Args:
            strategy: 策略實例
            position_size: 倉位大小（BTC）
            leverage: 槓桿倍數
            taker_fee_rate: Taker 費率
            maker_fee_rate: Maker 費率
            use_maker: 是否使用 Maker 單
        """
        self.strategy = strategy
        self.position_size = position_size
        self.leverage = leverage
        self.fee_rate = maker_fee_rate if use_maker else taker_fee_rate
        
        self.trades: List[Trade] = []
        self.trade_id_counter = 0
    
    def calculate_fee(self, entry_price: float) -> float:
        """計算手續費"""
        notional_value = entry_price * self.position_size * self.leverage
        fee = notional_value * self.fee_rate * 2  # 進場+出場
        return fee
    
    def calculate_pnl(
        self,
        direction: str,
        entry_price: float,
        exit_price: float
    ) -> float:
        """計算損益（毛利）"""
        notional_value = entry_price * self.position_size * self.leverage
        
        if direction == "LONG":
            pnl_percent = (exit_price - entry_price) / entry_price
        else:  # SHORT
            pnl_percent = (entry_price - exit_price) / entry_price
        
        pnl_gross = notional_value * pnl_percent
        return pnl_gross
    
    def simulate_trade(
        self,
        signal,
        entry_candle: pd.Series,
        future_candles: pd.DataFrame
    ) -> Optional[Trade]:
        """
        模擬單筆交易
        
        Args:
            signal: 信號對象
            entry_candle: 進場K線
            future_candles: 未來K線（用於模擬出場）
            
        Returns:
            Trade 對象或 None
        """
        if signal.direction is None:
            return None
        
        self.trade_id_counter += 1
        
        direction = signal.direction
        entry_price = signal.entry_price
        entry_time = entry_candle.name  # DataFrame index 是時間戳
        tp_price = signal.take_profit_price
        sl_price = signal.stop_loss_price
        time_stop = self.strategy.get_time_stop(entry_time)
        
        # 掃描未來K線，查找退出條件
        for i in range(len(future_candles)):
            candle = future_candles.iloc[i]
            candle_time = candle.name
            candle_high = candle['high']
            candle_low = candle['low']
            candle_close = candle['close']
            
            exit_price = None
            exit_reason = None
            
            # 檢查止盈/止損
            if direction == "LONG":
                if candle_high >= tp_price:
                    exit_price = tp_price
                    exit_reason = "TP_HIT"
                elif candle_low <= sl_price:
                    exit_price = sl_price
                    exit_reason = "SL_HIT"
            else:  # SHORT
                if candle_low <= tp_price:
                    exit_price = tp_price
                    exit_reason = "TP_HIT"
                elif candle_high >= sl_price:
                    exit_price = sl_price
                    exit_reason = "SL_HIT"
            
            # 檢查時間止損
            if exit_reason is None and candle_time >= time_stop:
                exit_price = candle_close
                exit_reason = "TIME_STOP"
            
            # 退出交易
            if exit_price and exit_reason:
                pnl_gross = self.calculate_pnl(direction, entry_price, exit_price)
                fees = self.calculate_fee(entry_price)
                pnl_net = pnl_gross - fees
                holding_minutes = int((candle_time - entry_time).total_seconds() / 60)
                
                trade = Trade(
                    trade_id=self.trade_id_counter,
                    direction=direction,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    exit_time=candle_time,
                    exit_price=exit_price,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price,
                    exit_reason=exit_reason,
                    pnl_gross=pnl_gross,
                    pnl_net=pnl_net,
                    fees_paid=fees,
                    holding_minutes=holding_minutes
                )
                
                return trade
        
        # 沒有退出條件觸發（數據結束）
        return None
    
    def run(
        self,
        data_file: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> BacktestReport:
        """
        運行回測
        
        Args:
            data_file: 歷史數據文件路徑（Parquet 或 CSV）
            start_date: 開始日期（YYYY-MM-DD）
            end_date: 結束日期（YYYY-MM-DD）
            
        Returns:
            BacktestReport 對象
        """
        print(f"🔄 載入數據: {data_file}")
        
        # 載入數據
        if data_file.endswith('.parquet'):
            df = pd.read_parquet(data_file)
            # 如果有 timestamp 列但不是索引，設為索引
            if 'timestamp' in df.columns and not isinstance(df.index, pd.DatetimeIndex):
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df.set_index('timestamp', inplace=True)
        else:
            df = pd.read_csv(data_file, parse_dates=['timestamp'])
            df.set_index('timestamp', inplace=True)
        
        # 篩選時間範圍
        if start_date:
            start_date = pd.to_datetime(start_date)
            df = df[df.index >= start_date]
        if end_date:
            end_date = pd.to_datetime(end_date)
            df = df[df.index <= end_date]
        
        print(f"✅ 數據載入完成: {len(df):,} 根K線")
        print(f"   期間: {df.index[0]} ~ {df.index[-1]}")
        
        # 逐K線掃描
        print(f"🔄 開始回測...")
        for i in range(len(df) - 1):  # -1 因為需要未來K線
            current_candles = df.iloc[:i+1]
            future_candles = df.iloc[i+1:]
            
            # 生成信號
            signal = self.strategy.generate_signal(current_candles)
            
            # 模擬交易
            if signal.direction:
                trade = self.simulate_trade(signal, df.iloc[i], future_candles)
                if trade:
                    self.trades.append(trade)
        
        print(f"✅ 回測完成: {len(self.trades)} 筆交易")
        
        # 生成報告
        report = self.generate_report(
            data_file=data_file,
            start_date=str(df.index[0]),
            end_date=str(df.index[-1]),
            total_candles=len(df)
        )
        
        return report
    
    def generate_report(
        self,
        data_file: str,
        start_date: str,
        end_date: str,
        total_candles: int
    ) -> BacktestReport:
        """生成報告"""
        if not self.trades:
            return BacktestReport(
                strategy_name="MVP Strategy v1.0",
                timeframe="unknown",
                start_date=start_date,
                end_date=end_date,
                total_candles=total_candles,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                total_pnl_gross=0.0,
                total_pnl_net=0.0,
                avg_pnl_gross=0.0,
                avg_pnl_net=0.0,
                total_fees=0.0,
                fee_to_profit_ratio=0.0,
                avg_holding_minutes=0.0,
                max_win=0.0,
                max_loss=0.0,
                trades=[]
            )
        
        # 統計
        total_trades = len(self.trades)
        winning_trades = sum(1 for t in self.trades if t.pnl_net > 0)
        losing_trades = total_trades - winning_trades
        win_rate = winning_trades / total_trades
        
        total_pnl_gross = sum(t.pnl_gross for t in self.trades)
        total_pnl_net = sum(t.pnl_net for t in self.trades)
        avg_pnl_gross = total_pnl_gross / total_trades
        avg_pnl_net = total_pnl_net / total_trades
        
        total_fees = sum(t.fees_paid for t in self.trades)
        fee_to_profit_ratio = total_fees / total_pnl_gross if total_pnl_gross > 0 else float('inf')
        
        avg_holding_minutes = sum(t.holding_minutes for t in self.trades) / total_trades
        
        max_win = max(t.pnl_net for t in self.trades)
        max_loss = min(t.pnl_net for t in self.trades)
        
        return BacktestReport(
            strategy_name="MVP Strategy v1.0",
            timeframe=Path(data_file).stem,
            start_date=start_date,
            end_date=end_date,
            total_candles=total_candles,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_pnl_gross=total_pnl_gross,
            total_pnl_net=total_pnl_net,
            avg_pnl_gross=avg_pnl_gross,
            avg_pnl_net=avg_pnl_net,
            total_fees=total_fees,
            fee_to_profit_ratio=fee_to_profit_ratio,
            avg_holding_minutes=avg_holding_minutes,
            max_win=max_win,
            max_loss=max_loss,
            trades=[t.to_dict() for t in self.trades]
        )


def main():
    """主程序"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Simple Backtest System')
    parser.add_argument('--data', type=str, required=True, help='Data file path (parquet/csv)')
    parser.add_argument('--start', type=str, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, help='End date (YYYY-MM-DD)')
    parser.add_argument('--output', type=str, default='data/backtest_report.json', help='Output file path')
    parser.add_argument('--position-size', type=float, default=0.1, help='Position size (BTC)')
    parser.add_argument('--leverage', type=int, default=3, help='Leverage')
    
    args = parser.parse_args()
    
    # 初始化策略
    strategy = MVPStrategyV1()
    
    # 初始化回測引擎
    backtest = SimpleBacktest(
        strategy=strategy,
        position_size=args.position_size,
        leverage=args.leverage
    )
    
    # 運行回測
    report = backtest.run(
        data_file=args.data,
        start_date=args.start,
        end_date=args.end
    )
    
    # 打印報告
    report.print_summary()
    
    # 保存報告
    report.save_to_file(args.output)


if __name__ == "__main__":
    main()
