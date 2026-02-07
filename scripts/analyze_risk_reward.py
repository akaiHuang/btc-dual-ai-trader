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

def simulate_trade(klines, direction, entry_price, sl_dist, tp_dist, fee):
    """
    Simulate a trade with specific SL/TP parameters.
    Returns: (Exit PnL %, Outcome String)
    """
    for k in klines:
        # kline: [time, open, high, low, close, ...]
        k_high = float(k[2])
        k_low = float(k[3])
        k_close = float(k[4])
        
        if direction == "LONG":
            # Check SL first (conservative)
            if k_low <= entry_price * (1 - sl_dist):
                return -sl_dist - fee, "STOPPED"
            # Check TP
            if k_high >= entry_price * (1 + tp_dist):
                return tp_dist - fee, "TAKE_PROFIT"
        else: # SHORT
            # Check SL (Price goes up)
            if k_high >= entry_price * (1 + sl_dist):
                return -sl_dist - fee, "STOPPED"
            # Check TP (Price goes down)
            if k_low <= entry_price * (1 - tp_dist):
                return tp_dist - fee, "TAKE_PROFIT"
                
    # If neither hit by end of data (15m), close at last price
    last_close = float(klines[-1][4])
    if direction == "LONG":
        pnl = (last_close - entry_price) / entry_price
    else:
        pnl = (entry_price - last_close) / entry_price
        
    return pnl - fee, "TIME_EXIT"

def analyze_risk_reward(trade_file):
    print(f"Analyzing Risk/Reward from: {trade_file}")
    
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

    # Parameters
    FEE = 0.0004  # 0.04% (Entry+Exit)
    TIGHT_SL = 0.00036 # 0.036% (Original tight SL)
    RELAXED_SL = 0.00076 # 0.076% (Tight + Fee buffer)
    TP_TARGET = 0.0025 # 0.25% (Ambitious target)

    print(f"Simulation Parameters:")
    print(f"Tight SL: {TIGHT_SL*100:.3f}% | Relaxed SL: {RELAXED_SL*100:.3f}%")
    print(f"TP Target: {TP_TARGET*100:.3f}% | Fee: {FEE*100:.3f}%")
    print("-" * 100)
    print(f"{'Time':<10} | {'Dir':<5} | {'Tight PnL':<10} | {'Relaxed PnL':<12} | {'Diff':<8} | {'Outcome'}")
    print("-" * 100)

    stats = {
        "saved_count": 0,      # Loss -> Profit
        "saved_pnl": 0.0,
        "worse_count": 0,      # Loss -> Bigger Loss
        "worse_pnl": 0.0,
        "neutral_count": 0,    # Same outcome
        "total_tight_pnl": 0.0,
        "total_relaxed_pnl": 0.0
    }

    for trade in trades:
        entry_time_str = trade['entry_time']
        entry_price = trade['entry_price']
        direction = trade['direction']
        
        try:
            entry_dt = datetime.fromisoformat(entry_time_str)
        except ValueError:
            try:
                entry_dt = datetime.strptime(entry_time_str.split('.')[0], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue

        # Fetch 15m data
        start_fetch = entry_dt
        end_fetch = entry_dt + timedelta(minutes=15)
        klines = get_binance_klines("BTCUSDT", "1m", start_fetch, end_fetch)
        
        if not klines:
            continue

        # Simulate both scenarios
        pnl_tight, res_tight = simulate_trade(klines, direction, entry_price, TIGHT_SL, TP_TARGET, FEE)
        pnl_relaxed, res_relaxed = simulate_trade(klines, direction, entry_price, RELAXED_SL, TP_TARGET, FEE)

        stats["total_tight_pnl"] += pnl_tight
        stats["total_relaxed_pnl"] += pnl_relaxed
        
        diff = pnl_relaxed - pnl_tight
        
        outcome_str = "SAME"
        if diff > 0.0001: # Improved significantly
            outcome_str = "✅ SAVED"
            stats["saved_count"] += 1
            stats["saved_pnl"] += diff
        elif diff < -0.0001: # Worsened significantly
            outcome_str = "⚠️ WORSE"
            stats["worse_count"] += 1
            stats["worse_pnl"] += diff
        else:
            stats["neutral_count"] += 1

        print(f"{entry_time_str[11:19]:<10} | {direction:<5} | {pnl_tight*100:>7.3f}% | {pnl_relaxed*100:>9.3f}% | {diff*100:>6.3f}% | {outcome_str} ({res_tight}->{res_relaxed})")

    print("-" * 100)
    print("SUMMARY:")
    print(f"Total Trades: {len(trades)}")
    print(f"✅ Saved Trades (Loss -> Win/Less Loss): {stats['saved_count']} (Net Gain: {stats['saved_pnl']*100:.3f}%)")
    print(f"⚠️ Worsened Trades (Loss -> Bigger Loss): {stats['worse_count']} (Net Loss: {stats['worse_pnl']*100:.3f}%)")
    print(f"Total Net PnL Improvement: {(stats['total_relaxed_pnl'] - stats['total_tight_pnl'])*100:.3f}%")
    print(f"Tight Strategy Total PnL: {stats['total_tight_pnl']*100:.3f}%")
    print(f"Relaxed Strategy Total PnL: {stats['total_relaxed_pnl']*100:.3f}%")

if __name__ == "__main__":
    log_dir = Path("logs/whale_paper_trader")
    files = sorted(log_dir.glob("trades_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if files:
        analyze_risk_reward(files[0])
