"""
OBI (Order Book Imbalance) 計算模組
訂單簿失衡指標 - 領先技術面 3~10 秒的核心指標 ⭐

功能:
- 基礎 OBI 計算與加權 OBI
- WebSocket 即時訂單簿訂閱
- 進場訊號生成 (STRONG_BUY ~ STRONG_SELL)
- 離場訊號檢測 (OBI 翻轉、趨勢轉弱、極端回歸)
- 異常檢測與告警
- 趨勢分析與統計
"""

import asyncio
import json
import time
from typing import Dict, Optional, Callable, List, Tuple
from datetime import datetime
from collections import deque
from enum import Enum
import numpy as np

try:
    import websockets
except ImportError:
    websockets = None


class OBISignal(Enum):
    """OBI 訊號類型"""
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class OBITrend(Enum):
    """OBI 趨勢類型"""
    INCREASING = "INCREASING"    # 上升（買盤增強）
    DECREASING = "DECREASING"    # 下降（賣盤增強）
    STABLE = "STABLE"            # 穩定


class ExitSignalType(Enum):
    """離場訊號類型"""
    OBI_REVERSAL = "OBI_REVERSAL"              # OBI 翻轉
    OBI_WEAKENING = "OBI_WEAKENING"            # OBI 趨勢轉弱
    EXTREME_REGRESSION = "EXTREME_REGRESSION"  # 極端值回歸
    DRASTIC_CHANGE = "DRASTIC_CHANGE"          # 劇烈變化
    NO_EXIT = "NO_EXIT"                        # 無離場訊號


class OBICalculator:
    """
    OBI 計算器
    
    公式: OBI = (ΣbidSize - ΣaskSize) / (ΣbidSize + ΣaskSize)
    範圍: -1 到 +1
    
    解讀:
    - OBI > 0.3: 買盤強勢 (STRONG BUY)
    - OBI > 0.1: 買盤優勢 (BUY)
    - -0.1 < OBI < 0.1: 平衡 (NEUTRAL)
    - OBI < -0.1: 賣盤優勢 (SELL)
    - OBI < -0.3: 賣盤強勢 (STRONG SELL)
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        depth_limit: int = 20,
        history_size: int = 100,
        exit_obi_threshold: float = 0.2,      # 離場 OBI 閾值
        exit_trend_periods: int = 5,          # 離場趨勢分析週期
        extreme_regression_threshold: float = 0.3  # 極端回歸閾值
    ):
        """
        初始化 OBI 計算器
        
        Args:
            symbol: 交易對
            depth_limit: 訂單簿深度（前N檔）
            history_size: 歷史數據保留數量
            exit_obi_threshold: 離場 OBI 閾值（絕對值）
            exit_trend_periods: 離場趨勢分析週期
            extreme_regression_threshold: 極端回歸閾值
        """
        self.symbol = symbol.upper()
        self.depth_limit = depth_limit
        self.history_size = history_size
        self.exit_obi_threshold = exit_obi_threshold
        self.exit_trend_periods = exit_trend_periods
        self.extreme_regression_threshold = extreme_regression_threshold
        
        # 訂單簿數據
        self.orderbook: Dict = {
            'bids': [],  # [[price, quantity], ...]
            'asks': [],
            'last_update': None
        }
        
        # OBI 歷史
        self.obi_history: deque = deque(maxlen=history_size)
        
        # 持倉信息（用於離場判斷）
        self.position: Optional[str] = None  # 'LONG' or 'SHORT'
        self.entry_obi: Optional[float] = None  # 進場時的 OBI
        self.max_obi: Optional[float] = None   # 持倉期間最大 OBI（多單）
        self.min_obi: Optional[float] = None   # 持倉期間最小 OBI（空單）
        
        # WebSocket 相關
        self.ws = None
        self.is_running = False
        
        # 回調函數
        self.on_obi_update: Optional[Callable] = None
        self.on_alert: Optional[Callable] = None
        self.on_exit_signal: Optional[Callable] = None  # 離場訊號回調
        
        # 統計
        self.stats = {
            'total_updates': 0,
            'last_obi': None,
            'max_obi': -1.0,
            'min_obi': 1.0,
            'alerts_triggered': 0,
            'exit_signals_triggered': 0  # 離場訊號計數
        }
    
    def get_obi(self) -> float:
        """
        獲取當前 OBI 值
        
        Returns:
            當前 OBI 值 (如果沒有數據則返回 0.0)
        """
        if self.stats['last_obi'] is not None:
            return self.stats['last_obi']
        return 0.0
    
    def calculate_obi(
        self,
        bids: List[List[float]],
        asks: List[List[float]],
        depth: int = None
    ) -> float:
        """
        計算 OBI 指標
        
        Args:
            bids: 買單列表 [[price, size], ...]
            asks: 賣單列表 [[price, size], ...]
            depth: 計算深度（前N檔）
            
        Returns:
            OBI 值 (-1 到 +1)
        """
        if not bids or not asks:
            return 0.0
        
        depth = depth or self.depth_limit
        
        # 取前 N 檔
        bids_slice = bids[:depth]
        asks_slice = asks[:depth]
        
        # 計算總量
        total_bid_size = sum(float(bid[1]) for bid in bids_slice)
        total_ask_size = sum(float(ask[1]) for ask in asks_slice)
        
        # 計算 OBI
        total_size = total_bid_size + total_ask_size
        
        if total_size == 0:
            return 0.0
        
        obi = (total_bid_size - total_ask_size) / total_size
        
        return obi
    
    def calculate_weighted_obi(
        self,
        bids: List[List[float]],
        asks: List[List[float]],
        depth: int = None
    ) -> float:
        """
        計算加權 OBI（距離最優價格越近，權重越高）
        
        Args:
            bids: 買單列表
            asks: 賣單列表
            depth: 計算深度
            
        Returns:
            加權 OBI 值
        """
        if not bids or not asks:
            return 0.0
        
        depth = depth or self.depth_limit
        
        bids_slice = bids[:depth]
        asks_slice = asks[:depth]
        
        # 最優價格
        best_bid = float(bids[0][0]) if bids else 0
        best_ask = float(asks[0][0]) if asks else 0
        mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
        
        if mid_price == 0:
            return 0.0
        
        # 加權計算
        weighted_bid_size = 0.0
        weighted_ask_size = 0.0
        
        for i, bid in enumerate(bids_slice):
            price, size = float(bid[0]), float(bid[1])
            # 距離中間價越近，權重越高
            weight = 1.0 / (1 + i)
            weighted_bid_size += size * weight
        
        for i, ask in enumerate(asks_slice):
            price, size = float(ask[0]), float(ask[1])
            weight = 1.0 / (1 + i)
            weighted_ask_size += size * weight
        
        total_weighted = weighted_bid_size + weighted_ask_size
        
        if total_weighted == 0:
            return 0.0
        
        weighted_obi = (weighted_bid_size - weighted_ask_size) / total_weighted
        
        return weighted_obi
    
    def calculate_multi_level_obi(
        self,
        bids: List[List[float]],
        asks: List[List[float]],
        max_depth: int = 10
    ) -> Dict[str, float]:
        """
        計算多層 OBI（Task 1.6.1 - B1）
        
        分別計算不同深度的 OBI，觀察訂單簿不同層級的失衡
        
        Args:
            bids: 買單列表
            asks: 賣單列表
            max_depth: 最大深度（預設10層）
            
        Returns:
            {
                'obi_level_1': float,  # 第1層（最優價）
                'obi_level_3': float,  # 前3層
                'obi_level_5': float,  # 前5層
                'obi_level_10': float, # 前10層
                'depth_imbalance': float  # 深層與淺層的差異
            }
        """
        if not bids or not asks:
            return {
                'obi_level_1': 0.0,
                'obi_level_3': 0.0,
                'obi_level_5': 0.0,
                'obi_level_10': 0.0,
                'depth_imbalance': 0.0
            }
        
        # 計算不同層級的 OBI
        obi_1 = self.calculate_obi(bids, asks, depth=1)
        obi_3 = self.calculate_obi(bids, asks, depth=3)
        obi_5 = self.calculate_obi(bids, asks, depth=5)
        obi_10 = self.calculate_obi(bids, asks, depth=min(max_depth, len(bids), len(asks)))
        
        # 深層失衡 = 深層OBI - 淺層OBI
        # 正值：深層買盤更強（可能有大單等待）
        # 負值：深層賣盤更強（可能有壓力）
        depth_imbalance = obi_10 - obi_1
        
        return {
            'obi_level_1': obi_1,
            'obi_level_3': obi_3,
            'obi_level_5': obi_5,
            'obi_level_10': obi_10,
            'depth_imbalance': depth_imbalance
        }
    
    def calculate_obi_velocity(self, window: int = 5) -> Optional[float]:
        """
        計算 OBI 速度（一階導數）- Task 1.6.1 - B1
        
        速度 = dOBI/dt，衡量 OBI 變化快慢
        
        Args:
            window: 計算視窗（預設5個樣本）
            
        Returns:
            OBI 速度（單位：OBI/秒）
            正值：買盤加速堆積
            負值：賣盤加速堆積
        """
        if len(self.obi_history) < window:
            return None
        
        # 取最近 window 個樣本，提取 obi 值
        recent_obis = [item['obi'] if isinstance(item, dict) else item 
                       for item in list(self.obi_history)[-window:]]
        
        # 使用線性擬合計算斜率
        x = np.arange(window)
        y = np.array(recent_obis)
        
        # 簡單線性回歸: slope = covariance(x,y) / variance(x)
        slope = np.polyfit(x, y, 1)[0]
        
        # 假設每個樣本間隔 100ms（Binance depth20@100ms）
        time_interval = 0.1  # 秒
        velocity = slope / time_interval
        
        return velocity
    
    def calculate_obi_acceleration(self, window: int = 5) -> Optional[float]:
        """
        計算 OBI 加速度（二階導數）- Task 1.6.1 - B1
        
        加速度 = d²OBI/dt²，衡量 OBI 變化的變化率
        
        Args:
            window: 計算視窗（預設5個樣本）
            
        Returns:
            OBI 加速度（單位：OBI/秒²）
            正值：買盤加速度上升（加速堆積）
            負值：賣盤加速度上升（加速拋售）
        """
        if len(self.obi_history) < window + 2:
            return None
        
        # 計算速度序列
        velocities = []
        history_list = list(self.obi_history)
        
        for i in range(len(history_list) - window + 1):
            window_data = history_list[i:i+window]
            # 提取 obi 值
            obi_values = [item['obi'] if isinstance(item, dict) else item 
                         for item in window_data]
            
            x = np.arange(window)
            y = np.array(obi_values)
            slope = np.polyfit(x, y, 1)[0]
            time_interval = 0.1
            velocity = slope / time_interval
            velocities.append(velocity)
        
        if len(velocities) < 2:
            return None
        
        # 計算速度的變化率（加速度）
        x = np.arange(len(velocities))
        y = np.array(velocities)
        acceleration_slope = np.polyfit(x, y, 1)[0]
        
        return acceleration_slope
    
    def calculate_microprice(
        self,
        bids: List[List[float]],
        asks: List[List[float]]
    ) -> Dict[str, float]:
        """
        計算 Microprice（加權中間價）- Task 1.6.1 - B2
        
        Microprice 是比簡單中間價更敏感的價格指標，考慮了買賣盤的數量加權。
        
        公式:
        microprice = (best_bid * ask_size + best_ask * bid_size) / (bid_size + ask_size)
        
        相比簡單 mid_price = (best_bid + best_ask) / 2，
        microprice 會向流動性較強的一方傾斜。
        
        Args:
            bids: 買單列表
            asks: 賣單列表
            
        Returns:
            {
                'microprice': float,      # 加權中間價
                'mid_price': float,        # 簡單中間價
                'pressure': float,         # 價格壓力 (microprice - mid) / mid
                'bid_weight': float,       # 買盤權重
                'ask_weight': float        # 賣盤權重
            }
        
        應用:
        - pressure > 0: microprice > mid，賣盤流動性強，價格可能下跌
        - pressure < 0: microprice < mid，買盤流動性強，價格可能上漲
        - |pressure| > 0.0001: 顯著壓力信號
        """
        if not bids or not asks:
            return {
                'microprice': 0.0,
                'mid_price': 0.0,
                'pressure': 0.0,
                'bid_weight': 0.0,
                'ask_weight': 0.0
            }
        
        # 最優價格和數量
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        bid_size = float(bids[0][1])
        ask_size = float(asks[0][1])
        
        # 簡單中間價
        mid_price = (best_bid + best_ask) / 2
        
        # 加權中間價（Microprice）
        total_size = bid_size + ask_size
        if total_size == 0:
            microprice = mid_price
            bid_weight = 0.5
            ask_weight = 0.5
        else:
            # Microprice 公式：根據對方盤口的數量加權
            microprice = (best_bid * ask_size + best_ask * bid_size) / total_size
            bid_weight = bid_size / total_size
            ask_weight = ask_size / total_size
        
        # 價格壓力（偏離度）
        if mid_price != 0:
            pressure = (microprice - mid_price) / mid_price
        else:
            pressure = 0.0
        
        return {
            'microprice': microprice,
            'mid_price': mid_price,
            'pressure': pressure,
            'bid_weight': bid_weight,
            'ask_weight': ask_weight
        }
    
    def calculate_microprice_deviation(
        self,
        window: int = 10
    ) -> Optional[Dict[str, float]]:
        """
        計算 Microprice 偏離統計 - Task 1.6.1 - B2
        
        追蹤 microprice 持續偏離 mid_price 的程度和方向。
        持續偏離代表單向壓力累積。
        
        Args:
            window: 統計視窗（預設10個樣本）
            
        Returns:
            {
                'mean_pressure': float,     # 平均壓力
                'pressure_trend': str,      # 壓力趨勢 ('BULLISH'/'BEARISH'/'NEUTRAL')
                'pressure_strength': float, # 壓力強度 (0-1)
                'consecutive_direction': int # 連續同方向偏離次數
            }
        
        應用:
        - mean_pressure < -0.0001 且連續 > 5: 買盤流動性持續強勁
        - mean_pressure > +0.0001 且連續 > 5: 賣盤流動性持續強勁
        """
        if len(self.obi_history) < window:
            return None
        
        # 從歷史中提取 microprice 相關數據
        # 注意：需要先在 update_orderbook 中儲存 microprice
        recent = list(self.obi_history)[-window:]
        
        # 如果歷史中沒有 microprice 資料，即時計算
        pressures = []
        for item in recent:
            if isinstance(item, dict) and 'microprice_pressure' in item:
                pressures.append(item['microprice_pressure'])
        
        if len(pressures) < window:
            return None
        
        # 統計分析
        mean_pressure = np.mean(pressures)
        pressure_array = np.array(pressures)
        
        # 判斷趨勢
        if mean_pressure < -0.0001:
            trend = 'BULLISH'  # 買盤流動性強
        elif mean_pressure > 0.0001:
            trend = 'BEARISH'  # 賣盤流動性強
        else:
            trend = 'NEUTRAL'
        
        # 計算壓力強度（絕對值平均）
        strength = np.abs(mean_pressure)
        
        # 計算連續同方向次數
        consecutive = 0
        last_sign = np.sign(pressures[-1])
        for p in reversed(pressures):
            if np.sign(p) == last_sign:
                consecutive += 1
            else:
                break
        
        return {
            'mean_pressure': mean_pressure,
            'pressure_trend': trend,
            'pressure_strength': strength,
            'consecutive_direction': consecutive
        }
    
    def get_obi_signal(self, obi: float) -> OBISignal:
        """
        根據 OBI 值判斷信號
        
        Args:
            obi: OBI 值
            
        Returns:
            OBISignal 枚舉
        """
        if obi > 0.3:
            return OBISignal.STRONG_BUY
        elif obi > 0.1:
            return OBISignal.BUY
        elif obi < -0.3:
            return OBISignal.STRONG_SELL
        elif obi < -0.1:
            return OBISignal.SELL
        else:
            return OBISignal.NEUTRAL
    
    def set_position(self, position: str, entry_obi: float = None):
        """
        設置持倉狀態（用於離場判斷）
        
        Args:
            position: 'LONG' 或 'SHORT'
            entry_obi: 進場時的 OBI 值
        """
        if position not in ['LONG', 'SHORT', None]:
            raise ValueError("position 必須是 'LONG', 'SHORT' 或 None")
        
        self.position = position
        self.entry_obi = entry_obi if entry_obi is not None else (
            self.stats['last_obi'] if self.stats['last_obi'] is not None else 0.0
        )
        
        # 重置極值追蹤
        if position == 'LONG':
            self.max_obi = self.entry_obi
            self.min_obi = None
        elif position == 'SHORT':
            self.min_obi = self.entry_obi
            self.max_obi = None
        else:
            self.max_obi = None
            self.min_obi = None
    
    def check_exit_signal(self, current_obi: float) -> Tuple[bool, ExitSignalType, Dict]:
        """
        檢查是否應該離場
        
        Args:
            current_obi: 當前 OBI 值
            
        Returns:
            (should_exit, signal_type, details)
        """
        if not self.position:
            return (False, ExitSignalType.NO_EXIT, {})
        
        details = {
            'position': self.position,
            'current_obi': current_obi,
            'entry_obi': self.entry_obi,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        # === 檢查 1: OBI 翻轉（最強訊號）===
        if self.position == 'LONG' and current_obi < -self.exit_obi_threshold:
            # 多單持倉，OBI 翻負且超過閾值
            details.update({
                'reason': 'OBI 翻負，賣盤開始堆積',
                'severity': 'HIGH',
                'change': current_obi - self.entry_obi
            })
            return (True, ExitSignalType.OBI_REVERSAL, details)
        
        elif self.position == 'SHORT' and current_obi > self.exit_obi_threshold:
            # 空單持倉，OBI 翻正且超過閾值
            details.update({
                'reason': 'OBI 翻正，買盤開始堆積',
                'severity': 'HIGH',
                'change': current_obi - self.entry_obi
            })
            return (True, ExitSignalType.OBI_REVERSAL, details)
        
        # === 檢查 2: OBI 趨勢轉弱 ===
        trend = self.get_obi_trend(periods=self.exit_trend_periods)
        if trend:
            if self.position == 'LONG' and trend == OBITrend.DECREASING:
                # 多單持倉，OBI 持續下降
                if current_obi < self.entry_obi - 0.1:
                    details.update({
                        'reason': 'OBI 趨勢轉弱，買盤力量減退',
                        'severity': 'MEDIUM',
                        'trend': trend.value,
                        'obi_decline': self.entry_obi - current_obi
                    })
                    return (True, ExitSignalType.OBI_WEAKENING, details)
            
            elif self.position == 'SHORT' and trend == OBITrend.INCREASING:
                # 空單持倉，OBI 持續上升
                if current_obi > self.entry_obi + 0.1:
                    details.update({
                        'reason': 'OBI 趨勢轉強，賣盤力量減退',
                        'severity': 'MEDIUM',
                        'trend': trend.value,
                        'obi_rise': current_obi - self.entry_obi
                    })
                    return (True, ExitSignalType.OBI_WEAKENING, details)
        
        # === 檢查 3: 極端值回歸 ===
        if self.position == 'LONG' and self.max_obi is not None:
            # 更新最大 OBI
            self.max_obi = max(self.max_obi, current_obi)
            
            # 從極端高位回落
            if self.max_obi > 0.5 and current_obi < self.max_obi - self.extreme_regression_threshold:
                details.update({
                    'reason': '從極端買盤高位回落',
                    'severity': 'MEDIUM',
                    'max_obi': self.max_obi,
                    'regression': self.max_obi - current_obi
                })
                return (True, ExitSignalType.EXTREME_REGRESSION, details)
        
        elif self.position == 'SHORT' and self.min_obi is not None:
            # 更新最小 OBI
            self.min_obi = min(self.min_obi, current_obi)
            
            # 從極端低位反彈
            if self.min_obi < -0.5 and current_obi > self.min_obi + self.extreme_regression_threshold:
                details.update({
                    'reason': '從極端賣盤低位反彈',
                    'severity': 'MEDIUM',
                    'min_obi': self.min_obi,
                    'regression': current_obi - self.min_obi
                })
                return (True, ExitSignalType.EXTREME_REGRESSION, details)
        
        # === 檢查 4: 劇烈變化（可能是大單撤單）===
        if len(self.obi_history) >= 2:
            prev_obi = self.obi_history[-1]['obi']
            obi_change = abs(current_obi - prev_obi)
            
            if obi_change > 0.4:  # 劇烈變化
                if self.position == 'LONG' and current_obi < prev_obi:
                    # 多單持倉，OBI 劇烈下降
                    details.update({
                        'reason': 'OBI 劇烈下降，可能大買單撤單',
                        'severity': 'HIGH',
                        'change': obi_change,
                        'prev_obi': prev_obi
                    })
                    return (True, ExitSignalType.DRASTIC_CHANGE, details)
                
                elif self.position == 'SHORT' and current_obi > prev_obi:
                    # 空單持倉，OBI 劇烈上升
                    details.update({
                        'reason': 'OBI 劇烈上升，可能大賣單撤單',
                        'severity': 'HIGH',
                        'change': obi_change,
                        'prev_obi': prev_obi
                    })
                    return (True, ExitSignalType.DRASTIC_CHANGE, details)
        
        # 無離場訊號
        return (False, ExitSignalType.NO_EXIT, details)
    
    def update_orderbook(self, bids: List, asks: List):
        """
        更新訂單簿數據
        
        Args:
            bids: 買單列表
            asks: 賣單列表
        """
        self.orderbook['bids'] = bids
        self.orderbook['asks'] = asks
        self.orderbook['last_update'] = datetime.utcnow()
        
        # 計算 OBI
        obi = self.calculate_obi(bids, asks)
        weighted_obi = self.calculate_weighted_obi(bids, asks)
        
        # 計算 Microprice（Task 1.6.1 - B2）
        microprice_data = self.calculate_microprice(bids, asks)
        
        # 記錄歷史
        self.obi_history.append({
            'timestamp': datetime.utcnow(),
            'obi': obi,
            'weighted_obi': weighted_obi,
            'bid_size': sum(float(b[1]) for b in bids[:self.depth_limit]),
            'ask_size': sum(float(a[1]) for a in asks[:self.depth_limit]),
            'spread': float(asks[0][0]) - float(bids[0][0]) if bids and asks else 0,
            # Microprice 相關數據
            'microprice': microprice_data['microprice'],
            'mid_price': microprice_data['mid_price'],
            'microprice_pressure': microprice_data['pressure'],
            'bid_weight': microprice_data['bid_weight'],
            'ask_weight': microprice_data['ask_weight']
        })
        
        # 更新統計
        self.stats['total_updates'] += 1
        self.stats['last_obi'] = obi
        self.stats['max_obi'] = max(self.stats['max_obi'], obi)
        self.stats['min_obi'] = min(self.stats['min_obi'], obi)
        
        # 檢查離場訊號（如果有持倉）
        if self.position:
            should_exit, exit_type, exit_details = self.check_exit_signal(obi)
            
            if should_exit:
                self.stats['exit_signals_triggered'] += 1
                
                # 觸發離場回調
                if self.on_exit_signal:
                    self.on_exit_signal({
                        'signal_type': exit_type.value,
                        'details': exit_details
                    })
        
        # 觸發更新回調
        if self.on_obi_update:
            self.on_obi_update({
                'symbol': self.symbol,
                'obi': obi,
                'weighted_obi': weighted_obi,
                'signal': self.get_obi_signal(obi).value,
                'position': self.position,
                'timestamp': datetime.utcnow().isoformat()
            })
        
        # 檢查異常
        self._check_alerts(obi)
    
    def _check_alerts(self, obi: float):
        """
        檢查 OBI 異常並觸發告警
        
        Args:
            obi: 當前 OBI 值
        """
        # 劇烈變化檢測
        if len(self.obi_history) >= 2:
            prev_obi = self.obi_history[-2]['obi']
            obi_change = abs(obi - prev_obi)
            
            # OBI 變化超過 0.3（劇烈變化）
            if obi_change > 0.3:
                self.stats['alerts_triggered'] += 1
                
                if self.on_alert:
                    self.on_alert({
                        'type': 'DRASTIC_CHANGE',
                        'message': f'OBI 劇烈變化: {prev_obi:.3f} -> {obi:.3f}',
                        'change': obi_change,
                        'timestamp': datetime.utcnow().isoformat()
                    })
        
        # 極端值檢測
        if abs(obi) > 0.5:
            if self.on_alert:
                self.on_alert({
                    'type': 'EXTREME_VALUE',
                    'message': f'OBI 極端值: {obi:.3f}',
                    'obi': obi,
                    'timestamp': datetime.utcnow().isoformat()
                })
    
    def get_current_obi(self) -> Optional[Dict]:
        """
        獲取當前 OBI 數據
        
        Returns:
            包含 OBI 和相關信息的字典
        """
        if not self.obi_history:
            return None
        
        latest = self.obi_history[-1]
        current_obi = latest['obi']
        
        # 檢查離場訊號
        exit_info = {}
        if self.position:
            should_exit, exit_type, exit_details = self.check_exit_signal(current_obi)
            exit_info = {
                'should_exit': should_exit,
                'exit_signal': exit_type.value,
                'exit_details': exit_details if should_exit else None
            }
        
        return {
            'symbol': self.symbol,
            'obi': current_obi,
            'weighted_obi': latest['weighted_obi'],
            'signal': self.get_obi_signal(current_obi).value,
            'trend': self.get_obi_trend().value if self.get_obi_trend() else None,
            'bid_size': latest['bid_size'],
            'ask_size': latest['ask_size'],
            'spread': latest['spread'],
            'position': self.position,
            'entry_obi': self.entry_obi,
            **exit_info,
            'timestamp': latest['timestamp'].isoformat()
        }
    
    def get_obi_trend(self, periods: int = 10) -> Optional[OBITrend]:
        """
        分析 OBI 趨勢
        
        Args:
            periods: 分析週期數
            
        Returns:
            OBITrend 枚舉 (INCREASING / DECREASING / STABLE)
        """
        if len(self.obi_history) < periods:
            return None
        
        recent = list(self.obi_history)[-periods:]
        obi_values = [item['obi'] for item in recent]
        
        # 線性回歸斜率
        x = np.arange(len(obi_values))
        slope = np.polyfit(x, obi_values, 1)[0]
        
        if slope > 0.01:
            return OBITrend.INCREASING  # 上升趨勢
        elif slope < -0.01:
            return OBITrend.DECREASING  # 下降趨勢
        else:
            return OBITrend.STABLE  # 穩定
    
    def get_statistics(self) -> Dict:
        """
        獲取統計信息
        
        Returns:
            統計數據字典
        """
        if not self.obi_history:
            return self.stats
        
        obi_values = [item['obi'] for item in self.obi_history]
        trend = self.get_obi_trend()
        
        return {
            **self.stats,
            'history_size': len(self.obi_history),
            'mean_obi': np.mean(obi_values),
            'std_obi': np.std(obi_values),
            'current_trend': trend.value if trend else None,
            'position': self.position,
            'entry_obi': self.entry_obi,
            'position_max_obi': self.max_obi,
            'position_min_obi': self.min_obi
        }
    
    async def start_websocket(self, on_message: Optional[Callable] = None):
        """
        啟動 WebSocket 訂單簿訂閱
        
        Args:
            on_message: 消息回調函數
        """
        if websockets is None:
            raise ImportError("websockets 未安裝，請執行: pip install websockets")
        
        self.is_running = True
        ws_url = f"wss://stream.binance.com:9443/ws/{self.symbol.lower()}@depth20@100ms"
        
        print(f"🔌 連接 WebSocket: {ws_url}")
        
        try:
            async with websockets.connect(ws_url) as ws:
                self.ws = ws
                print(f"✅ WebSocket 已連接")
                
                while self.is_running:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        data = json.loads(message)
                        
                        # 更新訂單簿
                        if 'bids' in data and 'asks' in data:
                            self.update_orderbook(data['bids'], data['asks'])
                        
                        if on_message:
                            on_message(data)
                        
                    except asyncio.TimeoutError:
                        print("⚠️  WebSocket 接收超時")
                        continue
                    except Exception as e:
                        print(f"❌ WebSocket 錯誤: {e}")
                        break
        
        except Exception as e:
            print(f"❌ WebSocket 連接失敗: {e}")
        finally:
            self.is_running = False
            print("🔌 WebSocket 已斷開")
    
    def stop_websocket(self):
        """停止 WebSocket"""
        self.is_running = False


# 便利函數
def calculate_obi_from_snapshot(orderbook: Dict, depth: int = 20) -> float:
    """
    從訂單簿快照計算 OBI
    
    Args:
        orderbook: 訂單簿數據 {'bids': [...], 'asks': [...]}
        depth: 計算深度
        
    Returns:
        OBI 值
    """
    calculator = OBICalculator(depth_limit=depth)
    return calculator.calculate_obi(orderbook.get('bids', []), orderbook.get('asks', []))
