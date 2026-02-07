#!/usr/bin/env python3
"""
AI Dragon Advisor - 使用 Kimi-k2 進行分析的獨立顧問
專門為 M_DRAGON 設計，與 M_WOLF (GPT-4) 平行運作

使用方式：
  python scripts/ai_trading_advisor_qwen.py [hours]
  例如: python scripts/ai_trading_advisor_qwen.py 8  # 運行 8 小時後自動停止
"""

import uuid
import json
import os
import sys
import time
import argparse
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# --- 專屬檔案路徑 (Dragon) ---
STATE_FILE = "ai_dragon_state.json"
PLAN_FILE = "ai_dragon_plan.json"
MEMORY_FILE = "ai_dragon_memory.json"
MARKET_MEMORY_FILE = "ai_dragon_market_memory.json"
TEAM_CONFIG_FILE = "config/ai_dragon_config.json"
BRIDGE_FILE = "ai_dragon_bridge.json"

def load_bridge():
    if os.path.exists(BRIDGE_FILE):
        try:
            with open(BRIDGE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "ai_to_dragon": {"command": "WAIT"},
        "dragon_to_ai": {"status": "IDLE"},
        "feedback_loop": {"total_trades": 0}
    }

def save_bridge(bridge):
    bridge['last_updated'] = datetime.now().isoformat()
    with open(BRIDGE_FILE, 'w') as f:
        json.dump(bridge, f, indent=2)

def load_team_config():
    if os.path.exists(TEAM_CONFIG_FILE):
        try:
            with open(TEAM_CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}

def get_llm_client(provider="ollama"):
    """
    獲取 LLM 客戶端 (Ollama / OpenAI / Kimi K2)
    
    支援的 provider:
    - "ollama": 使用本地 Ollama (qwen3:32b 等)
    - "openai": 使用 OpenAI GPT
    - "kimi": 使用 Kimi K2 API (需要 KIMI_API_KEY)
    """
    if provider == "ollama":
        return OpenAI(
            base_url='http://localhost:11434/v1',
            api_key='ollama',
        )
    elif provider == "kimi":
        # Kimi K2 API
        api_key = os.getenv("KIMI_API_KEY")
        if not api_key:
            print("❌ 未找到 KIMI_API_KEY，請在 .env 中設定")
            return None
        return OpenAI(
            base_url='https://api.moonshot.cn/v1',
            api_key=api_key,
        )
    else:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key: return None
        return OpenAI(api_key=api_key)

# --- 復用 ai_trading_advisor.py 的核心邏輯函數 ---
# 為了避免代碼重複，我們這裡直接複製關鍵邏輯，但修改檔案路徑
# 在生產環境中應該重構為共用模組，但為了快速部署，我們保持獨立

def load_strategy_plan():
    if os.path.exists(PLAN_FILE):
        try:
            with open(PLAN_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"plan_id": str(uuid.uuid4()), "created_at": datetime.now().isoformat(), "outlook": "NEUTRAL", "phases": []}

def save_strategy_plan(plan):
    with open(PLAN_FILE, 'w') as f: json.dump(plan, f, indent=2)

def load_learning_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"stats": {"total": 0, "correct": 0, "accuracy": 0.0}, "mistakes": [], "successes": []}

def save_learning_memory(memory):
    with open(MEMORY_FILE, 'w') as f: json.dump(memory, f, indent=2)

def load_market_memory():
    if os.path.exists(MARKET_MEMORY_FILE):
        try:
            with open(MARKET_MEMORY_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"regime": {"current": "UNKNOWN"}, "strategic_bias": {"direction": "NEUTRAL"}}

def save_market_memory(memory):
    with open(MARKET_MEMORY_FILE, 'w') as f: json.dump(memory, f, indent=2)

def load_advisor_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f: return json.load(f)
    return {"last_prediction": None, "prediction_time": None, "entry_price": 0, "action": "WAIT"}

def save_advisor_state(state):
    with open(STATE_FILE, 'w') as f: json.dump(state, f, indent=2)

# --- 核心分析邏輯 (簡化版) ---

def get_agent_opinion(client, agent_name, system_prompt, user_context, model_name):
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_context}
            ],
            temperature=0.5,
            max_tokens=500
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Agent {agent_name} failed: {e}"

def run_dragon_council():
    """M_DRAGON 的專屬會議 - 使用 Kimi K2 (透過 Ollama)"""
    
    # 1. 載入配置 (使用 Ollama 運行 Kimi K2)
    config = load_team_config()
    
    # Kimi K2 透過 Ollama 運行
    client = get_llm_client("ollama")
    model_name = config.get("model_config", {}).get("model_name", "kimi-k2:1t-cloud")
    
    if not client:
        print("❌ Dragon failed to connect to Ollama (Kimi K2)")
        return

    # 2. 載入數據 (從 Bridge 讀取即時狀態)
    bridge = load_bridge()
    dragon_data = bridge.get('dragon_to_ai', {})
    
    # 模擬一些市場數據 (實際應從 shared data 讀取)
    # 這裡簡化處理，假設 Dragon 依賴 Bridge 傳來的數據
    price = dragon_data.get('entry_price', 0)
    whale_status = dragon_data.get('whale_status', {})
    micro = dragon_data.get('market_microstructure', {})
    
    # 🆕 讀取爆倉瀑布警報
    cascade_alert = dragon_data.get('liquidation_cascade', {})
    cascade_active = cascade_alert.get('active', False)
    cascade_direction = cascade_alert.get('direction', 'NONE')
    cascade_strength = cascade_alert.get('strength', 0)
    cascade_action = cascade_alert.get('recommended_action', '')
    
    print(f"🐲 Dragon Council (Ollama: {model_name}) is debating...")
    
    # 🆕 構建爆倉瀑布警告
    cascade_warning = ""
    if cascade_active:
        cascade_warning = f"""
⚠️ **LIQUIDATION CASCADE ALERT** ⚠️
- Active: YES (Strength: {cascade_strength}/100)
- Direction: {cascade_direction}
- Recommended: {cascade_action}
- CRITICAL: A liquidation cascade is in progress! This causes extreme volatility.
  - LONG_SQUEEZE = Massive long liquidations = Price FALLING
  - SHORT_SQUEEZE = Massive short liquidations = Price RISING
"""
    
    # 3. 定義 Agents
    macro_prompt = f"""
You are 'The Dragon Seer'. You use Kimi K2's wisdom to analyze the market.
Focus: Long-term Whale Trends and Market Structure.

{cascade_warning}

Input Data:
- Whale NetQty: {whale_status.get('net_qty_btc', 0)} BTC
- Whale Direction: {whale_status.get('current_direction', 'UNKNOWN')}
- Cascade Alert: Active={cascade_active}, Direction={cascade_direction}, Strength={cascade_strength}

RULES:
1. If cascade is active with strength > 60, this takes priority over whale data!
2. LONG_SQUEEZE means expect price to DROP further
3. SHORT_SQUEEZE means expect price to PUMP further
4. After cascade exhausts (strength < 30), prepare for potential reversal

Output: BULLISH/BEARISH/NEUTRAL and Why (include cascade consideration).
"""
    micro_prompt = f"""
You are 'The Dragon Claw'. You are aggressive and opportunistic.
Focus: Immediate Price Action and Liquidation Opportunities.

{cascade_warning}

Input Data:
- OBI: {micro.get('obi', 0)}
- VPIN: {micro.get('vpin', 0)}
- Cascade Status: {cascade_direction} (Strength: {cascade_strength})

RULES:
1. If cascade is active, this is a TRADING OPPORTUNITY
2. Align with cascade direction for momentum trades
3. High VPIN during cascade = extreme toxicity, be cautious
4. Watch for cascade exhaustion signals

Output: BUY/SELL/HOLD (include cascade-based reasoning).
"""
    
    context = f"Current Price: {price}"
    
    # 4. 執行辯論
    macro_opinion = get_agent_opinion(client, "Seer", macro_prompt, context, model_name)
    micro_opinion = get_agent_opinion(client, "Claw", micro_prompt, context, model_name)
    
    # 5. 最終決策
    # 🆕 加入 cascade 決策邏輯
    cascade_rule = ""
    if cascade_active and cascade_strength >= 50:
        cascade_rule = f"""
**CASCADE OVERRIDE RULE**:
- A {cascade_direction} cascade is active (Strength: {cascade_strength})
- If {cascade_direction} = LONG_SQUEEZE: Favor SHORT or HOLD (avoid LONG)
- If {cascade_direction} = SHORT_SQUEEZE: Favor LONG or HOLD (avoid SHORT)
- This rule takes priority over advisor opinions when strength > 60
"""
    
    commander_prompt = f"""
You are the Dragon Commander. Make the FINAL trading decision.

{cascade_rule}

Based on:
Seer: {macro_opinion}
Claw: {micro_opinion}

Current Cascade Status: Active={cascade_active}, Direction={cascade_direction}, Strength={cascade_strength}

DECISION RULES:
1. If both advisors agree, follow their recommendation
2. If cascade is active (strength > 50), align with cascade direction
3. If conflicting signals, prefer HOLD
4. Never fight against an active cascade with strength > 60

Decide the strategy for M_DRAGON.
Output JSON:
{{
  "command": "LONG|SHORT|HOLD",
  "direction": "BULLISH|BEARISH",
  "confidence": 0-100,
  "cascade_aligned": true/false,
  "reasoning": "..."
}}
"""
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "system", "content": commander_prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        
        # 🆕 加入止損止盈設定 (避免被覆蓋為預設值)
        result['stop_loss_pct'] = 5.0    # 5% 止損 (30x槓桿下可容忍 0.17% 價格波動)
        result['take_profit_pct'] = 10.0  # 10% 止盈
        result['leverage'] = 30           # 建議槓桿
        
        # 更新 Bridge
        bridge['ai_to_dragon'] = result
        bridge['ai_to_dragon']['timestamp'] = datetime.now().isoformat()
        save_bridge(bridge)
        
        print(f"🐲 Dragon Decision: {result.get('command')} (Conf: {result.get('confidence')}, SL: {result.get('stop_loss_pct')}%)")
        
    except Exception as e:
        print(f"❌ Dragon Council Error: {e}")


def parse_args():
    """解析命令列參數"""
    parser = argparse.ArgumentParser(description='AI Dragon Advisor - Kimi K2 Version')
    parser.add_argument('hours', nargs='?', type=float, default=0,
                        help='運行時間（小時），0 表示無限運行')
    parser.add_argument('--interval', type=int, default=15,
                        help='分析間隔（秒），預設 15 秒')
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    
    # 計算結束時間
    start_time = datetime.now()
    end_time = None
    if args.hours > 0:
        end_time = start_time + timedelta(hours=args.hours)
        print(f"⏰ 將在 {args.hours} 小時後自動停止 ({end_time.strftime('%Y-%m-%d %H:%M:%S')})")
    
    print("="*60)
    print("🐲 AI Dragon Advisor (Kimi K2 Version)")
    print("="*60)
    if end_time:
        print(f"📅 開始時間: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📅 結束時間: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    while True:
        try:
            # 檢查是否超時
            if end_time and datetime.now() >= end_time:
                elapsed = datetime.now() - start_time
                print(f"\n⏰ 運行時間已達 {elapsed.total_seconds()/3600:.2f} 小時，自動停止")
                print(f"🛑 AI Dragon Advisor 已停止 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
                break
            
            # 顯示剩餘時間
            remaining = ""
            if end_time:
                remaining_seconds = (end_time - datetime.now()).total_seconds()
                remaining_hours = remaining_seconds / 3600
                remaining = f" | 剩餘 {remaining_hours:.1f}h"
            
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🐲 Dragon Council Meeting...{remaining}")
            run_dragon_council()
            
            print(f"💤 Dragon resting... (Next council in {args.interval}s)")
            time.sleep(args.interval)
            
        except KeyboardInterrupt:
            elapsed = datetime.now() - start_time
            print(f"\n🛑 AI Dragon Advisor Stopped. (運行時間: {elapsed.total_seconds()/3600:.2f} 小時)")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)
