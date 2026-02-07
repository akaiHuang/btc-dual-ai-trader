# 🐺🐲 Wolf & Dragon 策略備份
> 備份時間：2025-11-26
> 備份原因：創建新策略 🦐🐦 前的完整備份

## 📊 當前設定統計

### 勝率分析（基於 126 筆交易）

| 持倉時間 | 交易數 | 勝率 | 平均 ROI | 總 PnL |
|---------|-------|------|---------|--------|
| 30s-1min | 7 | 0% | -5% | -$17.48 |
| 1-1.5min | 72 | 22% | -2.51% | -$90.38 |
| 1.5-2min | 16 | 31% | -1.97% | -$15.79 |
| 2-2.5min | 9 | 67% | +0.53% | +$2.40 |
| **2.5-3min** | 5 | **100%** | **+3.22%** | **+$8.05** |
| 3-4min | 7 | 43% | -3.1% | -$10.85 |
| 4-5min | 6 | 67% | +2.83% | +$8.48 |
| 5min+ | 4 | 75% | +0.65% | +$1.31 |

### 獲利 vs 虧損特徵

| 特徵 | 獲利交易 (42筆) | 虧損交易 (84筆) |
|-----|----------------|----------------|
| 平均持倉 | **2.44 分鐘** | **1.41 分鐘** |
| 主要出場 | TRAILING_STOP (26) | STOP_LOSS (43) |

---

## 🐺 M_AI_WHALE_HUNTER (Wolf) 設定

### Bridge 檔案：`ai_wolf_bridge.json`

```json
{
  "ai_to_wolf": {
    "command": "LONG/SHORT/HOLD/ADD_LONG/ADD_SHORT/CUT_LOSS",
    "direction": "BULLISH/BEARISH/NEUTRAL",
    "confidence": 50-100,
    "leverage": 30-75,
    "whale_reversal_price": <price>,
    "take_profit_pct": 10,
    "stop_loss_pct": 5
  }
}
```

### 策略邏輯

1. **死水盤策略** (ATR < 0.05%):
   - 在 AI 預測的反轉點做反向單
   - 目標獲利：0.4%
   - 槓桿：75x

2. **反轉點埋伏** (鯨魚集中度 > 0.8):
   - 距離反轉點 0.1%-0.5% 時進場
   - 做反向單，等待鯨魚反轉
   - 目標獲利：1%
   - 槓桿：60x

3. **標準 AI 信號**:
   - 3/3 一致 + 高集中度：槓桿 65x
   - 其他情況：槓桿 30-55x
   - 目標獲利：0.8%

### 出場邏輯

- **Take Profit**: AI 指定 (預設 10%)
- **Stop Loss**: AI 指定 (預設 5%)
- **AI Flip**: AI 方向反轉時出場
- **Trailing Stop**: 追蹤止損

---

## 🐺🔄 M_INVERSE_WOLF 設定

### 與 Wolf 的差異

- 使用相同的 `ai_wolf_bridge.json`
- **所有信號反轉**：
  - LONG → SHORT
  - SHORT → LONG
  - ADD_LONG → ADD_SHORT
  - ADD_SHORT → ADD_LONG

### 反轉包裝器 (Line 3071-3081)

```python
def finalize_wrapper(d):
    if is_inverse:
        act = d.get('action')
        if act == 'LONG': d['action'] = 'SHORT'
        elif act == 'SHORT': d['action'] = 'LONG'
        elif act == 'ADD_LONG': d['action'] = 'ADD_SHORT'
        elif act == 'ADD_SHORT': d['action'] = 'ADD_LONG'
        
        if act in ['LONG', 'SHORT', 'ADD_LONG', 'ADD_SHORT']:
            d['reason'] = f"[INVERTED] {d.get('reason', '')}"
    return finalize(d)
```

---

## 🐲 M_DRAGON 設定

### Bridge 檔案：`ai_dragon_bridge.json`

```json
{
  "ai_to_dragon": {
    "command": "LONG/SHORT/HOLD",
    "direction": "BULLISH/BEARISH/NEUTRAL",
    "confidence": 50-100,
    "leverage": 30,
    "stop_loss_pct": 5,
    "take_profit_pct": 10
  }
}
```

### 與 Wolf 的差異

- 使用不同的 Bridge 檔案 (`ai_dragon_bridge.json`)
- 由 Qwen3 AI 驅動（Wolf 由 GPT-4 驅動）
- 策略邏輯相同，但 AI 決策獨立

---

## 🔧 MODE_CONFIGS 設定

### 位置：Line 978-979

```python
TradingMode.M_AI_WHALE_HUNTER: 60,  # 冷卻時間 60s
TradingMode.M_DRAGON: 60            # 冷卻時間 60s
```

### Emoji 設定

```python
TradingMode.M_AI_WHALE_HUNTER: '🐺M'
TradingMode.M_INVERSE_WOLF: '🐺🔄'
TradingMode.M_DRAGON: '🐲M'
```

---

## 📈 出場邏輯 (Line 4900-4980)

### AI 控制的出場條件

1. **CUT_LOSS**: AI 發出 CUT_LOSS 指令
2. **AI Flip**: AI 方向從 LONG→SHORT 或 SHORT→LONG
3. **Stop Loss**: unrealized_pnl_pct < -stop_loss_pct (預設 5%)

```python
# 4. 止損保護 (Stop Loss) - 預設 5%
stop_loss_pct = ai_cmd.get('stop_loss_pct', 5.0)
if unrealized_pnl_pct < -stop_loss_pct:
    should_exit = True
    exit_reason = f"{prefix} Stop Loss: {unrealized_pnl_pct:.2f}% < -{stop_loss_pct}%"
```

---

## 🎯 已知問題

1. **勝率低** (整體 33%): 主要因為持倉時間太短
2. **AI 方向準確率**: 只有 18.18%
3. **太早出場**: 71% 交易在 1-2 分鐘內出場，勝率只有 24%
4. **止損太寬**: 虧損交易平均 -4.6%

---

## 🦐🐦 新策略建議

基於上述分析，新策略應該：

1. **最小持倉時間**: 2 分鐘（強制等待）
2. **目標持倉**: 2.5-3 分鐘
3. **Take Profit**: 5% (到了就跑)
4. **Stop Loss**: 2% (錯了早跑)
5. **進場門檻**: confidence > 70%

預期改進：
- 勝率從 33% → 70%+
- 每筆獲利目標 $5-10 (100U 本金)

---

## 🦐🐦 新策略已實作！

### M_SHRIMP (🦐) 設定 - 使用 GPT-4 (Wolf Bridge)

```python
{
    'bridge_file': 'ai_wolf_bridge.json',  # GPT-4
    'min_holding_seconds': 120,     # 最小持倉 2 分鐘
    'max_holding_seconds': 180,     # 最大持倉 3 分鐘
    'take_profit_pct': 5.0,         # 5% ROI 止盈
    'stop_loss_pct': 2.0,           # 2% ROI 止損
    'leverage': 50,                 # 固定 50x 槓桿
    'min_confidence': 70,           # 進場門檻
    'min_confluence': 2,            # 需要 2/3 確認
    'disable_trailing_stop': True   # 禁用追蹤止損
}
```

### M_BIRD (🐦) 設定 - 使用 Kimi (Dragon Bridge)

```python
{
    'bridge_file': 'ai_dragon_bridge.json',  # Kimi
    'min_holding_seconds': 120,     # 最小持倉 2 分鐘
    'max_holding_seconds': 180,     # 最大持倉 3 分鐘
    'take_profit_pct': 5.0,         # 5% ROI 止盈
    'stop_loss_pct': 2.0,           # 2% ROI 止損
    'leverage': 50,                 # 固定 50x 槓桿
    'min_confidence': 70,           # 進場門檻
    'min_confluence': 2,            # 需要 2/3 確認
    'disable_trailing_stop': True   # 禁用追蹤止損
}
```

### 關鍵差異（vs Wolf/Dragon）

| 項目 | 🐺🐲 Wolf/Dragon | 🦐🐦 Shrimp/Bird |
|------|-----------------|-----------------|
| 最小持倉 | 20秒 | **2 分鐘** |
| 最大持倉 | 無限制 | **3 分鐘** |
| Take Profit | 動態 (0.8%-6%) | **固定 5%** |
| Stop Loss | 5% | **2%** |
| 進場門檻 | 無限制 | **confidence > 70%** |
| 追蹤止損 | 啟用 | **禁用** |
| 冷卻時間 | 60秒 | **180秒** |

### 配置文件

- 設定檔：`ai_shrimp_config.json`
- Bridge 來源：共用 `ai_wolf_bridge.json`

### 預期表現

- 目標 ROI/筆：5%
- 目標勝率：70%+
- 目標交易數：20 筆/天
- 目標日報酬：100% (20 × 5%)
