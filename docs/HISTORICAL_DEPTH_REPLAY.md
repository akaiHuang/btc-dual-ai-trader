# Historical Depth Replay Guide

本指南說明如何利用 Binance Data Portal (data.binance.vision) 的期貨深度資料
與本倉庫現有的 `HybridPaperTradingSystem` 結合，完成離線重放與回測。

## 1. 下載原始資料

```bash
python scripts/download_binance_depth.py \
  --symbol BTCUSDT \
  --start 2024-01-01 \
  --end 2024-01-02 \
  --dataset book \
  --output data/historical/depth_raw \
  --skip-existing
```

- `--dataset` 可選 `book` (bookDepth snapshot)、`depth` (差分檔) 或 `diff`。
- 腳本會自動解壓 `.zip` 並保留 CSV/JSON；如需節省空間可省略 `--keep-zip`。

## 2. 準備交易資料 (可選但建議)

`data/historical/BTCUSDT_agg_trades_20200101_20251115.parquet` 提供逐筆 agg trade。
若你分割出特定日期範圍，可自行產生新的 parquet 以降低記憶體需求。

## 3. 執行離線重放

```bash
python scripts/historical_depth_replay.py \
  --depth-dir data/historical/depth_raw \
  --depth-glob 'BTCUSDT-bookDepth-2024-01-01*.csv' \
  --start 2024-01-01T00:00:00 \
  --end 2024-01-01T06:00:00 \
  --agg-trades data/historical/BTCUSDT_agg_trades_20200101_20251115.parquet \
  --decision-interval 5 \
  --initial-capital 100 \
  --max-position 0.5
```

### 參數說明
- `--depth-dir`：放置官方下載後 CSV/JSON/ZIP 的資料夾。
- `--depth-glob`：可依日期篩選特定檔案，例如 `BTCUSDT-depth-2024-01-*.csv`。
- `--start`, `--end`：ISO8601 或 `YYYY-MM-DD`；若只給日期自動補 00:00:00 / 23:59:59。
- `--agg-trades`：Parquet 檔路徑，會依時間範圍過濾；若省略則 VPIN/SignedVolume 以零近似。
- `--decision-interval`：每多少秒觸發一次決策循環。
- `--print-status`：每多少秒輸出一次目前倉位/排行榜。
- `--dry-run`：只解析檔案，不跑策略邏輯；可用來確認資料品質。

## 4. 產出物

- `data/paper_trading/pt_YYYYMMDD_HHMM/` 下仍會生成 JSON / log / signal diagnostics。
- `logs/` 內可搭配 `signal_diagnostics.csv` 對 Mode6 做深入分析。

## 5. 常見問題

| 問題 | 解法 |
|------|------|
| 解析 CSV 失敗 | 確認檔案為官方原版 (通常一行一個 JSON)。若是其他格式，可先手動轉為 JSON 行。 |
| 記憶體不足載入 agg trades | 先用 pandas 將 parquet 切分成每日檔案，再以 `--agg-trades` 指向較小的檔。 |
| 沒有 trade 檔案 | `--agg-trades` 可省略，策略仍能執行，但 VPIN / SignedVolume 將近似為 0。 |
| 速度太慢 | 調整 `--depth-glob` 只覆蓋短時間、或使用 `--decision-interval 10` 降低決策頻率。 |

## 6. 下一步

- 若要進一步串接 `MarketReplayEngine` 或其他策略，可復用 `scripts/historical_depth_replay.py` 中的
  `DepthReplayRunner` 與 `iter_depth_files` 工具函式。
- 亦可擴充 `download_binance_depth.py` 以支援 weekly / monthly dataset 或自動排程。

如需更多自動化（例如將 ZIP 轉為 Parquet、整合 Promise-based pipeline），可在 `scripts/`
底下新增自訂工具，但請複用此指南的輸出目錄結構以保持一致性。
