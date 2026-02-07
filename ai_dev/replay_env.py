"""
Dataset-based trading environment (standalone, no runtime side effects).
Actions: 0=HOLD, 1=LONG, 2=SHORT. Reward uses future returns column.
Intended for quick RL/BC experiments when backtester integration is not ready.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

from ai_dev import config


@dataclass
class ReplayConfig:
    dataset: Path
    future_col: str = "future_ret_15m"
    position_size: float = 1.0
    leverage: int = 1
    fee_rate: float = 0.0005  # taker fee
    max_steps: int | None = None


class DatasetTradingEnv:
    def __init__(self, df: pd.DataFrame, cfg: ReplayConfig):
        self.cfg = cfg
        self.df = df.reset_index(drop=True)
        self.features, self.feature_cols = self._prepare_obs(df)
        self.returns = df[cfg.future_col].values
        self.ptr = 0
        self.steps = 0
        self.max_steps = cfg.max_steps or len(self.features)

    def _prepare_obs(self, df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        drop_cols = [c for c in numeric_cols if c.startswith("future_ret_")] + [c for c in numeric_cols if c.startswith("label_")]
        feature_cols = [c for c in numeric_cols if c not in drop_cols]
        return df[feature_cols].to_numpy(dtype=np.float32), feature_cols

    def reset(self):
        self.ptr = 0
        self.steps = 0
        return self.features[self.ptr]

    def step(self, action: int):
        # Compute reward from future return
        ret = self.returns[self.ptr]
        size = self.cfg.position_size
        lev = self.cfg.leverage
        fee = self.cfg.fee_rate * lev * size

        if action == 1:  # long
            reward = ret / 100 * lev * size - fee
        elif action == 2:  # short
            reward = -ret / 100 * lev * size - fee
        else:
            reward = 0.0

        self.ptr += 1
        self.steps += 1
        done = self.ptr >= len(self.features) - 1 or self.steps >= self.max_steps
        obs = self.features[self.ptr] if not done else np.zeros_like(self.features[0])
        info = {"ret": ret}
        return obs, reward, done, info


def load_dataset(path: Path, limit: int | None = None) -> pd.DataFrame:
    df = pd.read_parquet(path, engine="pyarrow")
    if limit:
        df = df.iloc[:limit]
    return df


def main():
    parser = argparse.ArgumentParser(description="Replay-style trading env over precomputed dataset.")
    parser.add_argument("--dataset", type=Path, default=config.DEFAULT_OUTPUT, help="Feature dataset parquet.")
    parser.add_argument("--future-col", type=str, default="future_ret_15m", help="Future return column for reward.")
    parser.add_argument("--limit", type=int, default=50000, help="Row limit for quick run.")
    parser.add_argument("--steps", type=int, default=200, help="Max steps per episode.")
    parser.add_argument("--leverage", type=int, default=5, help="Leverage for reward calc.")
    parser.add_argument("--position-size", type=float, default=1.0, help="Position size factor for reward.")
    args = parser.parse_args()

    df = load_dataset(args.dataset, limit=args.limit)
    cfg = ReplayConfig(dataset=args.dataset, future_col=args.future_col, leverage=args.leverage, position_size=args.position_size, max_steps=args.steps)
    env = DatasetTradingEnv(df, cfg)

    obs = env.reset()
    total_reward = 0.0
    for _ in range(args.steps):
        action = np.random.randint(0, 3)
        obs, reward, done, info = env.step(action)
        total_reward += reward
        if done:
            break
    print(f"Ran {env.steps} steps, total reward {total_reward:.4f}, last info {info}")
    print(f"Feature dims: {len(env.feature_cols)} | Using future col: {args.future_col}")


if __name__ == "__main__":
    main()
