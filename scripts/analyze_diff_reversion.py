#!/usr/bin/env python3
"""Analyze cross-venue diff and mean-reversion opportunity (Binance vs dYdX).

This script is *analysis-only*. It does NOT place orders.

What it does:
- Fetch dYdX 1m candles (BTC-USD perpetual) from Indexer.
- Fetch Binance Futures 1m klines (BTCUSDT) from REST.
- Align by minute (UTC) and compute spread (dYdX - Binance) / Binance.
- Detect "diff events" where |spread| crosses above an entry threshold.
- Simulate a dollar-neutral hedge:
    - If dYdX is higher (spread > 0): SHORT dYdX + LONG Binance
    - If dYdX is lower (spread < 0): LONG dYdX + SHORT Binance
  Exit when |spread| falls back below an exit threshold, or at max-hold.

Notes / limitations:
- Uses 1m candle closes, not orderbook VWAP. Real "HALT diff" may be driven by
  expected_fill (VWAP) and can be much larger than close-to-close spread.
- Ignores funding, slippage, and execution latency. You can approximate fees.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

# Allow running as a standalone script from repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.backtest_realtime_24h import fetch_dydx_candles, process_candles  # noqa: E402


BINANCE_FAPI_KLINES = "https://fapi.binance.com/fapi/v1/klines"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze Binance vs dYdX diff reversion (1m candles)")
    p.add_argument("--hours", type=int, default=48, help="Lookback hours (dYdX window in UTC)")
    p.add_argument("--symbol", type=str, default="BTCUSDT", help="Binance Futures symbol")
    p.add_argument(
        "--entry", type=float, default=0.30, help="Entry threshold in percent (abs spread >= entry)"
    )
    p.add_argument(
        "--exit", type=float, default=0.10, help="Exit threshold in percent (abs spread <= exit)"
    )
    p.add_argument("--max-hold-min", type=int, default=60, help="Max hold in minutes")
    p.add_argument(
        "--fees-bps",
        type=float,
        default=8.0,
        help="Approx total fees (both venues, entry+exit) in basis points. 8bps = 0.08%.",
    )
    p.add_argument("--min-gap-min", type=int, default=5, help="Min minutes between events")
    p.add_argument("--show", type=int, default=10, help="How many events to print")
    return p.parse_args()


def to_minute(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


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
        r = requests.get(BINANCE_FAPI_KLINES, params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break

        for k in rows:
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


@dataclass
class DiffEvent:
    entry_time: datetime
    exit_time: datetime
    entry_spread_pct: float
    exit_spread_pct: float
    side: str  # "SHORT_DYDX_LONG_BINANCE" or "LONG_DYDX_SHORT_BINANCE"
    hold_min: int
    pnl_pct: float
    exit_reason: str  # "REVERT" or "TIMEOUT" or "NO_DATA"


def _leg_pnl_pct(is_long: bool, entry_px: float, exit_px: float) -> float:
    if entry_px <= 0 or exit_px <= 0:
        return 0.0
    if is_long:
        return (exit_px - entry_px) / entry_px * 100
    return (entry_px - exit_px) / entry_px * 100


def simulate_reversion_events(
    df: pd.DataFrame,
    entry_abs_pct: float,
    exit_abs_pct: float,
    max_hold_min: int,
    fees_bps_total: float,
    min_gap_min: int,
) -> Tuple[List[DiffEvent], Dict[str, float]]:
    """Detect entry events and simulate hedge PnL using close-to-close prices."""

    entry_abs = abs(entry_abs_pct)
    exit_abs = abs(exit_abs_pct)
    fees_pct = fees_bps_total / 100.0  # bps -> %

    # Identify crossings: abs(spread) goes from < entry_abs to >= entry_abs
    spread = df["spread_pct"].to_numpy()
    times = df["time"].to_list()
    bpx = df["binance_close"].to_numpy()
    dpx = df["dydx_close"].to_numpy()

    events: List[DiffEvent] = []
    last_entry_idx: Optional[int] = None

    for i in range(1, len(df)):
        if last_entry_idx is not None and (i - last_entry_idx) < min_gap_min:
            continue

        prev_abs = abs(spread[i - 1])
        cur_abs = abs(spread[i])
        if prev_abs < entry_abs and cur_abs >= entry_abs:
            entry_idx = i
            entry_time = times[entry_idx]

            entry_spread = float(spread[entry_idx])
            if entry_spread > 0:
                side = "SHORT_DYDX_LONG_BINANCE"
                long_binance = True
                long_dydx = False
            else:
                side = "LONG_DYDX_SHORT_BINANCE"
                long_binance = False
                long_dydx = True

            entry_bin = float(bpx[entry_idx])
            entry_dydx = float(dpx[entry_idx])

            # Search for exit
            exit_idx: Optional[int] = None
            exit_reason = "TIMEOUT"
            last_idx = min(entry_idx + max_hold_min, len(df) - 1)

            for j in range(entry_idx + 1, last_idx + 1):
                if abs(spread[j]) <= exit_abs:
                    exit_idx = j
                    exit_reason = "REVERT"
                    break

            if exit_idx is None:
                exit_idx = last_idx

            exit_time = times[exit_idx]
            exit_spread = float(spread[exit_idx])

            exit_bin = float(bpx[exit_idx])
            exit_dydx = float(dpx[exit_idx])

            # Hedge PnL = long_leg + short_leg - fees
            pnl = _leg_pnl_pct(long_binance, entry_bin, exit_bin) + _leg_pnl_pct(long_dydx, entry_dydx, exit_dydx)
            pnl -= fees_pct

            hold_min = int((exit_time - entry_time).total_seconds() // 60)

            events.append(
                DiffEvent(
                    entry_time=entry_time,
                    exit_time=exit_time,
                    entry_spread_pct=entry_spread,
                    exit_spread_pct=exit_spread,
                    side=side,
                    hold_min=hold_min,
                    pnl_pct=float(pnl),
                    exit_reason=exit_reason,
                )
            )
            last_entry_idx = entry_idx

    # Summary
    if events:
        reverted = sum(1 for e in events if e.exit_reason == "REVERT")
        avg_pnl = sum(e.pnl_pct for e in events) / len(events)
        win = sum(1 for e in events if e.pnl_pct > 0)
        stats = {
            "count": float(len(events)),
            "revert_rate": reverted / len(events) * 100.0,
            "win_rate": win / len(events) * 100.0,
            "avg_pnl": avg_pnl,
            "median_hold_min": float(pd.Series([e.hold_min for e in events]).median()),
        }
    else:
        stats = {"count": 0.0, "revert_rate": 0.0, "win_rate": 0.0, "avg_pnl": 0.0, "median_hold_min": 0.0}

    return events, stats


async def main() -> int:
    args = _parse_args()

    candles = await fetch_dydx_candles(args.hours)
    if len(candles) < 200:
        print("❌ dYdX candles 不足，無法分析")
        return 2

    dydx_df = process_candles(candles)
    # Use close and time only
    start_utc = to_minute(dydx_df["time"].min().to_pydatetime())
    end_utc = to_minute(dydx_df["time"].max().to_pydatetime())

    start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(end_utc.timestamp() * 1000)

    try:
        binance_close = fetch_binance_1m_klines(args.symbol, start_ms, end_ms)
    except Exception as e:
        print(f"❌ Binance 1m K 線抓取失敗: {e}")
        return 3

    # Build aligned dataframe (inner join by minute)
    rows = []
    for _, r in dydx_df.iterrows():
        t = to_minute(r["time"].to_pydatetime())
        b = binance_close.get(t)
        if b is None:
            continue
        d = float(r["close"])
        spread_pct = (d - b) / b * 100.0 if b > 0 else 0.0
        rows.append((t, float(b), float(d), float(spread_pct)))

    if len(rows) < 200:
        print("❌ 對齊後資料不足（Binance 與 dYdX 交集太少）")
        return 4

    df = pd.DataFrame(rows, columns=["time", "binance_close", "dydx_close", "spread_pct"]).sort_values("time")

    # Basic spread stats
    max_abs = float(df["spread_pct"].abs().max())
    p99 = float(df["spread_pct"].abs().quantile(0.99))
    p95 = float(df["spread_pct"].abs().quantile(0.95))

    events, stats = simulate_reversion_events(
        df,
        entry_abs_pct=float(args.entry),
        exit_abs_pct=float(args.exit),
        max_hold_min=int(args.max_hold_min),
        fees_bps_total=float(args.fees_bps),
        min_gap_min=int(args.min_gap_min),
    )

    print("=" * 88)
    print(f"📌 分析區間(UTC): {df['time'].iloc[0]} → {df['time'].iloc[-1]} | 交集分鐘數: {len(df)}")
    print(f"📌 價差定義: spread = (dYdX_close - Binance_close) / Binance_close * 100%")
    print(f"📈 |spread| 分位數: p95={p95:.3f}% p99={p99:.3f}% max={max_abs:.3f}%")
    print("-")
    print(
        f"⚙️ 事件偵測: entry≥{args.entry:.3f}% | exit≤{args.exit:.3f}% | max_hold={args.max_hold_min}m | fees≈{args.fees_bps:.1f}bps"
    )
    print(
        f"🧪 結果: events={int(stats['count'])} | revert_rate={stats['revert_rate']:.1f}% | win_rate={stats['win_rate']:.1f}% | avg_pnl={stats['avg_pnl']:+.3f}% | median_hold={stats['median_hold_min']:.0f}m"
    )
    print("=" * 88)

    if not events:
        print("❌ 在此門檻下沒有偵測到價差事件；可嘗試調低 --entry 或拉長 --hours")
        return 0

    # Show sample events
    show_n = max(0, int(args.show))
    print(f"\n📋 範例事件 (前 {min(show_n, len(events))} 筆):")
    for e in events[:show_n]:
        direction = "dYdX>Binance" if e.entry_spread_pct > 0 else "dYdX<Binance"
        print(
            f"- {e.entry_time} | {direction} spread={e.entry_spread_pct:+.3f}% → {e.exit_spread_pct:+.3f}% | {e.side} | hold={e.hold_min}m | pnl={e.pnl_pct:+.3f}% | {e.exit_reason}"
        )

    # Best / worst
    best = sorted(events, key=lambda x: x.pnl_pct, reverse=True)[:5]
    worst = sorted(events, key=lambda x: x.pnl_pct)[:5]

    print("\n🏆 最佳 5 筆(依 pnl):")
    for e in best:
        print(f"- {e.entry_time} pnl={e.pnl_pct:+.3f}% hold={e.hold_min}m spread={e.entry_spread_pct:+.3f}%→{e.exit_spread_pct:+.3f}% {e.exit_reason}")

    print("\n🧨 最差 5 筆(依 pnl):")
    for e in worst:
        print(f"- {e.entry_time} pnl={e.pnl_pct:+.3f}% hold={e.hold_min}m spread={e.entry_spread_pct:+.3f}%→{e.exit_spread_pct:+.3f}% {e.exit_reason}")

    # Practical guidance (short)
    print("\n🔎 解讀：")
    print("- 若你看到系統 HALT 的 diff 遠大於這裡的 |spread|，多半是 expected_fill/VWAP 滑價造成，而不是純 mid/close 價差。")
    print("- 這份報告只回答：用 1m close 的價差做『對沖收斂』，歷史上是否常在指定時間內回到小價差。")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
