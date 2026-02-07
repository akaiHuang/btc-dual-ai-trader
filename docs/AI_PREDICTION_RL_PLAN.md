# 🤖 AI 預測與強化學習開發計畫 v1.2

**硬體環境**：macOS M1 Max 64GB（可用 Metal 加速），本地開發為主。  
**資料來源**：`data/historical/*` 價格/量/L0/大單，`data/news_history_multi/*` 新聞文本。  
**目標**：建立「價格/走勢預測 + AI 決策強化」雙路線，輸出可接入 Hybrid/Multi-Mode 策略的訊號與動作建議，維持毫秒級應變（策略層）與分鐘級模型更新（預測層）。  
**量化收益目標（加密日內風格，大膽但需可回測驗證）**：  
- 中等風險路徑：目標 1d 2x（100 → 200）在高波事件日才開啟，平日保持保守；勝率 +5~10% 絕對值，盈虧比 ≥1.6，MDD < 30%。  
- 進攻事件路徑：高波/爆倉壓力/量價共振時，目標 1d ROI 10~30%；控制事件內 MDD < 25%，連虧即停。  
- 極端押注（可選）：極端共振 + 流動性真空時，允許小倉位高槓桿押注，目標單次 10~20x，甚至 40x，但需隔離倉位、強制爆倉保護、連虧停機與滑點敏感度測試。

**關鍵共振訊號（優先用現有指標）**：
- `Liquidation Pressure`: `L_long_liq / L_short_liq` 極端且同向 → 加權方向/倉位倍率。
- `OBI` / `OBI Velocity`: 失衡幅度與速度同向突破。
- `VPIN`: 低毒性（安全放行）或極高毒性（避開/反向押注）。
- `Spread/Depth`: 流動性真空或單側深度稀薄 → 突破穿透力強。
- `Whale concentration / whale_flip`: 主動大單集中度與方向持續；累積小單→大單。
- `Volume Spike / Volatility`: 量價齊升，ATR/波動上行；突破伴隨成交量斜率增加。
- `Funding / Basis`: 極端正/負 funding + OI 增長，辨識擁擠邊（軋空/殺多）。
- `Event clock`: 美股開盤/FOMC/鏈上事件窗口 → 放寬進攻模式；平時收緊。

## 🧩 模組開發狀態（AI 沙盒不影響現有程式）
- ✅ `ai_dev/README.md`：目標與安全邊界。
- ✅ `ai_dev/config.py`：路徑/視窗/標籤閾值設定。
- ✅ `ai_dev/data_pipeline.py`：1m 價格 +（可選）新聞計數 → 特徵/標籤 Parquet；測試生成 `ai_dev/artifacts/dataset_sample.parquet`。
- ✅ `ai_dev/train_supervised.py`：基線訓練腳本（LightGBM→XGBoost→LogReg 順降）；時間切分、輸出模型。
- ✅ `ai_dev/inference.py`：讀模型 + 資料集，對最新樣本做推論（不動既有程式）。
- ✅ `ai_dev/env_wrapper.py`：gym-like env skeleton（Dummy 可測試），待接 `hybrid_backtester`。
- 🟡 `ai_dev/train_rl.py`：已加入 Dummy plumbing 與 TODO 清單；需補 HybridTradingEnv + BC/離線 RL 實作。
- ✅ `ai_dev/trajectory_export.py`：離線 RL/BC 軌跡匯出占位（現為 synthetic，需換為 backtester 輸出）。
- ✅ `ai_dev/replay_env.py`：Dataset-based 模擬 env（hold/long/short 動作 + reward 基於 future_ret）。
- ✅ `ai_dev/hybrid_env.py`：HybridTradingEnv 骨架，接受 Runner 注入（避免強耦合），可用 DummyRunner 冒煙。
- ✅ `ai_dev/dataset_runner.py`：DatasetRunner（用 future_ret 模擬 PnL），供 HybridTradingEnv 先行測試。
- ✅ `ai_dev/policy_adapter.py`：將模型預測轉成策略 bias JSON（direction/confidence/position multiplier/leverage），不動原程式。
- ✅ `ai_dev/strategy_bias_applier.py`：讀 bias JSON，列印建議 patch（手動套用 config）。
- ✅ `ai_dev/backtester_runner.py`：事件流 + MarketReplayEngine 指標 runner，obs/action/reward/position_state 可用；`train_rl.py` 可 `--hybrid-backtester-runner` 冒煙。
- ✅ `ai_dev/event_mode_patch.py`：生成事件模式 patch JSON（提升槓桿/倉位，多模式可配置），不直接改檔。
- ✅ `ai_dev/apply_bias_to_config.py`：將 bias 套用到 config 副本或 --inplace。
- ✅ `ai_dev/export_rl_buffer.py`：支援 DatasetRunner / `--use-backtester` 匯出 JSONL buffer（obs/action/reward/done）。
- ✅ HybridTradingEnv/backtester runner：事件流 obs/action/reward/position_state 已可用。
- ✅ 軌跡匯出 + BC/離線 RL（基底）：真實事件 buffer 已可生成；BC 仍在 `train_rl.py`，離線 RL 需裝 sb3/d3rlpy 後再跑。
- ✅ 推論 API + 策略接線：`policy_adapter.py` 產 bias；`apply_bias_to_config.py`/`strategy_bias_applier.py` 可套用；`event_mode_apply.py`/`event_mode_patch.py` 生成事件模式配置。
- ✅ 事件模式接線：`event_mode_apply.py` 高波/爆倉日 boost 槓桿/倉位（輸出副本），提供指令。

---

## 🎯 開發路線（階段性，未完成留空）
- [ ] **Stage 0：環境與工具棧**
  - 框架：優先 PyTorch + MPS；如需 TF，使用 `tensorflow-macos` + `tensorflow-metal`（確認 `torch.mps.is_available()` 和 `tf.config.list_physical_devices('GPU')`）。
  - 套件：`pyarrow`（Parquet）、`lightgbm/xgboost`、`sentence-transformers`、`faiss-cpu` 或 `chromadb`；RL 可先裝 `stable-baselines3`（PPO/SAC）+ `d3rlpy`（BC/CQL/BCQ）。
  - 共用特徵管線：Parquet + JSON/News → 統一 UTC；缺值補齊、極端值裁剪（如 5x IQR）、重採樣一致化（1m 基準）。
- [ ] **Stage 1：監督式預測基線**
  - 標籤：未來 5/15/60m 報酬（二元/多類）與連續報酬；regime（爆量上漲/平盤/爆量下跌）。
  - 特徵：多尺度報酬/波動/ATR、量能 Z-score、VWAP 偏離、OBI/VPIN/Spread/Depth、簽名成交量、Whale concentration、時間週期特徵；新聞情緒/主題 embedding（以 `date` 對齊）。
  - 模型：LightGBM/XGBoost 基線；時序 TCN/Transformer Encoder；ablation：無新聞 vs 含新聞；產出特徵重要度。  
  - 短期量化目標：勝率比現行提升 +5% 絕對值；含新聞版本在高波窗口的 direction 命中率/盈虧比較無新聞版提升 ≥10%。
- [ ] **Stage 2：即時推論與穩健性**
  - 延遲測試：單筆/批量 MPS 推論；在 `paper_trading_hybrid_full` 模擬內部延遲，確定不阻塞毫秒級 OBI/VPIN。
  - 降級策略：新聞缺失 → 價格-only 模型；資料 stale → 提高風控（減倉/加冷卻）；模型不可用 → 回退既有規則。
- [ ] **Stage 3：強化學習環境**
  - 環境：基於 `src/backtesting/hybrid_backtester.py` 封裝 gym-like；state = 技術/微觀/新聞特徵 + 持倉；action = {開多/開空/平倉/維持} × 倉位/槓桿 bucket（對應 `trading_strategies_dynamic.json` 的 position_size/leverage 檔位）；reward = PnL-費用-滑點-風險懲罰 (MDD、連虧)。
  - 演算法：行為克隆暖啟（用過去最佳規則交易軌跡），再做離線 RL（CQL/BCQ）或 PPO/SAC；使用時間分段 walk-forward。
  - 短期量化目標：與基線規則相比，回測/紙交的 Sharpe/Sortino 提升 ≥15%，連虧次數下降 ≥10%，事件窗口 ROI 分佈右移（高分位數更高）。
- [ ] **Stage 4：策略接線與動態調諧**
  - 預測訊號 → Multi-Mode：作為 regime filter、方向 prior、position_size_multiplier；RL 動作映射到模式/槓桿/TP-SL（`trading_strategies_dynamic.json`）。
  - 低延遲：策略層維持 OBI/VPIN/Spread/Depth 毫秒級；模型層提供分鐘級 bias，避免過度頻繁覆寫。
- [ ] **Stage 5：評估與灰度上線**
  - 指標：命中率、盈虧比、Sharpe/Sortino、MDD、持倉時間、交易次數、費用佔比；A/B（開/關 AI），含手續費/滑點敏感度。
  - 灰度：紙交小倉位 → 放量 → 實盤只讀提示 → 實盤小倉位；每階段門檻（MDD < X、Sharpe > Y）。
- [ ] **Stage 6：LLM 助手（可選）**
  - 本地 LLM（Ollama）僅做新聞/行情摘要、風險解釋，不直接下單；接入行情+新聞快照輸出人類可讀報告。
  - 如要自動調參，限制為建議稿，由人/規則層審核後套用，避免 LLM 直接改策略。

---

## 🛠️ 工具棧與硬體建議（M1 Max 64GB）
- 深度學習：PyTorch + MPS；需 TF 時再裝 `tensorflow-macos` + `tensorflow-metal`。確保 BLAS/OMP 不與 MPS 打架（設定 `export PYTORCH_ENABLE_MPS_FALLBACK=1`）。
- NLP：`sentence-transformers`（MiniLM/多語 Roberta）+ 中文情緒模型（FinBERT-CH/Roberta-wwm-ext）；Embedding 可緩存到 Parquet。
- 數據：`pyarrow` 讀 Parquet；新聞 JSON 轉中繼 Parquet（schema：title/content/date/source/情緒分數/embedding/was_published_at）；重採樣對齊 1m。
- 向量查詢：`faiss-cpu`（小型），需要附註索引版本；或 `chromadb` 本地檔案模式。

---

## 📋 任務拆解（細項）
1) 安裝/驗證：PyTorch(MPS)/TF-metal、pyarrow、lightgbm/xgboost、sentence-transformers、faiss-cpu/chromadb；確認 MPS 可用。  
2) 特徵管線：價格 Parquet + 新聞 JSON → UTC 對齊；補缺口、極端值裁剪；生成技術特徵（報酬/波動/ATR/量能 Z-score/VWAP 偏離）與 OBI/VPIN/Spread/Depth/Whale 特徵；新聞情緒/主題 embedding；cache。  
3) 監督模型：時間分段（2020-2022 train，2023 val，2024 test）；LightGBM/XGBoost + TCN/Transformer；ablation（無新聞 vs 含新聞）；輸出特徵重要度。  
4) 即時推論：封裝批/單筆推理 API，量測延遲；stale/缺值降級；推理結果寫入中繼（供 `paper_trading_hybrid_full` 使用）。  
5) RL 環境：將 `hybrid_backtester` 包成 gym-like；定義 action/reward（含費用、滑點、風險懲罰）；BC 暖啟 + CQL/BCQ/PPO；walk-forward 報告。  
6) 策略接線：將預測/動作映射到 `config/trading_strategies_dynamic.json`（regime whitelist/blacklist、min_direction_score、position_size_multiplier、tp/sl 調節）；保留 VPIN/流動性/爆倉壓力風控。  
7) 灰度與監控：紙交統計/告警（MDD、命中率、費用佔比）；A/B 對照；LLM 只做解說/告警文字。

---

## ⚠️ 風險與守則
- 避免資料洩漏：新聞用 `date`（發布時間），不可用 `scraped_at`；標籤窗口內特徵不得使用未來價量。  
- 成本與滑點：回測/訓練納入手續費/滑點敏感度；控制推論頻率，避免在毫秒層過度重計。  
- LLM 角色：僅解說/告警，不直接控單；任何策略改動需經規則層/人工確認。  
- 監控：缺值、延遲時自動降級（保守模式/減倉/拉長冷卻）；記錄模型版本、特徵集、決策，便於追溯。

---

## 🚀 下一步（可執行順序）
1) 建環境：裝 PyTorch(MPS)/TF-metal + pyarrow + lightgbm/xgboost + sentence-transformers。  
2) 資料中繼：新聞 JSON → Parquet（UTC 對齊）；抽樣檢查價格檔欄位/缺口；生成情緒基線。  
3) 監督基線：LightGBM/XGBoost + TCN/Transformer；產出無新聞 vs 含新聞對照與特徵重要度。  
4) 推理 API：封裝單筆/批推理，量測延遲並加入 stale/缺值降級；輸出中繼供策略使用。  
5) RL：建 gym-like 環境，BC 暖啟，再跑 CQL/BCQ/PPO，產出 walk-forward 報告。  
6) 策略接線：映射到 `config/trading_strategies_dynamic.json`（方向 bias、倉位倍率、tp/sl 調整），保持 VPIN/流動性/爆倉風控。  
7) 灰度：紙交 A/B，觀察 MDD/Sharpe/費用佔比；再考慮 LLM 解說管道，不讓 LLM 控單。  
