#!/usr/bin/env python3
"""
自動回測模組 v1.0
================
功能：
1. 監控虧損觸發回測
2. 虧損 25% 後自動停止真實交易，保留虛擬交易
3. 自動回測並生成報告
4. 預留 LLM 接口用於自動創建交易卡片

作者: AI Trading System
日期: 2025-12-11
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
import threading
import time


class TradingMode(Enum):
    """交易模式"""
    REAL = "real"           # 真實交易
    PAPER = "paper"         # 虛擬交易
    PAUSED = "paused"       # 暫停（等待回測）
    BACKTEST = "backtest"   # 回測中


@dataclass
class TradeRecord:
    """交易記錄"""
    timestamp: str
    direction: str          # LONG / SHORT
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_usdt: float
    size_btc: float
    hold_time_sec: int
    six_dim_score: int = 0
    win: bool = False


@dataclass
class BacktestTriggerConfig:
    """回測觸發配置"""
    # 虧損觸發
    max_cumulative_loss_pct: float = 25.0      # 累計虧損 25% 觸發
    max_consecutive_losses: int = 5            # 連續虧損 5 次觸發
    
    # 勝率觸發
    min_win_rate_threshold: float = 40.0       # 勝率低於 40% 觸發
    min_trades_for_win_rate: int = 10          # 至少 10 筆交易才計算勝率
    
    # 時間觸發
    auto_backtest_hours: int = 24              # 每 24 小時自動回測
    
    # 行為設定
    pause_real_on_trigger: bool = True         # 觸發時暫停真實交易
    keep_paper_on_trigger: bool = True         # 觸發時保留虛擬交易
    auto_generate_card: bool = False           # 是否自動生成新卡片 (需要 LLM)


@dataclass
class BacktestResult:
    """回測結果"""
    timestamp: str
    period_hours: int
    total_trades: int
    win_rate: float
    total_pnl_pct: float
    best_direction: str                        # LONG / SHORT / BOTH
    recommended_config: Dict[str, Any] = field(default_factory=dict)
    analysis_summary: str = ""


class AutoBacktestModule:
    """自動回測模組"""
    
    def __init__(
        self,
        data_dir: str = "data/backtest",
        config_dir: str = "config/trading_cards/auto_optimized",
        trigger_config: Optional[BacktestTriggerConfig] = None
    ):
        self.data_dir = Path(data_dir)
        self.config_dir = Path(config_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.trigger_config = trigger_config or BacktestTriggerConfig()
        
        # 狀態
        self.current_mode = TradingMode.PAPER
        self.trades: List[TradeRecord] = []
        self.backtest_results: List[BacktestResult] = []
        
        # 統計
        self.session_start = datetime.now()
        self.cumulative_pnl_pct = 0.0
        self.consecutive_losses = 0
        self.last_backtest_time = datetime.now()
        
        # 回調
        self.on_trigger_backtest = None         # 回測觸發回調
        self.on_mode_change = None              # 模式變更回調
        self.on_new_card_ready = None           # 新卡片就緒回調
        
        # LLM 接口 (預留)
        self.llm_client = None
        
        # 載入歷史
        self._load_state()
    
    def _load_state(self):
        """載入狀態"""
        state_file = self.data_dir / "auto_backtest_state.json"
        if state_file.exists():
            try:
                with open(state_file, 'r') as f:
                    state = json.load(f)
                self.cumulative_pnl_pct = state.get('cumulative_pnl_pct', 0.0)
                self.consecutive_losses = state.get('consecutive_losses', 0)
                self.current_mode = TradingMode(state.get('current_mode', 'paper'))
                
                # 載入交易記錄
                for t in state.get('trades', []):
                    self.trades.append(TradeRecord(**t))
                    
            except Exception as e:
                print(f"⚠️ 載入狀態失敗: {e}")
    
    def _save_state(self):
        """保存狀態"""
        state_file = self.data_dir / "auto_backtest_state.json"
        state = {
            'cumulative_pnl_pct': self.cumulative_pnl_pct,
            'consecutive_losses': self.consecutive_losses,
            'current_mode': self.current_mode.value,
            'last_update': datetime.now().isoformat(),
            'trades': [asdict(t) for t in self.trades[-100:]]  # 保留最近 100 筆
        }
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    
    def record_trade(self, trade: TradeRecord) -> Dict[str, Any]:
        """
        記錄交易並檢查是否需要觸發回測
        
        Returns:
            Dict with 'triggered', 'reason', 'action' keys
        """
        self.trades.append(trade)
        
        # 更新統計
        self.cumulative_pnl_pct += trade.pnl_pct
        
        if trade.win:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
        
        # 保存狀態
        self._save_state()
        
        # 檢查觸發條件
        return self._check_triggers()
    
    def _check_triggers(self) -> Dict[str, Any]:
        """檢查是否需要觸發回測"""
        result = {
            'triggered': False,
            'reason': None,
            'action': None,
            'details': {}
        }
        
        # 1. 累計虧損觸發
        if self.cumulative_pnl_pct <= -self.trigger_config.max_cumulative_loss_pct:
            result['triggered'] = True
            result['reason'] = 'cumulative_loss'
            result['details'] = {
                'current_loss': self.cumulative_pnl_pct,
                'threshold': -self.trigger_config.max_cumulative_loss_pct
            }
        
        # 2. 連續虧損觸發
        elif self.consecutive_losses >= self.trigger_config.max_consecutive_losses:
            result['triggered'] = True
            result['reason'] = 'consecutive_losses'
            result['details'] = {
                'consecutive': self.consecutive_losses,
                'threshold': self.trigger_config.max_consecutive_losses
            }
        
        # 3. 勝率觸發
        elif len(self.trades) >= self.trigger_config.min_trades_for_win_rate:
            recent_trades = self.trades[-self.trigger_config.min_trades_for_win_rate:]
            win_rate = sum(1 for t in recent_trades if t.win) / len(recent_trades) * 100
            
            if win_rate < self.trigger_config.min_win_rate_threshold:
                result['triggered'] = True
                result['reason'] = 'low_win_rate'
                result['details'] = {
                    'win_rate': win_rate,
                    'threshold': self.trigger_config.min_win_rate_threshold
                }
        
        # 4. 時間觸發
        hours_since_backtest = (datetime.now() - self.last_backtest_time).total_seconds() / 3600
        if hours_since_backtest >= self.trigger_config.auto_backtest_hours:
            result['triggered'] = True
            result['reason'] = 'scheduled'
            result['details'] = {
                'hours_elapsed': hours_since_backtest,
                'threshold': self.trigger_config.auto_backtest_hours
            }
        
        # 如果觸發，執行動作
        if result['triggered']:
            result['action'] = self._handle_trigger(result['reason'])
        
        return result
    
    def _handle_trigger(self, reason: str) -> str:
        """處理觸發"""
        action_taken = []
        
        # 暫停真實交易
        if self.trigger_config.pause_real_on_trigger and self.current_mode == TradingMode.REAL:
            old_mode = self.current_mode
            self.current_mode = TradingMode.PAUSED
            action_taken.append("paused_real_trading")
            
            if self.on_mode_change:
                self.on_mode_change(old_mode, self.current_mode, reason)
        
        # 保留虛擬交易
        if self.trigger_config.keep_paper_on_trigger:
            action_taken.append("kept_paper_trading")
        
        # 觸發回測回調
        if self.on_trigger_backtest:
            self.on_trigger_backtest(reason, self.get_statistics())
        
        # 記錄
        self._log_trigger(reason, action_taken)
        
        return ", ".join(action_taken)
    
    def _log_trigger(self, reason: str, actions: List[str]):
        """記錄觸發事件"""
        log_file = self.data_dir / "trigger_log.jsonl"
        entry = {
            'timestamp': datetime.now().isoformat(),
            'reason': reason,
            'actions': actions,
            'stats': self.get_statistics()
        }
        with open(log_file, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    
    def get_statistics(self) -> Dict[str, Any]:
        """獲取當前統計"""
        if not self.trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'cumulative_pnl_pct': 0,
                'consecutive_losses': 0
            }
        
        wins = sum(1 for t in self.trades if t.win)
        
        return {
            'total_trades': len(self.trades),
            'win_rate': wins / len(self.trades) * 100 if self.trades else 0,
            'cumulative_pnl_pct': self.cumulative_pnl_pct,
            'consecutive_losses': self.consecutive_losses,
            'avg_pnl_pct': sum(t.pnl_pct for t in self.trades) / len(self.trades),
            'best_trade_pct': max(t.pnl_pct for t in self.trades),
            'worst_trade_pct': min(t.pnl_pct for t in self.trades),
            'long_count': sum(1 for t in self.trades if t.direction == 'LONG'),
            'short_count': sum(1 for t in self.trades if t.direction == 'SHORT'),
            'session_hours': (datetime.now() - self.session_start).total_seconds() / 3600
        }
    
    def run_backtest(self, hours: int = 24) -> BacktestResult:
        """
        執行回測分析
        
        Args:
            hours: 回測時間範圍
            
        Returns:
            BacktestResult
        """
        self.current_mode = TradingMode.BACKTEST
        self.last_backtest_time = datetime.now()
        
        # 分析交易數據
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_trades = [
            t for t in self.trades 
            if datetime.fromisoformat(t.timestamp) > cutoff_time
        ]
        
        if not recent_trades:
            return BacktestResult(
                timestamp=datetime.now().isoformat(),
                period_hours=hours,
                total_trades=0,
                win_rate=0,
                total_pnl_pct=0,
                best_direction="UNKNOWN",
                analysis_summary="No trades in period"
            )
        
        # 分析方向表現
        long_trades = [t for t in recent_trades if t.direction == 'LONG']
        short_trades = [t for t in recent_trades if t.direction == 'SHORT']
        
        long_pnl = sum(t.pnl_pct for t in long_trades) if long_trades else 0
        short_pnl = sum(t.pnl_pct for t in short_trades) if short_trades else 0
        
        long_wr = sum(1 for t in long_trades if t.win) / len(long_trades) * 100 if long_trades else 0
        short_wr = sum(1 for t in short_trades if t.win) / len(short_trades) * 100 if short_trades else 0
        
        # 決定最佳方向
        if long_pnl > short_pnl and long_pnl > 0:
            best_direction = "LONG"
        elif short_pnl > long_pnl and short_pnl > 0:
            best_direction = "SHORT"
        elif long_pnl > short_pnl:
            best_direction = "SHORT"  # 兩邊都虧，選虧少的反向
        else:
            best_direction = "LONG"
        
        # 生成推薦配置
        recommended_config = self._generate_recommended_config(
            best_direction, recent_trades
        )
        
        # 分析摘要
        summary = f"""
回測分析結果 ({hours}h)
========================
總交易: {len(recent_trades)} 筆
總 PnL: {sum(t.pnl_pct for t in recent_trades):.2f}%

LONG 表現:
  - 交易數: {len(long_trades)}
  - 勝率: {long_wr:.1f}%
  - PnL: {long_pnl:.2f}%

SHORT 表現:
  - 交易數: {len(short_trades)}
  - 勝率: {short_wr:.1f}%
  - PnL: {short_pnl:.2f}%

推薦方向: {best_direction}
"""
        
        result = BacktestResult(
            timestamp=datetime.now().isoformat(),
            period_hours=hours,
            total_trades=len(recent_trades),
            win_rate=sum(1 for t in recent_trades if t.win) / len(recent_trades) * 100,
            total_pnl_pct=sum(t.pnl_pct for t in recent_trades),
            best_direction=best_direction,
            recommended_config=recommended_config,
            analysis_summary=summary
        )
        
        self.backtest_results.append(result)
        self._save_backtest_result(result)
        
        # 恢復模式
        if self.trigger_config.keep_paper_on_trigger:
            self.current_mode = TradingMode.PAPER
        
        return result
    
    def _generate_recommended_config(
        self, 
        best_direction: str, 
        trades: List[TradeRecord]
    ) -> Dict[str, Any]:
        """生成推薦配置"""
        
        # 分析最佳 six_dim_score 閾值
        winning_trades = [t for t in trades if t.win]
        if winning_trades:
            avg_winning_score = sum(t.six_dim_score for t in winning_trades) / len(winning_trades)
            recommended_min_score = max(6, int(avg_winning_score - 1))
        else:
            recommended_min_score = 8
        
        # 分析最佳持倉時間
        if winning_trades:
            avg_hold_time = sum(t.hold_time_sec for t in winning_trades) / len(winning_trades)
        else:
            avg_hold_time = 30
        
        return {
            "allowed_directions": [best_direction],
            "six_dim_min_score_to_trade": recommended_min_score,
            "recommended_hold_time_sec": int(avg_hold_time),
            "contextual_mode": True,
            "min_confidence": 0.3,
            "generated_at": datetime.now().isoformat(),
            "based_on_trades": len(trades)
        }
    
    def _save_backtest_result(self, result: BacktestResult):
        """保存回測結果"""
        results_dir = self.data_dir / "backtest_results"
        results_dir.mkdir(exist_ok=True)
        
        filename = f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(results_dir / filename, 'w') as f:
            json.dump(asdict(result), f, indent=2, ensure_ascii=False)
    
    def generate_new_card(
        self, 
        backtest_result: BacktestResult,
        card_name: str = None
    ) -> str:
        """
        根據回測結果生成新交易卡片
        
        Args:
            backtest_result: 回測結果
            card_name: 卡片名稱 (可選)
            
        Returns:
            新卡片路徑
        """
        if not card_name:
            card_name = f"auto_generated_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # 基於推薦配置生成卡片
        config = backtest_result.recommended_config
        
        card = {
            "name": card_name,
            "description": f"Auto-generated based on {backtest_result.period_hours}h backtest",
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            
            # 方向設定
            "allowed_directions": config.get("allowed_directions", ["SHORT"]),
            
            # 信號設定
            "contextual_mode": config.get("contextual_mode", True),
            "six_dim_min_score_to_trade": config.get("six_dim_min_score_to_trade", 7),
            "min_confidence": config.get("min_confidence", 0.3),
            
            # 風控設定
            "stop_loss_pct": 0.5,
            "take_profit_pct": 1.0,
            "max_hold_time_sec": config.get("recommended_hold_time_sec", 60) * 2,
            
            # 資金設定
            "position_size_btc": 0.001,
            "max_daily_trades": 50,
            
            # 元數據
            "metadata": {
                "generated_by": "auto_backtest_module",
                "based_on_trades": config.get("based_on_trades", 0),
                "source_backtest": backtest_result.timestamp,
                "expected_win_rate": backtest_result.win_rate
            }
        }
        
        # 保存卡片
        card_path = self.config_dir / f"{card_name}.json"
        with open(card_path, 'w') as f:
            json.dump(card, f, indent=2, ensure_ascii=False)
        
        print(f"✅ 新卡片已生成: {card_path}")
        
        # 回調
        if self.on_new_card_ready:
            self.on_new_card_ready(str(card_path), card)
        
        return str(card_path)
    
    def reset_statistics(self):
        """重置統計"""
        self.cumulative_pnl_pct = 0.0
        self.consecutive_losses = 0
        self.trades.clear()
        self.session_start = datetime.now()
        self._save_state()
        print("📊 統計已重置")
    
    def set_mode(self, mode: TradingMode):
        """設定交易模式"""
        old_mode = self.current_mode
        self.current_mode = mode
        
        if self.on_mode_change:
            self.on_mode_change(old_mode, mode, "manual")
        
        self._save_state()
        print(f"🔄 交易模式: {old_mode.value} → {mode.value}")
    
    # ============ LLM 接口 (預留) ============
    
    def set_llm_client(self, client):
        """設定 LLM 客戶端"""
        self.llm_client = client
        self.trigger_config.auto_generate_card = True
        print("🤖 LLM 客戶端已連接")
    
    async def generate_card_with_llm(
        self, 
        backtest_result: BacktestResult,
        market_context: Optional[Dict] = None
    ) -> Optional[str]:
        """
        使用 LLM 生成優化的交易卡片
        
        Args:
            backtest_result: 回測結果
            market_context: 市場上下文 (可選)
            
        Returns:
            新卡片路徑
        """
        if not self.llm_client:
            print("⚠️ LLM 客戶端未設定")
            return None
        
        # 準備 prompt
        prompt = self._build_llm_prompt(backtest_result, market_context)
        
        try:
            # 調用 LLM
            response = await self.llm_client.generate(prompt)
            
            # 解析 JSON
            card_config = self._parse_llm_response(response)
            
            if card_config:
                # 驗證配置
                if self._validate_card_config(card_config):
                    # 保存卡片
                    card_name = f"llm_generated_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    card_path = self.config_dir / f"{card_name}.json"
                    
                    with open(card_path, 'w') as f:
                        json.dump(card_config, f, indent=2, ensure_ascii=False)
                    
                    print(f"🤖 LLM 生成卡片: {card_path}")
                    return str(card_path)
            
        except Exception as e:
            print(f"❌ LLM 生成失敗: {e}")
        
        return None
    
    def _build_llm_prompt(
        self, 
        backtest_result: BacktestResult,
        market_context: Optional[Dict]
    ) -> str:
        """構建 LLM prompt"""
        return f"""
你是一個專業的加密貨幣交易策略優化器。

根據以下回測結果，生成一個優化的交易卡片配置：

## 回測結果 ({backtest_result.period_hours}小時)
- 總交易數: {backtest_result.total_trades}
- 勝率: {backtest_result.win_rate:.1f}%
- 總 PnL: {backtest_result.total_pnl_pct:.2f}%
- 最佳方向: {backtest_result.best_direction}

## 分析摘要
{backtest_result.analysis_summary}

## 市場上下文
{json.dumps(market_context, indent=2) if market_context else "無"}

請生成一個 JSON 格式的交易卡片配置，包含：
1. allowed_directions - 允許的交易方向
2. six_dim_min_score_to_trade - 最低六維分數
3. stop_loss_pct - 止損百分比
4. take_profit_pct - 止盈百分比
5. min_confidence - 最低信心度

只返回 JSON，不要其他文字。
"""
    
    def _parse_llm_response(self, response: str) -> Optional[Dict]:
        """解析 LLM 回應"""
        try:
            # 嘗試直接解析
            return json.loads(response)
        except:
            # 嘗試提取 JSON 塊
            import re
            match = re.search(r'\{[\s\S]*\}', response)
            if match:
                try:
                    return json.loads(match.group())
                except:
                    pass
        return None
    
    def _validate_card_config(self, config: Dict) -> bool:
        """驗證卡片配置"""
        required_fields = ['allowed_directions']
        
        for field in required_fields:
            if field not in config:
                print(f"⚠️ 缺少必要欄位: {field}")
                return False
        
        # 驗證方向
        if config.get('allowed_directions'):
            for d in config['allowed_directions']:
                if d not in ['LONG', 'SHORT']:
                    print(f"⚠️ 無效方向: {d}")
                    return False
        
        return True
    
    def get_status_display(self) -> str:
        """獲取狀態顯示字串"""
        stats = self.get_statistics()
        
        mode_emoji = {
            TradingMode.REAL: "🔴",
            TradingMode.PAPER: "📝",
            TradingMode.PAUSED: "⏸️",
            TradingMode.BACKTEST: "🔍"
        }
        
        return f"""
╔══════════════════════════════════════╗
║     自動回測模組狀態                 ║
╠══════════════════════════════════════╣
║ 模式: {mode_emoji.get(self.current_mode, '❓')} {self.current_mode.value.upper():<10}              ║
║ 累計 PnL: {stats['cumulative_pnl_pct']:>+8.2f}%                ║
║ 連續虧損: {self.consecutive_losses:>3} / {self.trigger_config.max_consecutive_losses}                  ║
║ 勝率: {stats['win_rate']:>6.1f}% ({stats['total_trades']} 筆)           ║
║ 觸發閾值: -{self.trigger_config.max_cumulative_loss_pct}% / {self.trigger_config.max_consecutive_losses} 連虧    ║
╚══════════════════════════════════════╝
"""


# ============ 整合用的便利函數 ============

def create_auto_backtest_module(
    loss_threshold: float = 25.0,
    consecutive_loss_limit: int = 5,
    auto_backtest_hours: int = 24
) -> AutoBacktestModule:
    """
    創建自動回測模組的便利函數
    
    Args:
        loss_threshold: 累計虧損觸發閾值 (%)
        consecutive_loss_limit: 連續虧損觸發次數
        auto_backtest_hours: 自動回測間隔 (小時)
    """
    config = BacktestTriggerConfig(
        max_cumulative_loss_pct=loss_threshold,
        max_consecutive_losses=consecutive_loss_limit,
        auto_backtest_hours=auto_backtest_hours
    )
    
    return AutoBacktestModule(trigger_config=config)


# ============ 測試 ============

if __name__ == "__main__":
    # 創建模組
    module = create_auto_backtest_module(
        loss_threshold=25.0,
        consecutive_loss_limit=5
    )
    
    print(module.get_status_display())
    
    # 模擬交易
    print("\n模擬交易...")
    
    # 模擬幾筆虧損交易
    for i in range(3):
        trade = TradeRecord(
            timestamp=datetime.now().isoformat(),
            direction="SHORT",
            entry_price=91000,
            exit_price=91100,
            pnl_pct=-0.11,
            pnl_usdt=-1.0,
            size_btc=0.001,
            hold_time_sec=30,
            six_dim_score=8,
            win=False
        )
        
        result = module.record_trade(trade)
        print(f"交易 {i+1}: PnL = {trade.pnl_pct}%, 觸發 = {result['triggered']}")
    
    print(module.get_status_display())
    
    # 執行回測
    print("\n執行回測...")
    backtest_result = module.run_backtest(hours=24)
    print(backtest_result.analysis_summary)
    
    # 生成新卡片
    print("\n生成新卡片...")
    card_path = module.generate_new_card(backtest_result, "test_auto_card")
    print(f"卡片路徑: {card_path}")
