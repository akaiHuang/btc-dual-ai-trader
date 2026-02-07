#!/usr/bin/env python3
"""
dYdX 真實交易記錄同步工具
=========================
從 dYdX Indexer API 獲取真實成交記錄並保存為 JSON 格式，
方便後續分析和除錯。

使用方式:
    python scripts/sync_dydx_trades_to_json.py                    # 同步最近 1000 筆
    python scripts/sync_dydx_trades_to_json.py --date 2025-12-22  # 只同步特定日期
    python scripts/sync_dydx_trades_to_json.py --limit 500        # 指定筆數
    python scripts/sync_dydx_trades_to_json.py --output trades.json  # 指定輸出檔案

輸出:
    logs/dydx_real_trades/trades_YYYYMMDD.json  (按日期分檔)
    或指定的輸出檔案
"""

import aiohttp
import asyncio
import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional


def load_address_from_env() -> Optional[str]:
    """從 .env 檔案讀取 dYdX 地址"""
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    if key == 'DYDX_ADDRESS':
                        return value.strip('"').strip("'")
    return None


def pair_trades(fills: List[Dict]) -> List[Dict]:
    """
    將成交記錄配對成完整交易 (開倉 + 平倉)
    
    Returns:
        交易列表，每筆包含:
        - trade_id: 交易 ID
        - direction: LONG/SHORT
        - entry_time, exit_time
        - entry_price, exit_price
        - size: BTC 數量
        - pnl_usdc: 盈虧 (USDC)
        - pnl_pct: 盈虧百分比
        - hold_seconds: 持倉秒數
        - fills: 原始成交記錄
    """
    # 按時間排序 (從舊到新)
    fills = sorted(fills, key=lambda x: x.get('createdAt', ''))
    
    trades = []
    current_position = None
    trade_counter = 0
    
    for fill in fills:
        side = fill.get('side', '')
        size = float(fill.get('size', 0))
        price = float(fill.get('price', 0))
        created_at = fill.get('createdAt', '')
        
        if current_position is None:
            # 開倉
            current_position = {
                'entry_side': side,
                'entry_price': price,
                'size': size,
                'entry_time': created_at,
                'entry_fills': [fill]
            }
        else:
            # 檢查是否平倉
            is_closing = (
                (current_position['entry_side'] == 'BUY' and side == 'SELL') or
                (current_position['entry_side'] == 'SELL' and side == 'BUY')
            )
            
            if is_closing:
                # 計算 PnL
                trade_size = min(size, current_position['size'])
                if current_position['entry_side'] == 'BUY':
                    pnl = (price - current_position['entry_price']) * trade_size
                    pnl_pct = (price - current_position['entry_price']) / current_position['entry_price'] * 100
                else:
                    pnl = (current_position['entry_price'] - price) * trade_size
                    pnl_pct = (current_position['entry_price'] - price) / current_position['entry_price'] * 100
                
                # 計算持倉時間
                try:
                    entry_dt = datetime.fromisoformat(current_position['entry_time'].replace('Z', '+00:00'))
                    exit_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    hold_seconds = (exit_dt - entry_dt).total_seconds()
                except:
                    hold_seconds = 0
                
                # 生成交易記錄
                direction = 'LONG' if current_position['entry_side'] == 'BUY' else 'SHORT'
                trade_id = f"DYDX_{current_position['entry_time'][:10].replace('-', '')}_{trade_counter:04d}"
                
                trades.append({
                    'trade_id': trade_id,
                    'direction': direction,
                    'entry_time': current_position['entry_time'],
                    'exit_time': created_at,
                    'entry_price': current_position['entry_price'],
                    'exit_price': price,
                    'size': trade_size,
                    'pnl_usdc': pnl,
                    'pnl_pct': pnl_pct,
                    'hold_seconds': hold_seconds,
                    'status': '✅ WIN' if pnl > 0 else '❌ LOSS',
                    'fills': current_position['entry_fills'] + [fill]
                })
                
                trade_counter += 1
                
                # 處理剩餘倉位
                remaining = current_position['size'] - size
                if remaining > 0.0001:
                    current_position['size'] = remaining
                else:
                    current_position = None
            else:
                # 同向加倉 - 計算平均價格
                total_size = current_position['size'] + size
                avg_price = (
                    current_position['entry_price'] * current_position['size'] + 
                    price * size
                ) / total_size
                current_position['entry_price'] = avg_price
                current_position['size'] = total_size
                current_position['entry_fills'].append(fill)
    
    return trades


async def sync_dydx_trades(
    address: str = None,
    limit: int = 1000,
    date_filter: str = None,
    output_file: str = None
) -> Dict:
    """
    同步 dYdX 真實交易記錄
    
    Args:
        address: dYdX 錢包地址
        limit: 獲取的成交記錄數量上限
        date_filter: 只處理特定日期 (YYYY-MM-DD)
        output_file: 指定輸出檔案路徑
    
    Returns:
        交易統計摘要
    """
    # 讀取地址
    if not address:
        address = load_address_from_env()
    if not address:
        print("❌ 錯誤: 未指定 dYdX 地址")
        return {}
    
    base_url = 'https://indexer.dydx.trade/v4'
    
    print(f'📍 錢包地址: {address[:15]}...{address[-5:]}')
    print(f'🔗 API: {base_url}')
    print()
    
    async with aiohttp.ClientSession() as session:
        # 獲取帳戶資訊
        async with session.get(f'{base_url}/addresses/{address}/subaccountNumber/0') as resp:
            if resp.status == 200:
                data = await resp.json()
                account = data.get('subaccount', {})
                equity = float(account.get('equity', 0))
                print(f'💰 帳戶權益: ${equity:,.2f} USDC')
                print()
        
        # 獲取成交紀錄
        async with session.get(f'{base_url}/fills?address={address}&subaccountNumber=0&limit={limit}') as resp:
            if resp.status != 200:
                print(f'❌ API 錯誤: {resp.status}')
                return {}
            
            data = await resp.json()
            fills = data.get('fills', [])
            
            print(f'📥 獲取到 {len(fills)} 筆成交記錄')
            
            # 日期過濾
            if date_filter:
                fills = [f for f in fills if f.get('createdAt', '').startswith(date_filter)]
                print(f'📅 過濾日期 {date_filter}: {len(fills)} 筆')
            
            if not fills:
                print('⚠️ 沒有成交記錄')
                return {}
            
            # 配對交易
            trades = pair_trades(fills)
            print(f'🔄 配對成 {len(trades)} 筆完整交易')
            print()
            
            # 計算統計
            if trades:
                wins = [t for t in trades if t['pnl_usdc'] > 0]
                losses = [t for t in trades if t['pnl_usdc'] <= 0]
                total_pnl = sum(t['pnl_usdc'] for t in trades)
                
                summary = {
                    'sync_time': datetime.now().isoformat(),
                    'address': address,
                    'date_filter': date_filter,
                    'total_fills': len(fills),
                    'total_trades': len(trades),
                    'wins': len(wins),
                    'losses': len(losses),
                    'win_rate': len(wins) / len(trades) * 100 if trades else 0,
                    'total_pnl_usdc': total_pnl,
                    'avg_pnl_usdc': total_pnl / len(trades) if trades else 0,
                    'avg_win': sum(t['pnl_usdc'] for t in wins) / len(wins) if wins else 0,
                    'avg_loss': sum(t['pnl_usdc'] for t in losses) / len(losses) if losses else 0,
                    'trades': trades
                }
                
                # 輸出統計
                print('=' * 50)
                print('📊 交易統計')
                print('=' * 50)
                print(f"  總交易數: {summary['total_trades']}")
                print(f"  獲利: {summary['wins']} ({summary['win_rate']:.1f}%)")
                print(f"  虧損: {summary['losses']}")
                print(f"  總 PnL: ${summary['total_pnl_usdc']:.4f} USDC")
                print(f"  平均獲利: ${summary['avg_win']:.4f}")
                print(f"  平均虧損: ${summary['avg_loss']:.4f}")
                print()
                
                # 顯示最近交易
                print('📝 最近 10 筆交易:')
                for i, t in enumerate(trades[-10:]):
                    emoji = '✅' if t['pnl_usdc'] > 0 else '❌'
                    print(f"  {i+1}. {emoji} {t['direction']} | ${t['entry_price']:.0f} → ${t['exit_price']:.0f}")
                    print(f"      PnL: ${t['pnl_usdc']:.4f} ({t['pnl_pct']:.3f}%) | 持倉: {t['hold_seconds']:.1f}s")
                print()
                
                # 保存到檔案
                output_dir = Path(__file__).parent.parent / 'logs' / 'dydx_real_trades'
                output_dir.mkdir(parents=True, exist_ok=True)
                
                if output_file:
                    output_path = Path(output_file)
                elif date_filter:
                    output_path = output_dir / f"trades_{date_filter.replace('-', '')}.json"
                else:
                    output_path = output_dir / f"trades_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                
                with open(output_path, 'w') as f:
                    json.dump(summary, f, indent=2, ensure_ascii=False)
                
                print(f'💾 已保存到: {output_path}')
                
                # 按日期分組輸出
                daily_stats = {}
                for t in trades:
                    date = t['exit_time'][:10]
                    if date not in daily_stats:
                        daily_stats[date] = {'count': 0, 'pnl': 0, 'wins': 0}
                    daily_stats[date]['count'] += 1
                    daily_stats[date]['pnl'] += t['pnl_usdc']
                    if t['pnl_usdc'] > 0:
                        daily_stats[date]['wins'] += 1
                
                print()
                print('📅 每日統計:')
                for date in sorted(daily_stats.keys()):
                    d = daily_stats[date]
                    wr = d['wins'] / d['count'] * 100 if d['count'] > 0 else 0
                    print(f"  {date}: ${d['pnl']:.2f} | {d['count']} trades | WR: {wr:.1f}%")
                
                return summary
            
            return {}


def main():
    parser = argparse.ArgumentParser(description='同步 dYdX 真實交易記錄到 JSON')
    parser.add_argument('--address', '-a', type=str, help='dYdX 錢包地址')
    parser.add_argument('--limit', '-l', type=int, default=1000, help='獲取的成交記錄數量 (預設: 1000)')
    parser.add_argument('--date', '-d', type=str, help='只處理特定日期 (格式: YYYY-MM-DD)')
    parser.add_argument('--output', '-o', type=str, help='指定輸出檔案路徑')
    
    args = parser.parse_args()
    asyncio.run(sync_dydx_trades(
        address=args.address,
        limit=args.limit,
        date_filter=args.date,
        output_file=args.output
    ))


if __name__ == '__main__':
    main()
