#!/usr/bin/env python3
"""
AI Dragon2 Advisor - 使用 Kimi-k2 但採用 Wolf 的 Prompt
專門為 M_DRAGON2 設計，目的是純粹比較 GPT-4 vs Kimi 模型差異

使用方式：
  python scripts/ai_trading_advisor_dragon2.py [hours]
  例如: python scripts/ai_trading_advisor_dragon2.py 8  # 運行 8 小時後自動停止
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

# --- 🐲2 專屬檔案路徑 (與 Wolf 完全獨立) ---
STATE_FILE = "ai_dragon2_state.json"
PLAN_FILE = "ai_dragon2_plan.json"
MEMORY_FILE = "ai_dragon2_memory.json"
MARKET_MEMORY_FILE = "ai_dragon2_market_memory.json"
TEAM_CONFIG_FILE = "config/ai_dragon2_config.json"
BRIDGE_FILE = "ai_dragon2_bridge.json"

# ================================================================
# 檔案操作函數
# ================================================================

def load_bridge():
    """載入 AI-Dragon2 橋接資料"""
    if os.path.exists(BRIDGE_FILE):
        try:
            with open(BRIDGE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "ai_to_dragon2": {"command": "WAIT"},
        "dragon2_to_ai": {"status": "IDLE"},
        "feedback_loop": {"total_trades": 0}
    }

def save_bridge(bridge):
    """儲存 AI-Dragon2 橋接資料"""
    bridge['last_updated'] = datetime.now().isoformat()
    with open(BRIDGE_FILE, 'w') as f:
        json.dump(bridge, f, indent=2)

def load_team_config():
    """載入 AI 團隊配置"""
    if os.path.exists(TEAM_CONFIG_FILE):
        try:
            with open(TEAM_CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    # 默認配置 (與 Wolf 相同結構，但使用 Ollama)
    return {
        "team_dynamics": {"current_mvp": "Macro", "debate_intensity": "HIGH"},
        "agent_profiles": {
            "macro": {"name": "The Whale Predictor", "bias": "Conservative", "weight": 1.0},
            "micro": {"name": "The Reality Checker", "bias": "Adaptive", "weight": 1.0},
            "strategist": {"name": "The Strategist", "bias": "Neutral", "weight": 1.0}
        },
        "dynamic_parameters": {"max_leverage": 50, "risk_level": "MODERATE"},
        "model_config": {"provider": "ollama", "model_name": "kimi-k2:1t-cloud"},
        "recent_adjustments": []
    }

def save_team_config(config):
    """儲存 AI 團隊配置"""
    os.makedirs(os.path.dirname(TEAM_CONFIG_FILE), exist_ok=True)
    with open(TEAM_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def load_strategy_plan():
    """載入 AI 的多階段策略計畫"""
    if os.path.exists(PLAN_FILE):
        try:
            with open(PLAN_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "plan_id": str(uuid.uuid4()),
        "created_at": datetime.now().isoformat(),
        "outlook": "NEUTRAL",
        "reasoning": "Initializing...",
        "phases": [],
        "grand_strategy": {"active": False}
    }

def save_strategy_plan(plan):
    """儲存 AI 的多階段策略計畫"""
    with open(PLAN_FILE, 'w') as f:
        json.dump(plan, f, indent=2)

def load_learning_memory():
    """載入 AI 的學習記憶"""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "stats": {"total": 0, "correct": 0, "accuracy": 0.0},
        "mistakes": [],
        "successes": []
    }

def save_learning_memory(memory):
    """儲存 AI 的學習記憶"""
    with open(MEMORY_FILE, 'w') as f:
        json.dump(memory, f, indent=2)

def load_market_memory():
    """載入市場記憶 (Regime & Bias)"""
    if os.path.exists(MARKET_MEMORY_FILE):
        try:
            with open(MARKET_MEMORY_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "regime": {"current": "UNKNOWN", "since": datetime.now().isoformat(), "volatility_score": 0.0},
        "strategic_bias": {
            "direction": "NEUTRAL", 
            "strength": 0, 
            "since": datetime.now().isoformat(),
            "pending_change": None
        },
        "short_term_memory": {"last_vpin_spike": None, "last_obi_flip": None}
    }

def save_market_memory(memory):
    """儲存市場記憶"""
    with open(MARKET_MEMORY_FILE, 'w') as f:
        json.dump(memory, f, indent=2)

def load_advisor_state():
    """載入 AI 的長期預測狀態"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"last_prediction": None, "prediction_time": None, "entry_price": 0, "action": "WAIT"}

def save_advisor_state(state):
    """儲存 AI 的長期預測狀態"""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

# ================================================================
# 數據載入函數
# ================================================================

def find_latest_pt_session():
    """找到最新的 paper trading 會話目錄"""
    pt_dir = Path("data/paper_trading")
    if not pt_dir.exists():
        return None
    sessions = sorted([d for d in pt_dir.iterdir() if d.is_dir() and d.name.startswith("pt_")])
    return sessions[-1] if sessions else None

def load_signal_diagnostics(session_path):
    """載入最新的信號診斷數據 (CSV)"""
    csv_file = session_path / "signal_diagnostics.csv"
    if not csv_file.exists():
        return None
    try:
        df = pd.read_csv(csv_file)
        return df.tail(50)
    except Exception as e:
        print(f"⚠️ 讀取 CSV 失敗: {e}")
        return None

def load_whale_flip_analysis(session_path):
    """載入最新的 Whale Flip 分析數據 (CSV)"""
    csv_file = session_path / "whale_flip_analysis.csv"
    if not csv_file.exists():
        return None
    try:
        df = pd.read_csv(csv_file)
        return df.tail(3000)
    except Exception as e:
        print(f"⚠️ 讀取 Whale Flip CSV 失敗: {e}")
        return None

def load_trading_data(session_path):
    """載入交易數據"""
    json_file = session_path / "trading_data.json"
    if not json_file.exists():
        return None
    with open(json_file, 'r') as f:
        return json.load(f)

def load_market_snapshot():
    """載入市場快照"""
    snapshot_path = Path("data/liquidation_pressure/latest_snapshot.json")
    if not snapshot_path.exists():
        return None
    with open(snapshot_path, 'r') as f:
        return json.load(f)

# ================================================================
# LLM 客戶端
# ================================================================

def get_llm_client():
    """
    獲取 LLM 客戶端 (Dragon2 使用 Ollama 運行 Kimi K2)
    與 Dragon (ai_trading_advisor_qwen.py) 相同設定
    """
    # 使用 Ollama 本地運行 Kimi K2
    return OpenAI(
        base_url='http://localhost:11434/v1',
        api_key='ollama',
    )

# ================================================================
# 分析輔助函數
# ================================================================

def evaluate_past_prediction(current_price, previous_state, memory):
    """評估過去的預測是否準確，並更新記憶"""
    if not previous_state.get("prediction_time") or not previous_state.get("entry_price"):
        return memory, None

    pred_time = datetime.fromisoformat(previous_state["prediction_time"])
    time_diff = (datetime.now() - pred_time).total_seconds() / 60.0
    
    price_diff_pct = (current_price - previous_state["entry_price"]) / previous_state["entry_price"] * 100
    
    is_emergency = False
    action = previous_state.get("action", "WAIT")
    
    if action == "LONG" and price_diff_pct < -0.3: is_emergency = True
    if action == "SHORT" and price_diff_pct > 0.3: is_emergency = True
    
    if not is_emergency and time_diff < 5 and abs(price_diff_pct) < 0.5:
        return memory, None

    result = "NEUTRAL"
    
    if action == "LONG":
        if price_diff_pct > 0.2: result = "WIN"
        elif price_diff_pct < -0.2: result = "LOSS"
    elif action == "SHORT":
        if price_diff_pct < -0.2: result = "WIN"
        elif price_diff_pct > 0.2: result = "LOSS"
    elif action == "WAIT":
        if abs(price_diff_pct) < 0.3: result = "WIN"
        else: result = "LOSS"

    if result == "NEUTRAL":
        return memory, None
        
    if is_emergency:
        result = "SEVERE_LOSS"

    memory["stats"]["total"] += 1
    if result == "WIN":
        memory["stats"]["correct"] += 1
        memory["successes"].append({
            "time": previous_state["prediction_time"],
            "action": action,
            "context_summary": previous_state.get("last_prediction", "")[:50]
        })
        if len(memory["successes"]) > 20: memory["successes"].pop(0)
    else:
        memory["mistakes"].append({
            "time": previous_state["prediction_time"],
            "action": action,
            "severity": "HIGH" if result == "SEVERE_LOSS" else "NORMAL",
            "reason": f"Price moved {price_diff_pct:.2f}% against prediction",
            "context_summary": previous_state.get("last_prediction", "")[:50]
        })
        if len(memory["mistakes"]) > 20: memory["mistakes"].pop(0)

    memory["stats"]["accuracy"] = round(memory["stats"]["correct"] / memory["stats"]["total"] * 100, 2)
    
    previous_state["prediction_time"] = None 
    save_advisor_state(previous_state)
    save_learning_memory(memory)
    
    return memory, result

def extract_micro_features(signals_df):
    """提取微觀特徵"""
    if signals_df is None or signals_df.empty:
        return {}
    
    recent = signals_df.tail(20)
    features = {"vpin_spike": False, "obi_flip": False, "avg_vpin": 0.0, "avg_obi": 0.0}
    
    if 'vpin' in recent.columns:
        vpin_values = recent['vpin'].astype(float)
        features["avg_vpin"] = vpin_values.mean()
        if vpin_values.max() - vpin_values.min() > 0.3 and vpin_values.iloc[-1] > 0.7:
            features["vpin_spike"] = True
            
    if 'obi' in recent.columns:
        obi_values = recent['obi'].astype(float)
        features["avg_obi"] = obi_values.mean()
        if (obi_values.max() > 0.2 and obi_values.min() < -0.2):
            features["obi_flip"] = True
            
    return features

def update_market_regime(signals_df, market_memory):
    """更新市場體制"""
    if signals_df is None or signals_df.empty:
        return "UNKNOWN", market_memory
    
    recent = signals_df.tail(50)
    vpin_std = recent['vpin'].std() if 'vpin' in recent.columns else 0
    obi_abs_mean = recent['obi'].abs().mean() if 'obi' in recent.columns else 0
    
    volatility_score = float(vpin_std) if not pd.isna(vpin_std) else 0.0
    trend_score = float(obi_abs_mean) if not pd.isna(obi_abs_mean) else 0.0
    
    current_regime = "RANGING"
    if volatility_score > 0.1 or trend_score > 0.5:
        current_regime = "VOLATILE"
    elif trend_score > 0.3:
        current_regime = "TRENDING"
        
    last_regime = market_memory["regime"].get("current", "UNKNOWN")
    if current_regime != last_regime:
        market_memory["regime"]["current"] = current_regime
        market_memory["regime"]["since"] = datetime.now().isoformat()
    
    market_memory["regime"]["volatility_score"] = volatility_score
    return current_regime, market_memory

def summarize_mode_performance(trading_data):
    """總結其他模式的表現"""
    if not trading_data or 'modes' not in trading_data:
        return "No trading data available."
    
    modes = trading_data['modes']
    summary = []
    trend_pnl = 0
    mean_pnl = 0
    
    for name, data in modes.items():
        pnl = data.get('pnl_usdt', 0)
        if pnl != 0:
            summary.append(f"{name}: ${pnl:.2f}")
            
    return f"Details: {', '.join(summary[:5])}"

def get_agent_opinion(client, agent_name, system_prompt, user_context, model_name):
    """獲取單個 Agent 的意見"""
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

# ================================================================
# 核心：戰略委員會會議 (與 Wolf 完全相同的 Prompt)
# ================================================================

def run_council_meeting(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state):
    """召開 AI 戰略委員會會議 (與 Wolf 完全相同的 Prompt，但使用 Ollama Kimi K2 模型)"""
    
    team_config = load_team_config()
    # 🔧 使用 Ollama 的 kimi-k2:1t-cloud 模型 (與 Dragon 相同)
    model_name = team_config.get("model_config", {}).get("model_name", "kimi-k2:1t-cloud")
    
    client = get_llm_client()
    if not client: 
        return "❌ Ollama LLM Client Init Failed (確保 Ollama 正在運行)"
    
    print(f"🐲2 Dragon2 Council (Ollama: {model_name}) using Wolf's Prompt...")
    
    # 載入記憶與計畫
    current_plan = load_strategy_plan()
    learning_memory = load_learning_memory()
    market_memory = load_market_memory()
    
    # --- 從 Dragon Bridge 讀取即時數據 ---
    # 注意：Dragon2 讀取 Dragon 的 Bridge (因為共用 Kimi advisor)
    dragon_bridge_file = "ai_dragon_bridge.json"
    if os.path.exists(dragon_bridge_file):
        with open(dragon_bridge_file, 'r') as f:
            dragon_bridge = json.load(f)
    else:
        dragon_bridge = {}
    
    dragon_data = dragon_bridge.get('dragon_to_ai', {})
    rt_whale = dragon_data.get('whale_status', {})
    rt_micro = dragon_data.get('market_microstructure', {})
    
    # 讀取 Cascade Alert
    cascade_alert = dragon_data.get('cascade_alert', {})
    cascade_active = cascade_alert.get('active', False)
    cascade_direction = cascade_alert.get('direction', 'NONE')
    cascade_strength = cascade_alert.get('strength', 0)
    cascade_warning = cascade_alert.get('warning', '')
    cascade_suggestion = cascade_alert.get('suggested_action', '')
    
    # --- 數據準備 ---
    try:
        latest_oi = market_snapshot['open_interest'][-1]
        latest_ls = market_snapshot['global_long_short'][-1]
        oi_val = float(latest_oi['sumOpenInterest'])
        oi_usdt = float(latest_oi['sumOpenInterestValue'])
        price = oi_usdt / oi_val if oi_val > 0 else 0
        ls_ratio = float(latest_ls['longShortRatio'])
        liq_pressure = market_snapshot.get('liquidation_pressure', {})
        long_liq = liq_pressure.get('L_long_liq', 0)
        short_liq = liq_pressure.get('L_short_liq', 0)
        
        taker_ratio = liq_pressure.get('taker_ratio', {})
        taker_buy_sell_ratio = taker_ratio.get('ratio', 1.0) if isinstance(taker_ratio, dict) else 1.0
        oi_change_pct = liq_pressure.get('oi_change_pct', 0)
        if isinstance(oi_change_pct, list):
            oi_change_pct = oi_change_pct[-1] if oi_change_pct else 0
        
        funding_rate_data = market_snapshot.get('funding_rate', [])
        if isinstance(funding_rate_data, list) and funding_rate_data:
            funding_rate = float(funding_rate_data[-1].get('fundingRate', 0))
        else:
            funding_rate = 0
    except:
        price = 0; oi_val = 0; ls_ratio = 0; long_liq = 0; short_liq = 0
        taker_buy_sell_ratio = 1.0; oi_change_pct = 0; funding_rate = 0
    
    # 自我評估
    learning_memory, eval_result = evaluate_past_prediction(price, previous_state, learning_memory)
    if eval_result:
        print(f"   🎓 [Self-Learning] Previous prediction: {eval_result}. Accuracy: {learning_memory['stats']['accuracy']}%")
    
    # 特徵提取
    micro_features = extract_micro_features(signals_df)
    market_regime, market_memory = update_market_regime(signals_df, market_memory)
    mode_performance_summary = summarize_mode_performance(trading_data)
    
    signal_summary = ""
    if signals_df is not None and not signals_df.empty:
        latest_signals = signals_df.tail(5)[['mode', 'action', 'reason', 'signal_score', 'obi', 'vpin']].to_dict('records')
        signal_summary = json.dumps(latest_signals, ensure_ascii=False)

    whale_short_term = {"net_qty": 0, "dominance": 0}
    whale_long_term = {"net_qty": 0, "trend": "NEUTRAL"}
    if whale_flip_df is not None and not whale_flip_df.empty:
        recent_whales = whale_flip_df.tail(5)
        if 'net_qty' in recent_whales.columns:
            whale_short_term["net_qty"] = recent_whales['net_qty'].sum()
            whale_short_term["dominance"] = recent_whales['dominance'].mean()
        long_term_whales = whale_flip_df.tail(300)
        if 'net_qty' in long_term_whales.columns:
            net_qty_sum = long_term_whales['net_qty'].sum()
            whale_long_term["net_qty"] = net_qty_sum
            if net_qty_sum > 200: whale_long_term["trend"] = "STRONG_ACCUMULATION"
            elif net_qty_sum > 50: whale_long_term["trend"] = "MILD_ACCUMULATION"
            elif net_qty_sum < -200: whale_long_term["trend"] = "STRONG_DISTRIBUTION"
            elif net_qty_sum < -50: whale_long_term["trend"] = "MILD_DISTRIBUTION"

    # --- 定義 Agents (與 Wolf 完全相同的 Prompt) ---
    profiles = team_config.get("agent_profiles", {})
    grand_strategy = current_plan.get("grand_strategy", {"active": False})
    
    # 🆕 構建 Cascade Alert 警告訊息
    cascade_warning_msg = ""
    if cascade_active:
        cascade_warning_msg = f"""
⚠️ **LIQUIDATION CASCADE ALERT** ⚠️
- Status: ACTIVE (Strength: {cascade_strength}/100)
- Direction: {cascade_direction}
- Warning: {cascade_warning}
- Suggested Action: {cascade_suggestion}
- RULE: When cascade is active, expect EXTREME volatility.
"""
    
    # 1. 👴 The Macro Seer (與 Wolf 相同)
    p_macro = profiles.get("macro", {})
    macro_prompt = f"""
You are '{p_macro.get('name', 'The Whale Predictor')}'. Your Role: **Grand Strategist**.
Your Focus: **Long-term Vision (1-5 Hours)**.
Your Bias: {p_macro.get('bias', 'Proactive')}.
Weight in Council: {p_macro.get('weight', 1.0)}.

{cascade_warning_msg}

Your Goal:
1. Analyze the "Big Picture" using Whale Trends (4H) and Other Modes' Performance.
2. Formulate a **Grand Strategy** for the next 1-5 hours.
3. **CRITICAL RULE**: If 'REAL-TIME WHALE STATUS' contradicts 'Whale Trend (4H)', you MUST trust the REAL-TIME status.
4. **LIQUIDATION CASCADE RULE**: If a cascade is active, factor it into your strategy.

Input Data:
- **REAL-TIME WHALE STATUS**: Direction={rt_whale.get('current_direction')}, NetQty={rt_whale.get('net_qty_btc', 0)} BTC, Dominance={rt_whale.get('dominance', 0)}
- **CASCADE ALERT**: Active={cascade_active}, Direction={cascade_direction}, Strength={cascade_strength}
- Whale Trend (4H): {whale_long_term['trend']} (Net: {whale_long_term['net_qty']:.2f} BTC)
- Other Modes: {mode_performance_summary}
- Market Regime: {market_regime}

Output:
- Grand Strategy Direction: BULLISH / BEARISH / NEUTRAL
- Target Duration: 1-5 hours
- Key Thesis: Why?
- Invalidation Level: Price level that proves you wrong.
"""
    macro_context = f"""
Current Price: {price}
LS Ratio: {ls_ratio}
Liquidation Pressure: Long={long_liq}, Short={short_liq}
Taker Buy/Sell Ratio: {taker_buy_sell_ratio:.4f}
OI Change: {oi_change_pct:+.2f}%
Funding Rate: {funding_rate:.6f}
Current Grand Strategy: {json.dumps(grand_strategy)}
"""

    # 2. ⚡ The Micro Navigator (與 Wolf 相同)
    p_micro = profiles.get("micro", {})
    micro_cascade_msg = ""
    if cascade_active:
        micro_cascade_msg = f"""
🚨 **LIVE LIQUIDATION CASCADE** 🚨
- Strength: {cascade_strength}/100 ({cascade_direction})
- Total Liquidated: ${cascade_alert.get('total_liquidated_usd', 0):,.0f}
"""
    
    micro_prompt = f"""
You are '{p_micro.get('name', 'The Reality Checker')}'. Your Role: **Tactical Navigator**.
Your Focus: **Validate the Grand Strategy**.
Your Bias: {p_micro.get('bias', 'Adaptive')}.
Weight in Council: {p_micro.get('weight', 1.0)}.

{micro_cascade_msg}

Your Goal:
1. Check if the current price action supports or threatens the Grand Strategy.
2. **Avoid Flip-Flopping**: Only recommend aborting the plan if there is a MAJOR structural break.
3. If the plan is working (or just noise), recommend HOLD or ADD.

Input Data:
- Grand Strategy: {json.dumps(grand_strategy)}
- **REAL-TIME MICROSTRUCTURE**: OBI={rt_micro.get('obi', 0):.2f}, VPIN={rt_micro.get('vpin', 0):.2f}
- Whale Activity (15m): Net {whale_short_term['net_qty']:.2f} BTC
- Micro Features: VPIN={micro_features.get('avg_vpin', 0):.2f}, OBI={micro_features.get('avg_obi', 0):.2f}

Output:
- Status: ON_TRACK / MINOR_DEVIATION / MAJOR_THREAT
- Recommendation: CONTINUE / PAUSE / ABORT
- Reasoning: Specific micro-structure evidence.
"""
    micro_context = f"Recent Signals: {signal_summary}\nCurrent Price: {price}"

    # 3. ⚖️ The Strategist (與 Wolf 相同)
    p_strat = profiles.get("strategist", {})
    hybrid_prompt = f"""
You are '{p_strat.get('name', 'The Strategist')}'. Risk Manager.
Your Focus: **Execution Quality & Discipline**.
Your Bias: {p_strat.get('bias', 'Neutral')}.
Weight in Council: {p_strat.get('weight', 1.0)}.

Your Goal:
1. Evaluate if we are changing goals too often.
2. Ensure we stick to the plan unless invalidated.
3. Recommend position sizing and risk management.

Input Data:
- Grand Strategy: {json.dumps(grand_strategy)}
- Learning Stats: Accuracy {learning_memory['stats']['accuracy']}%, Total {learning_memory['stats']['total']} predictions
- Recent Mistakes: {len(learning_memory['mistakes'])}
- Recent Successes: {len(learning_memory['successes'])}

Output:
- Discipline Score: 1-10
- Risk Level: LOW / MEDIUM / HIGH
- Recommendation: HOLD_COURSE / REDUCE_SIZE / INCREASE_SIZE / ABORT
"""
    hybrid_context = f"Current Price: {price}\nMarket Regime: {market_regime}"

    # 執行辯論
    macro_opinion = get_agent_opinion(client, "Macro", macro_prompt, macro_context, model_name)
    micro_opinion = get_agent_opinion(client, "Micro", micro_prompt, micro_context, model_name)
    hybrid_opinion = get_agent_opinion(client, "Strategist", hybrid_prompt, hybrid_context, model_name)
    
    print(f"   👴 Macro: {macro_opinion[:60]}...")
    print(f"   ⚡ Micro: {micro_opinion[:60]}...")
    print(f"   ⚖️ Strat: {hybrid_opinion[:60]}...")
    
    # --- 最終 Commander 決策 (與 Wolf 完全相同) ---
    cascade_commander_msg = ""
    if cascade_active:
        cascade_commander_msg = f"""
🚨 **ACTIVE LIQUIDATION CASCADE** 🚨
- Direction: {cascade_direction}
- Strength: {cascade_strength}/100
- Warning: {cascade_warning}

**CASCADE DECISION RULES**:
- If cascade is LONG_SQUEEZE (strength > 50): Favor SHORT or HOLD
- If cascade is SHORT_SQUEEZE (strength > 50): Favor LONG or HOLD
- NEVER fight against an active cascade with strength > 60
"""
    
    commander_prompt = f"""
You are 'The Supreme Commander'. You make the FINAL DECISION based on a Long-Term Vision.

**OBJECTIVE**: Execute a coherent strategy over 1-5 hours. Avoid frequent direction changes.

{cascade_commander_msg}

**SANITY CHECK (MANDATORY)**:
- If Real-Time Whale NetQty is NEGATIVE (e.g., <-5 BTC), you CANNOT be BULLISH.
- If Real-Time Whale NetQty is POSITIVE (e.g., >+5 BTC), you CANNOT be BEARISH.
- **CASCADE OVERRIDE**: If cascade strength > 70, the cascade direction takes priority!

Current Grand Strategy:
{json.dumps(grand_strategy)}

Advisor Opinions:
[Macro]: {macro_opinion}
[Micro]: {micro_opinion}
[Strategist]: {hybrid_opinion}

**CURRENT CASCADE STATUS**: Active={cascade_active}, Direction={cascade_direction}, Strength={cascade_strength}

**OUTPUT FORMAT (JSON)**:
{{
  "strategic_bias": "BULLISH|BEARISH",
  "tactical_action": "LONG|SHORT|HOLD|ADD_LONG|ADD_SHORT|CUT_LOSS",
  "recommended_leverage": 1-50,
  "conviction_score": 50-100,
  "whale_reversal_price": 87500,
  "cascade_aligned": true/false,
  "grand_strategy_update": {{
     "active": true,
     "direction": "...",
     "thesis": "...",
     "target_duration_hours": 3,
     "start_time": "{datetime.now().isoformat()}", 
     "invalidation_price": 0
  }},
  "analysis": "Reasoning..."
}}
"""
    commander_context = f"""
Current Price: {price}
Market Regime: {market_regime}
REAL-TIME WHALE: {rt_whale}
REAL-TIME MICRO: {rt_micro}
"""

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": commander_prompt},
                {"role": "user", "content": commander_context}
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        result = json.loads(content)
        
        analysis_text = result.get('analysis') or "No analysis provided"
        
        new_state = {
            "last_prediction": analysis_text[:100] + "...", 
            "prediction_time": datetime.now().isoformat(),
            "entry_price": price,
            "action": result.get('tactical_action') or 'WAIT',
            "strategic_bias": result.get('strategic_bias') or 'NEUTRAL',
            "confidence": result.get('conviction_score') or 50,
            "whale_reversal_price": result.get('whale_reversal_price', 0),
            "full_analysis": f"👑 COMMANDER DECISION:\n{analysis_text}"
        }
        save_advisor_state(new_state)
        
        # 更新 Dragon2 專屬 Bridge
        bridge = load_bridge()
        
        bridge['ai_to_dragon2'] = {
            "command": result.get('tactical_action') or 'WAIT',
            "direction": result.get('strategic_bias') or 'NEUTRAL',
            "confidence": result.get('conviction_score') or 50,
            "leverage": result.get('recommended_leverage') or 1,
            "whale_reversal_price": result.get('whale_reversal_price', 0),
            "take_profit_pct": 10.0,
            "stop_loss_pct": 5.0,
            "reasoning": analysis_text[:200],
            "timestamp": datetime.now().isoformat()
        }
        save_bridge(bridge)
        
        # 同時寫入 Dragon Bridge (因為 paper_trading 讀取的是 dragon bridge)
        # 這樣 M_DRAGON2 才能讀取到指令
        dragon_bridge = {}
        if os.path.exists("ai_dragon_bridge.json"):
            with open("ai_dragon_bridge.json", 'r') as f:
                dragon_bridge = json.load(f)
        
        dragon_bridge['ai_to_dragon2'] = bridge['ai_to_dragon2']
        dragon_bridge['last_updated'] = datetime.now().isoformat()
        
        with open("ai_dragon_bridge.json", 'w') as f:
            json.dump(dragon_bridge, f, indent=2)
        
        # 更新 Grand Strategy
        if 'grand_strategy_update' in result:
            current_plan['grand_strategy'] = result['grand_strategy_update']
            save_strategy_plan(current_plan)
        
        save_market_memory(market_memory)
        
        analysis_preview = (result.get('analysis') or "No analysis")[:120]
        return f"[{result.get('strategic_bias')} | {result.get('tactical_action')} (Lev x{result.get('recommended_leverage', 1)})] {analysis_preview}..."

    except Exception as e:
        return f"❌ Commander failed: {e}"

# ================================================================
# 主程式
# ================================================================

def parse_args():
    parser = argparse.ArgumentParser(description='AI Dragon2 Advisor - Kimi with Wolf Prompt')
    parser.add_argument('hours', nargs='?', type=float, default=0,
                        help='運行時間（小時），0 表示無限運行')
    parser.add_argument('--interval', type=int, default=15,
                        help='分析間隔（秒），預設 15 秒')
    return parser.parse_args()


def main():
    args = parse_args()
    
    start_time = datetime.now()
    end_time = None
    if args.hours > 0:
        end_time = start_time + timedelta(hours=args.hours)
        print(f"⏰ 將在 {args.hours} 小時後自動停止 ({end_time.strftime('%Y-%m-%d %H:%M:%S')})")
    
    print("="*60)
    print("🐲2 AI Dragon2 Advisor (Kimi + Wolf Prompt)")
    print("   目的: 純粹比較 GPT-4 vs Kimi 模型差異")
    print("="*60)
    if end_time:
        print(f"📅 開始時間: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📅 結束時間: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    # 初始化配置
    team_config = load_team_config()
    save_team_config(team_config)
    
    while True:
        try:
            if end_time and datetime.now() >= end_time:
                elapsed = datetime.now() - start_time
                print(f"\n⏰ 運行時間已達 {elapsed.total_seconds()/3600:.2f} 小時，自動停止")
                break
            
            session_path = find_latest_pt_session()
            if not session_path:
                print("❌ No session found.")
                time.sleep(60)
                continue
                
            trading_data = load_trading_data(session_path)
            market_snapshot = load_market_snapshot()
            signals_df = load_signal_diagnostics(session_path)
            whale_flip_df = load_whale_flip_analysis(session_path)
            prev_state = load_advisor_state()
            
            current_time = datetime.now().strftime('%H:%M:%S')
            remaining = ""
            if end_time:
                remaining_seconds = (end_time - datetime.now()).total_seconds()
                remaining_hours = remaining_seconds / 3600
                remaining = f" | 剩餘 {remaining_hours:.1f}h"
            
            print(f"\n[{current_time}] 🐲2 Dragon2 Council Meeting...{remaining}")
            analysis = run_council_meeting(trading_data, market_snapshot, signals_df, whale_flip_df, prev_state)
            
            print("\n" + analysis)
            print("\n" + "-"*60)
            print(f"💤 Dragon2 resting... (Next council in {args.interval}s)")
            
            time.sleep(args.interval)
            
        except KeyboardInterrupt:
            elapsed = datetime.now() - start_time
            print(f"\n🛑 AI Dragon2 Advisor Stopped. (運行時間: {elapsed.total_seconds()/3600:.2f} 小時)")
            break
        except Exception as e:
            print(f"⚠️ Error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)

if __name__ == "__main__":
    main()
