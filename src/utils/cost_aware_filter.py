"""
成本感知過濾器 - Cost-Aware Filter
實時監控手續費佔比，過濾掉成本過高的交易信號
避免「賺蠅頭小利，虧手續費」的情況

作者: Phase 0 優化項目
日期: 2025-11-14
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class CostDecision(Enum):
    """成本判斷結果"""
    APPROVE = "APPROVE"  # 批准交易
    REJECT = "REJECT"  # 拒絕交易
    WARNING = "WARNING"  # 警告（可交易但風險高）


@dataclass
class CostAnalysis:
    """成本分析結果"""
    decision: CostDecision
    fee_to_profit_ratio: float  # 手續費/預期利潤比率
    estimated_fee: float  # 預估手續費（USD）
    estimated_profit: float  # 預期利潤（USD）
    min_profit_required: float  # 最小所需利潤（USD）
    reason: str  # 判斷原因
    timestamp: datetime


class CostAwareFilter:
    """
    成本感知過濾器
    
    核心邏輯：
    1. 計算交易手續費（開倉 + 平倉）
    2. 計算預期利潤（TP 目標）
    3. 計算費用比率 = 手續費 / 預期利潤
    4. 如果比率 > 閾值，拒絕交易
    
    範例：
    - 預期利潤 $10，手續費 $6 → 比率 60% → 拒絕
    - 預期利潤 $100，手續費 $6 → 比率 6% → 批准
    """
    
    def __init__(
        self,
        maker_fee_rate: float = 0.0002,  # Maker 手續費率 0.02%
        taker_fee_rate: float = 0.0004,  # Taker 手續費率 0.04%
        max_fee_ratio: float = 0.30,  # 最大允許費率 30%
        warning_fee_ratio: float = 0.20,  # 警告費率 20%
        min_profit_usd: float = 5.0,  # 最小利潤要求（USD）
    ):
        """
        初始化成本感知過濾器
        
        Args:
            maker_fee_rate: Maker 手續費率
            taker_fee_rate: Taker 手續費率
            max_fee_ratio: 最大允許手續費/利潤比率
            warning_fee_ratio: 警告手續費/利潤比率
            min_profit_usd: 最小利潤要求（美元）
        """
        self.maker_fee_rate = maker_fee_rate
        self.taker_fee_rate = taker_fee_rate
        self.max_fee_ratio = max_fee_ratio
        self.warning_fee_ratio = warning_fee_ratio
        self.min_profit_usd = min_profit_usd
        
        # 統計數據
        self.total_signals = 0
        self.approved_signals = 0
        self.rejected_signals = 0
        self.warning_signals = 0
    
    def calculate_trading_fee(
        self,
        entry_price: float,
        position_size: float,
        leverage: int = 1,
        is_maker: bool = False
    ) -> float:
        """
        計算單次交易手續費（開倉 + 平倉）
        
        Args:
            entry_price: 進場價格
            position_size: 倉位大小（佔總資金比例，如 0.5 = 50%）
            leverage: 槓桿倍數
            is_maker: 是否使用 Maker 單（限價單）
            
        Returns:
            手續費（USD）
        """
        # 計算開倉手續費
        fee_rate = self.maker_fee_rate if is_maker else self.taker_fee_rate
        
        # 名義交易額 = 進場價格 × 倉位大小 × 槓桿
        notional_value = entry_price * position_size * leverage
        
        # 開倉 + 平倉手續費（平倉通常用 Taker）
        entry_fee = notional_value * fee_rate
        exit_fee = notional_value * self.taker_fee_rate
        
        total_fee = entry_fee + exit_fee
        return total_fee
    
    def calculate_expected_profit(
        self,
        entry_price: float,
        take_profit_percent: float,
        position_size: float,
        leverage: int = 1,
        direction: str = "LONG"
    ) -> float:
        """
        計算預期利潤
        
        Args:
            entry_price: 進場價格
            take_profit_percent: 止盈百分比（如 0.005 = 0.5%）
            position_size: 倉位大小
            leverage: 槓桿倍數
            direction: 方向（LONG/SHORT）
            
        Returns:
            預期利潤（USD）
        """
        # 名義交易額
        notional_value = entry_price * position_size * leverage
        
        # 利潤 = 名義交易額 × 止盈百分比
        profit = notional_value * take_profit_percent
        
        return profit
    
    def should_trade(
        self,
        entry_price: float,
        take_profit_percent: float,
        stop_loss_percent: float,
        position_size: float,
        leverage: int = 1,
        direction: str = "LONG",
        is_maker: bool = False,
        account_balance: Optional[float] = None
    ) -> CostAnalysis:
        """
        判斷是否應該執行交易
        
        Args:
            entry_price: 進場價格
            take_profit_percent: 止盈百分比
            stop_loss_percent: 止損百分比
            position_size: 倉位大小
            leverage: 槓桿倍數
            direction: 方向
            is_maker: 是否使用 Maker 單
            account_balance: 帳戶餘額（用於絕對金額計算）
            
        Returns:
            CostAnalysis 對象
        """
        self.total_signals += 1
        
        # 1. 計算手續費
        estimated_fee = self.calculate_trading_fee(
            entry_price, position_size, leverage, is_maker
        )
        
        # 2. 計算預期利潤
        estimated_profit = self.calculate_expected_profit(
            entry_price, take_profit_percent, position_size, leverage, direction
        )
        
        # 3. 計算費用比率
        if estimated_profit <= 0:
            fee_ratio = float('inf')
        else:
            fee_ratio = estimated_fee / estimated_profit
        
        # 4. 計算最小所需利潤（覆蓋手續費 + 最小利潤）
        min_profit_required = estimated_fee / (1 - self.max_fee_ratio)
        
        # 5. 判斷決策
        decision = CostDecision.APPROVE
        reason_parts = []
        
        # 檢查：預期利潤是否低於最小要求
        if account_balance and estimated_profit < self.min_profit_usd:
            decision = CostDecision.REJECT
            reason_parts.append(
                f"預期利潤過低 (${estimated_profit:.2f} < ${self.min_profit_usd})"
            )
        
        # 檢查：費用比率是否過高
        elif fee_ratio > self.max_fee_ratio:
            decision = CostDecision.REJECT
            reason_parts.append(
                f"費用比率過高 ({fee_ratio:.1%} > {self.max_fee_ratio:.1%})"
            )
        
        # 檢查：費用比率是否在警告區間
        elif fee_ratio > self.warning_fee_ratio:
            decision = CostDecision.WARNING
            reason_parts.append(
                f"費用比率偏高 ({fee_ratio:.1%})"
            )
        
        # 默認：批准交易
        else:
            reason_parts.append(
                f"費用比率合理 ({fee_ratio:.1%})"
            )
        
        # 6. 更新統計
        if decision == CostDecision.APPROVE:
            self.approved_signals += 1
        elif decision == CostDecision.REJECT:
            self.rejected_signals += 1
        elif decision == CostDecision.WARNING:
            self.warning_signals += 1
        
        # 7. 構建原因字符串
        reason = " | ".join(reason_parts)
        if decision == CostDecision.REJECT:
            reason = f"❌ {reason}"
        elif decision == CostDecision.WARNING:
            reason = f"⚠️ {reason}"
        else:
            reason = f"✅ {reason}"
        
        return CostAnalysis(
            decision=decision,
            fee_to_profit_ratio=fee_ratio,
            estimated_fee=estimated_fee,
            estimated_profit=estimated_profit,
            min_profit_required=min_profit_required,
            reason=reason,
            timestamp=datetime.now()
        )
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        獲取過濾統計數據
        
        Returns:
            包含統計信息的字典
        """
        if self.total_signals == 0:
            approval_rate = 0
            rejection_rate = 0
            warning_rate = 0
        else:
            approval_rate = self.approved_signals / self.total_signals
            rejection_rate = self.rejected_signals / self.total_signals
            warning_rate = self.warning_signals / self.total_signals
        
        return {
            'total_signals': self.total_signals,
            'approved': self.approved_signals,
            'rejected': self.rejected_signals,
            'warnings': self.warning_signals,
            'approval_rate': approval_rate,
            'rejection_rate': rejection_rate,
            'warning_rate': warning_rate,
            'filters_saved_trades': self.rejected_signals  # 避免的虧損交易
        }
    
    def reset_statistics(self):
        """重置統計數據"""
        self.total_signals = 0
        self.approved_signals = 0
        self.rejected_signals = 0
        self.warning_signals = 0
    
    def estimate_breakeven_tp(
        self,
        entry_price: float,
        position_size: float,
        leverage: int = 1,
        is_maker: bool = False
    ) -> float:
        """
        估算盈虧平衡所需的止盈百分比
        
        Args:
            entry_price: 進場價格
            position_size: 倉位大小
            leverage: 槓桿倍數
            is_maker: 是否使用 Maker 單
            
        Returns:
            盈虧平衡止盈百分比
        """
        # 計算手續費
        fee = self.calculate_trading_fee(entry_price, position_size, leverage, is_maker)
        
        # 名義交易額
        notional_value = entry_price * position_size * leverage
        
        # 盈虧平衡 TP% = 手續費 / 名義交易額
        if notional_value == 0:
            return 0
        
        breakeven_tp = fee / notional_value
        return breakeven_tp


# ==================== 使用範例 ====================
if __name__ == "__main__":
    # 創建過濾器
    filter = CostAwareFilter(
        maker_fee_rate=0.0002,  # 0.02%
        taker_fee_rate=0.0004,  # 0.04%
        max_fee_ratio=0.30,  # 30%
        min_profit_usd=5.0
    )
    
    print("=== 成本感知過濾器測試 ===\n")
    
    # 測試案例 1：小利潤交易（應被拒絕）
    print("案例 1：小利潤交易")
    analysis1 = filter.should_trade(
        entry_price=50000,
        take_profit_percent=0.001,  # 0.1% TP
        stop_loss_percent=0.002,
        position_size=0.1,  # 10% 倉位
        leverage=5,
        account_balance=1000
    )
    print(f"決策: {analysis1.decision.value}")
    print(f"預期利潤: ${analysis1.estimated_profit:.2f}")
    print(f"手續費: ${analysis1.estimated_fee:.2f}")
    print(f"費用比率: {analysis1.fee_to_profit_ratio:.1%}")
    print(f"原因: {analysis1.reason}\n")
    
    # 測試案例 2：合理利潤交易（應批准）
    print("案例 2：合理利潤交易")
    analysis2 = filter.should_trade(
        entry_price=50000,
        take_profit_percent=0.01,  # 1% TP
        stop_loss_percent=0.005,
        position_size=0.5,  # 50% 倉位
        leverage=3,
        account_balance=1000
    )
    print(f"決策: {analysis2.decision.value}")
    print(f"預期利潤: ${analysis2.estimated_profit:.2f}")
    print(f"手續費: ${analysis2.estimated_fee:.2f}")
    print(f"費用比率: {analysis2.fee_to_profit_ratio:.1%}")
    print(f"原因: {analysis2.reason}\n")
    
    # 測試案例 3：高利潤交易（應批准）
    print("案例 3：高利潤交易")
    analysis3 = filter.should_trade(
        entry_price=50000,
        take_profit_percent=0.03,  # 3% TP
        stop_loss_percent=0.01,
        position_size=1.0,  # 100% 倉位
        leverage=2,
        account_balance=10000,
        is_maker=True  # 使用限價單
    )
    print(f"決策: {analysis3.decision.value}")
    print(f"預期利潤: ${analysis3.estimated_profit:.2f}")
    print(f"手續費: ${analysis3.estimated_fee:.2f}")
    print(f"費用比率: {analysis3.fee_to_profit_ratio:.1%}")
    print(f"原因: {analysis3.reason}\n")
    
    # 顯示統計
    stats = filter.get_statistics()
    print("=== 過濾統計 ===")
    print(f"總信號: {stats['total_signals']}")
    print(f"批准: {stats['approved']} ({stats['approval_rate']:.1%})")
    print(f"拒絕: {stats['rejected']} ({stats['rejection_rate']:.1%})")
    print(f"警告: {stats['warnings']} ({stats['warning_rate']:.1%})")
    
    # 盈虧平衡計算
    print("\n=== 盈虧平衡分析 ===")
    breakeven = filter.estimate_breakeven_tp(
        entry_price=50000,
        position_size=0.5,
        leverage=5,
        is_maker=False
    )
    print(f"盈虧平衡止盈: {breakeven:.4%}")
    print(f"建議最小止盈: {breakeven * 2:.4%} (2x 盈虧平衡)")
