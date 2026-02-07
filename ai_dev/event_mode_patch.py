"""
Event mode patch generator (non-intrusive).
Reads strategy_bias.json and produces a JSON snippet to enable event/high-vol mode:
- Adjusts position_size_multiplier and leverage for target modes during event window.
- Does not modify any live config; prints and writes to ai_dev/artifacts/event_mode_patch.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_dev import config


def main():
    parser = argparse.ArgumentParser(description="Generate event-mode patch based on bias JSON.")
    parser.add_argument("--bias-file", type=Path, default=config.ARTIFACT_DIR / "strategy_bias.json", help="Bias JSON file.")
    parser.add_argument("--modes", nargs="+", default=["M2_NORMAL_PRIME", "M7_BREAKOUT_SNIPER"], help="Modes to boost.")
    parser.add_argument("--leverage-mult", type=float, default=1.5, help="Leverage multiplier during event.")
    parser.add_argument("--position-mult", type=float, default=1.3, help="Position size multiplier during event.")
    parser.add_argument("--output", type=Path, default=config.ARTIFACT_DIR / "event_mode_patch.json", help="Output patch file.")
    args = parser.parse_args()

    if not args.bias_file.exists():
        raise FileNotFoundError(f"Bias file not found: {args.bias_file}")
    with args.bias_file.open() as f:
        bias = json.load(f)

    patch = {
        "event_mode": {
            "enabled": True,
            "timestamp": bias.get("timestamp", ""),
            "direction": bias.get("direction", "NEUTRAL"),
            "confidence": bias.get("confidence", 0.0),
            "boost": {
                "modes": args.modes,
                "position_size_multiplier": args.position_mult,
                "leverage_multiplier": args.leverage_mult,
            },
        }
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        json.dump(patch, f, ensure_ascii=False, indent=2)
    print(f"Wrote event-mode patch to {args.output}")
    print(json.dumps(patch, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
