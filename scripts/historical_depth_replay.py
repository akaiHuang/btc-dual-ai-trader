#!/usr/bin/env python3
"""Historical depth replay runner for the hybrid paper trading system.

This script consumes Binance Futures depth snapshots downloaded from
https://data.binance.vision (bookDepth / depth datasets) together with
optional aggregated trade parquet files, then replays them through the
existing `HybridPaperTradingSystem` decision pipeline.

Features
--------
1. Handles both extracted CSV/JSON files and compressed ZIP archives from
   the official portal (daily downloads).
2. Inputs may cover arbitrary date ranges; filtering occurs by timestamp.
3. Optional aggregated trade stream (Parquet) keeps the legacy
   SignedVolumeTracker + VPIN calculators in sync for higher fidelity.
4. Reuses the exact sniper/hybrid logic from `paper_trading_hybrid_full.py`
   without mocking or reimplementing indicators.

Usage example
-------------
    python scripts/historical_depth_replay.py \
        --depth-dir data/historical/depth_raw \
        --start 2024-01-01T00:00:00 \
        --end 2024-01-01T06:00:00 \
        --agg-trades data/historical/BTCUSDT_agg_trades_20200101_20251115.parquet \
        --decision-interval 5 \
        --initial-capital 100 \
        --max-position 0.5

Important notes
---------------
* Please run `scripts/download_binance_depth.py` first to populate the
  `depth_raw` directory. The extractor inside this script accepts the
  original ZIP files or the CSV/JSON files produced after extraction.
* The aggregated trade parquet file is optional but strongly recommended
  so that VPIN / SignedVolume trackers see realistic order-flow. The
  sample included in this repo contains the necessary schema:
    - timestamp (timezone aware)
    - price
    - qty
    - side ("BUY" or "SELL")
    - trade_id
* To keep memory usage reasonable, limit each replay to a handful of
  hours (or preprocess the parquet file into per-day chunks).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# Ensure project root on sys.path
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from scripts.paper_trading_hybrid_full import HybridPaperTradingSystem  # noqa: E402


@dataclass
class DepthEvent:
    """Normalized depth snapshot."""

    timestamp_ms: int
    bids: List[Tuple[float, float]]
    asks: List[Tuple[float, float]]


@dataclass
class TradeEvent:
    """Normalized aggregated trade event."""

    timestamp_ms: int
    price: float
    qty: float
    is_buyer_maker: bool  # True => seller initiated (buying maker)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay Binance depth files through the hybrid system")
    parser.add_argument("--depth-dir", required=True, help="Directory containing extracted depth/book snapshot files")
    parser.add_argument("--depth-glob", default="*.csv", help="Glob pattern for depth files (default: *.csv)")
    parser.add_argument("--start", required=True, help="Start timestamp (YYYY-mm-dd or ISO8601)")
    parser.add_argument("--end", required=True, help="End timestamp (YYYY-mm-dd or ISO8601)")
    parser.add_argument("--agg-trades", help="Optional Parquet file with aggregated trades to replay")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol (default: BTCUSDT)")
    parser.add_argument("--decision-interval", type=float, default=5.0, help="Decision cadence in seconds (default: 5)")
    parser.add_argument("--initial-capital", type=float, default=100.0, help="Initial capital per mode")
    parser.add_argument("--max-position", type=float, default=0.5, help="Max position percentage (0-1)")
    parser.add_argument("--print-status", type=float, default=60.0, help="Status print interval in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Parse files only (no trading logic)")
    return parser.parse_args()


def parse_timestamp(value: object) -> Optional[int]:
    """Convert assorted timestamp representations to milliseconds."""

    if value is None:
        return None

    if isinstance(value, (int, float, np.integer, np.floating)):
        val = float(value)
        if val > 10_000_000_000:  # assume milliseconds
            return int(val)
        if val > 10_000_000:  # assume seconds
            return int(val * 1000)
        # smaller values => nanoseconds
        return int(val / 1_000_000)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return parse_timestamp(int(text))
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = pd.to_datetime(text, utc=True).to_pydatetime()
            except Exception:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    return None


def parse_levels(raw_levels: object) -> List[Tuple[float, float]]:
    """Normalize bids/asks arrays into float tuples."""

    if raw_levels is None:
        return []
    levels = raw_levels
    if isinstance(raw_levels, str):
        raw_levels = raw_levels.strip()
        if not raw_levels:
            return []
        try:
            levels = json.loads(raw_levels)
        except json.JSONDecodeError:
            # handle semi-colon delimited format: price:qty;price:qty
            parts = [lvl for lvl in raw_levels.split(";") if lvl]
            levels = []
            for part in parts:
                if ":" in part:
                    price_str, qty_str = part.split(":", 1)
                    levels.append([price_str, qty_str])
                else:
                    continue
    normalized: List[Tuple[float, float]] = []
    for entry in levels:
        price = qty = None
        if isinstance(entry, dict):
            price = entry.get("price") or entry.get("p") or entry.get("Price")
            qty = entry.get("qty") or entry.get("quantity") or entry.get("q")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            price, qty = entry[0], entry[1]
        if price is None or qty is None:
            continue
        try:
            normalized.append((float(price), float(qty)))
        except (TypeError, ValueError):
            continue
    return normalized


def payload_to_depth_event(payload: dict) -> Optional[DepthEvent]:
    timestamp_ms = None
    for key in ("timestamp", "event_time", "eventTime", "E", "T", "ts"):
        timestamp_ms = parse_timestamp(payload.get(key))
        if timestamp_ms:
            break
    if timestamp_ms is None:
        # Some bookDepth files only provide first/last update id -> skip
        return None

    bids = parse_levels(payload.get("bids") or payload.get("b"))
    asks = parse_levels(payload.get("asks") or payload.get("a"))
    if not bids or not asks:
        return None

    return DepthEvent(timestamp_ms=timestamp_ms, bids=bids, asks=asks)


def iter_depth_events_from_stream(stream: Iterable[str]) -> Iterator[DepthEvent]:
    peek = None
    for line in stream:
        line = line.strip()
        if not line:
            continue
        peek = line
        break
    if peek is None:
        return

    # JSON lines mode
    if peek.startswith("{"):
        yield from _iter_json_lines(peek, stream)
    else:
        yield from _iter_csv_rows(peek, stream)


def _iter_json_lines(first_line: str, stream: Iterable[str]) -> Iterator[DepthEvent]:
    for line in chain([first_line], stream):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        event = payload_to_depth_event(payload)
        if event:
            yield event


from itertools import chain


def _iter_csv_rows(first_line: str, stream: Iterable[str]) -> Iterator[DepthEvent]:
    reader = csv.DictReader(chain([first_line], stream))
    for row in reader:
        payload = {
            'timestamp': row.get('timestamp') or row.get('event_time') or row.get('E')
        }
        # pass through bids/asks style columns
        for key in ('bids', 'asks', 'b', 'a'):
            if key in row and row[key]:
                payload[key] = row[key]
        # handle flattened columns like bid_0_price
        bid_levels = []
        ask_levels = []
        for k, v in row.items():
            if 'bid' in k.lower() and 'price' in k.lower():
                idx = ''.join(ch for ch in k if ch.isdigit()) or '0'
                qty_key = k.lower().replace('price', 'qty')
                qty_val = row.get(qty_key) or row.get(qty_key.upper())
                if qty_val is None:
                    continue
                bid_levels.append([v, qty_val])
            if 'ask' in k.lower() and 'price' in k.lower():
                idx = ''.join(ch for ch in k if ch.isdigit()) or '0'
                qty_key = k.lower().replace('price', 'qty')
                qty_val = row.get(qty_key) or row.get(qty_key.upper())
                if qty_val is None:
                    continue
                ask_levels.append([v, qty_val])
        if bid_levels:
            payload['bids'] = bid_levels
        if ask_levels:
            payload['asks'] = ask_levels
        event = payload_to_depth_event(payload)
        if event:
            yield event


def iter_depth_files(paths: Sequence[Path]) -> Iterator[DepthEvent]:
    for path in sorted(paths):
        if path.suffix == '.zip':
            with zipfile.ZipFile(path) as zf:
                for member in sorted(zf.namelist()):
                    with zf.open(member) as fh:
                        text_stream = io.TextIOWrapper(fh, encoding='utf-8')
                        yield from iter_depth_events_from_stream(text_stream)
        else:
            with path.open('r', encoding='utf-8') as fh:
                yield from iter_depth_events_from_stream(fh)


def load_trade_events(parquet_path: Path, start_ms: int, end_ms: int) -> List[TradeEvent]:
    if not parquet_path.exists():
        raise FileNotFoundError(parquet_path)
    df = pd.read_parquet(parquet_path)
    if 'timestamp' not in df.columns:
        raise ValueError("Agg trade parquet must contain 'timestamp' column")
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    df = df[(df['timestamp'] >= start_dt) & (df['timestamp'] <= end_dt)].copy()
    df.sort_values('timestamp', inplace=True)
    trade_events: List[TradeEvent] = []
    for row in df.itertuples(index=False):
        timestamp_ms = int(row.timestamp.value // 1_000_000)
        price = float(getattr(row, 'price'))
        qty = float(getattr(row, 'qty'))
        side = str(getattr(row, 'side', 'SELL')).upper()
        # Binance agg trade: side==SELL => buyer is maker
        is_buyer_maker = True if side == 'SELL' else False
        trade_events.append(TradeEvent(timestamp_ms, price, qty, is_buyer_maker))
    return trade_events


class DepthReplayRunner:
    def __init__(
        self,
        system: HybridPaperTradingSystem,
        depth_events: Iterator[DepthEvent],
        trade_events: Optional[List[TradeEvent]],
        start_ms: int,
        end_ms: int,
        decision_interval_sec: float,
        status_interval_sec: float,
        dry_run: bool = False,
    ):
        self.system = system
        self.depth_events = depth_events
        self.trade_events = trade_events or []
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.decision_interval_ms = int(decision_interval_sec * 1000)
        self.status_interval_ms = int(status_interval_sec * 1000)
        self.dry_run = dry_run

    def run(self):
        trade_iter = iter(self.trade_events)
        next_trade = next(trade_iter, None)
        last_decision = None
        last_status = None
        first_loop = True

        processed = 0
        for event in self.depth_events:
            if event.timestamp_ms < self.start_ms:
                continue
            if event.timestamp_ms > self.end_ms:
                break

            processed += 1
            self._apply_depth_event(event)

            while next_trade and next_trade.timestamp_ms <= event.timestamp_ms:
                self._apply_trade(next_trade)
                next_trade = next(trade_iter, None)

            if self.dry_run:
                continue

            if last_decision is None or event.timestamp_ms - last_decision >= self.decision_interval_ms:
                snapshot = self.system._build_market_snapshot()
                if snapshot:
                    if not first_loop:
                        self.system.check_exits(snapshot)
                    else:
                        first_loop = False
                    self.system.check_entries(snapshot)
                last_decision = event.timestamp_ms

            if last_status is None or event.timestamp_ms - last_status >= self.status_interval_ms:
                if not self.dry_run:
                    try:
                        self.system.print_status()
                    except Exception:
                        pass
                last_status = event.timestamp_ms

        print(f"\n✅ Replay finished. Processed {processed} depth snapshots.")
        if self.trade_events:
            print(f"   Trades consumed: {len(self.trade_events)}")
        if not self.dry_run:
            self.system.generate_report()

    def _apply_depth_event(self, event: DepthEvent):
        bids = event.bids
        asks = event.asks
        if not bids or not asks:
            return
        best_bid_price = float(bids[0][0])
        best_ask_price = float(asks[0][0])
        self.system.obi_calc.update_orderbook(bids, asks)
        self.system.spread_depth.update(bids, asks)
        self.system.latest_price = (best_bid_price + best_ask_price) / 2
        event_dt = datetime.fromtimestamp(event.timestamp_ms / 1000, tz=timezone.utc)
        self.system.orderbook_timestamp = event_dt.isoformat()
        self.system.orderbook_data = {
            'bids': bids,
            'asks': asks,
            'timestamp': event.timestamp_ms
        }
        self.system._record_price(self.system.latest_price)
        self.system._update_price_bars(best_bid_price, best_ask_price)

    def _apply_trade(self, trade: TradeEvent):
        payload = {
            'p': trade.price,
            'q': trade.qty,
            'T': trade.timestamp_ms,
            'm': trade.is_buyer_maker,
            'isBuyerMaker': trade.is_buyer_maker
        }
        self.system.signed_volume.add_trade(payload)
        self.system.vpin_calc.process_trade(payload)
        self.system.pending_volume += trade.qty


def parse_iso_or_date(value: str, default_start: bool) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("Timestamp string cannot be empty")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.strptime(text, "%Y-%m-%d")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if len(text) == 10:  # date only
        if default_start:
            dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=0)
    return dt


def main():
    args = parse_args()

    start_dt = parse_iso_or_date(args.start, True)
    end_dt = parse_iso_or_date(args.end, False)
    if end_dt <= start_dt:
        raise SystemExit("end must be after start")

    depth_dir = Path(args.depth_dir)
    depth_files = sorted(depth_dir.glob(args.depth_glob))
    if not depth_files:
        raise SystemExit(f"No depth files found under {depth_dir} with pattern {args.depth_glob}")

    print(f"📁 Depth files: {len(depth_files)} (first: {depth_files[0].name})")
    trade_events = None
    if args.agg_trades:
        trade_events = load_trade_events(Path(args.agg_trades), int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000))
        print(f"📄 Aggregated trades loaded: {len(trade_events)}")

    system = HybridPaperTradingSystem(
        initial_capital=args.initial_capital,
        max_position_pct=args.max_position,
        test_duration_hours=((end_dt - start_dt).total_seconds() / 3600)
    )

    depth_iterator = iter_depth_files(depth_files)
    runner = DepthReplayRunner(
        system=system,
        depth_events=depth_iterator,
        trade_events=trade_events,
        start_ms=int(start_dt.timestamp() * 1000),
        end_ms=int(end_dt.timestamp() * 1000),
        decision_interval_sec=args.decision_interval,
        status_interval_sec=args.print_status,
        dry_run=args.dry_run
    )
    runner.run()


if __name__ == "__main__":
    main()
