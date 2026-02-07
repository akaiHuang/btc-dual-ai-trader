#!/usr/bin/env python3
"""
Record dYdX + Binance realtime features to JSONL using WS feeds.

This collector focuses on short-horizon features for signal validation.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import ssl
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Tuple

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from src.dydx_data_hub import get_data_hub, cleanup_data_hub


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value, default=0.0) -> float:
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


def _take_levels(levels: List, depth_levels: int) -> List[Tuple[float, float]]:
    trimmed = []
    for item in levels[:depth_levels]:
        if isinstance(item, dict):
            price = _to_float(item.get("price"), 0.0)
            size = _to_float(item.get("size"), 0.0)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price = _to_float(item[0], 0.0)
            size = _to_float(item[1], 0.0)
        else:
            continue
        if price > 0 and size > 0:
            trimmed.append((price, size))
    return trimmed


def _sum_notional(levels: Iterable[Tuple[float, float]]) -> float:
    return sum(price * size for price, size in levels)


def _aggregate_recent_trades(recent_trades: List[dict], window_ms: int, big_trade_usd: float):
    now_ms = time.time() * 1000
    cutoff = now_ms - window_ms
    buy_count = sell_count = 0
    buy_qty = sell_qty = 0.0
    buy_value = sell_value = 0.0
    big_trade_count = 0

    for t in recent_trades or []:
        ts = _to_float(t.get("time"), 0.0)
        if ts < cutoff:
            continue
        qty = _to_float(t.get("qty"), 0.0)
        value = _to_float(t.get("value_usdt"), 0.0)
        is_buy = bool(t.get("is_buy"))
        if is_buy:
            buy_count += 1
            buy_qty += qty
            buy_value += value
        else:
            sell_count += 1
            sell_qty += qty
            sell_value += value
        if value >= big_trade_usd:
            big_trade_count += 1

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


def _prefix_dict(values: Dict, prefix: str) -> Dict:
    return {f"{prefix}{key}": value for key, value in values.items()}


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


class BinanceFundingFetcher:
    """定期從 Binance REST API 抓取 Funding Rate"""
    
    def __init__(self, symbol: str = "BTCUSDT", market_type: str = "futures"):
        self.symbol = symbol.upper()
        self.market_type = market_type
        self.funding_rate: float = 0.0
        self.funding_time: int = 0
        self.next_funding_time: int = 0
        self.open_interest: float = 0.0
        self.open_interest_value: float = 0.0
        self.long_short_ratio: float = 1.0  # >1 = 多頭多, <1 = 空頭多
        self.last_fetch_time: float = 0.0
        self._fetch_interval: float = 60.0  # 每 60 秒更新一次
    
    async def fetch(self, session: aiohttp.ClientSession) -> None:
        """抓取 Funding Rate + Open Interest + Long/Short Ratio"""
        now = time.time()
        if now - self.last_fetch_time < self._fetch_interval:
            return
        
        try:
            base_url = "https://fapi.binance.com" if self.market_type == "futures" else "https://api.binance.com"
            
            # 1. Funding Rate
            funding_url = f"{base_url}/fapi/v1/premiumIndex?symbol={self.symbol}"
            async with session.get(funding_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.funding_rate = _to_float(data.get("lastFundingRate"), 0.0) * 100  # 轉成百分比
                    self.funding_time = int(_to_float(data.get("time"), 0))
                    self.next_funding_time = int(_to_float(data.get("nextFundingTime"), 0))
            
            # 2. Open Interest
            oi_url = f"{base_url}/fapi/v1/openInterest?symbol={self.symbol}"
            async with session.get(oi_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.open_interest = _to_float(data.get("openInterest"), 0.0)
            
            # 3. Long/Short Ratio (頂級交易員持倉比)
            ratio_url = f"{base_url}/futures/data/topLongShortPositionRatio?symbol={self.symbol}&period=5m&limit=1"
            async with session.get(ratio_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        self.long_short_ratio = _to_float(data[0].get("longShortRatio"), 1.0)
            
            self.last_fetch_time = now
        except Exception:
            pass  # 靜默失敗，下次再試
    
    def get_direction_bias(self) -> str:
        """根據 Funding Rate 計算方向偏好"""
        if self.funding_rate > 0.01:  # > 0.01% = 多頭過熱
            return "SHORT"
        elif self.funding_rate < -0.01:  # < -0.01% = 空頭過熱
            return "LONG"
        else:
            return "NEUTRAL"
    
    def snapshot(self) -> Dict:
        return {
            "funding_rate_pct": self.funding_rate,  # 百分比
            "funding_time": self.funding_time,
            "next_funding_time": self.next_funding_time,
            "open_interest": self.open_interest,
            "long_short_ratio": self.long_short_ratio,
            "direction_bias": self.get_direction_bias(),
        }


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

        for bucket in self._trade_buckets:
            buy_count += bucket.buy_count
            sell_count += bucket.sell_count
            buy_qty += bucket.buy_qty
            sell_qty += bucket.sell_qty
            buy_value += bucket.buy_value
            sell_value += bucket.sell_value
            big_trade_count += bucket.big_trade_count

        total_value = buy_value + sell_value
        trade_imbalance = (buy_value - sell_value) / total_value if total_value > 0 else 0.0

        return {
            "trade_imbalance": trade_imbalance,
            "big_trade_count": big_trade_count,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "buy_volume": buy_qty,
            "sell_volume": sell_qty,
            "buy_value": buy_value,
            "sell_value": sell_value,
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


async def _consume_binance_ws(
    url: str,
    state: BinanceWsState,
    ssl_ctx: Optional[ssl.SSLContext],
    stop_event: asyncio.Event,
) -> None:
    backoff = 1.0
    while not stop_event.is_set():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, ssl=ssl_ctx, heartbeat=20) as ws:
                    state.mark_connected()
                    backoff = 1.0
                    async for msg in ws:
                        if stop_event.is_set():
                            break
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
            if stop_event.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(15.0, backoff * 1.5)


async def run(args: argparse.Namespace) -> None:
    out_path = Path(args.out) if args.out else Path(
        f"data/ai_memory/market_snapshots_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    hub = get_data_hub(symbol=args.market, network=args.network, ssl_verify=not args.no_ssl_verify)
    hub.start()

    binance_state: Optional[BinanceWsState] = None
    binance_task: Optional[asyncio.Task] = None
    funding_fetcher: Optional[BinanceFundingFetcher] = None
    stop_event = asyncio.Event()

    if not args.binance_disable:
        binance_state = BinanceWsState(big_trade_usd=args.big_trade_usd, bucket_window_sec=60)
        funding_fetcher = BinanceFundingFetcher(symbol=args.binance_symbol, market_type=args.binance_market_type)
        symbol = args.binance_symbol.upper()
        symbol_stream = symbol.lower()
        ws_base = "wss://fstream.binance.com" if args.binance_market_type == "futures" else "wss://stream.binance.com:9443"
        streams = f"{symbol_stream}@depth20@100ms/{symbol_stream}@aggTrade"
        ws_url = f"{ws_base}/stream?streams={streams}"
        ssl_ctx = _make_ssl_context(verify=not args.no_ssl_verify)
        binance_task = asyncio.create_task(_consume_binance_ws(ws_url, binance_state, ssl_ctx, stop_event))

    deadline = time.time() + args.hours * 3600
    print(
        f"▶️ Recording dYdX + Binance snapshots to {out_path} for {args.hours} hours, interval {args.interval}s"
    )

    try:
        with out_path.open("a") as f:
            while time.time() < deadline:
                data = hub.get_data()
                if data.current_price <= 0:
                    await asyncio.sleep(args.interval)
                    continue

                bids = _take_levels(data.bids, args.depth_levels)
                asks = _take_levels(data.asks, args.depth_levels)
                bid_depth = _sum_notional(bids)
                ask_depth = _sum_notional(asks)
                depth_imbalance = (
                    (bid_depth - ask_depth) / (bid_depth + ask_depth) if (bid_depth + ask_depth) > 0 else 0.0
                )

                dydx_trade_stats = _aggregate_recent_trades(
                    data.recent_trades,
                    window_ms=60000,
                    big_trade_usd=args.big_trade_usd,
                )

                record = {
                    "timestamp": _now_iso(),
                    "source": "dydx_ws+binance_ws",
                    "dydx_network": args.network,
                    "dydx_market": args.market,
                    "dydx_price": data.current_price,
                    "dydx_bid_price": data.bid_price,
                    "dydx_ask_price": data.ask_price,
                    "dydx_spread_pct": data.spread_pct,
                    "dydx_bid_depth": bid_depth,
                    "dydx_ask_depth": ask_depth,
                    "dydx_depth_imbalance": depth_imbalance,
                    "dydx_price_change_1m": hub.get_price_change(60),
                    "dydx_price_change_5m": hub.get_price_change(300),
                    "dydx_buy_volume_1m": data.buy_volume_1m,
                    "dydx_sell_volume_1m": data.sell_volume_1m,
                    "dydx_ws_connected": data.ws_connected,
                    "dydx_last_update": data.last_update,
                }
                record.update(_prefix_dict(dydx_trade_stats, "dydx_"))

                binance_mid = binance_bid = binance_ask = 0.0
                binance_bid_depth = binance_ask_depth = 0.0
                binance_depth_imbalance = 0.0
                binance_spread_pct = 0.0
                binance_price_change_1m = 0.0
                binance_price_change_5m = 0.0
                binance_last_update = 0
                binance_ws_connected = False
                binance_stats: Dict[str, float] = {
                    "trade_imbalance": 0.0,
                    "big_trade_count": 0,
                    "buy_count": 0,
                    "sell_count": 0,
                    "buy_volume": 0.0,
                    "sell_volume": 0.0,
                    "buy_value": 0.0,
                    "sell_value": 0.0,
                }

                if binance_state is not None:
                    now_sec = int(time.time())
                    binance_mid, binance_bid, binance_ask = binance_state.current_prices()
                    if binance_mid > 0:
                        binance_state.record_price(now_sec, binance_mid)
                        binance_bid_depth = _sum_notional(binance_state.bids[: args.depth_levels])
                        binance_ask_depth = _sum_notional(binance_state.asks[: args.depth_levels])
                        if (binance_bid_depth + binance_ask_depth) > 0:
                            binance_depth_imbalance = (
                                (binance_bid_depth - binance_ask_depth) / (binance_bid_depth + binance_ask_depth)
                            )
                        if binance_bid > 0 and binance_ask > 0:
                            binance_spread_pct = (binance_ask - binance_bid) / binance_mid * 100
                        binance_price_change_1m = binance_state.get_price_change(now_sec, 60)
                        binance_price_change_5m = binance_state.get_price_change(now_sec, 300)
                    binance_last_update = max(binance_state.last_depth_event_ms, binance_state.last_trade_event_ms)
                    binance_ws_connected = binance_state.ws_connected()
                    binance_stats = binance_state.snapshot_trade_stats(now_sec)
                
                # 抓取 Funding Rate (每 60 秒更新)
                funding_data: Dict = {
                    "funding_rate_pct": 0.0,
                    "funding_time": 0,
                    "next_funding_time": 0,
                    "open_interest": 0.0,
                    "long_short_ratio": 1.0,
                    "direction_bias": "NEUTRAL",
                }
                if funding_fetcher is not None:
                    async with aiohttp.ClientSession() as session:
                        await funding_fetcher.fetch(session)
                    funding_data = funding_fetcher.snapshot()

                record.update(
                    {
                        "binance_market_type": args.binance_market_type,
                        "binance_market": args.binance_symbol.upper(),
                        "binance_price": binance_mid,
                        "binance_bid_price": binance_bid,
                        "binance_ask_price": binance_ask,
                        "binance_spread_pct": binance_spread_pct,
                        "binance_bid_depth": binance_bid_depth,
                        "binance_ask_depth": binance_ask_depth,
                        "binance_depth_imbalance": binance_depth_imbalance,
                        "binance_price_change_1m": binance_price_change_1m,
                        "binance_price_change_5m": binance_price_change_5m,
                        "binance_ws_connected": binance_ws_connected,
                        "binance_last_update": binance_last_update,
                    }
                )
                record.update(_prefix_dict(binance_stats, "binance_"))

                basis = binance_mid - data.current_price if binance_mid > 0 else 0.0
                basis_pct = (basis / data.current_price * 100) if binance_mid > 0 and data.current_price > 0 else 0.0
                record["basis_price"] = basis
                record["basis_pct"] = basis_pct
                record["basis_abs_pct"] = abs(basis_pct)
                
                # Funding Rate 數據 (方向偏好指標)
                record.update(_prefix_dict(funding_data, "binance_"))

                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()

                await asyncio.sleep(args.interval)
    except KeyboardInterrupt:
        print("🛑 interrupted")
    finally:
        stop_event.set()
        if binance_task is not None:
            binance_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await binance_task
        cleanup_data_hub()
        print("✅ done")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record dYdX + Binance realtime features (WS).")
    parser.add_argument("--market", default="BTC-USD", help="dYdX market symbol (default BTC-USD)")
    parser.add_argument("--network", default="mainnet", choices=["mainnet", "testnet"])
    parser.add_argument("--hours", type=float, default=1.0, help="How many hours to record")
    parser.add_argument("--interval", type=float, default=1.0, help="Sampling interval in seconds")
    parser.add_argument("--depth-levels", type=int, default=10, help="Orderbook depth levels")
    parser.add_argument("--big-trade-usd", type=float, default=10000.0, help="Big trade USD threshold")
    parser.add_argument("--out", type=str, default="", help="Output JSONL path")
    parser.add_argument("--binance-symbol", default="BTCUSDT", help="Binance symbol (default BTCUSDT)")
    parser.add_argument("--binance-market-type", default="futures", choices=["spot", "futures"])
    parser.add_argument("--binance-disable", action="store_true", help="Disable Binance feed")
    parser.add_argument("--no-ssl-verify", action="store_true", help="Disable SSL verification (use if SSL errors)")
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
