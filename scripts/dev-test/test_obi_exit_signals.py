"""
測試 OBI 離場訊號功能
展示 OBI 如何提供進場和離場決策
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.exchange.obi_calculator import OBICalculator, OBISignal, ExitSignalType
import numpy as np


def print_section(title):
    """打印分隔線"""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70 + "\n")


def test_obi_reversal():
    """測試 OBI 翻轉離場訊號"""
    print_section("測試 1: OBI 翻轉離場訊號")
    
    calculator = OBICalculator(
        exit_obi_threshold=0.2,
        extreme_regression_threshold=0.5  # 提高閾值，避免誤觸發
    )
    
    # 模擬場景：多單持倉，OBI 從正轉負
    print("場景：持有多單，觀察 OBI 變化\n")
    
    scenarios = [
        (0.45, "進場", "買盤強勢，OBI=0.45"),
        (0.35, "持有", "買盤仍強"),
        (0.20, "持有", "買盤轉弱，觀望"),
        (0.05, "警告", "OBI 接近 0，買盤力量衰退"),
        (-0.15, "出場", "OBI 翻負，賣盤開始堆積"),
    ]
    
    # 設置進場
    calculator.set_position('LONG', entry_obi=0.45)
    print(f"✅ 進場：LONG, 進場 OBI = 0.45\n")
    
    for obi, action, desc in scenarios:
        # 模擬訂單簿更新
        bids = [[100000, 10 + obi * 20]] * 20
        asks = [[100001, 10 - obi * 20]] * 20
        calculator.update_orderbook(bids, asks)
        
        # 檢查離場訊號
        should_exit, exit_type, details = calculator.check_exit_signal(obi)
        
        print(f"OBI = {obi:+.2f} | {action:^6} | {desc}")
        
        if should_exit:
            print(f"   🚨 離場訊號！")
            print(f"   類型: {exit_type.value}")
            print(f"   原因: {details['reason']}")
            print(f"   嚴重性: {details['severity']}")
            if 'change' in details:
                print(f"   OBI 變化: {details['change']:.2f}")
            break
    
    print()


def test_obi_weakening():
    """測試 OBI 趨勢轉弱離場訊號"""
    print_section("測試 2: OBI 趨勢轉弱離場訊號")
    
    calculator = OBICalculator(
        exit_trend_periods=5,
        extreme_regression_threshold=0.5  # 提高閾值
    )
    
    print("場景：持有多單，OBI 持續下降\n")
    
    # 設置進場
    calculator.set_position('LONG', entry_obi=0.40)
    print(f"✅ 進場：LONG, 進場 OBI = 0.40\n")
    
    # 模擬 OBI 持續下降
    obi_sequence = [0.40, 0.35, 0.30, 0.25, 0.20, 0.15, 0.10]
    
    for i, obi in enumerate(obi_sequence, 1):
        bids = [[100000, 10 + obi * 20]] * 20
        asks = [[100001, 10 - obi * 20]] * 20
        calculator.update_orderbook(bids, asks)
        
        trend = calculator.get_obi_trend()
        should_exit, exit_type, details = calculator.check_exit_signal(obi)
        
        print(f"第 {i} 次更新 | OBI = {obi:.2f} | 趨勢 = {trend.value if trend else 'N/A'}")
        
        if should_exit:
            print(f"\n   🚨 離場訊號！")
            print(f"   類型: {exit_type.value}")
            print(f"   原因: {details['reason']}")
            print(f"   趨勢: {details.get('trend', 'N/A')}")
            print(f"   OBI 下降: {details.get('obi_decline', 0):.2f}")
            break
    
    print()


def test_extreme_regression():
    """測試極端值回歸離場訊號"""
    print_section("測試 3: 極端值回歸離場訊號")
    
    calculator = OBICalculator(extreme_regression_threshold=0.3)
    
    print("場景：持有多單，OBI 從極端高位回落\n")
    
    # 設置進場
    calculator.set_position('LONG', entry_obi=0.50)
    print(f"✅ 進場：LONG, 進場 OBI = 0.50（極端買盤）\n")
    
    # 模擬 OBI 從極端高位回落
    obi_sequence = [0.50, 0.60, 0.70, 0.65, 0.50, 0.35]
    
    for i, obi in enumerate(obi_sequence, 1):
        bids = [[100000, 10 + obi * 20]] * 20
        asks = [[100001, 10 - obi * 20]] * 20
        calculator.update_orderbook(bids, asks)
        
        should_exit, exit_type, details = calculator.check_exit_signal(obi)
        
        max_obi = calculator.max_obi if calculator.max_obi else 0
        print(f"第 {i} 次更新 | OBI = {obi:.2f} | 最大 OBI = {max_obi:.2f}")
        
        if should_exit:
            print(f"\n   🚨 離場訊號！")
            print(f"   類型: {exit_type.value}")
            print(f"   原因: {details['reason']}")
            print(f"   最大 OBI: {details['max_obi']:.2f}")
            print(f"   回歸幅度: {details['regression']:.2f}")
            break
    
    print()


def test_drastic_change():
    """測試劇烈變化離場訊號"""
    print_section("測試 4: 劇烈變化離場訊號（大單撤單）")
    
    calculator = OBICalculator(extreme_regression_threshold=0.6)  # 提高閾值
    
    print("場景：持有多單，大買單突然撤單\n")
    
    # 設置進場
    calculator.set_position('LONG', entry_obi=0.50)
    print(f"✅ 進場：LONG, 進場 OBI = 0.50（大買單堆積）\n")
    
    # 模擬大買單撤單
    scenarios = [
        (0.50, "持有", "大買單維持"),
        (0.48, "持有", "OBI 微調"),
        (0.05, "⚠️", "大買單突然撤單！OBI 劇降"),
    ]
    
    for obi, status, desc in scenarios:
        bids = [[100000, 10 + obi * 20]] * 20
        asks = [[100001, 10 - obi * 20]] * 20
        calculator.update_orderbook(bids, asks)
        
        should_exit, exit_type, details = calculator.check_exit_signal(obi)
        
        print(f"OBI = {obi:+.2f} | {status:^6} | {desc}")
        
        if should_exit:
            print(f"\n   🚨 離場訊號！")
            print(f"   類型: {exit_type.value}")
            print(f"   原因: {details['reason']}")
            if 'change' in details:
                print(f"   變化幅度: {details['change']:.2f}")
            if 'prev_obi' in details:
                print(f"   前一次 OBI: {details['prev_obi']:.2f}")
            break
    
    print()


def test_complete_trading_scenario():
    """測試完整交易場景：進場 → 持有 → 離場"""
    print_section("測試 5: 完整交易場景")
    
    calculator = OBICalculator(
        exit_obi_threshold=0.2,
        exit_trend_periods=5,
        extreme_regression_threshold=0.6  # 提高閾值
    )
    
    print("完整交易流程演示\n")
    
    # 階段 1: 尋找進場機會
    print("階段 1: 尋找進場機會")
    print("-" * 50)
    
    entry_obi = 0.45
    bids = [[100000, 10 + entry_obi * 20]] * 20
    asks = [[100001, 10 - entry_obi * 20]] * 20
    calculator.update_orderbook(bids, asks)
    
    signal = calculator.get_obi_signal(entry_obi)
    print(f"當前 OBI: {entry_obi:.2f}")
    print(f"進場信號: {signal.value}")
    
    if signal in [OBISignal.STRONG_BUY, OBISignal.BUY]:
        calculator.set_position('LONG', entry_obi)
        print(f"✅ 進場：開多單 @ OBI = {entry_obi:.2f}\n")
    
    # 階段 2: 持倉監控
    print("階段 2: 持倉監控（實時 OBI 變化）")
    print("-" * 50)
    
    holding_sequence = [
        (0.40, "+0.3%", "買盤仍強"),
        (0.35, "+0.5%", "買盤轉弱"),
        (0.28, "+0.6%", "OBI 持續下降"),
        (0.20, "+0.7%", "買盤力量減退"),
        (0.10, "+0.8%", "接近平衡"),
        (0.05, "+0.8%", "⚠️ OBI 接近 0"),
        (-0.15, "+0.7%", "🚨 OBI 翻負！"),
    ]
    
    for obi, pnl, desc in holding_sequence:
        bids = [[100000, 10 + obi * 20]] * 20
        asks = [[100001, 10 - obi * 20]] * 20
        calculator.update_orderbook(bids, asks)
        
        should_exit, exit_type, details = calculator.check_exit_signal(obi)
        trend = calculator.get_obi_trend()
        
        print(f"OBI = {obi:+.2f} | PnL = {pnl:>6} | 趨勢 = {trend.value if trend else 'N/A':^10} | {desc}")
        
        if should_exit:
            print(f"\n   🚨 離場訊號觸發！")
            print(f"   類型: {exit_type.value}")
            print(f"   原因: {details['reason']}")
            print(f"   嚴重性: {details['severity']}")
            print(f"   ✅ 出場：平多單 @ OBI = {obi:.2f}")
            print(f"   💰 最終獲利: {pnl}")
            break
    
    print()
    
    # 階段 3: 統計分析
    print("\n階段 3: 交易統計")
    print("-" * 50)
    stats = calculator.get_statistics()
    print(f"總更新次數: {stats['total_updates']}")
    print(f"離場訊號觸發次數: {stats['exit_signals_triggered']}")
    print(f"平均 OBI: {stats['mean_obi']:.4f}")
    print(f"OBI 標準差: {stats['std_obi']:.4f}")
    print(f"持倉期間最大 OBI: {stats['position_max_obi']:.4f}")


def test_exit_callback():
    """測試離場回調函數"""
    print_section("測試 6: 離場訊號回調")
    
    exit_signals_received = []
    
    def on_exit_signal(data):
        """離場訊號回調"""
        exit_signals_received.append(data)
        print(f"\n📨 收到離場訊號回調:")
        print(f"   類型: {data['signal_type']}")
        print(f"   詳情: {data['details']['reason']}")
        print(f"   時間: {data['details']['timestamp']}")
    
    calculator = OBICalculator(
        exit_obi_threshold=0.2,
        extreme_regression_threshold=0.6  # 提高閾值
    )
    calculator.on_exit_signal = on_exit_signal
    
    # 設置進場
    calculator.set_position('LONG', entry_obi=0.40)
    print("✅ 進場：LONG @ OBI = 0.40")
    print("註冊離場訊號回調...")
    
    # 模擬 OBI 變化
    obi_changes = [0.40, 0.30, 0.15, -0.10, -0.25]
    
    print("\n實時監控 OBI 變化:")
    for obi in obi_changes:
        bids = [[100000, 10 + obi * 20]] * 20
        asks = [[100001, 10 - obi * 20]] * 20
        calculator.update_orderbook(bids, asks)
        
        print(f"OBI = {obi:+.2f}", end="")
        
        if not exit_signals_received:
            print(" | 持有中...")
        else:
            print(" | 已觸發離場訊號")
            break
    
    print(f"\n總共收到 {len(exit_signals_received)} 個離場訊號")
    print()


def main():
    """主函數"""
    print("\n" + "🎯" * 35)
    print(" " * 20 + "OBI 離場訊號測試")
    print("🎯" * 35)
    
    print("\n說明:")
    print("  OBI 不僅可以提供進場訊號，還能提供離場訊號")
    print("  本測試展示 4 種離場訊號類型：")
    print("    1. OBI 翻轉（最強訊號）")
    print("    2. OBI 趨勢轉弱")
    print("    3. 極端值回歸")
    print("    4. 劇烈變化（大單撤單）")
    print()
    
    # 執行測試
    test_obi_reversal()
    test_obi_weakening()
    test_extreme_regression()
    test_drastic_change()
    test_complete_trading_scenario()
    test_exit_callback()
    
    # 總結
    print_section("總結")
    
    print("✅ OBI 離場訊號功能")
    print("   1. OBI 翻轉：多單 OBI<-0.2 或 空單 OBI>0.2")
    print("   2. 趨勢轉弱：OBI 持續下降（多單）或上升（空單）")
    print("   3. 極端回歸：從極端值（>0.7或<-0.7）回落 >0.3")
    print("   4. 劇烈變化：OBI 變化 >0.4（大單撤單）")
    
    print("\n✅ 整合到策略引擎")
    print("   - set_position(): 設置持倉狀態")
    print("   - check_exit_signal(): 檢查離場條件")
    print("   - on_exit_signal: 註冊離場回調")
    
    print("\n✅ 實戰應用")
    print("   - 進場：OBI > 0.3 (STRONG_BUY)")
    print("   - 持有：監控 OBI 趨勢")
    print("   - 離場：OBI 翻轉 or 趨勢轉弱 or 極端回歸")
    
    print("\n📊 進度: 6/67 任務 (9.0%)")
    print("🎯 下一步: Task 1.7 市場狀態偵測器")
    
    print("\n" + "="*70)
    print(" " * 20 + "✨ 測試完成 ✨")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
