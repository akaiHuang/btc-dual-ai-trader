#!/usr/bin/env python3
"""
🧠 智能交易模擬器 v3.0
Smart Trading Simulator with Danger Detection & Dynamic Re-evaluation

新增功能：
1. 危險訊號檢測 - 自動避開高風險時段
2. 到達TP後重新評估 - 根據當前市場決定是否繼續持有
3. 雙向機率預測 - 同時計算做多/做空機率
4. ATR 動態止盈止損
5. 連續虧損保護機制

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

# ==================== 終端顏色 ====================
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'
    
    @classmethod
    def colored(cls, text: str, color: str) -> str:
        return f"{color}{text}{cls.RESET}"
    
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
    'monitor_interval': 5,      # 監控間隔（秒）
    'analysis_interval': 15,    # 分析間隔（分鐘）
    
    # ATR 設定
    'atr_period': 14,
    'atr_sl_multiplier': 1.5,
    'atr_tp1_multiplier': 1.0,
    'atr_tp2_multiplier': 1.7,
    
    # 止盈策略
    'tp1_close_pct': 50,        # TP1 平倉比例
    'continue_threshold': 55,    # 繼續持有的機率門檻
    
    # 危險控制
    'max_danger_score': 4,      # 超過此分數不開倉
    'cooldown_after_loss': 2,   # 止損後冷卻週期數
    'max_daily_loss_pct': 15,   # 當日最大虧損比例
    'position_reduce_on_loss': 0.5,  # 虧損後倉位減半
    
    'log_dir': 'logs/smart_simulator_v3',
}

# ==================== 數據結構 ====================
@dataclass
class DangerAssessment:
    """危險評估"""
    score: int
    signals: List[str]
    bb_squeeze: bool
    small_bodies: int
    rsi_neutral: bool
    obi_conflict: bool
    big_order_balanced: bool
    trend_conflict: bool
    obi_trap: bool  # 訂單簿誘餌：OBI和成交方向相反
    long_short_crowded: bool  # 多頭/空頭擁擠
    safe_to_trade: bool
    
@dataclass
class MarketAnalysis:
    """市場分析"""
    timestamp: str
    price: float
    atr: float
    
    # 指標
    obi: float
    trade_imbalance: float
    rsi: float
    ema9: float
    ema21: float
    bb_width: float
    
    # 趨勢
    trend_15m: str
    trend_1h: str
    trend_4h: str
    
    # 大單
    big_buy_value: float
    big_sell_value: float
    
    # 多空比
    long_short_ratio: float
    
    # 訂單簿誘餌檢測
    obi_trap_detected: bool
    obi_trap_direction: str  # 'LONG_TRAP' or 'SHORT_TRAP' or 'NONE'
    
    # 支撐阻力
    support: float
    resistance: float
    
    # 機率與評分
    long_prob: float
    short_prob: float
    score: float
    factors: List[str]
    
    # 危險評估
    danger: DangerAssessment
    
    # 交易計劃
    recommended_direction: str
    entry_price: float
    stop_loss: float
    invalidation: float
    tp1: float
    tp2: float

@dataclass
class Position:
    """持倉"""
    id: int
    open_time: str
    direction: str
    entry_price: float
    size: float
    initial_size: float
    margin: float
    leverage: int
    
    stop_loss: float
    invalidation: float
    tp1: float
    tp2: float
    
    tp1_triggered: bool = False
    tp1_time: Optional[str] = None
    tp1_price: Optional[float] = None
    
    status: str = 'OPEN'
    realized_pnl: float = 0.0
    fees: float = 0.0
    
    # 重新評估記錄
    re_evaluations: List[Dict] = field(default_factory=list)

# ==================== 市場分析器 ====================
class SmartAnalyzer:
    def __init__(self, symbol: str = 'BTCUSDT'):
        self.symbol = symbol
        self.base_url = 'https://fapi.binance.com'
    
    def _get(self, endpoint: str, params: dict = None) -> Any:
        resp = requests.get(f'{self.base_url}{endpoint}', params=params, timeout=10)
        return resp.json()
    
    def get_price(self) -> float:
        data = self._get('/fapi/v1/ticker/price', {'symbol': self.symbol})
        return float(data['price'])
    
    def get_klines(self, interval: str, limit: int = 50) -> list:
        return self._get('/fapi/v1/klines', {'symbol': self.symbol, 'interval': interval, 'limit': limit})
    
    def calculate_atr(self, klines: list, period: int = 14) -> float:
        tr_list = []
        for i in range(1, len(klines)):
            high = float(klines[i][2])
            low = float(klines[i][3])
            prev_close = float(klines[i-1][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)
        return sum(tr_list[-period:]) / period if tr_list else 0
    
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
        ema = sum(data[:period]) / period
        for p in data[period:]:
            ema = (p - ema) * multiplier + ema
        return ema
    
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
    
    def assess_danger(self, klines: list, obi: float, trade_imbalance: float, 
                      big_buy: float, big_sell: float, trends: list,
                      long_short_ratio: float = 1.0, obi_trap: bool = False) -> DangerAssessment:
        """評估市場危險程度"""
        closes = [float(k[4]) for k in klines]
        opens = [float(k[1]) for k in klines]
        
        atr = self.calculate_atr(klines)
        rsi = self.calculate_rsi(closes)
        
        # 布林帶寬度
        sma20 = sum(closes[-20:]) / 20
        std20 = (sum((c - sma20)**2 for c in closes[-20:]) / 20) ** 0.5
        bb_width = (4 * std20) / sma20 * 100
        
        # 歷史 BB 寬度
        bb_widths = []
        for i in range(20, len(closes)):
            sma = sum(closes[i-20:i]) / 20
            std = (sum((c - sma)**2 for c in closes[i-20:i]) / 20) ** 0.5
            bb_widths.append((4 * std) / sma * 100)
        avg_bb_width = sum(bb_widths) / len(bb_widths) if bb_widths else bb_width
        
        signals = []
        score = 0
        
        # 1. 布林帶收窄
        bb_squeeze = bb_width < avg_bb_width * 0.7
        if bb_squeeze:
            signals.append('布林帶收窄')
            score += 2
        
        # 2. 小實體 K 線
        bodies = [abs(closes[i] - opens[i]) for i in range(-5, 0)]
        small_bodies = sum(1 for b in bodies if b < atr * 0.3)
        if small_bodies >= 3:
            signals.append(f'連續小實體({small_bodies}/5)')
            score += 2
        
        # 3. RSI 中性區
        rsi_neutral = 45 <= rsi <= 55
        if rsi_neutral:
            signals.append(f'RSI中性({rsi:.0f})')
            score += 1
        
        # 4. OBI vs 成交矛盾
        obi_conflict = (obi > 0.2 and trade_imbalance < -0.1) or (obi < -0.2 and trade_imbalance > 0.1)
        if obi_conflict:
            signals.append('OBI成交矛盾')
            score += 2
        
        # 5. 大單平衡
        big_ratio = big_buy / big_sell if big_sell > 0 else 999
        big_order_balanced = 0.7 < big_ratio < 1.3
        if big_order_balanced:
            signals.append('大單平衡')
            score += 1
        
        # 6. 趨勢矛盾
        trend_conflict = len(set(trends)) == 3 or trends.count('FLAT') >= 2
        if trend_conflict:
            signals.append('時間框架矛盾')
            score += 2
        
        # 7. 🆕 訂單簿誘餌 (高危險!)
        if obi_trap:
            signals.append('🚨訂單簿誘餌!')
            score += 3  # 高權重
        
        # 8. 🆕 多空擁擠
        long_short_crowded = long_short_ratio > 1.5 or long_short_ratio < 0.7
        if long_short_ratio > 1.5:
            signals.append(f'多頭擁擠({long_short_ratio:.2f})')
            score += 2
        elif long_short_ratio < 0.7:
            signals.append(f'空頭擁擠({long_short_ratio:.2f})')
            score += 2
        
        return DangerAssessment(
            score=score,
            signals=signals,
            bb_squeeze=bb_squeeze,
            small_bodies=small_bodies,
            rsi_neutral=rsi_neutral,
            obi_conflict=obi_conflict,
            big_order_balanced=big_order_balanced,
            trend_conflict=trend_conflict,
            obi_trap=obi_trap,
            long_short_crowded=long_short_crowded,
            safe_to_trade=score < CONFIG['max_danger_score']
        )
    
    def analyze(self) -> MarketAnalysis:
        """完整市場分析"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # K線數據
        klines = self.get_klines('15m', 50)
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        
        price = float(klines[-1][4])
        atr = self.calculate_atr(klines)
        rsi = self.calculate_rsi(closes)
        ema9 = self.calculate_ema(closes, 9)
        ema21 = self.calculate_ema(closes, 21)
        
        # 布林帶
        sma20 = sum(closes[-20:]) / 20
        std20 = (sum((c - sma20)**2 for c in closes[-20:]) / 20) ** 0.5
        bb_width = (4 * std20) / sma20 * 100
        
        # 訂單簿
        depth = self._get('/fapi/v1/depth', {'symbol': self.symbol, 'limit': 20})
        bids = sum(float(q) for p, q in depth['bids'][:10])
        asks = sum(float(q) for p, q in depth['asks'][:10])
        obi = (bids - asks) / (bids + asks)
        
        # 成交
        trades = self._get('/fapi/v1/aggTrades', {'symbol': self.symbol, 'limit': 500})
        buy_vol = sum(float(t['q']) for t in trades if not t['m'])
        sell_vol = sum(float(t['q']) for t in trades if t['m'])
        trade_imbalance = (buy_vol - sell_vol) / (buy_vol + sell_vol) if (buy_vol + sell_vol) > 0 else 0
        
        # 大單
        big_buys = [t for t in trades if not t['m'] and float(t['q']) * float(t['p']) > 10000]
        big_sells = [t for t in trades if t['m'] and float(t['q']) * float(t['p']) > 10000]
        big_buy_val = sum(float(t['q']) * float(t['p']) for t in big_buys)
        big_sell_val = sum(float(t['q']) * float(t['p']) for t in big_sells)
        
        # 趨勢
        trend_15m = self.get_trend('15m')
        trend_1h = self.get_trend('1h')
        trend_4h = self.get_trend('4h')
        trends = [trend_15m, trend_1h, trend_4h]
        
        # 支撐阻力
        support = min(lows[-20:])
        resistance = max(highs[-20:])
        
        # 多空比
        long_short_ratio = 1.0
        try:
            ls_data = self._get('/futures/data/globalLongShortAccountRatio',
                               {'symbol': self.symbol, 'period': '5m', 'limit': 1})
            if ls_data:
                long_short_ratio = float(ls_data[0]['longShortRatio'])
        except:
            pass
        
        # 訂單簿誘餌檢測
        # 當 OBI 顯示買盤強，但實際成交/大單都在賣 → 做多陷阱
        # 當 OBI 顯示賣盤強，但實際成交/大單都在買 → 做空陷阱
        obi_trap_detected = False
        obi_trap_direction = 'NONE'
        
        if obi > 0.2 and trade_imbalance < -0.1 and big_sell_val > big_buy_val * 1.3:
            obi_trap_detected = True
            obi_trap_direction = 'LONG_TRAP'  # 訂單簿誘多
        elif obi < -0.2 and trade_imbalance > 0.1 and big_buy_val > big_sell_val * 1.3:
            obi_trap_detected = True
            obi_trap_direction = 'SHORT_TRAP'  # 訂單簿誘空
        
        # 危險評估
        danger = self.assess_danger(klines, obi, trade_imbalance, big_buy_val, big_sell_val, 
                                   trends, long_short_ratio, obi_trap_detected)
        
        # 計算評分 (傳入 OBI 誘餌狀態)
        score, factors = self._calculate_score(obi, trade_imbalance, rsi, ema9, ema21, 
                                               trend_4h, big_buy_val, big_sell_val, 
                                               price, support, resistance, danger,
                                               obi_trap=obi_trap_detected)
        
        # 機率
        long_prob = min(95, max(5, 50 + score * 6))
        short_prob = 100 - long_prob
        
        # 方向
        if not danger.safe_to_trade:
            direction = 'HOLD'
        elif score >= 2:
            direction = 'LONG'
        elif score <= -2:
            direction = 'SHORT'
        else:
            direction = 'HOLD'
        
        # 計算止盈止損
        if direction == 'LONG':
            stop_loss = price - atr * CONFIG['atr_sl_multiplier']
            invalidation = support * 0.998
            tp1 = price + atr * CONFIG['atr_tp1_multiplier']
            tp2 = price + atr * CONFIG['atr_tp2_multiplier']
        elif direction == 'SHORT':
            stop_loss = price + atr * CONFIG['atr_sl_multiplier']
            invalidation = resistance * 1.002
            tp1 = price - atr * CONFIG['atr_tp1_multiplier']
            tp2 = price - atr * CONFIG['atr_tp2_multiplier']
        else:
            stop_loss = price
            invalidation = price
            tp1 = price
            tp2 = price
        
        return MarketAnalysis(
            timestamp=timestamp,
            price=price,
            atr=atr,
            obi=obi,
            trade_imbalance=trade_imbalance,
            rsi=rsi,
            ema9=ema9,
            ema21=ema21,
            bb_width=bb_width,
            trend_15m=trend_15m,
            trend_1h=trend_1h,
            trend_4h=trend_4h,
            big_buy_value=big_buy_val,
            big_sell_value=big_sell_val,
            support=support,
            resistance=resistance,
            long_prob=long_prob,
            short_prob=short_prob,
            score=score,
            factors=factors,
            danger=danger,
            recommended_direction=direction,
            entry_price=price,
            stop_loss=stop_loss,
            invalidation=invalidation,
            tp1=tp1,
            tp2=tp2,
            # 🆕 新增欄位
            long_short_ratio=long_short_ratio,
            obi_trap_detected=obi_trap_detected,
            obi_trap_direction=obi_trap_direction
        )
    
    def _calculate_score(self, obi, trade_imbalance, rsi, ema9, ema21, trend_4h,
                        big_buy, big_sell, price, support, resistance, danger,
                        obi_trap: bool = False) -> tuple:
        """
        計算方向分數
        🆕 核心原則: 成交 > 訂單簿 (掛單可以撤，成交不能撤)
        """
        score = 0
        factors = []
        
        # 🆕 如果偵測到 OBI 誘餌，OBI 權重歸零或反向
        if obi_trap:
            factors.append('⚠️OBI誘餌偵測,忽略訂單簿')
            # 不加 OBI 分數，讓成交和大單決定
        else:
            # OBI (只在沒有誘餌時才計入)
            if obi > 0.3:
                score += 1
                factors.append('OBI買盤+1')
            elif obi < -0.3:
                score -= 1
                factors.append('OBI賣盤-1')
        
        # 🆕 成交權重提升 (成交是真實行為)
        if trade_imbalance > 0.2:
            weight = 2 if obi_trap else 1  # 誘餌時成交權重加倍
            score += weight
            factors.append(f'成交買入+{weight}')
        elif trade_imbalance < -0.2:
            weight = 2 if obi_trap else 1
            score -= weight
            factors.append(f'成交賣出-{weight}')
        
        # RSI
        if rsi < 30:
            score += 2
            factors.append(f'RSI超賣({rsi:.0f})+2')
        elif rsi > 70:
            score -= 2
            factors.append(f'RSI超買({rsi:.0f})-2')
        
        # EMA
        if ema9 > ema21 * 1.002:
            score += 1
            factors.append('EMA金叉+1')
        elif ema9 < ema21 * 0.998:
            score -= 1
            factors.append('EMA死叉-1')
        
        # 4H 趨勢
        if trend_4h == 'UP':
            score += 2
            factors.append('4H上升+2')
        elif trend_4h == 'DOWN':
            score -= 2
            factors.append('4H下降-2')
        
        # 🆕 大單權重提升 (大單是真金白銀)
        if big_buy > big_sell * 1.5:
            weight = 2 if obi_trap else 1
            score += weight
            factors.append(f'大單買入+{weight}')
        elif big_sell > big_buy * 1.5:
            weight = 2 if obi_trap else 1
            score -= weight
            factors.append(f'大單賣出-{weight}')
        
        # 支撐阻力
        dist_support = (price - support) / price * 100
        dist_resistance = (resistance - price) / price * 100
        if dist_support < 0.5:
            score += 1
            factors.append('接近支撐+1')
        if dist_resistance < 0.5:
            score -= 1
            factors.append('接近阻力-1')
        
        # 危險減分
        if danger.score >= 4:
            penalty = -2 if score > 0 else 2
            score += penalty
            factors.append(f'危險訊號{penalty:+d}')
        
        # 🆕 多空擁擠額外減分
        if danger.long_short_crowded:
            penalty = -1 if score > 0 else 1
            score += penalty
            factors.append(f'多空擁擠{penalty:+d}')
        
        return score, factors


# ==================== 智能模擬器 ====================
class SmartSimulator:
    def __init__(self, config: dict = CONFIG):
        self.config = config
        self.analyzer = SmartAnalyzer(config['symbol'])
        
        self.balance = config['initial_balance']
        self.initial_balance = config['initial_balance']
        self.position: Optional[Position] = None
        
        self.trade_history: List[Position] = []
        self.analysis_history: List[MarketAnalysis] = []
        
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0.0
        self.total_fees = 0.0
        self.max_drawdown = 0.0
        self.peak_balance = config['initial_balance']
        self.daily_pnl = 0.0
        
        # 冷卻和保護
        self.cooldown_remaining = 0
        self.consecutive_losses = 0
        self.position_multiplier = 1.0
        
        self.running = False
        self.last_analysis_time = 0
        
        os.makedirs(config['log_dir'], exist_ok=True)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(config['log_dir'], f'session_{self.session_id}.json')
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        print('\n\n⚠️ 收到停止信號...')
        self.running = False
    
    def _log(self, msg: str, level: str = 'INFO'):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f'[{ts}] [{level}] {msg}')
    
    def _open_position(self, analysis: MarketAnalysis):
        """開倉"""
        price = analysis.price
        
        # 根據連續虧損調整倉位
        base_margin = self.balance * 0.95
        margin = base_margin * self.position_multiplier
        
        notional = margin * self.config['leverage']
        size = notional / price
        fee = notional * self.config['fee_rate']
        
        self.position = Position(
            id=self.total_trades + 1,
            open_time=analysis.timestamp,
            direction=analysis.recommended_direction,
            entry_price=price,
            size=size,
            initial_size=size,
            margin=margin,
            leverage=self.config['leverage'],
            stop_loss=analysis.stop_loss,
            invalidation=analysis.invalidation,
            tp1=analysis.tp1,
            tp2=analysis.tp2,
            fees=fee
        )
        
        # 扣除保證金和手續費
        self.balance -= (margin + fee)
        self.total_fees += fee
        
        dir_color = Colors.GREEN if analysis.recommended_direction == 'LONG' else Colors.RED
        dir_str = Colors.colored(f'🟢 LONG' if analysis.recommended_direction == 'LONG' else '🔴 SHORT', dir_color)
        
        self._log(f'🚀 開倉 {dir_str} @ ${price:,.2f}')
        self._log(f'   倉位: {size:.6f} BTC (${margin:.2f} 保證金)')
        self._log(f'   止損: ${analysis.stop_loss:,.2f} | TP1: ${analysis.tp1:,.2f} | TP2: ${analysis.tp2:,.2f}')
    
    def _check_position(self, current_price: float, analysis: Optional[MarketAnalysis] = None):
        """檢查持倉"""
        if not self.position:
            return
        
        pos = self.position
        
        # 計算未實現盈虧
        if pos.direction == 'LONG':
            unrealized = (current_price - pos.entry_price) * pos.size
        else:
            unrealized = (pos.entry_price - current_price) * pos.size
        
        # 檢查止損
        if pos.direction == 'LONG':
            if current_price <= pos.stop_loss:
                self._close_position(current_price, '止損')
                return
            if current_price <= pos.invalidation:
                self._close_position(current_price, '信號失效')
                return
        else:
            if current_price >= pos.stop_loss:
                self._close_position(current_price, '止損')
                return
            if current_price >= pos.invalidation:
                self._close_position(current_price, '信號失效')
                return
        
        # 檢查 TP1
        if not pos.tp1_triggered:
            tp1_hit = (pos.direction == 'LONG' and current_price >= pos.tp1) or \
                      (pos.direction == 'SHORT' and current_price <= pos.tp1)
            
            if tp1_hit:
                self._handle_tp1(current_price, analysis)
        
        # 檢查 TP2 (對剩餘倉位)
        if pos.tp1_triggered:
            tp2_hit = (pos.direction == 'LONG' and current_price >= pos.tp2) or \
                      (pos.direction == 'SHORT' and current_price <= pos.tp2)
            
            if tp2_hit:
                self._close_position(current_price, 'TP2')
    
    def _handle_tp1(self, current_price: float, analysis: Optional[MarketAnalysis]):
        """處理 TP1 觸發 - 重新評估是否繼續"""
        pos = self.position
        
        # 平倉一半
        close_size = pos.size * (self.config['tp1_close_pct'] / 100)
        
        if pos.direction == 'LONG':
            pnl = (current_price - pos.entry_price) * close_size
        else:
            pnl = (pos.entry_price - current_price) * close_size
        
        fee = close_size * current_price * self.config['fee_rate']
        pnl -= fee
        
        pos.size -= close_size
        pos.realized_pnl += pnl
        pos.fees += fee
        pos.tp1_triggered = True
        pos.tp1_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pos.tp1_price = current_price
        
        self.balance += pnl + (pos.margin * self.config['tp1_close_pct'] / 100)
        self.total_fees += fee
        self.total_pnl += pnl
        self.daily_pnl += pnl
        
        self._log(f'🎯 TP1 觸發 @ ${current_price:,.2f} | 平倉 50% | 盈虧: {Colors.profit(pnl)}')
        
        # 重新評估
        if analysis:
            self._re_evaluate_after_tp(current_price, analysis)
    
    def _re_evaluate_after_tp(self, current_price: float, analysis: MarketAnalysis):
        """TP1 後重新評估是否繼續持有"""
        pos = self.position
        
        # 計算繼續方向的機率
        if pos.direction == 'LONG':
            continue_prob = analysis.long_prob
            reverse_prob = analysis.short_prob
        else:
            continue_prob = analysis.short_prob
            reverse_prob = analysis.long_prob
        
        eval_record = {
            'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'price': current_price,
            'continue_prob': continue_prob,
            'reverse_prob': reverse_prob,
            'danger_score': analysis.danger.score,
            'decision': None
        }
        
        print('\n' + '-'*60)
        print('🔄 TP1 後重新評估')
        print('-'*60)
        print(f'   繼續{pos.direction}機率: {continue_prob:.0f}%')
        print(f'   反轉機率: {reverse_prob:.0f}%')
        print(f'   危險指數: {analysis.danger.score}/10')
        
        # 決策邏輯
        if analysis.danger.score >= 4:
            # 危險，全部平倉
            eval_record['decision'] = '危險-全平'
            self._log(f'   ⚠️ 市場危險，平倉剩餘部位')
            self._close_position(current_price, '危險訊號')
        elif continue_prob >= self.config['continue_threshold']:
            # 繼續持有
            eval_record['decision'] = '繼續持有'
            
            # 移動止損到成本價
            pos.stop_loss = pos.entry_price
            
            # 計算新的 TP2
            new_tp2_dist = analysis.atr * self.config['atr_tp2_multiplier'] * 0.7
            if pos.direction == 'LONG':
                pos.tp2 = current_price + new_tp2_dist
            else:
                pos.tp2 = current_price - new_tp2_dist
            
            self._log(f'   ✅ 繼續持有 | 新止損(保本): ${pos.stop_loss:,.2f} | 新TP2: ${pos.tp2:,.2f}')
        else:
            # 機率不足，平倉
            eval_record['decision'] = '機率不足-平倉'
            self._log(f'   ❌ 繼續機率不足({continue_prob:.0f}% < {self.config["continue_threshold"]}%)，平倉')
            self._close_position(current_price, '機率不足')
        
        pos.re_evaluations.append(eval_record)
        print('-'*60)
    
    def _close_position(self, price: float, reason: str):
        """完全平倉"""
        pos = self.position
        if not pos:
            return
        
        if pos.direction == 'LONG':
            pnl = (price - pos.entry_price) * pos.size
        else:
            pnl = (pos.entry_price - price) * pos.size
        
        fee = pos.size * price * self.config['fee_rate']
        pnl -= fee
        
        remaining_margin_pct = pos.size / pos.initial_size
        returned_margin = pos.margin * remaining_margin_pct
        
        self.balance += returned_margin + pnl
        self.total_pnl += pnl
        self.total_fees += fee
        self.daily_pnl += pnl
        
        pos.realized_pnl += pnl
        pos.fees += fee
        pos.size = 0
        pos.status = 'CLOSED'
        
        self.total_trades += 1
        
        total_trade_pnl = pos.realized_pnl
        if total_trade_pnl > 0:
            self.winning_trades += 1
            self.consecutive_losses = 0
            self.position_multiplier = min(1.0, self.position_multiplier + 0.25)
            emoji = '✅'
        else:
            self.consecutive_losses += 1
            self.position_multiplier *= self.config['position_reduce_on_loss']
            self.cooldown_remaining = self.config['cooldown_after_loss']
            emoji = '❌'
        
        # 最大回撤
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd = (self.peak_balance - self.balance) / self.peak_balance
        if dd > self.max_drawdown:
            self.max_drawdown = dd
        
        self._log(f'{emoji} 平倉 @ ${price:,.2f} | 原因: {reason}')
        self._log(f'   本單總盈虧: {Colors.profit(total_trade_pnl)} | 餘額: ${self.balance:.2f}')
        
        if self.cooldown_remaining > 0:
            self._log(f'   ⏳ 冷卻中: {self.cooldown_remaining} 週期')
        
        self.trade_history.append(pos)
        self.position = None
    
    def _print_analysis(self, analysis: MarketAnalysis):
        """打印分析結果"""
        print('\n' + '='*70)
        print(f'📊 市場分析 | {analysis.timestamp}')
        print('='*70)
        
        # 🆕 OBI 誘餌警告 (最優先顯示)
        if analysis.obi_trap_detected:
            trap_msg = f'🚨 OBI 誘餌偵測! ({analysis.obi_trap_direction})'
            print(f'\n  {Colors.colored(trap_msg, Colors.RED + Colors.BOLD)}')
            print(f'     訂單簿顯示: {"買入" if analysis.obi > 0 else "賣出"}')
            print(f'     實際成交: {"賣出" if analysis.trade_imbalance < 0 else "買入"}')
            print(f'     ⚠️ 訂單簿是「意圖」，成交是「行動」。相信成交!')
        
        # 危險評估
        danger = analysis.danger
        if danger.score >= 6:
            danger_str = Colors.colored(f'🔴 極高風險 ({danger.score}/10)', Colors.RED)
        elif danger.score >= 4:
            danger_str = Colors.colored(f'🟠 高風險 ({danger.score}/10)', Colors.YELLOW)
        elif danger.score >= 2:
            danger_str = Colors.colored(f'🟡 中等 ({danger.score}/10)', Colors.YELLOW)
        else:
            danger_str = Colors.colored(f'🟢 安全 ({danger.score}/10)', Colors.GREEN)
        
        print(f'\n  ⚠️ 危險評估: {danger_str}')
        if danger.signals:
            print(f'     訊號: {", ".join(danger.signals)}')
        
        # 🆕 多空比例
        ls_ratio = analysis.long_short_ratio
        if ls_ratio > 1.5:
            ls_str = Colors.colored(f'多頭擁擠 ({ls_ratio:.2f})', Colors.YELLOW)
        elif ls_ratio < 0.7:
            ls_str = Colors.colored(f'空頭擁擠 ({ls_ratio:.2f})', Colors.YELLOW)
        else:
            ls_str = f'平衡 ({ls_ratio:.2f})'
        print(f'  📊 多空比: {ls_str}')
        
        # 機率
        print(f'\n  📈 做多機率: {Colors.GREEN}{analysis.long_prob:.0f}%{Colors.RESET}')
        print(f'  📉 做空機率: {Colors.RED}{analysis.short_prob:.0f}%{Colors.RESET}')
        
        # 方向
        if analysis.recommended_direction == 'LONG':
            dir_str = Colors.colored('🟢 LONG 做多', Colors.GREEN)
        elif analysis.recommended_direction == 'SHORT':
            dir_str = Colors.colored('🔴 SHORT 做空', Colors.RED)
        else:
            dir_str = Colors.colored('⚪ 觀望', Colors.DIM)
        
        print(f'\n  建議: {dir_str}')
        
        if analysis.recommended_direction != 'HOLD':
            sl_pct = (analysis.stop_loss / analysis.price - 1) * 100
            tp1_pct = (analysis.tp1 / analysis.price - 1) * 100
            tp2_pct = (analysis.tp2 / analysis.price - 1) * 100
            
            print(f'\n  ┌─────────────────────────────────────────────')
            print(f'  │ 進場: ${analysis.price:,.2f}')
            print(f'  │ 止損: ${analysis.stop_loss:,.2f} ({Colors.pct(sl_pct)})')
            print(f'  │ TP1:  ${analysis.tp1:,.2f} ({Colors.pct(tp1_pct)}) → 平倉50%+重新評估')
            print(f'  │ TP2:  ${analysis.tp2:,.2f} ({Colors.pct(tp2_pct)}) → 全部平倉')
            print(f'  └─────────────────────────────────────────────')
        
        print(f'\n  📋 因素: {", ".join(analysis.factors)}')
        print('='*70)
    
    def _print_status(self, current_price: float):
        """打印狀態"""
        print('\n' + '-'*70)
        print('💰 帳戶狀態')
        print('-'*70)
        
        net_value = self.balance
        unrealized = 0
        
        if self.position:
            pos = self.position
            if pos.direction == 'LONG':
                unrealized = (current_price - pos.entry_price) * pos.size
            else:
                unrealized = (pos.entry_price - current_price) * pos.size
            # 淨值 = 餘額 + 保證金(按剩餘倉位比例) + 未實現盈虧
            remaining_margin = pos.margin * (pos.size / pos.initial_size)
            net_value = self.balance + remaining_margin + unrealized
        
        roi = (net_value - self.initial_balance) / self.initial_balance * 100
        win_rate = self.winning_trades / self.total_trades * 100 if self.total_trades > 0 else 0
        
        print(f'  💵 餘額: ${self.balance:.2f} | 淨值: ${net_value:.2f} ({Colors.pct(roi)})')
        print(f'  📈 已實現: {Colors.profit(self.total_pnl)} | 當日: {Colors.profit(self.daily_pnl)}')
        print(f'  🎯 交易: {self.total_trades} | 勝率: {win_rate:.1f}% | 最大回撤: {self.max_drawdown*100:.2f}%')
        print(f'  🛡️ 倉位係數: {self.position_multiplier:.0%} | 連虧: {self.consecutive_losses}')
        
        if self.cooldown_remaining > 0:
            print(f'  ⏳ 冷卻中: {self.cooldown_remaining} 週期')
        
        if self.position:
            pos = self.position
            dir_str = Colors.colored('LONG', Colors.GREEN) if pos.direction == 'LONG' else Colors.colored('SHORT', Colors.RED)
            pnl_pct = unrealized / pos.margin * 100
            
            print(f'\n  📍 持倉: {dir_str} @ ${pos.entry_price:,.2f}')
            print(f'     倉位: {pos.size:.6f}/{pos.initial_size:.6f} BTC')
            print(f'     未實現: {Colors.profit(unrealized)} ({Colors.pct(pnl_pct)})')
            print(f'     止損: ${pos.stop_loss:,.2f} | TP1: {"✓" if pos.tp1_triggered else f"${pos.tp1:,.2f}"} | TP2: ${pos.tp2:,.2f}')
        
        print('-'*70)
    
    def _save_state(self):
        """保存狀態"""
        state = {
            'session_id': self.session_id,
            'config': self.config,
            'balance': self.balance,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'total_pnl': self.total_pnl,
            'max_drawdown': self.max_drawdown,
            'position': asdict(self.position) if self.position else None,
            'trade_history': [asdict(t) for t in self.trade_history[-30:]],
            'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(self.log_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    
    def run(self):
        """運行"""
        self.running = True
        
        print('\n' + '='*70)
        print('🧠 智能交易模擬器 v3.0')
        print('='*70)
        print(f'  初始資金: ${self.config["initial_balance"]:.2f}')
        print(f'  槓桿: {self.config["leverage"]}X')
        print(f'  分析間隔: {self.config["analysis_interval"]} 分鐘')
        print(f'  危險閾值: {self.config["max_danger_score"]}')
        print(f'  繼續持有門檻: {self.config["continue_threshold"]}%')
        print(f'  日誌: {self.log_file}')
        print('='*70)
        print('\n⚠️ Ctrl+C 停止\n')
        
        while self.running:
            try:
                current_time = time.time()
                
                # 獲取當前價格
                current_price = self.analyzer.get_price()
                
                # 檢查持倉
                if self.position:
                    # 快速檢查止損止盈
                    self._check_position(current_price)
                
                # 定期分析
                if current_time - self.last_analysis_time >= self.config['analysis_interval'] * 60:
                    self.last_analysis_time = current_time
                    
                    # 完整分析
                    analysis = self.analyzer.analyze()
                    self.analysis_history.append(analysis)
                    
                    self._print_analysis(analysis)
                    
                    # 如果有持倉，用新分析檢查
                    if self.position:
                        self._check_position(current_price, analysis)
                    
                    # 如果沒有持倉，考慮開倉
                    if not self.position:
                        # 檢查冷卻
                        if self.cooldown_remaining > 0:
                            self.cooldown_remaining -= 1
                            self._log(f'⏳ 冷卻中，跳過本週期 (剩餘 {self.cooldown_remaining})')
                        # 檢查當日虧損
                        elif self.daily_pnl < -self.initial_balance * self.config['max_daily_loss_pct'] / 100:
                            self._log(f'🛑 當日虧損超限 ({Colors.profit(self.daily_pnl)})，停止交易')
                        # 檢查危險
                        elif not analysis.danger.safe_to_trade:
                            self._log(f'⚠️ 市場危險 ({analysis.danger.score}/10)，暫不開倉')
                        # 開倉
                        elif analysis.recommended_direction != 'HOLD':
                            self._open_position(analysis)
                        else:
                            self._log(f'⏳ 信號不足，觀望 (評分: {analysis.score:+.1f})')
                
                self._print_status(current_price)
                self._save_state()
                
                # 等待
                for _ in range(self.config['monitor_interval']):
                    if not self.running:
                        break
                    time.sleep(1)
                
            except Exception as e:
                self._log(f'❌ 錯誤: {str(e)}', 'ERROR')
                time.sleep(30)
        
        # 清理
        if self.position:
            try:
                price = self.analyzer.get_price()
                self._close_position(price, '系統停止')
            except:
                pass
        
        self._save_state()
        self._print_final()
    
    def _print_final(self):
        """最終報告"""
        print('\n' + '='*70)
        print('📊 最終報告')
        print('='*70)
        
        roi = (self.balance - self.initial_balance) / self.initial_balance * 100
        win_rate = self.winning_trades / self.total_trades * 100 if self.total_trades > 0 else 0
        
        print(f'  初始: ${self.initial_balance:.2f} → 最終: ${self.balance:.2f}')
        print(f'  收益率: {Colors.pct(roi)}')
        print(f'  盈虧: {Colors.profit(self.total_pnl)} | 手續費: ${self.total_fees:.2f}')
        print(f'  交易: {self.total_trades} | 勝率: {win_rate:.1f}%')
        print(f'  最大回撤: {self.max_drawdown*100:.2f}%')
        print('='*70)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='🧠 智能交易模擬器 v3.0')
    parser.add_argument('--balance', type=float, default=100)
    parser.add_argument('--leverage', type=int, default=50)
    parser.add_argument('--interval', type=int, default=15, help='分析間隔(分鐘)')
    parser.add_argument('--monitor', type=int, default=5, help='監控間隔(秒)')
    parser.add_argument('--danger', type=int, default=4, help='危險閾值')
    parser.add_argument('--continue-threshold', type=int, default=55, help='繼續持有機率門檻')
    
    args = parser.parse_args()
    
    config = CONFIG.copy()
    config['initial_balance'] = args.balance
    config['leverage'] = args.leverage
    config['analysis_interval'] = args.interval
    config['monitor_interval'] = args.monitor
    config['max_danger_score'] = args.danger
    config['continue_threshold'] = args.continue_threshold
    
    sim = SmartSimulator(config)
    sim.run()


if __name__ == '__main__':
    main()
