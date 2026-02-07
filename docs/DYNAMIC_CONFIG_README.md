# 🔄 動態策略配置系統 (Dynamic Strategy Configuration System)

## 📋 概述

這是為 HybridPaperTradingSystem 設計的動態策略配置系統，讓你可以：

- ✅ **熱更新策略參數**：修改 JSON 後自動生效，無需重啟程式
- ✅ **統一配置管理**：所有模式（M0-M9 + 未來 M_MICRO 系列）都用 JSON 控制
- ✅ **規則引擎驅動**：策略邏輯由配置 + 規則引擎決定，不寫死在程式裡
- ✅ **即時實戰驗證**：改完參數立即在實盤/紙面交易看效果

## 🏗️ 架構

```
┌─────────────────────────────────────────────────────────────────┐
│                  HybridPaperTradingSystem                       │
│                                                                 │
│  ┌──────────────────┐      ┌──────────────────┐               │
│  │ ModeConfigManager│◄─────┤ JSON Config File │               │
│  │ (熱更新管理)      │      │ (策略參數)        │               │
│  └────────┬─────────┘      └──────────────────┘               │
│           │                                                     │
│           │ get_config()                                        │
│           ▼                                                     │
│  ┌──────────────────┐      ┌──────────────────┐               │
│  │   RuleEngine     │◄─────┤ Market Snapshot  │               │
│  │ (決策評估)        │      │ (實時行情)        │               │
│  └────────┬─────────┘      └──────────────────┘               │
│           │                                                     │
│           │ evaluate_entry()                                    │
│           ▼                                                     │
│  ┌──────────────────┐                                          │
│  │ SimulatedOrder   │                                          │
│  │ (執行開倉/平倉)   │                                          │
│  └──────────────────┘                                          │
└─────────────────────────────────────────────────────────────────┘
```

## 📁 檔案結構

```
config/
  └── trading_strategies_dynamic.json  # 主要配置檔（可熱更新）

src/strategy/
  ├── mode_config_manager.py          # 配置管理器（載入、驗證、熱更新）
  └── rule_engine.py                  # 規則引擎（評估進出場條件）

scripts/
  ├── paper_trading_hybrid_full.py    # 主系統（已整合動態配置）
  └── test_dynamic_config.py          # 測試腳本
```

## 🚀 快速開始

### 1. 測試配置系統

```bash
cd /Users/akaihuangm1/Desktop/btn
python3 scripts/test_dynamic_config.py
```

這會測試：
- ✅ 配置載入
- ✅ 模式啟用狀態
- ✅ 規則引擎決策
- ✅ 熱更新功能演示

### 2. 運行紙面交易（with 動態配置）

```bash
python3 scripts/paper_trading_hybrid_full.py 3  # 運行 3 小時
```

系統會：
- 自動載入 `config/trading_strategies_dynamic.json`
- 每 10 秒檢查配置檔是否更新
- 偵測到更新時自動重新載入（無需重啟）

### 3. 熱更新示範

**步驟：**

1. 啟動紙面交易：
   ```bash
   python3 scripts/paper_trading_hybrid_full.py 3
   ```

2. 在另一個終端／編輯器打開配置檔：
   ```bash
   code config/trading_strategies_dynamic.json
   ```

3. 修改任一模式的參數，例如把 M0 的 `tp_pct` 從 `1.0` 改成 `1.5`

4. 儲存檔案

5. 觀察紙面交易終端，你會看到：
   ```
   🔄 [22:30:15] Config hot-reloaded!
   ✅ Config reloaded: +0 modes, -0 modes, ~1 modified
      Modified: M0_ULTRA_SAFE
   ```

6. 新開的倉位會立即使用新的 TP/SL 參數！

## 📝 配置檔格式

### 基本結構

```json
{
  "modes": {
    "模式名稱": {
      "enabled": true,           // 是否啟用
      "type": "BASELINE",        // 模式類型
      "description": "說明",
      "emoji": "🛡️M0",
      
      "leverage": 10,            // 槓桿
      "position_size": 0.5,      // 倉位比例
      "tp_pct": 1.0,             // 止盈 %
      "sl_pct": 0.8,             // 止損 %
      
      "entry_cooldown_seconds": 120,     // 開倉冷卻（秒）
      "max_positions_per_day": 30,       // 單日最大開倉次數
      
      "regime_whitelist": [],            // Regime 白名單
      "regime_blacklist": ["CONSOLIDATION"], // Regime 黑名單
      
      "entry_rules": {                   // 進場規則
        "rsi": {"min": 35, "max": 65},
        "obi": {"min_abs": 0.1},
        "volume_factor": 1.0
      },
      
      "signal_weights": {                // 訊號權重
        "obi": 0.4,
        "funding": 0.3,
        "whale": 0.3
      },
      
      "min_direction_score": 0.4         // 最小方向分數
    }
  }
}
```

### 模式類型 (type)

- `BASELINE` / `SAFE` / `NORMAL` / `AGGRESSIVE` / `SANDBOX`: Hybrid 混合模式（M0-M6）
- `SNIPER_BREAKOUT`: 突破狙擊（M7）
- `SNIPER_VOLUME`: 量能狙擊（M8）
- `SNIPER_VOLATILITY`: 波動狙擊（M9）
- `MICRO1`: 順勢微利（未來）
- `MICRO2`: 盤整逆勢微利（未來）
- `MICRO3`: 事件／量能爆發微利（未來）

### 進場規則 (entry_rules)

支援的條件：

```json
"entry_rules": {
  "rsi": {
    "min": 40,        // RSI 最小值
    "max": 70         // RSI 最大值
  },
  "obi": {
    "min_abs": 0.15   // OBI 絕對值最小值
  },
  "volume_factor": 1.1,                    // 量能倍數（當前 / MA）
  "whale_min_concentration": 0.65,         // 巨鯨集中度最小值
  "whale_min_net_volume": 10.0             // 巨鯨淨量最小值（BTC）
}
```

### Regime 過濾

```json
"regime_whitelist": ["BULL", "BEAR"],      // 只允許這些 regime
"regime_blacklist": ["CONSOLIDATION"],     // 禁止這些 regime
"required_trend_states": ["STRONG_UP", "STRONG_DOWN"]  // 需要的趨勢狀態
```

## 🎯 常見使用場景

### 場景 1：微調 TP/SL

**需求**：M2 模式常常觸及 SL，想放寬一點

**操作**：
```json
"M2_NORMAL_PRIME": {
  "tp_pct": 0.8,   // 保持不變
  "sl_pct": 0.8    // 從 0.6 改成 0.8
}
```

**效果**：儲存後，下一筆 M2 交易會使用新的 0.8% SL

### 場景 2：暫停某個模式

**需求**：M5 最近虧太多，暫停它

**操作**：
```json
"M5_ULTRA_AGGRESSIVE_PRIME": {
  "enabled": false  // 從 true 改成 false
}
```

**效果**：M5 立即停止開新倉（舊倉照常管理）

### 場景 3：調整進場頻率

**需求**：M1 交易太頻繁，想降低頻率

**操作**：
```json
"M1_SAFE_PRIME": {
  "entry_cooldown_seconds": 150,  // 從 75 改成 150
  "max_positions_per_day": 20     // 從 35 改成 20
}
```

**效果**：M1 的開倉間隔變長，單日上限降低

### 場景 4：改變 Regime 適用範圍

**需求**：想讓 M2 也能在 CONSOLIDATION 盤交易

**操作**：
```json
"M2_NORMAL_PRIME": {
  "regime_blacklist": []  // 移除 CONSOLIDATION 黑名單
}
```

**效果**：M2 可以在所有 regime 下交易

## 🔍 監控與除錯

### 查看配置狀態

在 Python 中：
```python
from src.strategy.mode_config_manager import ModeConfigManager

manager = ModeConfigManager()
status = manager.get_status()
print(status)
# {
#   'config_path': 'config/trading_strategies_dynamic.json',
#   'last_load_time': '2025-01-18T22:30:15',
#   'total_modes': 10,
#   'enabled_modes': 9,
#   'load_error': None
# }
```

### 檢查模式是否啟用

```python
if manager.is_enabled("M0_ULTRA_SAFE"):
    print("M0 已啟用")
```

### 獲取模式配置

```python
config = manager.get_config("M1_SAFE_PRIME")
print(f"M1 槓桿: {config['leverage']}x")
print(f"M1 TP/SL: {config['tp_pct']}% / {config['sl_pct']}%")
```

## ⚠️ 注意事項

### 1. JSON 格式錯誤

如果 JSON 格式有誤（例如少逗號、括號不對），系統會：
- ❌ 拒絕載入錯誤的配置
- ✅ 保持使用上一次成功載入的配置
- 📝 在 log 中顯示錯誤訊息

**最佳實踐**：用 JSON 驗證工具檢查格式，例如：
```bash
python3 -m json.tool config/trading_strategies_dynamic.json
```

### 2. Schema 驗證

必要欄位：
- ✅ `enabled` (boolean)
- ✅ `type` (string)
- ✅ `leverage` (number > 0)
- ✅ `tp_pct` (number > 0)
- ✅ `sl_pct` (number > 0)

如果缺少這些欄位，配置會被拒絕。

### 3. 熱更新不影響現有倉位

重要：
- ✅ 新配置只影響「之後」開的倉位
- ✅ 已開的倉位會繼續使用「開倉時」的 TP/SL
- ⚠️ 如果要修改現有倉位，需要手動平倉重開

### 4. 更新頻率

系統預設每 10 秒檢查一次配置檔，可調整：
```python
self.config_reload_interval = 10.0  # 秒
```

## 🚧 未來擴展

### Phase 5: M_MICRO 系列

配置檔已預留 M_MICRO 範例：
```json
"M_MICRO1_1m": {
  "enabled": false,        // 目前停用，待實作
  "type": "MICRO1",
  "timeframe": "1m",
  "leverage": 8,
  "tp_pct": 0.5,
  "sl_pct": 0.5,
  // ...
}
```

實作完成後，只需：
1. 把 `enabled` 改成 `true`
2. 微調參數
3. 儲存即可啟用！

### WebSocket 整合（Phase 5 規劃）

未來可以擴展成：
- 外部 LLM 透過 WebSocket 發送「策略建議」
- Rule Engine 接收建議，更新內存中的 config
- 不需要改檔案，直接透過 API 動態調整策略

## 📚 相關文件

- `docs/DEVELOPMENT_PLAN.md` → Phase 5: AI Command System 完整規劃
- `config/trading_strategies.json` → 舊版靜態配置（參考用）
- `src/strategy/hybrid_multi_mode.py` → Hybrid 策略原始碼

## 🤝 貢獻與回饋

如果你發現任何問題或有改進建議，歡迎：
1. 在 log 中記錄問題
2. 修改配置嘗試不同參數
3. 回報哪些設定組合效果最好

祝交易順利！🚀
