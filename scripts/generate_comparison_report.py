"""
測試結果對比報告生成器

Purpose:
    對比三個測試的結果：
    1. 快速回測（模擬數據）
    2. Phase C 真實交易模擬
    3. HFT 策略對比
    
Output:
    - 交易次數對比
    - 收益對比
    - 分析結論
"""

import re
import json
from datetime import datetime
from typing import Dict, List
import pandas as pd


class TestComparisonReport:
    """測試對比報告生成器"""
    
    def __init__(self):
        self.backtest_results = {}
        self.phase_c_results = {}
        self.hft_results = {}
    
    def parse_backtest_log(self, log_file: str):
        """解析快速回測日誌"""
        print(f"📖 解析快速回測日誌: {log_file}")
        
        with open(log_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 提取統計數據
        decisions_match = re.search(r'總決策數:\s+(\d+)', content)
        signals_match = re.search(r'交易信號:\s+(\d+)', content)
        executed_match = re.search(r'實際執行:\s+(\d+)', content)
        
        self.backtest_results = {
            'name': '快速回測（模擬數據）',
            'total_decisions': int(decisions_match.group(1)) if decisions_match else 0,
            'signals_generated': int(signals_match.group(1)) if signals_match else 0,
            'trades_executed': int(executed_match.group(1)) if executed_match else 0,
            'source': '模擬訂單簿 + 交易',
            'data_quality': '低（生成自 K線）'
        }
        
        print(f"   決策: {self.backtest_results['total_decisions']}")
        print(f"   信號: {self.backtest_results['signals_generated']}")
        print(f"   交易: {self.backtest_results['trades_executed']}")
        print()
    
    def parse_phase_c_log(self, log_file: str):
        """解析 Phase C 真實交易日誌"""
        print(f"📖 解析 Phase C 交易日誌: {log_file}")
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except FileNotFoundError:
            print(f"   ⚠️  文件不存在")
            return
        
        decisions = 0
        signals = 0
        trades = 0
        
        for line in lines:
            if '決策 #' in line:
                decisions += 1
            if 'LONG' in line or 'SHORT' in line:
                if 'NEUTRAL' not in line:
                    signals += 1
            if '開倉' in line or '平倉' in line:
                trades += 1
        
        self.phase_c_results = {
            'name': 'Phase C 真實交易模擬',
            'total_decisions': decisions,
            'signals_generated': signals,
            'trades_executed': trades,
            'source': '真實 Binance WebSocket',
            'data_quality': '高（100% 真實數據）'
        }
        
        print(f"   決策: {self.phase_c_results['total_decisions']}")
        print(f"   信號: {self.phase_c_results['signals_generated']}")
        print(f"   交易: {self.phase_c_results['trades_executed']}")
        print()
    
    def parse_hft_log(self, log_file: str):
        """解析 HFT 對比日誌"""
        print(f"📖 解析 HFT 對比日誌: {log_file}")
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except FileNotFoundError:
            print(f"   ⚠️  文件不存在")
            return
        
        trades_match = re.search(r'總交易數:\s+(\d+)', content)
        freq_match = re.search(r'平均頻率:\s+([\d.]+)', content)
        
        self.hft_results = {
            'name': 'HFT 簡單策略',
            'trades_executed': int(trades_match.group(1)) if trades_match else 0,
            'avg_frequency': float(freq_match.group(1)) if freq_match else 0,
            'source': '真實 Binance WebSocket',
            'data_quality': '高（100% 真實數據）'
        }
        
        print(f"   交易: {self.hft_results['trades_executed']}")
        print(f"   頻率: {self.hft_results.get('avg_frequency', 0):.1f} 筆/小時")
        print()
    
    def generate_report(self):
        """生成對比報告"""
        print("="*70)
        print("📊 測試結果對比報告")
        print("="*70)
        print()
        
        # 表格對比
        print("📈 交易次數對比")
        print("-"*70)
        
        if self.backtest_results:
            print(f"\n1. {self.backtest_results['name']}")
            print(f"   數據源: {self.backtest_results['source']}")
            print(f"   決策數: {self.backtest_results['total_decisions']}")
            print(f"   信號數: {self.backtest_results['signals_generated']}")
            print(f"   交易數: {self.backtest_results['trades_executed']}")
            print(f"   轉換率: {self.backtest_results['trades_executed'] / max(self.backtest_results['signals_generated'], 1) * 100:.1f}%")
        
        if self.phase_c_results:
            print(f"\n2. {self.phase_c_results['name']}")
            print(f"   數據源: {self.phase_c_results['source']}")
            print(f"   決策數: {self.phase_c_results['total_decisions']}")
            print(f"   信號數: {self.phase_c_results['signals_generated']}")
            print(f"   交易數: {self.phase_c_results['trades_executed']}")
            if self.phase_c_results['signals_generated'] > 0:
                print(f"   轉換率: {self.phase_c_results['trades_executed'] / self.phase_c_results['signals_generated'] * 100:.1f}%")
        
        if self.hft_results:
            print(f"\n3. {self.hft_results['name']}")
            print(f"   數據源: {self.hft_results['source']}")
            print(f"   交易數: {self.hft_results['trades_executed']}")
            print(f"   平均頻率: {self.hft_results.get('avg_frequency', 0):.1f} 筆/小時")
        
        print()
        print("="*70)
        print("💡 分析結論")
        print("="*70)
        print()
        
        # 分析 1: 數據源影響
        print("1. 數據源對結果的影響:")
        if self.backtest_results and self.phase_c_results:
            backtest_trades = self.backtest_results['trades_executed']
            phase_c_trades = self.phase_c_results['trades_executed']
            
            print(f"   模擬數據回測: {backtest_trades} 筆交易")
            print(f"   真實數據測試: {phase_c_trades} 筆交易")
            
            if backtest_trades == 0 and phase_c_trades == 0:
                print(f"   ❌ 兩者都沒有交易 → 策略過於保守")
            elif backtest_trades == 0 and phase_c_trades > 0:
                print(f"   ✅ 真實數據有交易 → 模擬數據不準確")
            elif backtest_trades > 0 and phase_c_trades == 0:
                print(f"   ⚠️  模擬有交易但真實沒有 → 模擬過於樂觀")
            else:
                diff_pct = abs(backtest_trades - phase_c_trades) / max(backtest_trades, phase_c_trades) * 100
                print(f"   差異: {diff_pct:.1f}%")
        print()
        
        # 分析 2: Phase C vs HFT
        print("2. Phase C 策略 vs 高頻策略:")
        if self.phase_c_results and self.hft_results:
            phase_c_trades = self.phase_c_results['trades_executed']
            hft_trades = self.hft_results['trades_executed']
            
            print(f"   Phase C: {phase_c_trades} 筆")
            print(f"   HFT:     {hft_trades} 筆")
            
            if phase_c_trades == 0 and hft_trades > 0:
                print(f"   💡 Phase C 太保守，市場確實有交易機會")
            elif phase_c_trades > 0 and hft_trades > phase_c_trades * 10:
                print(f"   💡 HFT 頻率遠高於 Phase C（{hft_trades/max(phase_c_trades,1):.1f}x）")
                print(f"      但要考慮手續費成本")
        print()
        
        # 分析 3: VPIN 問題
        print("3. VPIN 持續過高問題:")
        print(f"   根據診斷報告: 93.8% 的時間 VPIN > 0.7")
        print(f"   這導致幾乎所有信號被阻擋")
        print(f"   ")
        print(f"   建議:")
        print(f"   ✓ 調高 VPIN 閾值: 0.5 → 0.7")
        print(f"   ✓ 或重新檢查 VPIN 計算邏輯")
        print(f"   ✓ 或考慮 VPIN 不適合 BTC 現貨市場")
        print()


def main():
    """主函數"""
    import sys
    
    report = TestComparisonReport()
    
    # 解析快速回測結果
    print("="*70)
    print("🔍 收集測試結果")
    print("="*70)
    print()
    
    # 1. 快速回測（已完成）
    backtest_log = "tests/test.txt"  # 或從快速回測輸出
    # report.parse_backtest_log(backtest_log)
    
    # 使用診斷工具的結果
    report.backtest_results = {
        'name': '快速回測（模擬數據）',
        'total_decisions': 5740,
        'signals_generated': 0,
        'trades_executed': 0,
        'source': '模擬訂單簿 + 交易',
        'data_quality': '低（生成自 K線）'
    }
    
    # 2. Phase C 真實交易（從昨天日誌）
    report.phase_c_results = {
        'name': 'Phase C 真實交易模擬',
        'total_decisions': 2193,
        'signals_generated': 6,  # 3 LONG + 3 SHORT
        'trades_executed': 0,    # 全部被 VPIN 阻擋
        'source': '真實 Binance WebSocket',
        'data_quality': '高（100% 真實數據）'
    }
    
    # 3. HFT 對比（待運行）
    report.hft_results = {
        'name': 'HFT 簡單策略',
        'trades_executed': 0,  # 待運行
        'avg_frequency': 0,
        'source': '真實 Binance WebSocket',
        'data_quality': '高（100% 真實數據）'
    }
    
    # 生成報告
    report.generate_report()


if __name__ == "__main__":
    main()
