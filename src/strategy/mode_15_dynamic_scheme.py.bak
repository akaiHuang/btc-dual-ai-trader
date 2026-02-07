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
    TradingScheme
)

logger = logging.getLogger(__name__)


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
    """智能方案選擇器 - M15 專用"""
    
    def __init__(self):
        self.current_scheme = "B"  # 默認從B方案開始
        self.trade_history = []
        self.scheme_start_time = datetime.now()
        self.scheme_performance = {
            "A": {"trades": 0, "wins": 0, "total_profit": 0.0},
            "B": {"trades": 0, "wins": 0, "total_profit": 0.0},
            "C": {"trades": 0, "wins": 0, "total_profit": 0.0}
        }
        self.market_condition_history = []  # 記錄市場狀態
        
    def evaluate_market_conditions(self, market_data: Dict, market_regime: str) -> Dict[str, float]:
        """
        評估市場條件的各項指標
        
        Returns:
            Dict: {
                'volatility_score': 0-1 (越低越好),
                'liquidity_score': 0-1 (越高越好),
                'trend_score': 0-1 (越高越好),
                'toxicity_score': 0-1 (越低越好)
            }
        """
        # 波動率評分 (低波動=高分)
        vpin = market_data.get('vpin', 0.5)
        volatility_score = max(0, 1 - (vpin / 0.8))
        
        # 流動性評分
        spread_bps = market_data.get('spread_bps', 10)
        depth = market_data.get('total_depth', 3)
        liquidity_score = min(1.0, (1 / max(1, spread_bps / 5)) * (depth / 5))
        
        # 趨勢評分
        trend_score = 1.0 if market_regime == "TRENDING" else 0.5 if market_regime == "NEUTRAL" else 0.2
        
        # 毒性評分 (低VPIN=高分)
        toxicity_score = max(0, 1 - vpin)
        
        return {
            'volatility_score': volatility_score,
            'liquidity_score': liquidity_score,
            'trend_score': trend_score,
            'toxicity_score': toxicity_score
        }
    
    def calculate_market_favorability(self, market_scores: Dict[str, float]) -> float:
        """
        計算市場有利度 (0-1)
        
        權重分配：
        - 毒性評分 40%
        - 流動性評分 30%
        - 趨勢評分 20%
        - 波動率評分 10%
        """
        favorability = (
            market_scores['toxicity_score'] * 0.4 +
            market_scores['liquidity_score'] * 0.3 +
            market_scores['trend_score'] * 0.2 +
            market_scores['volatility_score'] * 0.1
        )
        
        return favorability
    
    def evaluate_trading_performance(self, scheme: str = None) -> Dict[str, float]:
        """
        評估交易表現
        
        Args:
            scheme: 評估特定方案，None則評估當前整體表現
        
        Returns:
            Dict: {
                'win_rate': 勝率,
                'profit_factor': 盈虧比,
                'avg_profit': 平均盈利,
                'consistency': 穩定性 (0-1)
            }
        """
        if not self.trade_history:
            return {
                'win_rate': 0.5,
                'profit_factor': 1.0,
                'avg_profit': 0.0,
                'consistency': 0.5
            }
        
        # 選擇要評估的交易
        if scheme:
            trades = [t for t in self.trade_history if t.get('scheme') == scheme]
        else:
            trades = self.trade_history[-20:]  # 最近20筆交易
        
        if not trades:
            return {
                'win_rate': 0.5,
                'profit_factor': 1.0,
                'avg_profit': 0.0,
                'consistency': 0.5
            }
        
        # 計算勝率
        winning_trades = [t for t in trades if t['profit'] > 0]
        win_rate = len(winning_trades) / len(trades)
        
        # 計算盈虧比
        total_wins = sum(t['profit'] for t in winning_trades)
        losing_trades = [t for t in trades if t['profit'] < 0]
        total_losses = abs(sum(t['profit'] for t in losing_trades))
        profit_factor = total_wins / total_losses if total_losses > 0 else 2.0
        
        # 平均盈利
        avg_profit = sum(t['profit'] for t in trades) / len(trades)
        
        # 穩定性 (標準差的倒數)
        profits = [t['profit'] for t in trades]
        std_dev = np.std(profits) if len(profits) > 1 else 0.01
        consistency = 1 / (1 + std_dev * 100)  # 標準差越小，穩定性越高
        
        return {
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_profit': avg_profit,
            'consistency': consistency
        }
    
    def calculate_scheme_suitability(self, scheme: str, market_scores: Dict[str, float],
                                    performance: Dict[str, float]) -> float:
        """
        計算方案適配度 (0-1)
        
        Args:
            scheme: "A", "B", or "C"
            market_scores: 市場條件評分
            performance: 交易表現評估
        
        Returns:
            float: 適配度分數
        """
        market_favorability = self.calculate_market_favorability(market_scores)
        
        # 方案特性
        scheme_profiles = {
            "A": {
                'risk_tolerance': 0.3,  # 低風險容忍
                'required_favorability': 0.5,  # 需要中等有利市場
                'performance_weight': 0.6  # 重視表現
            },
            "B": {
                'risk_tolerance': 0.5,  # 中等風險容忍
                'required_favorability': 0.4,  # 市場要求較低
                'performance_weight': 0.5
            },
            "C": {
                'risk_tolerance': 0.7,  # 高風險容忍
                'required_favorability': 0.7,  # 需要非常有利市場
                'performance_weight': 0.4  # 較少依賴歷史表現
            }
        }
        
        profile = scheme_profiles[scheme]
        
        # 市場條件適配度
        market_fit = 1.0
        if market_favorability < profile['required_favorability']:
            market_fit = market_favorability / profile['required_favorability']
        
        # 表現適配度
        performance_fit = (
            performance['win_rate'] * 0.4 +
            min(1.0, performance['profit_factor'] / 2.0) * 0.3 +
            performance['consistency'] * 0.3
        )
        
        # 風險調整
        risk_adjustment = 1.0
        if market_scores['toxicity_score'] < profile['risk_tolerance']:
            risk_adjustment = market_scores['toxicity_score'] / profile['risk_tolerance']
        
        # 綜合適配度
        suitability = (
            market_fit * (1 - profile['performance_weight']) +
            performance_fit * profile['performance_weight']
        ) * risk_adjustment
        
        logger.debug(f"方案{scheme}適配度: 市場適配={market_fit:.2f}, 表現適配={performance_fit:.2f}, "
                    f"風險調整={risk_adjustment:.2f}, 總分={suitability:.2f}")
        
        return suitability
    
    def select_optimal_scheme_dynamic(self, market_data: Dict, market_regime: str,
                                     current_balance: float, initial_balance: float) -> str:
        """
        動態選擇最優方案
        
        Returns:
            str: "A", "B", or "C"
        """
        # 評估市場條件
        market_scores = self.evaluate_market_conditions(market_data, market_regime)
        
        # 評估整體交易表現
        overall_performance = self.evaluate_trading_performance()
        
        # 評估各方案表現
        scheme_performances = {
            scheme: self.evaluate_trading_performance(scheme)
            for scheme in ["A", "B", "C"]
        }
        
        # 計算各方案適配度
        suitabilities = {}
        for scheme in ["A", "B", "C"]:
            # 如果該方案有歷史數據，使用該方案的表現；否則使用整體表現
            perf = scheme_performances[scheme] if self.scheme_performance[scheme]['trades'] >= 3 else overall_performance
            suitabilities[scheme] = self.calculate_scheme_suitability(scheme, market_scores, perf)
        
        # 賬戶狀態調整
        profit_ratio = (current_balance - initial_balance) / initial_balance
        if profit_ratio < -0.15:  # 虧損超過15%，強制保守
            suitabilities["A"] *= 1.5
            suitabilities["C"] *= 0.5
            logger.warning(f"⚠️ 賬戶虧損{profit_ratio:.1%}，增加方案A權重")
        elif profit_ratio > 0.3:  # 盈利超過30%，可以積極
            suitabilities["C"] *= 1.3
            logger.info(f"💰 賬戶盈利{profit_ratio:.1%}，增加方案C權重")
        
        # 連續虧損檢查
        if len(self.trade_history) >= 3:
            recent_losses = sum(1 for t in self.trade_history[-3:] if t['profit'] < 0)
            if recent_losses >= 2:
                suitabilities["A"] *= 1.4
                suitabilities["C"] *= 0.6
                logger.warning(f"⚠️ 近期虧損{recent_losses}/3，增加方案A權重")
        
        # 選擇適配度最高的方案
        optimal_scheme = max(suitabilities, key=suitabilities.get)
        
        logger.info(f"📊 方案適配度評分: A={suitabilities['A']:.3f}, B={suitabilities['B']:.3f}, "
                   f"C={suitabilities['C']:.3f} → 選擇方案{optimal_scheme}")
        
        return optimal_scheme
    
    def should_switch_scheme(self, new_scheme: str, current_vpin: float) -> bool:
        """
        判斷是否應該切換方案
        
        考慮因素：
        1. 方案持續時間（避免頻繁切換）
        2. 當前市場狀態（極端市場禁止升級）
        3. 方案間差異（是否值得切換）
        """
        # 方案未改變
        if new_scheme == self.current_scheme:
            return False
        
        # 檢查方案持續時間（至少10分鐘）
        time_in_scheme = (datetime.now() - self.scheme_start_time).total_seconds() / 60
        if time_in_scheme < 10:
            logger.debug(f"⏸️ 方案持續時間不足10分鐘 ({time_in_scheme:.1f}min)，暫不切換")
            return False
        
        # 極端市場禁止升級到C方案
        if new_scheme == "C" and current_vpin > 0.7:
            logger.warning(f"⚠️ VPIN過高({current_vpin:.2f})，禁止升級到方案C")
            return False
        
        # 允許切換
        return True
    
    def update_scheme(self, new_scheme: str):
        """更新當前方案"""
        if new_scheme != self.current_scheme:
            old_scheme = self.current_scheme
            self.current_scheme = new_scheme
            self.scheme_start_time = datetime.now()
            logger.info(f"🔄 動態方案切換: {old_scheme} → {new_scheme}")
    
    def add_trade_result(self, profit: float, entry_time: datetime, scheme: str):
        """添加交易結果"""
        self.trade_history.append({
            'profit': profit,
            'time': entry_time,
            'scheme': scheme
        })
        
        # 更新方案表現統計
        self.scheme_performance[scheme]['trades'] += 1
        if profit > 0:
            self.scheme_performance[scheme]['wins'] += 1
        self.scheme_performance[scheme]['total_profit'] += profit
        
        # 只保留最近100次交易
        if len(self.trade_history) > 100:
            old_trade = self.trade_history.pop(0)
            # 從統計中移除
            old_scheme = old_trade['scheme']
            self.scheme_performance[old_scheme]['trades'] -= 1
            if old_trade['profit'] > 0:
                self.scheme_performance[old_scheme]['wins'] -= 1
            self.scheme_performance[old_scheme]['total_profit'] -= old_trade['profit']
    
    def get_scheme_statistics(self) -> Dict:
        """獲取方案統計信息"""
        stats = {}
        for scheme, perf in self.scheme_performance.items():
            if perf['trades'] > 0:
                stats[scheme] = {
                    'trades': perf['trades'],
                    'win_rate': perf['wins'] / perf['trades'],
                    'avg_profit': perf['total_profit'] / perf['trades'],
                    'total_profit': perf['total_profit']
                }
            else:
                stats[scheme] = {
                    'trades': 0,
                    'win_rate': 0.0,
                    'avg_profit': 0.0,
                    'total_profit': 0.0
                }
        return stats


class Mode15Strategy:
    """M15策略主引擎 - 動態方案決策版本"""
    
    def __init__(self, config: Dict):
        self.config = config
        
        # 初始化各組件
        self.market_detector = MarketRegimeDetector()
        self.signal_scorer = SignalQualityScorer()
        self.cost_calculator = CostAwareProfitCalculator()
        self.leverage_adjuster = DynamicLeverageAdjuster(
            base_leverage=config.get('base_leverage', 20)
        )
        self.position_sizer = DynamicPositionSizer(
            base_size=config.get('max_position_size', 0.5)
        )
        self.tpsl_adjuster = DynamicTPSLAdjuster()
        
        # M15 專用：智能方案選擇器
        self.strategy_selector = IntelligentSchemeSelector()
        
        # 狀態變量
        self.current_leverage = config.get('base_leverage', 20)
        self.current_position_size = config.get('max_position_size', 0.5)
        self.current_tp = 0.002
        self.current_sl = 0.001
        
        logger.info("✅ M15 動態方案決策策略初始化完成")
    
    def should_enter_trade(self, market_data: Dict) -> Tuple[bool, str]:
        """
        判斷是否應該進場（與M14相同）
        
        Args:
            market_data: {
                'vpin': float,
                'spread': float,
                'depth': float,
                'obi': float,
                'volume': float,
                'avg_volume': float,
                'price': float,
                'mtf_signals': Dict[str, float]
            }
        
        Returns:
            Tuple[bool, str]: (是否進場, 原因)
        """
        # 更新數據
        self.market_detector.update_price(market_data['price'])
        self.signal_scorer.update_data(market_data['volume'], market_data['price'])
        
        # 檢測市場狀態
        market_regime = self.market_detector.detect_regime()
        
        # 計算信號質量
        signal_score = self.signal_scorer.score_signal(
            obi_data={'current': market_data['obi']},
            volume_data={'current': market_data['volume'], 'average': market_data['avg_volume']},
            mtf_signals=market_data.get('mtf_signals', {})
        )
        
        # 多重過濾條件（8選7機制）
        conditions = {}
        
        # 核心風控
        conditions['vpin_safe'] = market_data['vpin'] < self.config['risk_control']['vpin_threshold']
        conditions['spread_ok'] = market_data['spread'] < self.config['risk_control']['spread_threshold']
        conditions['depth_ok'] = market_data['depth'] > self.config['risk_control']['depth_threshold']
        
        # 信號質量
        conditions['strong_signal'] = abs(market_data['obi']) > 0.6
        conditions['signal_quality'] = signal_score > 0.7
        conditions['volume_confirmation'] = (market_data['volume'] / market_data['avg_volume']) > 1.2
        
        # 趨勢確認
        conditions['trend_aligned'] = market_regime in ["TRENDING", "NEUTRAL"]
        
        # 盈利預期
        expected_move = 0.002
        conditions['profitable_after_costs'] = self.cost_calculator.is_trade_profitable(
            expected_move=expected_move,
            leverage=self.current_leverage,
            position_size=self.current_position_size
        )
        
        # 統計滿足的條件數
        met_conditions = sum(conditions.values())
        total_conditions = len(conditions)
        
        # 記錄條件檢查結果
        failed_conditions = [k for k, v in conditions.items() if not v]
        if failed_conditions:
            logger.debug(f"❌ 未滿足條件: {', '.join(failed_conditions)}")
        
        # 需要至少7/8條件滿足
        should_enter = met_conditions >= 7
        
        reason = f"條件滿足 {met_conditions}/{total_conditions}, 信號評分 {signal_score:.2f}, 市場狀態 {market_regime}"
        
        if should_enter:
            logger.info(f"✅ 進場信號: {reason}")
        else:
            logger.debug(f"⏸️ 不進場: {reason}")
        
        return should_enter, reason
    
    def calculate_trade_parameters(self, market_data: Dict, signal_duration: int = 0) -> Dict:
        """
        計算交易參數
        
        Returns:
            Dict: {
                'leverage': float,
                'position_size': float,
                'take_profit': float,
                'stop_loss': float,
                'market_regime': str,
                'signal_score': float,
                'current_scheme': str
            }
        """
        # 檢測市場狀態
        market_regime = self.market_detector.detect_regime()
        
        # 計算波動率
        volatility = self.market_detector.calculate_volatility()
        
        # 計算信號質量
        signal_score = self.signal_scorer.score_signal(
            obi_data={'current': market_data['obi']},
            volume_data={'current': market_data['volume'], 'average': market_data['avg_volume']},
            mtf_signals=market_data.get('mtf_signals', {})
        )
        
        # 調整槓桿
        self.current_leverage = self.leverage_adjuster.adjust_leverage(
            current_vpin=market_data['vpin'],
            volatility=volatility,
            signal_strength=signal_score
        )
        
        # 調整倉位
        self.current_position_size = self.position_sizer.adjust_position_size(
            leverage=self.current_leverage,
            confidence=signal_score,
            market_regime=market_regime
        )
        
        # 調整止盈止損
        self.current_tp, self.current_sl = self.tpsl_adjuster.adjust_tp_sl(
            leverage=self.current_leverage,
            volatility=volatility,
            signal_duration=signal_duration
        )
        
        return {
            'leverage': self.current_leverage,
            'position_size': self.current_position_size,
            'take_profit': self.current_tp,
            'stop_loss': self.current_sl,
            'market_regime': market_regime,
            'signal_score': signal_score,
            'current_scheme': self.strategy_selector.current_scheme
        }
    
    def update_scheme_if_needed(self, market_data: Dict, market_regime: str,
                                current_balance: float, initial_balance: float) -> str:
        """
        動態更新交易方案
        
        Returns:
            str: 當前方案 ("A", "B", or "C")
        """
        # 使用智能選擇器動態選擇最優方案
        optimal_scheme = self.strategy_selector.select_optimal_scheme_dynamic(
            market_data=market_data,
            market_regime=market_regime,
            current_balance=current_balance,
            initial_balance=initial_balance
        )
        
        # 檢查是否應該切換方案
        current_vpin = market_data.get('vpin', 0.5)
        if self.strategy_selector.should_switch_scheme(optimal_scheme, current_vpin):
            self.strategy_selector.update_scheme(optimal_scheme)
        
        return self.strategy_selector.current_scheme
    
    def get_current_scheme_config(self) -> Dict:
        """獲取當前方案配置"""
        return TradingScheme.get_scheme(self.strategy_selector.current_scheme)
    
    def record_trade_result(self, profit: float, entry_time: datetime):
        """記錄交易結果"""
        self.strategy_selector.add_trade_result(
            profit=profit,
            entry_time=entry_time,
            scheme=self.strategy_selector.current_scheme
        )
    
    def get_strategy_statistics(self) -> Dict:
        """獲取策略統計信息"""
        return self.strategy_selector.get_scheme_statistics()
