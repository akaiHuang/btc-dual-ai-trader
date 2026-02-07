"""
DatasetRunner: minimal backtester-like runner backed by a feature dataset.
Uses ActionSpec from hybrid_env to simulate trades:
 - mode/size/leverage are read but only leverage/size used in reward
 - reward = future_ret * leverage * size - fee
 - obs = feature vector for current index
This is a stand-in runner to plug into HybridTradingEnv before real backtester wiring.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

from ai_dev.hybrid_env import ActionSpec


@dataclass
class DatasetRunnerConfig:
    dataset_path: Path
    future_col: str = "future_ret_15m"
    fee_rate: float = 0.0005
    max_steps: int | None = None


class DatasetRunner:
    def __init__(self, cfg: DatasetRunnerConfig):
        self.cfg = cfg
        self.df = pd.read_parquet(cfg.dataset_path, engine="pyarrow")
        self.features, self.feature_cols = self._prepare_obs(self.df)
        self.future = self.df[cfg.future_col].values
        self.ptr = 0
        self.max_steps = cfg.max_steps or len(self.df) - 1

    def _prepare_obs(self, df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        drop_cols = [c for c in numeric_cols if c.startswith("future_ret_")] + [c for c in numeric_cols if c.startswith("label_")]
        feat_cols = [c for c in numeric_cols if c not in drop_cols]
        return df[feat_cols].to_numpy(dtype=np.float32), feat_cols

    def reset(self) -> dict:
        self.ptr = 0
        return {"features": self.features[self.ptr], "position_state": {}}

    def step(self, spec: ActionSpec) -> Tuple[dict, dict]:
        ret = self.future[self.ptr]
        size = spec.position_size
        lev = spec.leverage
        fee = self.cfg.fee_rate * lev * size
        if spec is None:
            pnl = 0.0
        elif spec.mode and spec.mode:
            # map mode to direction via leverage sign? we only know action index from env
            # here, we assume ActionSpec chosen by env already encodes direction via mode name
            # if mode contains "SHORT" we invert
            if "SHORT" in spec.mode.upper():
                pnl = -ret / 100 * lev * size - fee
            else:
                pnl = ret / 100 * lev * size - fee
        else:
            pnl = 0.0

        self.ptr += 1
        done = self.ptr >= len(self.features) - 1 or self.ptr >= self.max_steps
        obs = {"features": self.features[self.ptr], "position_state": {"mode": spec.mode, "ptr": self.ptr}}
        info = {"pnl": float(pnl), "fees": float(fee), "ret": float(ret), "done": done}
        return obs, info
