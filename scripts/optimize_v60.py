#!/usr/bin/env python3
"""
v6.0 最終優化 - 找出能盈利的參數組合
"""

import json
from pathlib import Path
from itertools import product

def load_all_trades():
    logs_dir = Path("/Users/akaihuangm1/Desktop/btn/logs")
    all_trades = []
    for json_file in logs_dir.rglob("trades_*.json"):
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)
            for trade in data.get('trades', []):
                if trade.get('status', '').startswith('CLOSED'):
                    all_trades.append(trade)
        except:
            continue
    return all_trades

def test_config(trades, config):
    """測試一組參數配置"""
    # 解構配置
    prob_min = config['prob_min']
    prob_max = config['prob_max']
    avoid_hours = config['avoid_hours']
    obi_min = config['obi_min']
    no_mom_th = config['no_mom_th']
    no_mom_sl = config['no_mom_sl']
    protect_th = config['protect_th']
    lock_th = config['lock_th']
    trailing = config['trailing']
    
    # 過濾
    passed = []
    for t in trades:
        strategy = t.get('strategy', '')
        direction = t.get('direction', '')
        prob = t.get('probability', 0)
        obi = t.get('obi', 0)
        ts = t.get('timestamp', '')
        
        hour = 12
        if 'T' in ts:
            try:
                hour = int(ts.split('T')[1][:2])
            except:
                pass
        
        # 過濾條件
        if strategy == 'DISTRIBUTION' and direction == 'SHORT':
            continue
        if prob < prob_min or prob > prob_max:
            continue
        if hour in avoid_hours:
            continue
        if direction == 'LONG' and obi < obi_min:
            continue
        
        passed.append(t)
    
    if len(passed) < 5:
        return None
    
    # 模擬出場
    total_pnl = 0
    wins = 0
    
    for t in passed:
        max_profit = t.get('max_profit_pct', 0)
        max_dd = abs(t.get('max_drawdown_pct', 0))
        actual_pnl = t.get('net_pnl_usdt', 0)
        
        # 無動能止損
        if max_profit < no_mom_th and max_dd >= no_mom_sl:
            pnl = -100 * (no_mom_sl / 100) - 4
        # 先漲保護
        elif max_profit >= protect_th and actual_pnl <= 0:
            pnl = -4
        # 早期鎖盈
        elif max_profit >= lock_th:
            exit_pct = max(max_profit - trailing, 0)
            pnl = max(100 * (exit_pct / 100) - 4, actual_pnl)
        else:
            pnl = actual_pnl
        
        total_pnl += pnl
        if pnl > 0:
            wins += 1
    
    winrate = wins / len(passed) * 100
    avg_pnl = total_pnl / len(passed)
    
    return {
        'pnl': total_pnl,
        'count': len(passed),
        'winrate': winrate,
        'avg_pnl': avg_pnl
    }

def main():
    trades = load_all_trades()
    
    print("=" * 80)
    print("🔬 v6.0 參數優化搜索")
    print("=" * 80)
    
    # 參數空間
    param_space = {
        'prob_min': [0.75, 0.80],
        'prob_max': [0.92, 0.95],
        'avoid_hours': [
            set(range(1, 7)),  # 1-6點
            set(range(1, 5)),  # 1-4點
            set(),  # 不避免
        ],
        'obi_min': [0.2, 0.3],
        'no_mom_th': [0.5, 1.0],
        'no_mom_sl': [4.0, 5.0],
        'protect_th': [3.0, 4.0],
        'lock_th': [3.0, 4.0],
        'trailing': [1.0, 1.5, 2.0],
    }
    
    best_profitable = None
    best_pnl = -99999
    profitable_configs = []
    
    # 網格搜索
    param_names = list(param_space.keys())
    param_values = list(param_space.values())
    
    for values in product(*param_values):
        config = dict(zip(param_names, values))
        result = test_config(trades, config)
        
        if result and result['pnl'] > 0:
            profitable_configs.append((config, result))
            
            if result['pnl'] > best_pnl:
                best_pnl = result['pnl']
                best_profitable = (config, result)
    
    print(f"\n找到 {len(profitable_configs)} 個盈利配置！")
    
    if profitable_configs:
        # 按 PnL 排序
        profitable_configs.sort(key=lambda x: x[1]['pnl'], reverse=True)
        
        print(f"\n🏆 Top 5 盈利配置:")
        for i, (config, result) in enumerate(profitable_configs[:5]):
            print(f"\n#{i+1}: PnL ${result['pnl']:+.2f}, {result['count']}筆, 勝率{result['winrate']:.0f}%")
            print(f"   機率: {config['prob_min']:.0%}-{config['prob_max']:.0%}")
            print(f"   避開時段: {sorted(config['avoid_hours']) if config['avoid_hours'] else '無'}")
            print(f"   OBI >= {config['obi_min']}")
            print(f"   無動能: <{config['no_mom_th']}% 且跌{config['no_mom_sl']}%")
            print(f"   先漲保護: >={config['protect_th']}%")
            print(f"   鎖盈: >={config['lock_th']}%, trailing {config['trailing']}%")
    else:
        print("\n❌ 沒有找到盈利配置")
        
        # 找最接近盈利的
        all_results = []
        for values in product(*param_values):
            config = dict(zip(param_names, values))
            result = test_config(trades, config)
            if result:
                all_results.append((config, result))
        
        all_results.sort(key=lambda x: x[1]['pnl'], reverse=True)
        
        print(f"\n最接近盈利的 3 個配置:")
        for i, (config, result) in enumerate(all_results[:3]):
            print(f"\n#{i+1}: PnL ${result['pnl']:+.2f}, {result['count']}筆, 勝率{result['winrate']:.0f}%")

if __name__ == "__main__":
    main()
