#!/usr/bin/env python3
"""
dYdX 24h Realtime Strategy Backtest
====================================
使用 dYdX 過去 24h 的真實交易數據模擬當前策略

核心邏輯：
1. 從 dYdX API 獲取過去 24h 的 1m K線 + 交易數據
2. 模擬六維系統的信號生成
3. 計算勝率與盈虧

限制：
- 無法回測真實的 OBI（需要 Level2 訂單簿快照）
- 用成交量不平衡模擬 OBI
- 用大額成交模擬主力偵測

Usage:
    python scripts/backtest_realtime_24h.py
    python scripts/backtest_realtime_24h.py --hours 48  # 48小時回測
"""

import asyncio
import aiohttp
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from collections import deque
import json
from pathlib import Path

# ============================================================
# 配置 (完全對齊 whale_testnet_trader.py 的參數)
# ============================================================

@dataclass
class BacktestConfig:
    """回測配置 - 對齊主程式參數"""
    # 基本設定
    symbol: str = "BTC-USD"
    leverage: int = 50
    
    # 止盈止損 (v12.8 N%鎖N%)
    use_n_lock_n: bool = True
    n_lock_n_threshold: float = 1.0
    n_lock_n_buffer: float = 0.0
    initial_stop_loss_pct: float = 2.0   # 初始止損 -2%
    take_profit_pct: float = 5.0          # 最終目標 5%
    
    # 六維系統門檻 (v13.1)
    six_dim_alignment_threshold: int = 6
    six_dim_min_score_to_trade: int = 8
    
    # 信號穩定性
    min_alignment_seconds: int = 10
    min_probability: float = 0.50
    min_signal_advantage: float = 0.15
    
    # OBI 門檻 (v13.0)
    obi_long_threshold: float = 0.10
    obi_short_threshold: float = -0.10
    
    # 動能門檻
    momentum_long_threshold: float = 0.02
    momentum_short_threshold: float = -0.02
    
    # 成交量門檻
    volume_long_threshold: float = 1.02
    volume_short_threshold: float = 0.98
    
    # 價格確認
    price_confirm_enabled: bool = True
    price_confirm_threshold: float = 0.03
    
    # 手續費 (% of notional)
    maker_fee_pct: float = 0.005
    taker_fee_pct: float = 0.04
    
    # Warmup
    warmup_candles: int = 30  # 前 30 根 K 線不交易


@dataclass
class Trade:
    """交易記錄"""
    entry_time: datetime
    entry_price: float
    side: str  # LONG or SHORT
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl_pct: float = 0.0
    exit_reason: str = ""
    max_pnl_pct: float = 0.0  # 最高盈利
    min_pnl_pct: float = 0.0  # 最大虧損


# ============================================================
# 數據獲取
# ============================================================

async def fetch_dydx_candles(hours: int = 24) -> List[Dict]:
    """從 dYdX 獲取 K 線數據"""
    # 嘗試多個 API 端點
    urls = [
        "https://indexer.dydx.trade/v4/candles/perpetualMarkets/BTC-USD",
        "https://indexer.v4.dydx.exchange/v4/candles/perpetualMarkets/BTC-USD",
    ]
    all_candles = []
    end_time = datetime.now(timezone.utc)
    target_candles = hours * 60 + 100  # 多取一些用於計算指標
    
    print(f"⏳ 正在從 dYdX 獲取 {hours}h 數據...", end="", flush=True)
    
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        url_idx = 0
        url = urls[url_idx]  # 使用主要端點
        consecutive_429 = 0
        backoff_s = 0.5
        total_errors = 0
        while len(all_candles) < target_candles:
            params = {
                "resolution": "1MIN",
                "limit": 100,
                "toISO": end_time.isoformat()
            }
            try:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 429:
                        consecutive_429 += 1
                        # simple exponential backoff with cap
                        await asyncio.sleep(min(backoff_s, 5.0))
                        backoff_s = min(backoff_s * 1.8, 5.0)
                        # switch endpoint if we keep getting rate-limited
                        if consecutive_429 >= 6 and len(urls) > 1:
                            url_idx = (url_idx + 1) % len(urls)
                            url = urls[url_idx]
                            consecutive_429 = 0
                            backoff_s = 0.5
                        continue

                    consecutive_429 = 0
                    backoff_s = 0.5

                    if resp.status != 200:
                        print(f"\n❌ API 錯誤: {resp.status}")
                        break
                    data = await resp.json()
                    candles = data.get("candles", [])
                    if not candles:
                        break
                    
                    all_candles.extend(candles)
                    last_time = candles[-1]['startedAt']
                    end_time = datetime.fromisoformat(last_time.replace("Z", "+00:00")) - timedelta(seconds=1)
                    print(".", end="", flush=True)
                    
                    await asyncio.sleep(0.15)  # 避免 rate limit
            except Exception as e:
                total_errors += 1
                print(f"\n⚠️ 獲取數據錯誤: {e}")
                # Try switching endpoint and retrying a few times instead of hard-failing.
                if len(urls) > 1 and total_errors <= 12:
                    url_idx = (url_idx + 1) % len(urls)
                    url = urls[url_idx]
                    consecutive_429 = 0
                    backoff_s = 0.5
                    await asyncio.sleep(min(0.5 + total_errors * 0.25, 3.0))
                    continue
                break
    
    print(f" 完成! ({len(all_candles)} 根)")
    return all_candles


async def fetch_dydx_trades(hours: int = 24) -> List[Dict]:
    """從 dYdX 獲取成交數據 (用於模擬大單偵測)"""
    url = "https://indexer.dydx.trade/v4/trades/perpetualMarket/BTC-USD"
    all_trades = []
    
    print(f"⏳ 正在從 dYdX 獲取成交數據...", end="", flush=True)
    
    async with aiohttp.ClientSession() as session:
        # dYdX trades API 有限制，只取最近的
        params = {"limit": 1000}
        try:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    all_trades = data.get("trades", [])
                    print(f" 完成! ({len(all_trades)} 筆)")
        except Exception as e:
            print(f"\n⚠️ 獲取成交錯誤: {e}")
    
    return all_trades


# ============================================================
# 指標計算
# ============================================================

def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """計算 RSI"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    """計算 EMA"""
    return prices.ewm(span=period, adjust=False).mean()


def process_candles(candles: List[Dict]) -> pd.DataFrame:
    """處理 K 線數據並計算指標"""
    df = pd.DataFrame(candles)
    df['time'] = pd.to_datetime(df['startedAt'])
    df = df.sort_values('time').reset_index(drop=True)
    
    # 轉換數據類型
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    df['volume'] = df['baseTokenVolume'].astype(float)
    
    # 計算指標
    df['rsi_14'] = calculate_rsi(df['close'], 14)
    df['rsi_5m'] = calculate_rsi(df['close'], 14 * 5)  # 近似 5 分鐘 RSI
    df['rsi_15m'] = calculate_rsi(df['close'], 14 * 15)  # 近似 15 分鐘 RSI
    
    df['ema_9'] = calculate_ema(df['close'], 9)
    df['ema_21'] = calculate_ema(df['close'], 21)
    df['ema_50'] = calculate_ema(df['close'], 50)
    
    # 價格變化
    df['price_change_1m'] = df['close'].pct_change() * 100
    df['price_change_5m'] = df['close'].pct_change(5) * 100
    
    # 成交量變化
    df['vol_ma_20'] = df['volume'].rolling(20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma_20']
    
    # 模擬 OBI (用買賣壓估計)
    # 真實的 OBI 需要訂單簿，這裡用價格動能近似
    df['momentum'] = df['close'] - df['open']
    df['body_ratio'] = df['momentum'] / (df['high'] - df['low'] + 0.01)
    df['simulated_obi'] = df['body_ratio'].rolling(5).mean()  # 5 根 K 線平均
    
    return df


# ============================================================
# 六維系統模擬
# ============================================================

def calculate_six_dim_score(row: pd.Series, prev_rows: pd.DataFrame, config: BacktestConfig) -> Dict:
    """
    計算六維分數 (模擬版)
    
    原三線 (6分):
      • 快線 (5秒): ±1 分  → 用 1m K 線近似
      • 中線 (30秒): ±2 分 → 用 5m 趨勢近似
      • 慢線 (5分): ±3 分 → 用 15m 趨勢近似
    
    新三維 (6分):
      • OBI 線: ±2 分 (訂單簿失衡) → 用模擬 OBI
      • 動能線: ±2 分 (價格動能)
      • 成交量線: ±2 分 (成交量方向)
    """
    long_score = 0
    short_score = 0
    
    # === 原三線 ===
    
    # 快線 (1m 價格方向): ±1 分
    if row['price_change_1m'] > 0.02:
        long_score += 1
    elif row['price_change_1m'] < -0.02:
        short_score += 1
    
    # 中線 (5m 趨勢): ±2 分
    if row['price_change_5m'] > 0.05:
        long_score += 2
    elif row['price_change_5m'] < -0.05:
        short_score += 2
    
    # 慢線 (EMA 趨勢): ±3 分
    if row['ema_9'] > row['ema_21'] > row['ema_50']:
        long_score += 3
    elif row['ema_9'] < row['ema_21'] < row['ema_50']:
        short_score += 3
    elif row['ema_9'] > row['ema_21']:
        long_score += 1
    elif row['ema_9'] < row['ema_21']:
        short_score += 1
    
    # === 新三維 ===
    
    # OBI 線 (模擬): ±2 分
    obi = row['simulated_obi']
    if obi > config.obi_long_threshold:
        long_score += 2
        obi_dir = 'LONG'
    elif obi < config.obi_short_threshold:
        short_score += 2
        obi_dir = 'SHORT'
    else:
        obi_dir = 'NEUTRAL'
    
    # 動能線: ±2 分
    momentum_pct = row['price_change_5m']
    if momentum_pct > config.momentum_long_threshold:
        long_score += 2
        momentum_dir = 'LONG'
    elif momentum_pct < config.momentum_short_threshold:
        short_score += 2
        momentum_dir = 'SHORT'
    else:
        momentum_dir = 'NEUTRAL'
    
    # 成交量線: ±2 分
    vol_ratio = row['vol_ratio']
    # 高成交量 + 上漲 = 買壓
    if vol_ratio > 1.2 and row['momentum'] > 0:
        long_score += 2
        volume_dir = 'LONG'
    elif vol_ratio > 1.2 and row['momentum'] < 0:
        short_score += 2
        volume_dir = 'SHORT'
    else:
        volume_dir = 'NEUTRAL'
    
    return {
        'long_score': long_score,
        'short_score': short_score,
        'score': max(long_score, short_score),
        'obi': obi,
        'obi_dir': obi_dir,
        'momentum_dir': momentum_dir,
        'volume_dir': volume_dir,
    }


# ============================================================
# 🆕 v13.3 Veto 檢查 (模擬真實邏輯)
# ============================================================

def check_entry_veto(direction: str, six_dim: Dict, row: pd.Series, config: BacktestConfig) -> Tuple[bool, str]:
    """
    模擬 check_entry_veto 函數
    """
    # 1. 六維分數檢查
    if direction == "LONG":
        score = six_dim['long_score']
    else:
        score = six_dim['short_score']
    
    if score < config.six_dim_min_score_to_trade:
        return False, f"六維分數不足 ({score} < {config.six_dim_min_score_to_trade})"
    
    # 2. OBI 方向衝突檢查
    obi = six_dim['obi']
    obi_dir = six_dim['obi_dir']
    momentum_dir = six_dim['momentum_dir']
    volume_dir = six_dim['volume_dir']
    
    if direction == "LONG":
        if obi_dir == 'SHORT' or momentum_dir == 'SHORT' or volume_dir == 'SHORT':
            return False, f"方向衝突: OBI/動能/量偏空"
        if obi < -0.2:
            return False, f"OBI {obi:.2f} < -0.2 嚴重背離"
    else:  # SHORT
        if obi_dir == 'LONG' or momentum_dir == 'LONG' or volume_dir == 'LONG':
            return False, f"方向衝突: OBI/動能/量偏多"
        if obi > 0.2:
            return False, f"OBI {obi:.2f} > 0.2 嚴重背離"
    
    # 3. 價格確認檢查
    if config.price_confirm_enabled:
        price_change_1m = row['price_change_1m']
        threshold = config.price_confirm_threshold
        
        if direction == "LONG" and price_change_1m < -threshold:
            return False, f"價格確認失敗: 做多但價跌 {price_change_1m:.3%}"
        elif direction == "SHORT" and price_change_1m > threshold:
            return False, f"價格確認失敗: 做空但價漲 {price_change_1m:.3%}"
    
    return True, ""


# ============================================================
# 策略模擬
# ============================================================

def simulate_strategy(df: pd.DataFrame, config: BacktestConfig) -> List[Trade]:
    """模擬交易策略"""
    trades = []
    position: Optional[Trade] = None

    entry_fee_pct = config.maker_fee_pct

    def _net_roe_pct(gross_roe_pct: float, exit_fee_pct: float) -> float:
        return gross_roe_pct - (entry_fee_pct + exit_fee_pct) * config.leverage
    
    # 追蹤六維對齊時間
    long_alignment_sec = 0
    short_alignment_sec = 0
    
    print(f"\n📊 開始模擬交易 (共 {len(df)} 根 K 線)...")
    
    for i in range(config.warmup_candles, len(df)):
        row = df.iloc[i]
        prev_rows = df.iloc[max(0, i-20):i]
        
        current_price = row['close']
        current_time = row['time']
        
        # 計算六維分數
        six_dim = calculate_six_dim_score(row, prev_rows, config)
        long_score = six_dim['long_score']
        short_score = six_dim['short_score']
        
        # === 處理現有持倉 ===
        if position:
            # 計算當前盈虧
            if position.side == 'LONG':
                gross_pnl_pct = (current_price - position.entry_price) / position.entry_price * 100 * config.leverage
            else:
                gross_pnl_pct = (position.entry_price - current_price) / position.entry_price * 100 * config.leverage

            # 淨 ROE%（含手續費）；止損/鎖利視為 Taker 出場，止盈視為 Maker 出場
            net_pnl_pct_tp = _net_roe_pct(gross_pnl_pct, config.maker_fee_pct)
            net_pnl_pct_stop = _net_roe_pct(gross_pnl_pct, config.taker_fee_pct)
            
            # 更新最高/最低盈虧
            position.max_pnl_pct = max(position.max_pnl_pct, net_pnl_pct_stop)
            position.min_pnl_pct = min(position.min_pnl_pct, net_pnl_pct_stop)
            
            # N%鎖N% 止盈止損
            should_exit = False
            exit_reason = ""
            exit_pnl_pct: Optional[float] = None
            
            if config.use_n_lock_n and net_pnl_pct_stop >= config.n_lock_n_threshold:
                # 已達到 N%，設定動態止損為 N - buffer
                trailing_stop = net_pnl_pct_stop - config.n_lock_n_buffer - 0.5  # 給一點緩衝
                if net_pnl_pct_stop < position.max_pnl_pct - 0.5:
                    # 從最高點回落超過 0.5%
                    should_exit = True
                    exit_pnl_pct = net_pnl_pct_stop
                    exit_reason = f"N%鎖N% (最高{position.max_pnl_pct:.1f}%→{net_pnl_pct_stop:.1f}%)"
            
            # 止損
            if net_pnl_pct_stop <= -config.initial_stop_loss_pct:
                should_exit = True
                exit_pnl_pct = net_pnl_pct_stop
                exit_reason = f"止損 ({net_pnl_pct_stop:.1f}%)"
            
            # 止盈
            if net_pnl_pct_tp >= config.take_profit_pct:
                should_exit = True
                exit_pnl_pct = net_pnl_pct_tp
                exit_reason = f"止盈 ({net_pnl_pct_tp:.1f}%)"
            
            # 信號反轉出場
            if position.side == 'LONG' and short_score >= config.six_dim_min_score_to_trade:
                should_exit = True
                exit_pnl_pct = net_pnl_pct_stop
                exit_reason = f"空方信號 (空分:{short_score}/12)"
            elif position.side == 'SHORT' and long_score >= config.six_dim_min_score_to_trade:
                should_exit = True
                exit_pnl_pct = net_pnl_pct_stop
                exit_reason = f"多方信號 (多分:{long_score}/12)"
            
            if should_exit:
                position.exit_time = current_time
                position.exit_price = current_price
                position.pnl_pct = net_pnl_pct_stop if exit_pnl_pct is None else exit_pnl_pct
                position.exit_reason = exit_reason
                trades.append(position)
                position = None
                long_alignment_sec = 0
                short_alignment_sec = 0
            
            continue  # 有持倉時不開新倉
        
        # === 六維對齊累積 ===
        if long_score >= config.six_dim_alignment_threshold:
            long_alignment_sec += 60  # 1 分鐘 = 60 秒
            short_alignment_sec = 0
        elif short_score >= config.six_dim_alignment_threshold:
            short_alignment_sec += 60
            long_alignment_sec = 0
        else:
            long_alignment_sec = max(0, long_alignment_sec - 30)
            short_alignment_sec = max(0, short_alignment_sec - 30)
        
        # === 進場條件 ===
        direction = None
        
        # 多方達標
        if (long_alignment_sec >= config.min_alignment_seconds * 60 and 
            long_score >= config.six_dim_min_score_to_trade):
            
            # 價格確認
            if config.price_confirm_enabled:
                if row['price_change_1m'] < -config.price_confirm_threshold:
                    continue  # 價格下跌，不做多
            
            # OBI/動能/成交量方向衝突檢查
            if six_dim['obi_dir'] == 'SHORT' or six_dim['momentum_dir'] == 'SHORT':
                continue  # 方向衝突
            
            direction = 'LONG'
        
        # 空方達標
        elif (short_alignment_sec >= config.min_alignment_seconds * 60 and 
              short_score >= config.six_dim_min_score_to_trade):
            
            # 價格確認
            if config.price_confirm_enabled:
                if row['price_change_1m'] > config.price_confirm_threshold:
                    continue  # 價格上漲，不做空
            
            # OBI/動能/成交量方向衝突檢查
            if six_dim['obi_dir'] == 'LONG' or six_dim['momentum_dir'] == 'LONG':
                continue  # 方向衝突
            
            direction = 'SHORT'
        
        # === 開倉 ===
        if direction:
            position = Trade(
                entry_time=current_time,
                entry_price=current_price,
                side=direction,
            )
            long_alignment_sec = 0
            short_alignment_sec = 0
    
    # 處理未平倉
    if position:
        final_price = df.iloc[-1]['close']
        if position.side == 'LONG':
            gross_pnl_pct = (final_price - position.entry_price) / position.entry_price * 100 * config.leverage
        else:
            gross_pnl_pct = (position.entry_price - final_price) / position.entry_price * 100 * config.leverage
        
        position.exit_time = df.iloc[-1]['time']
        position.exit_price = final_price
        position.pnl_pct = _net_roe_pct(gross_pnl_pct, config.taker_fee_pct)
        position.exit_reason = "回測結束"
        trades.append(position)
    
    return trades


# ============================================================
# 報告
# ============================================================

def print_report(trades: List[Trade], config: BacktestConfig, hours: int):
    """打印回測報告"""
    print("\n" + "=" * 70)
    print(f"📊 dYdX {hours}h 回測報告 (六維系統模擬)")
    print("=" * 70)
    
    if not trades:
        print("❌ 無交易")
        return
    
    # 統計
    total = len(trades)
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / total * 100 if total > 0 else 0
    
    total_pnl = sum(t.pnl_pct for t in trades)
    avg_win = sum(t.pnl_pct for t in wins) / win_count if wins else 0
    avg_loss = sum(t.pnl_pct for t in losses) / loss_count if losses else 0
    
    best_trade = max(trades, key=lambda t: t.pnl_pct)
    worst_trade = min(trades, key=lambda t: t.pnl_pct)
    
    # 勝率/敗率分析
    long_trades = [t for t in trades if t.side == 'LONG']
    short_trades = [t for t in trades if t.side == 'SHORT']
    long_wins = len([t for t in long_trades if t.pnl_pct > 0])
    short_wins = len([t for t in short_trades if t.pnl_pct > 0])
    
    print(f"\n📈 總體統計:")
    print(f"   總交易數: {total}")
    print(f"   獲勝: {win_count} | 虧損: {loss_count}")
    print(f"   勝率: {win_rate:.1f}%")
    print(f"   總盈虧: {total_pnl:+.2f}%")
    print(f"   平均獲勝: {avg_win:+.2f}%")
    print(f"   平均虧損: {avg_loss:+.2f}%")
    
    print(f"\n📊 方向分析:")
    print(f"   多單: {len(long_trades)} 筆 | 勝 {long_wins} ({long_wins/len(long_trades)*100:.0f}% if long_trades else 0)")
    print(f"   空單: {len(short_trades)} 筆 | 勝 {short_wins} ({short_wins/len(short_trades)*100:.0f}% if short_trades else 0)")
    
    print(f"\n🏆 最佳/最差:")
    print(f"   最佳: {best_trade.pnl_pct:+.2f}% ({best_trade.side} @ {best_trade.entry_time})")
    print(f"   最差: {worst_trade.pnl_pct:+.2f}% ({worst_trade.side} @ {worst_trade.entry_time})")
    
    print(f"\n⚙️ 當前配置:")
    print(f"   六維門檻: {config.six_dim_alignment_threshold}/12")
    print(f"   最低分數: {config.six_dim_min_score_to_trade}/12")
    print(f"   對齊時間: {config.min_alignment_seconds}秒")
    print(f"   止損: -{config.initial_stop_loss_pct}%")
    print(f"   止盈: +{config.take_profit_pct}%")
    print(f"   N%鎖N%: {'啟用' if config.use_n_lock_n else '停用'}")
    
    print("\n" + "=" * 70)
    
    # 最近 10 筆交易
    print("\n📋 最近 10 筆交易:")
    for t in trades[-10:]:
        emoji = "🟢" if t.pnl_pct > 0 else "🔴"
        print(f"   {emoji} {t.side:5} | {t.pnl_pct:+6.2f}% | {t.entry_time.strftime('%m-%d %H:%M')} → {t.exit_time.strftime('%H:%M') if t.exit_time else 'N/A'} | {t.exit_reason}")
    
    return {
        'total_trades': total,
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
    }


# ============================================================
# 主程式
# ============================================================

async def main():
    import argparse
    parser = argparse.ArgumentParser(description='dYdX 24h 回測')
    parser.add_argument('--hours', type=int, default=24, help='回測時數')
    args = parser.parse_args()
    
    print("=" * 70)
    print(f"🚀 dYdX {args.hours}h 回測 (六維系統模擬)")
    print("=" * 70)
    print("⚠️ 注意: 此回測使用模擬 OBI (非真實訂單簿)")
    print("   真實勝率可能因 OBI 準確度而有 ±10% 差異")
    print("=" * 70)
    
    # 獲取數據
    candles = await fetch_dydx_candles(args.hours)
    if len(candles) < 100:
        print("❌ 數據不足")
        return
    
    # 處理數據
    df = process_candles(candles)
    print(f"📊 時間範圍: {df['time'].min()} → {df['time'].max()}")
    print(f"   價格範圍: ${df['close'].min():,.0f} ~ ${df['close'].max():,.0f}")
    
    # 執行回測
    config = BacktestConfig()
    trades = simulate_strategy(df, config)
    
    # 打印報告
    results = print_report(trades, config, args.hours)
    
    # 保存結果
    output_file = Path(f"backtest_results/realtime_{args.hours}h_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    output_file.parent.mkdir(exist_ok=True)
    
    with open(output_file, 'w') as f:
        json.dump({
            'config': {
                'hours': args.hours,
                'six_dim_threshold': config.six_dim_alignment_threshold,
                'min_score': config.six_dim_min_score_to_trade,
                'stop_loss': config.initial_stop_loss_pct,
                'take_profit': config.take_profit_pct,
            },
            'results': results,
            'trades': [
                {
                    'entry_time': t.entry_time.isoformat(),
                    'exit_time': t.exit_time.isoformat() if t.exit_time else None,
                    'side': t.side,
                    'entry_price': t.entry_price,
                    'exit_price': t.exit_price,
                    'pnl_pct': t.pnl_pct,
                    'exit_reason': t.exit_reason,
                }
                for t in trades
            ]
        }, f, indent=2, default=str)
    
    print(f"\n💾 結果已保存至: {output_file}")


if __name__ == "__main__":
    asyncio.run(main())
