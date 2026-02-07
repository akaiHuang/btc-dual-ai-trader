# 交易程式參考指南

> **目的**: 避免重複開發和 bug，明確標注已驗證的交易程式及其正確用法

---

## 📋 目錄

1. [核心交易引擎](#核心交易引擎)
2. [已驗證的交易程式](#已驗證的交易程式)
3. [正確的依賴關係](#正確的依賴關係)
4. [常見錯誤避免](#常見錯誤避免)
5. [開發新交易程式時的檢查清單](#開發新交易程式時的檢查清單)

---

## 🎯 核心交易引擎

### LayeredTradingEngine (Phase C 決策系統)

**位置**: `src/strategy/layered_trading_engine.py`

**用途**: 完整的三層決策系統（信號生成 → 風險過濾 → 執行）

**組成部分**:
- `SignalGenerator` (`src/strategy/signal_generator.py`) - 信號生成
- `RegimeFilter` (`src/strategy/regime_filter.py`) - 風險評估
- `ExecutionEngine` (`src/strategy/execution_engine.py`) - 執行決策

**正確用法**:
```python
from src.strategy.layered_trading_engine import LayeredTradingEngine

# 初始化
engine = LayeredTradingEngine(
    symbol="BTCUSDT",
    enable_logging=True
)

# 使用
decision = engine.make_decision(market_data, verbose=True)
# decision: {'signal': 'LONG/SHORT/NEUTRAL', 'confidence': 0.0-1.0, ...}
```

**關鍵配置參數**:
```python
# SignalGenerator
long_threshold=0.6      # 做多信號閾值
short_threshold=0.6     # 做空信號閾值

# RegimeFilter
vpin_threshold=0.5      # VPIN 風險閾值（原始版本）
vpin_threshold=0.7      # VPIN 風險閾值（調整版本）

# ExecutionEngine
moderate_confidence=0.6   # 中等倉位閾值
aggressive_confidence=0.8 # 激進倉位閾值
```

---

## ✅ 已驗證的交易程式

### 1. quick_trading_test.py ⭐️ 推薦用於模擬數據測試

**檔案**: `scripts/quick_trading_test.py`

**狀態**: ✅ **已驗證無 bug**

**用途**: 使用**模擬市場數據**快速測試交易策略

**特點**:
- ✅ 使用 `LayeredTradingEngine` (完整 Phase C 系統)
- ✅ 模擬市場價格 + 微觀結構指標
- ✅ 完整持倉管理（開倉、止損、止盈）
- ✅ 詳細統計報告
- ✅ 可調整測試時間長度

**執行**:
```bash
python scripts/quick_trading_test.py [測試輪數] [每輪決策數]

# 範例
python scripts/quick_trading_test.py 100 50  # 100輪，每輪50次決策
```

**輸出**:
- 總決策數
- 信號生成數
- 交易執行數
- 勝率、總盈虧
- Phase C 各層表現統計

**何時使用**:
- ✅ 測試策略邏輯
- ✅ 驗證參數調整
- ✅ 快速原型開發
- ❌ 不適合真實市場驗證（數據是模擬的）

---

### 2. live_trading_simulation.py ⭐️ 推薦用於真實 API 模擬

**檔案**: `scripts/live_trading_simulation.py`

**狀態**: ⚠️ **使用模擬指標**（不推薦）

**問題**: 使用 `MarketDataSimulator` 生成模擬指標，而非真實計算

**建議**: 使用 `real_trading_simulation.py` 替代

---

### 3. real_trading_simulation.py ⭐️⭐️ **強烈推薦**

**檔案**: `scripts/real_trading_simulation.py`

**狀態**: ✅ **已驗證無 bug + 詳細輸出**

**更新日期**: 2025-01-11

**用途**: 使用**真實 Binance WebSocket 數據**進行模擬交易，包含完整的費率計算和詳細決策記錄

**特點**:
- ✅ 使用 `LayeredTradingEngine` (完整 Phase C 系統)
- ✅ 真實 Binance WebSocket (depth + aggTrade)
- ✅ 真實市場微觀結構指標計算
- ✅ **詳細的啟動資訊**（本金、槓桿、費率）
- ✅ **每次決策都有完整細項**（價格、信號、指標、持倉狀態）
- ✅ **每筆交易都有詳細明細**（費用、盈虧、ROI）
- ✅ 真實費率計算（Taker 0.05% + Funding 0.003%/hr）
- ✅ 支援 CLI 參數（時長、輸出文件）

**執行**:
```bash
python scripts/real_trading_simulation.py [時長分鐘] [輸出檔案]

# 範例
python scripts/real_trading_simulation.py 2              # 2分鐘快速測試
python scripts/real_trading_simulation.py 60 results.json  # 1小時測試
python scripts/real_trading_simulation.py 1440             # 24小時測試
```

**輸出格式**:

1. **指標說明** (程式啟動時顯示):
```
📖 市場指標說明
────────────────────────────────────────
📊 OBI (Order Book Imbalance)     訂單簿失衡度 [-1, 1]
   • 正值 = 買盤強勢 | 負值 = 賣盤強勢 | 0 = 平衡
   • 越接近 ±1 代表失衡越嚴重

⚡ OBI Velocity                    OBI 變化率 (速度)
   • 正值 = 買盤增強 | 負值 = 賣盤增強
   • 絕對值越大代表變化越快

📈 Signed Volume                   淨成交量 (買-賣)
   • 正值 = 主動買單多 | 負值 = 主動賣單多

☠️  VPIN (Volume-Synchronized PIN)  毒性指標 [0, 1]
   • 0 = 低風險 | 1 = 高風險
   • >0.5 表示知情交易者活躍，需謹慎

💹 Spread                          買賣價差 (bps)
   • 越小 = 流動性越好 | 越大 = 流動性差

🏊 Depth                           訂單簿深度 (BTC)
   • 前5檔買賣單總量，反映市場承接力

🎨 圖示說明
────────────────────────────────────────
交易方向:  📈 LONG (做多)  |  📉 SHORT (做空)  |  ⚖️  NEUTRAL (中立)
風險等級:  🟢 SAFE (安全)  |  🟡 WARNING (警告)  |  🟠 DANGER (危險)  |  🔴 CRITICAL (嚴重)
持倉狀態:  🏦 空倉  |  📊 持倉中
平倉原因:  🎯 TAKE_PROFIT (止盈)  |  🛑 STOP_LOSS (止損)  |  🔄 REVERSE_SIGNAL (反向)
```

2. **啟動資訊**:
```
⏱️  測試配置:
   運行時長: 60 分鐘
   交易對: BTCUSDT
   決策頻率: 每 15 秒

💰 資金配置:
   初始本金: 100 USDT
   最大槓桿: 10x
   倉位策略: 保守 30% | 中等 50% | 激進 80%

💸 費率設定:
   Maker 手續費: 0.02%
   Taker 手續費: 0.05%
   資金費率: 0.003%/小時

🎯 風控設定:
   保守模式: 槓桿 3x | 止損 8% | 止盈 12%
   中等模式: 槓桿 5x | 止損 5% | 止盈 8%
   激進模式: 槓桿 10x | 止損 3% | 止盈 5%
```

2. **每次決策細項**:
```
=====================================================================
[11:04:30] 決策 #1
=====================================================================
💰 當前價格: $106715.99
📊 持倉狀態: 空倉

🎯 交易信號:
   方向: 📈 LONG
   信心度: 0.650
   風險等級: 🟢 SAFE

📈 市場指標:
   OBI (訂單簿失衡): 0.3250
   OBI Velocity (變化率): 0.1234
   Signed Volume (淨量): 0.45
   VPIN (毒性): 0.300
   Spread (價差): 0.00 bps
   Depth (深度): 6.02 BTC
```

3. **開倉明細**:
```
=====================================================================
🚀 開倉 #1 [MODERATE]
=====================================================================
📍 基本資訊:
   方向: LONG
   進場價格: $106715.99
   進場時間: 2025-01-11 11:04:30

💰 資金配置:
   本金: 100.00 USDT
   倉位比例: 50.0%
   使用資金: 50.00 USDT
   槓桿倍數: 5.0x
   控制資產: 250.00 USDT
   BTC 數量: 0.002342 BTC

💸 費用明細:
   進場手續費: 0.1250 USDT (0.05%)
   資金費率: 0.003%/小時
   預估費用: ~0.0075 USDT/小時

🎯 風控設定:
   止損: -5.00%
   止盈: +8.00%
   信心度: 0.650

📊 預期收益:
   止盈收益: +4.00 USDT
   止損虧損: -2.50 USDT
   風險收益比: 1:1.60
=====================================================================
```

4. **平倉明細**:
```
=====================================================================
🔔 平倉 #1 [TAKE_PROFIT]
=====================================================================
📍 基本資訊:
   方向: LONG
   進場價格: $106715.99
   出場價格: $107569.44
   價格變動: +0.7995%
   持倉時間: 15.3 分鐘 (0.26 小時)

💰 倉位明細:
   本金: 100.00 USDT
   使用資金: 50.00 USDT (50.0%)
   槓桿倍數: 5.0x
   控制資產: 250.00 USDT
   BTC 數量: 0.002342 BTC

💸 費用明細:
   進場手續費: 0.1250 USDT
   出場手續費: 0.1262 USDT
   資金費率: 0.0019 USDT (0.26h)
   總費用: 0.2531 USDT (0.506%)

📊 盈虧結算:
   價格盈虧: +1.9987 USDT
   扣除費用: -0.2531 USDT
   淨盈虧: +1.7456 USDT
   投資報酬率: +3.49%

💵 資金變化:
   平倉前: 100.00 USDT
   平倉後: 101.75 USDT
   變動: +1.7456 USDT (+3.49%)
=====================================================================
```

**依賴的核心模組**:
```python
from src.strategy.layered_trading_engine import LayeredTradingEngine
from src.exchange.multi_level_orderbook import MultiLevelOrderbook
from src.exchange.signed_volume_tracker import SignedVolumeTracker
from src.exchange.vpin_calculator import VPINCalculator
from src.exchange.microprice_calculator import MicropriceCalculator
```

**何時使用**:
- ✅ 真實市場驗證
- ✅ 24小時長期測試
- ✅ 參數調整對比測試
- ✅ 生成報告用數據

---

### 3. real_trading_simulation.py ⭐️ 長期測試版本

**檔案**: `scripts/real_trading_simulation.py`

**狀態**: ✅ **已驗證無 bug**

**用途**: 與 `live_trading_simulation.py` 類似，但有額外功能

**額外特點**:
- ✅ JSON 結果輸出
- ✅ 更詳細的統計
- ✅ 支援外部啟動腳本調用

**執行**:
```bash
python scripts/real_trading_simulation.py [時長分鐘] [輸出檔案]
```

---

### 4. real_trading_simulation_adjusted.py ⭐️ 調整參數版本

**檔案**: `scripts/real_trading_simulation_adjusted.py`

**狀態**: ✅ **已驗證可運行**（需要長期測試驗證效果）

**用途**: 使用**調整後的參數**進行真實市場模擬

**調整的參數**:
```python
# 相比原始版本的變化：
SignalGenerator(
    long_threshold=0.5,   # ↓ 從 0.6 降低
    short_threshold=0.5   # ↓ 從 0.6 降低
)

RegimeFilter(
    vpin_threshold=0.7    # ↑ 從 0.5 提高（放鬆風險限制）
)

ExecutionEngine(
    moderate_confidence=0.5,   # ↓ 從 0.6 降低
    aggressive_confidence=0.7  # ↓ 從 0.8 降低
)

# 風險過濾邏輯
is_safe = risk_level != "CRITICAL"  # 只阻擋 CRITICAL（原本阻擋 DANGER + CRITICAL）
```

**何時使用**:
- ✅ 與原始版本對比測試
- ✅ 驗證參數調整效果
- ✅ 解決 VPIN 過高導致交易阻擋問題

---

## 🔧 正確的依賴關係

### 核心策略模組

```
LayeredTradingEngine (主引擎)
├── SignalGenerator (信號生成)
│   ├── MultiLevelOrderbook (多層訂單簿)
│   ├── MicropriceCalculator (微價格)
│   └── SignedVolumeTracker (成交量)
│
├── RegimeFilter (風險過濾)
│   ├── VPINCalculator (毒性檢測)
│   └── SpreadDepthAnalyzer (價差深度)
│
└── ExecutionEngine (執行引擎)
    └── RiskManager (風險管理)
```

### 交易程式依賴模式

#### ✅ 正確方式 (使用 LayeredTradingEngine)

```python
from src.strategy.layered_trading_engine import LayeredTradingEngine

engine = LayeredTradingEngine(symbol="BTCUSDT")
decision = engine.make_decision(market_data)
```

#### ❌ 錯誤方式 (手動組裝)

```python
# 不要這樣做！容易出現依賴問題
from src.strategy.signal_generator import SignalGenerator
from src.strategy.regime_filter import RegimeFilter
# ... 手動組裝各個組件
```

### 指標計算器的正確用法

#### SignedVolumeTracker

```python
from src.exchange.signed_volume_tracker import SignedVolumeTracker

tracker = SignedVolumeTracker(window_size=20)
tracker.add_trade(trade_data)
signed_volume = tracker.calculate_signed_volume()  # ✅ 正確方法名

# ❌ 錯誤: tracker.get_signed_volume()  # 這個方法不存在！
```

#### VPINCalculator

```python
from src.exchange.vpin_calculator import VPINCalculator

calculator = VPINCalculator(bucket_size=50, num_buckets=50)
calculator.process_trade(trade_data)
vpin = calculator.calculate_vpin()  # ✅ 返回 0.0-1.0

# 注意：需要至少 50 筆交易才會返回有效值
```

#### MultiLevelOrderbook

```python
from src.exchange.multi_level_orderbook import MultiLevelOrderbook

orderbook = MultiLevelOrderbook(symbol="BTCUSDT")
orderbook.update_snapshot(depth_data)  # 完整快照
orderbook.update_diff(diff_data)       # 差異更新

obi = orderbook.calculate_obi()       # ✅ 訂單簿失衡
```

---

## ⚠️ 常見錯誤避免

### 1. 方法名稱錯誤

#### ❌ 常見錯誤
```python
signed_volume = tracker.get_signed_volume()  # AttributeError!
```

#### ✅ 正確寫法
```python
signed_volume = tracker.calculate_signed_volume()
```

---

### 2. WebSocket 訊息格式

#### Binance Depth Socket 格式

```python
# ❌ 錯誤：期待完整訂單簿
if 'bids' in msg and 'asks' in msg:
    # 這只有 snapshot 才有！

# ✅ 正確：差異更新格式
if 'b' in msg and 'a' in msg:  # b=bids, a=asks
    orderbook.update_diff(msg)
```

#### Binance Trade Socket 格式

```python
# ✅ 正確
trade_data = {
    'p': float(msg['p']),  # 價格
    'q': float(msg['q']),  # 數量
    'm': msg['m']          # is_buyer_maker
}
```

---

### 3. 指標需要預熱

#### ❌ 錯誤：立即使用
```python
vpin_calc = VPINCalculator(bucket_size=50, num_buckets=50)
vpin = vpin_calc.calculate_vpin()  # 返回 0.0（無效）
```

#### ✅ 正確：等待預熱
```python
vpin_calc = VPINCalculator(bucket_size=50, num_buckets=50)

# 處理至少 50 筆交易
for trade in trades:
    vpin_calc.process_trade(trade)

# 現在可以安全使用
if len(trades) >= 50:
    vpin = vpin_calc.calculate_vpin()  # 有效值
```

---

### 4. asyncio.run() 巢狀呼叫

#### ❌ 錯誤：巢狀 event loop
```python
# 在 async 函數內
async def test():
    asyncio.run(another_async_func())  # RuntimeError!
```

#### ✅ 正確：使用 await
```python
async def test():
    await another_async_func()
```

---

### 5. WebSocket 佇列溢位

#### ❌ 問題：處理太慢
```python
async with socket as s:
    msg = await asyncio.wait_for(s.recv(), timeout=1.0)
    # 處理很慢的邏輯...
    time.sleep(5)  # 佇列累積！
```

#### ✅ 解決：快速消費
```python
async with socket as s:
    msg = await s.recv()  # 不要 timeout
    # 快速提取資料
    await process_queue.put(msg)  # 丟到背景處理
```

---

## 📝 開發新交易程式時的檢查清單

### 開始前

- [ ] 確認需求：是否已有現成程式可用？
  - [ ] 模擬數據測試 → `quick_trading_test.py`
  - [ ] 真實 API 模擬 → `live_trading_simulation.py`
  - [ ] 調整參數測試 → `real_trading_simulation_adjusted.py`

- [ ] 如果需要新程式，參考已驗證的程式作為模板

### 基本結構

- [ ] 使用 `LayeredTradingEngine` 而不是手動組裝
- [ ] 實現 `Position` 類進行持倉追蹤
- [ ] 支援 CLI 參數（時長、輸出檔案）
- [ ] 實現統計輸出（決策數、交易數、勝率、盈虧）

### 指標使用

- [ ] 正確匯入指標計算器
  ```python
  from src.exchange.signed_volume_tracker import SignedVolumeTracker
  from src.exchange.vpin_calculator import VPINCalculator
  from src.exchange.multi_level_orderbook import MultiLevelOrderbook
  ```

- [ ] 使用正確的方法名稱
  - [ ] `calculate_signed_volume()` 不是 `get_signed_volume()`
  - [ ] `calculate_vpin()` 
  - [ ] `calculate_obi()`

- [ ] 實現指標預熱邏輯
  ```python
  if trade_count < 50:
      return  # 等待預熱
  ```

### WebSocket 處理（如果使用真實 API）

- [ ] 正確處理 Binance 訊息格式
  - [ ] Depth: `'b'` 和 `'a'` 欄位（差異更新）
  - [ ] Trade: `'p'`, `'q'`, `'m'` 欄位

- [ ] 避免佇列溢位
  - [ ] 使用 `await socket.recv()` 而不是 `wait_for(..., timeout=1.0)`
  - [ ] 快速消費訊息，複雜處理放背景

- [ ] 錯誤處理
  ```python
  try:
      async with socket as s:
          while True:
              msg = await s.recv()
              # 處理...
  except Exception as e:
      print(f"WebSocket 錯誤: {e}")
  finally:
      await client.close_connection()
  ```

### 測試

- [ ] 短時間測試（1-5 分鐘）驗證程式可運行
- [ ] 檢查輸出統計是否合理
- [ ] 驗證 JSON 輸出格式正確（如果有）
- [ ] 長時間測試（1-24 小時）驗證穩定性

### 文檔

- [ ] 在檔案開頭標註用途和 Task 編號
- [ ] 列出依賴的核心模組
- [ ] 提供執行範例
- [ ] 說明輸出格式

---

## 🔍 快速參考表

| 程式 | 數據來源 | 引擎 | 狀態 | 用途 |
|------|---------|------|------|------|
| `quick_trading_test.py` | 模擬 | LayeredTradingEngine | ✅ 已驗證 | 快速策略測試 |
| `live_trading_simulation.py` | 真實 API | LayeredTradingEngine | ✅ 已驗證 | 真實市場驗證 |
| `real_trading_simulation.py` | 真實 API | LayeredTradingEngine | ✅ 已驗證 | 長期測試 |
| `real_trading_simulation_adjusted.py` | 真實 API | LayeredTradingEngine (調整參數) | ✅ 可運行 | 參數對比測試 |
| ~~`simple_live_trading.py`~~ | 真實 API | ❌ 自製簡化版 | ❌ 有 bug | ⚠️ 不建議使用 |

---

## 📌 重要提醒

1. **優先使用已驗證的程式**
   - 不要重新發明輪子
   - 已有的程式經過完整測試

2. **使用完整的 LayeredTradingEngine**
   - 不要自己組裝策略組件
   - 不要簡化指標計算

3. **參考正確的方法名稱**
   - 查看此文檔的「正確的依賴關係」章節
   - 遇到 `AttributeError` 先檢查方法名

4. **真實 API 測試前先用模擬數據**
   - 用 `quick_trading_test.py` 驗證邏輯
   - 確認沒問題再用 `live_trading_simulation.py`

5. **長期測試使用外部終端**
   - 不要在 VS Code 內跑 24 小時
   - 使用 `launch_multi_tests.sh` 啟動多視窗

---

## 🆘 遇到問題時

1. 查看此文檔的「常見錯誤避免」章節
2. 對比已驗證程式的寫法
3. 檢查方法名稱是否正確
4. 確認指標是否已預熱
5. 查看 WebSocket 訊息格式是否正確

---

**最後更新**: 2025-01-11  
**維護者**: Task 1.6 & 1.6.1 開發團隊
