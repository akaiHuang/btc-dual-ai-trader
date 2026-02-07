#!/usr/bin/env python3
"""Analyze when Binance leads and dYdX follows.

Goal (matches your hypothesis):
- Binance prints a clear bearish move first.
- dYdX is still priced higher (positive spread).
- Then dYdX "catches down" (spread compresses and/or dYdX drops).

This script finds those moments and evaluates whether a *dYdX-only short* (using
Binance as a signal) had an edge.

Important limitations:
- Uses 1-minute candle closes (REST), not orderbook/VWAP. Live HALT diffs can be
  much larger due to expected_fill (slippage) and WS desync.
- Ignores funding and execution latency. Optionally subtract fees.

Output:
- Summary stats (count/win-rate/avg pnl)
- Sample events with entry/exit info
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
    p = argparse.ArgumentParser(description="Find Binance-leads-dYdX-follows opportunities")
    p.add_argument("--hours", type=int, default=48, help="Lookback hours")
    p.add_argument("--symbol", type=str, default="BTCUSDT", help="Binance Futures symbol")

    p.add_argument(
        "--mode",
        type=str,
        default="short",
        choices=["short", "long"],
        help=(
            "Trade direction on dYdX. "
            "short: Binance leads down + dYdX premium => short dYdX. "
            "long:  Binance leads up   + dYdX discount => long dYdX."
        ),
    )

    # Trigger conditions
    p.add_argument(
        "--spread-entry",
        type=float,
        default=0.10,
        help=(
            "Spread magnitude threshold in percent. "
            "For mode=short: enter when spread >= +X (dYdX premium). "
            "For mode=long:  enter when spread <= -X (dYdX discount)."
        ),
    )
    p.add_argument(
        "--binance-drop-window",
        type=int,
        default=5,
        help="Binance bearish window in minutes (e.g., 5 = compare now vs 5m ago)",
    )
    p.add_argument(
        "--binance-drop-pct",
        type=float,
        default=0.15,
        help=(
            "Binance lead magnitude threshold in percent. "
            "For mode=short: require return <= -X over window. "
            "For mode=long:  require return >= +X over window."
        ),
    )

    # Exit / evaluation
    p.add_argument(
        "--horizon-min",
        type=int,
        default=15,
        help="Evaluate trade over this horizon (minutes) if TP/SL not hit",
    )
    p.add_argument("--tp", type=float, default=0.40, help="Take-profit percent for dYdX trade")
    p.add_argument("--sl", type=float, default=0.60, help="Stop-loss percent for dYdX trade")

    # Costs
    p.add_argument(
        "--fees-bps",
        type=float,
        default=8.0,
        help="Approx total fees (entry+exit) in basis points for dYdX-only trade",
    )

    # Sampling / filtering
    p.add_argument("--cooldown-min", type=int, default=10, help="Min minutes between entries")
    p.add_argument("--show", type=int, default=15, help="Print top N events")
    p.add_argument("--sort", type=str, default="pnl", choices=["pnl", "spread", "binance_drop"], help="Sort output")
    return p.parse_args()


def to_minute(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def fetch_binance_1m_klines(symbol: str, start_ms: int, end_ms: int) -> Dict[datetime, float]:
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
class EventResult:
    entry_time: datetime
    binance_entry: float
    dydx_entry: float
    spread_entry_pct: float
    binance_drop_pct: float

    exit_time: datetime
    dydx_exit: float
    pnl_pct: float
    exit_reason: str  # TP/SL/HORIZON

    mae_pct: float  # max adverse excursion vs short (worst drawdown)
    mfe_pct: float  # max favorable excursion vs short (best run)


def _short_pnl(entry: float, exit_: float) -> float:
    if entry <= 0 or exit_ <= 0:
        return 0.0
    return (entry - exit_) / entry * 100.0


def _long_pnl(entry: float, exit_: float) -> float:
    if entry <= 0 or exit_ <= 0:
        return 0.0
    return (exit_ - entry) / entry * 100.0


def simulate_dydx_trade(
    df: pd.DataFrame,
    entry_idx: int,
    horizon_min: int,
    tp_pct: float,
    sl_pct: float,
    fees_bps: float,
    mode: str,
) -> Tuple[int, str, float, float, float]:
    """Return (exit_idx, reason, pnl_pct_after_fees, mae_pct, mfe_pct)."""

    entry_px = float(df.loc[entry_idx, "dydx_close"])
    last_idx = min(entry_idx + horizon_min, len(df) - 1)

    mae = 0.0
    mfe = 0.0

    exit_idx = last_idx
    reason = "HORIZON"

    for j in range(entry_idx + 1, last_idx + 1):
        px = float(df.loc[j, "dydx_close"])
        if mode == "short":
            pnl = _short_pnl(entry_px, px)
        else:
            pnl = _long_pnl(entry_px, px)

        # adverse excursion is minimum pnl (more negative)
        mae = min(mae, pnl)
        # favorable excursion is maximum pnl
        mfe = max(mfe, pnl)

        if pnl >= tp_pct:
            exit_idx = j
            reason = "TP"
            break
        if pnl <= -abs(sl_pct):
            exit_idx = j
            reason = "SL"
            break

    exit_px = float(df.loc[exit_idx, "dydx_close"])
    if mode == "short":
        pnl_final = _short_pnl(entry_px, exit_px)
    else:
        pnl_final = _long_pnl(entry_px, exit_px)
    pnl_final -= fees_bps / 100.0  # bps -> %

    return exit_idx, reason, float(pnl_final), float(mae), float(mfe)


def build_aligned_df(dydx_df: pd.DataFrame, binance_close: Dict[datetime, float]) -> pd.DataFrame:
    rows = []
    for _, r in dydx_df.iterrows():
        t = to_minute(r["time"].to_pydatetime())
        b = binance_close.get(t)
        if b is None:
            continue
        d = float(r["close"])
        spread_pct = (d - b) / b * 100.0 if b > 0 else 0.0
        rows.append((t, float(b), float(d), float(spread_pct)))

    df = pd.DataFrame(rows, columns=["time", "binance_close", "dydx_close", "spread_pct"]).sort_values("time")
    df = df.reset_index(drop=True)

    # Binance returns for trend
    df["binance_ret_1m_pct"] = df["binance_close"].pct_change(1) * 100.0
    return df


def find_events(
    df: pd.DataFrame,
    spread_entry_pct: float,
    binance_drop_window: int,
    binance_drop_pct: float,
    cooldown_min: int,
    horizon_min: int,
    tp_pct: float,
    sl_pct: float,
    fees_bps: float,
    mode: str,
) -> List[EventResult]:
    events: List[EventResult] = []
    last_entry_idx: Optional[int] = None

    for i in range(binance_drop_window, len(df) - 1):
        if last_entry_idx is not None and (i - last_entry_idx) < cooldown_min:
            continue

        spread = float(df.loc[i, "spread_pct"])
        if mode == "short":
            # Need dYdX premium
            if spread < spread_entry_pct:
                continue
        else:
            # Need dYdX discount
            if spread > -abs(spread_entry_pct):
                continue

        past_px = float(df.loc[i - binance_drop_window, "binance_close"])
        cur_px = float(df.loc[i, "binance_close"])
        if past_px <= 0:
            continue
        binance_drop = (cur_px - past_px) / past_px * 100.0
        if mode == "short":
            if binance_drop > -abs(binance_drop_pct):
                continue
        else:
            if binance_drop < abs(binance_drop_pct):
                continue

        exit_idx, reason, pnl, mae, mfe = simulate_dydx_trade(
            df,
            entry_idx=i,
            horizon_min=horizon_min,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            fees_bps=fees_bps,
            mode=mode,
        )

        events.append(
            EventResult(
                entry_time=df.loc[i, "time"].to_pydatetime(),
                binance_entry=float(df.loc[i, "binance_close"]),
                dydx_entry=float(df.loc[i, "dydx_close"]),
                spread_entry_pct=spread,
                binance_drop_pct=float(binance_drop),
                exit_time=df.loc[exit_idx, "time"].to_pydatetime(),
                dydx_exit=float(df.loc[exit_idx, "dydx_close"]),
                pnl_pct=float(pnl),
                exit_reason=reason,
                mae_pct=float(mae),
                mfe_pct=float(mfe),
            )
        )
        last_entry_idx = i

    return events


def summarize(events: List[EventResult]) -> Dict[str, float]:
    if not events:
        return {"count": 0.0, "win_rate": 0.0, "avg_pnl": 0.0, "tp_rate": 0.0, "sl_rate": 0.0}

    win = sum(1 for e in events if e.pnl_pct > 0)
    tp = sum(1 for e in events if e.exit_reason == "TP")
    sl = sum(1 for e in events if e.exit_reason == "SL")
    avg_pnl = sum(e.pnl_pct for e in events) / len(events)

    return {
        "count": float(len(events)),
        "win_rate": win / len(events) * 100.0,
        "avg_pnl": float(avg_pnl),
        "tp_rate": tp / len(events) * 100.0,
        "sl_rate": sl / len(events) * 100.0,
    }


async def main() -> int:
    args = _parse_args()

    candles = await fetch_dydx_candles(args.hours)
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
    if len(df) < 200:
        print("❌ 對齊後資料不足（交集太少）")
        return 4

    mode = str(args.mode)
    events = find_events(
        df,
        spread_entry_pct=float(args.spread_entry),
        binance_drop_window=int(args.binance_drop_window),
        binance_drop_pct=float(args.binance_drop_pct),
        cooldown_min=int(args.cooldown_min),
        horizon_min=int(args.horizon_min),
        tp_pct=float(args.tp),
        sl_pct=float(args.sl),
        fees_bps=float(args.fees_bps),
        mode=mode,
    )

    stats = summarize(events)

    print("=" * 96)
    print(f"📌 區間(UTC): {df['time'].iloc[0]} → {df['time'].iloc[-1]} | minutes={len(df)}")
    print("📌 觸發條件(你說的情況)：")
    if mode == "short":
        print(
            f"- dYdX 溢價 spread >= {args.spread_entry:.3f}%"
            f"  (spread=(dYdX-Binance)/Binance*100)"
        )
        print(
            f"- Binance 先跌：{args.binance_drop_window}m 報酬 <= -{args.binance_drop_pct:.3f}%"
        )
        trade_label = "dYdX 做空"
    else:
        print(
            f"- dYdX 折價 spread <= -{args.spread_entry:.3f}%"
            f"  (spread=(dYdX-Binance)/Binance*100)"
        )
        print(
            f"- Binance 先漲：{args.binance_drop_window}m 報酬 >= +{args.binance_drop_pct:.3f}%"
        )
        trade_label = "dYdX 做多"

    print(
        "📌 交易評估：{}，TP={:.3f}% SL={:.3f}% horizon={}m fees≈{:.1f}bps".format(
            trade_label, args.tp, args.sl, args.horizon_min, args.fees_bps
        )
    )
    print("-")
    print(
        f"🧪 結果: n={int(stats['count'])} | win_rate={stats['win_rate']:.1f}% | avg_pnl={stats['avg_pnl']:+.3f}% | TP%={stats['tp_rate']:.1f}% | SL%={stats['sl_rate']:.1f}%"
    )
    print("=" * 96)

    if not events:
        # Help tuning thresholds
        max_spread = float(df["spread_pct"].max())
        min_spread = float(df["spread_pct"].min())
        binance_ret = df["binance_close"].pct_change(args.binance_drop_window) * 100.0
        min_binance_ret = float(binance_ret.min())
        max_binance_ret = float(binance_ret.max())
        print("❌ 沒找到符合條件的事件。")
        print(f"- 此區間 spread 範圍: min={min_spread:.3f}% max={max_spread:.3f}%")
        print(
            f"- Binance {args.binance_drop_window}m 報酬範圍: min={min_binance_ret:.3f}% max={max_binance_ret:.3f}%"
        )
        print("👉 可嘗試調低 --spread-entry 或 --binance-drop-pct，或拉長 --hours")
        return 0

    # Sorting
    if args.sort == "pnl":
        events_sorted = sorted(events, key=lambda e: e.pnl_pct, reverse=True)
    elif args.sort == "spread":
        events_sorted = sorted(events, key=lambda e: e.spread_entry_pct, reverse=True)
    else:
        events_sorted = sorted(events, key=lambda e: e.binance_drop_pct)  # more negative first

    show_n = max(0, int(args.show))
    print(f"\n📋 事件列表 (顯示 {min(show_n, len(events_sorted))} 筆，排序={args.sort})")
    for e in events_sorted[:show_n]:
        hold = int((e.exit_time - e.entry_time).total_seconds() // 60)
        print(
            f"- {e.entry_time} | Binance={e.binance_entry:,.0f} dYdX={e.dydx_entry:,.0f} "
            f"spread={e.spread_entry_pct:+.3f}% binance_drop({args.binance_drop_window}m)={e.binance_drop_pct:+.3f}%"
        )
        print(
            f"  exit {e.exit_time} hold={hold}m dYdX_exit={e.dydx_exit:,.0f} pnl={e.pnl_pct:+.3f}% "
            f"MAE={e.mae_pct:+.3f}% MFE={e.mfe_pct:+.3f}% reason={e.exit_reason}"
        )

    print("\n🔎 如何用 Binance 提高預測：")
    print("- 把 Binance 當『領先訊號』：只在 Binance 已經先走出明顯下跌(你設的 window/drop)時，才去找 dYdX 的溢價。")
    print("- 成功與否取決於：價差大小是否足夠 + dYdX 是否真的跟跌（而不是盤薄/不同步造成假價差）。")
    print("- 若你要對準機器人 HALT diff（常是 expected_fill/VWAP），需要另外記錄 orderbook/VWAP，不然 1m close 會低估。")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
