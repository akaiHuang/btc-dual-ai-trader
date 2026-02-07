"""
Export RL buffer using HybridTradingEnv + DatasetRunner (non-intrusive).
Creates a JSONL file with (obs, action, reward, done) for offline RL/BC.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ai_dev.dataset_runner import DatasetRunner, DatasetRunnerConfig
from ai_dev.backtester_runner import BacktesterRunner, BacktesterRunnerConfig
from ai_dev.hybrid_env import ActionSpec, HybridEnvConfig, HybridTradingEnv
from ai_dev import config


def main():
    parser = argparse.ArgumentParser(description="Export RL buffer via HybridTradingEnv + runner (dataset/backtester).")
    parser.add_argument("--dataset", type=Path, default=config.DEFAULT_OUTPUT, help="Feature dataset for DatasetRunner.")
    parser.add_argument("--future-col", type=str, default="future_ret_15m", help="Future return column (dataset runner).")
    parser.add_argument("--use-backtester", action="store_true", help="Use BacktesterRunner (MarketReplayEngine) instead of DatasetRunner.")
    parser.add_argument("--start-date", type=str, default="2024-11-10", help="Backtester start date.")
    parser.add_argument("--end-date", type=str, default="2024-11-10", help="Backtester end date.")
    parser.add_argument("--steps", type=int, default=5000, help="Max steps to export.")
    parser.add_argument("--output", type=Path, default=config.ARTIFACT_DIR / "rl_buffer.jsonl", help="Output JSONL path.")
    args = parser.parse_args()

    if args.use_backtester:
        runner = BacktesterRunner(
            BacktesterRunnerConfig(
                start_date=args.start_date,
                end_date=args.end_date,
                max_events=args.steps * 10,  # rough
            )
        )
        obs_dim = len(runner.reset()["features"])
    else:
        df = pd.read_parquet(args.dataset, engine="pyarrow")
        runner = DatasetRunner(DatasetRunnerConfig(dataset_path=args.dataset, future_col=args.future_col, max_steps=args.steps))
        obs_dim = runner.features.shape[1]

    # Build action specs for HOLD/LONG/SHORT
    action_specs = [
        ActionSpec(mode="HOLD", position_size=0.0, leverage=1),
        ActionSpec(mode="LONG", position_size=1.0, leverage=5),
        ActionSpec(mode="SHORT", position_size=1.0, leverage=5),
    ]
    env_cfg = HybridEnvConfig(obs_dim=obs_dim, action_specs=action_specs)
    env = HybridTradingEnv(env_cfg, runner)

    obs = env.reset()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        for _ in range(args.steps):
            act = np.random.randint(0, len(action_specs))
            obs, rew, done, info = env.step(act)
            rec = {
                "obs": obs.tolist(),
                "action": int(act),
                "reward": float(rew),
                "done": bool(done),
                "info": info,
            }
            f.write(json.dumps(rec) + "\n")
            if done:
                break
    print(f"Exported buffer to {args.output}")


if __name__ == "__main__":
    main()
