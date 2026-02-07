"""
Scalping Strategy V1 回測腳本

測試 ScalpStrategyV1 在歷史數據上的表現
由於缺少實時 L0 數據（Funding/OI/鏈上），
先用模擬數據驗證策略邏輯和統計頻率
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict
import json

from src.strategy.scalp_strategy_v1 import ScalpStrategyV1, ScalpSignal, ScalpTrigger
from src.core.signal_context import SignalContext, Direction, ImpactLevel, MarketRegime


class ScalpBacktester:
    """
    Scalping 策略回測器
    
    注意：由於缺少實時 Funding/OI/鏈上數據，
    我們使用模擬數據來驗證策略邏輯
    """
    
    def __init__(self, strategy: ScalpStrategyV1):
        self.strategy = strategy
        self.signals: List[ScalpSignal] = []
        self.trades: List[Dict] = []
    
    def _simulate_l0_data(self, df: pd.DataFrame, idx: int) -> SignalContext:
        """
        模擬 L0 數據
        
        真實環境中，這些數據來自：
        - Binance WebSocket (Funding, OI, 訂單簿)
        - Glassnode / Whale Alert API (鏈上數據)
        - Twitter / News API (新聞因子)
        
        現在我們用隨機+規則模擬，主要是驗證策略邏輯
        """
        row = df.iloc[idx]
        
        # 計算技術指標
        rsi = self._calculate_rsi(df, idx)
        volume_ratio = row['volume'] / df['volume'].rolling(20).mean().iloc[idx] if idx >= 20 else 1.0
        
        # 模擬 Funding Rate（基於價格變化）
        # 真實環境：從交易所 API 獲取
        price_change_1h = (row['close'] - df.iloc[max(0, idx-4)]['close']) / df.iloc[max(0, idx-4)]['close']
        funding_rate = np.clip(price_change_1h * 2, -0.15, 0.15)  # 模擬值
        
        # 模擬 OI 變化（基於成交量異常）
        # 真實環境：從交易所 API 獲取
        avg_volume = df['volume'].rolling(20).mean().iloc[idx] if idx >= 20 else df['volume'].mean()
        oi_change_rate = (volume_ratio - 1.0) * 0.5  # 模擬值
        oi_at_high_level = volume_ratio > 1.5
        
        # 模擬 訂單簿 OBI（基於價格和成交量）
        # 真實環境：從 WebSocket 實時獲取
        price_momentum = (row['close'] - row['open']) / row['open']
        obi = np.clip(price_momentum * 10, -1.0, 1.0)  # 模擬值
        
        # 模擬 Taker Ratio（基於 OBI）
        # 真實環境：計算主動買賣比
        taker_ratio = 1.0 + obi * 0.8  # 模擬值
        
        # 模擬清算數據（基於價格急跌/急漲）
        # 真實環境：從交易所獲取清算事件
        price_drop_pct = (row['low'] - row['high']) / row['high']
        liquidation_volume = abs(price_drop_pct) * 5000 if abs(price_drop_pct) > 0.02 else 0
        liquidation_direction = Direction.LONG if price_drop_pct < -0.02 else Direction.SHORT if price_drop_pct > 0.02 else Direction.NEUTRAL
        price_breaks_long_liq = price_drop_pct < -0.03
        price_breaks_short_liq = price_drop_pct > 0.03
        
        # 模擬鏈上數據（基於大額成交量）
        # 真實環境：Whale Alert / Glassnode API
        net_flow = (volume_ratio - 1.0) * 1000 if volume_ratio > 2.0 else 0
        whale_alert_level = ImpactLevel.HIGH if abs(net_flow) > 2000 else ImpactLevel.NONE
        
        # 構建 SignalContext
        context = SignalContext(
            timestamp=row['timestamp'],
            current_price=row['close'],
            
            # 技術指標
            rsi=rsi,
            volume_ratio=volume_ratio,
            
            # 訂單簿
            obi=obi,
            spread_bps=5.0,  # 模擬固定值
            taker_ratio=taker_ratio,
            
            # 衍生品
            funding_rate=funding_rate,
            oi_change_rate=oi_change_rate,
            oi_at_high_level=oi_at_high_level,
            open_interest=10000,  # 模擬值
            
            # 清算
            recent_liquidations_volume=liquidation_volume,
            liquidation_direction=liquidation_direction,
            price_breaks_long_liq_zone=price_breaks_long_liq,
            price_breaks_short_liq_zone=price_breaks_short_liq,
            
            # 鏈上
            net_flow=net_flow,
            whale_alert_level=whale_alert_level,
            
            # 新聞（暫時不模擬）
            news_bias=0,
            news_strength=0.0,
            news_impact_level=ImpactLevel.NONE,
        )
        
        return context
    
    def _calculate_rsi(self, df: pd.DataFrame, idx: int, period: int = 14) -> float:
        """計算 RSI"""
        if idx < period:
            return 50.0
        
        closes = df['close'].iloc[max(0, idx-period):idx+1].values
        deltas = np.diff(closes)
        
        gains = deltas.copy()
        losses = deltas.copy()
        gains[gains < 0] = 0
        losses[losses > 0] = 0
        losses = abs(losses)
        
        avg_gain = np.mean(gains) if len(gains) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 0
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def run_backtest(
        self,
        df: pd.DataFrame,
        start_date: str = None,
        end_date: str = None
    ) -> Dict:
        """
        運行回測
        
        Args:
            df: K 線數據（1m 或 5m）
            start_date: 開始日期
            end_date: 結束日期
        
        Returns:
            回測結果統計
        """
        print(f"開始回測 ScalpStrategyV1...")
        print(f"數據範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
        print(f"總 K 線數: {len(df)}")
        print()
        
        # 篩選日期範圍
        if start_date:
            df = df[df['timestamp'] >= start_date]
        if end_date:
            df = df[df['timestamp'] <= end_date]
        
        print(f"回測範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
        print(f"回測 K 線數: {len(df)}")
        print()
        
        # 重置索引
        df = df.reset_index(drop=True)
        
        # 逐根 K 線掃描
        for idx in range(len(df)):
            if idx % 1000 == 0:
                print(f"進度: {idx}/{len(df)} ({idx/len(df)*100:.1f}%)")
            
            # 模擬 L0 數據
            context = self._simulate_l0_data(df, idx)
            
            # 生成信號
            signal = self.strategy.generate_signal(df, context)
            
            if signal:
                self.signals.append(signal)
                
                # 模擬交易執行
                trade = self._simulate_trade(df, idx, signal)
                if trade:
                    self.trades.append(trade)
        
        print(f"✅ 回測完成！")
        print()
        
        # 生成統計報告
        stats = self._generate_stats(df)
        
        return stats
    
    def _simulate_trade(
        self,
        df: pd.DataFrame,
        entry_idx: int,
        signal: ScalpSignal
    ) -> Dict:
        """
        模擬交易執行
        
        假設：
        1. 立即成交（無滑點）
        2. 在 TP / SL / 時間止損 三者中最先觸發
        """
        entry_price = signal.entry_price
        tp_price = signal.tp_price
        sl_price = signal.sl_price
        
        # 計算時間止損 K 線數（假設 1m K 線）
        time_stop_bars = signal.time_stop_seconds // 60
        
        # 掃描後續 K 線
        exit_price = None
        exit_reason = None
        exit_idx = None
        
        for i in range(entry_idx + 1, min(entry_idx + time_stop_bars + 1, len(df))):
            row = df.iloc[i]
            
            if signal.direction == Direction.LONG:
                # 做多
                if row['high'] >= tp_price:
                    # TP 觸發
                    exit_price = tp_price
                    exit_reason = "TP"
                    exit_idx = i
                    break
                elif row['low'] <= sl_price:
                    # SL 觸發
                    exit_price = sl_price
                    exit_reason = "SL"
                    exit_idx = i
                    break
            
            else:  # SHORT
                # 做空
                if row['low'] <= tp_price:
                    # TP 觸發
                    exit_price = tp_price
                    exit_reason = "TP"
                    exit_idx = i
                    break
                elif row['high'] >= sl_price:
                    # SL 觸發
                    exit_price = sl_price
                    exit_reason = "SL"
                    exit_idx = i
                    break
        
        # 時間止損
        if exit_price is None:
            exit_idx = min(entry_idx + time_stop_bars, len(df) - 1)
            exit_price = df.iloc[exit_idx]['close']
            exit_reason = "TIME_STOP"
        
        # 計算盈虧
        if signal.direction == Direction.LONG:
            pnl_pct = (exit_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - exit_price) / entry_price
        
        pnl_pct_leveraged = pnl_pct * signal.leverage
        
        trade = {
            'entry_time': signal.timestamp,
            'exit_time': df.iloc[exit_idx]['timestamp'],
            'direction': signal.direction.value,
            'trigger': signal.trigger_type.value,
            'confidence': signal.confidence,
            'leverage': signal.leverage,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'exit_reason': exit_reason,
            'pnl_pct': pnl_pct,
            'pnl_pct_leveraged': pnl_pct_leveraged,
            'holding_bars': exit_idx - entry_idx,
            'reason': signal.reason,
        }
        
        return trade
    
    def _generate_stats(self, df: pd.DataFrame) -> Dict:
        """生成統計報告"""
        if not self.trades:
            print("❌ 沒有交易記錄")
            return {}
        
        trades_df = pd.DataFrame(self.trades)
        
        # 基礎統計
        total_trades = len(trades_df)
        winning_trades = len(trades_df[trades_df['pnl_pct_leveraged'] > 0])
        losing_trades = len(trades_df[trades_df['pnl_pct_leveraged'] < 0])
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        
        # 盈虧統計
        total_pnl = trades_df['pnl_pct_leveraged'].sum()
        avg_win = trades_df[trades_df['pnl_pct_leveraged'] > 0]['pnl_pct_leveraged'].mean() if winning_trades > 0 else 0
        avg_loss = trades_df[trades_df['pnl_pct_leveraged'] < 0]['pnl_pct_leveraged'].mean() if losing_trades > 0 else 0
        
        # 出場統計
        exit_reasons = trades_df['exit_reason'].value_counts().to_dict()
        
        # 觸發類型統計
        trigger_stats = trades_df.groupby('trigger').agg({
            'pnl_pct_leveraged': ['count', 'mean', lambda x: (x > 0).sum() / len(x)]
        }).round(4)
        trigger_stats.columns = ['count', 'avg_pnl', 'win_rate']
        
        # 日頻率統計
        trades_df['date'] = pd.to_datetime(trades_df['entry_time']).dt.date
        daily_counts = trades_df.groupby('date').size()
        avg_trades_per_day = daily_counts.mean()
        
        # 持倉時間統計
        avg_holding_bars = trades_df['holding_bars'].mean()
        avg_holding_minutes = avg_holding_bars  # 假設 1m K 線
        
        stats = {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'total_pnl_pct': total_pnl,
            'avg_win_pct': avg_win,
            'avg_loss_pct': avg_loss,
            'profit_factor': abs(avg_win / avg_loss) if avg_loss != 0 else 0,
            'exit_reasons': exit_reasons,
            'trigger_stats': trigger_stats.to_dict(),
            'avg_trades_per_day': avg_trades_per_day,
            'avg_holding_minutes': avg_holding_minutes,
        }
        
        # 打印報告
        self._print_stats(stats)
        
        return stats
    
    def _print_stats(self, stats: Dict):
        """打印統計報告"""
        print("=" * 80)
        print("📊 Scalping Strategy V1 回測結果")
        print("=" * 80)
        print()
        
        print(f"總交易數: {stats['total_trades']}")
        print(f"勝率: {stats['win_rate']:.2%}")
        print(f"  - 盈利交易: {stats['winning_trades']}")
        print(f"  - 虧損交易: {stats['losing_trades']}")
        print()
        
        print(f"盈虧統計:")
        print(f"  - 總盈虧: {stats['total_pnl_pct']:.2%} (槓桿後)")
        print(f"  - 平均盈利: {stats['avg_win_pct']:.2%}")
        print(f"  - 平均虧損: {stats['avg_loss_pct']:.2%}")
        print(f"  - 盈虧比: {stats['profit_factor']:.2f}")
        print()
        
        print(f"頻率統計:")
        print(f"  - 每日平均交易數: {stats['avg_trades_per_day']:.1f} 筆")
        print(f"  - 平均持倉時間: {stats['avg_holding_minutes']:.1f} 分鐘")
        print()
        
        print(f"出場原因:")
        for reason, count in stats['exit_reasons'].items():
            pct = count / stats['total_trades'] * 100
            print(f"  - {reason}: {count} ({pct:.1f}%)")
        print()
        
        print(f"觸發類型統計:")
        trigger_stats = stats['trigger_stats']
        for trigger in trigger_stats['count'].keys():
            count = trigger_stats['count'][trigger]
            avg_pnl = trigger_stats['avg_pnl'][trigger]
            win_rate = trigger_stats['win_rate'][trigger]
            print(f"  - {trigger}:")
            print(f"      數量: {count}, 勝率: {win_rate:.2%}, 平均盈虧: {avg_pnl:.2%}")
        print()


def main():
    """主函數"""
    # 讀取數據
    print("讀取歷史數據...")
    df = pd.read_parquet('data/historical/BTCUSDT_15m.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 轉換為 1m（模擬，實際應該有真實 1m 數據）
    # 這裡我們用 15m 數據，但策略邏輯不變
    print(f"數據範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    print(f"總 K 線數: {len(df)}")
    print()
    
    # 創建策略
    strategy = ScalpStrategyV1(
        timeframe="1m",
        tp_pct=0.0015,
        sl_pct=0.001,
        time_stop_seconds=180,
        min_confidence=0.6,
        
        # 閾值設定
        funding_threshold=0.05,
        oi_change_threshold=0.15,
        liquidation_threshold=1000,
        whale_threshold=2000,
    )
    
    # 創建回測器
    backtester = ScalpBacktester(strategy)
    
    # 運行回測（只測試 2025 年，因為數據較新）
    stats = backtester.run_backtest(
        df,
        start_date='2025-01-01',
        end_date='2025-11-10'
    )
    
    # 保存結果
    if stats:
        output_file = 'backtest_results/scalp_v1_test_2025.json'
        with open(output_file, 'w') as f:
            # 轉換 DataFrame 為 dict
            stats_copy = stats.copy()
            if 'trigger_stats' in stats_copy:
                stats_copy['trigger_stats'] = {
                    k: {kk: float(vv) for kk, vv in v.items()}
                    for k, v in stats_copy['trigger_stats'].items()
                }
            
            json.dump(stats_copy, f, indent=2, default=str)
        
        print(f"✅ 結果已保存至: {output_file}")


if __name__ == "__main__":
    main()
