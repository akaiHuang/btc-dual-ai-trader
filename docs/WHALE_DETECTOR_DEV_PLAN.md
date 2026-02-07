# 🧭 主力偵測/決策 V4.0 開發文件（建議版）

針對目前系統的優化方向與主力模式 V4.0，提供流程、風控、訊號、決策與時序的建議設計。

---

## A. 鯨魚訊號即時鏈路（1 秒 WS）
- **流程**：鯨魚/大單 WS (1s) → 訊號生成 (含支配度、方向、淨量、OBI/VPIN 當下值) → 「有效期 2–3 秒」 → 決策判斷 → 過期丟棄。
- **過期策略**：若 WS 訊號到決策超過 3 秒，直接廢棄，避免滑點吃光 edge。
- **併發控制**：決策鎖（同一秒僅處理最新訊號），避免排隊造成過期。
- **補償**：WS 失聯 → 回退 3–5 秒輪詢模式；延遲 >1.5s 時直接禁止下單。
- **記錄**：統計有效訊號/過期丟棄/延遲封鎖次數，回饋健康分。

---

## B. 風控門檻表（VPIN / Spread / 瀑布 / OBI）
| 檔位 | 條件 | 動作 |
|------|------|------|
| 極危 | VPIN > 0.85 或 Spread > 8bps 或 瀑布等級高 | 禁開倉；有倉減倉/平倉 |
| 危險 | VPIN 0.75–0.85 或 Spread 6–8bps | 降槓桿半檔、僅 Maker 小單、縮倉位 50% |
| 謹慎 | VPIN 0.6–0.75 或 Spread 4–6bps | Maker 優先、小限價偏移；OBI 必須同向且≥0.3 |
| 正常 | VPIN < 0.6 且 Spread < 4bps 且 無瀑布 | 正常策略；可用高槓桿區間 |
| OBI 反向 | OBI 與鯨魚/AI 方向衝突 | HOLD 或小倉對沖；不開新倉 |

> 瀑布 = Liquidation Cascade 等級高，或 WaterfallDropDetector 觸發。

---

## C. AI 健康分 → 槓桿/倉位調節
- **健康分構成**：近 20 筆勝率 + 平均滑點 + VPIN 均值/尖峰 + 鯨魚訊號命中率 + OBI 同向率。
- **分檔**：
  - <0.3：禁開倉；有倉減半/平倉。
  - 0.3–0.6：槓桿降 2 檔，倉位 50%，TP/SL 收緊。
  - 0.6–0.8：正常。
  - >0.8：可打開高槓桿（60–100x），但仍需風控檔位通過。
- **回寫**：每次實單成交結果回寫健康分，失敗高滑點則下調。

---

## D. Maker 優先與限價偏移（目標淨 ROI 5–10%）
- 進場以 Maker 為主，限價 = mid ± 1–2 bps（隨 Spread 擴張調整）。
- 若超時未成交且訊號仍有效，可轉半倉市價追；訊號過期則取消。
- **分批與額度**：設定 notional cap；Maker 分層（例如 40/30/30）；若首批未成且訊號仍有效，再追市價半倉；否則全撤。
- 下單前計算 `預期 edge - (手續費 + 預估滑點)`，若 <0 則不下單。
- 持倉 TP/SL 對應槓桿：60–100x 目標現貨波動 0.10–0.20% 即可達成 6–12% ROI。

---

## E. Testnet 為主的下單守則
- 每次下單前查 Testnet 持倉/行情（價/深度/費率），查詢失敗或超時 >1–2 秒 → 不下單。
- 成交後以「實際成交價 + 手續費/滑點」更新 AI/健康分；禁止使用 Paper 填補缺口。
- 持倉同步：若 API/WS 不一致，先停單並重查，不自動覆蓋。

---

## F. Paper 計算校正
- 將 Paper 端費率、滑點、成交價邏輯與 Testnet 對齊；如無真實成交價，使用 Testnet 成交價或放棄記錄，不產生虛假高報酬。
- 禁止 Paper 端獨立計算不含手續費/滑點的 ROI；統一用實盤公式。

---

## G. 主力模式 V4.0：策略/指標/程式對應
資料源：`src/strategy/whale_strategy_detector.py`，文檔：`docs/WHALE_STRATEGY_DETECTOR_V2.md`

| 策略 | 主要特徵指標 | 程式位置/偵測器 |
|------|--------------|-----------------|
| 吸籌建倉 | OBI 中性、VPIN 低、籌碼集中度高、量增價平/價量背離(累積)、隱藏買單 | `WhaleStrategyDetector.detect` + `PriceVolumeDivergenceDetector` + `HiddenOrderDetector` |
| 誘空吸籌 | 假跌破(量縮)、下方隱藏買單、止損掃蕩高、WPI 正向 | `SupportResistanceBreakDetector` (fake break) + `HiddenOrderDetector` + `StopHuntDetector` |
| 誘多派發 | 假突破(量縮)、上方隱藏賣單、止損掃蕩高、空頭背離 | `SupportResistanceBreakDetector` + `HiddenOrderDetector` + `PriceVolumeDivergenceDetector` |
| 拉高出貨 | OBI 強多、VPIN 高、巨量放大、上漲中量能衰竭、空頭背離 | `VolumeExhaustionDetector` + `PriceVolumeDivergenceDetector` |
| 洗盤震倉 | 下跌中量能極度萎縮、止損掃蕩高、隱藏買單承接 | `VolumeExhaustionDetector` + `StopHuntDetector` + `HiddenOrderDetector` |
| 試盤探測 | 中等量能異動、小幅拉升/打壓、訊號弱 | `WhaleStrategyDetector.detect` 基礎判分 |
| 對敲拉抬 | 量增但 OBI/VPIN 不支撐，成交模式異常 | `OrderFlowDistortionDetector` |
| 砸盤打壓 | OBI 強空、WPI 強負、瀑布式下跌、量先放後縮 | `WaterfallDropDetector` |

常用指標：OBI、OBI Velocity、VPIN、WPI、Liquidation Pressure、Volume Ratio/Exhaustion、Hidden Orders、Price/Volume Divergence、Support/Resistance Fake/True。

---

## G-1. 模式字典（對應圖示分類）
- **誘騙類（Trap）**：Bull Trap, Bear Trap, Fakeout, Stop Hunt → `SupportResistanceBreakDetector` + `StopHuntDetector` + 隱單；假突破/假跌破則反向，真突破則順勢。
- **洗盤類（Shakeout）**：Whipsaw, Consolidation Shake, Flash Crash, Slow Bleed → `VolumeExhaustion` + 瀑布/止損掃蕩；量縮=見底/頂候選，量放=避開。
- **吸籌/派發（Accum/Distrib）**：Accum, Re-accum, Distribution, Re-distribution → 價量背離 + 隱單 + 籌碼集中度；上漲/下跌中的累積/派發。
- **槓桿/爆倉（Liquidation）**：Long/Short Squeeze, Cascade → Liquidation Pressure + VPIN；順著軋空/軋多/瀑布。
- **趨勢類（Trend）**：Momentum Push, Continuation, Reversal → OBI + VWAP 偏離 + VPIN；順勢/反轉依微觀確認。
- **特殊（Special）**：News front-run, Funding play, Spoofing, Layering, Painting Tape → 事件或委託異常；偵測到 spoof/layering 時降低權重或暫停。

---

## G-2. 模式→特徵→偵測器→行動（精簡表）
| 模式 | 主要特徵 | 偵測器 | 建議行動 |
|------|----------|--------|---------|
| Bull Trap / 假突破 | 突破壓力、量能不足、長上影、隱藏賣單 | SupportResistanceBreak + StopHunt + HiddenOrder | 反向做空；限價靠近突破位；訊號 2–3 秒有效，過期撤單 |
| Bear Trap / 假跌破 | 跌破支撐、量能不足、長下影、隱藏買單 | SupportResistanceBreak + StopHunt + HiddenOrder | 反向做多；限價靠近支撐；訊號 2–3 秒有效，過期撤單 |
| Fakeout | 關鍵位突破後快速反轉 | SupportResistanceBreak | 依反轉方向輕倉/試單；未成即撤 |
| Stop Hunt | 長上下影、針狀 K 線、掃損後反轉 | StopHunt | 反向小倉；觀察 VPIN/Spread 檔位 |
| Whipsaw / Shake | 劇烈震盪、量縮或量放 | VolumeExhaustion + StopHunt | 觀望或縮倉；量放時禁開 |
| Flash Crash / Slow Bleed | 瀑布連陰 / 緩跌放量後量縮 | WaterfallDrop + VolumeExhaustion | 順勢做空或避開；量縮見底可小倉反彈 |
| Accum / Re-accum | 量增價平、VPIN 低、隱藏買單、集中度高 | PriceVolumeDivergence + HiddenOrder + ChipConcentration | 偏多，分批 Maker 低檔吸 |
| Distribution / Re-distrib | 量增價平或價跌、隱藏賣單、集中度下降 | PriceVolumeDivergence + HiddenOrder | 偏空，逢反彈分批做空 |
| Long/Short Squeeze | 爆倉壓力偏一側、VPIN 高、OI 快變 | Liquidation Pressure + VPIN | 順勢短打；VPIN 過檔則降槓桿/縮倉 |
| Cascade Liquidation | 瀑布 + 高 VPIN + OI 急跌 | WaterfallDrop + Liquidation Pressure | 順勢；設定更緊 TP/SL；檔位升級即減倉 |
| Momentum/Continuation | OBI 同向、VWAP 偏離、量放 | OBI + VWAP 偏離 + Volume | 順勢；Maker 分批；VPIN/Spread 檔位需通過 |
| Reversal | 反向背離、假突破/跌破、OBI 翻轉 | PriceVolumeDivergence + SupportResistanceBreak | 反向試單，小倉，訊號過期撤 |
| Spoofing/Layering/Painting | 掛單扭曲、假量 | OrderFlowDistortion | 降權重或暫停，避免被誘騙 |
| News/Funding Play | 事件/資金費率異常 | 外部事件 + Funding | 依事件方向，小倉或觀望 |

---

## H. 主力機率計算（頻率與權重）
- **頻率**：核心邏輯 1 秒一次（與 WS 同步）；若負荷高，可 1–3 秒但訊號過期 3 秒丟棄。
- **計算**：各偵測器給分（0–1），乘以權重後 softmax/normalize 成機率分佈；保留 top1 策略 + 其信號列表。
- **平滑**：可用短滑窗 (3–5 秒) 做指數平滑，避免抖動，但過期訊號仍要丟棄。

---

## I. 機率 → 行動
1) 檢查風控檔位（VPIN/Spread/瀑布/OBI 衝突），不通過即 HOLD。  
2) 若通過：  
   - 健康分檔位決定槓桿/倉位/TP/SL。  
   - 鯨魚訊號有效且同向 → 掛 Maker 分批限價單（notional cap）；訊號過期則取消，未成交部位不追。  
3) 已有持倉：若策略機率顯著轉向、或風控升級到危險/極危 → 減倉或平倉。
4) 訊號失效處理：訊號過期、檔位升級、健康分跌破 0.3、或鯨魚/OBI/VPIN 反向時，撤未成交單；持倉則依風控檔位決定減倉/平倉。

---

## J. 進出場判斷與時序
- **進場條件**：風控通過 + 健康分通過 + 鯨魚訊號有效 + OBI 同向 + VPIN < 檔位閾值。  
  - 價格/時間：以最新 WS 行情 + Depth；限價 = mid ± 1–2bps，成交超時即撤。  
  - 多空方向：取策略 top1 方向（含 OBI/鯨魚/WPI 一致性）。  
- **出場預測**：  
  - TP/SL 由策略配置 + 健康分調整；  
  - 若 VolumeExhaustion 或 反向背離/瀑布 觸發，提前止盈/減倉；  
  - 若 VPIN/Spread/瀑布檔位升級，立即檢查是否平倉。  
- **方向修正訊號**：  
  - 鯨魚方向翻轉且支配度高；  
  - OBI 快速翻轉；  
  - VPIN 飆升超檔；  
  - WaterfallDropDetector/StopHunt 偵測到反向異常；  
  - 連續高滑點/健康分跌破 0.3。

---

## K. 串接現有系統的時序建議
1) WS (1s) → 鯨魚訊號 + 行情/深度 → 立即生成主力特徵 → 機率分佈。  
2) 風控檢查（VPIN/Spread/瀑布/OBI 衝突） → 健康分 → 行動決策。  
3) 下單（Maker 優先，限價偏移）→ 成交後回寫健康分/指標 → 狀態同步至 Testnet Portfolio + AI 橋。  
4) 持倉監控：每秒檢查風控檔位/瀑布/背離/鯨魚翻轉 → 觸發減倉/平倉。  
5) 終端/日誌：記錄採用的檔位、健康分、過期訊號丟棄次數、滑點實際值。

---

## L. 待辦/驗證清單（落地時）
- 實作過期丟棄與決策鎖；WS 延遲退避。
- 風控檔位常數化（VPIN/Spread/瀑布/OBI），集中配置。
- 健康分計算與回寫；失敗案例下調。
- Maker 優先與限價偏移公式；分批 notional cap；超時轉市價半倉或取消。
- Testnet 查詢失敗 → 不下單；Paper 與 Testnet 計算對齊。
- 日誌新增必記欄位：檔位、健康分、Top 模式/機率、採用指標值、下單型態/限價偏移、實際滑點、訊號有效/過期丟棄次數、延遲/封鎖原因。
