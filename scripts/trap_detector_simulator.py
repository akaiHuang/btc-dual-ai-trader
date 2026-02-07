#!/usr/bin/env python3
"""
🔮 陷阱檢測模擬交易系統
基於空頭/多頭陷阱檢測的15分鐘自動交易模擬器

功能：
- 每15分鐘自動分析市場並做出交易決策
- 檢測空頭/多頭陷阱避免錯誤進場
- 記錄所有交易和指標數據供事後分析
- 支持槓桿交易和手續費計算

作者：AI Trading System
日期：2025-12-05
"""

import requests
import json
import time
import os
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict
import signal
import sys

# ==================== 終端顏色 ====================
class Colors:
    """終端顏色代碼"""
    # 基本顏色
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    
    # 背景顏色
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    
    # 樣式
    BOLD = '\033[1m'
    DIM = '\033[2m'
    UNDERLINE = '\033[4m'
    
    # 重置
    RESET = '\033[0m'
    
    @classmethod
    def long(cls, text: str) -> str:
        """做多文字 (綠色)"""
        return f"{cls.BOLD}{cls.GREEN}{text}{cls.RESET}"
    
    @classmethod
    def short(cls, text: str) -> str:
        """做空文字 (紅色)"""
        return f"{cls.BOLD}{cls.RED}{text}{cls.RESET}"
    
    @classmethod
    def profit(cls, value: float) -> str:
        """盈利文字 (綠色)"""
        if value >= 0:
            return f"{cls.GREEN}${value:+.2f}{cls.RESET}"
        else:
            return f"{cls.RED}${value:+.2f}{cls.RESET}"
    
    @classmethod
    def pct(cls, value: float) -> str:
        """百分比文字"""
        if value >= 0:
            return f"{cls.GREEN}{value:+.2f}%{cls.RESET}"
        else:
            return f"{cls.RED}{value:+.2f}%{cls.RESET}"

# ==================== 配置 ====================
CONFIG = {
    'symbol': 'BTCUSDT',
    'initial_balance': 100.0,      # 初始資金 (USDT)
    'leverage': 50,                 # 槓桿倍數
    'fee_rate': 0.0002,            # 手續費率 0.02%
    'interval_minutes': 15,         # 交易間隔 (分鐘)
    'position_size_pct': 0.95,     # 每次使用資金比例
    # 止盈止損建議：
    # - 50X槓桿下，0.5%價格波動 = 25%保證金波動
    # - 止盈 0.5% = 獲利 25% 保證金
    # - 止損 0.3% = 損失 15% 保證金 (風險報酬比 1.67:1)
    'take_profit_pct': 0.005,      # 止盈比例 0.5% (=25%保證金獲利)
    'stop_loss_pct': 0.003,        # 止損比例 0.3% (=15%保證金損失)
    'log_dir': 'logs/trap_simulator',
}

# ==================== 數據結構 ====================
@dataclass
class MarketData:
    """市場數據"""
    timestamp: str
    price: float
    obi: float
    trade_imbalance: float
    rsi: float
    ema9: float
    ema21: float
    price_change_15m: float
    funding_rate: float
    big_buy_value: float
    big_sell_value: float
    trend_15m: str
    trend_1h: str
    trend_4h: str
    bear_trap_signals: int
    bear_trap_reasons: List[str]
    bull_trap_signals: int
    bull_trap_reasons: List[str]
    score: float
    factors: List[str]

@dataclass
class Trade:
    """交易記錄"""
    id: int
    open_time: str
    close_time: Optional[str]
    direction: str  # LONG or SHORT
    entry_price: float
    exit_price: Optional[float]
    position_size: float  # BTC 數量
    leverage: int
    margin_used: float
    take_profit: float
    stop_loss: float
    pnl: Optional[float]
    pnl_pct: Optional[float]
    fee_paid: float
    status: str  # OPEN, CLOSED, LIQUIDATED
    close_reason: Optional[str]
    market_data: Dict

@dataclass
class Portfolio:
    """投資組合"""
    balance: float
    initial_balance: float
    current_position: Optional[Trade]
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_pnl: float
    total_fees: float
    max_drawdown: float
    peak_balance: float

# ==================== 市場分析 ====================
class MarketAnalyzer:
    """市場分析器"""
    
    def __init__(self, symbol: str = 'BTCUSDT'):
        self.symbol = symbol
        self.base_url = 'https://fapi.binance.com'
    
    def get_price(self) -> float:
        """獲取當前價格"""
        resp = requests.get(f'{self.base_url}/fapi/v1/ticker/price', 
                          params={'symbol': self.symbol}, timeout=10)
        return float(resp.json()['price'])
    
    def get_obi(self) -> float:
        """計算訂單簿失衡"""
        resp = requests.get(f'{self.base_url}/fapi/v1/depth',
                          params={'symbol': self.symbol, 'limit': 20}, timeout=10)
        data = resp.json()
        bids = [[float(p), float(q)] for p, q in data['bids']]
        asks = [[float(p), float(q)] for p, q in data['asks']]
        total_bid = sum(q for p, q in bids[:10])
        total_ask = sum(q for p, q in asks[:10])
        return (total_bid - total_ask) / (total_bid + total_ask)
    
    def get_trade_imbalance(self) -> tuple:
        """計算成交失衡和大單數據"""
        resp = requests.get(f'{self.base_url}/fapi/v1/aggTrades',
                          params={'symbol': self.symbol, 'limit': 500}, timeout=10)
        trades = resp.json()
        
        buy_qty = sum(float(t['q']) for t in trades if not t['m'])
        sell_qty = sum(float(t['q']) for t in trades if t['m'])
        imbalance = (buy_qty - sell_qty) / (buy_qty + sell_qty) if (buy_qty + sell_qty) > 0 else 0
        
        # 大單 (>$10,000)
        big_buys = [t for t in trades if not t['m'] and float(t['q']) * float(t['p']) > 10000]
        big_sells = [t for t in trades if t['m'] and float(t['q']) * float(t['p']) > 10000]
        big_buy_val = sum(float(t['q']) * float(t['p']) for t in big_buys)
        big_sell_val = sum(float(t['q']) * float(t['p']) for t in big_sells)
        
        return imbalance, big_buy_val, big_sell_val
    
    def get_klines(self, interval: str, limit: int = 30) -> list:
        """獲取K線數據"""
        resp = requests.get(f'{self.base_url}/fapi/v1/klines',
                          params={'symbol': self.symbol, 'interval': interval, 'limit': limit},
                          timeout=10)
        return resp.json()
    
    def calculate_rsi(self, closes: list, period: int = 14) -> float:
        """計算RSI"""
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
        """計算EMA"""
        if len(data) < period:
            return sum(data) / len(data) if data else 0
        multiplier = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for p in data[period:]:
            ema_val = (p - ema_val) * multiplier + ema_val
        return ema_val
    
    def get_trend(self, interval: str) -> str:
        """獲取趨勢"""
        klines = self.get_klines(interval, 25)
        closes = [float(k[4]) for k in klines]
        ema9 = self.calculate_ema(closes, 9)
        ema21 = self.calculate_ema(closes, 21)
        if ema9 > ema21 * 1.002:
            return 'UP'
        elif ema9 < ema21 * 0.998:
            return 'DOWN'
        return 'FLAT'
    
    def get_funding_rate(self) -> float:
        """獲取資金費率"""
        resp = requests.get(f'{self.base_url}/fapi/v1/premiumIndex',
                          params={'symbol': self.symbol}, timeout=10)
        return float(resp.json()['lastFundingRate']) * 100
    
    def analyze(self) -> MarketData:
        """完整市場分析"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 基礎數據
        price = self.get_price()
        obi = self.get_obi()
        trade_imbalance, big_buy_val, big_sell_val = self.get_trade_imbalance()
        
        # K線和技術指標
        klines = self.get_klines('15m', 30)
        closes = [float(k[4]) for k in klines]
        rsi = self.calculate_rsi(closes)
        ema9 = self.calculate_ema(closes, 9)
        ema21 = self.calculate_ema(closes, 21)
        
        latest = klines[-1]
        price_change_15m = (float(latest[4]) - float(latest[1])) / float(latest[1]) * 100
        
        # 多時間框架趨勢
        trend_15m = self.get_trend('15m')
        trend_1h = self.get_trend('1h')
        trend_4h = self.get_trend('4h')
        
        # 資金費率
        funding_rate = self.get_funding_rate()
        
        # 陷阱檢測
        bear_trap_signals, bear_trap_reasons = self._detect_bear_trap(
            rsi, obi, trend_4h, price_change_15m, big_buy_val, big_sell_val)
        bull_trap_signals, bull_trap_reasons = self._detect_bull_trap(
            rsi, obi, trend_4h, price_change_15m, big_buy_val, big_sell_val)
        
        # 綜合評分
        score, factors = self._calculate_score(
            obi, trade_imbalance, rsi, ema9, ema21, trend_4h,
            bear_trap_signals, bull_trap_signals, big_buy_val, big_sell_val)
        
        return MarketData(
            timestamp=timestamp,
            price=price,
            obi=obi,
            trade_imbalance=trade_imbalance,
            rsi=rsi,
            ema9=ema9,
            ema21=ema21,
            price_change_15m=price_change_15m,
            funding_rate=funding_rate,
            big_buy_value=big_buy_val,
            big_sell_value=big_sell_val,
            trend_15m=trend_15m,
            trend_1h=trend_1h,
            trend_4h=trend_4h,
            bear_trap_signals=bear_trap_signals,
            bear_trap_reasons=bear_trap_reasons,
            bull_trap_signals=bull_trap_signals,
            bull_trap_reasons=bull_trap_reasons,
            score=score,
            factors=factors
        )
    
    def _detect_bear_trap(self, rsi, obi, trend_4h, price_change, big_buy, big_sell) -> tuple:
        """檢測空頭陷阱"""
        signals = 0
        reasons = []
        
        if rsi < 30:
            signals += 2
            reasons.append(f'RSI超賣({rsi:.1f})')
        if obi < -0.6:
            signals += 1
            reasons.append(f'OBI極端({obi:.2f})')
        if trend_4h == 'UP':
            signals += 1
            reasons.append('4H趨勢向上')
        if obi < -0.3 and abs(price_change) < 0.1:
            signals += 1
            reasons.append('賣壓強但價格不跌')
        if big_buy > 0 and big_sell / big_buy > 3:
            signals += 1
            reasons.append('大單賣/買比極端')
        
        return signals, reasons
    
    def _detect_bull_trap(self, rsi, obi, trend_4h, price_change, big_buy, big_sell) -> tuple:
        """檢測多頭陷阱"""
        signals = 0
        reasons = []
        
        if rsi > 70:
            signals += 2
            reasons.append(f'RSI超買({rsi:.1f})')
        if obi > 0.6:
            signals += 1
            reasons.append(f'OBI極端({obi:.2f})')
        if trend_4h == 'DOWN':
            signals += 1
            reasons.append('4H趨勢向下')
        if obi > 0.3 and abs(price_change) < 0.1:
            signals += 1
            reasons.append('買壓強但價格不漲')
        if big_buy > 0 and big_sell / big_buy < 0.33:
            signals += 1
            reasons.append('大單買/賣比極端')
        
        return signals, reasons
    
    def _calculate_score(self, obi, trade_imbalance, rsi, ema9, ema21, trend_4h,
                        bear_trap_signals, bull_trap_signals, big_buy, big_sell) -> tuple:
        """計算綜合評分"""
        score = 0
        factors = []
        
        # OBI
        if obi > 0.3:
            score += 1
            factors.append('OBI買盤+1')
        elif obi < -0.3:
            score -= 1
            factors.append('OBI賣盤-1')
        
        # 成交失衡
        if trade_imbalance > 0.2:
            score += 1
            factors.append('成交買入+1')
        elif trade_imbalance < -0.2:
            score -= 1
            factors.append('成交賣出-1')
        
        # RSI
        if rsi < 30:
            score += 2
            factors.append('RSI超賣+2🔑')
        elif rsi > 70:
            score -= 2
            factors.append('RSI超買-2🔑')
        
        # EMA
        if ema9 > ema21 * 1.002:
            score += 1
            factors.append('EMA上升+1')
        elif ema9 < ema21 * 0.998:
            score -= 1
            factors.append('EMA下降-1')
        
        # 4H趨勢
        if trend_4h == 'UP':
            score += 2
            factors.append('4H上升+2🔑')
        elif trend_4h == 'DOWN':
            score -= 2
            factors.append('4H下降-2🔑')
        
        # 陷阱加成
        if bear_trap_signals >= 3:
            score += 2
            factors.append('空頭陷阱+2⚠️')
        if bull_trap_signals >= 3:
            score -= 2
            factors.append('多頭陷阱-2⚠️')
        
        # 大單
        if big_buy > big_sell * 1.5:
            score += 0.5
            factors.append('大單買入+0.5')
        elif big_sell > big_buy * 1.5:
            score -= 0.5
            factors.append('大單賣出-0.5')
        
        return score, factors


# ==================== 交易模擬器 ====================
class TrapDetectorSimulator:
    """陷阱檢測交易模擬器"""
    
    def __init__(self, config: dict = CONFIG):
        self.config = config
        self.analyzer = MarketAnalyzer(config['symbol'])
        self.portfolio = Portfolio(
            balance=config['initial_balance'],
            initial_balance=config['initial_balance'],
            current_position=None,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            total_pnl=0.0,
            total_fees=0.0,
            max_drawdown=0.0,
            peak_balance=config['initial_balance']
        )
        self.trade_history: List[Trade] = []
        self.market_history: List[MarketData] = []
        self.running = False
        
        # 創建日誌目錄
        os.makedirs(config['log_dir'], exist_ok=True)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(config['log_dir'], f'session_{self.session_id}.json')
        
        # 設置信號處理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """處理停止信號"""
        print('\n\n⚠️ 收到停止信號，正在安全關閉...')
        self.running = False
    
    def _log(self, message: str, level: str = 'INFO'):
        """輸出日誌"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f'[{timestamp}] [{level}] {message}')
    
    def _save_state(self):
        """保存當前狀態到文件"""
        state = {
            'session_id': self.session_id,
            'config': self.config,
            'portfolio': asdict(self.portfolio) if self.portfolio.current_position is None else {
                **asdict(self.portfolio),
                'current_position': asdict(self.portfolio.current_position)
            },
            'trade_history': [asdict(t) for t in self.trade_history],
            'market_history': [asdict(m) for m in self.market_history[-100:]],  # 保留最近100筆
            'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(self.log_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    
    def _decide_action(self, market: MarketData) -> tuple:
        """決定交易動作"""
        score = market.score
        
        if score >= 3:
            return 'LONG', min(95, 50 + score * 8)
        elif score <= -3:
            return 'SHORT', min(95, 50 + abs(score) * 8)
        elif score >= 1.5:
            return 'LIGHT_LONG', min(70, 40 + score * 8)
        elif score <= -1.5:
            return 'LIGHT_SHORT', min(70, 40 + abs(score) * 8)
        else:
            return 'HOLD', 0
    
    def _open_position(self, direction: str, market: MarketData, confidence: float):
        """開倉"""
        price = market.price
        leverage = self.config['leverage']
        fee_rate = self.config['fee_rate']
        
        # 計算倉位
        margin = self.portfolio.balance * self.config['position_size_pct']
        notional_value = margin * leverage
        position_size = notional_value / price
        
        # 計算開倉手續費
        open_fee = notional_value * fee_rate
        
        # 設置止盈止損
        if direction in ['LONG', 'LIGHT_LONG']:
            take_profit = price * (1 + self.config['take_profit_pct'])
            stop_loss = price * (1 - self.config['stop_loss_pct'])
            actual_direction = 'LONG'
        else:
            take_profit = price * (1 - self.config['take_profit_pct'])
            stop_loss = price * (1 + self.config['stop_loss_pct'])
            actual_direction = 'SHORT'
        
        # 創建交易
        trade = Trade(
            id=self.portfolio.total_trades + 1,
            open_time=market.timestamp,
            close_time=None,
            direction=actual_direction,
            entry_price=price,
            exit_price=None,
            position_size=position_size,
            leverage=leverage,
            margin_used=margin,
            take_profit=take_profit,
            stop_loss=stop_loss,
            pnl=None,
            pnl_pct=None,
            fee_paid=open_fee,
            status='OPEN',
            close_reason=None,
            market_data=asdict(market)
        )
        
        self.portfolio.current_position = trade
        self.portfolio.balance -= open_fee
        self.portfolio.total_fees += open_fee
        
        self._log(f'🚀 開倉 {actual_direction} @ ${price:,.2f} | '
                 f'數量: {position_size:.6f} BTC | '
                 f'保證金: ${margin:.2f} | '
                 f'信心度: {confidence:.0f}%')
        self._log(f'   🎯 止盈: ${take_profit:,.2f} | 🛑 止損: ${stop_loss:,.2f}')
    
    def _close_position(self, price: float, reason: str):
        """平倉"""
        pos = self.portfolio.current_position
        if pos is None:
            return
        
        # 計算盈虧
        notional_value = pos.position_size * price
        close_fee = notional_value * self.config['fee_rate']
        
        if pos.direction == 'LONG':
            pnl = (price - pos.entry_price) * pos.position_size
        else:
            pnl = (pos.entry_price - price) * pos.position_size
        
        pnl -= close_fee  # 扣除平倉手續費
        pnl_pct = pnl / pos.margin_used * 100
        
        # 更新交易記錄
        pos.close_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pos.exit_price = price
        pos.pnl = pnl
        pos.pnl_pct = pnl_pct
        pos.fee_paid += close_fee
        pos.status = 'CLOSED'
        pos.close_reason = reason
        
        # 更新投資組合
        self.portfolio.balance += pos.margin_used + pnl
        self.portfolio.total_fees += close_fee
        self.portfolio.total_pnl += pnl
        self.portfolio.total_trades += 1
        
        if pnl > 0:
            self.portfolio.winning_trades += 1
            emoji = '✅'
        else:
            self.portfolio.losing_trades += 1
            emoji = '❌'
        
        # 更新最大回撤
        if self.portfolio.balance > self.portfolio.peak_balance:
            self.portfolio.peak_balance = self.portfolio.balance
        drawdown = (self.portfolio.peak_balance - self.portfolio.balance) / self.portfolio.peak_balance
        if drawdown > self.portfolio.max_drawdown:
            self.portfolio.max_drawdown = drawdown
        
        self._log(f'{emoji} 平倉 @ ${price:,.2f} | '
                 f'原因: {reason} | '
                 f'盈虧: ${pnl:+.2f} ({pnl_pct:+.2f}%)')
        
        # 保存到歷史
        self.trade_history.append(pos)
        self.portfolio.current_position = None
    
    def _check_position(self, current_price: float):
        """檢查持倉止盈止損"""
        pos = self.portfolio.current_position
        if pos is None:
            return
        
        if pos.direction == 'LONG':
            if current_price >= pos.take_profit:
                self._close_position(current_price, '止盈')
            elif current_price <= pos.stop_loss:
                self._close_position(current_price, '止損')
        else:  # SHORT
            if current_price <= pos.take_profit:
                self._close_position(current_price, '止盈')
            elif current_price >= pos.stop_loss:
                self._close_position(current_price, '止損')
    
    def _print_status(self, market: MarketData):
        """打印當前狀態"""
        print('\n' + '='*70)
        print(f'📊 市場狀態 | {market.timestamp}')
        print('='*70)
        print(f'  價格: ${market.price:,.2f} | RSI: {market.rsi:.1f} | OBI: {market.obi:+.3f}')
        print(f'  趨勢: 15m={market.trend_15m} | 1h={market.trend_1h} | 4h={market.trend_4h}')
        print(f'  🐻 空頭陷阱: {market.bear_trap_signals}/6 | 🐂 多頭陷阱: {market.bull_trap_signals}/6')
        print(f'  📊 綜合評分: {market.score:+.1f}')
        
        print('\n' + '-'*70)
        print(f'💰 投資組合狀態')
        print('-'*70)
        
        # 計算淨值（餘額 + 未實現盈虧）
        net_value = self.portfolio.balance
        unrealized_pnl = 0
        unrealized_pnl_pct = 0
        
        if self.portfolio.current_position:
            pos = self.portfolio.current_position
            if pos.direction == 'LONG':
                unrealized_pnl = (market.price - pos.entry_price) * pos.position_size
            else:
                unrealized_pnl = (pos.entry_price - market.price) * pos.position_size
            unrealized_pnl -= pos.fee_paid  # 扣除已付手續費
            unrealized_pnl_pct = unrealized_pnl / pos.margin_used * 100
            net_value = self.portfolio.balance + pos.margin_used + unrealized_pnl
        
        # 總收益率（相對初始資金）
        total_roi = (net_value - self.portfolio.initial_balance) / self.portfolio.initial_balance * 100
        
        print(f'  💵 可用餘額: ${self.portfolio.balance:.2f}')
        print(f'  📊 淨值:     ${net_value:.2f} ({Colors.pct(total_roi)})')
        print(f'  📈 已實現盈虧: {Colors.profit(self.portfolio.total_pnl)}')
        print(f'  💸 總手續費:   ${self.portfolio.total_fees:.2f}')
        
        win_rate = (self.portfolio.winning_trades / self.portfolio.total_trades * 100 
                   if self.portfolio.total_trades > 0 else 0)
        print(f'  🎯 交易次數: {self.portfolio.total_trades} | '
              f'勝率: {win_rate:.1f}% | '
              f'最大回撤: {self.portfolio.max_drawdown*100:.2f}%')
        
        # 持倉顯示（彩色增強）
        print('\n' + '-'*70)
        if self.portfolio.current_position:
            pos = self.portfolio.current_position
            
            # 方向顏色
            if pos.direction == 'LONG':
                direction_str = Colors.long('🟢 LONG 做多')
                price_change = (market.price - pos.entry_price) / pos.entry_price * 100
            else:
                direction_str = Colors.short('🔴 SHORT 做空')
                price_change = (pos.entry_price - market.price) / pos.entry_price * 100
            
            print(f'  📍 當前持倉: {direction_str}')
            print(f'  ┌─────────────────────────────────────────────────────────────')
            print(f'  │ 開倉價格:   ${pos.entry_price:,.2f}')
            print(f'  │ 當前價格:   ${market.price:,.2f} ({Colors.pct(price_change)})')
            print(f'  │ 持倉數量:   {pos.position_size:.6f} BTC')
            print(f'  │ 保證金:     ${pos.margin_used:.2f}')
            print(f'  │ 槓桿倍數:   {pos.leverage}X')
            print(f'  ├─────────────────────────────────────────────────────────────')
            print(f'  │ 📈 未實現盈虧: {Colors.profit(unrealized_pnl)} ({Colors.pct(unrealized_pnl_pct)})')
            print(f'  ├─────────────────────────────────────────────────────────────')
            print(f'  │ 🎯 止盈價格: ${pos.take_profit:,.2f} ({Colors.GREEN}+{self.config["take_profit_pct"]*100:.1f}%{Colors.RESET})')
            print(f'  │ 🛑 止損價格: ${pos.stop_loss:,.2f} ({Colors.RED}-{self.config["stop_loss_pct"]*100:.1f}%{Colors.RESET})')
            print(f'  └─────────────────────────────────────────────────────────────')
        else:
            print(f'  📍 當前持倉: {Colors.DIM}無持倉{Colors.RESET}')
        
        print('='*70)
    
    def run(self):
        """運行模擬器"""
        self.running = True
        interval = self.config['interval_minutes'] * 60
        
        print('\n' + '='*70)
        print('🔮 陷阱檢測模擬交易系統啟動')
        print('='*70)
        print(f'  初始資金: ${self.config["initial_balance"]:.2f}')
        print(f'  槓桿倍數: {self.config["leverage"]}X')
        print(f'  手續費率: {self.config["fee_rate"]*100:.2f}%')
        print(f'  交易間隔: {self.config["interval_minutes"]} 分鐘')
        print(f'  日誌文件: {self.log_file}')
        print('='*70)
        print('\n⚠️ 按 Ctrl+C 停止模擬\n')
        
        while self.running:
            try:
                # 獲取市場數據
                market = self.analyzer.analyze()
                self.market_history.append(market)
                
                # 檢查持倉止盈止損
                self._check_position(market.price)
                
                # 打印狀態
                self._print_status(market)
                
                # 決定交易動作
                action, confidence = self._decide_action(market)
                
                if self.portfolio.current_position is None:
                    # 沒有持倉，考慮開倉
                    if action in ['LONG', 'SHORT', 'LIGHT_LONG', 'LIGHT_SHORT']:
                        self._open_position(action, market, confidence)
                    else:
                        self._log(f'⏳ 觀望，評分 {market.score:+.1f} 不足以開倉')
                else:
                    # 有持倉，考慮是否反手
                    pos = self.portfolio.current_position
                    should_reverse = False
                    
                    if pos.direction == 'LONG' and action in ['SHORT', 'LIGHT_SHORT']:
                        should_reverse = True
                    elif pos.direction == 'SHORT' and action in ['LONG', 'LIGHT_LONG']:
                        should_reverse = True
                    
                    if should_reverse:
                        self._close_position(market.price, '信號反轉')
                        self._open_position(action, market, confidence)
                    else:
                        self._log(f'📍 維持 {pos.direction} 持倉')
                
                # 保存狀態
                self._save_state()
                
                # 等待下一個週期
                self._log(f'⏰ 下次檢查: {self.config["interval_minutes"]} 分鐘後')
                
                # 分段等待，以便能夠響應停止信號
                for _ in range(interval):
                    if not self.running:
                        break
                    time.sleep(1)
                
            except Exception as e:
                self._log(f'❌ 錯誤: {str(e)}', 'ERROR')
                time.sleep(30)
        
        # 停止時的清理
        self._log('正在保存最終狀態...')
        if self.portfolio.current_position:
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
        
        roi = (self.portfolio.balance - self.portfolio.initial_balance) / self.portfolio.initial_balance * 100
        win_rate = (self.portfolio.winning_trades / self.portfolio.total_trades * 100 
                   if self.portfolio.total_trades > 0 else 0)
        
        print(f'  初始資金:     ${self.portfolio.initial_balance:.2f}')
        print(f'  最終餘額:     ${self.portfolio.balance:.2f}')
        print(f'  總收益率:     {roi:+.2f}%')
        print(f'  總盈虧:       ${self.portfolio.total_pnl:+.2f}')
        print(f'  總手續費:     ${self.portfolio.total_fees:.2f}')
        print(f'  交易次數:     {self.portfolio.total_trades}')
        print(f'  勝率:         {win_rate:.1f}%')
        print(f'  最大回撤:     {self.portfolio.max_drawdown*100:.2f}%')
        print(f'  日誌文件:     {self.log_file}')
        print('='*70)
        
        # 打印最近交易
        if self.trade_history:
            print('\n📋 最近交易記錄:')
            print('-'*70)
            for trade in self.trade_history[-10:]:
                emoji = '✅' if trade.pnl and trade.pnl > 0 else '❌'
                print(f'  {emoji} #{trade.id} {trade.direction} | '
                      f'入場: ${trade.entry_price:,.2f} | '
                      f'出場: ${trade.exit_price:,.2f} | '
                      f'盈虧: ${trade.pnl:+.2f} ({trade.pnl_pct:+.1f}%) | '
                      f'{trade.close_reason}')


# ==================== 主程序 ====================
def main():
    """主程序入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='🔮 陷阱檢測模擬交易系統')
    parser.add_argument('--balance', type=float, default=100, help='初始資金 (USDT)')
    parser.add_argument('--leverage', type=int, default=50, help='槓桿倍數')
    parser.add_argument('--interval', type=int, default=15, help='交易間隔 (分鐘)')
    parser.add_argument('--fee', type=float, default=0.0002, help='手續費率')
    
    args = parser.parse_args()
    
    # 更新配置
    config = CONFIG.copy()
    config['initial_balance'] = args.balance
    config['leverage'] = args.leverage
    config['interval_minutes'] = args.interval
    config['fee_rate'] = args.fee
    
    # 啟動模擬器
    simulator = TrapDetectorSimulator(config)
    simulator.run()


if __name__ == '__main__':
    main()
