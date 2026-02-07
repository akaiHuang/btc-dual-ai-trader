"""
Binance Client 快速測試腳本
測試基本功能（不需要 API Secret）
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.exchange.binance_client import BinanceClient


def test_basic_functions():
    """測試基本功能"""
    print("=" * 60)
    print("🧪 Binance Client 功能測試")
    print("=" * 60)
    print()
    
    # 初始化客戶端
    client = BinanceClient()
    print(f"✅ 客戶端初始化成功")
    print(f"   {client}")
    print()
    
    # 1. Ping 測試
    print("1️⃣  Ping 連線測試...")
    if client.ping():
        print("   ✅ 連線正常")
    else:
        print("   ❌ 連線失敗")
        return False
    print()
    
    # 2. 伺服器時間
    print("2️⃣  獲取伺服器時間...")
    server_time = client.get_server_time()
    print(f"   ✅ 伺服器時間: {server_time}")
    print()
    
    # 3. 當前價格
    print("3️⃣  獲取 BTC/USDT 價格...")
    ticker = client.get_symbol_ticker("BTCUSDT")
    price = float(ticker['price'])
    print(f"   ✅ 當前價格: ${price:,.2f}")
    print()
    
    # 4. K 線資料
    print("4️⃣  獲取 K 線資料...")
    klines = client.get_klines("BTCUSDT", "1m", limit=5)
    print(f"   ✅ 獲取到 {len(klines)} 根 K 線")
    print(f"   最新一根: 開 ${float(klines[-1][1]):,.2f}, 收 ${float(klines[-1][4]):,.2f}")
    print()
    
    # 5. 訂單簿
    print("5️⃣  獲取訂單簿...")
    order_book = client.get_order_book("BTCUSDT", limit=5)
    print(f"   ✅ 買單檔位: {len(order_book['bids'])}")
    print(f"   ✅ 賣單檔位: {len(order_book['asks'])}")
    print(f"   最佳買價: ${float(order_book['bids'][0][0]):,.2f}")
    print(f"   最佳賣價: ${float(order_book['asks'][0][0]):,.2f}")
    
    # 計算 OBI
    total_bid = sum(float(qty) for _, qty in order_book['bids'])
    total_ask = sum(float(qty) for _, qty in order_book['asks'])
    obi = (total_bid - total_ask) / (total_bid + total_ask)
    print(f"   OBI: {obi:.4f}", end="")
    if obi > 0.1:
        print(" (買盤強勢 🟢)")
    elif obi < -0.1:
        print(" (賣盤強勢 🔴)")
    else:
        print(" (相對平衡 ⚪)")
    print()
    
    # 6. 24h 統計
    print("6️⃣  獲取 24h 統計...")
    stats = client.get_24h_ticker("BTCUSDT")
    print(f"   ✅ 24h 漲跌: {float(stats['priceChangePercent']):.2f}%")
    print(f"   ✅ 24h 成交量: {float(stats['volume']):,.2f} BTC")
    print(f"   ✅ 24h 高點: ${float(stats['highPrice']):,.2f}")
    print(f"   ✅ 24h 低點: ${float(stats['lowPrice']):,.2f}")
    print()
    
    # 7. 交易規則
    print("7️⃣  獲取交易規則...")
    symbol_info = client.get_exchange_info("BTCUSDT")
    print(f"   ✅ 交易對: {symbol_info['symbol']}")
    print(f"   ✅ 狀態: {symbol_info['status']}")
    print(f"   ✅ 最小下單量: {symbol_info['filters'][1]['minQty']}")
    print()
    
    print("=" * 60)
    print("✅ 所有測試通過！")
    print("=" * 60)
    print()
    print("📝 功能確認:")
    print("  ✅ 連線測試")
    print("  ✅ 市場資料獲取")
    print("  ✅ K 線資料")
    print("  ✅ 訂單簿深度")
    print("  ✅ OBI 計算")
    print("  ✅ 24h 統計")
    print("  ✅ 交易規則查詢")
    print()
    print("🎯 Task 1.2 完成！")
    print()
    
    return True


if __name__ == '__main__':
    try:
        success = test_basic_functions()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
