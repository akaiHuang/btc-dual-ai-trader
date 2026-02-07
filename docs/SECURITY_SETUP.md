# 🔐 安全配置指南

## 📋 目錄
- [API Key 設定](#api-key-設定)
- [安全檢查清單](#安全檢查清單)
- [測試流程](#測試流程)
- [常見問題](#常見問題)

---

## 🔑 API Key 設定

### 1. 獲取 Binance Testnet API Key

**已完成 ✅**
- API Key: `gfjOUWIAwrZoKPP1OexUu8SCert3ihxilwC9a3Mr2DBUkGQSQUYRPf2VGmWLOyUE`
- Secret Key: **請自行安全保存**

**權限設定：**
- ✅ TRADE（交易）
- ✅ USER_DATA（查詢帳戶）
- ✅ USER_STREAM（WebSocket）

### 2. 填入 `.env` 文件

```bash
# 在 .env 文件中找到以下兩行：
BINANCE_API_KEY=gfjOUWIAwrZoKPP1OexUu8SCert3ihxilwC9a3Mr2DBUkGQSQUYRPf2VGmWLOyUE
BINANCE_API_SECRET=YOUR_SECRET_KEY_HERE  # ⬅️ 把你的 Secret Key 貼在這裡
```

**⚠️ 重要提醒：**
- `.env` 文件已經在 `.gitignore` 中，**絕對不會被 Git 追蹤**
- 請勿將 Secret Key 複製到任何公開位置
- 如果不小心洩漏，請立即到 Binance 刪除該 API Key

---

## 🛡️ 安全檢查清單

### 自動檢查（推薦）

```bash
# 運行安全掃描腳本
python scripts/check_security.py
```

**此腳本會檢查：**
- ✅ `.env` 是否被正確排除在 Git 之外
- ✅ 代碼中是否有硬編碼的敏感資訊
- ✅ 配置文件是否包含真實密碼

### 手動檢查

**文件保護：**
- [ ] `.env` 在 `.gitignore` 中
- [ ] `config/config.json` 在 `.gitignore` 中
- [ ] `config/secrets.json` 在 `.gitignore` 中

**代碼檢查：**
- [ ] 所有 Python 文件無硬編碼密碼
- [ ] 所有配置使用環境變數或 `config.py`
- [ ] 日誌不記錄完整 API Secret

**Git 檢查：**
```bash
# 確認 .env 未被追蹤
git status

# 應該看到：
# Untracked files:
#   .env
```

---

## 🧪 測試流程

### 步驟 1: 安裝依賴

```bash
# 運行自動化設定腳本
./setup.sh

# 或手動安裝
pip install -r requirements.txt
```

### 步驟 2: 填入 Secret Key

```bash
# 編輯 .env 文件
open .env  # macOS
# 或
nano .env  # Linux/Terminal

# 找到這一行並填入你的 Secret Key：
BINANCE_API_SECRET=<貼上你的 Secret Key>
```

### 步驟 3: 運行連線測試

```bash
python scripts/test_binance_connection.py
```

**預期輸出：**
```
🔗 測試 Binance API 連線...
============================================================
📍 環境: Testnet
✅ API Key: gfjOUWIA...WLOyUE

1️⃣  測試伺服器連線...
   ✅ 伺服器時間: 1699999999999

2️⃣  測試帳戶權限...
   ✅ 帳戶類型: SPOT
   ✅ 可以交易: True
   ✅ 可以提現: True

3️⃣  測試帳戶餘額...
   ⚠️  所有餘額為 0（Testnet 帳戶需要充值測試幣）

4️⃣  測試市場資料...
   ✅ BTC/USDT 當前價格: $35,000.00

5️⃣  測試訂單簿資料...
   ✅ 買單數量: 5
   ✅ 賣單數量: 5

6️⃣  測試交易權限...
   ✅ 交易對狀態: TRADING

============================================================
🎉 所有測試通過！API Key 配置正確
```

### 步驟 4: 充值測試幣（如需要）

如果測試通過但餘額為 0：
1. 前往 [Binance Testnet Faucet](https://testnet.binance.vision/)
2. 登入後點擊 "Get Test Funds"
3. 獲取測試 BTC 和 USDT

---

## ❓ 常見問題

### Q1: 測試失敗 - "Invalid API Key"

**原因：**
- API Key 或 Secret Key 輸入錯誤
- 複製時包含了多餘的空格

**解決方案：**
```bash
# 重新檢查 .env 文件
cat .env | grep BINANCE

# 確保沒有前後空格
BINANCE_API_KEY=gfjOUWIAwrZoKPP1OexUu8SCert3ihxilwC9a3Mr2DBUkGQSQUYRPf2VGmWLOyUE
BINANCE_API_SECRET=你的Secret（前後無空格）
```

### Q2: 測試失敗 - "Timestamp for this request is outside of the recvWindow"

**原因：**
- 本機時間與 Binance 伺服器時間不同步

**解決方案（macOS）：**
```bash
# 開啟系統偏好設定 > 日期與時間 > 自動設定日期與時間
sudo sntp -sS time.apple.com
```

### Q3: 不小心將 API Key 提交到 Git

**緊急處理：**
```bash
# 1. 立即到 Binance 刪除該 API Key
# 2. 生成新的 API Key
# 3. 清理 Git 歷史（如果已 push）
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch .env" \
  --prune-empty --tag-name-filter cat -- --all

# 4. 強制推送（危險操作，僅在必要時使用）
git push origin --force --all
```

### Q4: 正式環境 API Key 應該如何保護？

**最佳實踐：**

1. **生產環境配置：**
```bash
# 使用 CI/CD 平台的 Secret 管理
# GitHub Actions: Settings > Secrets
# AWS: Systems Manager Parameter Store
# Docker: docker-compose secrets
```

2. **IP 白名單：**
- 在 Binance 後台設定 IP 白名單
- 限制只有你的伺服器 IP 可以使用

3. **權限最小化：**
- 只啟用必要的權限（TRADE, USER_DATA）
- 不啟用提現權限（除非必要）

4. **定期輪換：**
- 每 90 天更換一次 API Key
- 設定日曆提醒

---

## 🔐 從 Testnet 切換到正式環境

**當你準備好進入正式交易時：**

1. **生成正式 API Key：**
   - 前往 [Binance API 管理](https://www.binance.com/zh-TC/my/settings/api-management)
   - 完成身份驗證（KYC）
   - 啟用 Google 2FA
   - 設定 IP 白名單

2. **更新 `.env`：**
```bash
BINANCE_API_KEY=<正式 API Key>
BINANCE_API_SECRET=<正式 Secret Key>
BINANCE_TESTNET=false  # ⬅️ 改為 false
```

3. **小額測試：**
```bash
# 先用最小資金測試（例如 $10）
# 觀察 24 小時無異常後再增加資金
```

4. **啟用監控：**
```bash
# 設定 Telegram 告警
TELEGRAM_BOT_TOKEN=<你的 Bot Token>
TELEGRAM_CHAT_ID=<你的 Chat ID>

# 啟動監控
docker-compose up -d grafana prometheus
```

---

## 📞 需要幫助？

如果遇到問題：
1. 查看日誌：`cat logs/trading.log`
2. 運行診斷：`python scripts/check_security.py`
3. 檢查文檔：`docs/DEVELOPMENT_PLAN.md`

---

**最後更新：** 2025-01-10  
**版本：** 1.0
