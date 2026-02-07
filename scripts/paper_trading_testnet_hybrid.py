#!/usr/bin/env python3
"""
🚀 Paper Trading + Testnet 混合模式
讓 Paper Trading 的 M🐺 決策同時驅動 Testnet 真實交易

使用方式:
python3 scripts/paper_trading_testnet_hybrid.py 8  # 運行 8 小時
"""

import sys
import os

# 確保 scripts 目錄在 path 中
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.insert(0, project_root)
sys.path.insert(0, script_dir)

import asyncio
import time
import json
import threading
import io
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# 導入 Paper Trading 系統
from scripts.paper_trading_hybrid_full import HybridPaperTradingSystem, TradingMode

# 導入 Testnet 執行器
from scripts.testnet_executor import (
    BinanceTestnetExecutor, 
    PaperToTestnetBridge,
    STRATEGY_NAME_MAP,
    STRATEGY_CONFIG
)

# 🆕 導入 WebSocket 整合模組
try:
    from scripts.testnet_websocket_integration import WebSocketIntegration
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("⚠️ WebSocket 模組不可用，將使用輪詢模式")

# 🆕 統一配置檔案路徑
SYNC_CONFIG_FILE = Path(__file__).parent.parent / 'config' / 'strategy_sync_config.json'


# ═══════════════════════════════════════════════════════════════════════════════
# 🆕 終端機輸出日誌記錄器
# ═══════════════════════════════════════════════════════════════════════════════

class TeeLogger:
    """
    同時輸出到終端機和日誌檔案的記錄器
    每 30 秒自動 flush 到檔案，避免效能問題
    """
    def __init__(self, log_dir="logs/trading_terminal", flush_interval=30):
        self.terminal = sys.stdout
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 建立日誌檔案 (按日期命名)
        self.log_file_path = self.log_dir / f"trading_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self.log_file = open(self.log_file_path, 'w', encoding='utf-8')
        
        # 緩衝區
        self.buffer = []
        self.buffer_lock = threading.Lock()
        self.flush_interval = flush_interval
        self.last_flush_time = time.time()
        
        print(f"📝 終端機輸出將記錄到: {self.log_file_path}")
    
    def write(self, message):
        self.terminal.write(message)
        
        # 添加到緩衝區
        with self.buffer_lock:
            if message.strip():  # 只記錄非空內容
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.buffer.append(f"[{timestamp}] {message}")
        
        # 檢查是否需要 flush
        if time.time() - self.last_flush_time >= self.flush_interval:
            self.flush()
    
    def flush(self):
        self.terminal.flush()
        
        # 寫入緩衝區內容到檔案
        with self.buffer_lock:
            if self.buffer:
                try:
                    for line in self.buffer:
                        self.log_file.write(line)
                        if not line.endswith('\n'):
                            self.log_file.write('\n')
                    self.log_file.flush()
                    self.buffer.clear()
                except Exception as e:
                    self.terminal.write(f"⚠️ 日誌寫入失敗: {e}\n")
        
        self.last_flush_time = time.time()
    
    def close(self):
        self.flush()
        self.log_file.close()
        print(f"\n📝 日誌已儲存到: {self.log_file_path}")

# 全域日誌記錄器
_tee_logger = None

def setup_terminal_logging():
    """啟用終端機輸出日誌記錄"""
    global _tee_logger
    if _tee_logger is None:
        _tee_logger = TeeLogger()
        sys.stdout = _tee_logger
    return _tee_logger

def close_terminal_logging():
    """關閉終端機輸出日誌記錄"""
    global _tee_logger
    if _tee_logger is not None:
        sys.stdout = _tee_logger.terminal
        _tee_logger.close()
        _tee_logger = None


class TestnetHybridSystem:
    """Paper Trading + Testnet 混合交易系統"""
    
    def __init__(self, test_duration_hours: float = 8.0, enable_testnet: bool = True, initial_capital: float = 100.0):
        """
        初始化混合系統
        
        Args:
            test_duration_hours: 測試時長 (小時)
            enable_testnet: 是否啟用 Testnet 真實交易
            initial_capital: 每個策略的啟動資金 (USDT)
        """
        self.test_duration_hours = test_duration_hours
        self.enable_testnet = enable_testnet
        self.initial_capital = initial_capital
        
        # 🆕 載入統一配置
        self.sync_config = self._load_sync_config()
        
        # 🔧 從統一配置取得設定
        global_settings = self.sync_config.get('global_settings', {})
        strategies_config = self.sync_config.get('strategies', {})
        
        # 🆕 v1.3.0: 統一模式 (M🐺 = T🐺)
        self.unified_mode = global_settings.get('unified_mode', False)
        self.ai_data_source = global_settings.get('ai_data_source', 'PAPER')  # TESTNET_ONLY / PAPER / BOTH
        
        if self.unified_mode:
            print("=" * 60)
            print("🔗 【統一模式啟用】 M🐺 = T🐺")
            print("   - AI 數據來源: Testnet 真實交易")
            print("   - Paper Trading 狀態跟隨 Testnet")
            print("   - 所有決策基於真實執行結果")
            print("=" * 60)
        
        # 🔧 Testnet 只追蹤啟用的策略
        self.strategy_priority = self.sync_config.get('testnet_priority', ['M🐺'])
        
        # 🆕 策略 -> Bridge 檔案對應 (從統一配置讀取)
        self.strategy_bridge_map = {
            key: cfg.get('bridge_file')
            for key, cfg in strategies_config.items()
        }
        
        # 🔧 從統一配置決定哪些策略同步到 Testnet
        self.tracked_modes = {}
        mode_mapping = {
            'M🐺': TradingMode.M_AI_WHALE_HUNTER,
        }
        for key, cfg in strategies_config.items():
            if cfg.get('testnet_enabled', False):
                mode = mode_mapping.get(key)
                if mode:
                    self.tracked_modes[mode] = key
        
        # 上次的持倉狀態 (用於檢測變化)
        self.last_position_states: Dict[TradingMode, Dict] = {}
        
        # 🔧 從統一配置讀取全域設定
        self.slippage_tolerance_bps = global_settings.get('slippage_tolerance_bps', 30)
        self.enable_slippage_check = global_settings.get('enable_slippage_check', True)
        self.last_trade_time: Dict[str, float] = {}
        
        # 統計
        self.testnet_trades = 0
        # 🔧 v3.0: 優化更新頻率以匹配 AI 5秒判斷
        self.testnet_sync_interval = 3   # 🔧 每 3 秒同步 Testnet 倉位 (原 5 秒)
        self.last_sync_time = 0
        self.last_bridge_update_time = 0
        self.bridge_update_interval = 2  # 每 2 秒更新 Bridge 給 AI
        self.slippage_rejections = 0  # 因滑價拒絕的次數
        
        # 🆕 同步鎖定機制 - 防止 Paper/Testnet 不同步
        self.testnet_sync_lock = False  # 當 Testnet 正在執行操作時鎖定
        self.testnet_sync_lock_time = 0  # 鎖定時間 (用於超時解鎖)
        self.testnet_sync_lock_timeout = 10  # 最多鎖定 10 秒
        
        # 🆕 當前 Testnet 主控策略
        self.active_testnet_strategy = None
        
        # 🆕 v3.1: AI 資訊對齊機制
        self.ai_sync_enabled = True
        self.last_ai_state = None
        
        # 印出載入的配置
        print(f"📋 載入統一配置: {SYNC_CONFIG_FILE.name}")
        print(f"   滑價容忍度: {self.slippage_tolerance_bps} bps")
        print(f"   同步到 Testnet: {list(self.tracked_modes.values())}")
        
        # 初始化 Paper Trading 系統
        print("📊 初始化 Paper Trading 系統...")
        self.paper_system = HybridPaperTradingSystem(
            initial_capital=initial_capital,
            max_position_pct=0.5,
            test_duration_hours=test_duration_hours
        )
        
        # 初始化 Testnet 執行器
        if enable_testnet:
            print("🌐 初始化 Testnet 執行器...")
            try:
                self.testnet_executor = BinanceTestnetExecutor()
                self.testnet_bridge = PaperToTestnetBridge(self.testnet_executor)
                print("✅ Testnet 連接成功!")
                
                # 🆕 重置 Portfolio 並設定啟動資金
                self._reset_portfolio_fresh_start()
                
                # 🆕 啟動時先查看 Testnet 真實持倉狀態
                self._startup_sync_testnet_positions()
                
                # 🆕 v3.1: 啟動時對齊 AI 資訊
                self._startup_sync_ai_state()
                
                # 🆕 初始化 WebSocket 整合 (即時止盈止損)
                self._init_websocket_integration()
                
            except Exception as e:
                print(f"⚠️ Testnet 初始化失敗: {e}")
                import traceback
                traceback.print_exc()
                print("   將僅運行 Paper Trading 模式")
                self.enable_testnet = False
                self.testnet_executor = None
                self.testnet_bridge = None
        
        # WebSocket 整合器
        self.ws_integration = None
    
    def _init_websocket_integration(self):
        """
        🆕 初始化 WebSocket 整合 - 即時止盈止損
        
        優勢:
        - 止盈止損響應: 10秒輪詢 → <1秒 WebSocket
        - 減少滑點: ~0.3-0.5% per trade
        - 即時同步: 偵測手動平倉立即更新 Paper Trading
        """
        if not WEBSOCKET_AVAILABLE:
            print("⚠️ WebSocket 模組不可用")
            return
        
        try:
            self.ws_integration = WebSocketIntegration(hybrid_system=self)
            
            # 設定回調
            self.ws_integration.on_instant_exit = self._handle_ws_instant_exit
            self.ws_integration.on_manual_close_detected = self._handle_ws_manual_close
            self.ws_integration.on_liquidation_warning = self._handle_ws_liquidation_warning
            
            # 啟動 WebSocket (非阻塞)
            self.ws_integration.start()
            
        except Exception as e:
            print(f"⚠️ WebSocket 初始化失敗: {e}")
            self.ws_integration = None
    
    def _handle_ws_instant_exit(self, data: dict):
        """
        🆕 處理 WebSocket 即時出場信號
        
        當 WebSocket 偵測到達到止盈/止損時，立即執行平倉
        """
        reason = data.get('reason', 'Unknown')
        pnl_usdt = data.get('pnl_usdt', 0)
        pnl_pct = data.get('pnl_pct', 0)
        force_taker = data.get('force_taker', False)  # 🆕 極端獲利強制用 Taker
        
        print(f"\n⚡ WebSocket 即時出場觸發!")
        print(f"   原因: {reason}")
        print(f"   PnL: ${pnl_usdt:.2f} ({pnl_pct:.2f}%)")
        if force_taker:
            print(f"   🚀 強制 Taker 市價單 (確保立即成交)")
        
        # 執行 Testnet 平倉
        if self.active_testnet_strategy and self.testnet_bridge:
            strategy_key = self.active_testnet_strategy
            
            # 找到對應的 TradingMode
            mode_mapping = {
                'M🐺': TradingMode.M_AI_WHALE_HUNTER,
                # 🔧 Testnet 只同步 M🐺
            }
            mode = mode_mapping.get(strategy_key)
            
            if mode:
                # 🆕 極端獲利強制用 Taker，不等 Maker
                use_maker = False if force_taker else None
                
                # 執行 Testnet 平倉
                result = self.testnet_bridge.process_signal(
                    strategy_name=mode.name,
                    direction='CLOSE',
                    reason=f'WebSocket 即時出場: {reason}',
                    use_maker=use_maker  # 🆕 傳遞 force_taker
                )
                
                if result:
                    print(f"✅ Testnet 已平倉: {result}")
                    self.testnet_trades += 1
                    
                    # 同步 Paper Trading
                    self._sync_paper_from_testnet_close(strategy_key, pnl_usdt, reason)
                    
                    # 清除主控策略
                    self.active_testnet_strategy = None
                    
                    # 🔥 通知 WebSocket 平倉完成，重置監控狀態
                    if self.ws_integration:
                        self.ws_integration.notify_exit_complete()
                else:
                    # 平倉失敗也要重置狀態，避免卡死
                    if self.ws_integration:
                        self.ws_integration.exit_in_progress = False
    
    def _handle_ws_manual_close(self, data: dict):
        """
        🆕 處理 WebSocket 偵測到的手動平倉
        
        同步 Paper Trading 狀態
        """
        reason = data.get('reason', 'UNKNOWN')
        position_side = data.get('position_side', '')
        
        print(f"\n📡 WebSocket 偵測到外部平倉!")
        print(f"   原因: {reason}")
        
        if self.active_testnet_strategy:
            strategy_key = self.active_testnet_strategy
            
            # 同步 Paper Trading
            self._sync_paper_from_testnet_close(strategy_key, 0, f'外部平倉: {reason}')
            
            # 清除主控策略
            self.active_testnet_strategy = None
    
    def _handle_ws_liquidation_warning(self, data: dict):
        """
        🆕 處理爆倉預警
        """
        pnl_pct = data.get('pnl_pct', 0)
        print(f"\n💀 爆倉預警! ROI: {pnl_pct:.2f}%")
        print(f"   建議立即檢查倉位或手動平倉")
    
    def _sync_paper_from_testnet_close(self, strategy_key: str, pnl_usdt: float, reason: str):
        """
        🆕 從 Testnet 平倉同步到 Paper Trading
        
        當 Testnet 先平倉時 (WebSocket 即時出場或手動平倉)，
        更新 Paper Trading 的倉位狀態
        """
        mode_mapping = {
            'M🐺': TradingMode.M_AI_WHALE_HUNTER,
        }
        mode = mode_mapping.get(strategy_key)
        
        if not mode:
            return
        
        print(f"   🔄 同步 Paper Trading: {strategy_key}")
        
        try:
            # 檢查 Paper Trading 是否有倉位 (使用 orders，不是 positions)
            open_orders = [
                o for o in self.paper_system.orders.get(mode, [])
                if not o.is_blocked and o.exit_time is None
            ]
            
            if open_orders:
                # Paper Trading 也有倉位，需要平倉
                order = open_orders[0]
                entry_price = order.entry_price
                direction = order.direction
                
                # 獲取當前價格
                current_price = self.testnet_executor.get_current_price() if self.testnet_executor else entry_price
                
                # 計算 Paper Trading 的 PnL
                if direction == 'LONG':
                    paper_pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
                else:
                    paper_pnl_pct = (entry_price - current_price) / entry_price if entry_price > 0 else 0
                
                # 執行 Paper Trading 平倉 (標記 exit_time)
                order.exit_price = current_price
                order.exit_time = datetime.now()
                order.exit_reason = reason
                
                print(f"   ✅ Paper Trading 已同步平倉")
                print(f"   Paper PnL: {paper_pnl_pct*100:.2f}%")
            else:
                print(f"   ℹ️ Paper Trading 無持倉，跳過同步")
        except Exception as e:
            print(f"   ⚠️ 同步 Paper Trading 失敗: {e}")
        
        # 更新 last_position_states
        self.last_position_states[mode] = {'has_position': False}
    
    def _load_sync_config(self) -> dict:
        """
        🆕 載入統一同步配置檔案
        從 config/strategy_sync_config.json 讀取所有策略設定
        """
        import json
        from pathlib import Path
        
        config_path = Path(SYNC_CONFIG_FILE)
        
        # 預設配置（如果檔案不存在）
        default_config = {
            "version": "1.0",
            "global_settings": {
                "slippage_tolerance_bps": 30,
                "sync_mode": "shadow",
                "rollback_on_fail": True
            },
            "testnet_priority": ["M🐺"],
            "strategies": {
                "M🐺": {
                    "name": "AI Wolf Hunter",
                    "bridge_file": "ai_wolf_bridge.json",
                    "ai_advisor": "ai_wolf_strategy.json",
                    "leverage": 10,
                    "tp_pct": 0.02,
                    "sl_pct": 0.015,
                    "testnet_enabled": True,
                    "paper_enabled": True
                }
            }
        }
        
        try:
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                print(f"✅ 載入統一配置: {config_path}")
                return config
            else:
                print(f"⚠️ 統一配置檔案不存在: {config_path}")
                print("   使用預設配置")
                return default_config
        except Exception as e:
            print(f"❌ 載入配置失敗: {e}")
            return default_config
    
    def _check_entry_protection(self, strategy_key: str, current_state: Dict, is_sync_mode: bool = False) -> Dict:
        """
        🛡️ 三重開倉保護機制
        
        1. 連續虧損冷卻：連續虧損 N 次後暫停 X 秒
        2. 鯨魚方向確認：當 AI 與鯨魚方向相反且鯨魚支配性 > 0.7 時阻止開倉
        3. 主力洗盤保護：ATR 極低 + 快速虧損歷史 = 可能被洗盤
        
        Args:
            strategy_key: 策略代碼
            current_state: 當前狀態
            is_sync_mode: 是否為同步 Paper Trading 模式
                         如果是同步模式，跳過 whale dominance 檢查（Paper 已檢查過）
        
        Returns:
            {'allow': bool, 'reason': str}
        """
        import json
        from pathlib import Path
        from datetime import datetime
        
        # 🆕 v2.2: 同步模式跳過大部分保護檢查
        # 原因: Paper Trading 開倉時已經做過保護檢查，Testnet 只需要跟隨
        # 避免因為時序差（數據更新）導致 Testnet 無法跟上
        if is_sync_mode:
            print(f"   🔄 [同步模式] 跳過保護檢查，直接跟隨 Paper Trading")
            return {'allow': True, 'reason': '同步模式 - Paper Trading 已通過檢查'}
        
        direction = current_state.get('direction', '').upper()
        
        # 讀取 Bridge 數據
        bridge_path = Path('ai_wolf_bridge.json')
        if not bridge_path.exists():
            return {'allow': True, 'reason': 'Bridge 不存在，允許交易'}
        
        try:
            with open(bridge_path, 'r', encoding='utf-8') as f:
                bridge = json.load(f)
        except:
            return {'allow': True, 'reason': 'Bridge 讀取失敗，允許交易'}
        
        wolf_data = bridge.get('wolf_to_ai', {})
        feedback = bridge.get('feedback_loop', {})
        
        # ═══════════════════════════════════════════════════════════
        # 🔴 保護 0: 洗盤模式偵測 (最高優先級)
        # ═══════════════════════════════════════════════════════════
        ai_cmd = bridge.get('ai_to_wolf', {})
        whale_strategy = ai_cmd.get('whale_strategy', {})
        whale_intent = whale_strategy.get('intent', '').upper()
        detected_patterns = whale_strategy.get('detected_patterns', [])
        
        # 洗盤模式關鍵字
        SHAKEOUT_PATTERNS = ['SHAKEOUT', 'WASHOUT', 'STOP_HUNT', 'TRAP']
        is_shakeout = (
            whale_intent in SHAKEOUT_PATTERNS or
            any(p.upper() in SHAKEOUT_PATTERNS for p in detected_patterns)
        )
        
        if is_shakeout:
            # 🚨 洗盤模式：需要更長的觀望時間
            SHAKEOUT_COOLDOWN = 300  # 洗盤模式觀望 5 分鐘
            last_trade = feedback.get('last_trade_result', {})
            last_exit_time = last_trade.get('exit_time')
            
            if last_exit_time:
                try:
                    last_time = datetime.fromisoformat(last_exit_time)
                    elapsed = (datetime.now() - last_time).total_seconds()
                    if elapsed < SHAKEOUT_COOLDOWN:
                        remaining = int(SHAKEOUT_COOLDOWN - elapsed)
                        return {
                            'allow': False,
                            'reason': f'🚨 洗盤模式偵測 ({whale_intent}) - 觀望中 (剩餘 {remaining}s)'
                        }
                except:
                    pass
        
        # ═══════════════════════════════════════════════════════════
        # 🔴 保護 1: 連續虧損冷卻期 (動態調整)
        # ═══════════════════════════════════════════════════════════
        failure_streak = feedback.get('failure_streak', 0)
        last_trade = feedback.get('last_trade_result', {})
        avg_holding = feedback.get('avg_holding_time', 0)
        
        # 🆕 動態冷卻時間：根據連虧次數和平均持倉時間調整
        if failure_streak >= 2:
            # 基礎冷卻 + 每次連虧增加 60 秒
            base_cooldown = 120
            extra_cooldown = (failure_streak - 2) * 60
            
            # 如果平均持倉時間過短 (<60s)，額外增加冷卻
            if avg_holding > 0 and avg_holding < 60:
                extra_cooldown += 120  # 被快速洗出，額外等 2 分鐘
            
            LOSS_COOLDOWN_SECONDS = min(base_cooldown + extra_cooldown, 600)  # 最多 10 分鐘
            
            last_exit_time = last_trade.get('exit_time')
            if last_exit_time:
                try:
                    last_time = datetime.fromisoformat(last_exit_time)
                    elapsed = (datetime.now() - last_time).total_seconds()
                    if elapsed < LOSS_COOLDOWN_SECONDS:
                        remaining = int(LOSS_COOLDOWN_SECONDS - elapsed)
                        return {
                            'allow': False,
                            'reason': f'連續虧損 {failure_streak} 次 (平均持倉 {avg_holding:.0f}s)，冷卻中 (剩餘 {remaining}s)'
                        }
                except:
                    pass
        
        # ═══════════════════════════════════════════════════════════
        # 🔴 保護 2: 鯨魚方向確認 (高支配性時必須一致)
        # ═══════════════════════════════════════════════════════════
        WHALE_DOMINANCE_THRESHOLD = 0.7  # 鯨魚支配性 > 70% 時必須一致
        
        whale_status = wolf_data.get('whale_status', {})
        whale_direction = whale_status.get('current_direction', '').upper()
        whale_dominance = whale_status.get('dominance', 0)
        whale_net_btc = whale_status.get('net_qty_btc', 0)
        
        # 🆕 檢查: 鯨魚數據不可用時禁止進場
        # whale_dominance = 0 表示系統剛啟動或數據斷線，無法判斷市場方向
        if whale_dominance == 0 or whale_dominance is None:
            return {
                'allow': False,
                'reason': f'🛡️ 鯨魚數據不可用 (dominance=0) - 系統需要收集數據，請稍候'
            }
        
        # 方向映射
        whale_signal = 'LONG' if whale_direction == 'LONG' else 'SHORT' if whale_direction == 'SHORT' else ''
        
        if whale_dominance >= WHALE_DOMINANCE_THRESHOLD and whale_signal:
            if direction != whale_signal:
                return {
                    'allow': False,
                    'reason': f'AI={direction} vs 鯨魚={whale_signal} (Dom={whale_dominance:.0%}, Net={whale_net_btc:.1f} BTC) → 方向衝突'
                }
        
        # ═══════════════════════════════════════════════════════════
        # 🔴 保護 3: 主力洗盤保護 (死水盤 + 快速連續虧損)
        # ═══════════════════════════════════════════════════════════
        ATR_DEAD_MARKET_THRESHOLD = 0.03  # ATR < 0.03% = 死水盤
        
        volatility = wolf_data.get('volatility', {})
        atr_pct = volatility.get('atr_pct', 0)
        
        avg_holding = feedback.get('avg_holding_time', 0)
        
        if atr_pct < ATR_DEAD_MARKET_THRESHOLD and failure_streak >= 2 and avg_holding < 60:
            return {
                'allow': False,
                'reason': f'疑似主力洗盤 (ATR={atr_pct:.4f}%, 連虧={failure_streak}, 平均持倉={avg_holding:.0f}s)'
            }
        
        # ═══════════════════════════════════════════════════════════
        # ✅ 通過所有保護，允許開倉
        # ═══════════════════════════════════════════════════════════
        return {'allow': True, 'reason': '通過保護檢查'}

    def _check_ai_wants_reverse(self, strategy_key: str, current_direction: str) -> str:
        """
        🆕 檢查 AI 是否想要反向開倉 (需多重證據支持)
        
        短時間反向開倉需要更強證據支持，避免被主力洗盤！
        
        需要滿足的條件 (至少 3/5):
        1. AI 發出明確反向指令
        2. AI 信心度 >= 70%
        3. 鯨魚方向一致 (Dom >= 60%)
        4. OBI 支持 (>0.6 做多, <-0.6 做空)
        5. 持倉時間 >= 120 秒 (避免秒級翻倉)
        
        Args:
            strategy_key: 策略鍵值
            current_direction: 當前持倉方向 (LONG/SHORT)
            
        Returns:
            反向方向 + 證據數量，或空字串表示證據不足
        """
        import json
        from pathlib import Path
        from datetime import datetime
        
        # 根據策略取得對應的 Bridge 檔案
        bridge_map = {
            'M🐺': 'ai_wolf_bridge.json',
            # 🔧 Testnet 只同步 M🐺
        }
        bridge_file = bridge_map.get(strategy_key, 'ai_wolf_bridge.json')
        bridge_path = Path(bridge_file)
        
        if not bridge_path.exists():
            return ''
        
        try:
            with open(bridge_path, 'r', encoding='utf-8') as f:
                bridge = json.load(f)
            
            # 取得數據
            ai_key_map = {
                'M🐺': 'ai_to_wolf',
                # 🔧 Testnet 只同步 M🐺
            }
            ai_key = ai_key_map.get(strategy_key, 'ai_to_wolf')
            ai_cmd = bridge.get(ai_key, {})
            wolf_data = bridge.get('wolf_to_ai', {})
            
            ai_command = ai_cmd.get('command', '').upper()
            ai_confidence = ai_cmd.get('confidence', 0)
            
            whale_status = wolf_data.get('whale_status', {})
            whale_direction = whale_status.get('current_direction', '').upper()
            whale_dominance = whale_status.get('dominance', 0)
            
            micro = wolf_data.get('market_microstructure', {})
            obi = micro.get('obi', 0)
            
            current_upper = current_direction.upper() if current_direction else ''
            
            # 判斷目標反向方向
            target_direction = ''
            if current_upper == 'LONG' and ai_command in ['SHORT', 'OPEN_SHORT']:
                target_direction = 'SHORT'
            elif current_upper == 'SHORT' and ai_command in ['LONG', 'OPEN_LONG']:
                target_direction = 'LONG'
            
            if not target_direction:
                return ''  # AI 沒有發出反向指令
            
            # ═══════════════════════════════════════════════════════════
            # 🔍 收集證據 (需要至少 3/5)
            # ═══════════════════════════════════════════════════════════
            evidence = []
            evidence_details = []
            
            # 證據 1: AI 明確反向指令 (必要條件)
            evidence.append(True)
            evidence_details.append(f"AI={ai_command}")
            
            # 證據 2: AI 高信心度 (>= 70%)
            if ai_confidence >= 70:
                evidence.append(True)
                evidence_details.append(f"信心={ai_confidence}%")
            else:
                evidence.append(False)
            
            # 證據 3: 鯨魚方向一致 (Dom >= 60%)
            whale_aligned = (
                (target_direction == 'LONG' and whale_direction == 'LONG' and whale_dominance >= 0.6) or
                (target_direction == 'SHORT' and whale_direction == 'SHORT' and whale_dominance >= 0.6)
            )
            if whale_aligned:
                evidence.append(True)
                evidence_details.append(f"鯨魚={whale_direction}({whale_dominance:.0%})")
            else:
                evidence.append(False)
            
            # 證據 4: OBI 支持
            obi_supports = (
                (target_direction == 'LONG' and obi > 0.6) or
                (target_direction == 'SHORT' and obi < -0.6)
            )
            if obi_supports:
                evidence.append(True)
                evidence_details.append(f"OBI={obi:.2f}")
            else:
                evidence.append(False)
            
            # 證據 5: 持倉時間 >= 120 秒
            holding_seconds = 0
            testnet_pos = wolf_data.get('testnet_trading', {}).get('position', {})
            if testnet_pos.get('entry_time'):
                try:
                    entry_time = datetime.fromisoformat(testnet_pos['entry_time'])
                    holding_seconds = (datetime.now() - entry_time).total_seconds()
                except:
                    pass
            
            if holding_seconds >= 120:
                evidence.append(True)
                evidence_details.append(f"持倉={holding_seconds:.0f}s")
            else:
                evidence.append(False)
            
            # ═══════════════════════════════════════════════════════════
            # 🎯 需要至少 3/5 證據
            # ═══════════════════════════════════════════════════════════
            evidence_count = sum(evidence)
            MIN_EVIDENCE_REQUIRED = 3
            
            if evidence_count >= MIN_EVIDENCE_REQUIRED:
                detail_str = ", ".join(evidence_details)
                print(f"   ✅ 反向證據充足 ({evidence_count}/5): {detail_str}")
                return f'{target_direction} ({evidence_count}/5 證據)'
            else:
                print(f"   ❌ 反向證據不足 ({evidence_count}/5 < {MIN_EVIDENCE_REQUIRED}): 需要更多確認")
                return ''
            
        except Exception as e:
            print(f"   ⚠️ 檢查 AI 反向信號失敗: {e}")
            return ''

    def _check_whale_wants_reverse(self, current_direction: str) -> str:
        """
        🆕 檢查鯨魚方向是否與當前持倉相反 (需多重證據支持)
        
        不只看鯨魚支配性，還要看其他指標確認。
        
        需要滿足的條件 (至少 3/4):
        1. 鯨魚方向相反 + 支配性 >= 80%
        2. 鯨魚淨量絕對值 >= 50 BTC
        3. OBI 與鯨魚方向一致
        4. 持倉已虧損 (順勢止損)
        
        Args:
            current_direction: 當前持倉方向 (LONG/SHORT)
            
        Returns:
            反向方向 + 證據數量，或空字串表示證據不足
        """
        import json
        from pathlib import Path
        
        bridge_path = Path('ai_wolf_bridge.json')
        if not bridge_path.exists():
            return ''
        
        try:
            with open(bridge_path, 'r', encoding='utf-8') as f:
                bridge = json.load(f)
            
            wolf_data = bridge.get('wolf_to_ai', {})
            whale_status = wolf_data.get('whale_status', {})
            micro = wolf_data.get('market_microstructure', {})
            
            whale_direction = whale_status.get('current_direction', '').upper()
            whale_dominance = whale_status.get('dominance', 0)
            whale_net_btc = whale_status.get('net_qty_btc', 0)
            obi = micro.get('obi', 0)
            
            current_upper = current_direction.upper() if current_direction else ''
            
            # 判斷目標反向方向
            target_direction = ''
            if current_upper == 'LONG' and whale_direction == 'SHORT':
                target_direction = 'SHORT'
            elif current_upper == 'SHORT' and whale_direction == 'LONG':
                target_direction = 'LONG'
            
            if not target_direction:
                return ''  # 鯨魚與持倉方向一致，無需反向
            
            # ═══════════════════════════════════════════════════════════
            # 🔍 收集證據 (需要至少 3/4)
            # ═══════════════════════════════════════════════════════════
            evidence = []
            evidence_details = []
            
            # 證據 1: 鯨魚高支配性 (>= 80%)
            WHALE_DOM_THRESHOLD = 0.80
            if whale_dominance >= WHALE_DOM_THRESHOLD:
                evidence.append(True)
                evidence_details.append(f"Dom={whale_dominance:.0%}")
            else:
                evidence.append(False)
            
            # 證據 2: 鯨魚淨量大 (>= 50 BTC)
            WHALE_NET_THRESHOLD = 50
            if abs(whale_net_btc) >= WHALE_NET_THRESHOLD:
                evidence.append(True)
                evidence_details.append(f"Net={whale_net_btc:.1f}BTC")
            else:
                evidence.append(False)
            
            # 證據 3: OBI 與鯨魚方向一致
            obi_aligned = (
                (target_direction == 'LONG' and obi > 0.5) or
                (target_direction == 'SHORT' and obi < -0.5)
            )
            if obi_aligned:
                evidence.append(True)
                evidence_details.append(f"OBI={obi:.2f}")
            else:
                evidence.append(False)
            
            # 證據 4: 持倉已虧損 (當前 PnL < 0)
            testnet_pos = wolf_data.get('testnet_trading', {}).get('position', {})
            current_pnl = testnet_pos.get('unrealized_pnl', 0)
            if current_pnl < 0:
                evidence.append(True)
                evidence_details.append(f"PnL={current_pnl:.2f}")
            else:
                evidence.append(False)
            
            # ═══════════════════════════════════════════════════════════
            # 🎯 需要至少 3/4 證據
            # ═══════════════════════════════════════════════════════════
            evidence_count = sum(evidence)
            MIN_EVIDENCE_REQUIRED = 3
            
            if evidence_count >= MIN_EVIDENCE_REQUIRED:
                detail_str = ", ".join(evidence_details)
                print(f"   🐳 鯨魚反向證據充足 ({evidence_count}/4): {detail_str}")
                return f'{target_direction} ({evidence_count}/4 證據)'
            else:
                # 不打印，避免刷屏
                return ''
            
        except Exception as e:
            return ''

    def _get_ai_leverage(self, strategy_key: str) -> int:
        """
        🆕 從 AI Bridge 讀取 AI 建議的槓桿
        優先使用 AI 建議，若無則使用統一配置的預設值
        
        🔧 Binance 最小訂單要求: 100 USDT notional
           以 100 USDT 本金計算，槓桿至少需要 2x 才能滿足
        
        Args:
            strategy_key: 策略代碼 (M🐺)
            
        Returns:
            槓桿倍數 (至少 2x)
        """
        import json
        from pathlib import Path
        
        # 🔧 Binance Testnet 最小槓桿 (100 USDT notional / 100 USDT capital = 1x，但需要緩衝)
        MIN_LEVERAGE = 2  # 至少 2x 確保滿足最小訂單要求
        
        # 取得策略的 bridge 檔案
        strategies_config = self.sync_config.get('strategies', {})
        strategy_cfg = strategies_config.get(strategy_key, {})
        bridge_file = strategy_cfg.get('bridge_file')
        default_leverage = max(strategy_cfg.get('leverage', 10), MIN_LEVERAGE)
        
        if not bridge_file:
            print(f"⚠️ {strategy_key} 未設定 bridge_file，使用預設槓桿 {default_leverage}x")
            return default_leverage
        
        bridge_path = Path(bridge_file)
        
        try:
            if bridge_path.exists():
                with open(bridge_path, 'r', encoding='utf-8') as f:
                    bridge_data = json.load(f)
                
                # 🔧 修正：根據策略讀取正確的 AI 指令欄位
                ai_key_map = {
                    'M🐺': 'ai_to_wolf',
                    # 🔧 Testnet 只同步 M🐺
                }
                ai_key = ai_key_map.get(strategy_key, 'ai_to_wolf')
                
                # 從 AI 指令取得槓桿
                ai_cmd = bridge_data.get(ai_key, {})
                ai_leverage = ai_cmd.get('leverage')
                
                if ai_leverage and isinstance(ai_leverage, (int, float)) and ai_leverage > 0:
                    # 🔧 確保至少達到最小槓桿
                    final_leverage = max(int(ai_leverage), MIN_LEVERAGE)
                    if final_leverage != ai_leverage:
                        print(f"📊 {strategy_key} AI 建議槓桿: {ai_leverage}x → 調整為 {final_leverage}x (最小要求)")
                    else:
                        print(f"📊 {strategy_key} AI 建議槓桿: {final_leverage}x (from {ai_key})")
                    return final_leverage
                
                # 🆕 備選：從持倉資訊讀取實際槓桿
                pos_key_map = {
                    'M🐺': 'wolf_to_ai',
                    # 🔧 Testnet 只同步 M🐺
                }
                pos_key = pos_key_map.get(strategy_key)
                if pos_key:
                    pos_data = bridge_data.get(pos_key, {}).get('position', {})
                    pos_leverage = pos_data.get('leverage')
                    if pos_leverage and pos_leverage > 0:
                        final_leverage = max(int(pos_leverage), MIN_LEVERAGE)
                        print(f"📊 {strategy_key} 持倉槓桿: {final_leverage}x (from {pos_key})")
                        return final_leverage
            
            print(f"📊 {strategy_key} 使用配置槓桿: {default_leverage}x")
            return default_leverage
            
        except Exception as e:
            print(f"⚠️ 讀取 {strategy_key} 槓桿失敗: {e}")
            return default_leverage
    
    def _get_testnet_position_pnl(self, strategy_key: str) -> dict:
        """
        🆕 獲取 Testnet 當前倉位的實際盈虧
        直接從 Binance Testnet API 讀取真實數據
        
        Returns:
            {
                'has_position': bool,
                'direction': 'LONG' | 'SHORT' | None,
                'unrealized_pnl': float,  # 未實現盈虧 (USDT)
                'pnl_pct': float,          # 盈虧百分比
                'entry_price': float,
                'mark_price': float,
                'position_value': float    # 倉位價值 (USDT)
            }
        """
        try:
            if not self.testnet_executor:
                return {'has_position': False}
            
            # 🔧 使用 testnet_executor 的內部方法獲取倉位
            import requests
            import time as _time
            
            params = {'timestamp': int(_time.time() * 1000)}
            resp = requests.get(
                f'{self.testnet_executor.base_url}/fapi/v2/positionRisk?{self.testnet_executor._sign_request(params)}',
                headers=self.testnet_executor._get_headers()
            )
            
            if resp.status_code != 200:
                return {'has_position': False, 'error': f'API error: {resp.status_code}'}
            
            positions = resp.json()
            
            result = {'has_position': False, 'direction': None, 'unrealized_pnl': 0, 'pnl_pct': 0}
            
            for pos in positions:
                # 只處理 BTCUSDT
                if pos.get('symbol') != 'BTCUSDT':
                    continue
                    
                pos_amt = float(pos.get('positionAmt', 0))
                if abs(pos_amt) > 0.0001:  # 有倉位
                    entry_price = float(pos.get('entryPrice', 0))
                    mark_price = float(pos.get('markPrice', 0))
                    unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                    leverage = int(pos.get('leverage', 10))
                    
                    # 計算倉位價值
                    position_value = abs(pos_amt) * mark_price
                    initial_margin = position_value / leverage
                    
                    # 計算盈虧百分比 (相對於保證金)
                    pnl_pct = (unrealized_pnl / initial_margin * 100) if initial_margin > 0 else 0
                    
                    result = {
                        'has_position': True,
                        'direction': 'LONG' if pos_amt > 0 else 'SHORT',
                        'unrealized_pnl': unrealized_pnl,
                        'pnl_pct': pnl_pct,
                        'entry_price': entry_price,
                        'mark_price': mark_price,
                        'position_value': position_value,
                        'leverage': leverage,
                        'position_side': pos.get('positionSide', 'BOTH')
                    }
                    
                    # 只取一個方向的倉位 (Hedge Mode 可能有多個)
                    if pos.get('positionSide') in ['LONG', 'SHORT']:
                        break
            
            return result
            
        except Exception as e:
            print(f"⚠️ 獲取 Testnet 倉位失敗: {e}")
            return {'has_position': False, 'error': str(e)}
    
    def _should_testnet_exit(self, strategy_key: str, paper_exiting: bool = False) -> tuple:
        """
        🆕 判斷 Testnet 是否應該獨立平倉
        基於 Testnet 實際盈虧，而非 Paper Trading
        
        🔧 v3.0 方案 ABC 整合:
          A: Maker 掛單 (已在 testnet_executor 實作)
          B: 更低的止盈門檻 ($3 或 4%)
          C: AI 反轉信號跟隨 (Paper 平倉時若有利潤就跟)
        
        Args:
            strategy_key: 策略鍵值 (e.g., 'M🐺')
            paper_exiting: Paper Trading 是否正在平倉 (用於方案C判斷)
        
        Returns:
            (should_exit: bool, reason: str)
        """
        global_settings = self.sync_config.get('global_settings', {})
        
        # 檢查是否啟用獨立出場
        # 🔧 v1.3.1 Bug修復: 同步模式下返回 False，讓 Paper 控制平倉
        if not global_settings.get('testnet_independent_exit', False):
            return (False, 'sync_mode_disabled')  # 🔧 同步模式：不獨立平倉
        
        # 獲取 Testnet 實際盈虧
        pnl_info = self._get_testnet_position_pnl(strategy_key)
        
        if not pnl_info.get('has_position'):
            return (False, 'no_position')
        
        unrealized_pnl = pnl_info.get('unrealized_pnl', 0)
        pnl_pct = pnl_info.get('pnl_pct', 0)
        
        # 讀取止盈止損門檻
        min_profit_usdt = global_settings.get('testnet_min_profit_usdt', 3.0)
        min_profit_pct = global_settings.get('testnet_min_profit_pct', 0.04)
        max_loss_usdt = global_settings.get('testnet_max_loss_usdt', 5.0)
        max_loss_pct = global_settings.get('testnet_max_loss_pct', 0.05)
        fee_rate = global_settings.get('testnet_fee_rate', 0.0004)
        
        # 估算平倉手續費 (開倉+平倉)
        position_value = pnl_info.get('position_value', 0)
        estimated_fees = position_value * fee_rate * 2  # 雙邊手續費
        net_profit = unrealized_pnl - estimated_fees
        
        # 🔴 止損檢查 (優先) - 任一條件觸發
        # ⚠️ Bug修復: max_loss_pct 是小數(0.035=3.5%)，pnl_pct 是百分比(-0.51%)，需要轉換
        if unrealized_pnl < -max_loss_usdt or pnl_pct < -(max_loss_pct * 100):
            return (True, f'止損: PnL={unrealized_pnl:.2f} USDT ({pnl_pct:.2f}%)')
        
        # 🟢 止盈檢查 - 方案B: 更低門檻 ($7 或 7%)
        if net_profit >= min_profit_usdt:
            return (True, f'止盈(金額): 淨利=${net_profit:.2f} >= ${min_profit_usdt} (扣手續費 ${estimated_fees:.2f})')
        
        if pnl_pct >= min_profit_pct * 100:
            return (True, f'止盈(比例): {pnl_pct:.2f}% >= {min_profit_pct*100:.1f}%')
        
        # 🆕 方案C: AI 反轉信號跟隨 - 🔧 關閉此功能，避免過早平倉
        # 當 Paper Trading 平倉 (AI 判斷反轉) 且 Testnet 有利潤時，跟隨平倉
        # 🔧 暫時關閉: 此功能導致在淨利僅 $1-3 時就平倉，無法達到 7-10% 目標
        if False and paper_exiting and global_settings.get('follow_ai_reversal_signal', True):
            ai_reversal_min = global_settings.get('ai_reversal_min_profit_usdt', 7.0)  # 提高到 $7
            if net_profit >= ai_reversal_min:
                return (True, f'AI反轉跟隨: 淨利=${net_profit:.2f} >= ${ai_reversal_min} (扣手續費)')
            # 🔧 移除「只要正就平倉」的邏輯
        
        # 📊 倉位狀態報告 (不出場)
        return (False, f'持倉中: PnL=${unrealized_pnl:.2f} ({pnl_pct:.2f}%), 淨利=${net_profit:.2f}')
    
    def _reset_portfolio_fresh_start(self):
        """
        🆕 重置 Portfolio，開始新的交易週期
        清除所有舊記錄，使用新的啟動資金
        🔧 槓桿從 Paper Trading MODE_CONFIGS 同步，而非使用 testnet 預設值
        🔧 同時清除 AI Bridge 績效數據，避免舊數據影響決策
        """
        print("\n" + "=" * 60)
        print("🔄 重置 Testnet Portfolio (Fresh Start)")
        print("=" * 60)
        
        from datetime import datetime
        from scripts.testnet_executor import Portfolio, StrategyPosition
        
        # 創建全新的 Portfolio
        new_portfolio = Portfolio(
            created_at=datetime.now().isoformat(),
            last_update=datetime.now().isoformat()
        )
        
        # 🔧 策略 key -> Paper Trading TradingMode 對應
        mode_mapping = {
            'M🐺': TradingMode.M_AI_WHALE_HUNTER,
            # 🔧 Testnet 只同步 M🐺
        }
        
        # 為每個策略設定啟動資金，槓桿從 Paper Trading 同步
        for key in ['M🐺']:
            # 🔧 從 Paper Trading MODE_CONFIGS 取得正確的槓桿
            paper_mode = mode_mapping.get(key)
            paper_config = self.paper_system.MODE_CONFIGS.get(paper_mode) if paper_mode else None
            leverage = paper_config.leverage if paper_config else 10
            
            new_portfolio.strategies[key] = StrategyPosition(
                strategy=key,
                balance=self.initial_capital,
                leverage=leverage  # 🔧 使用 Paper Trading 的槓桿
            )
            print(f"   💰 {key}: ${self.initial_capital:.2f} (槓桿: {leverage}x ← Paper Trading)")
        
        # 更新執行器的 Portfolio
        self.testnet_executor.portfolio = new_portfolio
        self.testnet_executor._save_portfolio()
        
        print(f"\n   📁 已保存到: testnet_portfolio.json")
        print(f"   💵 總啟動資金: ${self.initial_capital * 3:.2f}")
        
        # 🔧 同時清除 AI Bridge 績效數據
        self._reset_ai_bridge_performance()
        
        print("=" * 60)
    
    def _reset_ai_bridge_performance(self):
        """
        🆕 清除 AI Bridge 的績效數據
        確保新的交易週期從零開始計算
        """
        import json
        from pathlib import Path
        
        project_root = Path(__file__).parent.parent
        
        bridge_files = [
            ('ai_wolf_bridge.json', 'M🐺 Wolf'),
            # 🔧 Testnet 只同步 M🐺
        ]
        
        for filename, name in bridge_files:
            bridge_path = project_root / filename
            if bridge_path.exists():
                try:
                    with open(bridge_path, 'r') as f:
                        bridge = json.load(f)
                    
                    # 重置績效數據
                    bridge['feedback_loop'] = {
                        'total_trades': 0,
                        'total_wins': 0,
                        'total_pnl': 0,
                        'win_rate': 0,
                        'consecutive_losses': 0,
                        'consecutive_wins': 0,
                        'last_trade_result': {}
                    }
                    
                    # 清除當前倉位記錄
                    if 'trading_to_ai' in bridge:
                        bridge['trading_to_ai']['current_position'] = None
                        bridge['trading_to_ai']['testnet_position'] = None
                    
                    with open(bridge_path, 'w') as f:
                        json.dump(bridge, f, indent=2, ensure_ascii=False)
                    
                    print(f"   🔄 {name} Bridge 績效已清除")
                except Exception as e:
                    print(f"   ⚠️ {name} Bridge 清除失敗: {e}")
    
    def _startup_sync_testnet_positions(self):
        """
        🆕 啟動時同步 Testnet 真實持倉狀態
        避免 AI 在不知道實際持倉的情況下誤判
        """
        print("\n" + "=" * 60)
        print("🔍 查詢 Testnet 真實持倉狀態...")
        print("=" * 60)
        
        try:
            # 1. 獲取當前價格
            current_price = self.testnet_executor.get_current_price()
            print(f"   💰 BTC 價格: ${current_price:,.2f}")
            
            # 2. 獲取雙向持倉
            positions = self.testnet_executor.get_all_positions()
            print(f"\n   📊 雙向持倉 (Hedge Mode):")
            print(f"      LONG:  {positions.get('LONG')}")
            print(f"      SHORT: {positions.get('SHORT')}")
            
            # 3. 獲取本地 Portfolio 狀態
            print(f"\n   📁 本地 Portfolio 狀態:")
            for key, pos in self.testnet_executor.portfolio.strategies.items():
                if pos.position_amt != 0:
                    pnl_pct = 0
                    if pos.entry_price > 0:
                        if pos.direction == 'LONG':
                            pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                        else:
                            pnl_pct = (pos.entry_price - current_price) / pos.entry_price * 100
                    
                    emoji = '📈' if pnl_pct > 0 else '📉'
                    print(f"      {key}: {pos.direction} @ ${pos.entry_price:,.2f} ({emoji} {pnl_pct:+.2f}%)")
                else:
                    print(f"      {key}: 無持倉")
            
            # 4. 驗證本地與交易所同步
            print(f"\n   🔄 驗證本地與交易所同步...")
            
            has_mismatch = False
            
            for key in ['M🐺']:  # 🔧 Testnet 只同步 M🐺
                local_pos = self.testnet_executor.portfolio.strategies.get(key)
                if not local_pos:
                    continue
                
                local_direction = local_pos.direction if local_pos.position_amt != 0 else None
                
                # 檢查交易所是否有這個方向的持倉
                if local_direction:
                    exchange_pos = positions.get(local_direction)
                    if exchange_pos is None:
                        print(f"      ⚠️ {key}: 本地有 {local_direction} 持倉，但交易所無！可能已止損")
                        has_mismatch = True
                        
                        # 🆕 清理本地狀態
                        print(f"      🧹 清除 {key} 本地持倉記錄...")
                        local_pos.position_amt = 0
                        local_pos.entry_price = 0
                        local_pos.direction = ''
                        local_pos.entry_time = ''
                        local_pos.unrealized_pnl = 0
                    else:
                        exchange_amt = float(exchange_pos.get('positionAmt', 0))
                        print(f"      ✅ {key}: {local_direction} 同步正常 (qty: {exchange_amt})")
                else:
                    print(f"      ✅ {key}: 無持倉 (同步正常)")
            
            # 5. 檢查交易所有但本地沒有的持倉
            for side in ['LONG', 'SHORT']:
                if positions.get(side):
                    exchange_pos = positions[side]
                    exchange_amt = float(exchange_pos.get('positionAmt', 0))
                    entry_price = float(exchange_pos.get('entryPrice', 0))
                    
                    # 檢查是否有策略擁有這個持倉
                    found_owner = False
                    for key in ['M🐺']:  # 🔧 Testnet 只同步 M🐺
                        local_pos = self.testnet_executor.portfolio.strategies.get(key)
                        if local_pos and local_pos.direction == side:
                            found_owner = True
                            break
                    
                    if not found_owner and exchange_amt != 0:
                        print(f"\n      ⚠️ 發現孤兒持倉: {side} {exchange_amt} BTC @ ${entry_price:,.2f}")
                        print(f"         (交易所有持倉但本地無記錄)")
                        
                        # 🆕 v1.3.0 統一模式: 自動認領孤兒持倉給 M🐺
                        if self.unified_mode:
                            print(f"      🔄 統一模式: 自動認領給 M🐺...")
                            local_pos = self.testnet_executor.portfolio.strategies.get('M🐺')
                            if local_pos:
                                local_pos.position_amt = exchange_amt
                                local_pos.entry_price = entry_price
                                local_pos.direction = side
                                local_pos.entry_time = datetime.now().isoformat()
                                local_pos.leverage = int(positions[side].get('leverage', 75))
                                self.active_testnet_strategy = 'M🐺'
                                print(f"      ✅ 已認領: M🐺 {side} @ ${entry_price:,.2f}")
            
            # 6. 保存更新
            self.testnet_executor._save_portfolio()
            
            # 7. 更新 AI Bridge 檔案
            print(f"\n   📡 更新 AI Bridge 檔案...")
            for strategy_key in ['M🐺']:  # 🔧 Testnet 只同步 M🐺
                self._update_bridge_with_testnet_info(strategy_key)
            print(f"      ✅ AI 現在知道真實持倉狀態")
            
            # 8. 顯示總結
            print("\n" + "-" * 60)
            if has_mismatch:
                print("⚠️ 已修正本地與交易所的不一致")
            else:
                print("✅ 本地與交易所持倉同步正常")
            print("-" * 60)
            
            # 9. 顯示完整狀態
            print(self.testnet_executor.get_status())
            
        except Exception as e:
            print(f"\n❌ 啟動同步失敗: {e}")
            import traceback
            traceback.print_exc()
    
    def _startup_sync_ai_state(self):
        """
        🆕 v3.1: 啟動時對齊 AI 資訊
        
        讀取 AI Bridge 檔案，獲取 AI 目前的狀態和最新判斷
        確保 Trading 系統啟動時有最新的 AI 資訊
        """
        print("\n" + "=" * 60)
        print("🧠 對齊 AI 資訊狀態...")
        print("=" * 60)
        
        try:
            # 1. 讀取 AI Wolf Bridge
            bridge_file = Path("ai_wolf_bridge.json")
            if not bridge_file.exists():
                print("   ⚠️ AI Bridge 檔案不存在，等待 AI 系統建立...")
                return
            
            with open(bridge_file, 'r') as f:
                bridge = json.load(f)
            
            # 2. 檢查 AI 最後更新時間
            last_updated = bridge.get('last_updated')
            ai_to_wolf = bridge.get('ai_to_wolf', {})
            wolf_to_ai = bridge.get('wolf_to_ai', {})
            
            if last_updated:
                try:
                    last_update_time = datetime.fromisoformat(last_updated)
                    time_diff = (datetime.now() - last_update_time).total_seconds()
                    
                    print(f"   📅 AI 最後更新: {last_updated}")
                    print(f"   ⏱️ 距離現在: {time_diff:.0f} 秒")
                    
                    if time_diff > 300:  # 超過 5 分鐘
                        print(f"   ⚠️ AI 資訊過舊 (>5分鐘)，等待 AI 系統更新...")
                    else:
                        print(f"   ✅ AI 資訊新鮮 (<5分鐘)")
                except:
                    print(f"   ⚠️ 無法解析 AI 更新時間")
            
            # 3. 讀取 AI 當前指令
            ai_command = ai_to_wolf.get('command', 'WAIT')
            ai_direction = ai_to_wolf.get('direction', 'NEUTRAL')
            ai_confidence = ai_to_wolf.get('confidence', 0)
            ai_leverage = ai_to_wolf.get('leverage', 60)
            ai_timestamp = ai_to_wolf.get('timestamp')
            
            print(f"\n   🎯 AI 當前指令:")
            print(f"      指令: {ai_command}")
            print(f"      方向: {ai_direction}")
            print(f"      信心度: {ai_confidence}%")
            print(f"      槓桿: {ai_leverage}x")
            
            # 4. 讀取鯨魚狀態
            whale_status = wolf_to_ai.get('whale_status', {})
            whale_direction = whale_status.get('current_direction', 'UNKNOWN')
            whale_dominance = whale_status.get('dominance', 0)
            whale_net_btc = whale_status.get('net_qty_btc', 0)
            
            print(f"\n   🐳 鯨魚狀態:")
            print(f"      方向: {whale_direction}")
            print(f"      主導度: {whale_dominance:.1%}")
            print(f"      淨量: {whale_net_btc:.1f} BTC")
            
            # 5. 讀取市場微結構
            micro = wolf_to_ai.get('market_microstructure', {})
            obi = micro.get('obi', 0)
            vpin = micro.get('vpin', 0)
            
            print(f"\n   📊 市場微結構:")
            print(f"      OBI: {obi:+.3f}")
            print(f"      VPIN: {vpin:.3f}")
            
            # 6. 儲存 AI 狀態供後續使用
            self.last_ai_state = {
                'command': ai_command,
                'direction': ai_direction,
                'confidence': ai_confidence,
                'leverage': ai_leverage,
                'whale_direction': whale_direction,
                'whale_dominance': whale_dominance,
                'timestamp': ai_timestamp,
                'synced_at': datetime.now().isoformat()
            }
            
            # 7. 檢查 AI 和鯨魚是否一致
            print(f"\n   🔍 AI vs 鯨魚一致性:")
            if ai_direction == whale_direction and ai_direction != 'NEUTRAL':
                print(f"      ✅ 一致: 雙方都看 {ai_direction}")
            elif ai_direction == 'NEUTRAL' or whale_direction == 'UNKNOWN':
                print(f"      ⏳ 中性: AI={ai_direction}, 鯨魚={whale_direction}")
            else:
                print(f"      ⚠️ 分歧: AI={ai_direction}, 鯨魚={whale_direction}")
            
            print("\n" + "-" * 60)
            print("✅ AI 資訊對齊完成，Trading 系統已獲取最新 AI 狀態")
            print("-" * 60)
            
        except Exception as e:
            print(f"\n⚠️ AI 資訊對齊失敗: {e}")
            import traceback
            traceback.print_exc()
    
    def _get_paper_position_state(self, mode: TradingMode) -> Dict:
        """取得 Paper Trading 某個策略的持倉狀態"""
        orders = self.paper_system.orders.get(mode, [])
        
        # 🆕 取得該策略的槓桿設定
        config = self.paper_system.MODE_CONFIGS.get(mode)
        leverage = config.leverage if config else 10
        
        # 找出活躍的持倉
        active_positions = [
            o for o in orders 
            if not o.is_blocked and o.exit_time is None
        ]
        
        if not active_positions:
            return {
                'has_position': False,
                'direction': None,
                'entry_price': 0,
                'entry_reason': None,
                'leverage': leverage  # 🆕 即使無持倉也傳遞槓桿
            }
        
        # 取最新的持倉
        latest = active_positions[-1]
        return {
            'has_position': True,
            'direction': latest.direction,
            'entry_price': latest.entry_price,
            'entry_reason': getattr(latest, 'entry_reason', None) or 'N/A',
            'confidence': latest.market_data.get('confidence', 0.5),
            'leverage': latest.leverage  # 🆕 使用訂單的實際槓桿
        }
    
    def _update_bridge_with_testnet_info(self, strategy_key: str):
        """
        🆕 用 Paper + Testnet 持倉資訊更新 AI Bridge
        
        清楚分離兩者數據，讓你可以比較差異：
        - paper_trading: Paper 模擬的即時數據 (AI 決策依據)
        - testnet_trading: Testnet 真實數據 (實際執行結果)
        
        🎯 方案 A「影子模式」：
        - AI 只看 paper_trading 做決策
        - testnet_trading 只是跟隨執行的結果記錄
        """
        if not self.enable_testnet or not self.testnet_executor:
            return
        
        bridge_file = self.strategy_bridge_map.get(strategy_key)
        if not bridge_file:
            return
        
        bridge_path = Path(project_root) / bridge_file
        if not bridge_path.exists():
            return
        
        try:
            # 讀取現有 Bridge
            with open(bridge_path, 'r') as f:
                bridge = json.load(f)
            
            # ═══════════════════════════════════════════════════════════
            # 1. 取得 Paper Trading 狀態 (AI 決策依據)
            # ═══════════════════════════════════════════════════════════
            mode_mapping = {
                'M🐺': TradingMode.M_AI_WHALE_HUNTER,
                # 🔧 Testnet 只同步 M🐺
            }
            mode = mode_mapping.get(strategy_key)
            paper_state = self._get_paper_position_state(mode) if mode else {}
            
            # Paper Trading 詳細資訊
            paper_info = {
                "source": "PAPER_SIMULATION",
                "timestamp": datetime.now().isoformat(),
                "has_position": paper_state.get('has_position', False),
            }
            
            if paper_state.get('has_position'):
                # 取得 Paper 持倉詳情
                orders = self.paper_system.orders.get(mode, [])
                active_orders = [o for o in orders if not o.is_blocked and o.exit_time is None]
                if active_orders:
                    latest = active_orders[-1]
                    current_price = self.testnet_executor.get_current_price()
                    
                    # 計算 Paper 盈虧
                    if latest.direction == 'LONG':
                        paper_pnl_pct = (current_price - latest.actual_entry_price) / latest.actual_entry_price * 100 * latest.leverage
                    else:
                        paper_pnl_pct = (latest.actual_entry_price - current_price) / latest.actual_entry_price * 100 * latest.leverage
                    
                    paper_info["position"] = {
                        "direction": latest.direction,
                        "entry_price": latest.actual_entry_price,
                        "current_price": current_price,
                        "leverage": latest.leverage,
                        "unrealized_pnl_pct": round(paper_pnl_pct, 2),
                        "entry_time": latest.entry_time
                    }
            
            # ═══════════════════════════════════════════════════════════
            # 2. 取得 Testnet 真實狀態 (執行結果)
            # ═══════════════════════════════════════════════════════════
            strategy_pos = self.testnet_executor.portfolio.strategies.get(strategy_key)
            
            testnet_info = {
                "source": "TESTNET_REAL",
                "timestamp": datetime.now().isoformat(),
                "has_position": strategy_pos.position_amt != 0 if strategy_pos else False,
            }
            
            if strategy_pos and strategy_pos.position_amt != 0:
                current_price = self.testnet_executor.get_current_price()
                
                # 計算 Testnet 盈虧
                if strategy_pos.direction == 'LONG':
                    testnet_pnl_pct = (current_price - strategy_pos.entry_price) / strategy_pos.entry_price * 100 * strategy_pos.leverage
                else:
                    testnet_pnl_pct = (strategy_pos.entry_price - current_price) / strategy_pos.entry_price * 100 * strategy_pos.leverage
                
                testnet_info["position"] = {
                    "direction": strategy_pos.direction,
                    "entry_price": strategy_pos.entry_price,
                    "current_price": current_price,
                    "leverage": strategy_pos.leverage,
                    "unrealized_pnl_pct": round(testnet_pnl_pct, 2),
                    "entry_time": strategy_pos.entry_time
                }
                
                testnet_info["account"] = {
                    "balance": strategy_pos.balance,
                    "total_trades": self.testnet_executor.portfolio.total_trades,
                    "total_pnl": self.testnet_executor.portfolio.total_pnl
                }
            elif strategy_pos:
                testnet_info["account"] = {
                    "balance": strategy_pos.balance,
                    "total_trades": self.testnet_executor.portfolio.total_trades,
                    "total_pnl": self.testnet_executor.portfolio.total_pnl
                }
            
            # ═══════════════════════════════════════════════════════════
            # 3. 計算差異 (讓你一眼看出問題)
            # ═══════════════════════════════════════════════════════════
            comparison = {
                "sync_status": "IN_SYNC" if paper_info.get('has_position') == testnet_info.get('has_position') else "OUT_OF_SYNC",
                "timestamp": datetime.now().isoformat()
            }
            
            if paper_info.get('has_position') and testnet_info.get('has_position'):
                paper_pos = paper_info.get('position', {})
                testnet_pos = testnet_info.get('position', {})
                
                entry_diff = testnet_pos.get('entry_price', 0) - paper_pos.get('entry_price', 0)
                pnl_diff = testnet_pos.get('unrealized_pnl_pct', 0) - paper_pos.get('unrealized_pnl_pct', 0)
                
                comparison["entry_price_diff"] = round(entry_diff, 2)  # 滑價
                comparison["pnl_diff_pct"] = round(pnl_diff, 2)  # 盈虧差異
                comparison["slippage_bps"] = round(abs(entry_diff) / paper_pos.get('entry_price', 1) * 10000, 1)  # 滑價 bps
            
            # ═══════════════════════════════════════════════════════════
            # 4. 更新 Bridge
            # ═══════════════════════════════════════════════════════════
            agent_key = "wolf_to_ai"  # 🔧 Testnet 只同步 M🐺
            
            # 🎯 方案 A：AI 只看 Paper 數據做決策
            # 保留原有的市場分析數據
            existing_data = bridge.get(agent_key, {})
            preserved_keys = [
                'liquidation_cascade', 'whale_status', 'market_microstructure',
                'volatility', 'risk_indicators', 'market_reaction', 'URGENT_ALERT'
            ]
            
            # 構建新的 Bridge 資訊
            # 🆕 v1.3.0: 統一模式下 AI 只看 Testnet 數據
            if self.unified_mode:
                # 統一模式: AI 的 status 基於 Testnet
                new_status = {
                    "status": "IN_POSITION" if testnet_info.get('has_position') else "IDLE",
                    "source": "TESTNET_REAL",  # 🆕 明確標記 AI 看的是 Testnet
                    "timestamp": datetime.now().isoformat(),
                    "position": testnet_info.get('position'),  # Testnet 的持倉
                    
                    # 📊 同時提供對比資訊
                    "paper_trading": paper_info,
                    "testnet_trading": testnet_info,
                    "comparison": comparison,
                }
            else:
                # 分離模式: AI 看 Paper
                new_status = {
                    "status": "IN_POSITION" if paper_info.get('has_position') else "IDLE",
                    "source": "PAPER_SIMULATION",
                    "timestamp": datetime.now().isoformat(),
                    "position": paper_info.get('position'),
                    
                    "paper_trading": paper_info,
                    "testnet_trading": testnet_info,
                    "comparison": comparison,
                }
            
            # 保留市場分析數據
            for key in preserved_keys:
                if key in existing_data:
                    new_status[key] = existing_data[key]
            
            # 🆕 v1.4.0: 同步 websocket_realtime 狀態
            # 如果 Testnet 沒有持倉，必須清除 websocket_realtime 避免 AI 誤判
            if testnet_info.get('has_position'):
                # 有持倉：保留 WebSocket 數據（它會自己更新）
                if 'websocket_realtime' in existing_data:
                    new_status['websocket_realtime'] = existing_data['websocket_realtime']
            else:
                # 🔧 無持倉：清除 websocket_realtime，避免 AI 誤判
                new_status['websocket_realtime'] = {
                    'source': 'WEBSOCKET_REALTIME',
                    'timestamp': datetime.now().isoformat(),
                    'has_position': False,  # 🎯 關鍵！告訴 AI 沒有持倉
                    'position': None,
                    'alert': {'level': 'NONE', 'message': '無持倉'}
                }
            
            bridge[agent_key] = new_status
            
            with open(bridge_path, 'w') as f:
                json.dump(bridge, f, indent=2, ensure_ascii=False)
            
        except Exception as e:
            print(f"⚠️ 更新 {strategy_key} Bridge 失敗: {e}")
    
    def _check_and_sync_testnet(self, mode: TradingMode, current_state: Dict) -> bool:
        """
        檢查 Paper Trading 狀態變化並 100% 同步到 Testnet
        
        🔧 完全同步模式 v2.0：
        - 移除所有 Testnet 獨立的冷卻限制
        - Paper Trading 做什麼，Testnet 就跟著做什麼
        - 槓桿也完全同步
        - 如果 Testnet 訂單失敗，回滾 Paper Trading 的倉位
        
        Returns:
            True: 同步成功 (或無需同步)
            False: 同步失敗，需要回滾 Paper Trading
        """
        if not self.enable_testnet or not self.testnet_bridge:
            return True  # 沒有 Testnet，視為成功
        
        strategy_key = self.tracked_modes.get(mode)
        if not strategy_key:
            return True  # 不追蹤的策略，視為成功
        
        # 🆕 同步鎖定檢查 - 防止在 Testnet 操作中重複發送指令
        import time as _time
        current_time = _time.time()
        if self.testnet_sync_lock:
            # 檢查是否超時 (防止死鎖)
            if current_time - self.testnet_sync_lock_time < self.testnet_sync_lock_timeout:
                print(f"   🔒 Testnet 同步鎖定中，跳過 ({current_time - self.testnet_sync_lock_time:.1f}s)")
                return True  # 鎖定中，跳過這次同步
            else:
                print(f"   🔓 Testnet 同步鎖超時，強制解鎖")
                self.testnet_sync_lock = False
        
        last_state = self.last_position_states.get(mode, {'has_position': False})
        
        # 🆕 先檢查 Testnet 真實狀態，避免重複操作
        testnet_real_state = self._get_testnet_position_pnl(strategy_key)
        testnet_has_position = testnet_real_state.get('has_position', False)
        testnet_direction = testnet_real_state.get('direction')
        
        # 獲取策略優先級
        my_priority = self.strategy_priority.index(strategy_key) if strategy_key in self.strategy_priority else 999
        
        # 檢測狀態變化
        if current_state['has_position'] and not last_state.get('has_position'):
            # ═══════════════════════════════════════════════════════════
            # 新開倉請求 - 100% 同步 + 失敗回滾
            # ═══════════════════════════════════════════════════════════
            
            # 🆕 檢查 Testnet 是否已經有相同方向的持倉
            if testnet_has_position and testnet_direction == current_state.get('direction'):
                print(f"   ⏭️ Testnet 已有 {testnet_direction} 持倉，跳過開倉")
                self.last_position_states[mode] = current_state
                return True  # 已經有倉，不需要重複開
            
            # 🆕 v2.2: 同步模式 - Paper Trading 已通過保護檢查，Testnet 直接跟隨
            # 傳入 is_sync_mode=True 跳過 whale dominance 等檢查（避免時序差問題）
            protection_result = self._check_entry_protection(strategy_key, current_state, is_sync_mode=True)
            if not protection_result['allow']:
                print(f"\n🛡️ {strategy_key} 開倉被保護機制阻止")
                print(f"   原因: {protection_result['reason']}")
                self.last_position_states[mode] = current_state
                return True  # 保護機制觸發，跳過但不回滾
            
            # 檢查是否有更高優先級的策略正在持倉
            if self.active_testnet_strategy:
                active_priority = self.strategy_priority.index(self.active_testnet_strategy) if self.active_testnet_strategy in self.strategy_priority else 999
                
                if my_priority > active_priority:
                    # 我的優先級較低，不能搶佔
                    print(f"\n⏸️ {strategy_key} 開倉請求被拒絕")
                    print(f"   原因: {self.active_testnet_strategy} 正在持倉 (優先級更高)")
                    self.last_position_states[mode] = current_state
                    return True  # 優先級問題不需要回滾
                elif my_priority < active_priority:
                    # 我的優先級較高，先平倉舊的
                    print(f"\n🔄 {strategy_key} 搶佔 Testnet 主控權")
                    print(f"   原因: 優先級 {strategy_key} > {self.active_testnet_strategy}")
            
            # 執行開倉 (同步槓桿)
            # 🔧 v2.1: 直接使用 Paper Trading 的槓桿（Paper 已從 AI Bridge 讀取正確值）
            # 這確保 Testnet 和 Paper 使用完全相同的槓桿
            paper_leverage = current_state.get('leverage', 10)
            leverage = paper_leverage
            
            # 🔧 印出槓桿來源，方便除錯
            print(f"   📊 使用 Paper 槓桿: {leverage}x (同步自 Paper Trading)")
            
            # 🆕 加鎖 - 防止在 Testnet 執行期間重複發送指令
            self.testnet_sync_lock = True
            self.testnet_sync_lock_time = _time.time()
            
            result = self.testnet_bridge.process_signal(
                strategy_name=mode.name,
                direction=current_state['direction'],
                confidence=current_state.get('confidence', 0.5),
                reason=current_state.get('entry_reason', 'Paper Trading Signal'),
                leverage=leverage
            )
            
            # 🆕 解鎖
            self.testnet_sync_lock = False
            
            if result:
                # ✅ Testnet 開倉成功
                if '❌' not in result and '失敗' not in result:
                    # 🆕 滑價檢查
                    if self.enable_slippage_check:
                        slippage_ok, slippage_msg = self._check_slippage(
                            strategy_key, 
                            current_state.get('entry_price', 0)
                        )
                        if not slippage_ok:
                            # 滑價過大，平倉並回滾
                            print("\n" + "🚨" * 30)
                            print(f"🚨 滑價超過容忍度！{slippage_msg}")
                            print(f"   執行平倉並回滾 Paper Trading")
                            print("🚨" * 30 + "\n")
                            
                            # 平掉 Testnet 倉位
                            self.testnet_bridge.process_signal(
                                strategy_name=mode.name,
                                direction='CLOSE',
                                reason='滑價過大，取消交易'
                            )
                            self.slippage_rejections += 1
                            self._rollback_paper_position(mode, 'slippage_exceeded')
                            return False
                    
                    print("\n" + "🌐" * 30)
                    print(f"📡 TESTNET 同步: {strategy_key} 開倉 ({leverage}x 槓桿)")
                    print(result)
                    print("🌐" * 30 + "\n")
                    self.testnet_trades += 1
                    self.active_testnet_strategy = strategy_key
                    self._update_bridge_with_testnet_info(strategy_key)
                    self.last_position_states[mode] = current_state
                    
                    # 🆕 通知 WebSocket 開倉成功，啟動冷卻期
                    if self.ws_integration:
                        self.ws_integration.notify_position_opened()
                    
                    return True
                else:
                    # ❌ Testnet 開倉失敗，需要回滾 Paper Trading
                    print("\n" + "🚨" * 30)
                    print(f"🚨 TESTNET 開倉失敗！回滾 Paper Trading {strategy_key}")
                    print(f"   錯誤: {result}")
                    print("🚨" * 30 + "\n")
                    self._rollback_paper_position(mode, 'open_failed')
                    return False
            else:
                # ❌ Testnet 無回應 (可能是網路問題或掛單未成交)
                print("\n" + "🚨" * 30)
                print(f"🚨 TESTNET 無回應！回滾 Paper Trading {strategy_key}")
                print(f"   可能原因: 網路問題 / Maker 掛單未成交")
                print("🚨" * 30 + "\n")
                self._rollback_paper_position(mode, 'no_response')
                return False
        
        elif not current_state['has_position'] and last_state.get('has_position'):
            # ═══════════════════════════════════════════════════════════
            # 平倉請求 - 根據設定決定是否跟隨 Paper/AI
            # ═══════════════════════════════════════════════════════════
            
            # 只有當前主控策略可以平倉
            if self.active_testnet_strategy and self.active_testnet_strategy != strategy_key:
                print(f"\n⏸️ {strategy_key} 平倉請求被忽略")
                print(f"   原因: 當前主控是 {self.active_testnet_strategy}")
                self.last_position_states[mode] = current_state
                return True  # 優先級問題不需要回滾
            
            testnet_pnl = self._get_testnet_position_pnl(strategy_key)
            
            # 📊 顯示 Testnet 實際狀態
            print(f"\n📊 Testnet 實際狀態檢查:")
            print(f"   未實現盈虧: {testnet_pnl.get('unrealized_pnl', 0):.2f} USDT")
            print(f"   盈虧比例: {testnet_pnl.get('pnl_pct', 0):.2f}%")
            
            if not testnet_pnl.get('has_position'):
                # Testnet 沒有倉位，直接更新狀態
                print(f"   ⚠️ Testnet 無倉位，跳過平倉")
                self.active_testnet_strategy = None
                self.last_position_states[mode] = current_state
                return True
            
            # 🔧 檢查是否啟用獨立出場判斷
            global_settings = self.sync_config.get('global_settings', {})
            independent_exit = global_settings.get('testnet_independent_exit', False)
            
            should_exit = True  # 預設跟隨 Paper/AI 平倉
            exit_reason = "跟隨 Paper/AI 平倉信號"
            
            if independent_exit:
                # 🆕 檢查 AI 是否想要反向開倉 (此時應該允許平倉)
                ai_wants_reverse = self._check_ai_wants_reverse(strategy_key, last_state.get('direction', ''))
                
                # 🆕 檢查鯨魚方向是否與當前持倉相反 (高支配性時強制平倉)
                whale_wants_reverse = self._check_whale_wants_reverse(last_state.get('direction', ''))
                
                if ai_wants_reverse:
                    # AI 想反向，優先允許平倉以便開新倉
                    should_exit = True
                    exit_reason = f"AI 反向信號 ({ai_wants_reverse}) - 允許平倉"
                    print(f"   🔄 AI 想反向開倉: {ai_wants_reverse}")
                elif whale_wants_reverse:
                    # 鯨魚方向與持倉相反且支配性高，強制平倉
                    should_exit = True
                    exit_reason = f"鯨魚反向 ({whale_wants_reverse}) - 強制平倉"
                    print(f"   🐳 鯨魚方向相反，強制平倉: {whale_wants_reverse}")
                else:
                    # 🆕 方案C: 傳遞 paper_exiting=True 告知 Paper 正在平倉
                    should_exit, exit_reason = self._should_testnet_exit(strategy_key, paper_exiting=True)
                
                print(f"   📋 獨立判斷: {exit_reason}")
                
                if not should_exit:
                    # 🚫 Testnet 還沒達到止盈/止損，且 AI 沒有反向信號
                    print(f"\n🚫 Testnet 拒絕跟隨平倉 (獨立模式)")
                    print(f"   原因: {exit_reason}")
                    print(f"   💡 提示: 若 AI 發出反向信號將自動平倉")
                    # 🔧 重要: 不更新 last_position_states，讓下次還能檢測
                    return True
            else:
                # 🔧 同步模式：完全跟隨 Paper/AI
                print(f"   📋 同步模式: 跟隨 Paper/AI 平倉")
            
            # 🆕 檢查 Testnet 是否真的有持倉需要平
            if not testnet_has_position:
                print(f"   ⏭️ Testnet 無持倉，跳過平倉")
                self.last_position_states[mode] = current_state
                return True
            
            # 🆕 加鎖
            self.testnet_sync_lock = True
            self.testnet_sync_lock_time = _time.time()
            
            # ✅ Testnet 執行平倉
            result = self.testnet_bridge.process_signal(
                strategy_name=mode.name,
                direction='CLOSE',
                reason=f'跟隨 Paper/AI: {exit_reason}'
            )
            
            # 🆕 解鎖
            self.testnet_sync_lock = False
            if result:
                print("\n" + "🌐" * 30)
                print(f"📡 TESTNET 同步: {strategy_key} 平倉")
                print(f"   原因: {exit_reason}")
                print(result)
                print("🌐" * 30 + "\n")
                self.testnet_trades += 1
                self.active_testnet_strategy = None
                self._update_bridge_with_testnet_info(strategy_key)
            
            self.last_position_states[mode] = current_state
            return True  # 平倉失敗不回滾 Paper (Testnet 可能已被止損)
        
        elif (current_state['has_position'] and last_state.get('has_position') and 
              current_state['direction'] != last_state.get('direction')):
            # ═══════════════════════════════════════════════════════════
            # 方向改變 (反向開倉) - 100% 同步 + 失敗回滾
            # ═══════════════════════════════════════════════════════════
            
            # 檢查優先級
            if self.active_testnet_strategy and self.active_testnet_strategy != strategy_key:
                active_priority = self.strategy_priority.index(self.active_testnet_strategy) if self.active_testnet_strategy in self.strategy_priority else 999
                if my_priority > active_priority:
                    print(f"\n⏸️ {strategy_key} 反向請求被拒絕")
                    print(f"   原因: {self.active_testnet_strategy} 正在持倉 (優先級更高)")
                    self.last_position_states[mode] = current_state
                    return True  # 優先級問題不需要回滾
            
            # 同步槓桿
            leverage = current_state.get('leverage', 10)
            result = self.testnet_bridge.process_signal(
                strategy_name=mode.name,
                direction=current_state['direction'],
                confidence=current_state.get('confidence', 0.5),
                reason=f"反向信號: {current_state.get('entry_reason', 'Paper Trading Signal')}",
                leverage=leverage
            )
            
            if result:
                if '❌' not in result and '失敗' not in result:
                    print("\n" + "🌐" * 30)
                    print(f"📡 TESTNET 同步: {strategy_key} 反向開倉 ({leverage}x 槓桿)")
                    print(result)
                    print("🌐" * 30 + "\n")
                    self.testnet_trades += 1
                    self.active_testnet_strategy = strategy_key
                    self._update_bridge_with_testnet_info(strategy_key)
                    self.last_position_states[mode] = current_state
                    return True
                else:
                    # ❌ Testnet 反向開倉失敗
                    print("\n" + "🚨" * 30)
                    print(f"🚨 TESTNET 反向開倉失敗！回滾 Paper Trading {strategy_key}")
                    print(f"   錯誤: {result}")
                    print("🚨" * 30 + "\n")
                    self._rollback_paper_position(mode, 'flip_failed')
                    return False
            else:
                print("\n" + "🚨" * 30)
                print(f"🚨 TESTNET 反向無回應！回滾 Paper Trading {strategy_key}")
                print("🚨" * 30 + "\n")
                self._rollback_paper_position(mode, 'no_response')
                return False
        
        # 無變化，更新狀態
        self.last_position_states[mode] = current_state
        return True
    
    def _rollback_paper_position(self, mode: TradingMode, reason: str):
        """
        🆕 回滾 Paper Trading 倉位
        當 Testnet 交易失敗時，取消 Paper Trading 的倉位並恢復餘額
        
        Args:
            mode: 交易模式
            reason: 回滾原因 (open_failed / no_response / flip_failed)
        """
        try:
            # 🔧 修正：Paper Trading 使用 self.orders[mode] 而非 self.positions
            open_orders = [
                o for o in self.paper_system.orders.get(mode, [])
                if not o.is_blocked and o.exit_time is None
            ]
            
            if not open_orders:
                print(f"   ⚠️ 無法找到 {mode.name} 的 Paper 持倉")
                return
            
            # 取最後一筆開倉的訂單
            position = open_orders[-1]
            strategy_key = self.tracked_modes.get(mode, mode.name)
            
            print(f"\n🔄 回滾 {strategy_key} Paper Trading 倉位:")
            print(f"   📍 原方向: {position.direction}")
            print(f"   📍 原入場價: ${position.entry_price:,.2f}")
            print(f"   📍 倉位價值: ${position.position_value:.2f}")
            print(f"   📍 回滾原因: {reason}")
            
            # 恢復資金 (扣除的手續費不退)
            restored_capital = position.position_value
            
            # 🔧 標記訂單為已阻塞 (blocked)，而非刪除
            position.is_blocked = True
            position.exit_time = datetime.now()
            position.exit_reason = f'ROLLBACK: {reason}'
            
            # 恢復餘額
            self.paper_system.balances[mode] += restored_capital
            
            # 取消 Maker 掛單 (如果有)
            if hasattr(self.paper_system, 'pending_maker_orders') and mode in self.paper_system.pending_maker_orders:
                del self.paper_system.pending_maker_orders[mode]
            
            # 重置該策略的狀態
            self.last_position_states[mode] = {'has_position': False}
            
            print(f"   ✅ Paper 倉位已取消 (標記為 blocked)")
            print(f"   💰 恢復資金: ${restored_capital:.2f}")
            print(f"   ⏳ 等待下一次交易機會...")
            
            # 記錄到 Bridge
            self._update_bridge_with_rollback_info(strategy_key, reason)
            
        except Exception as e:
            print(f"   ❌ 回滾失敗: {e}")
            import traceback
            traceback.print_exc()
    
    def _check_slippage(self, strategy_key: str, paper_entry_price: float) -> tuple:
        """
        🆕 檢查 Testnet 成交價與 Paper 預期價的滑價
        
        Args:
            strategy_key: 策略名稱 (M🐺)
            paper_entry_price: Paper Trading 的入場價
            
        Returns:
            (is_ok, message): 是否在容忍範圍內, 訊息
        """
        try:
            # 取得 Testnet 成交價
            strategy_pos = self.testnet_executor.portfolio.strategies.get(strategy_key)
            if not strategy_pos or strategy_pos.position_amt == 0:
                return True, "無 Testnet 倉位"
            
            testnet_entry_price = strategy_pos.entry_price
            
            # 計算滑價 (bps)
            if paper_entry_price <= 0:
                # 沒有 Paper 入場價，用當前價格
                paper_entry_price = self.testnet_executor.get_current_price()
            
            slippage_abs = abs(testnet_entry_price - paper_entry_price)
            slippage_bps = slippage_abs / paper_entry_price * 10000
            
            # 判斷方向：做多時 Testnet 價格高是不利的，做空時反過來
            direction = strategy_pos.direction
            if direction == 'LONG':
                # 做多：Testnet 買得比 Paper 貴 = 不利滑價
                unfavorable = testnet_entry_price > paper_entry_price
            else:
                # 做空：Testnet 賣得比 Paper 便宜 = 不利滑價
                unfavorable = testnet_entry_price < paper_entry_price
            
            slippage_direction = "不利" if unfavorable else "有利"
            
            msg = (
                f"滑價: {slippage_bps:.1f} bps ({slippage_direction})\n"
                f"   Paper 入場: ${paper_entry_price:,.2f}\n"
                f"   Testnet 入場: ${testnet_entry_price:,.2f}\n"
                f"   差異: ${slippage_abs:,.2f}"
            )
            
            # 只檢查不利滑價
            if unfavorable and slippage_bps > self.slippage_tolerance_bps:
                return False, msg
            
            return True, msg
            
        except Exception as e:
            print(f"   ⚠️ 滑價檢查錯誤: {e}")
            return True, f"檢查錯誤: {e}"
    
    def _update_bridge_with_rollback_info(self, strategy_key: str, reason: str):
        """
        🆕 更新 Bridge 記錄回滾事件
        讓 AI 知道發生了什麼
        """
        try:
            bridge_file = self.strategy_bridge_map.get(strategy_key)
            if not bridge_file:
                return
            
            project_root = Path(__file__).parent.parent
            bridge_path = project_root / bridge_file
            
            if bridge_path.exists():
                with open(bridge_path, 'r') as f:
                    bridge = json.load(f)
            else:
                bridge = {}
            
            # 添加回滾事件記錄
            if 'rollback_events' not in bridge:
                bridge['rollback_events'] = []
            
            bridge['rollback_events'].append({
                'time': datetime.now().isoformat(),
                'reason': reason,
                'message': f'Testnet 交易失敗，Paper Trading 倉位已取消'
            })
            
            # 只保留最近 3 筆 (減少 token 消耗)
            bridge['rollback_events'] = bridge['rollback_events'][-3:]
            
            # 更新最後回滾時間
            bridge['last_rollback'] = {
                'time': datetime.now().isoformat(),
                'reason': reason
            }
            
            with open(bridge_path, 'w') as f:
                json.dump(bridge, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            print(f"   ⚠️ 更新 Bridge 回滾記錄失敗: {e}")
    
    def _periodic_sync(self):
        """定期同步 Testnet 狀態並更新 Bridge"""
        current_time = time.time()
        
        # 同步 Testnet 持倉 (每 10 秒)
        if current_time - self.last_sync_time >= self.testnet_sync_interval:
            if self.enable_testnet and self.testnet_executor:
                try:
                    # 🆕 sync_positions 現在會返回事件
                    events = self.testnet_executor.sync_positions()
                    
                    # 🆕 處理止損事件
                    for strategy_key, event in events.items():
                        if event == 'closed_by_sl':
                            print(f"\n🔔 {strategy_key} 在 Testnet 被止損!")
                            # 清除主控策略
                            if self.active_testnet_strategy == strategy_key:
                                self.active_testnet_strategy = None
                            # 更新 Bridge 通知 AI
                            self._update_bridge_with_testnet_info(strategy_key)
                            
                except Exception as e:
                    print(f"⚠️ Testnet 同步錯誤: {e}")
            self.last_sync_time = current_time
        
        # 🆕 定期更新 Bridge 給 AI (每 5 秒)
        if current_time - self.last_bridge_update_time >= self.bridge_update_interval:
            if self.enable_testnet and self.testnet_executor:
                for strategy_key in ['M🐺']:  # 🔧 Testnet 只同步 M🐺
                    self._update_bridge_with_testnet_info(strategy_key)
            self.last_bridge_update_time = current_time
    
    def _verify_and_sync_portfolio_state(self):
        """
        🆕 驗證並同步 Portfolio 狀態與 Testnet 實際狀態
        修復 Bug: Portfolio 顯示已平倉但實際還有倉位
        """
        if not self.testnet_executor:
            return
        
        try:
            # 獲取 Testnet 實際倉位
            actual_pnl = self._get_testnet_position_pnl('M🐺')
            
            # 獲取 Portfolio 記錄
            portfolio = self.testnet_executor.portfolio
            if not portfolio or 'M🐺' not in portfolio.strategies:
                return
            
            strat = portfolio.strategies['M🐺']
            
            # 檢查不同步情況
            portfolio_has_position = strat.position_amt != 0
            actual_has_position = actual_pnl.get('has_position', False)
            
            if portfolio_has_position != actual_has_position:
                print(f"\n⚠️ 偵測到 Portfolio 與 Testnet 實際狀態不同步!")
                print(f"   Portfolio: {'有倉位' if portfolio_has_position else '無倉位'} (position_amt={strat.position_amt})")
                print(f"   Testnet 實際: {'有倉位' if actual_has_position else '無倉位'}")
                
                if actual_has_position and not portfolio_has_position:
                    # Portfolio 說無倉位，但實際有 → 更新 Portfolio
                    print(f"   🔧 同步: 更新 Portfolio 為有倉位狀態")
                    strat.position_amt = 0.001 if actual_pnl.get('direction') == 'LONG' else -0.001
                    strat.entry_price = actual_pnl.get('entry_price', 0)
                    strat.direction = actual_pnl.get('direction', '')
                    strat.unrealized_pnl = actual_pnl.get('unrealized_pnl', 0)
                    strat.leverage = actual_pnl.get('leverage', 75)
                    self.active_testnet_strategy = 'M🐺'
                    
                    # 🆕 立即更新 Bridge，讓 AI 知道有持倉
                    self._update_bridge_with_testnet_info('M🐺')
                    print(f"   📡 已通知 AI 有持倉")
                    
                elif not actual_has_position and portfolio_has_position:
                    # Portfolio 說有倉位，但實際無 → 可能被止損了，更新 Portfolio
                    print(f"   🔧 同步: 更新 Portfolio 為無倉位狀態")
                    strat.position_amt = 0
                    strat.entry_price = 0
                    strat.direction = ''
                    strat.unrealized_pnl = 0
                    self.active_testnet_strategy = None
                
                self.testnet_executor._save_portfolio()
                print(f"   ✅ Portfolio 已同步")
                
        except Exception as e:
            print(f"⚠️ 驗證 Portfolio 狀態失敗: {e}")
    
    async def _testnet_sync_loop(self):
        """Testnet 同步循環 - 與 Paper Trading 並行運行"""
        last_sync_time = time.time()
        last_pnl_check_time = time.time()
        last_verify_time = time.time()
        
        while datetime.now() < self.paper_system.end_time:
            try:
                current_time = time.time()
                
                # 🆕 每 30 秒驗證 Portfolio 與實際狀態是否同步
                if current_time - last_verify_time >= 30:
                    self._verify_and_sync_portfolio_state()
                    last_verify_time = current_time
                
                # 🆕 每 10 秒主動檢查 Testnet 是否需要獨立止盈止損
                if current_time - last_pnl_check_time >= 10:
                    await self._check_testnet_independent_exit()
                    last_pnl_check_time = current_time
                
                # 每 5 秒同步一次
                if current_time - last_sync_time >= 5:
                    # 同步每個追蹤的策略
                    for mode in self.tracked_modes.keys():
                        current_state = self._get_paper_position_state(mode)
                        self._check_and_sync_testnet(mode, current_state)
                    
                    # 更新 Bridge (讓 AI 獲得 Testnet 真實資訊)
                    self._periodic_sync()
                    last_sync_time = current_time
                
                await asyncio.sleep(0.5)  # 🔧 v3.0: 每 0.5 秒檢查 (配合 AI 5 秒判斷)
                
            except Exception as e:
                print(f"⚠️ Testnet sync error: {e}")
                await asyncio.sleep(2)
    
    async def _check_testnet_independent_exit(self):
        """
        🆕 主動檢查 Testnet 是否應該獨立平倉
        即使 Paper Trading 沒有發出平倉信號
        
        🔧 v1.3.0 統一模式: 當 Testnet 平倉時，同步 Paper
        🔧 v1.3.1 Bug修復: 同步模式下不應該獨立平倉！
        """
        if not self.enable_testnet or not self.active_testnet_strategy:
            return
        
        # 🔧 v1.3.1 Bug修復: 同步模式下，不執行獨立平倉檢查！
        # 應該完全由 Paper Trading 控制平倉時機
        global_settings = self.sync_config.get('global_settings', {})
        if not global_settings.get('testnet_independent_exit', False):
            return  # 同步模式：不獨立平倉，由 Paper 控制
        
        strategy_key = self.active_testnet_strategy
        
        # 檢查是否應該獨立平倉
        should_exit, exit_reason = self._should_testnet_exit(strategy_key)
        
        if should_exit:
            print("\n" + "💰" * 30)
            print(f"💰 TESTNET 獨立出場: {strategy_key}")
            print(f"   原因: {exit_reason}")
            print("💰" * 30)
            
            # 🔧 使用正確的策略名稱，讓 STRATEGY_NAME_MAP 能識別
            # M🐺 -> M🐺 (🔧 Testnet 只同步 M🐺)
            result = self.testnet_bridge.process_signal(
                strategy_name=strategy_key,  # 直接用 M🐺
                direction='CLOSE',
                reason=f'Testnet 獨立止盈止損: {exit_reason}'
            )
            
            if result:
                print(f"✅ Testnet 獨立平倉成功")
                print(result)
                self.testnet_trades += 1
                
                # 🆕 v1.3.0 統一模式: 同步到 Paper Trading
                if self.unified_mode:
                    self._sync_paper_from_testnet_close(strategy_key, 0, exit_reason)
                
                self.active_testnet_strategy = None
                self._update_bridge_with_testnet_info(strategy_key)
                
                # 🔥 通知 WebSocket 平倉完成，重置監控狀態
                if self.ws_integration:
                    self.ws_integration.notify_exit_complete()
            else:
                print(f"⚠️ Testnet 獨立平倉失敗")
                # 平倉失敗也要重置狀態，避免卡死
                if self.ws_integration:
                    self.ws_integration.exit_in_progress = False
            
            print("💰" * 30 + "\n")
    
    def run(self):
        """運行混合系統"""
        print("\n" + "=" * 80)
        print("🚀 Paper Trading + Testnet 混合模式啟動")
        print(f"   測試時長: {self.test_duration_hours} 小時")
        print(f"   啟動資金: {self.initial_capital} U/策略")
        print(f"   Testnet: {'✅ 啟用' if self.enable_testnet else '❌ 停用'}")
        print(f"   追蹤策略: {', '.join([f'{v}' for v in self.tracked_modes.values()])}")
        
        # 🆕 統一模式標記
        if self.unified_mode:
            print("")
            print("   🔗 【統一模式】 M🐺 = T🐺")
            print("      - AI 數據源: Testnet 真實交易")
            print("      - Paper 跟隨 Testnet 狀態")
        
        print("")
        print("   🎯 Testnet 優先級: M🐺 (AI Whale Hunter)")
        print("      (🔧 只同步 M🐺 策略)")
        print("")
        # 🆕 WebSocket 狀態
        ws_status = "✅ 啟用 (即時止盈止損 <1秒)" if self.ws_integration else "❌ 停用 (10秒輪詢)"
        print(f"   ⚡ WebSocket: {ws_status}")
        print("")
        print("   📡 AI 將收到 Testnet 真實持倉資訊！")
        print("=" * 80 + "\n")
        
        # 使用 asyncio 同時運行 Paper Trading 和 Testnet 同步
        async def run_both():
            """同時運行兩個系統"""
            # 創建 Testnet 同步任務
            testnet_task = asyncio.create_task(self._testnet_sync_loop())
            
            try:
                # 運行 Paper Trading (這是 async 的)
                await self.paper_system.run()
            except KeyboardInterrupt:
                print("\n\n⏹️ 用戶中斷...")
            finally:
                testnet_task.cancel()
                try:
                    await testnet_task
                except asyncio.CancelledError:
                    pass
                
                # 🆕 停止 WebSocket 監控
                if self.ws_integration:
                    self.ws_integration.stop()
                
                self._print_final_summary()
        
        # 運行 async 主循環
        try:
            asyncio.run(run_both())
        except KeyboardInterrupt:
            print("\n\n⏹️ 用戶中斷...")
    
    def _print_final_summary(self):
        """列印最終摘要"""
        print("\n" + "=" * 80)
        print("📊 最終摘要")
        print("=" * 80)
        
        if self.enable_testnet and self.testnet_executor:
            print("\n🌐 Testnet 交易統計:")
            print(f"   總交易次數: {self.testnet_trades}")
            print(self.testnet_executor.get_status())
        
        # 🆕 WebSocket 統計
        if self.ws_integration:
            ws_stats = self.ws_integration.get_stats()
            print("\n⚡ WebSocket 即時出場統計:")
            print(f"   即時出場次數: {ws_stats.get('instant_exits', 0)}")
            print(f"   估計節省滑點: ${ws_stats.get('slippage_saved_estimate', 0):.2f}")
        
        print("\n📝 Paper Trading 統計已保存到 logs/")
        print("=" * 80)


def main():
    """主函數"""
    print("\n" + "=" * 60)
    print("🚀 Testnet Hybrid Trading System 啟動設定")
    print("=" * 60)
    
    # 🆕 啟用終端機日誌記錄
    logger = setup_terminal_logging()
    
    # 解析參數
    duration = 8.0  # 預設 8 小時
    enable_testnet = True
    initial_capital = 100.0  # 預設啟動資金
    
    if len(sys.argv) > 1:
        try:
            duration = float(sys.argv[1])
        except ValueError:
            print(f"⚠️ 無效的時長參數: {sys.argv[1]}, 使用預設值 8 小時")
    
    if len(sys.argv) > 2:
        if sys.argv[2].lower() in ['false', 'no', '0']:
            enable_testnet = False
    
    # 🆕 詢問啟動資金
    print(f"\n💰 請輸入每個策略的啟動資金 (USDT)")
    print(f"   預設: 100 U")
    print(f"   策略: M🐺 (AI Whale Hunter)")
    
    try:
        user_input = input("\n   輸入金額 (直接按 Enter 使用預設): ").strip()
        if user_input:
            initial_capital = float(user_input)
            if initial_capital <= 0:
                print("⚠️ 金額必須大於 0，使用預設值 100 U")
                initial_capital = 100.0
            elif initial_capital > 10000:
                print("⚠️ 金額超過 10000 U，請確認這是 Testnet 測試")
                confirm = input("   確認繼續？(y/n): ").strip().lower()
                if confirm != 'y':
                    initial_capital = 100.0
                    print("   使用預設值 100 U")
    except ValueError:
        print("⚠️ 無效輸入，使用預設值 100 U")
        initial_capital = 100.0
    except KeyboardInterrupt:
        print("\n\n⏹️ 用戶取消")
        close_terminal_logging()
        return
    
    print(f"\n✅ 設定完成:")
    print(f"   • 運行時長: {duration} 小時")
    print(f"   • 啟動資金: {initial_capital} U/策略")
    print(f"   • 總資金: {initial_capital * 3} U (3 策略)")
    print(f"   • Testnet: {'啟用' if enable_testnet else '停用'}")
    
    # 確認開始
    try:
        input("\n按 Enter 開始交易... (Ctrl+C 取消)")
    except KeyboardInterrupt:
        print("\n\n⏹️ 用戶取消")
        close_terminal_logging()
        return
    
    # 運行系統
    try:
        system = TestnetHybridSystem(
            test_duration_hours=duration,
            enable_testnet=enable_testnet,
            initial_capital=initial_capital
        )
        system.run()
    finally:
        # 🆕 確保日誌記錄關閉
        close_terminal_logging()


if __name__ == '__main__':
    main()
