"""
Isolated data pipeline to build supervised features/labels for AI experiments.
 - Reads 1m BTCUSDT parquet (default: data/historical/BTCUSDT_1m.parquet)
 - Optional news ingestion (counts per hour per source); safe fallback if missing
 - Outputs a single Parquet with aligned features + labels to ai_dev/artifacts
Does not modify existing runtime code.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, List, Optional

import numpy as np
import pandas as pd

from ai_dev import config


def load_price(path: Path, limit: Optional[int] = None) -> pd.DataFrame:
    df = pd.read_parquet(path, engine="pyarrow")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    if limit:
        df = df.iloc[:limit]
    return df


def _rolling_features(df: pd.DataFrame, col: str, windows: Iterable[int], prefix: str) -> pd.DataFrame:
    for w in windows:
        df[f"{prefix}_mean_{w}m"] = df[col].rolling(w, min_periods=max(1, w // 2)).mean()
        df[f"{prefix}_std_{w}m"] = df[col].rolling(w, min_periods=max(1, w // 2)).std()
    return df


def build_price_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ret_1m"] = df["close"].pct_change() * 100
    for w in config.RET_WINDOWS:
        df[f"ret_{w}m"] = df["close"].pct_change(w) * 100
    # Rolling volatility on returns
    df = _rolling_features(df, "ret_1m", config.VOL_WINDOWS, "ret")
    # Volume Z-score
    vol_mean = df["volume"].rolling(60, min_periods=30).mean()
    vol_std = df["volume"].rolling(60, min_periods=30).std()
    df["volume_zscore_60m"] = (df["volume"] - vol_mean) / (vol_std + 1e-9)
    return df


def build_labels(df: pd.DataFrame, horizons: List[int], thresholds: List[float]) -> pd.DataFrame:
    df = df.copy()
    for h in horizons:
        future = df["close"].shift(-h)
        ret = (future / df["close"] - 1) * 100
        df[f"future_ret_{h}m"] = ret
        # Discrete bins: -1, 0, 1 based on thresholds
        for th in thresholds:
            col = f"label_{h}m_{th:.2f}"
            df[col] = 0
            df.loc[ret >= th, col] = 1
            df.loc[ret <= -th, col] = -1
    return df


def ingest_news_counts(news_dir: Path, tz: str = "UTC") -> pd.DataFrame:
    """
    Minimal news ingestion: count articles per hour per source.
    Designed to be cheap; no embeddings here (can be added separately).
    """
    if not news_dir.exists():
        return pd.DataFrame(columns=["timestamp", "news_count"])

    rows = []
    for source in news_dir.iterdir():
        if not source.is_dir():
            continue
        for year_dir in source.iterdir():
            if not year_dir.is_dir():
                continue
            for file in year_dir.glob("*.json"):
                try:
                    with file.open() as f:
                        obj = json.load(f)
                except Exception:
                    continue
                articles = obj.get("articles") if isinstance(obj, dict) else obj
                if not isinstance(articles, list):
                    continue
                for art in articles:
                    ts = art.get("date") or art.get("scraped_at")
                    if not ts:
                        continue
                    try:
                        dt = pd.to_datetime(ts).tz_localize("UTC") if pd.to_datetime(ts).tzinfo is None else pd.to_datetime(ts).tz_convert("UTC")
                    except Exception:
                        continue
                    rows.append({"timestamp": dt, "source": source.name})
    if not rows:
        return pd.DataFrame(columns=["timestamp", "news_count"])
    df = pd.DataFrame(rows)
    df["timestamp"] = df["timestamp"].dt.floor("H")
    grouped = df.groupby("timestamp").size().reset_index(name="news_count")
    return grouped


def merge_news(price_df: pd.DataFrame, news_df: pd.DataFrame) -> pd.DataFrame:
    if news_df.empty:
        price_df["news_count"] = 0
        return price_df
    merged = price_df.copy()
    merged["hour"] = merged["timestamp"].dt.floor("H")
    merged = merged.merge(news_df, left_on="hour", right_on="timestamp", how="left", suffixes=("", "_news"))
    merged = merged.drop(columns=["timestamp_news", "hour"])
    merged["news_count"] = merged["news_count"].fillna(0)
    return merged


def main():
    parser = argparse.ArgumentParser(description="Build AI supervised dataset (isolated).")
    parser.add_argument("--price-file", type=Path, default=config.DEFAULT_PRICE_FILE, help="Parquet OHLCV (1m).")
    parser.add_argument("--output", type=Path, default=config.DEFAULT_OUTPUT, help="Output Parquet path.")
    parser.add_argument("--limit", type=int, default=None, help="Optional row limit for quick runs.")
    parser.add_argument("--with-news", action="store_true", help="Include hourly news counts (lightweight).")
    args = parser.parse_args()

    print(f"Loading price: {args.price_file}")
    price_df = load_price(args.price_file, limit=args.limit)
    print(f"Price rows: {len(price_df):,}")

    print("Building price features...")
    feat_df = build_price_features(price_df)

    print("Building labels...")
    feat_df = build_labels(feat_df, config.FUTURE_HORIZONS_MIN, config.RET_THRESHOLDS)

    if args.with_news:
        print(f"Loading news counts from {config.NEWS_DIR} ...")
        news_df = ingest_news_counts(config.NEWS_DIR)
        print(f"News rows: {len(news_df):,}")
        feat_df = merge_news(feat_df, news_df)
    else:
        feat_df["news_count"] = 0

    # Drop rows with NaN from rolling/shift
    before = len(feat_df)
    feat_df = feat_df.dropna().reset_index(drop=True)
    print(f"Dropped {before - len(feat_df):,} rows due to NaN; final {len(feat_df):,}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    feat_df.to_parquet(args.output, index=False)
    print(f"Saved features to {args.output}")


if __name__ == "__main__":
    main()
