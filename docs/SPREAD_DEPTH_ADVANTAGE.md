# 💹 Spread & Depth (價差與深度) 核心優勢說明

**最後更新**：2025年11月11日  
**模組狀態**：✅ 已完成（Task 1.6）  
**代碼位置**：`src/exchange/spread_depth_monitor.py`

---

## 🎯 核心優勢總覽

### **Spread & Depth 是本系統的流動性監控器 ★★★★**

| 優勢維度 | 評分 | 說明 |
|---------|------|------|
| **風控性** | ⭐⭐⭐⭐⭐ | 檢測流動性風險 |
| **成本性** | ⭐⭐⭐⭐⭐ | 評估交易成本 |
| **實時性** | ⭐⭐⭐⭐⭐ | 即時監控訂單簿 |
| **實用性** | ⭐⭐⭐⭐ | 避免高成本交易 |

---

## 一、什麼是 Spread & Depth？

### 📖 Spread (買賣價差)

```python
Spread = Ask Price - Bid Price
Spread (bps) = (Spread / Mid Price) × 10000

# 解讀
< 5 bps   → 流動性極佳 ✅
5-10 bps  → 流動性良好 ✅
10-20 bps → 流動性一般 ⚠️
> 20 bps  → 流動性差 ❌
```

### 📊 Depth (市場深度)

```python
Depth = Σ(前5檔買單量 + 前5檔賣單量)

# 解讀
> 20 BTC  → 深度充足 ✅
10-20 BTC → 深度正常 ✅
5-10 BTC  → 深度偏淺 ⚠️
< 5 BTC   → 深度不足 ❌
```

---

## 二、核心優勢：流動性風控

### 🚨 避免滑點與高成本

```plaintext
案例：相同信號，不同流動性

場景 A（流動性好）：
- Spread: 2 bps
- Depth: 30 BTC
→ 成本: 0.02%，滑點 < 0.01% ✅

場景 B（流動性差）：
- Spread: 25 bps
- Depth: 3 BTC
→ 成本: 0.25%，滑點 > 0.1% ❌

差異：10倍成本！
```

---

## 三、在策略系統中的定位

### 🎯 風控層（Layer 2: Regime Filter）

```python
def check_liquidity(spread_bps, depth):
    if spread_bps > 20 or depth < 5:
        return "BLOCK_TRADE"  # 流動性不足，阻擋交易
    
    elif spread_bps > 10 or depth < 10:
        return "REDUCE_SIZE"  # 降低倉位
    
    else:
        return "SAFE"  # 流動性充足
```

---

## 四、實戰應用

### 📊 應用：動態倉位調整

```python
if depth > 20:
    max_position = 0.8  # 流動性好，可用大倉位
elif depth > 10:
    max_position = 0.5  # 正常倉位
else:
    max_position = 0.2  # 流動性差，小倉位
```

---

## 五、總結

### ✅ 核心價值

- ✅ 避免高成本交易
- ✅ 檢測流動性風險
- ✅ 動態調整倉位
- ✅ 保護交易執行質量

---

**最後更新**：2025年11月11日  
**版本**：v1.0  
**狀態**：✅ 生產就緒
