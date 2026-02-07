"""
錯單放大鏡 - Loss Pattern Analyzer
分析每筆虧損交易的特徵，找出導致虧損的模式
幫助優化止損策略和進場條件

作者: Phase 0 優化項目
日期: 2025-11-14
"""

import json
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict, Counter


@dataclass
class LossTrade:
    """虧損交易記錄"""
    trade_id: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    position_size: float
    leverage: int
    direction: str  # LONG/SHORT
    loss_amount: float
    loss_percent: float
    holding_time_seconds: int
    
    # 進場特徵
    rsi_at_entry: Optional[float] = None
    spread_at_entry: Optional[float] = None
    volume_at_entry: Optional[float] = None
    volatility_at_entry: Optional[float] = None
    obi_at_entry: Optional[float] = None
    vpin_at_entry: Optional[float] = None
    
    # 出場特徵
    exit_reason: str = "UNKNOWN"  # SL_HIT/TP_HIT/MANUAL/TIMEOUT
    sl_percent: Optional[float] = None
    tp_percent: Optional[float] = None
    
    # 元數據
    strategy: str = "unknown"
    metadata: Optional[Dict] = None
    
    def to_dict(self) -> Dict:
        """轉換為字典（序列化友好）"""
        d = asdict(self)
        d['entry_time'] = self.entry_time.isoformat()
        d['exit_time'] = self.exit_time.isoformat()
        return d
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'LossTrade':
        """從字典創建對象"""
        data['entry_time'] = datetime.fromisoformat(data['entry_time'])
        data['exit_time'] = datetime.fromisoformat(data['exit_time'])
        return cls(**data)


@dataclass
class LossPattern:
    """虧損模式"""
    pattern_name: str
    description: str
    occurrence_count: int
    total_loss: float
    avg_loss: float
    trades: List[str]  # trade_id 列表
    confidence: float  # 信心度 (0-1)
    recommendation: str  # 優化建議


class LossPatternAnalyzer:
    """
    錯單放大鏡
    
    功能：
    1. 記錄所有虧損交易的詳細特徵
    2. 分析虧損模式（如「盤整期虧損」「快速止損」等）
    3. 提供優化建議
    4. 生成虧損報告
    """
    
    def __init__(
        self,
        data_file: str = "data/loss_trades.json",
        min_pattern_count: int = 3  # 至少3筆交易才認定為模式
    ):
        """
        初始化錯單放大鏡
        
        Args:
            data_file: 數據存儲文件
            min_pattern_count: 最少模式識別次數
        """
        self.data_file = Path(data_file)
        self.min_pattern_count = min_pattern_count
        
        # 虧損交易記錄
        self.loss_trades: Dict[str, LossTrade] = {}
        
        # 載入歷史數據
        self.load_data()
    
    def load_data(self):
        """載入歷史虧損數據"""
        if not self.data_file.exists():
            print(f"虧損數據文件不存在，創建新文件: {self.data_file}")
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            self.save_data()
            return
        
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.loss_trades = {}
            for trade_id, trade_data in data.get('loss_trades', {}).items():
                self.loss_trades[trade_id] = LossTrade.from_dict(trade_data)
            
            print(f"載入虧損記錄: {len(self.loss_trades)} 筆")
        
        except Exception as e:
            print(f"載入虧損數據失敗: {e}")
            self.loss_trades = {}
    
    def save_data(self):
        """保存數據到文件"""
        try:
            data = {
                'loss_trades': {
                    trade_id: trade.to_dict() 
                    for trade_id, trade in self.loss_trades.items()
                },
                'last_updated': datetime.now().isoformat(),
                'total_losses': len(self.loss_trades)
            }
            
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        
        except Exception as e:
            print(f"保存虧損數據失敗: {e}")
    
    def record_loss(self, trade: LossTrade):
        """
        記錄一筆虧損交易
        
        Args:
            trade: LossTrade 對象
        """
        self.loss_trades[trade.trade_id] = trade
        
        # 定期保存（每10筆）
        if len(self.loss_trades) % 10 == 0:
            self.save_data()
    
    def analyze_patterns(self) -> List[LossPattern]:
        """
        分析虧損模式
        
        Returns:
            識別出的虧損模式列表
        """
        if len(self.loss_trades) < self.min_pattern_count:
            return []
        
        patterns = []
        trades = list(self.loss_trades.values())
        
        # 模式 1：快速止損（持倉時間 < 5分鐘）
        fast_sl_trades = [
            t for t in trades 
            if t.holding_time_seconds < 300 and t.exit_reason == "SL_HIT"
        ]
        if len(fast_sl_trades) >= self.min_pattern_count:
            patterns.append(LossPattern(
                pattern_name="快速止損",
                description="進場後短時間內被止損，可能是假突破或進場時機不佳",
                occurrence_count=len(fast_sl_trades),
                total_loss=sum(t.loss_amount for t in fast_sl_trades),
                avg_loss=np.mean([t.loss_amount for t in fast_sl_trades]),
                trades=[t.trade_id for t in fast_sl_trades],
                confidence=min(1.0, len(fast_sl_trades) / 20),
                recommendation="建議：1) 加入確認指標（如突破後回踩） 2) 放寬止損 3) 使用時間篩選器"
            ))
        
        # 模式 2：高波動期虧損（VPIN > 0.5 或 ATR 高）
        high_vol_trades = [
            t for t in trades 
            if t.vpin_at_entry and t.vpin_at_entry > 0.5
        ]
        if len(high_vol_trades) >= self.min_pattern_count:
            patterns.append(LossPattern(
                pattern_name="高波動期虧損",
                description="在市場波動劇烈時進場，容易被掃止損",
                occurrence_count=len(high_vol_trades),
                total_loss=sum(t.loss_amount for t in high_vol_trades),
                avg_loss=np.mean([t.loss_amount for t in high_vol_trades]),
                trades=[t.trade_id for t in high_vol_trades],
                confidence=min(1.0, len(high_vol_trades) / 15),
                recommendation="建議：1) 啟用盤整偵測器 2) VPIN > 0.5 時禁止交易 3) 使用動態止損"
            ))
        
        # 模式 3：寬價差期虧損（Spread > 0.1%）
        wide_spread_trades = [
            t for t in trades 
            if t.spread_at_entry and t.spread_at_entry > 0.001
        ]
        if len(wide_spread_trades) >= self.min_pattern_count:
            patterns.append(LossPattern(
                pattern_name="寬價差期虧損",
                description="價差過寬時進場，滑點成本高",
                occurrence_count=len(wide_spread_trades),
                total_loss=sum(t.loss_amount for t in wide_spread_trades),
                avg_loss=np.mean([t.loss_amount for t in wide_spread_trades]),
                trades=[t.trade_id for t in wide_spread_trades],
                confidence=min(1.0, len(wide_spread_trades) / 10),
                recommendation="建議：1) Spread > 0.1% 時禁止交易 2) 使用限價單"
            ))
        
        # 模式 4：極端 RSI 進場虧損（RSI < 20 或 > 80）
        extreme_rsi_trades = [
            t for t in trades 
            if t.rsi_at_entry and (t.rsi_at_entry < 20 or t.rsi_at_entry > 80)
        ]
        if len(extreme_rsi_trades) >= self.min_pattern_count:
            patterns.append(LossPattern(
                pattern_name="極端RSI進場虧損",
                description="在 RSI 極端值進場（抄底/摸頂），但趨勢繼續",
                occurrence_count=len(extreme_rsi_trades),
                total_loss=sum(t.loss_amount for t in extreme_rsi_trades),
                avg_loss=np.mean([t.loss_amount for t in extreme_rsi_trades]),
                trades=[t.trade_id for t in extreme_rsi_trades],
                confidence=min(1.0, len(extreme_rsi_trades) / 15),
                recommendation="建議：1) 等待 RSI 背離確認 2) 結合趨勢指標（MA）3) 避免單純抄底"
            ))
        
        # 模式 5：長時間持倉虧損（> 1小時仍被止損）
        long_hold_trades = [
            t for t in trades 
            if t.holding_time_seconds > 3600 and t.exit_reason == "SL_HIT"
        ]
        if len(long_hold_trades) >= self.min_pattern_count:
            patterns.append(LossPattern(
                pattern_name="長時間持倉虧損",
                description="持倉超過1小時仍被止損，可能方向判斷錯誤",
                occurrence_count=len(long_hold_trades),
                total_loss=sum(t.loss_amount for t in long_hold_trades),
                avg_loss=np.mean([t.loss_amount for t in long_hold_trades]),
                trades=[t.trade_id for t in long_hold_trades],
                confidence=min(1.0, len(long_hold_trades) / 10),
                recommendation="建議：1) 使用時間止損（如30分鐘未盈利則出場）2) 使用追蹤止損"
            ))
        
        # 模式 6：特定時段虧損（例如凌晨2-6點）
        night_trades = [
            t for t in trades 
            if 2 <= t.entry_time.hour <= 6
        ]
        if len(night_trades) >= self.min_pattern_count:
            patterns.append(LossPattern(
                pattern_name="深夜時段虧損",
                description="在凌晨2-6點交易，流動性差",
                occurrence_count=len(night_trades),
                total_loss=sum(t.loss_amount for t in night_trades),
                avg_loss=np.mean([t.loss_amount for t in night_trades]),
                trades=[t.trade_id for t in night_trades],
                confidence=min(1.0, len(night_trades) / 10),
                recommendation="建議：1) 啟用時間區間分析器 2) 凌晨2-6點禁止交易"
            ))
        
        # 模式 7：高槓桿虧損（槓桿 >= 10x）
        high_leverage_trades = [
            t for t in trades 
            if t.leverage >= 10
        ]
        if len(high_leverage_trades) >= self.min_pattern_count:
            patterns.append(LossPattern(
                pattern_name="高槓桿虧損",
                description="使用高槓桿（≥10x），小波動即爆倉",
                occurrence_count=len(high_leverage_trades),
                total_loss=sum(t.loss_amount for t in high_leverage_trades),
                avg_loss=np.mean([t.loss_amount for t in high_leverage_trades]),
                trades=[t.trade_id for t in high_leverage_trades],
                confidence=min(1.0, len(high_leverage_trades) / 8),
                recommendation="建議：1) 降低槓桿至 3-5x 2) 使用動態槓桿（根據波動率調整）"
            ))
        
        # 按虧損總額排序
        patterns.sort(key=lambda p: p.total_loss, reverse=True)
        
        return patterns
    
    def get_worst_patterns(self, top_n: int = 3) -> List[LossPattern]:
        """
        獲取最嚴重的虧損模式
        
        Args:
            top_n: 返回前 N 個模式
            
        Returns:
            按虧損金額排序的模式列表
        """
        patterns = self.analyze_patterns()
        return patterns[:top_n]
    
    def get_summary_report(self) -> Dict:
        """
        生成虧損摘要報告
        
        Returns:
            包含統計信息的字典
        """
        if not self.loss_trades:
            return {'message': '尚無虧損記錄'}
        
        trades = list(self.loss_trades.values())
        patterns = self.analyze_patterns()
        
        # 計算統計
        total_loss = sum(t.loss_amount for t in trades)
        avg_loss = np.mean([t.loss_amount for t in trades])
        median_loss = np.median([t.loss_amount for t in trades])
        max_loss = max(t.loss_amount for t in trades)
        
        # 出場原因統計
        exit_reasons = Counter(t.exit_reason for t in trades)
        
        # 策略統計
        strategy_stats = defaultdict(lambda: {'count': 0, 'total_loss': 0})
        for t in trades:
            strategy_stats[t.strategy]['count'] += 1
            strategy_stats[t.strategy]['total_loss'] += t.loss_amount
        
        return {
            'total_losses': len(trades),
            'total_loss_amount': total_loss,
            'avg_loss': avg_loss,
            'median_loss': median_loss,
            'max_single_loss': max_loss,
            'patterns_identified': len(patterns),
            'top_patterns': [p.pattern_name for p in patterns[:3]],
            'exit_reasons': dict(exit_reasons),
            'worst_strategy': max(
                strategy_stats.items(), 
                key=lambda x: x[1]['total_loss']
            )[0] if strategy_stats else None,
            'avg_holding_time_minutes': np.mean([
                t.holding_time_seconds / 60 for t in trades
            ])
        }
    
    def generate_detailed_report(self) -> str:
        """
        生成詳細的錯單分析報告
        
        Returns:
            格式化的報告字符串
        """
        patterns = self.analyze_patterns()
        summary = self.get_summary_report()
        
        report = "=" * 60 + "\n"
        report += "錯單放大鏡 - 虧損模式分析報告\n"
        report += "=" * 60 + "\n\n"
        
        report += "📊 虧損統計摘要\n"
        report += f"  總虧損筆數: {summary['total_losses']}\n"
        report += f"  總虧損金額: ${summary['total_loss_amount']:.2f}\n"
        report += f"  平均虧損: ${summary['avg_loss']:.2f}\n"
        report += f"  最大單筆虧損: ${summary['max_single_loss']:.2f}\n"
        report += f"  平均持倉時間: {summary['avg_holding_time_minutes']:.1f} 分鐘\n\n"
        
        if patterns:
            report += f"🔍 識別出 {len(patterns)} 個虧損模式\n\n"
            
            for i, pattern in enumerate(patterns, 1):
                report += f"模式 {i}: {pattern.pattern_name}\n"
                report += f"  描述: {pattern.description}\n"
                report += f"  出現次數: {pattern.occurrence_count}\n"
                report += f"  累計虧損: ${pattern.total_loss:.2f}\n"
                report += f"  平均虧損: ${pattern.avg_loss:.2f}\n"
                report += f"  信心度: {pattern.confidence:.0%}\n"
                report += f"  {pattern.recommendation}\n\n"
        else:
            report += "⚪ 數據不足，暫無顯著虧損模式\n\n"
        
        report += "=" * 60 + "\n"
        
        return report


# ==================== 使用範例 ====================
if __name__ == "__main__":
    # 創建分析器
    analyzer = LossPatternAnalyzer(
        data_file="data/test_loss_trades.json",
        min_pattern_count=3
    )
    
    # 模擬記錄虧損交易
    print("=== 模擬虧損交易記錄 ===\n")
    
    np.random.seed(42)
    
    for i in range(30):
        entry_time = datetime.now() - timedelta(hours=np.random.randint(1, 720))
        exit_time = entry_time + timedelta(seconds=np.random.randint(60, 7200))
        
        trade = LossTrade(
            trade_id=f"LOSS_{i:04d}",
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=50000 + np.random.randn() * 1000,
            exit_price=49500 + np.random.randn() * 1000,
            position_size=0.1 + np.random.random() * 0.5,
            leverage=np.random.choice([3, 5, 10, 20]),
            direction=np.random.choice(["LONG", "SHORT"]),
            loss_amount=np.random.uniform(5, 100),
            loss_percent=np.random.uniform(0.005, 0.02),
            holding_time_seconds=int((exit_time - entry_time).total_seconds()),
            rsi_at_entry=np.random.uniform(15, 85),
            spread_at_entry=np.random.uniform(0.0001, 0.002),
            vpin_at_entry=np.random.uniform(0.1, 0.8),
            exit_reason=np.random.choice(["SL_HIT", "TIMEOUT", "MANUAL"]),
            sl_percent=0.01,
            tp_percent=0.02,
            strategy="test_strategy"
        )
        
        analyzer.record_loss(trade)
    
    # 生成報告
    print(analyzer.generate_detailed_report())
    
    # 最嚴重模式
    print("=== 前3大虧損模式 ===")
    for pattern in analyzer.get_worst_patterns(3):
        print(f"\n{pattern.pattern_name}: ${pattern.total_loss:.2f}")
        print(f"  {pattern.recommendation}")
