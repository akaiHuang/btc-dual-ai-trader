#!/bin/bash
# 🐺 AI Whale Hunter 雙進程啟動腳本

echo "======================================"
echo "🐺 AI Whale Hunter System Launcher"
echo "======================================"
echo ""

# 檢查是否有舊進程
echo "🔍 檢查舊進程..."
OLD_ADVISOR_GPT=$(pgrep -f "ai_trading_advisor_gpt.py")
OLD_ADVISOR_QWEN=$(pgrep -f "ai_trading_advisor_qwen.py")
OLD_ADVISOR_LEGACY=$(pgrep -f "ai_trading_advisor.py")
OLD_DRAGON_LEGACY=$(pgrep -f "ai_dragon_advisor.py")
OLD_TRADING=$(pgrep -f "paper_trading_hybrid_full.py")

if [ ! -z "$OLD_ADVISOR_GPT" ]; then
    echo "⚠️  發現舊的 AI Advisor (GPT) 進程: $OLD_ADVISOR_GPT"
    kill $OLD_ADVISOR_GPT
    echo "✅ 已停止 AI Advisor (GPT)"
fi

if [ ! -z "$OLD_ADVISOR_QWEN" ]; then
    echo "⚠️  發現舊的 AI Advisor (Qwen) 進程: $OLD_ADVISOR_QWEN"
    kill $OLD_ADVISOR_QWEN
    echo "✅ 已停止 AI Advisor (Qwen)"
fi

if [ ! -z "$OLD_ADVISOR_LEGACY" ]; then
    echo "⚠️  發現舊的 AI Advisor (Legacy) 進程: $OLD_ADVISOR_LEGACY"
    kill $OLD_ADVISOR_LEGACY
    echo "✅ 已停止 AI Advisor (Legacy)"
fi

if [ ! -z "$OLD_DRAGON_LEGACY" ]; then
    echo "⚠️  發現舊的 Dragon Advisor (Legacy) 進程: $OLD_DRAGON_LEGACY"
    kill $OLD_DRAGON_LEGACY
    echo "✅ 已停止 Dragon Advisor (Legacy)"
fi

if [ ! -z "$OLD_TRADING" ]; then
    echo "⚠️  發現舊的 Trading Bot 進程: $OLD_TRADING"
    read -p "是否停止？(y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        kill $OLD_TRADING
        echo "✅ 已停止舊的 Trading Bot"
    fi
fi

echo ""
echo "======================================"
echo "🚀 啟動系統"
echo "======================================"
echo ""

# 啟動 AI Advisor GPT（背景）
echo "🤖 啟動 AI Advisor (M_WOLF - GPT-4o-mini)..."
nohup .venv/bin/python -u scripts/ai_trading_advisor_gpt.py > logs/ai_advisor_gpt.log 2>&1 &
ADVISOR_GPT_PID=$!
echo "   ✅ AI Advisor (GPT) PID: $ADVISOR_GPT_PID"

# 啟動 AI Advisor Qwen（背景）
echo "🐲 啟動 AI Advisor (M_DRAGON - Kimi-k2)..."
nohup .venv/bin/python -u scripts/ai_trading_advisor_qwen.py > logs/ai_advisor_qwen.log 2>&1 &
ADVISOR_QWEN_PID=$!
echo "   ✅ AI Advisor (Dragon) PID: $ADVISOR_QWEN_PID"

# 等待 AI 初始化
echo ""
echo "⏳ 等待 AI 初始化 (10秒)..."
sleep 10

# 檢查 AI 是否成功啟動
if ps -p $ADVISOR_PID > /dev/null; then
    echo "   ✅ AI Advisor 運行中"
else
    echo "   ❌ AI Advisor 啟動失敗"
    exit 1
fi

# 檢查狀態文件
if [ -f "ai_advisor_state.json" ]; then
    echo "   ✅ AI 狀態文件已生成"
    ACTION=$(cat ai_advisor_state.json | grep '"action"' | cut -d'"' -f4)
    CONF=$(cat ai_advisor_state.json | grep '"confidence"' | cut -d':' -f2 | tr -d ' ,')
    echo "   📊 當前 AI 決策: $ACTION (信心: $CONF%)"
else
    echo "   ⏳ AI 狀態文件尚未生成（等待第一次分析）"
fi

echo ""
echo "======================================"
echo "🎯 啟動 Trading Bot"
echo "======================================"
echo ""

# 獲取測試時長
DURATION=8
if [ ! -z "$1" ]; then
    DURATION=$1
fi

echo "⏰ 測試時長: $DURATION 小時"
echo ""
echo "🎮 Trading Bot 即將啟動（前台運行）..."
echo "💡 按 Ctrl+C 可隨時停止"
echo ""
echo "======================================"
echo ""

# 啟動 Trading Bot（前台）
.venv/bin/python -u scripts/paper_trading_hybrid_full.py $DURATION

# Trading Bot 結束後
echo ""
echo "======================================"
echo "🛑 Trading Bot 已停止"
echo "======================================"
echo ""

# 詢問是否停止 AI Advisor
read -p "是否停止 AI Advisor & Dragon？(y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    kill $ADVISOR_PID 2>/dev/null
    kill $DRAGON_PID 2>/dev/null
    echo "✅ AI Advisor & Dragon 已停止"
else
    echo "💡 AI Advisor 繼續運行（PID: $ADVISOR_PID）"
    echo "💡 AI Dragon 繼續運行（PID: $DRAGON_PID）"
    echo "   查看日誌: tail -f logs/ai_advisor.log"
    echo "   停止方式: kill $ADVISOR_PID $DRAGON_PID"
fi

echo ""
echo "======================================"
echo "✅ 完成"
echo "======================================"
