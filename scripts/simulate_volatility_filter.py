import json
import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import sys
import os

def get_binance_klines(symbol, interval, start_time, end_time):
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": int(start_time.timestamp() * 1000),
        "endTime": int(end_time.timestamp() * 1000),
        "limit": 5
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching klines: {e}")
        return []

def analyze_filters(trade_file):
    print(f"Analyzing Filters for: {trade_file}")
    
    if not os.path.exists(trade_file):
        print(f"File not found: {trade_file}")
        return

    try:
        with open(trade_file, 'r') as f:
            data = json.load(f)
            trades = data.get('trades', [])
    except Exception as e:
        print(f"Failed to load trade file: {e}")
        return

    if not trades:
        print("No trades found.")
        return

    print(f"Total Trades to Analyze: {len(trades)}")
    print("-" * 100)
    print(f"{'Time':<20} | {'Dir':<5} | {'PnL':<8} | {'Vol(%)':<8} | {'Chg(%)':<8} | {'Action'}")
    print("-" * 100)

    original_pnl = 0
    filtered_pnl = 0
    original_wins = 0
    filtered_wins = 0
    original_count = 0
    filtered_count = 0
    
    filtered_good_trades = 0
    filtered_bad_trades = 0

    for trade in trades:
        entry_time_str = trade.get('entry_time')
        if not entry_time_str:
            continue
            
        try:
            entry_dt = datetime.fromisoformat(entry_time_str)
        except ValueError:
            entry_dt = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")

        start_time = entry_dt - timedelta(minutes=10)
        end_time = entry_dt
        
        klines = get_binance_klines("BTCUSDT", "5m", start_time, end_time)
        
        if not klines:
            print(f"Skipping {entry_time_str}: No data")
            continue
            
        target_kline = None
        for k in klines:
            k_open_time = k[0]
            k_dt = datetime.fromtimestamp(k_open_time / 1000)
            if k_dt == entry_dt - timedelta(minutes=5):
                target_kline = k
                break
        
        if not target_kline:
            target_kline = klines[-2] if len(klines) >= 2 else klines[-1]

        open_price = float(target_kline[1])
        high_price = float(target_kline[2])
        low_price = float(target_kline[3])
        close_price = float(target_kline[4])
        
        volatility = (high_price - low_price) / open_price * 100
        price_change = (close_price - open_price) / open_price * 100
        
        is_filtered = False
        filter_reason = ""
        
        if volatility < 0.15:
            is_filtered = True
            filter_reason = "LOW_VOL"
        elif abs(price_change) < 0.05:
            is_filtered = True
            filter_reason = "STAGNANT"
            
        raw_pnl = 0
        if 'pnl_net' in trade:
            raw_pnl = trade['pnl_net']
        elif 'pnl_pct' in trade:
            raw_pnl = trade['pnl_pct']
            
        original_pnl += raw_pnl
        original_count += 1
        if raw_pnl > 0:
            original_wins += 1
            
        action = "KEEP"
        if is_filtered:
            action = f"DROP ({filter_reason})"
            if raw_pnl > 0:
                filtered_good_trades += 1
            else:
                filtered_bad_trades += 1
        else:
            filtered_pnl += raw_pnl
            filtered_count += 1
            if raw_pnl > 0:
                filtered_wins += 1
                
        print(f"{entry_time_str} | {trade.get('direction', trade.get('side')):<5} | {raw_pnl:>8.2f} | {volatility:>8.3f} | {price_change:>8.3f} | {action}")
        
        time.sleep(0.1)

    print("-" * 100)
    print("RESULTS:")
    print(f"Original: {original_count} trades, PnL: {original_pnl:.2f}, Win Rate: {original_wins/original_count*100:.1f}%")
    if filtered_count > 0:
        print(f"Filtered: {filtered_count} trades, PnL: {filtered_pnl:.2f}, Win Rate: {filtered_wins/filtered_count*100:.1f}%")
    else:
        print("Filtered: 0 trades remaining.")
        
    print(f"Trades Dropped: {original_count - filtered_count}")
    print(f"  - Bad Trades Avoided: {filtered_bad_trades}")
    print(f"  - Good Trades Missed: {filtered_good_trades}")
    
    if filtered_bad_trades > filtered_good_trades:
        print("✅ SUCCESS: Filter removed more bad trades than good trades.")
    else:
        print("⚠️ WARNING: Filter removed more good trades (or equal).")

if __name__ == "__main__":
    file_path = "backtest_results/walk_forward/test_2024_v3.3.json"
    analyze_filters(file_path)
