# AI Prediction & RL Dev (Isolated)

This folder is a sandbox for the AI prediction / RL workstream. It does not touch existing trading runtime code.

## Goals
- Build a supervised predictor (direction/regime) and an RL agent that map to the multi-mode strategy knobs.
- Target profiles (event-day, crypto-style): 1d 10–30% ROI with drawdown guards; optional small-size high-leverage probes for 10–20x on extreme confluence; baseline uplift of win-rate (+5–10pp) and Sharpe/Sortino (+15%).

## Files
- `config.py` — Common paths/hyperparams.
- `data_pipeline.py` — Load price & news, align to UTC 1m grid, build features/labels, export Parquet/feather.
- `train_supervised.py` — Skeleton for LightGBM/XGBoost/Transformer-style models; logs metrics and feature importance.
- `train_rl.py` — Skeleton for gym-like env wrapping hybrid backtester; behavior cloning + offline RL entrypoints.

## Safety rails
- Read-only on existing data; outputs go to `ai_dev/artifacts/*`.
- No changes to `config/trading_strategies_dynamic.json` or runtime scripts.
- Fallbacks if news is missing; will still build price-only features.

## Quick start (all optional; uses `.venv`)
```bash
source .venv/bin/activate
python ai_dev/data_pipeline.py --help
python ai_dev/train_supervised.py --help
python ai_dev/train_rl.py --help
```
