"""
Trajectory exporter: builds (obs, action, reward, done) tuples from feature dataset for BC/offline RL.
Uses future returns as reward proxy; action derived from label (-1=SHORT, 0=HOLD, 1=LONG).
Non-intrusive: reads dataset, writes to ai_dev/artifacts; does not touch runtime code.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

from ai_dev import config


def select_features(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    drop_cols = [c for c in numeric_cols if c.startswith("future_ret_")] + [c for c in numeric_cols if c.startswith("label_")]
    feat_cols = [c for c in numeric_cols if c not in drop_cols]
    return df[feat_cols].to_numpy(dtype=np.float32), feat_cols


def export_trajectories(
    df: pd.DataFrame,
    label_col: str,
    future_col: str,
    fee_rate: float = 0.0005,
    leverage: int = 5,
    position_size: float = 1.0,
) -> List[dict]:
    features, feat_cols = select_features(df)
    labels = df[label_col].values
    future = df[future_col].values
    traj = []
    for i in range(len(df) - 1):
        obs = features[i].tolist()
        label = labels[i]
        if label == 1:
            action = 1
            reward = future[i] / 100 * leverage * position_size - fee_rate * leverage * position_size
        elif label == -1:
            action = 2
            reward = -future[i] / 100 * leverage * position_size - fee_rate * leverage * position_size
        else:
            action = 0
            reward = 0.0
        done = False
        info = {"ret": float(future[i])}
        traj.append({"obs": obs, "action": int(action), "reward": float(reward), "done": done, "info": info})
    # Mark last step done
    traj[-1]["done"] = True
    return traj


def main():
    parser = argparse.ArgumentParser(description="Export trajectories from dataset for BC/offline RL.")
    parser.add_argument("--dataset", type=Path, default=config.DEFAULT_OUTPUT, help="Feature dataset parquet.")
    parser.add_argument("--label-col", type=str, default="label_15m_0.25", help="Label column (-1/0/1).")
    parser.add_argument("--future-col", type=str, default="future_ret_15m", help="Future return column for reward.")
    parser.add_argument("--output", type=Path, default=config.ARTIFACT_DIR / "trajectories.json", help="Output json.")
    parser.add_argument("--limit", type=int, default=200000, help="Row limit for faster export.")
    parser.add_argument("--leverage", type=int, default=5, help="Reward leverage factor.")
    parser.add_argument("--position-size", type=float, default=1.0, help="Reward position size factor.")
    parser.add_argument("--fee-rate", type=float, default=0.0005, help="Fee rate used in reward.")
    args = parser.parse_args()

    df = pd.read_parquet(args.dataset, engine="pyarrow")
    if args.limit:
        df = df.iloc[: args.limit]
    if args.label_col not in df.columns or args.future_col not in df.columns:
        raise ValueError("Missing label or future return column in dataset.")

    traj = export_trajectories(
        df,
        label_col=args.label_col,
        future_col=args.future_col,
        fee_rate=args.fee_rate,
        leverage=args.leverage,
        position_size=args.position_size,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        json.dump(traj, f)
    print(f"Saved trajectories to {args.output} (steps {len(traj)}) using label {args.label_col} and future {args.future_col}")


if __name__ == "__main__":
    main()
