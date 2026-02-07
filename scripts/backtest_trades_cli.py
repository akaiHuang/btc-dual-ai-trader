#!/usr/bin/env python3
"""
簡易終端回測工具
----------------
從 logs/whale_paper_trader/trades_*.json 讀取交易紀錄，
統計最近 N 小時的勝率、盈虧與分佈。

用法：
    python scripts/backtest_trades_cli.py --hours 24
    python scripts/backtest_trades_cli.py --hours 6 --folder logs/whale_paper_trader
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any


def load_trades(folder: Path) -> List[Dict[str, Any]]:
    """讀取指定資料夾下的所有 trades_*.json，合併為列表"""
    trades: List[Dict[str, Any]] = []
    for file in sorted(folder.glob("trades_*.json")):
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 檔案格式可能是列表或字典包裹
                if isinstance(data, list):
                    trades.extend(data)
                elif isinstance(data, dict):
                    trades.extend(data.get("trades", []))
        except Exception as e:
            print(f"⚠️ 無法讀取 {file}: {e}", file=sys.stderr)
    return trades


def parse_time(ts: str) -> datetime:
    """解析 ISO 格式時間字串"""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return datetime.min


def filter_recent(trades: List[Dict[str, Any]], hours: float) -> List[Dict[str, Any]]:
    """過濾最近 N 小時內的交易"""
    cutoff = datetime.now() - timedelta(hours=hours)
    recent = []
    for t in trades:
        exit_time = parse_time(t.get("exit_time") or t.get("timestamp") or "")
        entry_time = parse_time(t.get("entry_time") or "")
        ref_time = exit_time if exit_time != datetime.min else entry_time
        if ref_time >= cutoff:
            recent.append(t)
    return recent


def summarize(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    wins = 0
    losses = 0
    pnl_list = []
    best = None
    worst = None

    for t in trades:
        pnl = float(t.get("net_pnl_usdt", t.get("net_pnl", 0)) or 0)
        pnl_list.append(pnl)
        best = pnl if best is None else max(best, pnl)
        worst = pnl if worst is None else min(worst, pnl)
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1

    total = len(trades)
    win_rate = (wins / total * 100) if total else 0.0
    avg_pnl = (sum(pnl_list) / total) if total else 0.0

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "best": best if best is not None else 0.0,
        "worst": worst if worst is not None else 0.0,
        "sum_pnl": sum(pnl_list),
    }


def main():
    parser = argparse.ArgumentParser(description="簡易交易回測統計（讀取 trades_*.json）")
    parser.add_argument("--hours", type=float, default=24, help="回測最近 N 小時，預設 24")
    parser.add_argument(
        "--folder",
        type=str,
        default="logs/whale_paper_trader",
        help="交易紀錄資料夾，預設 logs/whale_paper_trader",
    )
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        print(f"❌ 找不到資料夾: {folder}", file=sys.stderr)
        sys.exit(1)

    all_trades = load_trades(folder)
    recent = filter_recent(all_trades, args.hours)
    summary = summarize(recent)

    print("=" * 60)
    print(f"📊 回測統計 | 資料夾: {folder} | 最近 {args.hours} 小時")
    print("=" * 60)
    print(f"   總筆數: {summary['total']}  | 勝: {summary['wins']}  敗: {summary['losses']}")
    print(f"   勝率: {summary['win_rate']:.2f}%")
    print(f"   總盈虧: {summary['sum_pnl']:+.2f} USDT")
    print(f"   平均盈虧/筆: {summary['avg_pnl']:+.2f} USDT")
    print(f"   最佳: {summary['best']:+.2f}  | 最差: {summary['worst']:+.2f}")
    print("=" * 60)
    if not recent:
        print("⚠️ 最近區間內沒有交易紀錄")


if __name__ == "__main__":
    main()
