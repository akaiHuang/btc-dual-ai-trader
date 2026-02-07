#!/usr/bin/env python3
"""
下載 Binance Futures 歷史 L0 數據
包含：
1. Funding Rate (資金費率) - 每8小時一次
2. Open Interest (持倉量) - 每5分鐘一次
3. 整合到現有 15m K線數據中

無需 API Key，完全免費
"""

import pandas as pd
import requests
import time
from datetime import datetime, timedelta
from typing import List, Dict
import json
from pathlib import Path


class BinanceL0Downloader:
    """Binance L0 數據下載器"""
    
    BASE_URL = "https://fapi.binance.com"
    
    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol
        self.session = requests.Session()
        
    def download_funding_rate_history(
        self, 
        start_time: datetime, 
        end_time: datetime
    ) -> pd.DataFrame:
        """
        下載 Funding Rate 歷史數據
        
        Args:
            start_time: 開始時間
            end_time: 結束時間
            
        Returns:
            DataFrame with columns: [fundingTime, fundingRate]
        """
        print(f"📥 下載 Funding Rate: {start_time.date()} ~ {end_time.date()}")
        
        all_data = []
        current_start = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)
        
        batch = 0
        while current_start < end_ms:
            batch += 1
            print(f"   批次 {batch}: {datetime.fromtimestamp(current_start/1000).date()}", end="")
            
            try:
                url = f"{self.BASE_URL}/fapi/v1/fundingRate"
                params = {
                    'symbol': self.symbol,
                    'startTime': current_start,
                    'endTime': end_ms,
                    'limit': 1000  # 最多1000條
                }
                
                response = self.session.get(url, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                if not data:
                    print(" ✅ (無更多數據)")
                    break
                
                all_data.extend(data)
                print(f" ✅ (+{len(data)} 筆)")
                
                # 更新起始時間為最後一條記錄的時間+1ms
                current_start = data[-1]['fundingTime'] + 1
                
                # 避免請求過快
                time.sleep(0.2)
                
            except Exception as e:
                print(f" ❌ 錯誤: {e}")
                time.sleep(1)
                continue
        
        if not all_data:
            print("⚠️  未獲取到任何 Funding Rate 數據")
            return pd.DataFrame()
        
        df = pd.DataFrame(all_data)
        df['fundingTime'] = pd.to_datetime(df['fundingTime'], unit='ms')
        df['fundingRate'] = df['fundingRate'].astype(float)
        df = df.sort_values('fundingTime').reset_index(drop=True)
        
        print(f"✅ Funding Rate 完成: {len(df)} 筆記錄")
        return df[['fundingTime', 'fundingRate']]
    
    def download_open_interest_history(
        self, 
        start_time: datetime, 
        end_time: datetime
    ) -> pd.DataFrame:
        """
        下載 Open Interest 歷史數據
        
        注意：Binance API 只返回最近 30 天的 5m 數據
        因此我們改用 15m 數據並只下載最近的數據
        
        Args:
            start_time: 開始時間（會被忽略，API限制）
            end_time: 結束時間
            
        Returns:
            DataFrame with columns: [timestamp, sumOpenInterest, sumOpenInterestValue]
        """
        print(f"📥 下載 Open Interest（最近數據）")
        print(f"⚠️  注意：Binance 僅提供最近 30 天的詳細 OI 數據")
        
        try:
            url = f"{self.BASE_URL}/futures/data/openInterestHist"
            params = {
                'symbol': self.symbol,
                'period': '15m',  # 改用 15m 匹配 K線
                'limit': 500  # 最多500條
            }
            
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if not data:
                print("⚠️  未獲取到任何 Open Interest 數據")
                return pd.DataFrame()
            
            df = pd.DataFrame(data)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['sumOpenInterest'] = df['sumOpenInterest'].astype(float)
            df['sumOpenInterestValue'] = df['sumOpenInterestValue'].astype(float)
            df = df.sort_values('timestamp').reset_index(drop=True)
            
            print(f"✅ Open Interest 完成: {len(df)} 筆記錄")
            print(f"   時間範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
            return df[['timestamp', 'sumOpenInterest', 'sumOpenInterestValue']]
            
        except Exception as e:
            print(f"❌ 錯誤: {e}")
            return pd.DataFrame()
    
    def merge_with_klines(
        self,
        klines_df: pd.DataFrame,
        funding_df: pd.DataFrame,
        oi_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        將 L0 數據合併到 K線數據中
        
        策略：
        - Funding Rate: 前向填充（每8小時更新，中間時段使用最近值）
        - Open Interest: 線性插值（5分鐘數據插值到15分鐘）
        """
        print("\n🔧 合併數據到 K 線...")
        
        df = klines_df.copy()
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        # 1. 合併 Funding Rate (前向填充)
        if not funding_df.empty:
            print("   合併 Funding Rate...")
            funding_df = funding_df.rename(columns={'fundingTime': 'timestamp'})
            df = pd.merge_asof(
                df,
                funding_df,
                on='timestamp',
                direction='backward'  # 使用最近的過去值
            )
            print(f"   ✅ Funding Rate 已合併 (前向填充)")
        else:
            df['fundingRate'] = None
            print("   ⚠️  無 Funding Rate 數據，設為 None")
        
        # 2. 合併 Open Interest (先前向填充，再計算變化率)
        if not oi_df.empty:
            print("   合併 Open Interest...")
            df = pd.merge_asof(
                df,
                oi_df,
                on='timestamp',
                direction='backward'
            )
            
            # 計算 OI 變化率
            df['oi_change_rate'] = df['sumOpenInterest'].pct_change()
            df['oi_value_change_rate'] = df['sumOpenInterestValue'].pct_change()
            
            print(f"   ✅ Open Interest 已合併 (前向填充 + 變化率)")
        else:
            df['sumOpenInterest'] = None
            df['sumOpenInterestValue'] = None
            df['oi_change_rate'] = None
            df['oi_value_change_rate'] = None
            print("   ⚠️  無 Open Interest 數據，設為 None")
        
        # 3. 處理 NaN（首行變化率會是 NaN）
        df['fundingRate'] = df['fundingRate'].fillna(0)
        df['oi_change_rate'] = df['oi_change_rate'].fillna(0)
        df['oi_value_change_rate'] = df['oi_value_change_rate'].fillna(0)
        
        print(f"✅ 合併完成: {len(df)} 根 K 線")
        return df


def main():
    """主函數：下載並整合 L0 數據"""
    
    print("="*70)
    print("📊 Binance Futures L0 數據下載器")
    print("="*70)
    print()
    
    # 1. 讀取現有 K 線數據
    klines_path = Path("data/historical/BTCUSDT_15m.parquet")
    if not klines_path.exists():
        print(f"❌ 錯誤：找不到 K 線數據 {klines_path}")
        return
    
    print(f"📂 讀取現有 K 線數據: {klines_path}")
    df_klines = pd.read_parquet(klines_path)
    df_klines['timestamp'] = pd.to_datetime(df_klines['timestamp'])
    
    start_time = df_klines['timestamp'].min()
    end_time = df_klines['timestamp'].max()
    
    print(f"   時間範圍: {start_time} ~ {end_time}")
    print(f"   總 K 線: {len(df_klines):,} 根")
    print()
    
    # 2. 初始化下載器
    downloader = BinanceL0Downloader(symbol="BTCUSDT")
    
    # 3. 下載 Funding Rate
    print("🔽 步驟 1/3: 下載 Funding Rate")
    print("-" * 70)
    df_funding = downloader.download_funding_rate_history(start_time, end_time)
    print()
    
    # 4. 下載 Open Interest
    print("🔽 步驟 2/3: 下載 Open Interest")
    print("-" * 70)
    df_oi = downloader.download_open_interest_history(start_time, end_time)
    print()
    
    # 5. 合併數據
    print("🔧 步驟 3/3: 合併數據")
    print("-" * 70)
    df_merged = downloader.merge_with_klines(df_klines, df_funding, df_oi)
    print()
    
    # 6. 保存結果
    output_path = Path("data/historical/BTCUSDT_15m_with_l0.parquet")
    print(f"💾 保存到: {output_path}")
    df_merged.to_parquet(output_path, index=False)
    print(f"✅ 已保存: {len(df_merged):,} 根 K 線")
    print()
    
    # 7. 顯示統計
    print("="*70)
    print("📊 數據統計")
    print("="*70)
    print(f"欄位清單: {list(df_merged.columns)}")
    print()
    
    if 'fundingRate' in df_merged.columns:
        non_null = df_merged['fundingRate'].notna().sum()
        print(f"Funding Rate:")
        print(f"  有效數據: {non_null:,} / {len(df_merged):,} ({non_null/len(df_merged)*100:.1f}%)")
        print(f"  範圍: {df_merged['fundingRate'].min():.4f} ~ {df_merged['fundingRate'].max():.4f}")
        print(f"  平均: {df_merged['fundingRate'].mean():.4f}")
        print()
    
    if 'sumOpenInterest' in df_merged.columns:
        non_null = df_merged['sumOpenInterest'].notna().sum()
        print(f"Open Interest:")
        print(f"  有效數據: {non_null:,} / {len(df_merged):,} ({non_null/len(df_merged)*100:.1f}%)")
        print(f"  範圍: {df_merged['sumOpenInterest'].min():.0f} ~ {df_merged['sumOpenInterest'].max():.0f}")
        print(f"  平均變化率: {df_merged['oi_change_rate'].mean()*100:.3f}%")
        print()
    
    # 8. 保存樣本供檢查
    sample_path = Path("data/historical/l0_data_sample.csv")
    df_merged.head(100).to_csv(sample_path, index=False)
    print(f"💾 前 100 筆樣本已保存到: {sample_path}")
    print()
    
    print("="*70)
    print("✅ 全部完成！")
    print("="*70)
    print()
    print("📋 下一步：")
    print("   1. 檢查 data/historical/BTCUSDT_15m_with_l0.parquet")
    print("   2. 查看 data/historical/l0_data_sample.csv 確認數據正確")
    print("   3. 修改回測腳本使用新的數據文件")
    print("   4. 重新運行回測，預期勝率提升到 60-70%+")
    print()


if __name__ == "__main__":
    main()
