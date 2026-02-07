"""
Binance Client 使用範例
展示如何使用 BinanceClient 進行各種操作
"""

import sys
from pathlib import Path

# 添加項目根目錄到路徑
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.exchange.binance_client import BinanceClient
import pandas as pd


def example_basic_usage():
    """基本使用範例"""
    print("=" * 60)
    print("基本使用範例")
    print("=" * 60)
    
    # 初始化客戶端（從 .env 自動讀取配置）
    client = BinanceClient()
    
    # 測試連線
    if client.ping():
        print("✅ Binance 連線正常")
    else:
        print("❌ Binance 連線失敗")
        return
    
    # 獲取伺服器時間
    server_time = client.get_server_time()
    print(f"伺服器時間: {server_time}")
    
    # 獲取當前價格
    ticker = client.get_symbol_ticker("BTCUSDT")
    print(f"BTC/USDT 當前價格: ${float(ticker['price']):,.2f}")
    
    print()


def example_get_klines():
    """獲取 K 線資料範例"""
    print("=" * 60)
    print("獲取 K 線資料")
    print("=" * 60)
    
    client = BinanceClient()
    
    # 獲取最近 100 根 3 分鐘 K 線
    klines = client.get_klines(
        symbol="BTCUSDT",
        interval="3m",
        limit=100
    )
    
    # 轉換為 DataFrame
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])
    
    # 轉換資料類型
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    
    print(f"獲取到 {len(df)} 根 K 線")
    print("\n最近 5 根 K 線:")
    print(df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail())
    
    print()


def example_get_order_book():
    """獲取訂單簿範例"""
    print("=" * 60)
    print("獲取訂單簿深度")
    print("=" * 60)
    
    client = BinanceClient()
    
    # 獲取訂單簿（前 10 檔）
    order_book = client.get_order_book("BTCUSDT", limit=10)
    
    print(f"買單數量: {len(order_book['bids'])}")
    print(f"賣單數量: {len(order_book['asks'])}")
    
    print("\n前 5 檔買單:")
    for i, (price, qty) in enumerate(order_book['bids'][:5], 1):
        print(f"  {i}. ${float(price):,.2f} × {float(qty):.4f} BTC")
    
    print("\n前 5 檔賣單:")
    for i, (price, qty) in enumerate(order_book['asks'][:5], 1):
        print(f"  {i}. ${float(price):,.2f} × {float(qty):.4f} BTC")
    
    # 計算 OBI（訂單簿失衡）
    total_bid_qty = sum(float(qty) for _, qty in order_book['bids'][:10])
    total_ask_qty = sum(float(qty) for _, qty in order_book['asks'][:10])
    obi = (total_bid_qty - total_ask_qty) / (total_bid_qty + total_ask_qty)
    
    print(f"\nOBI (Order Book Imbalance): {obi:.4f}")
    if obi > 0.1:
        print("  → 買盤強勢 🟢")
    elif obi < -0.1:
        print("  → 賣盤強勢 🔴")
    else:
        print("  → 相對平衡 ⚪")
    
    print()


def example_get_account_info():
    """獲取帳戶資訊範例"""
    print("=" * 60)
    print("獲取帳戶資訊")
    print("=" * 60)
    
    client = BinanceClient()
    
    # 獲取帳戶資訊
    account = client.get_account_info()
    
    print(f"帳戶類型: {account['accountType']}")
    print(f"可以交易: {account['canTrade']}")
    print(f"可以提現: {account['canWithdraw']}")
    
    # 只顯示非零餘額
    print("\n非零餘額:")
    for balance in account['balances']:
        free = float(balance['free'])
        locked = float(balance['locked'])
        if free > 0 or locked > 0:
            print(f"  {balance['asset']}: {free} (可用) + {locked} (凍結)")
    
    # 獲取特定資產餘額
    usdt_balance = client.get_balance('USDT')
    print(f"\nUSDT 餘額: {usdt_balance['free']} USDT")
    
    print()


def example_24h_stats():
    """獲取 24 小時統計範例"""
    print("=" * 60)
    print("24 小時價格統計")
    print("=" * 60)
    
    client = BinanceClient()
    
    stats = client.get_24h_ticker("BTCUSDT")
    
    print(f"交易對: {stats['symbol']}")
    print(f"24h 開盤價: ${float(stats['openPrice']):,.2f}")
    print(f"24h 最高價: ${float(stats['highPrice']):,.2f}")
    print(f"24h 最低價: ${float(stats['lowPrice']):,.2f}")
    print(f"24h 收盤價: ${float(stats['lastPrice']):,.2f}")
    print(f"24h 成交量: {float(stats['volume']):,.2f} BTC")
    print(f"24h 成交額: ${float(stats['quoteVolume']):,.2f}")
    print(f"24h 漲跌幅: {float(stats['priceChangePercent']):.2f}%")
    
    change = float(stats['priceChangePercent'])
    if change > 0:
        print(f"  → 上漲 🟢")
    elif change < 0:
        print(f"  → 下跌 🔴")
    else:
        print(f"  → 持平 ⚪")
    
    print()


def example_test_order():
    """測試訂單範例（不實際下單）"""
    print("=" * 60)
    print("測試訂單（Test Order）")
    print("=" * 60)
    
    client = BinanceClient()
    
    # 獲取當前價格
    ticker = client.get_symbol_ticker("BTCUSDT")
    current_price = float(ticker['price'])
    
    # 測試限價買單
    test_order = client.create_test_order(
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        quantity=0.001,
        price=current_price * 0.99,  # 低於市價 1%
        timeInForce="GTC"
    )
    
    print("✅ 測試訂單通過（未實際下單）")
    print(f"交易對: BTCUSDT")
    print(f"方向: 買入")
    print(f"類型: 限價單")
    print(f"數量: 0.001 BTC")
    print(f"價格: ${current_price * 0.99:,.2f}")
    
    print()


def main():
    """主函數"""
    print("\n🚀 Binance Client 使用範例\n")
    
    try:
        example_basic_usage()
        example_get_klines()
        example_get_order_book()
        example_get_account_info()
        example_24h_stats()
        example_test_order()
        
        print("=" * 60)
        print("✅ 所有範例執行完成")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
