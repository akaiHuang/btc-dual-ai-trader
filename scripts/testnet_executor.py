#!/usr/bin/env python3
"""
🚀 Testnet 交易執行器
將 Paper Trading 決策轉換為 Binance Testnet 真實交易

特點:
1. 逐倉模式 (Isolated Margin) - 每個策略獨立 100U
2. 雙向持倉 (Hedge Mode) - M🐺 和 M🐲 可同時持有不同方向
3. 寬鬆 SL 保底 (-5%) + AI 控制 TP
4. 智能主控權 - 同方向時由績效好的策略主控
5. 複利交易 - 使用當前餘額計算下單量
"""

import os
import sys
import time
import hmac
import hashlib
import json
import requests
from datetime import datetime
from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path

# ==================== 配置 ====================

# 🆕 寬鬆止損設定 (保底，AI 可以提前平倉)
DEFAULT_EMERGENCY_SL_PCT = 0.05  # 5% 緊急止損 (保底)

# 🏷️ 載入統一配置 (Maker 設定)
def _load_sync_config():
    """載入統一配置"""
    config_path = Path(__file__).parent.parent / 'config' / 'strategy_sync_config.json'
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

_SYNC_CONFIG = _load_sync_config()
_GLOBAL_SETTINGS = _SYNC_CONFIG.get('global_settings', {})

# 🏷️ Maker 訂單設定 (從統一配置讀取)
MAKER_ENABLED = _GLOBAL_SETTINGS.get('maker_enabled', True)              # 全局開關
MAKER_TIMEOUT_SECONDS = _GLOBAL_SETTINGS.get('maker_timeout_seconds', 30.0)  # Maker 超時秒數
MAKER_OFFSET_BPS = _GLOBAL_SETTINGS.get('maker_offset_bps', 1.0)              # 掛單偏移 (1 bps = 0.01%)
MAKER_FALLBACK_TO_TAKER = _GLOBAL_SETTINGS.get('maker_fallback_to_taker', False)  # 超時後改用 Taker? (預設: 否，跳過)
MAKER_SKIP_ON_VOLATILITY = _GLOBAL_SETTINGS.get('maker_skip_on_volatility', True)  # 🆕 高波動時跳過交易
MAKER_FOR_ENTRY = _GLOBAL_SETTINGS.get('maker_for_entry', True)              # 開倉用 Maker
MAKER_FOR_EXIT = _GLOBAL_SETTINGS.get('maker_for_exit', False)               # 平倉用 Maker (預設關閉)

# 🚨 市場狀態偵測閾值
VOLATILITY_SPIKE_THRESHOLD = 0.5   # 波動率 > 0.5% 視為高波動
PRICE_MOMENTUM_THRESHOLD = 0.3     # 價格動量 > 0.3% 視為突破
CASCADE_RISK_THRESHOLD = 0.7       # 瀑布風險 > 70% 避開 Maker

# 策略配置
STRATEGY_CONFIG = {
    'M🐺': {
        'name': 'Wolf',
        'emoji': '🐺',
        'initial_capital': 100.0,
        'leverage': 10,
        'sl_pct': DEFAULT_EMERGENCY_SL_PCT,  # 🆕 只保留寬鬆 SL
        'enabled': True
    },
    'M🐲': {
        'name': 'Dragon',
        'emoji': '🐲',
        'initial_capital': 100.0,
        'leverage': 10,
        'sl_pct': DEFAULT_EMERGENCY_SL_PCT,
        'enabled': True
    },
    'M🐟': {
        'name': 'Fish',
        'emoji': '🐟',
        'initial_capital': 100.0,
        'leverage': 10,
        'sl_pct': DEFAULT_EMERGENCY_SL_PCT,
        'enabled': True
    }
}

# 策略名稱對應 (Paper Trading 用的名稱 -> 這裡用的 key)
STRATEGY_NAME_MAP = {
    'M_AI_WHALE_HUNTER': 'M🐺',
    'M🐺 AI Whale Hunter': 'M🐺',
    'M🐺': 'M🐺',
    'Wolf': 'M🐺',
    
    'M_DRAGON': 'M🐲',
    'M🐲 AI Dragon': 'M🐲',
    'M🐲': 'M🐲',
    'Dragon': 'M🐲',
    
    'M_FISH_MARKET_MAKER': 'M🐟',
    'M🐟 Fish Market Maker': 'M🐟',
    'M🐟': 'M🐟',
    'Fish': 'M🐟'
}

# 文件路徑
PORTFOLIO_FILE = Path(__file__).parent.parent / 'testnet_portfolio.json'


# ==================== 數據結構 ====================

@dataclass
class StrategyPosition:
    """策略持倉狀態"""
    strategy: str
    balance: float  # 當前餘額 (USDT)
    position_amt: float = 0  # 持倉數量 (BTC)
    entry_price: float = 0  # 開倉價
    direction: str = ''  # LONG / SHORT
    leverage: int = 10
    tp_order_id: Optional[str] = None  # 止盈訂單 ID
    sl_order_id: Optional[str] = None  # 止損訂單 ID
    entry_time: str = ''
    unrealized_pnl: float = 0
    last_update: str = ''


@dataclass 
class Portfolio:
    """投資組合狀態"""
    strategies: Dict[str, StrategyPosition] = field(default_factory=dict)
    total_trades: int = 0
    total_wins: int = 0
    total_pnl: float = 0
    created_at: str = ''
    last_update: str = ''


# ==================== Testnet 執行器 ====================

class BinanceTestnetExecutor:
    """Binance Testnet 交易執行器"""
    
    def __init__(self):
        self.base_url = 'https://testnet.binancefuture.com'
        self.symbol = 'BTCUSDT'
        
        # 從 .env 讀取 API 金鑰
        self._load_api_keys()
        
        # 初始化投資組合
        self.portfolio = self._load_portfolio()
        
        # 確保帳戶設定正確
        self._setup_account()
    
    def _load_api_keys(self):
        """從 .env 讀取 API 金鑰"""
        env_path = Path(__file__).parent.parent / '.env'
        env_vars = {}
        
        if env_path.exists():
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        env_vars[key.strip()] = value.strip()
        
        self.api_key = env_vars.get('BINANCE_TESTNET_API_KEY', '')
        self.api_secret = env_vars.get('BINANCE_TESTNET_API_SECRET', '')
        
        if not self.api_key or not self.api_secret:
            raise ValueError("❌ 缺少 BINANCE_TESTNET_API_KEY 或 BINANCE_TESTNET_API_SECRET")
    
    def _sign_request(self, params: Dict) -> str:
        """簽名請求"""
        query = '&'.join([f'{k}={v}' for k, v in params.items()])
        signature = hmac.new(
            self.api_secret.encode(), 
            query.encode(), 
            hashlib.sha256
        ).hexdigest()
        return query + '&signature=' + signature
    
    def _get_headers(self) -> Dict:
        """取得請求標頭"""
        return {'X-MBX-APIKEY': self.api_key}
    
    def _load_portfolio(self) -> Portfolio:
        """載入投資組合狀態"""
        if PORTFOLIO_FILE.exists():
            try:
                with open(PORTFOLIO_FILE, 'r') as f:
                    data = json.load(f)
                
                portfolio = Portfolio(
                    total_trades=data.get('total_trades', 0),
                    total_wins=data.get('total_wins', 0),
                    total_pnl=data.get('total_pnl', 0),
                    created_at=data.get('created_at', ''),
                    last_update=data.get('last_update', '')
                )
                
                for key, pos_data in data.get('strategies', {}).items():
                    portfolio.strategies[key] = StrategyPosition(**pos_data)
                
                return portfolio
            except Exception as e:
                print(f"⚠️ 載入投資組合失敗: {e}")
        
        # 初始化新投資組合
        portfolio = Portfolio(
            created_at=datetime.now().isoformat(),
            last_update=datetime.now().isoformat()
        )
        
        for key, config in STRATEGY_CONFIG.items():
            if config['enabled']:
                portfolio.strategies[key] = StrategyPosition(
                    strategy=key,
                    balance=config['initial_capital'],
                    leverage=config['leverage']
                )
        
        self._save_portfolio(portfolio)
        return portfolio
    
    def _save_portfolio(self, portfolio: Optional[Portfolio] = None):
        """儲存投資組合狀態"""
        if portfolio is None:
            portfolio = self.portfolio
        
        portfolio.last_update = datetime.now().isoformat()
        
        data = {
            'strategies': {k: asdict(v) for k, v in portfolio.strategies.items()},
            'total_trades': portfolio.total_trades,
            'total_wins': portfolio.total_wins,
            'total_pnl': portfolio.total_pnl,
            'created_at': portfolio.created_at,
            'last_update': portfolio.last_update
        }
        
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def _setup_account(self):
        """設定帳戶 (逐倉模式 + 槓桿 + 雙向持倉)"""
        try:
            # 1. 🆕 開啟雙向持倉 (Hedge Mode)
            hedge_params = {
                'dualSidePosition': 'true',
                'timestamp': int(time.time() * 1000)
            }
            hedge_resp = requests.post(
                f'{self.base_url}/fapi/v1/positionSide/dual?{self._sign_request(hedge_params)}',
                headers=self._get_headers()
            )
            if hedge_resp.status_code == 200:
                print("✅ 雙向持倉 (Hedge Mode) 已開啟")
            elif 'No need to change position side' in hedge_resp.text:
                print("✅ 雙向持倉 (Hedge Mode) 已啟用")
            else:
                print(f"⚠️ Hedge Mode 設定: {hedge_resp.text}")
            
            time.sleep(0.2)
            
            # 2. 設定逐倉模式
            margin_params = {
                'symbol': self.symbol,
                'marginType': 'ISOLATED',
                'timestamp': int(time.time() * 1000)
            }
            margin_resp = requests.post(
                f'{self.base_url}/fapi/v1/marginType?{self._sign_request(margin_params)}',
                headers=self._get_headers()
            )
            
            # 3. 設定槓桿 (使用第一個策略的槓桿)
            leverage = list(STRATEGY_CONFIG.values())[0]['leverage']
            leverage_params = {
                'symbol': self.symbol,
                'leverage': leverage,
                'timestamp': int(time.time() * 1000)
            }
            leverage_resp = requests.post(
                f'{self.base_url}/fapi/v1/leverage?{self._sign_request(leverage_params)}',
                headers=self._get_headers()
            )
            
            print(f"✅ 帳戶設定完成: ISOLATED 模式, {leverage}x 槓桿, Hedge Mode")
            
        except Exception as e:
            print(f"⚠️ 帳戶設定警告: {e}")
    
    def _set_leverage(self, leverage: int) -> bool:
        """
        🆕 動態設定槓桿倍數
        
        Args:
            leverage: 槓桿倍數 (1-125)
            
        Returns:
            是否設定成功
        """
        try:
            leverage_params = {
                'symbol': self.symbol,
                'leverage': leverage,
                'timestamp': int(time.time() * 1000)
            }
            resp = requests.post(
                f'{self.base_url}/fapi/v1/leverage?{self._sign_request(leverage_params)}',
                headers=self._get_headers()
            )
            if resp.status_code == 200:
                print(f"   ⚡ 交易所槓桿設定為 {leverage}x")
                return True
            else:
                print(f"   ⚠️ 槓桿設定失敗: {resp.text}")
                return False
        except Exception as e:
            print(f"   ⚠️ 槓桿設定錯誤: {e}")
            return False
    
    def get_current_price(self) -> float:
        """取得當前價格"""
        resp = requests.get(f'{self.base_url}/fapi/v1/ticker/price?symbol={self.symbol}')
        return float(resp.json()['price'])
    
    def get_account_balance(self) -> float:
        """取得帳戶餘額"""
        params = {'timestamp': int(time.time() * 1000)}
        resp = requests.get(
            f'{self.base_url}/fapi/v2/balance?{self._sign_request(params)}',
            headers=self._get_headers()
        )
        
        for asset in resp.json():
            if asset['asset'] == 'USDT':
                return float(asset['balance'])
        return 0
    
    def get_position(self, position_side: str = None) -> Optional[Dict]:
        """
        取得當前持倉
        
        Args:
            position_side: 'LONG', 'SHORT', 或 None (返回所有)
        """
        params = {'timestamp': int(time.time() * 1000)}
        resp = requests.get(
            f'{self.base_url}/fapi/v2/positionRisk?{self._sign_request(params)}',
            headers=self._get_headers()
        )
        
        positions = []
        for pos in resp.json():
            if pos['symbol'] == self.symbol and float(pos['positionAmt']) != 0:
                if position_side is None or pos.get('positionSide') == position_side:
                    positions.append(pos)
        
        if position_side:
            return positions[0] if positions else None
        return positions if positions else None
    
    def get_all_positions(self) -> Dict[str, Dict]:
        """🆕 取得雙向持倉 (LONG 和 SHORT)"""
        params = {'timestamp': int(time.time() * 1000)}
        resp = requests.get(
            f'{self.base_url}/fapi/v2/positionRisk?{self._sign_request(params)}',
            headers=self._get_headers()
        )
        
        result = {'LONG': None, 'SHORT': None}
        for pos in resp.json():
            if pos['symbol'] == self.symbol:
                side = pos.get('positionSide', 'BOTH')
                amt = float(pos['positionAmt'])
                if side in ['LONG', 'SHORT'] and amt != 0:
                    result[side] = pos
        return result
    
    def _get_real_positions(self) -> list:
        """🔧 取得交易所實際持倉 (不依賴本地記錄)"""
        params = {'timestamp': int(time.time() * 1000)}
        resp = requests.get(
            f'{self.base_url}/fapi/v2/positionRisk?{self._sign_request(params)}',
            headers=self._get_headers()
        )
        
        positions = []
        if resp.status_code == 200:
            for pos in resp.json():
                if pos['symbol'] == self.symbol:
                    amt = float(pos.get('positionAmt', 0))
                    if abs(amt) > 0.001:
                        positions.append(pos)
        return positions
    
    def _force_close_residual_position(self, position_side: str, quantity: float) -> bool:
        """🔧 強制平倉殘留持倉 (用 Taker 市價單)"""
        try:
            # 決定平倉方向
            if position_side == 'LONG':
                side = 'SELL'
            elif position_side == 'SHORT':
                side = 'BUY'
            else:
                print(f"   ⚠️ 未知 positionSide: {position_side}")
                return False
            
            order_params = {
                'symbol': self.symbol,
                'side': side,
                'positionSide': position_side,
                'type': 'MARKET',
                'quantity': round(quantity, 3),
                'timestamp': int(time.time() * 1000)
            }
            
            resp = requests.post(
                f'{self.base_url}/fapi/v1/order?{self._sign_request(order_params)}',
                headers=self._get_headers()
            )
            
            if resp.status_code == 200:
                print(f"   ✅ 殘留持倉已清空: {position_side} {quantity} BTC")
                return True
            else:
                print(f"   ❌ 清空失敗: {resp.text}")
                return False
        except Exception as e:
            print(f"   ❌ 清空異常: {e}")
            return False

    def cancel_all_orders(self) -> bool:
        """取消所有掛單"""
        params = {
            'symbol': self.symbol,
            'timestamp': int(time.time() * 1000)
        }
        resp = requests.delete(
            f'{self.base_url}/fapi/v1/allOpenOrders?{self._sign_request(params)}',
            headers=self._get_headers()
        )
        return resp.status_code == 200
    
    def get_open_orders(self) -> list:
        """🏷️ 取得所有未成交訂單"""
        params = {
            'symbol': self.symbol,
            'timestamp': int(time.time() * 1000)
        }
        resp = requests.get(
            f'{self.base_url}/fapi/v1/openOrders?{self._sign_request(params)}',
            headers=self._get_headers()
        )
        if resp.status_code == 200:
            return resp.json()
        return []
    
    def get_order_status(self, order_id: int) -> Optional[Dict]:
        """🏷️ 查詢訂單狀態"""
        params = {
            'symbol': self.symbol,
            'orderId': order_id,
            'timestamp': int(time.time() * 1000)
        }
        resp = requests.get(
            f'{self.base_url}/fapi/v1/order?{self._sign_request(params)}',
            headers=self._get_headers()
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    
    def cancel_order(self, order_id: int) -> bool:
        """🏷️ 取消指定訂單"""
        params = {
            'symbol': self.symbol,
            'orderId': order_id,
            'timestamp': int(time.time() * 1000)
        }
        resp = requests.delete(
            f'{self.base_url}/fapi/v1/order?{self._sign_request(params)}',
            headers=self._get_headers()
        )
        return resp.status_code == 200
    
    def get_orderbook(self) -> Dict:
        """🏷️ 取得即時 OrderBook (最佳買賣價)"""
        resp = requests.get(f'{self.base_url}/fapi/v1/ticker/bookTicker?symbol={self.symbol}')
        if resp.status_code == 200:
            data = resp.json()
            return {
                'best_bid': float(data.get('bidPrice', 0)),
                'best_ask': float(data.get('askPrice', 0))
            }
        return {'best_bid': 0, 'best_ask': 0}
    
    def calculate_maker_price(self, direction: str, current_price: float, aggressive: bool = False) -> float:
        """
        🏷️ 計算 Maker 掛單價格
        
        Args:
            direction: 'LONG' 或 'SHORT'
            current_price: 當前價格
            aggressive: 是否激進 (更靠近市價)
        
        Returns:
            掛單價格
        """
        orderbook = self.get_orderbook()
        best_bid = orderbook['best_bid'] or current_price
        best_ask = orderbook['best_ask'] or current_price
        
        # 偏移量
        offset = MAKER_OFFSET_BPS / 10000 * current_price
        if aggressive:
            offset *= 0.5  # 激進模式：偏移量減半
        
        if direction == 'LONG':
            # 買入：掛在 bid 上方一點點 (但不超過 ask)
            maker_price = min(best_bid + offset, best_ask - 0.1)
        else:
            # 賣出：掛在 ask 下方一點點 (但不低於 bid)
            maker_price = max(best_ask - offset, best_bid + 0.1)
        
        return round(maker_price, 1)
    
    def detect_market_conditions(self) -> Dict:
        """
        🚨 偵測市場狀態，決定是否適合使用 Maker
        
        Returns:
            {
                'is_volatile': bool,       # 高波動
                'is_breakout': bool,       # 突破中
                'is_cascade_risk': bool,   # 瀑布風險
                'should_use_maker': bool,  # 建議使用 Maker?
                'reason': str              # 原因
            }
        """
        try:
            # 讀取 AI Wolf Bridge 中的市場指標
            bridge_path = Path(__file__).parent.parent / 'ai_wolf_bridge.json'
            if bridge_path.exists():
                with open(bridge_path, 'r', encoding='utf-8') as f:
                    bridge_data = json.load(f)
                
                market_data = bridge_data.get('ai_to_wolf', {}).get('market_data', {})
                
                # 1️⃣ 波動率檢查 (ATR)
                atr = market_data.get('atr_pct', 0)
                is_volatile = atr > VOLATILITY_SPIKE_THRESHOLD
                
                # 2️⃣ 突破檢查 (trap_master_mode)
                trap_mode = market_data.get('trap_master_mode', 'standard')
                is_breakout = trap_mode in ['breakout', 'momentum', 'flash_crash']
                
                # 3️⃣ 瀑布風險 (liquidation_cascade)
                cascade_info = market_data.get('liquidation_cascade', {})
                cascade_risk = cascade_info.get('cascade_probability', 0)
                is_cascade_risk = cascade_risk > CASCADE_RISK_THRESHOLD
                
                # 4️⃣ 鯨魚活動
                whale_status = market_data.get('whale_status', 'neutral')
                is_whale_active = whale_status in ['ACCUMULATING', 'DISTRIBUTING', 'AGGRESSIVE']
                
                # 5️⃣ 價格動量
                price_momentum = abs(market_data.get('price_momentum_pct', 0))
                is_fast_move = price_momentum > PRICE_MOMENTUM_THRESHOLD
                
                # 綜合判斷
                should_use_maker = True
                reasons = []
                
                if is_volatile:
                    should_use_maker = False
                    reasons.append(f"高波動 ATR={atr:.2f}%")
                
                if is_breakout:
                    should_use_maker = False
                    reasons.append(f"突破模式 ({trap_mode})")
                
                if is_cascade_risk:
                    should_use_maker = False
                    reasons.append(f"瀑布風險 {cascade_risk:.0%}")
                
                if is_fast_move:
                    should_use_maker = False
                    reasons.append(f"快速移動 {price_momentum:.2f}%")
                
                if is_whale_active:
                    # 鯨魚活躍時，仍可用 Maker 但要更激進
                    reasons.append(f"鯨魚活躍 ({whale_status})")
                
                return {
                    'is_volatile': is_volatile,
                    'is_breakout': is_breakout,
                    'is_cascade_risk': is_cascade_risk,
                    'is_fast_move': is_fast_move,
                    'is_whale_active': is_whale_active,
                    'should_use_maker': should_use_maker,
                    'reason': ', '.join(reasons) if reasons else '市場穩定，適合 Maker'
                }
            
            # 無 Bridge 資料，預設可用 Maker
            return {
                'is_volatile': False,
                'is_breakout': False,
                'is_cascade_risk': False,
                'should_use_maker': True,
                'reason': '無市場資料，使用預設 Maker'
            }
            
        except Exception as e:
            print(f"⚠️ 市場狀態偵測錯誤: {e}")
            return {
                'is_volatile': False,
                'is_breakout': False,
                'is_cascade_risk': False,
                'should_use_maker': True,
                'reason': f'偵測錯誤: {e}'
            }
    
    def _get_strategy_performance(self, strategy_key: str) -> float:
        """獲取策略績效 (用於判斷主控權)"""
        try:
            bridge_file = "ai_wolf_bridge.json" if strategy_key == 'M🐺' else "ai_dragon_bridge.json"
            project_root = Path(__file__).parent.parent
            bridge_path = project_root / bridge_file
            
            if bridge_path.exists():
                with open(bridge_path, 'r') as f:
                    bridge = json.load(f)
                fb = bridge.get('feedback_loop', {})
                win_rate = fb.get('win_rate', 50)
                total_pnl = fb.get('total_pnl', 0)
                # 綜合評分 = 勝率權重 + 盈虧權重
                return win_rate * 0.7 + (total_pnl + 10) * 3  # +10 避免負數影響
            return 50  # 預設中等
        except:
            return 50
    
    def _check_whale_trap(self, strategy_key: str, direction: str) -> dict:
        """
        🎯 主力陷阱檢測 (Whale Trap Detection)
        
        讀取 AI Bridge 中的主力策略分析，檢查是否有陷阱警告
        
        Returns:
            {
                'safe': bool,           # 是否安全開倉
                'trap_type': str,       # 陷阱類型
                'reason': str,          # 原因說明
                'confidence': float     # 信心度
            }
        """
        result = {
            'safe': True,
            'trap_type': None,
            'reason': '無陷阱警告',
            'confidence': 0
        }
        
        try:
            # 讀取 AI Bridge
            bridge_file = "ai_wolf_bridge.json" if strategy_key == 'M🐺' else "ai_dragon_bridge.json"
            project_root = Path(__file__).parent.parent
            bridge_path = project_root / bridge_file
            
            if not bridge_path.exists():
                return result
            
            with open(bridge_path, 'r', encoding='utf-8') as f:
                bridge = json.load(f)
            
            # 讀取主力策略分析
            ai_command = bridge.get('ai_to_wolf', {})
            whale_strategy = ai_command.get('whale_strategy', {})
            
            if not whale_strategy:
                return result
            
            whale_intent = whale_strategy.get('intent', 'UNKNOWN')
            optimal_action = whale_strategy.get('optimal_action', 'WAIT')
            trap_warning = whale_strategy.get('trap_warning', {})
            danger_zones = whale_strategy.get('danger_zones', [])
            confidence = whale_strategy.get('confidence', 0)
            
            # 🚨 陷阱檢測邏輯
            
            # 1. 直接陷阱警告
            if trap_warning.get('active', False):
                trap_type = trap_warning.get('type', 'UNKNOWN')
                
                # 多頭陷阱 + 想做多 = 危險
                if trap_type == 'BULL_TRAP' and direction == 'LONG':
                    result['safe'] = False
                    result['trap_type'] = 'BULL_TRAP'
                    result['reason'] = f"🚨 多頭陷阱！主力正在出貨，禁止做多"
                    result['confidence'] = confidence
                    return result
                
                # 空頭陷阱 + 想做空 = 危險
                if trap_type == 'BEAR_TRAP' and direction == 'SHORT':
                    result['safe'] = False
                    result['trap_type'] = 'BEAR_TRAP'
                    result['reason'] = f"🚨 空頭陷阱！主力正在吸籌，禁止做空"
                    result['confidence'] = confidence
                    return result
            
            # 2. 主力意圖警告 (信心度 > 60%)
            if confidence >= 60:
                # 派發中 + 做多 = 危險
                if whale_intent == 'DISTRIBUTION' and direction == 'LONG':
                    result['safe'] = False
                    result['trap_type'] = 'DISTRIBUTION'
                    result['reason'] = f"⚠️ 主力正在派發 (信心度 {confidence}%)，不建議做多"
                    result['confidence'] = confidence
                    return result
                
                # 吸籌中 + 做空 = 危險
                if whale_intent == 'ACCUMULATION' and direction == 'SHORT':
                    result['safe'] = False
                    result['trap_type'] = 'ACCUMULATION'
                    result['reason'] = f"⚠️ 主力正在吸籌 (信心度 {confidence}%)，不建議做空"
                    result['confidence'] = confidence
                    return result
                
                # 多頭擠壓準備中 + 做多 = 危險
                if whale_intent == 'LONG_SQUEEZE_SETUP' and direction == 'LONG':
                    result['safe'] = False
                    result['trap_type'] = 'LONG_SQUEEZE_SETUP'
                    result['reason'] = f"⚠️ 主力準備觸發多頭擠壓 (信心度 {confidence}%)，不建議做多"
                    result['confidence'] = confidence
                    return result
                
                # 空頭擠壓準備中 + 做空 = 危險
                if whale_intent == 'SHORT_SQUEEZE_SETUP' and direction == 'SHORT':
                    result['safe'] = False
                    result['trap_type'] = 'SHORT_SQUEEZE_SETUP'
                    result['reason'] = f"⚠️ 主力準備觸發空頭擠壓 (信心度 {confidence}%)，不建議做空"
                    result['confidence'] = confidence
                    return result
            
            # 3. 最佳行動建議檢查
            if optimal_action == 'AVOID_LONG' and direction == 'LONG':
                result['safe'] = False
                result['trap_type'] = 'AVOID_LONG'
                result['reason'] = f"⚠️ AI 建議避免做多 (意圖: {whale_intent})"
                result['confidence'] = confidence
                return result
            
            if optimal_action == 'AVOID_SHORT' and direction == 'SHORT':
                result['safe'] = False
                result['trap_type'] = 'AVOID_SHORT'
                result['reason'] = f"⚠️ AI 建議避免做空 (意圖: {whale_intent})"
                result['confidence'] = confidence
                return result
            
            # 4. 危險區域檢查
            if '追多危險' in danger_zones and direction == 'LONG':
                result['safe'] = False
                result['trap_type'] = 'DANGER_ZONE'
                result['reason'] = f"⚠️ 當前處於追多危險區域"
                result['confidence'] = confidence
                return result
            
            if '追空危險' in danger_zones and direction == 'SHORT':
                result['safe'] = False
                result['trap_type'] = 'DANGER_ZONE'
                result['reason'] = f"⚠️ 當前處於追空危險區域"
                result['confidence'] = confidence
                return result
            
            # 通過所有檢查
            result['reason'] = f"✅ 無陷阱警告 (主力意圖: {whale_intent})"
            return result
            
        except Exception as e:
            print(f"   ⚠️ 陷阱檢測錯誤: {e}")
            return result  # 錯誤時預設放行
    
    def _check_same_direction_control(self, strategy_key: str, direction: str) -> Tuple[bool, str]:
        """
        🆕 檢查同方向主控權
        當 M🐺 和 M🐲 同方向時，只有績效好的有權開倉
        
        Returns:
            (can_trade, reason)
        """
        other_key = 'M🐲' if strategy_key == 'M🐺' else 'M🐺'
        
        # 檢查另一個策略的持倉
        if other_key in self.portfolio.strategies:
            other_pos = self.portfolio.strategies[other_key]
            
            # 如果另一個策略已有相同方向的持倉
            if other_pos.position_amt != 0 and other_pos.direction == direction:
                my_perf = self._get_strategy_performance(strategy_key)
                other_perf = self._get_strategy_performance(other_key)
                
                print(f"   🔍 同方向檢查: {strategy_key}={my_perf:.1f} vs {other_key}={other_perf:.1f}")
                
                if my_perf < other_perf:
                    return False, f"⚠️ {other_key} 績效更好 ({other_perf:.1f} > {my_perf:.1f})，讓出主控權"
                else:
                    # 績效更好，可以加倉或跟隨
                    return True, f"✅ {strategy_key} 績效更好，獲得主控權"
        
        return True, "✅ 無衝突"
    
    def open_position(
        self, 
        strategy_key: str,
        direction: str,  # LONG / SHORT
        reason: str = ''
    ) -> Tuple[bool, str]:
        """
        開倉 (雙向持倉 + 寬鬆 SL 保底 + AI 控制 TP)
        
        Returns:
            (success, message)
        """
        # 確認策略存在
        if strategy_key not in self.portfolio.strategies:
            return False, f"❌ 策略 {strategy_key} 不存在"
        
        strategy_pos = self.portfolio.strategies[strategy_key]
        config = STRATEGY_CONFIG.get(strategy_key, {})
        
        # 檢查是否已有持倉
        if strategy_pos.position_amt != 0:
            return False, f"⚠️ {strategy_key} 已有持倉，跳過"
        
        # 🔧 檢查交易所是否有殘留持倉 (Maker 超時後可能部分平倉)
        real_positions = self._get_real_positions()
        for pos in real_positions:
            real_amt = float(pos.get('positionAmt', 0))
            if abs(real_amt) > 0.001:
                position_side = pos.get('positionSide', '')
                print(f"   ⚠️ 偵測到交易所殘留持倉: {position_side} {real_amt} BTC")
                print(f"   🔄 強制清空殘留持倉...")
                # 強制用 Taker 清空
                self._force_close_residual_position(position_side, abs(real_amt))
                time.sleep(0.5)  # 等待成交
        
        # 🆕 Phase 0: 主力陷阱檢測 (Whale Trap Detection)
        trap_result = self._check_whale_trap(strategy_key, direction)
        if not trap_result['safe']:
            print(f"   🚨 [陷阱偵測] {trap_result['reason']}")
            return False, f"🚨 陷阱警告: {trap_result['trap_type']}"
        
        # 🆕 檢查同方向主控權 (M🐺 vs M🐲)
        if strategy_key in ['M🐺', 'M🐲']:
            can_trade, control_reason = self._check_same_direction_control(strategy_key, direction)
            if not can_trade:
                return False, control_reason
            print(f"   {control_reason}")
        
        # 計算下單數量 (使用當前餘額複利)
        current_price = self.get_current_price()
        leverage = strategy_pos.leverage
        balance = strategy_pos.balance
        
        # 倉位價值 = 餘額 * 槓桿
        position_value = balance * leverage
        quantity = round(position_value / current_price, 3)
        
        # 最小下單量檢查
        if quantity < 0.001:
            return False, f"❌ 下單量過小: {quantity} BTC"
        
        # 🆕 雙向持倉: 指定 positionSide
        position_side = 'LONG' if direction == 'LONG' else 'SHORT'
        side = 'BUY' if direction == 'LONG' else 'SELL'
        
        # 1. 開倉 (市價單)
        order_params = {
            'symbol': self.symbol,
            'side': side,
            'positionSide': position_side,  # 🆕 雙向持倉必須指定
            'type': 'MARKET',
            'quantity': quantity,
            'timestamp': int(time.time() * 1000)
        }
        
        order_resp = requests.post(
            f'{self.base_url}/fapi/v1/order?{self._sign_request(order_params)}',
            headers=self._get_headers()
        )
        
        if order_resp.status_code != 200:
            error_text = order_resp.text
            # 🆕 PERCENT_PRICE 錯誤：Testnet 流動性不足，改用 LIMIT 單以 mark price 下單
            if '-4131' in error_text or 'PERCENT_PRICE' in error_text:
                print(f"   ⚠️ PERCENT_PRICE 限制，改用 LIMIT 單...")
                return self._open_position_with_limit_fallback(strategy_key, direction, reason, quantity, position_side, side, current_price, config)
            return False, f"❌ 開倉失敗: {error_text}"
        
        order_data = order_resp.json()
        
        # 嘗試獲取成交價格
        entry_price = float(order_data.get('avgPrice', 0))
        if entry_price == 0:
            fills = order_data.get('fills', [])
            if fills:
                entry_price = float(fills[0].get('price', current_price))
            else:
                entry_price = current_price
        
        time.sleep(0.3)
        
        # 🆕 2. 只設定寬鬆止損 (保底)，不設止盈 (AI 控制)
        sl_pct = config.get('sl_pct', DEFAULT_EMERGENCY_SL_PCT)
        
        if direction == 'LONG':
            sl_price = round(entry_price * (1 - sl_pct), 1)
            close_side = 'SELL'
        else:
            sl_price = round(entry_price * (1 + sl_pct), 1)
            close_side = 'BUY'
        
        # 止損單 (保底)
        sl_params = {
            'symbol': self.symbol,
            'side': close_side,
            'positionSide': position_side,  # 🆕 雙向持倉
            'type': 'STOP_MARKET',
            'stopPrice': sl_price,
            'closePosition': 'true',  # 🆕 平掉該方向全部持倉
            'timestamp': int(time.time() * 1000)
        }
        sl_resp = requests.post(
            f'{self.base_url}/fapi/v1/order?{self._sign_request(sl_params)}',
            headers=self._get_headers()
        )
        sl_order_id = sl_resp.json().get('orderId') if sl_resp.status_code == 200 else None
        
        # 更新策略狀態
        strategy_pos.position_amt = quantity if direction == 'LONG' else -quantity
        strategy_pos.entry_price = entry_price
        strategy_pos.direction = direction
        strategy_pos.tp_order_id = None  # 🆕 無自動止盈 (AI 控制)
        strategy_pos.sl_order_id = str(sl_order_id) if sl_order_id else None
        strategy_pos.entry_time = datetime.now().isoformat()
        strategy_pos.last_update = datetime.now().isoformat()
        
        self._save_portfolio()
        
        msg = (
            f"✅ {strategy_key} 開倉成功! [Hedge Mode + AI TP]\n"
            f"   📍 方向: {direction} ({position_side})\n"
            f"   📍 數量: {quantity} BTC\n"
            f"   📍 價格: ${entry_price:,.2f}\n"
            f"   📍 保證金: ${balance:.2f}\n"
            f"   🛡️ 保底止損: ${sl_price:,.1f} (-{sl_pct*100}%)\n"
            f"   🤖 止盈: AI 控制\n"
            f"   📝 原因: {reason}"
        )
        
        return True, msg
    
    def _open_position_with_limit_fallback(
        self,
        strategy_key: str,
        direction: str,
        reason: str,
        quantity: float,
        position_side: str,
        side: str,
        current_price: float,
        config: Dict
    ) -> Tuple[bool, str]:
        """
        🆕 PERCENT_PRICE 錯誤 fallback: 使用 LIMIT 單 + IOC
        
        當 Testnet 流動性不足導致 MARKET 單被拒絕時，
        改用 LIMIT 單以略優於 mark price 的價格下單。
        """
        strategy_pos = self.portfolio.strategies[strategy_key]
        balance = strategy_pos.balance
        
        # 🎯 使用略微激進的 LIMIT 價格 (確保成交)
        # LONG: 價格略高於當前價 (0.05%)
        # SHORT: 價格略低於當前價 (0.05%)
        price_offset = current_price * 0.0005  # 0.05% offset
        
        if direction == 'LONG':
            limit_price = round(current_price + price_offset, 1)  # 買高一點
        else:
            limit_price = round(current_price - price_offset, 1)  # 賣低一點
        
        order_params = {
            'symbol': self.symbol,
            'side': side,
            'positionSide': position_side,
            'type': 'LIMIT',
            'price': limit_price,
            'quantity': quantity,
            'timeInForce': 'IOC',  # 🆕 立即成交或取消 (不掛單等待)
            'timestamp': int(time.time() * 1000)
        }
        
        print(f"   🔄 LIMIT IOC 下單: {direction} {quantity} BTC @ ${limit_price:,.1f}")
        
        order_resp = requests.post(
            f'{self.base_url}/fapi/v1/order?{self._sign_request(order_params)}',
            headers=self._get_headers()
        )
        
        if order_resp.status_code != 200:
            return False, f"❌ LIMIT 開倉也失敗: {order_resp.text}"
        
        order_data = order_resp.json()
        filled_qty = float(order_data.get('executedQty', 0))
        
        if filled_qty < 0.001:
            return False, f"❌ LIMIT IOC 未成交 (流動性不足)"
        
        # 成交價格
        entry_price = float(order_data.get('avgPrice', 0)) or limit_price
        
        # 設定止損單
        sl_pct = config.get('sl_pct', DEFAULT_EMERGENCY_SL_PCT)
        if direction == 'LONG':
            sl_price = round(entry_price * (1 - sl_pct), 1)
            close_side = 'SELL'
        else:
            sl_price = round(entry_price * (1 + sl_pct), 1)
            close_side = 'BUY'
        
        sl_params = {
            'symbol': self.symbol,
            'side': close_side,
            'positionSide': position_side,
            'type': 'STOP_MARKET',
            'stopPrice': sl_price,
            'closePosition': 'true',
            'timestamp': int(time.time() * 1000)
        }
        sl_resp = requests.post(
            f'{self.base_url}/fapi/v1/order?{self._sign_request(sl_params)}',
            headers=self._get_headers()
        )
        sl_order_id = sl_resp.json().get('orderId') if sl_resp.status_code == 200 else None
        
        # 更新策略狀態
        strategy_pos.position_amt = filled_qty if direction == 'LONG' else -filled_qty
        strategy_pos.entry_price = entry_price
        strategy_pos.direction = direction
        strategy_pos.tp_order_id = None
        strategy_pos.sl_order_id = str(sl_order_id) if sl_order_id else None
        strategy_pos.entry_time = datetime.now().isoformat()
        strategy_pos.last_update = datetime.now().isoformat()
        
        self._save_portfolio()
        
        msg = (
            f"✅ {strategy_key} 開倉成功 [LIMIT IOC fallback]\n"
            f"   📍 方向: {direction} ({position_side})\n"
            f"   📍 數量: {filled_qty} BTC\n"
            f"   📍 價格: ${entry_price:,.2f}\n"
            f"   📍 保證金: ${balance:.2f}\n"
            f"   🛡️ 保底止損: ${sl_price:,.1f} (-{sl_pct*100}%)\n"
            f"   🤖 止盈: AI 控制\n"
            f"   📝 原因: {reason} [PERCENT_PRICE fallback]"
        )
        
        return True, msg

    def open_position_maker(
        self, 
        strategy_key: str,
        direction: str,  # LONG / SHORT
        reason: str = '',
        timeout: float = None,
        fallback_to_taker: bool = None
    ) -> Tuple[bool, str]:
        """
        🏷️ Maker 掛單開倉 (省手續費)
        
        會先偵測市場狀態，如果是突破/瀑布則自動改用 Taker
        
        Args:
            strategy_key: 策略 key
            direction: 'LONG' 或 'SHORT'
            reason: 開倉原因
            timeout: 超時秒數 (None 則用預設值)
            fallback_to_taker: 超時後是否改用 Taker
        
        Returns:
            (success, message)
        """
        if not MAKER_ENABLED:
            # Maker 關閉，直接用 Taker
            return self.open_position(strategy_key, direction, reason + " [Maker OFF]")
        
        # 🚨 市場狀態偵測：突破/瀑布/高波動時處理
        market_state = self.detect_market_conditions()
        if not market_state['should_use_maker']:
            print(f"   🚨 市場狀態偵測: {market_state['reason']}")
            
            if MAKER_SKIP_ON_VOLATILITY:
                # 🆕 跳過交易，等待下一次信號
                print(f"   → 跳過本次交易，等待市場穩定")
                return False, f"⏸️ 跳過交易 (高波動): {market_state['reason']}"
            else:
                # 改用 Taker 成交
                print(f"   → 自動改用 Taker 確保成交")
                return self.open_position(strategy_key, direction, reason + f" [Market: {market_state['reason']}]")
        
        timeout = timeout or MAKER_TIMEOUT_SECONDS
        fallback = fallback_to_taker if fallback_to_taker is not None else MAKER_FALLBACK_TO_TAKER
        
        # 確認策略存在
        if strategy_key not in self.portfolio.strategies:
            return False, f"❌ 策略 {strategy_key} 不存在"
        
        strategy_pos = self.portfolio.strategies[strategy_key]
        config = STRATEGY_CONFIG.get(strategy_key, {})
        
        # 檢查是否已有持倉
        if strategy_pos.position_amt != 0:
            return False, f"⚠️ {strategy_key} 已有持倉，跳過"
        
        # 🔧 檢查交易所是否有殘留持倉 (Maker 超時後可能部分平倉)
        real_positions = self._get_real_positions()
        for pos in real_positions:
            real_amt = float(pos.get('positionAmt', 0))
            if abs(real_amt) > 0.001:
                position_side = pos.get('positionSide', '')
                print(f"   ⚠️ 偵測到交易所殘留持倉: {position_side} {real_amt} BTC")
                print(f"   🔄 強制清空殘留持倉...")
                self._force_close_residual_position(position_side, abs(real_amt))
                time.sleep(0.5)  # 等待成交
        
        # 檢查同方向主控權
        if strategy_key in ['M🐺', 'M🐲']:
            can_trade, control_reason = self._check_same_direction_control(strategy_key, direction)
            if not can_trade:
                return False, control_reason
            print(f"   {control_reason}")
        
        # 計算下單數量
        current_price = self.get_current_price()
        leverage = strategy_pos.leverage
        balance = strategy_pos.balance
        position_value = balance * leverage
        quantity = round(position_value / current_price, 3)
        
        if quantity < 0.001:
            return False, f"❌ 下單量過小: {quantity} BTC"
        
        # 🏷️ 計算 Maker 價格 (鯨魚活躍時更激進)
        aggressive = market_state.get('is_whale_active', False)
        maker_price = self.calculate_maker_price(direction, current_price, aggressive=aggressive)
        
        # 雙向持倉: 指定 positionSide
        position_side = 'LONG' if direction == 'LONG' else 'SHORT'
        side = 'BUY' if direction == 'LONG' else 'SELL'
        
        # 1. 🏷️ 掛 LIMIT 限價單
        order_params = {
            'symbol': self.symbol,
            'side': side,
            'positionSide': position_side,
            'type': 'LIMIT',
            'price': maker_price,
            'quantity': quantity,
            'timeInForce': 'GTC',  # Good Till Cancel
            'timestamp': int(time.time() * 1000)
        }
        
        print(f"   🏷️ 掛單中... {direction} @ ${maker_price:,.1f} (當前: ${current_price:,.2f})")
        
        order_resp = requests.post(
            f'{self.base_url}/fapi/v1/order?{self._sign_request(order_params)}',
            headers=self._get_headers()
        )
        
        if order_resp.status_code != 200:
            # 掛單失敗，嘗試 Taker
            print(f"   ⚠️ 掛單失敗: {order_resp.text}")
            if fallback:
                return self.open_position(strategy_key, direction, reason + " [Maker FAIL]")
            return False, f"❌ 掛單失敗: {order_resp.text}"
        
        order_data = order_resp.json()
        order_id = order_data.get('orderId')
        
        # 2. 🏷️ 等待成交
        start_time = time.time()
        filled = False
        entry_price = maker_price
        filled_qty = 0.0  # 🆕 追蹤已成交數量
        
        while time.time() - start_time < timeout:
            time.sleep(1)  # 每秒檢查一次
            
            status = self.get_order_status(order_id)
            if status:
                order_status = status.get('status', '')
                filled_qty = float(status.get('executedQty', 0))  # 🆕 更新已成交數量
                
                if order_status == 'FILLED':
                    entry_price = float(status.get('avgPrice', maker_price))
                    filled = True
                    print(f"   ✅ Maker 成交! @ ${entry_price:,.2f}")
                    break
                elif order_status == 'PARTIALLY_FILLED':
                    # 部分成交，繼續等待
                    print(f"   ⏳ 部分成交: {filled_qty}/{quantity} BTC")
                elif order_status in ['CANCELED', 'REJECTED', 'EXPIRED']:
                    break
            
            # 顯示等待進度
            elapsed = time.time() - start_time
            if int(elapsed) % 5 == 0 and int(elapsed) > 0:
                print(f"   ⏳ 等待成交... {int(elapsed)}/{int(timeout)}s")
        
        # 3. 處理超時或部分成交
        if not filled:
            # 取消未成交的剩餘部分
            self.cancel_order(order_id)
            elapsed = time.time() - start_time
            
            # 🆕 檢查是否有部分成交
            final_status = self.get_order_status(order_id)
            if final_status:
                filled_qty = float(final_status.get('executedQty', 0))
                entry_price = float(final_status.get('avgPrice', maker_price)) if filled_qty > 0 else maker_price
            
            if filled_qty > 0:
                # ✅ 有部分成交，保留已成交的部分
                print(f"   ⏰ Maker 超時 ({elapsed:.1f}s)，保留已成交 {filled_qty} BTC")
                print(f"   ✅ 部分成交成功! @ ${entry_price:,.2f}")
                quantity = filled_qty  # 🆕 更新實際數量為已成交數量
                filled = True  # 視為成功
            else:
                # 完全沒成交
                print(f"   ⏰ Maker 超時 ({elapsed:.1f}s)，取消訂單")
                
                if fallback:
                    print(f"   🔄 改用 Taker 市價成交")
                    return self.open_position(strategy_key, direction, reason + " [Maker TIMEOUT]")
                return False, f"⏰ Maker 超時，取消開倉"
        
        # 4. 成交後設定止損
        time.sleep(0.3)
        sl_pct = config.get('sl_pct', DEFAULT_EMERGENCY_SL_PCT)
        
        if direction == 'LONG':
            sl_price = round(entry_price * (1 - sl_pct), 1)
            close_side = 'SELL'
        else:
            sl_price = round(entry_price * (1 + sl_pct), 1)
            close_side = 'BUY'
        
        sl_params = {
            'symbol': self.symbol,
            'side': close_side,
            'positionSide': position_side,
            'type': 'STOP_MARKET',
            'stopPrice': sl_price,
            'closePosition': 'true',
            'timestamp': int(time.time() * 1000)
        }
        sl_resp = requests.post(
            f'{self.base_url}/fapi/v1/order?{self._sign_request(sl_params)}',
            headers=self._get_headers()
        )
        sl_order_id = sl_resp.json().get('orderId') if sl_resp.status_code == 200 else None
        
        # 更新策略狀態
        strategy_pos.position_amt = quantity if direction == 'LONG' else -quantity
        strategy_pos.entry_price = entry_price
        strategy_pos.direction = direction
        strategy_pos.tp_order_id = None
        strategy_pos.sl_order_id = str(sl_order_id) if sl_order_id else None
        strategy_pos.entry_time = datetime.now().isoformat()
        strategy_pos.last_update = datetime.now().isoformat()
        
        self._save_portfolio()
        
        # 計算省下的手續費
        taker_fee = position_value * 0.0005  # 0.05%
        maker_fee = position_value * -0.0001  # -0.01% (返佣)
        saved = taker_fee - maker_fee
        
        msg = (
            f"✅ {strategy_key} Maker 開倉成功! 🏷️\n"
            f"   📍 方向: {direction}\n"
            f"   📍 數量: {quantity} BTC\n"
            f"   📍 成交價: ${entry_price:,.2f} (掛單價: ${maker_price:,.1f})\n"
            f"   📍 保證金: ${balance:.2f}\n"
            f"   🛡️ 保底止損: ${sl_price:,.1f} (-{sl_pct*100}%)\n"
            f"   💰 省手續費: ${saved:.2f} (Maker 返佣)\n"
            f"   📝 原因: {reason}"
        )
        
        return True, msg
    
    def close_position(
        self, 
        strategy_key: str,
        reason: str = ''
    ) -> Tuple[bool, str]:
        """
        平倉
        
        Returns:
            (success, message)
        """
        if strategy_key not in self.portfolio.strategies:
            return False, f"❌ 策略 {strategy_key} 不存在"
        
        strategy_pos = self.portfolio.strategies[strategy_key]
        
        if strategy_pos.position_amt == 0:
            return False, f"⚠️ {strategy_key} 無持倉"
        
        # 取消止盈止損單
        self.cancel_all_orders()
        time.sleep(0.2)
        
        # 平倉 (雙向持倉模式)
        current_price = self.get_current_price()
        quantity = abs(strategy_pos.position_amt)
        
        # 🆕 雙向持倉: 需要指定 positionSide
        position_side = strategy_pos.direction  # LONG 或 SHORT
        side = 'SELL' if position_side == 'LONG' else 'BUY'
        
        order_params = {
            'symbol': self.symbol,
            'side': side,
            'positionSide': position_side,  # 🆕 雙向持倉必須指定
            'type': 'MARKET',
            'quantity': quantity,
            'timestamp': int(time.time() * 1000)
        }
        
        order_resp = requests.post(
            f'{self.base_url}/fapi/v1/order?{self._sign_request(order_params)}',
            headers=self._get_headers()
        )
        
        if order_resp.status_code != 200:
            return False, f"❌ 平倉失敗: {order_resp.text}"
        
        order_data = order_resp.json()
        
        # 嘗試獲取成交價格
        exit_price = float(order_data.get('avgPrice', 0))
        if exit_price == 0:
            # 從 fills 獲取
            fills = order_data.get('fills', [])
            if fills:
                exit_price = float(fills[0].get('price', current_price))
            else:
                exit_price = current_price
        
        # 計算盈虧
        if strategy_pos.direction == 'LONG':
            pnl_pct = (exit_price - strategy_pos.entry_price) / strategy_pos.entry_price
        else:
            pnl_pct = (strategy_pos.entry_price - exit_price) / strategy_pos.entry_price
        
        pnl_usdt = strategy_pos.balance * strategy_pos.leverage * pnl_pct
        
        # 🔧 修正: 手續費根據實際倉位價值計算，而非 balance * leverage
        # Binance Testnet Taker fee: 0.04% (開倉+平倉)
        # 實際倉位價值 = 開倉價 * 數量 + 平倉價 * 數量
        # 簡化計算: position_value ≈ balance * leverage (因為這就是實際開倉價值)
        # 手續費率: 0.0004 (Taker) * 2 (雙邊)
        fee = strategy_pos.balance * strategy_pos.leverage * 0.0004 * 2
        net_pnl = pnl_usdt - fee
        
        # 更新餘額 (複利)
        new_balance = strategy_pos.balance + net_pnl
        
        # 更新統計
        self.portfolio.total_trades += 1
        if net_pnl > 0:
            self.portfolio.total_wins += 1
        self.portfolio.total_pnl += net_pnl
        
        # 重置持倉狀態
        strategy_pos.balance = max(new_balance, 0)  # 不能為負
        strategy_pos.position_amt = 0
        strategy_pos.entry_price = 0
        strategy_pos.direction = ''
        strategy_pos.tp_order_id = None
        strategy_pos.sl_order_id = None
        strategy_pos.unrealized_pnl = 0
        strategy_pos.last_update = datetime.now().isoformat()
        
        self._save_portfolio()
        
        win_emoji = '🎉' if net_pnl > 0 else '😢'
        msg = (
            f"{win_emoji} {strategy_key} 平倉完成!\n"
            f"   📍 平倉價: ${exit_price:,.2f}\n"
            f"   📍 盈虧: ${net_pnl:+.2f} ({pnl_pct*100:+.2f}%)\n"
            f"   📍 新餘額: ${new_balance:.2f}\n"
            f"   📝 原因: {reason}"
        )
        
        return True, msg
    
    def close_position_maker(
        self, 
        strategy_key: str,
        reason: str = '',
        timeout: float = None,
        fallback_to_taker: bool = None
    ) -> Tuple[bool, str]:
        """
        🏷️ Maker 掛單平倉 (省手續費)
        
        ⚠️ 注意: 平倉通常比較急，建議設短超時或直接用 Taker
        
        Args:
            strategy_key: 策略 key
            reason: 平倉原因
            timeout: 超時秒數 (預設比開倉短)
            fallback_to_taker: 超時後是否改用 Taker (平倉預設 True)
        
        Returns:
            (success, message)
        """
        if not MAKER_ENABLED:
            return self.close_position(strategy_key, reason + " [Maker OFF]")
        
        timeout = timeout or (MAKER_TIMEOUT_SECONDS / 2)  # 平倉超時減半
        fallback = fallback_to_taker if fallback_to_taker is not None else True  # 平倉預設 fallback
        
        if strategy_key not in self.portfolio.strategies:
            return False, f"❌ 策略 {strategy_key} 不存在"
        
        strategy_pos = self.portfolio.strategies[strategy_key]
        
        if strategy_pos.position_amt == 0:
            return False, f"⚠️ {strategy_key} 無持倉"
        
        # 取消止盈止損單
        self.cancel_all_orders()
        time.sleep(0.2)
        
        current_price = self.get_current_price()
        quantity = abs(strategy_pos.position_amt)
        position_side = strategy_pos.direction
        
        # 平倉方向
        close_side = 'SELL' if position_side == 'LONG' else 'BUY'
        close_direction = 'SHORT' if position_side == 'LONG' else 'LONG'
        
        # 🏷️ 計算 Maker 價格
        maker_price = self.calculate_maker_price(close_direction, current_price, aggressive=True)
        
        # 掛 LIMIT 限價單
        order_params = {
            'symbol': self.symbol,
            'side': close_side,
            'positionSide': position_side,
            'type': 'LIMIT',
            'price': maker_price,
            'quantity': quantity,
            'timeInForce': 'GTC',
            'timestamp': int(time.time() * 1000)
        }
        
        print(f"   🏷️ Maker 平倉... {close_side} @ ${maker_price:,.1f}")
        
        order_resp = requests.post(
            f'{self.base_url}/fapi/v1/order?{self._sign_request(order_params)}',
            headers=self._get_headers()
        )
        
        if order_resp.status_code != 200:
            print(f"   ⚠️ 掛單失敗: {order_resp.text}")
            if fallback:
                return self.close_position(strategy_key, reason + " [Maker FAIL]")
            return False, f"❌ 掛單失敗: {order_resp.text}"
        
        order_data = order_resp.json()
        order_id = order_data.get('orderId')
        
        # 等待成交
        start_time = time.time()
        filled = False
        exit_price = maker_price
        filled_qty = 0.0  # 🆕 追蹤已成交數量
        
        while time.time() - start_time < timeout:
            time.sleep(1)
            
            status = self.get_order_status(order_id)
            if status:
                order_status = status.get('status', '')
                filled_qty = float(status.get('executedQty', 0))  # 🆕 更新已成交數量
                
                if order_status == 'FILLED':
                    exit_price = float(status.get('avgPrice', maker_price))
                    filled = True
                    print(f"   ✅ Maker 平倉成交! @ ${exit_price:,.2f}")
                    break
                elif order_status == 'PARTIALLY_FILLED':
                    # 🆕 顯示部分成交進度
                    print(f"   ⏳ 平倉部分成交: {filled_qty}/{quantity} BTC")
                elif order_status in ['CANCELED', 'REJECTED', 'EXPIRED']:
                    break
        
        if not filled:
            self.cancel_order(order_id)
            
            # 🆕 檢查是否有部分成交
            final_status = self.get_order_status(order_id)
            if final_status:
                filled_qty = float(final_status.get('executedQty', 0))
                exit_price = float(final_status.get('avgPrice', maker_price)) if filled_qty > 0 else maker_price
            
            if filled_qty > 0:
                # ✅ 有部分成交，計算部分平倉的盈虧
                print(f"   ⏰ Maker 平倉超時，保留已成交 {filled_qty} BTC")
                print(f"   ✅ 部分平倉成功! @ ${exit_price:,.2f}")
                # 更新 quantity 為實際成交數量
                quantity = filled_qty
                filled = True
                
                # 🆕 剩餘部分用 Taker 平倉
                remaining = abs(strategy_pos.position_amt) - filled_qty
                if remaining > 0.001 and fallback:
                    print(f"   🔄 剩餘 {remaining} BTC 改用 Taker 平倉")
                    # 不在這裡平倉，讓下一次循環處理
            else:
                print(f"   ⏰ Maker 平倉超時")
                
                if fallback:
                    print(f"   🔄 改用 Taker 市價平倉")
                    return self.close_position(strategy_key, reason + " [Maker TIMEOUT]")
                return False, f"⏰ Maker 超時，取消平倉"
        
        # 計算盈虧
        if strategy_pos.direction == 'LONG':
            pnl_pct = (exit_price - strategy_pos.entry_price) / strategy_pos.entry_price
        else:
            pnl_pct = (strategy_pos.entry_price - exit_price) / strategy_pos.entry_price
        
        pnl_usdt = strategy_pos.balance * strategy_pos.leverage * pnl_pct
        
        # 🏷️ Maker 手續費 (-0.01% 返佣)
        # 開倉 Taker 0.05% + 平倉 Maker -0.01% = 0.04% 總計
        # 全 Maker: -0.01% * 2 = -0.02% (獲得返佣)
        fee = strategy_pos.balance * strategy_pos.leverage * 0.0004  # 假設開倉 Taker，平倉 Maker
        net_pnl = pnl_usdt - fee
        
        new_balance = strategy_pos.balance + net_pnl
        
        # 更新統計
        self.portfolio.total_trades += 1
        if net_pnl > 0:
            self.portfolio.total_wins += 1
        self.portfolio.total_pnl += net_pnl
        
        # 重置持倉狀態
        strategy_pos.balance = max(new_balance, 0)
        strategy_pos.position_amt = 0
        strategy_pos.entry_price = 0
        strategy_pos.direction = ''
        strategy_pos.tp_order_id = None
        strategy_pos.sl_order_id = None
        strategy_pos.unrealized_pnl = 0
        strategy_pos.last_update = datetime.now().isoformat()
        
        self._save_portfolio()
        
        # 計算省下的手續費
        taker_fee = strategy_pos.leverage * 0.0005
        maker_fee = strategy_pos.leverage * -0.0001
        saved = (taker_fee - maker_fee) * strategy_pos.balance
        
        win_emoji = '🎉' if net_pnl > 0 else '😢'
        msg = (
            f"{win_emoji} {strategy_key} Maker 平倉完成! 🏷️\n"
            f"   📍 平倉價: ${exit_price:,.2f}\n"
            f"   📍 盈虧: ${net_pnl:+.2f} ({pnl_pct*100:+.2f}%)\n"
            f"   📍 新餘額: ${new_balance:.2f}\n"
            f"   💰 省手續費: ${saved:.2f}\n"
            f"   📝 原因: {reason}"
        )
        
        return True, msg

    def sync_positions(self) -> Dict[str, str]:
        """
        同步持倉狀態 (從交易所更新) - 支援雙向持倉
        
        🆕 會自動檢測並處理被止損平倉的情況
        
        Returns:
            Dict[strategy_key, event]: 發生的事件 ('closed_by_sl', 'updated', None)
        """
        events = {}
        
        # 🆕 獲取雙向持倉
        positions = self.get_all_positions()
        current_price = self.get_current_price()
        
        for key, strategy_pos in self.portfolio.strategies.items():
            if strategy_pos.position_amt != 0 and strategy_pos.entry_price > 0:
                # 計算未實現盈虧
                if strategy_pos.direction == 'LONG':
                    unrealized_pnl = (current_price - strategy_pos.entry_price) / strategy_pos.entry_price
                else:
                    unrealized_pnl = (strategy_pos.entry_price - current_price) / strategy_pos.entry_price
                
                strategy_pos.unrealized_pnl = unrealized_pnl * strategy_pos.balance * strategy_pos.leverage
                strategy_pos.last_update = datetime.now().isoformat()
                
                # 🆕 雙向持倉檢查: 檢查該方向的持倉是否還存在
                direction_pos = positions.get(strategy_pos.direction)
                if direction_pos is None:
                    # 🆕 該方向已無持倉 - 被止損平倉了！
                    print(f"\n🛑 {key} ({strategy_pos.direction}) 已被止損平倉!")
                    print(f"   📍 開倉價: ${strategy_pos.entry_price:,.2f}")
                    print(f"   📍 當前價: ${current_price:,.2f}")
                    
                    # 計算止損後的餘額
                    sl_pct = 0.05  # 5% 止損
                    loss = strategy_pos.balance * strategy_pos.leverage * sl_pct
                    fee = strategy_pos.balance * strategy_pos.leverage * 0.0004 * 2  # 🔧 修正: Taker 0.04%
                    new_balance = max(0, strategy_pos.balance - loss - fee)
                    
                    print(f"   💸 預估虧損: ${loss + fee:.2f}")
                    print(f"   💰 新餘額: ${new_balance:.2f}")
                    
                    # 更新狀態
                    strategy_pos.balance = new_balance
                    strategy_pos.position_amt = 0
                    strategy_pos.entry_price = 0
                    strategy_pos.direction = ''
                    strategy_pos.entry_time = ''
                    strategy_pos.unrealized_pnl = 0
                    strategy_pos.tp_order_id = None
                    strategy_pos.sl_order_id = None
                    
                    # 更新統計
                    self.portfolio.total_trades += 1
                    self.portfolio.total_pnl -= (loss + fee)
                    
                    events[key] = 'closed_by_sl'
                else:
                    events[key] = 'updated'
        
        self._save_portfolio()
        return events
    
    def get_status(self) -> str:
        """取得當前狀態摘要"""
        current_price = self.get_current_price()
        
        lines = [
            "=" * 60,
            f"📊 Testnet 投資組合狀態",
            f"   BTC 價格: ${current_price:,.2f}",
            "=" * 60
        ]
        
        total_balance = 0
        total_unrealized = 0
        
        for key, pos in self.portfolio.strategies.items():
            config = STRATEGY_CONFIG.get(key, {})
            emoji = config.get('emoji', '📌')
            
            total_balance += pos.balance
            total_unrealized += pos.unrealized_pnl
            
            if pos.position_amt != 0:
                pnl_emoji = '📈' if pos.unrealized_pnl > 0 else '📉'
                lines.append(
                    f"\n{emoji} {key}:\n"
                    f"   💰 餘額: ${pos.balance:.2f}\n"
                    f"   📍 持倉: {pos.position_amt} BTC ({pos.direction})\n"
                    f"   📍 開倉價: ${pos.entry_price:,.2f}\n"
                    f"   {pnl_emoji} 未實現: ${pos.unrealized_pnl:+.2f}"
                )
            else:
                lines.append(
                    f"\n{emoji} {key}:\n"
                    f"   💰 餘額: ${pos.balance:.2f}\n"
                    f"   📍 無持倉"
                )
        
        lines.extend([
            "\n" + "-" * 60,
            f"💼 總餘額: ${total_balance:.2f}",
            f"📈 未實現盈虧: ${total_unrealized:+.2f}",
            f"📊 總交易: {self.portfolio.total_trades} 次",
            f"🎯 勝率: {self.portfolio.total_wins}/{self.portfolio.total_trades} " +
            f"({self.portfolio.total_wins/self.portfolio.total_trades*100:.1f}%)" if self.portfolio.total_trades > 0 else "",
            f"💵 累計盈虧: ${self.portfolio.total_pnl:+.2f}",
            "=" * 60
        ])
        
        return '\n'.join(lines)
    
    def reset_portfolio(self):
        """重置投資組合 (測試用)"""
        self.portfolio = Portfolio(
            created_at=datetime.now().isoformat(),
            last_update=datetime.now().isoformat()
        )
        
        for key, config in STRATEGY_CONFIG.items():
            if config['enabled']:
                self.portfolio.strategies[key] = StrategyPosition(
                    strategy=key,
                    balance=config['initial_capital'],
                    leverage=config['leverage']
                )
        
        self._save_portfolio()
        print("✅ 投資組合已重置")


# ==================== 決策轉換器 ====================

class PaperToTestnetBridge:
    """Paper Trading 決策 -> Testnet 交易橋接器"""
    
    def __init__(self, executor: BinanceTestnetExecutor, use_maker: bool = True):
        self.executor = executor
        self.last_signals: Dict[str, Dict] = {}  # 記錄每個策略的最後信號
        self.use_maker = use_maker  # 🏷️ 是否使用 Maker 掛單
    
    def process_signal(
        self,
        strategy_name: str,
        direction: str,  # LONG / SHORT / CLOSE / None
        confidence: float = 0,
        reason: str = '',
        leverage: int = None,  # 🆕 動態槓桿
        use_maker: bool = None  # 🏷️ 覆蓋全局設定
    ) -> Optional[str]:
        """
        處理 Paper Trading 信號
        
        Args:
            strategy_name: 策略名稱 (如 M_AI_WHALE_HUNTER)
            direction: 方向 (LONG/SHORT/CLOSE/None)
            confidence: 信心度
            reason: 開倉原因
            leverage: 槓桿倍數 (如果指定，會同步到 Testnet)
            
        Returns:
            執行結果訊息
        """
        # 轉換策略名稱
        strategy_key = STRATEGY_NAME_MAP.get(strategy_name)
        if not strategy_key:
            return None  # 不是我們追蹤的策略
        
        # 檢查策略是否啟用
        if strategy_key not in self.executor.portfolio.strategies:
            return None
        
        strategy_pos = self.executor.portfolio.strategies[strategy_key]
        
        # 🔧 同步槓桿 (每次開倉都強制同步到交易所，確保一致性)
        if leverage is not None:
            if leverage != strategy_pos.leverage:
                old_leverage = strategy_pos.leverage
                strategy_pos.leverage = leverage
                self.executor._save_portfolio()
                print(f"   📊 {strategy_key} 槓桿同步: {old_leverage}x → {leverage}x")
            
            # 🔧 每次開倉都同步設定到交易所 (即使 Portfolio 已一致)
            if direction in ['LONG', 'SHORT']:
                self.executor._set_leverage(leverage)
        
        # 記錄信號
        current_signal = {
            'direction': direction,
            'confidence': confidence,
            'reason': reason,
            'leverage': leverage,
            'time': datetime.now().isoformat()
        }
        
        # 🏷️ 決定是否使用 Maker (分開設定開倉/平倉)
        should_use_maker_entry = (use_maker if use_maker is not None else self.use_maker) and MAKER_FOR_ENTRY
        should_use_maker_exit = (use_maker if use_maker is not None else self.use_maker) and MAKER_FOR_EXIT
        
        # 檢查是否需要執行
        if direction in ['LONG', 'SHORT']:
            # 開倉信號
            if strategy_pos.position_amt == 0:
                # 無持倉，執行開倉
                if should_use_maker_entry and MAKER_ENABLED:
                    success, msg = self.executor.open_position_maker(
                        strategy_key, 
                        direction, 
                        reason=f"[{confidence:.1%}] {reason}"
                    )
                else:
                    success, msg = self.executor.open_position(
                        strategy_key, 
                        direction, 
                        reason=f"[{confidence:.1%}] {reason}"
                    )
                self.last_signals[strategy_key] = current_signal
                return msg  # 🔧 成功或失敗都返回訊息
                    
            elif strategy_pos.direction != direction:
                # 方向相反，先平倉再開倉
                # 🏷️ 反向平倉用 Taker (確保成交)，開倉可用 Maker
                close_success, close_msg = self.executor.close_position(
                    strategy_key,
                    reason=f"反向信號: {direction}"
                )
                
                if close_success:
                    time.sleep(0.5)
                    if should_use_maker_entry and MAKER_ENABLED:
                        open_success, open_msg = self.executor.open_position_maker(
                            strategy_key,
                            direction,
                            reason=f"[{confidence:.1%}] {reason}"
                        )
                    else:
                        open_success, open_msg = self.executor.open_position(
                            strategy_key,
                            direction,
                            reason=f"[{confidence:.1%}] {reason}"
                        )
                    self.last_signals[strategy_key] = current_signal
                    return f"{close_msg}\n\n{open_msg}"  # 🔧 成功或失敗都返回
                else:
                    return close_msg  # 🔧 平倉失敗也返回訊息
            else:
                # 🔧 已有同方向持倉
                return f"⚠️ {strategy_key} 已有 {direction} 持倉，跳過"
        
        elif direction == 'CLOSE':
            # 平倉信號
            if strategy_pos.position_amt != 0:
                # 🏷️ 平倉可選 Maker (預設關閉，因為平倉通常較急)
                if should_use_maker_exit and MAKER_ENABLED:
                    # 啟用平倉 Maker，省手續費
                    success, msg = self.executor.close_position_maker(
                        strategy_key,
                        reason=reason
                    )
                else:
                    # 用 Taker 確保成交
                    success, msg = self.executor.close_position(
                        strategy_key,
                        reason=reason
                    )
                self.last_signals[strategy_key] = current_signal
                return msg  # 🔧 成功或失敗都返回訊息
            else:
                return f"⚠️ {strategy_key} 無持倉，無需平倉"
        
        return None


# ==================== 測試 ====================

if __name__ == '__main__':
    print("🚀 Testnet 交易執行器測試")
    print("=" * 60)
    
    try:
        executor = BinanceTestnetExecutor()
        print(executor.get_status())
        
        # 測試開倉
        print("\n📌 測試 M🐺 開倉...")
        success, msg = executor.open_position('M🐺', 'LONG', reason='測試開倉')
        print(msg)
        
        time.sleep(2)
        
        # 同步狀態
        executor.sync_positions()
        print("\n" + executor.get_status())
        
    except Exception as e:
        print(f"❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
