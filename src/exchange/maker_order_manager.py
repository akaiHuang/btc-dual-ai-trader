"""
🏷️ Maker 訂單管理系統
======================

實現 Maker 優先進場，降低手續費成本

費用對比 (100x 槓桿):
- Taker: 0.05% × 2 × 100 = 10% ROI
- Maker: 0.02% × 2 × 100 = 4% ROI (節省 6%!)

策略:
1. 嘗試用 Maker 掛單進場
2. 設定超時時間 (預設 30 秒)
3. 超時未成交 → 可選擇改用 Taker 或取消

Author: AI Trading System
Created: 2025-11-25
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from datetime import datetime
from enum import Enum
import time


class MakerOrderStatus(Enum):
    """Maker 訂單狀態"""
    PENDING = "PENDING"           # 等待成交
    FILLED = "FILLED"             # 已成交
    PARTIALLY_FILLED = "PARTIAL"  # 部分成交
    CANCELLED = "CANCELLED"       # 已取消
    EXPIRED = "EXPIRED"           # 超時
    TAKER_FALLBACK = "TAKER"      # 改用 Taker 成交


@dataclass
class MakerOrder:
    """Maker 掛單"""
    order_id: str
    direction: str                # "LONG" or "SHORT"
    limit_price: float            # 掛單價格
    quantity_usdt: float          # 倉位金額
    leverage: int
    
    # 狀態追蹤
    status: MakerOrderStatus = MakerOrderStatus.PENDING
    created_at: float = field(default_factory=time.time)
    filled_at: Optional[float] = None
    filled_price: Optional[float] = None
    
    # 超時設定
    timeout_seconds: float = 30.0
    allow_taker_fallback: bool = True
    
    # 額外資訊
    strategy: str = ""
    reason: str = ""
    market_data: Dict = field(default_factory=dict)
    
    def is_expired(self) -> bool:
        """檢查是否超時"""
        return time.time() - self.created_at > self.timeout_seconds
    
    def check_fill(self, current_price: float, best_bid: float, best_ask: float) -> bool:
        """
        檢查是否成交
        
        Maker 成交條件:
        - LONG: 市價下跌到我們的買入掛單價格
        - SHORT: 市價上漲到我們的賣出掛單價格
        """
        if self.status != MakerOrderStatus.PENDING:
            return self.status == MakerOrderStatus.FILLED
        
        is_filled = False
        
        if self.direction == "LONG":
            # 做多掛單：掛在買一附近，等價格下來成交
            # 當市場最佳賣價 <= 我們的限價，訂單成交
            if best_ask <= self.limit_price:
                is_filled = True
                self.filled_price = self.limit_price  # Maker 成交在掛單價
        else:  # SHORT
            # 做空掛單：掛在賣一附近，等價格上來成交
            # 當市場最佳買價 >= 我們的限價，訂單成交
            if best_bid >= self.limit_price:
                is_filled = True
                self.filled_price = self.limit_price
        
        if is_filled:
            self.status = MakerOrderStatus.FILLED
            self.filled_at = time.time()
            return True
        
        return False
    
    def get_fill_wait_time(self) -> float:
        """獲取等待成交時間"""
        return time.time() - self.created_at
    
    def to_dict(self) -> Dict:
        return {
            "order_id": self.order_id,
            "direction": self.direction,
            "limit_price": self.limit_price,
            "quantity_usdt": self.quantity_usdt,
            "leverage": self.leverage,
            "status": self.status.value,
            "created_at": self.created_at,
            "filled_at": self.filled_at,
            "filled_price": self.filled_price,
            "wait_time": self.get_fill_wait_time(),
            "strategy": self.strategy,
            "reason": self.reason
        }


class MakerOrderManager:
    """
    Maker 訂單管理器
    
    管理所有待成交的 Maker 掛單
    """
    
    def __init__(
        self,
        default_timeout: float = 30.0,
        default_taker_fallback: bool = True,
        maker_offset_bps: float = 1.0,  # 掛單偏移 (基點)
        aggressive_offset_bps: float = 0.0  # 激進模式：直接掛在最佳價
    ):
        """
        Args:
            default_timeout: 預設超時秒數
            default_taker_fallback: 是否允許 Taker 補單
            maker_offset_bps: 掛單價格偏移 (相對於最佳價的基點數)
                             - 正數: 更保守 (更容易成交但可能滑點)
                             - 負數: 更激進 (可能搶到更好價格但成交率低)
        """
        self.default_timeout = default_timeout
        self.default_taker_fallback = default_taker_fallback
        self.maker_offset_bps = maker_offset_bps
        self.aggressive_offset_bps = aggressive_offset_bps
        
        # 待處理的 Maker 訂單
        self.pending_orders: Dict[str, MakerOrder] = {}
        
        # 統計
        self.stats = {
            "total_orders": 0,
            "filled_as_maker": 0,
            "taker_fallback": 0,
            "cancelled": 0,
            "expired": 0,
            "avg_fill_time": 0.0,
            "maker_rate": 0.0,  # Maker 成交率
            "total_fee_saved": 0.0  # 節省的手續費估算
        }
        
        self.fill_times: List[float] = []
    
    def calculate_maker_price(
        self,
        direction: str,
        current_price: float,
        best_bid: float,
        best_ask: float,
        aggressive: bool = False
    ) -> float:
        """
        計算 Maker 掛單價格
        
        Args:
            direction: "LONG" or "SHORT"
            current_price: 當前市價
            best_bid: 最佳買價
            best_ask: 最佳賣價
            aggressive: 是否使用激進模式
        
        Returns:
            掛單價格
        """
        offset_bps = self.aggressive_offset_bps if aggressive else self.maker_offset_bps
        offset_pct = offset_bps / 10000  # 轉換為百分比
        
        if direction == "LONG":
            # 做多：掛買單
            # 保守模式：掛在 best_bid 稍微高一點（更容易成交）
            # 激進模式：直接掛在 best_bid（等價格下來）
            base_price = best_bid
            maker_price = base_price * (1 + offset_pct)
        else:  # SHORT
            # 做空：掛賣單
            # 保守模式：掛在 best_ask 稍微低一點
            # 激進模式：直接掛在 best_ask
            base_price = best_ask
            maker_price = base_price * (1 - offset_pct)
        
        return maker_price
    
    def create_maker_order(
        self,
        direction: str,
        current_price: float,
        best_bid: float,
        best_ask: float,
        quantity_usdt: float,
        leverage: int,
        strategy: str = "",
        reason: str = "",
        market_data: Dict = None,
        timeout: float = None,
        allow_taker_fallback: bool = None,
        aggressive: bool = False,
        custom_limit_price: float = None  # 允許自訂掛單價 (如 whale_reversal_price)
    ) -> MakerOrder:
        """
        創建 Maker 掛單
        
        Args:
            custom_limit_price: 自訂限價 (如果提供，覆蓋自動計算)
        """
        order_id = f"MK_{strategy}_{int(time.time()*1000)}"
        
        # 決定掛單價格
        if custom_limit_price and custom_limit_price > 0:
            limit_price = custom_limit_price
        else:
            limit_price = self.calculate_maker_price(
                direction, current_price, best_bid, best_ask, aggressive
            )
        
        order = MakerOrder(
            order_id=order_id,
            direction=direction,
            limit_price=limit_price,
            quantity_usdt=quantity_usdt,
            leverage=leverage,
            timeout_seconds=timeout or self.default_timeout,
            allow_taker_fallback=allow_taker_fallback if allow_taker_fallback is not None else self.default_taker_fallback,
            strategy=strategy,
            reason=reason,
            market_data=market_data or {}
        )
        
        self.pending_orders[order_id] = order
        self.stats["total_orders"] += 1
        
        return order
    
    def update_orders(
        self,
        current_price: float,
        best_bid: float,
        best_ask: float
    ) -> List[Tuple[MakerOrder, str]]:
        """
        更新所有待處理訂單
        
        Returns:
            List of (order, action) where action is:
            - "FILLED": Maker 成交
            - "TAKER_FALLBACK": 超時改用 Taker
            - "CANCELLED": 超時且不允許 Taker
            - None: 繼續等待
        """
        results = []
        orders_to_remove = []
        
        for order_id, order in self.pending_orders.items():
            if order.status != MakerOrderStatus.PENDING:
                continue
            
            # 檢查是否成交
            if order.check_fill(current_price, best_bid, best_ask):
                fill_time = order.get_fill_wait_time()
                self.fill_times.append(fill_time)
                self.stats["filled_as_maker"] += 1
                
                # 計算節省的費用 (相對於 Taker)
                # Taker: 0.05%, Maker: 0.02%, 差額: 0.03%
                fee_saved = order.quantity_usdt * order.leverage * 0.0003 * 2  # 進+出
                self.stats["total_fee_saved"] += fee_saved
                
                results.append((order, "FILLED"))
                orders_to_remove.append(order_id)
                continue
            
            # 檢查是否超時
            if order.is_expired():
                if order.allow_taker_fallback:
                    order.status = MakerOrderStatus.TAKER_FALLBACK
                    order.filled_price = current_price * (1.0002 if order.direction == "LONG" else 0.9998)
                    order.filled_at = time.time()
                    self.stats["taker_fallback"] += 1
                    results.append((order, "TAKER_FALLBACK"))
                else:
                    order.status = MakerOrderStatus.CANCELLED
                    self.stats["cancelled"] += 1
                    results.append((order, "CANCELLED"))
                
                orders_to_remove.append(order_id)
        
        # 清理已處理的訂單
        for order_id in orders_to_remove:
            del self.pending_orders[order_id]
        
        # 更新統計
        self._update_stats()
        
        return results
    
    def cancel_order(self, order_id: str) -> bool:
        """取消訂單"""
        if order_id in self.pending_orders:
            self.pending_orders[order_id].status = MakerOrderStatus.CANCELLED
            self.stats["cancelled"] += 1
            del self.pending_orders[order_id]
            return True
        return False
    
    def get_pending_count(self) -> int:
        """獲取待處理訂單數量"""
        return len(self.pending_orders)
    
    def _update_stats(self):
        """更新統計數據"""
        if self.fill_times:
            self.stats["avg_fill_time"] = sum(self.fill_times) / len(self.fill_times)
        
        total_completed = self.stats["filled_as_maker"] + self.stats["taker_fallback"]
        if total_completed > 0:
            self.stats["maker_rate"] = self.stats["filled_as_maker"] / total_completed
    
    def get_stats_summary(self) -> str:
        """獲取統計摘要"""
        return (
            f"📊 Maker 統計:\n"
            f"   總訂單: {self.stats['total_orders']}\n"
            f"   Maker成交: {self.stats['filled_as_maker']} ({self.stats['maker_rate']*100:.1f}%)\n"
            f"   Taker補單: {self.stats['taker_fallback']}\n"
            f"   取消: {self.stats['cancelled']}\n"
            f"   平均等待: {self.stats['avg_fill_time']:.1f}s\n"
            f"   💰 節省手續費: ${self.stats['total_fee_saved']:.2f}"
        )


# ==================== 便捷函數 ====================

def calculate_fee_impact(leverage: int, is_maker: bool = False) -> Dict:
    """
    計算手續費對 ROI 的影響
    
    Args:
        leverage: 槓桿倍數
        is_maker: 是否為 Maker
    
    Returns:
        {
            "fee_rate": 單邊費率,
            "round_trip_fee_pct": 來回手續費百分比,
            "roi_cost": 對 ROI 的影響,
            "breakeven_move": 打平所需的價格移動
        }
    """
    fee_rate = 0.0002 if is_maker else 0.0005  # Maker: 0.02%, Taker: 0.05%
    
    round_trip_fee_pct = fee_rate * 2 * 100  # 轉為百分比
    roi_cost = round_trip_fee_pct * leverage  # 對 ROI 的影響
    breakeven_move = round_trip_fee_pct  # 打平所需價格移動
    
    return {
        "fee_rate": fee_rate,
        "round_trip_fee_pct": round_trip_fee_pct,
        "roi_cost": roi_cost,
        "breakeven_move": breakeven_move,
        "description": f"{'Maker' if is_maker else 'Taker'} @ {leverage}x: {roi_cost:.1f}% ROI cost"
    }


def should_use_maker(
    urgency: str,
    confidence: float,
    is_breakout: bool = False
) -> Tuple[bool, float, str]:
    """
    決定是否使用 Maker
    
    Args:
        urgency: "LOW", "MEDIUM", "HIGH", "CRITICAL"
        confidence: 信心度 0-1
        is_breakout: 是否為突破信號
    
    Returns:
        (use_maker, timeout_seconds, reason)
    """
    if urgency == "CRITICAL" or is_breakout:
        # 緊急或突破：用 Taker 確保成交
        return False, 0, "Taker: 緊急/突破信號需立即成交"
    
    if urgency == "HIGH":
        # 高緊急度：短超時的 Maker
        return True, 10.0, "Maker: 高緊急度，10秒超時"
    
    if confidence > 0.8:
        # 高信心：可以等久一點
        return True, 45.0, "Maker: 高信心度，45秒超時"
    
    if urgency == "MEDIUM":
        return True, 30.0, "Maker: 中等緊急度，30秒超時"
    
    # 低緊急度：最長等待
    return True, 60.0, "Maker: 低緊急度，60秒超時"


# ==================== 測試 ====================

if __name__ == "__main__":
    print("🏷️ Maker 訂單管理系統測試")
    print("=" * 60)
    
    # 創建管理器
    manager = MakerOrderManager(
        default_timeout=30.0,
        maker_offset_bps=1.0
    )
    
    # 測試費用計算
    print("\n📊 費用對比 (100x 槓桿):")
    taker_fee = calculate_fee_impact(100, is_maker=False)
    maker_fee = calculate_fee_impact(100, is_maker=True)
    print(f"   Taker: {taker_fee['roi_cost']:.1f}% ROI 成本")
    print(f"   Maker: {maker_fee['roi_cost']:.1f}% ROI 成本")
    print(f"   💰 節省: {taker_fee['roi_cost'] - maker_fee['roi_cost']:.1f}% ROI")
    
    # 模擬創建訂單
    print("\n📝 創建 Maker 訂單:")
    order = manager.create_maker_order(
        direction="LONG",
        current_price=87000,
        best_bid=86998,
        best_ask=87002,
        quantity_usdt=27.5,
        leverage=100,
        strategy="M_AI_WHALE_HUNTER",
        reason="Test order"
    )
    print(f"   訂單ID: {order.order_id}")
    print(f"   方向: {order.direction}")
    print(f"   掛單價: ${order.limit_price:.2f}")
    print(f"   當前價: $87000")
    print(f"   狀態: {order.status.value}")
    
    # 模擬價格變動導致成交
    print("\n🔄 模擬價格下跌到掛單價...")
    results = manager.update_orders(
        current_price=86995,
        best_bid=86993,
        best_ask=86997  # best_ask 跌到我們的買入價以下
    )
    
    for order, action in results:
        print(f"   結果: {action}")
        print(f"   成交價: ${order.filled_price:.2f}")
        print(f"   等待時間: {order.get_fill_wait_time():.1f}s")
    
    print("\n" + manager.get_stats_summary())
