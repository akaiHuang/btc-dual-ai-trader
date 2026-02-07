"""
RL scaffold (isolated). Goal: wrap existing backtester into a gym-like env and enable BC + offline RL.
Non-intrusive: no changes to runtime trading code.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from ai_dev import config
from ai_dev.env_wrapper import DummyHybridEnv, EnvConfig
from ai_dev.replay_env import DatasetTradingEnv, ReplayConfig, load_dataset
from ai_dev.dataset_runner import DatasetRunner, DatasetRunnerConfig
from ai_dev.backtester_runner import BacktesterRunner, BacktesterRunnerConfig
from ai_dev.hybrid_env import ActionSpec
import pandas as pd
from sklearn.linear_model import LogisticRegression  # type: ignore
from sklearn.preprocessing import StandardScaler  # type: ignore
from sklearn.pipeline import Pipeline  # type: ignore


def check_rl_libs():
    libs = {}
    try:
        import stable_baselines3 as sb3  # type: ignore
        libs["sb3"] = sb3
    except Exception:
        libs["sb3"] = None
    try:
        import d3rlpy  # type: ignore
        libs["d3rlpy"] = d3rlpy
    except Exception:
        libs["d3rlpy"] = None
    return libs


def run_dummy_episode():
    env = DummyHybridEnv(EnvConfig(obs_dim=8))
    obs = env.reset()
    obs, reward, done, info = env.step(0)
    print(f"Dummy episode -> obs shape {obs.shape}, reward {reward}, done {done}, info {info}")


def main():
    parser = argparse.ArgumentParser(description="RL scaffold (BC + offline RL).")
    parser.add_argument("--dataset", type=Path, default=config.DEFAULT_OUTPUT, help="Feature dataset for BC pretrain.")
    parser.add_argument("--episodes", type=int, default=1, help="Dummy episodes to run (plumbing test).")
    parser.add_argument("--label-col", type=str, default="label_15m_0.25", help="Label column for BC (maps to actions).")
    parser.add_argument("--future-col", type=str, default="future_ret_15m", help="Future return column for reward (env).")
    parser.add_argument("--bc-limit", type=int, default=200000, help="Row limit for BC training.")
    parser.add_argument("--hybrid-dataset-runner", action="store_true", help="Use HybridTradingEnv + DatasetRunner smoke test.")
    parser.add_argument("--hybrid-backtester-runner", action="store_true", help="Use HybridTradingEnv + BacktesterRunner (MarketReplayEngine) smoke test.")
    parser.add_argument("--backtester-start", type=str, default="2024-11-10", help="Backtester start_date (for backtester runner).")
    parser.add_argument("--backtester-end", type=str, default="2024-11-10", help="Backtester end_date (for backtester runner).")
    args = parser.parse_args()

    libs = check_rl_libs()
    print("RL libs detected:", {k: bool(v) for k, v in libs.items()})
    if libs["sb3"] is None and libs["d3rlpy"] is None:
        print("Note: stable-baselines3 or d3rlpy not installed. Install gymnasium + sb3/d3rlpy for RL training.")

    print("Plumbing test with DummyHybridEnv...")
    for _ in range(args.episodes):
        run_dummy_episode()

    print("\nBC quick start (LogReg multi-class on dataset actions):")
    df = pd.read_parquet(args.dataset, engine="pyarrow")
    if args.bc_limit:
        df = df.iloc[: args.bc_limit]
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    drop_cols = [args.label_col] + [c for c in numeric_cols if c.startswith("future_ret_")]
    feat_cols = [c for c in numeric_cols if c not in drop_cols]
    if args.label_col not in df.columns:
        raise ValueError(f"Label column {args.label_col} not found.")
    X = df[feat_cols].values
    y_raw = df[args.label_col].values
    # map -1->2 (SHORT), 0->0 (HOLD), 1->1 (LONG)
    y = np.where(y_raw == -1, 2, y_raw)
    split = int(len(df) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]
    bc = Pipeline([("scaler", StandardScaler(with_mean=False)), ("clf", LogisticRegression(max_iter=200, multi_class="multinomial"))])
    bc.fit(X_train, y_train)
    train_acc = bc.score(X_train, y_train)
    val_acc = bc.score(X_val, y_val)
    bc_path = config.ARTIFACT_DIR / f"bc_logreg_{args.label_col}.bin"
    import pickle

    with open(bc_path, "wb") as f:
        pickle.dump({"model": bc, "features": feat_cols, "label_col": args.label_col}, f)
    print(f"BC saved to {bc_path} | train_acc {train_acc:.4f} | val_acc {val_acc:.4f}")

    print("\nReplay env sanity check:")
    cfg = ReplayConfig(dataset=args.dataset, future_col=args.future_col, leverage=5, position_size=1.0, max_steps=200)
    env = DatasetTradingEnv(df, cfg)
    obs = env.reset()
    total_reward = 0.0
    for _ in range(200):
        act = np.random.randint(0, 3)
        obs, rew, done, info = env.step(act)
        total_reward += rew
        if done:
            break
    print(f"Replay env ran {env.steps} steps, total_reward {total_reward:.4f}, last info {info}")

    if args.hybrid_dataset_runner or args.hybrid_backtester_runner:
        print("\nHybridTradingEnv smoke test:")
        action_specs = [
            ActionSpec(mode="HOLD", position_size=0.0, leverage=1),
            ActionSpec(mode="LONG", position_size=1.0, leverage=5),
            ActionSpec(mode="SHORT", position_size=1.0, leverage=5),
        ]
        try:
            from ai_dev.hybrid_env import HybridEnvConfig, HybridTradingEnv
            if args.hybrid_backtester_runner:
                runner = BacktesterRunner(
                    BacktesterRunnerConfig(
                        start_date=args.backtester_start,
                        end_date=args.backtester_end,
                        max_events=5000,
                        capital=100.0,
                    )
                )
                obs_dim = len(runner.reset()["features"])
            else:
                runner = DatasetRunner(DatasetRunnerConfig(dataset_path=args.dataset, future_col=args.future_col, max_steps=200))
                obs_dim = env.features.shape[1]
            env_cfg = HybridEnvConfig(obs_dim=obs_dim, action_specs=action_specs)
            env = HybridTradingEnv(env_cfg, runner)
            obs = env.reset()
            total = 0.0
            for _ in range(50):
                act = np.random.randint(0, len(action_specs))
                obs, rew, done, info = env.step(act)
                total += rew
                if done:
                    break
            print(f"Hybrid env ran, total_reward {total:.4f}, last info {info}")
        except ImportError as exc:
            print(f"Hybrid env skipped (gym/gymnasium not installed): {exc}")
        except Exception as exc:
            print(f"Hybrid env encountered error: {exc}")

    print("\nTODO to enable full RL training:")
    print("1) Implement HybridTradingEnv in ai_dev/env_wrapper.py using src/backtesting/hybrid_backtester.py.")
    print("2) Export real trajectories (obs, action, reward, done) from backtests; feed to BC/CQL/BCQ.")
    print("3) Train offline RL (CQL/BCQ) or SB3 SAC/PPO if installed; evaluate walk-forward (Sharpe/Sortino/MDD/事件日 ROI).")
    print("4) Map policy outputs to strategy knobs (direction bias/size/leverage) with existing VPIN/流動性/爆倉風控。")


if __name__ == "__main__":
    main()
