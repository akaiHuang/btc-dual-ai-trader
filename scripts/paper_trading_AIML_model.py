#!/usr/bin/env python3
"""
AI/ML model-driven paper trading launcher (non-intrusive).
- Reads base config (default: config/trading_strategies_dynamic.json)
- Reads latest bias (ai_dev/artifacts/strategy_bias.json) if present
- Generates a new config with an AI mode (M_AI_MODEL) cloned from a base mode, adjusted by bias
- Runs paper_trading_hybrid_full.py with the generated config (via TRADING_CONFIG_PATH), leaving originals untouched
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "ai_dev" / "artifacts"
AI_CONFIG_DIR = ROOT / "ai_dev" / "config"
BASE_CONFIG = AI_CONFIG_DIR / "trading_strategies_dynamic_base.json"  # always a copy, never the original
ORIGINAL_CONFIG = ROOT / "config" / "trading_strategies_dynamic.json"
BIAS_FILE = ARTIFACTS / "strategy_bias.json"
HYBRID_SCRIPT = ROOT / "scripts" / "paper_trading_hybrid_full.py"
AI_CONFIG_OUT = ARTIFACTS / "trading_strategies_dynamic_ai.json"


def load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def ensure_base_copy() -> Path:
    AI_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not BASE_CONFIG.exists():
        if not ORIGINAL_CONFIG.exists():
            raise FileNotFoundError(f"Original config missing: {ORIGINAL_CONFIG}")
        BASE_CONFIG.write_text(ORIGINAL_CONFIG.read_text())
    return BASE_CONFIG


def pick_base_mode(cfg: dict, candidate: str = "M2_NORMAL_PRIME") -> str:
    modes = cfg.get("modes", {})
    if candidate in modes:
        return candidate
    if modes:
        return list(modes.keys())[0]
    raise ValueError("No modes found in config.")


def build_ai_mode(base_mode_cfg: dict, bias: dict) -> dict:
    """Clone full mode config; only adjust sizing/lever/TP/SL and annotate bias."""
    mode = deepcopy(base_mode_cfg)
    direction = bias.get("direction", "NEUTRAL")
    conf = bias.get("confidence", 0.0)
    pos_mult = bias.get("position_size_multiplier", 1.0)
    lev_bias = bias.get("leverage", mode.get("leverage", 5))

    mode["enabled"] = True
    # keep original type to avoid manager拒收
    mode["description"] = f"{mode.get('description','')} (AI bias)"
    mode["emoji"] = "🧠M_AI"
    mode["position_size"] = round(mode.get("position_size", 0.5) * pos_mult, 4)
    mode["leverage"] = max(1, int(lev_bias))
    # Adjust TP/SL conservatively
    if "tp_pct" in mode:
        mode["tp_pct"] = round(mode["tp_pct"] * pos_mult, 4)
    if "sl_pct" in mode:
        mode["sl_pct"] = round(mode["sl_pct"] * max(1.0, 1 / pos_mult), 4)

    mode["bias_direction"] = direction
    mode["bias_confidence"] = conf
    return mode


def write_ai_config(base_cfg: dict, ai_mode: dict) -> Path:
    cfg = deepcopy(base_cfg)
    cfg.setdefault("modes", {})
    cfg["modes"]["M_AI_MODEL"] = ai_mode
    AI_CONFIG_OUT.parent.mkdir(parents=True, exist_ok=True)
    with AI_CONFIG_OUT.open("w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return AI_CONFIG_OUT


def run_hybrid_with_swap(config_path: Path, minutes: float):
    """
    Safe swap: backup original config, copy AI config to runtime path, run hybrid, restore.
    """
    runtime_cfg = ORIGINAL_CONFIG
    backup = ORIGINAL_CONFIG.with_suffix(".bak")
    if not runtime_cfg.exists():
        raise FileNotFoundError(f"Runtime config missing: {runtime_cfg}")

    print(f"[swap] Backup {runtime_cfg} -> {backup}")
    backup.write_text(runtime_cfg.read_text())
    try:
        print(f"[swap] Apply AI config -> {runtime_cfg}")
        runtime_cfg.write_text(config_path.read_text())
        cmd = [sys.executable, str(HYBRID_SCRIPT), str(minutes)]
        print(f"Launching hybrid (swapped) for {minutes} minutes...")
        res = subprocess.run(cmd)
        if res.returncode != 0:
            print(f"Hybrid script exited with code {res.returncode}")
    finally:
        print(f"[swap] Restore {runtime_cfg} from {backup}")
        runtime_cfg.write_text(backup.read_text())
        backup.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Run hybrid paper trading with AI-only config (modes pruned).")
    parser.add_argument("--base-config", type=Path, default=BASE_CONFIG, help="Base trading config (copy, not original).")
    parser.add_argument("--bias-file", type=Path, default=BIAS_FILE, help="Bias JSON from policy_adapter.")
    parser.add_argument("--base-mode", type=str, default="M2_NORMAL_PRIME", help="Mode to clone for AI mode.")
    parser.add_argument("--minutes", type=float, default=0.5, help="Duration in hours (e.g., 0.5 = 30 minutes).")
    args = parser.parse_args()

    # Ensure we work on a copy, never the original
    if args.base_config == BASE_CONFIG:
        ensure_base_copy()
    base_cfg = load_json(args.base_config)
    bias = load_json(args.bias_file) if args.bias_file.exists() else {}
    base_mode_name = pick_base_mode(base_cfg, args.base_mode)
    ai_mode = build_ai_mode(base_cfg["modes"][base_mode_name], bias)

    # overwrite a single existing mode in a copy, keep others so manager accepts
    cfg_copy = deepcopy(base_cfg)
    cfg_copy["modes"] = cfg_copy.get("modes", {})
    cfg_copy["modes"][base_mode_name] = ai_mode
    cfg_path = write_ai_config(cfg_copy, ai_mode)

    print("===== AI Mode Summary =====")
    print(f"Bias: {bias if bias else 'None (neutral)'}")
    print(f"Cloned from mode: {base_mode_name}")
    print(f"AI mode leverage: {ai_mode.get('leverage')}, position_size: {ai_mode.get('position_size')}")
    print(f"TP: {ai_mode.get('tp_pct','N/A')} SL: {ai_mode.get('sl_pct','N/A')}")
    print(f"Config written to: {cfg_path}")
    print("===========================")

    run_hybrid_with_swap(cfg_path, args.minutes)


if __name__ == "__main__":
    main()
