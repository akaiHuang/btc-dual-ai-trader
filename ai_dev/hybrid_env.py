"""
HybridTradingEnv wrapper for backtester integration (non-intrusive).

Design:
- Accepts a backtester-like runner injected at init (to avoid coupling to runtime).
- Observation: user-provided feature vector + position state.
- Action space: discrete buckets mapping to (mode, position_size, leverage, tp/sl tweak).
- Reward: PnL minus fees/slippage minus optional risk penalties (MDD/連虧) returned by runner.

This file keeps placeholders so it won't break if backtester modules are absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import gymnasium as gym  # type: ignore
    from gymnasium import spaces  # type: ignore
except Exception:
    try:
        import gym  # type: ignore
        from gym import spaces  # type: ignore
    except Exception:  # pragma: no cover
        gym = None
        spaces = None


@dataclass
class ActionSpec:
    mode: str
    position_size: float
    leverage: int
    tp_pct: Optional[float] = None
    sl_pct: Optional[float] = None


@dataclass
class HybridEnvConfig:
    obs_dim: int
    action_specs: Sequence[ActionSpec]
    reward_shaper: Optional[Callable[[dict], float]] = None  # input: runner info dict


class HybridTradingEnv:
    """
    Skeleton for gym-like env backed by a trading runner.
    A runner should expose:
      reset() -> dict (with 'obs', 'position_state')
      step(ActionSpec) -> (obs_dict, info_dict) where info_dict includes pnl/fees/done flags
    """

    def __init__(self, config: HybridEnvConfig, runner):
        if gym is None:
            raise ImportError("gym/gymnasium not installed; install to use HybridTradingEnv.")
        self.cfg = config
        self.runner = runner
        self.action_space = spaces.Discrete(len(config.action_specs))
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(config.obs_dim,), dtype=np.float32)
        self._last_obs = None

    def _obs_from_dict(self, obs_dict: dict) -> np.ndarray:
        # Expect obs_dict already contains vector under "features"
        vec = obs_dict.get("features")
        if vec is None:
            raise ValueError("Runner must return obs_dict with 'features' vector.")
        arr = np.array(vec, dtype=np.float32)
        if arr.shape[0] != self.cfg.obs_dim:
            raise ValueError(f"Obs dim mismatch: expected {self.cfg.obs_dim}, got {arr.shape}")
        return arr

    def reset(self):
        obs_dict = self.runner.reset()
        obs = self._obs_from_dict(obs_dict)
        self._last_obs = obs
        return obs

    def step(self, action_idx: int):
        spec = self.cfg.action_specs[action_idx]
        obs_dict, info = self.runner.step(spec)
        obs = self._obs_from_dict(obs_dict)
        reward = info.get("pnl", 0.0) - info.get("fees", 0.0)
        if self.cfg.reward_shaper:
            reward = self.cfg.reward_shaper(info)
        done = bool(info.get("done", False))
        self._last_obs = obs
        return obs, float(reward), done, info


# Placeholder runner for smoke tests
class DummyRunner:
    def __init__(self, obs_dim: int):
        self.obs_dim = obs_dim
        self.ptr = 0

    def reset(self) -> dict:
        self.ptr = 0
        return {"features": np.zeros(self.obs_dim, dtype=np.float32), "position_state": {}}

    def step(self, spec: ActionSpec) -> Tuple[dict, dict]:
        self.ptr += 1
        obs = {"features": np.random.randn(self.obs_dim).astype(np.float32), "position_state": {"mode": spec.mode}}
        info = {"pnl": float(np.random.randn() * 0.01), "fees": 0.0, "done": self.ptr > 10}
        return obs, info
