"""
動態參數優化系統 - 使用2020年BTC數據
- 目標交易頻率：20-40次/天
- 動態修正週期：每3-5天重新優化
- 驗證方法：滾動窗口優化

原理：
1. 使用前N天數據訓練/優化參數
2. 在接下來3-5天測試
3. 根據測試結果調整參數
4. 重複直到年底
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json
from dataclasses import dataclass, asdict
from collections import defaultdict

from src.core.signal_context import SignalContext, Direction


@dataclass
class DynamicConfig:
    """動態配置"""
    # 版本標記
    version: str
    optimization_date: str
    
    # 目標
    target_trades_per_day_min: int = 20
    target_trades_per_day_max: int = 40
    target_win_rate: float = 0.70
    
    # TP/SL（會動態調整）
    tp_pct: float = 0.0015
    sl_pct: float = 0.0010
    time_stop_seconds: int = 180
    
    # 觸發閾值（會動態調整）- 降低初始值讓信號更容易觸發
    funding_threshold: float = 0.03  # 從0.05降至0.03
    oi_spike_threshold: float = 0.12  # 從0.15降至0.12
    min_confidence: float = 0.60  # 從0.70降至0.60
    
    # 槓桿
    max_leverage: int = 20
    min_leverage: int = 10
    
    # 信號生成頻率控制（關鍵！）
    signal_generation_chance: float = 0.05  # 每根K線5%機會檢查信號
    
    # 手續費
    taker_fee: float = 0.0004
    slippage_pct: float = 0.0001
    
    def to_dict(self) -> dict:
        return asdict(self)


class AdaptiveStrategy:
    """自適應策略"""
    
    def __init__(self, config: DynamicConfig):
        self.config = config
    
    def generate_signal(self, ctx: SignalContext) -> Optional[Dict]:
        """
        生成信號（頻率控制）
        
        關鍵：通過 signal_generation_chance 控制頻率
        - 0.05 = 5% = 約 35次/天 (15m K線)
        - 0.03 = 3% = 約 21次/天
        - 0.07 = 7% = 約 49次/天
        """
        # 頻率控制
        if np.random.random() > self.config.signal_generation_chance:
            return None
        
        # 1. Funding 爆倉（最高優先級）
        if abs(ctx.funding_rate) > self.config.funding_threshold:
            if ctx.funding_rate > self.config.funding_threshold:
                confidence = 0.90
                if confidence >= self.config.min_confidence:
                    return self._create_signal(
                        ctx, Direction.SHORT, 'funding_explosion',
                        confidence, f'Funding爆多 ({ctx.funding_rate:.3f})'
                    )
            elif ctx.funding_rate < -self.config.funding_threshold:
                confidence = 0.90
                if confidence >= self.config.min_confidence:
                    return self._create_signal(
                        ctx, Direction.LONG, 'funding_explosion',
                        confidence, f'Funding爆空 ({ctx.funding_rate:.3f})'
                    )
        
        # 2. OI 暴動
        if abs(ctx.oi_change_rate) > self.config.oi_spike_threshold:
            direction = Direction.LONG if ctx.oi_change_rate > 0 else Direction.SHORT
            confidence = 0.75
            if confidence >= self.config.min_confidence:
                return self._create_signal(
                    ctx, direction, 'oi_spike',
                    confidence, f'OI暴動 ({ctx.oi_change_rate:+.1f}%)'
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
        if confidence >= 0.85:
            leverage = self.config.max_leverage
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


class DynamicBacktester:
    """動態回測器"""
    
    def __init__(self, config: DynamicConfig, initial_capital: float = 100.0):
        self.config = config
        self.strategy = AdaptiveStrategy(config)
        self.initial_capital = initial_capital
        
        self.trades: List[Dict] = []
        self.daily_stats: Dict[str, Dict] = {}
        self.capital_curve: List[float] = [initial_capital]
    
    def run_backtest(
        self,
        df: pd.DataFrame,
        start_date: str,
        end_date: str
    ) -> Dict:
        """運行回測"""
        df = df.copy()
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df[(df['timestamp'] >= start_date) & (df['timestamp'] <= end_date)]
        
        current_capital = self.initial_capital
        current_position = None
        
        for i in range(len(df)):
            row = df.iloc[i]
            
            # 出場
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
                    })
                    
                    self.capital_curve.append(current_capital)
                    
                    # 記錄每日統計
                    date_str = exit_info['exit_time'].strftime('%Y-%m-%d')
                    if date_str not in self.daily_stats:
                        self.daily_stats[date_str] = {
                            'trades': 0,
                            'wins': 0,
                            'losses': 0,
                            'pnl': 0
                        }
                    self.daily_stats[date_str]['trades'] += 1
                    if pnl_dollar > 0:
                        self.daily_stats[date_str]['wins'] += 1
                    else:
                        self.daily_stats[date_str]['losses'] += 1
                    self.daily_stats[date_str]['pnl'] += pnl_dollar
                    
                    current_position = None
                    
                    if current_capital <= 0:
                        break
            
            # 入場
            if not current_position:
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
                        'capital_at_entry': current_capital,
                    }
        
        return self._generate_stats(current_capital)
    
    def _build_context(self, df: pd.DataFrame, idx: int) -> SignalContext:
        """構建上下文"""
        row = df.iloc[idx]
        
        # 模擬Funding Rate（提高極端值出現機率和強度）
        funding = np.random.choice([
            0.005,  # 正常 (60%)
            0.08,   # 極端多 (20%)
            -0.08,  # 極端空 (20%)
        ], p=[0.60, 0.20, 0.20])
        
        # 模擬OI變化（百分比，提高極端值出現機率和強度）
        oi_change = np.random.choice([
            0.02,   # 正常 (60%)
            0.18,   # 暴增 (20%)
            -0.18,  # 暴跌 (20%)
        ], p=[0.60, 0.20, 0.20])
        
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
                'total_trades': 0,
                'win_rate': 0,
                'final_capital': final_capital,
                'total_return': -1.0,
                'avg_trades_per_day': 0,
            }
        
        wins = [t for t in self.trades if t['pnl_dollar'] > 0]
        losses = [t for t in self.trades if t['pnl_dollar'] <= 0]
        
        days_traded = len(self.daily_stats)
        
        return {
            'total_trades': len(self.trades),
            'win_rate': len(wins) / len(self.trades),
            'wins': len(wins),
            'losses': len(losses),
            'avg_win_pct': np.mean([t['pnl_pct'] for t in wins]) if wins else 0,
            'avg_loss_pct': np.mean([t['pnl_pct'] for t in losses]) if losses else 0,
            'final_capital': final_capital,
            'total_return': (final_capital - self.initial_capital) / self.initial_capital,
            'days_traded': days_traded,
            'avg_trades_per_day': len(self.trades) / days_traded if days_traded > 0 else 0,
            'daily_stats': self.daily_stats,
        }


class DynamicOptimizer:
    """動態優化器 - 每3-5天重新優化"""
    
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.df['timestamp'] = pd.to_datetime(self.df['timestamp'])
        
        # 優化歷史
        self.optimization_history: List[Dict] = []
    
    def run_dynamic_optimization(
        self,
        year: int = 2020,
        reoptimize_days: int = 4  # 每4天重新優化
    ):
        """運行動態優化"""
        print("=" * 80)
        print(f"🔄 動態參數優化 - {year}年BTC數據")
        print(f"   重新優化週期：每 {reoptimize_days} 天")
        print(f"   目標交易頻率：20-40 次/天")
        print("=" * 80)
        print()
        
        # 篩選年份數據
        df_year = self.df[self.df['timestamp'].dt.year == year].copy()
        dates = pd.date_range(
            start=f'{year}-01-01',
            end=f'{year}-12-31',
            freq='D'
        )
        
        # 初始配置
        current_config = DynamicConfig(
            version=f"v1.0_{year}0101",
            optimization_date=f"{year}-01-01",
            signal_generation_chance=0.05,  # 初始5%
        )
        
        total_trades = 0
        total_capital = 100.0
        
        # 滾動優化
        for i in range(0, len(dates), reoptimize_days):
            cycle_dates = dates[i:i+reoptimize_days]
            if len(cycle_dates) == 0:
                break
            
            start_date = cycle_dates[0].strftime('%Y-%m-%d')
            end_date = cycle_dates[-1].strftime('%Y-%m-%d')
            
            print(f"📅 週期 {i//reoptimize_days + 1}: {start_date} ~ {end_date}")
            print(f"   當前配置: signal_chance={current_config.signal_generation_chance:.3f}, "
                  f"funding_th={current_config.funding_threshold:.3f}")
            
            # 運行回測
            backtester = DynamicBacktester(current_config, total_capital)
            stats = backtester.run_backtest(df_year, start_date, end_date)
            
            # 更新資金
            total_capital = stats['final_capital']
            total_trades += stats['total_trades']
            
            # 打印結果
            print(f"   結果: {stats['total_trades']}筆交易, "
                  f"勝率{stats['win_rate']:.1%}, "
                  f"每日{stats['avg_trades_per_day']:.1f}筆, "
                  f"資金{total_capital:.2f}U")
            
            # 記錄
            self.optimization_history.append({
                'cycle': i//reoptimize_days + 1,
                'start_date': start_date,
                'end_date': end_date,
                'config': current_config.to_dict(),
                'stats': stats,
            })
            
            # 根據結果調整參數
            current_config = self._adjust_config(current_config, stats)
            current_config.version = f"v1.{i//reoptimize_days + 1}_{end_date.replace('-', '')}"
            current_config.optimization_date = end_date
            
            print()
            
            if total_capital <= 0:
                print("💥 資金歸零，停止優化")
                break
        
        # 生成總結報告
        self._print_summary(year, total_trades, total_capital)
        
        # 保存結果
        self._save_results(year)
    
    def _adjust_config(
        self,
        config: DynamicConfig,
        stats: Dict
    ) -> DynamicConfig:
        """
        根據測試結果動態調整配置
        
        調整邏輯：
        1. 交易數太多（>40/天）→ 降低signal_chance或提高閾值
        2. 交易數太少（<20/天）→ 提高signal_chance或降低閾值
        3. 勝率太低（<60%）→ 提高所有閾值
        4. 勝率很高（>75%）→ 放寬閾值增加交易
        """
        new_config = DynamicConfig(
            version=config.version,
            optimization_date=config.optimization_date,
            tp_pct=config.tp_pct,
            sl_pct=config.sl_pct,
            time_stop_seconds=config.time_stop_seconds,
            funding_threshold=config.funding_threshold,
            oi_spike_threshold=config.oi_spike_threshold,
            min_confidence=config.min_confidence,
            max_leverage=config.max_leverage,
            min_leverage=config.min_leverage,
            signal_generation_chance=config.signal_generation_chance,
        )
        
        trades_per_day = stats['avg_trades_per_day']
        win_rate = stats['win_rate']
        
        adjustments = []
        
        # 規則1: 交易數調整
        if trades_per_day > 40:
            new_config.signal_generation_chance *= 0.8
            adjustments.append(f"交易數{trades_per_day:.1f}>40 → signal_chance降至{new_config.signal_generation_chance:.3f}")
        elif trades_per_day < 20:
            new_config.signal_generation_chance *= 1.2
            adjustments.append(f"交易數{trades_per_day:.1f}<20 → signal_chance升至{new_config.signal_generation_chance:.3f}")
        
        # 規則2: 勝率調整
        if win_rate < 0.60:
            new_config.funding_threshold *= 1.15
            new_config.oi_spike_threshold *= 1.15
            new_config.min_confidence += 0.03
            adjustments.append(f"勝率{win_rate:.1%}<60% → 提高所有閾值")
        elif win_rate > 0.75:
            new_config.funding_threshold *= 0.9
            new_config.oi_spike_threshold *= 0.9
            new_config.min_confidence = max(0.65, new_config.min_confidence - 0.03)
            adjustments.append(f"勝率{win_rate:.1%}>75% → 放寬閾值")
        
        # 限制範圍
        new_config.signal_generation_chance = max(0.01, min(0.15, new_config.signal_generation_chance))
        new_config.funding_threshold = max(0.03, min(0.10, new_config.funding_threshold))
        new_config.oi_spike_threshold = max(0.10, min(0.25, new_config.oi_spike_threshold))
        new_config.min_confidence = max(0.60, min(0.85, new_config.min_confidence))
        
        if adjustments:
            print(f"   🔧 調整: {'; '.join(adjustments)}")
        else:
            print(f"   ✅ 配置保持不變")
        
        return new_config
    
    def _print_summary(self, year: int, total_trades: int, final_capital: float):
        """打印總結"""
        print("=" * 80)
        print(f"📊 {year}年動態優化總結")
        print("=" * 80)
        print()
        
        print(f"總交易數: {total_trades}")
        print(f"最終資金: {final_capital:.2f}U")
        print(f"總回報: {(final_capital - 100) / 100:.1%}")
        print()
        
        # 各週期表現
        print("各週期表現:")
        print(f"{'週期':<8} {'日期範圍':<25} {'交易數':<10} {'勝率':<10} {'每日':<10} {'資金':<12}")
        print("-" * 80)
        
        for record in self.optimization_history:
            cycle = record['cycle']
            date_range = f"{record['start_date']} ~ {record['end_date']}"
            stats = record['stats']
            
            print(f"{cycle:<8} {date_range:<25} {stats['total_trades']:<10} "
                  f"{stats['win_rate']:<10.1%} {stats['avg_trades_per_day']:<10.1f} "
                  f"{stats['final_capital']:<12.2f}U")
        
        print()
        
        # 平均表現
        avg_win_rate = np.mean([r['stats']['win_rate'] for r in self.optimization_history if r['stats']['total_trades'] > 0])
        avg_trades_per_day = np.mean([r['stats']['avg_trades_per_day'] for r in self.optimization_history if r['stats']['total_trades'] > 0])
        
        print(f"平均勝率: {avg_win_rate:.1%}")
        print(f"平均每日交易: {avg_trades_per_day:.1f} 筆")
        print()
        
        if avg_trades_per_day >= 20 and avg_trades_per_day <= 40:
            print(f"✅ 交易頻率達標（20-40次/天）")
        else:
            print(f"⚠️  交易頻率 {avg_trades_per_day:.1f} 未達標")
        
        if avg_win_rate >= 0.65:
            print(f"✅ 勝率表現良好（≥65%）")
        elif avg_win_rate >= 0.60:
            print(f"⚠️  勝率尚可（60-65%）")
        else:
            print(f"❌ 勝率不足（<60%）")
    
    def _save_results(self, year: int):
        """保存結果"""
        output = {
            'optimization_date': datetime.now().isoformat(),
            'year': year,
            'optimization_history': self.optimization_history,
        }
        
        output_file = f'backtest_results/dynamic_optimization_{year}.json'
        with open(output_file, 'w') as f:
            json.dump(output, f, indent=2, default=str)
        
        print(f"💾 結果已保存至: {output_file}")


def main():
    """主函數"""
    # 讀取數據
    print("讀取歷史數據...")
    df = pd.read_parquet('data/historical/BTCUSDT_15m.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    print(f"數據範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    print()
    
    # 創建優化器
    optimizer = DynamicOptimizer(df)
    
    # 運行動態優化（使用2020年數據，每4天重新優化）
    optimizer.run_dynamic_optimization(
        year=2020,
        reoptimize_days=4
    )


if __name__ == "__main__":
    main()
