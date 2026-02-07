# 🚦 主要執行程式與資料流總覽

目的：快速理解「系統現在在跑什麼、用到哪些模組、對 Binance 拉哪些資料、輸出到哪裡」，方便後續開發與排錯。

---

## 1) `scripts/paper_trading_testnet_hybrid.py`
混合模式入口，讓 Paper Trading 狀態與 Binance Testnet 同步。

- **核心職責**
  - 啟動 Hybrid Paper Trading 系統 (`scripts/paper_trading_hybrid_full.py`)。
  - 透過 `scripts/testnet_executor.py` 執行 Testnet 下單、持倉同步、帳戶狀態輪詢。
  - WebSocket/輪詢方式讀行情與持倉，並將實際成交狀態寫回 Paper/AI 橋接。

- **主要依賴模組**
  - `scripts/paper_trading_hybrid_full.py`：Paper Trading 主流程、策略管理、持倉/績效記錄。
  - `scripts/testnet_executor.py`：Binance Testnet REST 下單/查詢、WS user-data/mark price，同步 Portfolio。
  - `scripts/testnet_websocket_integration.py`（可選）：Testnet WS 整合。
  - `config/strategy_sync_config.json`：統一模式、同步策略清單、滑價容忍度、槓桿等。

- **Binance 資料/接口**
  - REST：帳戶/持倉查詢、下單/撤單、槓桿/模式設定（Isolated/Hedge）、行情（最新價/深度）。
  - WebSocket：user data（成交通知、倉位變更）、mark price stream；另啟 liquidation forceOrder stream（爆倉瀑布雷達）。
  - 風控：滑價檢查、持倉一致性檢查；失敗/超時時拒單或停單。

- **輸出/產物**
  - 終端 Tee Logger：`logs/trading_terminal/trading_*.log`（包含持倉、爆倉壓力雷達、策略面板）。
  - Paper 回放資料：`data/paper_trading/pt_*/trading_data.json`、`trading.log`。
  - 狀態/橋接（若啟用）：`testnet_portfolio.json`、`ai_wolf_bridge.json` 等。

- **關鍵行為**
  - 1 秒級 WS/輪詢，同步 Testnet 實際倉位→Paper；偵測不一致時停單並重查。
  - 支援 Maker 優先、滑價容忍度（bps）、訊號過期撤單（依 config）。
  - 動態配置熱重載：`config/trading_strategies_dynamic.json`。

---

## 2) `scripts/ai_trading_advisor_gpt.py`
AI 顧問終端，讀取紙機/市場快照/持倉並生成交易建議，並透過橋接檔與交易系統互動。

- **核心職責**
  - 讀取最新 Paper Trading 資料、爆倉壓力快照、持倉狀態（含 Testnet 回饋）。
  - 套用 Sniper/Flip Cooldown/Decision Stability 配置，產出 AI 指令（方向/槓桿/TP/SL/倉位）。
  - 透過橋接檔（`ai_wolf_bridge.json` 等）雙向同步 AI ↔ 交易引擎狀態。

- **主要依賴資料/配置**
  - 狀態/計畫/記憶：`ai_advisor_state.json`、`ai_strategy_plan.json`、`ai_learning_memory.json`、`ai_market_memory.json`。
  - 配置：`config/ai_team_config.json`（團隊/參數），內建 Sniper/Flip/Decision 常量。
  - 橋接：`ai_wolf_bridge.json`（AI→交易指令、交易→AI 回饋）。
  - 市場快照：爆倉壓力/風控面板（如 `data/liquidation_pressure/latest_snapshot.json`，或執行 `scripts/fetch_binance_leverage_data.py` 生成）。

- **Binance 資料來源（間接）**
  - 透過已落地的快照/交易日誌取得：爆倉壓力 (long/short liq)、mark price/行情、VPIN/OBI 等微觀指標（由交易腳本計算或記錄）。
  - 不直接調 Binance；依賴交易系統產出的 JSON/Log。

- **輸出/產物**
  - 終端 Tee Logger：`logs/ai_terminal/ai_advisor_*.log`（AI 指令、健康分、決策理由）。
  - 橋接檔：`ai_wolf_bridge.json`（最新 AI 指令、交易回饋、微觀指標），供交易系統讀取。

- **關鍵行為**
  - 健康分/決策穩定/翻倉冷卻：降低反覆無常、控制高槓桿風險。
  - 若市場數據過期或橋接未同步，優先 HOLD，不盲目下指令。
  - 目標 ROI 5–10%（扣手續費/滑點後），會調整槓桿/倉位/TP-SL。

---

## 3) 相關程式/資料流（間接）
- 風控/指標：`src/metrics/leverage_pressure.py`、`scripts/fetch_binance_leverage_data.py`（爆倉壓力快照）; `src/exchange/vpin_calculator.py`、`src/exchange/obi_calculator.py`（微觀指標）。
- 策略配置/熱重載：`config/trading_strategies_dynamic.json`、`config/strategy_sync_config.json`。
- 主力偵測：`src/strategy/whale_strategy_detector.py`（策略機率/信號）、對應文檔 `docs/WHALE_*`。
- 日誌/資料：`logs/trading_terminal/*`、`logs/ai_terminal/*`、`data/paper_trading/pt_*/*`。

---

## 4) 快速理解系統在做什麼（最短清單）
1. **入口**：`paper_trading_testnet_hybrid.py` 啟動 Paper+Testnet；`ai_trading_advisor_gpt.py` 產出 AI 指令。  
2. **資料來源**：Binance Testnet REST/WS（行情、持倉、下單回報、forceOrder）、爆倉壓力快照、微觀指標（OBI/VPIN）。  
3. **決策**：策略/主力偵測 + AI 指令 + 風控檔位 + 健康分 → Maker 分批下單，訊號過期撤單。  
4. **輸出**：交易日誌 + AI 日誌 + 橋接檔 + Paper/回放資料。  
5. **同步/安全**：持倉/行情查詢失敗或延遲過大 → 不下單；Paper 計算與 Testnet 對齊，避免虛高 ROI。  

---

## 5) 角色拆解：主程式 / JSON / API / WebSocket

**主程式**
- `scripts/paper_trading_testnet_hybrid.py`：入口，串 Paper + Testnet，下單/同步/熱重載。
- `scripts/paper_trading_hybrid_full.py`：Paper 邏輯、策略管理、持倉/績效。
- `scripts/testnet_executor.py`：Binance Testnet REST 下單/查詢、WS user-data/mark price、持倉同步。
- `scripts/testnet_websocket_integration.py`（可選）：Testnet WS 整合。
- `scripts/ai_trading_advisor_gpt.py`：AI 顧問，產生 AI 指令、更新橋接檔。

**JSON / 設定 / 狀態**
- 策略/同步設定：`config/strategy_sync_config.json`、`config/trading_strategies_dynamic.json`。
- AI 狀態/記憶：`ai_advisor_state.json`、`ai_strategy_plan.json`、`ai_learning_memory.json`、`ai_market_memory.json`。
- 橋接：`ai_wolf_bridge.json`（AI ↔ 交易狀態/指令）、`testnet_portfolio.json`（實際持倉快照）。
- 市場快照：`data/liquidation_pressure/*.json`（爆倉壓力）。
- 日誌/回放：`logs/trading_terminal/*`、`logs/ai_terminal/*`、`data/paper_trading/pt_*/*`。

**API (Binance Testnet REST)**
- 下單/撤單：市價/限價；回傳成交價、手續費、滑點。
- 帳戶/持倉：保證金、槓桿、持倉量/方向。
- 行情/深度/mark price：最新價、spread、depth。
- 參數設定：Isolated/Hedge、槓桿、保險。
- 頻率：下單/查詢視事件；行情/深度可作 fallback 3–5s 輪詢。

**WebSocket**
- user data stream：成交/持倉變更（即時）。
- mark price stream：1s 行情更新。
- forceOrder stream：爆倉瀑布即時。
- 渠道：Testnet；若 WS 斷線 → 輪詢 fallback (3–5s)。

**協作頻率（摘要）**
- 行情/持倉：WS 1s；fallback 輪詢 3–5s。
- 爆倉瀑布：forceOrder 即時；爆倉快照 60s 更新。
- 配置熱重載：10–30s。
- AI 指令：約 5s~數十秒一輪，橋接檔同步。
- 訊號有效期（下單層）：2–3s，過期撤單；Maker 分批 + notional cap，必要時半倉市價追。

---

## 6) 細分流程圖（文字版）

```
[AI 顧問層] scripts/ai_trading_advisor_gpt.py (5s~數十秒迴圈)
  ├─ 讀取狀態/記憶 JSON: ai_advisor_state / ai_strategy_plan / ai_learning_memory / ai_market_memory
  ├─ 讀取配置: config/ai_team_config.json
  ├─ 讀取市場快照: data/liquidation_pressure/*.json
  ├─ 讀取橋接回饋: ai_wolf_bridge.json (交易→AI 微觀/持倉/行情)
  └─ 產生 AI 指令 → 寫回 ai_wolf_bridge.json (方向/槓桿/TP/SL/倉位)
        ▲                                       │
        │                                       ▼
---------------------------------------------------------------------
[交易入口層] scripts/paper_trading_testnet_hybrid.py (1s 主迴圈)
  ├─ 載入配置: strategy_sync_config.json / trading_strategies_dynamic.json (10–30s 熱重載)
  ├─ 啟動 Paper 邏輯: scripts/paper_trading_hybrid_full.py
  ├─ 啟動 Testnet 執行器: scripts/testnet_executor.py
  │    ├─ REST: 下單/撤單、持倉/帳戶查詢、槓桿/模式設定、行情/深度 (fallback 3–5s)
  │    ├─ WS user-data: 成交/持倉變更 (1s)
  │    ├─ WS mark price: 行情 (1s)
  │    └─ WS forceOrder: 爆倉瀑布即時
  ├─ 讀 AI 桥: ai_wolf_bridge.json (AI 指令) → 檢查風控檔位/健康分 → Maker 分批下單
  ├─ 寫回橋接: ai_wolf_bridge.json (實際持倉/行情/微觀指標/成交價/滑點)
  └─ 記錄日誌: logs/trading_terminal/trading_*.log；Paper 資料 data/paper_trading/pt_*

[Binance Testnet]
  ├─ REST/WS 回饋: 成交、手續費、滑點、持倉變更、行情/深度
  └─ forceOrder 流: 爆倉事件 (瀑布雷達)

[動態/記憶]
  ├─ 爆倉壓力快照: 每 60s 更新 (fetch_binance_leverage_data.py)
  ├─ AI 健康分/記憶：由實際成交/滑點/勝率回寫
  └─ 配置熱重載：10–30s 更新 trading_strategies_dynamic.json
```
