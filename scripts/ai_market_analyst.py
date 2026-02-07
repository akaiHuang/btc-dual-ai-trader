import json
import os
import sys
from datetime import datetime
from pathlib import Path
import time
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

def analyze_market_with_llm(context):
    """
    Uses OpenAI GPT-4o-mini to analyze market data.
    Falls back to mock analysis if no API key is found.
    """
    price = context['price']
    oi = context['oi']
    ls_ratio = context['ls_ratio']
    pressure = context['pressure']
    
    api_key = os.getenv("OPENAI_API_KEY")
    
    # Fallback if no key
    if not api_key:
        return f"""
### 🤖 AI 戰情分析報告 (模擬模式 - 未檢測到 API Key)
**時間**: {datetime.now().strftime('%H:%M:%S')}
**市場狀態**: 
- 價格: ${price:,.2f}
- 持倉量 (OI): {oi:,.0f}
- 多空比: {ls_ratio}
- 爆倉壓力: {pressure}

(請在 .env 檔案中設定 OPENAI_API_KEY 以啟用真實 AI 分析)
"""

    client = OpenAI(api_key=api_key)
    
    system_prompt = """
    You are an expert crypto market analyst specializing in Market Microstructure and Liquidity Traps.
    Your job is to identify "Traps" (e.g., Price Flat + OI Up) and "Squeezes".
    Output a concise "Battle Report" in Markdown.
    """
    
    user_prompt = f"""
    Analyze this Bitcoin market snapshot:
    - Price: ${price:,.2f}
    - Open Interest: {oi:,.0f} (Check if high relative to recent history)
    - Long/Short Ratio: {ls_ratio} ( > 2.0 is bearish/crowded longs)
    - Liquidation Pressure: {pressure}
    
    Task:
    1. Define the Regime (Accumulation, Distribution, Trap, Squeeze).
    2. Predict the next move (Liquidity Hunt?).
    3. Give a clear Strategy (Long/Short/Wait).
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3
        )
        return f"### 🤖 AI 戰情分析報告 (GPT-4o-mini)\n**時間**: {datetime.now().strftime('%H:%M:%S')}\n\n" + response.choices[0].message.content
    except Exception as e:
        return f"❌ AI 分析錯誤: {e}"


def generate_market_report():
    # 1. 讀取最新數據
    snapshot_path = Path("/Users/akaihuangm1/Desktop/btn/data/liquidation_pressure/latest_snapshot.json")
    if not snapshot_path.exists():
        print("❌ 找不到數據快照")
        return

    with open(snapshot_path, 'r') as f:
        data = json.load(f)

    # 2. 提取關鍵指標
    try:
        latest_oi = data['open_interest'][-1]
        latest_ls = data['global_long_short'][-1]
        
        oi_val = float(latest_oi['sumOpenInterest'])
        oi_usdt = float(latest_oi['sumOpenInterestValue'])
        price = oi_usdt / oi_val if oi_val > 0 else 0
        ls_ratio = float(latest_ls['longShortRatio'])
        
        # 簡單計算壓力值 (模擬)
        pressure = "HIGH" if ls_ratio > 2.0 else "NORMAL"
        
        context = {
            "price": price,
            "oi": oi_val,
            "ls_ratio": ls_ratio,
            "pressure": pressure
        }
        
        # 3. 生成 Prompt (您可以將此 Prompt 貼給 ChatGPT)
        prompt = f"""
You are a professional crypto quant trader. Analyze the following market data:
- Symbol: BTCUSDT
- Current Price: {price}
- Open Interest: {oi_val} BTC (High/Low?)
- Long/Short Ratio: {ls_ratio}
- Trend: Ranging but OI is increasing.

Task:
1. Identify the market regime (Accumulation, Distribution, Trap?).
2. Predict the likely move of Smart Money.
3. Suggest a trading action (Long, Short, Wait).
"""
        
        # 4. 執行分析
        print("🔄 AI 正在分析市場結構...")
        # time.sleep(1) # 模擬思考
        report = analyze_market_with_llm(context)
        
        print("="*60)
        print(report)
        print("="*60)
        print(f"\n📋 [System Prompt for LLM]:\n{prompt}")
        
    except Exception as e:
        print(f"❌ 分析失敗: {e}")

if __name__ == "__main__":
    while True:
        os.system('clear') # 清除螢幕
        generate_market_report()
        print("\n⏳ 等待 60 秒後更新 (按 Ctrl+C 停止)...")
        time.sleep(60)
