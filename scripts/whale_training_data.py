#!/usr/bin/env python3
"""
Whale Training Data Collector
==============================
收集 Whale Trading System 的訓練資料

用途：
1. 記錄每筆交易的進場市場快照
2. 記錄交易結果 (勝/敗, 盈虧, 持倉時間)
3. 整合成 TensorFlow/sklearn 可用的格式

資料結構：
- features: 進場時的 23 種策略機率 + 市場指標
- labels: 是否成功 (binary)
- metadata: 策略名稱, 方向, 時間戳等

Author: AI Assistant
Date: 2025-11-28
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, field
import pandas as pd
import numpy as np


# ============================================================
# 資料結構
# ============================================================

@dataclass
class TradeFeatures:
    """
    交易進場時的特徵快照
    這些特徵將用於訓練 ML 模型
    """
    # 基本資訊
    trade_id: str
    timestamp: str
    
    # 策略資訊
    strategy: str                    # 觸發的策略名稱
    direction: str                   # LONG or SHORT
    predicted_probability: float     # 策略預測機率
    predicted_confidence: float      # 預測信心度
    
    # ===== 核心指標 =====
    current_price: float
    obi: float                       # Order Book Imbalance
    wpi: float                       # Whale Pressure Index
    vpin: float                      # Volume-synchronized PIN
    funding_rate: float              # 資金費率
    oi_change_pct: float             # OI 變化 %
    
    # ===== 爆倉壓力 =====
    liquidation_pressure_long: float
    liquidation_pressure_short: float
    
    # ===== 價格變動 =====
    price_change_1m_pct: float = 0.0
    price_change_5m_pct: float = 0.0
    price_change_15m_pct: float = 0.0
    price_change_1h_pct: float = 0.0
    
    # ===== 波動率 =====
    volatility_5m: float = 0.0
    volatility_1h: float = 0.0
    atr_14: float = 0.0
    
    # ===== 成交量 =====
    volume_ratio_5m: float = 1.0     # 當前量 / 5分鐘平均量
    volume_ratio_1h: float = 1.0
    large_trade_count_5m: int = 0    # 大單數量
    
    # ===== 策略機率分布 (23 種) =====
    prob_bull_trap: float = 0.0
    prob_bear_trap: float = 0.0
    prob_fakeout: float = 0.0
    prob_stop_hunt: float = 0.0
    prob_spoofing: float = 0.0
    prob_whipsaw: float = 0.0
    prob_consolidation_shake: float = 0.0
    prob_flash_crash: float = 0.0
    prob_slow_bleed: float = 0.0
    prob_accumulation: float = 0.0
    prob_distribution: float = 0.0
    prob_re_accumulation: float = 0.0
    prob_re_distribution: float = 0.0
    prob_long_squeeze: float = 0.0
    prob_short_squeeze: float = 0.0
    prob_cascade_liquidation: float = 0.0
    prob_trend_push: float = 0.0
    prob_trend_continuation: float = 0.0
    prob_trend_reversal: float = 0.0
    prob_pump_and_dump: float = 0.0
    prob_wash_trading: float = 0.0
    prob_layering: float = 0.0
    
    # ===== 進出場價格 =====
    entry_price: float = 0.0
    take_profit: float = 0.0
    stop_loss: float = 0.0
    position_size_pct: float = 0.0
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    def to_feature_vector(self) -> List[float]:
        """轉換為 ML 特徵向量 (數值欄位)"""
        return [
            self.predicted_probability,
            self.predicted_confidence,
            self.obi,
            self.wpi,
            self.vpin,
            self.funding_rate * 10000,  # 放大
            self.oi_change_pct,
            self.liquidation_pressure_long / 100,
            self.liquidation_pressure_short / 100,
            self.price_change_1m_pct,
            self.price_change_5m_pct,
            self.price_change_15m_pct,
            self.price_change_1h_pct,
            self.volatility_5m,
            self.volatility_1h,
            self.volume_ratio_5m,
            self.volume_ratio_1h,
            1 if self.direction == "LONG" else 0,
            # 策略機率
            self.prob_bull_trap,
            self.prob_bear_trap,
            self.prob_fakeout,
            self.prob_stop_hunt,
            self.prob_spoofing,
            self.prob_whipsaw,
            self.prob_consolidation_shake,
            self.prob_flash_crash,
            self.prob_slow_bleed,
            self.prob_accumulation,
            self.prob_distribution,
            self.prob_re_accumulation,
            self.prob_re_distribution,
            self.prob_long_squeeze,
            self.prob_short_squeeze,
            self.prob_cascade_liquidation,
            self.prob_trend_push,
            self.prob_trend_continuation,
            self.prob_trend_reversal,
            self.prob_pump_and_dump,
            self.prob_wash_trading,
            self.prob_layering,
        ]
    
    @staticmethod
    def feature_names() -> List[str]:
        """特徵名稱列表"""
        return [
            "predicted_probability",
            "predicted_confidence",
            "obi",
            "wpi",
            "vpin",
            "funding_rate",
            "oi_change_pct",
            "liq_pressure_long",
            "liq_pressure_short",
            "price_change_1m",
            "price_change_5m",
            "price_change_15m",
            "price_change_1h",
            "volatility_5m",
            "volatility_1h",
            "volume_ratio_5m",
            "volume_ratio_1h",
            "is_long",
            "prob_bull_trap",
            "prob_bear_trap",
            "prob_fakeout",
            "prob_stop_hunt",
            "prob_spoofing",
            "prob_whipsaw",
            "prob_consolidation_shake",
            "prob_flash_crash",
            "prob_slow_bleed",
            "prob_accumulation",
            "prob_distribution",
            "prob_re_accumulation",
            "prob_re_distribution",
            "prob_long_squeeze",
            "prob_short_squeeze",
            "prob_cascade_liquidation",
            "prob_trend_push",
            "prob_trend_continuation",
            "prob_trend_reversal",
            "prob_pump_and_dump",
            "prob_wash_trading",
            "prob_layering",
        ]


@dataclass
class TradeOutcome:
    """
    交易結果
    """
    trade_id: str
    
    # 結果
    is_successful: bool              # 是否成功 (盈利)
    hit_tp: bool = False             # 是否止盈
    hit_sl: bool = False             # 是否止損
    
    # 盈虧
    pnl_pct: float = 0.0
    pnl_usd: float = 0.0
    
    # 極值
    max_profit_pct: float = 0.0      # 最大浮盈
    max_drawdown_pct: float = 0.0    # 最大回撤
    
    # 時間
    duration_minutes: float = 0.0
    
    # 出場價格
    exit_price: float = 0.0
    
    def to_dict(self) -> Dict:
        return asdict(self)


# ============================================================
# 資料收集器
# ============================================================

class WhaleTrainingDataCollector:
    """
    收集並整理 Whale Trading 訓練資料
    """
    
    def __init__(self, data_dir: str = "logs/whale_training"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.features_file = self.data_dir / "trade_features.json"
        self.outcomes_file = self.data_dir / "trade_outcomes.json"
        self.dataset_file = self.data_dir / "training_dataset.parquet"
        
        # 載入現有資料
        self.features: List[TradeFeatures] = self._load_features()
        self.outcomes: Dict[str, TradeOutcome] = self._load_outcomes()
    
    def _load_features(self) -> List[TradeFeatures]:
        """載入特徵資料"""
        if self.features_file.exists():
            with open(self.features_file) as f:
                data = json.load(f)
            return [TradeFeatures(**d) for d in data]
        return []
    
    def _load_outcomes(self) -> Dict[str, TradeOutcome]:
        """載入結果資料"""
        if self.outcomes_file.exists():
            with open(self.outcomes_file) as f:
                data = json.load(f)
            return {d["trade_id"]: TradeOutcome(**d) for d in data}
        return {}
    
    def save(self):
        """保存所有資料"""
        # 保存特徵
        with open(self.features_file, 'w') as f:
            json.dump([f.to_dict() for f in self.features], f, indent=2, ensure_ascii=False)
        
        # 保存結果
        with open(self.outcomes_file, 'w') as f:
            json.dump([o.to_dict() for o in self.outcomes.values()], f, indent=2, ensure_ascii=False)
        
        print(f"✅ 已保存 {len(self.features)} 筆特徵, {len(self.outcomes)} 筆結果")
    
    def add_trade_features(self, features: TradeFeatures):
        """添加交易特徵"""
        self.features.append(features)
        self.save()
    
    def add_trade_outcome(self, outcome: TradeOutcome):
        """添加交易結果"""
        self.outcomes[outcome.trade_id] = outcome
        self.save()
    
    def build_training_dataset(self) -> pd.DataFrame:
        """
        整合特徵和結果，建立訓練資料集
        """
        if not self.features:
            print("⚠️ 沒有特徵資料")
            return pd.DataFrame()
        
        rows = []
        for feat in self.features:
            if feat.trade_id not in self.outcomes:
                continue  # 只包含有結果的交易
            
            outcome = self.outcomes[feat.trade_id]
            
            row = {
                "trade_id": feat.trade_id,
                "timestamp": feat.timestamp,
                "strategy": feat.strategy,
                "direction": feat.direction,
            }
            
            # 添加所有特徵
            feature_values = feat.to_feature_vector()
            feature_names = TradeFeatures.feature_names()
            for name, value in zip(feature_names, feature_values):
                row[f"feat_{name}"] = value
            
            # 添加標籤
            row["label_success"] = 1 if outcome.is_successful else 0
            row["label_hit_tp"] = 1 if outcome.hit_tp else 0
            row["label_pnl_pct"] = outcome.pnl_pct
            row["label_max_profit"] = outcome.max_profit_pct
            row["label_max_drawdown"] = outcome.max_drawdown_pct
            row["label_duration"] = outcome.duration_minutes
            
            rows.append(row)
        
        if not rows:
            print("⚠️ 沒有完整的交易記錄")
            return pd.DataFrame()
        
        df = pd.DataFrame(rows)
        
        # 保存為 Parquet
        df.to_parquet(self.dataset_file, index=False)
        print(f"✅ 已建立訓練資料集: {self.dataset_file}")
        print(f"   樣本數: {len(df)}")
        print(f"   特徵數: {len([c for c in df.columns if c.startswith('feat_')])}")
        print(f"   成功率: {df['label_success'].mean():.1%}")
        
        return df
    
    def get_statistics(self) -> Dict:
        """獲取資料統計"""
        total_features = len(self.features)
        total_outcomes = len(self.outcomes)
        matched = sum(1 for f in self.features if f.trade_id in self.outcomes)
        
        if total_outcomes > 0:
            successes = sum(1 for o in self.outcomes.values() if o.is_successful)
            win_rate = successes / total_outcomes
            avg_pnl = sum(o.pnl_pct for o in self.outcomes.values()) / total_outcomes
        else:
            win_rate = 0
            avg_pnl = 0
        
        # 按策略統計
        strategy_stats = {}
        for feat in self.features:
            if feat.trade_id in self.outcomes:
                s = feat.strategy
                if s not in strategy_stats:
                    strategy_stats[s] = {"total": 0, "success": 0}
                strategy_stats[s]["total"] += 1
                if self.outcomes[feat.trade_id].is_successful:
                    strategy_stats[s]["success"] += 1
        
        return {
            "total_features": total_features,
            "total_outcomes": total_outcomes,
            "matched_trades": matched,
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl,
            "strategy_stats": strategy_stats,
            "ready_for_training": matched >= 20
        }
    
    def print_report(self):
        """打印資料報告"""
        stats = self.get_statistics()
        
        print("\n" + "=" * 60)
        print("📊 WHALE TRAINING DATA REPORT")
        print("=" * 60)
        
        print(f"\n📁 資料路徑: {self.data_dir}")
        print(f"\n📈 資料統計:")
        print(f"   特徵記錄: {stats['total_features']}")
        print(f"   結果記錄: {stats['total_outcomes']}")
        print(f"   完整交易: {stats['matched_trades']}")
        print(f"   勝率:     {stats['win_rate']:.1%}")
        print(f"   平均盈虧: {stats['avg_pnl_pct']:+.2f}%")
        
        if stats['strategy_stats']:
            print(f"\n🎯 策略統計:")
            for s, data in sorted(stats['strategy_stats'].items(), 
                                  key=lambda x: x[1]['total'], reverse=True):
                wr = data['success'] / data['total'] if data['total'] > 0 else 0
                print(f"   {s:<20} | {data['total']:>3}筆 | 勝率: {wr:.1%}")
        
        print(f"\n🧠 訓練準備:")
        if stats['ready_for_training']:
            print(f"   ✅ 資料足夠 ({stats['matched_trades']} >= 20)，可以訓練")
        else:
            print(f"   ⚠️ 資料不足 ({stats['matched_trades']} < 20)，需要更多交易")
        
        print("\n" + "=" * 60)


# ============================================================
# 整合到 Paper Trader
# ============================================================

def create_features_from_snapshot(
    trade_id: str,
    snapshot: Any,  # WhaleStrategySnapshot
    market_data: Dict
) -> TradeFeatures:
    """
    從 WhaleStrategySnapshot 創建特徵
    """
    # 提取策略機率
    probs = snapshot.strategy_probabilities or {}
    
    features = TradeFeatures(
        trade_id=trade_id,
        timestamp=datetime.now().isoformat(),
        strategy=snapshot.primary_strategy.strategy.name if snapshot.primary_strategy else "UNKNOWN",
        direction=snapshot.entry_signal.direction.value if snapshot.entry_signal else "WAIT",
        predicted_probability=snapshot.primary_strategy.probability if snapshot.primary_strategy else 0,
        predicted_confidence=snapshot.primary_strategy.confidence if snapshot.primary_strategy else 0,
        current_price=snapshot.current_price,
        obi=market_data.get('obi', 0),
        wpi=market_data.get('wpi', 0),
        vpin=market_data.get('vpin', 0),
        funding_rate=market_data.get('funding_rate', 0),
        oi_change_pct=market_data.get('oi_change_pct', 0),
        liquidation_pressure_long=market_data.get('liq_long', 50),
        liquidation_pressure_short=market_data.get('liq_short', 50),
        price_change_1m_pct=market_data.get('price_change_1m', 0),
        price_change_5m_pct=market_data.get('price_change_5m', 0),
        # 策略機率
        prob_bull_trap=probs.get('BULL_TRAP', 0),
        prob_bear_trap=probs.get('BEAR_TRAP', 0),
        prob_fakeout=probs.get('FAKEOUT', 0),
        prob_stop_hunt=probs.get('STOP_HUNT', 0),
        prob_spoofing=probs.get('SPOOFING', 0),
        prob_whipsaw=probs.get('WHIPSAW', 0),
        prob_consolidation_shake=probs.get('CONSOLIDATION_SHAKE', 0),
        prob_flash_crash=probs.get('FLASH_CRASH', 0),
        prob_slow_bleed=probs.get('SLOW_BLEED', 0),
        prob_accumulation=probs.get('ACCUMULATION', 0),
        prob_distribution=probs.get('DISTRIBUTION', 0),
        prob_re_accumulation=probs.get('RE_ACCUMULATION', 0),
        prob_re_distribution=probs.get('RE_DISTRIBUTION', 0),
        prob_long_squeeze=probs.get('LONG_SQUEEZE', 0),
        prob_short_squeeze=probs.get('SHORT_SQUEEZE', 0),
        prob_cascade_liquidation=probs.get('CASCADE_LIQUIDATION', 0),
        prob_trend_push=probs.get('TREND_PUSH', 0),
        prob_trend_continuation=probs.get('TREND_CONTINUATION', 0),
        prob_trend_reversal=probs.get('TREND_REVERSAL', 0),
        prob_pump_and_dump=probs.get('PUMP_AND_DUMP', 0),
        prob_wash_trading=probs.get('WASH_TRADING', 0),
        prob_layering=probs.get('LAYERING', 0),
    )
    
    if snapshot.entry_signal:
        features.entry_price = snapshot.entry_signal.entry_price
        features.take_profit = snapshot.entry_signal.take_profit
        features.stop_loss = snapshot.entry_signal.stop_loss
        features.position_size_pct = snapshot.entry_signal.position_size_pct
    
    return features


# ============================================================
# 主程式
# ============================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Whale Training Data Collector')
    parser.add_argument('--report', action='store_true', help='顯示資料報告')
    parser.add_argument('--build', action='store_true', help='建立訓練資料集')
    
    args = parser.parse_args()
    
    collector = WhaleTrainingDataCollector()
    
    if args.report:
        collector.print_report()
    elif args.build:
        df = collector.build_training_dataset()
        if not df.empty:
            print(df.head())
    else:
        collector.print_report()


if __name__ == "__main__":
    main()
