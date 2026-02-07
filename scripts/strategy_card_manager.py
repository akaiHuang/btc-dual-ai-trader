#!/usr/bin/env python3
"""
🎴 策略卡片系統 (Strategy Card System)
動態切換交易策略參數，根據市場狀態自動選擇最適合的卡片組合
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class StrategyCard:
    """策略卡片"""
    card_id: str
    card_type: str  # entry, exit, risk, regime
    name: str
    description: str
    parameters: Dict
    meta: Dict = field(default_factory=dict)
    
    def __repr__(self):
        return f"<Card: {self.card_type}/{self.card_id} - {self.name}>"


@dataclass
class CardCombination:
    """卡片組合"""
    entry_card: StrategyCard
    exit_card: StrategyCard
    risk_card: StrategyCard
    regime: str
    activated_at: datetime = field(default_factory=datetime.now)
    trades_count: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    
    @property
    def win_rate(self) -> float:
        if self.trades_count == 0:
            return 0.0
        return self.wins / self.trades_count * 100
    
    def to_dict(self) -> Dict:
        return {
            "entry": self.entry_card.card_id,
            "exit": self.exit_card.card_id,
            "risk": self.risk_card.card_id,
            "regime": self.regime,
            "activated_at": self.activated_at.isoformat(),
            "trades_count": self.trades_count,
            "wins": self.wins,
            "losses": self.losses,
            "total_pnl": self.total_pnl,
            "win_rate": self.win_rate
        }


class StrategyCardManager:
    """
    🎴 策略卡片管理器
    
    功能:
    1. 載入和管理所有策略卡片
    2. 根據市場狀態自動選擇最佳卡片組合
    3. 追蹤各組合的表現
    4. 動態切換卡片
    """
    
    def __init__(self, cards_dir: str = "config/strategy_cards"):
        self.cards_dir = Path(cards_dir)
        self.cards: Dict[str, Dict[str, StrategyCard]] = {
            "entry": {},
            "exit": {},
            "risk": {},
            "regime": {}
        }
        self.master_config: Dict = {}
        self.active_combination: Optional[CardCombination] = None
        self.combination_history: List[Dict] = []
        self.last_switch_time: datetime = datetime.now()
        
        # 載入所有卡片
        self._load_all_cards()
        self._load_master_config()
        
    def _load_all_cards(self):
        """載入所有策略卡片"""
        for card_type in ["entry", "exit", "risk", "regime"]:
            type_dir = self.cards_dir / card_type
            if type_dir.exists():
                for file in type_dir.glob("*.json"):
                    try:
                        card = self._load_card(file)
                        if card:
                            self.cards[card_type][card.card_id] = card
                            logger.debug(f"載入卡片: {card}")
                    except Exception as e:
                        logger.warning(f"載入卡片失敗 {file}: {e}")
        
        # 統計
        total = sum(len(cards) for cards in self.cards.values())
        logger.info(f"🎴 載入 {total} 張策略卡片")
        for card_type, cards in self.cards.items():
            logger.info(f"   {card_type}: {len(cards)} 張")
    
    def _load_card(self, file_path: Path) -> Optional[StrategyCard]:
        """載入單張卡片"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        meta = data.get("_meta", {})
        return StrategyCard(
            card_id=meta.get("card_id", file_path.stem),
            card_type=meta.get("card_type", file_path.parent.name),
            name=meta.get("name", file_path.stem),
            description=meta.get("description", ""),
            parameters=data.get("parameters", {}),
            meta=meta
        )
    
    def _load_master_config(self):
        """載入主配置"""
        config_file = self.cards_dir / "master_config.json"
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                self.master_config = json.load(f)
            logger.info("📋 載入策略卡片主配置")
    
    def _save_master_config(self):
        """保存主配置"""
        config_file = self.cards_dir / "master_config.json"
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(self.master_config, f, indent=2, ensure_ascii=False)
    
    def get_card(self, card_type: str, card_id: str) -> Optional[StrategyCard]:
        """獲取指定卡片"""
        return self.cards.get(card_type, {}).get(card_id)
    
    def list_cards(self, card_type: Optional[str] = None) -> List[StrategyCard]:
        """列出所有卡片"""
        if card_type:
            return list(self.cards.get(card_type, {}).values())
        
        all_cards = []
        for cards in self.cards.values():
            all_cards.extend(cards.values())
        return all_cards
    
    def activate_combination(
        self, 
        entry_id: str, 
        exit_id: str, 
        risk_id: str,
        regime: str = "manual"
    ) -> Optional[CardCombination]:
        """
        啟用卡片組合
        
        Args:
            entry_id: 進場卡片 ID
            exit_id: 出場卡片 ID
            risk_id: 風控卡片 ID
            regime: 市場狀態
        
        Returns:
            CardCombination or None
        """
        entry_card = self.get_card("entry", entry_id)
        exit_card = self.get_card("exit", exit_id)
        risk_card = self.get_card("risk", risk_id)
        
        if not all([entry_card, exit_card, risk_card]):
            missing = []
            if not entry_card: missing.append(f"entry/{entry_id}")
            if not exit_card: missing.append(f"exit/{exit_id}")
            if not risk_card: missing.append(f"risk/{risk_id}")
            logger.error(f"❌ 找不到卡片: {missing}")
            return None
        
        # 保存上一個組合的表現
        if self.active_combination:
            self.combination_history.append(self.active_combination.to_dict())
        
        # 啟用新組合
        self.active_combination = CardCombination(
            entry_card=entry_card,
            exit_card=exit_card,
            risk_card=risk_card,
            regime=regime
        )
        self.last_switch_time = datetime.now()
        
        # 更新主配置
        self.master_config["active_cards"] = {
            "entry": entry_id,
            "exit": exit_id,
            "risk": risk_id,
            "regime": regime
        }
        self._save_master_config()
        
        logger.info(f"🎴 切換卡片組合: {entry_card.name} + {exit_card.name} + {risk_card.name}")
        return self.active_combination
    
    def get_merged_parameters(self) -> Dict:
        """
        獲取合併後的參數
        
        將 entry + exit + risk 卡片的參數合併成一個 dict
        """
        if not self.active_combination:
            return {}
        
        merged = {}
        
        # 依序合併 (後面的覆蓋前面的)
        for card in [
            self.active_combination.risk_card,
            self.active_combination.exit_card,
            self.active_combination.entry_card
        ]:
            merged.update(card.parameters)
        
        return merged
    
    def record_trade_result(self, is_win: bool, pnl: float):
        """記錄交易結果"""
        if self.active_combination:
            self.active_combination.trades_count += 1
            if is_win:
                self.active_combination.wins += 1
            else:
                self.active_combination.losses += 1
            self.active_combination.total_pnl += pnl
    
    def detect_market_regime(self, market_data: Dict) -> str:
        """
        檢測市場狀態
        
        Args:
            market_data: 市場數據 (包含 obi, price_change, volatility 等)
        
        Returns:
            市場狀態 ID (trending_up, trending_down, ranging, volatile, calm, whale_activity)
        """
        obi = market_data.get('obi', 0)
        price_change_5m = market_data.get('price_change_5m', 0)
        price_change_15m = market_data.get('price_change_15m', 0)
        volatility_5m = market_data.get('volatility_5m', 0)
        whale_count = market_data.get('whale_trade_count', 0)
        whale_value = market_data.get('whale_total_value', 0)
        volume_ratio = market_data.get('volume_ratio', 1.0)
        
        # 1. 鯨魚活躍檢測 (優先級最高)
        if whale_count >= 5 or whale_value >= 200000:
            if abs(obi) > 0.20:
                return "WHALE_ACTIVITY"
        
        # 2. 高波動檢測
        if volatility_5m > 0.30:
            return "HIGH_VOLATILITY"
        
        # 3. 趨勢檢測
        if price_change_5m > 0.15 and price_change_15m > 0.30:
            if obi > 0.10 or volume_ratio > 1.2:
                return "TRENDING_UP"
        
        if price_change_5m < -0.15 and price_change_15m < -0.30:
            if obi < -0.10 or volume_ratio < 0.8:
                return "TRENDING_DOWN"
        
        # 4. 平靜市場檢測
        if volatility_5m < 0.05 and abs(obi) < 0.05:
            return "CALM"
        
        # 5. 震盪市場 (默認)
        if abs(price_change_5m) < 0.10 and abs(price_change_15m) < 0.20:
            return "RANGING"
        
        return "UNKNOWN"
    
    def auto_select_cards(self, market_data: Dict) -> Optional[CardCombination]:
        """
        根據市場狀態自動選擇最佳卡片組合
        
        Args:
            market_data: 市場數據
        
        Returns:
            新的卡片組合 (如果切換) 或 None (如果不需要切換)
        """
        if not self.master_config.get("auto_switch", {}).get("enabled", False):
            return None
        
        # 檢查冷卻時間
        cooldown = self.master_config.get("auto_switch", {}).get("switch_cooldown_sec", 600)
        elapsed = (datetime.now() - self.last_switch_time).total_seconds()
        if elapsed < cooldown:
            return None
        
        # 檢測市場狀態
        regime = self.detect_market_regime(market_data)
        
        # 如果狀態沒變，不切換
        if self.active_combination and self.active_combination.regime == regime:
            return None
        
        # 獲取該狀態的推薦卡片
        mapping = self.master_config.get("regime_card_mapping", {})
        recommended = mapping.get(regime, {})
        
        if not recommended:
            logger.debug(f"無 {regime} 狀態的推薦卡片")
            return None
        
        # 檢查當前組合表現是否太差 (需要切換)
        if self.active_combination:
            min_trades = self.master_config.get("auto_switch", {}).get("min_trades_to_evaluate", 5)
            win_threshold = self.master_config.get("auto_switch", {}).get("win_rate_threshold_to_switch", 40.0)
            
            if self.active_combination.trades_count >= min_trades:
                if self.active_combination.win_rate < win_threshold:
                    logger.info(f"⚠️ 當前組合勝率 {self.active_combination.win_rate:.1f}% < {win_threshold}%，觸發切換")
                else:
                    # 表現還可以，不強制切換
                    return None
        
        # 切換到新組合
        entry_id = recommended.get("entry", "six_dim_strict")
        exit_id = recommended.get("exit", "lock_profit")
        risk_id = recommended.get("risk", "adaptive")
        
        logger.info(f"🔄 市場狀態: {regime} → 自動切換卡片")
        return self.activate_combination(entry_id, exit_id, risk_id, regime)
    
    def show_active_cards(self) -> str:
        """顯示當前啟用的卡片"""
        if not self.active_combination:
            return "❌ 沒有啟用的卡片組合"
        
        combo = self.active_combination
        lines = [
            "🎴 當前卡片組合:",
            f"   進場: {combo.entry_card.name}",
            f"   出場: {combo.exit_card.name}",
            f"   風控: {combo.risk_card.name}",
            f"   狀態: {combo.regime}",
            f"   表現: {combo.trades_count}筆 | 勝率 {combo.win_rate:.1f}% | PnL ${combo.total_pnl:.2f}"
        ]
        return "\n".join(lines)
    
    def get_card_recommendations(self, market_data: Dict) -> Dict:
        """
        根據市場數據獲取卡片推薦
        
        Returns:
            {
                "detected_regime": "TRENDING_UP",
                "recommended_entry": "momentum_follow",
                "recommended_exit": "hold_winner",
                "recommended_risk": "high_leverage",
                "reason": "趨勢明顯，建議跟隨動能"
            }
        """
        regime = self.detect_market_regime(market_data)
        mapping = self.master_config.get("regime_card_mapping", {})
        recommended = mapping.get(regime, {})
        
        # 獲取推薦理由
        regime_card = self.get_card("regime", regime.lower())
        reason = ""
        if regime_card:
            reason = regime_card.meta.get("strategy", regime_card.description)
        
        return {
            "detected_regime": regime,
            "recommended_entry": recommended.get("entry", "six_dim_strict"),
            "recommended_exit": recommended.get("exit", "lock_profit"),
            "recommended_risk": recommended.get("risk", "adaptive"),
            "reason": reason
        }


def main():
    """測試策略卡片系統"""
    logging.basicConfig(level=logging.INFO)
    
    # 初始化管理器
    manager = StrategyCardManager()
    
    # 列出所有卡片
    print("\n📋 所有可用卡片:")
    for card_type in ["entry", "exit", "risk", "regime"]:
        cards = manager.list_cards(card_type)
        print(f"\n{card_type.upper()} ({len(cards)} 張):")
        for card in cards:
            print(f"  - {card.card_id}: {card.name}")
    
    # 啟用默認組合
    print("\n🎴 啟用默認組合...")
    manager.activate_combination("six_dim_strict", "lock_profit", "adaptive", "manual")
    print(manager.show_active_cards())
    
    # 模擬市場數據
    market_data = {
        'obi': 0.15,
        'price_change_5m': 0.20,
        'price_change_15m': 0.35,
        'volatility_5m': 0.15,
        'whale_trade_count': 2,
        'whale_total_value': 80000,
        'volume_ratio': 1.3
    }
    
    # 獲取推薦
    print("\n🔍 市場分析與推薦:")
    rec = manager.get_card_recommendations(market_data)
    print(f"  檢測狀態: {rec['detected_regime']}")
    print(f"  推薦進場: {rec['recommended_entry']}")
    print(f"  推薦出場: {rec['recommended_exit']}")
    print(f"  推薦風控: {rec['recommended_risk']}")
    print(f"  理由: {rec['reason']}")
    
    # 獲取合併參數
    print("\n📊 合併後參數:")
    params = manager.get_merged_parameters()
    for key in ['six_dim_min_score_to_trade', 'stop_loss_pct', 'take_profit_pct', 'leverage_default']:
        if key in params:
            print(f"  {key}: {params[key]}")


if __name__ == "__main__":
    main()
