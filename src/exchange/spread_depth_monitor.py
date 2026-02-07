"""
Spread & Depth Monitor - Task 1.6.1 - B5

監控市場流動性指標:
- Spread（價差）: best_ask - best_bid
- Depth（深度）: 各檔位的掛單量
- 流動性健康度檢測
"""

from typing import List, Dict, Optional, Tuple
from collections import deque
from datetime import datetime
import numpy as np


class SpreadDepthMonitor:
    """
    價差與深度監控器
    
    功能:
    - 監控 bid-ask spread
    - 計算深度加權 spread
    - 檢測流動性枯竭
    - 分析價差擴大/收縮趨勢
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        history_size: int = 100,
        depth_levels: int = 10
    ):
        """
        初始化監控器
        
        Args:
            symbol: 交易對
            history_size: 歷史記錄數量
            depth_levels: 監控深度層數
        """
        self.symbol = symbol.upper()
        self.history_size = history_size
        self.depth_levels = depth_levels
        
        # 歷史記錄
        self.spread_history: deque = deque(maxlen=history_size)
        
        # 統計
        self.stats = {
            'min_spread': float('inf'),
            'max_spread': 0.0,
            'mean_spread': 0.0,
            'current_spread': 0.0,
            'spread_volatility': 0.0
        }
    
    def calculate_spread(
        self,
        bids: List[List[float]],
        asks: List[List[float]]
    ) -> Dict[str, float]:
        """
        計算價差指標
        
        Args:
            bids: 買單列表 [[price, size], ...]
            asks: 賣單列表 [[price, size], ...]
        
        Returns:
            {
                'absolute_spread': float,      # 絕對價差（USDT）
                'relative_spread': float,      # 相對價差（百分比）
                'mid_price': float,            # 中間價
                'spread_bps': float            # 價差（基點 1/10000）
            }
        """
        if not bids or not asks:
            return {
                'absolute_spread': 0.0,
                'relative_spread': 0.0,
                'mid_price': 0.0,
                'spread_bps': 0.0
            }
        
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        
        # 絕對價差
        absolute_spread = best_ask - best_bid
        
        # 中間價
        mid_price = (best_bid + best_ask) / 2
        
        # 相對價差（百分比）
        if mid_price > 0:
            relative_spread = absolute_spread / mid_price
        else:
            relative_spread = 0.0
        
        # 基點（1 basis point = 0.01%）
        spread_bps = relative_spread * 10000
        
        return {
            'absolute_spread': absolute_spread,
            'relative_spread': relative_spread,
            'mid_price': mid_price,
            'spread_bps': spread_bps
        }
    
    def calculate_depth(
        self,
        bids: List[List[float]],
        asks: List[List[float]],
        levels: int = None
    ) -> Dict[str, float]:
        """
        計算訂單簿深度
        
        Args:
            bids: 買單列表
            asks: 賣單列表
            levels: 計算層數（預設使用 self.depth_levels）
        
        Returns:
            {
                'bid_depth': float,          # 買單總深度（BTC）
                'ask_depth': float,          # 賣單總深度（BTC）
                'total_depth': float,        # 總深度
                'depth_imbalance': float,    # 深度失衡 (-1 到 1)
                'bid_value': float,          # 買單總價值（USDT）
                'ask_value': float           # 賣單總價值（USDT）
            }
        """
        levels = levels or self.depth_levels
        
        if not bids or not asks:
            return {
                'bid_depth': 0.0,
                'ask_depth': 0.0,
                'total_depth': 0.0,
                'depth_imbalance': 0.0,
                'bid_value': 0.0,
                'ask_value': 0.0
            }
        
        # 取前 N 層
        bids_slice = bids[:min(levels, len(bids))]
        asks_slice = asks[:min(levels, len(asks))]
        
        # 計算深度（數量）
        bid_depth = sum([float(b[1]) for b in bids_slice])
        ask_depth = sum([float(a[1]) for a in asks_slice])
        total_depth = bid_depth + ask_depth
        
        # 計算價值（數量 * 價格）
        bid_value = sum([float(b[0]) * float(b[1]) for b in bids_slice])
        ask_value = sum([float(a[0]) * float(a[1]) for a in asks_slice])
        
        # 深度失衡
        if total_depth > 0:
            depth_imbalance = (bid_depth - ask_depth) / total_depth
        else:
            depth_imbalance = 0.0
        
        return {
            'bid_depth': bid_depth,
            'ask_depth': ask_depth,
            'total_depth': total_depth,
            'depth_imbalance': depth_imbalance,
            'bid_value': bid_value,
            'ask_value': ask_value
        }
    
    def calculate_depth_weighted_spread(
        self,
        bids: List[List[float]],
        asks: List[List[float]]
    ) -> Dict[str, float]:
        """
        計算深度加權價差
        
        深度失衡會放大有效價差：
        - 買單深度 >> 賣單深度 → 賣出容易，但買入難（spread 放大）
        - 賣單深度 >> 買單深度 → 買入容易，但賣出難（spread 放大）
        
        Args:
            bids: 買單列表
            asks: 賣單列表
        
        Returns:
            {
                'base_spread': float,              # 基礎價差
                'depth_weighted_spread': float,    # 深度加權價差
                'liquidity_penalty': float         # 流動性懲罰係數
            }
        """
        spread_data = self.calculate_spread(bids, asks)
        depth_data = self.calculate_depth(bids, asks)
        
        base_spread = spread_data['relative_spread']
        depth_imbalance = depth_data['depth_imbalance']
        
        # 流動性懲罰係數（深度失衡越大，懲罰越大）
        liquidity_penalty = 1 + abs(depth_imbalance)
        
        # 深度加權價差
        depth_weighted_spread = base_spread * liquidity_penalty
        
        return {
            'base_spread': base_spread,
            'depth_weighted_spread': depth_weighted_spread,
            'liquidity_penalty': liquidity_penalty
        }
    
    def calculate_effective_spread(
        self,
        bids: List[List[float]],
        asks: List[List[float]],
        trade_size: float = 1.0
    ) -> Dict[str, float]:
        """
        計算有效價差（考慮滑點）
        
        有效價差 = 實際成交價格 vs 中間價的偏離
        對於大單，需要穿透多個檔位
        
        Args:
            bids: 買單列表
            asks: 賣單列表
            trade_size: 交易大小（BTC）
        
        Returns:
            {
                'effective_buy_price': float,   # 買入有效價格
                'effective_sell_price': float,  # 賣出有效價格
                'effective_spread': float,      # 有效價差
                'slippage': float               # 滑點（vs mid price）
            }
        """
        if not bids or not asks:
            return {
                'effective_buy_price': 0.0,
                'effective_sell_price': 0.0,
                'effective_spread': 0.0,
                'slippage': 0.0
            }
        
        mid_price = (float(bids[0][0]) + float(asks[0][0])) / 2
        
        # 計算買入有效價格（吃賣單）
        remaining_size = trade_size
        total_cost = 0.0
        
        for price_str, size_str in asks:
            price = float(price_str)
            size = float(size_str)
            
            if remaining_size <= size:
                total_cost += remaining_size * price
                remaining_size = 0
                break
            else:
                total_cost += size * price
                remaining_size -= size
        
        if remaining_size > 0:
            # 流動性不足
            effective_buy_price = float('inf')
        else:
            effective_buy_price = total_cost / trade_size
        
        # 計算賣出有效價格（吃買單）
        remaining_size = trade_size
        total_revenue = 0.0
        
        for price_str, size_str in bids:
            price = float(price_str)
            size = float(size_str)
            
            if remaining_size <= size:
                total_revenue += remaining_size * price
                remaining_size = 0
                break
            else:
                total_revenue += size * price
                remaining_size -= size
        
        if remaining_size > 0:
            # 流動性不足
            effective_sell_price = 0.0
        else:
            effective_sell_price = total_revenue / trade_size
        
        # 有效價差
        if effective_buy_price != float('inf') and effective_sell_price > 0:
            effective_spread = (effective_buy_price - effective_sell_price) / mid_price
            slippage = ((effective_buy_price + effective_sell_price) / 2 - mid_price) / mid_price
        else:
            effective_spread = float('inf')
            slippage = float('inf')
        
        return {
            'effective_buy_price': effective_buy_price,
            'effective_sell_price': effective_sell_price,
            'effective_spread': effective_spread,
            'slippage': slippage
        }
    
    def detect_liquidity_crisis(
        self,
        bids: List[List[float]],
        asks: List[List[float]],
        thresholds: Dict[str, float] = None
    ) -> Tuple[bool, str, Dict[str, float]]:
        """
        檢測流動性危機
        
        Args:
            bids: 買單列表
            asks: 賣單列表
            thresholds: 閾值字典
        
        Returns:
            (is_crisis, severity, details)
        """
        if thresholds is None:
            thresholds = {
                'spread_bps': 10.0,        # 價差 > 10 bps 警告
                'depth_ratio': 0.7,        # 深度失衡 > 70% 警告
                'total_depth': 5.0         # 總深度 < 5 BTC 警告
            }
        
        spread_data = self.calculate_spread(bids, asks)
        depth_data = self.calculate_depth(bids, asks)
        
        spread_bps = spread_data['spread_bps']
        depth_imbalance = abs(depth_data['depth_imbalance'])
        total_depth = depth_data['total_depth']
        
        issues = []
        
        # 檢查價差
        if spread_bps > thresholds['spread_bps']:
            issues.append(f"寬價差 ({spread_bps:.2f} bps)")
        
        # 檢查深度失衡
        if depth_imbalance > thresholds['depth_ratio']:
            issues.append(f"深度失衡 ({depth_imbalance*100:.1f}%)")
        
        # 檢查總深度
        if total_depth < thresholds['total_depth']:
            issues.append(f"流動性不足 ({total_depth:.2f} BTC)")
        
        if issues:
            severity = "CRITICAL" if len(issues) >= 2 else "WARNING"
            return (True, severity, {
                'issues': issues,
                'spread_bps': spread_bps,
                'depth_imbalance': depth_imbalance,
                'total_depth': total_depth
            })
        
        return (False, "HEALTHY", {
            'spread_bps': spread_bps,
            'depth_imbalance': depth_imbalance,
            'total_depth': total_depth
        })
    
    def update(
        self,
        bids: List[List[float]],
        asks: List[List[float]]
    ):
        """
        更新監控數據
        
        Args:
            bids: 買單列表
            asks: 賣單列表
        """
        spread_data = self.calculate_spread(bids, asks)
        
        # 記錄歷史
        self.spread_history.append({
            'timestamp': datetime.utcnow(),
            'spread': spread_data['relative_spread'],
            'spread_bps': spread_data['spread_bps']
        })
        
        # 更新統計
        self.stats['current_spread'] = spread_data['relative_spread']
        self.stats['min_spread'] = min(self.stats['min_spread'], spread_data['relative_spread'])
        self.stats['max_spread'] = max(self.stats['max_spread'], spread_data['relative_spread'])
        
        if len(self.spread_history) > 0:
            spreads = [h['spread'] for h in self.spread_history]
            self.stats['mean_spread'] = np.mean(spreads)
            self.stats['spread_volatility'] = np.std(spreads)
    
    def get_statistics(self) -> Dict:
        """獲取統計信息"""
        return {
            **self.stats,
            'history_size': len(self.spread_history)
        }
