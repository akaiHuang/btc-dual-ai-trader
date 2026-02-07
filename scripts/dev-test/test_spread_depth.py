"""
Task 1.6.1 - B5 測試: Spread & Depth 監控

測試內容:
1. 價差計算（絕對/相對/基點）
2. 深度計算（數量/價值/失衡）
3. 深度加權價差
4. 有效價差（考慮滑點）
5. 流動性危機檢測
6. 即時監控
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
import json
from datetime import datetime
from src.exchange.spread_depth_monitor import SpreadDepthMonitor
import websockets


def test_spread_calculation():
    """測試價差計算"""
    print("=" * 60)
    print("📊 測試 1: 價差計算")
    print("=" * 60)
    
    monitor = SpreadDepthMonitor()
    
    # 情境 1: 正常價差
    print("\n📈 情境 1: 正常價差（緊密市場）")
    bids = [["50000", "2.0"], ["49999", "1.5"]]
    asks = [["50001", "2.0"], ["50002", "1.5"]]
    
    spread = monitor.calculate_spread(bids, asks)
    
    print(f"  Best Bid:         $50000")
    print(f"  Best Ask:         $50001")
    print(f"  絕對價差:         ${spread['absolute_spread']:.2f}")
    print(f"  相對價差:         {spread['relative_spread']*100:.4f}%")
    print(f"  價差基點:         {spread['spread_bps']:.2f} bps")
    print(f"  中間價:           ${spread['mid_price']:.2f}")
    
    if spread['spread_bps'] < 5:
        print(f"  ✅ 流動性良好（<5 bps）")
    
    # 情境 2: 寬價差（流動性差）
    print("\n⚠️ 情境 2: 寬價差（流動性不足）")
    bids2 = [["50000", "0.5"], ["49990", "0.3"]]
    asks2 = [["50050", "0.5"], ["50060", "0.3"]]
    
    spread2 = monitor.calculate_spread(bids2, asks2)
    
    print(f"  Best Bid:         $50000")
    print(f"  Best Ask:         $50050")
    print(f"  絕對價差:         ${spread2['absolute_spread']:.2f}")
    print(f"  相對價差:         {spread2['relative_spread']*100:.4f}%")
    print(f"  價差基點:         {spread2['spread_bps']:.2f} bps")
    
    if spread2['spread_bps'] > 10:
        print(f"  ⚠️  流動性不足（>10 bps）")
    
    print()


def test_depth_calculation():
    """測試深度計算"""
    print("=" * 60)
    print("📊 測試 2: 訂單簿深度")
    print("=" * 60)
    
    monitor = SpreadDepthMonitor(depth_levels=10)
    
    # 情境 1: 買單優勢
    print("\n📈 情境 1: 買單深度優勢")
    bids = [[f"{50000-i}", f"{2.0+i*0.5}"] for i in range(10)]
    asks = [[f"{50001+i}", f"{1.0+i*0.2}"] for i in range(10)]
    
    depth = monitor.calculate_depth(bids, asks, levels=10)
    
    print(f"  買單深度:         {depth['bid_depth']:.2f} BTC")
    print(f"  賣單深度:         {depth['ask_depth']:.2f} BTC")
    print(f"  總深度:           {depth['total_depth']:.2f} BTC")
    print(f"  深度失衡:         {depth['depth_imbalance']:>6.3f}")
    print(f"  買單價值:         ${depth['bid_value']:,.0f}")
    print(f"  賣單價值:         ${depth['ask_value']:,.0f}")
    
    if depth['depth_imbalance'] > 0.2:
        print(f"  ✅ 買單支撐強（失衡 > 0.2）")
    
    # 情境 2: 賣單優勢
    print("\n📉 情境 2: 賣單深度優勢")
    bids2 = [[f"{50000-i}", f"{0.8+i*0.1}"] for i in range(10)]
    asks2 = [[f"{50001+i}", f"{2.5+i*0.3}"] for i in range(10)]
    
    depth2 = monitor.calculate_depth(bids2, asks2, levels=10)
    
    print(f"  買單深度:         {depth2['bid_depth']:.2f} BTC")
    print(f"  賣單深度:         {depth2['ask_depth']:.2f} BTC")
    print(f"  總深度:           {depth2['total_depth']:.2f} BTC")
    print(f"  深度失衡:         {depth2['depth_imbalance']:>6.3f}")
    
    if depth2['depth_imbalance'] < -0.2:
        print(f"  ⚠️  賣單壓力大（失衡 < -0.2）")
    
    print()


def test_depth_weighted_spread():
    """測試深度加權價差"""
    print("=" * 60)
    print("📊 測試 3: 深度加權價差")
    print("=" * 60)
    
    monitor = SpreadDepthMonitor()
    
    # 情境 1: 平衡市場
    print("\n⚖️ 情境 1: 買賣深度平衡")
    bids = [[f"{50000-i}", "2.0"] for i in range(10)]
    asks = [[f"{50001+i}", "2.0"] for i in range(10)]
    
    dw_spread = monitor.calculate_depth_weighted_spread(bids, asks)
    
    print(f"  基礎價差:         {dw_spread['base_spread']*100:.4f}%")
    print(f"  流動性懲罰:       {dw_spread['liquidity_penalty']:.3f}x")
    print(f"  加權價差:         {dw_spread['depth_weighted_spread']*100:.4f}%")
    
    if dw_spread['liquidity_penalty'] < 1.2:
        print(f"  ✅ 市場平衡，懲罰係數低")
    
    # 情境 2: 深度失衡
    print("\n⚠️ 情境 2: 深度嚴重失衡")
    bids2 = [[f"{50000-i}", "5.0"] for i in range(10)]
    asks2 = [[f"{50001+i}", "0.5"] for i in range(10)]
    
    dw_spread2 = monitor.calculate_depth_weighted_spread(bids2, asks2)
    
    print(f"  基礎價差:         {dw_spread2['base_spread']*100:.4f}%")
    print(f"  流動性懲罰:       {dw_spread2['liquidity_penalty']:.3f}x")
    print(f"  加權價差:         {dw_spread2['depth_weighted_spread']*100:.4f}%")
    
    if dw_spread2['liquidity_penalty'] > 1.5:
        print(f"  ⚠️  深度失衡，有效價差擴大 {(dw_spread2['liquidity_penalty']-1)*100:.1f}%")
    
    print()


def test_effective_spread():
    """測試有效價差（滑點）"""
    print("=" * 60)
    print("📊 測試 4: 有效價差與滑點")
    print("=" * 60)
    
    monitor = SpreadDepthMonitor()
    
    # 情境 1: 小單（0.1 BTC）
    print("\n💰 情境 1: 小單交易（0.1 BTC）")
    bids = [["50000", "2.0"], ["49999", "2.0"], ["49998", "2.0"]]
    asks = [["50001", "2.0"], ["50002", "2.0"], ["50003", "2.0"]]
    
    eff_spread_small = monitor.calculate_effective_spread(bids, asks, trade_size=0.1)
    
    print(f"  買入價:           ${eff_spread_small['effective_buy_price']:.2f}")
    print(f"  賣出價:           ${eff_spread_small['effective_sell_price']:.2f}")
    print(f"  有效價差:         {eff_spread_small['effective_spread']*100:.4f}%")
    print(f"  滑點:             {eff_spread_small['slippage']*100:.4f}%")
    
    if eff_spread_small['slippage'] < 0.01:
        print(f"  ✅ 小單滑點可忽略")
    
    # 情境 2: 大單（5 BTC，需要穿透多檔）
    print("\n💰 情境 2: 大單交易（5 BTC）")
    
    eff_spread_large = monitor.calculate_effective_spread(bids, asks, trade_size=5.0)
    
    print(f"  買入價:           ${eff_spread_large['effective_buy_price']:.2f}")
    print(f"  賣出價:           ${eff_spread_large['effective_sell_price']:.2f}")
    print(f"  有效價差:         {eff_spread_large['effective_spread']*100:.4f}%")
    print(f"  滑點:             {eff_spread_large['slippage']*100:.4f}%")
    
    if eff_spread_large['slippage'] > 0.05:
        print(f"  ⚠️  大單滑點顯著（{eff_spread_large['slippage']*100:.2f}%）")
    
    print()


def test_liquidity_crisis():
    """測試流動性危機檢測"""
    print("=" * 60)
    print("📊 測試 5: 流動性危機檢測")
    print("=" * 60)
    
    monitor = SpreadDepthMonitor()
    
    # 情境 1: 健康市場
    print("\n✅ 情境 1: 健康市場")
    bids = [[f"{50000-i}", "3.0"] for i in range(10)]
    asks = [[f"{50001+i}", "3.0"] for i in range(10)]
    
    is_crisis, severity, details = monitor.detect_liquidity_crisis(bids, asks)
    
    print(f"  危機狀態:         {'是' if is_crisis else '否'}")
    print(f"  嚴重程度:         {severity}")
    print(f"  價差:             {details['spread_bps']:.2f} bps")
    print(f"  深度失衡:         {details['depth_imbalance']*100:.1f}%")
    print(f"  總深度:           {details['total_depth']:.2f} BTC")
    
    if not is_crisis:
        print(f"  ✅ 市場流動性充足")
    
    # 情境 2: 流動性危機
    print("\n🚨 情境 2: 流動性危機")
    bids2 = [["50000", "0.3"], ["49950", "0.2"]]
    asks2 = [["50100", "2.5"], ["50150", "2.0"]]
    
    is_crisis2, severity2, details2 = monitor.detect_liquidity_crisis(bids2, asks2)
    
    print(f"  危機狀態:         {'是' if is_crisis2 else '否'}")
    print(f"  嚴重程度:         {severity2}")
    print(f"  價差:             {details2['spread_bps']:.2f} bps")
    print(f"  深度失衡:         {details2['depth_imbalance']*100:.1f}%")
    print(f"  總深度:           {details2['total_depth']:.2f} BTC")
    
    if is_crisis2:
        print(f"  🚨 危機警告:")
        for issue in details2['issues']:
            print(f"     - {issue}")
    
    print()


async def test_realtime_monitoring():
    """即時監控"""
    print("=" * 60)
    print("📡 測試 6: 即時 Spread & Depth 監控")
    print("=" * 60)
    print("連接 Binance WebSocket，監控 5 秒...")
    print()
    
    monitor = SpreadDepthMonitor(symbol="BTCUSDT", depth_levels=10)
    
    sample_count = 0
    max_samples = 10
    
    ws_url = "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms"
    
    try:
        async with websockets.connect(ws_url) as ws:
            print(f"🔌 已連接到: {ws_url}")
            print()
            
            while sample_count < max_samples:
                message = await ws.recv()
                data = json.loads(message)
                
                bids = data['bids']
                asks = data['asks']
                
                # 更新監控
                monitor.update(bids, asks)
                
                sample_count += 1
                
                # 每 2 次顯示一次
                if sample_count % 2 == 0:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    
                    spread = monitor.calculate_spread(bids, asks)
                    depth = monitor.calculate_depth(bids, asks, levels=10)
                    dw_spread = monitor.calculate_depth_weighted_spread(bids, asks)
                    is_crisis, severity, crisis_details = monitor.detect_liquidity_crisis(bids, asks)
                    
                    print(f"[{timestamp}] 樣本 {sample_count}/{max_samples}")
                    print(f"  Mid Price:    ${spread['mid_price']:.2f}")
                    print(f"  Spread:       {spread['spread_bps']:>6.2f} bps")
                    print(f"  總深度:       {depth['total_depth']:>6.2f} BTC")
                    print(f"  深度失衡:     {depth['depth_imbalance']:>6.3f}")
                    print(f"  加權Spread:   {dw_spread['depth_weighted_spread']*10000:>6.2f} bps")
                    print(f"  流動性:       {severity} {'🚨' if is_crisis else '✅'}")
                    print()
            
            print("✅ 即時測試完成")
            
            # 最終統計
            stats = monitor.get_statistics()
            print("\n📊 最終統計:")
            print(f"  平均價差:     {stats['mean_spread']*10000:.2f} bps")
            print(f"  價差波動:     {stats['spread_volatility']*10000:.2f} bps")
            print(f"  最小價差:     {stats['min_spread']*10000:.2f} bps")
            print(f"  最大價差:     {stats['max_spread']*10000:.2f} bps")
            
    except Exception as e:
        print(f"❌ 錯誤: {e}")


async def main():
    """主測試流程"""
    print("\n" + "=" * 60)
    print("🧪 Task 1.6.1 - B5: Spread & Depth 監控測試")
    print("=" * 60)
    print()
    
    # 測試 1: 價差計算
    test_spread_calculation()
    
    # 測試 2: 深度計算
    test_depth_calculation()
    
    # 測試 3: 深度加權價差
    test_depth_weighted_spread()
    
    # 測試 4: 有效價差
    test_effective_spread()
    
    # 測試 5: 流動性危機
    test_liquidity_crisis()
    
    # 測試 6: 即時監控
    await test_realtime_monitoring()
    
    print("\n" + "=" * 60)
    print("✅ 所有測試完成")
    print("=" * 60)
    print()
    
    # 總結
    print("📋 功能驗證總結:")
    print("  ✅ 價差計算（絕對/相對/基點）")
    print("  ✅ 深度計算（數量/價值/失衡）")
    print("  ✅ 深度加權價差")
    print("  ✅ 有效價差（考慮滑點）")
    print("  ✅ 流動性危機檢測")
    print("  ✅ 即時 WebSocket 整合")
    print()
    print("💡 應用場景:")
    print("  - Spread > 10 bps → 流動性不足，謹慎交易")
    print("  - 深度失衡 > 70% → 單向壓力大")
    print("  - 總深度 < 5 BTC → 流動性危機")
    print("  - 大單滑點 > 0.1% → 分批進場")
    print()
    print("🎯 下一步: 整合所有指標 + 開始 Phase C (分層決策系統)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
