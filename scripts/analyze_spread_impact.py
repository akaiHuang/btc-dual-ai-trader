#!/usr/bin/env python3
"""分析 Spread 與進場策略對獲利的影響"""

import json
import statistics
import sys

def analyze(filepath: str):
    with open(filepath, 'r') as f:
        data = json.load(f)

    trades = [t for t in data['trades'] if t.get('maker_filled') and t.get('pnl_usdt', 0) != 0]

    print('=' * 70)
    print('📊 Spread 與獲利分析報告')
    print('=' * 70)

    # 1. Spread 高低對獲利的影響
    print('\n【1】Spread 高低 vs 獲利')
    print('-' * 50)

    trades_with_spread = [t for t in trades if t.get('order_spread_bps', 0) > 0]

    if trades_with_spread:
        spreads = [t['order_spread_bps'] for t in trades_with_spread]
        median_spread = statistics.median(spreads)
        
        low_spread = [t for t in trades_with_spread if t['order_spread_bps'] <= median_spread]
        high_spread = [t for t in trades_with_spread if t['order_spread_bps'] > median_spread]
        
        print(f'   中位數 Spread: {median_spread:.2f} bps')
        print()
        
        # 低 spread
        low_wins = len([t for t in low_spread if t['pnl_usdt'] > 0])
        low_pnl = sum(t['pnl_usdt'] for t in low_spread)
        low_wr = low_wins / len(low_spread) * 100 if low_spread else 0
        low_avg_spread = statistics.mean([t['order_spread_bps'] for t in low_spread]) if low_spread else 0
        
        print(f'   📉 低 Spread (≤{median_spread:.2f} bps):')
        print(f'      交易數: {len(low_spread)} | 平均 Spread: {low_avg_spread:.2f} bps')
        print(f'      勝率: {low_wr:.1f}% | 總 PnL: ${low_pnl:.4f}')
        
        # 高 spread
        high_wins = len([t for t in high_spread if t['pnl_usdt'] > 0])
        high_pnl = sum(t['pnl_usdt'] for t in high_spread)
        high_wr = high_wins / len(high_spread) * 100 if high_spread else 0
        high_avg_spread = statistics.mean([t['order_spread_bps'] for t in high_spread]) if high_spread else 0
        
        print(f'   📈 高 Spread (>{median_spread:.2f} bps):')
        print(f'      交易數: {len(high_spread)} | 平均 Spread: {high_avg_spread:.2f} bps')
        print(f'      勝率: {high_wr:.1f}% | 總 PnL: ${high_pnl:.4f}')

    # 2. Spread 與止盈成功率
    print('\n【2】Spread vs 止盈成功率')
    print('-' * 50)

    tp_trades = [t for t in trades_with_spread if 'TP_' in t.get('exit_method', '')]
    sl_trades = [t for t in trades_with_spread if 'SL_' in t.get('exit_method', '') or 'M%M' in t.get('exit_method', '')]

    if tp_trades:
        tp_avg_spread = statistics.mean([t['order_spread_bps'] for t in tp_trades])
        print(f'   ✅ TP 止盈交易 ({len(tp_trades)} 筆): 平均 Spread = {tp_avg_spread:.2f} bps')

    if sl_trades:
        sl_avg_spread = statistics.mean([t['order_spread_bps'] for t in sl_trades])
        print(f'   ❌ SL/M%M 止損交易 ({len(sl_trades)} 筆): 平均 Spread = {sl_avg_spread:.2f} bps')

    # 分層分析
    print('\n   按 Spread 分層:')
    for threshold in [1.0, 1.5, 2.0, 2.5, 3.0]:
        layer = [t for t in trades_with_spread if t['order_spread_bps'] <= threshold]
        if layer:
            tp_count = len([t for t in layer if 'TP_' in t.get('exit_method', '')])
            tp_rate = tp_count / len(layer) * 100
            layer_pnl = sum(t['pnl_usdt'] for t in layer)
            layer_wr = len([t for t in layer if t['pnl_usdt'] > 0]) / len(layer) * 100
            print(f'      Spread ≤ {threshold:.1f} bps: {len(layer):3d} 筆 | 勝率: {layer_wr:5.1f}% | TP率: {tp_rate:5.1f}% | PnL: ${layer_pnl:+.4f}')

    # 3. 做空 + anchor 分析
    print('\n【3】做空 (SHORT) - Anchor 分析')
    print('-' * 50)

    short_trades = [t for t in trades if t.get('direction') == 'SHORT']
    for anchor in ['bid', 'ask', 'mid']:
        anchor_trades = [t for t in short_trades if t.get('entry_anchor') == anchor]
        if anchor_trades:
            wins = len([t for t in anchor_trades if t['pnl_usdt'] > 0])
            wr = wins / len(anchor_trades) * 100
            pnl = sum(t['pnl_usdt'] for t in anchor_trades)
            
            # 計算進場偏離中間價的距離
            offsets = []
            for t in anchor_trades:
                mid = t.get('order_mid_at_place', 0)
                entry = t.get('entry_limit_price', 0)
                if mid > 0 and entry > 0:
                    offset_bps = (entry - mid) / mid * 10000  # SHORT: 正=高於mid
                    offsets.append(offset_bps)
            avg_offset = statistics.mean(offsets) if offsets else 0
            
            # TP 率
            tp_count = len([t for t in anchor_trades if 'TP_' in t.get('exit_method', '')])
            tp_rate = tp_count / len(anchor_trades) * 100
            
            print(f'   {anchor.upper():4s}: {len(anchor_trades):3d} 筆 | 勝率: {wr:5.1f}% | TP率: {tp_rate:5.1f}% | PnL: ${pnl:+.4f} | 偏離Mid: {avg_offset:+.2f} bps')

    # 4. 做多 + anchor 分析
    print('\n【4】做多 (LONG) - Anchor 分析')
    print('-' * 50)

    long_trades = [t for t in trades if t.get('direction') == 'LONG']
    for anchor in ['bid', 'ask', 'mid']:
        anchor_trades = [t for t in long_trades if t.get('entry_anchor') == anchor]
        if anchor_trades:
            wins = len([t for t in anchor_trades if t['pnl_usdt'] > 0])
            wr = wins / len(anchor_trades) * 100
            pnl = sum(t['pnl_usdt'] for t in anchor_trades)
            
            # 計算進場偏離中間價的距離
            offsets = []
            for t in anchor_trades:
                mid = t.get('order_mid_at_place', 0)
                entry = t.get('entry_limit_price', 0)
                if mid > 0 and entry > 0:
                    offset_bps = (mid - entry) / mid * 10000  # LONG: 正=低於mid
                    offsets.append(offset_bps)
            avg_offset = statistics.mean(offsets) if offsets else 0
            
            # TP 率
            tp_count = len([t for t in anchor_trades if 'TP_' in t.get('exit_method', '')])
            tp_rate = tp_count / len(anchor_trades) * 100
            
            print(f'   {anchor.upper():4s}: {len(anchor_trades):3d} 筆 | 勝率: {wr:5.1f}% | TP率: {tp_rate:5.1f}% | PnL: ${pnl:+.4f} | 偏離Mid: {avg_offset:+.2f} bps')

    # 5. 綜合建議
    print('\n' + '=' * 70)
    print('📋 分析結論')
    print('=' * 70)

    # 找出最佳組合
    results = []
    for direction in ['LONG', 'SHORT']:
        for anchor in ['bid', 'ask', 'mid']:
            combo_trades = [t for t in trades if t.get('direction') == direction and t.get('entry_anchor') == anchor]
            if combo_trades:
                combo_pnl = sum(t['pnl_usdt'] for t in combo_trades)
                combo_wr = len([t for t in combo_trades if t['pnl_usdt'] > 0]) / len(combo_trades) * 100
                combo_tp = len([t for t in combo_trades if 'TP_' in t.get('exit_method', '')]) / len(combo_trades) * 100
                results.append((direction, anchor, len(combo_trades), combo_wr, combo_tp, combo_pnl))

    # 按 PnL 排序
    results.sort(key=lambda x: x[5], reverse=True)
    
    print('\n   📊 所有組合排名 (按 PnL):')
    for i, r in enumerate(results):
        icon = '🥇' if i == 0 else ('🥈' if i == 1 else ('🥉' if i == 2 else '  '))
        print(f'   {icon} {r[0]:5s} + {r[1].upper():4s}: {r[2]:3d} 筆 | 勝率: {r[3]:5.1f}% | TP率: {r[4]:5.1f}% | PnL: ${r[5]:+.4f}')

    print()

if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else 'logs/maker_test/maker_fill_test_20260104_075228.json'
    analyze(filepath)
