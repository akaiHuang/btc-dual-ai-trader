import sys
import os
import json
import time
import asyncio
from datetime import datetime

# Add scripts to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from scripts.paper_trading_hybrid_full import HybridPaperTradingSystem, TradingMode

# Config files
BRIDGE_FILE = "ai_wolf_bridge.json"
PROFIT_CONFIG_FILE = "ai_profit_config.json"

def setup_config_files():
    # 1. Setup Profit Config
    profit_config = {
        "dynamic_profit_taking": {
            "enabled": True,
            "base_targets": {"standard": 0.8},
            "performance_based_adjustment": {"enabled": False},
            "progressive_targets": {"enabled": False},
            "trailing_stop": {"enabled": False}
        }
    }
    with open(PROFIT_CONFIG_FILE, 'w') as f:
        json.dump(profit_config, f, indent=2)
    print(f"✅ Created {PROFIT_CONFIG_FILE}")

def set_ai_command(command, direction="NEUTRAL", mock_obi=0.0, mock_whale_dir=None):
    bridge_data = {
        "ai_to_wolf": {
            "command": command,
            "direction": direction,
            "confidence": 85,
            "leverage": 50,
            "whale_reversal_price": 90000,
            "timestamp": datetime.now().isoformat()
        },
        "wolf_to_ai": {
            "market_microstructure": {
                "obi": mock_obi,
                "vpin": 0.2
            },
            "whale_status": {
                "current_direction": mock_whale_dir,
                "dominance": 0.9
            },
            "volatility": {
                "atr_pct": 0.001,
                "is_dead_market": True
            }
        }
    }
    with open(BRIDGE_FILE, 'w') as f:
        json.dump(bridge_data, f, indent=2)
    print(f"🤖 AI Command sent: {command} ({direction}) | OBI: {mock_obi} | Whale: {mock_whale_dir}")

class MockTradingSystem(HybridPaperTradingSystem):
    def __init__(self):
        # Initialize with minimal duration
        super().__init__(test_duration_hours=0.1)
        self.mock_price = 88000.0
        self.mock_obi = 0.5
        self.mock_vpin = 0.2
        
    def _build_market_snapshot(self):
        # Return mock snapshot
        ts = datetime.now().isoformat()
        self.orderbook_timestamp = ts
        return {
            'timestamp': ts,
            'price': self.mock_price,
            'obi': self.mock_obi,
            'vpin': self.mock_vpin,
            'spread': 0.01,
            'funding_rate': 0.0001,
            'open_interest': [],
            'global_long_short': [],
            'liquidation_pressure': {},
            'volatility': {'atr_pct': 0.001, 'is_dead_market': False}
        }
        
    async def connect_websocket(self):
        # Mock websocket connection
        pass

async def run_test():
    setup_config_files()
    
    print("\n🐺 Initializing Mock M-Wolf System...")
    system = MockTradingSystem()
    
    # Ensure M_AI_WHALE_HUNTER is active
    if TradingMode.M_AI_WHALE_HUNTER not in system.active_modes:
        system.active_modes.append(TradingMode.M_AI_WHALE_HUNTER)
        system.balances[TradingMode.M_AI_WHALE_HUNTER] = 100.0
        system.orders[TradingMode.M_AI_WHALE_HUNTER] = []
        
    # Initialize mode configs
    system._sync_configs()
        
    print("✅ System initialized")
    
    # --- TEST CASE 1: AI SAYS SHORT ---
    print("\n🧪 TEST 1: AI says SHORT")
    set_ai_command("SHORT", "BEARISH", mock_obi=-0.5, mock_whale_dir="SHORT")
    
    # 1. Update internal state
    system.latest_price = 88000.0
    system.mock_price = 88000.0
    system.mock_obi = -0.5 # Bearish OBI to support SHORT
    
    # Mock Whale Signal (Agrees with AI)
    system.large_trade_signal = {
        'net_qty': -50, 
        'dominance_ratio': 0.9, 
        'direction': 'SHORT',
        'timestamp': time.time()
    }
    
    # 2. Execute Strategy
    print("   Running check_entries...")
    snapshot = system._build_market_snapshot()
    system.check_entries(snapshot)
    
    # Check orders
    orders = system.orders[TradingMode.M_AI_WHALE_HUNTER]
    if len(orders) > 0 and orders[-1].direction == "SHORT":
        print("✅ PASS: M-Wolf entered SHORT as commanded!")
        print(f"   Order: {orders[-1].direction} @ {orders[-1].entry_price}")
    else:
        print("❌ FAIL: M-Wolf did not enter SHORT.")
        print(f"   Orders: {len(orders)}")
        return

    # --- TEST CASE 2: AI FLIP TO LONG ---
    print("\n🧪 TEST 2: AI Flips to LONG (Expect Close SHORT + Open LONG)")
    set_ai_command("LONG", "BULLISH", mock_obi=0.5, mock_whale_dir="LONG")
    
    # Update price to simulate slight movement
    system.latest_price = 87800.0 # Price dropped (Short is profitable)
    system.mock_price = 87800.0
    system.mock_obi = 0.5 # Bullish OBI
    
    # Mock Whale Signal (Agrees with LONG)
    system.large_trade_signal = {
        'net_qty': 50, 
        'dominance_ratio': 0.9, 
        'direction': 'LONG',
        'timestamp': time.time()
    }
    
    # Run exit check first (Flip logic is in check_exits)
    print("   Checking exits (looking for Flip)...")
    snapshot = system._build_market_snapshot()
    system.check_exits(snapshot)
    
    # Check if SHORT is closed
    closed_orders = [o for o in orders if o.exit_time is not None]
    if len(closed_orders) > 0 and "AI Flip" in closed_orders[-1].exit_reason:
        print("✅ PASS: M-Wolf closed SHORT due to AI Flip!")
        print(f"   Exit Reason: {closed_orders[-1].exit_reason}")
    else:
        print("❌ FAIL: M-Wolf did not close SHORT on Flip.")
        # Debug info
        active_orders = [o for o in orders if o.exit_time is None]
        print(f"   Active Orders: {len(active_orders)}")
        if len(closed_orders) > 0:
             print(f"   Last Exit Reason: {closed_orders[-1].exit_reason}")
        return

    # Now check if it opens LONG
    print("   Running check_entries for Entry...")
    system.check_entries(snapshot)
    
    # Check for new LONG order
    # The orders list contains all orders. The last one should be LONG and open.
    last_order = system.orders[TradingMode.M_AI_WHALE_HUNTER][-1]
    if last_order.direction == "LONG" and last_order.exit_time is None:
        print("✅ PASS: M-Wolf entered LONG after Flip!")
        print(f"   Order: {last_order.direction} @ {last_order.entry_price}")
    else:
        print("❌ FAIL: M-Wolf did not enter LONG after Flip.")
        print(f"   Last Order: {last_order.direction} (Exit: {last_order.exit_time})")

if __name__ == "__main__":
    asyncio.run(run_test())
