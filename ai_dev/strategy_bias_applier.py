"""
Strategy bias applier (non-intrusive): reads strategy_bias.json and prints a patch suggestion
for config/trading_strategies_dynamic.json. Does not modify files automatically.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_dev import config


def main():
    parser = argparse.ArgumentParser(description="Read strategy_bias.json and print config patch suggestion.")
    parser.add_argument("--bias-file", type=Path, default=config.ARTIFACT_DIR / "strategy_bias.json", help="Bias JSON file.")
    parser.add_argument("--mode", type=str, default="M2_NORMAL_PRIME", help="Target mode to adjust.")
    args = parser.parse_args()

    if not args.bias_file.exists():
        raise FileNotFoundError(f"Bias file not found: {args.bias_file}")
    with args.bias_file.open() as f:
        bias = json.load(f)

    direction = bias.get("direction", "NEUTRAL")
    mult = bias.get("position_size_multiplier", 1.0)
    lev = bias.get("leverage", 5)
    conf = bias.get("confidence", 0.0)

    print("=== Suggested config patch (manual) ===")
    print(f"Mode: {args.mode}")
    print(f" - direction bias: {direction} (confidence {conf})")
    print(f" - position_size multiplier: {mult}")
    print(f" - leverage: {lev}")
    print("Apply manually to config/trading_strategies_dynamic.json if desired.")


if __name__ == "__main__":
    main()
