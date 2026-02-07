# 🚀 幣安真實交易串接規劃

## 📋 目錄

1. [架構概述](#架構概述)
2. [開發階段](#開發階段)
3. [安全措施](#安全措施)
4. [程式模組設計](#程式模組設計)
5. [測試流程](#測試流程)
6. [上線檢查清單](#上線檢查清單)

---

## 🏗️ 架構概述

### 現有架構
```
┌─────────────────────────────────────────────────────────────┐
│                    Paper Trading System                      │
├─────────────────────────────────────────────────────────────┤
│  AI Advisors (🐺🐲🐟)  →  Bridge Files  →  Trading Bot      │
│         │                      │                  │          │
│    GPT-4/Ollama           JSON Files      Paper Orders       │
│                                               (模擬)         │
└─────────────────────────────────────────────────────────────┘
```

### 目標架構
```
┌─────────────────────────────────────────────────────────────┐
│                    Live Trading System                       │
├─────────────────────────────────────────────────────────────┤
│  AI Advisors (🐺🐲🐟)  →  Bridge Files  →  Trading Bot      │
│         │                      │                  │          │
│    GPT-4/Ollama           JSON Files      Live Orders        │
│                                               │               │
│                              ┌────────────────┴───────────┐  │
│                              │   Binance Futures Client   │  │
│                              │   ├─ Order Manager         │  │
│                              │   ├─ Position Manager      │  │
│                              │   ├─ Risk Manager          │  │
│                              │   └─ Emergency Stop        │  │
│                              └────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 📅 開發階段

### Phase 1: 基礎建設 (1-2 天)
- [ ] 建立 `BinanceFuturesClient` 合約交易客戶端
- [ ] 實作帳戶餘額、持倉查詢
- [ ] 實作基本下單 (市價/限價)
- [ ] 實作訂單取消、查詢

### Phase 2: 訂單管理 (2-3 天)
- [ ] 建立 `OrderManager` 訂單管理器
- [ ] 實作 Maker 掛單邏輯 (省手續費)
- [ ] 實作 Take Profit / Stop Loss 訂單
- [ ] 實作 Trailing Stop 訂單
- [ ] 訂單狀態追蹤與同步

### Phase 3: 持倉管理 (1-2 天)
- [ ] 建立 `PositionManager` 持倉管理器
- [ ] 實作開倉/平倉/加倉邏輯
- [ ] 持倉同步 (本地 vs 交易所)
- [ ] 槓桿設定管理

### Phase 4: 風控系統 (2-3 天)
- [ ] 建立 `RiskManager` 風險管理器
- [ ] 最大虧損限制 (單筆/日/總)
- [ ] 持倉規模限制
- [ ] 緊急停止機制 (Kill Switch)
- [ ] 異常檢測與告警

### Phase 5: 整合測試 (3-5 天)
- [ ] Testnet 完整測試
- [ ] 與現有 AI 策略整合
- [ ] 壓力測試
- [ ] 小額真實交易測試

---

## 🔒 安全措施

### 1. API 權限設定 (幣安端)
```
✅ 必須啟用:
  - 讀取權限 (Read)
  - 合約交易權限 (Futures)

❌ 禁止啟用:
  - 提現權限 (Withdraw)
  - 轉帳權限 (Transfer)

🔐 建議:
  - 綁定 IP 白名單
  - 設定 API 過期時間
```

### 2. 程式端保護
```python
# 最大風險限制
MAX_POSITION_SIZE_USD = 1000      # 最大持倉 $1000
MAX_LEVERAGE = 20                  # 最大槓桿 20x
MAX_DAILY_LOSS_USD = 50           # 日最大虧損 $50
MAX_DAILY_LOSS_PCT = 5            # 日最大虧損 5%
EMERGENCY_STOP_LOSS_PCT = 10      # 緊急停止閾值 10%
```

### 3. 環境變數管理
```bash
# .env 文件 (不要提交到 git!)
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
BINANCE_TESTNET=true  # 先用 testnet 測試!
```

---

## 🧩 程式模組設計

### 1. `src/exchange/binance_futures_client.py`
```python
"""
幣安合約交易客戶端

功能:
- 合約帳戶管理
- 訂單下單/取消/查詢
- 持倉管理
- 槓桿設定
"""

class BinanceFuturesClient:
    def __init__(self, testnet: bool = True):
        """初始化，預設使用 Testnet"""
        pass
    
    # === 帳戶相關 ===
    async def get_account_balance(self) -> dict
    async def get_positions(self) -> list
    async def set_leverage(self, symbol: str, leverage: int) -> dict
    
    # === 訂單相關 ===
    async def place_market_order(self, symbol, side, quantity) -> dict
    async def place_limit_order(self, symbol, side, quantity, price) -> dict
    async def place_stop_loss(self, symbol, side, quantity, stop_price) -> dict
    async def place_take_profit(self, symbol, side, quantity, price) -> dict
    async def cancel_order(self, symbol, order_id) -> dict
    async def get_order_status(self, symbol, order_id) -> dict
    async def get_open_orders(self, symbol) -> list
    
    # === 持倉相關 ===
    async def close_position(self, symbol, side) -> dict
    async def get_position_risk(self, symbol) -> dict
```

### 2. `src/trading/order_manager.py`
```python
"""
訂單管理器

功能:
- 智能下單 (Maker 優先)
- 訂單追蹤
- 滑點控制
- 重試機制
"""

class OrderManager:
    def __init__(self, client: BinanceFuturesClient):
        pass
    
    async def smart_entry(self, direction, size_usd, leverage) -> Order
    async def smart_exit(self, position, reason) -> Order
    async def set_stop_loss(self, position, sl_price) -> Order
    async def set_take_profit(self, position, tp_price) -> Order
    async def cancel_all_orders(self, symbol) -> bool
```

### 3. `src/trading/position_manager.py`
```python
"""
持倉管理器

功能:
- 持倉狀態追蹤
- 開/平/加倉邏輯
- PnL 計算
- 持倉同步
"""

class PositionManager:
    def __init__(self, client: BinanceFuturesClient):
        pass
    
    async def open_position(self, direction, size, leverage, tp, sl) -> Position
    async def close_position(self, position, reason) -> Trade
    async def add_to_position(self, position, size) -> Position
    async def sync_positions(self) -> list  # 與交易所同步
    async def get_current_pnl(self) -> float
```

### 4. `src/trading/risk_manager.py`
```python
"""
風險管理器

功能:
- 風險限制檢查
- 緊急停止機制
- 異常告警
"""

class RiskManager:
    def __init__(self, config: dict):
        self.max_position_size = config['max_position_size']
        self.max_daily_loss = config['max_daily_loss']
        self.emergency_threshold = config['emergency_threshold']
    
    def check_can_open(self, size, leverage) -> tuple[bool, str]
    def check_daily_loss(self) -> tuple[bool, float]
    def trigger_emergency_stop(self) -> bool
    def send_alert(self, message) -> None
```

### 5. `src/trading/live_trading_engine.py`
```python
"""
真實交易引擎

整合所有模組，與 AI 策略對接
"""

class LiveTradingEngine:
    def __init__(self, config: dict):
        self.client = BinanceFuturesClient(testnet=config['testnet'])
        self.order_manager = OrderManager(self.client)
        self.position_manager = PositionManager(self.client)
        self.risk_manager = RiskManager(config['risk'])
    
    async def run(self):
        """主運行循環"""
        while True:
            # 1. 讀取 AI 指令
            # 2. 風險檢查
            # 3. 執行交易
            # 4. 更新狀態
            pass
    
    async def process_ai_signal(self, signal: dict) -> None
    async def handle_position_exit(self, reason: str) -> None
    async def emergency_shutdown(self) -> None
```

---

## 🧪 測試流程

### Step 1: 單元測試
```bash
# 測試各模組
pytest tests/test_binance_futures_client.py
pytest tests/test_order_manager.py
pytest tests/test_risk_manager.py
```

### Step 2: Testnet 整合測試
```bash
# 使用 Binance Testnet
BINANCE_TESTNET=true python scripts/live_trading_test.py
```

### Step 3: 小額真實測試
```bash
# 使用最小金額 ($10-20) 測試
BINANCE_TESTNET=false python scripts/live_trading_minimal.py
```

### Step 4: 逐步增加金額
```
Week 1: $50 測試
Week 2: $100 測試  
Week 3: $200 測試
...根據表現調整
```

---

## ✅ 上線檢查清單

### 程式端
- [ ] 所有模組單元測試通過
- [ ] Testnet 運行 24 小時無異常
- [ ] 緊急停止機制測試通過
- [ ] 日誌記錄完整
- [ ] 錯誤處理完善

### 幣安端
- [ ] API 權限設定正確 (無提現權限)
- [ ] IP 白名單已設定
- [ ] 合約帳戶已開通
- [ ] 測試資金已到位

### 風控設定
- [ ] 最大持倉限制已設定
- [ ] 日虧損限制已設定
- [ ] 緊急停止閾值已設定
- [ ] 告警通知已設定 (Telegram/Email)

### 監控
- [ ] 即時 PnL 監控
- [ ] 訂單狀態監控
- [ ] 系統健康監控
- [ ] 異常告警機制

---

## 🎯 下一步行動

1. **確認幣安 API 設定**
   - 創建 API Key (只開合約交易權限)
   - 設定 IP 白名單

2. **開始 Phase 1 開發**
   - 建立 `BinanceFuturesClient`
   - 實作基本帳戶/訂單功能

3. **Testnet 測試**
   - 取得 Testnet API Key
   - 進行完整功能測試

---

*文件建立: 2025-11-26*
*狀態: 規劃階段*
