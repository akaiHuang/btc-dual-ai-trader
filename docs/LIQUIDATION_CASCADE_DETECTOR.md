# 💣 爆倉瀑布偵測系統 v2.0

## 概述

BTC 爆倉瀑布是加密市場中最強烈的盈利機會之一。當大量槓桿倉位被強制平倉時，會形成連鎖反應，導致價格快速單向移動。

**本系統新增/優化的幣安 API：**

| API | 端點 | 用途 | 重要性 |
|-----|------|------|--------|
| 🆕 Taker Buy/Sell Ratio | `/futures/data/takerlongshortRatio` | 主動買賣比，爆倉前兆 | ⭐⭐⭐⭐⭐ |
| 🆕 Top Position Ratio | `/futures/data/topLongShortPositionRatio` | 頂級交易員持倉 | ⭐⭐⭐⭐ |
| 🆕 WebSocket forceOrder | `btcusdt@forceOrder` | 即時爆倉流 | ⭐⭐⭐⭐⭐ |
| ✅ Global Long/Short | `/futures/data/globalLongShortAccountRatio` | 散戶多空比 | ⭐⭐⭐⭐ |
| ✅ Top Long/Short | `/futures/data/topLongShortAccountRatio` | 大戶多空比 | ⭐⭐⭐⭐ |
| ✅ Open Interest | `/futures/data/openInterestHist` | 持倉量變化 | ⭐⭐⭐⭐ |
| ✅ Funding Rate | `/fapi/v1/fundingRate` | 資金費率 | ⭐⭐⭐ |

---

## 新增模組

### 1. `src/metrics/liquidation_cascade_detector.py`

**即時爆倉瀑布偵測器** - 使用 WebSocket 即時監聽爆倉流

```python
from src.metrics.liquidation_cascade_detector import LiquidationCascadeDetector, CascadeAlert

# 定義回調函數
def on_cascade(alert: CascadeAlert):
    print(f"🚨 爆倉瀑布！${alert.total_usd/1e6:.1f}M 被爆！")
    print(f"方向: {alert.direction.value}")
    print(f"建議: {alert.recommended_action}")

# 啟動偵測器
detector = LiquidationCascadeDetector(
    symbol="BTCUSDT",
    cascade_callback=on_cascade,
)
await detector.start()
```

**爆倉瀑布等級：**

| 等級 | 1分鐘爆倉量 | 說明 |
|------|-------------|------|
| QUIET | < $500k | 平靜 |
| BUILDING | $500k - $1M | 醞釀中 |
| MINOR | $1M - $3M | 小型瀑布 |
| SIGNIFICANT | $3M - $10M | 顯著瀑布 ⚠️ |
| MAJOR | $10M - $50M | 大型瀑布 🔥 |
| EXTREME | > $50M | 極端瀑布 💥 |

---

### 2. 更新 `src/metrics/leverage_pressure.py`

**新增指標：**

#### Taker Buy/Sell Ratio (主動買賣比)

這是爆倉偵測最關鍵的指標之一！

```
buySellRatio > 1.5 → 🟢 大量主動買入 → 空頭可能被軋
buySellRatio < 0.6 → 🔴 大量主動賣出 → 多頭可能被爆
buySellRatio ≈ 1.0 → ⚪ 平衡
```

#### OI Velocity (持倉量變化速度)

```
OI 快速下降 (-0.3%/5min) → 💥 正在發生爆倉
OI 快速上升 (+0.5%/5min) → 📈 新資金進場
```

---

## 新的爆倉壓力面板

更新後的顯示：

```
💣 爆倉壓力雷達 (Liquidation Pressure)
🐂 多頭爆倉壓力 L_long_liq : [██████----]  63.3 ⚪ 中等
🐻 空頭爆倉壓力 L_short_liq: [██--------]  23.9 🟢 很低
📊 持倉量變化 (OI Change): 💤 +0.097% (近30根K線)
⚡ 主動買賣比 (Taker): 🔴 0.72 賣方主導 (爆多風險↑)
🚀 OI 變化速度: 💥 -0.45% (近期)
➡ 解讀：多頭壓力偏高
➡ 策略傾向：⚠️ 慎追多 ／ ✅ 優先找做空機會
```

---

## 爆倉瀑布訊號解讀

### 多頭連環爆 (Long Liquidation Cascade)

**特徵：**
- 價格快速下跌 (> 0.5% in 30s)
- OI 大幅下降
- Taker Ratio < 0.7
- Funding Rate 從正轉負

**策略：**
1. 瀑布初期：跟隨趨勢做空
2. 瀑布後期 (價格跌 > 1%)：準備抄底做多

### 空頭連環爆 (Short Liquidation Cascade)

**特徵：**
- 價格快速上漲 (> 0.5% in 30s)
- OI 大幅下降
- Taker Ratio > 1.5
- Funding Rate 從負轉正

**策略：**
1. 瀑布初期：跟隨趨勢做多
2. 瀑布後期 (價格漲 > 1%)：準備做空回調

---

## 權重配置

### 爆倉壓力分數計算

```python
weights = {
    "crowding": 18,         # 散戶擁擠度
    "top_crowding": 15,     # 大戶擁擠度
    "funding": 15,          # 資金費率
    "oi_trend": 12,         # OI 趨勢
    "force_share": 10,      # 爆倉方向佔比
    "force_volume": 12,     # 爆倉量
    "taker_pressure": 10,   # 🆕 主動買賣壓力
    "oi_velocity": 8,       # 🆕 OI 變化速度
}
# 總權重: 100
```

---

## 使用建議

### 爆倉瀑布交易策略

1. **等待觸發條件：**
   - 瀑布等級 >= SIGNIFICANT ($3M+)
   - 方向明確 (Long 或 Short Liquidation)
   - 價格已經移動 > 0.3%

2. **進場時機：**
   - 瀑布初期 (0-30秒)：順勢進場
   - 瀑布後期 (> 60秒)：考慮反轉

3. **風險控制：**
   - 爆倉瀑布期間波動極大
   - 使用較小倉位
   - 設置較寬止損

### 與其他系統整合

```python
# 在 paper_trading_hybrid_full.py 中使用
from src.metrics.liquidation_cascade_detector import LiquidationCascadeDetector

# 在 run() 方法中啟動
cascade_detector = LiquidationCascadeDetector(
    cascade_callback=self._on_cascade_alert
)
asyncio.create_task(cascade_detector.start())
```

---

## API 調用頻率

| 端點 | 建議頻率 | 備註 |
|------|----------|------|
| REST APIs | 每 60 秒 | 避免 429 限制 |
| WebSocket forceOrder | 即時 | 無限制 |
| WebSocket aggTrade | 即時 | 用於價格追蹤 |

---

## 檔案結構

```
src/metrics/
├── leverage_pressure.py          # 爆倉壓力計算 (已更新)
├── liquidation_cascade_detector.py  # 🆕 即時爆倉瀑布偵測
└── __init__.py

scripts/
└── fetch_binance_leverage_data.py  # 數據獲取 (已更新，新增 2 個 API)
```

---

## 未來優化方向

1. **歷史爆倉數據庫**：記錄所有爆倉瀑布事件，用於回測
2. **機器學習預測**：使用歷史數據訓練爆倉預測模型
3. **多幣種監控**：同時監控 ETH、SOL 等的爆倉情況
4. **熱力圖**：顯示不同價格帶的潛在爆倉量

---

## 參考資料

- [幣安期貨 API 文檔](https://binance-docs.github.io/apidocs/futures/en/)
- [WebSocket Liquidation Streams](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Liquidation-Order-Streams)
