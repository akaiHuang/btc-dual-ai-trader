"""
BTC 智能交易系統 - 主程式入口
"""
import sys
import argparse
from pathlib import Path

# 添加 src 到 Python 路徑
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.core.config import get_config


def main():
    """主函數"""
    parser = argparse.ArgumentParser(description="BTC 智能交易系統")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["backtest", "paper", "live"],
        default="backtest",
        help="運行模式：backtest（回測）、paper（模擬交易）、live（實盤）"
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default="BTCHighFreq",
        help="策略名稱"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/config.json",
        help="配置文件路徑"
    )
    
    args = parser.parse_args()
    
    # 載入配置
    config = get_config()
    
    print("=" * 60)
    print("🚀 BTC 智能交易系統 v0.1.0")
    print("=" * 60)
    print(f"運行模式: {args.mode}")
    print(f"策略名稱: {args.strategy}")
    print(f"交易對: {config.trading.symbol}")
    print(f"時間框架: {config.trading.timeframe}")
    print(f"槓桿: {config.trading.leverage}x")
    print("=" * 60)
    
    if args.mode == "backtest":
        print("\n📊 回測模式")
        print("功能開發中...")
        # TODO: 實作回測邏輯
        
    elif args.mode == "paper":
        print("\n🎮 模擬交易模式")
        print("功能開發中...")
        # TODO: 實作虛擬交易邏輯
        
    elif args.mode == "live":
        print("\n⚠️  實盤交易模式")
        print("功能開發中...")
        # TODO: 實作實盤交易邏輯
    
    print("\n✅ 系統初始化完成！")


if __name__ == "__main__":
    main()
