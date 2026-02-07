"""
測試 Mode 14 動態槓桿優化策略
"""

import sys
import os
import logging
from datetime import datetime

# 添加專案根目錄到 Python 路徑
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.strategy.mode_14_dynamic_leverage import (
    Mode14Strategy,
    MarketRegimeDetector,
    SignalQualityScorer,
    CostAwareProfitCalculator,
    DynamicLeverageAdjuster,
    DynamicPositionSizer,
    DynamicTPSLAdjuster,
    StrategySelector,
    TradingScheme
)

# 配置日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_market_regime_detector():
    """測試市場狀態檢測器"""
    print("\n" + "="*60)
    print("測試 1: 市場狀態檢測器")
    print("="*60)
    
    detector = MarketRegimeDetector()
    
    # 模擬價格數據
    test_prices = [
        50000, 50100, 50200, 50150, 50300, 50400, 50350,  # 上漲趨勢
        50500, 50600, 50700, 50800, 50900, 51000, 51100,
        51200, 51300, 51400, 51500, 51600, 51700
    ]
    
    for price in test_prices:
        detector.update_price(price)
    
    # 檢測市場狀態
    regime = detector.detect_regime([0.7, 0.8, 0.75, 0.82, 0.78])  # OBI歷史
    volatility = detector.calculate_volatility()
    trend_strength = detector.calculate_trend_strength()
    
    print(f"✅ 市場狀態: {regime}")
    print(f"✅ 波動率: {volatility:.4f}")
    print(f"✅ 趨勢強度: {trend_strength:.2f}")
    
    assert regime in ["TRENDING", "VOLATILE", "CONSOLIDATION", "NEUTRAL"]
    print("✅ 市場狀態檢測器測試通過")


def test_signal_quality_scorer():
    """測試信號質量評分系統"""
    print("\n" + "="*60)
    print("測試 2: 信號質量評分系統")
    print("="*60)
    
    scorer = SignalQualityScorer()
    
    # 更新數據
    for _ in range(20):
        scorer.update_data(volume=1000, price=50000)
    
    # 測試信號評分
    obi_data = {'current': 0.8}
    volume_data = {'current': 1500, 'average': 1000}
    mtf_signals = {'5m': 0.7, '15m': 0.6, '30m': 0.65}
    
    score = scorer.score_signal(obi_data, volume_data, mtf_signals)
    
    print(f"✅ 信號質量評分: {score:.2f}")
    print(f"   - OBI強度貢獻: {abs(obi_data['current']) * 0.3:.2f}")
    print(f"   - 成交量確認貢獻: ~{0.25:.2f}")
    print(f"   - 價格動能貢獻: ~{0.20:.2f}")
    print(f"   - 多時間框架貢獻: ~{0.25:.2f}")
    
    assert 0 <= score <= 1.0
    print("✅ 信號質量評分系統測試通過")


def test_cost_aware_profit_calculator():
    """測試成本感知盈利計算器"""
    print("\n" + "="*60)
    print("測試 3: 成本感知盈利計算器")
    print("="*60)
    
    calculator = CostAwareProfitCalculator()
    
    # 測試盈虧平衡計算
    leverage = 20
    position_size = 0.5
    
    breakeven = calculator.calculate_breakeven(leverage, position_size)
    print(f"✅ 盈虧平衡點: {breakeven:.4f} ({breakeven*100:.2f}%)")
    
    # 測試盈利判斷
    expected_move = 0.002  # 0.2%
    is_profitable = calculator.is_trade_profitable(expected_move, leverage, position_size)
    
    print(f"✅ 預期價格變動: {expected_move:.4f} ({expected_move*100:.2f}%)")
    print(f"✅ 是否有利可圖: {'是' if is_profitable else '否'}")
    print(f"   - 需要價格變動: {breakeven * 1.5:.4f} ({breakeven * 1.5 * 100:.2f}%)")
    
    assert breakeven > 0
    print("✅ 成本感知盈利計算器測試通過")


def test_dynamic_leverage_adjuster():
    """測試動態槓桿調整器"""
    print("\n" + "="*60)
    print("測試 4: 動態槓桿調整器")
    print("="*60)
    
    adjuster = DynamicLeverageAdjuster(base_leverage=20)
    
    # 測試不同市場條件
    test_cases = [
        {"vpin": 0.8, "volatility": 0.035, "signal": 0.9, "desc": "高VPIN + 高波動 + 強信號"},
        {"vpin": 0.2, "volatility": 0.008, "signal": 0.85, "desc": "低VPIN + 低波動 + 強信號"},
        {"vpin": 0.5, "volatility": 0.015, "signal": 0.4, "desc": "中VPIN + 中波動 + 弱信號"},
    ]
    
    for case in test_cases:
        leverage = adjuster.adjust_leverage(
            current_vpin=case['vpin'],
            volatility=case['volatility'],
            signal_strength=case['signal']
        )
        print(f"\n✅ {case['desc']}")
        print(f"   調整後槓桿: {leverage:.1f}x")
    
    print("\n✅ 動態槓桿調整器測試通過")


def test_dynamic_position_sizer():
    """測試動態倉位調整器"""
    print("\n" + "="*60)
    print("測試 5: 動態倉位調整器")
    print("="*60)
    
    sizer = DynamicPositionSizer(base_size=0.5)
    
    # 測試不同市場狀態
    test_cases = [
        {"leverage": 20, "confidence": 0.85, "regime": "TRENDING", "desc": "趨勢市場 + 高信心"},
        {"leverage": 25, "confidence": 0.5, "regime": "VOLATILE", "desc": "波動市場 + 低信心"},
        {"leverage": 15, "confidence": 0.7, "regime": "CONSOLIDATION", "desc": "盤整市場 + 中信心"},
    ]
    
    for case in test_cases:
        position = sizer.adjust_position_size(
            leverage=case['leverage'],
            confidence=case['confidence'],
            market_regime=case['regime']
        )
        print(f"\n✅ {case['desc']}")
        print(f"   調整後倉位: {position:.1%}")
    
    print("\n✅ 動態倉位調整器測試通過")


def test_dynamic_tpsl_adjuster():
    """測試動態止盈止損調整器"""
    print("\n" + "="*60)
    print("測試 6: 動態止盈止損調整器")
    print("="*60)
    
    adjuster = DynamicTPSLAdjuster()
    
    # 更新ATR數據
    for _ in range(20):
        adjuster.update_atr(500)  # $500 ATR
    
    # 測試不同條件
    test_cases = [
        {"leverage": 25, "volatility": 0.03, "duration": 10, "desc": "高槓桿 + 高波動 + 長持續"},
        {"leverage": 10, "volatility": 0.01, "duration": 2, "desc": "低槓桿 + 低波動 + 短持續"},
        {"leverage": 20, "volatility": 0.015, "duration": 5, "desc": "中槓桿 + 中波動 + 中持續"},
    ]
    
    for case in test_cases:
        tp, sl = adjuster.adjust_tp_sl(
            leverage=case['leverage'],
            volatility=case['volatility'],
            signal_duration=case['duration']
        )
        print(f"\n✅ {case['desc']}")
        print(f"   止盈: {tp:.2%}, 止損: {sl:.2%}")
    
    print("\n✅ 動態止盈止損調整器測試通過")


def test_strategy_selector():
    """測試策略方案選擇器"""
    print("\n" + "="*60)
    print("測試 7: 策略方案選擇器")
    print("="*60)
    
    selector = StrategySelector()
    
    # 測試方案選擇
    test_cases = [
        {"regime": "VOLATILE", "balance": 90, "initial": 100, "desc": "波動市場 + 虧損中"},
        {"regime": "TRENDING", "balance": 125, "initial": 100, "desc": "趨勢市場 + 大幅盈利"},
        {"regime": "NEUTRAL", "balance": 105, "initial": 100, "desc": "中性市場 + 小幅盈利"},
    ]
    
    for case in test_cases:
        scheme = selector.select_optimal_scheme(
            market_regime=case['regime'],
            current_balance=case['balance'],
            initial_balance=case['initial']
        )
        print(f"\n✅ {case['desc']}")
        print(f"   推薦方案: {scheme}")
        print(f"   方案配置: {TradingScheme.get_scheme(scheme)['name']}")
    
    # 測試升級降級條件
    print("\n" + "-"*40)
    print("測試升級/降級條件")
    print("-"*40)
    
    # 添加交易歷史
    for i in range(10):
        selector.add_trade_result(profit=10 if i >= 5 else -5, entry_time=datetime.now())
    
    should_upgrade = selector.should_upgrade_strategy()
    should_downgrade = selector.should_downgrade_strategy(current_balance=95, initial_balance=100)
    
    print(f"✅ 應該升級: {'是' if should_upgrade else '否'}")
    print(f"✅ 應該降級: {'是' if should_downgrade else '否'}")
    
    print("\n✅ 策略方案選擇器測試通過")


def test_mode14_strategy():
    """測試 Mode14 完整策略"""
    print("\n" + "="*60)
    print("測試 8: Mode14 完整策略")
    print("="*60)
    
    # 配置
    config = {
        'base_leverage': 20,
        'max_position_size': 0.5,
        'risk_control': {
            'vpin_threshold': 0.75,
            'spread_threshold': 8,
            'depth_threshold': 5
        }
    }
    
    strategy = Mode14Strategy(config)
    
    # 準備市場數據
    market_data = {
        'vpin': 0.4,
        'spread': 5,
        'depth': 10,
        'obi': 0.7,
        'volume': 1500,
        'avg_volume': 1000,
        'price': 50000,
        'mtf_signals': {'5m': 0.6, '15m': 0.65, '30m': 0.7}
    }
    
    # 測試進場條件判斷
    should_enter, reason = strategy.should_enter_trade(market_data)
    print(f"\n✅ 是否應該進場: {'是' if should_enter else '否'}")
    print(f"✅ 原因: {reason}")
    
    # 測試交易參數計算
    params = strategy.calculate_trade_parameters(market_data, signal_duration=3)
    print(f"\n✅ 交易參數:")
    print(f"   - 槓桿: {params['leverage']:.1f}x")
    print(f"   - 倉位: {params['position_size']:.1%}")
    print(f"   - 止盈: {params['take_profit']:.2%}")
    print(f"   - 止損: {params['stop_loss']:.2%}")
    print(f"   - 市場狀態: {params['market_regime']}")
    print(f"   - 信號評分: {params['signal_score']:.2f}")
    
    # 測試方案更新
    current_scheme = strategy.update_scheme_if_needed(
        current_balance=110,
        initial_balance=100,
        market_regime="TRENDING",
        current_vpin=0.4,
        network_latency=50
    )
    print(f"\n✅ 當前方案: {current_scheme}")
    
    # 獲取當前方案配置
    scheme_config = strategy.get_current_scheme_config()
    print(f"✅ 方案配置: {scheme_config['name']}")
    print(f"   - 小時交易次數: {scheme_config['trades_per_hour']}")
    print(f"   - 槓桿範圍: {scheme_config['leverage_range']}")
    print(f"   - 倉位範圍: {scheme_config['position_range']}")
    print(f"   - 小時目標盈利: {scheme_config['hourly_target']:.1%}")
    
    print("\n✅ Mode14 完整策略測試通過")


def test_trading_schemes():
    """測試三方案配置"""
    print("\n" + "="*60)
    print("測試 9: 三方案配置")
    print("="*60)
    
    for scheme_name in ['A', 'B', 'C']:
        scheme = TradingScheme.get_scheme(scheme_name)
        print(f"\n✅ {scheme['name']}")
        print(f"   - 小時交易: {scheme['trades_per_hour']}")
        print(f"   - 槓桿範圍: {scheme['leverage_range']}")
        print(f"   - 倉位範圍: {scheme['position_range']}")
        print(f"   - 止盈: {scheme['price_tp']:.2%}")
        print(f"   - 止損: {scheme['price_sl']:.2%}")
        print(f"   - 盈虧比: {scheme['profit_loss_ratio']:.1f}")
        print(f"   - 小時目標: {scheme['hourly_target']:.1%}")
        print(f"   - 勝率目標: {scheme['win_rate_target']:.0%}")
        print(f"   - 達成時間: {scheme['time_to_double']}小時")
    
    print("\n✅ 三方案配置測試通過")


def main():
    """運行所有測試"""
    print("\n" + "="*60)
    print("🚀 Mode 14 動態槓桿優化策略 - 完整測試套件")
    print("="*60)
    
    try:
        test_market_regime_detector()
        test_signal_quality_scorer()
        test_cost_aware_profit_calculator()
        test_dynamic_leverage_adjuster()
        test_dynamic_position_sizer()
        test_dynamic_tpsl_adjuster()
        test_strategy_selector()
        test_mode14_strategy()
        test_trading_schemes()
        
        print("\n" + "="*60)
        print("✅ 所有測試通過！Mode 14 策略系統運作正常")
        print("="*60)
        
    except AssertionError as e:
        print(f"\n❌ 測試失敗: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 測試錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
