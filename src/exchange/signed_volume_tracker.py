"""
Signed Volume Tracker - Task 1.6.1 - B3

追蹤主動買賣量（Signed Volume）
- 使用 tick rule 或 Binance 的 isBuyerMaker 標記判斷交易方向
- 計算短窗口淨量（買單為正，賣單為負）
- 檢測買賣壓力累積
"""

from typing import List, Dict, Optional, Callable
from collections import deque
from datetime import datetime
import numpy as np


class SignedVolumeTracker:
    """
    Signed Volume 追蹤器
    
    功能:
    - 判斷每筆交易的主動方（買方/賣方）
    - 計算短窗口淨量
    - 分析買賣壓力趨勢
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        history_size: int = 1000,
        window_size: int = 100
    ):
        """
        初始化 Signed Volume 追蹤器
        
        Args:
            symbol: 交易對
            history_size: 交易歷史保留數量
            window_size: 計算窗口大小
        """
        self.symbol = symbol.upper()
        self.history_size = history_size
        self.window_size = window_size
        
        # 交易歷史
        self.trades: deque = deque(maxlen=history_size)
        
        # 最後價格（用於 tick rule）
        self.last_price: Optional[float] = None
        
        # 統計
        self.stats = {
            'total_trades': 0,
            'buy_trades': 0,
            'sell_trades': 0,
            'neutral_trades': 0,
            'total_buy_volume': 0.0,
            'total_sell_volume': 0.0,
            'net_volume': 0.0
        }
        
        # 回調函數
        self.on_trade: Optional[Callable] = None
        self.on_pressure_change: Optional[Callable] = None
    
    def classify_trade_side(self, trade: Dict) -> int:
        """
        判斷交易主動方
        
        Args:
            trade: 交易數據
                {
                    'p': price (str),
                    'q': quantity (str),
                    'm': isBuyerMaker (bool) - Binance 提供
                }
        
        Returns:
            1: 買方主動（aggressive buy）
            -1: 賣方主動（aggressive sell）
            0: 無法判斷
        
        判斷邏輯:
        1. 優先使用 Binance 的 isBuyerMaker 標記
           - m=True: maker 是買方 → taker 是賣方 → 賣方主動 = -1
           - m=False: maker 是賣方 → taker 是買方 → 買方主動 = 1
        
        2. 若無標記，使用 tick rule
           - price > last_price: 買方主動 = 1
           - price < last_price: 賣方主動 = -1
           - price == last_price: 無法判斷 = 0
        """
        price = float(trade['p'])
        
        # 方法 1: 使用 Binance 提供的 isBuyerMaker
        if 'm' in trade:
            # m=True: maker 是買方，taker 是賣方 → 賣方主動
            # m=False: maker 是賣方，taker 是買方 → 買方主動
            return -1 if trade['m'] else 1
        
        # 方法 2: 使用 tick rule
        if self.last_price is not None:
            if price > self.last_price:
                return 1  # 上漲 = 買方主動
            elif price < self.last_price:
                return -1  # 下跌 = 賣方主動
            else:
                return 0  # 價格不變，無法判斷
        
        # 初始交易，無法判斷
        return 0
    
    def add_trade(self, trade: Dict):
        """
        添加交易記錄
        
        Args:
            trade: 交易數據
        """
        price = float(trade['p'])
        quantity = float(trade['q'])
        
        # 判斷方向
        side = self.classify_trade_side(trade)
        
        # 儲存交易
        trade_record = {
            'timestamp': trade.get('T', datetime.utcnow().timestamp() * 1000),
            'price': price,
            'quantity': quantity,
            'side': side,
            'signed_volume': quantity * side
        }
        
        self.trades.append(trade_record)
        
        # 更新最後價格
        self.last_price = price
        
        # 更新統計
        self.stats['total_trades'] += 1
        
        if side == 1:
            self.stats['buy_trades'] += 1
            self.stats['total_buy_volume'] += quantity
        elif side == -1:
            self.stats['sell_trades'] += 1
            self.stats['total_sell_volume'] += quantity
        else:
            self.stats['neutral_trades'] += 1
        
        self.stats['net_volume'] = (
            self.stats['total_buy_volume'] - self.stats['total_sell_volume']
        )
        
        # 觸發回調
        if self.on_trade:
            self.on_trade(trade_record)
    
    def calculate_signed_volume(self, window: int = None) -> float:
        """
        計算短窗口淨量（Signed Volume）
        
        Args:
            window: 窗口大小（預設使用初始化時的 window_size）
        
        Returns:
            淨量（買單為正，賣單為負）
            正值：買方壓力強
            負值：賣方壓力強
        """
        window = window or self.window_size
        
        if len(self.trades) < window:
            window = len(self.trades)
        
        if window == 0:
            return 0.0
        
        recent_trades = list(self.trades)[-window:]
        signed_vol = sum([t['signed_volume'] for t in recent_trades])
        
        return signed_vol
    
    def calculate_volume_imbalance(self, window: int = None) -> Dict[str, float]:
        """
        計算成交量失衡指標
        
        Args:
            window: 窗口大小
        
        Returns:
            {
                'buy_volume': float,      # 買方成交量
                'sell_volume': float,     # 賣方成交量
                'net_volume': float,      # 淨量
                'imbalance': float,       # 失衡度 (-1 到 1)
                'buy_ratio': float,       # 買方佔比
                'sell_ratio': float       # 賣方佔比
            }
        """
        window = window or self.window_size
        
        if len(self.trades) < window:
            window = len(self.trades)
        
        if window == 0:
            return {
                'buy_volume': 0.0,
                'sell_volume': 0.0,
                'net_volume': 0.0,
                'imbalance': 0.0,
                'buy_ratio': 0.0,
                'sell_ratio': 0.0
            }
        
        recent_trades = list(self.trades)[-window:]
        
        buy_volume = sum([t['quantity'] for t in recent_trades if t['side'] == 1])
        sell_volume = sum([t['quantity'] for t in recent_trades if t['side'] == -1])
        net_volume = buy_volume - sell_volume
        total_volume = buy_volume + sell_volume
        
        # 失衡度（類似 OBI）
        if total_volume > 0:
            imbalance = net_volume / total_volume
            buy_ratio = buy_volume / total_volume
            sell_ratio = sell_volume / total_volume
        else:
            imbalance = 0.0
            buy_ratio = 0.0
            sell_ratio = 0.0
        
        return {
            'buy_volume': buy_volume,
            'sell_volume': sell_volume,
            'net_volume': net_volume,
            'imbalance': imbalance,
            'buy_ratio': buy_ratio,
            'sell_ratio': sell_ratio
        }
    
    def calculate_volume_pressure(self, window: int = None) -> Dict[str, any]:
        """
        計算成交量壓力趨勢
        
        Args:
            window: 窗口大小
        
        Returns:
            {
                'current_pressure': str,    # 當前壓力 ('BUY'/'SELL'/'NEUTRAL')
                'pressure_strength': float, # 壓力強度 (0-1)
                'trend': str,               # 趨勢 ('INCREASING'/'DECREASING'/'STABLE')
                'consecutive_buy': int,     # 連續買方主動次數
                'consecutive_sell': int     # 連續賣方主動次數
            }
        """
        imbalance_data = self.calculate_volume_imbalance(window)
        imbalance = imbalance_data['imbalance']
        
        # 判斷當前壓力
        if imbalance > 0.2:
            current_pressure = 'BUY'
        elif imbalance < -0.2:
            current_pressure = 'SELL'
        else:
            current_pressure = 'NEUTRAL'
        
        # 壓力強度
        pressure_strength = abs(imbalance)
        
        # 分析趨勢（使用最近的交易）
        if len(self.trades) >= 10:
            recent = list(self.trades)[-10:]
            sides = [t['side'] for t in recent]
            
            # 計算連續買/賣次數
            consecutive_buy = 0
            consecutive_sell = 0
            
            for side in reversed(sides):
                if side == 1:
                    consecutive_buy += 1
                    if consecutive_sell > 0:
                        break
                elif side == -1:
                    consecutive_sell += 1
                    if consecutive_buy > 0:
                        break
            
            # 判斷趨勢
            buy_count = sum([1 for s in sides if s == 1])
            sell_count = sum([1 for s in sides if s == -1])
            
            if buy_count > sell_count * 1.5:
                trend = 'INCREASING'  # 買壓增強
            elif sell_count > buy_count * 1.5:
                trend = 'DECREASING'  # 賣壓增強
            else:
                trend = 'STABLE'
        else:
            trend = 'STABLE'
            consecutive_buy = 0
            consecutive_sell = 0
        
        return {
            'current_pressure': current_pressure,
            'pressure_strength': pressure_strength,
            'trend': trend,
            'consecutive_buy': consecutive_buy,
            'consecutive_sell': consecutive_sell
        }
    
    def get_statistics(self) -> Dict:
        """
        獲取統計信息
        
        Returns:
            統計數據字典
        """
        total_volume = self.stats['total_buy_volume'] + self.stats['total_sell_volume']
        
        return {
            **self.stats,
            'total_volume': total_volume,
            'buy_sell_ratio': (
                self.stats['total_buy_volume'] / self.stats['total_sell_volume']
                if self.stats['total_sell_volume'] > 0 else 0
            ),
            'trades_in_memory': len(self.trades)
        }
    
    def reset_statistics(self):
        """重置統計數據"""
        self.stats = {
            'total_trades': 0,
            'buy_trades': 0,
            'sell_trades': 0,
            'neutral_trades': 0,
            'total_buy_volume': 0.0,
            'total_sell_volume': 0.0,
            'net_volume': 0.0
        }


def calculate_signed_volume_from_trades(
    trades: List[Dict],
    window: int = None
) -> float:
    """
    便利函數：從交易列表計算 Signed Volume
    
    Args:
        trades: 交易列表
        window: 窗口大小（None = 使用全部）
    
    Returns:
        Signed Volume
    """
    tracker = SignedVolumeTracker()
    
    for trade in trades:
        tracker.add_trade(trade)
    
    return tracker.calculate_signed_volume(window)
