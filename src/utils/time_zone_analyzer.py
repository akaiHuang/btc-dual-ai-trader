"""
時間區間分析器 - Time Zone Analyzer
分析不同時段的交易績效，識別高效/低效時段
避開低勝率時段，專注於高勝率時段交易

作者: Phase 0 優化項目
日期: 2025-11-14
"""

import json
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
from datetime import datetime, time
from pathlib import Path
from collections import defaultdict
import numpy as np


@dataclass
class TimeSlotStats:
    """時段統計數據"""
    hour: int  # 小時（0-23）
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_profit: float
    total_loss: float
    win_rate: float
    profit_factor: float  # 總盈利 / 總虧損
    avg_profit: float
    avg_loss: float
    expected_value: float  # 期望值
    is_profitable: bool  # 是否盈利時段


@dataclass
class TradingHoursRecommendation:
    """交易時段建議"""
    should_trade: bool
    current_hour: int
    slot_stats: Optional[TimeSlotStats]
    reason: str
    confidence: float  # 信心度 (0-1)
    timestamp: datetime


class TimeZoneAnalyzer:
    """
    時間區間分析器
    
    功能：
    1. 記錄每小時的交易績效
    2. 分析不同時段的勝率和盈利
    3. 識別高效/低效時段
    4. 提供實時交易建議
    
    數據存儲：
    - 使用 JSON 文件持久化數據
    - 每小時一個統計記錄
    - 支持增量更新
    """
    
    def __init__(
        self,
        data_file: str = "data/time_zone_analysis.json",
        min_trades_required: int = 20,  # 最少交易次數
        min_win_rate: float = 0.55,  # 最低勝率要求
        min_profit_factor: float = 1.2,  # 最低盈虧比要求
    ):
        """
        初始化時間區間分析器
        
        Args:
            data_file: 數據存儲文件路徑
            min_trades_required: 最少交易次數（用於判斷數據可靠性）
            min_win_rate: 最低勝率要求
            min_profit_factor: 最低盈虧比要求
        """
        self.data_file = Path(data_file)
        self.min_trades_required = min_trades_required
        self.min_win_rate = min_win_rate
        self.min_profit_factor = min_profit_factor
        
        # 時段數據（key: hour (0-23), value: 交易記錄列表）
        self.hourly_trades: Dict[int, List[Dict]] = defaultdict(list)
        
        # 時段統計（key: hour, value: TimeSlotStats）
        self.hourly_stats: Dict[int, TimeSlotStats] = {}
        
        # 載入歷史數據
        self.load_data()
    
    def load_data(self):
        """從文件載入歷史數據"""
        if not self.data_file.exists():
            print(f"數據文件不存在，創建新文件: {self.data_file}")
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            self.save_data()
            return
        
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 載入交易記錄
            self.hourly_trades = defaultdict(list)
            for hour_str, trades in data.get('hourly_trades', {}).items():
                self.hourly_trades[int(hour_str)] = trades
            
            # 重新計算統計
            self.recalculate_stats()
            
            print(f"載入數據: {sum(len(trades) for trades in self.hourly_trades.values())} 筆交易")
        
        except Exception as e:
            print(f"載入數據失敗: {e}")
            self.hourly_trades = defaultdict(list)
    
    def save_data(self):
        """保存數據到文件"""
        try:
            data = {
                'hourly_trades': {
                    str(hour): trades 
                    for hour, trades in self.hourly_trades.items()
                },
                'hourly_stats': {
                    str(hour): asdict(stats) 
                    for hour, stats in self.hourly_stats.items()
                },
                'last_updated': datetime.now().isoformat()
            }
            
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
        except Exception as e:
            print(f"保存數據失敗: {e}")
    
    def record_trade(
        self,
        entry_time: datetime,
        exit_time: datetime,
        profit: float,
        is_win: bool,
        strategy: str = "unknown",
        metadata: Optional[Dict] = None
    ):
        """
        記錄一筆交易
        
        Args:
            entry_time: 進場時間
            exit_time: 出場時間
            profit: 盈虧（USD）
            is_win: 是否獲勝
            strategy: 策略名稱
            metadata: 額外元數據
        """
        hour = entry_time.hour
        
        trade_record = {
            'entry_time': entry_time.isoformat(),
            'exit_time': exit_time.isoformat(),
            'hour': hour,
            'profit': profit,
            'is_win': is_win,
            'strategy': strategy,
            'metadata': metadata or {}
        }
        
        self.hourly_trades[hour].append(trade_record)
        
        # 更新統計
        self.recalculate_stats_for_hour(hour)
        
        # 定期保存（每 10 筆交易）
        total_trades = sum(len(trades) for trades in self.hourly_trades.values())
        if total_trades % 10 == 0:
            self.save_data()
    
    def recalculate_stats_for_hour(self, hour: int):
        """重新計算特定小時的統計"""
        trades = self.hourly_trades[hour]
        
        if not trades:
            return
        
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t['is_win'])
        losing_trades = total_trades - winning_trades
        
        profits = [t['profit'] for t in trades if t['is_win']]
        losses = [abs(t['profit']) for t in trades if not t['is_win']]
        
        total_profit = sum(profits) if profits else 0
        total_loss = sum(losses) if losses else 0
        
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
        
        avg_profit = total_profit / winning_trades if winning_trades > 0 else 0
        avg_loss = total_loss / losing_trades if losing_trades > 0 else 0
        
        # 期望值 = (勝率 × 平均盈利) - (敗率 × 平均虧損)
        expected_value = (win_rate * avg_profit) - ((1 - win_rate) * avg_loss)
        
        is_profitable = (
            win_rate >= self.min_win_rate and 
            profit_factor >= self.min_profit_factor and
            expected_value > 0
        )
        
        self.hourly_stats[hour] = TimeSlotStats(
            hour=hour,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            total_profit=total_profit,
            total_loss=total_loss,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_profit=avg_profit,
            avg_loss=avg_loss,
            expected_value=expected_value,
            is_profitable=is_profitable
        )
    
    def recalculate_stats(self):
        """重新計算所有時段的統計"""
        for hour in range(24):
            if hour in self.hourly_trades:
                self.recalculate_stats_for_hour(hour)
    
    def should_trade_now(
        self,
        current_time: Optional[datetime] = None
    ) -> TradingHoursRecommendation:
        """
        判斷當前時段是否適合交易
        
        Args:
            current_time: 當前時間（默認為系統時間）
            
        Returns:
            TradingHoursRecommendation 對象
        """
        if current_time is None:
            current_time = datetime.now()
        
        current_hour = current_time.hour
        
        # 檢查是否有該時段的統計數據
        if current_hour not in self.hourly_stats:
            return TradingHoursRecommendation(
                should_trade=True,  # 沒有數據時允許交易（收集數據）
                current_hour=current_hour,
                slot_stats=None,
                reason=f"⚪ {current_hour}:00 - 無歷史數據，允許交易以收集數據",
                confidence=0.3,  # 低信心度
                timestamp=current_time
            )
        
        stats = self.hourly_stats[current_hour]
        
        # 檢查數據量是否足夠
        if stats.total_trades < self.min_trades_required:
            return TradingHoursRecommendation(
                should_trade=True,
                current_hour=current_hour,
                slot_stats=stats,
                reason=f"⚪ {current_hour}:00 - 數據不足 ({stats.total_trades}/{self.min_trades_required})，允許交易",
                confidence=0.5,
                timestamp=current_time
            )
        
        # 判斷是否為盈利時段
        if stats.is_profitable:
            return TradingHoursRecommendation(
                should_trade=True,
                current_hour=current_hour,
                slot_stats=stats,
                reason=f"✅ {current_hour}:00 - 高效時段 (勝率 {stats.win_rate:.1%}, PF {stats.profit_factor:.2f})",
                confidence=min(0.9, stats.total_trades / 100),  # 交易次數越多，信心度越高
                timestamp=current_time
            )
        else:
            return TradingHoursRecommendation(
                should_trade=False,
                current_hour=current_hour,
                slot_stats=stats,
                reason=f"❌ {current_hour}:00 - 低效時段 (勝率 {stats.win_rate:.1%}, PF {stats.profit_factor:.2f})",
                confidence=min(0.9, stats.total_trades / 100),
                timestamp=current_time
            )
    
    def get_best_trading_hours(self, top_n: int = 5) -> List[TimeSlotStats]:
        """
        獲取最佳交易時段
        
        Args:
            top_n: 返回前 N 個時段
            
        Returns:
            按期望值排序的時段列表
        """
        # 過濾數據充足的時段
        valid_stats = [
            stats for stats in self.hourly_stats.values()
            if stats.total_trades >= self.min_trades_required
        ]
        
        # 按期望值排序
        sorted_stats = sorted(valid_stats, key=lambda x: x.expected_value, reverse=True)
        
        return sorted_stats[:top_n]
    
    def get_worst_trading_hours(self, top_n: int = 5) -> List[TimeSlotStats]:
        """獲取最差交易時段"""
        valid_stats = [
            stats for stats in self.hourly_stats.values()
            if stats.total_trades >= self.min_trades_required
        ]
        
        sorted_stats = sorted(valid_stats, key=lambda x: x.expected_value)
        
        return sorted_stats[:top_n]
    
    def get_summary_report(self) -> Dict:
        """
        生成摘要報告
        
        Returns:
            包含統計摘要的字典
        """
        all_stats = list(self.hourly_stats.values())
        
        if not all_stats:
            return {
                'total_hours_analyzed': 0,
                'total_trades': 0,
                'message': '尚無數據'
            }
        
        total_trades = sum(s.total_trades for s in all_stats)
        profitable_hours = [s for s in all_stats if s.is_profitable and s.total_trades >= self.min_trades_required]
        unprofitable_hours = [s for s in all_stats if not s.is_profitable and s.total_trades >= self.min_trades_required]
        
        return {
            'total_hours_analyzed': len(all_stats),
            'total_trades': total_trades,
            'hours_with_sufficient_data': len([s for s in all_stats if s.total_trades >= self.min_trades_required]),
            'profitable_hours': len(profitable_hours),
            'unprofitable_hours': len(unprofitable_hours),
            'best_hours': [s.hour for s in self.get_best_trading_hours(3)],
            'worst_hours': [s.hour for s in self.get_worst_trading_hours(3)],
            'overall_win_rate': (
                sum(s.winning_trades for s in all_stats) / total_trades 
                if total_trades > 0 else 0
            )
        }


# ==================== 使用範例 ====================
if __name__ == "__main__":
    # 創建分析器
    analyzer = TimeZoneAnalyzer(
        data_file="data/test_time_zone_analysis.json",
        min_trades_required=10,
        min_win_rate=0.55
    )
    
    # 模擬記錄交易
    print("=== 模擬記錄交易 ===")
    np.random.seed(42)
    
    for i in range(100):
        hour = np.random.randint(0, 24)
        entry_time = datetime(2025, 11, 14, hour, np.random.randint(0, 60))
        exit_time = datetime(2025, 11, 14, hour, np.random.randint(0, 60))
        
        # 9-17點 勝率高，其他時段勝率低
        if 9 <= hour <= 17:
            is_win = np.random.random() < 0.65  # 65% 勝率
        else:
            is_win = np.random.random() < 0.45  # 45% 勝率
        
        profit = np.random.uniform(5, 20) if is_win else -np.random.uniform(3, 15)
        
        analyzer.record_trade(entry_time, exit_time, profit, is_win)
    
    # 測試當前時段
    print("\n=== 當前時段判斷 ===")
    for test_hour in [10, 15, 22, 2]:
        test_time = datetime(2025, 11, 14, test_hour, 30)
        recommendation = analyzer.should_trade_now(test_time)
        print(f"{recommendation.reason}")
        if recommendation.slot_stats:
            print(f"  期望值: ${recommendation.slot_stats.expected_value:.2f}")
    
    # 最佳/最差時段
    print("\n=== 最佳交易時段 ===")
    for stats in analyzer.get_best_trading_hours(3):
        print(f"{stats.hour}:00 - 勝率 {stats.win_rate:.1%}, PF {stats.profit_factor:.2f}, EV ${stats.expected_value:.2f}")
    
    print("\n=== 最差交易時段 ===")
    for stats in analyzer.get_worst_trading_hours(3):
        print(f"{stats.hour}:00 - 勝率 {stats.win_rate:.1%}, PF {stats.profit_factor:.2f}, EV ${stats.expected_value:.2f}")
    
    # 摘要報告
    print("\n=== 摘要報告 ===")
    report = analyzer.get_summary_report()
    for key, value in report.items():
        print(f"{key}: {value}")
