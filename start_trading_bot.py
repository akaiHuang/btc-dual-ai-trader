#!/usr/bin/env python3
"""
🐺 AI Whale Hunter Trading Bot 啟動器
解決輸出緩衝問題的包裝腳本
"""
import sys
import os
import asyncio
import signal
from datetime import datetime
from pathlib import Path

# 禁用輸出緩衝
os.environ['PYTHONUNBUFFERED'] = '1'

def cleanup_stale_datahub():
    """清理過期的 DataHub 快取（啟動前執行）"""
    import time
    import json
    
    cache_file = Path("/tmp/dydx_data_hub.json")
    lock_file = Path("/tmp/dydx_data_hub.lock")
    
    if not cache_file.exists():
        return
    
    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
        
        last_update = data.get('last_update', 0)
        master_pid = data.get('master_pid', 0)
        age = time.time() - last_update
        
        # 如果數據過期超過 30 秒，檢查 master 是否還活著
        if age > 30 and master_pid > 0:
            try:
                os.kill(master_pid, 0)
                # 進程還在，不清理
            except OSError:
                # 進程已死，清理快取
                print(f"🗑️ 清理過期 DataHub 快取 (舊 Master PID: {master_pid}, 過期: {age:.0f}秒)")
                cache_file.unlink(missing_ok=True)
                lock_file.unlink(missing_ok=True)
    except Exception as e:
        # 靜默忽略錯誤
        pass

def signal_handler(signum, frame):
    """處理中斷信號"""
    print("\n\n⚠️  收到中斷信號，正在停止...")
    sys.exit(0)

# 註冊信號處理器
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# 🔧 v14.10: 啟動前清理過期的 DataHub 快取
cleanup_stale_datahub()

print("=" * 80)
print("🐺 AI Whale Hunter Trading Bot")
print("=" * 80)
print(f"⏰ 啟動時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"🐍 Python: {sys.version.split()[0]}")

# 獲取測試時長
duration = 8.0  # 預設 8 小時
if len(sys.argv) > 1:
    try:
        duration = float(sys.argv[1])
        print(f"⏱️  測試時長: {duration} 小時")
    except:
        print(f"⚠️  無效參數，使用預設值: {duration} 小時")
else:
    print(f"⏱️  測試時長: {duration} 小時（預設）")

# 檢查 AI Advisor 狀態
ai_state_file = "ai_advisor_state.json"
if os.path.exists(ai_state_file):
    print(f"✅ AI 狀態文件存在: {ai_state_file}")
    try:
        import json
        with open(ai_state_file, 'r') as f:
            state = json.load(f)
        action = state.get('action', 'N/A')
        conf = state.get('confidence', 0)
        pred_time = state.get('prediction_time', 'N/A')
        print(f"📊 當前 AI 決策: {action} (信心: {conf}%)")
        print(f"🕐 預測時間: {pred_time}")
    except Exception as e:
        print(f"⚠️  無法讀取 AI 狀態: {e}")
else:
    print(f"⚠️  AI 狀態文件不存在")
    print(f"💡 建議先啟動 AI Advisor: .venv/bin/python scripts/ai_trading_advisor.py")

print("=" * 80)
print()

# 導入並運行
try:
    from scripts.paper_trading_hybrid_full import HybridPaperTradingSystem
    
    print("🔧 初始化交易系統...")
    system = HybridPaperTradingSystem(test_duration_hours=duration)
    
    print("🚀 開始運行...")
    print("=" * 80)
    asyncio.run(system.run())
    
except KeyboardInterrupt:
    print("\n\n⚠️  用戶中斷")
except Exception as e:
    print(f"\n\n❌ 錯誤: {e}")
    import traceback
    traceback.print_exc()
finally:
    print(f"\n🏁 結束時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
