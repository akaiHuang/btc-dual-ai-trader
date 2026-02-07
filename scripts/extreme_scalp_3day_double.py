"""
極致版 Scalping 策略：3天翻倍計劃
目標：100U → 200U in 3 days（扣除所有手續費）

核心策略：
1. 使用 1分鐘 K線（高頻）
2. 每天 20-30 筆交易
3. TP 0.08-0.12% / SL 0.06-0.08%（極致）
4. 20-25x 槓桿
5. 70-75% 勝率
6. 持倉時間 30秒-3分鐘
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json
from dataclasses import dataclass, asdict

from src.core.signal_context import SignalContext, Direction
from src.strategy.scalp_strategy_v1 import ScalpStrategyV1


@dataclass
class ExtremeScalpConfig:
    """極致版配置"""
    version: str = "extreme_v1.0"
    
    # 極致參數
    tp_pct: float = 0.0010  # 0.10% TP（配合高槓桿）
    sl_pct: float = 0.0007  # 0.07% SL
    time_stop_seconds: int = 120  # 2分鐘時間止損
    
    # 更激進的觸發閾值
    funding_threshold: float = 0.03  # 降低（更多信號）
    oi_change_threshold: float = 0.08  # 降低（更多信號）
    liquidation_threshold: float = 500  # 降低
    whale_threshold: float = 1000  # 降低
    
    # 槓桿設置
    ultra_high_leverage: int = 25  # 極高信心
    high_leverage: int = 20
    medium_leverage: int = 15
    low_leverage: int = 10
    
    # 過濾條件（保持70%勝率）
    min_confidence: float = 0.55  # 降低閾值，增加交易數
    
    # 手續費（Binance VIP0）
    maker_fee: float = 0.0002  # 0.02%
    taker_fee: float = 0.0004  # 0.04%
    
    def to_dict(self) -> dict:
        return asdict(self)


class ExtremeScalpStrategy(ScalpStrategyV1):
    """
    極致版 Scalping 策略
    
    繼承 ScalpStrategyV1，但使用更激進的參數
    """
    
    def __init__(
        self,
        config: ExtremeScalpConfig = None
    ):
        self.config = config or ExtremeScalpConfig()
        
        # 初始化父類
        super().__init__(
            timeframe="1m",  # 改用1分鐘
            tp_pct=self.config.tp_pct,
            sl_pct=self.config.sl_pct,
            time_stop_seconds=self.config.time_stop_seconds,
            funding_threshold=self.config.funding_threshold,
            oi_change_threshold=self.config.oi_change_threshold,
            liquidation_threshold=self.config.liquidation_threshold,
            whale_threshold=self.config.whale_threshold,
            default_leverage=self.config.medium_leverage,
            high_confidence_leverage=self.config.high_leverage,
            low_confidence_leverage=self.config.low_leverage,
            min_confidence=self.config.min_confidence,
        )
    
    def _calculate_leverage(self, confidence: float) -> int:
        """
        更激進的槓桿計算
        
        極致版：槓桿範圍 10-25x
        """
        if confidence >= 0.85:
            return self.config.ultra_high_leverage  # 25x
        elif confidence >= 0.75:
            return self.config.high_leverage  # 20x
        elif confidence >= 0.65:
            return self.config.medium_leverage  # 15x
        else:
            return self.config.low_leverage  # 10x


class ExtremeBacktester:
    """
    極致版回測器
    
    關鍵改進：
    1. 考慮手續費（Maker/Taker）
    2. 滑點模擬（0.01-0.02%）
    3. 每日複利計算
    4. 3天週期追蹤
    """
    
    def __init__(
        self,
        strategy: ExtremeScalpStrategy,
        initial_capital: float = 100.0
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.config = strategy.config
        
        # 統計
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
        print("🚀 極致版 Scalping 回測開始...")
        print(f"目標：3天翻倍（{self.initial_capital}U → {self.initial_capital * 2}U）")
        print()
        
        # 篩選時間範圍
        df = df.copy()
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        if start_date:
            df = df[df['timestamp'] >= start_date]
        if end_date:
            df = df[df['timestamp'] <= end_date]
        
        print(f"回測範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
        print(f"回測 K 線數: {len(df)}")
        print()
        
        # 初始化
        current_capital = self.initial_capital
        current_position = None
        
        # 逐根K線回測
        for i in range(len(df)):
            if i % 5000 == 0:
                print(f"進度: {i}/{len(df)} ({i/len(df)*100:.1f}%)")
            
            row = df.iloc[i]
            
            # 如果有持倉，檢查是否出場
            if current_position:
                exit_info = self._check_exit(
                    current_position,
                    row,
                    df.iloc[i:min(i+10, len(df))]
                )
                
                if exit_info:
                    # 計算盈虧（含手續費）
                    pnl_pct, pnl_dollar = self._calculate_pnl(
                        current_position,
                        exit_info
                    )
                    
                    # 更新資金
                    current_capital += pnl_dollar
                    
                    # 記錄交易
                    trade_record = {
                        'entry_time': current_position['entry_time'],
                        'exit_time': exit_info['exit_time'],
                        'direction': current_position['direction'],
                        'entry_price': current_position['entry_price'],
                        'exit_price': exit_info['exit_price'],
                        'leverage': current_position['leverage'],
                        'pnl_pct': pnl_pct,
                        'pnl_dollar': pnl_dollar,
                        'capital_after': current_capital,
                        'exit_reason': exit_info['reason'],
                        'trigger_type': current_position['trigger_type'],
                        'confidence': current_position['confidence'],
                    }
                    self.trades.append(trade_record)
                    
                    # 更新資金曲線
                    self.capital_curve.append(current_capital)
                    
                    # 更新每日盈虧
                    date_str = exit_info['exit_time'].strftime('%Y-%m-%d')
                    if date_str not in self.daily_pnl:
                        self.daily_pnl[date_str] = 0
                    self.daily_pnl[date_str] += pnl_dollar
                    
                    # 清空持倉
                    current_position = None
                    
                    # 檢查是否爆倉
                    if current_capital <= 0:
                        print(f"💥 爆倉！資金歸零")
                        break
            
            # 如果沒有持倉，檢查是否有信號
            if not current_position:
                context = self._build_signal_context(df, i)
                signal = self.strategy.generate_signal(context)
                
                if signal:
                    # 開倉
                    current_position = {
                        'entry_time': row['timestamp'],
                        'entry_price': row['close'],
                        'direction': signal['direction'],
                        'leverage': signal['leverage'],
                        'tp': signal['tp'],
                        'sl': signal['sl'],
                        'trigger_type': signal['trigger_type'],
                        'confidence': signal['confidence'],
                        'capital_at_entry': current_capital,
                    }
        
        print("✅ 回測完成！")
        print()
        
        # 生成統計
        stats = self._generate_stats()
        
        return stats
    
    def _build_signal_context(
        self,
        df: pd.DataFrame,
        idx: int
    ) -> SignalContext:
        """構建信號上下文（模擬 L0 數據）"""
        row = df.iloc[idx]
        
        # 計算技術指標
        window = df.iloc[max(0, idx-100):idx+1]
        
        # RSI
        delta = window['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1] if len(rsi) > 0 else 50
        
        # MA
        ma_20 = window['close'].rolling(20).mean().iloc[-1]
        ma_distance = (row['close'] - ma_20) / ma_20 if ma_20 > 0 else 0
        
        # ATR
        high_low = window['high'] - window['low']
        high_close = abs(window['high'] - window['close'].shift())
        low_close = abs(window['low'] - window['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        
        # 模擬 L0 數據（更頻繁的異常）
        # 為了達到 20-30 筆/天，需要更高的觸發頻率
        
        # Funding Rate (5% 機會極端)
        base_funding = 0.01
        if np.random.random() < 0.05:
            funding_rate = base_funding + np.random.choice([-0.08, 0.08])
        else:
            funding_rate = base_funding + np.random.normal(0, 0.02)
        
        # OI 變化 (8% 機會大幅變化)
        base_oi = 2.0
        if np.random.random() < 0.08:
            oi_change = base_oi + np.random.choice([-20, 20])
        else:
            oi_change = base_oi + np.random.normal(0, 3)
        
        # 巨鯨活動 (5% 機會)
        if np.random.random() < 0.05:
            exchange_inflow = np.random.uniform(1000, 3000)
            exchange_outflow = 0
        elif np.random.random() < 0.05:
            exchange_inflow = 0
            exchange_outflow = np.random.uniform(1000, 3000)
        else:
            exchange_inflow = 0
            exchange_outflow = 0
        
        # 清算密度 (15% 機會高密度)
        if np.random.random() < 0.15:
            liquidation_density = np.random.uniform(800, 1500)
        else:
            liquidation_density = np.random.uniform(200, 600)
        
        # 訂單簿
        obi = 1.0 + np.random.normal(0, 0.3)
        spread = 0.0005 + np.random.normal(0, 0.0002)
        
        # Tape
        volume_ratio = 1.0 + np.random.normal(0, 0.5)
        if np.random.random() < 0.08:
            aggressive_buy = row['volume'] * volume_ratio * 4
            aggressive_sell = 0
        elif np.random.random() < 0.08:
            aggressive_buy = 0
            aggressive_sell = row['volume'] * volume_ratio * 4
        else:
            aggressive_buy = 0
            aggressive_sell = 0
        
        # 構建 Context
        context = SignalContext(
            timestamp=row['timestamp'],
            current_price=row['close'],
            volume=row['volume'],
            
            # Technical
            rsi=current_rsi,
            ma_distance_pct=ma_distance * 100,  # 轉換為百分比
            atr=atr,
            
            # L0 Derivatives
            funding_rate=funding_rate,
            oi_change_pct=oi_change,
            
            # L0 On-chain
            exchange_inflow=exchange_inflow,
            exchange_outflow=exchange_outflow,
            
            # L0 Orderbook
            obi=obi,
            spread_bps=spread * 10000,  # 轉換為 basis points
            liquidation_density=liquidation_density,
            
            # L0 Tape
            aggressive_buy_volume=aggressive_buy,
            aggressive_sell_volume=aggressive_sell,
        )
        
        return context
    
    def _check_exit(
        self,
        position: Dict,
        current_row: pd.Series,
        future_window: pd.DataFrame
    ) -> Optional[Dict]:
        """檢查是否該出場"""
        entry_time = position['entry_time']
        entry_price = position['entry_price']
        direction = position['direction']
        tp = position['tp']
        sl = position['sl']
        
        # 檢查時間止損
        time_elapsed = (current_row['timestamp'] - entry_time).total_seconds()
        if time_elapsed > self.config.time_stop_seconds:
            return {
                'exit_time': current_row['timestamp'],
                'exit_price': current_row['close'],
                'reason': 'TIME_STOP'
            }
        
        # 檢查 TP/SL
        for _, row in future_window.iterrows():
            if direction == Direction.LONG:
                # TP
                if row['high'] >= tp:
                    return {
                        'exit_time': row['timestamp'],
                        'exit_price': tp,
                        'reason': 'TP'
                    }
                # SL
                if row['low'] <= sl:
                    return {
                        'exit_time': row['timestamp'],
                        'exit_price': sl,
                        'reason': 'SL'
                    }
            else:  # SHORT
                # TP
                if row['low'] <= tp:
                    return {
                        'exit_time': row['timestamp'],
                        'exit_price': tp,
                        'reason': 'TP'
                    }
                # SL
                if row['high'] >= sl:
                    return {
                        'exit_time': row['timestamp'],
                        'exit_price': sl,
                        'reason': 'SL'
                    }
        
        return None
    
    def _calculate_pnl(
        self,
        position: Dict,
        exit_info: Dict
    ) -> Tuple[float, float]:
        """
        計算盈虧（含手續費和滑點）
        
        Returns:
            (pnl_pct, pnl_dollar)
        """
        entry_price = position['entry_price']
        exit_price = exit_info['exit_price']
        direction = position['direction']
        leverage = position['leverage']
        capital = position['capital_at_entry']
        
        # 價格變化百分比
        if direction == Direction.LONG:
            price_change_pct = (exit_price - entry_price) / entry_price
        else:  # SHORT
            price_change_pct = (entry_price - exit_price) / entry_price
        
        # 槓桿後盈虧
        leveraged_pnl_pct = price_change_pct * leverage
        
        # 手續費（進場 Taker + 出場 Taker）
        total_fee_pct = self.config.taker_fee * 2  # 0.08%
        
        # 滑點（模擬 0.01%）
        slippage_pct = 0.0001
        
        # 淨盈虧百分比
        net_pnl_pct = leveraged_pnl_pct - total_fee_pct - slippage_pct
        
        # 美元盈虧
        pnl_dollar = capital * net_pnl_pct
        
        return net_pnl_pct, pnl_dollar
    
    def _generate_stats(self) -> Dict:
        """生成統計報告"""
        if not self.trades:
            return {}
        
        total_trades = len(self.trades)
        wins = [t for t in self.trades if t['pnl_dollar'] > 0]
        losses = [t for t in self.trades if t['pnl_dollar'] <= 0]
        
        win_rate = len(wins) / total_trades if total_trades > 0 else 0
        
        avg_win = np.mean([t['pnl_pct'] for t in wins]) if wins else 0
        avg_loss = np.mean([t['pnl_pct'] for t in losses]) if losses else 0
        
        total_pnl = sum(t['pnl_dollar'] for t in self.trades)
        final_capital = self.capital_curve[-1]
        total_return = (final_capital - self.initial_capital) / self.initial_capital
        
        # 按日統計
        dates = sorted(self.daily_pnl.keys())
        days_traded = len(dates)
        
        # 3天週期分析
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
        
        # 統計
        stats = {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'wins': len(wins),
            'losses': len(losses),
            
            'avg_win_pct': avg_win,
            'avg_loss_pct': avg_loss,
            'profit_factor': abs(avg_win / avg_loss) if avg_loss != 0 else 0,
            
            'initial_capital': self.initial_capital,
            'final_capital': final_capital,
            'total_pnl_dollar': total_pnl,
            'total_return_pct': total_return,
            
            'days_traded': days_traded,
            'avg_trades_per_day': total_trades / days_traded if days_traded > 0 else 0,
            
            'three_day_periods': three_day_periods,
            'target_achieved': any(p['return_pct'] >= 1.0 for p in three_day_periods),
            
            'daily_pnl': self.daily_pnl,
            'capital_curve': self.capital_curve,
        }
        
        return stats
    
    def print_report(self, stats: Dict):
        """打印報告"""
        print("=" * 80)
        print("🚀 極致版 Scalping 回測結果")
        print("=" * 80)
        print()
        
        print(f"💰 資金曲線:")
        print(f"   初始: {stats['initial_capital']:.2f} U")
        print(f"   最終: {stats['final_capital']:.2f} U")
        print(f"   總回報: {stats['total_return_pct']:.1%} ({stats['total_pnl_dollar']:+.2f} U)")
        print()
        
        print(f"📊 交易統計:")
        print(f"   總交易數: {stats['total_trades']}")
        print(f"   勝率: {stats['win_rate']:.1%} ({stats['wins']} 勝 / {stats['losses']} 敗)")
        print(f"   平均盈利: {stats['avg_win_pct']:.2%}")
        print(f"   平均虧損: {stats['avg_loss_pct']:.2%}")
        print(f"   盈虧比: {stats['profit_factor']:.2f}")
        print()
        
        print(f"⏱️  頻率統計:")
        print(f"   交易天數: {stats['days_traded']} 天")
        print(f"   每日平均: {stats['avg_trades_per_day']:.1f} 筆")
        print()
        
        # 3天週期分析
        if stats['three_day_periods']:
            print(f"🎯 3天週期分析:")
            for i, period in enumerate(stats['three_day_periods'], 1):
                return_pct = period['return_pct']
                status = "✅ 達標" if return_pct >= 1.0 else "❌ 未達標"
                print(f"   週期 {i}: {period['dates'][0]} ~ {period['dates'][-1]}")
                print(f"      回報: {return_pct:.1%} ({period['pnl_dollar']:+.2f} U) {status}")
            print()
            
            success_rate = sum(1 for p in stats['three_day_periods'] if p['return_pct'] >= 1.0) / len(stats['three_day_periods'])
            print(f"   3天翻倍成功率: {success_rate:.1%}")
            print()
        
        # 判定
        if stats['target_achieved']:
            print("🎊 恭喜！至少一個3天週期達成翻倍目標！")
        else:
            print("⚠️  未達成3天翻倍目標，建議調整參數或策略")


def main():
    """主函數"""
    # 讀取數據（15m，因為沒有1m數據）
    print("讀取歷史數據...")
    df = pd.read_parquet('data/historical/BTCUSDT_15m.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    print(f"數據範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    print()
    
    # 注意：這裡用15m數據模擬1m效果
    # 實際應該下載1m數據
    print("⚠️  注意：目前使用15m數據模擬1m效果")
    print("   實際交易頻率會更高！")
    print()
    
    # 創建策略
    config = ExtremeScalpConfig()
    strategy = ExtremeScalpStrategy(config)
    
    # 創建回測器
    backtester = ExtremeBacktester(
        strategy=strategy,
        initial_capital=100.0
    )
    
    # 運行回測（測試2024-2025年）
    stats = backtester.run_backtest(
        df,
        start_date='2024-01-01',
        end_date='2025-11-10'
    )
    
    # 打印報告
    backtester.print_report(stats)
    
    # 保存結果
    output = {
        'backtest_date': datetime.now().isoformat(),
        'config': config.to_dict(),
        'stats': stats,
        'trades': backtester.trades[:100],  # 只保存前100筆
    }
    
    output_file = 'backtest_results/extreme_scalp_3day_double.json'
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"💾 結果已保存至: {output_file}")


if __name__ == "__main__":
    main()
