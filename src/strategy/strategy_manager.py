"""
策略管理器 (Strategy Manager)
=============================

管理多種交易策略的配置、風控和執行邏輯

策略類型:
- Mode 0: 基準線（無風控）
- Mode 1: VPIN 風控
- Mode 2: 流動性風控  
- Mode 3: 完整風控
- Mode 4: 趨勢跟隨
- Mode 5: 均值回歸
- Mode 6: 動態止損
- Mode 7: 混合策略
"""

import json
from typing import Dict, List, Tuple, Optional
from pathlib import Path


class TradingStrategy:
    """交易策略基類"""
    
    def __init__(self, config: dict):
        self.name = config['name']
        self.emoji = config['emoji']
        self.description = config['description']
        self.leverage = config['leverage']
        self.position_size = config['position_size']
        self.enabled = config.get('enabled', True)
        self.risk_control = config['risk_control']
        # 新增：VPIN 安全閾值（默認 0.5）
        self.vpin_safe_threshold = config.get('vpin_safe_threshold', 0.5)
    
    def get_dynamic_vpin_threshold(self, market_data: dict) -> float:
        """
        根據市場波動性動態調整VPIN閾值
        
        Args:
            market_data: 市場數據
            
        Returns:
            動態調整後的VPIN閾值（0.3-0.7範圍）
        """
        base_threshold = self.vpin_safe_threshold
        
        # 市場波動性指標
        obi_velocity = abs(market_data.get('obi_velocity', 0))
        spread_bps = market_data.get('spread_bps', 0)
        
        # 波動性調整係數
        volatility_factor = 1.0
        
        # 快速變化時降低閾值（更保守）
        if obi_velocity > 1.0:
            volatility_factor *= 0.7  # 降低30%
        elif obi_velocity > 0.5:
            volatility_factor *= 0.85  # 降低15%
        
        # 流動性差時降低閾值（更保守）
        if spread_bps > 10:
            volatility_factor *= 0.8  # 降低20%
        elif spread_bps > 5:
            volatility_factor *= 0.9  # 降低10%
        
        dynamic_threshold = base_threshold * volatility_factor
        return max(0.3, min(0.7, dynamic_threshold))  # 限制在0.3-0.7範圍
    
    def get_market_state(self, market_data: dict) -> str:
        """
        識別當前市場狀態
        
        Returns:
            市場狀態：CALM, NORMAL, VOLATILE, EXTREME
        """
        vpin = market_data.get('vpin', 0.3)
        obi_velocity = abs(market_data.get('obi_velocity', 0))
        
        if vpin < 0.3 and obi_velocity < 0.5:
            return "CALM"  # 平靜
        elif vpin < 0.5 and obi_velocity < 1.0:
            return "NORMAL"  # 正常
        elif vpin < 0.7 or obi_velocity < 2.0:
            return "VOLATILE"  # 波動
        else:
            return "EXTREME"  # 極端
    
    def enhanced_vpin_filter(self, market_data: dict) -> Tuple[bool, Optional[str]]:
        """
        增強版VPIN過濾器：分層決策機制
        
        Returns:
            (is_safe, reason): 是否安全及原因/警告
        """
        vpin = market_data.get('vpin', 0.3)
        
        # 動態閾值
        dynamic_threshold = self.get_dynamic_vpin_threshold(market_data)
        
        # 分層過濾
        if vpin < 0.3:
            return True, None  # 安全，正常交易
        
        elif vpin < 0.5:
            # 略高但可接受，給予警告
            if vpin > dynamic_threshold:
                return False, f"VPIN略高 ({vpin:.3f} > {dynamic_threshold:.3f}，動態閾值)"
            return True, "VPIN略高，注意風險"
        
        elif vpin < 0.7:
            # 需要額外確認信號強度
            obi_strength = abs(market_data.get('obi', 0))
            if obi_strength > 0.8:  # 強烈信號
                return True, f"VPIN較高 ({vpin:.3f})，但信號強烈"
            else:
                return False, f"VPIN過高 ({vpin:.3f})且信號不足 (OBI={obi_strength:.3f})"
        
        else:  # vpin >= 0.7
            return False, f"VPIN危險 ({vpin:.3f} >= 0.7)，禁止交易"
    
    def check_vpin_filter(self, market_data: dict) -> Tuple[bool, Optional[str]]:
        """
        VPIN 過濾器：使用增強版過濾邏輯
        
        Returns:
            (is_safe, reason): 是否安全及原因
        """
        return self.enhanced_vpin_filter(market_data)
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """
        檢查是否可以進場（默認實現）
        子類可以覆蓋此方法來實現自定義風控邏輯
        
        Returns:
            Tuple[bool, List[str]]: (是否可以進場, 阻擋原因列表)
        """
        # 默認實現：允許所有交易
        return True, []
    
    def adjust_signal(self, signal: dict, market_data: dict) -> dict:
        """根據策略調整信號"""
        return signal
    
    def get_stop_loss(self, market_data: dict) -> float:
        """獲取止損百分比"""
        return self.risk_control.get('stop_loss', 0.05)
    
    def get_take_profit(self, market_data: dict) -> float:
        """獲取止盈百分比"""
        return self.risk_control.get('take_profit', 0.08)


class BaselineStrategy(TradingStrategy):
    """Mode 0: 基準線策略（無風控）"""
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """無風控，所有信號都通過"""
        return True, []
    
    def adjust_signal(self, signal: dict, market_data: dict) -> dict:
        """強制交易 NEUTRAL 信號"""
        if signal['direction'] == 'NEUTRAL':
            obi = market_data.get('obi', 0)
            forced_direction = 'LONG' if obi >= 0 else 'SHORT'
            signal = signal.copy()
            signal['direction'] = forced_direction
            signal['forced'] = True
        return signal


class ReverseStrategy(TradingStrategy):
    """Mode 0': 反向交易策略（逆向操作）"""
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """無風控，所有信號都通過"""
        return True, []
    
    def adjust_signal(self, signal: dict, market_data: dict) -> dict:
        """
        反向交易邏輯：
        - 決策做多 → 實際做空
        - 決策做空 → 實際做多
        - 決策 NEUTRAL → 強制平倉（如果有持倉）
        """
        if signal['direction'] == 'NEUTRAL':
            # NEUTRAL 信號：強制交易相反方向
            obi = market_data.get('obi', 0)
            # 原本邏輯是 OBI>=0 做多，反向就是做空
            forced_direction = 'SHORT' if obi >= 0 else 'LONG'
            signal = signal.copy()
            signal['direction'] = forced_direction
            signal['forced'] = True
            signal['reversed'] = True
        else:
            # 反向信號
            signal = signal.copy()
            signal['direction'] = 'SHORT' if signal['direction'] == 'LONG' else 'LONG'
            signal['reversed'] = True
        
        return signal


class VPINStrategy(TradingStrategy):
    """Mode 1: VPIN 風控策略"""
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """只檢查 VPIN"""
        blocked_reasons = []
        
        if self.risk_control.get('vpin_check'):
            vpin = market_data.get('vpin', 0.3)
            threshold = self.risk_control.get('vpin_threshold', 0.7)
            
            if vpin > threshold:
                blocked_reasons.append(f"VPIN 過高 ({vpin:.3f} > {threshold})")
        
        return len(blocked_reasons) == 0, blocked_reasons


class LiquidityStrategy(TradingStrategy):
    """Mode 2: 流動性風控策略"""
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """只檢查流動性 + VPIN 安全過濾"""
        blocked_reasons = []
        
        # 新增：VPIN 安全過濾
        is_safe, reason = self.check_vpin_filter(market_data)
        if not is_safe:
            blocked_reasons.append(reason)
            return False, blocked_reasons
        
        if self.risk_control.get('liquidity_check'):
            spread_bps = market_data.get('spread_bps', 0)
            total_depth = market_data.get('total_depth', 0)
            
            spread_threshold = self.risk_control.get('spread_threshold', 10)
            depth_threshold = self.risk_control.get('depth_threshold', 3)
            
            if spread_bps > spread_threshold:
                blocked_reasons.append(f"Spread 過寬 ({spread_bps:.2f} bps)")
            if total_depth < depth_threshold:
                blocked_reasons.append(f"Depth 不足 ({total_depth:.2f} BTC)")
        
        return len(blocked_reasons) == 0, blocked_reasons


class FullControlStrategy(TradingStrategy):
    """Mode 3: 完整風控策略"""
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """檢查所有風控指標 + VPIN 安全過濾"""
        blocked_reasons = []
        
        # 新增：VPIN 安全過濾
        is_safe, reason = self.check_vpin_filter(market_data)
        if not is_safe:
            blocked_reasons.append(reason)
            return False, blocked_reasons
        
        # VPIN 檢查（額外的嚴格閾值）
        if self.risk_control.get('vpin_check'):
            vpin = market_data.get('vpin', 0.3)
            threshold = self.risk_control.get('vpin_threshold', 0.7)
            if vpin > threshold:
                blocked_reasons.append(f"VPIN 過高 ({vpin:.3f} > {threshold})")
        
        # 流動性檢查
        if self.risk_control.get('liquidity_check'):
            spread_bps = market_data.get('spread_bps', 0)
            total_depth = market_data.get('total_depth', 0)
            
            spread_threshold = self.risk_control.get('spread_threshold', 10)
            depth_threshold = self.risk_control.get('depth_threshold', 3)
            
            if spread_bps > spread_threshold:
                blocked_reasons.append(f"Spread 過寬 ({spread_bps:.2f} bps)")
            if total_depth < depth_threshold:
                blocked_reasons.append(f"Depth 不足 ({total_depth:.2f} BTC)")
        
        return len(blocked_reasons) == 0, blocked_reasons


class TrendFollowingStrategy(TradingStrategy):
    """Mode 4: 趨勢跟隨策略"""
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """確認趨勢強度 + VPIN 安全過濾"""
        blocked_reasons = []
        
        # 新增：VPIN 安全過濾
        is_safe, reason = self.check_vpin_filter(market_data)
        if not is_safe:
            blocked_reasons.append(reason)
            return False, blocked_reasons
        
        # 基本風控
        if self.risk_control.get('vpin_check'):
            vpin = market_data.get('vpin', 0.3)
            threshold = self.risk_control.get('vpin_threshold', 0.7)
            if vpin > threshold:
                blocked_reasons.append(f"VPIN 過高 ({vpin:.3f} > {threshold})")
        
        if self.risk_control.get('liquidity_check'):
            spread_bps = market_data.get('spread_bps', 0)
            total_depth = market_data.get('total_depth', 0)
            
            spread_threshold = self.risk_control.get('spread_threshold', 10)
            depth_threshold = self.risk_control.get('depth_threshold', 3)
            
            if spread_bps > spread_threshold:
                blocked_reasons.append(f"Spread 過寬 ({spread_bps:.2f} bps)")
            if total_depth < depth_threshold:
                blocked_reasons.append(f"Depth 不足 ({total_depth:.2f} BTC)")
        
        # 趨勢檢查
        if self.risk_control.get('trend_check'):
            obi_velocity = abs(market_data.get('obi_velocity', 0))
            signed_volume = abs(market_data.get('signed_volume', 0))
            
            velocity_threshold = self.risk_control.get('obi_velocity_threshold', 0.5)
            volume_threshold = self.risk_control.get('signed_volume_threshold', 3.0)
            
            # 需要有明顯的趨勢（快速變化 + 大成交量）
            if obi_velocity < velocity_threshold:
                blocked_reasons.append(f"趨勢不明顯 (Velocity={obi_velocity:.3f})")
            if signed_volume < volume_threshold:
                blocked_reasons.append(f"成交量不足 (SV={signed_volume:.2f})")
        
        return len(blocked_reasons) == 0, blocked_reasons


class MeanReversionStrategy(TradingStrategy):
    """Mode 5: 均值回歸策略"""
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """等待極端失衡 + VPIN 安全過濾"""
        blocked_reasons = []
        
        # 新增：VPIN 安全過濾（更嚴格）
        vpin = market_data.get('vpin', 0.3)
        vpin_safe = self.risk_control.get('vpin_safe_threshold', 0.4)
        if vpin > vpin_safe:
            blocked_reasons.append(f"VPIN 過高 ({vpin:.3f} > {vpin_safe}，均值回歸風險高)")
            return False, blocked_reasons
        
        # 基本風控（更嚴格）
        if self.risk_control.get('vpin_check'):
            threshold = self.risk_control.get('vpin_threshold', 0.6)
            if vpin > threshold:
                blocked_reasons.append(f"VPIN 過高 ({vpin:.3f} > {threshold})")
        
        if self.risk_control.get('liquidity_check'):
            spread_bps = market_data.get('spread_bps', 0)
            total_depth = market_data.get('total_depth', 0)
            
            spread_threshold = self.risk_control.get('spread_threshold', 8)
            depth_threshold = self.risk_control.get('depth_threshold', 5)
            
            if spread_bps > spread_threshold:
                blocked_reasons.append(f"Spread 過寬 ({spread_bps:.2f} bps)")
            if total_depth < depth_threshold:
                blocked_reasons.append(f"Depth 不足 ({total_depth:.2f} BTC)")
        
        # OBI 極端值檢查
        if self.risk_control.get('reverse_trade'):
            obi = market_data.get('obi', 0)
            threshold = self.risk_control.get('obi_extreme_threshold', 0.8)
            
            # 只在極端失衡時交易
            if abs(obi) < threshold:
                blocked_reasons.append(f"OBI 不夠極端 (|{obi:.3f}| < {threshold})")
        
        return len(blocked_reasons) == 0, blocked_reasons
    
    def adjust_signal(self, signal: dict, market_data: dict) -> dict:
        """反向交易"""
        if self.risk_control.get('reverse_trade'):
            signal = signal.copy()
            if signal['direction'] == 'LONG':
                signal['direction'] = 'SHORT'
            elif signal['direction'] == 'SHORT':
                signal['direction'] = 'LONG'
            signal['reversed'] = True
        return signal


class DynamicStopStrategy(TradingStrategy):
    """Mode 6: 動態止損策略"""
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """標準風控 + VPIN 安全過濾"""
        blocked_reasons = []
        
        # 新增：VPIN 安全過濾
        is_safe, reason = self.check_vpin_filter(market_data)
        if not is_safe:
            blocked_reasons.append(reason)
            return False, blocked_reasons
        
        if self.risk_control.get('vpin_check'):
            vpin = market_data.get('vpin', 0.3)
            threshold = self.risk_control.get('vpin_threshold', 0.7)
            if vpin > threshold:
                blocked_reasons.append(f"VPIN 過高 ({vpin:.3f} > {threshold})")
        
        if self.risk_control.get('liquidity_check'):
            spread_bps = market_data.get('spread_bps', 0)
            total_depth = market_data.get('total_depth', 0)
            
            spread_threshold = self.risk_control.get('spread_threshold', 10)
            depth_threshold = self.risk_control.get('depth_threshold', 3)
            
            if spread_bps > spread_threshold:
                blocked_reasons.append(f"Spread 過寬 ({spread_bps:.2f} bps)")
            if total_depth < depth_threshold:
                blocked_reasons.append(f"Depth 不足 ({total_depth:.2f} BTC)")
        
        return len(blocked_reasons) == 0, blocked_reasons
    
    def get_stop_loss(self, market_data: dict) -> float:
        """根據波動率動態調整止損"""
        if not self.risk_control.get('dynamic_stop'):
            return self.risk_control.get('stop_loss', 0.05)
        
        # 簡單波動率估算（基於 spread）
        spread_bps = market_data.get('spread_bps', 5)
        volatility = spread_bps / 100  # 轉換為百分比
        
        multiplier = self.risk_control.get('volatility_multiplier', 2.0)
        min_stop = self.risk_control.get('min_stop_loss', 0.03)
        max_stop = self.risk_control.get('max_stop_loss', 0.10)
        
        dynamic_stop = volatility * multiplier
        return max(min_stop, min(dynamic_stop, max_stop))


class HybridStrategy(TradingStrategy):
    """Mode 7: 混合策略"""
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """根據市場狀態動態調整 + VPIN 安全過濾"""
        blocked_reasons = []
        
        # 新增：VPIN 安全過濾
        is_safe, reason = self.check_vpin_filter(market_data)
        if not is_safe:
            blocked_reasons.append(reason)
            return False, blocked_reasons
        
        # 基本風控
        if self.risk_control.get('vpin_check'):
            vpin = market_data.get('vpin', 0.3)
            threshold = self.risk_control.get('vpin_threshold', 0.65)
            if vpin > threshold:
                blocked_reasons.append(f"VPIN 過高 ({vpin:.3f} > {threshold})")
        
        if self.risk_control.get('liquidity_check'):
            spread_bps = market_data.get('spread_bps', 0)
            total_depth = market_data.get('total_depth', 0)
            
            spread_threshold = self.risk_control.get('spread_threshold', 12)
            depth_threshold = self.risk_control.get('depth_threshold', 4)
            
            if spread_bps > spread_threshold:
                blocked_reasons.append(f"Spread 過寬 ({spread_bps:.2f} bps)")
            if total_depth < depth_threshold:
                blocked_reasons.append(f"Depth 不足 ({total_depth:.2f} BTC)")
        
        # 信心度檢查
        if self.risk_control.get('hybrid_mode'):
            confidence = signal.get('confidence', 0)
            threshold = self.risk_control.get('confidence_threshold', 0.6)
            
            if confidence < threshold:
                blocked_reasons.append(f"信心度不足 ({confidence:.3f} < {threshold})")
        
        # 趨勢確認
        if self.risk_control.get('trend_check'):
            obi = market_data.get('obi', 0)
            obi_velocity = market_data.get('obi_velocity', 0)
            
            # 信號和趨勢方向一致性檢查
            if signal['direction'] == 'LONG' and (obi < -0.3 or obi_velocity < 0):
                blocked_reasons.append("趨勢不一致（做多但賣壓強）")
            elif signal['direction'] == 'SHORT' and (obi > 0.3 or obi_velocity > 0):
                blocked_reasons.append("趨勢不一致（做空但買壓強）")
        
        return len(blocked_reasons) == 0, blocked_reasons


class TechnicalIndicatorStrategy(TradingStrategy):
    """Mode 8: 技術指標綜合策略"""
    
    def __init__(self, config: dict):
        super().__init__(config)
        # 延遲導入避免循環依賴
        from ..strategy.indicators import get_indicators
        self.indicators = get_indicators()
        self.price_history = []
        self.high_history = []
        self.low_history = []
        self.history_limit = 100  # 保留最近 100 根 K 線
    
    def update_price_history(self, market_data: dict):
        """更新價格歷史（用於計算技術指標）"""
        price = market_data.get('price', 0)
        spread_bps = market_data.get('spread_bps', 5)
        
        # 估算 high/low（基於 spread）
        spread_pct = spread_bps / 10000
        high = price * (1 + spread_pct)
        low = price * (1 - spread_pct)
        
        self.price_history.append(price)
        self.high_history.append(high)
        self.low_history.append(low)
        
        # 限制長度
        if len(self.price_history) > self.history_limit:
            self.price_history.pop(0)
            self.high_history.pop(0)
            self.low_history.pop(0)
    
    def get_technical_signals(self, market_data: dict) -> Dict[str, str]:
        """獲取所有技術指標信號"""
        import numpy as np
        
        # 更新歷史數據
        self.update_price_history(market_data)
        
        # 需要至少 30 根 K 線才能計算指標
        if len(self.price_history) < 30:
            return {}
        
        close = np.array(self.price_history)
        high = np.array(self.high_history)
        low = np.array(self.low_history)
        
        signals = {}
        
        try:
            # RSI
            rsi = self.indicators.calculate_rsi(close, period=14)
            if not np.isnan(rsi[-1]):
                rsi_signal = self.indicators.rsi_signal(
                    rsi[-1],
                    oversold=self.risk_control.get('rsi_oversold', 30),
                    overbought=self.risk_control.get('rsi_overbought', 70)
                )
                signals['rsi'] = rsi_signal.value
            
            # MA Crossover (10/20)
            ma_short = self.indicators.calculate_ma(close, period=10, ma_type="EMA")
            ma_long = self.indicators.calculate_ma(close, period=20, ma_type="EMA")
            if not np.isnan(ma_short[-1]) and not np.isnan(ma_long[-1]) and len(ma_short) > 1:
                ma_signal = self.indicators.ma_crossover_signal(
                    ma_short[-1], ma_long[-1],
                    ma_short[-2], ma_long[-2]
                )
                signals['ma'] = ma_signal.value
            
            # Bollinger Bands
            upper, middle, lower = self.indicators.calculate_bollinger_bands(close, period=20)
            if not np.isnan(upper[-1]):
                boll_signal = self.indicators.bollinger_signal(
                    close[-1], upper[-1], middle[-1], lower[-1]
                )
                signals['bollinger'] = boll_signal.value
            
            # SAR
            sar = self.indicators.calculate_sar(high, low)
            if not np.isnan(sar[-1]) and len(sar) > 1:
                sar_signal = self.indicators.sar_signal(
                    close[-1], sar[-1], sar[-2]
                )
                signals['sar'] = sar_signal.value
            
            # StochRSI
            fastk, fastd = self.indicators.calculate_stochrsi(close)
            if not np.isnan(fastk[-1]) and not np.isnan(fastd[-1]):
                stochrsi_signal = self.indicators.stochrsi_signal(
                    fastk[-1], fastd[-1],
                    oversold=self.risk_control.get('stochrsi_oversold', 20),
                    overbought=self.risk_control.get('stochrsi_overbought', 80)
                )
                signals['stochrsi'] = stochrsi_signal.value
        
        except Exception as e:
            # 計算失敗時返回空字典
            pass
        
        return signals
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """檢查技術指標是否同意 + VPIN 安全過濾"""
        blocked_reasons = []
        
        # 新增：VPIN 安全過濾
        is_safe, reason = self.check_vpin_filter(market_data)
        if not is_safe:
            blocked_reasons.append(reason)
            return False, blocked_reasons
        
        # 基本風控
        if self.risk_control.get('vpin_check'):
            vpin = market_data.get('vpin', 0.3)
            threshold = self.risk_control.get('vpin_threshold', 0.75)
            if vpin > threshold:
                blocked_reasons.append(f"VPIN 過高 ({vpin:.3f} > {threshold})")
        
        if self.risk_control.get('liquidity_check'):
            spread_bps = market_data.get('spread_bps', 0)
            total_depth = market_data.get('total_depth', 0)
            
            spread_threshold = self.risk_control.get('spread_threshold', 10)
            depth_threshold = self.risk_control.get('depth_threshold', 3)
            
            if spread_bps > spread_threshold:
                blocked_reasons.append(f"Spread 過寬 ({spread_bps:.2f} bps)")
            if total_depth < depth_threshold:
                blocked_reasons.append(f"Depth 不足 ({total_depth:.2f} BTC)")
        
        # 技術指標檢查
        if self.risk_control.get('technical_indicators'):
            tech_signals = self.get_technical_signals(market_data)
            
            if not tech_signals:
                blocked_reasons.append("技術指標數據不足")
                return False, blocked_reasons
            
            # 統計各指標方向（改進版：NEUTRAL 不算反對）
            direction = signal['direction']
            agreement_count = 0  # 同意票
            disagree_count = 0   # 反對票
            neutral_count = 0    # 中性票
            total_count = 0
            
            for indicator, ind_signal in tech_signals.items():
                total_count += 1
                
                if direction == 'LONG':
                    # 做多需要看多信號
                    if ind_signal in ['BUY', 'STRONG_BUY']:
                        agreement_count += 2 if ind_signal == 'STRONG_BUY' else 1
                    elif ind_signal in ['SELL', 'STRONG_SELL']:
                        disagree_count += 2 if ind_signal == 'STRONG_SELL' else 1
                    else:  # NEUTRAL
                        neutral_count += 1
                
                elif direction == 'SHORT':
                    # 做空需要看空信號
                    if ind_signal in ['SELL', 'STRONG_SELL']:
                        agreement_count += 2 if ind_signal == 'STRONG_SELL' else 1
                    elif ind_signal in ['BUY', 'STRONG_BUY']:
                        disagree_count += 2 if ind_signal == 'STRONG_BUY' else 1
                    else:  # NEUTRAL
                        neutral_count += 1
            
            # 檢查一致性（改進規則）
            min_agreement = self.risk_control.get('min_indicator_agreement', 3)
            
            # 規則：同意票 >= 最小要求 AND 同意票 > 反對票
            if agreement_count < min_agreement:
                blocked_reasons.append(
                    f"指標共識不足 (同意={agreement_count}, 反對={disagree_count}, 中性={neutral_count}, 需要{min_agreement}票同意)"
                )
                # 添加詳細信號資訊
                signal_details = ', '.join([f"{k}={v}" for k, v in tech_signals.items()])
                blocked_reasons.append(f"指標詳情: {signal_details}")
            elif disagree_count >= agreement_count:
                blocked_reasons.append(
                    f"指標反對過多 (同意={agreement_count} vs 反對={disagree_count})"
                )
                signal_details = ', '.join([f"{k}={v}" for k, v in tech_signals.items()])
                blocked_reasons.append(f"指標詳情: {signal_details}")
        
        return len(blocked_reasons) == 0, blocked_reasons


class AdaptiveMultiTimeframeStrategy(TradingStrategy):
    """Mode 13: 自適應多時間框架策略"""
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """多時間框架趨勢確認 + VPIN 安全過濾"""
        blocked_reasons = []
        
        # 新增：VPIN 安全過濾
        is_safe, reason = self.check_vpin_filter(market_data)
        if not is_safe:
            blocked_reasons.append(reason)
            return False, blocked_reasons
        
        # 基本風控
        if self.risk_control.get('vpin_check'):
            vpin = market_data.get('vpin', 0.3)
            threshold = self.risk_control.get('vpin_threshold', 0.85)
            if vpin > threshold:
                blocked_reasons.append(f"VPIN 過高 ({vpin:.3f} > {threshold})")
        
        if self.risk_control.get('liquidity_check'):
            spread_bps = market_data.get('spread_bps', 0)
            total_depth = market_data.get('total_depth', 0)
            
            spread_threshold = self.risk_control.get('spread_threshold', 10)
            depth_threshold = self.risk_control.get('depth_threshold', 3)
            
            if spread_bps > spread_threshold:
                blocked_reasons.append(f"Spread 過寬 ({spread_bps:.2f} bps)")
            if total_depth < depth_threshold:
                blocked_reasons.append(f"Depth 不足 ({total_depth:.2f} BTC)")
        
        # 多時間框架 OBI 檢查
        obi_5min = market_data.get('obi_5min', 0)
        obi_15min = market_data.get('obi_15min', 0)
        obi_30min = market_data.get('obi_30min', 0)
        obi_1hour = market_data.get('obi_1hour', 0)
        obi_4hour = market_data.get('obi_4hour', 0)
        obi_8hour = market_data.get('obi_8hour', 0)
        
        obi_30min_threshold = self.risk_control.get('obi_30min_threshold', 0.3)
        obi_1hour_threshold = self.risk_control.get('obi_1hour_threshold', 0.25)
        obi_4hour_threshold = self.risk_control.get('obi_4hour_threshold', 0.2)
        obi_5min_threshold = self.risk_control.get('obi_5min_threshold', 0.4)
        min_confidence = self.risk_control.get('min_confidence', 0.5)
        min_obi_threshold = self.risk_control.get('min_obi_threshold', 0.5)
        
        # 檢查信心度
        if signal.get('confidence', 0) < min_confidence:
            blocked_reasons.append(f"信心度不足 (confidence={signal.get('confidence', 0):.3f} < {min_confidence})")
        
        # 檢查 OBI 強度
        current_obi = abs(market_data.get('obi', 0))
        if current_obi < min_obi_threshold:
            blocked_reasons.append(f"OBI 信號過弱 (|OBI|={current_obi:.3f} < {min_obi_threshold})")
        
        direction = signal['direction']
        
        if direction == 'LONG':
            # 做多：所有時間框架都要是正值且超過閾值
            if obi_30min < obi_30min_threshold:
                blocked_reasons.append(f"30分鐘趨勢不足 (OBI={obi_30min:+.3f} < {obi_30min_threshold})")
            if obi_1hour < obi_1hour_threshold:
                blocked_reasons.append(f"1小時趨勢不足 (OBI={obi_1hour:+.3f} < {obi_1hour_threshold})")
            if obi_4hour < obi_4hour_threshold:
                blocked_reasons.append(f"4小時趨勢不足 (OBI={obi_4hour:+.3f} < {obi_4hour_threshold})")
            if obi_5min < obi_5min_threshold:
                blocked_reasons.append(f"5分鐘信號不足 (OBI={obi_5min:+.3f} < {obi_5min_threshold})")
        
        elif direction == 'SHORT':
            # 做空：所有時間框架都要是負值且超過閾值
            if obi_30min > -obi_30min_threshold:
                blocked_reasons.append(f"30分鐘趨勢不足 (OBI={obi_30min:+.3f} > -{obi_30min_threshold})")
            if obi_1hour > -obi_1hour_threshold:
                blocked_reasons.append(f"1小時趨勢不足 (OBI={obi_1hour:+.3f} > -{obi_1hour_threshold})")
            if obi_4hour > -obi_4hour_threshold:
                blocked_reasons.append(f"4小時趨勢不足 (OBI={obi_4hour:+.3f} > -{obi_4hour_threshold})")
            if obi_5min > -obi_5min_threshold:
                blocked_reasons.append(f"5分鐘信號不足 (OBI={obi_5min:+.3f} > -{obi_5min_threshold})")
        
        # Signed Volume 檢查
        signed_volume = abs(market_data.get('signed_volume', 0))
        sv_threshold = self.risk_control.get('signed_volume_5min_threshold', 5.0)
        if signed_volume < sv_threshold:
            blocked_reasons.append(f"成交量不足 (SV={signed_volume:.2f} < {sv_threshold})")
        
        return len(blocked_reasons) == 0, blocked_reasons
    
    def get_stop_loss(self, market_data: dict) -> float:
        """更緊的止損"""
        return self.risk_control.get('stop_loss', 0.008)
    
    def get_take_profit(self, market_data: dict) -> float:
        """更高的止盈目標"""
        return self.risk_control.get('take_profit', 0.02)


class DynamicLeverageStrategy(TradingStrategy):
    """Mode 14: 動態槓桿優化策略"""
    
    def __init__(self, config: dict):
        super().__init__(config)
        # M14 特殊屬性
        self.base_leverage = config.get('base_leverage', 20)
        self.dynamic_leverage = config.get('dynamic_leverage', True)
        self.current_scheme = 'B'  # 默認從 B 方案開始


class EnhancedDynamicStrategy(TradingStrategy):
    """Mode 15: 動態方案決策優化策略"""
    
    def __init__(self, config: dict):
        super().__init__(config)
        # M15 特殊屬性
        self.base_leverage = config.get('base_leverage', 20)
        self.dynamic_leverage = config.get('dynamic_leverage', True)
        self.dynamic_scheme_selection = True  # M15 特有：動態方案選擇
        self.current_scheme = 'B'  # 默認從 B 方案開始
    
    def check_entry(self, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """
        M14 進場條件：8選7機制
        必須滿足 8 個條件中的 7 個
        """
        blocked_reasons = []
        conditions_met = []
        
        # 1. VPIN 安全檢查
        vpin = market_data.get('vpin', 0.3)
        vpin_threshold = self.risk_control.get('vpin_threshold', 0.75)
        if vpin < vpin_threshold:
            conditions_met.append("VPIN安全")
        else:
            blocked_reasons.append(f"VPIN過高 ({vpin:.3f} > {vpin_threshold})")
        
        # 2. Spread 檢查
        spread_bps = market_data.get('spread_bps', 0)
        spread_threshold = self.risk_control.get('spread_threshold', 8)
        if spread_bps < spread_threshold:
            conditions_met.append("Spread正常")
        else:
            blocked_reasons.append(f"Spread過寬 ({spread_bps:.2f} bps)")
        
        # 3. Depth 檢查
        total_depth = market_data.get('total_depth', 0)
        depth_threshold = self.risk_control.get('depth_threshold', 5)
        if total_depth > depth_threshold:
            conditions_met.append("Depth充足")
        else:
            blocked_reasons.append(f"Depth不足 ({total_depth:.2f} BTC)")
        
        # 4. OBI 強度檢查
        current_obi = abs(market_data.get('obi', 0))
        min_obi_threshold = self.risk_control.get('min_obi_threshold', 0.6)
        if current_obi > min_obi_threshold:
            conditions_met.append("OBI強度足")
        else:
            blocked_reasons.append(f"OBI過弱 (|OBI|={current_obi:.3f} < {min_obi_threshold})")
        
        # 5. 信號質量檢查（這裡簡化為信心度）
        confidence = signal.get('confidence', 0)
        quality_threshold = self.risk_control.get('signal_quality_threshold', 0.7)
        if confidence > quality_threshold:
            conditions_met.append("信號質量高")
        else:
            blocked_reasons.append(f"信號質量低 (confidence={confidence:.3f})")
        
        # 6. 成交量確認
        signed_volume = abs(market_data.get('signed_volume', 0))
        volume_threshold = self.risk_control.get('volume_confirmation_threshold', 1.2)
        # 簡化判斷：如果有成交量就算通過
        if signed_volume > 0:
            conditions_met.append("成交量確認")
        else:
            blocked_reasons.append("成交量不足")
        
        # 7. 趨勢確認（簡化版本）
        # 檢查市場不是極度波動或盤整
        if vpin < 0.8:  # 不是極度高毒性
            conditions_met.append("趨勢適合")
        else:
            blocked_reasons.append("市場過度波動")
        
        # 8. 成本感知檢查（簡化版本）
        # 假設預期變動 0.2%，槓桿20x，倉位50%
        expected_move = 0.002  # 0.2%
        leverage = self.base_leverage
        position_size = 0.5
        fee_rate = 0.0006
        slippage = 0.0002
        total_cost = fee_rate * 2 + slippage
        breakeven = total_cost / (leverage * position_size)
        required_move = breakeven * 1.5
        
        if expected_move > required_move:
            conditions_met.append("盈利預期足")
        else:
            blocked_reasons.append(f"盈利空間不足 (預期{expected_move:.4f} < 需要{required_move:.4f})")
        
        # 判斷：至少需要 7/8 條件滿足
        required_conditions = self.risk_control.get('required_conditions', 7)
        total_conditions = self.risk_control.get('total_conditions', 8)
        
        passed = len(conditions_met) >= required_conditions
        
        if not passed:
            blocked_reasons.insert(0, f"條件不足 ({len(conditions_met)}/{total_conditions}，需要{required_conditions})")
        
        return passed, blocked_reasons
    
    def get_stop_loss(self, market_data: dict) -> float:
        """動態止損（根據方案）"""
        # 簡化版本：使用配置中的範圍
        min_sl = self.risk_control.get('min_stop_loss', 0.004)
        max_sl = self.risk_control.get('max_stop_loss', 0.015)
        
        # 根據當前方案返回止損
        scheme_config = self.config.get('schemes', {}).get(self.current_scheme, {})
        return scheme_config.get('price_sl', (min_sl + max_sl) / 2)
    
    def get_take_profit(self, market_data: dict) -> float:
        """動態止盈（根據方案）"""
        # 簡化版本：使用配置中的範圍
        min_tp = self.risk_control.get('min_take_profit', 0.008)
        max_tp = self.risk_control.get('max_take_profit', 0.025)
        
        # 根據當前方案返回止盈
        scheme_config = self.config.get('schemes', {}).get(self.current_scheme, {})
        return scheme_config.get('price_tp', (min_tp + max_tp) / 2)


class StrategyManager:
    """策略管理器"""
    
    def __init__(self, config_path: str = "config/trading_strategies.json"):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.strategies = self._init_strategies()
    
    def _load_config(self) -> dict:
        """載入策略配置"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _init_strategies(self) -> Dict[str, TradingStrategy]:
        """初始化所有策略"""
        strategy_classes = {
            'mode_0_baseline': BaselineStrategy,
            'mode_0_reverse': ReverseStrategy,
            'mode_1_vpin_only': VPINStrategy,
            'mode_2_liquidity_only': LiquidityStrategy,
            'mode_3_full_control': FullControlStrategy,
            'mode_4_trend_following': TrendFollowingStrategy,
            'mode_5_mean_reversion': MeanReversionStrategy,
            'mode_6_dynamic_stop': DynamicStopStrategy,
            'mode_7_hybrid': HybridStrategy,
            'mode_8_technical_loose': TechnicalIndicatorStrategy,
            'mode_9_technical_strict': TechnicalIndicatorStrategy,
            'mode_10_technical_off': TechnicalIndicatorStrategy,
            'mode_13_adaptive': AdaptiveMultiTimeframeStrategy,
            'mode_14_dynamic_leverage': DynamicLeverageStrategy,
            'mode_15_enhanced': None  # 動態導入
        }
        
        # 動態導入 M15 策略
        try:
            from .mode_15_enhanced import Mode15EnhancedStrategy
            strategy_classes['mode_15_enhanced'] = Mode15EnhancedStrategy
        except ImportError as e:
            print(f"⚠️ 無法導入 M15 增強策略: {e}")
        
        strategies = {}
        for mode_key, strategy_config in self.config['strategies'].items():
            if strategy_config.get('enabled', True):
                strategy_class = strategy_classes.get(mode_key)
                if strategy_class:
                    strategies[mode_key] = strategy_class(strategy_config)
        
        return strategies
    
    def get_strategy(self, mode: str) -> Optional[TradingStrategy]:
        """獲取指定策略"""
        return self.strategies.get(mode)
    
    def get_all_modes(self) -> List[str]:
        """獲取所有啟用的模式"""
        return list(self.strategies.keys())
    
    def apply_risk_control(self, mode: str, market_data: dict, signal: dict) -> Tuple[bool, List[str]]:
        """應用風控規則"""
        strategy = self.get_strategy(mode)
        if not strategy:
            return False, [f"策略 {mode} 不存在"]
        
        return strategy.check_entry(market_data, signal)
    
    def adjust_signal(self, mode: str, signal: dict, market_data: dict) -> dict:
        """調整信號"""
        strategy = self.get_strategy(mode)
        if not strategy:
            return signal
        
        return strategy.adjust_signal(signal, market_data)
    
    def get_leverage(self, mode: str) -> int:
        """獲取槓桿"""
        strategy = self.get_strategy(mode)
        return strategy.leverage if strategy else 3
    
    def get_position_size(self, mode: str) -> float:
        """獲取倉位比例"""
        strategy = self.get_strategy(mode)
        return strategy.position_size if strategy else 0.3
    
    def get_stop_loss(self, mode: str, market_data: dict) -> float:
        """獲取止損"""
        strategy = self.get_strategy(mode)
        return strategy.get_stop_loss(market_data) if strategy else 0.05
    
    def get_take_profit(self, mode: str, market_data: dict) -> float:
        """獲取止盈"""
        strategy = self.get_strategy(mode)
        return strategy.get_take_profit(market_data) if strategy else 0.08
    
    def get_strategy_info(self, mode: str) -> dict:
        """獲取策略資訊"""
        strategy = self.get_strategy(mode)
        if not strategy:
            return {}
        
        return {
            'name': strategy.name,
            'emoji': strategy.emoji,
            'description': strategy.description,
            'leverage': strategy.leverage,
            'position_size': strategy.position_size
        }
