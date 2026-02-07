#!/usr/bin/env python3
"""
🧪 dYdX 預掛單測試腳本

測試內容:
1. 開倉 (小額 0.001 BTC)
2. 立刻掛 TP + SL
3. 取消後重新掛單 (測量間隔)
4. 最後平倉清理

用法:
    .venv/bin/python scripts/test_dydx_pre_orders.py
"""

import asyncio
import time
import sys
from pathlib import Path

# 添加項目根目錄
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.dydx_whale_trader import DydxAPI, DydxConfig


async def test_pre_orders():
    """測試預掛單功能"""
    
    print("=" * 70)
    print("🧪 dYdX 預掛單測試 (僅測試掛單/取消，不開倉)")
    print("=" * 70)
    
    # 1. 初始化 API
    print("\n📡 連接 dYdX...")
    config = DydxConfig(symbol="BTC-USD")
    api = DydxAPI(config)
    
    if not await api.connect():
        print("❌ 連接失敗")
        return
    
    print("✅ 連接成功")
    
    # 初始化錢包 (必須)
    print("🔑 初始化錢包...")
    await api._init_node_client()
    
    if not api.wallet:
        print("❌ 錢包初始化失敗")
        return
    print(f"✅ 錢包已連接: {api.wallet.address[:20]}...")
    
    # 同步 sequence
    print("🔄 同步 sequence...")
    await api._refresh_sequence()
    print(f"✅ Sequence: {api.wallet.sequence}")
    
    # 2. 檢查餘額
    balance = await api.get_account_balance()
    print(f"💰 餘額: ${balance:.2f}")
    
    # 3. 獲取當前價格
    current_price = await api.get_price()
    print(f"📊 當前價格: ${current_price:,.2f}")
    
    # 測試參數 - 模擬已有持倉
    test_size = 0.001  # 最小測試量
    direction = "LONG"
    leverage = 20
    entry_price = current_price  # 假設已開倉
    
    # 計算 TP/SL 價格
    tp_pct = 2.0  # +2% 止盈
    sl_pct = 1.0  # -1% 止損
    
    tp_price = entry_price * (1 + tp_pct / 100 / leverage)
    sl_price = entry_price * (1 - sl_pct / 100 / leverage)
    
    print(f"\n📈 測試參數 (模擬持倉):")
    print(f"   方向: {direction}")
    print(f"   數量: {test_size} BTC")
    print(f"   模擬進場價: ${entry_price:,.2f}")
    print(f"   止盈價: ${tp_price:,.2f} (+{tp_pct}%)")
    print(f"   止損價: ${sl_price:,.2f} (-{sl_pct}%)")
    
    # ============================================================
    # 測試 1: 掛止盈單
    # ============================================================
    print(f"\n{'='*70}")
    print("📤 Test 1: 掛止盈單 (TP)")
    print("=" * 70)
    
    start_time = time.time()
    tp_tx, tp_order_id = await api.place_take_profit_order(
        side=direction,
        size=test_size,
        tp_price=tp_price,
        time_to_live_seconds=120  # 2 分鐘有效
    )
    tp_time = (time.time() - start_time) * 1000
    
    if tp_tx and tp_order_id:
        print(f"✅ TP 掛單成功! ID: {tp_order_id} | 耗時: {tp_time:.0f}ms")
    else:
        print(f"❌ TP 掛單失敗 | 耗時: {tp_time:.0f}ms")
        print("⚠️ 可能原因: Authenticator 設定問題或餘額不足")
        return
    
    # ============================================================
    # 測試 2: 掛止損單
    # ============================================================
    print(f"\n{'='*70}")
    print("📤 Test 2: 掛止損單 (SL)")
    print("=" * 70)
    
    start_time = time.time()
    sl_tx, sl_order_id = await api.place_stop_loss_order(
        side=direction,
        size=test_size,
        stop_price=sl_price,
        time_to_live_seconds=120
    )
    sl_time = (time.time() - start_time) * 1000
    
    if sl_tx and sl_order_id:
        print(f"✅ SL 掛單成功! ID: {sl_order_id} | 耗時: {sl_time:.0f}ms")
    else:
        print(f"❌ SL 掛單失敗 | 耗時: {sl_time:.0f}ms")
    
    # ============================================================
    # 測試 3: 取消 TP (LONG_TERM 訂單)
    # ============================================================
    print(f"\n{'='*70}")
    print("🔴 Test 3: 取消止盈單")
    print("=" * 70)
    
    if tp_order_id:
        start_time = time.time()
        cancel_result = await api.cancel_order(tp_order_id, order_type="LONG_TERM")
        cancel_tp_time = (time.time() - start_time) * 1000
        print(f"{'✅' if cancel_result else '❌'} 取消 TP | 耗時: {cancel_tp_time:.0f}ms")
    else:
        cancel_tp_time = 0
    
    # ============================================================
    # 測試 4: 取消 SL (CONDITIONAL 訂單)
    # ============================================================
    print(f"\n{'='*70}")
    print("🔴 Test 4: 取消止損單")
    print("=" * 70)
    
    if sl_order_id:
        start_time = time.time()
        cancel_result = await api.cancel_order(sl_order_id, order_type="CONDITIONAL")
        cancel_sl_time = (time.time() - start_time) * 1000
        print(f"{'✅' if cancel_result else '❌'} 取消 SL | 耗時: {cancel_sl_time:.0f}ms")
    else:
        cancel_sl_time = 0
    
    # ============================================================
    # 測試 5: 重新掛 TP (模擬中間位更新)
    # ============================================================
    print(f"\n{'='*70}")
    print("📤 Test 5: 重新掛止盈單 (新價格)")
    print("=" * 70)
    
    new_tp_price = entry_price * (1 + 1.5 / 100 / leverage)
    
    start_time = time.time()
    tp_tx2, tp_order_id2 = await api.place_take_profit_order(
        side=direction,
        size=test_size,
        tp_price=new_tp_price,
        time_to_live_seconds=120
    )
    tp_time2 = (time.time() - start_time) * 1000
    
    if tp_tx2 and tp_order_id2:
        print(f"✅ 新 TP 掛單成功! ID: {tp_order_id2} | 價格: ${new_tp_price:,.2f} | 耗時: {tp_time2:.0f}ms")
    else:
        print(f"❌ 新 TP 掛單失敗 | 耗時: {tp_time2:.0f}ms")
        tp_order_id2 = None
    
    # ============================================================
    # 測試 6: 重新掛 SL (更高止損價)
    # ============================================================
    print(f"\n{'='*70}")
    print("📤 Test 6: 重新掛止損單 (更高價格 - 鎖利)")
    print("=" * 70)
    
    new_sl_price = entry_price * (1 + 0.5 / 100 / leverage)
    
    start_time = time.time()
    sl_tx2, sl_order_id2 = await api.place_stop_loss_order(
        side=direction,
        size=test_size,
        stop_price=new_sl_price,
        time_to_live_seconds=120
    )
    sl_time2 = (time.time() - start_time) * 1000
    
    if sl_tx2 and sl_order_id2:
        print(f"✅ 新 SL 掛單成功! ID: {sl_order_id2} | 價格: ${new_sl_price:,.2f} | 耗時: {sl_time2:.0f}ms")
    else:
        print(f"❌ 新 SL 掛單失敗 | 耗時: {sl_time2:.0f}ms")
        sl_order_id2 = None
    
    # ============================================================
    # 測試 7: 快速循環 (取消+掛單)
    # ============================================================
    print(f"\n{'='*70}")
    print("⚡ Test 7: 快速取消+掛單循環測試 (3 次)")
    print("=" * 70)
    
    cycle_times = []
    
    for i in range(3):
        cycle_start = time.time()
        
        # 取消舊單 (SL 是 CONDITIONAL 類型)
        if sl_order_id2:
            await api.cancel_order(sl_order_id2, order_type="CONDITIONAL")
        
        # 掛新單
        new_price = entry_price * (1 + (0.6 + i * 0.1) / 100 / leverage)
        _, sl_order_id2 = await api.place_stop_loss_order(
            side=direction,
            size=test_size,
            stop_price=new_price,
            time_to_live_seconds=120
        )
        
        cycle_time = (time.time() - cycle_start) * 1000
        cycle_times.append(cycle_time)
        print(f"   循環 {i+1}: 取消+掛單 耗時 {cycle_time:.0f}ms | 新價: ${new_price:,.2f}")
    
    avg_cycle = sum(cycle_times) / len(cycle_times) if cycle_times else 0
    min_cycle = min(cycle_times) if cycle_times else 0
    
    # ============================================================
    # 清理：取消所有掛單
    # ============================================================
    print(f"\n{'='*70}")
    print("🧹 清理: 取消所有掛單")
    print("=" * 70)
    
    if tp_order_id2:
        await api.cancel_order(tp_order_id2, order_type="LONG_TERM")
        print(f"   ✅ 取消 TP")
    
    if sl_order_id2:
        await api.cancel_order(sl_order_id2, order_type="CONDITIONAL")
        print(f"   ✅ 取消 SL")
    
    # ============================================================
    # 測試報告
    # ============================================================
    print(f"\n{'='*70}")
    print("📊 測試報告")
    print("=" * 70)
    print(f"""
┌─────────────────────────────────────────────────────────────────────┐
│ 操作                      │ 耗時 (ms)                              │
├─────────────────────────────────────────────────────────────────────┤
│ 首次掛 TP                 │ {tp_time:>6.0f} ms                               │
│ 首次掛 SL                 │ {sl_time:>6.0f} ms                               │
│ 取消 TP                   │ {cancel_tp_time:>6.0f} ms                               │
│ 取消 SL                   │ {cancel_sl_time:>6.0f} ms                               │
│ 重新掛 TP                 │ {tp_time2:>6.0f} ms                               │
│ 重新掛 SL                 │ {sl_time2:>6.0f} ms                               │
│ 快速循環 (取消+掛單)      │ {avg_cycle:>6.0f} ms (平均)                        │
│ 快速循環 (最小)           │ {min_cycle:>6.0f} ms                               │
└─────────────────────────────────────────────────────────────────────┘

📈 結論:
   - 預掛單可行: {'✅ 是' if tp_tx else '❌ 否'}
   - 動態更新最快間隔: ~{min_cycle:.0f}ms
   - 建議更新頻率: 每 {max(1000, min_cycle * 2):.0f}ms 以上
""")
    
    print("=" * 70)
    print("✅ 測試完成!")
    print("=" * 70)


async def main():
    try:
        await test_pre_orders()
    except KeyboardInterrupt:
        print("\n⚠️ 測試中斷")
    except Exception as e:
        print(f"\n❌ 測試失敗: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
