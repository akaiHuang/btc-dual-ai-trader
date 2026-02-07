"""
Policy adapter: convert model predictions into strategy bias JSON (non-intrusive).
Reads a trained model and dataset, scores latest rows, and writes a bias file that
can be manually merged into trading_strategies_dynamic.json (or read by a future hook).

Output example (ai_dev/artifacts/strategy_bias.json):
{
  "timestamp": "...",
  "direction": "LONG" | "SHORT" | "NEUTRAL",
  "confidence": 0.0-1.0,
  "position_size_multiplier": 1.0,
  "leverage": 5
}
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

from ai_dev import config
from ai_dev.inference import select_features, predict


def load_model(path: Path):
    with path.open("rb") as f:
        obj = pickle.load(f)
    # Some saved objects may wrap model in dict
    if isinstance(obj, dict) and "model" in obj:
        return obj["model"]
    return obj


def direction_from_probs(probs: np.ndarray) -> Tuple[str, float]:
    """
    probs shape (num_classes,) for labels mapped to [-1,0,1] or shifted classes.
    We infer direction by argmax: 0 -> NEUTRAL, 1 -> LONG, 2 -> SHORT (or if 3 classes).
    """
    if probs.shape[0] == 2:
        # binary case: class 0/1
        idx = int(np.argmax(probs))
        if idx == 0:
            return "NEUTRAL", float(probs[idx])
        else:
            return "LONG", float(probs[idx])
    idx = int(np.argmax(probs))
    if idx == 0:
        return "NEUTRAL", float(probs[idx])
    if idx == 1:
        return "LONG", float(probs[idx])
    return "SHORT", float(probs[idx])


def main():
    parser = argparse.ArgumentParser(description="Generate strategy bias JSON from model predictions (non-intrusive).")
    parser.add_argument("--dataset", type=Path, default=config.DEFAULT_OUTPUT, help="Feature dataset parquet.")
    parser.add_argument("--model", type=Path, required=True, help="Model pickle.")
    parser.add_argument("--label-col", type=str, default="label_15m_0.25", help="Label column for feature selection.")
    parser.add_argument("--rows", type=int, default=100, help="Number of latest rows to score.")
    parser.add_argument("--output", type=Path, default=config.ARTIFACT_DIR / "strategy_bias.json", help="Output JSON file.")
    parser.add_argument("--multiplier-long", type=float, default=1.2, help="Position size multiplier when long bias.")
    parser.add_argument("--multiplier-short", type=float, default=1.2, help="Position size multiplier when short bias.")
    parser.add_argument("--base-leverage", type=int, default=5, help="Base leverage for bias suggestion.")
    args = parser.parse_args()

    df = pd.read_parquet(args.dataset, engine="pyarrow")
    if args.label_col not in df.columns:
        raise ValueError(f"Label column {args.label_col} not found.")
    sample = df.tail(args.rows)
    features = select_features(df, args.label_col)
    X = sample[features].values

    model = load_model(args.model)
    preds, proba = predict(model, X)
    if proba is None:
        raise ValueError("Model does not provide probabilities; cannot compute confidence.")

    # Use last row prediction as latest signal
    last_prob = proba[-1]
    direction, conf = direction_from_probs(last_prob)
    if direction == "LONG":
        mult = args.multiplier_long
        lev = args.base_leverage
    elif direction == "SHORT":
        mult = args.multiplier_short
        lev = args.base_leverage
    else:
        mult = 1.0
        lev = max(1, args.base_leverage // 2)

    bias = {
        "timestamp": str(sample["timestamp"].iloc[-1]) if "timestamp" in sample else "",
        "direction": direction,
        "confidence": round(conf, 4),
        "position_size_multiplier": round(mult, 3),
        "leverage": lev,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        json.dump(bias, f, ensure_ascii=False, indent=2)
    print(f"Wrote bias to {args.output}: {bias}")


if __name__ == "__main__":
    main()
