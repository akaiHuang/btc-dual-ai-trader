#!/usr/bin/env python3
"""分析什麼條件讓 M%M 成功 vs SL 失敗"""

import json
import statistics
import sys

def analyze(filepath: str):
    with open(filepath, 'r') as f:
        data = json.load(f)

    mpm_trades = [t for t in data['trades'] if t.get('maker_filled') and t.get('exit_method') == 'M%M_LOCK_MAKER']
    sl_trades = [t for t in data['trades'] if t.get('maker_filled') and t.get('exit_method') == 'SL_TAKER']

    print('=' * 70)
    print('📊 M%M 成功 vs SL 失敗 - 關鍵差異分析')
    print('=' * 70)

    print(f'\n   M%M 成功: {len(mpm_trades)} 筆 (+$0.63)')
    print(f'   SL 失敗: {len(sl_trades)} 筆 (-$0.74)')

    # 1. 進場 Spread 差異
    print('\n【1】進場 Spread 差異')
    print('-' * 50)

    mpm_spreads = [t.get('order_spread_bps', 0) for t in mpm_trades if t.get('order_spread_bps', 0) > 0]
    sl_spreads = [t.get('order_spread_bps', 0) for t in sl_trades if t.get('order_spread_bps', 0) > 0]

    if mpm_spreads and sl_spreads:
        print(f'   M%M 成功: 平均 {statistics.mean(mpm_spreads):.2f} bps')
        print(f'   SL 失敗: 平均 {statistics.mean(sl_spreads):.2f} bps')

    # 2. 進場價 vs Mid 差異
    print('\n【2】進場價 vs Mid 差異')
    print('-' * 50)

    for direction in ['LONG', 'SHORT']:
        mpm_dir = [t for t in mpm_trades if t.get('direction') == direction]
        sl_dir = [t for t in sl_trades if t.get('direction') == direction]
        
        mpm_diffs = []
        sl_diffs = []
        
        for t in mpm_dir:
            entry = t.get('maker_price', 0)
            mid = t.get('order_mid_at_place', 0)
            if entry > 0 and mid > 0:
                mpm_diffs.append((entry - mid) / mid * 10000)
        
        for t in sl_dir:
            entry = t.get('maker_price', 0)
            mid = t.get('order_mid_at_place', 0)
            if entry > 0 and mid > 0:
                sl_diffs.append((entry - mid) / mid * 10000)
        
        if mpm_diffs and sl_diffs:
            print(f'\n   {direction}:')
            mpm_avg = statistics.mean(mpm_diffs)
            sl_avg = statistics.mean(sl_diffs)
            
            if direction == 'LONG':
                mpm_good = '買便宜' if mpm_avg < 0 else '買貴'
                sl_good = '買便宜' if sl_avg < 0 else '買貴'
            else:
                mpm_good = '賣貴' if mpm_avg > 0 else '賣便宜'
                sl_good = '賣貴' if sl_avg > 0 else '賣便宜'
            
            print(f'      M%M 成功: {mpm_avg:+.2f} bps ({mpm_good})')
            print(f'      SL 失敗: {sl_avg:+.2f} bps ({sl_good})')

    # 3. 好進場價的比例
    print('\n【3】好進場價的比例')
    print('-' * 50)

    for direction in ['LONG', 'SHORT']:
        print(f'\n   {direction}:')
        
        mpm_dir = [t for t in mpm_trades if t.get('direction') == direction]
        sl_dir = [t for t in sl_trades if t.get('direction') == direction]
        
        # M%M 好進場價比例
        mpm_good = 0
        for t in mpm_dir:
            entry = t.get('maker_price', 0)
            mid = t.get('order_mid_at_place', 0)
            if entry > 0 and mid > 0:
                if direction == 'LONG' and entry < mid:
                    mpm_good += 1
                elif direction == 'SHORT' and entry > mid:
                    mpm_good += 1
        mpm_rate = mpm_good / len(mpm_dir) * 100 if mpm_dir else 0
        
        # SL 好進場價比例
        sl_good = 0
        for t in sl_dir:
            entry = t.get('maker_price', 0)
            mid = t.get('order_mid_at_place', 0)
            if entry > 0 and mid > 0:
                if direction == 'LONG' and entry < mid:
                    sl_good += 1
                elif direction == 'SHORT' and entry > mid:
                    sl_good += 1
        sl_rate = sl_good / len(sl_dir) * 100 if sl_dir else 0
        
        print(f'      M%M 成功: {mpm_rate:.1f}% 有好進場價')
        print(f'      SL 失敗: {sl_rate:.1f}% 有好進場價')

    # 4. Anchor 分布
    print('\n【4】Anchor 分布')
    print('-' * 50)

    for group_name, group_trades in [('M%M 成功', mpm_trades), ('SL 失敗', sl_trades)]:
        bid_count = len([t for t in group_trades if t.get('entry_anchor') == 'bid'])
        ask_count = len([t for t in group_trades if t.get('entry_anchor') == 'ask'])
        total = len(group_trades)
        print(f'   {group_name}: BID {bid_count} ({bid_count/total*100:.1f}%) | ASK {ask_count} ({ask_count/total*100:.1f}%)')

    # 5. 結論
    print('\n' + '=' * 70)
    print('📋 結論：M%M 成功 vs SL 失敗的關鍵差異')
    print('=' * 70)

    print()

if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else 'logs/maker_test/maker_fill_test_20260104_075228.json'
    analyze(filepath)
