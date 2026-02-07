"""
策略診斷工具 - 分析為什麼沒有交易執行

Purpose:
    分析 Phase C 策略為何產生信號但不執行交易：
    1. 信號信心度分佈
    2. 風險等級分佈
    3. VPIN 過高原因
    4. 各指標貢獻度
"""

import re
from collections import defaultdict, Counter
from typing import List, Dict
import pandas as pd


class StrategyDiagnostic:
    """策略診斷器"""
    
    def __init__(self, log_file: str):
        """
        初始化診斷器
        
        Args:
            log_file: 日誌文件路徑（如 test.txt）
        """
        self.log_file = log_file
        self.decisions = []
        
    def parse_log(self):
        """解析日誌文件"""
        print(f"📖 解析日誌: {self.log_file}")
        
        with open(self.log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        current_decision = None
        
        for i, line in enumerate(lines):
            # 檢測決策開始
            decision_match = re.search(r'\[(\d{2}:\d{2}:\d{2})\] 決策 #(\d+)', line)
            if decision_match:
                if current_decision:
                    self.decisions.append(current_decision)
                
                current_decision = {
                    'time': decision_match.group(1),
                    'number': int(decision_match.group(2))
                }
                continue
            
            if current_decision is None:
                continue
            
            # 提取價格
            price_match = re.search(r'價格: \$([0-9,]+\.\d+)', line)
            if price_match:
                current_decision['price'] = float(price_match.group(1).replace(',', ''))
            
            # 提取信號
            signal_match = re.search(r'信號: [^(]+\(信心度: (0\.\d+)\)', line)
            if signal_match:
                current_decision['confidence'] = float(signal_match.group(1))
                
                if '📈 LONG' in line:
                    current_decision['signal'] = 'LONG'
                elif '📉 SHORT' in line:
                    current_decision['signal'] = 'SHORT'
                else:
                    current_decision['signal'] = 'NEUTRAL'
            
            # 提取風險等級
            if '風險:' in line:
                if '🟢 SAFE' in line:
                    current_decision['risk'] = 'SAFE'
                elif '🟡 WARNING' in line:
                    current_decision['risk'] = 'WARNING'
                elif '🟠 DANGER' in line:
                    current_decision['risk'] = 'DANGER'
                elif '🔴 CRITICAL' in line:
                    current_decision['risk'] = 'CRITICAL'
            
            # 提取指標
            indicator_match = re.search(r'OBI: ([+-]?\d+\.\d+) \| Velocity:\s+([+-]?\d+\.\d+)', line)
            if indicator_match:
                current_decision['obi'] = float(indicator_match.group(1))
                current_decision['obi_velocity'] = float(indicator_match.group(2))
            
            volume_match = re.search(r'Volume:\s+([+-]?\d+\.\d+) \| VPIN: (0\.\d+)', line)
            if volume_match:
                current_decision['signed_volume'] = float(volume_match.group(1))
                current_decision['vpin'] = float(volume_match.group(2))
            
            spread_match = re.search(r'Spread:\s+([\d.]+)bps \| Depth: ([\d.]+) BTC', line)
            if spread_match:
                current_decision['spread'] = float(spread_match.group(1))
                current_decision['depth'] = float(spread_match.group(2))
        
        # 添加最後一個決策
        if current_decision:
            self.decisions.append(current_decision)
        
        print(f"✅ 解析完成: {len(self.decisions)} 個決策\n")
    
    def analyze(self):
        """執行完整分析"""
        if not self.decisions:
            self.parse_log()
        
        print("="*70)
        print("📊 策略診斷報告")
        print("="*70)
        print()
        
        # 1. 基本統計
        self._analyze_basic_stats()
        
        # 2. 信號分析
        self._analyze_signals()
        
        # 3. 風險分析
        self._analyze_risk()
        
        # 4. VPIN 分析（關鍵！）
        self._analyze_vpin()
        
        # 5. 指標相關性
        self._analyze_indicators()
        
        # 6. 為何沒有交易？
        self._analyze_why_no_trades()
    
    def _analyze_basic_stats(self):
        """基本統計"""
        print("📈 基本統計")
        print("-"*70)
        print(f"總決策數: {len(self.decisions)}")
        
        if self.decisions:
            df = pd.DataFrame(self.decisions)
            print(f"時間範圍: {self.decisions[0]['time']} - {self.decisions[-1]['time']}")
            print(f"價格範圍: ${df['price'].min():.2f} - ${df['price'].max():.2f}")
            print(f"價格波動: {((df['price'].max() - df['price'].min()) / df['price'].mean() * 100):.2f}%")
        print()
    
    def _analyze_signals(self):
        """信號分析"""
        print("🎯 信號生成分析")
        print("-"*70)
        
        df = pd.DataFrame(self.decisions)
        
        # 信號分佈
        signal_counts = df['signal'].value_counts()
        print("信號類型分佈:")
        for signal, count in signal_counts.items():
            pct = count / len(df) * 100
            print(f"  {signal:10s}: {count:4d} ({pct:5.1f}%)")
        
        # 信心度統計
        print(f"\n信心度統計:")
        print(f"  平均值: {df['confidence'].mean():.3f}")
        print(f"  中位數: {df['confidence'].median():.3f}")
        print(f"  最小值: {df['confidence'].min():.3f}")
        print(f"  最大值: {df['confidence'].max():.3f}")
        
        # 信心度分佈
        confidence_ranges = [
            (0.0, 0.2, "極低"),
            (0.2, 0.4, "低"),
            (0.4, 0.6, "中等"),
            (0.6, 0.8, "高"),
            (0.8, 1.0, "極高")
        ]
        
        print(f"\n信心度分佈:")
        for low, high, label in confidence_ranges:
            count = ((df['confidence'] >= low) & (df['confidence'] < high)).sum()
            pct = count / len(df) * 100
            print(f"  {label:6s} ({low:.1f}-{high:.1f}): {count:4d} ({pct:5.1f}%)")
        
        # 超過閾值的信號
        moderate_threshold = 0.6
        aggressive_threshold = 0.8
        
        moderate_signals = df[df['confidence'] >= moderate_threshold]
        aggressive_signals = df[df['confidence'] >= aggressive_threshold]
        
        print(f"\n達到交易閾值的信號:")
        print(f"  中等閾值 (>= {moderate_threshold}): {len(moderate_signals)} ({len(moderate_signals)/len(df)*100:.1f}%)")
        if len(moderate_signals) > 0:
            print(f"    - LONG:    {(moderate_signals['signal'] == 'LONG').sum()}")
            print(f"    - SHORT:   {(moderate_signals['signal'] == 'SHORT').sum()}")
            print(f"    - NEUTRAL: {(moderate_signals['signal'] == 'NEUTRAL').sum()}")
        
        print(f"  激進閾值 (>= {aggressive_threshold}): {len(aggressive_signals)} ({len(aggressive_signals)/len(df)*100:.1f}%)")
        print()
    
    def _analyze_risk(self):
        """風險分析"""
        print("⚠️  風險等級分析")
        print("-"*70)
        
        df = pd.DataFrame(self.decisions)
        
        risk_counts = df['risk'].value_counts()
        print("風險等級分佈:")
        for risk, count in risk_counts.items():
            pct = count / len(df) * 100
            emoji = {'SAFE': '🟢', 'WARNING': '🟡', 'DANGER': '🟠', 'CRITICAL': '🔴'}.get(risk, '❓')
            print(f"  {emoji} {risk:10s}: {count:4d} ({pct:5.1f}%)")
        
        # 有信號但被阻擋
        df_with_signal = df[df['signal'] != 'NEUTRAL']
        df_blocked = df_with_signal[df_with_signal['risk'].isin(['DANGER', 'CRITICAL'])]
        
        print(f"\n有方向性信號（LONG/SHORT）: {len(df_with_signal)}")
        print(f"被風險阻擋的信號: {len(df_blocked)} ({len(df_blocked)/len(df_with_signal)*100:.1f}%)" if len(df_with_signal) > 0 else "N/A")
        print()
    
    def _analyze_vpin(self):
        """VPIN 分析 - 這是關鍵！"""
        print("🔥 VPIN 分析（關鍵指標）")
        print("-"*70)
        
        df = pd.DataFrame(self.decisions)
        
        print(f"VPIN 統計:")
        print(f"  平均值: {df['vpin'].mean():.3f}")
        print(f"  中位數: {df['vpin'].median():.3f}")
        print(f"  最小值: {df['vpin'].min():.3f}")
        print(f"  最大值: {df['vpin'].max():.3f}")
        
        # VPIN 分佈
        vpin_ranges = [
            (0.0, 0.3, "低毒性", "🟢"),
            (0.3, 0.5, "中等", "🟡"),
            (0.5, 0.7, "高毒性", "🟠"),
            (0.7, 1.0, "極高", "🔴")
        ]
        
        print(f"\nVPIN 分佈:")
        for low, high, label, emoji in vpin_ranges:
            count = ((df['vpin'] >= low) & (df['vpin'] < high)).sum()
            pct = count / len(df) * 100
            print(f"  {emoji} {label:6s} ({low:.1f}-{high:.1f}): {count:4d} ({pct:5.1f}%)")
        
        # 關鍵發現
        high_vpin_count = (df['vpin'] > 0.5).sum()
        very_high_vpin_count = (df['vpin'] > 0.7).sum()
        
        print(f"\n⚠️  關鍵發現:")
        print(f"  VPIN > 0.5 (觸發阻擋): {high_vpin_count} ({high_vpin_count/len(df)*100:.1f}%)")
        print(f"  VPIN > 0.7 (極度危險): {very_high_vpin_count} ({very_high_vpin_count/len(df)*100:.1f}%)")
        
        if high_vpin_count / len(df) > 0.8:
            print(f"\n  💡 診斷結論: VPIN 持續過高！")
            print(f"     - 超過 80% 的決策時 VPIN > 0.5")
            print(f"     - 這導致幾乎所有信號都被風險過濾器阻擋")
            print(f"     - 可能原因：")
            print(f"       1. VPIN 計算參數過於敏感（bucket_size 太小？）")
            print(f"       2. 真實市場確實有高比例 toxic flow")
            print(f"       3. 閾值設定過於保守（0.5 → 0.7？）")
        print()
    
    def _analyze_indicators(self):
        """指標分析"""
        print("📊 微觀結構指標統計")
        print("-"*70)
        
        df = pd.DataFrame(self.decisions)
        
        indicators = ['obi', 'obi_velocity', 'signed_volume', 'spread', 'depth']
        
        for ind in indicators:
            if ind in df.columns:
                print(f"{ind:15s}: 平均 {df[ind].mean():+7.3f} | 中位數 {df[ind].median():+7.3f} | 範圍 [{df[ind].min():+7.3f}, {df[ind].max():+7.3f}]")
        print()
    
    def _analyze_why_no_trades(self):
        """為何沒有交易？"""
        print("❓ 為何沒有交易執行？")
        print("="*70)
        
        df = pd.DataFrame(self.decisions)
        
        # 條件 1: 需要有方向性信號
        df_with_signal = df[df['signal'] != 'NEUTRAL']
        print(f"✓ 有方向性信號 (LONG/SHORT): {len(df_with_signal)} / {len(df)}")
        
        if len(df_with_signal) == 0:
            print(f"  ❌ 問題: 沒有任何 LONG/SHORT 信號產生")
            print(f"     - 所有決策都是 NEUTRAL")
            print(f"     - 原因: 信心度全部低於 moderate_threshold (0.6)")
            return
        
        # 條件 2: 信心度需要 >= 0.6
        df_high_conf = df_with_signal[df_with_signal['confidence'] >= 0.6]
        print(f"✓ 信心度 >= 0.6: {len(df_high_conf)} / {len(df_with_signal)}")
        
        if len(df_high_conf) == 0:
            print(f"  ❌ 問題: 雖然有信號，但信心度都不夠")
            print(f"     - 最高信心度: {df_with_signal['confidence'].max():.3f}")
            print(f"     - 建議: 降低閾值到 0.5？")
            return
        
        # 條件 3: 風險等級需要是 SAFE 或 WARNING
        df_safe = df_high_conf[df_high_conf['risk'].isin(['SAFE', 'WARNING'])]
        print(f"✓ 風險等級允許交易: {len(df_safe)} / {len(df_high_conf)}")
        
        if len(df_safe) == 0:
            print(f"  ❌ 問題: 所有高信心度信號都被風險過濾器阻擋！")
            print(f"     - {len(df_high_conf)} 個信號全部是 DANGER 或 CRITICAL 風險")
            print(f"     - 主要原因: VPIN 過高（見上方 VPIN 分析）")
            print(f"     - 建議:")
            print(f"       1. 調高 VPIN 閾值: 0.5 → 0.7")
            print(f"       2. 檢查 VPIN 計算是否正確")
            print(f"       3. 考慮放寬風險等級限制（允許 DANGER？）")
            return
        
        print(f"\n✅ 理論上應該有 {len(df_safe)} 筆交易！")
        print(f"   但實際執行數: 0")
        print(f"   可能原因: 還有其他阻擋條件（檢查 ExecutionEngine）")
        print()


def main():
    """主函數"""
    import sys
    
    log_file = "tests/test.txt"
    if len(sys.argv) > 1:
        log_file = sys.argv[1]
    
    print("="*70)
    print("🔍 Phase C 策略診斷工具")
    print("="*70)
    print()
    
    diagnostic = StrategyDiagnostic(log_file)
    diagnostic.analyze()
    
    print("="*70)
    print("💡 下一步建議")
    print("="*70)
    print()
    print("1. 調整 VPIN 閾值:")
    print("   src/strategy/regime_filter.py")
    print("   vpin_threshold: 0.5 → 0.7")
    print()
    print("2. 降低信號信心度閾值:")
    print("   src/strategy/execution_engine.py")
    print("   moderate_threshold: 0.6 → 0.5")
    print()
    print("3. 運行真實數據測試:")
    print("   python scripts/real_trading_simulation.py")
    print()
    print("4. 收集真實歷史數據:")
    print("   python scripts/collect_historical_snapshots.py")
    print()


if __name__ == "__main__":
    main()
