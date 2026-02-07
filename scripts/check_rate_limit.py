#!/usr/bin/env python3
"""
dYdX 速率限制狀態查看器
========================

顯示當前速率限制狀態和活躍進程

Usage:
    python scripts/check_rate_limit.py
    
    # 持續監控
    python scripts/check_rate_limit.py --watch
"""

import argparse
import json
import sys
import time
from pathlib import Path

# 添加 src 路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.shared_rate_limiter import SharedRateLimiter


def main():
    parser = argparse.ArgumentParser(description="dYdX 速率限制狀態查看器")
    parser.add_argument("--watch", "-w", action="store_true", help="持續監控模式")
    parser.add_argument("--interval", "-i", type=float, default=1.0, help="監控間隔 (秒)")
    parser.add_argument("--clear", "-c", action="store_true", help="清除所有狀態")
    args = parser.parse_args()
    
    limiter = SharedRateLimiter()
    
    if args.clear:
        # 清除狀態文件
        if limiter.STATE_FILE.exists():
            limiter.STATE_FILE.unlink()
            print("✅ 已清除速率限制狀態")
        return
    
    def show_stats():
        stats = limiter.get_stats()
        
        # 使用率條
        usage_pct = stats['usage_percent']
        bar_len = 30
        filled = int(bar_len * usage_pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        
        # 顏色
        if usage_pct > 80:
            color = "\033[91m"  # 紅色
        elif usage_pct > 50:
            color = "\033[93m"  # 黃色
        else:
            color = "\033[92m"  # 綠色
        reset = "\033[0m"
        
        print(f"\r{color}[{bar}]{reset} {usage_pct:.1f}% | "
              f"請求: {stats['current_requests']}/{stats['max_requests']} | "
              f"進程: {stats['active_processes']} | "
              f"剩餘: {stats['remaining_quota']}   ", end="")
        
        return stats
    
    if args.watch:
        print("📊 dYdX API 速率限制監控 (Ctrl+C 退出)")
        print("-" * 70)
        
        try:
            while True:
                show_stats()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n✅ 監控結束")
    else:
        stats = limiter.get_stats()
        
        print("╔══════════════════════════════════════════════════════════════════╗")
        print("║           dYdX API 速率限制狀態                                  ║")
        print("╚══════════════════════════════════════════════════════════════════╝")
        print()
        print(f"📊 配額使用:")
        print(f"   當前請求數: {stats['current_requests']} / {stats['max_requests']}")
        print(f"   使用率: {stats['usage_percent']:.1f}%")
        print(f"   剩餘配額: {stats['remaining_quota']}")
        print(f"   時間窗口: {stats['window_seconds']} 秒")
        print()
        print(f"🖥️ 活躍進程: {stats['active_processes']}")
        if stats['process_ids']:
            for pid in stats['process_ids']:
                print(f"   - {pid}")
        else:
            print("   (無)")
        print()
        
        if stats['usage_percent'] > 80:
            print("⚠️ 警告: 速率限制使用率過高！")
        elif stats['usage_percent'] > 50:
            print("ℹ️ 提示: 速率限制使用率中等")
        else:
            print("✅ 狀態正常")


if __name__ == "__main__":
    main()
