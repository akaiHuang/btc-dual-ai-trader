#!/bin/bash

# BTC 智能交易系統 - 快速設定腳本
# 用途：初始化開發環境

set -e

echo "=========================================="
echo "🚀 BTC 智能交易系統 - 環境設定"
echo "=========================================="

# 檢查 Python 版本
echo ""
echo "📌 檢查 Python 版本..."
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "當前 Python 版本: $python_version"

if [[ $(echo "$python_version" | cut -d. -f1,2) < "3.11" ]]; then
    echo "❌ 錯誤: 需要 Python 3.11 或更高版本"
    echo "請先安裝 Python 3.11+"
    exit 1
fi

echo "✅ Python 版本符合要求"

# 創建虛擬環境
echo ""
echo "📌 創建虛擬環境..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ 虛擬環境創建成功"
else
    echo "⚠️  虛擬環境已存在，跳過"
fi

# 啟動虛擬環境
echo ""
echo "📌 啟動虛擬環境..."
source venv/bin/activate

# 升級 pip
echo ""
echo "📌 升級 pip..."
pip install --upgrade pip

# 安裝依賴
echo ""
echo "📌 安裝 Python 依賴套件..."
echo "⏳ 這可能需要幾分鐘..."
pip install -r requirements.txt

# 複製配置文件範例
echo ""
echo "📌 設定配置文件..."
if [ ! -f "config/config.json" ]; then
    cp config/config.example.json config/config.json
    echo "✅ 已創建 config/config.json（請編輯此文件填入 API Key）"
else
    echo "⚠️  config/config.json 已存在，跳過"
fi

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "✅ 已創建 .env（請編輯此文件填入環境變數）"
else
    echo "⚠️  .env 已存在，跳過"
fi

# 檢查 Docker
echo ""
echo "📌 檢查 Docker..."
if command -v docker &> /dev/null; then
    echo "✅ Docker 已安裝"
    docker --version
    
    if command -v docker-compose &> /dev/null; then
        echo "✅ Docker Compose 已安裝"
        docker-compose --version
    else
        echo "⚠️  Docker Compose 未安裝"
        echo "請手動安裝: https://docs.docker.com/compose/install/"
    fi
else
    echo "⚠️  Docker 未安裝"
    echo "請手動安裝: https://docs.docker.com/get-docker/"
fi

# 測試程式
echo ""
echo "📌 測試主程式..."
python main.py --mode backtest --strategy BTCHighFreq

echo ""
echo "=========================================="
echo "✅ 環境設定完成！"
echo "=========================================="
echo ""
echo "下一步："
echo "1. 編輯 config/config.json，填入 Binance API Key"
echo "2. 編輯 .env，填入環境變數"
echo "3. 啟動資料庫: docker-compose up -d"
echo "4. 下載歷史資料: python scripts/download_data.py"
echo "5. 開始開發！"
echo ""
echo "查看開發計劃: docs/DEVELOPMENT_PLAN.md"
echo ""
