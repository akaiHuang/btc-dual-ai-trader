#!/bin/bash
# 🚀 智能交易啟動器 v1.0
# 用法: ./start_smart_trading.sh [卡片名稱] [小時數] [BTC數量]
#
# 功能:
# 1. 啟動前執行趨勢分析，自動調整門檻
# 2. 4小時後檢查盈虧，虧損過多則重新調整
# 3. 8小時後自動結束

CARD="${1:-optimal_v1}"
HOURS="${2:-8}"
BTC="${3:-0.002}"
BASE_DEDUCT="${4:-88.88}"

# 虧損閾值 (超過這個就重新分析)
LOSS_THRESHOLD="-1.0"  # -1 USDT

cd /Users/akaihuangm1/Desktop/btn

echo "============================================================"
echo "🚀 智能交易啟動器 v1.0"
echo "============================================================"
echo "📋 卡片: $CARD"
echo "⏰ 時長: ${HOURS}小時"
echo "₿ BTC: $BTC"
echo "💰 Base Deduct: $BASE_DEDUCT"
echo ""

# ============================================================
# Step 1: 啟動前趨勢分析
# ============================================================
echo "📈 [Step 1] 執行趨勢分析..."
.venv/bin/python scripts/trend_analyzer.py --update-if-needed --card "$CARD"

if [ $? -eq 0 ]; then
    echo "✅ 趨勢分析完成"
else
    echo "⚠️ 趨勢分析失敗，使用預設配置"
fi
echo ""

# ============================================================
# Step 2: 啟動交易程式 (背景執行)
# ============================================================
echo "🤖 [Step 2] 啟動交易程式..."
START_TIME=$(date +%s)

# 啟動交易程式 (背景)
.venv/bin/python scripts/whale_testnet_trader.py \
    --sync --paper \
    --card "$CARD" \
    --hours "$HOURS" \
    --btc "$BTC" \
    --base_balance_deduct "$BASE_DEDUCT" \
    2>&1 | tee "logs/smart_trading_$(date +%Y%m%d_%H%M%S).log" &

TRADER_PID=$!
echo "✅ 交易程式已啟動 (PID: $TRADER_PID)"
echo ""

# ============================================================
# Step 3: 4小時後檢查 (背景監控)
# ============================================================
echo "⏰ [Step 3] 設定 4 小時後自動檢查..."

(
    # 等待 4 小時 (14400 秒)
    sleep 14400
    
    # 檢查交易程式是否還在運行
    if ps -p $TRADER_PID > /dev/null 2>&1; then
        echo ""
        echo "============================================================"
        echo "🔍 [4小時檢查] 執行中期盈虧分析..."
        echo "============================================================"
        
        # 分析當前盈虧
        PNL_RESULT=$(.venv/bin/python -c "
import json
import glob
import os

# 找最新的交易紀錄
files = glob.glob('logs/whale_paper_trader/trades_*.json')
if not files:
    print('NO_DATA')
    exit()

latest = max(files, key=os.path.getctime)
with open(latest) as f:
    data = json.load(f)

trades = data.get('trades', [])
if not trades:
    print('NO_TRADES')
    exit()

# 計算總盈虧
total_pnl = sum(t.get('net_pnl_usdt', 0) for t in trades)
win_count = sum(1 for t in trades if t.get('net_pnl_usdt', 0) > 0)
total_count = len(trades)
win_rate = win_count / total_count * 100 if total_count > 0 else 0

print(f'{total_pnl:.2f}|{win_rate:.1f}|{total_count}')
")
        
        if [[ "$PNL_RESULT" == "NO_DATA" ]] || [[ "$PNL_RESULT" == "NO_TRADES" ]]; then
            echo "⚠️ 沒有交易數據，跳過調整"
        else
            CURRENT_PNL=$(echo $PNL_RESULT | cut -d'|' -f1)
            WIN_RATE=$(echo $PNL_RESULT | cut -d'|' -f2)
            TRADE_COUNT=$(echo $PNL_RESULT | cut -d'|' -f3)
            
            echo "📊 4小時統計:"
            echo "   總盈虧: \$${CURRENT_PNL}"
            echo "   勝率: ${WIN_RATE}%"
            echo "   交易數: ${TRADE_COUNT}"
            
            # 判斷是否需要重新分析
            NEED_READJUST=$(echo "$CURRENT_PNL < $LOSS_THRESHOLD" | bc -l)
            
            if [ "$NEED_READJUST" == "1" ]; then
                echo ""
                echo "⚠️ 虧損超過閾值 (${LOSS_THRESHOLD} USDT)，重新執行趨勢分析..."
                .venv/bin/python scripts/trend_analyzer.py --update-if-needed --card "$CARD"
                echo "✅ 門檻已重新調整，新設定將用於後續交易"
            else
                echo "✅ 盈虧正常，無需調整"
            fi
        fi
        echo "============================================================"
    fi
) &

MONITOR_PID=$!
echo "✅ 監控程序已啟動 (PID: $MONITOR_PID)"
echo ""

echo "============================================================"
echo "📝 啟動完成！"
echo "============================================================"
echo ""
echo "💡 提示:"
echo "   - 交易程式 PID: $TRADER_PID"
echo "   - 監控程序 PID: $MONITOR_PID"
echo "   - 查看日誌: tail -f logs/smart_trading_*.log"
echo "   - 停止交易: kill $TRADER_PID"
echo ""
echo "⏰ 時間表:"
echo "   - 現在: 開始交易"
echo "   - 4小時後: 自動檢查盈虧"
echo "   - 8小時後: 自動結束"
echo ""

# 等待交易程式結束
wait $TRADER_PID
echo ""
echo "============================================================"
echo "🏁 交易程式已結束"
echo "============================================================"

# 清理監控程序
kill $MONITOR_PID 2>/dev/null

# 最終統計
echo ""
echo "📊 最終統計:"
.venv/bin/python -c "
import json
import glob
import os

files = glob.glob('logs/whale_paper_trader/trades_*.json')
if not files:
    print('沒有交易數據')
    exit()

latest = max(files, key=os.path.getctime)
with open(latest) as f:
    data = json.load(f)

trades = data.get('trades', [])
if not trades:
    print('沒有交易')
    exit()

total_pnl = sum(t.get('net_pnl_usdt', 0) for t in trades)
win_count = sum(1 for t in trades if t.get('net_pnl_usdt', 0) > 0)
total_count = len(trades)
win_rate = win_count / total_count * 100 if total_count > 0 else 0

print(f'   總交易: {total_count} 筆')
print(f'   勝率: {win_rate:.1f}%')
print(f'   總盈虧: \${total_pnl:.2f}')
"
