"""
Task 1.4 資料庫 Schema 設計 - 測試腳本
展示資料庫架構設計成果
"""

import json
from datetime import datetime


def print_header(title: str):
    """打印標題"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_section(title: str):
    """打印小節"""
    print(f"\n📊 {title}")
    print("-" * 70)


def test_postgresql_schema():
    """展示 PostgreSQL Schema"""
    print_header("PostgreSQL 關聯式資料庫設計")
    
    tables = {
        "trades": {
            "description": "交易記錄表",
            "columns": [
                "trade_id (BIGSERIAL PRIMARY KEY)",
                "symbol (VARCHAR 20)",
                "strategy (VARCHAR 50)",
                "side (VARCHAR 10) - BUY/SELL",
                "order_type (VARCHAR 20)",
                "price (DECIMAL 18,8)",
                "quantity (DECIMAL 18,8)",
                "total_value (DECIMAL 18,8)",
                "commission (DECIMAL 18,8)",
                "pnl (DECIMAL 18,8)",
                "created_at (TIMESTAMP)",
            ],
            "indexes": [
                "idx_trades_symbol_created",
                "idx_trades_strategy",
                "idx_trades_created_at DESC",
            ]
        },
        "signal_annotations": {
            "description": "交易信號標註表（AI 訓練用）",
            "columns": [
                "annotation_id (BIGSERIAL PRIMARY KEY)",
                "signal_id (VARCHAR 100 UNIQUE)",
                "symbol (VARCHAR 20)",
                "timestamp (TIMESTAMP)",
                "signal_type (VARCHAR 20) - BUY/SELL/HOLD",
                "indicators (JSONB) - RSI, MA, OBI 等",
                "actual_result (VARCHAR 20) - WIN/LOSS/NEUTRAL",
                "pnl (DECIMAL 18,8)",
            ],
            "indexes": [
                "idx_annotations_timestamp",
                "idx_annotations_signal_type",
                "idx_annotations_indicators GIN",
            ]
        },
        "training_data": {
            "description": "AI 訓練資料集",
            "columns": [
                "data_id (BIGSERIAL PRIMARY KEY)",
                "symbol (VARCHAR 20)",
                "timestamp (TIMESTAMP)",
                "features (JSONB) - 特徵向量",
                "label (INTEGER) - 1=BUY, 0=HOLD, -1=SELL",
                "market_regime (VARCHAR 20)",
                "data_version (VARCHAR 20)",
            ],
            "indexes": [
                "idx_training_timestamp",
                "idx_training_label",
                "idx_training_features GIN",
            ]
        },
        "model_metadata": {
            "description": "AI 模型元資料",
            "columns": [
                "model_id (BIGSERIAL PRIMARY KEY)",
                "model_name (VARCHAR 100 UNIQUE)",
                "model_version (VARCHAR 20)",
                "algorithm (VARCHAR 50) - XGBoost/LightGBM",
                "hyperparameters (JSONB)",
                "training_period_start (TIMESTAMP)",
                "training_period_end (TIMESTAMP)",
                "accuracy (DECIMAL 5,4)",
                "win_rate (DECIMAL 5,4)",
                "sharpe_ratio (DECIMAL 10,6)",
            ],
            "indexes": [
                "idx_model_name_version",
                "idx_model_accuracy DESC",
            ]
        },
        "virtual_exchange_state": {
            "description": "紙上交易狀態追蹤",
            "columns": [
                "session_id (VARCHAR 100 PRIMARY KEY)",
                "timestamp (TIMESTAMP)",
                "balance (DECIMAL 18,8)",
                "equity (DECIMAL 18,8)",
                "margin_used (DECIMAL 18,8)",
                "open_positions (JSONB)",
                "total_trades (INTEGER)",
                "winning_trades (INTEGER)",
                "total_pnl (DECIMAL 18,8)",
            ],
            "indexes": [
                "idx_virtual_timestamp DESC",
            ]
        },
        "system_logs": {
            "description": "系統日誌",
            "columns": [
                "log_id (BIGSERIAL PRIMARY KEY)",
                "timestamp (TIMESTAMP)",
                "log_level (VARCHAR 20) - DEBUG/INFO/WARNING/ERROR",
                "component (VARCHAR 50)",
                "function_name (VARCHAR 100)",
                "message (TEXT)",
                "trace_id (VARCHAR 100) - 追蹤ID",
                "exception_type (VARCHAR 100)",
                "stack_trace (TEXT)",
            ],
            "indexes": [
                "idx_logs_timestamp DESC",
                "idx_logs_level",
                "idx_logs_trace_id",
            ]
        }
    }
    
    print(f"\n📦 共設計 {len(tables)} 張表\n")
    
    for table_name, info in tables.items():
        print(f"✅ {table_name}")
        print(f"   描述: {info['description']}")
        print(f"   欄位數: {len(info['columns'])}")
        print(f"   索引數: {len(info['indexes'])}")
        print(f"   主要欄位: {', '.join(info['columns'][:3])}")
        print()


def test_influxdb_schema():
    """展示 InfluxDB Schema"""
    print_header("InfluxDB 時間序列資料庫設計")
    
    buckets = {
        "trading_data": {
            "retention": "30 天",
            "description": "短期交易資料",
            "measurements": ["klines", "indicators", "orderbook"]
        },
        "trading_data_1y": {
            "retention": "1 年",
            "description": "長期交易資料",
            "measurements": ["klines (降採樣)"]
        },
        "trading_data_forever": {
            "retention": "永久",
            "description": "重要歷史資料",
            "measurements": ["klines (關鍵時期)"]
        },
        "performance_metrics": {
            "retention": "90 天",
            "description": "系統性能指標",
            "measurements": ["performance_metrics"]
        }
    }
    
    print(f"\n📦 共設計 {len(buckets)} 個 Buckets\n")
    
    for bucket_name, info in buckets.items():
        print(f"✅ {bucket_name}")
        print(f"   保留期限: {info['retention']}")
        print(f"   描述: {info['description']}")
        print(f"   Measurements: {', '.join(info['measurements'])}")
        print()
    
    print_section("Measurement 結構")
    
    measurements = {
        "klines": {
            "tags": ["symbol", "interval"],
            "fields": ["open", "high", "low", "close", "volume"],
            "example": {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "open": 43250.0,
                "high": 43300.0,
                "low": 43200.0,
                "close": 43280.0,
                "volume": 150.25
            }
        },
        "indicators": {
            "tags": ["symbol", "indicator_type"],
            "fields": ["value", "signal"],
            "example": {
                "symbol": "BTCUSDT",
                "indicator_type": "RSI",
                "value": 65.5,
                "signal": "NEUTRAL"
            }
        },
        "performance_metrics": {
            "tags": ["component", "metric_type"],
            "fields": ["value", "count"],
            "example": {
                "component": "binance_client",
                "metric_type": "api_latency",
                "value": 125.5,
                "count": 1
            }
        },
        "orderbook": {
            "tags": ["symbol"],
            "fields": ["bid_price", "bid_volume", "ask_price", "ask_volume", "obi"],
            "example": {
                "symbol": "BTCUSDT",
                "bid_price": 43280.0,
                "bid_volume": 125.5,
                "ask_price": 43281.0,
                "ask_volume": 85.3,
                "obi": 0.19
            }
        }
    }
    
    for measurement_name, info in measurements.items():
        print(f"\n✅ {measurement_name}")
        print(f"   Tags: {', '.join(info['tags'])}")
        print(f"   Fields: {', '.join(info['fields'])}")
        print(f"   範例資料: {json.dumps(info['example'], indent=6, ensure_ascii=False)}")


def test_redis_schema():
    """展示 Redis Schema"""
    print_header("Redis 即時快取設計")
    
    key_patterns = {
        "price:{symbol}": {
            "type": "String (JSON)",
            "ttl": "60 秒",
            "description": "實時價格快取",
            "example": {
                "symbol": "BTCUSDT",
                "price": 43280.50,
                "timestamp": "2025-11-10T15:30:00Z"
            }
        },
        "obi:{symbol}": {
            "type": "String (JSON)",
            "ttl": "10 秒",
            "description": "訂單簿不平衡指標",
            "example": {
                "symbol": "BTCUSDT",
                "obi": 0.35,
                "bid_volume": 1250.5,
                "ask_volume": 850.3,
                "timestamp": "2025-11-10T15:30:00Z"
            }
        },
        "signal:{strategy}:{symbol}": {
            "type": "List (Queue)",
            "ttl": "無限制",
            "description": "交易信號佇列",
            "example": {
                "strategy": "obi_rsi_combined",
                "symbol": "BTCUSDT",
                "action": "BUY",
                "price": 43280.50,
                "confidence": 0.85,
                "timestamp": "2025-11-10T15:30:00Z"
            }
        },
        "strategy:{strategy}:state": {
            "type": "String (JSON)",
            "ttl": "無限制",
            "description": "策略運行狀態",
            "example": {
                "strategy": "obi_rsi_combined",
                "status": "RUNNING",
                "position": None,
                "last_signal": "BUY",
                "updated_at": "2025-11-10T15:30:00Z"
            }
        },
        "session:{user_id}": {
            "type": "Hash",
            "ttl": "24 小時",
            "description": "用戶會話資料",
            "example": {
                "user_id": "user_001",
                "login_time": "2025-11-10T10:00:00Z",
                "last_activity": "2025-11-10T15:30:00Z",
                "active_strategies": ["obi_rsi_combined"]
            }
        },
        "ratelimit:{endpoint}:{ip}": {
            "type": "String (Counter)",
            "ttl": "60 秒",
            "description": "API 限流計數器",
            "example": {
                "endpoint": "api/v1/trade",
                "ip": "127.0.0.1",
                "count": 45,
                "limit": 60
            }
        }
    }
    
    print(f"\n📦 共設計 {len(key_patterns)} 種 Key Pattern\n")
    
    for pattern, info in key_patterns.items():
        print(f"✅ {pattern}")
        print(f"   類型: {info['type']}")
        print(f"   TTL: {info['ttl']}")
        print(f"   描述: {info['description']}")
        print(f"   範例: {json.dumps(info['example'], indent=6, ensure_ascii=False)}")
        print()


def test_data_flow():
    """展示資料流設計"""
    print_header("資料流設計")
    
    flows = {
        "回測模式": [
            "1. 從 Parquet 讀取歷史 K 線",
            "2. 計算技術指標並存入 InfluxDB",
            "3. 執行交易策略產生信號",
            "4. 虛擬下單並記錄到 PostgreSQL (trades)",
            "5. 計算績效指標存入 InfluxDB (performance_metrics)",
            "6. 生成訓練資料集存入 PostgreSQL (training_data)"
        ],
        "實盤模式": [
            "1. WebSocket 接收即時 K 線",
            "2. 寫入 InfluxDB (klines) + Redis 快取 (price)",
            "3. 計算 OBI 並存入 Redis (obi)",
            "4. 策略引擎從 Redis 讀取最新數據",
            "5. 產生信號推送到 Redis 佇列 (signal)",
            "6. 執行真實下單並記錄到 PostgreSQL (trades)",
            "7. 更新策略狀態到 Redis (strategy:state)"
        ],
        "AI 訓練模式": [
            "1. 從 PostgreSQL 讀取標註資料 (signal_annotations)",
            "2. 從 InfluxDB 讀取對應的指標資料",
            "3. 特徵工程並存入 PostgreSQL (training_data)",
            "4. 訓練模型（XGBoost/LightGBM）",
            "5. 模型評估並存入 PostgreSQL (model_metadata)",
            "6. 部署最佳模型用於實盤交易"
        ]
    }
    
    for flow_name, steps in flows.items():
        print(f"\n📈 {flow_name}")
        print("-" * 70)
        for step in steps:
            print(f"   {step}")


def test_backup_strategy():
    """展示備份策略"""
    print_header("備份策略設計")
    
    strategies = {
        "PostgreSQL": {
            "頻率": "每日 02:00",
            "方法": "pg_dump 全量備份",
            "保留": "最近 30 天",
            "儲存": "S3 / 本地磁碟"
        },
        "InfluxDB": {
            "頻率": "每小時",
            "方法": "自動降採樣到長期 bucket",
            "保留": "trading_data: 30天, trading_data_1y: 1年",
            "儲存": "內建多層保留策略"
        },
        "Redis": {
            "頻率": "每 15 分鐘",
            "方法": "RDB 快照 + AOF 日誌",
            "保留": "最近 24 小時",
            "儲存": "本地磁碟"
        }
    }
    
    print()
    for db, info in strategies.items():
        print(f"✅ {db}")
        print(f"   頻率: {info['頻率']}")
        print(f"   方法: {info['方法']}")
        print(f"   保留: {info['保留']}")
        print(f"   儲存: {info['儲存']}")
        print()


def test_performance_optimization():
    """展示性能優化建議"""
    print_header("性能優化建議")
    
    optimizations = {
        "PostgreSQL": [
            "✓ 在高頻查詢欄位建立索引 (symbol, timestamp)",
            "✓ 使用 JSONB 儲存靈活結構（indicators, features）",
            "✓ Partitioning: 按月分割 trades 表",
            "✓ Connection Pool: 使用 pgBouncer",
            "✓ 定期 VACUUM 清理碎片"
        ],
        "InfluxDB": [
            "✓ 使用 Tag 進行快速過濾",
            "✓ 批次寫入（每 1000 點或 1 秒）",
            "✓ 連續查詢自動降採樣",
            "✓ 適當設置保留策略避免空間爆炸",
            "✓ 使用 Flux 查詢語言優化複雜查詢"
        ],
        "Redis": [
            "✓ 設置合理的 TTL 避免內存溢出",
            "✓ 使用 Pipeline 批次操作",
            "✓ 選擇合適的淘汰策略 (allkeys-lru)",
            "✓ 避免儲存大型物件（>1MB）",
            "✓ 使用 Hash 代替多個 String key"
        ]
    }
    
    print()
    for db, tips in optimizations.items():
        print(f"📊 {db}")
        print("-" * 70)
        for tip in tips:
            print(f"   {tip}")
        print()


def main():
    """主函數"""
    print("\n" + "🎯" * 35)
    print(" " * 20 + "Task 1.4 資料庫 Schema 設計測試")
    print("🎯" * 35)
    
    # 1. PostgreSQL Schema
    test_postgresql_schema()
    
    # 2. InfluxDB Schema
    test_influxdb_schema()
    
    # 3. Redis Schema
    test_redis_schema()
    
    # 4. 資料流設計
    test_data_flow()
    
    # 5. 備份策略
    test_backup_strategy()
    
    # 6. 性能優化
    test_performance_optimization()
    
    # 總結
    print_header("Task 1.4 完成總結")
    print("""
✅ PostgreSQL Schema: 6 張表設計完成
   • trades, signal_annotations, training_data
   • model_metadata, virtual_exchange_state, system_logs

✅ InfluxDB Schema: 4 個 Buckets + 4 個 Measurements
   • trading_data (30天), trading_data_1y (1年), trading_data_forever (永久)
   • klines, indicators, performance_metrics, orderbook

✅ Redis Schema: 6 種 Key Pattern
   • price (60s), obi (10s), signal (queue), strategy state
   • session (24h), ratelimit (60s)

✅ 資料流設計: 回測、實盤、AI 訓練三種模式

✅ 備份策略: PostgreSQL 每日、InfluxDB 降採樣、Redis RDB+AOF

✅ 性能優化: 索引、批次寫入、連接池、TTL 管理

📄 文檔位置: docs/DATABASE_SCHEMA.md (600+ 行)
🔧 初始化腳本:
   • scripts/init_postgres.sql
   • scripts/init_influxdb.py
   • scripts/init_redis.py

📊 進度: 4/67 任務 (6.0%)
🎯 下一步: Task 1.5 TA-Lib 指標庫
    """)
    
    print("=" * 70)
    print(" " * 20 + "✨ Task 1.4 測試完成 ✨")
    print("=" * 70 + "\n")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  使用者中斷")
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
