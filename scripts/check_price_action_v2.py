import requests
import json
import pandas as pd
from datetime import datetime, timedelta
import time
import sys
from pathlib import Path

def get_binance_klines(symbol, interval, start_time, end_time):
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": int(start_time.timestamp() * 1000),
        "endTime": int(end_time.timestamp() * 1000),
        "limit": 1000
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching klines: {e}")
        return []

def analyze_trade_potential(trade_file):
    print(f"Analyzing trades from: {trade_file}")
    
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

    print(f"Found {len(trades)} trades. Analyzing price action after entry...")
    print("-" * 100)
    print(f"{'Time':<20} | {'Dir':<5} | {'Entry':<10} | {'1m Max%':<8} | {'5m Max%':<8} | {'15m Max%':<8} | {'Fee%':<6} | {'Result'}")
    print("-" * 100)

    fee_threshold_pct = 0.04  # Approx fee for entry+exit (0.02% * 2)
    
    results = []

    for trade in trades:
        entry_time_str = trade['entry_time']
        entry_price = trade['entry_price']
        direction = trade['direction']
        
        # Parse time
        try:
            entry_dt = datetime.fromisoformat(entry_time_str)
        except ValueError:
            try:
                entry_dt = datetime.strptime(entry_time_str.split('.')[0], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                print(f"Skipping invalid date format: {entry_time_str}")
                continue

        # Fetch data for next 30 minutes
        start_fetch = entry_dt
        end_fetch = entry_dt + timedelta(minutes=30)
        
        klines = get_binance_klines("BTCUSDT", "1m", start_fetch, end_fetch)
        
        if not klines:
            print(f"No klines found for {entry_time_str}")
            continue

        # Analyze price action
        max_profit_pct_1m = -999
        max_profit_pct_5m = -999
        max_profit_pct_15m = -999
        
        for i, k in enumerate(klines):
            # kline format: [open_time, open, high, low, close, volume, ...]
            k_time = datetime.fromtimestamp(k[0] / 1000)
            k_high = float(k[2])
            k_low = float(k[3])
            
            minutes_passed = (k_time - entry_dt).total_seconds() / 60
            
            if direction == "LONG":
                profit_pct = (k_high - entry_price) / entry_price * 100
            else: # SHORT
                profit_pct = (entry_price - k_low) / entry_price * 100
            
            if minutes_passed <= 2: # Approx 1-2 min
                max_profit_pct_1m = max(max_profit_pct_1m, profit_pct)
            
            if minutes_passed <= 6: # Approx 5 min
                max_profit_pct_5m = max(max_profit_pct_5m, profit_pct)
                
            if minutes_passed <= 16: # Approx 15 min
                max_profit_pct_15m = max(max_profit_pct_15m, profit_pct)

        # Determine if it was a "Good Call" (Profit > Fee)
        is_profitable = max_profit_pct_15m > fee_threshold_pct
        result_str = "✅ PROFIT" if is_profitable else "❌ LOSS"
        if max_profit_pct_15m > fee_threshold_pct * 2:
            result_str = "🚀 BIG WIN"
            
        print(f"{entry_time_str[11:19]:<20} | {direction:<5} | {entry_price:<10.2f} | {max_profit_pct_1m:>7.3f}% | {max_profit_pct_5m:>7.3f}% | {max_profit_pct_15m:>7.3f}% | {fee_threshold_pct:.3f}% | {result_str}")
        
        results.append({
            'direction': direction,
            'max_15m': max_profit_pct_15m,
            'profitable': is_profitable
        })

    # Summary
    print("-" * 100)
    total = len(results)
    profitable = sum(1 for r in results if r['profitable'])
    avg_max_profit = sum(r['max_15m'] for r in results) / total if total > 0 else 0
    
    print(f"Summary:")
    print(f"Total Trades Analyzed: {total}")
    print(f"Potential Win Rate (if held 15m): {profitable/total*100:.1f}%")
    print(f"Avg Max Potential Profit (15m): {avg_max_profit:.3f}% (Fee: {fee_threshold_pct:.3f}%)")

if __name__ == "__main__":
    # Find the latest trade file
    log_dir = Path("logs/whale_paper_trader")
    files = sorted(log_dir.glob("trades_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    
    if files:
        analyze_trade_potential(files[0])
    else:
        print("No trade files found.")
