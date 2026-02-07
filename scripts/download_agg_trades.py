"""
下載幣安歷史 aggTrades（大單交易數據）

用途：
- 獲取真實的大額交易記錄
- 區分主動買入/賣出（taker/maker）
- 用於「方案A：幣安大單 + Order Book」策略回測

API 限制：
- aggTrades 只能獲取最近 1000 筆
- 需要分批下載歷史數據
- 每次請求限制 1200/分鐘
"""

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
from pathlib import Path
from tqdm import tqdm

class BinanceAggTradesDownloader:
    """幣安聚合交易下載器"""
    
    def __init__(self):
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',  # 期貨市場
            }
        })
    
    def download_agg_trades_range(
        self,
        symbol: str = 'BTC/USDT',
        start_time: datetime = None,
        end_time: datetime = None,
        min_amount: float = 5.0  # 最小追蹤金額 5 BTC
    ) -> pd.DataFrame:
        """
        下載指定時間範圍的 aggTrades
        
        注意：Binance API 限制，只能獲取最近的交易數據
        建議下載最近 7-30 天的數據進行測試
        """
        print(f"📡 下載 {symbol} 的 aggTrades 數據...")
        print(f"   時間範圍: {start_time.strftime('%Y-%m-%d')} ~ {end_time.strftime('%Y-%m-%d')}")
        print(f"   最小金額: {min_amount} BTC")
        print()
        
        all_trades = []
        batch_count = 0
        last_trade_id = None
        last_trade_time = start_time
        
        # 計算預估總批次數（用於進度條）
        total_seconds = (end_time - start_time).total_seconds()
        estimated_trades = int(total_seconds / 60 * 10)  # 估計每分鐘10筆大單
        estimated_batches = max(estimated_trades // 100, 10)  # 每批最多100筆大單
        
        # 使用進度條
        pbar = tqdm(total=estimated_batches, desc="下載進度", unit="批次")
        
        while True:
            try:
                # 使用 fromId 而不是 since（更可靠）
                params = {'limit': 1000}
                if last_trade_id:
                    params['fromId'] = int(last_trade_id) + 1
                else:
                    # 第一次請求使用 startTime
                    params['startTime'] = int(start_time.timestamp() * 1000)
                    params['endTime'] = int(end_time.timestamp() * 1000)
                
                trades = self.exchange.fetch_trades(
                    symbol,
                    params=params
                )
                
                if not trades:
                    pbar.close()
                    print("\n✅ 已到達數據結尾")
                    break
                
                batch_count += 1
                
                # 過濾大單
                new_large_trades = 0
                for trade in trades:
                    trade_time = datetime.fromtimestamp(trade['timestamp'] / 1000)
                    
                    # 檢查時間範圍
                    if trade_time > end_time:
                        pbar.close()
                        print(f"\n✅ 已到達結束時間")
                        break
                    
                    if trade_time < start_time:
                        continue
                    
                    amount = float(trade['amount'])
                    if amount >= min_amount:
                        all_trades.append({
                            'trade_id': trade['id'],
                            'timestamp': trade_time,
                            'price': float(trade['price']),
                            'amount': amount,
                            'side': trade['side'],
                            'is_taker': (trade.get('takerOrMaker') == 'taker')
                        })
                        new_large_trades += 1
                    
                    last_trade_id = trade['id']
                    last_trade_time = trade_time
                
                # 計算進度百分比
                time_progress = (last_trade_time - start_time).total_seconds() / total_seconds
                progress_pct = min(time_progress * 100, 100)
                
                # 更新進度條
                pbar.n = min(int(time_progress * estimated_batches), estimated_batches)
                pbar.set_postfix({
                    '大單數': len(all_trades),
                    '進度': f'{progress_pct:.1f}%',
                    '當前': last_trade_time.strftime('%m-%d %H:%M')
                })
                pbar.refresh()
                
                # 檢查是否超過時間範圍
                if trades and datetime.fromtimestamp(trades[-1]['timestamp'] / 1000) > end_time:
                    pbar.close()
                    print(f"\n✅ 已完成下載")
                    break
                
                # 避免 API 限制
                time.sleep(0.05)
                
            except KeyboardInterrupt:
                pbar.close()
                print(f"\n⚠️ 用戶中斷下載")
                print(f"   已下載: {len(all_trades)} 筆大單")
                break
            except Exception as e:
                print(f"\n⚠️ 批次 {batch_count} 失敗: {e}")
                print(f"   最後 trade_id: {last_trade_id}")
                print(f"   當前時間: {last_trade_time}")
                time.sleep(2)
                continue
        
        pbar.close()
        
        if not all_trades:
            print("⚠️ 未找到大單交易")
            return pd.DataFrame()
        
        df = pd.DataFrame(all_trades)
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        print(f"\n✅ 下載完成")
        print(f"   總計: {len(df)} 筆大單")
        print(f"   時間範圍: {df['timestamp'].min()} ~ {df['timestamp'].max()}")
        
        return df
    
    def analyze_large_trades(self, df: pd.DataFrame):
        """分析大單統計"""
        print("\n" + "="*70)
        print("📊 大單統計分析")
        print("="*70)
        
        total = len(df)
        buy_trades = df[df['side'] == 'buy']
        sell_trades = df[df['side'] == 'sell']
        
        taker_buy = df[(df['side'] == 'buy') & (df['is_taker'] == True)]
        taker_sell = df[(df['side'] == 'sell') & (df['is_taker'] == True)]
        
        print(f"\n總大單數: {total}")
        print(f"  買入: {len(buy_trades)} 筆 ({len(buy_trades)/total:.1%})")
        print(f"  賣出: {len(sell_trades)} 筆 ({len(sell_trades)/total:.1%})")
        print(f"\n主動交易:")
        print(f"  主動買入 (taker buy): {len(taker_buy)} 筆")
        print(f"  主動賣出 (taker sell): {len(taker_sell)} 筆")
        
        print(f"\n金額統計:")
        print(f"  平均: {df['amount'].mean():.2f} BTC")
        print(f"  中位數: {df['amount'].median():.2f} BTC")
        print(f"  最大: {df['amount'].max():.2f} BTC")
        
        # 按年份統計
        df['year'] = df['timestamp'].dt.year
        year_counts = df['year'].value_counts().sort_index()
        print(f"\n年度分佈:")
        for year, count in year_counts.items():
            year_df = df[df['year'] == year]
            avg_per_day = count / 365
            print(f"  {year}: {count} 筆 ({avg_per_day:.1f} 筆/天)")
    
    def save_data(self, df: pd.DataFrame, filepath: str):
        """保存數據"""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(filepath, index=False)
        print(f"\n✅ 數據已保存: {filepath}")

def main():
    """主函數"""
    downloader = BinanceAggTradesDownloader()
    
    # 下載最近 7 天的數據（快速測試）
    end_time = datetime.now()
    start_time = end_time - timedelta(days=7)
    
    print("="*70)
    print("🚀 下載幣安大單交易數據（方案A 測試）")
    print("="*70)
    print()
    print("⚠️ 注意：")
    print("   - 下載最近 7 天數據進行快速測試")
    print("   - Binance API 有歷史限制，無法獲取很久以前的數據")
    print("   - 如需完整歷史，建議購買付費數據服務")
    print()
    print("預計時間: 2-5 分鐘")
    print()
    
    # 下載數據
    df = downloader.download_agg_trades_range(
        symbol='BTC/USDT',
        start_time=start_time,
        end_time=end_time,
        min_amount=5.0  # 5 BTC 以上
    )
    
    if not df.empty:
        # 分析
        downloader.analyze_large_trades(df)
        
        # 保存
        downloader.save_data(
            df,
            'data/historical/BTCUSDT_agg_trades_large.parquet'
        )
    
    print("\n" + "="*70)
    print("💡 後續步驟:")
    print("="*70)
    print("1. 將 aggTrades 數據與 15m K 線對齊")
    print("2. 重新運行 Walk-Forward 回測")
    print("3. 驗證策略在真實大單數據上的表現")

if __name__ == '__main__':
    main()
