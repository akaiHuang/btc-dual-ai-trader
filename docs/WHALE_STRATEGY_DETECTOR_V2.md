# 🐋 主力策略識別系統 v2.0

## 📊 概覽

本系統用於識別加密貨幣市場中的主力（做市商/大戶）操作策略，幫助交易系統避開陷阱、順勢而為。

## 🎯 支援的 8 種主力策略

| 策略 | 代碼 | 目的與手法 |
|------|------|-----------|
| 吸籌建倉 | `ACCUMULATION` | 低位隱蔽大量買入，為後續拉升準備 |
| 誘空吸籌 | `BEAR_TRAP` | 製造空頭陷阱，誘使散戶拋售，趁低吸籌 |
| 誘多派發 | `BULL_TRAP` | 製造多頭陷阱，誘使散戶追高，趁高出貨 |
| 拉高出貨 | `PUMP_DUMP` | 快速拉抬價格吸引跟風盤，高位出貨 |
| 洗盤震倉 | `SHAKE_OUT` | 震盪清洗意志不堅定籌碼，清除拉升阻力 |
| 試盤探測 | `TESTING` | 小幅拉升/打壓試探市場多空力量 |
| 對敲拉抬 | `WASH_TRADING` | 自買自賣製造成交假象 |
| 砸盤打壓 | `DUMP` | 高位大手筆拋售，打壓價格 |

---

## 🔍 v2.0 新增檢測器

### 1. 支撐/壓力突破檢測器 (`SupportResistanceBreakDetector`)

**功能：** 檢測假突破和真突破，識別誘空/誘多陷阱

**特徵信號：**
- 突破壓力位但量能不足 → 可能是假突破（誘多）
- 跌破支撐位但量能不足 → 可能是假跌破（誘空）

```python
breakout_info = {
    "type": "RESISTANCE_BREAK" / "SUPPORT_BREAK" / "NONE",
    "level": 價位,
    "is_likely_fake": True/False,
    "signals": ["⚠️ 突破量能不足，可能是假突破"]
}
```

### 2. 成交量衰竭檢測器 (`VolumeExhaustionDetector`)

**功能：** 檢測趨勢末端的成交量萎縮

**特徵信號：**
- 上漲趨勢中量能萎縮 → 可能見頂（拉高出貨末期）
- 下跌趨勢中量能萎縮 → 賣壓衰竭，可能見底

```python
is_exhausted, trend, signals = detector.detect_exhaustion()
# trend: "UP" / "DOWN" / "NONE"
```

### 3. 隱藏大單檢測器 (`HiddenOrderDetector`)

**功能：** 檢測訂單簿中的冰山單

**特徵信號：**
- 某價位被反覆吃掉但掛單量不減少 → 冰山買單（主力吸籌）
- 成交量遠大於顯示掛單量 → 隱藏大單

```python
hidden_info = {
    "hidden_bid_detected": True,  # 下方有隱藏買單
    "hidden_ask_detected": False,
    "hidden_bid_level": 86500,
    "signals": ["💰 發現隱藏買單 @$86500 (累計吃貨 15.5)"]
}
```

### 4. 價量背離檢測器 (`PriceVolumeDivergenceDetector`)

**功能：** 檢測價格與成交量的背離

**類型：**
- **多頭背離：** 價跌但量縮 → 賣壓衰竭，可能反彈
- **空頭背離：** 價漲但量縮 → 追漲乏力，可能回落
- **量增價平：** 成交量放大但價格橫盤 → 主力吸籌或派發

```python
divergence_info = {
    "divergence_type": "BULLISH" / "BEARISH" / "ACCUMULATION" / "NONE",
    "strength": 0.7,
    "signals": ["📈 多頭背離：價跌-2%，量縮40% → 賣壓衰竭"]
}
```

### 5. 瀑布式下跌檢測器 (`WaterfallDropDetector`)

**功能：** 檢測砸盤打壓的特徵

**特徵信號：**
- 連續 3+ 根大陰線
- 跌幅超過 2%
- 成交量先放後縮（恐慌性放量後衰竭）

```python
waterfall_info = {
    "is_waterfall": True,
    "consecutive_bearish": 5,
    "total_drop_pct": -3.5,
    "volume_pattern": "EXHAUSTING",  # 成交量正在萎縮
    "signals": ["🌊 瀑布式下跌：連續5根陰線，跌幅-3.5%"]
}
```

---

## 📊 策略識別特徵矩陣

### 吸籌建倉 (ACCUMULATION)
| 指標 | 特徵 | 權重 |
|------|------|------|
| OBI | 中性 (-0.3 ~ 0.3) | 15% |
| VPIN | 低 (< 0.4) | 15% |
| WPI | 正向 (> 0.1) | 20% |
| 籌碼集中度 | 高 (> 0.55) | 15% |
| 價量特徵 | 量增價平 | 15% |
| **新增** 價量背離 | ACCUMULATION | 20% |
| **新增** 隱藏買單 | 檢測到 | 15% |

### 誘空吸籌 (BEAR_TRAP)
| 指標 | 特徵 | 權重 |
|------|------|------|
| OBI | 偏空 (< 0) | 10% |
| WPI | 正向 (> 0.2) - 跌時主力買 | 20% |
| 止損掃蕩 | 高 (> 40) | 20% |
| 價格變化 | 下跌 (< -0.3%) | 10% |
| **新增** 假跌破 | 跌破支撐後量縮 | **25%** |
| **新增** 隱藏買單 | 下方有承接 | 15% |

### 誘多派發 (BULL_TRAP)
| 指標 | 特徵 | 權重 |
|------|------|------|
| OBI | 偏多 (> 0) | 10% |
| WPI | 負向 (< -0.2) - 漲時主力賣 | 20% |
| 止損掃蕩 | 高 (> 40) | 20% |
| **新增** 假突破 | 突破壓力後量縮 | **25%** |
| **新增** 隱藏賣單 | 上方有派發 | 15% |
| **新增** 空頭背離 | 價漲量縮 | 15% |

### 拉高出貨 (PUMP_DUMP)
| 指標 | 特徵 | 權重 |
|------|------|------|
| OBI | 強多 (> 0.3) | 15% |
| VPIN | 高 (> 0.5) | 20% |
| 成交量比 | 巨量 (> 3x) | 20% |
| 價格漲幅 | 高 (> 1%) | 15% |
| **新增** 上漲中量能衰竭 | 是 | **20%** |
| **新增** 空頭背離 | 是 | 10% |

### 砸盤打壓 (DUMP)
| 指標 | 特徵 | 權重 |
|------|------|------|
| OBI | 強空 (< -0.3) | 20% |
| WPI | 強負 (< -0.4) | 20% |
| **新增** 瀑布式下跌 | 連續陰線 | **25%** |
| **新增** 成交量模式 | 先放後縮 | 10% |

---

## 💡 使用範例

```python
from src.strategy.whale_strategy_detector import WhaleStrategyDetector

# 創建偵測器
detector = WhaleStrategyDetector()

# 分析市場狀況
prediction = detector.analyze(
    obi=-0.3,               # 訂單簿偏空
    vpin=0.45,              # 中等知情交易
    current_price=87000,
    price_change_pct=-0.5,  # 價格下跌 0.5%
    volume_ratio=1.5,       # 成交量比平均高 50%
    whale_net_qty=5.0,      # 鯨魚淨買入 5 BTC
    funding_rate=-0.0002,
    liquidation_pressure_long=40,
    liquidation_pressure_short=60,
    recent_candles=candle_data,
    orderbook_snapshot=orderbook_data,
    current_volume=100000   # 當前成交量 $100k
)

# 獲取結果
print(f"檢測策略: {prediction.detected_strategy.value}")
print(f"關鍵信號: {prediction.key_signals}")
print(f"風險警告: {prediction.risk_warnings}")
```

---

## ⚠️ 風險警告生成規則

| 條件 | 警告內容 |
|------|---------|
| VPIN > 0.6 | ⚠️ 高毒性流量，市場可能被操控 |
| 止損掃蕩 > 50 | ⚠️ 注意假突破 |
| 委託異動 > 50 | ⚠️ 可能有 Spoofing |
| PUMP_DUMP 機率 > 30% | 🚨 可能是拉高出貨，不要追高！ |
| BULL_TRAP 機率 > 30% | 🚨 可能是誘多陷阱，突破可能是假的！ |
| BEAR_TRAP 機率 > 30% | 💡 可能是誘空陷阱，考慮逢低買入 |
| 假突破檢測到 | ⚠️ RESISTANCE_BREAK 可能是假突破！ |
| 瀑布式下跌 | 🌊 瀑布式下跌：連續N根陰線，跌幅X% |

---

## 🔄 整合到交易系統

此偵測器已整合到 `whale_strategy_bridge.py`，並在以下模式中使用：
- **M🐺 Wolf (AI_WHALE_HUNTER)** - GPT-4o-mini
- **M🐲 Dragon (AI_KIMI_HUNTER)** - Kimi-k2

偵測結果會影響：
1. 開倉決策（是否進場）
2. Maker 掛單價格（whale_reversal_price）
3. 止盈止損設置
4. 風險警告顯示

---

## 📈 未來改進方向

1. **時間序列分析**：追蹤策略演變（如吸籌 → 拉升）
2. **機器學習模型**：訓練分類器提高識別準確率
3. **多時間框架**：結合 1m/5m/15m/1h 多週期分析
4. **歷史回測**：驗證各策略識別準確率
5. **即時警報**：關鍵策略轉變時推送通知
