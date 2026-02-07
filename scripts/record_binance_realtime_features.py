#!/usr/bin/env python3
"""
Record Binance realtime features to JSONL via WebSocket.

Default is Binance Futures (perp) to better match dYdX perps.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import aiohttp


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _make_ssl_context(verify: bool) -> Optional[ssl.SSLContext]:
    if verify:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _sum_notional(levels: List[Tuple[float, float]], depth_levels: int) -> float:
    total = 0.0
    for price, qty in levels[:depth_levels]:
        total += price * qty
    return total


@dataclass
class _TradeBucket:
    sec: int
    buy_count: int = 0
    sell_count: int = 0
    buy_qty: float = 0.0
    sell_qty: float = 0.0
    buy_value: float = 0.0
    sell_value: float = 0.0
    big_trade_count: int = 0


class BinanceWsState:
    def __init__(self, big_trade_usd: float, bucket_window_sec: int = 60) -> None:
        self.big_trade_usd = big_trade_usd
        self.bucket_window_sec = bucket_window_sec

        self.bids: List[Tuple[float, float]] = []
        self.asks: List[Tuple[float, float]] = []
        self.last_depth_event_ms: int = 0
        self.last_trade_event_ms: int = 0

        self._trade_buckets: Deque[_TradeBucket] = deque()
        self._price_history: Deque[Tuple[int, float]] = deque(maxlen=900)  # ~15 minutes at 1s

        self._ws_connected: bool = False

    def mark_disconnected(self) -> None:
        self._ws_connected = False

    def mark_connected(self) -> None:
        self._ws_connected = True

    def ws_connected(self) -> bool:
        return self._ws_connected

    def update_depth(self, data: dict) -> None:
        bids_raw = data.get("b") or data.get("bids") or []
        asks_raw = data.get("a") or data.get("asks") or []

        bids: List[Tuple[float, float]] = []
        asks: List[Tuple[float, float]] = []

        for item in bids_raw:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            price = _to_float(item[0], 0.0)
            qty = _to_float(item[1], 0.0)
            if price > 0 and qty > 0:
                bids.append((price, qty))

        for item in asks_raw:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            price = _to_float(item[0], 0.0)
            qty = _to_float(item[1], 0.0)
            if price > 0 and qty > 0:
                asks.append((price, qty))

        self.bids = bids
        self.asks = asks
        self.last_depth_event_ms = int(_to_float(data.get("E") or data.get("eventTime") or 0, 0.0))

    def update_trade(self, data: dict) -> None:
        price = _to_float(data.get("p"), 0.0)
        qty = _to_float(data.get("q"), 0.0)
        if price <= 0 or qty <= 0:
            return

        trade_time_ms = int(_to_float(data.get("T") or data.get("tradeTime") or data.get("E") or 0, 0.0))
        if trade_time_ms <= 0:
            trade_time_ms = int(time.time() * 1000)

        is_buyer_maker = bool(data.get("m"))
        is_buy = not is_buyer_maker
        value_usd = price * qty

        self.last_trade_event_ms = int(_to_float(data.get("E") or 0, 0.0)) or trade_time_ms

        sec = trade_time_ms // 1000
        if not self._trade_buckets or self._trade_buckets[-1].sec != sec:
            self._trade_buckets.append(_TradeBucket(sec=sec))
        bucket = self._trade_buckets[-1]

        if is_buy:
            bucket.buy_count += 1
            bucket.buy_qty += qty
            bucket.buy_value += value_usd
        else:
            bucket.sell_count += 1
            bucket.sell_qty += qty
            bucket.sell_value += value_usd

        if value_usd >= self.big_trade_usd:
            bucket.big_trade_count += 1

    def _purge_old_buckets(self, now_sec: int) -> None:
        cutoff = now_sec - self.bucket_window_sec + 1
        while self._trade_buckets and self._trade_buckets[0].sec < cutoff:
            self._trade_buckets.popleft()

    def snapshot_trade_stats(self, now_sec: int) -> Dict[str, float]:
        self._purge_old_buckets(now_sec)

        buy_count = sell_count = 0
        buy_qty = sell_qty = 0.0
        buy_value = sell_value = 0.0
        big_trade_count = 0

        for b in self._trade_buckets:
            buy_count += b.buy_count
            sell_count += b.sell_count
            buy_qty += b.buy_qty
            sell_qty += b.sell_qty
            buy_value += b.buy_value
            sell_value += b.sell_value
            big_trade_count += b.big_trade_count

        total_value = buy_value + sell_value
        trade_imbalance = (buy_value - sell_value) / total_value if total_value > 0 else 0.0

        return {
            "trade_imbalance": trade_imbalance,
            "big_trade_count": big_trade_count,
            "big_buy_count": buy_count,
            "big_sell_count": sell_count,
            "big_buy_volume": buy_qty,
            "big_sell_volume": sell_qty,
            "big_buy_value": buy_value,
            "big_sell_value": sell_value,
        }

    def current_prices(self) -> Tuple[float, float, float]:
        bid = self.bids[0][0] if self.bids else 0.0
        ask = self.asks[0][0] if self.asks else 0.0
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
        return mid, bid, ask

    def record_price(self, now_sec: int, price: float) -> None:
        if price <= 0:
            return
        if self._price_history and self._price_history[-1][0] == now_sec:
            self._price_history[-1] = (now_sec, price)
        else:
            self._price_history.append((now_sec, price))

    def get_price_change(self, now_sec: int, period_seconds: int) -> float:
        if not self._price_history:
            return 0.0

        current = self._price_history[-1][1]
        if current <= 0:
            return 0.0

        target = now_sec - period_seconds
        old_price = None
        for ts, price in reversed(self._price_history):
            if ts <= target:
                old_price = price
                break
        if old_price is None:
            old_price = self._price_history[0][1]

        if not old_price or old_price <= 0:
            return 0.0
        return (current - old_price) / old_price * 100


async def _consume_binance_ws(url: str, state: BinanceWsState, ssl_ctx: Optional[ssl.SSLContext]) -> None:
    backoff = 1.0
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, ssl=ssl_ctx, heartbeat=20) as ws:
                    state.mark_connected()
                    backoff = 1.0
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        payload = json.loads(msg.data)
                        stream = payload.get("stream", "")
                        data = payload.get("data") or {}
                        if not isinstance(data, dict):
                            continue

                        if "depth" in stream:
                            state.update_depth(data)
                        elif "aggTrade" in stream:
                            state.update_trade(data)
        except asyncio.CancelledError:
            raise
        except Exception:
            state.mark_disconnected()
            await asyncio.sleep(backoff)
            backoff = min(15.0, backoff * 1.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Record Binance realtime features (WS).")
    parser.add_argument("--symbol", default="BTCUSDT", help="Binance symbol (default BTCUSDT)")
    parser.add_argument("--market-type", default="futures", choices=["spot", "futures"])
    parser.add_argument("--hours", type=float, default=1.0, help="How many hours to record")
    parser.add_argument("--interval", type=float, default=1.0, help="Sampling interval in seconds")
    parser.add_argument("--depth-levels", type=int, default=10, help="Orderbook depth levels (max 20)")
    parser.add_argument("--big-trade-usd", type=float, default=10000.0, help="Big trade USD threshold")
    parser.add_argument("--out", type=str, default="", help="Output JSONL path")
    parser.add_argument("--no-ssl-verify", action="store_true", help="Disable SSL verification (use if SSL errors)")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else Path(
        f"data/ai_memory/binance_snapshots_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    symbol = args.symbol.upper()
    symbol_stream = symbol.lower()
    ws_base = "wss://fstream.binance.com" if args.market_type == "futures" else "wss://stream.binance.com:9443"
    streams = f"{symbol_stream}@depth20@100ms/{symbol_stream}@aggTrade"
    ws_url = f"{ws_base}/stream?streams={streams}"

    ssl_ctx = _make_ssl_context(verify=not args.no_ssl_verify)
    state = BinanceWsState(big_trade_usd=args.big_trade_usd, bucket_window_sec=60)

    deadline = time.time() + args.hours * 3600
    print(f"▶️ Recording Binance snapshots to {out_path} for {args.hours} hours, interval {args.interval}s")

    async def runner() -> None:
        consumer = asyncio.create_task(_consume_binance_ws(ws_url, state, ssl_ctx))
        try:
            with out_path.open("a") as f:
                while time.time() < deadline:
                    now_sec = int(time.time())
                    mid, bid, ask = state.current_prices()
                    if mid <= 0:
                        await asyncio.sleep(args.interval)
                        continue

                    state.record_price(now_sec, mid)

                    depth_levels = max(1, min(int(args.depth_levels), 20))
                    bid_depth = _sum_notional(state.bids, depth_levels)
                    ask_depth = _sum_notional(state.asks, depth_levels)
                    depth_imbalance = (
                        (bid_depth - ask_depth) / (bid_depth + ask_depth) if (bid_depth + ask_depth) > 0 else 0.0
                    )

                    spread_pct = ((ask - bid) / mid * 100) if bid > 0 and ask > 0 and mid > 0 else 0.0

                    trade_stats = state.snapshot_trade_stats(now_sec)

                    record = {
                        "timestamp": _now_iso(),
                        "source": "binance_ws",
                        "market_type": args.market_type,
                        "market": symbol,
                        "price": mid,
                        "bid_price": bid,
                        "ask_price": ask,
                        "spread_pct": spread_pct,
                        "bid_depth": bid_depth,
                        "ask_depth": ask_depth,
                        "depth_imbalance": depth_imbalance,
                        "price_change_1m": state.get_price_change(now_sec, 60),
                        "price_change_5m": state.get_price_change(now_sec, 300),
                        "buy_volume_1m": trade_stats["big_buy_volume"],
                        "sell_volume_1m": trade_stats["big_sell_volume"],
                        "ws_connected": state.ws_connected(),
                        "last_update": max(state.last_depth_event_ms, state.last_trade_event_ms),
                    }
                    record.update(trade_stats)

                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()

                    await asyncio.sleep(args.interval)
        finally:
            consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer

    import contextlib

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        print("🛑 interrupted")
    finally:
        print("✅ done")


if __name__ == "__main__":
    main()
