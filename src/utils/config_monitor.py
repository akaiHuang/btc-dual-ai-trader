"""
配置熱重載監控器 (Config Hot-Reload Monitor)
==========================================

功能：
- 監控配置檔案變更
- 自動重載並驗證配置
- 支援回滾機制
- 不停機動態更新策略參數

重要性：⭐⭐⭐⭐
用途：交易過程中動態調整策略參數
"""

import json
import os
import time
import shutil
from pathlib import Path
from typing import Dict, Optional, Callable
from datetime import datetime


class ConfigMonitor:
    """配置檔案熱重載監控器"""
    
    def __init__(self, config_path: str, check_interval: int = 3, 
                 on_reload: Optional[Callable] = None):
        """
        初始化配置監控器
        
        Args:
            config_path: 配置檔案路徑
            check_interval: 檢查間隔（秒）
            on_reload: 配置重載後的回調函數
        """
        self.config_path = Path(config_path)
        self.check_interval = check_interval
        self.on_reload = on_reload
        
        # 確保配置檔案存在
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置檔案不存在: {config_path}")
        
        # 記錄最後修改時間
        self.last_modified = self.config_path.stat().st_mtime
        
        # 備份目錄
        self.backup_dir = self.config_path.parent / "backups"
        self.backup_dir.mkdir(exist_ok=True)
        
        # 當前配置
        self.current_config = self._load_config()
        
        # 創建初始備份
        self._create_backup("initial")
        
        print(f"📡 配置監控器已啟動")
        print(f"   監控檔案: {self.config_path}")
        print(f"   檢查間隔: {self.check_interval} 秒")
        print(f"   備份目錄: {self.backup_dir}")
    
    def _load_config(self) -> dict:
        """載入配置檔案"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            return config
        except json.JSONDecodeError as e:
            raise ValueError(f"配置檔案 JSON 格式錯誤: {e}")
        except Exception as e:
            raise Exception(f"載入配置失敗: {e}")
    
    def _validate_config(self, config: dict) -> tuple[bool, list[str]]:
        """
        驗證配置檔案有效性
        
        Returns:
            (是否有效, 錯誤訊息列表)
        """
        errors = []
        
        # 檢查必要欄位
        if 'strategies' not in config:
            errors.append("缺少 'strategies' 欄位")
            return False, errors
        
        if 'global_settings' not in config:
            errors.append("缺少 'global_settings' 欄位")
        
        # 檢查每個策略的必要欄位
        for mode_key, strategy in config['strategies'].items():
            required_fields = ['name', 'emoji', 'leverage', 'position_size', 'risk_control']
            for field in required_fields:
                if field not in strategy:
                    errors.append(f"策略 {mode_key} 缺少 '{field}' 欄位")
            
            # 檢查數值範圍
            if 'leverage' in strategy:
                if not isinstance(strategy['leverage'], int) or strategy['leverage'] < 1:
                    errors.append(f"策略 {mode_key} 的 leverage 必須是正整數")
            
            if 'position_size' in strategy:
                if not (0 < strategy['position_size'] <= 1):
                    errors.append(f"策略 {mode_key} 的 position_size 必須在 (0, 1] 範圍內")
        
        return len(errors) == 0, errors
    
    def _create_backup(self, suffix: str = ""):
        """創建配置檔案備份"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if suffix:
            backup_name = f"{self.config_path.stem}_{suffix}_{timestamp}.json"
        else:
            backup_name = f"{self.config_path.stem}_backup_{timestamp}.json"
        
        backup_path = self.backup_dir / backup_name
        shutil.copy2(self.config_path, backup_path)
        return backup_path
    
    def _rollback(self, backup_path: Path):
        """從備份回滾配置"""
        shutil.copy2(backup_path, self.config_path)
        print(f"🔄 已回滾配置: {backup_path.name}")
    
    def check_for_updates(self) -> bool:
        """
        檢查配置檔案是否有更新
        
        Returns:
            是否成功重載配置
        """
        try:
            # 檢查檔案修改時間
            current_modified = self.config_path.stat().st_mtime
            
            if current_modified <= self.last_modified:
                return False
            
            print()
            print("=" * 80)
            print("🔄 檢測到配置檔案變更")
            print("=" * 80)
            print(f"⏰ 檢測時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 創建變更前備份
            backup_path = self._create_backup("before_change")
            print(f"💾 已創建備份: {backup_path.name}")
            
            # 載入新配置
            new_config = self._load_config()
            
            # 驗證新配置
            is_valid, errors = self._validate_config(new_config)
            
            if not is_valid:
                print()
                print("❌ 配置驗證失敗:")
                for error in errors:
                    print(f"   • {error}")
                print()
                print("🔄 正在回滾到上一個有效配置...")
                self._rollback(backup_path)
                return False
            
            # 更新配置
            self.current_config = new_config
            self.last_modified = current_modified
            
            # 顯示變更摘要
            self._print_config_changes(new_config)
            
            # 調用回調函數
            if self.on_reload:
                self.on_reload(new_config)
            
            print()
            print("✅ 配置已成功重載")
            print("=" * 80)
            print()
            
            return True
            
        except json.JSONDecodeError as e:
            print()
            print(f"❌ 配置檔案 JSON 格式錯誤: {e}")
            print("🔄 保持使用當前配置")
            print()
            return False
            
        except Exception as e:
            print()
            print(f"❌ 重載配置失敗: {e}")
            print("🔄 保持使用當前配置")
            print()
            return False
    
    def _print_config_changes(self, new_config: dict):
        """顯示配置變更摘要"""
        print()
        print("📊 配置變更摘要:")
        print("-" * 80)
        
        old_strategies = self.current_config.get('strategies', {})
        new_strategies = new_config.get('strategies', {})
        
        # 檢查啟用/禁用狀態變更
        for mode_key in set(old_strategies.keys()) | set(new_strategies.keys()):
            old_enabled = old_strategies.get(mode_key, {}).get('enabled', False)
            new_enabled = new_strategies.get(mode_key, {}).get('enabled', False)
            
            if mode_key not in old_strategies:
                print(f"   ✨ 新增策略: {mode_key}")
            elif mode_key not in new_strategies:
                print(f"   🗑️  移除策略: {mode_key}")
            elif old_enabled != new_enabled:
                status = "啟用" if new_enabled else "禁用"
                emoji = "✅" if new_enabled else "❌"
                print(f"   {emoji} {mode_key}: {status}")
            
            # 檢查參數變更
            if mode_key in old_strategies and mode_key in new_strategies:
                old_strat = old_strategies[mode_key]
                new_strat = new_strategies[mode_key]
                
                # 檢查槓桿變更
                if old_strat.get('leverage') != new_strat.get('leverage'):
                    print(f"   ⚡ {mode_key} 槓桿: {old_strat.get('leverage')}x → {new_strat.get('leverage')}x")
                
                # 檢查風控參數變更
                old_rc = old_strat.get('risk_control', {})
                new_rc = new_strat.get('risk_control', {})
                
                if old_rc.get('vpin_threshold') != new_rc.get('vpin_threshold'):
                    print(f"   🔒 {mode_key} VPIN閾值: {old_rc.get('vpin_threshold')} → {new_rc.get('vpin_threshold')}")
                
                if old_rc.get('stop_loss') != new_rc.get('stop_loss'):
                    print(f"   🛑 {mode_key} 止損: {old_rc.get('stop_loss')*100:.1f}% → {new_rc.get('stop_loss')*100:.1f}%")
                
                if old_rc.get('take_profit') != new_rc.get('take_profit'):
                    print(f"   🎯 {mode_key} 止盈: {old_rc.get('take_profit')*100:.1f}% → {new_rc.get('take_profit')*100:.1f}%")
        
        print("-" * 80)
    
    def get_current_config(self) -> dict:
        """獲取當前配置"""
        return self.current_config.copy()
    
    def start_monitoring(self, duration_seconds: Optional[int] = None):
        """
        開始監控（阻塞模式）
        
        Args:
            duration_seconds: 監控時長（秒），None 表示無限期監控
        """
        print(f"🚀 開始監控配置變更...")
        
        start_time = time.time()
        
        try:
            while True:
                self.check_for_updates()
                time.sleep(self.check_interval)
                
                # 檢查是否達到時長限制
                if duration_seconds and (time.time() - start_time) >= duration_seconds:
                    print(f"⏰ 監控時長已達 {duration_seconds} 秒，停止監控")
                    break
                    
        except KeyboardInterrupt:
            print()
            print("⚠️ 監控已手動停止")


if __name__ == "__main__":
    # 測試用例
    config_path = "config/trading_strategies_dev.json"
    
    def on_config_reload(new_config):
        print(f"🔔 配置重載回調: 共 {len(new_config['strategies'])} 個策略")
    
    monitor = ConfigMonitor(config_path, check_interval=3, on_reload=on_config_reload)
    monitor.start_monitoring()
