import json
from collections import defaultdict

def analyze_historical_performance(file_path):
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return

    orders = data.get('orders', {})
    
    # If orders is a list (old format), group by strategy
    if isinstance(orders, list):
        temp_orders = defaultdict(list)
        for t in orders:
            temp_orders[t.get('strategy', 'Unknown')].append(t)
        orders = temp_orders

    print(f"{'Strategy':<25} | {'Trades':<6} | {'Win Rate':<8} | {'Avg PnL':<10}")
    print("-" * 60)

    for strategy, trades in orders.items():
        if not trades:
            continue
        
        closed_trades = [t for t in trades if t.get('exit_time')]
        if not closed_trades:
            continue
            
        wins = [t for t in closed_trades if t.get('pnl_usdt', 0) > 0]
        win_rate = len(wins) / len(closed_trades) * 100
        avg_pnl = sum(t.get('pnl_usdt', 0) for t in closed_trades) / len(closed_trades)
        
        print(f"{strategy:<25} | {len(closed_trades):<6} | {win_rate:<6.1f}% | ${avg_pnl:<8.2f}")

if __name__ == "__main__":
    # Analyze a more recent file
    analyze_historical_performance('/Users/akaihuangm1/Desktop/btn/data/paper_trading/pt_20251124_1553/trading_data.json')
