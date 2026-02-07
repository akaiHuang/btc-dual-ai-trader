#!/usr/bin/env python3
"""
階段1: 將真實大單數據與15m K線對齊
生成包含大單特徵的完整數據集，用於回測
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

class LargeTradeFeatureEngine:
    """大單特徵工程"""
    
    def __init__(self, kline_file: str, large_trade_file: str):
        """
        初始化
        
        Args:
            kline_file: 15m K線數據文件路徑
            large_trade_file: 大單數據文件路徑
        """
        print("="*70)
        print("🔧 階段1: 大單數據與K線對齊")
        print("="*70)
        print()
        
        # 載入數據
        print("📂 載入數據...")
        self.df_kline = pd.read_parquet(kline_file)
        self.df_kline['timestamp'] = pd.to_datetime(self.df_kline['timestamp'])
        
        self.df_trades = pd.read_parquet(large_trade_file)
        self.df_trades['timestamp'] = pd.to_datetime(self.df_trades['timestamp'])
        
        print(f"✅ K線數據: {len(self.df_kline):,} 根")
        print(f"✅ 大單數據: {len(self.df_trades):,} 筆")
        print()
        
    def calculate_large_trade_features(self, lookback_minutes: int = 15):
        """
        計算每根K線的大單特徵
        
        Args:
            lookback_minutes: 回看窗口（分鐘），默認15分鐘（對齊K線週期）
        
        Returns:
            DataFrame: 包含大單特徵的K線數據
        """
        print(f"🔍 計算大單特徵（回看窗口: {lookback_minutes} 分鐘）...")
        
        # 只處理有大單數據的時間範圍
        trade_start = self.df_trades['timestamp'].min()
        trade_end = self.df_trades['timestamp'].max()
        
        df_result = self.df_kline.copy()
        
        # 初始化特徵列
        df_result['large_trade_count'] = 0  # 大單總數
        df_result['large_buy_count'] = 0    # 大單買入數量
        df_result['large_sell_count'] = 0   # 大單賣出數量
        df_result['large_buy_volume'] = 0.0  # 大單買入量(BTC)
        df_result['large_sell_volume'] = 0.0  # 大單賣出量(BTC)
        df_result['large_net_volume'] = 0.0  # 大單淨流入(BTC)
        df_result['large_trade_imbalance'] = 0.0  # 大單不平衡度 [-1, 1]
        df_result['large_trade_strength'] = 0.0  # 大單強度（平均單量）
        df_result['whale_detected'] = False  # 是否檢測到巨鯨(>50 BTC)
        df_result['max_single_trade'] = 0.0  # 最大單筆交易量
        
        # 只處理有大單數據的K線
        mask = (df_result['timestamp'] >= trade_start) & (df_result['timestamp'] <= trade_end)
        df_target = df_result[mask].copy()
        
        print(f"   處理範圍: {trade_start} ~ {trade_end}")
        print(f"   待處理K線: {len(df_target)} 根")
        print()
        
        # 對每根K線計算特徵
        lookback_delta = timedelta(minutes=lookback_minutes)
        
        features_list = []
        
        for idx, row in df_target.iterrows():
            kline_time = row['timestamp']
            window_start = kline_time - lookback_delta
            
            # 獲取窗口內的大單
            window_trades = self.df_trades[
                (self.df_trades['timestamp'] >= window_start) &
                (self.df_trades['timestamp'] <= kline_time)
            ]
            
            if len(window_trades) == 0:
                # 無大單
                features = {
                    'timestamp': kline_time,
                    'large_trade_count': 0,
                    'large_buy_count': 0,
                    'large_sell_count': 0,
                    'large_buy_volume': 0.0,
                    'large_sell_volume': 0.0,
                    'large_net_volume': 0.0,
                    'large_trade_imbalance': 0.0,
                    'large_trade_strength': 0.0,
                    'whale_detected': False,
                    'max_single_trade': 0.0
                }
            else:
                # 計算特徵
                buy_trades = window_trades[window_trades['side'] == 'buy']
                sell_trades = window_trades[window_trades['side'] == 'sell']
                
                buy_volume = buy_trades['amount'].sum() if len(buy_trades) > 0 else 0.0
                sell_volume = sell_trades['amount'].sum() if len(sell_trades) > 0 else 0.0
                total_volume = buy_volume + sell_volume
                
                # 不平衡度
                if total_volume > 0:
                    imbalance = (buy_volume - sell_volume) / total_volume
                else:
                    imbalance = 0.0
                
                # 巨鯨檢測（單筆>50 BTC）
                whale_detected = (window_trades['amount'] > 50).any()
                
                features = {
                    'timestamp': kline_time,
                    'large_trade_count': len(window_trades),
                    'large_buy_count': len(buy_trades),
                    'large_sell_count': len(sell_trades),
                    'large_buy_volume': buy_volume,
                    'large_sell_volume': sell_volume,
                    'large_net_volume': buy_volume - sell_volume,
                    'large_trade_imbalance': imbalance,
                    'large_trade_strength': window_trades['amount'].mean(),
                    'whale_detected': whale_detected,
                    'max_single_trade': window_trades['amount'].max()
                }
            
            features_list.append(features)
        
        # 轉換為DataFrame
        df_features = pd.DataFrame(features_list)
        
        # 合併回原始數據
        df_result = df_result.merge(
            df_features,
            on='timestamp',
            how='left',
            suffixes=('', '_new')
        )
        
        # 更新特徵列
        feature_cols = [
            'large_trade_count', 'large_buy_count', 'large_sell_count',
            'large_buy_volume', 'large_sell_volume', 'large_net_volume',
            'large_trade_imbalance', 'large_trade_strength',
            'whale_detected', 'max_single_trade'
        ]
        
        for col in feature_cols:
            if f'{col}_new' in df_result.columns:
                df_result[col] = df_result[f'{col}_new'].fillna(df_result[col])
                df_result.drop(columns=[f'{col}_new'], inplace=True)
        
        print("✅ 特徵計算完成！")
        print()
        
        return df_result
    
    def add_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        添加技術指標（為後續策略準備）
        
        Args:
            df: K線數據
            
        Returns:
            DataFrame: 包含技術指標的數據
        """
        print("📊 計算技術指標...")
        
        df = df.copy()
        
        # RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / avg_loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # 移動平均線
        df['ma7'] = df['close'].rolling(window=7).mean()
        df['ma25'] = df['close'].rolling(window=25).mean()
        
        # 成交量均線
        df['volume_ma7'] = df['volume'].rolling(window=7).mean()
        
        # 成交量突增標記
        df['volume_spike'] = df['volume'] > (df['volume_ma7'] * 1.5)
        
        print("✅ 技術指標完成！")
        print()
        
        return df
    
    def print_feature_summary(self, df: pd.DataFrame):
        """打印特徵統計摘要"""
        print("="*70)
        print("📊 大單特徵統計摘要")
        print("="*70)
        print()
        
        # 只看有大單的K線
        df_with_trades = df[df['large_trade_count'] > 0]
        
        if len(df_with_trades) == 0:
            print("⚠️ 無大單數據")
            return
        
        print(f"時間範圍: {df_with_trades['timestamp'].min()} ~ {df_with_trades['timestamp'].max()}")
        print(f"總K線數: {len(df):,} 根")
        print(f"有大單K線: {len(df_with_trades):,} 根 ({len(df_with_trades)/len(df)*100:.1f}%)")
        print()
        
        print("大單統計:")
        print(f"  總大單數: {df_with_trades['large_trade_count'].sum():.0f} 筆")
        print(f"  平均每根K線: {df_with_trades['large_trade_count'].mean():.1f} 筆")
        print(f"  最多單根K線: {df_with_trades['large_trade_count'].max():.0f} 筆")
        print()
        
        print("買賣分佈:")
        total_buy = df_with_trades['large_buy_count'].sum()
        total_sell = df_with_trades['large_sell_count'].sum()
        total = total_buy + total_sell
        print(f"  買入: {total_buy:.0f} 筆 ({total_buy/total*100:.1f}%)")
        print(f"  賣出: {total_sell:.0f} 筆 ({total_sell/total*100:.1f}%)")
        print()
        
        print("成交量統計:")
        print(f"  總買入量: {df_with_trades['large_buy_volume'].sum():.0f} BTC")
        print(f"  總賣出量: {df_with_trades['large_sell_volume'].sum():.0f} BTC")
        print(f"  淨流入: {df_with_trades['large_net_volume'].sum():.0f} BTC")
        print()
        
        print("不平衡度分佈:")
        print(f"  平均: {df_with_trades['large_trade_imbalance'].mean():.3f}")
        print(f"  中位數: {df_with_trades['large_trade_imbalance'].median():.3f}")
        print(f"  標準差: {df_with_trades['large_trade_imbalance'].std():.3f}")
        print(f"  極端買入(>0.5): {(df_with_trades['large_trade_imbalance'] > 0.5).sum()} 根")
        print(f"  極端賣出(<-0.5): {(df_with_trades['large_trade_imbalance'] < -0.5).sum()} 根")
        print()
        
        print("巨鯨活動:")
        whale_klines = df_with_trades[df_with_trades['whale_detected']]
        print(f"  檢測到巨鯨K線: {len(whale_klines)} 根")
        if len(whale_klines) > 0:
            print(f"  最大單筆: {whale_klines['max_single_trade'].max():.2f} BTC")
        print()
        
    def save_merged_data(self, df: pd.DataFrame, output_file: str):
        """保存合併後的數據"""
        print(f"💾 保存數據到: {output_file}")
        
        # 確保目錄存在
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        
        # 保存為 parquet
        df.to_parquet(output_file, index=False)
        
        file_size = Path(output_file).stat().st_size / 1024 / 1024
        print(f"✅ 保存完成！文件大小: {file_size:.2f} MB")
        print()


def main():
    """主函數"""
    # 文件路徑
    kline_file = 'data/historical/BTCUSDT_15m.parquet'
    large_trade_file = 'data/historical/BTCUSDT_agg_trades_large.parquet'
    output_file = 'data/historical/BTCUSDT_15m_with_large_trades.parquet'
    
    # 初始化
    engine = LargeTradeFeatureEngine(kline_file, large_trade_file)
    
    # 計算大單特徵
    df_merged = engine.calculate_large_trade_features(lookback_minutes=15)
    
    # 添加技術指標
    df_merged = engine.add_technical_indicators(df_merged)
    
    # 打印統計摘要
    engine.print_feature_summary(df_merged)
    
    # 保存數據
    engine.save_merged_data(df_merged, output_file)
    
    print("="*70)
    print("✅ 階段1完成！數據已準備好用於回測")
    print("="*70)
    print()
    print("下一步:")
    print("  1. 運行 Walk-Forward 回測")
    print("  2. 驗證策略在真實大單數據上的表現")
    print()


if __name__ == '__main__':
    main()
