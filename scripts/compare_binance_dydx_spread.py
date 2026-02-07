#!/usr/bin/env python3
"""
Compare Binance and dYdX top-of-book spread in bps.

Example:
  .venv/bin/python scripts/compare_binance_dydx_spread.py --samples 50 --interval 0.5
"""

import argparse
import statistics
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import requests


DYDX_BASES = {
    "mainnet": "https://indexer.dydx.trade",
    "testnet": "https://indexer.v4testnet.dydx.exchange",
}

BINANCE_BASES = {
    "futures": ("https://fapi.binance.com", "/fapi/v1/depth"),
    "spot": ("https://api.binance.com", "/api/v3/depth"),
}


def _mid(bid: float, ask: float) -> float:
    if bid <= 0 or ask <= 0:
        return 0.0
    return (bid + ask) / 2


def _spread_bps(bid: float, ask: float) -> float:
    mid = _mid(bid, ask)
    if mid <= 0:
        return 0.0
    return (ask - bid) / mid * 10000


def _parse_binance_orderbook(data: Dict) -> Tuple[float, float]:
    bids = data.get("bids") or []
    asks = data.get("asks") or []
    bid = float(bids[0][0]) if bids else 0.0
    ask = float(asks[0][0]) if asks else 0.0
    return bid, ask


def _parse_dydx_orderbook(data: Dict) -> Tuple[float, float]:
    bids = data.get("bids") or []
    asks = data.get("asks") or []
    bid = float(bids[0].get("price", 0)) if bids else 0.0
    ask = float(asks[0].get("price", 0)) if asks else 0.0
    return bid, ask


def _fetch_binance_orderbook(
    session: requests.Session,
    market: str,
    symbol: str,
    limit: int,
    timeout: float,
) -> Tuple[float, float]:
    base_url, path = BINANCE_BASES[market]
    resp = session.get(
        f"{base_url}{path}",
        params={"symbol": symbol, "limit": limit},
        timeout=timeout,
    )
    resp.raise_for_status()
    return _parse_binance_orderbook(resp.json())


def _fetch_dydx_orderbook(
    session: requests.Session,
    network: str,
    symbol: str,
    timeout: float,
) -> Tuple[float, float]:
    base_url = DYDX_BASES[network]
    resp = session.get(
        f"{base_url}/v4/orderbooks/perpetualMarket/{symbol}",
        timeout=timeout,
    )
    resp.raise_for_status()
    return _parse_dydx_orderbook(resp.json())


def _summarize(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"avg": 0.0, "median": 0.0, "min": 0.0, "max": 0.0, "stdev": 0.0}
    return {
        "avg": statistics.fmean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Binance vs dYdX spread (bps)")
    parser.add_argument("--samples", type=int, default=40, help="Number of samples")
    parser.add_argument("--interval", type=float, default=0.5, help="Seconds between samples")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout seconds")
    parser.add_argument("--binance-market", choices=["futures", "spot"], default="futures")
    parser.add_argument("--binance-symbol", default="BTCUSDT")
    parser.add_argument("--dydx-network", choices=["mainnet", "testnet"], default="mainnet")
    parser.add_argument("--dydx-symbol", default="BTC-USD")
    parser.add_argument("--limit", type=int, default=5, help="Orderbook depth limit")
    parser.add_argument("--verbose", action="store_true", help="Print each sample")
    args = parser.parse_args()

    binance_bps: List[float] = []
    dydx_bps: List[float] = []
    delta_bps: List[float] = []
    errors = 0
    tighter_binance = 0
    tighter_dydx = 0
    ties = 0
    epsilon = 1e-6

    print(
        "Comparing spreads:",
        f"binance={args.binance_market}({args.binance_symbol})",
        f"dydx={args.dydx_network}({args.dydx_symbol})",
        f"samples={args.samples}",
        f"interval={args.interval}s",
    )

    with requests.Session() as session:
        for idx in range(args.samples):
            try:
                bin_bid, bin_ask = _fetch_binance_orderbook(
                    session,
                    market=args.binance_market,
                    symbol=args.binance_symbol,
                    limit=args.limit,
                    timeout=args.timeout,
                )
                dydx_bid, dydx_ask = _fetch_dydx_orderbook(
                    session,
                    network=args.dydx_network,
                    symbol=args.dydx_symbol,
                    timeout=args.timeout,
                )
            except requests.RequestException as exc:
                errors += 1
                if args.verbose:
                    print(f"[{idx + 1:02d}] error: {exc}")
                time.sleep(args.interval)
                continue

            if bin_bid <= 0 or bin_ask <= 0 or dydx_bid <= 0 or dydx_ask <= 0:
                errors += 1
                if args.verbose:
                    print(f"[{idx + 1:02d}] error: missing bid/ask")
                time.sleep(args.interval)
                continue

            bps_binance = _spread_bps(bin_bid, bin_ask)
            bps_dydx = _spread_bps(dydx_bid, dydx_ask)
            delta = bps_binance - bps_dydx

            binance_bps.append(bps_binance)
            dydx_bps.append(bps_dydx)
            delta_bps.append(delta)

            if abs(delta) <= epsilon:
                ties += 1
            elif delta < 0:
                tighter_binance += 1
            else:
                tighter_dydx += 1

            if args.verbose:
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(
                    f"[{idx + 1:02d}] {ts} "
                    f"binance={bps_binance:.3f}bps "
                    f"dydx={bps_dydx:.3f}bps "
                    f"delta={delta:+.3f}bps"
                )

            time.sleep(args.interval)

    total = len(binance_bps)
    print()
    print("Summary")
    print("-" * 60)
    print(f"Samples: {total} (errors: {errors})")

    bin_stats = _summarize(binance_bps)
    dydx_stats = _summarize(dydx_bps)
    delta_stats = _summarize(delta_bps)

    print(
        "Binance spread bps:",
        f"avg={bin_stats['avg']:.3f}",
        f"median={bin_stats['median']:.3f}",
        f"min={bin_stats['min']:.3f}",
        f"max={bin_stats['max']:.3f}",
        f"stdev={bin_stats['stdev']:.3f}",
    )
    print(
        "dYdX spread bps:",
        f"avg={dydx_stats['avg']:.3f}",
        f"median={dydx_stats['median']:.3f}",
        f"min={dydx_stats['min']:.3f}",
        f"max={dydx_stats['max']:.3f}",
        f"stdev={dydx_stats['stdev']:.3f}",
    )
    print(
        "Delta (binance - dydx) bps:",
        f"avg={delta_stats['avg']:.3f}",
        f"median={delta_stats['median']:.3f}",
        f"min={delta_stats['min']:.3f}",
        f"max={delta_stats['max']:.3f}",
        f"stdev={delta_stats['stdev']:.3f}",
    )

    if total > 0:
        print(
            "Tighter spread counts:",
            f"binance={tighter_binance} ({tighter_binance / total * 100:.1f}%)",
            f"dydx={tighter_dydx} ({tighter_dydx / total * 100:.1f}%)",
            f"ties={ties} ({ties / total * 100:.1f}%)",
        )


if __name__ == "__main__":
    main()
