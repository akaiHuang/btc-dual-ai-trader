#!/usr/bin/env python3
"""
AI Trading Advisor - 分析當前交易狀態並提供獲利建議
讀取：
1. 最新的 paper trading 數據
2. 市場快照（爆倉壓力、OI）
3. 當前持倉狀態

輸出：
- 哪些策略表現好/差
- 當前市場機會在哪裡
- 建議調整哪些參數
"""

import uuid
import json
import os
import sys
import time
import pandas as pd
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# 狀態檔案，用於記錄 AI 的長期預測
STATE_FILE = "ai_advisor_state.json"
# 策略記憶檔案，用於記錄 AI 的多階段計畫
PLAN_FILE = "ai_strategy_plan.json"
# 學習記憶檔案，用於記錄成功與失敗的經驗
MEMORY_FILE = "ai_learning_memory.json"
# 市場記憶檔案，用於記錄動態市場體制與長期偏見
MARKET_MEMORY_FILE = "ai_market_memory.json"
# 團隊配置檔案，用於動態調整 AI 參數
TEAM_CONFIG_FILE = "config/ai_team_config.json"
# 🆕 AI-Wolf 雙向溝通橋接檔案
BRIDGE_FILE = "ai_wolf_bridge.json"

def load_bridge():
    """載入 AI-Wolf 橋接資料"""
    if os.path.exists(BRIDGE_FILE):
        try:
            with open(BRIDGE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "ai_to_wolf": {"command": "WAIT"},
        "wolf_to_ai": {"status": "IDLE"},
        "feedback_loop": {"total_trades": 0}
    }

def save_bridge(bridge):
    """儲存 AI-Wolf 橋接資料"""
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
    # 默認配置
    return {
        "team_dynamics": {"current_mvp": "Macro", "debate_intensity": "HIGH"},
        "agent_profiles": {
            "macro": {"name": "The Macro Seer", "bias": "Conservative"},
            "micro": {"name": "The Scalp Hunter", "bias": "Aggressive"},
            "strategist": {"name": "The Strategist", "bias": "Neutral"}
        },
        "dynamic_parameters": {"max_leverage": 50, "risk_level": "MODERATE"}
    }

def save_team_config(config):
    """儲存 AI 團隊配置"""
    os.makedirs(os.path.dirname(TEAM_CONFIG_FILE), exist_ok=True)
    with open(TEAM_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def find_latest_pt_session():
    """找到最新的 paper trading 會話目錄"""
    pt_dir = Path("data/paper_trading")
    if not pt_dir.exists():
        return None
    
    # 確保只選取目錄且符合命名規則
    sessions = sorted([d for d in pt_dir.iterdir() if d.is_dir() and d.name.startswith("pt_")])
    return sessions[-1] if sessions else None


def load_signal_diagnostics(session_path):
    """載入最新的信號診斷數據 (CSV)"""
    csv_file = session_path / "signal_diagnostics.csv"
    if not csv_file.exists():
        return None
    
    try:
        # 讀取最後 50 行以進行微觀特徵分析
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
        # 讀取更多行數以支援長期分析 (例如 3000 行，確保覆蓋 4 小時)
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
        "phases": []
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
        "mistakes": [], # 記錄失敗的預測特徵
        "successes": [] # 記錄成功的預測特徵
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
            "pending_change": None # 用於防抖動 (Debounce)
        },
        "short_term_memory": {"last_vpin_spike": None, "last_obi_flip": None}
    }

def save_market_memory(memory):
    """儲存市場記憶"""
    with open(MARKET_MEMORY_FILE, 'w') as f:
        json.dump(memory, f, indent=2)

def evaluate_past_prediction(current_price, previous_state, memory):
    """評估過去的預測是否準確，並更新記憶"""
    if not previous_state.get("prediction_time") or not previous_state.get("entry_price"):
        return memory, None

    pred_time = datetime.fromisoformat(previous_state["prediction_time"])
    time_diff = (datetime.now() - pred_time).total_seconds() / 60.0 # 分鐘
    
    # 至少過 5 分鐘才評估，或者價格波動超過 0.5%
    price_diff_pct = (current_price - previous_state["entry_price"]) / previous_state["entry_price"] * 100
    
    # 🆕 動態止損/止盈檢測：如果方向錯誤且波動超過 0.3% (高槓桿下約 15-30% 損益)，立即判定為失敗
    is_emergency = False
    action = previous_state.get("action", "WAIT")
    
    if action == "LONG" and price_diff_pct < -0.3: is_emergency = True
    if action == "SHORT" and price_diff_pct > 0.3: is_emergency = True
    
    if not is_emergency and time_diff < 5 and abs(price_diff_pct) < 0.5:
        return memory, None # 還太早，不評估

    result = "NEUTRAL"
    
    # 判定勝負
    if action == "LONG":
        if price_diff_pct > 0.2: result = "WIN"
        elif price_diff_pct < -0.2: result = "LOSS"
    elif action == "SHORT":
        if price_diff_pct < -0.2: result = "WIN"
        elif price_diff_pct > 0.2: result = "LOSS"
    elif action == "WAIT":
        # WAIT 的評估比較模糊，假設如果波動很小就是正確的
        if abs(price_diff_pct) < 0.3: result = "WIN"
        else: result = "LOSS" # 錯過了行情

    if result == "NEUTRAL":
        return memory, None
        
    # 🆕 如果是緊急情況，強制標記為嚴重失敗
    if is_emergency:
        result = "SEVERE_LOSS"
        print(f"   🚨 [Emergency] Detected rapid loss! Price moved {price_diff_pct:.2f}% against {action}.")

    # 更新統計
    memory["stats"]["total"] += 1
    if result == "WIN":
        memory["stats"]["correct"] += 1
        # 記錄成功模式 (只保留最近 20 筆)
        memory["successes"].append({
            "time": previous_state["prediction_time"],
            "action": action,
            "context_summary": previous_state.get("last_prediction", "")[:50]
        })
        if len(memory["successes"]) > 20: memory["successes"].pop(0)
    else:
        # 記錄失敗模式 (只保留最近 20 筆)
        memory["mistakes"].append({
            "time": previous_state["prediction_time"],
            "action": action,
            "severity": "HIGH" if result == "SEVERE_LOSS" else "NORMAL",
            "reason": f"Price moved {price_diff_pct:.2f}% against prediction",
            "context_summary": previous_state.get("last_prediction", "")[:50]
        })
        if len(memory["mistakes"]) > 20: memory["mistakes"].pop(0)

    memory["stats"]["accuracy"] = round(memory["stats"]["correct"] / memory["stats"]["total"] * 100, 2)
    
    # 重置預測時間，避免重複評估
    previous_state["prediction_time"] = None 
    save_advisor_state(previous_state)
    save_learning_memory(memory)
    
    return memory, result

def extract_micro_features(signals_df):
    """提取微觀特徵 (毫秒級特徵模擬)"""
    if signals_df is None or signals_df.empty:
        return {}
    
    # 使用最後 20 筆數據 (假設每筆間隔很短)
    recent = signals_df.tail(20)
    
    features = {
        "vpin_spike": False,
        "obi_flip": False,
        "volatility_increasing": False,
        "avg_vpin": 0.0,
        "avg_obi": 0.0
    }
    
    if 'vpin' in recent.columns:
        vpin_values = recent['vpin'].astype(float)
        features["avg_vpin"] = vpin_values.mean()
        features["vpin_max"] = vpin_values.max()
        # 檢測 VPIN 是否在短時間內急劇上升 (Spike)
        if vpin_values.max() - vpin_values.min() > 0.3 and vpin_values.iloc[-1] > 0.7:
            features["vpin_spike"] = True
            
    if 'obi' in recent.columns:
        obi_values = recent['obi'].astype(float)
        features["avg_obi"] = obi_values.mean()
        # 檢測 OBI 是否發生正負翻轉 (Flip)
        if (obi_values.max() > 0.2 and obi_values.min() < -0.2):
            features["obi_flip"] = True
            
    return features

def update_market_regime(signals_df, market_memory):
    """更新市場體制 (Trending vs Ranging) 並寫入記憶"""
    if signals_df is None or signals_df.empty:
        return "UNKNOWN", market_memory
    
    # 使用 VPIN 和 OBI 的波動性來判斷
    recent = signals_df.tail(50)
    vpin_std = recent['vpin'].std()
    obi_abs_mean = recent['obi'].abs().mean()
    
    # 計算當前分數
    volatility_score = float(vpin_std) if not pd.isna(vpin_std) else 0.0
    trend_score = float(obi_abs_mean) if not pd.isna(obi_abs_mean) else 0.0
    
    # 判斷當前狀態
    current_regime = "RANGING"
    if volatility_score > 0.1 or trend_score > 0.5:
        current_regime = "VOLATILE"
    elif trend_score > 0.3:
        current_regime = "TRENDING"
        
    # 更新記憶 (簡單的滯後邏輯，避免頻繁切換)
    last_regime = market_memory["regime"].get("current", "UNKNOWN")
    
    # 如果狀態改變，記錄時間
    if current_regime != last_regime:
        market_memory["regime"]["current"] = current_regime
        market_memory["regime"]["since"] = datetime.now().isoformat()
    
    market_memory["regime"]["volatility_score"] = volatility_score
    market_memory["regime"]["trend_score"] = trend_score
    
    return current_regime, market_memory

def summarize_mode_performance(trading_data):
    """總結其他模式的表現，以判斷市場特性"""
    if not trading_data or 'modes' not in trading_data:
        return "No trading data available."
    
    modes = trading_data['modes']
    summary = []
    
    # 分類模式
    trend_modes = ['M1', 'M7', 'M8', 'M9']
    mean_reversion_modes = ['M0', 'M2', 'M6']
    whale_modes = ['M_WHALE', 'M_LP_WHALE']
    
    trend_pnl = 0
    mean_pnl = 0
    
    for name, data in modes.items():
        pnl = data.get('pnl_usdt', 0)
        # 簡單的名稱匹配
        is_trend = any(m in name for m in trend_modes)
        is_mean = any(m in name for m in mean_reversion_modes)
        
        if is_trend: trend_pnl += pnl
        if is_mean: mean_pnl += pnl
        
        if pnl != 0:
            summary.append(f"{name}: ${pnl:.2f}")
            
    regime_hint = "UNCLEAR"
    if trend_pnl > mean_pnl and trend_pnl > 0:
        regime_hint = "TRENDING (Trend strategies are winning)"
    elif mean_pnl > trend_pnl and mean_pnl > 0:
        regime_hint = "RANGING (Mean reversion strategies are winning)"
    elif trend_pnl < 0 and mean_pnl < 0:
        regime_hint = "CHOPPY/DIFFICULT (All strategies losing)"
        
    return f"Market Regime Hint: {regime_hint}. Details: {', '.join(summary)}"

def get_llm_client(model_type="openai"):
    """獲取 LLM 客戶端 (OpenAI 或 Ollama)"""
    if model_type == "ollama":
        # Ollama 不需要 API Key，base_url 指向本地
        return OpenAI(
            base_url='http://localhost:11434/v1',
            api_key='ollama', # required, but unused
        )
    else:
        # 默認使用 OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("❌ 未找到 OPENAI_API_KEY")
            return None
        return OpenAI(api_key=api_key)

def analyze_with_ai(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state):
    """使用 AI 分析當前交易狀況並提供建議"""
    
    # 讀取配置以決定使用哪個模型
    team_config = load_team_config()
    model_choice = team_config.get("model_config", {}).get("provider", "openai") # openai or ollama
    
    client = get_llm_client(model_choice)
    if not client: return "❌ LLM Client Init Failed"
    
    # 載入記憶與計畫
    current_plan = load_strategy_plan()
    learning_memory = load_learning_memory()
    market_memory = load_market_memory()
    
    # 1. 提取關鍵市場指標
    try:
        latest_oi = market_snapshot['open_interest'][-1]
        latest_ls = market_snapshot['global_long_short'][-1]
        
        oi_val = float(latest_oi['sumOpenInterest'])
        oi_usdt = float(latest_oi['sumOpenInterestValue'])
        price = oi_usdt / oi_val if oi_val > 0 else 0
        ls_ratio = float(latest_ls['longShortRatio'])
        
        # 🆕 提取爆倉壓力 (Liquidation Pressure)
        liq_pressure = market_snapshot.get('liquidation_pressure', {})
        long_liq = liq_pressure.get('L_long_liq', 0)
        short_liq = liq_pressure.get('L_short_liq', 0)
        
    except:
        price = 0
        oi_val = 0
        ls_ratio = 0
        long_liq = 0
        short_liq = 0
    
    # 0. 自我評估與學習
    learning_memory, eval_result = evaluate_past_prediction(price, previous_state, learning_memory)
    if eval_result:
        print(f"   🎓 [Self-Learning] Previous prediction result: {eval_result}. Accuracy: {learning_memory['stats']['accuracy']}%")

    # 2. 提取信號摘要 & 微觀特徵 & 更新市場體制
    signal_summary = ""
    micro_features = extract_micro_features(signals_df)
    market_regime, market_memory = update_market_regime(signals_df, market_memory)
    
    if signals_df is not None and not signals_df.empty:
        latest_signals = signals_df.tail(5)[['mode', 'action', 'reason', 'signal_score', 'obi', 'vpin']].to_dict('records')
        signal_summary = json.dumps(latest_signals, ensure_ascii=False)

    # 3. 提取 Whale Flip 數據 (多重時間框架)
    whale_short_term = {"net_qty": 0, "dominance": 0}
    whale_long_term = {"net_qty": 0, "trend": "NEUTRAL"}
    
    if whale_flip_df is not None and not whale_flip_df.empty:
        # 確保 timestamp 欄位是 datetime 格式
        if 'timestamp' in whale_flip_df.columns:
            whale_flip_df['timestamp'] = pd.to_datetime(whale_flip_df['timestamp'])
            
        # 短期 (最近 15 分鐘)
        # 使用時間過濾而非固定行數
        current_time = pd.Timestamp.now()
        short_term_start = current_time - pd.Timedelta(minutes=15)
        
        if 'timestamp' in whale_flip_df.columns:
            recent_whales = whale_flip_df[whale_flip_df['timestamp'] >= short_term_start]
        else:
            recent_whales = whale_flip_df.tail(20) # Fallback
            
        if 'net_qty' in recent_whales.columns and not recent_whales.empty:
            whale_short_term["net_qty"] = recent_whales['net_qty'].sum()
            whale_short_term["dominance"] = recent_whales['dominance'].mean()
            
        # 長期 (真正鎖定過去 4 小時)
        long_term_start = current_time - pd.Timedelta(hours=4)
        
        if 'timestamp' in whale_flip_df.columns:
            long_term_whales = whale_flip_df[whale_flip_df['timestamp'] >= long_term_start]
        else:
            long_term_whales = whale_flip_df.tail(1000) # Fallback
            
        if 'net_qty' in long_term_whales.columns and not long_term_whales.empty:
            net_qty_sum = long_term_whales['net_qty'].sum()
            whale_long_term["net_qty"] = net_qty_sum
            
            # 根據 4 小時累積量判斷趨勢 (門檻值需要隨時間窗口調整)
            # 4小時的累積量通常較大，提高門檻以過濾雜訊
            if net_qty_sum > 500: whale_long_term["trend"] = "STRONG_ACCUMULATION"
            elif net_qty_sum > 150: whale_long_term["trend"] = "MILD_ACCUMULATION"
            elif net_qty_sum < -500: whale_long_term["trend"] = "STRONG_DISTRIBUTION"
            elif net_qty_sum < -150: whale_long_term["trend"] = "MILD_DISTRIBUTION"

    # 4. 構建 AI Prompt (動態信號 + 長短期記憶 + 堅定決策 + 高槓桿刷單)
    # 這裡將被拆分為多個 Agent 的 Prompt
    pass

def get_agent_opinion(client, agent_name, system_prompt, user_context, model_name="gpt-4o-mini"):
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

def run_council_meeting(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state):
    """召開 AI 戰略委員會會議 (4 Agents Debate)"""
    
    # 讀取配置以決定使用哪個模型
    team_config = load_team_config()
    model_config = team_config.get("model_config", {})
    provider = model_config.get("provider", "openai") # openai or ollama
    model_name = model_config.get("model_name", "gpt-4o-mini") # gpt-4o-mini or qwen3:32b
    
    client = get_llm_client(provider)
    if not client: return "❌ LLM Client Init Failed"
    
    # 載入記憶與計畫
    current_plan = load_strategy_plan()
    learning_memory = load_learning_memory()
    market_memory = load_market_memory()
    team_config = load_team_config()
    
    # --- 數據準備 (與之前相同) ---
    # 🆕 優先讀取 Bridge 的即時數據
    bridge = load_bridge()
    wolf_data = bridge.get('wolf_to_ai', {})
    rt_whale = wolf_data.get('whale_status', {})
    rt_micro = wolf_data.get('market_microstructure', {})
    
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
    except:
        price = 0; oi_val = 0; ls_ratio = 0; long_liq = 0; short_liq = 0
    
    # 自我評估
    learning_memory, eval_result = evaluate_past_prediction(price, previous_state, learning_memory)
    
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

    # --- 定義 Agents (使用 team_config) ---
    profiles = team_config.get("agent_profiles", {})
    params = team_config.get("dynamic_parameters", {})
    
    # 檢查是否有正在進行的 Grand Strategy
    grand_strategy = current_plan.get("grand_strategy", {"active": False})
    
    # 1. 👴 The Macro Seer (長期) - 改為「主力預測模式」
    p_macro = profiles.get("macro", {})
    macro_prompt = f"""
You are '{p_macro.get('name', 'The Whale Predictor')}'. Your Role: **Grand Strategist**.
Your Focus: **Long-term Vision (1-5 Hours)**.
Your Bias: {p_macro.get('bias', 'Proactive')}.
Weight in Council: {p_macro.get('weight', 1.0)}.

Your Goal:
1. Analyze the "Big Picture" using Whale Trends (4H) and Other Modes' Performance.
2. Formulate a **Grand Strategy** for the next 1-5 hours.
3. **CRITICAL RULE**: If 'REAL-TIME WHALE STATUS' contradicts 'Whale Trend (4H)', you MUST trust the REAL-TIME status.
   - Example: If 4H says "Accumulation" but Real-Time says "NetQty -10 BTC", you must assume the trend has REVERSED to BEARISH.

Input Data:
- **REAL-TIME WHALE STATUS (LIVE & AUTHORITATIVE)**: Direction={rt_whale.get('current_direction')}, NetQty={rt_whale.get('net_qty_btc', 0)} BTC, Dominance={rt_whale.get('dominance', 0)}
- Whale Trend (4H Historical - Lagging): {whale_long_term['trend']} (Net: {whale_long_term['net_qty']:.2f} BTC)
- Other Modes Performance: {mode_performance_summary}
- Market Regime: {market_regime}

Output:
- Grand Strategy Direction: BULLISH / BEARISH / NEUTRAL
- Target Duration: 1-5 hours
- Key Thesis: Why? (e.g., "Real-time selling overrides historical accumulation")
- Invalidation Level: Price level that proves you wrong.
"""
    macro_context = f"""
Current Price: {price}
LS Ratio: {ls_ratio}
Liquidation Pressure: Long={long_liq}, Short={short_liq}
Current Grand Strategy: {json.dumps(grand_strategy)}
"""

    # 2. ⚡ The Market Reaction Tracker (短期) - 改為「驗證與修正模式」
    p_micro = profiles.get("micro", {})
    micro_prompt = f"""
You are '{p_micro.get('name', 'The Reality Checker')}'. Your Role: **Tactical Navigator**.
Your Focus: **Validate the Grand Strategy**.
Your Bias: {p_micro.get('bias', 'Adaptive')}.
Weight in Council: {p_micro.get('weight', 1.0)}.

Your Goal:
1. Check if the current price action supports or threatens the Grand Strategy.
2. **Avoid Flip-Flopping**: Only recommend aborting the plan if there is a MAJOR structural break.
3. If the plan is working (or just noise), recommend HOLD or ADD.

Input Data:
- Grand Strategy: {json.dumps(grand_strategy)}
- **REAL-TIME MICROSTRUCTURE**: OBI={rt_micro.get('obi', 0):.2f}, VPIN={rt_micro.get('vpin', 0):.2f}, Spread={rt_micro.get('spread_bps', 0)}bps
- Whale Activity (15m): Net {whale_short_term['net_qty']:.2f} BTC
- Micro Features: VPIN={micro_features.get('avg_vpin', 0):.2f}, OBI={micro_features.get('avg_obi', 0):.2f}

Output:
- Status: ON_TRACK / MINOR_DEVIATION / MAJOR_THREAT
- Recommendation: CONTINUE / PAUSE / ABORT
- Reasoning: Specific micro-structure evidence.
"""
    micro_context = f"""
Recent Signals: {signal_summary}
Current Price: {price}
"""

    # 3. ⚖️ The Strategist (混合)
    p_strat = profiles.get("strategist", {})
    hybrid_prompt = f"""
You are '{p_strat.get('name', 'The Strategist')}'. {p_strat.get('role', 'Risk Manager')}.
Your Focus: **Execution Quality & Discipline**.
Your Bias: {p_strat.get('bias', 'Neutral')}.
Weight in Council: {p_strat.get('weight', 1.0)}.

Your Goal:
1. Evaluate if we are changing goals too often.
2. Ensure we stick to the plan unless invalidated.
3. Monitor progress: Profitability, Time Elapsed.

Input Data:
- Current Plan: {json.dumps(current_plan)}
- Market Bias: {market_memory['strategic_bias']['direction']}

Output:
- Discipline Check: PASS / FAIL (Are we flip-flopping?)
- Action: MAINTAIN_COURSE / REVISE_PLAN
"""
    hybrid_context = f"""
Time since plan start: {grand_strategy.get('start_time', 'N/A')}
"""

    # --- 執行辯論 (平行調用) ---
    print(f"   🗣️  Council is debating (Model: {model_name})...")
    # 這裡為了簡單用順序調用，實際生產環境可用 asyncio
    macro_opinion = get_agent_opinion(client, "Macro", macro_prompt, macro_context, model_name) or "No opinion"
    micro_opinion = get_agent_opinion(client, "Micro", micro_prompt, micro_context, model_name) or "No opinion"
    hybrid_opinion = get_agent_opinion(client, "Hybrid", hybrid_prompt, hybrid_context, model_name) or "No opinion"

    # --- 4. 👑 The Supreme Commander (裁判) ---
    commander_prompt = f"""
You are 'The Supreme Commander'. You make the FINAL DECISION based on a Long-Term Vision.

**OBJECTIVE**: Execute a coherent strategy over 1-5 hours. Avoid frequent direction changes.
**METRICS**: Profitability, Few Corrections, Successful Execution.

**SANITY CHECK (MANDATORY)**:
- If Real-Time Whale NetQty is NEGATIVE (e.g., <-5 BTC), you CANNOT be BULLISH.
- If Real-Time Whale NetQty is POSITIVE (e.g., >+5 BTC), you CANNOT be BEARISH.
- Ignore the "Advisors" if they contradict this Real-Time Truth.

Current Grand Strategy:
{json.dumps(grand_strategy)}

Advisor Opinions:
[Macro]: {macro_opinion}
[Micro]: {micro_opinion}
[Strategist]: {hybrid_opinion}

**DECISION LOGIC**:
1. **IF Grand Strategy is ACTIVE**:
   - Check Micro's "Status".
   - If "MAJOR_THREAT" or Invalidation Level hit -> **ABORT/CUT_LOSS**.
   - If "ON_TRACK" or "MINOR_DEVIATION" -> **HOLD** or **ADD** (Pyramid).
   - Do NOT change direction just because of small noise.
   - If Time Expired -> **EXIT/RE-EVALUATE**.

2. **IF Grand Strategy is INACTIVE (or Aborted)**:
   - Create a NEW Grand Strategy based on Macro's input.
   - Set a clear Direction, Target, and Invalidation Level.

**OUTPUT FORMAT (JSON)**:
{{
  "strategic_bias": "BULLISH|BEARISH",
  "tactical_action": "LONG|SHORT|HOLD|ADD_LONG|ADD_SHORT|CUT_LOSS",
  "recommended_leverage": 1-50,
  "conviction_score": 50-100,
  "whale_reversal_price": 87500,
  "grand_strategy_update": {{
     "active": true,
     "direction": "...",
     "thesis": "...",
     "target_duration_hours": 3,
     "start_time": "{datetime.now().isoformat()}", 
     "invalidation_price": 0
  }},
  "analysis": "Reasoning...",
  "parameter_updates": {{ ... }}
}}
*Note: If maintaining existing strategy, keep 'start_time' unchanged in 'grand_strategy_update'.*
"""
    commander_context = f"""
Current Price: {price}
Market Regime: {market_regime}
Other Modes: {mode_performance_summary}
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
        
        # --- 處理結果 (與之前相同) ---
        analysis_text = result.get('analysis') or "No analysis provided"
        
        new_state = {
            "last_prediction": analysis_text[:100] + "...", 
            "prediction_time": datetime.now().isoformat(),
            "entry_price": price,
            "action": result.get('tactical_action') or 'WAIT',
            "strategic_bias": result.get('strategic_bias') or 'NEUTRAL',
            "confidence": result.get('conviction_score') or 50,
            "whale_reversal_price": result.get('whale_reversal_price', 0),  # 🆕 新增反轉價格
            "full_analysis": f"👑 COMMANDER DECISION:\n{analysis_text}\n\n🗣️ DEBATE HIGHLIGHTS:\nWhale Predictor: {macro_opinion[:100]}...\nReality Checker: {micro_opinion[:100]}..."
        }
        save_advisor_state(new_state)
        
        # 🆕 更新 Bridge: AI → Wolf 指令
        bridge = load_bridge()
        wolf_feedback = bridge.get('wolf_to_ai', {})
        
        bridge['ai_to_wolf'] = {
            "command": result.get('tactical_action') or 'WAIT',
            "direction": result.get('strategic_bias') or 'NEUTRAL',
            "confidence": result.get('conviction_score') or 50,
            "leverage": result.get('recommended_leverage') or 1,
            "whale_reversal_price": result.get('whale_reversal_price', 0),
            "take_profit_pct": 2.5,  # 可動態調整
            "stop_loss_pct": 1.2,
            "reasoning": analysis_text[:200],
            "timestamp": datetime.now().isoformat()
        }
        save_bridge(bridge)

        # 🔄 強制同步 Strategy Plan (確保與 Bridge 同步)
        # 即使 LLM 沒有返回完整的 strategic_plan，也要根據當前決策更新關鍵欄位
        try:
            current_plan = load_strategy_plan()
            
            # 1. 提取 LLM 的計畫細節 (如果有的話)
            if 'strategic_plan' in result and isinstance(result['strategic_plan'], dict):
                plan_update = result['strategic_plan']
                current_plan.update(plan_update)
            
            # 🆕 更新 Grand Strategy
            if 'grand_strategy_update' in result and isinstance(result['grand_strategy_update'], dict):
                current_plan['grand_strategy'] = result['grand_strategy_update']
                
            # 2. 強制覆蓋關鍵狀態 (以 Bridge 決策為準)
            current_plan['market_bias'] = result.get('strategic_bias', 'NEUTRAL')
            current_plan['phase'] = result.get('tactical_action', 'WAIT')
            current_plan['max_leverage'] = result.get('recommended_leverage', 1)
            current_plan['risk_level'] = team_config['dynamic_parameters'].get('risk_level', 'MODERATE')
            
            # 3. 更新元數據
            current_plan['created_at'] = datetime.now().isoformat()
            current_plan['plan_id'] = str(uuid.uuid4())
            
            save_strategy_plan(current_plan)
            print(f"   📝 [Plan Synced] Strategy Plan updated to {current_plan['market_bias']} / {current_plan['phase']}")
        except Exception as e:
            print(f"   ⚠️ Failed to sync strategy plan: {e}")
        
        # 🆕 讀取 Wolf 的完整回饋並做智能調整
        feedback_loop = bridge.get('feedback_loop', {})
        failure_streak = feedback_loop.get('failure_streak', 0)
        
        # 🚨 CIRCUIT BREAKER (熔斷機制)
        if failure_streak >= 5:
            print(f"   🚨 [CIRCUIT BREAKER] Failure streak {failure_streak} detected! Forcing HOLD and RESET.")
            bridge['ai_to_wolf']['command'] = 'HOLD'
            bridge['ai_to_wolf']['reasoning'] = f"CIRCUIT BREAKER: Too many consecutive losses ({failure_streak}). Pausing to realign."
            save_bridge(bridge)
            
            # 重置 Grand Strategy
            current_plan['grand_strategy'] = {"active": False}
            save_strategy_plan(current_plan)
            return "CIRCUIT BREAKER TRIGGERED"

        if wolf_feedback.get('status') == 'IN_POSITION':
            pnl_pct = wolf_feedback.get('current_pnl_pct', 0)
            
            # 📊 Priority 1: 分析鯨魚狀態
            whale_status = wolf_feedback.get('whale_status', {})
            whale_direction = whale_status.get('current_direction')
            whale_dominance = whale_status.get('dominance', 0)
            whale_flip_count = whale_status.get('flip_count_30min', 0)
            
            # 📈 Priority 1: 分析市場微結構
            micro = wolf_feedback.get('market_microstructure', {})
            obi = micro.get('obi', 0)
            vpin = micro.get('vpin', 0)
            funding_rate = micro.get('funding_rate', 0)
            
            # 🌊 Priority 1: 分析波動環境
            volatility = wolf_feedback.get('volatility', {})
            atr_pct = volatility.get('atr_pct', 0)
            is_dead_market = volatility.get('is_dead_market', False)
            regime = volatility.get('regime', 'UNKNOWN')
            
            # 🎯 Priority 2: 檢查預測準確度
            feedback_loop = bridge.get('feedback_loop', {})
            prediction_accuracy = feedback_loop.get('prediction_accuracy', {})
            direction_accuracy = prediction_accuracy.get('direction_accuracy_pct', 0)
            
            # 🚨 Priority 3: 風險警示
            risk_indicators = wolf_feedback.get('risk_indicators', {})
            liquidation_pressure = risk_indicators.get('liquidation_pressure', 0)
            whale_trap_prob = risk_indicators.get('whale_trap_probability', 0)
            
            # 智能決策邏輯
            warnings = []
            profit_adjustments = []
            
            # 🎯 動態調整止盈配置
            profit_config_file = "ai_profit_config.json"
            if os.path.exists(profit_config_file):
                try:
                    with open(profit_config_file, 'r') as f:
                        profit_config = json.load(f)
                    
                    # 根據市場狀況和績效調整止盈目標
                    win_rate = feedback_loop.get('win_rate', 0)
                    total_trades = feedback_loop.get('total_trades', 0)
                    
                    should_update_config = False
                    
                    # 根據勝率動態調整
                    if total_trades >= 5:
                        dynamic_profit = profit_config.get('dynamic_profit_taking', {})
                        base_targets = dynamic_profit.get('base_targets', {})
                        
                        if win_rate >= 70 and base_targets.get('standard', 0.8) < 2.0:
                            # 勝率高,提高止盈目標
                            base_targets['standard'] = 1.5
                            base_targets['dead_market_reversal'] = 0.8
                            base_targets['reversal_ambush'] = 2.0
                            profit_adjustments.append(f"📈 High win rate ({win_rate:.0f}%) → Increased profit targets")
                            should_update_config = True
                        elif win_rate < 30 and base_targets.get('standard', 0.8) > 0.5:
                            # 勝率低,降低止盈目標
                            base_targets['standard'] = 0.5
                            base_targets['dead_market_reversal'] = 0.3
                            base_targets['reversal_ambush'] = 0.7
                            profit_adjustments.append(f"📉 Low win rate ({win_rate:.0f}%) → Reduced profit targets")
                            should_update_config = True
                    
                    # 根據波動率調整
                    if atr_pct > 0.1:
                        # 高波動,可以提高目標
                        progressive = profit_config.get('dynamic_profit_taking', {}).get('progressive_targets', {})
                        high_stage = progressive.get('stages', {}).get('high', {})
                        if high_stage.get('max_target', 6.0) < 8.0:
                            high_stage['max_target'] = 8.0
                            profit_adjustments.append(f"⚡ High volatility (ATR: {atr_pct:.4f}%) → Max target 8%")
                            should_update_config = True
                    elif atr_pct < 0.02:
                        # 低波動,降低目標
                        progressive = profit_config.get('dynamic_profit_taking', {}).get('progressive_targets', {})
                        high_stage = progressive.get('stages', {}).get('high', {})
                        if high_stage.get('max_target', 6.0) > 3.0:
                            high_stage['max_target'] = 3.0
                            profit_adjustments.append(f"💤 Low volatility (ATR: {atr_pct:.4f}%) → Max target 3%")
                            should_update_config = True
                    
                    # 儲存更新
                    if should_update_config:
                        profit_config['last_updated'] = datetime.now().isoformat()
                        history = profit_config.get('ai_adjustment_history', [])
                        history.append({
                            "timestamp": datetime.now().isoformat(),
                            "reason": f"Auto-adjustment based on WR={win_rate:.0f}%, ATR={atr_pct:.4f}%",
                            "changes": profit_adjustments
                        })
                        profit_config['ai_adjustment_history'] = history[-20:]  # 保留最近 20 次
                        
                        with open(profit_config_file, 'w') as f:
                            json.dump(profit_config, f, indent=2)
                except Exception as e:
                    print(f"   ⚠️ Failed to adjust profit config: {e}")
            
            # 檢查 1: PnL + 鯨魚反轉
            if pnl_pct < -0.5:
                if whale_direction and whale_direction != bridge['ai_to_wolf']['direction']:
                    warnings.append(f"⚠️ WHALE FLIPPED! Now {whale_direction} (Dom: {whale_dominance:.2f})")
                elif whale_flip_count >= 2:
                    warnings.append(f"⚠️ Whale churning ({whale_flip_count} flips) - possible trap")
                else:
                    warnings.append(f"⚠️ Position underwater: {pnl_pct:.2f}%")
            
            # 檢查 2: 死水盤警告
            if is_dead_market and atr_pct < 0.01:
                warnings.append(f"💤 Dead market (ATR: {atr_pct:.4f}%) - low win probability")
            
            # 檢查 3: 極端風險
            if liquidation_pressure > 70:
                warnings.append(f"🔴 High liquidation risk: {liquidation_pressure}/100")
            
            if whale_trap_prob > 0.6:
                warnings.append(f"🪤 Whale trap probability: {whale_trap_prob:.0%}")
            
            # 檢查 4: 預測準確度低
            if direction_accuracy < 40 and feedback_loop.get('total_trades', 0) >= 5:
                warnings.append(f"📉 Low prediction accuracy: {direction_accuracy:.0f}%")
            
            # 檢查 5: 獲利 + 環境確認
            if pnl_pct > 1.0:
                if whale_direction == bridge['ai_to_wolf']['direction']:
                    warnings.append(f"✅ Profitable + Whale aligned ({whale_dominance:.2f} dom) - hold/add")
                else:
                    warnings.append(f"⚠️ Profitable but whale diverging - consider partial exit")
            
            # 輸出所有警告
            if profit_adjustments:
                print(f"   🎯 Profit Config Auto-Adjustments:")
                for adj in profit_adjustments:
                    print(f"      {adj}")
            
            for warning in warnings:
                print(f"   {warning}")
            
            # 輸出關鍵市場指標
            print(f"   📊 Market: {regime} | ATR: {atr_pct:.4f}% | OBI: {obi:.2f} | VPIN: {vpin:.2f} | Fund: {funding_rate:.4f}")
        
        # 更新市場記憶 (Bias) - 帶防抖動
        suggested_bias = result.get('strategic_bias') or 'NEUTRAL'
        current_bias = market_memory["strategic_bias"]["direction"]
        
        if suggested_bias != current_bias:
            pending = market_memory["strategic_bias"].get("pending_change")
            if pending and pending["direction"] == suggested_bias:
                pending["count"] += 1
                if pending["count"] >= 3:
                    market_memory["strategic_bias"]["direction"] = suggested_bias
                    market_memory["strategic_bias"]["since"] = datetime.now().isoformat()
                    market_memory["strategic_bias"]["pending_change"] = None
                    print(f"   🔄 [Bias Flip] Confirmed change to {suggested_bias}")
                else:
                    print(f"   ⏳ [Bias Stability] Potential flip to {suggested_bias} detected ({pending['count']}/3)... Holding {current_bias}")
            else:
                market_memory["strategic_bias"]["pending_change"] = {
                    "direction": suggested_bias,
                    "count": 1,
                    "last_check": datetime.now().isoformat()
                }
                print(f"   ⏳ [Bias Stability] Potential flip to {suggested_bias} detected (1/3)... Holding {current_bias}")
                new_state["strategic_bias"] = current_bias # 強制保持
        else:
             if market_memory["strategic_bias"].get("pending_change"):
                market_memory["strategic_bias"]["pending_change"] = None
        
        save_market_memory(market_memory)
            
        # 處理參數更新
        if 'parameter_updates' in result and result['parameter_updates']:
            updates = result['parameter_updates']
            print(f"   ⚙️ [Auto-Tuning] Commander suggested updates: {updates}")
            # 更新 team_config
            for k, v in updates.items():
                if k in team_config['dynamic_parameters']:
                    team_config['dynamic_parameters'][k] = v
            team_config['recent_adjustments'].append({
                "time": datetime.now().isoformat(),
                "updates": updates,
                "reason": "Commander Decision"
            })
            save_team_config(team_config)
            
        # 格式化輸出，讓用戶看到辯論亮點
        analysis_preview = (result.get('analysis') or "No analysis")[:120]
        debate_highlights = (
            f"\n   👉 [Macro]: {macro_opinion[:80]}..."
            f"\n   👉 [Micro]: {micro_opinion[:80]}..."
            f"\n   👉 [Strat]: {hybrid_opinion[:80]}..."
        )
        
        return f"[{result.get('strategic_bias')} | {result.get('tactical_action')} (Lev x{result.get('recommended_leverage', 1)})] {analysis_preview}...{debate_highlights}"

    except Exception as e:
        return f"❌ Commander failed: {e}"

def analyze_with_ai(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state):
    # 為了兼容舊代碼接口，這裡直接轉發給 run_council_meeting
    return run_council_meeting(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state)



def main():
    print("="*60)
    print("🤖 AI Whale Hunter (Trap Master Mode)")
    print("="*60)
    
    while True:
        try:
            # 1. 載入數據
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
            
            # 2. AI 分析
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🔍 Analyzing Session: {session_path.name}")
            analysis = analyze_with_ai(trading_data, market_snapshot, signals_df, whale_flip_df, prev_state)
            
            print("\n" + analysis)
            print("\n" + "-"*60)
            print("💤 Observing fluctuations... (Next check in 15s)")
            
            time.sleep(15)
            
        except KeyboardInterrupt:
            print("\n🛑 AI Advisor Stopped.")
            break
        except Exception as e:
            print(f"⚠️ Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()

