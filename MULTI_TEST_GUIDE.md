# 多視窗交易測試使用說明

## 快速開始

在 **外部終端** 運行以下命令：

```bash
cd /Users/akaihuangm1/Desktop/btn
bash scripts/launch_multi_tests.sh 24
```

這將打開 **4 個獨立終端視窗**：

1. **📥 數據收集** - 真實 WebSocket 數據保存
2. **💹 Phase C 原始** - VPIN 0.5 / 信號 0.6 閾值
3. **🔧 Phase C 調整** - VPIN 0.7 / 信號 0.5 閾值  
4. **⚡ HFT 對比** - 簡單高頻策略

---

## 測試配置

### 測試 1: 數據收集
- **WebSocket**: depth20@100ms + aggTrade
- **保存位置**: `data/test_runs/時間戳/snapshots/`
- **用途**: 未來準確回測

### 測試 2: Phase C 原始參數
```python
VPIN 閾值: 0.5        # 診斷發現 93.8% > 0.7
信號閾值: 0.6        # 很少達到
風險過濾: DANGER + CRITICAL 阻擋
```

**預期結果**: 
- 幾乎沒有交易（根據昨天日誌）
- 用於對比參數調整效果

### 測試 3: Phase C 調整參數
```python
VPIN 閾值: 0.7        # 放寬 (0.5 → 0.7)
信號閾值: 0.5        # 降低 (0.6 → 0.5)
風險過濾: 僅 CRITICAL 阻擋
```

**目標**: 
- 增加交易機會
- 驗證參數調整是否有效

### 測試 4: HFT 簡單策略
```python
策略: 價格偏離均值 > 0.02% 立即交易
手續費: Taker 0.05% × 2 = 0.1%
最小間隔: 60 秒
```

**目的**:
- 對比 Phase C 保守程度
- 了解高頻交易的交易量和手續費影響

---

## 監控進度

### 方式 1: 查看單個日誌
```bash
tail -f data/test_runs/*/logs/data_collection.log
tail -f data/test_runs/*/logs/phase_c_original.log
tail -f data/test_runs/*/logs/phase_c_adjusted.log
tail -f data/test_runs/*/logs/hft_comparison.log
```

### 方式 2: Tmux 分割視窗（推薦）
```bash
tmux new-session \; \
  split-window -h \; \
  split-window -v \; \
  select-pane -t 0 \; \
  split-window -v \; \
  send-keys -t 0 'tail -f data/test_runs/*/logs/data_collection.log' C-m \; \
  send-keys -t 1 'tail -f data/test_runs/*/logs/phase_c_original.log' C-m \; \
  send-keys -t 2 'tail -f data/test_runs/*/logs/phase_c_adjusted.log' C-m \; \
  send-keys -t 3 'tail -f data/test_runs/*/logs/hft_comparison.log' C-m
```

---

## 停止測試

### 停止所有測試進程
```bash
ps aux | grep 'python.*real_trading_simulation\|collect_historical\|hft_comparison' | grep -v grep | awk '{print $2}' | xargs kill
```

### 或手動關閉各個終端視窗
按 `Ctrl+C` 中斷各個測試

---

## 查看結果

### 測試完成後生成對比報告
```bash
python scripts/generate_comparison_report.py data/test_runs/時間戳/
```

報告內容:
- ✅ 交易次數對比
- ✅ 收益對比
- ✅ 參數調整效果
- ✅ 原始 vs 調整版本差異
- ✅ Phase C vs HFT 差異
- ✅ 數據源影響分析

### 結果文件位置
```
data/test_runs/20251111_HHMMSS/
├── logs/                          # 運行日誌
│   ├── data_collection.log
│   ├── phase_c_original.log
│   ├── phase_c_adjusted.log
│   └── hft_comparison.log
├── results/                       # JSON 結果
│   ├── phase_c_original.json
│   ├── phase_c_adjusted.json
│   └── hft_comparison.json
├── snapshots/                     # 真實數據
│   ├── BTCUSDT_orderbook_20251111.parquet
│   └── BTCUSDT_trades_20251111.parquet
└── README.txt                     # 測試信息
```

---

## 診斷發現總結

根據 `scripts/diagnose_strategy.py` 的分析：

### 🔴 問題 1: VPIN 持續過高
- **數據**: 平均 0.973，93.8% > 0.7
- **影響**: 所有 6 個交易信號被阻擋
- **原因**:
  1. VPIN 計算過於敏感？
  2. 真實市場確實有高 toxic flow？
  3. 閾值 0.5 太保守？

### 🔴 問題 2: 信號信心度不足
- **數據**: 平均 0.389，僅 0.5% 達到 0.6
- **影響**: 99.7% 決策為 NEUTRAL
- **原因**:
  - OBI 平均 -0.568 (負值居多)
  - 各指標加權後難達閾值

### ✅ 調整方案
1. **VPIN 閾值**: 0.5 → 0.7
2. **信號閾值**: 0.6 → 0.5
3. **風險過濾**: DANGER 允許交易

---

## 預期結果

### Phase C 原始 (昨天結果)
- 決策: ~2200
- 信號: ~6 (0.3%)
- 交易: 0 (全被 VPIN 阻擋)

### Phase C 調整 (預期)
- 決策: ~2200
- 信號: ~50-100 (2-5%)  ← 閾值降低
- 交易: 10-30 (20-60%) ← DANGER 允許

### HFT 簡單策略 (預期)
- 交易: 100-200 筆
- 頻率: 4-8 筆/小時
- 問題: 手續費高 (0.1% × 次數)

---

## 下一步

1. ✅ 啟動 24 小時測試（外部終端）
2. ⏳ 等待結果（明天同時）
3. 📊 生成對比報告
4. 🔄 根據結果進一步調整或進入 Phase E

---

## 注意事項

- ⚠️ 測試運行在後台，**不影響 VS Code 開發**
- ⚠️ 確保網絡穩定（24 小時 WebSocket 連接）
- ⚠️ 磁盤空間足夠（真實數據 ~100-200 MB/天）
- ⚠️ 可隨時查看各視窗進度
- ⚠️ `Ctrl+C` 中斷會正常關閉連接並保存數據
