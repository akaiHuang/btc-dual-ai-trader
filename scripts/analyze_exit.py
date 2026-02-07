#!/usr/bin/env python3
"""分析平倉情況"""

import json
import statistics
import sys

def analyze(filepath: str):
    with open(filepath, 'r') as f:
        data = json.load(f)

    trades = [t for t in data['trades'] if t.get('maker_filled') and t.get('pnl_usdt', 0) != 0]

    print('=' * 70)
    print('📊 平倉分析報告')
    print('=' * 70)

    # 1. 平倉方式統計
    print('\n【1】平倉方式統計')
    print('-' * 50)

    exit_methods = {}
    for t in trades:
        method = t.get('exit_method', 'unknown')
        if method not in exit_methods:
            exit_methods[method] = {'count': 0, 'pnl': 0, 'wins': 0}
        exit_methods[method]['count'] += 1
        exit_methods[method]['pnl'] += t['pnl_usdt']
        if t['pnl_usdt'] > 0:
            exit_methods[method]['wins'] += 1

    for method in sorted(exit_methods.keys()):
        stats = exit_methods[method]
        wr = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
        avg_pnl = stats['pnl'] / stats['count'] if stats['count'] > 0 else 0
        print(f'   {method}:')
        print(f'      次數: {stats["count"]:3d} | 勝率: {wr:5.1f}% | 總PnL: ${stats["pnl"]:+.4f} | 平均: ${avg_pnl:+.6f}')

    # 2. 平倉價 vs Mid (按方向和方式)
    print('\n【2】平倉價 vs 觸發時 Mid')
    print('-' * 50)

    for direction in ['LONG', 'SHORT']:
        dir_trades = [t for t in trades if t.get('direction') == direction]
        if not dir_trades:
            continue
        
        print(f'\n   {direction}:')
        
        for method in ['M%M_LOCK_MAKER', 'SL_TAKER', 'TP_MAKER', 'TP_TAKER']:
            method_trades = [t for t in dir_trades if t.get('exit_method') == method]
            if not method_trades:
                continue
            
            diffs = []
            for t in method_trades:
                exit_price = t.get('exit_price', 0)
                trigger_mid = t.get('exit_trigger_mid', 0)
                if exit_price > 0 and trigger_mid > 0:
                    diff_bps = (exit_price - trigger_mid) / trigger_mid * 10000
                    diffs.append(diff_bps)
            
            if diffs:
                avg_diff = statistics.mean(diffs)
                # LONG 平倉: 賣出，正=賣貴(好), 負=賣便宜(差)
                # SHORT 平倉: 買回，正=買貴(差), 負=買便宜(好)
                if direction == 'LONG':
                    status = '賣貴✅' if avg_diff > 0 else '賣便宜❌'
                else:
                    status = '買便宜✅' if avg_diff < 0 else '買貴❌'
                
                pnl = sum(t['pnl_usdt'] for t in method_trades)
                print(f'      {method}: 平倉價 vs Mid = {avg_diff:+.2f} bps ({status}) | PnL ${pnl:+.4f}')

    # 3. M%M_LOCK 詳細分析
    print('\n【3】M%M_LOCK_MAKER 詳細分析')
    print('-' * 50)

    mpm_trades = [t for t in trades if t.get('exit_method') == 'M%M_LOCK_MAKER']
    if mpm_trades:
        wins = len([t for t in mpm_trades if t['pnl_usdt'] > 0])
        losses = len([t for t in mpm_trades if t['pnl_usdt'] < 0])
        total_pnl = sum(t['pnl_usdt'] for t in mpm_trades)
        print(f'   總筆數: {len(mpm_trades)} (勝 {wins}, 敗 {losses})')
        print(f'   勝率: {wins/len(mpm_trades)*100:.1f}%')
        print(f'   總 PnL: ${total_pnl:.4f}')
        
        # 分方向
        for direction in ['LONG', 'SHORT']:
            dir_mpm = [t for t in mpm_trades if t.get('direction') == direction]
            if dir_mpm:
                dir_wins = len([t for t in dir_mpm if t['pnl_usdt'] > 0])
                dir_pnl = sum(t['pnl_usdt'] for t in dir_mpm)
                print(f'   {direction}: {len(dir_mpm)} 筆 | 勝 {dir_wins} | PnL ${dir_pnl:+.4f}')

    # 4. SL_TAKER 詳細分析  
    print('\n【4】SL_TAKER 詳細分析')
    print('-' * 50)

    sl_trades = [t for t in trades if t.get('exit_method') == 'SL_TAKER']
    if sl_trades:
        wins = len([t for t in sl_trades if t['pnl_usdt'] > 0])
        losses = len([t for t in sl_trades if t['pnl_usdt'] < 0])
        total_pnl = sum(t['pnl_usdt'] for t in sl_trades)
        print(f'   總筆數: {len(sl_trades)} (勝 {wins}, 敗 {losses})')
        print(f'   勝率: {wins/len(sl_trades)*100:.1f}%')
        print(f'   總 PnL: ${total_pnl:.4f}')
        
        # 分方向
        for direction in ['LONG', 'SHORT']:
            dir_sl = [t for t in sl_trades if t.get('direction') == direction]
            if dir_sl:
                dir_wins = len([t for t in dir_sl if t['pnl_usdt'] > 0])
                dir_pnl = sum(t['pnl_usdt'] for t in dir_sl)
                print(f'   {direction}: {len(dir_sl)} 筆 | 勝 {dir_wins} | PnL ${dir_pnl:+.4f}')

    # 5. 按 anchor 分析平倉
    print('\n【5】平倉按 Anchor 分析')
    print('-' * 50)

    for anchor in ['bid', 'ask']:
        anchor_trades = [t for t in trades if t.get('entry_anchor') == anchor]
        if not anchor_trades:
            continue
        
        print(f'\n   進場用 {anchor.upper()}:')
        
        methods = {}
        for t in anchor_trades:
            method = t.get('exit_method', 'unknown')
            if method not in methods:
                methods[method] = {'count': 0, 'pnl': 0, 'wins': 0}
            methods[method]['count'] += 1
            methods[method]['pnl'] += t['pnl_usdt']
            if t['pnl_usdt'] > 0:
                methods[method]['wins'] += 1
        
        for method in sorted(methods.keys()):
            stats = methods[method]
            wr = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
            print(f'      {method}: {stats["count"]:3d} 筆 | 勝率 {wr:5.1f}% | PnL ${stats["pnl"]:+.4f}')

    # 6. 平倉滑點分析
    print('\n【6】平倉滑點分析 (實際成交 vs 預期)')
    print('-' * 50)
    
    for method in ['M%M_LOCK_MAKER', 'SL_TAKER']:
        method_trades = [t for t in trades if t.get('exit_method') == method]
        if not method_trades:
            continue
        
        slippages = [t.get('exit_slippage_bps', 0) for t in method_trades if t.get('exit_slippage_bps', 0) != 0]
        if slippages:
            avg_slip = statistics.mean(slippages)
            print(f'   {method}: 平均滑點 = {avg_slip:+.2f} bps')

    print()

if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else 'logs/maker_test/maker_fill_test_20260104_075228.json'
    analyze(filepath)
