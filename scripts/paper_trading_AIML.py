#!/usr/bin/env python3
"""
AI/ML launcher for paper_trading_hybrid_full.py

Features:
- Picks the best available strategy config (event-mode -> patched -> base).
- Reads latest bias (if present) and prints direction/信心/倍數。
- Runs existing paper_trading_hybrid_full.py for quick real-time test, passing minutes argument.
- Non-intrusive: does not overwrite your base config; sets an env var hint (TRADING_CONFIG_PATH) for downstream use.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "ai_dev" / "artifacts"
BASE_CONFIG = ROOT / "config" / "trading_strategies_dynamic.json"
PATCHED_CONFIG = ARTIFACTS / "trading_strategies_dynamic_patched.json"
EVENT_CONFIG = ARTIFACTS / "trading_strategies_dynamic_event.json"
BIAS_FILE = ARTIFACTS / "strategy_bias.json"
HYBRID_SCRIPT = ROOT / "scripts" / "paper_trading_hybrid_full.py"


def pick_config(prefer_event: bool) -> Path:
    if prefer_event and EVENT_CONFIG.exists():
        return EVENT_CONFIG
    if PATCHED_CONFIG.exists():
        return PATCHED_CONFIG
    return BASE_CONFIG


def load_bias() -> dict:
    if not BIAS_FILE.exists():
        return {}
    try:
        return json.loads(BIAS_FILE.read_text())
    except Exception:
        return {}


def run_hybrid(minutes: float, config_path: Path):
    env = os.environ.copy()
    env["TRADING_CONFIG_PATH"] = str(config_path)
    cmd = [sys.executable, str(HYBRID_SCRIPT), str(minutes)]
    print(f"Launching hybrid with config={config_path} for {minutes} minutes...")
    res = subprocess.run(cmd, env=env)
    if res.returncode != 0:
        print(f"Hybrid script exited with code {res.returncode}")


def main():
    parser = argparse.ArgumentParser(description="Run paper_trading_hybrid_full with AI/ML patched configs.")
    parser.add_argument("--minutes", type=float, default=0.5, help="Run duration in hours (e.g., 0.5 = 30 minutes).")
    parser.add_argument("--prefer-event", action="store_true", help="Prefer event-mode config if available.")
    args = parser.parse_args()

    cfg = pick_config(args.prefer_event)
    bias = load_bias()

    print("===== AI/ML Bias Summary =====")
    if bias:
        print(
            f"Timestamp: {bias.get('timestamp','')}, Direction: {bias.get('direction','NEUTRAL')}, "
            f"Confidence: {bias.get('confidence',0)}, PosMult: {bias.get('position_size_multiplier',1)}, "
            f"Leverage: {bias.get('leverage',1)}"
        )
    else:
        print("No bias file found; running with base config.")
    print(f"Using config: {cfg}")
    print("==============================")

    run_hybrid(args.minutes, cfg)


if __name__ == "__main__":
    main()
