#!/usr/bin/env python3
"""
Task 1.6.1.1: 延遲測量工具 (Latency Monitor)

測量目標:
1. WebSocket 行情延遲 (t2 - t1)
2. 下單 ACK 延遲 (order_time → ack_time)
3. 統計分佈 (mean, std, p50, p95, p99)
4. 高峰/離峰時段對比

用途:
- 評估家用網路是否適合 HFT
- 決定策略時間框架 (100ms/1s/5m)
- VPS 成本效益分析依據

作者: AI Trading System
日期: 2025-11-10
"""

import asyncio
import time
import json
import statistics
from datetime import datetime
from typing import List, Dict, Any
import websockets
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException

# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════

BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms"
BINANCE_API_KEY = ""  # 如需測試下單延遲需填寫
BINANCE_API_SECRET = ""

# 測試參數
WS_SAMPLES = 30  # WebSocket 延遲採樣次數（降低以加快測試）
ORDER_SAMPLES = 10  # 下單延遲採樣次數（會消耗 API 請求額度）
TEST_INTERVAL = 0.05  # 採樣間隔（秒）


# ═══════════════════════════════════════════════════════════════════
# 延遲監控類
# ═══════════════════════════════════════════════════════════════════

class LatencyMonitor:
    """實時延遲監控與分析"""
    
    def __init__(self):
        self.ws_latencies = []
        self.order_latencies = []
        self.client = None
        
        if BINANCE_API_KEY and BINANCE_API_SECRET:
            self.client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    
    # ───────────────────────────────────────────────────────────────
    # WebSocket 延遲測量
    # ───────────────────────────────────────────────────────────────
    
    async def measure_websocket_latency_single(self) -> float:
        """
        測量單次 WebSocket 延遲
        
        計算方式:
        t1 = 發送訂閱請求時間
        t2 = 收到第一個行情包時間
        latency = t2 - t1
        
        返回:
            延遲（毫秒）
        """
        try:
            # 訂閱消息
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": ["btcusdt@depth20@100ms"],
                "id": 1
            }
            
            # 記錄開始時間（納秒精度）
            start_ns = time.perf_counter_ns()
            
            async with websockets.connect(BINANCE_WS_URL) as ws:
                # 發送訂閱
                await ws.send(json.dumps(subscribe_msg))
                
                # 等待響應（訂閱確認）
                _ = await ws.recv()
                
                # 等待第一個實際數據包
                data = await ws.recv()
                
                # 記錄結束時間
                end_ns = time.perf_counter_ns()
                
                # 計算延遲（轉換為毫秒）
                latency_ms = (end_ns - start_ns) / 1_000_000
                
                return latency_ms
                
        except Exception as e:
            print(f"❌ WebSocket 測量失敗: {e}")
            return -1
    
    async def measure_websocket_latency_batch(self, samples: int = WS_SAMPLES) -> List[float]:
        """
        批次測量 WebSocket 延遲
        
        參數:
            samples: 採樣次數
        
        返回:
            延遲列表（毫秒）
        """
        print(f"\n📊 開始 WebSocket 延遲測量...")
        print(f"   採樣次數: {samples}")
        print(f"   目標: Binance BTCUSDT Depth20@100ms")
        print(f"   進度: ", end="", flush=True)
        
        latencies = []
        
        for i in range(samples):
            latency = await self.measure_websocket_latency_single()
            
            if latency > 0:
                latencies.append(latency)
                self.ws_latencies.append(latency)
            
            # 進度條
            if (i + 1) % 10 == 0:
                print(f"{i+1}...", end="", flush=True)
            
            # 間隔
            await asyncio.sleep(TEST_INTERVAL)
        
        print(" ✅ 完成!\n")
        return latencies
    
    # ───────────────────────────────────────────────────────────────
    # 下單延遲測量
    # ───────────────────────────────────────────────────────────────
    
    def measure_order_latency_single(self) -> Dict[str, float]:
        """
        測量單次下單延遲（使用測試訂單，不會實際成交）
        
        測量指標:
        1. order_to_ack: 本地發送 → 收到 API 響應
        2. exchange_latency: API 響應中的 transactTime
        
        返回:
            {'order_to_ack': ms, 'exchange_latency': ms}
        """
        if not self.client:
            return {'order_to_ack': -1, 'exchange_latency': -1}
        
        try:
            # 獲取當前價格
            ticker = self.client.get_symbol_ticker(symbol='BTCUSDT')
            current_price = float(ticker['price'])
            
            # 設置一個不會成交的價格（低於市價 10%）
            test_price = round(current_price * 0.9, 2)
            
            # 記錄發送時間
            start_ns = time.perf_counter_ns()
            
            # 發送測試訂單
            response = self.client.create_test_order(
                symbol='BTCUSDT',
                side='BUY',
                type='LIMIT',
                timeInForce='GTC',
                quantity=0.001,  # 最小數量
                price=test_price
            )
            
            # 記錄收到響應時間
            end_ns = time.perf_counter_ns()
            
            # 計算延遲
            order_to_ack = (end_ns - start_ns) / 1_000_000
            
            # 注意: test_order 不會返回 transactTime，所以這裡無法測量 exchange_latency
            return {
                'order_to_ack': order_to_ack,
                'exchange_latency': -1  # 測試訂單無此數據
            }
            
        except BinanceAPIException as e:
            print(f"❌ 下單測試失敗: {e}")
            return {'order_to_ack': -1, 'exchange_latency': -1}
    
    def measure_order_latency_batch(self, samples: int = ORDER_SAMPLES) -> List[Dict[str, float]]:
        """
        批次測量下單延遲
        
        參數:
            samples: 採樣次數
        
        返回:
            延遲字典列表
        """
        if not self.client:
            print("\n⚠️  未配置 API Key，跳過下單延遲測量")
            return []
        
        print(f"\n📊 開始下單延遲測量...")
        print(f"   採樣次數: {samples}")
        print(f"   方法: 測試訂單（不會實際成交）")
        print(f"   進度: ", end="", flush=True)
        
        latencies = []
        
        for i in range(samples):
            latency = self.measure_order_latency_single()
            
            if latency['order_to_ack'] > 0:
                latencies.append(latency)
                self.order_latencies.append(latency['order_to_ack'])
            
            # 進度條
            if (i + 1) % 5 == 0:
                print(f"{i+1}...", end="", flush=True)
            
            # 間隔（避免 API 限流）
            time.sleep(TEST_INTERVAL * 2)
        
        print(" ✅ 完成!\n")
        return latencies
    
    # ───────────────────────────────────────────────────────────────
    # 統計分析
    # ───────────────────────────────────────────────────────────────
    
    def analyze_latency_distribution(self, latencies: List[float], name: str = "延遲") -> Dict[str, Any]:
        """
        統計延遲分佈
        
        參數:
            latencies: 延遲列表（毫秒）
            name: 指標名稱
        
        返回:
            統計字典 {mean, std, p50, p95, p99, min, max}
        """
        if not latencies:
            return {}
        
        stats = {
            'name': name,
            'samples': len(latencies),
            'mean': np.mean(latencies),
            'std': np.std(latencies),
            'median': np.median(latencies),
            'p50': np.percentile(latencies, 50),
            'p95': np.percentile(latencies, 95),
            'p99': np.percentile(latencies, 99),
            'min': np.min(latencies),
            'max': np.max(latencies)
        }
        
        return stats
    
    def print_statistics(self, stats: Dict[str, Any]):
        """
        打印統計結果
        
        參數:
            stats: 統計字典
        """
        if not stats:
            return
        
        print(f"\n{'='*60}")
        print(f"📈 {stats['name']} 統計分析")
        print(f"{'='*60}")
        print(f"  樣本數量: {stats['samples']}")
        print(f"  平均延遲: {stats['mean']:.2f} ms")
        print(f"  標準差:   {stats['std']:.2f} ms (抖動)")
        print(f"  中位數:   {stats['median']:.2f} ms")
        print(f"  P50:      {stats['p50']:.2f} ms")
        print(f"  P95:      {stats['p95']:.2f} ms")
        print(f"  P99:      {stats['p99']:.2f} ms")
        print(f"  最小值:   {stats['min']:.2f} ms")
        print(f"  最大值:   {stats['max']:.2f} ms")
        print(f"{'='*60}\n")
    
    def recommend_strategy_timeframe(self, ws_p99: float) -> str:
        """
        根據延遲推薦策略時間框架
        
        參數:
            ws_p99: WebSocket P99 延遲（毫秒）
        
        返回:
            推薦建議字符串
        """
        print(f"\n{'='*60}")
        print(f"💡 策略時間框架建議")
        print(f"{'='*60}")
        print(f"  WebSocket P99 延遲: {ws_p99:.2f} ms")
        print(f"")
        
        if ws_p99 < 50:
            recommendation = "✅ 可嘗試 HFT (100ms-1s)"
            detail = """
  延遲表現: 優秀 (<50ms)
  建議策略: 
    - 超高頻 (100ms-500ms) ✅
    - 需要 VPS colocated 接入
    - 適合純 OBI / Microprice 策略
  注意事項:
    - 仍需測試實際下單延遲
    - 建議租用 near-exchange VPS
            """
        elif ws_p99 < 100:
            recommendation = "⚠️  可嘗試中頻 (1s-5s)"
            detail = """
  延遲表現: 良好 (50-100ms)
  建議策略:
    - 中頻策略 (1-5秒) ✅
    - 可能需要 VPS 優化
    - OBI + 技術指標組合
  注意事項:
    - HFT 有風險，建議先測試
    - 考慮使用 VPS 降低延遲
            """
        elif ws_p99 < 200:
            recommendation = "⚠️  適合 5分鐘級別策略"
            detail = """
  延遲表現: 中等 (100-200ms)
  建議策略:
    - 5分鐘 K線策略 ✅ (推薦)
    - OBI + RSI + MACD 組合
    - 持倉 10-60 分鐘
  注意事項:
    - 不適合 HFT (<1s)
    - 可能不需要 VPS
    - 家用網路可接受
            """
        else:
            recommendation = "❌ 僅適合長線策略 (15分鐘+)"
            detail = """
  延遲表現: 較高 (>200ms)
  建議策略:
    - 15分鐘+ K線策略 ✅
    - 持倉 1小時+ 
    - 中長線趨勢跟隨
  注意事項:
    - 不適合任何高頻策略
    - 建議檢查網路環境
    - 考慮更換網路 ISP 或使用 VPS
            """
        
        print(f"  結論: {recommendation}")
        print(detail)
        print(f"{'='*60}\n")
        
        return recommendation
    
    # ───────────────────────────────────────────────────────────────
    # 報告生成
    # ───────────────────────────────────────────────────────────────
    
    def generate_report(self) -> Dict[str, Any]:
        """
        生成完整測試報告
        
        返回:
            報告字典
        """
        report = {
            'timestamp': datetime.now().isoformat(),
            'websocket': {},
            'order': {},
            'recommendation': ''
        }
        
        # WebSocket 統計
        if self.ws_latencies:
            ws_stats = self.analyze_latency_distribution(
                self.ws_latencies, 
                "WebSocket 行情延遲"
            )
            report['websocket'] = ws_stats
            self.print_statistics(ws_stats)
            
            # 推薦建議
            report['recommendation'] = self.recommend_strategy_timeframe(
                ws_stats['p99']
            )
        
        # 下單統計
        if self.order_latencies:
            order_stats = self.analyze_latency_distribution(
                self.order_latencies,
                "下單 ACK 延遲"
            )
            report['order'] = order_stats
            self.print_statistics(order_stats)
        
        return report
    
    def save_report(self, report: Dict[str, Any], filename: str = "latency_report.json"):
        """
        保存報告到文件
        
        參數:
            report: 報告字典
            filename: 文件名
        """
        import os
        
        # 確保目錄存在
        os.makedirs('data/latency', exist_ok=True)
        
        filepath = f'data/latency/{filename}'
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"💾 報告已保存: {filepath}\n")


# ═══════════════════════════════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════════════════════════════

async def main():
    """主測試流程"""
    
    print("\n" + "="*60)
    print("🚀 Task 1.6.1.1: 延遲測量工具")
    print("="*60)
    print(f"測試時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"測試目標: Binance BTCUSDT")
    print(f"WebSocket 採樣: {WS_SAMPLES} 次")
    print(f"下單測試採樣: {ORDER_SAMPLES} 次")
    print("="*60 + "\n")
    
    monitor = LatencyMonitor()
    
    # 1. 測量 WebSocket 延遲
    ws_latencies = await monitor.measure_websocket_latency_batch(WS_SAMPLES)
    
    # 2. 測量下單延遲（可選）
    if BINANCE_API_KEY and BINANCE_API_SECRET:
        order_latencies = monitor.measure_order_latency_batch(ORDER_SAMPLES)
    
    # 3. 生成報告
    report = monitor.generate_report()
    
    # 4. 保存報告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    monitor.save_report(report, f'latency_report_{timestamp}.json')
    
    print("\n" + "="*60)
    print("✅ 延遲測量完成!")
    print("="*60)
    print("\n下一步:")
    print("  1. 查看報告了解網路延遲狀況")
    print("  2. 根據建議決定策略時間框架")
    print("  3. 如需優化，考慮:")
    print("     - 租用 near-exchange VPS")
    print("     - 更換網路 ISP")
    print("     - 調整策略到更長時間框架")
    print("\n")


if __name__ == "__main__":
    asyncio.run(main())
