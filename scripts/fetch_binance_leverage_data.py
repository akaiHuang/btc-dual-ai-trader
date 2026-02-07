#!/usr/bin/env python3
"""Fetch Binance Futures long/short, open interest, funding, and liquidation data.

This script is the data-ingestion half of Phase 4 (槓桿壓力與合約擁擠度指標).
It collects the raw metrics required to compute the liquidation pressure scores
(`L_long_liq`, `L_short_liq`) and stores them as JSON so other modules can consume
without repeatedly hitting the Binance API.

Usage (real network call):
    python scripts/fetch_binance_leverage_data.py --symbol BTCUSDT --outfile data/liquidation_pressure/latest_snapshot.json

Usage (offline demo payload, no network):
    python scripts/fetch_binance_leverage_data.py --demo --outfile data/liquidation_pressure/demo_snapshot.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

BINANCE_FUTURES_BASE = "https://fapi.binance.com"


@dataclass
class FetchStats:
    """Simple telemetry for logging."""

    elapsed_seconds: float
    endpoint_count: int
    symbol: str
    period: str
    limit: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "endpoint_count": self.endpoint_count,
            "symbol": self.symbol,
            "period": self.period,
            "limit": self.limit,
        }


class BinanceLeverageDataFetcher:
    """Thin wrapper around Binance Futures public endpoints we care about."""

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        period: str = "5m",
        limit: int = 30,
        force_limit: int = 50,
        timeout: float = 10.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.period = period
        self.limit = limit
        self.force_limit = force_limit
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "btn-liq-pressure/1.0"})

    # ------------------------ HTTP helpers ------------------------ #
    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        url = f"{BINANCE_FUTURES_BASE}{path}"
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    # ------------------------ Public endpoints ------------------------ #
    def fetch_global_long_short_ratio(self) -> List[Dict[str, Any]]:
        params = {"symbol": self.symbol, "period": self.period, "limit": self.limit}
        return self._get("/futures/data/globalLongShortAccountRatio", params)

    def fetch_top_long_short_ratio(self) -> List[Dict[str, Any]]:
        params = {"symbol": self.symbol, "period": self.period, "limit": self.limit}
        return self._get("/futures/data/topLongShortAccountRatio", params)

    def fetch_open_interest_history(self) -> List[Dict[str, Any]]:
        params = {"symbol": self.symbol, "period": self.period, "limit": self.limit}
        return self._get("/futures/data/openInterestHist", params)

    def fetch_recent_funding_rate(self) -> List[Dict[str, Any]]:
        params = {"symbol": self.symbol, "limit": max(1, min(self.limit, 100))}
        return self._get("/fapi/v1/fundingRate", params)

    def fetch_force_orders(self) -> List[Dict[str, Any]]:
        params = {"symbol": self.symbol, "limit": self.force_limit}
        try:
            return self._get("/fapi/v1/forceOrders", params)
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                print(f"⚠️ Warning: 401 Unauthorized for forceOrders. Skipping.", file=sys.stderr)
                return []
            raise

    def fetch_taker_long_short_ratio(self) -> List[Dict[str, Any]]:
        """
        🆕 主動買賣比 (Taker Buy/Sell Volume Ratio)
        
        這是爆倉偵測的關鍵指標！
        - buySellRatio > 1.5: 大量主動買入 → 空頭可能被軋
        - buySellRatio < 0.6: 大量主動賣出 → 多頭可能被爆
        
        API: GET /futures/data/takerlongshortRatio
        """
        params = {"symbol": self.symbol, "period": self.period, "limit": self.limit}
        try:
            return self._get("/futures/data/takerlongshortRatio", params)
        except requests.HTTPError as e:
            print(f"⚠️ Warning: takerlongshortRatio failed: {e}", file=sys.stderr)
            return []

    def fetch_top_position_ratio(self) -> List[Dict[str, Any]]:
        """
        🆕 頂級交易員持倉比 (Top Trader Position Ratio)
        
        顯示頂級交易員的多空持倉比，跟散戶反著做！
        
        API: GET /futures/data/topLongShortPositionRatio
        """
        params = {"symbol": self.symbol, "period": self.period, "limit": self.limit}
        try:
            return self._get("/futures/data/topLongShortPositionRatio", params)
        except requests.HTTPError as e:
            print(f"⚠️ Warning: topLongShortPositionRatio failed: {e}", file=sys.stderr)
            return []

    # ------------------------ Orchestration ------------------------ #
    def collect(self) -> Dict[str, Any]:
        start = time.time()
        payload = {
            "symbol": self.symbol,
            "period": self.period,
            "limit": self.limit,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "global_long_short": self.fetch_global_long_short_ratio(),
            "top_long_short": self.fetch_top_long_short_ratio(),
            "open_interest": self.fetch_open_interest_history(),
            "funding_rate": self.fetch_recent_funding_rate(),
            "force_orders": self.fetch_force_orders(),
            # 🆕 新增 API
            "taker_long_short": self.fetch_taker_long_short_ratio(),
            "top_position_ratio": self.fetch_top_position_ratio(),
        }
        elapsed = time.time() - start
        payload["fetch_stats"] = FetchStats(
            elapsed_seconds=elapsed,
            endpoint_count=7,  # 🆕 5 → 7
            symbol=self.symbol,
            period=self.period,
            limit=self.limit,
        ).to_dict()
        return payload


# ---------------------------------------------------------------------------
# Demo payload (offline fallback)
# ---------------------------------------------------------------------------

def build_demo_payload(symbol: str = "BTCUSDT", period: str = "5m", limit: int = 30) -> Dict[str, Any]:
    """Return a deterministic, human-readable payload for offline tests."""

    now = datetime(2025, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
    timestamps = [int(now.timestamp() * 1000) - 5 * 60 * 1000 * i for i in range(limit)][::-1]

    def ratio_entry(scale: float) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "longShortRatio": f"{scale:.4f}",
            "longAccount": f"{min(scale / (scale + 1), 0.95):.4f}",
            "shortAccount": f"{1 - min(scale / (scale + 1), 0.95):.4f}",
            "timestamp": timestamps[-1],
        }

    demo_long_short = [ratio_entry(1.35 + 0.02 * i) for i in range(limit)]
    demo_top_ratio = [ratio_entry(1.55 + 0.015 * i) for i in range(limit)]
    demo_open_interest = [
        {
            "symbol": symbol,
            "sumOpenInterest": f"{(350000 + i * 1200):.2f}",
            "sumOpenInterestValue": f"{(950000000 + i * 3500000):.2f}",
            "timestamp": timestamps[i],
        }
        for i in range(limit)
    ]
    demo_funding = [
        {
            "symbol": symbol,
            "fundingRate": "0.00045",
            "fundingTime": timestamps[-1],
        }
    ]
    demo_force_orders = [
        {
            "symbol": symbol,
            "orderId": 10_000 + i,
            "price": f"{36000 + i * 5:.2f}",
            "origQty": f"{2.5 + (i % 4) * 0.3:.4f}",
            "executedQty": f"{2.5 + (i % 4) * 0.3:.4f}",
            "averagePrice": f"{36000 + i * 5:.2f}",
            "side": "SELL" if i % 3 != 0 else "BUY",
            "time": timestamps[-1] - i * 60000,
            "status": "FILLED",
        }
        for i in range(min(40, limit * 2))
    ]

    return {
        "symbol": symbol,
        "period": period,
        "limit": limit,
        "collected_at": now.isoformat(),
        "global_long_short": demo_long_short,
        "top_long_short": demo_top_ratio,
        "open_interest": demo_open_interest,
        "funding_rate": demo_funding,
        "force_orders": demo_force_orders,
        "fetch_stats": {
            "elapsed_seconds": 0.0,
            "endpoint_count": 0,
            "symbol": symbol,
            "period": period,
            "limit": limit,
            "demo": True,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Binance Futures leverage/crowding data")
    parser.add_argument("--symbol", default="BTCUSDT", help="Perpetual symbol, e.g. BTCUSDT")
    parser.add_argument("--period", default="5m", help="Binance aggregation period (5m, 15m, 30m, 1h, 4h)")
    parser.add_argument("--limit", type=int, default=30, help="Number of historical points to pull for ratio/OI")
    parser.add_argument("--force-limit", type=int, default=50, help="Number of recent force orders to fetch")
    parser.add_argument("--outfile", default="data/liquidation_pressure/latest_snapshot.json", help="Output JSON path")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds")
    parser.add_argument("--demo", action="store_true", help="Generate deterministic payload without network calls")
    parser.add_argument("--pretty", action="store_true", help="Write pretty-printed JSON")
    parser.add_argument("--stdout", action="store_true", help="Also echo the JSON payload to stdout")
    return parser.parse_args(argv)


def save_payload(payload: Dict[str, Any], outfile: str, pretty: bool = False, echo: bool = False) -> None:
    path = Path(outfile)
    path.parent.mkdir(parents=True, exist_ok=True)

    dump_kwargs = {"ensure_ascii": False}
    if pretty:
        dump_kwargs.update({"indent": 2, "sort_keys": True})

    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, **dump_kwargs)

    if echo:
        json.dump(payload, sys.stdout, **dump_kwargs)
        sys.stdout.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if args.demo:
        payload = build_demo_payload(args.symbol, args.period, args.limit)
    else:
        fetcher = BinanceLeverageDataFetcher(
            symbol=args.symbol,
            period=args.period,
            limit=args.limit,
            force_limit=args.force_limit,
            timeout=args.timeout,
        )
        try:
            payload = fetcher.collect()
        except requests.RequestException as exc:
            print(f"❌ Binance fetch failed: {exc}", file=sys.stderr)
            return 2

    save_payload(payload, args.outfile, pretty=args.pretty, echo=args.stdout)
    print(
        f"✅ Saved leverage snapshot for {payload['symbol']} to {args.outfile}"
        f" | collected_at={payload['collected_at']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
