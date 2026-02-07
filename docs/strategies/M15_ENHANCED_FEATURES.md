# M15 增強版功能說明文檔 🤖🐳🦾

## 📋 新增功能總覽

M15 策略在原有多維度智能切換基礎上，新增 **4 大安全防護機制**：

| 功能 | 圖標 | 描述 | 優先級 |
|------|------|------|--------|
| 緊急熔斷機制 | 🔴 | 連續虧損/單日虧損自動暫停交易 | ⚠️ CRITICAL |
| 平滑過渡管理 | 🔄 | A↔C 劇烈切換需經30分鐘B方案過渡 | ⚡ HIGH |
| 極端市場處理 | ⚠️ | VPIN/Spread異常強制降級或暫停 | ⚠️ CRITICAL |
| 增強性能監控 | 📊 | 實時預警回撤/勝率/連續虧損 | 📈 MEDIUM |

---

## 🔴 1. 緊急熔斷機制 (EmergencyCircuitBreaker)

### 功能描述
自動檢測危險交易模式，觸發熔斷後**完全停止交易**，需手動重置才能恢復。

### 觸發條件

#### 1️⃣ 連續虧損熔斷
```python
max_consecutive_losses = 3  # 連續3次虧損
```
- **檢測**: 每次交易結束後檢查
- **動作**: 立即暫停所有交易
- **恢復**: 需要管理員手動重置
- **日誌**: `🔴 觸發連續虧損熔斷: 3次`

#### 2️⃣ 單日虧損熔斷
```python
daily_loss_limit = -0.15  # 單日虧損15%
```
- **檢測**: 每次交易結束後計算累計虧損率
- **計算**: `(當前餘額 - 初始餘額) / 初始餘額`
- **動作**: 立即暫停所有交易
- **恢復**: 需要管理員手動重置
- **日誌**: `🔴 觸發單日虧損熔斷: -16.0%`

### 使用方法

```python
# 策略初始化時自動創建
strategy = Mode15EnhancedStrategy(config)

# 初始化會話（必須！）
strategy.initialize_session(initial_balance=100.0)

# 每次交易後記錄結果
strategy.record_trade_result(
    profit=-2.5,
    entry_time=datetime.now(),
    current_balance=97.5  # 重要！用於單日虧損檢查
)

# 檢查是否可以交易
can_trade, halt_reason = strategy.circuit_breaker.can_trade()
if not can_trade:
    print(f"交易暫停: {halt_reason}")

# 手動重置（需要管理員權限）
strategy.manual_reset_circuit_breaker()
```

### 測試結果
```
📊 測試連續虧損觸發:
   第 1 次虧損: 餘額=98.0, 熔斷=False
   第 2 次虧損: 餘額=96.0, 熔斷=False
   第 3 次虧損: 餘額=94.0, 熔斷=True ✅
   🔴 熔斷觸發！原因: 連續虧損3次

📊 測試單日虧損觸發:
   虧損金額: -16 USDT (-16%)
   熔斷觸發: True ✅
   🔴 原因: 單日虧損-16.0%
```

---

## 🔄 2. 平滑過渡管理 (SmoothTransitionManager)

### 功能描述
避免 **A↔C** 方案間劇烈切換，強制經過30分鐘的B方案過渡期。

### 過渡規則

| 切換路徑 | 是否需要過渡 | 過渡方案 | 過渡時間 |
|---------|------------|---------|---------|
| A → B | ❌ 否 | - | 立即 |
| A → C | ✅ 是 | B | 30分鐘 |
| B → A | ❌ 否 | - | 立即 |
| B → C | ❌ 否 | - | 立即 |
| C → A | ✅ 是 | B | 30分鐘 |
| C → B | ❌ 否 | - | 立即 |

### 過渡時間線

```
A → C 切換流程:

時刻 0:   當前方案=A, 檢測到需要升級到C
         ↓
時刻 0:   啟動過渡: A → B → C
         🔄 啟動平滑過渡: A → B → C (預計30分鐘)
         當前方案=B (過渡方案)
         ↓
時刻 15:  過渡中...
         🔄 過渡進度: 50% (剩餘 15.0 分鐘)
         當前方案=B
         ↓
時刻 30:  完成過渡
         ✅ 完成平滑過渡: B → C
         當前方案=C (目標方案)
```

### 使用方法

```python
# 在 update_scheme_dynamic 中自動啟用
strategy.update_scheme_dynamic(market_data, current_balance, initial_balance)

# 檢查過渡狀態
if strategy.transition_manager.is_in_transition():
    status = strategy.transition_manager.get_transition_status()
    print(f"過渡進度: {status['progress']:.0%}")
    print(f"目標方案: {status['target_scheme']}")
    print(f"剩餘時間: {status['remaining_minutes']:.1f} 分鐘")
```

### 測試結果
```
📊 測試 A→C 劇烈切換:
   目標: A → C
   實際方案: B ✅ (過渡方案)
   過渡中: True ✅
   過渡目標: C
   預計時間: 30.0 分鐘

📊 測試 B→C 直接切換:
   目標: B → C
   實際方案: C ✅ (直接切換)
   過渡中: False
```

### 優勢
- ✅ **避免劇烈波動**: A↔C 直接切換會導致槓桿/倉位/止盈止損劇烈變化
- ✅ **風險緩衝期**: 30分鐘B方案過渡給市場更多觀察時間
- ✅ **防止頻繁切換**: 過渡期內不會再次切換

---

## ⚠️ 3. 極端市場處理 (ExtremeMarketHandler)

### 功能描述
檢測極端市場條件，**立即**強制降級或暫停交易，優先級**高於**多維度評估。

### 極端條件定義

```python
extreme_thresholds = {
    'vpin_critical': 0.8,      # VPIN危機
    'vpin_high': 0.7,          # VPIN偏高
    'spread_critical': 25,     # 流動性危機 (bps)
    'spread_high': 20,         # 流動性不足 (bps)
    'volatility_critical': 0.05,  # 波動率爆炸 (5%)
    'volatility_high': 0.04    # 波動率偏高 (4%)
}
```

### 處理邏輯

| 條件 | 動作 | 適用方案 | 優先級 |
|------|------|---------|--------|
| VPIN > 0.8 | 強制降級到 A | 所有 | ⚠️ CRITICAL |
| Spread > 25bps | 暫停交易 (PAUSE) | 所有 | ⚠️ CRITICAL |
| Volatility > 5% | 強制降級到 A | 所有 | ⚠️ CRITICAL |
| VPIN > 0.7 & 當前=C | 強制降級到 B | C | 🔴 HIGH |
| Spread > 20bps & 當前=C | 強制降級到 B | C | 🔴 HIGH |

### 風險等級

```python
def get_market_risk_level(market_data) -> str:
    """返回: LOW | MEDIUM | HIGH | CRITICAL"""
    
    # CRITICAL: 任一指標超過 critical 閾值
    # HIGH:     任一指標超過 high 閾值
    # MEDIUM:   VPIN>0.5 或 Spread>15 或 Volatility>3%
    # LOW:      正常市場
```

### 使用方法

```python
# 在 check_entry 和 update_scheme_dynamic 中自動檢查

# 手動檢查極端市場
action, reason = strategy.extreme_handler.handle_extreme_conditions(
    market_data, current_scheme="C"
)

if action == "PAUSE":
    print(f"暫停交易: {reason}")
elif action == "A":
    print(f"強制降級到A: {reason}")
elif action == "B":
    print(f"強制降級到B: {reason}")

# 獲取風險等級
risk_level = strategy.extreme_handler.get_market_risk_level(market_data)
print(f"市場風險: {risk_level}")
```

### 測試結果
```
📊 測試正常市場:
   VPIN: 0.4, Spread: 8bps
   風險等級: LOW ✅
   強制動作: 無

📊 測試VPIN危機:
   VPIN: 0.85, Spread: 10bps
   風險等級: CRITICAL ⚠️
   強制動作: A ✅
   原因: 極端VPIN: 0.85

📊 測試流動性危機:
   VPIN: 0.5, Spread: 30bps
   風險等級: CRITICAL ⚠️
   強制動作: PAUSE ✅
   原因: 流動性危機: Spread 30.0bps
```

---

## 📊 4. 增強性能監控 (EnhancedPerformanceMonitor)

### 功能描述
實時監控交易性能，自動觸發 **WARNING** 和 **CRITICAL** 級別預警。

### 預警閾值

```python
alert_triggers = {
    # 回撤警告
    'drawdown_alert': -0.08,       # 回撤8%  → WARNING
    'drawdown_critical': -0.12,    # 回撤12% → CRITICAL
    
    # 連續虧損
    'consecutive_loss_alert': 2,   # 連續2次 → WARNING
    'consecutive_loss_critical': 3, # 連續3次 → CRITICAL (觸發熔斷)
    
    # 勝率
    'win_rate_alert': 0.3,         # 勝率<30% → WARNING
    'win_rate_critical': 0.2,      # 勝率<20% → CRITICAL
    
    # VPIN
    'vpin_alert': 0.7,             # VPIN>0.7 → WARNING
    'vpin_critical': 0.8           # VPIN>0.8 → CRITICAL (觸發極端市場)
}
```

### 預警類型

| 類型 | 級別 | 計算方式 | 自動動作 |
|------|------|---------|---------|
| 回撤 | WARNING | 峰值回撤 8-12% | 記錄日誌 |
| 回撤 | CRITICAL | 峰值回撤 >12% | 強制降級 C→B |
| 連續虧損 | WARNING | 連續2次 | 記錄日誌 |
| 連續虧損 | CRITICAL | 連續3次 | 觸發熔斷 + 降級 |
| 勝率 | WARNING | 近10筆 <30% | 記錄日誌 |
| 勝率 | CRITICAL | 近10筆 <20% | 強制降級 C→B |
| VPIN | WARNING | VPIN > 0.7 | 記錄日誌 |
| VPIN | CRITICAL | VPIN > 0.8 | 極端市場處理 |

### 使用方法

```python
# 自動記錄交易
strategy.record_trade_result(profit, entry_time, current_balance)

# 檢查預警
alerts = strategy.performance_monitor.check_performance_alerts(market_data)

for alert in alerts:
    if alert['level'] == 'CRITICAL':
        print(f"🚨 嚴重預警: {alert['message']}")
    else:
        print(f"⚠️ 預警: {alert['message']}")

# 獲取性能摘要
summary = strategy.performance_monitor.get_performance_summary()
print(f"勝率: {summary['win_rate']:.1%}")
print(f"回撤: {summary['drawdown']:.1%}")
print(f"連續虧損: {summary['consecutive_losses']}")
```

### 測試結果
```
📊 模擬交易記錄:
   交易 1: 盈利 1.5 USDT | 方案: C
   交易 2: 盈利 2.0 USDT | 方案: C
   交易 3: 虧損 1.0 USDT | 方案: B
   交易 4: 虧損 1.5 USDT | 方案: B
   交易 5: 虧損 2.0 USDT | 方案: A

📊 性能檢查:
   總交易: 5
   勝率: 40.0%
   回撤: -128.6%
   連續虧損: 3

⚠️ 發現 3 個預警:
   🚨 [CRITICAL] 嚴重回撤: -128.6% ✅
   🚨 [CRITICAL] 嚴重連續虧損: 3次 ✅
   ⚠️ [WARNING] VPIN過高: 0.75 ✅
```

---

## 🔗 功能集成流程

### 1. 進場檢查流程 (check_entry)

```
檢查進場條件
    ↓
1️⃣ 檢查熔斷器
    ├─ 是否觸發熔斷？
    │   ├─ 是 → 🔴 阻擋交易
    │   └─ 否 → 繼續
    ↓
2️⃣ 檢查極端市場
    ├─ PAUSE → 🔴 阻擋交易
    ├─ A/B → ⚠️ 強制降級
    └─ None → 繼續
    ↓
3️⃣ 檢查性能預警
    ├─ CRITICAL → ⚠️ 強制降級 C→B
    ├─ WARNING → 📝 記錄日誌
    └─ 繼續
    ↓
4️⃣ 調用父類 should_enter_trade
    └─ 返回最終結果
```

### 2. 方案切換流程 (update_scheme_dynamic)

```
動態更新方案
    ↓
1️⃣ 檢查極端市場（最高優先級）
    ├─ 有強制動作？
    │   ├─ 是 → ⚠️ 立即切換
    │   └─ 否 → 繼續
    ↓
2️⃣ 多維度評估方案
    └─ 獲取推薦方案
    ↓
3️⃣ 平滑過渡管理
    ├─ A↔C？
    │   ├─ 是 → 🔄 啟動30分鐘過渡
    │   └─ 否 → 直接切換
    ↓
4️⃣ 執行方案切換
    └─ 返回當前方案
```

### 3. 交易結果記錄流程 (record_trade_result)

```
記錄交易結果
    ↓
1️⃣ 記錄到方案管理器
    ↓
2️⃣ 記錄到父類
    ↓
3️⃣ 記錄到性能監控器
    ↓
4️⃣ 檢查熔斷條件
    ├─ 觸發熔斷？
    │   ├─ 是 → 🔴 記錄嚴重日誌
    │   └─ 否 → 繼續
    ↓
5️⃣ 記錄交易統計
    └─ 完成
```

---

## 📈 使用示例

### 完整初始化

```python
from src.strategy.mode_15_enhanced import Mode15EnhancedStrategy

# 1. 載入配置
config = {
    'name': 'M15 Enhanced',
    'mode': 'mode_15_enhanced',
    'base_leverage': 20,
    'max_position_size': 0.5,
    # ... 其他配置
}

# 2. 創建策略實例
strategy = Mode15EnhancedStrategy(config)

# 3. 初始化會話（重要！）
initial_balance = 100.0
strategy.initialize_session(initial_balance)

# 日誌輸出:
# 🔄 M15 交易會話已初始化
#    💰 初始餘額: 100.00 USDT
#    🔴 熔斷設定: 連續虧損3次 或 單日虧損-15%
```

### 交易循環

```python
while trading:
    # 獲取市場數據
    market_data = get_market_data()
    
    # 檢查進場條件
    can_enter, reasons = strategy.check_entry(market_data, signal)
    
    if not can_enter:
        print(f"無法進場: {', '.join(reasons)}")
        continue
    
    # 進場交易
    trade_result = execute_trade(...)
    
    # 記錄結果（重要！需要傳入 current_balance）
    strategy.record_trade_result(
        profit=trade_result['profit'],
        entry_time=trade_result['entry_time'],
        current_balance=current_balance  # 用於熔斷檢查
    )
    
    # 動態更新方案
    current_scheme = strategy.update_scheme_dynamic(
        market_data=market_data,
        current_balance=current_balance,
        initial_balance=initial_balance
    )
    
    # 獲取風險摘要
    risk_summary = strategy.get_risk_summary(market_data)
    print(f"市場風險: {risk_summary['market_risk_level']}")
    print(f"當前方案: {risk_summary['current_scheme']}")
```

### 監控與重置

```python
# 獲取完整統計
stats = strategy.get_comprehensive_statistics()
print(json.dumps(stats, indent=2))

# 手動重置熔斷（管理員操作）
if input("確定要重置熔斷機制？(y/n): ") == 'y':
    strategy.manual_reset_circuit_breaker()
    print("✅ 熔斷機制已重置")
```

---

## ⚙️ 配置參數

### 熔斷機制配置

```python
# 在 EmergencyCircuitBreaker.__init__ 中修改
self.max_consecutive_losses = 3      # 連續虧損次數
self.daily_loss_limit = -0.15        # 單日虧損限制 (15%)
```

### 平滑過渡配置

```python
# 在 SmoothTransitionManager.__init__ 中修改
self.transition_duration = 30        # 過渡時間 (分鐘)
```

### 極端市場配置

```json
{
  "extreme_thresholds": {
    "vpin_critical": 0.8,
    "vpin_high": 0.7,
    "spread_critical": 25,
    "spread_high": 20,
    "volatility_critical": 0.05,
    "volatility_high": 0.04
  }
}
```

### 性能監控配置

```python
# 在 EnhancedPerformanceMonitor.__init__ 中修改
self.alert_triggers = {
    'drawdown_alert': -0.08,
    'drawdown_critical': -0.12,
    'consecutive_loss_alert': 2,
    'consecutive_loss_critical': 3,
    'win_rate_alert': 0.3,
    'win_rate_critical': 0.2,
    'vpin_alert': 0.7,
    'vpin_critical': 0.8
}
```

---

## 🎯 最佳實踐

### ✅ DO

1. **務必初始化會話**
   ```python
   strategy.initialize_session(initial_balance)
   ```

2. **記錄交易時傳入餘額**
   ```python
   strategy.record_trade_result(profit, time, current_balance=balance)
   ```

3. **監控預警日誌**
   ```python
   # 設置日誌級別為 INFO
   logging.basicConfig(level=logging.INFO)
   ```

4. **定期檢查風險摘要**
   ```python
   risk = strategy.get_risk_summary(market_data)
   ```

### ❌ DON'T

1. **不要忽略熔斷信號**
   ```python
   # 錯誤示範
   can_trade, _ = strategy.circuit_breaker.can_trade()
   # 繼續交易... ❌
   ```

2. **不要頻繁手動重置熔斷**
   ```python
   # 應該分析原因，而不是直接重置
   strategy.manual_reset_circuit_breaker()  # ⚠️ 謹慎使用
   ```

3. **不要在過渡期強制切換**
   ```python
   # 應該讓平滑過渡自然完成
   if strategy.transition_manager.is_in_transition():
       # 等待... ⏳
   ```

---

## 📊 性能影響

### 計算開銷

| 功能 | 時間複雜度 | 空間複雜度 | 性能影響 |
|------|----------|----------|---------|
| 熔斷檢查 | O(1) | O(1) | 極小 |
| 平滑過渡 | O(1) | O(1) | 極小 |
| 極端市場 | O(1) | O(1) | 極小 |
| 性能監控 | O(n) n≤100 | O(100) | 很小 |

### 記憶體使用

```python
EmergencyCircuitBreaker:       ~1 KB
SmoothTransitionManager:       ~1 KB
ExtremeMarketHandler:          ~2 KB
EnhancedPerformanceMonitor:    ~50 KB (100筆交易記錄)
────────────────────────────────────
總計:                         ~54 KB
```

---

## 🧪 測試覆蓋

### 測試腳本

```bash
python scripts/test_m15_enhanced.py
```

### 測試內容

✅ **測試 1: 緊急熔斷機制**
- 連續虧損觸發
- 單日虧損觸發
- 手動重置

✅ **測試 2: 平滑過渡管理**
- A→C 劇烈切換 (需要過渡)
- B→C 直接切換 (無需過渡)

✅ **測試 3: 極端市場處理**
- 正常市場 (無動作)
- VPIN危機 (強制A)
- 流動性危機 (暫停)

✅ **測試 4: 增強性能監控**
- 交易記錄
- 預警檢查
- 性能摘要

✅ **測試 5: M15 完整集成**
- 策略初始化
- 進場檢查
- 風險摘要

---

## 📝 更新日誌

### v2.0.0 (2025-11-13)

**新增功能:**
- 🔴 緊急熔斷機制
- 🔄 平滑過渡管理
- ⚠️ 極端市場處理
- 📊 增強性能監控

**改進:**
- `check_entry`: 新增4層檢查
- `update_scheme_dynamic`: 新增極端市場優先級
- `record_trade_result`: 新增熔斷檢查和性能監控
- 新增 `initialize_session` 方法
- 新增 `get_comprehensive_statistics` 方法
- 新增 `get_risk_summary` 方法
- 新增 `manual_reset_circuit_breaker` 方法

**測試:**
- 新增 `test_m15_enhanced.py` 測試腳本
- 5項功能測試全部通過

---

## 🤝 支援與反饋

如有問題或建議，請查看:
- 測試腳本: `scripts/test_m15_enhanced.py`
- 源碼: `src/strategy/mode_15_enhanced.py`
- 主文檔: `docs/strategies/M15_ENHANCED_FEATURES.md`

---

**M15 增強版 - 更安全、更穩定、更智能** 🤖🐳🦾
