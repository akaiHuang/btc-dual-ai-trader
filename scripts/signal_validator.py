#!/usr/bin/env python3
"""
📊 信號驗證與校正系統 v1.0
Signal Validation & Calibration System

功能：
1. 驗證六維系統準確率
2. 驗證主力策略偵測準確率
3. 驗證其他指標準確率
4. 發現新指標機會
5. 自動校正參數

使用幣安 API 獲取實際價格數據進行驗證
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
import numpy as np

# 添加專案路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False
    print("⚠️ ccxt 未安裝，部分功能受限")


class SignalValidator:
    """信號驗證器"""
    
    def __init__(self, log_dir: str = "logs/whale_paper_trader"):
        self.log_dir = Path(log_dir)
        self.calibration_file = Path("config/calibration/signal_calibration.json")
        self.calibration = self._load_calibration()
        
        # 幣安連接
        self.exchange = None
        if CCXT_AVAILABLE:
            try:
                self.exchange = ccxt.binance({'timeout': 10000})
                print("✅ 幣安 API 連接成功")
            except Exception as e:
                print(f"⚠️ 幣安連接失敗: {e}")
    
    def _load_calibration(self) -> Dict:
        """載入校正配置"""
        if self.calibration_file.exists():
            with open(self.calibration_file, 'r') as f:
                return json.load(f)
        return {}
    
    def _save_calibration(self):
        """保存校正配置"""
        self.calibration_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.calibration_file, 'w') as f:
            json.dump(self.calibration, f, indent=2, ensure_ascii=False)
    
    def load_signals(self, hours: int = 24) -> List[Dict]:
        """載入最近 N 小時的信號數據"""
        signals = []
        cutoff = datetime.now() - timedelta(hours=hours)
        
        for file in sorted(self.log_dir.glob("signals_*.json"), reverse=True):
            try:
                with open(file, 'r') as f:
                    data = json.load(f)
                    for sig in data.get('signals', []):
                        ts = datetime.fromisoformat(sig['timestamp'].replace('Z', '+00:00').replace('+00:00', ''))
                        if ts > cutoff:
                            sig['_file'] = file.name
                            signals.append(sig)
            except Exception as e:
                print(f"⚠️ 無法載入 {file}: {e}")
        
        print(f"📊 載入 {len(signals)} 筆信號 (最近 {hours} 小時)")
        return signals
    
    def load_trades(self, hours: int = 24) -> List[Dict]:
        """載入最近 N 小時的交易數據"""
        trades = []
        cutoff = datetime.now() - timedelta(hours=hours)
        
        for file in sorted(self.log_dir.glob("trades_*.json"), reverse=True):
            try:
                with open(file, 'r') as f:
                    data = json.load(f)
                    for trade in data.get('trades', []):
                        ts = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00').replace('+00:00', ''))
                        if ts > cutoff:
                            trade['_file'] = file.name
                            trades.append(trade)
            except Exception as e:
                pass
        
        print(f"📊 載入 {len(trades)} 筆交易 (最近 {hours} 小時)")
        return trades
    
    def get_binance_klines(self, start_time: datetime, end_time: datetime, 
                          interval: str = '1m') -> List[Dict]:
        """獲取幣安 K 線數據"""
        if not self.exchange:
            print("⚠️ 幣安 API 未連接")
            return []
        
        try:
            since = int(start_time.timestamp() * 1000)
            duration_minutes = int((end_time - start_time).total_seconds() / 60) + 10
            limit = max(1, min(1000, duration_minutes))
            
            ohlcv = self.exchange.fetch_ohlcv('BTC/USDT', interval, since=since, limit=limit)
            
            klines = []
            for k in ohlcv:
                klines.append({
                    'timestamp': datetime.fromtimestamp(k[0] / 1000),
                    'open': k[1],
                    'high': k[2],
                    'low': k[3],
                    'close': k[4],
                    'volume': k[5]
                })
            
            return klines
        except Exception as e:
            print(f"⚠️ 獲取 K 線失敗: {e}")
            return []
    
    def get_binance_orderbook_stats(self) -> Dict:
        """獲取幣安訂單簿統計"""
        if not self.exchange:
            return {}
        
        try:
            orderbook = self.exchange.fetch_order_book('BTC/USDT', limit=20)
            
            bids = orderbook['bids']
            asks = orderbook['asks']
            
            bid_volume = sum([b[1] for b in bids[:10]])
            ask_volume = sum([a[1] for a in asks[:10]])
            
            obi = (bid_volume - ask_volume) / (bid_volume + ask_volume) if (bid_volume + ask_volume) > 0 else 0
            
            return {
                'obi': obi,
                'bid_volume': bid_volume,
                'ask_volume': ask_volume,
                'spread_pct': (asks[0][0] - bids[0][0]) / bids[0][0] * 100 if bids else 0,
                'mid_price': (bids[0][0] + asks[0][0]) / 2 if bids and asks else 0
            }
        except Exception as e:
            print(f"⚠️ 獲取訂單簿失敗: {e}")
            return {}
    
    def validate_six_dim(self, signals: List[Dict], lookahead_minutes: int = 5) -> Dict:
        """
        驗證六維系統準確率
        
        方法：檢查信號方向是否與未來 N 分鐘價格走向一致
        """
        print("\n" + "="*60)
        print("🔍 驗證六維系統準確率")
        print("="*60)
        
        results = {
            'total': 0,
            'correct': 0,
            'wrong': 0,
            'by_score': defaultdict(lambda: {'total': 0, 'correct': 0}),
            'by_direction': {'LONG': {'total': 0, 'correct': 0}, 'SHORT': {'total': 0, 'correct': 0}},
            'profit_when_correct': [],
            'loss_when_wrong': []
        }
        
        # 篩選有六維數據的進場信號
        entry_signals = [s for s in signals if s.get('signal_type') == 'ENTERED' and s.get('six_dim')]
        
        if not entry_signals:
            print("⚠️ 沒有找到六維進場信號")
            return results
        
        # 獲取價格數據範圍
        first_time = datetime.fromisoformat(entry_signals[0]['timestamp'].replace('Z', ''))
        last_time = datetime.fromisoformat(entry_signals[-1]['timestamp'].replace('Z', ''))
        
        klines = self.get_binance_klines(
            first_time - timedelta(minutes=5),
            last_time + timedelta(minutes=lookahead_minutes + 5)
        )
        
        if not klines:
            print("⚠️ 無法獲取幣安 K 線數據")
            return results
        
        # 建立時間索引
        kline_dict = {k['timestamp'].strftime('%Y-%m-%d %H:%M'): k for k in klines}
        
        for sig in entry_signals:
            sig_time = datetime.fromisoformat(sig['timestamp'].replace('Z', ''))
            sig_minute = sig_time.strftime('%Y-%m-%d %H:%M')
            
            # 找進場時的 K 線
            entry_kline = kline_dict.get(sig_minute)
            if not entry_kline:
                continue
            
            # 找 N 分鐘後的 K 線
            future_time = (sig_time + timedelta(minutes=lookahead_minutes)).strftime('%Y-%m-%d %H:%M')
            future_kline = kline_dict.get(future_time)
            if not future_kline:
                continue
            
            direction = sig.get('direction', '')
            six_dim = sig.get('six_dim', {})
            score = max(six_dim.get('long_score', 0), six_dim.get('short_score', 0))
            
            entry_price = entry_kline['close']
            future_price = future_kline['close']
            price_change_pct = (future_price - entry_price) / entry_price * 100
            
            # 判斷是否正確
            is_correct = False
            if direction == 'LONG' and price_change_pct > 0:
                is_correct = True
            elif direction == 'SHORT' and price_change_pct < 0:
                is_correct = True
            
            results['total'] += 1
            results['by_score'][score]['total'] += 1
            results['by_direction'][direction]['total'] += 1
            
            if is_correct:
                results['correct'] += 1
                results['by_score'][score]['correct'] += 1
                results['by_direction'][direction]['correct'] += 1
                results['profit_when_correct'].append(abs(price_change_pct))
            else:
                results['wrong'] += 1
                results['loss_when_wrong'].append(abs(price_change_pct))
        
        # 計算統計
        if results['total'] > 0:
            results['accuracy'] = results['correct'] / results['total'] * 100
            results['avg_profit'] = np.mean(results['profit_when_correct']) if results['profit_when_correct'] else 0
            results['avg_loss'] = np.mean(results['loss_when_wrong']) if results['loss_when_wrong'] else 0
            
            print(f"\n📊 六維系統驗證結果 (前看 {lookahead_minutes} 分鐘):")
            print(f"   總信號數: {results['total']}")
            print(f"   正確數: {results['correct']} ({results['accuracy']:.1f}%)")
            print(f"   錯誤數: {results['wrong']}")
            print(f"   正確時平均獲利: {results['avg_profit']:.3f}%")
            print(f"   錯誤時平均虧損: {results['avg_loss']:.3f}%")
            
            print(f"\n📈 按分數統計:")
            for score in sorted(results['by_score'].keys(), reverse=True):
                data = results['by_score'][score]
                if data['total'] > 0:
                    acc = data['correct'] / data['total'] * 100
                    print(f"   {score}/12 分: {data['correct']}/{data['total']} ({acc:.1f}%)")
            
            print(f"\n📈 按方向統計:")
            for direction, data in results['by_direction'].items():
                if data['total'] > 0:
                    acc = data['correct'] / data['total'] * 100
                    print(f"   {direction}: {data['correct']}/{data['total']} ({acc:.1f}%)")
        
        return results
    
    def validate_whale_strategy(self, signals: List[Dict]) -> Dict:
        """驗證主力策略偵測準確率"""
        print("\n" + "="*60)
        print("🐋 驗證主力策略偵測準確率")
        print("="*60)
        
        results = {
            'by_strategy': defaultdict(lambda: {'total': 0, 'correct': 0, 'signals': []})
        }
        
        # 篩選有主力策略的信號
        strategy_signals = [s for s in signals if s.get('market', {}).get('strategy')]
        
        if not strategy_signals:
            print("⚠️ 沒有找到主力策略信號")
            return results
        
        # 策略預期方向
        strategy_expected = {
            'ACCUMULATION': 'LONG',      # 吸籌 → 後漲
            'DISTRIBUTION': 'SHORT',      # 派發 → 後跌
            'BULL_TRAP': 'SHORT',         # 多頭陷阱 → 後跌
            'BEAR_TRAP': 'LONG',          # 空頭陷阱 → 後漲
            'STOP_HUNT_LONG': 'SHORT',    # 獵殺多頭止損 → 短期跌
            'STOP_HUNT_SHORT': 'LONG',    # 獵殺空頭止損 → 短期漲
            'FLASH_CRASH': 'LONG',        # 閃崩後反彈
            'SLOW_BLEED': 'SHORT',        # 陰跌持續
        }
        
        first_time = datetime.fromisoformat(strategy_signals[0]['timestamp'].replace('Z', ''))
        last_time = datetime.fromisoformat(strategy_signals[-1]['timestamp'].replace('Z', ''))
        
        klines = self.get_binance_klines(
            first_time - timedelta(minutes=5),
            last_time + timedelta(minutes=15)
        )
        
        if not klines:
            print("⚠️ 無法獲取幣安 K 線數據")
            return results
        
        kline_dict = {k['timestamp'].strftime('%Y-%m-%d %H:%M'): k for k in klines}
        
        for sig in strategy_signals:
            strategy = sig['market']['strategy']
            if not strategy or strategy not in strategy_expected:
                continue
            
            sig_time = datetime.fromisoformat(sig['timestamp'].replace('Z', ''))
            sig_minute = sig_time.strftime('%Y-%m-%d %H:%M')
            
            entry_kline = kline_dict.get(sig_minute)
            future_time = (sig_time + timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M')
            future_kline = kline_dict.get(future_time)
            
            if not entry_kline or not future_kline:
                continue
            
            price_change = (future_kline['close'] - entry_kline['close']) / entry_kline['close'] * 100
            expected = strategy_expected[strategy]
            
            is_correct = (expected == 'LONG' and price_change > 0) or (expected == 'SHORT' and price_change < 0)
            
            results['by_strategy'][strategy]['total'] += 1
            if is_correct:
                results['by_strategy'][strategy]['correct'] += 1
            results['by_strategy'][strategy]['signals'].append({
                'time': sig_time.isoformat(),
                'expected': expected,
                'actual_change': price_change,
                'correct': is_correct
            })
        
        print(f"\n📊 主力策略驗證結果:")
        total_correct = 0
        total_count = 0
        
        for strategy, data in sorted(results['by_strategy'].items()):
            if data['total'] > 0:
                acc = data['correct'] / data['total'] * 100
                total_correct += data['correct']
                total_count += data['total']
                emoji = "✅" if acc >= 50 else "❌"
                print(f"   {emoji} {strategy}: {data['correct']}/{data['total']} ({acc:.1f}%)")
        
        if total_count > 0:
            overall_acc = total_correct / total_count * 100
            print(f"\n   整體準確率: {total_correct}/{total_count} ({overall_acc:.1f}%)")
        
        return results
    
    def analyze_obi_distribution(self, hours: int = 24) -> Dict:
        """分析 OBI 分佈，校正門檻"""
        print("\n" + "="*60)
        print("📈 分析 OBI 分佈")
        print("="*60)
        
        signals = self.load_signals(hours)
        obi_values = [s['market']['obi'] for s in signals if s.get('market', {}).get('obi') is not None]
        
        if not obi_values:
            print("⚠️ 沒有 OBI 數據")
            return {}
        
        results = {
            'count': len(obi_values),
            'mean': np.mean(obi_values),
            'std': np.std(obi_values),
            'min': np.min(obi_values),
            'max': np.max(obi_values),
            'p10': np.percentile(obi_values, 10),
            'p25': np.percentile(obi_values, 25),
            'p50': np.percentile(obi_values, 50),
            'p75': np.percentile(obi_values, 75),
            'p90': np.percentile(obi_values, 90),
        }
        
        # 建議門檻
        results['suggested_long_threshold'] = round(results['p70'] if 'p70' in results else results['p75'] * 0.9, 3)
        results['suggested_short_threshold'] = round(results['p30'] if 'p30' in results else results['p25'] * 0.9, 3)
        
        print(f"\n📊 OBI 統計 ({results['count']} 筆):")
        print(f"   平均值: {results['mean']:.4f}")
        print(f"   標準差: {results['std']:.4f}")
        print(f"   範圍: [{results['min']:.4f}, {results['max']:.4f}]")
        print(f"\n📈 分位數:")
        print(f"   P10: {results['p10']:.4f}")
        print(f"   P25: {results['p25']:.4f}")
        print(f"   P50: {results['p50']:.4f}")
        print(f"   P75: {results['p75']:.4f}")
        print(f"   P90: {results['p90']:.4f}")
        print(f"\n💡 建議門檻:")
        print(f"   LONG: > {results['p75']:.3f} (P75)")
        print(f"   SHORT: < {results['p25']:.3f} (P25)")
        
        return results
    
    def discover_new_indicators(self) -> Dict:
        """發現新指標機會"""
        print("\n" + "="*60)
        print("🔬 發現新指標機會")
        print("="*60)
        
        discoveries = []
        
        if not self.exchange:
            print("⚠️ 需要幣安 API 連接")
            return {'discoveries': discoveries}
        
        try:
            # 1. 資金費率 (使用 Futures API)
            try:
                # 使用 Binance Futures 獲取資金費率
                futures = ccxt.binanceusdm({'timeout': 10000})
                funding = futures.fetch_funding_rate('BTC/USDT:USDT')
                funding_rate = funding.get('fundingRate', 0)
                
                if abs(funding_rate) > 0.0005:  # > 0.05%
                    direction = "SHORT" if funding_rate > 0 else "LONG"
                    discoveries.append({
                        'indicator': 'funding_rate_extreme',
                        'value': funding_rate,
                        'signal': direction,
                        'description': f"極端資金費率 ({funding_rate*100:.3f}%) → 建議 {direction}"
                    })
            except Exception as e:
                print(f"   ⚠️ 資金費率獲取失敗: {e}")
            
            # 2. 訂單簿深度不平衡
            ob_stats = self.get_binance_orderbook_stats()
            if ob_stats:
                obi = ob_stats['obi']
                if abs(obi) > 0.3:
                    direction = "LONG" if obi > 0 else "SHORT"
                    discoveries.append({
                        'indicator': 'orderbook_extreme_imbalance',
                        'value': obi,
                        'signal': direction,
                        'description': f"極端訂單簿失衡 (OBI={obi:.3f}) → 建議 {direction}"
                    })
            
            # 3. 波動率 (ATR)
            ohlcv = self.exchange.fetch_ohlcv('BTC/USDT', '1m', limit=20)
            if ohlcv:
                closes = [c[4] for c in ohlcv]
                highs = [c[2] for c in ohlcv]
                lows = [c[3] for c in ohlcv]
                
                tr_list = []
                for i in range(1, len(ohlcv)):
                    tr = max(
                        highs[i] - lows[i],
                        abs(highs[i] - closes[i-1]),
                        abs(lows[i] - closes[i-1])
                    )
                    tr_list.append(tr)
                
                atr = np.mean(tr_list[-14:])
                atr_pct = atr / closes[-1] * 100
                
                if atr_pct > 0.20:
                    discoveries.append({
                        'indicator': 'high_volatility',
                        'value': atr_pct,
                        'signal': 'CAUTION',
                        'description': f"高波動環境 (ATR={atr_pct:.3f}%) → 建議提高止損"
                    })
            
            print(f"\n🔍 發現 {len(discoveries)} 個潛在信號:")
            for d in discoveries:
                print(f"   • {d['description']}")
            
        except Exception as e:
            print(f"⚠️ 發現新指標時出錯: {e}")
        
        return {'discoveries': discoveries}
    
    def generate_calibration_report(self, hours: int = 24) -> Dict:
        """生成完整校正報告"""
        print("\n" + "="*70)
        print("📊 信號校正系統 - 完整報告")
        print(f"   分析時間範圍: 最近 {hours} 小時")
        print(f"   報告生成時間: {datetime.now().isoformat()}")
        print("="*70)
        
        signals = self.load_signals(hours)
        trades = self.load_trades(hours)
        
        report = {
            'generated_at': datetime.now().isoformat(),
            'analysis_hours': hours,
            'signal_count': len(signals),
            'trade_count': len(trades),
        }
        
        # 1. 六維驗證
        report['six_dim'] = self.validate_six_dim(signals)
        
        # 2. 主力策略驗證
        report['whale_strategy'] = self.validate_whale_strategy(signals)
        
        # 3. OBI 分佈分析
        report['obi_analysis'] = self.analyze_obi_distribution(hours)
        
        # 4. 新指標發現
        report['new_indicators'] = self.discover_new_indicators()
        
        # 5. 更新校正配置
        self._update_calibration(report)
        
        # 6. 生成建議
        report['recommendations'] = self._generate_recommendations(report)
        
        print("\n" + "="*70)
        print("💡 校正建議")
        print("="*70)
        for rec in report['recommendations']:
            print(f"   • {rec}")
        
        return report
    
    def _update_calibration(self, report: Dict):
        """更新校正配置"""
        # 更新六維結果
        if report.get('six_dim', {}).get('accuracy'):
            self.calibration['six_dim_calibration']['validation_results'] = {
                'total_signals': report['six_dim']['total'],
                'correct_direction': report['six_dim']['correct'],
                'accuracy_pct': report['six_dim']['accuracy'],
                'avg_profit_when_correct': report['six_dim'].get('avg_profit'),
                'avg_loss_when_wrong': report['six_dim'].get('avg_loss')
            }
        
        # 更新 OBI 結果
        if report.get('obi_analysis'):
            obi = report['obi_analysis']
            self.calibration['obi_calibration']['validation_results'] = {
                'binance_obi_mean': obi.get('mean'),
                'binance_obi_std': obi.get('std'),
                'binance_obi_p25': obi.get('p25'),
                'binance_obi_p75': obi.get('p75'),
            }
        
        # 更新主力策略結果
        if report.get('whale_strategy', {}).get('by_strategy'):
            for strategy, data in report['whale_strategy']['by_strategy'].items():
                if strategy in self.calibration['whale_strategy_calibration']['strategies']:
                    self.calibration['whale_strategy_calibration']['strategies'][strategy] = {
                        'accuracy': data['correct'] / data['total'] * 100 if data['total'] > 0 else None,
                        'sample_count': data['total']
                    }
        
        # 記錄校正歷史
        self.calibration['calibration_history'].append({
            'timestamp': datetime.now().isoformat(),
            'six_dim_accuracy': report.get('six_dim', {}).get('accuracy'),
            'signal_count': report.get('signal_count', 0)
        })
        
        # 保留最近 30 筆歷史
        self.calibration['calibration_history'] = self.calibration['calibration_history'][-30:]
        
        self.calibration['_meta']['last_calibration'] = datetime.now().isoformat()
        self._save_calibration()
        
        print(f"\n✅ 校正配置已更新: {self.calibration_file}")
    
    def _generate_recommendations(self, report: Dict) -> List[str]:
        """生成校正建議"""
        recommendations = []
        
        # 六維系統建議
        six_dim = report.get('six_dim', {})
        if six_dim.get('accuracy'):
            acc = six_dim['accuracy']
            if acc < 50:
                recommendations.append(f"⚠️ 六維準確率偏低 ({acc:.1f}%)，建議提高 min_score_to_trade")
            elif acc > 70:
                recommendations.append(f"✅ 六維準確率良好 ({acc:.1f}%)，可考慮降低門檻增加交易機會")
            
            # 按分數建議
            by_score = six_dim.get('by_score', {})
            best_score = max(by_score.keys(), key=lambda k: by_score[k]['correct'] / by_score[k]['total'] if by_score[k]['total'] > 0 else 0, default=None)
            if best_score:
                recommendations.append(f"💡 最佳分數區間: {best_score}/12 分")
        
        # OBI 建議
        obi = report.get('obi_analysis', {})
        if obi.get('p75') and obi.get('p25'):
            current_long = self.calibration.get('obi_calibration', {}).get('long_threshold', {}).get('current', 0.05)
            if abs(current_long - obi['p75']) > 0.02:
                recommendations.append(f"💡 OBI 門檻建議調整: LONG > {obi['p75']:.3f}, SHORT < {obi['p25']:.3f}")
        
        # 主力策略建議
        whale = report.get('whale_strategy', {}).get('by_strategy', {})
        low_acc_strategies = [s for s, d in whale.items() if d['total'] >= 3 and d['correct'] / d['total'] < 0.4]
        if low_acc_strategies:
            recommendations.append(f"⚠️ 以下主力策略準確率偏低: {', '.join(low_acc_strategies)}")
        
        # 新指標建議
        discoveries = report.get('new_indicators', {}).get('discoveries', [])
        if discoveries:
            recommendations.append(f"🔬 發現 {len(discoveries)} 個潛在新指標信號")
        
        if not recommendations:
            recommendations.append("✅ 系統表現正常，暫無特別建議")
        
        return recommendations


def main():
    """主程序"""
    import argparse
    
    parser = argparse.ArgumentParser(description='信號驗證與校正系統')
    parser.add_argument('--hours', type=int, default=24, help='分析最近 N 小時的數據')
    parser.add_argument('--validate-only', action='store_true', help='只驗證不校正')
    args = parser.parse_args()
    
    validator = SignalValidator()
    report = validator.generate_calibration_report(hours=args.hours)
    
    # 保存報告
    report_file = Path(f"logs/calibration_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    
    # 轉換為可序列化格式
    def convert_to_serializable(obj):
        if isinstance(obj, dict):
            return {k: convert_to_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_to_serializable(i) for i in obj]
        elif isinstance(obj, (np.integer, np.floating)):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, defaultdict):
            return dict(obj)
        return obj
    
    with open(report_file, 'w') as f:
        json.dump(convert_to_serializable(report), f, indent=2, ensure_ascii=False)
    
    print(f"\n📄 報告已保存: {report_file}")


if __name__ == "__main__":
    main()
