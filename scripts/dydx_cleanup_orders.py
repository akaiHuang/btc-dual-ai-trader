#!/usr/bin/env python3
"""
Cleanup dYdX open orders (OPEN / UNTRIGGERED) for a symbol.

Default is dry-run: list counts and a sample of open orders.
Use --execute to cancel.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Iterable

try:
    from dydx_whale_trader import DydxAPI, DydxConfig, DYDX_AVAILABLE
except Exception:
    from scripts.dydx_whale_trader import DydxAPI, DydxConfig, DYDX_AVAILABLE


def _parse_statuses(raw: str) -> list[str]:
    if not raw:
        return ["OPEN", "UNTRIGGERED"]
    parts = [p.strip().upper() for p in raw.split(",")]
    return [p for p in parts if p]


def _fmt_order(o: dict) -> str:
    client_id = o.get("clientId", "")
    side = o.get("side", "")
    status = o.get("status", "")
    otype = o.get("type", "")
    size = o.get("size", "")
    price = o.get("price", "")
    trigger = o.get("triggerPrice", "") or o.get("triggerPrice")
    tif = o.get("timeInForce", "")
    flags = o.get("orderFlags", "")
    return f"id={client_id} side={side} status={status} type={otype} size={size} price={price} trigger={trigger} tif={tif} flags={flags}"


def _print_orders(orders: Iterable[dict], limit: int) -> None:
    shown = 0
    for o in orders:
        if shown >= limit:
            break
        print(f"  - {_fmt_order(o)}")
        shown += 1
    if shown == 0:
        print("  (none)")


async def _run(args: argparse.Namespace) -> int:
    if not DYDX_AVAILABLE:
        print("dYdX SDK not available; install dydx-v4-client first.")
        return 1

    statuses = _parse_statuses(args.statuses)
    if args.subaccount is not None:
        os.environ["DYDX_SUBACCOUNT_NUMBER"] = str(args.subaccount)

    cfg = DydxConfig(
        network=args.network,
        symbol=args.symbol,
        paper_trading=False,
        sync_real_trading=True,
    )

    api = DydxAPI(cfg)
    ok = await api.connect()
    if not ok:
        print("Failed to connect to dYdX.")
        return 1

    print(f"Using subaccount: {api.subaccount}")
    orders = await api.get_open_orders(status=statuses, symbol=args.symbol)
    print(f"Open orders ({args.symbol}) before: {len(orders)}")
    _print_orders(orders, args.limit)

    if not args.execute:
        print("Dry-run only. Re-run with --execute to cancel.")
        return 0

    cancelled = await api.cancel_open_orders(symbol=args.symbol, status=statuses)
    cancelled += await api.cancel_all_conditional_orders()

    # Force refresh
    try:
        api._open_orders_cache = None
        api._open_orders_cache_time = 0.0
    except Exception:
        pass

    remaining = await api.get_open_orders(status=statuses, symbol=args.symbol)
    print(f"Cancelled: {cancelled}")
    print(f"Open orders ({args.symbol}) after: {len(remaining)}")
    _print_orders(remaining, args.limit)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", default="mainnet", choices=["mainnet", "testnet"])
    parser.add_argument("--symbol", default="BTC-USD")
    parser.add_argument("--subaccount", type=int, default=None)
    parser.add_argument("--statuses", default="OPEN,UNTRIGGERED")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
