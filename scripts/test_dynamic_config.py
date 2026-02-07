"""
動態策略配置系統 - 測試腳本
驗證 ModeConfigManager 和 RuleEngine 的基本功能
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.strategy.mode_config_manager import ModeConfigManager
from src.strategy.rule_engine import RuleEngine
import time


def test_config_manager():
    """測試配置管理器"""
    print("\n" + "="*80)
    print("測試 1: ModeConfigManager 基本功能")
    print("="*80)
    
    # 初始化
    manager = ModeConfigManager("config/trading_strategies_dynamic.json")
    
    # 獲取狀態
    status = manager.get_status()
    print(f"\n📊 Config Manager 狀態:")
    print(f"   配置檔: {status['config_path']}")
    print(f"   載入時間: {status['last_load_time']}")
    print(f"   總模式數: {status['total_modes']}")
    print(f"   啟用模式數: {status['enabled_modes']}")
    print(f"   載入錯誤: {status['load_error']}")
    
    # 獲取所有啟用的模式
    enabled_modes = manager.get_all_enabled_modes()
    print(f"\n✅ 已啟用的模式:")
    for mode_name, config in enabled_modes.items():
        print(f"   • {config.get('emoji', '🤖')} {mode_name}: {config.get('description', 'N/A')}")
        print(f"     槓桿: {config['leverage']}x, TP: {config['tp_pct']}%, SL: {config['sl_pct']}%")
    
    # 測試獲取單個配置
    print(f"\n📝 測試獲取單個模式配置:")
    m0_config = manager.get_config("M0_ULTRA_SAFE")
    if m0_config:
        print(f"   ✅ M0_ULTRA_SAFE 配置存在")
        print(f"      類型: {m0_config['type']}")
        print(f"      槓桿: {m0_config['leverage']}x")
        print(f"      TP/SL: {m0_config['tp_pct']}% / {m0_config['sl_pct']}%")
    
    # 測試熱更新
    print(f"\n🔄 測試熱更新檢查:")
    print(f"   第一次檢查...")
    result1 = manager.reload_if_updated()
    print(f"   結果: {'✅ 成功' if result1 else '❌ 失敗'}")
    
    print(f"   等待 1 秒後再次檢查...")
    time.sleep(1)
    result2 = manager.reload_if_updated()
    print(f"   結果: {'✅ 無變更（正常）' if result2 else '❌ 失敗'}")
    
    print(f"\n   💡 提示: 嘗試修改 config/trading_strategies_dynamic.json 然後重新運行此測試")
    print(f"            你會看到配置自動重新載入！")


def test_rule_engine():
    """測試規則引擎"""
    print("\n" + "="*80)
    print("測試 2: RuleEngine 決策功能")
    print("="*80)
    
    # 初始化
    manager = ModeConfigManager("config/trading_strategies_dynamic.json")
    engine = RuleEngine()
    
    # 模擬市場快照
    mock_snapshot = {
        'regime': 'BULL',
        'trend_state': 'STRONG_UP',
        'rsi': 55.0,
        'obi': 0.25,
        'funding_zscore': 1.2,
        'large_trade_direction': 'LONG',
        'volume': 1500,
        'volume_ma': 1000,
        'whale_concentration': 0.75,
        'vpin': 0.4,
        'spread_bps': 2.5,
        'timestamp': time.time()
    }
    
    print(f"\n📊 模擬市場快照:")
    print(f"   Regime: {mock_snapshot['regime']}")
    print(f"   Trend: {mock_snapshot['trend_state']}")
    print(f"   RSI: {mock_snapshot['rsi']}")
    print(f"   OBI: {mock_snapshot['obi']}")
    print(f"   巨鯨方向: {mock_snapshot['large_trade_direction']}")
    print(f"   巨鯨集中度: {mock_snapshot['whale_concentration']}")
    
    # 測試幾個模式的決策
    test_modes = ["M0_ULTRA_SAFE", "M1_SAFE_PRIME", "M2_NORMAL_PRIME"]
    
    print(f"\n🎯 測試決策結果:")
    for mode_name in test_modes:
        config = manager.get_config(mode_name)
        if config:
            decision = engine.evaluate_entry(mode_name, config, mock_snapshot)
            
            action_icon = "🟢" if decision['action'] == 'LONG' else "🔴" if decision['action'] == 'SHORT' else "⚪"
            print(f"\n   {action_icon} {config.get('emoji', '🤖')} {mode_name}:")
            print(f"      動作: {decision['action']}")
            print(f"      原因: {decision['reason']}")
            print(f"      信心: {decision['confidence']:.2f}")
            print(f"      TP/SL: {decision['tp_pct']:.2f}% / {decision['sl_pct']:.2f}%")
    
    # 獲取引擎統計
    stats = engine.get_stats()
    print(f"\n📈 引擎統計:")
    print(f"   總評估次數: {stats['total_evaluations']}")


def test_hot_reload_demo():
    """演示熱更新功能"""
    print("\n" + "="*80)
    print("測試 3: 熱更新演示（互動式）")
    print("="*80)
    
    manager = ModeConfigManager("config/trading_strategies_dynamic.json")
    engine = RuleEngine()
    
    print(f"\n🔥 熱更新演示")
    print(f"   配置檔: {manager.config_path}")
    print(f"\n   📝 步驟:")
    print(f"   1. 此程式會每 3 秒檢查一次配置檔變化")
    print(f"   2. 請在另一個編輯器中打開:")
    print(f"      config/trading_strategies_dynamic.json")
    print(f"   3. 嘗試修改某個模式的參數（例如改 TP/SL）")
    print(f"   4. 儲存檔案")
    print(f"   5. 觀察此程式自動偵測並重新載入！")
    print(f"\n   按 Ctrl+C 結束演示\n")
    
    try:
        check_count = 0
        while check_count < 20:  # 最多跑 1 分鐘
            check_count += 1
            print(f"[{check_count:02d}] 檢查配置更新...", end="")
            
            if manager.reload_if_updated():
                status = manager.get_status()
                print(f" ✅ 已載入 {status['enabled_modes']} 個模式")
                
                # 顯示當前 M0 的配置
                m0_config = manager.get_config("M0_ULTRA_SAFE")
                if m0_config:
                    print(f"      M0 當前配置: TP={m0_config['tp_pct']}%, SL={m0_config['sl_pct']}%")
            else:
                print(f" ❌ 載入失敗")
            
            time.sleep(3)
        
        print(f"\n✅ 演示結束")
        
    except KeyboardInterrupt:
        print(f"\n\n✅ 使用者中斷，演示結束")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("🧪 動態策略配置系統 - 功能測試")
    print("="*80)
    
    # 執行所有測試
    test_config_manager()
    test_rule_engine()
    
    # 詢問是否執行熱更新演示
    print("\n" + "="*80)
    response = input("\n❓ 是否執行熱更新演示？(y/n): ")
    if response.lower() == 'y':
        test_hot_reload_demo()
    
    print("\n" + "="*80)
    print("✅ 所有測試完成！")
    print("="*80 + "\n")
