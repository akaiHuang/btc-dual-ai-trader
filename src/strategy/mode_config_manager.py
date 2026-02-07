"""
ModeConfigManager - 動態策略配置管理器
負責：JSON 載入、熱更新、schema 驗證、配置快取
"""
import json
import os
import time
from typing import Dict, Optional, Any
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class ModeConfigManager:
    """策略配置管理器 - 支援熱更新"""
    
    def __init__(self, config_path: str = "config/trading_strategies_dynamic.json"):
        self.config_path = config_path
        self.configs: Dict[str, dict] = {}
        self.last_mtime: float = 0
        self.last_load_time: datetime = None
        self.load_error: Optional[str] = None
        
        # 初次載入
        self.reload()
    
    def reload(self) -> bool:
        """重新載入配置檔（如果有變更）
        
        Returns:
            bool: True 表示成功載入或無變更，False 表示載入失敗
        """
        try:
            if not os.path.exists(self.config_path):
                logger.warning(f"Config file not found: {self.config_path}")
                return False
            
            # 檢查檔案修改時間
            current_mtime = os.path.getmtime(self.config_path)
            if current_mtime <= self.last_mtime:
                return True  # 無變更
            
            # 載入 JSON
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 驗證 schema
            if not self._validate_schema(data):
                logger.error(f"Config schema validation failed for {self.config_path}")
                self.load_error = "Schema validation failed"
                return False
            
            # 更新配置
            old_configs = self.configs.copy()
            self.configs = data.get('modes', {})
            self.last_mtime = current_mtime
            self.last_load_time = datetime.now()
            self.load_error = None
            
            # 報告變更
            added = set(self.configs.keys()) - set(old_configs.keys())
            removed = set(old_configs.keys()) - set(self.configs.keys())
            modified = {k for k in self.configs.keys() if k in old_configs and self.configs[k] != old_configs[k]}
            
            if added or removed or modified:
                logger.info(f"✅ Config reloaded: +{len(added)} modes, -{len(removed)} modes, ~{len(modified)} modified")
                if added:
                    logger.info(f"   Added: {', '.join(added)}")
                if removed:
                    logger.info(f"   Removed: {', '.join(removed)}")
                if modified:
                    logger.info(f"   Modified: {', '.join(modified)}")
            
            return True
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error in {self.config_path}: {e}")
            self.load_error = f"JSON decode error: {e}"
            return False
        except Exception as e:
            logger.error(f"Error loading config from {self.config_path}: {e}")
            self.load_error = str(e)
            return False
    
    def _validate_schema(self, data: dict) -> bool:
        """驗證 JSON schema
        
        基本檢查：
        - 必須有 'modes' key
        - 每個 mode 必須有 enabled, type, leverage 等基本欄位
        """
        if 'modes' not in data:
            logger.error("Missing 'modes' key in config")
            return False
        
        modes = data['modes']
        if not isinstance(modes, dict):
            logger.error("'modes' must be a dictionary")
            return False
        
        for mode_name, mode_config in modes.items():
            # 必要欄位檢查
            required_fields = ['enabled', 'type', 'leverage', 'tp_pct', 'sl_pct']
            for field in required_fields:
                if field not in mode_config:
                    logger.error(f"Mode '{mode_name}' missing required field: {field}")
                    return False
            
            # 類型檢查
            if not isinstance(mode_config['enabled'], bool):
                logger.error(f"Mode '{mode_name}': 'enabled' must be boolean")
                return False
            
            if not isinstance(mode_config['leverage'], (int, float)) or mode_config['leverage'] <= 0:
                logger.error(f"Mode '{mode_name}': 'leverage' must be positive number")
                return False
            
            if not isinstance(mode_config['tp_pct'], (int, float)) or mode_config['tp_pct'] <= 0:
                logger.error(f"Mode '{mode_name}': 'tp_pct' must be positive number")
                return False
            
            if not isinstance(mode_config['sl_pct'], (int, float)) or mode_config['sl_pct'] <= 0:
                logger.error(f"Mode '{mode_name}': 'sl_pct' must be positive number")
                return False
        
        return True
    
    def get_config(self, mode_name: str) -> Optional[dict]:
        """獲取指定模式的配置
        
        Args:
            mode_name: 模式名稱（例如 "M0_ULTRA_SAFE", "M_MICRO1_1m"）
        
        Returns:
            配置字典，若不存在或未啟用則回傳 None
        """
        config = self.configs.get(mode_name)
        if config is None:
            return None
        
        if not config.get('enabled', False):
            return None
        
        return config
    
    def is_enabled(self, mode_name: str) -> bool:
        """檢查模式是否啟用"""
        config = self.configs.get(mode_name)
        if config is None:
            return False
        return config.get('enabled', False)
    
    def get_all_enabled_modes(self) -> Dict[str, dict]:
        """獲取所有啟用的模式配置"""
        return {
            name: config 
            for name, config in self.configs.items() 
            if config.get('enabled', False)
        }
    
    def reload_if_updated(self) -> bool:
        """檢查並重新載入（如果檔案有更新）
        
        Returns:
            bool: True 表示成功或無變更，False 表示失敗
        """
        return self.reload()
    
    def get_status(self) -> dict:
        """獲取管理器狀態資訊"""
        return {
            'config_path': self.config_path,
            'last_load_time': self.last_load_time.isoformat() if self.last_load_time else None,
            'total_modes': len(self.configs),
            'enabled_modes': len([c for c in self.configs.values() if c.get('enabled', False)]),
            'load_error': self.load_error
        }
