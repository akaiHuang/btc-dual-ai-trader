"""
Apply strategy bias to a copy of trading_strategies_dynamic.json (non-intrusive by default).
- Reads config/trading_strategies_dynamic.json
- Applies direction/position_size_multiplier/leverage/TP/SL adjustments to target modes
- Writes patched config to ai_dev/artifacts/trading_strategies_dynamic_patched.json (or --inplace to overwrite)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict

from ai_dev import config

ROOT = config.ROOT


def load_json(path: Path) -> Dict:
    with path.open() as f:
        return json.load(f)


def apply_bias(cfg: Dict, bias: Dict, modes: list, pos_mult: float, lev: int) -> Dict:
    direction = bias.get("direction", "NEUTRAL")
    for m in modes:
        if "modes" not in cfg or m not in cfg["modes"]:
            continue
        mode_cfg = cfg["modes"][m]
        mode_cfg["position_size"] = round(mode_cfg.get("position_size", 0.5) * pos_mult, 4)
        mode_cfg["leverage"] = max(1, int(lev))
        # Optional TP/SL multipliers (keep risk filters intact)
        if "tp_pct" in mode_cfg:
            mode_cfg["tp_pct"] = round(mode_cfg["tp_pct"] * pos_mult, 4)
        if "sl_pct" in mode_cfg:
            mode_cfg["sl_pct"] = round(mode_cfg["sl_pct"] * max(1.0, 1 / pos_mult), 4)
        # Optional: encode bias direction for downstream use
        mode_cfg["bias_direction"] = direction
        mode_cfg["bias_confidence"] = bias.get("confidence", 0.0)
    return cfg


def main():
    parser = argparse.ArgumentParser(description="Apply bias to trading_strategies_dynamic.json (copy only).")
    parser.add_argument("--bias-file", type=Path, default=config.ARTIFACT_DIR / "strategy_bias.json", help="Bias JSON file.")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "trading_strategies_dynamic.json", help="Source config.")
    parser.add_argument("--modes", nargs="+", default=["M2_NORMAL_PRIME", "M7_BREAKOUT_SNIPER"], help="Modes to patch.")
    parser.add_argument("--pos-mult", type=float, default=1.2, help="Position size multiplier.")
    parser.add_argument("--leverage", type=int, default=5, help="Leverage to set.")
    parser.add_argument("--output", type=Path, default=config.ARTIFACT_DIR / "trading_strategies_dynamic_patched.json", help="Patched output path.")
    parser.add_argument("--inplace", action="store_true", help="Overwrite the source config (use with caution).")
    args = parser.parse_args()

    if not args.bias_file.exists():
        raise FileNotFoundError(f"Bias file not found: {args.bias_file}")
    bias = load_json(args.bias_file)
    cfg = load_json(args.config)
    patched = apply_bias(cfg, bias, args.modes, args.pos_mult, args.leverage)

    out_path = args.config if args.inplace else args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(patched, f, ensure_ascii=False, indent=2)
    print(f"Patched config written to {out_path} for modes {args.modes} with bias {bias}")


if __name__ == "__main__":
    main()
