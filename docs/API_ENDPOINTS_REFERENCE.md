# 📡 幣安 & dYdX API 端點參考文件

> **版本**: v1.0.0  
> **更新日期**: 2025-12-17  
> **狀態**: ✅ 已驗證可用

本文件記錄所有經過測試且運作良好的 API 端點，避免重複踩坑。

---

## ⚠️ 安全提醒：認明官方網站

> **dYdX 官方網站只有以下幾個，其他都可能是釣魚網站！**

| 用途 | 官方 URL | 狀態 |
|------|----------|------|
| **官方首頁** | `https://dydx.exchange` | ✅ 官方 |
| **交易介面** | `https://dydx.trade` | ✅ 官方 |
| **官方文檔** | `https://docs.dydx.exchange` | ✅ 官方 |
| **GitHub** | `https://github.com/dydxprotocol` | ✅ 官方 |
| **Indexer API** | `https://indexer.dydx.trade` | ✅ 官方 |

### ❌ 已知釣魚/詐騙網站
- `dydx.xyz` - ⚠️ **不是官方！**
- 任何要求輸入私鑰或助記詞的網站

---

## 📌 目錄

1. [dYdX WebSocket (主要數據源)](#1-dydx-websocket-主要數據源)
2. [dYdX REST API (初始化/備援)](#2-dydx-rest-api-初始化備援)
3. [幣安 WebSocket (輔助數據)](#3-幣安-websocket-輔助數據)
4. [幣安 REST API (交易/帳戶)](#4-幣安-rest-api-交易帳戶)
5. [重要經驗教訓](#5-重要經驗教訓)
6. [速率限制注意事項](#6-速率限制注意事項)
7. [dYdX 交易 (下單) 完整指南](#7-dydx-交易-下單-完整指南)

---

## 1. dYdX WebSocket (主要數據源)

### 🌐 端點 URL

| 網路 | WebSocket URL | 狀態 |
|------|--------------|------|
| **Mainnet** | `wss://indexer.dydx.trade/v4/ws` | ✅ 穩定 |
| **Testnet** | `wss://indexer.v4testnet.dydx.exchange/v4/ws` | ✅ 可用 |

### 📊 訂閱頻道

```python
# 1. 即時交易 (v4_trades)
{"type": "subscribe", "channel": "v4_trades", "id": "BTC-USD"}

# 2. 訂單簿 (v4_orderbook) - 推薦使用，更新頻率最高
{"type": "subscribe", "channel": "v4_orderbook", "id": "BTC-USD"}

# 3. K 線數據 (v4_candles)
{"type": "subscribe", "channel": "v4_candles", "id": "BTC-USD/1MIN"}
```

### 📦 消息格式

```python
# 訂閱成功回覆
{
    "type": "subscribed",
    "channel": "v4_orderbook",
    "id": "BTC-USD",
    "contents": {
        "bids": [{"price": "104000.5", "size": "0.15"}, ...],
        "asks": [{"price": "104001.0", "size": "0.20"}, ...]
    }
}

# 增量更新
{
    "type": "channel_data",
    "channel": "v4_orderbook", 
    "id": "BTC-USD",
    "contents": {
        "bids": [{"price": "104000.5", "size": "0.25"}],  # size=0 表示刪除
        "asks": []
    }
}

# 交易數據
{
    "type": "channel_data",
    "channel": "v4_trades",
    "id": "BTC-USD",
    "contents": {
        "trades": [
            {"price": "104000.5", "size": "0.1", "side": "BUY", "createdAt": "..."}
        ]
    }
}
```

### ⚙️ 連接設定 (推薦)

```python
import websockets

async with websockets.connect(
    "wss://indexer.dydx.trade/v4/ws",
    ping_interval=30.0,   # 心跳間隔
    ping_timeout=60.0     # 超時時間
) as ws:
    # 訂閱頻道...
```

### 🔑 關鍵發現

> **訂單簿更新頻率極高 (~243 次/秒)**  
> 這是最適合用來更新 `last_trade_time` 的數據源，確保延遲監控正確。

```python
# src/dydx_data_hub.py - _process_orderbook() 
def _process_orderbook(self, contents: Dict, is_snapshot: bool = False):
    # ... 訂單簿處理邏輯 ...
    
    # 🔧 v14.9: 更新時間戳 (orderbook 更新頻率高，確保數據新鮮度)
    self._data.last_trade_time = time.time() * 1000
```

---

## 2. dYdX REST API (初始化/備援)

### 🌐 端點 URL

| 網路 | REST API Base URL | 狀態 |
|------|------------------|------|
| **Mainnet** | `https://indexer.dydx.trade/v4` | ✅ 穩定 |
| **Testnet** | `https://indexer.v4testnet.dydx.exchange/v4` | ✅ 可用 |

### 📋 常用端點

#### 訂單簿

```bash
GET /orderbooks/perpetualMarket/{symbol}

# 範例
curl "https://indexer.dydx.trade/v4/orderbooks/perpetualMarket/BTC-USD"
```

#### K 線數據

```bash
GET /candles/perpetualMarkets/{symbol}?resolution=1MIN&limit=60

# 範例
curl "https://indexer.dydx.trade/v4/candles/perpetualMarkets/BTC-USD?resolution=1MIN&limit=60"
```

#### 帳戶資訊

```bash
GET /addresses/{address}/subaccountNumber/{subaccountNumber}

# 範例
curl "https://indexer.dydx.trade/v4/addresses/dydx1.../subaccountNumber/0"
```

#### 成交記錄

```bash
GET /fills?address={address}&subaccountNumber=0&limit=100

# 範例
curl "https://indexer.dydx.trade/v4/fills?address=dydx1...&subaccountNumber=0&limit=100"
```

---

## 3. 幣安 WebSocket (輔助數據)

### 🌐 端點 URL

| 用途 | WebSocket URL | 狀態 |
|------|--------------|------|
| **合約 (正式網)** | `wss://fstream.binance.com` | ✅ 穩定 |
| **現貨 (正式網)** | `wss://stream.binance.com:9443` | ✅ 穩定 |

> ⚠️ **重要**: Testnet 沒有公開的 WebSocket，統一使用正式網。  
> 正式網和 Testnet 的價格通常差異很小 (< $10)。

### 📊 訂閱串流

```python
# 逐筆成交 (aggTrade)
ws_url = "wss://fstream.binance.com/ws/btcusdt@aggTrade"

# 訂單簿深度 (depth5@100ms - 每 100ms 更新 top 5)
depth_url = "wss://fstream.binance.com/ws/btcusdt@depth5@100ms"

# K 線 (kline_15m)
kline_url = "wss://stream.binance.com:9443/ws/btcusdt@kline_15m"
```

### 📦 消息格式

```python
# aggTrade 格式
{
    "e": "aggTrade",
    "E": 1702789200000,  # 事件時間
    "s": "BTCUSDT",
    "p": "104000.50",    # 成交價
    "q": "0.100",        # 成交量
    "T": 1702789200000,  # 交易時間
    "m": true            # True = 賣方主動 (maker), False = 買方主動 (taker)
}

# depth5 格式
{
    "e": "depthUpdate",
    "E": 1702789200000,
    "s": "BTCUSDT",
    "b": [["104000.00", "1.500"], ...],  # bids
    "a": [["104001.00", "0.800"], ...]   # asks
}
```

### 💡 使用範例

```python
# scripts/whale_testnet_trader.py - BinanceWebSocket
class BinanceWebSocket:
    def __init__(self, symbol="btcusdt", use_testnet=False):
        # 注意: Testnet 沒有公開的 WebSocket，統一使用正式網
        base_url = "wss://fstream.binance.com"
        
        self.ws_url = f"{base_url}/ws/{symbol}@aggTrade"
        self.depth_url = f"{base_url}/ws/{symbol}@depth5@100ms"
```

---

## 4. 幣安 REST API (交易/帳戶)

### 🌐 端點 URL

| 用途 | REST API Base URL | 狀態 |
|------|------------------|------|
| **Testnet (合約)** | `https://testnet.binancefuture.com` | ✅ 可用 |
| **正式網 (合約)** | `https://fapi.binance.com` | ✅ 穩定 |
| **正式網 (現貨)** | `https://api.binance.com` | ✅ 穩定 |

### 📋 常用端點

#### 帳戶資訊 (需簽名)

```bash
GET /fapi/v2/account

# Python 範例
url = f"{base_url}/fapi/v2/account?{sign(params)}"
```

#### 持倉風險

```bash
GET /fapi/v2/positionRisk

# Python 範例
url = f"{base_url}/fapi/v2/positionRisk?{sign(params)}"
```

#### 設定槓桿

```bash
POST /fapi/v1/leverage

# Python 範例
url = f"{base_url}/fapi/v1/leverage?{sign(params)}"
```

#### 下單

```bash
POST /fapi/v1/order

# Python 範例
url = f"{base_url}/fapi/v1/order?{sign(params)}"
```

#### 公開端點 (無需簽名)

```bash
# 當前價格
GET /fapi/v1/ticker/price?symbol=BTCUSDT

# 資金費率
GET /fapi/v1/premiumIndex?symbol=BTCUSDT

# 24 小時統計
GET /fapi/v1/ticker/24hr?symbol=BTCUSDT

# 訂單簿
GET /fapi/v1/depth?symbol=BTCUSDT&limit=20

# 最近成交
GET /fapi/v1/trades?symbol=BTCUSDT&limit=100

# K 線數據
GET /fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=100
```

---

## 5. 重要經驗教訓

### ❌ 常見錯誤

#### 5.1 延遲監控問題

**問題**: 延遲顯示 56,000ms (56 秒)  
**原因**: 只有 `_process_trades` 更新 `last_trade_time`，但交易數據可能長時間沒有

**解決方案**: 在 `_process_orderbook` 中也更新時間戳

```python
def _process_orderbook(self, ...):
    # ... 處理訂單簿 ...
    
    # 🔧 重要: 更新時間戳
    self._data.last_trade_time = time.time() * 1000
```

**結果**: 延遲從 56,000ms 降到 13ms~331ms

#### 5.2 Testnet WebSocket 不存在

**問題**: 嘗試連接 `wss://testnet.binancefuture.com`  
**原因**: 幣安 Testnet 沒有公開 WebSocket

**解決方案**: 統一使用正式網 WebSocket `wss://fstream.binance.com`

#### 5.3 429 速率限制

**問題**: 多 Bot 運行時頻繁 429 錯誤  
**原因**: REST API 請求過於頻繁

**解決方案**: 
1. 使用 Data Hub 共享數據 (Master/Consumer 架構)
2. 優先使用 WebSocket
3. 實作指數退避

```python
# src/dydx_data_hub.py - 共享架構
class DydxDataHub:
    """
    - 第一個啟動的 bot 成為「數據主機」(master)
    - 後續 bot 成為「數據消費者」(consumer)
    - 所有 bot 透過本機 JSON 檔案共享數據
    """
```

### ✅ 最佳實踐

1. **優先使用 WebSocket** - 延遲低、無速率限制
2. **REST 僅用於初始化** - 啟動時獲取快照
3. **使用 Data Hub 架構** - 多 Bot 共享數據
4. **實作重連機制** - 自動處理斷線
5. **監控數據新鮮度** - 追蹤 `last_trade_time`

---

## 6. 速率限制注意事項

### dYdX

| 類型 | 限制 | 備註 |
|------|-----|------|
| REST API | ~50 req/10s | 使用共享限速器 |
| WebSocket | 無限制 | 推薦使用 |

### 幣安

| 類型 | 限制 | 備註 |
|------|-----|------|
| REST API | 1200 req/min | IP 限制 |
| WebSocket | 5 msg/sec | 訂閱限制 |
| 訂單 | 10 orders/sec | 帳戶限制 |

### 共享限速器實作

```python
# 使用 fcntl 檔案鎖實現跨進程共享
LOCK_FILE_PATH = Path("/tmp/dydx_data_hub.lock")

def _try_become_master(self):
    self._lock_fd = os.open(str(LOCK_FILE_PATH), os.O_RDWR | os.O_CREAT, 0o666)
    fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # 成功 = master, 失敗 = consumer
```

---

## 📁 相關檔案位置

| 檔案 | 用途 |
|------|------|
| [src/dydx_data_hub.py](../src/dydx_data_hub.py) | dYdX 數據共享中心 |
| [scripts/whale_testnet_trader.py](../scripts/whale_testnet_trader.py) | 主交易腳本 |
| [src/trading/paper_trading_engine.py](../src/trading/paper_trading_engine.py) | 模擬交易引擎 |

---

## 📝 快速複製區

### dYdX WebSocket 連接

```python
import websockets
import json

WS_URL = "wss://indexer.dydx.trade/v4/ws"  # mainnet
# WS_URL = "wss://indexer.v4testnet.dydx.exchange/v4/ws"  # testnet

async def connect():
    async with websockets.connect(WS_URL, ping_interval=30, ping_timeout=60) as ws:
        # 訂閱
        await ws.send(json.dumps({"type": "subscribe", "channel": "v4_orderbook", "id": "BTC-USD"}))
        await ws.send(json.dumps({"type": "subscribe", "channel": "v4_trades", "id": "BTC-USD"}))
        
        # 接收
        async for msg in ws:
            data = json.loads(msg)
            print(data)
```

### 幣安 WebSocket 連接

```python
import websockets
import json

WS_URL = "wss://fstream.binance.com/ws/btcusdt@aggTrade"

async def connect():
    async with websockets.connect(WS_URL) as ws:
        async for msg in ws:
            data = json.loads(msg)
            price = float(data['p'])
            qty = float(data['q'])
            is_buy = not data['m']  # m=True 是賣方主動
            print(f"{'買' if is_buy else '賣'} {qty} @ {price}")
```

---

## 7. dYdX 交易 (下單) 完整指南

### 🔧 安裝 SDK

```bash
pip install dydx-v4-client
```

### 📦 必要 Import

```python
from dydx_v4_client import MAX_CLIENT_ID, OrderFlags
from dydx_v4_client.indexer.rest.constants import OrderType
from dydx_v4_client.indexer.rest.indexer_client import IndexerClient
from dydx_v4_client.node.client import NodeClient
from dydx_v4_client.node.market import Market
from dydx_v4_client.node.builder import TxOptions  # Permissioned Keys 需要
from dydx_v4_client.wallet import Wallet
from dydx_v4_client.key_pair import KeyPair
from dydx_v4_client.network import make_mainnet, make_testnet, TESTNET
from v4_proto.dydxprotocol.clob.order_pb2 import Order
```

### 🌐 網路配置

```python
NETWORK_CONFIG = {
    "mainnet": {
        "rest_indexer": "https://indexer.dydx.trade",
        "websocket_indexer": "wss://indexer.dydx.trade/v4/ws",
        "node_grpc": "dydx-ops-grpc.kingnodes.com:443",
    },
    "testnet": {
        "rest_indexer": "https://indexer.v4testnet.dydx.exchange",
        "websocket_indexer": "wss://indexer.v4testnet.dydx.exchange/v4/ws",
        "node_grpc": "test-dydx-grpc.kingnodes.com:443",
    }
}
```

### 🔑 環境變數設定 (.env)

```bash
# 主帳戶地址 (資金所在)
DYDX_ADDRESS=dydx1xxxxx...

# API 錢包私鑰 (用於簽名)
DYDX_PRIVATE_KEY=0x123456...

# Permissioned Keys 的 Authenticator ID (可選)
DYDX_AUTHENTICATOR_ID=526

# 網路
DYDX_NETWORK=mainnet
```

---

### 📋 初始化流程

#### 1. 連接 Indexer (REST API - 讀取數據)

```python
from dydx_v4_client.indexer.rest.indexer_client import IndexerClient

indexer = IndexerClient("https://indexer.dydx.trade")

# 取得帳戶資訊
account_info = await indexer.account.get_subaccount(address, 0)
equity = float(account_info["subaccount"]["equity"])
free_collateral = float(account_info["subaccount"]["freeCollateral"])

# 取得市場資訊
market_data = await indexer.markets.get_perpetual_markets("BTC-USD")
market_info = market_data["markets"]["BTC-USD"]
oracle_price = float(market_info["oraclePrice"])

# 取得持倉
positions = await indexer.account.get_subaccount_perpetual_positions(address, 0)

# 取得訂單簿
orderbook = await indexer.markets.get_perpetual_market_orderbook("BTC-USD")
best_bid = float(orderbook["bids"][0]["price"])
best_ask = float(orderbook["asks"][0]["price"])
```

#### 2. 連接 Node (gRPC - 下單)

```python
from dydx_v4_client.node.client import NodeClient
from dydx_v4_client.network import make_mainnet

# 建立網路配置
node_config = make_mainnet(
    rest_indexer="https://indexer.dydx.trade",
    websocket_indexer="wss://indexer.dydx.trade/v4/ws",
    node_url="dydx-ops-grpc.kingnodes.com:443"
)

# 連接節點
node = await NodeClient.connect(node_config.node)
```

#### 3. 建立錢包

```python
from dydx_v4_client.wallet import Wallet
from dydx_v4_client.key_pair import KeyPair

# 從私鑰建立 KeyPair
private_key = "your_private_key_hex"  # 不含 0x 前綴
key_pair = KeyPair.from_hex(private_key)

# 取得帳戶資訊 (重要!)
account = await node.get_account(main_address)

# 建立錢包
wallet = Wallet(
    key=key_pair,
    account_number=account.account_number,
    sequence=account.sequence
)

print(f"錢包地址: {wallet.address}")
```

---

### 📤 下單方式

#### 方式 1: 市價單 (IOC - Immediate Or Cancel)

```python
from dydx_v4_client.node.market import Market
from v4_proto.dydxprotocol.clob.order_pb2 import Order
import random

# 建立 Market 物件
market = Market(market_info)

# 建立訂單 ID
client_id = random.randint(0, MAX_CLIENT_ID)
order_id = market.order_id(
    main_address,       # 主帳戶地址
    0,                  # subaccount number
    client_id,
    OrderFlags.SHORT_TERM
)

# 取得區塊高度
current_block = await node.latest_block_height()
good_til_block = current_block + 20  # 約 30 秒有效

# 建立市價單
# 做空 (SELL): 價格設為 oracle_price * 0.95 (5% 滑點保護)
# 做多 (BUY): 價格設為 oracle_price * 1.05
slippage_price = oracle_price * 0.95  # 做空

new_order = market.order(
    order_id=order_id,
    order_type=OrderType.MARKET,
    side=Order.Side.SIDE_SELL,  # 做空
    size=0.001,                 # BTC 數量
    price=slippage_price,       # 滑點保護價
    time_in_force=Order.TimeInForce.TIME_IN_FORCE_IOC,
    reduce_only=False,
    good_til_block=good_til_block,
)

# 下單
transaction = await node.place_order(
    wallet=wallet,
    order=new_order,
)

# 更新 sequence (重要!)
wallet.sequence += 1
```

#### 方式 2: 限價單 (POST_ONLY - Maker Only)

```python
# 掛單價格 (做多: 略低於 best_bid，做空: 略高於 best_ask)
limit_price = best_bid + 1.0  # 做多，掛在 bid + $1

new_order = market.order(
    order_id=order_id,
    order_type=OrderType.LIMIT,
    side=Order.Side.SIDE_BUY,
    size=0.001,
    price=limit_price,
    time_in_force=Order.TimeInForce.TIME_IN_FORCE_POST_ONLY,  # 只做 Maker
    reduce_only=False,
    good_til_block=good_til_block,
)
```

#### 方式 3: 使用 Permissioned Keys (子帳戶授權)

```python
from dydx_v4_client.node.builder import TxOptions

# 建立 TxOptions
tx_options = TxOptions(
    authenticators=[authenticator_id],  # 例如: 526
    sequence=wallet.sequence,
    account_number=wallet.account_number,
)

# 下單時傳入 tx_options
transaction = await node.place_order(
    wallet=wallet,
    order=new_order,
    tx_options=tx_options,  # 🔑 關鍵!
)
```

---

### ⚠️ 常見錯誤與解決方案

#### 7.1 Sequence 錯誤 (account sequence mismatch)

**問題**: 連續下單時出現 sequence 不匹配

**原因**: 每次成功交易後 sequence 必須 +1

**解決方案**:
```python
# 方法 1: 手動遞增
wallet.sequence += 1

# 方法 2: 從鏈上重新同步
account = await node.get_account(main_address)
wallet.sequence = account.sequence
wallet.account_number = account.account_number
```

#### 7.2 POST_ONLY 訂單被拒絕

**問題**: Maker Only 訂單被拒絕

**原因**: 掛單價格穿越了對手盤 (會被成交為 Taker)

**解決方案**:
```python
# 確保安全邊際
if side == "LONG":
    # 買單必須 < best_ask
    max_price = best_ask - 1.0  # 至少 $1 安全邊際
    limit_price = min(limit_price, max_price)
else:
    # 賣單必須 > best_bid  
    min_price = best_bid + 1.0
    limit_price = max(limit_price, min_price)
```

#### 7.3 訂單數量太小

**問題**: Order size too small

**原因**: dYdX BTC-USD 最小訂單量 0.0001 BTC

**解決方案**:
```python
MIN_ORDER_SIZE = 0.0001
size = max(size, MIN_ORDER_SIZE)
size = round(size, 4)  # 四位小數
```

#### 7.4 GTT 訂單無法使用 reduce_only

**問題**: Long-term orders (GTT) 無法設定 reduce_only

**原因**: dYdX v4 協議限制

**解決方案**:
- 止盈止損需要自己追蹤持倉數量
- 確保止盈單數量 <= 當前持倉

---

### 📊 訂單類型總結

| 類型 | time_in_force | 用途 | 手續費 |
|------|--------------|------|--------|
| **市價單** | `IOC` | 立即成交 | Taker (0.04%) |
| **限價 Maker** | `POST_ONLY` | 零滑點 | Maker (0.005%) |
| **限價 IOC** | `IOC` + LIMIT | 快速吃單 | Taker |
| **GTT 止盈** | `GTT` | 長期掛單 | 視成交方式 |

### 📁 相關檔案

| 檔案 | 用途 |
|------|------|
| [scripts/dydx_whale_trader.py](../scripts/dydx_whale_trader.py) | 完整交易 Bot 實現 |
| [dydx/dydx_place_short.py](../dydx/dydx_place_short.py) | 簡單做空範例 |
| [dydx/dydx_permissioned_short.py](../dydx/dydx_permissioned_short.py) | Permissioned Keys 範例 |

---

### 📝 完整下單範例

```python
#!/usr/bin/env python3
"""dYdX 完整下單範例"""

import asyncio
import random
from dydx_v4_client import MAX_CLIENT_ID, OrderFlags
from dydx_v4_client.indexer.rest.constants import OrderType
from dydx_v4_client.indexer.rest.indexer_client import IndexerClient
from dydx_v4_client.node.client import NodeClient
from dydx_v4_client.node.market import Market
from dydx_v4_client.wallet import Wallet
from dydx_v4_client.key_pair import KeyPair
from dydx_v4_client.network import make_mainnet
from v4_proto.dydxprotocol.clob.order_pb2 import Order


async def place_order_example():
    # === 配置 ===
    MAIN_ADDRESS = "dydx1..."  # 主帳戶
    PRIVATE_KEY = "abc123..."  # 私鑰 (無 0x)
    MARKET = "BTC-USD"
    SIZE = 0.001  # BTC
    
    # === 1. 連接 Indexer ===
    indexer = IndexerClient("https://indexer.dydx.trade")
    
    # 取得帳戶和市場資訊
    account_info = await indexer.account.get_subaccount(MAIN_ADDRESS, 0)
    market_data = await indexer.markets.get_perpetual_markets(MARKET)
    market_info = market_data["markets"][MARKET]
    oracle_price = float(market_info["oraclePrice"])
    
    print(f"帳戶權益: ${float(account_info['subaccount']['equity']):,.2f}")
    print(f"Oracle 價格: ${oracle_price:,.2f}")
    
    # === 2. 連接 Node ===
    node_config = make_mainnet(
        rest_indexer="https://indexer.dydx.trade",
        websocket_indexer="wss://indexer.dydx.trade/v4/ws",
        node_url="dydx-ops-grpc.kingnodes.com:443"
    )
    node = await NodeClient.connect(node_config.node)
    
    # === 3. 建立錢包 ===
    key_pair = KeyPair.from_hex(PRIVATE_KEY)
    account = await node.get_account(MAIN_ADDRESS)
    wallet = Wallet(
        key=key_pair,
        account_number=account.account_number,
        sequence=account.sequence
    )
    
    # === 4. 建立訂單 ===
    market = Market(market_info)
    client_id = random.randint(0, MAX_CLIENT_ID)
    order_id = market.order_id(MAIN_ADDRESS, 0, client_id, OrderFlags.SHORT_TERM)
    
    current_block = await node.latest_block_height()
    good_til_block = current_block + 20
    
    new_order = market.order(
        order_id=order_id,
        order_type=OrderType.MARKET,
        side=Order.Side.SIDE_BUY,  # 做多
        size=SIZE,
        price=oracle_price * 1.05,  # 滑點保護
        time_in_force=Order.TimeInForce.TIME_IN_FORCE_IOC,
        reduce_only=False,
        good_til_block=good_til_block,
    )
    
    # === 5. 下單 ===
    print(f"📤 下單: BUY {SIZE} BTC")
    transaction = await node.place_order(wallet=wallet, order=new_order)
    wallet.sequence += 1
    
    print(f"✅ 交易哈希: {transaction}")


if __name__ == "__main__":
    asyncio.run(place_order_example())
```

---

## 8. dYdX 交易分析工具

### 📊 analyze_dydx_real.py

這是一個分析 dYdX 真實交易記錄的工具，可以計算盈虧統計。

#### 使用方式

```bash
# 使用 .env 中的地址 (DYDX_ADDRESS)
python analyze_dydx_real.py

# 指定地址
python analyze_dydx_real.py --address dydx1ul7g6jen6ce84pjacmcwtrg2xyyfe3hgaua7fg

# 指定獲取筆數
python analyze_dydx_real.py --limit 500

# 只分析特定日期
python analyze_dydx_real.py --date 2025-12-22

# 組合使用
python analyze_dydx_real.py --date 2025-12-22 --limit 100
```

#### 輸出範例

```
📍 錢包地址: dydx1ul7g6jen...a7fg
🔗 API: https://indexer.dydx.trade/v4

💰 帳戶權益: $71.07 USDC
💵 可用保證金: $71.07 USDC

=== dYdX 真實交易分析 ===
總成交數: 1000
配對交易數: 470

📊 交易統計:
  總 PnL: -16.8366 USDC
  獲利交易: 146 (31.1%)
  虧損交易: 324 (68.9%)
  平均獲利: 0.1395 USDC
  平均虧損: -0.1148 USDC

📅 按日期統計:
  2025-12-19: -13.34 USDC | 410 trades | Win Rate: 33.7%
  2025-12-22: -3.49 USDC | 59 trades | Win Rate: 13.6%
```

#### .env 設定

```bash
# 在 .env 中設定 dYdX 地址 (使用 DYDX_ADDRESS，不是 DYDX_WALLET_ADDRESS)
DYDX_ADDRESS="dydx1ul7g6jen6ce84pjacmcwtrg2xyyfe3hgaua7fg"
```

---

## 9. 重要環境變數

| 變數名稱 | 用途 | 範例 |
|---------|------|------|
| `DYDX_ADDRESS` | dYdX 主帳戶地址 | `dydx1ul7g6jen...` |
| `DYDX_PRIVATE_KEY` | API 錢包私鑰 | `0x123456...` |
| `DYDX_AUTHENTICATOR_ID` | Permissioned Keys ID | `526` |
| `DYDX_NETWORK` | 網路 (mainnet/testnet) | `mainnet` |

> ⚠️ **注意**: 環境變數名稱是 `DYDX_ADDRESS`，不是 `DYDX_WALLET_ADDRESS`

---

## 10. 交易記錄同步工具

### 📍 背景問題

**發現**: 真實交易的 JSON 紀錄有缺漏

| 來源 | 2025-12-22 交易數 | 說明 |
|------|------------------|------|
| `whale_live_trader/*.json` | 17 筆 | 本地 JSON 紀錄 |
| dYdX API `/fills` | 59 筆 | 真實鏈上成交 |
| **缺漏** | **42 筆 (71%)** | 未記錄到 JSON |

**原因分析**: 
- `dydx_order_journal.jsonl` 顯示 `open_filled` 事件有記錄 (16 筆)
- 但 `close_filled` 事件沒有被記錄 (0 筆)
- 平倉/止損觸發時可能沒有正確呼叫記錄函數

### 🔧 解決方案: sync_dydx_trades_to_json.py

新增工具從 dYdX API 同步完整的成交紀錄：

```bash
# 同步特定日期的交易
python scripts/sync_dydx_trades_to_json.py --date 2025-12-22

# 同步最近 1000 筆成交
python scripts/sync_dydx_trades_to_json.py --limit 1000

# 指定輸出檔案
python scripts/sync_dydx_trades_to_json.py --output my_trades.json
```

**輸出位置**: `logs/dydx_real_trades/trades_YYYYMMDD.json`

### 📊 輸出格式

```json
{
  "sync_time": "2025-12-22T17:57:36",
  "total_trades": 59,
  "win_rate": 13.6,
  "total_pnl_usdc": -3.49,
  "trades": [
    {
      "trade_id": "DYDX_20251222_0000",
      "direction": "SHORT",
      "entry_time": "2025-12-22T03:20:50.045Z",
      "exit_time": "2025-12-22T03:20:55.807Z",
      "entry_price": 88695.9,
      "exit_price": 88716.0,
      "size": 0.0022,
      "pnl_usdc": -0.0442,
      "pnl_pct": -0.023,
      "hold_seconds": 5.76,
      "status": "❌ LOSS",
      "fills": [ /* 原始成交資料 */ ]
    }
  ]
}
```

### 🎯 使用建議

1. **每次交易後同步**: 執行 `sync_dydx_trades_to_json.py` 取得完整紀錄
2. **分析用**: 使用 `analyze_dydx_real.py` 快速查看統計
3. **除錯用**: 比對 `dydx_order_journal.jsonl` 和同步的 JSON 找出缺漏

### ⚠️ 待修復

建議修改 `dydx/dydx_trading_executor.py` 中的平倉邏輯，確保：
- `close_filled` 事件也記錄到 journal
- 每筆平倉也寫入 `trades_*.json`

---

## 11. 交易日誌分析工具

### 📍 新增工具

#### analyze_dydx_journal.py

分析 `logs/dydx_order_journal.jsonl` 中的交易事件：

```bash
# 分析所有記錄
python scripts/analyze_dydx_journal.py

# 只分析特定日期
python scripts/analyze_dydx_journal.py --date 2025-12-22

# 顯示交易詳情
python scripts/analyze_dydx_journal.py --trades

# 只顯示錯誤記錄
python scripts/analyze_dydx_journal.py --errors
```

**輸出範例**:
```
📊 dYdX Journal 分析報告
==================================================

📈 事件統計:
  總事件數: 223
  運行會話數: 3
  開倉成功 (open_filled): 16
  平倉成功 (close_filled): 16
  完整交易 (trade_closed): 17
  錯誤/異常: 2

💰 交易統計:
  總交易數: 17
  獲利: 2 (11.8%)
  虧損: 15
  總 PnL: $-0.4494

📤 出場原因分析:
  止損: 12 (70.6%)
  鎖利: 3 (17.6%)
  止盈: 2 (11.8%)
```

### 📝 v14.9.7 日誌增強

#### trades_*.json 格式優化

新增 `statistics` 和 `session` 欄位：

```json
{
  "trades": [...],
  "statistics": {
    "total_trades": 17,
    "wins": 2,
    "losses": 15,
    "win_rate": 11.76,
    "total_pnl_usdt": -0.4494,
    "avg_win": 0.0377,
    "avg_loss": -0.0744,
    "avg_hold_seconds": 45.2,
    "profit_factor": 0.51
  },
  "session": {
    "mode": "live",
    "dydx_sync": true,
    "initial_balance": 100.0,
    "current_balance": 99.55
  }
}
```

#### dydx_order_journal.jsonl 新增事件

| 事件類型 | 說明 | 關鍵欄位 |
|---------|------|---------|
| `trade_closed` | 完整交易平倉 | `trade_id`, `direction`, `entry_price`, `exit_price`, `net_pnl_usdt`, `exit_reason`, `is_win` |
| `open_filled` | dYdX 開倉成交 | `side`, `size`, `fill_price`, `execution_time_sec` |
| `close_filled` | dYdX 平倉成交 | `side`, `size`, `fill_price`, `pnl_pct`, `pnl_usd` |

### 🔧 除錯建議

1. **交易不對等**: 如果 `open_filled` ≠ `close_filled`，可能有掛單未成交
2. **同步問題**: 如果 `trade_closed` > `close_filled`，Paper 平倉但 dYdX 未同步
3. **錯誤追蹤**: 使用 `--errors` 參數查看所有錯誤事件

---

> 💡 **提示**: 如果遇到新問題，請更新此文件！
