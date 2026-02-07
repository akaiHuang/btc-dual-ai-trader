#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests


UTC = timezone.utc


def _pct_change(open_price: float, close_price: float) -> float:
    if open_price == 0:
        return 0.0
    return (close_price - open_price) / open_price * 100.0


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return float("nan")
    if pct <= 0:
        return float(min(values))
    if pct >= 100:
        return float(max(values))

    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return float(d0 + d1)


def _mean(values: Sequence[float]) -> float:
    return float(sum(values) / len(values)) if values else float("nan")


def _median(values: Sequence[float]) -> float:
    if not values:
        return float("nan")
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_vals[mid])
    return float((sorted_vals[mid - 1] + sorted_vals[mid]) / 2)


def _dt_floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_dt(s: str) -> datetime:
    # Accept "YYYY-MM-DD" or ISO8601 with/without Z.
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return datetime.fromisoformat(s).replace(tzinfo=UTC)
    if s.endswith("Z"):
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)
    return datetime.fromisoformat(s).astimezone(UTC)


def _requests_get_json(session: requests.Session, url: str, *, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Any:
    resp = session.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _binance_base_urls() -> List[str]:
    # Spot endpoints; keep a small fallback list for DNS/route issues.
    return [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api2.binance.com",
        "https://api3.binance.com",
    ]


@dataclass(frozen=True)
class Candle:
    started_at: datetime
    open: float
    high: float
    low: float
    close: float

    @property
    def move_pct(self) -> float:
        return _pct_change(self.open, self.close)

    @property
    def abs_move_pct(self) -> float:
        return abs(self.move_pct)

    @property
    def range_pct(self) -> float:
        if self.open == 0:
            return 0.0
        return (self.high - self.low) / self.open * 100.0

    @property
    def up_from_open_pct(self) -> float:
        if self.open == 0:
            return 0.0
        return (self.high - self.open) / self.open * 100.0

    @property
    def down_from_open_pct(self) -> float:
        if self.open == 0:
            return 0.0
        return (self.open - self.low) / self.open * 100.0


def fetch_binance_1m_candles(
    session: requests.Session,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
) -> List[Candle]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    out: List[Candle] = []

    last_open_ms: Optional[int] = None
    cursor_ms = start_ms

    base_urls = _binance_base_urls()
    base_url_idx = 0

    while cursor_ms < end_ms:
        url = f"{base_urls[base_url_idx]}/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": cursor_ms,
            "endTime": end_ms,
            "limit": 1000,
        }

        try:
            data = _requests_get_json(session, url, params=params, timeout=10.0)
        except Exception:
            base_url_idx += 1
            if base_url_idx >= len(base_urls):
                raise
            continue

        if not isinstance(data, list) or not data:
            break

        for row in data:
            open_time_ms = int(row[0])
            open_price = float(row[1])
            high_price = float(row[2])
            low_price = float(row[3])
            close_price = float(row[4])
            dt = datetime.fromtimestamp(open_time_ms / 1000.0, tz=UTC)
            out.append(Candle(started_at=dt, open=open_price, high=high_price, low=low_price, close=close_price))
            last_open_ms = open_time_ms

        if last_open_ms is None:
            break

        next_ms = last_open_ms + 60_000
        if next_ms <= cursor_ms:
            break
        cursor_ms = next_ms

        # tiny sleep to be polite; 7d requires ~11 calls
        time.sleep(0.05)

    # Ensure within range and sorted
    out = [c for c in out if start <= c.started_at < end]
    out.sort(key=lambda c: c.started_at)
    return out


def fetch_dydx_1m_candles(
    session: requests.Session,
    *,
    market: str,
    start: datetime,
    end: datetime,
    base_url: str = "https://indexer.dydx.trade/v4",
) -> List[Candle]:
    # dYdX max limit=1000; use toISO pagination (exclusive upper bound).
    out: List[Candle] = []
    cursor_end = end

    while cursor_end > start:
        url = f"{base_url}/candles/perpetualMarkets/{market}"
        params = {
            "resolution": "1MIN",
            "limit": 1000,
            "toISO": _iso_z(cursor_end),
        }
        data = _requests_get_json(session, url, params=params, timeout=10.0)
        candles = data.get("candles", []) if isinstance(data, dict) else []
        if not candles:
            break

        # API returns newest->oldest
        oldest_dt: Optional[datetime] = None
        for c in candles:
            started_at = _parse_dt(c["startedAt"])
            open_price = float(c["open"])
            high_price = float(c["high"])
            low_price = float(c["low"])
            close_price = float(c["close"])
            out.append(Candle(started_at=started_at, open=open_price, high=high_price, low=low_price, close=close_price))
            if oldest_dt is None or started_at < oldest_dt:
                oldest_dt = started_at

        if oldest_dt is None:
            break

        # Move cursor to oldest candle start (exclusive in next query)
        cursor_end = oldest_dt
        time.sleep(0.05)

    out = [c for c in out if start <= c.started_at < end]
    out.sort(key=lambda c: c.started_at)
    return out


def last_closed_1m_move(candles: Sequence[Candle], *, now: datetime) -> Optional[Candle]:
    if not candles:
        return None
    now = now.astimezone(UTC)
    # choose the last candle whose end time <= now
    for c in reversed(candles):
        if c.started_at + timedelta(minutes=1) <= now:
            return c
    return None


@dataclass(frozen=True)
class VolStats:
    minutes: int
    events: int
    events_up: int
    events_down: int
    avg_minutes_per_event: float
    mean_abs_move_pct: float
    median_abs_move_pct: float
    p95_abs_move_pct: float
    max_abs_move_pct: float
    mean_gap_minutes: float
    median_gap_minutes: float
    p95_gap_minutes: float


def compute_stats(candles: Sequence[Candle], *, threshold_pct: float, metric: str) -> VolStats:
    if metric == "range":
        metric_moves = [c.range_pct for c in candles]
        events = [c for c in candles if c.range_pct >= threshold_pct]
        events_up = [c for c in candles if c.up_from_open_pct >= threshold_pct]
        events_down = [c for c in candles if c.down_from_open_pct >= threshold_pct]
    else:
        metric_moves = [c.abs_move_pct for c in candles]
        events = [c for c in candles if c.abs_move_pct >= threshold_pct]
        events_up = [c for c in candles if c.move_pct >= threshold_pct]
        events_down = [c for c in candles if c.move_pct <= -threshold_pct]

    minutes = len(candles)

    gaps: List[float] = []
    last_t: Optional[datetime] = None
    for c in events:
        if last_t is not None:
            gaps.append((c.started_at - last_t).total_seconds() / 60.0)
        last_t = c.started_at

    return VolStats(
        minutes=minutes,
        events=len(events),
        events_up=len(events_up),
        events_down=len(events_down),
        avg_minutes_per_event=(minutes / len(events)) if events else float("inf"),
        mean_abs_move_pct=_mean(metric_moves),
        median_abs_move_pct=_median(metric_moves),
        p95_abs_move_pct=_percentile(metric_moves, 95),
        max_abs_move_pct=max(metric_moves) if metric_moves else float("nan"),
        mean_gap_minutes=_mean(gaps),
        median_gap_minutes=_median(gaps),
        p95_gap_minutes=_percentile(gaps, 95),
    )


def group_by_day(candles: Sequence[Candle]) -> Dict[str, List[Candle]]:
    grouped: Dict[str, List[Candle]] = {}
    for c in candles:
        k = c.started_at.date().isoformat()
        grouped.setdefault(k, []).append(c)
    return grouped


def _fmt_float(x: float, *, digits: int = 4) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "-"
    return f"{x:.{digits}f}"


def _fmt_minutes(x: float) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "-"
    if x < 60:
        return f"{x:.1f}m"
    return f"{x/60.0:.2f}h"


def print_markdown_table(headers: List[str], rows: List[List[str]]) -> None:
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        print("| " + " | ".join(r) + " |")


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare 1-minute volatility between Binance and dYdX (last N days).")
    ap.add_argument("--days", type=int, default=7, help="Lookback days (default: 7)")
    ap.add_argument("--threshold-pct", type=float, default=0.04, help="Event threshold in percent (default: 0.04)")
    ap.add_argument(
        "--metric",
        type=str,
        default="close",
        choices=("close", "range"),
        help="Event metric: close=|open-close|, range=high-low (default: close)",
    )
    ap.add_argument("--binance-symbol", type=str, default="BTCUSDT", help="Binance spot symbol (default: BTCUSDT)")
    ap.add_argument("--dydx-market", type=str, default="BTC-USD", help="dYdX perpetual market (default: BTC-USD)")
    ap.add_argument("--start", type=str, default=None, help="Start time (UTC) e.g. 2025-12-19 or 2025-12-19T00:00:00Z")
    ap.add_argument("--end", type=str, default=None, help="End time (UTC) e.g. 2025-12-26 or 2025-12-26T00:00:00Z")
    ap.add_argument("--out", type=str, default=None, help="Write per-minute rows to CSV (optional)")
    args = ap.parse_args()

    now = datetime.now(tz=UTC)
    end = _parse_dt(args.end) if args.end else _dt_floor_minute(now)
    start = _parse_dt(args.start) if args.start else (end - timedelta(days=args.days))
    if start >= end:
        print("❌ start must be before end", file=sys.stderr)
        return 2

    threshold_pct = float(args.threshold_pct)

    with requests.Session() as session:
        # Current 1m move (use last closed candle) for each exchange
        binance_recent = fetch_binance_1m_candles(session, symbol=args.binance_symbol, start=end - timedelta(minutes=10), end=end)
        dydx_recent = fetch_dydx_1m_candles(session, market=args.dydx_market, start=end - timedelta(minutes=10), end=end)

        binance_last = last_closed_1m_move(binance_recent, now=end)
        dydx_last = last_closed_1m_move(dydx_recent, now=end)

        print("\n## Current 1m (last closed candle, UTC)")
        rows: List[List[str]] = []
        for name, c in [("Binance", binance_last), ("dYdX", dydx_last)]:
            if not c:
                rows.append([name, "-", "-", "-", "-"])
                continue
            rows.append(
                [
                    name,
                    c.started_at.strftime("%Y-%m-%d %H:%M"),
                    f"{c.open:.2f}",
                    f"{c.close:.2f}",
                    f"{c.move_pct:+.4f}%",
                ]
            )
        print_markdown_table(["Exchange", "Minute", "Open", "Close", "Move"], rows)

        metric_label = "|move|" if args.metric == "close" else "range"
        print(f"\n## Last {args.days} days (1m candles) | threshold = {metric_label} ≥ {threshold_pct:.4f}%")
        print(f"- Range (UTC): {start.isoformat()} → {end.isoformat()}")

        # Fetch full range
        binance_candles = fetch_binance_1m_candles(session, symbol=args.binance_symbol, start=start, end=end)
        dydx_candles = fetch_dydx_1m_candles(session, market=args.dydx_market, start=start, end=end)

    stats_bin = compute_stats(binance_candles, threshold_pct=threshold_pct, metric=args.metric)
    stats_dydx = compute_stats(dydx_candles, threshold_pct=threshold_pct, metric=args.metric)

    summary_rows = [
        [
            "Binance",
            str(stats_bin.minutes),
            str(stats_bin.events),
            str(stats_bin.events_up),
            str(stats_bin.events_down),
            _fmt_minutes(stats_bin.avg_minutes_per_event),
            _fmt_float(stats_bin.mean_abs_move_pct, digits=4) + "%",
            _fmt_float(stats_bin.p95_abs_move_pct, digits=4) + "%",
            _fmt_minutes(stats_bin.mean_gap_minutes),
        ],
        [
            "dYdX",
            str(stats_dydx.minutes),
            str(stats_dydx.events),
            str(stats_dydx.events_up),
            str(stats_dydx.events_down),
            _fmt_minutes(stats_dydx.avg_minutes_per_event),
            _fmt_float(stats_dydx.mean_abs_move_pct, digits=4) + "%",
            _fmt_float(stats_dydx.p95_abs_move_pct, digits=4) + "%",
            _fmt_minutes(stats_dydx.mean_gap_minutes),
        ],
    ]
    print_markdown_table(
        [
            "Exchange",
            "Minutes",
            f"Events≥{threshold_pct:.4f}%",
            "UpEvents",
            "DownEvents",
            "AvgMin/Event",
            f"Mean{metric_label}",
            f"P95{metric_label}",
            "MeanGap",
        ],
        summary_rows,
    )

    # Per-day table
    per_day_rows: List[List[str]] = []
    for day in sorted(set(group_by_day(binance_candles)) | set(group_by_day(dydx_candles))):
        b = group_by_day(binance_candles).get(day, [])
        d = group_by_day(dydx_candles).get(day, [])
        sb = compute_stats(b, threshold_pct=threshold_pct, metric=args.metric)
        sd = compute_stats(d, threshold_pct=threshold_pct, metric=args.metric)
        per_day_rows.append(
            [
                day,
                _fmt_minutes(sb.avg_minutes_per_event),
                f"{sb.events}/{sb.minutes}",
                _fmt_minutes(sd.avg_minutes_per_event),
                f"{sd.events}/{sd.minutes}",
            ]
        )

    print("\n## Per-day (UTC) avg spacing of |move|≥threshold")
    print_markdown_table(
        ["Day", "Binance AvgMin/Event", "Binance Events/Min", "dYdX AvgMin/Event", "dYdX Events/Min"],
        per_day_rows,
    )

    # Optional CSV dump (per-minute rows, both exchanges)
    if args.out:
        path = args.out
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "exchange",
                "started_at_utc",
                "open",
                "high",
                "low",
                "close",
                "move_pct",
                "abs_move_pct",
                "range_pct",
            ])
            for name, candles in [("binance", binance_candles), ("dydx", dydx_candles)]:
                for c in candles:
                    w.writerow([
                        name,
                        c.started_at.isoformat(),
                        f"{c.open:.2f}",
                        f"{c.high:.2f}",
                        f"{c.low:.2f}",
                        f"{c.close:.2f}",
                        f"{c.move_pct:.6f}",
                        f"{c.abs_move_pct:.6f}",
                        f"{c.range_pct:.6f}",
                    ])
        print(f"\n✅ Wrote CSV: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
