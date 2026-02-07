#!/usr/bin/env python3
"""
dYdX 交易日誌分析工具
=====================
分析 dydx_order_journal.jsonl 中的交易事件，
生成統計報告並檢測潛在問題。

使用方式:
    python scripts/analyze_dydx_journal.py                    # 分析所有記錄
    python scripts/analyze_dydx_journal.py --date 2025-12-22  # 只分析特定日期
    python scripts/analyze_dydx_journal.py --events           # 顯示所有事件類型
    python scripts/analyze_dydx_journal.py --trades           # 只顯示完整交易
    python scripts/analyze_dydx_journal.py --errors           # 只顯示錯誤/異常
"""

import json
import argparse
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Optional


JOURNAL_PATH = Path("logs/dydx_order_journal.jsonl")


def load_journal(date_filter: str = None) -> List[Dict]:
    """載入 journal 記錄"""
    if not JOURNAL_PATH.exists():
        print(f"❌ 找不到 journal 檔案: {JOURNAL_PATH}")
        return []
    
    events = []
    with open(JOURNAL_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                # 日期過濾
                if date_filter:
                    ts = event.get('ts', '')
                    if not ts.startswith(date_filter):
                        continue
                events.append(event)
            except json.JSONDecodeError:
                continue
    
    return events


def analyze_events(events: List[Dict]) -> Dict:
    """分析事件統計"""
    stats = {
        'total_events': len(events),
        'event_types': defaultdict(int),
        'runs': defaultdict(list),
        'trades_opened': 0,
        'trades_closed': 0,
        'open_filled': 0,
        'close_filled': 0,
        'errors': [],
        'warnings': [],
    }
    
    for e in events:
        event_type = e.get('event', 'unknown')
        stats['event_types'][event_type] += 1
        
        # 按 run_id 分組
        run_id = e.get('run_id', 'unknown')
        stats['runs'][run_id].append(e)
        
        # 統計關鍵事件
        if event_type == 'open_filled':
            stats['open_filled'] += 1
        elif event_type == 'close_filled':
            stats['close_filled'] += 1
        elif event_type == 'trade_closed':
            stats['trades_closed'] += 1
        elif 'error' in event_type or 'exception' in event_type or 'failed' in event_type:
            stats['errors'].append(e)
    
    return stats


def analyze_trades(events: List[Dict]) -> List[Dict]:
    """提取完整的交易記錄 (從 trade_closed 事件)"""
    trades = []
    for e in events:
        if e.get('event') == 'trade_closed':
            trades.append({
                'timestamp': e.get('ts'),
                'trade_id': e.get('trade_id'),
                'direction': e.get('direction'),
                'entry_price': e.get('entry_price'),
                'exit_price': e.get('exit_price'),
                'price_move_pct': e.get('price_move_pct'),
                'pnl_pct': e.get('pnl_pct'),
                'net_pnl_usdt': e.get('net_pnl_usdt'),
                'hold_seconds': e.get('hold_seconds'),
                'exit_reason': e.get('exit_reason'),
                'is_win': e.get('is_win'),
                'leverage': e.get('leverage'),
                'position_size_usdt': e.get('position_size_usdt'),
                # 進場時的市場狀態
                'entry_obi': e.get('entry_obi'),
                'entry_six_dim_long': e.get('entry_six_dim_long'),
                'entry_six_dim_short': e.get('entry_six_dim_short'),
            })
    return trades


def print_summary(stats: Dict, trades: List[Dict]):
    """打印統計摘要"""
    print("\n" + "=" * 60)
    print("📊 dYdX Journal 分析報告")
    print("=" * 60)
    
    print(f"\n📈 事件統計:")
    print(f"  總事件數: {stats['total_events']}")
    print(f"  運行會話數: {len(stats['runs'])}")
    print(f"  開倉成功 (open_filled): {stats['open_filled']}")
    print(f"  平倉成功 (close_filled): {stats['close_filled']}")
    print(f"  完整交易 (trade_closed): {stats['trades_closed']}")
    print(f"  錯誤/異常: {len(stats['errors'])}")
    
    print(f"\n📋 事件類型分布:")
    for event_type, count in sorted(stats['event_types'].items(), key=lambda x: -x[1]):
        print(f"  {event_type}: {count}")
    
    # 交易統計
    if trades:
        wins = [t for t in trades if t.get('is_win')]
        losses = [t for t in trades if not t.get('is_win')]
        total_pnl = sum(t.get('net_pnl_usdt', 0) or 0 for t in trades)
        
        print(f"\n💰 交易統計:")
        print(f"  總交易數: {len(trades)}")
        print(f"  獲利: {len(wins)} ({len(wins)/len(trades)*100:.1f}%)")
        print(f"  虧損: {len(losses)}")
        print(f"  總 PnL: ${total_pnl:.4f}")
        
        if wins:
            avg_win = sum(t.get('net_pnl_usdt', 0) or 0 for t in wins) / len(wins)
            print(f"  平均獲利: ${avg_win:.4f}")
        if losses:
            avg_loss = sum(t.get('net_pnl_usdt', 0) or 0 for t in losses) / len(losses)
            print(f"  平均虧損: ${avg_loss:.4f}")
        
        # 出場原因分析
        exit_reasons = defaultdict(int)
        for t in trades:
            reason = t.get('exit_reason', 'unknown')
            # 簡化原因 (取主要關鍵字)
            if '止損' in reason or 'STOP' in reason.upper() or 'SL' in reason:
                exit_reasons['止損'] += 1
            elif '止盈' in reason or 'TP' in reason or 'PROFIT' in reason.upper():
                exit_reasons['止盈'] += 1
            elif '鎖利' in reason or 'LOCK' in reason.upper():
                exit_reasons['鎖利'] += 1
            elif '超時' in reason or 'TIME' in reason.upper():
                exit_reasons['超時'] += 1
            else:
                exit_reasons['其他'] += 1
        
        print(f"\n📤 出場原因分析:")
        for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            pct = count / len(trades) * 100
            print(f"  {reason}: {count} ({pct:.1f}%)")
        
        # 方向分析
        long_trades = [t for t in trades if t.get('direction') == 'LONG']
        short_trades = [t for t in trades if t.get('direction') == 'SHORT']
        
        print(f"\n📊 方向分析:")
        if long_trades:
            long_wins = [t for t in long_trades if t.get('is_win')]
            long_pnl = sum(t.get('net_pnl_usdt', 0) or 0 for t in long_trades)
            print(f"  LONG: {len(long_trades)} 筆, WR: {len(long_wins)/len(long_trades)*100:.1f}%, PnL: ${long_pnl:.4f}")
        if short_trades:
            short_wins = [t for t in short_trades if t.get('is_win')]
            short_pnl = sum(t.get('net_pnl_usdt', 0) or 0 for t in short_trades)
            print(f"  SHORT: {len(short_trades)} 筆, WR: {len(short_wins)/len(short_trades)*100:.1f}%, PnL: ${short_pnl:.4f}")
    
    # 顯示最近錯誤
    if stats['errors']:
        print(f"\n⚠️ 最近錯誤 (最多 5 筆):")
        for e in stats['errors'][-5:]:
            ts = e.get('ts', '')[:19]
            event = e.get('event', '')
            error = e.get('error', e.get('reason', ''))[:50]
            print(f"  [{ts}] {event}: {error}")


def print_trades_detail(trades: List[Dict], limit: int = 20):
    """打印交易詳情"""
    print(f"\n📝 最近 {limit} 筆交易:")
    print("-" * 80)
    
    for i, t in enumerate(trades[-limit:]):
        ts = t.get('timestamp', '')[:19] if t.get('timestamp') else ''
        direction = t.get('direction', '?')
        emoji = '✅' if t.get('is_win') else '❌'
        pnl = t.get('net_pnl_usdt', 0) or 0
        pnl_pct = t.get('pnl_pct', 0) or 0
        entry = t.get('entry_price', 0) or 0
        exit_p = t.get('exit_price', 0) or 0
        hold = t.get('hold_seconds', 0) or 0
        reason = t.get('exit_reason', '')[:30]
        
        print(f"{i+1:2}. {emoji} {direction:5} | ${entry:,.0f} → ${exit_p:,.0f} | {pnl_pct:+.2f}% (${pnl:+.4f}) | {hold:.0f}s | {reason}")


def detect_issues(events: List[Dict], stats: Dict) -> List[str]:
    """檢測潛在問題"""
    issues = []
    
    # 檢查開平倉不對等
    if stats['open_filled'] != stats['close_filled']:
        diff = stats['open_filled'] - stats['close_filled']
        issues.append(f"⚠️ 開倉({stats['open_filled']}) vs 平倉({stats['close_filled']}) 不對等 (差異: {diff})")
    
    # 檢查是否有 trade_closed 但沒有 close_filled
    if stats['trades_closed'] > stats['close_filled']:
        issues.append(f"⚠️ trade_closed({stats['trades_closed']}) > close_filled({stats['close_filled']}): 可能有 Paper 平倉但 dYdX 未同步")
    
    # 檢查錯誤率
    error_rate = len(stats['errors']) / max(1, stats['total_events']) * 100
    if error_rate > 5:
        issues.append(f"⚠️ 錯誤率偏高: {error_rate:.1f}%")
    
    return issues


def main():
    parser = argparse.ArgumentParser(description='分析 dYdX 交易日誌')
    parser.add_argument('--date', '-d', type=str, help='只分析特定日期 (格式: YYYY-MM-DD)')
    parser.add_argument('--events', '-e', action='store_true', help='顯示所有事件類型統計')
    parser.add_argument('--trades', '-t', action='store_true', help='顯示交易詳情')
    parser.add_argument('--errors', action='store_true', help='只顯示錯誤記錄')
    parser.add_argument('--limit', '-l', type=int, default=20, help='顯示的交易數量')
    
    args = parser.parse_args()
    
    print(f"📂 讀取: {JOURNAL_PATH}")
    events = load_journal(args.date)
    
    if not events:
        print("⚠️ 沒有找到符合條件的事件")
        return
    
    print(f"📥 載入 {len(events)} 筆事件" + (f" (日期: {args.date})" if args.date else ""))
    
    # 分析
    stats = analyze_events(events)
    trades = analyze_trades(events)
    
    # 顯示錯誤
    if args.errors:
        print(f"\n⚠️ 錯誤記錄 ({len(stats['errors'])} 筆):")
        for e in stats['errors']:
            print(json.dumps(e, indent=2, ensure_ascii=False))
        return
    
    # 打印摘要
    print_summary(stats, trades)
    
    # 顯示交易詳情
    if args.trades and trades:
        print_trades_detail(trades, args.limit)
    
    # 問題檢測
    issues = detect_issues(events, stats)
    if issues:
        print(f"\n🔍 潛在問題檢測:")
        for issue in issues:
            print(f"  {issue}")
    
    print()


if __name__ == '__main__':
    main()
