# 開發測試腳本資料夾 (dev-test)

本資料夾存放開發過程中用於測試、診斷和實驗的臨時腳本。

## 📁 腳本分類

### 🧪 單元測試腳本 (test_*.py)

| 腳本 | 用途 | 測試任務 |
|------|------|---------|
| `test_task_1_2.py` | 測試 Binance API 串接 | 1.2 |
| `test_task_1_4.py` | 測試資料庫 Schema | 1.4 |
| `test_task_1_5.py` | 測試 TA-Lib 指標庫 | 1.5 |
| `test_task_1_6.py` | 測試 OBI 計算模組 | 1.6 |
| `test_binance_connection.py` | 測試 Binance 連線 | 1.2 |
| `test_data_reception.py` | 測試 WebSocket 數據接收 | 1.6 |
| `test_integration.py` | 測試策略管理器整合 | 1.6.1 |
| `test_vpin.py` | 測試 VPIN 指標 | 1.6 |
| `test_microprice.py` | 測試微觀價格計算 | 1.6 |
| `test_spread_depth.py` | 測試價差深度指標 | 1.6 |
| `test_signed_volume.py` | 測試簽名成交量 | 1.6 |
| `test_multi_level_obi.py` | 測試多層級 OBI | 1.6 |
| `test_obi_exit_signals.py` | 測試 OBI 出場信號 | 1.6.1 |
| `test_exit_strategies.py` | 測試出場策略 | 1.6.1 |
| `test_layered_engine.py` | 測試分層引擎 | 1.9 |
| `test_market_replay.py` | 測試市場回放 | 1.10 |
| `test_mode_8_9_10.py` | 測試技術指標策略 | 1.6.1 |
| `test_technical_strategy.py` | 測試技術策略 | 1.6.1 |
| `test_technical_indicators_detailed.py` | 詳細測試技術指標 | 1.5 |
| `test_quick_backtest.py` | 快速回測測試 | 1.10 |

### 🔍 診斷工具 (diagnose_*.py)

| 腳本 | 用途 |
|------|------|
| `diagnose_simple.py` | 簡單診斷工具 |
| `diagnose_strategy.py` | 策略診斷工具 |
| `diagnose_no_trades.py` | 無交易問題診斷 |

### ⚡ 快速測試 (quick_*.py)

| 腳本 | 用途 |
|------|------|
| `quick_test_all.py` | 全面快速測試 |
| `quick_trading_test.py` | 快速交易測試 |
| `quick_latency_test.py` | 快速延遲測試 |

### 🚀 HFT 實驗 (hft_*.py)

| 腳本 | 用途 | 相關任務 |
|------|------|---------|
| `hft_fee_comparison.py` | 手續費對比分析 | 1.6.1 |
| `hft_leverage_test.py` | 槓桿測試 | 1.6.1 |
| `hft_strategy_comparison.py` | HFT 策略對比 | 1.6.1 |

### 📊 模擬交易 (simulation)

| 腳本 | 用途 |
|------|------|
| `real_trading_simulation.py` | 真實交易模擬 |
| `real_trading_simulation_adjusted.py` | 調整版模擬 |
| `real_trading_simulation_backup.py` | 備份版本 |
| `live_trading_simulation.py` | 即時交易模擬 |
| `live_obi_trading_demo.py` | OBI 交易演示 |
| `live_obi_trading_demo_multi_timeframe.py` | 多時間框架演示 |

### 🔧 簡化工具 (simple_*.py)

| 腳本 | 用途 |
|------|------|
| `simple_hft_comparison.py` | 簡化 HFT 對比 |
| `simple_live_trading.py` | 簡化即時交易 |

### 📝 範例程式 (example_*.py)

| 腳本 | 用途 |
|------|------|
| `example_binance_client.py` | Binance 客戶端範例 |

### 🎯 其他測試

| 腳本 | 用途 |
|------|------|
| `ultra_conservative_hft.py` | 超保守 HFT 測試 |
| `parallel_test_controller.py` | 並行測試控制器 |

## 🗂️ 使用說明

這些腳本主要用於：
1. **開發階段的功能測試**
2. **問題診斷和除錯**
3. **實驗性功能驗證**
4. **快速原型開發**

## ⚠️ 注意事項

- 這些腳本可能包含過時的代碼
- 部分腳本可能無法正常運行（依賴已改變）
- 不建議在生產環境使用
- 主要用於開發參考

## 🧹 清理建議

可以定期清理不再使用的測試腳本：

```bash
# 查看超過 30 天未修改的腳本
find scripts/dev-test -name "*.py" -mtime +30

# 刪除特定測試腳本
rm scripts/dev-test/test_old_feature.py
```

## 📚 主要生產腳本

生產環境使用的主要腳本在 `scripts/` 根目錄：

| 腳本 | 用途 | 任務 |
|------|------|------|
| `paper_trading_system.py` | 紙面交易系統 | 1.6.1 |
| `analyze_paper_trading.py` | 分析交易結果 | 1.6.1 |
| `download_historical_data.py` | 下載歷史數據 | 1.3 |
| `latency_monitor.py` | 延遲監控 | 1.6.1 |
| `init_influxdb.py` | 初始化 InfluxDB | 1.1 |
| `init_redis.py` | 初始化 Redis | 1.1 |

---

**維護者**: 開發團隊  
**最後更新**: 2025-11-12
