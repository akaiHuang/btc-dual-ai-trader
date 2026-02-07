# Binance Lead → dYdX Follow：開發計畫（2025-12-19）

## 目標與結論（先講重點）
- **目標**：用 Binance 的「領先變動」作為 dYdX 進出場的加權訊號/門檻，提升方向單（dYdX-only）勝率與期望值，同時把「HALT diff」從不明確的數字變成可被驗證/量化的風控指標。
- **目前已知**（基於本 repo 的小樣本分析腳本結果）：
  - **close-to-close 的跨所價差**通常很小（<~0.15%），就算常回歸，扣掉費用後利潤很薄。
  - 使用「Binance 短窗領跌 + dYdX 溢價」觸發 dYdX short，在某些時段可觀察到事件與正期望，但需要更長區間驗證。
- **重要提醒**：`HALT diff: 3%+` 多半不是 mid-vs-mid 價差，而可能是 `expected_fill/VWAP`、滑價或資料不同步的風險警報；在沒把組成拆開紀錄前，不應當把它當套利訊號。

## 現況（你現在手上已經有的工具/修正）
- 交易端（paper/testnet）：`scripts/whale_testnet_trader.py`
  - 已修正 MTF RSI 在啟動/早期為 0 的問題（透過 `mtf_analyzer.latest_snapshot`）。
- 分析端：
  - `scripts/analyze_diff_reversion.py`：量化 dYdX vs Binance 1m close 價差分布與「回歸」模擬（偏中性對沖概念）。
  - `scripts/analyze_binance_lead_dydx_follow.py`：偵測「Binance leads, dYdX lags」事件並模擬 dYdX-only 方向單（支援 `--mode short|long`）。

## 主要風險與設計原則
- **可交易性（executability）優先**：任何 diff 都要能對應到「真實可成交」價格，而不是純指標。
- **時間對齊**：跨所比較必須用同一分鐘/同一秒級時間基準，並記錄資料來源延遲。
- **成本模型**：至少包含 taker fee、預估滑價、以及 dYdX funding 的影響（後者可先做為額外濾網）。

---

## 特徵總表（常見特徵、你在問的價差、以及可能的獲利方式）

> 下面把「你在系統裡常看到的數字」拆成可檢驗的特徵，並說明它通常代表什麼、怎麼用、以及常見陷阱。

| 分類 | 特徵 | 定義（建議紀錄/計算） | 代表意義 | 可能的獲利用法（最簡） | 常見陷阱 / 風險 |
|---|---|---|---|---|---|
| 跨所價差（可觀測） | `spread_pct` | $(dYdX\_close - Binance\_close)/Binance\_close$（同分鐘 1m close 對齊） | dYdX 相對 Binance 的溢價/折價 | **回歸**：溢價大時偏 short dYdX、折價大時偏 long dYdX（或做中性對沖） | close-to-close 幅度常很小；成本吃掉利潤；時間對齊錯會假訊號 |
| 跨所價差（可成交） | `effective_diff_signed_buy/sell` | 以 `expected_fill_buy/sell`（orderbook/VWAP）對 `binance_mid` 算 signed diff | 「真實能成交」的偏離（含滑價、深度不足） | **風控**：diff 大 → 拒單/降量/降槓桿；不是直接套利 | 常被誤讀成套利；其實多是滑價/薄盤/WS 不同步 |
| 跨所同步性 | `*_age_ms` / `*_ts` | Binance/dYdX 各自最新資料時間戳與延遲 | 資料是否新鮮、是否能跨所比較 | **濾網**：age 過大 → 不交易（避免假價差/假 lead） | 不記錄 age 就無法解釋「為何 diff 突然變很大」 |
| Binance lead | `binance_ret_window_pct` | $(P_t - P_{t-w})/P_{t-w}$，w=3~10m | Binance 短窗是否先走出趨勢 | **方向單**：lead down + dYdX premium → short dYdX；lead up + dYdX discount → long dYdX | 多頭/空頭偏態不對稱；門檻太高事件很少；門檻太低噪音大 |
| dYdX 跟隨/落後 | `spread_entry_pct` + `spread_compress` | 進場 spread 與之後是否收斂 | dYdX 是否「追上」Binance 的路徑 | **事件驗證**：用回測看「觸發後」收斂機率/速度 | 很可能只是 1m close 的幻覺；實盤滑價不等於 close |
| 波動/狀態 | `ATR` / `realized_vol`（可選） | 以 1m 近 N 根估計 | regime 判斷：高波動 vs 低波動 | **分桶**：高波動期才啟用 lead 策略（或調高 TP/SL） | 不分 regime 會把「特定時段有效」誤判成普適 |
| MTF RSI | `rsi_1m/5m/15m/1h/4h` | 由 MTF analyzer 快照（不可為 0） | 多週期動能/過熱過冷 | **濾網/加權**：只做與趨勢方向一致的 lead 事件 | RSI 在趨勢盤會鈍化；若快照不即時會出現 0/舊值 |
| MTF 一致性 | `mtf.aligned` | 多週期方向一致（你的 gate） | 趨勢一致性更高 | **提高勝率**：只在 aligned 時做方向單；或未 aligned 降槓桿 | 太嚴會導致 0 交易；需用回測調門檻 |
| 策略分數 | `signal_score` / 6D features | 你系統內的加總分數/子項（動能/成交量/OBI…） | 綜合可交易性 | **排序**：同樣事件內挑分數最高者；或做 position sizing | 分數若引用滯後概率會導致方向欄位延遲（你之前遇到的） |
| 風控狀態 | `risk_state` (CAN_TRADE/SUSPECT/HALT/ESCAPE) | 由 diff、staleness、其他健康度推斷 | 系統是否允許交易 | **保命**：Halt 就不進場；Suspect 只縮小倉位 | 若原因不透明（未記錄組成），會變成「黑盒」 |

---

## Phase 1 — 觀測/日誌：把 diff 變成可驗證的數據（最高優先）
### 交付物
- 在交易決策與風控狀態變更（尤其 `HALT/SUSPECT/ESCAPE`）時，寫入可回溯欄位（JSON line 或結構化 log）。

### 必記欄位（建議最小集合）
- 時間：`ts`（ISO）、`minute_bucket`（對齊用）
- Binance：`binance_mid`、`binance_last`、`binance_ts`、`binance_age_ms`
- dYdX：`dydx_mid`（或 oracle/mark/last 你實際用哪個）、`dydx_ts`、`dydx_age_ms`
- 成交預估：
  - `expected_fill_buy`、`expected_fill_sell`（由 orderbook/VWAP 推）
  - `effective_diff_signed_buy = (expected_fill_buy - binance_mid)/binance_mid`
  - `effective_diff_signed_sell = (expected_fill_sell - binance_mid)/binance_mid`
- 風控：`risk_state`、`halt_reason`、`thresholds`
- 交易候選：`signal_score`、`mtf_aligned`、RSI（至少 1m/5m/15m）

### 驗收方式
- 你能對任一筆 `HALT diff` 回放：
  - 它是 **dYdX mid 偏離**？還是 **expected_fill 偏離（滑價/深度不足）**？還是 **資料延遲**？

---

## Phase 2 — 離線驗證：把假設做成可重複的統計
### 目標
- 用 7 天（或更長）資料跑 parameter sweep，避免小樣本錯覺。

### 你要驗證的兩條路
1) **Spread Reversion（中性/價差回歸）**
- 指標：$spread = (dYdX_{close} - Binance_{close})/Binance_{close}$
- 事件：`abs(spread) >= entry` 進場，`abs(spread) <= exit` 出場或 timeout。
- 成功定義：扣費後的平均 pnl、勝率、持有時間分布。

2) **Binance Lead → dYdX Follow（方向/單邊）**
- Short：`Binance` 在 `window` 分鐘內下跌超過 `drop_pct` 且 dYdX 有溢價（`spread >= spread_entry`）
- Long：`Binance` 在 `window` 分鐘內上漲超過 `rise_pct` 且 dYdX 有折價（`spread <= -spread_entry`）
- 成功定義：TP/SL/horizon 的 win rate、avg pnl、尾端風險（max loss、連敗）。

### 建議輸出報表欄位
- 事件數、勝率、平均/中位 pnl、pnl 分位數、平均/中位持有、TP/SL/timeout 占比。
- 依「波動 regime / 交易時段」分桶（先簡單：UTC 時段；或用 ATR 分位）。

---

## Phase 3 — 紙上交易整合：把 lead 訊號變成可控的 gate
### 整合策略（最小改動版本）
- 不直接取代你現有的 score 系統；先做成 **額外 gate / 加權**：
  - 只有當 `binance_lead_signal == True` 時，才允許更激進的進場條件；或在 `score` 上加分。

### 風控連動
- 當 `effective_diff_signed_(buy/sell)` 達到風控閾值 → 降槓桿/降低下單量/直接拒單。
- 若 `age_ms` 過高 → 直接拒單（避免假訊號）。

### 驗收
- paper 跑 24h：
  - 記錄：觸發次數、實際下單次數、拒單原因 top N、PnL（含費用）。

---

## Phase 4 — 上線前檢查清單（最小必要）
- 任何跨所訊號都必須有：
  - 資料延遲保護（age gate）
  - 成本模型（fees + 預估滑價）
  - 黑天鵝保護（連續虧損/波動飆升/深度不足 → 降速或停機）

---

## 立即可跑的指令（你現在就能用）
- 一週快速驗證（同時跑：價差分布、回歸、lead/follow；會輸出 JSON 報表）：
  - `./.venv/bin/python scripts/backtest_week_theory.py --days 7 --mode both --out backtest_results/week_theory_YYYYMMDD.json`
- 價差回歸（中性概念，利潤薄，用來理解分布）：
  - `./.venv/bin/python scripts/analyze_diff_reversion.py --hours 12 --entry 0.08 --exit 0.03 --fees-bps 8`
- Binance lead / dYdX follow（方向單）：
  - Short：`./.venv/bin/python scripts/analyze_binance_lead_dydx_follow.py --hours 12 --mode short --spread-entry 0.08 --binance-drop-window 5 --binance-drop-pct 0.15 --horizon-min 15 --tp 0.40 --sl 0.60 --fees-bps 8`
  - Long： `./.venv/bin/python scripts/analyze_binance_lead_dydx_follow.py --hours 12 --mode long  --spread-entry 0.03 --binance-drop-window 5 --binance-drop-pct 0.15 --horizon-min 15 --tp 0.40 --sl 0.60 --fees-bps 8`

---

## 下一步（我建議我可以直接幫你做的事）
- 我可以直接在交易端補上 **Phase 1 的結構化日誌**（最小侵入），並附一個小工具把 log 轉成事件表（CSV/JSON），讓你能針對每次 `HALT diff` 回放。你要我先改哪個 bot（`whale_testnet_trader.py` 那支）？
