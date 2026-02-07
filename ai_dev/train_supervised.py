"""
Minimal supervised training scaffold (isolated from runtime).
 - Loads dataset built by data_pipeline.py
 - Time-based split (train first N%, validate last part)
 - Tries LightGBM/XGBoost/LogisticRegression in order; degrades gracefully
This is a starting point; extend with better models/feature selection as needed.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

import warnings

warnings.filterwarnings("ignore", category=UserWarning)

from ai_dev import config


def select_features(df: pd.DataFrame, label_col: str) -> List[str]:
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    drop_cols = [label_col] + [c for c in numeric_cols if c.startswith("future_ret_")]
    features = [c for c in numeric_cols if c not in drop_cols]
    return features


def load_model_lib():
    try:
        import lightgbm as lgb  # type: ignore

        return "lightgbm", lgb
    except Exception:
        try:
            import xgboost as xgb  # type: ignore

            return "xgboost", xgb
        except Exception:
            try:
                from sklearn.linear_model import LogisticRegression  # type: ignore
                from sklearn.pipeline import Pipeline  # type: ignore
                from sklearn.preprocessing import StandardScaler  # type: ignore

                return "sklearn", (LogisticRegression, Pipeline, StandardScaler)
            except Exception as exc:
                raise ImportError("No supported model library found (LightGBM/XGBoost/sklearn).") from exc


def train_lightgbm(X_train, y_train, X_val, y_val):
    import lightgbm as lgb

    train_set = lgb.Dataset(X_train, label=y_train)
    val_set = lgb.Dataset(X_val, label=y_val, reference=train_set)
    params = {
        "objective": "multiclass",
        "num_class": len(np.unique(y_train)),
        "learning_rate": 0.05,
        "num_leaves": 31,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 3,
        "metric": "multi_logloss",
        "verbosity": -1,
    }
    model = lgb.train(params, train_set, num_boost_round=300, valid_sets=[val_set])
    return model


def train_xgboost(X_train, y_train, X_val, y_val):
    import xgboost as xgb

    model = xgb.XGBClassifier(
        objective="multi:softprob",
        num_class=len(np.unique(y_train)),
        learning_rate=0.05,
        max_depth=6,
        subsample=0.8,
        colsample_bytree=0.8,
        n_estimators=400,
        eval_metric="mlogloss",
    )
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=50)
    return model


def train_sklearn(X_train, y_train, X_val, y_val):
    from sklearn.linear_model import LogisticRegression  # type: ignore
    from sklearn.pipeline import Pipeline  # type: ignore
    from sklearn.preprocessing import StandardScaler  # type: ignore

    pipe = Pipeline(
        [
            ("scaler", StandardScaler(with_mean=False)),
            ("clf", LogisticRegression(max_iter=200, multi_class="auto")),
        ]
    )
    pipe.fit(X_train, y_train)
    return pipe


def evaluate(model, X, y):
    proba = None
    pred_raw = model.predict(X)
    if isinstance(pred_raw, np.ndarray) and pred_raw.ndim == 2:
        # Multi-class probability or raw scores
        proba = pred_raw
        pred = np.argmax(pred_raw, axis=1)
    elif hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)
        pred = np.argmax(proba, axis=1)
    else:
        pred = pred_raw
    acc = (pred == y).mean()
    return acc, proba


def main():
    parser = argparse.ArgumentParser(description="Train supervised model on engineered features.")
    parser.add_argument("--dataset", type=Path, default=config.DEFAULT_OUTPUT, help="Parquet with features/labels.")
    parser.add_argument("--label-col", type=str, default="label_15m_0.25", help="Label column to predict.")
    parser.add_argument("--val-ratio", type=float, default=0.2, help="Holdout ratio from tail (time-split).")
    parser.add_argument("--limit", type=int, default=None, help="Optional row limit (head) for faster smoke tests.")
    args = parser.parse_args()

    df = pd.read_parquet(args.dataset, engine="pyarrow")
    if args.limit:
        df = df.iloc[: args.limit]
    if args.label_col not in df.columns:
        raise ValueError(f"Label column {args.label_col} not found in dataset.")
    features = select_features(df, args.label_col)
    if not features:
        raise ValueError("No features selected.")

    split_idx = int(len(df) * (1 - args.val_ratio))
    train_df = df.iloc[:split_idx]
    val_df = df.iloc[split_idx:]

    X_train, y_train = train_df[features].values, train_df[args.label_col].values
    X_val, y_val = val_df[features].values, val_df[args.label_col].values

    # Re-index labels to non-negative for multi-class learners if needed
    label_min = min(y_train.min(), y_val.min())
    label_shift = 0
    if label_min < 0:
        label_shift = int(-label_min)
        y_train = y_train + label_shift
        y_val = y_val + label_shift

    lib, handle = load_model_lib()
    print(f"Using model library: {lib}")

    if lib == "lightgbm":
        model = train_lightgbm(X_train, y_train, X_val, y_val)
    elif lib == "xgboost":
        model = train_xgboost(X_train, y_train, X_val, y_val)
    else:
        model = train_sklearn(X_train, y_train, X_val, y_val)

    train_acc, _ = evaluate(model, X_train, y_train)
    val_acc, _ = evaluate(model, X_val, y_val)
    print(f"Train acc: {train_acc:.4f} | Val acc: {val_acc:.4f}")

    out_path = config.ARTIFACT_DIR / f"model_{lib}_{args.label_col}.bin"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import pickle

    with open(out_path, "wb") as f:
        pickle.dump(model, f)
    print(f"Saved model to {out_path}")


if __name__ == "__main__":
    main()
