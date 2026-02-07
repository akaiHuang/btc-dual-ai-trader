# 🐋 主力偵測系統 (Whale Detector) 核心總覽

彙整目前主力偵測/決策相關的主要程式、配置與輸出物，便於開發與排錯。

## 1) 主要程式與模組
- `src/strategy/whale_strategy_detector.py`  
  - `WhaleStrategyDetector`：主控，整合各偵測器、計分與策略機率輸出。  
  - 偵測器：`StopHuntDetector`、`SupportResistanceBreakDetector`、`VolumeExhaustionDetector`、`HiddenOrderDetector`、`PriceVolumeDivergenceDetector`、`WaterfallDropDetector`、`OrderFlowDistortionDetector`。  
  - 輸出：`StrategyPrediction`（包含 `detected_strategy`、`strategy_probabilities`、`key_signals`、`risk_warnings`、`predicted_action/price/timeframe` 等）。
- `docs/WHALE_STRATEGY_DETECTOR_V2.md`  
  - 模式詳解與指標權重矩陣、使用範例。
- `docs/WHALE_DETECTOR_DEV_PLAN.md` / `docs/WHALE_DETECTOR_SUMMARY.md` / `docs/WHALE_DETECTOR_V4_DEV_PLAN.md`  
  - 開發計劃、模式字典、行動規則、風控檔位。

## 2) 配置 / 策略檔
- `ai_whale_strategy.json`  
  - 主力策略參數/權重/閾值（若有），供偵測器或決策邏輯讀取。
- （其他全域配置視執行腳本而定，如 `config/strategy_sync_config.json`、`config/trading_strategies_dynamic.json`）

## 3) 執行與關聯腳本
- 交易/實測流程（依啟動腳本而定）：  
  - `scripts/paper_trading_testnet_hybrid.py`、`scripts/paper_trading_hybrid_full.py` 等會載入/呼叫主力偵測結果，用於決策和面板。
  - `scripts/ai_trading_advisor_gpt.py` 可讀取主力/鯨魚橋接資料並產生建議。

## 4) 產出 / 輸出檔
- 偵測結果（程式內部）：`StrategyPrediction` 物件  
  - 包含策略機率、關鍵信號、風險警告、預測動作/價格/時間框架。  
  - 可被交易腳本用於下單、面板或橋接檔案更新。
- 日誌與橋接：
  - 交易日誌：`logs/trading_terminal/trading_*.log`（含主力/鯨魚面板、風控檔位、持倉、滑點等）。  
  - AI 終端日誌：`logs/ai_terminal/ai_advisor_*.log`（AI 決策、健康分、訊號）。  
  - 橋接檔（若啟用雙腦/同步）：例如 `ai_wolf_bridge.json` 或其他 *_bridge.json，紀錄 AI ↔ 交易引擎/鯨魚狀態。
- 回測/紙機資料：`data/paper_trading/pt_*/trading_data.json`、`trading.log`（依腳本而異，用於離線分析）。

## 5) 指標與特徵（常用欄位）
- 微觀：OBI / OBI Velocity、Spread/Depth、Hidden Orders、Order Flow 異常。  
- 流動性/毒性：VPIN、Liquidation Pressure（爆倉瀑布/軋空軋多）、WPI（鯨魚壓力指數）。  
- 價量：Volume Ratio/Exhaustion、Price/Volume Divergence、假突破/假跌破。  
- 事件/特殊：Spoofing/Layering、Funding/News front-run（若有）。

## 6) 行動/風控（簡述）
- 風控檔位（VPIN/Spread/瀑布/OBI）：檔位越高越禁止交易/降槓桿/縮倉。  
- Maker 優先 + 限價偏移 + 分批 notional cap；訊號有效期 2–3 秒，過期撤單。  
- 健康分：影響槓桿/倉位/TP/SL，勝率/滑點/VPIN/鯨魚命中率/OBI 同向率等綜合。  
- 訊號失效：過期、檔位升級、健康分跌破、鯨魚/OBI/VPIN 反向 → 撤單或減倉。

> 若需深入規則與數值，請對照 `docs/WHALE_DETECTOR_DEV_PLAN.md` 與 `docs/WHALE_STRATEGY_DETECTOR_V2.md`。***
