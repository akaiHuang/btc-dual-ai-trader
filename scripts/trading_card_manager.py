#!/usr/bin/env python3
"""
🎴 Trading Card Manager v2.0
============================
完整的交易參數卡片系統

每張卡片 = 一套完整的 TradingConfig 參數
可以根據市場情況動態切換不同的卡片

卡片類型:
- 市場趨勢卡: trending_bull, trending_bear, ranging_sideways
- 波動度卡: high_volatility, low_volatility_calm
- 特殊情況卡: whale_activity, news_event, recovery_mode
- 交易風格卡: scalp_aggressive, conservative_safe
"""

import json
import os
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List
from datetime import datetime
import copy

logger = logging.getLogger(__name__)


@dataclass
class CardMeta:
    """卡片元數據"""
    card_id: str
    card_name: str
    description: str
    version: str
    created_at: str
    market_condition: str
    risk_level: str
    expected_win_rate: float
    tags: List[str] = field(default_factory=list)


@dataclass
class TradingCard:
    """交易卡片"""
    meta: CardMeta
    parameters: Dict[str, Any]
    extends: Optional[str] = None
    
    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> 'TradingCard':
        """從 JSON 創建卡片"""
        meta_data = data.get('_meta', {})
        meta = CardMeta(
            card_id=meta_data.get('card_id', 'unknown'),
            card_name=meta_data.get('card_name', 'Unknown'),
            description=meta_data.get('description', ''),
            version=meta_data.get('version', '1.0'),
            created_at=meta_data.get('created_at', ''),
            market_condition=meta_data.get('market_condition', ''),
            risk_level=meta_data.get('risk_level', 'medium'),
            expected_win_rate=meta_data.get('expected_win_rate', 0.5),
            tags=meta_data.get('tags', [])
        )
        
        # 提取所有非 meta 的參數
        parameters = {}
        for key, value in data.items():
            if not key.startswith('_'):
                parameters[key] = value
        
        extends = data.get('_extends')
        
        return cls(meta=meta, parameters=parameters, extends=extends)
    
    def to_flat_dict(self) -> Dict[str, Any]:
        """將嵌套參數展平為單層 dict (用於 TradingConfig)"""
        flat = {}
        
        # 需要保持為字典的特殊欄位 (不展平)
        keep_as_dict = {
            'auto_backtest_integration',
            'dydx_simulation',
            'chase_protection',
            'early_exit_v2',
            'whale_detection',
        }
        
        for category, params in self.parameters.items():
            if isinstance(params, dict):
                # 檢查是否是特殊欄位 (保持為 dict)
                if category in keep_as_dict:
                    flat[category] = params
                    continue

                # 注意：卡片內可能同名欄位重複出現（例如 basic.leverage 與 random_entry_specific.leverage）。
                # 若已有數值型 (int/float) 欄位，避免被描述字串覆蓋，確保 TradingConfig 型別正確。
                for key, value in params.items():
                    if key in flat:
                        existing = flat[key]
                        if isinstance(existing, (int, float)) and isinstance(value, str):
                            continue
                        if isinstance(existing, str) and isinstance(value, (int, float)):
                            flat[key] = value
                            continue
                    flat[key] = value
            else:
                flat[category] = params
        return flat


class TradingCardManager:
    """
    交易卡片管理器
    
    負責:
    1. 載入所有卡片
    2. 處理卡片繼承 (_extends)
    3. 切換當前使用的卡片
    4. 根據市場情況自動選擇卡片
    """
    
    def __init__(self, cards_dir: str = "config/trading_cards"):
        self.cards_dir = Path(cards_dir)
        self.cards: Dict[str, TradingCard] = {}
        self.active_card_id: str = "base_default"
        self.master_config: Dict[str, Any] = {}
        self.card_history: List[Dict] = []
        
        # 載入所有卡片
        self._load_all_cards()
        self._load_master_config()
    
    def _load_master_config(self):
        """載入主配置"""
        master_path = self.cards_dir / "master_config.json"
        if master_path.exists():
            with open(master_path, 'r', encoding='utf-8') as f:
                self.master_config = json.load(f)
                self.active_card_id = self.master_config.get('active_card', 'base_default')
                logger.info(f"🎴 載入主配置，當前卡片: {self.active_card_id}")
    
    def _load_all_cards(self):
        """載入所有卡片"""
        if not self.cards_dir.exists():
            logger.warning(f"卡片目錄不存在: {self.cards_dir}")
            return
        
        for json_file in self.cards_dir.glob("*.json"):
            if json_file.name == "master_config.json":
                continue
            
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    card = TradingCard.from_json(data)
                    self.cards[card.meta.card_id] = card
                    logger.debug(f"載入卡片: {card.meta.card_name}")
            except Exception as e:
                logger.error(f"載入卡片失敗 {json_file}: {e}")
        
        # 處理繼承關係
        self._resolve_inheritance()
        
        logger.info(f"🎴 載入 {len(self.cards)} 張交易卡片")
    
    def _resolve_inheritance(self):
        """處理卡片繼承"""
        for card_id, card in self.cards.items():
            if card.extends and card.extends in self.cards:
                base_card = self.cards[card.extends]
                # 深度合併參數
                merged = self._deep_merge(
                    copy.deepcopy(base_card.parameters),
                    card.parameters
                )
                card.parameters = merged
                logger.debug(f"卡片 {card_id} 繼承自 {card.extends}")
    
    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        """深度合併字典"""
        result = copy.deepcopy(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    
    def get_card(self, card_id: str) -> Optional[TradingCard]:
        """獲取指定卡片"""
        return self.cards.get(card_id)
    
    def get_active_card(self) -> Optional[TradingCard]:
        """獲取當前啟用的卡片"""
        return self.cards.get(self.active_card_id)
    
    def switch_card(self, card_id: str, reason: str = "") -> bool:
        """切換當前卡片"""
        if card_id not in self.cards:
            logger.error(f"❌ 卡片不存在: {card_id}")
            return False
        
        old_card = self.active_card_id
        self.active_card_id = card_id
        
        # 記錄切換歷史
        self.card_history.append({
            'timestamp': datetime.now().isoformat(),
            'from_card': old_card,
            'to_card': card_id,
            'reason': reason
        })
        
        card = self.cards[card_id]
        logger.info(f"🎴 切換卡片: {card.meta.card_name} ({reason})")
        
        return True
    
    def get_config_dict(self, card_id: Optional[str] = None) -> Dict[str, Any]:
        """
        獲取卡片的完整配置字典 (用於創建 TradingConfig)
        
        Returns:
            展平後的參數字典，可直接傳給 TradingConfig
        """
        target_id = card_id or self.active_card_id
        card = self.cards.get(target_id)
        
        if not card:
            logger.warning(f"卡片不存在: {target_id}，使用預設")
            return {}
        
        return card.to_flat_dict()
    
    def list_cards(self, category: Optional[str] = None) -> List[TradingCard]:
        """列出所有卡片或指定類別的卡片"""
        if category and 'card_categories' in self.master_config:
            card_ids = self.master_config['card_categories'].get(category, [])
            return [self.cards[cid] for cid in card_ids if cid in self.cards]
        return list(self.cards.values())
    
    def auto_select_card(self, market_data: Dict[str, Any]) -> str:
        """
        根據市場數據自動選擇卡片
        
        Args:
            market_data: 包含以下字段
                - volatility: 波動度 (0-10)
                - trend_strength: 趨勢強度 (0-1)
                - trend_direction: 'up' | 'down' | 'neutral'
                - whale_activity: 鯨魚活動度 (0-1)
                - consecutive_losses: 連續虧損次數
                - news_event_active: 是否有新聞事件
        
        Returns:
            推薦的卡片 ID
        """
        if not self.master_config.get('auto_switch', {}).get('enabled', False):
            return self.active_card_id
        
        rules = self.master_config.get('card_switch_rules', [])
        
        # 按優先級排序
        sorted_rules = sorted(rules, key=lambda x: x.get('priority', 0), reverse=True)
        
        for rule in sorted_rules:
            if self._evaluate_condition(rule.get('condition', ''), market_data):
                return rule.get('switch_to', self.active_card_id)
        
        return self.active_card_id
    
    def _evaluate_condition(self, condition: str, data: Dict[str, Any]) -> bool:
        """評估條件表達式"""
        try:
            # 簡單的條件評估
            if not condition:
                return False
            
            # 替換變數
            expr = condition
            for key, value in data.items():
                if isinstance(value, str):
                    expr = expr.replace(key, f"'{value}'")
                else:
                    expr = expr.replace(key, str(value))
            
            # 安全評估
            return eval(expr, {"__builtins__": {}}, {})
        except:
            return False
    
    def show_cards_summary(self) -> str:
        """顯示所有卡片摘要"""
        lines = ["🎴 交易卡片系統 v2.0", "=" * 50]
        
        categories = self.master_config.get('card_categories', {})
        
        for cat_name, card_ids in categories.items():
            lines.append(f"\n📁 {cat_name.upper()}")
            for cid in card_ids:
                card = self.cards.get(cid)
                if card:
                    active = "✅" if cid == self.active_card_id else "  "
                    lines.append(f"  {active} {card.meta.card_name} [{card.meta.risk_level}] - {card.meta.description[:30]}...")
        
        lines.append(f"\n📌 當前使用: {self.active_card_id}")
        
        return "\n".join(lines)
    
    def save_active_card(self):
        """保存當前啟用的卡片到主配置"""
        master_path = self.cards_dir / "master_config.json"
        self.master_config['active_card'] = self.active_card_id
        self.master_config['_meta']['last_updated'] = datetime.now().isoformat()
        
        with open(master_path, 'w', encoding='utf-8') as f:
            json.dump(self.master_config, f, indent=4, ensure_ascii=False)
        
        logger.info(f"💾 保存當前卡片: {self.active_card_id}")
    
    def create_new_card(self, card_id: str, name: str, base_card: str = "base_default",
                        overrides: Dict[str, Any] = None, description: str = "") -> bool:
        """
        創建新卡片
        
        Args:
            card_id: 卡片 ID (唯一)
            name: 卡片名稱
            base_card: 繼承的基礎卡片
            overrides: 覆蓋的參數
            description: 描述
        
        Returns:
            是否創建成功
        """
        if card_id in self.cards:
            logger.error(f"卡片已存在: {card_id}")
            return False
        
        card_data = {
            "_meta": {
                "card_id": card_id,
                "card_name": name,
                "description": description,
                "version": "1.0",
                "created_at": datetime.now().strftime("%Y-%m-%d"),
                "market_condition": "custom",
                "risk_level": "medium",
                "expected_win_rate": 0.5,
                "tags": ["custom"]
            },
            "_extends": base_card
        }
        
        if overrides:
            card_data.update(overrides)
        
        # 保存到文件
        card_path = self.cards_dir / f"{card_id}.json"
        with open(card_path, 'w', encoding='utf-8') as f:
            json.dump(card_data, f, indent=4, ensure_ascii=False)
        
        # 重新載入
        card = TradingCard.from_json(card_data)
        self.cards[card_id] = card
        self._resolve_inheritance()
        
        logger.info(f"✨ 創建新卡片: {name}")
        return True
    
    def get_card_diff(self, card_id: str) -> Dict[str, Any]:
        """
        獲取卡片與基礎卡片的差異
        
        Returns:
            只有被修改的參數
        """
        card = self.cards.get(card_id)
        if not card or not card.extends:
            return {}
        
        base = self.cards.get(card.extends)
        if not base:
            return {}
        
        diff = {}
        base_flat = base.to_flat_dict()
        card_flat = card.to_flat_dict()
        
        for key, value in card_flat.items():
            if key not in base_flat or base_flat[key] != value:
                diff[key] = {'base': base_flat.get(key), 'card': value}
        
        return diff


def create_trading_config_from_card(card_manager: TradingCardManager, 
                                    card_id: Optional[str] = None) -> 'TradingConfig':
    """
    從卡片創建 TradingConfig 實例
    
    用法:
        manager = TradingCardManager()
        config = create_trading_config_from_card(manager, "scalp_aggressive")
    """
    # 這個函數會在主程式中實作，因為需要 import TradingConfig
    pass


# ==================== CLI 測試 ====================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    manager = TradingCardManager()
    
    print(manager.show_cards_summary())
    print("\n" + "=" * 50)
    
    # 測試切換卡片
    manager.switch_card("scalp_aggressive", "測試高頻交易")
    
    # 獲取配置
    config = manager.get_config_dict()
    print(f"\n📊 當前卡片參數數量: {len(config)}")
    
    # 顯示部分關鍵參數
    print("\n🔑 關鍵參數:")
    key_params = ['leverage', 'target_profit_pct', 'stop_loss_pct', 'max_daily_trades', 
                  'six_dim_min_score_to_trade', 'min_probability']
    for p in key_params:
        if p in config:
            print(f"  • {p}: {config[p]}")
    
    # 測試自動選卡
    print("\n🤖 自動選卡測試:")
    test_data = {
        'volatility': 3.0,
        'trend_strength': 0.2,
        'trend_direction': 'neutral',
        'whale_activity': 0.1,
        'consecutive_losses': 0,
        'news_event_active': False
    }
    recommended = manager.auto_select_card(test_data)
    print(f"  市場數據: {test_data}")
    print(f"  推薦卡片: {recommended}")
