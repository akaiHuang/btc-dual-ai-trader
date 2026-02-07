"""
dYdX 短線分析工具
==================
分析 BTC-USD 未來 15 分鐘的交易方向
"""

import os
import asyncio
from dotenv import load_dotenv
from datetime import datetime
import statistics

load_dotenv()

from dydx_v4_client.indexer.rest.indexer_client import IndexerClient

# 網路設定
NETWORK_CONFIG = {
    "mainnet": "https://indexer.dydx.trade",
    "testnet": "https://indexer.v4testnet.dydx.exchange",
}


async def analyze_market():
    """分析 BTC-USD 市場"""
    network = os.getenv("DYDX_NETWORK", "mainnet")
    client = IndexerClient(NETWORK_CONFIG[network])
    
    print("=" * 60)
    print(f"🔍 BTC-USD 15分鐘短線分析 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # 1. 取得市場基本資訊
    print("\n📊 【市場概況】")
    market_data = await client.markets.get_perpetual_markets("BTC-USD")
    market = market_data.get("markets", {}).get("BTC-USD", {})
    
    oracle_price = float(market.get("oraclePrice", 0))
    index_price = float(market.get("indexPrice", 0))
    next_funding = float(market.get("nextFundingRate", 0))
    open_interest = float(market.get("openInterest", 0))
    volume_24h = float(market.get("volume24H", 0))
    
    print(f"   Oracle 價格: ${oracle_price:,.2f}")
    print(f"   指數價格: ${index_price:,.2f}")
    print(f"   資金費率: {next_funding * 100:.4f}%")
    print(f"   24h 交易量: ${volume_24h:,.0f}")
    print(f"   未平倉量: ${open_interest:,.2f}")
    
    # 2. 取得訂單簿分析
    print("\n📖 【訂單簿分析】")
    orderbook = await client.markets.get_perpetual_market_orderbook("BTC-USD")
    
    bids = orderbook.get("bids", [])[:20]  # 前20檔買單
    asks = orderbook.get("asks", [])[:20]  # 前20檔賣單
    
    bid_volume = sum(float(b["size"]) for b in bids)
    ask_volume = sum(float(a["size"]) for a in asks)
    
    best_bid = float(bids[0]["price"]) if bids else 0
    best_ask = float(asks[0]["price"]) if asks else 0
    spread = best_ask - best_bid
    spread_pct = (spread / best_bid) * 100 if best_bid > 0 else 0
    
    bid_ask_ratio = bid_volume / ask_volume if ask_volume > 0 else 1
    
    print(f"   最佳買價: ${best_bid:,.2f}")
    print(f"   最佳賣價: ${best_ask:,.2f}")
    print(f"   價差: ${spread:.2f} ({spread_pct:.4f}%)")
    print(f"   買單量 (前20檔): {bid_volume:.4f} BTC")
    print(f"   賣單量 (前20檔): {ask_volume:.4f} BTC")
    print(f"   買賣比: {bid_ask_ratio:.2f}")
    
    if bid_ask_ratio > 1.2:
        orderbook_signal = "🟢 買盤較強"
    elif bid_ask_ratio < 0.8:
        orderbook_signal = "🔴 賣盤較強"
    else:
        orderbook_signal = "⚪ 買賣均衡"
    print(f"   訂單簿信號: {orderbook_signal}")
    
    # 3. 取得最近交易分析
    print("\n📈 【成交分析】")
    trades = await client.markets.get_perpetual_market_trades("BTC-USD", limit=100)
    trade_list = trades.get("trades", [])
    
    buy_volume = sum(float(t["size"]) for t in trade_list if t["side"] == "BUY")
    sell_volume = sum(float(t["size"]) for t in trade_list if t["side"] == "SELL")
    
    buy_count = sum(1 for t in trade_list if t["side"] == "BUY")
    sell_count = sum(1 for t in trade_list if t["side"] == "SELL")
    
    trade_prices = [float(t["price"]) for t in trade_list]
    avg_price = statistics.mean(trade_prices) if trade_prices else 0
    price_std = statistics.stdev(trade_prices) if len(trade_prices) > 1 else 0
    
    latest_price = trade_prices[0] if trade_prices else 0
    oldest_price = trade_prices[-1] if trade_prices else 0
    price_change = latest_price - oldest_price
    price_change_pct = (price_change / oldest_price) * 100 if oldest_price > 0 else 0
    
    print(f"   最近100筆成交:")
    print(f"   - 買入: {buy_count} 筆 ({buy_volume:.4f} BTC)")
    print(f"   - 賣出: {sell_count} 筆 ({sell_volume:.4f} BTC)")
    print(f"   - 平均價: ${avg_price:,.2f}")
    print(f"   - 價格波動: ±${price_std:.2f}")
    print(f"   - 短期趨勢: {price_change_pct:+.3f}%")
    
    buy_sell_ratio = buy_volume / sell_volume if sell_volume > 0 else 1
    if buy_sell_ratio > 1.3:
        trade_signal = "🟢 主動買入較多"
    elif buy_sell_ratio < 0.7:
        trade_signal = "🔴 主動賣出較多"
    else:
        trade_signal = "⚪ 買賣平衡"
    print(f"   成交信號: {trade_signal}")
    
    # 4. 取得 K 線分析 (15分鐘線)
    print("\n🕯️ 【K線分析 - 15分鐘】")
    candles = await client.markets.get_perpetual_market_candles(
        "BTC-USD",
        resolution="15MINS",
        limit=10
    )
    candle_list = candles.get("candles", [])
    
    if candle_list:
        # 最新 K 線
        latest = candle_list[0]
        open_p = float(latest["open"])
        high_p = float(latest["high"])
        low_p = float(latest["low"])
        close_p = float(latest["close"])
        volume = float(latest.get("baseTokenVolume", 0))
        
        print(f"   當前 K 線:")
        print(f"   - 開: ${open_p:,.2f} → 收: ${close_p:,.2f}")
        print(f"   - 高: ${high_p:,.2f} / 低: ${low_p:,.2f}")
        print(f"   - 成交量: {volume:.4f} BTC")
        
        # K 線形態
        body = close_p - open_p
        upper_shadow = high_p - max(open_p, close_p)
        lower_shadow = min(open_p, close_p) - low_p
        
        if body > 0:
            candle_type = "🟢 陽線"
        else:
            candle_type = "🔴 陰線"
        
        print(f"   - 類型: {candle_type} (實體 ${abs(body):.2f})")
        
        # 計算趨勢 (最近幾根 K 線)
        closes = [float(c["close"]) for c in candle_list[:5]]
        if len(closes) >= 3:
            trend = closes[0] - closes[-1]
            trend_pct = (trend / closes[-1]) * 100 if closes[-1] > 0 else 0
            
            if trend_pct > 0.1:
                trend_signal = f"🟢 上漲趨勢 ({trend_pct:+.2f}%)"
            elif trend_pct < -0.1:
                trend_signal = f"🔴 下跌趨勢 ({trend_pct:+.2f}%)"
            else:
                trend_signal = f"⚪ 盤整 ({trend_pct:+.2f}%)"
            print(f"   - 近期趨勢: {trend_signal}")
    
    # 5. 資金費率分析
    print("\n💰 【資金費率分析】")
    if next_funding > 0.0001:
        funding_signal = "🔴 多方付費給空方 (做空有利)"
    elif next_funding < -0.0001:
        funding_signal = "🟢 空方付費給多方 (做多有利)"
    else:
        funding_signal = "⚪ 資金費率中性"
    print(f"   {funding_signal}")
    
    # 6. 綜合分析
    print("\n" + "=" * 60)
    print("🎯 【15分鐘交易建議】")
    print("=" * 60)
    
    # 計算綜合分數
    score = 0
    signals = []
    
    # 訂單簿信號
    if bid_ask_ratio > 1.2:
        score += 1
        signals.append("訂單簿買盤強 (+1)")
    elif bid_ask_ratio < 0.8:
        score -= 1
        signals.append("訂單簿賣盤強 (-1)")
    
    # 成交信號
    if buy_sell_ratio > 1.3:
        score += 1
        signals.append("主動買入多 (+1)")
    elif buy_sell_ratio < 0.7:
        score -= 1
        signals.append("主動賣出多 (-1)")
    
    # 趨勢信號
    if price_change_pct > 0.05:
        score += 1
        signals.append("短期上漲 (+1)")
    elif price_change_pct < -0.05:
        score -= 1
        signals.append("短期下跌 (-1)")
    
    # 資金費率
    if next_funding < -0.0001:
        score += 0.5
        signals.append("資金費率利多 (+0.5)")
    elif next_funding > 0.0001:
        score -= 0.5
        signals.append("資金費率利空 (-0.5)")
    
    # K 線趨勢
    if candle_list and len(closes) >= 3:
        if trend_pct > 0.1:
            score += 1
            signals.append("K線上漲趨勢 (+1)")
        elif trend_pct < -0.1:
            score -= 1
            signals.append("K線下跌趨勢 (-1)")
    
    print(f"\n   📋 信號統計:")
    for s in signals:
        print(f"      • {s}")
    
    print(f"\n   📊 綜合評分: {score:+.1f}")
    
    if score >= 2:
        recommendation = "🟢 建議做多 (LONG)"
        confidence = "高"
    elif score >= 1:
        recommendation = "🟢 傾向做多 (LONG)"
        confidence = "中"
    elif score <= -2:
        recommendation = "🔴 建議做空 (SHORT)"
        confidence = "高"
    elif score <= -1:
        recommendation = "🔴 傾向做空 (SHORT)"
        confidence = "中"
    else:
        recommendation = "⚪ 觀望，暫不建議交易"
        confidence = "低"
    
    print(f"\n   🎯 交易建議: {recommendation}")
    print(f"   📈 信心度: {confidence}")
    
    # 建議價位
    if score > 0:
        entry = best_bid
        stop_loss = entry * 0.995  # 0.5% 止損
        take_profit = entry * 1.01  # 1% 止盈
        print(f"\n   💡 建議價位 (做多):")
        print(f"      進場: ${entry:,.2f}")
        print(f"      止損: ${stop_loss:,.2f} (-0.5%)")
        print(f"      止盈: ${take_profit:,.2f} (+1%)")
    elif score < 0:
        entry = best_ask
        stop_loss = entry * 1.005  # 0.5% 止損
        take_profit = entry * 0.99  # 1% 止盈
        print(f"\n   💡 建議價位 (做空):")
        print(f"      進場: ${entry:,.2f}")
        print(f"      止損: ${stop_loss:,.2f} (+0.5%)")
        print(f"      止盈: ${take_profit:,.2f} (-1%)")
    
    print("\n" + "=" * 60)
    print("⚠️  風險提示: 以上分析僅供參考，加密貨幣交易風險極高！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(analyze_market())
