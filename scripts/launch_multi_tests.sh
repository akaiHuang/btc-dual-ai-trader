#!/bin/bash

# 多視窗交易測試啟動腳本
# Purpose: 在外部終端一次性啟動所有測試
# Usage: bash scripts/launch_multi_tests.sh [duration_hours]

# 顏色定義
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 配置
DURATION=${1:-24}  # 默認 24 小時
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_DIR="data/test_runs/${TIMESTAMP}"
VENV_PYTHON=".venv/bin/python"

# 創建日誌目錄
mkdir -p "${LOG_DIR}/logs"
mkdir -p "${LOG_DIR}/results"
mkdir -p "${LOG_DIR}/snapshots"

echo "=================================================================="
echo -e "${CYAN}🚀 多視窗交易測試啟動器${NC}"
echo "=================================================================="
echo ""
echo -e "${YELLOW}測試配置:${NC}"
echo "  時長: ${DURATION} 小時"
echo "  開始: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  日誌: ${LOG_DIR}"
echo ""

# 檢測操作系統
OS_TYPE=$(uname)

# 啟動函數 - macOS
launch_macos() {
    local title=$1
    local script=$2
    local log=$3
    
    osascript <<EOF
tell application "Terminal"
    do script "cd $(pwd) && echo '${title}' && ${VENV_PYTHON} ${script} 2>&1 | tee ${log}"
end tell
EOF
}

# 啟動函數 - Linux
launch_linux() {
    local title=$1
    local script=$2
    local log=$3
    
    if command -v gnome-terminal &> /dev/null; then
        gnome-terminal --title="${title}" -- bash -c "cd $(pwd) && ${VENV_PYTHON} ${script} 2>&1 | tee ${log}; exec bash"
    elif command -v xterm &> /dev/null; then
        xterm -T "${title}" -e "cd $(pwd) && ${VENV_PYTHON} ${script} 2>&1 | tee ${log}; bash" &
    else
        echo "無法找到終端模擬器"
        return 1
    fi
}

# 根據系統選擇啟動函數
if [ "$OS_TYPE" = "Darwin" ]; then
    LAUNCH_FUNC=launch_macos
    echo -e "${GREEN}✓ 檢測到 macOS，使用 Terminal.app${NC}"
else
    LAUNCH_FUNC=launch_linux
    echo -e "${GREEN}✓ 檢測到 Linux，使用 gnome-terminal/xterm${NC}"
fi

echo ""
echo "=================================================================="
echo -e "${CYAN}啟動測試視窗...${NC}"
echo "=================================================================="
echo ""

# Test 1: 真實數據收集
echo -e "${BLUE}[1/4]${NC} 📥 真實 WebSocket 數據收集"
echo "      → ${LOG_DIR}/logs/data_collection.log"
$LAUNCH_FUNC \
    "📥 Data Collection" \
    "scripts/collect_historical_snapshots.py ${DURATION} ${LOG_DIR}/snapshots" \
    "${LOG_DIR}/logs/data_collection.log"
sleep 2

# Test 2: Phase C 原始參數測試
echo -e "${BLUE}[2/4]${NC} 💹 Phase C 策略測試（原始參數）"
echo "      → ${LOG_DIR}/logs/phase_c_original.log"
$LAUNCH_FUNC \
    "💹 Phase C Original" \
    "scripts/real_trading_simulation.py ${DURATION} ${LOG_DIR}/results/phase_c_original.json" \
    "${LOG_DIR}/logs/phase_c_original.log"
sleep 2

# Test 3: Phase C 調整參數測試
echo -e "${BLUE}[3/4]${NC} 🔧 Phase C 策略測試（調整參數）"
echo "      → ${LOG_DIR}/logs/phase_c_adjusted.log"
$LAUNCH_FUNC \
    "🔧 Phase C Adjusted" \
    "scripts/real_trading_simulation_adjusted.py ${DURATION} ${LOG_DIR}/results/phase_c_adjusted.json" \
    "${LOG_DIR}/logs/phase_c_adjusted.log"
sleep 2

# Test 4: HFT 對比測試
echo -e "${BLUE}[4/4]${NC} ⚡ 高頻交易策略對比"
echo "      → ${LOG_DIR}/logs/hft_comparison.log"
$LAUNCH_FUNC \
    "⚡ HFT Comparison" \
    "scripts/simple_hft_comparison.py ${DURATION} ${LOG_DIR}/results/hft_comparison.json" \
    "${LOG_DIR}/logs/hft_comparison.log"
sleep 2

echo ""
echo "=================================================================="
echo -e "${GREEN}✅ 所有測試視窗已啟動${NC}"
echo "=================================================================="
echo ""
echo -e "${YELLOW}📊 監控指令:${NC}"
echo ""
echo "  # 查看各測試進度"
echo "  tail -f ${LOG_DIR}/logs/data_collection.log"
echo "  tail -f ${LOG_DIR}/logs/phase_c_original.log"
echo "  tail -f ${LOG_DIR}/logs/phase_c_adjusted.log"
echo "  tail -f ${LOG_DIR}/logs/hft_comparison.log"
echo ""
echo "  # 查看所有測試（分割視窗）"
echo "  tmux new-session \\; \\"
echo "    split-window -h \\; \\"
echo "    split-window -v \\; \\"
echo "    select-pane -t 0 \\; \\"
echo "    split-window -v \\; \\"
echo "    send-keys -t 0 'tail -f ${LOG_DIR}/logs/data_collection.log' C-m \\; \\"
echo "    send-keys -t 1 'tail -f ${LOG_DIR}/logs/phase_c_original.log' C-m \\; \\"
echo "    send-keys -t 2 'tail -f ${LOG_DIR}/logs/phase_c_adjusted.log' C-m \\; \\"
echo "    send-keys -t 3 'tail -f ${LOG_DIR}/logs/hft_comparison.log' C-m"
echo ""
echo -e "${YELLOW}📈 結果分析:${NC}"
echo ""
echo "  # 測試完成後生成對比報告"
echo "  python scripts/generate_comparison_report.py ${LOG_DIR}"
echo ""
echo -e "${YELLOW}🛑 停止所有測試:${NC}"
echo ""
echo "  # 查找並停止所有測試進程"
echo "  ps aux | grep 'python.*real_trading_simulation\\|collect_historical\\|hft_comparison' | grep -v grep | awk '{print \$2}' | xargs kill"
echo ""
echo "=================================================================="
echo -e "${CYAN}測試運行中... 預計完成時間: $(date -v+${DURATION}H '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -d "+${DURATION} hours" '+%Y-%m-%d %H:%M:%S' 2>/dev/null)${NC}"
echo "=================================================================="
echo ""

# 創建信息文件
cat > "${LOG_DIR}/README.txt" <<EOF
測試運行信息
============

開始時間: $(date '+%Y-%m-%d %H:%M:%S')
測試時長: ${DURATION} 小時
預計結束: $(date -v+${DURATION}H '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -d "+${DURATION} hours" '+%Y-%m-%d %H:%M:%S' 2>/dev/null)

測試項目:
---------
1. 真實數據收集
   - WebSocket: depth20@100ms + aggTrade
   - 保存位置: snapshots/
   - 用途: 未來準確回測

2. Phase C 原始參數
   - VPIN 閾值: 0.5
   - 信號閾值: 0.6
   - 風險過濾: DANGER/CRITICAL 阻擋

3. Phase C 調整參數
   - VPIN 閾值: 0.7 (放寬)
   - 信號閾值: 0.5 (降低)
   - 風險過濾: 僅 CRITICAL 阻擋

4. HFT 簡單策略
   - 策略: 價格偏離 > 0.02%
   - 用途: 對比 Phase C 保守程度

日誌文件:
---------
- data_collection.log: 數據收集進度
- phase_c_original.log: 原始參數測試
- phase_c_adjusted.log: 調整參數測試
- hft_comparison.log: HFT 對比測試

結果文件:
---------
- phase_c_original.json: 原始參數交易記錄
- phase_c_adjusted.json: 調整參數交易記錄
- hft_comparison.json: HFT 交易記錄

分析:
-----
測試結束後運行:
  python scripts/generate_comparison_report.py ${LOG_DIR}

生成對比報告，包含:
  - 交易次數對比
  - 收益對比
  - 參數調整效果分析
  - 數據源影響分析
EOF

echo -e "${GREEN}✓ 測試信息已保存到 ${LOG_DIR}/README.txt${NC}"
echo ""
