"""
3天翻倍：三方案對比測試
- 方案1: 穩健複利（26%/天，8-10筆/天，70%勝率，15x槓桿）
- 方案2: 激進複利（33%/天，15-20筆/天，70%勝率，20x槓桿）
- 方案3: 極致複利（40%/天，20-30筆/天，75%勝率，25x槓桿）
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json
from dataclasses import dataclass, asdict

from src.core.signal_context import SignalContext, Direction


@dataclass
class ScenarioConfig:
    """方案配置"""
    name: str
    version: str
    
    # 目標
    daily_return_target: float  # 每日目標回報
    trades_per_day_min: int
    trades_per_day_max: int
    target_win_rate: float
    
    # TP/SL
    tp_pct: float
    sl_pct: float
    time_stop_seconds: int
    
    # 觸發閾值
    funding_threshold: float
    oi_change_threshold: float
    liquidation_threshold: float
    whale_threshold: float
    
    # 槓桿
    ultra_high_leverage: int
    high_leverage: int
    medium_leverage: int
    low_leverage: int
    
    # 過濾
    min_confidence: float
    
    # 手續費
    maker_fee: float = 0.0002
    taker_fee: float = 0.0004
    slippage_pct: float = 0.0001
    
    # L0數據模擬頻率
    funding_spike_chance: float = 0.05
    oi_spike_chance: float = 0.08
    whale_chance: float = 0.05
    liquidation_high_chance: float = 0.15
    tape_aggression_chance: float = 0.08
    
    def to_dict(self) -> dict:
        return asdict(self)


# 三個方案配置
SCENARIOS = {
    'conservative': ScenarioConfig(
        name="穩健複利",
        version="scenario_1_conservative",
        daily_return_target=0.26,
        trades_per_day_min=8,
        trades_per_day_max=10,
        target_win_rate=0.70,
        
        tp_pct=0.0012,  # 0.12%
        sl_pct=0.0008,  # 0.08%
        time_stop_seconds=180,
        
        funding_threshold=0.04,
        oi_change_threshold=0.12,
        liquidation_threshold=800,
        whale_threshold=1500,
        
        ultra_high_leverage=18,
        high_leverage=15,
        medium_leverage=12,
        low_leverage=10,
        
        min_confidence=0.65,  # 較高過濾
        
        # 較低觸發頻率
        funding_spike_chance=0.04,
        oi_spike_chance=0.06,
        whale_chance=0.04,
        liquidation_high_chance=0.12,
        tape_aggression_chance=0.06,
    ),
    
    'aggressive': ScenarioConfig(
        name="激進複利",
        version="scenario_2_aggressive",
        daily_return_target=0.33,
        trades_per_day_min=15,
        trades_per_day_max=20,
        target_win_rate=0.70,
        
        tp_pct=0.0010,  # 0.10%
        sl_pct=0.0007,  # 0.07%
        time_stop_seconds=150,
        
        funding_threshold=0.03,
        oi_change_threshold=0.10,
        liquidation_threshold=600,
        whale_threshold=1200,
        
        ultra_high_leverage=22,
        high_leverage=20,
        medium_leverage=15,
        low_leverage=12,
        
        min_confidence=0.60,
        
        # 中等觸發頻率
        funding_spike_chance=0.06,
        oi_spike_chance=0.10,
        whale_chance=0.06,
        liquidation_high_chance=0.16,
        tape_aggression_chance=0.10,
    ),
    
    'extreme': ScenarioConfig(
        name="極致複利",
        version="scenario_3_extreme",
        daily_return_target=0.40,
        trades_per_day_min=20,
        trades_per_day_max=30,
        target_win_rate=0.75,
        
        tp_pct=0.0008,  # 0.08%
        sl_pct=0.0006,  # 0.06%
        time_stop_seconds=120,
        
        funding_threshold=0.025,
        oi_change_threshold=0.08,
        liquidation_threshold=500,
        whale_threshold=1000,
        
        ultra_high_leverage=25,
        high_leverage=22,
        medium_leverage=18,
        low_leverage=15,
        
        min_confidence=0.55,  # 較低過濾，更多交易
        
        # 高觸發頻率
        funding_spike_chance=0.08,
        oi_spike_chance=0.12,
        whale_chance=0.08,
        liquidation_high_chance=0.20,
        tape_aggression_chance=0.12,
    ),
}


class ScalpStrategy:
    """通用 Scalping 策略"""
    
    def __init__(self, config: ScenarioConfig):
        self.config = config
    
    def generate_signal(self, context: SignalContext) -> Optional[Dict]:
        """生成信號"""
        # 檢查各種觸發條件
        triggers = [
            self._check_funding_explosion(context),
            self._check_oi_spike(context),
            self._check_whale_shock(context),
            self._check_liquidation_cascade(context),
            self._check_tape_aggression(context),
        ]
        
        # 返回第一個有效信號
        for trigger in triggers:
            if trigger:
                confidence = trigger['confidence']
                if confidence >= self.config.min_confidence:
                    # 計算 TP/SL
                    entry_price = context.current_price
                    direction = trigger['direction']
                    
                    if direction == Direction.LONG:
                        tp = entry_price * (1 + self.config.tp_pct)
                        sl = entry_price * (1 - self.config.sl_pct)
                    else:
                        tp = entry_price * (1 - self.config.tp_pct)
                        sl = entry_price * (1 + self.config.sl_pct)
                    
                    return {
                        'direction': direction,
                        'trigger_type': trigger['type'],
                        'confidence': confidence,
                        'leverage': self._calculate_leverage(confidence),
                        'entry_price': entry_price,
                        'tp': tp,
                        'sl': sl,
                        'reason': trigger['reason']
                    }
        
        return None
    
    def _check_funding_explosion(self, ctx: SignalContext) -> Optional[Dict]:
        """Funding 爆倉"""
        if abs(ctx.funding_rate) > self.config.funding_threshold and ctx.oi_change_rate > 5:
            if ctx.funding_rate > self.config.funding_threshold:
                return {
                    'type': 'funding_explosion',
                    'direction': Direction.SHORT,
                    'confidence': 0.95,
                    'reason': f'多單爆倉 (Funding {ctx.funding_rate:.3f})'
                }
            elif ctx.funding_rate < -self.config.funding_threshold:
                return {
                    'type': 'funding_explosion',
                    'direction': Direction.LONG,
                    'confidence': 0.95,
                    'reason': f'空單爆倉 (Funding {ctx.funding_rate:.3f})'
                }
        return None
    
    def _check_oi_spike(self, ctx: SignalContext) -> Optional[Dict]:
        """OI 暴動"""
        if abs(ctx.oi_change_rate) > self.config.oi_change_threshold:
            direction = Direction.LONG if ctx.oi_change_rate > 0 else Direction.SHORT
            return {
                'type': 'oi_spike',
                'direction': direction,
                'confidence': 0.75,
                'reason': f'OI 暴動 ({ctx.oi_change_rate:+.1f}%)'
            }
        return None
    
    def _check_whale_shock(self, ctx: SignalContext) -> Optional[Dict]:
        """巨鯨異動"""
        if ctx.exchange_inflow_24h > self.config.whale_threshold:
            return {
                'type': 'whale_shock',
                'direction': Direction.SHORT,
                'confidence': 0.65,
                'reason': f'巨鯨流入 {ctx.exchange_inflow_24h:.0f} BTC'
            }
        elif ctx.exchange_outflow_24h > self.config.whale_threshold:
            return {
                'type': 'whale_shock',
                'direction': Direction.LONG,
                'confidence': 0.65,
                'reason': f'巨鯨流出 {ctx.exchange_outflow_24h:.0f} BTC'
            }
        return None
    
    def _check_liquidation_cascade(self, ctx: SignalContext) -> Optional[Dict]:
        """清算連鎖"""
        if ctx.recent_liquidations_volume > self.config.liquidation_threshold:
            direction = Direction.LONG if ctx.rsi < 40 else Direction.SHORT
            return {
                'type': 'liquidation_cascade',
                'direction': direction,
                'confidence': 0.80,
                'reason': f'高清算量 {ctx.recent_liquidations_volume:.0f}'
            }
        return None
    
    def _check_tape_aggression(self, ctx: SignalContext) -> Optional[Dict]:
        """成交流攻擊"""
        total_volume = ctx.aggressive_buy_volume + ctx.aggressive_sell_volume
        if total_volume > ctx.volume * 3:
            if ctx.aggressive_buy_volume > ctx.aggressive_sell_volume * 2:
                return {
                    'type': 'tape_aggression',
                    'direction': Direction.LONG,
                    'confidence': 0.60,
                    'reason': '買盤攻擊'
                }
            elif ctx.aggressive_sell_volume > ctx.aggressive_buy_volume * 2:
                return {
                    'type': 'tape_aggression',
                    'direction': Direction.SHORT,
                    'confidence': 0.60,
                    'reason': '賣盤攻擊'
                }
        return None
    
    def _calculate_leverage(self, confidence: float) -> int:
        """動態槓桿"""
        if confidence >= 0.85:
            return self.config.ultra_high_leverage
        elif confidence >= 0.75:
            return self.config.high_leverage
        elif confidence >= 0.65:
            return self.config.medium_leverage
        else:
            return self.config.low_leverage


class ScenarioBacktester:
    """方案回測器"""
    
    def __init__(self, config: ScenarioConfig, initial_capital: float = 100.0):
        self.config = config
        self.strategy = ScalpStrategy(config)
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
        print(f"🚀 {self.config.name} 回測開始...")
        print(f"   目標：每天 {self.config.daily_return_target:.0%}，3天翻倍")
        print()
        
        # 篩選時間
        df = df.copy()
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if start_date:
            df = df[df['timestamp'] >= start_date]
        if end_date:
            df = df[df['timestamp'] <= end_date]
        
        # 初始化
        current_capital = self.initial_capital
        current_position = None
        
        # 逐根K線
        for i in range(len(df)):
            if i % 10000 == 0 and i > 0:
                progress = i / len(df) * 100
                print(f"   進度: {progress:.1f}% (資金: {current_capital:.2f}U)")
            
            row = df.iloc[i]
            
            # 檢查出場
            if current_position:
                exit_info = self._check_exit(
                    current_position,
                    row,
                    df.iloc[i:min(i+10, len(df))]
                )
                
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
                        print(f"   💥 爆倉！")
                        break
            
            # 檢查入場
            if not current_position:
                context = self._build_context(df, i)
                signal = self.strategy.generate_signal(context)
                
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
        
        print(f"   ✅ 完成！最終資金: {current_capital:.2f}U")
        print()
        
        return self._generate_stats(current_capital)
    
    def _build_context(self, df: pd.DataFrame, idx: int) -> SignalContext:
        """構建信號上下文"""
        row = df.iloc[idx]
        window = df.iloc[max(0, idx-100):idx+1]
        
        # RSI
        delta = window['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1] if len(rsi) > 0 else 50
        
        # 模擬 L0 數據（根據方案頻率）
        cfg = self.config
        
        # Funding
        if np.random.random() < cfg.funding_spike_chance:
            funding_rate = 0.01 + np.random.choice([-0.08, 0.08])
        else:
            funding_rate = 0.01 + np.random.normal(0, 0.02)
        
        # OI
        if np.random.random() < cfg.oi_spike_chance:
            oi_change = 2.0 + np.random.choice([-20, 20])
        else:
            oi_change = 2.0 + np.random.normal(0, 3)
        
        # 巨鯨
        exchange_inflow = 0
        exchange_outflow = 0
        if np.random.random() < cfg.whale_chance:
            if np.random.random() < 0.5:
                exchange_inflow = np.random.uniform(1000, 3000)
            else:
                exchange_outflow = np.random.uniform(1000, 3000)
        
        # 清算
        if np.random.random() < cfg.liquidation_high_chance:
            liquidation_volume = np.random.uniform(800, 1500)
        else:
            liquidation_volume = np.random.uniform(200, 600)
        
        # Tape
        aggressive_buy = 0
        aggressive_sell = 0
        if np.random.random() < cfg.tape_aggression_chance:
            if np.random.random() < 0.5:
                aggressive_buy = row['volume'] * 4
            else:
                aggressive_sell = row['volume'] * 4
        
        return SignalContext(
            timestamp=row['timestamp'],
            current_price=row['close'],
            volume=row['volume'],
            rsi=current_rsi,
            funding_rate=funding_rate,
            oi_change_rate=oi_change,
            exchange_inflow_24h=exchange_inflow,
            exchange_outflow_24h=exchange_outflow,
            recent_liquidations_volume=liquidation_volume,
            aggressive_buy_volume=aggressive_buy,
            aggressive_sell_volume=aggressive_sell,
        )
    
    def _check_exit(self, position: Dict, current_row: pd.Series, future_window: pd.DataFrame) -> Optional[Dict]:
        """檢查出場"""
        entry_time = position['entry_time']
        tp = position['tp']
        sl = position['sl']
        direction = position['direction']
        
        # 時間止損
        time_elapsed = (current_row['timestamp'] - entry_time).total_seconds()
        if time_elapsed > self.config.time_stop_seconds:
            return {
                'exit_time': current_row['timestamp'],
                'exit_price': current_row['close'],
                'reason': 'TIME_STOP'
            }
        
        # TP/SL
        for _, row in future_window.iterrows():
            if direction == Direction.LONG:
                if row['high'] >= tp:
                    return {'exit_time': row['timestamp'], 'exit_price': tp, 'reason': 'TP'}
                if row['low'] <= sl:
                    return {'exit_time': row['timestamp'], 'exit_price': sl, 'reason': 'SL'}
            else:
                if row['low'] <= tp:
                    return {'exit_time': row['timestamp'], 'exit_price': tp, 'reason': 'TP'}
                if row['high'] >= sl:
                    return {'exit_time': row['timestamp'], 'exit_price': sl, 'reason': 'SL'}
        
        return None
    
    def _calculate_pnl(self, position: Dict, exit_info: Dict) -> Tuple[float, float]:
        """計算盈虧"""
        entry_price = position['entry_price']
        exit_price = exit_info['exit_price']
        direction = position['direction']
        leverage = position['leverage']
        capital = position['capital_at_entry']
        
        if direction == Direction.LONG:
            price_change = (exit_price - entry_price) / entry_price
        else:
            price_change = (entry_price - exit_price) / entry_price
        
        leveraged_pnl = price_change * leverage
        total_fee = self.config.taker_fee * 2
        slippage = self.config.slippage_pct
        
        net_pnl_pct = leveraged_pnl - total_fee - slippage
        pnl_dollar = capital * net_pnl_pct
        
        return net_pnl_pct, pnl_dollar
    
    def _generate_stats(self, final_capital: float) -> Dict:
        """生成統計"""
        if not self.trades:
            return {}
        
        wins = [t for t in self.trades if t['pnl_dollar'] > 0]
        losses = [t for t in self.trades if t['pnl_dollar'] <= 0]
        
        dates = sorted(self.daily_pnl.keys())
        days_traded = len(dates)
        
        # 3天週期
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
            'target_achieved': any(p['return_pct'] >= 1.0 for p in three_day_periods),
            'daily_pnl': self.daily_pnl,
        }


def print_comparison_report(results: Dict[str, Dict]):
    """打印對比報告"""
    print("=" * 100)
    print("📊 三方案對比報告：3天翻倍計劃")
    print("=" * 100)
    print()
    
    print(f"{'方案':<15} {'總交易':<10} {'勝率':<10} {'每日交易':<12} {'最終資金':<12} {'總回報':<12} {'3天達標':<10}")
    print("-" * 100)
    
    for scenario_key in ['conservative', 'aggressive', 'extreme']:
        stats = results[scenario_key]
        if not stats:
            continue
        
        name = stats['config_name']
        total_trades = stats['total_trades']
        win_rate = stats['win_rate']
        avg_daily = stats['avg_trades_per_day']
        final = stats['final_capital']
        total_return = stats['total_return_pct']
        achieved = "✅ 是" if stats['target_achieved'] else "❌ 否"
        
        print(f"{name:<15} {total_trades:<10} {win_rate:<10.1%} {avg_daily:<12.1f} {final:<12.2f}U {total_return:<12.1%} {achieved:<10}")
    
    print()
    print("=" * 100)
    print()
    
    # 詳細分析
    for scenario_key, scenario_name in [('conservative', '穩健'), ('aggressive', '激進'), ('extreme', '極致')]:
        stats = results[scenario_key]
        if not stats:
            continue
        
        print(f"📈 {stats['config_name']} 詳細分析:")
        print(f"   總交易: {stats['total_trades']} 筆")
        print(f"   勝率: {stats['win_rate']:.1%} ({stats['wins']}勝 / {stats['losses']}敗)")
        print(f"   平均盈利: {stats['avg_win_pct']:.2%}")
        print(f"   平均虧損: {stats['avg_loss_pct']:.2%}")
        print(f"   每日交易: {stats['avg_trades_per_day']:.1f} 筆")
        print()
        
        if stats['three_day_periods']:
            print(f"   🎯 3天週期表現:")
            for i, period in enumerate(stats['three_day_periods'][:5], 1):  # 只顯示前5個
                ret = period['return_pct']
                status = "✅" if ret >= 1.0 else "❌"
                print(f"      週期{i}: {ret:+.1%} ({period['pnl_dollar']:+.2f}U) {status}")
            
            success_rate = sum(1 for p in stats['three_day_periods'] if p['return_pct'] >= 1.0) / len(stats['three_day_periods'])
            print(f"      成功率: {success_rate:.1%}")
        
        print()
    
    # 推薦
    print("=" * 100)
    print("🎯 推薦方案:")
    print()
    
    best_scenario = None
    best_score = 0
    
    for key in ['conservative', 'aggressive', 'extreme']:
        stats = results[key]
        if not stats:
            continue
        
        # 評分：勝率*50 + 達標率*30 + 回報*20
        win_rate_score = stats['win_rate'] * 50
        success_periods = [p for p in stats['three_day_periods'] if p['return_pct'] >= 1.0]
        success_rate = len(success_periods) / len(stats['three_day_periods']) if stats['three_day_periods'] else 0
        achieve_score = success_rate * 30
        return_score = min(stats['total_return_pct'], 5.0) / 5.0 * 20  # 最高5倍
        
        total_score = win_rate_score + achieve_score + return_score
        
        if total_score > best_score:
            best_score = total_score
            best_scenario = stats
    
    if best_scenario:
        print(f"✅ 最佳方案：{best_scenario['config_name']}")
        print(f"   勝率: {best_scenario['win_rate']:.1%}")
        print(f"   3天達標: {'是' if best_scenario['target_achieved'] else '否'}")
        print(f"   總回報: {best_scenario['total_return_pct']:.1%}")
        print()
        print(f"💡 建議：先用小資金（50-100U）測試此方案")
    
    print("=" * 100)


def main():
    """主函數"""
    # 讀取數據
    print("讀取歷史數據...")
    df = pd.read_parquet('data/historical/BTCUSDT_15m.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    print(f"數據範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    print()
    
    # 運行三個方案
    results = {}
    
    for scenario_key, config in SCENARIOS.items():
        print("=" * 100)
        backtester = ScenarioBacktester(config, initial_capital=100.0)
        stats = backtester.run_backtest(
            df,
            start_date='2024-01-01',
            end_date='2025-11-10'
        )
        results[scenario_key] = stats
    
    # 打印對比報告
    print()
    print_comparison_report(results)
    
    # 保存結果
    output = {
        'backtest_date': datetime.now().isoformat(),
        'scenarios': {k: v for k, v in results.items()},
        'configs': {k: v.to_dict() for k, v in SCENARIOS.items()},
    }
    
    output_file = 'backtest_results/3day_double_comparison.json'
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"💾 結果已保存至: {output_file}")


if __name__ == "__main__":
    main()
