"""
Regime Filter - Task 1.6.1 Phase C

Purpose:
    市場狀態過濾器，決定當前市場是否適合交易。
    使用 VPIN + Spread/Depth 進行風險評估。

Architecture:
    Layer 1 (Signal) → Layer 2 (Regime) ✓ → Layer 3 (Execution)

Risk Filters:
    1. VPIN (Toxicity) - 檢測知情交易者流量
    2. Spread - 檢測流動性成本
    3. Depth - 檢測市場深度充足性
    4. Depth Imbalance - 檢測訂單簿失衡

Output:
    - Safe: True / False
    - Risk Level: SAFE / WARNING / DANGER / CRITICAL
    - Blocked Reasons: 阻擋原因列表

Author: GitHub Copilot
Created: 2025-11-10 (Task 1.6.1 - C2)
"""

from typing import Dict, Tuple, List, Optional
from datetime import datetime
from collections import deque


class RegimeFilter:
    """
    市場狀態過濾器
    
    使用多個風險指標評估當前市場是否適合交易：
    1. VPIN: 檢測 toxic flow（知情交易者）
    2. Spread: 檢測交易成本
    3. Depth: 檢測流動性充足性
    4. Depth Imbalance: 檢測訂單簿異常
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        vpin_threshold: float = 0.5,         # VPIN 危險閾值
        spread_bps_threshold: float = 10.0,  # Spread 基點閾值
        min_depth_btc: float = 5.0,          # 最小深度（BTC）
        depth_imbalance_threshold: float = 0.7,  # 深度失衡閾值
        history_size: int = 100
    ):
        """
        初始化市場狀態過濾器
        
        Args:
            symbol: 交易對
            vpin_threshold: VPIN 危險閾值（>0.5 = 高風險）
            spread_bps_threshold: Spread 基點閾值（>10 bps = 流動性差）
            min_depth_btc: 最小總深度（BTC）
            depth_imbalance_threshold: 深度失衡閾值（>0.7 = 嚴重失衡）
            history_size: 歷史記錄大小
        """
        self.symbol = symbol
        
        # 閾值配置
        self.vpin_threshold = vpin_threshold
        self.spread_bps_threshold = spread_bps_threshold
        self.min_depth_btc = min_depth_btc
        self.depth_imbalance_threshold = depth_imbalance_threshold
        
        # 歷史記錄
        self.regime_history: deque = deque(maxlen=history_size)
        
        # 統計數據
        self.total_checks = 0
        self.safe_count = 0
        self.blocked_count = 0
        self.block_reasons = {
            'vpin': 0,
            'spread': 0,
            'depth': 0,
            'depth_imbalance': 0
        }
    
    def check_regime(self, market_data: dict) -> Tuple[bool, str, dict]:
        """
        檢查市場狀態是否適合交易
        
        Args:
            market_data: 市場數據字典，包含：
                - 'vpin': VPIN 值（0-1）
                - 'spread_bps': Spread 基點
                - 'total_depth': 總深度（BTC）
                - 'depth_imbalance': 深度失衡度（-1 到 1）
                - 'timestamp': 時間戳（可選）
        
        Returns:
            (is_safe, risk_level, details)
            - is_safe: 是否可以交易
            - risk_level: "SAFE" / "WARNING" / "DANGER" / "CRITICAL"
            - details: 詳細信息
        """
        # 提取市場數據
        vpin = market_data.get('vpin')
        spread_bps = market_data.get('spread_bps')
        total_depth = market_data.get('total_depth')
        depth_imbalance = market_data.get('depth_imbalance')
        timestamp = market_data.get('timestamp', datetime.now().timestamp() * 1000)
        
        # 阻擋原因列表
        blocked_reasons = []
        risk_factors = []
        
        # === 檢查 1: VPIN（最高優先級）===
        vpin_risk = "SAFE"
        if vpin is not None:
            if vpin > 0.7:
                blocked_reasons.append(f"極高 VPIN ({vpin:.3f} > 0.7) - Flash Crash 風險")
                risk_factors.append(('vpin', 'CRITICAL', vpin))
                vpin_risk = "CRITICAL"
                self.block_reasons['vpin'] += 1
            elif vpin > self.vpin_threshold:
                blocked_reasons.append(f"高 VPIN ({vpin:.3f} > {self.vpin_threshold}) - Toxic flow 風險")
                risk_factors.append(('vpin', 'DANGER', vpin))
                vpin_risk = "DANGER"
                self.block_reasons['vpin'] += 1
            elif vpin > 0.3:
                risk_factors.append(('vpin', 'WARNING', vpin))
                vpin_risk = "WARNING"
        
        # === 檢查 2: Spread（流動性成本）===
        spread_risk = "SAFE"
        if spread_bps is not None:
            if spread_bps > self.spread_bps_threshold * 2:
                blocked_reasons.append(f"極寬價差 ({spread_bps:.2f} bps) - 成本過高")
                risk_factors.append(('spread', 'CRITICAL', spread_bps))
                spread_risk = "CRITICAL"
                self.block_reasons['spread'] += 1
            elif spread_bps > self.spread_bps_threshold:
                blocked_reasons.append(f"寬價差 ({spread_bps:.2f} bps > {self.spread_bps_threshold}) - 流動性差")
                risk_factors.append(('spread', 'DANGER', spread_bps))
                spread_risk = "DANGER"
                self.block_reasons['spread'] += 1
            elif spread_bps > 5.0:
                risk_factors.append(('spread', 'WARNING', spread_bps))
                spread_risk = "WARNING"
        
        # === 檢查 3: Depth（市場深度）===
        depth_risk = "SAFE"
        if total_depth is not None:
            if total_depth < self.min_depth_btc * 0.5:
                blocked_reasons.append(f"極低深度 ({total_depth:.2f} BTC) - 流動性枯竭")
                risk_factors.append(('depth', 'CRITICAL', total_depth))
                depth_risk = "CRITICAL"
                self.block_reasons['depth'] += 1
            elif total_depth < self.min_depth_btc:
                blocked_reasons.append(f"低深度 ({total_depth:.2f} BTC < {self.min_depth_btc}) - 流動性不足")
                risk_factors.append(('depth', 'DANGER', total_depth))
                depth_risk = "DANGER"
                self.block_reasons['depth'] += 1
            elif total_depth < self.min_depth_btc * 1.5:
                risk_factors.append(('depth', 'WARNING', total_depth))
                depth_risk = "WARNING"
        
        # === 檢查 4: Depth Imbalance（訂單簿失衡）===
        imbalance_risk = "SAFE"
        if depth_imbalance is not None:
            abs_imbalance = abs(depth_imbalance)
            if abs_imbalance > 0.9:
                blocked_reasons.append(f"極度失衡 ({depth_imbalance:+.2f}) - 單邊市場")
                risk_factors.append(('depth_imbalance', 'CRITICAL', depth_imbalance))
                imbalance_risk = "CRITICAL"
                self.block_reasons['depth_imbalance'] += 1
            elif abs_imbalance > self.depth_imbalance_threshold:
                blocked_reasons.append(f"嚴重失衡 ({depth_imbalance:+.2f}) - 訂單簿不平衡")
                risk_factors.append(('depth_imbalance', 'DANGER', depth_imbalance))
                imbalance_risk = "DANGER"
                self.block_reasons['depth_imbalance'] += 1
            elif abs_imbalance > 0.5:
                risk_factors.append(('depth_imbalance', 'WARNING', depth_imbalance))
                imbalance_risk = "WARNING"
        
        # === 綜合風險評估 ===
        risk_levels = [vpin_risk, spread_risk, depth_risk, imbalance_risk]
        
        # 計算最高風險等級
        if "CRITICAL" in risk_levels:
            overall_risk = "CRITICAL"
        elif "DANGER" in risk_levels:
            overall_risk = "DANGER"
        elif "WARNING" in risk_levels:
            overall_risk = "WARNING"
        else:
            overall_risk = "SAFE"
        
        # 決定是否可以交易（只有在 DANGER 或 CRITICAL 時阻擋）
        is_safe = overall_risk not in ["DANGER", "CRITICAL"]
        
        # 更新統計
        self.total_checks += 1
        if is_safe:
            self.safe_count += 1
        else:
            self.blocked_count += 1
        
        # 記錄詳細資訊
        details = {
            'is_safe': is_safe,
            'risk_level': overall_risk,
            'blocked_reasons': blocked_reasons,
            'risk_factors': risk_factors,
            'checks': {
                'vpin': {
                    'value': vpin,
                    'threshold': self.vpin_threshold,
                    'risk': vpin_risk
                },
                'spread': {
                    'value': spread_bps,
                    'threshold': self.spread_bps_threshold,
                    'risk': spread_risk
                },
                'depth': {
                    'value': total_depth,
                    'threshold': self.min_depth_btc,
                    'risk': depth_risk
                },
                'depth_imbalance': {
                    'value': depth_imbalance,
                    'threshold': self.depth_imbalance_threshold,
                    'risk': imbalance_risk
                }
            },
            'timestamp': timestamp
        }
        
        # 儲存歷史
        self.regime_history.append(details)
        
        return is_safe, overall_risk, details
    
    def get_statistics(self) -> dict:
        """
        獲取統計資料
        
        Returns:
            統計資料字典
        """
        safe_ratio = self.safe_count / self.total_checks if self.total_checks > 0 else 0
        blocked_ratio = self.blocked_count / self.total_checks if self.total_checks > 0 else 0
        
        stats = {
            'symbol': self.symbol,
            'total_checks': self.total_checks,
            'safe_count': self.safe_count,
            'blocked_count': self.blocked_count,
            'safe_ratio': safe_ratio,
            'blocked_ratio': blocked_ratio,
            'block_reasons': self.block_reasons.copy(),
            'history_size': len(self.regime_history)
        }
        
        # 計算平均風險因子
        if self.regime_history:
            vpin_values = [h['checks']['vpin']['value'] for h in self.regime_history 
                          if h['checks']['vpin']['value'] is not None]
            spread_values = [h['checks']['spread']['value'] for h in self.regime_history 
                            if h['checks']['spread']['value'] is not None]
            
            if vpin_values:
                stats['avg_vpin'] = sum(vpin_values) / len(vpin_values)
            if spread_values:
                stats['avg_spread_bps'] = sum(spread_values) / len(spread_values)
        
        return stats
    
    def get_recent_regimes(self, count: int = 10) -> List[dict]:
        """
        獲取最近的市場狀態記錄
        
        Args:
            count: 返回的記錄數量
        
        Returns:
            最近的狀態列表
        """
        if not self.regime_history:
            return []
        
        return list(self.regime_history)[-count:]
    
    def analyze_market_stability(self, window: int = 20) -> dict:
        """
        分析市場穩定性
        
        Args:
            window: 分析窗口大小
        
        Returns:
            穩定性分析結果
        """
        if len(self.regime_history) < window:
            return {
                'sufficient_data': False,
                'message': f'需要至少 {window} 個記錄，當前只有 {len(self.regime_history)}'
            }
        
        recent = list(self.regime_history)[-window:]
        
        # 統計風險等級分布
        safe_count = sum(1 for r in recent if r['risk_level'] == 'SAFE')
        warning_count = sum(1 for r in recent if r['risk_level'] == 'WARNING')
        danger_count = sum(1 for r in recent if r['risk_level'] == 'DANGER')
        critical_count = sum(1 for r in recent if r['risk_level'] == 'CRITICAL')
        
        # 計算穩定性得分（0-1，1=最穩定）
        stability_score = (safe_count * 1.0 + warning_count * 0.7 + 
                          danger_count * 0.3 + critical_count * 0.0) / window
        
        # 判斷市場狀態
        if stability_score > 0.8:
            market_state = "VERY_STABLE"
        elif stability_score > 0.6:
            market_state = "STABLE"
        elif stability_score > 0.4:
            market_state = "VOLATILE"
        else:
            market_state = "VERY_VOLATILE"
        
        return {
            'sufficient_data': True,
            'window_size': window,
            'safe_count': safe_count,
            'warning_count': warning_count,
            'danger_count': danger_count,
            'critical_count': critical_count,
            'stability_score': stability_score,
            'market_state': market_state,
            'tradeable_ratio': (safe_count + warning_count) / window
        }
    
    def reset(self):
        """重置過濾器"""
        self.regime_history.clear()
        self.total_checks = 0
        self.safe_count = 0
        self.blocked_count = 0
        self.block_reasons = {
            'vpin': 0,
            'spread': 0,
            'depth': 0,
            'depth_imbalance': 0
        }
