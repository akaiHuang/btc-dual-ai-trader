#!/usr/bin/env python3
"""
AI Trading Advisor 🦁 Lion - v2.0 Whale Strategy Detector Enhanced
基於 M🐺 (Wolf) 架構，但整合 v2.0 鯨魚策略檢測器

特色：
1. 使用與 M🐺 相同的 GPT 模型和 prompt 結構
2. 新增 v2.0 WhaleStrategyDetector 分析：
   - 8 種主力策略識別 (吸籌、誘空、誘多、拉盤出貨、震倉洗盤、試盤、對敲洗盤、崩盤出貨)
   - 5 種新增檢測器 (支撐壓力突破、成交量衰竭、隱藏大單、價量背離、瀑布下跌)
   - 主力 vs 散戶對峙分析
   - 預測下一步行動

使用方式：
  python scripts/ai_trading_advisor_lion.py [hours]
  例如: python scripts/ai_trading_advisor_lion.py 8  # 運行 8 小時後自動停止
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

# 添加 src 目錄到路徑以導入 whale_strategy_detector
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.strategy.whale_strategy_detector import WhaleStrategyDetector, WhaleStrategy

# Load environment variables
load_dotenv()

# 🦁 Lion 專用狀態檔案
STATE_FILE = "ai_lion_state.json"
PLAN_FILE = "ai_lion_plan.json"
MEMORY_FILE = "ai_lion_memory.json"
MARKET_MEMORY_FILE = "ai_lion_market_memory.json"
TEAM_CONFIG_FILE = "config/ai_team_config.json"
BRIDGE_FILE = "ai_lion_bridge.json"

# 🆕 v2.0 鯨魚策略檢測器實例 (全局)
whale_detector = WhaleStrategyDetector()


def load_bridge():
    """載入 AI-Lion 橋接資料"""
    if os.path.exists(BRIDGE_FILE):
        try:
            with open(BRIDGE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "ai_to_wolf": {"command": "WAIT"},
        "wolf_to_ai": {"status": "IDLE"},
        "feedback_loop": {"total_trades": 0},
        "v2_strategy_detection": {}  # 🆕 v2.0 策略檢測結果
    }


def save_bridge(bridge):
    """儲存 AI-Lion 橋接資料"""
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
        "team_dynamics": {"current_mvp": "Lion", "debate_intensity": "HIGH"},
        "agent_profiles": {
            "macro": {"name": "The Whale Strategist", "bias": "Proactive", "weight": 1.0},
            "micro": {"name": "The Pattern Hunter", "bias": "Adaptive", "weight": 1.0},
            "strategist": {"name": "The Risk Master", "bias": "Neutral", "weight": 1.0}
        },
        "dynamic_parameters": {"max_leverage": 50, "risk_level": "MODERATE"},
        "model_config": {"provider": "openai", "model_name": "gpt-4o-mini"}
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
    sessions = sorted([d for d in pt_dir.iterdir() if d.is_dir() and d.name.startswith("pt_")])
    return sessions[-1] if sessions else None


def load_signal_diagnostics(session_path):
    """載入最新的信號診斷數據 (CSV)"""
    csv_file = session_path / "signal_diagnostics.csv"
    if not csv_file.exists():
        return None
    try:
        df = pd.read_csv(csv_file)
        return df.tail(100)
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
        print(f"   🚨 [Emergency] Detected rapid loss! Price moved {price_diff_pct:.2f}% against {action}.")

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
    vpin_std = recent['vpin'].std()
    obi_abs_mean = recent['obi'].abs().mean()
    
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
    market_memory["regime"]["trend_score"] = trend_score
    
    return current_regime, market_memory


def summarize_mode_performance(trading_data):
    """總結其他模式的表現"""
    if not trading_data or 'modes' not in trading_data:
        return "No trading data available."
    
    modes = trading_data['modes']
    summary = []
    
    trend_modes = ['M1', 'M7', 'M8', 'M9']
    mean_reversion_modes = ['M0', 'M2', 'M6']
    
    trend_pnl = 0
    mean_pnl = 0
    
    for name, data in modes.items():
        pnl = data.get('pnl_usdt', 0)
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
    """獲取 LLM 客戶端"""
    if model_type == "ollama":
        return OpenAI(
            base_url='http://localhost:11434/v1',
            api_key='ollama',
        )
    elif model_type == "kimi":
        api_key = os.getenv("KIMI_API_KEY")
        if not api_key:
            print("❌ 未找到 KIMI_API_KEY")
            return None
        return OpenAI(
            base_url='https://api.moonshot.cn/v1',
            api_key=api_key,
        )
    else:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("❌ 未找到 OPENAI_API_KEY")
            return None
        return OpenAI(api_key=api_key)


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


# ========================================
# 🦁 v2.0 鯨魚策略檢測核心函數
# ========================================

def run_v2_whale_detection(market_snapshot, signals_df, whale_flip_df, bridge):
    """
    執行 v2.0 鯨魚策略檢測
    
    Returns:
        dict: 策略檢測結果，包含：
        - detected_strategy: 識別到的主力策略
        - strategy_probabilities: 各策略機率分布
        - conflict_state: 主力 vs 散戶對峙狀態
        - predicted_action: 預測下一步行動
        - key_signals: 關鍵信號
        - risk_warnings: 風險警告
    """
    global whale_detector
    
    # 提取所需數據
    try:
        # 基礎市場數據
        latest_oi = market_snapshot.get('open_interest', [{}])[-1]
        oi_val = float(latest_oi.get('sumOpenInterest', 0))
        oi_usdt = float(latest_oi.get('sumOpenInterestValue', 0))
        current_price = oi_usdt / oi_val if oi_val > 0 else 0
        
        # 從 Bridge 獲取即時數據
        wolf_data = bridge.get('wolf_to_ai', {})
        rt_micro = wolf_data.get('market_microstructure', {})
        rt_whale = wolf_data.get('whale_status', {})
        
        obi = rt_micro.get('obi', 0)
        vpin = rt_micro.get('vpin', 0)
        
        # 爆倉壓力
        liq_pressure = market_snapshot.get('liquidation_pressure', {})
        long_liq = liq_pressure.get('L_long_liq', 0)
        short_liq = liq_pressure.get('L_short_liq', 0)
        
        # 資金費率
        funding_rate_data = market_snapshot.get('funding_rate', [])
        if isinstance(funding_rate_data, list) and funding_rate_data:
            funding_rate = float(funding_rate_data[-1].get('fundingRate', 0))
        else:
            funding_rate = 0
        
        # 鯨魚淨買入量
        whale_net_qty = rt_whale.get('net_qty_btc', 0)
        
        # 價格變化百分比 (從信號數據)
        price_change_pct = 0
        volume_ratio = 1.0
        
        if signals_df is not None and not signals_df.empty:
            recent = signals_df.tail(10)
            if 'price' in recent.columns:
                prices = recent['price'].astype(float)
                if len(prices) >= 2 and prices.iloc[0] > 0:
                    price_change_pct = (prices.iloc[-1] - prices.iloc[0]) / prices.iloc[0] * 100
            if 'volume_ratio' in recent.columns:
                volume_ratio = recent['volume_ratio'].mean()
        
        # 準備 K 線數據
        recent_candles = []
        if signals_df is not None and not signals_df.empty:
            for _, row in signals_df.tail(30).iterrows():
                recent_candles.append({
                    "open": row.get('open', current_price),
                    "high": row.get('high', current_price),
                    "low": row.get('low', current_price),
                    "close": row.get('close', current_price),
                    "volume": row.get('volume', 0)
                })
        
        # 準備訂單簿快照 (如果有的話)
        orderbook_snapshot = wolf_data.get('orderbook', None)
        
        # 🔥 執行 v2.0 檢測
        prediction = whale_detector.analyze(
            obi=obi,
            vpin=vpin,
            current_price=current_price,
            price_change_pct=price_change_pct,
            volume_ratio=volume_ratio,
            whale_net_qty=whale_net_qty,
            funding_rate=funding_rate,
            liquidation_pressure_long=long_liq,
            liquidation_pressure_short=short_liq,
            recent_candles=recent_candles if recent_candles else None,
            orderbook_snapshot=orderbook_snapshot,
            current_volume=oi_usdt * 0.01  # 估算當前成交量
        )
        
        # 轉換結果為字典
        strategy_probs_dict = {}
        for sp in prediction.strategy_probabilities:
            strategy_probs_dict[sp.strategy.name] = {
                "probability": sp.probability,
                "confidence": sp.confidence,
                "features": sp.matched_features[:3]  # 只取前 3 個特徵
            }
        
        result = {
            "detected_strategy": prediction.detected_strategy.name,
            "strategy_probabilities": strategy_probs_dict,
            "conflict_state": prediction.conflict_state,
            "predicted_action": prediction.predicted_action,
            "predicted_price_target": prediction.predicted_price_target,
            "prediction_confidence": prediction.prediction_confidence,
            "expected_timeframe_minutes": prediction.expected_timeframe_minutes,
            "key_signals": prediction.key_signals[:5],  # 只取前 5 個
            "risk_warnings": prediction.risk_warnings[:3],  # 只取前 3 個
            "timestamp": prediction.timestamp,
            "current_price": prediction.current_price
        }
        
        return result
        
    except Exception as e:
        print(f"   ⚠️ v2.0 Detection Error: {e}")
        return {
            "detected_strategy": "UNKNOWN",
            "strategy_probabilities": {},
            "conflict_state": "UNKNOWN",
            "predicted_action": "HOLD",
            "key_signals": [],
            "risk_warnings": [f"Detection Error: {str(e)}"],
            "timestamp": datetime.now().isoformat(),
            "current_price": 0
        }


def format_v2_detection_for_prompt(v2_result):
    """
    將 v2.0 檢測結果格式化為 GPT 可讀的字串
    """
    if not v2_result or v2_result.get("detected_strategy") == "UNKNOWN":
        return "v2.0 Detection: No data available"
    
    # 策略名稱翻譯
    strategy_names = {
        "ACCUMULATION": "吸籌建倉 (主力正在低調買入)",
        "BEAR_TRAP": "誘空吸籌 (假跌破後會反彈)",
        "BULL_TRAP": "誘多出貨 (假突破後會下跌)",
        "PUMP_DUMP": "拉盤出貨 (急漲後會暴跌)",
        "SHAKE_OUT": "震倉洗盤 (劇烈波動洗出散戶)",
        "TESTING": "試盤 (主力在測試市場反應)",
        "WASH_TRADING": "對敲洗盤 (製造假成交量)",
        "DUMP": "崩盤出貨 (主力瘋狂出貨)"
    }
    
    # 對峙狀態翻譯
    conflict_names = {
        "WHALE_DOMINANT": "主力控盤中 (跟隨主力)",
        "RETAIL_DOMINANT": "散戶主導 (可能被收割)",
        "STANDOFF": "多空對峙 (即將爆發)",
        "UNKNOWN": "狀態不明"
    }
    
    detected = v2_result.get("detected_strategy", "UNKNOWN")
    conflict = v2_result.get("conflict_state", "UNKNOWN")
    
    # 找出前 3 高機率策略
    probs = v2_result.get("strategy_probabilities", {})
    sorted_probs = sorted(probs.items(), key=lambda x: x[1].get("probability", 0), reverse=True)[:3]
    
    top_strategies = []
    for name, data in sorted_probs:
        prob = data.get("probability", 0)
        features = ", ".join(data.get("features", [])[:2])
        top_strategies.append(f"  - {name}: {prob:.0%} ({features})")
    
    result = f"""
🦁 v2.0 WHALE STRATEGY DETECTION:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 Detected Strategy: {detected} - {strategy_names.get(detected, detected)}
⚔️ Conflict State: {conflict} - {conflict_names.get(conflict, conflict)}
📈 Predicted Action: {v2_result.get('predicted_action', 'N/A')}
🎯 Price Target: ${v2_result.get('predicted_price_target', 0):,.0f}
🔮 Confidence: {v2_result.get('prediction_confidence', 0):.0%}
⏱️ Expected Timeframe: {v2_result.get('expected_timeframe_minutes', 0)} minutes

Top 3 Strategy Probabilities:
{chr(10).join(top_strategies)}

Key Signals:
{chr(10).join(['  • ' + s for s in v2_result.get('key_signals', [])[:3]])}

Risk Warnings:
{chr(10).join(['  ⚠️ ' + w for w in v2_result.get('risk_warnings', [])[:2]])}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    return result


# ========================================
# 🦁 Lion Council Meeting (主要決策邏輯)
# ========================================

def run_council_meeting(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state):
    """召開 AI 🦁 Lion 戰略委員會會議 (整合 v2.0 檢測)"""
    
    team_config = load_team_config()
    model_config = team_config.get("model_config", {})
    provider = model_config.get("provider", "openai")
    model_name = model_config.get("model_name", "gpt-4o-mini")
    
    client = get_llm_client(provider)
    if not client: return "❌ LLM Client Init Failed"
    
    current_plan = load_strategy_plan()
    learning_memory = load_learning_memory()
    market_memory = load_market_memory()
    
    # 讀取 Bridge 數據
    bridge = load_bridge()
    wolf_data = bridge.get('wolf_to_ai', {})
    rt_whale = wolf_data.get('whale_status', {})
    rt_micro = wolf_data.get('market_microstructure', {})
    
    # 🦁 執行 v2.0 鯨魚策略檢測
    v2_detection = run_v2_whale_detection(market_snapshot, signals_df, whale_flip_df, bridge)
    v2_prompt_text = format_v2_detection_for_prompt(v2_detection)
    
    # 更新 Bridge 中的 v2 檢測結果
    bridge['v2_strategy_detection'] = v2_detection
    save_bridge(bridge)
    
    # Cascade Alert
    cascade_alert = wolf_data.get('cascade_alert', {})
    cascade_active = cascade_alert.get('active', False)
    cascade_direction = cascade_alert.get('direction', 'NONE')
    cascade_strength = cascade_alert.get('strength', 0)
    cascade_warning = cascade_alert.get('warning', '')
    cascade_suggestion = cascade_alert.get('suggested_action', '')
    
    # 市場數據
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
        print(f"   🎓 [Self-Learning] Previous prediction result: {eval_result}. Accuracy: {learning_memory['stats']['accuracy']}%")
    
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

    profiles = team_config.get("agent_profiles", {})
    grand_strategy = current_plan.get("grand_strategy", {"active": False})
    
    # 🦁 CASCADE WARNING MESSAGE
    cascade_warning_msg = ""
    if cascade_active:
        cascade_warning_msg = f"""
⚠️ **LIQUIDATION CASCADE ALERT** ⚠️
- Status: ACTIVE (Strength: {cascade_strength}/100)
- Direction: {cascade_direction}
- Warning: {cascade_warning}
- Suggested Action: {cascade_suggestion}
"""
    
    # ========================================
    # 🦁 AGENT 1: Macro Seer (整合 v2.0 策略分析)
    # ========================================
    p_macro = profiles.get("macro", {})
    
    macro_prompt = f"""
You are '{p_macro.get('name', 'The Whale Strategist')}'. Your Role: **Grand Strategist with v2.0 Pattern Recognition**.
Your Focus: **Long-term Vision (1-5 Hours) + Whale Strategy Detection**.
Your Bias: {p_macro.get('bias', 'Proactive')}.
Weight in Council: {p_macro.get('weight', 1.0)}.

{cascade_warning_msg}

{v2_prompt_text}

🦁 **v2.0 ENHANCED DECISION RULES**:
1. **ACCUMULATION detected** → Bullish bias, wait for confirmation before LONG
2. **BEAR_TRAP detected** → Expect reversal UP, consider LONG on dip
3. **BULL_TRAP detected** → Expect reversal DOWN, consider SHORT on spike
4. **PUMP_DUMP detected** → Be cautious, avoid chasing, prepare for SHORT
5. **SHAKE_OUT detected** → Wait for dust to settle, then follow whale direction
6. **DUMP detected** → Strongly BEARISH, consider SHORT or HOLD

Your Goal:
1. Analyze the "Big Picture" using v2.0 Whale Strategy Detection.
2. Trust the v2.0 detected strategy over simple whale net qty.
3. Formulate a **Grand Strategy** that aligns with detected whale behavior.

Input Data:
- **v2.0 DETECTED STRATEGY**: {v2_detection.get('detected_strategy', 'UNKNOWN')}
- **v2.0 CONFLICT STATE**: {v2_detection.get('conflict_state', 'UNKNOWN')}
- **v2.0 PREDICTED ACTION**: {v2_detection.get('predicted_action', 'HOLD')}
- **REAL-TIME WHALE STATUS**: Direction={rt_whale.get('current_direction')}, NetQty={rt_whale.get('net_qty_btc', 0)} BTC
- Whale Trend (4H Historical): {whale_long_term['trend']} (Net: {whale_long_term['net_qty']:.2f} BTC)
- Market Regime: {market_regime}

Output:
- Grand Strategy Direction: BULLISH / BEARISH / NEUTRAL
- v2.0 Strategy Alignment: How does your strategy align with detected whale strategy?
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

    # ========================================
    # 🦁 AGENT 2: Micro Tactical (v2.0 信號驗證)
    # ========================================
    p_micro = profiles.get("micro", {})
    
    micro_cascade_msg = ""
    if cascade_active:
        micro_cascade_msg = f"""
🚨 **LIVE LIQUIDATION CASCADE** 🚨
- Strength: {cascade_strength}/100 ({cascade_direction})
"""
    
    micro_prompt = f"""
You are '{p_micro.get('name', 'The Pattern Hunter')}'. Your Role: **Tactical Navigator with v2.0 Signal Validation**.
Your Focus: **Validate the Grand Strategy using v2.0 Key Signals**.
Your Bias: {p_micro.get('bias', 'Adaptive')}.
Weight in Council: {p_micro.get('weight', 1.0)}.

{micro_cascade_msg}

🦁 **v2.0 KEY SIGNALS TO VALIDATE**:
{chr(10).join(['  • ' + s for s in v2_detection.get('key_signals', ['No signals'])[:5]])}

🦁 **v2.0 RISK WARNINGS**:
{chr(10).join(['  ⚠️ ' + w for w in v2_detection.get('risk_warnings', ['No warnings'])[:3]])}

Your Goal:
1. Check if v2.0 key signals support the Grand Strategy.
2. If v2.0 risk warnings are severe, recommend caution.
3. Validate microstructure alignment.

Input Data:
- Grand Strategy: {json.dumps(grand_strategy)}
- **REAL-TIME MICROSTRUCTURE**: OBI={rt_micro.get('obi', 0):.2f}, VPIN={rt_micro.get('vpin', 0):.2f}
- **v2.0 PREDICTION CONFIDENCE**: {v2_detection.get('prediction_confidence', 0):.0%}
- Whale Activity (15m): Net {whale_short_term['net_qty']:.2f} BTC

Output:
- Status: ON_TRACK / MINOR_DEVIATION / MAJOR_THREAT
- v2.0 Signal Alignment: Do key signals support our position?
- Recommendation: CONTINUE / PAUSE / ABORT
"""
    micro_context = f"""
Recent Signals: {signal_summary}
Current Price: {price}
"""

    # ========================================
    # 🦁 AGENT 3: Strategist (風險管理)
    # ========================================
    p_strat = profiles.get("strategist", {})
    hybrid_prompt = f"""
You are '{p_strat.get('name', 'The Risk Master')}'. Your Role: **Risk Manager & Discipline Enforcer**.
Your Focus: **Execution Quality & v2.0 Strategy Consistency**.
Your Bias: {p_strat.get('bias', 'Neutral')}.
Weight in Council: {p_strat.get('weight', 1.0)}.

Your Goal:
1. Evaluate if we are changing goals too often.
2. Check if v2.0 detected strategy has been consistent.
3. Monitor progress and risk exposure.

Input Data:
- Current Plan: {json.dumps(current_plan)}
- v2.0 Detected Strategy: {v2_detection.get('detected_strategy', 'UNKNOWN')}
- v2.0 Risk Warnings Count: {len(v2_detection.get('risk_warnings', []))}

Output:
- Discipline Check: PASS / FAIL
- Action: MAINTAIN_COURSE / REVISE_PLAN
- Risk Assessment: LOW / MEDIUM / HIGH
"""
    hybrid_context = f"""
Time since plan start: {grand_strategy.get('start_time', 'N/A')}
Learning Stats: Accuracy {learning_memory['stats'].get('accuracy', 0)}%
"""

    # 執行辯論
    print(f"   🦁 Lion Council is debating (Model: {model_name})...")
    macro_opinion = get_agent_opinion(client, "Macro", macro_prompt, macro_context, model_name) or "No opinion"
    micro_opinion = get_agent_opinion(client, "Micro", micro_prompt, micro_context, model_name) or "No opinion"
    hybrid_opinion = get_agent_opinion(client, "Hybrid", hybrid_prompt, hybrid_context, model_name) or "No opinion"

    # ========================================
    # 🦁 COMMANDER DECISION (整合 v2.0)
    # ========================================
    cascade_commander_msg = ""
    if cascade_active:
        cascade_commander_msg = f"""
🔥 **LIQUIDATION CASCADE IN PROGRESS** 🔥
- Direction: {cascade_direction}
- Strength: {cascade_strength}/100
"""
    
    commander_prompt = f"""
You are '🦁 The Lion King'. You make the FINAL DECISION using v2.0 Whale Strategy Intelligence.

**OBJECTIVE**: Execute a coherent strategy over 1-5 hours, enhanced by v2.0 pattern recognition.

{cascade_commander_msg}

{v2_prompt_text}

🦁 **v2.0 ENHANCED SANITY CHECK (MANDATORY)**:
1. **v2.0 Detected Strategy OVERRIDES simple whale net qty analysis**.
2. If v2.0 says "BULL_TRAP" → Do NOT go LONG even if net qty is positive.
3. If v2.0 says "BEAR_TRAP" → Do NOT go SHORT even if net qty is negative.
4. If v2.0 says "DUMP" → Strongly BEARISH regardless of other signals.
5. If v2.0 says "ACCUMULATION" → Wait for confirmation, then LONG.
6. Trust v2.0 PREDICTION CONFIDENCE: High (>70%) = Act decisively, Low (<50%) = Be cautious.

Current Grand Strategy:
{json.dumps(grand_strategy)}

Advisor Opinions:
[Macro]: {macro_opinion}
[Micro]: {micro_opinion}
[Strategist]: {hybrid_opinion}

**v2.0 DECISION SUMMARY**:
- Detected Strategy: {v2_detection.get('detected_strategy', 'UNKNOWN')}
- Conflict State: {v2_detection.get('conflict_state', 'UNKNOWN')}
- Predicted Action: {v2_detection.get('predicted_action', 'HOLD')}
- Confidence: {v2_detection.get('prediction_confidence', 0):.0%}

**OUTPUT FORMAT (JSON)**:
{{
  "strategic_bias": "BULLISH|BEARISH",
  "tactical_action": "LONG|SHORT|HOLD|ADD_LONG|ADD_SHORT|CUT_LOSS",
  "recommended_leverage": 1-50,
  "conviction_score": 50-100,
  "whale_reversal_price": 87500,
  "v2_strategy_aligned": true/false,
  "v2_detected_strategy": "{v2_detection.get('detected_strategy', 'UNKNOWN')}",
  "grand_strategy_update": {{
     "active": true,
     "direction": "...",
     "thesis": "...",
     "v2_strategy": "{v2_detection.get('detected_strategy', 'UNKNOWN')}",
     "target_duration_hours": 3,
     "start_time": "{datetime.now().isoformat()}", 
     "invalidation_price": 0
  }},
  "analysis": "Reasoning with v2.0 context...",
  "parameter_updates": {{ ... }}
}}
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
        
        # 處理結果
        analysis_text = result.get('analysis') or "No analysis provided"
        
        new_state = {
            "last_prediction": analysis_text[:100] + "...", 
            "prediction_time": datetime.now().isoformat(),
            "entry_price": price,
            "action": result.get('tactical_action') or 'WAIT',
            "strategic_bias": result.get('strategic_bias') or 'NEUTRAL',
            "confidence": result.get('conviction_score') or 50,
            "whale_reversal_price": result.get('whale_reversal_price', 0),
            "v2_detected_strategy": result.get('v2_detected_strategy', 'UNKNOWN'),
            "full_analysis": f"🦁 LION COMMANDER DECISION:\n{analysis_text}\n\n🗣️ DEBATE HIGHLIGHTS:\nWhale Strategist: {macro_opinion[:100]}...\nPattern Hunter: {micro_opinion[:100]}..."
        }
        save_advisor_state(new_state)
        
        # 更新 Bridge
        bridge = load_bridge()
        wolf_feedback = bridge.get('wolf_to_ai', {})
        
        bridge['ai_to_wolf'] = {
            "command": result.get('tactical_action') or 'WAIT',
            "direction": result.get('strategic_bias') or 'NEUTRAL',
            "confidence": result.get('conviction_score') or 50,
            "leverage": result.get('recommended_leverage') or 1,
            "whale_reversal_price": result.get('whale_reversal_price', 0),
            "take_profit_pct": 10.0,
            "stop_loss_pct": 5.0,
            "v2_detected_strategy": result.get('v2_detected_strategy', 'UNKNOWN'),
            "v2_strategy_aligned": result.get('v2_strategy_aligned', False),
            "reasoning": analysis_text[:200],
            "timestamp": datetime.now().isoformat()
        }
        save_bridge(bridge)

        # 更新 Strategy Plan
        try:
            current_plan = load_strategy_plan()
            
            if 'grand_strategy_update' in result and isinstance(result['grand_strategy_update'], dict):
                current_plan['grand_strategy'] = result['grand_strategy_update']
                
            current_plan['market_bias'] = result.get('strategic_bias', 'NEUTRAL')
            current_plan['phase'] = result.get('tactical_action', 'WAIT')
            current_plan['max_leverage'] = result.get('recommended_leverage', 1)
            current_plan['v2_detected_strategy'] = result.get('v2_detected_strategy', 'UNKNOWN')
            current_plan['created_at'] = datetime.now().isoformat()
            current_plan['plan_id'] = str(uuid.uuid4())
            
            save_strategy_plan(current_plan)
            print(f"   📝 [Plan Synced] Strategy Plan updated to {current_plan['market_bias']} / {current_plan['phase']} (v2.0: {current_plan['v2_detected_strategy']})")
        except Exception as e:
            print(f"   ⚠️ Failed to sync strategy plan: {e}")
        
        # Circuit Breaker
        feedback_loop = bridge.get('feedback_loop', {})
        failure_streak = feedback_loop.get('failure_streak', 0)
        
        if failure_streak >= 5:
            print(f"   🚨 [CIRCUIT BREAKER] Failure streak {failure_streak} detected! Forcing HOLD and RESET.")
            bridge['ai_to_wolf']['command'] = 'HOLD'
            bridge['ai_to_wolf']['reasoning'] = f"CIRCUIT BREAKER: Too many consecutive losses ({failure_streak}). Pausing to realign."
            save_bridge(bridge)
            current_plan['grand_strategy'] = {"active": False}
            save_strategy_plan(current_plan)
            return "CIRCUIT BREAKER TRIGGERED"

        # Bias 防抖動
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
                new_state["strategic_bias"] = current_bias
        else:
             if market_memory["strategic_bias"].get("pending_change"):
                market_memory["strategic_bias"]["pending_change"] = None
        
        save_market_memory(market_memory)
            
        # 格式化輸出
        analysis_preview = (result.get('analysis') or "No analysis")[:120]
        v2_info = f"v2.0: {result.get('v2_detected_strategy', 'UNKNOWN')} (Aligned: {result.get('v2_strategy_aligned', False)})"
        debate_highlights = (
            f"\n   👉 [Macro]: {macro_opinion[:80]}..."
            f"\n   👉 [Micro]: {micro_opinion[:80]}..."
            f"\n   👉 [Strat]: {hybrid_opinion[:80]}..."
        )
        
        return f"🦁 [{result.get('strategic_bias')} | {result.get('tactical_action')} (Lev x{result.get('recommended_leverage', 1)})] {v2_info}\n{analysis_preview}...{debate_highlights}"

    except Exception as e:
        return f"❌ Lion Commander failed: {e}"


def analyze_with_ai(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state):
    """兼容舊接口"""
    return run_council_meeting(trading_data, market_snapshot, signals_df, whale_flip_df, previous_state)


def parse_args():
    """解析命令列參數"""
    parser = argparse.ArgumentParser(description='AI Trading Advisor 🦁 Lion - v2.0 Enhanced')
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
    print("🦁 AI Lion Hunter (v2.0 Whale Strategy Enhanced)")
    print("="*60)
    if end_time:
        print(f"📅 開始時間: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📅 結束時間: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    print("🆕 Features:")
    print("   • v2.0 Whale Strategy Detection (8 strategies)")
    print("   • Enhanced Detectors: S/R Break, Volume Exhaustion, Hidden Orders, P/V Divergence, Waterfall")
    print("   • Conflict State Analysis: Whale vs Retail")
    print("="*60)
    
    while True:
        try:
            if end_time and datetime.now() >= end_time:
                elapsed = datetime.now() - start_time
                print(f"\n⏰ 運行時間已達 {elapsed.total_seconds()/3600:.2f} 小時，自動停止")
                print(f"🛑 AI Lion 已停止 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
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
            
            print(f"\n[{current_time}] 🦁 Analyzing Session: {session_path.name}{remaining}")
            analysis = analyze_with_ai(trading_data, market_snapshot, signals_df, whale_flip_df, prev_state)
            
            print("\n" + analysis)
            print("\n" + "-"*60)
            print(f"💤 Lion is watching... (Next check in {args.interval}s)")
            
            time.sleep(args.interval)
            
        except KeyboardInterrupt:
            elapsed = datetime.now() - start_time
            print(f"\n🛑 AI Lion Stopped. (運行時間: {elapsed.total_seconds()/3600:.2f} 小時)")
            break
        except Exception as e:
            print(f"⚠️ Error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)


if __name__ == "__main__":
    main()
