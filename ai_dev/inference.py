"""
Lightweight inference helper for supervised models produced by train_supervised.py.
Reads a saved pickle model and the feature dataset, runs the latest N samples, and prints predictions.
Non-intrusive: does not write to runtime configs or trading code.
"""
from __future__ import annotations

import argparse
import pickle
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

from ai_dev import config


def select_features(df: pd.DataFrame, label_col: str) -> List[str]:
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    drop_cols = [label_col] + [c for c in numeric_cols if c.startswith("future_ret_")]
    return [c for c in numeric_cols if c not in drop_cols]


def load_model(path: Path):
    with path.open("rb") as f:
        model = pickle.load(f)
    return model


def predict(model, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray | None]:
    proba = None
    pred_raw = model.predict(X)
    if isinstance(pred_raw, np.ndarray) and pred_raw.ndim == 2:
        proba = pred_raw
        pred = np.argmax(pred_raw, axis=1)
    elif hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)
        pred = np.argmax(proba, axis=1)
    else:
        pred = pred_raw
    return np.array(pred), proba


def main():
    parser = argparse.ArgumentParser(description="Run inference on latest rows of the feature dataset.")
    parser.add_argument("--dataset", type=Path, default=config.DEFAULT_OUTPUT, help="Parquet with features/labels.")
    parser.add_argument("--model", type=Path, required=True, help="Pickle model file from train_supervised.py.")
    parser.add_argument("--label-col", type=str, default="label_15m_0.25", help="Label column used in training.")
    parser.add_argument("--rows", type=int, default=100, help="Number of latest rows to score.")
    args = parser.parse_args()

    df = pd.read_parquet(args.dataset, engine="pyarrow")
    if args.label_col not in df.columns:
        raise ValueError(f"Label column {args.label_col} not found in dataset.")
    features = select_features(df, args.label_col)
    if not features:
        raise ValueError("No features selected.")

    sample = df.tail(args.rows)
    X = sample[features].values

    model = load_model(args.model)
    pred, proba = predict(model, X)

    print(f"Scored {len(sample)} rows. Showing last 5:")
    for i, (_, row) in enumerate(sample.iloc[-5:].iterrows()):
        idx = len(sample) - 5 + i
        p = pred[idx]
        prob_str = ""
        if proba is not None:
            prob_vec = proba[idx]
            prob_str = f" proba={np.round(prob_vec, 3).tolist()}"
        print(f"{row['timestamp'] if 'timestamp' in row else idx}: pred={p}{prob_str}")


if __name__ == "__main__":
    main()
