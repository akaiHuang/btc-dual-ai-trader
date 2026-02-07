#!/usr/bin/env python3
"""
🔗 連鎖止盈模擬交易系統 v2.0
Chain Take-Profit Trading Simulator

功能：
- 雙向機率預測
- ATR 動態止損
- 連鎖止盈 (TP1 → 新TP1.1 → 新TP1.1.1...)
- 移動止損保護利潤
- 完整分支策略發展圖記錄

作者：AI Trading System
日期：2025-12-05
"""

import requests
import json
import time
import os
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any
import signal
import sys
from copy import deepcopy

# ==================== 終端顏色 ====================
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'
    
    @classmethod
    def long(cls, text: str) -> str:
        return f"{cls.BOLD}{cls.GREEN}{text}{cls.RESET}"
    
    @classmethod
    def short(cls, text: str) -> str:
        return f"{cls.BOLD}{cls.RED}{text}{cls.RESET}"
    
    @classmethod
    def profit(cls, value: float) -> str:
        return f"{cls.GREEN}${value:+.2f}{cls.RESET}" if value >= 0 else f"{cls.RED}${value:+.2f}{cls.RESET}"
    
    @classmethod
    def pct(cls, value: float) -> str:
        return f"{cls.GREEN}{value:+.2f}%{cls.RESET}" if value >= 0 else f"{cls.RED}{value:+.2f}%{cls.RESET}"

# ==================== 配置 ====================
CONFIG = {
    'symbol': 'BTCUSDT',
    'initial_balance': 100.0,
    'leverage': 50,
    'fee_rate': 0.0002,
    'interval_seconds': 10,  # 監控間隔（秒）
    'analysis_interval': 15,  # 分析間隔（分鐘）
    'position_size_pct': 0.95,
    # ATR 設定
    'atr_period': 14,
    'atr_sl_multiplier': 1.5,  # 止損 = 1.5 ATR
    'atr_tp_multiplier': 1.0,  # TP1 = 1.0 ATR
    'atr_tp2_multiplier': 1.7, # TP2 = 1.7 ATR
    # 連鎖止盈設定
    'tp1_close_pct': 50,       # TP1 平倉比例
    'chain_tp_enabled': True,   # 啟用連鎖止盈
    'max_chain_levels': 5,      # 最大連鎖層級
    'chain_tp_ratio': 0.7,      # 每級TP距離縮短比例
    'trailing_sl_enabled': True, # 啟用移動止損
    'log_dir': 'logs/chain_tp_simulator',
}

# ==================== 數據結構 ====================
@dataclass
class TradePlan:
    """交易計劃"""
    timestamp: str
    direction: str
    long_probability: float
    short_probability: float
    entry_market: float
    entry_limit: float
    stop_loss: float
    invalidation: float
    tp1: float
    tp2: float
    atr: float
    risk_reward: float
    score: float
    factors: List[str]
    market_data: Dict[str, Any]

@dataclass
class TPBranch:
    """止盈分支節點"""
    level: int
    tp_price: float
    sl_price: float
    close_pct: float
    remaining_pct: float
    triggered: bool = False
    trigger_time: Optional[str] = None
    trigger_price: Optional[float] = None
    pnl: Optional[float] = None
    children: List['TPBranch'] = field(default_factory=list)

@dataclass
class ChainPosition:
    """連鎖持倉"""
    id: int
    open_time: str
    direction: str
    entry_price: float
    initial_size: float  # 初始倉位
    current_size: float  # 當前倉位
    margin_used: float
    leverage: int
    
    # 動態止損止盈
    current_sl: float
    invalidation: float
    
    # 分支樹
    tp_tree: TPBranch
    
    # 狀態
    status: str  # OPEN, PARTIAL, CLOSED
    total_pnl: float = 0.0
    total_fee: float = 0.0
    close_history: List[Dict] = field(default_factory=list)
    
    # 原始計劃
    plan: Optional[TradePlan] = None

# ==================== 市場分析器 ====================
class MarketAnalyzer:
    def __init__(self, symbol: str = 'BTCUSDT'):
        self.symbol = symbol
        self.base_url = 'https://fapi.binance.com'
    
    def get_price(self) -> float:
        resp = requests.get(f'{self.base_url}/fapi/v1/ticker/price', 
                          params={'symbol': self.symbol}, timeout=10)
        return float(resp.json()['price'])
    
    def get_klines(self, interval: str, limit: int = 30) -> list:
        resp = requests.get(f'{self.base_url}/fapi/v1/klines',
                          params={'symbol': self.symbol, 'interval': interval, 'limit': limit},
                          timeout=10)
        return resp.json()
    
    def calculate_atr(self, period: int = 14) -> float:
        """計算 ATR (Average True Range)"""
        klines = self.get_klines('15m', period + 1)
        tr_list = []
        
        for i in range(1, len(klines)):
            high = float(klines[i][2])
            low = float(klines[i][3])
            prev_close = float(klines[i-1][4])
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            tr_list.append(tr)
        
        return sum(tr_list[-period:]) / period if tr_list else 0
    
    def get_obi(self) -> float:
        resp = requests.get(f'{self.base_url}/fapi/v1/depth',
                          params={'symbol': self.symbol, 'limit': 20}, timeout=10)
        data = resp.json()
        bids = [[float(p), float(q)] for p, q in data['bids']]
        asks = [[float(p), float(q)] for p, q in data['asks']]
        total_bid = sum(q for p, q in bids[:10])
        total_ask = sum(q for p, q in asks[:10])
        return (total_bid - total_ask) / (total_bid + total_ask)
    
    def calculate_rsi(self, closes: list, period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [max(0, c) for c in changes[-period:]]
        losses = [abs(min(0, c)) for c in changes[-period:]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        return 100 - (100 / (1 + avg_gain / avg_loss))
    
    def calculate_ema(self, data: list, period: int) -> float:
        if len(data) < period:
            return sum(data) / len(data) if data else 0
        multiplier = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for p in data[period:]:
            ema_val = (p - ema_val) * multiplier + ema_val
        return ema_val
    
    def get_trend(self, interval: str) -> str:
        klines = self.get_klines(interval, 25)
        closes = [float(k[4]) for k in klines]
        ema9 = self.calculate_ema(closes, 9)
        ema21 = self.calculate_ema(closes, 21)
        if ema9 > ema21 * 1.002:
            return 'UP'
        elif ema9 < ema21 * 0.998:
            return 'DOWN'
        return 'FLAT'
    
    def get_support_resistance(self, klines: list) -> tuple:
        """計算支撐阻力位"""
        highs = [float(k[2]) for k in klines[-20:]]
        lows = [float(k[3]) for k in klines[-20:]]
        
        resistance = max(highs)
        support = min(lows)
        
        return support, resistance
    
    def analyze(self, config: dict) -> TradePlan:
        """完整市場分析，生成交易計劃"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 基礎數據
        price = self.get_price()
        atr = self.calculate_atr(config['atr_period'])
        obi = self.get_obi()
        
        # K線和技術指標
        klines = self.get_klines('15m', 30)
        closes = [float(k[4]) for k in klines]
        rsi = self.calculate_rsi(closes)
        ema9 = self.calculate_ema(closes, 9)
        ema21 = self.calculate_ema(closes, 21)
        
        # 多時間框架趨勢
        trend_15m = self.get_trend('15m')
        trend_1h = self.get_trend('1h')
        trend_4h = self.get_trend('4h')
        
        # 支撐阻力
        support, resistance = self.get_support_resistance(klines)
        
        # 計算評分和機率
        score, factors = self._calculate_score(obi, rsi, ema9, ema21, trend_4h, price, support, resistance)
        
        # 轉換為機率
        # score 範圍大約 -8 到 +8，轉換為 0-100%
        long_prob = min(95, max(5, 50 + score * 6))
        short_prob = 100 - long_prob
        
        # 決定方向
        if score >= 1.5:
            direction = 'LONG'
        elif score <= -1.5:
            direction = 'SHORT'
        else:
            direction = 'NEUTRAL'
        
        # 計算進出場價格
        if direction == 'LONG':
            entry_market = price
            entry_limit = price * 0.998  # 限價低 0.2%
            stop_loss = price - atr * config['atr_sl_multiplier']
            invalidation = support * 0.998  # 跌破支撐失效
            tp1 = price + atr * config['atr_tp_multiplier']
            tp2 = price + atr * config['atr_tp2_multiplier']
        elif direction == 'SHORT':
            entry_market = price
            entry_limit = price * 1.002  # 限價高 0.2%
            stop_loss = price + atr * config['atr_sl_multiplier']
            invalidation = resistance * 1.002  # 突破阻力失效
            tp1 = price - atr * config['atr_tp_multiplier']
            tp2 = price - atr * config['atr_tp2_multiplier']
        else:
            entry_market = price
            entry_limit = price
            stop_loss = price
            invalidation = price
            tp1 = price
            tp2 = price
        
        # 風險報酬比
        risk = abs(price - stop_loss)
        reward = abs(tp2 - price)
        risk_reward = reward / risk if risk > 0 else 0
        
        market_data = {
            'price': price,
            'obi': obi,
            'rsi': rsi,
            'ema9': ema9,
            'ema21': ema21,
            'trend_15m': trend_15m,
            'trend_1h': trend_1h,
            'trend_4h': trend_4h,
            'support': support,
            'resistance': resistance,
        }
        
        return TradePlan(
            timestamp=timestamp,
            direction=direction,
            long_probability=long_prob,
            short_probability=short_prob,
            entry_market=entry_market,
            entry_limit=entry_limit,
            stop_loss=stop_loss,
            invalidation=invalidation,
            tp1=tp1,
            tp2=tp2,
            atr=atr,
            risk_reward=risk_reward,
            score=score,
            factors=factors,
            market_data=market_data
        )
    
    def _calculate_score(self, obi, rsi, ema9, ema21, trend_4h, price, support, resistance) -> tuple:
        score = 0
        factors = []
        
        # OBI
        if obi > 0.3:
            score += 1
            factors.append('OBI買盤+1')
        elif obi < -0.3:
            score -= 1
            factors.append('OBI賣盤-1')
        
        # RSI
        if rsi < 30:
            score += 2
            factors.append(f'RSI超賣({rsi:.0f})+2')
        elif rsi > 70:
            score -= 2
            factors.append(f'RSI超買({rsi:.0f})-2')
        elif rsi < 40:
            score += 0.5
            factors.append('RSI偏低+0.5')
        elif rsi > 60:
            score -= 0.5
            factors.append('RSI偏高-0.5')
        
        # EMA
        if ema9 > ema21 * 1.002:
            score += 1
            factors.append('EMA金叉+1')
        elif ema9 < ema21 * 0.998:
            score -= 1
            factors.append('EMA死叉-1')
        
        # 4H趨勢
        if trend_4h == 'UP':
            score += 2
            factors.append('4H上升+2')
        elif trend_4h == 'DOWN':
            score -= 2
            factors.append('4H下降-2')
        
        # 支撐阻力位置
        dist_to_support = (price - support) / price * 100
        dist_to_resistance = (resistance - price) / price * 100
        
        if dist_to_support < 0.5:  # 接近支撐
            score += 1
            factors.append('接近支撐+1')
        if dist_to_resistance < 0.5:  # 接近阻力
            score -= 1
            factors.append('接近阻力-1')
        
        return score, factors


# ==================== 連鎖止盈模擬器 ====================
class ChainTPSimulator:
    def __init__(self, config: dict = CONFIG):
        self.config = config
        self.analyzer = MarketAnalyzer(config['symbol'])
        
        self.balance = config['initial_balance']
        self.initial_balance = config['initial_balance']
        self.current_position: Optional[ChainPosition] = None
        
        self.trade_history: List[ChainPosition] = []
        self.plan_history: List[TradePlan] = []
        
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0.0
        self.total_fees = 0.0
        self.max_drawdown = 0.0
        self.peak_balance = config['initial_balance']
        
        self.running = False
        self.last_analysis_time = 0
        
        # 創建日誌目錄
        os.makedirs(config['log_dir'], exist_ok=True)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(config['log_dir'], f'session_{self.session_id}.json')
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        print('\n\n⚠️ 收到停止信號，正在安全關閉...')
        self.running = False
    
    def _log(self, message: str, level: str = 'INFO'):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f'[{timestamp}] [{level}] {message}')
    
    def _create_tp_tree(self, direction: str, entry: float, tp1: float, tp2: float, 
                        sl: float, atr: float) -> TPBranch:
        """創建止盈分支樹"""
        
        # 根節點 (TP1)
        root = TPBranch(
            level=0,
            tp_price=tp1,
            sl_price=sl,
            close_pct=self.config['tp1_close_pct'],
            remaining_pct=100,
            children=[]
        )
        
        # TP2 作為平行分支
        tp2_branch = TPBranch(
            level=0,
            tp_price=tp2,
            sl_price=sl,
            close_pct=100,  # 到達TP2全部平倉
            remaining_pct=100,
            children=[]
        )
        
        # 如果啟用連鎖止盈，為 TP1 創建子節點
        if self.config['chain_tp_enabled']:
            self._add_chain_children(root, direction, tp1, atr, 1)
        
        return root
    
    def _add_chain_children(self, parent: TPBranch, direction: str, 
                           parent_tp: float, atr: float, level: int):
        """遞歸添加連鎖止盈子節點"""
        if level >= self.config['max_chain_levels']:
            return
        
        # 計算新的 TP 距離（逐級縮短）
        chain_distance = atr * self.config['atr_tp_multiplier'] * (self.config['chain_tp_ratio'] ** level)
        
        if direction == 'LONG':
            new_tp = parent_tp + chain_distance
            new_sl = parent_tp - chain_distance * 0.3  # 移動止損到上一個TP附近
        else:
            new_tp = parent_tp - chain_distance
            new_sl = parent_tp + chain_distance * 0.3
        
        child = TPBranch(
            level=level,
            tp_price=new_tp,
            sl_price=new_sl,
            close_pct=50,  # 每級平倉剩餘的50%
            remaining_pct=parent.remaining_pct * (100 - parent.close_pct) / 100,
            children=[]
        )
        
        parent.children.append(child)
        
        # 繼續遞歸
        self._add_chain_children(child, direction, new_tp, atr, level + 1)
    
    def _open_position(self, plan: TradePlan):
        """開倉"""
        price = plan.entry_market
        
        margin = self.balance * self.config['position_size_pct']
        notional = margin * self.config['leverage']
        size = notional / price
        
        fee = notional * self.config['fee_rate']
        
        # 創建止盈樹
        tp_tree = self._create_tp_tree(
            plan.direction, price, plan.tp1, plan.tp2, 
            plan.stop_loss, plan.atr
        )
        
        position = ChainPosition(
            id=self.total_trades + 1,
            open_time=plan.timestamp,
            direction=plan.direction,
            entry_price=price,
            initial_size=size,
            current_size=size,
            margin_used=margin,
            leverage=self.config['leverage'],
            current_sl=plan.stop_loss,
            invalidation=plan.invalidation,
            tp_tree=tp_tree,
            status='OPEN',
            total_fee=fee,
            plan=plan
        )
        
        self.current_position = position
        self.balance -= fee
        self.total_fees += fee
        
        dir_str = Colors.long('🟢 LONG') if plan.direction == 'LONG' else Colors.short('🔴 SHORT')
        self._log(f'🚀 開倉 {dir_str} @ ${price:,.2f}')
        self._log(f'   數量: {size:.6f} BTC | 保證金: ${margin:.2f}')
    
    def _check_and_execute_tp(self, current_price: float) -> bool:
        """檢查並執行止盈，返回是否完全平倉"""
        pos = self.current_position
        if pos is None:
            return False
        
        # 檢查止損
        if pos.direction == 'LONG':
            if current_price <= pos.current_sl:
                return self._close_position(current_price, '止損')
            if current_price <= pos.invalidation:
                return self._close_position(current_price, '信號失效')
        else:
            if current_price >= pos.current_sl:
                return self._close_position(current_price, '止損')
            if current_price >= pos.invalidation:
                return self._close_position(current_price, '信號失效')
        
        # 遞歸檢查止盈樹
        closed = self._check_tp_branch(pos.tp_tree, current_price, pos)
        
        if pos.current_size <= 0:
            pos.status = 'CLOSED'
            return True
        
        return False
    
    def _check_tp_branch(self, branch: TPBranch, current_price: float, 
                        pos: ChainPosition) -> bool:
        """遞歸檢查止盈分支"""
        if branch.triggered:
            # 已觸發，檢查子節點
            for child in branch.children:
                if self._check_tp_branch(child, current_price, pos):
                    return True
            return False
        
        # 檢查是否觸發
        triggered = False
        if pos.direction == 'LONG':
            if current_price >= branch.tp_price:
                triggered = True
        else:
            if current_price <= branch.tp_price:
                triggered = True
        
        if triggered:
            branch.triggered = True
            branch.trigger_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            branch.trigger_price = current_price
            
            # 計算平倉數量
            close_size = pos.current_size * (branch.close_pct / 100)
            
            # 計算盈虧
            if pos.direction == 'LONG':
                pnl = (current_price - pos.entry_price) * close_size
            else:
                pnl = (pos.entry_price - current_price) * close_size
            
            # 手續費
            fee = close_size * current_price * self.config['fee_rate']
            pnl -= fee
            
            branch.pnl = pnl
            pos.total_pnl += pnl
            pos.total_fee += fee
            pos.current_size -= close_size
            self.balance += pnl + (pos.margin_used * (branch.close_pct / 100))
            self.total_fees += fee
            self.total_pnl += pnl
            
            # 記錄
            pos.close_history.append({
                'level': branch.level,
                'time': branch.trigger_time,
                'price': current_price,
                'size': close_size,
                'pnl': pnl,
                'remaining_size': pos.current_size
            })
            
            # 更新移動止損
            if self.config['trailing_sl_enabled'] and branch.children:
                pos.current_sl = branch.children[0].sl_price
            
            level_str = f"TP{branch.level+1}" if branch.level == 0 else f"TP1.{'1.' * branch.level}"
            self._log(f'🎯 {level_str} 觸發 @ ${current_price:,.2f} | '
                     f'平倉 {branch.close_pct}% | 盈虧: {Colors.profit(pnl)}')
            
            if pos.current_size > 0:
                self._log(f'   剩餘倉位: {pos.current_size:.6f} BTC | 新止損: ${pos.current_sl:,.2f}')
            
            return pos.current_size <= 0
        
        return False
    
    def _close_position(self, price: float, reason: str) -> bool:
        """完全平倉"""
        pos = self.current_position
        if pos is None:
            return False
        
        # 計算剩餘倉位盈虧
        if pos.direction == 'LONG':
            pnl = (price - pos.entry_price) * pos.current_size
        else:
            pnl = (pos.entry_price - price) * pos.current_size
        
        fee = pos.current_size * price * self.config['fee_rate']
        pnl -= fee
        
        # 更新
        remaining_margin_pct = pos.current_size / pos.initial_size
        returned_margin = pos.margin_used * remaining_margin_pct
        
        self.balance += returned_margin + pnl
        self.total_pnl += pnl
        self.total_fees += fee
        
        pos.total_pnl += pnl
        pos.total_fee += fee
        pos.current_size = 0
        pos.status = 'CLOSED'
        
        pos.close_history.append({
            'level': 'FINAL',
            'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'price': price,
            'size': 0,
            'pnl': pnl,
            'reason': reason,
            'remaining_size': 0
        })
        
        # 統計
        self.total_trades += 1
        if pos.total_pnl > 0:
            self.winning_trades += 1
            emoji = '✅'
        else:
            emoji = '❌'
        
        # 最大回撤
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd = (self.peak_balance - self.balance) / self.peak_balance
        if dd > self.max_drawdown:
            self.max_drawdown = dd
        
        self._log(f'{emoji} 完全平倉 @ ${price:,.2f} | 原因: {reason}')
        self._log(f'   總盈虧: {Colors.profit(pos.total_pnl)} | 總手續費: ${pos.total_fee:.2f}')
        
        # 保存到歷史
        self.trade_history.append(pos)
        self.current_position = None
        
        return True
    
    def _print_plan(self, plan: TradePlan):
        """打印交易計劃"""
        print('\n' + '='*70)
        print(f'📊 交易計劃 | {plan.timestamp}')
        print('='*70)
        
        # 機率
        long_bar = '█' * int(plan.long_probability / 5)
        short_bar = '█' * int(plan.short_probability / 5)
        print(f'  📈 做多機率: {Colors.GREEN}{plan.long_probability:.0f}%{Colors.RESET} {long_bar}')
        print(f'  📉 做空機率: {Colors.RED}{plan.short_probability:.0f}%{Colors.RESET} {short_bar}')
        
        print()
        
        if plan.direction == 'NEUTRAL':
            print(f'  ⚪ 建議方向: {Colors.DIM}觀望 (信號不足){Colors.RESET}')
        else:
            dir_str = Colors.long('🟢 LONG 做多') if plan.direction == 'LONG' else Colors.short('🔴 SHORT 做空')
            print(f'  {dir_str}')
            
            print()
            print(f'  ┌─────────────────────────────────────────────────────────────')
            print(f'  │ 📍 進場價格')
            print(f'  │    市價進場: ${plan.entry_market:,.2f}')
            limit_diff = (plan.entry_limit / plan.entry_market - 1) * 100
            print(f'  │    限價進場: ${plan.entry_limit:,.2f} ({limit_diff:+.2f}%)')
            print(f'  ├─────────────────────────────────────────────────────────────')
            print(f'  │ 🛑 風險控制')
            sl_diff = (plan.stop_loss / plan.entry_market - 1) * 100
            inv_diff = (plan.invalidation / plan.entry_market - 1) * 100
            print(f'  │    止損價格: ${plan.stop_loss:,.2f} ({Colors.pct(sl_diff)}, 基於 1.5 ATR)')
            print(f'  │    信號失效: ${plan.invalidation:,.2f} ({Colors.pct(inv_diff)})')
            print(f'  ├─────────────────────────────────────────────────────────────')
            print(f'  │ 🎯 止盈目標 (連鎖式)')
            tp1_diff = (plan.tp1 / plan.entry_market - 1) * 100
            tp2_diff = (plan.tp2 / plan.entry_market - 1) * 100
            print(f'  │    TP1: ${plan.tp1:,.2f} ({Colors.pct(tp1_diff)}) → 平倉 50%')
            print(f'  │    TP2: ${plan.tp2:,.2f} ({Colors.pct(tp2_diff)}) → 全部平倉')
            if self.config['chain_tp_enabled']:
                print(f'  │    [連鎖模式] TP1 觸發後自動生成新 TP1.1, TP1.2...')
            print(f'  └─────────────────────────────────────────────────────────────')
            
            print()
            print(f'  💰 風險報酬比: 1:{plan.risk_reward:.2f}')
            print(f'  📊 ATR (15分鐘): ${plan.atr:,.2f}')
            print(f'  📋 評分因素: {", ".join(plan.factors)}')
        
        print('='*70)
    
    def _print_position_tree(self, pos: ChainPosition, current_price: float):
        """打印持倉分支樹"""
        print('\n' + '-'*70)
        print(f'📍 當前持倉狀態')
        print('-'*70)
        
        dir_str = Colors.long('🟢 LONG') if pos.direction == 'LONG' else Colors.short('🔴 SHORT')
        print(f'  方向: {dir_str} | 開倉: ${pos.entry_price:,.2f} | 當前: ${current_price:,.2f}')
        
        # 未實現盈虧
        if pos.direction == 'LONG':
            unrealized = (current_price - pos.entry_price) * pos.current_size
        else:
            unrealized = (pos.entry_price - current_price) * pos.current_size
        
        print(f'  倉位: {pos.current_size:.6f} / {pos.initial_size:.6f} BTC ({pos.current_size/pos.initial_size*100:.0f}%)')
        print(f'  未實現盈虧: {Colors.profit(unrealized)}')
        print(f'  已實現盈虧: {Colors.profit(pos.total_pnl)}')
        print(f'  當前止損: ${pos.current_sl:,.2f}')
        
        # 打印分支樹
        print()
        print(f'  📊 止盈分支樹:')
        self._print_branch(pos.tp_tree, pos.direction, current_price, '  ')
    
    def _print_branch(self, branch: TPBranch, direction: str, current_price: float, indent: str):
        """遞歸打印分支"""
        level_name = f"TP{branch.level+1}" if branch.level == 0 else f"TP1{'.' + '1' * branch.level}"
        
        if branch.triggered:
            status = f'{Colors.GREEN}✓ 已觸發{Colors.RESET}'
            price_info = f'@ ${branch.trigger_price:,.2f}'
        else:
            # 計算距離
            if direction == 'LONG':
                dist = (branch.tp_price - current_price) / current_price * 100
            else:
                dist = (current_price - branch.tp_price) / current_price * 100
            
            if dist > 0:
                status = f'{Colors.YELLOW}待觸發{Colors.RESET}'
            else:
                status = f'{Colors.CYAN}可能觸發{Colors.RESET}'
            price_info = f'${branch.tp_price:,.2f} ({dist:+.2f}%)'
        
        print(f'{indent}├── {level_name}: {price_info} [{status}] 平倉{branch.close_pct}%')
        
        for i, child in enumerate(branch.children):
            is_last = (i == len(branch.children) - 1)
            new_indent = indent + ('    ' if is_last else '│   ')
            self._print_branch(child, direction, current_price, new_indent)
    
    def _print_status(self, current_price: float):
        """打印狀態"""
        print('\n' + '-'*70)
        print(f'💰 帳戶狀態')
        print('-'*70)
        
        net_value = self.balance
        if self.current_position:
            pos = self.current_position
            if pos.direction == 'LONG':
                unrealized = (current_price - pos.entry_price) * pos.current_size
            else:
                unrealized = (pos.entry_price - current_price) * pos.current_size
            net_value = self.balance + pos.margin_used * (pos.current_size / pos.initial_size) + unrealized
        
        roi = (net_value - self.initial_balance) / self.initial_balance * 100
        win_rate = self.winning_trades / self.total_trades * 100 if self.total_trades > 0 else 0
        
        print(f'  💵 餘額: ${self.balance:.2f} | 淨值: ${net_value:.2f} ({Colors.pct(roi)})')
        print(f'  📈 已實現盈虧: {Colors.profit(self.total_pnl)} | 總手續費: ${self.total_fees:.2f}')
        print(f'  🎯 交易: {self.total_trades} | 勝率: {win_rate:.1f}% | 最大回撤: {self.max_drawdown*100:.2f}%')
    
    def _save_state(self):
        """保存狀態"""
        def serialize_branch(branch: TPBranch) -> dict:
            return {
                'level': branch.level,
                'tp_price': branch.tp_price,
                'sl_price': branch.sl_price,
                'close_pct': branch.close_pct,
                'remaining_pct': branch.remaining_pct,
                'triggered': branch.triggered,
                'trigger_time': branch.trigger_time,
                'trigger_price': branch.trigger_price,
                'pnl': branch.pnl,
                'children': [serialize_branch(c) for c in branch.children]
            }
        
        def serialize_position(pos: ChainPosition) -> dict:
            return {
                'id': pos.id,
                'open_time': pos.open_time,
                'direction': pos.direction,
                'entry_price': pos.entry_price,
                'initial_size': pos.initial_size,
                'current_size': pos.current_size,
                'margin_used': pos.margin_used,
                'leverage': pos.leverage,
                'current_sl': pos.current_sl,
                'invalidation': pos.invalidation,
                'tp_tree': serialize_branch(pos.tp_tree),
                'status': pos.status,
                'total_pnl': pos.total_pnl,
                'total_fee': pos.total_fee,
                'close_history': pos.close_history,
                'plan': asdict(pos.plan) if pos.plan else None
            }
        
        state = {
            'session_id': self.session_id,
            'config': self.config,
            'balance': self.balance,
            'initial_balance': self.initial_balance,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'total_pnl': self.total_pnl,
            'total_fees': self.total_fees,
            'max_drawdown': self.max_drawdown,
            'current_position': serialize_position(self.current_position) if self.current_position else None,
            'trade_history': [serialize_position(t) for t in self.trade_history[-50:]],
            'plan_history': [asdict(p) for p in self.plan_history[-50:]],
            'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(self.log_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    
    def run(self):
        """運行模擬器"""
        self.running = True
        
        print('\n' + '='*70)
        print('🔗 連鎖止盈模擬交易系統 v2.0')
        print('='*70)
        print(f'  初始資金: ${self.config["initial_balance"]:.2f}')
        print(f'  槓桿倍數: {self.config["leverage"]}X')
        print(f'  手續費率: {self.config["fee_rate"]*100:.2f}%')
        print(f'  分析間隔: {self.config["analysis_interval"]} 分鐘')
        print(f'  監控間隔: {self.config["interval_seconds"]} 秒')
        print(f'  連鎖止盈: {"啟用" if self.config["chain_tp_enabled"] else "禁用"}')
        print(f'  日誌文件: {self.log_file}')
        print('='*70)
        print('\n⚠️ 按 Ctrl+C 停止模擬\n')
        
        while self.running:
            try:
                current_time = time.time()
                current_price = self.analyzer.get_price()
                
                # 如果有持倉，檢查止盈止損
                if self.current_position:
                    self._check_and_execute_tp(current_price)
                    
                    if self.current_position:
                        self._print_position_tree(self.current_position, current_price)
                
                # 定期分析
                if current_time - self.last_analysis_time >= self.config['analysis_interval'] * 60:
                    self.last_analysis_time = current_time
                    
                    plan = self.analyzer.analyze(self.config)
                    self.plan_history.append(plan)
                    
                    self._print_plan(plan)
                    
                    # 如果沒有持倉且有信號，開倉
                    if self.current_position is None and plan.direction != 'NEUTRAL':
                        self._open_position(plan)
                    elif self.current_position and plan.direction != 'NEUTRAL':
                        # 檢查是否需要反手
                        if self.current_position.direction != plan.direction:
                            self._log(f'⚠️ 信號反轉! 當前 {self.current_position.direction} → 新信號 {plan.direction}')
                            # 可選：自動反手
                            # self._close_position(current_price, '信號反轉')
                            # self._open_position(plan)
                
                self._print_status(current_price)
                self._save_state()
                
                # 等待
                for _ in range(self.config['interval_seconds']):
                    if not self.running:
                        break
                    time.sleep(1)
                
            except Exception as e:
                self._log(f'❌ 錯誤: {str(e)}', 'ERROR')
                time.sleep(30)
        
        # 清理
        if self.current_position:
            try:
                price = self.analyzer.get_price()
                self._close_position(price, '系統停止')
            except:
                pass
        
        self._save_state()
        self._print_final_report()
    
    def _print_final_report(self):
        """打印最終報告"""
        print('\n' + '='*70)
        print('📊 模擬交易最終報告')
        print('='*70)
        
        roi = (self.balance - self.initial_balance) / self.initial_balance * 100
        win_rate = self.winning_trades / self.total_trades * 100 if self.total_trades > 0 else 0
        
        print(f'  初始資金:   ${self.initial_balance:.2f}')
        print(f'  最終餘額:   ${self.balance:.2f}')
        print(f'  總收益率:   {Colors.pct(roi)}')
        print(f'  總盈虧:     {Colors.profit(self.total_pnl)}')
        print(f'  總手續費:   ${self.total_fees:.2f}')
        print(f'  交易次數:   {self.total_trades}')
        print(f'  勝率:       {win_rate:.1f}%')
        print(f'  最大回撤:   {self.max_drawdown*100:.2f}%')
        print(f'  日誌文件:   {self.log_file}')
        print('='*70)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='🔗 連鎖止盈模擬交易系統')
    parser.add_argument('--balance', type=float, default=100, help='初始資金')
    parser.add_argument('--leverage', type=int, default=50, help='槓桿倍數')
    parser.add_argument('--interval', type=int, default=15, help='分析間隔(分鐘)')
    parser.add_argument('--monitor', type=int, default=10, help='監控間隔(秒)')
    
    args = parser.parse_args()
    
    config = CONFIG.copy()
    config['initial_balance'] = args.balance
    config['leverage'] = args.leverage
    config['analysis_interval'] = args.interval
    config['interval_seconds'] = args.monitor
    
    simulator = ChainTPSimulator(config)
    simulator.run()


if __name__ == '__main__':
    main()
