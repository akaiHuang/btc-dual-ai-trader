#!/usr/bin/env python3
"""
紙面交易訂單簿分析工具
====================

功能：
1. 讀取紙面交易訂單簿
2. 對比不同風控模式的績效
3. 驗證風控指標是否有用
4. 生成詳細分析報告

重要指標：
- 總 ROI（相對於初始資金）
- 勝率（盈利交易 / 總交易）
- 夏普比率（風險調整後收益）
- 最大回撤
- 平均持倉時間
- 風控阻擋的有效性
"""

import json
import sys
from pathlib import Path
from typing import Dict, List
from datetime import datetime, timedelta
import numpy as np
from collections import defaultdict


class PaperTradingAnalyzer:
    """紙面交易分析器"""
    
    def __init__(self, orderbook_file: str):
        self.orderbook_file = orderbook_file
        
        # 載入數據
        with open(orderbook_file, 'r', encoding='utf-8') as f:
            self.data = json.load(f)
        
        self.metadata = self.data['metadata']
        self.orders = self.data['orders']
        
        print("=" * 80)
        print("📊 紙面交易訂單簿分析")
        print("=" * 80)
        print(f"📁 檔案: {orderbook_file}")
        print(f"⏰ 時間: {self.metadata['timestamp']}")
        print(f"💰 初始資金: {self.metadata['initial_capital']} USDT")
        print(f"📝 總決策數: {self.metadata['total_decisions']}")
        print("=" * 80)
        print()
    
    def analyze_mode(self, mode: str) -> dict:
        """分析單一風控模式"""
        orders = self.orders[mode]
        
        # 基本統計
        total_orders = len(orders)
        blocked = [o for o in orders if o['status'] == 'BLOCKED']
        closed = [o for o in orders if o['status'] == 'CLOSED']
        
        stats = {
            'mode': mode,
            'total_orders': total_orders,
            'blocked_count': len(blocked),
            'blocked_rate': len(blocked) / total_orders if total_orders > 0 else 0,
            'closed_count': len(closed),
            'open_count': total_orders - len(blocked) - len(closed)
        }
        
        if len(closed) == 0:
            # 沒有平倉訂單
            stats.update({
                'win_count': 0,
                'loss_count': 0,
                'win_rate': 0,
                'total_roi': 0,
                'avg_roi': 0,
                'best_roi': 0,
                'worst_roi': 0,
                'sharpe_ratio': 0,
                'max_drawdown': 0,
                'avg_holding_seconds': 0,
                'profit_factor': 0
            })
            return stats
        
        # 盈虧統計
        rois = [o['roi'] for o in closed]
        wins = [o for o in closed if o['roi'] > 0]
        losses = [o for o in closed if o['roi'] <= 0]
        
        stats['win_count'] = len(wins)
        stats['loss_count'] = len(losses)
        stats['win_rate'] = len(wins) / len(closed) if len(closed) > 0 else 0
        
        # ROI 統計
        stats['total_roi'] = sum(rois)
        stats['avg_roi'] = np.mean(rois)
        stats['best_roi'] = max(rois)
        stats['worst_roi'] = min(rois)
        stats['std_roi'] = np.std(rois)
        
        # 夏普比率（假設無風險利率 = 0）
        if stats['std_roi'] > 0:
            stats['sharpe_ratio'] = stats['avg_roi'] / stats['std_roi']
        else:
            stats['sharpe_ratio'] = 0
        
        # 最大回撤
        cumulative_roi = np.cumsum(rois)
        running_max = np.maximum.accumulate(cumulative_roi)
        drawdown = running_max - cumulative_roi
        stats['max_drawdown'] = np.max(drawdown) if len(drawdown) > 0 else 0
        
        # 平均持倉時間
        holding_times = [o['holding_seconds'] for o in closed]
        stats['avg_holding_seconds'] = np.mean(holding_times)
        
        # 盈虧比 (Profit Factor)
        total_profit = sum(o['roi'] for o in wins)
        total_loss = abs(sum(o['roi'] for o in losses))
        stats['profit_factor'] = total_profit / total_loss if total_loss > 0 else float('inf')
        
        # 阻擋原因統計
        if len(blocked) > 0:
            blocking_reasons = defaultdict(int)
            for order in blocked:
                for reason in order['blocked_reasons']:
                    blocking_reasons[reason] += 1
            stats['blocking_reasons'] = dict(blocking_reasons)
        else:
            stats['blocking_reasons'] = {}
        
        return stats
    
    def compare_modes(self) -> dict:
        """對比所有風控模式"""
        all_stats = {}
        
        for mode in self.orders.keys():
            all_stats[mode] = self.analyze_mode(mode)
        
        return all_stats
    
    def validate_risk_control(self, all_stats: dict) -> dict:
        """驗證風控指標是否有效"""
        validation = {}
        
        # 對比 Mode 0 (無風控) vs Mode 3 (完整風控)
        mode_0 = all_stats['mode_0_no_risk']
        mode_3 = all_stats['mode_3_full_risk']
        
        # 1. ROI 改善
        roi_improvement = mode_3['total_roi'] - mode_0['total_roi']
        validation['roi_improvement'] = roi_improvement
        validation['roi_improvement_pct'] = (roi_improvement / abs(mode_0['total_roi']) * 100 
                                             if mode_0['total_roi'] != 0 else 0)
        
        # 2. 勝率改善
        win_rate_improvement = mode_3['win_rate'] - mode_0['win_rate']
        validation['win_rate_improvement'] = win_rate_improvement
        
        # 3. 夏普比率改善
        sharpe_improvement = mode_3['sharpe_ratio'] - mode_0['sharpe_ratio']
        validation['sharpe_improvement'] = sharpe_improvement
        
        # 4. 最大回撤改善
        drawdown_improvement = mode_0['max_drawdown'] - mode_3['max_drawdown']
        validation['drawdown_improvement'] = drawdown_improvement
        
        # 5. 交易機會成本
        validation['blocked_trades'] = mode_3['blocked_count']
        validation['trade_opportunity_cost'] = mode_3['blocked_rate']
        
        # 6. 總結
        validation['is_effective'] = (
            roi_improvement > 0 and 
            win_rate_improvement > 0 and 
            sharpe_improvement > 0
        )
        
        return validation
    
    def analyze_blocking_effectiveness(self, mode: str) -> dict:
        """分析風控阻擋的有效性"""
        orders = self.orders[mode]
        blocked = [o for o in orders if o['status'] == 'BLOCKED']
        
        if len(blocked) == 0:
            return {
                'total_blocked': 0,
                'effectiveness': 'N/A'
            }
        
        # 對於 Mode 0（無風控），找到對應的被阻擋訂單
        # 看它們的實際表現如何
        mode_0_orders = self.orders['mode_0_no_risk']
        
        # 找到同時間點的 Mode 0 訂單
        corresponding_losses = 0
        corresponding_wins = 0
        
        for blocked_order in blocked:
            # 找到相同時間戳的 Mode 0 訂單
            same_time_orders = [
                o for o in mode_0_orders 
                if o['timestamp'] == blocked_order['timestamp'] and 
                   o['status'] == 'CLOSED'
            ]
            
            for order in same_time_orders:
                if order['roi'] < 0:
                    corresponding_losses += 1
                else:
                    corresponding_wins += 1
        
        total_corresponding = corresponding_losses + corresponding_wins
        
        if total_corresponding > 0:
            block_accuracy = corresponding_losses / total_corresponding
        else:
            block_accuracy = 0
        
        return {
            'total_blocked': len(blocked),
            'corresponding_losses': corresponding_losses,
            'corresponding_wins': corresponding_wins,
            'block_accuracy': block_accuracy,
            'effectiveness': '有效' if block_accuracy > 0.6 else '無效'
        }
    
    def print_report(self):
        """打印完整分析報告"""
        # 分析所有模式
        all_stats = self.compare_modes()
        
        # 打印每種模式的績效
        print("📈 各風控模式績效對比")
        print("=" * 80)
        print()
        
        mode_names = {
            'mode_0_no_risk': '❌ Mode 0: 無風控',
            'mode_1_vpin_only': '🟡 Mode 1: 僅 VPIN',
            'mode_2_liquidity_only': '🔵 Mode 2: 僅流動性',
            'mode_3_full_risk': '🟢 Mode 3: 完整風控'
        }
        
        for mode, stats in all_stats.items():
            print(f"{mode_names[mode]}")
            print(f"  總訂單: {stats['total_orders']}")
            print(f"  已阻擋: {stats['blocked_count']} ({stats['blocked_rate']*100:.1f}%)")
            print(f"  已平倉: {stats['closed_count']}")
            
            if stats['closed_count'] > 0:
                print(f"  勝率: {stats['win_rate']*100:.1f}% ({stats['win_count']}勝/{stats['loss_count']}敗)")
                print(f"  總 ROI: {stats['total_roi']:+.2f}%")
                print(f"  平均 ROI: {stats['avg_roi']:+.2f}%")
                print(f"  最佳/最差: {stats['best_roi']:+.2f}% / {stats['worst_roi']:+.2f}%")
                print(f"  夏普比率: {stats['sharpe_ratio']:.2f}")
                print(f"  最大回撤: {stats['max_drawdown']:.2f}%")
                print(f"  平均持倉: {stats['avg_holding_seconds']:.0f} 秒 ({stats['avg_holding_seconds']/60:.1f} 分鐘)")
                print(f"  盈虧比: {stats['profit_factor']:.2f}")
            
            if stats['blocking_reasons']:
                print(f"  阻擋原因:")
                for reason, count in stats['blocking_reasons'].items():
                    print(f"    • {reason}: {count} 次")
            
            print()
        
        # 驗證風控有效性
        print("🔍 風控有效性驗證")
        print("=" * 80)
        
        validation = self.validate_risk_control(all_stats)
        
        print(f"ROI 改善: {validation['roi_improvement']:+.2f}% "
              f"({validation['roi_improvement_pct']:+.1f}%)")
        print(f"勝率改善: {validation['win_rate_improvement']*100:+.1f}%")
        print(f"夏普比率改善: {validation['sharpe_improvement']:+.2f}")
        print(f"最大回撤改善: {validation['drawdown_improvement']:+.2f}%")
        print(f"交易機會成本: {validation['trade_opportunity_cost']*100:.1f}% "
              f"({validation['blocked_trades']} 筆被阻擋)")
        print()
        
        # 總結
        if validation['is_effective']:
            print("✅ 結論: 風控指標**有效**，建議使用完整風控模式進行真實交易")
        else:
            print("❌ 結論: 風控指標效果不明顯，需要調整閾值或策略")
        print()
        
        # 分析阻擋有效性
        print("🎯 風控阻擋有效性分析")
        print("=" * 80)
        
        for mode in ['mode_1_vpin_only', 'mode_2_liquidity_only', 'mode_3_full_risk']:
            blocking = self.analyze_blocking_effectiveness(mode)
            if blocking['total_blocked'] > 0:
                print(f"{mode_names[mode]}")
                print(f"  總阻擋: {blocking['total_blocked']} 筆")
                print(f"  對應虧損: {blocking['corresponding_losses']} 筆")
                print(f"  對應獲利: {blocking['corresponding_wins']} 筆")
                print(f"  阻擋準確率: {blocking['block_accuracy']*100:.1f}%")
                print(f"  有效性: {blocking['effectiveness']}")
                print()
    
    def generate_json_report(self) -> str:
        """生成 JSON 格式報告"""
        all_stats = self.compare_modes()
        validation = self.validate_risk_control(all_stats)
        
        report = {
            'metadata': self.metadata,
            'statistics': all_stats,
            'validation': validation,
            'blocking_effectiveness': {
                mode: self.analyze_blocking_effectiveness(mode)
                for mode in ['mode_1_vpin_only', 'mode_2_liquidity_only', 'mode_3_full_risk']
            }
        }
        
        # 保存
        output_file = self.orderbook_file.replace('.json', '_analysis.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"💾 分析報告已保存: {output_file}")
        return output_file


def main():
    """主函數"""
    if len(sys.argv) < 2:
        # 找最新的訂單簿
        data_dir = Path("data/paper_trading")
        orderbooks = sorted(data_dir.glob("paper_trading_*.json"), reverse=True)
        
        if not orderbooks:
            print("❌ 找不到訂單簿檔案")
            print("💡 請先運行: python scripts/paper_trading_system.py")
            sys.exit(1)
        
        orderbook_file = str(orderbooks[0])
        print(f"📁 使用最新訂單簿: {orderbook_file}")
        print()
    else:
        orderbook_file = sys.argv[1]
    
    # 分析
    analyzer = PaperTradingAnalyzer(orderbook_file)
    analyzer.print_report()
    analyzer.generate_json_report()


if __name__ == "__main__":
    main()
