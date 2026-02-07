# 📊 策略文檔資料夾

本資料夾包含所有交易策略相關的文檔和分析報告。

## 📑 文檔清單

### 🎯 核心文檔

| 文檔 | 描述 | 更新日期 |
|------|------|---------|
| **[COMPLETE_STRATEGIES_OVERVIEW.md](COMPLETE_STRATEGIES_OVERVIEW.md)** | **13 個策略完整總覽** ⭐ 主文檔 | 2025-11-12 |

### 📈 策略開發報告

| 文檔 | 描述 | 更新日期 |
|------|------|---------|
| [11_STRATEGIES_COMPLETION_REPORT.md](11_STRATEGIES_COMPLETION_REPORT.md) | Mode 0-10 策略系統完成報告 | 2025-11-11 |
| [9_STRATEGIES_COMPLETION_REPORT.md](9_STRATEGIES_COMPLETION_REPORT.md) | Mode 0-8 策略初版報告 | 2025-11-10 |
| [STRATEGY_MANAGER_INTEGRATION_REPORT.md](STRATEGY_MANAGER_INTEGRATION_REPORT.md) | 策略管理器系統架構 | 2025-11-10 |

### 🔬 策略分析

| 文檔 | 描述 | 更新日期 |
|------|------|---------|
| [HFT_STRATEGY_ANALYSIS.md](HFT_STRATEGY_ANALYSIS.md) | OBI 高頻交易策略深度分析 | 2025-11-10 |
| [HFT_COMPLETE_TEST_REPORT.md](HFT_COMPLETE_TEST_REPORT.md) | HFT 完整測試報告與結論 | 2025-11-10 |

### 🎨 技術指標策略

| 文檔 | 描述 | 更新日期 |
|------|------|---------|
| [MODE_8_TECHNICAL_INDICATORS.md](MODE_8_TECHNICAL_INDICATORS.md) | Mode 8 技術指標詳細說明 | 2025-11-11 |
| [MODE_8_9_10_COMPARISON.md](MODE_8_9_10_COMPARISON.md) | Mode 8/9/10 對比分析 | 2025-11-11 |

---

## 🎯 13 個策略快速索引

| Mode | 策略名稱 | Emoji | 槓桿 | 狀態 |
|------|---------|-------|------|------|
| M0 | 無風控基準 | 🤖❌ | 5x | ✅ 啟用 |
| M0' | 反向交易 | ❌🤖 | 5x | ❌ 禁用 |
| M1 | VPIN風控 | 🤖🟡 | 3x | ✅ 啟用 |
| M2 | 流動性風控 | 🤖🔵 | 3x | ✅ 啟用 |
| M3 | 完整風控 | 🤖🟢 | 5x | ✅ 啟用 |
| M4 | 趨勢跟隨 | 🤖🟣 | 4x | ✅ 啟用 |
| M5 | 均值回歸 | 🤖🟠 | 2x | ✅ 啟用 |
| M6 | 動態止損 | 🤖⚪ | 3x | ✅ 啟用 |
| M7 | 混合策略 | 🤖🔴 | 4x | ✅ 啟用 |
| M8 | 技術指標寬鬆 | 🤖📊 | 3x | ✅ 啟用 |
| M9 | 技術指標嚴格 | 🤖📈 | 2x | ✅ 啟用 |
| M10 | 技術指標關閉 | 🤖📉 | 4x | ✅ 啟用 |
| **M13** | **自適應多時間框架** | **🤖🌈** | **3x** | **✅ 啟用** |
| **M14** | **動態槓桿優化** | **🤖🐳** | **5-25x** | **✅ 啟用** |

---

## 📊 最新策略文檔

### 🆕 M14 動態槓桿優化策略

| 文檔 | 描述 | 更新日期 |
|------|------|---------|
| **[M14_DYNAMIC_LEVERAGE_STRATEGY.md](M14_DYNAMIC_LEVERAGE_STRATEGY.md)** | **M14 完整策略說明** ⭐ 新增 | 2025-11-12 |

**核心特性**：
- 🎯 動態槓桿調整（5-25x）根據 VPIN/波動率/信號強度
- 🔄 三方案自適應切換（A保守/B平衡/C積極）
- 📊 市場狀態檢測（TRENDING/VOLATILE/CONSOLIDATION/NEUTRAL）
- 💎 信號質量評分系統（多因子綜合 0-1）
- 💰 成本感知盈利計算（考慮手續費和滑價）
- ⚡ 動態倉位調整（20-70%）
- 🎯 動態止盈止損（TP: 0.8-3.5%, SL: 0.4-2.0%）

**目標性能**：
- 方案 A：48小時翻倍（勝率 75%+）
- 方案 B：36小時翻倍（勝率 72%+）
- 方案 C：24小時翻倍（勝率 70%+）

---

## 📊 最新測試結果 (2025-11-12)

**測試條件**: 60分鐘，高毒性市場 (VPIN 0.859)

| 排名 | 策略 | ROI | 交易次數 |
|------|------|-----|---------|
| 🥇 | M8 技術指標寬鬆 | +0.00% | 0 |
| 🥈 | M9 技術指標嚴格 | +0.00% | 0 |
| 🥉 | **M13 自適應多時間框架** | **+0.00%** | **0** |
| 4 | M5 均值回歸 | -1.12% | 5 |
| ... | ... | ... | ... |
| 12 | M0 無風控基準 | -36.77% | 216 |

詳細結果請參閱: [COMPLETE_STRATEGIES_OVERVIEW.md](COMPLETE_STRATEGIES_OVERVIEW.md)

---

### 🔍 快速查找

### 想了解 M14 動態槓桿優化策略？ 🆕
👉 查看 [M14_DYNAMIC_LEVERAGE_STRATEGY.md](M14_DYNAMIC_LEVERAGE_STRATEGY.md) - 24-48小時翻倍的進階策略

### 想了解 M13 自適應策略？
👉 查看 [COMPLETE_STRATEGIES_OVERVIEW.md](COMPLETE_STRATEGIES_OVERVIEW.md) 中的 M13 章節

### 想了解某個策略？
👉 查看 [COMPLETE_STRATEGIES_OVERVIEW.md](COMPLETE_STRATEGIES_OVERVIEW.md) - 包含所有策略的詳細配置和說明

### 想了解開發過程？
👉 查看策略開發報告:
- [11_STRATEGIES_COMPLETION_REPORT.md](11_STRATEGIES_COMPLETION_REPORT.md) (最新)
- [9_STRATEGIES_COMPLETION_REPORT.md](9_STRATEGIES_COMPLETION_REPORT.md) (初版)

### 想了解 HFT 可行性？
👉 查看 [HFT_STRATEGY_ANALYSIS.md](HFT_STRATEGY_ANALYSIS.md) 和 [HFT_COMPLETE_TEST_REPORT.md](HFT_COMPLETE_TEST_REPORT.md)

### 想了解技術指標策略？
👉 查看:
- [MODE_8_TECHNICAL_INDICATORS.md](MODE_8_TECHNICAL_INDICATORS.md)
- [MODE_8_9_10_COMPARISON.md](MODE_8_9_10_COMPARISON.md)

### 想了解系統架構？
👉 查看 [STRATEGY_MANAGER_INTEGRATION_REPORT.md](STRATEGY_MANAGER_INTEGRATION_REPORT.md)

---

## 📝 相關配置文件

策略配置文件位於項目根目錄的 `config/` 資料夾:

```
config/
├── trading_strategies.json          # 生產環境配置
├── trading_strategies_dev.json      # 開發環境配置 (VPIN 0.85)
└── trading_strategies_production.json  # 生產備份
```

---

## 🔗 其他相關文檔

其他技術指標文檔位於 `docs/` 根目錄:
- `VPIN_ADVANTAGE.md` - VPIN 指標詳解
- `OBI_ADVANTAGE.md` - OBI 指標詳解
- `SPREAD_DEPTH_ADVANTAGE.md` - 流動性指標
- `SIGNED_VOLUME_ADVANTAGE.md` - 簽名成交量
- `MICROPRICE_ADVANTAGE.md` - 微觀價格

---

**維護者**: 開發團隊  
**最後更新**: 2025-11-12
