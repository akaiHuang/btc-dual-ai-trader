# 🐋 Whale Trading System - 技術文檔

> **版本**: v4.0  
> **更新日期**: 2025-11-28  
> **狀態**: 開發中 (Phase 1 完成)

---

## 📋 目錄

1. [系統架構](#系統架構)
2. [檔案結構](#檔案結構)
3. [核心模組](#核心模組)
4. [TensorFlow 訓練資料](#tensorflow-訓練資料)
5. [使用方式](#使用方式)
6. [開發進度](#開發進度)
7. [API 配置](#api-配置)

---

## 🏗️ 系統架構

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Whale Trading System v4.0                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                       數據層 (Data Layer)                             │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │  │
│  │  │ WebSocket   │  │ REST API   │  │ Orderbook   │  │ Trades      │  │  │
│  │  │ (1秒即時)   │  │ (歷史數據) │  │ (深度5檔)   │  │ (逐筆成交)  │  │  │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │  │
│  └─────────┼────────────────┼────────────────┼────────────────┼─────────┘  │
│            └────────────────┴────────────────┴────────────────┘            │
│                                      │                                      │
│  ┌───────────────────────────────────▼──────────────────────────────────┐  │
│  │                      分析層 (Analysis Layer)                          │  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │  │
│  │  │              WhaleStrategyDetectorV4 (15個偵測器)                │ │  │
│  │  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐               │ │  │
│  │  │  │ OBI 偵測器  │ │ WPI 偵測器  │ │ VPIN 偵測器 │  ...          │ │  │
│  │  │  └─────────────┘ └─────────────┘ └─────────────┘               │ │  │
│  │  │                          │                                      │ │  │
│  │  │               ┌──────────▼──────────┐                          │ │  │
│  │  │               │    23種策略分析     │                          │ │  │
│  │  │               │  (7大類主力行為)    │                          │ │  │
│  │  │               └──────────┬──────────┘                          │ │  │
│  │  │                          │                                      │ │  │
│  │  │               ┌──────────▼──────────┐                          │ │  │
│  │  │               │  Softmax 機率計算   │                          │ │  │
│  │  │               │  信號生成 & 評分    │                          │ │  │
│  │  │               └─────────────────────┘                          │ │  │
│  │  └─────────────────────────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                      │                                      │
│  ┌───────────────────────────────────▼──────────────────────────────────┐  │
│  │                      交易層 (Trading Layer)                           │  │
│  │                                                                       │  │
│  │  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐ │  │
│  │  │  信號過濾       │ ──> │  風險管理       │ ──> │  訂單執行      │ │  │
│  │  │  (機率>70%)     │     │  (每日限額)     │     │  (Testnet)     │ │  │
│  │  │  (信心>60%)     │     │  (止盈止損)     │     │  (100X槓桿)    │ │  │
│  │  └─────────────────┘     └─────────────────┘     └─────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                      │                                      │
│  ┌───────────────────────────────────▼──────────────────────────────────┐  │
│  │                      記錄層 (Logging Layer)                           │  │
│  │                                                                       │  │
│  │  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐ │  │
│  │  │  trades.json    │     │  training_data  │     │  system.log     │ │  │
│  │  │  (交易記錄)     │     │  (TensorFlow)   │     │  (運行日誌)     │ │  │
│  │  └─────────────────┘     └─────────────────┘     └─────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 檔案結構

### 核心程式

```
scripts/
├── whale_strategy_detector_v4.py    # 🎯 主力策略偵測器 (2,629行)
│   ├── 15個偵測器
│   ├── 23種策略
│   └── Softmax機率計算
│
├── whale_detector_v4_test.py        # 🧪 偵測器測試 (含模擬交易)
│   ├── Binance API 整合
│   ├── 彩色中文儀表板
│   └── 模擬交易追蹤
│
├── whale_testnet_trader.py          # 💰 Testnet 實盤交易
│   ├── WebSocket 即時數據
│   ├── 30秒分析週期
│   ├── 100X槓桿交易
│   └── JSON交易記錄
│
├── whale_paper_trader.py            # 📝 紙上交易系統
├── whale_trading_system.py          # 🔄 整合系統
└── whale_training_data.py           # 📊 訓練資料收集器
```

### 配置文件

```
config/
├── config.json                      # 主配置 (API keys備用)
├── whale_testnet_config.json        # Testnet 交易配置
└── ai_team_config.json              # AI 團隊配置

.env                                 # 🔑 API Keys (優先讀取)
```

### 輸出記錄

```
logs/
├── whale_paper_trader/              # 模擬交易記錄
│   ├── trades_YYYYMMDD.json         # 📊 交易記錄 (TensorFlow用)
│   └── system_*.log                 # 系統日誌
│
├── whale_live_trader/               # 真實交易記錄
│   ├── trades_YYYYMMDD.json         # 交易記錄
│   └── system_*.log                 # 系統日誌
│
├── whale_detector_v4/
│   ├── whale_v4_test_*.log          # 偵測器測試日誌
│   └── paper_trades.json            # 模擬交易記錄
│
└── whale_training/
    └── training_data_*.csv          # 訓練資料 CSV
```

---

## 🧠 核心模組

### 1. WhaleStrategyDetectorV4 (主力策略偵測器)

**檔案**: `scripts/whale_strategy_detector_v4.py`

#### 15個偵測器

| 偵測器 | 功能 | 輸入 |
|--------|------|------|
| OBI | 訂單簿失衡 | Orderbook |
| WPI | 主力壓力指數 | Trades |
| VPIN | 毒性流動性 | Volume |
| FundingRate | 資金費率 | API |
| OIChange | 未平倉變化 | API |
| LiquidationPressure | 清算壓力 | API |
| PricePattern | 價格形態 | OHLCV |
| VolumeCluster | 成交量聚類 | Volume |
| SpreadAnalyzer | 價差分析 | Orderbook |
| FlowAnalyzer | 資金流向 | Trades |
| MomentumAnalyzer | 動量分析 | Price |
| VolatilityAnalyzer | 波動率分析 | Price |
| TrendAnalyzer | 趨勢分析 | Price |
| DivergenceAnalyzer | 背離分析 | Price+Volume |
| TimeAnalyzer | 時間週期 | Timestamp |

#### 23種策略 (7大類)

```python
# 🔥 進攻型 (紅色)
WHALE_PUMP              # 主力拉盤
WHALE_DUMP              # 主力砸盤
MOMENTUM_SURGE          # 動量爆發

# 🛡️ 防守型 (藍色)
WHALE_ACCUMULATE        # 主力吸籌
WHALE_DISTRIBUTE        # 主力出貨
STEALTH_ACCUMULATE      # 隱形吸籌

# ⚡ 獵殺型 (紫色)
STOP_HUNT_LONG          # 多頭止損獵殺
STOP_HUNT_SHORT         # 空頭止損獵殺
LIQUIDATION_CASCADE     # 清算瀑布

# 🎯 精準型 (綠色)
BREAKOUT_REAL           # 真突破
BREAKOUT_FAKE           # 假突破
REVERSAL_SIGNAL         # 反轉信號

# 🔄 盤整型 (灰色)
RANGE_SUPPORT           # 區間支撐
RANGE_RESISTANCE        # 區間壓力
CONSOLIDATION           # 盤整震盪

# ⚠️ 特殊型 (橙色)
HIGH_VOLATILITY         # 高波動
NEWS_DRIVEN             # 消息驅動
FUNDING_ARBITRAGE       # 資金費率套利

# 📊 中性型 (青色)
MARKET_MAKING           # 做市商行為
LIQUIDITY_PROVISION     # 流動性提供
REBALANCING            # 再平衡
ORDER_ABSORPTION        # 訂單吸收
WAIT_AND_SEE           # 觀望
```

### 2. WhaleTestnetTrader (Testnet 交易)

**檔案**: `scripts/whale_testnet_trader.py`

#### 交易參數

| 參數 | 值 | 說明 |
|------|-----|------|
| `leverage` | 100X | 高槓桿短線交易 |
| `position_size_usdt` | 100 | 每筆交易金額 |
| `ws_interval_sec` | 1秒 | WebSocket 數據接收 |
| `analysis_interval_sec` | 30秒 | 主力分析週期 |
| `min_trade_interval_sec` | 60秒 | 最小交易間隔 |
| `target_profit_pct` | 0.15% | 止盈 (價格移動) |
| `stop_loss_pct` | 0.10% | 止損 (價格移動) |
| `max_hold_minutes` | 30分 | 最長持倉時間 |

#### 盈利計算

```
100X 槓桿，100U 本金：
┌─────────────────────────────────────────────┐
│ 控制部位 = 100U × 100 = 10,000U             │
│                                             │
│ 目標 15% 槓桿後盈利：                        │
│   價格移動 = 15% / 100 = 0.15%              │
│   盈利 = 100U × 15% = $15                   │
│                                             │
│ 手續費：                                     │
│   開倉 = 10,000 × 0.04% = $4                │
│   平倉 = 10,000 × 0.04% = $4                │
│   總計 = $8                                  │
│                                             │
│ 淨盈利 = $15 - $8 = $7 (7%)                 │
└─────────────────────────────────────────────┘
```

---

## 📊 TensorFlow 訓練資料

### 📁 資料存儲位置

```
data/tensorflow_training/
├── raw/                              # 原始數據
│   ├── market_snapshots_YYYYMMDD.jsonl  # 每秒市場快照 (JSONL格式)
│   └── trade_records_YYYYMMDD.json      # 交易記錄
├── processed/                        # 處理後數據
├── features/                         # 特徵向量 (NumPy)
│   └── features_YYYYMMDD.npy
└── labels/                           # 標籤 (NumPy)
    └── labels_YYYYMMDD.npy
```

### 📊 市場快照數據 (每秒記錄)

存放於：`data/tensorflow_training/raw/market_snapshots_YYYYMMDD.jsonl`

```json
{
  "timestamp": "2025-11-28T17:15:00.123",
  "price": 91500.00,
  "obi": 0.35,
  "trade_imbalance": 0.42,
  "big_trade_count": 5,
  "big_buy_volume": 2.5,
  "big_sell_volume": 1.2,
  "big_buy_value": 228750,
  "big_sell_value": 109800,
  "strategy_probs": {"WHALE_PUMP": 0.78, ...}
}
```

### 40個特徵 (Features)

#### 市場狀態特徵 (7個)

| 特徵 | 說明 | 範圍 |
|------|------|------|
| `obi` | 訂單簿失衡 | -1 ~ +1 |
| `wpi` | 主力壓力指數 | -1 ~ +1 |
| `vpin` | 毒性流動性指標 | 0 ~ 1 |
| `funding_rate` | 資金費率 | -0.1% ~ +0.1% |
| `oi_change_pct` | 未平倉變化% | -10% ~ +10% |
| `liq_pressure_long` | 多頭清算壓力 | 0 ~ 100 |
| `liq_pressure_short` | 空頭清算壓力 | 0 ~ 100 |

#### 價格行為特徵 (3個)

| 特徵 | 說明 | 範圍 |
|------|------|------|
| `price_change_1m` | 1分鐘價格變化% | -2% ~ +2% |
| `price_change_5m` | 5分鐘價格變化% | -5% ~ +5% |
| `volatility_5m` | 5分鐘波動率 | 0 ~ 5% |

#### 策略機率特徵 (23個)

| 特徵 | 說明 |
|------|------|
| `prob_WHALE_PUMP` | 主力拉盤機率 |
| `prob_WHALE_DUMP` | 主力砸盤機率 |
| `prob_MOMENTUM_SURGE` | 動量爆發機率 |
| ... | (共23個策略機率) |

#### 交易資訊特徵 (7個)

| 特徵 | 說明 |
|------|------|
| `strategy` | 觸發策略名稱 |
| `probability` | 策略機率 |
| `confidence` | 信心度 |
| `direction` | 交易方向 (LONG/SHORT) |
| `entry_price` | 進場價格 |
| `take_profit_price` | 止盈價格 |
| `stop_loss_price` | 止損價格 |

### 標籤 (Labels)

| 標籤 | 說明 | 類型 |
|------|------|------|
| `is_profitable` | 是否獲利 | 二元分類 |
| `net_pnl_usdt` | 淨盈虧金額 | 迴歸 |
| `price_move_pct` | 價格移動% | 迴歸 |
| `hold_seconds` | 持倉時間 | 迴歸 |
| `max_profit_pct` | 最大浮盈% | 迴歸 |
| `max_drawdown_pct` | 最大回撤% | 迴歸 |

### 資料格式範例

```json
{
  "trade_id": "WT_20251128_171500_0001",
  "timestamp": "2025-11-28T17:15:00",
  
  "strategy": "WHALE_PUMP",
  "probability": 0.78,
  "confidence": 0.72,
  "direction": "LONG",
  
  "entry_price": 91500.00,
  "take_profit_price": 91637.25,
  "stop_loss_price": 91408.50,
  
  "obi": 0.35,
  "wpi": 0.42,
  "vpin": 0.28,
  "funding_rate": 0.0008,
  "oi_change_pct": 1.5,
  "liq_pressure_long": 45,
  "liq_pressure_short": 55,
  "price_change_1m": 0.05,
  "price_change_5m": 0.12,
  "volatility_5m": 0.08,
  
  "strategy_probs": {
    "WHALE_PUMP": 0.78,
    "MOMENTUM_SURGE": 0.12,
    "BREAKOUT_REAL": 0.05,
    ...
  },
  
  "status": "CLOSED_TP",
  "exit_price": 91638.00,
  "exit_time": "2025-11-28T17:18:30",
  "price_move_pct": 0.151,
  "pnl_pct": 15.1,
  "pnl_usdt": 15.10,
  "fee_usdt": 8.00,
  "net_pnl_usdt": 7.10,
  "hold_seconds": 210,
  "max_profit_pct": 16.2,
  "max_drawdown_pct": -2.1
}
```

### TensorFlow 訓練建議

```python
# 最小訓練資料量
MIN_TRADES_FOR_TRAINING = 50

# 建議訓練資料量
RECOMMENDED_TRADES = 200+

# 模型類型建議
# 1. 二元分類：預測交易是否獲利
# 2. 迴歸模型：預測盈虧金額
# 3. 多分類：預測最佳策略
```

---

## 🚀 使用方式

### 環境準備

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 確認 .env 已配置 (已完成)
cat .env | grep TESTNET
```

### 執行偵測器測試 (不交易)

```bash
# 執行 1 小時，純監控
.venv/bin/python scripts/whale_detector_v4_test.py 1

# 執行 8 小時
.venv/bin/python scripts/whale_detector_v4_test.py 8
```

### 執行 Testnet 交易

```bash
# ⚠️ 注意：Binance Testnet Futures 已停用
# 系統改為支援兩種模式：

# 1. 模擬交易模式 (預設，不需要 API)
.venv/bin/python scripts/whale_testnet_trader.py --paper

# 執行 8 小時模擬交易
.venv/bin/python scripts/whale_testnet_trader.py --hours 8 --paper

# 2. 真實交易模式 (需要正式網 API，謹慎使用!)
.venv/bin/python scripts/whale_testnet_trader.py --hours 1 --live

# 自訂參數
.venv/bin/python scripts/whale_testnet_trader.py \
    --hours 8 \
    --leverage 100 \
    --size 100 \
    --interval 30 \
    --paper

# 查看模擬交易報告
.venv/bin/python scripts/whale_testnet_trader.py --report --paper

# 查看真實交易報告
.venv/bin/python scripts/whale_testnet_trader.py --report --live
```

### 參數說明

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `--hours` | 1.0 | 運行時間 (小時) |
| `--leverage` | 100 | 槓桿倍數 |
| `--size` | 100.0 | 每筆交易金額 (USDT) |
| `--interval` | 30.0 | 分析間隔 (秒) |
| `--paper` | 預設 | 模擬交易模式 (不需API) |
| `--live` | - | 真實交易模式 (需確認) |
| `--report` | - | 查看交易報告 |

### 查看記錄

```bash
# 查看模擬交易記錄
cat logs/whale_paper_trader/trades_20251128.json | jq

# 查看真實交易記錄
cat logs/whale_live_trader/trades_20251128.json | jq

# 查看系統日誌
tail -f logs/whale_paper_trader/system_*.log
```

---

## 📈 開發進度

### Phase 1: 偵測系統 ✅ 完成

| 項目 | 狀態 | 說明 |
|------|------|------|
| WhaleStrategyDetectorV4 | ✅ | 15偵測器、23策略 |
| 彩色中文儀表板 | ✅ | ANSI顏色、即時更新 |
| Binance API 整合 | ✅ | REST + WebSocket |
| 模擬交易追蹤 | ✅ | 止盈止損、盈虧計算 |

### Phase 2: Testnet 交易 ✅ 完成 (改為模擬模式)

| 項目 | 狀態 | 說明 |
|------|------|------|
| WebSocket 即時數據 | ✅ | 1秒更新 |
| 模擬交易模式 | ✅ | 不需 API，本地計算盈虧 |
| 真實交易模式 | ✅ | 支援正式網 API |
| 交易記錄 JSON | ✅ | 40特徵完整記錄 |
| 風險管理 | ✅ | 每日限額、止盈止損 |

> ⚠️ 注意：Binance Testnet Futures 已於 2024 年停用，系統改為模擬交易模式

### Phase 3: TensorFlow 訓練 🔄 進行中

| 項目 | 狀態 | 說明 |
|------|------|------|
| 資料收集 | ⏳ 待執行 | 需運行 8+ 小時收集 |
| 資料清洗 | 📋 待開發 | 異常值處理 |
| 模型訓練 | 📋 待開發 | 二元分類 + 迴歸 |
| 模型整合 | 📋 待開發 | 即時預測 |

### Phase 4: 正式網交易 📋 待開發

| 項目 | 狀態 | 說明 |
|------|------|------|
| 正式 API 切換 | 📋 | .env 已有 KEY |
| 資金管理 | 📋 | 風控加強 |
| 監控告警 | 📋 | Telegram 通知 |

---

## 🔑 API 配置

### API Key 存放位置

**主要**: `.env` 文件 (程式優先讀取)

```bash
# .env (已配置)
BINANCE_TESTNET_API_KEY=qtLK99JokWAU...   # Testnet (已停用)
BINANCE_API_KEY=mzvgri87Wjcs...           # 正式網
```

**備用**: `config/config.json`

```json
{
  "binance_testnet": {
    "api_key": "your_key",
    "api_secret": "your_secret"
  }
}
```

### 交易模式說明

| 模式 | 需要 API | 說明 |
|------|----------|------|
| `--paper` | ❌ 不需要 | 模擬交易，本地計算盈虧 |
| `--live` | ✅ 需要正式網 | 真實交易，使用 .env 的 API |

### 注意事項

- ⚠️ `.env` 已加入 `.gitignore`，不會上傳
- ⚠️ Binance Testnet Futures 已停用，建議使用模擬模式
- ⚠️ 真實交易前請確認風險
- ⚠️ 正式網 API 只開啟「讀取」+「合約交易」，不要開提現

---

## 📞 後續開發

### 下一步行動

1. **執行 8 小時模擬交易收集資料**
   ```bash
   .venv/bin/python scripts/whale_testnet_trader.py --hours 8 --paper
   ```

2. **收集 50+ 筆交易資料**

3. **開發 TensorFlow 訓練腳本**
   ```python
   # scripts/whale_tensorflow_train.py (待開發)
   ```

4. **整合預測模型到交易系統**

5. **（可選）小額真實交易驗證**
   ```bash
   # 確認風險後使用
   .venv/bin/python scripts/whale_testnet_trader.py --hours 1 --live --size 10
   ```

### 聯絡方式

如有問題，請查看：
- `docs/` 目錄下的其他文檔
- GitHub Issues
- 專案 README.md

---

> 📝 本文檔最後更新：2025-11-28
