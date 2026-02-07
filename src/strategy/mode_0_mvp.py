"""
Mode 0 - Phase 0 MVP Strategy
簡單 MA7/MA25 金叉死叉 + RSI + Volume 確認
目標：快速驗證整合後系統可行性

作者: Phase 0 MVP
日期: 2025-11-14
"""

import logging
import numpy as np
from typing import Dict, Optional, Tuple
from datetime import datetime
import talib

logger = logging.getLogger(__name__)


class Mode0MVPStrategy:
    """
    Phase 0 MVP 策略
    
    進場條件（LONG）：
    1. MA7 上穿 MA25（金叉）
    2. RSI 在 40-70 之間（避免超買超賣）
    3. Volume > 近20根平均 Volume 的 1.2倍（成交量確認）
    4. 價格在 MA25 上方
    
    進場條件（SHORT）：
    1. MA7 下穿 MA25（死叉）
    2. RSI 在 30-60 之間
    3. Volume > 近20根平均 Volume 的 1.2倍
    4. 價格在 MA25 下方
    
    出場條件：
    - 止盈：0.5% (固定)
    - 止損：0.25% (固定)
    - 時間止損：30分鐘未盈利則出場
    """
    
    def __init__(self, config: Dict):
        """
        初始化策略
        
        Args:
            config: 策略配置字典
        """
        self.name = "Mode0_MVP"
        self.config = config
        
        # 參數設定
        self.ma_fast = config.get('ma_fast', 7)
        self.ma_slow = config.get('ma_slow', 25)
        self.rsi_period = config.get('rsi_period', 14)
        self.volume_multiplier = config.get('volume_multiplier', 1.2)
        
        # RSI 範圍
        self.rsi_long_min = config.get('rsi_long_min', 40)
        self.rsi_long_max = config.get('rsi_long_max', 70)
        self.rsi_short_min = config.get('rsi_short_min', 30)
        self.rsi_short_max = config.get('rsi_short_max', 60)
        
        # 止盈止損
        self.take_profit_pct = config.get('take_profit_pct', 0.005)  # 0.5%
        self.stop_loss_pct = config.get('stop_loss_pct', 0.0025)  # 0.25%
        self.time_stop_minutes = config.get('time_stop_minutes', 30)
        
        # 倉位和槓桿
        self.position_size = config.get('position_size', 0.3)  # 30% 資金
        self.leverage = config.get('leverage', 3)  # 3x 槓桿
        
        # 數據緩存
        self.price_history = []
        self.volume_history = []
        self.max_history = 50  # 保留最近50根K線
        
        # 持倉信息
        self.current_position = None
        self.entry_time = None
        
        # 信號計數（用於去重）
        self.last_signal_time = None
        self.signal_cooldown_seconds = 60  # 信號冷卻時間
        
        logger.info(f"[{self.name}] 策略初始化完成")
        logger.info(f"  MA: {self.ma_fast}/{self.ma_slow}")
        logger.info(f"  RSI期間: {self.rsi_period}")
        logger.info(f"  止盈/止損: {self.take_profit_pct:.2%}/{self.stop_loss_pct:.2%}")
        logger.info(f"  倉位/槓桿: {self.position_size:.0%}/{self.leverage}x")
    
    def update_data(self, close: float, volume: float):
        """
        更新歷史數據
        
        Args:
            close: 收盤價
            volume: 成交量
        """
        self.price_history.append(close)
        self.volume_history.append(volume)
        
        # 限制歷史長度
        if len(self.price_history) > self.max_history:
            self.price_history.pop(0)
        if len(self.volume_history) > self.max_history:
            self.volume_history.pop(0)
    
    def calculate_indicators(self) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """
        計算技術指標
        
        Returns:
            (ma_fast, ma_slow, rsi, avg_volume)
        """
        if len(self.price_history) < self.ma_slow:
            return None, None, None, None
        
        prices = np.array(self.price_history, dtype=float)
        volumes = np.array(self.volume_history, dtype=float)
        
        # 計算移動平均
        ma_fast = talib.SMA(prices, timeperiod=self.ma_fast)[-1]
        ma_slow = talib.SMA(prices, timeperiod=self.ma_slow)[-1]
        
        # 計算RSI
        rsi = talib.RSI(prices, timeperiod=self.rsi_period)[-1]
        
        # 計算平均成交量
        avg_volume = np.mean(volumes[-20:]) if len(volumes) >= 20 else volumes[-1]
        
        return ma_fast, ma_slow, rsi, avg_volume
    
    def check_golden_cross(self) -> bool:
        """
        檢查是否發生金叉（MA7 上穿 MA25）
        
        Returns:
            True if 金叉
        """
        if len(self.price_history) < self.ma_slow + 2:
            return False
        
        prices = np.array(self.price_history, dtype=float)
        
        # 前一根K線的MA
        ma_fast_prev = talib.SMA(prices[:-1], timeperiod=self.ma_fast)[-1]
        ma_slow_prev = talib.SMA(prices[:-1], timeperiod=self.ma_slow)[-1]
        
        # 當前K線的MA
        ma_fast_curr = talib.SMA(prices, timeperiod=self.ma_fast)[-1]
        ma_slow_curr = talib.SMA(prices, timeperiod=self.ma_slow)[-1]
        
        # 金叉：前一根 MA7 < MA25，當前 MA7 > MA25
        is_golden = (ma_fast_prev <= ma_slow_prev) and (ma_fast_curr > ma_slow_curr)
        
        return is_golden
    
    def check_death_cross(self) -> bool:
        """
        檢查是否發生死叉（MA7 下穿 MA25）
        
        Returns:
            True if 死叉
        """
        if len(self.price_history) < self.ma_slow + 2:
            return False
        
        prices = np.array(self.price_history, dtype=float)
        
        # 前一根K線的MA
        ma_fast_prev = talib.SMA(prices[:-1], timeperiod=self.ma_fast)[-1]
        ma_slow_prev = talib.SMA(prices[:-1], timeperiod=self.ma_slow)[-1]
        
        # 當前K線的MA
        ma_fast_curr = talib.SMA(prices, timeperiod=self.ma_fast)[-1]
        ma_slow_curr = talib.SMA(prices, timeperiod=self.ma_slow)[-1]
        
        # 死叉：前一根 MA7 > MA25，當前 MA7 < MA25
        is_death = (ma_fast_prev >= ma_slow_prev) and (ma_fast_curr < ma_slow_curr)
        
        return is_death
    
    def should_enter_long(self) -> Tuple[bool, str]:
        """
        判斷是否應該做多
        
        Returns:
            (should_enter, reason)
        """
        # 數據不足
        if len(self.price_history) < self.ma_slow:
            return False, "數據不足"
        
        # 已有持倉
        if self.current_position is not None:
            return False, "已有持倉"
        
        # 信號冷卻
        if self.last_signal_time:
            elapsed = (datetime.now() - self.last_signal_time).total_seconds()
            if elapsed < self.signal_cooldown_seconds:
                return False, f"信號冷卻中 ({int(elapsed)}s)"
        
        # 計算指標
        ma_fast, ma_slow, rsi, avg_volume = self.calculate_indicators()
        
        if None in [ma_fast, ma_slow, rsi, avg_volume]:
            return False, "指標計算失敗"
        
        current_price = self.price_history[-1]
        current_volume = self.volume_history[-1]
        
        # 條件1：金叉
        has_golden_cross = self.check_golden_cross()
        if not has_golden_cross:
            return False, "無金叉信號"
        
        # 條件2：RSI 在合理範圍
        if not (self.rsi_long_min <= rsi <= self.rsi_long_max):
            return False, f"RSI 不在範圍 ({rsi:.1f})"
        
        # 條件3：成交量放大
        if current_volume < avg_volume * self.volume_multiplier:
            return False, f"成交量不足 ({current_volume/avg_volume:.2f}x)"
        
        # 條件4：價格在 MA25 上方
        if current_price < ma_slow:
            return False, f"價格低於MA25"
        
        # 所有條件滿足
        reason = (
            f"✅ 做多信號: 金叉 | RSI={rsi:.1f} | "
            f"Vol={current_volume/avg_volume:.2f}x | Price>{ma_slow:.2f}"
        )
        return True, reason
    
    def should_enter_short(self) -> Tuple[bool, str]:
        """
        判斷是否應該做空
        
        Returns:
            (should_enter, reason)
        """
        # 數據不足
        if len(self.price_history) < self.ma_slow:
            return False, "數據不足"
        
        # 已有持倉
        if self.current_position is not None:
            return False, "已有持倉"
        
        # 信號冷卻
        if self.last_signal_time:
            elapsed = (datetime.now() - self.last_signal_time).total_seconds()
            if elapsed < self.signal_cooldown_seconds:
                return False, f"信號冷卻中 ({int(elapsed)}s)"
        
        # 計算指標
        ma_fast, ma_slow, rsi, avg_volume = self.calculate_indicators()
        
        if None in [ma_fast, ma_slow, rsi, avg_volume]:
            return False, "指標計算失敗"
        
        current_price = self.price_history[-1]
        current_volume = self.volume_history[-1]
        
        # 條件1：死叉
        has_death_cross = self.check_death_cross()
        if not has_death_cross:
            return False, "無死叉信號"
        
        # 條件2：RSI 在合理範圍
        if not (self.rsi_short_min <= rsi <= self.rsi_short_max):
            return False, f"RSI 不在範圍 ({rsi:.1f})"
        
        # 條件3：成交量放大
        if current_volume < avg_volume * self.volume_multiplier:
            return False, f"成交量不足 ({current_volume/avg_volume:.2f}x)"
        
        # 條件4：價格在 MA25 下方
        if current_price > ma_slow:
            return False, f"價格高於MA25"
        
        # 所有條件滿足
        reason = (
            f"✅ 做空信號: 死叉 | RSI={rsi:.1f} | "
            f"Vol={current_volume/avg_volume:.2f}x | Price<{ma_slow:.2f}"
        )
        return True, reason
    
    def should_exit_position(self, current_price: float) -> Tuple[bool, str]:
        """
        判斷是否應該出場
        
        Args:
            current_price: 當前價格
            
        Returns:
            (should_exit, reason)
        """
        if self.current_position is None:
            return False, "無持倉"
        
        entry_price = self.current_position['entry_price']
        direction = self.current_position['direction']
        
        # 計算盈虧百分比
        if direction == "LONG":
            pnl_pct = (current_price - entry_price) / entry_price
        else:  # SHORT
            pnl_pct = (entry_price - current_price) / entry_price
        
        # 止盈
        if pnl_pct >= self.take_profit_pct:
            return True, f"✅ 止盈 ({pnl_pct:.2%})"
        
        # 止損
        if pnl_pct <= -self.stop_loss_pct:
            return True, f"❌ 止損 ({pnl_pct:.2%})"
        
        # 時間止損
        if self.entry_time:
            holding_minutes = (datetime.now() - self.entry_time).total_seconds() / 60
            if holding_minutes >= self.time_stop_minutes and pnl_pct < 0:
                return True, f"⏰ 時間止損 ({holding_minutes:.1f}分鐘, {pnl_pct:.2%})"
        
        return False, f"持倉中 ({pnl_pct:.2%})"
    
    def make_decision(self, market_data: Dict) -> Dict:
        """
        主決策函數
        
        Args:
            market_data: 市場數據字典，包含 close, volume 等
            
        Returns:
            決策字典 {action, direction, size, leverage, reason}
        """
        # 更新數據
        close = market_data.get('close', 0)
        volume = market_data.get('volume', 0)
        
        if close <= 0 or volume <= 0:
            return {
                'action': 'HOLD',
                'direction': None,
                'size': 0,
                'leverage': self.leverage,
                'reason': '數據無效'
            }
        
        self.update_data(close, volume)
        
        # 檢查出場
        if self.current_position:
            should_exit, reason = self.should_exit_position(close)
            if should_exit:
                logger.info(f"[{self.name}] {reason}")
                self.current_position = None
                self.entry_time = None
                return {
                    'action': 'CLOSE',
                    'direction': None,
                    'size': 0,
                    'leverage': self.leverage,
                    'reason': reason
                }
        
        # 檢查進場 - 做多
        should_long, long_reason = self.should_enter_long()
        if should_long:
            logger.info(f"[{self.name}] {long_reason}")
            self.current_position = {
                'direction': 'LONG',
                'entry_price': close
            }
            self.entry_time = datetime.now()
            self.last_signal_time = datetime.now()
            
            return {
                'action': 'OPEN',
                'direction': 'LONG',
                'size': self.position_size,
                'leverage': self.leverage,
                'reason': long_reason,
                'take_profit_pct': self.take_profit_pct,
                'stop_loss_pct': self.stop_loss_pct
            }
        
        # 檢查進場 - 做空
        should_short, short_reason = self.should_enter_short()
        if should_short:
            logger.info(f"[{self.name}] {short_reason}")
            self.current_position = {
                'direction': 'SHORT',
                'entry_price': close
            }
            self.entry_time = datetime.now()
            self.last_signal_time = datetime.now()
            
            return {
                'action': 'OPEN',
                'direction': 'SHORT',
                'size': self.position_size,
                'leverage': self.leverage,
                'reason': short_reason,
                'take_profit_pct': self.take_profit_pct,
                'stop_loss_pct': self.stop_loss_pct
            }
        
        # 無操作
        return {
            'action': 'HOLD',
            'direction': None,
            'size': 0,
            'leverage': self.leverage,
            'reason': '等待信號'
        }
    
    def get_strategy_info(self) -> Dict:
        """
        獲取策略信息
        
        Returns:
            策略信息字典
        """
        return {
            'name': self.name,
            'type': 'trend_following',
            'timeframe': '1m',
            'position_size': self.position_size,
            'leverage': self.leverage,
            'take_profit': self.take_profit_pct,
            'stop_loss': self.stop_loss_pct,
            'current_position': self.current_position
        }


# ==================== 使用範例 ====================
if __name__ == "__main__":
    # 配置日誌
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 創建策略
    config = {
        'ma_fast': 7,
        'ma_slow': 25,
        'rsi_period': 14,
        'take_profit_pct': 0.005,
        'stop_loss_pct': 0.0025,
        'position_size': 0.3,
        'leverage': 3
    }
    
    strategy = Mode0MVPStrategy(config)
    
    # 模擬數據測試
    print("\n=== Mode 0 MVP 策略測試 ===\n")
    
    # 模擬50根K線（上升趨勢）
    np.random.seed(42)
    base_price = 50000
    
    for i in range(50):
        # 上升趨勢 + 隨機波動
        price = base_price + i * 10 + np.random.randn() * 50
        volume = 1000 + np.random.randn() * 100
        
        market_data = {
            'close': price,
            'volume': abs(volume),
            'timestamp': datetime.now()
        }
        
        decision = strategy.make_decision(market_data)
        
        if decision['action'] != 'HOLD':
            print(f"K線 {i}: {decision['action']} {decision.get('direction', '')} - {decision['reason']}")
    
    # 策略信息
    print("\n=== 策略信息 ===")
    info = strategy.get_strategy_info()
    for key, value in info.items():
        print(f"{key}: {value}")
