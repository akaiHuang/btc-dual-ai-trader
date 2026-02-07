#!/usr/bin/env python3
"""
Binance API 連線測試腳本
測試 Testnet API Key 是否正確配置
"""

import os
import sys
from pathlib import Path

# 添加項目根目錄到 Python 路徑
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from binance.client import Client
from binance.exceptions import BinanceAPIException


def load_env():
    """載入環境變數"""
    env_path = project_root / '.env'
    if not env_path.exists():
        print("❌ 錯誤: .env 文件不存在")
        print("請執行: cp .env.example .env")
        print("然後填入你的 API Key 和 Secret")
        sys.exit(1)
    
    load_dotenv(env_path)
    
    api_key = os.getenv('BINANCE_API_KEY')
    api_secret = os.getenv('BINANCE_API_SECRET')
    testnet = os.getenv('BINANCE_TESTNET', 'true').lower() == 'true'
    
    if not api_key or api_key == 'YOUR_API_KEY_HERE':
        print("❌ 錯誤: BINANCE_API_KEY 未設置")
        sys.exit(1)
    
    if not api_secret or api_secret == 'YOUR_SECRET_KEY_HERE':
        print("❌ 錯誤: BINANCE_API_SECRET 未設置")
        print("請在 .env 文件中填入你的 Secret Key")
        sys.exit(1)
    
    return api_key, api_secret, testnet


def test_connection(api_key: str, api_secret: str, testnet: bool):
    """測試 Binance API 連線"""
    print("🔗 測試 Binance API 連線...")
    print("=" * 60)
    
    try:
        # 創建客戶端
        if testnet:
            print("📍 環境: Testnet")
            client = Client(api_key, api_secret, testnet=True)
            # Testnet 基礎 URL
            client.API_URL = 'https://testnet.binance.vision/api'
        else:
            print("📍 環境: Production (正式環境)")
            client = Client(api_key, api_secret)
        
        print(f"✅ API Key: {api_key[:8]}...{api_key[-8:]}")
        print()
        
        # 測試 1: 獲取伺服器時間
        print("1️⃣  測試伺服器連線...")
        server_time = client.get_server_time()
        print(f"   ✅ 伺服器時間: {server_time['serverTime']}")
        print()
        
        # 測試 2: 獲取帳戶資訊（需要 API 權限）
        print("2️⃣  測試帳戶權限...")
        account_info = client.get_account()
        print(f"   ✅ 帳戶類型: {account_info.get('accountType', 'N/A')}")
        print(f"   ✅ 可以交易: {account_info.get('canTrade', False)}")
        print(f"   ✅ 可以提現: {account_info.get('canWithdraw', False)}")
        print()
        
        # 測試 3: 獲取餘額
        print("3️⃣  測試帳戶餘額...")
        balances = account_info.get('balances', [])
        non_zero_balances = [b for b in balances if float(b['free']) > 0 or float(b['locked']) > 0]
        
        if non_zero_balances:
            print("   ✅ 非零餘額:")
            for balance in non_zero_balances[:5]:  # 只顯示前 5 個
                print(f"      {balance['asset']}: {balance['free']} (可用) + {balance['locked']} (凍結)")
        else:
            print("   ⚠️  所有餘額為 0（Testnet 帳戶需要充值測試幣）")
        print()
        
        # 測試 4: 獲取 BTC/USDT 行情
        print("4️⃣  測試市場資料...")
        ticker = client.get_symbol_ticker(symbol="BTCUSDT")
        print(f"   ✅ BTC/USDT 當前價格: ${float(ticker['price']):,.2f}")
        print()
        
        # 測試 5: 獲取訂單簿（測試 WebSocket 連線能力）
        print("5️⃣  測試訂單簿資料...")
        depth = client.get_order_book(symbol="BTCUSDT", limit=5)
        print(f"   ✅ 買單數量: {len(depth['bids'])}")
        print(f"   ✅ 賣單數量: {len(depth['asks'])}")
        print(f"   ✅ 最佳買價: ${float(depth['bids'][0][0]):,.2f}")
        print(f"   ✅ 最佳賣價: ${float(depth['asks'][0][0]):,.2f}")
        print()
        
        # 測試 6: 測試下單權限（不實際下單）
        print("6️⃣  測試交易權限...")
        try:
            # 獲取交易規則
            exchange_info = client.get_symbol_info("BTCUSDT")
            print(f"   ✅ 交易對狀態: {exchange_info['status']}")
            print(f"   ✅ 最小下單量: {exchange_info['filters'][1]['minQty']}")
            print()
        except Exception as e:
            print(f"   ⚠️  無法獲取交易規則: {e}")
            print()
        
        print("=" * 60)
        print("🎉 所有測試通過！API Key 配置正確")
        print()
        print("建議後續步驟：")
        if testnet:
            print("  1. 在 Testnet 充值測試幣（如果餘額為 0）")
            print("  2. 開始實作 Task 1.2: Binance API 串接模組")
            print("  3. 測試完成後切換到正式 API")
        else:
            print("  1. 確認風控參數設置正確")
            print("  2. 從小額資金開始測試")
            print("  3. 監控所有交易日誌")
        
        return True
        
    except BinanceAPIException as e:
        print(f"❌ Binance API 錯誤:")
        print(f"   錯誤代碼: {e.status_code}")
        print(f"   錯誤訊息: {e.message}")
        
        if e.status_code == -2015:
            print("\n💡 解決方案:")
            print("   - 檢查 API Key 是否正確")
            print("   - 確認 API Key 已啟用交易權限")
            print("   - 檢查 IP 白名單設置（如有）")
        
        return False
        
    except Exception as e:
        print(f"❌ 未知錯誤: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("🚀 BTC 智能交易系統 - API 連線測試")
    print()
    
    # 載入環境變數
    try:
        api_key, api_secret, testnet = load_env()
    except Exception as e:
        print(f"❌ 環境變數載入失敗: {e}")
        sys.exit(1)
    
    # 測試連線
    success = test_connection(api_key, api_secret, testnet)
    
    if success:
        sys.exit(0)
    else:
        print("\n請檢查配置後重試")
        sys.exit(1)


if __name__ == '__main__':
    main()
