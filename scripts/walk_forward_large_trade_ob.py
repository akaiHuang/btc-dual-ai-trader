"""
Walk-Forward Optimization 回測框架
用於測試「幣安大單 + Order Book」策略

流程：
1. 2020年數據 → 優化參數 → 測試 2021年
2. 2021年數據 → 優化參數 → 測試 2022年
3. 2022年數據 → 優化參數 → 測試 2023年
4. 2023年數據 → 優化參數 → 測試 2024年
5. 2024年數據 → 優化參數 → 測試 2025年

目的：避免過擬合，確保策略在未來數據上有效
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import itertools

@dataclass
class StrategyConfig:
    """策略配置"""
    # 大單參數
    large_trade_threshold: float = 10.0  # BTC
    aggressive_net_threshold: float = 30.0  # BTC
    
    # 訂單簿參數
    orderbook_imbalance_threshold: float = 0.3  # [-1, 1]
    orderbook_depth: int = 20
    
    # 技術指標參數
    rsi_oversold: float = 30
    rsi_overbought: float = 70
    use_ma_filter: bool = True
    use_volume_filter: bool = True
    
    # 交易參數
    min_confidence: float = 0.5
    tp_pct: float = 0.0015  # 0.15%
    sl_pct: float = 0.0010  # 0.10%
    time_stop_minutes: int = 180
    leverage: int = 20
    
    # 權重
    large_trade_weight: float = 0.4
    orderbook_weight: float = 0.3
    technical_weight: float = 0.3

@dataclass
class TradeResult:
    """交易結果"""
    entry_time: datetime
    exit_time: datetime
    signal: str  # 'LONG' or 'SHORT'
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_amount: float
    exit_reason: str  # 'TP', 'SL', 'TIME_STOP'
    confidence: float
    reasons: List[str]

class LargeTradeOBBacktester:
    """大單 + 訂單簿策略回測器"""
    
    def __init__(self, config: StrategyConfig):
        self.config = config
        
    def calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """計算技術指標"""
        df = df.copy()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # MA
        df['ma7'] = df['close'].rolling(7).mean()
        df['ma25'] = df['close'].rolling(25).mean()
        
        # Volume
        df['volume_ma20'] = df['volume'].rolling(20).mean()
        df['volume_surge'] = df['volume'] > df['volume_ma20'] * 1.5
        
        return df
    
    def simulate_large_trades(self, df: pd.DataFrame, current_idx: int) -> Dict:
        """
        模擬大單交易（使用成交量作為代理）
        
        注意：這是簡化版，實際應該使用 aggTrades 數據
        但由於我們只有歷史 K 線，用成交量突增來模擬大單
        """
        if current_idx < 20:
            return {
                'count': 0,
                'aggressive_net': 0,
                'signal': 'NEUTRAL'
            }
        
        # 查看最近 20 根 K 線（5小時，因為是15m）
        recent_window = df.iloc[max(0, current_idx-20):current_idx+1]
        
        # 使用成交量和價格變化模擬大單
        volume_avg = recent_window['volume'].mean()
        volume_std = recent_window['volume'].std()
        
        # 大成交量 K 線
        large_volume_bars = recent_window[
            recent_window['volume'] > volume_avg + 2 * volume_std
        ]
        
        if len(large_volume_bars) == 0:
            return {
                'count': 0,
                'aggressive_net': 0,
                'signal': 'NEUTRAL'
            }
        
        # 根據價格方向判斷買賣
        # 上漲 + 大量 = 買入，下跌 + 大量 = 賣出
        buy_volume = large_volume_bars[
            large_volume_bars['close'] > large_volume_bars['open']
        ]['volume'].sum()
        
        sell_volume = large_volume_bars[
            large_volume_bars['close'] < large_volume_bars['open']
        ]['volume'].sum()
        
        # 假設 1% 的成交量來自大單
        aggressive_buy = buy_volume * 0.01 / df.iloc[current_idx]['close']  # 轉換為 BTC
        aggressive_sell = sell_volume * 0.01 / df.iloc[current_idx]['close']
        
        aggressive_net = aggressive_buy - aggressive_sell
        
        # 判斷信號
        signal = 'NEUTRAL'
        if aggressive_net > self.config.aggressive_net_threshold:
            signal = 'BULLISH'
        elif aggressive_net < -self.config.aggressive_net_threshold:
            signal = 'BEARISH'
        
        return {
            'count': len(large_volume_bars),
            'aggressive_buy': aggressive_buy,
            'aggressive_sell': aggressive_sell,
            'aggressive_net': aggressive_net,
            'signal': signal
        }
    
    def simulate_orderbook(self, df: pd.DataFrame, current_idx: int) -> Dict:
        """
        模擬訂單簿不平衡（使用成交量和價格範圍作為代理）
        
        注意：這是簡化版，實際應該使用 depth 數據
        """
        if current_idx < 10:
            return {
                'imbalance': 0,
                'signal': 'NEUTRAL'
            }
        
        # 查看最近 10 根 K 線
        recent_window = df.iloc[max(0, current_idx-10):current_idx+1]
        
        # 使用 high-low 範圍和成交量估計買賣壓力
        # 價格偏向上限 + 大量 = 買盤強
        # 價格偏向下限 + 大量 = 賣盤強
        
        total_imbalance = 0
        for _, row in recent_window.iterrows():
            price_range = row['high'] - row['low']
            if price_range == 0:
                continue
            
            # 收盤價位置（0=low, 1=high）
            close_position = (row['close'] - row['low']) / price_range
            
            # 0.5 = 中性，>0.5 = 偏買盤，<0.5 = 偏賣盤
            bar_imbalance = (close_position - 0.5) * 2  # 轉換為 [-1, 1]
            
            # 加權成交量
            weight = row['volume'] / recent_window['volume'].sum()
            total_imbalance += bar_imbalance * weight
        
        # 判斷信號
        signal = 'NEUTRAL'
        if total_imbalance > self.config.orderbook_imbalance_threshold:
            signal = 'BULLISH'
        elif total_imbalance < -self.config.orderbook_imbalance_threshold:
            signal = 'BEARISH'
        
        return {
            'imbalance': total_imbalance,
            'signal': signal
        }
    
    def generate_signal(
        self, 
        df: pd.DataFrame, 
        current_idx: int
    ) -> Optional[Dict]:
        """
        生成交易信號
        """
        if current_idx < 50:  # 需要足夠的歷史數據
            return None
        
        current_row = df.iloc[current_idx]
        
        # 1. 大單分析
        large_trade_analysis = self.simulate_large_trades(df, current_idx)
        
        # 2. 訂單簿分析
        orderbook_analysis = self.simulate_orderbook(df, current_idx)
        
        # 3. 技術指標
        rsi = current_row['rsi']
        ma_trend = 'NEUTRAL'
        if current_row['close'] > current_row['ma7'] > current_row['ma25']:
            ma_trend = 'BULLISH'
        elif current_row['close'] < current_row['ma7'] < current_row['ma25']:
            ma_trend = 'BEARISH'
        
        technical_signal = 'NEUTRAL'
        if rsi < self.config.rsi_oversold and ma_trend == 'BULLISH':
            technical_signal = 'BULLISH'
        elif rsi > self.config.rsi_overbought and ma_trend == 'BEARISH':
            technical_signal = 'BEARISH'
        
        # 計算綜合信號
        signals = []
        reasons = []
        
        if large_trade_analysis['signal'] == 'BULLISH':
            signals.append(('LONG', self.config.large_trade_weight))
            reasons.append(f"大單買入 {large_trade_analysis['aggressive_net']:.1f} BTC")
        elif large_trade_analysis['signal'] == 'BEARISH':
            signals.append(('SHORT', self.config.large_trade_weight))
            reasons.append(f"大單賣出 {large_trade_analysis['aggressive_net']:.1f} BTC")
        
        if orderbook_analysis['signal'] == 'BULLISH':
            signals.append(('LONG', self.config.orderbook_weight))
            reasons.append(f"買盤強勁 {orderbook_analysis['imbalance']:+.2f}")
        elif orderbook_analysis['signal'] == 'BEARISH':
            signals.append(('SHORT', self.config.orderbook_weight))
            reasons.append(f"賣盤強勁 {orderbook_analysis['imbalance']:+.2f}")
        
        if technical_signal == 'BULLISH':
            signals.append(('LONG', self.config.technical_weight))
            reasons.append(f"技術看漲 RSI={rsi:.1f}")
        elif technical_signal == 'BEARISH':
            signals.append(('SHORT', self.config.technical_weight))
            reasons.append(f"技術看跌 RSI={rsi:.1f}")
        
        if not signals:
            return None
        
        # 統計得分
        long_score = sum([w for s, w in signals if s == 'LONG'])
        short_score = sum([w for s, w in signals if s == 'SHORT'])
        
        if long_score > short_score and long_score >= self.config.min_confidence:
            return {
                'signal': 'LONG',
                'confidence': long_score,
                'reasons': reasons,
                'entry_price': current_row['close']
            }
        elif short_score > long_score and short_score >= self.config.min_confidence:
            return {
                'signal': 'SHORT',
                'confidence': short_score,
                'reasons': reasons,
                'entry_price': current_row['close']
            }
        
        return None
    
    def run_backtest(
        self, 
        df: pd.DataFrame,
        start_time: datetime,
        end_time: datetime
    ) -> List[TradeResult]:
        """
        運行回測
        """
        # 過濾時間範圍
        df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)].copy()
        
        if len(df) < 100:
            print(f"⚠️ 數據不足: {len(df)} 根 K 線")
            return []
        
        # 計算技術指標
        df = self.calculate_technical_indicators(df)
        df = df.reset_index(drop=True)
        
        trades = []
        position = None  # 當前持倉
        
        for i in range(50, len(df)):
            current_time = df.iloc[i]['timestamp']
            current_price = df.iloc[i]['close']
            
            # 檢查是否需要平倉
            if position is not None:
                exit_reason = None
                exit_price = current_price
                
                # 計算 PnL
                if position['signal'] == 'LONG':
                    pnl_pct = (current_price - position['entry_price']) / position['entry_price']
                else:  # SHORT
                    pnl_pct = (position['entry_price'] - current_price) / position['entry_price']
                
                # 檢查止盈
                if pnl_pct >= self.config.tp_pct:
                    exit_reason = 'TP'
                # 檢查止損
                elif pnl_pct <= -self.config.sl_pct:
                    exit_reason = 'SL'
                # 檢查時間止損
                elif (current_time - position['entry_time']).total_seconds() / 60 >= self.config.time_stop_minutes:
                    exit_reason = 'TIME_STOP'
                
                if exit_reason:
                    # 平倉
                    pnl_amount = pnl_pct * position['capital'] * self.config.leverage
                    
                    trades.append(TradeResult(
                        entry_time=position['entry_time'],
                        exit_time=current_time,
                        signal=position['signal'],
                        entry_price=position['entry_price'],
                        exit_price=exit_price,
                        pnl_pct=pnl_pct,
                        pnl_amount=pnl_amount,
                        exit_reason=exit_reason,
                        confidence=position['confidence'],
                        reasons=position['reasons']
                    ))
                    
                    position = None
            
            # 如果無持倉，尋找開倉信號
            if position is None:
                signal_data = self.generate_signal(df, i)
                
                if signal_data:
                    position = {
                        'signal': signal_data['signal'],
                        'entry_time': current_time,
                        'entry_price': signal_data['entry_price'],
                        'confidence': signal_data['confidence'],
                        'reasons': signal_data['reasons'],
                        'capital': 100  # 假設每次投入 100U
                    }
        
        return trades

class WalkForwardOptimizer:
    """Walk-Forward 優化器"""
    
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.df['timestamp'] = pd.to_datetime(self.df['timestamp'])
    
    def optimize_on_year(self, year: int) -> StrategyConfig:
        """
        在指定年份上優化參數
        """
        print(f"\n🔧 優化 {year} 年參數...")
        
        # 參數網格
        param_grid = {
            'large_trade_threshold': [5.0, 10.0, 20.0],
            'aggressive_net_threshold': [20.0, 30.0, 50.0],
            'orderbook_imbalance_threshold': [0.2, 0.3, 0.4],
            'min_confidence': [0.4, 0.5, 0.6],
        }
        
        best_score = -float('inf')
        best_config = None
        
        # 遍歷參數組合
        keys = param_grid.keys()
        values = param_grid.values()
        
        for combo in itertools.product(*values):
            config = StrategyConfig()
            for key, value in zip(keys, combo):
                setattr(config, key, value)
            
            # 回測
            backtester = LargeTradeOBBacktester(config)
            start_time = datetime(year, 1, 1)
            end_time = datetime(year, 12, 31, 23, 59, 59)
            
            trades = backtester.run_backtest(self.df, start_time, end_time)
            
            if not trades:
                continue
            
            # 計算指標
            win_trades = [t for t in trades if t.pnl_pct > 0]
            win_rate = len(win_trades) / len(trades)
            total_return = sum([t.pnl_amount for t in trades])
            
            days = (end_time - start_time).days
            trades_per_day = len(trades) / days
            
            # 評分函數
            score = (
                win_rate * 100 +
                min(trades_per_day, 20) * 5 +
                total_return * 0.5
            )
            
            if score > best_score:
                best_score = score
                best_config = config
        
        if best_config is None:
            print(f"⚠️ {year} 年無有效配置，使用默認值")
            best_config = StrategyConfig()
        else:
            print(f"✅ 最佳配置: score={best_score:.1f}")
            print(f"   大單閾值: {best_config.large_trade_threshold} BTC")
            print(f"   淨流入閾值: {best_config.aggressive_net_threshold} BTC")
            print(f"   不平衡閾值: {best_config.orderbook_imbalance_threshold}")
            print(f"   最小信心: {best_config.min_confidence}")
        
        return best_config
    
    def run_walk_forward(self) -> Dict:
        """
        運行 Walk-Forward Optimization
        """
        print("="*70)
        print("🚀 Walk-Forward Optimization - 方案A 策略")
        print("="*70)
        
        results = {
            'train_years': [],
            'test_years': [],
            'configs': [],
            'test_results': []
        }
        
        train_years = [2020, 2021, 2022, 2023, 2024]
        test_years = [2021, 2022, 2023, 2024, 2025]
        
        for train_year, test_year in zip(train_years, test_years):
            print(f"\n{'='*70}")
            print(f"訓練年份: {train_year} → 測試年份: {test_year}")
            print(f"{'='*70}")
            
            # 1. 在訓練年份優化參數
            best_config = self.optimize_on_year(train_year)
            
            # 2. 在測試年份測試
            print(f"\n📊 測試 {test_year} 年...")
            backtester = LargeTradeOBBacktester(best_config)
            start_time = datetime(test_year, 1, 1)
            end_time = datetime(test_year, 12, 31, 23, 59, 59)
            
            trades = backtester.run_backtest(self.df, start_time, end_time)
            
            # 統計結果
            if trades:
                win_trades = [t for t in trades if t.pnl_pct > 0]
                win_rate = len(win_trades) / len(trades)
                total_return = sum([t.pnl_amount for t in trades])
                
                days = (end_time - start_time).days
                trades_per_day = len(trades) / days
                
                avg_pnl = np.mean([t.pnl_pct for t in trades])
                
                print(f"\n✅ {test_year} 年結果:")
                print(f"   交易數: {len(trades)} 筆")
                print(f"   勝率: {win_rate:.1%}")
                print(f"   總回報: {total_return:+.1f} U")
                print(f"   平均 PnL: {avg_pnl:+.2%}")
                print(f"   交易頻率: {trades_per_day:.1f} 筆/天")
            else:
                print(f"⚠️ {test_year} 年無交易")
                win_rate = 0
                total_return = 0
                trades_per_day = 0
            
            # 保存結果
            results['train_years'].append(train_year)
            results['test_years'].append(test_year)
            results['configs'].append(asdict(best_config))
            results['test_results'].append({
                'year': test_year,
                'total_trades': len(trades),
                'win_rate': win_rate,
                'total_return': total_return,
                'trades_per_day': trades_per_day,
                'trades': [asdict(t) for t in trades]
            })
        
        return results
    
    def save_results(self, results: Dict, filepath: str):
        """保存結果"""
        # 轉換 datetime 為字符串
        def convert_datetime(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            elif isinstance(obj, dict):
                return {k: convert_datetime(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_datetime(v) for v in obj]
            return obj
        
        results_serializable = convert_datetime(results)
        
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(results_serializable, f, indent=2, ensure_ascii=False)
        
        print(f"\n✅ 結果已保存: {filepath}")

def main():
    """主函數"""
    # 載入數據
    print("📂 載入歷史數據...")
    df = pd.read_parquet('data/historical/BTCUSDT_15m.parquet')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    print(f"✅ 載入 {len(df)} 根 K 線")
    print(f"   時間範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    
    # 運行 Walk-Forward
    optimizer = WalkForwardOptimizer(df)
    results = optimizer.run_walk_forward()
    
    # 保存結果
    optimizer.save_results(
        results, 
        'backtest_results/walk_forward/large_trade_ob_walk_forward.json'
    )
    
    # 打印總結
    print("\n" + "="*70)
    print("📊 Walk-Forward 總結")
    print("="*70)
    
    for i, test_result in enumerate(results['test_results']):
        year = test_result['year']
        trades = test_result['total_trades']
        win_rate = test_result['win_rate']
        total_return = test_result['total_return']
        trades_per_day = test_result['trades_per_day']
        
        print(f"\n{year} 年:")
        print(f"  交易數: {trades} 筆")
        print(f"  勝率: {win_rate:.1%}")
        print(f"  回報: {total_return:+.1f} U")
        print(f"  頻率: {trades_per_day:.1f} 筆/天")

if __name__ == '__main__':
    main()
