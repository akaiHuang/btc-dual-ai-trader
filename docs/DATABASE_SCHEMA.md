# 資料庫 Schema 設計文檔

**版本**: 1.0  
**日期**: 2025-11-10  
**狀態**: ✅ 已完成

---

## 📋 目錄

1. [架構總覽](#架構總覽)
2. [PostgreSQL Schema](#postgresql-schema)
3. [InfluxDB Schema](#influxdb-schema)
4. [Redis Schema](#redis-schema)
5. [資料流設計](#資料流設計)

---

## 架構總覽

### 資料庫分工

```
┌─────────────────────────────────────────────────────────┐
│                    資料庫架構                              │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ PostgreSQL   │  │ InfluxDB     │  │ Redis        │  │
│  │ (關聯資料)    │  │ (時序資料)    │  │ (快取/即時)   │  │
│  ├──────────────┤  ├──────────────┤  ├──────────────┤  │
│  │ 交易記錄      │  │ K線資料       │  │ 即時價格      │  │
│  │ AI訓練集     │  │ 指標計算結果   │  │ OBI快取      │  │
│  │ 模型元數據    │  │ 性能指標      │  │ 訊號佇列      │  │
│  │ 系統日誌      │  │               │  │ 會話資料      │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│         ▲                 ▲                 ▲            │
│         │                 │                 │            │
│         └─────────────────┴─────────────────┘            │
│                    Python 應用層                          │
└─────────────────────────────────────────────────────────┘
```

### 選擇理由

| 資料庫 | 用途 | 優勢 |
|--------|------|------|
| **PostgreSQL** | 關聯資料、交易記錄 | ACID保證、複雜查詢、JSON支援 |
| **InfluxDB** | 時序資料、K線 | 高效時序查詢、自動聚合、壓縮率高 |
| **Redis** | 快取、即時資料 | 超低延遲、Pub/Sub、過期機制 |

---

## PostgreSQL Schema

### 1. 交易記錄表 (`trades`)

**用途**: 記錄所有交易（回測、模擬、實盤）

```sql
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    trade_id VARCHAR(64) UNIQUE NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,  -- 'LONG' or 'SHORT'
    
    -- 進場資訊
    entry_time TIMESTAMP NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    leverage INTEGER NOT NULL,
    
    -- 出場資訊
    exit_time TIMESTAMP,
    exit_price DECIMAL(20, 8),
    
    -- 損益
    pnl DECIMAL(20, 8),
    pnl_percentage DECIMAL(10, 4),
    fee DECIMAL(20, 8),
    
    -- 風控
    stop_loss DECIMAL(20, 8),
    take_profit DECIMAL(20, 8),
    
    -- 狀態
    status VARCHAR(20) NOT NULL,  -- 'OPEN', 'CLOSED', 'CANCELLED'
    mode VARCHAR(20) NOT NULL,  -- 'BACKTEST', 'PAPER', 'LIVE'
    
    -- 策略
    strategy_name VARCHAR(50) NOT NULL,
    strategy_version VARCHAR(20),
    
    -- 時間戳
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_entry_time ON trades(entry_time);
CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_mode ON trades(mode);
CREATE INDEX idx_trades_strategy ON trades(strategy_name);
```

**關鍵欄位說明**:
- `trade_id`: UUID，全域唯一
- `mode`: 區分回測/模擬/實盤
- `pnl`: 實際損益（USDT）
- `pnl_percentage`: 損益百分比

### 2. 訊號標註表 (`signal_annotations`)

**用途**: AI 訓練資料，記錄每個時間點的技術指標和訊號

```sql
CREATE TABLE signal_annotations (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    
    -- 訊號
    signal_type VARCHAR(10) NOT NULL,  -- 'LONG', 'SHORT', 'NONE'
    signal_strength DECIMAL(5, 4),  -- 0.0 - 1.0
    
    -- 技術指標
    indicators JSONB NOT NULL,  -- 包含所有指標值
    
    -- 市場狀態
    market_regime VARCHAR(20),  -- 'BULL', 'BEAR', 'CONSOLIDATION'
    volatility_level VARCHAR(10),  -- 'LOW', 'MEDIUM', 'HIGH'
    
    -- 標註元數據
    is_correct BOOLEAN,
    actual_outcome VARCHAR(10),  -- 'PROFIT', 'LOSS', 'BREAKEVEN'
    outcome_pnl DECIMAL(20, 8),
    holding_minutes INTEGER,
    
    -- AI 預測（事後填入）
    predicted_signal VARCHAR(10),
    prediction_confidence DECIMAL(5, 4),
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_signals_timestamp ON signal_annotations(timestamp);
CREATE INDEX idx_signals_symbol_timeframe ON signal_annotations(symbol, timeframe);
CREATE INDEX idx_signals_type ON signal_annotations(signal_type);
CREATE INDEX idx_signals_regime ON signal_annotations(market_regime);
```

**JSONB `indicators` 結構範例**:
```json
{
  "rsi": 65.34,
  "stochrsi_k": 78.21,
  "stochrsi_d": 71.45,
  "ma_7": 43250.50,
  "ma_25": 42800.30,
  "boll_upper": 44500.00,
  "boll_middle": 43000.00,
  "boll_lower": 41500.00,
  "sar": 42500.00,
  "atr": 850.50,
  "obi": 0.1234,
  "volume": 1500.25,
  "quote_volume": 64500000.50
}
```

### 3. AI 訓練資料表 (`training_data`)

**用途**: 模型訓練和驗證資料集

```sql
CREATE TABLE training_data (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    
    -- 特徵向量（完整的特徵工程結果）
    features JSONB NOT NULL,
    
    -- 標籤
    label INTEGER NOT NULL,  -- 0: NONE, 1: LONG, 2: SHORT
    label_confidence DECIMAL(5, 4),
    
    -- 預測結果
    prediction INTEGER,
    prediction_confidence DECIMAL(5, 4),
    prediction_model VARCHAR(50),
    
    -- 實際結果
    actual_pnl DECIMAL(20, 8),
    holding_period INTEGER,  -- 分鐘數
    max_drawdown DECIMAL(10, 4),
    
    -- 資料來源
    data_source VARCHAR(20),  -- 'BACKTEST', 'PAPER', 'LIVE'
    model_version VARCHAR(50),
    
    -- 資料分割標記
    dataset_split VARCHAR(10),  -- 'TRAIN', 'VALIDATION', 'TEST'
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_training_timestamp ON training_data(timestamp);
CREATE INDEX idx_training_symbol ON training_data(symbol);
CREATE INDEX idx_training_label ON training_data(label);
CREATE INDEX idx_training_split ON training_data(dataset_split);
```

### 4. 模型元數據表 (`model_metadata`)

**用途**: 管理所有 AI 模型版本和性能指標

```sql
CREATE TABLE model_metadata (
    id SERIAL PRIMARY KEY,
    model_name VARCHAR(100) NOT NULL,
    model_version VARCHAR(50) NOT NULL,
    model_type VARCHAR(50) NOT NULL,  -- 'XGBoost', 'LightGBM', 'Prophet'
    
    -- 訓練資訊
    training_start_date TIMESTAMP,
    training_end_date TIMESTAMP,
    training_samples INTEGER,
    validation_samples INTEGER,
    test_samples INTEGER,
    
    -- 超參數
    hyperparameters JSONB,
    
    -- 性能指標（分類）
    accuracy DECIMAL(5, 4),
    precision DECIMAL(5, 4),
    recall DECIMAL(5, 4),
    f1_score DECIMAL(5, 4),
    
    -- 性能指標（交易）
    sharpe_ratio DECIMAL(10, 4),
    win_rate DECIMAL(5, 4),
    avg_profit DECIMAL(20, 8),
    avg_loss DECIMAL(20, 8),
    max_drawdown DECIMAL(10, 4),
    
    -- 特徵重要性
    feature_importance JSONB,
    
    -- 檔案資訊
    model_path VARCHAR(255),
    model_size_mb DECIMAL(10, 2),
    
    -- 狀態
    status VARCHAR(20) NOT NULL,  -- 'ACTIVE', 'INACTIVE', 'DEPRECATED'
    notes TEXT,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(model_name, model_version)
);

CREATE INDEX idx_model_status ON model_metadata(status);
CREATE INDEX idx_model_type ON model_metadata(model_type);
```

### 5. 系統日誌表 (`system_logs`)

**用途**: 記錄所有系統事件和錯誤

```sql
CREATE TABLE system_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    level VARCHAR(10) NOT NULL,  -- 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
    module VARCHAR(50) NOT NULL,
    function_name VARCHAR(100),
    message TEXT NOT NULL,
    details JSONB,
    
    -- 追蹤資訊
    trace_id VARCHAR(100),
    user_id VARCHAR(50),
    session_id VARCHAR(100),
    
    -- 錯誤資訊
    exception_type VARCHAR(100),
    stack_trace TEXT
);

CREATE INDEX idx_logs_timestamp ON system_logs(timestamp DESC);
CREATE INDEX idx_logs_level ON system_logs(level);
CREATE INDEX idx_logs_module ON system_logs(module);
CREATE INDEX idx_logs_trace_id ON system_logs(trace_id);

-- 自動清理 30 天前的日誌
CREATE INDEX idx_logs_cleanup ON system_logs(timestamp) WHERE level IN ('DEBUG', 'INFO');
```

### 6. 虛擬交易引擎狀態表 (`virtual_exchange_state`)

**用途**: 記錄虛擬交易所的帳戶狀態

```sql
CREATE TABLE virtual_exchange_state (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    
    -- 帳戶資訊
    balance DECIMAL(20, 8) NOT NULL,
    equity DECIMAL(20, 8) NOT NULL,
    margin_used DECIMAL(20, 8),
    margin_available DECIMAL(20, 8),
    
    -- 持倉
    open_positions JSONB,  -- [{symbol, side, quantity, entry_price, ...}]
    
    -- 統計
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    total_pnl DECIMAL(20, 8) DEFAULT 0,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_virtual_session ON virtual_exchange_state(session_id);
CREATE INDEX idx_virtual_timestamp ON virtual_exchange_state(timestamp);
```

---

## InfluxDB Schema

### Measurement 設計

InfluxDB 使用 **Measurement（測量）** 概念，類似於關聯資料庫的表。

### 1. K線資料 (`klines`)

```
Measurement: klines
Tags:
  - symbol (String): 交易對，如 "BTCUSDT"
  - interval (String): 時間框架，如 "1m", "5m", "1h"
  - source (String): 資料來源 "binance", "resampled"

Fields:
  - open (Float): 開盤價
  - high (Float): 最高價
  - low (Float): 最低價
  - close (Float): 收盤價
  - volume (Float): 成交量
  - quote_volume (Float): 成交額
  - trades (Integer): 成交筆數
  - taker_buy_base (Float): 主動買入量
  - taker_buy_quote (Float): 主動買入額

Time: timestamp (納秒精度)
```

**查詢範例**:
```sql
-- 查詢最近 24 小時的 1m K線
SELECT * FROM klines
WHERE symbol='BTCUSDT' 
  AND interval='1m'
  AND time >= now() - 24h

-- 計算 1 小時平均價
SELECT MEAN(close) FROM klines
WHERE symbol='BTCUSDT' 
  AND interval='1m'
  AND time >= now() - 1h
GROUP BY time(1h)
```

### 2. 技術指標 (`indicators`)

```
Measurement: indicators
Tags:
  - symbol (String)
  - interval (String)
  - indicator_type (String): "RSI", "MA", "BOLL", "SAR", "OBI"

Fields:
  - value (Float): 指標值
  - param1 (String): 參數1（如 RSI 的周期）
  - param2 (String): 參數2

Time: timestamp
```

### 3. 交易性能指標 (`performance_metrics`)

```
Measurement: performance_metrics
Tags:
  - strategy (String): 策略名稱
  - mode (String): "BACKTEST", "PAPER", "LIVE"
  - symbol (String)

Fields:
  - pnl (Float): 損益
  - win_rate (Float): 勝率
  - sharpe_ratio (Float): 夏普比率
  - max_drawdown (Float): 最大回撤
  - trades_count (Integer): 交易次數

Time: timestamp
```

### 4. 訂單簿快照 (`orderbook`)

```
Measurement: orderbook
Tags:
  - symbol (String)
  - depth (String): "5", "10", "20"

Fields:
  - best_bid (Float): 最高買價
  - best_ask (Float): 最低賣價
  - bid_volume (Float): 買單總量
  - ask_volume (Float): 賣單總量
  - obi (Float): 訂單簿失衡
  - spread (Float): 價差
  - bids (String): JSON 格式的買單列表
  - asks (String): JSON 格式的賣單列表

Time: timestamp
```

### InfluxDB 資料保留策略

```python
# 創建保留策略
CREATE RETENTION POLICY "30d_policy" ON "trading_db" 
  DURATION 30d REPLICATION 1 DEFAULT

CREATE RETENTION POLICY "1y_policy" ON "trading_db" 
  DURATION 365d REPLICATION 1

CREATE RETENTION POLICY "infinite_policy" ON "trading_db" 
  DURATION INF REPLICATION 1
```

---

## Redis Schema

### Key 命名規範

```
格式: {namespace}:{entity}:{identifier}
範例: btc:price:BTCUSDT
     btc:obi:BTCUSDT:1m
     btc:signal:BTCUSDT:LONG
```

### 1. 即時價格快取

```python
# Key: btc:price:{symbol}
# Type: Hash
# TTL: 60 秒

HSET btc:price:BTCUSDT 
  "price" "43250.50"
  "volume_24h" "1234567890.50"
  "change_24h" "2.34"
  "timestamp" "1699612345"

# 查詢
HGETALL btc:price:BTCUSDT
```

### 2. OBI（訂單簿失衡）快取

```python
# Key: btc:obi:{symbol}:{interval}
# Type: String (JSON)
# TTL: 30 秒

SET btc:obi:BTCUSDT:1m 
'{
  "obi": 0.1234,
  "bid_volume": 150.25,
  "ask_volume": 132.10,
  "best_bid": 43250.00,
  "best_ask": 43251.50,
  "timestamp": 1699612345
}'
EX 30
```

### 3. 訊號佇列

```python
# Key: btc:signals:{symbol}
# Type: List (FIFO)

LPUSH btc:signals:BTCUSDT 
'{
  "signal": "LONG",
  "strength": 0.85,
  "price": 43250.50,
  "timestamp": 1699612345,
  "indicators": {...}
}'

# 消費訊號
RPOP btc:signals:BTCUSDT
```

### 4. 策略狀態快取

```python
# Key: btc:strategy:{strategy_name}:state
# Type: Hash
# TTL: 無（持久）

HSET btc:strategy:short_term_v1:state
  "status" "RUNNING"
  "last_signal" "LONG"
  "last_trade_time" "1699612345"
  "open_positions" "1"
  "total_pnl" "1250.50"
```

### 5. 會話管理

```python
# Key: btc:session:{session_id}
# Type: Hash
# TTL: 3600 秒（1小時）

HSET btc:session:abc123
  "user_id" "user_001"
  "strategy" "short_term_v1"
  "mode" "PAPER"
  "start_time" "1699612345"
  "status" "ACTIVE"

EXPIRE btc:session:abc123 3600
```

### 6. 速率限制

```python
# Key: btc:ratelimit:{api}:{user_id}
# Type: String (Counter)
# TTL: 60 秒

INCR btc:ratelimit:binance_api:user_001
EXPIRE btc:ratelimit:binance_api:user_001 60

# 檢查限制
GET btc:ratelimit:binance_api:user_001
# 如果 > 1200，拒絕請求
```

---

## 資料流設計

### 1. 回測流程

```
┌─────────────┐
│ InfluxDB    │
│ (K線資料)    │
└──────┬──────┘
       │ 讀取
       ▼
┌─────────────┐      ┌─────────────┐
│ 回測引擎     │ ───> │ PostgreSQL  │
│             │      │ (交易記錄)   │
└─────────────┘      └─────────────┘
       │
       ├──> PostgreSQL (訓練資料)
       └──> InfluxDB (性能指標)
```

### 2. 實盤交易流程

```
┌─────────────┐      ┌─────────────┐
│ Binance API │ ───> │ Redis       │
│ (WebSocket) │      │ (即時快取)   │
└─────────────┘      └──────┬──────┘
                            │
       ┌────────────────────┘
       │
       ▼
┌─────────────┐      ┌─────────────┐
│ 策略引擎     │ ───> │ PostgreSQL  │
│             │      │ (交易記錄)   │
└──────┬──────┘      └─────────────┘
       │
       └──> InfluxDB (K線、指標)
```

### 3. AI 訓練流程

```
┌─────────────┐
│ PostgreSQL  │
│ (訓練資料)   │
└──────┬──────┘
       │ 讀取
       ▼
┌─────────────┐      ┌─────────────┐
│ AI 訓練模組  │ ───> │ PostgreSQL  │
│             │      │ (模型元數據) │
└─────────────┘      └─────────────┘
       │
       └──> 本地檔案 (模型檔案)
```

---

## 資料備份策略

### PostgreSQL
- **完整備份**: 每天 2:00 AM
- **增量備份**: 每 6 小時
- **保留期限**: 30 天
- **備份工具**: `pg_dump` + 定時任務

### InfluxDB
- **快照備份**: 每天 3:00 AM
- **保留期限**: 7 天
- **備份工具**: InfluxDB Backup

### Redis
- **RDB 快照**: 每小時
- **AOF 日誌**: 每秒同步
- **保留期限**: 3 天

---

## 效能優化建議

### PostgreSQL
1. **分區表**: 按月分區 `trades` 表
2. **索引**: 為常用查詢欄位建立索引
3. **JSONB**: 使用 GIN 索引加速 JSON 查詢
4. **連接池**: 使用 `pgbouncer` 或 `pgpool`

### InfluxDB
1. **標籤設計**: 高基數欄位避免作為標籤
2. **批次寫入**: 使用批次插入提升效能
3. **壓縮**: 啟用自動壓縮
4. **分片**: 大數據量時考慮分片

### Redis
1. **記憶體管理**: 設定 `maxmemory-policy`
2. **持久化**: 根據需求選擇 RDB 或 AOF
3. **管道**: 使用 Pipeline 減少網路往返
4. **叢集**: 大流量時考慮 Redis Cluster

---

## 監控指標

### 需要監控的指標
1. **資料庫大小**: 監控磁碟使用率
2. **查詢效能**: 慢查詢日誌
3. **連接數**: 監控連接池狀態
4. **寫入速率**: InfluxDB 寫入 TPS
5. **快取命中率**: Redis 命中率

### 告警閾值
- PostgreSQL 連接數 > 80%
- InfluxDB 寫入延遲 > 100ms
- Redis 記憶體使用率 > 90%
- 磁碟使用率 > 85%

---

**文檔結束**
