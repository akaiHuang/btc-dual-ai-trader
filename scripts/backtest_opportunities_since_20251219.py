#!/usr/bin/env python3
"""Backtest opportunities since local 2025-12-19.

Purpose:
- Use dYdX Indexer REST candles (1m) to run the repo's simulated six-dim strategy.
- Use Binance Futures REST klines (1m) to compute cross-venue diff at entry.
- Report trades whose entry time (Asia/Taipei) falls on 2025-12-19.

Note:
- This is an analysis script; it does not place orders.
- WS is not used because this is historical/backtest.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Dict, Optional

import requests

# Allow running as a standalone script from repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.backtest_realtime_24h import (  # noqa: E402
    BacktestConfig,
    fetch_dydx_candles,
    process_candles,
    simulate_strategy,
)


BINANCE_FAPI_KLINES = "https://fapi.binance.com/fapi/v1/klines"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backtest 2025-12-19 opportunities (dYdX + Binance)")
    p.add_argument("--hours", type=int, default=12, help="Lookback hours fetched from dYdX (UTC-based)")
    p.add_argument("--tp", type=float, default=0.99, help="Take-profit percent (e.g., 0.99)")
    p.add_argument("--sl", type=float, default=2.0, help="Initial stop-loss percent (e.g., 2.0)")
    p.add_argument("--symbol", type=str, default="BTCUSDT", help="Binance symbol")
    p.add_argument("--local-date", type=str, default="2025-12-19", help="Local date in Asia/Taipei")
    return p.parse_args()


def fetch_binance_1m_klines(symbol: str, start_ms: int, end_ms: int) -> Dict[datetime, float]:
    """Fetch Binance Futures 1m close prices, keyed by candle start time (UTC, minute precision)."""
    out: Dict[datetime, float] = {}
    limit = 1500
    cur = start_ms

    while cur <= end_ms:
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": cur,
            "endTime": end_ms,
            "limit": limit,
        }
        r = requests.get(BINANCE_FAPI_KLINES, params=params, timeout=10)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break

        for k in rows:
            # [open_time, open, high, low, close, volume, close_time, ...]
            t = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
            out[t] = float(k[4])

        last_open = rows[-1][0]
        next_open = last_open + 60_000
        if next_open <= cur:
            break
        cur = next_open

        if len(rows) < limit:
            break

    return out


def to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


async def main() -> int:
    args = _parse_args()

    local_tz = ZoneInfo("Asia/Taipei")
    target_local_date = datetime.fromisoformat(args.local_date).date()

    candles = await fetch_dydx_candles(args.hours)
    if len(candles) < 100:
        print("❌ dYdX candles 不足，無法回測")
        return 2

    df = process_candles(candles)

    config = replace(
        BacktestConfig(),
        take_profit_pct=float(args.tp),
        initial_stop_loss_pct=float(args.sl),
    )

    trades = simulate_strategy(df, config)

    if not trades:
        print("❌ 回測無交易")
        return 0

    # Build Binance 1m close map over the same candle window
    start_utc = df["time"].min().to_pydatetime()
    end_utc = df["time"].max().to_pydatetime()
    start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(end_utc.timestamp() * 1000)

    try:
        binance_close = fetch_binance_1m_klines(args.symbol, start_ms, end_ms)
    except Exception as e:
        print(f"⚠️ Binance 1m K 線抓取失敗: {e}")
        binance_close = {}

    # Filter to local date entries
    filtered = []
    for t in trades:
        entry_utc = t.entry_time
        if entry_utc.tzinfo is None:
            entry_utc = entry_utc.replace(tzinfo=timezone.utc)
        entry_local = entry_utc.astimezone(local_tz)
        if entry_local.date() != target_local_date:
            continue

        m = to_minute(entry_utc.astimezone(timezone.utc))
        b = binance_close.get(m)
        diff_pct: Optional[float] = None
        if b and b > 0:
            diff_pct = abs(t.entry_price - b) / b * 100

        filtered.append((entry_local, t, diff_pct))

    if not filtered:
        print(f"❌ 在 Asia/Taipei 的 {args.local_date} 沒有產生交易（可能需要調大 --hours 覆蓋到該日）")
        print(f"   目前 dYdX 資料範圍(UTC): {start_utc} → {end_utc}")
        return 0

    filtered.sort(key=lambda x: x[0])

    print("=" * 80)
    print(f"📌 回測區間(dYdX, UTC): {start_utc} → {end_utc}")
    print(f"📌 篩選進場(Asia/Taipei): {args.local_date}")
    print(f"⚙️ TP: +{args.tp:.2f}% | SL: -{args.sl:.2f}% | 六維(模擬)門檻: {config.six_dim_alignment_threshold}/12 | min_score: {config.six_dim_min_score_to_trade}/12")
    print("=" * 80)

    win = 0
    total_pnl = 0.0

    for entry_local, tr, diff_pct in filtered:
        total_pnl += tr.pnl_pct
        if tr.pnl_pct > 0:
            win += 1

        exit_local = tr.exit_time.astimezone(local_tz) if tr.exit_time else None
        dur_min = None
        if tr.exit_time:
            dur_min = (tr.exit_time - tr.entry_time).total_seconds() / 60

        diff_str = "N/A" if diff_pct is None else f"{diff_pct:.3f}%"
        dur_str = "N/A" if dur_min is None else f"{dur_min:.1f}m"

        print(
            f"- {entry_local.strftime('%H:%M')} {tr.side:<5} entry=${tr.entry_price:,.0f} | "
            f"pnl={tr.pnl_pct:+.2f}% | dur={dur_str} | diff(Binance)={diff_str} | {tr.exit_reason}"
        )

    print("-" * 80)
    print(f"總筆數: {len(filtered)} | 勝率: {win/len(filtered)*100:.1f}% | 總盈虧: {total_pnl:+.2f}%")
    print("=" * 80)

    # Highlight best opportunities (highest pnl)
    top = sorted(filtered, key=lambda x: x[1].pnl_pct, reverse=True)[:5]
    print("\n🏆 12/19 最佳 5 次進場 (依 pnl 排序)")
    for entry_local, tr, diff_pct in top:
        diff_str = "N/A" if diff_pct is None else f"{diff_pct:.3f}%"
        print(f"- {entry_local.strftime('%H:%M')} {tr.side:<5} pnl={tr.pnl_pct:+.2f}% diff={diff_str} entry=${tr.entry_price:,.0f} {tr.exit_reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
