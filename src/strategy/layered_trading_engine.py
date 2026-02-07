"""
Layered Trading Engine - Task 1.6.1 Phase C

Purpose:
    整合三層決策架構的完整交易引擎。
    
Architecture:
    Layer 1 (Signal) → Layer 2 (Regime) → Layer 3 (Execution)
    
Flow:
    1. Signal Generator: 生成多空信號與信心度
    2. Regime Filter: 檢查市場風險，決定是否可交易
    3. Execution Engine: 決定倉位、槓桿、止損止盈

Integration:
    整合所有微觀結構指標：
    - OBI (Order Book Imbalance)
    - OBI Velocity
    - Signed Volume
    - Microprice Pressure
    - VPIN (Toxicity)
    - Spread & Depth

Author: GitHub Copilot
Created: 2025-11-10 (Task 1.6.1 - C4)
"""

from typing import Dict, Tuple, Optional, List
from datetime import datetime
from collections import deque

from src.strategy.signal_generator import SignalGenerator
from src.strategy.regime_filter import RegimeFilter
from src.strategy.execution_engine import ExecutionEngine


class LayeredTradingEngine:
    """
    分層交易引擎
    
    整合 Signal → Regime → Execution 三層架構：
    1. 信號層：分析微觀結構指標，生成交易信號
    2. 風險層：評估市場風險，過濾不適合交易的時機
    3. 執行層：決定具體執行方式（倉位、槓桿、止損止盈）
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        signal_config: Optional[dict] = None,
        regime_config: Optional[dict] = None,
        execution_config: Optional[dict] = None,
        history_size: int = 1000
    ):
        """
        初始化分層交易引擎
        
        Args:
            symbol: 交易對
            signal_config: 信號生成器配置
            regime_config: 風險過濾器配置
            execution_config: 執行引擎配置
            history_size: 歷史記錄大小
        """
        self.symbol = symbol
        
        # 初始化三層
        self.signal_generator = SignalGenerator(
            symbol=symbol,
            **(signal_config or {})
        )
        
        self.regime_filter = RegimeFilter(
            symbol=symbol,
            **(regime_config or {})
        )
        
        self.execution_engine = ExecutionEngine(
            symbol=symbol,
            **(execution_config or {})
        )
        
        # 決策歷史
        self.decision_history: deque = deque(maxlen=history_size)
        
        # 統計數據
        self.total_decisions = 0
        self.executed_trades = 0
        self.blocked_trades = 0
    
    def process_market_data(self, market_data: dict) -> dict:
        """
        處理市場數據，生成完整的交易決策
        
        Args:
            market_data: 完整的市場數據，包含所有指標：
                - obi: OBI 值
                - obi_velocity: OBI 速度
                - signed_volume: 簽名成交量
                - microprice_pressure: 微觀價格壓力
                - vpin: VPIN 值
                - spread_bps: Spread 基點
                - total_depth: 總深度
                - depth_imbalance: 深度失衡
                - timestamp: 時間戳（可選）
        
        Returns:
            完整的交易決策字典
        """
        timestamp = market_data.get('timestamp', datetime.now().timestamp() * 1000)
        
        # === Layer 1: Signal Generation ===
        signal, confidence, signal_details = self.signal_generator.generate_signal(market_data)
        
        # === Layer 2: Regime Filtering ===
        is_safe, risk_level, regime_details = self.regime_filter.check_regime(market_data)
        
        # === Layer 3: Execution Decision ===
        execution_decision = self.execution_engine.decide_execution(
            signal=signal,
            confidence=confidence,
            risk_level=risk_level,
            is_safe=is_safe,
            market_data=market_data
        )
        
        # === 整合決策 ===
        final_decision = {
            'timestamp': timestamp,
            'symbol': self.symbol,
            
            # Layer 1: Signal
            'signal': {
                'direction': signal,
                'confidence': confidence,
                'details': signal_details
            },
            
            # Layer 2: Regime
            'regime': {
                'is_safe': is_safe,
                'risk_level': risk_level,
                'blocked_reasons': regime_details.get('blocked_reasons', []),
                'details': regime_details
            },
            
            # Layer 3: Execution
            'execution': execution_decision,
            
            # 最終決定
            'action': execution_decision['execution_style'],
            'can_trade': execution_decision['position_size'] > 0
        }
        
        # 更新統計
        self.total_decisions += 1
        if final_decision['can_trade']:
            self.executed_trades += 1
        else:
            self.blocked_trades += 1
        
        # 儲存歷史
        self.decision_history.append(final_decision)
        
        return final_decision
    
    def get_comprehensive_statistics(self) -> dict:
        """
        獲取綜合統計資料
        
        Returns:
            包含所有三層統計的字典
        """
        return {
            'symbol': self.symbol,
            'total_decisions': self.total_decisions,
            'executed_trades': self.executed_trades,
            'blocked_trades': self.blocked_trades,
            'execution_rate': self.executed_trades / self.total_decisions if self.total_decisions > 0 else 0,
            'block_rate': self.blocked_trades / self.total_decisions if self.total_decisions > 0 else 0,
            
            # Layer 1: Signal Statistics
            'signal_stats': self.signal_generator.get_signal_statistics(),
            
            # Layer 2: Regime Statistics
            'regime_stats': self.regime_filter.get_statistics(),
            
            # Layer 3: Execution Statistics
            'execution_stats': self.execution_engine.get_statistics(),
            
            # History
            'history_size': len(self.decision_history)
        }
    
    def get_recent_decisions(self, count: int = 10) -> List[dict]:
        """
        獲取最近的決策記錄
        
        Args:
            count: 返回的記錄數量
        
        Returns:
            最近的決策列表
        """
        if not self.decision_history:
            return []
        
        return list(self.decision_history)[-count:]
    
    def analyze_trading_performance(self, window: int = 50) -> dict:
        """
        分析交易表現
        
        Args:
            window: 分析窗口大小
        
        Returns:
            交易表現分析結果
        """
        if len(self.decision_history) < window:
            return {
                'sufficient_data': False,
                'message': f'需要至少 {window} 個決策，當前只有 {len(self.decision_history)}'
            }
        
        recent = list(self.decision_history)[-window:]
        
        # 統計執行情況
        executed = sum(1 for d in recent if d['can_trade'])
        blocked = sum(1 for d in recent if not d['can_trade'])
        
        # 信號分布
        long_signals = sum(1 for d in recent if d['signal']['direction'] == 'LONG')
        short_signals = sum(1 for d in recent if d['signal']['direction'] == 'SHORT')
        neutral_signals = sum(1 for d in recent if d['signal']['direction'] == 'NEUTRAL')
        
        # 執行風格分布
        aggressive = sum(1 for d in recent if d['execution']['execution_style'] == 'AGGRESSIVE')
        moderate = sum(1 for d in recent if d['execution']['execution_style'] == 'MODERATE')
        conservative = sum(1 for d in recent if d['execution']['execution_style'] == 'CONSERVATIVE')
        
        # 風險等級分布
        safe_periods = sum(1 for d in recent if d['regime']['risk_level'] == 'SAFE')
        warning_periods = sum(1 for d in recent if d['regime']['risk_level'] == 'WARNING')
        danger_periods = sum(1 for d in recent if d['regime']['risk_level'] == 'DANGER')
        critical_periods = sum(1 for d in recent if d['regime']['risk_level'] == 'CRITICAL')
        
        # 計算平均信心度（已執行的交易）
        executed_decisions = [d for d in recent if d['can_trade']]
        avg_confidence = (sum(d['signal']['confidence'] for d in executed_decisions) / 
                         len(executed_decisions)) if executed_decisions else 0
        
        # 計算平均倉位與槓桿
        avg_position = (sum(d['execution']['position_size'] for d in executed_decisions) / 
                       len(executed_decisions)) if executed_decisions else 0
        avg_leverage = (sum(d['execution']['leverage'] for d in executed_decisions) / 
                       len(executed_decisions)) if executed_decisions else 0
        
        return {
            'sufficient_data': True,
            'window_size': window,
            
            # 執行統計
            'executed_count': executed,
            'blocked_count': blocked,
            'execution_rate': executed / window,
            
            # 信號統計
            'long_signals': long_signals,
            'short_signals': short_signals,
            'neutral_signals': neutral_signals,
            'long_bias': (long_signals - short_signals) / window,
            
            # 執行風格統計
            'aggressive_count': aggressive,
            'moderate_count': moderate,
            'conservative_count': conservative,
            
            # 風險統計
            'safe_periods': safe_periods,
            'warning_periods': warning_periods,
            'danger_periods': danger_periods,
            'critical_periods': critical_periods,
            'market_stability': safe_periods / window,
            
            # 交易特徵
            'avg_confidence': avg_confidence,
            'avg_position_size': avg_position,
            'avg_leverage': avg_leverage,
            'avg_risk_exposure': avg_position * avg_leverage
        }
    
    def get_layer_interactions(self, window: int = 50) -> dict:
        """
        分析三層之間的交互作用
        
        Args:
            window: 分析窗口大小
        
        Returns:
            層間交互分析結果
        """
        if len(self.decision_history) < window:
            return {
                'sufficient_data': False,
                'message': f'需要至少 {window} 個決策，當前只有 {len(self.decision_history)}'
            }
        
        recent = list(self.decision_history)[-window:]
        
        # 分析：有信號但被 Regime 阻擋的情況
        signal_blocked = sum(1 for d in recent 
                            if d['signal']['direction'] != 'NEUTRAL' 
                            and not d['regime']['is_safe'])
        
        # 分析：信號強但執行保守的情況
        high_signal_conservative = sum(1 for d in recent 
                                       if d['signal']['confidence'] > 0.7 
                                       and d['execution']['execution_style'] in ['CONSERVATIVE', 'NO_TRADE'])
        
        # 分析：市場安全但無信號的情況
        safe_but_no_signal = sum(1 for d in recent 
                                 if d['regime']['is_safe'] 
                                 and d['signal']['direction'] == 'NEUTRAL')
        
        # 分析：三層都同意的情況（最理想）
        all_agree_long = sum(1 for d in recent 
                            if d['signal']['direction'] == 'LONG' 
                            and d['regime']['is_safe'] 
                            and d['execution']['execution_style'] in ['AGGRESSIVE', 'MODERATE'])
        
        all_agree_short = sum(1 for d in recent 
                             if d['signal']['direction'] == 'SHORT' 
                             and d['regime']['is_safe'] 
                             and d['execution']['execution_style'] in ['AGGRESSIVE', 'MODERATE'])
        
        return {
            'sufficient_data': True,
            'window_size': window,
            
            # 衝突分析
            'signal_blocked_by_regime': signal_blocked,
            'signal_blocked_rate': signal_blocked / window,
            
            'high_signal_but_conservative': high_signal_conservative,
            'conservative_rate': high_signal_conservative / window,
            
            'safe_market_no_signal': safe_but_no_signal,
            'missed_opportunity_rate': safe_but_no_signal / window,
            
            # 理想情況
            'all_agree_long': all_agree_long,
            'all_agree_short': all_agree_short,
            'total_ideal_setups': all_agree_long + all_agree_short,
            'ideal_setup_rate': (all_agree_long + all_agree_short) / window
        }
    
    def reset(self):
        """重置整個引擎"""
        self.signal_generator.reset()
        self.regime_filter.reset()
        self.execution_engine.reset()
        self.decision_history.clear()
        self.total_decisions = 0
        self.executed_trades = 0
        self.blocked_trades = 0
