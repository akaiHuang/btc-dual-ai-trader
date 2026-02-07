"""
InfluxDB 初始化腳本
創建資料庫、保留策略和連續查詢
"""

from influxdb_client import InfluxDBClient, BucketRetentionRules
from influxdb_client.client.write_api import SYNCHRONOUS
import os


class InfluxDBInitializer:
    """InfluxDB 初始化器"""
    
    def __init__(
        self,
        url: str = "http://localhost:8086",
        token: str = None,
        org: str = "btc-trading"
    ):
        """
        初始化
        
        Args:
            url: InfluxDB URL
            token: 認證 token
            org: 組織名稱
        """
        self.url = url
        self.token = token or os.getenv("INFLUXDB_TOKEN")
        self.org = org
        self.client = None
    
    def connect(self):
        """連接到 InfluxDB"""
        try:
            self.client = InfluxDBClient(
                url=self.url,
                token=self.token,
                org=self.org
            )
            print(f"✅ 已連接到 InfluxDB: {self.url}")
            return True
        except Exception as e:
            print(f"❌ 連接失敗: {e}")
            return False
    
    def create_buckets(self):
        """創建 buckets（資料庫）"""
        buckets_api = self.client.buckets_api()
        
        # 定義 buckets 和保留策略
        buckets_config = [
            {
                "name": "trading_data",
                "description": "交易資料（K線、指標）",
                "retention": 30 * 24 * 3600,  # 30 天（秒）
            },
            {
                "name": "trading_data_1y",
                "description": "長期交易資料",
                "retention": 365 * 24 * 3600,  # 1 年
            },
            {
                "name": "trading_data_forever",
                "description": "永久保存（重要資料）",
                "retention": 0,  # 永久
            },
            {
                "name": "performance_metrics",
                "description": "性能指標",
                "retention": 90 * 24 * 3600,  # 90 天
            },
        ]
        
        print("\n📦 創建 Buckets...")
        
        for config in buckets_config:
            try:
                # 檢查是否已存在
                existing = buckets_api.find_bucket_by_name(config["name"])
                
                if existing:
                    print(f"   ✓ Bucket '{config['name']}' 已存在")
                    continue
                
                # 創建新 bucket
                retention_rules = BucketRetentionRules(
                    type="expire",
                    every_seconds=config["retention"]
                ) if config["retention"] > 0 else None
                
                bucket = buckets_api.create_bucket(
                    bucket_name=config["name"],
                    description=config["description"],
                    retention_rules=retention_rules,
                    org=self.org
                )
                
                retention_desc = f"{config['retention'] // (24*3600)} 天" if config['retention'] > 0 else "永久"
                print(f"   ✅ 已創建 Bucket '{config['name']}' (保留: {retention_desc})")
                
            except Exception as e:
                print(f"   ❌ 創建 Bucket '{config['name']}' 失敗: {e}")
    
    def setup_continuous_queries(self):
        """設置連續查詢（降採樣）"""
        print("\n📊 設置連續查詢...")
        
        # InfluxDB 2.x 使用 Tasks 代替 Continuous Queries
        tasks_api = self.client.tasks_api()
        
        # 定義任務
        tasks_config = [
            {
                "name": "downsample_1h_to_1d",
                "flux": """
                    option task = {name: "downsample_1h_to_1d", every: 1h}
                    
                    from(bucket: "trading_data")
                        |> range(start: -2d)
                        |> filter(fn: (r) => r["_measurement"] == "klines")
                        |> filter(fn: (r) => r["interval"] == "1h")
                        |> aggregateWindow(every: 1d, fn: mean, createEmpty: false)
                        |> to(bucket: "trading_data_1y", org: "btc-trading")
                """,
                "description": "每小時將 1h K線降採樣為 1d"
            },
        ]
        
        for config in tasks_config:
            try:
                # 檢查是否已存在
                existing_tasks = tasks_api.find_tasks(name=config["name"])
                if existing_tasks:
                    print(f"   ✓ Task '{config['name']}' 已存在")
                    continue
                
                # 創建任務
                task = tasks_api.create_task_every(
                    name=config["name"],
                    flux=config["flux"],
                    every="1h",
                    organization=self.org
                )
                
                print(f"   ✅ 已創建 Task '{config['name']}'")
                
            except Exception as e:
                print(f"   ⚠️  創建 Task '{config['name']}' 失敗: {e}")
    
    def create_sample_data(self):
        """寫入測試資料"""
        print("\n🧪 寫入測試資料...")
        
        write_api = self.client.write_api(write_options=SYNCHRONOUS)
        
        from influxdb_client import Point
        from datetime import datetime
        
        # 創建測試資料點
        point = Point("klines") \
            .tag("symbol", "BTCUSDT") \
            .tag("interval", "1m") \
            .tag("source", "test") \
            .field("open", 43250.0) \
            .field("high", 43300.0) \
            .field("low", 43200.0) \
            .field("close", 43280.0) \
            .field("volume", 150.25) \
            .time(datetime.utcnow())
        
        try:
            write_api.write(bucket="trading_data", record=point)
            print("   ✅ 測試資料已寫入")
        except Exception as e:
            print(f"   ❌ 寫入失敗: {e}")
    
    def verify_setup(self):
        """驗證設置"""
        print("\n✅ 驗證設置...")
        
        try:
            # 查詢測試資料
            query_api = self.client.query_api()
            
            query = '''
                from(bucket: "trading_data")
                    |> range(start: -1h)
                    |> filter(fn: (r) => r["_measurement"] == "klines")
                    |> limit(n: 1)
            '''
            
            result = query_api.query(query)
            
            if result:
                print("   ✅ 查詢測試成功")
                return True
            else:
                print("   ⚠️  沒有找到測試資料")
                return False
                
        except Exception as e:
            print(f"   ❌ 驗證失敗: {e}")
            return False
    
    def close(self):
        """關閉連接"""
        if self.client:
            self.client.close()
            print("\n👋 InfluxDB 連接已關閉")


def main():
    """主函數"""
    print("🚀 InfluxDB 初始化開始\n")
    print("=" * 60)
    
    # 初始化
    initializer = InfluxDBInitializer(
        url="http://localhost:8086",
        org="btc-trading"
    )
    
    # 連接
    if not initializer.connect():
        print("\n❌ 無法連接到 InfluxDB，請確認服務已啟動")
        return
    
    try:
        # 創建 buckets
        initializer.create_buckets()
        
        # 設置連續查詢
        initializer.setup_continuous_queries()
        
        # 寫入測試資料
        initializer.create_sample_data()
        
        # 驗證設置
        initializer.verify_setup()
        
        print("\n" + "=" * 60)
        print("✅ InfluxDB 初始化完成！")
        print("=" * 60)
        
        print("\n📊 已創建的 Buckets:")
        print("   • trading_data (30天)")
        print("   • trading_data_1y (1年)")
        print("   • trading_data_forever (永久)")
        print("   • performance_metrics (90天)")
        
        print("\n💡 下一步:")
        print("   1. 配置環境變數 INFLUXDB_TOKEN")
        print("   2. 使用 src/database/influxdb_client.py 進行資料操作")
        print("   3. 查看文檔: docs/DATABASE_SCHEMA.md")
        
    finally:
        initializer.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  使用者中斷")
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
