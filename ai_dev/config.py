"""
Shared config for AI prediction / RL experiments (isolated from runtime).
Defaults are conservative and only read existing data; outputs go to ai_dev/artifacts.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORICAL_DIR = DATA_DIR / "historical"
NEWS_DIR = DATA_DIR / "news_history_multi"
ARTIFACT_DIR = ROOT / "ai_dev" / "artifacts"

# Defaults for supervised labels
FUTURE_HORIZONS_MIN = [5, 15, 60]  # minutes
RET_THRESHOLDS = [0.1, 0.25, 0.5]  # % move for classification bins

# Rolling windows (minutes) for basic features
RET_WINDOWS = [1, 3, 5, 15, 60]
VOL_WINDOWS = [5, 15, 60, 240]

# File names
DEFAULT_PRICE_FILE = HISTORICAL_DIR / "BTCUSDT_1m.parquet"
DEFAULT_OUTPUT = ARTIFACT_DIR / "dataset_features.parquet"
DEFAULT_NEWS_EMBED_FILE = ARTIFACT_DIR / "news_embeds.parquet"

# Ensure artifact dirs exist
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
