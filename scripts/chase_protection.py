#!/usr/bin/env python3
"""
追單保護模組 v1.0
==================
防止連續同方向追單導致的連續虧損

核心問題：
- 第1筆 SHORT 獲利後，價格已下跌
- 系統繼續做 SHORT，但行情已接近反轉
- 結果連續虧損 2-3 筆

解決方案：
1. 同方向冷卻 - 獲利後同方向需等待
2. 價格累計移動檢查 - 價格已大幅移動則提高門檻
3. 六維分數衰減檢查 - 分數下降則拒絕進場
4. 動能耗盡檢測 - RSI/成交量異常則警告

作者: AI Trading System
日期: 2025-12-11
版本: v1.0
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import deque


@dataclass
class TradeMemory:
    """交易記憶"""
    timestamp: float
    direction: str          # LONG / SHORT
    entry_price: float
    exit_price: float
    pnl_pct: float
    is_win: bool
    six_dim_score: int
    hold_time_sec: float
    market_price_at_entry: float  # 進場時的市場價格


@dataclass 
class ChaseProtectionConfig:
    """追單保護配置"""
    # 同方向冷卻
    same_direction_cooldown_enabled: bool = True
    same_direction_cooldown_after_win_sec: float = 120.0  # 獲利後同方向冷卻 2 分鐘
    same_direction_cooldown_after_loss_sec: float = 60.0  # 虧損後同方向冷卻 1 分鐘
    
    # 連續同方向限制
    max_consecutive_same_direction: int = 2  # 最多連續同方向 2 筆
    
    # 強信號繞過 (v14.10)
    strong_signal_bypass_enabled: bool = False  # 是否啟用強信號繞過
    strong_signal_min_score: int = 12  # 強信號最低分數 (滿分繞過)
    
    # 價格累計移動檢查
    price_move_check_enabled: bool = True
    price_move_threshold_pct: float = 0.5  # 價格已移動 0.5% 則警告
    price_move_block_threshold_pct: float = 1.0  # 價格已移動 1% 則阻擋
    price_move_lookback_sec: float = 300.0  # 看過去 5 分鐘
    
    # 六維分數衰減檢查
    score_decay_check_enabled: bool = True
    min_score_vs_last_trade: int = -2  # 分數最多比上筆低 2 分
    
    # 動能耗盡檢測
    momentum_exhaustion_enabled: bool = True
    volume_decay_threshold: float = 0.5  # 成交量衰減到 50% 以下
    
    # 反向信號優先
    prefer_reversal_after_win: bool = True  # 獲利後優先等反向信號


class ChaseProtectionModule:
    """追單保護模組"""
    
    def __init__(self, config: Optional[ChaseProtectionConfig] = None):
        self.config = config or ChaseProtectionConfig()
        
        # 交易記憶
        self.trade_history: deque = deque(maxlen=50)  # 最近 50 筆
        self.last_trade: Optional[TradeMemory] = None
        
        # 價格歷史
        self.price_history: deque = deque(maxlen=300)  # 5 分鐘 @ 1秒
        
        # 統計
        self.consecutive_same_direction: int = 0
        self.current_direction_streak: str = ""  # 當前連續方向
        self.last_trade_time: float = 0
        self.blocked_count: int = 0
        self.warned_count: int = 0
        
        # 狀態文件
        self.state_file = Path("data/chase_protection_state.json")
        self._load_state()
    
    def _load_state(self):
        """載入狀態"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                self.consecutive_same_direction = state.get('consecutive_same_direction', 0)
                self.current_direction_streak = state.get('current_direction_streak', '')
                self.last_trade_time = state.get('last_trade_time', 0)
                self.blocked_count = state.get('blocked_count', 0)
                self.warned_count = state.get('warned_count', 0)
                
                # 載入最後一筆交易
                if state.get('last_trade'):
                    self.last_trade = TradeMemory(**state['last_trade'])
            except Exception as e:
                print(f"⚠️ 載入追單保護狀態失敗: {e}")
    
    def _save_state(self):
        """保存狀態"""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            'consecutive_same_direction': self.consecutive_same_direction,
            'current_direction_streak': self.current_direction_streak,
            'last_trade_time': self.last_trade_time,
            'blocked_count': self.blocked_count,
            'warned_count': self.warned_count,
            'last_trade': asdict(self.last_trade) if self.last_trade else None,
            'updated_at': datetime.now().isoformat()
        }
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    def record_price(self, price: float):
        """記錄價格"""
        self.price_history.append({
            'price': price,
            'timestamp': time.time()
        })
    
    def record_trade(
        self,
        direction: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        is_win: bool,
        six_dim_score: int = 0,
        hold_time_sec: float = 0,
        market_price: float = 0
    ):
        """
        記錄交易結果
        
        Args:
            direction: LONG / SHORT
            entry_price: 進場價
            exit_price: 出場價
            pnl_pct: 盈虧 %
            is_win: 是否獲利
            six_dim_score: 六維分數
            hold_time_sec: 持倉秒數
            market_price: 進場時市場價格
        """
        trade = TradeMemory(
            timestamp=time.time(),
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl_pct=pnl_pct,
            is_win=is_win,
            six_dim_score=six_dim_score,
            hold_time_sec=hold_time_sec,
            market_price_at_entry=market_price or entry_price
        )
        
        self.trade_history.append(trade)
        self.last_trade = trade
        self.last_trade_time = time.time()
        
        # 更新連續同方向計數
        if direction == self.current_direction_streak:
            self.consecutive_same_direction += 1
        else:
            self.consecutive_same_direction = 1
            self.current_direction_streak = direction
        
        self._save_state()
    
    def check_entry(
        self,
        direction: str,
        current_price: float,
        six_dim_score: int = 0,
        volume_ratio: float = 1.0,
        market_data: Optional[Dict] = None
    ) -> Tuple[bool, str, Dict]:
        """
        檢查是否允許進場
        
        Args:
            direction: 想要進場的方向 LONG / SHORT
            current_price: 當前價格
            six_dim_score: 當前六維分數
            volume_ratio: 成交量比率 (相對於平均)
            market_data: 額外市場數據
            
        Returns:
            (允許進場, 原因, 詳細資訊)
        """
        result = {
            'checks_passed': [],
            'checks_failed': [],
            'warnings': [],
            'recommendation': ''
        }
        
        # ============================================================
        # 1. 同方向冷卻檢查
        # ============================================================
        if self.config.same_direction_cooldown_enabled and self.last_trade:
            if direction == self.last_trade.direction:
                time_since_last = time.time() - self.last_trade_time
                
                if self.last_trade.is_win:
                    cooldown = self.config.same_direction_cooldown_after_win_sec
                    if time_since_last < cooldown:
                        remaining = cooldown - time_since_last
                        reason = f"🛡️ 同方向冷卻中 (獲利後): 還需等待 {remaining:.0f} 秒"
                        result['checks_failed'].append(reason)
                        self.blocked_count += 1
                        self._save_state()
                        return False, reason, result
                else:
                    cooldown = self.config.same_direction_cooldown_after_loss_sec
                    if time_since_last < cooldown:
                        remaining = cooldown - time_since_last
                        reason = f"🛡️ 同方向冷卻中 (虧損後): 還需等待 {remaining:.0f} 秒"
                        result['checks_failed'].append(reason)
                        self.blocked_count += 1
                        self._save_state()
                        return False, reason, result
                
                result['checks_passed'].append("✅ 同方向冷卻已過")
        
        # ============================================================
        # 2. 連續同方向限制 (支援強信號繞過)
        # ============================================================
        if self.last_trade and direction == self.current_direction_streak:
            if self.consecutive_same_direction >= self.config.max_consecutive_same_direction:
                # v14.10: 強信號繞過檢查
                if self.config.strong_signal_bypass_enabled and six_dim_score >= self.config.strong_signal_min_score:
                    result['warnings'].append(f"⚡ 強信號繞過 ({six_dim_score}/12): 允許連續 {direction}")
                else:
                    reason = f"🛡️ 連續同方向已達上限: {self.consecutive_same_direction}/{self.config.max_consecutive_same_direction}"
                    result['checks_failed'].append(reason)
                    result['recommendation'] = f"建議等待反向 ({self._opposite(direction)}) 信號"
                    self.blocked_count += 1
                    self._save_state()
                    return False, reason, result
            else:
                result['warnings'].append(f"⚠️ 已連續 {self.consecutive_same_direction} 筆 {direction}")
        
        # ============================================================
        # 3. 價格累計移動檢查
        # ============================================================
        if self.config.price_move_check_enabled and self.price_history:
            price_move = self._calculate_price_move(current_price)
            
            if price_move is not None:
                move_pct = price_move['move_pct']
                move_direction = price_move['direction']
                
                # 檢查是否追單 (價格已往該方向移動)
                is_chasing = (
                    (direction == "SHORT" and move_direction == "DOWN") or
                    (direction == "LONG" and move_direction == "UP")
                )
                
                if is_chasing:
                    if abs(move_pct) >= self.config.price_move_block_threshold_pct:
                        reason = f"🛡️ 追單風險過高: 價格已{move_direction} {abs(move_pct):.2f}%，拒絕 {direction}"
                        result['checks_failed'].append(reason)
                        self.blocked_count += 1
                        self._save_state()
                        return False, reason, result
                    elif abs(move_pct) >= self.config.price_move_threshold_pct:
                        result['warnings'].append(f"⚠️ 追單警告: 價格已{move_direction} {abs(move_pct):.2f}%")
                        self.warned_count += 1
                else:
                    result['checks_passed'].append(f"✅ 價格移動方向有利: {move_direction} {abs(move_pct):.2f}%")
        
        # ============================================================
        # 4. 六維分數衰減檢查
        # ============================================================
        if self.config.score_decay_check_enabled and self.last_trade:
            if direction == self.last_trade.direction:
                score_diff = six_dim_score - self.last_trade.six_dim_score
                
                if score_diff < self.config.min_score_vs_last_trade:
                    reason = f"🛡️ 六維分數衰減過大: {self.last_trade.six_dim_score} → {six_dim_score} (差 {score_diff})"
                    result['checks_failed'].append(reason)
                    self.blocked_count += 1
                    self._save_state()
                    return False, reason, result
                elif score_diff < 0:
                    result['warnings'].append(f"⚠️ 六維分數下降: {self.last_trade.six_dim_score} → {six_dim_score}")
                else:
                    result['checks_passed'].append(f"✅ 六維分數維持/上升: {six_dim_score}")
        
        # ============================================================
        # 5. 動能耗盡檢測
        # ============================================================
        if self.config.momentum_exhaustion_enabled:
            if volume_ratio < self.config.volume_decay_threshold:
                result['warnings'].append(f"⚠️ 成交量萎縮: {volume_ratio:.1%} (可能動能耗盡)")
                self.warned_count += 1
        
        # ============================================================
        # 6. 反向信號優先建議
        # ============================================================
        if self.config.prefer_reversal_after_win and self.last_trade:
            if self.last_trade.is_win and direction == self.last_trade.direction:
                opposite = self._opposite(direction)
                result['warnings'].append(f"💡 建議: 獲利後優先等待反向 ({opposite}) 信號")
        
        # 通過所有檢查
        return True, "✅ 追單保護檢查通過", result
    
    def _calculate_price_move(self, current_price: float) -> Optional[Dict]:
        """計算價格累計移動"""
        if not self.price_history:
            return None
        
        # 找到 lookback 時間內的最早價格
        now = time.time()
        lookback = self.config.price_move_lookback_sec
        
        earliest_price = None
        for entry in self.price_history:
            if now - entry['timestamp'] <= lookback:
                earliest_price = entry['price']
                break
        
        if earliest_price is None:
            return None
        
        move_pct = (current_price - earliest_price) / earliest_price * 100
        
        return {
            'earliest_price': earliest_price,
            'current_price': current_price,
            'move_pct': move_pct,
            'direction': "UP" if move_pct > 0 else "DOWN",
            'lookback_sec': lookback
        }
    
    def _opposite(self, direction: str) -> str:
        """返回反向"""
        return "SHORT" if direction == "LONG" else "LONG"
    
    def get_status(self) -> Dict:
        """獲取狀態"""
        return {
            'enabled': True,
            'last_trade_direction': self.last_trade.direction if self.last_trade else None,
            'last_trade_result': "WIN" if self.last_trade and self.last_trade.is_win else "LOSS" if self.last_trade else None,
            'consecutive_same_direction': self.consecutive_same_direction,
            'current_direction_streak': self.current_direction_streak,
            'time_since_last_trade': time.time() - self.last_trade_time if self.last_trade_time > 0 else None,
            'blocked_count': self.blocked_count,
            'warned_count': self.warned_count,
            'total_trades_recorded': len(self.trade_history)
        }
    
    def get_display(self) -> str:
        """獲取狀態顯示"""
        status = self.get_status()
        
        lines = [
            "╔══════════════════════════════════════╗",
            "║       🛡️ 追單保護模組狀態            ║",
            "╠══════════════════════════════════════╣",
        ]
        
        if self.last_trade:
            emoji = "✅" if self.last_trade.is_win else "❌"
            lines.append(f"║ 上筆: {emoji} {self.last_trade.direction} ({self.last_trade.pnl_pct:+.2f}%)")
        else:
            lines.append("║ 上筆: 無記錄")
        
        lines.append(f"║ 連續同向: {self.consecutive_same_direction} 筆 {self.current_direction_streak}")
        lines.append(f"║ 已阻擋: {self.blocked_count} 次 | 已警告: {self.warned_count} 次")
        lines.append("╚══════════════════════════════════════╝")
        
        return "\n".join(lines)
    
    def reset(self):
        """重置狀態"""
        self.consecutive_same_direction = 0
        self.current_direction_streak = ""
        self.last_trade = None
        self.last_trade_time = 0
        self.blocked_count = 0
        self.warned_count = 0
        self.trade_history.clear()
        self.price_history.clear()
        self._save_state()
        print("🔄 追單保護狀態已重置")


# ============================================================
# 便利函數
# ============================================================

def create_chase_protection(
    same_direction_cooldown_sec: float = 120.0,
    max_consecutive_same_direction: int = 2,
    price_move_block_pct: float = 1.0,
    strong_signal_bypass: bool = False,
    strong_signal_min_score: int = 12
) -> ChaseProtectionModule:
    """
    創建追單保護模組的便利函數
    
    Args:
        same_direction_cooldown_sec: 同方向冷卻秒數
        max_consecutive_same_direction: 最大連續同方向次數
        price_move_block_pct: 價格移動阻擋門檻 %
        strong_signal_bypass: 是否啟用強信號繞過 (v14.10)
        strong_signal_min_score: 強信號最低分數 (預設12=滿分)
    """
    config = ChaseProtectionConfig(
        same_direction_cooldown_after_win_sec=same_direction_cooldown_sec,
        max_consecutive_same_direction=max_consecutive_same_direction,
        price_move_block_threshold_pct=price_move_block_pct,
        strong_signal_bypass_enabled=strong_signal_bypass,
        strong_signal_min_score=strong_signal_min_score
    )
    return ChaseProtectionModule(config)


# ============================================================
# 測試
# ============================================================

if __name__ == "__main__":
    print("🛡️ 追單保護模組測試\n")
    
    # 創建模組
    module = create_chase_protection(
        same_direction_cooldown_sec=10,  # 測試用短冷卻
        max_consecutive_same_direction=2,
        price_move_block_pct=0.5
    )
    
    # 模擬價格歷史
    base_price = 90000
    for i in range(60):
        # 價格下跌
        price = base_price - (i * 10)
        module.record_price(price)
    
    current_price = base_price - 600  # $89,400 (跌了 0.67%)
    
    # 測試 1: 第一筆交易 SHORT
    print("=" * 50)
    print("測試 1: 第一筆 SHORT")
    allowed, reason, details = module.check_entry("SHORT", current_price, six_dim_score=10)
    print(f"結果: {'✅ 允許' if allowed else '❌ 拒絕'} - {reason}")
    
    if allowed:
        # 模擬交易完成 (獲利)
        module.record_trade(
            direction="SHORT",
            entry_price=89400,
            exit_price=89000,
            pnl_pct=1.5,
            is_win=True,
            six_dim_score=10
        )
        print("📝 記錄: SHORT 獲利 +1.5%")
    
    # 測試 2: 立即再做 SHORT (應該被冷卻阻擋)
    print("\n" + "=" * 50)
    print("測試 2: 立即再做 SHORT (應該被冷卻阻擋)")
    allowed, reason, details = module.check_entry("SHORT", 89000, six_dim_score=9)
    print(f"結果: {'✅ 允許' if allowed else '❌ 拒絕'} - {reason}")
    
    # 測試 3: 做 LONG (反向應該允許)
    print("\n" + "=" * 50)
    print("測試 3: 做 LONG (反向應該允許)")
    allowed, reason, details = module.check_entry("LONG", 89000, six_dim_score=8)
    print(f"結果: {'✅ 允許' if allowed else '❌ 拒絕'} - {reason}")
    
    # 等待冷卻
    print("\n⏳ 等待冷卻 11 秒...")
    import time as t
    t.sleep(11)
    
    # 測試 4: 冷卻後再做 SHORT
    print("\n" + "=" * 50)
    print("測試 4: 冷卻後再做 SHORT")
    allowed, reason, details = module.check_entry("SHORT", 88800, six_dim_score=8)
    print(f"結果: {'✅ 允許' if allowed else '❌ 拒絕'} - {reason}")
    if details.get('warnings'):
        print(f"警告: {details['warnings']}")
    
    # 顯示狀態
    print("\n" + module.get_display())
