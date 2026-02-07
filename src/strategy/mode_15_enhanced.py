"""
M15 策略 - 多維度智能方案切換策略
具備全自動 ABC 方案動態切換機制
包含：緊急情況響應、預測性切換、表現驅動、市場狀態感知
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import numpy as np
from collections import deque

# 導入 M14 的基礎類
from .mode_14_dynamic_leverage import (
    MarketRegimeDetector,
    SignalQualityScorer,
    CostAwareProfitCalculator,
    DynamicLeverageAdjuster,
    DynamicPositionSizer,
    DynamicTPSLAdjuster,
    TradingScheme,
    Mode14Strategy
)

logger = logging.getLogger(__name__)


class EmergencyCircuitBreaker:
    """緊急熔斷機制 - 防止連續虧損和過度風險"""
    
    def __init__(self):
        self.consecutive_losses = 0
        self.max_consecutive_losses = 3
        self.daily_loss_limit = -0.15  # 單日虧損15%
        self.session_start_time = datetime.now()
        self.session_start_balance = None
        self.is_halted = False
        self.halt_reason = None
        
    def reset_session(self, initial_balance: float):
        """重置交易會話"""
        self.session_start_time = datetime.now()
        self.session_start_balance = initial_balance
        self.consecutive_losses = 0
        self.is_halted = False
        self.halt_reason = None
        
    def check_circuit_breaker(self, trade_result: Dict, current_balance: float) -> bool:
        """
        檢查是否觸發熔斷
        
        Args:
            trade_result: {'profit': float, 'scheme': str}
            current_balance: 當前餘額
            
        Returns:
            bool: True=允許交易, False=熔斷觸發
        """
        # 連續虧損熔斷
        if trade_result['profit'] < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
            
        if self.consecutive_losses >= self.max_consecutive_losses:
            self.halt_reason = f"連續虧損{self.consecutive_losses}次"
            logger.error(f"🔴 觸發連續虧損熔斷: {self.consecutive_losses}次")
            self.is_halted = True
            return False
            
        # 單日虧損熔斷
        if self.session_start_balance is not None:
            daily_pnl = (current_balance - self.session_start_balance) / self.session_start_balance
            if daily_pnl < self.daily_loss_limit:
                self.halt_reason = f"單日虧損{daily_pnl:.1%}"
                logger.error(f"🔴 觸發單日虧損熔斷: {daily_pnl:.1%}")
                self.is_halted = True
                return False
            
        return True
    
    def can_trade(self) -> Tuple[bool, Optional[str]]:
        """
        檢查是否允許交易
        
        Returns:
            Tuple[bool, str]: (是否允許, 熔斷原因)
        """
        return not self.is_halted, self.halt_reason
    
    def manual_reset(self):
        """手動解除熔斷（需要管理員確認）"""
        logger.warning("⚠️ 手動解除熔斷機制")
        self.is_halted = False
        self.halt_reason = None
        self.consecutive_losses = 0


class SmoothTransitionManager:
    """平滑過渡管理器 - 避免 A↔C 劇烈切換"""
    
    def __init__(self):
        self.transition_phase = None
        self.transition_start = None
        self.intermediate_scheme = None
        self.target_scheme = None
        self.transition_duration = 30  # 30分鐘過渡期
        
    def manage_transition(self, from_scheme: str, to_scheme: str) -> Tuple[str, bool]:
        """
        管理方案過渡
        
        Args:
            from_scheme: 當前方案
            to_scheme: 目標方案
            
        Returns:
            Tuple[str, bool]: (實際使用的方案, 是否在過渡中)
        """
        # 直接切換的情況
        if from_scheme == to_scheme:
            return to_scheme, False
            
        # B方案可以直接切換到任何方案
        if from_scheme == "B" or to_scheme == "B":
            return to_scheme, False
            
        # A↔C 需要平滑過渡
        if (from_scheme == "A" and to_scheme == "C") or (from_scheme == "C" and to_scheme == "A"):
            if self.transition_phase is None:
                # 開始過渡
                self.transition_phase = "transitioning"
                self.transition_start = datetime.now()
                self.intermediate_scheme = "B"
                self.target_scheme = to_scheme
                logger.info(f"🔄 啟動平滑過渡: {from_scheme} → B → {to_scheme} (預計{self.transition_duration}分鐘)")
                return "B", True
                
            elif self.transition_phase == "transitioning":
                # 檢查過渡時間
                transition_time = (datetime.now() - self.transition_start).total_seconds() / 60
                
                if transition_time >= self.transition_duration:
                    # 完成過渡
                    self.transition_phase = None
                    self.intermediate_scheme = None
                    final_scheme = self.target_scheme
                    self.target_scheme = None
                    logger.info(f"✅ 完成平滑過渡: B → {final_scheme}")
                    return final_scheme, False
                else:
                    # 保持過渡方案
                    remaining = self.transition_duration - transition_time
                    logger.debug(f"🔄 過渡中: 剩餘 {remaining:.1f} 分鐘")
                    return "B", True
        
        return to_scheme, False
    
    def is_in_transition(self) -> bool:
        """是否正在過渡中"""
        return self.transition_phase == "transitioning"
    
    def get_transition_status(self) -> Dict:
        """獲取過渡狀態"""
        if not self.is_in_transition():
            return {'in_transition': False}
            
        elapsed = (datetime.now() - self.transition_start).total_seconds() / 60
        remaining = max(0, self.transition_duration - elapsed)
        progress = min(1.0, elapsed / self.transition_duration)
        
        return {
            'in_transition': True,
            'intermediate_scheme': self.intermediate_scheme,
            'target_scheme': self.target_scheme,
            'elapsed_minutes': elapsed,
            'remaining_minutes': remaining,
            'progress': progress
        }


class ExtremeMarketHandler:
    """極端市場處理器 - 強化風險控制"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.extreme_thresholds = self.config.get('extreme_thresholds', {
            'vpin_critical': 0.8,
            'vpin_high': 0.7,
            'spread_critical': 25,
            'spread_high': 20,
            'volatility_critical': 0.05,
            'volatility_high': 0.04
        })
        
    def handle_extreme_conditions(self, market_data: Dict, current_scheme: str) -> Tuple[Optional[str], str]:
        """
        處理極端市場條件
        
        Args:
            market_data: 市場數據
            current_scheme: 當前方案
            
        Returns:
            Tuple[str, str]: (強制方案, 原因) - None表示無需強制切換
        """
        vpin = market_data.get('vpin', 0.5)
        spread = market_data.get('spread_bps', 10)
        volatility = market_data.get('volatility', 0.02)
        
        # 極端高波動 - 強制降級到A
        if vpin > self.extreme_thresholds['vpin_critical']:
            return "A", f"極端VPIN: {vpin:.2f}"
            
        # 流動性危機 - 暫停交易
        if spread > self.extreme_thresholds['spread_critical']:
            return "PAUSE", f"流動性危機: Spread {spread:.1f}bps"
            
        # 波動率爆炸 - 降級處理
        if volatility > self.extreme_thresholds['volatility_critical']:
            return "A", f"波動率過高: {volatility:.2%}"
            
        # 高風險市場 - 禁止C方案
        if vpin > self.extreme_thresholds['vpin_high'] and current_scheme == "C":
            return "B", f"高VPIN禁止C方案: {vpin:.2f}"
            
        if spread > self.extreme_thresholds['spread_high'] and current_scheme == "C":
            return "B", f"流動性不足禁止C方案: {spread:.1f}bps"
            
        # 正常情況，返回None
        return None, "市場正常"
    
    def get_market_risk_level(self, market_data: Dict) -> str:
        """
        獲取市場風險等級
        
        Returns:
            "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
        """
        vpin = market_data.get('vpin', 0.5)
        spread = market_data.get('spread_bps', 10)
        volatility = market_data.get('volatility', 0.02)
        
        if (vpin > self.extreme_thresholds['vpin_critical'] or 
            spread > self.extreme_thresholds['spread_critical'] or
            volatility > self.extreme_thresholds['volatility_critical']):
            return "CRITICAL"
            
        if (vpin > self.extreme_thresholds['vpin_high'] or 
            spread > self.extreme_thresholds['spread_high'] or
            volatility > self.extreme_thresholds['volatility_high']):
            return "HIGH"
            
        if vpin > 0.5 or spread > 15 or volatility > 0.03:
            return "MEDIUM"
            
        return "LOW"


class EnhancedPerformanceMonitor:
    """增強版性能監控 - 實時預警系統"""
    
    def __init__(self):
        self.performance_history = deque(maxlen=100)
        self.alert_triggers = {
            'drawdown_alert': -0.08,       # 回撤8%警告
            'drawdown_critical': -0.12,    # 回撤12%嚴重
            'consecutive_loss_alert': 2,   # 連續2次虧損警告
            'consecutive_loss_critical': 3, # 連續3次嚴重
            'win_rate_alert': 0.3,         # 勝率低於30%警告
            'win_rate_critical': 0.2,      # 勝率低於20%嚴重
            'vpin_alert': 0.7,             # VPIN超過0.7警告
            'vpin_critical': 0.8           # VPIN超過0.8嚴重
        }
        self.active_alerts = []
        
    def add_trade(self, trade_result: Dict):
        """添加交易記錄"""
        self.performance_history.append({
            'profit': trade_result['profit'],
            'time': trade_result.get('time', datetime.now()),
            'scheme': trade_result.get('scheme', 'unknown')
        })
        
    def check_performance_alerts(self, market_data: Dict) -> List[Dict]:
        """
        檢查性能預警
        
        Returns:
            List[Dict]: [{'level': 'WARNING'|'CRITICAL', 'message': str}, ...]
        """
        alerts = []
        
        if not self.performance_history:
            return alerts
        
        # 計算當前回撤
        current_drawdown = self.calculate_drawdown()
        if current_drawdown < self.alert_triggers['drawdown_critical']:
            alerts.append({
                'level': 'CRITICAL',
                'type': 'drawdown',
                'message': f"嚴重回撤: {current_drawdown:.1%}"
            })
        elif current_drawdown < self.alert_triggers['drawdown_alert']:
            alerts.append({
                'level': 'WARNING',
                'type': 'drawdown',
                'message': f"回撤警報: {current_drawdown:.1%}"
            })
            
        # 檢查連續虧損
        consecutive_losses = self.count_consecutive_losses()
        if consecutive_losses >= self.alert_triggers['consecutive_loss_critical']:
            alerts.append({
                'level': 'CRITICAL',
                'type': 'consecutive_loss',
                'message': f"嚴重連續虧損: {consecutive_losses}次"
            })
        elif consecutive_losses >= self.alert_triggers['consecutive_loss_alert']:
            alerts.append({
                'level': 'WARNING',
                'type': 'consecutive_loss',
                'message': f"連續虧損: {consecutive_losses}次"
            })
            
        # 檢查勝率
        if len(self.performance_history) >= 10:
            win_rate = self.calculate_win_rate(lookback=10)
            if win_rate < self.alert_triggers['win_rate_critical']:
                alerts.append({
                    'level': 'CRITICAL',
                    'type': 'win_rate',
                    'message': f"勝率過低: {win_rate:.1%}"
                })
            elif win_rate < self.alert_triggers['win_rate_alert']:
                alerts.append({
                    'level': 'WARNING',
                    'type': 'win_rate',
                    'message': f"勝率警告: {win_rate:.1%}"
                })
            
        # 檢查市場條件
        vpin = market_data.get('vpin', 0.5)
        if vpin > self.alert_triggers['vpin_critical']:
            alerts.append({
                'level': 'CRITICAL',
                'type': 'vpin',
                'message': f"VPIN嚴重過高: {vpin:.2f}"
            })
        elif vpin > self.alert_triggers['vpin_alert']:
            alerts.append({
                'level': 'WARNING',
                'type': 'vpin',
                'message': f"VPIN過高: {vpin:.2f}"
            })
            
        self.active_alerts = alerts
        return alerts
    
    def calculate_drawdown(self) -> float:
        """計算最大回撤"""
        if not self.performance_history:
            return 0.0
            
        cumulative = 0
        peak = 0
        max_drawdown = 0
        
        for trade in self.performance_history:
            cumulative += trade['profit']
            if cumulative > peak:
                peak = cumulative
            
            if peak != 0:
                drawdown = (cumulative - peak) / abs(peak)
                if drawdown < max_drawdown:
                    max_drawdown = drawdown
                
        return max_drawdown
    
    def count_consecutive_losses(self) -> int:
        """計算連續虧損次數"""
        if not self.performance_history:
            return 0
            
        consecutive = 0
        for trade in reversed(self.performance_history):
            if trade['profit'] < 0:
                consecutive += 1
            else:
                break
                
        return consecutive
    
    def calculate_win_rate(self, lookback: int = 20) -> float:
        """計算勝率"""
        if not self.performance_history:
            return 0.5
            
        recent_trades = list(self.performance_history)[-lookback:]
        wins = sum(1 for t in recent_trades if t['profit'] > 0)
        
        return wins / len(recent_trades) if recent_trades else 0.5
    
    def get_performance_summary(self) -> Dict:
        """獲取性能摘要"""
        if not self.performance_history:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'drawdown': 0.0,
                'consecutive_losses': 0,
                'active_alerts': []
            }
            
        return {
            'total_trades': len(self.performance_history),
            'win_rate': self.calculate_win_rate(),
            'drawdown': self.calculate_drawdown(),
            'consecutive_losses': self.count_consecutive_losses(),
            'active_alerts': self.active_alerts
        }


class RealTimeMarketAssessor:
    """實時市場評估器"""
    
    def __init__(self):
        self.market_history = deque(maxlen=50)  # 保留最近50次評估
    
    def calculate_market_score(self, market_data: Dict) -> float:
        """
        計算市場綜合評分 (0-1)
        
        評分維度：
        - VPIN 25%: 市場毒性
        - 流動性 25%: Spread + Depth
        - 趨勢質量 30%: OBI強度 + 成交量
        - 波動率 20%: 價格波動
        """
        scores = []
        
        # VPIN評分 (25%) - 越低越好
        vpin = market_data.get('vpin', 0.5)
        vpin_score = max(0, 1 - (vpin / 0.8))
        scores.append(vpin_score * 0.25)
        
        # 流動性評分 (25%)
        spread = market_data.get('spread_bps', 10)
        depth = market_data.get('total_depth', 3)
        spread_score = max(0, 1 - (spread / 20))  # spread越小越好
        depth_score = min(1, depth / 8)  # depth越大越好
        liquidity_score = (spread_score + depth_score) / 2
        scores.append(liquidity_score * 0.25)
        
        # 趨勢質量評分 (30%)
        obi_strength = abs(market_data.get('obi', 0))
        volume = market_data.get('volume', 1)
        avg_volume = market_data.get('avg_volume', 1)
        volume_ratio = volume / max(1, avg_volume)
        trend_score = min(1, (obi_strength + min(2, volume_ratio)) / 2)
        scores.append(trend_score * 0.3)
        
        # 波動率評分 (20%) - 適度波動最好
        volatility = market_data.get('volatility', 0.02)
        if volatility < 0.01:  # 太低
            volatility_score = 0.5
        elif volatility > 0.04:  # 太高
            volatility_score = max(0, 1 - (volatility / 0.06))
        else:  # 適度波動
            volatility_score = 1.0
        scores.append(volatility_score * 0.2)
        
        total_score = sum(scores)
        
        # 記錄歷史
        self.market_history.append({
            'timestamp': datetime.now(),
            'score': total_score,
            'components': {
                'vpin': vpin_score,
                'liquidity': liquidity_score,
                'trend': trend_score,
                'volatility': volatility_score
            }
        })
        
        return total_score
    
    def get_score_trend(self) -> str:
        """
        獲取市場評分趨勢
        
        Returns:
            "improving" | "deteriorating" | "stable"
        """
        if len(self.market_history) < 5:
            return "stable"
        
        recent_scores = [h['score'] for h in list(self.market_history)[-5:]]
        older_scores = [h['score'] for h in list(self.market_history)[-10:-5]]
        
        if len(older_scores) < 5:
            return "stable"
        
        recent_avg = np.mean(recent_scores)
        older_avg = np.mean(older_scores)
        
        diff = recent_avg - older_avg
        
        if diff > 0.15:
            return "improving"
        elif diff < -0.15:
            return "deteriorating"
        else:
            return "stable"


class PredictiveEngine:
    """預測引擎 - 基於技術指標和歷史數據預測市場走向"""
    
    def __init__(self):
        self.price_history = deque(maxlen=30)
        self.vpin_history = deque(maxlen=20)
        self.obi_history = deque(maxlen=20)
    
    def update_data(self, price: float, vpin: float, obi: float):
        """更新歷史數據"""
        self.price_history.append({
            'timestamp': datetime.now(),
            'price': price
        })
        self.vpin_history.append(vpin)
        self.obi_history.append(obi)
    
    def predict_trend_change(self, market_data: Dict) -> Dict:
        """
        預測趨勢變化
        
        Returns:
            {
                'improving': bool,
                'deteriorating': bool,
                'confidence': float,
                'timeframe': str
            }
        """
        if len(self.price_history) < 10:
            return {
                'improving': False,
                'deteriorating': False,
                'confidence': 0.0,
                'timeframe': 'insufficient_data'
            }
        
        # 計算動量指標
        recent_prices = [p['price'] for p in list(self.price_history)[-10:]]
        price_momentum = (recent_prices[-1] - recent_prices[0]) / recent_prices[0]
        
        # OBI趨勢
        if len(self.obi_history) >= 5:
            recent_obi = list(self.obi_history)[-5:]
            obi_trend = np.mean(recent_obi)
        else:
            obi_trend = 0
        
        # VPIN趨勢
        if len(self.vpin_history) >= 5:
            recent_vpin = list(self.vpin_history)[-5:]
            older_vpin = list(self.vpin_history)[-10:-5] if len(self.vpin_history) >= 10 else recent_vpin
            vpin_trend = np.mean(recent_vpin) - np.mean(older_vpin)
        else:
            vpin_trend = 0
        
        # 綜合判斷
        improving = False
        deteriorating = False
        confidence = 0.0
        
        # 市場好轉信號
        if vpin_trend < -0.1 and abs(obi_trend) > 0.5:
            improving = True
            confidence = 0.75
        
        # 市場惡化信號
        if vpin_trend > 0.15 or (vpin_trend > 0.05 and abs(obi_trend) < 0.3):
            deteriorating = True
            confidence = 0.8
        
        return {
            'improving': improving,
            'deteriorating': deteriorating,
            'confidence': confidence,
            'timeframe': '15min'
        }
    
    def predict_volatility(self, market_data: Dict) -> Dict:
        """
        預測波動率變化
        
        Returns:
            {
                'increasing': bool,
                'decreasing': bool,
                'confidence': float,
                'timeframe': str
            }
        """
        if len(self.price_history) < 10:
            return {
                'increasing': False,
                'decreasing': False,
                'confidence': 0.0,
                'timeframe': 'insufficient_data'
            }
        
        # 計算歷史波動率
        prices = [p['price'] for p in self.price_history]
        recent_volatility = np.std(prices[-5:]) / np.mean(prices[-5:])
        older_volatility = np.std(prices[-10:-5]) / np.mean(prices[-10:-5])
        
        volatility_change = recent_volatility - older_volatility
        
        increasing = volatility_change > 0.001
        decreasing = volatility_change < -0.001
        confidence = min(0.9, abs(volatility_change) * 1000)
        
        return {
            'increasing': increasing,
            'decreasing': decreasing,
            'confidence': confidence,
            'timeframe': '10min'
        }


class PerformanceMonitor:
    """表現監控器 - 追蹤交易表現和統計數據"""
    
    def __init__(self):
        self.trade_history = deque(maxlen=50)
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.scheme_performance = {
            "A": {"trades": 0, "wins": 0, "total_profit": 0.0},
            "B": {"trades": 0, "wins": 0, "total_profit": 0.0},
            "C": {"trades": 0, "wins": 0, "total_profit": 0.0}
        }
    
    def add_trade(self, profit: float, scheme: str, entry_time: datetime):
        """添加交易記錄"""
        trade = {
            'profit': profit,
            'scheme': scheme,
            'time': entry_time,
            'win': profit > 0
        }
        
        self.trade_history.append(trade)
        
        # 更新連續勝敗
        if profit > 0:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
        
        # 更新方案統計
        self.scheme_performance[scheme]['trades'] += 1
        if profit > 0:
            self.scheme_performance[scheme]['wins'] += 1
        self.scheme_performance[scheme]['total_profit'] += profit
    
    def get_performance_metrics(self, lookback: int = 10) -> Dict:
        """
        獲取表現指標
        
        Args:
            lookback: 回看交易數量
        
        Returns:
            {
                'win_rate': float,
                'profit_factor': float,
                'consecutive_wins': int,
                'consecutive_losses': int,
                'drawdown': float,
                'avg_profit': float
            }
        """
        if not self.trade_history:
            return {
                'win_rate': 0.5,
                'profit_factor': 1.0,
                'consecutive_wins': 0,
                'consecutive_losses': 0,
                'drawdown': 0.0,
                'avg_profit': 0.0
            }
        
        recent_trades = list(self.trade_history)[-lookback:]
        
        # 勝率
        wins = sum(1 for t in recent_trades if t['win'])
        win_rate = wins / len(recent_trades)
        
        # 盈虧比
        total_wins = sum(t['profit'] for t in recent_trades if t['win'])
        total_losses = abs(sum(t['profit'] for t in recent_trades if not t['win']))
        profit_factor = total_wins / total_losses if total_losses > 0 else 2.0
        
        # 最大回撤
        cumulative = 0
        peak = 0
        max_drawdown = 0
        for trade in recent_trades:
            cumulative += trade['profit']
            if cumulative > peak:
                peak = cumulative
            drawdown = (cumulative - peak) / max(1, abs(peak)) if peak != 0 else 0
            if drawdown < max_drawdown:
                max_drawdown = drawdown
        
        # 平均盈利
        avg_profit = sum(t['profit'] for t in recent_trades) / len(recent_trades)
        
        return {
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'consecutive_wins': self.consecutive_wins,
            'consecutive_losses': self.consecutive_losses,
            'drawdown': max_drawdown,
            'avg_profit': avg_profit
        }
    
    def get_scheme_performance(self, scheme: str) -> Dict:
        """獲取特定方案的表現"""
        perf = self.scheme_performance[scheme]
        if perf['trades'] == 0:
            return {
                'trades': 0,
                'win_rate': 0.0,
                'avg_profit': 0.0,
                'total_profit': 0.0
            }
        
        return {
            'trades': perf['trades'],
            'win_rate': perf['wins'] / perf['trades'],
            'avg_profit': perf['total_profit'] / perf['trades'],
            'total_profit': perf['total_profit']
        }


class AdaptiveRiskController:
    """自適應風險控制器"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.emergency_conditions = self.config.get('emergency_conditions', {
            'vpin_threshold': 0.8,
            'spread_threshold': 25,
            'consecutive_losses': 3,
            'drawdown_threshold': -0.1,
            'volatility_threshold': 0.05
        })
    
    def check_emergency_conditions(self, market_data: Dict, performance: Dict) -> Tuple[bool, List[str]]:
        """
        檢查緊急情況
        
        Returns:
            (是否緊急, 觸發原因列表)
        """
        triggers = []
        
        # VPIN危機
        if market_data.get('vpin', 0) > self.emergency_conditions['vpin_threshold']:
            triggers.append(f"VPIN危機: {market_data.get('vpin', 0):.2f}")
        
        # 流動性危機
        if market_data.get('spread_bps', 0) > self.emergency_conditions['spread_threshold']:
            triggers.append(f"流動性危機: Spread {market_data.get('spread_bps', 0):.1f}bps")
        
        # 連續虧損
        if performance.get('consecutive_losses', 0) >= self.emergency_conditions['consecutive_losses']:
            triggers.append(f"連續虧損: {performance.get('consecutive_losses', 0)}次")
        
        # 回撤警報
        if performance.get('drawdown', 0) < self.emergency_conditions['drawdown_threshold']:
            triggers.append(f"回撤警報: {performance.get('drawdown', 0):.1%}")
        
        # 波動率激增
        volatility = market_data.get('volatility', 0)
        if volatility > self.emergency_conditions['volatility_threshold']:
            triggers.append(f"波動率激增: {volatility:.2%}")
        
        return len(triggers) > 0, triggers
    
    def calculate_risk_level(self, market_data: Dict, performance: Dict) -> str:
        """
        計算當前風險等級
        
        Returns:
            "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
        """
        risk_score = 0
        
        # VPIN風險
        vpin = market_data.get('vpin', 0.5)
        if vpin > 0.7:
            risk_score += 3
        elif vpin > 0.5:
            risk_score += 1
        
        # 流動性風險
        spread = market_data.get('spread_bps', 10)
        if spread > 20:
            risk_score += 2
        elif spread > 15:
            risk_score += 1
        
        # 表現風險
        if performance.get('consecutive_losses', 0) >= 2:
            risk_score += 2
        if performance.get('drawdown', 0) < -0.05:
            risk_score += 1
        
        # 波動率風險
        volatility = market_data.get('volatility', 0.02)
        if volatility > 0.04:
            risk_score += 2
        elif volatility > 0.03:
            risk_score += 1
        
        if risk_score >= 7:
            return "CRITICAL"
        elif risk_score >= 4:
            return "HIGH"
        elif risk_score >= 2:
            return "MEDIUM"
        else:
            return "LOW"


class MultiDimensionalSchemeManager:
    """多維度智能方案管理器 - M15 核心組件"""
    
    def __init__(self, config: Dict = None):
        self.current_scheme = "B"  # 默認從B方案開始
        self.last_switch_time = datetime.now()
        self.switch_history = deque(maxlen=20)
        self.min_scheme_duration = 600  # 10分鐘
        
        # 四個維度的評估器
        self.market_assessor = RealTimeMarketAssessor()
        self.performance_monitor = PerformanceMonitor()
        self.risk_controller = AdaptiveRiskController(config)
        self.predictive_engine = PredictiveEngine()
        
        # 切換策略配置
        self.switch_strategies = {
            "emergency": {"weight": 0.4, "immediate": True},    # 緊急情況
            "predictive": {"weight": 0.3, "immediate": True},   # 預測性
            "performance": {"weight": 0.2, "immediate": False}, # 表現驅動
            "market": {"weight": 0.1, "immediate": False}       # 市場狀態
        }
        
        logger.info("✅ 多維度智能方案管理器初始化完成")
    
    def update_market_data(self, market_data: Dict):
        """更新市場數據到預測引擎"""
        self.predictive_engine.update_data(
            price=market_data.get('price', 0),
            vpin=market_data.get('vpin', 0.5),
            obi=market_data.get('obi', 0)
        )
    
    def evaluate_scheme_switch(self, market_data: Dict, current_balance: float,
                               initial_balance: float) -> Dict:
        """
        多維度評估方案切換
        
        Returns:
            {
                'recommended_scheme': str,
                'confidence': float,
                'reason': str,
                'immediate_action': bool,
                'evaluations': Dict
            }
        """
        # 獲取當前表現指標
        performance = self.performance_monitor.get_performance_metrics()
        
        evaluations = {}
        
        # 1. 緊急情況評估（最高優先級）
        emergency_eval = self._evaluate_emergency_conditions(market_data, performance)
        evaluations["emergency"] = emergency_eval
        
        # 如果有緊急情況，立即返回
        if emergency_eval["recommended_scheme"] and emergency_eval["immediate_action"]:
            logger.warning(f"🚨 緊急切換觸發: {emergency_eval['reason']}")
            return {
                'recommended_scheme': emergency_eval["recommended_scheme"],
                'confidence': emergency_eval["confidence"],
                'reason': emergency_eval["reason"],
                'immediate_action': True,
                'evaluations': evaluations
            }
        
        # 2. 預測性評估
        predictive_eval = self._evaluate_predictive_switch(market_data)
        evaluations["predictive"] = predictive_eval
        
        # 如果預測到重要變化且需要立即行動
        if predictive_eval["recommended_scheme"] and predictive_eval["immediate_action"]:
            logger.info(f"🔮 預測性切換觸發: {predictive_eval['reason']}")
            return {
                'recommended_scheme': predictive_eval["recommended_scheme"],
                'confidence': predictive_eval["confidence"],
                'reason': predictive_eval["reason"],
                'immediate_action': True,
                'evaluations': evaluations
            }
        
        # 3. 表現驅動評估
        performance_eval = self._evaluate_performance_based_switch(performance)
        evaluations["performance"] = performance_eval
        
        # 4. 市場狀態評估
        market_eval = self._evaluate_market_based_switch(market_data)
        evaluations["market"] = market_eval
        
        # 綜合決策
        final_decision = self._make_final_decision(evaluations)
        
        return final_decision
    
    def _evaluate_emergency_conditions(self, market_data: Dict, performance: Dict) -> Dict:
        """緊急情況評估"""
        is_emergency, triggers = self.risk_controller.check_emergency_conditions(
            market_data, performance
        )
        
        if is_emergency:
            return {
                "recommended_scheme": "A",
                "confidence": 0.95,
                "reason": f"緊急情況: {', '.join(triggers)}",
                "immediate_action": True
            }
        
        return {
            "recommended_scheme": None,
            "confidence": 0,
            "reason": "無緊急情況",
            "immediate_action": False
        }
    
    def _evaluate_predictive_switch(self, market_data: Dict) -> Dict:
        """預測性切換評估"""
        # 市場趨勢預測
        trend_prediction = self.predictive_engine.predict_trend_change(market_data)
        volatility_prediction = self.predictive_engine.predict_volatility(market_data)
        
        # 預測市場惡化 → 提前降級
        if (trend_prediction.get('deteriorating', False) and 
            volatility_prediction.get('increasing', False)):
            confidence = min(
                trend_prediction.get('confidence', 0),
                volatility_prediction.get('confidence', 0)
            )
            return {
                "recommended_scheme": "A",
                "confidence": confidence,
                "reason": "預測市場惡化，提前降級保護資金",
                "immediate_action": True
            }
        
        # 預測市場好轉 → 提前升級
        elif (trend_prediction.get('improving', False) and 
              volatility_prediction.get('decreasing', False)):
            current_score = self.market_assessor.calculate_market_score(market_data)
            if current_score > 0.65:  # 確認當前市場已經比較好
                confidence = min(
                    trend_prediction.get('confidence', 0),
                    volatility_prediction.get('confidence', 0)
                )
                return {
                    "recommended_scheme": "C" if current_score > 0.75 else "B",
                    "confidence": confidence,
                    "reason": f"預測市場好轉(當前評分{current_score:.2f})，提前升級",
                    "immediate_action": True
                }
        
        return {
            "recommended_scheme": None,
            "confidence": 0,
            "reason": "無預測性信號",
            "immediate_action": False
        }
    
    def _evaluate_performance_based_switch(self, performance: Dict) -> Dict:
        """表現驅動切換"""
        consecutive_wins = performance.get('consecutive_wins', 0)
        consecutive_losses = performance.get('consecutive_losses', 0)
        win_rate = performance.get('win_rate', 0.5)
        profit_factor = performance.get('profit_factor', 1.0)
        
        # 升級條件：表現優秀
        if consecutive_wins >= 3 and win_rate > 0.7 and profit_factor > 1.5:
            return {
                "recommended_scheme": "C",
                "confidence": 0.85,
                "reason": f"表現優秀: {consecutive_wins}連勝, 勝率{win_rate:.1%}, 盈虧比{profit_factor:.2f}",
                "immediate_action": False
            }
        elif consecutive_wins >= 2 and win_rate > 0.65:
            return {
                "recommended_scheme": "B",
                "confidence": 0.75,
                "reason": f"表現良好: {consecutive_wins}連勝, 勝率{win_rate:.1%}",
                "immediate_action": False
            }
        
        # 降級條件：表現不佳
        if consecutive_losses >= 2 and win_rate < 0.4:
            return {
                "recommended_scheme": "A",
                "confidence": 0.8,
                "reason": f"表現不佳: {consecutive_losses}連敗, 勝率{win_rate:.1%}",
                "immediate_action": False
            }
        elif consecutive_losses >= 3:
            return {
                "recommended_scheme": "A",
                "confidence": 0.9,
                "reason": f"連續虧損: {consecutive_losses}連敗",
                "immediate_action": True  # 連續3敗立即降級
            }
        
        return {
            "recommended_scheme": None,
            "confidence": 0,
            "reason": "表現正常",
            "immediate_action": False
        }
    
    def _evaluate_market_based_switch(self, market_data: Dict) -> Dict:
        """市場狀態驅動切換"""
        market_score = self.market_assessor.calculate_market_score(market_data)
        score_trend = self.market_assessor.get_score_trend()
        
        # 市場條件極佳 → 升級
        if market_score >= 0.8:
            return {
                "recommended_scheme": "C",
                "confidence": market_score,
                "reason": f"市場條件極佳: 評分{market_score:.2f}, 趨勢{score_trend}",
                "immediate_action": False
            }
        elif market_score >= 0.65 and score_trend == "improving":
            return {
                "recommended_scheme": "B",
                "confidence": market_score * 0.9,
                "reason": f"市場條件良好: 評分{market_score:.2f}, 趨勢改善中",
                "immediate_action": False
            }
        
        # 市場條件差 → 降級
        elif market_score <= 0.4:
            return {
                "recommended_scheme": "A",
                "confidence": 1 - market_score,
                "reason": f"市場條件差: 評分{market_score:.2f}, 趨勢{score_trend}",
                "immediate_action": False
            }
        elif market_score <= 0.5 and score_trend == "deteriorating":
            return {
                "recommended_scheme": "A",
                "confidence": 0.75,
                "reason": f"市場條件惡化: 評分{market_score:.2f}, 趨勢惡化中",
                "immediate_action": True  # 惡化趨勢立即降級
            }
        
        return {
            "recommended_scheme": None,
            "confidence": 0,
            "reason": "市場條件正常",
            "immediate_action": False
        }
    
    def _make_final_decision(self, evaluations: Dict) -> Dict:
        """
        綜合決策 - 加權投票
        
        決策優先級：
        1. 緊急情況 (已在外層處理)
        2. 預測性切換 (已在外層處理)
        3. 加權投票決策
        """
        # 加權投票
        scheme_votes = {"A": 0.0, "B": 0.0, "C": 0.0}
        confidence_scores = {"A": 0.0, "B": 0.0, "C": 0.0}
        reasons = []
        
        for strategy_name, evaluation in evaluations.items():
            recommended = evaluation.get("recommended_scheme")
            if recommended:
                weight = self.switch_strategies[strategy_name]["weight"]
                confidence = evaluation.get("confidence", 0)
                
                scheme_votes[recommended] += weight
                confidence_scores[recommended] += confidence * weight
                reasons.append(f"{strategy_name}: {evaluation['reason']}")
        
        # 選擇得分最高的方案
        best_scheme = max(scheme_votes, key=scheme_votes.get)
        best_score = scheme_votes[best_scheme]
        
        # 達到切換閾值
        if best_score >= 0.5:
            return {
                "recommended_scheme": best_scheme,
                "confidence": confidence_scores[best_scheme],
                "reason": f"綜合評估得分: {best_score:.2f} - " + "; ".join(reasons[:2]),
                "immediate_action": False,
                "evaluations": evaluations
            }
        
        # 未達到切換閾值
        return {
            "recommended_scheme": None,
            "confidence": 0,
            "reason": f"未達到切換閾值(當前最高分{best_score:.2f})",
            "immediate_action": False,
            "evaluations": evaluations
        }
    
    def should_switch_scheme(self, recommended_scheme: str, immediate: bool = False) -> bool:
        """
        判斷是否應該切換方案
        
        考慮因素：
        1. 方案是否改變
        2. 方案持續時間（避免頻繁切換）
        3. 是否緊急情況（可跳過時間限制）
        """
        # 方案未改變
        if recommended_scheme == self.current_scheme:
            return False
        
        # 緊急情況可立即切換
        if immediate:
            logger.info(f"⚡ 緊急切換: {self.current_scheme} → {recommended_scheme}")
            return True
        
        # 檢查方案持續時間
        time_in_scheme = (datetime.now() - self.last_switch_time).total_seconds()
        if time_in_scheme < self.min_scheme_duration:
            logger.debug(
                f"⏸️ 方案持續時間不足 ({time_in_scheme:.0f}s < {self.min_scheme_duration}s)，暫不切換"
            )
            return False
        
        return True
    
    def execute_scheme_switch(self, new_scheme: str, reason: str):
        """執行方案切換"""
        old_scheme = self.current_scheme
        self.current_scheme = new_scheme
        self.last_switch_time = datetime.now()
        
        # 記錄切換歷史
        self.switch_history.append({
            'timestamp': datetime.now(),
            'from': old_scheme,
            'to': new_scheme,
            'reason': reason
        })
        
        logger.info(f"🔄 方案切換執行: {old_scheme} → {new_scheme} | 原因: {reason}")
    
    def add_trade_result(self, profit: float, scheme: str, entry_time: datetime):
        """添加交易結果"""
        self.performance_monitor.add_trade(profit, scheme, entry_time)
    
    def get_statistics(self) -> Dict:
        """獲取統計信息"""
        return {
            'current_scheme': self.current_scheme,
            'time_in_scheme': (datetime.now() - self.last_switch_time).total_seconds() / 60,
            'switch_count': len(self.switch_history),
            'performance': self.performance_monitor.get_performance_metrics(),
            'scheme_performance': {
                scheme: self.performance_monitor.get_scheme_performance(scheme)
                for scheme in ["A", "B", "C"]
            },
            'recent_switches': [
                {
                    'time': sw['timestamp'].strftime('%H:%M:%S'),
                    'switch': f"{sw['from']}→{sw['to']}",
                    'reason': sw['reason']
                }
                for sw in list(self.switch_history)[-5:]
            ]
        }


class Mode15EnhancedStrategy(Mode14Strategy):
    """
    M15 增強策略 - 繼承自 M14 Mode14Strategy
    增加多維度智能方案切換功能
    """
    
    def __init__(self, config: Dict):
        # 調用父類初始化
        super().__init__(config)
        
        # 添加基本屬性 (兼容 strategy_manager)
        self.name = config.get('name', 'M15 Enhanced')
        self.emoji = config.get('emoji', '🤖🐳🦾')
        self.description = config.get('description', 'Multi-Dimensional Intelligent Scheme Switching')
        self.leverage = config.get('base_leverage', 20)
        self.position_size = config.get('max_position_size', 0.5)
        self.enabled = config.get('enabled', True)
        self.risk_control = config.get('risk_control', {})
        
        # M15 專用：多維度方案管理器
        self.scheme_manager = MultiDimensionalSchemeManager(config)
        
        # M15 新增：緊急熔斷機制 🔴
        self.circuit_breaker = EmergencyCircuitBreaker()
        
        # M15 新增：平滑過渡管理器 🔄
        self.transition_manager = SmoothTransitionManager()
        
        # M15 新增：極端市場處理器 ⚠️
        self.extreme_handler = ExtremeMarketHandler(config)
        
        # M15 新增：增強性能監控 📊
        self.performance_monitor = EnhancedPerformanceMonitor()
        
        logger.info("✅ M15 多維度智能方案切換策略初始化完成")
        logger.info("   🔴 熔斷機制已啟動")
        logger.info("   🔄 平滑過渡管理器已就緒")
        logger.info("   ⚠️ 極端市場處理器已啟動")
        logger.info("   📊 增強性能監控已開啟")
    
    def update_scheme_dynamic(self, market_data: Dict, current_balance: float,
                             initial_balance: float) -> str:
        """
        動態更新交易方案（M15核心功能）
        增強版：包含平滑過渡、極端市場處理
        
        Returns:
            str: 當前方案 ("A", "B", or "C")
        """
        # 更新市場數據
        self.scheme_manager.update_market_data(market_data)
        
        # 檢查極端市場條件（優先級最高）
        extreme_action, extreme_reason = self.extreme_handler.handle_extreme_conditions(
            market_data, self.scheme_manager.current_scheme
        )
        
        if extreme_action and extreme_action != "PAUSE":
            # 極端市場強制切換
            if extreme_action != self.scheme_manager.current_scheme:
                logger.error(f"⚠️ 極端市場強制切換: {self.scheme_manager.current_scheme} → {extreme_action}")
                logger.error(f"   原因: {extreme_reason}")
                self.scheme_manager.execute_scheme_switch(extreme_action, extreme_reason)
                self.strategy_selector.update_scheme(extreme_action)
                return extreme_action
        
        # 多維度評估方案切換
        decision = self.scheme_manager.evaluate_scheme_switch(
            market_data=market_data,
            current_balance=current_balance,
            initial_balance=initial_balance
        )
        
        # 判斷是否應該切換
        recommended_scheme = decision.get('recommended_scheme')
        if recommended_scheme:
            immediate = decision.get('immediate_action', False)
            if self.scheme_manager.should_switch_scheme(recommended_scheme, immediate):
                
                # 使用平滑過渡管理器
                from_scheme = self.scheme_manager.current_scheme
                actual_scheme, in_transition = self.transition_manager.manage_transition(
                    from_scheme, recommended_scheme
                )
                
                if actual_scheme != from_scheme:
                    self.scheme_manager.execute_scheme_switch(
                        actual_scheme,
                        decision.get('reason', '')
                    )
                    # 更新策略選擇器的方案
                    self.strategy_selector.update_scheme(actual_scheme)
                    
                    # 顯示過渡狀態
                    if in_transition:
                        transition_status = self.transition_manager.get_transition_status()
                        logger.info(f"🔄 過渡進度: {transition_status['progress']:.0%} "
                                  f"(剩餘 {transition_status['remaining_minutes']:.1f} 分鐘)")
        
        return self.scheme_manager.current_scheme
    
    def record_trade_result(self, profit: float, entry_time: datetime, current_balance: float = None):
        """
        記錄交易結果
        增強版：包含熔斷檢查、性能監控、平滑過渡
        
        Args:
            profit: 交易盈虧
            entry_time: 進場時間
            current_balance: 當前餘額（用於熔斷檢查）
        """
        current_scheme = self.scheme_manager.current_scheme
        
        # 記錄到方案管理器
        self.scheme_manager.add_trade_result(profit, current_scheme, entry_time)
        
        # 記錄到父類
        super().record_trade_result(profit, entry_time)
        
        # 記錄到性能監控器
        trade_result = {
            'profit': profit,
            'time': entry_time,
            'scheme': current_scheme
        }
        self.performance_monitor.add_trade(trade_result)
        
        # 檢查熔斷條件
        if current_balance is not None:
            circuit_ok = self.circuit_breaker.check_circuit_breaker(trade_result, current_balance)
            if not circuit_ok:
                logger.error("🔴🔴🔴 熔斷機制已觸發！交易已暫停 🔴🔴🔴")
                logger.error(f"   原因: {self.circuit_breaker.halt_reason}")
                logger.error(f"   當前餘額: {current_balance:.2f} USDT")
                logger.error(f"   需要手動重置才能恢復交易")
        
        # 記錄交易統計
        if profit > 0:
            logger.info(f"✅ 盈利交易: +{profit:.4f} | 方案: {current_scheme}")
        else:
            logger.warning(f"❌ 虧損交易: {profit:.4f} | 方案: {current_scheme}")
            
        # 獲取性能摘要
        perf_summary = self.performance_monitor.get_performance_summary()
        logger.debug(f"📊 性能摘要: 勝率={perf_summary['win_rate']:.1%}, "
                    f"回撤={perf_summary['drawdown']:.1%}, "
                    f"連續虧損={perf_summary['consecutive_losses']}")
    
    def get_enhanced_statistics(self) -> Dict:
        """獲取增強版統計信息"""
        base_stats = self.get_strategy_statistics()
        enhanced_stats = self.scheme_manager.get_statistics()
        
        return {
            **base_stats,
            'enhanced': enhanced_stats
        }
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """
        檢查是否可以進場 (兼容 strategy_manager 接口)
        增強版：包含熔斷、極端市場、性能預警檢查
        
        Args:
            market_data: 市場數據
            signal: 交易信號
            
        Returns:
            Tuple[bool, List[str]]: (是否可以進場, 阻擋原因列表)
        """
        blocked_reasons = []
        
        # 1️⃣ 檢查熔斷器
        can_trade, halt_reason = self.circuit_breaker.can_trade()
        if not can_trade:
            logger.error(f"🔴 熔斷機制觸發: {halt_reason}")
            return False, [f"交易暫停：熔斷機制 - {halt_reason}"]
        
        # 2️⃣ 檢查極端市場條件
        extreme_action, extreme_reason = self.extreme_handler.handle_extreme_conditions(
            market_data, self.scheme_manager.current_scheme
        )
        
        if extreme_action == "PAUSE":
            logger.error(f"⚠️ 極端市場暫停: {extreme_reason}")
            return False, [f"極端市場：{extreme_reason}"]
        elif extreme_action == "A":
            # 強制降級到A方案
            if self.scheme_manager.current_scheme != "A":
                logger.warning(f"⚠️ 強制降級到方案A: {extreme_reason}")
                self.scheme_manager.current_scheme = "A"
                self.strategy_selector.update_scheme("A")
        elif extreme_action == "B":
            # 強制降級到B方案
            if self.scheme_manager.current_scheme == "C":
                logger.warning(f"⚠️ 強制降級到方案B: {extreme_reason}")
                self.scheme_manager.current_scheme = "B"
                self.strategy_selector.update_scheme("B")
        
        # 3️⃣ 檢查性能預警
        alerts = self.performance_monitor.check_performance_alerts(market_data)
        
        critical_alerts = [a for a in alerts if a['level'] == 'CRITICAL']
        if critical_alerts:
            for alert in critical_alerts:
                logger.error(f"🚨 嚴重預警: {alert['message']}")
            # 嚴重預警不阻擋交易，但強制降級
            if self.scheme_manager.current_scheme == "C":
                logger.warning("🚨 嚴重預警觸發，強制降級到B方案")
                self.scheme_manager.current_scheme = "B"
                self.strategy_selector.update_scheme("B")
        
        warning_alerts = [a for a in alerts if a['level'] == 'WARNING']
        if warning_alerts:
            for alert in warning_alerts:
                logger.warning(f"⚠️ 預警: {alert['message']}")
        
        # 4️⃣ 調用父類的 should_enter_trade 方法
        # 確保 market_data 包含必要的鍵（兼容 paper_trading_system）
        market_data_copy = market_data.copy()
        
        # 添加缺失的鍵（映射 paper_trading_system 的鍵名到 M14 期望的鍵名）
        if 'volume' not in market_data_copy:
            market_data_copy['volume'] = market_data_copy.get('signed_volume', 0)
        
        if 'avg_volume' not in market_data_copy:
            market_data_copy['avg_volume'] = 1.0
        
        # 關鍵修復：確保 spread 鍵存在（M14 必需）
        if 'spread' not in market_data_copy and 'spread_bps' in market_data_copy:
            # spread_bps -> spread (bps 保持一致)
            market_data_copy['spread'] = market_data_copy['spread_bps']
        elif 'spread' not in market_data_copy:
            # 如果兩者都沒有，使用默認值
            market_data_copy['spread'] = 10.0
        
        # 關鍵修復：確保 depth 鍵存在（M14 必需）
        if 'depth' not in market_data_copy and 'total_depth' in market_data_copy:
            # total_depth -> depth
            market_data_copy['depth'] = market_data_copy['total_depth']
        elif 'depth' not in market_data_copy:
            # 如果兩者都沒有，使用默認值
            market_data_copy['depth'] = 5.0
        
        can_enter, reason = self.should_enter_trade(market_data_copy)
        
        if can_enter:
            return True, []
        else:
            return False, [reason]
    
    def initialize_session(self, initial_balance: float):
        """
        初始化交易會話
        重置熔斷器和性能監控
        
        Args:
            initial_balance: 初始餘額
        """
        self.circuit_breaker.reset_session(initial_balance)
        logger.info(f"🔄 M15 交易會話已初始化")
        logger.info(f"   💰 初始餘額: {initial_balance:.2f} USDT")
        logger.info(f"   🔴 熔斷設定: 連續虧損{self.circuit_breaker.max_consecutive_losses}次 或 單日虧損{self.circuit_breaker.daily_loss_limit:.0%}")
    
    def get_comprehensive_statistics(self) -> Dict:
        """
        獲取完整的統計信息
        包含：基礎統計、方案統計、性能監控、過渡狀態、熔斷狀態
        
        Returns:
            Dict: 完整統計信息
        """
        base_stats = self.get_strategy_statistics()
        enhanced_stats = self.scheme_manager.get_statistics()
        performance = self.performance_monitor.get_performance_summary()
        transition = self.transition_manager.get_transition_status()
        
        # 熔斷狀態
        can_trade, halt_reason = self.circuit_breaker.can_trade()
        circuit_breaker_status = {
            'active': not can_trade,
            'reason': halt_reason,
            'consecutive_losses': self.circuit_breaker.consecutive_losses
        }
        
        # 市場風險等級（需要最新市場數據，這裡返回None）
        market_risk = "UNKNOWN"
        
        return {
            'base_statistics': base_stats,
            'scheme_statistics': enhanced_stats,
            'performance_monitor': performance,
            'transition_status': transition,
            'circuit_breaker': circuit_breaker_status,
            'market_risk_level': market_risk,
            'current_scheme': self.scheme_manager.current_scheme,
            'switch_history': self.scheme_manager.switch_history[-10:] if hasattr(self.scheme_manager, 'switch_history') else []
        }
    
    def manual_reset_circuit_breaker(self):
        """手動重置熔斷機制（管理員操作）"""
        self.circuit_breaker.manual_reset()
        logger.warning("⚠️ 管理員手動重置熔斷機制")
    
    def get_risk_summary(self, market_data: Dict) -> Dict:
        """
        獲取風險摘要
        
        Args:
            market_data: 當前市場數據
            
        Returns:
            Dict: 風險摘要信息
        """
        # 市場風險等級
        market_risk = self.extreme_handler.get_market_risk_level(market_data)
        
        # 性能預警
        alerts = self.performance_monitor.check_performance_alerts(market_data)
        
        # 熔斷狀態
        can_trade, halt_reason = self.circuit_breaker.can_trade()
        
        # 過渡狀態
        transition_status = self.transition_manager.get_transition_status()
        
        return {
            'market_risk_level': market_risk,
            'can_trade': can_trade,
            'halt_reason': halt_reason,
            'active_alerts': alerts,
            'in_transition': transition_status['in_transition'],
            'current_scheme': self.scheme_manager.current_scheme,
            'performance': self.performance_monitor.get_performance_summary()
        }
