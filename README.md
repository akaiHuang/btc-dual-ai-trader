# BTC Dual-AI Trader

### Autonomous Cryptocurrency Trading with GPT-4 + Kimi K2

> A dual-AI trading system where GPT-4 handles strategy and market analysis while Kimi K2 (via Ollama) executes local decisions -- backed by XGBoost/LightGBM models, 187 strategy scripts, and 3.1M K-line records of historical data.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

---

## About

BTC Dual-AI Trader 是一套雙 AI 架構的加密貨幣自動交易系統，將高層策略分析與低延遲決策分工到不同模型與執行環境。適合用於自動交易研究、策略驗證與交易系統工程實驗，也可作為多代理/多模型交易架構的參考。

## About (EN)

BTC Dual-AI Trader is a dual-model crypto trading system that separates high-level market strategy from low-latency execution. It is built for automated trading research, system experimentation, and multi-agent strategy testing.

## 📋 Quick Summary

> 💰 這是一套**雙 AI 加密貨幣自動交易系統**，採用「雲端 + 本地」雙引擎架構：**GPT-4（代號 Wolf）**負責戰略層級的市場分析、鯨魚行為追蹤與宏觀市場結構判斷；**Kimi K2（代號 Dragon）**透過 Ollama 在本地運行，負責零延遲的即時交易決策。🤖 雙 AI 之外，還搭配 **XGBoost / LightGBM** 機器學習模型，以 5.9 年、308 萬根 K 線的歷史數據訓練。🎭 系統內建五種交易人格（Whale Hunter、Dragon、Wolf、Lion、Shrimp），各自針對不同市場狀態和風險偏好優化。📊 核心技術亮點包括**微觀結構分析**（VPIN 毒性指標、簽名成交量、深度價差）、**清算連鎖偵測**、**三層決策系統**（訊號層 → 市場狀態層 → 執行層）。🔧 支援 Binance 期貨與 dYdX v4 交易所，含紙上交易、回測與實盤三種模式。包含 **187 個策略腳本**和 **198+ 回測配置**。適合對**量化交易、AI 投資策略、加密貨幣市場微觀結構**有興趣的開發者。

---

## 🤔 Why This Exists

Single-model trading bots are inherently limited. One AI cannot simultaneously excel at high-level market regime detection, microstructure analysis, and split-second execution decisions. And rule-based systems cannot adapt to shifting market conditions.

This project takes a different approach: two AI systems working in tandem. **GPT-4 ("Wolf")** provides strategic intelligence -- analyzing whale behavior, institutional flows, and macro market structure. **Kimi K2 ("Dragon")**, running locally via Ollama, handles real-time execution decisions with zero API latency. Both are augmented by **XGBoost and LightGBM** models trained on 5.9 years of minute-level BTC data.

The system includes five distinct trading personas (Whale Hunter, Dragon, Wolf, Lion, Shrimp), each optimized for different market conditions and risk profiles. It supports paper trading for validation and live trading via dYdX.

Built from hundreds of hours of strategy research, 198+ backtested configurations, and hard-won lessons about what does and does not work in high-frequency crypto markets.

---

## 🏗️ Architecture

```
                    +------------------+
                    |   Market Data    |
                    |  (Binance/dYdX)  |
                    +--------+---------+
                             |
              +--------------+--------------+
              |                             |
     +--------v---------+         +--------v---------+
     |  Wolf (GPT-4)    |         |  Dragon (Kimi K2)|
     |  Cloud AI         |         |  Local AI (Ollama)|
     |                   |         |                   |
     |  - Whale analysis |         |  - Real-time      |
     |  - Market regime  |         |    execution       |
     |  - Strategy plan  |         |  - Signal scoring  |
     |  - Risk assessment|         |  - Position mgmt   |
     +--------+----------+         +--------+----------+
              |                             |
              +----------+   +--------------+
                         |   |
                  +------v---v------+
                  | Strategy Engine |
                  |                 |
                  | - 5 Personas    |
                  | - ML Models     |
                  | - Signal Fusion |
                  +--------+--------+
                           |
              +------------+------------+
              |            |            |
     +--------v--+  +------v-----+  +--v---------+
     | Paper      |  | Live       |  | Backtest   |
     | Trading    |  | Trading    |  | Engine     |
     | (Testnet)  |  | (dYdX)     |  | (Historical)|
     +------------+  +------------+  +------------+
```

### Trading Personas

| Persona | AI Engine | Strategy Profile |
|---------|-----------|-----------------|
| **Whale Hunter** | GPT-4 | Tracks institutional order flow, detects whale accumulation/distribution patterns |
| **Dragon** | Kimi K2 | Local AI execution with bridge-state memory, market regime detection |
| **Wolf** | GPT-4 | Primary hunter strategy with M_INVERSE_WOLF (contrarian hedge) |
| **Lion** | GPT-4 | Trend-following with liquidation cascade detection |
| **Shrimp** | Configurable | High-frequency scalping optimized for low-fee environments |

### Core Systems

| System | Description |
|--------|-------------|
| **Dual AI Engine** | GPT-4 for strategy + Kimi K2 for execution, with bridge-state synchronization |
| **ML Models** | XGBoost and LightGBM trained on 3.08M K-lines (5.9 years) |
| **Microstructure Analysis** | VPIN (toxicity), Signed Volume, Spread/Depth, Microprice, OBI |
| **Liquidation Cascade Detector** | Multi/short squeeze pressure monitoring with force-liquidation data |
| **Layered Decision System** | Signal Layer -> Regime Layer -> Execution Layer |
| **Dynamic Config** | Auto-generated trading parameters based on real-time market structure |

---

## 📁 Project Structure

```
btc-dual-ai-trader/
|-- src/                    # Core modules (14 packages)
|   |-- core/               # Config, main loop
|   |-- strategy/           # Trading strategy implementations
|   |-- trading/            # Order execution, position management
|   |-- exchange/           # Binance/dYdX API clients
|   |-- metrics/            # Market microstructure indicators
|   |-- data/               # Data pipeline and storage
|   |-- backtesting/        # Backtest engine
|   |-- evaluation/         # Strategy performance analysis
|   +-- utils/              # Shared utilities
|-- ai_dev/                 # 23 AI development modules
|   |-- train_rl.py         # Reinforcement learning training
|   |-- train_supervised.py # Supervised model training
|   |-- inference.py        # Model inference pipeline
|   +-- ...                 # Backtesting, data pipeline, configs
|-- scripts/                # 187 strategy and analysis scripts
|-- config/                 # 30+ configuration files
|   |-- strategy_cards/     # Per-strategy parameter cards
|   +-- trading_cards/      # Per-persona trading configs
|-- docs/                   # 54 documentation files
|-- dydx/                   # dYdX v4 integration
+-- main.py                 # Entry point (backtest/paper/live)
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|------------|
| **Language** | Python 3.11+ |
| **AI (Cloud)** | GPT-4 (OpenAI API) |
| **AI (Local)** | Kimi K2 via Ollama |
| **ML Models** | XGBoost, LightGBM, scikit-learn |
| **Hyperparameter Tuning** | Optuna |
| **Explainability** | SHAP |
| **Technical Indicators** | TA-Lib (6 core indicators) |
| **Exchange APIs** | python-binance, ccxt, dYdX v4 Client |
| **Data Processing** | pandas, NumPy, SciPy, PyArrow |
| **Time Series** | Prophet, statsmodels |
| **Infrastructure** | Docker, Docker Compose, Prometheus |
| **API** | FastAPI, uvicorn, WebSockets |
| **Task Queue** | Celery, Flower |
| **Visualization** | Plotly, Matplotlib, Seaborn |

---

## 🚀 Quick Start

### Paper Trading (Recommended First Step)

```bash
# Clone and set up
cd btc-dual-ai-trader
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your Binance API keys (testnet recommended)

# Run paper trading
python main.py --mode paper --strategy BTCHighFreq

# Or use the smart trading launcher
bash start_smart_trading.sh
```

### AI-Powered Trading

```bash
# Start the full dual-AI system
bash start_ai_system.sh

# Run specific strategy personas
python scripts/paper_trading_hybrid_full.py 0.5    # Hybrid strategy
python scripts/ai_market_analyst.py                 # Market analysis
python scripts/ai_trading_advisor_gpt.py            # GPT-4 advisor
```

### Backtesting

```bash
python main.py --mode backtest --strategy BTCHighFreq
```

---

## 📊 Key Metrics

| Metric | Value |
|--------|-------|
| Historical Data | 3,080,304 K-lines (5.9 years) |
| Strategy Scripts | 187 |
| AI Modules | 23 |
| Configuration Files | 30+ |
| Documentation Files | 54 |
| Backtested Configs | 198+ across 15 strategy types |
| Trading Personas | 5 (Whale Hunter, Dragon, Wolf, Lion, Shrimp) |
| Supported Exchanges | Binance Futures (Testnet + Mainnet), dYdX v4 |

---

## 📚 Documentation

Detailed guides are available in the `docs/` directory:

- [Development Plan](docs/DEVELOPMENT_PLAN.md) -- Full roadmap and task breakdown
- [Paper Trading Guide](PAPER_TRADING_README.md) -- Setup and usage for simulated trading
- [Real Trading Guide](REAL_TRADING_README.md) -- Live deployment on dYdX
- [System Architecture](docs/SYSTEM_ARCHITECTURE_ANALYSIS.md) -- Technical deep-dive
- [Whale Hunter Integration](AI_WHALE_HUNTER_INTEGRATION.md) -- Institutional flow tracking
- [Multi-Test Guide](MULTI_TEST_GUIDE.md) -- Running parallel strategy tests

---

## 👤 Author

**Huang Akai (Kai)** -- Founder @ Universal FAW Labs | Creative Technologist | Ex-Ogilvy | 15+ years experience

---

## 📄 License

MIT
