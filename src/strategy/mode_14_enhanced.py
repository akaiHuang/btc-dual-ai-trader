"""
M14 增強版策略 - 集成動態 VPIN 和智能獲利了結
==================================================

新增功能：
1. 動態 VPIN 閾值調整（替代靜態 0.75）
2. 分層風險過濾機制
3. 智能獲利了結引擎
4. 市場狀態感知的方案切換
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import numpy as np

from .mode_14_dynamic_leverage import (
    Mode14Strategy,
    MarketRegimeDetector,
    SignalQualityScorer,
    DynamicLeverageAdjuster,
    DynamicPositionSizer,
    DynamicTPSLAdjuster,
    CostAwareProfitCalculator,
    StrategySelector,
    TradingScheme
)

logger = logging.getLogger(__name__)


class DynamicVPINAdapter:
    """動態 VPIN 適配器：為 M14 提供動態閾值調整"""
    
    def __init__(self, base_threshold: float = 0.75):
        self.base_threshold = base_threshold
        self.min_threshold = 0.5  # M14 最小閾值
        self.max_threshold = 0.9  # M14 最大閾值
    
    def get_dynamic_threshold(self, market_data: Dict) -> float:
        """
        根據市場狀態動態調整 VPIN 閾值
        
        Args:
            market_data: {
                'obi_velocity': float,  # OBI 變化速度
                'spread_bps': float,    # 點差（基點）
                'volatility': float     # 波動率
            }
        
        Returns:
            動態調整後的閾值（0.5-0.9）
        """
        threshold = self.base_threshold
        
        # 獲取市場指標
        obi_velocity = abs(market_data.get('obi_velocity', 0))
        spread_bps = market_data.get('spread_bps', 5)
        volatility = market_data.get('volatility', 0.015)
        
        # 波動性調整係數
        volatility_factor = 1.0
        
        # OBI 快速變化 → 降低閾值（更保守）
        if obi_velocity > 1.5:
            volatility_factor *= 0.6  # 降低 40%
        elif obi_velocity > 1.0:
            volatility_factor *= 0.7  # 降低 30%
        elif obi_velocity > 0.5:
            volatility_factor *= 0.85  # 降低 15%
        
        # 流動性差 → 降低閾值（更保守）
        if spread_bps > 15:
            volatility_factor *= 0.7  # 降低 30%
        elif spread_bps > 10:
            volatility_factor *= 0.8  # 降低 20%
        elif spread_bps > 5:
            volatility_factor *= 0.9  # 降低 10%
        
        # 高波動率 → 降低閾值（更保守）
        if volatility > 0.04:  # 4%
            volatility_factor *= 0.75  # 降低 25%
        elif volatility > 0.03:  # 3%
            volatility_factor *= 0.85  # 降低 15%
        
        # 計算動態閾值
        dynamic_threshold = threshold * volatility_factor
        
        # 限制在合理範圍
        return max(self.min_threshold, min(self.max_threshold, dynamic_threshold))
    
    def get_market_state(self, market_data: Dict) -> str:
        """
        識別當前市場狀態
        
        Returns:
            CALM: 平靜（VPIN < 0.3）
            NORMAL: 正常（0.3 ≤ VPIN < 0.5）
            VOLATILE: 波動（0.5 ≤ VPIN < 0.7）
            EXTREME: 極端（VPIN ≥ 0.7）
        """
        vpin = market_data.get('vpin', 0.3)
        obi_velocity = abs(market_data.get('obi_velocity', 0))
        
        if vpin < 0.3 and obi_velocity < 0.5:
            return "CALM"
        elif vpin < 0.5 and obi_velocity < 1.0:
            return "NORMAL"
        elif vpin < 0.7 or obi_velocity < 2.0:
            return "VOLATILE"
        else:
            return "EXTREME"
    
    def enhanced_vpin_filter(self, market_data: Dict) -> Tuple[bool, Optional[str]]:
        """
        增強版 VPIN 過濾器：四級分層決策
        
        Returns:
            (is_safe, reason): (是否安全, 原因/警告)
        """
        vpin = market_data.get('vpin', 0.3)
        dynamic_threshold = self.get_dynamic_threshold(market_data)
        
        # 第一層：安全區（VPIN < 0.3）
        if vpin < 0.3:
            return True, None  # 完全安全，正常交易
        
        # 第二層：警告區（0.3 ≤ VPIN < 0.5）
        elif vpin < 0.5:
            if vpin > dynamic_threshold:
                return False, f"⚠️ VPIN略高 ({vpin:.3f} > {dynamic_threshold:.3f} 動態閾值)"
            return True, f"ℹ️ VPIN略高 ({vpin:.3f})，密切監控"
        
        # 第三層：條件允許區（0.5 ≤ VPIN < 0.7）
        elif vpin < 0.7:
            # 需要強信號確認
            obi_strength = abs(market_data.get('obi', 0))
            signal_quality = market_data.get('signal_quality', 0.5)
            
            if obi_strength > 0.8 and signal_quality > 0.8:
                return True, f"⚠️ VPIN較高 ({vpin:.3f})，但信號極強"
            else:
                return False, f"🚫 VPIN過高 ({vpin:.3f})且信號不足 (OBI={obi_strength:.3f}, Q={signal_quality:.3f})"
        
        # 第四層：禁止區（VPIN ≥ 0.7）
        else:
            return False, f"🔴 VPIN危險 ({vpin:.3f} ≥ 0.7)，禁止交易"


class ProfitDecision:
    """獲利決策結果"""
    
    def __init__(self, should_exit: bool, reason: str, confidence: float):
        self.should_exit = should_exit
        self.reason = reason
        self.confidence = confidence  # 0-1


class DynamicProfitTakingEngine:
    """動態獲利了結引擎：多因子評估系統"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.enabled = config.get('enabled', True)
        
        # M14 專用目標（根據方案調整）
        self.profit_targets = {
            'A': 0.03,   # 保守方案：3%
            'B': 0.05,   # 平衡方案：5%
            'C': 0.08    # 積極方案：8%
        }
        
        # 強制平倉閾值
        self.force_exit_threshold = {
            'A': 0.05,   # 保守：5%
            'B': 0.10,   # 平衡：10%
            'C': 0.15    # 積極：15%
        }
        
        # 評估權重
        self.weights = {
            'profit_target': 0.3,    # 目標達成度
            'time_decay': 0.3,       # 時間衰減（HFT 特性）
            'market_toxicity': 0.2,  # 市場毒性
            'volatility': 0.2        # 波動率變化
        }
    
    def should_take_profit(
        self, 
        position: Dict, 
        market_data: Dict, 
        current_scheme: str = 'B'
    ) -> Tuple[bool, str, float]:
        """
        判斷是否應該獲利了結
        
        Args:
            position: {
                'unrealized_pnl_pct': float,  # 未實現盈虧百分比
                'entry_time': datetime,        # 進場時間
                'entry_price': float,          # 進場價格
                'leverage': float              # 槓桿
            }
            market_data: 市場數據
            current_scheme: 當前方案 ('A', 'B', 'C')
        
        Returns:
            (should_exit, reason, confidence)
        """
        if not self.enabled:
            return False, "", 0.0
        
        current_profit = position['unrealized_pnl_pct']
        
        # 只在盈利時考慮獲利了結
        if current_profit <= 0:
            return False, "", 0.0
        
        # 獲取目標和閾值
        profit_target = self.profit_targets.get(current_scheme, 0.05)
        force_threshold = self.force_exit_threshold.get(current_scheme, 0.10)
        
        # 強制平倉（超額收益保護）
        if current_profit >= force_threshold:
            return True, f"🎯 強制獲利了結 (收益 {current_profit:.2%} ≥ {force_threshold:.2%})", 1.0
        
        # 多因子評估
        decision = self._evaluate_factors(
            position, market_data, profit_target, current_scheme
        )
        
        return decision.should_exit, decision.reason, decision.confidence
    
    def _evaluate_factors(
        self, 
        position: Dict, 
        market_data: Dict, 
        profit_target: float,
        current_scheme: str
    ) -> ProfitDecision:
        """多因子綜合評估"""
        
        current_profit = position['unrealized_pnl_pct']
        
        # 因子 1：目標達成度
        target_achievement = min(current_profit / profit_target, 1.0)
        target_score = target_achievement * self.weights['profit_target']
        
        # 因子 2：時間衰減（HFT 特性：快速進出）
        entry_time = position.get('entry_time', datetime.now())
        hold_duration = (datetime.now() - entry_time).total_seconds() / 60  # 分鐘
        
        # M14 是 HFT 策略，持倉時間越長越傾向平倉
        if hold_duration > 10:  # 超過 10 分鐘
            time_score = 1.0 * self.weights['time_decay']
        elif hold_duration > 5:  # 5-10 分鐘
            time_score = 0.7 * self.weights['time_decay']
        elif hold_duration > 3:  # 3-5 分鐘
            time_score = 0.4 * self.weights['time_decay']
        else:  # < 3 分鐘
            time_score = 0.0
        
        # 因子 3：市場毒性（VPIN）
        vpin = market_data.get('vpin', 0.3)
        if vpin > 0.7:
            toxicity_score = 1.0 * self.weights['market_toxicity']
        elif vpin > 0.5:
            toxicity_score = 0.6 * self.weights['market_toxicity']
        elif vpin > 0.3:
            toxicity_score = 0.3 * self.weights['market_toxicity']
        else:
            toxicity_score = 0.0
        
        # 因子 4：波動率惡化
        volatility = market_data.get('volatility', 0.015)
        if volatility > 0.04:  # 高波動
            volatility_score = 0.8 * self.weights['volatility']
        elif volatility > 0.03:
            volatility_score = 0.5 * self.weights['volatility']
        else:
            volatility_score = 0.0
        
        # 綜合評分
        total_score = target_score + time_score + toxicity_score + volatility_score
        
        # 決策閾值（根據方案調整）
        decision_thresholds = {
            'A': 0.4,  # 保守：低閾值，容易觸發
            'B': 0.5,  # 平衡：中等閾值
            'C': 0.6   # 積極：高閾值，不容易觸發
        }
        threshold = decision_thresholds.get(current_scheme, 0.5)
        
        # 生成決策
        if total_score >= threshold:
            reason = f"✅ 多因子觸發 (評分 {total_score:.2f}≥{threshold:.2f}): "
            reason += f"目標{target_achievement:.1%}, "
            reason += f"持倉{hold_duration:.1f}分, "
            reason += f"VPIN {vpin:.3f}, "
            reason += f"波動率 {volatility:.3f}"
            return ProfitDecision(True, reason, total_score)
        else:
            return ProfitDecision(False, "", total_score)


class EnhancedStrategySelector(StrategySelector):
    """增強版策略選擇器：市場狀態感知的方案切換"""
    
    def __init__(self):
        super().__init__()
        self.vpin_adapter = DynamicVPINAdapter()
    
    def should_upgrade_strategy(self, market_data: Optional[Dict] = None) -> Tuple[bool, str]:
        """
        增強版升級判斷：考慮市場環境
        
        Args:
            market_data: 市場數據（可選）
        
        Returns:
            (should_upgrade, reason)
        """
        # 原有升級條件檢查
        if not super().should_upgrade_strategy():
            return False, "未滿足基本升級條件"
        
        # 如果沒有市場數據，允許升級
        if market_data is None:
            return True, "滿足基本升級條件"
        
        # 市場環境檢查
        market_state = self.vpin_adapter.get_market_state(market_data)
        vpin = market_data.get('vpin', 0.3)
        
        # 極端市場阻止升級
        if market_state == "EXTREME" or vpin > 0.8:
            return False, f"❌ 市場環境不適合升級 (狀態={market_state}, VPIN={vpin:.3f})"
        
        # 波動市場謹慎升級
        if market_state == "VOLATILE" and vpin > 0.6:
            return False, f"⚠️ 市場波動較大，暫緩升級 (VPIN={vpin:.3f})"
        
        return True, f"✅ 市場環境適合升級 (狀態={market_state})"
    
    def should_downgrade_strategy(
        self, 
        current_balance: float, 
        initial_balance: float,
        market_data: Optional[Dict] = None
    ) -> Tuple[bool, str]:
        """
        增強版降級判斷：市場毒性自動觸發
        
        Returns:
            (should_downgrade, reason)
        """
        # 市場毒性檢查（優先級最高）
        if market_data is not None:
            vpin = market_data.get('vpin', 0.3)
            
            # VPIN 極端升高 → 立即降級
            if vpin > 0.85:
                return True, f"🚨 VPIN極端升高 ({vpin:.3f})，立即降級保護"
            
            # VPIN 持續偏高 → 預防性降級
            if vpin > 0.75:
                market_state = self.vpin_adapter.get_market_state(market_data)
                if market_state == "EXTREME":
                    return True, f"⚠️ 市場極端波動 (VPIN={vpin:.3f})，預防性降級"
        
        # 原有降級條件檢查
        if super().should_downgrade_strategy(current_balance, initial_balance):
            return True, "觸發基本降級條件（連續虧損或回撤過大）"
        
        return False, ""


class EnhancedMode14Strategy(Mode14Strategy):
    """
    M14 增強版策略
    
    新功能：
    1. 動態 VPIN 閾值（替代靜態 0.75）
    2. 四級風險過濾機制
    3. 智能獲利了結引擎
    4. 市場感知的方案切換
    """
    
    def __init__(self, config: Dict):
        # 初始化基礎 M14 策略
        super().__init__(config)
        
        # 集成增強組件
        self.vpin_adapter = DynamicVPINAdapter(
            base_threshold=config.get('risk_control', {}).get('vpin_threshold', 0.75)
        )
        
        self.profit_engine = DynamicProfitTakingEngine(
            config.get('profit_taking', {
                'enabled': True,
                'aggressive': False
            })
        )
        
        # 替換策略選擇器為增強版
        self.strategy_selector = EnhancedStrategySelector()
        
        logger.info("✨ M14 增強版策略初始化完成（集成動態 VPIN + 智能獲利了結）")
    
    def should_enter_trade(self, market_data: Dict) -> Tuple[bool, str]:
        """
        增強版進場判斷
        
        改進：
        1. 使用動態 VPIN 過濾替代靜態檢查
        2. 四級分層風險評估
        3. 更靈活的條件判斷
        """
        # 更新數據
        self.market_detector.update_price(market_data['price'])
        self.signal_scorer.update_data(market_data['volume'], market_data['price'])
        
        # 第一步：動態 VPIN 過濾（最高優先級）
        is_vpin_safe, vpin_reason = self.vpin_adapter.enhanced_vpin_filter(market_data)
        if not is_vpin_safe:
            return False, f"VPIN過濾: {vpin_reason}"
        
        # 檢測市場狀態
        market_regime = self.market_detector.detect_regime()
        market_state = self.vpin_adapter.get_market_state(market_data)
        
        # 極端市場直接拒絕
        if market_state == "EXTREME":
            return False, "市場極端波動，暫停交易"
        
        # 計算信號質量
        signal_score = self.signal_scorer.score_signal(
            obi_data={'current': market_data['obi']},
            volume_data={'current': market_data['volume'], 'average': market_data['avg_volume']},
            mtf_signals=market_data.get('mtf_signals', {})
        )
        
        # 將信號質量加入市場數據（供 VPIN 過濾使用）
        market_data['signal_quality'] = signal_score
        
        # 第二步：8選7 條件檢查（增強版）
        conditions = self._check_enhanced_conditions(market_data, signal_score, market_regime)
        met_conditions = sum(conditions.values())
        
        # 決策閾值
        if met_conditions >= 7:
            # 完美進場
            reasons = [f"✅ {k}" for k, v in conditions.items() if v]
            return True, f"8選7通過 ({met_conditions}/8): " + ", ".join(reasons[:3])
        elif met_conditions >= 6 and signal_score > 0.8:
            # 高質量信號可以放寬條件
            return True, f"高質量信號放寬進場 ({met_conditions}/8, 信號質量 {signal_score:.2f})"
        else:
            # 條件不足
            failed = [k for k, v in conditions.items() if not v]
            return False, f"條件不足 ({met_conditions}/8)，失敗: {', '.join(failed[:2])}"
    
    def _check_enhanced_conditions(
        self, 
        market_data: Dict, 
        signal_score: float,
        market_regime: str
    ) -> Dict[str, bool]:
        """
        增強版 8選7 條件檢查
        
        改進：
        1. VPIN 使用動態閾值
        2. 放寬部分條件以提高進場機會
        3. 根據市場狀態調整標準
        """
        # 動態閾值
        dynamic_vpin_threshold = self.vpin_adapter.get_dynamic_threshold(market_data)
        
        conditions = {}
        
        # 核心風控（3個）
        conditions['vpin_safe'] = market_data['vpin'] < dynamic_vpin_threshold  # 動態！
        conditions['spread_ok'] = market_data['spread'] < 15  # 放寬：10 → 15 bps
        conditions['depth_ok'] = market_data['depth'] > 3     # 放寬：5 → 3
        
        # 信號質量（3個）
        conditions['strong_signal'] = abs(market_data['obi']) > 0.5  # 放寬：0.6 → 0.5
        conditions['signal_quality'] = signal_score > 0.6             # 放寬：0.7 → 0.6
        conditions['volume_confirmation'] = (
            market_data['volume'] / market_data['avg_volume']
        ) > 1.0  # 放寬：1.2 → 1.0
        
        # 趨勢確認（1個）
        conditions['trend_aligned'] = market_regime in ["TRENDING", "NEUTRAL"]
        
        # 盈利預期（1個）
        expected_move = 0.002  # 0.2%
        conditions['profitable_after_costs'] = self.cost_calculator.is_trade_profitable(
            expected_move=expected_move,
            leverage=self.current_leverage,
            position_size=self.current_position_size
        )
        
        return conditions
    
    def check_profit_taking(
        self, 
        position: Dict, 
        market_data: Dict
    ) -> Tuple[bool, str]:
        """
        檢查是否應該獲利了結
        
        Args:
            position: 持倉信息
            market_data: 市場數據
        
        Returns:
            (should_exit, reason)
        """
        # 只在盈利時檢查
        if position.get('unrealized_pnl_pct', 0) <= 0:
            return False, ""
        
        # 獲取當前方案
        current_scheme = self.strategy_selector.current_scheme
        
        # 調用獲利引擎
        should_exit, reason, confidence = self.profit_engine.should_take_profit(
            position, market_data, current_scheme
        )
        
        if should_exit:
            logger.info(f"💰 獲利了結觸發: {reason} (置信度 {confidence:.2f})")
        
        return should_exit, reason
    
    def update_scheme_if_needed(
        self, 
        current_balance: float, 
        initial_balance: float,
        market_regime: str, 
        current_vpin: float, 
        network_latency: float = 0,
        market_data: Optional[Dict] = None
    ) -> str:
        """
        增強版方案更新：考慮市場環境
        
        Args:
            market_data: 完整的市場數據（可選，用於更精確的判斷）
        """
        # 停止交易檢查
        if self.strategy_selector.should_stop_trading(
            current_balance, initial_balance, current_vpin, network_latency
        ):
            logger.error("🛑 觸發停止交易條件")
            return "STOP"
        
        # 準備市場數據
        if market_data is None:
            market_data = {'vpin': current_vpin}
        
        # 選擇最佳方案
        optimal_scheme = self.strategy_selector.select_optimal_scheme(
            market_regime, current_balance, initial_balance
        )
        
        current_scheme = self.strategy_selector.current_scheme
        
        # 檢查升級條件（考慮市場環境）
        if optimal_scheme > current_scheme:
            should_upgrade, reason = self.strategy_selector.should_upgrade_strategy(market_data)
            if should_upgrade:
                self.strategy_selector.update_scheme(optimal_scheme)
                logger.info(f"🚀 策略升級: {current_scheme} → {optimal_scheme} ({reason})")
        
        # 檢查降級條件（考慮市場毒性）
        elif optimal_scheme < current_scheme:
            should_downgrade, reason = self.strategy_selector.should_downgrade_strategy(
                current_balance, initial_balance, market_data
            )
            if should_downgrade:
                self.strategy_selector.update_scheme(optimal_scheme)
                logger.warning(f"⬇️ 策略降級: {current_scheme} → {optimal_scheme} ({reason})")
        
        return self.strategy_selector.current_scheme
    
    def get_dynamic_vpin_threshold(self, market_data: Dict) -> float:
        """獲取當前動態 VPIN 閾值（用於監控顯示）"""
        return self.vpin_adapter.get_dynamic_threshold(market_data)
    
    def get_market_state(self, market_data: Dict) -> str:
        """獲取市場狀態（用於監控顯示）"""
        return self.vpin_adapter.get_market_state(market_data)
