#!/usr/bin/env python3
"""
dYdX Market Analysis Script
===========================
Analyzes 30 days of market data (1m, 5m, 15m) to find characteristics
of the 24-hour cycle in Taiwan Time (UTC+8).

Features:
- Fetches historical data from dYdX Indexer
- Aggregates by Hour of Day (0-23)
- Calculates:
    - Average Volatility (Pipe range)
    - Average Volume
    - Bull/Bear Probabilities
"""

import asyncio
import json
import time
import statistics
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import sys
import os

# Try to import pandas for easier analysis, fallback to manual if not present
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

# Import dYdX client logic (reusing DydxAPI structure)
try:
    from dydx_v4_client.indexer.rest.indexer_client import IndexerClient
except ImportError:
    print("❌ dYdX SDK not installed. Please run: pip install dydx-v4-client")
    sys.exit(1)

# Configuration
SYMBOL = "BTC-USD"
DAYS_TO_ANALYZE = 30
TAIWAN_OFFSET = timezone(timedelta(hours=8))

async def fetch_candles(client, resolution, days):
    """
    Fetch historical candles for N days.
    dYdX API usually returns 100 candles max per request.
    We need to paginate backwards.
    """
    all_candles = []
    
    # Calculate target time window
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)
    
    print(f"📥 Fetching {resolution} data for last {days} days...")
    print(f"   Target: {start_time.isoformat()} to {end_time.isoformat()}")
    
    current_end_iso = end_time.isoformat().replace("+00:00", "Z")
    
    total_candles_needed = 0
    if resolution == "1MIN":
        total_candles_needed = days * 24 * 60
    elif resolution == "5MINS":
        total_candles_needed = days * 24 * 12
    elif resolution == "15MINS":
        total_candles_needed = days * 24 * 4
        
    pbar_width = 50
    
    while True:
        try:
            # Fetch batch
            response = await client.markets.get_perpetual_market_candles(
                SYMBOL,
                resolution=resolution,
                to_iso=current_end_iso,
                limit=100
            )
            batch = response.get("candles", [])
            
            if not batch:
                break
                
            all_candles.extend(batch)
            
            # Progress update
            progress = min(len(all_candles) / total_candles_needed, 1.0)
            filled = int(pbar_width * progress)
            bar = "█" * filled + "-" * (pbar_width - filled)
            sys.stdout.write(f"\r   [{bar}] {len(all_candles)}/{total_candles_needed} candles")
            sys.stdout.flush()
            
            # Update cursor
            last_candle_time = batch[-1]["startedAt"]
            current_end_iso = last_candle_time
            
            # Check if we went far enough back
            last_dt = datetime.fromisoformat(last_candle_time.replace("Z", "+00:00"))
            if last_dt < start_time:
                break
                
            # Rate limit protection
            await asyncio.sleep(0.1)
            
        except Exception as e:
            print(f"\n❌ Error fetching batch: {e}")
            break
            
    print(f"\n   ✅ Fetched {len(all_candles)} candles")
    return all_candles

def parse_iso(iso_str):
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))

def analyze_data(candles, resolution):
    """Analyze candle data grouped by Taiwan Hour"""
    print(f"\n📊 Analyzing {resolution} Data (30 Days)...")
    
    # 1. Parse and Convert to Taiwan Time
    parsed_data = []
    for c in candles:
        dt = parse_iso(c["startedAt"]).astimezone(TAIWAN_OFFSET)
        open_p = float(c["open"])
        high_p = float(c["high"])
        low_p = float(c["low"])
        close_p = float(c["close"])
        vol = float(c["usdVolume"])
        
        parsed_data.append({
            "dt": dt,
            "hour": dt.hour,
            "volatility_pct": (high_p - low_p) / open_p * 100,
            "volume": vol,
            "close_change_pct": (close_p - open_p) / open_p * 100,
            "is_up": close_p > open_p
        })
        
    # Aggegation buckets
    hourly_stats = defaultdict(list)
    
    for d in parsed_data:
        h = d["hour"]
        hourly_stats[h].append(d)
        
    # Calculate statistics per hour
    report = []
    print(f"{'Hour (TW)':<10} | {'Avg Volatility':<15} | {'Avg Volume ($)':<15} | {'Win Rate (Bull)':<15} | {'Score'}")
    print("-" * 80)
    
    for h in range(24):
        stats = hourly_stats[h]
        if not stats:
            continue
            
        avg_volat = statistics.mean(d["volatility_pct"] for d in stats)
        avg_vol = statistics.mean(d["volume"] for d in stats)
        win_rate = sum(1 for d in stats if d["is_up"]) / len(stats) * 100
        
        # Simple Score: Volatility * Volume (normalized needed, but raw is fine for relative)
        score = avg_volat * 100 # weight volatility more
        
        report.append({
            "hour": h,
            "avg_volat": avg_volat,
            "avg_vol": avg_vol,
            "win_rate": win_rate
        })
        
        # Color coding for terminal
        volat_str = f"{avg_volat:.3f}%"
        if avg_volat > 0.15: volat_str = f"\033[92m{volat_str}\033[0m" # Green for high volat
        
        print(f"{h:02d}:00      | {volat_str:<15} | ${avg_vol/1000:,.0f}k{' '*7} | {win_rate:.1f}%{' '*9} |")

    if not report:
        print("⚠️ No data available for analysis.")
        return []

    # Find Best/Worst
    best_hour = max(report, key=lambda x: x["avg_volat"])
    quietest_hour = min(report, key=lambda x: x["avg_volat"])
    
    print("\n💡 Insights:")
    print(f"   🔥 Most Volatile Hour: {best_hour['hour']:02d}:00 (TW Time) - Avg Move: {best_hour['avg_volat']:.3f}%")
    print(f"   💤 Quietest Hour:      {quietest_hour['hour']:02d}:00 (TW Time) - Avg Move: {quietest_hour['avg_volat']:.3f}%")
    
    return report

async def main():
    indexer = IndexerClient("https://indexer.dydx.trade")
    
    print(f"🌏 Analyzing dYdX Market: {SYMBOL}")
    print(f"📅 Window: Last {DAYS_TO_ANALYZE} Days")
    print(f"⏰ Timezone: Taiwan (UTC+8)")
    
    # 1. 15-Min Analysis (General Trend)
    candles_15m = await fetch_candles(indexer, "15MINS", DAYS_TO_ANALYZE)
    analyze_data(candles_15m, "15MINS")
    
    # 2. 5-Min Analysis (Scalping Context)
    candles_5m = await fetch_candles(indexer, "5MINS", DAYS_TO_ANALYZE//2) # Analyze 15 days for 5m to save time
    analyze_data(candles_5m, "5MINS (Last 15 Days)")
    
    # 3. 1-Min Analysis (Micro Structure) -> Maybe analyze distinct high-volat days?
    # Fetching 30 days of 1-min is huge. Let's do last 7 days.
    candles_1m = await fetch_candles(indexer, "1MIN", 7)
    analyze_data(candles_1m, "1MIN (Last 7 Days)")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Analysis stopped")
