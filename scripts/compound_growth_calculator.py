#!/usr/bin/env python3
"""
複利增長計算器
============

計算從 10U 到 100U/200U/400U 需要多久
以及 v2.1 策略如何改進以達到目標

作者: Strategy Planning
日期: 2025-11-15
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta


def calculate_compound_growth(
    initial_capital: float,
    target_capital: float,
    monthly_return_pct: float,
    max_months: int = 24
) -> dict:
    """
    計算複利增長路徑
    
    Returns:
        {
            'months_needed': int,
            'final_capital': float,
            'monthly_path': list,
            'total_return_pct': float
        }
    """
    capital = initial_capital
    monthly_path = [capital]
    
    for month in range(1, max_months + 1):
        capital *= (1 + monthly_return_pct / 100)
        monthly_path.append(capital)
        
        if capital >= target_capital:
            return {
                'months_needed': month,
                'final_capital': capital,
                'monthly_path': monthly_path[:month+1],
                'total_return_pct': (capital - initial_capital) / initial_capital * 100,
                'achievable': True
            }
    
    return {
        'months_needed': max_months,
        'final_capital': capital,
        'monthly_path': monthly_path,
        'total_return_pct': (capital - initial_capital) / initial_capital * 100,
        'achievable': False
    }


def calculate_required_monthly_return(
    initial_capital: float,
    target_capital: float,
    months: int
) -> float:
    """計算需要的月回報率"""
    multiplier = target_capital / initial_capital
    monthly_return = (multiplier ** (1 / months) - 1) * 100
    return monthly_return


def analyze_v21_performance():
    """分析 v2.1 當前表現"""
    print('='*80)
    print('📊 v2.1 策略當前表現分析')
    print('='*80)
    print()
    
    # v2.1 5 年數據
    v21_data = {
        '年均交易數': 81.8,
        '平均勝率': 47.9,
        '5年總淨利': 1.60,
        '年均淨利': 0.32,
        '時間止損率': 65.8,
    }
    
    print('當前性能:')
    for key, value in v21_data.items():
        if '%' in key or '率' in key:
            print(f'  {key}: {value:.1f}%')
        else:
            print(f'  {key}: {value:.2f}')
    print()
    
    # 計算月回報率
    monthly_return = (1.60 / 10 / 5 * 12) * 100  # 轉換為月回報
    print(f'當前月回報率: {monthly_return:.2f}%')
    print()
    
    return monthly_return


def print_compound_paths():
    """打印不同月回報率的複利路徑"""
    print('='*80)
    print('💰 複利增長路徑分析：10U → 100U/200U/400U')
    print('='*80)
    print()
    
    initial = 10
    targets = [100, 200, 400]
    monthly_returns = [5, 10, 15, 20, 25]
    
    # 表格標題
    print(f'{"月回報率":<10} {"達到100U":<12} {"達到200U":<12} {"達到400U":<12} {"24個月資金":<15}')
    print('-'*80)
    
    for monthly_return in monthly_returns:
        results = []
        final_24m = initial * (1 + monthly_return/100) ** 24
        
        for target in targets:
            result = calculate_compound_growth(initial, target, monthly_return, max_months=24)
            if result['achievable']:
                results.append(f"{result['months_needed']} 個月")
            else:
                results.append("超過24個月")
        
        print(f'{monthly_return:>3}%       {results[0]:<12} {results[1]:<12} {results[2]:<12} ${final_24m:>8.2f}')
    
    print()


def analyze_improvement_targets():
    """分析需要改進的目標"""
    print('='*80)
    print('🎯 v2.1 改進目標（達到 15-20% 月回報）')
    print('='*80)
    print()
    
    # 當前表現
    current = {
        '勝率': 47.9,
        '年交易數': 81.8,
        '時間止損率': 65.8,
        '年淨利': 0.32,
    }
    
    # 目標 1: 提高勝率
    print('📈 目標 1: 提高勝率 (47.9% → 55%)')
    print('   改進方法:')
    print('   1. 多時間框架確認 (5m + 15m + 1h)')
    print('   2. 趨勢強度過濾 (ADX > 25)')
    print('   3. 放寬盤整閾值 (增加樣本量)')
    print('   預期效果: 勝率 +7%, 年淨利 +$3-5')
    print()
    
    # 目標 2: 減少時間止損
    print('📉 目標 2: 減少時間止損 (65.8% → 50%)')
    print('   改進方法:')
    print('   1. 多時間框架對齊 (避免逆勢)')
    print('   2. 動態時間止損 (根據 ATR)')
    print('   3. 成交量確認 (大量配合)')
    print('   預期效果: 時間止損 -15%, TP 達成率 +10%')
    print()
    
    # 目標 3: 動態倉位
    print('💼 目標 3: 動態倉位管理')
    print('   改進方法:')
    print('   1. Kelly Criterion (勝率 55% → 建議 10% 倉位)')
    print('   2. 信心度調整 (高信心 15%, 標準 10%, 低信心 5%)')
    print('   3. 連勝加倉 (3連勝 → 倉位 +20%)')
    print('   預期效果: 年回報 +50-100%')
    print()
    
    # 計算改進後的表現
    print('🚀 改進後預期表現:')
    print('-'*80)
    print(f'{"指標":<20} {"當前":<15} {"目標":<15} {"改進":<15}')
    print('-'*80)
    print(f'{"勝率":<20} {current["勝率"]:.1f}%          55.0%          +{55-current["勝率"]:.1f}%')
    print(f'{"時間止損率":<20} {current["時間止損率"]:.1f}%          50.0%          -{current["時間止損率"]-50:.1f}%')
    print(f'{"年交易數":<20} {current["年交易數"]:.1f}           100-120        +{100-current["年交易數"]:.1f}')
    
    # 估算改進後年淨利
    improved_trades = 110  # 年交易數
    improved_win_rate = 0.55
    improved_time_stop = 0.50
    avg_win = 1.0  # $1 平均盈利
    avg_loss = 0.8  # $0.8 平均虧損
    
    winning_trades = improved_trades * improved_win_rate
    losing_trades = improved_trades * (1 - improved_win_rate)
    annual_pnl = (winning_trades * avg_win) - (losing_trades * avg_loss)
    
    print(f'{"年淨利":<20} ${current["年淨利"]:.2f}          ${annual_pnl:.2f}          +${annual_pnl-current["年淨利"]:.2f}')
    print(f'{"年回報率":<20} {current["年淨利"]/10*100:.1f}%          {annual_pnl/10*100:.1f}%          +{(annual_pnl-current["年淨利"])/10*100:.1f}%')
    
    # 轉換為月回報
    monthly_return = (annual_pnl / 10 / 12) * 100
    print(f'{"月回報率":<20} {current["年淨利"]/10/12*100:.2f}%          {monthly_return:.2f}%          +{monthly_return-current["年淨利"]/10/12*100:.2f}%')
    print()


def print_realistic_timeline():
    """打印現實的時間線"""
    print('='*80)
    print('📅 現實可行的成長時間線')
    print('='*80)
    print()
    
    # 階段 1: 驗證階段 (1-2個月)
    print('階段 1: 策略驗證 (1-2 個月)')
    print('  資金: 10U')
    print('  目標: 驗證改進有效，月回報 8-10%')
    print('  期末資金: 10U → 11-12U')
    print()
    
    # 階段 2: 穩定增長 (3-8個月)
    print('階段 2: 穩定增長 (3-8 個月)')
    print('  資金: 12U')
    print('  目標: 月回報 12-15%')
    print('  期末資金: 12U → 25-30U')
    print()
    
    # 階段 3: 加速增長 (9-12個月)
    print('階段 3: 加速增長 (9-12 個月)')
    print('  資金: 30U')
    print('  目標: 月回報 15-18% (動態倉位)')
    print('  期末資金: 30U → 55-70U')
    print()
    
    # 階段 4: 達標 (13-18個月)
    print('階段 4: 達標衝刺 (13-18 個月)')
    print('  資金: 70U')
    print('  目標: 月回報 15-20%')
    print('  期末資金: 70U → 100-150U ✅')
    print()
    
    print('總時間: 12-18 個月達到 100U (10x)')
    print('        18-24 個月達到 200U (20x)')
    print('        24-30 個月達到 400U (40x)')
    print()


def main():
    """主函數"""
    # 1. 分析當前表現
    current_monthly_return = analyze_v21_performance()
    
    # 2. 打印複利路徑
    print_compound_paths()
    
    # 3. 分析改進目標
    analyze_improvement_targets()
    
    # 4. 現實時間線
    print_realistic_timeline()
    
    # 5. 關鍵建議
    print('='*80)
    print('💡 關鍵建議')
    print('='*80)
    print()
    print('✅ 可行路徑:')
    print('  1. 優化 v2.1 → 月回報 10-15%')
    print('  2. 複利增長 → 12-18 個月達到 100U')
    print('  3. 動態倉位 → 加速後期增長')
    print()
    print('❌ 不可行路徑:')
    print('  1. 高頻交易 → 無法覆蓋手續費')
    print('  2. 100% 日獲利 → 數學上不可能')
    print('  3. 過度激進 → 大幅回撤風險')
    print()
    print('🎯 下一步行動:')
    print('  1. 實現多時間框架確認 (預期勝率 +5-7%)')
    print('  2. 動態倉位管理系統 (預期回報 +50%)')
    print('  3. 紙上交易 2 周驗證改進')
    print('  4. 小額實盤 ($100) 開始複利')
    print()


if __name__ == '__main__':
    main()
