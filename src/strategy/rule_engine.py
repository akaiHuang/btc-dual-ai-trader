"""
RuleEngine - 統一的策略決策引擎
根據 mode config + market snapshot 評估進出場條件
"""
from typing import Dict, Optional, Any, List
import logging

logger = logging.getLogger(__name__)


class RuleEngine:
    """規則引擎 - 統一評估所有模式的進出場條件"""
    
    def __init__(self):
        self.evaluation_count = 0
    
    def evaluate_entry(
        self,
        mode_name: str,
        mode_config: dict,
        snapshot: dict
    ) -> dict:
        """評估進場條件
        
        Args:
            mode_name: 模式名稱
            mode_config: 模式配置
            snapshot: 市場快照
        
        Returns:
            {
                'action': 'LONG' | 'SHORT' | 'HOLD',
                'reason': str,
                'confidence': float,
                'tp_pct': float,
                'sl_pct': float,
                'position_size': float,
                'leverage': int
            }
        """
        self.evaluation_count += 1
        
        # 預設結果
        result = {
            'action': 'HOLD',
            'reason': 'No signal',
            'confidence': 0.0,
            'tp_pct': mode_config.get('tp_pct', 0.01),
            'sl_pct': mode_config.get('sl_pct', 0.01),
            'position_size': mode_config.get('position_size', 0.5),
            'leverage': mode_config.get('leverage', 10)
        }
        
        # 取得模式類型
        mode_type = mode_config.get('type', 'UNKNOWN')
        
        # 依照類型分派到不同的評估邏輯
        if mode_type in ['BASELINE', 'SAFE', 'NORMAL', 'AGGRESSIVE', 'SANDBOX']:
            return self._evaluate_hybrid_mode(mode_name, mode_config, snapshot, result)
        elif mode_type == 'MICRO1':
            return self._evaluate_micro_trend(mode_name, mode_config, snapshot, result)
        elif mode_type == 'MICRO2':
            return self._evaluate_micro_range(mode_name, mode_config, snapshot, result)
        elif mode_type == 'MICRO3':
            return self._evaluate_micro_momentum(mode_name, mode_config, snapshot, result)
        elif mode_type == 'SNIPER_BREAKOUT':
            return self._evaluate_sniper_breakout(mode_name, mode_config, snapshot, result)
        elif mode_type == 'SNIPER_VOLUME':
            return self._evaluate_sniper_volume(mode_name, mode_config, snapshot, result)
        elif mode_type == 'SNIPER_VOLATILITY':
            return self._evaluate_sniper_volatility(mode_name, mode_config, snapshot, result)
        else:
            result['reason'] = f'Unknown mode type: {mode_type}'
            return result
    
    def _evaluate_hybrid_mode(
        self,
        mode_name: str,
        mode_config: dict,
        snapshot: dict,
        result: dict
    ) -> dict:
        """評估 Hybrid 模式（M0-M6 系列）"""
        
        # 檢查 Regime 白名單/黑名單
        regime = snapshot.get('regime', 'UNKNOWN')
        regime_whitelist = mode_config.get('regime_whitelist', [])
        regime_blacklist = mode_config.get('regime_blacklist', [])
        
        if regime_whitelist and regime not in regime_whitelist:
            result['reason'] = f'Regime {regime} not in whitelist'
            return result
        
        if regime_blacklist and regime in regime_blacklist:
            result['reason'] = f'Regime {regime} in blacklist'
            return result
        
        # 檢查 Trend state（如果需要）
        trend_state = snapshot.get('trend_state', 'UNKNOWN')
        required_trend_states = mode_config.get('required_trend_states', [])
        if required_trend_states and trend_state not in required_trend_states:
            result['reason'] = f'Trend state {trend_state} not required'
            return result
        
        # 檢查 entry_rules（簡化版：支援基本的指標條件）
        entry_rules = mode_config.get('entry_rules', {})
        
        # 範例：檢查 RSI 條件
        if 'rsi' in entry_rules:
            rsi_rule = entry_rules['rsi']
            current_rsi = snapshot.get('rsi', 50)
            
            if 'min' in rsi_rule and current_rsi < rsi_rule['min']:
                result['reason'] = f"RSI {current_rsi:.1f} below min {rsi_rule['min']}"
                return result
            
            if 'max' in rsi_rule and current_rsi > rsi_rule['max']:
                result['reason'] = f"RSI {current_rsi:.1f} above max {rsi_rule['max']}"
                return result
        
        # 檢查 OBI 條件
        if 'obi' in entry_rules:
            obi_rule = entry_rules['obi']
            current_obi = snapshot.get('obi', 0)
            
            if 'min_abs' in obi_rule and abs(current_obi) < obi_rule['min_abs']:
                result['reason'] = f"OBI {current_obi:.3f} too weak"
                return result
        
        # 檢查量能條件
        if 'volume_factor' in entry_rules:
            volume_factor = entry_rules['volume_factor']
            current_volume = snapshot.get('volume', 0)
            volume_ma = snapshot.get('volume_ma', 1)
            
            if volume_ma > 0 and current_volume / volume_ma < volume_factor:
                result['reason'] = f"Volume factor {current_volume/volume_ma:.2f} below {volume_factor}"
                return result
        
        # 檢查巨鯨條件
        if 'whale_min_concentration' in entry_rules:
            min_conc = entry_rules['whale_min_concentration']
            whale_conc = snapshot.get('whale_concentration', 0)
            
            if whale_conc < min_conc:
                result['reason'] = f"Whale concentration {whale_conc:.2f} below {min_conc}"
                return result
        
        # 決定方向（簡化版：依照 OBI / funding / large_trade_direction）
        obi = snapshot.get('obi', 0)
        funding_zscore = snapshot.get('funding_zscore', 0)
        large_trade_direction = snapshot.get('large_trade_direction', None)
        
        # 計算方向分數
        direction_score = 0
        weights = mode_config.get('signal_weights', {'obi': 0.4, 'funding': 0.3, 'whale': 0.3})
        
        direction_score += weights.get('obi', 0.4) * (1 if obi > 0 else -1 if obi < 0 else 0)
        direction_score += weights.get('funding', 0.3) * (1 if funding_zscore > 0 else -1 if funding_zscore < 0 else 0)
        
        if large_trade_direction == 'LONG':
            direction_score += weights.get('whale', 0.3)
        elif large_trade_direction == 'SHORT':
            direction_score -= weights.get('whale', 0.3)
        
        # 依照方向門檻決定
        min_direction_score = mode_config.get('min_direction_score', 0.5)
        
        if direction_score >= min_direction_score:
            result['action'] = 'LONG'
            result['reason'] = f'Direction score {direction_score:.2f} >= {min_direction_score}'
            result['confidence'] = min(abs(direction_score), 1.0)
        elif direction_score <= -min_direction_score:
            result['action'] = 'SHORT'
            result['reason'] = f'Direction score {direction_score:.2f} <= -{min_direction_score}'
            result['confidence'] = min(abs(direction_score), 1.0)
        else:
            result['reason'] = f'Direction score {direction_score:.2f} too weak'
        
        return result
    
    def _evaluate_micro_trend(self, mode_name: str, mode_config: dict, snapshot: dict, result: dict) -> dict:
        """評估 M_MICRO1：順勢微利模式"""
        
        # 檢查大方向（必須是 TREND）
        trend_state = snapshot.get('trend_state', 'UNKNOWN')
        if trend_state not in ['STRONG_UP', 'STRONG_DOWN', 'LEAN_UP', 'LEAN_DOWN']:
            result['reason'] = f'Trend state {trend_state} not suitable for trend micro'
            return result
        
        # 檢查短期 MA 交叉（簡化：用 price vs MA5/MA10）
        # 實際應該在 snapshot 裡算好 MA5/MA10
        
        # 檢查 RSI 中性區
        rsi = snapshot.get('rsi', 50)
        rsi_long_lower = mode_config.get('rsi_long_lower', 40)
        rsi_long_upper = mode_config.get('rsi_long_upper', 70)
        rsi_short_lower = mode_config.get('rsi_short_lower', 30)
        rsi_short_upper = mode_config.get('rsi_short_upper', 60)
        
        # 檢查量能
        volume_factor = mode_config.get('volume_factor', 1.1)
        current_volume = snapshot.get('volume', 0)
        volume_ma = snapshot.get('volume_ma', 1)
        
        if volume_ma > 0 and current_volume / volume_ma < volume_factor:
            result['reason'] = f'Volume too low for micro trend'
            return result
        
        # 檢查巨鯨
        whale_conc = snapshot.get('whale_concentration', 0)
        whale_min_conc = mode_config.get('whale_min_concentration', 0.65)
        
        if whale_conc < whale_min_conc:
            result['reason'] = f'Whale concentration {whale_conc:.2f} too low'
            return result
        
        # 決定方向
        large_trade_direction = snapshot.get('large_trade_direction', None)
        
        if trend_state in ['STRONG_UP', 'LEAN_UP']:
            if rsi_long_lower <= rsi <= rsi_long_upper:
                if large_trade_direction == 'LONG' or large_trade_direction is None:
                    result['action'] = 'LONG'
                    result['reason'] = 'Micro trend: LONG setup confirmed'
                    result['confidence'] = 0.7
        elif trend_state in ['STRONG_DOWN', 'LEAN_DOWN']:
            if rsi_short_lower <= rsi <= rsi_short_upper:
                if large_trade_direction == 'SHORT' or large_trade_direction is None:
                    result['action'] = 'SHORT'
                    result['reason'] = 'Micro trend: SHORT setup confirmed'
                    result['confidence'] = 0.7
        
        return result
    
    def _evaluate_micro_range(self, mode_name: str, mode_config: dict, snapshot: dict, result: dict) -> dict:
        """評估 M_MICRO2：盤整逆勢微利模式"""
        
        # 只在 RANGE 盤啟用
        regime = snapshot.get('regime', 'UNKNOWN')
        if regime not in ['CONSOLIDATION', 'NEUTRAL']:
            result['reason'] = f'Regime {regime} not suitable for range micro'
            return result
        
        # 檢查價格位置（是否接近區間邊緣）
        # 這裡需要 snapshot 提供 range_position（例如 0-1，0 = 下緣，1 = 上緣）
        range_position = snapshot.get('range_position', 0.5)
        
        # 上緣做空，下緣做多
        if range_position > 0.7:
            rsi = snapshot.get('rsi', 50)
            if rsi > 60:
                result['action'] = 'SHORT'
                result['reason'] = 'Range micro: Near top, RSI overbought'
                result['confidence'] = 0.6
        elif range_position < 0.3:
            rsi = snapshot.get('rsi', 50)
            if rsi < 40:
                result['action'] = 'LONG'
                result['reason'] = 'Range micro: Near bottom, RSI oversold'
                result['confidence'] = 0.6
        
        return result
    
    def _evaluate_micro_momentum(self, mode_name: str, mode_config: dict, snapshot: dict, result: dict) -> dict:
        """評估 M_MICRO3：事件／量能爆發微利模式"""
        
        # 檢查量能爆發
        volume_factor = snapshot.get('volume', 0) / max(snapshot.get('volume_ma', 1), 1)
        volume_spike_threshold = mode_config.get('volume_spike_threshold', 2.5)
        
        if volume_factor < volume_spike_threshold:
            result['reason'] = f'Volume factor {volume_factor:.2f} below spike threshold'
            return result
        
        # 檢查巨鯨集中度
        whale_conc = snapshot.get('whale_concentration', 0)
        if whale_conc < 0.8:
            result['reason'] = f'Whale concentration {whale_conc:.2f} not extreme enough'
            return result
        
        # 決定方向
        large_trade_direction = snapshot.get('large_trade_direction', None)
        
        if large_trade_direction == 'LONG':
            result['action'] = 'LONG'
            result['reason'] = 'Momentum spike: LONG confirmed'
            result['confidence'] = 0.75
        elif large_trade_direction == 'SHORT':
            result['action'] = 'SHORT'
            result['reason'] = 'Momentum spike: SHORT confirmed'
            result['confidence'] = 0.75
        
        return result
    
    def _evaluate_sniper_breakout(self, mode_name: str, mode_config: dict, snapshot: dict, result: dict) -> dict:
        """評估 M7：突破狙擊模式"""
        breakout_signal = snapshot.get('breakout_signal', {})
        
        if not breakout_signal.get('detected', False):
            result['reason'] = 'No breakout detected'
            return result
        
        result['action'] = breakout_signal.get('direction', 'HOLD')
        result['reason'] = f"Breakout: {breakout_signal.get('reason', 'Unknown')}"
        result['confidence'] = breakout_signal.get('strength', 0.5)
        
        return result
    
    def _evaluate_sniper_volume(self, mode_name: str, mode_config: dict, snapshot: dict, result: dict) -> dict:
        """評估 M8：量能狙擊模式"""
        volume_signal = snapshot.get('volume_surge_signal', {})
        
        if not volume_signal.get('detected', False):
            result['reason'] = 'No volume surge detected'
            return result
        
        result['action'] = volume_signal.get('direction', 'HOLD')
        result['reason'] = f"Volume surge: {volume_signal.get('reason', 'Unknown')}"
        result['confidence'] = volume_signal.get('strength', 0.5)
        
        return result
    
    def _evaluate_sniper_volatility(self, mode_name: str, mode_config: dict, snapshot: dict, result: dict) -> dict:
        """評估 M9：波動狙擊模式"""
        volatility_signal = snapshot.get('volatility_window_signal', {})
        
        if not volatility_signal.get('detected', False):
            result['reason'] = 'No volatility window detected'
            return result
        
        result['action'] = volatility_signal.get('direction', 'HOLD')
        result['reason'] = f"Volatility window: {volatility_signal.get('reason', 'Unknown')}"
        result['confidence'] = volatility_signal.get('strength', 0.5)
        
        return result
    
    def get_stats(self) -> dict:
        """獲取引擎統計資訊"""
        return {
            'total_evaluations': self.evaluation_count
        }
