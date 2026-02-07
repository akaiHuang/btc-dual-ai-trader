"""
Gym-like environment skeleton for RL experiments.
Safe by default: does not touch runtime trading code; can run in dummy mode without backtester.

How to use:
 - Implement `HybridTradingEnv` with real backtester integration (see TODOs).
 - For quick smoke tests, use DummyHybridEnv which samples random rewards.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

import numpy as np

try:
    import gym  # type: ignore
    from gym import spaces  # type: ignore
except Exception:  # pragma: no cover - fallback if gym not installed
    gym = None
    spaces = None


@dataclass
class ActionBucket:
    mode: str
    position_sizes: Sequence[float] = field(default_factory=lambda: (0.2, 0.4, 0.6))
    leverages: Sequence[int] = field(default_factory=lambda: (5, 10, 15))


@dataclass
class EnvConfig:
    obs_dim: int = 32  # placeholder; actual should match engineered feature count + position state
    action_buckets: List[ActionBucket] = field(default_factory=lambda: [ActionBucket(mode="M2_NORMAL_PRIME")])
    reward_fn: Optional[Callable] = None  # custom reward shaping hook


class DummyHybridEnv:
    """
    Minimal env that returns zero obs/reward and terminates immediately.
    Useful for plumbing tests when gym/backtester are unavailable.
    """

    def __init__(self, config: EnvConfig):
        self.config = config
        self.observation_space = None
        self.action_space = None

    def reset(self):
        return np.zeros(self.config.obs_dim, dtype=np.float32)

    def step(self, action):
        obs = np.zeros(self.config.obs_dim, dtype=np.float32)
        reward = 0.0
        done = True
        info = {}
        return obs, reward, done, info


class HybridTradingEnv:
    """
    TODO: Integrate with src/backtesting/hybrid_backtester.py to simulate trades.
    - obs: engineered features + position state
    - action: choose (mode, position_size, leverage) bucket and maybe tp/sl tweak
    - reward: PnL minus fees/slippage minus risk penalties
    """

    def __init__(self, config: EnvConfig):
        self.config = config
        if gym is None:
            raise ImportError("gym not installed; install gymnasium or gym to use HybridTradingEnv.")

        # Placeholder spaces; replace with real dimensions and discrete combos
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(config.obs_dim,), dtype=np.float32)
        self.action_space = spaces.Discrete(len(config.action_buckets))
        self._position_state = {"has_position": False, "entry_price": 0.0, "direction": 0}

    def reset(self):
        # TODO: reset backtester state and return first observation
        self._position_state = {"has_position": False, "entry_price": 0.0, "direction": 0}
        return np.zeros(self.config.obs_dim, dtype=np.float32)

    def step(self, action: int):
        # TODO: map action -> mode/size/leverage, run one step in backtester, compute reward
        obs = np.zeros(self.config.obs_dim, dtype=np.float32)
        reward = 0.0
        done = True
        info = {"position_state": self._position_state}
        return obs, reward, done, info
