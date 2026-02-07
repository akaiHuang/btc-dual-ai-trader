#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime
from pathlib import Path

def find_latest_session():
    base_path = Path("data/paper_trading")
    if not base_path.exists():
        return None
    sessions = [d for d in base_path.iterdir() if d.is_dir() and d.name.startswith("pt_")]
    if not sessions:
        return None
    return max(sessions, key=lambda d: d.stat().st_mtime)

def main():
    # 1. Read AI State
    ai_state = {}
    if os.path.exists("ai_advisor_state.json"):
        try:
            with open("ai_advisor_state.json", "r") as f:
                ai_state = json.load(f)
        except:
            pass

    # 2. Read Trading Data
    session_path = find_latest_session()
    trading_data = {}
    m_wolf_orders = []
    m_wolf_pnl = 0.0
    m_wolf_trades_count = 0
    
    if session_path:
        data_file = session_path / "trading_data.json"
        if data_file.exists():
            try:
                with open(data_file, "r") as f:
                    trading_data = json.load(f)
                    m_wolf_orders = trading_data.get("orders", {}).get("M_AI_WHALE_HUNTER", [])
                    
                    # Calculate PnL
                    for order in m_wolf_orders:
                        if order.get("exit_time"):
                            m_wolf_pnl += order.get("pnl_usdt", 0.0)
                            m_wolf_trades_count += 1
            except:
                pass

    # 3. Construct Output
    output = {
        "timestamp": datetime.now().isoformat(),
        "ai_advisor": {
            "action": ai_state.get("action", "UNKNOWN"),
            "confidence": ai_state.get("confidence", 0),
            "bias": ai_state.get("strategic_bias", "UNKNOWN"),
            "whale_reversal_price": ai_state.get("whale_reversal_price", 0),  # 🆕 預測的反轉價格
            "prediction_time": ai_state.get("prediction_time"),
            "last_prediction": ai_state.get("last_prediction", "")[:100] + "..."
        },
        "m_wolf_status": {
            "active": len(m_wolf_orders) > 0,
            "total_trades": m_wolf_trades_count,
            "open_positions": len([o for o in m_wolf_orders if not o.get('exit_time')]),
            "current_pnl_usdt": round(m_wolf_pnl, 2),
            "consistency": "PREEMPTIVE_MODE" if ai_state.get("action") in ["LONG", "SHORT", "ADD_LONG", "ADD_SHORT"] else "WAIT_MODE"
        },
        "market_context": {
            "current_price": ai_state.get("entry_price", 0),
            "distance_to_reversal": round(ai_state.get("whale_reversal_price", 0) - ai_state.get("entry_price", 0), 2) if ai_state.get("whale_reversal_price") else None
        },
        "recent_trades": m_wolf_orders[-3:] if m_wolf_orders else []  # 顯示最近3筆交易
    }

    print(json.dumps(output, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
