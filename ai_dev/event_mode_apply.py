"""
Apply event-mode boost to a copy of trading_strategies_dynamic.json (non-intrusive by default).
- Uses latest strategy_bias.json to set direction/confidence metadata
- Adds/updates top-level event_mode block
- Multiplies position_size and leverage for target modes during event
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_dev import config

ROOT = config.ROOT


def load_json(path: Path):
    with path.open() as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Apply event-mode boost to strategy config.")
    parser.add_argument("--bias-file", type=Path, default=config.ARTIFACT_DIR / "strategy_bias.json", help="Bias JSON file.")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "trading_strategies_dynamic.json", help="Source config.")
    parser.add_argument("--modes", nargs="+", default=["M2_NORMAL_PRIME", "M7_BREAKOUT_SNIPER"], help="Modes to boost.")
    parser.add_argument("--pos-mult", type=float, default=1.3, help="Position size multiplier in event mode.")
    parser.add_argument("--lev-mult", type=float, default=1.5, help="Leverage multiplier in event mode.")
    parser.add_argument("--output", type=Path, default=config.ARTIFACT_DIR / "trading_strategies_dynamic_event.json", help="Output config.")
    parser.add_argument("--inplace", action="store_true", help="Overwrite source config (use with caution).")
    args = parser.parse_args()

    if not args.bias_file.exists():
        raise FileNotFoundError(f"Bias file not found: {args.bias_file}")
    bias = load_json(args.bias_file)
    cfg = load_json(args.config)

    cfg["event_mode"] = {
        "enabled": True,
        "timestamp": bias.get("timestamp", ""),
        "direction": bias.get("direction", "NEUTRAL"),
        "confidence": bias.get("confidence", 0.0),
        "boost": {
            "modes": args.modes,
            "position_size_multiplier": args.pos_mult,
            "leverage_multiplier": args.lev_mult,
        },
    }

    for m in args.modes:
        if "modes" not in cfg or m not in cfg["modes"]:
            continue
        mode_cfg = cfg["modes"][m]
        mode_cfg["position_size"] = round(mode_cfg.get("position_size", 0.5) * args.pos_mult, 4)
        mode_cfg["leverage"] = max(1, int(round(mode_cfg.get("leverage", 5) * args.lev_mult)))
        mode_cfg["bias_direction"] = bias.get("direction", "NEUTRAL")
        mode_cfg["bias_confidence"] = bias.get("confidence", 0.0)

    out_path = args.config if args.inplace else args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"Wrote event-mode config to {out_path}")


if __name__ == "__main__":
    main()
