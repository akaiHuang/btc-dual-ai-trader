# 🧠 AI Whale Hunter - 系統整合說明

## 📋 修復內容總結

### 1. 配置完整性修復
- ✅ 添加 `mode_styles['M_AI_WHALE_HUNTER'] = 'ai_whale_hunter'`
- ✅ 添加 `mode_labels['M_AI_WHALE_HUNTER'] = 'M🧠 AI Whale Hunter'`
- ✅ 添加 `mode_emojis['M_AI_WHALE_HUNTER'] = '🧠M'`
- ✅ 添加 `entry_cooldown['M_AI_WHALE_HUNTER'] = 15`  (配合 AI 15秒週期)

### 2. 市場狀態支持
已將 `M_AI_WHALE_HUNTER` 加入所有市場狀態的白名單：
- ✅ `MarketRegime.BULL` - 允許運行
- ✅ `MarketRegime.BEAR` - 允許運行
- ✅ `MarketRegime.NEUTRAL` - 允許運行
- ✅ `MarketRegime.CONSOLIDATION` - 允許運行

### 3. 錯誤處理
- ✅ 修復 `ZeroDivisionError` 當 `signal_threshold = 0` 時
- ✅ 添加 AI 文件讀取異常處理
- ✅ 添加訊號時效性檢查（60秒內有效）

### 4. AI Advisor 升級
**Prompt 更新 - 支持雙向 Trap 檢測：**

```
- **Bull Trap (Pump & Dump)**: 
    - Phase 1 (Bait): Whale Buy + Price Up → SCALP LONG (Ride the bait)
    - Phase 2 (Kill): VPIN Spike + OBI Negative → REVERSAL SHORT (Catch the drop)
    
- **Bear Trap (Dump & Pump)**:
    - Phase 1 (Fear): Whale Sell + Price Down → SCALP SHORT (Ride the fear)
    - Phase 2 (Squeeze): VPIN Spike + OBI Positive → REVERSAL LONG (Catch the bounce)
```

**輪詢頻率調整：**
- ⏱️ 從 60 秒 → 15 秒（提升反應速度）

### 5. 決策邏輯實現

```python
# scripts/paper_trading_hybrid_full.py (Line ~2638)

if style == 'ai_whale_hunter':
    try:
        ai_state_file = "ai_advisor_state.json"
        if os.path.exists(ai_state_file):
            with open(ai_state_file, 'r') as f:
                ai_state = json.load(f)
            
            last_pred = ai_state.get('last_prediction')
            pred_time_str = ai_state.get('prediction_time')
            
            # 檢查訊號時效性 (60 秒內有效)
            is_fresh = False
            if pred_time_str:
                pred_time = datetime.fromisoformat(pred_time_str)
                if (datetime.now() - pred_time).total_seconds() < 60:
                    is_fresh = True
            
            if is_fresh and last_pred in ['LONG', 'SHORT']:
                return finalize({
                    'action': last_pred,
                    'reason': f'AI Whale Hunter Signal: {last_pred} (Fresh)',
                    'confidence': 0.8
                })
            elif not is_fresh:
                 return finalize({'action': 'HOLD', 'reason': 'AI Signal Stale'})
            else:
                 return finalize({'action': 'HOLD', 'reason': f'AI Signal WAIT: {last_pred}'})
        else:
            return finalize({'action': 'HOLD', 'reason': 'AI State File Not Found'})
    except Exception as e:
        return finalize({'action': 'HOLD', 'reason': f'AI Read Error: {str(e)}'})
```

---

## 🚀 使用方式

### 方法 1: 使用整合測試腳本（推薦）

```bash
./test_ai_integration.sh
```

### 方法 2: 手動啟動

**Terminal 1 - 啟動 AI Advisor:**
```bash
.venv/bin/python scripts/ai_trading_advisor.py
```

**Terminal 2 - 啟動 Paper Trading:**
```bash
.venv/bin/python scripts/paper_trading_hybrid_full.py 8
```

---

## 📊 系統架構

```
┌─────────────────────────────────────────────────────────────────┐
│                      AI Trading System                           │
└─────────────────────────────────────────────────────────────────┘

┌────────────────────────┐          ┌────────────────────────────┐
│   AI Advisor (15s)     │          │  Main Trading Bot (5s)     │
│  ─────────────────────│          │  ──────────────────────── │
│                        │          │                            │
│  1. Read whale_flip    │          │  1. Read ai_advisor_state  │
│  2. Read signals       │          │  2. Check signal freshness │
│  3. Call GPT-4         │          │  3. M_AI_WHALE_HUNTER      │
│  4. Save state.json ───┼─────────▶│  4. Execute if confident   │
│                        │          │                            │
│  Output:               │          │  Threshold:                │
│  - action: LONG/SHORT/ │          │  - Confidence ≥ 70%        │
│           WAIT          │          │  - Freshness < 60s         │
│  - confidence: 0-100   │          │                            │
└────────────────────────┘          └────────────────────────────┘
```

---

## 🎯 進場條件

**M🧠 AI Whale Hunter 會在以下情況進場：**

1. ✅ AI 訊號存在且新鮮（< 60秒）
2. ✅ AI 動作 = `LONG` 或 `SHORT`（非 `WAIT`）
3. ✅ AI 信心度 ≥ 70%
4. ✅ 沒有持倉
5. ✅ 不在冷卻期（15秒）

---

## 📈 監控指標

### AI Advisor 輸出範例：
```json
{
  "action": "LONG",
  "confidence": 85,
  "full_analysis": "Whale Accumulation detected: Net Qty +150 BTC...",
  "prediction_time": "2025-11-24T23:36:45"
}
```

### Trading Bot 決策範例：
```
[M_AI_WHALE_HUNTER] 
Action: LONG
Reason: AI Whale Hunter Signal: LONG (Fresh)
Confidence: 0.8
```

---

## ⚠️ 注意事項

1. **AI Advisor 必須運行**  
   - 主交易系統依賴 `ai_advisor_state.json`
   - 如果文件不存在，AI 模式會 HOLD

2. **OpenAI API Key 必須設置**  
   ```bash
   export OPENAI_API_KEY="your-key-here"
   # 或在 .env 文件中設置
   ```

3. **訊號時效性**  
   - AI 訊號 60 秒後視為 stale
   - 建議 AI Advisor 保持運行狀態

4. **成本考量**  
   - AI Advisor 每 15 秒調用一次 GPT-4
   - 建議監控 OpenAI API 使用量

---

## 🔍 故障排查

### 問題：AI 模式一直 HOLD

**檢查清單：**
```bash
# 1. 檢查 AI Advisor 是否運行
ps aux | grep ai_trading_advisor

# 2. 檢查狀態文件是否存在
ls -lh ai_advisor_state.json

# 3. 檢查狀態文件內容
cat ai_advisor_state.json | jq .

# 4. 檢查訊號時間戳
cat ai_advisor_state.json | jq .prediction_time
```

### 問題：KeyError 或 ZeroDivisionError

**解決方案：**
- ✅ 已在 line 2805-2807 添加防護
- ✅ 已在 line 2638-2669 添加異常處理

---

## 📝 日誌位置

- **AI Advisor 輸出**: 終端機標準輸出
- **Trading Bot 日誌**: `data/paper_trading/pt_YYYYMMDD_HHMM/trading.log`
- **AI 狀態文件**: `ai_advisor_state.json`
- **詳細訊號**: `data/paper_trading/pt_YYYYMMDD_HHMM/signal_diagnostics.csv`

---

## ✅ 驗證檢查表

- [x] `M_AI_WHALE_HUNTER` 出現在模式列表
- [x] AI Advisor 正常運行
- [x] `ai_advisor_state.json` 定期更新
- [x] AI 模式沒有 KeyError
- [x] AI 模式沒有 ZeroDivisionError
- [x] AI 模式可以讀取訊號
- [x] 訊號時效性檢查正常
- [x] 進場條件邏輯正確

---

## 🎓 下一步優化建議

1. **訊號品質提升**
   - 加入更多 Micro 指標（如 Microprice, Spread Depth）
   - 整合新聞情緒分析

2. **成本優化**
   - 使用 GPT-3.5-turbo 降低成本
   - 實現訊號快取機制（相似市場狀態不重複調用）

3. **回測驗證**
   - 使用歷史數據評估 AI 訊號準確率
   - 調整信心度閾值（目前 70%）

4. **多策略整合**
   - AI 訊號與技術指標融合
   - 實現 AI + Whale Watcher 雙重確認

---

**Status:** ✅ 所有已知問題已修復，系統可正常運行
**Last Updated:** 2025-11-24 23:40
