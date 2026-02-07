#!/bin/bash
# 重啟 AI Trading System 以載入 Bridge 增強功能

echo "🔄 重啟 AI Trading System..."
echo ""

# 1. 停止現有進程
echo "1️⃣ 停止現有進程..."
pkill -f "ai_trading_advisor.py" 2>/dev/null
pkill -f "start_trading_bot.py" 2>/dev/null
sleep 2

# 2. 清理舊 Bridge (備份)
echo "2️⃣ 備份舊 Bridge..."
if [ -f "ai_wolf_bridge.json" ]; then
    cp ai_wolf_bridge.json "ai_wolf_bridge.backup_$(date +%Y%m%d_%H%M%S).json"
    echo "   ✅ 備份完成"
fi

# 3. 啟動 AI Advisor
echo "3️⃣ 啟動 AI Advisor..."
.venv/bin/python scripts/ai_trading_advisor.py > logs/ai_advisor_$(date +%Y%m%d_%H%M%S).log 2>&1 &
AI_PID=$!
echo "   ✅ AI Advisor PID: $AI_PID"

# 4. 等待 5 秒
sleep 5

# 5. 啟動 Trading Bot (Mode 8 = M_AI_WHALE_HUNTER)
echo "4️⃣ 啟動 Trading Bot (M🐺 Mode)..."
.venv/bin/python start_trading_bot.py 8 > logs/trading_bot_$(date +%Y%m%d_%H%M%S).log 2>&1 &
BOT_PID=$!
echo "   ✅ Trading Bot PID: $BOT_PID"

echo ""
echo "🎉 系統啟動完成！"
echo ""
echo "📊 監控指令:"
echo "   tail -f logs/ai_advisor_*.log     # 查看 AI 日誌"
echo "   tail -f logs/trading_bot_*.log    # 查看交易日誌"
echo ""
echo "🧪 測試指令:"
echo "   .venv/bin/python scripts/test_bridge_enhancement.py  # 測試 Bridge"
echo ""
echo "🛑 停止指令:"
echo "   pkill -f ai_trading_advisor.py && pkill -f start_trading_bot.py"
echo ""
