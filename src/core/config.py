"""
配置管理模組
"""
import json
import os
from pathlib import Path
from typing import Any, Dict
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class BinanceConfig(BaseModel):
    """Binance API 配置"""
    api_key: str = Field(default="", env="BINANCE_API_KEY")
    api_secret: str = Field(default="", env="BINANCE_API_SECRET")
    testnet: bool = Field(default=True, env="BINANCE_TESTNET")
    

class DatabaseConfig(BaseModel):
    """資料庫配置"""
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "trading"
    postgres_password: str = "trading"
    postgres_db: str = "trading_db"
    
    influxdb_url: str = "http://localhost:8086"
    influxdb_token: str = ""
    influxdb_org: str = "trading"
    influxdb_bucket: str = "market_data"
    
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0


class TradingConfig(BaseModel):
    """交易配置"""
    symbol: str = "BTCUSDT"
    timeframe: str = "3m"
    leverage: int = 20
    stake_amount: float = 100.0
    max_open_trades: int = 3
    
    # 風控參數
    max_daily_drawdown: float = 0.02  # 2%
    max_consecutive_losses: int = 5
    max_position_risk: float = 0.002  # 0.2%
    
    # 止盈止損（固定值，後續由 AI 動態調整）
    stop_loss: float = 0.0025  # 0.25%
    take_profit: float = 0.0045  # 0.45%


class Config(BaseSettings):
    """主配置類別"""
    
    # 環境
    environment: str = Field(default="development")
    debug: bool = Field(default=True)
    
    # 子配置
    binance: BinanceConfig = Field(default_factory=BinanceConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "allow"  # 允許額外的環境變數
    }
    
    @classmethod
    def from_json(cls, config_path: str) -> "Config":
        """從 JSON 文件載入配置"""
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)
    
    def to_json(self, config_path: str) -> None:
        """保存配置到 JSON 文件"""
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(), f, indent=2, ensure_ascii=False)


# 全局配置實例
_config: Config | None = None


def get_config() -> Config:
    """獲取全局配置"""
    global _config
    if _config is None:
        # 優先從環境變數讀取 Binance 配置
        from dotenv import load_dotenv
        load_dotenv()
        
        config_path = Path("config/config.json")
        if config_path.exists():
            _config = Config.from_json(str(config_path))
        else:
            _config = Config()
        
        # 覆蓋環境變數中的 Binance 配置
        if os.getenv("BINANCE_API_KEY"):
            _config.binance.api_key = os.getenv("BINANCE_API_KEY")
        if os.getenv("BINANCE_API_SECRET"):
            _config.binance.api_secret = os.getenv("BINANCE_API_SECRET")
        if os.getenv("BINANCE_TESTNET"):
            _config.binance.testnet = os.getenv("BINANCE_TESTNET").lower() == "true"
    
    return _config


def set_config(config: Config) -> None:
    """設置全局配置"""
    global _config
    _config = config
