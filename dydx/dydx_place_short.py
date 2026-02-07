"""
dYdX 做空下單程式
==================
50X 槓桿逐倉做空 BTC-USD
- 使用 10% 帳戶資金
- 獲利 2% 平倉
- 止損 1%
"""

import os
import asyncio
import random
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

from v4_proto.dydxprotocol.clob.order_pb2 import Order
from dydx_v4_client import MAX_CLIENT_ID, OrderFlags
from dydx_v4_client.indexer.rest.constants import OrderType
from dydx_v4_client.indexer.rest.indexer_client import IndexerClient
from dydx_v4_client.node.client import NodeClient
from dydx_v4_client.node.market import Market
from dydx_v4_client.wallet import Wallet
from dydx_v4_client.key_pair import KeyPair


# 網路設定
NETWORK_CONFIG = {
    "mainnet": {
        "rest_indexer": "https://indexer.dydx.trade",
        "node_grpc": "dydx-ops-grpc.kingnodes.com:443",
    },
    "testnet": {
        "rest_indexer": "https://indexer.v4testnet.dydx.exchange",
        "node_grpc": "test-dydx-grpc.kingnodes.com:443",
    }
}


async def place_short_order():
    """下單做空 BTC-USD"""
    
    # 讀取設定
    network = os.getenv("DYDX_NETWORK", "mainnet")
    main_address = os.getenv("DYDX_ADDRESS", "")  # 主帳戶地址（資金所在）
    api_wallet_address = os.getenv("WALLET_ADDRESS", "")  # API 錢包地址（簽名用）
    private_key = os.getenv("DYDX_PRIVATE_KEY", "")
    
    if not main_address or not private_key:
        print("❌ 請在 .env 設定 DYDX_ADDRESS 和 DYDX_PRIVATE_KEY")
        return
    
    # API 錢包地址如果沒設定，從私鑰推導
    if not api_wallet_address:
        import hashlib
        import bech32
        from Crypto.Hash import RIPEMD160
        temp_key = private_key[2:] if private_key.startswith("0x") else private_key
        key_pair_temp = KeyPair.from_hex(temp_key)
        public_key_bytes = key_pair_temp.public_key_bytes
        sha256_hash = hashlib.sha256(public_key_bytes).digest()
        ripemd160_hash = RIPEMD160.new(sha256_hash).digest()
        api_wallet_address = bech32.bech32_encode('dydx', bech32.convertbits(ripemd160_hash, 8, 5))
    
    # 移除私鑰的 0x 前綴
    if private_key.startswith("0x"):
        private_key = private_key[2:]
    
    config = NETWORK_CONFIG[network]
    
    print("=" * 60)
    print(f"🚀 dYdX 做空下單 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📡 網路: {network}")
    print(f"👤 主帳戶: {main_address}")
    print(f"🔑 API錢包: {api_wallet_address}")
    print("=" * 60)
    
    # 初始化客戶端
    print("\n🔗 連接中...")
    indexer = IndexerClient(config["rest_indexer"])
    
    # 取得帳戶資訊 (使用主帳戶地址查詢餘額)
    print("\n👤 取得帳戶資訊...")
    account_info = await indexer.account.get_subaccount(main_address, 0)
    subaccount = account_info.get("subaccount", {})
    equity = float(subaccount.get("equity", 0))
    free_collateral = float(subaccount.get("freeCollateral", 0))
    
    print(f"   帳戶權益: ${equity:,.2f}")
    print(f"   可用保證金: ${free_collateral:,.2f}")
    
    if equity <= 0:
        print("❌ 帳戶餘額不足")
        return
    
    # 取得市場資訊
    MARKET_ID = "BTC-USD"
    print(f"\n📊 取得 {MARKET_ID} 市場資訊...")
    market_data = await indexer.markets.get_perpetual_markets(MARKET_ID)
    market_info = market_data.get("markets", {}).get(MARKET_ID, {})
    
    oracle_price = float(market_info.get("oraclePrice", 0))
    print(f"   Oracle 價格: ${oracle_price:,.2f}")
    
    # 計算下單參數
    # 50X 槓桿，使用 10% 資金
    LEVERAGE = 50
    POSITION_PERCENT = 0.10  # 10%
    TAKE_PROFIT_PERCENT = 0.02  # 2%
    STOP_LOSS_PERCENT = 0.01  # 1%
    
    # 計算倉位大小
    # 保證金 = 帳戶權益 * 10%
    margin = equity * POSITION_PERCENT
    # 名義價值 = 保證金 * 槓桿
    notional_value = margin * LEVERAGE
    # BTC 數量 = 名義價值 / 價格
    size = notional_value / oracle_price
    
    # dYdX BTC-USD 最小交易單位是 0.0001 BTC
    size = round(size, 4)
    
    # 確保最小交易量
    if size < 0.0001:
        size = 0.0001
    
    # 計算止盈止損價格 (做空)
    entry_price = oracle_price
    take_profit_price = entry_price * (1 - TAKE_PROFIT_PERCENT)  # 做空獲利價格較低
    stop_loss_price = entry_price * (1 + STOP_LOSS_PERCENT)  # 做空止損價格較高
    
    print(f"\n📋 下單參數:")
    print(f"   方向: 🔴 做空 (SHORT)")
    print(f"   槓桿: {LEVERAGE}X")
    print(f"   保證金: ${margin:,.2f} ({POSITION_PERCENT*100:.0f}% 帳戶)")
    print(f"   名義價值: ${notional_value:,.2f}")
    print(f"   數量: {size:.4f} BTC")
    print(f"   進場價: ${entry_price:,.2f}")
    print(f"   止盈價: ${take_profit_price:,.2f} ({TAKE_PROFIT_PERCENT*100:.0f}%)")
    print(f"   止損價: ${stop_loss_price:,.2f} ({STOP_LOSS_PERCENT*100:.0f}%)")
    
    # 確認下單
    print("\n" + "=" * 60)
    print("⚠️  確認下單資訊")
    print("=" * 60)
    print(f"   市場: {MARKET_ID}")
    print(f"   方向: 做空 (SELL)")
    print(f"   數量: {size:.4f} BTC")
    print(f"   預估保證金: ${margin:,.2f}")
    print(f"   槓桿: {LEVERAGE}X")
    
    confirm = input("\n確認下單? (輸入 'yes' 確認): ")
    if confirm.lower() != 'yes':
        print("❌ 已取消下單")
        return
    
    # 連接節點
    print("\n🔗 連接節點...")
    try:
        # 建立節點連接
        from dydx_v4_client.network import make_mainnet, make_testnet
        
        if network == "mainnet":
            node_config = make_mainnet(
                rest_indexer=config["rest_indexer"],
                websocket_indexer=f"wss://indexer.dydx.trade/v4/ws",
                node_url=config["node_grpc"]
            )
        else:
            from dydx_v4_client.network import TESTNET
            node_config = TESTNET
            
        node = await NodeClient.connect(node_config.node)
        
        # 從私鑰建立錢包
        # 使用 API 錢包地址取得帳戶資訊
        try:
            account = await node.get_account(api_wallet_address)
        except Exception as e:
            print(f"   ⚠️  API 錢包尚未在鏈上註冊，使用預設值...")
            # 如果 API 錢包尚未註冊，建立一個新帳戶
            from dataclasses import dataclass
            @dataclass
            class DummyAccount:
                account_number: int = 0
                sequence: int = 0
            account = DummyAccount()
        
        key_pair = KeyPair.from_hex(private_key)
        
        wallet = Wallet(
            key=key_pair,
            account_number=account.account_number,
            sequence=account.sequence
        )
        
        print(f"   ✅ 錢包已連接: {wallet.address}")
        
    except Exception as e:
        print(f"❌ 連接節點失敗: {e}")
        print("\n💡 提示: 主網下單需要正確的節點連接設定")
        print("   建議使用官方節點或信任的第三方節點")
        return
    
    # 建立市場物件
    market = Market(market_info)
    
    # 建立訂單 ID (使用主帳戶地址，因為訂單屬於主帳戶)
    client_id = random.randint(0, MAX_CLIENT_ID)
    order_id = market.order_id(
        main_address, 
        0,  # subaccount number
        client_id, 
        OrderFlags.SHORT_TERM
    )
    
    # 取得當前區塊高度
    current_block = await node.latest_block_height()
    good_til_block = current_block + 20  # 訂單有效期約 30 秒
    
    print(f"\n📤 提交訂單...")
    print(f"   Client ID: {client_id}")
    print(f"   Good til block: {good_til_block}")
    
    # 建立市價賣單 (做空)
    # 對於市價單，price 設為 0 或一個保護價格
    # 做空的市價單應該設一個較低的保護價格
    slippage_price = oracle_price * 0.95  # 5% 滑點保護
    
    new_order = market.order(
        order_id=order_id,
        order_type=OrderType.MARKET,
        side=Order.Side.SIDE_SELL,  # 做空 = 賣出
        size=size,
        price=slippage_price,  # 市價單的滑點保護價格
        time_in_force=Order.TimeInForce.TIME_IN_FORCE_IOC,  # 立即成交或取消
        reduce_only=False,
        good_til_block=good_til_block,
    )
    
    # 下單
    try:
        transaction = await node.place_order(
            wallet=wallet,
            order=new_order,
        )
        
        print(f"\n✅ 訂單已提交!")
        print(f"   交易哈希: {transaction}")
        
        # 更新錢包 sequence
        wallet.sequence += 1
        
        # 等待成交
        print("\n⏳ 等待成交確認...")
        await asyncio.sleep(5)
        
        # 查詢持倉 (使用主帳戶地址)
        positions = await indexer.account.get_subaccount_perpetual_positions(main_address, 0)
        if positions and positions.get("positions"):
            for pos in positions["positions"]:
                if pos["market"] == MARKET_ID:
                    side = "🟢多" if pos["side"] == "LONG" else "🔴空"
                    print(f"\n📊 當前持倉:")
                    print(f"   {pos['market']} {side}")
                    print(f"   數量: {pos['size']} BTC")
                    print(f"   入場價: ${float(pos['entryPrice']):,.2f}")
                    print(f"   未實現盈虧: ${float(pos.get('unrealizedPnl', 0)):,.2f}")
        
        print("\n" + "=" * 60)
        print("💡 止盈止損提醒:")
        print(f"   止盈價: ${take_profit_price:,.2f} (獲利 {TAKE_PROFIT_PERCENT*100:.0f}%)")
        print(f"   止損價: ${stop_loss_price:,.2f} (損失 {STOP_LOSS_PERCENT*100:.0f}%)")
        print("=" * 60)
        print("⚠️  請手動設定止盈止損單，或使用自動監控程式")
        
    except Exception as e:
        print(f"\n❌ 下單失敗: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(place_short_order())
