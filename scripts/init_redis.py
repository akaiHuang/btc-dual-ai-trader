"""
Redis 初始化腳本
配置 Redis 資料結構和測試連接
"""

import redis
import json
from datetime import datetime, timedelta
from typing import Dict, Any


class RedisInitializer:
    """Redis 初始化器"""
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str = None
    ):
        """
        初始化
        
        Args:
            host: Redis 主機
            port: Redis 端口
            db: 資料庫編號
            password: 密碼
        """
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.client = None
    
    def connect(self) -> bool:
        """連接到 Redis"""
        try:
            self.client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                password=self.password,
                decode_responses=True
            )
            
            # 測試連接
            self.client.ping()
            print(f"✅ 已連接到 Redis: {self.host}:{self.port} (DB: {self.db})")
            return True
            
        except Exception as e:
            print(f"❌ 連接失敗: {e}")
            return False
    
    def setup_key_patterns(self):
        """設置 Key 命名規範的文檔"""
        print("\n📝 Redis Key 命名規範:")
        
        patterns = {
            "實時價格": "price:{symbol}",
            "OBI 快取": "obi:{symbol}",
            "交易信號": "signal:{strategy}:{symbol}",
            "策略狀態": "strategy:{strategy}:state",
            "用戶會話": "session:{user_id}",
            "限流計數": "ratelimit:{endpoint}:{ip}",
            "訂單簿快照": "orderbook:{symbol}:snapshot",
            "性能指標": "metrics:{type}:{timestamp}",
        }
        
        for desc, pattern in patterns.items():
            print(f"   • {desc}: {pattern}")
    
    def configure_memory_policy(self):
        """配置內存策略"""
        print("\n⚙️  配置內存策略...")
        
        try:
            # 設置最大內存（256MB）
            self.client.config_set('maxmemory', '256mb')
            
            # 設置淘汰策略（LRU - 移除最近最少使用的 key）
            self.client.config_set('maxmemory-policy', 'allkeys-lru')
            
            print("   ✅ 已設置:")
            print("      • maxmemory: 256mb")
            print("      • maxmemory-policy: allkeys-lru")
            
        except redis.ResponseError as e:
            print(f"   ⚠️  無法設置配置（可能需要管理員權限）: {e}")
            print("      建議在 redis.conf 中手動設置:")
            print("      maxmemory 256mb")
            print("      maxmemory-policy allkeys-lru")
    
    def create_sample_data(self):
        """創建測試資料"""
        print("\n🧪 創建測試資料...")
        
        # 1. 實時價格
        price_data = {
            "symbol": "BTCUSDT",
            "price": 43280.50,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.client.setex(
            "price:BTCUSDT",
            60,  # 60秒過期
            json.dumps(price_data)
        )
        print("   ✅ 已設置實時價格: price:BTCUSDT")
        
        # 2. OBI 指標
        obi_data = {
            "symbol": "BTCUSDT",
            "obi": 0.35,
            "bid_volume": 1250.5,
            "ask_volume": 850.3,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.client.setex(
            "obi:BTCUSDT",
            10,  # 10秒過期
            json.dumps(obi_data)
        )
        print("   ✅ 已設置 OBI: obi:BTCUSDT")
        
        # 3. 交易信號佇列
        signal_data = {
            "strategy": "obi_rsi_combined",
            "symbol": "BTCUSDT",
            "action": "BUY",
            "price": 43280.50,
            "confidence": 0.85,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.client.lpush(
            "signal:obi_rsi_combined:BTCUSDT",
            json.dumps(signal_data)
        )
        print("   ✅ 已推送信號: signal:obi_rsi_combined:BTCUSDT")
        
        # 4. 策略狀態
        strategy_state = {
            "strategy": "obi_rsi_combined",
            "status": "RUNNING",
            "position": None,
            "last_signal": "BUY",
            "updated_at": datetime.utcnow().isoformat()
        }
        self.client.set(
            "strategy:obi_rsi_combined:state",
            json.dumps(strategy_state)
        )
        print("   ✅ 已設置策略狀態: strategy:obi_rsi_combined:state")
        
        # 5. 限流計數（示例：API 每分鐘 60 次）
        self.client.setex(
            "ratelimit:api:127.0.0.1",
            60,  # 60秒過期
            1
        )
        print("   ✅ 已設置限流: ratelimit:api:127.0.0.1")
        
        # 6. 訂單簿快照
        orderbook_data = {
            "symbol": "BTCUSDT",
            "bids": [[43280.0, 5.5], [43279.0, 3.2]],
            "asks": [[43281.0, 4.1], [43282.0, 6.8]],
            "timestamp": datetime.utcnow().isoformat()
        }
        self.client.setex(
            "orderbook:BTCUSDT:snapshot",
            5,  # 5秒過期
            json.dumps(orderbook_data)
        )
        print("   ✅ 已設置訂單簿: orderbook:BTCUSDT:snapshot")
    
    def verify_setup(self) -> bool:
        """驗證設置"""
        print("\n✅ 驗證設置...")
        
        try:
            # 1. 檢查實時價格
            price = self.client.get("price:BTCUSDT")
            if price:
                data = json.loads(price)
                print(f"   ✓ 實時價格: {data['price']}")
            else:
                print("   ⚠️  找不到實時價格")
                return False
            
            # 2. 檢查 OBI
            obi = self.client.get("obi:BTCUSDT")
            if obi:
                data = json.loads(obi)
                print(f"   ✓ OBI: {data['obi']}")
            else:
                print("   ⚠️  找不到 OBI")
                return False
            
            # 3. 檢查信號佇列長度
            signal_count = self.client.llen("signal:obi_rsi_combined:BTCUSDT")
            print(f"   ✓ 信號佇列長度: {signal_count}")
            
            # 4. 檢查策略狀態
            state = self.client.get("strategy:obi_rsi_combined:state")
            if state:
                data = json.loads(state)
                print(f"   ✓ 策略狀態: {data['status']}")
            else:
                print("   ⚠️  找不到策略狀態")
                return False
            
            # 5. 檢查總 key 數量
            total_keys = len(self.client.keys("*"))
            print(f"   ✓ 總 key 數量: {total_keys}")
            
            print("\n   ✅ 所有測試通過！")
            return True
            
        except Exception as e:
            print(f"   ❌ 驗證失敗: {e}")
            return False
    
    def get_info(self) -> Dict[str, Any]:
        """獲取 Redis 信息"""
        print("\n📊 Redis 服務器信息:")
        
        try:
            info = self.client.info()
            
            print(f"   • Redis 版本: {info.get('redis_version', 'N/A')}")
            print(f"   • 運行模式: {info.get('redis_mode', 'N/A')}")
            print(f"   • 已用內存: {info.get('used_memory_human', 'N/A')}")
            print(f"   • 總 keys: {info.get('db0', {}).get('keys', 0)}")
            print(f"   • 連接數: {info.get('connected_clients', 0)}")
            
            return info
            
        except Exception as e:
            print(f"   ❌ 無法獲取信息: {e}")
            return {}
    
    def cleanup(self):
        """清理測試資料"""
        print("\n🧹 清理測試資料...")
        
        try:
            # 刪除所有測試 key
            test_keys = [
                "price:BTCUSDT",
                "obi:BTCUSDT",
                "signal:obi_rsi_combined:BTCUSDT",
                "strategy:obi_rsi_combined:state",
                "ratelimit:api:127.0.0.1",
                "orderbook:BTCUSDT:snapshot"
            ]
            
            deleted = 0
            for key in test_keys:
                if self.client.delete(key):
                    deleted += 1
            
            print(f"   ✅ 已刪除 {deleted} 個測試 key")
            
        except Exception as e:
            print(f"   ❌ 清理失敗: {e}")
    
    def close(self):
        """關閉連接"""
        if self.client:
            self.client.close()
            print("\n👋 Redis 連接已關閉")


def main():
    """主函數"""
    print("🚀 Redis 初始化開始\n")
    print("=" * 60)
    
    # 初始化
    initializer = RedisInitializer(
        host="localhost",
        port=6379,
        db=0
    )
    
    # 連接
    if not initializer.connect():
        print("\n❌ 無法連接到 Redis，請確認服務已啟動")
        print("\n💡 啟動 Redis:")
        print("   macOS: brew services start redis")
        print("   Linux: sudo systemctl start redis")
        print("   Docker: docker run -d -p 6379:6379 redis:latest")
        return
    
    try:
        # 顯示 Key 命名規範
        initializer.setup_key_patterns()
        
        # 配置內存策略
        initializer.configure_memory_policy()
        
        # 創建測試資料
        initializer.create_sample_data()
        
        # 驗證設置
        if initializer.verify_setup():
            # 獲取服務器信息
            initializer.get_info()
            
            print("\n" + "=" * 60)
            print("✅ Redis 初始化完成！")
            print("=" * 60)
            
            print("\n📊 已創建的資料結構:")
            print("   • 實時價格快取 (60s TTL)")
            print("   • OBI 指標快取 (10s TTL)")
            print("   • 交易信號佇列")
            print("   • 策略狀態")
            print("   • API 限流計數 (60s TTL)")
            print("   • 訂單簿快照 (5s TTL)")
            
            print("\n💡 下一步:")
            print("   1. 使用 src/database/redis_client.py 進行操作")
            print("   2. 配置 redis.conf 中的持久化策略 (RDB/AOF)")
            print("   3. 查看文檔: docs/DATABASE_SCHEMA.md")
            
            # 選項：清理測試資料
            response = input("\n❓ 是否清理測試資料？(y/N): ").strip().lower()
            if response == 'y':
                initializer.cleanup()
        
    except KeyboardInterrupt:
        print("\n\n⚠️  使用者中斷")
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
    finally:
        initializer.close()


if __name__ == '__main__':
    main()
