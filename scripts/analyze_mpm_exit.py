#!/usr/bin/env python3
"""分析 M%M 出場如何獲利最高"""

import json
import statistics
import sys

def analyze(filepath: str):
    with open(filepath, 'r') as f:
        data = json.load(f)

    trades = [t for t in data['trades'] if t.get('maker_filled') and t.get('exit_method') == 'M%M_LOCK_MAKER']

    print('=' * 70)
    print('📊 M%M_LOCK_MAKER 出場分析 - 如何獲利最高')
    print('=' * 70)

    print(f'\n總筆數: {len(trades)}')
    print(f'總 PnL: ${sum(t["pnl_usdt"] for t in trades):.4f}')

    # 1. 按 peak_pnl_pct 分析 (鎖利時的最高獲利%)
    print('\n【1】鎖利時的 Peak ROE% vs 實際獲利')
    print('-' * 50)

    peak_groups = {}
    for t in trades:
        peak = t.get('peak_pnl_pct', 0) * 50  # 轉成 ROE%
        if peak < 0.5:
            group = '0.3-0.5%'
        elif peak < 1.0:
            group = '0.5-1.0%'
        elif peak < 1.5:
            group = '1.0-1.5%'
        elif peak < 2.0:
            group = '1.5-2.0%'
        else:
            group = '2.0%+'
        
        if group not in peak_groups:
            peak_groups[group] = []
        peak_groups[group].append(t)

    for group in ['0.3-0.5%', '0.5-1.0%', '1.0-1.5%', '1.5-2.0%', '2.0%+']:
        if group in peak_groups:
            trades_in_group = peak_groups[group]
            total_pnl = sum(t['pnl_usdt'] for t in trades_in_group)
            avg_pnl = total_pnl / len(trades_in_group)
            wins = len([t for t in trades_in_group if t['pnl_usdt'] > 0])
            wr = wins / len(trades_in_group) * 100
            print(f'   Peak ROE {group}: {len(trades_in_group):3d} 筆 | 勝率 {wr:5.1f}% | 總 ${total_pnl:+.4f} | 平均 ${avg_pnl:+.6f}')

    # 2. 按持倉時間分析
    print('\n【2】持倉時間 vs 獲利')
    print('-' * 50)

    hold_times = [t.get('hold_seconds', 0) for t in trades if t.get('hold_seconds', 0) > 0]
    if hold_times:
        median_hold = statistics.median(hold_times)
        
        short_hold = [t for t in trades if t.get('hold_seconds', 0) <= median_hold]
        long_hold = [t for t in trades if t.get('hold_seconds', 0) > median_hold]
        
        print(f'   中位數持倉: {median_hold:.1f} 秒')
        
        if short_hold:
            pnl = sum(t['pnl_usdt'] for t in short_hold)
            avg = pnl / len(short_hold)
            print(f'   短持倉 (≤{median_hold:.0f}s): {len(short_hold):3d} 筆 | 總 ${pnl:+.4f} | 平均 ${avg:+.6f}')
        
        if long_hold:
            pnl = sum(t['pnl_usdt'] for t in long_hold)
            avg = pnl / len(long_hold)
            print(f'   長持倉 (>{median_hold:.0f}s): {len(long_hold):3d} 筆 | 總 ${pnl:+.4f} | 平均 ${avg:+.6f}')

    # 3. 按 spread 分析
    print('\n【3】進場 Spread vs 獲利')
    print('-' * 50)

    spreads = [t.get('order_spread_bps', 0) for t in trades if t.get('order_spread_bps', 0) > 0]
    if spreads:
        median_spread = statistics.median(spreads)
        
        low_spread = [t for t in trades if t.get('order_spread_bps', 0) <= median_spread]
        high_spread = [t for t in trades if t.get('order_spread_bps', 0) > median_spread]
        
        print(f'   中位數 Spread: {median_spread:.2f} bps')
        
        if low_spread:
            pnl = sum(t['pnl_usdt'] for t in low_spread)
            avg = pnl / len(low_spread)
            print(f'   低 Spread (≤{median_spread:.2f}): {len(low_spread):3d} 筆 | 總 ${pnl:+.4f} | 平均 ${avg:+.6f}')
        
        if high_spread:
            pnl = sum(t['pnl_usdt'] for t in high_spread)
            avg = pnl / len(high_spread)
            print(f'   高 Spread (>{median_spread:.2f}): {len(high_spread):3d} 筆 | 總 ${pnl:+.4f} | 平均 ${avg:+.6f}')

    # 4. 按方向分析
    print('\n【4】方向 vs 獲利')
    print('-' * 50)

    for direction in ['LONG', 'SHORT']:
        dir_trades = [t for t in trades if t.get('direction') == direction]
        if dir_trades:
            pnl = sum(t['pnl_usdt'] for t in dir_trades)
            avg = pnl / len(dir_trades)
            wins = len([t for t in dir_trades if t['pnl_usdt'] > 0])
            wr = wins / len(dir_trades) * 100
            print(f'   {direction}: {len(dir_trades):3d} 筆 | 勝率 {wr:5.1f}% | 總 ${pnl:+.4f} | 平均 ${avg:+.6f}')

    # 5. 按 anchor 分析
    print('\n【5】進場 Anchor vs 獲利')
    print('-' * 50)

    for anchor in ['bid', 'ask']:
        anchor_trades = [t for t in trades if t.get('entry_anchor') == anchor]
        if anchor_trades:
            pnl = sum(t['pnl_usdt'] for t in anchor_trades)
            avg = pnl / len(anchor_trades)
            wins = len([t for t in anchor_trades if t['pnl_usdt'] > 0])
            wr = wins / len(anchor_trades) * 100
            print(f'   {anchor.upper()}: {len(anchor_trades):3d} 筆 | 勝率 {wr:5.1f}% | 總 ${pnl:+.4f} | 平均 ${avg:+.6f}')

    # 6. 找出獲利最高的交易特徵
    print('\n【6】獲利最高的 Top 10 交易特徵')
    print('-' * 50)

    sorted_trades = sorted(trades, key=lambda x: x['pnl_usdt'], reverse=True)[:10]
    for i, t in enumerate(sorted_trades):
        peak_roe = t.get('peak_pnl_pct', 0) * 50
        hold = t.get('hold_seconds', 0)
        spread = t.get('order_spread_bps', 0)
        direction = t.get('direction', '?')
        anchor = t.get('entry_anchor', '?')
        pnl = t['pnl_usdt']
        print(f'   #{i+1}: ${pnl:+.4f} | {direction} {anchor.upper()} | Peak {peak_roe:.2f}% | Hold {hold:.1f}s | Spread {spread:.2f}bps')

    # 7. 最佳組合分析
    print('\n【7】最佳組合分析 (按平均 PnL 排序)')
    print('-' * 50)

    combos = {}
    for t in trades:
        peak = t.get('peak_pnl_pct', 0) * 50
        spread = t.get('order_spread_bps', 0)
        direction = t.get('direction', '?')
        
        peak_cat = 'highPeak' if peak >= 1.0 else 'lowPeak'
        spread_cat = 'lowSpread' if spread <= 0.66 else 'highSpread'
        
        key = f'{direction}_{peak_cat}_{spread_cat}'
        if key not in combos:
            combos[key] = []
        combos[key].append(t)

    combo_stats = []
    for key, combo_trades in combos.items():
        total_pnl = sum(t['pnl_usdt'] for t in combo_trades)
        avg_pnl = total_pnl / len(combo_trades)
        combo_stats.append((key, len(combo_trades), total_pnl, avg_pnl))

    combo_stats.sort(key=lambda x: x[3], reverse=True)

    for key, count, total, avg in combo_stats:
        print(f'   {key}: {count:3d} 筆 | 總 ${total:+.4f} | 平均 ${avg:+.6f}')

    # 8. M%M 鎖利階段分析
    print('\n【8】M%M 觸發後回吐多少?')
    print('-' * 50)
    
    for t in trades[:5]:  # 看幾筆例子
        peak_roe = t.get('peak_pnl_pct', 0) * 50
        actual_roe = t.get('pnl_usdt', 0) / (20 / 50) * 100  # 估算
        direction = t.get('direction', '?')
        print(f'   {direction}: Peak {peak_roe:.2f}% → 實際 ${t["pnl_usdt"]:+.4f}')

    print()

if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else 'logs/maker_test/maker_fill_test_20260104_075228.json'
    analyze(filepath)
