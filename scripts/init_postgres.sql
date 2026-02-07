-- BTC 智能交易系統 - PostgreSQL 初始化腳本
-- 創建時間: 2025-01-10
-- 用途: 建立資料庫 schema

-- ==========================================
-- 交易記錄表
-- ==========================================
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    trade_id VARCHAR(64) UNIQUE NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,  -- 'LONG' or 'SHORT'
    entry_time TIMESTAMP NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    exit_time TIMESTAMP,
    exit_price DECIMAL(20, 8),
    quantity DECIMAL(20, 8) NOT NULL,
    leverage INTEGER NOT NULL,
    pnl DECIMAL(20, 8),
    pnl_percentage DECIMAL(10, 4),
    fee DECIMAL(20, 8),
    status VARCHAR(20) NOT NULL,  -- 'OPEN', 'CLOSED', 'CANCELLED'
    stop_loss DECIMAL(20, 8),
    take_profit DECIMAL(20, 8),
    mode VARCHAR(20) NOT NULL,  -- 'BACKTEST', 'PAPER', 'LIVE'
    strategy_name VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_entry_time ON trades(entry_time);
CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_mode ON trades(mode);

-- ==========================================
-- 訊號標註表（用於 AI 訓練）
-- ==========================================
CREATE TABLE IF NOT EXISTS signal_annotations (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    signal_type VARCHAR(10) NOT NULL,  -- 'LONG', 'SHORT', 'NONE'
    confidence DECIMAL(5, 4),  -- 0.0000 - 1.0000
    
    -- 技術指標值
    rsi DECIMAL(10, 4),
    stochrsi_k DECIMAL(10, 4),
    stochrsi_d DECIMAL(10, 4),
    ma_7 DECIMAL(20, 8),
    ma_25 DECIMAL(20, 8),
    boll_upper DECIMAL(20, 8),
    boll_middle DECIMAL(20, 8),
    boll_lower DECIMAL(20, 8),
    sar DECIMAL(20, 8),
    atr DECIMAL(20, 8),
    obi DECIMAL(10, 6),  -- Order Book Imbalance
    volume DECIMAL(20, 8),
    
    -- 市場狀態
    market_regime VARCHAR(20),  -- 'BULL', 'BEAR', 'CONSOLIDATION', 'NEUTRAL'
    
    -- 標註元數據
    is_correct BOOLEAN,  -- 事後驗證是否正確
    actual_outcome VARCHAR(10),  -- 'PROFIT', 'LOSS', 'BREAKEVEN'
    outcome_pnl DECIMAL(20, 8),
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_signals_timestamp ON signal_annotations(timestamp);
CREATE INDEX idx_signals_symbol ON signal_annotations(symbol);
CREATE INDEX idx_signals_type ON signal_annotations(signal_type);

-- ==========================================
-- AI 訓練資料表
-- ==========================================
CREATE TABLE IF NOT EXISTS training_data (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,  -- '1m', '3m', '15m', etc.
    
    -- 特徵向量（JSON 格式存儲完整特徵）
    features JSONB NOT NULL,
    
    -- 標籤
    label INTEGER NOT NULL,  -- 0: NONE, 1: LONG, 2: SHORT
    label_confidence DECIMAL(5, 4),
    
    -- 預測結果（用於驗證模型）
    prediction INTEGER,
    prediction_confidence DECIMAL(5, 4),
    
    -- 實際結果
    actual_pnl DECIMAL(20, 8),
    holding_period INTEGER,  -- 持倉分鐘數
    
    -- 資料來源
    data_source VARCHAR(20),  -- 'BACKTEST', 'PAPER', 'LIVE'
    model_version VARCHAR(50),
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_training_timestamp ON training_data(timestamp);
CREATE INDEX idx_training_symbol ON training_data(symbol);
CREATE INDEX idx_training_label ON training_data(label);

-- ==========================================
-- 模型元數據表
-- ==========================================
CREATE TABLE IF NOT EXISTS model_metadata (
    id SERIAL PRIMARY KEY,
    model_name VARCHAR(100) NOT NULL,
    model_version VARCHAR(50) NOT NULL,
    model_type VARCHAR(50) NOT NULL,  -- 'XGBoost', 'LightGBM', 'Prophet', etc.
    
    -- 訓練資訊
    training_start_date TIMESTAMP,
    training_end_date TIMESTAMP,
    training_samples INTEGER,
    
    -- 超參數（JSON 格式）
    hyperparameters JSONB,
    
    -- 性能指標
    accuracy DECIMAL(5, 4),
    precision DECIMAL(5, 4),
    recall DECIMAL(5, 4),
    f1_score DECIMAL(5, 4),
    sharpe_ratio DECIMAL(10, 4),
    win_rate DECIMAL(5, 4),
    
    -- 特徵重要性（JSON 格式）
    feature_importance JSONB,
    
    -- 檔案路徑
    model_path VARCHAR(255),
    
    -- 狀態
    status VARCHAR(20) NOT NULL,  -- 'ACTIVE', 'INACTIVE', 'DEPRECATED'
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(model_name, model_version)
);

CREATE INDEX idx_model_status ON model_metadata(status);
CREATE INDEX idx_model_version ON model_metadata(model_version);

-- ==========================================
-- 虛擬交易引擎狀態表
-- ==========================================
CREATE TABLE IF NOT EXISTS virtual_exchange_state (
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

-- ==========================================
-- 系統日誌表
-- ==========================================
CREATE TABLE IF NOT EXISTS system_logs (
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

-- ==========================================
-- 更新時間觸發器
-- ==========================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_trades_updated_at BEFORE UPDATE ON trades
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_model_metadata_updated_at BEFORE UPDATE ON model_metadata
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ==========================================
-- 初始資料插入
-- ==========================================
INSERT INTO system_logs (level, module, message, details) 
VALUES ('INFO', 'INIT', 'Database schema initialized successfully', 
        '{"version": "1.0", "tables": ["trades", "signal_annotations", "training_data", "model_metadata", "system_logs"]}'::jsonb);

-- 完成
\echo 'PostgreSQL schema initialization completed!'
