#!/usr/bin/env python3
import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from dydx_v4_client.indexer.rest.indexer_client import IndexerClient
except ImportError as exc:
    print(f"ERROR: missing dydx-v4-client: {exc}")
    print("Install with: pip install dydx-v4-client")
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from src.dydx_data_hub import DydxDataHub
except ImportError as exc:
    print(f"ERROR: failed to load DydxDataHub: {exc}")
    sys.exit(1)


NETWORK_CONFIG = {
    "mainnet": {
        "rest_indexer": "https://indexer.dydx.trade",
    },
    "testnet": {
        "rest_indexer": "https://indexer.v4testnet.dydx.exchange",
    },
}


def _parse_orderbook_ts(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        ts = float(value)
        return ts / 1000.0 if ts > 1e12 else ts
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0
    return 0.0


def _best_bid_ask(orderbook):
    bids = orderbook.get("bids", []) or []
    asks = orderbook.get("asks", []) or []
    best_bid = float(bids[0].get("price", 0)) if bids else 0.0
    best_ask = float(asks[0].get("price", 0)) if asks else 0.0
    return best_bid, best_ask


def _mid(bid, ask):
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    return 0.0


def _spread_bps(bid, ask):
    mid = _mid(bid, ask)
    if mid <= 0:
        return 0.0
    return (ask - bid) / mid * 10000


async def _fetch_api_snapshot(indexer, symbol):
    t0 = time.time()
    orderbook = await indexer.markets.get_perpetual_market_orderbook(symbol)
    t1 = time.time()
    bid, ask = _best_bid_ask(orderbook or {})
    mid = _mid(bid, ask)
    ob_ts = _parse_orderbook_ts(orderbook.get("time") if orderbook else None)
    if ob_ts <= 0:
        ob_ts = _parse_orderbook_ts(orderbook.get("timestamp") if orderbook else None)
    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_bps": _spread_bps(bid, ask),
        "fetch_ms": (t1 - t0) * 1000,
        "ob_time": ob_ts,
    }


def _format_age_ms(seconds):
    if seconds <= 0:
        return "NA"
    return f"{seconds * 1000:.0f}"


async def main():
    parser = argparse.ArgumentParser(description="Compare dYdX API vs WS mid prices")
    parser.add_argument("--symbol", default="BTC-USD", help="Market symbol, e.g. BTC-USD")
    parser.add_argument("--network", default="mainnet", choices=["mainnet", "testnet"])
    parser.add_argument("--interval", type=float, default=1.0, help="Sample interval seconds")
    parser.add_argument("--duration", type=float, default=60.0, help="Total duration seconds")
    args = parser.parse_args()

    rest_indexer = NETWORK_CONFIG[args.network]["rest_indexer"]
    indexer = IndexerClient(rest_indexer)

    hub = DydxDataHub(symbol=args.symbol, network=args.network)
    hub.start()

    try:
        print(
            "ts,ws_mid,api_mid,mid_diff,mid_diff_bps,ws_spread_bps,api_spread_bps,ws_age_ms,api_age_ms,api_fetch_ms"
        )
        start = time.time()
        while time.time() - start < args.duration:
            now = time.time()
            ws_data = hub.get_data()

            ws_bid = float(getattr(ws_data, "bid_price", 0) or 0.0)
            ws_ask = float(getattr(ws_data, "ask_price", 0) or 0.0)
            ws_mid = float(getattr(ws_data, "current_price", 0) or 0.0)
            if ws_mid <= 0:
                ws_mid = _mid(ws_bid, ws_ask)
            ws_spread = _spread_bps(ws_bid, ws_ask)
            ws_last_update = float(getattr(ws_data, "last_update", 0.0) or 0.0)
            ws_age = now - ws_last_update if ws_last_update > 0 else 0.0

            api = await _fetch_api_snapshot(indexer, args.symbol)
            api_age = (now - api["ob_time"]) if api["ob_time"] > 0 else 0.0

            if ws_mid > 0 and api["mid"] > 0:
                mid_diff = api["mid"] - ws_mid
                mid_diff_bps = mid_diff / ws_mid * 10000
            else:
                mid_diff = 0.0
                mid_diff_bps = 0.0

            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(
                f"{ts},"
                f"{ws_mid:.2f},"
                f"{api['mid']:.2f},"
                f"{mid_diff:+.2f},"
                f"{mid_diff_bps:+.2f},"
                f"{ws_spread:.2f},"
                f"{api['spread_bps']:.2f},"
                f"{_format_age_ms(ws_age)},"
                f"{_format_age_ms(api_age)},"
                f"{api['fetch_ms']:.0f}"
            )

            await asyncio.sleep(max(0.05, args.interval))
    finally:
        hub.stop()


if __name__ == "__main__":
    asyncio.run(main())
