import json
from datetime import datetime

def analyze_snapshot():
    with open('/Users/akaihuangm1/Desktop/btn/data/liquidation_pressure/latest_snapshot.json', 'r') as f:
        data = json.load(f)

    oi_series = data.get('open_interest', [])
    funding_series = data.get('funding_rate', [])

    print(f"{'Time':<20} | {'Price':<10} | {'OI':<10} | {'Funding':<10}")
    print("-" * 60)

    # Create a map for funding rates by timestamp (approximate matching might be needed)
    funding_map = {item['fundingTime']: item for item in funding_series}

    for item in oi_series:
        ts = item['timestamp']
        dt = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M')
        
        oi = float(item['sumOpenInterest'])
        val = float(item['sumOpenInterestValue'])
        price = val / oi if oi > 0 else 0
        
        # Find closest funding rate
        # Funding rate is usually every 8 hours, so we just look for the latest one before this timestamp
        latest_funding = None
        for f_ts in sorted(funding_map.keys()):
            if f_ts <= ts:
                latest_funding = funding_map[f_ts]
            else:
                break
        
        funding_rate = float(latest_funding['fundingRate']) if latest_funding else 0
        
        print(f"{dt:<20} | {price:<10.2f} | {oi:<10.2f} | {funding_rate:<10.8f}")

if __name__ == "__main__":
    analyze_snapshot()
