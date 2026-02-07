"""
VPIN (Volume-Synchronized Probability of Informed Trading) Calculator

Purpose:
    檢測市場中的「toxic flow」（知情交易者流量），用於識別高風險交易環境。
    
Academic Reference:
    Easley, D., López de Prado, M. M., & O'Hara, M. (2012).
    "Flow Toxicity and Liquidity in a High-Frequency World."
    The Review of Financial Studies, 25(5), 1457-1493.

Key Concepts:
    1. Volume Clock: 以成交量而非時間切分市場
    2. Bucket: 固定成交量的時間段（例如：每 10,000 USDT）
    3. Imbalance: 每個 bucket 內的買賣失衡度
    4. VPIN: 近期 N 個 bucket 的平均失衡度（0-1 之間）

Interpretation:
    - VPIN < 0.3: 健康市場，信息流均衡
    - VPIN 0.3-0.5: 謹慎交易，開始有知情交易者
    - VPIN > 0.5: 危險市場，避免激進策略
    - VPIN > 0.7: 極度危險，建議退出所有倉位

Use Case:
    在 Flash Crash 前，VPIN 會飆升到 0.9+，提前預警流動性崩潰。

Author: GitHub Copilot
Created: 2025-11-10 (Task 1.6.1 - B4)
"""

from collections import deque
from typing import List, Dict, Optional, Tuple
import numpy as np
from datetime import datetime


class VPINCalculator:
    """
    VPIN 計算器（Volume-Synchronized Probability of Informed Trading）
    
    使用 Volume Clock 將市場切分為等成交量的 bucket，
    計算每個 bucket 的買賣失衡度，然後取滑動平均得到 VPIN。
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        bucket_size: float = 50000,  # 每個 bucket 的目標成交量（USDT）
        num_buckets: int = 50,       # 用於計算 VPIN 的 bucket 數量
        use_dollar_volume: bool = True  # True=使用金額，False=使用數量
    ):
        """
        初始化 VPIN 計算器
        
        Args:
            symbol: 交易對符號
            bucket_size: 每個 bucket 的目標成交量
                        - 如果 use_dollar_volume=True: 單位為 USDT（建議 10000-100000）
                        - 如果 use_dollar_volume=False: 單位為 BTC（建議 1-10）
            num_buckets: 用於計算 VPIN 的 bucket 數量（建議 30-100）
            use_dollar_volume: 是否使用金額成交量（True 較穩定）
        """
        self.symbol = symbol
        self.bucket_size = bucket_size
        self.num_buckets = num_buckets
        self.use_dollar_volume = use_dollar_volume
        
        # 當前 bucket 的累計數據
        self.current_bucket = {
            'buy_volume': 0.0,
            'sell_volume': 0.0,
            'total_volume': 0.0,
            'start_time': None,
            'end_time': None,
            'trade_count': 0
        }
        
        # 已完成的 bucket 歷史（儲存失衡度）
        self.buckets: deque = deque(maxlen=num_buckets * 2)  # 保留更多歷史供分析
        
        # VPIN 計算歷史
        self.vpin_history: deque = deque(maxlen=1000)
        
        # 統計數據
        self.total_trades_processed = 0
        self.total_buckets_completed = 0
    
    def classify_trade_side(self, trade: dict) -> int:
        """
        分類交易方向（買方主動 vs 賣方主動）
        
        優先使用 Binance 的 isBuyerMaker 標記，
        回退到 Tick Rule（價格變化方向）。
        
        Args:
            trade: 交易資料，包含 price, quantity, isBuyerMaker 等
        
        Returns:
            1: 買方主動（aggressive buyer）
            -1: 賣方主動（aggressive seller）
            0: 無法判斷（極少出現）
        """
        # 優先使用 Binance 標記
        if 'isBuyerMaker' in trade:
            # isBuyerMaker=True → 買方掛單，賣方吃單（賣方主動）
            # isBuyerMaker=False → 賣方掛單，買方吃單（買方主動）
            return -1 if trade['isBuyerMaker'] else 1
        
        # 回退到 Tick Rule
        if 'm' in trade:
            return -1 if trade['m'] else 1
        
        # 最後回退：無法判斷
        return 0
    
    def process_trade(self, trade: dict) -> Optional[float]:
        """
        處理單筆交易，更新當前 bucket，必要時完成並計算 VPIN
        
        Args:
            trade: 交易資料
                   - 'p' or 'price': 成交價格
                   - 'q' or 'quantity': 成交數量
                   - 'T' or 'timestamp': 時間戳
                   - 'isBuyerMaker' or 'm': 買方是否為 maker
        
        Returns:
            如果完成了一個 bucket，返回最新的 VPIN 值
            否則返回 None
        """
        # 提取交易數據（兼容不同格式）
        price = float(trade.get('p') or trade.get('price', 0))
        quantity = float(trade.get('q') or trade.get('quantity', 0))
        timestamp = trade.get('T') or trade.get('timestamp', 0)
        
        # 分類交易方向
        side = self.classify_trade_side(trade)
        
        # 計算成交量（金額或數量）
        if self.use_dollar_volume:
            volume = price * quantity
        else:
            volume = quantity
        
        # 更新當前 bucket
        if self.current_bucket['start_time'] is None:
            self.current_bucket['start_time'] = timestamp
        
        self.current_bucket['end_time'] = timestamp
        self.current_bucket['total_volume'] += volume
        self.current_bucket['trade_count'] += 1
        
        if side == 1:
            self.current_bucket['buy_volume'] += volume
        elif side == -1:
            self.current_bucket['sell_volume'] += volume
        
        self.total_trades_processed += 1
        
        # 檢查是否完成一個 bucket
        if self.current_bucket['total_volume'] >= self.bucket_size:
            return self._complete_bucket()
        
        return None
    
    def _complete_bucket(self) -> Optional[float]:
        """
        完成當前 bucket，計算失衡度並更新 VPIN
        
        Returns:
            最新的 VPIN 值，如果 bucket 數量不足則返回 None
        """
        # 計算當前 bucket 的失衡度
        buy_vol = self.current_bucket['buy_volume']
        sell_vol = self.current_bucket['sell_volume']
        total_vol = self.current_bucket['total_volume']
        
        if total_vol > 0:
            imbalance = abs(buy_vol - sell_vol) / total_vol
        else:
            imbalance = 0.0
        
        # 儲存 bucket 資料
        bucket_data = {
            'imbalance': imbalance,
            'buy_volume': buy_vol,
            'sell_volume': sell_vol,
            'total_volume': total_vol,
            'start_time': self.current_bucket['start_time'],
            'end_time': self.current_bucket['end_time'],
            'trade_count': self.current_bucket['trade_count'],
            'bucket_id': self.total_buckets_completed
        }
        
        self.buckets.append(bucket_data)
        self.total_buckets_completed += 1
        
        # 重置當前 bucket
        self.current_bucket = {
            'buy_volume': 0.0,
            'sell_volume': 0.0,
            'total_volume': 0.0,
            'start_time': None,
            'end_time': None,
            'trade_count': 0
        }
        
        # 計算 VPIN
        vpin = self.calculate_vpin()
        
        if vpin is not None:
            self.vpin_history.append({
                'vpin': vpin,
                'timestamp': bucket_data['end_time'],
                'bucket_id': bucket_data['bucket_id']
            })
        
        return vpin
    
    def calculate_vpin(self) -> Optional[float]:
        """
        計算 VPIN（近期 N 個 bucket 的平均失衡度）
        
        Returns:
            VPIN 值（0-1 之間），如果 bucket 數量不足則返回 None
        """
        if len(self.buckets) < self.num_buckets:
            return None
        
        # 取最近的 N 個 bucket
        recent_buckets = list(self.buckets)[-self.num_buckets:]
        
        # 計算平均失衡度
        imbalances = [b['imbalance'] for b in recent_buckets]
        vpin = np.mean(imbalances)
        
        return float(vpin)
    
    def get_current_vpin(self) -> Optional[float]:
        """
        獲取最新的 VPIN 值
        
        Returns:
            最新的 VPIN 值，如果尚未計算則返回 None
        """
        if not self.vpin_history:
            return None
        return self.vpin_history[-1]['vpin']
    
    def assess_toxicity(self, vpin: Optional[float] = None) -> Tuple[str, str, dict]:
        """
        評估市場毒性風險等級
        
        Args:
            vpin: VPIN 值，如果為 None 則使用最新值
        
        Returns:
            (風險等級, 建議行動, 詳細資訊)
            
            風險等級:
                - "SAFE": 安全，可正常交易
                - "CAUTION": 謹慎，減少倉位
                - "DANGER": 危險，避免新倉位
                - "CRITICAL": 極度危險，退出所有倉位
        """
        if vpin is None:
            vpin = self.get_current_vpin()
        
        if vpin is None:
            return "UNKNOWN", "Data insufficient", {
                'buckets_completed': len(self.buckets),
                'required_buckets': self.num_buckets
            }
        
        # 風險閾值（基於 Easley et al. 2012）
        if vpin < 0.3:
            level = "SAFE"
            action = "Normal trading conditions"
            severity = 1
        elif vpin < 0.5:
            level = "CAUTION"
            action = "Reduce position size by 30-50%"
            severity = 2
        elif vpin < 0.7:
            level = "DANGER"
            action = "Avoid opening new positions"
            severity = 3
        else:
            level = "CRITICAL"
            action = "Close all positions immediately"
            severity = 4
        
        details = {
            'vpin': vpin,
            'level': level,
            'severity': severity,
            'action': action,
            'buckets_used': min(len(self.buckets), self.num_buckets),
            'total_buckets': len(self.buckets)
        }
        
        return level, action, details
    
    def get_statistics(self) -> dict:
        """
        獲取統計資料
        
        Returns:
            包含各項統計指標的字典
        """
        current_vpin = self.get_current_vpin()
        
        # 計算 VPIN 統計
        vpin_stats = {
            'current_vpin': current_vpin
        }
        if self.vpin_history:
            vpin_values = [v['vpin'] for v in self.vpin_history]
            vpin_stats.update({
                'mean_vpin': float(np.mean(vpin_values)),
                'std_vpin': float(np.std(vpin_values)),
                'min_vpin': float(np.min(vpin_values)),
                'max_vpin': float(np.max(vpin_values)),
                'vpin_history_size': len(self.vpin_history)
            })
        
        # 計算 bucket 統計
        bucket_stats = {}
        if self.buckets:
            imbalances = [b['imbalance'] for b in self.buckets]
            durations = []
            for b in self.buckets:
                if b['start_time'] and b['end_time']:
                    duration = (b['end_time'] - b['start_time']) / 1000  # 轉為秒
                    durations.append(duration)
            
            bucket_stats = {
                'mean_imbalance': float(np.mean(imbalances)),
                'std_imbalance': float(np.std(imbalances)),
                'mean_bucket_duration_sec': float(np.mean(durations)) if durations else None,
                'total_buckets_completed': self.total_buckets_completed
            }
        
        return {
            'symbol': self.symbol,
            'bucket_size': self.bucket_size,
            'num_buckets': self.num_buckets,
            'use_dollar_volume': self.use_dollar_volume,
            'total_trades_processed': self.total_trades_processed,
            'current_bucket_progress': {
                'volume': self.current_bucket['total_volume'],
                'progress_pct': (self.current_bucket['total_volume'] / self.bucket_size * 100) 
                               if self.bucket_size > 0 else 0,
                'trade_count': self.current_bucket['trade_count']
            },
            **vpin_stats,
            **bucket_stats
        }
    
    def get_vpin_trend(self, window: int = 10) -> Optional[str]:
        """
        獲取 VPIN 趨勢（上升/下降/穩定）
        
        Args:
            window: 分析窗口大小
        
        Returns:
            "RISING": VPIN 上升（風險增加）
            "FALLING": VPIN 下降（風險降低）
            "STABLE": VPIN 穩定
            None: 數據不足
        """
        if len(self.vpin_history) < window:
            return None
        
        recent_vpins = [v['vpin'] for v in list(self.vpin_history)[-window:]]
        
        # 計算線性回歸斜率
        x = np.arange(len(recent_vpins))
        slope = np.polyfit(x, recent_vpins, 1)[0]
        
        # 閾值：每個 bucket VPIN 變化 > 0.01 視為趨勢
        if slope > 0.01:
            return "RISING"
        elif slope < -0.01:
            return "FALLING"
        else:
            return "STABLE"
    
    def reset(self):
        """重置計算器到初始狀態"""
        self.current_bucket = {
            'buy_volume': 0.0,
            'sell_volume': 0.0,
            'total_volume': 0.0,
            'start_time': None,
            'end_time': None,
            'trade_count': 0
        }
        self.buckets.clear()
        self.vpin_history.clear()
        self.total_trades_processed = 0
        self.total_buckets_completed = 0
