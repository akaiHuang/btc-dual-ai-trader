"""
並行測試控制器 - 同時運行多個測試

Purpose:
    同時運行以下測試並保存結果：
    1. 真實 WebSocket 數據收集
    2. Phase C 策略模擬交易（含手續費）
    3. 高頻交易策略對比測試
    
Output:
    - 每個測試運行在獨立終端窗口
    - 結果保存到獨立日誌文件
    - 可以隨時查看各個測試進度
"""

import subprocess
import time
from datetime import datetime
import os


class ParallelTestController:
    """並行測試控制器"""
    
    def __init__(self):
        self.test_dir = "data/parallel_tests"
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 創建測試目錄
        os.makedirs(self.test_dir, exist_ok=True)
        os.makedirs(f"{self.test_dir}/logs", exist_ok=True)
        os.makedirs(f"{self.test_dir}/snapshots", exist_ok=True)
        
    def start_all_tests(self, duration_hours: int = 24):
        """
        啟動所有測試
        
        Args:
            duration_hours: 測試運行時長（小時）
        """
        print("="*70)
        print("🚀 Phase C 並行測試啟動")
        print("="*70)
        print()
        print(f"測試時長: {duration_hours} 小時")
        print(f"開始時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        # Test 1: 真實數據收集
        print("📥 Test 1: 真實 WebSocket 數據收集")
        print(f"   輸出: {self.test_dir}/logs/data_collection_{self.timestamp}.log")
        self._start_data_collection(duration_hours)
        print("   ✅ 已啟動")
        print()
        
        time.sleep(2)
        
        # Test 2: Phase C 模擬交易
        print("💹 Test 2: Phase C 策略模擬交易（含手續費）")
        print(f"   輸出: {self.test_dir}/logs/phase_c_trading_{self.timestamp}.log")
        self._start_phase_c_trading(duration_hours)
        print("   ✅ 已啟動")
        print()
        
        time.sleep(2)
        
        # Test 3: 高頻交易對比
        print("⚡ Test 3: 高頻交易策略對比測試")
        print(f"   輸出: {self.test_dir}/logs/hft_comparison_{self.timestamp}.log")
        self._start_hft_comparison(duration_hours)
        print("   ✅ 已啟動")
        print()
        
        print("="*70)
        print("✅ 所有測試已啟動")
        print("="*70)
        print()
        print("📊 查看測試進度:")
        print(f"   tail -f {self.test_dir}/logs/data_collection_{self.timestamp}.log")
        print(f"   tail -f {self.test_dir}/logs/phase_c_trading_{self.timestamp}.log")
        print(f"   tail -f {self.test_dir}/logs/hft_comparison_{self.timestamp}.log")
        print()
        print("🛑 停止所有測試:")
        print(f"   ps aux | grep 'python.*parallel_test' | awk '{{print $2}}' | xargs kill")
        print()
    
    def _start_data_collection(self, duration_hours: int):
        """啟動數據收集"""
        log_file = f"{self.test_dir}/logs/data_collection_{self.timestamp}.log"
        
        # 創建修改版的收集腳本（保存到指定目錄）
        script_content = f"""
import asyncio
from binance import AsyncClient, BinanceSocketManager
import pandas as pd
from datetime import datetime
import json
import time

SAVE_DIR = "{self.test_dir}/snapshots"
DURATION_HOURS = {duration_hours}

class DataCollector:
    def __init__(self):
        self.orderbook_buffer = []
        self.trade_buffer = []
        self.buffer_size = 1000
        self.start_time = time.time()
        
    def process_orderbook(self, msg):
        if msg['e'] == 'error':
            return
        
        record = {{
            'timestamp': datetime.now().timestamp() * 1000,
            'event_time': msg.get('E', 0),
            'bids': json.dumps(msg['bids'][:20]),
            'asks': json.dumps(msg['asks'][:20])
        }}
        
        self.orderbook_buffer.append(record)
        
        if len(self.orderbook_buffer) >= self.buffer_size:
            self.flush_orderbook()
    
    def process_trade(self, msg):
        if msg['e'] == 'error':
            return
        
        record = {{
            'timestamp': datetime.now().timestamp() * 1000,
            'event_time': msg.get('E', 0),
            'trade_id': msg.get('a', 0),
            'price': float(msg.get('p', 0)),
            'quantity': float(msg.get('q', 0)),
            'is_buyer_maker': msg.get('m', False)
        }}
        
        self.trade_buffer.append(record)
        
        if len(self.trade_buffer) >= self.buffer_size:
            self.flush_trades()
    
    def flush_orderbook(self):
        if not self.orderbook_buffer:
            return
        
        df = pd.DataFrame(self.orderbook_buffer)
        date_str = datetime.now().strftime('%Y%m%d')
        filename = f"{{SAVE_DIR}}/BTCUSDT_orderbook_{{date_str}}.parquet"
        
        if pd.io.common.file_exists(filename):
            existing = pd.read_parquet(filename)
            df = pd.concat([existing, df], ignore_index=True)
        
        df.to_parquet(filename, compression='snappy', index=False)
        print(f"💾 Saved {{len(self.orderbook_buffer)}} orderbook records")
        self.orderbook_buffer = []
    
    def flush_trades(self):
        if not self.trade_buffer:
            return
        
        df = pd.DataFrame(self.trade_buffer)
        date_str = datetime.now().strftime('%Y%m%d')
        filename = f"{{SAVE_DIR}}/BTCUSDT_trades_{{date_str}}.parquet"
        
        if pd.io.common.file_exists(filename):
            existing = pd.read_parquet(filename)
            df = pd.concat([existing, df], ignore_index=True)
        
        df.to_parquet(filename, compression='snappy', index=False)
        print(f"💾 Saved {{len(self.trade_buffer)}} trade records")
        self.trade_buffer = []
    
    async def start(self):
        client = await AsyncClient.create()
        bsm = BinanceSocketManager(client)
        
        depth_socket = bsm.depth_socket('BTCUSDT')
        trade_socket = bsm.aggtrade_socket('BTCUSDT')
        
        print(f"🔌 開始收集數據: {{DURATION_HOURS}} 小時")
        print(f"📂 保存目錄: {{SAVE_DIR}}")
        
        async with depth_socket as ds, trade_socket as ts:
            while time.time() - self.start_time < DURATION_HOURS * 3600:
                depth_msg = await ds.recv()
                self.process_orderbook(depth_msg)
                
                trade_msg = await ts.recv()
                self.process_trade(trade_msg)
                
                # 每 5 分鐘報告一次
                elapsed = time.time() - self.start_time
                if int(elapsed) % 300 == 0:
                    print(f"⏱️  已運行: {{elapsed/3600:.1f}}h | 訂單簿: {{len(self.orderbook_buffer)}} | 交易: {{len(self.trade_buffer)}}")
        
        self.flush_orderbook()
        self.flush_trades()
        
        await client.close_connection()
        print("✅ 數據收集完成")

if __name__ == "__main__":
    collector = DataCollector()
    asyncio.run(collector.start())
"""
        
        # 寫入臨時腳本
        temp_script = f"{self.test_dir}/temp_data_collection.py"
        with open(temp_script, 'w') as f:
            f.write(script_content)
        
        # 後台運行
        subprocess.Popen(
            [".venv/bin/python", temp_script],
            stdout=open(log_file, 'w'),
            stderr=subprocess.STDOUT
        )
    
    def _start_phase_c_trading(self, duration_hours: int):
        """啟動 Phase C 模擬交易"""
        log_file = f"{self.test_dir}/logs/phase_c_trading_{self.timestamp}.log"
        
        # 直接使用現有的 real_trading_simulation.py
        subprocess.Popen(
            [".venv/bin/python", "scripts/real_trading_simulation.py"],
            stdout=open(log_file, 'w'),
            stderr=subprocess.STDOUT
        )
    
    def _start_hft_comparison(self, duration_hours: int):
        """啟動高頻交易對比測試"""
        log_file = f"{self.test_dir}/logs/hft_comparison_{self.timestamp}.log"
        
        # 創建高頻交易對比腳本
        script_content = f"""
import asyncio
from binance import AsyncClient, BinanceSocketManager
from datetime import datetime
import time
import json

DURATION_HOURS = {duration_hours}

class HFTComparison:
    def __init__(self):
        self.trades_executed = 0
        self.phase_c_trades = 0
        self.hft_trades = 0
        self.start_time = time.time()
        
        # 簡單 HFT 策略: 快速進出
        self.last_trade_time = 0
        self.min_trade_interval = 60  # 最短 60 秒
        
    async def test_hft_strategy(self):
        '''簡單的高頻策略: 價格突破立即交易'''
        client = await AsyncClient.create()
        bsm = BinanceSocketManager(client)
        
        trade_socket = bsm.aggtrade_socket('BTCUSDT')
        
        print("⚡ 高頻交易策略測試開始")
        print(f"   策略: 價格波動 > 0.02% 立即交易")
        print(f"   手續費: Taker 0.05%")
        print()
        
        prices = []
        
        async with trade_socket as ts:
            while time.time() - self.start_time < DURATION_HOURS * 3600:
                msg = await ts.recv()
                
                if msg['e'] == 'error':
                    continue
                
                price = float(msg['p'])
                prices.append(price)
                
                if len(prices) > 20:
                    prices = prices[-20:]
                    
                    # 簡單策略: 價格偏離均值超過 0.02%
                    avg_price = sum(prices) / len(prices)
                    deviation = abs(price - avg_price) / avg_price
                    
                    current_time = time.time()
                    if deviation > 0.0002 and (current_time - self.last_trade_time) > self.min_trade_interval:
                        self.hft_trades += 1
                        self.last_trade_time = current_time
                        
                        pnl = deviation * 100  # 理論收益
                        fee = 0.05 * 2  # 開倉 + 平倉
                        net_pnl = pnl - fee
                        
                        print(f"[{{datetime.now().strftime('%H:%M:%S')}}] HFT 交易 #{{self.hft_trades}}")
                        print(f"   價格: ${{price:.2f}} | 偏離: {{deviation*100:.3f}}%")
                        print(f"   理論 PnL: {{net_pnl:.3f}}% (收益 {{pnl:.3f}}% - 手續費 {{fee:.2f}}%)")
                        print()
                
                # 每 10 分鐘報告
                if int(time.time() - self.start_time) % 600 == 0:
                    elapsed = (time.time() - self.start_time) / 3600
                    print(f"⏱️  已運行: {{elapsed:.1f}}h | HFT 交易: {{self.hft_trades}}")
                    print()
        
        await client.close_connection()
        
        print("="*70)
        print("📊 高頻交易測試結果")
        print("="*70)
        print(f"總交易數: {{self.hft_trades}}")
        print(f"平均頻率: {{self.hft_trades / DURATION_HOURS:.1f}} 筆/小時")
        print()

if __name__ == "__main__":
    hft = HFTComparison()
    asyncio.run(hft.test_hft_strategy())
"""
        
        temp_script = f"{self.test_dir}/temp_hft_comparison.py"
        with open(temp_script, 'w') as f:
            f.write(script_content)
        
        subprocess.Popen(
            [".venv/bin/python", temp_script],
            stdout=open(log_file, 'w'),
            stderr=subprocess.STDOUT
        )


def main():
    import sys
    
    duration = 24
    if len(sys.argv) > 1:
        duration = int(sys.argv[1])
    
    controller = ParallelTestController()
    controller.start_all_tests(duration_hours=duration)


if __name__ == "__main__":
    main()
