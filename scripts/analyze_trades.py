#!/usr/bin/env python3
"""分析交易記錄和dYdX歷史訂單"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

def analyze_local_trades():
    """分析本地交易記錄"""
    print('📊 本地交易記錄分析')
    print('='*60)
    
    trades_file = Path('logs/whale_paper_trader/trades_20251218_012547.json')
    with open(trades_file) as f:
        data = json.load(f)
    
    trades = data['trades']
    print(f'總交易數: {len(trades)}')
    print(f'總 PnL: ${data["total_pnl"]:.4f}')
    print()
    
    print('📋 交易明細:')
    print('-'*60)
    for i, t in enumerate(trades, 1):
        direction = t['direction']
        entry = t['entry_price']
        exit_p = t.get('exit_price', 0)
        pnl = t.get('net_pnl_usdt', 0)
        hold = t.get('hold_seconds', 0)
        status = t.get('status', '')[:50]
        strategy = t['strategy']
        entry_time = t.get('entry_time', '')[:19]
        
        emoji = '+' if pnl > 0 else '-'
        print(f'{i}. [{emoji}] {direction:5} | ${entry:.0f} -> ${exit_p:.0f} | {hold:.1f}s | ${pnl:+.4f} | {strategy}')
        print(f'   時間: {entry_time}')
        print(f'   狀態: {status}')
    print()
    
    # 分析問題
    print('🔍 問題分析:')
    print('-'*60)
    
    # 持倉時間
    hold_times = [t.get('hold_seconds', 0) for t in trades]
    avg_hold = sum(hold_times)/len(hold_times) if hold_times else 0
    print(f'平均持倉時間: {avg_hold:.1f}秒')
    print(f'最短: {min(hold_times):.1f}秒, 最長: {max(hold_times):.1f}秒')
    
    # 策略分布
    strategies = {}
    for t in trades:
        s = t['strategy']
        strategies[s] = strategies.get(s, 0) + 1
    print(f'策略分布: {strategies}')
    
    # 勝率
    wins = sum(1 for t in trades if t.get('net_pnl_usdt', 0) > 0)
    print(f'勝率: {wins}/{len(trades)} ({wins/len(trades)*100:.1f}%)')
    
    # 六維分數
    print()
    print('⚠️ 六維分數問題:')
    for t in trades:
        ls = t.get('six_dim_long_score', 0)
        ss = t.get('six_dim_short_score', 0)
        print(f'   {t["trade_id"][:20]}: LONG={ls}, SHORT={ss}')
    
    return trades


def fetch_dydx_history():
    """獲取dYdX歷史訂單"""
    print()
    print('📊 dYdX 歷史訂單')
    print('='*60)
    
    try:
        from dydx.dydx_client import DydxClient
        
        client = DydxClient()
        
        # 獲取最近的成交記錄
        fills = client.get_fills(limit=20)
        
        if fills:
            print(f'最近 {len(fills)} 筆成交:')
            print('-'*60)
            for f in fills:
                side = f.get('side', '')
                price = float(f.get('price', 0))
                size = float(f.get('size', 0))
                created = f.get('createdAt', '')[:19]
                fee = float(f.get('fee', 0))
                
                print(f'  {created} | {side:5} | ${price:.0f} | {size} BTC | fee=${fee:.4f}')
        else:
            print('沒有找到成交記錄')
            
        # 獲取最近訂單
        print()
        print('最近訂單:')
        orders = client.get_orders(limit=20)
        if orders:
            for o in orders:
                side = o.get('side', '')
                price = float(o.get('price', 0))
                size = float(o.get('size', 0))
                status = o.get('status', '')
                created = o.get('createdAt', '')[:19]
                
                print(f'  {created} | {side:5} | ${price:.0f} | {size} BTC | {status}')
        
    except Exception as e:
        print(f'❌ 無法連接 dYdX: {e}')
        print('嘗試使用 API 直接查詢...')
        fetch_dydx_via_api()


def fetch_dydx_via_api():
    """透過 API 直接查詢 dYdX"""
    try:
        import ccxt
        
        # 讀取配置
        config_file = Path('config/dydx_config.json')
        if config_file.exists():
            with open(config_file) as f:
                config = json.load(f)
        else:
            print('找不到 dydx_config.json')
            return
        
        exchange = ccxt.dydx({
            'apiKey': config.get('api_key', ''),
            'secret': config.get('api_secret', ''),
            'password': config.get('passphrase', ''),
            'options': {
                'network': 'testnet' if config.get('testnet', True) else 'mainnet'
            }
        })
        
        # 獲取成交記錄
        trades = exchange.fetch_my_trades('BTC/USD', limit=20)
        
        print(f'最近 {len(trades)} 筆成交:')
        for t in trades:
            print(f"  {t['datetime']} | {t['side']:5} | ${t['price']:.0f} | {t['amount']} BTC")
            
    except Exception as e:
        print(f'API 查詢失敗: {e}')


def analyze_signal_logs():
    """分析信號記錄，找出為什麼交易這麼少"""
    print()
    print('📊 信號記錄分析')
    print('='*60)
    
    # 讀取最近的 system log
    log_dir = Path('logs/whale_paper_trader')
    log_files = sorted(log_dir.glob('system_*.log'), reverse=True)
    
    if not log_files:
        print('找不到 system log')
        return
    
    # 統計拒絕原因
    reject_reasons = {}
    signal_count = 0
    entered_count = 0
    
    for log_file in log_files[:3]:  # 最近3個log
        with open(log_file, 'r', errors='ignore') as f:
            for line in f:
                # 統計信號
                if 'LONG_READY' in line or 'SHORT_READY' in line:
                    signal_count += 1
                if 'ENTERED' in line:
                    entered_count += 1
                    
                # 統計拒絕原因
                if '六維分數不足' in line:
                    reject_reasons['六維分數不足'] = reject_reasons.get('六維分數不足', 0) + 1
                if '方向衝突' in line:
                    reject_reasons['方向衝突'] = reject_reasons.get('方向衝突', 0) + 1
                if '追單保護' in line:
                    reject_reasons['追單保護'] = reject_reasons.get('追單保護', 0) + 1
                if '盤整' in line and ('跳過' in line or '觀望' in line):
                    reject_reasons['盤整觀望'] = reject_reasons.get('盤整觀望', 0) + 1
                if 'MTF 矛盾' in line:
                    reject_reasons['MTF矛盾'] = reject_reasons.get('MTF矛盾', 0) + 1
                if '無明顯主力' in line:
                    reject_reasons['無明顯主力'] = reject_reasons.get('無明顯主力', 0) + 1
                if 'OBI' in line and '背離' in line:
                    reject_reasons['OBI背離'] = reject_reasons.get('OBI背離', 0) + 1
    
    print(f'信號總數: {signal_count}')
    print(f'進場信號: {entered_count}')
    print()
    print('拒絕原因統計:')
    for reason, count in sorted(reject_reasons.items(), key=lambda x: -x[1]):
        print(f'  {reason}: {count} 次')


if __name__ == '__main__':
    trades = analyze_local_trades()
    fetch_dydx_history()
    analyze_signal_logs()
