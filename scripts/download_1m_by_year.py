"""
分年下載 1m K線資料
更安全、可恢復、不易中斷
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import time
import pandas as pd
from tqdm import tqdm
import json

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.exchange.binance_client import BinanceClient


def download_year_data(client: BinanceClient, year: int, symbol: str = "BTCUSDT") -> pd.DataFrame:
    """
    下載指定年份的 1m K線資料
    
    Args:
        client: BinanceClient 實例
        year: 年份
        symbol: 交易對
    
    Returns:
        該年份的 K線 DataFrame
    """
    # 時間範圍
    start_time = datetime(year, 1, 1)
    end_time = datetime(year + 1, 1, 1)
    
    # 如果是當前年份，結束時間設為現在
    if year == datetime.now().year:
        end_time = datetime.now()
    
    print(f"\n📥 下載 {year} 年資料")
    print(f"   時間範圍: {start_time.date()} ~ {end_time.date()}")
    
    all_klines = []
    current_start = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)
    
    # 預估請求次數（1年約 525,600 分鐘 = 526 個請求）
    estimated_requests = 530
    pbar = tqdm(total=estimated_requests, desc=f"  {year}", unit="req")
    
    request_count = 0
    
    while current_start < end_ts:
        try:
            klines = client.get_klines(
                symbol=symbol,
                interval='1m',
                start_time=current_start,
                limit=1000
            )
            
            if not klines:
                break
            
            all_klines.extend(klines)
            current_start = int(klines[-1][6]) + 1  # 最後一根的收盤時間 + 1ms
            
            request_count += 1
            pbar.update(1)
            
            # 避免速率限制
            time.sleep(0.1)
            
        except Exception as e:
            print(f"\n   ⚠️  錯誤: {e}")
            print(f"   ⏸️  等待 5 秒後重試...")
            time.sleep(5)
            continue
    
    pbar.close()
    
    # 轉換為 DataFrame
    if not all_klines:
        print(f"   ❌ 沒有資料")
        return None
    
    df = pd.DataFrame(all_klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])
    
    # 轉換資料類型
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
    
    for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'taker_buy_base', 'taker_buy_quote']:
        df[col] = df[col].astype(float)
    
    df['trades'] = df['trades'].astype(int)
    df = df.drop(columns=['ignore'])
    
    # 去重排序
    df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
    
    print(f"   ✅ 完成！{len(df):,} 根K線")
    print(f"   實際範圍: {df.iloc[0]['timestamp']} ~ {df.iloc[-1]['timestamp']}")
    
    return df


def main():
    """主函數"""
    print("\n🚀 分年下載 BTC/USDT 1m K線資料\n")
    print("=" * 80)
    
    # 初始化客戶端
    client = BinanceClient()
    from binance.client import Client
    client.client = Client(api_key="", api_secret="")  # Mainnet 公開資料
    
    data_dir = Path("data/historical")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    symbol = "BTCUSDT"
    
    # 下載年份範圍（2020-2025）
    years = [2020, 2021, 2022, 2023, 2024, 2025]
    
    print(f"📅 將下載 {len(years)} 年的資料: {years}\n")
    
    all_dfs = []
    stats = {
        'symbol': symbol,
        'interval': '1m',
        'years': {},
        'download_time': datetime.now().isoformat(),
    }
    
    # 逐年下載
    for year in years:
        year_file = data_dir / f"{symbol}_1m_{year}.parquet"
        
        # 檢查是否已下載
        if year_file.exists():
            print(f"\n✅ {year} 年資料已存在，載入中...")
            df_year = pd.read_parquet(year_file)
            print(f"   已載入 {len(df_year):,} 根K線")
            all_dfs.append(df_year)
            
            stats['years'][year] = {
                'rows': len(df_year),
                'start': df_year.iloc[0]['timestamp'].isoformat(),
                'end': df_year.iloc[-1]['timestamp'].isoformat(),
                'status': 'cached'
            }
            continue
        
        # 下載
        try:
            df_year = download_year_data(client, year, symbol)
            
            if df_year is not None and len(df_year) > 0:
                # 儲存年度檔案
                df_year.to_parquet(year_file, index=False)
                file_size_mb = year_file.stat().st_size / 1024 / 1024
                print(f"   💾 已儲存: {year_file.name} ({file_size_mb:.2f} MB)")
                
                all_dfs.append(df_year)
                
                stats['years'][year] = {
                    'rows': len(df_year),
                    'start': df_year.iloc[0]['timestamp'].isoformat(),
                    'end': df_year.iloc[-1]['timestamp'].isoformat(),
                    'size_mb': round(file_size_mb, 2),
                    'status': 'downloaded'
                }
            
        except Exception as e:
            print(f"   ❌ {year} 年下載失敗: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # 合併所有年份
    if not all_dfs:
        print("\n❌ 沒有任何資料")
        return
    
    print("\n" + "=" * 80)
    print("📦 合併所有年份資料...")
    
    df_final = pd.concat(all_dfs, ignore_index=True)
    df_final = df_final.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
    
    # 儲存最終檔案
    final_file = data_dir / f"{symbol}_1m.parquet"
    df_final.to_parquet(final_file, index=False)
    final_size_mb = final_file.stat().st_size / 1024 / 1024
    
    print(f"   ✅ 已合併 {len(df_final):,} 根K線")
    print(f"   💾 已儲存: {final_file.name} ({final_size_mb:.2f} MB)")
    print(f"   時間範圍: {df_final.iloc[0]['timestamp']} ~ {df_final.iloc[-1]['timestamp']}")
    
    # 統計資訊
    stats['total_rows'] = len(df_final)
    stats['total_size_mb'] = round(final_size_mb, 2)
    stats['time_range'] = {
        'start': df_final.iloc[0]['timestamp'].isoformat(),
        'end': df_final.iloc[-1]['timestamp'].isoformat(),
        'days': (df_final.iloc[-1]['timestamp'] - df_final.iloc[0]['timestamp']).days
    }
    
    # 儲存統計
    stats_file = data_dir / 'download_1m_stats.json'
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    
    # 顯示摘要
    print("\n" + "=" * 80)
    print("✅ 下載完成！")
    print("=" * 80)
    print()
    print("📊 各年份統計:")
    for year, info in stats['years'].items():
        status_icon = "✅" if info['status'] == 'downloaded' else "📦"
        print(f"   {status_icon} {year}: {info['rows']:>10,} rows")
    
    print()
    print(f"📈 總計: {stats['total_rows']:,} 根K線")
    print(f"💾 大小: {stats['total_size_mb']:.2f} MB")
    print(f"📅 範圍: {stats['time_range']['days']} 天")
    print()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  使用者中斷")
        print("💡 已下載的年份資料會保留，下次執行會自動跳過")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
