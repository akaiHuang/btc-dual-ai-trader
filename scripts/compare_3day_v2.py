"""
3天翻倍計劃 v2 - 優化版
問題診斷：v1 勝率49%，交易過多（94筆/天），全部爆倉
解決方案：提高閾值，只保留高質量信號，目標10-15筆/天，70%勝率
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import json
from dataclasses import dataclass, asdict

from src.core.signal_context import SignalContext, Direction


@dataclass
class ImprovedConfig:
    """改進配置"""
    name: str
    version: str
    
    # 目標
    daily_return_target: float
    trades_per_day_target: int
    target_win_rate: float
    
    # TP/SL（更大，提高勝率）
    tp_pct: float
    sl_pct: float
    time_stop_seconds: int
    
    # 槓桿（降低）
    max_leverage: int
    min_leverage: int
    
    # 觸發閾值（提高，更嚴格）
    funding_threshold: float
    oi_spike_threshold: float
    
    # 過濾
    min_confidence: float
    
    # 手續費
    taker_fee: float = 0.0004
    slippage_pct: float = 0.0001
    
    # 觸發頻率（降低）
    signal_chance: float = 0.02  # 每根K線2%機會
    
    def to_dict(self) -> dict:
        return asdict(self)


# 三個改進方案
IMPROVED_SCENARIOS = {
    'conservative': ImprovedConfig(
        name="穩健複利 v2",
        version="v2_conservative",
        daily_return_target=0.26,
        trades_per_day_target=10,
        target_win_rate=0.70,
        
        tp_pct=0.0020,  # 0.20% TP（更大）
        sl_pct=0.0010,  # 0.10% SL
        time_stop_seconds=300,
        
        max_leverage=15,
        min_leverage=10,
        
        funding_threshold=0.06,  # 更嚴格
        oi_spike_threshold=0.20,  # 更嚴格
        
        min_confidence=0.75,  # 更高
        
        signal_chance=0.015,  # 1.5%機會
    ),
    
    'aggressive': ImprovedConfig(
        name="激進複利 v2",
        version="v2_aggressive",
        daily_return_target=0.33,
        trades_per_day_target=15,
        target_win_rate=0.70,
        
        tp_pct=0.0018,  # 0.18%
        sl_pct=0.0009,  # 0.09%
        time_stop_seconds=240,
        
        max_leverage=20,
        min_leverage=12,
        
        funding_threshold=0.05,
        oi_spike_threshold=0.18,
        
        min_confidence=0.70,
        
        signal_chance=0.020,  # 2%機會
    ),
    
    'extreme': ImprovedConfig(
        name="極致複利 v2",
        version="v2_extreme",
        daily_return_target=0.40,
        trades_per_day_target=20,
        target_win_rate=0.75,
        
        tp_pct=0.0015,  # 0.15%
        sl_pct=0.0008,  # 0.08%
        time_stop_seconds=180,
        
        max_leverage=25,
        min_leverage=15,
        
        funding_threshold=0.045,
        oi_spike_threshold=0.15,
        
        min_confidence=0.68,
        
        signal_chance=0.025,  # 2.5%機會
    ),
}


class ImprovedStrategy:
    """改進策略 - 只保留最高質量信號"""
    
    def __init__(self, config: ImprovedConfig):
        self.config = config
    
    def generate_signal(self, ctx: SignalContext) -> Optional[Dict]:
        """只檢查最可靠的兩種觸發：Funding爆倉 + OI暴動"""
        
        # 1. Funding 爆倉（最高優先級，95%信心）
        if abs(ctx.funding_rate) > self.config.funding_threshold:
            if ctx.funding_rate > self.config.funding_threshold:
                # 多單爆倉
                confidence = 0.95
                if confidence >= self.config.min_confidence:
                    return self._create_signal(
                        ctx,
                        Direction.SHORT,
                        'funding_explosion',
                        confidence,
                        f'多單爆倉 (Funding {ctx.funding_rate:.3f})'
                    )
            
            elif ctx.funding_rate < -self.config.funding_threshold:
                # 空單爆倉
                confidence = 0.95
                if confidence >= self.config.min_confidence:
                    return self._create_signal(
                        ctx,
                        Direction.LONG,
                        'funding_explosion',
                        confidence,
                        f'空單爆倉 (Funding {ctx.funding_rate:.3f})'
                    )
        
        # 2. OI 暴動（次優先級，80%信心）
        if abs(ctx.oi_change_rate) > self.config.oi_spike_threshold:
            direction = Direction.LONG if ctx.oi_change_rate > 0 else Direction.SHORT
            confidence = 0.80
            
            if confidence >= self.config.min_confidence:
                return self._create_signal(
                    ctx,
                    direction,
                    'oi_spike',
                    confidence,
                    f'OI 暴動 ({ctx.oi_change_rate:+.1f}%)'
                )
        
        return None
    
    def _create_signal(
        self,
        ctx: SignalContext,
        direction: Direction,
        trigger_type: str,
        confidence: float,
        reason: str
    ) -> Dict:
        """創建信號"""
        entry_price = ctx.current_price
        
        # 動態槓桿
        if confidence >= 0.90:
            leverage = self.config.max_leverage
        elif confidence >= 0.80:
            leverage = int((self.config.max_leverage + self.config.min_leverage) / 2)
        else:
            leverage = self.config.min_leverage
        
        # TP/SL
        if direction == Direction.LONG:
            tp = entry_price * (1 + self.config.tp_pct)
            sl = entry_price * (1 - self.config.sl_pct)
        else:
            tp = entry_price * (1 - self.config.tp_pct)
            sl = entry_price * (1 + self.config.sl_pct)
        
        return {
            'direction': direction,
            'trigger_type': trigger_type,
            'confidence': confidence,
            'leverage': leverage,
            'entry_price': entry_price,
            'tp': tp,
            'sl': sl,
            'reason': reason
        }


class ImprovedBacktester:
    """改進回測器"""
    
    def __init__(self, config: ImprovedConfig, initial_capital: float = 100.0):
        self.config = config
        self.strategy = ImprovedStrategy(config)
        self.initial_capital = initial_capital
        
        self.trades: List[Dict] = []
        self.daily_pnl: Dict[str, float] = {}
        self.capital_curve: List[float] = [initial_capital]
    
    def run_backtest(
        self,
        df: pd.DataFrame,
        start_date: str = None,
        end_date: str = None
    ) -> Dict:
        """運行回測"""
        print(f"🚀 {self.config.name} 回測...")
        
        df = df.copy()
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if start_date:
            df = df[df['timestamp'] >= start_date]
        if end_date:
            df = df[df['timestamp'] <= end_date]
        
        current_capital = self.initial_capital
        current_position = None
        
        for i in range(len(df)):
            if i % 10000 == 0 and i > 0:
                print(f"   {i/len(df)*100:.0f}% (資金: {current_capital:.2f}U)")
            
            row = df.iloc[i]
            
            # 出場檢查
            if current_position:
                exit_info = self._check_exit(current_position, row, df.iloc[i:min(i+10, len(df))])
                if exit_info:
                    pnl_pct, pnl_dollar = self._calculate_pnl(current_position, exit_info)
                    current_capital += pnl_dollar
                    
                    self.trades.append({
                        'entry_time': current_position['entry_time'],
                        'exit_time': exit_info['exit_time'],
                        'direction': current_position['direction'],
                        'leverage': current_position['leverage'],
                        'pnl_pct': pnl_pct,
                        'pnl_dollar': pnl_dollar,
                        'capital_after': current_capital,
                        'exit_reason': exit_info['reason'],
                        'trigger_type': current_position['trigger_type'],
                    })
                    
                    self.capital_curve.append(current_capital)
                    
                    date_str = exit_info['exit_time'].strftime('%Y-%m-%d')
                    if date_str not in self.daily_pnl:
                        self.daily_pnl[date_str] = 0
                    self.daily_pnl[date_str] += pnl_dollar
                    
                    current_position = None
                    
                    if current_capital <= 0:
                        print(f"   💥 爆倉")
                        break
            
            # 入場檢查（降低頻率）
            if not current_position and np.random.random() < self.config.signal_chance:
                ctx = self._build_context(df, i)
                signal = self.strategy.generate_signal(ctx)
                
                if signal:
                    current_position = {
                        'entry_time': row['timestamp'],
                        'entry_price': row['close'],
                        'direction': signal['direction'],
                        'leverage': signal['leverage'],
                        'tp': signal['tp'],
                        'sl': signal['sl'],
                        'trigger_type': signal['trigger_type'],
                        'capital_at_entry': current_capital,
                    }
        
        print(f"   ✅ 最終: {current_capital:.2f}U")
        return self._generate_stats(current_capital)
    
    def _build_context(self, df: pd.DataFrame, idx: int) -> SignalContext:
        """構建上下文（更真實的極端值模擬）"""
        row = df.iloc[idx]
        
        # Funding Rate極端值（真實歷史可達±0.10）
        funding = np.random.choice([
            0.01,  # 正常 (70%)
            0.08,  # 極端多單 (15%)
            -0.08, # 極端空單 (15%)
        ], p=[0.70, 0.15, 0.15])
        
        # OI 變化極端值
        oi_change = np.random.choice([
            2.0,   # 正常 (70%)
            25.0,  # 暴增 (15%)
            -25.0, # 暴跌 (15%)
        ], p=[0.70, 0.15, 0.15])
        
        return SignalContext(
            timestamp=row['timestamp'],
            current_price=row['close'],
            funding_rate=funding,
            oi_change_rate=oi_change,
        )
    
    def _check_exit(self, position: Dict, current_row: pd.Series, future_window: pd.DataFrame) -> Optional[Dict]:
        """檢查出場"""
        time_elapsed = (current_row['timestamp'] - position['entry_time']).total_seconds()
        if time_elapsed > self.config.time_stop_seconds:
            return {'exit_time': current_row['timestamp'], 'exit_price': current_row['close'], 'reason': 'TIME_STOP'}
        
        for _, row in future_window.iterrows():
            if position['direction'] == Direction.LONG:
                if row['high'] >= position['tp']:
                    return {'exit_time': row['timestamp'], 'exit_price': position['tp'], 'reason': 'TP'}
                if row['low'] <= position['sl']:
                    return {'exit_time': row['timestamp'], 'exit_price': position['sl'], 'reason': 'SL'}
            else:
                if row['low'] <= position['tp']:
                    return {'exit_time': row['timestamp'], 'exit_price': position['tp'], 'reason': 'TP'}
                if row['high'] >= position['sl']:
                    return {'exit_time': row['timestamp'], 'exit_price': position['sl'], 'reason': 'SL'}
        return None
    
    def _calculate_pnl(self, position: Dict, exit_info: Dict) -> Tuple[float, float]:
        """計算盈虧"""
        entry = position['entry_price']
        exit = exit_info['exit_price']
        direction = position['direction']
        leverage = position['leverage']
        capital = position['capital_at_entry']
        
        if direction == Direction.LONG:
            price_change = (exit - entry) / entry
        else:
            price_change = (entry - exit) / entry
        
        leveraged_pnl = price_change * leverage
        fees = self.config.taker_fee * 2 + self.config.slippage_pct
        net_pnl_pct = leveraged_pnl - fees
        pnl_dollar = capital * net_pnl_pct
        
        return net_pnl_pct, pnl_dollar
    
    def _generate_stats(self, final_capital: float) -> Dict:
        """生成統計"""
        if not self.trades:
            return {
                'config_name': self.config.name,
                'total_trades': 0,
                'win_rate': 0,
                'final_capital': final_capital,
                'total_return_pct': -1.0,
            }
        
        wins = [t for t in self.trades if t['pnl_dollar'] > 0]
        losses = [t for t in self.trades if t['pnl_dollar'] <= 0]
        
        dates = sorted(self.daily_pnl.keys())
        days_traded = len(dates)
        
        three_day_periods = []
        for i in range(0, len(dates), 3):
            period_dates = dates[i:i+3]
            if len(period_dates) >= 3:
                period_pnl = sum(self.daily_pnl[d] for d in period_dates)
                three_day_periods.append({
                    'dates': period_dates,
                    'pnl_dollar': period_pnl,
                    'return_pct': period_pnl / self.initial_capital
                })
        
        return {
            'config_name': self.config.name,
            'total_trades': len(self.trades),
            'win_rate': len(wins) / len(self.trades),
            'wins': len(wins),
            'losses': len(losses),
            'avg_win_pct': np.mean([t['pnl_pct'] for t in wins]) if wins else 0,
            'avg_loss_pct': np.mean([t['pnl_pct'] for t in losses]) if losses else 0,
            'initial_capital': self.initial_capital,
            'final_capital': final_capital,
            'total_return_pct': (final_capital - self.initial_capital) / self.initial_capital,
            'days_traded': days_traded,
            'avg_trades_per_day': len(self.trades) / days_traded if days_traded > 0 else 0,
            'three_day_periods': three_day_periods,
            'target_achieved': any(p['return_pct'] >= 1.0 for p in three_day_periods) if three_day_periods else False,
        }


def print_report(results: Dict[str, Dict]):
    """打印報告"""
    print("\n" + "=" * 100)
    print("📊 改進版三方案對比")
    print("=" * 100)
    print(f"\n{'方案':<20} {'交易數':<10} {'勝率':<10} {'每日':<10} {'最終資金':<15} {'回報':<12} {'達標'}")
    print("-" * 100)
    
    for key in ['conservative', 'aggressive', 'extreme']:
        s = results[key]
        if s['total_trades'] == 0:
            continue
        print(f"{s['config_name']:<20} {s['total_trades']:<10} {s['win_rate']:<10.1%} {s['avg_trades_per_day']:<10.1f} {s['final_capital']:<15.2f}U {s['total_return_pct']:<12.1%} {'✅' if s['target_achieved'] else '❌'}")
    
    print("\n" + "=" * 100)
    
    for key in ['conservative', 'aggressive', 'extreme']:
        s = results[key]
        if s['total_trades'] == 0:
            continue
        
        print(f"\n📈 {s['config_name']}:")
        print(f"   總交易: {s['total_trades']}，勝率: {s['win_rate']:.1%}")
        print(f"   平均盈: {s['avg_win_pct']:.2%}，平均虧: {s['avg_loss_pct']:.2%}")
        print(f"   每日: {s['avg_trades_per_day']:.1f}筆")
        
        if s.get('three_day_periods'):
            print(f"   3天週期（前3個）:")
            for i, p in enumerate(s['three_day_periods'][:3], 1):
                status = "✅" if p['return_pct'] >= 1.0 else "❌"
                print(f"      {i}. {p['return_pct']:+.1%} ({p['pnl_dollar']:+.2f}U) {status}")


def main():
    """主函數"""
    df = pd.read_parquet('data/historical/BTCUSDT_15m.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    results = {}
    for key, config in IMPROVED_SCENARIOS.items():
        backtester = ImprovedBacktester(config, 100.0)
        stats = backtester.run_backtest(df, '2024-01-01', '2025-11-10')
        results[key] = stats
    
    print_report(results)
    
    with open('backtest_results/3day_double_v2.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\n💾 已保存: backtest_results/3day_double_v2.json")


if __name__ == "__main__":
    main()
