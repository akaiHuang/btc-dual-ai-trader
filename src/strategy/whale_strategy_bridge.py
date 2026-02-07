"""
🐋 主力策略分析整合模組
========================

將 WhaleStrategyDetector 整合到現有的交易系統中

功能：
1. 從現有的 bridge 文件讀取數據
2. 計算主力策略
3. 提供給 AI Advisor 使用
4. 記錄預測並驗證準確度

Author: AI Trading System
Created: 2025-11-25
"""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Any, List
import numpy as np

from src.strategy.whale_strategy_detector import (
    WhaleStrategyDetector,
    StrategyPrediction,
    WhaleStrategy,
    quick_analyze
)


class WhaleStrategyBridge:
    """
    主力策略分析橋接器
    連接現有系統與新的主力策略檢測器
    """
    
    def __init__(
        self,
        wolf_bridge_path: str = "ai_wolf_bridge.json",
        dragon_bridge_path: str = "ai_dragon_bridge.json",
        output_path: str = "ai_whale_strategy.json"
    ):
        self.wolf_bridge_path = Path(wolf_bridge_path)
        self.dragon_bridge_path = Path(dragon_bridge_path)
        self.output_path = Path(output_path)
        
        # 初始化檢測器
        self.detector = WhaleStrategyDetector()
        
        # 歷史記錄
        self.analysis_history: List[Dict] = []
        self.max_history = 1000
        
    def _read_bridge(self, path: Path) -> Dict:
        """讀取 bridge 文件"""
        if not path.exists():
            return {}
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except:
            return {}
    
    def _extract_market_data(self) -> Dict:
        """從 bridge 文件提取市場數據"""
        wolf_data = self._read_bridge(self.wolf_bridge_path)
        dragon_data = self._read_bridge(self.dragon_bridge_path)
        
        # 優先使用 wolf bridge（數據較完整）
        data = wolf_data.get("wolf_to_ai", {})
        
        if not data:
            data = dragon_data.get("dragon_to_ai", {})
        
        return {
            "obi": data.get("market_microstructure", {}).get("obi", 0),
            "vpin": data.get("market_microstructure", {}).get("vpin", 0.5),
            "spread_bps": data.get("market_microstructure", {}).get("spread_bps", 0.1),
            "funding_rate": data.get("market_microstructure", {}).get("funding_rate", 0),
            "depth_imbalance": data.get("market_microstructure", {}).get("depth_imbalance", 0),
            "whale_net_qty": data.get("whale_status", {}).get("net_qty_btc", 0),
            "whale_dominance": data.get("whale_status", {}).get("dominance", 0.5),
            "whale_direction": data.get("whale_status", {}).get("current_direction", "NEUTRAL"),
            "entry_price": data.get("entry_price", 0) or data.get("position", {}).get("entry_price", 0),
            "current_pnl_pct": data.get("current_pnl_pct", 0),
            "volatility_atr_pct": data.get("volatility", {}).get("atr_pct", 0.1),
            "volatility_regime": data.get("volatility", {}).get("regime", "UNKNOWN"),
            "risk_indicators": data.get("risk_indicators", {}),
            "market_reaction": data.get("market_reaction", {}),
            "feedback_loop": wolf_data.get("feedback_loop", dragon_data.get("feedback_loop", {}))
        }
    
    def analyze_current_market(self) -> Optional[StrategyPrediction]:
        """分析當前市場的主力策略"""
        
        market_data = self._extract_market_data()
        
        if not market_data.get("entry_price"):
            # 嘗試從其他來源獲取價格
            market_data["entry_price"] = 87000  # 預設值
        
        # 計算價格變化（從最近交易）
        feedback = market_data.get("feedback_loop", {})
        last_trade = feedback.get("last_trade_result", {})
        price_change_pct = last_trade.get("roi", 0) / 10  # 粗略估計
        
        # 計算成交量比率（使用 VPIN 作為代理）
        vpin = market_data.get("vpin", 0.5)
        volume_ratio = 1 + (vpin - 0.3) * 2  # VPIN 高 → 成交量高
        
        # 獲取爆倉壓力
        risk = market_data.get("risk_indicators", {})
        liq_pressure = risk.get("liquidation_pressure", 50)
        
        # 執行分析
        prediction = self.detector.analyze(
            obi=market_data.get("obi", 0),
            vpin=vpin,
            current_price=market_data.get("entry_price", 87000),
            price_change_pct=price_change_pct,
            volume_ratio=volume_ratio,
            whale_net_qty=market_data.get("whale_net_qty", 0),
            funding_rate=market_data.get("funding_rate", 0),
            liquidation_pressure_long=liq_pressure if market_data.get("whale_direction") == "LONG" else 50,
            liquidation_pressure_short=liq_pressure if market_data.get("whale_direction") == "SHORT" else 50
        )
        
        # 記錄歷史
        self._record_analysis(prediction, market_data)
        
        # 保存結果
        self._save_result(prediction, market_data)
        
        return prediction
    
    def _record_analysis(self, prediction: StrategyPrediction, market_data: Dict):
        """記錄分析歷史"""
        record = {
            "timestamp": prediction.timestamp,
            "strategy": prediction.detected_strategy.value,
            "confidence": prediction.prediction_confidence,
            "predicted_action": prediction.predicted_action,
            "predicted_price": prediction.predicted_price_target,
            "actual_price_at_prediction": prediction.current_price,
            "market_data_snapshot": {
                "obi": market_data.get("obi"),
                "vpin": market_data.get("vpin"),
                "whale_net_qty": market_data.get("whale_net_qty"),
                "whale_direction": market_data.get("whale_direction")
            }
        }
        
        self.analysis_history.append(record)
        
        # 限制歷史大小
        if len(self.analysis_history) > self.max_history:
            self.analysis_history = self.analysis_history[-self.max_history:]
    
    def _save_result(self, prediction: StrategyPrediction, market_data: Dict):
        """保存分析結果到 JSON"""
        
        result = {
            "timestamp": prediction.timestamp,
            "analysis": {
                "detected_strategy": prediction.detected_strategy.value,
                "strategy_probabilities": [
                    {
                        "strategy": p.strategy.value,
                        "probability": round(p.probability, 3),
                        "confidence": round(p.confidence, 3)
                    }
                    for p in prediction.strategy_probabilities[:5]
                ],
                "conflict_state": {
                    "whale_direction": prediction.conflict_state.whale_direction,
                    "retail_direction": prediction.conflict_state.retail_direction,
                    "conflict_level": round(prediction.conflict_state.conflict_level, 2),
                    "likely_winner": prediction.conflict_state.likely_winner,
                    "reasoning": prediction.conflict_state.reasoning
                }
            },
            "prediction": {
                "action": prediction.predicted_action,
                "price_target": round(prediction.predicted_price_target, 2),
                "confidence": round(prediction.prediction_confidence, 3),
                "timeframe_minutes": prediction.expected_timeframe_minutes
            },
            "signals": prediction.key_signals,
            "warnings": prediction.risk_warnings,
            "market_context": {
                "current_price": prediction.current_price,
                "obi": market_data.get("obi"),
                "vpin": market_data.get("vpin"),
                "whale_net_qty": market_data.get("whale_net_qty")
            },
            "accuracy_stats": self.detector.get_accuracy_stats()
        }
        
        with open(self.output_path, 'w') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    
    def get_trading_recommendation(self) -> Dict:
        """
        獲取交易建議
        基於主力策略分析給出操作建議
        """
        prediction = self.analyze_current_market()
        
        if not prediction:
            return {
                "action": "HOLD",
                "reason": "無法獲取市場數據",
                "confidence": 0
            }
        
        strategy = prediction.detected_strategy
        confidence = prediction.prediction_confidence
        conflict = prediction.conflict_state
        
        # 根據策略給出建議
        recommendations = {
            WhaleStrategy.ACCUMULATION: {
                "action": "BUY" if confidence > 0.6 else "HOLD",
                "reason": "主力吸籌中，可考慮逢低買入"
            },
            WhaleStrategy.BEAR_TRAP: {
                "action": "BUY",
                "reason": "誘空陷阱，價格可能反彈"
            },
            WhaleStrategy.BULL_TRAP: {
                "action": "SELL" if confidence > 0.6 else "HOLD",
                "reason": "誘多陷阱，不要追高"
            },
            WhaleStrategy.PUMP_DUMP: {
                "action": "SELL",
                "reason": "拉高出貨，立即減倉"
            },
            WhaleStrategy.SHAKE_OUT: {
                "action": "HOLD",
                "reason": "洗盤震倉，等待方向確認"
            },
            WhaleStrategy.TESTING: {
                "action": "HOLD",
                "reason": "主力試盤，觀望為主"
            },
            WhaleStrategy.WASH_TRADING: {
                "action": "HOLD",
                "reason": "疑似對敲，避免交易"
            },
            WhaleStrategy.DUMP: {
                "action": "SELL",
                "reason": "主力砸盤，建議避險"
            },
            WhaleStrategy.NORMAL: {
                "action": "HOLD",
                "reason": "正常波動，根據其他指標決定"
            }
        }
        
        rec = recommendations.get(strategy, {"action": "HOLD", "reason": "未知策略"})
        
        # 考慮對峙狀態
        if conflict.likely_winner == "WHALE" and conflict.whale_direction == "BULLISH":
            if rec["action"] != "SELL":
                rec["action"] = "BUY"
                rec["reason"] += f" | 主力做多勝率高"
        elif conflict.likely_winner == "WHALE" and conflict.whale_direction == "BEARISH":
            if rec["action"] != "BUY":
                rec["action"] = "SELL"
                rec["reason"] += f" | 主力做空勝率高"
        
        return {
            "action": rec["action"],
            "reason": rec["reason"],
            "confidence": confidence,
            "detected_strategy": strategy.value,
            "key_signals": prediction.key_signals,
            "risk_warnings": prediction.risk_warnings,
            "predicted_price_target": prediction.predicted_price_target,
            "timeframe_minutes": prediction.expected_timeframe_minutes
        }
    
    def get_llm_prompt_context(self) -> str:
        """
        獲取給 LLM 的完整上下文
        用於增強 AI Advisor 的分析能力
        """
        prediction = self.analyze_current_market()
        
        if not prediction:
            return "## ⚠️ 無法獲取主力策略分析數據"
        
        return self.detector.to_prompt_context(prediction)


# ==================== 便捷函數 ====================

_global_bridge: Optional[WhaleStrategyBridge] = None


def get_whale_strategy_bridge() -> WhaleStrategyBridge:
    """獲取全局單例"""
    global _global_bridge
    if _global_bridge is None:
        _global_bridge = WhaleStrategyBridge()
    return _global_bridge


def analyze_whale_strategy() -> Dict:
    """快速分析主力策略"""
    bridge = get_whale_strategy_bridge()
    return bridge.get_trading_recommendation()


def get_whale_strategy_prompt() -> str:
    """獲取主力策略 LLM prompt"""
    bridge = get_whale_strategy_bridge()
    return bridge.get_llm_prompt_context()


# ==================== 測試 ====================

if __name__ == "__main__":
    print("🐋 主力策略分析橋接測試")
    print("=" * 60)
    
    # 創建橋接器
    bridge = WhaleStrategyBridge()
    
    # 執行分析
    recommendation = bridge.get_trading_recommendation()
    
    print("\n📊 交易建議:")
    print(f"  行動: {recommendation['action']}")
    print(f"  原因: {recommendation['reason']}")
    print(f"  信心度: {recommendation['confidence']:.0%}")
    print(f"  識別策略: {recommendation['detected_strategy']}")
    
    if recommendation.get('key_signals'):
        print("\n🔔 關鍵信號:")
        for s in recommendation['key_signals']:
            print(f"  {s}")
    
    if recommendation.get('risk_warnings'):
        print("\n⚠️ 風險警告:")
        for w in recommendation['risk_warnings']:
            print(f"  {w}")
    
    print("\n" + "=" * 60)
    print("📝 LLM Prompt 上下文:")
    print(bridge.get_llm_prompt_context())
