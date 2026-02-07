"""
展示技術指標策略的實際應用場景
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path

# 添加專案根目錄到路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy.indicators import TechnicalIndicators


def test_real_market_scenario():
    """測試真實市場情境"""
    
    print("=" * 70)
    print("技術指標測試 - 真實市場數據模擬")
    print("=" * 70)
    
    indicators = TechnicalIndicators()
    
    # 情境 1: 突破上漲 (從震盪突破向上)
    print("\n" + "=" * 70)
    print("情境 1: 價格突破整理區間，開始上漲")
    print("=" * 70)
    
    # 生成價格數據：前 30 根震盪，後 30 根上漲
    prices = []
    
    # 震盪期 (90000 ± 100)
    for i in range(30):
        price = 90000 + 100 * np.sin(i * 0.5) + np.random.uniform(-30, 30)
        prices.append(price)
    
    # 突破上漲期
    for i in range(30):
        price = 90000 + i * 30 + np.random.uniform(-20, 20)  # 每根上漲 30
        prices.append(price)
    
    # 轉成 DataFrame
    df = pd.DataFrame({
        'open': prices,
        'high': [p * 1.0005 for p in prices],
        'low': [p * 0.9995 for p in prices],
        'close': prices,
        'volume': [100] * len(prices)
    })
    
    # 計算指標
    result = indicators.analyze_all_indicators(df)
    
    print(f"\n當前價格: {result['price']:.2f}")
    print(f"\n各指標信號:")
    print(f"  RSI ({result['indicators']['rsi']['value']:.2f}): {result['indicators']['rsi']['signal']}")
    print(f"  MA (短={result['indicators']['ma']['short']:.2f}, 長={result['indicators']['ma']['long']:.2f}): {result['indicators']['ma']['signal']}")
    print(f"  Bollinger (價格相對位置): {result['indicators']['bollinger']['signal']}")
    print(f"  SAR ({result['indicators']['sar']['value']:.2f}): {result['indicators']['sar']['signal']}")
    print(f"  StochRSI (K={result['indicators']['stochrsi']['fastk']:.2f}, D={result['indicators']['stochrsi']['fastd']:.2f}): {result['indicators']['stochrsi']['signal']}")
    print(f"  ATR 波動性: {result['indicators']['atr']['volatility']}")
    
    print(f"\n📊 綜合信號: {result['综合信號']}")
    print(f"   • 看多評分: {result['信號評分']['buy_score']}")
    print(f"   • 看空評分: {result['信號評分']['sell_score']}")
    print(f"   • 信心度: {result['信號評分']['confidence']:.2%}")
    
    # 情境 2: 超買回調 (持續上漲後)
    print("\n" + "=" * 70)
    print("情境 2: 持續上漲後，RSI 超買，可能回調")
    print("=" * 70)
    
    prices = []
    # 持續上漲
    for i in range(60):
        price = 90000 + i * 40 + np.random.uniform(-20, 20)  # 每根上漲 40
        prices.append(price)
    
    df = pd.DataFrame({
        'open': prices,
        'high': [p * 1.0005 for p in prices],
        'low': [p * 0.9995 for p in prices],
        'close': prices,
        'volume': [100] * len(prices)
    })
    
    result = indicators.analyze_all_indicators(df)
    
    print(f"\n當前價格: {result['price']:.2f}")
    print(f"\n各指標信號:")
    print(f"  RSI ({result['indicators']['rsi']['value']:.2f}): {result['indicators']['rsi']['signal']}")
    print(f"  MA (短={result['indicators']['ma']['short']:.2f}, 長={result['indicators']['ma']['long']:.2f}): {result['indicators']['ma']['signal']}")
    print(f"  Bollinger: {result['indicators']['bollinger']['signal']}")
    print(f"  SAR ({result['indicators']['sar']['value']:.2f}): {result['indicators']['sar']['signal']}")
    print(f"  StochRSI (K={result['indicators']['stochrsi']['fastk']:.2f}, D={result['indicators']['stochrsi']['fastd']:.2f}): {result['indicators']['stochrsi']['signal']}")
    
    print(f"\n📊 綜合信號: {result['综合信號']}")
    print(f"   • 看多評分: {result['信號評分']['buy_score']}")
    print(f"   • 看空評分: {result['信號評分']['sell_score']}")
    print(f"   • 信心度: {result['信號評分']['confidence']:.2%}")
    
    # 情境 3: 超賣反彈 (持續下跌後)
    print("\n" + "=" * 70)
    print("情境 3: 持續下跌後，RSI 超賣，可能反彈")
    print("=" * 70)
    
    prices = []
    # 持續下跌
    for i in range(60):
        price = 92000 - i * 35 + np.random.uniform(-20, 20)  # 每根下跌 35
        prices.append(price)
    
    df = pd.DataFrame({
        'open': prices,
        'high': [p * 1.0005 for p in prices],
        'low': [p * 0.9995 for p in prices],
        'close': prices,
        'volume': [100] * len(prices)
    })
    
    result = indicators.analyze_all_indicators(df)
    
    print(f"\n當前價格: {result['price']:.2f}")
    print(f"\n各指標信號:")
    print(f"  RSI ({result['indicators']['rsi']['value']:.2f}): {result['indicators']['rsi']['signal']}")
    print(f"  MA (短={result['indicators']['ma']['short']:.2f}, 長={result['indicators']['ma']['long']:.2f}): {result['indicators']['ma']['signal']}")
    print(f"  Bollinger: {result['indicators']['bollinger']['signal']}")
    print(f"  SAR ({result['indicators']['sar']['value']:.2f}): {result['indicators']['sar']['signal']}")
    print(f"  StochRSI (K={result['indicators']['stochrsi']['fastk']:.2f}, D={result['indicators']['stochrsi']['fastd']:.2f}): {result['indicators']['stochrsi']['signal']}")
    
    print(f"\n📊 綜合信號: {result['综合信號']}")
    print(f"   • 看多評分: {result['信號評分']['buy_score']}")
    print(f"   • 看空評分: {result['信號評分']['sell_score']}")
    print(f"   • 信心度: {result['信號評分']['confidence']:.2%}")
    
    print("\n" + "=" * 70)
    print("總結:")
    print("  技術指標適合用於:")
    print("  ✅ 確認趨勢強度（MA、SAR）")
    print("  ✅ 判斷超買超賣（RSI、StochRSI）")
    print("  ✅ 識別價格極端位置（Bollinger）")
    print("  ✅ 評估市場波動（ATR）")
    print("\n  Mode 8 策略建議:")
    print("  • 設定較低的同意門檻（1-2 票）")
    print("  • 或作為輔助參考，不作為主要攔截條件")
    print("  • 在實盤中，技術指標更適合用於「選擇進場時機」而非「阻止進場」")
    print("=" * 70)


if __name__ == "__main__":
    test_real_market_scenario()
