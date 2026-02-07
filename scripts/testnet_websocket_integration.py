#!/usr/bin/env python3
"""
🔌 Testnet WebSocket 整合模組
將 WebSocket 即時監控整合到 Hybrid 交易系統

功能:
1. 即時 ROI 監控 (每秒更新)
2. 即時止盈止損 (<1秒響應 vs 原本10秒輪詢)
3. 偵測手動平倉 → 同步 Paper Trading
4. 爆倉預警 (ROI 接近 -80%)
"""

import asyncio
import json
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Dict, Any

# 導入 WebSocket 監控器
from scripts.testnet_websocket import TestnetWebSocketMonitor

# 載入統一配置
SYNC_CONFIG_FILE = Path(__file__).parent.parent / 'config' / 'strategy_sync_config.json'


class WebSocketIntegration:
    """
    WebSocket 整合器 - 將即時監控整合到 Hybrid 系統
    
    優勢:
    - 止盈止損響應: 10秒 → <1秒 (減少滑點 ~0.3-0.5%)
    - 即時同步: 偵測手動平倉立即更新 Paper Trading
    - 爆倉預警: ROI 接近 -80% 時提前警告
    """
    
    def __init__(self, hybrid_system=None):
        """
        初始化 WebSocket 整合器
        
        Args:
            hybrid_system: TestnetHybridSystem 實例 (用於回調)
        """
        self.hybrid_system = hybrid_system
        self.monitor = None
        self.ws_thread = None
        self.running = False
        
        # 載入配置
        self.sync_config = self._load_sync_config()
        
        # 止盈止損參數 (從配置讀取) - 🎯 目標 7-10% ROI
        global_settings = self.sync_config.get('global_settings', {})
        self.min_profit_usdt = global_settings.get('testnet_min_profit_usdt', 7.0)
        self.min_profit_pct = global_settings.get('testnet_min_profit_pct', 0.07) * 100  # 轉換為 % (7%)
        self.max_loss_usdt = global_settings.get('testnet_max_loss_usdt', 3.5)
        self.max_loss_pct = global_settings.get('testnet_max_loss_pct', 0.035) * 100  # 轉換為 % (3.5%)
        self.fee_rate = global_settings.get('testnet_fee_rate', 0.0001)
        
        # 🆕 WebSocket 模式設定
        self.instant_exit_enabled = global_settings.get('websocket_instant_exit', True)  # 🔧 預設開啟
        self.feed_to_ai = global_settings.get('websocket_feed_to_ai', True)
        
        # 🆕 追蹤止盈 (Trailing Stop)
        self.trailing_stop_enabled = global_settings.get('trailing_stop_enabled', True)
        self.trailing_activation_pct = global_settings.get('trailing_activation_pct', 7.0)  # 7% 啟動
        self.trailing_distance_pct = global_settings.get('trailing_distance_pct', 2.5)  # 回撤 2.5% 平倉
        self.peak_pnl_pct = 0.0  # 追蹤最高盈利
        self.trailing_active = False  # 追蹤止盈是否已啟動
        
        # 🆕 極端獲利強制止盈 (無論 AI 說什麼)
        self.extreme_profit_enabled = global_settings.get('extreme_profit_exit_enabled', True)
        self.extreme_profit_pct = global_settings.get('extreme_profit_pct', 13.0)  # +13% ROI 強制止盈
        self.extreme_profit_usdt = global_settings.get('extreme_profit_usdt', 13.0)  # +$13 強制止盈
        self.extreme_profit_use_taker = global_settings.get('extreme_profit_use_taker', True)  # 🆕 極端獲利用 Taker 確保成交
        
        # 爆倉預警門檻
        self.liquidation_warning_pct = -80.0  # ROI 接近 -80% 時預警
        
        # 狀態追蹤
        self.last_pnl_check = 0
        self.pnl_check_interval = 0.5  # 每 0.5 秒檢查一次
        self.last_exit_time = 0
        self.exit_cooldown = 3.0  # 平倉後冷卻 3 秒避免重複觸發
        self.last_bridge_update = 0
        self.bridge_update_interval = 1.0  # 每秒更新一次 Bridge
        
        # 🆕 開倉後冷卻期 - 等待 Maker 掛單完全成交
        self.position_open_time = 0  # 開倉時間
        self.entry_cooldown = 10.0   # 開倉後 10 秒內不觸發止盈止損
        
        # 🆕🔥 防止重複觸發 - 當正在平倉時不再觸發新的平倉
        self.exit_in_progress = False  # 平倉進行中標記
        
        # 🆕 即時 Testnet 狀態 (餵給 AI)
        self.realtime_testnet_state = {
            'pnl_usdt': 0,
            'pnl_pct': 0,
            'current_price': 0,
            'entry_price': 0,
            'direction': '',
            'leverage': 0,
            'last_update': ''
        }
        
        # 回調函數
        self.on_instant_exit: Optional[Callable] = None  # 即時平倉
        self.on_manual_close_detected: Optional[Callable] = None  # 手動平倉
        self.on_liquidation_warning: Optional[Callable] = None  # 爆倉預警
        self.on_pnl_feed: Optional[Callable] = None  # 🆕 餵資料給 AI
        
        # 統計
        self.instant_exits = 0
        self.slippage_saved_estimate = 0.0
        self.ai_feed_count = 0
    
    def notify_position_opened(self):
        """🆕 通知 WebSocket 有新倉位開啟，啟動冷卻期"""
        self.position_open_time = time.time()
        self.exit_in_progress = False  # 新倉位開啟，重置平倉標記
        print(f"   ⏳ WebSocket 冷卻期啟動: {self.entry_cooldown}秒後開始監控止盈止損")
    
    def notify_exit_complete(self):
        """🆕 通知 WebSocket 平倉已完成，可以開始監控新倉位"""
        self.exit_in_progress = False
        self.last_exit_time = time.time()  # 重設冷卻計時
        self._reset_trailing()  # 重置追蹤止盈
        print(f"   ✅ WebSocket 平倉完成，重置監控狀態")
        
    def _load_sync_config(self) -> dict:
        """載入統一配置"""
        try:
            if SYNC_CONFIG_FILE.exists():
                with open(SYNC_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"⚠️ 載入配置失敗: {e}")
        return {}
    
    async def _on_pnl_update(self, data: Dict):
        """
        處理即時 PnL 更新 - 核心止盈止損邏輯
        
        🚀 優勢: <1秒響應 vs 原本10秒輪詢
        """
        current_time = time.time()
        
        # 🔥 平倉進行中 - 不再觸發新的平倉 (防止 Maker 等待成交期間重複觸發)
        if self.exit_in_progress:
            return
        
        # 🆕 開倉冷卻期 - 等待 Maker 掛單完全成交
        if self.position_open_time > 0:
            elapsed = current_time - self.position_open_time
            if elapsed < self.entry_cooldown:
                # 只在第一次顯示
                if int(elapsed) == 0:
                    remaining = self.entry_cooldown - elapsed
                    print(f"\r   ⏳ 冷卻中: {remaining:.0f}秒後開始監控...    ", end='', flush=True)
                return
        
        # 平倉冷卻期間不檢查
        if current_time - self.last_exit_time < self.exit_cooldown:
            return
        
        # 頻率限制
        if current_time - self.last_pnl_check < self.pnl_check_interval:
            return
        self.last_pnl_check = current_time
        
        pnl_usdt = data.get('pnl_usdt', 0)
        pnl_pct = data.get('pnl_pct', 0)
        direction = data.get('direction', '')
        current_price = data.get('current_price', 0)
        entry_price = data.get('entry_price', 0)
        leverage = data.get('leverage', 0)
        
        # 🆕 更新即時狀態 (供 AI 讀取)
        self.realtime_testnet_state = {
            'pnl_usdt': pnl_usdt,
            'pnl_pct': pnl_pct,
            'current_price': current_price,
            'entry_price': entry_price,
            'direction': direction,
            'leverage': leverage,
            'last_update': datetime.now().isoformat()
        }
        
        # 估算淨利 (扣手續費)
        if entry_price > 0 and current_price > 0:
            # 估算倉位價值
            position_value = abs(data.get('position_amt', 0)) * current_price if 'position_amt' in data else pnl_usdt * 10
            estimated_fees = position_value * self.fee_rate * 2
            net_profit = pnl_usdt - estimated_fees
        else:
            net_profit = pnl_usdt
        
        # ═══════════════════════════════════════════════════════════
        # 🆕 餵資料給 AI Bridge (每秒更新)
        # ═══════════════════════════════════════════════════════════
        if self.feed_to_ai and current_time - self.last_bridge_update >= self.bridge_update_interval:
            self.last_bridge_update = current_time
            self.ai_feed_count += 1
            await self._update_ai_bridge(pnl_usdt, pnl_pct, current_price, entry_price, direction, leverage)
        
        # ═══════════════════════════════════════════════════════════
        # 💎 極端獲利強制止盈 (無論 instant_exit_enabled 是否開啟)
        # ═══════════════════════════════════════════════════════════
        if self.extreme_profit_enabled:
            if pnl_pct >= self.extreme_profit_pct or pnl_usdt >= self.extreme_profit_usdt:
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"\n\n{'💎' * 30}")
                print(f"💎 [{timestamp}] 極端獲利強制止盈!")
                print(f"   ROI: +{pnl_pct:.2f}% | PnL: +${pnl_usdt:.2f}")
                print(f"   門檻: {self.extreme_profit_pct}% 或 ${self.extreme_profit_usdt}")
                print(f"{'💎' * 30}\n")
                
                if self.on_instant_exit:
                    self.exit_in_progress = True  # 🔥 標記平倉進行中，防止重複觸發
                    self.last_exit_time = current_time
                    self.instant_exits += 1
                    self._reset_trailing()  # 重置追蹤止盈
                    await self._safe_callback(self.on_instant_exit, {
                        'reason': f'極端獲利: +{pnl_pct:.1f}% (+${pnl_usdt:.2f})',
                        'pnl_usdt': pnl_usdt,
                        'pnl_pct': pnl_pct,
                        'direction': direction,
                        'current_price': current_price,
                        'force_taker': self.extreme_profit_use_taker  # 🆕 極端獲利直接用 Taker
                    })
                return  # 已觸發，不繼續檢查
        
        # ═══════════════════════════════════════════════════════════
        # 🚀 即時止盈止損判斷 (可關閉)
        # ═══════════════════════════════════════════════════════════
        
        if not self.instant_exit_enabled:
            # 即時出場已關閉，只餵資料給 AI
            return
        
        should_exit = False
        exit_reason = ""
        
        # 🔴 止損檢查 (優先)
        if pnl_usdt < -self.max_loss_usdt or pnl_pct < -self.max_loss_pct:
            should_exit = True
            exit_reason = f"⚡止損: PnL=${pnl_usdt:.2f} ({pnl_pct:.2f}%) | 門檻: -{self.max_loss_pct:.1f}%"
        
        # 🎯 追蹤止盈 (Trailing Stop) - 優先於固定止盈
        elif self.trailing_stop_enabled:
            # 更新峰值
            if pnl_pct > self.peak_pnl_pct:
                self.peak_pnl_pct = pnl_pct
            
            # 檢查是否啟動追蹤
            if pnl_pct >= self.trailing_activation_pct and not self.trailing_active:
                self.trailing_active = True
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"\n   🎯 [{timestamp}] 追蹤止盈啟動! ROI: {pnl_pct:.2f}% >= {self.trailing_activation_pct}%")
            
            # 檢查是否觸發追蹤止盈
            if self.trailing_active:
                drawdown = self.peak_pnl_pct - pnl_pct
                if drawdown >= self.trailing_distance_pct:
                    should_exit = True
                    exit_reason = f"🎯追蹤止盈: 從峰值 {self.peak_pnl_pct:.2f}% 回撤 {drawdown:.2f}% | 當前: {pnl_pct:.2f}%"
        
        # 🟢 固定止盈檢查 (金額 OR 比例) - 如果沒觸發追蹤止盈
        if not should_exit:
            if net_profit >= self.min_profit_usdt:
                should_exit = True
                exit_reason = f"⚡止盈(金額): 淨利=${net_profit:.2f} >= ${self.min_profit_usdt}"
            elif pnl_pct >= self.min_profit_pct:
                should_exit = True
                exit_reason = f"⚡止盈(比例): {pnl_pct:.2f}% >= {self.min_profit_pct:.1f}%"
        
        # ⚠️ 爆倉預警
        if not should_exit and pnl_pct <= self.liquidation_warning_pct and self.on_liquidation_warning:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"\n💀 [{timestamp}] ⚠️ 爆倉預警！ROI: {pnl_pct:.2f}%")
            await self._safe_callback(self.on_liquidation_warning, {
                'pnl_pct': pnl_pct,
                'pnl_usdt': pnl_usdt,
                'current_price': current_price
            })
        
        # 執行即時平倉
        if should_exit and self.on_instant_exit:
            self.exit_in_progress = True  # 🔥 標記平倉進行中，防止重複觸發
            self.last_exit_time = current_time
            self.instant_exits += 1
            self._reset_trailing()  # 重置追蹤止盈
            
            # 估算節省的滑點 (假設原本10秒延遲 × 0.03%/秒 波動)
            self.slippage_saved_estimate += abs(pnl_usdt) * 0.003
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"\n\n{'🚀' * 30}")
            print(f"⚡ [{timestamp}] WebSocket 即時出場!")
            print(f"   {exit_reason}")
            print(f"   峰值 ROI: {self.peak_pnl_pct:.2f}%")
            print(f"   響應時間: <1秒")
            print(f"{'🚀' * 30}\n")
            
            await self._safe_callback(self.on_instant_exit, {
                'reason': exit_reason,
                'pnl_usdt': pnl_usdt,
                'pnl_pct': pnl_pct,
                'peak_pnl_pct': self.peak_pnl_pct,
                'direction': direction,
                'current_price': current_price
            })
    
    def _reset_trailing(self):
        """重置追蹤止盈狀態 (開新倉時調用)"""
        self.peak_pnl_pct = 0.0
        self.trailing_active = False
        self.position_open_time = 0
    
    def notify_position_closed(self):
        """🆕 通知 WebSocket 倉位已關閉"""
        self._reset_trailing()
        print(f"   🔄 WebSocket 追蹤止盈已重置")
    
    async def _update_ai_bridge(self, pnl_usdt: float, pnl_pct: float, current_price: float, 
                                 entry_price: float, direction: str, leverage: int):
        """
        🆕 更新 AI Bridge 檔案，讓 AI 可以讀取即時 Testnet 狀態
        """
        import json
        from pathlib import Path
        
        bridge_file = Path(__file__).parent.parent / 'ai_wolf_bridge.json'
        
        try:
            # 讀取現有 bridge
            bridge = {}
            if bridge_file.exists():
                with open(bridge_file, 'r', encoding='utf-8') as f:
                    bridge = json.load(f)
            
            # 更新 Testnet 即時資料
            if 'wolf_to_ai' not in bridge:
                bridge['wolf_to_ai'] = {}
            
            bridge['wolf_to_ai']['websocket_realtime'] = {
                'source': 'WEBSOCKET_REALTIME',
                'timestamp': datetime.now().isoformat(),
                'has_position': abs(pnl_usdt) > 0.01 or direction != '',
                'position': {
                    'direction': direction,
                    'entry_price': entry_price,
                    'current_price': current_price,
                    'leverage': leverage,
                    'pnl_usdt': round(pnl_usdt, 2),
                    'pnl_pct': round(pnl_pct, 2)
                },
                'alert': self._get_alert_level(pnl_pct)
            }
            
            # 寫回
            with open(bridge_file, 'w', encoding='utf-8') as f:
                json.dump(bridge, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            pass  # 靜默失敗，不影響主流程
    
    def _get_alert_level(self, pnl_pct: float) -> dict:
        """根據 PnL 判斷警示等級"""
        if pnl_pct <= -50:
            return {'level': 'CRITICAL', 'message': '⚠️ 接近爆倉！建議立即平倉'}
        elif pnl_pct <= -20:
            return {'level': 'HIGH', 'message': '⚠️ 虧損嚴重，考慮止損'}
        elif pnl_pct <= -5:
            return {'level': 'MEDIUM', 'message': '📉 輕微虧損，持續觀察'}
        elif pnl_pct >= 15:
            return {'level': 'TAKE_PROFIT', 'message': '🎯 獲利豐厚，考慮止盈'}
        elif pnl_pct >= 5:
            return {'level': 'PROFIT', 'message': '📈 獲利中，可考慮部分止盈'}
        else:
            return {'level': 'NORMAL', 'message': '持倉中'}
    
    async def _on_manual_close(self, data: Dict):
        """
        處理手動平倉事件
        
        當偵測到手動或系統平倉時，同步 Paper Trading 狀態
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        reason = data.get('reason', 'UNKNOWN')
        position_side = data.get('position_side', '')
        
        print(f"\n\n{'📡' * 30}")
        print(f"🔔 [{timestamp}] 偵測到外部平倉!")
        print(f"   方向: {position_side}")
        print(f"   原因: {reason}")
        print(f"   動作: 同步 Paper Trading 狀態")
        print(f"{'📡' * 30}\n")
        
        if self.on_manual_close_detected:
            await self._safe_callback(self.on_manual_close_detected, data)
    
    async def _on_liquidation(self, data: Dict):
        """處理爆倉事件"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        print(f"\n\n{'💀' * 30}")
        print(f"💀 [{timestamp}] 爆倉事件!")
        print(f"   必須立即同步 Paper Trading")
        print(f"{'💀' * 30}\n")
        
        if self.on_manual_close_detected:
            data['reason'] = 'LIQUIDATION'
            await self._safe_callback(self.on_manual_close_detected, data)
    
    async def _on_order_update(self, data: Dict):
        """處理訂單更新 - 記錄已實現盈虧"""
        realized_pnl = data.get('realized_pnl', 0)
        if realized_pnl != 0:
            status = data.get('status', '')
            timestamp = datetime.now().strftime("%H:%M:%S")
            emoji = "✅" if realized_pnl >= 0 else "🔴"
            print(f"\n{emoji} [{timestamp}] 訂單 {status}: 已實現 ${realized_pnl:+.2f}")
    
    async def _safe_callback(self, callback: Callable, data: Dict):
        """安全執行回調"""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            print(f"⚠️ WebSocket 回調錯誤: {e}")
    
    def _run_ws_loop(self):
        """在獨立線程中運行 WebSocket 事件循環"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(self._connect_and_monitor())
        except Exception as e:
            print(f"⚠️ WebSocket 線程錯誤: {e}")
        finally:
            loop.close()
    
    async def _connect_and_monitor(self):
        """連接並監控 WebSocket"""
        self.monitor = TestnetWebSocketMonitor()
        
        # 設定回調
        self.monitor.on_pnl_update = self._on_pnl_update
        self.monitor.on_manual_close = self._on_manual_close
        self.monitor.on_liquidation = self._on_liquidation
        self.monitor.on_order_update = self._on_order_update
        
        # 連接
        while self.running:
            try:
                await self.monitor.connect()
            except Exception as e:
                print(f"⚠️ WebSocket 連接失敗: {e}")
                if self.running:
                    print("   5秒後重新連接...")
                    await asyncio.sleep(5)
    
    def start(self):
        """啟動 WebSocket 監控 (非阻塞)"""
        if self.running:
            print("⚠️ WebSocket 已在運行中")
            return
        
        self.running = True
        
        print("\n" + "=" * 60)
        print("🔌 啟動 WebSocket 即時監控")
        print("=" * 60)
        print(f"   📡 即時出場: {'✅ 啟用' if self.instant_exit_enabled else '❌ 關閉 (餵資料給 AI)'}")
        print(f"   📊 餵資料給 AI: {'✅ 啟用' if self.feed_to_ai else '❌ 關閉'}")
        if self.instant_exit_enabled:
            print(f"   止盈: ${self.min_profit_usdt} 或 {self.min_profit_pct:.1f}%")
            print(f"   止損: ${self.max_loss_usdt} 或 {self.max_loss_pct:.1f}%")
        print("=" * 60 + "\n")
        
        # 在獨立線程中運行
        self.ws_thread = threading.Thread(target=self._run_ws_loop, daemon=True)
        self.ws_thread.start()
    
    def stop(self):
        """停止 WebSocket 監控"""
        self.running = False
        if self.monitor:
            self.monitor.stop()
        
        print("\n" + "=" * 60)
        print("🛑 WebSocket 監控已停止")
        print(f"   即時出場次數: {self.instant_exits}")
        print(f"   AI 餵資料次數: {self.ai_feed_count}")
        print(f"   估計節省滑點: ${self.slippage_saved_estimate:.2f}")
        print("=" * 60 + "\n")
    
    def get_stats(self) -> Dict:
        """獲取統計資訊"""
        return {
            'instant_exits': self.instant_exits,
            'ai_feed_count': self.ai_feed_count,
            'slippage_saved_estimate': self.slippage_saved_estimate,
            'running': self.running,
            'realtime_state': self.realtime_testnet_state
        }
    
    def get_realtime_state(self) -> Dict:
        """🆕 獲取即時 Testnet 狀態 (供外部讀取)"""
        return self.realtime_testnet_state


# ==================== 測試 ====================

async def test_integration():
    """測試 WebSocket 整合"""
    integration = WebSocketIntegration()
    
    # 設定測試回調
    async def on_exit(data):
        print(f"🧪 即時出場回調: {data}")
    
    async def on_manual_close(data):
        print(f"🧪 手動平倉回調: {data}")
    
    integration.on_instant_exit = on_exit
    integration.on_manual_close_detected = on_manual_close
    
    # 啟動
    integration.start()
    
    try:
        # 保持運行
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        integration.stop()


if __name__ == "__main__":
    print("🔌 WebSocket 整合模組測試")
    print("按 Ctrl+C 停止\n")
    
    asyncio.run(test_integration())
