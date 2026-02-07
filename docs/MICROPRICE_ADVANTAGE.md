# 🎯 Microprice (微觀價格壓力) 核心優勢說明

**最後更新**：2025年11月11日  
**模組狀態**：✅ 已完成（Task 1.6）  
**代碼位置**：`src/strategy/signal_generator.py`

---

## 🎯 核心優勢總覽

### **Microprice 是本系統的精準定價工具 ★★★**

| 優勢維度 | 評分 | 說明 |
|---------|------|------|
| **精準性** | ⭐⭐⭐⭐⭐ | 比 Mid Price 更準確 |
| **學術性** | ⭐⭐⭐⭐ | 學術級公式 |
| **領先性** | ⭐⭐⭐⭐ | 領先成交價格 |

---

## 一、什麼是 Microprice？

### 📖 定義

```python
Microprice = (Bid × AskSize + Ask × BidSize) / (BidSize + AskSize)

# vs Mid Price
Mid Price = (Bid + Ask) / 2

# 差異
Microprice 考慮了訂單簿深度權重 ✅
```

### 🎨 視覺化

```plaintext
訂單簿：
Ask: $106,500 (3 BTC)
Bid: $106,400 (30 BTC)

Mid Price = (106500 + 106400) / 2 = $106,450

Microprice = (106400×3 + 106500×30) / 33
           = $106,491 (更接近賣方)

解讀：買方深度遠大於賣方，
      實際成交價會偏向 Ask
```

---

## 二、核心優勢：更準確的價格預測

### 📊 Microprice Pressure

```python
Pressure = (Microprice - Mid Price) / Mid Price

> 0.001  → 買方壓力 (價格可能上漲) ✅
< -0.001 → 賣方壓力 (價格可能下跌) ❌
```

---

## 三、在策略系統中的定位

### 🎯 信號生成（10% 權重）

```python
# 在 SignalGenerator 中
if microprice_pressure > 0:
    long_score += 0.1 * microprice_weight  # 買方壓力
else:
    short_score += 0.1 * abs(microprice_pressure)
```

---

## 四、總結

### ✅ 核心價值

- ✅ 比 Mid Price 更準確
- ✅ 考慮訂單簿深度
- ✅ 輔助信號生成

---

**最後更新**：2025年11月11日  
**版本**：v1.0  
**狀態**：✅ 生產就緒
