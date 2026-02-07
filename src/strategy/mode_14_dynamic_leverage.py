"""
M14 策略 - 動態槓桿優化策略
具備三方案自適應切換機制（A/B/C）
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import numpy as np

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """市場狀態檢測器"""
    
    def __init__(self, volatility_threshold_high=0.025, volatility_threshold_low=0.01):
        self.volatility_threshold_high = volatility_threshold_high
        self.volatility_threshold_low = volatility_threshold_low
        self.price_history = []
        self.max_history_length = 60  # 保留60個數據點
        
    def update_price(self, price: float):
        """更新價格歷史"""
        self.price_history.append(price)
        if len(self.price_history) > self.max_history_length:
            self.price_history.pop(0)
    
    def calculate_volatility(self) -> float:
        """計算波動率（ATR百分比）"""
        if len(self.price_history) < 20:
            return 0.015  # 默認值
        
        prices = np.array(self.price_history[-20:])
        returns = np.diff(prices) / prices[:-1]
        atr_percentage = np.std(returns)
        return atr_percentage
    
    def calculate_trend_strength(self) -> float:
        """計算趨勢強度（0-1）"""
        if len(self.price_history) < 20:
            return 0.0
        
        prices = np.array(self.price_history[-20:])
        
        # 使用線性回歸斜率
        x = np.arange(len(prices))
        slope, _ = np.polyfit(x, prices, 1)
        
        # 正規化斜率
        trend_strength = abs(slope) / (np.mean(prices) * 0.01)  # 與1%價格變動比較
        return min(1.0, trend_strength)
    
    def calculate_obi_consistency(self, obi_history: List[float]) -> float:
        """計算OBI一致性（0-1）"""
        if len(obi_history) < 5:
            return 0.0
        
        recent_obi = obi_history[-10:]
        
        # 計算同向性
        positive_count = sum(1 for x in recent_obi if x > 0)
        negative_count = sum(1 for x in recent_obi if x < 0)
        
        consistency = max(positive_count, negative_count) / len(recent_obi)
        return consistency
    
    def detect_regime(self, obi_history: Optional[List[float]] = None) -> str:
        """
        檢測市場狀態
        
        Returns:
            str: TRENDING, VOLATILE, CONSOLIDATION, NEUTRAL
        """
        volatility = self.calculate_volatility()
        trend_strength = self.calculate_trend_strength()
        obi_consistency = self.calculate_obi_consistency(obi_history or [])
        
        logger.debug(f"市場指標 - 波動率: {volatility:.4f}, 趨勢強度: {trend_strength:.2f}, OBI一致性: {obi_consistency:.2f}")
        
        if trend_strength > 0.7 and obi_consistency > 0.6:
            return "TRENDING"
        elif volatility > self.volatility_threshold_high:
            return "VOLATILE"
        elif volatility < self.volatility_threshold_low and trend_strength < 0.3:
            return "CONSOLIDATION"
        else:
            return "NEUTRAL"


class SignalQualityScorer:
    """信號質量評分器"""
    
    def __init__(self):
        self.volume_history = []
        self.price_history = []
        self.max_history = 20
        
    def update_data(self, volume: float, price: float):
        """更新數據"""
        self.volume_history.append(volume)
        self.price_history.append(price)
        
        if len(self.volume_history) > self.max_history:
            self.volume_history.pop(0)
        if len(self.price_history) > self.max_history:
            self.price_history.pop(0)
    
    def calculate_momentum(self) -> float:
        """計算價格動能（0-1）"""
        if len(self.price_history) < 10:
            return 0.5
        
        recent_prices = self.price_history[-10:]
        price_change = (recent_prices[-1] - recent_prices[0]) / recent_prices[0]
        
        # 正規化到0-1
        momentum = min(1.0, abs(price_change) / 0.01)  # 1%價格變動作為滿分
        return momentum
    
    def multi_timeframe_confirmation(self, mtf_signals: Dict[str, float]) -> float:
        """
        多時間框架確認度（0-1）
        
        Args:
            mtf_signals: {timeframe: obi_value} 例如 {"5m": 0.8, "15m": 0.6, "30m": 0.5}
        """
        if not mtf_signals:
            return 0.5
        
        # 檢查信號方向一致性
        values = list(mtf_signals.values())
        positive_count = sum(1 for v in values if v > 0)
        negative_count = sum(1 for v in values if v < 0)
        
        # 方向一致性
        direction_consistency = max(positive_count, negative_count) / len(values)
        
        # 信號強度平均
        avg_strength = np.mean([abs(v) for v in values])
        
        # 綜合評分
        confirmation = (direction_consistency * 0.6 + avg_strength * 0.4)
        return confirmation
    
    def score_signal(self, obi_data: Dict, volume_data: Dict, 
                     mtf_signals: Optional[Dict[str, float]] = None) -> float:
        """
        綜合評分信號質量（0-1）
        
        Args:
            obi_data: {"current": float}
            volume_data: {"current": float, "average": float}
            mtf_signals: 多時間框架OBI信號
        """
        score = 0.0
        
        # OBI 強度 (30%)
        obi_strength = abs(obi_data.get('current', 0))
        score += obi_strength * 0.3
        
        # 成交量確認 (25%)
        current_volume = volume_data.get('current', 0)
        avg_volume = volume_data.get('average', 1)
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        volume_confirm = min(1.0, volume_ratio)
        score += volume_confirm * 0.25
        
        # 價格動能 (20%)
        price_momentum = self.calculate_momentum()
        score += price_momentum * 0.2
        
        # 多時間框架確認 (25%)
        mtf_confirm = self.multi_timeframe_confirmation(mtf_signals or {})
        score += mtf_confirm * 0.25
        
        return min(1.0, score)


class CostAwareProfitCalculator:
    """成本感知盈利計算器"""
    
    def __init__(self, fee_rate=0.0006, slippage=0.0002):
        self.fee_rate = fee_rate  # 0.06% taker費率
        self.slippage = slippage  # 0.02% 滑價
        
    def calculate_breakeven(self, leverage: float, position_size: float) -> float:
        """
        計算盈虧平衡點（價格變動百分比）
        
        Args:
            leverage: 槓桿倍數
            position_size: 倉位大小（比例）
        
        Returns:
            float: 所需的價格變動百分比
        """
        total_cost = self.fee_rate * 2 + self.slippage  # 開倉+平倉費用+滑價
        breakeven_price_move = total_cost / (leverage * position_size)
        return breakeven_price_move
    
    def is_trade_profitable(self, expected_move: float, leverage: float, 
                           position_size: float, safety_margin: float = 1.5) -> bool:
        """
        判斷交易是否有利可圖
        
        Args:
            expected_move: 預期價格變動（百分比）
            leverage: 槓桿倍數
            position_size: 倉位大小
            safety_margin: 安全邊際倍數（默認1.5倍）
        
        Returns:
            bool: 是否有利可圖
        """
        breakeven = self.calculate_breakeven(leverage, position_size)
        required_move = breakeven * safety_margin
        
        is_profitable = expected_move > required_move
        
        if not is_profitable:
            logger.debug(f"盈利不足 - 預期: {expected_move:.4f}, 需要: {required_move:.4f}, 盈虧平衡: {breakeven:.4f}")
        
        return is_profitable


class DynamicLeverageAdjuster:
    """動態槓桿調整器"""
    
    def __init__(self, base_leverage=20):
        self.base_leverage = base_leverage
        
    def adjust_leverage(self, current_vpin: float, volatility: float, 
                       signal_strength: float) -> float:
        """
        根據市場狀態動態調整槓桿
        
        Args:
            current_vpin: 當前VPIN值
            volatility: 波動率（ATR百分比）
            signal_strength: 信號強度（0-1）
        
        Returns:
            float: 調整後的槓桿倍數
        """
        leverage_multiplier = 1.0
        
        # VPIN 調整
        if current_vpin > 0.7:
            leverage_multiplier = 0.5    # 高毒性減半槓桿
            logger.info(f"🔴 高VPIN ({current_vpin:.2f}) - 槓桿減半")
        elif current_vpin > 0.6:
            leverage_multiplier = 0.7
            logger.info(f"🟡 中高VPIN ({current_vpin:.2f}) - 槓桿降至70%")
        elif current_vpin < 0.3:
            leverage_multiplier = 1.2    # 低毒性增加槓桿
            logger.info(f"🟢 低VPIN ({current_vpin:.2f}) - 槓桿增至120%")
        else:
            leverage_multiplier = 1.0
        
        # 波動率調整
        if volatility > 0.03:            # 高波動（>3%）
            leverage_multiplier *= 0.6
            logger.info(f"⚡ 高波動 ({volatility:.2%}) - 槓桿再降至60%")
        elif volatility < 0.01:          # 低波動（<1%）
            leverage_multiplier *= 1.1
            logger.debug(f"📊 低波動 ({volatility:.2%}) - 槓桿微增至110%")
        
        # 信號強度調整
        if signal_strength > 0.8:
            leverage_multiplier *= 1.1   # 強信號適度增加
            logger.debug(f"💪 強信號 ({signal_strength:.2f}) - 槓桿微增")
        elif signal_strength < 0.5:
            leverage_multiplier *= 0.8   # 弱信號減少
            logger.debug(f"⚠️ 弱信號 ({signal_strength:.2f}) - 槓桿減少")
        
        # 計算最終槓桿（限制在5-25倍之間）
        final_leverage = min(25, max(5, self.base_leverage * leverage_multiplier))
        
        logger.info(f"📊 槓桿調整: {self.base_leverage}x → {final_leverage:.1f}x (倍數: {leverage_multiplier:.2f})")
        
        return final_leverage


class DynamicPositionSizer:
    """動態倉位調整器"""
    
    def __init__(self, base_size=0.5):
        self.base_size = base_size
        
    def adjust_position_size(self, leverage: float, confidence: float, 
                            market_regime: str) -> float:
        """
        根據信心度和市場狀態調整倉位
        
        Args:
            leverage: 當前槓桿倍數
            confidence: 信號信心度（0-1）
            market_regime: 市場狀態
        
        Returns:
            float: 調整後的倉位大小
        """
        size_multiplier = 1.0
        
        # 信心度調整
        if confidence > 0.8:
            size_multiplier = 1.2
            logger.debug(f"💎 高信心度 ({confidence:.2f}) - 倉位增至120%")
        elif confidence > 0.6:
            size_multiplier = 1.0
        else:
            size_multiplier = 0.7
            logger.debug(f"⚠️ 低信心度 ({confidence:.2f}) - 倉位降至70%")
        
        # 市場狀態調整
        if market_regime == "TRENDING":
            size_multiplier *= 1.1
            logger.info(f"📈 趨勢市場 - 倉位增至110%")
        elif market_regime == "VOLATILE":
            size_multiplier *= 0.7
            logger.info(f"⚡ 波動市場 - 倉位降至70%")
        elif market_regime == "CONSOLIDATION":
            size_multiplier *= 0.5
            logger.info(f"📊 盤整市場 - 倉位降至50%")
        
        # 槓桿調整（高槓桿降低倉位）
        if leverage > 15:
            size_multiplier *= 0.8
            logger.debug(f"🔴 高槓桿 ({leverage}x) - 倉位降至80%")
        elif leverage < 10:
            size_multiplier *= 1.2
            logger.debug(f"🟢 低槓桿 ({leverage}x) - 倉位增至120%")
        
        # 計算最終倉位（限制在20%-70%之間）
        final_size = min(0.7, max(0.2, self.base_size * size_multiplier))
        
        logger.info(f"📊 倉位調整: {self.base_size:.0%} → {final_size:.0%} (倍數: {size_multiplier:.2f})")
        
        return final_size


class DynamicTPSLAdjuster:
    """動態止盈止損調整器"""
    
    def __init__(self, base_tp=0.002, base_sl=0.001):
        self.base_tp = base_tp  # 基礎止盈 0.2%
        self.base_sl = base_sl  # 基礎止損 0.1%
        self.atr_history = []
        
    def update_atr(self, current_atr: float):
        """更新ATR歷史"""
        self.atr_history.append(current_atr)
        if len(self.atr_history) > 20:
            self.atr_history.pop(0)
    
    def get_atr_ratio(self) -> float:
        """計算當前ATR與平均ATR的比率"""
        if len(self.atr_history) < 2:
            return 1.0
        
        current = self.atr_history[-1]
        average = np.mean(self.atr_history)
        
        return current / average if average > 0 else 1.0
    
    def adjust_tp_sl(self, leverage: float, volatility: float, 
                     signal_duration: int) -> Tuple[float, float]:
        """
        根據波動率和信號持續性調整止盈止損
        
        Args:
            leverage: 當前槓桿倍數
            volatility: 波動率
            signal_duration: 信號持續時間（分鐘）
        
        Returns:
            Tuple[float, float]: (止盈百分比, 止損百分比)
        """
        tp = self.base_tp
        sl = self.base_sl
        
        # 波動率調整
        atr_ratio = self.get_atr_ratio()
        if atr_ratio > 1.5:
            tp *= 1.3  # 高波動放大止盈
            sl *= 1.2  # 高波動放大止損
            logger.info(f"⚡ 高波動 (ATR比率: {atr_ratio:.2f}) - 擴大TP/SL")
        elif atr_ratio < 0.7:
            tp *= 0.8  # 低波動縮小止盈
            sl *= 0.8  # 低波動縮小止損
            logger.debug(f"📊 低波動 (ATR比率: {atr_ratio:.2f}) - 縮小TP/SL")
        
        # 信號持續性調整
        if signal_duration > 5:  # 信號持續5分鐘以上
            tp *= 1.2            # 趨勢穩定，放大止盈
            logger.info(f"⏱️ 信號持續 {signal_duration} 分鐘 - 放大止盈")
        
        # 槓桿調整（高槓桿緊止損）
        if leverage > 15:
            sl *= 0.8            # 高槓桿緊止損
            logger.debug(f"🔴 高槓桿 ({leverage}x) - 緊縮止損")
        
        # 限制範圍
        final_tp = min(0.035, max(0.008, tp))  # 0.8%-3.5%
        final_sl = min(0.020, max(0.004, sl))  # 0.4%-2.0%
        
        logger.info(f"🎯 TP/SL調整: TP {final_tp:.2%} | SL {final_sl:.2%}")
        
        return final_tp, final_sl


class TradingScheme:
    """交易方案配置"""
    
    SCHEME_A = {
        "name": "方案A - 保守穩健",
        "trades_per_hour": (2, 3),
        "leverage_range": (10, 15),
        "position_range": (0.30, 0.40),
        "price_tp": 0.0012,  # 0.12%
        "price_sl": 0.0008,  # 0.08%
        "profit_loss_ratio": 1.5,
        "hourly_target": 0.015,  # 1.5%
        "win_rate_target": 0.75,
        "max_drawdown": 0.08,
        "time_to_double": 48  # 小時
    }
    
    SCHEME_B = {
        "name": "方案B - 平衡成長",
        "trades_per_hour": (3, 4),
        "leverage_range": (15, 20),
        "position_range": (0.40, 0.45),
        "price_tp": 0.0015,  # 0.15%
        "price_sl": 0.0009,  # 0.09%
        "profit_loss_ratio": 1.7,
        "hourly_target": 0.020,  # 2.0%
        "win_rate_target": 0.72,
        "max_drawdown": 0.12,
        "time_to_double": 36  # 小時
    }
    
    SCHEME_C = {
        "name": "方案C - 積極加速",
        "trades_per_hour": (4, 5),
        "leverage_range": (18, 25),
        "position_range": (0.45, 0.50),
        "price_tp": 0.0020,  # 0.20%
        "price_sl": 0.0010,  # 0.10%
        "profit_loss_ratio": 2.0,
        "hourly_target": 0.030,  # 3.0%
        "win_rate_target": 0.70,
        "max_drawdown": 0.15,
        "time_to_double": 24  # 小時
    }
    
    @classmethod
    def get_scheme(cls, scheme_name: str) -> Dict:
        """獲取方案配置"""
        schemes = {
            "A": cls.SCHEME_A,
            "B": cls.SCHEME_B,
            "C": cls.SCHEME_C
        }
        return schemes.get(scheme_name, cls.SCHEME_B)


class StrategySelector:
    """策略方案選擇器"""
    
    def __init__(self):
        self.current_scheme = "B"  # 默認從B方案開始
        self.trade_history = []
        self.scheme_start_time = datetime.now()
        
    def analyze_market_regime(self, market_regime: str) -> str:
        """分析市場狀態"""
        if market_regime == "VOLATILE":
            return "RISK_AVERSE"
        elif market_regime == "TRENDING":
            return "FAVORABLE"
        else:
            return "NEUTRAL"
    
    def analyze_account_status(self, current_balance: float, initial_balance: float) -> str:
        """分析賬戶狀態"""
        profit_ratio = (current_balance - initial_balance) / initial_balance
        
        if profit_ratio < -0.1:  # 虧損超過10%
            return "RISK_AVERSE"
        elif profit_ratio > 0.2:  # 盈利超過20%
            return "HEALTHY"
        else:
            return "NEUTRAL"
    
    def analyze_trading_performance(self) -> str:
        """分析交易表現"""
        if len(self.trade_history) < 5:
            return "INSUFFICIENT_DATA"
        
        recent_trades = self.trade_history[-10:]
        
        # 計算勝率
        winning_trades = sum(1 for t in recent_trades if t['profit'] > 0)
        win_rate = winning_trades / len(recent_trades)
        
        # 檢查連續虧損
        consecutive_losses = 0
        for t in reversed(recent_trades):
            if t['profit'] < 0:
                consecutive_losses += 1
            else:
                break
        
        if consecutive_losses >= 3:
            return "RECENT_LOSSES"
        elif win_rate > 0.75:
            return "CONSISTENT_PROFIT"
        else:
            return "NEUTRAL"
    
    def select_optimal_scheme(self, market_regime: str, current_balance: float, 
                             initial_balance: float) -> str:
        """
        選擇最佳方案
        
        Returns:
            str: "A", "B", or "C"
        """
        market_status = self.analyze_market_regime(market_regime)
        account_status = self.analyze_account_status(current_balance, initial_balance)
        performance = self.analyze_trading_performance()
        
        logger.info(f"📊 狀態評估 - 市場: {market_status}, 賬戶: {account_status}, 表現: {performance}")
        
        # 保守條件 - 使用A方案
        if (market_status == "RISK_AVERSE" or 
            account_status == "RISK_AVERSE" or 
            performance == "RECENT_LOSSES"):
            return "A"
        
        # 積極條件 - 使用C方案
        elif (market_status == "FAVORABLE" and
              account_status == "HEALTHY" and
              performance == "CONSISTENT_PROFIT"):
            return "C"
        
        # 默認 - 使用B方案
        else:
            return "B"
    
    def should_upgrade_strategy(self) -> bool:
        """判斷是否可以升級到更積極的方案"""
        if len(self.trade_history) < 10:
            return False
        
        recent_trades = self.trade_history[-10:]
        
        # 連續盈利檢查
        consecutive_wins = 0
        for t in reversed(recent_trades):
            if t['profit'] > 0:
                consecutive_wins += 1
            else:
                break
        
        # 勝率檢查
        winning_trades = sum(1 for t in recent_trades if t['profit'] > 0)
        win_rate = winning_trades / len(recent_trades)
        
        conditions_met = 0
        if consecutive_wins >= 5:
            conditions_met += 1
            logger.info(f"✅ 連續盈利 {consecutive_wins} 次")
        
        if win_rate > 0.80:
            conditions_met += 1
            logger.info(f"✅ 勝率達標 {win_rate:.1%}")
        
        should_upgrade = conditions_met >= 2
        
        if should_upgrade:
            logger.info(f"🚀 滿足升級條件 ({conditions_met}/2)")
        
        return should_upgrade
    
    def should_downgrade_strategy(self, current_balance: float, 
                                  initial_balance: float) -> bool:
        """判斷是否需要降級到更保守的方案"""
        if len(self.trade_history) < 5:
            return False
        
        recent_trades = self.trade_history[-10:]
        
        # 連續虧損檢查
        consecutive_losses = 0
        for t in reversed(recent_trades):
            if t['profit'] < 0:
                consecutive_losses += 1
            else:
                break
        
        # 單日虧損檢查
        daily_profit_ratio = (current_balance - initial_balance) / initial_balance
        
        conditions = []
        if consecutive_losses >= 3:
            conditions.append(f"連續虧損{consecutive_losses}次")
        
        if daily_profit_ratio < -0.10:
            conditions.append(f"虧損{daily_profit_ratio:.1%}")
        
        should_downgrade = len(conditions) > 0
        
        if should_downgrade:
            logger.warning(f"⚠️ 觸發降級條件: {', '.join(conditions)}")
        
        return should_downgrade
    
    def should_stop_trading(self, current_balance: float, initial_balance: float,
                           current_vpin: float, network_latency: float) -> bool:
        """判斷是否需要完全停止交易"""
        stop_conditions = []
        
        # 總虧損檢查
        total_loss_ratio = (current_balance - initial_balance) / initial_balance
        if total_loss_ratio < -0.30:
            stop_conditions.append(f"總虧損達{total_loss_ratio:.1%}")
        
        # VPIN檢查
        if current_vpin > 0.85:
            stop_conditions.append(f"VPIN過高({current_vpin:.2f})")
        
        # 網絡延遲檢查
        if network_latency > 200:
            stop_conditions.append(f"網絡延遲過高({network_latency}ms)")
        
        should_stop = len(stop_conditions) > 0
        
        if should_stop:
            logger.error(f"🛑 觸發停止交易條件: {', '.join(stop_conditions)}")
        
        return should_stop
    
    def update_scheme(self, new_scheme: str):
        """更新當前方案"""
        if new_scheme != self.current_scheme:
            old_scheme = self.current_scheme
            self.current_scheme = new_scheme
            self.scheme_start_time = datetime.now()
            logger.info(f"🔄 方案切換: {old_scheme} → {new_scheme}")
    
    def add_trade_result(self, profit: float, entry_time: datetime):
        """添加交易結果"""
        self.trade_history.append({
            'profit': profit,
            'time': entry_time
        })
        
        # 只保留最近50次交易
        if len(self.trade_history) > 50:
            self.trade_history.pop(0)


class Mode14Strategy:
    """M14策略主引擎"""
    
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
        self.strategy_selector = StrategySelector()
        
        # 狀態變量
        self.current_leverage = config.get('base_leverage', 20)
        self.current_position_size = config.get('max_position_size', 0.5)
        self.current_tp = 0.002
        self.current_sl = 0.001
        
        logger.info("✅ M14 動態槓桿優化策略初始化完成")
    
    def should_enter_trade(self, market_data: Dict) -> Tuple[bool, str]:
        """
        判斷是否應該進場
        
        Args:
            market_data: {
                'vpin': float,
                'spread': float,  # 或 'spread_bps'
                'depth': float,   # 或 'total_depth'
                'obi': float,
                'volume': float,  # 或 'signed_volume'
                'avg_volume': float,
                'price': float,
                'mtf_signals': Dict[str, float]  # 多時間框架信號
            }
        
        Returns:
            Tuple[bool, str]: (是否進場, 原因)
        """
        # 🔧 兼容性修復：確保必要的鍵存在（支持 paper_trading_system 的鍵名）
        if 'spread' not in market_data and 'spread_bps' in market_data:
            market_data['spread'] = market_data['spread_bps']
        elif 'spread' not in market_data:
            market_data['spread'] = 10.0  # 默認值
        
        if 'depth' not in market_data and 'total_depth' in market_data:
            market_data['depth'] = market_data['total_depth']
        elif 'depth' not in market_data:
            market_data['depth'] = 5.0  # 默認值
        
        if 'volume' not in market_data and 'signed_volume' in market_data:
            market_data['volume'] = abs(market_data['signed_volume'])  # 使用絕對值作為成交量
        elif 'volume' not in market_data:
            market_data['volume'] = 1.0  # 默認值
        
        if 'avg_volume' not in market_data:
            market_data['avg_volume'] = 1.0  # 默認值
        
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
        
        # 多重過濾條件
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
        expected_move = 0.002  # 預期0.2%價格變動
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
                'signal_score': float
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
            'signal_score': signal_score
        }
    
    def update_scheme_if_needed(self, current_balance: float, initial_balance: float,
                                market_regime: str, current_vpin: float, 
                                network_latency: float = 0) -> str:
        """
        更新交易方案（如果需要）
        
        Returns:
            str: 當前方案 ("A", "B", or "C")
        """
        # 檢查是否需要停止交易
        if self.strategy_selector.should_stop_trading(
            current_balance, initial_balance, current_vpin, network_latency
        ):
            logger.error("🛑 觸發停止交易條件")
            return "STOP"
        
        # 選擇最佳方案
        optimal_scheme = self.strategy_selector.select_optimal_scheme(
            market_regime, current_balance, initial_balance
        )
        
        current_scheme = self.strategy_selector.current_scheme
        
        # 檢查升級條件
        if optimal_scheme > current_scheme and self.strategy_selector.should_upgrade_strategy():
            self.strategy_selector.update_scheme(optimal_scheme)
            logger.info(f"🚀 策略升級: {current_scheme} → {optimal_scheme}")
            
        # 檢查降級條件
        elif optimal_scheme < current_scheme and self.strategy_selector.should_downgrade_strategy(
            current_balance, initial_balance
        ):
            self.strategy_selector.update_scheme(optimal_scheme)
            logger.warning(f"⬇️ 策略降級: {current_scheme} → {optimal_scheme}")
        
        return self.strategy_selector.current_scheme
    
    def get_current_scheme_config(self) -> Dict:
        """獲取當前方案配置"""
        return TradingScheme.get_scheme(self.strategy_selector.current_scheme)
    
    def record_trade_result(self, profit: float, entry_time: datetime):
        """記錄交易結果"""
        self.strategy_selector.add_trade_result(profit, entry_time)
