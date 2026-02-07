#!/usr/bin/env python3
"""Backtest 7-day (or N-day) data to verify the theory.

Theory you asked to validate (simple, reproducible):
1) Cross-venue close-to-close spread is usually small; reversion exists but is often eaten by costs.
2) Using Binance as a lead signal can improve dYdX directional entries:
   - short: Binance leads down + dYdX premium
   - long:  Binance leads up   + dYdX discount

This script reuses the existing analysis logic in:
- scripts/analyze_diff_reversion.py
- scripts/analyze_binance_lead_dydx_follow.py

It fetches dYdX 1m candles once, fetches Binance 1m klines for the same window,
aligns by minute, and prints summary stats.

Limitations:
- Uses 1m candle closes (REST), not orderbook/VWAP. Real HALT diff can be much larger.
- Ignores funding and execution latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

# Allow running as a standalone script from repo root (or elsewhere).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analyze_binance_lead_dydx_follow import (
    EventResult,
    build_aligned_df,
    fetch_binance_1m_klines,
    find_events,
    summarize,
    to_minute,
)
from scripts.analyze_diff_reversion import simulate_reversion_events
from scripts.backtest_realtime_24h import fetch_dydx_candles, process_candles


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backtest N days to verify spread + Binance-lead theory")
    p.add_argument("--days", type=int, default=7, help="Lookback days (default 7)")
    p.add_argument("--symbol", type=str, default="BTCUSDT", help="Binance Futures symbol")

    # Spread reversion
    p.add_argument("--revert-entry", type=float, default=0.08, help="Reversion entry abs spread (percent)")
    p.add_argument("--revert-exit", type=float, default=0.03, help="Reversion exit abs spread (percent)")
    p.add_argument("--revert-max-hold", type=int, default=60, help="Reversion max hold minutes")
    p.add_argument(
        "--revert-fees-bps",
        type=float,
        default=8.0,
        help="Approx total fees (both venues, entry+exit) for hedge, in bps",
    )
    p.add_argument("--revert-min-gap", type=int, default=5, help="Min gap minutes between reversion events")

    # Binance-lead directional (dYdX-only)
    p.add_argument("--mode", type=str, default="both", choices=["short", "long", "both"], help="Directional modes")
    p.add_argument("--spread-entry", type=float, default=0.08, help="Directional spread magnitude (percent)")
    p.add_argument("--binance-window", type=int, default=5, help="Lead window minutes")
    p.add_argument("--binance-move-pct", type=float, default=0.15, help="Lead move magnitude (percent)")
    p.add_argument("--cooldown-min", type=int, default=10, help="Min minutes between directional entries")
    p.add_argument("--horizon-min", type=int, default=15, help="Directional horizon minutes")
    p.add_argument("--tp", type=float, default=0.40, help="Directional TP percent")
    p.add_argument("--sl", type=float, default=0.60, help="Directional SL percent")
    p.add_argument("--dir-fees-bps", type=float, default=8.0, help="Directional total fees (entry+exit) in bps")

    # Output
    p.add_argument("--out", type=str, default="", help="Optional output JSON path")
    return p.parse_args()


def _spread_distribution(df: pd.DataFrame) -> Dict[str, float]:
    s = df["spread_pct"].astype(float)
    return {
        "minutes": float(len(df)),
        "min": float(s.min()),
        "p50": float(s.quantile(0.50)),
        "p95": float(s.quantile(0.95)),
        "p99": float(s.quantile(0.99)),
        "max": float(s.max()),
    }


def _events_to_dict(events: List[EventResult]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in events:
        d = asdict(e)
        d["entry_time"] = e.entry_time.isoformat()
        d["exit_time"] = e.exit_time.isoformat()
        out.append(d)
    return out


async def main() -> int:
    args = _parse_args()
    hours = int(max(1, args.days) * 24)

    candles = await fetch_dydx_candles(hours)
    if len(candles) < 200:
        print("❌ dYdX candles 不足")
        return 2

    dydx_df = process_candles(candles)
    start_utc = to_minute(dydx_df["time"].min().to_pydatetime())
    end_utc = to_minute(dydx_df["time"].max().to_pydatetime())

    start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(end_utc.timestamp() * 1000)

    try:
        binance_close = fetch_binance_1m_klines(args.symbol, start_ms, end_ms)
    except Exception as e:
        print(f"❌ Binance 1m K 線抓取失敗: {e}")
        return 3

    df = build_aligned_df(dydx_df, binance_close)
    if len(df) < 500:
        print("❌ 對齊後資料不足（交集太少）")
        return 4

    print("=" * 96)
    print(f"📌 區間(UTC): {df['time'].iloc[0]} → {df['time'].iloc[-1]} | minutes={len(df)}")

    # Spread distribution
    dist = _spread_distribution(df)
    print("📊 spread 分布（%）: " + ", ".join([f"{k}={v:+.3f}" if k != "minutes" else f"minutes={int(v)}" for k, v in dist.items()]))

    # Reversion backtest
    reversion_events, rev_stats = simulate_reversion_events(
        df,
        entry_abs_pct=float(args.revert_entry),
        exit_abs_pct=float(args.revert_exit),
        max_hold_min=int(args.revert_max_hold),
        fees_bps_total=float(args.revert_fees_bps),
        min_gap_min=int(args.revert_min_gap),
    )

    print("-")
    print(
        "🧪 回歸(中性概念) : entry=±{:.3f}% exit=±{:.3f}% max_hold={}m fees≈{:.1f}bps | n={} revert_rate={:.1f}% win_rate={:.1f}% avg_pnl={:+.3f}% median_hold={}m".format(
            args.revert_entry,
            args.revert_exit,
            args.revert_max_hold,
            args.revert_fees_bps,
            int(rev_stats.get("count", 0.0)),
            float(rev_stats.get("revert_rate", 0.0)),
            float(rev_stats.get("win_rate", 0.0)),
            float(rev_stats.get("avg_pnl", 0.0)),
            int(rev_stats.get("median_hold_min", 0.0)),
        )
    )

    # Directional lead-follow
    modes = ["short", "long"] if args.mode == "both" else [str(args.mode)]
    dir_results: Dict[str, Any] = {}

    for mode in modes:
        events = find_events(
            df,
            spread_entry_pct=float(args.spread_entry),
            binance_drop_window=int(args.binance_window),
            binance_drop_pct=float(args.binance_move_pct),
            cooldown_min=int(args.cooldown_min),
            horizon_min=int(args.horizon_min),
            tp_pct=float(args.tp),
            sl_pct=float(args.sl),
            fees_bps=float(args.dir_fees_bps),
            mode=mode,
        )
        stats = summarize(events)

        label = "dYdX 做空" if mode == "short" else "dYdX 做多"
        print("-")
        if mode == "short":
            cond = f"spread>=+{args.spread_entry:.3f}% 且 Binance {args.binance_window}m<=-{args.binance_move_pct:.3f}%"
        else:
            cond = f"spread<=-{args.spread_entry:.3f}% 且 Binance {args.binance_window}m>=+{args.binance_move_pct:.3f}%"

        print(
            "🧪 Lead/Follow({}) : {} | TP={:.3f}% SL={:.3f}% horizon={}m fees≈{:.1f}bps | n={} win_rate={:.1f}% avg_pnl={:+.3f}% TP%={:.1f}% SL%={:.1f}%".format(
                label,
                cond,
                args.tp,
                args.sl,
                args.horizon_min,
                args.dir_fees_bps,
                int(stats.get("count", 0.0)),
                float(stats.get("win_rate", 0.0)),
                float(stats.get("avg_pnl", 0.0)),
                float(stats.get("tp_rate", 0.0)),
                float(stats.get("sl_rate", 0.0)),
            )
        )

        dir_results[mode] = {
            "stats": stats,
            "events": _events_to_dict(events[:200]),  # keep it small if saved
        }

    print("=" * 96)

    if args.out:
        payload = {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "days": int(args.days),
            "symbol": str(args.symbol),
            "window": {"start_utc": start_utc.isoformat(), "end_utc": end_utc.isoformat()},
            "spread_distribution": dist,
            "reversion": {"stats": rev_stats, "count": int(rev_stats.get("count", 0.0))},
            "directional": dir_results,
        }
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"🧾 已輸出 JSON: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
