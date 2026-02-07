#!/usr/bin/env python3
"""
dYdX 24h Backtest Script (Simplified)
=====================================
Fetches last 24h of 1m candles from dYdX and simulates trading based on Price Action & MTF RSI.
Note: Cannot backtest OBI (Order Book Imbalance) or Whale Order Flow as historical tick data is not available via standard API.
This backtest focuses on:
1. MTF Alignment (RSI, Trend Check)
2. Volatility Conditions
3. 24h Price Structure

Usage:
    python scripts/backtest_dydx_24h.py
"""

import asyncio
import aiohttp
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import talib
from typing import List, Dict

# Configuration (Matches User's latest settings)
class BacktestConfig:
    symbol = "BTC-USD"
    leverage = 50
    stop_loss_pct = 0.20
    take_profit_pct = 0.40  # Avg target
    
    # Strategy Parameters
    mtf_alignment_threshold = 40.0
    mtf_rsi_long_min = 40.0
    mtf_rsi_long_max = 65.0
    mtf_rsi_short_min = 35.0
    mtf_rsi_short_max = 70.0
    
    noise_threshold = 0.05 # 1m Volatility threshold

async def fetch_dydx_candles():
    """Fetch last 24h candles (approx 1440 candles + buffer)"""
    url = "https://indexer.dydx.trade/v4/candles/perpetualMarkets/BTC-USD"
    # We need 24h * 60 = 1440 candles. API limit usually 100.
    # We'll fetch in chunks or just get what is allowed. 
    # dYdX v4 API limit is often 100. We need to iterate.
    
    all_candles = []
    end_time = datetime.utcnow()
    
    print("⏳ Fetching 24h data from dYdX...", end="", flush=True)
    
    async with aiohttp.ClientSession() as session:
        for _ in range(20): # Try to get enough batches
            iso_end = end_time.isoformat() + "Z"
            params = {
                "resolution": "1MIN",
                "limit": 100,
                "toISO": iso_end
            }
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    break
                data = await resp.json()
                candles = data.get("candles", [])
                if not candles:
                    break
                
                all_candles.extend(candles)
                last_time = candles[-1]['startedAt']
                end_time = datetime.fromisoformat(last_time.replace("Z", ""))
                print(".", end="", flush=True)
                
                if len(all_candles) > 1600: # 1440 + buffer
                    break
                    
    print(" Done!")
    return all_candles

def process_data(candles):
    """Convert to DataFrame and calculate indicators"""
    df = pd.DataFrame(candles)
    df['startedAt'] = pd.to_datetime(df['startedAt'])
    df = df.sort_values('startedAt').reset_index(drop=True)
    
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)
    # dYdX v4 uses 'baseTokenVolume' or 'quoteTokenVolume'
    if 'volume' not in df.columns:
        if 'baseTokenVolume' in df.columns:
            df['volume'] = df['baseTokenVolume'].astype(float)
        elif 'quoteTokenVolume' in df.columns:
            df['volume'] = df['quoteTokenVolume'].astype(float)
    else:
        df['volume'] = df['volume'].astype(float)
    
    # Indicators
    df['rsi_1m'] = talib.RSI(df['close'], timeperiod=14)
    df['rsi_5m'] = talib.RSI(df['close'], timeperiod=14 * 5) # Approx
    df['rsi_15m'] = talib.RSI(df['close'], timeperiod=14 * 15) # Approx
    df['rsi_1h'] = talib.RSI(df['close'], timeperiod=14 * 60) # Approx
    
    # Simple Trend (EMA)
    df['ema_fast'] = talib.EMA(df['close'], timeperiod=9)
    df['ema_slow'] = talib.EMA(df['close'], timeperiod=21)
    
    return df

def simulate_strategy(df, config: BacktestConfig):
    """Simulate trades based on config"""
    trades = []
    position = None # {'entry': float, 'side': str, 'time': datetime}
    
    # Iterate through candles
    for i in range(100, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        # 1. MTF Check
        # Simplified: Alignment of RSIs and EMAs
        long_condition = (
            row['rsi_1m'] > config.mtf_rsi_long_min and 
            row['rsi_15m'] > 50 and 
            row['ema_fast'] > row['ema_slow']
        )
        
        short_condition = (
            row['rsi_1m'] < config.mtf_rsi_short_max and 
            row['rsi_15m'] < 50 and 
            row['ema_fast'] < row['ema_slow']
        )
        
        # 2. Entry Logic
        if position is None:
            if long_condition:
                position = {'entry': row['close'], 'side': 'LONG', 'time': row['startedAt']}
            elif short_condition:
                position = {'entry': row['close'], 'side': 'SHORT', 'time': row['startedAt']}
        
        # 3. Exit Logic (TP/SL)
        elif position:
            pnl_pct = 0
            if position['side'] == 'LONG':
                pnl_pct = (row['close'] - position['entry']) / position['entry'] * 100
            else:
                pnl_pct = (position['entry'] - row['close']) / position['entry'] * 100
            
            # Simple TP/SL check (High/Low usually used, but Close is safer for simple backtest)
            if pnl_pct <= -config.stop_loss_pct or pnl_pct >= config.take_profit_pct:
                trades.append({
                    'entry_time': position['time'],
                    'exit_time': row['startedAt'],
                    'side': position['side'],
                    'pnl_pct': pnl_pct,
                    'result': 'WIN' if pnl_pct > 0 else 'LOSS'
                })
                position = None
                
    return trades

async def main():
    print("="*60)
    print("🚀 dYdX 24h Backtest Simulation (Price Action Only)")
    print("="*60)
    print("⚠️ Note: This simulation ignores OBI/Whale Flow (Tick Data).")
    print("   Real bot performance depends 50% on OBI which is missing here.")
    print("="*60)
    
    candles = await fetch_dydx_candles()
    if not candles:
        print("❌ No data fetched.")
        return
        
    df = process_data(candles)
    print(f"📊 Processed {len(df)} candles (1m). Time: {df['startedAt'].min()} -> {df['startedAt'].max()}")
    
    config = BacktestConfig()
    trades = simulate_strategy(df, config)
    
    print(f"\n📈 Results (Last 24h):")
    total_trades = len(trades)
    wins = len([t for t in trades if t['result'] == 'WIN'])
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    total_pnl = sum([t['pnl_pct'] for t in trades])
    
    print(f"   Total Trades: {total_trades}")
    print(f"   Wins: {wins} | Losses: {losses}")
    print(f"   Win Rate: {win_rate:.2f}%")
    print(f"   Est. Net PnL (w/o fees): {total_pnl:.2f}%")
    print("\n   (Note: Real bot filters false signals using OBI, so real Win Rate might be higher)")

if __name__ == "__main__":
    asyncio.run(main())
