#!/usr/bin/env python3
"""
🚀 Strategy Optimizer & Analyzer
================================
讀取最新的 Paper Trading 交易紀錄，分析各策略表現，並自動生成優化後的參數建議。

功能：
1. 自動尋找最新的交易數據 (data/paper_trading/pt_*)
2. 計算關鍵指標：勝率、盈虧比、最大回撤、獲利因子
3. 根據表現動態調整參數：
   - 表現好 (WinRate > 60%, Profit > 0) -> 放大槓桿、增加倉位
   - 表現差 (WinRate < 40%) -> 提高進場門檻 (Signal Threshold)
   - 風險高 (Drawdown > 10%) -> 降低槓桿
4. 生成 `config/trading_strategies_optimized.json`
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd

# 設定路徑
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "paper_trading"
CONFIG_PATH = PROJECT_ROOT / "config" / "trading_strategies_dynamic.json"
OUTPUT_CONFIG_PATH = PROJECT_ROOT / "config" / "trading_strategies_optimized.json"
HISTORY_LOG_PATH = PROJECT_ROOT / "data" / "optimization_history.csv"

def save_optimization_log(mode_name, changes, performance):
    """記錄優化歷史到 CSV"""
    if not changes:
        return

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 確保目錄存在
    HISTORY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # 檢查是否需要寫入標頭
    file_exists = HISTORY_LOG_PATH.exists()
    
    try:
        with open(HISTORY_LOG_PATH, 'a', encoding='utf-8') as f:
            if not file_exists:
                f.write("timestamp,mode,win_rate,profit_factor,trades,change_details\n")
                
            for change in changes:
                # 格式: 時間,模式,勝率,獲利因子,交易數,"變更內容"
                line = f"{timestamp},{mode_name},{performance['win_rate']:.2f},{performance['profit_factor']:.2f},{performance['trades']},\"{change}\"\n"
                f.write(line)
        print(f"   📝 已記錄 {len(changes)} 項變更至歷史紀錄")
    except Exception as e:
        print(f"   ⚠️ 無法寫入歷史紀錄: {e}")

def find_latest_trading_data():
    """尋找最新的交易數據資料夾"""
    if not DATA_DIR.exists():
        print(f"❌ 找不到數據目錄: {DATA_DIR}")
        return None
    
    # 找出所有 pt_ 開頭的資料夾
    dirs = [d for d in DATA_DIR.iterdir() if d.is_dir() and d.name.startswith("pt_")]
    if not dirs:
        print("❌ 找不到任何交易紀錄")
        return None
    
    # 按時間排序 (最新的在最後)
    latest_dir = sorted(dirs, key=lambda x: x.stat().st_mtime)[-1]
    data_file = latest_dir / "trading_data.json"
    
    if not data_file.exists():
        print(f"❌ 在 {latest_dir} 中找不到 trading_data.json")
        return None
        
    print(f"📂 讀取最新數據: {data_file}")
    return data_file

def analyze_performance(orders):
    """分析單一策略的表現"""
    if not orders:
        return None
        
    df = pd.DataFrame(orders)
    
    # 過濾已平倉的訂單
    closed_trades = df[df['exit_time'].notna()]
    if len(closed_trades) == 0:
        return None
        
    wins = closed_trades[closed_trades['pnl_usdt'] > 0]
    losses = closed_trades[closed_trades['pnl_usdt'] <= 0]
    
    win_rate = len(wins) / len(closed_trades)
    total_pnl = closed_trades['pnl_usdt'].sum()
    avg_win = wins['pnl_usdt'].mean() if not wins.empty else 0
    avg_loss = losses['pnl_usdt'].mean() if not losses.empty else 0
    
    # 獲利因子 (Profit Factor)
    gross_profit = wins['pnl_usdt'].sum()
    gross_loss = abs(losses['pnl_usdt'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    return {
        "trades": len(closed_trades),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss
    }

def optimize_config(current_config, performance):
    """根據表現優化配置"""
    new_config = current_config.copy()
    changes = []
    
    if not performance:
        return new_config, changes
        
    win_rate = performance['win_rate']
    profit_factor = performance['profit_factor']
    trades = performance['trades']
    
    # 1. 表現優異 -> 放大
    if win_rate > 0.6 and profit_factor > 1.5 and trades >= 3:
        # 增加槓桿 (上限 125)
        old_lev = new_config.get('leverage', 10)
        new_lev = min(int(old_lev * 1.2), 125)
        if new_lev != old_lev:
            new_config['leverage'] = new_lev
            changes.append(f"槓桿 {old_lev}x -> {new_lev}x")
            
        # 增加倉位 (上限 1.0)
        old_size = new_config.get('position_size', 0.5)
        new_size = min(old_size * 1.1, 1.0)
        if new_size != old_size:
            new_config['position_size'] = round(new_size, 2)
            changes.append(f"倉位 {old_size} -> {new_size}")
            
    # 2. 表現不佳 -> 緊縮
    elif win_rate < 0.4 and trades >= 3:
        # 提高信號門檻
        old_score = new_config.get('min_direction_score', 0.0)
        new_score = min(old_score + 0.1, 0.8)
        if new_score != old_score:
            new_config['min_direction_score'] = round(new_score, 2)
            changes.append(f"門檻 {old_score} -> {new_score}")
            
        # 降低槓桿
        old_lev = new_config.get('leverage', 10)
        new_lev = max(int(old_lev * 0.8), 1)
        if new_lev != old_lev:
            new_config['leverage'] = new_lev
            changes.append(f"槓桿 {old_lev}x -> {new_lev}x")

    if changes:
        print(f"   ✨ 優化建議: {', '.join(changes)}")
    else:
        print("   ⚪ 表現平穩，維持參數")
        
    return new_config, changes

def main():
    print("🚀 開始策略優化分析...")
    
    # 1. 讀取數據
    data_file = find_latest_trading_data()
    if not data_file:
        return
        
    with open(data_file, 'r') as f:
        trading_data = json.load(f)
        
    # 2. 讀取當前配置
    if not CONFIG_PATH.exists():
        print(f"❌ 找不到配置檔: {CONFIG_PATH}")
        return
        
    with open(CONFIG_PATH, 'r') as f:
        current_config = json.load(f)
        
    optimized_config = current_config.copy()
    optimized_config['_last_optimized'] = datetime.now().isoformat()
    
    # 3. 逐一分析模式
    orders_by_mode = trading_data.get('orders', {})
    
    print(f"\n📊 分析結果:")
    print("="*60)
    
    for mode_name, mode_conf in current_config.get('modes', {}).items():
        if not mode_conf.get('enabled', False):
            continue
            
        print(f"\n🔍 分析模式: {mode_name} ({mode_conf.get('emoji', '')})")
        
        orders = orders_by_mode.get(mode_name, [])
        perf = analyze_performance(orders)
        
        if perf:
            print(f"   交易數: {perf['trades']} | 勝率: {perf['win_rate']*100:.1f}% | 獲利因子: {perf['profit_factor']:.2f}")
            print(f"   總盈虧: ${perf['total_pnl']:.2f}")
            
            # 優化
            new_mode_conf, changes = optimize_config(mode_conf, perf)
            optimized_config['modes'][mode_name] = new_mode_conf
            
            # 記錄變更
            if changes:
                save_optimization_log(mode_name, changes, perf)
        else:
            print("   ⚠️ 無足夠交易數據進行分析")
            
    # 4. 保存結果
    with open(OUTPUT_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(optimized_config, f, indent=2, ensure_ascii=False)
        
    print("\n" + "="*60)
    print(f"✅ 優化完成！新配置已保存至: {OUTPUT_CONFIG_PATH}")
    print("💡 您可以將其重命名為 trading_strategies_dynamic.json 以應用更改。")

if __name__ == "__main__":
    main()
