"""
策略調度器 - Strategy Orchestrator
智能選擇最合適的策略執行交易
根據時段、VPIN、市場狀態動態調度

作者: Phase 2 優化項目
日期: 2025-11-14
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class TimeFrame(Enum):
    """時間框架枚舉"""
    ULTRA_HIGH_FREQ = "1m"      # 超高頻
    HIGH_FREQ = "5m"            # 高頻
    MID_FREQ = "15m"            # 中頻
    LOW_FREQ = "1h"             # 低頻
    VERY_LOW_FREQ = "4h"        # 超低頻


class MarketRegime(Enum):
    """市場狀態枚舉"""
    SAFE = "SAFE"               # 安全：VPIN < 0.3
    NORMAL = "NORMAL"           # 正常：VPIN 0.3-0.5
    RISKY = "RISKY"             # 風險：VPIN 0.5-0.7
    DANGEROUS = "DANGEROUS"     # 危險：VPIN > 0.7


@dataclass
class StrategyConfig:
    """策略配置"""
    strategy_id: str
    name: str
    
    # 時段限制
    allowed_hours: List[int]  # 允許交易的小時列表，如 [9,10,11,...,16] 或 None（全天）
    
    # 時間框架
    preferred_timeframe: TimeFrame
    
    # VPIN 範圍
    vpin_min: float  # 最小 VPIN
    vpin_max: float  # 最大 VPIN
    
    # 風控參數
    max_open_positions: int  # 最大同時持倉數
    max_loss_per_day: float  # 每日最大損失（USD）
    max_consecutive_losses: int  # 最大連續虧損次數
    
    # 策略類型
    strategy_type: str  # aggressive, defensive, neutral
    
    # 優先級（數字越大優先級越高）
    priority: int = 5


class StrategyOrchestrator:
    """
    策略調度器
    
    功能：
    1. 時段調度：不同時段使用不同頻率策略
    2. VPIN 調度：根據市場風險選擇防禦/進攻策略
    3. 風控管理：控制每策略的開倉數和損失額
    4. 智能切換：動態選擇最合適的策略
    """
    
    def __init__(self):
        """初始化策略調度器"""
        # 策略配置列表
        self.strategies: Dict[str, StrategyConfig] = {}
        
        # 策略運行狀態
        self.strategy_stats: Dict[str, Dict] = {}
        
        # 當前活躍策略
        self.active_strategies: List[str] = []
        
        # 時段配置
        self._setup_time_rules()
        
        logger.info("策略調度器初始化完成")
    
    def _setup_time_rules(self):
        """
        設置時段規則
        
        交易時段劃分：
        - 亞洲時段（00:00-08:00 UTC+8）：低頻策略
        - 歐洲開盤（15:00-17:00 UTC+8）：中高頻策略
        - 美洲時段（21:00-05:00 UTC+8）：高頻策略
        - 深夜時段（02:00-06:00 UTC+8）：超低頻或暫停
        """
        self.time_rules = {
            'asian_session': {
                'hours': list(range(8, 15)),  # 8:00-14:59
                'preferred_timeframe': TimeFrame.MID_FREQ,
                'description': '亞洲時段 - 中頻策略'
            },
            'europe_session': {
                'hours': list(range(15, 21)),  # 15:00-20:59
                'preferred_timeframe': TimeFrame.HIGH_FREQ,
                'description': '歐洲時段 - 高頻策略'
            },
            'america_session': {
                'hours': list(range(21, 24)) + list(range(0, 2)),  # 21:00-01:59
                'preferred_timeframe': TimeFrame.HIGH_FREQ,
                'description': '美洲時段 - 高頻策略'
            },
            'night_session': {
                'hours': list(range(2, 8)),  # 02:00-07:59
                'preferred_timeframe': TimeFrame.LOW_FREQ,
                'description': '深夜時段 - 低頻策略'
            }
        }
    
    def register_strategy(self, config: StrategyConfig):
        """
        註冊策略
        
        Args:
            config: 策略配置
        """
        self.strategies[config.strategy_id] = config
        
        # 初始化統計
        self.strategy_stats[config.strategy_id] = {
            'open_positions': 0,
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'consecutive_losses': 0,
            'daily_pnl': 0.0,
            'last_trade_time': None,
            'is_active': True
        }
        
        logger.info(f"策略已註冊: {config.name} ({config.strategy_id})")
    
    def get_current_session(self, current_hour: Optional[int] = None) -> Tuple[str, TimeFrame]:
        """
        獲取當前時段
        
        Args:
            current_hour: 當前小時（0-23），默認使用系統時間
            
        Returns:
            (session_name, preferred_timeframe)
        """
        if current_hour is None:
            current_hour = datetime.now().hour
        
        for session_name, rules in self.time_rules.items():
            if current_hour in rules['hours']:
                return session_name, rules['preferred_timeframe']
        
        # 默認返回中頻
        return 'default', TimeFrame.MID_FREQ
    
    def get_market_regime(self, vpin: float) -> MarketRegime:
        """
        判斷市場狀態
        
        Args:
            vpin: VPIN 值
            
        Returns:
            MarketRegime 枚舉
        """
        if vpin < 0.3:
            return MarketRegime.SAFE
        elif vpin < 0.5:
            return MarketRegime.NORMAL
        elif vpin < 0.7:
            return MarketRegime.RISKY
        else:
            return MarketRegime.DANGEROUS
    
    def select_strategies(
        self,
        vpin: float,
        current_time: Optional[datetime] = None
    ) -> List[str]:
        """
        選擇當前應該啟用的策略
        
        Args:
            vpin: 當前 VPIN 值
            current_time: 當前時間，默認使用系統時間
            
        Returns:
            應啟用的策略 ID 列表
        """
        if current_time is None:
            current_time = datetime.now()
        
        current_hour = current_time.hour
        market_regime = self.get_market_regime(vpin)
        session_name, preferred_timeframe = self.get_current_session(current_hour)
        
        selected_strategies = []
        
        for strategy_id, config in self.strategies.items():
            stats = self.strategy_stats[strategy_id]
            
            # 檢查 1: 策略是否已停用
            if not stats['is_active']:
                continue
            
            # 檢查 2: 時段限制
            if config.allowed_hours and current_hour not in config.allowed_hours:
                continue
            
            # 檢查 3: VPIN 範圍
            if not (config.vpin_min <= vpin <= config.vpin_max):
                continue
            
            # 檢查 4: 時間框架匹配（優先選擇匹配的）
            # 如果不匹配但在合理範圍內也可以使用
            
            # 檢查 5: 風控限制
            if stats['consecutive_losses'] >= config.max_consecutive_losses:
                logger.warning(f"策略 {config.name} 達到最大連續虧損，暫停使用")
                continue
            
            if abs(stats['daily_pnl']) >= config.max_loss_per_day:
                logger.warning(f"策略 {config.name} 達到每日最大損失，暫停使用")
                continue
            
            if stats['open_positions'] >= config.max_open_positions:
                logger.debug(f"策略 {config.name} 達到最大持倉數")
                continue
            
            # 檢查 6: 根據市場狀態選擇策略類型
            if market_regime == MarketRegime.DANGEROUS:
                # 危險時只用防禦型策略
                if config.strategy_type != 'defensive':
                    continue
            elif market_regime == MarketRegime.RISKY:
                # 風險時避免進攻型策略
                if config.strategy_type == 'aggressive':
                    continue
            
            selected_strategies.append(strategy_id)
        
        # 按優先級排序
        selected_strategies.sort(
            key=lambda sid: self.strategies[sid].priority,
            reverse=True
        )
        
        self.active_strategies = selected_strategies
        
        logger.info(
            f"時段: {session_name} | VPIN: {vpin:.3f} ({market_regime.value}) | "
            f"已選擇 {len(selected_strategies)} 個策略"
        )
        
        return selected_strategies
    
    def should_trade(self, strategy_id: str) -> Tuple[bool, str]:
        """
        判斷某策略是否應該交易
        
        Args:
            strategy_id: 策略 ID
            
        Returns:
            (should_trade, reason)
        """
        if strategy_id not in self.active_strategies:
            return False, "策略未啟用"
        
        stats = self.strategy_stats.get(strategy_id)
        if not stats:
            return False, "策略不存在"
        
        if not stats['is_active']:
            return False, "策略已停用"
        
        config = self.strategies[strategy_id]
        
        # 檢查持倉限制
        if stats['open_positions'] >= config.max_open_positions:
            return False, f"持倉數已達上限 ({config.max_open_positions})"
        
        # 檢查損失限制
        if abs(stats['daily_pnl']) >= config.max_loss_per_day:
            return False, f"已達每日最大損失 (${config.max_loss_per_day})"
        
        # 檢查連續虧損
        if stats['consecutive_losses'] >= config.max_consecutive_losses:
            return False, f"連續虧損過多 ({config.max_consecutive_losses})"
        
        return True, "允許交易"
    
    def update_position(self, strategy_id: str, opened: bool):
        """
        更新持倉數
        
        Args:
            strategy_id: 策略 ID
            opened: True=開倉，False=平倉
        """
        if strategy_id not in self.strategy_stats:
            return
        
        stats = self.strategy_stats[strategy_id]
        
        if opened:
            stats['open_positions'] += 1
        else:
            stats['open_positions'] = max(0, stats['open_positions'] - 1)
    
    def record_trade(
        self,
        strategy_id: str,
        profit: float,
        is_win: bool
    ):
        """
        記錄交易結果
        
        Args:
            strategy_id: 策略 ID
            profit: 盈虧金額（USD）
            is_win: 是否獲勝
        """
        if strategy_id not in self.strategy_stats:
            return
        
        stats = self.strategy_stats[strategy_id]
        
        stats['total_trades'] += 1
        stats['daily_pnl'] += profit
        stats['last_trade_time'] = datetime.now()
        
        if is_win:
            stats['winning_trades'] += 1
            stats['consecutive_losses'] = 0
        else:
            stats['losing_trades'] += 1
            stats['consecutive_losses'] += 1
    
    def reset_daily_stats(self):
        """重置每日統計（應在每天開始時調用）"""
        for strategy_id in self.strategy_stats:
            self.strategy_stats[strategy_id]['daily_pnl'] = 0.0
            self.strategy_stats[strategy_id]['consecutive_losses'] = 0
        
        logger.info("每日統計已重置")
    
    def get_strategy_report(self) -> Dict:
        """
        生成策略報告
        
        Returns:
            包含所有策略狀態的字典
        """
        report = {
            'total_strategies': len(self.strategies),
            'active_strategies': len(self.active_strategies),
            'strategies': []
        }
        
        for strategy_id, config in self.strategies.items():
            stats = self.strategy_stats[strategy_id]
            
            win_rate = (
                stats['winning_trades'] / stats['total_trades']
                if stats['total_trades'] > 0
                else 0
            )
            
            strategy_info = {
                'id': strategy_id,
                'name': config.name,
                'type': config.strategy_type,
                'is_active': stats['is_active'],
                'is_selected': strategy_id in self.active_strategies,
                'stats': {
                    'total_trades': stats['total_trades'],
                    'win_rate': win_rate,
                    'open_positions': stats['open_positions'],
                    'daily_pnl': stats['daily_pnl'],
                    'consecutive_losses': stats['consecutive_losses']
                },
                'config': {
                    'vpin_range': f"{config.vpin_min:.2f}-{config.vpin_max:.2f}",
                    'timeframe': config.preferred_timeframe.value,
                    'max_positions': config.max_open_positions,
                    'max_loss': config.max_loss_per_day
                }
            }
            
            report['strategies'].append(strategy_info)
        
        return report


# ==================== 使用範例 ====================
if __name__ == "__main__":
    # 配置日誌
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 創建調度器
    orchestrator = StrategyOrchestrator()
    
    # 註冊策略 1：進攻型（低 VPIN，白天交易）
    orchestrator.register_strategy(StrategyConfig(
        strategy_id="aggressive_day",
        name="日間進攻策略",
        allowed_hours=list(range(9, 21)),  # 9:00-20:59
        preferred_timeframe=TimeFrame.HIGH_FREQ,
        vpin_min=0.0,
        vpin_max=0.4,
        max_open_positions=3,
        max_loss_per_day=50.0,
        max_consecutive_losses=3,
        strategy_type='aggressive',
        priority=8
    ))
    
    # 註冊策略 2：防禦型（高 VPIN，全天候）
    orchestrator.register_strategy(StrategyConfig(
        strategy_id="defensive_all",
        name="全天防禦策略",
        allowed_hours=None,  # 全天候
        preferred_timeframe=TimeFrame.MID_FREQ,
        vpin_min=0.4,
        vpin_max=1.0,
        max_open_positions=2,
        max_loss_per_day=30.0,
        max_consecutive_losses=5,
        strategy_type='defensive',
        priority=6
    ))
    
    # 註冊策略 3：夜間低頻
    orchestrator.register_strategy(StrategyConfig(
        strategy_id="night_low_freq",
        name="夜間低頻策略",
        allowed_hours=list(range(0, 8)),  # 00:00-07:59
        preferred_timeframe=TimeFrame.LOW_FREQ,
        vpin_min=0.0,
        vpin_max=0.6,
        max_open_positions=1,
        max_loss_per_day=20.0,
        max_consecutive_losses=2,
        strategy_type='neutral',
        priority=5
    ))
    
    print("\n=== 策略調度器測試 ===\n")
    
    # 測試場景 1：白天，低 VPIN
    print("場景 1：白天 10:00，VPIN=0.25（安全）")
    test_time = datetime(2025, 11, 14, 10, 0)
    selected = orchestrator.select_strategies(vpin=0.25, current_time=test_time)
    print(f"選中策略: {selected}\n")
    
    # 測試場景 2：白天，高 VPIN
    print("場景 2：白天 15:00，VPIN=0.75（危險）")
    test_time = datetime(2025, 11, 14, 15, 0)
    selected = orchestrator.select_strategies(vpin=0.75, current_time=test_time)
    print(f"選中策略: {selected}\n")
    
    # 測試場景 3：深夜，中等 VPIN
    print("場景 3：深夜 03:00，VPIN=0.35（正常）")
    test_time = datetime(2025, 11, 14, 3, 0)
    selected = orchestrator.select_strategies(vpin=0.35, current_time=test_time)
    print(f"選中策略: {selected}\n")
    
    # 模擬交易
    print("=== 模擬交易 ===\n")
    
    # 開倉
    orchestrator.update_position("aggressive_day", opened=True)
    print("aggressive_day 開倉")
    
    # 記錄虧損
    orchestrator.record_trade("aggressive_day", profit=-10.0, is_win=False)
    print("記錄虧損: -$10")
    
    # 平倉
    orchestrator.update_position("aggressive_day", opened=False)
    print("aggressive_day 平倉\n")
    
    # 生成報告
    print("=== 策略報告 ===")
    report = orchestrator.get_strategy_report()
    print(f"總策略數: {report['total_strategies']}")
    print(f"活躍策略數: {report['active_strategies']}\n")
    
    for strategy in report['strategies']:
        print(f"{strategy['name']} ({strategy['id']}):")
        print(f"  類型: {strategy['type']}")
        print(f"  已選中: {strategy['is_selected']}")
        print(f"  交易次數: {strategy['stats']['total_trades']}")
        print(f"  勝率: {strategy['stats']['win_rate']:.1%}")
        print(f"  當日盈虧: ${strategy['stats']['daily_pnl']:.2f}")
        print()
