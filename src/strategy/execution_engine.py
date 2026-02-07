"""
Execution Engine - Task 1.6.1 Phase C

Purpose:
    執行引擎，根據信號強度與市場狀態決定交易執行方式。
    
Architecture:
    Layer 1 (Signal) → Layer 2 (Regime) → Layer 3 (Execution) ✓

Decision Factors:
    1. Signal Confidence (0-1)
    2. Market Risk Level (SAFE/WARNING/DANGER/CRITICAL)
    3. Recent Performance (optional)

Output:
    - Execution Style: AGGRESSIVE / MODERATE / CONSERVATIVE / NO_TRADE
    - Position Size: 0.0 - 1.0
    - Leverage: 1x - 5x
    - Entry Method: MARKET / LIMIT
    - Exit Strategy: Stop Loss / Take Profit levels

Author: GitHub Copilot
Created: 2025-11-10 (Task 1.6.1 - C3)
"""

from typing import Dict, Tuple, Optional, List
from datetime import datetime
from collections import deque
import numpy as np


class ExecutionEngine:
    """
    執行引擎 - 決定如何執行交易
    
    根據信號強度與市場風險決定：
    1. 執行風格（激進/穩健/保守）
    2. 倉位大小（0-100%）
    3. 槓桿倍數（1-5x）
    4. 進場方式（市價/限價）
    5. 止損止盈設定
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        max_leverage: int = 5,
        base_position_size: float = 1.0,
        aggressive_threshold: float = 0.8,
        moderate_threshold: float = 0.6,
        conservative_threshold: float = 0.4,
        history_size: int = 100
    ):
        """
        初始化執行引擎
        
        Args:
            symbol: 交易對
            max_leverage: 最大槓桿倍數
            base_position_size: 基礎倉位大小（1.0 = 100%）
            aggressive_threshold: 激進交易閾值
            moderate_threshold: 穩健交易閾值
            conservative_threshold: 保守交易閾值
            history_size: 歷史記錄大小
        """
        self.symbol = symbol
        self.max_leverage = max_leverage
        self.base_position_size = base_position_size
        
        # 閾值配置
        self.aggressive_threshold = aggressive_threshold
        self.moderate_threshold = moderate_threshold
        self.conservative_threshold = conservative_threshold
        
        # 歷史記錄
        self.execution_history: deque = deque(maxlen=history_size)
        
        # 統計數據
        self.total_decisions = 0
        self.aggressive_count = 0
        self.moderate_count = 0
        self.conservative_count = 0
        self.no_trade_count = 0
    
    def decide_execution(
        self, 
        signal: str, 
        confidence: float, 
        risk_level: str,
        is_safe: bool,
        market_data: Optional[dict] = None
    ) -> dict:
        """
        決定執行策略
        
        Args:
            signal: 信號方向 ("LONG" / "SHORT" / "NEUTRAL")
            confidence: 信號信心度 (0-1)
            risk_level: 市場風險等級 ("SAFE" / "WARNING" / "DANGER" / "CRITICAL")
            is_safe: 市場是否安全
            market_data: 額外市場數據（可選）
        
        Returns:
            執行決策字典
        """
        timestamp = datetime.now().timestamp() * 1000
        
        # === 決策邏輯 ===
        
        # 1. 如果信號是 NEUTRAL 或市場不安全，不交易
        if signal == "NEUTRAL" or not is_safe:
            execution_style = "NO_TRADE"
            position_size = 0.0
            leverage = 1
            entry_method = "NONE"
            reason = []
            
            if signal == "NEUTRAL":
                reason.append("信號不明確")
            if not is_safe:
                reason.append(f"市場風險過高 ({risk_level})")
            
            self.no_trade_count += 1
        
        # 2. AGGRESSIVE（激進交易）
        elif confidence >= self.aggressive_threshold and risk_level == "SAFE":
            execution_style = "AGGRESSIVE"
            position_size = self.base_position_size * 1.0  # 滿倉
            leverage = self.max_leverage  # 最大槓桿
            entry_method = "MARKET"  # 市價單快速進場
            reason = [f"高信心度 ({confidence:.2f})", "市場安全"]
            
            self.aggressive_count += 1
        
        # 3. MODERATE（穩健交易）
        elif confidence >= self.moderate_threshold:
            execution_style = "MODERATE"
            
            # 根據風險等級調整倉位
            if risk_level == "SAFE":
                position_size = self.base_position_size * 0.7
                leverage = 3
            else:  # WARNING
                position_size = self.base_position_size * 0.5
                leverage = 2
            
            entry_method = "LIMIT"  # 限價單掛單
            reason = [f"中等信心度 ({confidence:.2f})", f"風險 {risk_level}"]
            
            self.moderate_count += 1
        
        # 4. CONSERVATIVE（保守交易）
        elif confidence >= self.conservative_threshold:
            execution_style = "CONSERVATIVE"
            position_size = self.base_position_size * 0.3  # 小倉位
            leverage = 1  # 無槓桿
            entry_method = "LIMIT"
            reason = [f"低信心度 ({confidence:.2f})"]
            
            self.conservative_count += 1
        
        # 5. 信心度過低，不交易
        else:
            execution_style = "NO_TRADE"
            position_size = 0.0
            leverage = 1
            entry_method = "NONE"
            reason = [f"信心度過低 ({confidence:.2f} < {self.conservative_threshold})"]
            
            self.no_trade_count += 1
        
        # === 止損止盈設定 ===
        stop_loss_pct = self._calculate_stop_loss(execution_style, leverage)
        take_profit_pct = self._calculate_take_profit(execution_style, leverage)
        
        # === 構建執行決策 ===
        decision = {
            'signal': signal,
            'confidence': confidence,
            'risk_level': risk_level,
            'is_safe': is_safe,
            'execution_style': execution_style,
            'position_size': position_size,
            'leverage': leverage,
            'entry_method': entry_method,
            'stop_loss_pct': stop_loss_pct,
            'take_profit_pct': take_profit_pct,
            'reason': reason,
            'timestamp': timestamp
        }
        
        # 如果有額外市場數據，加入決策
        if market_data:
            decision['market_data'] = market_data
        
        # 儲存歷史
        self.execution_history.append(decision)
        self.total_decisions += 1
        
        return decision
    
    def _calculate_stop_loss(self, style: str, leverage: int) -> float:
        """
        計算止損百分比
        
        Args:
            style: 執行風格
            leverage: 槓桿倍數
        
        Returns:
            止損百分比（例如：0.02 = 2%）
        """
        # 基礎止損：根據槓桿調整
        base_stop = 0.02  # 2%
        
        if style == "AGGRESSIVE":
            # 激進交易：較寬止損，給價格空間
            return base_stop * 1.5 / leverage
        elif style == "MODERATE":
            # 穩健交易：標準止損
            return base_stop / leverage
        elif style == "CONSERVATIVE":
            # 保守交易：較緊止損
            return base_stop * 0.5 / leverage
        else:
            return 0.0
    
    def _calculate_take_profit(self, style: str, leverage: int) -> float:
        """
        計算止盈百分比
        
        Args:
            style: 執行風格
            leverage: 槓桿倍數
        
        Returns:
            止盈百分比（例如：0.04 = 4%）
        """
        # 風險報酬比：2:1 或 3:1
        if style == "AGGRESSIVE":
            risk_reward_ratio = 3.0
        elif style == "MODERATE":
            risk_reward_ratio = 2.5
        elif style == "CONSERVATIVE":
            risk_reward_ratio = 2.0
        else:
            return 0.0
        
        stop_loss = self._calculate_stop_loss(style, leverage)
        return stop_loss * risk_reward_ratio
    
    def get_statistics(self) -> dict:
        """
        獲取統計資料
        
        Returns:
            統計資料字典
        """
        stats = {
            'symbol': self.symbol,
            'total_decisions': self.total_decisions,
            'aggressive_count': self.aggressive_count,
            'moderate_count': self.moderate_count,
            'conservative_count': self.conservative_count,
            'no_trade_count': self.no_trade_count,
            'aggressive_ratio': self.aggressive_count / self.total_decisions if self.total_decisions > 0 else 0,
            'moderate_ratio': self.moderate_count / self.total_decisions if self.total_decisions > 0 else 0,
            'conservative_ratio': self.conservative_count / self.total_decisions if self.total_decisions > 0 else 0,
            'no_trade_ratio': self.no_trade_count / self.total_decisions if self.total_decisions > 0 else 0,
            'history_size': len(self.execution_history)
        }
        
        # 計算平均倉位與槓桿
        if self.execution_history:
            traded_decisions = [d for d in self.execution_history if d['position_size'] > 0]
            
            if traded_decisions:
                avg_position = np.mean([d['position_size'] for d in traded_decisions])
                avg_leverage = np.mean([d['leverage'] for d in traded_decisions])
                avg_risk = avg_position * avg_leverage  # 實際風險暴露
                
                stats.update({
                    'avg_position_size': avg_position,
                    'avg_leverage': avg_leverage,
                    'avg_risk_exposure': avg_risk,
                    'traded_decisions': len(traded_decisions)
                })
        
        return stats
    
    def get_recent_decisions(self, count: int = 10) -> List[dict]:
        """
        獲取最近的執行決策
        
        Args:
            count: 返回的記錄數量
        
        Returns:
            最近的決策列表
        """
        if not self.execution_history:
            return []
        
        return list(self.execution_history)[-count:]
    
    def analyze_execution_pattern(self, window: int = 20) -> dict:
        """
        分析執行模式
        
        Args:
            window: 分析窗口大小
        
        Returns:
            執行模式分析結果
        """
        if len(self.execution_history) < window:
            return {
                'sufficient_data': False,
                'message': f'需要至少 {window} 個決策，當前只有 {len(self.execution_history)}'
            }
        
        recent = list(self.execution_history)[-window:]
        
        # 統計執行風格分布
        aggressive = sum(1 for d in recent if d['execution_style'] == 'AGGRESSIVE')
        moderate = sum(1 for d in recent if d['execution_style'] == 'MODERATE')
        conservative = sum(1 for d in recent if d['execution_style'] == 'CONSERVATIVE')
        no_trade = sum(1 for d in recent if d['execution_style'] == 'NO_TRADE')
        
        # 計算積極度得分（0-1，1=最激進）
        aggressiveness_score = (aggressive * 1.0 + moderate * 0.6 + 
                               conservative * 0.3 + no_trade * 0.0) / window
        
        # 判斷交易風格
        if aggressiveness_score > 0.7:
            trading_style = "VERY_AGGRESSIVE"
        elif aggressiveness_score > 0.5:
            trading_style = "AGGRESSIVE"
        elif aggressiveness_score > 0.3:
            trading_style = "BALANCED"
        elif aggressiveness_score > 0.1:
            trading_style = "CONSERVATIVE"
        else:
            trading_style = "VERY_CONSERVATIVE"
        
        # 計算實際交易率
        trade_rate = (aggressive + moderate + conservative) / window
        
        return {
            'sufficient_data': True,
            'window_size': window,
            'aggressive_count': aggressive,
            'moderate_count': moderate,
            'conservative_count': conservative,
            'no_trade_count': no_trade,
            'aggressiveness_score': aggressiveness_score,
            'trading_style': trading_style,
            'trade_rate': trade_rate
        }
    
    def reset(self):
        """重置執行引擎"""
        self.execution_history.clear()
        self.total_decisions = 0
        self.aggressive_count = 0
        self.moderate_count = 0
        self.conservative_count = 0
        self.no_trade_count = 0
