#!/usr/bin/env python3
"""
📈 趨勢分析器 v1.0
基於幣安 API 分析市場大方向，動態調整 LONG/SHORT 門檻
"""

import requests
from datetime import datetime, timedelta
from typing import Dict, Tuple
import json
import logging

logger = logging.getLogger(__name__)


class TrendAnalyzer:
    """分析市場趨勢並提供門檻建議"""
    
    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol
        self.base_url = "https://api.binance.com/api/v3"
        self._cache = {}
        self._cache_time = None
        self._cache_ttl = 300  # 5 分鐘緩存
    
    def get_daily_trend(self, days: int = 7) -> Dict:
        """獲取日線趨勢"""
        try:
            url = f"{self.base_url}/klines"
            params = {
                'symbol': self.symbol,
                'interval': '1d',
                'limit': days
            }
            resp = requests.get(url, params=params, timeout=10)
            klines = resp.json()
            
            # 計算累計變化
            total_change = 0
            up_days = 0
            down_days = 0
            
            for k in klines:
                open_p = float(k[1])
                close_p = float(k[4])
                change_pct = (close_p - open_p) / open_p * 100
                total_change += change_pct
                if change_pct > 0:
                    up_days += 1
                else:
                    down_days += 1
            
            return {
                'total_change_pct': total_change,
                'up_days': up_days,
                'down_days': down_days,
                'trend': self._classify_trend(total_change, up_days, down_days, days)
            }
        except Exception as e:
            logger.error(f"獲取日線趨勢失敗: {e}")
            return {'trend': 'UNKNOWN', 'total_change_pct': 0}
    
    def get_hourly_trend(self, hours: int = 24) -> Dict:
        """獲取小時線趨勢 (更靈敏)"""
        try:
            url = f"{self.base_url}/klines"
            params = {
                'symbol': self.symbol,
                'interval': '1h',
                'limit': hours
            }
            resp = requests.get(url, params=params, timeout=10)
            klines = resp.json()
            
            if not klines:
                return {'trend': 'UNKNOWN', 'total_change_pct': 0}
            
            first_open = float(klines[0][1])
            last_close = float(klines[-1][4])
            total_change = (last_close - first_open) / first_open * 100
            
            return {
                'total_change_pct': total_change,
                'trend': self._classify_short_term_trend(total_change)
            }
        except Exception as e:
            logger.error(f"獲取小時線趨勢失敗: {e}")
            return {'trend': 'UNKNOWN', 'total_change_pct': 0}
    
    def _classify_trend(self, change: float, up_days: int, down_days: int, total_days: int) -> str:
        """分類趨勢"""
        # 強趨勢判定
        if change > 5 and up_days >= total_days * 0.6:
            return 'STRONG_UP'
        elif change < -5 and down_days >= total_days * 0.6:
            return 'STRONG_DOWN'
        # 中等趨勢
        elif change > 2:
            return 'UP'
        elif change < -2:
            return 'DOWN'
        # 盤整
        else:
            return 'SIDEWAYS'
    
    def _classify_short_term_trend(self, change: float) -> str:
        """分類短期趨勢"""
        if change > 3:
            return 'STRONG_UP'
        elif change > 1:
            return 'UP'
        elif change < -3:
            return 'STRONG_DOWN'
        elif change < -1:
            return 'DOWN'
        else:
            return 'SIDEWAYS'
    
    def get_recommended_thresholds(self) -> Dict:
        """
        根據趨勢推薦 LONG/SHORT 門檻
        
        原則:
        - 順勢交易放寬門檻 (順勢容易賺)
        - 逆勢交易提高門檻 (逆勢風險高)
        """
        # 使用緩存
        if self._cache_time and (datetime.now() - self._cache_time).seconds < self._cache_ttl:
            return self._cache
        
        daily = self.get_daily_trend(7)
        hourly = self.get_hourly_trend(24)
        
        trend = daily['trend']
        short_trend = hourly['trend']
        
        # 基準門檻
        base_long = 8
        base_short = 8
        
        # 根據日線趨勢調整
        if trend == 'STRONG_UP':
            # 強上漲：放寬 LONG，收緊 SHORT
            long_threshold = base_long - 2  # 6
            short_threshold = base_short + 2  # 10
            bias = 'LONG'
            reason = f"7日趨勢強上漲 ({daily['total_change_pct']:+.1f}%)"
        elif trend == 'UP':
            long_threshold = base_long - 1  # 7
            short_threshold = base_short + 1  # 9
            bias = 'LONG'
            reason = f"7日趨勢上漲 ({daily['total_change_pct']:+.1f}%)"
        elif trend == 'STRONG_DOWN':
            # 強下跌：收緊 LONG，放寬 SHORT
            long_threshold = base_long + 2  # 10
            short_threshold = base_short - 2  # 6
            bias = 'SHORT'
            reason = f"7日趨勢強下跌 ({daily['total_change_pct']:+.1f}%)"
        elif trend == 'DOWN':
            long_threshold = base_long + 1  # 9
            short_threshold = base_short - 1  # 7
            bias = 'SHORT'
            reason = f"7日趨勢下跌 ({daily['total_change_pct']:+.1f}%)"
        else:  # SIDEWAYS
            long_threshold = base_long
            short_threshold = base_short
            bias = 'NEUTRAL'
            reason = f"7日盤整 ({daily['total_change_pct']:+.1f}%)"
        
        # 短期趨勢微調 (±1)
        if short_trend in ('STRONG_UP', 'UP') and bias != 'LONG':
            long_threshold = max(6, long_threshold - 1)
            reason += f" | 24h偏多 ({hourly['total_change_pct']:+.1f}%)"
        elif short_trend in ('STRONG_DOWN', 'DOWN') and bias != 'SHORT':
            short_threshold = max(6, short_threshold - 1)
            reason += f" | 24h偏空 ({hourly['total_change_pct']:+.1f}%)"
        
        result = {
            'long_threshold': long_threshold,
            'short_threshold': short_threshold,
            'bias': bias,
            'reason': reason,
            'daily_trend': trend,
            'daily_change_pct': daily['total_change_pct'],
            'hourly_trend': short_trend,
            'hourly_change_pct': hourly['total_change_pct'],
            'timestamp': datetime.now().isoformat()
        }
        
        # 更新緩存
        self._cache = result
        self._cache_time = datetime.now()
        
        return result
    
    def print_analysis(self):
        """打印完整分析報告"""
        rec = self.get_recommended_thresholds()
        
        print("=" * 60)
        print("📈 趨勢分析報告")
        print("=" * 60)
        print()
        print(f"📅 7日趨勢: {rec['daily_trend']} ({rec['daily_change_pct']:+.2f}%)")
        print(f"⏰ 24h趨勢: {rec['hourly_trend']} ({rec['hourly_change_pct']:+.2f}%)")
        print()
        print(f"🎯 建議方向偏好: {rec['bias']}")
        print(f"📝 原因: {rec['reason']}")
        print()
        print("=" * 60)
        print("📊 建議門檻")
        print("=" * 60)
        print(f"  LONG 門檻: {rec['long_threshold']} 分")
        print(f"  SHORT 門檻: {rec['short_threshold']} 分")
        print()
        
        # 與靜態配置比較
        print("📋 與當前配置比較:")
        try:
            with open('config/trading_cards/optimal_v1.json') as f:
                cfg = json.load(f)
            current_long = cfg.get('six_dim_threshold', {}).get('six_dim_min_score_long', 10)
            current_short = cfg.get('six_dim_threshold', {}).get('six_dim_min_score_short', 8)
            
            long_diff = rec['long_threshold'] - current_long
            short_diff = rec['short_threshold'] - current_short
            
            print(f"  LONG: 當前 {current_long} → 建議 {rec['long_threshold']} ({long_diff:+d})")
            print(f"  SHORT: 當前 {current_short} → 建議 {rec['short_threshold']} ({short_diff:+d})")
        except:
            pass
        
        return rec


def update_card_thresholds(card_path: str, long_threshold: int, short_threshold: int, reason: str):
    """更新卡片配置的門檻"""
    with open(card_path, 'r') as f:
        cfg = json.load(f)
    
    if 'six_dim_threshold' not in cfg:
        cfg['six_dim_threshold'] = {}
    
    cfg['six_dim_threshold']['six_dim_min_score_long'] = long_threshold
    cfg['six_dim_threshold']['six_dim_min_score_short'] = short_threshold
    cfg['six_dim_threshold']['_trend_adjusted'] = True
    cfg['six_dim_threshold']['_trend_reason'] = reason
    cfg['six_dim_threshold']['_trend_update_time'] = datetime.now().isoformat()
    
    with open(card_path, 'w') as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    
    print(f"✅ 已更新 {card_path}")
    print(f"   LONG: {long_threshold}, SHORT: {short_threshold}")
    print(f"   原因: {reason}")


if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description='趨勢分析器')
    parser.add_argument('--update', action='store_true', help='手動確認更新卡片')
    parser.add_argument('--update-if-needed', action='store_true', help='自動更新卡片(無需確認)')
    parser.add_argument('--card', default='optimal_v1', help='卡片名稱')
    parser.add_argument('--quiet', '-q', action='store_true', help='安靜模式')
    args = parser.parse_args()
    
    analyzer = TrendAnalyzer()
    rec = analyzer.print_analysis()
    
    card_path = f'config/trading_cards/{args.card}.json'
    
    # 檢查是否需要更新
    need_update = False
    try:
        with open(card_path) as f:
            cfg = json.load(f)
        current_long = cfg.get('six_dim_threshold', {}).get('six_dim_min_score_long', 10)
        current_short = cfg.get('six_dim_threshold', {}).get('six_dim_min_score_short', 8)
        
        # 如果建議與當前不同，需要更新
        if rec['long_threshold'] != current_long or rec['short_threshold'] != current_short:
            need_update = True
            print()
            print(f"⚠️ 建議調整門檻:")
            print(f"   LONG: {current_long} → {rec['long_threshold']}")
            print(f"   SHORT: {current_short} → {rec['short_threshold']}")
    except Exception as e:
        print(f"⚠️ 讀取卡片失敗: {e}")
    
    if args.update_if_needed and need_update:
        # 自動更新 (不需確認)
        print()
        print("🔄 自動更新門檻...")
        update_card_thresholds(
            card_path,
            rec['long_threshold'],
            rec['short_threshold'],
            rec['reason']
        )
    elif args.update:
        # 手動確認更新
        print()
        confirm = input(f"確定要更新 {args.card}.json 的門檻嗎? (y/n): ")
        if confirm.lower() == 'y':
            update_card_thresholds(
                card_path,
                rec['long_threshold'],
                rec['short_threshold'],
                rec['reason']
            )
    else:
        print()
        print("=" * 60)
        print("💡 使用方式")
        print("=" * 60)
        print("1. 手動查看建議: python scripts/trend_analyzer.py")
        print("2. 手動更新卡片: python scripts/trend_analyzer.py --update")
        print("3. 自動更新卡片: python scripts/trend_analyzer.py --update-if-needed")
        print("4. 指定卡片: python scripts/trend_analyzer.py --card optimal_v1 --update")

