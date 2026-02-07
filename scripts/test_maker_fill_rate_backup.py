#!/usr/bin/env python3
"""
🧪 Maker 成交率測試
==================
測試 Maker 訂單在 2 秒內的成交機率，以及 Taker 出場的滑點

測試流程:
1. 用 Maker (bid/ask) 開倉，等待 2 秒
2. 若未成交則取消
3. 若成交，用 Taker 平倉
4. 記錄統計數據

使用方式:
    python scripts/test_maker_fill_rate.py --testnet --trades 10 --size 10
"""

import asyncio
import argparse
import time
import json
import math
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sys
import os

# ANSI 顏色碼
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    # 前景色
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    
    # 背景色
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    
    @staticmethod
    def red(text): return f"{Colors.RED}{text}{Colors.RESET}"
    @staticmethod
    def green(text): return f"{Colors.GREEN}{text}{Colors.RESET}"
    @staticmethod
    def yellow(text): return f"{Colors.YELLOW}{text}{Colors.RESET}"
    @staticmethod
    def blue(text): return f"{Colors.BLUE}{text}{Colors.RESET}"
    @staticmethod
    def cyan(text): return f"{Colors.CYAN}{text}{Colors.RESET}"
    @staticmethod
    def magenta(text): return f"{Colors.MAGENTA}{text}{Colors.RESET}"
    @staticmethod
    def bold(text): return f"{Colors.BOLD}{text}{Colors.RESET}"
    @staticmethod
    def dim(text): return f"{Colors.DIM}{text}{Colors.RESET}"

# 添加項目路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

# 嘗試導入 dYdX
try:
    from scripts.dydx_whale_trader import DydxAPI, DydxConfig
    DYDX_AVAILABLE = True
except ImportError as e:
    print(f"❌ 無法導入 dYdX API: {e}")
    DYDX_AVAILABLE = False
    sys.exit(1)

try:
    from src.dydx_data_hub import DydxDataHub
except ImportError as e:
    print(f"❌ 無法導入 DydxDataHub: {e}")
    sys.exit(1)


class MakerFillRateTest:
    """Maker 成交率測試器"""
    
    def __init__(
        self,
        testnet: bool = True,
        size_usdc: float = 20.0,
        maker_timeout: float = 2.0,
        drift_windows: Optional[List[float]] = None,
        price_sources: Optional[List[str]] = None,
        entry_anchors: Optional[List[str]] = None,
        tp_bps: float = 5.0,   # TP ROE% (預設 5% = 價格 0.1%)
        sl_bps: float = 0.5,   # SL ROE% (預設 0.5% = 價格 0.01%)
        max_hold: float = 30.0,
        tp_exit: str = "maker",
        sl_exit: str = "taker",
        maker_exit_timeout: float = 2.0,
        poll_interval: float = 0.25,
        leverage: int = 50,
        midpoint_ratio: float = 0.8,  # M%M 鎖利比例
        no_timeout: bool = False,  # 不設超時，純靠 M%M 止盈止損
        max_loss: float = 0.0,  # 最大虧損限制 (0=不限制)
        max_spread: float = 0.0,  # 最大 spread 過濾 (0=不限制)
    ):
        self.testnet = testnet
        self.size_usdc = size_usdc
        self.no_timeout = no_timeout
        self.maker_timeout = maker_timeout
        self.api: Optional[DydxAPI] = None
        self.ws_hub: Optional[DydxDataHub] = None
        self.drift_windows = sorted(drift_windows or [0.5, 1.0, 2.0, 5.0])
        self.price_sources = self._normalize_sources(price_sources)
        self.entry_anchors = self._normalize_anchors(entry_anchors)
        self.tp_bps = float(tp_bps)
        self.sl_bps = float(sl_bps)
        self.max_hold = float(max_hold)
        self.tp_exit = (tp_exit or "maker").strip().lower()
        self.sl_exit = (sl_exit or "taker").strip().lower()
        self.maker_exit_timeout = float(maker_exit_timeout)
        self.poll_interval = float(poll_interval)
        self.leverage = int(leverage)
        self.midpoint_ratio = float(midpoint_ratio)
        self.max_loss = float(max_loss)  # 最大虧損限制
        self.max_spread = float(max_spread)  # 最大 spread 過濾
        if self.tp_exit not in ("maker", "taker"):
            self.tp_exit = "maker"
        if self.sl_exit not in ("maker", "taker"):
            self.sl_exit = "taker"
        
        # 統計
        self.results: List[Dict] = []
        self.maker_fills = 0
        self.maker_misses = 0
        
        # 累計統計 (真實交易)
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        self.best_pnl = float('-inf')
        self.worst_pnl = float('inf')
        
        # 隨機波次系統
        self.random_mode = True  # 啟用隨機模式
        self.wave1: List[str] = []  # 第1波 (11個)
        self.wave2: List[str] = []  # 第2波 (20個)
        self.wave1_index = 0  # 第1波當前索引
        self.wave2_index = 0  # 第2波當前索引
        self.current_wave = 1  # 當前波次
        self._init_waves()

    def _init_waves(self):
        """初始化隨機波次"""
        self.wave1 = self._generate_wave(11)
        self.wave2 = self._generate_wave(20)
        self.wave1_index = 0
        self.wave2_index = 0
        self.current_wave = 1

    def _generate_wave(self, count: int) -> List[str]:
        """生成隨機波次 (50:50 多空)"""
        half = count // 2
        wave = ["LONG"] * half + ["SHORT"] * (count - half)
        random.shuffle(wave)
        return wave

    def _get_next_direction(self) -> str:
        """從波次中取得下一個方向（預覽，不消耗）"""
        if self.current_wave == 1:
            if self.wave1_index < len(self.wave1):
                return self.wave1[self.wave1_index]
            else:
                # 第1波用完，切換到第2波，並重抽第1波
                self.current_wave = 2
                self.wave1 = self._generate_wave(20)  # 重抽20個
                self.wave1_index = 0
                return self._get_next_direction()
        else:
            if self.wave2_index < len(self.wave2):
                return self.wave2[self.wave2_index]
            else:
                # 第2波用完，切換回第1波，並重抽第2波
                self.current_wave = 1
                self.wave2 = self._generate_wave(20)  # 重抽20個
                self.wave2_index = 0
                return self._get_next_direction()

    def _consume_direction(self):
        """成交後消耗一個點數"""
        if self.current_wave == 1:
            if self.wave1_index < len(self.wave1):
                self.wave1_index += 1
        else:
            if self.wave2_index < len(self.wave2):
                self.wave2_index += 1

    def _print_wave_status(self):
        """打印波次狀態"""
        def format_wave(wave: List[str], current_idx: int) -> str:
            dots = []
            for i, d in enumerate(wave):
                if i < current_idx:
                    dots.append(Colors.dim("⚫"))  # 已使用
                else:
                    if d == "LONG":
                        dots.append(Colors.green("🟢"))
                    else:
                        dots.append(Colors.red("🔴"))
            return ", ".join(dots)
        
        print(f"\n{Colors.cyan('🎲 隨機入場模式')} - 策略分析區塊已隱藏 (進場方向隨機，出場按止盈止損)")
        
        # 第1波
        wave1_remaining = len(self.wave1) - self.wave1_index
        wave1_str = format_wave(self.wave1, self.wave1_index)
        wave1_marker = f" {Colors.yellow('◀ 當前')}" if self.current_wave == 1 else ""
        print(f"   第1波：{wave1_str}{wave1_marker}")
        
        # 第2波
        wave2_remaining = len(self.wave2) - self.wave2_index
        wave2_str = format_wave(self.wave2, self.wave2_index)
        wave2_marker = f" {Colors.yellow('◀ 當前')}" if self.current_wave == 2 else ""
        print(f"   第2波：{wave2_str}{wave2_marker}")

    @staticmethod
    def _normalize_sources(price_sources: Optional[List[str]]) -> List[str]:
        sources = []
        for item in (price_sources or ["api", "ws"]):
            key = str(item).strip().lower()
            if key in ("api", "ws") and key not in sources:
                sources.append(key)
        return sources or ["api", "ws"]

    @staticmethod
    def _normalize_anchors(entry_anchors: Optional[List[str]]) -> List[str]:
        anchors = []
        valid_anchors = ("bid", "ask", "mid", "mid+")
        for item in (entry_anchors or ["bid", "ask"]):
            key = str(item).strip().lower()
            if key in valid_anchors and key not in anchors:
                anchors.append(key)
        return anchors or ["bid", "ask"]

    @staticmethod
    def _calc_min_safety(spread: float) -> float:
        return max(spread * 0.20, 1.0)

    @staticmethod
    def _format_window(window: float) -> str:
        return f"{window:g}"

    @staticmethod
    def _signed_mid_drift_bps(mid_now: float, mid_ref: float, direction: str) -> float:
        if mid_now <= 0 or mid_ref <= 0:
            return 0.0
        raw = (mid_now - mid_ref) / mid_ref * 10000
        return raw if direction == "LONG" else -raw

    @staticmethod
    def _edge_bps(price: float, mid: float, direction: str) -> float:
        if price <= 0 or mid <= 0:
            return 0.0
        if direction == "LONG":
            return (mid - price) / mid * 10000
        return (price - mid) / mid * 10000

    def _calc_entry_limit_price(
        self,
        direction: str,
        bid: float,
        ask: float,
        anchor: str,
    ) -> Tuple[float, float]:
        spread = ask - bid
        min_safety = self._calc_min_safety(spread)
        anchor = (anchor or "bid").lower()
        mid = (bid + ask) / 2

        if direction == "LONG":
            if anchor == "ask":
                # 激進: 掛在 Ask - safety (接近 Ask)
                limit_price = max(bid, ask - min_safety)
            elif anchor == "mid":
                # 中等激進: 掛在 Mid (50% spread)
                limit_price = mid
            elif anchor == "mid+":
                # 更激進: 掛在 Mid + 25% spread (75% 位置)
                limit_price = mid + spread * 0.25
            else:  # bid
                # 保守: 掛在 Bid
                limit_price = bid
        else:  # SHORT
            if anchor == "bid":
                # 激進: 掛在 Bid + safety (接近 Bid)
                limit_price = min(ask, bid + min_safety)
            elif anchor == "mid":
                # 中等激進: 掛在 Mid (50% spread)
                limit_price = mid
            elif anchor == "mid+":
                # 更激進: 掛在 Mid - 25% spread (25% 位置)
                limit_price = mid - spread * 0.25
            else:  # ask
                # 保守: 掛在 Ask
                limit_price = ask

        return limit_price, min_safety

    def _calc_tp_sl_prices(self, direction: str, entry_price: float) -> Tuple[float, float]:
        """計算 TP/SL 價格 (tp_bps/sl_bps 為 ROE%，需除以槓桿得到價格%)"""
        tp_factor = (self.tp_bps / 100) / self.leverage  # ROE% → 價格%
        sl_factor = (self.sl_bps / 100) / self.leverage  # ROE% → 價格%
        if direction == "LONG":
            return entry_price * (1 + tp_factor), entry_price * (1 - sl_factor)
        return entry_price * (1 - tp_factor), entry_price * (1 + sl_factor)

    @staticmethod
    def _check_exit_signal(direction: str, mid: float, tp_price: float, sl_price: float) -> str:
        if direction == "LONG":
            if mid >= tp_price:
                return "TP"
            if mid <= sl_price:
                return "SL"
        else:
            if mid <= tp_price:
                return "TP"
            if mid >= sl_price:
                return "SL"
        return ""

    def _calc_exit_expected_prices(
        self,
        direction: str,
        bid: float,
        ask: float,
    ) -> Tuple[float, float, str, str]:
        spread = ask - bid
        min_safety = self._calc_min_safety(spread)
        if direction == "LONG":
            expected_taker = bid
            expected_maker = max(bid, ask - min_safety)
            exit_side = "SHORT"
            exit_dir = "SHORT"
        else:
            expected_taker = ask
            expected_maker = min(ask, bid + min_safety)
            exit_side = "LONG"
            exit_dir = "LONG"
        return expected_maker, expected_taker, exit_side, exit_dir

    def _calc_pnl_pct(self, direction: str, entry_price: float, current_price: float) -> float:
        """計算 PnL% (未乘槓桿)"""
        if entry_price <= 0:
            return 0.0
        if direction == "LONG":
            return (current_price - entry_price) / entry_price * 100
        else:
            return (entry_price - current_price) / entry_price * 100

    def _calc_roe_pct(self, direction: str, entry_price: float, current_price: float) -> float:
        """計算 ROE% (乘槓桿)"""
        return self._calc_pnl_pct(direction, entry_price, current_price) * self.leverage

    def _calc_midpoint_lock_price(self, direction: str, entry_price: float, peak_pnl_pct: float) -> float:
        """計算 M%M 鎖利止損價"""
        lock_pct = peak_pnl_pct * self.midpoint_ratio
        if direction == "LONG":
            return entry_price * (1 + lock_pct / 100)
        else:
            return entry_price * (1 - lock_pct / 100)

    def _print_position_status(
        self,
        direction: str,
        entry_price: float,
        btc_size: float,
        current_mid: float,
        peak_pnl_pct: float,
        current_sl_price: float,  # 當前止損線
        tp_price: float,
        current_sl_pct: Optional[float] = None,  # 當前鎖利百分比
        sl_stage: str = "",  # 階段描述
    ):
        """打印詳細持倉狀態"""
        pnl_pct = self._calc_pnl_pct(direction, entry_price, current_mid)
        roe_pct = pnl_pct * self.leverage
        
        # 計算原始 SL
        original_tp, original_sl = self._calc_tp_sl_prices(direction, entry_price)
        
        # 計算實際盈虧
        if direction == "LONG":
            pnl_usdt = (current_mid - entry_price) * btc_size
        else:
            pnl_usdt = (entry_price - current_mid) * btc_size
        
        # 獲利狀態 (帶顏色)
        if pnl_pct > 0:
            roe_str = Colors.green(f"{roe_pct:+.2f}%")
            pnl_str = Colors.green(f"${pnl_usdt:+.4f}")
        else:
            roe_str = Colors.red(f"{roe_pct:+.2f}%")
            pnl_str = Colors.red(f"${pnl_usdt:+.4f}")
        
        if direction == "SHORT":
            direction_emoji = Colors.red("🔴")
            direction_str = Colors.red(direction)
        else:
            direction_emoji = Colors.green("🟢")
            direction_str = Colors.green(direction)
        
        print(f"\n   {direction_emoji} {Colors.bold('[dYdX 真實]')} {direction_str}")
        print(f"      進場: {Colors.cyan(f'${entry_price:,.2f}')} | 數量: {Colors.yellow(f'{btc_size:.4f}')} BTC")
        print(f"      槓桿: {Colors.magenta(f'{self.leverage}X')} (ROE% = 價格%×{self.leverage})")
        print(f"      浮動: {roe_str}  💵 淨盈虧: {roe_str} ({pnl_str})")
        
        # TP 價格
        tp_diff_pct = abs(tp_price - entry_price) / entry_price * 100
        tp_roe = tp_diff_pct * self.leverage
        print(f"      TP: {Colors.green(f'${tp_price:,.2f}')} (ROE +{tp_roe:.1f}%)")
        
        # 原始止損
        original_sl_diff_pct = abs(original_sl - entry_price) / entry_price * 100
        original_sl_roe = original_sl_diff_pct * self.leverage
        
        # M%M 階梯式鎖利
        print(f"      {Colors.yellow('🔐 M%M 階梯式鎖利')}")
        roe_pnl = pnl_pct * self.leverage
        roe_peak = peak_pnl_pct * self.leverage
        pnl_color = Colors.green if pnl_pct > 0 else Colors.red
        peak_color = Colors.green if peak_pnl_pct > 0 else Colors.dim
        print(f"         當前: {pnl_color(f'{pnl_pct:+.2f}%')} (ROE {pnl_color(f'{roe_pnl:+.1f}%')})")
        print(f"         最高: {peak_color(f'{peak_pnl_pct:+.2f}%')} (ROE {peak_color(f'{roe_peak:+.1f}%')})")
        
        if current_sl_pct is None:
            # 未達門檻，使用原始止損
            print(f"         狀態: {Colors.dim(f'📊 等待中 (ROE {roe_peak:.1f}% < 0.5%，讓利潤跑)')}")
            print(f"         🎯 止損線: {Colors.red(f'${original_sl:,.2f}')} ({Colors.red(f'-{original_sl_diff_pct:.2f}%')} / ROE {Colors.red(f'-{original_sl_roe:.1f}%')})")
        else:
            # 階梯鎖利
            sl_roe = current_sl_pct * self.leverage
            roe_peak = peak_pnl_pct * self.leverage
            print(f"         狀態: {Colors.yellow(f'🔒 {sl_stage}')} (最高 ROE {roe_peak:.1f}% → 鎖住 ROE {sl_roe:.1f}%)")
            print(f"         🎯 止損線: {Colors.cyan(f'${current_sl_price:,.2f}')} (ROE {Colors.green(f'+{sl_roe:.1f}%')})")
        
        # 顯示階梯規則 (ROE%)
        print(f"         📋 規則(ROE): <0.7%→原始SL | 0.7~0.9%→+0.2%(保本) | 1~1.4%→+0.5% | 1.5~1.9%→+1% | ...")

    def _print_realtime_stats(self):
        """打印即時統計 (格式 B)"""
        win_rate = self.wins / self.total_trades * 100 if self.total_trades > 0 else 0.0
        avg_pnl = self.total_pnl / self.total_trades if self.total_trades > 0 else 0.0
        
        best_str = Colors.green(f"${self.best_pnl:+.4f}") if self.best_pnl != float('-inf') else Colors.dim("N/A")
        worst_str = Colors.red(f"${self.worst_pnl:+.4f}") if self.worst_pnl != float('inf') else Colors.dim("N/A")
        
        # 勝率顏色
        if win_rate >= 60:
            win_rate_str = Colors.green(f"{win_rate:.1f}%")
        elif win_rate >= 40:
            win_rate_str = Colors.yellow(f"{win_rate:.1f}%")
        else:
            win_rate_str = Colors.red(f"{win_rate:.1f}%")
        
        # 總盈虧顏色
        if self.total_pnl > 0:
            total_pnl_str = Colors.green(f"${self.total_pnl:.4f}")
        elif self.total_pnl < 0:
            total_pnl_str = Colors.red(f"${self.total_pnl:.4f}")
        else:
            total_pnl_str = f"${self.total_pnl:.4f}"
        
        print(f"\n   {Colors.cyan('📊 真實統計 (WS):')}")
        # 計算打平數
        draws = self.total_trades - self.wins - self.losses
        print(f"      總交易: {Colors.bold(f'{self.total_trades}筆')}  |  勝: {Colors.green(str(self.wins))}  敗: {Colors.red(str(self.losses))}  平: {draws}  |  勝率: {win_rate_str}")
        print(f"      總盈虧: {total_pnl_str}  |  平均: ${avg_pnl:.4f}/筆")
        print(f"      最佳: {best_str}  最差: {worst_str}")

    async def _wait_for_exit_signal_with_mpm(
        self,
        direction: str,
        entry_price: float,
        btc_size: float,
    ) -> Tuple[str, float, float, float, Dict[str, float], float, float]:
        """等待出場信號，支援 M%M 階梯式鎖利"""
        tp_price, sl_price = self._calc_tp_sl_prices(direction, entry_price)
        start = time.time()
        peak_pnl_pct = 0.0
        last_print_time = 0.0
        print_interval = 2.0  # 每 2 秒打印一次狀態

        while self.no_timeout or (time.time() - start < self.max_hold):
            bid, ask, mid, ws_snap = await self.get_dual_snapshot()
            
            # 計算當前 PnL%
            pnl_pct = self._calc_pnl_pct(direction, entry_price, mid)
            
            # 更新峰值
            if pnl_pct > peak_pnl_pct:
                peak_pnl_pct = pnl_pct
            
            # M%M 階梯式鎖利止損計算
            # 關鍵: 只看「峰值」決定鎖利線，不管當前浮動
            # - 峰值 ROE < 0%: 從未獲利過 → 使用原始止損 (-0.5%)
            # - 峰值 ROE >= 0%: 曾經獲利過 → 啟用 M%M 階梯鎖利
            current_sl_pct, sl_stage = self._calc_mpm_sl_pct(peak_pnl_pct)
            
            if current_sl_pct is None:
                # 從未獲利過，使用原始止損
                current_sl_price = sl_price
            else:
                # 曾經獲利過，使用 M%M 鎖利止損
                if direction == "LONG":
                    current_sl_price = entry_price * (1 + current_sl_pct / 100)
                else:
                    current_sl_price = entry_price * (1 - current_sl_pct / 100)

            # 定期打印持倉狀態
            now = time.time()
            if now - last_print_time >= print_interval:
                self._print_position_status(
                    direction, entry_price, btc_size, mid,
                    peak_pnl_pct, current_sl_price, tp_price, current_sl_pct, sl_stage
                )
                last_print_time = now

            # 檢查 TP
            if direction == "LONG" and mid >= tp_price:
                return "TP", bid, ask, mid, ws_snap, time.time() - start, peak_pnl_pct
            if direction == "SHORT" and mid <= tp_price:
                return "TP", bid, ask, mid, ws_snap, time.time() - start, peak_pnl_pct
            
            # 檢查止損
            if direction == "LONG" and mid <= current_sl_price:
                if current_sl_pct is not None and current_sl_pct >= 0:
                    print(f"\n   {Colors.yellow('⚡ 觸發 M%M 鎖利!')} mid=${mid:.2f} <= 鎖利線=${current_sl_price:.2f} (鎖住 +{current_sl_pct:.1f}%)")
                    return "M%M_LOCK", bid, ask, mid, ws_snap, time.time() - start, peak_pnl_pct
                else:
                    print(f"\n   {Colors.red('⚡ 觸發止損!')} mid=${mid:.2f} <= 止損線=${current_sl_price:.2f}")
                    return "SL", bid, ask, mid, ws_snap, time.time() - start, peak_pnl_pct
            if direction == "SHORT" and mid >= current_sl_price:
                if current_sl_pct is not None and current_sl_pct >= 0:
                    print(f"\n   {Colors.yellow('⚡ 觸發 M%M 鎖利!')} mid=${mid:.2f} >= 鎖利線=${current_sl_price:.2f} (鎖住 +{current_sl_pct:.1f}%)")
                    return "M%M_LOCK", bid, ask, mid, ws_snap, time.time() - start, peak_pnl_pct
                else:
                    print(f"\n   {Colors.red('⚡ 觸發止損!')} mid=${mid:.2f} >= 止損線=${current_sl_price:.2f}")
                    return "SL", bid, ask, mid, ws_snap, time.time() - start, peak_pnl_pct

            await asyncio.sleep(self.poll_interval)

        bid, ask, mid, ws_snap = await self.get_dual_snapshot()
        return "TIMEOUT", bid, ask, mid, ws_snap, time.time() - start, peak_pnl_pct

    def _calc_mpm_sl_pct(self, peak_pnl_pct: float) -> Tuple[Optional[float], str]:
        """
        計算 M%M 階梯式鎖利止損百分比 (基於 ROE%)
        
        規則 (ROE%，已乘槓桿):
        - 未獲利: 原始止損
        - ROE 0% ~ 0.69%: 不啟動鎖利，使用原始止損 (讓利潤跑起來)
        - ROE 0.7% ~ 0.9%: 保本線 (鎖 +0.2%，預留滑點)
        - ROE 1.0% ~ 1.4%: 鎖 +0.5%
        - ROE 1.5% ~ 1.9%: 鎖 +1.0%
        - ROE 2.0% ~ 2.4%: 鎖 +1.5%
        - 依此類推 (每 0.5% 一階)
        
        Returns:
            (sl_pct, stage_str): 止損「價格%」和 階段描述
            - None 表示使用原始止損
        """
        # 轉換為 ROE%
        peak_roe = peak_pnl_pct * self.leverage
        
        if peak_roe < 0.7:
            # ROE < 0.7%: 不啟動鎖利，讓利潤跑起來
            return None, "未達門檻"
        elif peak_roe < 1.0:
            # ROE 0.7% ~ 0.9%: 保本階段，鎖 +0.2% 預留滑點
            # 這樣即使滑點 0.2%，最差也是打平
            sl_roe = 0.2
            sl_pct = sl_roe / self.leverage
            return sl_pct, "保本"
        else:
            # 1.0% 以上：取到前一個 0.5 的整數倍，再減 0.5
            # 1.0~1.4 → 0.5, 1.5~1.9 → 1.0, 2.0~2.4 → 1.5, ...
            sl_roe = math.floor(peak_roe * 2) / 2 - 0.5
            stage = int((sl_roe + 0.5) * 2) - 1  # 0.5→1, 1.0→2, ...
            sl_pct = sl_roe / self.leverage
            return sl_pct, f"階段 {stage}"

    async def _cleanup_position(self, context: str = "") -> bool:
        """清理殘留持倉，返回是否有清理動作"""
        try:
            positions = await self.api.get_positions()
            for pos in positions or []:
                if pos.get('market') == 'BTC-USD' and pos.get('status') == 'OPEN':
                    pos_size = abs(float(pos.get('size', 0)))
                    if pos_size > 0.00001:
                        pos_side = pos.get('side')
                        ctx_str = f" ({context})" if context else ""
                        print(f"   {Colors.yellow(f'🧹 清理殘留持倉{ctx_str}:')} {pos_side} {pos_size:.4f} BTC")
                        await self.api._close_ioc_order(pos_side, pos_size)
                        await asyncio.sleep(0.5)
                        return True
            return False
        except Exception as e:
            print(f"   {Colors.red(f'❌ 清理持倉失敗: {e}')}")
            return False

    async def _sample_book_at(self, target_ts: float) -> Dict[str, float]:
        delay = max(0.0, target_ts - time.time())
        if delay > 0:
            await asyncio.sleep(delay)
        bid, ask, mid = await self.get_orderbook()
        return {
            "ts": time.time(),
            "bid": bid,
            "ask": ask,
            "mid": mid,
        }
        
    async def connect(self):
        """連接 dYdX"""
        config = DydxConfig()
        config.network = "testnet" if self.testnet else "mainnet"
        config.paper_trading = False  # 需要真實下單
        config.sync_real_trading = True  # 啟用節點連接
        # 依使用者設定：假設手續費為 0
        config.maker_fee_pct = 0.0
        config.taker_fee_pct = 0.0
        self.api = DydxAPI(config)
        await self.api.connect()
        print(f"✅ 已連接到 dYdX {'Testnet' if self.testnet else 'Mainnet'}")
        
        # 確認節點已連接
        if not self.api.node:
            print("⚠️ Node 未連接，嘗試重新初始化...")
            await self.api._init_node_client()
        
        if not self.api.node:
            raise Exception("❌ 無法連接 Node，無法執行真實交易")

        # 啟動 WS Data Hub (用於比較 WS 中間價)
        if self.ws_hub:
            self.ws_hub.stop()
        self.ws_hub = DydxDataHub(
            symbol="BTC-USD",
            network="testnet" if self.testnet else "mainnet",
        )
        self.ws_hub.start()

        # 等待 WS 有數據
        start = time.time()
        while time.time() - start < 10.0:
            data = self.ws_hub.get_data()
            if data.current_price > 0 or (data.bid_price > 0 and data.ask_price > 0):
                break
            await asyncio.sleep(0.2)
        
    async def get_orderbook(self) -> Tuple[float, float, float]:
        """獲取訂單簿 bid/ask/mid"""
        bid, ask = await self.api.get_best_bid_ask()
        mid = (bid + ask) / 2
        return bid, ask, mid

    def get_ws_snapshot(self) -> Dict[str, float]:
        """獲取 WS 中間價與更新時間"""
        if not self.ws_hub:
            return {"bid": 0.0, "ask": 0.0, "mid": 0.0, "age_ms": 0.0}
        data = self.ws_hub.get_data()
        bid = float(getattr(data, "bid_price", 0.0) or 0.0)
        ask = float(getattr(data, "ask_price", 0.0) or 0.0)
        mid = float(getattr(data, "current_price", 0.0) or 0.0)
        if mid <= 0 and bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        last_update = float(getattr(data, "last_update", 0.0) or 0.0)
        age_ms = (time.time() - last_update) * 1000 if last_update > 0 else 0.0
        return {"bid": bid, "ask": ask, "mid": mid, "age_ms": age_ms}

    async def get_dual_snapshot(self) -> Tuple[float, float, float, Dict[str, float]]:
        """同時取 API 與 WS 價格快照"""
        bid, ask, mid = await self.get_orderbook()
        ws = self.get_ws_snapshot()
        return bid, ask, mid, ws
    
    async def run_single_test(self, test_num: int, direction: str, price_source: str, entry_anchor: str) -> Dict:
        """執行單筆測試"""
        # 方向顯示顏色
        if direction == "LONG":
            dir_str = Colors.green(f"🟢 {direction}")
        else:
            dir_str = Colors.red(f"🔴 {direction}")
        
        print(f"\n{Colors.cyan('='*60)}")
        print(f"{Colors.bold(f'📊 測試 #{test_num}')} - {dir_str} ({Colors.yellow(entry_anchor)})")
        print(f"{Colors.cyan('='*60)}")
        sample_tasks = {}
        
        result = {
            "test_num": test_num,
            "direction": direction,
            "timestamp": datetime.now().isoformat(),
            "size_usdc": self.size_usdc,
            "maker_timeout": self.maker_timeout,
            "order_price_source_requested": price_source,
            "order_price_source": "",
            "order_bid_at_place": 0.0,
            "order_ask_at_place": 0.0,
            "order_mid_at_place": 0.0,
            "order_spread_bps": 0.0,
            "entry_anchor": entry_anchor,
            "entry_limit_price": 0.0,
            "entry_limit_offset": 0.0,
            "entry_min_safety": 0.0,
            "maker_filled": False,
            "maker_price": 0.0,
            "expected_maker_price": 0.0,
            "exit_reason": "",
            "exit_method": "",
            "exit_price": 0.0,
            "exit_expected_price": 0.0,
            "exit_maker_expected_price": 0.0,
            "exit_taker_expected_price": 0.0,
            "exit_trigger_mid": 0.0,
            "exit_trigger_bid": 0.0,
            "exit_trigger_ask": 0.0,
            "exit_trigger_ws_mid": 0.0,
            "exit_trigger_ws_age_ms": 0.0,
            "hold_seconds": 0.0,
            "tp_bps": self.tp_bps,
            "sl_bps": self.sl_bps,
            "taker_exit_price": 0.0,
            "expected_taker_price": 0.0,
            "entry_slippage_bps": 0.0,
            "exit_slippage_bps": 0.0,
            "entry_edge_bps": 0.0,
            "exit_edge_bps": 0.0,
            "api_entry_error_bps": 0.0,
            "api_exit_error_bps": 0.0,
            "ws_entry_error_bps": 0.0,
            "ws_exit_error_bps": 0.0,
            "bid_at_place": 0.0,
            "ask_at_place": 0.0,
            "mid_at_place": 0.0,
            "mid_at_result": 0.0,
            "mid_at_exit": 0.0,
            "ws_bid_at_place": 0.0,
            "ws_ask_at_place": 0.0,
            "ws_mid_at_place": 0.0,
            "ws_age_ms_at_place": 0.0,
            "ws_mid_at_result": 0.0,
            "ws_age_ms_at_result": 0.0,
            "ws_mid_at_exit": 0.0,
            "ws_age_ms_at_exit": 0.0,
            "api_mid_pnl": 0.0,
            "ws_mid_pnl": 0.0,
            "api_pnl_error": 0.0,
            "ws_pnl_error": 0.0,
            "mid_samples": {},
            "mid_drift_bps": {},
            "pnl_usdt": 0.0,
            "fee_usdt": 0.0,
            "net_pnl_usdt": 0.0,
            # Paper 交易 (用中間價模擬)
            "paper_entry_price": 0.0,
            "paper_exit_price": 0.0,
            "paper_pnl_usdt": 0.0,
            "paper_vs_dydx_entry_bps": 0.0,
            "paper_vs_dydx_exit_bps": 0.0,
            "paper_vs_dydx_pnl_diff": 0.0,
            # M%M 鎖利
            "leverage": self.leverage,
            "midpoint_ratio": self.midpoint_ratio,
            "peak_pnl_pct": 0.0,
            "error": None
        }
        
        try:
            # 1. 獲取當前訂單簿
            bid, ask, mid, ws_snap = await self.get_dual_snapshot()
            spread_bps = (ask - bid) / mid * 10000

            result["bid_at_place"] = bid
            result["ask_at_place"] = ask
            result["mid_at_place"] = mid
            result["ws_bid_at_place"] = ws_snap.get("bid", 0.0)
            result["ws_ask_at_place"] = ws_snap.get("ask", 0.0)
            result["ws_mid_at_place"] = ws_snap.get("mid", 0.0)
            result["ws_age_ms_at_place"] = ws_snap.get("age_ms", 0.0)
            
            order_source = price_source
            order_bid = bid
            order_ask = ask
            if price_source == "ws":
                ws_bid = ws_snap.get("bid", 0.0)
                ws_ask = ws_snap.get("ask", 0.0)
                if ws_bid > 0 and ws_ask > 0:
                    order_bid = ws_bid
                    order_ask = ws_ask
                else:
                    order_source = "api"

            order_mid = (order_bid + order_ask) / 2 if (order_bid > 0 and order_ask > 0) else 0.0
            order_spread = (order_ask - order_bid) / order_mid * 10000 if order_mid > 0 else 0.0
            result["order_price_source"] = order_source
            result["order_bid_at_place"] = order_bid
            result["order_ask_at_place"] = order_ask
            result["order_mid_at_place"] = order_mid
            result["order_spread_bps"] = order_spread
            entry_limit_price, min_safety = self._calc_entry_limit_price(
                direction,
                order_bid,
                order_ask,
                entry_anchor,
            )
            entry_offset = (
                entry_limit_price - order_bid if direction == "LONG" else order_ask - entry_limit_price
            )
            result["entry_limit_price"] = entry_limit_price
            result["entry_limit_offset"] = entry_offset
            result["entry_min_safety"] = min_safety

            print(f"📈 API 訂單簿: Bid={bid:.2f} | Mid={mid:.2f} | Ask={ask:.2f}")
            print(f"   API Spread: {spread_bps:.2f} bps")
            
            # Spread 過濾
            if self.max_spread > 0 and order_spread > self.max_spread:
                print(f"   ⏸️  Spread {order_spread:.2f} bps > 限制 {self.max_spread:.2f} bps，跳過此次進場")
                result["error"] = f"spread_too_high:{order_spread:.2f}"
                return result
            
            if ws_snap.get("mid", 0.0) > 0:
                ws_mid = ws_snap.get("mid", 0.0)
                ws_bid = ws_snap.get("bid", 0.0)
                ws_ask = ws_snap.get("ask", 0.0)
                ws_spread = (ws_ask - ws_bid) / ws_mid * 10000 if ws_mid > 0 else 0.0
                print(f"   WS  訂單簿: Bid={ws_bid:.2f} | Mid={ws_mid:.2f} | Ask={ws_ask:.2f} (age {ws_snap.get('age_ms', 0.0):.0f}ms)")
                print(f"   WS  Spread: {ws_spread:.2f} bps")
            print(f"   下單來源: {order_source.upper()}")
            print(f"   進場 Anchor: {entry_anchor.upper()} | Maker 價格: {entry_limit_price:.2f} | Offset: {entry_offset:.2f}")
            
            # 2. 計算倉位大小
            btc_size = self.size_usdc / mid
            btc_size = round(btc_size, 4)  # dYdX 精度
            if btc_size < 0.0001:
                btc_size = 0.0001
            
            print(f"📦 倉位大小: {Colors.yellow(f'{btc_size:.4f}')} BTC (~{Colors.green(f'${self.size_usdc:.2f}')})")
            
            # 3. Maker 開倉價格 (依 Anchor)
            maker_price = entry_limit_price
            result["expected_maker_price"] = maker_price
            
            dir_color = Colors.green if direction == "LONG" else Colors.red
            print(f"\n{Colors.bold('🎯 嘗試 Maker 開倉...')}")
            print(f"   方向: {dir_color(direction)}")
            print(f"   Maker 價格: {Colors.cyan(f'{maker_price:.2f}')} (等待 {self.maker_timeout}s)")

            start_ts = time.time()
            for window in self.drift_windows:
                key = self._format_window(window)
                sample_tasks[key] = asyncio.create_task(self._sample_book_at(start_ts + window))
            
            # 4. 下 Maker 單
            start_time = time.time()
            tx_hash, fill_price = await self.api._try_place_order(
                side=direction,
                size=btc_size,
                timeout_seconds=self.maker_timeout,
                attempt=1,
                max_attempts=1,
                best_bid=order_bid,
                best_ask=order_ask,
                limit_price=entry_limit_price,
            )
            elapsed = time.time() - start_time
            
            # 🔧 修復: 即使 _try_place_order 返回超時，也要檢查是否真的有持倉
            if not tx_hash or fill_price <= 0:
                await asyncio.sleep(0.5)  # 等待鏈上確認
                positions = await self.api.get_positions()
                for pos in positions or []:
                    if pos.get('market') == 'BTC-USD' and pos.get('status') == 'OPEN':
                        pos_size = abs(float(pos.get('size', 0)))
                        if pos_size >= 0.0001:  # 🔧 只要有持倉就算成交
                            fill_price = float(pos.get('entryPrice', 0))
                            tx_hash = "delayed_fill"
                            print(f"🔧 延遲確認成交! 價格: ${fill_price:,.2f} | 持倉: {pos_size:.4f} BTC")
                            break
            
            bid_r, ask_r, mid_r, ws_r = await self.get_dual_snapshot()
            result["mid_at_result"] = mid_r
            result["ws_mid_at_result"] = ws_r.get("mid", 0.0)
            result["ws_age_ms_at_result"] = ws_r.get("age_ms", 0.0)
            
            if tx_hash and fill_price > 0:
                # Maker 成交
                result["maker_filled"] = True
                result["maker_price"] = fill_price
                self.maker_fills += 1
                
                # 成交後才消耗隨機波次點數
                if self.random_mode:
                    self._consume_direction()
                
                # 計算進場滑點
                if direction == "LONG":
                    entry_slippage = (fill_price - maker_price) / maker_price * 10000
                else:
                    entry_slippage = (maker_price - fill_price) / maker_price * 10000
                result["entry_slippage_bps"] = entry_slippage
                result["entry_edge_bps"] = self._edge_bps(fill_price, result["order_mid_at_place"], direction)
                if result["mid_at_place"] > 0:
                    result["api_entry_error_bps"] = abs(fill_price - result["mid_at_place"]) / result["mid_at_place"] * 10000
                if result["ws_mid_at_place"] > 0:
                    result["ws_entry_error_bps"] = abs(fill_price - result["ws_mid_at_place"]) / result["ws_mid_at_place"] * 10000
                
                # Paper: 記錄開倉時的中間價
                paper_entry_mid = result["order_mid_at_place"]
                result["paper_entry_price"] = paper_entry_mid
                
                # Paper vs dYdX 進場價差 (bps)
                if paper_entry_mid > 0 and fill_price > 0:
                    if direction == "LONG":
                        # LONG: dYdX 價格越低越好，paper_entry > fill_price 代表 dYdX 更優
                        result["paper_vs_dydx_entry_bps"] = (paper_entry_mid - fill_price) / paper_entry_mid * 10000
                    else:
                        # SHORT: dYdX 價格越高越好，fill_price > paper_entry 代表 dYdX 更優
                        result["paper_vs_dydx_entry_bps"] = (fill_price - paper_entry_mid) / paper_entry_mid * 10000
                
                print(f"{Colors.green('✅ Maker 成交!')} 價格: {Colors.cyan(f'{fill_price:.2f}')} (耗時 {elapsed:.2f}s)")
                slip_color = Colors.green if entry_slippage <= 0 else Colors.red
                print(f"   進場滑點: {slip_color(f'{entry_slippage:.2f} bps')}")
                print(f"   📝 Paper 進場價 (mid): {Colors.dim(f'{paper_entry_mid:.2f}')} | 差異: {result['paper_vs_dydx_entry_bps']:+.2f} bps")
                
                # 🔧 API 驗證實際持倉 (使用 API 返回的真實數據)
                await asyncio.sleep(0.2)  # 等待結算
                positions = await self.api.get_positions()
                actual_size = 0.0
                actual_entry = 0.0
                actual_side = None
                for pos in positions or []:
                    if pos.get('market') == 'BTC-USD' and pos.get('status') == 'OPEN':
                        actual_size = abs(float(pos.get('size', 0)))
                        actual_entry = float(pos.get('entryPrice', 0))
                        actual_side = pos.get('side')
                        break
                
                if actual_size > 0 and actual_entry > 0:
                    maker_fill_price = fill_price  # 保存原始 Maker 成交價
                    entry_diff_bps = (actual_entry - maker_fill_price) / maker_fill_price * 10000 if maker_fill_price > 0 else 0
                    size_diff_pct = (actual_size - btc_size) / btc_size * 100 if btc_size > 0 else 0
                    
                    # 🔧 始終使用 API 返回的實際進場價 (這才是真實的持倉成本)
                    if abs(entry_diff_bps) > 0.5 or abs(size_diff_pct) > 1:
                        print(f"   {Colors.yellow('⚠️ API 持倉與 Maker 單不同:')}")
                        print(f"      Maker: {direction} {btc_size:.4f} BTC @ ${maker_fill_price:,.2f}")
                        print(f"      API:   {actual_side} {actual_size:.4f} BTC @ ${actual_entry:,.2f}")
                        print(f"      差異: 數量 {size_diff_pct:+.2f}% | 進場價 {entry_diff_bps:+.2f} bps")
                    else:
                        print(f"   {Colors.dim(f'📋 API 驗證: {actual_side} {actual_size:.4f} BTC @ ${actual_entry:,.2f} ✓')}")
                    
                    # 🔧 重要：使用 API 返回的實際數據進行後續計算
                    fill_price = actual_entry
                    btc_size = actual_size
                    result["maker_price"] = actual_entry
                    result["actual_entry_price"] = actual_entry
                    result["actual_size"] = actual_size
                else:
                    print(f"   {Colors.red('⚠️ API 未找到持倉，使用 Maker 掛單價')}")
                
                print(f"   📊 槓桿: {Colors.magenta(f'{self.leverage}X')} | 止損: {Colors.red(f'ROE -{self.sl_bps:.1f}%')} | M%M: {Colors.yellow(f'{self.midpoint_ratio*100:.0f}%')}")
                
                # 5. 等待 TP/SL 或超時 (使用 M%M 鎖利)
                signal, bid2, ask2, mid2, ws_exit, hold_seconds, peak_pnl_pct = await self._wait_for_exit_signal_with_mpm(
                    direction,
                    fill_price,
                    btc_size,
                )
                result["hold_seconds"] = hold_seconds
                result["exit_reason"] = signal
                result["peak_pnl_pct"] = peak_pnl_pct
                result["exit_trigger_mid"] = mid2
                result["exit_trigger_bid"] = bid2
                result["exit_trigger_ask"] = ask2
                result["exit_trigger_ws_mid"] = ws_exit.get("mid", 0.0)
                result["exit_trigger_ws_age_ms"] = ws_exit.get("age_ms", 0.0)

                expected_maker, expected_taker, exit_side, exit_dir = self._calc_exit_expected_prices(
                    direction,
                    bid2,
                    ask2,
                )
                result["expected_taker_price"] = expected_taker
                result["exit_maker_expected_price"] = expected_maker
                result["exit_taker_expected_price"] = expected_taker

                if signal == "TP":
                    print(f"\n{Colors.green('🎯 觸發 TP')}，嘗試 Maker 平倉...")
                    # 先嘗試 Maker (使用正確的 API 參數)
                    exit_tx, exit_price = await self.api._try_place_order(
                        side=exit_side,
                        size=btc_size,
                        timeout_seconds=self.maker_exit_timeout,
                        attempt=1,
                        max_attempts=1,
                        best_bid=bid2,
                        best_ask=ask2,
                    )
                    if exit_tx and exit_price > 0:
                        result["exit_method"] = "TP_MAKER"
                        result["exit_expected_price"] = expected_maker
                    else:
                        # Maker 超時，改用 Taker - 使用 _close_ioc_order (平倉專用)
                        print(f"   {Colors.yellow('⚠️ Maker 超時，改用 Taker')}")
                        exit_tx, exit_price = await self.api._close_ioc_order(direction, btc_size)
                        result["exit_method"] = "TP_TAKER"
                        result["exit_expected_price"] = expected_taker
                elif signal == "SL":
                    # SL 直接用 Taker（緊急止損）- 使用 _close_ioc_order (平倉專用)
                    print(f"\n{Colors.red('🛑 觸發 SL')}，緊急平倉 (taker)...")
                    result["exit_method"] = "SL_TAKER"
                    exit_tx, exit_price = await self.api._close_ioc_order(direction, btc_size)
                    result["exit_expected_price"] = expected_taker
                elif signal == "M%M_LOCK":
                    # M%M 鎖利 - 先嘗試 Maker
                    # 計算鎖住的百分比
                    lock_sl_pct, _ = self._calc_mpm_sl_pct(peak_pnl_pct)
                    lock_roe = (lock_sl_pct or 0) * self.leverage
                    print(f"\n{Colors.yellow(f'🔐 觸發 M%M 鎖利!')} (最高 {peak_pnl_pct:.2f}% → 鎖住 {lock_sl_pct or 0:.1f}% / ROE {lock_roe:.1f}%)")
                    print(f"   嘗試 Maker 平倉 (最多等 {self.maker_exit_timeout}s)...")
                    exit_tx, exit_price = await self.api._try_place_order(
                        side=exit_side,
                        size=btc_size,
                        timeout_seconds=self.maker_exit_timeout,
                        attempt=1,
                        max_attempts=1,
                        best_bid=bid2,
                        best_ask=ask2,
                    )
                    if exit_tx and exit_price > 0:
                        result["exit_method"] = "M%M_LOCK_MAKER"
                        result["exit_expected_price"] = expected_maker
                        print(f"   {Colors.green('✅ Maker 鎖利成功!')}")
                    else:
                        # Maker 超時，改用 Taker - 使用 _close_ioc_order (平倉專用)
                        print(f"   {Colors.yellow('⚠️ Maker 超時，改用 Taker')}")
                        exit_tx, exit_price = await self.api._close_ioc_order(direction, btc_size)
                        result["exit_method"] = "M%M_LOCK_TAKER"
                        result["exit_expected_price"] = expected_taker
                else:
                    print(f"\n⏲️ 超時未觸發 TP/SL，強制平倉 (taker)...")
                    result["exit_method"] = "TIMEOUT_TAKER"
                    exit_tx, exit_price = await self.api._close_ioc_order(direction, btc_size)
                    result["exit_expected_price"] = expected_taker

                if exit_tx and exit_price > 0:
                    result["exit_price"] = exit_price
                    if "TAKER" in result["exit_method"]:
                        result["taker_exit_price"] = exit_price

                    # 退出後再取一次中間價作為對照
                    bid_exit, ask_exit, mid_exit, ws_exit2 = await self.get_dual_snapshot()
                    result["mid_at_exit"] = mid_exit
                    result["ws_mid_at_exit"] = ws_exit2.get("mid", 0.0)
                    result["ws_age_ms_at_exit"] = ws_exit2.get("age_ms", 0.0)

                    result["exit_edge_bps"] = self._edge_bps(exit_price, mid_exit, exit_dir)
                    
                    # Paper: 記錄平倉時的中間價
                    paper_exit_mid = mid_exit
                    result["paper_exit_price"] = paper_exit_mid
                    
                    # Paper vs dYdX 出場價差 (bps)
                    if paper_exit_mid > 0 and exit_price > 0:
                        if direction == "LONG":
                            # LONG 平倉 = SHORT: dYdX 價格越高越好
                            result["paper_vs_dydx_exit_bps"] = (exit_price - paper_exit_mid) / paper_exit_mid * 10000
                        else:
                            # SHORT 平倉 = LONG: dYdX 價格越低越好
                            result["paper_vs_dydx_exit_bps"] = (paper_exit_mid - exit_price) / paper_exit_mid * 10000

                    expected_exit = result["exit_expected_price"] or expected_taker
                    if expected_exit > 0:
                        if exit_side == "SHORT":
                            exit_slippage = (expected_exit - exit_price) / expected_exit * 10000
                        else:
                            exit_slippage = (exit_price - expected_exit) / expected_exit * 10000
                        result["exit_slippage_bps"] = exit_slippage

                    if result["mid_at_exit"] > 0:
                        result["api_exit_error_bps"] = abs(exit_price - result["mid_at_exit"]) / result["mid_at_exit"] * 10000
                    if result["ws_mid_at_exit"] > 0:
                        result["ws_exit_error_bps"] = abs(exit_price - result["ws_mid_at_exit"]) / result["ws_mid_at_exit"] * 10000

                    # 計算 PnL
                    if direction == "LONG":
                        pnl = (exit_price - fill_price) * btc_size
                    else:
                        pnl = (fill_price - exit_price) * btc_size
                    result["pnl_usdt"] = pnl
                    
                    # Paper PnL (用中間價)
                    paper_entry = result["paper_entry_price"]
                    paper_exit = result["paper_exit_price"]
                    if paper_entry > 0 and paper_exit > 0:
                        if direction == "LONG":
                            paper_pnl = (paper_exit - paper_entry) * btc_size
                        else:
                            paper_pnl = (paper_entry - paper_exit) * btc_size
                        result["paper_pnl_usdt"] = paper_pnl
                        result["paper_vs_dydx_pnl_diff"] = pnl - paper_pnl
                    maker_fee_pct = self.api.config.maker_fee_pct if self.api else 0.0
                    taker_fee_pct = self.api.config.taker_fee_pct if self.api else 0.0
                    entry_notional = fill_price * btc_size
                    exit_notional = exit_price * btc_size
                    fee = entry_notional * maker_fee_pct / 100 + exit_notional * taker_fee_pct / 100
                    result["fee_usdt"] = fee
                    result["net_pnl_usdt"] = pnl - fee

                    if result["mid_at_place"] > 0 and result["mid_at_exit"] > 0:
                        api_mid_pnl = (result["mid_at_exit"] - result["mid_at_place"]) * btc_size
                        if direction == "SHORT":
                            api_mid_pnl = -api_mid_pnl
                        result["api_mid_pnl"] = api_mid_pnl
                        result["api_pnl_error"] = api_mid_pnl - pnl
                    if result["ws_mid_at_place"] > 0 and result["ws_mid_at_exit"] > 0:
                        ws_mid_pnl = (result["ws_mid_at_exit"] - result["ws_mid_at_place"]) * btc_size
                        if direction == "SHORT":
                            ws_mid_pnl = -ws_mid_pnl
                        result["ws_mid_pnl"] = ws_mid_pnl
                        result["ws_pnl_error"] = ws_mid_pnl - pnl

                    print(f"{Colors.green('✅ 平倉成功!')} 價格: {Colors.cyan(f'{exit_price:.2f}')} | 方法: {Colors.yellow(result['exit_method'])}")
                    if result["exit_expected_price"] > 0:
                        print(f"   預期: {result['exit_expected_price']:.2f}")
                    exit_slip_color = Colors.green if result['exit_slippage_bps'] <= 0 else Colors.red
                    exit_slip_str = f"{result['exit_slippage_bps']:.2f} bps"
                    print(f"   出場滑點: {exit_slip_color(exit_slip_str)}")
                    print(f"   📝 Paper 出場價 (mid): {Colors.dim(f'{paper_exit_mid:.2f}')} | 差異: {result['paper_vs_dydx_exit_bps']:+.2f} bps")
                    
                    # 計算 ROE%
                    roe_pct = self._calc_roe_pct(direction, fill_price, exit_price)
                    pnl_color = Colors.green if pnl > 0 else Colors.red
                    roe_color = Colors.green if roe_pct > 0 else Colors.red
                    print(f"   💰 dYdX PnL: {pnl_color(f'${pnl:.4f}')} (ROE: {roe_color(f'{roe_pct:+.2f}%')}) | Paper PnL: ${result['paper_pnl_usdt']:.4f} | 差異: ${result['paper_vs_dydx_pnl_diff']:+.4f}")
                    
                    # 更新累計統計
                    self.total_trades += 1
                    self.total_pnl += pnl
                    if pnl > 0:
                        self.wins += 1
                    elif pnl < 0:
                        self.losses += 1
                    # pnl == 0 時不算勝也不算敗
                    if pnl > self.best_pnl:
                        self.best_pnl = pnl
                    if pnl < self.worst_pnl:
                        self.worst_pnl = pnl
                    
                    # 打印即時統計
                    self._print_realtime_stats()
                    
                    # 🔧 API 驗證平倉完成
                    await asyncio.sleep(0.3)  # 等待結算
                    positions = await self.api.get_positions()
                    remaining_pos = None
                    for pos in positions or []:
                        if pos.get('market') == 'BTC-USD' and pos.get('status') == 'OPEN':
                            remaining_size = abs(float(pos.get('size', 0)))
                            if remaining_size > 0.00001:
                                remaining_pos = pos
                                break
                    
                    if remaining_pos:
                        remaining_size = abs(float(remaining_pos.get('size', 0)))
                        remaining_side = remaining_pos.get('side')
                        print(f"   {Colors.yellow(f'⚠️ 殘留持倉:')} {remaining_side} {remaining_size:.4f} BTC，清理中...")
                        await self.api._close_ioc_order(remaining_side, remaining_size)
                        await asyncio.sleep(0.5)
                        # 再次確認
                        if await self._cleanup_position("二次清理"):
                            print(f"   {Colors.red('❌ 清倉失敗，等待 3 秒...')}")
                            await asyncio.sleep(3)
                        else:
                            print(f"   {Colors.green('✅ 已清倉')}")
                    else:
                        print(f"   {Colors.dim('📋 API 確認: 持倉已清空 ✓')}")
                else:
                    result["error"] = "Exit failed"
                    print(f"{Colors.red('❌ 平倉失敗!')}")
                    await asyncio.sleep(0.3)
                    await self._cleanup_position("平倉失敗後")
            else:
                # Maker 未成交
                result["maker_filled"] = False
                self.maker_misses += 1
                result["entry_edge_bps"] = self._edge_bps(maker_price, result["order_mid_at_place"], direction)
                print(f"{Colors.red('❌ Maker 未成交')} (耗時 {elapsed:.2f}s)")
                await self._cleanup_position("Maker 未成交")
                    
        except Exception as e:
            result["error"] = str(e)
            print(f"❌ 錯誤: {e}")
            await self._cleanup_position("異常處理")
        
        mid_samples = {}
        mid_drifts = {}
        for key, task in sample_tasks.items():
            try:
                sample = await task
                mid_samples[key] = sample
                drift_bps = self._signed_mid_drift_bps(
                    sample.get("mid", 0.0),
                    result["mid_at_place"],
                    direction,
                )
                mid_drifts[key] = drift_bps
            except Exception as e:
                mid_samples[key] = {"error": str(e)}
                mid_drifts[key] = 0.0

        result["mid_samples"] = mid_samples
        result["mid_drift_bps"] = mid_drifts

        self.results.append(result)
        return result
    
    async def run_all_tests(self, num_trades: int, hours: float = 0):
        """執行所有測試
        
        Args:
            num_trades: 交易數量 (0=無限制)
            hours: 運行時數 (0=不限制)
        """
        # 決定運行模式
        if hours > 0:
            run_mode = "hours"
            end_time = time.time() + hours * 3600
            mode_str = f"{hours} 小時"
        elif num_trades > 0:
            run_mode = "trades"
            end_time = None
            mode_str = f"{num_trades} 筆"
        else:
            # 預設 10 筆
            run_mode = "trades"
            num_trades = 10
            end_time = None
            mode_str = f"{num_trades} 筆"
        
        print(f"\n{Colors.cyan('='*70)}")
        print(f"{Colors.bold('🚀 開始 Maker 成交率測試 (M%M 鎖利模式)')}")
        print(f"{Colors.cyan('='*70)}")
        if run_mode == "hours":
            print(f"   運行時間: {Colors.yellow(mode_str)}")
        else:
            print(f"   交易數: {Colors.yellow(mode_str)}")
        print(f"   每筆金額: {Colors.green(f'${self.size_usdc}')}")
        print(f"   槓桿: {Colors.magenta(f'{self.leverage}X')}")
        print(f"   Maker 超時: {self.maker_timeout}s")
        net_str = Colors.yellow('Testnet') if self.testnet else Colors.green('Mainnet')
        print(f"   網路: {net_str}")
        print(f"   價格來源輪替: {', '.join(self.price_sources)}")
        print(f"   進場 Anchor: {', '.join(self.entry_anchors)}")
        print(
            f"   TP: {Colors.green(f'ROE +{self.tp_bps:.1f}%')} | "
            f"SL: {Colors.red(f'ROE -{self.sl_bps:.1f}%')}"
        )
        print(
            f"   {Colors.yellow('M%M 鎖利')}: {self.midpoint_ratio*100:.0f}% | "
            f"監控間隔: {self.poll_interval:.2f}s"
        )
        if self.no_timeout:
            print(f"   {Colors.green('♾️ 無限等待模式')}: 純靠 TP/SL/M%M 觸發出場")
        else:
            print(f"   Max Hold: {self.max_hold:.1f}s (超時強制平倉)")
        if self.max_loss > 0:
            print(f"   {Colors.red(f'🛑 最大虧損限制: ${self.max_loss:.2f}')} (達到後停止交易)")
        if self.drift_windows:
            windows_str = ", ".join(self._format_window(w) for w in self.drift_windows)
            print(f"   Mid drift 取樣: {windows_str}s")
        
        # 連接
        await self.connect()
        
        if self.api:
            print(f"   費率假設: Maker {self.api.config.maker_fee_pct:.3f}% | Taker {self.api.config.taker_fee_pct:.3f}%")

        # 說明軟止損機制
        print(f"\n   {Colors.yellow('⚠️ 注意: 止損為「軟監控」模式')}")
        print(f"      - 每 {self.poll_interval}s 檢查一次中間價")
        print(f"      - 如果價格在 {self.poll_interval}s 內跳過止損線，可能不會觸發")
        print(f"      - 這不是真正的交易所掛單止損")

        # 顯示初始波次狀態
        if self.random_mode:
            self._print_wave_status()

        try:
            trade_count = 0
            while True:
                # 檢查結束條件
                if run_mode == "hours" and time.time() >= end_time:
                    elapsed = hours
                    print(f"\n{Colors.green(f'⏱️ 已達到設定時間 {hours} 小時，停止測試')}")
                    break
                if run_mode == "trades" and trade_count >= num_trades:
                    break
                
                trade_count += 1
                
                # 使用隨機波次決定方向
                if self.random_mode:
                    direction = self._get_next_direction()
                    anchor = self.entry_anchors[(trade_count - 1) % len(self.entry_anchors)]
                else:
                    # 非隨機模式：交替 LONG/SHORT
                    combos = []
                    for anchor in self.entry_anchors:
                        combos.append(("LONG", anchor))
                        combos.append(("SHORT", anchor))
                    direction, anchor = combos[(trade_count - 1) % len(combos)]
                
                source = self.price_sources[(trade_count - 1) % len(self.price_sources)]
                
                # 顯示波次狀態
                if self.random_mode:
                    self._print_wave_status()
                
                # 顯示剩餘時間（如果是時間模式）
                if run_mode == "hours":
                    remaining = end_time - time.time()
                    remaining_min = remaining / 60
                    if remaining_min > 60:
                        remaining_str = f"{remaining_min/60:.1f} 小時"
                    else:
                        remaining_str = f"{remaining_min:.0f} 分鐘"
                    print(f"\n{Colors.dim(f'⏱️ 剩餘時間: {remaining_str} | 已完成: {trade_count-1} 筆')}")
                
                await self.run_single_test(trade_count, direction, source, anchor)
                
                # 🛑 檢查最大虧損限制
                if self.max_loss > 0 and self.total_pnl < 0:
                    if abs(self.total_pnl) >= self.max_loss:
                        print(f"\n{Colors.red('='*70)}")
                        print(f"{Colors.red(f'🛑 達到最大虧損限制!')}")
                        print(f"   累計虧損: {Colors.red(f'${self.total_pnl:.4f}')}")
                        print(f"   限制: {Colors.yellow(f'${self.max_loss:.2f}')}")
                        print(f"{Colors.red('='*70)}")
                        print(f"\n{Colors.yellow('⚠️ 停止交易以控制風險')}")
                        break
                
                # 交易間隔
                print(f"\n{Colors.dim('⏳ 等待 3 秒...')}")
                await asyncio.sleep(3)
        finally:
            if self.ws_hub:
                self.ws_hub.stop()
                self.ws_hub = None
        
        # 輸出統計
        self.print_summary()
        
        # 保存結果
        self.save_results()
    
    def print_summary(self):
        """輸出統計摘要"""
        print(f"\n{Colors.cyan('='*70)}")
        print(f"{Colors.bold('📊 測試結果摘要')}")
        print(f"{Colors.cyan('='*70)}")
        
        total = len(self.results)
        filled = sum(1 for r in self.results if r["maker_filled"])
        missed = total - filled
        fill_rate = filled / total * 100 if total > 0 else 0
        
        # 成交率顏色
        if fill_rate >= 80:
            fill_rate_str = Colors.green(f"{fill_rate:.1f}%")
        elif fill_rate >= 50:
            fill_rate_str = Colors.yellow(f"{fill_rate:.1f}%")
        else:
            fill_rate_str = Colors.red(f"{fill_rate:.1f}%")
        
        print(f"\n{Colors.bold('🎯 Maker 成交率:')}")
        print(f"   總測試: {Colors.bold(str(total))} 筆")
        print(f"   成交: {Colors.green(str(filled))} 筆")
        print(f"   未成交: {Colors.red(str(missed))} 筆")
        print(f"   成交率: {fill_rate_str}")

        by_source = {}
        for r in self.results:
            src = r.get("order_price_source") or "api"
            by_source.setdefault(src, []).append(r)
        if by_source:
            print(f"\n🔬 來源比較 (使用該來源價格下單):")
            for src in sorted(by_source.keys()):
                rows = by_source[src]
                attempts = len(rows)
                fills = sum(1 for r in rows if r.get("maker_filled"))
                fill_rate_src = fills / attempts * 100 if attempts > 0 else 0.0
                filled_rows = [r for r in rows if r.get("maker_filled")]
                net_total = sum(r.get("net_pnl_usdt", 0.0) for r in filled_rows)
                win = sum(1 for r in filled_rows if r.get("net_pnl_usdt", 0.0) > 0)
                win_rate = win / len(filled_rows) * 100 if filled_rows else 0.0
                ws_ages = [r.get("ws_age_ms_at_place", 0.0) for r in rows if r.get("ws_age_ms_at_place", 0.0) > 0]
                avg_ws_age = sum(ws_ages) / len(ws_ages) if ws_ages else 0.0

                if src == "ws":
                    entry_err = [r.get("ws_entry_error_bps", 0.0) for r in filled_rows if r.get("ws_entry_error_bps", 0.0) > 0]
                    exit_err = [r.get("ws_exit_error_bps", 0.0) for r in filled_rows if r.get("ws_exit_error_bps", 0.0) > 0]
                    mid_label = "WS"
                else:
                    entry_err = [r.get("api_entry_error_bps", 0.0) for r in filled_rows if r.get("api_entry_error_bps", 0.0) > 0]
                    exit_err = [r.get("api_exit_error_bps", 0.0) for r in filled_rows if r.get("api_exit_error_bps", 0.0) > 0]
                    mid_label = "API"

                avg_entry_err = sum(entry_err) / len(entry_err) if entry_err else 0.0
                avg_exit_err = sum(exit_err) / len(exit_err) if exit_err else 0.0

                print(f"   {src.upper():<3} | 成交率 {fill_rate_src:.1f}% ({fills}/{attempts}) | 淨PnL ${net_total:.4f} | 勝率 {win_rate:.1f}%")
                print(f"       {mid_label} 中間價誤差: 進場 {avg_entry_err:.2f} bps | 出場 {avg_exit_err:.2f} bps | WS age {avg_ws_age:.1f}ms")

        by_anchor = {}
        for r in self.results:
            key = (r.get("direction"), r.get("entry_anchor") or "-")
            by_anchor.setdefault(key, []).append(r)
        if by_anchor:
            print(f"\n🧭 進場 Anchor 比較 (方向/Anchor):")
            for key in sorted(by_anchor.keys()):
                rows = by_anchor[key]
                attempts = len(rows)
                fills = sum(1 for r in rows if r.get("maker_filled"))
                fill_rate_anchor = fills / attempts * 100 if attempts > 0 else 0.0
                filled_rows = [r for r in rows if r.get("maker_filled")]
                entry_edges = [r.get("entry_edge_bps", 0.0) for r in filled_rows]
                entry_slips = [r.get("entry_slippage_bps", 0.0) for r in filled_rows]
                avg_edge = sum(entry_edges) / len(entry_edges) if entry_edges else 0.0
                avg_abs_edge = sum(abs(v) for v in entry_edges) / len(entry_edges) if entry_edges else 0.0
                avg_slip = sum(entry_slips) / len(entry_slips) if entry_slips else 0.0
                direction, anchor = key
                print(
                    f"   {direction:<5} {anchor:<3} | 成交率 {fill_rate_anchor:.1f}% ({fills}/{attempts}) | "
                    f"進場相對 mid {avg_edge:+.2f} bps | abs {avg_abs_edge:.2f} bps | 滑點 {avg_slip:.2f} bps"
                )

        ws_requests = sum(1 for r in self.results if r.get("order_price_source_requested") == "ws")
        ws_fallback = sum(1 for r in self.results if r.get("order_price_source_requested") == "ws" and r.get("order_price_source") != "ws")
        if ws_requests and ws_fallback:
            print(f"\n⚠️ WS 價格無效回退 API: {ws_fallback}/{ws_requests}")
        
        # 成交的交易統計
        filled_results = [r for r in self.results if r["maker_filled"]]
        if filled_results:
            avg_entry_slip = sum(r["entry_slippage_bps"] for r in filled_results) / len(filled_results)
            avg_exit_slip = sum(r["exit_slippage_bps"] for r in filled_results) / len(filled_results)
            total_pnl = sum(r["pnl_usdt"] for r in filled_results)
            avg_entry_edge = sum(r.get("entry_edge_bps", 0.0) for r in filled_results) / len(filled_results)
            avg_exit_edge = sum(r.get("exit_edge_bps", 0.0) for r in filled_results) / len(filled_results)
            total_net = sum(r.get("net_pnl_usdt", 0.0) for r in filled_results)
            net_win = sum(1 for r in filled_results if r.get("net_pnl_usdt", 0.0) > 0)
            
            print(f"\n📈 滑點統計 (已成交):")
            print(f"   平均進場滑點: {avg_entry_slip:.2f} bps")
            print(f"   平均出場滑點: {avg_exit_slip:.2f} bps")
            print(f"   平均進場相對 mid 優勢: {avg_entry_edge:.2f} bps")
            print(f"   平均出場相對 mid 優勢: {avg_exit_edge:.2f} bps")
            print(f"   總 PnL: ${total_pnl:.4f}")
            print(f"   淨 PnL (估算含費用): ${total_net:.4f} | 勝率: {net_win / len(filled_results) * 100:.1f}%")

            api_entry_vals = [r.get("api_entry_error_bps", 0.0) for r in filled_results if r.get("api_entry_error_bps", 0.0) > 0]
            ws_entry_vals = [r.get("ws_entry_error_bps", 0.0) for r in filled_results if r.get("ws_entry_error_bps", 0.0) > 0]
            api_exit_vals = [r.get("api_exit_error_bps", 0.0) for r in filled_results if r.get("api_exit_error_bps", 0.0) > 0]
            ws_exit_vals = [r.get("ws_exit_error_bps", 0.0) for r in filled_results if r.get("ws_exit_error_bps", 0.0) > 0]

            if api_entry_vals or ws_entry_vals or api_exit_vals or ws_exit_vals:
                api_entry_avg = sum(api_entry_vals) / len(api_entry_vals) if api_entry_vals else 0.0
                ws_entry_avg = sum(ws_entry_vals) / len(ws_entry_vals) if ws_entry_vals else 0.0
                api_exit_avg = sum(api_exit_vals) / len(api_exit_vals) if api_exit_vals else 0.0
                ws_exit_avg = sum(ws_exit_vals) / len(ws_exit_vals) if ws_exit_vals else 0.0
                print(f"\n📏 Mid 誤差 (絕對值 bps, 以成交價為基準):")
                print(f"   進場 API: {api_entry_avg:.2f} bps | WS: {ws_entry_avg:.2f} bps")
                print(f"   出場 API: {api_exit_avg:.2f} bps | WS: {ws_exit_avg:.2f} bps")

            api_pnl_err = [abs(r.get("api_pnl_error", 0.0)) for r in filled_results if r.get("api_mid_pnl", 0.0) != 0.0]
            ws_pnl_err = [abs(r.get("ws_pnl_error", 0.0)) for r in filled_results if r.get("ws_mid_pnl", 0.0) != 0.0]
            if api_pnl_err or ws_pnl_err:
                api_pnl_avg = sum(api_pnl_err) / len(api_pnl_err) if api_pnl_err else 0.0
                ws_pnl_avg = sum(ws_pnl_err) / len(ws_pnl_err) if ws_pnl_err else 0.0
                print(f"\n📉 Mid PnL 誤差 (絕對值, 估算 vs 實際):")
                print(f"   API: ${api_pnl_avg:.6f} | WS: ${ws_pnl_avg:.6f}")

            # Paper vs dYdX 比較統計
            paper_filled = [r for r in filled_results if r.get("paper_entry_price", 0.0) > 0 and r.get("paper_exit_price", 0.0) > 0]
            if paper_filled:
                avg_paper_entry_diff = sum(r.get("paper_vs_dydx_entry_bps", 0.0) for r in paper_filled) / len(paper_filled)
                avg_paper_exit_diff = sum(r.get("paper_vs_dydx_exit_bps", 0.0) for r in paper_filled) / len(paper_filled)
                total_paper_pnl = sum(r.get("paper_pnl_usdt", 0.0) for r in paper_filled)
                total_dydx_pnl = sum(r.get("pnl_usdt", 0.0) for r in paper_filled)
                total_pnl_diff = sum(r.get("paper_vs_dydx_pnl_diff", 0.0) for r in paper_filled)
                
                paper_win = sum(1 for r in paper_filled if r.get("paper_pnl_usdt", 0.0) > 0)
                dydx_win = sum(1 for r in paper_filled if r.get("pnl_usdt", 0.0) > 0)
                
                print(f"\n📝 Paper vs dYdX 比較 (+ = dYdX 更優):")
                print(f"   進場價差: {avg_paper_entry_diff:+.2f} bps (dYdX 比 mid)")
                print(f"   出場價差: {avg_paper_exit_diff:+.2f} bps (dYdX 比 mid)")
                print(f"   ────────────────────────────────────")
                print(f"   Paper 總 PnL:  ${total_paper_pnl:+.4f} | 勝率: {paper_win}/{len(paper_filled)}")
                print(f"   dYdX  總 PnL:  ${total_dydx_pnl:+.4f} | 勝率: {dydx_win}/{len(paper_filled)}")
                print(f"   dYdX 優勢:     ${total_pnl_diff:+.4f} ({total_pnl_diff / abs(total_paper_pnl) * 100 if total_paper_pnl != 0 else 0:+.1f}%)")

            exit_rows = [r for r in filled_results if r.get("exit_price", 0.0) > 0]
            if exit_rows:
                exit_groups = {}
                for r in exit_rows:
                    key = (r.get("exit_reason") or "-", r.get("exit_method") or "-")
                    exit_groups.setdefault(key, []).append(r)
                print(f"\n🚪 平倉方式統計 (原因/方式):")
                for key in sorted(exit_groups.keys()):
                    rows = exit_groups[key]
                    count = len(rows)
                    avg_exit_edge = sum(r.get("exit_edge_bps", 0.0) for r in rows) / count if count else 0.0
                    avg_exit_slip = sum(r.get("exit_slippage_bps", 0.0) for r in rows) / count if count else 0.0
                    avg_hold = sum(r.get("hold_seconds", 0.0) for r in rows) / count if count else 0.0
                    reason, method = key
                    print(
                        f"   {reason:<7} {method:<16} | 次數 {count:<2} | 出場相對 mid {avg_exit_edge:+.2f} bps | "
                        f"滑點 {avg_exit_slip:.2f} bps | 平均持倉 {avg_hold:.2f}s"
                    )
        
        missed_results = [r for r in self.results if not r["maker_filled"]]
        if missed_results:
            avg_maker_edge = sum(r.get("entry_edge_bps", 0.0) for r in missed_results) / len(missed_results)
            print(f"\n📉 未成交 (假想掛價相對 mid 優勢):")
            print(f"   平均掛價優勢: {avg_maker_edge:.2f} bps")

        if self.drift_windows:
            print(f"\n🧭 Mid drift (signed bps, + 為有利):")
            for window in self.drift_windows:
                key = self._format_window(window)
                filled_drifts = [
                    r.get("mid_drift_bps", {}).get(key)
                    for r in filled_results
                    if r.get("mid_drift_bps", {}).get(key) is not None
                ]
                missed_drifts = [
                    r.get("mid_drift_bps", {}).get(key)
                    for r in missed_results
                    if r.get("mid_drift_bps", {}).get(key) is not None
                ]
                avg_filled = sum(filled_drifts) / len(filled_drifts) if filled_drifts else 0.0
                avg_missed = sum(missed_drifts) / len(missed_drifts) if missed_drifts else 0.0
                diff = avg_filled - avg_missed
                print(f"   {key:>4}s | 成交 {avg_filled:+.2f} bps | 未成 {avg_missed:+.2f} bps | 差 {diff:+.2f} bps")
        
        # 詳細列表
        print(f"\n📋 詳細記錄:")
        print(
            f"{'#':>3} | {'Src':>3} | {'方向':>6} | {'Anc':>3} | {'Maker':>8} | {'進場滑點':>10} | "
            f"{'出場滑點':>10} | {'Exit':>12} | {'NetPnL':>10}"
        )
        print("-" * 94)
        for r in self.results:
            status = "✅成交" if r["maker_filled"] else "❌未成交"
            entry_slip = f"{r['entry_slippage_bps']:.2f}bps" if r["maker_filled"] else "-"
            exit_slip = f"{r['exit_slippage_bps']:.2f}bps" if r["maker_filled"] else "-"
            pnl = f"${r['net_pnl_usdt']:.4f}" if r["maker_filled"] else "-"
            src = (r.get("order_price_source") or "-").upper()
            anchor = (r.get("entry_anchor") or "-").upper()
            exit_method = r.get("exit_method") or "-"
            print(
                f"{r['test_num']:>3} | {src:>3} | {r['direction']:>6} | {anchor:>3} | "
                f"{status:>8} | {entry_slip:>10} | {exit_slip:>10} | {exit_method:>12} | {pnl:>10}"
            )
        
        # 最終總結
        self._print_final_summary()
    
    def _print_final_summary(self):
        """打印最終總結 (類似即時統計格式)"""
        filled_results = [r for r in self.results if r["maker_filled"]]
        if not filled_results:
            return
        
        # 計算統計
        total_trades = len(filled_results)
        wins = sum(1 for r in filled_results if r.get("pnl_usdt", 0) > 0)
        losses = sum(1 for r in filled_results if r.get("pnl_usdt", 0) < 0)
        draws = total_trades - wins - losses
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0.0
        
        total_pnl = sum(r.get("pnl_usdt", 0) for r in filled_results)
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0.0
        
        pnl_values = [r.get("pnl_usdt", 0) for r in filled_results]
        best_pnl = max(pnl_values) if pnl_values else 0
        worst_pnl = min(pnl_values) if pnl_values else 0
        
        # 計算 ROE (基於每筆金額)
        roe = total_pnl / self.size_usdc * 100 if self.size_usdc > 0 else 0.0
        
        # 顏色處理
        if win_rate >= 60:
            win_rate_str = Colors.green(f"{win_rate:.1f}%")
        elif win_rate >= 40:
            win_rate_str = Colors.yellow(f"{win_rate:.1f}%")
        else:
            win_rate_str = Colors.red(f"{win_rate:.1f}%")
        
        if total_pnl > 0:
            total_pnl_str = Colors.green(f"${total_pnl:.4f}")
            roe_str = Colors.green(f"+{roe:.2f}%")
        elif total_pnl < 0:
            total_pnl_str = Colors.red(f"${total_pnl:.4f}")
            roe_str = Colors.red(f"{roe:.2f}%")
        else:
            total_pnl_str = f"${total_pnl:.4f}"
            roe_str = f"{roe:.2f}%"
        
        best_str = Colors.green(f"${best_pnl:+.4f}") if best_pnl > 0 else f"${best_pnl:+.4f}"
        worst_str = Colors.red(f"${worst_pnl:+.4f}") if worst_pnl < 0 else f"${worst_pnl:+.4f}"
        
        # 網路名稱
        network = "Mainnet" if not self.testnet else "Testnet"
        
        # 計算最終金額
        final_balance = self.size_usdc + total_pnl
        if total_pnl >= 0:
            balance_str = f"{final_balance:.2f}U({Colors.green(f'+{total_pnl:.4f}')})"
        else:
            balance_str = f"{final_balance:.2f}U({Colors.red(f'{total_pnl:.4f}')})"
        
        # 打印最終總結
        print(f"\n{Colors.cyan('='*70)}")
        print(f"{Colors.bold('🏁 最終總結')}")
        print(f"{Colors.cyan('='*70)}")
        print(f"\n   {Colors.cyan('📊 真實統計 (WS):')}")
        print(f"     dYdX {network} {balance_str}  |  PnL: {total_pnl_str} (ROE: {roe_str})")
        print(f"      總交易: {Colors.bold(f'{total_trades}筆')}  |  勝: {Colors.green(str(wins))}  敗: {Colors.red(str(losses))}  平: {draws}  |  勝率: {win_rate_str}")
        print(f"      總盈虧: {total_pnl_str}  |  平均: ${avg_pnl:.4f}/筆")
        print(f"      最佳: {best_str}  最差: {worst_str}")
        print(f"{Colors.cyan('='*70)}")
    
    def save_results(self):
        """保存結果到 JSON"""
        output_dir = Path("logs/maker_test")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = output_dir / f"maker_fill_test_{timestamp}.json"
        
        summary = {
            "test_config": {
                "testnet": self.testnet,
                "size_usdc": self.size_usdc,
                "maker_timeout": self.maker_timeout,
                "drift_windows_sec": self.drift_windows,
                "price_sources": self.price_sources,
                "entry_anchors": self.entry_anchors,
                "tp_bps": self.tp_bps,
                "sl_bps": self.sl_bps,
                "max_hold": self.max_hold,
                "tp_exit": self.tp_exit,
                "sl_exit": self.sl_exit,
                "maker_exit_timeout": self.maker_exit_timeout,
                "poll_interval": self.poll_interval,
                "timestamp": datetime.now().isoformat()
            },
            "summary": {
                "total_trades": len(self.results),
                "maker_fills": self.maker_fills,
                "maker_misses": self.maker_misses,
                "fill_rate_pct": self.maker_fills / len(self.results) * 100 if self.results else 0,
                "net_pnl_total": sum(r.get("net_pnl_usdt", 0.0) for r in self.results),
            },
            "trades": self.results
        }
        
        with open(output_file, 'w') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        print(f"\n💾 結果已保存: {output_file}")


async def main():
    parser = argparse.ArgumentParser(description="Maker 成交率測試")
    parser.add_argument("--testnet", action="store_true", default=True, help="使用 Testnet")
    parser.add_argument("--mainnet", action="store_true", help="使用 Mainnet")
    parser.add_argument("--trades", type=int, default=0, help="交易數量 (0=無限制，配合 --hours 使用)")
    parser.add_argument("--hours", type=float, default=0, help="運行時數 (0=不限制，由 --trades 決定)")
    parser.add_argument("--size", type=float, default=20.0, help="每筆金額 (USDC), 預設 20")
    parser.add_argument("--timeout", type=float, default=2.0, help="Maker 超時秒數")
    parser.add_argument(
        "--drift-windows",
        type=str,
        default="0.5,1,2,5",
        help="Mid drift 取樣秒數 (逗號分隔, 例如: 0.5,1,2,5)",
    )
    parser.add_argument(
        "--price-sources",
        type=str,
        default="api,ws",
        help="下單價格來源輪替 (逗號分隔，api,ws)",
    )
    parser.add_argument(
        "--entry-anchors",
        type=str,
        default="bid,ask",
        help="進場 Maker Anchor (逗號分隔，bid,ask)",
    )
    parser.add_argument("--tp-bps", type=float, default=5.0, help="TP ROE%% (預設 5%%)")
    parser.add_argument("--sl-bps", type=float, default=0.4, help="SL ROE%% (預設 0.4%%)")
    parser.add_argument("--max-hold", type=float, default=30.0, help="最多持倉秒數 (配合 --no-timeout 無效)")
    parser.add_argument("--no-timeout", action="store_true", help="停用超時，純靠 M%%M 鎖利止損出場")
    parser.add_argument("--tp-exit", type=str, default="maker", help="TP 出場方式 (maker/taker)")
    parser.add_argument("--sl-exit", type=str, default="taker", help="SL 出場方式 (maker/taker)")
    parser.add_argument("--maker-exit-timeout", type=float, default=2.0, help="Maker 出場等待秒數")
    parser.add_argument("--poll-interval", type=float, default=0.10, help="TP/SL 監控間隔秒數 (預設 0.1s)")
    parser.add_argument("--leverage", type=int, default=50, help="槓桿倍數 (預設 50X)")
    parser.add_argument("--midpoint-ratio", type=float, default=0.8, help="M%%M 鎖利比例 (預設 0.8 = 80%%)")
    parser.add_argument("--random", action="store_true", default=True, help="使用隨機波次進場 (預設啟用)")
    parser.add_argument("--no-random", action="store_true", help="停用隨機波次，使用交替 LONG/SHORT")
    parser.add_argument("--max-loss", type=float, default=0.0, help="最大虧損金額 (USDC)，達到後停止交易 (預設 0=不限制)")
    parser.add_argument("--max-spread", type=float, default=0.0, help="最大 Spread (bps)，超過則跳過進場 (預設 0=不限制)")
    
    args = parser.parse_args()
    
    testnet = not args.mainnet
    windows = []
    for part in (args.drift_windows or "").split(","):
        item = part.strip()
        if not item:
            continue
        try:
            value = float(item)
        except ValueError:
            continue
        if value > 0:
            windows.append(value)
    if not windows:
        windows = [0.5, 1.0, 2.0, 5.0]
    
    price_sources = []
    for part in (args.price_sources or "").split(","):
        item = part.strip().lower()
        if item in ("api", "ws"):
            price_sources.append(item)
    if not price_sources:
        price_sources = ["api", "ws"]

    entry_anchors = []
    valid_anchors = ("bid", "ask", "mid", "mid+")
    for part in (args.entry_anchors or "").split(","):
        item = part.strip().lower()
        if item in valid_anchors:
            entry_anchors.append(item)
    if not entry_anchors:
        entry_anchors = ["bid"]
    
    # 處理隨機模式
    use_random = not args.no_random
    
    tester = MakerFillRateTest(
        testnet=testnet,
        size_usdc=args.size,
        maker_timeout=args.timeout,
        drift_windows=windows,
        price_sources=price_sources,
        entry_anchors=entry_anchors,
        tp_bps=args.tp_bps,
        sl_bps=args.sl_bps,
        max_hold=args.max_hold,
        tp_exit=args.tp_exit,
        sl_exit=args.sl_exit,
        maker_exit_timeout=args.maker_exit_timeout,
        poll_interval=args.poll_interval,
        leverage=args.leverage,
        midpoint_ratio=args.midpoint_ratio,
        no_timeout=args.no_timeout,
        max_loss=args.max_loss,
        max_spread=args.max_spread,
    )
    
    # 設定隨機模式
    tester.random_mode = use_random
    
    try:
        await tester.run_all_tests(args.trades, args.hours)
    except KeyboardInterrupt:
        print(f"\n{Colors.yellow('='*70)}")
        print(f"{Colors.yellow('⚠️ 使用者中斷 (Ctrl+C)')}")
        print(f"{Colors.yellow('='*70)}")
        # 嘗試清理
        if tester.ws_hub:
            tester.ws_hub.stop()
        # 如果有交易結果，還是輸出統計和保存
        if tester.results:
            tester.print_summary()
            tester.save_results()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.yellow('👋 已退出')}")
